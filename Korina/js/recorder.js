// Microphone setup, audio recording, and buffered or streaming STT requests.

import { state } from "./state.js";
import { $, status } from "./dom.js";
import { resetAdaptiveVad, drawWave } from "./vad.js";
import { sttDevice, sttModel, sttBackend, effectiveSttLlmModel } from "./settings-ui.js";
import { recordSttBackend } from "./api.js";
import { createBoundedRecorder } from "./bounded-recorder.js";
import { readJsonEvents } from "./sse.js";
import { responseError } from "./response-body.js";
import { apiErrorMessage } from './api-errors.js';

let microphoneGeneration = 0;
let microphoneSetup = null;

function stopTracks(stream) {
  stream?.getTracks().forEach(track => track.stop());
}

function closeMeter(context) {
  if (context && context.state !== 'closed') {
    try { Promise.resolve(context.close()).catch(() => {}); } catch {}
  }
}

export function releaseMic() {
  microphoneGeneration++;
  microphoneSetup = null;
  const stream = state.stream;
  const meter = state.meterAudioCtx;
  state.stream = null;
  state.meterAudioCtx = null;
  state.analyser = null;
  if (state.raf) cancelAnimationFrame(state.raf);
  state.raf = null;
  stopTracks(stream);
  closeMeter(meter);
}

function microphoneError(error) {
  const messages = {
    NotAllowedError: 'Microphone permission denied. Allow microphone access in your browser and try again.',
    SecurityError: 'Microphone access is blocked. Open Korina over HTTPS or localhost and allow microphone access.',
    NotFoundError: 'No microphone was found. Connect a microphone and try again.',
    NotReadableError: 'The microphone is unavailable or in use. Close other recording apps and try again.',
    OverconstrainedError: 'The microphone does not support the requested audio settings. Try another device.',
  };
  return new Error(messages[error.name] || error.message || 'Microphone setup failed. Please try again.');
}

// Share one pending permission request and discard streams from cancelled starts.
export function setupMic() {
  if (microphoneSetup) return microphoneSetup;
  if (state.stream?.active && state.meterAudioCtx && state.meterAudioCtx.state !== 'closed') return Promise.resolve(true);
  releaseMic();
  const generation = microphoneGeneration;
  let pending;
  pending = (async () => {
    let stream, context;
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('Microphone unavailable. Open Korina in a recent browser over HTTPS or http://localhost.');
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (generation !== microphoneGeneration) { stopTracks(stream); return false; }
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error('Microphone audio processing is unavailable in this browser.');
      context = new Context();
      if (context.state === 'suspended') await context.resume();
      if (generation !== microphoneGeneration) { stopTracks(stream); closeMeter(context); return false; }
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      state.stream = stream;
      state.meterAudioCtx = context;
      state.analyser = analyser;
      resetAdaptiveVad();
      drawWave();
      return true;
    } catch (error) {
      stopTracks(stream);
      closeMeter(context);
      const cancelled = generation !== microphoneGeneration;
      if (state.stream === stream) releaseMic();
      if (cancelled) return false;
      throw microphoneError(error);
    }
  })().finally(() => { if (microphoneSetup === pending) microphoneSetup = null; });
  microphoneSetup = pending;
  return pending;
}

// Each recorder owns its chunks; starting twice cannot replace an active capture.
export function makeRecorder(onstop, timeslice = 500, { isCurrent = () => true, clock } = {}) {
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    throw new Error('The microphone is already recording.');
  }
  if (!state.stream) throw new Error('The microphone is not ready. Try starting the recording again.');
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
  const capture = createBoundedRecorder(state.stream, {
    mimeType: mime, onstop, clock,
    isCurrent: () => state.mediaRecorder === capture.recorder && isCurrent(),
  });
  state.recChunks = capture.chunks;
  state.mediaRecorder = capture.recorder;
  try { capture.start(timeslice); }
  catch (error) { capture.cancel(); state.mediaRecorder = null; state.recChunks = []; throw error; }
  return capture;
}

function transcriptionAborted() {
  return new DOMException('Transcription is no longer current.', 'AbortError');
}

// Streaming STT transcription. Callers own cancellation and session validity.
export async function transcribeBlob(blob, { signal, isCurrent = () => true } = {}) {
  const current = () => !signal?.aborted && isCurrent();
  const assertCurrent = () => { if (!current()) throw transcriptionAborted(); };
  assertCurrent();
  const fd = new FormData();
  fd.append('audio', blob, 'recording.webm');
  try {
    var r = await fetch(`/api/transcribe/stream?device=${encodeURIComponent(sttDevice())}&model=${encodeURIComponent(sttModel())}&backend=${encodeURIComponent(sttBackend())}&llm_model=${encodeURIComponent(effectiveSttLlmModel())}`, { method: 'POST', body: fd, signal });
  } catch (e) {
    assertCurrent();
    recordSttBackend(null, `fetch failed: ${e && e.message ? e.message : e}`);
    throw e;
  }
  assertCurrent();
  if (!r.ok) {
    const error = await responseError(r, { signal, isCurrent: current });
    assertCurrent();
    recordSttBackend(null, error.message);
    throw error;
  }
  let final = null;
  const pieces = [];
  try {
    for await (const { event, data: payload } of readJsonEvents(r.body, { isCurrent: current })) {
      assertCurrent();
      if (event === 'status') {
        if (typeof payload.message !== 'string') throw new Error('Invalid STT status event.');
        status($('sttStatus'), `STT: ${payload.message}…`, 'warn');
      } else if (event === 'segment') {
        if (typeof payload.text !== 'string') throw new Error('Invalid STT transcript segment.');
        if (payload.text) pieces.push(payload.text);
        $('transcript').value = pieces.join(' ').trim();
        status($('sttStatus'), `Streaming STT segment ${payload.index}: ${payload.text}`, 'warn');
        $('transcriptInfo').textContent = 'Receiving final STT segments…';
      } else if (event === 'done') {
        if (typeof payload.text !== 'string') throw new Error('Invalid STT final result.');
        for (const field of ['seconds', 'duration', 'samples', 'sample_rate']) {
          if (payload[field] !== undefined && (!Number.isFinite(payload[field]) || payload[field] < 0)) {
            throw new Error(`Invalid STT final result: ${field}.`);
          }
        }
        final = payload;
        recordSttBackend(payload, undefined);
      } else if (event === 'error') {
        throw new Error(typeof payload.detail === 'string' ? payload.detail : 'streaming transcription failed');
      }
    }
  } catch (error) {
    assertCurrent();
    recordSttBackend(null, error.message);
    throw error;
  }
  assertCurrent();
  if (!final) throw new Error('streaming transcription ended without final result');
  return final;
}

// Partial window STT transcription
export async function transcribePartialBlob(blob, seq, meta = {}, { isCurrent = () => true } = {}) {
  const ctl = new AbortController();
  const current = () => seq === state.partialSeq && !ctl.signal.aborted && isCurrent();
  const assertCurrent = () => { if (!current()) throw transcriptionAborted(); };
  assertCurrent();
  const fd = new FormData();
  fd.append('audio', blob, 'partial.webm');
  state.partialController = ctl;
  try {
    let r;
    try {
      r = await fetch(`/api/transcribe/partial?device=${encodeURIComponent(sttDevice())}&model=${encodeURIComponent(sttModel())}&backend=${encodeURIComponent(sttBackend())}&llm_model=${encodeURIComponent(effectiveSttLlmModel())}`, { method: 'POST', body: fd, signal: ctl.signal });
    } catch (e) {
      assertCurrent();
      recordSttBackend(null, `partial fetch failed: ${e && e.message ? e.message : e}`);
      throw e;
    }
    assertCurrent();
    const j = await r.json();
    assertCurrent();
    if (!r.ok) {
      const message = apiErrorMessage(j, `HTTP ${r.status}`);
      recordSttBackend(null, message);
      throw new Error(message);
    }
    recordSttBackend({
      backend: j.backend,
      seconds: j.seconds,
      samples: j.samples,
      sample_rate: j.sample_rate,
      model: j.model,
      fallback_reason: j.fallback_reason,
    }, undefined);
    const text = (j.text || '').trim();
    if (text) {
      // Each window is independently decodable; middle WebM chunks are not.
      state.latestPartialText = (state.latestPartialText ? `${state.latestPartialText} ${text}` : text).replace(/\s+/g, ' ').trim();
      $('transcript').value = state.latestPartialText + ' …';
      $('transcript').scrollTop = $('transcript').scrollHeight;
      status($('sttStatus'), `Queued partial window ${meta.startChunk} · ${j.duration.toFixed(1)}s audio → ${j.seconds.toFixed(2)}s STT: ${text}`, 'warn'); $('transcriptInfo').textContent = `Queued WebM partial window ${meta.startChunk} · ${j.seconds.toFixed(2)}s CPU STT`;
    }
    return j;
  } finally {
    if (state.partialController === ctl) state.partialController = null;
  }
}
