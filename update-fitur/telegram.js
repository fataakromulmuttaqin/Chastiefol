'use strict';
const { cfg }     = require('./config');
const { agentLoop } = require('./agent');
const log           = require('./logger');
const fs            = require('fs');
const path          = require('path');

const CHAT_ID_FILE = path.join(__dirname, '.telegram_chat_id');

let chatId      = cfg.telegramChatId || (fs.existsSync(CHAT_ID_FILE) ? fs.readFileSync(CHAT_ID_FILE, 'utf8').trim() : '');
let lastUpdateId = 0;
let chatHistory  = [];

async function apiFetch(method, body = {}) {
  if (!cfg.telegramToken) return null;
  const fetch = (await import('node-fetch')).default;
  const url   = `https://api.telegram.org/bot${cfg.telegramToken}/${method}`;
  try {
    const res  = await fetch(url, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    return res.json();
  } catch (e) {
    log.error('telegram', 'API error:', e.message);
    return null;
  }
}

async function sendMessage(text, extra = {}) {
  if (!chatId || !cfg.telegramToken) {
    log.debug('telegram', 'No chatId or token – skip message');
    return;
  }
  return apiFetch('sendMessage', {
    chat_id:    chatId,
    text:       text.slice(0, 4096),
    parse_mode: 'HTML',
    ...extra,
  });
}

async function sendCycleReport(role, report) {
  const emoji = role === 'SCREENER' ? '🔍' : role === 'MANAGER' ? '⚙️' : '📊';
  const header = `${emoji} <b>${role} CYCLE REPORT</b>\n<i>${new Date().toUTCString()}</i>\n\n`;
  await sendMessage(header + report);
}

// Poll for incoming messages and respond via GENERAL agent
async function pollMessages() {
  if (!cfg.telegramToken) return;

  const data = await apiFetch('getUpdates', { offset: lastUpdateId + 1, timeout: 10 });
  if (!data?.result?.length) return;

  for (const update of data.result) {
    lastUpdateId = update.update_id;
    const msg = update.message;
    if (!msg?.text) continue;

    // Auto-register chat ID
    if (!chatId) {
      chatId = String(msg.chat.id);
      fs.writeFileSync(CHAT_ID_FILE, chatId);
      log.info('telegram', `Registered chat ID: ${chatId}`);
      await sendMessage('👋 <b>Chastiefol connected!</b> Send me any message to chat with the gold agent.');
    }

    if (String(msg.chat.id) !== chatId) continue;

    const text = msg.text.trim();
    log.info('telegram', `Incoming: ${text}`);

    // Keep last 10 exchanges
    chatHistory.push({ role: 'user', content: text });
    if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);

    await sendMessage('⏳ Processing...');

    try {
      const reply = await agentLoop({
        role:        'GENERAL',
        userMessage: text,
        history:     chatHistory.slice(0, -1),
      });
      chatHistory.push({ role: 'assistant', content: reply });
      await sendMessage(reply);
    } catch (e) {
      log.error('telegram', 'Agent error:', e.message);
      await sendMessage(`❌ Error: ${e.message}`);
    }
  }
}

module.exports = { sendMessage, sendCycleReport, pollMessages };
