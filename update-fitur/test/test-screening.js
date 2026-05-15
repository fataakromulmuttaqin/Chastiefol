'use strict';
/**
 * test/test-screening.js
 * Dry-run test for the SCREENER agent.
 * Run: npm run test:screen
 */
require('dotenv').config();
process.env.DRY_RUN = 'true';

const { agentLoop } = require('../agent');
const log = require('../logger');

(async () => {
  console.log('🔍 Testing SCREENER agent (dry run)...\n');
  try {
    const result = await agentLoop({
      role: 'SCREENER',
      userMessage: 'Run a full screening cycle and explain your reasoning step by step.',
    });
    console.log('\n── SCREENER OUTPUT ──\n');
    console.log(result);
    console.log('\n✅ Screener test complete');
  } catch (e) {
    log.error('test', e.message);
    console.error('❌ Test failed:', e.message);
    process.exit(1);
  }
})();
