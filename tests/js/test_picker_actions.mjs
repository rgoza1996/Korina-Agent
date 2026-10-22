import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { clearAudioProbe } from '../../Korina/js/api.js';
import { loadModelOptions, loadAgentModelOptions, resetSelectedAudioProbe, syncAudioProbeButton } from '../../Korina/js/providers-ui.js';
import { state } from '../../Korina/js/state.js';

const elements = new Map();
const el = id => {
  if (!elements.has(id)) elements.set(id, { value: '', textContent: '', disabled: false, style: {}, dataset: {}, children: [],
    classList: { add() {}, remove() {}, toggle() {} },
    get options() { return this.children; }, set innerHTML(value) { this.children = []; },
    appendChild(child) { this.children.push(child); } });
  return elements.get(id);
};
const response = (body, status = 200) => new Response(JSON.stringify(body), { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise(setImmediate);
const triple = model => `openai-compatible::http://audio.test/v1::${model}`;
const payload = { llm_models: ['response-model', 'another-response'], stt_llm_models: ['audio-a', 'audio-b'],
  stt_llm_models_capabilities: { 'audio-a': { supports_audio_input: true }, 'audio-b': { supports_audio_input: true } } };

beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: el, createElement: () => el(Symbol()) };
  el('llmProvider').value = el('sttLlmProvider').value = 'openai-compatible';
  el('llmBaseUrl').value = 'http://response.test/v1';
  el('sttLlmBaseUrl').value = 'http://audio.test/v1';
  el('lmModel').value = 'response-model'; el('sttLlmModel').value = 'audio-a';
  el('agentProvider').value = 'openai-compatible'; el('agentBaseUrl').value = 'http://agent.test/v1';
  Object.assign(state, { lastModelsPayload: null, lastModelsQuery: '', lastModelsNeedsRefresh: false,
    capabilityFilterOverride: false, audioUnsupported: {
      [triple('audio-a')]: { reason: 'unsupported a' }, [triple('audio-b')]: { reason: 'unsupported b' },
    } });
  syncAudioProbeButton();
});

test('overlapping picker focus and pointer loads share discovery and retain the selected model', async () => {
  const pending = deferred(); let calls = 0;
  globalThis.fetch = async url => {
    if (url === '/api/audio-probe') return response({ entries: {} });
    calls++;
    return pending.promise;
  };
  const focus = loadModelOptions(), pointer = loadModelOptions();
  assert.equal(focus, pointer);
  await tick();
  assert.equal(calls, 1);
  pending.resolve(response(payload)); await focus;
  assert.equal(el('lmModel').value, 'response-model');
  await loadModelOptions();
  assert.equal(calls, 1, 'a completed cached query does not repeat model discovery');
});

test('failed picker discovery can retry the same query without forcing a refresh', async () => {
  let calls = 0;
  globalThis.fetch = async url => {
    if (url === '/api/audio-probe') return response({ entries: {} });
    return ++calls === 1 ? response({ detail: 'Provider unavailable' }, 503) : response(payload);
  };
  await loadModelOptions();
  assert.match(el('modelOptionsStatus').textContent, /503.*Provider unavailable/);
  await loadModelOptions();
  assert.equal(calls, 2);
  assert.equal(el('lmModel').value, 'response-model');
});

test('the actual STT picker filters text models and its override retains a visible no-audio label', async () => {
  globalThis.fetch = async url => url === '/api/audio-probe' ? response({ entries: {} })
    : response({ ...payload, stt_llm_models: ['audio-a', 'text-only'], stt_llm_models_capabilities: {
      'audio-a': { supports_audio_input: true }, 'text-only': { supports_audio_input: false },
    } });
  await loadModelOptions(true);
  assert.deepEqual(el('sttLlmModel').options.map(option => option.value), ['', 'audio-a']);
  state.capabilityFilterOverride = true;
  await loadModelOptions();
  const text = el('sttLlmModel').options.find(option => option.value === 'text-only');
  assert.match(text.textContent, /no audio/);
});

test('agent focus coalesces boot discovery, reuses success, and refreshes a changed candidate', async () => {
  const first = deferred(); let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? first.promise : response({ ok: true, models: ['agent-b'] });
  const boot = loadAgentModelOptions(), focus = loadAgentModelOptions(false);
  assert.equal(boot, focus);
  first.resolve(response({ ok: true, models: ['agent-a'] })); await boot;
  await loadAgentModelOptions(false);
  assert.equal(calls, 1);
  el('agentBaseUrl').value = 'http://replacement.test/v1';
  await loadAgentModelOptions(false);
  assert.equal(calls, 2);
  assert.ok(el('agentModel').options.some(option => option.value === 'agent-b'));
});

for (const [name, failed] of [
  ['HTTP503', () => response({ detail: { reason: 'Disk unavailable' } }, 503)],
  ['network error', () => { throw new TypeError('Failed to fetch'); }],
  ['invalid JSON', () => new Response('private malformed body')],
  ['wrong confirmation', () => response({ triple: triple('different'), removed: true })],
  ['non-boolean confirmation', () => response({ triple: triple('audio-a'), removed: 'yes' })],
  ['oversized response', () => new Response('x'.repeat(65537))],
]) {
  test(`probe reset ${name} is visible and leaves its cache and retry control intact`, async () => {
    globalThis.fetch = async () => failed();
    await resetSelectedAudioProbe();
    assert.match(el('settingsActionStatus').textContent, /Audio probe reset failed:.*Retry Reset audio probe/);
    assert.doesNotMatch(el('settingsActionStatus').textContent, /private malformed body/);
    assert.equal(el('clearProbeBtn').disabled, false);
    assert.equal(el('clearProbeBtn').style.display, '');
    assert.ok(state.audioUnsupported[triple('audio-a')]);
  });
}

test('an already absent probe is a valid confirmed reset', async () => {
  globalThis.fetch = async () => response({ triple: triple('audio-a'), removed: false });
  assert.deepEqual(await clearAudioProbe('openai-compatible', 'http://audio.test/v1/chat/completions/', 'audio-a'),
    { triple: triple('audio-a'), removed: false });
});

test('an old reset completion cannot hide a new selected probe or replace its error', async () => {
  const pending = deferred(); let calls = 0;
  globalThis.fetch = async () => { calls++; return pending.promise; };
  const old = resetSelectedAudioProbe();
  el('sttLlmModel').value = 'audio-b'; syncAudioProbeButton();
  el('settingsActionStatus').textContent = 'A newer settings failure.';
  pending.resolve(response({ triple: triple('audio-a'), removed: true })); await old;
  assert.equal(calls, 1, 'retired reset does not start discovery');
  assert.equal(el('clearProbeBtn').dataset.model, 'audio-b');
  assert.equal(el('clearProbeBtn').style.display, '');
  assert.equal(el('clearProbeBtn').disabled, false);
  assert.equal(el('settingsActionStatus').textContent, 'A newer settings failure.');
  assert.ok(state.audioUnsupported[triple('audio-b')]);
});

test('changing selection retires a held reset immediately and its finally cannot release the next reset', async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    const pending = deferred();
    requests.push({ ...pending, signal: options.signal });
    return pending.promise;
  };
  const old = resetSelectedAudioProbe();
  el('sttLlmModel').value = 'audio-b'; syncAudioProbeButton();
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(el('clearProbeBtn').disabled, false);
  assert.equal(el('settingsActionStatus').textContent, '');
  const fresh = resetSelectedAudioProbe();
  requests[0].resolve(response({ triple: triple('audio-a'), removed: true })); await old;
  assert.equal(el('clearProbeBtn').disabled, true);
  assert.equal(el('settingsActionStatus').textContent, 'Resetting audio capability probe…');
  assert.equal(requests[1].signal.aborted, false);
  requests[1].resolve(response({ detail: 'Current reset failed' }, 503)); await fresh;
  assert.equal(el('clearProbeBtn').disabled, false);
  assert.match(el('settingsActionStatus').textContent, /Current reset failed/);
});

for (const field of ['llmBaseUrl', 'llmApiKeyEnv', 'sttLlmProvider', 'sttLlmBaseUrl', 'sttLlmApiKeyEnv']) {
  test(`a changed ${field} retires reset ownership before its response`, async () => {
    const pending = deferred(); let signal;
    globalThis.fetch = async (url, options) => { signal = options.signal; return pending.promise; };
    const old = resetSelectedAudioProbe();
    el(field).value += '-replacement'; syncAudioProbeButton();
    assert.equal(signal.aborted, true);
    assert.equal(el('clearProbeBtn').disabled, false);
    assert.equal(el('settingsActionStatus').textContent, '');
    pending.resolve(response({ detail: 'Old error' }, 503)); await old;
    assert.equal(el('settingsActionStatus').textContent, '');
  });
}

test('a successful current reset removes only its probe and refreshes model status', async () => {
  globalThis.fetch = async (url, options) => {
    if (options?.method === 'DELETE') return response({ triple: triple('audio-a'), removed: true });
    if (url === '/api/audio-probe') return response({ entries: { [triple('audio-b')]: { reason: 'unsupported b' } } });
    return response(payload);
  };
  await resetSelectedAudioProbe();
  assert.equal(state.audioUnsupported[triple('audio-a')], undefined);
  assert.ok(state.audioUnsupported[triple('audio-b')]);
  assert.equal(el('clearProbeBtn').style.display, 'none');
  assert.match(el('settingsActionStatus').textContent, /probe reset.*Try transcription again/);
});
