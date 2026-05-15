'use strict';
/**
 * tools/definitions.js
 * JSON Schema definitions for all tools the LLM can call.
 * Format: OpenAI function-calling compatible.
 */

const ALL_TOOLS = [

  // ── Market analysis ──────────────────────────────────────────────
  {
    type: 'function',
    function: {
      name: 'get_market_snapshot',
      description: 'Get current XAU/USD price, spread, technical indicators (RSI, MACD, ATR, EMA50, EMA200, Bollinger Bands) for a given timeframe. Call this first before making any trading decision.',
      parameters: {
        type: 'object',
        properties: {
          timeframe: { type: 'string', enum: ['M5','M15','M30','H1','H4','D1'], description: 'Candle timeframe for indicators.' },
        },
        required: [],
      },
    },
  },

  {
    type: 'function',
    function: {
      name: 'get_ohlc',
      description: 'Fetch recent OHLC candles for XAU/USD. Use to visually inspect price action, identify S/R levels, trend structure.',
      parameters: {
        type: 'object',
        properties: {
          timeframe: { type: 'string', enum: ['M15','M30','H1','H4','D1'], description: 'Candle timeframe.' },
          bars:      { type: 'number', description: 'Number of bars to fetch (max 200).', default: 50 },
        },
        required: [],
      },
    },
  },

  {
    type: 'function',
    function: {
      name: 'get_session_info',
      description: 'Returns current trading session (Asia, London, New York, Overlap) and time in UTC. Gold is most liquid during London–NY overlap.',
      parameters: { type: 'object', properties: {} },
    },
  },

  // ── Account & positions ───────────────────────────────────────────
  {
    type: 'function',
    function: {
      name: 'get_account_status',
      description: 'Get account balance, equity, free margin, open positions count, daily PnL, and risk status.',
      parameters: { type: 'object', properties: {} },
    },
  },

  {
    type: 'function',
    function: {
      name: 'get_open_positions',
      description: 'List all currently open XAU/USD positions with entry price, direction, lots, SL, TP, and current PnL.',
      parameters: { type: 'object', properties: {} },
    },
  },

  // ── Trade execution (WRITE – safety-checked) ───────────────────────
  {
    type: 'function',
    function: {
      name: 'open_trade',
      description: 'Open a market order on XAU/USD. Always call get_market_snapshot first. The executor will run safety checks before executing.',
      parameters: {
        type: 'object',
        properties: {
          direction: { type: 'string', enum: ['BUY', 'SELL'], description: 'Trade direction.' },
          lots:      { type: 'number', description: 'Lot size (e.g. 0.01). Risk per trade is enforced by the executor.' },
          sl:        { type: 'number', description: 'Stop loss price (not pips – absolute price level).' },
          tp:        { type: 'number', description: 'Take profit price (not pips – absolute price level).' },
          label:     { type: 'string', description: 'Short descriptive label for this trade setup (e.g. "H1 breakout above 2320").' },
        },
        required: ['direction', 'lots', 'sl', 'tp'],
      },
    },
  },

  {
    type: 'function',
    function: {
      name: 'close_trade',
      description: 'Close an open position by its positionId. Use when: SL/TP not triggered but market conditions deteriorated, or to take partial profit.',
      parameters: {
        type: 'object',
        properties: {
          positionId: { type: 'string', description: 'Position ID to close.' },
          lots:       { type: 'number', description: 'Lots to close (omit for full close).' },
          reason:     { type: 'string', description: 'Reason for closing (logged for learning).' },
        },
        required: ['positionId'],
      },
    },
  },

  {
    type: 'function',
    function: {
      name: 'modify_trade',
      description: 'Modify SL or TP of an open position. Use to: trail stop, move SL to breakeven, extend/tighten TP.',
      parameters: {
        type: 'object',
        properties: {
          positionId: { type: 'string', description: 'Position ID to modify.' },
          sl:         { type: 'number', description: 'New stop loss price.' },
          tp:         { type: 'number', description: 'New take profit price.' },
          reason:     { type: 'string', description: 'Reason for modification.' },
        },
        required: ['positionId'],
      },
    },
  },

  // ── Learning & memory ─────────────────────────────────────────────
  {
    type: 'function',
    function: {
      name: 'add_lesson',
      description: 'Save a new trading insight or rule to the lessons database. Call after a notable event: winning pattern, losing pattern, false signal.',
      parameters: {
        type: 'object',
        properties: {
          role:   { type: 'string', enum: ['SCREENER', 'MANAGER', 'GENERAL'], description: 'Which agent role this lesson applies to.' },
          lesson: { type: 'string', description: 'The lesson or rule (be specific and actionable).' },
          source: { type: 'string', description: 'What triggered this lesson (e.g. positionId or market event).' },
        },
        required: ['lesson'],
      },
    },
  },

  {
    type: 'function',
    function: {
      name: 'get_performance_summary',
      description: 'Get win rate, total PnL, average pips, and last 10 closed trades. Use to evaluate if strategy is working.',
      parameters: { type: 'object', properties: {} },
    },
  },

  // ── Config ────────────────────────────────────────────────────────
  {
    type: 'function',
    function: {
      name: 'update_config',
      description: 'Update a runtime config value without restarting. Useful to tighten risk or change lot size mid-session.',
      parameters: {
        type: 'object',
        properties: {
          key:   { type: 'string', description: 'Config key to update (e.g. "stopLossPips", "riskPerTradePct", "maxOpenTrades").' },
          value: { description: 'New value.' },
        },
        required: ['key', 'value'],
      },
    },
  },
];

// Role-based tool access
const ROLE_TOOLS = {
  SCREENER: ['get_market_snapshot', 'get_ohlc', 'get_session_info', 'get_account_status', 'open_trade', 'add_lesson', 'get_performance_summary'],
  MANAGER:  ['get_market_snapshot', 'get_ohlc', 'get_session_info', 'get_account_status', 'get_open_positions', 'close_trade', 'modify_trade', 'add_lesson', 'get_performance_summary', 'update_config'],
  GENERAL:  ALL_TOOLS.map(t => t.function.name),
};

// Tools that touch the broker (require safety checks)
const WRITE_TOOLS = new Set(['open_trade', 'close_trade', 'modify_trade']);

function getToolsForRole(role) {
  const names = ROLE_TOOLS[role] ?? ROLE_TOOLS.GENERAL;
  return ALL_TOOLS.filter(t => names.includes(t.function.name));
}

module.exports = { ALL_TOOLS, ROLE_TOOLS, WRITE_TOOLS, getToolsForRole };
