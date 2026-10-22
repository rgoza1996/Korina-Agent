import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import {
  askLM, askAndSpeak, deliverTranscriptToAgent, handleAgentEvent, pollAgentEvents,
  handlePermissionAnswerIfAny,
} from '../../Korina/js/agent-ui.js';

const elements = new Map();
globalThis.document = { getElementById(id) {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '', style: {}, appendChild() {} });
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
  element('agentEnabled').checked = true;
  state.agentFirstDelivered = true;
  state.agentLastDeliveredTurn = 100;
  state.agentLastDeliveryAt = performance.now();
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

test('an already cancelled caller cannot log, consume permission, or start a chat request', async () => {
  state.awaitingAgentPermission = { id: 7 };
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error('cancelled work must not start'); };
  const options = { isCurrent: () => false };
  assert.equal(await askLM('Old message.', [], options), null);
  assert.equal(await askAndSpeak('yes', '', options), null);
  assert.equal(await handlePermissionAnswerIfAny('yes', options), false);
  assert.equal(state.awaitingAgentPermission.id, 7);
  assert.deepEqual(state.history, []);
  assert.equal(element('sttStatus').textContent, '');
  assert.equal(calls, 0);
});

for (const phase of ['fetch', 'JSON']) {
  test(`caller cancellation during chat ${phase} preserves pending report and current UI`, async () => {
    let current = true;
    let finish;
    state.pendingAgentStateReport = 'Keep for the next turn.';
    globalThis.fetch = () => phase === 'fetch'
      ? new Promise(resolve => { finish = () => resolve({ ok: true, json: async () => ({ reply: 'Old reply.' }) }); })
      : Promise.resolve({ ok: true, json: () => new Promise(resolve => { finish = () => resolve({ reply: 'Old reply.' }); }) });
    const pending = askLM('Old message.', [], { isCurrent: () => current });
    await new Promise(setImmediate);
    current = false;
    element('sttStatus').textContent = 'Fresh session status.';
    finish();
    assert.equal(await pending, null);
    assert.equal(state.pendingAgentStateReport, 'Keep for the next turn.');
    assert.equal(element('sttStatus').textContent, 'Fresh session status.');
  });
}

test('a late failed chat request is ignored when its live caller is cancelled', async () => {
  let current = true;
  let fail;
  globalThis.fetch = () => new Promise((resolve, reject) => { fail = reject; });
  const pending = askLM('Old message.', [], { isCurrent: () => current });
  current = false;
  fail(new Error('old connection failed'));
  assert.equal(await pending, null);
});

test('caller cancellation suppresses a late reply without resetting the agent session', async () => {
  let current = true;
  let finish;
  const requests = [];
  globalThis.fetch = async url => { requests.push(url); return new Promise(resolve => { finish = resolve; }); };
  const pending = askAndSpeak('Old user turn.', '', { isCurrent: () => current });
  await new Promise(setImmediate);
  current = false;
  element('text').value = 'Current reply.';
  finish({ ok: true, json: async () => ({ reply: 'Old reply must be ignored.' }) });
  assert.equal(await pending, null);
  assert.equal(element('text').value, 'Current reply.');
  assert.equal(state.agentTurnCount, 3);
  assert.deepEqual(state.history, [{ role: 'user', content: 'Old user turn.' }]);
  assert.deepEqual(requests, ['/api/chat']);
});

test('caller cancellation suppresses a late permission acknowledgement and speech', async () => {
  let current = true;
  let finish;
  state.awaitingAgentPermission = { id: 7 };
  const requests = [];
  globalThis.fetch = async url => { requests.push(url); return new Promise(resolve => { finish = resolve; }); };
  const pending = askAndSpeak('yes', '', { isCurrent: () => current });
  current = false;
  state.history = [];
  element('text').value = 'Current reply.';
  finish({ ok: true, json: async () => ({ accepted: true }) });
  assert.equal(await pending, null);
  assert.deepEqual(state.history, []);
  assert.equal(element('text').value, 'Current reply.');
  assert.deepEqual(requests, ['/api/agent/permission-answer']);
});

for (const [name, response] of [
  ['HTTP 503', { ok: false, status: 503, json: async () => ({ detail: 'Agent unavailable' }) }],
  ['non-JSON success', { ok: true, json: async () => { throw new SyntaxError('invalid JSON'); } }],
  ['non-JSON HTTP failure', { ok: false, status: 503, json: async () => { throw new SyntaxError('HTML response'); } }],
  ['rejected acceptance', { ok: true, json: async () => ({ ok: true, accepted: false }) }],
  ['disabled legacy agent', { ok: true, json: async () => ({ ok: true, accepted: false, disabled: true }) }],
  ['missing acceptance', { ok: true, json: async () => ({ ok: true }) }],
  ['explicit failure', { ok: true, json: async () => ({ ok: false, accepted: true }) }],
  ['network failure', null],
]) {
  test(`permission ${name} retains the pending question and does not record or speak success`, async () => {
    const request = { id: 7 };
    state.awaitingAgentPermission = request;
    state.history = [{ role: 'user', content: 'Earlier turn.' }];
    element('text').value = 'yes';
    const posts = [];
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/agent/permission-answer');
      posts.push(JSON.parse(options.body));
      if (!response) throw new Error('Network unavailable');
      return response;
    };
    await assert.rejects(askAndSpeak('yes'), /Permission answer failed:.*still pending/);
    await assert.rejects(askAndSpeak('yes'), /Permission answer failed:.*still pending/);
    assert.equal(state.awaitingAgentPermission, request);
    assert.deepEqual(state.history, [{ role: 'user', content: 'Earlier turn.' }]);
    assert.equal(state.agentTurnCount, 3);
    assert.equal(state.agentLastDeliveredTurn, 0);
    assert.equal(state.agentFirstDelivered, false);
    assert.equal(element('text').value, 'yes');
    assert.equal(element('sttStatus').className, 'status bad');
    assert.equal(posts.length, 2);
    assert.deepEqual(posts[0], posts[1], 'retries do not accumulate failed answer turns');
    assert.equal(posts[0].transcript.filter(turn => turn.content.startsWith('Permission answer')).length, 1);
  });
}

test('concurrent permission answers share one request without adding history', async () => {
  const request = { id: 7 };
  state.awaitingAgentPermission = request;
  let finish;
  let posts = 0;
  globalThis.fetch = () => { posts++; return new Promise(resolve => { finish = resolve; }); };
  const pending = handlePermissionAnswerIfAny('yes');
  assert.equal(await handlePermissionAnswerIfAny('yes'), true);
  assert.equal(posts, 1);
  assert.deepEqual(state.history, []);
  finish({ ok: false, status: 503, json: async () => ({ detail: 'Agent unavailable' }) });
  await assert.rejects(pending, /Agent unavailable/);
  assert.equal(state.awaitingAgentPermission, request);
  assert.equal(state.agentTurnCount, 3);
});

test('an accepted old answer preserves a newer permission question without speaking over it', async () => {
  const old = { id: 7 };
  const newer = { id: 8 };
  state.awaitingAgentPermission = old;
  let finish;
  const posts = [];
  globalThis.fetch = (url, options) => {
    assert.equal(url, '/api/agent/permission-answer');
    posts.push(JSON.parse(options.body));
    return new Promise(resolve => { finish = resolve; });
  };
  const pending = handlePermissionAnswerIfAny('yes');
  state.awaitingAgentPermission = newer;
  element('text').value = 'New permission question.';
  finish({ ok: true, json: async () => ({ ok: true, accepted: true, queued_as: 'permission_answer' }) });
  assert.equal(await pending, true);
  assert.equal(state.awaitingAgentPermission, newer);
  assert.equal(element('text').value, 'New permission question.');
  assert.match(element('sttStatus').textContent, /accepted; a newer permission request is still waiting/);
  assert.deepEqual(state.history, [{ role: 'user', content: 'Permission answer to Korina Agent: yes' }]);
  assert.equal(state.agentTurnCount, 4);
  assert.equal(state.agentLastDeliveredTurn, 4);
  assert.equal(posts.length, 1);
});

test('an old permission failure does not overwrite a newer question status', async () => {
  state.awaitingAgentPermission = { id: 7 };
  let fail;
  globalThis.fetch = () => new Promise((resolve, reject) => { fail = reject; });
  const pending = handlePermissionAnswerIfAny('yes');
  const newer = { id: 8 };
  state.awaitingAgentPermission = newer;
  element('sttStatus').textContent = 'New permission question is waiting.';
  fail(new Error('Old request failed'));
  assert.equal(await pending, true);
  assert.equal(state.awaitingAgentPermission, newer);
  assert.equal(element('sttStatus').textContent, 'New permission question is waiting.');
  assert.deepEqual(state.history, []);
});

test('accepted permission bookkeeping does not mark newer conversation turns delivered', async () => {
  state.awaitingAgentPermission = { id: 7 };
  let finish;
  globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = handlePermissionAnswerIfAny('no');
  state.agentTurnCount++;
  state.awaitingAgentPermission = { id: 8 };
  finish({ ok: true, json: async () => ({ ok: true, accepted: true }) });
  await pending;
  assert.equal(state.agentTurnCount, 5);
  assert.equal(state.agentLastDeliveredTurn, 4);
});

class PermissionAudio extends EventTarget {
  duration = 1;
  play() { this.dispatchEvent(new Event('playing')); return Promise.resolve(); }
  pause() {}
  removeAttribute() {}
  load() {}
}

test('a failed permission answer can retry successfully with one canonical history turn and no extra agent delivery', async () => {
  const audio = new PermissionAudio();
  elements.set('audio', audio);
  element('voice').value = 'af_heart'; element('speed').value = '1';
  element('ttsBaseUrl').value = 'http://speech.test';
  Object.assign(state, {
    mode: 'buf', live: false, audioCtx: { state: 'running', currentTime: 10 },
    controller: null, activeSources: [], handlingBarge: false, history: [],
    awaitingAgentPermission: { id: 0 },
  });
  const attempts = [];
  let speechRequests = 0;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/agent/permission-answer') {
      attempts.push(JSON.parse(options.body));
      return attempts.length === 1
        ? { ok: false, status: 503, json: async () => ({ detail: 'Agent unavailable' }) }
        : { ok: true, json: async () => ({ ok: true, accepted: true, queued_as: 'prompt', status: {} }) };
    }
    assert.equal(url, 'http://speech.test/v1/audio/speech', 'permission acceptance owns follow-up; do not submit a second transcript');
    speechRequests++;
    return new Response(new Blob(['fixture audio']));
  };
  await assert.rejects(askAndSpeak('yes'), /Agent unavailable/);
  const pending = askAndSpeak('yes');
  await new Promise(setImmediate);
  assert.equal(state.awaitingAgentPermission, null);
  audio.dispatchEvent(new Event('ended'));
  assert.equal(await pending, null);
  assert.equal(speechRequests, 1);
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].request_id, '0');
  assert.deepEqual(attempts[0], attempts[1]);
  assert.deepEqual(state.history, [
    { role: 'user', content: 'Permission answer to Korina Agent: yes' },
    { role: 'assistant', content: 'Okay. I told Korina Agent it has permission.' },
  ]);
  assert.equal(state.agentTurnCount, 4);
  assert.equal(state.agentLastDeliveredTurn, 4);
  assert.equal(state.agentFirstDelivered, true);
  assert.equal(element('sttStatus').textContent, 'Korina Agent accepted the permission answer.');
});
