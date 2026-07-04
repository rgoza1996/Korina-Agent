// js/providers-ui.js
//
// Frontend provider-capability and model-options helpers extracted
// from the original inline script and adapted for red warning states.

import { state } from "./state.js";
import { $ } from "./dom.js";
import { prettyModelLabel } from "./labels.js";
import {
  llmBaseUrl,
  effectiveSttLlmProvider,
  effectiveSttLlmBaseUrl,
  effectiveSttLlmApiKeyEnv,
  sttModel,
  lmModel,
  sttLlmModel,
  syncConverseSettingsUI,
} from "./settings-ui.js";
import {
  saveConfigNow,
  listAudioProbes,
  clearAudioProbe,
  normalizeTripleBaseUrl,
} from "./api.js";
import {
  filterModelsByCapability,
  sttLlmModelRequirement,
  setCapabilityFilterOverride,
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

export function providerPresetBaseUrl(provider){
  const caps = getResponseLlmProviderCaps(provider);
  return caps ? String(caps.default_base_url || "") : "";
}

export function maybeApplyProviderPreset(providerId, baseUrlId, {clearWhenBlank=false}={}){
  const provider=$(providerId)?.value||"";
  const input=$(baseUrlId);
  if(!input) return false;
  const isAgent = providerId === "agentProvider";
  const caps = (isAgent ? getAgentProviderCaps(provider) : getResponseLlmProviderCaps(provider));
  const preset = caps ? String(caps.default_base_url || "") : providerPresetBaseUrl(provider);
  if(preset){ input.value=preset; return true; }
  if(clearWhenBlank && !provider){ input.value=""; return true; }
  return false;
}

function modelStatus(statusDict, modelId){
  return statusDict ? (statusDict[String(modelId || "")] || null) : null;
}

function applyModelOptionStatus(option, baseLabel, status){
  const summary = String(status?.summary || "").trim();
  const reasons = Array.isArray(status?.reasons) ? status.reasons.map(x => String(x || "").trim()).filter(Boolean) : [];
  option.classList.remove('modelOptionBad');
  option.style.color = '';
  option.style.fontWeight = '';
  option.dataset.unusable = 'false';
  option.textContent = baseLabel;
  option.title = '';
  if (status && status.usable === false) {
    option.classList.add('modelOptionBad');
    option.style.color = '#ff7b72';
    option.style.fontWeight = '600';
    option.dataset.unusable = 'true';
    option.textContent = `${baseLabel} [red: ${summary || 'likely unusable'}]`;
    option.title = reasons.join(' • ');
  }
}

export async function setBaseUrlEditability(){
  await loadCapabilities();
  const llmId = $("llmProvider")?.value || "";
  const llmCaps = getResponseLlmProviderCaps(llmId);
  const llmEditable = llmCaps ? !!llmCaps.editable_base_url : (llmId === "openai-compatible");
  if($("llmBaseUrl")) $("llmBaseUrl").disabled = !llmEditable;
  const sttId = String($("sttLlmProvider")?.value || "").trim();
  const sttCaps = getResponseLlmProviderCaps(sttId);
  const sttEditable = sttCaps ? !!sttCaps.editable_base_url : (sttId === "openai-compatible");
  if($("sttLlmBaseUrl")) $("sttLlmBaseUrl").disabled = !sttEditable;
  const agentId = String($("agentProvider")?.value || "").trim();
  const agentCaps = getAgentProviderCaps(agentId);
  const agentEditable = agentCaps ? !!agentCaps.editable_base_url : (agentId === "openai-compatible" || agentId === "anthropic");
  if($("agentBaseUrl")) $("agentBaseUrl").disabled = !agentEditable;
}

export async function loadModelOptions(force=false){
  try{
    try {
      const probeResp = await listAudioProbes();
      state.audioUnsupported = probeResp.entries || {};
    } catch (_) {
      state.audioUnsupported = {};
    }
    const params=new URLSearchParams({
      llm_base_url: llmBaseUrl(),
      llm_api_key_env: String($("llmApiKeyEnv")?.value||"").trim(),
      stt_llm_base_url: effectiveSttLlmBaseUrl(),
      stt_llm_api_key_env: effectiveSttLlmApiKeyEnv(),
      llm_provider: String($("llmProvider")?.value||"").trim(),
      stt_llm_provider: effectiveSttLlmProvider(),
    });
    const query=params.toString();
    let j=state.lastModelsPayload;
    if(force || !j || query!==state.lastModelsQuery){
      const r=await fetch(`/api/models?${query}`);
      j=await r.json();
      state.lastModelsQuery=query;
      state.lastModelsPayload=j;
      state.lastModelsLoadedAt=Date.now();
    }

    const llmStatuses = j.llm_models_status || {};
    const sttStatuses = j.stt_llm_models_status || {};

    if(Array.isArray(j.whisper_models) && j.whisper_models.length){
      const current=sttModel(); $("sttModel").innerHTML="";
      for(const m of j.whisper_models){ const o=document.createElement("option"); o.value=m; o.textContent=m; $("sttModel").appendChild(o); }
      $("sttModel").value=j.whisper_models.includes(current)?current:(j.whisper_models.includes("base.en")?"base.en":j.whisper_models[0]);
    }

    if(Array.isArray(j.llm_models) && j.llm_models.length){
      const current=lmModel(); $("lmModel").innerHTML="";
      for(const m of j.llm_models){
        const o=document.createElement("option");
        o.value=m;
        applyModelOptionStatus(o, (j.labels&&j.labels[m])||prettyModelLabel(m), modelStatus(llmStatuses, m));
        $("lmModel").appendChild(o);
      }
      $("lmModel").value=j.llm_models.includes(current)?current:(j.llm_default||j.llm_models[0]);
    } else if($("lmModel")){
      const localPool = [
        ...((j.llama_cpp_local_models || []).map(id => ({ id, group: 'llama.cpp (local GGUF)' }))),
        ...((j.lmstudio_catalog_models || []).map(id => ({ id, group: 'lmstudio (catalog)' }))),
      ];
      if(localPool.length){
        const current=lmModel();
        $("lmModel").innerHTML="";
        let lastGroup = null;
        for(const entry of localPool){
          const o=document.createElement("option");
          o.value=entry.id;
          const baseLabel=(j.labels&&j.labels[entry.id])||prettyModelLabel(entry.id);
          const label = entry.group !== lastGroup ? `[${entry.group}] ${baseLabel}` : baseLabel;
          applyModelOptionStatus(o, label, modelStatus(llmStatuses, entry.id));
          if(entry.group !== lastGroup){ lastGroup = entry.group; }
          $("lmModel").appendChild(o);
        }
        const saved = current || j.llm_default;
        const match = saved && localPool.some(e => e.id === saved);
        $("lmModel").value = match ? saved : localPool[0].id;
      }
    }

    let sttFilterInfo = "";
    if($("sttLlmModel")){
      const current=sttLlmModel();
      $("sttLlmModel").innerHTML="";
      const blank=document.createElement("option"); blank.value=""; blank.textContent="Inherit from LLM Response"; $("sttLlmModel").appendChild(blank);
      for(const m of (j.stt_llm_models || [])){
        const o=document.createElement("option");
        o.value=m;
        applyModelOptionStatus(o, (j.labels&&j.labels[m])||prettyModelLabel(m), modelStatus(sttStatuses, m));
        $("sttLlmModel").appendChild(o);
      }
      const allSttIds = Array.from($("sttLlmModel").options).map(o => o.value).filter(Boolean);
      if(allSttIds.length === 0){
        const localPool = [
          ...((j.llama_cpp_local_models || []).map(id => ({ id, group: 'llama.cpp (local GGUF)' }))),
          ...((j.lmstudio_catalog_models || []).map(id => ({ id, group: 'lmstudio (catalog)' }))),
        ];
        if(localPool.length){
          let lastGroup = null;
          for(const entry of localPool){
            const o=document.createElement("option");
            o.value=entry.id;
            const baseLabel=(j.labels&&j.labels[entry.id])||prettyModelLabel(entry.id);
            const label = entry.group !== lastGroup ? `[${entry.group}] ${baseLabel}` : baseLabel;
            applyModelOptionStatus(o, label, modelStatus(sttStatuses, entry.id));
            if(entry.group !== lastGroup){ lastGroup = entry.group; }
            $("sttLlmModel").appendChild(o);
          }
          const saved = current || j.stt_llm_default;
          const match = saved && localPool.some(e => e.id === saved);
          $("sttLlmModel").value = match ? saved : localPool[0].id;
        }
      } else {
        $("sttLlmModel").value=[...$("sttLlmModel").options].some(o=>o.value===current)?current:"";
      }
      const renderedSttIds = Array.from($("sttLlmModel").options).map(o => o.value).filter(Boolean);
      const sttRed = renderedSttIds.filter(id => modelStatus(sttStatuses, id)?.usable === false).length;
      sttFilterInfo = sttRed > 0 ? ` · ${sttRed} shown in red as likely unusable` : "";

      const clearBtn = $("clearProbeBtn");
      if (clearBtn) {
        const selected = $("sttLlmModel").value || $("lmModel")?.value || "";
        const provider = effectiveSttLlmProvider();
        const baseUrl = normalizeTripleBaseUrl(effectiveSttLlmBaseUrl());
        const triple = `${provider}::${baseUrl}::${selected}`;
        const probe = state.audioUnsupported?.[triple];
        if (selected && probe) {
          clearBtn.style.display = '';
          clearBtn.dataset.provider = provider;
          clearBtn.dataset.baseUrl = baseUrl;
          clearBtn.dataset.model = selected;
          clearBtn.title = String(probe.reason || '');
        } else {
          clearBtn.style.display = 'none';
          clearBtn.dataset.provider = '';
          clearBtn.dataset.baseUrl = '';
          clearBtn.dataset.model = '';
          clearBtn.title = '';
        }
      }
    }
    syncConverseSettingsUI();
    $("settingsInfo").textContent=`Loaded ${(j.llm_models||[]).length} response models from ${j.llm_base_url||llmBaseUrl()} and ${(j.stt_llm_models||[]).length} multimodal STT models from ${j.stt_llm_base_url||effectiveSttLlmBaseUrl()}. ${j.llm_error?("LLM error: "+j.llm_error+" "):""}${j.stt_llm_error?("STT multimodal error: "+j.stt_llm_error):""}${sttFilterInfo}`.trim();
  }catch(e){ $("settingsInfo").textContent="Model list load failed: "+e.message; }
}

export async function loadAgentModelOptions(){
  try{
    await saveConfigNow();
    const current=$("agentModel")?.value||"";
    const j=await (await fetch("/api/agent/models")).json();
    if($("agentModel")){
      $("agentModel").innerHTML="";
      const blank=document.createElement("option"); blank.value=""; blank.textContent="Use Korina Converse model"; $("agentModel").appendChild(blank);
      for(const m of (j.models||[])){ const o=document.createElement("option"); o.value=m; o.textContent=prettyModelLabel(m); $("agentModel").appendChild(o); }
      $("agentModel").value=[...$("agentModel").options].some(o=>o.value===current)?current:"";
    }
    $("settingsInfo").textContent=`Loaded ${(j.models||[]).length} Korina Agent models from ${j.provider}. ${j.error?("Error: "+j.error):""}`;
  }catch(e){ $("settingsInfo").textContent="Agent model list load failed: "+e.message; }
}
