import { test, expect } from '@playwright/test';
import { wavTone } from './fixtures/audio.js';

test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }, permissions: ['microphone'] });

async function interruptionPage(page, { holdFollowup = false } = {}) {
  const errors = [], chats = [], captures = [], speech = [];
  let release;
  const followup = new Promise(resolve => { release = resolve; });
  await page.addInitScript(() => {
    // Control endpointing only; capture, encoding, decoding, and playback stay native.
    window.__inputLevel = 128;
    AnalyserNode.prototype.getByteTimeDomainData = data => data.fill(window.__inputLevel);
  });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, mode: 'buf', ack_enabled: 'off', agent_enabled: 'off',
        final_stt_mode: 'full', silence_ms: 600, min_speech_ms: 150, partial_window_ms: 10000,
        tts_provider: 'kokoro', tts_base_url: 'http://speech.fixture' } });
    }
    if (path === '/api/health') return route.fulfill({ json: { ok: true, whisper_available: true,
      response_llm_load: { ok: true, loaded: true } } });
    if (path === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (path === '/api/transcribe/stream') {
      const body = request.postDataBuffer();
      const start = body.indexOf(Buffer.from('\r\n\r\n')) + 4;
      const end = body.lastIndexOf(Buffer.from('\r\n--'));
      captures.push(body.subarray(start, end));
      const text = captures.length === 1 ? 'Tell me about the demo.' : 'Please give a shorter answer.';
      return route.fulfill({ contentType: 'text/event-stream', body:
        `event: done\ndata: ${JSON.stringify({ text, backend: 'whisper', seconds: 0.01 })}\n\n` });
    }
    if (path === '/api/chat') {
      chats.push(request.postDataJSON());
      if (holdFollowup && chats.length === 2) await followup;
      return route.fulfill({ json: { reply: chats.length === 1 ? 'A long first answer for the demo.' : 'Here is the shorter answer.' } }).catch(() => {});
    }
    if (request.method() === 'GET' || path === '/api/agent/models') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation in interruption check' } });
  });
  await page.route('http://speech.fixture/v1/audio/speech', route => {
    speech.push(route.request().postDataJSON());
    return route.fulfill({ contentType: 'audio/wav', body: wavTone(speech.length === 1 ? 10 : 1) });
  });
  await page.goto('/');
  await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
  await page.evaluate(() => {
    window.__nativeEnded = 0;
    document.getElementById('audio').addEventListener('ended', () => window.__nativeEnded++);
  });
  await page.locator('#liveBtn').click();
  await expect.poll(() => page.evaluate(() => window.state.mediaRecorder?.state)).toBe('recording');
  await expect.poll(() => page.evaluate(() => performance.now() > window.state.vadCalibratingUntil)).toBe(true);
  await page.evaluate(() => { window.__inputLevel = 160; });
  // Confirm observed speech duration, not wall time while a Live callback may
  // not have sampled the synthetic input. Otherwise min_speech_ms can reject it.
  await expect.poll(() => page.evaluate(() => window.state.liveSpeechStart > 0
    && window.state.liveLastVoice - window.state.liveSpeechStart > 450)).toBe(true);
  await page.evaluate(() => { window.__inputLevel = 128; });
  await expect.poll(() => page.evaluate(() => window.state.ttsSpeaking
    && !document.getElementById('audio').paused && window.state.bargeRecorder?.state === 'recording')).toBe(true);
  return { errors, chats, captures, speech, release };
}

async function interrupt(page) {
  // Retain enough native Opus data to cross the product's short-capture cutoff.
  await expect.poll(() => page.evaluate(() => window.state.bargeChunks.reduce((size, chunk) => size + chunk.size, 0))).toBeGreaterThan(2500);
  await page.evaluate(() => { window.__inputLevel = 160; });
  await expect.poll(() => page.evaluate(() => window.state.handlingBarge)).toBe(true);
  expect(await page.evaluate(() => document.getElementById('audio').paused)).toBe(true);
  expect(await page.locator('#audio').getAttribute('src')).toBeNull();
  await page.evaluate(() => { window.__inputLevel = 128; });
}

test('native interruption stops speech, records a complete turn, replies with context, and resumes capture', async ({ page }) => {
  const result = await interruptionPage(page);
  try {
    await interrupt(page);
    await expect.poll(() => result.chats.length).toBe(2);
    await expect(page.locator('#text')).toHaveValue('Here is the shorter answer.');
    await expect.poll(() => page.evaluate(() => window.__nativeEnded)).toBe(1);
    await expect.poll(() => page.evaluate(() => window.state.live && !window.state.liveBusy
      && !window.state.handlingBarge && !window.state.ttsSpeaking && window.state.mediaRecorder?.state === 'recording')).toBe(true);
    expect(result.captures).toHaveLength(2);
    expect(result.chats[0].message).toBe('Tell me about the demo.');
    expect(result.chats[1].message).toContain('The user interrupted Korina');
    expect(result.chats[1].message).toContain('The user now says: Please give a shorter answer.');
    expect(result.chats[1].history).toEqual([
      { role: 'user', content: 'Tell me about the demo.' },
      { role: 'assistant', content: 'A long first answer for the demo.' },
    ]);
    expect(result.speech.map(request => request.input)).toEqual([
      'A long first answer for the demo.', 'Here is the shorter answer.',
    ]);
    for (const capture of result.captures) {
      const duration = await page.evaluate(async data => {
        const bytes = Uint8Array.from(atob(data), character => character.charCodeAt(0));
        const context = new AudioContext();
        try { return (await context.decodeAudioData(bytes.buffer)).duration; }
        finally { await context.close(); }
      }, capture.toString('base64'));
      expect(duration).toBeGreaterThan(0.3);
    }
    expect(result.errors).toEqual([]);
  } finally {
    result.release();
    await page.locator('#liveBtn').click();
    await expect.poll(() => page.evaluate(() => window.state.stream)).toBeNull();
  }
});

test('Stop Live during native interruption chat aborts the fetch and releases capture without stale speech', async ({ page }) => {
  const result = await interruptionPage(page, { holdFollowup: true });
  let aborted = false;
  page.on('requestfailed', request => { if (new URL(request.url()).pathname === '/api/chat') aborted = true; });
  try {
    await interrupt(page);
    await expect.poll(() => result.chats.length).toBe(2);
    await page.locator('#liveBtn').click();
    await expect.poll(() => aborted).toBe(true);
    await expect.poll(() => page.evaluate(() => window.state.stream)).toBeNull();
    result.release();
    await expect(page.locator('#sttStatus')).toHaveText('Live mode stopped.');
    expect(await page.evaluate(() => window.state.history.some(turn => turn.content === 'Here is the shorter answer.'))).toBe(false);
    expect(result.speech).toHaveLength(1);
    expect(await page.evaluate(() => document.getElementById('audio').paused)).toBe(true);
    expect(await page.locator('#audio').getAttribute('src')).toBeNull();
    expect(result.errors).toEqual([]);
  } finally { result.release(); }
});
