'use strict';
const fs = require('fs');
const path = require('path');

const LOG_FILE = path.join(__dirname, 'chastiefol.log');

function ts() {
  return new Date().toISOString();
}

function write(level, tag, ...args) {
  const msg = args.map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ');
  const line = `[${ts()}] [${level}] [${tag}] ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(LOG_FILE, line + '\n');
  } catch (_) {}
}

module.exports = {
  info:  (tag, ...a) => write('INFO ', tag, ...a),
  warn:  (tag, ...a) => write('WARN ', tag, ...a),
  error: (tag, ...a) => write('ERROR', tag, ...a),
  debug: (tag, ...a) => {
    if (process.env.DEBUG) write('DEBUG', tag, ...a);
  },
  trade: (tag, ...a) => write('TRADE', tag, ...a),
};
