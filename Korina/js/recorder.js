// js/recorder.js
//
// Frontend recorder helpers — microphone setup, MediaRecorder factory,
// and streaming/partial STT transcription — extracted verbatim from
// the original inline <script> in Korina/index.html (lines 1004, 1005,
// 1006–1045, 1046–1066 at commit bdda3cc).
//
// Bare top-level globals (stream, meterAudioCtx, analyser, mediaRecorder,
// recChunks, partialController, partialSeq, latestPartialText,
// latestPartialAt) have been migrated to the consolidated `state`
// object (see state.js). `status` is imported from dom.js.
// `resetAdaptiveVad` and `drawWave` are imported from vad.js (3.2.9,
// already merged into beta). `sttDevice`, `sttModel`, `sttBackend`,
// `effectiveSttLlmModel` are imported from settings-ui.js (3.2.4,
// already merged into beta).
//
// Consumers import the named exports below.

import { state } from "./state.js";
import { $, status } from "./dom.js";
import { resetAdaptiveVad, drawWave } from "./vad.js";
import { sttDevice, sttModel, sttBackend, effectiveSttLlmModel } from "./settings-ui.js";

// --- Microphone + meter setup (verbatim from index.html:1004) ---
export async function setupMic() {
  state.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  state.meterAudioCtx = new AudioContext();
  const src = state.meterAudioCtx.createMediaStreamSource(state.stream);
  state.analyser = state.meterAudioCtx.createAnalyser();
  state.analyser.fftSize = 512;
  src.connect(state.analyser);
  resetAdaptiveVad();
  drawWave();
}

// --- MediaRecorder factory (verbatim from index.html:1005) ---
export function makeRecorder(onstop, timeslice = 0) {
  state.recChunks = [];
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
  state.mediaRecorder = new MediaRecorder(state.stream, { mimeType: mime });
  state.mediaRecorder.ondataavailable = e => { if (e.data.size) state.recChunks.push(e.data); };
  state.mediaRecorder.onstop = onstop;
  state.mediaRecorder.start(timeslice || undefined);
}

// --- Streaming STT transcription (verbatim from index.html:1006–1045) ---
export async function transcribeBlob(blob) {
  const fd = new FormData();
  fd.append('audio', blob, 'recording.webm');
  const r = await fetch(`/api/transcribe/stream?device=${encodeURIComponent(sttDevice())}&model=${encodeURIComponent(sttModel())}&backend=${encodeURIComponent(sttBackend())}&llm_model=${encodeURIComponent(effectiveSttLlmModel())}`, { method: 'POST', body: fd });
  if (!r.ok) {
    const j = await r.json().catch(async () => ({ detail: await r.text() }));
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
      } else if (ev === 'error') {
        throw new Error(payload.detail || 'streaming transcription failed');
      }
    }
  }
  if (!final) throw new Error('streaming transcription ended without final result');
  return final;
}

// --- Partial window STT transcription (verbatim from index.html:1046–1066) ---
export async function transcribePartialBlob(blob, seq, meta = {}) {
  const fd = new FormData();
  fd.append('audio', blob, 'partial.webm');
  const ctl = new AbortController();
  state.partialController = ctl;
  const r = await fetch(`/api/transcribe/partial?device=${encodeURIComponent(sttDevice())}&model=${encodeURIComponent(sttModel())}&backend=${encodeURIComponent(sttBackend())}&llm_model=${encodeURIComponent(effectiveSttLlmModel())}`, { method: 'POST', body: fd, signal: ctl.signal });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
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
