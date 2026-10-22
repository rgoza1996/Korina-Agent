import { test, expect } from '@playwright/test';

// Exercise native select semantics using production HTML/modules. Other boot
// work is disabled; model responses below are deterministic catalog fixtures.
async function catalogPage(page, payload) {
  const mutations = [];
  const current = { payload };
  await page.route('**/js/app.js', route => route.fulfill({ contentType: 'text/javascript', body: '' }));
  await page.route('**/api/**', route => {
    const endpoint = new URL(route.request().url()).pathname;
    if (endpoint === '/api/audio-probe') return route.fulfill({ json: { entries: {} } });
    if (endpoint === '/api/models') {
      return typeof current.payload === 'string'
        ? route.fulfill({ contentType: 'application/json', body: current.payload })
        : route.fulfill({ json: current.payload });
    }
    if (endpoint === '/api/agent/models') return route.fulfill({ json: { ok: true, provider: 'fixture', models: ['other-agent'] } });
    mutations.push(endpoint);
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation' } });
  });
  await page.goto('/');
  await expect(page.locator('#lmModel')).toBeAttached();
  await page.evaluate(() => {
    document.getElementById('llmProvider').value = 'openai-compatible';
    document.getElementById('sttLlmProvider').value = 'openai-compatible';
  });
  return { current, mutations };
}

test('old-server large catalogs are bounded before option/cache construction and retain a late selection', async ({ page }) => {
  const ids = Array.from({ length: 40000 }, (_, index) => `model-${index}`);
  const fixture = await catalogPage(page, { llm_models: [...ids, 'x'.repeat(513)], stt_llm_models: ids });
  const result = await page.evaluate(async selected => {
    const { state } = await import('/js/state.js');
    const { loadModelOptions } = await import('/js/providers-ui.js');
    const { collectConfig } = await import('/js/settings-ui.js');
    const select = document.getElementById('lmModel');
    select.add(new Option(selected, selected)); select.value = selected;
    state.capabilityFilterOverride = true;
    await loadModelOptions(true);
    return { responseCount: select.options.length, sttCount: document.getElementById('sttLlmModel').options.length,
      config: collectConfig(), cacheBytes: JSON.stringify(state.lastModelsPayload).length,
      status: document.getElementById('modelOptionsStatus').textContent };
  }, ids.at(-1));
  expect(result.responseCount).toBe(512);
  expect(result.sttCount).toBe(513);
  expect(result.config.lm_model).toBe(ids.at(-1));
  expect(result.config.stt_llm_model).toBe('');
  expect(result.cacheBytes).toBeLessThan(100000);
  expect(result.status).toMatch(/limited.*Invalid model IDs/i);
  expect(fixture.mutations).toEqual([]);
});

test('unlisted saved choices survive local fallback and malformed/oversize refreshes', async ({ page }) => {
  const fixture = await catalogPage(page, { llm_models: [], stt_llm_models: [],
    llama_cpp_local_models: ['/fixture/other.gguf'],
    stt_llm_models_capabilities: { '/fixture/other.gguf': { supports_audio_input: true } } });
  await page.evaluate(async () => {
    const { loadModelOptions, loadAgentModelOptions } = await import('/js/providers-ui.js');
    for (const [id, value] of [['lmModel', 'saved-response'], ['sttLlmModel', 'saved-audio'], ['agentModel', 'saved-agent']]) {
      const select = document.getElementById(id); select.add(new Option(value, value)); select.value = value;
    }
    await loadModelOptions(true);
    await loadAgentModelOptions();
  });
  for (const [id, expected] of [['lmModel', 'saved-response'], ['sttLlmModel', 'saved-audio'], ['agentModel', 'saved-agent']]) {
    await expect(page.locator(`#${id}`)).toHaveValue(expected);
    expect(await page.locator(`#${id} option:checked`).getAttribute('data-unlisted')).toBe('true');
  }
  const before = await page.evaluate(async () => JSON.stringify((await import('/js/state.js')).state.lastModelsPayload));
  for (const broken of ['{malformed', 'x'.repeat(8 * 1024 * 1024 + 1),
    { llm_models: ['other'], stt_llm_models: [], llm_models_capabilities: { other: [] } },
    { llm_models: ['other'], stt_llm_models: [], llm_models_status: { other: 'bad' } },
  ]) {
    fixture.current.payload = broken;
    await page.evaluate(async () => (await import('/js/providers-ui.js')).loadModelOptions(true));
    await expect(page.locator('#lmModel')).toHaveValue('saved-response');
    await expect(page.locator('#sttLlmModel')).toHaveValue('saved-audio');
    expect(await page.evaluate(async () => JSON.stringify((await import('/js/state.js')).state.lastModelsPayload))).toBe(before);
    await expect(page.locator('#modelOptionsStatus')).toContainText('load failed');
  }
  fixture.current.payload = { llm_models: ['retried'], stt_llm_models: [], catalog_info: {
    metadata: { truncated: true, incomplete: true, visited_entries: 10000 },
  } };
  await page.evaluate(async () => (await import('/js/providers-ui.js')).loadModelOptions());
  await expect(page.locator('#modelOptionsStatus')).not.toContainText('load failed');
  await expect(page.locator('#modelOptionsStatus')).toContainText('mmproj capability estimates are incomplete');
  await expect(page.locator('#lmModel')).toHaveValue('saved-response');
  expect(fixture.mutations).toEqual([]);
});
