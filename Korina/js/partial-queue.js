// js/partial-queue.js
//
// Frontend partial-window STT queue — bounded WebM window recorder,
// FIFO transcription queue, lifecycle start/idle-wait/stop helpers —
// extracted verbatim from the original inline <script> in
// Korina/index.html (lines 1067–1136 at commit bdda3cc).
//
// Bare top-level globals (partialTimer, partialSeq, partialWindowIndex,
// latestPartialText, latestPartialAt, partialRecorder, partialChunks,
// partialQueueProcessing, partialInFlight, partialQueue, partialController)
// have been migrated to the consolidated `state` object
// (see state.js). `live`, `liveBusy`, `stream`, `liveSpeechStart` are
// also state.X reads. `transcribePartialBlob` is from recorder.js (3.2.10,
// same batch) — referenced as a bare call so it resolves at call time
// once recorder.js lands in beta alongside this module.
//
// Consumers import the named exports below.

import { state } from "./state.js";
import { $ } from "./dom.js";

// --- Start partial transcription loop (verbatim from index.html:1067–1073) ---
export function startPartialTranscriptionLoop() {
  stopPartialTranscriptionLoop(false);
  state.partialSeq++;
  state.partialWindowIndex = 0;
  state.latestPartialText = ''; state.latestPartialAt = 0;
  startPartialWindowRecorder();
}

// --- Bounded partial WebM window recorder (verbatim from index.html:1074–1101) ---
export function startPartialWindowRecorder() {
  if (!state.live || state.liveBusy || !state.stream) return;
  try {
    if (state.partialRecorder && state.partialRecorder.state !== 'inactive') return;
    state.partialChunks = [];
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    const windowId = ++state.partialWindowIndex;
    const seq = state.partialSeq;
    state.partialRecorder = new MediaRecorder(state.stream, { mimeType: mime });
    state.partialRecorder.ondataavailable = e => { if (e.data.size) state.partialChunks.push(e.data); };
    state.partialRecorder.onstop = () => {
      const chunks = state.partialChunks;
      state.partialChunks = [];
      // Restart first so capture continues while faster-whisper drains the queue.
      if (state.live && !state.liveBusy && seq === state.partialSeq) setTimeout(startPartialWindowRecorder, 0);
      if (!state.live || seq !== state.partialSeq || !state.liveSpeechStart || chunks.length < 2) return;
      const blob = new Blob(chunks, { type: chunks[0]?.type || mime });
      if (blob.size < 2500) return;
      state.partialQueue.push({ blob, seq, windowId });
      $('transcriptInfo').textContent = `Queued partial window ${windowId}; queue depth ${state.partialQueue.length}.`;
      processPartialQueue();
    };
    state.partialRecorder.start(500);
    state.partialTimer = setTimeout(() => {
      if (state.partialRecorder && state.partialRecorder.state !== 'inactive') state.partialRecorder.stop();
    }, state.partialWindowMs);
  } catch (e) { console.warn('partial recorder failed', e); }
}

// --- Drain the partial transcription queue (verbatim from index.html:1102–1118) ---
export async function processPartialQueue() {
  if (state.partialQueueProcessing) return;
  state.partialQueueProcessing = true;
  state.partialInFlight = true;
  try {
    while (state.partialQueue.length) {
      const item = state.partialQueue.shift();
      if (item.seq !== state.partialSeq) continue;
      $('transcriptInfo').textContent = `Transcribing queued window ${item.windowId}; ${state.partialQueue.length} waiting.`;
      try { await transcribePartialBlob(item.blob, item.seq, { startChunk: item.windowId, endChunk: item.windowId }); }
      catch (e) { if (e.name !== 'AbortError') console.warn('partial STT failed', e); }
    }
  } finally {
    state.partialInFlight = false;
    state.partialQueueProcessing = false;
  }
}

// --- Wait until the partial queue drains (verbatim from index.html:1119–1123) ---
export async function waitForPartialQueueIdle() {
  while (state.partialQueueProcessing || state.partialQueue.length) {
    await new Promise(r => setTimeout(r, 150));
  }
}

// --- Stop partial transcription loop (verbatim from index.html:1124–1136) ---
export function stopPartialTranscriptionLoop(abort = true) {
  if (state.partialTimer) clearTimeout(state.partialTimer);
  state.partialTimer = null;
  if (abort) state.partialSeq++;
  if (state.partialRecorder && state.partialRecorder.state !== 'inactive') { try { state.partialRecorder.stop(); } catch { } }
  state.partialRecorder = null;
  state.partialChunks = [];
  if (abort) { state.partialQueue = []; }
  if (abort && state.partialController) { try { state.partialController.abort(); } catch { } }
  state.partialController = null;
  if (abort) { state.partialInFlight = false; state.partialQueueProcessing = false; }
}
