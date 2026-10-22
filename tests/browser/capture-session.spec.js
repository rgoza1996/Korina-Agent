import { test, expect } from '@playwright/test';

test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }, permissions: ['microphone'] });

async function loadCapturePage(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, ack_enabled: 'off', agent_enabled: 'off', partial_window_ms: 10000 } });
    }
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (endpoint === '/api/agent/reset') return route.fulfill({ json: { ok: true } });
    if (request.method() === 'GET') return route.continue();
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
