'use strict';
require('dotenv').config();
const cron     = require('node-cron');
const readline = require('readline');
const { cfg }    = require('./config');
const { state, updateEquity } = require('./state');
const { agentLoop }           = require('./agent');
const telegram                = require('./telegram');
const ctrader                 = require('./ctrader');
const { addLesson, formatLessonsForPrompt, getPerfSummary } = require('./lessons');
const log                     = require('./logger');

let nextScreen = Date.now() + cfg.screeningIntervalMin * 60 * 1000;
let nextManage = Date.now() + cfg.managementIntervalMin * 60 * 1000;

function fmtCountdown(ms) {
  if (ms <= 0) return 'now';
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return `${m}m ${s}s`;
}

function showPrompt() {
  const screen = fmtCountdown(nextScreen - Date.now());
  const manage = fmtCountdown(nextManage - Date.now());
  process.stdout.write(`\r[screen: ${screen} | manage: ${manage}] > `);
}

async function runScreener() {
  nextScreen = Date.now() + cfg.screeningIntervalMin * 60 * 1000;
  log.info('orchestrator', '── SCREENER cycle start ──');
  try {
    const report = await agentLoop({ role: 'SCREENER' });
    log.info('orchestrator', 'SCREENER done:', report.slice(0, 300));
    if (cfg.telegramReportScreen) await telegram.sendCycleReport('SCREENER', report);
  } catch (e) {
    log.error('orchestrator', 'SCREENER error:', e.message);
  }
}

async function runManager() {
  nextManage = Date.now() + cfg.managementIntervalMin * 60 * 1000;
  log.info('orchestrator', '── MANAGER cycle start ──');
  try {
    try {
      const info = await ctrader.getAccountInfo();
      updateEquity(info.equity || info.balance || 0);
    } catch (_) {}

    if (state.openPositions.length === 0) {
      log.info('orchestrator', 'No open positions – MANAGER skipped');
      return;
    }
    const report = await agentLoop({ role: 'MANAGER' });
    log.info('orchestrator', 'MANAGER done:', report.slice(0, 300));
    if (cfg.telegramReportManage) await telegram.sendCycleReport('MANAGER', report);
  } catch (e) {
    log.error('orchestrator', 'MANAGER error:', e.message);
  }
}

async function handleCommand(input) {
  const cmd = input.trim();
  if (!cmd) return;

  if (cmd === '/status') {
    try {
      const info = await ctrader.getAccountInfo();
      console.log('\n📊 Account:', JSON.stringify(info, null, 2));
      console.log('Open positions:', state.openPositions.length);
      console.log('Daily PnL:', state.dailyPnlUsd.toFixed(2), 'USD\n');
    } catch (e) { console.log('Error:', e.message); }
    return;
  }
  if (cmd === '/screen')  { console.log('\n🔍 Forcing screener...'); await runScreener(); return; }
  if (cmd === '/manage')  { console.log('\n⚙️  Forcing manager...'); await runManager(); return; }
  if (cmd === '/lessons') { console.log('\n📚 Lessons:\n' + formatLessonsForPrompt() + '\n'); return; }
  if (cmd === '/perf')    { console.log('\n📈 Performance:', JSON.stringify(getPerfSummary(), null, 2), '\n'); return; }
  if (cmd === '/stop')    { console.log('👋 Shutting down...'); process.exit(0); }

  if (cmd.startsWith('/lesson ')) {
    const lesson = cmd.slice(8).trim();
    addLesson({ role: 'GENERAL', lesson, source: 'manual' });
    console.log('✅ Lesson saved\n');
    return;
  }

  if (cmd === '/help') {
    console.log(`
  Commands:
    /status       – account info & open positions
    /screen       – run screener now
    /manage       – run manager now
    /lessons      – show all lessons
    /lesson <txt> – add a manual lesson
    /perf         – win rate & PnL summary
    /stop         – shutdown
    <anything>    – free-form chat with the gold agent\n`);
    return;
  }

  // Free-form → GENERAL agent
  try {
    process.stdout.write('\n⏳ Thinking...\n');
    const reply = await agentLoop({ role: 'GENERAL', userMessage: cmd });
    console.log('\n' + reply + '\n');
  } catch (e) {
    console.log('Error:', e.message, '\n');
  }
}

async function main() {
  console.log(`
╔═══════════════════════════════════════╗
║    CHASTIEFOL — Gold Trading Agent    ║
║  Symbol: ${cfg.symbol}  |  ${cfg.dryRun ? 'DRY RUN  ⚠️ ' : 'LIVE MODE 🔴'}        ║
╚═══════════════════════════════════════╝`);

  if (!cfg.openrouterApiKey) {
    console.error('\n❌ OPENROUTER_API_KEY not set. Copy .env.example → .env and fill it.\n');
    process.exit(1);
  }

  // Initial broker connect
  try {
    const info = await ctrader.getAccountInfo();
    updateEquity(info.equity || info.balance || 0);
    console.log(`✅ cTrader connected | Balance: ${info.balance} ${info.currency} | Equity: ${info.equity}\n`);
  } catch (e) {
    console.warn(`⚠️  cTrader connection failed: ${e.message}`);
    if (!cfg.dryRun) {
      console.error('❌ LIVE mode requires a working cTrader connection. Use DRY_RUN=true to simulate.\n');
      process.exit(1);
    }
    console.log('Continuing in DRY RUN mode without live broker data.\n');
  }

  await telegram.sendMessage(
    `🚀 <b>Chastiefol started</b>\n` +
    `Mode: <b>${cfg.dryRun ? 'DRY RUN' : 'LIVE'}</b> | Symbol: ${cfg.symbol}\n` +
    `Screen: every ${cfg.screeningIntervalMin}min | Manage: every ${cfg.managementIntervalMin}min`
  );

  // Cron: screener
  cron.schedule(`*/${cfg.screeningIntervalMin} * * * *`, runScreener);

  // Cron: manager
  cron.schedule(`*/${cfg.managementIntervalMin} * * * *`, runManager);

  // Telegram polling
  setInterval(async () => {
    try { await telegram.pollMessages(); } catch (_) {}
  }, 3000);

  // First screener run after 5s (allow startup to settle)
  setTimeout(runScreener, 5000);

  // REPL
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  console.log('Type /help for commands. Chat freely with the agent at any time.\n');
  setInterval(showPrompt, 1000);

  rl.on('line', async line => {
    await handleCommand(line);
    showPrompt();
  });
  rl.on('close', () => process.exit(0));
}

main().catch(e => { log.error('main', e.message); process.exit(1); });
