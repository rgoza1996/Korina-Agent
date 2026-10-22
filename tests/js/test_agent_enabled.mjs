import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { saveConfigNow, loadConfig } from '../../Korina/js/api.js';
import { applyConfig } from '../../Korina/js/settings-ui.js';
import { askLM, deliverTranscriptToAgent, handleAgentEvent, handlePermissionAnswerIfAny,
  maybeReleaseDeferredAgentInterrupt, pollAgentEvents, setAgentEnabledFromUi } from '../../Korina/js/agent-ui.js';

const elements = new Map();
const element = id => document.getElementById(id);
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
function response(body, status = 200) { return { ok: status < 400, status, json: async () => body }; }
let posts, requests, server;
beforeEach(() => {
  elements.clear(); posts = []; requests = []; server = { agent_enabled: 'on' };
  globalThis.document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', style: {}, className: '',
      classList: { add() {}, remove() {}, toggle() {} }, appendChild() {} });
    return elements.get(id);
  }, createElement: () => ({}) };
  element('agentEnabled').checked = true;
  Object.assign(state, { appConfig: { agent_enabled: 'on' }, history: [{ role: 'user', content: 'Keep this conversation.' }],
    agentSession: 10, agentActivityEpoch: 0, agentActivityBlocked: false, agentControlPending: false,
    agentResetBlocked: false, agentEventCursor: 0, agentFirstDelivered: false, agentLastDeliveredTurn: 0,
    agentLastDeliveryAt: 0, agentTurnCount: 3, pendingAgentStateReport: '', pendingAgentReportVersion: 0,
    pendingImportantAgentMessage: '', awaitingAgentPermission: null, deferredAgentInterrupt: null,
    agentInterruptInProgress: false, agentInterruptCooldownUntil: 0, controller: null, activeSources: [],
    live: false, ttsSpeaking: false, audioCtx: null });
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url === '/api/config') {
      if (options.method === 'POST') {
        const payload = JSON.parse(options.body); posts.push(payload); Object.assign(server, payload);
      }
      return response({ ...server });
    }
    if (url.startsWith('/api/agent/events')) return response({ last_event_id: 24, events: [{ id: 24, type: 'state_report', report: 'Ignored old backlog' }] });
    if (url === '/api/agent/status') return response({ busy: false });
    if (url === '/api/chat') return response({ reply: 'Normal reply.' });
    throw new Error('Unexpected request: ' + url);
  };
});
async function toggle(enabled) { element('agentEnabled').checked = enabled; return setAgentEnabledFromUi(); }


test('disabled agent cannot inject retained reports, handle permissions, or release deferred speech', async () => {
  element('agentEnabled').checked = false;
  state.pendingAgentStateReport = 'OLD REPORT';
  state.awaitingAgentPermission = { id: 4 };
  state.deferredAgentInterrupt = { message: 'Old interruption' };
  assert.equal(await handlePermissionAnswerIfAny('yes'), false);
  maybeReleaseDeferredAgentInterrupt();
  await askLM('Continue normally.');
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(requests[0].options.body).message, 'Continue normally.');
});


test('rapid off/on persists both transitions, keeps generic saves from overriding them, and preserves chat/Live', async () => {
  const originalFetch = fetch;
  const blocked = deferred(), started = deferred();
  globalThis.fetch = async (url, options) => {
    if (url === '/api/config' && JSON.parse(options.body).agent_enabled === 'off') {
      started.resolve(); await blocked.promise;
    }
    return originalFetch(url, options);
  };
  state.live = true;
  const history = state.history, controller = { currentChat: true };
  state.controller = controller;
  state.pendingAgentStateReport = 'Retire report';
  state.awaitingAgentPermission = { id: 5 };
  state.deferredAgentInterrupt = { message: 'Retire interrupt' };
  const off = toggle(false); await started.promise;
  const on = toggle(true);
  const generic = saveConfigNow();
  assert.equal(state.agentActivityBlocked, true);
  assert.equal(state.pendingAgentStateReport, '');
  assert.equal(state.awaitingAgentPermission, null);
  assert.equal(state.deferredAgentInterrupt, null);
  blocked.resolve(); await Promise.all([off, on, generic]);
  assert.deepEqual(posts.filter(post => Object.hasOwn(post, 'agent_enabled')).map(post => post.agent_enabled), ['off', 'on']);
  assert.equal(posts.at(-1).agent_enabled, undefined, 'ordinary form save cannot undo a control transition');
  assert.equal(state.agentEventCursor, 24);
  assert.equal(state.agentSession, 10);
  assert.equal(state.live, true);
  assert.equal(state.controller, controller);
  assert.equal(state.history, history);
  assert.equal(state.agentActivityBlocked, false);
  assert.equal(state.pendingAgentStateReport, '', 'resume baselines and skips custom adapter backlog');
});


test('failed off stays ahead of queued on until explicit retry succeeds', async () => {
  const originalFetch = fetch;
  const blocked = deferred(), started = deferred();
  let fail = true;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/config' && JSON.parse(options.body).agent_enabled === 'off' && fail) {
      posts.push(JSON.parse(options.body)); started.resolve(); await blocked.promise;
      return response({ detail: 'Disk unavailable' }, 503);
    }
    return originalFetch(url, options);
  };
  const off = toggle(false); await started.promise;
  const on = toggle(true); blocked.resolve();
  assert.deepEqual(await Promise.all([off, on]), [false, false]);
  assert.deepEqual(posts.map(post => post.agent_enabled), ['off']);
  assert.equal(state.agentActivityBlocked, true);
  assert.match(element('settingsActionStatus').textContent, /Disk unavailable.*retry/);
  fail = false;
  assert.equal(await toggle(true), true);
  assert.deepEqual(posts.map(post => post.agent_enabled), ['off', 'off', 'on']);
  assert.equal(server.agent_enabled, 'on');
  assert.equal(state.agentActivityBlocked, false);
});

test('a new control queued while an unchanged control settles is still persisted', async () => {
  const unchanged = toggle(true);
  const off = toggle(false);
  await Promise.all([unchanged, off]);
  assert.deepEqual(posts.map(post => post.agent_enabled), ['off']);
  assert.equal(server.agent_enabled, 'off');
  assert.equal(state.agentActivityBlocked, false);
});


test('a late pre-toggle event poll is aborted and cannot repopulate the resumed context', async () => {
  const originalFetch = fetch, old = deferred(), started = deferred();
  let oldSignal;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/agent/events?after=0' && !oldSignal) {
      oldSignal = options.signal; started.resolve(); return old.promise;
    }
    return originalFetch(url, options);
  };
  const polling = pollAgentEvents(); await started.promise;
  await toggle(false); await toggle(true);
  old.resolve(response({ last_event_id: 8, events: [{ id: 8, type: 'state_report', priority: 'normal', report: 'STALE POLL' }] }));
  await polling;
  assert.equal(oldSignal.aborted, true);
  assert.equal(state.agentEventCursor, 24);
  assert.equal(state.pendingAgentStateReport, '');
});


test('a late accepted delivery cannot update bookkeeping after off/on', async () => {
  const originalFetch = fetch, old = deferred(), started = deferred();
  globalThis.fetch = async (url, options) => {
    if (url === '/api/agent/transcript') { started.resolve(); return old.promise; }
    return originalFetch(url, options);
  };
  const delivering = deliverTranscriptToAgent(); await started.promise;
  await toggle(false); await toggle(true);
  old.resolve(response({ ok: true, accepted: true }));
  assert.equal(await delivering, false);
  assert.equal(state.agentFirstDelivered, false);
  assert.equal(state.agentLastDeliveredTurn, 0);
});


test('a late accepted permission answer cannot clear new context, write history, or speak', async () => {
  const originalFetch = fetch, old = deferred(), started = deferred();
  globalThis.fetch = async (url, options) => {
    if (url === '/api/agent/permission-answer') { started.resolve(); return old.promise; }
    return originalFetch(url, options);
  };
  state.awaitingAgentPermission = { id: 6 };
  const history = [...state.history];
  const permission = handlePermissionAnswerIfAny('yes'); await started.promise;
  await toggle(false); await toggle(true);
  state.awaitingAgentPermission = { id: 7 };
  old.resolve(response({ accepted: true })); await permission;
  assert.deepEqual(state.history, history);
  assert.equal(state.awaitingAgentPermission.id, 7);
  assert.equal(requests.some(request => request.url.includes('speech')), false);
});


test('ordinary in-flight chat survives disabling while a reply containing retired injection is discarded', async () => {
  const originalFetch = fetch;
  let pending = deferred(), started = deferred();
  globalThis.fetch = async (url, options) => {
    if (url === '/api/chat') { started.resolve(); return pending.promise; }
    return originalFetch(url, options);
  };
  const ordinary = askLM('Normal turn.'); await started.promise;
  await toggle(false); pending.resolve(response({ reply: 'Normal reply.' }));
  assert.equal(await ordinary, 'Normal reply.');
  await toggle(true);
  handleAgentEvent({ type: 'state_report', report: 'Retired report.', priority: 'normal' });
  pending = deferred(); started = deferred();
  const injected = askLM('With agent context.'); await started.promise;
  await toggle(false); pending.resolve(response({ reply: 'Old report reply.' }));
  assert.equal(await injected, null);
  assert.equal(state.agentSession, 10);
});


test('late settings loads and applyConfig cannot overwrite an explicit checkbox transition', async () => {
  const originalFetch = fetch, old = deferred(), started = deferred();
  globalThis.fetch = async (url, options) => {
    if (url === '/api/config' && !options?.method) { started.resolve(); return old.promise; }
    return originalFetch(url, options);
  };
  const loading = loadConfig(); await started.promise;
  await toggle(false);
  old.resolve(response({ agent_enabled: 'on' })); await loading;
  assert.equal(element('agentEnabled').checked, false);
  assert.equal(state.appConfig.agent_enabled, 'off');
  state.agentControlPending = true;
  applyConfig({ agent_enabled: 'on' });
  assert.equal(element('agentEnabled').checked, false);
  state.agentControlPending = false;
});
