// js/local-models.js
//
// Frontend controller for the "Local GGUF models" settings section.

import { loadModelOptions } from './providers-ui.js';
import { saveConfigSoon } from './api.js';
import {
  llmProvider,
  effectiveSttLlmProvider,
  effectiveSttLlmBaseUrl,
} from './settings-ui.js';

const listEl = () => document.getElementById('localModelRootsList');
const statusEl = () => document.getElementById('localModelsStatus');
const inputEl = () => document.getElementById('newLocalModelRoot');
const refreshBtn = () => document.getElementById('refreshLocalModelsBtn');
const addBtn = () => document.getElementById('addLocalModelRootBtn');

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
      setStatus('Removing ' + entry.path + '…');
      try {
        await fetch('/api/models/roots?path=' + encodeURIComponent(entry.path), { method: 'DELETE' });
        await loadLocalModelRoots();
        await loadModelOptions(true);
        setStatus('Removed ' + entry.path, 'good');
      } catch (e) {
        setStatus('Remove failed: ' + e.message, 'bad');
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
  try {
    const roots = await fetchRoots();
    renderList(roots);
    return roots;
  } catch (e) {
    renderList([]);
    setStatus('Failed to load roots: ' + e.message, 'bad');
    return [];
  }
}

async function addRoot() {
  const path = String(inputEl()?.value || '').trim();
  if (!path) {
    setStatus('Path is empty.', 'bad');
    return;
  }
  setStatus('Adding ' + path + '…');
  try {
    const r = await fetch('/api/models/roots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ roots: [path] }),
    });
    if (!r.ok) {
      // Dogfood pass 2026-06-26: parse the FastAPI JSON envelope so the
      // user sees "path not found: /tmp/…" instead of the raw body
      // {"detail":"path not found: /tmp/…"}.
      let detail = '';
      try {
        const j = await r.json();
        detail = j?.detail || '';
      } catch (_) {
        detail = await r.text().catch(() => '');
      }
      throw new Error(detail || ('HTTP ' + r.status));
    }
    if (inputEl()) inputEl().value = '';
    await loadLocalModelRoots();
    await loadModelOptions(true);
    setStatus('Added ' + path, 'good');
  } catch (e) {
    setStatus('Add failed: ' + e.message, 'bad');
  }
}

// Phase 5.0: actively probe audio-capability of the currently-known model
// pool for the active STT provider. Sends one tiny synthetic audio request
// per candidate to the running multimodal endpoint and updates the
// audio_unsupported cache that the dropdown consults. Called from
// refreshLocalModels after the existing /api/llm/llama/refresh call returns
// so the user only triggers probes when they actively want a refresh.
async function probeActiveModels() {
  const provider = String(effectiveSttLlmProvider() || '').trim();
  const baseUrl = String(effectiveSttLlmBaseUrl() || '').trim();
  if (!provider || !baseUrl) return;
  // The active audio probe belongs to the llama.cpp local-GGUF refresh path.
  // LM Studio, Ollama, and openai-compatible providers expose their own
  // /v1/models endpoints and should not be probed by the local-model refresh.
  if (provider !== 'llama.cpp') return;

  // Pull the currently displayed model pool. loadModelOptions(true) above
  // populates state.lastModelsPayload; re-fetch it explicitly so we probe
  // the same IDs the dropdown shows.
  let payload = null;
  try {
    const params = new URLSearchParams({
      llm_base_url: baseUrl,
      stt_llm_base_url: baseUrl,
      llm_provider: provider,
      stt_llm_provider: provider,
    });
    const r = await fetch('/api/models?' + params.toString());
    payload = await r.json();
  } catch (_) {
    return;
  }
  const allIds = new Set([
    ...(payload.llm_models || []),
    ...(payload.stt_llm_models || []),
    ...(payload.llama_cpp_local_models || []),
    ...(payload.lmstudio_catalog_models || []),
  ]);
  // Filter out mmproj sidecars; they aren't real models.
  const candidates = Array.from(allIds).filter(
    (id) => id && !/mmproj/i.test(String(id))
  );
  if (candidates.length === 0) return;

  setStatus('Probing audio support for ' + candidates.length + ' model' + (candidates.length === 1 ? '' : 's') + '…');

  let probed = 0;
  let failed = 0;
  let transient = 0;
  try {
    const r = await fetch('/api/llm/probe-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, base_url: baseUrl, models: candidates }),
    });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    for (const id of Object.keys(j.results || {})) {
      probed++;
      if (!j.results[id].supported) {
        if (j.results[id].cached) failed++;
        else transient++;
      }
    }
  } catch (e) {
    setStatus('Probe failed: ' + (e.message || e), 'bad');
    return;
  }

  // Re-render the dropdown so red/green state reflects the fresh probe cache.
  await loadModelOptions(true);

  const parts = ['Probed ' + probed + ' model' + (probed === 1 ? '' : 's')];
  if (failed > 0) parts.push(failed + ' confirmed audio-unsupported');
  if (transient > 0) parts.push(transient + ' transient (no cache)');
  setStatus(parts.join(' · ') + '. Dropdown refreshed.', 'good');
}

async function refreshLocalModels() {
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
    if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
    await loadLocalModelRoots();
    await loadModelOptions(true);
    const cacheNote = j.cleared_audio_probes ? (' · cleared ' + j.cleared_audio_probes + ' stale audio probe cache entr' + (j.cleared_audio_probes === 1 ? 'y' : 'ies')) : '';
    if (j.restarted) {
      setStatus('Refreshed · ' + j.discovered_count + ' models · llama.cpp restarted on ' + j.model + cacheNote, 'good');
    } else {
      setStatus('Refreshed · ' + j.discovered_count + ' models · ' + (j.note || 'no restart') + cacheNote, 'good');
    }
    // After the refresh, actively probe so red/green state is fresh.
    await probeActiveModels();
  } catch (e) {
    setStatus('Refresh failed: ' + e.message, 'bad');
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
