// js/acks.js
//
// Frontend ack-phrase helpers — loading the per-voice ack catalog,
// preloading decoded AudioBuffers for instant playback, waiting for
// scheduled audio to drain, and picking + playing a random ack —
// extracted verbatim from the original inline <script> in
// Korina/index.html (lines 697–706, 776–794, 795, 934–955 at commit
// bdda3cc).
//
// Bare top-level globals (ackFiles, ackBuffers, ackBuffersByTag,
// audioCtx, gain, ackPlayingUntil, ackSource, nextPlayTime,
// suppressAckUntil, pendingImportantAgentMessage,
// awaitingAgentPermission) have been migrated to the consolidated
// `state` object (see state.js). `status` and `$` are imported from
// dom.js. `addTranscriptEntry` is imported from history.js.
// `markActivity` and `recordIdleAck` are imported from
// settings-ui.js. `ensureAudio` is a self-call (speakText-style
// helper that lives in speech.js — bare reference here resolves at
// call time once the module graph is wired by the bootstrap in 3.2.15).
//
// Consumers import the named exports below.

import { state } from "./state.js";
import { $, status } from "./dom.js";
import { addTranscriptEntry } from "./history.js";
import { markActivity, recordIdleAck } from "./settings-ui.js";

// --- Ack catalog load (verbatim from index.html:697-706) ---
//
// Bare globals `ackFiles` -> `state.ackFiles`.
export async function loadAcks(tag = 'global') {
  try {
    const j = await (await fetch(`/api/acks?voice=${encodeURIComponent($('voice').value)}&tag=${encodeURIComponent(tag)}`)).json();
    const urls = (j.acks || []).map(a => ({ url: a.url, text: a.text || a.id || a.name || a.url, id: a.id, name: a.name, tags: a.tags || [] }));
    if (tag === 'global') state.ackFiles = urls;
    if (urls.length) $('sttStatus').textContent = `Ready. ${urls.length} ${tag} ack phrases for ${$('voice').value}. Queue ${j.status?.queue_depth || 0}; missing ${j.status?.missing_count || 0}.`;
    else $('sttStatus').textContent = `Ack phrases for ${$('voice').value}/${tag} are queued for generation. Queue ${j.status?.queue_depth || 0}; missing ${j.status?.missing_count || 0}.`;
    return urls;
  } catch (e) { console.warn('ack load failed', e); return []; }
}

// --- Ack buffer preload (verbatim from index.html:776-794) ---
//
// Bare globals `ackBuffersByTag`, `ackFiles`, `audioCtx`, `ackBuffers`
// -> `state.*`. `ensureAudio` is a self-call resolved at call time.
export async function preloadAckBuffers(tag = 'global') {
  ensureAudio();
  if (state.ackBuffersByTag[tag]?.length) return state.ackBuffersByTag[tag];
  const urls = tag === 'global' && state.ackFiles.length ? state.ackFiles : await loadAcks(tag);
  if (!urls.length) return [];
  const decoded = [];
  for (const item of urls) {
    const url = typeof item === 'string' ? item : item.url;
    try {
      const ab = await (await fetch(url)).arrayBuffer();
      const buf = await state.audioCtx.decodeAudioData(ab.slice(0));
      decoded.push({ url, text: (typeof item === 'string' ? url : (item.text || item.id || item.name || url)), buffer: buf, tag });
    } catch (e) { console.warn('ack decode failed', url, e); }
  }
  state.ackBuffersByTag[tag] = decoded;
  if (tag === 'global') state.ackBuffers = decoded;
  if (decoded.length) status($('sttStatus'), `Live mode ready. ${decoded.length} ${tag} ack phrases preloaded for ${$('voice').value}.`, 'good');
  return decoded;
}

// --- Wait for scheduled audio (verbatim from index.html:795) ---
//
// Bare globals `audioCtx`, `nextPlayTime` -> `state.*`.
export async function waitForScheduledAudio() {
  if (!state.audioCtx) return;
  const ms = Math.max(0, (state.nextPlayTime - state.audioCtx.currentTime) * 1000) + 150;
  if (ms > 0) await new Promise(r => setTimeout(r, ms));
}

// --- Play random ack (verbatim from index.html:934-955) ---
//
// Bare globals `suppressAckUntil`, `pendingImportantAgentMessage`,
// `awaitingAgentPermission`, `ackSource`, `audioCtx`, `gain`,
// `ackPlayingUntil` -> `state.*`. `ensureAudio` and
// `preloadAckBuffers` are self-calls (ensureAudio resolves at call
// time via the speech.js module's global side effect in the current
// bootstrap). `addTranscriptEntry` is imported from history.js.
// `recordIdleAck` and `markActivity` are imported from settings-ui.js.
export async function playRandomAck(tag = 'global') {
  if ($('ackEnabled').value !== 'on') return;
  if (performance.now() < state.suppressAckUntil || state.pendingImportantAgentMessage || state.awaitingAgentPermission) return;
  try {
    ensureAudio();
    const pool = await preloadAckBuffers(tag);
    if (!pool.length) return;
    if (state.ackSource) { try { state.ackSource.stop(); } catch {} }
    const pick = pool[Math.floor(Math.random() * pool.length)];
    const src = state.audioCtx.createBufferSource();
    src.buffer = pick.buffer;
    src.connect(state.gain);
    const when = Math.max(state.audioCtx.currentTime + 0.005, state.ackPlayingUntil);
    src.start(when);
    state.ackSource = src;
    state.ackPlayingUntil = when + pick.buffer.duration + 0.05;
    const ackText = pick.text || pick.url.split('/').pop();
    addTranscriptEntry('Ack Phrase', ackText, { includeInHistory: false });
    if (tag === 'idle') recordIdleAck(); else markActivity('ack');
    $('chunkInfo').textContent = `Ack ${tag}: ${ackText} · ${pick.buffer.duration.toFixed(2)}s`;
  } catch (e) { console.warn('ack playback failed', e); }
}
