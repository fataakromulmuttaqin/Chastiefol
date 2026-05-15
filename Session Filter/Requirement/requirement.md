# Requirements - Automated XAUUSD Trading System (cTrader + TradingView)

## 1. Functional Requirements (Detail)

### 1.1 Signal Generation (TradingView)
- Indikator yang digunakan:
  - Parabolic SAR (default setting: 0.02 / 0.2 atau sesuai user).
  - EMA 20, EMA 50, EMA 100, EMA 200 (close price).
- Logika sinyal contoh (bisa diubah):
  - **BUY**: PSAR di bawah harga + harga > EMA20 > EMA50 > EMA100 > EMA200 (trend bull kuat).
  - **SELL**: PSAR di atas harga + harga < EMA20 < EMA50 < EMA100 < EMA200 (trend bear kuat).
  - **CLOSE**: PSAR flip atau harga cross EMA200.
- Setiap alert mengirim JSON via webhook:
  ```json
  {
    "action": "BUY"|"SELL"|"CLOSE",
    "symbol": "XAUUSD",
    "volume": 0.01,
    "sl_pips": 50,
    "tp_pips": 150,
    "comment": "PSAR_EMA_Strategy"
  }
  
  # Technical Requirements

## 1. Environment
- **Operating System:** Ubuntu 22.04 LTS or higher / Windows 10/11 (WSL2 recommended).
- **Runtime:** Python 3.10+ or Node.js 20 LTS.
- **Memory:** Minimum 2GB RAM (4GB recommended for local LLM inference).

## 2. Dependencies & Frameworks
- **AI/LLM:** LangChain or Haystack for agent orchestration.
- **Database:** SQLite or PostgreSQL (optional for history logging).
- **Financial APIs:** 
  - TwelveData / ALpha Vantage / GoldAPI.io / TVC for price feeds.
  - NewsAPI for sentiment analysis.
- **AI Models:** Support for Anthropic, Openrouter, Minimax, Codex, Opencode, OpenAI, Groq, or local models via Ollama.

## 3. Installation Requirements
- **Package Manager:** `pip` or `npm`.
- **Environment Management:** `python-dotenv` for managing secrets.

## 4. External Integrations
- **Telegram Bot API:** For real-time notifications.
- **Solana Web3.js / Anchor:** (If integrating with Gold-backed tokens on Solana like PAXG).
