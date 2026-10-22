import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { populateEndpoints } from '../../Korina/js/endpoints.js';

const list = {
  innerHTML: '',
  set textContent(value) { this.innerHTML = value; },
};
const health = {
  response_llm_base_url: 'http://127.0.0.1:8080/v1',
  response_llm_chat_url: 'http://127.0.0.1:8080/v1/chat/completions',
  multimodal_stt_base_url: 'http://127.0.0.1:8080/v1',
  multimodal_stt_chat_url: 'http://127.0.0.1:8080/v1/chat/completions',
  agent_base_url: 'http://127.0.0.1:8080/v1',
  agent_chat_url: 'http://127.0.0.1:8080/v1/chat/completions',
  tts_base_url: 'http://127.0.0.1:8880',
  response_llm_load: { ok: true, loaded: true, error: null },
  multimodal_stt_load: { ok: true, loaded: true, error: null },
  agent_load: { ok: false, error: 'Connection refused' },
  tts: { ok: true, base_url: 'http://127.0.0.1:8880', error: null },
};

beforeEach(() => {
  list.innerHTML = '';
  globalThis.document = { getElementById: id => id === 'endpointsList' ? list : null };
  globalThis.location = { hostname: 'demo.local', origin: 'http://demo.local:8001' };
});

test('Endpoints renders all health-resolved service groups without remaining on Loading', async () => {
  globalThis.fetch = async url => {
    assert.equal(url, '/api/health');
    return { ok: true, json: async () => health };
  };
  await populateEndpoints();
  for (const group of ['Page server', 'Response LLM', 'Multimodal STT', 'Agent', 'TTS']) {
    assert.ok(list.innerHTML.includes(group), `missing ${group}`);
  }
  assert.doesNotMatch(list.innerHTML, /Loading|Failed to render/);
  assert.match(list.innerHTML, /aria-disabled="true"/);
});

test('Endpoints replaces Loading with a visible health fetch failure', async () => {
  globalThis.fetch = async () => { throw new Error('fixture connection failed'); };
  await populateEndpoints();
  assert.match(list.innerHTML, /Failed to fetch \/api\/health: fixture connection failed/);
  assert.doesNotMatch(list.innerHTML, /Loading/);
});
