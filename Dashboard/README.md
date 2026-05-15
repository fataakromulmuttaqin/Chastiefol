# Chastiefol — Web Dashboard

Real-time React dashboard for monitoring the Chastiefol trading agent.

## Features

- **Live Status Cards** — Balance, equity, P&L, drawdown, open trades
- **Equity Curve** — Interactive chart (Recharts + TailwindCSS)
- **Trade Table** — Recent trades with direction, entry/exit, P&L
- **Signal Panel** — Live BUY/SELL signals with confluence score
- **Portfolio View** — Per-pair performance & risk allocation
- **Auto-refresh** — Polls API every 3–10 seconds

## Tech Stack

- **Frontend**: React 18 + Vite + TailwindCSS + Recharts
- **Backend API**: Python (aiohttp) — served by main agent
- **Styling**: Dark mode, responsive

## Quick Start

```bash
cd Dashboard/frontend
npm install
npm run dev
# Opens at http://localhost:3000
```

Make sure the Chastiefol agent is running (provides API at port 3001).

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| GET `/api/status` | Agent status (balance, equity, mode) |
| GET `/api/trades?limit=50` | Recent trade history |
| GET `/api/equity` | Equity curve data points |
| GET `/api/signals?limit=10` | Recent signals |
| GET `/api/portfolio` | Portfolio breakdown per pair |
| GET `/api/health` | API health check |

## Production Build

```bash
npm run build
# Output in dist/ — serve with nginx or embed in Python server
```
