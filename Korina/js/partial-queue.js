// Queue complete, independently decodable partial windows within one capture epoch.

import { state } from "./state.js";
import { $ } from "./dom.js";
import { transcribePartialBlob } from "./recorder.js";
import { finalSttMode } from "./settings-ui.js";

export const MAX_PARTIAL_QUEUE_WINDOWS = 4;
export const MAX_PARTIAL_WINDOW_BYTES = 2 * 1024 * 1024;
let activeWorker = null;

function requireFullTranscript(reason) {
  if (state.partialNeedsFullPass) return;
  state.partialNeedsFullPass = true;
  state.partialFallbackReason = reason;
  stopPartialTranscriptionLoop(true);
  $('transcriptInfo').textContent = `${reason} The complete recording will be transcribed at the end of this turn.`;
}

export function startPartialTranscriptionLoop() {
  stopPartialTranscriptionLoop(true);
  state.partialWindowIndex = 0;
  state.latestPartialText = '';
  state.partialNeedsFullPass = false;
  state.partialFallbackReason = '';
  state.partialCaptureActive = true;
  startPartialWindowRecorder();
}

export function startPartialWindowRecorder() {
  if (!state.live || state.liveBusy || !state.stream || !state.partialCaptureActive) return;
  if (state.partialRecorder && state.partialRecorder.state !== 'inactive') return;
  let finishStop;
  let recorder;
  let timer;
  let done;
  try {
    const chunks = [];
    let bytes = 0;
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    const windowId = ++state.partialWindowIndex;
    const seq = state.partialSeq;
    const startedAt = performance.now();
    recorder = new MediaRecorder(state.stream, { mimeType: mime });
    done = new Promise(resolve => { finishStop = resolve; });
    state.partialRecorder = recorder;
    state.partialRecorderDone = done;
    recorder.ondataavailable = event => {
      if (!event.data.size || seq !== state.partialSeq) return;
      bytes += event.data.size;
      if (bytes > MAX_PARTIAL_WINDOW_BYTES) {
        requireFullTranscript('A partial audio window exceeded its size limit.');
        return;
      }
      chunks.push(event.data);
    };
    recorder.onstop = () => {
      clearTimeout(timer);
      if (state.partialTimer === timer) state.partialTimer = null;
      if (state.partialRecorder === recorder) state.partialRecorder = null;
      try {
        if (!state.live || seq !== state.partialSeq || !state.liveSpeechStart || state.liveLastVoice < startedAt) return;
        const blob = new Blob(chunks, { type: chunks[0]?.type || mime });
        if (blob.size < 2500) {
          requireFullTranscript('A speech window was too short for partial transcription.');
          return;
        }
        if (state.partialQueue.length >= MAX_PARTIAL_QUEUE_WINDOWS) {
          requireFullTranscript('Partial transcription fell behind the microphone.');
          return;
        }
        state.partialQueue.push({ blob, seq, windowId });
        $('transcriptInfo').textContent = `Queued partial window ${windowId}; queue depth ${state.partialQueue.length}.`;
        processPartialQueue();
      } finally {
        if (state.partialRecorderDone === done) state.partialRecorderDone = null;
        finishStop();
        if (state.live && !state.liveBusy && state.partialCaptureActive && seq === state.partialSeq) {
          setTimeout(() => { if (seq === state.partialSeq) startPartialWindowRecorder(); }, 0);
        }
      }
    };
    recorder.onerror = () => {
      if (seq === state.partialSeq) requireFullTranscript('Partial audio recording failed.');
      finishStop();
    };
    recorder.start(500);
    timer = setTimeout(() => { if (recorder.state !== 'inactive') recorder.stop(); }, state.partialWindowMs);
    state.partialTimer = timer;
  } catch (error) {
    if (state.partialRecorder === recorder) state.partialRecorder = null;
    if (state.partialRecorderDone === done) state.partialRecorderDone = null;
    finishStop?.();
    requireFullTranscript(`Partial recording is unavailable: ${error.message}`);
  }
}

export async function processPartialQueue() {
  if (activeWorker || state.partialNeedsFullPass || !state.partialQueue.length) return;
  const worker = { seq: state.partialSeq };
  activeWorker = worker;
  state.partialQueueProcessing = true;
  const current = () => activeWorker === worker && worker.seq === state.partialSeq;
  try {
    while (current() && state.partialQueue.length) {
      // Full-final mode will replace partial feedback at the endpoint. Hold
      // queued work during its silence timer rather than start another native
      // step that cannot be interrupted when the full recording is submitted.
      if (finalSttMode() === 'full' && state.liveSilenceStart) break;
      const item = state.partialQueue.shift();
      if (item.seq !== worker.seq) continue;
      $('transcriptInfo').textContent = `Transcribing queued window ${item.windowId}; ${state.partialQueue.length} waiting.`;
      try {
        await transcribePartialBlob(item.blob, item.seq, { startChunk: item.windowId, endChunk: item.windowId }, { isCurrent: current });
      } catch (error) {
        if (current()) requireFullTranscript(`A partial transcription failed: ${error.message}`);
      }
    }
  } finally {
    // A cancelled old worker must not drain or clear a newer capture's state.
    if (activeWorker === worker) {
      activeWorker = null;
      state.partialQueueProcessing = false;
    }
  }
}

export async function waitForPartialQueueIdle(seq = state.partialSeq) {
  while (seq === state.partialSeq && (state.partialQueueProcessing || state.partialQueue.length)) {
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

// A graceful stop awaits the recorder's final dataavailable/onstop events before
// chunks mode drains the queue. An abort invalidates all work from this epoch.
export function stopPartialTranscriptionLoop(abort = true) {
  state.partialCaptureActive = false;
  if (state.partialTimer) clearTimeout(state.partialTimer);
  state.partialTimer = null;
  if (abort) {
    state.partialSeq++;
    state.partialQueue = [];
    activeWorker = null;
    state.partialQueueProcessing = false;
    const controller = state.partialController;
    state.partialController = null;
    controller?.abort();
  }
  const recorder = state.partialRecorder;
  const done = state.partialRecorderDone || Promise.resolve();
  if (recorder && recorder.state !== 'inactive') { try { recorder.stop(); } catch {} }
  state.partialRecorder = null;
  return done;
}
