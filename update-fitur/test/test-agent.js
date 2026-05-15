'use strict';
require('dotenv').config();
process.env.DRY_RUN = 'true';

const { agentLoop } = require('../agent');

(async () => {
  console.log('💬 Testing GENERAL agent (dry run)...\n');

  const questions = [
    'What is the current market sentiment for gold?',
    'Should I open a buy or sell position right now?',
    'What are the key levels I should watch today?',
  ];

  for (const q of questions) {
    console.log(`\nQ: ${q}`);
    try {
      const ans = await agentLoop({ role: 'GENERAL', userMessage: q });
      console.log('A:', ans.slice(0, 500));
    } catch (e) {
      console.error('Error:', e.message);
    }
  }

  console.log('\n✅ Agent test complete');
})();
