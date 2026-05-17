"""
Unit tests for SignalRouter — the deduplication layer that prevents
double-execution when the same trade signal arrives via multiple sources
(direct webhook + email parser + Soranoo bridge).

The risk if this module misbehaves is *executing the same trade twice*,
so we test the dedup logic carefully:

  - identical payload from two sources within the dedup window -> one
    accepted, one marked DUPLICATE
  - identical payload OUTSIDE the window -> both accepted
  - different actions on same symbol -> both accepted
  - different entry prices on same action+symbol -> both accepted
  - validation rejects bad action / bad symbol
  - on_execute callback only fires for ACCEPTED signals
"""

import asyncio
import os
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Webhook.signal_router import (  # noqa: E402
    RouterConfig,
    SignalRouter,
    SignalSource,
    SignalStatus,
)


def _payload(action="BUY", symbol="XAUUSD", entry=0):
    p = {"action": action, "symbol": symbol, "volume": 0.1,
         "sl_pips": 50, "tp_pips": 100, "comment": "test"}
    if entry:
        p["entry"] = entry
    return p


class SignalRouterTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def _new_router(self, **overrides) -> SignalRouter:
        cfg = RouterConfig(
            dedup_window_seconds=overrides.pop("dedup_window_seconds", 60),
            allowed_symbols=["XAUUSD", "BTCUSD"],
        )
        return SignalRouter(config=cfg)

    # ── Deduplication ─────────────────────────────────────────────

    def test_first_signal_accepted(self):
        async def scenario():
            router = self._new_router()
            sig = await router.route_signal(_payload(), SignalSource.EMAIL_PARSER)
            self.assertEqual(sig.status, SignalStatus.ACCEPTED)

        self.loop.run_until_complete(scenario())

    def test_same_signal_from_different_source_is_duplicate(self):
        async def scenario():
            router = self._new_router()
            sig1 = await router.route_signal(_payload(), SignalSource.EMAIL_PARSER)
            sig2 = await router.route_signal(_payload(), SignalSource.SORANOO_BRIDGE)
            self.assertEqual(sig1.status, SignalStatus.ACCEPTED)
            self.assertEqual(sig2.status, SignalStatus.DUPLICATE)
            # And the dup carries a pointer to the original.
            self.assertEqual(sig2.duplicate_of, sig1.fingerprint)

        self.loop.run_until_complete(scenario())

    def test_different_actions_not_deduped(self):
        async def scenario():
            router = self._new_router()
            buy = await router.route_signal(_payload(action="BUY"), SignalSource.EMAIL_PARSER)
            sell = await router.route_signal(_payload(action="SELL"), SignalSource.EMAIL_PARSER)
            self.assertEqual(buy.status, SignalStatus.ACCEPTED)
            self.assertEqual(sell.status, SignalStatus.ACCEPTED)

        self.loop.run_until_complete(scenario())

    def test_different_entry_prices_not_deduped(self):
        # The fingerprint includes entry rounded to 0.5, so meaningfully
        # different prices on the same action should both be accepted.
        async def scenario():
            router = self._new_router()
            sig1 = await router.route_signal(
                _payload(entry=2400.0), SignalSource.EMAIL_PARSER,
            )
            sig2 = await router.route_signal(
                _payload(entry=2410.0), SignalSource.EMAIL_PARSER,
            )
            self.assertEqual(sig1.status, SignalStatus.ACCEPTED)
            self.assertEqual(sig2.status, SignalStatus.ACCEPTED)

        self.loop.run_until_complete(scenario())

    def test_dedup_window_uses_time_bucket(self):
        # The fingerprint quantizes time into floor(now / window) buckets.
        # We can flip to the next bucket by directly mutating received_at.
        async def scenario():
            router = self._new_router(dedup_window_seconds=1)
            sig1 = await router.route_signal(_payload(), SignalSource.EMAIL_PARSER)
            self.assertEqual(sig1.status, SignalStatus.ACCEPTED)
            # Wait past the window so the time-bucket flips. We use a
            # short window so the test stays fast.
            await asyncio.sleep(1.1)
            sig2 = await router.route_signal(_payload(), SignalSource.EMAIL_PARSER)
            self.assertEqual(sig2.status, SignalStatus.ACCEPTED)
            self.assertNotEqual(sig1.fingerprint, sig2.fingerprint)

        self.loop.run_until_complete(scenario())

    # ── Validation ────────────────────────────────────────────────

    def test_invalid_action_rejected(self):
        async def scenario():
            router = self._new_router()
            sig = await router.route_signal(
                _payload(action="HODL"), SignalSource.EMAIL_PARSER,
            )
            self.assertEqual(sig.status, SignalStatus.REJECTED)

        self.loop.run_until_complete(scenario())

    def test_disallowed_symbol_rejected(self):
        async def scenario():
            router = self._new_router()
            sig = await router.route_signal(
                _payload(symbol="EURUSD"), SignalSource.EMAIL_PARSER,
            )
            self.assertEqual(sig.status, SignalStatus.REJECTED)

        self.loop.run_until_complete(scenario())

    # ── Callback contract ────────────────────────────────────────

    def test_callback_fires_only_for_accepted(self):
        async def scenario():
            router = self._new_router()
            executed = []

            async def on_exec(sig):
                executed.append(sig)

            router.on_execute = on_exec

            await router.route_signal(_payload(), SignalSource.EMAIL_PARSER)
            await router.route_signal(_payload(), SignalSource.SORANOO_BRIDGE)  # dup
            await router.route_signal(
                _payload(action="HODL"), SignalSource.EMAIL_PARSER,
            )  # rejected

            # Exactly one execution.
            self.assertEqual(len(executed), 1)
            self.assertEqual(executed[0].status, SignalStatus.ACCEPTED)

        self.loop.run_until_complete(scenario())


if __name__ == "__main__":
    unittest.main()
