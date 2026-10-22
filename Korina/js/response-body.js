// Bound downloaded audio and diagnostics before retaining or decoding them.

import { apiErrorMessage } from './api-errors.js';

export const MAX_AUDIO_SECONDS = 600;
export const MAX_BUFFERED_AUDIO_BYTES = 32 * 1024 * 1024;
export const MAX_ACK_AUDIO_BYTES = 2 * 1024 * 1024;
const MAX_ERROR_BYTES = 4096;

export function cancelReader(reader) {
  try { Promise.resolve(reader?.cancel()).catch(() => {}); } catch {}
}

function assertCurrent(signal, isCurrent) {
  signal?.throwIfAborted();
  if (!isCurrent()) throw new DOMException('The response is no longer current.', 'AbortError');
}

export async function readResponseBlob(response, {
  maxBytes = MAX_BUFFERED_AUDIO_BYTES, signal, isCurrent = () => true,
} = {}) {
  try { assertCurrent(signal, isCurrent); }
  catch (error) { cancelReader(response.body); throw error; }
  const length = Number(response.headers?.get?.('Content-Length'));
  if (Number.isFinite(length) && length > maxBytes) {
    cancelReader(response.body);
    throw new Error('The response exceeded its download size limit.');
  }
  if (!response.body || typeof response.body.getReader !== 'function') throw new Error('The response has no readable body.');
  const reader = response.body.getReader();
  const abort = () => cancelReader(reader);
  signal?.addEventListener('abort', abort, { once: true });
  let bytes = 0, completed = false;
  const chunks = [];
  try {
    while (true) {
      assertCurrent(signal, isCurrent);
      const next = await reader.read();
      assertCurrent(signal, isCurrent);
      if (next.done) break;
      bytes += next.value.byteLength;
      if (bytes > maxBytes) throw new Error('The response exceeded its download size limit.');
      chunks.push(next.value);
    }
    completed = true;
    return new Blob(chunks, { type: response.headers?.get?.('Content-Type') || 'application/octet-stream' });
  } finally {
    signal?.removeEventListener('abort', abort);
    if (!completed) cancelReader(reader);
    try { reader.releaseLock?.(); } catch {}
  }
}

export async function responseError(response, options = {}) {
  const fallback = `HTTP ${response.status}`;
  try {
    const blob = await readResponseBlob(response, { ...options, maxBytes: MAX_ERROR_BYTES });
    const text = await blob.text();
    assertCurrent(options.signal, options.isCurrent || (() => true));
    let detail = apiErrorMessage(text, '');
    try {
      detail = apiErrorMessage(JSON.parse(text), '');
    } catch {}
    return new Error(detail ? `${fallback}: ${detail}` : fallback);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    return new Error(`${fallback}: ${error.message}`);
  }
}
