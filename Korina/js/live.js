// Live microphone lifecycle, adaptive endpointing, and the STT → LLM → TTS loop.

import { state } from "./state.js";
import { askAndSpeak } from "./agent-ui.js";
import { $, status } from "./dom.js";
import { markActivity, nextIdleDelayMs, minSpeechMs, finalSttMode, endpointMode } from "./settings-ui.js";
import { preloadAckBuffers, playRandomAck } from "./acks.js";
import { setupMic, releaseMic, makeRecorder, transcribeBlob } from "./recorder.js";
import { startPartialTranscriptionLoop, stopPartialTranscriptionLoop, waitForPartialQueueIdle } from "./partial-queue.js";
import { stopTtsNow } from "./speech.js";
import { rmsLevel, updateAdaptiveVad } from "./vad.js";

// VAD frame thresholds.
const VAD_SPEECH_FRAMES = 8;
const VAD_SILENCE_FRAMES = 10;

// Confirm the selected response model, using the server's runtime load block.
export async function responseLlmPreflight() {
  try {
    const response = await fetch('/api/health');
    if (!response.ok) return { ok: false, reason: `/api/health HTTP ${response.status}` };
    const health = await response.json();
    if (health?.ok !== true) return { ok: false, reason: 'The page server is not ready.' };
    const provider = health.response_llm_load;
    if (!provider?.ok) return { ok: false, reason: provider?.error || 'The response LLM endpoint is unavailable.' };
    if (!provider.loaded) return { ok: false, reason: provider.error || 'The selected response model is not loaded.' };
    return { ok: true };
  } catch (error) {
    return { ok: false, reason: `Health check failed: ${error.message}` };
  }
}

// Start live conversation; stopping while permission is pending cancels this session.
export async function startLive() {
  if (state.live) return;
  const session = ++state.liveSession;
  state.live = true;
  state.liveBusy = false;
  $('liveBtn').classList.add('on');
  $('liveBtn').textContent = 'Stop Live Conversation';
  $('recBtn').disabled = true;
  $('stopRecBtn').disabled = true;
  status($('sttStatus'), 'Preloading ack phrases + arming mic…', 'warn');
  try {
    if (!await setupMic() || session !== state.liveSession || !state.live) return;
    await preloadAckBuffers('global');
    await preloadAckBuffers('idle');
    if (session !== state.liveSession || !state.live) return;
    markActivity('live-start');
    status($('sttStatus'), `Live mode armed (${endpointMode()}, ${state.endSilenceMs}ms endpoint). Start speaking…`, 'warn');
    startLiveRecorder();
    liveMonitor();
  } catch (error) {
    if (session !== state.liveSession) return;
    stopLive();
    status($('sttStatus'), error.message, 'bad');
    throw error;
  }
}

// Stop live conversation
export function stopLive() {
  state.liveSession++;
  state.live = false; state.liveBusy = false;
  stopPartialTranscriptionLoop(true);
  $('liveBtn').classList.remove('on');
  $('liveBtn').textContent = 'Live Conversation';
  $('recBtn').disabled = false;
  $('stopRecBtn').disabled = true;
  if (state.liveLoopId) cancelAnimationFrame(state.liveLoopId);
  if (state.bargeLoopId) cancelAnimationFrame(state.bargeLoopId);
  state.liveLoopId = null;
  state.bargeLoopId = null;
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') state.mediaRecorder.stop();
  if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') state.bargeRecorder.stop();
  releaseMic();
  stopTtsNow();
  if (state.ackSource) { try { state.ackSource.stop(); } catch {} state.ackSource = null; }
  state.ackPlayingUntil = 0;
  state.handlingBarge = false;
  status($('sttStatus'), 'Live mode stopped.');
}

// Start the live recorder
export function startLiveRecorder() {
  if (!state.live || !state.stream || (state.mediaRecorder && state.mediaRecorder.state !== 'inactive')) return;
  const session = state.liveSession;
  state.liveSpeechStart = 0;
  state.liveSilenceStart = 0;
  state.liveLastVoice = 0;
  state.vadSpeechFrames = 0;
  state.vadSilenceFrames = 0;
  state.partialWindowIndex = 0;
  state.latestPartialText = '';
  makeRecorder(async () => {
    if (!state.live || session !== state.liveSession) return;
    await handleLiveTurn(session);
  }, 500);
  startPartialTranscriptionLoop();
}

// Adaptive-VAD silence monitor
export function liveMonitor() {
  if (state.liveLoopId) cancelAnimationFrame(state.liveLoopId);
  state.liveLoopId = null;
  if (!state.live || state.liveBusy) return;
  const level = rmsLevel();
  const now = performance.now();
  updateAdaptiveVad(level, now);
  const aboveSpeech = level > state.vadSpeechThreshold;
  const belowSilence = level < state.vadSilenceThreshold;
  if (aboveSpeech) {
    state.vadSpeechFrames++;
    state.vadSilenceFrames = 0;
  } else if (belowSilence) {
    state.vadSilenceFrames++;
    state.vadSpeechFrames = 0;
  } else {
    // Between thresholds: hold current state instead of flapping.
    state.vadSpeechFrames = 0;
    state.vadSilenceFrames = 0;
  }

  if (!state.liveSpeechStart) {
    if (state.vadSpeechFrames >= VAD_SPEECH_FRAMES) {
      state.liveSpeechStart = now;
      state.liveLastVoice = now;
      markActivity('user-speech-start');
      state.liveSilenceStart = 0;
      status($('sttStatus'), `Listening… voice confirmed level ${level.toFixed(3)} · noise ${state.vadNoiseFloor.toFixed(3)} · speech>${state.vadSpeechThreshold.toFixed(3)} silence<${state.vadSilenceThreshold.toFixed(3)}`, 'warn');
    } else {
      $('transcriptInfo').textContent = `Calibrated noise ${state.vadNoiseFloor.toFixed(3)} · speech>${state.vadSpeechThreshold.toFixed(3)} · silence<${state.vadSilenceThreshold.toFixed(3)}`;
      const idleFor = now - state.lastActivityAt;
      const due = nextIdleDelayMs();
      if (idleFor > due && !state.ttsSpeaking && !state.liveBusy && (!state.audioCtx || state.audioCtx.currentTime >= state.ackPlayingUntil)) {
        playRandomAck('idle');
      }
    }
  } else if (aboveSpeech) {
    state.liveLastVoice = now;
    markActivity('user-speaking');
    state.liveSilenceStart = 0;
    status($('sttStatus'), `Listening… level ${level.toFixed(3)} · speech>${state.vadSpeechThreshold.toFixed(3)} silence<${state.vadSilenceThreshold.toFixed(3)}`, 'warn');
  } else if (state.vadSilenceFrames >= VAD_SILENCE_FRAMES) {
    if (!state.liveSilenceStart) state.liveSilenceStart = now;
    const speechMs = state.liveLastVoice - state.liveSpeechStart;
    const silenceMs = now - state.liveSilenceStart;
    if (speechMs > minSpeechMs() && silenceMs > state.endSilenceMs) {
      state.liveBusy = true;
      status($('sttStatus'), `End of speech after ${Math.round(silenceMs)}ms silence (${endpointMode()}) with ${(speechMs / 1000).toFixed(1)}s continuous speech. noise ${state.vadNoiseFloor.toFixed(3)} · draining STT queue…`, 'warn');
      stopPartialTranscriptionLoop(false);
      playRandomAck();
      if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') state.mediaRecorder.stop();
      return;
    }
  }
  state.liveLoopId = requestAnimationFrame(liveMonitor);
}

// Per-turn STT→LM→TTS handoff
export async function handleLiveTurn(session = state.liveSession) {
  let preserveStatus = false;
  stopPartialTranscriptionLoop(false);
  try {
    const blob = new Blob(state.recChunks, { type: state.recChunks[0]?.type || 'audio/webm' });
    if (blob.size < 2500) {
      status($('sttStatus'), 'Ignored tiny/no-speech clip. Listening…', 'warn');
      preserveStatus = true;
      return;
    }
    await waitForPartialQueueIdle();
    if (!state.live || session !== state.liveSession) return;
    let text = '';
    if (finalSttMode() === 'chunks' && state.latestPartialText.trim()) {
      text = state.latestPartialText.trim();
      $('transcriptInfo').textContent = 'Using queued live chunks after draining queue; skipped full paragraph retranscription.';
      status($('sttStatus'), `Using queued live chunk transcript (${text.length} chars); no full-utterance STT pass.`, 'good');
    } else {
      const j = await transcribeBlob(blob);
      if (!state.live || session !== state.liveSession) return;
      text = (j.text || '').trim();
      if (text) {
        const existing = String($('transcript').value).trim();
        if (!existing || !existing.includes(text.slice(0, Math.min(40, text.length)))) {
          $('transcript').value += (existing ? '\n' : '') + text;
        }
        $('transcript').scrollTop = $('transcript').scrollHeight;
        status($('sttStatus'), `Final full-turn transcript ready (${text.length} chars); displayed partial chunks remain above.`, 'good');
        $('transcriptInfo').textContent = 'Final full-turn transcript is used for LM; live chunk history is preserved above.';
      }
    }
    if (!text) {
      status($('sttStatus'), 'No transcript. Listening…', 'warn');
      preserveStatus = true;
      return;
    }
    const pre = await responseLlmPreflight();
    if (!state.live || session !== state.liveSession) return;
    if (!pre.ok) {
      status($('sttStatus'), `Live mode: ${pre.reason} Check the response LLM in Settings, then try speaking again.`, 'bad');
      // Don't break the live loop — keep listening so the user can talk again.
      preserveStatus = true;
      return;
    }
    try {
      const replyText = await askAndSpeak(text);
      // askAndSpeak() routes through speakText() which already clears
      // ttsSpeaking in its `finally` block. An empty string here usually means
      // the LLM replied with whitespace or no content; surface that loudly so
      // the user knows it's not a stalled TTS.
      if (!replyText || !String(replyText).trim()) {
        preserveStatus = true;
        status($('sttStatus'), 'Live mode: response LLM returned an empty reply. The model may need a longer context or different prompt.', 'bad');
      }
    } catch (e) {
      preserveStatus = true;
      status($('sttStatus'), `Live mode: askAndSpeak failed: ${e && e.message ? e.message : e}`, 'bad');
    }
  } catch (e) {
    preserveStatus = true;
    status($('sttStatus'), 'Live loop error: ' + e.message, 'bad');
  } finally {
    if (state.live && session === state.liveSession && !state.handlingBarge) {
      state.liveBusy = false;
      if (!preserveStatus) status($('sttStatus'), `Live mode armed (${endpointMode()}, ${state.endSilenceMs}ms endpoint). Start speaking…`, 'warn');
      try {
        startLiveRecorder();
        liveMonitor();
      } catch (error) {
        stopLive();
        status($('sttStatus'), `Microphone restart failed: ${error.message} Try starting live mode again.`, 'bad');
      }
    }
  }
}
