"""
Chastiefol — Notification Package
Provides real-time alerting via Telegram Bot API.
"""

from .telegram_notifier import TelegramNotifier, TelegramConfig, NotificationType

__all__ = [
    "TelegramNotifier",
    "TelegramConfig",
    "NotificationType",
]
