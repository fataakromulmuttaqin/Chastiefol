'use strict';
/**
 * tools/executor.js
 * Safety pipeline + dispatcher for all agent tool calls.
 *
 * Flow:
 *   LLM requests tool → executor.run(name, args)
 *     → if WRITE_TOOL: run safety checks
 *     → dispatch to implementation
 *     → post-execution: telegram notify, state sync, lesson record
 */

const { cfg, reload, USER_CONFIG_PATH } = require('../config');
const { state, addPosition, removePosition, updatePosition, recordClose, updateEquity } = require('../state');
const { addLesson, getPerfSummary, recordTrade } = require('../lessons');
const ctrader = require('../ctrader');
const telegram = require('../telegram');
const log      = require('../logger');
const { WRITE_TOOLS } = require('./definitions');
const fs  = require('fs');

// ── Session guard (prevent infinite retry) ────────────────────────────
const onceDone = new Set();

// ── Helper: current session ───────────────────────────────────────────
function getSessionInfo() {
  const now = new Date();
  const utcH = now.getUTCHours();
  const utcM = now.getUTCMinutes();
  const total = utcH * 60 + utcM;

  const sessions = [];
  if (total >= 0   && total < 480)  sessions.push('asia');
  if (total >= 480 && total < 960)  sessions.push('london');
  if (total >= 840 && total < 1080) sessions.push('overlap');
  if (total >= 840 && total < 1320) sessions.push('newyork');
  if (!sessions.length) sessions.push('offhours');

  return { utcTime: now.toUTCString(), sessions, utcHour: utcH };
}

// ── Safety checks for WRITE tools ────────────────────────────────────
async function runSafetyChecks(toolName, args) {
  if (toolName !== 'open_trade') return null; // only open_trade needs pre-checks

  const errors = [];

  // 1. Daily loss circuit breaker
  const maxDailyLoss = cfg.maxDailyLossPct / 100 * state.accountEquity;
  if (state.dailyPnlUsd < -maxDailyLoss) {
    errors.push(`Daily loss limit hit (${state.dailyPnlUsd.toFixed(2)} USD / max ${maxDailyLoss.toFixed(2)} USD). No new trades today.`);
  }

  // 2. Max open trades
  if (state.openPositions.length >= cfg.maxOpenTrades) {
    errors.push(`Max open trades reached (${state.openPositions.length}/${cfg.maxOpenTrades}).`);
  }

  // 3. Free margin
  if (state.accountEquity > 0 && state.accountEquity < cfg.minFreeMarginUsd) {
    errors.push(`Free margin too low (${state.accountEquity.toFixed(2)} USD < min ${cfg.minFreeMarginUsd} USD).`);
  }

  // 4. Lot size cap
  if (args.lots > cfg.maxLots) {
    errors.push(`Requested lots (${args.lots}) exceeds maxLots (${cfg.maxLots}). Capped.`);
    args.lots = cfg.maxLots;
  }

  // 5. Spread check
  const spread = state.currentSpread ?? 999;
  if (spread > cfg.maxSpreadPips) {
    errors.push(`Spread too high (${spread} > max ${cfg.maxSpreadPips} pips). Skip trade.`);
  }

  // 6. Session check
  const { sessions } = getSessionInfo();
  const allowed = cfg.allowedSessions;
  const inAllowed = sessions.some(s => allowed.includes(s));
  if (!inAllowed) {
    errors.push(`Current session (${sessions.join(',')}) not in allowed sessions (${allowed.join(',')}).`);
  }

  // 7. Require SL
  if (!args.sl) {
    errors.push('Stop loss is required. Never trade without SL.');
  }

  return errors.length ? errors : null;
}

// ── Tool implementations ──────────────────────────────────────────────
const toolMap = {

  get_market_snapshot: async ({ timeframe } = {}) => {
    const tf = timeframe ?? cfg.technicalTimeframe;
    const [price, tech] = await Promise.all([
      ctrader.getCurrentPrice(cfg.symbol),
      ctrader.getTechnicalData(cfg.symbol, tf),
    ]);
    return { price, technical: tech, symbol: cfg.symbol, timeframe: tf };
  },

  get_ohlc: async ({ timeframe, bars } = {}) => {
    return ctrader.getOHLC(cfg.symbol, timeframe ?? cfg.technicalTimeframe, bars ?? 50);
  },

  get_session_info: async () => getSessionInfo(),

  get_account_status: async () => {
    const info = await ctrader.getAccountInfo();
    updateEquity(info.equity || info.balance || 0);
    return {
      ...info,
      openPositions: state.openPositions.length,
      dailyPnlUsd:  state.dailyPnlUsd,
      weeklyPnlUsd: state.weeklyPnlUsd,
      dryRun:       cfg.dryRun,
    };
  },

  get_open_positions: async () => {
    // Sync from broker
    let brokerPositions = [];
    try {
      brokerPositions = await ctrader.getOpenPositions();
    } catch (e) {
      log.warn('executor', 'Could not fetch positions from broker:', e.message);
    }
    return { positions: brokerPositions.length ? brokerPositions : state.openPositions, count: brokerPositions.length };
  },

  open_trade: async (args) => {
    const result = await ctrader.placeMarketOrder({
      symbol:    cfg.symbol,
      direction: args.direction,
      lots:      args.lots,
      sl:        args.sl,
      tp:        args.tp,
      label:     args.label ?? 'CHASTIEFOL',
    });

    if (!result.isDryRun) {
      const posId = String(result?.order?.positionId ?? result?.positionId ?? `live-${Date.now()}`);
      addPosition({
        positionId: posId,
        symbol:     cfg.symbol,
        direction:  args.direction,
        lots:       args.lots,
        entryPrice: state.currentPrice,
        sl:         args.sl,
        tp:         args.tp,
        openTime:   new Date().toISOString(),
        label:      args.label ?? '',
      });
      await telegram.sendMessage(
        `🟢 <b>TRADE OPENED</b>\n` +
        `${args.direction} ${args.lots} lots ${cfg.symbol}\n` +
        `Entry: ~${state.currentPrice}\nSL: ${args.sl}  TP: ${args.tp}\n` +
        `Label: ${args.label ?? '-'}\n` +
        (cfg.dryRun ? '⚠️ <i>DRY RUN</i>' : '')
      );
    } else {
      await telegram.sendMessage(`🔵 <b>[DRY RUN]</b> Would open: ${args.direction} ${args.lots} lots ${cfg.symbol} SL=${args.sl} TP=${args.tp}`);
    }

    return result;
  },

  close_trade: async (args) => {
    const pos = state.openPositions.find(p => p.positionId === args.positionId);
    const result = await ctrader.closePosition(args.positionId, args.lots);

    if (pos) {
      const pnlUsd = pos.pnlUsd ?? 0;
      const pips   = args.direction === 'BUY'
        ? (state.currentPrice - pos.entryPrice) / 0.01
        : (pos.entryPrice - state.currentPrice) / 0.01;

      recordClose(args.positionId, pnlUsd);
      recordTrade({
        positionId: args.positionId,
        direction:  pos.direction,
        lots:       pos.lots,
        entryPrice: pos.entryPrice,
        exitPrice:  state.currentPrice,
        pnlUsd,
        pips:       parseFloat(pips.toFixed(1)),
        durationMin: Math.round((Date.now() - new Date(pos.openTime).getTime()) / 60000),
        label:      pos.label,
      });

      await telegram.sendMessage(
        `🔴 <b>TRADE CLOSED</b>\n` +
        `${pos.direction} ${pos.lots} lots ${cfg.symbol}\n` +
        `PnL: ${pnlUsd >= 0 ? '+' : ''}${pnlUsd.toFixed(2)} USD\n` +
        `Reason: ${args.reason ?? 'agent decision'}\n` +
        (cfg.dryRun ? '⚠️ <i>DRY RUN</i>' : '')
      );
    }

    return result;
  },

  modify_trade: async (args) => {
    const result = await ctrader.modifyPosition(args.positionId, { sl: args.sl, tp: args.tp });
    if (args.sl || args.tp) {
      updatePosition(args.positionId, { sl: args.sl, tp: args.tp });
      log.info('executor', `Modified ${args.positionId}: SL=${args.sl} TP=${args.tp} – ${args.reason ?? ''}`);
    }
    return result;
  },

  add_lesson: async (args) => {
    const count = addLesson({ role: args.role, lesson: args.lesson, source: args.source });
    return { saved: true, totalLessons: count };
  },

  get_performance_summary: async () => getPerfSummary(),

  update_config: async ({ key, value }) => {
    const allowed = ['stopLossPips','takeProfitRatio','trailingStopPips','riskPerTradePct',
                     'maxOpenTrades','maxLots','defaultLots','maxDailyLossPct','maxSpreadPips',
                     'screeningIntervalMin','managementIntervalMin'];
    if (!allowed.includes(key)) return { error: `Key "${key}" not updatable at runtime.` };

    // Persist to user-config.json
    let userCfg = {};
    try { userCfg = JSON.parse(fs.readFileSync(USER_CONFIG_PATH, 'utf8')); } catch (_) {}
    userCfg[key] = value;
    fs.writeFileSync(USER_CONFIG_PATH, JSON.stringify(userCfg, null, 2));
    reload();

    log.info('executor', `Config updated: ${key} = ${value}`);
    return { updated: key, newValue: value };
  },
};

// ── Main dispatcher ───────────────────────────────────────────────────
async function run(toolName, args = {}) {
  log.debug('executor', `→ ${toolName}`, args);

  // Safety pipeline for write tools
  if (WRITE_TOOLS.has(toolName)) {
    const errors = await runSafetyChecks(toolName, args);
    if (errors) {
      const msg = `Safety check failed for ${toolName}:\n${errors.join('\n')}`;
      log.warn('executor', msg);
      return { error: msg };
    }

    // Once-per-session guard for open_trade (prevents hammering on retry)
    const guardKey = `${toolName}:${args.label ?? ''}:${args.direction ?? ''}`;
    if (toolName === 'open_trade' && onceDone.has(guardKey)) {
      return { error: `Already attempted ${toolName} with this label this session. Skipping duplicate.` };
    }
    if (toolName === 'open_trade') onceDone.add(guardKey);
  }

  const fn = toolMap[toolName];
  if (!fn) return { error: `Unknown tool: ${toolName}` };

  try {
    const result = await fn(args);
    log.debug('executor', `← ${toolName}`, result);
    return result ?? { ok: true };
  } catch (e) {
    log.error('executor', `Tool ${toolName} threw:`, e.message);
    return { error: e.message };
  }
}

module.exports = { run };
