# CHASTIEFOL — Website Specification

Website dokumentasi dan dashboard untuk Chastiefol — autonomous XAUUSD trading agent.

---

## 1. Concept & Vision

**Chastiefol** adalah autonomous trading agent untuk XAUUSD (Gold) yang menggabungkan:
- **Technical Analysis** — PSAR, EMA stack, RSI, MACD, Bollinger Bands, ATR
- **Smart Money Concepts (SMC)** — Market structure, Order blocks, Fair Value Gaps, Break of Structure
- **Risk Management** — Position sizing (Kelly/Fixed), Drawdown guard, Session filter, Trailing SL/Breakeven
- **Multi-source Execution** — cTrader MCP (REST), cTrader FIX 4.4 (TCP), atau Paper mode

Website ini berfungsi sebagai:
- **Dokumentasi lengkap** — semua fitur, arsitektur, API, konfigurasi
- **Dashboard monitoring** — status, trades, equity, signals
- **Landing page** — penjelasan produk, setup guide, FAQ

**Visual identity:** Dark, premium fintech aesthetic — hitam/gold, seperti terminal trading profesional.

---

## 2. Design Language

### Color Palette
```
Background Primary:    #0A0A0F (deep black)
Background Secondary:   #12121A (card/surface)
Background Tertiary:    #1A1A26 (elevated surface)
Accent Gold:           #F5C518 (Chastiefol signature gold)
Accent Gold Light:     #FFD966 (hover/active states)
Accent Green:          #00D084 (profit/win)
Accent Red:            #FF4757 (loss/error)
Accent Blue:           #3B82F6 (info/links)
Text Primary:          #FFFFFF
Text Secondary:        #A0A0B0
Text Muted:            #606070
Border:                #2A2A3A
```

### Typography
- **Headings:** Inter (700, 600) — clean, modern fintech
- **Body:** Inter (400, 500) — readable
- **Monospace:** JetBrains Mono — untuk code, prices, IDs
- **Fallback:** system-ui, sans-serif

### Spacing System
- Base unit: 4px
- Section padding: 80px vertical, 24px horizontal
- Card padding: 24px
- Gap between cards: 16px
- Border radius: 12px (cards), 8px (buttons), 4px (inputs)

### Motion Philosophy
- Micro-interactions: 150ms ease-out
- Page transitions: 300ms ease
- Loading states: skeleton pulse animation
- No heavy animations — professional, not flashy

---

## 3. Project Structure & Architecture

### What is Chastiefol?

Chastiefol is a Python-based autonomous trading agent that:
1. Analyzes XAUUSD market using SMC + technical indicators
2. Generates trade signals with confidence scores and R:R ratios
3. Executes orders via cTrader broker (MCP or FIX API)
4. Manages risk with position sizing, drawdown guards, trailing SL
5. Notifies via Telegram on every trade event

### System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     CHASTIEFOL AGENT                             │
│                    (chastiefol_main.py)                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  TradingView │  │  Autonomous  │  │       cTrader          │  │
│  │   Webhook   │  │   Scanner    │  │       Broker           │  │
│  │  Port 8080  │  │  (60s loop)  │  │  (MCP / FIX)          │  │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬─────────────┘  │
│         │                │                     │                │
│         ▼                ▼                     ▼                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              SIGNAL ROUTER + RISK ENGINE                  │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────────────┐   │   │
│  │  │ Webhook    │ │ Drawdown   │ │  Position Sizer     │   │   │
│  │  │ Listener   │ │ Guard      │ │  (Kelly/Fixed)     │   │   │
│  │  │ (aiohttp)  │ │            │ │                    │   │   │
│  │  └────────────┘ └────────────┘ └────────────────────┘   │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────────────┐   │   │
│  │  │ Gold       │ │ Trade      │ │  Entry Price        │   │   │
│  │  │ Session    │ │ Manager    │ │  Validator (FIX)    │   │   │
│  │  │ Filter     │ │ (Trail/BE) │ │                    │   │   │
│  │  └────────────┘ └────────────┘ └────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│         ┌──────────────────┼──────────────────┐                 │
│         ▼                  ▼                  ▼                 │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐   │
│  │  cTrader    │   │  cTrader    │   │     Paper           │   │
│  │  MCP        │   │  FIX 4.4    │   │     Mode            │   │
│  │  REST/JSON  │   │  TCP/SSL    │   │     (Simulated)    │   │
│  └─────────────┘   └─────────────┘   └─────────────────────┘   │
│                            │                                     │
│         ┌──────────────────┴──────────────────┐               │
│         ▼                                       ▼               │
│  ┌──────────────────┐            ┌─────────────────────────┐  │
│  │  Telegram         │            │   Dashboard             │  │
│  │  Notifier         │            │   (React + Vite)        │  │
│  │  (bot alerts)     │            │   Port 3000             │  │
│  └──────────────────┘            └─────────────────────────┘  │
│                                                                  │
│  DATA LAYER:                                                     │
│  ┌───────────┐   ┌────────────┐   ┌────────────────────────┐  │
│  │ DataFeed  │──►│  Analysis  │──►│   Risk Manager          │  │
│  │ Manager   │   │  Engine    │   │   (Position + Drawdown) │  │
│  │           │   │            │   │                         │  │
│  │TwelveData │   │ Chastiefoll│   │ PositionSizer           │  │
│  │AlphaVantage│  │ Agent      │   │ DrawdownGuard           │  │
│  │GoldAPI    │   │            │   │ TradeManager            │  │
│  └───────────┘   └────────────┘   └────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Pages & Sections

### Page 1: Landing / Home

**Hero Section**
- Headline: "Chastiefol — Autonomous XAUUSD Trading Agent"
- Subheadline: "Professional-grade gold trading with SMC analysis, smart risk management, and multi-broker execution"
- CTA: "View Documentation" + "See Dashboard"
- Live status badge: Online/Offline dengan uptime counter

**Features Grid (3 columns)**
1. Smart Analysis — SMC + Technical indicators, confluence scoring
2. Multi-Execution — cTrader MCP, FIX 4.4, Paper mode
3. Risk Management — Kelly position sizing, drawdown guard, trailing SL

**Stats Bar**
- Total trades executed
- Win rate
- Average R:R
- Uptime

**Architecture Overview** — visual diagram showing all components

---

### Page 2: Features

**Analysis Engine**
- PSAR flip detection
- EMA 20/50/100/200 stack alignment
- Market structure (BOS, CHoCH)
- Order blocks, Fair Value Gaps
- Confluence scoring (min 55/100)
- ATR-based SL/TP calculation

**Signal Modes**
- Webhook mode (TradingView integration)
- Autonomous mode (self-scanning every 60s)
- Hybrid mode (both simultaneously)

**Execution Methods**
- cTrader MCP (REST/JSON-RPC) — direct broker API
- cTrader FIX 4.4 (TCP/SSL) — industry standard protocol
- Paper mode (simulated fills for backtesting)

**Risk Management**
- Position sizing: Fixed Percent, Kelly, Volatility-based
- Drawdown guard: 5-stage circuit breaker
- Session filter: London/NY Killzones, Asian Range
- Trade manager: Breakeven, trailing SL, partial TP at 1:1 R:R

---

### Page 3: Architecture

**Component Map**
- Full interactive diagram showing all modules
- Click each module for detail

**Data Flow**
1. Data Feed → Analysis Engine → Signal Generation
2. Signal → Risk Check (DrawdownGuard + PositionSizer)
3. Risk Check → Execution (MCP/FIX/Paper)
4. Execution → Trade Manager (trailing/BE/partial TP)
5. Events → Telegram Notifier

**Technology Stack Table**
| Component | Technology |
|-----------|------------|
| Main Agent | Python 3.10+ (asyncio) |
| Webhook Server | aiohttp (port 8080) |
| Frontend | React 18 + Vite |
| Data Providers | TwelveData, AlphaVantage, GoldAPI |
| Broker Integration | cTrader MCP (REST), cTrader FIX 4.4 |
| Notifications | Telegram Bot API |
| AI Agent | ReAct loop (OpenRouter/GroQ/OpenAI) |

---

### Page 4: API Reference

**Webhook Server (port 8080)**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhook` | Receive TradingView signals |
| GET | `/health` | Health check |
| GET | `/signals` | Recent signal history |
| GET | `/status` | Server status + stats |

**POST /webhook**
```json
// Request
{
  "action": "BUY",         // BUY | SELL | CLOSE (required)
  "symbol": "XAUUSD",      // Symbol (required)
  "volume": 0.01,          // Lot size (optional, default 0.01)
  "sl_pips": 150,           // Stop loss in pips (required)
  "tp_pips": 300,           // Take profit in pips (required)
  "comment": "PSAR flip"    // Optional annotation
}

// Response
{
  "status": "accepted",
  "signal": {
    "action": "BUY",
    "symbol": "XAUUSD",
    "volume": 0.01,
    "sl_price": 2350.50,
    "tp_price": 2385.50",
    "timestamp": "2025-05-16T08:30:00Z"
  },
  "queue_size": 1
}
```

**GET /health**
```json
{
  "status": "healthy",
  "service": "chastiefol-webhook",
  "timestamp": "2025-05-16T08:30:00Z",
  "uptime": true,
  "queue_size": 0,
  "signals_received": 142
}
```

**GET /signals?limit=20**
```json
{
  "signals": [
    {
      "id": "sig_abc123",
      "action": "BUY",
      "symbol": "XAUUSD",
      "volume": 0.05,
      "entry_price": 2365.50,
      "sl_price": 2350.50,
      "tp_price": 2385.50,
      "confidence": 78,
      "timestamp": "2025-05-16T08:30:00Z",
      "status": "executed"
    }
  ],
  "total": 142
}
```

**GET /status**
```json
{
  "running": true,
  "port": 8080,
  "allowed_symbols": ["XAUUSD"],
  "rate_limit": 10,
  "hmac_enabled": true,
  "queue_size": 0,
  "signals_total": 142,
  "execution_method": "fix",
  "paper_mode": false
}
```

---

### Page 5: Configuration

**Environment Variables Reference**

```
# ═══════════════════════════════════════════════════
# MODE & SYMBOL
# ═══════════════════════════════════════════════════
AGENT_MODE=hybrid           # webhook | autonomous | hybrid
SYMBOL=XAUUSD               # Trading symbol
PAPER_MODE=false            # true = simulated fills
SCAN_INTERVAL_SEC=60        # Autonomous scan frequency

# ═══════════════════════════════════════════════════
# ACCOUNT & RISK
# ═══════════════════════════════════════════════════
INITIAL_BALANCE=1000        # Starting balance (USD)
RISK_PCT=1.0                # Risk % per trade
MAX_RISK_PCT=2.0            # Hard cap risk %
MIN_CONFIDENCE=0.55         # Min signal confidence (0-1)
MAX_DAILY_LOSS_PCT=3.0      # Daily loss circuit breaker
MAX_DRAWDOWN_PCT=10.0       # Max equity drawdown %
MAX_OPEN_TRADES=2           # Max concurrent trades

# ═══════════════════════════════════════════════════
# EXECUTION METHOD
# ═══════════════════════════════════════════════════
EXECUTION_METHOD=fix        # mcp | fix | paper

# ═══════════════════════════════════════════════════
# CTRADER MCP (REST/JSON-RPC)
# ═══════════════════════════════════════════════════
CTRADER_MCP_URL=https://mcp.ctrader.com/trading/mcp
CTRADER_ACCESS_TOKEN=       # Bearer token
CTRADER_ACCOUNT_ID=5820056

# ═══════════════════════════════════════════════════
# CTRADER FIX 4.4 (TCP/SSL)
# ═══════════════════════════════════════════════════
FIX_PRICE_HOST=demo-uk-eqx-01.p.c-trader.com
FIX_PRICE_PORT=5211
FIX_TRADE_HOST=demo-uk-eqx-01.p.c-trader.com
FIX_TRADE_PORT=5212
FIX_SENDER_COMP_ID=demo.ctrader.5820056
FIX_TARGET_COMP_ID=cServer
FIX_PASSWORD=
FIX_SYMBOL_MAP={"XAUUSD":"41"}

# ═══════════════════════════════════════════════════
# WEBHOOK SERVER
# ═══════════════════════════════════════════════════
WEBHOOK_PORT=8080
WEBHOOK_SECRET=
WEBHOOK_HMAC_ENABLED=true

# ═══════════════════════════════════════════════════
# DATA FEED PROVIDERS
# ═══════════════════════════════════════════════════
TWELVEDATA_API_KEY=
ALPHAVANTAGE_API_KEY=
GOLDAPI_API_KEY=

# ═══════════════════════════════════════════════════
# TELEGRAM NOTIFICATIONS
# ═══════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=633709469

# ═══════════════════════════════════════════════════
# LLM AGENT (ReAct)
# ═══════════════════════════════════════════════════
LLM_PROVIDER=openrouter
LLM_MODEL=inclusionai/ring-2.6-1t:free
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=2048
LLM_MAX_REACT_STEPS=5
```

---

### Page 6: Data Models

**TradeSetup (Analysis Output)**
```python
{
  "signal": "BUY" | "SELL" | "HOLD",
  "entry": 2365.50,           # Entry price
  "stop_loss": 2350.50,       # SL price
  "take_profit": 2385.50,     # TP price
  "rr_ratio": 2.0,           # Reward:Risk ratio
  "confidence": 0.78,         # 0.0 to 1.0
  "confluence": 5,            # Number of confirming factors
  "reasons": [
    "Parabolic SAR flipped bullish (SAR: 2363.20)",
    "EMA 20/50/100/200 stack confirmed",
    "RSI 58.2 in buy zone (40-65)"
  ],
  "timeframe": "H1"
}
```

**WebhookSignal (Incoming)**
```python
{
  "action": "BUY" | "SELL" | "CLOSE",
  "symbol": "XAUUSD",
  "volume": 0.01,            # Lot size
  "sl_pips": 150,            # SL in pips
  "tp_pips": 300,            # TP in pips
  "comment": "PSAR flip",
  "timestamp": "2025-05-16T08:30:00Z",
  "raw_payload": {...}
}
```

**TradeLifecycle (Active Trade)**
```python
{
  "entry_price": 2365.50,
  "stop_loss": 2350.50,
  "take_profit": 2385.50,
  "lot_size": 0.05,
  "direction": "BUY",
  "breakeven_moved": false,
  "partial_closed": false,
  "current_price": 2370.00,
  "pnl_usd": 22.50,          # Calculated: pips * lot * 100
  "rr_achieved": 0.3          # pips_profit / abs(entry - SL)
}
```

**AccountState**
```python
{
  "balance": 1050.00,        # Realized balance
  "equity": 1072.50,         # Balance + floating P&L
  "open_trades": 1,
  "daily_loss": 0.00,
  "peak_balance": 1100.00,
  "drawdown_pct": 2.5        # (peak - equity) / peak * 100
}
```

**PositionSpec (Risk Calculation Result)**
```python
{
  "lot_size": 0.05,
  "units": 5.0,              # lot_size * 100
  "risk_usd": 10.00,         # Dollar risk
  "risk_pct": 1.0,           # % of account
  "margin_required": 50.00,
  "max_lot": 10.0
}
```

**OrderResult (Execution Response)**
```python
{
  "success": true,
  "order_id": "CHAST_abc123",
  "position_id": "303060867",
  "execution_price": 2365.50,
  "filled_volume": 0.05,
  "error_code": null,
  "error_message": null
}
```

---

### Page 7: Telegram Notifications

**Signal Alert 🟢/🔴**
```
🟢 CHASTIEFOL SIGNAL
━━━━━━━━━━━━━━━━━━━━
📈 BUY XAUUSD

💰 Entry: $2365.50
🛑 Stop Loss: $2350.50
🎯 Take Profit: $2385.50
⚖️ R:R Ratio: 2.0
📊 Confidence: 78%
📦 Lot Size: 0.05

🔍 Confluence (5 factors):
  ✓ Parabolic SAR flipped bullish (SAR: 2363.20)
  ✓ EMA 20/50/100/200 stack confirmed
  ✓ RSI 58.2 in buy zone (40-65)
  ✓ MACD histogram bullish and rising
  ✓ Active trading session (London/NY)

🕐 2025-05-16 08:30 UTC
```

**Order Executed ✅**
```
✅ ORDER EXECUTED
━━━━━━━━━━━━━━━━━━━━
Action: BUY
Symbol: XAUUSD
Volume: 0.05 lots
Price: $2365.50
SL: $2350.50
TP: $2385.50
Order ID: CHAST_1747382400000

🕐 08:30:15 UTC
```

**Trade Closed WIN 💚**
```
💚 TRADE CLOSED — WIN
━━━━━━━━━━━━━━━━━━━━
Direction: BUY
Symbol: XAUUSD
Volume: 0.05 lots
Entry: $2365.50
Exit: $2385.50
P&L: +$100.00

🕐 09:45:22 UTC
```

**Trade Closed LOSS 💔**
```
💔 TRADE CLOSED — LOSS
━━━━━━━━━━━━━━━━━━━━
Direction: SELL
Symbol: XAUUSD
Volume: 0.05 lots
Entry: $2370.00
Exit: $2350.50
P&L: -$97.50

🕐 10:30:00 UTC
```

**Risk Alert 🛡️**
```
🛡️ RISK ALERT
━━━━━━━━━━━━━━━━━━━━
Type: Max Drawdown Breached
Current: 7.50%
Threshold: 10.00%

🕐 2025-05-16 14:00 UTC
```

**Error 🚨**
```
🚨 ERROR
━━━━━━━━━━━━━━━━━━━━
Order rejected — price mismatch!

Signal entry: $2365.50
Live FIX price: $2385.00
Deviation: 0.8%
Threshold: 1%

Context: BUY 0.05 lots

🕐 2025-05-16 08:30 UTC
```

**System Status 🟢/🔴**
```
🟢 CHASTIEFOL ONLINE
━━━━━━━━━━━━━━━━━━━━
Status: online
Time: 2025-05-16 08:00:00 UTC
Mode: XAUUSD Automated Trading

Chastiefol v1.0
```

---

### Page 8: Dashboard (Monitoring)

**Real-time Metrics**
- Balance & Equity (with floating P&L)
- Open positions count
- Win rate (session/day/all-time)
- Max drawdown current %
- Trading status (Allowed/Halted)

**Open Trades Table**
| Symbol | Side | Entry | SL | TP | Lot | P&L | Duration |
|--------|------|-------|----|----|-----|-----|----------|
| XAUUSD | BUY | 2365.50 | 2350.50 | 2385.50 | 0.05 | +$22.50 | 45m |

**Signal History Table**
| Time | Action | Symbol | Entry | Confidence | Status |
|------|--------|--------|-------|------------|--------|
| 08:30 | BUY | XAUUSD | 2365.50 | 78% | Filled |
| 07:15 | SELL | XAUUSD | 2372.00 | 65% | Rejected |

**Equity Curve Chart**
- Line chart showing balance + equity over time
- Drawdown visualization
- Win/loss markers

**Component Status**
| Component | Status | Details |
|-----------|--------|---------|
| FIX Connection | 🟢 Online | Price + Trade connected |
| Data Feed | 🟡 Degraded | TwelveData rate limited |
| Telegram | 🟢 Connected | Bot active |
| Webhook | 🟢 Listening | Port 8080 |

---

### Page 9: Trading Sessions

**Session Schedule (UTC)**
| Session | Time | Probability Boost |
|---------|------|-------------------|
| London Open Killzone | 07:00-09:00 | High |
| New York Open Killzone | 12:00-14:00 | High |
| London Close | 15:00-17:00 | Medium |
| Asian Range | 00:00-05:00 | Low |

**Active Session Indicator**
- Show current session name and time remaining
- Probability multiplier applied to confluence scoring

---

### Page 10: Setup Guide

**Prerequisites**
- Python 3.10+
- cTrader demo account (or live)
- API keys: TwelveData / AlphaVantage / GoldAPI (optional)

**Installation Steps**
1. Clone repo: `git clone https://github.com/fataakromulmuttaqin/Chastiefol`
2. Install deps: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`
4. Fill in API keys and broker credentials
5. Run: `python chastiefol_main.py`

**cTrader Setup**
- MCP: Get access token from cTrader dashboard
- FIX: Use demo credentials `demo.ctrader.XXXXXX` + FIX password
- Symbol ID for XAUUSD: 41 (verify with broker)

**TradingView Integration**
1. Create alert in TradingView
2. Set webhook URL: `https://your-domain.com/webhook`
3. Add HMAC secret to header `X-Webhook-Secret`
4. Payload format per API reference

---

### Page 11: Troubleshooting

**Common Issues**

| Issue | Cause | Solution |
|-------|-------|----------|
| Order rejected: "Symbol must be numeric" | Using string symbol instead of numeric ID | Set `FIX_SYMBOL_MAP={"XAUUSD":"41"}` in .env |
| Order rejected: "Invalid MsgType" | SL/TP in 35=D message | Use `modify_position()` (35=AM) after fill |
| MARKET_CLOSED error | Market closed (weekend/holiday) | Wait for market open |
| Price validation failed | Stale data feed prices | FIX price now primary fallback |
| Twelvedata rate limited | Daily 800 credit limit | Wait for reset or upgrade plan |
| MCP 401 Unauthorized | Missing "Bearer " prefix | Token must be `f"Bearer {token}"` |

---

## 5. Component Inventory

### Navigation
- Sticky top navbar
- Logo left, links center, status badge right
- Mobile: hamburger menu

### Cards
- Dark background (#12121A)
- 1px border (#2A2A3A)
- 12px border-radius
- Hover: subtle glow with gold accent
- States: default, hover, loading (skeleton)

### Buttons
- Primary: Gold background, black text
- Secondary: Transparent, gold border
- Danger: Red background
- States: default, hover, active, disabled, loading

### Tables
- Dark rows alternating (#12121A / #0A0A0F)
- Gold accent on hover row
- Sortable columns
- Pagination

### Charts
- Dark theme with gold/green/red data colors
- Tooltip on hover
- Responsive

### Status Badges
- Online: Green dot + "Online"
- Offline: Red dot + "Offline"
- Degraded: Yellow dot + "Degraded"

### Forms
- Dark inputs (#1A1A26)
- Gold focus ring
- Error state: red border + message below

---

## 6. Technical Approach

**Frontend:** React 18 + Vite + Tailwind CSS

**Backend API:** Existing webhook server (aiohttp, port 8080) — serves as API backend. Frontend makes requests to `/api/*` which are reverse-proxied to port 8080.

**Data Flow:**
```
Frontend (React) → Nginx/Caddy reverse proxy → Webhook Server (port 8080)
                                    ↓
                              /api/* requests handled alongside webhook endpoints
```

**Monitoring:**
- Live status from `/health` endpoint
- Signal history from `/signals`
- Price data from FIX connection (via webhook server state)

**Authentication:** HMAC-SHA256 for webhook, no auth needed for dashboard public pages

---

## 7. File Structure

```
Chastiefol/
├── SPEC.md                          ← This file
├── README.md                        ← Existing README
├── chastiefol_main.py               ← Main entry point
├── .env                             ← Configuration (gitignored)
├── requirements.txt                 ← Python dependencies
├── Agent/                           ← Agent persona
├── Analysis/                        ← Trading engine (SMC + TA)
├── Connector/
│   ├── ctrader_mcp.py              ← cTrader REST MCP
│   └── ctrader_fix.py               ← cTrader FIX 4.4
├── Dashboard/
│   └── frontend/                    ← React + Vite frontend
│       ├── src/
│       │   ├── components/
│       │   ├── pages/
│       │   ├── hooks/
│       │   └── styles/
│       ├── package.json
│       └── vite.config.js
├── DataFeed/                        ← Multi-provider data feed
├── LLM/                             ← ReAct AI agent
├── Notification/
│   └── telegram_notifier.py        ← Telegram bot
├── PineScript/                      ← TradingView strategies
├── Risk/                            ← Position sizing, drawdown
├── Session Filter/                  ← Session-based filtering
└── Webhook/
    └── webhook_listener.py         ← aiohttp webhook server
```

---

## 8. Key Design Decisions

1. **No database** — Project is stateless; data flows through memory + JSON files
2. **FIX primary for price** — Live broker price is authoritative for validation
3. **Dual execution paths** — MCP or FIX depending on preference/capability
4. **Price sanity check** — Orders rejected if entry deviates >1% from live FIX price
5. **SL/TP post-fill** — SL/TP set via `modify_position()` (35=AM) after order fill, not in initial order
6. **Numeric symbol IDs** — cTrader FIX requires numeric IDs (XAUUSD = 41), not string names