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
  listAudioProbes, clearAudioProbe, normalizeTripleBaseUrl,
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
import { setCapabilityFilterOverride } from "./capability-filter.js";
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
  listAudioProbes, clearAudioProbe, normalizeTripleBaseUrl,
  collectConfig, applyConfig, markActivity, syncConverseSettingsUI,
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  setBaseUrlEditability, maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
  setCapabilityFilterOverride,
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

// Wire the multimodal STT capability filter override.
const _sttOverride = $('sttCapabilityFilterOverride');
if (_sttOverride) {
  _sttOverride.addEventListener('change', () => {
    setCapabilityFilterOverride(_sttOverride.checked);
    // Reload the model list so the dropdown reflects the change immediately.
    (async () => {
      try { await loadModelOptions(true); } catch (e) { /* best effort */ }
    })();
  });
}

// Wire clearProbeBtn (Phase 4.5.6 — clear audio probe cache and retry).
const clearBtn = $('clearProbeBtn');
if (clearBtn) {
  clearBtn.addEventListener('click', async () => {
    const { provider, baseUrl, model } = clearBtn.dataset;
    await clearAudioProbe(provider, baseUrl, model);
    clearBtn.style.display = 'none';
    try { await loadModelOptions(true); } catch (_) {}
  });
}

// --- Boot ---
await loadCapabilities().then(() => setBaseUrlEditability());
initApp();
