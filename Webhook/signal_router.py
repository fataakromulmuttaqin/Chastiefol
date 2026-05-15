"""
Chastiefol — Signal Router (Dual-Source Deduplication & Routing)
Aggregates signals from multiple sources with deduplication to ensure
each TradingView alert is only executed ONCE even if received by both
Email Parser and Soranoo Bridge simultaneously.

Architecture:
    ┌───────────────────────┐     ┌───────────────────────┐
    │  Email Alert Parser   │     │   Soranoo Bridge      │
    │  (Primary — IMAP)     │     │   (Fallback — HTTP)   │
    └──────────┬────────────┘     └──────────┬────────────┘
               │                              │
               ▼                              ▼
    ┌─────────────────────────────────────────────────────┐
    │              SIGNAL ROUTER                           │
    │                                                     │
    │  1. Receive signal from any source                  │
    │  2. Generate dedup fingerprint (action+symbol+time) │
    │  3. Check if fingerprint seen in last N seconds     │
    │  4. If NEW → forward to execution pipeline          │
    │  5. If DUPLICATE → log & discard                    │
    │                                                     │
    │  Priority: Email > Soranoo > Direct Webhook         │
    └──────────────────────┬──────────────────────────────┘
                           │
                           ▼
    ┌─────────────────────────────────────────────────────┐
    │         Chastiefol Execution Pipeline                │
    │   (Risk Check → Position Size → cTrader → Notify)   │
    └─────────────────────────────────────────────────────┘

Deduplication Strategy:
- Fingerprint = hash(action + symbol + rounded_time_window)
- Time window: 60 seconds (configurable)
- If same fingerprint arrives within window → duplicate → discard
- First signal wins (whichever source delivers first)
"""

import hashlib
import logging
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any
from enum import Enum
from collections import OrderedDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("SignalRouter")


# ──────────────────────────────────────────────
# Configuration & Models
# ──────────────────────────────────────────────

class SignalSource(str, Enum):
    EMAIL_PARSER = "email_parser"
    SORANOO_BRIDGE = "soranoo_bridge"
    DIRECT_WEBHOOK = "direct_webhook"
    AUTONOMOUS = "autonomous"


class SignalStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class RouterConfig:
    """Signal router configuration."""
    # Deduplication
    dedup_window_seconds: int = 60      # Signals within this window = duplicate
    max_fingerprint_cache: int = 500    # Max cached fingerprints before cleanup
    fingerprint_ttl_seconds: int = 300  # Remove fingerprints older than 5 min

    # Source priority (lower = higher priority)
    source_priority: Dict[str, int] = field(default_factory=lambda: {
        SignalSource.DIRECT_WEBHOOK.value: 1,
        SignalSource.EMAIL_PARSER.value: 2,
        SignalSource.SORANOO_BRIDGE.value: 3,
        SignalSource.AUTONOMOUS.value: 4,
    })

    # Validation
    allowed_actions: List[str] = field(default_factory=lambda: ["BUY", "SELL", "CLOSE"])
    allowed_symbols: List[str] = field(default_factory=lambda: ["XAUUSD"])
    max_signal_age_seconds: int = 300   # Reject signals older than 5 min

    # Failover
    enable_failover_logging: bool = True
    alert_on_source_down: bool = True
    source_timeout_seconds: int = 120   # Mark source as "down" if no signal for 2 min


@dataclass
class RoutedSignal:
    """A signal that has passed through the router."""
    # Signal data
    action: str = ""
    symbol: str = "XAUUSD"
    volume: float = 0.01
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    entry: float = 0.0
    confluence: int = 0
    comment: str = ""

    # Routing metadata
    source: SignalSource = SignalSource.EMAIL_PARSER
    fingerprint: str = ""
    status: SignalStatus = SignalStatus.ACCEPTED
    received_at: float = 0.0
    routed_at: float = 0.0
    duplicate_of: str = ""  # Fingerprint of original if duplicate

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "volume": self.volume,
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "entry": self.entry,
            "confluence": self.confluence,
            "comment": self.comment,
            "source": self.source.value,
            "fingerprint": self.fingerprint,
            "status": self.status.value,
            "received_at": self.received_at,
        }


@dataclass
class SourceHealth:
    """Health tracking for a signal source."""
    source: SignalSource
    last_signal_time: float = 0.0
    total_signals: int = 0
    duplicates_caught: int = 0
    errors: int = 0
    is_healthy: bool = True

    @property
    def seconds_since_last(self) -> float:
        if self.last_signal_time == 0:
            return -1  # Never received
        return time.time() - self.last_signal_time


# ──────────────────────────────────────────────
# Signal Router
# ──────────────────────────────────────────────

class SignalRouter:
    """
    Central signal router with deduplication for multiple signal sources.

    Ensures that the same TradingView alert (received via Email AND Soranoo)
    is only executed once. First signal to arrive wins.

    Usage:
        router = SignalRouter(RouterConfig())

        # Set execution callback
        router.on_execute = my_execution_handler

        # Feed signals from different sources
        await router.route_signal(payload_from_email, source=SignalSource.EMAIL_PARSER)
        await router.route_signal(payload_from_soranoo, source=SignalSource.SORANOO_BRIDGE)

        # The same signal from both sources → only executed once
    """

    def __init__(self, config: RouterConfig = None):
        self.config = config or RouterConfig()

        # Execution callback
        self.on_execute: Optional[Callable] = None

        # Dedup cache: fingerprint → (timestamp, source, signal_data)
        self._fingerprint_cache: OrderedDict = OrderedDict()

        # Source health tracking
        self._source_health: Dict[str, SourceHealth] = {
            source.value: SourceHealth(source=source)
            for source in SignalSource
        }

        # Statistics
        self._total_received = 0
        self._total_executed = 0
        self._total_duplicates = 0
        self._total_rejected = 0

        # Signal history (last N)
        self._signal_history: List[dict] = []
        self._max_history = 100

        log.info(f"SignalRouter initialized | "
                 f"Dedup window: {self.config.dedup_window_seconds}s | "
                 f"Sources: {list(self.config.source_priority.keys())}")

    # ──────────────────────────────────────────
    # Main Routing
    # ──────────────────────────────────────────

    async def route_signal(self, payload: dict, source: SignalSource) -> RoutedSignal:
        """
        Route a signal through deduplication and validation.

        Args:
            payload: Signal data dict with action, symbol, etc.
            source: Which source this signal came from

        Returns:
            RoutedSignal with status (ACCEPTED, DUPLICATE, REJECTED)
        """
        self._total_received += 1
        now = time.time()

        # Build routed signal
        signal = RoutedSignal(
            action=str(payload.get("action", "")).upper().strip(),
            symbol=str(payload.get("symbol", "XAUUSD")).upper().strip(),
            volume=float(payload.get("volume", 0.01)),
            sl_pips=float(payload.get("sl_pips", 0)),
            tp_pips=float(payload.get("tp_pips", 0)),
            entry=float(payload.get("entry", 0)),
            confluence=int(payload.get("confluence", 0)),
            comment=str(payload.get("comment", "")),
            source=source,
            received_at=now,
        )

        # Validate
        if not self._validate(signal):
            signal.status = SignalStatus.REJECTED
            self._total_rejected += 1
            self._record_signal(signal)
            log.warning(f"[Router] REJECTED from {source.value}: "
                        f"invalid action={signal.action} symbol={signal.symbol}")
            return signal

        # Generate fingerprint
        fingerprint = self._generate_fingerprint(signal)
        signal.fingerprint = fingerprint

        # Check dedup
        existing = self._fingerprint_cache.get(fingerprint)
        if existing:
            existing_time, existing_source, _ = existing
            age = now - existing_time
            if age < self.config.dedup_window_seconds:
                # DUPLICATE
                signal.status = SignalStatus.DUPLICATE
                signal.duplicate_of = fingerprint
                self._total_duplicates += 1
                self._update_source_health(source, duplicate=True)
                self._record_signal(signal)
                log.info(f"[Router] DUPLICATE from {source.value} "
                         f"(original: {existing_source} {age:.1f}s ago) | "
                         f"{signal.action} {signal.symbol}")
                return signal

        # NEW SIGNAL — Accept and execute
        signal.status = SignalStatus.ACCEPTED
        signal.routed_at = now
        self._fingerprint_cache[fingerprint] = (now, source.value, signal.to_dict())
        self._total_executed += 1
        self._update_source_health(source, duplicate=False)
        self._record_signal(signal)
        self._cleanup_cache()

        log.info(f"[Router] ✓ ACCEPTED from {source.value} | "
                 f"{signal.action} {signal.symbol} | "
                 f"Fingerprint: {fingerprint[:12]}...")

        # Execute
        if self.on_execute:
            try:
                if asyncio.iscoroutinefunction(self.on_execute):
                    await self.on_execute(signal)
                else:
                    self.on_execute(signal)
            except Exception as e:
                log.error(f"[Router] Execution callback error: {e}")

        return signal

    # ──────────────────────────────────────────
    # Deduplication
    # ──────────────────────────────────────────

    def _generate_fingerprint(self, signal: RoutedSignal) -> str:
        """
        Generate a dedup fingerprint for a signal.
        Same action + symbol within the time window = same fingerprint.

        Fingerprint = SHA256(action + symbol + time_bucket)
        Time bucket = floor(timestamp / window_seconds)
        """
        time_bucket = int(signal.received_at / self.config.dedup_window_seconds)
        raw = f"{signal.action}|{signal.symbol}|{time_bucket}"

        # Include entry price if available (more precise dedup)
        if signal.entry > 0:
            # Round entry to nearest 0.5 to handle slight price differences
            rounded_entry = round(signal.entry * 2) / 2
            raw += f"|{rounded_entry}"

        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _cleanup_cache(self):
        """Remove expired fingerprints from cache."""
        now = time.time()
        ttl = self.config.fingerprint_ttl_seconds

        # Remove old entries
        expired = []
        for fp, (ts, _, _) in self._fingerprint_cache.items():
            if now - ts > ttl:
                expired.append(fp)
            else:
                break  # OrderedDict — older entries first

        for fp in expired:
            del self._fingerprint_cache[fp]

        # Hard limit
        while len(self._fingerprint_cache) > self.config.max_fingerprint_cache:
            self._fingerprint_cache.popitem(last=False)

    # ──────────────────────────────────────────
    # Validation
    # ──────────────────────────────────────────

    def _validate(self, signal: RoutedSignal) -> bool:
        """Validate signal data."""
        if signal.action not in self.config.allowed_actions:
            return False
        if signal.symbol not in self.config.allowed_symbols:
            return False
        return True

    # ──────────────────────────────────────────
    # Source Health Tracking
    # ──────────────────────────────────────────

    def _update_source_health(self, source: SignalSource, duplicate: bool = False):
        """Update health metrics for a source."""
        health = self._source_health.get(source.value)
        if health:
            health.last_signal_time = time.time()
            health.total_signals += 1
            if duplicate:
                health.duplicates_caught += 1
            health.is_healthy = True

    def get_source_health(self) -> Dict[str, dict]:
        """Get health status of all sources."""
        result = {}
        for name, health in self._source_health.items():
            is_down = (
                health.last_signal_time > 0 and
                health.seconds_since_last > self.config.source_timeout_seconds
            )
            result[name] = {
                "healthy": health.is_healthy and not is_down,
                "total_signals": health.total_signals,
                "duplicates_caught": health.duplicates_caught,
                "seconds_since_last": round(health.seconds_since_last, 1),
                "errors": health.errors,
            }
        return result

    def is_source_active(self, source: SignalSource) -> bool:
        """Check if a source has sent signals recently."""
        health = self._source_health.get(source.value)
        if not health or health.last_signal_time == 0:
            return False
        return health.seconds_since_last < self.config.source_timeout_seconds

    # ──────────────────────────────────────────
    # History & Stats
    # ──────────────────────────────────────────

    def _record_signal(self, signal: RoutedSignal):
        """Record signal in history."""
        entry = signal.to_dict()
        entry["status"] = signal.status.value
        self._signal_history.append(entry)
        if len(self._signal_history) > self._max_history:
            self._signal_history = self._signal_history[-self._max_history:]

    def get_stats(self) -> dict:
        """Get router statistics."""
        return {
            "total_received": self._total_received,
            "total_executed": self._total_executed,
            "total_duplicates": self._total_duplicates,
            "total_rejected": self._total_rejected,
            "dedup_rate": (
                round(self._total_duplicates / max(self._total_received, 1) * 100, 1)
            ),
            "cache_size": len(self._fingerprint_cache),
            "source_health": self.get_source_health(),
        }

    def get_recent_signals(self, limit: int = 20) -> List[dict]:
        """Get recent signal history."""
        return self._signal_history[-limit:]

    # ──────────────────────────────────────────
    # Integration Helper
    # ──────────────────────────────────────────

    def create_email_callback(self):
        """
        Create a callback function compatible with EmailAlertParser.on_signal.
        Automatically routes email signals through this router.
        """
        async def _email_callback(parsed_signal):
            payload = parsed_signal.to_webhook_payload()
            await self.route_signal(payload, source=SignalSource.EMAIL_PARSER)
        return _email_callback

    def create_soranoo_callback(self):
        """
        Create a callback function compatible with SoranooBridge.on_signal.
        Automatically routes soranoo signals through this router.
        """
        async def _soranoo_callback(soranoo_signal):
            payload = soranoo_signal.to_webhook_payload()
            await self.route_signal(payload, source=SignalSource.SORANOO_BRIDGE)
        return _soranoo_callback

    def create_webhook_callback(self):
        """
        Create a callback for the main webhook listener (direct webhook).
        Used when TradingView Premium sends directly.
        """
        async def _webhook_callback(webhook_signal):
            payload = webhook_signal.to_dict()
            await self.route_signal(payload, source=SignalSource.DIRECT_WEBHOOK)
        return _webhook_callback


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    async def test():
        config = RouterConfig(dedup_window_seconds=60)
        router = SignalRouter(config)

        executed_signals = []

        async def mock_execute(signal: RoutedSignal):
            executed_signals.append(signal)
            print(f"  EXECUTED: {signal.action} {signal.symbol} from {signal.source.value}")

        router.on_execute = mock_execute

        print(f"\n{'='*55}")
        print(f"  SIGNAL ROUTER — DEDUPLICATION TEST")
        print(f"{'='*55}\n")

        # Signal 1: From email parser
        payload = {"action": "BUY", "symbol": "XAUUSD", "sl_pips": 150, "tp_pips": 300, "entry": 2365.5}

        print("  [1] Email sends BUY XAUUSD...")
        r1 = await router.route_signal(payload, SignalSource.EMAIL_PARSER)
        print(f"      Result: {r1.status.value}")

        # Signal 2: Same signal from Soranoo (duplicate)
        print("  [2] Soranoo sends same BUY XAUUSD...")
        r2 = await router.route_signal(payload, SignalSource.SORANOO_BRIDGE)
        print(f"      Result: {r2.status.value}")

        # Signal 3: Different signal (SELL)
        payload_sell = {"action": "SELL", "symbol": "XAUUSD", "sl_pips": 120, "tp_pips": 240}
        print("  [3] Email sends SELL XAUUSD...")
        r3 = await router.route_signal(payload_sell, SignalSource.EMAIL_PARSER)
        print(f"      Result: {r3.status.value}")

        # Signal 4: Invalid
        payload_bad = {"action": "HOLD", "symbol": "BTCUSD"}
        print("  [4] Invalid signal (HOLD BTCUSD)...")
        r4 = await router.route_signal(payload_bad, SignalSource.SORANOO_BRIDGE)
        print(f"      Result: {r4.status.value}")

        # Stats
        stats = router.get_stats()
        print(f"\n  Stats:")
        print(f"    Received: {stats['total_received']}")
        print(f"    Executed: {stats['total_executed']}")
        print(f"    Duplicates: {stats['total_duplicates']}")
        print(f"    Rejected: {stats['total_rejected']}")
        print(f"    Dedup rate: {stats['dedup_rate']}%")
        print(f"\n    Signals actually executed: {len(executed_signals)}")

        assert len(executed_signals) == 2, f"Expected 2, got {len(executed_signals)}"
        assert r1.status == SignalStatus.ACCEPTED
        assert r2.status == SignalStatus.DUPLICATE
        assert r3.status == SignalStatus.ACCEPTED
        assert r4.status == SignalStatus.REJECTED

        print(f"\n  ✓ All deduplication tests passed!")
        print(f"{'='*55}\n")

    asyncio.run(test())
