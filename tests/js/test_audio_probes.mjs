import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { listAudioProbes } from '../../Korina/js/audio-probes.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const response = payload => new Response(JSON.stringify(payload));

test('a full server-supported Unicode cache fits the download and codepoint limits', async () => {
  const basePrefix = 'https://probe.fixture/v1/';
  const base = basePrefix + '😀'.repeat(4096 - basePrefix.length);
  const entries = {};
  for (let index = 0; index < 512; index++) {
    const prefix = `model-${index}-`;
    const model = prefix + '😀'.repeat(512 - prefix.length);
    entries[`${'😀'.repeat(64)}::${base}::${model}`] = {
      reason: 'http_415_unsupported_media_type', since: '2026-10-22T00:00:00.000000+00:00', last_error: '😀'.repeat(500),
    };
  }
  const body = response({ entries });
  assert.ok(Number((await body.clone().blob()).size) > 10 * 1024 * 1024);
  globalThis.fetch = async () => body;
  assert.deepEqual((await listAudioProbes()).entries, entries);
});

for (const [name, body] of [
  ['missing entries', {}], ['array entries', { entries: [] }],
  ['too many entries', { entries: Object.fromEntries(Array.from({ length: 513 }, (_, index) => [String(index), {}])) }],
  ['invalid entry', { entries: { model: [] } }],
  ['invalid reason', { entries: { model: { reason: 123 } } }],
  ['long diagnostic', { entries: { model: { last_error: '😀'.repeat(501) } } }],
  ['long key', { entries: { ['😀'.repeat(4677)]: {} } }],
  ['unpaired surrogate', { entries: { model: { reason: '\ud800' } } }],
]) {
  test(`probe cache rejects ${name} instead of publishing a usable-looking entry`, async () => {
    globalThis.fetch = async () => response(body);
    await assert.rejects(listAudioProbes(), /audio probe cache/i);
  });
}

test('optional legacy fields remain optional and a confirmed empty cache is authoritative', async () => {
  globalThis.fetch = async () => response({ entries: { model: { reason: 'unsupported' } } });
  assert.deepEqual(await listAudioProbes(), { entries: { model: { reason: 'unsupported' } } });
  globalThis.fetch = async () => response({ entries: {} });
  assert.deepEqual(await listAudioProbes(), { entries: {} });
});

test('malformed JSON and invalid UTF8 are rejected without echoing provider data', async () => {
  for (const body of ['private malformed body', new Uint8Array([123, 34, 120, 34, 58, 34, 255, 34, 125])]) {
    globalThis.fetch = async () => new Response(body);
    await assert.rejects(listAudioProbes(), error => /invalid audio probe cache response/.test(error.message)
      && !error.message.includes('private'));
  }
});

for (const declared of [true, false]) {
  test(`probe cache ${declared ? 'declared' : 'streamed'} oversize cancels before JSON parsing`, async () => {
    let cancelled = false;
    const stream = new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(16 * 1024 * 1024 + 1)); },
      cancel() { cancelled = true; },
    });
    globalThis.fetch = async () => new Response(stream,
      { headers: declared ? { 'Content-Length': String(16 * 1024 * 1024 + 1) } : {} });
    await assert.rejects(listAudioProbes(), /download size limit/);
    assert.equal(cancelled, true);
    assert.equal(stream.locked, false);
  });
}

test('a retired standalone cache read cannot admit or publish a response', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return response({ entries: {} }); };
  await assert.rejects(listAudioProbes({ isCurrent: () => false }), { name: 'AbortError' });
  assert.equal(calls, 0);
  const owner = new AbortController(); owner.abort();
  await assert.rejects(listAudioProbes({ signal: owner.signal }), { name: 'AbortError' });
  assert.equal(calls, 0);
});
