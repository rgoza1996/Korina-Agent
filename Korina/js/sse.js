// Read bounded JSON SSE frames across native network and UTF-8 boundaries.

import { cancelReader } from './response-body.js';

const DEFAULT_EVENT_BYTES = 2 * 1024 * 1024;
const DEFAULT_TOTAL_BYTES = 8 * 1024 * 1024;

export async function* readJsonEvents(body, {
  isCurrent = () => true,
  maxEventBytes = DEFAULT_EVENT_BYTES,
  maxTotalBytes = DEFAULT_TOTAL_BYTES,
} = {}) {
  if (!body || typeof body.getReader !== 'function') throw new Error('The streaming response has no body.');
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const encoder = new TextEncoder();
  let totalBytes = 0, eventBytes = 0, lineParts = [], data = [], event = 'message';
  let skipLf = false, completed = false;

  function assertCurrent() {
    if (!isCurrent()) throw new DOMException('The stream is no longer current.', 'AbortError');
  }

  function append(part) {
    eventBytes += encoder.encode(part).byteLength;
    if (eventBytes > maxEventBytes) throw new Error('A streaming event exceeded its size limit.');
    if (part) lineParts.push(part);
  }

  function* consume(text) {
    if (!text) return;
    let offset = skipLf && text[0] === '\n' ? 1 : 0;
    skipLf = false;
    const breaks = /\r\n|\r|\n/g;
    breaks.lastIndex = offset;
    for (let match; (match = breaks.exec(text));) {
      append(text.slice(offset, match.index));
      // Count one canonical newline regardless of CRLF splitting across reads.
      append('\n');
      const line = lineParts.join('').slice(0, -1);
      lineParts = [];
      offset = match.index + match[0].length;
      skipLf = match[0] === '\r' && offset === text.length;
      if (!line) {
        if (data.length) {
          let payload;
          try { payload = JSON.parse(data.join('\n')); }
          catch { throw new Error('The stream returned invalid JSON.'); }
          if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
            throw new Error('The stream returned an invalid JSON event.');
          }
          assertCurrent();
          yield { event, data: payload };
        }
        data = []; event = 'message'; eventBytes = 0;
      } else if (!line.startsWith(':')) {
        const separator = line.indexOf(':');
        const field = separator < 0 ? line : line.slice(0, separator);
        const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '');
        if (field === 'event') event = value || 'message';
        else if (field === 'data') data.push(value);
      }
    }
    append(text.slice(offset));
  }

  try {
    while (true) {
      assertCurrent();
      const next = await reader.read();
      assertCurrent();
      if (next.done) break;
      totalBytes += next.value.byteLength;
      if (totalBytes > maxTotalBytes) throw new Error('The streaming response exceeded its size limit.');
      let text;
      try { text = decoder.decode(next.value, { stream: true }); }
      catch { throw new Error('The stream returned invalid UTF-8.'); }
      yield* consume(text);
    }
    let tail;
    try { tail = decoder.decode(); }
    catch { throw new Error('The stream ended with incomplete UTF-8.'); }
    yield* consume(tail);
    const trailingLine = lineParts.join('');
    if (data.length || event !== 'message' || (trailingLine.trim() && !trailingLine.startsWith(':'))) {
      throw new Error('The stream ended with an incomplete event.');
    }
    completed = true;
  } finally {
    if (!completed) cancelReader(reader);
    try { reader.releaseLock?.(); } catch {}
  }
}
