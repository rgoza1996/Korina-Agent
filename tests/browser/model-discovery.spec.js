import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';

async function picker(page) {
  // Load production modules into a native DOM without app boot/polling, so
  // fixture requests can be completed in a deterministic order.
  await page.goto('/js/state.js');
  await page.setContent(`
    <input id="llmProvider" value="openai-compatible"><input id="llmBaseUrl" value="http://old.example/v1">
    <input id="llmApiKeyEnv"><input id="sttLlmProvider" value="openai-compatible">
    <input id="sttLlmBaseUrl" value="http://old-audio.example/v1"><input id="sttLlmApiKeyEnv">
    <select id="sttModel"><option>base.en</option></select>
    <select id="lmModel"><option>saved-response</option></select><select id="sttLlmModel"><option value=""></option></select>
    <input id="agentProvider" value="openai-compatible"><input id="agentBaseUrl" value="http://old-agent.example/v1">
    <input id="agentApiKey"><select id="agentModel"><option>saved-agent</option></select>
    <p id="modelOptionsStatus"></p><p id="settingsActionStatus"></p><p id="sttStatus"></p>`);
  await page.evaluate(async () => {
    window.pickers = await import('/js/providers-ui.js');
    window.pickerState = (await import('/js/state.js')).state;
  });
  await page.route('**/api/audio-probe', route => route.fulfill({ json: { entries: {} } }));
}
function payload(tag) {
  return { whisper_models: ['base.en'], llm_models: [`${tag}-response`],
    stt_llm_models: [`${tag}-audio`],
    stt_llm_models_capabilities: { [`${tag}-audio`]: { supports_audio_input: true } } };
}

test('native response/STT pickers and cache retain the newest discovery when requests finish out of order', async ({ page }) => {
  await picker(page);
  const requests = [];
  await page.route('**/api/models?**', route => { requests.push(route); });
  await page.evaluate(() => { window.oldDiscovery = window.pickers.loadModelOptions(true); });
  await expect.poll(() => requests.length).toBe(1);
  await page.evaluate(() => {
    document.getElementById('llmBaseUrl').value = 'http://new.example/v1';
    document.getElementById('sttLlmBaseUrl').value = 'http://new-audio.example/v1';
    window.newDiscovery = window.pickers.loadModelOptions(true);
  });
  await expect.poll(() => requests.length).toBe(2);
  await requests[1].fulfill({ json: payload('new') });
  await page.evaluate(() => window.newDiscovery);
  await requests[0].fulfill({ json: payload('old') });
  await page.evaluate(() => window.oldDiscovery);
  await expect(page.locator('#lmModel')).toHaveValue('saved-response');
  expect(await page.locator('#lmModel option').evaluateAll(options => options.map(option => option.value)))
    .toEqual(['new-response', 'saved-response']);
  await expect(page.locator('#lmModel option[value="saved-response"]')).toHaveAttribute('data-unlisted', 'true');
  expect(await page.locator('#sttLlmModel option').evaluateAll(options => options.map(option => option.value))).toEqual(['', 'new-audio']);
  expect(await page.evaluate(() => window.pickerState.lastModelsPayload)).toEqual(payload('new'));
  expect(new URLSearchParams(await page.evaluate(() => window.pickerState.lastModelsQuery)).get('llm_base_url')).toBe('http://new.example/v1');
});

test('native agent picker rejects old discovery and preserves a selection made during the current request', async ({ page }) => {
  await picker(page);
  const requests = [];
  await page.route('**/api/agent/models', route => { requests.push(route); });
  await page.evaluate(() => { window.oldDiscovery = window.pickers.loadAgentModelOptions(); });
  await expect.poll(() => requests.length).toBe(1);
  await page.evaluate(() => {
    document.getElementById('agentBaseUrl').value = 'http://new-agent.example/v1';
    const choice = new Option('New selection', 'new-choice');
    document.getElementById('agentModel').append(choice);
    window.newDiscovery = window.pickers.loadAgentModelOptions();
    document.getElementById('agentModel').value = 'new-choice';
  });
  await expect.poll(() => requests.length).toBe(2);
  await requests[1].fulfill({ json: { ok: true, models: ['new-choice'], provider: 'new-agent' } });
  await page.evaluate(() => window.newDiscovery);
  await requests[0].fulfill({ json: { ok: true, models: ['old-choice'], provider: 'old-agent' } });
  await page.evaluate(() => window.oldDiscovery);
  await expect(page.locator('#agentModel')).toHaveValue('new-choice');
  expect(await page.locator('#agentModel option').evaluateAll(options => options.map(option => option.value))).toEqual(['', 'new-choice']);
  await expect(page.locator('#modelOptionsStatus')).toContainText('new-agent');
});

test('an HTTP model discovery failure stays visible and the next ordinary picker request retries', async ({ page }) => {
  await picker(page);
  let calls = 0;
  await page.route('**/api/models?**', route => {
    calls++;
    return calls === 1 ? route.fulfill({ status: 503, json: { detail: 'Provider unavailable' } })
      : route.fulfill({ json: payload('recovered') });
  });
  await page.evaluate(() => window.pickers.loadModelOptions());
  await expect(page.locator('#modelOptionsStatus')).toContainText('failed: HTTP 503');
  await expect(page.locator('#lmModel')).toHaveValue('saved-response');
  expect(await page.evaluate(() => window.pickerState.lastModelsPayload)).toBeNull();
  await page.evaluate(() => window.pickers.loadModelOptions());
  expect(calls).toBe(2);
  await expect(page.locator('#lmModel')).toHaveValue('saved-response');
  expect(await page.locator('#lmModel option').evaluateAll(options => options.map(option => option.value)))
    .toEqual(['recovered-response', 'saved-response']);
  expect(await page.evaluate(() => window.pickerState.lastModelsPayload)).toEqual(payload('recovered'));
});

test('agent discovery uses the current form through the real backend while a settings save is pending', async ({ page, request }) => {
  const upstream = [];
  const provider = createServer((incoming, response) => {
    upstream.push({ path: incoming.url, authorization: incoming.headers.authorization });
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ data: [{ id: incoming.url === '/new/v1/models' ? 'new-model' : 'old-model' }] }));
  });
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve));
  const providerBase = `http://127.0.0.1:${provider.address().port}`;
  const originalResponse = await request.get('/api/config');
  expect(originalResponse.ok()).toBeTruthy();
  const original = await originalResponse.json();
  const savedValues = {
    agent_provider: 'openai-compatible', agent_base_url: `${providerBase}/old/v1`,
    agent_api_key: '', agent_model: 'saved-agent',
  };
  let pendingSave;
  let releaseSave;
  const saveBarrier = new Promise(resolve => { releaseSave = resolve; });
  try {
    expect((await request.post('/api/config', { data: savedValues })).ok()).toBeTruthy();
    await picker(page);
    await page.evaluate(async saved => {
      window.pickerState.appConfig = saved;
      window.settingsApi = await import('/js/api.js');
      document.getElementById('agentBaseUrl').value = saved.agent_base_url;
      window.settingsApi.initializeSettingsForm();
    }, { ...original, ...savedValues });
    await page.route('**/api/config', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      pendingSave = route.request();
      await saveBarrier;
      return route.fulfill({ status: 503, json: { detail: 'Held save was not persisted' } });
    });
    const discoveryRequests = [];
    const activationRequests = [];
    page.on('request', incoming => {
      if (new URL(incoming.url()).pathname === '/api/agent/models') discoveryRequests.push(incoming);
      if (new URL(incoming.url()).pathname === '/api/llm/provider/activate') activationRequests.push(incoming);
    });
    await page.evaluate(base => {
      document.getElementById('agentBaseUrl').value = `${base}/new/v1`;
      document.getElementById('agentApiKey').value = 'candidate-browser-key';
      window.settingsApi.saveConfigSoon();
    }, providerBase);
    await expect.poll(() => Boolean(pendingSave)).toBeTruthy();
    await page.evaluate(() => window.pickers.loadAgentModelOptions());

    await expect(page.locator('#agentModel')).toHaveValue('saved-agent');
    expect(await page.locator('#agentModel option').evaluateAll(options => options.map(option => option.value)))
      .toEqual(['', 'new-model', 'saved-agent']);
    expect(upstream).toEqual([{ path: '/new/v1/models', authorization: 'Bearer candidate-browser-key' }]);
    expect(discoveryRequests).toHaveLength(1);
    expect(new URL(discoveryRequests[0].url()).pathname + new URL(discoveryRequests[0].url()).search).toBe('/api/agent/models');
    expect(discoveryRequests[0].method()).toBe('POST');
    expect(discoveryRequests[0].postDataJSON()).toEqual({
      agent_provider: 'openai-compatible', agent_base_url: `${providerBase}/new/v1`,
      agent_api_key: 'candidate-browser-key', llm_base_url: 'http://old.example/v1', llm_api_key_env: '',
    });
    expect((await (await request.get('/api/config')).json()).agent_base_url).toBe(`${providerBase}/old/v1`);
    expect(activationRequests).toHaveLength(0);
  } finally {
    releaseSave();
    if (pendingSave) await expect(page.locator('#settingsActionStatus')).toContainText('Held save was not persisted');
    await request.post('/api/config', {
      data: Object.fromEntries(Object.keys(savedValues).map(key => [key, original[key]])),
    });
    provider.closeAllConnections();
    await new Promise(resolve => provider.close(resolve));
  }
});
