// js/endpoints.js
//
// Endpoints modal — shows every URL Korina currently has wired, each as a
// clickable <a target="_blank"> link. Page-server endpoints (everything
// under /api/...) are local; LLM/STT/TTS/agent endpoints are read from
// /api/health so the list always reflects the live, server-resolved URLs.
//
// Exports:
//   openEndpoints()       - show the modal, populate it from /api/health
//   closeEndpoints()      - hide the modal
//   populateEndpoints()   - re-fetch /api/health and rebuild the list
//                          (also exposed for the Refresh button)

import { $ } from './dom.js';

const PAGE_SERVER_ENDPOINTS = [
  { group: 'Page server', key: 'GET  /api/health', path: '/api/health' },
  { group: 'Page server', key: 'GET  /api/config', path: '/api/config' },
  { group: 'Page server', key: 'GET  /api/capabilities', path: '/api/capabilities' },
  { group: 'Page server', key: 'GET  /api/models', path: '/api/models' },
  { group: 'Page server', key: 'GET  /api/models/roots', path: '/api/models/roots' },
  { group: 'Page server', key: 'GET  /api/acks', path: '/api/acks' },
  { group: 'Page server', key: 'GET  /api/acks/status', path: '/api/acks/status' },
  { group: 'Page server', key: 'GET  /api/audio-probe', path: '/api/audio-probe' },
  { group: 'Page server', key: 'GET  /api/agent/status', path: '/api/agent/status' },
  { group: 'Page server', key: 'GET  /api/agent/events', path: '/api/agent/events' },
  { group: 'Page server', key: 'GET  /api/agent/models', path: '/api/agent/models' },
  { group: 'Page server', key: 'POST /api/chat', path: '/api/chat', method: 'POST' },
  { group: 'Page server', key: 'POST /api/transcribe', path: '/api/transcribe', method: 'POST' },
  { group: 'Page server', key: 'POST /api/transcribe/stream', path: '/api/transcribe/stream', method: 'POST' },
  { group: 'Page server', key: 'POST /api/transcribe/partial', path: '/api/transcribe/partial', method: 'POST' },
  { group: 'Page server', key: 'POST /api/config', path: '/api/config', method: 'POST' },
  { group: 'Page server', key: 'POST /api/llm/provider/activate', path: '/api/llm/provider/activate', method: 'POST' },
  { group: 'Page server', key: 'POST /api/llm/probe-models', path: '/api/llm/probe-models', method: 'POST' },
  { group: 'Page server', key: 'POST /api/llm/llama/refresh', path: '/api/llm/llama/refresh', method: 'POST' },
  { group: 'Page server', key: 'POST /api/models/roots', path: '/api/models/roots', method: 'POST' },
  { group: 'Page server', key: 'POST /api/acks/rebuild', path: '/api/acks/rebuild', method: 'POST' },
  { group: 'Page server', key: 'POST /api/agent/transcript', path: '/api/agent/transcript', method: 'POST' },
  { group: 'Page server', key: 'POST /api/agent/state-report', path: '/api/agent/state-report', method: 'POST' },
  { group: 'Page server', key: 'POST /api/agent/permission-answer', path: '/api/agent/permission-answer', method: 'POST' },
  { group: 'Page server', key: 'POST /api/agent/reset', path: '/api/agent/reset', method: 'POST' },
  { group: 'Page server', key: 'DELETE /api/audio-probe', path: '/api/audio-probe', method: 'DELETE' },
  { group: 'Page server', key: 'DELETE /api/models/roots', path: '/api/models/roots', method: 'DELETE' },
  { group: 'Page server', key: 'GET  /openapi.json', path: '/openapi.json' },
  { group: 'Page server', key: 'GET  /docs', path: '/docs' },
];

const EXTERNAL_GROUPS = [
  {
    group: 'Response LLM',
    loadKey: 'response_llm_load',
    keys: [
      { key: 'base_url', path: ['response_llm_base_url'] },
      { key: 'chat_url', path: ['response_llm_chat_url'] },
      { key: 'models',   path: ['response_llm_base_url'], append: '/models' },
    ],
  },
  {
    group: 'Multimodal STT',
    loadKey: 'multimodal_stt_load',
    keys: [
      { key: 'base_url', path: ['multimodal_stt_base_url'] },
      { key: 'chat_url', path: ['multimodal_stt_chat_url'] },
      { key: 'models',   path: ['multimodal_stt_base_url'], append: '/models' },
    ],
  },
  {
    group: 'Agent',
    loadKey: 'agent_load',
    keys: [
      { key: 'base_url', path: ['agent_base_url'] },
      { key: 'chat_url', path: ['agent_chat_url'] },
      { key: 'models',   path: ['agent_base_url'], append: '/models' },
    ],
  },
  {
    group: 'TTS',
    loadKey: 'tts',
    keys: [
      { key: 'base_url', path: ['tts_base_url'] },
      { key: 'health',   path: ['tts', 'base_url'], append: '/health' },
    ],
  },
];

function _externalize(url) {
  if (!url) return url;
  let u = String(url);
  try {
    if (typeof location !== 'undefined' && location.hostname) {
      const host = location.hostname;
      u = u.replace(/127\.0\.0\.1|localhost/i, host);
    }
  } catch (_) {}
  return u;
}

function _rowHtml(key, url, disabled, title) {
  const safeUrl = String(url);
  const cls = 'link' + (disabled ? ' disabled' : '');
  const href = disabled ? '#' : safeUrl;
  const attrs = disabled
    ? `class="${cls}" aria-disabled="true" title="${_escape(title || 'server reports this endpoint as unreachable')}" rel="noopener"`
    : `class="${cls}" href="${_escape(href)}" target="_blank" rel="noopener" title="${_escape(safeUrl)}"`;
  return `<div class="endpointsRow"><span class="key">${_escape(key)}</span><a ${attrs}>${_escape(disabled ? safeUrl + ' \u26a0 unreachable' : safeUrl)}</a></div>`;
}

function _groupHtml(groupName, rowsHtml, count) {
  return `
    <section class="endpointsGroup">
      <div class="endpointsGroupHead">
        <span>${_escape(groupName)}</span>
        <span class="count">${count} link${count === 1 ? '' : 's'}</span>
      </div>
      <div class="endpointsGroupBody">${rowsHtml}</div>
    </section>`;
}

function _escape(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _lookup(h, path) {
  let v = h;
  for (const k of path) {
    if (v && typeof v === 'object') v = v[k];
    else return undefined;
  }
  return v;
}

function _loadState(h, grp) {
  if (!grp.loadKey) return { disabled: false };
  const block = h && h[grp.loadKey];
  if (!block || typeof block !== 'object') return { disabled: false };
  const hasError = !!block.error;
  const ok = block.ok !== false;
  if (hasError && !ok) return { disabled: true, title: block.error };
  return { disabled: false };
}

export async function populateEndpoints() {
  const list = $('endpointsList');
  if (!list) return;
  list.textContent = 'Loading…';

  let h = {};
  try {
    const r = await fetch('/api/health');
    if (r.ok) h = await r.json();
  } catch (e) {
    list.innerHTML = `<p class="small" style="color:var(--red)">Failed to fetch /api/health: ${_escape(e.message)}</p>`;
    return;
  }

  try {
    const origin = (typeof location !== 'undefined' && location.origin) || '';
    const groups = [];

    const pageRows = PAGE_SERVER_ENDPOINTS.map(ep => _rowHtml(ep.key, _externalize(origin + ep.path), false)).join('');
    groups.push(_groupHtml('Page server', pageRows, PAGE_SERVER_ENDPOINTS.length));

    for (const grp of EXTERNAL_GROUPS) {
      const { disabled: groupDisabled, title: groupTitle } = _loadState(h, grp);
      const rows = [];
      let count = 0;
      for (const item of grp.keys) {
        const base = _lookup(h, item.path);
        if (!base) continue;
        const url = item.append ? String(base).replace(/\/+$/, '') + item.append : String(base);
        rows.push(_rowHtml(item.key, _externalize(url), groupDisabled, groupTitle));
        count++;
      }
      if (rows.length) groups.push(_groupHtml(grp.group, rows.join(''), count));
    }

    list.innerHTML = groups.join('');
  } catch (e) {
    list.innerHTML = `<p class="small" style="color:var(--red)">Failed to render endpoints: ${_escape(e && e.message || String(e))}</p>`;
    console.warn('populateEndpoints render failed:', e);
  }
}

export function openEndpoints() {
  const modal = $('endpointsModal');
  if (!modal) return;
  modal.classList.add('open');
  populateEndpoints().catch(e => console.warn('populateEndpoints failed:', e));
}

export function closeEndpoints() {
  const modal = $('endpointsModal');
  modal?.classList.remove('open');
}
