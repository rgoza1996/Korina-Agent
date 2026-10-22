import { test, expect } from '@playwright/test';

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
    <p id="modelOptionsStatus"></p>`);
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
  await expect(page.locator('#lmModel')).toHaveValue('new-response');
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
  await expect(page.locator('#lmModel')).toHaveValue('recovered-response');
});
