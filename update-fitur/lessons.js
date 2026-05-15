'use strict';
const fs   = require('fs');
const path = require('path');

const LESSONS_FILE = path.join(__dirname, 'lessons.json');
const PERF_FILE    = path.join(__dirname, 'performance.json');

// ── Lessons ───────────────────────────────────────────────────────────
function loadLessons() {
  if (!fs.existsSync(LESSONS_FILE)) return [];
  try { return JSON.parse(fs.readFileSync(LESSONS_FILE, 'utf8')); } catch (_) { return []; }
}

function saveLessons(lessons) {
  fs.writeFileSync(LESSONS_FILE, JSON.stringify(lessons, null, 2));
}

function addLesson({ role, lesson, source }) {
  const lessons = loadLessons();
  lessons.push({
    id:        Date.now(),
    role:      role ?? 'GENERAL',
    lesson,
    source:    source ?? 'agent',
    addedAt:   new Date().toISOString(),
  });
  saveLessons(lessons);
  return lessons.length;
}

function clearLessons(role) {
  if (!role) { saveLessons([]); return; }
  saveLessons(loadLessons().filter(l => l.role !== role));
}

function getLessonsForRole(role) {
  return loadLessons().filter(l => !role || l.role === role || l.role === 'GENERAL');
}

function formatLessonsForPrompt(role) {
  const lessons = getLessonsForRole(role);
  if (!lessons.length) return 'No lessons recorded yet.';
  return lessons.map((l, i) => `${i + 1}. [${l.role}] ${l.lesson}`).join('\n');
}

// ── Performance records ───────────────────────────────────────────────
function loadPerf() {
  if (!fs.existsSync(PERF_FILE)) return [];
  try { return JSON.parse(fs.readFileSync(PERF_FILE, 'utf8')); } catch (_) { return []; }
}

function recordTrade({ positionId, direction, lots, entryPrice, exitPrice, pnlUsd, pips, durationMin, label, closedAt }) {
  const perf = loadPerf();
  perf.push({ positionId, direction, lots, entryPrice, exitPrice, pnlUsd, pips, durationMin, label, closedAt: closedAt ?? new Date().toISOString() });
  fs.writeFileSync(PERF_FILE, JSON.stringify(perf, null, 2));
}

function getPerfSummary() {
  const perf = loadPerf();
  if (!perf.length) return { trades: 0 };
  const wins  = perf.filter(p => p.pnlUsd > 0);
  const total = perf.reduce((s, p) => s + p.pnlUsd, 0);
  const avgPips = perf.reduce((s, p) => s + (p.pips ?? 0), 0) / perf.length;
  return {
    trades:   perf.length,
    winRate:  ((wins.length / perf.length) * 100).toFixed(1) + '%',
    totalPnl: total.toFixed(2),
    avgPips:  avgPips.toFixed(1),
    last10:   perf.slice(-10),
  };
}

module.exports = {
  addLesson,
  clearLessons,
  getLessonsForRole,
  formatLessonsForPrompt,
  recordTrade,
  getPerfSummary,
};
