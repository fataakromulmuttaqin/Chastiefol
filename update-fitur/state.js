'use strict';
const fs   = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, 'state.json');

const defaultState = {
  openPositions:   [],   // { positionId, symbol, direction, lots, entryPrice, sl, tp, openTime, label }
  closedToday:     [],   // { positionId, pnlUsd, closeTime }
  dailyPnlUsd:     0,
  weeklyPnlUsd:    0,
  accountEquity:   0,
  lastPriceUpdate: null,
  currentPrice:    null,
  currentSpread:   null,
  sessionStart:    new Date().toISOString(),
};

let state = { ...defaultState };

function load() {
  if (fs.existsSync(STATE_FILE)) {
    try {
      const raw = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      // Reset daily/weekly if date changed
      const today = new Date().toDateString();
      if (raw._savedDate !== today) {
        raw.closedToday = [];
        raw.dailyPnlUsd = 0;
        raw._savedDate  = today;
      }
      Object.assign(state, raw);
    } catch (_) {}
  }
}

function save() {
  try {
    const toWrite = { ...state, _savedDate: new Date().toDateString() };
    fs.writeFileSync(STATE_FILE, JSON.stringify(toWrite, null, 2));
  } catch (_) {}
}

function addPosition(pos) {
  state.openPositions.push(pos);
  save();
}

function removePosition(positionId) {
  state.openPositions = state.openPositions.filter(p => p.positionId !== positionId);
  save();
}

function updatePosition(positionId, updates) {
  const pos = state.openPositions.find(p => p.positionId === positionId);
  if (pos) Object.assign(pos, updates);
  save();
}

function recordClose(positionId, pnlUsd) {
  state.closedToday.push({ positionId, pnlUsd, closeTime: new Date().toISOString() });
  state.dailyPnlUsd  += pnlUsd;
  state.weeklyPnlUsd += pnlUsd;
  removePosition(positionId);
}

function updatePrice(bid, ask) {
  state.currentPrice    = (bid + ask) / 2;
  state.currentSpread   = parseFloat((ask - bid).toFixed(5));
  state.lastPriceUpdate = new Date().toISOString();
  save();
}

function updateEquity(equity) {
  state.accountEquity = equity;
  save();
}

function reset() {
  state = { ...defaultState };
  save();
}

load();

module.exports = {
  state,
  addPosition,
  removePosition,
  updatePosition,
  recordClose,
  updatePrice,
  updateEquity,
  save,
  reset,
};
