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
    ):
        """Send a trading signal alert."""
        emoji = {"BUY": "🟢", "SELL": "🔴", "CLOSE": "⚪"}.get(action.upper(), "📊")
        direction_emoji = "📈" if action.upper() == "BUY" else "📉"

        msg = (
            f"{emoji} *CHASTIEFOL SIGNAL*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{direction_emoji} *{action.upper()}* `{symbol}`\n\n"
            f"💰 Entry: `${entry:.2f}`\n"
            f"🛑 Stop Loss: `${stop_loss:.2f}`\n"
            f"🎯 Take Profit: `${take_profit:.2f}`\n"
            f"⚖️ R:R Ratio: `{rr_ratio:.1f}`\n"
            f"📊 Confidence: `{confidence*100:.0f}%`\n"
            f"📦 Lot Size: `{lot_size}`\n"
        )

        if reasons:
            msg += f"\n🔍 *Confluence ({len(reasons)} factors):*\n"
            for reason in reasons[:5]:
                msg += f"  ✓ {reason}\n"

        msg += f"\n🕐 _{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"

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
    ):
        """Send order execution confirmation."""
        emoji = "✅" if action.upper() in ("BUY", "SELL") else "🔄"
        msg = (
            f"{emoji} *ORDER EXECUTED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Action: *{action.upper()}*\n"
            f"Symbol: `{symbol}`\n"
            f"Volume: `{volume}` lots\n"
            f"Price: `${price:.2f}`\n"
            f"SL: `${stop_loss:.2f}`\n"
            f"TP: `${take_profit:.2f}`\n"
            f"Order ID: `{order_id}`\n"
            f"\n🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"
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
    ):
        """Send order close notification with P&L."""
        emoji = "💚" if pnl >= 0 else "💔"
        outcome = "WIN" if pnl >= 0 else "LOSS"
        msg = (
            f"{emoji} *TRADE CLOSED — {outcome}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Direction: *{action.upper()}*\n"
            f"Symbol: `{symbol}`\n"
            f"Volume: `{volume}` lots\n"
            f"Entry: `${entry_price:.2f}`\n"
            f"Exit: `${exit_price:.2f}`\n"
            f"P&L: *${pnl:+.2f}*\n"
        )
        if duration:
            msg += f"Duration: `{duration}`\n"
        msg += f"\n🕐 _{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"

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
    ):
        """Send daily P&L summary with per-pair breakdown."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        msg = (
            f"📋 *DAILY SUMMARY — {date}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"📊 *Performance:*\n"
            f"  Total Trades: `{total_trades}`\n"
            f"  Wins: `{wins}` | Losses: `{losses}`\n"
            f"  Win Rate: `{win_rate:.1f}%`\n"
            f"\n"
            f"💰 *Financials:*\n"
            f"  {pnl_emoji} Day P&L: *${total_pnl:+.2f}*\n"
            f"  Balance: `${balance:,.2f}`\n"
            f"  Equity: `${equity:,.2f}`\n"
            f"  Max Drawdown: `{max_drawdown:.2f}%`\n"
        )

        # Per-pair breakdown
        if per_pair:
            msg += f"\n📈 *Per Pair:*\n"
            for pair, stats in per_pair.items():
                pair_pnl = stats.get("pnl", 0)
                pair_trades = stats.get("trades", 0)
                pair_wins = stats.get("wins", 0)
                pair_losses = stats.get("losses", 0)
                pair_emoji = "🟢" if pair_pnl >= 0 else "🔴"
                msg += (
                    f"  {pair_emoji} {pair}: *${pair_pnl:+.2f}* "
                    f"({pair_wins}W/{pair_losses}L)\n"
                )

        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"_Chastiefol v3.0 | XAUUSD + BTCUSD_"
        )
        await self._enqueue(msg, NotificationType.DAILY_SUMMARY)

    async def send_system_status(self, status: str):
        """Send system status notification (startup/shutdown)."""
        emoji = "🟢" if status == "online" else "🔴"
        msg = (
            f"{emoji} *CHASTIEFOL {status.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: *{status}*\n"
            f"Time: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
            f"Mode: `Multi-Pair Automated Trading`\n"
            f"Pairs: `XAUUSD` | `BTCUSD`\n"
            f"\n_Chastiefol v3.0_"
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
