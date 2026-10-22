// Streaming and buffered speech playback, audio scheduling, and interruption context.

import { state } from "./state.js";
import { $, status } from "./dom.js";
import { stripTranscriptLabels } from "./history.js";
import { markActivity, ttsBaseUrl, ttsDevice, ttsModel, ttsProvider } from "./settings-ui.js";
import { startBargeInCapture } from "./barge-in.js";

let bufferedPlayback = null;
let speechVersion = 0;

// Audio context lifecycle
export function ensureAudio() {
  if (!state.audioCtx) {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    state.gain = state.audioCtx.createGain();
    state.gain.connect(state.audioCtx.destination);
  }
  if (state.audioCtx.state === 'suspended') state.audioCtx.resume();
}

// TTS abort
export function stopTtsNow() {
  if (state.controller) { try { state.controller.abort(); } catch {} state.controller = null; }
  bufferedPlayback?.cancel();
  for (const s of state.activeSources) { try { s.stop(); } catch {} }
  state.activeSources = [];
  state.nextPlayTime = 0;
  state.ackPlayingUntil = 0;
  state.ttsSpeaking = false;
}

// Interrupt context
export function interruptedSpeechContext() {
  if (!state.audioCtx || !state.currentKorinaText) return '';
  const elapsed = Math.max(0, state.audioCtx.currentTime - state.currentTtsStartedAt);
  const total = Math.max(0.1, state.currentTtsEstimatedEnd - state.currentTtsStartedAt);
  const ratio = Math.max(0, Math.min(1, elapsed / total));
  const idx = Math.max(0, Math.min(state.currentKorinaText.length, Math.round(state.currentKorinaText.length * ratio)));
  const before = state.currentKorinaText.slice(0, idx).trim();
  const after = state.currentKorinaText.slice(idx).trim();
  return `The user interrupted Korina about ${(ratio * 100).toFixed(0)}% through her previous spoken reply. Already spoken approximately: "${before}". Not yet spoken approximately: "${after}".`;
}

// PCM chunk scheduling
export async function schedulePcm(b64, sr = 24000) {
  ensureAudio();
  const bin = atob(b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  if (!u8.length) return 0;
  const i16 = new Int16Array(u8.buffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = Math.max(-1, Math.min(1, i16[i] / 32768));
  const buf = state.audioCtx.createBuffer(1, f32.length, sr);
  buf.copyToChannel(f32, 0);
  const src = state.audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(state.gain);
  const when = Math.max(state.audioCtx.currentTime, state.nextPlayTime, state.ackPlayingUntil);
  src.start(when);
  state.activeSources.push(src);
  src.onended = () => { state.activeSources = state.activeSources.filter(x => x !== src); };
  state.nextPlayTime = when + f32.length / sr;
  state.currentTtsEstimatedEnd = Math.max(state.currentTtsEstimatedEnd, state.nextPlayTime);
  return f32.length;
}

// Streaming SSE speak
export async function speakSSE(text) {
  state.controller = new AbortController();
  state.nextPlayTime = 0;
  let chunks = 0, samples = 0, start = performance.now(), buf = '';
  status($('ttsStatus'), 'Streaming Kokoro chunks…', 'warn');
  $('progress').style.width = '5%';
  const r = await fetch(`${ttsBaseUrl()}/stream/speech`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: text, voice: $('voice').value, speed: parseFloat($('speed').value), device: ttsDevice(), model: ttsModel(), provider: ttsProvider() }),
    signal: state.controller.signal
  });
  if (!r.ok) throw new Error(await r.text());
  const reader = r.body.getReader(), dec = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const ev = JSON.parse(line.slice(6));
      if (typeof ev.audio !== 'string') throw new Error('TTS stream chunk is missing PCM audio.');
      const frames = await schedulePcm(ev.audio, ev.sample_rate || 24000);
      if (!frames) continue;
      chunks++;
      samples += frames;
      const elapsed = (performance.now() - start) / 1000;
      $('progress').style.width = Math.min(99, 10 + chunks * 12) + '%';
      $('chunkInfo').textContent = `${chunks} chunks · ${samples.toLocaleString()} samples · ${elapsed.toFixed(1)}s`;
    }
  }
  if (!samples) throw new Error('TTS stream returned no audio samples.');
  $('progress').style.width = '100%';
  status($('ttsStatus'), `Done · ${chunks} chunks`, 'good');
}

// Buffered WAV speak
function playBufferedAudio(blob, signal) {
  signal.throwIfAborted();
  const audio = $('audio');
  const url = URL.createObjectURL(blob);
  return new Promise((resolve, reject) => {
    let finished = false;
    const finish = (error) => {
      if (finished) return;
      finished = true;
      audio.removeEventListener('ended', ended);
      audio.removeEventListener('error', failed);
      audio.removeEventListener('playing', playing);
      signal.removeEventListener('abort', aborted);
      if (bufferedPlayback?.url === url) bufferedPlayback = null;
      try {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
      } finally {
        URL.revokeObjectURL(url);
      }
      if (error) reject(error);
      else resolve();
    };
    const ended = () => finish();
    const failed = () => finish(new Error(audio.error?.message || 'Buffered audio playback failed.'));
    const aborted = () => finish(new DOMException('Speech playback interrupted.', 'AbortError'));
    const playing = () => {
      if (state.audioCtx && Number.isFinite(audio.duration)) {
        state.currentTtsStartedAt = state.audioCtx.currentTime;
        state.currentTtsEstimatedEnd = state.currentTtsStartedAt + audio.duration;
      }
    };
    bufferedPlayback = { url, cancel: aborted };
    audio.addEventListener('ended', ended);
    audio.addEventListener('error', failed);
    audio.addEventListener('playing', playing);
    signal.addEventListener('abort', aborted, { once: true });
    audio.src = url;
    try {
      Promise.resolve(audio.play()).catch(finish);
    } catch (error) {
      finish(error);
    }
  });
}

export async function speakBuffered(text) {
  bufferedPlayback?.cancel();
  const controller = new AbortController();
  state.controller = controller;
  status($('ttsStatus'), 'Synthesizing full WAV…', 'warn');
  $('progress').style.width = '10%';
  const r = await fetch(`${ttsBaseUrl()}/v1/audio/speech`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: text, voice: $('voice').value, speed: parseFloat($('speed').value), device: ttsDevice(), model: ttsModel(), provider: ttsProvider() }),
    signal: controller.signal
  });
  if (!r.ok) throw new Error(await r.text());
  const blob = await r.blob();
  controller.signal.throwIfAborted();
  status($('ttsStatus'), 'Playing buffered WAV…', 'warn');
  await playBufferedAudio(blob, controller.signal);
  $('progress').style.width = '100%';
  status($('ttsStatus'), 'Done · buffered playback complete', 'good');
}

// Speak entrypoint
export async function speakText(text, wait = false) {
  text = stripTranscriptLabels(text);
  if (!text.trim()) return;
  const version = ++speechVersion;
  markActivity('korina-speech-start');
  $('playBtn').disabled = true;
  $('stopBtn').disabled = false;
  $('chunkInfo').textContent = '';
  ensureAudio();
  state.currentKorinaText = text;
  state.currentTtsStartedAt = state.audioCtx.currentTime;
  state.currentTtsEstimatedEnd = state.currentTtsStartedAt;
  state.activeSources = [];
  state.ttsSpeaking = true;
  if (state.live) startBargeInCapture();
  try {
    if (state.mode === 'sse') await speakSSE(text);
    else await speakBuffered(text);
    if (wait && state.mode === 'sse') await waitForScheduledAudio();
  } catch (e) {
    if (e.name === 'AbortError') {
      if (version === speechVersion) status($('ttsStatus'), 'Interrupted');
      return;
    } else {
      if (version === speechVersion) status($('ttsStatus'), 'Error: ' + e.message, 'bad');
      throw e;
    }
  } finally {
    if (version === speechVersion) {
      if (!state.handlingBarge) {
        state.ttsSpeaking = false;
        if (state.bargeLoopId) cancelAnimationFrame(state.bargeLoopId);
        state.bargeLoopId = null;
        if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') state.bargeRecorder.stop();
      }
      markActivity('korina-speech-end');
      $('playBtn').disabled = false;
      $('stopBtn').disabled = true;
      state.controller = null;
    }
  }
}

// Wait for scheduled audio to finish
export async function waitForScheduledAudio() {
  if (!state.audioCtx) return;
  const ms = Math.max(0, (state.nextPlayTime - state.audioCtx.currentTime) * 1000) + 150;
  if (ms > 0) await new Promise(r => setTimeout(r, ms));
}
