import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import {
  askLM, askAndSpeak, deliverTranscriptToAgent, handleAgentEvent, pollAgentEvents,
} from '../../Korina/js/agent-ui.js';

const elements = new Map();
globalThis.document = { getElementById(id) {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '', appendChild() {} });
  return elements.get(id);
}, createElement: () => ({}) };
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
    pendingAgentReportVersion: 0, agentSession: 0, agentResetBlocked: false,
    awaitingAgentPermission: null, pendingImportantAgentMessage: '', deferredAgentInterrupt: null,
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

test('a current chat turn is sent once while visible history keeps it and remains bounded', async () => {
  element('agentEnabled').checked = false;
  const prior = Array.from({ length: 20 }, (_, index) => ({
    role: index % 2 ? 'assistant' : 'user', content: `Earlier turn ${index}.`,
  }));
  state.history = [...prior];
  let payload;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/chat');
    payload = JSON.parse(options.body);
    assert.equal(state.history.at(-1).content, 'Current user turn.', 'record the visible turn before I/O');
    return { ok: true, json: async () => ({ reply: '' }) };
  };

  await askAndSpeak('Current user turn.');

  assert.equal(payload.message, 'Current user turn.');
  assert.deepEqual(payload.history, prior.slice(-12));
  assert.equal(payload.history.some(turn => turn.content === payload.message), false);
  assert.deepEqual(state.history.at(-2), { role: 'user', content: 'Current user turn.' });
  assert.equal(state.history.length, 20);
});

test('interruption context and agent injection do not duplicate or replace the raw user history', async () => {
  element('agentEnabled').checked = false;
  const prior = [{ role: 'assistant', content: 'An earlier spoken reply.' }];
  state.history = [...prior];
  state.pendingAgentStateReport = 'The background task is ready.';
  let payload;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/chat');
    payload = JSON.parse(options.body);
    return { ok: true, json: async () => ({ reply: '' }) };
  };

  await askAndSpeak('Please change direction.', 'The user interrupted halfway through the reply.');

  assert.deepEqual(payload.history, prior);
  assert.match(payload.message, /^Injection: Korina Agent state report/);
  assert.match(payload.message, /The user interrupted halfway/);
  assert.equal(payload.message.match(/Please change direction\./g).length, 1);
  assert.deepEqual(state.history.at(-2), { role: 'user', content: 'Please change direction.' });
  assert.equal(state.pendingAgentStateReport, 'The background task is ready.', 'an empty response retains the injection for retry');
});

test('direct LLM calls preserve a legitimate trailing prior user turn, including repeated text', async () => {
  const prior = [{ role: 'user', content: 'Please continue.' }];
  state.history = [...prior];
  let payload;
  globalThis.fetch = async (url, options) => {
    payload = JSON.parse(options.body);
    return { ok: true, json: async () => ({ reply: 'Continuing.' }) };
  };

  await askLM('Please continue.');

  assert.deepEqual(payload.history, prior, 'history is an explicit prior-turn contract, not text deduplication');
  assert.equal(payload.message, 'Please continue.');
  assert.deepEqual(state.history, prior);
});

test('event polling advances the active cursor and stores the next report', async () => {
  state.agentEventCursor = 7;
  globalThis.fetch = async url => {
    assert.equal(url, '/api/agent/events?after=7');
    return { ok: true, json: async () => ({ events: [
      { id: 8, type: 'agent_status', busy: false },
      { id: 9, type: 'state_report', priority: 'normal', report: 'Ready for the next turn.' },
    ] }) };
  };
  await pollAgentEvents();
  assert.equal(state.agentEventCursor, 9);
  assert.equal(state.pendingAgentStateReport, 'Ready for the next turn.');
});

test('an authoritative lower cursor clears stale agent context and receives new events once', async () => {
  state.agentEventCursor = 42;
  state.agentFirstDelivered = true;
  state.agentLastDeliveredTurn = 3;
  state.agentStateReport = 'Old report.';
  state.pendingAgentStateReport = 'Old injection.';
  state.awaitingAgentPermission = { id: 41 };
  state.pendingImportantAgentMessage = 'Old interrupt.';
  state.deferredAgentInterrupt = { message: 'Old deferred interrupt.' };
  state.history = [{ role: 'user', content: 'Keep the conversation.' }];
  const urls = [];
  const responses = [
    { last_event_id: 0, events: [] },
    { last_event_id: 1, events: [{ id: 1, type: 'state_report', priority: 'normal', report: 'New report.' }] },
    { last_event_id: 1, events: [] },
  ];
  globalThis.fetch = async url => {
    urls.push(url);
    return { ok: true, json: async () => responses.shift() };
  };

  await pollAgentEvents();
  assert.equal(state.agentEventCursor, 0);
  assert.equal(state.agentSession, 1);
  assert.equal(state.agentStateReport, '');
  assert.equal(state.pendingAgentStateReport, '');
  assert.equal(state.awaitingAgentPermission, null);
  assert.equal(state.pendingImportantAgentMessage, '');
  assert.equal(state.deferredAgentInterrupt, null);
  assert.equal(state.agentFirstDelivered, false);
  assert.equal(state.agentLastDeliveredTurn, 0);
  assert.deepEqual(state.history, [{ role: 'user', content: 'Keep the conversation.' }]);

  await pollAgentEvents();
  const reportVersion = state.pendingAgentReportVersion;
  await pollAgentEvents();
  assert.deepEqual(urls, ['/api/agent/events?after=42', '/api/agent/events?after=0', '/api/agent/events?after=1']);
  assert.equal(state.agentEventCursor, 1);
  assert.equal(state.pendingAgentStateReport, 'New report.');
  assert.equal(state.pendingAgentReportVersion, reportVersion, 'the new event was injected once');
});

test('a server session change prevents an old in-flight delivery from advancing the new session', async () => {
  state.agentEventCursor = 42;
  let finish;
  globalThis.fetch = async url => {
    if (url === '/api/agent/status') return { ok: true, json: async () => ({ busy: false }) };
    if (url.startsWith('/api/agent/events')) return { ok: true, json: async () => ({ last_event_id: 0, events: [] }) };
    return new Promise(resolve => { finish = resolve; });
  };
  const delivery = deliverTranscriptToAgent('old-session');
  await new Promise(setImmediate);
  await pollAgentEvents();
  finish({ ok: true, json: async () => ({ accepted: true }) });
  assert.equal(await delivery, false);
  assert.equal(state.agentFirstDelivered, false);
  assert.equal(state.agentLastDeliveredTurn, 0);
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

test('agent injection stays within the message limit without truncating the user turn', async () => {
  state.pendingAgentStateReport = 'r'.repeat(8192);
  const userMessage = 'u'.repeat(8100);
  let payload;
  globalThis.fetch = async (url, options) => {
    payload = JSON.parse(options.body);
    return { ok: true, json: async () => ({ reply: 'Understood.' }) };
  };
  await askLM(userMessage);
  assert.equal([...payload.message].length, 8192);
  assert.equal(payload.message.endsWith(userMessage), true);
  assert.match(payload.message, /^Injection:/);
});

test('a full-length user turn keeps an agent report queued when no injection space remains', async () => {
  state.pendingAgentStateReport = 'Keep this for a shorter turn.';
  const userMessage = 'u'.repeat(8192);
  let payload;
  globalThis.fetch = async (url, options) => {
    payload = JSON.parse(options.body);
    return { ok: true, json: async () => ({ reply: 'Understood.' }) };
  };
  await askLM(userMessage);
  assert.equal(payload.message, userMessage);
  assert.equal(state.pendingAgentStateReport, 'Keep this for a shorter turn.');
});

test('overlapping event polls cannot deliver old events into a cleared session', async () => {
  let finish;
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const pending = pollAgentEvents();
  await pollAgentEvents();
  state.agentSession++;
  state.agentEventCursor = 0;
  state.pendingAgentStateReport = '';
  finish({ ok: true, json: async () => ({ events: [
    { id: 99, type: 'state_report', priority: 'normal', report: 'Old session report.' },
  ] }) });
  await pending;
  assert.equal(calls, 1);
  assert.equal(state.agentEventCursor, 0);
  assert.equal(state.pendingAgentStateReport, '');
});

test('a chat response from a cleared session cannot append or speak its reply', async () => {
  let finish;
  const requests = [];
  globalThis.fetch = async url => { requests.push(url); return new Promise(resolve => { finish = resolve; }); };
  const pending = askAndSpeak('Old user turn.');
  await new Promise(setImmediate);
  state.agentSession++;
  state.history = [];
  element('text').value = '';
  finish({ ok: true, json: async () => ({ reply: 'Old reply must be ignored.' }) });
  assert.equal(await pending, null);
  assert.deepEqual(state.history, []);
  assert.equal(element('text').value, '');
  assert.deepEqual(requests, ['/api/chat']);
});

test('pending or failed reset blocks agent delivery, polling, and event injection', async () => {
  state.agentResetBlocked = true;
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error('reset has not succeeded'); };
  await deliverTranscriptToAgent('turn');
  await pollAgentEvents();
  handleAgentEvent({ type: 'state_report', priority: 'normal', report: 'Old server event.' });
  assert.equal(requests, 0);
  assert.equal(state.pendingAgentStateReport, '');
});
