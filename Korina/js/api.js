// js/api.js
//
// Frontend API client + bootstrap. Provides the canonical wrappers
// for the page server's HTTP endpoints (`/api/config`, `/api/health`,
// `/api/llm/provider/activate`) and the application entrypoint
// `initApp()` that wires up the rest of the page once DOM modules
// have been extracted.
//
// Forward-reference imports: `settings-ui.js` (lands in 3.2.4) and
// `providers-ui.js` (lands in 3.2.6) are imported statically. Until
// those modules exist, importing this file will fail to load — this
// is intentional per the Phase 3 plan and will be resolved by the
// time `app.js` actually imports this module.
//
// Dynamic imports for `acks.js`, `live.js`, and `agent-ui.js` keep
// this file loadable before those modules land; they are replaced
// with static imports in Task 3.2.15 (cleanup).

import { state } from './state.js';
import { ttsProviderLabel, llmProviderLabel } from './labels.js';
import { $, setDot } from './dom.js';
import {
  applyConfig, ttsProvider,
  sttBackend,
  sttDevice, sttModel,
  effectiveSttLlmModel, effectiveSttLlmBaseUrl,
  sttLlmReasoningEnabled,
  llmReasoningEnabled,
  llmProvider, llmBaseUrl, lmModel,
  partialWindowMsSetting,
} from './settings-ui.js';
import { loadAgentModelOptions } from './providers-ui.js';   // lands in 3.2.6

export async function activateSelectedProvider(provider, model = '') {
  const r = await fetch('/api/llm/provider/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, model: model || '' }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
  if (j.saved) applyConfig(j.saved);
  // Signal a pending provider switch so the readiness pill flips to
  // "loading" until /api/health confirms the new model is loaded.
  state.providerPending = true;
  return j;
}

// loadConfig — moved verbatim from index.html:583
export async function loadConfig() {
  try {
    const c = await (await fetch('/api/config')).json();
    applyConfig(c);
    syncConverseSettingsUI();
    $('settingsInfo').textContent = 'Loaded saved config.json settings.';
  } catch (e) {
    console.warn('config load failed', e);
  }
}

// saveConfigNow + saveConfigSoon — moved verbatim from index.html:587-591
export function saveConfigNow() {
  const cfg = collectConfig();
  return fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  }).catch((e) => console.warn('config save failed', e));
}

export function saveConfigSoon() {
  clearTimeout(state.saveConfigTimer);
  state.saveConfigTimer = setTimeout(saveConfigNow, 250);
}

// health — refactored for Commit 2 of the debug-strip audit.
//
// Single source of truth: /api/health (server-resolved URLs).
// - ttsHealth reads j.tts.base_url and j.tts.{loaded,error,provider}.
// - pageHealth flips red only after 2-3 consecutive poll failures
//   (state.healthFailures), so a single 5s blip does not alarm.
// - hostInfo is owned here (every 5s), not in form change handlers.
// - settingsLiveStatus is updated from j.llm_error and j.stt_llm_error
//   so the user can see which downstream service is failing.
const HEALTH_FAIL_GRACE = 3; // consecutive misses before flipping pageDot red

export async function health() {
  let j = null;
  let pageFailed = false;
  try {
    j = await (await fetch('/api/health')).json();
    state.healthFailures = 0;
  } catch (e) {
    pageFailed = true;
    state.healthFailures = (state.healthFailures || 0) + 1;
  }

  if (!pageFailed) {
    setDot('pageDot', j.whisper_loaded ? 'good' : 'warn');
    const sttSummary = sttBackend() === 'llm'
      ? `multimodal ${effectiveSttLlmModel()} @ ${effectiveSttLlmBaseUrl()} (${sttLlmReasoningEnabled() === 'on' ? 'reasoning on' : 'reasoning off'})`
      : `built-in Whisper ${sttDevice()}/${sttModel()}`;
    const responseSummary = `${j.response_llm_provider || llmProvider()} ${j.response_llm_model || lmModel()} (${llmReasoningEnabled() === 'on' ? 'reasoning on' : 'reasoning off'})`;
    $('pageHealth').textContent = `${j.whisper_backend || 'Whisper'} ${j.whisper_loaded ? 'loaded' : 'lazy'} · selected STT ${sttSummary} · response ${responseSummary} @ ${j.response_llm_base_url || llmBaseUrl()} · partial ${(Number(j.partial_window_ms || partialWindowMs) / 1000).toFixed(1)}s · last ${j.whisper_device}/${j.whisper_compute_type} · CUDA ${j.cuda_available ? 'yes' : 'no'}`;

    // hostInfo (Bug C / #19): server-resolved URLs only.
    const llmBase = j.response_llm_base_url || '';
    const ttsBase = (j.tts && j.tts.base_url) || j.tts_base_url || '';
    $('hostInfo').textContent = `${location.host} → LLM (${llmProviderLabel(j.response_llm_provider || '')}) ${llmBase} → TTS (${ttsProviderLabel((j.tts && j.tts.provider) || j.tts_provider || '')}) ${ttsBase}`;

    // settingsLiveStatus (Bug D / #21): live LLM/STT health from j.{llm_error,stt_llm_error}.
    const llmOk = !j.llm_error;
    const sttOk = !j.stt_llm_error;
    const parts = [];
    parts.push(`LLM ${llmOk ? 'up' : 'down'}${llmOk ? '' : ' (' + j.llm_error + ')'}`);
    parts.push(`STT ${sttOk ? 'up' : 'down'}${sttOk ? '' : ' (' + j.stt_llm_error + ')'}`);
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
    if (tts.ok && tts.loaded) {
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
  if (ready && readyDot) {
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

// initApp — moved from index.html:743-755 with dynamic imports for the
// not-yet-extracted modules (`acks.js`, `live.js`, `agent-ui.js`).
// Note: the setInterval callbacks must be `async` because the dynamic
// `import()` calls inside them require an async context. setInterval
// ignores the returned Promise, so this is a safe no-op for the timer.
export async function initApp() {
  await loadConfig();
  await loadAgentModelOptions();
  await loadConfig(); // Re-apply after model options are populated.
  await (await import('./acks.js')).loadAcks('global');
  health();
  setInterval(health, 5000); // poll debug strip every 5s so it reflects runtime state, not last config-change
  await (await import('./live.js')).markActivity('init');
  setInterval(async () => (await import('./agent-ui.js')).maybeDeliverTranscriptToAgent('timer'), 3000);
  setInterval(async () => (await import('./agent-ui.js')).pollAgentEvents(), 2000);
  setInterval(async () => (await import('./agent-ui.js')).maybeReleaseDeferredAgentInterrupt(), 500);
}

export async function listAudioProbes() {
  const r = await fetch('/api/audio-probe');
  if (!r.ok) return { entries: {} };
  return r.json();
}

export async function clearAudioProbe(provider, baseUrl, model) {
  // Query params: see `routes/providers.py` Task 4.5.5 — path-param form
  // with two `:path` converters routes incorrectly for HF-style model ids.
  const qs = new URLSearchParams({
    provider: String(provider || ''),
    base_url: String(baseUrl || ''),
    model: String(model || ''),
  });
  const r = await fetch(`/api/audio-probe?${qs.toString()}`, { method: 'DELETE' });
  return r.json();
}

// Cache key normalizer. MUST match the backend's `triple_key` in
// `korina/services/audio_probe.py`: trim, strip trailing slashes, and
// collapse `/chat/completions` to the API base URL. If we don't apply
// the same normalization here, badge lookup silently misses.
export function normalizeTripleBaseUrl(s) {
  const base = String(s || '').trim().replace(/\/+$/, '');
  return base.endsWith('/chat/completions')
    ? base.slice(0, -'/chat/completions'.length)
    : base;
}
