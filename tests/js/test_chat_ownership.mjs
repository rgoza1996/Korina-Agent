import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { askLM, askAndSpeak, cancelChatRequests } from '../../Korina/js/agent-ui.js';
import { state } from '../../Korina/js/state.js';
import { handleLiveTurn, stopLive } from '../../Korina/js/live.js';

const elements = new Map();
const el = id => {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '',
    style: {}, classList: { add() {}, remove() {} }, appendChild() {} });
  return elements.get(id);
};
const tick = () => new Promise(setImmediate);
function held(signal) {
  return new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Cancelled native fetch', 'AbortError')), { once: true });
  });
}

beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: el, createElement: () => ({}) };
  globalThis.cancelAnimationFrame = () => {};
  Object.assign(state, { agentSession: 1, history: [], agentTurnCount: 0,
    live: false, liveSession: 1, liveTurnController: null, liveTranscriptionController: null,
    stream: null, meterAudioCtx: null, analyser: null, raf: null, mediaRecorder: null,
    partialRecorder: null, partialRecorderDone: null, partialController: null,
    partialTimer: null, partialQueue: [], liveLoopId: null, bargeLoopId: null,
    bargeRecorder: null, activeSources: [], controller: null, ackSource: null,
    pendingAgentStateReport: '', awaitingAgentPermission: null, pendingImportantAgentMessage: '',
  });
});
afterEach(() => { cancelChatRequests(); stopLive(); });

for (const phase of ['headers', 'JSON body']) {
  test(`Clear aborts old chat ${phase} without publishing an error or consuming a report`, async () => {
    let signal;
    const started = [];
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/chat');
      signal = options.signal;
      if (phase === 'headers') { started.push(true); return held(signal); }
      return { ok: true, json: () => { started.push(true); return held(signal); } };
    };
    const old = askAndSpeak('Old turn.');
    await tick();
    assert.equal(started.length, 1);
    state.agentSession++;
    state.history = [];
    el('text').value = 'New conversation.';
    el('sttStatus').textContent = 'Session cleared.';
    cancelChatRequests();
    assert.equal(signal.aborted, true);
    assert.equal(await old, null);
    assert.deepEqual(state.history, []);
    assert.equal(el('text').value, 'New conversation.');
    assert.equal(el('sttStatus').textContent, 'Session cleared.');
  });
}

test('an old request finally cannot remove the controller of a newer chat', async () => {
  const signals = [];
  globalThis.fetch = (url, options) => { signals.push(options.signal); return held(options.signal); };
  const old = askLM('Old turn.');
  cancelChatRequests();
  const fresh = askLM('Fresh turn.');
  await old;
  assert.equal(signals[1].aborted, false);
  cancelChatRequests();
  assert.equal(signals[1].aborted, true);
  assert.equal(await fresh, null);
});

test('Stop Live aborts the actual live chat and leaves a current typed Ask connected', async () => {
  state.live = true;
  state.liveBusy = true;
  state.recChunks = [new Blob(['x'.repeat(3000)])];
  state.latestPartialText = 'Live utterance.';
  state.partialNeedsFullPass = false;
  el('finalSttMode').value = 'chunks';
  let liveSignal, typedSignal, finishTyped;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/health') return { ok: true, json: async () => ({ ok: true, response_llm_load: { ok: true, loaded: true } }) };
    assert.equal(url, '/api/chat');
    if (JSON.parse(options.body).message === 'Live utterance.') {
      liveSignal = options.signal;
      return held(liveSignal);
    }
    typedSignal = options.signal;
    return new Promise(resolve => { finishTyped = resolve; });
  };
  const live = handleLiveTurn();
  await tick();
  assert.ok(liveSignal);
  const typed = askLM('Typed Ask stays current.');
  stopLive();
  assert.equal(liveSignal.aborted, true);
  assert.equal(typedSignal.aborted, false);
  await live;
  assert.equal(state.liveTurnController, null);
  finishTyped({ ok: true, json: async () => ({ reply: 'Typed answer.' }) });
  assert.equal(await typed, 'Typed answer.');
  assert.equal(state.history.some(turn => turn.role === 'assistant'), false);
});

for (const failure of [new TypeError('Network failed'), new DOMException('Unexpected abort', 'AbortError')]) {
  test(`a current ${failure.name} remains an error when its owner was not retired`, async () => {
    globalThis.fetch = async () => { throw failure; };
    await assert.rejects(askLM('Current turn.'), error => error === failure);
  });
}

test('an already retired caller creates no request or visible user turn', async () => {
  const controller = new AbortController();
  controller.abort();
  globalThis.fetch = async () => { throw new Error('Retired caller must not fetch'); };
  assert.equal(await askAndSpeak('Do not send.', '', { signal: controller.signal }), null);
  assert.deepEqual(state.history, []);
});
