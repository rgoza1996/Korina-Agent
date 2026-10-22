import test from 'node:test';
import assert from 'node:assert/strict';
import { health } from '../../Korina/js/api.js';
import { state } from '../../Korina/js/state.js';

const elements = new Map();
globalThis.document = { getElementById(id) {
  if (!elements.has(id)) elements.set(id, { value: '', textContent: '', className: '' });
  return elements.get(id);
} };
globalThis.location = { host: 'demo.local' };
const el = id => document.getElementById(id);

const healthy = {
  ok: true, whisper_loaded: true, whisper_available: true, stt_backend: 'whisper',
  response_llm_provider: 'lmstudio', response_llm_model: 'demo',
  response_llm_base_url: 'http://server.local:1234/v1',
  response_llm_load: { ok: true, loaded: true, provider: 'lmstudio', loaded_model: 'demo' },
  tts: { ok: true, loaded: true, provider: 'openai-compatible', base_url: 'http://tts.local', device: 'cpu' },
};

test('health reflects server truth and survives network loss while switching provider', async () => {
  state.providerPending = true;
  globalThis.fetch = async () => ({ ok: true, json: async () => structuredClone(healthy) });
  await health();
  assert.equal(state.providerPending, false);
  assert.match(el('hostInfo').textContent, /server.local:1234/);
  assert.match(el('ttsHealth').textContent, /OpenAI TTS ready/);
  assert.match(el('settingsLiveStatus').textContent, /LLM up · STT up/);
  const lastReady = el('providerReady').textContent;
  state.providerPending = true;
  globalThis.fetch = async () => { throw new Error('temporarily offline'); };
  await health();
  assert.equal(state.healthFailures, 1);
  assert.equal(el('pageDot').className, 'dot good');
  assert.equal(el('providerReady').textContent, lastReady);
  await health();
  await health();
  assert.equal(el('pageDot').className, 'dot bad');
  assert.match(el('pageHealth').textContent, /unreachable for 15s/);

  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => healthy });
  await health();
  assert.equal(state.healthFailures, 4, 'HTTP failure must not reset the failure counter');
  globalThis.fetch = async () => ({ ok: true, json: async () => healthy });
  await health();
  assert.equal(state.healthFailures, 0);
  assert.equal(el('pageDot').className, 'dot good');
});

test('live settings show unavailable models instead of inferring success from missing errors', async () => {
  const payload = structuredClone(healthy);
  payload.response_llm_load = { ok: false, loaded: false, error: 'connection refused' };
  payload.whisper_available = false;
  globalThis.fetch = async () => ({ ok: true, json: async () => payload });
  await health();
  assert.match(el('settingsLiveStatus').textContent, /LLM down \(connection refused\)/);
  assert.match(el('settingsLiveStatus').textContent, /STT down/);
});

test('unsupported custom TTS health remains unverified instead of claiming offline', async () => {
  const payload = structuredClone(healthy);
  payload.tts = {
    ok: false, loaded: false, provider: 'openai-compatible',
    base_url: 'https://tts.example/v1', status: 'unverified', probe_supported: false,
  };
  globalThis.fetch = async () => ({ ok: true, json: async () => payload });
  await health();
  assert.equal(el('ttsDot').className, 'dot warn');
  assert.match(el('ttsHealth').textContent, /OpenAI TTS health unverified/);
  assert.doesNotMatch(el('ttsHealth').textContent, /offline/);
});
