import { test, expect } from '@playwright/test';

function wavTone(seconds) {
  const rate = 24000;
  const samples = Math.round(seconds * rate);
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36);
  wav.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index++) wav.writeInt16LE(Math.round(1200 * Math.sin(index * 2 * Math.PI * 440 / rate)), 44 + index * 2);
  return wav;
}

async function loadBufferedPage(page, body) {
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
      return route.fulfill({ json: { ...config, mode: 'buf', ack_enabled: 'off', agent_enabled: 'off', tts_provider: 'kokoro', tts_base_url: 'http://speech.fixture' } });
    }
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (request.method() === 'GET') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during playback test' } });
  });
  await page.route('http://speech.fixture/v1/audio/speech', route => route.fulfill({ status: 200, contentType: 'audio/wav', body }));
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('');
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

test('an empty SSE success response is reported as missing speech', async ({ page }) => {
  await loadBufferedPage(page, wavTone(1));
  await page.route('http://speech.fixture/stream/speech', route => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }));
  await page.evaluate(() => { window.state.mode = 'sse'; });
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('no audio samples');
  await expect(page.locator('#stopBtn')).toBeDisabled();
});
