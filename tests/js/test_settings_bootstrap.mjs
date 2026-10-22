import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { initializeSettingsForm, loadConfig, saveConfigNow } from '../../Korina/js/api.js';
import { applyConfig } from '../../Korina/js/settings-ui.js';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '', options: [],
    querySelector() { return null; }, appendChild() {}, classList: { toggle() {} } });
  return elements.get(id);
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: element, querySelectorAll: () => [], createElement: () => ({}) };
  globalThis.CSS = { escape: value => value };
  globalThis.location = { protocol: 'http:', hostname: 'localhost', host: 'localhost' };
  Object.assign(state, { settingsBootstrap: null, appConfig: {}, appConfigSnapshot: {}, agentControlPending: false, _capabilitiesCache: null });
  element('speed').value = '1';
  initializeSettingsForm();
});

test('closing an untouched form before any config read sends no HTML defaults', async () => {
  globalThis.fetch = async () => { throw new Error('Untouched defaults must not be posted'); };
  await saveConfigNow();
  assert.deepEqual(state.appConfig, {});
});

test('a pre-load save posts only the edited value and retains other saved settings', async () => {
  const server = { speed: 1.3, voice: 'af_bella', llm_provider: 'openai-compatible' }, posts = [];
  element('speed').value = '1.8';
  globalThis.fetch = async (_url, options) => {
    const patch = JSON.parse(options.body); posts.push(patch); Object.assign(server, patch); return response({ ...server });
  };
  await saveConfigNow();
  assert.deepEqual(posts, [{ speed: 1.8 }]);
  assert.equal(server.voice, 'af_bella');
  assert.equal(server.llm_provider, 'openai-compatible');
});

test('a delayed read fills untouched fields and preserves an unsaved form edit', async () => {
  const read = deferred(), posts = [];
  globalThis.fetch = async (_url, options) => {
    if (!options) return read.promise;
    posts.push(JSON.parse(options.body)); return response({ speed: 1.8, voice: 'af_bella' });
  };
  const loading = loadConfig();
  element('speed').value = '1.8';
  read.resolve(response({ speed: 1.3, voice: 'af_bella' }));
  assert.equal(await loading, true);
  assert.equal(String(element('speed').value), '1.8');
  assert.equal(element('voice').value, 'af_bella');
  await saveConfigNow();
  assert.deepEqual(posts, [{ speed: 1.8 }]);
});

test('a newer confirmed save wins over an older read while activation keeps its original baseline', async () => {
  const read = deferred(), posts = [];
  globalThis.fetch = async (_url, options) => {
    if (!options) return read.promise;
    posts.push(JSON.parse(options.body));
    return response({ llm_provider: 'ollama', llm_base_url: 'http://localhost:11434/v1', voice: 'af_bella' });
  };
  const loading = loadConfig();
  element('llmProvider').value = 'ollama'; element('llmBaseUrl').value = 'http://localhost:11434/v1';
  await saveConfigNow();
  read.resolve(response({ llm_provider: 'openai-compatible', llm_base_url: 'http://saved/v1', voice: 'af_bella' }));
  await loading;
  assert.equal(element('llmProvider').value, 'ollama');
  assert.equal(state.appConfig.llm_provider, 'ollama');
  assert.equal(state.appConfigSnapshot.llm_provider, 'openai-compatible');
  assert.deepEqual(posts, [{ llm_provider: 'ollama', llm_base_url: 'http://localhost:11434/v1' }]);
});

test('failed initial read retains edits for a later retry without posting untouched values', async () => {
  let fail = true;
  const posts = [];
  globalThis.fetch = async (_url, options) => {
    if (!options) return fail ? response({ detail: 'Unavailable' }, 503) : response({ speed: 1.8, voice: 'af_bella' });
    posts.push(JSON.parse(options.body)); return response({ speed: 1.8, voice: 'af_bella' });
  };
  const warn = console.warn; console.warn = () => {};
  try { assert.equal(await loadConfig(), false); } finally { console.warn = warn; }
  assert.match(element('settingsInfo').textContent, /Open Settings to retry/);
  element('speed').value = '1.8';
  await saveConfigNow();
  fail = false;
  assert.equal(await loadConfig(), true);
  assert.equal(String(element('speed').value), '1.8');
  assert.equal(element('voice').value, 'af_bella');
  assert.deepEqual(posts, [{ speed: 1.8 }]);
});

test('a successful in-flight save cannot clear a newer edit of the same field', async () => {
  const saved = deferred(), posts = [];
  globalThis.fetch = async (_url, options) => {
    const patch = JSON.parse(options.body); posts.push(patch);
    return posts.length === 1 ? saved.promise : response(patch);
  };
  element('speed').value = '1.4';
  const first = saveConfigNow();
  element('speed').value = '1.7';
  saved.resolve(response({ speed: 1.4 }));
  await first;
  assert.equal(state.settingsBootstrap.dirty.speed, 1.7);
  await saveConfigNow();
  assert.deepEqual(posts, [{ speed: 1.4 }, { speed: 1.7 }]);
});

test('partial config preserves omitted reasoning, model selections, and activation snapshot keys', () => {
  applyConfig({ llm_provider: 'lmstudio', lm_model: 'response-model', stt_llm_model: 'audio-model',
    llm_reasoning: 'on', stt_llm_reasoning: 'on' });
  applyConfig({ speed: 1.5 });
  assert.equal(element('llmReasoningEnabled').checked, true);
  assert.equal(element('sttLlmReasoningEnabled').checked, true);
  assert.equal(element('sttLlmModel').value, 'audio-model');
  assert.equal(element('lmModel').value, 'response-model');
  assert.equal(state.appConfigSnapshot.llm_provider, 'lmstudio');
  assert.equal(state.appConfigSnapshot.lm_model, 'response-model');
  applyConfig({ lm_model: '', stt_llm_model: '', llm_reasoning: 'off', stt_llm_reasoning: 'off' });
  assert.equal(element('lmModel').value, '');
  assert.equal(element('sttLlmModel').value, '');
  assert.equal(element('llmReasoningEnabled').checked, false);
  assert.equal(element('sttLlmReasoningEnabled').checked, false);
});
