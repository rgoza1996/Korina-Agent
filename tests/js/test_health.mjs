import test from 'node:test';
import assert from 'node:assert/strict';
import { health } from '../../Korina/js/api.js';
import { state } from '../../Korina/js/state.js';
import { cancelAckPlayback, changeAckVoice, loadAcks } from '../../Korina/js/acks.js';

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

test('Meta health reports provider-default reasoning instead of the ignored local toggle', async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy, response_llm_provider: 'meta' }) });
  el('llmReasoningEnabled').checked = false;
  await health();
  assert.match(el('pageHealth').textContent, /response meta demo \(provider-default reasoning\)/);
  assert.doesNotMatch(el('pageHealth').textContent, /response meta demo \(reasoning off\)/);
});

test('Meta health uses effective server effort independently for response and agent, not unsaved form drafts', async () => {
  el('llmReasoningEffort').value = 'xhigh';
  el('agentReasoningEffort').value = 'minimal';
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy,
    response_llm_provider: 'meta', response_llm_reasoning_effort: 'low',
    agent_provider: 'meta', agent_reasoning_effort: 'high',
  }) });
  await health();
  assert.match(el('pageHealth').textContent, /response meta demo \(reasoning effort low\)/);
  assert.match(el('settingsLiveStatus').textContent, /Agent configured with reasoning effort high/);
  assert.doesNotMatch(el('pageHealth').textContent, /effort xhigh/);
  assert.doesNotMatch(el('settingsLiveStatus').textContent, /effort minimal/);
});

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

test('health accepts only resolved numeric capture budgets and preserves them through old or failed polls', async () => {
  state.audioLimits = null;
  state.audioLimitsError = '';
  const initialConfig = structuredClone(state.appConfig);
  const limits = { max_duration_seconds: 90.5, max_upload_bytes: 4 * 1024 * 1024 };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy, audio_limits: limits }) });
  await health();
  assert.deepEqual(state.audioLimits, limits);
  assert.notEqual(state.audioLimits, limits);
  assert.deepEqual(state.appConfig, initialConfig, 'read-only budgets are not persisted settings');

  for (const malformed of [
    null, [], { max_duration_seconds: '120', max_upload_bytes: 1024 },
    { max_duration_seconds: Infinity, max_upload_bytes: 1024 },
    { max_duration_seconds: NaN, max_upload_bytes: 1024 },
    { max_duration_seconds: 0, max_upload_bytes: 1024 },
    { max_duration_seconds: 1801, max_upload_bytes: 1024 },
    { max_duration_seconds: 120, max_upload_bytes: 0 },
    { max_duration_seconds: 120, max_upload_bytes: 1.5 },
    { max_duration_seconds: 120, max_upload_bytes: Number.MAX_SAFE_INTEGER + 1 },
  ]) {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy, audio_limits: malformed }) });
    await health();
    assert.deepEqual(state.audioLimits, limits);
  }
  globalThis.fetch = async () => ({ ok: true, json: async () => healthy });
  await health();
  assert.deepEqual(state.audioLimits, limits);
  globalThis.fetch = async () => { throw new Error('temporary network loss'); };
  await health();
  assert.deepEqual(state.audioLimits, limits);
});

test('an explicit server audio-limit error blocks recording until valid limits recover', async () => {
  const error = 'KORINA_MAX_AUDIO_SECONDS must be between 1 and 1800 seconds';
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy, audio_limits: null, audio_limits_error: error }) });
  await health();
  assert.equal(state.audioLimitsError, error);
  assert.match(el('settingsLiveStatus').textContent, /STT down \(KORINA_MAX_AUDIO_SECONDS/);
  const recovered = { max_duration_seconds: 120, max_upload_bytes: 1024 * 1024 };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy, audio_limits: recovered, audio_limits_error: null }) });
  await health();
  assert.equal(state.audioLimitsError, '');
  assert.deepEqual(state.audioLimits, recovered);
  assert.match(el('settingsLiveStatus').textContent, /STT up/);
});

test('a degraded ACK catalog reports unknown counts separately and recovers without claiming audio ready', async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy,
    ack_count: null, ack_status: { ok: false, error: 'Repair Ack/ack_phrases.json and retry', generated_count: null } }) });
  await health();
  assert.match(el('ackStatus').textContent, /clip counts unknown.*Repair Ack/);
  assert.doesNotMatch(el('ackStatus').textContent, /0 cached|ready/);
  assert.match(el('settingsLiveStatus').textContent, /LLM up · STT up/);
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...healthy, ack_status: { ok: true, generated_count: 0 } }) });
  await health();
  assert.match(el('ackStatus').textContent, /catalog available; cached clips reload on use/);
  assert.doesNotMatch(el('ackStatus').textContent, /ready/);
});

test('an old health response cannot overwrite a newer voice operation diagnostic', async () => {
  let finish;
  globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = health();
  await changeAckVoice(async () => { throw new Error('Disk unavailable'); });
  const newerMessage = el('ackStatus').textContent;
  finish({ ok: true, json: async () => ({ ...healthy, ack_status: { ok: false, error: 'Older catalog error' } }) });
  await pending;
  assert.equal(el('ackStatus').textContent, newerMessage);
  assert.match(newerMessage, /Voice settings save failed: Disk unavailable/);
  cancelAckPlayback();
});

test('successful catalog revalidation owns status even when its visible text is identical', async () => {
  let finish;
  el('voice').value = 'af_heart';
  el('ackStatus').textContent = '1 cached acknowledgement clips available.';
  globalThis.fetch = url => url === '/api/health' ? new Promise(resolve => { finish = resolve; })
    : Promise.resolve(new Response(JSON.stringify({ acks: [{ url: '/Ack/current.wav', text: 'Current catalog' }] })));
  const pending = health();
  const revision = state.ackStatusRevision;
  await loadAcks();
  assert.equal(el('ackStatus').textContent, '1 cached acknowledgement clips available.');
  assert(state.ackStatusRevision > revision);
  finish({ ok: true, json: async () => ({ ...healthy, ack_status: { ok: false, error: 'Older catalog error' } }) });
  await pending;
  assert.equal(el('ackStatus').textContent, '1 cached acknowledgement clips available.');
  assert.equal(el('ackStatus').className, 'status good');
});
