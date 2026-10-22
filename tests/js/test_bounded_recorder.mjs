import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { state } from '../../Korina/js/state.js';
import { captureLimits, createBoundedRecorder, MAX_BROWSER_CAPTURE_BYTES } from '../../Korina/js/bounded-recorder.js';

class Clock {
  time = 0;
  timers = new Map();
  serial = 0;
  now = () => this.time;
  setTimeout = (callback, delay) => { const id = ++this.serial; this.timers.set(id, { callback, at: this.time + delay }); return id; };
  clearTimeout = id => this.timers.delete(id);
  tick(ms) {
    const end = this.time + ms;
    while (true) {
      const next = [...this.timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      this.time = next[1].at;
      this.timers.delete(next[0]);
      next[1].callback();
    }
    this.time = end;
  }
}
class Recorder {
  static created = 0;
  constructor() { Recorder.created++; this.state = 'inactive'; this.stops = 0; }
  start(timeslice) { this.state = 'recording'; this.timeslice = timeslice; }
  stop() { this.state = 'inactive'; this.stops++; }
  emit(blob) { this.ondataavailable({ data: blob }); }
  finish() { this.onstop(); }
}

beforeEach(() => {
  globalThis.MediaRecorder = Recorder;
  Recorder.created = 0;
  state.audioLimits = null;
  state.audioLimitsError = '';
});

test('missing health uses conservative budgets and a valid server override remains effective', () => {
  assert.equal(captureLimits().maxSeconds, 300);
  assert.equal(captureLimits().maxBytes, MAX_BROWSER_CAPTURE_BYTES);
  state.audioLimits = { max_duration_seconds: 600, max_upload_bytes: 50 * 1024 * 1024 };
  assert.equal(captureLimits().maxSeconds, 600);
  assert.equal(captureLimits().deadlineMs, 599000);
});

test('the effective duration ends capture early and keeps all final native data', async () => {
  state.audioLimits = { max_duration_seconds: 2, max_upload_bytes: 500000 };
  const clock = new Clock();
  let info;
  const capture = createBoundedRecorder({}, { clock, onstop: result => { info = result; } });
  capture.start();
  capture.recorder.emit(new Blob(['header and first data']));
  clock.tick(1799);
  assert.equal(capture.recorder.state, 'recording');
  clock.tick(1);
  assert.equal(capture.recorder.state, 'inactive');
  assert.equal(clock.timers.size, 0);
  capture.recorder.emit(new Blob([' and final native data']));
  capture.recorder.finish();
  assert.equal(await new Blob(capture.chunks).text(), 'header and first data and final native data');
  assert.equal(info.limitReached.reason, 'duration');
  assert.equal(info.discarded, false);
  assert.equal(info.durationSeconds, 1.8);
});

test('byte segmentation reserves room for the final chunk without slicing the file', async () => {
  state.audioLimits = { max_duration_seconds: 300, max_upload_bytes: 16000 };
  const clock = new Clock();
  let info;
  const capture = createBoundedRecorder({}, { clock, onstop: result => { info = result; } });
  capture.start(1000);
  assert.equal(capture.recorder.timeslice, 500);
  capture.recorder.emit(new Blob(['h'.repeat(6000)]));
  capture.recorder.emit(new Blob(['m'.repeat(5000)]));
  assert.equal(capture.recorder.state, 'inactive');
  capture.recorder.emit(new Blob(['t'.repeat(800)]));
  capture.recorder.finish();
  assert.equal(await new Blob(capture.chunks).text(), 'h'.repeat(6000) + 'm'.repeat(5000) + 't'.repeat(800));
  assert.equal(info.bytes, 11800);
  assert.equal(info.limitReached.reason, 'bytes');
  assert.equal(info.discarded, false);
});

test('one oversized delayed native event discards the whole recording with an actionable error', () => {
  state.audioLimits = { max_duration_seconds: 300, max_upload_bytes: 16000 };
  let info;
  const capture = createBoundedRecorder({}, { clock: new Clock(), onstop: result => { info = result; } });
  capture.start();
  capture.recorder.emit(new Blob(['header']));
  capture.recorder.emit(new Blob([new Uint8Array(13000)]));
  capture.recorder.finish();
  assert.equal(capture.chunks.length, 0);
  assert.equal(info.discarded, true);
  assert.match(info.error, /safe upload size.*shorter turn/);
});

test('Stop clears the deadline immediately and completion latency is not mistaken for audio duration', () => {
  state.audioLimits = { max_duration_seconds: 2, max_upload_bytes: 500000 };
  const clock = new Clock();
  let info;
  const capture = createBoundedRecorder({}, { clock, onstop: result => { info = result; } });
  capture.start();
  clock.tick(500);
  capture.recorder.stop();
  assert.equal(clock.timers.size, 0);
  clock.tick(10000);
  capture.recorder.finish();
  assert.equal(info.durationSeconds, .5);
  assert.equal(info.discarded, false);
  assert.equal(info.limitReached, null);
});

test('a throttled deadline that already exceeded the server duration never uploads a truncated prefix', () => {
  state.audioLimits = { max_duration_seconds: 2, max_upload_bytes: 500000 };
  const clock = new Clock();
  let info;
  const capture = createBoundedRecorder({}, { clock, onstop: result => { info = result; } });
  capture.start();
  capture.recorder.emit(new Blob(['old prefix']));
  clock.time = 3000; // Simulate a suspended/backgrounded browser delaying timers.
  capture.recorder.emit(new Blob(['late tail']));
  capture.recorder.finish();
  assert.equal(capture.chunks.length, 0);
  assert.equal(info.discarded, true);
  assert.match(info.error, /beyond the server audio limit/);
});

test('cancellation and stale ownership release timers and data without delivering old completion', () => {
  const clock = new Clock();
  let current = true;
  let completions = 0;
  const capture = createBoundedRecorder({}, { clock, isCurrent: () => current, onstop: () => completions++ });
  capture.start();
  capture.recorder.emit(new Blob(['old data']));
  current = false;
  capture.recorder.emit(new Blob(['late data']));
  capture.recorder.finish();
  assert.equal(clock.timers.size, 0);
  assert.equal(capture.chunks.length, 0);
  assert.equal(completions, 0);
});

test('an explicit server limits error prevents recording instead of applying fallback limits', () => {
  state.audioLimitsError = 'KORINA_MAX_AUDIO_SECONDS must be between 1 and 1800 seconds';
  assert.throws(() => createBoundedRecorder({}), /Recording unavailable.*KORINA_MAX_AUDIO_SECONDS/);
  assert.equal(Recorder.created, 0);
});

test('a tighter server upload budget invalidates previously retained data on completion', () => {
  state.audioLimits = { max_duration_seconds: 300, max_upload_bytes: 20000 };
  let info;
  const capture = createBoundedRecorder({}, { clock: new Clock(), onstop: result => { info = result; } });
  capture.start();
  capture.recorder.emit(new Blob([new Uint8Array(10000)]));
  state.audioLimits.max_upload_bytes = 8000;
  capture.recorder.stop();
  capture.recorder.finish();
  assert.equal(info.discarded, true);
  assert.equal(info.bytes, 0);
  assert.equal(capture.chunks.length, 0);
  assert.match(info.error, /no longer fits the server upload limit/);
});
