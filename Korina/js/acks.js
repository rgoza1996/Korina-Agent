// Acknowledgement catalog loading, bounded preloading, and idle playback.

import { state } from './state.js';
import { $ } from './dom.js';
import { addTranscriptEntry } from './history.js';
import { markActivity, recordIdleAck } from './settings-ui.js';
import { ensureAudio } from './speech.js';
import { MAX_ACK_AUDIO_BYTES, MAX_AUDIO_SECONDS, readResponseBlob } from './response-body.js';

let generation = 0;
let cachedVoice = null;
let pendingPlayback = null;
const preloads = new Map();
const retryAfter = new Map();
const EMPTY_RETRY_MS = 2000;

function snapshot() {
  return { generation, liveSession: state.liveSession, agentSession: state.agentSession,
    voice: $('voice').value, audio: state.audioCtx };
}

function current(context, audio = false) {
  return context.generation === generation && context.liveSession === state.liveSession &&
    context.agentSession === state.agentSession && context.voice === $('voice').value &&
    (!audio || context.audio === state.audioCtx && context.audio?.state !== 'closed');
}

function selectVoice(voice) {
  if (cachedVoice === voice) return;
  cachedVoice = voice;
  state.ackFiles = [];
  state.ackBuffers = [];
  state.ackBuffersByTag = {};
}

export function cancelAckPlayback() {
  generation++;
  pendingPlayback = null;
  for (const job of preloads.values()) job.controller.abort();
  preloads.clear();
  retryAfter.clear();
  if (state.ackSource) { try { state.ackSource.stop(); } catch {} }
  state.ackSource = null;
  state.ackPlayingUntil = 0;
}

export async function loadAcks(tag = 'global', context = snapshot(), { signal } = {}) {
  selectVoice(context.voice);
  try {
    const response = await fetch(`/api/acks?voice=${encodeURIComponent(context.voice)}&tag=${encodeURIComponent(tag)}`, { signal });
    if (!response.ok) throw new Error(`Acknowledgement catalog HTTP ${response.status}`);
    const payload = await response.json();
    if (!current(context)) return [];
    const urls = (payload.acks || []).map(item => ({ url: item.url,
      text: item.text || item.id || item.name || item.url, id: item.id,
      name: item.name, tags: item.tags || [] }));
    if (tag === 'global') state.ackFiles = urls;
    if (urls.length) retryAfter.delete(tag);
    return urls;
  } catch (error) {
    if (current(context) && error.name !== 'AbortError') console.warn('ack load failed', error);
    return [];
  }
}

export async function preloadAckBuffers(tag = 'global') {
  // Arm playback while starting Live even when acknowledgements are disabled.
  ensureAudio();
  if ($('ackEnabled').value !== 'on') return [];
  const context = snapshot();
  selectVoice(context.voice);
  if (state.ackBuffersByTag[tag]?.length) return state.ackBuffersByTag[tag];
  const retry = retryAfter.get(tag);
  if (retry && current(retry.context, true) && performance.now() < retry.at) return [];
  const existing = preloads.get(tag);
  if (existing && current(existing.context, true)) return existing.promise;
  existing?.controller.abort();
  const job = { context, controller: new AbortController(), promise: null };
  job.promise = (async () => {
    const urls = tag === 'global' && state.ackFiles.length ? state.ackFiles : await loadAcks(tag, context, { signal: job.controller.signal });
    const decoded = [];
    for (const item of urls) {
      if (!current(context, true)) return [];
      const url = typeof item === 'string' ? item : item.url;
      try {
        const response = await fetch(url, { signal: job.controller.signal });
        if (!response.ok) throw new Error(`Acknowledgement audio HTTP ${response.status}`);
        const blob = await readResponseBlob(response, {
          maxBytes: MAX_ACK_AUDIO_BYTES, signal: job.controller.signal,
          isCurrent: () => current(context, true),
        });
        const bytes = await blob.arrayBuffer();
        if (!current(context, true)) return [];
        const buffer = await context.audio.decodeAudioData(bytes);
        if (!current(context, true)) return [];
        if (!Number.isFinite(buffer.duration) || buffer.duration <= 0 || buffer.duration > MAX_AUDIO_SECONDS) {
          throw new Error('Acknowledgement audio exceeds the ten minute audio limit.');
        }
        decoded.push({ url, text: typeof item === 'string' ? url : item.text || item.id || item.name || url,
          buffer, tag });
      } catch (error) {
        if (current(context, true) && error.name !== 'AbortError') console.warn('ack decode failed', url, error);
      }
    }
    if (!current(context, true)) return [];
    state.ackBuffersByTag[tag] = decoded;
    if (tag === 'global') state.ackBuffers = decoded;
    // Missing/generated-later audio must not turn animation-frame idle checks
    // into a catalog or WAV request on every frame.
    if (!decoded.length) retryAfter.set(tag, { context, at: performance.now() + EMPTY_RETRY_MS });
    return decoded;
  })().finally(() => { if (preloads.get(tag) === job) preloads.delete(tag); });
  preloads.set(tag, job);
  return job.promise;
}

function canPlay() {
  return $('ackEnabled').value === 'on' && performance.now() >= state.suppressAckUntil &&
    !state.pendingImportantAgentMessage && !state.awaitingAgentPermission;
}

export async function playRandomAck(tag = 'global') {
  if (!canPlay()) return;
  if (pendingPlayback && current(pendingPlayback.context, true)) return pendingPlayback.promise;
  try { ensureAudio(); } catch (error) { console.warn('ack playback failed', error); return; }
  const context = snapshot();
  const playback = { context, promise: null };
  playback.promise = (async () => {
    try {
      const pool = await preloadAckBuffers(tag);
      if (!current(context, true) || !canPlay() || !pool.length) return;
      if (state.ackSource) { try { state.ackSource.stop(); } catch {} }
      const pick = pool[Math.floor(Math.random() * pool.length)];
      const source = context.audio.createBufferSource();
      source.buffer = pick.buffer;
      source.connect(state.gain);
      const when = Math.max(context.audio.currentTime + 0.005, state.ackPlayingUntil);
      source.start(when);
      state.ackSource = source;
      source.onended = () => { if (state.ackSource === source) state.ackSource = null; };
      state.ackPlayingUntil = when + pick.buffer.duration + 0.05;
      const text = pick.text || pick.url.split('/').pop();
      addTranscriptEntry('Ack Phrase', text, { includeInHistory: false });
      if (tag === 'idle') recordIdleAck(); else markActivity('ack');
      $('chunkInfo').textContent = `Ack ${tag}: ${text} · ${pick.buffer.duration.toFixed(2)}s`;
    } catch (error) { if (current(context, true)) console.warn('ack playback failed', error); }
  })().finally(() => { if (pendingPlayback === playback) pendingPlayback = null; });
  pendingPlayback = playback;
  return playback.promise;
}
