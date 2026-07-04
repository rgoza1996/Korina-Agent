// js/barge-in.js
//
// Frontend barge-in helpers — capture during TTS playback, RMS-based
// speech/silence monitoring, and after-interrupt turn handling —
// extracted verbatim from the original inline <script> in
// Korina/index.html (lines 1137–1219 at commit bdda3cc).
//
// Bare top-level globals (bargeRecorder, bargeChunks, bargeLoopId,
// bargeSpeechStart, bargeSilenceStart, handlingBarge, live, stream,
// ttsSpeaking, interruptContext, vadSpeechThreshold, vadSilenceThreshold,
// endSilenceMs) have been migrated to the consolidated `state` object
// (see state.js). `status` and `log` are imported from dom.js.
// `stopTtsNow` and `interruptedSpeechContext` are imported from
// speech.js (3.2.8, already merged into beta). `rmsLevel` and
// `updateAdaptiveVad` are imported from vad.js (3.2.9, already merged
// into beta). `endpointMode` is imported from settings-ui.js (3.2.4,
// already merged into beta). `EventBus` is imported from state.js
// (extracted in 3.1.1, already merged). `transcribeBlob` is from
// recorder.js (3.2.10, same batch) — referenced as a bare call so it
// resolves at call time once recorder.js lands in beta alongside this
// module. `askAndSpeak`, `startLiveRecorder`, `liveMonitor`,
// `currentConfig` remain bare globals — they belong to modules that
// have not yet been extracted and will resolve at call time when the
// bootstrap wires them up.
//
// Consumers import the named exports below.

import { state, EventBus } from "./state.js";
import { $, status, log } from "./dom.js";
import { stopTtsNow, interruptedSpeechContext } from "./speech.js";
import { rmsLevel, updateAdaptiveVad } from "./vad.js";
import { endpointMode } from "./settings-ui.js";

// --- Start barge-in capture during TTS (verbatim from index.html:1137–1149) ---
export function startBargeInCapture() {
  if (!state.live || !state.stream || !state.ttsSpeaking || state.handlingBarge) return;
  try {
    if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') return;
    state.bargeChunks = []; state.bargeSpeechStart = 0; state.bargeSilenceStart = 0;
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    state.bargeRecorder = new MediaRecorder(state.stream, { mimeType: mime });
    state.bargeRecorder.ondataavailable = e => { if (e.data.size) state.bargeChunks.push(e.data); };
    state.bargeRecorder.onstop = handleBargeRecording;
    state.bargeRecorder.start();
    monitorBargeIn();
  } catch (e) { console.warn('barge recorder failed', e); }
}

// --- Monitor for user speech during TTS (verbatim from index.html:1150–1168) ---
export function monitorBargeIn() {
  if (!state.live || !state.ttsSpeaking || state.handlingBarge) return;
  const level = rmsLevel(); const now = performance.now();
  if (level > state.vadSpeechThreshold) {
    if (!state.bargeSpeechStart) state.bargeSpeechStart = now;
    if (now - state.bargeSpeechStart > 220) {
      state.handlingBarge = true;
      state.interruptContext = interruptedSpeechContext();
      status($('sttStatus'), 'User interruption detected. Korina stopped; listening to interruption…', 'warn');
      stopTtsNow();
      state.bargeSilenceStart = 0;
      monitorBargeSpeechEnd();
      return;
    }
  } else if (level < state.vadSilenceThreshold) {
    state.bargeSpeechStart = 0;
  }
  state.bargeLoopId = requestAnimationFrame(monitorBargeIn);
}

// --- Monitor for end of user interruption speech (verbatim from index.html:1169–1182) ---
export function monitorBargeSpeechEnd() {
  if (!state.live || !state.handlingBarge) return;
  const level = rmsLevel(); const now = performance.now();
  if (level > state.vadSpeechThreshold) { state.bargeSilenceStart = 0; }
  else if (level < state.vadSilenceThreshold) {
    if (!state.bargeSilenceStart) state.bargeSilenceStart = now;
    if (now - state.bargeSilenceStart > state.endSilenceMs) {
      status($('sttStatus'), 'Interruption finished. Transcribing…', 'warn');
      if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') state.bargeRecorder.stop();
      return;
    }
  }
  state.bargeLoopId = requestAnimationFrame(monitorBargeSpeechEnd);
}

// --- Handle completed barge recording (verbatim from index.html:1183–1219) ---
export async function handleBargeRecording() {
  if (!state.handlingBarge) return;
  let secondBargeStarted = false;
  try {
    const blob = new Blob(state.bargeChunks, { type: state.bargeChunks[0]?.type || 'audio/webm' });
    if (blob.size < 2500) { state.handlingBarge = false; state.interruptContext = ''; state.bargeChunks = []; status($('sttStatus'), 'Interruption was too short. Listening…', 'warn'); return; }
    const j = await transcribeBlob(blob);
    const text = (j.text || '').trim();
    if (!text) { state.handlingBarge = false; state.interruptContext = ''; state.bargeChunks = []; status($('sttStatus'), 'No interruption transcript. Listening…', 'warn'); return; }
    const ctx = state.interruptContext;
    $('transcript').value += (String($('transcript').value).trim() ? '\n' : '') + text;
    $('transcript').scrollTop = $('transcript').scrollHeight;
    log('Interruption point', ctx);

    // Critical: clear the current barge state BEFORE Korina speaks the
    // after-interrupt reply, otherwise that reply cannot itself be interrupted.
    state.handlingBarge = false;
    state.interruptContext = '';
    state.bargeChunks = [];
    state.bargeSpeechStart = 0;
    state.bargeSilenceStart = 0;

    await askAndSpeak(text, ctx);
    secondBargeStarted = state.handlingBarge;
  } catch (e) { status($('sttStatus'), 'Interruption error: ' + e.message, 'bad'); }
  finally {
    // If another interruption started while Korina was answering this one,
    // do not clobber its state or restart the normal live loop underneath it.
    if (state.handlingBarge || secondBargeStarted) return;
    state.ttsSpeaking = false;
    if (state.live) { state.liveBusy = false; status($('sttStatus'), `Live mode armed (${endpointMode()}, ${state.endSilenceMs}ms endpoint). Start speaking…`, 'warn'); EventBus.reset(); EventBus.updateConfig(currentConfig); EventBus._startFirstDeliveryTimer();
      startLiveRecorder(); liveMonitor(); }
  }
}
