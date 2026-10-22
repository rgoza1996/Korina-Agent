import { test, expect } from '@playwright/test';
import path from 'node:path';

async function mockProviders(page, request) {
  const config = {
    ...await (await request.get('/api/config')).json(),
    stt_backend: 'whisper', ack_enabled: 'off', llm_provider: 'openai-compatible',
    llm_base_url: 'http://llm.example/v1', lm_model: 'demo-response', llm_reasoning: 'off',
    stt_llm_provider: '', stt_llm_base_url: '', stt_llm_model: '',
    agent_enabled: 'off', agent_model: 'saved-agent-model',
    agent_provider: 'openai-compatible', agent_base_url: 'http://agent.example/v1',
  };
  const capabilities = await (await request.get('/api/capabilities')).json();
  const scenario = { config, saves: [], activations: [], activeChannel: 'korina', failSave: false, delaySave: 0, activeSaves: 0, maximumActiveSaves: 0, rebuilds: 0, delayActivation: 0, resets: 0, polls: 0, deliveries: 0, failReset: false, resetBarrier: null, agentEvents: [] };
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname;
    let body;
    let status = 200;
    if (endpoint === '/api/config' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      scenario.saves.push(payload);
      scenario.activeSaves++;
      scenario.maximumActiveSaves = Math.max(scenario.maximumActiveSaves, scenario.activeSaves);
      if (scenario.delaySave) await new Promise(resolve => setTimeout(resolve, scenario.delaySave));
      scenario.activeSaves--;
      if (scenario.failSave) { status = 503; body = { detail: 'Disk unavailable for settings' }; }
      else { Object.assign(config, payload); body = { ...config }; }
    } else if (endpoint === '/api/config') body = config;
    else if (endpoint === '/api/capabilities') body = capabilities;
    else if (endpoint === '/api/models') body = {
      whisper_models: ['base.en', 'tiny.en'], llm_models: ['demo-response', 'next-response'],
      stt_llm_models: ['audio-model'], stt_llm_models_capabilities: { 'audio-model': { supports_audio_input: true } },
      llm_base_url: config.llm_base_url, stt_llm_base_url: config.stt_llm_base_url,
    };
    else if (endpoint === '/api/agent/models') body = { ok: true, models: [], provider: 'openai-compatible' };
    else if (endpoint === '/api/models/roots') body = { roots: [] };
    else if (endpoint === '/api/audio-probe') body = { entries: {} };
    else if (endpoint === '/api/acks') body = { acks: [] };
    else if (endpoint === '/api/acks/rebuild') { scenario.rebuilds++; body = { voice: config.voice, removed: 0, queued_or_missing: 0 }; }
    else if (endpoint === '/api/agent/events') { scenario.polls++; body = { events: scenario.agentEvents }; }
    else if (endpoint === '/api/agent/transcript') { scenario.deliveries++; body = { ok: true, accepted: true }; }
    else if (endpoint === '/api/agent/reset') {
      scenario.resets++;
      if (scenario.resetBarrier) await scenario.resetBarrier;
      if (scenario.failReset) { status = 503; body = { detail: 'Reset backend unavailable' }; }
      else { scenario.agentEvents = []; body = { ok: true }; }
    }
    else if (endpoint === '/api/agent/status') body = { busy: false };
    else if (endpoint === '/api/converse/channels') body = { channels: ['korina', 'testing'] };
    else if (endpoint === '/api/converse/channel') body = { channel: scenario.activeChannel };
    else if (endpoint.startsWith('/api/converse/channel/')) {
      scenario.activeChannel = decodeURIComponent(endpoint.split('/').at(-1)); body = { ok: true, channel: scenario.activeChannel };
    } else if (endpoint === '/api/llm/provider/activate') {
      const payload = request.postDataJSON();
      scenario.activations.push(payload);
      config.llm_provider = payload.provider; config.lm_model = payload.model;
      const saved = { ...config };
      if (scenario.delayActivation) await new Promise(resolve => setTimeout(resolve, scenario.delayActivation));
      body = { ok: true, saved, activation: { ...payload, started: [], stopped: [] } };
    } else if (endpoint === '/api/health') body = {
      ok: true, whisper_available: true, whisper_loaded: false, stt_backend: config.stt_backend,
      whisper_backend: 'faster-whisper', whisper_device: 'cpu', whisper_compute_type: 'int8',
      response_llm_provider: config.llm_provider, response_llm_model: config.lm_model,
      response_llm_base_url: config.llm_base_url,
      response_llm_load: { ok: true, loaded: true, provider: config.llm_provider, loaded_model: config.lm_model },
      multimodal_stt_load: { ok: true, loaded: true },
      tts: { ok: false, loaded: false, provider: 'openai-compatible', base_url: 'http://tts.example/v1', status: 'unverified', probe_supported: false },
    };
    else { status = 404; body = { detail: `Unexpected browser-test endpoint ${endpoint}` }; }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return scenario;
}

async function waitForBoot(page) {
  await expect(page.locator('#pageHealth')).not.toHaveText('');
  await expect(page.locator('#settingsBtn')).toBeEnabled();
}

async function loadResponseModels(page) {
  await page.evaluate(() => window.loadModelOptions(true));
  await expect(page.locator('#lmModel option[value="next-response"]')).toHaveCount(1);
}

test('real backend boots, lists channels, and closes settings without runtime errors', async ({ page }) => {
  const errors = [];
  const blocked = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET') return route.continue();
    blocked.push(route.request().url());
    return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'Mutation blocked during real-backend smoke' }) });
  });
  await page.goto('/');
  await waitForBoot(page);
  await page.locator('#settingsBtn').click();
  await expect(page.locator('#settingsModal')).toHaveClass(/open/);
  await page.locator('[data-settings-tab="channel"]').click();
  await expect(page.locator('#channelSelect')).toBeEnabled();
  await expect(page.locator('#channelSelect option[value="korina"]')).toHaveCount(1);
  await page.screenshot({ path: process.env.KORINA_QA_SCREENSHOT || path.resolve('../korina-browser-qa.png'), fullPage: true });
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  expect(errors).toEqual([]);
  expect(blocked.filter(url => url.includes('/provider/activate'))).toEqual([]);
});

test('settings preserve a saved agent model when discovery returns no models', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click();
  await page.locator('[data-settings-tab="agent"]').click();
  await expect(page.locator('#agentModel')).toHaveValue('saved-agent-model');
  await page.locator('[data-settings-tab="channel"]').click();
  await page.locator('#channelSelect').selectOption('testing');
  await expect(page.locator('#channelSelectHint')).toContainText('Selection is saved');
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  expect(scenario.activations).toEqual([]);
  expect(scenario.config.agent_model).toBe('saved-agent-model');
  expect(errors).toEqual([]);
});

test('a failed settings save closes the modal, shows the error, and does not activate a provider', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click(); await loadResponseModels(page);
  scenario.failSave = true;
  await page.locator('#lmModel').selectOption('next-response');
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  await expect(page.locator('#sttStatus')).toContainText('Disk unavailable for settings');
  expect(scenario.activations).toEqual([]);
  expect(scenario.config.lm_model).toBe('demo-response');
});

test('response model edits activate once on close and remain stable on a second close', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click(); await loadResponseModels(page);
  await page.locator('#lmModel').selectOption('next-response');
  await page.locator('#closeSettingsBtn').click();
  await expect.poll(() => scenario.activations.length).toBe(1);
  expect(scenario.activations[0]).toEqual({ provider: 'openai-compatible', model: 'next-response' });
  await page.locator('#settingsBtn').click();
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  expect(scenario.activations).toHaveLength(1);
});

test('STT-only settings never activate or change the response provider', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click();
  await page.locator('#sttBackend').selectOption('llm');
  await page.locator('#sttLlmProvider').selectOption('openai-compatible');
  await page.locator('#sttLlmBaseUrl').fill('http://audio.example/v1');
  await page.locator('#sttLlmBaseUrl').dispatchEvent('change');
  await expect(page.locator('#sttLlmModel option[value="audio-model"]')).toHaveCount(1);
  await page.locator('#sttLlmModel').selectOption('audio-model');
  await page.locator('#closeSettingsBtn').click();
  await expect.poll(() => scenario.config.stt_llm_model).toBe('audio-model');
  expect(scenario.config.llm_provider).toBe('openai-compatible');
  expect(scenario.config.llm_base_url).toBe('http://llm.example/v1');
  expect(scenario.config.lm_model).toBe('demo-response');
  expect(scenario.activations).toEqual([]);
});

test('saves stay serialized while rapid edits and modal close overlap', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click(); await loadResponseModels(page);
  scenario.delaySave = 200;
  await page.locator('#lmModel').selectOption('next-response');
  await expect.poll(() => scenario.activeSaves).toBe(1);
  await page.locator('#speed').evaluate(element => { element.value = '1.2'; element.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  await expect.poll(() => scenario.activations.length).toBe(1);
  expect(scenario.maximumActiveSaves).toBe(1);
  expect(scenario.config.speed).toBe(1.2);
});

test('unverified custom TTS has a warning pill and no runtime errors', async ({ page, request }) => {
  await mockProviders(page, request);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/'); await waitForBoot(page);
  await expect(page.locator('#ttsHealth')).toContainText('OpenAI TTS health unverified');
  await expect(page.locator('#ttsDot')).toHaveClass('dot warn');
  expect(errors).toEqual([]);
});

test('speech settings preserve a saved provider and round trip its custom health URL', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  scenario.config.tts_provider = 'saved-compatible-service';
  scenario.config.tts_health_url = 'http://tts.example/old-health';
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click();
  await expect(page.locator('#ttsProvider')).toHaveValue('saved-compatible-service');
  await expect(page.locator('#ttsHealthUrl')).toHaveValue('http://tts.example/old-health');
  await page.locator('#ttsHealthUrl').fill('http://tts.example/status');
  await page.locator('#closeSettingsBtn').click();
  await expect.poll(() => scenario.config.tts_health_url).toBe('http://tts.example/status');
  expect(scenario.config.tts_provider).toBe('saved-compatible-service');
  expect(scenario.activations).toEqual([]);
  expect(errors).toEqual([]);
});

test('changing voice with acknowledgements disabled does not enqueue synthesis', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#voice').selectOption('af_bella');
  await expect.poll(() => scenario.config.voice).toBe('af_bella');
  expect(scenario.rebuilds).toBe(0);
  await expect(page.locator('#sttStatus')).toContainText('Acknowledgement phrases are disabled');
});

test('a late provider activation cannot overwrite edits made after settings reopen', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click(); await loadResponseModels(page);
  scenario.delayActivation = 500;
  await page.locator('#lmModel').selectOption('next-response');
  await page.locator('#closeSettingsBtn').click();
  await expect.poll(() => scenario.activations.length).toBe(1);
  await page.locator('#settingsBtn').click();
  await page.locator('#lmModel').selectOption('demo-response');
  await expect(page.locator('#lmModel')).toHaveValue('demo-response');
  await page.locator('#closeSettingsBtn').click();
  await expect.poll(() => scenario.activations.length).toBe(2);
  await expect(page.locator('#lmModel')).toHaveValue('demo-response');
  expect(scenario.activations.map(request => request.model)).toEqual(['next-response', 'demo-response']);
});

test('configuration banner stays stable while model and runtime statuses update independently', async ({ page, request }) => {
  await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  await page.locator('#settingsBtn').click();
  const banner = await page.locator('#settingsInfo').textContent();
  expect(banner).toBe('Loaded saved config.json settings.');
  await loadResponseModels(page);
  await expect(page.locator('#modelOptionsStatus')).toContainText('Loaded 2 response models');
  await expect(page.locator('#settingsInfo')).toHaveText(banner);
  await expect(page.locator('#settingsLiveStatus')).toContainText('LLM up · STT up');
  await page.locator('#sttBackend').selectOption('llm');
  await expect(page.locator('#settingsInfo')).toHaveText(banner);
  await expect(page.locator('#modelOptionsStatus')).toContainText('Multimodal STT selected');
});

test('agent delivery and polling stay paused while a backend reset is pending', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  let release;
  scenario.resetBarrier = new Promise(resolve => { release = resolve; });
  await page.evaluate(() => { document.getElementById('agentEnabled').checked = true; });
  await page.locator('#clearSessionBtn').click();
  await expect.poll(() => scenario.resets).toBe(1);
  scenario.agentEvents = [{ id: 50, type: 'state_report', priority: 'normal', report: 'Stale backend report.' }];
  try {
    await page.evaluate(async () => {
      window.state.history = [{ role: 'user', content: 'A new conversation can continue.' }];
      await window.pollAgentEvents();
      await window.deliverTranscriptToAgent('test');
    });
    expect(scenario.polls).toBe(0);
    expect(scenario.deliveries).toBe(0);
    await expect.poll(() => page.evaluate(() => window.state.agentResetBlocked)).toBe(true);
    expect(await page.evaluate(() => window.state.pendingAgentStateReport)).toBe('');
  } finally { release(); }
  await expect.poll(() => page.evaluate(() => window.state.agentResetBlocked)).toBe(false);
  await page.evaluate(() => window.deliverTranscriptToAgent('after-reset'));
  expect(scenario.deliveries).toBe(1);
});

test('a failed backend reset stays blocked until Clear Session succeeds on retry', async ({ page, request }) => {
  const scenario = await mockProviders(page, request);
  await page.goto('/'); await waitForBoot(page);
  scenario.failReset = true;
  await page.evaluate(() => { document.getElementById('agentEnabled').checked = true; });
  await page.locator('#clearSessionBtn').click();
  await expect(page.locator('#clearSessionBtn')).toHaveText('Retry Clear Session');
  await expect(page.locator('#sttStatus')).toContainText('Reset backend unavailable');
  await expect(page.locator('#sttStatus')).toContainText('Retry Clear Session');
  await page.evaluate(async () => { await window.pollAgentEvents(); await window.deliverTranscriptToAgent('test'); });
  expect(scenario.polls).toBe(0);
  expect(scenario.deliveries).toBe(0);
  expect(await page.evaluate(() => window.state.agentResetBlocked)).toBe(true);
  scenario.failReset = false;
  await page.locator('#clearSessionBtn').click();
  await expect(page.locator('#clearSessionBtn')).toHaveText('Clear Session');
  await expect.poll(() => page.evaluate(() => window.state.agentResetBlocked)).toBe(false);
  expect(scenario.resets).toBe(2);
  await expect(page.locator('#settingsActionStatus')).not.toContainText('Agent reset failed');
});
