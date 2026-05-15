# ⚔️ Chastiefol — AI-Powered XAUUSD Trading Agent

<p align="center">
  <strong>Autonomous Gold Trading System powered by Smart Money Concepts, Technical Analysis & LLM Intelligence</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Pine_Script-v6-orange?logo=tradingview" alt="PineScript">
  <img src="https://img.shields.io/badge/cTrader-MCP_%2B_FIX-green" alt="cTrader">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

---

## 📖 Deskripsi

**Chastiefol** adalah sistem trading otomatis (_automated trading agent_) yang dirancang khusus untuk pair **XAUUSD (Gold/USD)**. Sistem ini menggabungkan:

- **TradingView Pine Script** — Signal generation (Parabolic SAR + EMA alignment)
- **Python Agent** — Middleware yang menerima sinyal, menganalisis pasar dengan AI, dan mengeksekusi order
- **cTrader** — Broker execution via Remote MCP Server & FIX 4.4 API
- **LLM Intelligence** — AI-driven market insight menggunakan ReAct framework (OpenAI/Anthropic/Groq/Ollama)
- **Telegram Notifications** — Real-time alert untuk setiap signal, order, dan risk event

### Nama "Chastiefol"

Terinspirasi dari senjata legendaris dalam anime _Seven Deadly Sins_ — senjata milik King yang bisa berubah bentuk sesuai situasi. Sama seperti agent ini yang adaptif terhadap kondisi pasar.

---

## 🏗️ Arsitektur Sistem

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CHASTIEFOL ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐         ┌──────────────────────────────────────────┐ │
│  │ TradingView  │         │        Chastiefol Agent (Python)         │ │
│  │              │  POST   │                                          │ │
│  │ Pine Script  ├────────►│  Webhook ─► Risk Manager ─► Executor    │ │
│  │ PSAR + EMA   │ /webhook│      │          │              │         │ │
│  └──────────────┘         │      ▼          ▼              ▼         │ │
│                           │  Validator   Drawdown    ┌──────────┐    │ │
│  ┌──────────────┐         │      │       Guard      │ cTrader  │    │ │
│  │  Data Feed   │         │      ▼          │       │ MCP/FIX  │    │ │
│  │              │  API    │  Analysis    Position    └──────────┘    │ │
│  │ TwelveData   ├────────►│  Engine      Sizer           │          │ │
│  │ AlphaVantage │         │  (SMC+TA)       │            ▼          │ │
│  │ GoldAPI      │         │      │          ▼       ┌──────────┐    │ │
│  └──────────────┘         │      ▼      Execute     │ Telegram │    │ │
│                           │  LLM Agent   Order      │ Notifier │    │ │
│  ┌──────────────┐         │  (ReAct)        │       └──────────┘    │ │
│  │   LLM API    │  AI     │      │          │                       │ │
│  │              ├────────►│      ▼          ▼                       │ │
│  │ OpenAI/Groq  │         │  Market     Trade Log                   │ │
│  │ Anthropic    │         │  Insight    + Summary                   │ │
│  └──────────────┘         └──────────────────────────────────────────┘ │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Struktur Proyek

```
Chastiefol/
├── Agent/
│   └── agent.md                 # Definisi persona & arsitektur agent
├── Analysis/
│   └── xauusd_engine.py         # Engine analisis teknikal (SMC + multi-indicator)
├── Connector/
│   ├── __init__.py
│   ├── ctrader_mcp.py           # cTrader Remote MCP Server connector (REST/JSON-RPC)
│   └── ctrader_fix.py           # cTrader FIX 4.4 API connector (TCP/SSL)
├── DataFeed/
│   ├── __init__.py
│   └── data_feed.py             # Multi-provider data feed (TwelveData, AlphaVantage, GoldAPI)
├── LLM/
│   ├── __init__.py
│   └── llm_agent.py             # ReAct AI agent (multi-provider LLM)
├── Notification/
│   ├── __init__.py
│   └── telegram_notifier.py     # Telegram Bot notification service
├── PineScript/
│   ├── chastiefol_xauusd_strategy.pine  # TradingView strategy (v6)
│   └── README.md                # Pine Script documentation
├── Risk/
│   └── risk_manager.py          # Position sizing, drawdown guard, trade management
├── Webhook/
│   └── webhook_listener.py      # HTTP webhook server (aiohttp)
├── Requirement/
│   └── requirement.md           # Functional & technical requirements
├── SRS/
│   └── srs.md                   # Software Requirements Specification
├── Session Filter/
│   ├── Agent/chastiefol_agent.py    # Original orchestrator (paper mode)
│   ├── Backtest/backtester.py       # Walk-forward + Monte Carlo backtester
│   ├── Dashboard/dashboard.html     # Real-time monitoring UI
│   └── Requirement/                 # Session filter specific docs
├── chastiefol_main.py           # 🚀 MAIN ENTRY POINT — Integrated orchestrator
├── .env.example                 # Environment variables template
├── requirements.txt             # Python dependencies
├── LICENSE                      # MIT License
└── README.md                    # This file
```

---

## ⚙️ Requirements

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Ubuntu 20.04 / Windows 10 | Ubuntu 22.04 / WSL2 |
| Python | 3.10+ | 3.11+ |
| RAM | 2 GB | 4 GB |
| Network | Stable internet | Low-latency VPS |
| Disk | 500 MB | 1 GB |

### External Accounts Required

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| [TradingView](https://tradingview.com) | Signal generation (Pine Script) | ✅ Basic (webhook limited) |
| [cTrader](https://ctrader.com) | Order execution (Demo/Live) | ✅ Demo account |
| [TwelveData](https://twelvedata.com) | Real-time price data | ✅ 8 req/min |
| [Alpha Vantage](https://alphavantage.co) | Historical data (backup) | ✅ 25 req/day |
| [GoldAPI](https://goldapi.io) | Spot price (backup) | ✅ Limited |
| [Telegram](https://telegram.org) | Notifications | ✅ Free |
| LLM Provider | AI insights (optional) | Varies |

### Supported LLM Providers

| Provider | Model | Cost |
|----------|-------|------|
| OpenAI | GPT-4o, GPT-4-turbo | Paid |
| Anthropic | Claude 3.5 Sonnet | Paid |
| Groq | Llama 3.1 70B | ✅ Free tier |
| OpenRouter | Multi-model gateway | Varies |
| Ollama | Local Llama/Mistral | ✅ Free (local) |

---

## 🚀 Instalasi

### 1. Clone Repository

```bash
git clone https://github.com/fataakromulmuttaqin/Chastiefol.git
cd Chastiefol
```

### 2. Setup Python Environment

```bash
# Buat virtual environment
python -m venv venv
source venv/bin/activate    # Linux/Mac
# venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Konfigurasi Environment

```bash
# Copy template
cp .env.example .env

# Edit dengan credentials kamu
nano .env   # atau gunakan editor favorit
```

### 4. Isi `.env` — Minimum Configuration

```env
# Mode
AGENT_MODE=hybrid
PAPER_MODE=true

# Data Feed (minimal satu)
TWELVEDATA_API_KEY=your_key_here

# Telegram (opsional tapi recommended)
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_ID=-1001234567890

# Webhook
WEBHOOK_PORT=8080
WEBHOOK_SECRET=your_secret_key
```

### 5. Setup TradingView (Pine Script)

1. Buka **TradingView** → Pine Editor
2. Copy isi file `PineScript/chastiefol_xauusd_strategy.pine`
3. Paste di Pine Editor → **Add to Chart** (pair: XAUUSD, timeframe: H1)
4. Buat Alert:
   - Condition: "Chastiefol XAUUSD [PSAR + EMA]"
   - Webhook URL: `http://YOUR_SERVER_IP:8080/webhook`
   - (Opsional) Header: `X-Webhook-Secret: your_secret_key`

### 6. Jalankan Agent

```bash
python chastiefol_main.py
```

---

## 🔧 Cara Kerja (How It Works)

### Mode Operasi

Chastiefol mendukung **3 mode** yang bisa dikonfigurasi via `.env`:

| Mode | `AGENT_MODE` | Deskripsi |
|------|-------------|-----------|
| **Webhook** | `webhook` | Hanya merespon sinyal dari TradingView. Pasif. |
| **Autonomous** | `autonomous` | Self-scan pasar setiap N detik, generate sinyal sendiri. |
| **Hybrid** | `hybrid` | Keduanya berjalan bersamaan (recommended). |

### Flow: Webhook Mode

```
TradingView Alert → POST /webhook → Validate JSON → Risk Check → Execute Order → Telegram Alert
```

1. **Pine Script** mendeteksi PSAR flip + EMA alignment
2. **Alert** dikirim sebagai JSON via HTTP POST ke Chastiefol webhook
3. **Webhook Listener** memvalidasi payload (action, symbol, volume, SL/TP)
4. **Risk Manager** cek drawdown, daily loss, max open trades
5. **Position Sizer** hitung lot size berdasarkan risk % & equity
6. **cTrader Connector** eksekusi market order (MCP/FIX/Paper)
7. **Telegram Notifier** kirim alert ke chat kamu

### Flow: Autonomous Mode

```
Timer (60s) → Fetch Data → Analysis Engine → Confluence Check → Risk Check → Execute → Notify
```

1. Setiap **60 detik** (configurable), agent fetch OHLCV data
2. **Analysis Engine** menghitung:
   - Market Structure (BOS, CHoCH, Order Blocks, FVG)
   - Technical Indicators (EMA, RSI, MACD, BB, ATR, VWAP)
   - Confluence Score (0–100%)
3. **Session Filter** cek apakah London/NY session aktif
4. Jika confidence > threshold → generate signal
5. Proses selanjutnya sama dengan webhook mode

### Signal Logic (Pine Script)

| Sinyal | Kondisi Utama |
|--------|---------------|
| **BUY** | PSAR flip ke bawah harga + Price > EMA20 > EMA50 > EMA100 > EMA200 |
| **SELL** | PSAR flip ke atas harga + Price < EMA20 < EMA50 < EMA100 < EMA200 |
| **CLOSE** | PSAR flip berlawanan ATAU price cross EMA200 |

### Confirmation Indicators

| Indicator | Bullish | Bearish |
|-----------|---------|---------|
| RSI (14) | 50 < RSI < 70 | 30 < RSI < 50 |
| MACD | Line > Signal & Hist > 0 | Line < Signal & Hist < 0 |
| Bollinger | Price > Middle Band | Price < Middle Band |
| Volume | Above 1.2× MA(20) | Above 1.2× MA(20) |

### Confluence Scoring

Setiap faktor konfirmasi = +1 point. Score range: **0–7**.
Minimum default untuk trigger signal: **3/7**.

---

## 🛡️ Risk Management

| Feature | Default | Configurable |
|---------|---------|-------------|
| Risk per trade | 1% equity | ✅ `RISK_PCT` |
| Max risk per trade | 2% equity | ✅ `MAX_RISK_PCT` |
| Max daily loss | 3% | ✅ `MAX_DAILY_LOSS_PCT` |
| Max drawdown (circuit breaker) | 10% | ✅ `MAX_DRAWDOWN_PCT` |
| Max open trades | 2 | ✅ `MAX_OPEN_TRADES` |
| Stop Loss | ATR × 1.5 | ✅ `sl_atr_mult` |
| Take Profit | SL × 2.0 (R:R) | ✅ `tp_rr` |
| Trailing Stop | PSAR-based | ✅ `use_trailing` |
| Session Filter | London + NY | ✅ Configurable |

### Drawdown Guard Actions

| Level | Action |
|-------|--------|
| 50% of max DD | ⚠️ Warning + reduce position size 50% |
| 75% of max DD | ⚠️ Warning + reduce position size 75% |
| 100% of max DD | 🛑 **CIRCUIT BREAKER** — trading halted |

---

## 📡 Execution Methods

### cTrader Remote MCP Server (Recommended)

```env
EXECUTION_METHOD=mcp
CTRADER_MCP_URL=https://mcp.ctrader.com/trading/mcp
CTRADER_ACCESS_TOKEN=Bearer your_token
CTRADER_ACCOUNT_ID=5820056
```

- REST/JSON-RPC based
- Simpler setup
- Lower latency untuk retail

### cTrader FIX API (Advanced)

```env
EXECUTION_METHOD=fix
FIX_PRICE_HOST=demo-uk-eqx-01.p.c-trader.com
FIX_PRICE_PORT=5211
FIX_TRADE_HOST=demo-uk-eqx-01.p.c-trader.com
FIX_TRADE_PORT=5212
FIX_SENDER_COMP_ID=demo.ctrader.5820056
FIX_TARGET_COMP_ID=cServer
FIX_PASSWORD=your_password
```

- FIX 4.4 protocol over TCP/SSL
- Lowest latency
- Untuk institutional-grade setup

### Paper Trading (Testing)

```env
EXECUTION_METHOD=paper
PAPER_MODE=true
```

- No real money
- Simulated fills at market price
- Perfect untuk development & testing

---

## 📱 Telegram Notifications

### Setup

1. Chat [@BotFather](https://t.me/BotFather) → `/newbot` → dapatkan **Bot Token**
2. Chat [@userinfobot](https://t.me/userinfobot) → dapatkan **Chat ID**
3. Tambahkan ke `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=-1001234567890
```

### Jenis Notifikasi

| Type | Emoji | Trigger |
|------|-------|---------|
| Signal Alert | 🟢/🔴 | New BUY/SELL signal detected |
| Order Executed | ✅ | Order filled at broker |
| Trade Closed | 💚/💔 | Position closed (win/loss) |
| Risk Alert | 🛡️ | Drawdown threshold breached |
| Error | 🚨 | Connection failure, order rejected |
| Daily Summary | 📋 | End of day P&L report |
| System Status | 🟢/🔴 | Agent online/offline |

---

## 🤖 LLM Integration (AI Insights)

### Konfigurasi

```env
LLM_PROVIDER=groq          # openai, anthropic, groq, openrouter, ollama
LLM_API_KEY=your_key
LLM_MODEL=llama-3.1-70b-versatile
LLM_TEMPERATURE=0.3
```

### Capabilities

- **Technical Analysis Interpretation** — Membaca pola indikator dan memberikan insight
- **News Sentiment** — Analisis berita gold, Fed, geopolitik
- **Macro Correlation** — Korelasi DXY, Treasury yields, inflasi
- **Risk Assessment** — Evaluasi risiko berdasarkan volatilitas
- **Trade Recommendation** — BUY/SELL/HOLD dengan probability score

### ReAct Framework

```
Thought: "RSI at 58, MACD bullish cross, EMA aligned bullish..."
Action: get_gold_price
Observation: "Current XAUUSD: $2365.50"
Thought: "Price above all EMAs, PSAR bullish, confluence 5/7..."
Action: search_gold_news
Observation: "Fed signals rate cut, gold demand record high..."
Final Answer: { "bias": "BULLISH", "confidence": 0.78, "recommendation": "BUY" }
```

---

## 🧪 Testing

### Paper Mode (Default)

```bash
# Jalankan dengan synthetic data
PAPER_MODE=true python chastiefol_main.py
```

### Test Webhook Manually

```bash
# Kirim test signal
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your_secret_key" \
  -d '{"action":"BUY","symbol":"XAUUSD","volume":0.01,"sl_pips":150,"tp_pips":300,"comment":"TEST"}'
```

### Health Check

```bash
curl http://localhost:8080/health
```

### View Recent Signals

```bash
curl http://localhost:8080/signals?limit=10
```

---

## 🌐 Deployment (VPS)

### Recommended: Ubuntu VPS

```bash
# 1. Setup
sudo apt update && sudo apt install python3.11 python3.11-venv -y

# 2. Clone & install
git clone https://github.com/fataakromulmuttaqin/Chastiefol.git
cd Chastiefol
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env && nano .env

# 4. Run with systemd (production)
sudo tee /etc/systemd/system/chastiefol.service << EOF
[Unit]
Description=Chastiefol Trading Agent
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python chastiefol_main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable chastiefol
sudo systemctl start chastiefol

# 5. Monitor
sudo journalctl -u chastiefol -f
```

### Docker (Coming Soon)

```bash
docker build -t chastiefol .
docker run -d --env-file .env -p 8080:8080 chastiefol
```

---

## 📊 Endpoints API

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| POST | `/webhook` | Receive TradingView signals |
| GET | `/health` | Health check & status |
| GET | `/signals` | Recent signal history |
| GET | `/status` | Server configuration |

---

## ⚠️ Disclaimer

> **PENTING**: Chastiefol adalah tool untuk automated trading. Trading forex/gold melibatkan risiko kehilangan modal yang signifikan.
>
> - Selalu gunakan **Paper Mode** terlebih dahulu untuk testing
> - Jangan pernah trade dengan uang yang tidak bisa kamu tanggung kehilangannya
> - Past performance is NOT indicative of future results
> - Sistem ini BUKAN financial advice
> - Gunakan demo account sebelum live trading
> - Author tidak bertanggung jawab atas kerugian finansial apapun

---

## 🗺️ Roadmap

- [x] Analysis Engine (SMC + Multi-Indicator)
- [x] Risk Manager (Position Sizing, Drawdown Guard)
- [x] Webhook Listener (TradingView → Agent)
- [x] cTrader Connector (MCP + FIX API)
- [x] Real-Time Data Feed (Multi-provider)
- [x] Telegram Notifications
- [x] LLM Integration (ReAct Agent)
- [x] Pine Script Strategy (PSAR + EMA)
- [x] Backtester (Walk-Forward + Monte Carlo)
- [x] Dashboard (HTML/Chart.js)
- [x] Docker containerization
- [x] Multi-timeframe analysis
- [x] Portfolio mode (multi-pair)
- [x] Web dashboard (React)
- [x] Database persistence (PostgreSQL)
- [x] Automated daily reports
- [x] Strategy optimizer (genetic algorithm)
- [x] Email Alert Parser (free TradingView alternative)
- [x] Soranoo Bridge (redundant signal source)
- [x] Signal Router (deduplication across sources)
- [x] TradingView WebSocket data feed (free, no API key)

---

## 📡 Data Feed Priority (All FREE)

| Priority | Source | Accuracy | Setup |
|----------|--------|----------|-------|
| 1 (Primary) | **cTrader FIX Price Connection** | ⭐⭐⭐⭐⭐ | Demo account + FIX credentials |
| 2 (Secondary) | **TradingView WebSocket** | ⭐⭐⭐⭐ | No key needed — auto-connect |
| 3 (Tertiary) | **TwelveData REST** | ⭐⭐⭐ | Free API key (800 req/day) |

Failover otomatis: jika source #1 disconnect, langsung switch ke #2, dst.

---

## 🆓 Signal Ingestion (Tanpa TradingView Premium)

```
┌────────────────────┐    ┌────────────────────┐
│ 📧 Email Parser    │    │ 🔌 Soranoo Bridge  │
│ (Gmail IMAP Poll)  │    │ (HTTP POST)        │
└────────┬───────────┘    └────────┬───────────┘
         │                          │
         ▼                          ▼
┌────────────────────────────────────────────────┐
│       🔀 Signal Router (Deduplication)         │
│   Same signal → only executed ONCE             │
└────────────────────┬───────────────────────────┘
                     ▼
            Execution Pipeline
```

Atau jalankan **Autonomous Mode** — tidak perlu TradingView sama sekali.

---

## 📜 License

MIT License — See [LICENSE](LICENSE) for details.

---

## 👤 Author

**Fata Akrom Ul Muttaqin**

- GitHub: [@fataakromulmuttaqin](https://github.com/fataakromulmuttaqin)

---

<p align="center">
  <em>Built with ☕ and 📈 — Trade smart, not hard.</em>
</p>
