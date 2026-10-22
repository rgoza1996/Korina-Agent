// Streaming and buffered speech playback, audio scheduling, and interruption context.

import { state } from "./state.js";
import { $, status } from "./dom.js";
import { stripTranscriptLabels } from "./history.js";
import { markActivity, ttsBaseUrl, ttsDevice, ttsModel, ttsProvider } from "./settings-ui.js";
import { startBargeInCapture } from "./barge-in.js";
import { readJsonEvents } from "./sse.js";
import { MAX_AUDIO_SECONDS, readResponseBlob, responseError } from "./response-body.js";

let bufferedPlayback = null;
let speechVersion = 0;
let standaloneAudioSeconds = 0;

function playbackAborted() {
  return new DOMException('Speech playback interrupted.', 'AbortError');
}

function assertPlaybackCurrent(playback) {
  playback.controller.signal.throwIfAborted();
  if (state.controller !== playback.controller) throw playbackAborted();
}

function stopPlayback(playback) {
  playback.controller.abort();
  if (bufferedPlayback?.signal === playback.controller.signal) bufferedPlayback.cancel();
  for (const source of playback.sources) { try { source.stop(); } catch {} }
  state.activeSources = state.activeSources.filter(source => !playback.sources.has(source));
  playback.sources.clear();
  if (state.controller === playback.controller) state.controller = null;
}

function beginPlayback() {
  const version = ++speechVersion;
  const ackUntil = state.ackPlayingUntil;
  stopTtsNow();
  // A newly started reply still waits for an already playing acknowledgement.
  state.ackPlayingUntil = ackUntil;
  const playback = { version, controller: new AbortController(), sources: new Set(), audioSeconds: 0 };
  state.controller = playback.controller;
  return playback;
}

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
  standaloneAudioSeconds = 0;
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
export async function schedulePcm(b64, sr = 24000, playback = null) {
  if (playback) assertPlaybackCurrent(playback);
  if (!Number.isSafeInteger(sr) || sr < 3000 || sr > 384000) throw new Error('TTS stream has an invalid sample rate.');
  ensureAudio();
  let bin;
  try { bin = atob(b64); }
  catch { throw new Error('TTS stream contains invalid base64 PCM audio.'); }
  if (bin.length % 2) throw new Error('TTS stream contains incomplete 16-bit PCM audio.');
  const seconds = bin.length / 2 / sr;
  const totalSeconds = (playback?.audioSeconds ?? standaloneAudioSeconds) + seconds;
  if (totalSeconds > MAX_AUDIO_SECONDS) throw new Error('Generated speech exceeds the ten minute audio limit.');
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
  if (playback) playback.audioSeconds = totalSeconds;
  else standaloneAudioSeconds = totalSeconds;
  state.activeSources.push(src);
  playback?.sources.add(src);
  src.onended = () => {
    state.activeSources = state.activeSources.filter(x => x !== src);
    playback?.sources.delete(src);
  };
  state.nextPlayTime = when + f32.length / sr;
  state.currentTtsEstimatedEnd = Math.max(state.currentTtsEstimatedEnd, state.nextPlayTime);
  return f32.length;
}

// Streaming SSE speak
export async function speakSSE(text, playback = beginPlayback()) {
  state.nextPlayTime = 0;
  let chunks = 0, samples = 0, start = performance.now();
  try {
    status($('ttsStatus'), 'Streaming Kokoro chunks…', 'warn');
    $('progress').style.width = '5%';
    const r = await fetch(`${ttsBaseUrl()}/stream/speech`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: text, voice: $('voice').value, speed: parseFloat($('speed').value), device: ttsDevice(), model: ttsModel(), provider: ttsProvider() }),
      signal: playback.controller.signal
    });
    assertPlaybackCurrent(playback);
    if (!r.ok) throw await responseError(r, { signal: playback.controller.signal,
      isCurrent: () => state.controller === playback.controller });
    // The bundled service permits at most ten minutes of mono PCM. Its
    // base64 output fits these budgets even when emitted as one large event.
    for await (const { data: ev } of readJsonEvents(r.body, {
      isCurrent: () => !playback.controller.signal.aborted && state.controller === playback.controller,
      maxEventBytes: 40 * 1024 * 1024,
      maxTotalBytes: 48 * 1024 * 1024,
    })) {
      assertPlaybackCurrent(playback);
      if (typeof ev.audio !== 'string') throw new Error('TTS stream chunk is missing PCM audio.');
      const frames = await schedulePcm(ev.audio, ev.sample_rate ?? 24000, playback);
      assertPlaybackCurrent(playback);
      if (!frames) continue;
      chunks++;
      samples += frames;
      const elapsed = (performance.now() - start) / 1000;
      $('progress').style.width = Math.min(99, 10 + chunks * 12) + '%';
      $('chunkInfo').textContent = `${chunks} chunks · ${samples.toLocaleString()} samples · ${elapsed.toFixed(1)}s`;
    }
    if (!samples) throw new Error('TTS stream returned no audio samples.');
    await waitForScheduledAudio(playback.controller.signal);
    assertPlaybackCurrent(playback);
    $('progress').style.width = '100%';
    status($('ttsStatus'), `Done · ${chunks} chunks`, 'good');
  } catch (error) {
    stopPlayback(playback);
    throw error;
  }
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
      audio.removeEventListener('loadedmetadata', metadata);
      audio.removeEventListener('durationchange', durationChanged);
      signal.removeEventListener('abort', aborted);
      clearTimeout(deadline);
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
    const metadata = () => {
      if (!Number.isFinite(audio.duration) || audio.duration <= 0 || audio.duration > MAX_AUDIO_SECONDS) {
        finish(new Error('Buffered speech must contain at most ten minutes of finite audio.'));
        return false;
      }
      return true;
    };
    const durationChanged = () => { if (audio.readyState >= 1) metadata(); };
    const playing = () => {
      if (!metadata()) return;
      if (state.audioCtx) {
        state.currentTtsStartedAt = state.audioCtx.currentTime;
        state.currentTtsEstimatedEnd = state.currentTtsStartedAt + audio.duration;
      }
    };
    bufferedPlayback = { url, signal, cancel: aborted };
    audio.addEventListener('ended', ended);
    audio.addEventListener('error', failed);
    audio.addEventListener('playing', playing);
    audio.addEventListener('loadedmetadata', metadata);
    audio.addEventListener('durationchange', durationChanged);
    signal.addEventListener('abort', aborted, { once: true });
    const deadline = setTimeout(() => finish(new Error('Buffered speech exceeded the ten minute playback limit.')), MAX_AUDIO_SECONDS * 1000);
    audio.src = url;
    try {
      Promise.resolve(audio.play()).catch(finish);
    } catch (error) {
      finish(error);
    }
  });
}

export async function speakBuffered(text, playback = beginPlayback()) {
  const { controller } = playback;
  try {
    status($('ttsStatus'), 'Synthesizing full WAV…', 'warn');
    $('progress').style.width = '10%';
    const r = await fetch(`${ttsBaseUrl()}/v1/audio/speech`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: text, voice: $('voice').value, speed: parseFloat($('speed').value), device: ttsDevice(), model: ttsModel(), provider: ttsProvider() }),
      signal: controller.signal
    });
    assertPlaybackCurrent(playback);
    if (!r.ok) throw await responseError(r, { signal: controller.signal,
      isCurrent: () => state.controller === controller });
    const blob = await readResponseBlob(r, { signal: controller.signal,
      isCurrent: () => state.controller === controller });
    assertPlaybackCurrent(playback);
    status($('ttsStatus'), 'Playing buffered WAV…', 'warn');
    await playBufferedAudio(blob, controller.signal);
    assertPlaybackCurrent(playback);
    $('progress').style.width = '100%';
    status($('ttsStatus'), 'Done · buffered playback complete', 'good');
  } catch (error) {
    stopPlayback(playback);
    throw error;
  }
}

// The promise covers actual playback; callers may choose not to await it.
// The legacy wait argument remains accepted for existing integrations.
export async function speakText(text, _wait = false) {
  text = stripTranscriptLabels(text);
  if (!text.trim()) return;
  const playback = beginPlayback();
  const { version } = playback;
  try {
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
    if (state.mode === 'sse') await speakSSE(text, playback);
    else await speakBuffered(text, playback);
  } catch (e) {
    stopPlayback(playback);
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
      if (state.controller === playback.controller) state.controller = null;
    }
  }
}

// Wait for scheduled audio to finish
export async function waitForScheduledAudio(signal) {
  signal?.throwIfAborted();
  if (!state.audioCtx) return;
  const ms = Math.max(0, (state.nextPlayTime - state.audioCtx.currentTime) * 1000) + 150;
  if (ms > 0) await new Promise((resolve, reject) => {
    const finish = error => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', aborted);
      if (error) reject(error);
      else resolve();
    };
    const aborted = () => finish(playbackAborted());
    const timer = setTimeout(() => finish(), ms);
    signal?.addEventListener('abort', aborted, { once: true });
  });
}
