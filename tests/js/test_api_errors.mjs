import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { apiErrorMessage, MAX_API_ERROR_CHARS } from '../../Korina/js/api-errors.js';
import { activateSelectedProvider, saveConfigNow, initializeSettingsForm } from '../../Korina/js/api.js';
import { askLM, deliverTranscriptToAgent, handlePermissionAnswerIfAny } from '../../Korina/js/agent-ui.js';
import { transcribePartialBlob } from '../../Korina/js/recorder.js';
import { wireLocalModelsUi } from '../../Korina/js/local-models.js';
import { state } from '../../Korina/js/state.js';

const elements = new Map(), handlers = new Map();
function element(id) {
  if (id === 'settingsModal') return null;
  if (!elements.has(id)) elements.set(id, {
    value: '', checked: false, textContent: '', className: '', style: {}, options: [], children: [],
    appendChild(child) { this.children.push(child); }, querySelector() { return null; },
    addEventListener(event, callback) { handlers.set(`${id}:${event}`, callback); },
    classList: { add() {}, toggle() {}, contains() { return false; } },
  });
  return elements.get(id);
}

beforeEach(() => {
  elements.clear(); handlers.clear();
  globalThis.document = { getElementById: element, createElement: () => ({ classList: { add() {} } }) };
  globalThis.window = {};
  globalThis.location = { hostname: 'localhost', host: 'localhost', protocol: 'http:' };
  Object.assign(state, {
    appConfig: {}, settingsBootstrap: null, history: [], mode: 'sse', partialSeq: 0, latestPartialText: '',
    agentSession: 0, agentActivityEpoch: 0, agentActivityBlocked: false, agentResetBlocked: false,
    agentTurnCount: 1, agentLastDeliveredTurn: 0, awaitingAgentPermission: null,
    pendingAgentStateReport: '', pendingAgentReportVersion: 0,
  });
  initializeSettingsForm();
});

function response(body, status = 422) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
function validation(field, msg = 'Field required') {
  return { detail: [{ type: 'missing', loc: ['body', field], msg }] };
}

test('formatter preserves backend strings and selects the human incompatibility reason', () => {
  assert.equal(apiErrorMessage({ detail: 'Provider is busy' }), 'Provider is busy');
  assert.equal(apiErrorMessage({ detail: {
    error: 'incompatible_provider_model', provider: 'lmstudio', model: 'missing-catalog-model',
    reason: "lmstudio catalog does not contain 'missing-catalog-model'",
  } }), "lmstudio catalog does not contain 'missing-catalog-model'");
  assert.equal(apiErrorMessage({ ok: false, error: 'Agent is unavailable' }), 'Agent is unavailable');
});

test('validation errors show bounded field paths and messages without raw inputs or contexts', () => {
  const errors = Array.from({ length: 6 }, (_, index) => ({
    loc: ['body', 'history', index, 'content'], msg: `Invalid turn ${index}`,
    input: 'private-input', ctx: { error: 'private-context' },
  }));
  const message = apiErrorMessage({ detail: errors });
  assert.equal(message, 'history.0.content: Invalid turn 0; history.1.content: Invalid turn 1; history.2.content: Invalid turn 2');
  assert.doesNotMatch(message, /private|Invalid turn 3/);
});

test('unknown error objects use the caller fallback without serializing arbitrary fields', () => {
  const payload = { detail: { input: 'private-key', ctx: { token: 'private-key' } }, api_key: 'private-key' };
  assert.equal(apiErrorMessage(payload, 'HTTP 422'), 'HTTP 422');
  assert.equal(apiErrorMessage({ detail: [null, { input: 'private-key' }] }, 'HTTP 422'), 'HTTP 422');
});

test('diagnostic and fallback messages are capped without splitting Unicode characters', () => {
  for (const message of [
    apiErrorMessage({ detail: 'x'.repeat(100_000) }),
    apiErrorMessage(null, 'x'.repeat(100_000)),
    apiErrorMessage({ detail: 'x'.repeat(MAX_API_ERROR_CHARS - 2) + '😀tail' }),
  ]) {
    assert(message.length <= MAX_API_ERROR_CHARS);
    assert(message.endsWith('…'));
    assert.equal(new TextDecoder().decode(new TextEncoder().encode(message)), message);
  }
});

test('provider activation displays a real incompatible-model envelope reason', async () => {
  globalThis.fetch = async () => response({ detail: {
    error: 'incompatible_provider_model', provider: 'lmstudio', model: 'missing-catalog-model',
    reason: "lmstudio catalog does not contain 'missing-catalog-model'",
  } }, 400);
  await assert.rejects(activateSelectedProvider('lmstudio', 'missing-catalog-model'), /catalog does not contain/);
});

test('settings save displays the invalid speed field from a real validation envelope', async () => {
  element('speed').value = '0';
  globalThis.fetch = async () => response(validation('speed', 'Input should be greater than or equal to 0.25'));
  await assert.rejects(saveConfigNow(), /speed: Input should be greater than or equal to 0.25/);
});

test('chat displays an overlong model field instead of object coercion', async () => {
  element('lmModel').value = 'x'.repeat(513);
  globalThis.fetch = async () => response(validation('model', 'String should have at most 512 characters'));
  await assert.rejects(askLM('Hello'), /model: String should have at most 512 characters/);
});

test('partial STT uses the same validation message for rejection and its backend pill', async () => {
  globalThis.fetch = async () => response(validation('audio'));
  await assert.rejects(transcribePartialBlob(new Blob(['audio']), 0), /audio: Field required/);
  assert.match(element('sttBackendHealth').textContent, /audio: Field required/);
});

test('partial STT success still updates the transcript and its backend pill', async () => {
  globalThis.fetch = async () => response({
    text: 'Hello', backend: 'faster-whisper', duration: 0.5, seconds: 0.1,
    samples: 8000, sample_rate: 16000, model: 'base.en',
  }, 200);
  const result = await transcribePartialBlob(new Blob(['audio']), 0, { startChunk: 1 });
  assert.equal(result.text, 'Hello');
  assert.equal(element('transcript').value, 'Hello …');
  assert.match(element('sttBackendHealth').textContent, /whisper.*base\.en/);
  assert.equal(element('sttBackendDot').className, 'dot good');
});

test('agent transcript diagnostic formats fields without advancing failed delivery', async () => {
  element('agentEnabled').checked = true;
  globalThis.fetch = async url => url === '/api/agent/status'
    ? response({ busy: false }, 200) : response(validation('transcript', 'List should have at most 64 items'));
  const warn = console.warn; console.warn = () => {};
  try { assert.equal(await deliverTranscriptToAgent(), false); }
  finally { console.warn = warn; }
  assert.equal(state.agentLastDeliveredTurn, 0);
  assert.match(element('agentDebug').textContent, /transcript: List should have at most 64 items/);
});

test('permission failure formats validation and leaves its request pending', async () => {
  element('agentEnabled').checked = true;
  const pending = { id: 'request', message: 'May I continue?' };
  state.awaitingAgentPermission = pending;
  globalThis.fetch = async () => response(validation('request_id', 'String should have at most 128 characters'));
  await assert.rejects(handlePermissionAnswerIfAny('yes'), /request_id: String should have at most 128 characters/);
  assert.equal(state.awaitingAgentPermission, pending);
  assert.deepEqual(state.history, []);
});

for (const [name, body, status, expected] of [
  ['structured validation', validation('roots', 'Path must not contain NUL'), 422, /roots: Path must not contain NUL/],
  ['plain HTTP diagnostics', 'Storage is temporarily unavailable', 503, /Storage is temporarily unavailable/],
]) {
  test(`wired Add-root action displays ${name} with the bounded HTTP reader`, async () => {
    globalThis.fetch = async (_url, options) => options?.method === 'POST'
      ? typeof body === 'string' ? new Response(body, { status }) : response(body, status)
      : response({ roots: [] }, 200);
    wireLocalModelsUi();
    await new Promise(setImmediate);
    element('newLocalModelRoot').value = '/demo/models';
    await handlers.get('addLocalModelRootBtn:click')();
    assert.match(element('localModelsStatus').textContent, expected);
    assert.doesNotMatch(element('localModelsStatus').textContent, /\[object Object\]/);
    assert.equal(element('newLocalModelRoot').value, '/demo/models');
  });
}
