import { test, expect } from '@playwright/test';

function pcmEvent(seconds = 2, rate = 24000) {
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
    window.__speechScheduleTimes = [];
    window.__speechEof = false;
    const Context = window.AudioContext || window.webkitAudioContext;
    const createSource = Context.prototype.createBufferSource;
    Context.prototype.createBufferSource = function (...args) {
      const source = createSource.apply(this, args);
      const record = { started: 0, stopped: 0, ended: false };
      window.__speechSources.push(record);
      const start = source.start.bind(source), stop = source.stop.bind(source);
      source.start = (...parameters) => { record.started++; window.__speechScheduleTimes.push(parameters[0]); return start(...parameters); };
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
    if (request.method() === 'GET' || endpoint === '/api/agent/models' && request.method() === 'POST') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during streaming playback check' } });
  });
  await page.route('http://speech.fixture/stream/speech', route => route.fulfill({ contentType: 'text/event-stream', body }));
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('page server…', { timeout: 15000 });
  await expect(page.locator('#ttsBaseUrl')).toHaveValue('http://speech.fixture');
  return errors;
}

test('streamed interruption context starts at the first native PCM schedule after delayed synthesis', async ({ page }) => {
  const errors = await loadStreamingPage(page, pcmEvent(4));
  let release, requested = false;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('http://speech.fixture/stream/speech', async route => {
    requested = true;
    await held;
    await route.fulfill({ contentType: 'text/event-stream', body: pcmEvent(4) }).catch(() => {});
  });
  const text = 'The entire streamed reply is still unspoken.';
  try {
    await page.locator('#text').fill(text);
    await page.locator('#playBtn').click();
    await expect.poll(() => requested).toBe(true);
    const pendingClock = await page.evaluate(() => window.state.audioCtx.currentTime);
    await expect.poll(() => page.evaluate(start => window.state.audioCtx.currentTime - start > .3, pendingClock)).toBe(true);
    const pending = await page.evaluate(async () => ({ startedAt: window.state.currentTtsStartedAt,
      sources: window.state.activeSources.length,
      context: (await import('/js/speech.js')).interruptedSpeechContext() }));
    expect(pending.startedAt).toBeNull();
    expect(pending.sources).toBe(0);
    expect(pending.context).toContain('about 0%');
    expect(pending.context).toContain(`Already spoken approximately: "". Not yet spoken approximately: "${text}"`);
    release();
    await expect.poll(() => page.evaluate(() => window.state.activeSources.length)).toBe(1);
    const scheduled = await page.evaluate(() => ({ startedAt: window.state.currentTtsStartedAt,
      when: window.__speechScheduleTimes[0], estimatedEnd: window.state.currentTtsEstimatedEnd }));
    expect(scheduled.startedAt).toBe(scheduled.when);
    expect(scheduled.startedAt).toBeGreaterThan(pendingClock + .3);
    expect(scheduled.estimatedEnd - scheduled.startedAt).toBeCloseTo(4);
    await page.locator('#stopBtn').click();
    await expect(page.locator('#stopBtn')).toBeDisabled();
    expect(errors).toEqual([]);
  } finally { release(); }
});

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

for (const action of ['resume', 'Stop']) {
  test(`suspended native PCM retains its playback owner until ${action}`, async ({ page }) => {
    const errors = await loadStreamingPage(page, pcmEvent());
    await page.locator('#playBtn').click();
    await expect.poll(() => page.evaluate(() => window.__speechEof)).toBe(true);
    await expect.poll(() => page.evaluate(() => window.state.activeSources.length)).toBe(1);
    await page.evaluate(() => window.state.audioCtx.suspend());
    await page.waitForTimeout(2300);
    expect(await page.evaluate(() => window.state.audioCtx.state)).toBe('suspended');
    expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(true);
    expect(await page.evaluate(() => window.__speechSources[0].ended)).toBe(false);
    await expect(page.locator('#stopBtn')).toBeEnabled();
    await expect(page.locator('#playBtn')).toBeDisabled();
    await expect(page.locator('#ttsStatus')).not.toContainText('Done');

    if (action === 'resume') {
      await page.evaluate(() => window.state.audioCtx.resume());
      await expect(page.locator('#ttsStatus')).toContainText('Done · 1 chunks');
      expect(await page.evaluate(() => window.__speechSources[0])).toEqual({ started: 1, stopped: 0, ended: true });
    } else {
      await page.locator('#stopBtn').click();
      await expect.poll(() => page.evaluate(() => window.__speechSources[0].stopped), { timeout: 750 }).toBeGreaterThan(0);
      expect(await page.evaluate(() => window.state.activeSources.length)).toBe(0);
    }
    await expect(page.locator('#stopBtn')).toBeDisabled();
    await expect(page.locator('#playBtn')).toBeEnabled();
    expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
    if (action === 'Stop') {
      await page.locator('#playBtn').click();
      await expect.poll(() => page.evaluate(() => window.__speechSources.length)).toBe(2);
      expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(true);
      await expect(page.locator('#stopBtn')).toBeEnabled();
      await page.locator('#stopBtn').click();
      await expect(page.locator('#stopBtn')).toBeDisabled({ timeout: 750 });
      expect(await page.evaluate(() => window.__speechSources.every(source => source.stopped > 0))).toBe(true);
    }
    expect(errors).toEqual([]);
  });
}

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

test('a truncated SSE tail stops already scheduled native PCM and reports failure', async ({ page }) => {
  const errors = await loadStreamingPage(page, pcmEvent() + 'data: {"audio":"truncated');
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('Error:');
  const sources = await page.evaluate(() => window.__speechSources);
  expect(sources).toHaveLength(1);
  expect(sources[0].started).toBe(1);
  expect(sources[0].stopped).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.state.activeSources.length)).toBe(0);
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  await expect(page.locator('#stopBtn')).toBeDisabled();
  await expect(page.locator('#playBtn')).toBeEnabled();
  expect(errors).toEqual([]);
});

test('CRLF speech events play native PCM and complete normally', async ({ page }) => {
  const errors = await loadStreamingPage(page, pcmEvent(0.1).replaceAll('\n', '\r\n'));
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toContainText('Done · 1 chunks');
  expect(await page.evaluate(() => window.__speechSources)).toEqual([
    { started: 1, stopped: 0, ended: true },
  ]);
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  await expect(page.locator('#stopBtn')).toBeDisabled();
  expect(errors).toEqual([]);
});

test('cumulative speech duration stops native playback before allocating an over-limit chunk', async ({ page }) => {
  const errors = await loadStreamingPage(page, pcmEvent(300, 3000) + pcmEvent(301, 3000));
  await page.locator('#playBtn').click();
  await expect(page.locator('#ttsStatus')).toHaveClass('status bad');
  await expect(page.locator('#ttsStatus')).toContainText('audio limit');
  const sources = await page.evaluate(() => window.__speechSources);
  expect(sources).toHaveLength(1);
  expect(sources[0].started).toBe(1);
  expect(sources[0].stopped).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.state.activeSources.length)).toBe(0);
  expect(await page.evaluate(() => window.state.ttsSpeaking)).toBe(false);
  await expect(page.locator('#stopBtn')).toBeDisabled();
  await expect(page.locator('#playBtn')).toBeEnabled();
  expect(errors).toEqual([]);
});
