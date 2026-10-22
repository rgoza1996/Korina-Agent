import { test, expect } from '@playwright/test';

async function app(page) {
  const scenario = { controls: [], resets: 0, offBarrier: null, failOff: false };
  const config = { agent_enabled: 'on', ack_enabled: 'off', stt_backend: 'whisper',
    llm_provider: 'openai-compatible', llm_base_url: 'http://fixture.test/v1', lm_model: 'demo',
    agent_provider: 'openai-compatible', agent_base_url: 'http://fixture.test/v1', agent_model: 'demo',
    agent_first_delivery_mode: 'timer', agent_first_delivery_seconds: 10000, agent_interrupts_enabled: 'off' };
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    let body = {};
    if (url.pathname === '/api/config') {
      if (request.method() === 'POST') {
        const payload = request.postDataJSON();
        if (Object.hasOwn(payload, 'agent_enabled')) scenario.controls.push(payload.agent_enabled);
        if (payload.agent_enabled === 'off') {
          if (scenario.offBarrier) await scenario.offBarrier;
          if (scenario.failOff) return route.fulfill({ status: 503, json: { detail: 'Disk unavailable' } });
        }
        Object.assign(config, payload);
      }
      body = { ...config };
    } else if (url.pathname === '/api/agent/events') body = { events: [], last_event_id: 41 };
    else if (url.pathname === '/api/agent/models') body = { ok: true, provider: 'openai-compatible', models: [] };
    else if (url.pathname === '/api/models/roots') body = { roots: [] };
    else if (url.pathname === '/api/acks') body = { acks: [] };
    else if (url.pathname === '/api/agent/status') body = { busy: false };
    else if (url.pathname === '/api/agent/reset') { scenario.resets++; body = { ok: true }; }
    else if (url.pathname === '/api/converse/channels') body = { channels: ['korina'] };
    else if (url.pathname === '/api/converse/channel') body = { channel: 'korina' };
    else if (url.pathname === '/api/health') body = { ok: true, whisper_available: true,
      audio_limits: { max_upload_bytes: 52428800, max_duration_seconds: 300 },
      tts: { ok: false, status: 'unverified' } };
    await route.fulfill({ json: body });
  });
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('page server…', { timeout: 15000 });
  await page.locator('#settingsBtn').click();
  await page.locator('[data-settings-tab="agent"]').click();
  return scenario;
}

test('rapid native checkbox off/on persists cancellation and preserves conversation state', async ({ page }) => {
  const scenario = await app(page);
  let release;
  scenario.offBarrier = new Promise(resolve => { release = resolve; });
  const session = await page.evaluate(() => {
    window.state.history = [{ role: 'user', content: 'Keep the conversation.' }];
    window.state.pendingAgentStateReport = 'Discard pending agent report.';
    window.state.awaitingAgentPermission = { id: 5 };
    window.state.deferredAgentInterrupt = { message: 'Discard interruption.' };
    return window.state.agentSession;
  });
  await page.locator('#agentEnabled').uncheck();
  await expect.poll(() => scenario.controls.length).toBe(1);
  await page.locator('#agentEnabled').check();
  expect(await page.evaluate(() => window.state.agentActivityBlocked)).toBe(true);
  release();
  await expect(page.locator('#settingsActionStatus')).toContainText('enabled with fresh delivery context');
  expect(scenario.controls).toEqual(['off', 'on']);
  expect(scenario.resets).toBe(0);
  expect(await page.evaluate(() => ({ session: window.state.agentSession, history: window.state.history,
    report: window.state.pendingAgentStateReport, permission: window.state.awaitingAgentPermission,
    deferred: window.state.deferredAgentInterrupt, cursor: window.state.agentEventCursor }))).toEqual({
    session, history: [{ role: 'user', content: 'Keep the conversation.' }], report: '', permission: null, deferred: null, cursor: 41,
  });
});

test('failed disable stays blocked and retries off before accepting on', async ({ page }) => {
  const scenario = await app(page);
  scenario.failOff = true;
  await page.locator('#agentEnabled').uncheck();
  await expect(page.locator('#settingsActionStatus')).toContainText('Disk unavailable');
  expect(await page.evaluate(() => window.state.agentActivityBlocked)).toBe(true);
  scenario.failOff = false;
  await page.locator('#agentEnabled').check();
  await expect(page.locator('#settingsActionStatus')).toContainText('enabled with fresh delivery context');
  expect(scenario.controls).toEqual(['off', 'off', 'on']);
  expect(scenario.resets).toBe(0);
});

test('ordinary chat already awaiting a response completes after the agent is disabled', async ({ page }) => {
  await app(page);
  let pending;
  await page.route('**/api/chat', route => { pending = route; });
  const result = page.evaluate(() => window.askLM('Keep normal conversation working.'));
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.locator('#agentEnabled').uncheck();
  await expect(page.locator('#settingsActionStatus')).toContainText('disabled. Conversation can continue');
  await pending.fulfill({ json: { reply: 'Normal conversation continues.' } });
  expect(await result).toBe('Normal conversation continues.');
});
