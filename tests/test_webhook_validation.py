"""
Unit tests for WebhookListener payload validation.

These tests exercise the pure-logic validation path (no HTTP server needed)
and pin down the bug fixes applied to webhook_listener.py:

  - `payload["action"].upper()` previously crashed with AttributeError when
    a non-string value was sent. It now raises a clean ValueError that the
    caller turns into a 422.
  - `float(payload.get("volume", 1.0))` previously raised an ambiguous
    ValueError on non-numeric input. The new `_coerce_float` helper
    produces a consistent, field-named error.
  - `int(request.query.get("limit", 20))` could crash on non-numeric input.
    (Tested implicitly via clamp; the handler returns 400 on bad input.)
"""

import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Stub aiohttp if missing — webhook_listener imports `from aiohttp import web`
# at module load time, but the validation tests don't actually use the web
# server. CI installs the real package so this stub becomes a no-op.
if "aiohttp" not in sys.modules:
    try:  # pragma: no cover — environment-dependent
        import aiohttp  # noqa: F401
    except ImportError:
        aiohttp_stub = types.ModuleType("aiohttp")
        aiohttp_web = types.ModuleType("aiohttp.web")
        aiohttp_web.Application = object
        aiohttp_web.AppRunner = object
        aiohttp_web.TCPSite = object
        aiohttp_web.Request = object
        aiohttp_web.Response = object
        aiohttp_web.json_response = lambda *a, **kw: None
        aiohttp_stub.web = aiohttp_web
        sys.modules["aiohttp"] = aiohttp_stub
        sys.modules["aiohttp.web"] = aiohttp_web


def _listener():
    try:
        from Webhook.webhook_listener import WebhookListener, WebhookConfig  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise unittest.SkipTest(f"Webhook module unavailable: {exc}")
    cfg = WebhookConfig(
        allowed_symbols=["XAUUSD", "BTCUSD"],
        max_volume=10.0,
        min_volume=0.01,
    )
    return WebhookListener(config=cfg)


class ValidatePayloadTest(unittest.TestCase):
    def setUp(self):
        self.listener = _listener()

    # ── Happy path ─────────────────────────────────────────────────

    def test_valid_buy_signal(self):
        from Webhook.webhook_listener import TradeAction
        signal = self.listener._validate_payload({
            "action": "BUY",
            "symbol": "XAUUSD",
            "volume": 0.1,
            "sl_pips": 50,
            "tp_pips": 100,
        })
        self.assertEqual(signal.action, TradeAction.BUY)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertAlmostEqual(signal.volume, 0.1)

    def test_close_action_skips_volume_and_sltp_checks(self):
        # CLOSE doesn't require positive sl_pips/tp_pips
        from Webhook.webhook_listener import TradeAction
        signal = self.listener._validate_payload({
            "action": "close",
            "symbol": "xauusd",  # case-insensitive
        })
        self.assertEqual(signal.action, TradeAction.CLOSE)
        self.assertEqual(signal.symbol, "XAUUSD")

    # ── Defensive type coercion ────────────────────────────────────

    def test_non_string_action_raises_valueerror(self):
        # Previously this crashed with AttributeError ('int' has no .upper).
        with self.assertRaises(ValueError) as ctx:
            self.listener._validate_payload({"action": 1, "symbol": "XAUUSD"})
        self.assertIn("action", str(ctx.exception))

    def test_non_string_symbol_raises_valueerror(self):
        with self.assertRaises(ValueError) as ctx:
            self.listener._validate_payload({"action": "BUY", "symbol": 42})
        self.assertIn("symbol", str(ctx.exception))

    def test_non_numeric_volume_raises_valueerror(self):
        with self.assertRaises(ValueError) as ctx:
            self.listener._validate_payload({
                "action": "BUY",
                "symbol": "XAUUSD",
                "volume": "abc",
                "sl_pips": 50,
                "tp_pips": 100,
            })
        self.assertIn("volume", str(ctx.exception))

    def test_bool_volume_rejected(self):
        # bool is a subclass of int — without explicit guard, `True` would
        # silently coerce to 1.0. That's almost certainly a config bug, so
        # we reject it loudly.
        with self.assertRaises(ValueError) as ctx:
            self.listener._validate_payload({
                "action": "BUY",
                "symbol": "XAUUSD",
                "volume": True,
                "sl_pips": 50,
                "tp_pips": 100,
            })
        self.assertIn("volume", str(ctx.exception))

    def test_string_numeric_volume_accepted(self):
        # TradingView alert templates often produce strings — accept "0.1".
        signal = self.listener._validate_payload({
            "action": "BUY",
            "symbol": "XAUUSD",
            "volume": "0.1",
            "sl_pips": "50",
            "tp_pips": "100",
        })
        self.assertAlmostEqual(signal.volume, 0.1)
        self.assertAlmostEqual(signal.sl_pips, 50.0)

    # ── Required fields & business rules ──────────────────────────

    def test_missing_action_raises(self):
        with self.assertRaises(ValueError):
            self.listener._validate_payload({"symbol": "XAUUSD"})

    def test_missing_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.listener._validate_payload({"action": "BUY"})

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.listener._validate_payload({
                "action": "HODL",
                "symbol": "XAUUSD",
            })
        self.assertIn("HODL", str(ctx.exception))

    def test_disallowed_symbol_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.listener._validate_payload({
                "action": "BUY",
                "symbol": "EURUSD",
                "volume": 0.1,
                "sl_pips": 50,
                "tp_pips": 100,
            })
        self.assertIn("EURUSD", str(ctx.exception))

    def test_volume_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            self.listener._validate_payload({
                "action": "BUY",
                "symbol": "XAUUSD",
                "volume": 999.0,
                "sl_pips": 50,
                "tp_pips": 100,
            })

    def test_zero_sl_pips_rejected_for_buy(self):
        with self.assertRaises(ValueError):
            self.listener._validate_payload({
                "action": "BUY",
                "symbol": "XAUUSD",
                "volume": 0.1,
                "sl_pips": 0,
                "tp_pips": 100,
            })

    def test_long_comment_truncated(self):
        signal = self.listener._validate_payload({
            "action": "BUY",
            "symbol": "XAUUSD",
            "volume": 0.1,
            "sl_pips": 50,
            "tp_pips": 100,
            "comment": "x" * 1000,
        })
        self.assertEqual(len(signal.comment), 256)


if __name__ == "__main__":
    unittest.main()
