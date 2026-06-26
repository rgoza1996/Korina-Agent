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
import { prettyModelLabel, ttsProviderLabel, llmProviderLabel } from "./labels.js";
import {
  loadConfig, saveConfigNow, saveConfigSoon, health,
  activateSelectedProvider, initApp,
  listAudioProbes, clearAudioProbe, normalizeTripleBaseUrl,
} from "./api.js";
import {
  collectConfig, applyConfig, markActivity,
  syncConverseSettingsUI,
  ttsDevice, ttsProvider, ttsBaseUrl,
  sttDevice, sttModel, sttBackend,
  llmProvider, llmBaseUrl, lmModel,
  sttLlmModel, effectiveSttLlmModel, effectiveSttLlmBaseUrl,
  finalSttMode, endpointMode, minSpeechMs, partialWindowMsSetting,
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
import { wireLocalModelsUi, loadLocalModelRoots } from "./local-models.js";
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
  wireUiHandlers, openSettings, closeSettings, saveSettings,
  startRecording, stopRecording, toggleLive, stopPlayback,
  clearSession,
  wireLocalModelsUi, loadLocalModelRoots,
});


// --- UI event wiring ---
// Phase 3 extracted the inline script into ES modules. The functions were
// re-exported on window, but the static HTML no longer has inline onclick
// attributes. Bind the controls explicitly so clicks work in normal browsers
// and in headless Playwright tests.
function bindClick(id, handler) {
  const el = $(id);
  if (!el) return;
  el.addEventListener('click', handler);
}

async function closeSettings() {
  // Persist any unsaved field changes first. saveConfigNow() is a no-op
  // if the form values already match what's on disk; the activation
  // diff below is what we actually care about.
  try {
    await saveConfigNow();
  } catch (e) {
    console.warn('saveConfigNow failed during modal close:', e);
  }

  // Compare provider-affecting form fields against the last-loaded
  // config snapshot. If anything that changes which server / model is
  // serving requests has changed, run /api/llm/provider/activate so the
  // running backend reflects the new choice.
  const snap = state.appConfigSnapshot || {};
  const llmChanged =
    llmProvider() !== (snap.llm_provider || '') ||
    lmModel() !== (snap.lm_model || '') ||
    llmBaseUrl() !== (snap.llm_base_url || '');
  const sttChanged =
    effectiveSttLlmProvider() !== (snap.stt_llm_provider || '') ||
    effectiveSttLlmBaseUrl() !== (snap.stt_llm_base_url || '') ||
    sttLlmModel() !== (snap.stt_llm_model || '');

  if (llmChanged || sttChanged) {
    try {
      const provider = sttChanged && !llmChanged ? effectiveSttLlmProvider() : llmProvider();
      const model = sttChanged && !llmChanged ? effectiveSttLlmModel() : lmModel();
      status($('sttStatus'), `Applying settings: activating ${provider}…`, 'warn');
      const j = await activateSelectedProvider(provider, model);
      $('settingsInfo').textContent = `Activated ${j.activation.provider}. Started ${j.activation.started.join(', ') || 'nothing'}; stopped ${j.activation.stopped.join(', ') || 'nothing'}.`;
      // Refresh the snapshot so subsequent closes don't re-activate
      // the same change.
      state.appConfigSnapshot = {
        llm_provider: llmProvider(),
        lm_model: lmModel(),
        llm_base_url: llmBaseUrl(),
        stt_llm_provider: effectiveSttLlmProvider(),
        stt_llm_base_url: effectiveSttLlmBaseUrl(),
        stt_llm_model: sttLlmModel(),
        tts_provider: ttsProvider(),
        tts_base_url: String($('ttsBaseUrl')?.value || '').trim(),
        tts_port: ttsPort(),
        tts_model: ttsModel(),
      };
    } catch (e) {
      $('settingsInfo').textContent = 'Provider activation failed: ' + e.message;
      status($('sttStatus'), 'Provider activation failed: ' + e.message, 'bad');
      console.warn('activateSelectedProvider failed during modal close:', e);
    }
  }

  // Hide the modal last so the user is never trapped by a thrown error.
  $('settingsModal')?.classList.remove('open');

  // Refresh the debug strip so the pills reflect the new state.
  try { await health(); } catch (e) { console.warn('health refresh failed:', e); }
}

async function openSettings() {
  $('settingsModal')?.classList.add('open');
  syncConverseSettingsUI();
  await setBaseUrlEditability();
  loadAgentModelOptions();
}

function saveSettings() {
  // The Done button goes through the same close path as Close/click-outside/Escape
  // so persist+activate semantics are guaranteed to be identical.
  closeSettings();
}

async function startRecording() {
  try {
    await setupMic();
    makeRecorder(async () => {
      try {
        if (state.raf) cancelAnimationFrame(state.raf);
        if (state.stream) {
          state.stream.getTracks().forEach(t => t.stop());
          state.stream = null;
        }
        status($('sttStatus'), 'Uploading to faster-whisper streaming STT…', 'warn');
        const blob = new Blob(state.recChunks, { type: state.recChunks[0]?.type || 'audio/webm' });
        const j = await transcribeBlob(blob);
        $('transcript').value = j.text || '';
        const seconds = Number.isFinite(j.seconds) ? j.seconds.toFixed(2) : '0.00';
        status($('sttStatus'), `Transcribed in ${seconds}s · ${j.model || 'STT model'}`, 'good');
        $('transcriptInfo').textContent = 'Manual recording transcribed.';
      } catch (e) {
        status($('sttStatus'), 'Transcribe error: ' + e.message, 'bad');
      } finally {
        $('recBtn').disabled = false;
        $('stopRecBtn').disabled = true;
      }
    });
    $('recBtn').disabled = true;
    $('stopRecBtn').disabled = false;
    status($('sttStatus'), 'Recording…', 'warn');
  } catch (e) {
    status($('sttStatus'), 'Mic error: ' + e.message, 'bad');
  }
}

function stopRecording() {
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    state.mediaRecorder.stop();
  }
}

function toggleLive() {
  if (state.live) {
    stopLive();
    return;
  }
  startLive().catch(e => {
    stopLive();
    status($('sttStatus'), 'Live mode error: ' + e.message, 'bad');
  });
}

function stopPlayback() {
  stopTtsNow();
  if (state.bargeLoopId) cancelAnimationFrame(state.bargeLoopId);
  state.bargeLoopId = null;
  if (state.bargeRecorder && state.bargeRecorder.state !== 'inactive') state.bargeRecorder.stop();
  if (state.ackSource) {
    try { state.ackSource.stop(); } catch {}
    state.ackSource = null;
  }
  if (state.audioCtx) {
    try { state.audioCtx.close(); } catch {}
    state.audioCtx = null;
    state.ackBuffers = [];
    state.ackBuffersByTag = {};
  }
  $('audio')?.pause();
  status($('ttsStatus'), 'Stopped');
}

async function clearSession() {
  try { stopTtsNow(); } catch {}
  state.history = [];
  state.agentStateReport = '';
  state.agentLastTranscriptHash = '';
  state.pendingAgentStateReport = '';
  state.pendingImportantAgentMessage = '';
  state.awaitingAgentPermission = null;
  state.agentEventCursor = 0;
  state.agentTurnCount = 0;
  state.agentLastDeliveredTurn = 0;
  state.agentFirstDelivered = false;
  state.agentStartedAt = performance.now();
  state.agentLastDeliveryAt = 0;
  state.suppressAckUntil = 0;
  state.agentInterruptCooldownUntil = 0;
  state.deferredAgentInterrupt = null;
  state.agentInterruptInProgress = false;
  state.latestPartialText = '';
  state.latestPartialAt = 0;
  state.partialQueue = [];
  $('log').innerHTML = '';
  $('agentDebug').textContent = 'Session cleared. Agent prompts, injection deliveries, and outputs will appear here.';
  $('transcript').value = '';
  $('text').value = '';
  $('transcriptInfo').textContent = 'Session cleared. Korina writes the transcript here. This box is read-only; queued live chunks append while you speak.';
  $('chunkInfo').textContent = '';
  status($('sttStatus'), 'Session cleared. Live mode can continue with fresh context.', 'good');
  try {
    await fetch('/api/agent/reset', { method: 'POST' });
    agentDebug('Agent backend reset', { ok: true });
  } catch (e) {
    agentDebug('Agent backend reset failed', String(e));
  }
  markActivity('session-clear');
}

function wireSettingsInputs() {
  const speed = $('speed');
  if (speed) speed.oninput = () => { $('speedDisplay').textContent = parseFloat(speed.value).toFixed(1) + '×'; saveConfigSoon(); };
  const ackEnabled = $('ackEnabled');
  if (ackEnabled) ackEnabled.onchange = () => saveConfigSoon();
  const silenceMs = $('silenceMs');
  if (silenceMs) silenceMs.oninput = () => { state.endSilenceMs = parseInt(silenceMs.value, 10); $('silenceDisplay').textContent = state.endSilenceMs + 'ms'; saveConfigSoon(); };
  const endpoint = $('endpointMode');
  if (endpoint) endpoint.onchange = () => {
    state.endSilenceMs = endpointMode() === 'reading' ? 3200 : 950;
    $('silenceMs').value = state.endSilenceMs;
    $('silenceDisplay').textContent = state.endSilenceMs + 'ms';
    status($('sttStatus'), `Endpointing mode: ${endpointMode()} (${state.endSilenceMs}ms silence before Korina responds).`, 'warn');
    saveConfigSoon();
  };
  const sttDeviceEl = $('sttDevice');
  if (sttDeviceEl) sttDeviceEl.onchange = () => { status($('sttStatus'), `STT device set to ${sttDevice()}. Next transcription will use it. First use may load a second model.`, 'warn'); health(); saveConfigSoon(); };
  const sttModelEl = $('sttModel');
  if (sttModelEl) sttModelEl.onchange = () => { status($('sttStatus'), `Whisper model set to ${sttModel()}. First use downloads/loads that model.`, 'warn'); health(); saveConfigSoon(); };
  const sttBackendEl = $('sttBackend');
  if (sttBackendEl) sttBackendEl.onchange = () => {
    const msg = sttBackend() === 'llm'
      ? `Multimodal STT selected. Audio transcription now uses ${effectiveSttLlmModel()} @ ${effectiveSttLlmBaseUrl()}. Blank STT fields inherit from LLM Response.`
      : 'Built-in Whisper selected.';
    syncConverseSettingsUI();
    status($('sttStatus'), msg, 'warn');
    $('settingsInfo').textContent = msg;
    health();
    saveConfigSoon();
  };
  const llmProviderEl = $('llmProvider');
  if (llmProviderEl) llmProviderEl.onchange = async () => {
    maybeApplyProviderPreset('llmProvider', 'llmBaseUrl');
    await setBaseUrlEditability();
    syncConverseSettingsUI();
    status($('sttStatus'), `Switching response provider to ${llmProvider()}…`, 'warn');
    await saveConfigNow();
    try {
      const j = await activateSelectedProvider(llmProvider(), lmModel());
      $('settingsInfo').textContent = `Activated ${j.activation.provider}. Started ${j.activation.started.join(', ') || 'nothing'}; stopped ${j.activation.stopped.join(', ') || 'nothing'}.`;
    } catch (e) {
      $('settingsInfo').textContent = 'Provider activation failed: ' + e.message;
      status($('sttStatus'), 'Provider activation failed: ' + e.message, 'bad');
    }
    await loadModelOptions(true);
    await loadAgentModelOptions();
    health();
    saveConfigSoon();
  };
  const lmModelEl = $('lmModel');
  if (lmModelEl) lmModelEl.onchange = async () => {
    if (!sttLlmModel()) syncConverseSettingsUI();
    status($('sttStatus'), `LLM response model set to ${prettyModelLabel(lmModel())}.`, 'warn');
    await saveConfigNow();
    if (llmProvider() === 'llama.cpp') {
      try {
        const j = await activateSelectedProvider('llama.cpp', lmModel());
        $('settingsInfo').textContent = `Reloaded llama.cpp with ${prettyModelLabel(j.activation.model)}.`;
        await loadModelOptions(true);
      } catch (e) {
        $('settingsInfo').textContent = 'llama.cpp reload failed: ' + e.message;
        status($('sttStatus'), 'llama.cpp reload failed: ' + e.message, 'bad');
      }
    }
    saveConfigSoon();
  };
  const sttLlmModelEl = $('sttLlmModel');
  if (sttLlmModelEl) sttLlmModelEl.onchange = () => { status($('sttStatus'), `Multimodal STT model set to ${effectiveSttLlmModel()}.`, 'warn'); health(); saveConfigSoon(); };
  const sttLlmProviderEl = $('sttLlmProvider');
  if (sttLlmProviderEl) sttLlmProviderEl.onchange = async () => { maybeApplyProviderPreset('sttLlmProvider', 'sttLlmBaseUrl', { clearWhenBlank: true }); await setBaseUrlEditability(); syncConverseSettingsUI(); await loadModelOptions(true); health(); saveConfigSoon(); };
  const sttLlmBaseUrlEl = $('sttLlmBaseUrl');
  if (sttLlmBaseUrlEl) sttLlmBaseUrlEl.onchange = async () => { await setBaseUrlEditability(); syncConverseSettingsUI(); await loadModelOptions(true); health(); saveConfigSoon(); };
  const sttLlmApiKeyEnvEl = $('sttLlmApiKeyEnv');
  if (sttLlmApiKeyEnvEl) sttLlmApiKeyEnvEl.onchange = () => { syncConverseSettingsUI(); loadModelOptions(); saveConfigSoon(); };
  const ttsDeviceEl = $('ttsDevice');
  if (ttsDeviceEl) ttsDeviceEl.onchange = () => { status($('ttsStatus'), `TTS device set to ${ttsDevice()}. Next speech will use it. First use may load a second pipeline.`, 'warn'); health(); saveConfigSoon(); };
  [
    'ttsProvider','ttsPort','ttsBaseUrl','ttsModel','llmBaseUrl','llmApiKeyEnv',
    'sttCloudBaseUrl','sttCloudModel','sttApiKeyEnv','agentEnabled','agentProvider',
    'agentBaseUrl','agentApiKey','agentModel','agentMaxTurns','agentMaxTokens',
    'agentYoloMode','agentProjectTrust','agentInjectionMode','agentFollowUpMode',
    'agentThinkingLevel','agentAutoCompact','agentCompactionReserveTokens',
    'agentCompactionKeepRecentTokens','agentHideThinking','agentTransport',
    'agentRetryEnabled','agentMaxRetries','agentRetryBaseDelayMs','agentHttpIdleTimeoutMs',
    'agentEnableSkillCommands','agentBlockImages','agentFirstDeliveryMode',
    'agentFirstDeliverySeconds','agentPeriodicDeliveryTurns','agentPeriodicDeliverySeconds',
    'agentBusyDeliveryMode','agentIdleDeliveryMode','agentInterruptsEnabled',
    'agentInterruptMinPriority','agentHardInterruptMinPriority','agentInterruptCooldownPaddingMs',
    'agentPermissionInterrupts','agentReportInjectionMode',
  ].forEach(id => {
    const el = $(id);
    if (el) el.onchange = () => {
      // hostInfo is now owned by api.js:health() and updated every 5s
      // from server-resolved URLs (Bug C / #19).
      syncConverseSettingsUI();
      health();
      saveConfigSoon();
    };
  });
  const voice = $('voice');
  if (voice) voice.onchange = async () => {
    state.ackFiles = [];
    state.ackBuffers = [];
    state.ackBuffersByTag = {};
    status($('sttStatus'), `Voice changed to ${voice.value}. Clearing old ack cache and queueing fresh acks for this voice…`, 'warn');
    try {
      const r = await fetch('/api/acks/rebuild', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice: voice.value, clear: true }) });
      const j = await r.json();
      $('transcriptInfo').textContent = `Ack rebuild for ${j.voice}: removed ${j.removed}, missing/queued ${j.queued_or_missing}.`;
    } catch (e) { console.warn('ack rebuild failed', e); }
    saveConfigSoon();
    markActivity('voice-change');
    setTimeout(() => loadAcks('global'), 1500);
  };
  const finalSttModeEl = $('finalSttMode');
  if (finalSttModeEl) finalSttModeEl.onchange = () => { status($('sttStatus'), `Finalization mode: ${finalSttMode() === 'chunks' ? 'live chunks only' : 'full utterance final pass'}.`, 'warn'); saveConfigSoon(); };
  const minSpeechMsEl = $('minSpeechMs');
  if (minSpeechMsEl) minSpeechMsEl.oninput = () => { $('minSpeechMsDisplay').textContent = (minSpeechMs() / 1000).toFixed(1) + 's'; saveConfigSoon(); };
  const partialWindowMsEl = $('partialWindowMs');
  if (partialWindowMsEl) partialWindowMsEl.oninput = () => { state.partialWindowMs = partialWindowMsSetting(); $('partialWindowMsDisplay').textContent = (state.partialWindowMs / 1000).toFixed(1) + 's'; saveConfigSoon(); };
}

function wireUiHandlers() {
  if (window.__korinaUiHandlersWired) return;
  window.__korinaUiHandlersWired = true;

  bindClick('settingsBtn', openSettings);
  bindClick('closeSettingsBtn', closeSettings);
  bindClick('saveSettingsBtn', saveSettings);
  bindClick('refreshAgentModelsBtn', () => loadAgentModelOptions());
  bindClick('clearSessionBtn', () => clearSession());
  bindClick('recBtn', () => startRecording());
  bindClick('stopRecBtn', stopRecording);
  bindClick('liveBtn', toggleLive);
  bindClick('playBtn', () => speakText($('text').value, false).catch(() => {}));
  bindClick('askBtn', () => askAndSpeak($('text').value.trim()).catch(e => status($('sttStatus'), 'LM/TTS error: ' + e.message, 'bad')));
  bindClick('stopBtn', stopPlayback);

  const modal = $('settingsModal');
  if (modal) modal.addEventListener('click', e => { if (e.target === modal) closeSettings(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSettings(); });
  document.querySelectorAll('.settingsTab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.settingsTab').forEach(x => x.classList.toggle('active', x === btn));
    $('settingsConverse')?.classList.toggle('active', btn.dataset.settingsTab === 'converse');
    $('settingsAgent')?.classList.toggle('active', btn.dataset.settingsTab === 'agent');
  }));
  document.querySelectorAll('.tab[data-mode]').forEach(tab => tab.addEventListener('click', () => {
    state.mode = tab.dataset.mode;
    document.querySelectorAll('.tab[data-mode]').forEach(x => x.classList.toggle('active', x === tab));
    saveConfigSoon();
  }));
  wireSettingsInputs();
}

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
wireUiHandlers();
wireLocalModelsUi();
initApp();
