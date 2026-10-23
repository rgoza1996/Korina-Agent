import { test, expect } from '@playwright/test';

import { wavTone } from './fixtures/audio.js';

async function loadBufferedPage(page, body, mode = 'buf') {
  await page.addInitScript(() => {
    window.__revokedAudioUrls = [];
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => { window.__revokedAudioUrls.push(url); revoke(url); };
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, mode, ack_enabled: 'off', agent_enabled: 'off', tts_provider: 'kokoro', tts_base_url: 'http://speech.fixture' } });
    }
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (request.method() === 'GET' || endpoint === '/api/agent/models' && request.method() === 'POST') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during playback test' } });
  });
  await page.route('http://speech.fixture/v1/audio/speech', route => route.fulfill({ status: 200, contentType: 'audio/wav', body }));
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('page server…', { timeout: 15000 });
  await expect(page.locator('#ttsBaseUrl')).toHaveValue('http://speech.fixture');
  await page.evaluate(() => {
    window.__bufferedEnded = 0;
    document.getElementById('audio').addEventListener('ended', () => window.__bufferedEnded++);
  });
}

test('native buffered playback keeps Stop enabled until ended and revokes its blob URL', async ({ page }) => {
  await loadBufferedPage(page, wavTone(1.2));
  await page.locator('#playBtn').click();
  await expect.poll(() => page.evaluate(() => !document.getElementById('audio').paused)).toBe(true);
  const url = await page.locator('#audio').getAttribute('src');
  expect(url).toMatch(/^blob:/);
  await expect(page.locator('#stopBtn')).toBeEnabled();
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.__bufferedEnded)).toBe(1);
  await expect(page.locator('#ttsStatus')).toHaveText('Done · buffered playback complete');
  await expect(page.locator('#stopBtn')).toBeDisabled();
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  expect(await page.locator('#audio').getAttribute('src')).toBeNull();
  expect(await page.evaluate(() => window.__revokedAudioUrls)).toEqual([url]);
});

test('Stop interrupts native buffered playback and releases its media source', async ({ page }) => {
  await loadBufferedPage(page, wavTone(4));
  await page.locator('#playBtn').click();
  await expect.poll(() => page.evaluate(() => !document.getElementById('audio').paused)).toBe(true);
  const url = await page.locator('#audio').getAttribute('src');
  await page.locator('#stopBtn').click();
  await expect(page.locator('#stopBtn')).toBeDisabled();
  expect(await page.evaluate(() => document.getElementById('audio').paused)).toBe(true);
  expect(await page.locator('#audio').getAttribute('src')).toBeNull();
  expect(await page.evaluate(() => window.__bufferedEnded)).toBe(0);
  expect(await page.evaluate(() => window.__revokedAudioUrls)).toEqual([url]);
});

test('native media decode failure is visible instead of reporting completed playback', async ({ page }) => {
  await loadBufferedPage(page, Buffer.from('This is not a WAV file.'));
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('Error:');
  await expect(page.locator('#stopBtn')).toBeDisabled();
  expect(await page.locator('#audio').getAttribute('src')).toBeNull();
  expect(await page.evaluate(() => window.__revokedAudioUrls.length)).toBe(1);
});

test('Stop settles a buffered native fetch before response headers arrive', async ({ page }) => {
  await loadBufferedPage(page, wavTone(1));
  let release;
  let requested = false;
  const barrier = new Promise(resolve => { release = resolve; });
  await page.route('http://speech.fixture/v1/audio/speech', async route => {
    requested = true;
    await barrier;
    await route.fulfill({ status: 200, contentType: 'audio/wav', body: wavTone(1) }).catch(() => {});
  });
  try {
    await page.locator('#playBtn').click();
    await expect.poll(() => requested).toBe(true);
    await page.locator('#stopBtn').click();
    await expect(page.locator('#playBtn')).toBeEnabled();
    await expect(page.locator('#stopBtn')).toBeDisabled();
    expect(await page.evaluate(() => window.state.controller)).toBeNull();
    expect(await page.locator('#audio').getAttribute('src')).toBeNull();
    expect(await page.evaluate(() => window.__revokedAudioUrls)).toEqual([]);
  } finally { release(); }
});

test('buffered interruption context waits for synthesis and follows the paused native media clock', async ({ page }) => {
  await loadBufferedPage(page, wavTone(4));
  await page.evaluate(() => {
    window.__bufferedPlaying = false;
    document.getElementById('audio').addEventListener('playing', () => { window.__bufferedPlaying = true; }, { once: true });
  });
  let release, requested = false;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('http://speech.fixture/v1/audio/speech', async route => {
    requested = true;
    await held;
    await route.fulfill({ contentType: 'audio/wav', body: wavTone(4) }).catch(() => {});
  });
  const text = 'The entire buffered reply is still unspoken.';
  try {
    await page.locator('#text').fill(text);
    await page.locator('#playBtn').click();
    await expect.poll(() => requested).toBe(true);
    const pendingClock = await page.evaluate(() => window.state.audioCtx.currentTime);
    await expect.poll(() => page.evaluate(start => window.state.audioCtx.currentTime - start > .3, pendingClock)).toBe(true);
    const pending = await page.evaluate(async () => ({ startedAt: window.state.currentTtsStartedAt,
      paused: document.getElementById('audio').paused,
      context: (await import('/js/speech.js')).interruptedSpeechContext() }));
    expect(pending.startedAt).toBeNull();
    expect(pending.paused).toBe(true);
    expect(pending.context).toContain('about 0%');
    expect(pending.context).toContain(`Already spoken approximately: "". Not yet spoken approximately: "${text}"`);
    release();
    // play() clears paused before metadata is available; wait for native
    // playback to start before pausing and seeking its actual media clock.
    await expect.poll(() => page.evaluate(() => window.__bufferedPlaying)).toBe(true);
    const paused = await page.evaluate(async () => {
      const audio = document.getElementById('audio');
      audio.pause();
      audio.currentTime = 1;
      await window.state.audioCtx.suspend();
      return { duration: audio.duration, context: (await import('/js/speech.js')).interruptedSpeechContext() };
    });
    expect(paused.duration).toBe(4);
    expect(paused.context).toContain('about 25%');
    await page.locator('#stopBtn').click();
    await expect(page.locator('#stopBtn')).toBeDisabled();
    expect(await page.locator('#audio').getAttribute('src')).toBeNull();
  } finally { release(); }
});

test('an empty SSE success response is reported as missing speech', async ({ page }) => {
  await loadBufferedPage(page, wavTone(1), 'sse');
  await page.route('http://speech.fixture/stream/speech', route => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }));
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('no audio samples');
  await expect(page.locator('#stopBtn')).toBeDisabled();
});

test('native buffered metadata rejects compact audio longer than the playback limit', async ({ page }) => {
  await loadBufferedPage(page, wavTone(601, 3000));
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('ten minutes');
  await expect(page.locator('#stopBtn')).toBeDisabled();
  await expect(page.locator('#playBtn')).toBeEnabled();
  expect(await page.locator('#audio').getAttribute('src')).toBeNull();
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  expect(await page.evaluate(() => window.__revokedAudioUrls.length)).toBe(1);
});
