// Audio-capability probe cache requests and shared cache-key normalization.

import { readResponseBlob, responseError } from './response-body.js';

export async function listAudioProbes() {
  const r = await fetch('/api/audio-probe');
  if (!r.ok) return { entries: {} };
  return r.json();
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
