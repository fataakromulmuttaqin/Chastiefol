"""
Chastiefol — Webhook Listener
Receives trading signals from TradingView via HTTP POST.
Validates payload, authenticates via secret key, and queues signals for execution.
"""

import json
import hmac
import hashlib
import logging
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, List
from enum import Enum

from aiohttp import web

from Common.health import HealthRegistry, HealthStatus, default_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Webhook")


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────

class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"


@dataclass
class WebhookSignal:
    """Parsed and validated signal from TradingView webhook."""
    action: TradeAction
    symbol: str
    volume: float
    sl_pips: float
    tp_pips: float
    comment: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action"] = self.action.value
        return d


@dataclass
class WebhookConfig:
    """Configuration for the webhook listener."""
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = ""
    allowed_symbols: List[str] = field(default_factory=lambda: ["XAUUSD"])
    max_volume: float = 1.0
    min_volume: float = 1.0  # cTrader FIX demo server min = 1.0 lot
    rate_limit_per_minute: int = 10
    enable_hmac_auth: bool = True


# ──────────────────────────────────────────────
# Webhook Listener
# ──────────────────────────────────────────────

class WebhookListener:
    """
    Async HTTP server that listens for TradingView webhook alerts.
    
    Features:
    - JSON payload parsing & validation
    - HMAC-SHA256 authentication (via X-Webhook-Secret header)
    - Rate limiting
    - Health check endpoint
    - Signal queue for downstream processing
    """

    def __init__(
        self,
        config: WebhookConfig = None,
        on_signal: Optional[Callable] = None,
        health_registry: Optional[HealthRegistry] = None,
    ):
        self.config = config or WebhookConfig()
        self.on_signal = on_signal  # Callback when valid signal received
        # Health registry — defaults to the process-wide singleton so any
        # subsystem that imports `default_registry()` reports into the
        # same map this listener exposes via /health. Tests can pass a
        # fresh HealthRegistry to isolate state.
        self.health = health_registry or default_registry()
        self.signal_queue: asyncio.Queue = asyncio.Queue()
        self.signal_history: List[dict] = []
        self._request_timestamps: List[float] = []
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._is_running = False

        # Pre-register self so /health shows "webhook" before first signal.
        self.health.register("webhook")

        log.info(f"WebhookListener initialized | Port: {self.config.port} | "
                 f"HMAC Auth: {self.config.enable_hmac_auth} | "
                 f"Symbols: {self.config.allowed_symbols}")

    # ──────────────────────────────────────────
    # Server Lifecycle
    # ──────────────────────────────────────────

    async def start(self):
        """Start the webhook HTTP server."""
        self._app = web.Application()
        self._app.router.add_post("/webhook", self._handle_webhook)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/signals", self._handle_signals)
        self._app.router.add_get("/status", self._handle_status)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
        self._is_running = True
        # Mark ourselves healthy now that the listener is accepting connections.
        self.health.report(
            "webhook",
            HealthStatus.HEALTHY,
            detail=f"listening on {self.config.host}:{self.config.port}",
        )
        log.info(f"Webhook server started on http://{self.config.host}:{self.config.port}")
        log.info(f"  POST /webhook  — Receive TradingView signals")
        log.info(f"  GET  /health   — Health check (per-component status)")
        log.info(f"  GET  /signals  — Recent signal history")
        log.info(f"  GET  /status   — Server status")

    async def stop(self):
        """Gracefully stop the server."""
        if self._runner:
            await self._runner.cleanup()
        self._is_running = False
        log.info("Webhook server stopped.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ──────────────────────────────────────────
    # Request Handlers
    # ──────────────────────────────────────────

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook POST request from TradingView."""
        client_ip = request.remote
        log.info(f"Incoming webhook from {client_ip}")

        # Rate limiting
        if not self._check_rate_limit():
            log.warning(f"Rate limit exceeded from {client_ip}")
            return web.json_response(
                {"error": "Rate limit exceeded", "retry_after_seconds": 60},
                status=429,
            )

        # Authentication
        if self.config.enable_hmac_auth:
            if not self.config.secret_key:
                # Misconfiguration: HMAC requested but no secret set. Refuse to
                # serve rather than silently skip auth (which would expose the
                # webhook to anyone who finds the URL).
                log.error(
                    "Webhook HMAC enabled but secret_key is empty — refusing %s",
                    client_ip,
                )
                return web.json_response(
                    {"error": "Server misconfigured: webhook secret not set"},
                    status=503,
                )
            if not await self._authenticate(request):
                log.warning(f"Authentication failed from {client_ip}")
                return web.json_response(
                    {"error": "Unauthorized"},
                    status=401,
                )

        # Parse body. Catch JSON-specific errors with a 400 and treat any
        # other unexpected error as 500 (don't lump them together as 400).
        try:
            body = await request.text()
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON payload: {e}")
            return web.json_response(
                {"error": "Invalid JSON", "detail": str(e)},
                status=400,
            )
        except Exception as e:
            log.exception(f"Unexpected error reading webhook body: {e}")
            return web.json_response(
                {"error": "Bad request", "detail": str(e)},
                status=400,
            )

        # Reject non-object payloads (TV alerts must be JSON objects).
        if not isinstance(payload, dict):
            log.warning(f"Rejected non-object payload from {client_ip}: {type(payload).__name__}")
            return web.json_response(
                {"error": "Payload must be a JSON object"},
                status=400,
            )

        # Validate & create signal
        try:
            signal = self._validate_payload(payload)
        except ValueError as e:
            log.error(f"Validation failed: {e}")
            return web.json_response(
                {"error": "Validation failed", "detail": str(e)},
                status=422,
            )

        # Process signal
        log.info(f"✓ Valid signal: {signal.action.value} {signal.symbol} "
                 f"vol={signal.volume} SL={signal.sl_pips} TP={signal.tp_pips}")

        # Add to queue and history
        await self.signal_queue.put(signal)
        self.signal_history.append(signal.to_dict())
        if len(self.signal_history) > 100:
            self.signal_history = self.signal_history[-100:]

        # Callback
        if self.on_signal:
            try:
                if asyncio.iscoroutinefunction(self.on_signal):
                    await self.on_signal(signal)
                else:
                    self.on_signal(signal)
            except Exception as e:
                log.error(f"Signal callback error: {e}")

        return web.json_response({
            "status": "accepted",
            "signal": signal.to_dict(),
            "queue_size": self.signal_queue.qsize(),
        }, status=200)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint.

        Returns the registry snapshot so external monitors (uptime checks,
        Telegram alert daemons, k8s probes) can see *which* subsystem is
        unhealthy rather than just a binary up/down. HTTP status is set
        to 200 for healthy/unknown and 503 for degraded/unhealthy so
        load balancers can route around a sick instance.
        """
        registry_snapshot = self.health.snapshot()
        overall = registry_snapshot["status"]
        http_status = 200 if overall in ("healthy", "unknown") else 503
        body = {
            "status": overall,
            "service": "chastiefol-webhook",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime": self._is_running,
            "queue_size": self.signal_queue.qsize(),
            "signals_received": len(self.signal_history),
            "components": registry_snapshot["components"],
        }
        return web.json_response(body, status=http_status)

    async def _handle_signals(self, request: web.Request) -> web.Response:
        """Return recent signal history."""
        # Coerce ?limit=... defensively — non-numeric values would otherwise
        # raise ValueError and produce an opaque 500.
        try:
            limit = int(request.query.get("limit", 20))
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "Query param 'limit' must be an integer"},
                status=400,
            )
        limit = max(1, min(limit, 1000))  # clamp to a sensible range
        return web.json_response({
            "signals": self.signal_history[-limit:],
            "total": len(self.signal_history),
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Return server status and config (non-sensitive)."""
        return web.json_response({
            "running": self._is_running,
            "port": self.config.port,
            "allowed_symbols": self.config.allowed_symbols,
            "rate_limit": self.config.rate_limit_per_minute,
            "hmac_enabled": self.config.enable_hmac_auth,
            "queue_size": self.signal_queue.qsize(),
            "signals_total": len(self.signal_history),
        })

    # ──────────────────────────────────────────
    # Validation & Auth
    # ──────────────────────────────────────────

    def _validate_payload(self, payload: dict) -> WebhookSignal:
        """Validate and parse the webhook payload.

        All field-level errors are surfaced as ``ValueError`` so the caller
        can return a 422. We never let raw ``AttributeError`` / ``TypeError``
        from bad upstream payloads escape this method.
        """
        # Required fields
        required_fields = ["action", "symbol"]
        for field_name in required_fields:
            if field_name not in payload:
                raise ValueError(f"Missing required field: '{field_name}'")

        # Action validation — coerce to str defensively (TV may send numbers).
        raw_action = payload["action"]
        if not isinstance(raw_action, str):
            raise ValueError(
                f"Field 'action' must be a string, got {type(raw_action).__name__}"
            )
        action_str = raw_action.upper().strip()
        try:
            action = TradeAction(action_str)
        except ValueError:
            raise ValueError(
                f"Invalid action '{action_str}'. Must be one of: BUY, SELL, CLOSE"
            )

        # Symbol validation
        raw_symbol = payload["symbol"]
        if not isinstance(raw_symbol, str):
            raise ValueError(
                f"Field 'symbol' must be a string, got {type(raw_symbol).__name__}"
            )
        symbol = raw_symbol.upper().strip()
        if symbol not in self.config.allowed_symbols:
            raise ValueError(
                f"Symbol '{symbol}' not allowed. Allowed: {self.config.allowed_symbols}"
            )

        # Volume validation (optional for CLOSE action)
        volume = self._coerce_float(payload.get("volume", 1.0), "volume")
        if action != TradeAction.CLOSE:
            if volume < self.config.min_volume:
                raise ValueError(
                    f"Volume {volume} below minimum {self.config.min_volume}"
                )
            if volume > self.config.max_volume:
                raise ValueError(
                    f"Volume {volume} exceeds maximum {self.config.max_volume}"
                )

        # SL/TP validation
        sl_pips = self._coerce_float(payload.get("sl_pips", 0), "sl_pips")
        tp_pips = self._coerce_float(payload.get("tp_pips", 0), "tp_pips")
        if action != TradeAction.CLOSE:
            if sl_pips <= 0:
                raise ValueError("sl_pips must be positive for BUY/SELL orders")
            if tp_pips <= 0:
                raise ValueError("tp_pips must be positive for BUY/SELL orders")

        # Comment must be a string; reject huge values to bound log size.
        raw_comment = payload.get("comment", "TradingView_Signal")
        if not isinstance(raw_comment, str):
            raw_comment = str(raw_comment)
        comment = raw_comment[:256]

        return WebhookSignal(
            action=action,
            symbol=symbol,
            volume=volume,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            comment=comment,
            raw_payload=payload,
        )

    @staticmethod
    def _coerce_float(value, field_name: str) -> float:
        """Coerce a numeric/string value to float, raising ValueError on failure.

        Centralizes the conversion so all numeric fields produce the same
        validation error shape regardless of input type (int, str, bool, None).
        """
        # Reject bool early — bool is a subclass of int in Python, but a TV
        # alert sending `true`/`false` for a numeric field is almost certainly
        # a configuration mistake.
        if isinstance(value, bool):
            raise ValueError(f"Field '{field_name}' must be numeric, got bool")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Field '{field_name}' must be numeric, got {value!r}"
            )

    async def _authenticate(self, request: web.Request) -> bool:
        """
        Authenticate the request using HMAC-SHA256.
        TradingView sends the secret in X-Webhook-Secret header,
        or we compute HMAC of the body.
        """
        # Method 1: Direct secret comparison (simpler, for TradingView)
        header_secret = request.headers.get("X-Webhook-Secret", "")
        if header_secret and hmac.compare_digest(header_secret, self.config.secret_key):
            return True

        # Method 2: HMAC-SHA256 signature verification
        signature = request.headers.get("X-Signature", "")
        if signature:
            body = await request.read()
            expected = hmac.new(
                self.config.secret_key.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(signature, expected)

        # Method 3: Query parameter (fallback, less secure)
        query_secret = request.query.get("secret", "")
        if query_secret and hmac.compare_digest(query_secret, self.config.secret_key):
            return True

        return False

    def _check_rate_limit(self) -> bool:
        """Simple sliding-window rate limiter."""
        import time as _time
        now = _time.time()
        window = 60.0  # 1 minute

        # Remove old timestamps
        self._request_timestamps = [
            ts for ts in self._request_timestamps if now - ts < window
        ]

        if len(self._request_timestamps) >= self.config.rate_limit_per_minute:
            return False

        self._request_timestamps.append(now)
        return True

    # ──────────────────────────────────────────
    # Signal Consumer
    # ──────────────────────────────────────────

    async def consume_signals(self):
        """
        Async generator to consume signals from the queue.
        Usage:
            async for signal in webhook.consume_signals():
                process(signal)
        """
        while True:
            signal = await self.signal_queue.get()
            yield signal
            self.signal_queue.task_done()

    async def get_next_signal(self, timeout: float = None) -> Optional[WebhookSignal]:
        """Get the next signal from queue with optional timeout."""
        try:
            if timeout:
                signal = await asyncio.wait_for(
                    self.signal_queue.get(), timeout=timeout
                )
            else:
                signal = await self.signal_queue.get()
            self.signal_queue.task_done()
            return signal
        except asyncio.TimeoutError:
            return None


# ──────────────────────────────────────────────
# Standalone Runner
# ──────────────────────────────────────────────

async def main():
    """Run webhook listener standalone for testing."""
    import os

    config = WebhookConfig(
        host="0.0.0.0",
        port=int(os.environ.get("WEBHOOK_PORT", 8080)),
        secret_key=os.environ.get("WEBHOOK_SECRET", "chastiefol_secret_key"),
        allowed_symbols=["XAUUSD"],
        rate_limit_per_minute=10,
        enable_hmac_auth=bool(os.environ.get("WEBHOOK_HMAC_ENABLED", "true").lower() == "true"),
    )

    def on_signal(signal: WebhookSignal):
        log.info(f"📨 Signal received: {signal.action.value} {signal.symbol} "
                 f"vol={signal.volume}")

    listener = WebhookListener(config=config, on_signal=on_signal)
    await listener.start()

    log.info("Webhook listener running. Press Ctrl+C to stop.")
    log.info(f"Test with: curl -X POST http://localhost:{config.port}/webhook "
             f'-H "Content-Type: application/json" '
             f'-H "X-Webhook-Secret: {config.secret_key}" '
             f'-d \'{{"action":"BUY","symbol":"XAUUSD","volume":0.01,"sl_pips":50,"tp_pips":150}}\'')

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
