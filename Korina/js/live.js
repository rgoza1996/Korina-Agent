// js/live.js
//
// Frontend live-conversation controller — start/stop armed microphone,
// adaptive-VAD silence monitor, end-of-speech endpointing, and the
// per-turn STT→LM→TTS handoff — extracted verbatim from the original
// inline <script> in Korina/index.html (lines 1220–1302 at commit
// bdda3cc).
//
// Bare top-level globals (live, liveBusy, liveSilenceStart,
// liveSpeechStart, liveLastVoice, liveLoopId, history, mediaRecorder,
// recChunks, analyser, stream, meterAudioCtx, mode, controller,
// nextPlayTime, ttsSpeaking, currentKorinaText, interruptContext,
// idleAckCount, endSilenceMs, ackSource, ackPlayingUntil,
// lastActivityAt, vadSpeechFrames, vadSilenceFrames, vadNoiseFloor,
// vadSpeechThreshold, vadSilenceThreshold, partialWindowIndex,
// latestPartialText, handlingBarge, bargeRecorder, bargeLoopId, raf,
// audioCtx) have been migrated to the consolidated `state` object
// (see state.js). `setupMic`, `makeRecorder` are imported from
// recorder.js (3.2.10, already merged). `startPartialTranscriptionLoop`
// and `stopPartialTranscriptionLoop` are from partial-queue.js
// (3.2.11, already merged). `preloadAckBuffers`, `playRandomAck` are
// from acks.js (3.2.7, already merged). `markActivity`,
// `nextIdleDelayMs` are from settings-ui.js (3.2.4, already merged).
// `minSpeechMs`, `finalSttMode`, `endpointMode` are from settings-ui.js.
// `waitForPartialQueueIdle` is from partial-queue.js. `transcribeBlob`
// is from recorder.js. `stopTtsNow` is from speech.js (3.2.8, already
// merged). `rmsLevel`, `updateAdaptiveVad` are from vad.js (3.2.9,
// already merged). `EventBus` is from state.js (3.1.1, already merged).
// `status` and `$` are imported from dom.js (3.2.1, already merged).
//
// `askAndSpeak` is a forward reference — it lives in agent-ui.js
// (3.2.14, same batch) — referenced as a bare call so it resolves at
// call time once agent-ui.js lands in beta alongside this module.
//
// Consumers import the named exports below.

import { state, EventBus } from "./state.js";
import { $, status } from "./dom.js";
import { markActivity, nextIdleDelayMs, minSpeechMs, finalSttMode, endpointMode } from "./settings-ui.js";
import { preloadAckBuffers, playRandomAck } from "./acks.js";
import { setupMic, makeRecorder, transcribeBlob } from "./recorder.js";
import { startPartialTranscriptionLoop, stopPartialTranscriptionLoop, waitForPartialQueueIdle } from "./partial-queue.js";
import { stopTtsNow } from "./speech.js";
import { rmsLevel, updateAdaptiveVad } from "./vad.js";

// VAD frame thresholds (top-level const at bdda3cc:325).
const VAD_SPEECH_FRAMES = 8;
const VAD_SILENCE_FRAMES = 10;

// --- Response-LLM reachability preflight (added for live-loop visibility) ---
// Phase 5/visibility fix: instead of letting acks play and then going silent
// when the configured response LLM is unreachable, do a fast preflight before
// `askAndSpeak()` and surface a specific "bad" status so the user understands
// why no reply is coming.
async function responseLlmPreflight() {
  try {
    const r = await fetch('/api/health');
    if (!r.ok) return { ok: false, reason: `/api/health HTTP ${r.status}` };
    const j = await r.json();
    // /api/health returns `llm_error` if llama-server etc. is unreachable, but
    // does not include it on /api/capabilities. We only need to know whether
    // the response LLM (not the page server) is up.
    if (j && j.llm_error) return { ok: false, reason: j.llm_error };
    if (j && j.ok === false) return { ok: false, reason: 'korina-voice-lab reports not ok' };
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: `health fetch failed: ${e.message}` };
  }
}

// --- Start live conversation (verbatim from index.html:1220) ---
export async function startLive() {
  state.live = true; state.liveBusy = false;
  $('liveBtn').classList.add('on');
  $('liveBtn').textContent = 'Stop Live Conversation';
  $('recBtn').disabled = true;
  $('stopRecBtn').disabled = true;
  status($('sttStatus'), 'Preloading ack phrases + arming mic…', 'warn');
  await setupMic();
  await preloadAckBuffers('global');
  await preloadAckBuffers('idle');
  markActivity('live-start');
  status($('sttStatus'), `Live mode armed (${endpointMode()}, ${state.endSilenceMs}ms endpoint). Start speaking…`, 'warn');
  startLiveRecorder();
  liveMonitor();
}

// --- Stop live conversation (verbatim from index.html:1221) ---
export function stopLive() {
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
  if (EventBus.firstDeliveryTimer) clearTimeout(EventBus.firstDeliveryTimer);
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') state.mediaRecorder.stop();
  if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') state.bargeRecorder.stop();
  if (state.stream) state.stream.getTracks().forEach(t => t.stop());
  if (state.raf) cancelAnimationFrame(state.raf);
  stopTtsNow();
  if (state.ackSource) { try { state.ackSource.stop(); } catch {} state.ackSource = null; }
  state.ackPlayingUntil = 0;
  state.handlingBarge = false;
  status($('sttStatus'), 'Live mode stopped.');
}

// --- Start the live recorder (verbatim from index.html:1222) ---
export function startLiveRecorder() {
  if (!state.live || !state.stream) return;
  state.liveSpeechStart = 0;
  state.liveSilenceStart = 0;
  state.liveLastVoice = 0;
  state.vadSpeechFrames = 0;
  state.vadSilenceFrames = 0;
  state.partialWindowIndex = 0;
  state.latestPartialText = '';
  makeRecorder(async () => {
    if (!state.live) return;
    await handleLiveTurn();
  }, 500);
  startPartialTranscriptionLoop();
}

// --- Adaptive-VAD silence monitor (verbatim from index.html:1223–1276) ---
export function liveMonitor() {
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

// --- Per-turn STT→LM→TTS handoff (verbatim from index.html:1277–1302) ---
export async function handleLiveTurn() {
  stopPartialTranscriptionLoop(false);
  try {
    const blob = new Blob(state.recChunks, { type: state.recChunks[0]?.type || 'audio/webm' });
    if (blob.size < 2500) {
      status($('sttStatus'), 'Ignored tiny/no-speech clip. Listening…', 'warn');
      state.liveBusy = false;
      startLiveRecorder();
      liveMonitor();
      return;
    }
    await waitForPartialQueueIdle();
    let text = '';
    if (finalSttMode() === 'chunks' && state.latestPartialText.trim()) {
      text = state.latestPartialText.trim();
      $('transcriptInfo').textContent = 'Using queued live chunks after draining queue; skipped full paragraph retranscription.';
      status($('sttStatus'), `Using queued live chunk transcript (${text.length} chars); no full-utterance STT pass.`, 'good');
    } else {
      const j = await transcribeBlob(blob);
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
      state.liveBusy = false;
      startLiveRecorder();
      liveMonitor();
      return;
    }
    const pre = await responseLlmPreflight();
    if (!pre.ok) {
      status($('sttStatus'), `Live mode: response LLM unreachable (${pre.reason}). Acks are still playing but no reply will come until llama.cpp / LM Studio is running.`, 'bad');
      // Don't break the live loop — keep listening so the user can talk again.
      state.liveBusy = false;
      startLiveRecorder();
      liveMonitor();
      return;
    }
    try {
      const replyText = await askAndSpeak(text);
      // askAndSpeak() routes through speakText() which already clears
      // ttsSpeaking in its `finally` block. An empty string here usually means
      // the LLM replied with whitespace or no content; surface that loudly so
      // the user knows it's not a stalled TTS.
      if (!replyText || !String(replyText).trim()) {
        status($('sttStatus'), 'Live mode: response LLM returned an empty reply. The model may need a longer context or different prompt.', 'bad');
      }
    } catch (e) {
      // askAndSpeak already swallowed the error into TTS status; do not
      // double-throw, but make sure the live loop's status is also updated.
      status($('sttStatus'), `Live mode: askAndSpeak failed: ${e && e.message ? e.message : e}`, 'bad');
    }
  } catch (e) {
    status($('sttStatus'), 'Live loop error: ' + e.message, 'bad');
  } finally {
    if (state.live && !state.handlingBarge) {
      state.liveBusy = false;
      status($('sttStatus'), `Live mode armed (${endpointMode()}, ${state.endSilenceMs}ms endpoint). Start speaking…`, 'warn');
      startLiveRecorder();
      liveMonitor();
    }
  }
}
