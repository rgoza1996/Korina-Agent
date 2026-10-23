import { test, expect } from '@playwright/test';

test('Meta dropdowns apply registry presets, preserve blank key controls, and save through the real config route', async ({ page, request }) => {
  const original = await (await request.get('/api/config')).json();
  const errors = [], discovery = [], agents = [], activations = [], chats = [], resets = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    expect((await request.post('/api/config', { data: {
      ack_enabled: 'off', agent_enabled: 'off', stt_backend: 'whisper',
      llm_provider: 'openai-compatible', llm_base_url: 'http://response.fixture/v1',
      lm_model: 'initial-fixture', llm_api_key_env: '', llm_reasoning: 'off',
      llm_reasoning_effort: '', agent_reasoning_effort: '',
      stt_llm_provider: '', stt_llm_base_url: '', stt_llm_model: '', stt_llm_api_key_env: '',
      agent_provider: 'openai-compatible', agent_base_url: 'http://agent.fixture/v1',
      agent_model: 'initial-agent-fixture', agent_api_key: '',
    } })).ok()).toBeTruthy();
    await page.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname === '/api/health') {
        const saved = await (await request.get('/api/config')).json();
        return route.fulfill({ json: { ok: true, whisper_available: true,
          response_llm_provider: saved.llm_provider, response_llm_model: saved.lm_model,
          response_llm_reasoning_effort: saved.llm_reasoning_effort,
          agent_provider: saved.agent_provider, agent_reasoning_effort: saved.agent_reasoning_effort,
        } });
      }
      if (url.pathname === '/api/acks') return route.fulfill({ json: { acks: [] } });
      if (url.pathname === '/api/audio-probe') return route.fulfill({ json: { entries: {} } });
      if (url.pathname === '/api/models') {
        discovery.push(Object.fromEntries(url.searchParams));
        return route.fulfill({ json: { llm_models: ['team-text-fixture'], stt_llm_models: [],
          llm_models_capabilities: { 'team-text-fixture': { supports_audio_input: false, source: 'inferred' } } } });
      }
      if (url.pathname === '/api/agent/models') {
        agents.push(req.postDataJSON());
        return route.fulfill({ json: { ok: true, models: ['team-text-fixture'] } });
      }
      if (url.pathname === '/api/chat') {
        chats.push(req.postDataJSON());
        return route.fulfill({ json: { reply: 'Fixture reply.' } });
      }
      if (url.pathname === '/api/agent/reset') {
        resets.push(req.method());
        return route.fulfill({ json: { ok: true } });
      }
      if (url.pathname === '/api/llm/provider/activate') {
        activations.push(req.postDataJSON());
        const saved = await (await request.get('/api/config')).json();
        return route.fulfill({ json: { ok: true, saved, activation: { started: [], stopped: [] } } });
      }
      // Capabilities and config use the actual backend; outbound provider
      // discovery, health and activation are completely intercepted above.
      return route.continue();
    });
    await page.goto('/');
    await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
    await expect(page.locator('.sub')).not.toContainText('local response');
    await page.locator('#settingsBtn').click();
    await expect(page.locator('#llmReasoningEffortWrap')).toBeHidden();
    await expect(page.locator('#llmReasoningEnabledWrap')).toBeVisible();
    await page.locator('#llmProvider').selectOption('meta');
    await expect(page.locator('#llmBaseUrl')).toHaveValue('https://api.meta.ai/v1');
    await expect(page.locator('#llmBaseUrl')).toBeDisabled();
    await expect(page.locator('#llmApiKeyEnv')).toHaveValue('');
    await expect(page.locator('#llmApiKeyEnv')).toHaveAttribute('placeholder', 'MODEL_API_KEY');
    await expect(page.locator('#llmReasoningEnabled')).toBeDisabled();
    await expect(page.locator('#llmReasoningHint')).toContainText('Meta provider default');
    await expect(page.locator('#llmReasoningEffortWrap')).toBeVisible();
    await expect(page.locator('#llmReasoningEnabledWrap')).toBeHidden();
    expect(await page.locator('#llmReasoningEffort option').evaluateAll(options => options.map(option => option.value)))
      .toEqual(['', 'minimal', 'low', 'medium', 'high', 'xhigh']);
    await page.locator('#llmReasoningEffort').selectOption('minimal');
    await expect(page.locator('#llmReasoningHint')).toContainText('Meta effort minimal');
    await expect.poll(() => discovery.some(query => query.llm_provider === 'meta'
      && query.llm_base_url === 'https://api.meta.ai/v1'
      && query.llm_api_key_env === 'MODEL_API_KEY'
      && query.stt_llm_api_key_env === 'MODEL_API_KEY')).toBe(true);
    await expect(page.locator('#lmModel option[value="team-text-fixture"]')).not.toContainText('no audio');
    await page.locator('#lmModel').selectOption('team-text-fixture');
    await page.locator('#llmMaxTokens').fill('2048');
    await page.evaluate(() => window.saveConfigNow());

    await page.locator('[data-settings-tab="agent"]').click();
    await expect(page.locator('#agentReasoningEffortWrap')).toBeHidden();
    await page.locator('#agentProvider').selectOption('meta');
    await expect(page.locator('#agentBaseUrl')).toHaveValue('https://api.meta.ai/v1');
    await expect(page.locator('#agentBaseUrl')).toBeDisabled();
    await expect(page.locator('#agentApiKey')).toHaveValue('');
    await expect(page.locator('#agentApiKey')).toHaveAttribute('placeholder', /MODEL_API_KEY/);
    await expect(page.locator('#agentApiKey')).toHaveAttribute('placeholder', /same-URL response key, otherwise/);
    await expect(page.locator('#agentReasoningEffortWrap')).toBeVisible();
    expect(await page.locator('#agentReasoningEffort option').evaluateAll(options => options.map(option => option.value)))
      .toEqual(['', 'minimal', 'low', 'medium', 'high', 'xhigh']);
    await page.locator('#agentReasoningEffort').selectOption('high');
    await expect(page.locator('#agentReasoningHint')).toContainText('Meta effort high');
    await expect.poll(() => agents.some(candidate => candidate.agent_provider === 'meta'
      && candidate.agent_base_url === 'https://api.meta.ai/v1')).toBe(true);
    await page.locator('#agentModel').selectOption('team-text-fixture');
    await page.evaluate(() => window.saveConfigNow());
    const saved = await (await request.get('/api/config')).json();
    expect(saved.llm_provider).toBe('meta'); expect(saved.agent_provider).toBe('meta');
    expect(saved.llm_base_url).toBe('https://api.meta.ai/v1'); expect(saved.agent_base_url).toBe('https://api.meta.ai/v1');
    expect(saved.lm_model).toBe('team-text-fixture'); expect(saved.agent_model).toBe('team-text-fixture');
    expect(saved.llm_api_key_env).toBe(''); expect(saved.agent_api_key).toBe('');
    expect(saved.llm_max_tokens).toBe(2048);
    expect(saved.llm_reasoning_effort).toBe('minimal');
    expect(saved.agent_reasoning_effort).toBe('high');
    expect(saved.stt_backend).toBe('whisper');
    // Ask from the current draft before a settings event/autosave can persist
    // it. Explicit blank must likewise override an older saved effort.
    await page.evaluate(async () => {
      document.getElementById('llmReasoningEffort').value = 'xhigh';
      await window.askLM('Use the current response effort and saved token budget.');
    });
    expect(chats[0].reasoning_effort).toBe('xhigh');
    expect(chats[0].max_tokens).toBe(2048);
    await page.evaluate(() => window.health());
    await expect(page.locator('#pageHealth')).toContainText('reasoning effort minimal');
    await expect(page.locator('#settingsLiveStatus')).toContainText('Agent configured with reasoning effort high');
    await page.evaluate(async () => {
      document.getElementById('llmReasoningEffort').value = '';
      await window.askLM('Use model-default response effort.');
    });
    expect(chats).toHaveLength(2);
    expect(chats[1].reasoning_effort).toBe('');
    expect((await (await request.get('/api/config')).json()).llm_reasoning_effort).toBe('minimal');
    await page.locator('#closeSettingsBtn').click();
    await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
    expect(activations).toEqual([{ provider: 'meta', model: 'team-text-fixture' }]);
    await page.locator('#settingsBtn').click();
    await page.locator('[data-settings-tab="converse"]').click();
    await page.locator('#llmReasoningEffort').selectOption('low');
    await page.locator('[data-settings-tab="agent"]').click();
    await page.locator('#agentReasoningEffort').selectOption('');
    await page.locator('#closeSettingsBtn').click();
    await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
    const effortOnly = await (await request.get('/api/config')).json();
    expect(effortOnly.llm_reasoning_effort).toBe('low');
    expect(effortOnly.agent_reasoning_effort).toBe('');
    expect(activations).toHaveLength(1, 'effort-only saves must not activate a provider');
    expect(resets).toEqual([]);
    await page.reload();
    await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
    await page.locator('#settingsBtn').click();
    await expect(page.locator('#llmReasoningEffort')).toHaveValue('low');
    await expect(page.locator('#llmReasoningHint')).toContainText('Meta effort low');
    await page.locator('[data-settings-tab="agent"]').click();
    await expect(page.locator('#agentReasoningEffort')).toHaveValue('');
    expect(errors).toEqual([]);
  } finally {
    expect((await request.post('/api/config', { data: original })).ok()).toBeTruthy();
  }
});
