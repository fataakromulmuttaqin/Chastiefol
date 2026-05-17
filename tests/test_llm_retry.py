"""
Unit tests for LLMClient retry/backoff and circuit-breaker integration.

Strategy: mock the aiohttp session so we can inject any sequence of
HTTP statuses / exceptions and assert the client's retry/backoff
behavior + breaker bookkeeping.

We pin down four invariants:
  1. Transient statuses (429/5xx) are retried up to ``max_retries`` times.
  2. Permanent statuses (4xx other than 429/408/425) are NOT retried.
  3. ``Retry-After`` header (numeric) is respected, capped to 60s.
  4. After enough consecutive failures, the breaker opens and short-circuits.
"""

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Stub aiohttp + aiohttp.web before importing LLM module (it imports
# aiohttp at module load time but we don't actually need a real session).
if "aiohttp" not in sys.modules:
    try:  # pragma: no cover
        import aiohttp  # noqa: F401
    except ImportError:
        aiohttp_stub = types.ModuleType("aiohttp")

        class _ClientError(Exception):
            pass

        class _ClientTimeout:  # noqa: D401
            def __init__(self, total=None, **kw):
                self.total = total

        class _ClientSession:
            def __init__(self, *a, **kw):
                pass

        aiohttp_stub.ClientError = _ClientError
        aiohttp_stub.ClientTimeout = _ClientTimeout
        aiohttp_stub.ClientSession = _ClientSession
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

import aiohttp  # noqa: E402  (now resolves to real or stub)

from LLM.llm_agent import LLMClient, LLMConfig, LLMProvider  # noqa: E402
from Common.circuit_breaker import CircuitState  # noqa: E402


def _mock_response(status: int, body: str = "", headers: dict = None, json_data=None):
    """Build an async-context-manager mock for aiohttp's `session.post`.

    aiohttp returns a context manager whose __aenter__ yields a response
    with .status, .text(), .json(), and .headers. We mirror that shape.
    """
    headers = headers or {}

    resp = MagicMock()
    resp.status = status
    resp.headers = headers
    resp.text = AsyncMock(return_value=body)
    resp.json = AsyncMock(return_value=json_data if json_data is not None else {})

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class _FakeSession:
    """Returns a queued sequence of mock responses (or raises)."""

    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = 0
        self.closed = False

    def post(self, url, json=None, headers=None):
        self.calls += 1
        if not self._queue:
            raise AssertionError("FakeSession ran out of queued responses")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            # Schedule the exception to be raised when entering the
            # context manager.
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(side_effect=item)
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm
        return item

    async def close(self):
        self.closed = True


def _client_with_session(session, **overrides) -> LLMClient:
    cfg = LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key="test",
        model="test-model",
        base_url="http://example.test/v1",
        max_retries=overrides.pop("max_retries", 3),
        retry_backoff_base=overrides.pop("retry_backoff_base", 0.0),  # zero so tests are fast
    )
    client = LLMClient(cfg)
    client._session = session
    return client


class LLMRetryTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    # ── Happy path ────────────────────────────────────────────────

    def test_success_on_first_attempt(self):
        session = _FakeSession([
            _mock_response(200, json_data={
                "choices": [{"message": {"content": "hello"}}]
            }),
        ])
        client = _client_with_session(session)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "hello")
        self.assertEqual(session.calls, 1)
        self.assertEqual(client._breaker.state, CircuitState.CLOSED)

    # ── Retry on transient status ────────────────────────────────

    def test_retries_on_429_then_succeeds(self):
        session = _FakeSession([
            _mock_response(429, body="rate limit", headers={"Retry-After": "0"}),
            _mock_response(503, body="upstream"),
            _mock_response(200, json_data={
                "choices": [{"message": {"content": "recovered"}}]
            }),
        ])
        client = _client_with_session(session, max_retries=3)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "recovered")
        self.assertEqual(session.calls, 3)
        # Breaker should NOT have tripped — final attempt succeeded.
        self.assertEqual(client._breaker.state, CircuitState.CLOSED)

    def test_exhausts_retries_returns_empty(self):
        session = _FakeSession([
            _mock_response(503),
            _mock_response(503),
            _mock_response(503),
        ])
        client = _client_with_session(session, max_retries=3)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "")
        self.assertEqual(session.calls, 3)
        # One full chat() failure increments the breaker by one.
        self.assertEqual(client._breaker.failure_count, 1)

    # ── Permanent status: do NOT retry ───────────────────────────

    def test_does_not_retry_on_400(self):
        # 400 = bad request (our bug). Retrying just delays the failure.
        session = _FakeSession([
            _mock_response(400, body="bad payload"),
        ])
        client = _client_with_session(session, max_retries=5)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "")
        self.assertEqual(session.calls, 1)

    def test_does_not_retry_on_401(self):
        # 401 = bad API key. Retrying never helps.
        session = _FakeSession([
            _mock_response(401, body="unauthorized"),
        ])
        client = _client_with_session(session, max_retries=5)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "")
        self.assertEqual(session.calls, 1)

    # ── Network errors ────────────────────────────────────────────

    def test_retries_on_timeout(self):
        session = _FakeSession([
            asyncio.TimeoutError("slow"),
            _mock_response(200, json_data={
                "choices": [{"message": {"content": "ok"}}]
            }),
        ])
        client = _client_with_session(session, max_retries=2)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "ok")
        self.assertEqual(session.calls, 2)

    def test_retries_on_clienterror(self):
        session = _FakeSession([
            aiohttp.ClientError("connection reset"),
            _mock_response(200, json_data={
                "choices": [{"message": {"content": "ok"}}]
            }),
        ])
        client = _client_with_session(session, max_retries=2)

        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "ok")
        self.assertEqual(session.calls, 2)

    # ── Backoff: Retry-After is honored ──────────────────────────

    def test_retry_after_header_is_clamped(self):
        # Server says wait 9999 seconds; we cap at 60.
        client = LLMClient(LLMConfig(
            provider=LLMProvider.OPENAI,
            api_key="test",
            base_url="http://x",
            max_retries=1,
            retry_backoff_base=1.0,
        ))
        # Numeric -> clamped
        self.assertAlmostEqual(client._compute_backoff("9999", 1), 60.0)
        # Non-numeric (HTTP-date) -> falls back to exponential
        self.assertAlmostEqual(client._compute_backoff("Wed, 01 Jan 2025 00:00:00 GMT", 1), 1.0)
        # No header
        self.assertAlmostEqual(client._compute_backoff(None, 1), 1.0)
        self.assertAlmostEqual(client._compute_backoff(None, 2), 2.0)
        self.assertAlmostEqual(client._compute_backoff(None, 10), 30.0)  # capped

    # ── Circuit breaker integration ──────────────────────────────

    def test_breaker_opens_after_repeated_failures(self):
        # 5 chat() calls each exhausting retries -> 5 breaker failures
        # -> breaker should be OPEN.
        responses = []
        for _ in range(5):
            responses.extend([_mock_response(503), _mock_response(503)])
        session = _FakeSession(responses)
        client = _client_with_session(session, max_retries=2)

        for _ in range(5):
            self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))

        self.assertEqual(client._breaker.state, CircuitState.OPEN)

        # 6th call should NOT hit the network.
        calls_before = session.calls
        result = self.loop.run_until_complete(client.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(result, "")
        self.assertEqual(session.calls, calls_before)


if __name__ == "__main__":
    unittest.main()
