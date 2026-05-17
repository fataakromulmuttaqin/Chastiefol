# Chastiefol — Operations & Recovery Runbook

Audience: anyone running Chastiefol in paper or live mode. This doc covers
how to read the health endpoint, how the reliability machinery (timeouts,
retry, circuit breakers) behaves in practice, and what to do when something
goes wrong.

---

## 1. Health endpoint

`GET /health` returns the consolidated status of every registered subsystem.

```json
{
  "status": "degraded",
  "service": "chastiefol-webhook",
  "timestamp": "2026-05-17T14:00:00Z",
  "uptime": true,
  "queue_size": 0,
  "signals_received": 12,
  "components": [
    {"name": "webhook",        "status": "healthy",   "detail": "listening on 0.0.0.0:8080", "age_seconds": 0.3},
    {"name": "datafeed",       "status": "degraded",  "detail": "primary down, using CCXT PAXG/USDT proxy", "age_seconds": 1.2},
    {"name": "llm:openai",     "status": "unhealthy", "detail": "Circuit 'llm:openai' is OPEN; retry in ~28.4s", "breaker": "open", "age_seconds": 5.0}
  ]
}
```

| Top-level status | HTTP code | Meaning |
|---|---|---|
| `healthy`   | 200 | Everything green. |
| `unknown`   | 200 | At least one component never reported (typically right after boot). |
| `degraded`  | 503 | Working but reduced — fallback feed, breaker half-open, etc. |
| `unhealthy` | 503 | At least one critical component is down. Trading should pause. |

The HTTP 503 is intentional — load balancers and uptime monitors can
route around or alert on a sick instance.

A component is also tagged `"stale": true` when it hasn't reported in
`stale_after_seconds` (default 120s). Stale components contribute as
`degraded` to the overall status even if they last reported `healthy`.

---

## 2. Reliability machinery

### 2.1 Timeouts

Every outbound HTTP call has an explicit timeout. Currently:

| Caller                                | Timeout | Notes |
|---|---|---|
| `LLMClient`                           | `LLM_TIMEOUT` env (default 120s) | Total per-call |
| `TelegramNotifier.test_connection`    | 10s                | Boot path — startup must not hang |
| `TelegramNotifier` send                | 10s, retried `max_retries` times |   |
| `scripts/daily_report.send_telegram`  | 15s                | Cron — failures are printed, not raised |
| `BinanceWebSocket.start`              | `sock_connect=15s` | HTTP-Upgrade only; streaming uses heartbeat |
| `CCXT` provider                        | 15s                |   |
| `CTraderMCP`                           | `config.timeout`   | Configurable |

If you see a startup hang, check whether a recent change reintroduced an
unbounded `aiohttp.ClientSession()` or `urlopen()` without `timeout=`.

### 2.2 LLM retry policy

`LLMClient` retries on transient failures:

- HTTP 408 (Request Timeout), 425 (Too Early), 429 (Rate Limit), 5xx
- `asyncio.TimeoutError`, `aiohttp.ClientError`

It does **not** retry on other 4xx (400, 401, 403, 404). Those indicate a
bug or misconfig and retrying just delays the visible failure.

Backoff: exponential `LLMConfig.retry_backoff_base * 2^(attempt-1)`,
capped at 30s, but `Retry-After` (numeric) takes precedence and is
clamped to 60s.

Tunables (env vars, all optional):

- `LLM_TIMEOUT` — total per-request timeout in seconds (default 120).
- `LLM_BREAKER_THRESHOLD` — consecutive failures before breaker opens (default 5).
- `LLM_BREAKER_COOLDOWN` — seconds the breaker stays OPEN before probing (default 60).

### 2.3 Circuit breaker

Each LLM provider has its own breaker (`Common.CircuitBreaker`).

```
CLOSED ──[N consecutive failures]──> OPEN
  ▲                                    │
  │                            [recovery_timeout]
  │                                    ▼
SUCCESS ◄──── HALF_OPEN  ◄──────────────┘
                  │
              [probe fails]
                  ▼
                OPEN
```

When OPEN, `chat()` returns `""` immediately without hitting the network.
Existing callers already treat empty responses as "LLM unavailable, fall
back to HOLD", so trading degrades safely.

To force a reset (e.g. after fixing an API key without restarting):

```python
from LLM.llm_agent import LLMInsightAgent
await agent.client._breaker.reset()
```

---

## 3. Common incidents

### 3.1 `/health` shows `datafeed: unhealthy, all providers down; serving stale cache`

**What it means:** TradingView WebSocket is disconnected, the CCXT proxy is
also failing, and the agent is serving the last cached price.

**Action:**
1. Check internet egress on the host.
2. Check `journalctl -u chastiefol -f | grep DataFeed` for the underlying error
   (rate limit? DNS? auth?).
3. If TradingView WS is rejecting the symbol, try one of `symbol_alt`
   (`FOREXCOM:XAUUSD`, `FX:XAUUSD`, `CAPITALCOM:GOLD`).
4. **Pause trading until this clears.** Stale prices can produce
   wrong-side fills.

### 3.2 `/health` shows `llm:<provider>: unhealthy, Circuit ... OPEN`

**What it means:** The LLM provider returned 5+ consecutive failures.
Trading continues to function — the agent will recommend HOLD when LLM
is unavailable — but you've lost AI insight until the breaker recovers.

**Action:**
1. Check the provider's status page.
2. Look at `journalctl ... | grep "LLM API"` for the most recent error
   body. 401/403 means key/permissions; 429 means rate limit; 5xx means
   the provider is down.
3. Wait `LLM_BREAKER_COOLDOWN` seconds (default 60) for an automatic
   probe, or restart the agent.

### 3.3 Webhook returns 503 `Server misconfigured: webhook secret not set`

**What it means:** `WEBHOOK_HMAC_ENABLED=true` but `WEBHOOK_SECRET` is empty.
The listener refuses to serve rather than silently expose the endpoint.

**Action:** Set `WEBHOOK_SECRET` in `.env` (any random 32+ char string)
and restart, OR set `WEBHOOK_HMAC_ENABLED=false` if you knowingly want an
open webhook (paper mode behind a firewall only).

### 3.4 Webhook returns 422 `Validation failed`

The payload was syntactically valid JSON but a field is wrong. The
`detail` always names the offending field. Common causes:

- `Field 'volume' must be numeric, got 'true'` — TradingView template
  has a typo (Pine `true`/`false` got serialized as a number-like string).
- `Symbol 'EURUSD' not allowed` — adjust `WebhookConfig.allowed_symbols`.
- `sl_pips must be positive for BUY/SELL orders` — your alert template
  forgot to substitute the placeholder.

### 3.5 Tests pass locally but CI fails on flake8

The CI workflow (`.github/workflows/python-package.yml`) runs:

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

This catches syntax errors and undefined names only — style warnings are
deliberately not fatal. The same check locally:

```bash
ruff check --select=E9,F63,F7,F82 .
```

---

## 4. Test discipline

The existing test suite covers the failure paths that historically caused
silent data corruption or duplicate trade execution:

```
tests/
├── test_circuit_breaker.py       # state machine, recovery, exception filter
├── test_health_registry.py       # ordering, staleness, snapshot shape
├── test_llm_retry.py              # retry/backoff/no-retry on permanent errors
├── test_llm_thinking_strip.py    # MiniMax/DeepSeek thinking-tag stripping
├── test_signal_router.py         # dedup correctness — DOUBLE-EXECUTION RISK
└── test_webhook_validation.py    # type coercion, HMAC enforcement
```

Run them all:

```bash
PYTHONPATH=. python -m unittest discover tests
```

Before opening a PR that touches:

- **anything in `LLM/llm_agent.py`** — verify `_strip_thinking_tags` and
  `_post_with_retry` tests still pass.
- **anything in `Webhook/`** — verify validation + dedup tests pass.
  Dedup tests are particularly load-bearing because a regression here
  means *executing the same trade twice*.
- **anything in `Common/`** — both circuit_breaker and health tests
  should pass.

---

## 5. Telegram alerts

`TelegramNotifier` (when configured) sends:

- 🟢 / 🔴 startup / shutdown announcements
- ✅ order fills, 💔 trade losses
- 🛡️ drawdown threshold breaches
- 🚨 connection / order errors
- 📋 daily P&L summary (via `scripts/daily_report.py`)

There is currently **no automatic alert on `/health` degradation** — the
registry exposes it via HTTP, but you need an external monitor (uptimerobot,
healthchecks.io, k8s liveness probe) to page on it. That's intentional;
running cross-channel alerting in-process tends to amplify outages.

A simple shell loop that pages on degradation:

```bash
while true; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health)
  if [ "$status" = "503" ]; then
    curl -s "https://api.telegram.org/bot$BOT/sendMessage" \
      -d "chat_id=$CHAT" -d "text=⚠️ Chastiefol /health is 503"
    sleep 300  # avoid spam
  fi
  sleep 30
done
```
