'use strict';
/**
 * agent.js
 * Core ReAct agent loop: LLM → tool call → observe → repeat
 *
 * Supports three roles: SCREENER, MANAGER, GENERAL
 * Each role gets a tailored system prompt and scoped tool access.
 */

const OpenAI = require('openai');
const { cfg }              = require('./config');
const { buildSystemPrompt } = require('./prompt');
const { getToolsForRole }   = require('./tools/definitions');
const { run: runTool }      = require('./tools/executor');
const log                   = require('./logger');

// OpenAI SDK pointed at OpenRouter
let _client = null;
function getClient() {
  if (!_client) {
    _client = new OpenAI({
      apiKey:  cfg.openrouterApiKey,
      baseURL: 'https://openrouter.ai/api/v1',
      defaultHeaders: {
        'HTTP-Referer': 'https://github.com/fataakromulmuttaqin/Chastiefol',
        'X-Title':      'Chastiefol Gold Agent',
      },
    });
  }
  return _client;
}

const MAX_ITERATIONS = 10; // prevent infinite loops

/**
 * Run one full agent cycle.
 *
 * @param {object} options
 * @param {'SCREENER'|'MANAGER'|'GENERAL'} options.role
 * @param {string} [options.userMessage]  - extra context or user query
 * @param {Array}  [options.history]      - prior messages (for GENERAL/chat)
 * @returns {Promise<string>} final text output from LLM
 */
async function agentLoop({ role = 'GENERAL', userMessage = null, history = [] }) {
  const client      = getClient();
  const tools       = getToolsForRole(role);
  const systemPrompt = buildSystemPrompt(role);
  const model       = role === 'SCREENER' ? cfg.screeningModel
                    : role === 'MANAGER'  ? cfg.managementModel
                    : cfg.generalModel;

  log.info('agent', `Starting ${role} cycle | model: ${model}`);

  // Build message history
  const messages = [
    { role: 'system', content: systemPrompt },
    ...history,
  ];

  if (userMessage) {
    messages.push({ role: 'user', content: userMessage });
  } else {
    // Default trigger message per role
    const trigger = role === 'SCREENER'
      ? 'Run your screening cycle now. Analyse the market and decide whether to open a trade.'
      : role === 'MANAGER'
      ? 'Run your management cycle now. Review all open positions and take appropriate action.'
      : 'Ready.';
    messages.push({ role: 'user', content: trigger });
  }

  let iterations = 0;
  let finalText  = '';

  while (iterations < MAX_ITERATIONS) {
    iterations++;

    const response = await client.chat.completions.create({
      model,
      messages,
      tools,
      tool_choice: 'auto',
      temperature: 0.2,
      max_tokens:  2048,
    });

    const choice = response.choices?.[0];
    if (!choice) break;

    const msg = choice.message;
    messages.push(msg);

    // Extract any text content
    if (msg.content) {
      finalText = msg.content;
      log.debug('agent', `LLM text: ${msg.content.slice(0, 200)}`);
    }

    // No tool calls → done
    if (!msg.tool_calls || msg.tool_calls.length === 0) {
      log.info('agent', `${role} cycle complete (${iterations} iterations)`);
      break;
    }

    // Execute all tool calls (can be parallel if LLM batches them)
    const toolResults = await Promise.all(
      msg.tool_calls.map(async tc => {
        const name = tc.function.name;
        let args   = {};
        try { args = JSON.parse(tc.function.arguments ?? '{}'); } catch (_) {}

        log.info('agent', `Tool call: ${name}`, args);
        const result = await runTool(name, args);

        return {
          role:         'tool',
          tool_call_id: tc.id,
          content:      JSON.stringify(result),
        };
      })
    );

    messages.push(...toolResults);
  }

  if (iterations >= MAX_ITERATIONS) {
    log.warn('agent', `${role} cycle hit MAX_ITERATIONS (${MAX_ITERATIONS})`);
  }

  return finalText || `${role} cycle completed (no text output).`;
}

module.exports = { agentLoop };
