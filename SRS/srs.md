# Software Requirements Specification (SRS) - Automated Trading System for XAUUSD

## 1. Introduction
### 1.1 Purpose
Dokumen ini mendefinisikan persyaratan software untuk membangun **sistem otomatisasi trading (Automated Trading System)** pada pair **XAUUSD (Gold vs USD)**. Sistem ini menggunakan platform **cTrader** sebagai engine eksekusi order dan **TradingView** sebagai sumber indikator (Parabolic SAR + EMA 20, 50, 100, 200) untuk menghasilkan sinyal trading.

### 1.2 Scope
- Sistem menerima sinyal otomatis dari TradingView melalui webhook.
- Sentiment analysis from news and social media
- Data collection from various financial APIs TwelveData & AlphaVantage (XAU/USD)
- AI-driven technical analysis
- Reporting and alerting via terminal or messaging platform (e.g Telegram)
- Sinyal dihasilkan berdasarkan logika Parabolic SAR dan Exponential Moving Average (EMA 20/50/100/200) pada chart TradingView.
- Eksekusi order (Buy/Sell, close position, SL/TP, risk management) dilakukan melalui **cTrader Remote MCP Server & Fix API**. dengan format Remote MCP Server:
"url": "https://mcp.ctrader.com/trading/mcp",
"headers": {
  "Authorization": "Bearer....."
}
- format Fix API : Price Connection 
Host name: demo-uk-eqx-01.p.c-trader.com
Port: 5211 (SSL), 5201 (Plain text).
Password: (a/c 5820056 password)
SenderCompID: demo.ctrader.5820056
TargetCompID: cServer
SenderSubID: QUOTE

- format Fix API : Trade Connection
Host name: demo-uk-eqx-01.p.c-trader.com
Port: 5212 (SSL), 5202 (Plain text).
Password: (a/c 5820056 password)
SenderCompID: demo.ctrader.5820056
TargetCompID: cServer
SenderSubID: TRADE

- Hanya pair **XAUUSD** yang didukung.
- Fitur utama: signal reception, order execution, position management, logging, error handling, dan monitoring.
- Tidak termasuk: backtesting (dilakukan di TradingView), UI dashboard (opsional di fase berikutnya).

### 1.3 Definitions, Acronyms, and Abbreviations
- **PSAR**: Parabolic Stop and Reverse (indikator trend-following).
- **EMA**: Exponential Moving Average (periode 20, 50, 100, 200).
- **Webhook**: HTTP POST dari TradingView ke Agent.
- **cTrader Remote MCP Server dan Fix API**: Remote MCP Server dan Fix API resmi Spotware untuk trading.
- **cBot**: Automated robot di cTrader (opsional fallback).
- **XAUUSD**: Forex pair Gold/USD.

### 1.4 References
- cTrader Remote MCP Server Documentation (Spotware).
- TradingView Pine Script v6 & Webhook Alert.
- cTrader Automate.

### 1.5 Overview
Sistem terdiri dari 3 komponen utama:
1. TradingView (Pine Script + Alert → Webhook).
2. **Agent** (middleware yang menerima sinyal dan eksekusi via cTrader Remote MCP Server atau Fix API) → lihat agent.md.
3. cTrader (broker account untuk eksekusi live/demo).

## 2. Overall Description
### 2.1 Product Perspective
Menggantikan trading manual dengan sistem fully-automated yang menggabungkan kekuatan charting TradingView dengan eksekusi cepat cTrader dan LLM sebagai decision maker.

### 2.2 Product Functions
- Menerima sinyal BUY/SELL/CLOSE dari TradingView.
- Validasi sinyal (symbol = XAUUSD, risk rules).
- Eksekusi order market dengan volume yang sesuai risk management.
- Manajemen posisi (trailing stop via PSAR jika diinginkan).
- Logging & notifikasi (Telegram/Discord/email).
- set Stop Loss dan Take Profit yang sesuai risk management
- Error recovery & reconnection.

### 2.3 User Classes and Characteristics
- Trader / Developer yang mengatur alert di TradingView.
- Admin sistem yang memonitor agent.

### 2.4 Operating Environment
- Server (VPS/cloud/local): Windows/Linux dengan .NET 8+ atau Node.js/Python.
- Internet stabil & low-latency ke broker cTrader.
- Broker yang mendukung cTrader Remote MCP Server dan Fix API (contoh: IC Markets, FxPro, dll).

### 2.5 Design and Implementation Constraints
- Menggunakan cTrader Remote MCP Server dan Fix API (bukan cBot internal kecuali fallback).
- Pine Script TradingView harus menggunakan `alertcondition()` atau `alert()` dengan format JSON yang jelas.
- Keamanan: API keys, secret, no hard-coded credentials.

## 3. Specific Requirements
### 3.1 Functional Requirements
**FR-01**: TradingView mengirim webhook saat kondisi PSAR + EMA terpenuhi.  
**FR-02**: Agent parse payload JSON (action, symbol, volume, sl, tp, comment).  
**FR-03**: Agent autentikasi & koneksi ke cTrader Remote MCP Server dan Fix API.  
**FR-04**: Eksekusi Buy/Sell market order pada XAUUSD.  
**FR-05**: Close semua posisi jika sinyal CLOSE.  
**FR-06**: Risk management (max risk per trade 1-2%, max drawdown).  
**FR-07**: Logging lengkap ke file/database + notifikasi real-time.  
**FR-08**: Graceful shutdown & reconnect otomatis.
**FR-09**: Signal output provide "buy", "sell" or "hold" recommendations
**FR-10**: AI Insight Generation Use LLM to interpret market data and news
**FR11**: Real-Time Data Ingestion The system must fetch live gold prices with minimal latency.
**FR12**: Technical Indicator Calculation Support for RSI, MACD, and Moving Averages.

### 3.2 Non-Functional Requirements
- **Performance**: Latency < 500ms dari webhook ke order execution.
- **Reliability**: 99.9% uptime, auto-reconnect.
- **Security**: HTTPS, API key encryption, rate limiting.
- **Scalability**: Support 1 pair (XAUUSD) dulu, mudah di-extend.
- **Maintainability**: Clean code, modular, dokumentasi lengkap.
- **Compliance**: Ikuti regulasi broker (no HFT abuse, dll).

### 3.3 Assumptions and Dependencies
- User sudah memiliki akun cTrader + API access (Remote MCP Server & Fix API (cTID).
- User mampu membuat Pine Script sederhana di TradingView.
- Strategi entry/exit (PSAR flip + EMA alignment) didefinisikan di TradingView.

## 4. Appendix
- Contoh Pine Script sederhana (disediakan terpisah).
- Contoh payload webhook JSON.

## 4.1 Non-Functional Requirements
- **Performance:** Analysis should complete within 5 seconds of a data refresh.
- **Scalability:** Ability to handle multiple data sources simultaneously.
- **Security:** Secure management of API keys and environment variables.
