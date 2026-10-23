// Retain one complete MediaRecorder file within the server and browser budgets.

import { state } from './state.js';

export const MAX_BROWSER_CAPTURE_BYTES = 24 * 1024 * 1024;
const DEFAULT_MAX_SECONDS = 300;
const DEFAULT_UPLOAD_BYTES = 50 * 1024 * 1024;
const nativeClock = {
  now: () => performance.now(),
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: timer => clearTimeout(timer),
};

export function captureLimits() {
  if (state.audioLimitsError) throw new Error(`Recording unavailable: ${state.audioLimitsError}`);
  const limits = state.audioLimits || {};
  const seconds = Number(limits.max_duration_seconds);
  const upload = Number(limits.max_upload_bytes);
  const maxSeconds = Number.isFinite(seconds) && seconds >= 1 && seconds <= 1800 ? seconds : DEFAULT_MAX_SECONDS;
  const maxUploadBytes = Number.isSafeInteger(upload) && upload > 0 ? upload : DEFAULT_UPLOAD_BYTES;
  const multipartReserve = Math.min(65536, Math.floor(maxUploadBytes / 4));
  const maxBytes = Math.min(MAX_BROWSER_CAPTURE_BYTES, maxUploadBytes - multipartReserve);
  const tailReserve = Math.min(2 * 1024 * 1024, Math.ceil(maxBytes / 10));
  return {
    maxSeconds,
    // Leave time for encoder padding and space for the final native data event.
    deadlineMs: (maxSeconds - Math.min(1, maxSeconds / 10)) * 1000,
    maxBytes,
    stopBytes: Math.max(1, maxBytes - tailReserve),
  };
}

export function createBoundedRecorder(stream, {
  mimeType, onstop = () => {}, isCurrent = () => true, clock = nativeClock,
} = {}) {
  let policy = captureLimits();
  const recorder = new MediaRecorder(stream, { mimeType });
  const chunks = [];
  const nativeStop = recorder.stop.bind(recorder);
  let timer = null;
  let startedAt = null;
  let stoppedAt = null;
  let completed = false;
  let cancelled = false;
  let bytes = 0;
  let limitReached = null;
  let error = '';

  function clearTimer() {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
  }

  function stop() {
    clearTimer();
    if (stoppedAt === null) stoppedAt = clock.now();
    if (recorder.state !== 'inactive') return nativeStop();
  }
  recorder.stop = stop;

  function discard(message) {
    error = message;
    chunks.length = 0;
    bytes = 0;
    stop();
  }

  function reachLimit(reason) {
    limitReached ||= {
      reason,
      message: 'Recording limit reached; processing this segment. Pause while Korina replies, then continue.',
    };
    stop();
  }

  function refreshPolicy() {
    try { policy = captureLimits(); }
    catch (failure) { discard(failure.message); return false; }
    if (bytes > policy.maxBytes) {
      discard('Recording no longer fits the server upload limit. Please try again with a shorter turn.');
      return false;
    }
    const elapsed = (stoppedAt ?? clock.now()) - startedAt;
    if (elapsed > policy.maxSeconds * 1000) {
      discard('Recording ran beyond the server audio limit. Please try again with a shorter turn.');
      return false;
    }
    return true;
  }

  function scheduleDeadline() {
    clearTimer();
    if (stoppedAt !== null || cancelled || !refreshPolicy()) return;
    const remaining = policy.deadlineMs - (clock.now() - startedAt);
    if (remaining <= 0) reachLimit('duration');
    else timer = clock.setTimeout(scheduleDeadline, remaining);
  }

  function cancel() {
    cancelled = true;
    chunks.length = 0;
    bytes = 0;
    stop();
  }

  recorder.ondataavailable = event => {
    if (completed || cancelled || error) return;
    if (!isCurrent()) { cancel(); return; }
    if (!refreshPolicy() || !event.data.size) return;
    if (bytes + event.data.size > policy.maxBytes) {
      // A delayed oversized native event cannot be safely cropped as WebM.
      discard('Recording exceeded the safe upload size. Please try again with a shorter turn.');
      return;
    }
    chunks.push(event.data);
    bytes += event.data.size;
    if (stoppedAt === null && bytes >= policy.stopBytes) reachLimit('bytes');
    else scheduleDeadline();
  };

  function complete() {
    if (completed) return;
    completed = true;
    clearTimer();
    if (cancelled || !isCurrent()) { chunks.length = 0; return; }
    refreshPolicy();
    return onstop({
      limitReached, discarded: !!error, error,
      durationSeconds: Math.max(0, ((stoppedAt ?? clock.now()) - startedAt) / 1000),
      bytes,
    });
  }
  recorder.onstop = complete;
  recorder.onerror = event => {
    discard(event.error?.message || 'Microphone recording failed. Please try again.');
    queueMicrotask(complete);
  };

  return {
    recorder, chunks, cancel,
    start(timeslice = 500) {
      if (startedAt !== null) throw new Error('Start a new capture instead of restarting an old recording.');
      startedAt = clock.now();
      recorder.start(Math.min(500, Math.max(1, timeslice || 500)));
      scheduleDeadline();
    },
  };
}
