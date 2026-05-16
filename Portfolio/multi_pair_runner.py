"""
Chastiefol — Multi-Pair Runner (Real-time Orchestrator)
Runs XAUUSD and BTCUSD agents in parallel, coordinates signals,
and executes trades via a shared portfolio with correlation guard.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                   MultiPairRunner (async)                     │
    ├──────────────────────┬──────────────────────────────────────┤
    │  PairWorker: XAUUSD  │  PairWorker: BTCUSD                  │
    │  - ChastiefollAgent  │  - ChastiefollAgent                  │
    │  - SessionFilter     │  - SessionFilter (24/7)              │
    │  - DataBuffer        │  - DataBuffer                        │
    └──────────┬───────────┴──────────────┬───────────────────────┘
               │                          │
               ▼                          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │            PortfolioManager (shared, thread-safe)             │
    │  - Correlation guard  - Risk limits  - Position tracking     │
    └──────────────────────────┬──────────────────────────────────┘
                               ▼
    ┌─────────────────────────────────────────────────────────────┐
    │            Order Executor (cTrader FIX / webhook)             │
    └─────────────────────────────────────────────────────────────┘

Usage:
    from Portfolio.multi_pair_runner import MultiPairRunner
    from Analysis.pair_config import XAUUSD_CONFIG, BTCUSD_CONFIG

    runner = MultiPairRunner(
        pairs=[XAUUSD_CONFIG, BTCUSD_CONFIG],
        initial_balance=10_000,
    )

    # Start (blocking)
    asyncio.run(runner.start())

    # Or integrate into existing event loop
    await runner.start()
"""

import sys
import os

# ── Resolve project root so all internal modules import without PYTHONPATH ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import asyncio
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Any
from enum import Enum

import numpy as np
import pandas as pd

from Analysis.xauusd_engine import ChastiefollAgent, Signal, TradeSetup
from Analysis.pair_config import PairConfig, XAUUSD_CONFIG, BTCUSD_CONFIG, PAIR_CONFIGS
from Risk.risk_manager import (
    AccountState, DrawdownGuard, PositionSizer, SessionFilter, RiskModel
)
from Portfolio.portfolio_manager import (
    PortfolioManager, PortfolioConfig, PortfolioState,
    PairConfig as PortfolioPairConfig, PairCategory, PairStatus,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("MultiPairRunner")


# ──────────────────────────────────────────────
# Data Buffer
# ──────────────────────────────────────────────

class OHLCVBuffer:
    """
    Ring buffer for OHLCV candle data.
    Stores last N candles and provides a pandas DataFrame view.
    """

    def __init__(self, max_bars: int = 200):
        self.max_bars = max_bars
        self.data: List[dict] = []

    def append(self, candle: dict):
        """Add a new candle (dict with open, high, low, close, volume, timestamp)."""
        self.data.append(candle)
        if len(self.data) > self.max_bars:
            self.data = self.data[-self.max_bars:]

    def to_dataframe(self) -> pd.DataFrame:
        """Convert buffer to DataFrame for analysis."""
        if not self.data:
            return pd.DataFrame()
        return pd.DataFrame(self.data)

    @property
    def is_ready(self) -> bool:
        """Whether we have enough data for analysis (min 100 bars)."""
        return len(self.data) >= 100

    @property
    def last_price(self) -> float:
        """Get last close price."""
        return self.data[-1]["close"] if self.data else 0.0

    def __len__(self):
        return len(self.data)


# ──────────────────────────────────────────────
# Pair Worker
# ──────────────────────────────────────────────

@dataclass
class PairWorkerState:
    """Runtime state for a single pair worker."""
    symbol: str
    last_signal: Optional[TradeSetup] = None
    last_signal_time: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    total_signals: int = 0
    signals_accepted: int = 0
    signals_rejected: int = 0


class PairWorker:
    """
    Worker for a single trading pair.
    Manages its own agent, data buffer, and cooldown logic.
    """

    COOLDOWN_SECONDS = 300  # 5 minutes between signals (avoid rapid-fire)

    def __init__(self, pair_config: PairConfig):
        self.pair_config = pair_config
        self.agent = ChastiefollAgent(pair_config=pair_config)
        self.sizer = PositionSizer.from_pair_config(pair_config)
        self.session_filter = SessionFilter.from_pair_config(pair_config)
        self.buffer = OHLCVBuffer(max_bars=200)
        self.state = PairWorkerState(symbol=pair_config.symbol)

        log.info(f"[Worker:{pair_config.symbol}] Initialized | "
                 f"ATR mult: {pair_config.atr_sl_multiplier} | "
                 f"Session: {'24/7' if not pair_config.use_session_filter else 'filtered'} | "
                 f"Min lot: {pair_config.min_lot_size}")

    def feed_candle(self, candle: dict):
        """Feed a new OHLCV candle to this worker."""
        self.buffer.append(candle)

    def analyze(self) -> Optional[TradeSetup]:
        """
        Run analysis on current buffer data.
        Returns TradeSetup if conditions are met, None otherwise.
        """
        if not self.buffer.is_ready:
            return None

        # Check cooldown
        now = datetime.now(timezone.utc)
        if self.state.cooldown_until and now < self.state.cooldown_until:
            return None

        # Check session
        session_active, session_name = self.session_filter.is_active(
            now.hour, now.minute
        )

        # Run agent analysis
        df = self.buffer.to_dataframe()
        setup = self.agent.analyze(df, session_active=session_active)

        if setup:
            self.state.total_signals += 1
            self.state.last_signal = setup
            self.state.last_signal_time = now
            # Set cooldown
            self.state.cooldown_until = datetime.fromtimestamp(
                now.timestamp() + self.COOLDOWN_SECONDS, tz=timezone.utc
            )
            log.info(f"[Worker:{self.pair_config.symbol}] Signal: {setup.signal.value} | "
                     f"Entry: {setup.entry} | SL: {setup.stop_loss} | TP: {setup.take_profit} | "
                     f"Confidence: {setup.confidence*100:.0f}%")

        return setup

    def get_position_size(self, account: AccountState) -> float:
        """Calculate position size for current signal."""
        if not self.state.last_signal:
            return 0.0
        spec = self.sizer.calculate(
            account,
            self.state.last_signal.entry,
            self.state.last_signal.stop_loss,
        )
        return spec.lot_size


# ──────────────────────────────────────────────
# Signal Decision
# ──────────────────────────────────────────────

@dataclass
class SignalDecision:
    """Result of the orchestrator's decision on a signal."""
    symbol: str
    setup: TradeSetup
    approved: bool
    lot_size: float = 0.0
    rejection_reason: str = ""
    correlation_adjusted: bool = False
    timestamp: str = ""


# ──────────────────────────────────────────────
# Multi-Pair Runner (Orchestrator)
# ──────────────────────────────────────────────

class MultiPairRunner:
    """
    Async orchestrator that runs multiple ChastiefollAgent instances in parallel.

    Responsibilities:
    - Manage PairWorker instances (one per symbol)
    - Route incoming candle data to correct worker
    - Coordinate signal approval via PortfolioManager
    - Apply correlation guard (reduce size / reject if too correlated)
    - Execute approved trades (via callback or connector)
    - Track portfolio state across all pairs

    Usage:
        runner = MultiPairRunner(
            pairs=[XAUUSD_CONFIG, BTCUSD_CONFIG],
            initial_balance=10_000,
            on_signal=my_execution_callback,
        )
        await runner.start()
    """

    def __init__(self,
                 pairs: List[PairConfig] = None,
                 initial_balance: float = 10_000.0,
                 risk_pct: float = 1.0,
                 max_total_risk_pct: float = 5.0,
                 max_drawdown_pct: float = 10.0,
                 correlation_threshold: float = 0.7,
                 on_signal: Optional[Callable[[SignalDecision], Any]] = None,
                 on_trade_open: Optional[Callable[[SignalDecision], Any]] = None,
                 analysis_interval: float = 1.0,  # seconds between analysis cycles
                 ):
        # Default to XAUUSD + BTCUSD
        if pairs is None:
            pairs = [XAUUSD_CONFIG, BTCUSD_CONFIG]

        self.pairs = pairs
        self.risk_pct = risk_pct
        self.analysis_interval = analysis_interval
        self.on_signal = on_signal
        self.on_trade_open = on_trade_open

        # Create workers
        self.workers: Dict[str, PairWorker] = {}
        for pair_cfg in pairs:
            self.workers[pair_cfg.symbol] = PairWorker(pair_cfg)

        # Portfolio manager (shared risk)
        portfolio_pairs = []
        for pair_cfg in pairs:
            portfolio_pairs.append(PortfolioPairConfig(
                symbol=pair_cfg.symbol,
                category=PairCategory.CRYPTO if pair_cfg.base_asset == "BTC" else PairCategory.METALS,
                risk_pct=risk_pct,
                max_open_trades=2,
                contract_size=pair_cfg.contract_size,
                spread_avg=pair_cfg.typical_spread,
                commission=pair_cfg.commission_per_lot,
            ))

        self.portfolio = PortfolioManager(PortfolioConfig(
            pairs=portfolio_pairs,
            max_total_risk_pct=max_total_risk_pct,
            max_drawdown_pct=max_drawdown_pct,
            correlation_threshold=correlation_threshold,
            initial_balance=initial_balance,
        ))

        # Shared account state (mirrors portfolio)
        self.account = AccountState(
            balance=initial_balance,
            equity=initial_balance,
            peak_balance=initial_balance,
        )

        # Signal history
        self.decisions: List[SignalDecision] = []
        self._running = False

        log.info(f"[Runner] MultiPairRunner initialized | "
                 f"Pairs: {[p.symbol for p in pairs]} | "
                 f"Balance: ${initial_balance:,.2f} | "
                 f"Max risk: {max_total_risk_pct}%")

    # ──────────────────────────────────────────
    # Data Feed Interface
    # ──────────────────────────────────────────

    def feed_candle(self, symbol: str, candle: dict):
        """
        Feed a new candle to the appropriate worker.
        Call this from your data feed (TradingView WS, cTrader, etc.)

        Args:
            symbol: e.g. "XAUUSD", "BTCUSD"
            candle: dict with keys: open, high, low, close, volume, timestamp
        """
        worker = self.workers.get(symbol)
        if worker:
            worker.feed_candle(candle)
        else:
            log.warning(f"[Runner] Unknown symbol: {symbol}")

    def feed_candles_bulk(self, symbol: str, candles: List[dict]):
        """Feed multiple historical candles at once (for initialization)."""
        worker = self.workers.get(symbol)
        if worker:
            for candle in candles:
                worker.feed_candle(candle)
            log.info(f"[Runner] Loaded {len(candles)} candles for {symbol} "
                     f"(buffer: {len(worker.buffer)} bars)")

    # ──────────────────────────────────────────
    # Analysis Loop
    # ──────────────────────────────────────────

    async def start(self):
        """
        Start the async analysis loop.
        Runs continuously, analyzing all pairs on each cycle.
        """
        self._running = True
        log.info("[Runner] Starting multi-pair analysis loop...")

        while self._running:
            await self._analysis_cycle()
            await asyncio.sleep(self.analysis_interval)

    def stop(self):
        """Stop the analysis loop."""
        self._running = False
        log.info("[Runner] Stopping...")

    async def _analysis_cycle(self):
        """Run one analysis cycle across all pairs."""
        signals = []

        # Run all workers in parallel
        for symbol, worker in self.workers.items():
            setup = worker.analyze()
            if setup:
                signals.append((symbol, worker, setup))

        # Process signals through portfolio manager
        for symbol, worker, setup in signals:
            decision = self._process_signal(symbol, worker, setup)
            self.decisions.append(decision)

            # Fire callbacks
            if self.on_signal:
                self.on_signal(decision)

            if decision.approved and self.on_trade_open:
                self.on_trade_open(decision)

    def _process_signal(self, symbol: str, worker: PairWorker,
                        setup: TradeSetup) -> SignalDecision:
        """
        Process a signal through portfolio risk checks.
        Returns SignalDecision with approval status.
        """
        # Calculate position size
        lot_size = worker.get_position_size(self.account)
        risk_usd = abs(setup.entry - setup.stop_loss) * lot_size * worker.pair_config.contract_size

        # Check portfolio approval
        allowed, reason = self.portfolio.can_open_trade(symbol, risk_usd=risk_usd)

        if not allowed:
            worker.state.signals_rejected += 1
            log.info(f"[Runner] Signal REJECTED for {symbol}: {reason}")
            return SignalDecision(
                symbol=symbol,
                setup=setup,
                approved=False,
                lot_size=0.0,
                rejection_reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Apply correlation adjustment
        adjusted_lot = self.portfolio.get_adjusted_lot_size(symbol, lot_size)
        correlation_adjusted = (adjusted_lot != lot_size)

        # Register trade in portfolio
        self.portfolio.open_trade(
            symbol=symbol,
            direction=setup.signal.value,
            lot_size=adjusted_lot,
            entry=setup.entry,
            sl=setup.stop_loss,
            tp=setup.take_profit,
            risk_usd=risk_usd,
        )

        worker.state.signals_accepted += 1
        log.info(f"[Runner] Signal APPROVED for {symbol} | "
                 f"{setup.signal.value} | Lot: {adjusted_lot} | "
                 f"Corr adjusted: {correlation_adjusted}")

        return SignalDecision(
            symbol=symbol,
            setup=setup,
            approved=True,
            lot_size=adjusted_lot,
            correlation_adjusted=correlation_adjusted,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ──────────────────────────────────────────
    # Trade Closure
    # ──────────────────────────────────────────

    def close_trade(self, symbol: str, pnl: float, lot_size: float = 0, entry: float = 0):
        """
        Register a trade closure (call when SL/TP/manual close occurs).
        Updates portfolio and account state.
        """
        self.portfolio.close_trade(symbol, pnl=pnl, lot_size=lot_size, entry=entry)

        # Sync account state
        self.account.balance = self.portfolio.state.balance
        self.account.equity = self.portfolio.state.equity
        self.account.update_peak()

    # ──────────────────────────────────────────
    # Correlation Update
    # ──────────────────────────────────────────

    def update_correlations(self):
        """
        Update correlation matrix from current buffer data.
        Call periodically (e.g. every hour or at session open).
        """
        price_data = {}
        for symbol, worker in self.workers.items():
            df = worker.buffer.to_dataframe()
            if not df.empty and "close" in df.columns:
                price_data[symbol] = df["close"]

        if len(price_data) >= 2:
            self.portfolio.update_correlation(price_data)

    # ──────────────────────────────────────────
    # Status & Monitoring
    # ──────────────────────────────────────────

    def get_status(self) -> dict:
        """Get comprehensive runner status."""
        portfolio_status = self.portfolio.get_status()

        worker_status = {}
        for symbol, worker in self.workers.items():
            worker_status[symbol] = {
                "buffer_size": len(worker.buffer),
                "is_ready": worker.buffer.is_ready,
                "last_price": worker.buffer.last_price,
                "total_signals": worker.state.total_signals,
                "signals_accepted": worker.state.signals_accepted,
                "signals_rejected": worker.state.signals_rejected,
                "last_signal": worker.state.last_signal.signal.value if worker.state.last_signal else None,
                "last_signal_time": worker.state.last_signal_time.isoformat() if worker.state.last_signal_time else None,
            }

        return {
            "running": self._running,
            "pairs": list(self.workers.keys()),
            "portfolio": portfolio_status,
            "workers": worker_status,
            "total_decisions": len(self.decisions),
            "recent_decisions": [
                {
                    "symbol": d.symbol,
                    "signal": d.setup.signal.value,
                    "approved": d.approved,
                    "lot": d.lot_size,
                    "reason": d.rejection_reason,
                    "time": d.timestamp,
                }
                for d in self.decisions[-10:]  # last 10
            ],
        }

    def print_status(self):
        """Print formatted status to console."""
        status = self.get_status()
        print(f"\n{'═'*60}")
        print(f"  CHASTIEFOL MULTI-PAIR RUNNER STATUS")
        print(f"{'═'*60}")
        print(f"  Running:    {status['running']}")
        print(f"  Pairs:      {status['pairs']}")
        print(f"  Balance:    ${status['portfolio']['balance']:,.2f}")
        print(f"  P&L:        ${status['portfolio']['total_pnl']:+,.2f}")
        print(f"  Drawdown:   {status['portfolio']['drawdown_pct']:.2f}%")
        print(f"  Open pos:   {status['portfolio']['open_positions']}")
        print(f"  Total risk: {status['portfolio']['total_risk_pct']:.1f}%")
        print(f"\n  Workers:")
        for sym, ws in status['workers'].items():
            print(f"    {sym}: {ws['buffer_size']} bars | "
                  f"Signals: {ws['total_signals']} "
                  f"(✓{ws['signals_accepted']} ✗{ws['signals_rejected']}) | "
                  f"Last: {ws['last_signal'] or '—'}")
        print(f"{'═'*60}\n")


# ──────────────────────────────────────────────
# Convenience: Run both pairs
# ──────────────────────────────────────────────

def create_default_runner(
    initial_balance: float = 10_000.0,
    risk_pct: float = 1.0,
    on_signal: Optional[Callable] = None,
) -> MultiPairRunner:
    """
    Create a MultiPairRunner with default XAUUSD + BTCUSD configuration.

    Usage:
        runner = create_default_runner(initial_balance=10_000)
        # Feed data from your source
        runner.feed_candle("XAUUSD", candle_data)
        runner.feed_candle("BTCUSD", candle_data)
        # Run analysis
        asyncio.run(runner.start())
    """
    return MultiPairRunner(
        pairs=[XAUUSD_CONFIG, BTCUSD_CONFIG],
        initial_balance=initial_balance,
        risk_pct=risk_pct,
        on_signal=on_signal,
    )


# ──────────────────────────────────────────────
# Demo
# ──────────────────────────────────────────────

if __name__ == "__main__":
    """
    LIVE MODE: Connect to cTrader FIX Price API for real market data.
    
    Requires:
    - .env with CTRADER_PASSWORD set
    - cTrader FIX Price connection (demo-uk-eqx-01.p.c-trader.com:5211)
    
    Symbol IDs (cTrader):
    - XAUUSD = 41
    - BTCUSD = 22395
    
    Current market prices (as of config):
    - XAUUSD ≈ $4,538
    - BTCUSD ≈ $79,050
    """
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    def on_signal_callback(decision: SignalDecision):
        status = "APPROVED" if decision.approved else "REJECTED"
        print(f"  [{decision.symbol}] {status} | {decision.setup.signal.value} | "
              f"Entry: ${decision.setup.entry:,.2f} | "
              f"SL: ${decision.setup.stop_loss:,.2f} | "
              f"TP: ${decision.setup.take_profit:,.2f} | "
              f"Lot: {decision.lot_size} | {decision.rejection_reason}")

    # Create runner with real market parameters
    runner = create_default_runner(
        initial_balance=10_000,
        risk_pct=1.0,
        on_signal=on_signal_callback,
    )

    print(f"\n{'═'*60}")
    print(f"  CHASTIEFOL MULTI-PAIR RUNNER — LIVE MODE")
    print(f"{'═'*60}")
    print(f"  Pairs:   XAUUSD (FIX ID: 41)   | BTCUSD (FIX ID: 22395)")
    print(f"  Prices:  XAUUSD ≈ $4,538       | BTCUSD ≈ $79,050")
    print(f"  Session: XAUUSD London/NY only  | BTCUSD 24/7")
    print(f"  ATR SL:  XAUUSD × 2.0          | BTCUSD × 2.5")
    print(f"  Min Lot: XAUUSD 0.01           | BTCUSD 0.01")
    print(f"{'═'*60}")
    print(f"\n  To run live, integrate with cTrader FIX price stream:")
    print(f"    from Connector.ctrader_fix import FIXConnection, FIXConfig, FIXSymbolMap")
    print(f"    price_conn = FIXConnection(host, port, config, 'QUOTE', 'PRICE')")
    print(f"    price_conn.subscribe_market_data('XAUUSD', '1', symbol_map)")
    print(f"    price_conn.subscribe_market_data('BTCUSD', '2', symbol_map)")
    print(f"")
    print(f"  Then feed candles to runner:")
    print(f"    runner.feed_candle('XAUUSD', candle_dict)")
    print(f"    runner.feed_candle('BTCUSD', candle_dict)")
    print(f"    asyncio.run(runner.start())")
    print(f"\n  Runner status:")
    runner.print_status()
