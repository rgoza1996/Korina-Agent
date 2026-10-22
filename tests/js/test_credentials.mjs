import test from 'node:test';
import assert from 'node:assert/strict';
import { effectiveSttLlmApiKeyEnv } from '../../Korina/js/settings-ui.js';

const elements = new Map();
globalThis.document = { getElementById: id => elements.get(id) };

for (const [base, inherits] of [
  ['', true],
  ['https://response.test/v1', true],
  ['https://response.test/v1/chat/completions/', true],
  ['https://audio.test/v1', false],
  ['https://response.test/other', false],
  ['https://response.test.evil/v1', false],
  ['http://response.test/v1', false],
]) {
  test(`STT credential inheritance is scoped to ${base || 'the inherited response URL'}`, () => {
    elements.set('llmBaseUrl', { value: 'https://response.test/v1/' });
    elements.set('llmApiKeyEnv', { value: 'RESPONSE_KEY' });
    elements.set('sttLlmBaseUrl', { value: base });
    elements.set('sttLlmApiKeyEnv', { value: '' });
    assert.equal(effectiveSttLlmApiKeyEnv(), inherits ? 'RESPONSE_KEY' : '');
    elements.set('sttLlmApiKeyEnv', { value: 'EXPLICIT_AUDIO_KEY' });
    assert.equal(effectiveSttLlmApiKeyEnv(), 'EXPLICIT_AUDIO_KEY');
  });
}
