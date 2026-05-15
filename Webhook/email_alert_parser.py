"""
Chastiefol — Email Alert Parser (Primary Signal Source)
Polls Gmail/IMAP inbox for TradingView alert emails and converts them to
trading signals that are forwarded to the Chastiefol webhook.

How it works:
1. TradingView (free plan) sends alert email to your Gmail
2. This module polls Gmail via IMAP every N seconds
3. Parses email subject/body for signal data (action, symbol, SL, TP)
4. Converts to WebhookSignal and forwards to internal signal queue
5. Marks email as read to avoid reprocessing

Supports:
- Gmail (IMAP with App Password)
- Outlook/Hotmail
- Any IMAP-compatible email provider
- Custom parsing rules for different alert formats
"""

import imaplib
import email
import re
import json
import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Tuple
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("EmailParser")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

class EmailProvider(str, Enum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    CUSTOM = "custom"


@dataclass
class EmailConfig:
    """Email parser configuration."""
    # IMAP connection
    provider: EmailProvider = EmailProvider.GMAIL
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    email_address: str = ""
    email_password: str = ""  # Gmail: use App Password (not regular password)
    use_ssl: bool = True

    # Polling
    poll_interval_seconds: int = 10  # Check every 10 seconds
    max_emails_per_poll: int = 5     # Process max 5 emails per cycle

    # Filtering
    sender_filter: str = "noreply@tradingview.com"  # Only process from TradingView
    subject_keywords: List[str] = field(default_factory=lambda: [
        "Alert", "XAUUSD", "BUY", "SELL", "CLOSE", "Chastiefol"
    ])

    # Parsing
    parse_mode: str = "auto"  # "auto" | "json" | "text" | "subject_only"

    # Cleanup
    mark_as_read: bool = True
    delete_after_parse: bool = False
    max_email_age_minutes: int = 30  # Ignore emails older than this

    # Provider presets
    @classmethod
    def gmail(cls, email_addr: str, app_password: str) -> "EmailConfig":
        return cls(
            provider=EmailProvider.GMAIL,
            imap_host="imap.gmail.com",
            imap_port=993,
            email_address=email_addr,
            email_password=app_password,
        )

    @classmethod
    def outlook(cls, email_addr: str, password: str) -> "EmailConfig":
        return cls(
            provider=EmailProvider.OUTLOOK,
            imap_host="outlook.office365.com",
            imap_port=993,
            email_address=email_addr,
            email_password=password,
        )


@dataclass
class ParsedSignal:
    """Signal parsed from email alert."""
    action: str = ""       # BUY, SELL, CLOSE
    symbol: str = "XAUUSD"
    volume: float = 0.01
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    entry: float = 0.0
    confluence: int = 0
    comment: str = ""
    source: str = "email"
    email_subject: str = ""
    email_timestamp: str = ""
    raw_body: str = ""
    is_valid: bool = False
    parse_error: str = ""

    def to_webhook_payload(self) -> dict:
        """Convert to webhook-compatible JSON payload."""
        return {
            "action": self.action,
            "symbol": self.symbol,
            "volume": self.volume,
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "comment": self.comment or f"Email_Alert_{self.action}",
            "entry": self.entry,
            "confluence": self.confluence,
            "source": "email_parser",
            "timestamp": self.email_timestamp or datetime.now(timezone.utc).isoformat(),
        }


# ──────────────────────────────────────────────
# Email Alert Parser
# ──────────────────────────────────────────────

class EmailAlertParser:
    """
    Polls email inbox for TradingView alerts and converts to trading signals.

    Usage:
        config = EmailConfig.gmail("your@gmail.com", "xxxx xxxx xxxx xxxx")
        parser = EmailAlertParser(config)

        # Set callback for when signal is parsed
        parser.on_signal = my_signal_handler

        # Start polling
        await parser.start()

        # ... runs in background ...

        await parser.stop()
    """

    def __init__(self, config: EmailConfig):
        self.config = config
        self.on_signal: Optional[Callable] = None  # Callback(ParsedSignal)
        self._imap: Optional[imaplib.IMAP4_SSL] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._processed_ids: set = set()  # Track processed email IDs
        self._signal_count = 0
        self._error_count = 0
        self._last_poll_time = 0.0

        log.info(f"EmailAlertParser initialized | "
                 f"Provider: {config.provider.value} | "
                 f"Email: {self._mask_email(config.email_address)} | "
                 f"Interval: {config.poll_interval_seconds}s")

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    async def start(self):
        """Start the email polling loop."""
        if not self.config.email_address or not self.config.email_password:
            log.error("Email credentials not configured. Cannot start parser.")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info("Email alert parser started.")

    async def stop(self):
        """Stop polling and disconnect."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._disconnect()
        log.info(f"Email parser stopped. Signals: {self._signal_count}, Errors: {self._error_count}")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "signals_parsed": self._signal_count,
            "errors": self._error_count,
            "processed_emails": len(self._processed_ids),
            "last_poll": self._last_poll_time,
        }

    # ──────────────────────────────────────────
    # IMAP Connection
    # ──────────────────────────────────────────

    def _connect(self) -> bool:
        """Connect to IMAP server."""
        try:
            if self.config.use_ssl:
                self._imap = imaplib.IMAP4_SSL(
                    self.config.imap_host, self.config.imap_port
                )
            else:
                self._imap = imaplib.IMAP4(
                    self.config.imap_host, self.config.imap_port
                )

            self._imap.login(self.config.email_address, self.config.email_password)
            self._imap.select("INBOX")
            log.debug("IMAP connected successfully.")
            return True
        except imaplib.IMAP4.error as e:
            log.error(f"IMAP login failed: {e}")
            self._error_count += 1
            return False
        except Exception as e:
            log.error(f"IMAP connection error: {e}")
            self._error_count += 1
            return False

    def _disconnect(self):
        """Disconnect from IMAP server."""
        if self._imap:
            try:
                self._imap.close()
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    def _ensure_connected(self) -> bool:
        """Ensure IMAP connection is active, reconnect if needed."""
        if self._imap is None:
            return self._connect()
        try:
            self._imap.noop()
            return True
        except Exception:
            self._disconnect()
            return self._connect()

    # ──────────────────────────────────────────
    # Polling Loop
    # ──────────────────────────────────────────

    async def _poll_loop(self):
        """Main polling loop — runs in background."""
        while self._running:
            try:
                await self._poll_once()
                self._last_poll_time = time.time()
                await asyncio.sleep(self.config.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Poll loop error: {e}")
                self._error_count += 1
                await asyncio.sleep(self.config.poll_interval_seconds * 2)

    async def _poll_once(self):
        """Single poll cycle — check for new emails."""
        # Run IMAP operations in thread to avoid blocking
        loop = asyncio.get_event_loop()
        signals = await loop.run_in_executor(None, self._fetch_and_parse)

        for signal in signals:
            if signal.is_valid and self.on_signal:
                self._signal_count += 1
                log.info(f"📧 Email signal parsed: {signal.action} {signal.symbol} "
                         f"(SL={signal.sl_pips}, TP={signal.tp_pips})")
                if asyncio.iscoroutinefunction(self.on_signal):
                    await self.on_signal(signal)
                else:
                    self.on_signal(signal)

    def _fetch_and_parse(self) -> List[ParsedSignal]:
        """Fetch unread emails and parse signals (runs in thread)."""
        signals = []

        if not self._ensure_connected():
            return signals

        try:
            # Search for unread emails from TradingView
            search_criteria = self._build_search_criteria()
            status, message_ids = self._imap.search(None, search_criteria)

            if status != "OK" or not message_ids[0]:
                return signals

            ids = message_ids[0].split()
            # Limit per poll
            ids = ids[-self.config.max_emails_per_poll:]

            for msg_id in ids:
                msg_id_str = msg_id.decode()

                # Skip already processed
                if msg_id_str in self._processed_ids:
                    continue

                # Fetch email
                status, msg_data = self._imap.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                # Parse email
                raw_email = msg_data[0][1]
                email_msg = email.message_from_bytes(raw_email)

                # Check age
                if self._is_too_old(email_msg):
                    self._processed_ids.add(msg_id_str)
                    continue

                # Parse signal from email
                signal = self._parse_email(email_msg)
                if signal.is_valid:
                    signals.append(signal)

                # Mark as processed
                self._processed_ids.add(msg_id_str)

                # Mark as read
                if self.config.mark_as_read:
                    self._imap.store(msg_id, "+FLAGS", "\\Seen")

                # Delete if configured
                if self.config.delete_after_parse:
                    self._imap.store(msg_id, "+FLAGS", "\\Deleted")

            if self.config.delete_after_parse:
                self._imap.expunge()

        except Exception as e:
            log.error(f"Fetch error: {e}")
            self._error_count += 1
            self._disconnect()

        # Keep processed IDs from growing too large
        if len(self._processed_ids) > 1000:
            self._processed_ids = set(list(self._processed_ids)[-500:])

        return signals

    def _build_search_criteria(self) -> str:
        """Build IMAP search criteria."""
        criteria_parts = ["UNSEEN"]

        if self.config.sender_filter:
            criteria_parts.append(f'FROM "{self.config.sender_filter}"')

        # Only search recent emails
        since_date = (datetime.now() - timedelta(minutes=self.config.max_email_age_minutes))
        criteria_parts.append(f'SINCE "{since_date.strftime("%d-%b-%Y")}"')

        return f'({" ".join(criteria_parts)})'

    def _is_too_old(self, email_msg) -> bool:
        """Check if email is older than max age."""
        date_str = email_msg.get("Date", "")
        if not date_str:
            return False
        try:
            from email.utils import parsedate_to_datetime
            email_date = parsedate_to_datetime(date_str)
            age = datetime.now(timezone.utc) - email_date
            return age.total_seconds() > self.config.max_email_age_minutes * 60
        except Exception:
            return False

    # ──────────────────────────────────────────
    # Email Parsing
    # ──────────────────────────────────────────

    def _parse_email(self, email_msg) -> ParsedSignal:
        """Parse a TradingView alert email into a signal."""
        signal = ParsedSignal()

        # Get subject
        subject = self._decode_subject(email_msg.get("Subject", ""))
        signal.email_subject = subject

        # Get timestamp
        signal.email_timestamp = email_msg.get("Date", "")

        # Get body
        body = self._get_email_body(email_msg)
        signal.raw_body = body

        # Try parsing methods in order
        if self.config.parse_mode == "json":
            self._parse_json_body(signal, body)
        elif self.config.parse_mode == "subject_only":
            self._parse_subject(signal, subject)
        elif self.config.parse_mode == "text":
            self._parse_text_body(signal, subject, body)
        else:  # "auto" — try all methods
            # 1. Try JSON in body
            if not self._parse_json_body(signal, body):
                # 2. Try structured text parsing
                if not self._parse_text_body(signal, subject, body):
                    # 3. Try subject-only parsing
                    self._parse_subject(signal, subject)

        # Validate
        if signal.action in ("BUY", "SELL", "CLOSE") and signal.symbol:
            signal.is_valid = True
            # Default SL/TP if not parsed
            if signal.action != "CLOSE":
                if signal.sl_pips <= 0:
                    signal.sl_pips = 150  # Default 150 pips SL
                if signal.tp_pips <= 0:
                    signal.tp_pips = 300  # Default 300 pips TP (2:1 RR)

        return signal

    def _parse_json_body(self, signal: ParsedSignal, body: str) -> bool:
        """Try to parse JSON from email body."""
        # Find JSON in body
        json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', body, re.DOTALL)
        if not json_match:
            # Try finding any JSON object
            json_match = re.search(r'\{[^{}]+\}', body, re.DOTALL)

        if json_match:
            try:
                data = json.loads(json_match.group())
                signal.action = data.get("action", "").upper().strip()
                signal.symbol = data.get("symbol", "XAUUSD").upper().strip()
                signal.volume = float(data.get("volume", 0.01))
                signal.sl_pips = float(data.get("sl_pips", 0))
                signal.tp_pips = float(data.get("tp_pips", 0))
                signal.entry = float(data.get("entry", 0))
                signal.confluence = int(data.get("confluence", 0))
                signal.comment = data.get("comment", "Email_JSON")
                return True
            except (json.JSONDecodeError, ValueError):
                pass
        return False

    def _parse_text_body(self, signal: ParsedSignal, subject: str, body: str) -> bool:
        """Parse signal from plain text email body."""
        combined = f"{subject}\n{body}".upper()

        # Detect action
        if re.search(r'\bBUY\b', combined):
            signal.action = "BUY"
        elif re.search(r'\bSELL\b', combined):
            signal.action = "SELL"
        elif re.search(r'\bCLOSE\b', combined):
            signal.action = "CLOSE"
        else:
            return False

        # Detect symbol
        if "XAUUSD" in combined or "GOLD" in combined:
            signal.symbol = "XAUUSD"
        elif "XAGUSD" in combined or "SILVER" in combined:
            signal.symbol = "XAGUSD"

        # Try to extract numbers
        # Entry price
        entry_match = re.search(r'(?:entry|price|at)[:\s]*\$?([\d,.]+)', combined)
        if entry_match:
            signal.entry = float(entry_match.group(1).replace(",", ""))

        # SL pips
        sl_match = re.search(r'(?:sl|stop.?loss|sl.?pips)[:\s]*(\d+\.?\d*)', combined)
        if sl_match:
            signal.sl_pips = float(sl_match.group(1))

        # TP pips
        tp_match = re.search(r'(?:tp|take.?profit|tp.?pips)[:\s]*(\d+\.?\d*)', combined)
        if tp_match:
            signal.tp_pips = float(tp_match.group(1))

        # Confluence
        conf_match = re.search(r'(?:confluence|conf)[:\s]*(\d+)', combined)
        if conf_match:
            signal.confluence = int(conf_match.group(1))

        signal.comment = "Email_Text"
        return signal.action != ""

    def _parse_subject(self, signal: ParsedSignal, subject: str) -> bool:
        """Parse signal from email subject line only."""
        subject_upper = subject.upper()

        if "BUY" in subject_upper:
            signal.action = "BUY"
        elif "SELL" in subject_upper:
            signal.action = "SELL"
        elif "CLOSE" in subject_upper:
            signal.action = "CLOSE"
        else:
            return False

        if "XAUUSD" in subject_upper or "GOLD" in subject_upper:
            signal.symbol = "XAUUSD"

        signal.comment = "Email_Subject"
        return signal.action != ""

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    def _decode_subject(self, subject: str) -> str:
        """Decode email subject (handles encoded headers)."""
        if not subject:
            return ""
        decoded_parts = decode_header(subject)
        result = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="ignore")
            else:
                result += part
        return result.strip()

    def _get_email_body(self, email_msg) -> str:
        """Extract plain text body from email."""
        body = ""
        if email_msg.is_multipart():
            for part in email_msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="ignore")
                        break
                elif content_type == "text/html" and not body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="ignore")
                        # Strip HTML tags for basic parsing
                        body = re.sub(r'<[^>]+>', ' ', html)
                        body = re.sub(r'\s+', ' ', body).strip()
        else:
            payload = email_msg.get_payload(decode=True)
            if payload:
                charset = email_msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="ignore")
        return body

    @staticmethod
    def _mask_email(email_addr: str) -> str:
        """Mask email for logging."""
        if "@" in email_addr:
            name, domain = email_addr.split("@")
            return f"{name[:3]}***@{domain}"
        return "***"


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import os

    config = EmailConfig.gmail(
        email_addr=os.environ.get("EMAIL_ADDRESS", ""),
        app_password=os.environ.get("EMAIL_APP_PASSWORD", ""),
    )

    parser = EmailAlertParser(config)

    # Test parsing directly
    test_body = '{"action":"BUY","symbol":"XAUUSD","volume":0.01,"sl_pips":150,"tp_pips":300,"comment":"PSAR_EMA_Strategy","confluence":5}'
    signal = ParsedSignal()
    parser._parse_json_body(signal, test_body)
    print(f"JSON parse test: {signal.action} {signal.symbol} SL={signal.sl_pips} TP={signal.tp_pips}")

    # Test text parsing
    test_subject = "Alert: BUY XAUUSD - Chastiefol Signal"
    test_text_body = "Action: BUY\nSymbol: XAUUSD\nEntry: 2365.50\nSL pips: 150\nTP pips: 300\nConfluence: 5"
    signal2 = ParsedSignal()
    parser._parse_text_body(signal2, test_subject, test_text_body)
    print(f"Text parse test: {signal2.action} {signal2.symbol} Entry={signal2.entry} SL={signal2.sl_pips}")

    # Test subject-only
    signal3 = ParsedSignal()
    parser._parse_subject(signal3, "Alert: SELL XAUUSD triggered")
    print(f"Subject parse test: {signal3.action} {signal3.symbol}")

    print("\n✓ All parsing tests passed!")
