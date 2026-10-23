// Settings form access, saved configuration, and channel selection.

import { state } from './state.js';
import { $, setSectionHidden } from './dom.js';
import { prettyModelLabel } from './labels.js';

// Settings getters

export function ttsPort() { return parseInt($('ttsPort')?.value || '8880', 10) || 8880 }
export function ttsBaseUrl() {
  const explicit = String($('ttsBaseUrl')?.value || '').trim().replace(/\/$/, '');
  return explicit || `${location.protocol}//${location.hostname}:${ttsPort()}`;
}
export function sttDevice() { return $('sttDevice')?.value || 'cpu' }
export function ttsDevice() { return $('ttsDevice')?.value || 'cpu' }
export function ttsProvider() { return $('ttsProvider')?.value || 'kokoro' }
export function ttsModel() { return $('ttsModel')?.value || 'kokoro' }
export function llmProvider() { return $('llmProvider')?.value || 'llama.cpp' }
export function llmBaseUrl() { return $('llmBaseUrl')?.value || 'http://127.0.0.1:8080/v1' }
export function llmMaxTokens() { return Number($('llmMaxTokens')?.value || '180') }
export function llmReasoningEffort() { return String($('llmReasoningEffort')?.value || '').trim() }
export function agentReasoningEffort() { return String($('agentReasoningEffort')?.value || '').trim() }
export function metaReasoningSummary(effort) { return effort ? `reasoning effort ${effort}` : 'provider-default reasoning' }
const apiBase = value => String(value).trim().replace(/\/+$/, '').replace(/\/chat\/completions$/, '');
export function effectiveLlmApiKeyEnv() {
  const explicit = String($('llmApiKeyEnv')?.value || '').trim();
  if (explicit) return explicit;
  const profile = state._capabilitiesCache?.providers?.[llmProvider()];
  return profile?.default_base_url && apiBase(llmBaseUrl()) === apiBase(profile.default_base_url)
    ? String(profile.default_api_key_env || '') : '';
}
export function sttModel() { return $('sttModel')?.value || 'base.en' }
export function sttBackend() { return $('sttBackend')?.value || 'whisper' }
export function lmModel() { return $('lmModel')?.value || '' }
export function sttLlmProvider() { return String($('sttLlmProvider')?.value || '').trim() }
export function sttLlmBaseUrl() { return String($('sttLlmBaseUrl')?.value || '').trim() }
export function sttLlmApiKeyEnv() { return String($('sttLlmApiKeyEnv')?.value || '').trim() }
export function sttLlmModel() { return String($('sttLlmModel')?.value || '').trim() }
export function llmReasoningEnabled() { return $('llmReasoningEnabled')?.checked ? 'on' : 'off' }
export function sttLlmReasoningEnabled() { return $('sttLlmReasoningEnabled')?.checked ? 'on' : 'off' }
export function effectiveSttLlmProvider() { return sttLlmProvider() || llmProvider() }
export function effectiveSttLlmBaseUrl() { return sttLlmBaseUrl() || llmBaseUrl() }
export function effectiveSttLlmApiKeyEnv() {
  if (sttLlmApiKeyEnv()) return sttLlmApiKeyEnv();
  return apiBase(effectiveSttLlmBaseUrl()) === apiBase(llmBaseUrl()) ? effectiveLlmApiKeyEnv() : '';
}
export function effectiveSttLlmModel() {
  const explicit = sttLlmModel();
  if (explicit) return explicit;
  if (sttLlmProvider() || sttLlmBaseUrl()) return '';
  return lmModel();
}
export function minSpeechMs() { return parseInt($('minSpeechMs')?.value || '1200', 10) || 1200 }
export function partialWindowMsSetting() { return parseInt($('partialWindowMs')?.value || String(state.partialWindowMs), 10) || 1800 }
export function agentEnabled() { return $('agentEnabled')?.checked ? 'on' : 'off' }
export function agentModel() { return $('agentModel')?.value || lmModel() }
export function agentInterruptCooldownPaddingMs() {
  const raw = String($('agentInterruptCooldownPaddingMs')?.value ?? '').trim();
  const value = parseInt(raw || '3000', 10);
  return Number.isNaN(value) ? 3000 : value;
}
export function finalSttMode() { return $('finalSttMode')?.value || 'chunks' }
export function endpointMode() { return $('endpointMode')?.value || 'reading' }

// collectConfig

export function collectConfig() {
  return {
    voice: $('voice')?.value || 'af_heart',
    speed: parseFloat($('speed')?.value || '1'),
    mode: state.mode,
    ack_enabled: $('ackEnabled')?.value || 'on',
    stt_backend: sttBackend(),
    stt_device: sttDevice(),
    stt_model: sttModel(),
    stt_llm_provider: sttLlmProvider(),
    stt_llm_base_url: sttLlmBaseUrl(),
    stt_llm_api_key_env: sttLlmApiKeyEnv(),
    stt_llm_model: sttLlmModel(),
    stt_llm_reasoning: sttLlmReasoningEnabled(),
    lm_model: lmModel(),
    tts_device: ttsDevice(),
    tts_provider: ttsProvider(),
    tts_port: ttsPort(),
    tts_base_url: String($('ttsBaseUrl')?.value || '').trim(),
    tts_health_url: String($('ttsHealthUrl')?.value || '').trim(),
    tts_model: ttsModel(),
    llm_provider: llmProvider(),
    llm_base_url: llmBaseUrl(),
    llm_api_key_env: String($('llmApiKeyEnv')?.value || '').trim(),
    llm_reasoning: llmReasoningEnabled(),
    llm_reasoning_effort: llmReasoningEffort(),
    llm_max_tokens: llmMaxTokens(),
    agent_enabled: agentEnabled(),
    agent_provider: $('agentProvider')?.value || 'openai-compatible',
    agent_reasoning_effort: agentReasoningEffort(),
    agent_base_url: String($('agentBaseUrl')?.value || '').trim(),
    agent_api_key: String($('agentApiKey')?.value || '').trim(),
    agent_model: String($('agentModel')?.value || '').trim(),
    agent_max_turns: parseInt($('agentMaxTurns')?.value || '16', 10) || 16,
    agent_max_tokens: parseInt($('agentMaxTokens')?.value || '512', 10) || 512,
    agent_first_delivery_mode: $('agentFirstDeliveryMode')?.value || 'first_turn_or_timer',
    agent_first_delivery_seconds: parseInt($('agentFirstDeliverySeconds')?.value || '20', 10) || 20,
    agent_periodic_delivery_turns: parseInt($('agentPeriodicDeliveryTurns')?.value || '2', 10) || 2,
    agent_periodic_delivery_seconds: parseInt($('agentPeriodicDeliverySeconds')?.value || '45', 10) || 45,
    agent_busy_delivery_mode: $('agentBusyDeliveryMode')?.value || 'injection',
    agent_idle_delivery_mode: $('agentIdleDeliveryMode')?.value || 'prompt',
    agent_interrupts_enabled: $('agentInterruptsEnabled')?.value || 'on',
    agent_interrupt_min_priority: $('agentInterruptMinPriority')?.value || 'important',
    agent_hard_interrupt_min_priority: $('agentHardInterruptMinPriority')?.value || 'critical',
    agent_interrupt_cooldown_padding_ms: agentInterruptCooldownPaddingMs(),
    agent_permission_interrupts: $('agentPermissionInterrupts')?.value || 'on',
    agent_report_injection_mode: $('agentReportInjectionMode')?.value || 'next_reply',
    endpoint_mode: endpointMode(),
    silence_ms: state.endSilenceMs,
    final_stt_mode: finalSttMode(),
    min_speech_ms: minSpeechMs(),
    partial_window_ms: partialWindowMsSetting(),
    idle_ack_initial_ms: state.appConfig.idle_ack_initial_ms || 5000,
    idle_ack_step_ms: state.appConfig.idle_ack_step_ms || 5000,
  };
}

// applyConfig

export function applyConfig(c = {}, { preserveKeys = [], updateSnapshot = true, snapshot = c } = {}) {
  if (state.agentControlPending) {
    c = { ...c };
    delete c.agent_enabled;
  }
  state.appConfig = { ...state.appConfig, ...c };
  c = { ...c };
  for (const key of preserveKeys) delete c[key];
  if (c.voice && $('voice')) $('voice').value = c.voice;
  if (c.speed && $('speed')) { $('speed').value = c.speed; $('speedDisplay').textContent = parseFloat(c.speed).toFixed(1) + '×'; }
  if (c.ack_enabled && $('ackEnabled')) $('ackEnabled').value = c.ack_enabled;
  if (c.stt_backend && $('sttBackend')) $('sttBackend').value = c.stt_backend;
  if (c.stt_device && $('sttDevice')) $('sttDevice').value = c.stt_device;
  if (c.stt_model && $('sttModel')) $('sttModel').value = c.stt_model;
  if (c.stt_llm_provider !== undefined && $('sttLlmProvider')) $('sttLlmProvider').value = c.stt_llm_provider || '';
  if (c.stt_llm_base_url !== undefined && $('sttLlmBaseUrl')) $('sttLlmBaseUrl').value = c.stt_llm_base_url || '';
  if (c.stt_llm_api_key_env !== undefined && $('sttLlmApiKeyEnv')) $('sttLlmApiKeyEnv').value = c.stt_llm_api_key_env || '';
  if (c.stt_llm_reasoning !== undefined && $('sttLlmReasoningEnabled')) $('sttLlmReasoningEnabled').checked = String(c.stt_llm_reasoning || 'off') === 'on';
  if (c.stt_llm_model !== undefined && $('sttLlmModel')) {
    const sttModelValue = c.stt_llm_model;
    if (sttModelValue && !$('sttLlmModel').querySelector(`option[value="${CSS.escape(sttModelValue)}"]`)) { const o = document.createElement('option'); o.value = sttModelValue; o.textContent = prettyModelLabel(sttModelValue); $('sttLlmModel').appendChild(o); }
    $('sttLlmModel').value = sttModelValue || '';
  }
  if (c.lm_model !== undefined && $('lmModel')) { if (c.lm_model && !$('lmModel').querySelector(`option[value="${CSS.escape(c.lm_model)}"]`)) { const o = document.createElement('option'); o.value = c.lm_model; o.textContent = prettyModelLabel(c.lm_model); $('lmModel').appendChild(o); } $('lmModel').value = c.lm_model || ''; }
  if (c.tts_device && $('ttsDevice')) $('ttsDevice').value = c.tts_device;
  if (c.tts_provider && $('ttsProvider')) {
    const select = $('ttsProvider');
    if (!Array.from(select.options).some(option => option.value === c.tts_provider)) {
      const option = document.createElement('option');
      option.value = c.tts_provider;
      option.textContent = `Saved provider: ${c.tts_provider} (Kokoro protocol required)`;
      select.appendChild(option);
    }
    select.value = c.tts_provider;
  }
  if (c.tts_health_url !== undefined && $('ttsHealthUrl')) $('ttsHealthUrl').value = c.tts_health_url || '';
  if (c.tts_port && $('ttsPort')) $('ttsPort').value = c.tts_port;
  if (c.tts_base_url !== undefined && $('ttsBaseUrl')) $('ttsBaseUrl').value = c.tts_base_url || '';
  if (c.tts_model && $('ttsModel')) $('ttsModel').value = c.tts_model;
  if (c.llm_provider && $('llmProvider')) $('llmProvider').value = c.llm_provider;
  if (c.llm_base_url && $('llmBaseUrl')) $('llmBaseUrl').value = c.llm_base_url;
  if (c.llm_api_key_env !== undefined && $('llmApiKeyEnv')) $('llmApiKeyEnv').value = c.llm_api_key_env || '';
  if (c.llm_reasoning !== undefined && $('llmReasoningEnabled')) $('llmReasoningEnabled').checked = String(c.llm_reasoning || 'off') === 'on';
  if (c.llm_reasoning_effort !== undefined && $('llmReasoningEffort')) $('llmReasoningEffort').value = c.llm_reasoning_effort;
  if (c.llm_max_tokens !== undefined && $('llmMaxTokens')) $('llmMaxTokens').value = c.llm_max_tokens;
  if (c.agent_enabled && $('agentEnabled')) $('agentEnabled').checked = c.agent_enabled !== 'off';
  if (c.agent_provider && $('agentProvider')) $('agentProvider').value = c.agent_provider;
  if (c.agent_reasoning_effort !== undefined && $('agentReasoningEffort')) $('agentReasoningEffort').value = c.agent_reasoning_effort;
  if (c.agent_base_url !== undefined && $('agentBaseUrl')) $('agentBaseUrl').value = c.agent_base_url || '';
  if (c.agent_api_key !== undefined && $('agentApiKey')) $('agentApiKey').value = c.agent_api_key || '';
  if (c.agent_model !== undefined && $('agentModel')) {
    if (c.agent_model && !$('agentModel').querySelector(`option[value="${CSS.escape(c.agent_model)}"]`)) { const o = document.createElement('option'); o.value = c.agent_model; o.textContent = prettyModelLabel(c.agent_model); $('agentModel').appendChild(o); }
    $('agentModel').value = c.agent_model || '';
  }
  if (c.agent_max_turns && $('agentMaxTurns')) $('agentMaxTurns').value = c.agent_max_turns;
  if (c.agent_max_tokens && $('agentMaxTokens')) $('agentMaxTokens').value = c.agent_max_tokens;
  if (c.agent_first_delivery_mode && $('agentFirstDeliveryMode')) $('agentFirstDeliveryMode').value = c.agent_first_delivery_mode;
  if (c.agent_first_delivery_seconds !== undefined && $('agentFirstDeliverySeconds')) $('agentFirstDeliverySeconds').value = c.agent_first_delivery_seconds;
  if (c.agent_periodic_delivery_turns !== undefined && $('agentPeriodicDeliveryTurns')) $('agentPeriodicDeliveryTurns').value = c.agent_periodic_delivery_turns;
  if (c.agent_periodic_delivery_seconds !== undefined && $('agentPeriodicDeliverySeconds')) $('agentPeriodicDeliverySeconds').value = c.agent_periodic_delivery_seconds;
  if (c.agent_busy_delivery_mode && $('agentBusyDeliveryMode')) $('agentBusyDeliveryMode').value = c.agent_busy_delivery_mode;
  if (c.agent_idle_delivery_mode && $('agentIdleDeliveryMode')) $('agentIdleDeliveryMode').value = c.agent_idle_delivery_mode;
  if (c.agent_interrupts_enabled && $('agentInterruptsEnabled')) $('agentInterruptsEnabled').value = c.agent_interrupts_enabled;
  if (c.agent_interrupt_min_priority && $('agentInterruptMinPriority')) $('agentInterruptMinPriority').value = c.agent_interrupt_min_priority;
  if (c.agent_hard_interrupt_min_priority && $('agentHardInterruptMinPriority')) $('agentHardInterruptMinPriority').value = c.agent_hard_interrupt_min_priority;
  if (c.agent_interrupt_cooldown_padding_ms !== undefined && $('agentInterruptCooldownPaddingMs')) $('agentInterruptCooldownPaddingMs').value = c.agent_interrupt_cooldown_padding_ms;
  if (c.agent_permission_interrupts && $('agentPermissionInterrupts')) $('agentPermissionInterrupts').value = c.agent_permission_interrupts;
  if (c.agent_report_injection_mode && $('agentReportInjectionMode')) $('agentReportInjectionMode').value = c.agent_report_injection_mode;
  if (c.endpoint_mode && $('endpointMode')) $('endpointMode').value = c.endpoint_mode;
  if (c.final_stt_mode && $('finalSttMode')) $('finalSttMode').value = c.final_stt_mode;
  if (c.min_speech_ms !== undefined && $('minSpeechMs')) { $('minSpeechMs').value = c.min_speech_ms; $('minSpeechMsDisplay').textContent = (Number(c.min_speech_ms) / 1000).toFixed(1) + 's'; }
  if (c.partial_window_ms !== undefined && $('partialWindowMs')) { state.partialWindowMs = parseInt(c.partial_window_ms, 10) || 1800; $('partialWindowMs').value = state.partialWindowMs; $('partialWindowMsDisplay').textContent = (state.partialWindowMs / 1000).toFixed(1) + 's'; }
  if (c.silence_ms) { state.endSilenceMs = parseInt(c.silence_ms, 10); $('silenceMs').value = state.endSilenceMs; $('silenceDisplay').textContent = state.endSilenceMs + 'ms'; }
  syncConverseSettingsUI();
  if (c.mode) { state.mode = c.mode; document.querySelectorAll('.tab[data-mode]').forEach(x => x.classList.toggle('active', x.dataset.mode === state.mode)); }
  // hostInfo is now owned by api.js:health() and updated every 5s from
  // server-resolved URLs (Bug C / #19). The previous form-derived setter
  // could disagree with the server when config.tts_base_url was empty or
  // when the settings UI had a stale value.

  // Keep the response-provider baseline used by closeSettings() to decide
  // whether activation is needed; unrelated settings do not activate it.
  if (updateSnapshot) {
    const next = { ...state.appConfigSnapshot };
    for (const key of ['llm_provider', 'lm_model', 'llm_base_url', 'llm_reasoning']) {
      if (snapshot[key] !== undefined) next[key] = snapshot[key];
    }
    state.appConfigSnapshot = next;
  }
}

// Idle activity tracking

export function markActivity(_reason = 'activity') {
  state.lastActivityAt = performance.now();
  state.idleAckCount = 0;
  // Keep this quiet except in the status debug area to avoid log spam.
}

export function recordIdleAck() {
  state.lastActivityAt = performance.now();
  state.idleAckCount++;
}

export function nextIdleDelayMs() {
  const initial = Number(state.appConfig.idle_ack_initial_ms || 5000);
  const step = Number(state.appConfig.idle_ack_step_ms || 5000);
  return initial + (state.idleAckCount * step);
}

// Existing consumers can keep importing the channel controls from here.
export { populateChannelTab, saveChannelSelection } from './channel-ui.js';

// Reasoning/converse sync helpers

export function syncReasoningHints() {
  const responseMeta = llmProvider() === 'meta';
  const agentMeta = $('agentProvider')?.value === 'meta';
  setSectionHidden('llmReasoningEffortWrap', !responseMeta);
  setSectionHidden('llmReasoningEnabledWrap', responseMeta);
  setSectionHidden('agentReasoningEffortWrap', !agentMeta);
  if ($('llmReasoningHint')) $('llmReasoningHint').textContent = responseMeta
    ? `Reasoning: Meta ${llmReasoningEffort() ? `effort ${llmReasoningEffort()}` : 'provider default'}.`
    : `Reasoning: ${llmReasoningEnabled() === 'on' ? 'on' : 'off'}.`;
  if ($('agentReasoningHint')) $('agentReasoningHint').textContent = `Reasoning: Meta ${agentReasoningEffort() ? `effort ${agentReasoningEffort()}` : 'provider default'}. Applies after settings save.`;
  if ($('sttReasoningHint')) $('sttReasoningHint').textContent = sttBackend() === 'llm'
    ? `Reasoning: ${sttLlmReasoningEnabled() === 'on' ? 'on' : 'off'} for multimodal STT.`
    : `Reasoning: ${sttBackend() === 'whistle' ? 'Whistle' : 'built-in Whisper'} does not use this toggle.`;
}

export function syncConverseSettingsUI() {
  const multimodal = sttBackend() === 'llm';
  ['sttWhisperDeviceWrap', 'sttWhisperModelWrap'].forEach(id => setSectionHidden(id, sttBackend() !== 'whisper'));
  ['sttMultimodalProviderWrap', 'sttMultimodalModelWrap', 'sttMultimodalBaseUrlWrap', 'sttMultimodalApiKeyWrap', 'sttMultimodalReasoningWrap'].forEach(id => setSectionHidden(id, !multimodal));
  $('llmResponseSection')?.classList.toggle('settingsDisabled', multimodal);
  if ($('llmResponseSection')) $('llmResponseSection').open = !multimodal;
  syncReasoningHints();
  $('modelOptionsStatus').textContent = multimodal
    ? `Multimodal STT selected. STT model endpoint resolves to ${effectiveSttLlmBaseUrl() || 'the LLM Response base URL'} and falls back to the LLM Response section when STT-specific fields are blank.`
    : sttBackend() === 'whistle'
      ? 'Whistle runs on the local CPU using the configured offline weights and engine. Whisper model/device controls do not apply.'
      : 'Whisper is built into Korina. Open a model picker to query its current /v1/models endpoint or the local llama.cpp model catalog.';
}
