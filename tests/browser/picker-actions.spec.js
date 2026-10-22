import { test, expect } from '@playwright/test';

const triple = model => `openai-compatible::http://audio.fixture/v1::${model}`;
const models = { llm_models: ['saved-response', 'other-response'], llm_default: 'saved-response',
  stt_llm_models: ['audio-a', 'audio-b'], stt_llm_models_capabilities: {
    'audio-a': { supports_audio_input: true }, 'audio-b': { supports_audio_input: true },
  } };

async function app(page, backend = 'whisper') {
  const scenario = { modelCalls: 0, heldModels: [], holdModels: false, reset: 'http', heldReset: null,
    probeRead: 'success', heldProbeReads: [], abortedProbeReads: 0,
    probes: { [triple('audio-a')]: { reason: 'cached unsupported a' }, [triple('audio-b')]: { reason: 'cached unsupported b' } }, errors: [] };
  const config = { ack_enabled: 'off', agent_enabled: 'off', stt_backend: backend,
    llm_provider: 'openai-compatible', llm_base_url: 'http://response.fixture/v1', lm_model: 'saved-response',
    stt_llm_provider: 'openai-compatible', stt_llm_base_url: 'http://audio.fixture/v1', stt_llm_model: 'audio-a' };
  page.on('pageerror', error => scenario.errors.push(error.message));
  page.on('requestfailed', request => {
    if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/audio-probe') scenario.abortedProbeReads++;
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === '/api/config') {
      if (request.method() === 'POST') Object.assign(config, request.postDataJSON());
      return route.fulfill({ json: config });
    }
    if (url.pathname === '/api/health') return route.fulfill({ json: { ok: true, whisper_available: true,
      response_llm_load: { ok: true, loaded: true } } });
    if (url.pathname === '/api/agent/models') return route.fulfill({ json: { ok: true, models: ['agent-choice'] } });
    if (url.pathname === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (url.pathname === '/api/models') {
      scenario.modelCalls++;
      if (scenario.holdModels) { scenario.heldModels.push(route); return; }
      return route.fulfill({ json: models });
    }
    if (url.pathname === '/api/audio-probe') {
      if (request.method() === 'GET') {
        if (scenario.probeRead === 'hold') { scenario.heldProbeReads.push(route); return; }
        if (scenario.probeRead === 'http') return route.fulfill({ status: 503, json: { detail: 'Probe cache temporarily unavailable' } });
        return route.fulfill({ json: { entries: scenario.probes } });
      }
      if (scenario.reset === 'hold') { scenario.heldReset = route; return; }
      if (scenario.reset === 'network') return route.abort('failed');
      if (scenario.reset === 'http') return route.fulfill({ status: 503, json: { detail: { reason: 'Disk unavailable' } } });
      const key = `${url.searchParams.get('provider')}::${url.searchParams.get('base_url')}::${url.searchParams.get('model')}`;
      delete scenario.probes[key];
      return route.fulfill({ json: { triple: key, removed: true } });
    }
    if (request.method() === 'GET') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation during picker action proof' } });
  });
  await page.goto('/');
  await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
  await page.locator('#settingsBtn').click();
  return scenario;
}

test('keyboard and pointer model-picker interactions share discovery and can retry a failed query', async ({ page }) => {
  const scenario = await app(page);
  scenario.holdModels = true;
  const picker = page.locator('#lmModel');
  await picker.focus();
  await picker.press('ArrowDown');
  await picker.click();
  await page.keyboard.press('Escape');
  await expect.poll(() => scenario.heldModels.length).toBe(1);
  expect(scenario.modelCalls).toBe(1);
  await scenario.heldModels[0].fulfill({ status: 503, json: { detail: 'Discovery temporarily unavailable' } });
  await expect(page.locator('#modelOptionsStatus')).toContainText('503');
  await expect(picker).toHaveValue('saved-response');

  await picker.press('ArrowDown');
  await expect.poll(() => scenario.heldModels.length).toBe(2);
  await scenario.heldModels[1].fulfill({ json: models });
  await expect(page.locator('#modelOptionsStatus')).toContainText('Loaded 2 response models');
  await expect(picker).toHaveValue('saved-response');
  expect(await picker.locator('option').evaluateAll(options => options.map(option => option.value)))
    .toEqual(['saved-response', 'other-response']);
  await picker.click();
  await page.keyboard.press('Escape');
  await expect.poll(() => scenario.modelCalls).toBe(2);
  expect(scenario.errors).toEqual([]);
});

test('probe-reset HTTP and network failures stay visible and retry succeeds without an uncaught error', async ({ page }) => {
  const scenario = await app(page, 'llm');
  await page.locator('#sttLlmModel').focus();
  const reset = page.locator('#clearProbeBtn');
  await expect(reset).toBeVisible();
  await reset.click();
  await expect(page.locator('#settingsActionStatus')).toContainText('HTTP 503: Disk unavailable');
  await expect(reset).toBeEnabled();
  await expect(reset).toBeVisible();
  expect(scenario.probes[triple('audio-a')]).toBeTruthy();

  scenario.reset = 'network';
  await reset.click();
  await expect(page.locator('#settingsActionStatus')).toContainText('Audio probe reset failed: Failed to fetch');
  await expect(reset).toBeEnabled();
  scenario.reset = 'success';
  await reset.click();
  await expect(page.locator('#settingsActionStatus')).toContainText('Audio capability probe reset. Try transcription again.');
  await expect(reset).toBeHidden();
  expect(scenario.probes[triple('audio-a')]).toBeUndefined();
  expect(scenario.probes[triple('audio-b')]).toBeTruthy();
  expect(scenario.errors).toEqual([]);
});

test('an old reset response cannot hide the newly selected probe or replace a newer diagnostic', async ({ page }) => {
  const scenario = await app(page, 'llm');
  await page.locator('#sttLlmModel').focus();
  const reset = page.locator('#clearProbeBtn');
  await expect(reset).toBeVisible();
  scenario.reset = 'hold';
  await reset.click();
  await expect.poll(() => Boolean(scenario.heldReset)).toBe(true);
  await expect(reset).toBeDisabled();
  await page.locator('#sttLlmModel').selectOption('audio-b');
  await page.evaluate(() => { document.getElementById('settingsActionStatus').textContent = 'Newer settings diagnostic.'; });
  await scenario.heldReset.fulfill({ json: { triple: triple('audio-a'), removed: true } });
  await expect(reset).toBeEnabled();
  await expect(reset).toBeVisible();
  await expect(reset).toHaveAttribute('data-model', 'audio-b');
  await expect(page.locator('#settingsActionStatus')).toHaveText('Newer settings diagnostic.');
  expect(scenario.probes[triple('audio-b')]).toBeTruthy();
  expect(scenario.errors).toEqual([]);
});

test('selecting another model releases its reset control before the old DELETE completes', async ({ page }) => {
  const scenario = await app(page, 'llm');
  await page.locator('#sttLlmModel').focus();
  const reset = page.locator('#clearProbeBtn');
  await expect(reset).toBeVisible();
  scenario.reset = 'hold';
  await reset.click();
  await expect.poll(() => Boolean(scenario.heldReset)).toBe(true);
  const old = scenario.heldReset;
  await page.locator('#sttLlmModel').selectOption('audio-b');
  await expect(reset).toBeEnabled();
  await expect(page.locator('#settingsActionStatus')).toHaveText('');
  await reset.click();
  await expect.poll(() => scenario.heldReset !== old).toBe(true);
  const fresh = scenario.heldReset;
  await old.fulfill({ json: { triple: triple('audio-a'), removed: true } });
  await expect(reset).toBeDisabled();
  await expect(page.locator('#settingsActionStatus')).toHaveText('Resetting audio capability probe…');
  await fresh.fulfill({ status: 503, json: { detail: 'Current reset failed' } });
  await expect(reset).toBeEnabled();
  await expect(page.locator('#settingsActionStatus')).toContainText('Current reset failed');
  expect(scenario.probes[triple('audio-b')]).toBeTruthy();
  expect(scenario.errors).toEqual([]);
});

test('changing an endpoint aborts a held probe GET and a later picker action can discover models', async ({ page }) => {
  const scenario = await app(page, 'llm');
  scenario.probeRead = 'hold';
  await page.locator('#sttLlmModel').focus();
  await expect.poll(() => scenario.heldProbeReads.length).toBe(1);
  expect(scenario.modelCalls).toBe(0);
  await page.locator('#sttLlmBaseUrl').fill('http://new-audio.fixture/v1');
  scenario.probeRead = 'success';
  await page.locator('#sttLlmBaseUrl').press('Tab');
  await expect.poll(() => scenario.abortedProbeReads).toBe(1);
  await expect(page.locator('#modelOptionsStatus')).toContainText('Loaded 2 response models');
  await page.locator('#sttLlmModel').focus();
  await page.locator('#sttLlmModel').press('ArrowDown');
  await page.evaluate(() => window.loadModelOptions());
  await expect(page.locator('#modelOptionsStatus')).toContainText('Loaded 2 response models');
  expect(scenario.modelCalls).toBe(1);
  expect(scenario.errors).toEqual([]);
});

test('confirmed Reset releases before optional probe discovery and retains other cached choices', async ({ page }) => {
  const scenario = await app(page, 'llm');
  await page.locator('#sttLlmModel').focus();
  const reset = page.locator('#clearProbeBtn');
  await expect(reset).toBeVisible();
  scenario.reset = 'success'; scenario.probeRead = 'hold';
  await reset.click();
  await expect.poll(() => scenario.heldProbeReads.length).toBe(1);
  await expect(page.locator('#settingsActionStatus')).toContainText('Audio capability probe reset.');
  await expect(reset).toBeEnabled();
  await page.locator('#sttLlmModel').selectOption('audio-b');
  await expect(reset).toBeVisible();
  await expect(reset).toBeEnabled();
  await expect(reset).toHaveAttribute('data-model', 'audio-b');
  expect(await page.evaluate(async () => {
    const { state } = await import('/js/state.js');
    return Object.keys(state.audioUnsupported);
  })).toEqual([triple('audio-b')]);
  scenario.probeRead = 'success';
  await scenario.heldProbeReads[0].fulfill({ json: { entries: scenario.probes } });
  await page.evaluate(() => window.loadModelOptions());
  await expect(page.locator('#modelOptionsStatus')).toContainText('Loaded 2 response models');
  expect(scenario.errors).toEqual([]);
});

test('probe cache failure keeps Reset available and a confirmed empty retry clears it', async ({ page }) => {
  const scenario = await app(page, 'llm');
  await page.locator('#sttLlmModel').focus();
  const reset = page.locator('#clearProbeBtn');
  await expect(reset).toBeVisible();
  scenario.probeRead = 'http';
  await page.evaluate(() => window.loadModelOptions(true));
  await expect(page.locator('#modelOptionsStatus')).toContainText('Audio probe cache unavailable; previous entries retained.');
  await expect(reset).toBeVisible(); await expect(reset).toBeEnabled();
  scenario.probeRead = 'success'; scenario.probes = {};
  await page.evaluate(() => window.loadModelOptions());
  await expect(page.locator('#modelOptionsStatus')).not.toContainText('cache unavailable');
  await expect(reset).toBeHidden();
  expect(scenario.errors).toEqual([]);
});
