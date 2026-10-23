import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { cancelAckPlayback, changeAckVoice, loadAcks, playRandomAck, preloadAckBuffers } from '../../Korina/js/acks.js';
import { stopLive } from '../../Korina/js/live.js';
import { MAX_ACK_AUDIO_BYTES } from '../../Korina/js/response-body.js';

const elements = new Map();
const sources = [];
let requests;
function element(id) {
  if (!elements.has(id)) elements.set(id, { value: '', textContent: '', style: {}, children: [],
    classList: { add() {}, remove() {} }, appendChild(node) { this.children.push(node); } });
  return elements.get(id);
}
function jsonResponse(value, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function delayedAudio() {
  const started = deferred();
  const finish = deferred();
  globalThis.fetch = async url => {
    requests.push(url);
    if (url.startsWith('/api/acks')) return jsonResponse({ acks: [{ url: '/ack.wav', text: 'One moment.' }] });
    started.resolve();
    await finish.promise;
    return new Response(new Uint8Array(4));
  };
  return { started: started.promise, finish: finish.resolve };
}

beforeEach(() => {
  elements.clear(); sources.length = 0; requests = [];
  globalThis.document = { getElementById: element, createElement: () => ({}) };
  globalThis.cancelAnimationFrame = () => {};
  element('voice').value = 'af_heart';
  element('ackEnabled').value = 'on';
  const context = { state: 'running', currentTime: 10,
    decodeAudioData: async () => ({ duration: 1 }),
    createBufferSource() {
      const source = { connect() {}, start() { sources.push(this); }, stop() { this.stopped = true; this.onended?.(); } };
      return source;
    } };
  Object.assign(state, { live: true, liveSession: 1, agentSession: 1, audioCtx: context,
    gain: {}, activeSources: [], controller: null, ackFiles: [], ackBuffersByTag: {},
    ackSource: null, ackPlayingUntil: 0, suppressAckUntil: 0,
    pendingImportantAgentMessage: '', awaitingAgentPermission: null,
    partialRecorder: null, partialTimer: null, partialQueue: [], partialController: null,
    partialRecorderDone: null, liveTranscriptionController: null,
    mediaRecorder: null, bargeRecorder: null, stream: null, meterAudioCtx: null,
    liveLoopId: null, bargeLoopId: null, raf: null,
  });
  cancelAckPlayback();
});
afterEach(() => { cancelAckPlayback(); });

test('concurrent idle triggers share one preload and schedule one acknowledgement', async () => {
  const audio = delayedAudio();
  const plays = Array.from({ length: 20 }, () => playRandomAck('idle'));
  await audio.started;
  assert.equal(requests.filter(url => url.startsWith('/api/acks')).length, 1);
  assert.equal(requests.filter(url => url === '/ack.wav').length, 1);
  audio.finish();
  await Promise.all(plays);
  assert.equal(sources.length, 1);
  assert.equal(element('log').children.length, 1);
});

for (const [name, invalidate] of [
  ['Stop Live', () => stopLive()],
  ['session reset', () => { state.agentSession++; }],
  ['voice change', () => { element('voice').value = 'af_bella'; }],
  ['disabled acknowledgements', () => { element('ackEnabled').value = 'off'; }],
  ['pending permission', () => { state.awaitingAgentPermission = { id: 'new' }; }],
]) {
  test(`an acknowledgement finishing after ${name} cannot play or overwrite status`, async () => {
    const audio = delayedAudio();
    const playing = playRandomAck();
    await audio.started;
    invalidate();
    element('sttStatus').textContent = 'The current session status';
    audio.finish();
    await playing;
    assert.equal(sources.length, 0);
    assert.equal(element('log').children.length, 0);
    assert.equal(element('sttStatus').textContent, 'The current session status');
  });
}

test('catalog and buffer loading never replace transcription or playback status', async () => {
  const audio = delayedAudio();
  element('sttStatus').textContent = 'Current transcription error';
  await loadAcks();
  const loading = preloadAckBuffers();
  await audio.started;
  audio.finish();
  assert.equal((await loading).length, 1);
  assert.equal(element('sttStatus').textContent, 'Current transcription error');
});

test('cancelling a playing acknowledgement stops and releases its source', async () => {
  const audio = delayedAudio();
  const playing = playRandomAck();
  await audio.started; audio.finish(); await playing;
  const source = sources[0];
  cancelAckPlayback();
  assert.equal(source.stopped, true);
  assert.equal(state.ackSource, null);
  assert.equal(state.ackPlayingUntil, 0);
});

test('completed empty preload attempts back off instead of polling on every idle frame', async () => {
  let catalogs = 0;
  globalThis.fetch = async () => {
    catalogs++;
    return jsonResponse({ acks: [] });
  };
  for (let frame = 0; frame < 20; frame++) await playRandomAck('idle');
  assert.equal(catalogs, 1);
  cancelAckPlayback();
  await playRandomAck('idle');
  assert.equal(catalogs, 2, 'a fresh session can discover newly available audio');
});

test('failed audio preload attempts also back off', async () => {
  const previousWarn = console.warn;
  console.warn = () => {};
  try {
    globalThis.fetch = async url => {
      requests.push(url);
      if (url.startsWith('/api/acks')) return jsonResponse({ acks: [{ url: '/missing.wav' }] });
      return { ok: false, status: 503 };
    };
    for (let frame = 0; frame < 20; frame++) await playRandomAck();
    assert.equal(requests.filter(url => url === '/missing.wav').length, 1);
    assert.equal(sources.length, 0);
  } finally { console.warn = previousWarn; }
});

test('cancellation aborts a pending acknowledgement download', async () => {
  const started = deferred();
  let signal;
  globalThis.fetch = async (url, options) => {
    if (url.startsWith('/api/acks')) return jsonResponse({ acks: [{ url: '/pending.wav' }] });
    signal = options.signal;
    started.resolve();
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')), { once: true }));
  };
  const playing = playRandomAck();
  await started.promise;
  cancelAckPlayback();
  await playing;
  assert.equal(signal.aborted, true);
  assert.equal(sources.length, 0);
});

test('oversized acknowledgement downloads never reach the native decoder', async () => {
  let decodes = 0, cancelled = 0;
  state.audioCtx.decodeAudioData = async () => { decodes++; return { duration: 1 }; };
  globalThis.fetch = async url => {
    if (url.startsWith('/api/acks')) return jsonResponse({ acks: [{ url: '/huge.wav' }] });
    return new Response(new ReadableStream({ cancel() { cancelled++; } }), {
      headers: { 'Content-Length': String(MAX_ACK_AUDIO_BYTES + 1) },
    });
  };
  const warn = console.warn;
  console.warn = () => {};
  try { assert.deepEqual(await preloadAckBuffers(), []); }
  finally { console.warn = warn; }
  assert.equal(decodes, 0);
  assert.equal(cancelled, 1);
  assert.equal(sources.length, 0);
});

test('a decoded overlong acknowledgement is rejected without scheduling playback', async () => {
  state.audioCtx.decodeAudioData = async () => ({ duration: 601 });
  const audio = delayedAudio();
  const warn = console.warn;
  console.warn = () => {};
  try {
    const playing = playRandomAck();
    await audio.started;
    audio.finish();
    await playing;
  } finally { console.warn = warn; }
  assert.equal(sources.length, 0);
  assert.deepEqual(state.ackBuffersByTag.global, []);
});

test('acknowledgement cancellation releases a pending native response reader', { timeout: 1000 }, async () => {
  const started = deferred();
  let body;
  globalThis.fetch = async url => {
    if (url.startsWith('/api/acks')) return jsonResponse({ acks: [{ url: '/pending.wav' }] });
    body = new ReadableStream({ pull() { started.resolve(); }, cancel() { return new Promise(() => {}); } });
    return new Response(body);
  };
  const playing = playRandomAck();
  await started.promise;
  cancelAckPlayback();
  await playing;
  assert.equal(body.locked, false);
  assert.equal(sources.length, 0);
});

for (const [name, response] of [
  ['HTTP 503', () => jsonResponse({ detail: [{ loc: ['body', 'voice'], msg: 'Service unavailable' }] }, 503)],
  ['malformed JSON', () => new Response('{')],
  ['explicit failure', () => jsonResponse({ ok: false, voice: 'af_heart', removed: 0, queued_or_missing: 0 })],
]) {
  test(`voice rebuild ${name} is visible without replacing transcription status`, async () => {
    globalThis.fetch = async () => response();
    element('sttStatus').textContent = 'Current STT error';
    element('transcriptInfo').textContent = 'Current transcript';
    await changeAckVoice(async () => {});
    assert.match(element('ackStatus').textContent, /Acknowledgement rebuild failed:/);
    assert.doesNotMatch(element('ackStatus').textContent, /\[object Object\]/);
    if (name === 'HTTP 503') assert.match(element('ackStatus').textContent, /voice: Service unavailable/);
    assert.equal(element('sttStatus').textContent, 'Current STT error');
    assert.equal(element('transcriptInfo').textContent, 'Current transcript');
  });
}

test('voice save failure preserves cached clips and cannot clear or rebuild on the backend', async () => {
  const buffers = [{ buffer: 'previous audio' }];
  state.ackBuffersByTag.global = buffers;
  globalThis.fetch = async () => assert.fail('A failed settings save must precede any rebuild request');
  await changeAckVoice(async () => { throw new Error('Disk unavailable'); });
  assert.equal(state.ackBuffersByTag.global, buffers);
  assert.match(element('ackStatus').textContent, /Voice settings save failed: Disk unavailable/);
});

test('voice rebuild waits for settings acceptance and serializes/coalesces newer voices', async () => {
  const first = deferred(), entered = deferred(), save = deferred();
  const voices = [];
  globalThis.fetch = async (_url, options) => {
    const payload = JSON.parse(options.body);
    assert.equal(payload.require_saved_voice, true);
    const voice = payload.voice;
    voices.push(voice);
    if (voice === 'af_heart') { entered.resolve(); await first.promise; }
    return jsonResponse({ ok: true, voice, removed: 0, queued_or_missing: 1 });
  };
  const initial = changeAckVoice(() => save.promise);
  assert.deepEqual(voices, []);
  save.resolve(); await entered.promise;
  element('voice').value = 'af_bella';
  const middle = changeAckVoice(async () => {});
  element('voice').value = 'af_nicole';
  const newest = changeAckVoice(async () => {});
  await Promise.resolve();
  assert.deepEqual(voices, ['af_heart']);
  first.resolve(); await Promise.all([initial, middle, newest]);
  assert.deepEqual(voices, ['af_heart', 'af_nicole']);
  assert.match(element('ackStatus').textContent, /queued for af_nicole/);
  assert.doesNotMatch(element('ackStatus').textContent, /af_heart|af_bella/);
});

test('retrying a failed voice save rebuilds only after the new save succeeds', async () => {
  let saved = false;
  const payloads = [];
  globalThis.fetch = async (_url, options) => {
    assert.equal(saved, true);
    const payload = JSON.parse(options.body); payloads.push(payload);
    return jsonResponse({ ok: true, voice: payload.voice, removed: 0, queued_or_missing: 1 });
  };
  await changeAckVoice(async () => { throw new Error('Temporary save failure'); });
  assert.deepEqual(payloads, []);
  assert.match(element('ackStatus').textContent, /Voice settings save failed/);
  await changeAckVoice(async () => { saved = true; });
  assert.deepEqual(payloads, [{ voice: 'af_heart', clear: true, require_saved_voice: true }]);
  assert.match(element('ackStatus').textContent, /rebuild queued for af_heart/);
});

test('a rebuild completing after session cancellation cannot publish status or schedule a catalog reload', async () => {
  const entered = deferred(), finish = deferred();
  globalThis.fetch = async () => { entered.resolve(); await finish.promise;
    return jsonResponse({ ok: true, voice: 'af_heart', removed: 0, queued_or_missing: 1 }); };
  const rebuilding = changeAckVoice(async () => {});
  await entered.promise;
  cancelAckPlayback();
  state.agentSession++;
  element('ackStatus').textContent = 'The new session owns this status';
  finish.resolve(); await rebuilding;
  assert.equal(element('ackStatus').textContent, 'The new session owns this status');
});

test('invalid catalog shapes are visible and do not become a successful empty catalog', async () => {
  globalThis.fetch = async () => jsonResponse({ acks: 'invalid' });
  assert.deepEqual(await loadAcks(), []);
  assert.match(element('ackStatus').textContent, /ACK catalog unavailable: Invalid acknowledgement catalog/);
});

test('a stalled rebuild times out visibly and a later voice selection can retry', async () => {
  const nativeTimeout = globalThis.setTimeout;
  const entered = deferred();
  let expire;
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay === 15000) { expire = callback; return undefined; }
    return nativeTimeout(callback, delay, ...args);
  };
  globalThis.fetch = async (_url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')), { once: true });
    entered.resolve();
  });
  try {
    const first = changeAckVoice(async () => {});
    await entered.promise;
    expire(); await first;
    assert.match(element('ackStatus').textContent, /request timed out/);
  } finally { globalThis.setTimeout = nativeTimeout; }
  globalThis.fetch = async () => jsonResponse({ ok: true, voice: 'af_heart', removed: 0, queued_or_missing: 1 });
  await changeAckVoice(async () => {});
  assert.match(element('ackStatus').textContent, /rebuild queued for af_heart/);
});
