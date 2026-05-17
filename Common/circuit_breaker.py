"""
Asyncio circuit breaker for external service calls.

Why this exists
---------------
Chastiefol talks to several flaky externals (Telegram, cTrader MCP/FIX,
LLM providers, market data APIs). When one of them is fully down, naive
retry loops burn CPU + tokens + rate-limit budget for nothing. A circuit
breaker fails fast once the service is provably broken, then probes it
periodically to detect recovery.

States
------
- CLOSED: normal operation. Failures increment a counter.
- OPEN:   service is presumed down. Calls raise CircuitBreakerOpen
          immediately without hitting the network. After
          ``recovery_timeout`` seconds we transition to HALF_OPEN.
- HALF_OPEN: a single probe is allowed through. Success closes the
             circuit; failure re-opens it for another full timeout.

Usage
-----

    breaker = CircuitBreaker("telegram", failure_threshold=5, recovery_timeout=30)
    try:
        result = await breaker.call(send_message, payload)
    except CircuitBreakerOpen:
        # service is down — degrade gracefully
        ...

Thread/task safety
------------------
This implementation uses an asyncio.Lock so concurrent tasks can't both
flip the state. It is NOT safe across processes — each process has its
own state, which is fine for our single-process trading agent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Tuple, Type

log = logging.getLogger("Common.CircuitBreaker")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the breaker is OPEN."""

    def __init__(self, name: str, opened_at: float, retry_after: float):
        self.name = name
        self.opened_at = opened_at
        self.retry_after = retry_after
        super().__init__(
            f"Circuit '{name}' is OPEN; retry in ~{retry_after:.1f}s"
        )


class CircuitBreaker:
    """Async circuit breaker with configurable failure threshold and timeout.

    Args:
        name: Identifier used in logs and exception messages.
        failure_threshold: Consecutive failures that trip the breaker.
            Must be >= 1.
        recovery_timeout: Seconds the breaker stays OPEN before allowing
            a single HALF_OPEN probe.
        expected_exceptions: Tuple of exception types that count as
            "failures". Other exceptions propagate without affecting
            state — useful so e.g. ``CancelledError`` or our own
            ``ValueError`` from input validation don't trip the breaker.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        expected_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    # ── Public state accessors ────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "opened_at": self._opened_at if self._state != CircuitState.CLOSED else None,
        }

    # ── Manual control (mostly for tests) ────────────────────────

    async def reset(self) -> None:
        async with self._lock:
            if self._state != CircuitState.CLOSED:
                log.info("Circuit '%s' manually reset (was %s)", self.name, self._state.value)
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = 0.0

    # ── Decision helpers ─────────────────────────────────────────

    async def _before_call(self) -> None:
        """Decide whether to admit the next call. Raises if OPEN."""
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return

            now = time.monotonic()
            elapsed = now - self._opened_at
            if self._state == CircuitState.OPEN:
                if elapsed >= self.recovery_timeout:
                    # Promote to HALF_OPEN: allow ONE probe.
                    self._state = CircuitState.HALF_OPEN
                    log.info(
                        "Circuit '%s' transitioning OPEN -> HALF_OPEN after %.1fs",
                        self.name, elapsed,
                    )
                    return
                # Still cooling down — fail fast.
                raise CircuitBreakerOpen(
                    self.name,
                    self._opened_at,
                    max(0.0, self.recovery_timeout - elapsed),
                )

            # HALF_OPEN: only one probe in flight at a time. We don't
            # serialize concurrent callers here for simplicity — the first
            # to flip state to CLOSED or back to OPEN wins, and the cost
            # of a second probe is acceptable.
            return

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                log.info(
                    "Circuit '%s' recovered: HALF_OPEN -> CLOSED",
                    self.name,
                )
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = 0.0

    async def _on_failure(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — back to OPEN for another full timeout.
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                log.warning(
                    "Circuit '%s' probe failed: HALF_OPEN -> OPEN (retry in %.1fs)",
                    self.name, self.recovery_timeout,
                )
                return

            self._failure_count += 1
            if (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                log.warning(
                    "Circuit '%s' tripped: %d consecutive failures (retry in %.1fs)",
                    self.name, self._failure_count, self.recovery_timeout,
                )

    # ── Main entry point ─────────────────────────────────────────

    async def call(
        self,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Invoke ``func(*args, **kwargs)`` through the breaker.

        Re-raises whatever ``func`` raises so callers can keep their
        existing error-handling. Counts only ``expected_exceptions``
        toward the failure threshold.
        """
        await self._before_call()
        try:
            result = await func(*args, **kwargs)
        except self.expected_exceptions:
            await self._on_failure()
            raise
        else:
            await self._on_success()
            return result

    # Convenience: mark success/failure without going through call()
    async def record_success(self) -> None:
        """Manually record a successful interaction (e.g. when the call
        path can't easily route through ``call()``)."""
        await self._on_success()

    async def record_failure(self) -> None:
        """Manually record a failed interaction."""
        await self._on_failure()
