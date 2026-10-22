import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { speakBuffered, speakText, stopTtsNow } from '../../Korina/js/speech.js';

class AudioElement extends EventTarget {
  src = '';
  paused = true;
  duration = 2;
  pauseCalls = 0;
  loadCalls = 0;
  playCalls = 0;
  play() {
    this.playCalls++;
    this.paused = false;
    this.dispatchEvent(new Event('playing'));
    return Promise.resolve();
  }
  pause() { this.pauseCalls++; this.paused = true; }
  removeAttribute(name) { if (name === 'src') this.src = ''; }
  load() { this.loadCalls++; }
  finish() { this.paused = true; this.dispatchEvent(new Event('ended')); }
}

const elements = new Map();
const created = [];
const revoked = [];
const createObjectURL = URL.createObjectURL;
const revokeObjectURL = URL.revokeObjectURL;
const element = id => {
  if (!elements.has(id)) elements.set(id, { value: '', textContent: '', className: '', style: {}, disabled: false });
  return elements.get(id);
};
const tick = () => new Promise(setImmediate);
const response = () => ({ ok: true, blob: async () => new Blob(['audio fixture']) });

beforeEach(() => {
  elements.clear(); created.length = 0; revoked.length = 0;
  elements.set('audio', new AudioElement());
  globalThis.document = { getElementById: element };
  element('ttsBaseUrl').value = 'http://speech.test';
  element('voice').value = 'af_heart';
  element('speed').value = '1';
  URL.createObjectURL = blob => { const url = `blob:test-${created.length}`; created.push({ blob, url }); return url; };
  URL.revokeObjectURL = url => revoked.push(url);
  globalThis.fetch = async () => response();
  Object.assign(state, {
    mode: 'buf', live: false, audioCtx: { state: 'running', currentTime: 10 },
    controller: null, activeSources: [], handlingBarge: false, bargeLoopId: null, bargeRecorder: null,
  });
});
afterEach(() => {
  stopTtsNow();
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
});

test('buffered speech keeps its lifecycle active until native playback ends and releases its URL', async () => {
  let complete = false;
  const pending = speakText('A buffered reply.', true).then(() => { complete = true; });
  await tick();
  assert.equal(complete, false);
  assert.equal(state.ttsSpeaking, true);
  assert.equal(element('stopBtn').disabled, false);
  assert.equal(element('playBtn').disabled, true);
  assert.equal(element('audio').paused, false);
  assert.equal(state.currentTtsEstimatedEnd, 12);
  assert.match(element('ttsStatus').textContent, /Playing buffered/);

  element('audio').finish();
  await pending;
  assert.equal(state.ttsSpeaking, false);
  assert.equal(element('stopBtn').disabled, true);
  assert.equal(element('playBtn').disabled, false);
  assert.equal(element('audio').src, '');
  assert.equal(element('audio').loadCalls, 1);
  assert.deepEqual(revoked, ['blob:test-0']);
  assert.match(element('ttsStatus').textContent, /playback complete/);
});

test('a rejected buffered play request reports failure and cleans up instead of reporting success', async () => {
  element('audio').play = async () => { throw new Error('Autoplay permission denied'); };
  await assert.rejects(speakText('A rejected reply.', true), /Autoplay permission denied/);
  assert.equal(element('ttsStatus').className, 'status bad');
  assert.doesNotMatch(element('ttsStatus').textContent, /Done/);
  assert.equal(element('audio').src, '');
  assert.deepEqual(revoked, ['blob:test-0']);
  assert.equal(state.ttsSpeaking, false);
});

test('a native audio decode error rejects the buffered turn and releases its resources', async () => {
  const pending = speakText('An invalid audio reply.', true);
  await tick();
  element('audio').error = { message: 'Audio could not be decoded' };
  element('audio').dispatchEvent(new Event('error'));
  await assert.rejects(pending, /Audio could not be decoded/);
  assert.equal(element('ttsStatus').className, 'status bad');
  assert.equal(element('stopBtn').disabled, true);
  assert.deepEqual(revoked, ['blob:test-0']);
});

test('Stop pauses buffered playback, resolves its waiting turn, and revokes its URL once', async () => {
  const pending = speakText('Stop this reply.', true);
  await tick();
  stopTtsNow();
  await pending;
  assert.equal(element('audio').paused, true);
  assert.equal(element('audio').src, '');
  assert.equal(state.ttsSpeaking, false);
  assert.equal(element('stopBtn').disabled, true);
  assert.equal(element('ttsStatus').textContent, 'Interrupted');
  stopTtsNow();
  assert.deepEqual(revoked, ['blob:test-0']);
});

test('a buffered response arriving after Stop cannot begin playback', async () => {
  let finish;
  globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = speakText('This request will be cancelled.', true);
  stopTtsNow();
  finish(response());
  await pending;
  assert.equal(element('audio').playCalls, 0);
  assert.equal(created.length, 0);
  assert.equal(element('ttsStatus').textContent, 'Interrupted');
});

test('Stop settles a buffered turn while native fetch is still pending', async () => {
  globalThis.fetch = (url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Request aborted', 'AbortError')), { once: true });
  });
  const pending = speakText('This fetch has not responded.', true);
  stopTtsNow();
  await pending;
  assert.equal(created.length, 0);
  assert.equal(state.ttsSpeaking, false);
  assert.equal(element('stopBtn').disabled, true);
});

for (const phase of ['fetch', 'playback']) {
  test(`direct buffered calls reject AbortError promptly when stopped during ${phase}`, async () => {
    if (phase === 'fetch') globalThis.fetch = (url, { signal }) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Request aborted', 'AbortError')), { once: true });
    });
    const pending = speakBuffered('A directly invoked buffered reply.');
    await tick();
    stopTtsNow();
    await assert.rejects(pending, { name: 'AbortError' });
    assert.equal(element('audio').paused, true);
    assert.equal(created.length, revoked.length);
  });
}

for (const [name, body, error] of [
  ['empty HTTP 200', '', /no audio samples/],
  ['empty PCM despite a positive metadata sample count', 'data: {"audio":"","samples":999,"sample_rate":24000}\n\n', /no audio samples/],
  ['a missing audio field', 'data: {"samples":999,"sample_rate":24000}\n\n', /missing PCM audio/],
]) {
  test(`SSE speech reports failure for ${name}`, async () => {
    state.mode = 'sse';
    globalThis.fetch = async () => {
      let read = false;
      return { ok: true, body: { getReader: () => ({ read: async () => {
        if (read || !body) return { done: true };
        read = true;
        return { done: false, value: new TextEncoder().encode(body) };
      } }) } };
    };
    await assert.rejects(speakText('No audio was synthesized.', true), error);
    assert.equal(element('ttsStatus').className, 'status bad');
    assert.doesNotMatch(element('ttsStatus').textContent, /Done/);
  });
}

test('SSE progress counts scheduled PCM frames rather than trusting provider metadata', async () => {
  state.mode = 'sse';
  state.gain = {};
  state.audioCtx.createBuffer = () => ({ copyToChannel() {} });
  state.audioCtx.createBufferSource = () => ({ connect() {}, start() {} });
  const body = 'data: {"audio":"AQACAA==","samples":0,"sample_rate":24000}\n\n';
  globalThis.fetch = async () => {
    let read = false;
    return { ok: true, body: { getReader: () => ({ read: async () => {
      if (read) return { done: true };
      read = true;
      return { done: false, value: new TextEncoder().encode(body) };
    } }) } };
  };
  await speakText('A valid PCM chunk.', false);
  assert.match(element('ttsStatus').textContent, /Done · 1 chunks/);
  assert.match(element('chunkInfo').textContent, /2 samples/);
});

test('finishing a stopped old reply cannot clear the new buffered playback lifecycle', async () => {
  const old = speakText('The old reply.', true);
  await tick();
  stopTtsNow();
  const current = speakText('The replacement reply.', true);
  await tick();
  await old;
  assert.equal(state.ttsSpeaking, true);
  assert.equal(element('audio').paused, false);
  assert.equal(element('stopBtn').disabled, false);
  assert.notEqual(state.controller, null);
  assert.match(element('ttsStatus').textContent, /Playing buffered/);
  assert.deepEqual(revoked, ['blob:test-0']);
  element('audio').finish();
  await current;
  assert.deepEqual(revoked, ['blob:test-0', 'blob:test-1']);
});

function pcmAudioContext() {
  const sources = [];
  state.gain = {};
  Object.assign(state.audioCtx, {
    createBuffer: () => ({ copyToChannel() {} }),
    createBufferSource: () => {
      const source = { stopped: 0, connect() {}, start() {}, stop() { this.stopped++; } };
      sources.push(source);
      return source;
    },
  });
  return sources;
}

const pcmEvent = 'data: {"audio":"AQACAA==","sample_rate":24000}\n\n';
const encoded = value => new TextEncoder().encode(value);

for (const failure of ['invalid JSON', 'missing PCM', 'network error']) {
  test(`an SSE ${failure} after valid PCM stops scheduled audio before restoring controls`, async () => {
    state.mode = 'sse';
    const sources = pcmAudioContext();
    let reads = 0, cancelled = 0;
    globalThis.fetch = async () => ({ ok: true, body: { getReader: () => ({
      read: async () => {
        if (reads++ === 0) return { done: false, value: encoded(pcmEvent) };
        if (failure === 'network error') throw new Error('Stream connection failed');
        return { done: false, value: encoded(failure === 'invalid JSON' ? 'data: {bad\n\n' : 'data: {"error":"synthesis failed"}\n\n') };
      },
      cancel: async () => { cancelled++; },
    }) } });
    await assert.rejects(speakText('A partially synthesized reply.', true));
    assert.equal(sources.length, 1);
    assert(sources[0].stopped > 0);
    assert.equal(state.activeSources.length, 0);
    assert.equal(state.controller, null);
    assert.equal(state.ttsSpeaking, false);
    assert.equal(element('stopBtn').disabled, true);
    assert.equal(element('playBtn').disabled, false);
    assert.equal(element('ttsStatus').className, 'status bad');
    assert.equal(cancelled, 1);
  });
}

for (const completion of ['error', 'late PCM']) {
  test(`an old SSE ${completion} cannot stop or overwrite a replacement buffered reply`, async () => {
    state.mode = 'sse';
    const sources = pcmAudioContext();
    let finishOldRead, reads = 0;
    globalThis.fetch = async () => ({ ok: true, body: { getReader: () => ({
      read: () => {
        if (reads++ === 0) return Promise.resolve({ done: false, value: encoded(pcmEvent) });
        return new Promise((resolve, reject) => { finishOldRead = completion === 'error' ? () => reject(new Error('Old stream failed')) : () => resolve({ done: false, value: encoded(pcmEvent) }); });
      },
      cancel: async () => {},
    }) } });
    const old = speakText('The old streamed reply.', true);
    const oldResult = old.catch(error => error);
    await tick();
    assert.equal(sources.length, 1);
    state.mode = 'buf';
    globalThis.fetch = async () => response();
    const current = speakText('The current buffered reply.', true);
    await tick();
    const controller = state.controller;
    finishOldRead();
    await oldResult;
    assert.equal(sources.length, 1, 'a late old chunk cannot schedule another source');
    assert.equal(state.controller, controller);
    assert.equal(controller.signal.aborted, false);
    assert.equal(state.ttsSpeaking, true);
    assert.equal(element('audio').paused, false);
    assert.equal(element('stopBtn').disabled, false);
    assert.match(element('ttsStatus').textContent, /Playing buffered/);
    assert.deepEqual(revoked, []);
    element('audio').finish();
    await current;
  });
}

test('stream EOF keeps controls active through scheduled PCM playback and Stop cancels the wait', async () => {
  state.mode = 'sse';
  const sources = pcmAudioContext();
  const body = `data: ${JSON.stringify({ audio: btoa('\x00\x00'.repeat(24000)), sample_rate: 24000 })}\n\n`;
  let reads = 0;
  globalThis.fetch = async () => ({ ok: true, body: { getReader: () => ({
    read: async () => reads++ ? { done: true } : { done: false, value: encoded(body) },
    cancel: async () => {},
  }) } });
  let complete = false;
  const pending = speakText('A queued streamed reply.', false).then(() => { complete = true; });
  await tick();
  assert.equal(complete, false);
  assert.equal(state.ttsSpeaking, true);
  assert.equal(element('stopBtn').disabled, false);
  stopTtsNow();
  await pending;
  assert.equal(complete, true);
  assert.equal(state.activeSources.length, 0);
  assert(sources[0].stopped > 0);
  assert.equal(element('stopBtn').disabled, true);
  assert.equal(element('ttsStatus').textContent, 'Interrupted');
});
