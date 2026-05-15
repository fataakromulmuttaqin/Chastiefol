'use strict';
/**
 * scripts/evolve-thresholds.js
 * Analyse closed trade performance and suggest config adjustments.
 * Run: node scripts/evolve-thresholds.js
 * Requires at least 5 closed trades in performance.json.
 */
require('dotenv').config();
const { getPerfSummary } = require('../lessons');
const { cfg, USER_CONFIG_PATH } = require('../config');
const { agentLoop } = require('../agent');
const fs = require('fs');

(async () => {
  const perf = getPerfSummary();
  if (perf.trades < 5) {
    console.log(`Need at least 5 closed trades. Currently: ${perf.trades}`);
    process.exit(0);
  }

  console.log('📊 Current performance:', perf);
  console.log('\n🤖 Asking agent to evolve thresholds...\n');

  const prompt = `
You are a risk manager reviewing gold trading performance.
Current config:
- stopLossPips: ${cfg.stopLossPips}
- takeProfitRatio: ${cfg.takeProfitRatio}
- riskPerTradePct: ${cfg.riskPerTradePct}
- maxSpreadPips: ${cfg.maxSpreadPips}
- screeningIntervalMin: ${cfg.screeningIntervalMin}

Performance data:
${JSON.stringify(perf, null, 2)}

Suggest 2-4 specific config changes to improve performance.
For each change, call update_config with the exact key and new value.
Then explain your reasoning.
  `.trim();

  try {
    const result = await agentLoop({ role: 'GENERAL', userMessage: prompt });
    console.log('\n── EVOLUTION RESULT ──\n');
    console.log(result);
  } catch (e) {
    console.error('Error:', e.message);
  }
})();
