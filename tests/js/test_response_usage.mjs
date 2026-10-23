import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { askLM, cancelChatRequests, clearResponseUsage } from '../../Korina/js/agent-ui.js';
import { state } from '../../Korina/js/state.js';
import { stopLive } from '../../Korina/js/live.js';

const elements = new Map();
const el = id => {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '',
    style: {}, classList: { add() {}, remove() {} }, appendChild() {} });
  return elements.get(id);
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = body => ({ ok: true, json: async () => body });
function result() {
  return { reply: 'Only this answer is spoken.', usage: {
    prompt_tokens: 1024, completion_tokens: 64, reasoning_tokens: 32, cached_tokens: 0,
  }, request_settings: { provider: 'meta', model: 'requested-model', max_tokens: 2048,
    reasoning_effort: 'minimal', reasoning: null } };
}

beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: el, createElement: () => ({}) };
  globalThis.cancelAnimationFrame = () => {};
  Object.assign(state, { agentSession: 1, history: [], pendingAgentStateReport: '',
    live: false, liveSession: 1, liveTurnController: null, liveTranscriptionController: null,
    stream: null, meterAudioCtx: null, analyser: null, raf: null, mediaRecorder: null,
    partialRecorder: null, partialRecorderDone: null, partialController: null,
    partialTimer: null, partialQueue: [], liveLoopId: null, bargeLoopId: null, bargeRecorder: null });
  el('llmProvider').value = 'meta'; el('lmModel').value = 'requested-model';
  el('llmReasoningEffort').value = 'minimal'; el('llmMaxTokens').value = '2048';
  clearResponseUsage();
});
afterEach(() => { clearResponseUsage(); cancelChatRequests(); stopLive(); });

test('usage is separate from the reply and labels the server request snapshot despite newer drafts', async () => {
  const pending = deferred(); let request;
  globalThis.fetch = (_url, options) => { request = JSON.parse(options.body); return pending.promise; };
  const chat = askLM('A question.');
  assert.equal(el('responseUsageStatus').textContent, 'Waiting for response…');
  el('llmProvider').value = 'ollama'; el('lmModel').value = 'new-draft';
  el('llmReasoningEffort').value = 'xhigh'; el('llmMaxTokens').value = '8192';
  pending.resolve(response(result()));
  assert.equal(await chat, 'Only this answer is spoken.');
  assert.equal(request.model, 'requested-model');
  assert.equal(el('responsePromptTokens').textContent, '1024');
  assert.equal(el('responseCachedTokens').textContent, '0');
  assert.match(el('responseUsageSettings').textContent, /Provider: meta · Model: requested-model · Output budget: 2048 · Reasoning effort: minimal/);
  assert.doesNotMatch(el('responseUsageSettings').textContent, /new-draft|ollama|8192|xhigh/);
  assert.deepEqual(state.history, []);
  assert.equal(JSON.stringify(request).includes('prompt_tokens'), false);
});

test('reported zero remains distinct from unavailable values and unknown usage fields are ignored', async () => {
  const body = result();
  body.usage = { prompt_tokens: 0, completion_tokens: null, reasoning_tokens: null, cached_tokens: 0, future_field: 99 };
  globalThis.fetch = async () => response(body);
  await askLM('Count zeros.');
  assert.equal(el('responsePromptTokens').textContent, '0');
  assert.equal(el('responseCachedTokens').textContent, '0');
  assert.equal(el('responseCompletionTokens').textContent, 'Unavailable');
  assert.equal(el('responseReasoningTokens').textContent, 'Unavailable');
});

test('all-null usage remains unavailable even if an unknown field is present', async () => {
  const body = result();
  body.usage = { prompt_tokens: null, completion_tokens: null, reasoning_tokens: null, cached_tokens: null, future_field: 99 };
  globalThis.fetch = async () => response(body);
  await askLM('No observations.');
  assert.equal(el('responseUsageStatus').textContent, 'Usage unavailable for this response.');
});

for (const [name, change] of [
  ['older backend', body => { delete body.usage; delete body.request_settings; }],
  ['missing usage', body => { body.usage = null; }],
  ['negative count', body => { body.usage.prompt_tokens = -1; }],
  ['boolean count', body => { body.usage.cached_tokens = false; }],
  ['unsafe integer', body => { body.usage.prompt_tokens = Number.MAX_SAFE_INTEGER + 1; }],
  ['missing count field', body => { delete body.usage.reasoning_tokens; }],
  ['cache exceeds prompt', body => { body.usage.cached_tokens = 1025; }],
  ['reasoning exceeds completion', body => { body.usage.reasoning_tokens = 65; }],
  ['invalid request model', body => { body.request_settings.model = 'x'.repeat(513); }],
  ['invalid request budget', body => { body.request_settings.max_tokens = 8193; }],
  ['invalid Meta effort', body => { body.request_settings.reasoning_effort = 'none'; }],
]) {
  test(`${name} cannot block a valid reply or invent provider observations`, async () => {
    const body = result(); change(body);
    globalThis.fetch = async () => response(body);
    assert.equal(await askLM('Keep the reply.'), body.reply);
    assert.equal(el('responseUsageStatus').textContent, 'Usage unavailable for this response.');
    for (const id of ['responsePromptTokens', 'responseCompletionTokens', 'responseReasoningTokens', 'responseCachedTokens']) {
      assert.equal(el(id).textContent, 'Unavailable');
    }
  });
}

test('generic reasoning remains a bounded server string without guessing Meta effort', async () => {
  const body = result();
  body.request_settings = { provider: 'openai-compatible', model: 'generic-model', max_tokens: 180,
    reasoning: 'auto', reasoning_effort: null };
  globalThis.fetch = async () => response(body);
  await askLM('Generic response.');
  assert.match(el('responseUsageSettings').textContent, /Reasoning: auto/);
  assert.doesNotMatch(el('responseUsageSettings').textContent, /effort/);
});

for (const phase of ['headers', 'JSON body']) {
  test(`latest-dispatched chat owns usage after an older ${phase} completes`, async () => {
    const old = deferred(); let calls = 0;
    globalThis.fetch = async () => ++calls === 1
      ? (phase === 'headers' ? old.promise : { ok: true, json: () => old.promise })
      : response(result());
    const older = askLM('Older question.');
    await Promise.resolve();
    await askLM('Current question.');
    const oldResult = result(); oldResult.usage.prompt_tokens = 9999; oldResult.request_settings.model = 'old-model';
    old.resolve(phase === 'headers' ? response(oldResult) : oldResult);
    await older;
    assert.equal(el('responsePromptTokens').textContent, '1024');
    assert.doesNotMatch(el('responseUsageSettings').textContent, /old-model/);
  });

  test(`Clear invalidates usage before abort and ignores late ${phase} metadata`, async () => {
    const pending = deferred(); let signal, statusAtAbort;
    globalThis.fetch = async (_url, options) => {
      signal = options.signal;
      signal.addEventListener('abort', () => { statusAtAbort = el('responseUsageStatus').textContent; });
      return phase === 'headers' ? pending.promise : { ok: true, json: () => pending.promise };
    };
    const old = askLM('Old session.');
    await Promise.resolve();
    clearResponseUsage(); cancelChatRequests(); state.agentSession++;
    assert.equal(signal.aborted, true);
    assert.equal(statusAtAbort, 'No response yet.');
    pending.resolve(phase === 'headers' ? response(result()) : result());
    assert.equal(await old, null);
    assert.equal(el('responseUsageStatus').textContent, 'No response yet.');
    assert.equal(el('responsePromptTokens').textContent, 'Unavailable');
  });
}

test('an older success cannot overwrite the latest request failure', async () => {
  const old = deferred(); let calls = 0;
  globalThis.fetch = async () => { if (++calls === 1) return old.promise; throw new TypeError('Current network failure'); };
  const older = askLM('Old.');
  await assert.rejects(askLM('Latest.'), /Current network failure/);
  old.resolve(response(result())); await older;
  assert.equal(el('responseUsageStatus').textContent, 'Response failed; usage unavailable.');
  assert.equal(el('responsePromptTokens').textContent, 'Unavailable');
});

test('Stop Live retires only its pending observation while typed Ask stays current', async () => {
  state.live = true;
  const liveController = new AbortController(); state.liveTurnController = liveController;
  const live = deferred(), typed = deferred(); let calls = 0;
  globalThis.fetch = () => ++calls === 1 ? live.promise : typed.promise;
  const liveChat = askLM('Live.', [], { signal: liveController.signal });
  const typedChat = askLM('Typed.');
  stopLive();
  const body = result(); body.request_settings.model = 'typed-model';
  typed.resolve(response(body)); await typedChat;
  live.resolve(response(result())); assert.equal(await liveChat, null);
  assert.match(el('responseUsageSettings').textContent, /typed-model/);
});

test('Stop Live makes its cancelled pending usage unavailable', async () => {
  state.live = true;
  const controller = new AbortController(); state.liveTurnController = controller;
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true });
  });
  const chat = askLM('Live question.', [], { signal: controller.signal });
  stopLive();
  assert.equal(await chat, null);
  assert.equal(el('responseUsageStatus').textContent, 'Response cancelled; usage unavailable.');
  assert.equal(el('responsePromptTokens').textContent, 'Unavailable');
});
