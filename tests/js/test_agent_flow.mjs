import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import {
  askLM, deliverTranscriptToAgent, handleAgentEvent, pollAgentEvents,
} from '../../Korina/js/agent-ui.js';

const elements = new Map();
globalThis.document = { getElementById(id) {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '' });
  return elements.get(id);
} };
const element = id => document.getElementById(id);

beforeEach(() => {
  elements.clear();
  element('agentEnabled').checked = true;
  element('agentMaxTurns').value = '2';
  element('agentBusyDeliveryMode').value = 'injection';
  element('agentIdleDeliveryMode').value = 'prompt';
  element('agentInterruptsEnabled').value = 'off';
  element('agentReportInjectionMode').value = 'next_reply';
  element('lmModel').value = 'demo-model';
  Object.assign(state, {
    history: [], agentTurnCount: 3, agentLastDeliveredTurn: 0,
    agentFirstDelivered: false, agentLastDeliveryAt: 0,
    agentEventCursor: 0, pendingAgentStateReport: '', agentStateReport: '',
    pendingAgentReportVersion: 0, agentSession: 0,
  });
});

test('active agent delivery sends a bounded sanitized transcript using the current busy mode', async () => {
  state.history = [
    { role: 'user', content: 'Earlier turn.' },
    { role: 'assistant', content: '[Ack Phrase] Okay.' },
    { role: 'user', content: 'Build the demo.' },
    { role: 'assistant', content: '<think>private reasoning</think>Ready.' },
  ];
  const deliveries = [];
  globalThis.fetch = async (url, options) => {
    if (url === '/api/agent/status') return { ok: true, json: async () => ({ busy: true }) };
    assert.equal(url, '/api/agent/transcript');
    deliveries.push(JSON.parse(options.body));
    return { ok: true, json: async () => ({ accepted: true }) };
  };

  await deliverTranscriptToAgent('turn');

  assert.deepEqual(deliveries, [{
    transcript: [
      { role: 'user', content: 'Build the demo.' },
      { role: 'assistant', content: 'Ready.' },
    ],
    delivery_mode: 'injection', reason: 'turn', turn_count: 3,
  }]);
  assert.equal(state.agentLastDeliveredTurn, 3);
  assert.equal(state.agentFirstDelivered, true);
  assert.ok(state.agentLastDeliveryAt > 0);
});

test('agent reports are injected into one response without entering visible history', async () => {
  state.history = [{ role: 'user', content: 'What needs attention?' }];
  handleAgentEvent({
    type: 'state_report', priority: 'normal',
    report: '<think>internal</think>The demo server is ready.',
  });
  assert.equal(state.pendingAgentStateReport, 'The demo server is ready.');
  const messages = [];
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/chat');
    messages.push(JSON.parse(options.body));
    return { ok: true, json: async () => ({ reply: 'Continue with the rehearsal.' }) };
  };

  assert.equal(await askLM('What next?'), 'Continue with the rehearsal.');
  await askLM('And after that?');

  assert.match(messages[0].message, /Injection: Korina Agent state report/);
  assert.match(messages[0].message, /The demo server is ready/);
  assert.doesNotMatch(messages[0].message, /internal|<think>/);
  assert.equal(messages[1].message, 'And after that?');
  assert.equal(state.pendingAgentStateReport, '');
  assert.deepEqual(state.history, [{ role: 'user', content: 'What needs attention?' }]);
});

test('event polling advances the active cursor and stores the next report', async () => {
  state.agentEventCursor = 7;
  globalThis.fetch = async url => {
    assert.equal(url, '/api/agent/events?after=7');
    return { json: async () => ({ events: [
      { id: 8, type: 'agent_status', busy: false },
      { id: 9, type: 'state_report', priority: 'normal', report: 'Ready for the next turn.' },
    ] }) };
  };
  await pollAgentEvents();
  assert.equal(state.agentEventCursor, 9);
  assert.equal(state.pendingAgentStateReport, 'Ready for the next turn.');
});

test('disabling the agent prevents transcript delivery and event polling', async () => {
  element('agentEnabled').checked = false;
  globalThis.fetch = async () => { throw new Error('disabled agent must not fetch'); };
  await deliverTranscriptToAgent('turn');
  await pollAgentEvents();
  assert.equal(state.agentFirstDelivered, false);
});

for (const [name, failed] of [
  ['HTTP error', async () => ({ ok: false, status: 503, json: async () => ({ detail: 'busy' }) })],
  ['rejected acceptance', async () => ({ ok: true, status: 200, json: async () => ({ ok: true, accepted: false }) })],
  ['invalid JSON', async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('invalid JSON'); } })],
  ['network error', async () => { throw new Error('offline'); }],
]) {
  test(`agent delivery retains its retry cursor after ${name}`, async () => {
    globalThis.fetch = async url => url === '/api/agent/status'
      ? { ok: true, json: async () => ({ busy: false }) }
      : failed();
    const warn = console.warn;
    console.warn = () => {};
    try {
      assert.equal(await deliverTranscriptToAgent('turn'), false);
    } finally {
      console.warn = warn;
    }
    assert.equal(state.agentFirstDelivered, false);
    assert.equal(state.agentLastDeliveredTurn, 0);
    assert.equal(state.agentLastDeliveryAt, 0);

    globalThis.fetch = async url => ({ ok: true, json: async () => url === '/api/agent/status'
      ? { busy: false } : { ok: true, accepted: true } });
    assert.equal(await deliverTranscriptToAgent('retry'), true);
    assert.equal(state.agentLastDeliveredTurn, 3);
  });
}

test('concurrent agent delivery triggers share one request and acknowledge its original turn', async () => {
  let accept;
  let posts = 0;
  globalThis.fetch = async url => {
    if (url === '/api/agent/status') return { ok: true, json: async () => ({ busy: false }) };
    posts++;
    return new Promise(resolve => { accept = resolve; });
  };
  const first = deliverTranscriptToAgent('turn');
  await new Promise(setImmediate);
  state.agentTurnCount = 4;
  assert.equal(await deliverTranscriptToAgent('timer'), false);
  accept({ ok: true, json: async () => ({ accepted: true }) });
  assert.equal(await first, true);
  assert.equal(posts, 1);
  assert.equal(state.agentLastDeliveredTurn, 3, 'a newer turn still needs delivery');
});

test('a delivery completing after session reset cannot advance the new session', async () => {
  let accept;
  globalThis.fetch = async url => {
    if (url === '/api/agent/status') return { ok: true, json: async () => ({ busy: false }) };
    return new Promise(resolve => { accept = resolve; });
  };
  const pending = deliverTranscriptToAgent('turn');
  await new Promise(setImmediate);
  state.agentSession++;
  accept({ ok: true, json: async () => ({ accepted: true }) });
  assert.equal(await pending, false);
  assert.equal(state.agentLastDeliveredTurn, 0);
});

for (const [name, response] of [
  ['HTTP error', { ok: false, json: async () => ({ detail: 'server unavailable' }) }],
  ['invalid reply', { ok: true, json: async () => ({}) }],
  ['invalid JSON', { ok: true, json: async () => { throw new SyntaxError('invalid JSON'); } }],
]) {
  test(`a pending agent report survives a response LLM ${name}`, async () => {
    handleAgentEvent({ type: 'state_report', priority: 'normal', report: 'Keep this report.' });
    globalThis.fetch = async () => response;
    await assert.rejects(askLM('What next?'));
    assert.equal(state.pendingAgentStateReport, 'Keep this report.');
  });
}

test('an empty LLM reply keeps the pending report available for retry', async () => {
  handleAgentEvent({ type: 'state_report', priority: 'normal', report: 'Keep this report.' });
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ reply: '' }) });
  assert.equal(await askLM('What next?'), '');
  assert.equal(state.pendingAgentStateReport, 'Keep this report.');
});

test('a newer report arriving during a chat request is preserved even if its text repeats', async () => {
  handleAgentEvent({ type: 'state_report', priority: 'normal', report: 'Latest status.' });
  let finish;
  globalThis.fetch = async () => new Promise(resolve => { finish = resolve; });
  const pending = askLM('What next?');
  handleAgentEvent({ type: 'state_report', priority: 'normal', report: 'Latest status.' });
  finish({ ok: true, json: async () => ({ reply: 'Understood.' }) });
  assert.equal(await pending, 'Understood.');
  assert.equal(state.pendingAgentStateReport, 'Latest status.');
});
