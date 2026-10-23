import test from 'node:test';
import assert from 'node:assert/strict';
import { filterModelsByCapability } from '../../Korina/js/capability-filter.js';

test('the audio requirement excludes text-only models and keeps audio-capable models', () => {
  const models = ['google/gemma-4-e4b', 'google/gemma-4-e2b-it-mmproj',
    'ultravox-v0_5-llama-3_1-8b', 'qwen2.5-1.5b-instruct'];
  const capabilities = Object.fromEntries(models.map((model, index) => [model,
    { supports_audio_input: index === 1 || index === 2 }]));
  assert.deepEqual(filterModelsByCapability(models, capabilities, { kind: 'audio_input' }), models.slice(1, 3));
});

test('the any-model requirement preserves text-only choices', () => {
  const models = ['google/gemma-4-e4b', 'qwen2.5-1.5b-instruct'];
  const capabilities = Object.fromEntries(models.map(model => [model, { supports_audio_input: false }]));
  assert.deepEqual(filterModelsByCapability(models, capabilities, { kind: 'any' }), models);
});
