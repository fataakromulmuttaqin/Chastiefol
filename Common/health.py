"""
Lightweight health registry for Chastiefol subsystems.

Why this exists
---------------
Chastiefol orchestrates several long-running subsystems (data feed, LLM,
broker connection, telegram). When something silently degrades — e.g. the
TradingView WebSocket reconnect loop is stuck, the LLM circuit breaker
tripped, FIX trade session lost auth — the trading loop continues but
decisions are made on stale/missing data.

The HealthRegistry lets each subsystem publish its own status on a
known schedule, and consolidates them into a single ``/health`` payload
that the webhook server (and external monitors / Telegram alerts) can
consume.

Design notes
------------
- Pull, not push: subsystems update their own ``ComponentHealth`` and
  the registry just reads the latest snapshot. No callbacks, no locks
  on the hot path.
- Staleness is a signal: every status carries ``updated_at``. If the
  webhook hasn't heard from a component in N seconds, it's reported as
  ``stale`` regardless of the component's last self-reported state.
- No external deps: stdlib only so this file works in CI without aiohttp.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"   # working but with reduced capability (e.g. stale cache, breaker half_open)
    UNHEALTHY = "unhealthy"  # not working at all
    UNKNOWN = "unknown"      # never reported

    @classmethod
    def worst(cls, statuses) -> "HealthStatus":
        """Aggregate: a system is only as healthy as its weakest link."""
        order = [cls.HEALTHY, cls.UNKNOWN, cls.DEGRADED, cls.UNHEALTHY]
        worst = cls.HEALTHY
        for s in statuses:
            if order.index(s) > order.index(worst):
                worst = s
        return worst


@dataclass
class ComponentHealth:
    """One subsystem's last-reported state."""
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    detail: str = ""
    updated_at: float = field(default_factory=time.time)
    extra: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        age = time.time() - self.updated_at
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "age_seconds": round(age, 1),
            **self.extra,
        }


class HealthRegistry:
    """Process-wide registry. Single instance lives in webhook_listener."""

    def __init__(self, stale_after_seconds: float = 120.0):
        # If a component hasn't reported in this many seconds, we
        # downgrade its status to "stale" regardless of last value.
        self.stale_after_seconds = stale_after_seconds
        self._components: Dict[str, ComponentHealth] = {}

    def register(self, name: str) -> None:
        """Pre-register a component so it shows up in /health even before
        its first update — makes missing subsystems obvious."""
        if name not in self._components:
            self._components[name] = ComponentHealth(name=name)

    def report(
        self,
        name: str,
        status: HealthStatus,
        detail: str = "",
        **extra,
    ) -> None:
        """Update a component's health snapshot."""
        self._components[name] = ComponentHealth(
            name=name,
            status=status,
            detail=detail,
            updated_at=time.time(),
            extra=extra,
        )

    def get(self, name: str) -> Optional[ComponentHealth]:
        return self._components.get(name)

    def overall(self) -> HealthStatus:
        if not self._components:
            return HealthStatus.UNKNOWN
        statuses = []
        now = time.time()
        for c in self._components.values():
            if now - c.updated_at > self.stale_after_seconds:
                statuses.append(HealthStatus.DEGRADED)  # stale = degraded, not down
            else:
                statuses.append(c.status)
        return HealthStatus.worst(statuses)

    def snapshot(self) -> Dict:
        """Full /health payload."""
        components = []
        now = time.time()
        for c in self._components.values():
            entry = c.to_dict()
            if now - c.updated_at > self.stale_after_seconds:
                entry["stale"] = True
            components.append(entry)
        return {
            "status": self.overall().value,
            "components": components,
        }


# Module-level singleton — convenient for cross-module imports without
# threading a registry through every constructor. Subsystems can also
# create their own HealthRegistry for tests.
_default_registry = HealthRegistry()


def default_registry() -> HealthRegistry:
    return _default_registry
