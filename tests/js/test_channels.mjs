import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { populateChannelTab, saveChannelSelection } from '../../Korina/js/channel-ui.js';

let select, hint;
const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
beforeEach(() => {
  select = { value: '', disabled: false, options: [],
    set innerHTML(_value) { this.options = []; },
    appendChild(option) { this.options.push(option); },
  };
  hint = { textContent: '' };
  globalThis.document = {
    getElementById: id => id === 'channelSelect' ? select : hint,
    createElement: () => ({}),
  };
});

test('channel discovery renders the saved selection and deduplicates options', async () => {
  globalThis.fetch = async url => response(url.endsWith('/channels')
    ? { channels: ['korina', 'testing', 'korina'] } : { channel: 'testing' });
  await populateChannelTab();
  assert.equal(select.value, 'testing');
  assert.deepEqual(select.options.map(option => option.value), ['korina', 'testing']);
  assert.equal(select.disabled, false);
  assert.match(hint.textContent, /Active: testing/);
});

test('an empty registry has a disabled placeholder and no invented active channel', async () => {
  globalThis.fetch = async url => response(url.endsWith('/channels') ? { channels: [] } : { channel: null });
  await populateChannelTab();
  assert.deepEqual(select.options, [{ value: '', textContent: '-' }]);
  assert.equal(select.disabled, true);
  assert.equal(hint.textContent, 'No channels registered.');
});

for (const failed of ['/channels', '/channel']) {
  test(`failure of ${failed} remains visible and an ordinary retry recovers`, async () => {
    let fail = true;
    globalThis.fetch = async url => fail && url.endsWith(failed)
      ? response({ detail: 'Channel registry unavailable' }, 503)
      : response(url.endsWith('/channels') ? { channels: ['korina'] } : { channel: 'korina' });
    await populateChannelTab();
    assert.equal(select.disabled, true);
    assert.match(hint.textContent, /HTTP 503: Channel registry unavailable.*retry/);
    fail = false;
    await populateChannelTab();
    assert.equal(select.disabled, false);
    assert.equal(select.value, 'korina');
  });
}

test('older discovery cannot replace the newer selection or its success hint', async () => {
  const old = deferred(); let calls = 0;
  // Capture old bodies before resolving the newer pair.
  globalThis.fetch = url => {
    const index = calls++;
    const body = url.endsWith('/channels') ? { channels: ['korina', 'testing'] }
      : { channel: index < 2 ? 'korina' : 'testing' };
    return index < 2 ? old.promise.then(() => response(body)) : Promise.resolve(response(body));
  };
  const first = populateChannelTab();
  await populateChannelTab();
  old.resolve(); await first;
  assert.equal(select.value, 'testing');
  assert.match(hint.textContent, /Active: testing/);
});

test('starting a selection invalidates old discovery and serializes the visible save', async () => {
  const old = deferred(), saved = deferred(); let active = 'korina', reads = 0, posts = 0;
  globalThis.fetch = (url, options) => {
    if (options) {
      posts++;
      return saved.promise.then(() => { active = 'testing'; return response({ ok: true, channel: active }); });
    }
    const body = url.endsWith('/channels') ? { channels: ['korina', 'testing'] } : { channel: active };
    return reads++ < 2 ? old.promise.then(() => response(body)) : Promise.resolve(response(body));
  };
  const loading = populateChannelTab();
  select.value = 'testing';
  const saving = saveChannelSelection();
  await populateChannelTab();
  await saveChannelSelection();
  old.resolve(); await loading;
  assert.equal(select.disabled, true);
  assert.match(hint.textContent, /Switching to: testing/);
  saved.resolve(); await saving;
  assert.equal(posts, 1);
  assert.equal(select.value, 'testing');
  assert.equal(select.disabled, false);
  assert.match(hint.textContent, /Active: testing/);
});

for (const [name, body, status] of [
  ['HTTP rejection', { detail: 'Cannot save channel' }, 503],
  ['unconfirmed success', { ok: true, channel: 'another' }, 200],
]) {
  test(`${name} does not claim a selection was saved and permits retry`, async () => {
    select.value = 'testing'; let calls = 0;
    globalThis.fetch = async () => { calls++; return response(body, status); };
    await saveChannelSelection();
    assert.equal(calls, 1);
    assert.equal(select.disabled, false);
    assert.match(hint.textContent, /Switch failed:/);
    assert.doesNotMatch(hint.textContent, /Selection is saved/);
  });
}

test('malformed registry data preserves previous options and gives a retryable diagnostic', async () => {
  select.options.push({ value: 'korina', textContent: 'korina' });
  globalThis.fetch = async url => response(url.endsWith('/channels') ? { channels: 'korina' } : { channel: 'korina' });
  await populateChannelTab();
  assert.deepEqual(select.options, [{ value: 'korina', textContent: 'korina' }]);
  assert.match(hint.textContent, /Invalid channel discovery response.*retry/);
});
