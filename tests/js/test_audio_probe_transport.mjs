import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { listAudioProbes } from '../../Korina/js/audio-probes.js';
import { loadModelOptions, resetSelectedAudioProbe, syncAudioProbeButton } from '../../Korina/js/providers-ui.js';
import { state } from '../../Korina/js/state.js';

const nativeFetch = globalThis.fetch;
const triple = model => `openai-compatible::http://audio.fixture/v1::${model}`;
const models = { llm_models: ['response-model'], stt_llm_models: ['audio-a'],
  stt_llm_models_capabilities: { 'audio-a': { supports_audio_input: true } } };
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function until(predicate, timeout = 1000) {
  const deadline = performance.now() + timeout;
  while (!predicate() && performance.now() < deadline) await delay(5);
  assert.ok(predicate(), 'expected local HTTP phase did not occur');
}
function json(response, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
  response.end(body);
}
async function fixture(t, handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  globalThis.fetch = (path, options) => nativeFetch(new URL(path, `http://127.0.0.1:${server.address().port}`), options);
  t.after(async () => {
    globalThis.fetch = nativeFetch;
    const closed = new Promise(resolve => server.close(resolve));
    server.closeAllConnections();
    await closed;
  });
}
function dom() {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, { value: '', textContent: '', disabled: false, style: {}, dataset: {}, children: [],
      classList: { add() {}, remove() {}, toggle() {} },
      get options() { return this.children; }, set innerHTML(value) { this.children = []; },
      appendChild(child) { this.children.push(child); } });
    return elements.get(id);
  };
  globalThis.document = { getElementById: el, createElement: () => el(Symbol()) };
  el('llmProvider').value = el('sttLlmProvider').value = 'openai-compatible';
  el('llmBaseUrl').value = 'http://response.fixture/v1'; el('sttLlmBaseUrl').value = 'http://audio.fixture/v1';
  el('lmModel').value = 'response-model'; el('sttLlmModel').value = 'audio-a';
  Object.assign(state, { lastModelsPayload: null, lastModelsQuery: '', lastModelsNeedsRefresh: false,
    capabilityFilterOverride: false, audioUnsupported: { [triple('audio-a')]: { reason: 'unsupported a' },
      [triple('audio-b')]: { reason: 'unsupported b' } } });
  return el;
}

for (const phase of ['headers', 'body']) {
  test(`retiring a cache owner closes a real stalled ${phase} connection and permits a fresh request`, { timeout: 3000 }, async t => {
    let calls = 0, entered = false, closed = false;
    await fixture(t, (_request, response) => {
      if (++calls > 1) return json(response, { entries: {} });
      response.on('close', () => { closed = true; });
      if (phase === 'body') { response.writeHead(200); response.write('{'); }
      entered = true;
    });
    const owner = new AbortController();
    const old = listAudioProbes({ signal: owner.signal });
    const rejected = assert.rejects(old, { name: 'AbortError' });
    await until(() => entered);
    owner.abort();
    await rejected; await until(() => closed);
    assert.deepEqual(await listAudioProbes(), { entries: {} });
  });
}

test('cache timeout is one five-second budget shared by headers and a stalled body', { timeout: 10000 }, async t => {
  let entered = false, bodyStarted = false, closed = false;
  await fixture(t, (_request, response) => {
    entered = true;
    const headers = setTimeout(() => { bodyStarted = true; response.writeHead(200); response.write('{'); }, 2500);
    response.on('close', () => { closed = true; clearTimeout(headers); });
  });
  const started = performance.now();
  await assert.rejects(listAudioProbes(), { name: 'TimeoutError' });
  const elapsed = performance.now() - started;
  assert.equal(entered, true); assert.equal(bodyStarted, true);
  assert.ok(elapsed >= 4500 && elapsed < 7000, `total deadline elapsed ${elapsed}ms`);
  await until(() => closed);
});

for (const retire of ['forced refresh', 'query change without another request']) {
  test(`${retire} aborts a held probe GET before model admission and allows current discovery`, { timeout: 3000 }, async t => {
    const el = dom();
    let probes = 0, modelCalls = 0, entered = false, closed = false;
    await fixture(t, (request, response) => {
      if (request.url.startsWith('/api/models')) { modelCalls++; return json(response, models); }
      if (++probes > 1) return json(response, { entries: {} });
      response.on('close', () => { closed = true; });
      response.writeHead(200); response.write('{'); entered = true;
    });
    const old = loadModelOptions(true);
    await until(() => entered);
    assert.equal(loadModelOptions(), old, 'same-query focus coalesces while its request is current');
    let fresh;
    if (retire === 'forced refresh') fresh = loadModelOptions(true);
    else { el('llmBaseUrl').value = 'http://new-response.fixture/v1'; syncAudioProbeButton(); }
    await until(() => closed); await old;
    if (!fresh) {
      assert.equal(modelCalls, 0, 'retired query must not continue to models');
      fresh = loadModelOptions();
    }
    await fresh;
    assert.equal(modelCalls, 1); assert.equal(probes, 2);
    assert.equal(el('lmModel').value, 'response-model');
    assert.deepEqual(state.audioUnsupported, {});
  });
}

test('confirmed Reset releases immediately while a timed-out optional refresh retains other entries', { timeout: 10000 }, async t => {
  const el = dom();
  let entered = false, closed = false, modelCalls = 0;
  await fixture(t, (request, response) => {
    if (request.method === 'DELETE') return json(response, { triple: triple('audio-a'), removed: true });
    if (request.url.startsWith('/api/models')) { modelCalls++; return json(response, models); }
    response.on('close', () => { closed = true; });
    response.writeHead(200); response.write('{'); entered = true;
  });
  await resetSelectedAudioProbe();
  assert.equal(el('clearProbeBtn').disabled, false);
  assert.match(el('settingsActionStatus').textContent, /probe reset.*Try transcription again/);
  assert.equal(state.audioUnsupported[triple('audio-a')], undefined);
  assert.ok(state.audioUnsupported[triple('audio-b')]);
  await until(() => entered);
  await loadModelOptions(); // Join the optional refresh until its finite timeout.
  await until(() => closed);
  assert.equal(modelCalls, 1);
  assert.equal(state.audioUnsupported[triple('audio-a')], undefined);
  assert.ok(state.audioUnsupported[triple('audio-b')]);
  assert.equal(el('clearProbeBtn').disabled, false);
  assert.match(el('modelOptionsStatus').textContent, /cache unavailable.*retained.*retry/);
});
