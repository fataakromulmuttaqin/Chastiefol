"""
Chastiefol — Multi-Pair Unified Main (Single Process)
Runs XAUUSD + BTCUSD with 1 SHARED FIX Trade connection.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │              chastiefol_multi_main.py (1 process)             │
    ├─────────────────────────────────────────────────────────────┤
    │                                                              │
    │  ┌─────────────────────────────────────────────────────┐    │
    │  │  1x FIX PRICE Connection (port 5211)                 │    │
    │  │  → subscribe XAUUSD (ID 41)                          │    │
    │  │  → subscribe BTCUSD (ID 22395)                       │    │
    │  └─────────────────────────────────────────────────────┘    │
    │                                                              │
    │  ┌─────────────────────────────────────────────────────┐    │
    │  │  1x FIX TRADE Connection (port 5212)  ← SHARED       │    │
    │  │  → executes orders for ALL pairs                      │    │
    │  └─────────────────────────────────────────────────────┘    │
    │                                                              │
    │  ┌────────────────┐    ┌────────────────┐                   │
    │  │ XAUUSD Worker  │    │ BTCUSD Worker  │                   │
    │  │ - DataFeed     │    │ - DataFeed     │                   │
    │  │ - Agent        │    │ - Agent        │                   │
    │  │ - Session filt │    │ - 24/7         │                   │
    │  └───────┬────────┘    └───────┬────────┘                   │
    │          │                     │                             │
    │          └──────────┬──────────┘                             │
    │                     ▼                                        │
    │  ┌─────────────────────────────────────────────────────┐    │
    │  │  MultiPairRunner (Portfolio + Risk + Correlation)     │    │
    │  └──────────────────────┬──────────────────────────────┘    │
    │                         ▼                                    │
    │  ┌─────────────────────────────────────────────────────┐    │
    │  │  Shared FIX Trade Executor + Telegram Notifier        │    │
    │  └─────────────────────────────────────────────────────┘    │
    └─────────────────────────────────────────────────────────────┘

Why 1 shared Trade connection?
  cTrader FIX only allows 1 active Trade session per account.
  Running 2 instances causes the 2nd to timeout on Logon.

Usage:
    python chastiefol_multi_main.py
    # or
    docker-compose up chastiefol-multi
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from Connector.ctrader_fix import (
    CTraderFIXConnector, FIXConfig, FIXSymbolMap
)
from DataFeed.data_feed import DataFeedManager, DataFeedConfig, Timeframe
from Notification.telegram_notifier import TelegramNotifier, TelegramConfig
from Analysis.pair_config import XAUUSD_CONFIG, BTCUSD_CONFIG, get_pair_config
from Portfolio.multi_pair_runner import (
    MultiPairRunner, SignalDecision, create_default_runner
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Chastiefol.Multi")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class MultiPairConfig:
    """Configuration loaded from environment."""

    def __init__(self):
        # FIX Connection (SHARED — 1 price + 1 trade)
        self.fix_price_host = os.getenv("FIX_PRICE_HOST", "demo-uk-eqx-01.p.c-trader.com")
        self.fix_price_port = int(os.getenv("FIX_PRICE_PORT", "5211"))
        self.fix_trade_host = os.getenv("FIX_TRADE_HOST", "demo-uk-eqx-01.p.c-trader.com")
        self.fix_trade_port = int(os.getenv("FIX_TRADE_PORT", "5212"))
        self.fix_sender_comp_id = os.getenv("FIX_SENDER_COMP_ID", "demo.ctrader.5820056")
        self.fix_target_comp_id = os.getenv("FIX_TARGET_COMP_ID", "cServer")
        self.fix_password = os.getenv("FIX_PASSWORD", os.getenv("CTRADER_PASSWORD", ""))

        # Data Feed — GoldAPI only (FIX provides live price fallback)
        self.goldapi_api_key = os.getenv("GOLDAPI_API_KEY", "")

        # Telegram
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Trading
        self.initial_balance = float(os.getenv("INITIAL_BALANCE", "10000"))
        self.risk_pct = float(os.getenv("RISK_PCT", "1.0"))
        self.paper_mode = os.getenv("PAPER_MODE", "false").lower() == "true"
        self.scan_interval_sec = int(os.getenv("SCAN_INTERVAL_SEC", "60"))

        # Pairs
        self.pairs = ["XAUUSD", "BTCUSD"]


# ──────────────────────────────────────────────
# Multi-Pair Unified Main
# ──────────────────────────────────────────────

class ChastiefollMultiMain:
    """
    Unified multi-pair main process.
    
    Key difference from old architecture:
    - OLD: 2 separate processes, each with own FIX Trade connection → 2nd fails
    - NEW: 1 process, 1 shared FIX Trade connection, 2 analysis workers
    """

    def __init__(self):
        self.config = MultiPairConfig()
        self.fix_connector: Optional[CTraderFIXConnector] = None
        self.telegram: Optional[TelegramNotifier] = None
        self.runner: Optional[MultiPairRunner] = None
        self.data_feeds: dict = {}  # symbol → DataFeedManager
        self._running = False

    async def start(self):
        """Initialize all components and start."""
        log.info("=" * 60)
        log.info("  CHASTIEFOL MULTI-PAIR UNIFIED AGENT")
        log.info("  Pairs: XAUUSD (ID:41) + BTCUSD (ID:22395)")
        log.info("  Architecture: 1 shared FIX Trade connection")
        log.info("=" * 60)

        # 1. Telegram
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            tg_config = TelegramConfig(
                bot_token=self.config.telegram_bot_token,
                chat_id=self.config.telegram_chat_id,
            )
            self.telegram = TelegramNotifier(tg_config)
            await self.telegram.start()
            log.info(f"✓ Telegram connected (Chat {self.config.telegram_chat_id})")

        # 2. FIX Connection — 1 SHARED instance for both pairs
        if self.config.fix_password and not self.config.paper_mode:
            fix_config = FIXConfig(
                price_host=self.config.fix_price_host,
                price_port=self.config.fix_price_port,
                trade_host=self.config.fix_trade_host,
                trade_port=self.config.fix_trade_port,
                sender_comp_id=self.config.fix_sender_comp_id,
                target_comp_id=self.config.fix_target_comp_id,
                password=self.config.fix_password,
            )
            self.fix_connector = CTraderFIXConnector(fix_config)
            connected = self.fix_connector.connect()

            if connected:
                log.info("✓ FIX PRICE: Logon accepted")
                log.info("✓ FIX TRADE: Logon accepted (SHARED for all pairs)")

                # Subscribe to market data for BOTH pairs via 1 Price connection
                self.fix_connector.subscribe("XAUUSD")
                log.info("  → Subscribed XAUUSD (ID: 41)")
                self.fix_connector.subscribe("BTCUSD")
                log.info("  → Subscribed BTCUSD (ID: 22395)")
            else:
                log.error("✗ FIX connection failed — falling back to paper mode")
                self.config.paper_mode = True
                if self.telegram:
                    await self.telegram.send_error(
                        "FIX connection failed — running in paper mode",
                        context="Multi-pair startup"
                    )
        else:
            if not self.config.fix_password:
                log.warning("⚠ No FIX_PASSWORD set — paper mode")
            log.info("✓ Paper mode active")

        # 3. Data Feeds (TradingView WebSocket primary — FREE, no API key)
        for symbol in self.config.pairs:
            if symbol == "XAUUSD":
                feed_symbol = "OANDA:XAUUSD"
                binance_symbol = "PAXG/USDT"  # Gold proxy via PAXG
            elif symbol == "BTCUSD":
                feed_symbol = f"BINANCE:BTCUSDT"
                binance_symbol = "BTC/USDT"  # Real BTC data from Binance
            else:
                feed_symbol = f"BINANCE:{symbol.replace('USD', 'USDT')}"
                binance_symbol = f"{symbol.replace('USD', '/USDT')}"
            feed_config = DataFeedConfig(
                symbol=feed_symbol,
                default_timeframe=Timeframe.H1,
                bars_to_load=200,
                binance_gold_symbol=binance_symbol,
            )
            feed = DataFeedManager(feed_config)
            await feed.initialize()
            self.data_feeds[symbol] = feed
            log.info(f"✓ DataFeed: TradingView WS → {feed_symbol} (CCXT fallback: {binance_symbol})")

        # 4. Multi-Pair Runner (portfolio orchestrator)
        self.runner = create_default_runner(
            initial_balance=self.config.initial_balance,
            risk_pct=self.config.risk_pct,
            on_signal=self._on_signal,
        )
        log.info(f"✓ MultiPairRunner initialized (balance: ${self.config.initial_balance:,.2f})")

        # 5. Load historical data into runner buffers
        await self._load_historical_data()

        # 6. Start main loop
        self._running = True
        log.info("")
        log.info("=" * 60)
        log.info("  ALL SYSTEMS ONLINE — Multi-pair agent active")
        log.info("  XAUUSD: London/NY sessions | BTCUSD: 24/7")
        log.info("  Scan interval: %ds", self.config.scan_interval_sec)
        log.info("=" * 60)
        log.info("")

        await self._main_loop()

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        log.info("Shutting down multi-pair agent...")

        if self.fix_connector:
            self.fix_connector.disconnect()
        for feed in self.data_feeds.values():
            await feed.shutdown()
        if self.telegram:
            await self.telegram.stop()

        log.info("Shutdown complete.")

    # ──────────────────────────────────────────
    # Main Loop
    # ──────────────────────────────────────────

    async def _main_loop(self):
        """Main analysis loop — runs both pairs every scan interval."""
        while self._running:
            try:
                await self._scan_cycle()
                await asyncio.sleep(self.config.scan_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Main loop error: {e}")
                if self.telegram:
                    await self.telegram.send_error(str(e), "Main Loop")
                await asyncio.sleep(self.config.scan_interval_sec)

    async def _scan_cycle(self):
        """One analysis cycle — fetch latest candle for each pair, feed to runner."""
        now = datetime.now(timezone.utc)
        log.info(f"--- Scan Cycle {now.strftime('%H:%M:%S UTC')} ---")

        # Fetch latest candle for each pair and feed to runner
        for symbol in self.config.pairs:
            feed = self.data_feeds.get(symbol)
            df = None
            if feed:
                df = await feed.get_ohlcv(timeframe=Timeframe.H1, bars=5)
                if df is not None and not df.empty:
                    latest = df.iloc[-1]
                    candle = {
                        "open": float(latest.get("open", 0)),
                        "high": float(latest.get("high", 0)),
                        "low": float(latest.get("low", 0)),
                        "close": float(latest.get("close", 0)),
                        "volume": float(latest.get("volume", 0)),
                        "timestamp": str(latest.get("timestamp", "")),
                    }
                    self.runner.feed_candle(symbol, candle)

            # Fallback: if no data feed or feed failed, use FIX live quote
            if df is None or df.empty:
                quote = self.fix_connector.get_latest_quote(symbol) if self.fix_connector else None
                if quote:
                    bid = quote.get("bid", "")
                    ask = quote.get("ask", "")
                    if bid and ask:
                        mid_price = (float(bid) + float(ask)) / 2
                    elif ask:
                        mid_price = float(ask)
                    elif bid:
                        mid_price = float(bid)
                    else:
                        mid_price = None
                    if mid_price:
                        candle = {
                            "open": mid_price, "high": mid_price,
                            "low": mid_price, "close": mid_price,
                            "volume": 0, "timestamp": now.isoformat(),
                        }
                        self.runner.feed_candle(symbol, candle)
                        log.info(f"  [FIX fallback] {symbol} = ${mid_price:.2f}")

        # Run analysis cycle
        await self.runner._analysis_cycle()

        # Update correlations periodically
        if now.minute == 0:  # Every hour
            self.runner.update_correlations()

    # ──────────────────────────────────────────
    # Signal Execution
    # ──────────────────────────────────────────

    def _on_signal(self, decision: SignalDecision):
        """Called when MultiPairRunner approves/rejects a signal (sync wrapper)."""
        if decision.approved:
            log.info(f"✓ SIGNAL APPROVED: {decision.setup.signal.value} {decision.symbol} "
                     f"@ ${decision.setup.entry:.2f} | Lot: {decision.lot_size}")
            # Schedule async execution
            asyncio.create_task(self._execute_signal(decision))
        else:
            log.info(f"✗ Signal rejected: {decision.symbol} — {decision.rejection_reason}")
            if self.telegram:
                await self.telegram.send_signal_rejected(
                    symbol=decision.symbol,
                    action=decision.setup.signal.value,
                    entry=decision.setup.entry,
                    reason=decision.rejection_reason,
                    confidence=decision.setup.confidence,
                )

    async def _execute_signal(self, decision: SignalDecision):
        """Async signal handler: execute trade + send Telegram."""
        log.info(f"→ Executing {decision.symbol} {decision.setup.signal.value}")

        # Execute via shared FIX Trade connection
        await self._execute_trade(decision)

        # Notify Telegram
        if self.telegram:
            await self.telegram.send_signal_alert(
                action=decision.setup.signal.value,
                symbol=decision.symbol,
                entry=decision.setup.entry,
                stop_loss=decision.setup.stop_loss,
                take_profit=decision.setup.take_profit,
                confidence=decision.setup.confidence,
                rr_ratio=decision.setup.rr_ratio,
                lot_size=decision.lot_size,
                reasons=decision.setup.reasons,
            )

    async def _execute_trade(self, decision: SignalDecision):
        """Execute trade via shared FIX Trade connection."""
        if self.config.paper_mode:
            log.info(f"  [PAPER] {decision.setup.signal.value} {decision.symbol} "
                     f"{decision.lot_size} lots @ ${decision.setup.entry:.2f}")
            return

        if not self.fix_connector:
            log.error("No FIX connector available for execution!")
            return

        # Execute via shared connector (uses single Trade session)
        result = await self.fix_connector.execute_order(
            symbol=decision.symbol,
            side=decision.setup.signal.value,
            volume=decision.lot_size,
            stop_loss=decision.setup.stop_loss,
            take_profit=decision.setup.take_profit,
        )

        if result.get("success"):
            log.info(f"  ✓ Order filled: {decision.symbol} @ ${result.get('execution_price', 0):.2f}")
            if self.telegram:
                await self.telegram.send_order_executed(
                    action=decision.setup.signal.value,
                    symbol=decision.symbol,
                    volume=decision.lot_size,
                    price=result.get("execution_price", decision.setup.entry),
                    order_id=result.get("order_id", ""),
                    stop_loss=decision.setup.stop_loss,
                    take_profit=decision.setup.take_profit,
                )
        else:
            error_msg = result.get("text", "Unknown error")
            log.error(f"  ✗ Order failed: {error_msg}")
            if self.telegram:
                await self.telegram.send_error(
                    f"Order failed for {decision.symbol}: {error_msg}",
                    context=f"{decision.setup.signal.value} {decision.lot_size} lots"
                )

    # ──────────────────────────────────────────
    # Historical Data Loading
    # ──────────────────────────────────────────

    async def _load_historical_data(self):
        """Load 150 H1 bars for each pair into runner buffers."""
        for symbol in self.config.pairs:
            feed = self.data_feeds.get(symbol)
            if not feed:
                log.warning(f"No data feed for {symbol} — buffer will be empty")
                continue

            df = await feed.get_ohlcv(timeframe=Timeframe.H1, bars=150)
            if df is not None and not df.empty:
                candles = []
                for _, row in df.iterrows():
                    candles.append({
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": float(row.get("volume", 0)),
                        "timestamp": str(row.get("timestamp", "")),
                    })
                self.runner.feed_candles_bulk(symbol, candles)
                log.info(f"  ✓ Loaded {len(candles)} H1 bars for {symbol} "
                         f"(last: ${candles[-1]['close']:.2f})")
            else:
                log.warning(f"  ⚠ No historical data for {symbol}")


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

async def main():
    """Main entry point."""
    agent = ChastiefollMultiMain()
    try:
        await agent.start()
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
