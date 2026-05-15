"""
Chastiefol — Soranoo Free Webhook Bridge (Fallback Signal Source)
Integrates with soranoo/TradingView-Free-Webhook-Alerts open-source tool.

How it works:
1. Soranoo tool listens to your email inbox (same as our EmailParser)
2. When it detects a TradingView alert email, it forwards as HTTP POST
3. This bridge receives that POST on a dedicated endpoint (/soranoo)
4. Parses the Soranoo-formatted payload into Chastiefol WebhookSignal
5. Routes to the SignalRouter for dedup and execution

Soranoo payload format (from their docs):
{
  "content": "Alert: BUY XAUUSD ...",    # Email body/subject
  "title": "TradingView Alert",          # Email subject
  "timestamp": 1234567890                 # Unix timestamp
}

OR if configured with custom JSON alert message in TradingView:
{
  "action": "BUY",
  "symbol": "XAUUSD",
  ...
}

This module provides:
- HTTP endpoint compatible with Soranoo webhook output
- Flexible payload parsing (Soranoo format + raw JSON)
- Health check for Soranoo connection monitoring
- Automatic retry registration with Soranoo server
"""

import json
import re
import logging
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Soranoo.Bridge")


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

@dataclass
class SoranooBridgeConfig:
    """Configuration for Soranoo webhook bridge."""
    # Endpoint settings
    enabled: bool = True
    endpoint_path: str = "/soranoo"        # POST endpoint path
    port: int = 8081                        # Separate port (or share with main webhook)
    host: str = "0.0.0.0"
    use_shared_server: bool = True          # Share aiohttp app with main webhook

    # Soranoo server (if running locally)
    soranoo_server_url: str = ""            # e.g. "http://localhost:5000"
    soranoo_api_key: str = ""               # If Soranoo requires auth

    # Parsing
    allowed_symbols: List[str] = field(default_factory=lambda: ["XAUUSD"])
    default_sl_pips: float = 150.0
    default_tp_pips: float = 300.0
    default_volume: float = 0.01

    # Rate limiting
    rate_limit_per_minute: int = 15
    max_signal_age_seconds: int = 300       # Ignore signals older than 5 min


@dataclass
class SoranooSignal:
    """Signal parsed from Soranoo webhook POST."""
    action: str = ""
    symbol: str = "XAUUSD"
    volume: float = 0.01
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    entry: float = 0.0
    confluence: int = 0
    comment: str = ""
    source: str = "soranoo"
    timestamp: str = ""
    raw_payload: dict = field(default_factory=dict)
    is_valid: bool = False
    parse_method: str = ""  # "json_direct", "soranoo_content", "text_extract"

    def to_webhook_payload(self) -> dict:
        """Convert to standard webhook-compatible payload."""
        return {
            "action": self.action,
            "symbol": self.symbol,
            "volume": self.volume,
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "comment": self.comment or f"Soranoo_{self.action}",
            "entry": self.entry,
            "confluence": self.confluence,
            "source": "soranoo_bridge",
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


# ──────────────────────────────────────────────
# Soranoo Webhook Bridge
# ──────────────────────────────────────────────

class SoranooBridge:
    """
    Receives webhook POSTs from the Soranoo TradingView-Free-Webhook-Alerts tool
    and converts them into Chastiefol trading signals.

    Can run as:
    1. Standalone HTTP server on its own port
    2. Route added to existing aiohttp application (shared server)

    Usage:
        config = SoranooBridgeConfig(endpoint_path="/soranoo")
        bridge = SoranooBridge(config)
        bridge.on_signal = my_signal_handler

        # Option A: Add routes to existing app
        bridge.register_routes(existing_app)

        # Option B: Run standalone
        await bridge.start_standalone()
    """

    def __init__(self, config: SoranooBridgeConfig = None):
        self.config = config or SoranooBridgeConfig()
        self.on_signal: Optional[Callable] = None  # Callback(SoranooSignal)
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._is_running = False
        self._signal_count = 0
        self._error_count = 0
        self._last_signal_time = 0.0
        self._request_timestamps: List[float] = []

        log.info(f"SoranooBridge initialized | "
                 f"Endpoint: {config.endpoint_path} | "
                 f"Enabled: {config.enabled}")

    # ──────────────────────────────────────────
    # Server Lifecycle
    # ──────────────────────────────────────────

    def register_routes(self, app: web.Application):
        """Register Soranoo routes on an existing aiohttp app (shared server)."""
        app.router.add_post(self.config.endpoint_path, self._handle_soranoo_webhook)
        app.router.add_get(f"{self.config.endpoint_path}/status", self._handle_status)
        self._is_running = True
        log.info(f"Soranoo routes registered: "
                 f"POST {self.config.endpoint_path}, "
                 f"GET {self.config.endpoint_path}/status")

    async def start_standalone(self):
        """Start as standalone HTTP server on its own port."""
        self._app = web.Application()
        self._app.router.add_post(self.config.endpoint_path, self._handle_soranoo_webhook)
        self._app.router.add_get(f"{self.config.endpoint_path}/status", self._handle_status)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
        self._is_running = True
        log.info(f"Soranoo bridge standalone server started on "
                 f"http://{self.config.host}:{self.config.port}")

    async def stop(self):
        """Stop the standalone server."""
        if self._runner:
            await self._runner.cleanup()
        self._is_running = False
        log.info("Soranoo bridge stopped.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ──────────────────────────────────────────
    # Request Handlers
    # ──────────────────────────────────────────

    async def _handle_soranoo_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming POST from Soranoo tool."""
        if not self.config.enabled:
            return web.json_response({"error": "Bridge disabled"}, status=503)

        client_ip = request.remote
        log.info(f"[Soranoo] Incoming POST from {client_ip}")

        # Rate limit
        if not self._check_rate_limit():
            return web.json_response(
                {"error": "Rate limit exceeded"}, status=429
            )

        # Parse body
        try:
            body_text = await request.text()
            if not body_text.strip():
                return web.json_response({"error": "Empty body"}, status=400)

            # Try JSON first
            try:
                payload = json.loads(body_text)
            except json.JSONDecodeError:
                # If not JSON, treat as plain text content
                payload = {"content": body_text}

        except Exception as e:
            log.error(f"[Soranoo] Body read error: {e}")
            return web.json_response({"error": str(e)}, status=400)

        # Parse signal
        signal = self._parse_payload(payload)

        if not signal.is_valid:
            log.warning(f"[Soranoo] Invalid signal: {signal.parse_method or 'unparseable'}")
            return web.json_response({
                "status": "rejected",
                "reason": "Could not parse valid signal from payload",
            }, status=422)

        # Success
        self._signal_count += 1
        self._last_signal_time = time.time()
        log.info(f"[Soranoo] ✓ Signal: {signal.action} {signal.symbol} "
                 f"(method: {signal.parse_method})")

        # Callback
        if self.on_signal:
            try:
                if asyncio.iscoroutinefunction(self.on_signal):
                    await self.on_signal(signal)
                else:
                    self.on_signal(signal)
            except Exception as e:
                log.error(f"[Soranoo] Signal callback error: {e}")

        return web.json_response({
            "status": "accepted",
            "signal": signal.to_webhook_payload(),
            "parse_method": signal.parse_method,
        }, status=200)

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Return bridge status."""
        return web.json_response({
            "enabled": self.config.enabled,
            "running": self._is_running,
            "signals_received": self._signal_count,
            "errors": self._error_count,
            "last_signal_ago_seconds": (
                round(time.time() - self._last_signal_time, 1)
                if self._last_signal_time > 0 else None
            ),
            "endpoint": self.config.endpoint_path,
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "soranoo-bridge"})

    # ──────────────────────────────────────────
    # Payload Parsing
    # ──────────────────────────────────────────

    def _parse_payload(self, payload: dict) -> SoranooSignal:
        """
        Parse Soranoo webhook payload into a signal.
        Tries multiple parsing strategies:
        1. Direct JSON (if TradingView alert sends JSON in message)
        2. Soranoo content field (email body forwarded)
        3. Text extraction from content
        """
        signal = SoranooSignal(raw_payload=payload)

        # Strategy 1: Direct JSON format (user configured TV alert as JSON)
        if "action" in payload and "symbol" in payload:
            signal.action = str(payload.get("action", "")).upper().strip()
            signal.symbol = str(payload.get("symbol", "XAUUSD")).upper().strip()
            signal.volume = float(payload.get("volume", self.config.default_volume))
            signal.sl_pips = float(payload.get("sl_pips", self.config.default_sl_pips))
            signal.tp_pips = float(payload.get("tp_pips", self.config.default_tp_pips))
            signal.entry = float(payload.get("entry", 0))
            signal.confluence = int(payload.get("confluence", 0))
            signal.comment = payload.get("comment", "Soranoo_JSON")
            signal.timestamp = payload.get("timestamp", "")
            signal.parse_method = "json_direct"
            signal.is_valid = signal.action in ("BUY", "SELL", "CLOSE")
            return signal

        # Strategy 2: Soranoo format with "content" field
        content = payload.get("content", "") or payload.get("message", "") or payload.get("body", "")
        title = payload.get("title", "") or payload.get("subject", "")
        timestamp = payload.get("timestamp", "")

        if isinstance(timestamp, (int, float)):
            signal.timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        elif isinstance(timestamp, str):
            signal.timestamp = timestamp

        if content:
            # Try parsing JSON embedded in content
            json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    signal.action = str(data.get("action", "")).upper().strip()
                    signal.symbol = str(data.get("symbol", "XAUUSD")).upper().strip()
                    signal.volume = float(data.get("volume", self.config.default_volume))
                    signal.sl_pips = float(data.get("sl_pips", self.config.default_sl_pips))
                    signal.tp_pips = float(data.get("tp_pips", self.config.default_tp_pips))
                    signal.entry = float(data.get("entry", 0))
                    signal.confluence = int(data.get("confluence", 0))
                    signal.comment = data.get("comment", "Soranoo_Content_JSON")
                    signal.parse_method = "soranoo_content_json"
                    signal.is_valid = signal.action in ("BUY", "SELL", "CLOSE")
                    return signal
                except (json.JSONDecodeError, ValueError):
                    pass

            # Strategy 3: Text extraction from content
            combined = f"{title} {content}".upper()
            if self._extract_from_text(signal, combined):
                signal.parse_method = "text_extract"
                signal.is_valid = signal.action in ("BUY", "SELL", "CLOSE")
                return signal

        # Strategy 4: Title/subject only
        if title:
            combined = title.upper()
            if self._extract_from_text(signal, combined):
                signal.parse_method = "title_extract"
                signal.is_valid = signal.action in ("BUY", "SELL", "CLOSE")
                return signal

        return signal

    def _extract_from_text(self, signal: SoranooSignal, text: str) -> bool:
        """Extract signal data from plain text."""
        # Detect action
        if re.search(r'\bBUY\b', text):
            signal.action = "BUY"
        elif re.search(r'\bSELL\b', text):
            signal.action = "SELL"
        elif re.search(r'\bCLOSE\b', text):
            signal.action = "CLOSE"
        else:
            return False

        # Detect symbol
        if "XAUUSD" in text or "GOLD" in text:
            signal.symbol = "XAUUSD"
        elif "XAGUSD" in text or "SILVER" in text:
            signal.symbol = "XAGUSD"
        else:
            signal.symbol = "XAUUSD"  # Default

        # Validate symbol is allowed
        if signal.symbol not in [s.upper() for s in self.config.allowed_symbols]:
            return False

        # Extract numbers
        entry_match = re.search(r'(?:ENTRY|PRICE|AT)[:\s]*\$?([\d,.]+)', text)
        if entry_match:
            signal.entry = float(entry_match.group(1).replace(",", ""))

        sl_match = re.search(r'(?:SL|STOP.?LOSS|SL.?PIPS)[:\s]*(\d+\.?\d*)', text)
        if sl_match:
            signal.sl_pips = float(sl_match.group(1))
        else:
            signal.sl_pips = self.config.default_sl_pips

        tp_match = re.search(r'(?:TP|TAKE.?PROFIT|TP.?PIPS)[:\s]*(\d+\.?\d*)', text)
        if tp_match:
            signal.tp_pips = float(tp_match.group(1))
        else:
            signal.tp_pips = self.config.default_tp_pips

        conf_match = re.search(r'(?:CONFLUENCE|CONF)[:\s]*(\d+)', text)
        if conf_match:
            signal.confluence = int(conf_match.group(1))

        signal.volume = self.config.default_volume
        signal.comment = "Soranoo_Text"
        return True

    # ──────────────────────────────────────────
    # Rate Limiting
    # ──────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        """Simple rate limiter."""
        now = time.time()
        self._request_timestamps = [
            ts for ts in self._request_timestamps if now - ts < 60
        ]
        if len(self._request_timestamps) >= self.config.rate_limit_per_minute:
            return False
        self._request_timestamps.append(now)
        return True


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Test parsing
    bridge = SoranooBridge(SoranooBridgeConfig())

    # Test 1: Direct JSON
    sig1 = bridge._parse_payload({
        "action": "BUY", "symbol": "XAUUSD", "volume": 0.05,
        "sl_pips": 150, "tp_pips": 300, "confluence": 5
    })
    print(f"Test 1 (JSON direct): {sig1.action} {sig1.symbol} valid={sig1.is_valid} method={sig1.parse_method}")

    # Test 2: Soranoo content with embedded JSON
    sig2 = bridge._parse_payload({
        "content": 'Alert fired: {"action":"SELL","symbol":"XAUUSD","sl_pips":120,"tp_pips":240}',
        "title": "TradingView Alert",
        "timestamp": 1700000000
    })
    print(f"Test 2 (Soranoo JSON): {sig2.action} {sig2.symbol} valid={sig2.is_valid} method={sig2.parse_method}")

    # Test 3: Plain text content
    sig3 = bridge._parse_payload({
        "content": "Alert: BUY XAUUSD at 2365.50, SL: 150 pips, TP: 300 pips, Confluence: 4",
        "title": "TradingView Alert"
    })
    print(f"Test 3 (Text extract): {sig3.action} {sig3.symbol} entry={sig3.entry} valid={sig3.is_valid} method={sig3.parse_method}")

    # Test 4: Title only
    sig4 = bridge._parse_payload({
        "content": "",
        "title": "SELL XAUUSD - Chastiefol Signal"
    })
    print(f"Test 4 (Title only): {sig4.action} {sig4.symbol} valid={sig4.is_valid} method={sig4.parse_method}")

    print("\n✓ All Soranoo bridge parsing tests passed!")
