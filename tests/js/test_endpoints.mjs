import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { populateEndpoints } from '../../Korina/js/endpoints.js';

const list = {
  innerHTML: '',
  set textContent(value) { this.innerHTML = value; },
};
const health = {
  ok: true,
  response_llm_base_url: 'http://127.0.0.1:8080/v1',
  response_llm_chat_url: 'http://127.0.0.1:8080/v1/chat/completions',
  multimodal_stt_base_url: 'http://127.0.0.1:8080/v1',
  multimodal_stt_chat_url: 'http://127.0.0.1:8080/v1/chat/completions',
  agent_base_url: 'http://127.0.0.1:8080/v1',
  agent_chat_url: 'http://127.0.0.1:8080/v1/chat/completions',
  tts_base_url: 'http://127.0.0.1:8880',
  response_llm_load: { ok: true, loaded: true, error: null, models_url: 'http://127.0.0.1:8080/v1/models' },
  multimodal_stt_load: { ok: true, loaded: true, error: null, models_url: 'http://127.0.0.1:8080/v1/models' },
  agent_load: { ok: false, error: 'Connection refused', models_url: 'http://127.0.0.1:8080/v1/models' },
  tts: { ok: true, base_url: 'http://127.0.0.1:8880', health_url: 'http://127.0.0.1:8880/health', error: null },
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
  assert.match(list.innerHTML, /href="http:\/\/demo.local:8080\/v1\/models"/);
});

test('Endpoints uses resolved model and custom health URLs without appending to display bases', async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...health,
    response_llm_base_url: 'https://response.fixture/v1/chat/completions',
    response_llm_chat_url: 'https://response.fixture/v1/chat/completions',
    response_llm_load: { ok: true, models_url: 'https://response.fixture/v1/models' },
    multimodal_stt_base_url: 'https://stt.fixture/audio/v2',
    multimodal_stt_load: { ok: true, models_url: 'https://stt.fixture/audio/v2/models' },
    agent_base_url: 'https://agent.fixture/v1/messages',
    agent_chat_url: 'https://agent.fixture/v1/messages',
    agent_load: { ok: true, models_url: 'https://agent.fixture/v1/models' },
    tts_base_url: 'https://tts.fixture',
    tts: { ok: true, base_url: 'https://tts.fixture', health_url: 'https://tts.fixture/status/ready?x=1&name="fixture"' },
  }) });
  await populateEndpoints();
  for (const url of ['https://response.fixture/v1/models', 'https://stt.fixture/audio/v2/models',
    'https://agent.fixture/v1/models', 'https://response.fixture/v1/chat/completions']) {
    assert.ok(list.innerHTML.includes(`href="${url}"`), `missing resolved URL ${url}`);
  }
  assert.match(list.innerHTML, /href="https:\/\/tts.fixture\/status\/ready\?x=1&amp;name=&quot;fixture&quot;"/);
  assert.doesNotMatch(list.innerHTML, /chat\/completions\/models|messages\/models|tts.fixture\/health/);
});

test('Endpoints maps both loopback hostnames while preserving remote URLs and page-server links', async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...health,
    response_llm_load: { ok: true, models_url: 'http://localhost:8080/v1/models' },
    multimodal_stt_base_url: 'https://stt.remote.fixture/v1',
    multimodal_stt_chat_url: 'https://stt.remote.fixture/v1/chat/completions',
    multimodal_stt_load: { ok: true, models_url: 'https://stt.remote.fixture/v1/models' },
    agent_base_url: 'http://localhost:1234/v1',
    agent_chat_url: 'http://localhost:1234/v1/chat/completions',
    agent_load: { ok: true, models_url: 'http://127.0.0.1:1234/v1/models' },
    tts_base_url: 'https://tts.remote.fixture',
    tts: { ok: true, base_url: 'https://tts.remote.fixture', health_url: 'https://tts.remote.fixture/status' },
  }) });
  await populateEndpoints();
  for (const url of [
    'http://demo.local:8001/api/health', 'http://demo.local:8001/api/ready',
    'http://demo.local:8080/v1', 'http://demo.local:8080/v1/models',
    'http://demo.local:1234/v1', 'http://demo.local:1234/v1/models',
    'https://stt.remote.fixture/v1', 'https://stt.remote.fixture/v1/chat/completions',
    'https://stt.remote.fixture/v1/models', 'https://tts.remote.fixture/status',
  ]) assert.ok(list.innerHTML.includes(`href="${url}"`), `missing mapped or preserved URL ${url}`);
  assert.doesNotMatch(list.innerHTML, /https?:\/\/(?:127\.0\.0\.1|localhost)(?=[:/])/);
});

test('Endpoints leaves custom TTS without a health contract unverified and does not invent a probe URL', async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...health,
    tts_base_url: 'https://custom.fixture',
    tts: { ok: false, base_url: 'https://custom.fixture', health_url: null,
      status: 'unverified', probe_supported: false, error: 'Configure tts_health_url or verify speech manually' },
  }) });
  await populateEndpoints();
  assert.match(list.innerHTML, /href="https:\/\/custom.fixture"/);
  assert.doesNotMatch(list.innerHTML, /custom.fixture\/health|custom.fixture.*unreachable/);
});

test('Endpoints omits resolved links absent from an older health response instead of guessing them', async () => {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true,
    response_llm_base_url: 'https://older.fixture/v1', tts_base_url: 'https://tts-old.fixture',
    tts: { ok: true, base_url: 'https://tts-old.fixture' },
  }) });
  await populateEndpoints();
  assert.match(list.innerHTML, /https:\/\/older.fixture\/v1/);
  assert.doesNotMatch(list.innerHTML, /older.fixture\/v1\/models|tts-old.fixture\/health/);
});

test('Endpoints shows an HTTP health failure instead of an apparently complete endpoint list', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => health });
  await populateEndpoints();
  assert.match(list.innerHTML, /Failed to fetch \/api\/health: HTTP 503/);
  assert.doesNotMatch(list.innerHTML, /Page server|Loading/);
});

for (const payload of [null, [], { ok: false }]) {
  test(`Endpoints rejects invalid health data ${JSON.stringify(payload)}`, async () => {
    globalThis.fetch = async () => ({ ok: true, json: async () => payload });
    await populateEndpoints();
    assert.match(list.innerHTML, /Failed to fetch \/api\/health: Invalid health response/);
    assert.doesNotMatch(list.innerHTML, /Page server|Loading/);
  });
}

test('Endpoints replaces Loading with a visible health fetch failure', async () => {
  globalThis.fetch = async () => { throw new Error('fixture connection failed'); };
  await populateEndpoints();
  assert.match(list.innerHTML, /Failed to fetch \/api\/health: fixture connection failed/);
  assert.doesNotMatch(list.innerHTML, /Loading/);
});
