import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { initializeSettingsForm, loadConfig, saveConfigNow } from '../../Korina/js/api.js';
import { applyConfig, collectConfig, effectiveSttLlmModel, llmMaxTokens } from '../../Korina/js/settings-ui.js';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', className: '', options: [], style: {}, dataset: {},
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

test('zero interrupt padding survives a settings save and its confirmed response', async () => {
  const posts = [];
  globalThis.fetch = async (_url, options) => {
    const patch = JSON.parse(options.body); posts.push(patch);
    return response(patch);
  };
  element('agentInterruptCooldownPaddingMs').value = '0';
  await saveConfigNow();
  assert.deepEqual(posts, [{ agent_interrupt_cooldown_padding_ms: 0 }]);
  assert.equal(state.appConfig.agent_interrupt_cooldown_padding_ms, 0);
  assert.equal(collectConfig().agent_interrupt_cooldown_padding_ms, 0);
  await saveConfigNow();
  assert.equal(posts.length, 1, 'confirmation must not restore the old default');
});

test('unrelated settings saves preserve legacy cloud-STT keys without exposing them as editable settings', async () => {
  const legacy = {
    stt_cloud_base_url: 'http://legacy-stt.test/v1',
    stt_cloud_model: 'legacy-model', stt_api_key_env: 'LEGACY_STT_KEY',
  };
  const server = { ...legacy, speed: 1.3, voice: 'af_bella' }, posts = [];
  globalThis.fetch = async (_url, options) => {
    if (!options) return response({ ...server });
    const patch = JSON.parse(options.body); posts.push(patch);
    Object.assign(server, patch); return response({ ...server });
  };
  await loadConfig();
  element('speed').value = '1.8';
  await saveConfigNow();
  assert.deepEqual(posts, [{ speed: 1.8 }]);
  for (const [key, value] of Object.entries(legacy)) {
    assert.equal(server[key], value);
    assert.equal(state.appConfig[key], value);
    assert.equal(Object.hasOwn(collectConfig(), key), false);
  }
});

test('saved response budget loads without becoming an implicit new settings write', async () => {
  globalThis.fetch = async (_url, options) => {
    assert.equal(options, undefined);
    return response({ llm_max_tokens: 2048 });
  };
  assert.equal(llmMaxTokens(), 180);
  await loadConfig();
  assert.equal(String(element('llmMaxTokens').value), '2048');
  assert.equal(llmMaxTokens(), 2048);
  assert.equal(collectConfig().llm_max_tokens, 2048);
  await saveConfigNow();
});

test('a newer response-budget draft survives confirmation of an older save', async () => {
  const pending = deferred(), posts = [];
  globalThis.fetch = async (_url, options) => {
    posts.push(JSON.parse(options.body));
    return posts.length === 1 ? pending.promise : response({ llm_max_tokens: 1024 });
  };
  element('llmMaxTokens').value = '512';
  const saving = saveConfigNow();
  element('llmMaxTokens').value = '1024';
  pending.resolve(response({ llm_max_tokens: 512 }));
  await saving;
  assert.equal(element('llmMaxTokens').value, '1024');
  assert.equal(state.settingsBootstrap.dirty.llm_max_tokens, 1024);
  assert.equal(state.appConfig.llm_max_tokens, 512);
  await saveConfigNow();
  assert.deepEqual(posts, [{ llm_max_tokens: 512 }, { llm_max_tokens: 1024 }]);
  assert.equal(state.appConfig.llm_max_tokens, 1024);
});

test('response-budget drafts preserve invalid numeric values for strict server validation', () => {
  for (const value of [0, 1.5, 8193]) {
    element('llmMaxTokens').value = String(value);
    assert.equal(collectConfig().llm_max_tokens, value);
    assert.equal(llmMaxTokens(), value);
  }
  element('llmMaxTokens').value = '';
  assert.equal(llmMaxTokens(), 180);
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

const following = { llm_provider: 'openai-compatible', llm_base_url: 'http://response.test/v1', lm_model: 'response-a',
  stt_backend: 'llm', stt_llm_provider: 'openai-compatible', stt_llm_base_url: 'http://response.test/v1', stt_llm_model: 'response-a',
  agent_base_url: 'http://response.test/v1', agent_model: 'response-a' };

test('successful saves reconcile server-derived following models while preserving the activation baseline', async () => {
  applyConfig(following);
  state.settingsBootstrap.observed = collectConfig();
  const baseline = state.appConfigSnapshot;
  const posts = [];
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/config', 'a save must not activate providers or perform model discovery');
    posts.push(JSON.parse(options.body));
    return response({ ...following, lm_model: 'response-b', stt_llm_model: 'response-b', agent_model: 'response-b' });
  };
  element('lmModel').value = 'response-b';
  await saveConfigNow();
  assert.deepEqual(posts, [{ lm_model: 'response-b' }]);
  assert.equal(effectiveSttLlmModel(), 'response-b');
  assert.equal(element('agentModel').value, 'response-b');
  assert.equal(state.appConfig.stt_llm_model, 'response-b');
  assert.equal(state.appConfigSnapshot, baseline);
  assert.equal(state.appConfigSnapshot.lm_model, 'response-a');
  assert.deepEqual(state.settingsBootstrap.dirty, {});
  await saveConfigNow();
  assert.equal(posts.length, 1, 'clean reconciliation must not create a second save');
});

test('an explicit STT edit during save survives reconciliation while untouched following agent fields advance', async () => {
  applyConfig(following);
  state.settingsBootstrap.observed = collectConfig();
  const held = deferred(), posts = [];
  globalThis.fetch = async (_url, options) => {
    const patch = JSON.parse(options.body); posts.push(patch);
    return posts.length === 1 ? held.promise : response({ ...following,
      lm_model: 'response-b', stt_llm_model: 'independent-audio', agent_model: 'response-b' });
  };
  element('lmModel').value = 'response-b';
  const saving = saveConfigNow();
  element('sttLlmModel').value = 'independent-audio';
  held.resolve(response({ ...following, lm_model: 'response-b', stt_llm_model: 'response-b', agent_model: 'response-b' }));
  await saving;
  assert.equal(element('sttLlmModel').value, 'independent-audio');
  assert.equal(element('agentModel').value, 'response-b');
  assert.equal(state.settingsBootstrap.dirty.stt_llm_model, 'independent-audio');
  assert.equal(state.appConfig.stt_llm_model, 'response-b');
  await saveConfigNow();
  assert.deepEqual(posts, [{ lm_model: 'response-b' }, { stt_llm_model: 'independent-audio' }]);
  assert.equal(state.appConfig.stt_llm_model, 'independent-audio');
});

test('confirmed independent STT and agent settings remain independent after a response-model save', async () => {
  const independent = { ...following, stt_llm_base_url: 'http://audio.test/v1', stt_llm_model: 'audio-only',
    agent_base_url: 'http://agent.test/v1', agent_model: 'agent-only' };
  applyConfig(independent);
  state.settingsBootstrap.observed = collectConfig();
  globalThis.fetch = async () => response({ ...independent, lm_model: 'response-b' });
  element('lmModel').value = 'response-b';
  await saveConfigNow();
  assert.equal(element('sttLlmBaseUrl').value, independent.stt_llm_base_url);
  assert.equal(effectiveSttLlmModel(), 'audio-only');
  assert.equal(element('agentBaseUrl').value, independent.agent_base_url);
  assert.equal(element('agentModel').value, 'agent-only');
});

test('a late initial GET cannot revert derived values confirmed by a newer save', async () => {
  applyConfig(following); state.settingsBootstrap.observed = collectConfig();
  const held = deferred();
  globalThis.fetch = async (_url, options) => options?.method === 'POST'
    ? response({ ...following, lm_model: 'response-b', stt_llm_model: 'response-b', agent_model: 'response-b' }) : held.promise;
  const reading = loadConfig();
  element('lmModel').value = 'response-b'; await saveConfigNow();
  held.resolve(response(following)); await reading;
  assert.equal(effectiveSttLlmModel(), 'response-b');
  assert.equal(element('agentModel').value, 'response-b');
  assert.equal(state.appConfig.stt_llm_model, 'response-b');
  assert.equal(state.appConfigSnapshot.lm_model, 'response-a');
});

test('a partial save response leaves omitted fields and explicit blank STT model unchanged', async () => {
  applyConfig({ ...following, stt_llm_base_url: 'http://independent-audio.test/v1', stt_llm_model: '' });
  state.settingsBootstrap.observed = collectConfig();
  element('modelOptionsStatus').textContent = 'A previous discovery failure stays visible.';
  globalThis.fetch = async () => response({ speed: 1.5 });
  element('speed').value = '1.5'; await saveConfigNow();
  assert.equal(element('sttLlmModel').value, '');
  assert.equal(effectiveSttLlmModel(), '');
  assert.equal(element('sttLlmBaseUrl').value, 'http://independent-audio.test/v1');
  assert.equal(element('agentModel').value, 'response-a');
  assert.equal(element('modelOptionsStatus').textContent, 'A previous discovery failure stays visible.');
});

test('a failed save cannot apply normalized response fields or clear dirty edits', async () => {
  applyConfig(following); state.settingsBootstrap.observed = collectConfig();
  const baseline = state.appConfigSnapshot;
  globalThis.fetch = async () => response({ ...following, lm_model: 'response-b', stt_llm_model: 'response-b', detail: 'Disk unavailable' }, 503);
  element('lmModel').value = 'response-b';
  await assert.rejects(saveConfigNow(), /Disk unavailable/);
  assert.equal(effectiveSttLlmModel(), 'response-a');
  assert.equal(state.appConfig.lm_model, 'response-a');
  assert.equal(state.settingsBootstrap.dirty.lm_model, 'response-b');
  assert.equal(state.appConfigSnapshot, baseline);
});

test('derived provider/base changes update cached editability and probe selection without new provider requests', async () => {
  applyConfig({ ...following, stt_llm_provider: '', stt_llm_base_url: '', stt_llm_model: '' });
  state.settingsBootstrap.observed = collectConfig();
  state._capabilitiesCache = { providers: { 'openai-compatible': { editable_base_url: true }, lmstudio: { editable_base_url: false } }, agentProviders: {} };
  element('sttLlmBaseUrl').disabled = true;
  state.audioUnsupported = { 'openai-compatible::http://response.test/v1::response-b': { reason: 'cached unsupported' } };
  globalThis.fetch = async url => {
    assert.equal(url, '/api/config', 'cached metadata reconciliation must not discover or activate providers');
    return response({ ...following, lm_model: 'response-b', stt_llm_model: 'response-b', agent_model: 'response-b' });
  };
  element('lmModel').value = 'response-b'; await saveConfigNow();
  assert.equal(element('sttLlmBaseUrl').disabled, false);
  assert.equal(element('clearProbeBtn').dataset.model, 'response-b');
  assert.equal(element('clearProbeBtn').style.display, '');
  globalThis.fetch = async () => response({ ...following, llm_provider: 'lmstudio', llm_base_url: 'http://localhost:1234/v1',
    stt_llm_provider: 'lmstudio', stt_llm_base_url: 'http://localhost:1234/v1', lm_model: 'response-b', stt_llm_model: 'response-b' });
  element('llmProvider').value = 'lmstudio'; await saveConfigNow();
  assert.equal(element('sttLlmProvider').value, 'lmstudio');
  assert.equal(element('sttLlmBaseUrl').disabled, true);
  assert.equal(element('clearProbeBtn').style.display, 'none');
});
