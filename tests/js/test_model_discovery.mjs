import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { loadAgentModelOptions, loadModelOptions } from '../../Korina/js/providers-ui.js';
import { loadLocalModelRoots, wireLocalModelsUi } from '../../Korina/js/local-models.js';
import { MAX_MODEL_RESPONSE_BYTES } from '../../Korina/js/model-catalog.js';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', style: {}, dataset: {}, children: [], listeners: {},
    classList: { add() {}, remove() {}, toggle() {} },
    get options() { return this.children; },
    set innerHTML(value) { this.children = []; },
    appendChild(node) { this.children.push(node); },
    addEventListener(name, listener) { this.listeners[name] = listener; },
  });
  return elements.get(id);
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
function payload(tag) {
  return { whisper_models: ['base.en'], llm_models: [`${tag}-response`],
    llm_default: `${tag}-response`,
    stt_llm_models: [`${tag}-audio`],
    stt_llm_models_capabilities: { [`${tag}-audio`]: { supports_audio_input: true } } };
}
function endpoint(tag) {
  element('llmBaseUrl').value = `http://${tag}.example/v1`;
  element('sttLlmBaseUrl').value = `http://${tag}-audio.example/v1`;
}
let pending;
beforeEach(() => {
  elements.clear(); pending = [];
  globalThis.window = {};
  globalThis.document = { getElementById: id => elements.get(id), createElement: () => element(Symbol()) };
  for (const id of ['llmProvider', 'llmApiKeyEnv', 'sttLlmProvider', 'sttLlmApiKeyEnv', 'sttModel',
    'lmModel', 'sttLlmModel', 'agentProvider', 'agentBaseUrl', 'agentApiKey', 'agentModel', 'modelOptionsStatus']) element(id);
  element('llmProvider').value = element('sttLlmProvider').value = 'openai-compatible';
  element('agentProvider').value = 'openai-compatible';
  element('agentBaseUrl').value = 'http://agent.example/v1';
  endpoint('old');
  Object.assign(state, { lastModelsPayload: null, lastModelsQuery: '',
    audioUnsupported: {}, capabilityFilterOverride: false });
  state.lastModelsNeedsRefresh = false;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/audio-probe') return response({ entries: {} });
    const result = deferred();
    pending.push({ url, options, ...result });
    return result.promise;
  };
});
async function waitRequests(count) {
  for (let turn = 0; turn < 100 && pending.length < count; turn++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(pending.length >= count, `expected ${count} requests, received ${pending.length}`);
}

test('response labels omit unverified audio claims while preserving explicit metadata and STT hints', async () => {
  const ids = ['unverified-text', 'rejected-unverified', 'known-text', 'known-audio'];
  const capabilities = {
    'unverified-text': { supports_audio_input: false, source: 'inferred' },
    'rejected-unverified': { supports_audio_input: false, source: 'inferred' },
    'known-text': { supports_audio_input: false, source: 'endpoint_loaded' },
    'known-audio': { supports_audio_input: true, source: 'endpoint_loaded' },
  };
  const statuses = {
    'rejected-unverified': { usable: false, summary: 'Not available', reasons: ['Not available'] },
    'known-audio': { usable: false, summary: 'Insufficient memory', reasons: ['Insufficient memory'] },
  };
  state.capabilityFilterOverride = true;
  const loading = loadModelOptions(true); await waitRequests(1);
  pending[0].resolve(response({ whisper_models: ['base.en'], llm_models: ids, stt_llm_models: ids,
    llm_models_capabilities: capabilities, stt_llm_models_capabilities: capabilities, llm_models_status: statuses }));
  await loading;
  const option = (select, id) => element(select).options.find(item => item.value === id);
  for (const id of ['unverified-text', 'rejected-unverified']) {
    assert.doesNotMatch(option('lmModel', id).textContent, /no audio/);
    assert.match(option('sttLlmModel', id).textContent, /no audio/);
    assert.equal(option('lmModel', id).dataset.audioCapable, 'false');
  }
  assert.match(option('lmModel', 'rejected-unverified').textContent, /Not available/);
  assert.equal(option('lmModel', 'rejected-unverified').dataset.unusable, 'true');
  assert.match(option('lmModel', 'known-text').textContent, /no audio/);
  assert.doesNotMatch(option('lmModel', 'known-audio').textContent, /no audio/);
  assert.match(option('lmModel', 'known-audio').textContent, /Insufficient memory/);
  assert.deepEqual(state.lastModelsPayload.llm_models_capabilities, capabilities);
  assert.deepEqual(state.lastModelsPayload.stt_llm_models_capabilities, capabilities);
});

test('late response/STT discovery cannot replace a newer picker or cached query', async () => {
  const old = loadModelOptions(true); await waitRequests(1);
  endpoint('new');
  const current = loadModelOptions(true); await waitRequests(2);
  pending[1].resolve(response(payload('new'))); await current;
  const currentQuery = state.lastModelsQuery;
  pending[0].resolve(response(payload('old'))); await old;
  assert.equal(element('lmModel').value, 'new-response');
  assert.deepEqual(element('sttLlmModel').options.map(option => option.value), ['', 'new-audio']);
  assert.equal(state.lastModelsQuery, currentQuery);
  assert.deepEqual(state.lastModelsPayload, payload('new'));
});

test('HTTP discovery failures are reported and remain retryable without force', async () => {
  const failed = loadModelOptions(); await waitRequests(1);
  pending[0].resolve(response({ detail: 'Provider unavailable' }, 503)); await failed;
  assert.equal(state.lastModelsPayload, null);
  assert.match(element('modelOptionsStatus').textContent, /failed.*503/i);
  const retry = loadModelOptions(); await waitRequests(2);
  pending[1].resolve(response(payload('recovered'))); await retry;
  assert.equal(element('lmModel').value, 'recovered-response');
});

test('candidate model discovery explicitly requests preflighted credential intent', async () => {
  element('llmApiKeyEnv').value = 'RESPONSE_ENV';
  element('sttLlmApiKeyEnv').value = 'AUDIO_ENV';
  const loading = loadModelOptions(true); await waitRequests(1);
  const query = new URL(pending[0].url, 'http://korina.test').searchParams;
  assert.equal(query.get('llm_api_key_env'), 'RESPONSE_ENV');
  assert.equal(query.get('stt_llm_api_key_env'), 'AUDIO_ENV');
  assert.equal(pending[0].options.headers['X-Korina-Intent'], 'model-discovery');
  pending[0].resolve(response(payload('credentialed'))); await loading;
  assert.deepEqual(state.lastModelsPayload, payload('credentialed'));
});

test('late agent discovery cannot replace newer options or restore an old selected model', async () => {
  element('agentModel').value = 'old-choice';
  const old = loadAgentModelOptions(); await waitRequests(1);
  element('agentBaseUrl').value = 'http://new-agent.example/v1';
  element('agentModel').value = 'new-choice';
  const current = loadAgentModelOptions(); await waitRequests(2);
  pending[1].resolve(response({ ok: true, models: ['new-choice'], provider: 'new-agent' })); await current;
  pending[0].resolve(response({ ok: true, models: ['old-choice'], provider: 'old-agent' })); await old;
  assert.equal(element('agentModel').value, 'new-choice');
  assert.deepEqual(element('agentModel').options.map(option => option.value), ['', 'new-choice']);
  assert.match(element('modelOptionsStatus').textContent, /new-agent/);
});

test('agent discovery posts the captured unsaved form without putting its key in a URL or saving settings', async () => {
  element('agentProvider').value = 'anthropic';
  element('agentBaseUrl').value = 'https://candidate.example/v1/messages';
  element('agentApiKey').value = 'candidate-secret';
  element('llmBaseUrl').value = 'https://response.example/v1';
  element('llmApiKeyEnv').value = 'RESPONSE_ENV';
  const loading = loadAgentModelOptions(); await waitRequests(1);
  assert.equal(pending[0].url, '/api/agent/models');
  assert.equal(pending[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(pending[0].options.body), {
    agent_provider: 'anthropic', agent_base_url: 'https://candidate.example/v1/messages',
    agent_api_key: 'candidate-secret', llm_base_url: 'https://response.example/v1',
    llm_api_key_env: 'RESPONSE_ENV',
  });
  element('agentApiKey').value = 'new-secret';
  pending[0].resolve(response({ ok: true, models: ['old-secret-model'], provider: 'anthropic' }));
  await loading;
  assert.equal(pending.length, 1, 'discovery performs no settings save or provider activation');
  assert.deepEqual(element('agentModel').options, [], 'changing a field invalidates the captured response');
});

test('changing endpoint without starting another discovery still invalidates its pending result', async () => {
  element('lmModel').value = 'saved';
  const old = loadModelOptions(); await waitRequests(1);
  endpoint('unsent');
  element('modelOptionsStatus').textContent = 'New settings selected';
  pending[0].resolve(response(payload('old'))); await old;
  assert.equal(element('lmModel').value, 'saved');
  assert.equal(state.lastModelsPayload, null);
  assert.equal(element('modelOptionsStatus').textContent, 'New settings selected');
});

test('an old failed discovery cannot clear a later successful cache or status', async () => {
  const old = loadModelOptions(true); await waitRequests(1);
  const current = loadModelOptions(true); await waitRequests(2);
  pending[1].resolve(response(payload('current'))); await current;
  const currentStatus = element('modelOptionsStatus').textContent;
  pending[0].resolve(response({ detail: 'old failure' }, 503)); await old;
  assert.deepEqual(state.lastModelsPayload, payload('current'));
  assert.equal(element('modelOptionsStatus').textContent, currentStatus);
});

test('partial provider errors render available choices but do not poison the success cache', async () => {
  const initial = loadModelOptions(); await waitRequests(1);
  pending[0].resolve(response(payload('initial'))); await initial;
  const failed = loadModelOptions(true); await waitRequests(2);
  pending[1].resolve(response({ ...payload('local'), llm_error: 'upstream timeout' })); await failed;
  assert.equal(state.lastModelsPayload, null);
  assert.match(element('modelOptionsStatus').textContent, /upstream timeout/);
  const retry = loadModelOptions(); await waitRequests(3);
  pending[2].resolve(response(payload('recovered'))); await retry;
  assert.equal(element('lmModel').value, 'initial-response', 'discovery retains an existing selection');
  assert.deepEqual(state.lastModelsPayload, payload('recovered'));
});

test('current agent discovery preserves a model selected while the request was pending', async () => {
  element('agentModel').value = 'before';
  const loading = loadAgentModelOptions(); await waitRequests(1);
  element('agentModel').value = 'user-selection';
  pending[0].resolve(response({ ok: true, models: ['before', 'user-selection'], provider: 'demo' })); await loading;
  assert.equal(element('agentModel').value, 'user-selection');
});

for (const [name, result] of [
  ['HTTP failure', response({ detail: 'unavailable' }, 503)],
  ['provider failure', response({ ok: false, models: [], error: 'provider unavailable' })],
]) {
  test(`agent ${name} preserves existing options and reports the failure`, async () => {
    const choice = document.createElement('option'); choice.value = 'saved';
    element('agentModel').appendChild(choice); element('agentModel').value = 'saved';
    const loading = loadAgentModelOptions(); await waitRequests(1);
    pending[0].resolve(result); await loading;
    assert.deepEqual(element('agentModel').options.map(option => option.value), ['saved']);
    assert.equal(element('agentModel').value, 'saved');
    assert.match(element('modelOptionsStatus').textContent, /failed/i);
  });
}

test('late root discovery cannot replace a more recent list', async () => {
  element('localModelRootsList');
  const old = loadLocalModelRoots(); await waitRequests(1);
  const current = loadLocalModelRoots(); await waitRequests(2);
  const roots = path => [{ path, exists: true, is_directory: true }];
  pending[1].resolve(response({ roots: roots('/new') })); await current;
  pending[0].resolve(response({ roots: roots('/old') })); await old;
  assert.equal(element('localModelRootsList').children[0].children[1].textContent, '/new');
});

async function localRefresh() {
  for (const id of ['refreshLocalModelsBtn', 'localModelsStatus', 'localModelRootsList']) element(id);
  wireLocalModelsUi(); await waitRequests(1);
  pending[0].resolve(response({ roots: [] }));
  await new Promise(resolve => setImmediate(resolve));
  return element('refreshLocalModelsBtn').listeners.click();
}

test('a local refresh completed after changing providers cannot start stale discovery or probes', async () => {
  element('llmProvider').value = element('sttLlmProvider').value = 'llama.cpp';
  const refreshing = localRefresh(); await waitRequests(2);
  endpoint('changed'); element('sttLlmProvider').value = 'ollama';
  element('localModelsStatus').textContent = 'Current settings';
  pending[1].resolve(response({ discovered_count: 1, restarted: false })); await refreshing;
  assert.equal(pending.length, 2);
  assert.equal(element('localModelsStatus').textContent, 'Current settings');
});

test('a probe uses accepted discovery and cannot refresh or overwrite status after endpoint changes', async () => {
  element('llmProvider').value = element('sttLlmProvider').value = 'llama.cpp';
  const refreshing = localRefresh(); await waitRequests(2);
  pending[1].resolve(response({ discovered_count: 1, restarted: false })); await waitRequests(3);
  pending[2].resolve(response({ roots: [] })); await waitRequests(4);
  pending[3].resolve(response(payload('local'))); await waitRequests(5);
  assert.equal(pending[4].url, '/api/llm/probe-models');
  assert.deepEqual(JSON.parse(pending[4].options.body), {
    provider: 'llama.cpp', base_url: 'http://old-audio.example/v1', api_key_env: '',
    models: ['local-audio'],
  });
  endpoint('changed'); element('localModelsStatus').textContent = 'Current endpoint';
  pending[4].resolve(response({ results: { 'local-audio': { supported: true } } })); await refreshing;
  assert.equal(pending.length, 5);
  assert.equal(element('localModelsStatus').textContent, 'Current endpoint');
});

test('Add Directory requests an atomic append instead of replacing all configured roots', async () => {
  for (const id of ['addLocalModelRootBtn', 'newLocalModelRoot', 'localModelsStatus', 'localModelRootsList']) element(id);
  wireLocalModelsUi(); await waitRequests(1);
  pending[0].resolve(response({ roots: [{ path: '/first', exists: true, is_directory: true }] }));
  await new Promise(resolve => setImmediate(resolve));
  element('newLocalModelRoot').value = '/second';
  const adding = element('addLocalModelRootBtn').listeners.click(); await waitRequests(2);
  assert.equal(pending[1].url, '/api/models/roots');
  assert.deepEqual(JSON.parse(pending[1].options.body), { roots: ['/second'], append: true });
  pending[1].resolve(response({ ok: true })); await waitRequests(3);
  pending[2].resolve(response({ roots: [{ path: '/first' }, { path: '/second' }] })); await waitRequests(4);
  pending[3].resolve(response(payload('current'))); await adding;
  assert.equal(element('localModelRootsList').children.length, 2);
  assert.match(element('localModelsStatus').textContent, /Added \/second/);
});

test('forty thousand old-server models cannot expand options/cache or offer invalid IDs', async () => {
  const ids = Array.from({ length: 40000 }, (_, index) => `model-${index}`);
  element('lmModel').value = ids.at(-1);
  state.capabilityFilterOverride = true;
  const loading = loadModelOptions(true); await waitRequests(1);
  pending[0].resolve(response({ llm_models: [...ids, 'x'.repeat(513), '\ud800'], stt_llm_models: ids,
    llm_default: ids.at(-1), unused: 'discard me', labels: { unrelated: 'unrelated' } }));
  await loading;
  assert.equal(element('lmModel').options.length, 512);
  assert.equal(element('sttLlmModel').options.length, 513);
  assert.equal(element('lmModel').value, ids.at(-1));
  assert.equal(element('sttLlmModel').value, '', 'explicit STT inheritance remains selected');
  assert.equal(state.lastModelsPayload.llm_models.includes(ids.at(-1)), true);
  assert.equal(state.lastModelsPayload.llm_models.some(id => id.length > 512), false);
  assert.equal('unused' in state.lastModelsPayload, false);
  assert.ok(JSON.stringify(state.lastModelsPayload).length < 100000);
  assert.match(element('modelOptionsStatus').textContent, /limited.*Invalid model IDs/i);
  assert.equal(pending.length, 1, 'discovery never saves rejected or replacement model IDs');
});

test('missing response/STT selections survive local fallback as annotated unlisted options', async () => {
  element('lmModel').value = 'saved-response'; element('sttLlmModel').value = 'saved-audio';
  const loading = loadModelOptions(true); await waitRequests(1);
  pending[0].resolve(response({ llm_models: [], stt_llm_models: [], llama_cpp_local_models: ['other.gguf'],
    stt_llm_models_capabilities: { 'other.gguf': { supports_audio_input: true } } }));
  await loading;
  assert.equal(element('lmModel').value, 'saved-response');
  assert.equal(element('sttLlmModel').value, 'saved-audio');
  assert.equal(element('lmModel').options.at(-1).dataset.unlisted, 'true');
  assert.equal(element('sttLlmModel').options.at(-1).dataset.unlisted, 'true');
  assert.match(element('modelOptionsStatus').textContent, /Saved selections.*retained/);
});

for (const mode of ['declared-oversize', 'stream-oversize', 'malformed-json', 'invalid-utf8']) {
  test(`${mode} model discovery preserves options/cache and remains retryable`, async () => {
    const first = loadModelOptions(true); await waitRequests(1);
    pending[0].resolve(response(payload('saved'))); await first;
    const cached = state.lastModelsPayload;
    const before = element('lmModel').options.map(option => option.value);
    let cancelled = false;
    const source = mode === 'stream-oversize' ? new Uint8Array(MAX_MODEL_RESPONSE_BYTES + 1)
      : mode === 'invalid-utf8' ? new Uint8Array([0xff]) : new TextEncoder().encode('{broken');
    const stream = new ReadableStream({ start(controller) { controller.enqueue(source); }, cancel() { cancelled = true; } });
    const broken = mode.startsWith('malformed') || mode === 'invalid-utf8' ? new Response(source)
      : new Response(stream, { headers: mode === 'declared-oversize' ? { 'Content-Length': MAX_MODEL_RESPONSE_BYTES + 1 } : {} });
    const failed = loadModelOptions(true); await waitRequests(2);
    pending[1].resolve(broken); await failed;
    assert.equal(state.lastModelsPayload, cached);
    assert.deepEqual(element('lmModel').options.map(option => option.value), before);
    assert.match(element('modelOptionsStatus').textContent, /load failed/i);
    if (mode.includes('oversize')) assert.equal(cancelled, true);
    const retry = loadModelOptions(); await waitRequests(3);
    pending[2].resolve(response(payload('retried'))); await retry;
    assert.deepEqual(state.lastModelsPayload, payload('retried'));
    assert.equal(element('lmModel').value, 'saved-response');
  });
}

test('agent old-server discovery has the same ID/option bounds and preserves its saved selection', async () => {
  element('agentModel').value = 'not-listed';
  const loading = loadAgentModelOptions(); await waitRequests(1);
  pending[0].resolve(response({ ok: true, models: ['x'.repeat(513), '\ud800', ...Array.from({length: 40000}, (_, index) => `agent-${index}`)] }));
  await loading;
  assert.equal(element('agentModel').value, 'not-listed');
  assert.equal(element('agentModel').options.length, 514, '512 valid choices, inherit, saved');
  assert.equal(element('agentModel').options.at(-1).dataset.unlisted, 'true');
  assert.match(element('modelOptionsStatus').textContent, /limited.*Invalid model IDs/i);
  assert.equal(pending.length, 1);
});

for (const [name, metadata] of [
  ['capability map string', { llm_models_capabilities: 'bad' }],
  ['capability map array', { llm_models_capabilities: [] }],
  ['capability map null', { llm_models_capabilities: null }],
  ...['bad', [], true, null].map(value => ['capability value ' + JSON.stringify(value), { llm_models_capabilities: { other: value } }]),
  ...['bad', [], true, null].map(value => ['status value ' + JSON.stringify(value), { llm_models_status: { other: value } }]),
  ['status usable string', { llm_models_status: { other: { usable: 'true' } } }],
  ['status non-string reasons', { llm_models_status: { other: { reasons: [3] } } }],
  ['invalid label value', { labels: { other: [] } }],
]) {
  test(`${name} is rejected before replacing the last good picker/cache and stays retryable`, async () => {
    const good = loadModelOptions(true); await waitRequests(1);
    pending[0].resolve(response(payload('saved'))); await good;
    const cached = state.lastModelsPayload;
    const options = element('lmModel').options.map(option => option.value);
    const failed = loadModelOptions(true); await waitRequests(2);
    pending[1].resolve(response({ llm_models: ['other'], stt_llm_models: [], ...metadata })); await failed;
    assert.equal(state.lastModelsPayload, cached);
    assert.deepEqual(element('lmModel').options.map(option => option.value), options);
    assert.equal(element('lmModel').value, 'saved-response');
    assert.match(element('modelOptionsStatus').textContent, /load failed.*Invalid model/i);
    const retry = loadModelOptions(); await waitRequests(3);
    pending[2].resolve(response(payload('retried'))); await retry;
    assert.deepEqual(state.lastModelsPayload, payload('retried'));
    assert.equal(element('lmModel').value, 'saved-response');
  });
}

test('an incomplete metadata budget is visible without claiming additional model IDs were omitted', async () => {
  const loading = loadModelOptions(true); await waitRequests(1);
  pending[0].resolve(response({ ...payload('estimated'), catalog_info: { metadata: {
    max_entries: 10000, visited_entries: 10000, directories_scanned: 1, truncated: true, incomplete: true,
  } } })); await loading;
  assert.match(element('modelOptionsStatus').textContent, /mmproj capability estimates are incomplete/);
  assert.doesNotMatch(element('modelOptionsStatus').textContent, /additional models omitted/);
  assert.equal(state.lastModelsPayload.catalog_info.metadata.incomplete, true);
});

test('a retained saved STT choice keeps its known no-audio and red unusable warnings', async () => {
  element('sttLlmModel').value = 'saved-audio';
  const loading = loadModelOptions(true); await waitRequests(1);
  pending[0].resolve(response({ llm_models: ['response'], stt_llm_models: ['saved-audio'],
    stt_llm_models_capabilities: { 'saved-audio': { supports_audio_input: false } },
    stt_llm_models_status: { 'saved-audio': { usable: false, summary: 'no audio input support', reasons: ['no audio input support'] } },
  })); await loading;
  const saved = element('sttLlmModel').options.at(-1);
  assert.equal(element('sttLlmModel').value, 'saved-audio');
  assert.equal(saved.dataset.unlisted, 'true');
  assert.equal(saved.dataset.audioCapable, 'false');
  assert.equal(saved.dataset.unusable, 'true');
  assert.match(saved.textContent, /no audio.*red: no audio input support.*saved; not listed/);
  assert.match(saved.title, /no audio input support.*retained/);
});

test('large local refresh keeps the selected STT model inside the bounded audio probe batch', async () => {
  element('llmProvider').value = element('sttLlmProvider').value = 'llama.cpp';
  const ids = Array.from({ length: 65 }, (_, index) => `local-${index}.gguf`);
  element('sttLlmModel').value = ids.at(-1);
  const refreshing = localRefresh(); await waitRequests(2);
  pending[1].resolve(response({ discovered_count: ids.length, restarted: false })); await waitRequests(3);
  pending[2].resolve(response({ roots: [] })); await waitRequests(4);
  const catalog = { ...payload('local'), stt_llm_models: ids, llama_cpp_local_models: ids };
  pending[3].resolve(response(catalog)); await waitRequests(5);
  const probe = JSON.parse(pending[4].options.body);
  assert.equal(probe.models.length, 64, 'the API accepts at most 64 models');
  assert.equal(probe.models[0], ids.at(-1), 'selected STT model is checked first');
  pending[4].resolve(response({ results: {}, skipped_models: probe.models.slice(4), timed_out: true })); await waitRequests(6);
  pending[5].resolve(response(catalog)); await refreshing;
  assert.match(element('localModelsStatus').textContent, /1.*catalog.*omitted/i);
  assert.match(element('localModelsStatus').textContent, /60.*not probed.*time/i);
});
