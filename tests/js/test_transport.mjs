import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { transcribeBlob } from '../../Korina/js/recorder.js';
import { loadConfig } from '../../Korina/js/api.js';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', className: '', checked: false, options: [],
    querySelector() { return null; }, appendChild() {}, classList: { toggle() {} },
  });
  return elements.get(id);
}
beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: element, querySelectorAll: () => [] };
  globalThis.location = { protocol: 'http:', hostname: 'localhost', host: 'localhost' };
});

function sse(parts) {
  let index = 0;
  const observed = { cancelled: 0 };
  return { observed, response: { ok: true, body: { getReader: () => ({
    read: async () => index < parts.length
      ? { done: false, value: new TextEncoder().encode(parts[index++]) } : { done: true },
    cancel: async () => { observed.cancelled++; },
  }) } } };
}

test('full STT accepts CRLF events and preserves non-ASCII transcript text', async () => {
  const text = 'café 🦊';
  const { response } = sse([
    `event: segment\r\ndata: {"text":"${text}","index":1}\r`,
    `\n\r\nevent: done\r\ndata: {"text":"${text}","backend":"faster-whisper","seconds":0.1}\r\n\r\n`,
  ]);
  globalThis.fetch = async () => response;
  assert.equal((await transcribeBlob(new Blob(['audio']))).text, text);
  assert.equal(element('transcript').value, text);
  assert.match(element('sttBackendHealth').textContent, /whisper/);
});

test('full STT surfaces backend error events in the pill and cancels the stream', async () => {
  const { response, observed } = sse(['event: error\ndata: {"detail":"Whisper model failed"}\n\n']);
  globalThis.fetch = async () => response;
  await assert.rejects(transcribeBlob(new Blob(['audio'])), /Whisper model failed/);
  assert.equal(element('sttBackendDot').className, 'dot bad');
  assert.match(element('sttBackendHealth').textContent, /Whisper model failed/);
  assert.equal(observed.cancelled, 1);
});

for (const [name, event, expected] of [
  ['a non-string segment', 'event: segment\ndata: {"text":42}\n\n', /Invalid STT transcript segment/],
  ['a malformed final result', 'event: done\ndata: {"text":[]}\n\n', /Invalid STT final result/],
  ['nonfinite timing', 'event: done\ndata: {"text":"words","seconds":1e999}\n\n', /Invalid STT final result: seconds/],
  ['an unfinished final result', 'event: done\ndata: {"text":"words"}', /incomplete event/],
]) {
  test(`full STT rejects ${name} and cancels its response`, async () => {
    const { response, observed } = sse([event]);
    globalThis.fetch = async () => response;
    await assert.rejects(transcribeBlob(new Blob(['audio'])), expected);
    assert.equal(observed.cancelled, 1);
    assert.equal(element('sttBackendDot').className, 'dot bad');
  });
}

for (const [name, response] of [
  ['HTTP 503 JSON', { ok: false, status: 503, json: async () => ({ detail: 'Unavailable' }) }],
  ['HTTP 503 HTML', { ok: false, status: 503, json: async () => { throw new Error('Unexpected HTML'); } }],
  ['a JSON null body', { ok: true, json: async () => null }],
  ['a JSON array body', { ok: true, json: async () => [] }],
  ['an error envelope', { ok: true, json: async () => ({ detail: 'Unavailable' }) }],
]) {
  test(`settings load rejects ${name} without mutating saved form state`, async () => {
    const config = { llm_provider: 'lmstudio', lm_model: 'saved-model', llm_reasoning: 'on' };
    state.appConfig = { ...config };
    state.appConfigSnapshot = { ...config };
    element('llmReasoningEnabled').checked = true;
    element('sttLlmModel').value = 'saved-stt';
    element('settingsInfo').textContent = 'Previously loaded settings.';
    globalThis.fetch = async () => response;
    const warn = console.warn;
    console.warn = () => {};
    try { await loadConfig(); } finally { console.warn = warn; }
    assert.deepEqual(state.appConfig, config);
    assert.deepEqual(state.appConfigSnapshot, config);
    assert.equal(element('llmReasoningEnabled').checked, true);
    assert.equal(element('sttLlmModel').value, 'saved-stt');
    assert.equal(element('settingsInfo').textContent, 'Previously loaded settings.');
    assert.match(element('settingsActionStatus').textContent, /Settings load failed:/);
    assert.equal(element('sttStatus').className, 'status bad');
  });
}

test('a valid settings load still applies the config and records its provider snapshot', async () => {
  const config = { llm_provider: 'lmstudio', llm_reasoning: 'on', stt_llm_model: '' };
  globalThis.fetch = async () => ({ ok: true, json: async () => config });
  await loadConfig();
  assert.equal(element('llmReasoningEnabled').checked, true);
  assert.equal(element('llmProvider').value, 'lmstudio');
  assert.equal(state.appConfigSnapshot.llm_provider, 'lmstudio');
  assert.equal(element('settingsInfo').textContent, 'Loaded saved config.json settings.');
});
