import { test, expect } from '@playwright/test';

function healthPayload(provider = 'lmstudio', model = 'old-model') {
  return { ok: true, whisper_loaded: true, whisper_available: true, stt_backend: 'whisper',
    response_llm_provider: provider, response_llm_model: model, response_llm_base_url: `http://${provider}.fixture/v1`,
    response_llm_load: { ok: true, loaded: true, provider, loaded_model: model, expected_model: model },
    tts: { ok: true, loaded: true, provider: 'kokoro', base_url: 'http://tts.fixture' },
    audio_limits: { max_duration_seconds: 90, max_upload_bytes: 4 * 1024 * 1024 }, audio_limits_error: '',
  };
}

async function loadHealthPage(page) {
  const errors = [], pending = [];
  let hold = false;
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const interval = window.setInterval.bind(window);
    window.setInterval = (callback, ...args) => callback.name === 'health' ? 0 : interval(callback, ...args);
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/health') {
      if (!hold) return route.fulfill({ json: healthPayload() });
      return new Promise(resolve => { pending.push({ route, resolve }); });
    }
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, llm_provider: 'lmstudio', lm_model: 'old-model',
        llm_base_url: 'http://lmstudio.fixture/v1', agent_enabled: 'off', ack_enabled: 'off' } });
    }
    if (endpoint === '/api/llm/provider/activate') {
      const selection = request.postDataJSON();
      return route.fulfill({ json: { saved: { llm_provider: selection.provider, lm_model: selection.model,
        llm_base_url: `http://${selection.provider}.fixture/v1` } } });
    }
    if (endpoint === '/api/agent/reset') return route.fulfill({ json: { ok: true } });
    if (endpoint === '/api/agent/models') return route.fulfill({ json: {
      ok: true, provider: 'openai-compatible', models: [], catalog_info: {} } });
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (request.method() === 'GET') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during health ownership proof' } });
  });
  await page.goto('/');
  await expect(page.locator('#providerReady')).toContainText('ready · old-model', { timeout: 15000 });
  await expect(page.locator('#lmModel')).toHaveValue('old-model');
  hold = true;
  async function start(name) {
    await page.evaluate(name => { window[name] = window.health(); }, name);
  }
  async function release(index, payload, fail = false) {
    const item = pending[index];
    if (fail) await item.route.abort('failed');
    else await item.route.fulfill({ json: payload });
    item.resolve();
  }
  return { errors, pending, start, release };
}

test('late old readiness cannot clear pending activation or replace the current loading model', async ({ page }) => {
  const proof = await loadHealthPage(page);
  await proof.start('oldHealth');
  await expect.poll(() => proof.pending.length).toBe(1);
  await page.evaluate(async () => {
    document.getElementById('llmProvider').value = 'ollama';
    const select = document.getElementById('lmModel');
    select.add(new Option('new-model', 'new-model')); select.value = 'new-model';
    document.getElementById('llmBaseUrl').value = 'http://ollama.fixture/v1';
    await window.activateSelectedProvider('ollama', 'new-model');
  });
  await proof.start('currentHealth');
  await expect.poll(() => proof.pending.length).toBe(2);
  const loading = healthPayload('ollama', 'new-model'); loading.response_llm_load.loaded = false;
  await proof.release(1, loading);
  await page.evaluate(() => window.currentHealth);
  await expect(page.locator('#providerReady')).toContainText('ollama · loading new-model');
  await proof.release(0, healthPayload());
  await page.evaluate(() => window.oldHealth);
  await expect(page.locator('#providerReady')).toContainText('ollama · loading new-model');
  await expect(page.locator('#providerReadyDot')).toHaveClass('dot warn');
  await expect(page.locator('#hostInfo')).toContainText('http://ollama.fixture/v1');
  expect(await page.evaluate(() => window.state.providerPending)).toBe(true);
  expect(proof.errors).toEqual([]);
});

test('Clear and newer valid audio budgets retire an older recording-blocking error', async ({ page }) => {
  const proof = await loadHealthPage(page);
  await proof.start('oldHealth');
  await expect.poll(() => proof.pending.length).toBe(1);
  await page.locator('#clearSessionBtn').click();
  await expect(page.locator('#sttStatus')).toContainText('Session cleared');
  await proof.start('currentHealth');
  await expect.poll(() => proof.pending.length).toBe(2);
  await proof.release(1, healthPayload());
  await page.evaluate(() => window.currentHealth);
  await proof.release(0, { ...healthPayload(), audio_limits: null, audio_limits_error: 'Retired invalid limits' });
  await page.evaluate(() => window.oldHealth);
  expect(await page.evaluate(() => window.state.audioLimitsError)).toBe('');
  expect(await page.evaluate(async () => {
    const { captureLimits } = await import('/js/bounded-recorder.js');
    return captureLimits().maxSeconds;
  })).toBe(90);
  await expect(page.locator('#settingsLiveStatus')).toContainText('STT up');
  expect(proof.errors).toEqual([]);
});

test('network failures from older health polls cannot undo a newer successful recovery', async ({ page }) => {
  const proof = await loadHealthPage(page);
  for (let index = 0; index < 3; index++) await proof.start(`oldHealth${index}`);
  await proof.start('currentHealth');
  await expect.poll(() => proof.pending.length).toBe(4);
  await proof.release(3, healthPayload());
  await page.evaluate(() => window.currentHealth);
  for (let index = 0; index < 3; index++) {
    await proof.release(index, null, true);
    await page.evaluate(index => window[`oldHealth${index}`], index);
  }
  expect(await page.evaluate(() => window.state.healthFailures)).toBe(0);
  await expect(page.locator('#pageDot')).toHaveClass('dot good');
  await expect(page.locator('#pageHealth')).not.toContainText('unreachable');
  expect(proof.errors).toEqual([]);
});
