import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { cancelBargeIn, monitorBargeIn, startBargeInCapture } from '../../Korina/js/barge-in.js';
import { stopLive } from '../../Korina/js/live.js';
import { interruptConverse } from '../../Korina/js/agent-ui.js';
import { stopTtsNow } from '../../Korina/js/speech.js';

const elements = new Map();
const frames = new Map();
const recorders = [];
let nextFrame = 0;
const tick = () => new Promise(setImmediate);

function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', className: '', style: {}, children: [],
    classList: { add() {}, remove() {} },
    appendChild(child) { this.children.push(child); },
  });
  return elements.get(id);
}

class AudioElement extends EventTarget {
  paused = true;
  duration = 2;
  play() { this.paused = false; this.dispatchEvent(new Event('playing')); return Promise.resolve(); }
  pause() { this.paused = true; }
  removeAttribute(name) { if (name === 'src') this.src = ''; }
  load() {}
  finish() { this.dispatchEvent(new Event('ended')); }
}

class Recorder {
  static isTypeSupported() { return true; }
  constructor() { this.state = 'inactive'; recorders.push(this); }
  start() { this.state = 'recording'; }
  stop() {
    if (this.state === 'inactive') return this.finished;
    this.state = 'inactive';
    this.finished = Promise.resolve(this.onstop?.());
    return this.finished;
  }
  emit(text) { this.ondataavailable?.({ data: new Blob([text], { type: 'audio/webm' }) }); }
}

function transcription(text) {
  let read = false;
  const body = `event: segment\ndata: ${JSON.stringify({ text, index: 1 })}\n\nevent: done\ndata: ${JSON.stringify({ text, backend: 'whisper', segments: 1 })}\n\n`;
  return { ok: true, body: { getReader: () => ({
    read: async () => {
      if (read) return { done: true };
      read = true;
      return { done: false, value: new TextEncoder().encode(body) };
    },
    cancel: async () => {},
  }) } };
}

function captureInterruption() {
  state.ttsSpeaking = true;
  startBargeInCapture();
  const recorder = state.bargeRecorder;
  recorder.emit('x'.repeat(3000));
  state.handlingBarge = true;
  state.interruptContext = 'Already spoken: the demo is ready.';
  state.ttsSpeaking = false;
  return recorder.stop();
}

beforeEach(() => {
  cancelBargeIn();
  elements.clear(); frames.clear(); recorders.length = 0;
  globalThis.document = { getElementById: element, createElement: () => ({}) };
  globalThis.MediaRecorder = Recorder;
  globalThis.requestAnimationFrame = callback => { const id = ++nextFrame; frames.set(id, callback); return id; };
  globalThis.cancelAnimationFrame = id => frames.delete(id);
  elements.set('audio', new AudioElement());
  element('voice').value = 'af_heart'; element('speed').value = '1';
  element('ttsBaseUrl').value = 'http://speech.test'; element('ackEnabled').value = 'off';
  element('agentEnabled').checked = false;
  const stream = { active: true, getTracks: () => [{ stop() { stream.active = false; } }] };
  Object.assign(state, {
    live: true, liveSession: 1, liveBusy: true, agentSession: 1, agentResetBlocked: false,
    handlingBarge: false, bargeRecorder: null, bargeChunks: [], bargeLoopId: null,
    stream, mediaRecorder: null, recChunks: [], meterAudioCtx: null, analyser: null, raf: null,
    partialRecorder: null, partialRecorderDone: null, partialCaptureActive: false,
    partialTimer: null, partialQueue: [], partialSeq: 0, partialController: null,
    partialInFlight: false, partialQueueProcessing: false, partialNeedsFullPass: false,
    liveLoopId: null, liveTranscriptionController: null,
    audioCtx: { state: 'running', currentTime: 10, close: async () => {} }, gain: {},
    ttsSpeaking: false, mode: 'buf', controller: null, activeSources: [], ackSource: null,
    ackBuffersByTag: {}, ackFiles: [], history: [], agentTurnCount: 0,
    pendingAgentStateReport: '', pendingAgentReportVersion: 0,
    pendingImportantAgentMessage: '', awaitingAgentPermission: null, agentInterruptInProgress: false,
    agentInterruptCooldownUntil: 0, currentTtsEstimatedEnd: 0,
  });
});

afterEach(() => { cancelBargeIn(); stopLive(); stopTtsNow(); });

for (const cancellation of ['Stop Live', 'Clear Session']) {
  for (const outcome of ['response', 'failure']) {
    test(`${cancellation} discards a late barge STT ${outcome} without changing replacement state`, async () => {
      let finish, fail, signal;
      globalThis.fetch = (url, options) => {
        assert.match(url, /^\/api\/transcribe\/stream/);
        signal = options.signal;
        return new Promise((resolve, reject) => { finish = resolve; fail = reject; });
      };
      const pending = captureInterruption();
      if (cancellation === 'Stop Live') stopLive();
      else { state.agentSession++; cancelBargeIn(); }
      state.live = true; state.liveSession++;
      state.handlingBarge = true; state.liveBusy = true; state.ttsSpeaking = true;
      const replacementChunks = [new Blob(['new capture'])];
      state.bargeChunks = replacementChunks;
      element('transcript').value = 'Fresh transcript.';
      element('text').value = 'Fresh reply.';
      element('sttStatus').textContent = 'Fresh session status.';
      const recorderCount = recorders.length;
      if (outcome === 'response') finish(transcription('Old interrupted turn.'));
      else fail(new Error('Old STT connection failed'));
      await pending;
      assert.equal(signal.aborted, true);
      assert.equal(element('transcript').value, 'Fresh transcript.');
      assert.equal(element('text').value, 'Fresh reply.');
      assert.equal(element('sttStatus').textContent, 'Fresh session status.');
      assert.deepEqual(state.history, []);
      assert.equal(state.handlingBarge, true);
      assert.equal(state.liveBusy, true);
      assert.equal(state.ttsSpeaking, true);
      assert.equal(state.bargeChunks, replacementChunks);
      assert.equal(recorders.length, recorderCount, 'no old live recorder restart');
    });
  }
}

test('old recorder data, stop, and animation callbacks cannot use a replacement capture', async () => {
  state.ttsSpeaking = true;
  startBargeInCapture();
  const old = state.bargeRecorder;
  const oldFrame = frames.get(state.bargeLoopId);
  cancelBargeIn();
  old.state = 'inactive';
  startBargeInCapture();
  const current = state.bargeRecorder;
  current.emit('current capture');
  const chunks = state.bargeChunks;
  state.handlingBarge = true;
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error('old callback must not submit'); };
  old.emit('old capture');
  await old.onstop();
  oldFrame();
  assert.equal(state.bargeRecorder, current);
  assert.equal(state.bargeChunks, chunks);
  assert.equal(await chunks[0].text(), 'current capture');
  assert.equal(chunks.length, 1);
  assert.equal(state.handlingBarge, true);
  assert.equal(requests, 0);
});

test('a current STT failure preserves its error and resumes capture once', async () => {
  globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: 'STT model unavailable' }) });
  await captureInterruption();
  assert.equal(element('sttStatus').className, 'status bad');
  assert.match(element('sttStatus').textContent, /Interruption error: STT model unavailable/);
  assert.equal(state.handlingBarge, false);
  assert.equal(state.liveBusy, false);
  assert.equal(recorders.length, 3, 'barge recorder plus one normal and one partial recorder');
  assert.equal(state.mediaRecorder.state, 'recording');
});

test('Stop Live during barge chat prevents the late reply and speech request', async () => {
  let reply;
  const requests = [];
  element('transcript').value = 'Earlier transcript.';
  globalThis.fetch = async url => {
    requests.push(url);
    if (url.startsWith('/api/transcribe/stream')) return transcription('Interruption turn.');
    assert.equal(url, '/api/chat');
    return new Promise(resolve => { reply = resolve; });
  };
  const pending = captureInterruption();
  await tick();
  assert.equal(element('transcript').value, 'Earlier transcript.\nInterruption turn.');
  stopLive();
  element('text').value = 'Fresh reply.';
  element('sttStatus').textContent = 'Stopped by the user.';
  reply({ ok: true, json: async () => ({ reply: 'Do not speak this old reply.' }) });
  await pending;
  assert.equal(element('text').value, 'Fresh reply.');
  assert.equal(element('sttStatus').textContent, 'Stopped by the user.');
  assert.equal(state.history.some(turn => turn.role === 'assistant'), false);
  assert.equal(requests.length, 2);
  assert.equal(recorders.length, 1);
});

function installConversation() {
  globalThis.fetch = async url => {
    if (url.startsWith('/api/transcribe/stream')) return transcription('Interruption turn.');
    if (url === '/api/chat') return { ok: true, json: async () => ({ reply: 'A current reply.' }) };
    assert.equal(url, 'http://speech.test/v1/audio/speech');
    return { ok: true, blob: async () => new Blob(['fixture audio']) };
  };
}

test('a current interruption reply stays interruptible and resumes normal recording after playback', async () => {
  installConversation();
  const pending = captureInterruption();
  await tick();
  assert.equal(element('audio').paused, false);
  assert.equal(state.handlingBarge, false);
  assert.equal(state.ttsSpeaking, true);
  assert.equal(recorders.length, 2, 'reply has its own barge capture');
  element('audio').finish();
  await pending;
  assert.equal(state.liveBusy, false);
  assert.equal(state.ttsSpeaking, false);
  assert.equal(recorders.length, 4, 'one normal and one partial recorder resume');
  assert.deepEqual(state.history.map(turn => turn.role), ['user', 'assistant']);
  assert.equal(element('text').value, 'A current reply.');
});

test('a second interruption invalidates the first reply cleanup and keeps its own capture', async () => {
  installConversation();
  const pending = captureInterruption();
  await tick();
  const secondRecorder = state.bargeRecorder;
  secondRecorder.emit('second interruption audio');
  const secondChunks = state.bargeChunks;
  state.analyser = { fftSize: 512, getByteTimeDomainData: data => data.fill(255) };
  state.bargeSpeechStart = performance.now() - 300;
  monitorBargeIn();
  await pending;
  assert.equal(state.handlingBarge, true);
  assert.equal(state.bargeRecorder, secondRecorder);
  assert.equal(state.bargeChunks, secondChunks);
  assert.equal(state.mediaRecorder, null);
  assert.equal(recorders.length, 2, 'first reply cannot restart the ordinary live recorder');
  assert.equal(state.agentTurnCount, 1);
});

test('cleared agent interrupt speech cannot clear a newer interrupt owner or set its cooldown', async () => {
  state.live = false;
  globalThis.fetch = async () => ({ ok: true, blob: async () => new Blob(['fixture audio']) });
  interruptConverse('Old permission request.', true);
  await tick();
  stopTtsNow();
  state.agentSession++;
  state.agentInterruptInProgress = false;
  interruptConverse('New permission request.', true);
  await tick();
  assert.equal(state.agentInterruptInProgress, true);
  assert.equal(state.agentInterruptCooldownUntil, 0);
  element('audio').finish();
  await tick();
  assert.equal(state.agentInterruptInProgress, false);
  assert.ok(state.agentInterruptCooldownUntil > 0);
});
