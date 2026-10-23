import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import {
  MAX_PARTIAL_QUEUE_WINDOWS, MAX_PARTIAL_WINDOW_BYTES, processPartialQueue,
  startPartialTranscriptionLoop, startPartialWindowRecorder,
  stopPartialTranscriptionLoop, waitForPartialQueueIdle,
} from '../../Korina/js/partial-queue.js';
import { handleLiveTurn, liveMonitor, stopLive } from '../../Korina/js/live.js';
import { transcribeBlob, transcribePartialBlob } from '../../Korina/js/recorder.js';

const elements = new Map();
const recorders = [];
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', className: '', style: {}, disabled: false,
    classList: { add() {}, remove() {} }, appendChild() {},
  });
  return elements.get(id);
};
class Recorder {
  static isTypeSupported() { return true; }
  constructor() { this.state = 'inactive'; this.deferStop = false; recorders.push(this); }
  start() { this.state = 'recording'; }
  stop() { this.state = 'inactive'; if (!this.deferStop) queueMicrotask(() => this.finishStop()); }
  emit(blob) { this.ondataavailable?.({ data: blob }); }
  finishStop(chunks = []) { for (const chunk of chunks) this.emit(chunk); return this.onstop?.(); }
}
const tick = () => new Promise(setImmediate);
const audio = text => new Blob([text.repeat(3000)], { type: 'audio/webm' });
const partial = text => ({ text, backend: 'faster-whisper', seconds: .1, duration: 1, samples: 16000, sample_rate: 16000, model: 'tiny.en' });
const json = body => ({ ok: true, json: async () => body });
const final = text => new Response(`event: segment\ndata: ${JSON.stringify({ text, index: 1 })}\n\nevent: done\ndata: ${JSON.stringify(partial(text))}\n\n`);

beforeEach(() => {
  elements.clear(); recorders.length = 0;
  globalThis.document = { getElementById: element, createElement: () => ({}) };
  globalThis.MediaRecorder = Recorder;
  globalThis.requestAnimationFrame = () => 1;
  globalThis.cancelAnimationFrame = () => {};
  Object.assign(state, {
    live: true, liveBusy: false, liveSession: 1, agentSession: 0, history: [],
    stream: { getTracks: () => [{ stop() {} }] }, mediaRecorder: null, meterAudioCtx: null,
    audioCtx: null, analyser: null, raf: null, liveLoopId: null, bargeLoopId: null,
    bargeRecorder: null, handlingBarge: false, awaitingAgentPermission: null,
    partialRecorder: null, partialRecorderDone: null, partialController: null,
    partialQueue: [], partialQueueProcessing: false,
    partialCaptureActive: false, partialNeedsFullPass: false, partialFallbackReason: '',
    partialWindowMs: 60_000, partialTimer: null, latestPartialText: '',
    liveSpeechStart: performance.now() - 1000, liveLastVoice: performance.now(),
    liveSilenceStart: 0,
    recChunks: [audio('whole-utterance-')], liveTranscriptionController: null,
    pendingAgentStateReport: '', pendingImportantAgentMessage: '',
  });
  element('finalSttMode').value = 'chunks';
  element('agentEnabled').checked = false;
  globalThis.fetch = async () => { throw new Error('Unexpected transport call'); };
});
afterEach(() => { stopLive(); });

async function captureWindow(blob = audio('window-')) {
  startPartialWindowRecorder();
  const recorder = state.partialRecorder;
  state.liveLastVoice = performance.now();
  recorder.emit(blob);
  recorder.stop();
  await tick();
  return recorder;
}

function turnTransport(onFinal, onChat) {
  return async (url, options) => {
    if (url.startsWith('/api/transcribe/stream')) {
      onFinal?.(options.body.get('audio'));
      return final('The complete recording.');
    }
    if (url === '/api/health') return json({ ok: true, response_llm_load: { ok: true, loaded: true } });
    if (url === '/api/chat') { onChat?.(JSON.parse(options.body)); return json({ reply: '' }); }
    throw new Error(`Unexpected turn request ${url}`);
  };
}

test('partial windows and queue are bounded; overflow falls back to the untouched full recording', async () => {
  let finishOld;
  globalThis.fetch = () => new Promise(resolve => { finishOld = resolve; });
  startPartialTranscriptionLoop();
  await captureWindow(); // The first window is in flight, the remainder wait.
  const controller = state.partialController;
  for (let index = 0; index < MAX_PARTIAL_QUEUE_WINDOWS; index++) await captureWindow();
  assert.equal(state.partialQueue.length, MAX_PARTIAL_QUEUE_WINDOWS);
  await captureWindow();
  assert.equal(state.partialNeedsFullPass, true);
  assert.match(state.partialFallbackReason, /fell behind/);
  assert.equal(state.partialQueue.length, 0);
  assert.equal(controller.signal.aborted, true);
  assert.equal(state.partialCaptureActive, false);
  const original = await new Blob(state.recChunks).text();
  let uploaded;
  let message;
  globalThis.fetch = turnTransport(blob => { uploaded = blob; }, payload => { message = payload.message; });
  state.liveBusy = true;
  await handleLiveTurn();
  assert.equal(await uploaded.text(), original);
  assert.equal(message, 'The complete recording.');
  finishOld(json(partial('This stale partial must not appear.')));
  await tick();
  assert.doesNotMatch(element('transcript').value, /stale partial/);
});

test('an oversized window is discarded before its bytes enter the queue', async () => {
  startPartialTranscriptionLoop();
  state.partialRecorder.emit(new Blob([new Uint8Array(MAX_PARTIAL_WINDOW_BYTES + 1)]));
  await tick();
  assert.equal(state.partialNeedsFullPass, true);
  assert.match(state.partialFallbackReason, /size limit/);
  assert.equal(state.partialQueue.length, 0);
});

test('a cancelled old worker cannot consume a new queue or clear its controller and busy flags', async () => {
  const requests = [];
  globalThis.fetch = (url, options) => new Promise(resolve => { requests.push({ resolve, signal: options.signal }); });
  startPartialTranscriptionLoop();
  await captureWindow(audio('old-'));
  const old = requests[0];
  stopPartialTranscriptionLoop(true);
  startPartialTranscriptionLoop();
  await captureWindow(audio('new-first-'));
  await captureWindow(audio('new-second-'));
  const controller = state.partialController;
  assert.equal(requests.length, 2);
  assert.equal(state.partialQueue.length, 1);
  old.resolve(json(partial('Old text.')));
  await tick();
  assert.equal(state.partialQueueProcessing, true);
  assert.equal(state.partialController, controller);
  assert.equal(state.partialQueue.length, 1);
  assert.equal(state.latestPartialText, '');
  requests[1].resolve(json(partial('New first.')));
  await tick();
  assert.equal(requests.length, 3);
  requests[2].resolve(json(partial('New second.')));
  await waitForPartialQueueIdle();
  assert.equal(state.latestPartialText, 'New first. New second.');
  assert.doesNotMatch(element('transcript').value, /Old text/);
});

test('full-final partial feedback holds queued work during endpoint silence and resumes when speech returns', async () => {
  element('finalSttMode').value = 'full';
  const requests = [];
  globalThis.fetch = (url, options) => new Promise(resolve => { requests.push({ resolve, signal: options.signal }); });
  startPartialTranscriptionLoop();
  await captureWindow(audio('first-'));
  await captureWindow(audio('deferred-'));
  assert.equal(requests.length, 1);
  state.liveSilenceStart = performance.now();
  requests[0].resolve(json(partial('First feedback.')));
  await tick();
  assert.equal(requests.length, 1, 'endpoint silence must not start another native request');
  assert.equal(state.latestPartialText, 'First feedback.', 'already admitted feedback still completes');
  assert.equal(state.partialQueue.length, 1, 'keep the complete deferred window for resumed speech');
  assert.equal(state.partialQueueProcessing, false);
  assert.equal(requests[0].signal.aborted, false);

  state.analyser = { fftSize: 512, getByteTimeDomainData: data => data.fill(200) };
  liveMonitor();
  assert.equal(state.liveSilenceStart, 0);
  assert.equal(requests.length, 2, 'speech resumption admits the held window without waiting for another recording');
  requests[1].resolve(json(partial('Resumed feedback.')));
  await waitForPartialQueueIdle();
  assert.equal(state.latestPartialText, 'First feedback. Resumed feedback.');
  assert.equal(state.partialQueue.length, 0);
});

test('full-final endpoint silence leaves new queued feedback untouched until Stop retires it', async () => {
  element('finalSttMode').value = 'full';
  state.liveSilenceStart = performance.now();
  const blob = audio('complete-deferred-window-');
  state.partialQueue = [{ seq: state.partialSeq, windowId: 1, blob }];
  await processPartialQueue();
  assert.equal(state.partialQueue.length, 1);
  assert.equal(state.partialQueue[0].blob, blob);
  assert.equal(state.partialController, null);
  assert.equal(state.partialQueueProcessing, false);
  stopLive();
  assert.equal(state.partialQueue.length, 0);
  assert.equal(state.partialController, null);
});

test('chunks mode continues draining every complete window during endpoint silence', async () => {
  const uploaded = [];
  globalThis.fetch = async (url, options) => {
    uploaded.push(await options.body.get('audio').text());
    return json(partial(`Feedback ${uploaded.length}.`));
  };
  state.liveSilenceStart = performance.now();
  state.partialQueue = [
    { seq: state.partialSeq, windowId: 1, blob: audio('first-') },
    { seq: state.partialSeq, windowId: 2, blob: audio('second-') },
  ];
  await processPartialQueue();
  assert.deepEqual(uploaded, ['first-'.repeat(3000), 'second-'.repeat(3000)]);
  assert.equal(state.latestPartialText, 'Feedback 1. Feedback 2.');
  assert.equal(state.partialQueue.length, 0);
});

test('a mode edit during chunks finalization cannot strand its queued windows', { timeout: 1000 }, async () => {
  const requests = [];
  const messages = [];
  let fullRequests = 0;
  let pending;
  const turns = turnTransport(() => { fullRequests++; }, payload => messages.push(payload.message));
  globalThis.fetch = (url, options) => {
    if (url.startsWith('/api/transcribe/partial')) {
      return new Promise(resolve => requests.push({ resolve, signal: options.signal }));
    }
    return turns(url, options);
  };
  try {
    startPartialTranscriptionLoop();
    await captureWindow(audio('first-'));
    await captureWindow(audio('second-'));
    assert.equal(requests.length, 1);
    assert.equal(state.partialQueue.length, 1);
    state.liveSilenceStart = performance.now();
    state.liveBusy = true;
    pending = handleLiveTurn();
    await tick();
    assert.equal(state.partialQueueProcessing, true, 'finalization is awaiting the first window');
    assert.deepEqual(messages, []);

    element('finalSttMode').value = 'full';
    requests[0].resolve(json(partial('First window.')));
    await tick();
    assert.equal(requests.length, 2, 'the captured chunks policy must drain its second window');
    assert.equal(state.partialQueueProcessing, true);
    requests[1].resolve(json(partial('Second window.')));
    await pending;
    assert.deepEqual(messages, ['First window. Second window.']);
    assert.equal(fullRequests, 0, 'this turn keeps its already-started chunks finalization');
    assert.equal(state.partialQueue.length, 0);
    assert.equal(state.partialQueueProcessing, false);
    assert.equal(state.liveBusy, false);
    assert.equal(element('finalSttMode').value, 'full', 'the new setting remains for the next turn');
    assert(requests.every(request => !request.signal.aborted));
  } finally {
    stopLive();
    for (const request of requests) request.resolve(json(partial('Retired window.')));
    await pending;
  }
});

test('late final data from an old recorder cannot consume or clear a new recorder window', async () => {
  let uploaded;
  globalThis.fetch = async (url, options) => { uploaded = options.body.get('audio'); return json(partial('Current window.')); };
  startPartialTranscriptionLoop();
  const old = state.partialRecorder;
  old.deferStop = true;
  old.emit(audio('old-initial-'));
  stopPartialTranscriptionLoop(true);
  startPartialTranscriptionLoop();
  const current = state.partialRecorder;
  const timer = state.partialTimer;
  current.emit(audio('current-only-'));
  old.finishStop([audio('old-late-')]);
  assert.equal(state.partialRecorder, current);
  assert.equal(state.partialTimer, timer);
  assert.equal(state.partialQueue.length, 0);
  state.liveLastVoice = performance.now();
  current.stop();
  await tick();
  await waitForPartialQueueIdle();
  assert.equal(await uploaded.text(), 'current-only-'.repeat(3000));
});

test('full-final mode sends the full recording without waiting for a stalled partial worker', { timeout: 1000 }, async () => {
  let finishOld;
  let partialSignal;
  globalThis.fetch = (url, options) => new Promise(resolve => { finishOld = resolve; partialSignal = options.signal; });
  startPartialTranscriptionLoop();
  await captureWindow();
  await captureWindow();
  element('finalSttMode').value = 'full';
  let fullRequests = 0;
  globalThis.fetch = turnTransport(() => { fullRequests++; });
  state.liveBusy = true;
  await handleLiveTurn();
  assert.equal(fullRequests, 1);
  assert.equal(partialSignal.aborted, true);
  finishOld(json(partial('Old queued text.')));
  await tick();
  assert.doesNotMatch(element('transcript').value, /Old queued/);
});

test('chunks mode waits for final recorder events and preserves its independently recorded complete blob', async () => {
  startPartialTranscriptionLoop();
  const recorder = state.partialRecorder;
  recorder.deferStop = true;
  state.liveLastVoice = performance.now();
  state.liveBusy = true;
  let uploaded;
  let chat;
  let fullRequests = 0;
  const turns = turnTransport(() => { fullRequests++; }, payload => { chat = payload.message; });
  globalThis.fetch = async (url, options) => {
    if (url.startsWith('/api/transcribe/partial')) { uploaded = options.body.get('audio'); return json(partial('Final recorder data.')); }
    return turns(url, options);
  };
  const pending = handleLiveTurn();
  await tick();
  assert.equal(uploaded, undefined);
  assert.equal(chat, undefined);
  recorder.finishStop([audio('header-and-final-window-')]);
  await pending;
  assert.equal(await uploaded.text(), 'header-and-final-window-'.repeat(3000));
  assert.equal(chat, 'Final recorder data.');
  assert.equal(fullRequests, 0);
});

test('a failed window cannot silently turn a partial prefix into the final response prompt', async () => {
  state.latestPartialText = 'Only the beginning.';
  state.partialQueue = [{ blob: audio('failed-'), seq: state.partialSeq, windowId: 2 }];
  globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Inference unavailable' }) });
  await processPartialQueue();
  assert.equal(state.partialNeedsFullPass, true);
  let fullRequests = 0;
  let chat;
  globalThis.fetch = turnTransport(() => { fullRequests++; }, payload => { chat = payload.message; });
  state.liveBusy = true;
  await handleLiveTurn();
  assert.equal(fullRequests, 1);
  assert.equal(chat, 'The complete recording.');
});

test('stale partial responses cannot update transcript, status, backend label, or a new controller', async () => {
  let finish;
  globalThis.fetch = async () => ({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
  const pending = transcribePartialBlob(audio('old-'), state.partialSeq);
  await tick();
  state.partialSeq++;
  const currentController = new AbortController();
  state.partialController = currentController;
  element('transcript').value = 'New transcript.';
  element('sttStatus').textContent = 'New status.';
  element('sttBackendHealth').textContent = 'New backend.';
  finish(partial('Stale response.'));
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(element('transcript').value, 'New transcript.');
  assert.equal(element('sttStatus').textContent, 'New status.');
  assert.equal(element('sttBackendHealth').textContent, 'New backend.');
  assert.equal(state.partialController, currentController);
});

test('stale full SSE events and backend reports cannot repopulate a cleared transcript', async () => {
  let finish;
  let current = true;
  globalThis.fetch = async () => ({ ok: true, body: { getReader: () => ({ read: () => new Promise(resolve => { finish = resolve; }), cancel() {} }) } });
  const pending = transcribeBlob(audio('old-'), { isCurrent: () => current });
  await tick();
  current = false;
  element('transcript').value = 'New transcript.';
  element('sttStatus').textContent = 'New status.';
  element('sttBackendHealth').textContent = 'New backend.';
  const events = 'event: status\ndata: {"message":"old status"}\n\nevent: segment\ndata: {"text":"Old text.","index":1}\n\nevent: done\ndata: {"text":"Old text.","backend":"old-backend"}\n\n';
  finish({ done: false, value: new TextEncoder().encode(events) });
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(element('transcript').value, 'New transcript.');
  assert.equal(element('sttStatus').textContent, 'New status.');
  assert.equal(element('sttBackendHealth').textContent, 'New backend.');
});

test('Stop Live aborts a full transcription and leaves a new session untouched by its late response', async () => {
  element('finalSttMode').value = 'full';
  state.liveBusy = true;
  let finish;
  let signal;
  globalThis.fetch = (url, options) => new Promise(resolve => { finish = resolve; signal = options.signal; });
  const pending = handleLiveTurn();
  await tick();
  stopLive();
  assert.equal(signal.aborted, true);
  state.live = true;
  state.liveSession++;
  element('transcript').value = 'The new session.';
  element('sttStatus').textContent = 'New session listening.';
  finish(final('Old session transcript.'));
  await pending;
  assert.equal(element('transcript').value, 'The new session.');
  assert.equal(element('sttStatus').textContent, 'New session listening.');
  assert.equal(state.mediaRecorder, null, 'old cleanup must not restart capture in the new session');
});
