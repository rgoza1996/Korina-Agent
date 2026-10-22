// Acknowledgement catalog loading, bounded preloading, and idle playback.

import { state } from './state.js';
import { $, status } from './dom.js';
import { apiErrorMessage } from './api-errors.js';
import { addTranscriptEntry } from './history.js';
import { markActivity, recordIdleAck } from './settings-ui.js';
import { ensureAudio } from './speech.js';
import { MAX_ACK_AUDIO_BYTES, MAX_AUDIO_SECONDS, readResponseBlob, responseError } from './response-body.js';

let generation = 0;
let cachedVoice = null;
let pendingPlayback = null;
const preloads = new Map();
const retryAfter = new Map();
const EMPTY_RETRY_MS = 2000;
let catalogTimer = null;
let nextRebuild = null;
let rebuildWorker = null;

function ackStatus(message, level = 'warn') {
  if ($('ackStatus')) {
    state.ackStatusRevision++;
    status($('ackStatus'), message, level);
  }
}

async function ackJson(response, options = {}) {
  if (!response.ok) throw await responseError(response, options);
  const blob = await readResponseBlob(response, { ...options, maxBytes: 256 * 1024 });
  const payload = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer()));
  if (options.isCurrent && !options.isCurrent()) throw new DOMException('ACK request superseded', 'AbortError');
  return payload;
}

// Voice saves precede destructive rebuilds. One worker coalesces newer voice
// selections. The server serializes cache mutation after a client timeout and
// rejects an old voice change against the latest saved voice.
export function changeAckVoice(saveSettings) {
  cancelAckPlayback();
  const context = snapshot();
  ackStatus(`Saving voice ${context.voice}…`);
  let saved;
  try { saved = Promise.resolve(saveSettings()); }
  catch (error) { saved = Promise.reject(error); }
  const job = { context, saved: saved.then(() => true, error => {
    if (current(context)) ackStatus(`Voice settings save failed: ${apiErrorMessage(error)}. Retry the voice selection.`, 'bad');
    return false;
  }) };
  nextRebuild = job;
  return startRebuilds();
}

function startRebuilds() {
  if (!rebuildWorker) rebuildWorker = runRebuilds().finally(() => {
    rebuildWorker = null;
    if (nextRebuild) return startRebuilds();
  });
  return rebuildWorker;
}

async function runRebuilds() {
  while (nextRebuild) {
    const job = nextRebuild;
    nextRebuild = null;
    const { context } = job;
    if (!await job.saved || !current(context)) continue;
    state.ackFiles = []; state.ackBuffers = []; state.ackBuffersByTag = {};
    if ($('ackEnabled').value !== 'on') {
      ackStatus(`Voice ${context.voice} saved. Acknowledgements are disabled.`);
      continue;
    }
    ackStatus(`Preparing acknowledgements for ${context.voice}…`);
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/acks/rebuild', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: context.voice, clear: true, require_saved_voice: true }), signal: controller.signal });
      const payload = await ackJson(response, { signal: controller.signal, isCurrent: () => current(context) });
      if (!current(context)) continue;
      if (!payload || payload.ok !== true || payload.voice !== context.voice
        || !Number.isSafeInteger(payload.removed) || payload.removed < 0
        || !Number.isSafeInteger(payload.queued_or_missing) || payload.queued_or_missing < 0) throw new Error('Invalid acknowledgement rebuild response');
      ackStatus(`Acknowledgement rebuild queued for ${context.voice}: ${payload.queued_or_missing} missing clips.`);
      catalogTimer = setTimeout(() => {
        catalogTimer = null;
        if (current(context)) void loadAcks('global', context);
      }, 1500);
    } catch (error) {
      if (current(context)) ackStatus(`Acknowledgement rebuild failed: ${controller.signal.aborted ? 'request timed out' : apiErrorMessage(error)}. Retry the voice selection.`, 'bad');
    } finally { clearTimeout(deadline); }
  }
}

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
  if (nextRebuild || rebuildWorker || catalogTimer) ackStatus('Acknowledgement playback stopped.');
  generation++;
  clearTimeout(catalogTimer);
  catalogTimer = null;
  nextRebuild = null;
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
    const payload = await ackJson(response, { signal, isCurrent: () => current(context) });
    if (!current(context)) return [];
    if (!payload || !Array.isArray(payload.acks) || payload.acks.length > 256
      || payload.acks.some(item => !item || typeof item.url !== 'string' || !item.url || item.url.length > 2048
        || item.text !== undefined && typeof item.text !== 'string')) throw new Error('Invalid acknowledgement catalog response');
    const urls = payload.acks.map(item => ({ url: item.url,
      text: item.text || item.id || item.name || item.url, id: item.id,
      name: item.name, tags: item.tags || [] }));
    if (tag === 'global') state.ackFiles = urls;
    if (urls.length) retryAfter.delete(tag);
    if (tag === 'global') ackStatus(urls.length ? `${urls.length} cached acknowledgement clips available.`
      : payload.status?.missing_count > 0 ? 'Acknowledgement audio is being generated; no cached clips yet.' : 'No cached acknowledgement clips available.', urls.length ? 'good' : 'warn');
    return urls;
  } catch (error) {
    if (current(context) && error.name !== 'AbortError') ackStatus(`ACK catalog unavailable: ${apiErrorMessage(error)}. Repair the catalog or retry.`, 'bad');
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
