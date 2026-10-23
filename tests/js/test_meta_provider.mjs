import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { maybeApplyProviderPreset, modelDiscoveryQuery, setBaseUrlEditability } from '../../Korina/js/providers-ui.js';
import { syncReasoningHints } from '../../Korina/js/settings-ui.js';

const elements = new Map();
const control = id => elements.get(id);
beforeEach(() => {
  elements.clear();
  for (const id of ['llmProvider', 'llmBaseUrl', 'llmApiKeyEnv', 'llmReasoningEnabled',
    'sttLlmProvider', 'sttLlmBaseUrl', 'agentProvider', 'agentBaseUrl', 'agentApiKey', 'llmReasoningHint',
    'llmReasoningEffort', 'agentReasoningEffort', 'agentReasoningHint',
    'llmReasoningEffortWrap', 'agentReasoningEffortWrap', 'llmReasoningEnabledWrap']) {
    const classes = new Set();
    elements.set(id, { value: '', disabled: false, placeholder: '', title: '',
      classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) } });
  }
  globalThis.document = { getElementById: control };
  state._capabilitiesCache = {
    providers: {
      meta: { default_base_url: 'https://api.meta.ai/v1', default_api_key_env: 'MODEL_API_KEY', editable_base_url: false },
      'openai-compatible': { editable_base_url: true },
    },
    agentProviders: {
      meta: { default_base_url: 'https://api.meta.ai/v1', default_api_key_env: 'MODEL_API_KEY', editable_base_url: false },
      'openai-compatible': { editable_base_url: true },
    },
  };
  globalThis.fetch = async () => { throw new Error('Cached capability presets must not request a provider'); };
});

test('Meta response preset fixes the base and hints its key without overwriting explicit configuration', async () => {
  control('llmProvider').value = 'meta';
  control('llmApiKeyEnv').value = 'EXPLICIT_KEY_ENV';
  assert.equal(maybeApplyProviderPreset('llmProvider', 'llmBaseUrl'), true);
  await setBaseUrlEditability();
  assert.equal(control('llmBaseUrl').value, 'https://api.meta.ai/v1');
  assert.equal(control('llmBaseUrl').disabled, true);
  assert.equal(control('llmApiKeyEnv').value, 'EXPLICIT_KEY_ENV');
  assert.equal(control('llmApiKeyEnv').placeholder, 'MODEL_API_KEY');
  assert.equal(control('llmReasoningEnabled').disabled, true);
  assert.match(control('llmReasoningEnabled').title, /separate reasoning effort/);
  syncReasoningHints();
  assert.match(control('llmReasoningHint').textContent, /Meta provider default/);
});

test('Meta effort controls stay independent and hidden for other provider profiles', () => {
  control('llmProvider').value = 'meta';
  control('agentProvider').value = 'meta';
  control('llmReasoningEffort').value = 'minimal';
  control('agentReasoningEffort').value = 'high';
  syncReasoningHints();
  assert.equal(control('llmReasoningEffortWrap').classList.contains('settingsHidden'), false);
  assert.equal(control('agentReasoningEffortWrap').classList.contains('settingsHidden'), false);
  assert.equal(control('llmReasoningEnabledWrap').classList.contains('settingsHidden'), true);
  assert.match(control('llmReasoningHint').textContent, /Meta effort minimal/);
  assert.match(control('agentReasoningHint').textContent, /Meta effort high.*after settings save/);
  control('llmProvider').value = 'openai-compatible';
  syncReasoningHints();
  assert.equal(control('llmReasoningEffortWrap').classList.contains('settingsHidden'), true);
  assert.equal(control('llmReasoningEnabledWrap').classList.contains('settingsHidden'), false);
  assert.equal(control('agentReasoningEffortWrap').classList.contains('settingsHidden'), false);
  assert.equal(control('llmReasoningEffort').value, 'minimal', 'profile changes do not erase saved preferences');
  control('agentProvider').value = 'anthropic';
  syncReasoningHints();
  assert.equal(control('agentReasoningEffortWrap').classList.contains('settingsHidden'), true);
});

test('Meta agent preset uses the same registry and preserves an explicit role key', async () => {
  control('agentProvider').value = 'meta';
  control('agentApiKey').value = 'explicit-fixture-key';
  assert.equal(maybeApplyProviderPreset('agentProvider', 'agentBaseUrl'), true);
  await setBaseUrlEditability();
  assert.equal(control('agentBaseUrl').value, 'https://api.meta.ai/v1');
  assert.equal(control('agentBaseUrl').disabled, true);
  assert.equal(control('agentApiKey').value, 'explicit-fixture-key');
  assert.match(control('agentApiKey').placeholder, /MODEL_API_KEY/);
  assert.match(control('agentApiKey').placeholder, /same-URL response key, otherwise/);
});

test('generic provider selection restores endpoint editing and legacy reasoning control', async () => {
  control('llmProvider').value = 'meta';
  await setBaseUrlEditability();
  control('llmProvider').value = 'openai-compatible';
  control('agentProvider').value = 'openai-compatible';
  control('llmBaseUrl').value = 'https://custom.test/v1';
  assert.equal(maybeApplyProviderPreset('llmProvider', 'llmBaseUrl'), false);
  await setBaseUrlEditability();
  assert.equal(control('llmBaseUrl').value, 'https://custom.test/v1');
  assert.equal(control('llmBaseUrl').disabled, false);
  assert.equal(control('agentBaseUrl').disabled, false);
  assert.equal(control('llmReasoningEnabled').disabled, false);
  assert.equal(control('llmReasoningEnabled').title, '');
  syncReasoningHints();
  assert.equal(control('llmReasoningHint').textContent, 'Reasoning: off.');
  assert.doesNotMatch(control('agentApiKey').placeholder, /MODEL_API_KEY/);
});

test('actual Meta discovery query resolves the blank profile key hint for response and inherited STT', () => {
  control('llmProvider').value = 'meta';
  control('llmBaseUrl').value = 'https://api.meta.ai/v1';
  const query = new URLSearchParams(modelDiscoveryQuery());
  assert.equal(control('llmApiKeyEnv').value, '');
  assert.equal(query.get('llm_api_key_env'), 'MODEL_API_KEY');
  assert.equal(query.get('stt_llm_api_key_env'), 'MODEL_API_KEY');
});

test('custom discovery URLs and generic profiles never acquire a Meta env default', () => {
  control('llmProvider').value = 'meta';
  control('llmBaseUrl').value = 'https://candidate.test/v1';
  let query = new URLSearchParams(modelDiscoveryQuery());
  assert.equal(query.get('llm_api_key_env'), '');
  assert.equal(query.get('stt_llm_api_key_env'), '');
  control('llmProvider').value = 'openai-compatible';
  control('llmBaseUrl').value = 'https://api.meta.ai/v1';
  query = new URLSearchParams(modelDiscoveryQuery());
  assert.equal(query.get('llm_api_key_env'), '');
  assert.equal(query.get('stt_llm_api_key_env'), '');
});
