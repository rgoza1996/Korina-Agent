// js/providers-ui.js
//
// Frontend provider-capability and model-options helpers extracted
// verbatim from the original inline <script> in Korina/index.html
// (lines 359–391, 393–410, 630–696 at commit bdda3cc). Bare top-level
// globals from the inline script have been migrated to the
// consolidated `state` object (see state.js):
//   - `_capabilitiesCache`  →  state._capabilitiesCache
//   - `lastModelsQuery`     →  state.lastModelsQuery
//   - `lastModelsLoadedAt`  →  state.lastModelsLoadedAt
//   - `lastModelsPayload`   →  state.lastModelsPayload
// Bare function references (e.g. `llmBaseUrl()`, `prettyModelLabel(...)`,
// `syncConverseSettingsUI()`, `saveConfigNow()`) are imported here from
// their owning modules (settings-ui.js, labels.js, api.js).
//
// Consumers (e.g. app.js) import the named exports below.

import { state } from "./state.js";
import { $ } from "./dom.js";
import { prettyModelLabel } from "./labels.js";
import {
  llmBaseUrl,
  effectiveSttLlmBaseUrl,
  effectiveSttLlmApiKeyEnv,
  sttModel,
  lmModel,
  sttLlmModel,
  syncConverseSettingsUI,
} from "./settings-ui.js";
import { saveConfigNow } from "./api.js";

// --- Capabilities (verbatim from index.html:359–391) ---

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
  // agent provider uses agent_providers section
  const isAgent = providerId === "agentProvider";
  const caps = (isAgent ? getAgentProviderCaps(provider) : getResponseLlmProviderCaps(provider));
  const preset = caps ? String(caps.default_base_url || "") : providerPresetBaseUrl(provider);
  if(preset){ input.value=preset; return true; }
  if(clearWhenBlank && !provider){ input.value=""; return true; }
  return false;
}

// --- Base-URL editability (verbatim from index.html:393–410) ---

export async function setBaseUrlEditability(){
  await loadCapabilities();
  // response-LLM
  const llmId = $("llmProvider")?.value || "";
  const llmCaps = getResponseLlmProviderCaps(llmId);
  const llmEditable = llmCaps ? !!llmCaps.editable_base_url : (llmId === "openai-compatible");
  if($("llmBaseUrl")) $("llmBaseUrl").disabled = !llmEditable;
  // multimodal-STT (uses response-LLM provider list)
  const sttId = String($("sttLlmProvider")?.value || "").trim();
  const sttCaps = getResponseLlmProviderCaps(sttId);
  const sttEditable = sttCaps ? !!sttCaps.editable_base_url : (sttId === "openai-compatible");
  if($("sttLlmBaseUrl")) $("sttLlmBaseUrl").disabled = !sttEditable;
  // agent (NEW: this was missing)
  const agentId = String($("agentProvider")?.value || "").trim();
  const agentCaps = getAgentProviderCaps(agentId);
  const agentEditable = agentCaps ? !!agentCaps.editable_base_url : (agentId === "openai-compatible" || agentId === "anthropic");
  if($("agentBaseUrl")) $("agentBaseUrl").disabled = !agentEditable;
}

// --- Model options (verbatim from index.html:630–696) ---

export async function loadModelOptions(force=false){
  try{
    const params=new URLSearchParams({
      llm_base_url: llmBaseUrl(),
      llm_api_key_env: String($("llmApiKeyEnv")?.value||"").trim(),
      stt_llm_base_url: effectiveSttLlmBaseUrl(),
      stt_llm_api_key_env: effectiveSttLlmApiKeyEnv(),
    });
    const query=params.toString();
    let j=state.lastModelsPayload;
    if(force || !j || query!==state.lastModelsQuery || (Date.now()-state.lastModelsLoadedAt)>4000){
      const r=await fetch(`/api/models?${query}`);
      j=await r.json();
      state.lastModelsQuery=query;
      state.lastModelsPayload=j;
      state.lastModelsLoadedAt=Date.now();
    }
    if(Array.isArray(j.whisper_models) && j.whisper_models.length){
      const current=sttModel(); $("sttModel").innerHTML="";
      for(const m of j.whisper_models){ const o=document.createElement("option"); o.value=m; o.textContent=m; $("sttModel").appendChild(o); }
      $("sttModel").value=j.whisper_models.includes(current)?current:(j.whisper_models.includes("base.en")?"base.en":j.whisper_models[0]);
    }
    if(Array.isArray(j.llm_models) && j.llm_models.length){
      const current=lmModel(); $("lmModel").innerHTML="";
      for(const m of j.llm_models){ const o=document.createElement("option"); o.value=m; o.textContent=(j.labels&&j.labels[m])||prettyModelLabel(m); $("lmModel").appendChild(o); }
      $("lmModel").value=j.llm_models.includes(current)?current:(j.llm_default||j.llm_models[0]);
    }
    if($("sttLlmModel")){
      const current=sttLlmModel();
      $("sttLlmModel").innerHTML="";
      const blank=document.createElement("option"); blank.value=""; blank.textContent="Inherit from LLM Response"; $("sttLlmModel").appendChild(blank);
      for(const m of (j.stt_llm_models||[])){ const o=document.createElement("option"); o.value=m; o.textContent=(j.labels&&j.labels[m])||prettyModelLabel(m); $("sttLlmModel").appendChild(o); }
      $("sttLlmModel").value=[...$("sttLlmModel").options].some(o=>o.value===current)?current:"";
    }
    syncConverseSettingsUI();
    $("settingsInfo").textContent=`Loaded ${(j.llm_models||[]).length} response models from ${j.llm_base_url||llmBaseUrl()} and ${(j.stt_llm_models||[]).length} multimodal STT models from ${j.stt_llm_base_url||effectiveSttLlmBaseUrl()}. ${j.llm_error?("LLM error: "+j.llm_error+" "):""}${j.stt_llm_error?("STT multimodal error: "+j.stt_llm_error):""}`.trim();
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
