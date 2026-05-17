"""
Unit tests for Common.circuit_breaker.

Covers the state machine end-to-end:
  - CLOSED stays closed under success
  - failure_threshold consecutive failures -> OPEN
  - OPEN fails fast with CircuitBreakerOpen
  - after recovery_timeout -> HALF_OPEN allows one probe
  - HALF_OPEN success -> CLOSED, failure -> OPEN again
  - expected_exceptions filter: untracked exceptions don't trip the breaker
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Common.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)


def run_async(coro):
    """unittest <3.11 compatibility helper."""
    return asyncio.get_event_loop().run_until_complete(coro)


class CircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        # Fresh event loop per test so state from one test never leaks
        # into another (CircuitBreaker uses asyncio.Lock).
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    # ── Construction ──────────────────────────────────────────────

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            CircuitBreaker("x", failure_threshold=0, recovery_timeout=1.0)

    def test_invalid_recovery_rejected(self):
        with self.assertRaises(ValueError):
            CircuitBreaker("x", failure_threshold=1, recovery_timeout=0)

    # ── State machine ─────────────────────────────────────────────

    def test_closed_stays_closed_on_success(self):
        async def scenario():
            cb = CircuitBreaker("svc", failure_threshold=3, recovery_timeout=10)
            for _ in range(5):
                self.assertEqual(await cb.call(self._ok), "ok")
            self.assertEqual(cb.state, CircuitState.CLOSED)
            self.assertEqual(cb.failure_count, 0)

        self.loop.run_until_complete(scenario())

    def test_trips_after_threshold_failures(self):
        async def scenario():
            cb = CircuitBreaker("svc", failure_threshold=3, recovery_timeout=10)
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    await cb.call(self._boom)
            self.assertEqual(cb.state, CircuitState.OPEN)
            # Next call short-circuits without invoking the function.
            with self.assertRaises(CircuitBreakerOpen):
                await cb.call(self._ok)

        self.loop.run_until_complete(scenario())

    def test_recovers_via_half_open(self):
        async def scenario():
            cb = CircuitBreaker("svc", failure_threshold=2, recovery_timeout=0.05)
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    await cb.call(self._boom)
            self.assertEqual(cb.state, CircuitState.OPEN)

            # Wait past the cooldown so the next call promotes to HALF_OPEN.
            await asyncio.sleep(0.06)

            # Successful probe should close the circuit.
            self.assertEqual(await cb.call(self._ok), "ok")
            self.assertEqual(cb.state, CircuitState.CLOSED)
            self.assertEqual(cb.failure_count, 0)

        self.loop.run_until_complete(scenario())

    def test_half_open_failure_reopens(self):
        async def scenario():
            cb = CircuitBreaker("svc", failure_threshold=2, recovery_timeout=0.05)
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    await cb.call(self._boom)

            await asyncio.sleep(0.06)

            # Probe fails -> back to OPEN.
            with self.assertRaises(RuntimeError):
                await cb.call(self._boom)
            self.assertEqual(cb.state, CircuitState.OPEN)

            # And we should fail fast again immediately.
            with self.assertRaises(CircuitBreakerOpen):
                await cb.call(self._ok)

        self.loop.run_until_complete(scenario())

    # ── Exception filtering ───────────────────────────────────────

    def test_untracked_exception_does_not_trip(self):
        async def scenario():
            cb = CircuitBreaker(
                "svc",
                failure_threshold=2,
                recovery_timeout=10,
                expected_exceptions=(RuntimeError,),
            )

            # ValueError isn't in expected_exceptions -> not counted.
            for _ in range(5):
                with self.assertRaises(ValueError):
                    await cb.call(self._value_error)
            self.assertEqual(cb.state, CircuitState.CLOSED)
            self.assertEqual(cb.failure_count, 0)

            # RuntimeError IS counted.
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    await cb.call(self._boom)
            self.assertEqual(cb.state, CircuitState.OPEN)

        self.loop.run_until_complete(scenario())

    def test_reset_returns_to_closed(self):
        async def scenario():
            cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=10)
            with self.assertRaises(RuntimeError):
                await cb.call(self._boom)
            self.assertEqual(cb.state, CircuitState.OPEN)

            await cb.reset()
            self.assertEqual(cb.state, CircuitState.CLOSED)
            self.assertEqual(cb.failure_count, 0)
            self.assertEqual(await cb.call(self._ok), "ok")

        self.loop.run_until_complete(scenario())

    def test_stats_payload(self):
        async def scenario():
            cb = CircuitBreaker("svc", failure_threshold=2, recovery_timeout=5)
            stats = cb.stats()
            self.assertEqual(stats["state"], "closed")
            self.assertEqual(stats["failure_count"], 0)
            self.assertIsNone(stats["opened_at"])

        self.loop.run_until_complete(scenario())

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    async def _ok():
        return "ok"

    @staticmethod
    async def _boom():
        raise RuntimeError("synthetic failure")

    @staticmethod
    async def _value_error():
        raise ValueError("not in expected_exceptions")


if __name__ == "__main__":
    unittest.main()
