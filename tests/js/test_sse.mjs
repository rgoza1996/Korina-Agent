import test from 'node:test';
import assert from 'node:assert/strict';
import { readJsonEvents } from '../../Korina/js/sse.js';

const encode = value => new TextEncoder().encode(value);
function transport(parts) {
  let index = 0;
  const observed = { cancelled: 0, released: 0, reads: 0 };
  const body = { getReader: () => ({
    read: async () => { observed.reads++; return index < parts.length ? { value: parts[index++], done: false } : { done: true }; },
    cancel: async () => { observed.cancelled++; },
    releaseLock: () => { observed.released++; },
  }) };
  return { body, observed };
}
async function collect(body, options) {
  const events = [];
  for await (const event of readJsonEvents(body, options)) events.push(event);
  return events;
}

for (const newline of ['\n', '\r\n', '\r']) {
  test(`SSE accepts ${JSON.stringify(newline)}, optional field spaces and split UTF-8`, async () => {
    const bytes = encode(`: heartbeat${newline}${newline}event:done${newline}data: {"text":${newline}data:"café 🦊"}${newline}${newline}`);
    const { body, observed } = transport([...bytes].map(byte => new Uint8Array([byte])));
    assert.deepEqual(await collect(body), [{ event: 'done', data: { text: 'café 🦊' } }]);
    assert.equal(observed.cancelled, 0);
    assert.equal(observed.released, 1);
  });
}

for (const [name, value, expected] of [
  ['invalid JSON', 'data: {bad}\n\n', /invalid JSON/],
  ['null JSON', 'data: null\n\n', /invalid JSON event/],
  ['array JSON', 'data: []\n\n', /invalid JSON event/],
  ['scalar JSON', 'data: 4\n\n', /invalid JSON event/],
  ['partial JSON at EOF', 'data: {"audio":"partial', /incomplete event/],
  ['missing frame terminator', 'data: {"text":"complete JSON"}\n', /incomplete event/],
]) {
  test(`SSE rejects ${name} and releases its reader`, async () => {
    const { body, observed } = transport([encode(value)]);
    await assert.rejects(collect(body), expected);
    assert.equal(observed.cancelled, 1);
    assert.equal(observed.released, 1);
  });
}

for (const [name, bytes, expected] of [
  ['invalid bytes', new Uint8Array([255]), /invalid UTF-8/],
  ['an unfinished codepoint', new Uint8Array([195]), /incomplete UTF-8/],
]) {
  test(`SSE rejects ${name}`, async () => {
    const { body, observed } = transport([bytes]);
    await assert.rejects(collect(body), expected);
    assert.equal(observed.cancelled, 1);
  });
}

test('SSE bounds a single unfinished event before accepting more network data', async () => {
  const { body, observed } = transport([encode('data: {"text":"'), encode('x'.repeat(30)), encode('never read')]);
  await assert.rejects(collect(body, { maxEventBytes: 20 }), /event exceeded its size limit/);
  assert.equal(observed.reads, 2);
  assert.equal(observed.cancelled, 1);
});

test('SSE bounds total bytes across many individually small events', async () => {
  const { body, observed } = transport(Array.from({ length: 10 }, () => encode('data: {}\n\n')));
  await assert.rejects(collect(body, { maxTotalBytes: 25 }), /response exceeded its size limit/);
  assert.equal(observed.reads, 3);
  assert.equal(observed.cancelled, 1);
});

test('a consumer failure cancels the underlying response before it can keep streaming', async () => {
  const { body, observed } = transport([encode('data: {}\n\n'), encode('data: {}\n\n')]);
  await assert.rejects((async () => {
    for await (const _event of readJsonEvents(body)) throw new Error('Invalid application payload');
  })(), /Invalid application payload/);
  assert.equal(observed.reads, 1);
  assert.equal(observed.cancelled, 1);
  assert.equal(observed.released, 1);
});

test('stale ownership rejects old events and cancels the response', async () => {
  const { body, observed } = transport([encode('data: {}\n\n')]);
  await assert.rejects(collect(body, { isCurrent: () => false }), { name: 'AbortError' });
  assert.equal(observed.reads, 0);
  assert.equal(observed.cancelled, 1);
});

test('ownership is rechecked between events from the same network read', async () => {
  const { body, observed } = transport([encode('data: {}\n\ndata: {}\n\n')]);
  let current = true, delivered = 0;
  await assert.rejects((async () => {
    for await (const _event of readJsonEvents(body, { isCurrent: () => current })) {
      delivered++;
      current = false;
    }
  })(), { name: 'AbortError' });
  assert.equal(delivered, 1);
  assert.equal(observed.cancelled, 1);
});

test('invalid SSE rejects promptly when native cancellation never settles', { timeout: 1000 }, async () => {
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(encode('data: {bad}\n\n')); },
    cancel() { return new Promise(() => {}); },
  });
  await assert.rejects(collect(stream), /invalid JSON/);
  assert.equal(stream.locked, false);
});
