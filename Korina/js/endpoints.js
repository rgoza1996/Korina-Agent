// js/endpoints.js
//
// Endpoints modal — shows every URL Korina currently has wired, each as a
// clickable <a target="_blank"> link. Page-server endpoints (everything
// under /api/...) are local; LLM/STT/TTS/agent endpoints are read from
// /api/health so the list always reflects the live, server-resolved URLs.
//
// Two exports:
//   openEndpoints()       - show the modal, populate it from /api/health
//   closeEndpoints()      - hide the modal
//   populateEndpoints()   - re-fetch /api/health and rebuild the list
//                          (also exposed for the Refresh button)
//
// All links target="_blank" so they open in a new tab and never navigate
// the Korina page away from the running session. Disabled (struck-through)
// styling for endpoints the server reports as unreachable so the user can
// see at a glance which links will actually respond.

import { $ } from './dom.js';

// Static page-server endpoints (relative to the current origin). The
// modal resolves these to absolute URLs by prepending location.origin.
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

// External services read from /api/health. We pick a stable key per
// group and look up the URL via the loader below.
const EXTERNAL_GROUPS = [
  {
    group: 'Response LLM',
    keys: [
      { key: 'base_url', path: ['response_llm_base_url'] },
      { key: 'chat_url', path: ['response_llm_chat_url'] },
      { key: 'models',   path: ['response_llm_base_url'], append: '/models' },
    ],
  },
  {
    group: 'Multimodal STT',
    keys: [
      { key: 'base_url', path: ['multimodal_stt_base_url'] },
      { key: 'chat_url', path: ['multimodal_stt_chat_url'] },
      { key: 'models',   path: ['multimodal_stt_base_url'], append: '/models' },
    ],
  },
  {
    group: 'Agent',
    keys: [
      { key: 'base_url', path: ['agent_base_url'] },
      { key: 'chat_url', path: ['agent_chat_url'] },
      { key: 'models',   path: ['agent_base_url'], append: '/models' },
    ],
  },
  {
    group: 'TTS',
    keys: [
      { key: 'base_url', path: ['tts_base_url'] },
      { key: 'health',   path: ['tts', 'base_url'], append: '/health' },
    ],
  },
];

// Rewrite loopback hostnames to whatever host the user is currently
// browsing Korina from. Without this, links to e.g. llama.cpp at
// http://127.0.0.1:8080/v1 only work when Korina itself is on the same
// machine; browsing Korina over Tailscale would render an unreachable
// link. /api/health reports the loopback URL by design (the page server
// reads its own config), so we have to do the host swap client-side.
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

// Render one endpoint as <a target="_blank" href=URL>URL</a>.
// `disabled` strikes the link out (server reported unreachable / error).
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

// Recursively read /api/health and rebuild the endpoint list. Called on
// open and on Refresh.
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

  const origin = (typeof location !== 'undefined' && location.origin) || '';
  const groups = [];
  const usedKeys = new Set();

  // 1. Page server endpoints (always render; always local)
  const pageRows = PAGE_SERVER_ENDPOINTS.map(ep => _rowHtml(ep.key, _externalize(origin + ep.path), false)).join('');
  groups.push(_groupHtml('Page server', pageRows, PAGE_SERVER_ENDPOINTS.length));

  // 2. External services from /api/health
  for (const grp of EXTERNAL_GROUPS) {
    const rows = [];
    let count = 0;
    for (const item of grp.keys) {
      const base = _lookup(h, item.path);
      if (!base) continue;
      const url = item.append ? String(base).replace(/\/+$/, '') + item.append : String(base);
      // Mark disabled if any sibling block in the same group reports an error
      const loadBlock = _lookup(h, grp.keys[0] && h[`${grp.group.toLowerCase().replace(/ /g, '_')}_load`]);
      const groupLoadKey = grp.group === 'Response LLM' ? 'response_llm_load'
                         : grp.group === 'Multimodal STT' ? 'multimodal_stt_load'
                         : grp.group === 'Agent' ? 'agent_load'
                         : null;
      const loadBlockInfo = groupLoadKey ? h[groupLoadKey] : null;
      const disabled = loadBlockInfo && loadBlockInfo.error && !loadBlockInfo.ok;
      rows.push(_rowHtml(item.key, _externalize(url), !!disabled, loadBlockInfo && loadBlockInfo.error));
      count++;
    }
    if (rows.length) groups.push(_groupHtml(grp.group, rows.join(''), count));
  }

  list.innerHTML = groups.join('');
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