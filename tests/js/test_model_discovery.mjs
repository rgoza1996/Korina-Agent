import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { loadAgentModelOptions, loadModelOptions } from '../../Korina/js/providers-ui.js';
import { loadLocalModelRoots, wireLocalModelsUi } from '../../Korina/js/local-models.js';

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
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
function payload(tag) {
  return { whisper_models: ['base.en'], llm_models: [`${tag}-response`],
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
  Object.assign(state, { lastModelsPayload: null, lastModelsQuery: '', lastModelsLoadedAt: 0,
    audioUnsupported: {}, capabilityFilterOverride: false });
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
  assert.equal(element('lmModel').value, 'recovered-response');
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
