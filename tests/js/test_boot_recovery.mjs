import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { refreshProviderCapabilities } from '../../Korina/js/api.js';
import { state } from '../../Korina/js/state.js';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { value: '', textContent: '', className: '', disabled: false });
  return elements.get(id);
}
const registry = {
  providers: {
    'openai-compatible': { editable_base_url: true, default_base_url: '' },
    'llama.cpp': { editable_base_url: false, default_base_url: 'http://127.0.0.1:8080/v1' },
  },
  agent_providers: { 'openai-compatible': { editable_base_url: true } },
};
function response(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}
beforeEach(() => {
  elements.clear();
  globalThis.document = { getElementById: element };
  state._capabilitiesCache = null;
  element('llmProvider').value = 'openai-compatible';
  element('agentProvider').value = 'openai-compatible';
});

for (const [name, failure] of [
  ['HTTP 503', async () => response({ detail: 'Unavailable' }, 503)],
  ['network failure', async () => { throw new TypeError('Failed to fetch'); }],
  ['invalid JSON', async () => new Response('not JSON')],
  ['empty registry', async () => response({})],
  ['invalid provider map', async () => response({ providers: [], agent_providers: {} })],
]) {
  test(`capability ${name} is visible, does not poison cache, and can retry`, async () => {
    let calls = 0;
    globalThis.fetch = async () => ++calls === 1 ? failure() : response(registry);
    assert.equal(await refreshProviderCapabilities(), false);
    assert.equal(state._capabilitiesCache, null);
    assert.match(element('settingsActionStatus').textContent, /Provider capabilities unavailable:.*Open Settings to retry/);
    assert.equal(await refreshProviderCapabilities(), true);
    assert.equal(calls, 2);
    assert.equal(element('settingsActionStatus').textContent, '');
    assert.equal(element('llmBaseUrl').disabled, false);
    assert.deepEqual(Object.keys(state._capabilitiesCache.providers), ['openai-compatible', 'llama.cpp']);
  });
}

test('overlapping boot and Settings retries share one request and use the current form selection', async () => {
  let finish, calls = 0;
  globalThis.fetch = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  element('llmProvider').value = 'llama.cpp';
  const boot = refreshProviderCapabilities();
  const settings = refreshProviderCapabilities();
  assert.equal(boot, settings);
  element('llmProvider').value = 'openai-compatible';
  finish(response(registry));
  assert.equal(await boot, true);
  assert.equal(calls, 1);
  assert.equal(element('llmProvider').value, 'openai-compatible');
  assert.equal(element('llmBaseUrl').disabled, false);
});

test('capability recovery does not clear a newer unrelated error or invent registry entries', async () => {
  let finish;
  globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = refreshProviderCapabilities();
  element('settingsActionStatus').textContent = 'Settings save failed: Disk full';
  element('sttStatus').textContent = 'Transcription failed';
  finish(response({ providers: { custom: { editable_base_url: true } }, agent_providers: {} }));
  assert.equal(await pending, true);
  assert.deepEqual(Object.keys(state._capabilitiesCache.providers), ['custom']);
  assert.equal(element('llmBaseUrl').disabled, true);
  assert.equal(element('agentBaseUrl').disabled, true);
  assert.equal(element('settingsActionStatus').textContent, 'Settings save failed: Disk full');
  assert.equal(element('sttStatus').textContent, 'Transcription failed');
});
