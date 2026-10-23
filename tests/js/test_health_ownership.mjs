import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { health, activateSelectedProvider } from '../../Korina/js/api.js';
import { state } from '../../Korina/js/state.js';
import { captureLimits } from '../../Korina/js/bounded-recorder.js';

const elements = new Map();
const el = id => {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '' });
  return elements.get(id);
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = payload => ({ ok: true, json: async () => payload });
function payload(provider = 'lmstudio', model = 'old-model') {
  return { ok: true, whisper_loaded: true, whisper_available: true, stt_backend: 'whisper',
    response_llm_provider: provider, response_llm_model: model, response_llm_base_url: `http://${provider}.fixture/v1`,
    response_llm_load: { ok: true, loaded: true, provider, loaded_model: model, expected_model: model },
    tts: { ok: true, loaded: true, provider: 'kokoro', base_url: 'http://tts.fixture' },
    audio_limits: { max_duration_seconds: 90, max_upload_bytes: 4 * 1024 * 1024 }, audio_limits_error: '',
  };
}
beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: el };
  globalThis.location = { host: 'fixture.local' };
  Object.assign(state, { healthFailures: 0, providerPending: false, audioLimits: null, audioLimitsError: '',
    liveSession: 1, agentSession: 1, appConfig: {}, ackStatusRevision: 0 });
  el('llmProvider').value = 'lmstudio'; el('lmModel').value = 'old-model';
  el('llmBaseUrl').value = 'http://lmstudio.fixture/v1';
});

for (const phase of ['fetch', 'JSON body']) {
  test(`an older healthy ${phase} cannot replace a newly activated provider's loading state`, async () => {
    const old = deferred(), fresh = deferred();
    let polls = 0;
    globalThis.fetch = async url => {
      if (url === '/api/llm/provider/activate') return response({ saved: {
        llm_provider: 'ollama', lm_model: 'new-model', llm_base_url: 'http://ollama.fixture/v1' } });
      if (++polls === 1) return phase === 'fetch' ? old.promise : { ok: true, json: () => old.promise };
      return fresh.promise;
    };
    const retired = health();
    await Promise.resolve();
    el('llmProvider').value = 'ollama'; el('lmModel').value = 'new-model';
    el('llmBaseUrl').value = 'http://ollama.fixture/v1';
    await activateSelectedProvider('ollama', 'new-model');
    const current = health();
    const loading = payload('ollama', 'new-model'); loading.response_llm_load.loaded = false;
    fresh.resolve(response(loading)); await current;
    const ready = el('providerReady').textContent, host = el('hostInfo').textContent;
    assert.match(ready, /ollama · loading new-model/);
    old.resolve(phase === 'fetch' ? response(payload()) : payload()); await retired;
    assert.equal(el('providerReady').textContent, ready);
    assert.equal(el('providerReadyDot').className, 'dot warn');
    assert.equal(el('hostInfo').textContent, host);
    assert.equal(state.providerPending, true);
  });
}

test('a health result started during same-selection activation is retired on successful activation', async () => {
  const activation = deferred(), poll = deferred();
  const saved = { llm_provider: 'lmstudio', lm_model: 'old-model', llm_base_url: 'http://lmstudio.fixture/v1' };
  state.appConfig = { ...saved };
  globalThis.fetch = url => url === '/api/llm/provider/activate' ? activation.promise : poll.promise;
  const activating = activateSelectedProvider('lmstudio', 'old-model');
  const retired = health();
  activation.resolve(response({ saved })); await activating;
  el('providerReady').textContent = 'Activation is awaiting fresh health';
  poll.resolve(response(payload())); await retired;
  assert.equal(state.providerPending, true);
  assert.equal(el('providerReady').textContent, 'Activation is awaiting fresh health');
});

test('a failed same-selection activation also retires a poll started during its partial work', async () => {
  const activation = deferred(), poll = deferred();
  globalThis.fetch = url => url === '/api/llm/provider/activate' ? activation.promise : poll.promise;
  const activating = activateSelectedProvider('lmstudio', 'old-model');
  const failure = assert.rejects(activating, /Fixture activation failed/);
  const retired = health();
  activation.resolve({ ok: false, status: 500, json: async () => ({ detail: 'Fixture activation failed' }) });
  await failure;
  el('providerReady').textContent = 'Activation failed; waiting for fresh health';
  poll.resolve(response(payload())); await retired;
  assert.equal(el('providerReady').textContent, 'Activation failed; waiting for fresh health');
});

test('a pre-Clear audio-limit error cannot disable capture after newer valid budgets', async () => {
  const old = deferred(), fresh = deferred(); let polls = 0;
  globalThis.fetch = () => ++polls === 1 ? old.promise : fresh.promise;
  const retired = health(); state.agentSession++;
  const current = health(); fresh.resolve(response(payload())); await current;
  old.resolve(response({ ...payload(), audio_limits: null, audio_limits_error: 'Older invalid limits' }));
  await retired;
  assert.equal(state.audioLimitsError, '');
  assert.equal(captureLimits().maxSeconds, 90);
  assert.match(el('settingsLiveStatus').textContent, /STT up/);
});

test('retired network failures do not count against a newer successful health response', async () => {
  const older = Array.from({ length: 3 }, deferred), fresh = deferred(); let polls = 0;
  globalThis.fetch = () => polls < 3 ? older[polls++].promise : fresh.promise;
  const retired = older.map(() => health());
  const current = health(); fresh.resolve(response(payload())); await current;
  for (const [index, pending] of older.entries()) {
    pending.reject(new Error('Older outage')); await retired[index];
  }
  assert.equal(state.healthFailures, 0);
  assert.equal(el('pageDot').className, 'dot good');
  assert.doesNotMatch(el('pageHealth').textContent, /unreachable/);
});

for (const [label, invalidate] of [
  ['provider form selection', () => { el('lmModel').value = 'replacement-model'; }],
  ['saved endpoint', () => { state.appConfig.llm_base_url = 'http://replacement.fixture/v1'; }],
  ['response effort draft', () => { el('llmReasoningEffort').value = 'minimal'; }],
  ['confirmed agent effort', () => { state.appConfig.agent_reasoning_effort = 'high'; }],
  ['Clear Session', () => { state.agentSession++; }],
  ['Stop Live', () => { state.liveSession++; }],
]) {
  test(`${label} retires a pending health snapshot without requiring another poll`, async () => {
    const pending = deferred(); globalThis.fetch = () => pending.promise;
    const retired = health(); invalidate();
    el('pageHealth').textContent = 'Current owner';
    pending.resolve(response({ ...payload(), audio_limits_error: 'Retired error' })); await retired;
    assert.equal(el('pageHealth').textContent, 'Current owner');
    assert.equal(state.audioLimitsError, '');
    assert.equal(state.healthFailures, 0);
  });
}

test('unrelated voice and speed edits do not discard a current runtime health snapshot', async () => {
  const pending = deferred(); globalThis.fetch = () => pending.promise;
  const current = health();
  el('speed').value = '1.8'; el('voice').value = 'af_bella';
  state.appConfig.speed = 1.8; state.appConfig.voice = 'af_bella';
  pending.resolve(response(payload())); await current;
  assert.match(el('providerReady').textContent, /ready · old-model/);
  assert.equal(state.audioLimits.max_duration_seconds, 90);
});
