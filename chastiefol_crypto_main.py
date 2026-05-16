"""
Chastiefol — Crypto Trading Orchestrator (Binance)
Main entry point for autonomous crypto trading across all Binance spot pairs.

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │              ChastiefollCrypto (Main Orchestrator)               │
  ├─────────────────────────────────────────────────────────────────┤
  │  CryptoDataFeedManager ──── CCXT/Binance + CoinGecko + WS      │
  │  BinanceWebSocket ────────── Real-time kline streaming          │
  │  CryptoAnalysisEngine ───── SMC + Indicators + Volume           │
  │  CryptoScanner ──────────── Multi-pair scanning                 │
  │  BinanceConnector ────────── Order execution (CCXT)             │
  │  CryptoRiskManager ──────── Position sizing + DD protection     │
  │  TelegramNotifier ────────── Real-time alerts                   │
  └─────────────────────────────────────────────────────────────────┘

Modes:
  - SCANNER: Scan watchlist, signal when setups found (no execution)
  - AUTONOMOUS: Full auto — scan + analyze + execute + manage
  - PAPER: Same as autonomous but with paper trading

Data Providers:
  1. CCXT/Binance (primary) — api.binance.com/api/v3/klines
  2. Binance WebSocket — wss://stream.binance.com:9443
  3. CoinGecko (fallback) — free market data
  4. CoinMarketCap (metrics) — market cap data
  5. CoinStats (supplementary) — portfolio data
  6. Historical: data.binance.vision

Usage:
    # Start with environment variables
    python chastiefol_crypto_main.py

    # Or import and configure
    from chastiefol_crypto_main import ChastiefollCrypto, CryptoConfig
    bot = ChastiefollCrypto(CryptoConfig.from_env())
    asyncio.run(bot.run_forever())
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

# Internal modules
from CryptoDataFeed.crypto_data_feed import (
    CryptoDataFeedManager, CryptoDataFeedConfig,
)
from CryptoDataFeed.providers import CryptoTimeframe, CryptoPriceQuote
from CryptoDataFeed.binance_ws import BinanceWebSocket, KlineUpdate
from Connector.binance_connector import (
    BinanceConnector, BinanceConfig, OrderResult, OrderSide,
)
from Analysis.crypto_engine import CryptoAnalysisEngine, CryptoScanner
from Analysis.crypto_pair_config import (
    get_crypto_pair_config, get_config_or_default, get_watchlist,
    CryptoPairConfig, CRYPTO_CONFIGS,
)
from Risk.crypto_risk_manager import (
    CryptoRiskManager, CryptoRiskConfig, CryptoPositionSize,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Chastiefol.Crypto")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class CryptoMode(str, Enum):
    SCANNER = "scanner"         # Signal-only, no execution
    AUTONOMOUS = "autonomous"   # Full auto trading
    PAPER = "paper"             # Paper trading (simulated)


@dataclass
class CryptoConfig:
    """Master configuration for crypto trading."""
    # Mode
    mode: CryptoMode = CryptoMode.PAPER
    watchlist_tier: str = "top20"    # "top10", "top20", "defi", "ai", "all"

    # Account
    initial_balance: float = 10000.0

    # Binance API
    binance_api_key: str = ""
    binance_secret: str = ""
    binance_sandbox: bool = False

    # Data Providers
    coingecko_api_key: str = ""
    coinmarketcap_api_key: str = ""
    coinstats_api_key: str = ""

    # Strategy
    timeframe: str = "1h"
    scan_interval_sec: int = 300    # 5 minutes between scans
    min_confidence: float = 0.50
    max_signals_per_scan: int = 3   # Max trades to open per scan cycle

    # Risk
    risk_pct_per_trade: float = 1.5
    max_open_trades: int = 5
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 15.0

    # WebSocket
    enable_websocket: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "CryptoConfig":
        """Load configuration from environment variables."""
        return cls(
            mode=CryptoMode(os.getenv("CRYPTO_MODE", "paper")),
            watchlist_tier=os.getenv("CRYPTO_WATCHLIST", "top20"),
            initial_balance=float(os.getenv("CRYPTO_INITIAL_BALANCE", "10000")),
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_secret=os.getenv("BINANCE_SECRET", ""),
            binance_sandbox=os.getenv("BINANCE_SANDBOX", "false").lower() == "true",
            coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
            coinmarketcap_api_key=os.getenv("COINMARKETCAP_API_KEY", ""),
            coinstats_api_key=os.getenv("COINSTATS_API_KEY", ""),
            timeframe=os.getenv("CRYPTO_TIMEFRAME", "1h"),
            scan_interval_sec=int(os.getenv("CRYPTO_SCAN_INTERVAL", "300")),
            min_confidence=float(os.getenv("CRYPTO_MIN_CONFIDENCE", "0.50")),
            max_signals_per_scan=int(os.getenv("CRYPTO_MAX_SIGNALS_PER_SCAN", "3")),
            risk_pct_per_trade=float(os.getenv("CRYPTO_RISK_PCT", "1.5")),
            max_open_trades=int(os.getenv("CRYPTO_MAX_OPEN_TRADES", "5")),
            max_daily_loss_pct=float(os.getenv("CRYPTO_MAX_DAILY_LOSS", "5.0")),
            max_drawdown_pct=float(os.getenv("CRYPTO_MAX_DRAWDOWN", "15.0")),
            enable_websocket=os.getenv("CRYPTO_WEBSOCKET", "true").lower() == "true",
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )



# ──────────────────────────────────────────────
# Main Orchestrator
# ──────────────────────────────────────────────

class ChastiefollCrypto:
    """
    Production-ready crypto trading orchestrator for Binance.
    Integrates all components: data feed, analysis, execution, risk management.
    """

    def __init__(self, config: CryptoConfig = None):
        self.config = config or CryptoConfig.from_env()
        self._is_running = False

        # Watchlist
        self._watchlist = get_watchlist(self.config.watchlist_tier)

        # Components (initialized in start())
        self.data_feed: Optional[CryptoDataFeedManager] = None
        self.connector: Optional[BinanceConnector] = None
        self.risk_manager: Optional[CryptoRiskManager] = None
        self.scanner: Optional[CryptoScanner] = None
        self.telegram = None

        # State
        self._scan_task: Optional[asyncio.Task] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ohlcv_cache: Dict[str, any] = {}
        self._signal_history: List[Dict] = []
        self._cycle_count = 0

        log.info(f"{'='*60}")
        log.info(f"  CHASTIEFOL CRYPTO TRADING BOT")
        log.info(f"{'='*60}")
        log.info(f"  Mode:       {self.config.mode.value.upper()}")
        log.info(f"  Watchlist:  {self.config.watchlist_tier} ({len(self._watchlist)} pairs)")
        log.info(f"  Timeframe:  {self.config.timeframe}")
        log.info(f"  Balance:    ${self.config.initial_balance:,.2f}")
        log.info(f"  Risk/Trade: {self.config.risk_pct_per_trade}%")
        log.info(f"  Max Trades: {self.config.max_open_trades}")
        log.info(f"  Exchange:   Binance {'(Sandbox)' if self.config.binance_sandbox else '(Live)'}")
        log.info(f"  WebSocket:  {'Enabled' if self.config.enable_websocket else 'Disabled'}")
        log.info(f"{'='*60}")

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    async def start(self):
        """Initialize all components and start trading."""
        log.info("Starting Chastiefol Crypto...")

        # 1. Initialize Data Feed
        feed_config = CryptoDataFeedConfig(
            binance_api_key=self.config.binance_api_key,
            binance_secret=self.config.binance_secret,
            sandbox_mode=self.config.binance_sandbox,
            coingecko_api_key=self.config.coingecko_api_key,
            coinmarketcap_api_key=self.config.coinmarketcap_api_key,
            coinstats_api_key=self.config.coinstats_api_key,
            enable_websocket=self.config.enable_websocket,
        )
        self.data_feed = CryptoDataFeedManager(feed_config)
        await self.data_feed.initialize()
        log.info("  ✓ Data feed initialized")

        # 2. Initialize Binance Connector
        is_paper = self.config.mode in (CryptoMode.PAPER, CryptoMode.SCANNER)
        connector_config = BinanceConfig(
            api_key=self.config.binance_api_key,
            secret=self.config.binance_secret,
            sandbox=self.config.binance_sandbox,
            paper_mode=is_paper,
        )
        self.connector = BinanceConnector(connector_config)
        await self.connector.connect()
        log.info(f"  ✓ Binance connector ({'PAPER' if is_paper else 'LIVE'})")

        # 3. Initialize Risk Manager
        risk_config = CryptoRiskConfig(
            initial_balance=self.config.initial_balance,
            risk_pct_per_trade=self.config.risk_pct_per_trade,
            max_open_trades=self.config.max_open_trades,
            max_daily_loss_pct=self.config.max_daily_loss_pct,
            max_drawdown_pct=self.config.max_drawdown_pct,
        )
        self.risk_manager = CryptoRiskManager(risk_config)
        log.info("  ✓ Risk manager initialized")

        # 4. Initialize Scanner
        self.scanner = CryptoScanner(min_confidence=self.config.min_confidence)
        log.info("  ✓ Multi-pair scanner initialized")

        # 5. Initialize Telegram (optional)
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            try:
                from Notification.telegram_notifier import TelegramNotifier, TelegramConfig
                tg_config = TelegramConfig(
                    bot_token=self.config.telegram_bot_token,
                    chat_id=self.config.telegram_chat_id,
                )
                self.telegram = TelegramNotifier(tg_config)
                await self.telegram.start()
                log.info("  ✓ Telegram notifier started")
            except Exception as e:
                log.warning(f"  ⚠ Telegram setup failed: {e}")

        # 6. Start background tasks
        self._is_running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        log.info(f"  ✓ Scan loop started (interval: {self.config.scan_interval_sec}s)")

        # 7. Start WebSocket streaming for watchlist
        if self.config.enable_websocket and self.data_feed.websocket:
            await self.data_feed.start_streaming(
                symbols=self._watchlist[:50],  # WS limit
                interval=self.config.timeframe,
                on_kline=self._on_kline_update,
            )
            log.info(f"  ✓ WebSocket streaming for {min(len(self._watchlist), 50)} pairs")

        log.info(f"\n{'='*60}")
        log.info(f"  ALL SYSTEMS ONLINE — Chastiefol Crypto is active")
        log.info(f"  Scanning {len(self._watchlist)} pairs every "
                 f"{self.config.scan_interval_sec}s")
        log.info(f"{'='*60}\n")

    async def stop(self):
        """Gracefully stop all components."""
        log.info("Shutting down Chastiefol Crypto...")
        self._is_running = False

        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        if self.data_feed:
            await self.data_feed.shutdown()
        if self.connector:
            await self.connector.disconnect()
        if self.telegram:
            await self.telegram.stop()

        self._print_summary()
        log.info("Chastiefol Crypto shutdown complete.")

    async def run_forever(self):
        """Main entry — start and run until interrupted."""
        await self.start()
        try:
            while self._is_running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    # ──────────────────────────────────────────
    # Scan Loop (Core Trading Logic)
    # ──────────────────────────────────────────

    async def _scan_loop(self):
        """Main scanning loop — analyze pairs and generate/execute signals."""
        # Initial delay to let data feed warm up
        await asyncio.sleep(5)

        while self._is_running:
            try:
                await self._scan_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Scan cycle error: {e}")
                await self._notify_error(str(e))

            await asyncio.sleep(self.config.scan_interval_sec)

    async def _scan_cycle(self):
        """Single scan cycle: fetch data → analyze → execute."""
        self._cycle_count += 1
        now = datetime.now(timezone.utc)
        log.info(f"─── Scan Cycle #{self._cycle_count} | "
                 f"{now.strftime('%H:%M:%S UTC')} | "
                 f"Pairs: {len(self._watchlist)} ───")

        # 1. Risk check
        risk_status = self.risk_manager.check_risk()
        if not risk_status["trading_allowed"]:
            log.warning(f"Trading blocked: {risk_status['reason']}")
            return

        for w in risk_status.get("warnings", []):
            log.warning(w)

        # 2. Manage existing trades
        await self._manage_open_trades()

        # 3. Fetch OHLCV data for all watchlist pairs
        pair_data = await self._fetch_watchlist_data()
        if not pair_data:
            log.info("No data available for analysis")
            return

        # 4. Scan for signals
        pair_configs = {
            sym: get_config_or_default(sym) for sym in pair_data.keys()
        }
        signals = self.scanner.scan(pair_data, pair_configs)

        if not signals:
            log.info(f"No signals found across {len(pair_data)} pairs")
            return

        log.info(f"Found {len(signals)} signal(s)")

        # 5. Process top signals (limited by max_signals_per_scan)
        executed = 0
        for signal_info in signals[:self.config.max_signals_per_scan]:
            if executed >= self.config.max_signals_per_scan:
                break

            # Skip if we already have a position in this pair
            open_trades = self.risk_manager.get_open_trades()
            if signal_info["symbol"] in open_trades:
                continue

            result = await self._process_signal(signal_info, risk_status)
            if result:
                executed += 1

        if executed > 0:
            log.info(f"Executed {executed} trade(s) this cycle")

    async def _fetch_watchlist_data(self) -> Dict[str, any]:
        """Fetch OHLCV data for all watchlist pairs."""
        pair_data = {}
        timeframe_enum = self._get_timeframe_enum()

        # Fetch in batches to respect rate limits
        batch_size = 10
        for i in range(0, len(self._watchlist), batch_size):
            batch = self._watchlist[i:i + batch_size]
            tasks = [
                self.data_feed.get_ohlcv(sym, timeframe_enum, bars=200)
                for sym in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for sym, result in zip(batch, results):
                if isinstance(result, Exception):
                    continue
                if result is not None and not result.empty and len(result) >= 50:
                    pair_data[sym] = result

        log.info(f"Fetched data for {len(pair_data)}/{len(self._watchlist)} pairs")
        return pair_data

    async def _process_signal(self, signal_info: Dict, risk_status: Dict) -> bool:
        """Process a single signal through risk checks and execution."""
        symbol = signal_info["symbol"]
        setup = signal_info["setup"]
        pair_config = get_config_or_default(symbol)

        log.info(f"  Processing: {setup.signal.value} {symbol} | "
                 f"Conf: {setup.confidence*100:.0f}% | "
                 f"R:R: {setup.rr_ratio}")

        # Calculate position size
        position = self.risk_manager.calculate_position(
            symbol=symbol,
            entry_price=setup.entry,
            stop_loss=setup.stop_loss,
            pair_config=pair_config,
        )

        if not position.is_valid:
            log.info(f"    Position rejected: {position.rejection_reason}")
            return False

        # Apply drawdown multiplier
        adjusted_amount = position.amount * risk_status["size_multiplier"]
        if adjusted_amount <= 0:
            return False

        # Scanner mode — signal only, no execution
        if self.config.mode == CryptoMode.SCANNER:
            await self._notify_signal(setup, position)
            self._signal_history.append({
                "symbol": symbol,
                "signal": setup.signal.value,
                "entry": setup.entry,
                "sl": setup.stop_loss,
                "tp": setup.take_profit,
                "confidence": setup.confidence,
                "amount": adjusted_amount,
                "time": datetime.now(timezone.utc).isoformat(),
            })
            return True

        # Execute order
        side = "buy" if setup.signal.value == "BUY" else "sell"
        order_result = await self.connector.open_position(
            symbol=symbol,
            side=side,
            amount=adjusted_amount,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            comment=f"Chast_{setup.confidence*100:.0f}pct",
        )

        if order_result.success:
            # Register with risk manager
            category = pair_config.category.value if hasattr(pair_config.category, 'value') else ""
            self.risk_manager.open_trade(
                symbol=symbol,
                side=side,
                amount=adjusted_amount,
                entry_price=order_result.average_price or setup.entry,
                stop_loss=setup.stop_loss,
                take_profit=setup.take_profit,
                category=category,
            )

            log.info(f"    ✓ EXECUTED: {side.upper()} {adjusted_amount} {symbol} "
                     f"@ ${order_result.average_price or setup.entry:,.2f}")

            await self._notify_trade(setup, position, order_result)
            return True
        else:
            log.warning(f"    ✗ Order failed: {order_result.error_message}")
            return False

    # ──────────────────────────────────────────
    # Trade Management
    # ──────────────────────────────────────────

    async def _manage_open_trades(self):
        """Check and manage all open trades (SL/TP/breakeven)."""
        open_trades = self.risk_manager.get_open_trades()
        if not open_trades:
            return

        for symbol, trade in list(open_trades.items()):
            # Get current price
            quote = await self.data_feed.get_quote(symbol)
            if not quote or quote.price <= 0:
                continue

            current_price = quote.price
            mgmt = self.risk_manager.check_trade_management(symbol, current_price)

            if mgmt["action"] == "close":
                # Close the trade
                log.info(f"  Closing {symbol}: {mgmt['reason']} | "
                         f"P&L: ${mgmt['pnl']:+,.2f}")
                
                close_result = await self.connector.close_position(
                    symbol=symbol,
                    amount=trade.amount,
                )
                
                if close_result.success:
                    exit_price = close_result.average_price or current_price
                    pnl = self.risk_manager.close_trade(symbol, exit_price)
                    await self._notify_close(symbol, pnl, mgmt["reason"])

            elif mgmt["action"] == "manage":
                for rec in mgmt.get("recommendations", []):
                    if rec["type"] == "move_sl":
                        log.info(f"  {symbol}: {rec['reason']}")
                        # In spot, we'd cancel old stop order and place new one
                        # For paper mode this is tracked internally

    async def _on_kline_update(self, update: KlineUpdate):
        """Handle real-time kline updates from WebSocket."""
        # Only process closed candles for signal generation
        if update.is_closed:
            # Update trade management with latest price
            open_trades = self.risk_manager.get_open_trades()
            if update.symbol in open_trades:
                self.risk_manager.update_trade_price(update.symbol, update.close)

    # ──────────────────────────────────────────
    # Notifications
    # ──────────────────────────────────────────

    async def _notify_signal(self, setup, position):
        """Send signal notification via Telegram."""
        if not self.telegram:
            return
        try:
            msg = (
                f"📊 *CRYPTO SIGNAL*\n"
                f"{'🟢' if setup.signal.value == 'BUY' else '🔴'} "
                f"*{setup.signal.value} {setup.symbol}*\n\n"
                f"Entry: `${setup.entry:,.2f}`\n"
                f"SL: `${setup.stop_loss:,.2f}`\n"
                f"TP: `${setup.take_profit:,.2f}`\n"
                f"R:R: `{setup.rr_ratio}`\n"
                f"Confidence: `{setup.confidence*100:.0f}%`\n"
                f"Size: `{position.amount}` (${position.value_usdt:,.2f})\n"
                f"Risk: `${position.risk_usdt:,.2f}` ({position.risk_pct:.1f}%)"
            )
            await self.telegram.send_message(msg)
        except Exception:
            pass

    async def _notify_trade(self, setup, position, order_result):
        """Send trade execution notification."""
        if not self.telegram:
            return
        try:
            msg = (
                f"✅ *TRADE EXECUTED*\n"
                f"{'🟢' if setup.signal.value == 'BUY' else '🔴'} "
                f"*{setup.signal.value} {setup.symbol}*\n\n"
                f"Filled: `{order_result.filled}` @ `${order_result.average_price:,.2f}`\n"
                f"SL: `${setup.stop_loss:,.2f}`\n"
                f"TP: `${setup.take_profit:,.2f}`\n"
                f"Cost: `${order_result.cost:,.2f}`"
            )
            await self.telegram.send_message(msg)
        except Exception:
            pass

    async def _notify_close(self, symbol, pnl, reason):
        """Send trade close notification."""
        if not self.telegram:
            return
        try:
            emoji = "💰" if pnl > 0 else "💸"
            msg = (
                f"{emoji} *TRADE CLOSED*\n"
                f"*{symbol}* — {reason}\n"
                f"P&L: `${pnl:+,.2f}`\n"
                f"Balance: `${self.risk_manager.account.balance:,.2f}`"
            )
            await self.telegram.send_message(msg)
        except Exception:
            pass

    async def _notify_error(self, error_msg):
        """Send error notification."""
        if not self.telegram:
            return
        try:
            await self.telegram.send_message(f"⚠️ *ERROR*: {error_msg}")
        except Exception:
            pass

    # ──────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────

    def _get_timeframe_enum(self) -> CryptoTimeframe:
        """Convert config timeframe string to enum."""
        tf_map = {
            "1m": CryptoTimeframe.M1, "3m": CryptoTimeframe.M3,
            "5m": CryptoTimeframe.M5, "15m": CryptoTimeframe.M15,
            "30m": CryptoTimeframe.M30, "1h": CryptoTimeframe.H1,
            "2h": CryptoTimeframe.H2, "4h": CryptoTimeframe.H4,
            "6h": CryptoTimeframe.H6, "8h": CryptoTimeframe.H8,
            "12h": CryptoTimeframe.H12, "1d": CryptoTimeframe.D1,
            "1w": CryptoTimeframe.W1,
        }
        return tf_map.get(self.config.timeframe, CryptoTimeframe.H1)

    def _print_summary(self):
        """Print session summary on shutdown."""
        status = self.risk_manager.get_status()
        log.info(f"\n{'='*60}")
        log.info(f"  SESSION SUMMARY")
        log.info(f"{'='*60}")
        log.info(f"  Cycles:        {self._cycle_count}")
        log.info(f"  Total Trades:  {status['total_trades']}")
        log.info(f"  Win Rate:      {status['win_rate']:.1f}%")
        log.info(f"  Total P&L:     ${status['total_pnl']:+,.2f}")
        log.info(f"  Final Balance: ${status['balance']:,.2f}")
        log.info(f"  Max Drawdown:  {status['drawdown_pct']:.1f}%")
        log.info(f"  Signals Found: {len(self._signal_history)}")
        log.info(f"{'='*60}\n")

    def get_status(self) -> Dict:
        """Get comprehensive bot status."""
        return {
            "running": self._is_running,
            "mode": self.config.mode.value,
            "watchlist": len(self._watchlist),
            "cycle_count": self._cycle_count,
            "risk": self.risk_manager.get_status() if self.risk_manager else {},
            "connector": self.connector.get_stats() if self.connector else {},
            "data_feed": self.data_feed.get_status() if self.data_feed else {},
        }


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

async def main():
    """Main entry point."""
    config = CryptoConfig.from_env()
    bot = ChastiefollCrypto(config)
    await bot.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
