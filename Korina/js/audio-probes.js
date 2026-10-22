// Audio-capability probe cache requests and shared cache-key normalization.

import { readResponseBlob, responseError } from './response-body.js';

// A valid 512-entry cache can approach 11 MiB with maximum-length Unicode
// endpoint/model keys and 500-character diagnostics. Bound the whole download
// before parsing without rejecting that server-supported cache.
const MAX_PROBE_BYTES = 16 * 1024 * 1024;
const MAX_PROBE_KEY_CHARS = 64 + 4096 + 512 + 4;
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const boundedText = (value, max) => typeof value === 'string' && value.length <= max * 2
  && !/[\uD800-\uDFFF]/u.test(value) && [...value].length <= max;

function probeEntries(payload) {
  if (!record(payload) || !record(payload.entries)) throw new Error('Invalid audio probe cache response.');
  const entries = Object.entries(payload.entries);
  if (entries.length > 512) throw new Error('The audio probe cache exceeds its entry limit.');
  return Object.fromEntries(entries.map(([key, value]) => {
    if (!key || !boundedText(key, MAX_PROBE_KEY_CHARS) || !record(value)) throw new Error('Invalid audio probe cache entry.');
    const entry = {};
    for (const [field, max] of [['reason', 256], ['since', 64], ['last_error', 500]]) {
      if (field in value && !boundedText(value[field], max)) throw new Error('Invalid audio probe cache entry.');
      if (field in value) entry[field] = value[field];
    }
    return [key, entry];
  }));
}

export async function listAudioProbes({ signal, isCurrent = () => true } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const deadline = setTimeout(() => controller.abort(new DOMException('Audio probe cache request timed out.', 'TimeoutError')), 5000);
  try {
    controller.signal.throwIfAborted();
    if (!isCurrent()) throw new DOMException('Probe cache request superseded', 'AbortError');
    const options = { signal: controller.signal, isCurrent };
    const r = await fetch('/api/audio-probe', { signal: controller.signal });
    if (!r.ok) throw await responseError(r, options);
    const blob = await readResponseBlob(r, { ...options, maxBytes: MAX_PROBE_BYTES });
    const bytes = await blob.arrayBuffer();
    controller.signal.throwIfAborted();
    if (!isCurrent()) throw new DOMException('Probe cache request superseded', 'AbortError');
    let payload;
    try { payload = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { throw new Error('The server returned an invalid audio probe cache response.'); }
    return { entries: probeEntries(payload) };
  } catch (error) {
    controller.signal.throwIfAborted();
    throw error;
  } finally {
    clearTimeout(deadline);
    signal?.removeEventListener('abort', abort);
  }
}

export async function clearAudioProbe(provider, baseUrl, model, options = {}) {
  // Query params: see `routes/providers.py` Task 4.5.5 — path-param form
  // with two `:path` converters routes incorrectly for HF-style model ids.
  const qs = new URLSearchParams({
    provider: String(provider || ''),
    base_url: String(baseUrl || ''),
    model: String(model || ''),
  });
  const r = await fetch(`/api/audio-probe?${qs.toString()}`, { method: 'DELETE', signal: options.signal });
  if (!r.ok) throw await responseError(r, options);
  const blob = await readResponseBlob(r, { ...options, maxBytes: 64 * 1024 });
  const bytes = await blob.arrayBuffer();
  if (options.signal?.aborted || options.isCurrent && !options.isCurrent()) {
    throw new DOMException('Probe reset superseded', 'AbortError');
  }
  let result;
  try { result = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new Error('The server returned an invalid audio probe reset response.'); }
  const triple = `${String(provider || '').trim()}::${normalizeTripleBaseUrl(baseUrl)}::${String(model || '').trim()}`;
  if (!result || result.triple !== triple || typeof result.removed !== 'boolean') {
    throw new Error('The server did not confirm the audio probe reset.');
  }
  return result;
}

// Cache key normalizer. MUST match the backend's `triple_key` in
// `korina/services/audio_probe.py`: trim, strip trailing slashes, and
// collapse `/chat/completions` to the API base URL. If we don't apply
// the same normalization here, badge lookup silently misses.
export function normalizeTripleBaseUrl(s) {
  const base = String(s || '').trim().replace(/\/+$/, '');
  return base.endsWith('/chat/completions')
    ? base.slice(0, -'/chat/completions'.length)
    : base;
}
