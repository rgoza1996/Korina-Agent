import { test, expect } from '@playwright/test';

test('response usage stays separate, uses the request snapshot, and Clear retires late observations', async ({ page }) => {
  const errors = [], speech = [], chats = [];
  let releaseFirst, releaseLast, lastFinished = false;
  const first = new Promise(resolve => { releaseFirst = resolve; });
  const last = new Promise(resolve => { releaseLast = resolve; });
  const observed = {
    reply: 'The answer stays separate.',
    usage: { prompt_tokens: 1024, completion_tokens: 64, reasoning_tokens: 32, cached_tokens: 0 },
    request_settings: { provider: 'meta', model: 'fixture-model', max_tokens: 2048,
      reasoning_effort: 'minimal', reasoning: null },
  };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/chat') {
      chats.push(request.postDataJSON());
      if (chats.length === 1) { await first; return route.fulfill({ json: observed }); }
      if (chats.length === 2) return route.fulfill({ json: { reply: 'Older backend reply.' } });
      await last;
      await route.fulfill({ json: observed });
      lastFinished = true;
      return;
    }
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, ack_enabled: 'off', agent_enabled: 'off', mode: 'sse',
        llm_provider: 'meta', lm_model: 'fixture-model', llm_reasoning_effort: 'minimal',
        llm_max_tokens: 2048, tts_base_url: 'http://speech.fixture' } });
    }
    if (endpoint === '/api/health') return route.fulfill({ json: { ok: true, whisper_available: true,
      whisper_loaded: true, response_llm_load: { ok: true, loaded: true } } });
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (endpoint === '/api/models') return route.fulfill({ json: { llm_models: ['fixture-model'],
      stt_llm_models: [], whisper_models: ['base.en'] } });
    if (endpoint === '/api/agent/models') return route.fulfill({ json: { ok: true, models: [] } });
    if (endpoint === '/api/agent/events') return route.fulfill({ json: { events: [], last_event_id: 0 } });
    if (endpoint === '/api/agent/reset') return route.fulfill({ json: { ok: true } });
    return route.continue();
  });
  await page.route('http://speech.fixture/stream/speech', route => {
    speech.push(route.request().postDataJSON().input);
    const audio = Buffer.alloc(24000 * 2 * 2).toString('base64');
    return route.fulfill({ contentType: 'text/event-stream',
      body: `data: ${JSON.stringify({ audio, sample_rate: 24000 })}\n\n` });
  });
  try {
    await page.goto('/');
    await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
    await expect(page.locator('#responseUsageStatus')).toHaveText('No response yet.');
    await page.locator('#text').fill('First question.');
    await page.locator('#askBtn').click();
    await expect.poll(() => chats.length).toBe(1);
    await expect(page.locator('#responseUsageStatus')).toHaveText('Waiting for response…');
    await page.evaluate(() => {
      const model = document.getElementById('lmModel');
      model.add(new Option('New draft', 'new-draft')); model.value = 'new-draft';
      document.getElementById('llmReasoningEffort').value = 'xhigh';
      document.getElementById('llmMaxTokens').value = '8192';
    });
    releaseFirst();
    await expect(page.locator('#responsePromptTokens')).toHaveText('1024');
    await expect(page.locator('#responseCachedTokens')).toHaveText('0');
    await expect(page.locator('#responseUsageSettings')).toHaveText(
      'Provider: meta · Model: fixture-model · Output budget: 2048 · Reasoning effort: minimal');
    expect(chats[0]).toMatchObject({ model: 'fixture-model', max_tokens: 2048, reasoning_effort: 'minimal' });
    await expect(page.locator('#text')).toHaveValue(observed.reply);
    await expect.poll(() => speech.length).toBe(1);
    expect(speech).toEqual([observed.reply]);
    expect(await page.evaluate(() => window.state.history)).toEqual([
      { role: 'user', content: 'First question.' }, { role: 'assistant', content: observed.reply },
    ]);
    await expect(page.locator('#log')).not.toContainText('prompt_tokens');
    await expect(page.locator('#agentDebug')).not.toContainText('1024');
    await page.evaluate(() => window.stopPlayback());
    await expect(page.locator('#responsePromptTokens')).toHaveText('1024');

    expect(await page.evaluate(() => window.askLM('An older backend.'))).toBe('Older backend reply.');
    await expect(page.locator('#responseUsageStatus')).toHaveText('Usage unavailable for this response.');
    await expect(page.locator('#responsePromptTokens')).toHaveText('Unavailable');
    await page.evaluate(() => { window.__usagePending = window.askLM('A request to retire.'); });
    await expect.poll(() => chats.length).toBe(3);
    await page.locator('#clearSessionBtn').click();
    await expect(page.locator('#responseUsageStatus')).toHaveText('No response yet.');
    releaseLast();
    await expect.poll(() => lastFinished).toBe(true);
    expect(await page.evaluate(() => window.__usagePending)).toBeNull();
    await expect(page.locator('#responsePromptTokens')).toHaveText('Unavailable');
    await expect(page.locator('#responseUsageStatus')).toHaveText('No response yet.');
    expect(errors).toEqual([]);
  } finally { releaseFirst(); releaseLast(); }
});
