// Browser entrypoint: settings, recording controls, event wiring, and startup.

import { state } from "./state.js";
import { apiErrorMessage } from './api-errors.js';
import { $, status, setDot, log, setSectionHidden, clamp } from "./dom.js";
import { prettyModelLabel, llmProviderLabel } from "./labels.js";
import {
  loadConfig, saveConfigNow, saveConfigSoon, health, initializeSettingsForm,
  activateSelectedProvider, initApp,
  refreshProviderCapabilities,
  listAudioProbes, clearAudioProbe, normalizeTripleBaseUrl,
} from "./api.js";
import {
  collectConfig, applyConfig, markActivity,
  populateChannelTab, saveChannelSelection,
  syncConverseSettingsUI,
  ttsDevice,
  sttDevice, sttModel, sttBackend,
  llmProvider, llmBaseUrl, lmModel,
  effectiveSttLlmModel, effectiveSttLlmBaseUrl,
  finalSttMode, endpointMode, minSpeechMs, partialWindowMsSetting, llmReasoningEnabled,
} from "./settings-ui.js";
import {
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
  resetSelectedAudioProbe, syncAudioProbeButton,
} from "./providers-ui.js";
import { setCapabilityFilterOverride } from "./capability-filter.js";
import { loadAcks, preloadAckBuffers, playRandomAck, cancelAckPlayback, changeAckVoice } from "./acks.js";
import { drawWave, rmsLevel, resetAdaptiveVad, updateAdaptiveVad } from "./vad.js";
import { setupMic, releaseMic, makeRecorder, transcribeBlob, transcribePartialBlob } from "./recorder.js";
import {
  startPartialTranscriptionLoop, stopPartialTranscriptionLoop,
  processPartialQueue, waitForPartialQueueIdle,
} from "./partial-queue.js";
import {
  ensureAudio, stopTtsNow, schedulePcm, speakSSE, speakBuffered, speakText,
} from "./speech.js";
import {
  startBargeInCapture, monitorBargeIn, handleBargeRecording, cancelBargeIn,
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
  interruptConverse, askAndSpeak, askLM, agentDebug, cancelChatRequests,
  setAgentEnabledFromUi, isPermissionAnswerPending,
} from "./agent-ui.js";
import { openEndpoints, closeEndpoints, populateEndpoints } from "./endpoints.js";

// Expose controls for browser debugging; UI events are bound below.
Object.assign(window, {
  state, $, status, setDot, log, setSectionHidden, clamp,
  prettyModelLabel,
  loadConfig, saveConfigNow, saveConfigSoon, health, activateSelectedProvider, initApp,
  listAudioProbes, clearAudioProbe, normalizeTripleBaseUrl,
  collectConfig, applyConfig, markActivity, syncConverseSettingsUI,
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  setBaseUrlEditability: refreshProviderCapabilities, maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
  setCapabilityFilterOverride,
  loadAcks, preloadAckBuffers, playRandomAck,
  drawWave, rmsLevel, resetAdaptiveVad, updateAdaptiveVad,
  setupMic, releaseMic, makeRecorder, transcribeBlob, transcribePartialBlob,
  startPartialTranscriptionLoop, stopPartialTranscriptionLoop,
  processPartialQueue, waitForPartialQueueIdle,
  ensureAudio, stopTtsNow, schedulePcm, speakSSE, speakBuffered, speakText,
  startBargeInCapture, monitorBargeIn, handleBargeRecording,
  startLive, stopLive, liveMonitor, handleLiveTurn, startLiveRecorder,
  stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry,
  deliverTranscriptToAgent, pollAgentEvents, handleAgentEvent,
  interruptConverse, askAndSpeak, askLM, agentDebug,
  openEndpoints, closeEndpoints, populateEndpoints,
  wireUiHandlers, openSettings, closeSettings, saveSettings,
  startRecording, stopRecording, toggleLive, stopPlayback,
  clearSession,
  wireLocalModelsUi, loadLocalModelRoots,
});


// Bind each UI control explicitly.
function bindClick(id, handler) {
  const el = $(id);
  if (!el) return;
  el.addEventListener('click', handler);
}

function closeSettings() {
  // GUARANTEE: the modal MUST hide immediately, before any save/activate
  // network call. Provider activation can start/reload local model servers
  // (llama.cpp, LM Studio, Ollama) and may take seconds or fail. Waiting for
  // it before removing `.open` trapped users in the settings modal.
  const modal = $('settingsModal');
  const wasOpen = !!modal?.classList.contains('open');
  modal?.classList.remove('open');
  if (!wasOpen) return;

  // Persist + activate in the background. Surface errors in settings/status,
  // but never let them control whether the modal can close.
  void _closeSettingsImpl().catch(e => {
    console.error('closeSettings background apply failed:', e);
    try {
      $('settingsActionStatus').textContent = 'Settings apply failed: ' + (e.message || e);
      status($('sttStatus'), 'Settings apply failed: ' + (e.message || e), 'bad');
    } catch (_) {}
  });
}

let settingsApplyInFlight = null;
let settingsApplyQueued = false;

async function _closeSettingsImpl() {
  settingsApplyQueued = true;
  if (settingsApplyInFlight) return settingsApplyInFlight;
  settingsApplyInFlight = (async () => {
    do {
      settingsApplyQueued = false;
      await saveConfigNow(); // A failed save must prevent provider side effects.
      if (!initializeSettingsForm().loaded) {
        $('settingsActionStatus').textContent = 'Saved settings have not loaded. Open Settings to retry before provider activation.';
        await health();
        continue;
      }
      const response = {
        llm_provider: llmProvider(), lm_model: lmModel(), llm_base_url: llmBaseUrl(),
        llm_reasoning: llmReasoningEnabled(),
      };
      const snap = state.appConfigSnapshot || {};
      const changed = Object.keys(response).some(key => String(response[key] || 'off') !== String(snap[key] || 'off'));
      if (changed) {
        try {
          if (!await refreshProviderCapabilities()) throw new Error('Provider capabilities are unavailable. Reopen Settings to retry.');
          status($('sttStatus'), `Applying settings: activating ${response.llm_provider}…`, 'warn');
          const result = await activateSelectedProvider(response.llm_provider, response.lm_model);
          const activation = result.activation;
          const retained = (activation.retained || []).map(item => `${llmProviderLabel(item.provider)} for ${(item.roles || []).join(', ')}`).join('; ');
          const warnings = (activation.warnings || []).join(' ');
          $('settingsActionStatus').textContent = `Activated ${activation.provider}. Started ${(activation.started || []).join(', ') || 'nothing'}; stopped ${(activation.stopped || []).join(', ') || 'nothing'}.${retained ? ` Kept ${retained}.` : ''}${warnings ? ` ${warnings}` : ''}`;
          state.appConfigSnapshot = { ...snap, ...response };
        } catch (e) {
          throw new Error('Provider activation failed: ' + e.message);
        }
      }
      // STT-only changes are saved without repurposing the response LLM endpoint.
      await health();
    } while (settingsApplyQueued);
  })().finally(() => { settingsApplyInFlight = null; });
  return settingsApplyInFlight;
}

async function openSettings() {
  $('settingsModal')?.classList.add('open');
  syncConverseSettingsUI();
  if (!initializeSettingsForm().loaded) void loadConfig();
  if (!await refreshProviderCapabilities()) return;
  loadAgentModelOptions(false);
}

function saveSettings() {
  // The Done button goes through the same close path as Close/click-outside/Escape
  // so persist+activate semantics are guaranteed to be identical.
  closeSettings();
}

let manualRecordingGeneration = 0;
let manualTranscriptionController = null;

function cancelManualRecording() {
  manualRecordingGeneration++;
  manualTranscriptionController?.abort();
  manualTranscriptionController = null;
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') state.mediaRecorder.stop();
  releaseMic();
  $('recBtn').disabled = false;
  $('stopRecBtn').disabled = true;
}

async function startRecording() {
  if (state.live || $('recBtn').disabled) return;
  const generation = ++manualRecordingGeneration;
  const isCurrent = () => generation === manualRecordingGeneration && !state.live;
  $('recBtn').disabled = true;
  $('stopRecBtn').disabled = false;
  try {
    if (!await setupMic() || !isCurrent()) return;
    makeRecorder(async (metadata = {}) => {
      if (!isCurrent()) return;
      const controller = new AbortController();
      manualTranscriptionController = controller;
      try {
        releaseMic();
        if (metadata.discarded || metadata.error) {
          status($('sttStatus'), metadata.error || 'The recording exceeded its limit. Start a shorter recording.', 'bad');
          return;
        }
        status($('sttStatus'), 'Uploading to faster-whisper streaming STT…', 'warn');
        const blob = new Blob(state.recChunks, { type: state.recChunks[0]?.type || 'audio/webm' });
        const j = await transcribeBlob(blob, { signal: controller.signal, isCurrent });
        if (!isCurrent()) return;
        $('transcript').value = j.text || '';
        const seconds = Number.isFinite(j.seconds) ? j.seconds.toFixed(2) : '0.00';
        const limitMessage = metadata.limitReached ? ' Recording limit reached; start another recording to continue.' : '';
        status($('sttStatus'), `Transcribed in ${seconds}s · ${j.model || 'STT model'}.${limitMessage}`, 'good');
        $('transcriptInfo').textContent = `Manual recording transcribed.${limitMessage}`;
      } catch (e) {
        if (isCurrent() && e.name !== 'AbortError') status($('sttStatus'), 'Transcribe error: ' + e.message, 'bad');
      } finally {
        if (manualTranscriptionController === controller) manualTranscriptionController = null;
        if (isCurrent()) {
          $('recBtn').disabled = false;
          $('stopRecBtn').disabled = true;
        }
      }
    }, 500, { isCurrent });
    $('recBtn').disabled = true;
    $('stopRecBtn').disabled = false;
    status($('sttStatus'), 'Recording…', 'warn');
  } catch (e) {
    if (!isCurrent()) return;
    releaseMic();
    $('recBtn').disabled = false;
    $('stopRecBtn').disabled = true;
    status($('sttStatus'), 'Mic error: ' + e.message, 'bad');
  }
}

function stopRecording() {
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    state.mediaRecorder.stop();
  } else {
    cancelManualRecording();
    status($('sttStatus'), 'Recording cancelled.');
  }
}

function toggleLive() {
  if (state.live) {
    stopLive();
    return;
  }
  cancelManualRecording();
  startLive().catch(e => {
    status($('sttStatus'), 'Live mode error: ' + e.message, 'bad');
  });
}

function stopPlayback() {
  cancelAckPlayback();
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
  state.agentResetBlocked = true;
  cancelChatRequests();
  const resumeLive = state.live;
  if (resumeLive) stopLive();
  cancelBargeIn();
  cancelAckPlayback();
  cancelManualRecording();
  stopPartialTranscriptionLoop(true);
  const clearButton = $('clearSessionBtn');
  clearButton.disabled = true;
  clearButton.textContent = 'Clearing session…';
  try { stopTtsNow(); } catch {}
  state.history = [];
  state.agentStateReport = '';
  state.pendingAgentStateReport = '';
  state.pendingAgentReportVersion++;
  state.agentSession++;
  const session = state.agentSession;
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
  state.partialQueue = [];
  $('log').innerHTML = '';
  $('agentDebug').textContent = 'Session cleared. Agent prompts, injection deliveries, and outputs will appear here.';
  $('transcript').value = '';
  $('text').value = '';
  $('transcriptInfo').textContent = 'Session cleared. Korina writes the transcript here. This box is read-only; queued live chunks append while you speak.';
  $('chunkInfo').textContent = '';
  status($('sttStatus'), 'Clearing session. Agent deliveries are paused until reset completes.', 'warn');
  if (resumeLive) {
    void startLive().catch(error => {
      if (session === state.agentSession) status($('sttStatus'), 'Microphone restart failed: ' + error.message, 'bad');
    });
  }
  try {
    const response = await fetch('/api/agent/reset', { method: 'POST' });
    const result = await response.json();
    if (!response.ok || result?.ok !== true) throw new Error(result?.detail || `HTTP ${response.status}`);
    if (session !== state.agentSession) return;
    state.agentResetBlocked = false;
    clearButton.textContent = 'Clear Session';
    if ($('settingsActionStatus').textContent.startsWith('Agent reset failed:')) $('settingsActionStatus').textContent = '';
    agentDebug('Agent backend reset', { ok: true });
    status($('sttStatus'), 'Session cleared. Live mode can continue with fresh context.', 'good');
  } catch (error) {
    if (session !== state.agentSession) return;
    const message = `Agent reset failed: ${error.message}. Retry Clear Session to resume agent delivery. Conversation can continue.`;
    clearButton.textContent = 'Retry Clear Session';
    $('settingsActionStatus').textContent = message;
    status($('sttStatus'), message, 'bad');
    agentDebug('Agent backend reset failed', error.message || String(error));
  } finally {
    if (session === state.agentSession) clearButton.disabled = false;
  }
  markActivity('session-clear');
}

function wireSettingsInputs() {
  const speed = $('speed');
  if (speed) speed.oninput = () => { $('speedDisplay').textContent = parseFloat(speed.value).toFixed(1) + '×'; saveConfigSoon(); };
  const ackEnabled = $('ackEnabled');
  if (ackEnabled) ackEnabled.onchange = () => {
    if (ackEnabled.value !== 'on') cancelAckPlayback();
    saveConfigSoon();
  };
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
  if (sttDeviceEl) sttDeviceEl.onchange = () => { status($('sttStatus'), `STT device set to ${sttDevice()}. The selected model loads after active transcription finishes; retry if busy.`, 'warn'); health(); saveConfigSoon(); };
  const sttModelEl = $('sttModel');
  if (sttModelEl) sttModelEl.onchange = () => { status($('sttStatus'), `Whisper model set to ${sttModel()}. First use downloads/loads that model.`, 'warn'); health(); saveConfigSoon(); };
  const sttBackendEl = $('sttBackend');
  if (sttBackendEl) sttBackendEl.onchange = () => {
    const msg = sttBackend() === 'llm'
      ? `Multimodal STT selected. Audio transcription now uses ${effectiveSttLlmModel()} @ ${effectiveSttLlmBaseUrl()}. Blank STT fields inherit from LLM Response.`
      : 'Built-in Whisper selected.';
    syncConverseSettingsUI();
    status($('sttStatus'), msg, 'warn');
    $('modelOptionsStatus').textContent = msg;
    health();
    saveConfigSoon();
  };
  const llmProviderEl = $('llmProvider');
  if (llmProviderEl) llmProviderEl.onchange = async () => {
    syncAudioProbeButton();
    if (!await refreshProviderCapabilities()) return;
    maybeApplyProviderPreset('llmProvider', 'llmBaseUrl');
    syncConverseSettingsUI();
    status($('sttStatus'), 'Response provider will be activated when settings close.', 'warn');
    saveConfigSoon();
    await loadModelOptions(true);
  };
  const lmModelEl = $('lmModel');
  if (lmModelEl) lmModelEl.onchange = () => {
    syncConverseSettingsUI();
    syncAudioProbeButton();
    status($('sttStatus'), `Response model set to ${prettyModelLabel(lmModel())}; apply by closing settings.`, 'warn');
    saveConfigSoon();
  };
  for (const id of ['llmReasoningEnabled', 'sttLlmReasoningEnabled']) {
    const control = $(id);
    if (control) control.onchange = () => { syncConverseSettingsUI(); saveConfigSoon(); };
  }
  const sttLlmModelEl = $('sttLlmModel');
  if (sttLlmModelEl) sttLlmModelEl.onchange = () => { syncAudioProbeButton(); status($('sttStatus'), `Multimodal STT model set to ${effectiveSttLlmModel()}.`, 'warn'); health(); saveConfigSoon(); };
  const sttLlmProviderEl = $('sttLlmProvider');
  if (sttLlmProviderEl) sttLlmProviderEl.onchange = async () => { syncAudioProbeButton(); if (!await refreshProviderCapabilities()) return; maybeApplyProviderPreset('sttLlmProvider', 'sttLlmBaseUrl', { clearWhenBlank: true }); syncConverseSettingsUI(); await loadModelOptions(true); health(); saveConfigSoon(); };
  const sttLlmBaseUrlEl = $('sttLlmBaseUrl');
  if (sttLlmBaseUrlEl) sttLlmBaseUrlEl.onchange = async () => { syncAudioProbeButton(); if (!await refreshProviderCapabilities()) return; syncConverseSettingsUI(); await loadModelOptions(true); health(); saveConfigSoon(); };
  const sttLlmApiKeyEnvEl = $('sttLlmApiKeyEnv');
  if (sttLlmApiKeyEnvEl) sttLlmApiKeyEnvEl.onchange = () => { syncAudioProbeButton(); syncConverseSettingsUI(); loadModelOptions(); saveConfigSoon(); };
  const ttsDeviceEl = $('ttsDevice');
  if (ttsDeviceEl) ttsDeviceEl.onchange = () => { status($('ttsStatus'), `TTS device set to ${ttsDevice()}. Next speech will use it. First use may load a second pipeline.`, 'warn'); health(); saveConfigSoon(); };
  [
    'ttsProvider','ttsPort','ttsBaseUrl','ttsHealthUrl','ttsModel','llmBaseUrl','llmApiKeyEnv','llmMaxTokens',
    'agentBaseUrl','agentApiKey','agentModel','agentMaxTurns','agentMaxTokens',
    'agentFirstDeliveryMode',
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
      syncAudioProbeButton();
      health();
      saveConfigSoon();
    };
  });
  if ($('agentProvider')) $('agentProvider').onchange = async () => {
    if (!await refreshProviderCapabilities()) return;
    maybeApplyProviderPreset('agentProvider', 'agentBaseUrl');
    syncConverseSettingsUI();
    health();
    saveConfigSoon();
    await loadAgentModelOptions(true);
  };
  if ($('agentEnabled')) $('agentEnabled').onchange = () => { void setAgentEnabledFromUi(); };
  const voice = $('voice');
  if (voice) voice.onchange = () => {
    void changeAckVoice(saveConfigNow);
    markActivity('voice-change');
  };
  const finalSttModeEl = $('finalSttMode');
  if (finalSttModeEl) finalSttModeEl.onchange = () => { status($('sttStatus'), `Finalization mode: ${finalSttMode() === 'chunks' ? 'live chunks only' : 'full utterance final pass'}.`, 'warn'); saveConfigSoon(); };
  const minSpeechMsEl = $('minSpeechMs');
  if (minSpeechMsEl) minSpeechMsEl.oninput = () => { $('minSpeechMsDisplay').textContent = (minSpeechMs() / 1000).toFixed(1) + 's'; saveConfigSoon(); };
  const partialWindowMsEl = $('partialWindowMs');
  if (partialWindowMsEl) partialWindowMsEl.oninput = () => { state.partialWindowMs = partialWindowMsSetting(); $('partialWindowMsDisplay').textContent = (state.partialWindowMs / 1000).toFixed(1) + 's'; saveConfigSoon(); };
}

let typedAskOwner = null;

function askTyped() {
  // A submitted permission answer owns its confirmation. Repeated clicks must
  // not retire that owner before it records the server's acceptance.
  if (isPermissionAnswerPending()) return typedAskOwner?.task;
  typedAskOwner?.controller.abort();
  const owner = { controller: new AbortController(), session: state.agentSession };
  typedAskOwner = owner;
  owner.task = askAndSpeak($('text').value.trim(), '', { signal: owner.controller.signal })
    .catch(error => {
      if (typedAskOwner === owner && owner.session === state.agentSession && !owner.controller.signal.aborted) {
        status($('sttStatus'), 'LM/TTS error: ' + error.message, 'bad');
      }
    })
    .finally(() => { if (typedAskOwner === owner) typedAskOwner = null; });
  return owner.task;
}

function wireUiHandlers() {
  if (window.__korinaUiHandlersWired) return;
  window.__korinaUiHandlersWired = true;

  bindClick('settingsBtn', openSettings);
  bindClick('closeSettingsBtn', closeSettings);
  bindClick('saveSettingsBtn', saveSettings);
  bindClick('endpointsBtn', openEndpoints);
  bindClick('closeEndpointsBtn', closeEndpoints);
  bindClick('refreshEndpointsBtn', () => populateEndpoints().catch(e => console.warn('populateEndpoints failed:', e)));
  bindClick('refreshAgentModelsBtn', async () => { if (await refreshProviderCapabilities()) await loadAgentModelOptions(); });
  bindClick('clearProbeBtn', resetSelectedAudioProbe);
  bindClick('clearSessionBtn', () => clearSession());
  bindClick('recBtn', () => startRecording());
  bindClick('stopRecBtn', stopRecording);
  bindClick('liveBtn', toggleLive);
  bindClick('playBtn', () => speakText($('text').value, false).catch(() => {}));
  bindClick('askBtn', askTyped);
  bindClick('stopBtn', stopPlayback);

  const modal = $('settingsModal');
  if (modal) modal.addEventListener('click', e => { if (e.target === modal) closeSettings(); });
  const epModal = $('endpointsModal');
  if (epModal) epModal.addEventListener('click', e => { if (e.target === epModal) closeEndpoints(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      // Close whichever modal is open.
      if ($('endpointsModal')?.classList.contains('open')) closeEndpoints();
      else closeSettings();
    }
  });
  document.querySelectorAll('.settingsTab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.settingsTab').forEach(x => x.classList.toggle('active', x === btn));
    $('settingsConverse')?.classList.toggle('active', btn.dataset.settingsTab === 'converse');
    $('settingsAgent')?.classList.toggle('active', btn.dataset.settingsTab === 'agent');
    $('settingsChannel')?.classList.toggle('active', btn.dataset.settingsTab === 'channel');
    if (btn.dataset.settingsTab === 'channel') {
      populateChannelTab().catch((err) => {
        console.warn('populateChannelTab failed:', err);
      });
    }
  }));

  // Wire the Channel dropdown's `change` listener once during initialization
  // so repeated tab clicks do not accumulate duplicate listeners.
  const _channelSelect = $('channelSelect');
  if (_channelSelect && !_channelSelect.__wired) {
    _channelSelect.addEventListener('change', () => saveChannelSelection());
    _channelSelect.__wired = true;
  }
  document.querySelectorAll('.tab[data-mode]').forEach(tab => tab.addEventListener('click', () => {
    state.mode = tab.dataset.mode;
    document.querySelectorAll('.tab[data-mode]').forEach(x => x.classList.toggle('active', x === tab));
    saveConfigSoon();
  }));
  for (const [id, load] of [['lmModel', loadModelOptions], ['sttLlmModel', loadModelOptions],
    ['agentModel', () => loadAgentModelOptions(false)]]) {
    const picker = $(id);
    if (!picker) continue;
    picker.addEventListener('focus', () => { void load(); });
    picker.addEventListener('pointerdown', () => { void load(); });
    picker.addEventListener('keydown', event => {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) void load();
    });
  }
  wireSettingsInputs();
}

// Wire the multimodal STT capability filter override.
const _sttOverride = $('sttCapabilityFilterOverride');
if (_sttOverride) {
  _sttOverride.addEventListener('change', () => {
    setCapabilityFilterOverride(_sttOverride.checked);
    // Reload the model list so the dropdown reflects the change immediately.
    (async () => {
      try { await loadModelOptions(true); } catch { /* best effort */ }
    })();
  });
}

// --- Boot ---
initializeSettingsForm();
wireUiHandlers();
wireLocalModelsUi();
void refreshProviderCapabilities();
void initApp().catch(error => {
  const message = 'Application startup failed: ' + apiErrorMessage(error);
  $('settingsActionStatus').textContent = message;
  status($('sttStatus'), message, 'bad');
});
