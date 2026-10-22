import { test, expect } from '@playwright/test';

test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }, permissions: ['microphone'] });

async function loadCapturePage(page, healthOverrides = {}) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/health') {
      const health = await (await route.fetch()).json();
      return route.fulfill({ json: { ...health, ...healthOverrides } });
    }
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, ack_enabled: 'off', agent_enabled: 'off', partial_window_ms: 10000 } });
    }
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (endpoint === '/api/agent/reset') return route.fulfill({ json: { ok: true } });
    if (request.method() === 'GET' || endpoint === '/api/agent/models' && request.method() === 'POST') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during capture check' } });
  });
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('');
  return errors;
}

test('Clear Session cancels manual transcription without restoring old text or controls', async ({ page }) => {
  const errors = await loadCapturePage(page);
  let release;
  let requested = false;
  let aborted = false;
  page.on('requestfailed', request => {
    if (new URL(request.url()).pathname === '/api/transcribe/stream') aborted = true;
  });
  const barrier = new Promise(resolve => { release = resolve; });
  await page.route('**/api/transcribe/stream?**', async route => {
    requested = true;
    await barrier;
    await route.fulfill({ contentType: 'text/event-stream', body:
      'event: segment\ndata: {"text":"Old captured words","index":1}\n\n' +
      'event: done\ndata: {"text":"Old captured words","seconds":1,"model":"fixture","backend":"whisper"}\n\n',
    }).catch(() => {});
  });
  try {
    await page.locator('#recBtn').click();
    await expect.poll(() => page.evaluate(() => window.state.mediaRecorder?.state)).toBe('recording');
    await page.locator('#stopRecBtn').click();
    await expect.poll(() => requested).toBe(true);
    await page.locator('#clearSessionBtn').click();
    await expect(page.locator('#clearSessionBtn')).toBeEnabled();
    await expect.poll(() => aborted).toBe(true);
    release();
    await expect(page.locator('#recBtn')).toBeEnabled();
    await expect(page.locator('#stopRecBtn')).toBeDisabled();
    await expect(page.locator('#transcript')).toHaveValue('');
    await expect(page.locator('#sttStatus')).toContainText('Session cleared');
    expect(errors).toEqual([]);
  } finally { release(); }
});

test('starting Live cancels an active manual recording and retains one live capture', async ({ page }) => {
  const errors = await loadCapturePage(page);
  let uploads = 0;
  await page.route('**/api/transcribe/stream?**', route => {
    uploads++;
    return route.fulfill({ status: 409, json: { detail: 'Cancelled manual capture must not upload' } });
  });
  await page.locator('#recBtn').click();
  await expect.poll(() => page.evaluate(() => window.state.mediaRecorder?.state)).toBe('recording');
  await page.locator('#liveBtn').click();
  await expect.poll(() => page.evaluate(() => window.state.live && window.state.stream?.active && window.state.mediaRecorder?.state === 'recording')).toBe(true);
  await expect(page.locator('#recBtn')).toBeDisabled();
  expect(uploads).toBe(0);
  await page.locator('#liveBtn').click();
  await expect.poll(() => page.evaluate(() => window.state.stream)).toBeNull();
  expect(errors).toEqual([]);
});

test('manual capture stops at the advertised duration and uploads a complete native recording', async ({ page }) => {
  const errors = await loadCapturePage(page, {
    audio_limits: { max_duration_seconds: 2, max_upload_bytes: 50 * 1024 * 1024 },
    audio_limits_error: '',
  });
  let uploads = 0;
  await page.route('**/api/transcribe/stream?**', route => {
    uploads++;
    return route.fulfill({ contentType: 'text/event-stream', body:
      'event: done\ndata: {"text":"A bounded recording","seconds":0.01,"model":"fixture","backend":"whisper"}\n\n',
    });
  });
  await page.locator('#recBtn').click();
  await expect.poll(() => page.evaluate(() => window.state.mediaRecorder?.state)).toBe('recording');
  await expect(page.locator('#transcript')).toHaveValue('A bounded recording');
  await expect(page.locator('#sttStatus')).toContainText('Recording limit reached');
  await expect(page.locator('#recBtn')).toBeEnabled();
  await expect(page.locator('#stopRecBtn')).toBeDisabled();
  expect(uploads).toBe(1);
  const captured = await page.evaluate(async () => {
    const blob = new Blob(window.state.recChunks, { type: window.state.recChunks[0]?.type });
    const context = new AudioContext();
    try {
      const decoded = await context.decodeAudioData(await blob.arrayBuffer());
      return { bytes: blob.size, duration: decoded.duration, stream: window.state.stream };
    } finally { await context.close(); }
  });
  expect(captured.bytes).toBeGreaterThan(0);
  expect(captured.duration).toBeGreaterThan(1);
  expect(captured.duration).toBeLessThanOrEqual(2);
  expect(captured.stream).toBeNull();
  expect(errors).toEqual([]);
});

test('an idle Live session rotates native captures without uploading silence', async ({ page }) => {
  // Control only the VAD meter. MediaRecorder and its microphone stream stay native.
  await page.addInitScript(() => {
    AnalyserNode.prototype.getByteTimeDomainData = data => data.fill(128);
  });
  const errors = await loadCapturePage(page, {
    audio_limits: { max_duration_seconds: 1, max_upload_bytes: 50 * 1024 * 1024 },
    audio_limits_error: '',
  });
  let uploads = 0;
  await page.route('**/api/transcribe/**', route => {
    uploads++;
    return route.fulfill({ status: 409, json: { detail: 'An idle capture must not upload' } });
  });
  await page.locator('#liveBtn').click();
  await expect.poll(() => page.evaluate(() => {
    if (window.state.mediaRecorder?.state !== 'recording') return false;
    window.firstCapture = window.state.mediaRecorder;
    return true;
  })).toBe(true);
  await expect.poll(() => page.evaluate(() =>
    window.state.live && window.state.stream?.active &&
    window.state.mediaRecorder !== window.firstCapture &&
    window.state.mediaRecorder?.state === 'recording',
  )).toBe(true);
  expect(uploads).toBe(0);
  await page.locator('#liveBtn').click();
  await expect.poll(() => page.evaluate(() => window.state.stream)).toBeNull();
  expect(errors).toEqual([]);
});

test('an invalid server audio limit prevents capture and releases the microphone', async ({ page }) => {
  const errors = await loadCapturePage(page, {
    audio_limits: null,
    audio_limits_error: 'KORINA_MAX_AUDIO_SECONDS must be a finite number between 1 and 1800.',
  });
  await page.locator('#recBtn').click();
  await expect(page.locator('#sttStatus')).toContainText('Recording unavailable');
  await expect(page.locator('#sttStatus')).toContainText('KORINA_MAX_AUDIO_SECONDS');
  await expect(page.locator('#recBtn')).toBeEnabled();
  await expect(page.locator('#stopRecBtn')).toBeDisabled();
  expect(await page.evaluate(() => window.state.stream)).toBeNull();
  expect(errors).toEqual([]);
});
