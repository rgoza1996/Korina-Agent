// js/local-models.js
//
// Frontend controller for the "Local GGUF models" settings section.

import { loadModelOptions } from './providers-ui.js';
import { saveConfigSoon } from './api.js';

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
      const txt = await r.text();
      throw new Error(txt || ('HTTP ' + r.status));
    }
    if (inputEl()) inputEl().value = '';
    await loadLocalModelRoots();
    await loadModelOptions(true);
    setStatus('Added ' + path, 'good');
  } catch (e) {
    setStatus('Add failed: ' + e.message, 'bad');
  }
}

async function refreshLocalModels() {
  setStatus('Re-walking GGUF roots…');
  try {
    const r = await fetch('/api/llm/llama/refresh', { method: 'POST' });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
    await loadLocalModelRoots();
    await loadModelOptions(true);
    if (j.restarted) {
      setStatus('Refreshed · ' + j.discovered_count + ' models · llama.cpp restarted on ' + j.model, 'good');
    } else {
      setStatus('Refreshed · ' + j.discovered_count + ' models · ' + (j.note || 'no restart'), 'good');
    }
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