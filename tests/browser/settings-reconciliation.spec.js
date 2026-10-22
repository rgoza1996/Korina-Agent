import { test, expect } from '@playwright/test';

const initial = { ack_enabled: 'off', agent_enabled: 'off', llm_provider: 'openai-compatible',
  llm_base_url: 'http://response.fixture/v1', lm_model: 'response-a', stt_backend: 'llm',
  stt_llm_provider: 'openai-compatible', stt_llm_base_url: 'http://response.fixture/v1', stt_llm_model: 'response-a',
  agent_provider: 'openai-compatible', agent_base_url: 'http://response.fixture/v1', agent_model: 'response-a' };
let restore = null;

test.afterEach(async ({ request }) => {
  if (restore) {
    const response = await request.post('/api/config', { data: restore });
    expect(response.ok()).toBeTruthy();
    restore = null;
  }
});

async function app(page, request) {
  const original = await (await request.get('/api/config')).json();
  restore = Object.fromEntries(Object.keys(initial).map(key => [key, original[key]]));
  expect((await request.post('/api/config', { data: initial })).ok()).toBeTruthy();
  const scenario = { posts: [], activations: 0, outgoingModels: [], errors: [], hold: false, held: false, release: null };
  page.on('pageerror', error => scenario.errors.push(error.message));
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname === '/api/config') {
      if (req.method() === 'POST') {
        const patch = req.postDataJSON(); scenario.posts.push(patch);
        if (scenario.hold && patch.lm_model === 'response-b') {
          scenario.hold = false;
          const response = await route.fetch(); // Real backend normalization/persistence.
          scenario.held = true;
          await new Promise(resolve => { scenario.release = resolve; });
          return route.fulfill({ response });
        }
      }
      return route.continue();
    }
    if (url.pathname === '/api/health') return route.fulfill({ json: { ok: true, whisper_available: true } });
    if (url.pathname === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (url.pathname === '/api/agent/models') return route.fulfill({ json: { ok: true, models: ['response-a', 'response-b'] } });
    if (url.pathname === '/api/models') return route.fulfill({ json: {
      llm_models: ['response-a', 'response-b'], stt_llm_models: ['response-a', 'response-b', 'independent-audio'],
      stt_llm_models_capabilities: Object.fromEntries(['response-a', 'response-b', 'independent-audio']
        .map(id => [id, { supports_audio_input: true }])),
    } });
    if (url.pathname === '/api/transcribe/partial') {
      scenario.outgoingModels.push(url.searchParams.get('llm_model'));
      return route.fulfill({ json: { text: '', backend: 'llm', model: url.searchParams.get('llm_model'), seconds: 0, duration: 0 } });
    }
    if (url.pathname === '/api/llm/provider/activate') {
      scenario.activations++;
      return route.fulfill({ status: 500, json: { detail: 'A settings save must not activate providers' } });
    }
    return route.continue();
  });
  await page.goto('/');
  await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
  await page.evaluate(() => window.loadModelOptions(true));
  return scenario;
}

async function nextPartial(page) {
  await page.evaluate(async () => {
    const { state } = await import('/js/state.js');
    const { transcribePartialBlob } = await import('/js/recorder.js');
    await transcribePartialBlob(new Blob(['fixture response only; no audio decoder/model']), state.partialSeq);
  });
}

test('confirmed derived models update native controls and the next STT request without activation', async ({ page, request }) => {
  const scenario = await app(page, request);
  // Exercise the public save API against the real config route; model
  // discovery and the final transcription response are the only fixtures.
  await page.evaluate(async () => {
    document.getElementById('lmModel').value = 'response-b';
    await window.saveConfigNow();
  });
  await expect(page.locator('#sttLlmModel')).toHaveValue('response-b');
  await expect(page.locator('#agentModel')).toHaveValue('response-b');
  const saved = await (await request.get('/api/config')).json();
  expect(saved.stt_llm_model).toBe('response-b'); expect(saved.agent_model).toBe('response-b');
  await nextPartial(page);
  expect(scenario.outgoingModels).toEqual(['response-b']);
  expect(scenario.posts).toEqual([{ lm_model: 'response-b' }]);
  expect(scenario.activations).toBe(0);
  expect(await page.evaluate(async () => (await import('/js/state.js')).state.appConfigSnapshot.lm_model)).toBe('response-a');
  expect(scenario.errors).toEqual([]);
});

test('a newer explicit STT edit survives a pending response save and remains the next request model', async ({ page, request }) => {
  const scenario = await app(page, request);
  scenario.hold = true;
  await page.evaluate(() => {
    document.getElementById('lmModel').value = 'response-b';
    window.pendingDerivedSave = window.saveConfigNow();
  });
  await expect.poll(() => scenario.held).toBe(true);
  try {
    await page.evaluate(() => {
      document.getElementById('sttLlmModel').value = 'independent-audio';
      void window.saveConfigNow();
    });
  } finally { scenario.release(); }
  await page.evaluate(() => window.pendingDerivedSave);
  await expect(page.locator('#sttLlmModel')).toHaveValue('independent-audio');
  await expect(page.locator('#agentModel')).toHaveValue('response-b');
  expect((await (await request.get('/api/config')).json()).stt_llm_model).toBe('independent-audio');
  await nextPartial(page);
  expect(scenario.outgoingModels).toEqual(['independent-audio']);
  expect(scenario.posts).toEqual([{ lm_model: 'response-b' }, { lm_model: 'response-b', stt_llm_model: 'independent-audio' }]);
  expect(scenario.activations).toBe(0);
  expect(scenario.errors).toEqual([]);
});
