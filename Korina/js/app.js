// js/app.js
//
// Frontend entrypoint. Loaded by index.html via <script type="module">.
// Imports every extracted Phase-3 module and re-exports the most-used
// symbols on window so inline onclick handlers (e.g.
// <button onclick="startLive()">) continue to work after extraction.
//
// Phase 3 / Task 3.2.15: this is the final wire-up step. After this
// commit lands, the page should be functional again — loadConfig()
// runs, intervals start, the user can interact with Live Conversation,
// recordings, TTS, and the Korina Agent driver.
//
// Boot order:
//   1. await loadCapabilities().then(() => setBaseUrlEditability())
//      (matches the documented Phase 3 plan; initApp() also runs
//      loadConfig internally so the sequence is consistent.)
//   2. initApp()  (in api.js — does loadConfig, loadAgentModelOptions,
//      loadAcks, health, markActivity, and starts the polling
//      intervals for agent events.)

import { state, EventBus } from "./state.js";
import { $, status, setDot, log, setSectionHidden, clamp } from "./dom.js";
import { prettyModelLabel } from "./labels.js";
import {
  loadConfig, saveConfigNow, saveConfigSoon, health,
  activateSelectedProvider, initApp,
} from "./api.js";
import {
  collectConfig, applyConfig, markActivity,
  syncConverseSettingsUI,
} from "./settings-ui.js";
import {
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  setBaseUrlEditability, maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
} from "./providers-ui.js";
import { loadAcks, preloadAckBuffers, playRandomAck } from "./acks.js";
import { drawWave, rmsLevel, resetAdaptiveVad, updateAdaptiveVad } from "./vad.js";
import { setupMic, makeRecorder, transcribeBlob, transcribePartialBlob } from "./recorder.js";
import {
  startPartialTranscriptionLoop, stopPartialTranscriptionLoop,
  processPartialQueue, waitForPartialQueueIdle,
} from "./partial-queue.js";
import {
  ensureAudio, stopTtsNow, schedulePcm, speakSSE, speakBuffered, speakText,
} from "./speech.js";
import {
  startBargeInCapture, monitorBargeIn, handleBargeRecording,
} from "./barge-in.js";
import {
  startLive, stopLive, liveMonitor, handleLiveTurn, startLiveRecorder,
} from "./live.js";
import {
  stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry,
} from "./history.js";
import {
  deliverTranscriptToAgent, pollAgentEvents, handleAgentEvent,
  interruptConverse, askAndSpeak, askLM, agentDebug,
} from "./agent-ui.js";

// Re-export the most common symbols on window so HTML inline event
// handlers and DevTools debugging keep working.
Object.assign(window, {
  state, EventBus, $, status, setDot, log, setSectionHidden, clamp,
  prettyModelLabel,
  loadConfig, saveConfigNow, saveConfigSoon, health, activateSelectedProvider, initApp,
  collectConfig, applyConfig, markActivity, syncConverseSettingsUI,
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  setBaseUrlEditability, maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
  loadAcks, preloadAckBuffers, playRandomAck,
  drawWave, rmsLevel, resetAdaptiveVad, updateAdaptiveVad,
  setupMic, makeRecorder, transcribeBlob, transcribePartialBlob,
  startPartialTranscriptionLoop, stopPartialTranscriptionLoop,
  processPartialQueue, waitForPartialQueueIdle,
  ensureAudio, stopTtsNow, schedulePcm, speakSSE, speakBuffered, speakText,
  startBargeInCapture, monitorBargeIn, handleBargeRecording,
  startLive, stopLive, liveMonitor, handleLiveTurn, startLiveRecorder,
  stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry,
  deliverTranscriptToAgent, pollAgentEvents, handleAgentEvent,
  interruptConverse, askAndSpeak, askLM, agentDebug,
});

// --- Boot ---
await loadCapabilities().then(() => setBaseUrlEditability());
initApp();
