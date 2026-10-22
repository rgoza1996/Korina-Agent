import test from 'node:test';
import assert from 'node:assert/strict';
import { readResponseBlob, responseError } from '../../Korina/js/response-body.js';

function response(parts, headers = {}) {
  let index = 0;
  const seen = { reads: 0, cancelled: 0, released: 0 };
  return { seen, body: { headers: new Headers(headers), body: { getReader: () => ({
    read: async () => { seen.reads++; return index < parts.length ? { done: false, value: parts[index++] } : { done: true }; },
    cancel: () => { seen.cancelled++; }, releaseLock: () => { seen.released++; },
  }), cancel: () => { seen.cancelled++; } } } };
}

test('bounded binary reads preserve every byte and content type and release the reader', async () => {
  const { body, seen } = response([new Uint8Array([1, 2]), new Uint8Array([3, 4])], { 'Content-Type': 'audio/wav' });
  const blob = await readResponseBlob(body, { maxBytes: 4 });
  assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())], [1, 2, 3, 4]);
  assert.equal(blob.type, 'audio/wav');
  assert.deepEqual(seen, { reads: 3, cancelled: 0, released: 1 });
});

test('Content-Length rejects oversized downloads before reading their body', async () => {
  const { body, seen } = response([new Uint8Array(10)], { 'Content-Length': '10' });
  await assert.rejects(readResponseBlob(body, { maxBytes: 4 }), /download size limit/);
  assert.deepEqual(seen, { reads: 0, cancelled: 1, released: 0 });
});

for (const length of [undefined, '1', 'invalid']) {
  test(`counted bytes enforce the limit even with Content-Length ${length}`, async () => {
    const { body, seen } = response([new Uint8Array(3), new Uint8Array(3), new Uint8Array(3)],
      length === undefined ? {} : { 'Content-Length': length });
    await assert.rejects(readResponseBlob(body, { maxBytes: 4 }), /download size limit/);
    assert.deepEqual(seen, { reads: 2, cancelled: 1, released: 1 });
  });
}

test('an already aborted owner cancels the unread response without consuming bytes', async () => {
  const controller = new AbortController();
  controller.abort();
  const { body, seen } = response([new Uint8Array(4)]);
  await assert.rejects(readResponseBlob(body, { signal: controller.signal }), { name: 'AbortError' });
  assert.deepEqual(seen, { reads: 0, cancelled: 1, released: 0 });
});

test('an owner changing while a read is pending cannot retain old response data', async () => {
  let current = true, finish;
  const seen = { cancelled: 0, released: 0 };
  const body = { body: { getReader: () => ({
    read: () => new Promise(resolve => { finish = resolve; }),
    cancel: () => { seen.cancelled++; }, releaseLock: () => { seen.released++; },
  }) } };
  const reading = readResponseBlob(body, { isCurrent: () => current });
  current = false;
  finish({ value: new Uint8Array(4), done: false });
  await assert.rejects(reading, { name: 'AbortError' });
  assert.deepEqual(seen, { cancelled: 1, released: 1 });
});

test('abort settles a pending native read even when underlying cancellation never finishes', { timeout: 1000 }, async () => {
  const controller = new AbortController();
  let cancelled = 0;
  const stream = new ReadableStream({ cancel() { cancelled++; return new Promise(() => {}); } });
  const reading = readResponseBlob(new Response(stream), { signal: controller.signal });
  controller.abort();
  await assert.rejects(reading, { name: 'AbortError' });
  assert.equal(cancelled, 1);
  assert.equal(stream.locked, false);
});

test('an oversized native read reports failure without awaiting a stuck cancel callback', { timeout: 1000 }, async () => {
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(5)); },
    cancel() { return new Promise(() => {}); },
  });
  await assert.rejects(readResponseBlob(new Response(stream), { maxBytes: 4 }), /download size limit/);
  assert.equal(stream.locked, false);
});

for (const [name, text, expected] of [
  ['JSON diagnostics', '{"detail":"Model missing"}', /HTTP 503: Model missing/],
  ['plain diagnostics', 'Model missing', /HTTP 503: Model missing/],
  ['an oversized diagnostic body', 'x'.repeat(4097), /HTTP 503:.*download size limit/],
]) {
  test(`HTTP errors use bounded ${name}`, async () => {
    const error = await responseError(new Response(text, { status: 503 }));
    assert.match(error.message, expected);
    assert(error.message.length < 4097);
  });
}
