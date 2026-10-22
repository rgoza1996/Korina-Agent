// js/local-models.js
//
// Frontend controller for the "Local GGUF models" settings section.

import { loadModelOptions, modelDiscoveryQuery } from './providers-ui.js';
import { apiErrorMessage } from './api-errors.js';
import { responseError } from './response-body.js';
import {
  llmProvider,
  effectiveSttLlmProvider,
  effectiveSttLlmBaseUrl,
  effectiveSttLlmApiKeyEnv,
  effectiveSttLlmModel,
} from './settings-ui.js';

const MAX_PROBE_MODELS = 64;

const listEl = () => document.getElementById('localModelRootsList');
const statusEl = () => document.getElementById('localModelsStatus');
const inputEl = () => document.getElementById('newLocalModelRoot');
const refreshBtn = () => document.getElementById('refreshLocalModelsBtn');
const addBtn = () => document.getElementById('addLocalModelRootBtn');
let rootsRequest = 0;
let localAction = 0;

function beginLocalAction() {
  const action = ++localAction;
  const query = modelDiscoveryQuery();
  return () => action === localAction && query === modelDiscoveryQuery();
}

function setStatus(text, cls) {
  const el = statusEl();
  if (!el) return;
  el.textContent = text || '';
  el.className = 'small mono' + (cls ? ' ' + cls : '');
}

function renderList(roots) {
  const ul = listEl();
  if (!ul) return;
  ul.innerHTML = '';
  if (!roots || roots.length === 0) {
    const li = document.createElement('li');
    li.className = 'settingsListEmpty small mono';
    li.textContent = 'No roots configured. Add a directory below.';
    ul.appendChild(li);
    return;
  }
  for (const entry of roots) {
    const li = document.createElement('li');
    li.className = 'settingsListRow';

    const path = document.createElement('code');
    path.className = 'mono';
    path.textContent = entry.path;
    path.style.flex = '1';

    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.title = entry.reason || (entry.exists && entry.is_directory ? 'ok' : 'unknown');
    if (entry.exists && entry.is_directory) dot.classList.add('good');
    else dot.classList.add('bad');

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'secondary';
    remove.textContent = 'Remove';
    remove.addEventListener('click', async () => {
      const isCurrent = beginLocalAction();
      setStatus('Removing ' + entry.path + '…');
      try {
        const response = await fetch('/api/models/roots?path=' + encodeURIComponent(entry.path), { method: 'DELETE' });
        if (!isCurrent()) return;
        if (!response.ok) throw new Error('HTTP ' + response.status);
        await loadLocalModelRoots();
        if (!isCurrent()) return;
        await loadModelOptions(true);
        if (isCurrent()) setStatus('Removed ' + entry.path, 'good');
      } catch (e) {
        if (isCurrent()) setStatus('Remove failed: ' + e.message, 'bad');
      }
    });

    li.appendChild(dot);
    li.appendChild(path);
    li.appendChild(remove);
    ul.appendChild(li);
  }
}

async function fetchRoots() {
  const r = await fetch('/api/models/roots');
  if (!r.ok) throw new Error('GET /api/models/roots -> ' + r.status);
  const j = await r.json();
  return j.roots || [];
}

export async function loadLocalModelRoots() {
  const request = ++rootsRequest;
  try {
    const roots = await fetchRoots();
    if (request === rootsRequest) renderList(roots);
    return roots;
  } catch (e) {
    if (request === rootsRequest) {
      renderList([]);
      setStatus('Failed to load roots: ' + e.message, 'bad');
    }
    return [];
  }
}

async function addRoot() {
  const path = String(inputEl()?.value || '').trim();
  if (!path) {
    setStatus('Path is empty.', 'bad');
    return;
  }
  const isCurrent = beginLocalAction();
  setStatus('Adding ' + path + '…');
  try {
    const r = await fetch('/api/models/roots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ roots: [path], append: true }),
    });
    if (!isCurrent()) return;
    if (!r.ok) {
      throw await responseError(r, { isCurrent });
    }
    if (!isCurrent()) return;
    if (inputEl()?.value.trim() === path) inputEl().value = '';
    await loadLocalModelRoots();
    if (!isCurrent()) return;
    await loadModelOptions(true);
    if (isCurrent()) setStatus('Added ' + path, 'good');
  } catch (e) {
    if (isCurrent()) setStatus('Add failed: ' + e.message, 'bad');
  }
}

// Phase 5.0: actively probe audio-capability of the currently-known model
// pool for the active STT provider. Sends one tiny synthetic audio request
// per candidate to the running multimodal endpoint and updates the
// audio_unsupported cache that the dropdown consults. Called from
// refreshLocalModels after the existing /api/llm/llama/refresh call returns
// so the user only triggers probes when they actively want a refresh.
async function probeActiveModels(payload, isCurrent) {
  if (!payload || !isCurrent()) return;
  const provider = String(effectiveSttLlmProvider() || '').trim();
  const baseUrl = String(effectiveSttLlmBaseUrl() || '').trim();
  if (!provider || !baseUrl) return;
  // The active audio probe belongs to the llama.cpp local-GGUF refresh path.
  // LM Studio, Ollama, and openai-compatible providers expose their own
  // /v1/models endpoints and should not be probed by the local-model refresh.
  if (provider !== 'llama.cpp') return;

  // Use the accepted discovery response for this action, avoiding a second
  // discovery request whose endpoint/settings might already have changed.
  const allIds = new Set([
    ...(payload.stt_llm_models || []),
    ...(payload.llama_cpp_local_models || []),
    ...(payload.lmstudio_catalog_models || []),
  ]);
  // Filter out mmproj sidecars; they aren't real models.
  const available = Array.from(allIds).filter(
    (id) => id && !/mmproj/i.test(String(id))
  );
  const selected = effectiveSttLlmModel();
  if (available.includes(selected)) available.splice(available.indexOf(selected), 1);
  if (selected && allIds.has(selected) && !/mmproj/i.test(selected)) available.unshift(selected);
  const candidates = available.slice(0, MAX_PROBE_MODELS);
  const omitted = available.length - candidates.length;
  if (!candidates.length) return;

  setStatus('Probing audio support for ' + candidates.length + ' model' + (candidates.length === 1 ? '' : 's') + '…');

  let probed = 0;
  let failed = 0;
  let transient = 0;
  let skipped = 0;
  try {
    const r = await fetch('/api/llm/probe-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, base_url: baseUrl, api_key_env: effectiveSttLlmApiKeyEnv(), models: candidates }),
    });
    if (!isCurrent()) return;
    if (!r.ok) throw await responseError(r, { isCurrent });
    const j = await r.json();
    if (!isCurrent()) return;
    skipped = Array.isArray(j.skipped_models) ? j.skipped_models.length : 0;
    for (const id of Object.keys(j.results || {})) {
      probed++;
      if (!j.results[id].supported) {
        if (j.results[id].cached) failed++;
        else transient++;
      }
    }
  } catch (e) {
    if (isCurrent()) setStatus('Probe failed: ' + (e.message || e), 'bad');
    return;
  }

  // Re-render the dropdown so red/green state reflects the fresh probe cache.
  await loadModelOptions(true);
  if (!isCurrent()) return;

  const parts = ['Probed ' + probed + ' model' + (probed === 1 ? '' : 's')];
  if (failed > 0) parts.push(failed + ' confirmed audio-unsupported');
  if (transient > 0) parts.push(transient + ' transient (no cache)');
  if (omitted) parts.push(omitted + ' catalog choices omitted (64-model batch limit)');
  if (skipped) parts.push(skipped + ' not probed (batch time limit)');
  setStatus(parts.join(' · ') + '. Dropdown refreshed.', omitted || skipped ? 'warn' : 'good');
}

async function refreshLocalModels() {
  const isCurrent = beginLocalAction();
  const responseProvider = String(llmProvider() || '').trim();
  const sttProvider = String(effectiveSttLlmProvider() || '').trim();
  if (responseProvider !== 'llama.cpp' && sttProvider !== 'llama.cpp') {
    setStatus('Refresh local models only applies to llama.cpp. LM Studio, Ollama, and OpenAI-compatible endpoints are queried through their /v1/models endpoint by the model dropdown refresh.', 'warn');
    await loadModelOptions(true);
    return;
  }
  setStatus('Re-walking GGUF roots…');
  try {
    const r = await fetch('/api/llm/llama/refresh', { method: 'POST' });
    const j = await r.json();
    if (!isCurrent()) return;
    if (!r.ok) throw new Error(apiErrorMessage(j, 'HTTP ' + r.status));
    await loadLocalModelRoots();
    if (!isCurrent()) return;
    const payload = await loadModelOptions(true);
    if (!isCurrent()) return;
    const cacheNote = j.cleared_audio_probes ? (' · cleared ' + j.cleared_audio_probes + ' stale audio probe cache entr' + (j.cleared_audio_probes === 1 ? 'y' : 'ies')) : '';
    if (j.restarted) {
      setStatus('Refreshed · ' + j.discovered_count + ' models · llama.cpp restarted on ' + j.model + cacheNote, 'good');
    } else {
      setStatus('Refreshed · ' + j.discovered_count + ' models · ' + (j.note || 'no restart') + cacheNote, 'good');
    }
    // After the refresh, actively probe so red/green state is fresh.
    await probeActiveModels(payload, isCurrent);
  } catch (e) {
    if (isCurrent()) setStatus('Refresh failed: ' + e.message, 'bad');
  }
}

export function wireLocalModelsUi() {
  if (window.__korinaLocalModelsWired) return;
  window.__korinaLocalModelsWired = true;
  refreshBtn()?.addEventListener('click', refreshLocalModels);
  addBtn()?.addEventListener('click', addRoot);
  inputEl()?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addRoot(); }
  });
  const modal = document.getElementById('settingsModal');
  if (modal) {
    new MutationObserver(() => {
      if (modal.classList.contains('open')) {
        loadLocalModelRoots();
      }
    }).observe(modal, { attributes: true, attributeFilter: ['class'] });
  }
  loadLocalModelRoots();
}
