# Chastiefol 🟡

**Autonomous gold (XAU/USD) trading agent for cTrader (IC Markets), powered by LLMs via OpenRouter.**

Adapted from the architecture of [Meridian](https://github.com/yunus-0x/meridian) — a Solana DLMM LP agent — and retooled for forex gold trading.

---

## What it does

- **Screens for setups** — runs a SCREENER agent every 15 minutes that analyses price action, indicators (RSI, MACD, ATR, EMA50/200, Bollinger Bands), session timing, and account risk — then opens a trade if all conditions align
- **Manages open positions** — runs a MANAGER agent every 5 minutes that evaluates each open trade and decides: STAY / MODIFY (trail stop, move to BE) / CLOSE
- **Safety pipeline** — every order passes through: daily loss circuit breaker, max position cap, free margin check, spread guard, session filter, mandatory SL enforcement
- **Learns from history** — saves lessons after each notable event; evolve thresholds after 5+ closed trades via `/evolve`
- **Telegram chat** — full agent chat via Telegram + cycle reports after every screener/manager run

---

## Architecture

```
index.js          ← main entry: REPL + cron orchestration + Telegram polling
agent.js          ← ReAct loop: LLM → tool call → observe → repeat
prompt.js         ← builds role-specific system prompts (SCREENER / MANAGER / GENERAL)
config.js         ← runtime config (hot-reload)
state.js          ← in-memory + persisted state (positions, PnL, equity)
lessons.js        ← persistent learning memory + performance records
ctrader.js        ← cTrader Open API wrapper (account, price, orders)
telegram.js       ← bot notifications + chat handler
tools/
  definitions.js  ← JSON Schema for all LLM tools
  executor.js     ← safety pipeline + tool dispatcher
```

### Dual-agent design (from Meridian)

| Agent   | Interval | Role |
|---------|----------|------|
| SCREENER | 15 min  | Find new trade setups |
| MANAGER  | 5 min   | Manage open positions |

---

## Quick start

**1. Clone & install**
```bash
git clone https://github.com/fataakromulmuttaqin/Chastiefol
cd Chastiefol
npm install
```

**2. Setup**
```bash
npm run setup
```
The wizard creates `.env` and `user-config.json`.

**3. Run dry (no real orders)**
```bash
npm run dev
```

**4. Go live**
```bash
npm start
```

---

## Requirements

- Node.js ≥ 18
- [OpenRouter](https://openrouter.ai) API key
- cTrader account at IC Markets (get your OAuth credentials from the [cTrader Open API portal](https://openapi.ctrader.com/))
- [Twelve Data](https://twelvedata.com) API key (optional — for RSI, MACD, ATR, EMA)
- Telegram bot token (optional — create via @BotFather)

---

## REPL commands

```
[screen: 12m 4s | manage: 3m 22s] >
```

| Command | Description |
|---------|-------------|
| `/status` | Account info + open positions |
| `/screen` | Force screener cycle now |
| `/manage` | Force manager cycle now |
| `/lessons` | Show all saved lessons |
| `/lesson <text>` | Add a manual lesson |
| `/perf` | Win rate, total PnL, last 10 trades |
| `/stop` | Graceful shutdown |
| `<anything>` | Free-form chat with the gold agent |

---

## Config reference (`user-config.json`)

| Field | Default | Description |
|-------|---------|-------------|
| `symbol` | `XAUUSD` | Trading symbol |
| `dryRun` | `true` | Simulate without real orders |
| `riskPerTradePct` | `1.0` | Risk per trade (% of equity) |
| `maxOpenTrades` | `3` | Max concurrent positions |
| `stopLossPips` | `20` | Default SL distance |
| `takeProfitRatio` | `2.0` | Minimum R:R ratio |
| `maxDailyLossPct` | `3.0` | Daily loss circuit breaker |
| `maxSpreadPips` | `0.5` | Skip trade if spread > this |
| `screeningIntervalMin` | `15` | How often screener runs |
| `managementIntervalMin` | `5` | How often manager runs |
| `screeningModel` | gemini-2.5-flash | LLM for screener |
| `managementModel` | gemini-2.5-flash | LLM for manager |

---

## Threshold evolution

After 5+ closed trades:
```bash
node scripts/evolve-thresholds.js
```
The agent analyses win rate, average pips, and PnL — then adjusts config values and explains each change.

---

## ⚠️ Disclaimer

This software is provided as-is, with no warranty. Running an autonomous trading agent carries **real financial risk** — you can lose funds. Always start with `npm run dev` (dry run). Never trade more than you can afford to lose. This is not financial advice.
