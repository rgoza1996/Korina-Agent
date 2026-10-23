// Provider capabilities, model discovery, and model-picker rendering.

import { state } from "./state.js";
import { $ } from "./dom.js";
import { prettyModelLabel } from "./labels.js";
import { readResponseBlob, responseError } from './response-body.js';
import { boundedModelIds, boundedModelPayload, catalogNotice, normalizeModelId, MAX_MODEL_RESPONSE_BYTES } from './model-catalog.js';
import {
  llmBaseUrl,
  effectiveLlmApiKeyEnv,
  effectiveSttLlmProvider,
  effectiveSttLlmBaseUrl,
  effectiveSttLlmApiKeyEnv,
  effectiveSttLlmModel,
  sttModel,
  lmModel,
  sttLlmModel,
  syncConverseSettingsUI,
} from "./settings-ui.js";
import {
  listAudioProbes,
  clearAudioProbe,
  normalizeTripleBaseUrl,
} from "./audio-probes.js";
import {
  filterModelsByCapability,
  sttLlmModelRequirement,
} from "./capability-filter.js";

export async function loadCapabilities(force=false){
  if(state._capabilitiesCache && !force) return state._capabilitiesCache;
  const r = await fetch("/api/capabilities");
  if(!r.ok){ throw new Error("capabilities fetch failed: "+r.status); }
  const j = await r.json();
  state._capabilitiesCache = { providers: j.providers || {}, agentProviders: j.agent_providers || {} };
  return state._capabilitiesCache;
}

export function getResponseLlmProviderCaps(provider){
  const caps = state._capabilitiesCache?.providers || {};
  return caps[String(provider||"").trim()] || null;
}

export function getAgentProviderCaps(provider){
  const caps = state._capabilitiesCache?.agentProviders || {};
  return caps[String(provider||"").trim()] || null;
}

export function maybeApplyProviderPreset(providerId, baseUrlId, {clearWhenBlank=false}={}){
  const provider=$(providerId)?.value||"";
  const input=$(baseUrlId);
  if(!input) return false;
  const isAgent = providerId === "agentProvider";
  const caps = (isAgent ? getAgentProviderCaps(provider) : getResponseLlmProviderCaps(provider));
  const preset = String(caps?.default_base_url || "");
  if(preset){ input.value=preset; return true; }
  if(clearWhenBlank && !provider){ input.value=""; return true; }
  return false;
}

function modelStatus(statusDict, modelId){
  return statusDict ? (statusDict[String(modelId || "")] || null) : null;
}

function applyModelOptionStatus(option, baseLabel, status, capability, { responseModel = false } = {}){
  const summary = String(status?.summary || "").trim();
  const reasons = Array.isArray(status?.reasons) ? status.reasons.map(x => String(x || "").trim()).filter(Boolean) : [];
  const audioSupported = capability ? (capability.supports_audio_input === true) : null;
  const unverifiedResponseAudio = responseModel && capability?.source === 'inferred' && audioSupported === false;
  option.classList.remove('modelOptionBad','modelOptionNoAudio');
  option.style.color = '';
  option.style.fontWeight = '';
  option.dataset.unusable = 'false';
  option.dataset.audioCapable = audioSupported === null ? '' : (audioSupported ? 'true' : 'false');
  option.textContent = baseLabel;
  option.title = '';
  // Keep STT's capability hints, including its override view. A missing
  // positive audio signal does not establish a response model's capabilities.
  let label = baseLabel;
  if (audioSupported === false && !unverifiedResponseAudio) {
    label = `${baseLabel} (no audio)`;
    option.classList.add('modelOptionNoAudio');
  }
  if (status && status.usable === false) {
    option.classList.add('modelOptionBad');
    option.style.color = '#ff7b72';
    option.style.fontWeight = '600';
    option.dataset.unusable = 'true';
    label = `${label} [red: ${summary || 'likely unusable'}]`;
    option.title = reasons.join(' • ');
  }
  option.textContent = label;
}

export async function setBaseUrlEditability(){
  await loadCapabilities();
  const llmId = $("llmProvider")?.value || "";
  const llmCaps = getResponseLlmProviderCaps(llmId);
  const llmEditable = llmCaps?.editable_base_url === true;
  if($("llmBaseUrl")) $("llmBaseUrl").disabled = !llmEditable;
  if($("llmApiKeyEnv")) $("llmApiKeyEnv").placeholder = llmCaps?.default_api_key_env || 'Optional environment variable name';
  if($("llmReasoningEnabled")) {
    $("llmReasoningEnabled").disabled = llmId === 'meta';
    $("llmReasoningEnabled").title = llmId === 'meta' ? 'Meta Model API uses its provider-default reasoning.' : '';
  }
  const sttId = String($("sttLlmProvider")?.value || "").trim();
  const sttCaps = getResponseLlmProviderCaps(sttId);
  const sttEditable = sttCaps?.editable_base_url === true;
  if($("sttLlmBaseUrl")) $("sttLlmBaseUrl").disabled = !sttEditable;
  const agentId = String($("agentProvider")?.value || "").trim();
  const agentCaps = getAgentProviderCaps(agentId);
  const agentEditable = agentCaps?.editable_base_url === true;
  if($("agentBaseUrl")) $("agentBaseUrl").disabled = !agentEditable;
  if($("agentApiKey")) $("agentApiKey").placeholder = agentCaps?.default_api_key_env
    ? `Blank: same-URL response key, otherwise ${agentCaps.default_api_key_env}` : 'Optional; saved locally in config.json';
}

let modelsRequest = 0;
let agentModelsRequest = 0;
let modelStatusRequest = 0;
let modelsController = null;
let agentModelsController = null;
let modelsInFlight = null;
let agentModelsInFlight = null;
let agentModelsLoaded = null;
let probeReset = null;

async function discoveryJson(response, signal, isCurrent) {
  if (!response.ok) throw await responseError(response, { signal, isCurrent });
  const blob = await readResponseBlob(response, { maxBytes: MAX_MODEL_RESPONSE_BYTES, signal, isCurrent });
  const bytes = await blob.arrayBuffer();
  signal.throwIfAborted();
  if (!isCurrent()) throw new DOMException('Discovery is no longer current.', 'AbortError');
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}

function keepModelSelection(select, current, fallback = '', { statuses, capabilities, responseModel = false } = {}) {
  const selected = normalizeModelId(current) || normalizeModelId(fallback) || '';
  let unlisted = false;
  if (selected && !Array.from(select.options).some(option => option.value === selected)) {
    const option = document.createElement('option');
    option.value = selected;
    applyModelOptionStatus(option, prettyModelLabel(selected), modelStatus(statuses, selected), capabilities?.[selected], { responseModel });
    option.textContent += ' (saved; not listed)';
    option.title = (option.title ? option.title + ' • ' : '') + 'Discovery did not list this saved selection. It has been retained.';
    option.dataset.unlisted = 'true';
    select.appendChild(option);
    unlisted = true;
  }
  select.value = selected;
  return unlisted;
}

function localModelPool(payload) {
  const source = [
    ...(payload.llama_cpp_local_models || []).map(id => ({ id, group: 'llama.cpp (local GGUF)' })),
    ...(payload.lmstudio_catalog_models || []).map(id => ({ id, group: 'lmstudio (catalog)' })),
  ];
  const entries = new Map(source.map(entry => [entry.id, entry]));
  return boundedModelIds(source.map(entry => entry.id), [lmModel(), sttLlmModel()]).ids.map(id => entries.get(id));
}

export function modelDiscoveryQuery(){
  return new URLSearchParams({
    llm_base_url: llmBaseUrl(),
    llm_api_key_env: effectiveLlmApiKeyEnv(),
    stt_llm_base_url: effectiveSttLlmBaseUrl(),
    stt_llm_api_key_env: effectiveSttLlmApiKeyEnv(),
    llm_provider: String($("llmProvider")?.value||"").trim(),
    stt_llm_provider: effectiveSttLlmProvider(),
  }).toString();
}

function agentDiscoverySettings(){
  return {
    agent_provider: String($("agentProvider")?.value || '').trim(),
    agent_base_url: String($("agentBaseUrl")?.value || '').trim(),
    agent_api_key: String($("agentApiKey")?.value || '').trim(),
    llm_base_url: String($("llmBaseUrl")?.value || '').trim(),
    llm_api_key_env: String($("llmApiKeyEnv")?.value || '').trim(),
  };
}

export function loadModelOptions(force=false){
  const query = modelDiscoveryQuery();
  if (!force && modelsInFlight?.query === query && modelsController && !modelsController.signal.aborted) return modelsInFlight.promise;
  const flight = { query, promise: null };
  flight.promise = discoverModelOptions(force).finally(() => {
    if (modelsInFlight === flight) modelsInFlight = null;
  });
  modelsInFlight = flight;
  return flight.promise;
}

async function discoverModelOptions(force){
  modelsController?.abort();
  const controller = new AbortController();
  modelsController = controller;
  const request = ++modelsRequest;
  const statusRequest = ++modelStatusRequest;
  const query = modelDiscoveryQuery();
  const isCurrent = () => !controller.signal.aborted && request === modelsRequest && query === modelDiscoveryQuery();
  const clearFailedCache = () => {
    if(state.lastModelsQuery === query){
      state.lastModelsPayload = null;
      state.lastModelsQuery = '';
    }
  };
  try{
    let audioUnsupported = null, probeWarning = '';
    try {
      const probeResp = await listAudioProbes({ signal: controller.signal, isCurrent });
      audioUnsupported = probeResp.entries;
    } catch (_) {
      if (controller.signal.aborted || !isCurrent()) return;
      probeWarning = 'Audio probe cache unavailable; previous entries retained. Reopen the picker to retry.';
    }
    if(!isCurrent()) return;
    let j=state.lastModelsPayload;
    if(force || state.lastModelsNeedsRefresh || !j || query!==state.lastModelsQuery){
      const r=await fetch(`/api/models?${query}`, {
        signal: controller.signal, headers: { 'X-Korina-Intent': 'model-discovery' },
      });
      const payload = await discoveryJson(r, controller.signal, isCurrent);
      if(!isCurrent()) return;
      j = boundedModelPayload(payload, { response: lmModel(), stt: sttLlmModel() });
      // Partial discovery still renders local/fallback choices and its error,
      // but must retry the unavailable provider on the next picker request.
      if(j.llm_error || j.stt_llm_error) clearFailedCache();
      else {
        state.lastModelsQuery=query;
        state.lastModelsPayload=j;
        state.lastModelsNeedsRefresh=false;
      }
    }
    if(!isCurrent()) return;
    if (audioUnsupported !== null) state.audioUnsupported = audioUnsupported;

    const llmStatuses = j.llm_models_status || {};
    const sttStatuses = j.stt_llm_models_status || {};

    if(Array.isArray(j.whisper_models) && j.whisper_models.length){
      const current=sttModel(); $("sttModel").innerHTML="";
      for(const m of j.whisper_models){ const o=document.createElement("option"); o.value=m; o.textContent=m; $("sttModel").appendChild(o); }
      $("sttModel").value=j.whisper_models.includes(current)?current:(j.whisper_models.includes("base.en")?"base.en":j.whisper_models[0]);
    }

    const currentResponse = lmModel();
    $("lmModel").innerHTML="";
    if(Array.isArray(j.llm_models) && j.llm_models.length){
      for(const m of j.llm_models){
        const o=document.createElement("option");
        o.value=m;
        applyModelOptionStatus(o, (j.labels&&j.labels[m])||prettyModelLabel(m), modelStatus(llmStatuses, m), (j.llm_models_capabilities||{})[m], { responseModel: true });
        $("lmModel").appendChild(o);
      }
    } else if($("lmModel")){
      const localPool = localModelPool(j);
      if(localPool.length){
        let lastGroup = null;
        for(const entry of localPool){
          const o=document.createElement("option");
          o.value=entry.id;
          const baseLabel=(j.labels&&j.labels[entry.id])||prettyModelLabel(entry.id);
          const label = entry.group !== lastGroup ? `[${entry.group}] ${baseLabel}` : baseLabel;
          applyModelOptionStatus(o, label, modelStatus(llmStatuses, entry.id), (j.llm_models_capabilities||{})[entry.id], { responseModel: true });
          if(entry.group !== lastGroup){ lastGroup = entry.group; }
          $("lmModel").appendChild(o);
        }
      }
    }
    let unlistedSelection = keepModelSelection($("lmModel"), currentResponse, j.llm_default,
      { statuses: llmStatuses, capabilities: j.llm_models_capabilities, responseModel: true });

    let sttFilterInfo = "";
    if($("sttLlmModel")){
      const current=sttLlmModel();
      $("sttLlmModel").innerHTML="";
      const blank=document.createElement("option"); blank.value=""; blank.textContent="Inherit from LLM Response"; $("sttLlmModel").appendChild(blank);
      // Apply the capability filter to the multimodal STT picker. Default
      // (override=false) drops text-only models like google/gemma-4-e4b
      // so the user can't accidentally pick one for STT and pay a 4s
      // multimodal round-trip per utterance. The override checkbox in
      // Settings flips this to 'any'.
      const filteredStt = filterModelsByCapability(
        j.stt_llm_models || [],
        j.stt_llm_models_capabilities || {},
        sttLlmModelRequirement(),
      );
      for(const m of filteredStt){
        const o=document.createElement("option");
        o.value=m;
        applyModelOptionStatus(
          o,
          (j.labels&&j.labels[m])||prettyModelLabel(m),
          modelStatus(sttStatuses, m),
          (j.stt_llm_models_capabilities||{})[m],
        );
        $("sttLlmModel").appendChild(o);
      }
      const allSttIds = Array.from($("sttLlmModel").options).map(o => o.value).filter(Boolean);
      if(allSttIds.length === 0){
        const localPool = localModelPool(j);
        if(localPool.length){
          // Apply the same capability filter to the local-pool fallback.
          const filteredPool = filterModelsByCapability(
            localPool.map(e => e.id),
            j.stt_llm_models_capabilities || {},
            sttLlmModelRequirement(),
          ).map(id => localPool.find(e => e.id === id)).filter(Boolean);
          let lastGroup = null;
          for(const entry of filteredPool){
            const o=document.createElement("option");
            o.value=entry.id;
            const baseLabel=(j.labels&&j.labels[entry.id])||prettyModelLabel(entry.id);
            const label = entry.group !== lastGroup ? `[${entry.group}] ${baseLabel}` : baseLabel;
            applyModelOptionStatus(o, label, modelStatus(sttStatuses, entry.id), (j.stt_llm_models_capabilities||{})[entry.id]);
            if(entry.group !== lastGroup){ lastGroup = entry.group; }
            $("sttLlmModel").appendChild(o);
          }
        }
      }
      unlistedSelection = keepModelSelection($("sttLlmModel"), current, '',
        { statuses: sttStatuses, capabilities: j.stt_llm_models_capabilities }) || unlistedSelection;
      const renderedSttIds = Array.from($("sttLlmModel").options).map(o => o.value).filter(Boolean);
      const sttRed = renderedSttIds.filter(id => modelStatus(sttStatuses, id)?.usable === false).length;
      sttFilterInfo = sttRed > 0 ? ` · ${sttRed} shown in red as likely unusable` : "";

      syncAudioProbeButton();
    }
    const previousStatus = $("modelOptionsStatus").textContent;
    syncConverseSettingsUI();
    $("modelOptionsStatus").textContent = statusRequest === modelStatusRequest
      ? `Loaded ${(j.llm_models||[]).length} response models from ${j.llm_base_url||llmBaseUrl()} and ${(j.stt_llm_models||[]).length} multimodal STT models from ${j.stt_llm_base_url||effectiveSttLlmBaseUrl()}. ${j.llm_error?("LLM error: "+j.llm_error+" "):""}${j.stt_llm_error?("STT multimodal error: "+j.stt_llm_error):""}${sttFilterInfo} ${catalogNotice(j.catalog_info)} ${unlistedSelection ? 'Saved selections not listed by discovery were retained.' : ''} ${probeWarning}`.trim()
      : previousStatus;
    return j;
  }catch(e){
    if(!isCurrent()) return;
    state.lastModelsNeedsRefresh=true;
    if(statusRequest === modelStatusRequest) $("modelOptionsStatus").textContent="Model list load failed: "+e.message;
  } finally {
    if (modelsController === controller) modelsController = null;
  }
}

export function loadAgentModelOptions(force=true){
  const query = JSON.stringify(agentDiscoverySettings());
  if (!force && agentModelsInFlight?.query === query) return agentModelsInFlight.promise;
  if (!force && agentModelsLoaded?.select === $("agentModel") && agentModelsLoaded.query === query) return Promise.resolve();
  const flight = { query, promise: null };
  flight.promise = discoverAgentModelOptions().finally(() => {
    if (agentModelsInFlight === flight) agentModelsInFlight = null;
  });
  agentModelsInFlight = flight;
  return flight.promise;
}

async function discoverAgentModelOptions(){
  agentModelsController?.abort();
  const controller = new AbortController();
  agentModelsController = controller;
  const request = ++agentModelsRequest;
  const statusRequest = ++modelStatusRequest;
  const query = JSON.stringify(agentDiscoverySettings());
  const isCurrent = () => request === agentModelsRequest && query === JSON.stringify(agentDiscoverySettings());
  try{
    const r=await fetch("/api/agent/models", {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: query, signal: controller.signal,
    });
    const j=await discoveryJson(r, controller.signal, isCurrent);
    if(!isCurrent()) return;
    if(!j || j.ok === false || !Array.isArray(j.models)) throw new Error(j?.error || 'invalid agent model list response');
    const current=$("agentModel")?.value||"";
    const bounded = boundedModelIds(j.models, [current]);
    if($("agentModel")){
      $("agentModel").innerHTML="";
      const blank=document.createElement("option"); blank.value=""; blank.textContent="Use Korina Converse model"; $("agentModel").appendChild(blank);
      for(const m of bounded.ids){ const o=document.createElement("option"); o.value=m; o.textContent=prettyModelLabel(m); $("agentModel").appendChild(o); }
      keepModelSelection($("agentModel"), current);
    }
    agentModelsLoaded = { select: $("agentModel"), query };
    if(statusRequest === modelStatusRequest) $("modelOptionsStatus").textContent=`Loaded ${bounded.ids.length} Korina Agent models from ${String(j.provider || '').slice(0,64)}. ${catalogNotice({ server: j.catalog_info, browser: bounded.info })}`.trim();
  }catch(e){
    if(isCurrent()) agentModelsLoaded = null;
    if(isCurrent() && statusRequest === modelStatusRequest) $("modelOptionsStatus").textContent="Agent model list load failed: "+e.message;
  } finally {
    if (agentModelsController === controller) agentModelsController = null;
  }
}

function selectedAudioProbe() {
  const provider = effectiveSttLlmProvider();
  const baseUrl = normalizeTripleBaseUrl(effectiveSttLlmBaseUrl());
  const model = effectiveSttLlmModel();
  return { provider, baseUrl, model, triple: `${provider}::${baseUrl}::${model}` };
}

export function syncAudioProbeButton() {
  if (modelsInFlight && modelsInFlight.query !== modelDiscoveryQuery()) modelsController?.abort();
  const button = $("clearProbeBtn");
  if (!button) return;
  if (probeReset && !probeReset.isCurrent()) retireProbeReset(probeReset);
  const selected = selectedAudioProbe();
  const probe = selected.model && state.audioUnsupported?.[selected.triple];
  button.style.display = probe ? '' : 'none';
  button.dataset.provider = probe ? selected.provider : '';
  button.dataset.baseUrl = probe ? selected.baseUrl : '';
  button.dataset.model = probe ? selected.model : '';
  button.title = probe ? String(probe.reason || '') : '';
}

function retireProbeReset(operation) {
  if (probeReset !== operation) return;
  probeReset = null;
  operation.controller.abort();
  $("clearProbeBtn").disabled = false;
  if (operation.action.textContent === operation.pending) operation.action.textContent = '';
}

export async function resetSelectedAudioProbe() {
  const button = $("clearProbeBtn"), action = $("settingsActionStatus");
  const selected = selectedAudioProbe();
  if (!button || !selected.model) return;
  if (probeReset) retireProbeReset(probeReset);
  const query = modelDiscoveryQuery();
  const operation = { controller: new AbortController(), action, pending: 'Resetting audio capability probe…' };
  const isCurrent = () => probeReset === operation && query === modelDiscoveryQuery()
    && selected.triple === selectedAudioProbe().triple;
  operation.isCurrent = isCurrent;
  probeReset = operation;
  const { controller, pending } = operation;
  action.textContent = pending;
  button.disabled = true;
  try {
    await clearAudioProbe(selected.provider, selected.baseUrl, selected.model, { signal: controller.signal, isCurrent });
    if (!isCurrent()) return;
    const probes = { ...state.audioUnsupported };
    delete probes[selected.triple];
    state.audioUnsupported = probes;
    syncAudioProbeButton();
    if (action.textContent === pending) action.textContent = 'Audio capability probe reset. Try transcription again.';
    // The DELETE is confirmed. Optional model discovery owns its own errors
    // and must not keep the reset control waiting on another network request.
    void loadModelOptions(true);
  } catch (error) {
    if (isCurrent() && action.textContent === pending) action.textContent = `Audio probe reset failed: ${error.message}. Retry Reset audio probe.`;
  } finally {
    if (probeReset === operation) {
      button.disabled = false;
      probeReset = null;
    }
  }
}
