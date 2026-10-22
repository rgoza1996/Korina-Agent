import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import {
  handleLiveTurn, liveMonitor, responseLlmPreflight, startLive, startLiveRecorder, stopLive,
} from '../../Korina/js/live.js';
import { makeRecorder, releaseMic, setupMic } from '../../Korina/js/recorder.js';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const classes = new Set();
    elements.set(id, {
      value: '', textContent: '', className: '', disabled: false, style: {}, clientWidth: 200,
      classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
      appendChild() {},
    });
  }
  return elements.get(id);
}
const frames = new Map();
let nextFrame = 0;
const recorders = [];
const contexts = [];

class AudioContextStub {
  constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; contexts.push(this); }
  createMediaStreamSource() { return { connect() {} }; }
  createAnalyser() { return { fftSize: 512, frequencyBinCount: 256, getByteTimeDomainData: data => data.fill(128) }; }
  createGain() { return { connect() {} }; }
  close() { this.state = 'closed'; return Promise.resolve(); }
}
class MediaRecorderStub {
  static isTypeSupported() { return true; }
  constructor(stream) { this.stream = stream; this.state = 'inactive'; recorders.push(this); }
  start() { this.state = 'recording'; }
  stop() { this.state = 'inactive'; if (this.onstop) this.onstop(); }
}
function streamStub() {
  const stream = { active: true, stopped: 0 };
  stream.getTracks = () => [{ stop() { stream.stopped++; stream.active = false; } }];
  return stream;
}

beforeEach(() => {
  elements.clear(); frames.clear(); recorders.length = 0; contexts.length = 0;
  globalThis.document = { getElementById: element, createElement: () => ({ style: {} }) };
  globalThis.window = { AudioContext: AudioContextStub, isSecureContext: true };
  globalThis.MediaRecorder = MediaRecorderStub;
  globalThis.getComputedStyle = () => ({ paddingLeft: '0', paddingRight: '0' });
  globalThis.requestAnimationFrame = callback => { const id = ++nextFrame; frames.set(id, callback); return id; };
  globalThis.cancelAnimationFrame = id => frames.delete(id);
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
    mediaDevices: { getUserMedia: async () => streamStub() },
  } });
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ acks: [] }) });
  Object.assign(state, {
    live: false, liveSession: 0, liveBusy: false, handlingBarge: false,
    stream: null, meterAudioCtx: null, analyser: null, mediaRecorder: null,
    partialRecorder: null, partialTimer: null, partialQueue: [], partialQueueProcessing: false,
    partialController: null, liveLoopId: null, bargeLoopId: null, raf: null,
    bargeRecorder: null, audioCtx: null, ackSource: null, activeSources: [],
    controller: null, recChunks: [], latestPartialText: '', ackFiles: [], ackBuffersByTag: {},
    pendingImportantAgentMessage: '', awaitingAgentPermission: null,
  });
});
afterEach(() => { stopLive(); releaseMic(); });

for (const [name, block, ok] of [
  ['offline endpoint', { ok: false, loaded: false, error: 'connection refused' }, false],
  ['unloaded model', { ok: true, loaded: false }, false],
  ['loaded model', { ok: true, loaded: true }, true],
]) {
  test(`response preflight uses runtime load truth for ${name}`, async () => {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, response_llm_load: block }) });
    assert.equal((await responseLlmPreflight()).ok, ok);
  });
}

test('permission denial restores controls and leaves no microphone or live loop', async () => {
  navigator.mediaDevices.getUserMedia = async () => { throw Object.assign(new Error('denied'), { name: 'NotAllowedError' }); };
  await assert.rejects(startLive(), /Microphone permission denied/);
  assert.equal(state.live, false);
  assert.equal(element('recBtn').disabled, false);
  assert.equal(element('liveBtn').classList.contains('on'), false);
  assert.equal(state.stream, null);
  assert.equal(frames.size, 0);
  assert.equal(recorders.length, 0);
  assert.match(element('sttStatus').textContent, /Allow microphone access/);
});

test('missing media APIs report how to enable microphone access', async () => {
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {} });
  await assert.rejects(setupMic(), /Microphone unavailable.*HTTPS or http:\/\/localhost/);
});

test('microphone setup shares one permission request and reuses its live stream', async () => {
  let resolve;
  let requests = 0;
  navigator.mediaDevices.getUserMedia = () => { requests++; return new Promise(done => { resolve = done; }); };
  const first = setupMic();
  const second = setupMic();
  assert.equal(first, second);
  const stream = streamStub();
  resolve(stream);
  assert.equal(await first, true);
  assert.equal(await setupMic(), true);
  assert.equal(requests, 1);
  assert.equal(contexts.length, 1);
  releaseMic();
  assert.equal(stream.stopped, 1);
  assert.equal(contexts[0].state, 'closed');
});

test('stopping during a permission prompt discards the late stream without arming live mode', async () => {
  let resolve;
  navigator.mediaDevices.getUserMedia = () => new Promise(done => { resolve = done; });
  const starting = startLive();
  stopLive();
  const stream = streamStub();
  resolve(stream);
  await starting;
  assert.equal(stream.stopped, 1);
  assert.equal(state.live, false);
  assert.equal(state.stream, null);
  assert.equal(recorders.length, 0);
  assert.equal(frames.size, 0);
});

test('a failed audio context releases an acquired stream and supports retry', async () => {
  const stream = streamStub();
  navigator.mediaDevices.getUserMedia = async () => stream;
  window.AudioContext = class { constructor() { throw new Error('audio initialization failed'); } };
  await assert.rejects(setupMic(), /audio initialization failed/);
  assert.equal(stream.stopped, 1);
  assert.equal(state.stream, null);
  window.AudioContext = AudioContextStub;
  navigator.mediaDevices.getUserMedia = async () => streamStub();
  assert.equal(await setupMic(), true);
});

test('restarting live recorders and monitors cannot duplicate active loops', async () => {
  await startLive();
  assert.equal(recorders.length, 2, 'one turn recorder and one partial recorder');
  const existing = [...frames.keys()];
  startLiveRecorder();
  liveMonitor();
  assert.equal(recorders.length, 2);
  assert.equal(frames.size, 2, 'one meter frame and one live monitor frame');
  assert.equal(frames.has(existing.at(-1)), false, 'the prior monitor frame is replaced');
  assert.throws(() => makeRecorder(() => {}), /already recording/);
});

test('an unavailable LLM preserves its error while restarting capture exactly once', async () => {
  state.live = true; state.liveBusy = true; state.liveSession = 1;
  state.stream = streamStub();
  state.recChunks = [new Blob(['x'.repeat(3000)])];
  state.latestPartialText = 'Hello Korina.';
  globalThis.fetch = async url => {
    assert.equal(url, '/api/health');
    return { ok: true, json: async () => ({ ok: true, response_llm_load: { ok: false, loaded: false, error: 'connection refused' } }) };
  };
  await handleLiveTurn();
  assert.equal(element('sttStatus').className, 'status bad');
  assert.match(element('sttStatus').textContent, /connection refused/);
  assert.equal(state.liveBusy, false);
  assert.equal(recorders.length, 2, 'restart creates one turn and one partial recorder');
  assert.equal(frames.size, 1, 'restart creates a single live monitor');
});

test('a tiny clip resumes with one recorder pair and retains its explanation', async () => {
  state.live = true; state.liveBusy = true; state.stream = streamStub();
  state.recChunks = [new Blob(['tiny'])];
  await handleLiveTurn();
  assert.equal(recorders.length, 2);
  assert.equal(frames.size, 1);
  assert.match(element('sttStatus').textContent, /Ignored tiny\/no-speech clip/);
});

test('a late permission result from an old session cannot replace a newly started microphone', async () => {
  const pending = [];
  navigator.mediaDevices.getUserMedia = () => new Promise(resolve => pending.push(resolve));
  const oldStart = startLive();
  stopLive();
  const newStart = startLive();
  const current = streamStub();
  pending[1](current);
  await newStart;
  const old = streamStub();
  pending[0](old);
  await oldStart;
  assert.equal(old.stopped, 1);
  assert.equal(current.stopped, 0);
  assert.equal(state.stream, current);
  assert.equal(state.live, true);
  assert.equal(recorders.length, 2);
  assert.equal(frames.size, 2);
});

for (const [name, reply, message] of [
  ['empty reply', { ok: true, json: async () => ({ reply: '' }) }, /returned an empty reply/],
  ['failed chat', { ok: false, json: async () => ({ detail: 'model crashed' }) }, /model crashed/],
]) {
  test(`live mode retains its ${name} error after resuming listening`, async () => {
    state.live = true; state.liveBusy = true; state.liveSession = 1;
    state.stream = streamStub(); state.history = [];
    state.recChunks = [new Blob(['x'.repeat(3000)])];
    state.latestPartialText = 'Hello Korina.';
    globalThis.fetch = async url => {
      if (url === '/api/health') return { ok: true, json: async () => ({ ok: true, response_llm_load: { ok: true, loaded: true } }) };
      assert.equal(url, '/api/chat');
      return reply;
    };
    await handleLiveTurn();
    assert.equal(element('sttStatus').className, 'status bad');
    assert.match(element('sttStatus').textContent, message);
    assert.equal(recorders.length, 2);
    assert.equal(frames.size, 1);
  });
}

test('a recorder failure during live restart restores controls instead of leaving an armed loop', async () => {
  state.live = true; state.liveBusy = true; state.stream = streamStub();
  state.recChunks = [new Blob(['tiny'])];
  globalThis.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor() { throw new Error('device disconnected'); }
  };
  await handleLiveTurn();
  assert.equal(state.live, false);
  assert.equal(element('recBtn').disabled, false);
  assert.equal(frames.size, 0);
  assert.equal(state.stream, null);
  assert.match(element('sttStatus').textContent, /Microphone restart failed: device disconnected/);
});
