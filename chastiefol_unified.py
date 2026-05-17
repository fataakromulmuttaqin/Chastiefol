"""
Chastiefol — Unified Orchestrator (Single Process)
Runs ALL trading systems concurrently in one async event loop:

  1. XAUUSD Agent (chastiefol_main.py logic)
     - Webhook listener on port 8080
     - Autonomous scanning with session filter (London/NY)
     - FIX execution via shared connector

  2. Multi-Pair Runner (chastiefol_multi_main.py logic)
     - XAUUSD + BTCUSD via shared FIX Trade connection
     - Portfolio correlation guard
     - MultiPairRunner orchestrator

  3. Crypto Scanner (chastiefol_crypto_main.py logic)
     - Binance spot pairs (24/7, no session filter)
     - CCXT data feed + WebSocket streaming
     - Independent from FIX (uses Binance API)

Shared Resources (no port conflicts):
  - 1x TelegramNotifier instance (shared across all modules)
  - 1x FIX PRICE connection (port 5211) — shared between XAUUSD + Multi
  - 1x FIX TRADE connection (port 5212) — shared (cTrader allows only 1)
  - 1x Webhook listener (port 8080) — XAUUSD only
  - Crypto uses Binance API independently (no FIX)

Usage:
    python chastiefol_unified.py

    # Or with specific modules disabled:
    UNIFIED_ENABLE_XAUUSD=true
    UNIFIED_ENABLE_MULTI=false
    UNIFIED_ENABLE_CRYPTO=true
    python chastiefol_unified.py

Environment Variables (new):
    UNIFIED_ENABLE_XAUUSD=true   # Enable XAUUSD agent
    UNIFIED_ENABLE_MULTI=true    # Enable Multi-pair runner
    UNIFIED_ENABLE_CRYPTO=true   # Enable Crypto scanner
"""

import asyncio
import logging
import os
import sys
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "Session Filter" / "Agent"))

from dotenv import load_dotenv
load_dotenv()

log_handler = logging.FileHandler("/tmp/chastiefol_unified.log")
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(log_handler)

log = logging.getLogger("Chastiefol.Unified")


# ──────────────────────────────────────────────
# Shared Resources Manager
# ──────────────────────────────────────────────

class SharedResources:
    """
    Manages shared resources across all modules to prevent port conflicts.
    
    Shared:
      - TelegramNotifier (1 instance, 1 bot token)
      - CTrader FIX connector (1 price + 1 trade session)
    """

    def __init__(self):
        self.telegram = None
        self.fix_connector = None
        self._initialized = False

    async def initialize(self):
        """Initialize all shared resources once."""
        if self._initialized:
            return

        # 1. Shared Telegram Notifier
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if tg_token and tg_chat:
            from Notification.telegram_notifier import TelegramNotifier, TelegramConfig
            tg_config = TelegramConfig(bot_token=tg_token, chat_id=tg_chat)
            self.telegram = TelegramNotifier(tg_config)
            await self.telegram.start()
            log.info("  [Shared] Telegram notifier started")
        else:
            log.warning("  [Shared] Telegram not configured")

        # 2. Shared FIX Connector (cTrader only allows 1 Trade session)
        fix_password = os.getenv("FIX_PASSWORD", "")
        paper_mode = os.getenv("PAPER_MODE", "true").lower() == "true"
        if fix_password and not paper_mode:
            from Connector.ctrader_fix import CTraderFIXConnector, FIXConfig
            fix_config = FIXConfig(
                price_host=os.getenv("FIX_PRICE_HOST", "demo-uk-eqx-01.p.c-trader.com"),
                price_port=int(os.getenv("FIX_PRICE_PORT", "5211")),
                trade_host=os.getenv("FIX_TRADE_HOST", "demo-uk-eqx-01.p.c-trader.com"),
                trade_port=int(os.getenv("FIX_TRADE_PORT", "5212")),
                sender_comp_id=os.getenv("FIX_SENDER_COMP_ID", "demo.ctrader.5820056"),
                target_comp_id=os.getenv("FIX_TARGET_COMP_ID", "cServer"),
                password=fix_password,
            )
            self.fix_connector = CTraderFIXConnector(fix_config)
            connected = self.fix_connector.connect()
            if connected:
                log.info("  [Shared] FIX PRICE connected (port 5211)")
                log.info("  [Shared] FIX TRADE connected (port 5212)")
                # Subscribe symbols
                self.fix_connector.subscribe("XAUUSD")
                self.fix_connector.subscribe("BTCUSD")
                log.info("  [Shared] Subscribed: XAUUSD, BTCUSD")
            else:
                log.error("  [Shared] FIX connection FAILED — falling back to paper mode")
                self.fix_connector = None
        else:
            log.info("  [Shared] FIX not configured (paper mode or no password)")

        self._initialized = True

    async def shutdown(self):
        """Gracefully close all shared resources."""
        if self.fix_connector:
            try:
                self.fix_connector.disconnect()
            except Exception as e:
                log.warning(f"FIX disconnect error: {e}")

        if self.telegram:
            try:
                await self.telegram.stop()
            except Exception as e:
                log.warning(f"Telegram stop error: {e}")

        self._initialized = False



# ──────────────────────────────────────────────
# Module Wrappers (adapt existing classes to use shared resources)
# ──────────────────────────────────────────────

class XAUUSDModule:
    """
    Wraps ChastiefollIntegrated (chastiefol_main.py) to use shared resources.
    Handles: Webhook (port 8080) + Autonomous XAUUSD scanning with session filter.
    """

    def __init__(self, shared: SharedResources):
        self.shared = shared
        self.agent = None
        self._enabled = os.getenv("UNIFIED_ENABLE_XAUUSD", "true").lower() == "true"

    async def start(self):
        if not self._enabled:
            log.info("  [XAUUSD] Module DISABLED")
            return

        from chastiefol_main import ChastiefollIntegrated, IntegratedConfig
        config = IntegratedConfig.from_env()
        self.agent = ChastiefollIntegrated(config)

        # Inject shared resources (prevent duplicate connections)
        self.agent.telegram = self.shared.telegram
        self.agent.ctrader_fix = self.shared.fix_connector

        # Start only non-shared components
        log.info("  [XAUUSD] Starting module...")

        # Data Feed (TradingView WS)
        from DataFeed.data_feed import DataFeedManager, DataFeedConfig
        feed_config = DataFeedConfig(
            symbol="OANDA:XAUUSD" if config.symbol == "XAUUSD" else f"BINANCE:{config.symbol}",
        )
        self.agent.data_feed = DataFeedManager(feed_config)
        await self.agent.data_feed.initialize()
        log.info("  [XAUUSD] Data feed initialized (TradingView WS)")

        # cTrader MCP (if configured and not using FIX)
        if not config.paper_mode and config.execution_method == "mcp" and config.ctrader_access_token:
            from Connector.ctrader_mcp import CTraderMCPConnector, MCPConfig
            mcp_config = MCPConfig(
                url=config.ctrader_mcp_url,
                access_token=config.ctrader_access_token,
                account_id=config.ctrader_account_id,
            )
            self.agent.ctrader_mcp = CTraderMCPConnector(mcp_config)
            connected = await self.agent.ctrader_mcp.connect()
            if connected:
                log.info("  [XAUUSD] cTrader MCP connected")
                await self.agent._sync_account_from_broker()
            else:
                log.error("  [XAUUSD] cTrader MCP connection failed")

        # FIX is already shared — just mark it
        if self.shared.fix_connector and config.execution_method == "fix":
            log.info("  [XAUUSD] Using shared FIX connector")
            if not config.paper_mode:
                await self.agent._sync_account_from_broker()

        # Webhook Listener (unique port — no conflict)
        from chastiefol_main import AgentMode
        if config.mode in (AgentMode.WEBHOOK, AgentMode.HYBRID):
            from Webhook.webhook_listener import WebhookListener, WebhookConfig
            wh_config = WebhookConfig(
                port=config.webhook_port,
                secret_key=config.webhook_secret,
                enable_hmac_auth=config.webhook_hmac_enabled,
            )
            self.agent.webhook = WebhookListener(
                config=wh_config,
                on_signal=self.agent._on_webhook_signal,
            )
            await self.agent.webhook.start()
            log.info(f"  [XAUUSD] Webhook listener on port {config.webhook_port}")

        # Background tasks
        self.agent._is_running = True
        if config.mode in (AgentMode.AUTONOMOUS, AgentMode.HYBRID):
            self.agent._scan_task = asyncio.create_task(self.agent._autonomous_loop())
            log.info(f"  [XAUUSD] Autonomous scanner started ({config.scan_interval_sec}s)")

        if self.agent.webhook:
            self.agent._webhook_consumer_task = asyncio.create_task(
                self.agent._webhook_consumer_loop()
            )

        log.info("  [XAUUSD] Module ONLINE")

    async def stop(self):
        if not self._enabled or not self.agent:
            return
        log.info("  [XAUUSD] Stopping...")
        self.agent._is_running = False
        if self.agent._scan_task:
            self.agent._scan_task.cancel()
        if self.agent._webhook_consumer_task:
            self.agent._webhook_consumer_task.cancel()
        if self.agent.webhook:
            await self.agent.webhook.stop()
        if self.agent.ctrader_mcp:
            await self.agent.ctrader_mcp.disconnect()
        if self.agent.data_feed:
            await self.agent.data_feed.shutdown()
        # Don't close shared resources (telegram, fix)
        self.agent.telegram = None
        self.agent.ctrader_fix = None
        log.info("  [XAUUSD] Stopped")



class MultiPairModule:
    """
    Wraps ChastiefollMultiMain (chastiefol_multi_main.py) to use shared resources.
    Handles: XAUUSD + BTCUSD portfolio trading with correlation guard.
    Uses shared FIX connector — no duplicate connections.
    """

    def __init__(self, shared: SharedResources):
        self.shared = shared
        self.agent = None
        self._enabled = os.getenv("UNIFIED_ENABLE_MULTI", "true").lower() == "true"
        self._scan_task: Optional[asyncio.Task] = None

    async def start(self):
        if not self._enabled:
            log.info("  [Multi] Module DISABLED")
            return

        from chastiefol_multi_main import ChastiefollMultiMain, MultiPairConfig
        self.agent = ChastiefollMultiMain()

        # Inject shared resources
        self.agent.telegram = self.shared.telegram
        self.agent.fix_connector = self.shared.fix_connector

        log.info("  [Multi] Starting module...")

        # Data Feeds for each pair (TradingView WS — no port conflict)
        from DataFeed.data_feed import DataFeedManager, DataFeedConfig, Timeframe
        for symbol in self.agent.config.pairs:
            if symbol == "XAUUSD":
                feed_symbol = "OANDA:XAUUSD"
                binance_symbol = "PAXG/USDT"
            elif symbol == "BTCUSD":
                feed_symbol = "BINANCE:BTCUSDT"
                binance_symbol = "BTC/USDT"
            elif symbol == "PAXGUSDT":
                feed_symbol = "BINANCE:PAXGUSDT"
                binance_symbol = "PAXG/USDT"
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
            self.agent.data_feeds[symbol] = feed
            log.info(f"  [Multi] DataFeed: {feed_symbol} (fallback: {binance_symbol})")

        # MultiPairRunner (portfolio orchestrator)
        from Portfolio.multi_pair_runner import create_default_runner
        self.agent.runner = create_default_runner(
            initial_balance=self.agent.config.initial_balance,
            risk_pct=self.agent.config.risk_pct,
            on_signal=self.agent._on_signal,
        )
        log.info(f"  [Multi] MultiPairRunner initialized (${self.agent.config.initial_balance:,.0f})")

        # Load historical data
        await self.agent._load_historical_data()

        # Start scan loop
        self.agent._running = True
        self._scan_task = asyncio.create_task(self._run_loop())
        log.info(f"  [Multi] Scan loop started ({self.agent.config.scan_interval_sec}s)")
        log.info("  [Multi] Module ONLINE (XAUUSD + BTCUSD)")

    async def _run_loop(self):
        """Run multi-pair scanning loop."""
        while self.agent._running:
            try:
                await self.agent._scan_cycle()
                await asyncio.sleep(self.agent.config.scan_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[Multi] Scan error: {e}")
                await asyncio.sleep(self.agent.config.scan_interval_sec)

    async def stop(self):
        if not self._enabled or not self.agent:
            return
        log.info("  [Multi] Stopping...")
        self.agent._running = False
        if self._scan_task:
            self._scan_task.cancel()
        for feed in self.agent.data_feeds.values():
            await feed.shutdown()
        # Don't close shared resources
        self.agent.telegram = None
        self.agent.fix_connector = None
        log.info("  [Multi] Stopped")



class CryptoModule:
    """
    Wraps ChastiefollCrypto (chastiefol_crypto_main.py) to use shared Telegram.
    Handles: Binance spot pair scanning (24/7, independent from FIX).
    Uses CCXT + Binance WebSocket — no port conflict with FIX.
    """

    def __init__(self, shared: SharedResources):
        self.shared = shared
        self.agent = None
        self._enabled = os.getenv("UNIFIED_ENABLE_CRYPTO", "true").lower() == "true"

    async def start(self):
        if not self._enabled:
            log.info("  [Crypto] Module DISABLED")
            return

        from chastiefol_crypto_main import ChastiefollCrypto, CryptoConfig
        config = CryptoConfig.from_env()
        self.agent = ChastiefollCrypto(config)

        log.info("  [Crypto] Starting module...")

        # Initialize Data Feed (CCXT/Binance)
        from CryptoDataFeed.crypto_data_feed import CryptoDataFeedManager, CryptoDataFeedConfig
        feed_config = CryptoDataFeedConfig(
            binance_api_key=config.effective_api_key,
            binance_secret=config.effective_secret,
            sandbox_mode=config.binance_sandbox or config.binance_demo_mode == "testnet",
            coingecko_api_key=config.coingecko_api_key,
            coinmarketcap_api_key=config.coinmarketcap_api_key,
            coinstats_api_key=config.coinstats_api_key,
            enable_websocket=config.enable_websocket,
        )
        self.agent.data_feed = CryptoDataFeedManager(feed_config)
        await self.agent.data_feed.initialize()
        log.info("  [Crypto] Data feed initialized (CCXT/Binance)")

        # Initialize Binance Connector
        from Connector.binance_connector import BinanceConnector, BinanceConfig as BinConnConfig
        from chastiefol_crypto_main import CryptoMode
        is_paper = config.mode in (CryptoMode.PAPER, CryptoMode.SCANNER)
        demo_mode = config.binance_demo_mode

        connector_config = BinConnConfig(
            api_key=config.effective_api_key,
            secret=config.effective_secret,
            sandbox=config.binance_sandbox,
            demo_mode=demo_mode,
            default_type=config.binance_default_type,
            paper_mode=is_paper and demo_mode == "paper",
        )
        self.agent.connector = BinanceConnector(connector_config)
        await self.agent.connector.connect()
        log.info(f"  [Crypto] Binance connector ({config.binance_demo_mode.upper()})")

        # Initialize Risk Manager
        from Risk.crypto_risk_manager import CryptoRiskManager, CryptoRiskConfig
        risk_config = CryptoRiskConfig(
            initial_balance=config.initial_balance,
            risk_pct_per_trade=config.risk_pct_per_trade,
            max_open_trades=config.max_open_trades,
            max_daily_loss_pct=config.max_daily_loss_pct,
            max_drawdown_pct=config.max_drawdown_pct,
        )
        self.agent.risk_manager = CryptoRiskManager(risk_config)
        log.info("  [Crypto] Risk manager initialized")

        # Initialize Scanner
        from Analysis.crypto_engine import CryptoScanner
        self.agent.scanner = CryptoScanner(min_confidence=config.min_confidence)
        log.info("  [Crypto] Multi-pair scanner initialized")

        # Inject shared Telegram (don't create a new one)
        self.agent.telegram = self.shared.telegram
        log.info("  [Crypto] Using shared Telegram notifier")

        # Start scan loop
        self.agent._is_running = True
        self.agent._scan_task = asyncio.create_task(self.agent._scan_loop())
        log.info(f"  [Crypto] Scan loop started ({config.scan_interval_sec}s interval)")

        # Start WebSocket streaming
        if config.enable_websocket and self.agent.data_feed.websocket:
            watchlist = self.agent._watchlist[:50]
            await self.agent.data_feed.start_streaming(
                symbols=watchlist,
                interval=config.timeframe,
                on_kline=self.agent._on_kline_update,
            )
            log.info(f"  [Crypto] WebSocket streaming for {len(watchlist)} pairs")

        log.info(f"  [Crypto] Module ONLINE ({len(self.agent._watchlist)} pairs, 24/7)")

    async def stop(self):
        if not self._enabled or not self.agent:
            return
        log.info("  [Crypto] Stopping...")
        self.agent._is_running = False
        if self.agent._scan_task:
            self.agent._scan_task.cancel()
            try:
                await self.agent._scan_task
            except asyncio.CancelledError:
                pass
        if self.agent.data_feed:
            await self.agent.data_feed.shutdown()
        if self.agent.connector:
            await self.agent.connector.disconnect()
        # Don't close shared Telegram
        self.agent.telegram = None
        log.info("  [Crypto] Stopped")



# ──────────────────────────────────────────────
# Unified Orchestrator
# ──────────────────────────────────────────────

class ChastiefollUnified:
    """
    Master orchestrator that runs all Chastiefol modules in a single process.
    
    Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │              ChastiefollUnified (1 process, 1 event loop)            │
    ├─────────────────────────────────────────────────────────────────────┤
    │                                                                      │
    │  ┌───────────────────────────────────────────────────────────┐      │
    │  │  SharedResources                                           │      │
    │  │  - 1x TelegramNotifier (shared)                            │      │
    │  │  - 1x FIX connector: Price 5211 + Trade 5212 (shared)      │      │
    │  └───────────────────────────────────────────────────────────┘      │
    │                                                                      │
    │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐        │
    │  │ XAUUSDModule │  │ MultiModule  │  │   CryptoModule     │        │
    │  │ - Webhook    │  │ - XAUUSD     │  │   - Binance CCXT   │        │
    │  │   port 8080  │  │ - BTCUSD     │  │   - 24/7 scanner   │        │
    │  │ - Session    │  │ - Correlation│  │   - WebSocket       │        │
    │  │   filter     │  │ - Portfolio  │  │   - Top20 pairs     │        │
    │  │ - LLM review │  │              │  │   - LLM review      │        │
    │  └──────────────┘  └──────────────┘  └────────────────────┘        │
    │                                                                      │
    └─────────────────────────────────────────────────────────────────────┘
    """

    def __init__(self):
        self.shared = SharedResources()
        self.xauusd = XAUUSDModule(self.shared)
        self.multi = MultiPairModule(self.shared)
        self.crypto = CryptoModule(self.shared)
        self._running = False

    async def start(self):
        """Initialize shared resources, then start all enabled modules."""
        log.info("")
        log.info("╔══════════════════════════════════════════════════════════╗")
        log.info("║          CHASTIEFOL UNIFIED TRADING SYSTEM              ║")
        log.info("║                                                          ║")
        log.info("║  XAUUSD Agent + Multi-Pair + Crypto Scanner             ║")
        log.info("║  Single Process | Shared Resources | No Port Conflicts   ║")
        log.info("╚══════════════════════════════════════════════════════════╝")
        log.info("")

        # Show module status
        log.info("┌─ MODULE STATUS ─────────────────────────────────────────┐")
        log.info(f"│  XAUUSD Agent:    {'ENABLED' if self.xauusd._enabled else 'DISABLED':>10} │")
        log.info(f"│  Multi-Pair:      {'ENABLED' if self.multi._enabled else 'DISABLED':>10} │")
        log.info(f"│  Crypto Scanner:  {'ENABLED' if self.crypto._enabled else 'DISABLED':>10} │")
        log.info("└─────────────────────────────────────────────────────────┘")
        log.info("")

        # 1. Initialize shared resources FIRST (Telegram + FIX)
        log.info("── Initializing Shared Resources ──")
        await self.shared.initialize()
        log.info("")

        # 2. Start modules (order: XAUUSD → Multi → Crypto)
        log.info("── Starting Modules ──")

        try:
            await self.xauusd.start()
        except Exception as e:
            log.error(f"  [XAUUSD] Failed to start: {e}")
            if self.shared.telegram:
                await self.shared.telegram.send_message(
                    f"⚠️ *XAUUSD module failed to start*\n`{str(e)[:200]}`"
                )

        try:
            await self.multi.start()
        except Exception as e:
            log.error(f"  [Multi] Failed to start: {e}")
            if self.shared.telegram:
                await self.shared.telegram.send_message(
                    f"⚠️ *Multi-pair module failed to start*\n`{str(e)[:200]}`"
                )

        try:
            await self.crypto.start()
        except Exception as e:
            log.error(f"  [Crypto] Failed to start: {e}")
            if self.shared.telegram:
                await self.shared.telegram.send_message(
                    f"⚠️ *Crypto module failed to start*\n`{str(e)[:200]}`"
                )

        # 3. Send startup notification
        self._running = True
        if self.shared.telegram:
            modules = []
            if self.xauusd._enabled:
                modules.append("XAUUSD")
            if self.multi._enabled:
                modules.append("Multi (XAU+BTC)")
            if self.crypto._enabled:
                modules.append("Crypto (Binance)")
            await self.shared.telegram.send_message(
                f"🚀 *CHASTIEFOL UNIFIED — ONLINE*\n\n"
                f"Active Modules:\n" +
                "\n".join(f"  ✅ {m}" for m in modules) +
                f"\n\nTimestamp: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`"
            )

        log.info("")
        log.info("═══════════════════════════════════════════════════════════")
        log.info("  ALL MODULES ONLINE — Chastiefol Unified is active")
        log.info("═══════════════════════════════════════════════════════════")
        log.info("")

    async def stop(self):
        """Gracefully stop all modules, then shared resources."""
        log.info("")
        log.info("── Shutting Down Chastiefol Unified ──")
        self._running = False

        # Stop modules (reverse order)
        await self.crypto.stop()
        await self.multi.stop()
        await self.xauusd.stop()

        # Send shutdown notification before closing Telegram
        if self.shared.telegram:
            try:
                await self.shared.telegram.send_message(
                    "🔴 *CHASTIEFOL UNIFIED — OFFLINE*\n"
                    f"Shutdown: `{datetime.now(timezone.utc).strftime('%H:%M UTC')}`"
                )
                await asyncio.sleep(1)  # Let message send
            except Exception:
                pass

        # Close shared resources last
        await self.shared.shutdown()
        log.info("── Shutdown Complete ──")

    async def run_forever(self):
        """Main entry — start and run until interrupted."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    def get_status(self) -> dict:
        """Get status of all modules."""
        status = {
            "running": self._running,
            "modules": {
                "xauusd": {
                    "enabled": self.xauusd._enabled,
                    "running": bool(self.xauusd.agent and self.xauusd.agent._is_running)
                    if self.xauusd.agent else False,
                },
                "multi": {
                    "enabled": self.multi._enabled,
                    "running": bool(self.multi.agent and self.multi.agent._running)
                    if self.multi.agent else False,
                },
                "crypto": {
                    "enabled": self.crypto._enabled,
                    "running": bool(self.crypto.agent and self.crypto.agent._is_running)
                    if self.crypto.agent else False,
                },
            },
            "shared": {
                "telegram": self.shared.telegram is not None,
                "fix": self.shared.fix_connector is not None,
            },
        }
        return status


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

async def main():
    """Main entry point for unified orchestrator."""
    orchestrator = ChastiefollUnified()

    # Handle graceful shutdown on signals
    loop = asyncio.get_event_loop()

    def _signal_handler():
        log.info("Received shutdown signal...")
        orchestrator._running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            pass  # Windows doesn't support add_signal_handler

    await orchestrator.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as e:
        log.error(f"Fatal error: {e}")
        raise
