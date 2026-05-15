'use strict';
/**
 * ctrader.js
 * Wrapper for cTrader Open API (REST / OAuth2 endpoints).
 *
 * Docs: https://help.ctrader.com/open-api/
 *
 * cTrader uses OAuth2 for auth. This module:
 *  1. Uses the access token from .env directly (you must obtain it manually or via setup.js)
 *  2. Provides helpers for: account info, market data, placing/closing/modifying orders
 *
 * For production, use the cTrader Fix API or the protobuf TCP API for lower latency.
 * This implementation uses the REST gateway for simplicity and broader compatibility.
 */

const log = require('./logger');
const { cfg } = require('./config');

const BASE_URL = 'https://api.spotware.com';

async function apiFetch(endpoint, options = {}) {
  const fetch = (await import('node-fetch')).default;
  const token = cfg.ctrader.accessToken;
  const url   = `${BASE_URL}${endpoint}${endpoint.includes('?') ? '&' : '?'}access_token=${token}`;

  if (cfg.dryRun && options.method === 'POST') {
    log.debug('ctrader', '[DRY_RUN] would POST', endpoint, options.body);
    return { isDryRun: true };
  }

  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`cTrader API ${res.status}: ${text}`);
  }
  return res.json();
}

// ── Account ───────────────────────────────────────────────────────────
async function getAccountInfo() {
  const data = await apiFetch(`/v2/webserv/traders/${cfg.ctrader.accountId}`);
  return {
    balance:     data?.balance          ?? 0,
    equity:      data?.equity           ?? 0,
    freeMargin:  data?.freeMargin       ?? 0,
    currency:    data?.depositCurrency  ?? 'USD',
    leverage:    data?.leverageInCents  ? data.leverageInCents / 100 : 0,
  };
}

// ── Market data ───────────────────────────────────────────────────────
async function getSymbolInfo(symbol = 'XAUUSD') {
  const data = await apiFetch(`/v2/webserv/symbols?ctidTraderAccountId=${cfg.ctrader.accountId}`);
  const sym  = (data?.symbol ?? []).find(s => s.symbolName === symbol);
  if (!sym) throw new Error(`Symbol ${symbol} not found`);
  return sym;
}

async function getCurrentPrice(symbol = 'XAUUSD') {
  // cTrader REST provides last tick via this endpoint
  const data = await apiFetch(`/v2/webserv/symbols/${symbol}/tick?ctidTraderAccountId=${cfg.ctrader.accountId}`);
  return {
    bid:    data?.bid    ?? 0,
    ask:    data?.ask    ?? 0,
    spread: data?.spread ?? 0,
    time:   data?.time   ?? Date.now(),
  };
}

async function getOHLC(symbol = 'XAUUSD', timeframe = 'H1', bars = 100) {
  // Timeframe map: M1 M5 M15 M30 H1 H4 D1 W1 MN1
  const tfMap = { M1: 1, M5: 5, M15: 15, M30: 30, H1: 60, H4: 240, D1: 1440, W1: 10080, MN1: 43200 };
  const periodicity = tfMap[timeframe] ?? 60;
  const to   = Date.now();
  const from = to - periodicity * bars * 60 * 1000;

  const data = await apiFetch(
    `/v2/webserv/symbols/${symbol}/bars?ctidTraderAccountId=${cfg.ctrader.accountId}` +
    `&periodicity=${periodicity}&fromTimestamp=${from}&toTimestamp=${to}`
  );

  return (data?.bar ?? []).map(b => ({
    time:   new Date(b.timestamp).toISOString(),
    open:   b.open   / 100000,
    high:   b.high   / 100000,
    low:    b.low    / 100000,
    close:  b.close  / 100000,
    volume: b.volume ?? 0,
  }));
}

// ── Open positions ────────────────────────────────────────────────────
async function getOpenPositions() {
  const data = await apiFetch(`/v2/webserv/positions?ctidTraderAccountId=${cfg.ctrader.accountId}`);
  return (data?.position ?? []).map(p => ({
    positionId: String(p.positionId),
    symbol:     p.symbolName,
    direction:  p.tradeSide === 'BUY' ? 'BUY' : 'SELL',
    lots:       p.volume / 100,
    entryPrice: p.price,
    sl:         p.stopLoss    ?? null,
    tp:         p.takeProfit  ?? null,
    pnlUsd:     p.netProfit   ?? 0,
    openTime:   new Date(p.tradeData?.openTimestamp ?? Date.now()).toISOString(),
    label:      p.tradeData?.label ?? '',
  }));
}

// ── Place market order ─────────────────────────────────────────────────
async function placeMarketOrder({ symbol, direction, lots, sl, tp, label = 'CHASTIEFOL' }) {
  const volume = Math.round(lots * 100); // cTrader volume in centilots

  if (cfg.dryRun) {
    log.trade('ctrader', `[DRY_RUN] MARKET ${direction} ${lots} lots ${symbol} SL=${sl} TP=${tp}`);
    return { isDryRun: true, positionId: `DRY-${Date.now()}` };
  }

  const body = {
    ctidTraderAccountId: cfg.ctrader.accountId,
    symbolName:          symbol,
    orderType:           'MARKET',
    tradeSide:           direction,
    volume,
    stopLoss:            sl  ?? undefined,
    takeProfit:          tp  ?? undefined,
    label,
    comment:             'Chastiefol agent',
  };

  const data = await apiFetch('/v2/webserv/orders', { method: 'POST', body });
  log.trade('ctrader', `Market order placed: ${direction} ${lots} ${symbol}`, data);
  return data;
}

// ── Close position ─────────────────────────────────────────────────────
async function closePosition(positionId, lots) {
  if (cfg.dryRun) {
    log.trade('ctrader', `[DRY_RUN] CLOSE position ${positionId}`);
    return { isDryRun: true };
  }

  const body = {
    ctidTraderAccountId: cfg.ctrader.accountId,
    positionId:          parseInt(positionId, 10),
    volume:              lots ? Math.round(lots * 100) : undefined,
  };

  const data = await apiFetch('/v2/webserv/positions/close', { method: 'POST', body });
  log.trade('ctrader', `Position closed: ${positionId}`, data);
  return data;
}

// ── Modify SL/TP ──────────────────────────────────────────────────────
async function modifyPosition(positionId, { sl, tp }) {
  if (cfg.dryRun) {
    log.trade('ctrader', `[DRY_RUN] MODIFY ${positionId} SL=${sl} TP=${tp}`);
    return { isDryRun: true };
  }

  const body = {
    ctidTraderAccountId: cfg.ctrader.accountId,
    positionId:          parseInt(positionId, 10),
    stopLoss:            sl ?? undefined,
    takeProfit:          tp ?? undefined,
  };

  const data = await apiFetch('/v2/webserv/positions/amend', { method: 'POST', body });
  log.trade('ctrader', `Position modified: ${positionId}`, data);
  return data;
}

// ── Technical indicators (via Twelve Data) ────────────────────────────
async function getTechnicalData(symbol = 'XAUUSD', timeframe = 'H1') {
  const key = cfg.twelveDataKey;
  if (!key) return null;

  const fetch = (await import('node-fetch')).default;

  async function tdGet(indicator, params = '') {
    const url = `https://api.twelvedata.com/${indicator}?symbol=${symbol}&interval=${timeframe}&apikey=${key}${params}`;
    const r   = await fetch(url);
    return r.json();
  }

  const [price, rsi, macd, atr, ema50, ema200, bbands] = await Promise.allSettled([
    tdGet('price'),
    tdGet('rsi', '&time_period=14'),
    tdGet('macd', '&fast_period=12&slow_period=26&signal_period=9'),
    tdGet('atr', '&time_period=14'),
    tdGet('ema', '&time_period=50'),
    tdGet('ema', '&time_period=200'),
    tdGet('bbands', '&time_period=20&sd=2'),
  ]);

  function val(r) { return r.status === 'fulfilled' ? r.value : null; }

  return {
    price:  val(price)?.price,
    rsi:    val(rsi)?.values?.[0]?.rsi,
    macd: {
      macd:      val(macd)?.values?.[0]?.macd,
      signal:    val(macd)?.values?.[0]?.macd_signal,
      histogram: val(macd)?.values?.[0]?.macd_hist,
    },
    atr:    val(atr)?.values?.[0]?.atr,
    ema50:  val(ema50)?.values?.[0]?.ema,
    ema200: val(ema200)?.values?.[0]?.ema,
    bbands: {
      upper:  val(bbands)?.values?.[0]?.upper_band,
      middle: val(bbands)?.values?.[0]?.middle_band,
      lower:  val(bbands)?.values?.[0]?.lower_band,
    },
    timeframe,
    fetchedAt: new Date().toISOString(),
  };
}

module.exports = {
  getAccountInfo,
  getSymbolInfo,
  getCurrentPrice,
  getOHLC,
  getOpenPositions,
  placeMarketOrder,
  closePosition,
  modifyPosition,
  getTechnicalData,
};
