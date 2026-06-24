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
import { $ } from './dom.js';
import { applyConfig } from './settings-ui.js';   // lands in 3.2.4
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

// health — moved verbatim from index.html:624
export async function health() {
  try {
    let j = await (await fetch('/api/health')).json();
    setDot('pageDot', j.whisper_loaded ? 'good' : 'warn');
    const sttSummary = sttBackend() === 'llm'
      ? `multimodal ${effectiveSttLlmModel()} @ ${effectiveSttLlmBaseUrl()} (${sttLlmReasoningEnabled() === 'on' ? 'reasoning on' : 'reasoning off'})`
      : `built-in Whisper ${sttDevice()}/${sttModel()}`;
    const responseSummary = `${j.response_llm_provider || llmProvider()} ${j.response_llm_model || lmModel()} (${llmReasoningEnabled() === 'on' ? 'reasoning on' : 'reasoning off'})`;
    $('pageHealth').textContent = `${j.whisper_backend || 'Whisper'} ${j.whisper_loaded ? 'loaded' : 'lazy'} · selected STT ${sttSummary} · response ${responseSummary} @ ${j.response_llm_base_url || llmBaseUrl()} · partial ${(Number(j.partial_window_ms || partialWindowMs) / 1000).toFixed(1)}s · last ${j.whisper_device}/${j.whisper_compute_type} · CUDA ${j.cuda_available ? 'yes' : 'no'}`;
  } catch (e) {
    setDot('pageDot', 'bad');
    $('pageHealth').textContent = 'page server offline';
  }
  try {
    let j = await (await fetch(`${ttsBaseUrl()}/health`)).json();
    setDot('ttsDot', j.loaded ? 'good' : 'warn');
    $('ttsHealth').textContent = `Kokoro ${j.loaded ? 'ready' : 'lazy'} · selected TTS ${ttsProvider()} ${ttsDevice()} @ ${ttsBaseUrl()} · last ${j.device || 'none'} · CUDA ${j.cuda_available ? 'yes' : 'no'}`;
  } catch (e) {
    setDot('ttsDot', 'bad');
    $('ttsHealth').textContent = 'Kokoro offline';
  }
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
