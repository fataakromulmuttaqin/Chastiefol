"""
Chastiefol — Integrated Main Orchestrator
Ties together ALL components:
  - Webhook Listener (receive TradingView signals)
  - Data Feed (TwelveData / AlphaVantage / GoldAPI)
  - Analysis Engine (SMC + Technical Indicators)
  - Risk Manager (Position sizing, Drawdown guard)
  - cTrader Connector (MCP + FIX API execution)
  - Telegram Notifier (Real-time alerts)

Modes:
  - WEBHOOK: Listen for external signals, validate, execute
  - AUTONOMOUS: Self-analyze market, generate signals, execute
  - HYBRID: Both webhook + autonomous scanning
"""

import json
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
sys.path.insert(0, str(PROJECT_ROOT / "Session Filter" / "Agent"))

from dotenv import load_dotenv
load_dotenv()

# Internal modules
from Webhook.webhook_listener import WebhookListener, WebhookConfig, WebhookSignal, TradeAction
from Connector.ctrader_mcp import CTraderMCPConnector, MCPConfig, OrderSide, OrderResult
from Connector.ctrader_fix import CTraderFIXConnector, FIXConfig
from DataFeed.data_feed import DataFeedManager, DataFeedConfig, Timeframe, PriceQuote
from Notification.telegram_notifier import TelegramNotifier, TelegramConfig
from Analysis.xauusd_engine import ChastiefollAgent, Signal, TradeSetup
from Risk.risk_manager import (
    AccountState, DrawdownGuard, PositionSizer, GoldSessionFilter,
    TradeManager, TradeLifecycle, RiskModel
)
from LLM.llm_agent import LLMInsightAgent, LLMConfig, MarketInsight
from LLM.lessons import LessonsManager
from LLM.trading_memory import TradingMemory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Chastiefol.Main")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class AgentMode(str, Enum):
    WEBHOOK = "webhook"         # Only respond to TradingView signals
    AUTONOMOUS = "autonomous"   # Self-scan and execute
    HYBRID = "hybrid"           # Both modes simultaneously


@dataclass
class IntegratedConfig:
    """Master configuration — loads from environment variables."""
    # Agent Mode
    mode: AgentMode = AgentMode.HYBRID
    symbol: str = "XAUUSD"
    instance_id: int = 0          # 0=XAUUSD (port 8080), 1=BTCUSD (port 8081)
    paper_mode: bool = True

    # Account
    initial_balance: float = 10_000.0
    currency: str = "USD"

    # Strategy
    timeframe: str = "H1"
    lookback_bars: int = 100
    risk_pct: float = 1.0
    max_risk_pct: float = 2.0
    atr_sl_mult: float = 1.5
    rr_target: float = 2.0
    min_confidence: float = 0.55
    min_llm_confidence: float = 0.65  # Minimum LLM AI confidence to approve a trade

    # Risk
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 10.0
    max_open_trades: int = 2

    # Timing
    scan_interval_sec: int = 60

    # Webhook
    webhook_port: int = 8080
    webhook_secret: str = ""
    webhook_hmac_enabled: bool = True

    # cTrader MCP
    ctrader_mcp_url: str = "https://mcp.ctrader.com/trading/mcp"
    ctrader_access_token: str = ""
    ctrader_account_id: str = ""

    # cTrader FIX
    fix_price_host: str = "demo-uk-eqx-01.p.c-trader.com"
    fix_price_port: int = 5211
    fix_trade_host: str = "demo-uk-eqx-01.p.c-trader.com"
    fix_trade_port: int = 5212
    fix_sender_comp_id: str = "demo.ctrader.5820056"
    fix_target_comp_id: str = "cServer"
    fix_password: str = ""

    # Data Feed (TradingView WS — no API key needed)
    # TwelveData, AlphaVantage, GoldAPI REMOVED — they had no historical candles
    # TradingView WebSocket provides FREE real-time + historical OHLCV

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Execution preference
    execution_method: str = "mcp"  # "mcp" | "fix" | "paper"

    @classmethod
    def from_env(cls) -> "IntegratedConfig":
        """Load configuration from environment variables."""
        return cls(
            mode=AgentMode(os.getenv("AGENT_MODE", "hybrid")),
            symbol=os.getenv("SYMBOL", "XAUUSD"),
            instance_id=int(os.getenv("INSTANCE_ID", "0")),
            paper_mode=os.getenv("PAPER_MODE", "true").lower() == "true",
            initial_balance=float(os.getenv("INITIAL_BALANCE", "10000")),
            risk_pct=float(os.getenv("RISK_PCT", "1.0")),
            max_risk_pct=float(os.getenv("MAX_RISK_PCT", "2.0")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.55")),
            min_llm_confidence=float(os.getenv("MIN_LLM_CONFIDENCE", "0.65")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0")),
            max_drawdown_pct=float(os.getenv("MAX_DRAWDOWN_PCT", "10.0")),
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "2")),
            scan_interval_sec=int(os.getenv("SCAN_INTERVAL_SEC", "60")),
            webhook_port=int(os.getenv("WEBHOOK_PORT", str(8080 + int(os.getenv("INSTANCE_ID", "0"))))),
            webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
            webhook_hmac_enabled=os.getenv("WEBHOOK_HMAC_ENABLED", "true").lower() == "true",
            ctrader_mcp_url=os.getenv("CTRADER_MCP_URL", "https://mcp.ctrader.com/trading/mcp"),
            ctrader_access_token=os.getenv("CTRADER_ACCESS_TOKEN", ""),
            ctrader_account_id=os.getenv("CTRADER_ACCOUNT_ID", ""),
            fix_price_host=os.getenv("FIX_PRICE_HOST", "demo-uk-eqx-01.p.c-trader.com"),
            fix_price_port=int(os.getenv("FIX_PRICE_PORT", "5211")),
            fix_trade_host=os.getenv("FIX_TRADE_HOST", "demo-uk-eqx-01.p.c-trader.com"),
            fix_trade_port=int(os.getenv("FIX_TRADE_PORT", "5212")),
            fix_sender_comp_id=os.getenv("FIX_SENDER_COMP_ID", "demo.ctrader.5820056"),
            fix_target_comp_id=os.getenv("FIX_TARGET_COMP_ID", "cServer"),
            fix_password=os.getenv("FIX_PASSWORD", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            execution_method=os.getenv("EXECUTION_METHOD", "mcp"),
        )



# ──────────────────────────────────────────────
# Integrated Orchestrator
# ──────────────────────────────────────────────

class ChastiefollIntegrated:
    """
    Production-ready orchestrator integrating all Chastiefol components.

    Architecture:
        ┌─────────────────────────────────────────────────┐
        │              ChastiefollIntegrated               │
        ├─────────────────────────────────────────────────┤
        │  Webhook Listener ──┐                           │
        │                     ├─→ Signal Router           │
        │  Autonomous Scan ───┘       │                   │
        │                             ▼                   │
        │                     Risk Manager                │
        │                             │                   │
        │                             ▼                   │
        │                     cTrader Executor            │
        │                        (MCP / FIX)             │
        │                             │                   │
        │                             ▼                   │
        │                     Telegram Notifier           │
        └─────────────────────────────────────────────────┘
    """

    def __init__(self, config: IntegratedConfig = None):
        self.config = config or IntegratedConfig.from_env()
        self._is_running = False

        # ── Core Components ──
        self.analysis_engine = ChastiefollAgent()
        self.position_sizer = PositionSizer(
            risk_pct=self.config.risk_pct,
            max_risk_pct=self.config.max_risk_pct,
            model=RiskModel.FIXED_PERCENT,
        )
        self.drawdown_guard = DrawdownGuard(
            max_daily_loss_pct=self.config.max_daily_loss_pct,
            max_drawdown_pct=self.config.max_drawdown_pct,
            max_open_trades=self.config.max_open_trades,
        )
        self.session_filter = GoldSessionFilter()
        self._market_closed = False   # Set True when broker reports MARKET_CLOSED
        self._market_closed_at = None  # Timestamp when market closed was first detected
        self.trade_manager = TradeManager()

        # ── Account State ──
        self.account = AccountState(
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            peak_balance=self.config.initial_balance,
        )

        # ── Integration Components (initialized in start()) ──
        self.webhook: Optional[WebhookListener] = None
        self.ctrader_mcp: Optional[CTraderMCPConnector] = None
        self.ctrader_fix: Optional[CTraderFIXConnector] = None
        self.data_feed: Optional[DataFeedManager] = None
        self.telegram: Optional[TelegramNotifier] = None

        # ── State ──
        self.open_trades: List[TradeLifecycle] = []
        self.trade_position_ids: Dict[int, str] = {}  # trade id(obj) → broker position_id
        self.trade_log: List[dict] = []
        self.signal_count: int = 0
        self._scan_task: Optional[asyncio.Task] = None
        self._webhook_consumer_task: Optional[asyncio.Task] = None

        # ── Deduplication: prevent same signal from executing multiple times ──
        # Tracks last signal hash (action+entry+sl+tp) and timestamp
        self._last_signal_hash: Optional[int] = None
        self._last_signal_time: float = 0.0  # monotonic clock of last execution
        self._dedupe_window_sec: float = 120.0  # ignore duplicates within 2 minutes

        # ── LLM Review Agent (validates signals before execution) ──
        self.lessons = LessonsManager()
        self.trading_memory = TradingMemory(self.lessons)
        self.llm_agent: Optional[LLMInsightAgent] = None
        self._llm_enabled = bool(
            os.getenv("LLM_PROVIDER") or os.getenv("LLM_API_KEY") or
            os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY") or
            os.getenv("MINIMAX_API_KEY")
        )
        if self._llm_enabled:
            try:
                llm_config = LLMConfig.from_env()
                self.llm_agent = LLMInsightAgent(
                    config=llm_config,
                    lessons_manager=self.lessons,
                    trading_memory=self.trading_memory,
                )
            except Exception as e:
                log.warning(f"LLM agent init failed: {e} — signals will execute without LLM review")
                self._llm_enabled = False

        log.info(f"{'='*55}")
        log.info(f"  CHASTIEFOL INTEGRATED AGENT")
        log.info(f"  Mode: {self.config.mode.value.upper()}")
        log.info(f"  Symbol: {self.config.symbol}")
        log.info(f"  Instance: {self.config.instance_id} (webhook port {self.config.webhook_port})")
        log.info(f"  Execution: {self.config.execution_method.upper()}")
        log.info(f"  Paper: {self.config.paper_mode}")
        log.info(f"  LLM Review: {'ENABLED (' + self.llm_agent.config.provider.value + '/' + self.llm_agent.config.model + ')' if self._llm_enabled else 'DISABLED (no API key)'}")
        log.info(f"  Learning: ENABLED ({self.lessons.get_stats()['total_lessons']} lessons loaded)")
        log.info(f"{'='*55}")

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    async def start(self):
        """Initialize and start all components."""
        log.info("Starting Chastiefol Integrated Agent...")

        # 1. Initialize Telegram Notifier
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            tg_config = TelegramConfig(
                bot_token=self.config.telegram_bot_token,
                chat_id=self.config.telegram_chat_id,
            )
            self.telegram = TelegramNotifier(tg_config)
            await self.telegram.start()
            log.info("✓ Telegram notifier started")
        else:
            log.warning("⚠ Telegram not configured — notifications disabled")

        # 2. Initialize Data Feed (TradingView WS — FREE, no API key)
        feed_config = DataFeedConfig(
            symbol="OANDA:XAUUSD" if self.config.symbol == "XAUUSD" else f"BINANCE:{self.config.symbol}",
        )
        self.data_feed = DataFeedManager(feed_config)
        await self.data_feed.initialize()
        log.info("✓ Data feed initialized (TradingView WS — no API key needed)")

        # 3. Initialize cTrader Connector + Sync Account Balance
        if not self.config.paper_mode:
            if self.config.execution_method == "mcp" and self.config.ctrader_access_token:
                mcp_config = MCPConfig(
                    url=self.config.ctrader_mcp_url,
                    access_token=self.config.ctrader_access_token,
                    account_id=self.config.ctrader_account_id,
                )
                self.ctrader_mcp = CTraderMCPConnector(mcp_config)
                connected = await self.ctrader_mcp.connect()
                if connected:
                    log.info("✓ cTrader MCP connected")
                    # ── SYNC ACCOUNT FROM BROKER ──
                    await self._sync_account_from_broker()
                else:
                    log.error("✗ cTrader MCP connection failed")
                    await self._notify_error("cTrader MCP connection failed")

            elif self.config.execution_method == "fix" and self.config.fix_password:
                fix_config = FIXConfig(
                    price_host=self.config.fix_price_host,
                    price_port=self.config.fix_price_port,
                    trade_host=self.config.fix_trade_host,
                    trade_port=self.config.fix_trade_port,
                    sender_comp_id=self.config.fix_sender_comp_id,
                    target_comp_id=self.config.fix_target_comp_id,
                    password=self.config.fix_password,
                )
                self.ctrader_fix = CTraderFIXConnector(fix_config)
                connected = self.ctrader_fix.connect()
                if connected:
                    self.ctrader_fix.subscribe(self.config.symbol)
                    log.info("✓ cTrader FIX connected")
                    # ── SYNC ACCOUNT FROM BROKER ──
                    await self._sync_account_from_broker()
                else:
                    log.error("✗ cTrader FIX connection failed")
                    await self._notify_error("cTrader FIX connection failed")
        else:
            log.info("✓ Paper mode — using INITIAL_BALANCE from .env")

        # 4. Initialize Webhook Listener
        if self.config.mode in (AgentMode.WEBHOOK, AgentMode.HYBRID):
            wh_config = WebhookConfig(
                port=self.config.webhook_port,
                secret_key=self.config.webhook_secret,
                enable_hmac_auth=self.config.webhook_hmac_enabled,
            )
            self.webhook = WebhookListener(
                config=wh_config,
                on_signal=self._on_webhook_signal,
            )
            await self.webhook.start()
            log.info(f"✓ Webhook listener started on port {self.config.webhook_port}")

        # 5. Start background tasks
        self._is_running = True

        if self.config.mode in (AgentMode.AUTONOMOUS, AgentMode.HYBRID):
            self._scan_task = asyncio.create_task(self._autonomous_loop())
            log.info(f"✓ Autonomous scanner started (interval: {self.config.scan_interval_sec}s)")

        if self.webhook:
            self._webhook_consumer_task = asyncio.create_task(self._webhook_consumer_loop())

        log.info("\n" + "="*55)
        log.info("  ALL SYSTEMS ONLINE — Chastiefol is active")
        log.info("="*55 + "\n")

    async def stop(self):
        """Gracefully stop all components."""
        log.info("Shutting down Chastiefol...")
        self._is_running = False

        # Cancel background tasks
        if self._scan_task:
            self._scan_task.cancel()
        if self._webhook_consumer_task:
            self._webhook_consumer_task.cancel()

        # Stop components
        if self.webhook:
            await self.webhook.stop()
        if self.ctrader_mcp:
            await self.ctrader_mcp.disconnect()
        if self.ctrader_fix:
            self.ctrader_fix.disconnect()
        if self.data_feed:
            await self.data_feed.shutdown()
        if self.telegram:
            await self.telegram.stop()

        self._print_summary()
        log.info("Chastiefol shutdown complete.")

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
    # Webhook Signal Processing
    # ──────────────────────────────────────────

    async def _on_webhook_signal(self, signal: WebhookSignal):
        """Callback when a valid webhook signal is received."""
        log.info(f"📨 Webhook signal: {signal.action.value} {signal.symbol} "
                 f"vol={signal.volume}")

    async def _webhook_consumer_loop(self):
        """Process signals from the webhook queue."""
        while self._is_running:
            try:
                signal = await self.webhook.get_next_signal(timeout=1.0)
                if signal:
                    await self._process_webhook_signal(signal)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Webhook consumer error: {e}")
                await self._notify_error(str(e), "Webhook Consumer")

    async def _process_webhook_signal(self, signal: WebhookSignal):
        """Process a validated webhook signal through risk checks and execution."""
        log.info(f"Processing webhook signal: {signal.action.value}")

        # ── Early exits ──
        if signal.action == TradeAction.CLOSE:
            await self._close_all_positions()
            return

        # Determine side first (needed for deduplication hash)
        side = OrderSide.BUY if signal.action == TradeAction.BUY else OrderSide.SELL

        # Get current price
        current_price = await self._get_current_price()
        if current_price == 0:
            log.error("Cannot get current price — aborting signal")
            await self._notify_error("Failed to get price for signal execution")
            return

        # Calculate SL/TP from pips
        pip_value = 0.01  # XAUUSD pip = $0.01
        if side == OrderSide.BUY:
            stop_loss = current_price - (signal.sl_pips * pip_value)
            take_profit = current_price + (signal.tp_pips * pip_value)
        else:
            stop_loss = current_price + (signal.sl_pips * pip_value)
            take_profit = current_price - (signal.tp_pips * pip_value)

        # ── Risk check (needs account.open_trades) ──
        self.account.open_trades = len(self.open_trades)
        status = self.drawdown_guard.check(self.account)
        if not status["trading_allowed"]:
            log.warning(f"Trading blocked by risk guard: {status['reason']}")
            await self._notify_warning(f"Signal rejected — {status['reason']}")
            return

        # ── Deduplication: skip if we just executed the exact same signal ──
        # Round prices to nearest 0.50 before hashing to absorb floating-point noise
        # (e.g. TradingView sending 2365.47 vs 2365.50 for the same alert)
        import hashlib
        _round_dedup = lambda p: f"{round(p * 2) / 2:.1f}"
        sig_hash = hashlib.md5(
            f"{side.value}|{_round_dedup(current_price)}|{_round_dedup(stop_loss)}|{_round_dedup(take_profit)}".encode()
        ).hexdigest()[:12]
        now_monotonic = asyncio.get_event_loop().time()
        if sig_hash == self._last_signal_hash and (now_monotonic - self._last_signal_time) < self._dedupe_window_sec:
            log.info(f"    ⏳ Duplicate signal suppressed ({self._dedupe_window_sec}s window)")
            return
        self._last_signal_hash = sig_hash
        self._last_signal_time = now_monotonic

        # ── Session filter: reject signals outside trading hours ──
        now_utc = datetime.now(timezone.utc)
        session_active, session_name = self.session_filter.is_active(now_utc.hour, now_utc.minute)
        if not session_active:
            log.info(f"    ⏳ Webhook signal skipped — {session_name} (off-session)")
            await self._notify_warning(f"Signal blocked — off-session ({session_name})")
            return

        # Calculate position size from risk
        pos_spec = self.position_sizer.calculate(self.account, current_price, stop_loss)
        volume = round(pos_spec.lot_size * status["size_multiplier"], 2)
        volume = max(volume, signal.volume)  # Use at least the signal volume
        volume = max(volume, 1.0)  # Enforce minimum 1.0 lot for cTrader FIX demo server

        # ── LLM REVIEW: Ask AI to validate webhook signal before execution ──
        if self.llm_agent and self._llm_enabled:
            try:
                # Create a synthetic TradeSetup-like object for LLM review
                webhook_setup = type("WebhookSetup", (), {
                    "signal": type("Sig", (), {"value": side.value})(),
                    "entry": current_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.70,  # Webhook signals get default confidence
                    "rr_ratio": round(abs(take_profit - current_price) / abs(current_price - stop_loss), 2) if abs(current_price - stop_loss) > 0 else 0,
                    "reasons": [f"Webhook signal: {signal.comment or 'TradingView'}"],
                })()
                insight = await self._llm_review_signal(webhook_setup, volume)
                if not insight or insight.trade_recommendation.upper() == "HOLD":
                    reason = insight.summary[:200] if insight else "LLM review unavailable"
                    log.info(f"    ✗ LLM rejected webhook signal — skipping execution")
                    await self._notify_warning(f"LLM rejected webhook {side.value} signal: {reason}")
                    return
            except Exception as e:
                log.warning(f"    ⚠ LLM review failed for webhook ({e}) — proceeding without review")

        # Execute
        await self._execute_order(
            side=side,
            volume=volume,
            entry=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=signal.comment,
        )

    # ──────────────────────────────────────────
    # Autonomous Scanning
    # ──────────────────────────────────────────

    async def _autonomous_loop(self):
        """Autonomous market scanning loop."""
        while self._is_running:
            try:
                await self._autonomous_cycle()
                await asyncio.sleep(self.config.scan_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Autonomous cycle error: {e}")
                await self._notify_error(str(e), "Autonomous Scanner")
                await asyncio.sleep(self.config.scan_interval_sec)

    async def _autonomous_cycle(self):
        """Single autonomous analysis + decision cycle."""
        now = datetime.now(timezone.utc)
        log.info(f"--- Autonomous Cycle {now.strftime('%H:%M:%S UTC')} ---")

        # Periodic account sync from broker (every cycle)
        if self.ctrader_mcp and self.ctrader_mcp.is_connected and not self.config.paper_mode:
            account_info = await self.ctrader_mcp.get_account_info()
            if account_info:
                self.account.balance = account_info.balance
                self.account.equity = account_info.equity
                self.account.update_peak()

        # Session filter
        active, sess_name = self.session_filter.is_active(now.hour, now.minute)
        log.info(f"Session: {sess_name} | Active: {active}")

        # Get market data
        df = await self._get_market_data()
        if df is None or len(df) < self.config.lookback_bars:
            log.info("Insufficient market data for analysis.")
            return

        # Manage existing trades
        if self.open_trades:
            current_price = float(df["close"].iloc[-1])
            await self._manage_open_trades(current_price, df)

# Session filter — skip if off-session
        if not active:
            log.info(f"    ⏳ No signal — {sess_name} (off-session)")
            return

        # Market-closed guard — pause autonomous scanning until market reopens
        if self._market_closed:
            log.info("    ⏸ Market closed — autonomous scanning paused (will auto-resume)")
            return

        # Risk check
        self.account.open_trades = len(self.open_trades)
        status = self.drawdown_guard.check(self.account)
        if not status["trading_allowed"]:
            log.warning(f"Trading paused: {status['reason']}")
            return

        for w in status["warnings"]:
            log.warning(w)
            await self._notify_warning(w)

        # Run analysis
        setup = self.analysis_engine.analyze(
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

        # ── VALIDATE setup.entry AGAINST LIVE FIX PRICE ──
        # setup.entry comes from df["close"].iloc[-1] which may be from stale/synthetic data.
        # We must validate it against the live broker price before proceeding.
        live_price = await self._get_current_price()
        if live_price > 0:
            entry_deviation = abs(setup.entry - live_price) / live_price
            if entry_deviation > 0.01:  # > 1% deviation
                log.warning(
                    f"Setup entry ${setup.entry:.2f} deviates {entry_deviation*100:.1f}% "
                    f"from live price ${live_price:.2f} — replacing with live price"
                )
                # Recalculate SL/TP relative to live price (preserve distance)
                sl_distance = abs(setup.entry - setup.stop_loss)
                tp_distance = abs(setup.entry - setup.take_profit)

                setup.entry = live_price
                if setup.signal == Signal.BUY:
                    setup.stop_loss = live_price - sl_distance
                    setup.take_profit = live_price + tp_distance
                else:
                    setup.stop_loss = live_price + sl_distance
                    setup.take_profit = live_price - tp_distance

                log.info(f"  Adjusted: Entry=${setup.entry:.2f} SL=${setup.stop_loss:.2f} "
                         f"TP=${setup.take_profit:.2f}")
        elif not self.config.paper_mode:
            # Cannot get live price in live mode — skip this signal
            log.error("Cannot validate setup.entry — no live price available. Skipping signal.")
            await self._notify_error(
                "Signal skipped: no live price to validate entry",
                context=f"{setup.signal.value} entry=${setup.entry:.2f}"
            )
            return

        # ── Deduplication: skip if same signal executed within window ──
        # Round prices to nearest 0.50 before hashing to absorb floating-point noise
        import hashlib
        _round_dedup = lambda p: f"{round(p * 2) / 2:.1f}"
        sig_hash = hashlib.md5(
            f"{setup.signal.value}|{_round_dedup(setup.entry)}|{_round_dedup(setup.stop_loss)}|{_round_dedup(setup.take_profit)}".encode()
        ).hexdigest()[:12]
        now_monotonic = asyncio.get_event_loop().time()
        if sig_hash == self._last_signal_hash and (now_monotonic - self._last_signal_time) < self._dedupe_window_sec:
            log.info(f"    ⏳ Duplicate signal suppressed ({self._dedupe_window_sec}s window)")
            return
        self._last_signal_hash = sig_hash
        self._last_signal_time = now_monotonic

        # Position size
        pos_spec = self.position_sizer.calculate(
            self.account, setup.entry, setup.stop_loss
        )
        lot = round(pos_spec.lot_size * status["size_multiplier"], 2)
        lot = max(lot, 1.0)  # Enforce minimum 1.0 lot for cTrader FIX demo server
        log.info(f"  Verified Entry: ${setup.entry:.2f} (FIX validated)")

        log.info(f"{'*'*50}")
        log.info(f"  SIGNAL: {setup.signal.value} | Confidence: {setup.confidence*100:.0f}%")
        log.info(f"  Entry: ${setup.entry:.2f} | SL: ${setup.stop_loss:.2f} | "
                 f"TP: ${setup.take_profit:.2f}")
        log.info(f"  R:R: {setup.rr_ratio} | Lots: {lot}")
        log.info(f"{'*'*50}")

        # ── LLM REVIEW: Ask AI to validate the signal before execution ──
        if self.llm_agent and self._llm_enabled:
            try:
                insight = await self._llm_review_signal(setup, lot)
                if not insight or insight.trade_recommendation.upper() == "HOLD":
                    reason = insight.summary[:200] if insight else "LLM review unavailable"
                    log.info(f"    ✗ LLM rejected signal for {self.config.symbol} — skipping execution")
                    await self._notify_warning(f"LLM rejected {setup.signal.value} for {self.config.symbol}: {reason}")
                    return
            except Exception as e:
                log.warning(f"    ⚠ LLM review failed ({e}) — proceeding without review")

        # Execute
        side = OrderSide.BUY if setup.signal == Signal.BUY else OrderSide.SELL
        exec_result = await self._execute_order(
            side=side,
            volume=lot,
            entry=setup.entry,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            comment=f"Auto:{sess_name}",
        )

        # Only send Telegram alert on successful execution
        if exec_result and self.telegram:
            await self.telegram.send_signal_alert(
                action=setup.signal.value,
                entry=setup.entry,
                stop_loss=setup.stop_loss,
                take_profit=setup.take_profit,
                confidence=setup.confidence,
                rr_ratio=setup.rr_ratio,
                lot_size=lot,
                reasons=setup.reasons,
            )
        self.signal_count += 1



    # ──────────────────────────────────────────
    # LLM Signal Review
    # ──────────────────────────────────────────

    async def _llm_review_signal(self, setup, lot_size: float):
        """
        Ask the LLM to review a signal BEFORE execution.
        The LLM checks against lessons, patterns, and risk rules.

        Works for both autonomous signals (TradeSetup) and webhook signals.

        Returns an insight object (has .trade_recommendation and .summary).
        - recommendation == "HOLD" → signal REJECTED
        - recommendation in ("BUY","SELL") → signal APPROVED
        - returns True (bool) if LLM unavailable / failed / timeout (auto-approve)
        - returns None on failure
        """
        if not self.llm_agent:
            return True  # No LLM = auto-approve

        symbol = self.config.symbol
        rr_ratio = getattr(setup, 'rr_ratio', 0)
        confidence = getattr(setup, 'confidence', 0.5)
        reasons = getattr(setup, 'reasons', [])

        query = (
            f"SIGNAL REVIEW REQUEST — should I execute this XAUUSD trade?\n\n"
            f"Symbol: {symbol}\n"
            f"Direction: {setup.signal.value}\n"
            f"Entry: ${setup.entry:,.2f}\n"
            f"Stop Loss: ${setup.stop_loss:,.2f}\n"
            f"Take Profit: ${setup.take_profit:,.2f}\n"
            f"R:R Ratio: {rr_ratio}\n"
            f"Confidence: {confidence*100:.0f}%\n"
            f"Lot Size: {lot_size}\n"
            f"Confluence Reasons: {', '.join(reasons[:5]) if reasons else 'N/A'}\n\n"
            f"Before approving, check:\n"
            f"1. Does this match your lessons? (call get_lessons if needed)\n"
            f"2. Does assess_setup show favorable history for XAUUSD?\n"
            f"3. Does the R:R and confidence meet minimum standards?\n"
            f"4. Is there any reason from past experience to avoid this trade?\n\n"
            f"Respond with Final Answer JSON including:\n"
            f"- trade_recommendation: 'BUY' or 'SELL' or 'HOLD'\n"
            f"- If HOLD = trade is REJECTED\n"
            f"- reasoning: why approved/rejected"
        )

        if not self.llm_agent._initialized:
            await self.llm_agent.initialize()

        try:
            insight = await asyncio.wait_for(
                self.llm_agent.analyze(query=query, role="SCREENER"),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            log.warning(f"    ⚠ LLM review timed out for {symbol} — auto-approving")
            return True

        if not insight:
            return True  # Failed = auto-approve

        recommendation = insight.trade_recommendation.upper()

        if recommendation == "HOLD":
            log.info(f"    🧠 LLM REJECTED: {symbol} | Reason: {insight.summary[:150]}")
            if self.lessons:
                self.lessons.add_lesson(
                    lesson=f"LLM rejected {setup.signal.value} {symbol}: {insight.summary[:150]}",
                    role="SCREENER",
                    source="agent",
                    context=f"Rejected at confidence {confidence*100:.0f}%",
                    symbol=symbol,
                    confidence=0.6,
                )
            return insight  # Caller checks .trade_recommendation == "HOLD"

        # ── LLM CONFIDENCE THRESHOLD ──
        # Even if LLM recommends BUY/SELL, reject if its own confidence is too low.
        # This prevents borderline approvals (e.g. 57%) from reaching execution.
        min_llm_conf = self.config.min_llm_confidence
        if insight.confidence < min_llm_conf:
            log.info(
                f"    🧠 LLM confidence too low: {insight.confidence*100:.0f}% "
                f"< {min_llm_conf*100:.0f}% threshold — treating as HOLD"
            )
            insight.trade_recommendation = "HOLD"
            if self.lessons:
                self.lessons.add_lesson(
                    lesson=f"LLM approved {setup.signal.value} {symbol} but with low confidence "
                           f"({insight.confidence*100:.0f}% < {min_llm_conf*100:.0f}% min). "
                           f"Borderline signals should be skipped.",
                    role="SCREENER",
                    source="agent",
                    context=f"Low AI confidence rejection",
                    symbol=symbol,
                    confidence=0.7,
                )
            return insight

        log.info(f"    🧠 LLM APPROVED: {symbol} {setup.signal.value} | "
                 f"AI Confidence: {insight.confidence*100:.0f}%")
        return insight  # Approved — return insight so caller has full details

    # ──────────────────────────────────────────
    # Order Execution (MCP / FIX / Paper)
    # ──────────────────────────────────────────

    async def _execute_order(
        self,
        side: OrderSide,
        volume: float,
        entry: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "Chastiefol",
    ):
        """Execute order via configured method (MCP, FIX, or Paper).
        
        Includes price sanity check: if the entry price deviates more than 1%
        from the live price, the order is rejected to prevent executing
        at stale/synthetic prices.
        
        Price validation priority:
          1. FIX price connection (synchronous, fastest)
          2. Async _get_current_price() fallback (MCP, DataFeed providers)
          3. If no price available in live mode → reject order
        """
        # ── PRICE SANITY CHECK ──
        # Validate entry against live price to catch stale/synthetic data.
        # Try FIX first (sync), then fallback to async multi-source.
        live_price = self._get_fix_price()
        if live_price <= 0:
            # FIX price unavailable — fallback to async price sources
            live_price = await self._get_current_price()

        if live_price > 0 and entry > 0:
            deviation = abs(entry - live_price) / live_price
            if deviation > 0.01:  # > 1% deviation
                log.error(
                    f"PRICE SANITY FAILED: entry=${entry:.2f} vs live=${live_price:.2f} "
                    f"(deviation={deviation*100:.1f}% > 1% threshold). "
                    f"Order REJECTED to prevent bad fill."
                )
                await self._notify_error(
                    f"Order rejected — price mismatch!\n"
                    f"Signal entry: ${entry:.2f}\n"
                    f"Live price: ${live_price:.2f}\n"
                    f"Deviation: {deviation*100:.1f}%\n"
                    f"Threshold: 1%", context=f"{side.value} {volume} lots"
                )
                return False
        elif live_price <= 0 and entry > 0 and not self.config.paper_mode:
            # No live price available at all in live mode — too risky to execute
            log.error(
                f"PRICE SANITY FAILED: No live price available to validate "
                f"entry=${entry:.2f}. Order REJECTED (live mode requires price validation)."
            )
            await self._notify_error(
                f"Order rejected — no live price for validation!\n"
                f"Signal entry: ${entry:.2f}\n"
                f"All price sources returned 0.\n"
                f"Cannot execute without price confirmation in live mode.",
                context=f"{side.value} {volume} lots"
            )
            return False

        log.info(f"Executing: {side.value} {volume} lots @ ~{entry:.2f} "
                 f"SL={stop_loss:.2f} TP={take_profit:.2f}")

        order_result = None

        if self.config.paper_mode or self.config.execution_method == "paper":
            # Paper execution
            order_result = self._paper_execute(side, volume, entry, stop_loss, take_profit)

        elif self.config.execution_method == "mcp" and self.ctrader_mcp:
            # cTrader MCP execution
            order_result = await self.ctrader_mcp.send_market_order(
                symbol=self.config.symbol,
                side=side,
                volume=volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                comment=comment,
            )

        elif self.config.execution_method == "fix" and self.ctrader_fix:
            # cTrader FIX execution — wait for broker confirmation
            order_result = await self.ctrader_fix.execute_order(
                symbol=self.config.symbol,
                side=side.value,
                volume=volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            if not order_result.get("success"):
                err_text = order_result.get("text", "")
                # Detect market-closed reject from broker
                if "MARKET_CLOSED" in err_text.upper() or "SESSION REJECT" in err_text.upper():
                    if not self._market_closed:
                        self._market_closed = True
                        self._market_closed_at = datetime.now(timezone.utc)
                        log.warning(f"Market closed detected — pausing autonomous cycle")
                        await self._notify_warning(f"Market closed ({err_text}). Pausing auto-trading ~30 min.")
                    else:
                        log.warning(f"Market still closed (since {self._market_closed_at.strftime('%H:%M UTC')})")
                else:
                    # Clear market-closed flag on other errors (might be transient)
                    if self._market_closed:
                        log.info("Order error different from MARKET_CLOSED — resuming monitoring")
                        self._market_closed = False
                log.error(f"FIX order failed: {err_text}")
                await self._notify_error(f"FIX order failed: {err_text}")
                return False

        else:
            log.error("No execution method available!")
            await self._notify_error("No execution method configured")
            return False

        # Handle result — normalize to common interface
        # OrderResult (dataclass from MCP/Paper) uses attribute access
        # FIX returns a plain dict — normalize both to attribute-style access
        if order_result is None:
            return False

        # Normalize FIX dict result to OrderResult dataclass
        if isinstance(order_result, dict):
            order_result = OrderResult(
                success=order_result.get("success", False),
                order_id=str(order_result.get("order_id", "")),
                position_id=str(order_result.get("position_id", "")),
                execution_price=float(order_result.get("execution_price", 0)),
                filled_volume=float(order_result.get("filled_volume", 0)),
                error_code="",
                error_message=order_result.get("text", ""),
            )

        if order_result.success:
            # ── VALIDATE FILL DATA before tracking ──
            # Only add to open_trades if we have valid fill confirmation.
            # This prevents ghost trades from partial/invalid broker responses.
            exec_price = order_result.execution_price or entry
            filled_vol = order_result.filled_volume or volume

            if exec_price <= 0:
                log.error(
                    f"Order reported success but execution_price=0 — "
                    f"NOT adding to open_trades (possible broker data issue)"
                )
                await self._notify_error(
                    f"Order success but no fill price! Broker may have "
                    f"acknowledged without filling. Check manually.",
                    context=f"{side.value} {volume} lots"
                )
                return False

            if filled_vol <= 0:
                log.error(
                    f"Order reported success but filled_volume=0 — "
                    f"NOT adding to open_trades (possible broker data issue)"
                )
                await self._notify_error(
                    f"Order success but no fill volume! Check broker manually.",
                    context=f"{side.value} {volume} lots"
                )
                return False

            trade = TradeLifecycle(
                entry_price=exec_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                lot_size=filled_vol,
                direction=side.value,
                current_price=exec_price,
            )
            self.open_trades.append(trade)
            self.account.open_trades = len(self.open_trades)

            # Map trade object → broker position ID for close operations
            broker_pos_id = order_result.position_id or order_result.order_id
            if broker_pos_id:
                self.trade_position_ids[id(trade)] = broker_pos_id
            else:
                log.warning(
                    f"No broker position ID returned — trade tracked locally "
                    f"but broker close/modify will not work"
                )

            log.info(f"✓ Order filled: {side.value} {filled_vol} lots @ "
                     f"{exec_price:.2f} "
                     f"(ID: {order_result.order_id})")

            # Notify
            if self.telegram:
                await self.telegram.send_order_executed(
                    action=side.value,
                    symbol=self.config.symbol,
                    volume=filled_vol,
                    price=exec_price,
                    order_id=order_result.order_id,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
            return True
        else:
            log.error(f"Order failed: {order_result.error_message}")
            await self._notify_error(
                f"Order rejected: {order_result.error_message}",
                context=f"{side.value} {volume} lots"
            )
            return False

    def _paper_execute(
        self, side: OrderSide, volume: float, entry: float,
        stop_loss: float, take_profit: float
    ) -> OrderResult:
        """Simulate order execution in paper mode."""
        import time as _time
        order_id = f"PAPER_{int(_time.time()*1000)}"
        log.info(f"[PAPER] Order filled: {side.value} {volume} lots @ {entry:.2f}")
        return OrderResult(
            success=True,
            order_id=order_id,
            position_id=order_id,
            execution_price=entry,
            filled_volume=volume,
        )

    async def _close_all_positions(self):
        """Close all open positions."""
        log.info("Closing all positions...")

        if self.ctrader_mcp and not self.config.paper_mode:
            results = await self.ctrader_mcp.close_all_positions(self.config.symbol)
            for r in results:
                if r.success:
                    log.info(f"Position {r.position_id} closed @ {r.execution_price:.2f}")
                else:
                    log.error(f"Failed to close {r.position_id}: {r.error_message}")

        elif self.ctrader_fix and not self.config.paper_mode:
            log.warning("FIX close all: not implemented yet — close manually")

        # Update local state
        for trade in self.open_trades:
            current_price = trade.current_price
            pnl = trade.pnl_usd
            self.account.balance += pnl
            self.trade_log.append({
                "direction": trade.direction,
                "entry": trade.entry_price,
                "exit": current_price,
                "lot_size": trade.lot_size,
                "pnl_usd": round(pnl, 2),
                "outcome": "WIN" if pnl > 0 else "LOSS",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            if self.telegram:
                await self.telegram.send_order_closed(
                    action=trade.direction,
                    volume=trade.lot_size,
                    entry_price=trade.entry_price,
                    exit_price=current_price,
                    pnl=pnl,
                )

        self.open_trades.clear()
        self.account.equity = self.account.balance
        self.account.open_trades = 0
        log.info(f"All positions closed. Balance: ${self.account.balance:.2f}")

    # ──────────────────────────────────────────
    # Trade Management
    # ──────────────────────────────────────────

    async def _manage_open_trades(self, current_price: float, df):
        """Manage open trades — trailing SL, breakeven, partial TP.
        
        When SL/TP is updated locally (trailing stop, breakeven move),
        also sends modification to the broker (FIX/MCP) to keep state in sync.
        """
        from Analysis.xauusd_engine import TechnicalIndicators
        ti = TechnicalIndicators()
        atr = ti.atr(df["high"], df["low"], df["close"]).iloc[-1]

        closed = []
        for trade in self.open_trades:
            trade.current_price = current_price
            result = self.trade_manager.manage(trade, current_price, current_atr=atr)

            for action_desc in result["action_descriptions"]:
                log.info(f"  Trade mgmt: {action_desc}")
                if self.telegram:
                    await self.telegram.send_trade_update(
                        update_type=action_desc.split(":")[0] if ":" in action_desc else "Update",
                        details=action_desc,
                    )

            # ── BROKER SL/TP SYNC: send modification to broker when SL changes ──
            if result["update_sl"] and not self.config.paper_mode:
                broker_pos_id = self.trade_position_ids.get(id(trade))
                if broker_pos_id:
                    try:
                        if self.ctrader_fix:
                            self.ctrader_fix.modify_position(
                                position_id=broker_pos_id,
                                symbol=self.config.symbol,
                                stop_loss=result["update_sl"],
                                take_profit=trade.take_profit,
                            )
                            log.info(f"  Broker SL/TP updated: pos={broker_pos_id} "
                                     f"SL={result['update_sl']:.2f} TP={trade.take_profit:.2f}")
                        elif self.ctrader_mcp:
                            await self.ctrader_mcp.modify_position(
                                position_id=broker_pos_id,
                                stop_loss=result["update_sl"],
                                take_profit=trade.take_profit,
                            )
                            log.info(f"  Broker SL/TP updated via MCP: pos={broker_pos_id}")
                    except Exception as e:
                        log.warning(f"  Failed to update SL/TP on broker: {e}")
                else:
                    log.warning(f"  No broker position ID — SL/TP only updated locally")

            if result["close_all"]:
                pnl = trade.pnl_usd
                self.account.balance += pnl
                self.account.equity = self.account.balance
                if pnl < 0:
                    self.account.daily_loss += abs(pnl)
                self.account.update_peak()

                outcome = "WIN" if pnl > 0 else "LOSS"
                self.trade_log.append({
                    "direction": trade.direction,
                    "entry": trade.entry_price,
                    "exit": current_price,
                    "lot_size": trade.lot_size,
                    "pnl_usd": round(pnl, 2),
                    "outcome": outcome,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                log.info(f"  Trade closed: {outcome} | P&L: ${pnl:+.2f}")

                if self.telegram:
                    await self.telegram.send_order_closed(
                        action=trade.direction,
                        volume=trade.lot_size,
                        entry_price=trade.entry_price,
                        exit_price=current_price,
                        pnl=pnl,
                    )

                # Close on broker side
                broker_close_ok = True
                if not self.config.paper_mode:
                    broker_pos_id = self.trade_position_ids.get(id(trade))
                    if broker_pos_id:
                        try:
                            if self.ctrader_mcp:
                                await self.ctrader_mcp.close_position(broker_pos_id)
                            elif self.ctrader_fix:
                                # FIX close: send opposite market order
                                opposite_side = "SELL" if trade.direction == "BUY" else "BUY"
                                close_result = await self.ctrader_fix.execute_order(
                                    symbol=self.config.symbol,
                                    side=opposite_side,
                                    volume=trade.lot_size,
                                )
                                if not close_result.get("success"):
                                    broker_close_ok = False
                                    log.error(
                                        f"  Broker close FAILED for pos={broker_pos_id}: "
                                        f"{close_result.get('text', 'unknown error')} — "
                                        f"keeping trade in local state"
                                    )
                        except Exception as e:
                            broker_close_ok = False
                            log.error(f"  Broker close exception: {e} — keeping in local state")
                    else:
                        log.warning(f"  No broker position ID for trade — cannot close on broker")

                # Only remove from local state if broker close succeeded (or paper mode)
                if broker_close_ok:
                    closed.append(trade)
                else:
                    # Revert local account changes since trade is still open
                    self.account.balance -= pnl
                    self.account.equity = self.account.balance
                    if pnl < 0:
                        self.account.daily_loss -= abs(pnl)

        for t in closed:
            self.open_trades.remove(t)
            self.trade_position_ids.pop(id(t), None)

    # ──────────────────────────────────────────
    # Data Retrieval
    # ──────────────────────────────────────────

    async def _get_market_data(self):
        """Get OHLCV data from data feed or synthetic."""
        if self.data_feed:
            df = await self.data_feed.get_ohlcv(
                timeframe=Timeframe.H1,
                bars=self.config.lookback_bars + 50,
            )
            if df is not None and not df.empty:
                return df

        # Fallback: synthetic
        return self._synthetic_data()

    async def _get_current_price(self) -> float:
        """
        Get current market price with FIX as PRIMARY fallback.
        
        Priority:
          1. FIX price connection (_latest_quotes) — always live from broker
          2. cTrader MCP quote
          3. DataFeed providers (TwelveData, AlphaVantage, GoldAPI)
          4. Return 0.0 (caller must handle)
        
        NOTE: FIX price is prioritized over DataFeed because DataFeed providers
        can be stale, rate-limited, or return synthetic data when all fail.
        FIX price comes directly from the broker's market data stream (port 5211).
        """
        # 1. FIX price — live from broker, always fresh
        if self.ctrader_fix:
            quote = self.ctrader_fix.get_latest_quote(self.config.symbol)
            if quote and quote.get("bid"):
                fix_price = float(quote["bid"])
                if fix_price > 0:
                    return fix_price

        # 2. cTrader MCP quote
        if self.ctrader_mcp and self.ctrader_mcp.is_connected:
            try:
                quote = await self.ctrader_mcp.get_quote(self.config.symbol)
                if quote:
                    bid = float(quote.get("bid", 0))
                    ask = float(quote.get("ask", 0))
                    if bid > 0 and ask > 0:
                        return (bid + ask) / 2
            except Exception as e:
                log.warning(f"MCP quote failed: {e}")

        # 3. DataFeed providers (may be stale/rate-limited)
        if self.data_feed:
            try:
                quote = await self.data_feed.get_quote()
                if quote and quote.price > 0:
                    return quote.price
            except Exception as e:
                log.warning(f"DataFeed quote failed: {e}")

        log.warning("All price sources failed — returning 0.0")
        return 0.0

    def _get_fix_price(self) -> float:
        """
        Get live price from FIX connection only (synchronous).
        Used for price validation checks.
        Returns 0.0 if FIX price not available.
        """
        if self.ctrader_fix:
            quote = self.ctrader_fix.get_latest_quote(self.config.symbol)
            if quote and quote.get("bid"):
                return float(quote["bid"])
        return 0.0

    async def _verify_entry_price(self, proposed_entry: float) -> float:
        """
        Verify proposed entry price against FIX market data.
        Returns corrected price or 0.0 if no price data available.
        
        If all external data providers failed and we have FIX connectivity,
        use FIX bid as the authoritative price. Also validates that
        proposed_entry is within reasonable bounds (±5% of FIX price).
        """
        # Priority 1: FIX market data (most authoritative, always connected)
        if self.ctrader_fix:
            quote = self.ctrader_fix.get_latest_quote(self.config.symbol)
            if quote:
                fix_bid = float(quote.get("bid", 0))
                fix_ask = float(quote.get("ask", 0))
                if fix_bid > 0 and fix_ask > 0:
                    fix_mid = (fix_bid + fix_ask) / 2
                    # If proposed price is way off from FIX, something is wrong
                    if proposed_entry > 0 and fix_mid > 0:
                        deviation = abs(proposed_entry - fix_mid) / fix_mid
                        if deviation > 0.05:
                            log.warning(f"[PRICE] Large deviation: signal=${proposed_entry:.2f} FIX mid=${fix_mid:.2f} ({deviation*100:.1f}%)")
                            return fix_mid
                        # If within tolerance, use FIX mid (more accurate)
                        if abs(proposed_entry - fix_mid) / fix_mid > 0.001:
                            return fix_mid
                        return proposed_entry
                    elif fix_mid > 0:
                        return fix_mid

        # Priority 2: data_feed (external providers)
        if self.data_feed:
            quote = await self.data_feed.get_quote()
            if quote and quote.price > 0:
                return quote.price

        # No price data available
        return 0.0

    def _synthetic_data(self):
        """Generate synthetic XAUUSD data for paper testing."""
        import numpy as np
        import pandas as pd
        import time as _time

        np.random.seed(int(_time.time()) % 1000)
        n = self.config.lookback_bars + 50
        prices = 2350 + np.cumsum(np.random.randn(n) * 2.5)
        return pd.DataFrame({
            "open": prices + np.random.randn(n) * 0.4,
            "high": prices + np.abs(np.random.randn(n)) * 4,
            "low": prices - np.abs(np.random.randn(n)) * 4,
            "close": prices,
            "volume": np.abs(np.random.randn(n)) * 1000 + 500,
        })

    # ──────────────────────────────────────────
    # Account Sync from Broker
    # ──────────────────────────────────────────

    async def _sync_account_from_broker(self):
        """
        Sync account balance/equity from cTrader broker.
        Called on startup and periodically to keep local state in sync.
        Replaces the static INITIAL_BALANCE with real broker data.
        """
        account_info = None

        # Try MCP first
        if self.ctrader_mcp and self.ctrader_mcp.is_connected:
            account_info = await self.ctrader_mcp.get_account_info()

        if account_info:
            old_balance = self.account.balance
            self.account.balance = account_info.balance
            self.account.equity = account_info.equity
            self.account.peak_balance = max(account_info.balance, account_info.equity)
            self.account.open_trades = 0  # Will be updated from positions

            # Also get open positions count
            positions = await self.ctrader_mcp.get_positions(self.config.symbol)
            if positions:
                self.account.open_trades = len(positions)

            log.info(f"✓ Account synced from broker:")
            log.info(f"    Balance:    ${self.account.balance:,.2f} (was ${old_balance:,.2f})")
            log.info(f"    Equity:     ${self.account.equity:,.2f}")
            log.info(f"    Margin:     ${account_info.margin_used:,.2f}")
            log.info(f"    Free Margin:${account_info.free_margin:,.2f}")
            log.info(f"    Leverage:   1:{account_info.leverage}")
            log.info(f"    Open Pos:   {self.account.open_trades}")
            log.info(f"    Account:    {'LIVE' if account_info.is_live else 'DEMO'}")

            # Notify via Telegram
            if self.telegram:
                await self.telegram.send_raw(
                    f"🔄 *ACCOUNT SYNCED*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Balance: `${self.account.balance:,.2f}`\n"
                    f"Equity: `${self.account.equity:,.2f}`\n"
                    f"Free Margin: `${account_info.free_margin:,.2f}`\n"
                    f"Leverage: `1:{account_info.leverage}`\n"
                    f"Open Positions: `{self.account.open_trades}`\n"
                    f"Account Type: `{'LIVE' if account_info.is_live else 'DEMO'}`\n"
                    f"\n_Synced from cTrader_"
                )
        else:
            log.warning("⚠ Could not sync account from broker — using INITIAL_BALANCE")
            if self.telegram:
                await self.telegram.send_warning(
                    f"Could not read account from cTrader.\n"
                    f"Using fallback balance: ${self.account.balance:,.2f}"
                )

    # ──────────────────────────────────────────
    # Notification Helpers
    # ──────────────────────────────────────────

    async def _notify_error(self, message: str, context: str = ""):
        """Send error notification."""
        if self.telegram:
            await self.telegram.send_error(message, context)

    async def _notify_warning(self, message: str):
        """Send warning notification."""
        if self.telegram:
            await self.telegram.send_warning(message)

    async def _notify_risk_alert(self, alert_type: str, value: float, threshold: float):
        """Send risk alert notification."""
        if self.telegram:
            await self.telegram.send_risk_alert(alert_type, value, threshold)

    # ──────────────────────────────────────────
    # Summary & Status
    # ──────────────────────────────────────────

    def _print_summary(self):
        """Print session summary."""
        total_pnl = self.account.balance - self.config.initial_balance
        wins = sum(1 for t in self.trade_log if t["outcome"] == "WIN")
        losses = sum(1 for t in self.trade_log if t["outcome"] == "LOSS")
        win_rate = (wins / len(self.trade_log) * 100) if self.trade_log else 0

        log.info(f"\n{'='*55}")
        log.info(f"  CHASTIEFOL SESSION SUMMARY")
        log.info(f"{'='*55}")
        log.info(f"  Mode:             {self.config.mode.value}")
        log.info(f"  Signals:          {self.signal_count}")
        log.info(f"  Trades:           {len(self.trade_log)}")
        log.info(f"  Wins/Losses:      {wins}/{losses} ({win_rate:.1f}%)")
        log.info(f"  Final Balance:    ${self.account.balance:,.2f}")
        log.info(f"  Session P&L:      ${total_pnl:+,.2f}")
        log.info(f"{'='*55}")

    def get_status(self) -> dict:
        """Get current agent status."""
        return {
            "running": self._is_running,
            "mode": self.config.mode.value,
            "paper_mode": self.config.paper_mode,
            "symbol": self.config.symbol,
            "balance": self.account.balance,
            "equity": self.account.equity,
            "open_trades": len(self.open_trades),
            "signals_generated": self.signal_count,
            "trades_completed": len(self.trade_log),
            "components": {
                "webhook": self.webhook.is_running if self.webhook else False,
                "ctrader_mcp": self.ctrader_mcp.is_connected if self.ctrader_mcp else False,
                "ctrader_fix": (self.ctrader_fix.price_conn.is_connected
                               if self.ctrader_fix else False),
                "data_feed": self.data_feed is not None,
                "telegram": (self.telegram._is_running if self.telegram else False),
            },
        }

    def save_trade_log(self, path: str = "trade_log.json"):
        """Save trade log to JSON file."""
        with open(path, "w") as f:
            json.dump(self.trade_log, f, indent=2, default=str)
        log.info(f"Trade log saved → {path}")


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

async def main():
    """Main entry point for Chastiefol Integrated Agent."""
    config = IntegratedConfig.from_env()
    agent = ChastiefollIntegrated(config)
    await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
