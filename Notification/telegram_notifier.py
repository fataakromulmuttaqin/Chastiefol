"""
Chastiefol — Telegram Notification Service
Real-time trading notifications via Telegram Bot API.

Features:
- Signal alerts (BUY/SELL/CLOSE)
- Order execution confirmation
- Error & warning alerts
- Daily P&L summary
- Risk alerts (drawdown, circuit breaker)
- Formatted messages with emojis & Markdown
- Message queue with retry logic
- Rate limit compliance (30 msg/sec Telegram limit)
"""

import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Telegram")


# ──────────────────────────────────────────────
# Configuration & Types
# ──────────────────────────────────────────────

class NotificationType(str, Enum):
    SIGNAL = "signal"
    ORDER_EXECUTED = "order_executed"
    ORDER_CLOSED = "order_closed"
    ERROR = "error"
    WARNING = "warning"
    RISK_ALERT = "risk_alert"
    DAILY_SUMMARY = "daily_summary"
    SYSTEM_STATUS = "system_status"
    TRADE_UPDATE = "trade_update"


@dataclass
class TelegramConfig:
    """Telegram Bot configuration."""
    bot_token: str = ""
    chat_id: str = ""
    parse_mode: str = "Markdown"
    disable_notification: bool = False
    max_retries: int = 3
    retry_delay: float = 1.0
    rate_limit_per_second: int = 25  # Telegram limit is 30/sec
    enabled: bool = True
    # Optional: send to multiple chat IDs
    additional_chat_ids: List[str] = field(default_factory=list)


@dataclass
class NotificationMessage:
    """Internal message structure for the queue."""
    text: str
    notification_type: NotificationType
    chat_id: str = ""
    timestamp: float = field(default_factory=time.time)
    retries: int = 0


# ──────────────────────────────────────────────
# Telegram Notifier
# ──────────────────────────────────────────────

class TelegramNotifier:
    """
    Async Telegram notification service for Chastiefol trading agent.
    
    Usage:
        config = TelegramConfig(bot_token="123:ABC", chat_id="-100123456")
        notifier = TelegramNotifier(config)
        await notifier.start()
        
        await notifier.send_signal_alert(signal_data)
        await notifier.send_order_executed(order_data)
        await notifier.send_daily_summary(summary_data)
        
        await notifier.stop()
    """

    BASE_URL = "https://api.telegram.org/bot{token}"

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._sent_count = 0
        self._error_count = 0
        self._last_send_time = 0.0

        if config.bot_token and config.chat_id:
            log.info(f"TelegramNotifier initialized | Chat: {config.chat_id}")
        else:
            log.warning("TelegramNotifier: Missing bot_token or chat_id — notifications disabled.")
            self.config.enabled = False

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    async def start(self):
        """Start the notification service and message worker."""
        if not self.config.enabled:
            log.info("Telegram notifications disabled.")
            return

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        self._is_running = True
        self._worker_task = asyncio.create_task(self._message_worker())
        log.info("Telegram notification service started.")

        # Send startup message
        await self.send_system_status("online")

    async def stop(self):
        """Stop the notification service gracefully."""
        if not self._is_running:
            return

        # Send shutdown message
        await self.send_system_status("offline")
        # Wait for queue to drain
        await asyncio.sleep(1)

        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        log.info(f"Telegram service stopped. Sent: {self._sent_count}, Errors: {self._error_count}")

    # ──────────────────────────────────────────
    # Public Notification Methods
    # ──────────────────────────────────────────

    async def send_signal_alert(
        self,
        action: str,
        symbol: str = "XAUUSD",
        entry: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        confidence: float = 0.0,
        rr_ratio: float = 0.0,
        lot_size: float = 0.0,
        reasons: List[str] = None,
        session: str = "",
        timeframe: str = "H1",
        atr: float = 0.0,
        spread: float = 0.0,
    ):
        """Send a rich XAUUSD trading signal alert with full analysis breakdown."""
        emoji = {"BUY": "🟢", "SELL": "🔴", "CLOSE": "⚪"}.get(action.upper(), "📊")
        direction_emoji = "📈" if action.upper() == "BUY" else "📉"

        # Calculate pip distance
        sl_pips = abs(entry - stop_loss) / 0.01 if entry > 0 else 0
        tp_pips = abs(take_profit - entry) / 0.01 if entry > 0 else 0

        # Confidence visual bar
        conf_pct = int(confidence * 100)
        conf_bar = "█" * (conf_pct // 10) + "░" * (10 - conf_pct // 10)

        # Risk amount estimate (assuming $10 per pip per lot for XAUUSD)
        risk_usd = sl_pips * 0.01 * lot_size * 100 if lot_size > 0 else 0
        reward_usd = tp_pips * 0.01 * lot_size * 100 if lot_size > 0 else 0

        msg = (
            f"{emoji} *XAUUSD SIGNAL — {action.upper()}*\n"
            f"╔══════════════════════════╗\n"
            f"║ {direction_emoji}  *{action.upper()} GOLD*  {'🏅' if symbol == 'XAUUSD' else '💎'}\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *TRADE SETUP* ─────────────┐\n"
            f"│ 💰 Entry:       `${entry:.2f}`\n"
            f"│ 🛑 Stop Loss:   `${stop_loss:.2f}` ({sl_pips:.0f} pips)\n"
            f"│ 🎯 Take Profit: `${take_profit:.2f}` ({tp_pips:.0f} pips)\n"
            f"│ 📦 Size:        `{lot_size}` lots\n"
            f"└────────────────────────────┘\n\n"
            f"┌─ *RISK METRICS* ────────────┐\n"
            f"│ ⚖️ R:R Ratio:  `1:{rr_ratio:.1f}`\n"
            f"│ 💸 Risk:       `~${risk_usd:.0f}`\n"
            f"│ 💎 Reward:     `~${reward_usd:.0f}`\n"
            f"│ 📊 Confidence: `{conf_pct}%` [{conf_bar}]\n"
            f"└────────────────────────────┘\n"
        )

        if reasons:
            msg += f"\n┌─ *CONFLUENCE* ({len(reasons)} factors) ──┐\n"
            for i, reason in enumerate(reasons, 1):
                icon = "✅" if i <= 3 else "✓"
                msg += f"│ {icon} {reason}\n"
            msg += f"└────────────────────────────┘\n"

        # Footer with session & timeframe
        session_str = f" | Session: {session}" if session else ""
        msg += (
            f"\n⏱ TF: `{timeframe}`{session_str}\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_\n"
            f"_⚔️ Chastiefol XAUUSD Agent_"
        )

        await self._enqueue(msg, NotificationType.SIGNAL)

    async def send_crypto_signal(
        self,
        action: str,
        symbol: str = "BTC/USDT",
        entry: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        confidence: float = 0.0,
        rr_ratio: float = 0.0,
        amount: float = 0.0,
        value_usdt: float = 0.0,
        risk_usdt: float = 0.0,
        risk_pct: float = 0.0,
        reasons: List[str] = None,
        category: str = "",
        timeframe: str = "1h",
        volume_ratio: float = 0.0,
        market_bias: str = "",
        adx: float = 0.0,
        rsi: float = 0.0,
    ):
        """Send a rich crypto trading signal with market context and analysis breakdown."""
        emoji = {"BUY": "🟢", "SELL": "🔴"}.get(action.upper(), "📊")
        direction_emoji = "📈" if action.upper() == "BUY" else "📉"

        # Pair emoji based on category
        cat_emoji = {
            "large_cap": "🏛️", "mid_cap": "💠", "small_cap": "🔹",
            "meme": "🐕", "defi": "🏦", "ai": "🤖",
            "layer2": "⚡", "gaming": "🎮", "commodity": "🥇",
        }.get(category.lower(), "🪙")

        # Confidence visual
        conf_pct = int(confidence * 100)
        conf_bar = "█" * (conf_pct // 10) + "░" * (10 - conf_pct // 10)

        # SL/TP percentage
        sl_pct = abs(entry - stop_loss) / entry * 100 if entry > 0 else 0
        tp_pct = abs(take_profit - entry) / entry * 100 if entry > 0 else 0

        # Volume strength indicator
        vol_str = ""
        if volume_ratio > 0:
            if volume_ratio >= 3.0:
                vol_str = f"🐋 `{volume_ratio:.1f}x` (WHALE ACTIVITY)"
            elif volume_ratio >= 2.0:
                vol_str = f"🔥 `{volume_ratio:.1f}x` (HIGH)"
            elif volume_ratio >= 1.5:
                vol_str = f"📊 `{volume_ratio:.1f}x` (Above avg)"
            else:
                vol_str = f"📉 `{volume_ratio:.1f}x` (Normal)"

        # Bias indicator
        bias_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(market_bias.lower(), "")

        # Base asset from symbol
        base = symbol.split("/")[0] if "/" in symbol else symbol.replace("USDT", "")

        msg = (
            f"{emoji} *CRYPTO SIGNAL — {action.upper()}*\n"
            f"╔══════════════════════════╗\n"
            f"║ {cat_emoji} *{action.upper()} {base}*  {direction_emoji}\n"
            f"║ `{symbol}` | {category.upper() if category else 'SPOT'}\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *TRADE SETUP* ─────────────┐\n"
            f"│ 💰 Entry:  `${entry:,.2f}`\n"
            f"│ 🛑 SL:     `${stop_loss:,.2f}` (-{sl_pct:.1f}%)\n"
            f"│ 🎯 TP:     `${take_profit:,.2f}` (+{tp_pct:.1f}%)\n"
            f"│ 📦 Amount: `{amount}` {base}\n"
            f"│ 💵 Value:  `${value_usdt:,.2f}` USDT\n"
            f"└────────────────────────────┘\n\n"
            f"┌─ *RISK & REWARD* ───────────┐\n"
            f"│ ⚖️ R:R:        `1:{rr_ratio:.1f}`\n"
            f"│ 💸 Risk:       `${risk_usdt:,.2f}` ({risk_pct:.1f}%)\n"
            f"│ 📊 Confidence: `{conf_pct}%` [{conf_bar}]\n"
            f"└────────────────────────────┘\n\n"
            f"┌─ *MARKET CONTEXT* ──────────┐\n"
        )

        if market_bias:
            msg += f"│ {bias_emoji} Bias: `{market_bias.upper()}`\n"
        if adx > 0:
            trend_str = "STRONG" if adx > 25 else "WEAK" if adx < 18 else "MODERATE"
            msg += f"│ 📐 ADX: `{adx:.1f}` ({trend_str} trend)\n"
        if rsi > 0:
            rsi_zone = "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL"
            msg += f"│ 📈 RSI: `{rsi:.1f}` ({rsi_zone})\n"
        if vol_str:
            msg += f"│ 📊 Volume: {vol_str}\n"
        msg += f"└────────────────────────────┘\n"

        if reasons:
            msg += f"\n┌─ *ANALYSIS* ({len(reasons)} signals) ──┐\n"
            for i, reason in enumerate(reasons, 1):
                icon = "✅" if i <= 3 else "•"
                msg += f"│ {icon} {reason}\n"
            msg += f"└────────────────────────────┘\n"

        msg += (
            f"\n⏱ TF: `{timeframe}` | 24/7 Market\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_\n"
            f"_⚔️ Chastiefol Crypto Scanner_"
        )

        await self._enqueue(msg, NotificationType.SIGNAL)

    async def send_llm_review(
        self,
        symbol: str,
        action: str,
        approved: bool,
        ai_confidence: float = 0.0,
        reasoning: str = "",
        model: str = "",
        entry: float = 0.0,
        signal_confidence: float = 0.0,
        lessons_referenced: int = 0,
        review_time_ms: float = 0.0,
    ):
        """Send LLM signal review result with full AI reasoning."""
        if approved:
            emoji = "🧠✅"
            status = "APPROVED"
            header_color = "╔══ AI APPROVED ══════════╗"
        else:
            emoji = "🧠❌"
            status = "REJECTED"
            header_color = "╔══ AI REJECTED ══════════╗"

        direction_emoji = "📈" if action.upper() == "BUY" else "📉"

        # AI confidence visual
        ai_conf_pct = int(ai_confidence * 100)
        ai_conf_bar = "█" * (ai_conf_pct // 10) + "░" * (10 - ai_conf_pct // 10)

        # Signal confidence visual
        sig_conf_pct = int(signal_confidence * 100)

        msg = (
            f"{emoji} *LLM REVIEW — {status}*\n"
            f"{header_color}\n"
            f"║ {direction_emoji} {action.upper()} `{symbol}` @ `${entry:,.2f}`\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *AI ASSESSMENT* ──────────┐\n"
            f"│ 🤖 Model:      `{model or 'LLM'}`\n"
            f"│ 🎯 AI Conf:    `{ai_conf_pct}%` [{ai_conf_bar}]\n"
            f"│ 📊 Signal Conf: `{sig_conf_pct}%`\n"
            f"│ 📚 Lessons:    `{lessons_referenced}` referenced\n"
        )
        if review_time_ms > 0:
            msg += f"│ ⏱ Review:     `{review_time_ms:.0f}ms`\n"
        msg += f"│ 🏁 Decision:  *{status}*\n"
        msg += f"└────────────────────────────┘\n"

        if reasoning:
            # Split reasoning into lines for readability
            reason_lines = reasoning.strip().split(". ")
            msg += f"\n┌─ *REASONING* ──────────────┐\n"
            for line in reason_lines[:5]:
                line = line.strip()
                if line:
                    if not line.endswith("."):
                        line += "."
                    msg += f"│ 💡 {line}\n"
            msg += f"└────────────────────────────┘\n"

        verdict = "Trade will be executed." if approved else "Trade was skipped."
        msg += (
            f"\n{'✅' if approved else '⛔'} _{verdict}_\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_\n"
            f"_⚔️ Chastiefol AI Review Engine_"
        )

        await self._enqueue(msg, NotificationType.SIGNAL)

    async def send_order_executed(
        self,
        action: str,
        symbol: str = "XAUUSD",
        volume: float = 0.0,
        price: float = 0.0,
        order_id: str = "",
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        rr_ratio: float = 0.0,
        confidence: float = 0.0,
        execution_method: str = "",
    ):
        """Send order execution confirmation with rich details."""
        emoji = "✅" if action.upper() in ("BUY", "SELL") else "🔄"
        direction_emoji = "📈" if action.upper() == "BUY" else "📉"

        # Calculate distances
        sl_dist = abs(price - stop_loss) if price > 0 and stop_loss > 0 else 0
        tp_dist = abs(take_profit - price) if price > 0 and take_profit > 0 else 0

        # Execution method badge
        exec_badge = {
            "fix": "⚡ FIX", "mcp": "🔌 MCP", "paper": "📝 PAPER",
            "binance": "🟡 Binance",
        }.get(execution_method.lower(), "")

        msg = (
            f"{emoji} *ORDER FILLED*\n"
            f"╔══════════════════════════╗\n"
            f"║ {direction_emoji} *{action.upper()}* `{symbol}`\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *EXECUTION DETAILS* ──────┐\n"
            f"│ 💰 Fill Price: `${price:,.2f}`\n"
            f"│ 📦 Volume:    `{volume}` lots\n"
            f"│ 🛑 SL:        `${stop_loss:,.2f}` (Δ{sl_dist:.2f})\n"
            f"│ 🎯 TP:        `${take_profit:,.2f}` (Δ{tp_dist:.2f})\n"
        )
        if rr_ratio > 0:
            msg += f"│ ⚖️ R:R:       `1:{rr_ratio:.1f}`\n"
        if confidence > 0:
            msg += f"│ 📊 Conf:      `{confidence*100:.0f}%`\n"
        if exec_badge:
            msg += f"│ 🏷️ Via:       {exec_badge}\n"
        msg += (
            f"│ 🆔 ID:        `{order_id[:20]}`\n"
            f"└────────────────────────────┘\n\n"
            f"⏳ _Trade is now being monitored..._\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"
        )
        await self._enqueue(msg, NotificationType.ORDER_EXECUTED)

    async def send_order_closed(
        self,
        action: str,
        symbol: str = "XAUUSD",
        volume: float = 0.0,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        pnl: float = 0.0,
        duration: str = "",
        close_reason: str = "",
        balance_after: float = 0.0,
        win_streak: int = 0,
        total_trades: int = 0,
        win_rate: float = 0.0,
    ):
        """Send order close notification with P&L and performance context."""
        is_win = pnl >= 0
        emoji = "💰" if is_win else "💸"
        outcome = "WIN" if is_win else "LOSS"
        outcome_emoji = "🏆" if is_win else "📉"

        # Calculate move in pips/percentage
        move_pips = abs(exit_price - entry_price) / 0.01 if entry_price > 0 else 0
        move_pct = abs(exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

        # Close reason badge
        reason_badge = {
            "tp_hit": "🎯 Take Profit Hit",
            "sl_hit": "🛑 Stop Loss Hit",
            "trailing_stop": "📏 Trailing Stop",
            "breakeven": "⚖️ Breakeven Stop",
            "manual": "🖐️ Manual Close",
        }.get(close_reason.lower(), close_reason or "Auto")

        # Streak indicator
        streak_str = ""
        if win_streak > 2:
            streak_str = f"\n│ 🔥 Streak: `{win_streak} wins in a row!`"
        elif win_streak < -2:
            streak_str = f"\n│ ❄️ Cold: `{abs(win_streak)} losses in a row`"

        msg = (
            f"{emoji} *TRADE CLOSED — {outcome}*\n"
            f"╔══════════════════════════╗\n"
            f"║ {outcome_emoji} *{action.upper()}* `{symbol}` = *${pnl:+,.2f}*\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *TRADE RESULT* ────────────┐\n"
            f"│ 📥 Entry:    `${entry_price:,.2f}`\n"
            f"│ 📤 Exit:     `${exit_price:,.2f}`\n"
            f"│ {'📈' if is_win else '📉'} Move:     `{move_pips:.0f} pips` ({move_pct:.2f}%)\n"
            f"│ 📦 Volume:   `{volume}` lots\n"
            f"│ {'💰' if is_win else '💸'} P&L:      *${pnl:+,.2f}*\n"
            f"│ 📋 Reason:   {reason_badge}\n"
        )
        if duration:
            msg += f"│ ⏱ Duration: `{duration}`\n"
        msg += f"└────────────────────────────┘\n"

        # Account overview
        if balance_after > 0 or total_trades > 0:
            msg += f"\n┌─ *ACCOUNT* ────────────────┐\n"
            if balance_after > 0:
                msg += f"│ 💼 Balance: `${balance_after:,.2f}`\n"
            if total_trades > 0:
                msg += f"│ 📊 Trades:  `{total_trades}` total\n"
            if win_rate > 0:
                msg += f"│ 🏆 WinRate: `{win_rate:.1f}%`\n"
            if streak_str:
                msg += streak_str + "\n"
            msg += f"└────────────────────────────┘\n"

        msg += f"\n🕐 _{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"
        await self._enqueue(msg, NotificationType.ORDER_CLOSED)

    async def send_error(self, error_message: str, context: str = ""):
        """Send error notification."""
        msg = (
            f"🚨 *ERROR*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"`{error_message}`\n"
        )
        if context:
            msg += f"\nContext: _{context}_\n"
        msg += f"\n🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"

        await self._enqueue(msg, NotificationType.ERROR)

    async def send_signal_rejected(
        self,
        symbol: str,
        action: str,
        entry: float,
        reason: str,
        confidence: float = 0.0,
        rejected_by: str = "",
    ):
        """Send signal rejection notification with clear reason."""
        emoji = {"BUY": "🟢", "SELL": "🔴"}.get(action.upper(), "📊")
        direction_emoji = "📈" if action.upper() == "BUY" else "📉"

        # Who rejected
        rejector = ""
        if rejected_by:
            rejector = f"\n│ 🏷️ Rejected by: `{rejected_by}`"

        msg = (
            f"⛔ *SIGNAL REJECTED*\n"
            f"╔══════════════════════════╗\n"
            f"║ {emoji} {action.upper()} `{symbol}` — BLOCKED\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *DETAILS* ────────────────┐\n"
            f"│ 💰 Entry:      `${entry:,.2f}`\n"
            f"│ 📊 Confidence: `{confidence*100:.0f}%`"
            f"{rejector}\n"
            f"└────────────────────────────┘\n\n"
            f"┌─ *REASON* ─────────────────┐\n"
            f"│ ❌ {reason}\n"
            f"└────────────────────────────┘\n\n"
            f"_Signal was NOT executed._\n"
            f"🕐 _{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
        )
        await self._enqueue(msg, NotificationType.SIGNAL)

    async def send_warning(self, warning_message: str):
        """Send warning notification."""
        msg = (
            f"⚠️ *WARNING*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{warning_message}\n"
            f"\n🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"
        )
        await self._enqueue(msg, NotificationType.WARNING)

    async def send_risk_alert(
        self,
        alert_type: str,
        current_value: float = 0.0,
        threshold: float = 0.0,
        action_taken: str = "",
    ):
        """Send risk management alert (drawdown, circuit breaker, etc.)."""
        msg = (
            f"🛡️ *RISK ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Type: *{alert_type}*\n"
            f"Current: `{current_value:.2f}%`\n"
            f"Threshold: `{threshold:.2f}%`\n"
        )
        if action_taken:
            msg += f"Action: _{action_taken}_\n"
        msg += f"\n🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"

        await self._enqueue(msg, NotificationType.RISK_ALERT)

    async def send_daily_summary(
        self,
        date: str = "",
        total_trades: int = 0,
        wins: int = 0,
        losses: int = 0,
        total_pnl: float = 0.0,
        balance: float = 0.0,
        equity: float = 0.0,
        max_drawdown: float = 0.0,
        win_rate: float = 0.0,
        per_pair: Dict[str, Dict] = None,
        lessons_learned: int = 0,
        signals_generated: int = 0,
        signals_rejected: int = 0,
        best_trade: Dict = None,
        worst_trade: Dict = None,
    ):
        """Send comprehensive daily P&L summary with per-pair breakdown and insights."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        day_grade = ""
        if total_trades > 0:
            if win_rate >= 70:
                day_grade = "🏆 A+"
            elif win_rate >= 60:
                day_grade = "⭐ A"
            elif win_rate >= 50:
                day_grade = "👍 B"
            elif win_rate >= 40:
                day_grade = "⚠️ C"
            else:
                day_grade = "📉 D"

        msg = (
            f"📋 *DAILY REPORT — {date}*\n"
            f"╔══════════════════════════╗\n"
            f"║  {pnl_emoji} P&L: *${total_pnl:+,.2f}*  {day_grade}\n"
            f"╚══════════════════════════╝\n\n"
            f"┌─ *PERFORMANCE* ────────────┐\n"
            f"│ 📊 Trades:    `{total_trades}`\n"
            f"│ ✅ Wins:      `{wins}`\n"
            f"│ ❌ Losses:    `{losses}`\n"
            f"│ 🏆 Win Rate:  `{win_rate:.1f}%`\n"
        )
        if signals_generated > 0:
            msg += f"│ 📡 Signals:   `{signals_generated}` generated\n"
        if signals_rejected > 0:
            msg += f"│ ⛔ Rejected:  `{signals_rejected}` (by AI/risk)\n"
        msg += (
            f"└────────────────────────────┘\n\n"
            f"┌─ *ACCOUNT* ────────────────┐\n"
            f"│ 💼 Balance:     `${balance:,.2f}`\n"
            f"│ 📊 Equity:      `${equity:,.2f}`\n"
            f"│ 📉 Max DD:      `{max_drawdown:.2f}%`\n"
            f"│ {pnl_emoji} Day P&L:    *${total_pnl:+,.2f}*\n"
            f"└────────────────────────────┘\n"
        )

        # Per-pair breakdown
        if per_pair:
            msg += f"\n┌─ *PER PAIR* ───────────────┐\n"
            for pair, stats in per_pair.items():
                pair_pnl = stats.get("pnl", 0)
                pair_trades = stats.get("trades", 0)
                pair_wins = stats.get("wins", 0)
                pair_losses = stats.get("losses", 0)
                pair_emoji = "🟢" if pair_pnl >= 0 else "🔴"
                msg += (
                    f"│ {pair_emoji} *{pair}*: `${pair_pnl:+,.2f}` "
                    f"({pair_wins}W/{pair_losses}L)\n"
                )
            msg += f"└────────────────────────────┘\n"

        # Best/worst trade highlights
        if best_trade or worst_trade:
            msg += f"\n┌─ *HIGHLIGHTS* ─────────────┐\n"
            if best_trade:
                msg += (
                    f"│ 🏆 Best: {best_trade.get('symbol', '')} "
                    f"`+${best_trade.get('pnl', 0):,.2f}`\n"
                )
            if worst_trade:
                msg += (
                    f"│ 💸 Worst: {worst_trade.get('symbol', '')} "
                    f"`${worst_trade.get('pnl', 0):,.2f}`\n"
                )
            msg += f"└────────────────────────────┘\n"

        # Learning stats
        if lessons_learned > 0:
            msg += (
                f"\n🧠 _AI learned {lessons_learned} new "
                f"lesson{'s' if lessons_learned > 1 else ''} today._\n"
            )

        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_⚔️ Chastiefol Unified v5.0_"
        )
        await self._enqueue(msg, NotificationType.DAILY_SUMMARY)

    async def send_system_status(self, status: str, modules: Dict[str, bool] = None):
        """Send system status notification for unified mode showing all active modules."""
        emoji = "🟢" if status == "online" else "🔴"
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        # Try to get pair count
        pair_info = "100+ pairs"
        try:
            from Analysis.crypto_pair_config import CRYPTO_CONFIGS
            pair_info = f"{len(CRYPTO_CONFIGS)} crypto pairs"
        except ImportError:
            pass

        if status == "online":
            msg = (
                f"🚀 *CHASTIEFOL UNIFIED — ONLINE*\n"
                f"╔══════════════════════════╗\n"
                f"║  ⚔️ Trading Systems Active  ║\n"
                f"╚══════════════════════════╝\n\n"
                f"┌─ *MODULES* ─────────────────┐\n"
            )
            if modules:
                for mod_name, mod_active in modules.items():
                    mod_emoji = "✅" if mod_active else "⬜"
                    msg += f"│ {mod_emoji} {mod_name}\n"
            else:
                msg += (
                    f"│ ✅ XAUUSD Agent (London/NY)\n"
                    f"│ ✅ Multi-Pair (XAU+BTC)\n"
                    f"│ ✅ Crypto Scanner (24/7)\n"
                )
            msg += (
                f"└────────────────────────────┘\n\n"
                f"┌─ *CONNECTIONS* ─────────────┐\n"
                f"│ 📡 FIX Price: port `5211`\n"
                f"│ 📡 FIX Trade: port `5212`\n"
                f"│ 🌐 Webhook:   port `8080`\n"
                f"│ 🟡 Binance:   CCXT + WS\n"
                f"│ 💬 Telegram:  Connected\n"
                f"│ 🧠 LLM:       Active\n"
                f"└────────────────────────────┘\n\n"
                f"┌─ *COVERAGE* ────────────────┐\n"
                f"│ 🏅 Gold:    XAUUSD (sessions)\n"
                f"│ 💎 BTC:     BTCUSD (24/7)\n"
                f"│ 🪙 Crypto:  {pair_info}\n"
                f"│ 📊 Data:    TV WS + CCXT\n"
                f"│ 📚 Learning: Enabled\n"
                f"└────────────────────────────┘\n\n"
                f"🕐 `{now_str}`\n"
                f"_⚔️ Chastiefol v5.0 Unified_"
            )
        else:
            msg = (
                f"🔴 *CHASTIEFOL UNIFIED — OFFLINE*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"All modules shutting down...\n"
                f"🕐 `{now_str}`\n"
                f"_See you next session!_ 👋"
            )

        await self._enqueue(msg, NotificationType.SYSTEM_STATUS)

    async def send_trade_update(
        self,
        update_type: str,
        symbol: str = "XAUUSD",
        details: str = "",
    ):
        """Send trade management update (trailing SL, breakeven, partial close)."""
        msg = (
            f"🔄 *TRADE UPDATE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Type: *{update_type}*\n"
            f"Symbol: `{symbol}`\n"
        )
        if details:
            msg += f"Details: _{details}_\n"
        msg += f"\n🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"

        await self._enqueue(msg, NotificationType.TRADE_UPDATE)

    # ──────────────────────────────────────────
    # Raw Send (for custom messages)
    # ──────────────────────────────────────────

    async def send_message(self, text: str):
        """Send a raw text message (convenience alias used by modules)."""
        await self._enqueue(text, NotificationType.SYSTEM_STATUS)

    async def send_raw(self, text: str, notification_type: NotificationType = NotificationType.SYSTEM_STATUS):
        """Send a raw text message."""
        await self._enqueue(text, notification_type)

    # ──────────────────────────────────────────
    # Internal: Queue & Worker
    # ──────────────────────────────────────────

    async def _enqueue(self, text: str, notification_type: NotificationType):
        """Add message to the send queue."""
        if not self.config.enabled:
            return

        msg = NotificationMessage(
            text=text,
            notification_type=notification_type,
            chat_id=self.config.chat_id,
        )
        await self._message_queue.put(msg)

        # Also send to additional chat IDs
        for chat_id in self.config.additional_chat_ids:
            extra_msg = NotificationMessage(
                text=text,
                notification_type=notification_type,
                chat_id=chat_id,
            )
            await self._message_queue.put(extra_msg)

    async def _message_worker(self):
        """Background worker that processes the message queue."""
        while self._is_running:
            try:
                msg = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
                await self._send_message(msg)
                self._message_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Worker error: {e}")

    async def _send_message(self, msg: NotificationMessage):
        """Send a single message to Telegram with retry logic."""
        if not self._session:
            return

        # Rate limiting
        elapsed = time.time() - self._last_send_time
        min_interval = 1.0 / self.config.rate_limit_per_second
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

        url = f"{self.BASE_URL.format(token=self.config.bot_token)}/sendMessage"
        payload = {
            "chat_id": msg.chat_id or self.config.chat_id,
            "text": msg.text,
            "parse_mode": self.config.parse_mode,
            "disable_notification": self.config.disable_notification,
        }

        for attempt in range(1, self.config.max_retries + 1):
            try:
                async with self._session.post(url, json=payload) as resp:
                    self._last_send_time = time.time()

                    if resp.status == 200:
                        self._sent_count += 1
                        log.debug(f"Message sent ({msg.notification_type.value})")
                        return
                    elif resp.status == 429:
                        # Rate limited by Telegram
                        data = await resp.json()
                        wait = data.get("parameters", {}).get("retry_after", 5)
                        log.warning(f"Telegram rate limit. Waiting {wait}s...")
                        await asyncio.sleep(wait)
                    elif resp.status == 400:
                        data = await resp.json()
                        log.error(f"Telegram bad request: {data.get('description', '')}")
                        self._error_count += 1
                        return  # Don't retry bad requests
                    else:
                        log.warning(f"Telegram HTTP {resp.status} (attempt {attempt})")
                        if attempt < self.config.max_retries:
                            await asyncio.sleep(self.config.retry_delay * attempt)

            except asyncio.TimeoutError:
                log.warning(f"Telegram timeout (attempt {attempt})")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_delay * attempt)
            except Exception as e:
                log.error(f"Telegram send error: {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_delay * attempt)

        self._error_count += 1
        log.error(f"Failed to send message after {self.config.max_retries} attempts.")

    # ──────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────

    @property
    def stats(self) -> Dict:
        """Get notification statistics."""
        return {
            "sent": self._sent_count,
            "errors": self._error_count,
            "queue_size": self._message_queue.qsize(),
            "enabled": self.config.enabled,
            "running": self._is_running,
        }

    async def test_connection(self) -> bool:
        """Test if bot token and chat_id are valid."""
        if not self.config.bot_token or not self.config.chat_id:
            return False

        url = f"{self.BASE_URL.format(token=self.config.bot_token)}/getMe"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        bot_name = data.get("result", {}).get("username", "unknown")
                        log.info(f"Telegram bot verified: @{bot_name}")
                        return True
                    else:
                        log.error(f"Bot verification failed: HTTP {resp.status}")
                        return False
        except Exception as e:
            log.error(f"Connection test failed: {e}")
            return False


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

async def main():
    """Test Telegram notifier standalone."""
    import os

    config = TelegramConfig(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )

    notifier = TelegramNotifier(config)

    # Test connection
    is_valid = await notifier.test_connection()
    if not is_valid:
        log.error("Bot connection test failed. Check TELEGRAM_BOT_TOKEN env var.")
        return

    await notifier.start()

    # Send test notifications
    await notifier.send_signal_alert(
        action="BUY",
        symbol="XAUUSD",
        entry=2365.50,
        stop_loss=2350.00,
        take_profit=2395.00,
        confidence=0.78,
        rr_ratio=1.9,
        lot_size=0.05,
        reasons=["EMA alignment bullish", "RSI oversold bounce", "Order block support"],
    )

    await asyncio.sleep(2)

    await notifier.send_daily_summary(
        total_trades=5,
        wins=3,
        losses=2,
        total_pnl=127.50,
        balance=10127.50,
        equity=10150.00,
        max_drawdown=1.2,
        win_rate=60.0,
    )

    await asyncio.sleep(2)
    await notifier.stop()


if __name__ == "__main__":
    asyncio.run(main())
