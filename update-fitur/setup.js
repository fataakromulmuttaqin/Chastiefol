'use strict';
/**
 * setup.js
 * Interactive setup wizard. Run with: npm run setup
 * Creates .env and user-config.json from user input.
 */

const readline = require('readline');
const fs       = require('fs');
const path     = require('path');

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

function ask(question, defaultVal = '') {
  return new Promise(resolve => {
    const suffix = defaultVal ? ` [${defaultVal}]` : '';
    rl.question(`${question}${suffix}: `, ans => resolve(ans.trim() || defaultVal));
  });
}

async function main() {
  console.log('\n🟡 Chastiefol Setup Wizard\n');

  // ── .env ──────────────────────────────────────────────────────────
  console.log('── API Keys ─────────────────────────────────────────────');
  const openrouterKey = await ask('OpenRouter API key (sk-or-...)');
  const ctraderClientId     = await ask('cTrader Client ID');
  const ctraderClientSecret = await ask('cTrader Client Secret');
  const ctraderAccessToken  = await ask('cTrader Access Token');
  const ctraderAccountId    = await ask('cTrader Account ID (CTID)');
  const ctraderHost = await ask('cTrader host', 'demo.ctraderapi.com');
  const twelveKey   = await ask('Twelve Data API key (optional, for indicators)', '');
  const tgToken     = await ask('Telegram bot token (optional)', '');
  const dryRun      = await ask('Dry run mode? (true/false)', 'true');

  const envContent = `OPENROUTER_API_KEY=${openrouterKey}
CTRADER_CLIENT_ID=${ctraderClientId}
CTRADER_CLIENT_SECRET=${ctraderClientSecret}
CTRADER_ACCESS_TOKEN=${ctraderAccessToken}
CTRADER_ACCOUNT_ID=${ctraderAccountId}
CTRADER_HOST=${ctraderHost}
CTRADER_PORT=5035
TWELVE_DATA_API_KEY=${twelveKey}
TELEGRAM_BOT_TOKEN=${tgToken}
DRY_RUN=${dryRun}
`;
  fs.writeFileSync(path.join(__dirname, '.env'), envContent);
  console.log('✅ .env created\n');

  // ── user-config.json ──────────────────────────────────────────────
  console.log('── Risk Settings ────────────────────────────────────────');
  const risk    = await ask('Risk per trade (%)', '1.0');
  const maxPos  = await ask('Max concurrent trades', '3');
  const slPips  = await ask('Default stop loss (pips)', '20');
  const tpRatio = await ask('Take profit ratio (R:R)', '2.0');
  const maxLoss = await ask('Max daily loss (%)', '3.0');
  const model   = await ask('LLM model (OpenRouter)', 'google/gemini-2.5-flash-preview');

  const userCfg = {
    symbol:              'XAUUSD',
    dryRun:              dryRun === 'true',
    riskPerTradePct:     parseFloat(risk),
    maxOpenTrades:       parseInt(maxPos, 10),
    stopLossPips:        parseInt(slPips, 10),
    takeProfitRatio:     parseFloat(tpRatio),
    maxDailyLossPct:     parseFloat(maxLoss),
    defaultLots:         0.01,
    maxLots:             0.10,
    screeningIntervalMin: 15,
    managementIntervalMin: 5,
    screeningModel:      model,
    managementModel:     model,
    generalModel:        model,
    maxSpreadPips:       0.5,
    minAtrPips:          8,
    allowedSessions:     ['london', 'newyork', 'overlap'],
    telegramReportAfterEveryManageCycle: true,
    telegramReportAfterEveryScreenCycle: true,
  };

  fs.writeFileSync(path.join(__dirname, 'user-config.json'), JSON.stringify(userCfg, null, 2));
  console.log('✅ user-config.json created\n');

  console.log('🎉 Setup complete! Start the agent with:\n');
  console.log('  npm run dev    ← dry run (recommended first)');
  console.log('  npm start      ← live trading\n');

  rl.close();
}

main().catch(e => { console.error(e.message); process.exit(1); });
