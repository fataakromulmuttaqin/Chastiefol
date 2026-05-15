'use strict';
require('dotenv').config();
const fs = require('fs');
const path = require('path');

const USER_CONFIG_PATH = path.join(__dirname, 'user-config.json');

function loadUserConfig() {
  if (!fs.existsSync(USER_CONFIG_PATH)) {
    console.warn('[config] user-config.json not found – using defaults. Run: npm run setup');
    return {};
  }
  try {
    return JSON.parse(fs.readFileSync(USER_CONFIG_PATH, 'utf8'));
  } catch (e) {
    console.error('[config] Failed to parse user-config.json:', e.message);
    return {};
  }
}

function buildConfig() {
  const u = loadUserConfig();

  return {
    // ── Identity ────────────────────────────────────────────────────
    symbol:                u.symbol                ?? 'XAUUSD',
    dryRun:                process.env.DRY_RUN === 'true' || u.dryRun === true,

    // ── LLM ─────────────────────────────────────────────────────────
    openrouterApiKey:      process.env.OPENROUTER_API_KEY ?? '',
    screeningModel:        u.screeningModel        ?? 'google/gemini-2.5-flash-preview',
    managementModel:       u.managementModel       ?? 'google/gemini-2.5-flash-preview',
    generalModel:          u.generalModel          ?? 'google/gemini-2.5-flash-preview',

    // ── cTrader ─────────────────────────────────────────────────────
    ctrader: {
      clientId:     process.env.CTRADER_CLIENT_ID     ?? '',
      clientSecret: process.env.CTRADER_CLIENT_SECRET ?? '',
      accessToken:  process.env.CTRADER_ACCESS_TOKEN  ?? '',
      accountId:    parseInt(process.env.CTRADER_ACCOUNT_ID ?? '0', 10),
      host:         process.env.CTRADER_HOST ?? 'demo.ctraderapi.com',
      port:         parseInt(process.env.CTRADER_PORT ?? '5035', 10),
    },

    // ── Telegram ─────────────────────────────────────────────────────
    telegramToken:  process.env.TELEGRAM_BOT_TOKEN ?? '',
    telegramChatId: process.env.TELEGRAM_CHAT_ID   ?? '',
    telegramReportManage: u.telegramReportAfterEveryManageCycle ?? true,
    telegramReportScreen: u.telegramReportAfterEveryScreenCycle ?? true,

    // ── Market data ──────────────────────────────────────────────────
    twelveDataKey: process.env.TWELVE_DATA_API_KEY ?? '',

    // ── Risk management ──────────────────────────────────────────────
    riskPerTradePct:    u.riskPerTradePct    ?? 1.0,
    maxOpenTrades:      u.maxOpenTrades      ?? 3,
    minFreeMarginUsd:   u.minFreeMarginUsd   ?? 500,
    defaultLots:        u.defaultLots        ?? 0.01,
    maxLots:            u.maxLots            ?? 0.10,
    maxDailyLossPct:    u.maxDailyLossPct    ?? 3.0,
    maxWeeklyLossPct:   u.maxWeeklyLossPct   ?? 6.0,

    // ── Trade parameters ─────────────────────────────────────────────
    stopLossPips:       u.stopLossPips       ?? 20,
    takeProfitRatio:    u.takeProfitRatio    ?? 2.0,
    trailingStopPips:   u.trailingStopPips   ?? 15,
    minAtrPips:         u.minAtrPips         ?? 8,
    maxSpreadPips:      u.maxSpreadPips      ?? 0.5,

    // ── Timeframes ────────────────────────────────────────────────────
    technicalTimeframe: u.technicalTimeframe ?? 'H1',
    confirmTimeframe:   u.confirmTimeframe   ?? 'H4',
    allowedSessions:    u.allowedSessions    ?? ['london', 'newyork', 'overlap'],

    // ── Scheduling ───────────────────────────────────────────────────
    screeningIntervalMin:   u.screeningIntervalMin   ?? 15,
    managementIntervalMin:  u.managementIntervalMin  ?? 5,
  };
}

// Export as live object (re-read on every access so hot-reload works)
const cfg = buildConfig();

function reload() {
  const fresh = buildConfig();
  Object.assign(cfg, fresh);
}

module.exports = { cfg, reload, USER_CONFIG_PATH };
