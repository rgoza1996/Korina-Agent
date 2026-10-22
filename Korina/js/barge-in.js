// Capture speech during playback and resume the conversation after an interruption.

import { state } from "./state.js";
import { $, status, log } from "./dom.js";
import { stopTtsNow, interruptedSpeechContext } from "./speech.js";
import { rmsLevel } from "./vad.js";
import { endpointMode } from "./settings-ui.js";
import { transcribeBlob } from "./recorder.js";
import { askAndSpeak } from "./agent-ui.js";
import { startLiveRecorder, liveMonitor, stopLive } from "./live.js";
import { createBoundedRecorder } from "./bounded-recorder.js";

let currentCapture = null;
let activeDelivery = null;

function sessionIsCurrent(capture) {
  return state.live && capture.liveSession === state.liveSession && capture.agentSession === state.agentSession;
}

function captureIsCurrent(capture) {
  return capture && currentCapture === capture && state.bargeRecorder === capture.recorder && sessionIsCurrent(capture);
}

// Stop/Clear invalidate callbacks immediately; the live owner stops recorders.
export function cancelBargeIn() {
  currentCapture?.recording?.cancel();
  currentCapture = null;
  activeDelivery?.controller.abort();
  activeDelivery = null;
}

// Start barge-in capture during TTS
export function startBargeInCapture() {
  if (!state.live || !state.stream || !state.ttsSpeaking || state.handlingBarge) return;
  try {
    if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') return;
    if (state.bargeLoopId) cancelAnimationFrame(state.bargeLoopId);
    state.bargeLoopId = null;
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    let capture;
    const recording = createBoundedRecorder(state.stream, {
      mimeType: mime,
      isCurrent: () => captureIsCurrent(capture),
      onstop: metadata => {
        if (!captureIsCurrent(capture)) return;
        if (metadata.discarded) {
          stopLive();
          status($('sttStatus'), metadata.error, 'bad');
          return;
        }
        if (!state.handlingBarge && (!metadata.limitReached || !state.bargeSpeechStart)) {
          currentCapture = null;
          recording.cancel();
          state.bargeRecorder = null;
          state.bargeChunks = [];
          if (metadata.limitReached && state.ttsSpeaking) startBargeInCapture();
          return;
        }
        if (metadata.limitReached) {
          if (!state.handlingBarge) {
            state.handlingBarge = true;
            capture.interruptContext = interruptedSpeechContext();
            stopTtsNow();
          }
          capture.limitMessage = metadata.limitReached.message;
          status($('sttStatus'), metadata.limitReached.message, 'warn');
          log('Recording limit', metadata.limitReached.message);
        }
        if (state.handlingBarge) return handleBargeRecording(capture);
      },
    });
    const { recorder, chunks } = recording;
    capture = { recorder, chunks, recording, liveSession: state.liveSession, agentSession: state.agentSession };
    currentCapture = capture;
    state.bargeRecorder = recorder;
    state.bargeChunks = chunks;
    state.bargeSpeechStart = 0;
    state.bargeSilenceStart = 0;
    recording.start();
    monitorBargeIn(capture);
  } catch (error) {
    stopLive();
    status($('sttStatus'), `Interruption recording failed: ${error.message} Try starting live mode again.`, 'bad');
  }
}

// Monitor for user speech during TTS
export function monitorBargeIn(capture = currentCapture) {
  if (!captureIsCurrent(capture) || !state.ttsSpeaking || state.handlingBarge) return;
  const level = rmsLevel(); const now = performance.now();
  if (level > state.vadSpeechThreshold) {
    if (!state.bargeSpeechStart) state.bargeSpeechStart = now;
    if (now - state.bargeSpeechStart > 220) {
      activeDelivery?.controller.abort();
      activeDelivery = null;
      state.handlingBarge = true;
      state.interruptContext = interruptedSpeechContext();
      capture.interruptContext = state.interruptContext;
      status($('sttStatus'), 'User interruption detected. Korina stopped; listening to interruption…', 'warn');
      stopTtsNow();
      state.bargeSilenceStart = 0;
      monitorBargeSpeechEnd(capture);
      return;
    }
  } else if (level < state.vadSilenceThreshold) {
    state.bargeSpeechStart = 0;
  }
  state.bargeLoopId = requestAnimationFrame(() => monitorBargeIn(capture));
}

// Monitor for end of user interruption speech
export function monitorBargeSpeechEnd(capture = currentCapture) {
  if (!captureIsCurrent(capture) || !state.handlingBarge) return;
  const level = rmsLevel(); const now = performance.now();
  if (level > state.vadSpeechThreshold) { state.bargeSilenceStart = 0; }
  else if (level < state.vadSilenceThreshold) {
    if (!state.bargeSilenceStart) state.bargeSilenceStart = now;
    if (now - state.bargeSilenceStart > state.endSilenceMs) {
      status($('sttStatus'), 'Interruption finished. Transcribing…', 'warn');
      if (capture.recorder.state !== 'inactive') capture.recorder.stop();
      return;
    }
  }
  state.bargeLoopId = requestAnimationFrame(() => monitorBargeSpeechEnd(capture));
}

// Handle completed barge recording
export async function handleBargeRecording(capture = currentCapture) {
  if (!capture) {
    capture = {
      recorder: state.bargeRecorder, chunks: state.bargeChunks,
      liveSession: state.liveSession, agentSession: state.agentSession,
    };
    currentCapture = capture;
  }
  if (!captureIsCurrent(capture) || !state.handlingBarge) return;
  activeDelivery?.controller.abort();
  const delivery = { capture, controller: new AbortController() };
  activeDelivery = delivery;
  const isCurrent = () => activeDelivery === delivery && sessionIsCurrent(capture);
  const context = capture.interruptContext ?? state.interruptContext;
  const previousTranscript = String($('transcript').value).trim();
  let preserveStatus = false;

  function clearCapture() {
    if (currentCapture === capture) currentCapture = null;
    capture.recording?.cancel();
    if (state.bargeRecorder === capture.recorder) state.bargeRecorder = null;
    state.handlingBarge = false;
    state.interruptContext = '';
    state.bargeChunks = [];
    state.bargeSpeechStart = 0;
    state.bargeSilenceStart = 0;
  }

  try {
    const blob = new Blob(capture.chunks, { type: capture.chunks[0]?.type || 'audio/webm' });
    if (blob.size < 2500) {
      clearCapture();
      preserveStatus = true;
      status($('sttStatus'), 'Interruption was too short. Listening…', 'warn');
      return;
    }
    const j = await transcribeBlob(blob, { signal: delivery.controller.signal, isCurrent });
    if (!isCurrent()) return;
    const text = (j.text || '').trim();
    if (!text) {
      clearCapture();
      preserveStatus = true;
      status($('sttStatus'), 'No interruption transcript. Listening…', 'warn');
      return;
    }
    $('transcript').value = (previousTranscript ? previousTranscript + '\n' : '') + text;
    $('transcript').scrollTop = $('transcript').scrollHeight;
    if (capture.limitMessage) $('transcriptInfo').textContent = capture.limitMessage;
    log('Interruption point', context);

    // Critical: clear the current barge state BEFORE Korina speaks the
    // after-interrupt reply, otherwise that reply cannot itself be interrupted.
    clearCapture();
    await askAndSpeak(text, context, { isCurrent, signal: delivery.controller.signal });
  } catch (e) {
    if (!isCurrent()) return;
    clearCapture();
    if (e.name !== 'AbortError') {
      preserveStatus = true;
      status($('sttStatus'), 'Interruption error: ' + e.message, 'bad');
    }
  }
  finally {
    // If another interruption started while Korina was answering this one,
    // do not clobber its state or restart the normal live loop underneath it.
    if (isCurrent() && !state.handlingBarge) {
      state.ttsSpeaking = false;
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
    if (activeDelivery === delivery) activeDelivery = null;
  }
}
