"""
Chastiefol — Main Agent Orchestrator
Ties together: data → analysis → risk → signal → execution (paper/live).
"""

import json
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from Analysis.xauusd_engine import ChastiefollAgent, Signal, TradeSetup
from Risk.risk_manager import (
    AccountState, DrawdownGuard, PositionSizer, GoldSessionFilter,
    TradeManager, TradeLifecycle, RiskModel
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Chastiefol")


@dataclass
class AgentConfig:
    # Account
    broker:          str   = "paper"            # "paper" | "mt5" | "oanda"
    symbol:          str   = "XAUUSD"
    initial_balance: float = 10_000.0
    currency:        str   = "USD"

    # Strategy
    timeframe:       str   = "H1"
    lookback_bars:   int   = 100
    risk_pct:        float = 1.0
    max_risk_pct:    float = 2.0
    atr_sl_mult:     float = 1.5
    rr_target:       float = 2.0
    min_confidence:  float = 0.55

    # Risk
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct:   float = 10.0
    max_open_trades:    int   = 2

    # Execution
    scan_interval_sec: int   = 60
    paper_mode:        bool  = True

    # Notifications
    telegram_token:  Optional[str] = None
    telegram_chat_id: Optional[str] = None


class ChastiefollOrchestrator:
    """
    Top-level agent that:
    1. Fetches market data (pluggable: MT5, OANDA, CSV, WebSocket)
    2. Runs analysis engine
    3. Checks risk guards
    4. Issues paper or live orders
    5. Manages open trades
    6. Logs everything
    """

    def __init__(self, config: AgentConfig = None):
        self.config  = config or AgentConfig()
        self.agent   = ChastiefollAgent()
        self.sizer   = PositionSizer(
            risk_pct=self.config.risk_pct,
            max_risk_pct=self.config.max_risk_pct,
            model=RiskModel.FIXED_PERCENT,
        )
        self.guard   = DrawdownGuard(
            max_daily_loss_pct=self.config.max_daily_loss_pct,
            max_drawdown_pct=self.config.max_drawdown_pct,
            max_open_trades=self.config.max_open_trades,
        )
        self.session = GoldSessionFilter()
        self.manager = TradeManager()

        self.account = AccountState(
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            peak_balance=self.config.initial_balance,
        )

        self.open_trades:  list[TradeLifecycle] = []
        self.trade_log:    list[dict]           = []
        self.signal_count: int                  = 0

        log.info(f"Chastiefol agent initialized | "
                 f"Symbol: {self.config.symbol} | "
                 f"Mode: {'PAPER' if self.config.paper_mode else 'LIVE'}")

    # ──────────────────────────────────────────
    # Main Loop
    # ──────────────────────────────────────────

    def run(self, data_provider=None):
        """
        Main event loop.
        data_provider: callable() → pd.DataFrame with OHLCV data.
        If None, uses demo sine-wave data generator.
        """
        import numpy as np, pandas as pd

        if data_provider is None:
            log.warning("No data provider — using synthetic data demo mode.")
            data_provider = self._synthetic_provider()

        log.info("Starting main loop...")
        try:
            while True:
                self._cycle(data_provider())
                if self.config.paper_mode:
                    break   # In demo, run one cycle only
                time.sleep(self.config.scan_interval_sec)
        except KeyboardInterrupt:
            log.info("Agent stopped by user.")
        finally:
            self._print_summary()

    def _cycle(self, df):
        """Single analysis + decision cycle."""
        now = datetime.now(timezone.utc)
        log.info(f"--- Cycle {now.strftime('%H:%M:%S UTC')} ---")

        # Session filter
        active, sess_name = self.session.is_active(now.hour, now.minute)
        log.info(f"Session: {sess_name} | Active: {active}")

        # Manage existing trades first
        if self.open_trades and len(df) > 0:
            current_price = float(df["close"].iloc[-1])
            self._manage_open_trades(current_price, df)

        self.account.open_trades = len(self.open_trades)

        # Risk check
        status = self.guard.check(self.account)
        if not status["trading_allowed"]:
            log.warning(f"Trading paused: {status['reason']}")
            return

        for w in status["warnings"]:
            log.warning(w)

        # Run analysis
        if len(df) < self.config.lookback_bars:
            log.info("Insufficient data for analysis.")
            return

        setup = self.agent.analyze(
            df.tail(self.config.lookback_bars).reset_index(drop=True),
            session_active=active,
            atr_multiplier_sl=self.config.atr_sl_mult,
            rr_target=self.config.rr_target,
        )

        if setup is None:
            log.info("No valid setup found this cycle.")
            return

        if setup.confidence < self.config.min_confidence:
            log.info(f"Signal found but confidence too low: {setup.confidence*100:.0f}%")
            return

        # Position size
        pos_spec = self.sizer.calculate(
            self.account, setup.entry, setup.stop_loss
        )
        lot = round(pos_spec.lot_size * status["size_multiplier"], 2)

        log.info(f"\n{'*'*50}")
        log.info(f"  SIGNAL: {setup.signal.value}")
        log.info(f"  Entry:      ${setup.entry:.2f}")
        log.info(f"  Stop Loss:  ${setup.stop_loss:.2f}")
        log.info(f"  Take Profit:${setup.take_profit:.2f}")
        log.info(f"  R:R:        {setup.rr_ratio}")
        log.info(f"  Confidence: {setup.confidence*100:.0f}%")
        log.info(f"  Lots:       {lot}")
        log.info(f"  Risk USD:   ${pos_spec.risk_usd}")
        log.info(f"  Confluence ({setup.confluence} factors):")
        for r in setup.reasons:
            log.info(f"    ✓ {r}")
        log.info(f"{'*'*50}\n")

        # Execute
        if self.config.paper_mode:
            self._paper_execute(setup, lot)
        else:
            self._live_execute(setup, lot)

        self.signal_count += 1

        # Notify
        if self.config.telegram_token:
            self._send_telegram(setup, lot)

    # ──────────────────────────────────────────
    # Trade Management
    # ──────────────────────────────────────────

    def _manage_open_trades(self, current_price: float, df):
        """Update all open trades with current price and apply management."""
        import numpy as np
        from Analysis.xauusd_engine import TechnicalIndicators
        ti  = TechnicalIndicators()
        atr = ti.atr(df["high"], df["low"], df["close"]).iloc[-1]

        closed = []
        for trade in self.open_trades:
            result = self.manager.manage(trade, current_price, current_atr=atr)

            for action in result["action_descriptions"]:
                log.info(f"  Trade mgmt: {action}")

            if result["close_all"]:
                pnl = trade.pnl_usd
                self.account.balance += pnl
                self.account.equity   = self.account.balance
                if pnl < 0:
                    self.account.daily_loss += abs(pnl)
                self.account.update_peak()

                outcome = "WIN" if pnl > 0 else "LOSS"
                self.trade_log.append({
                    "direction":   trade.direction,
                    "entry":       trade.entry_price,
                    "exit":        current_price,
                    "lot_size":    trade.lot_size,
                    "pnl_usd":     round(pnl, 2),
                    "outcome":     outcome,
                    "timestamp":   datetime.now(timezone.utc).isoformat(),
                })
                log.info(f"  Trade closed: {outcome} | P&L: ${pnl:+.2f} | "
                         f"Balance: ${self.account.balance:.2f}")
                closed.append(trade)

        for t in closed:
            self.open_trades.remove(t)

    # ──────────────────────────────────────────
    # Execution
    # ──────────────────────────────────────────

    def _paper_execute(self, setup: TradeSetup, lot: float):
        """Simulate order fill instantly at setup.entry."""
        trade = TradeLifecycle(
            entry_price=setup.entry,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            lot_size=lot,
            direction=setup.signal.value,
            current_price=setup.entry,
        )
        self.open_trades.append(trade)
        self.account.open_trades = len(self.open_trades)
        log.info(f"[PAPER] Order filled: {setup.signal.value} "
                 f"{lot} lots @ {setup.entry:.2f}")

    def _live_execute(self, setup: TradeSetup, lot: float):
        """
        Live execution placeholder.
        Implement with MetaTrader5 Python SDK or OANDA v20 API.
        Example MT5:
            import MetaTrader5 as mt5
            request = {
                "action":   mt5.TRADE_ACTION_DEAL,
                "symbol":   self.config.symbol,
                "volume":   lot,
                "type":     mt5.ORDER_TYPE_BUY if setup.signal == Signal.BUY else mt5.ORDER_TYPE_SELL,
                "price":    mt5.symbol_info_tick(self.config.symbol).ask,
                "sl":       setup.stop_loss,
                "tp":       setup.take_profit,
                "magic":    20250101,
                "comment":  f"Chastiefol {setup.confidence*100:.0f}%",
            }
            result = mt5.order_send(request)
        """
        log.warning("Live execution not implemented. Switch to paper mode or add broker SDK.")

    # ──────────────────────────────────────────
    # Notifications
    # ──────────────────────────────────────────

    def _send_telegram(self, setup: TradeSetup, lot: float):
        """Send trade signal alert via Telegram."""
        try:
            import urllib.request
            emoji = "🟢" if setup.signal == Signal.BUY else "🔴"
            msg = (
                f"{emoji} *CHASTIEFOL SIGNAL*\n"
                f"Pair: XAUUSD\n"
                f"Signal: *{setup.signal.value}*\n"
                f"Entry: `{setup.entry:.2f}`\n"
                f"SL: `{setup.stop_loss:.2f}`\n"
                f"TP: `{setup.take_profit:.2f}`\n"
                f"R:R: `{setup.rr_ratio}`\n"
                f"Confidence: `{setup.confidence*100:.0f}%`\n"
                f"Lots: `{lot}`\n"
                f"Confluence: {setup.confluence} factors\n"
                f"✓ " + "\n✓ ".join(setup.reasons)
            )
            url = (f"https://api.telegram.org/bot{self.config.telegram_token}"
                   f"/sendMessage?chat_id={self.config.telegram_chat_id}"
                   f"&text={urllib.request.quote(msg)}&parse_mode=Markdown")
            urllib.request.urlopen(url, timeout=5)
            log.info("Telegram notification sent.")
        except Exception as e:
            log.error(f"Telegram error: {e}")

    # ──────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────

    def _print_summary(self):
        log.info("\n" + "="*52)
        log.info("  CHASTIEFOL SESSION SUMMARY")
        log.info("="*52)
        log.info(f"  Signals generated:  {self.signal_count}")
        log.info(f"  Trades executed:    {len(self.trade_log)}")
        log.info(f"  Final balance:      ${self.account.balance:,.2f}")
        log.info(f"  Session P&L:        ${self.account.balance - self.config.initial_balance:+,.2f}")
        log.info("="*52)

        if self.trade_log:
            log.info("\n  Trade Log:")
            for t in self.trade_log:
                log.info(f"    {t['outcome']:4s} | {t['direction']:4s} | "
                         f"${t['pnl_usd']:+7.2f} | @ {t['exit']:.2f}")

    def _synthetic_provider(self):
        """Returns a callable that generates synthetic XAUUSD data."""
        import numpy as np, pandas as pd

        def _provider():
            np.random.seed(int(time.time()) % 1000)
            n      = 200
            prices = 2050 + np.cumsum(np.random.randn(n) * 2.5)
            return pd.DataFrame({
                "open":   prices + np.random.randn(n) * 0.4,
                "high":   prices + np.abs(np.random.randn(n)) * 4,
                "low":    prices - np.abs(np.random.randn(n)) * 4,
                "close":  prices,
                "volume": np.abs(np.random.randn(n)) * 1000 + 500,
            })

        return _provider

    def save_trade_log(self, path: str = "trade_log.json"):
        with open(path, "w") as f:
            json.dump(self.trade_log, f, indent=2, default=str)
        log.info(f"Trade log saved → {path}")


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    config = AgentConfig(
        broker="paper",
        symbol="XAUUSD",
        initial_balance=10_000,
        risk_pct=1.0,
        min_confidence=0.50,
        paper_mode=True,
    )
    orchestrator = ChastiefollOrchestrator(config)
    orchestrator.run()
