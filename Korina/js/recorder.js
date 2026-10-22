// Microphone setup, audio recording, and buffered or streaming STT requests.

import { state } from "./state.js";
import { $, status } from "./dom.js";
import { resetAdaptiveVad, drawWave } from "./vad.js";
import { sttDevice, sttModel, sttBackend, effectiveSttLlmModel } from "./settings-ui.js";
import { recordSttBackend } from "./api.js";

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
export function makeRecorder(onstop, timeslice = 0) {
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    throw new Error('The microphone is already recording.');
  }
  if (!state.stream) throw new Error('The microphone is not ready. Try starting the recording again.');
  const chunks = [];
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
  const recorder = new MediaRecorder(state.stream, { mimeType: mime });
  recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
  recorder.onstop = onstop;
  state.recChunks = chunks;
  state.mediaRecorder = recorder;
  recorder.start(timeslice || undefined);
}

// Streaming STT transcription
export async function transcribeBlob(blob) {
  const fd = new FormData();
  fd.append('audio', blob, 'recording.webm');
  try {
    var r = await fetch(`/api/transcribe/stream?device=${encodeURIComponent(sttDevice())}&model=${encodeURIComponent(sttModel())}&backend=${encodeURIComponent(sttBackend())}&llm_model=${encodeURIComponent(effectiveSttLlmModel())}`, { method: 'POST', body: fd });
  } catch (e) {
    recordSttBackend(null, `fetch failed: ${e && e.message ? e.message : e}`);
    throw e;
  }
  if (!r.ok) {
    let j;
    try { j = await r.json(); } catch { try { j = { detail: await r.text() }; } catch { j = { detail: 'unknown' }; } }
    recordSttBackend(null, j.detail || JSON.stringify(j));
    throw new Error(j.detail || JSON.stringify(j));
  }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = '', final = null, pieces = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const events = buf.split('\n\n');
    buf = events.pop();
    for (const raw of events) {
      let ev = 'message', data = '';
      for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) ev = line.slice(7);
        else if (line.startsWith('data: ')) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (ev === 'status') status($('sttStatus'), `STT: ${payload.message}…`, 'warn');
      else if (ev === 'segment') {
        if (payload.text) pieces.push(payload.text);
        $('transcript').value = pieces.join(' ').trim();
        status($('sttStatus'), `Streaming STT segment ${payload.index}: ${payload.text}`, 'warn'); $('transcriptInfo').textContent = 'Receiving final STT segments…';
      } else if (ev === 'done') {
        final = payload;
        recordSttBackend(payload, undefined);
      } else if (ev === 'error') {
        recordSttBackend(null, payload.detail || 'streaming transcription failed');
        throw new Error(payload.detail || 'streaming transcription failed');
      }
    }
  }
  if (!final) throw new Error('streaming transcription ended without final result');
  return final;
}

// Partial window STT transcription
export async function transcribePartialBlob(blob, seq, meta = {}) {
  const fd = new FormData();
  fd.append('audio', blob, 'partial.webm');
  const ctl = new AbortController();
  state.partialController = ctl;
  let r;
  try {
    r = await fetch(`/api/transcribe/partial?device=${encodeURIComponent(sttDevice())}&model=${encodeURIComponent(sttModel())}&backend=${encodeURIComponent(sttBackend())}&llm_model=${encodeURIComponent(effectiveSttLlmModel())}`, { method: 'POST', body: fd, signal: ctl.signal });
  } catch (e) {
    recordSttBackend(null, `partial fetch failed: ${e && e.message ? e.message : e}`);
    throw e;
  }
  const j = await r.json();
  if (!r.ok) {
    recordSttBackend(null, j.detail || JSON.stringify(j));
    throw new Error(j.detail || JSON.stringify(j));
  }
  recordSttBackend({
    backend: j.backend,
    seconds: j.seconds,
    samples: j.samples,
    sample_rate: j.sample_rate,
    model: j.model,
    fallback_reason: j.fallback_reason,
  }, undefined);
  if (seq !== state.partialSeq) return j;
  const text = (j.text || '').trim();
  if (text) {
    // This is intentionally a separately-recorded bounded WebM window, not a growing whole-utterance snapshot.
    // Do not slice middle MediaRecorder chunks; those may not be independently decodable.
    state.latestPartialText = (state.latestPartialText ? `${state.latestPartialText} ${text}` : text).replace(/\s+/g, ' ').trim();
    state.latestPartialAt = performance.now();
    $('transcript').value = state.latestPartialText + ' …';
    $('transcript').scrollTop = $('transcript').scrollHeight;
    status($('sttStatus'), `Queued partial window ${meta.startChunk} · ${j.duration.toFixed(1)}s audio → ${j.seconds.toFixed(2)}s STT: ${text}`, 'warn'); $('transcriptInfo').textContent = `Queued WebM partial window ${meta.startChunk} · ${j.seconds.toFixed(2)}s CPU STT`;
  }
  return j;
}
