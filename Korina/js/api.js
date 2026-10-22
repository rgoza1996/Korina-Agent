// Backend API client, runtime health rendering, and application bootstrap.

import { state } from './state.js';
import { apiErrorMessage } from './api-errors.js';
import { ttsProviderLabel, llmProviderLabel } from './labels.js';
import { $, setDot, status } from './dom.js';
import {
  applyConfig, collectConfig, syncConverseSettingsUI,
  sttBackend,
  sttDevice, sttModel,
  effectiveSttLlmModel, effectiveSttLlmBaseUrl,
  sttLlmReasoningEnabled,
  llmReasoningEnabled,
  llmProvider, llmBaseUrl, lmModel,
  markActivity,
} from './settings-ui.js';
import { loadAgentModelOptions, loadCapabilities, setBaseUrlEditability, syncAudioProbeButton } from './providers-ui.js';

let capabilitiesInFlight = null;
const CAPABILITIES_FAILURE = 'Provider capabilities unavailable: ';
let healthPollVersion = 0;

export function refreshProviderCapabilities() {
  if (capabilitiesInFlight) return capabilitiesInFlight;
  capabilitiesInFlight = (async () => {
    try {
      const registry = await loadCapabilities();
      if (!registry?.providers || typeof registry.providers !== 'object' || Array.isArray(registry.providers)
        || !Object.keys(registry.providers).length
        || !registry.agentProviders || typeof registry.agentProviders !== 'object' || Array.isArray(registry.agentProviders)) {
        throw new Error('The server returned an invalid provider registry');
      }
      // Read current selections after the request; a delayed initial reply
      // must not apply editability for an older form selection.
      await setBaseUrlEditability();
      const action = $('settingsActionStatus');
      if (action?.textContent.startsWith(CAPABILITIES_FAILURE)) action.textContent = '';
      const stt = $('sttStatus');
      if (stt?.textContent.startsWith(CAPABILITIES_FAILURE)) status(stt, 'Provider capabilities loaded.', 'good');
      return true;
    } catch (error) {
      state._capabilitiesCache = null;
      const message = apiErrorMessage({ detail: `${CAPABILITIES_FAILURE}${apiErrorMessage(error)}. Open Settings to retry.` });
      const action = $('settingsActionStatus');
      if (action) action.textContent = message;
      const stt = $('sttStatus');
      if (stt) status(stt, message, 'bad');
      return false;
    } finally {
      capabilitiesInFlight = null;
    }
  })();
  return capabilitiesInFlight;
}

export async function activateSelectedProvider(provider, model = '') {
  healthPollVersion++;
  try {
    const r = await fetch('/api/llm/provider/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model: model || '' }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(apiErrorMessage(j, `HTTP ${r.status}`));
    if (j.saved) state.appConfig = { ...state.appConfig, ...j.saved };
    // Signal a pending provider switch so the readiness pill flips to
    // "loading" until /api/health confirms the new model is loaded.
    state.providerPending = true;
    return j;
  } finally {
    // A same-selection reload (including a partial failure) can change the
    // running server without changing the selected configuration identity.
    healthPollVersion++;
  }
}

const sameSetting = (left, right) => JSON.stringify(left) === JSON.stringify(right);

export function initializeSettingsForm() {
  if (!state.settingsBootstrap) state.settingsBootstrap = {
    observed: collectConfig(), dirty: {}, confirmed: {}, writeRevision: 0, loaded: false,
  };
  return state.settingsBootstrap;
}

function observeSettingsForm() {
  const bootstrap = initializeSettingsForm();
  const current = collectConfig();
  for (const [key, value] of Object.entries(current)) {
    if (key !== 'agent_enabled' && !sameSetting(value, bootstrap.observed[key])) bootstrap.dirty[key] = value;
  }
  bootstrap.observed = current;
  return bootstrap;
}

function clearSavedEdits(config) {
  const bootstrap = initializeSettingsForm();
  for (const [key, value] of Object.entries(config)) {
    if (sameSetting(value, bootstrap.dirty[key])) delete bootstrap.dirty[key];
  }
}

let configReadInFlight = null;
export function loadConfig() {
  if (!configReadInFlight) configReadInFlight = readConfig().finally(() => { configReadInFlight = null; });
  return configReadInFlight;
}

// A late read may fill untouched fields, but cannot replace local edits or
// values confirmed by a newer successful save.
async function readConfig() {
  const bootstrap = initializeSettingsForm();
  const writeRevision = bootstrap.writeRevision;
  const activeSnapshot = state.appConfigSnapshot;
  const controlVersion = agentControlVersion;
  try {
    const response = await fetch('/api/config');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const c = await response.json();
    if (!c || typeof c !== 'object' || Array.isArray(c) || c.ok === false || Object.hasOwn(c, 'detail')) {
      throw new Error('The server returned invalid settings.');
    }
    if (state.settingsBootstrap !== bootstrap) return false;
    observeSettingsForm();
    const current = { ...c };
    for (const [key, confirmed] of Object.entries(bootstrap.confirmed)) {
      if (confirmed.revision > writeRevision) current[key] = confirmed.value;
    }
    if (controlVersion !== agentControlVersion || state.agentControlPending) delete current.agent_enabled;
    applyConfig(current, { preserveKeys: Object.keys(bootstrap.dirty),
      updateSnapshot: state.appConfigSnapshot === activeSnapshot, snapshot: c });
    bootstrap.observed = collectConfig();
    bootstrap.loaded = true;
    syncConverseSettingsUI();
    // Config and capabilities load independently. Re-read these selections
    // even when an overlapping capabilities refresh already applied its UI.
    if (state._capabilitiesCache) await setBaseUrlEditability();
    $('settingsInfo').textContent = 'Loaded saved config.json settings.';
    const action = $('settingsActionStatus');
    if (action.textContent.startsWith('Settings load failed:')
      || action.textContent === 'Saved settings have not loaded. Open Settings to retry before provider activation.') action.textContent = '';
    if ($('sttStatus').textContent.startsWith('Settings load failed:')) status($('sttStatus'), 'Saved settings loaded.', 'good');
    return true;
  } catch (e) {
    if (!bootstrap.loaded) $('settingsInfo').textContent = 'Saved settings have not loaded. Open Settings to retry.';
    $('settingsActionStatus').textContent = `Settings load failed: ${e.message}`;
    status($('sttStatus'), `Settings load failed: ${e.message}`, 'bad');
    console.warn('config load failed', e);
    return false;
  }
}

let saveInFlight = null;
let queuedConfig = null;
const agentControlQueue = [];
let agentControlVersion = 0;

// Serialize saves and coalesce rapid edits into the latest form snapshot.
function flushConfigSaves() {
  if (!saveInFlight) {
    let completed = false;
    saveInFlight = (async () => {
      while (agentControlQueue.length || queuedConfig) {
        const control = agentControlQueue[0];
        const config = control || queuedConfig;
        if (!control) queuedConfig = null;
        if (!Object.keys(config).some(key => JSON.stringify(config[key]) !== JSON.stringify(state.appConfig[key]))) {
          clearSavedEdits(config);
          if (control) agentControlQueue.shift();
          continue;
        }
        const response = await fetch('/api/config', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(apiErrorMessage(result, `Settings save failed: HTTP ${response.status}`));
        const saved = result?.saved || result;
        if (!saved || typeof saved !== 'object' || Array.isArray(saved) || saved.ok === false) {
          throw new Error('The server returned an invalid settings response.');
        }
        const bootstrap = observeSettingsForm();
        bootstrap.writeRevision++;
        for (const [key, value] of Object.entries(saved)) bootstrap.confirmed[key] = { value, revision: bootstrap.writeRevision };
        state.appConfig = { ...state.appConfig, ...saved };
        clearSavedEdits(config);
        // The server may move following STT/agent fields with the response
        // model. Reconcile confirmed values without replacing newer edits or
        // advancing the last activated provider's baseline.
        if (Object.entries(saved).some(([key, value]) => Object.hasOwn(bootstrap.observed, key)
          && !(key === 'agent_enabled' && state.agentControlPending)
          && !Object.hasOwn(bootstrap.dirty, key) && !sameSetting(value, bootstrap.observed[key]))) {
          applyConfig(saved, { preserveKeys: Object.keys(bootstrap.dirty), updateSnapshot: false });
          syncAudioProbeButton();
          if (state._capabilitiesCache) await setBaseUrlEditability();
        }
        bootstrap.observed = collectConfig();
        if (control) agentControlQueue.shift();
      }
      return state.appConfig;
    })().then(result => {
      completed = true;
      return result;
    }).finally(() => {
      saveInFlight = null;
      if (completed && (agentControlQueue.length || queuedConfig)) return flushConfigSaves();
    });
  }
  return saveInFlight;
}

export function saveConfigNow() {
  clearTimeout(state.saveConfigTimer);
  state.saveConfigTimer = null;
  queuedConfig = { ...observeSettingsForm().dirty };
  return flushConfigSaves();
}

// Explicit enable transitions are never coalesced: an off/on burst must
// persist the off transition that cancels old work before enabling again.
export function saveAgentEnabled(enabled) {
  agentControlVersion++;
  agentControlQueue.push({ agent_enabled: enabled ? 'on' : 'off' });
  return flushConfigSaves();
}

export function saveConfigSoon() {
  observeSettingsForm();
  clearTimeout(state.saveConfigTimer);
  state.saveConfigTimer = setTimeout(() => {
    saveConfigNow().catch(error => {
      $('settingsActionStatus').textContent = `Settings save failed: ${error.message}`;
      status($('sttStatus'), `Settings save failed: ${error.message}`, 'bad');
      console.warn('config save failed', error);
    });
  }, 250);
}

// health — refactored for Commit 2 of the debug-strip audit.
//
// Single source of truth: /api/health (server-resolved URLs).
// - ttsHealth reads j.tts.base_url and j.tts.{loaded,error,provider}.
// - pageHealth flips red only after 2-3 consecutive poll failures
//   (state.healthFailures), so a single 5s blip does not alarm.
// - hostInfo is owned here (every 5s), not in form change handlers.
// - settingsLiveStatus reads the runtime provider load blocks.
const HEALTH_FAIL_GRACE = 3; // consecutive misses before flipping pageDot red
const HEALTH_SETTING_KEYS = [
  'llm_provider', 'llm_base_url', 'lm_model', 'llm_api_key_env',
  'stt_backend', 'stt_device', 'stt_model', 'stt_llm_provider',
  'stt_llm_base_url', 'stt_llm_model', 'stt_llm_api_key_env', 'partial_window_ms',
  'tts_provider', 'tts_base_url', 'tts_port', 'tts_health_url', 'tts_model', 'tts_device',
  'agent_enabled', 'agent_provider', 'agent_base_url', 'agent_model', 'agent_api_key',
];

function healthIdentity() {
  const form = collectConfig();
  return JSON.stringify([state.liveSession, state.agentSession,
    HEALTH_SETTING_KEYS.map(key => [form[key], state.appConfig[key]])]);
}

export async function health() {
  const version = ++healthPollVersion;
  const identity = healthIdentity();
  const current = () => version === healthPollVersion && identity === healthIdentity();
  const ackContext = { revision: state.ackStatusRevision, voice: $('voice')?.value,
    liveSession: state.liveSession, agentSession: state.agentSession };
  let j = null;
  let pageFailed = false;
  try {
    const response = await fetch('/api/health');
    if (!current()) {
      try { void response.body?.cancel().catch(() => {}); } catch {}
      return;
    }
    if (!response.ok) throw new Error(`Health check failed: HTTP ${response.status}`);
    j = await response.json();
    if (!current()) return;
    if (!j || typeof j !== 'object' || j.ok !== true) throw new Error('Invalid health response');
    const limits = j.audio_limits;
    const limitsError = typeof j.audio_limits_error === 'string' ? j.audio_limits_error.trim() : '';
    if (limitsError) state.audioLimitsError = limitsError;
    else if (limits && Number.isFinite(limits.max_duration_seconds)
      && limits.max_duration_seconds >= 1 && limits.max_duration_seconds <= 1800
      && Number.isSafeInteger(limits.max_upload_bytes) && limits.max_upload_bytes > 0) {
      state.audioLimits = {
        max_duration_seconds: limits.max_duration_seconds,
        max_upload_bytes: limits.max_upload_bytes,
      };
      state.audioLimitsError = '';
    }
    state.healthFailures = 0;
  } catch {
    if (!current()) return;
    j = null;
    pageFailed = true;
    state.healthFailures = (state.healthFailures || 0) + 1;
  }

  if (!pageFailed) {
    const ack = $('ackStatus');
    const ownsAckStatus = ack && state.ackStatusRevision === ackContext.revision && $('voice')?.value === ackContext.voice
      && state.liveSession === ackContext.liveSession && state.agentSession === ackContext.agentSession;
    if (ownsAckStatus && j.ack_status?.ok === false) {
      state.ackStatusRevision++;
      status(ack, `ACK catalog unavailable (clip counts unknown): ${apiErrorMessage({ detail: j.ack_status.error })}`, 'bad');
    }
    else if (ownsAckStatus && j.ack_status?.ok === true && ack.textContent.startsWith('ACK catalog unavailable')) {
      state.ackStatusRevision++;
      status(ack, 'Acknowledgement catalog available; cached clips reload on use.', 'warn');
    }
    setDot('pageDot', j.whisper_loaded ? 'good' : 'warn');
    const sttSummary = sttBackend() === 'llm'
      ? `multimodal ${effectiveSttLlmModel()} @ ${effectiveSttLlmBaseUrl()} (${sttLlmReasoningEnabled() === 'on' ? 'reasoning on' : 'reasoning off'})`
      : `built-in Whisper ${sttDevice()}/${sttModel()}`;
    const responseProvider = j.response_llm_provider || llmProvider();
    const responseReasoning = responseProvider === 'meta' ? 'provider-default reasoning'
      : llmReasoningEnabled() === 'on' ? 'reasoning on' : 'reasoning off';
    const responseSummary = `${responseProvider} ${j.response_llm_model || lmModel()} (${responseReasoning})`;
    $('pageHealth').textContent = `${j.whisper_backend || 'Whisper'} ${j.whisper_loaded ? 'loaded' : 'lazy'} · selected STT ${sttSummary} · response ${responseSummary} @ ${j.response_llm_base_url || llmBaseUrl()} · partial ${(Number(j.partial_window_ms || state.partialWindowMs) / 1000).toFixed(1)}s · last ${j.whisper_device}/${j.whisper_compute_type} · CUDA ${j.cuda_available ? 'yes' : 'no'}`;

    // hostInfo (Bug C / #19): server-resolved URLs only.
    const llmBase = j.response_llm_base_url || '';
    const ttsBase = (j.tts && j.tts.base_url) || j.tts_base_url || '';
    $('hostInfo').textContent = `${location.host} → LLM (${llmProviderLabel(j.response_llm_provider || '')}) ${llmBase} → TTS (${ttsProviderLabel((j.tts && j.tts.provider) || j.tts_provider || '')}) ${ttsBase}`;

    // Live LLM/STT health uses server-resolved provider load blocks.
    const llmOk = !!(j.response_llm_load?.ok && j.response_llm_load?.loaded);
    const sttOk = !state.audioLimitsError && (j.stt_backend === 'llm'
      ? !!(j.multimodal_stt_load?.ok && j.multimodal_stt_load?.loaded)
      : !!j.whisper_available);
    const parts = [];
    parts.push(`LLM ${llmOk ? 'up' : 'down'}${llmOk ? '' : ' (' + (j.response_llm_load?.error || 'model not loaded') + ')'}`);
    parts.push(`STT ${sttOk ? 'up' : 'down'}${sttOk ? '' : ' (' + (state.audioLimitsError || j.multimodal_stt_load?.error || 'speech runtime unavailable') + ')'}`);
    const liveEl = $('settingsLiveStatus');
    if (liveEl) liveEl.textContent = parts.join(' · ');
  } else {
    // /api/health fetch failed this tick.
    const fails = state.healthFailures || 1;
    if (fails >= HEALTH_FAIL_GRACE) {
      setDot('pageDot', 'bad');
      $('pageHealth').textContent = `page server unreachable for ${fails * 5}s`;
    }
    // Else: keep the last-known-good dot color (no flicker).
  }

  // TTS pill — uses server block j.tts, not form-derived URL (Bug A / #17).
  if (j && j.tts) {
    const tts = j.tts;
    const ttsProviderName = tts.provider || j.tts_provider || '';
    if (tts.status === 'unverified' || tts.probe_supported === false) {
      setDot('ttsDot', 'warn');
      $('ttsHealth').textContent = `${ttsProviderLabel(ttsProviderName)} health unverified · ${tts.base_url}`;
    } else if (tts.ok && tts.loaded) {
      setDot('ttsDot', 'good');
      $('ttsHealth').textContent = `${ttsProviderLabel(ttsProviderName)} ready · ${tts.base_url} · last ${tts.device || 'none'} · CUDA ${tts.cuda_available ? 'yes' : 'no'}`;
    } else if (tts.ok && !tts.loaded) {
      setDot('ttsDot', 'warn');
      $('ttsHealth').textContent = `${ttsProviderLabel(ttsProviderName)} lazy · ${tts.base_url} · model not yet loaded`;
    } else {
      setDot('ttsDot', 'bad');
      $('ttsHealth').textContent = `${ttsProviderLabel(ttsProviderName)} offline · ${tts.base_url} · ${tts.error || 'unknown error'}`;
    }
  } else {
    // Server didn't return a tts block (older backend). Keep last-known state.
  }

  // Provider / model readiness pill (drives off server truth).
  const ready = $('providerReady');
  const readyDot = $('providerReadyDot');
  if (ready && readyDot && j) {
    const rll = j && j.response_llm_load;
    const pending = !!state.providerPending;
    if (pending && !(rll && rll.ok && rll.loaded)) {
      setDot('providerReadyDot', 'warn');
      ready.textContent = `${(rll && rll.provider) || j.response_llm_provider || 'provider'} · loading ${(rll && rll.expected_model) || j.response_llm_model || ''}…`;
    } else if (rll && rll.ok && rll.loaded) {
      setDot('providerReadyDot', 'good');
      ready.textContent = `${rll.provider || j.response_llm_provider || 'provider'} · ready · ${rll.loaded_model || rll.expected_model || j.response_llm_model || ''}`;
      state.providerPending = false;
    } else if (rll && rll.ok && !rll.loaded) {
      setDot('providerReadyDot', 'warn');
      ready.textContent = `${rll.provider || j.response_llm_provider || 'provider'} · model not loaded · ${rll.expected_model || j.response_llm_model || ''}`;
    } else {
      setDot('providerReadyDot', 'bad');
      ready.textContent = `${(rll && rll.provider) || j.response_llm_provider || 'provider'} offline · ${(rll && rll.error) || 'no /v1/models response'}`;
    }
  }
}

// recordSttBackend -- update the #sttBackendHealth / #sttBackendDot pill
// with the result of the most recent STT request. Called from
// recorder.js when /api/transcribe/stream emits its 'done' or 'error'
// SSE event, and when /api/transcribe/partial returns its JSON.
//
// Args:
//   payload: object | null -- the SSE 'done' JSON, partial JSON, or null.
//            Expected fields (any may be absent):
//              backend        'multimodal-stt' | 'faster-whisper' |
//                             'whisper-fallback' | 'whisper-llm'
//              seconds        number   seconds STT took
//              samples        number   raw audio samples
//              sample_rate    number   Hz
//              model          string   model id used
//              fallback_reason string  (whisper-fallback only)
//              warning        string   (whisper-fallback only)
//   errorMsg: string | undefined -- set when the call threw/failed
//
// The pill text is intentionally compact (one line) so it fits the
// debug strip without overflowing. It shows the backend label, the
// audio duration in seconds, the STT latency in seconds, and the
// resolved model id (last segment only -- long ids truncated).
const STT_BACKEND_LABELS = {
  'multimodal-stt': 'multimodal',
  'faster-whisper': 'whisper',
  'whisper-fallback': 'whisper (fallback)',
  'whisper-llm': 'whisper',
};
const STT_BACKEND_DOTS = {
  'multimodal-stt': 'good',
  'faster-whisper': 'good',
  'whisper-fallback': 'warn',
  'whisper-llm': 'warn',
};

export function recordSttBackend(payload, errorMsg) {
  const pill = $('sttBackendHealth');
  const dot = $('sttBackendDot');
  if (!pill || !dot) return;
  if (errorMsg) {
    setDot('sttBackendDot', 'bad');
    pill.textContent = `STT error · ${String(errorMsg).slice(0, 80)}`;
    return;
  }
  const backend = payload && payload.backend;
  const seconds = payload && payload.seconds;
  const samples = payload && payload.samples;
  const sampleRate = payload && payload.sample_rate;
  const model = payload && payload.model;
  const fallbackReason = payload && payload.fallback_reason;
  const audioSec = (samples && sampleRate) ? (samples / sampleRate) : null;
  const backendLabel = STT_BACKEND_LABELS[backend] || backend || 'unknown';
  const tail = fallbackReason ? ` · ⚠ ${fallbackReason}` : '';
  const durStr = audioSec != null ? `${audioSec.toFixed(1)}s audio` : null;
  const latStr = seconds != null ? `${seconds.toFixed(2)}s STT` : null;
  const timing = [durStr, latStr].filter(Boolean).join(' / ');
  const modelShort = model ? String(model).split(/[\\/]/).pop() : '';
  pill.textContent = timing
    ? `${backendLabel} · ${timing}${modelShort ? ` · ${modelShort}` : ''}${tail}`
    : `${backendLabel}${modelShort ? ` · ${modelShort}` : ''}${tail}`;
  setDot('sttBackendDot', STT_BACKEND_DOTS[backend] || 'bad');
}

// Load configuration before starting periodic health and agent updates.
export async function initApp() {
  await loadConfig();
  health();
  setInterval(health, 5000); // poll debug strip every 5s so it reflects runtime state, not last config-change
  markActivity('init');
  setInterval(async () => (await import('./agent-ui.js')).maybeDeliverTranscriptToAgent('timer'), 3000);
  setInterval(async () => (await import('./agent-ui.js')).pollAgentEvents(), 2000);
  setInterval(async () => (await import('./agent-ui.js')).maybeReleaseDeferredAgentInterrupt(), 500);
  // Discovery preserves the current selection and must not delay runtime status.
  void loadAgentModelOptions();
  void import('./acks.js').then(module => module.loadAcks('global'))
    .catch(error => console.warn('ack startup failed', error));
}

export { listAudioProbes, clearAudioProbe, normalizeTripleBaseUrl } from './audio-probes.js';
