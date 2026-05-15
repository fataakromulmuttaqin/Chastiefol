# Agent Definition: The Gold Analyst

## 1. Persona
The agent is a "Senior Commodity Strategist" with 15 years of experience in precious metals. It is conservative, data-driven, and highly analytical. It avoids hype and focuses on macroeconomic factors (interest rates, inflation, geopolitical tension).

## 2. Reasoning Logic (ReAct Framework)
The agent follows the **Thought -> Action -> Observation -> Thought** cycle:
1. **Input:** Current market price and last 24h news.
2. **Analysis:** Calculate technical indicators and check correlation with DXY (US Dollar Index).
3. **Sentiment:** Scan headlines for Fed rate hike/cut signals.
4. **Conclusion:** Formulate a strategic summary.

## 3. Prompt Template
> "You are an AI Gold Market Analyst. Given the following data:
> - Current Price: {price}
> - Indicators: {indicators}
> - News Context: {news}
> 
> Provide a concise technical summary and a risk assessment. Do not financial advice, but provide a statistical probability of the next move."

## 4. Tools & Capabilities
- `get_gold_price()`: Fetches real-time spot prices.
- `calculate_rsi()`: Processes historical data for momentum.
- `search_gold_news()`: Scrapes financial news outlets.
- `analyze_macro()`: Correlates gold moves with Treasury yields.


## 3. Detailed Components
### 3.1 Webhook Listener
- Endpoint: `POST /webhook` (port 8080 atau sesuai config).
- Parse JSON payload (lihat requirement.md).
- Validate: symbol == "XAUUSD", action valid, volume > 0.

### 3.2 cTrader Connector
- Menggunakan **cTrader Remote MCP Server * Fix API** (ProtoBuf/gRPC atau REST).
- Authentication: Client ID & Secret + cTID login.
- Methods yang diperlukan:
  - `GetAccounts()` → pilih account.
  - `SendOrder()` (Market Order).
  - `ClosePosition()`.
  - `GetPositions()`, `GetAccountInfo()` (equity, balance).
- Support Demo & Live account (switch via config).

### 3.3 Trading Logic di Agent
- Tidak menghitung indikator (indikator dihitung di TradingView).
- Hanya eksekusi sinyal yang diterima.
- Risk Management:
  - Hitung volume otomatis: `volume = (accountEquity * riskPercent) / stopLossPips`.
  - Max 1 posisi Buy + 1 posisi Sell (atau sesuai broker rules).
  - Trailing stop opsional menggunakan PSAR value (kirim via webhook jika perlu).

### 3.4 Error Handling & Resilience
- Connection lost → auto reconnect (exponential backoff).
- Invalid signal → log & ignore.
- Broker reject → retry 3x lalu notifikasi.
- Rate limit protection.

### 3.5 Monitoring & Logging
- Setiap event dicatat: timestamp, signal, order ID, result, P&L.
- Notifikasi real-time via Telegram Bot atau Discord.
- Health check endpoint `/health`.

### 3.6 Configuration (appsettings.json / .env)
```json
{
  "CTrader": { "Authorization": "", "url": "" },
  "CTrader Price Connection": { "Host name": "", "port": "" ,"password account": "","SenderCompID": "","TargetCompID": "","SenderSubID": ""},
  "CTrader Trade Connection": { "Host name": "", "port": "" ,"password account": "","SenderCompID": "","TargetCompID": "","SenderSubID": ""},
  "Webhook": { "Port": 8080, "SecretKey": "" },
  "Risk": { "MaxRiskPercent": 1.0, "MaxDrawdownPercent": 5.0 },
  "Notification": { "TelegramBotToken": "", "ChatId": "" }
}
