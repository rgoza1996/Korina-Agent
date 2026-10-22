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
