import { test, expect } from '@playwright/test';

function pcmEvent(seconds = 2) {
  const rate = 24000;
  const pcm = Buffer.alloc(Math.round(seconds * rate) * 2);
  for (let index = 0; index < pcm.length / 2; index++) {
    pcm.writeInt16LE(Math.round(1200 * Math.sin(index * 2 * Math.PI * 440 / rate)), index * 2);
  }
  return `data: ${JSON.stringify({ audio: pcm.toString('base64'), sample_rate: rate })}\n\n`;
}

async function loadStreamingPage(page, body) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__speechSources = [];
    window.__speechEof = false;
    const Context = window.AudioContext || window.webkitAudioContext;
    const createSource = Context.prototype.createBufferSource;
    Context.prototype.createBufferSource = function (...args) {
      const source = createSource.apply(this, args);
      const record = { started: 0, stopped: 0, ended: false };
      window.__speechSources.push(record);
      const start = source.start.bind(source), stop = source.stop.bind(source);
      source.start = (...parameters) => { record.started++; return start(...parameters); };
      source.stop = (...parameters) => { record.stopped++; return stop(...parameters); };
      source.addEventListener('ended', () => { record.ended = true; });
      return source;
    };
    const fetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await fetch(...args);
      if (String(args[0]).endsWith('/stream/speech')) {
        const getReader = response.body.getReader.bind(response.body);
        response.body.getReader = (...parameters) => {
          const reader = getReader(...parameters), read = reader.read.bind(reader);
          reader.read = async () => {
            const result = await read();
            if (result.done) window.__speechEof = true;
            return result;
          };
          return reader;
        };
      }
      return response;
    };
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, mode: 'sse', ack_enabled: 'off', agent_enabled: 'off', tts_provider: 'kokoro', tts_base_url: 'http://speech.fixture' } });
    }
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (request.method() === 'GET') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during streaming playback check' } });
  });
  await page.route('http://speech.fixture/stream/speech', route => route.fulfill({ contentType: 'text/event-stream', body }));
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('');
  await expect(page.locator('#ttsBaseUrl')).toHaveValue('http://speech.fixture');
  return errors;
}

test('finite SSE keeps native PCM playback active after EOF and Stop cancels it promptly', async ({ page }) => {
  const errors = await loadStreamingPage(page, pcmEvent());
  await page.locator('#playBtn').click();
  await expect.poll(() => page.evaluate(() => window.__speechEof)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.__speechSources.some(source => source.started))).toBe(true);
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(true);
  await expect(page.locator('#stopBtn')).toBeEnabled();
  expect(await page.evaluate(() => window.__speechSources[0].ended)).toBe(false);
  await page.locator('#stopBtn').click();
  await expect(page.locator('#stopBtn')).toBeDisabled({ timeout: 750 });
  await expect(page.locator('#playBtn')).toBeEnabled();
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  expect(await page.evaluate(() => window.state.activeSources.length)).toBe(0);
  expect(await page.evaluate(() => window.state.controller)).toBeNull();
  expect(await page.evaluate(() => window.__speechSources.every(source => source.stopped > 0))).toBe(true);
  expect(errors).toEqual([]);
});

test('an SSE error after native PCM scheduling stops the audio before disabling Stop', async ({ page }) => {
  const errors = await loadStreamingPage(page, pcmEvent() + 'data: {"error":"synthesis failed after first chunk"}\n\n');
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('missing PCM audio');
  const sources = await page.evaluate(() => window.__speechSources);
  expect(sources).toHaveLength(1);
  expect(sources[0].started).toBe(1);
  expect(sources[0].stopped).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.state.activeSources.length)).toBe(0);
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  expect(await page.evaluate(() => window.state.controller)).toBeNull();
  await expect(page.locator('#stopBtn')).toBeDisabled();
  await expect(page.locator('#playBtn')).toBeEnabled();
  expect(errors).toEqual([]);
});
