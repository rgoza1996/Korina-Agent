import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { startLiveRecorder, stopLive } from '../../Korina/js/live.js';
import { startBargeInCapture } from '../../Korina/js/barge-in.js';
import { makeRecorder } from '../../Korina/js/recorder.js';

class Clock {
  time = 1000;
  timers = new Map();
  serial = 0;
  now = () => this.time;
  setTimeout = (callback, delay) => { const id = ++this.serial; this.timers.set(id, { callback, at: this.time + delay }); return id; };
  clearTimeout = id => this.timers.delete(id);
  tick(ms) {
    const end = this.time + ms;
    while (true) {
      const next = [...this.timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      this.time = next[1].at;
      this.timers.delete(next[0]); next[1].callback();
    }
    this.time = end;
  }
}
class Recorder {
  static all = [];
  static failNext = false;
  static isTypeSupported() { return true; }
  constructor() {
    if (Recorder.failNext) { Recorder.failNext = false; throw new Error('Microphone disconnected'); }
    this.state = 'inactive'; Recorder.all.push(this);
  }
  start(timeslice) { this.state = 'recording'; this.timeslice = timeslice; }
  stop() {
    this.state = 'inactive';
    if (this.finalData) this.emit(this.finalData);
    this.finished = Promise.resolve(this.onstop?.());
    return this.finished;
  }
  emit(text) { this.ondataavailable?.({ data: new Blob([text], { type: 'audio/webm' }) }); }
}
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', className: '', style: {}, classList: { add() {}, remove() {} },
    children: [], appendChild(child) { this.children.push(child); },
  });
  return elements.get(id);
};
const nativePerformance = globalThis.performance;
const nativeSetTimeout = globalThis.setTimeout;
const nativeClearTimeout = globalThis.clearTimeout;
const tick = () => new Promise(setImmediate);
let clock;

beforeEach(() => {
  clock = new Clock();
  Object.defineProperty(globalThis, 'performance', { configurable: true, value: { now: clock.now } });
  globalThis.setTimeout = clock.setTimeout; globalThis.clearTimeout = clock.clearTimeout;
  globalThis.MediaRecorder = Recorder; Recorder.all.length = 0; Recorder.failNext = false;
  elements.clear();
  globalThis.document = { getElementById: element, createElement: () => ({}) };
  globalThis.requestAnimationFrame = () => 1; globalThis.cancelAnimationFrame = () => {};
  element('agentEnabled').checked = false; element('ackEnabled').value = 'off';
  const stream = { active: true, getTracks: () => [{ stop() { stream.active = false; } }] };
  Object.assign(state, {
    audioLimits: { max_duration_seconds: 1, max_upload_bytes: 1000000 }, audioLimitsError: '',
    live: true, liveSession: 1, agentSession: 1, liveBusy: false, stream,
    liveSpeechStart: 0, liveLastVoice: 0, vadSpeechFrames: 0, liveLoopId: null,
    mediaRecorder: null, recChunks: [], meterAudioCtx: null, analyser: null, raf: null,
    liveTranscriptionController: null, partialRecorder: null, partialRecorderDone: null,
    partialController: null, partialTimer: null, partialQueue: [], partialCaptureActive: false,
    partialQueueProcessing: false, partialInFlight: false, partialNeedsFullPass: false, partialWindowMs: 1800,
    bargeRecorder: null, bargeChunks: [], bargeLoopId: null, handlingBarge: false,
    ttsSpeaking: false, controller: null, activeSources: [], audioCtx: null, ackSource: null,
    awaitingAgentPermission: null, pendingAgentStateReport: '', pendingImportantAgentMessage: '', history: [],
  });
  globalThis.fetch = async () => { throw new Error('Silent rotation must not call a model'); };
});
afterEach(async () => {
  stopLive();
  await tick();
  assert.equal(clock.timers.size, 0, 'Stop releases every capture and partial timer');
  Object.defineProperty(globalThis, 'performance', { configurable: true, value: nativePerformance });
  globalThis.setTimeout = nativeSetTimeout; globalThis.clearTimeout = nativeClearTimeout;
});

function installTurn() {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.startsWith('/api/transcribe/stream')) return new Response('event: done\ndata: {"text":"The bounded turn.","backend":"whisper"}\n\n');
    if (url === '/api/health') return { ok: true, json: async () => ({ ok: true, response_llm_load: { ok: true, loaded: true } }) };
    if (url === '/api/chat') return { ok: true, json: async () => ({ reply: '' }) };
    throw new Error(`Unexpected capture request ${url}`);
  };
  return requests;
}

test('manual capture returns intact final data and limit metadata using the shared deadline', async () => {
  state.live = false;
  let info;
  makeRecorder(metadata => { info = metadata; });
  const recorder = state.mediaRecorder;
  recorder.emit('header'); recorder.finalData = 'final';
  assert.equal(recorder.timeslice, 500);
  clock.tick(900);
  await recorder.finished;
  assert.equal(info.limitReached.reason, 'duration');
  assert.equal(await new Blob(state.recChunks).text(), 'headerfinal');
});

test('silent live capture rotates a complete file without uploading or releasing the microphone', async () => {
  startLiveRecorder();
  const recorder = state.mediaRecorder;
  const chunks = state.recChunks;
  recorder.emit('silent data');
  clock.tick(900);
  await recorder.finished;
  assert.notEqual(state.mediaRecorder, recorder);
  assert.equal(state.mediaRecorder.state, 'recording');
  assert.equal(state.mediaRecorder.timeslice, 500);
  assert.equal(chunks.length, 0);
  assert.equal(state.live, true);
  assert.equal(state.stream.active, true);
  assert.equal(state.liveBusy, false);
});

test('a live speech deadline uploads the intact file through the normal full-turn path', async () => {
  const requests = installTurn();
  startLiveRecorder();
  const recorder = state.mediaRecorder;
  recorder.emit('h'.repeat(3000)); recorder.finalData = 'tail';
  state.liveSpeechStart = clock.now() - 100; state.liveLastVoice = clock.now(); state.vadSpeechFrames = 8;
  clock.tick(900);
  await recorder.finished;
  const upload = requests.find(request => request.url.startsWith('/api/transcribe/stream'));
  assert.equal(await upload.options.body.get('audio').text(), 'h'.repeat(3000) + 'tail');
  assert.match(element('log').children.map(child => child.innerHTML).join(''), /Recording limit reached/);
  assert.equal(state.live, true);
  assert.equal(state.mediaRecorder.state, 'recording');
});

test('failed silent live rotation releases the microphone and exposes the restart failure', async () => {
  startLiveRecorder();
  const recorder = state.mediaRecorder;
  Recorder.failNext = true;
  clock.tick(900);
  await recorder.finished;
  assert.equal(state.live, false);
  assert.equal(state.stream, null);
  assert.equal(state.mediaRecorder, null);
  assert.equal(element('sttStatus').className, 'status bad');
  assert.match(element('sttStatus').textContent, /Microphone restart failed.*disconnected/);
});

test('silent barge capture rotates while TTS continues and clears the previous file', async () => {
  state.ttsSpeaking = true; state.liveBusy = true;
  startBargeInCapture();
  const recorder = state.bargeRecorder;
  const chunks = state.bargeChunks;
  recorder.emit('silent listening');
  clock.tick(900);
  await recorder.finished;
  assert.notEqual(state.bargeRecorder, recorder);
  assert.equal(state.bargeRecorder.timeslice, 500);
  assert.equal(state.bargeRecorder.state, 'recording');
  assert.equal(chunks.length, 0);
  assert.equal(state.ttsSpeaking, true);
  assert.equal(state.live, true);
});

test('failed silent barge rotation stops live mode with a visible capture error', async () => {
  state.ttsSpeaking = true; state.liveBusy = true;
  startBargeInCapture();
  const recorder = state.bargeRecorder;
  Recorder.failNext = true;
  clock.tick(900);
  await recorder.finished;
  assert.equal(state.live, false);
  assert.equal(state.stream, null);
  assert.equal(element('sttStatus').className, 'status bad');
  assert.match(element('sttStatus').textContent, /Interruption recording failed.*disconnected/);
});

test('an active interruption deadline transcribes its complete segment and resumes live capture', async () => {
  const requests = installTurn();
  state.ttsSpeaking = true; state.liveBusy = true;
  startBargeInCapture();
  const recorder = state.bargeRecorder;
  recorder.emit('interruption'.repeat(300)); recorder.finalData = 'tail';
  state.handlingBarge = true; state.ttsSpeaking = false;
  clock.tick(900);
  await recorder.finished;
  const upload = requests.find(request => request.url.startsWith('/api/transcribe/stream'));
  assert.equal(await upload.options.body.get('audio').text(), 'interruption'.repeat(300) + 'tail');
  assert.match(element('log').children.map(child => child.innerHTML).join(''), /Recording limit reached/);
  assert.equal(state.handlingBarge, false);
  assert.equal(state.liveBusy, false);
  assert.equal(state.mediaRecorder.state, 'recording');
});
