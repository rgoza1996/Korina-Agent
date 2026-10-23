import { test, expect } from '@playwright/test';

function pcmEvent(seconds = 3) {
  const rate = 24000, pcm = Buffer.alloc(seconds * rate * 2);
  for (let index = 0; index < pcm.length / 2; index++) {
    pcm.writeInt16LE(Math.round(1200 * Math.sin(index * 2 * Math.PI * 440 / rate)), index * 2);
  }
  return `data: ${JSON.stringify({ audio: pcm.toString('base64'), sample_rate: rate })}\n\n`;
}

async function ready(page, chat, permission) {
  const errors = [], speech = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__typedSources = [];
    const Context = window.AudioContext || window.webkitAudioContext;
    const create = Context.prototype.createBufferSource;
    Context.prototype.createBufferSource = function (...args) {
      const source = create.apply(this, args), record = { stopped: 0, ended: false };
      window.__typedSources.push(record);
      const stop = source.stop.bind(source);
      source.stop = (...parameters) => { record.stopped++; return stop(...parameters); };
      source.addEventListener('ended', () => { record.ended = true; });
      return source;
    };
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    if (endpoint === '/api/chat') return chat(route);
    if (endpoint === '/api/agent/permission-answer' && permission) return permission(route);
    if (endpoint === '/api/config' && request.method() === 'GET') {
      const config = await (await route.fetch()).json();
      return route.fulfill({ json: { ...config, ack_enabled: 'off', agent_enabled: 'off', mode: 'sse',
        llm_provider: 'openai-compatible', lm_model: 'fixture-model', tts_base_url: 'http://speech.fixture' } });
    }
    if (endpoint === '/api/health') return route.fulfill({ json: { ok: true, whisper_available: true,
      whisper_loaded: true, response_llm_load: { ok: true, loaded: true } } });
    if (endpoint === '/api/acks') return route.fulfill({ json: { acks: [] } });
    if (endpoint === '/api/models') return route.fulfill({ json: { llm_models: ['fixture-model'],
      stt_llm_models: ['fixture-model'], whisper_models: ['base.en'] } });
    if (endpoint === '/api/agent/models') return route.fulfill({ json: { ok: true, models: [] } });
    if (endpoint === '/api/agent/events') return route.fulfill({ json: { events: [], last_event_id: 0 } });
    return route.continue();
  });
  await page.route('http://speech.fixture/stream/speech', route => {
    speech.push(route.request().postDataJSON().input);
    return route.fulfill({ contentType: 'text/event-stream', body: pcmEvent() });
  });
  await page.goto('/');
  await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
  await expect(page.locator('#ttsBaseUrl')).toHaveValue('http://speech.fixture');
  return { errors, speech };
}

async function ask(page, message) {
  await page.locator('#text').fill(message);
  await page.locator('#askBtn').click();
}

test('a newer typed Ask retires the held reply without replacing its current text, history, or native speech', async ({ page }) => {
  const requests = [];
  let release, retiredFinished = false, aborted = false;
  const held = new Promise(resolve => { release = resolve; });
  page.on('requestfailed', request => { if (new URL(request.url()).pathname === '/api/chat') aborted = true; });
  const fixture = await ready(page, async route => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    if (requests.length === 1) {
      await held;
      await route.fulfill({ json: { reply: 'Retired first reply.' } });
      retiredFinished = true;
      return;
    }
    return route.fulfill({ json: { reply: 'Current second reply.' } });
  });
  try {
    await ask(page, 'First typed turn.');
    await expect.poll(() => requests.length).toBe(1);
    await ask(page, 'Second typed turn.');
    await expect.poll(() => aborted).toBe(true);
    await expect(page.locator('#text')).toHaveValue('Current second reply.');
    await expect.poll(() => page.evaluate(() => window.state.activeSources.length)).toBe(1);
    release();
    await expect.poll(() => retiredFinished).toBe(true);
    await expect(page.locator('#text')).toHaveValue('Current second reply.');
    expect(await page.evaluate(() => window.state.history)).toEqual([
      { role: 'user', content: 'First typed turn.' }, { role: 'user', content: 'Second typed turn.' },
      { role: 'assistant', content: 'Current second reply.' },
    ]);
    await expect(page.locator('#log')).not.toContainText('Retired first reply.');
    expect(fixture.speech).toEqual(['Current second reply.']);
    expect(await page.evaluate(() => window.__typedSources)).toEqual([{ stopped: 0, ended: false }]);
    await expect(page.locator('#askBtn')).toBeEnabled();
    await page.locator('#stopBtn').click();
    expect(fixture.errors).toEqual([]);
  } finally { release(); }
});

test('retired failures cannot hide a current error, and the next typed Ask can retry', async ({ page }) => {
  let release, oldFinished = false, calls = 0;
  const held = new Promise(resolve => { release = resolve; });
  const fixture = await ready(page, async route => {
    calls++;
    if (calls === 1) {
      await held;
      await route.fulfill({ status: 500, json: { detail: 'Retired failure' } });
      oldFinished = true;
      return;
    }
    if (calls === 2) return route.fulfill({ status: 503, json: { detail: 'Current service unavailable' } });
    return route.fulfill({ json: { reply: 'Retry succeeded.' } });
  });
  try {
    await ask(page, 'Old question.');
    await expect.poll(() => calls).toBe(1);
    await ask(page, 'Current question.');
    await expect(page.locator('#sttStatus')).toContainText('LM/TTS error: Current service unavailable');
    release();
    await expect.poll(() => oldFinished).toBe(true);
    await expect(page.locator('#sttStatus')).toContainText('LM/TTS error: Current service unavailable');
    await ask(page, 'Retry current question.');
    await expect(page.locator('#text')).toHaveValue('Retry succeeded.');
    expect(fixture.speech).toEqual(['Retry succeeded.']);
    await page.locator('#stopBtn').click();
    expect(fixture.errors).toEqual([]);
  } finally { release(); }
});

test('an older typed cleanup cannot drop the owner of another pending Ask', async ({ page }) => {
  const releases = [], finished = [];
  let calls = 0, aborted = 0;
  page.on('requestfailed', request => { if (new URL(request.url()).pathname === '/api/chat') aborted++; });
  const fixture = await ready(page, async route => {
    const index = calls++;
    if (index < 2) {
      await new Promise(resolve => { releases[index] = resolve; });
      await route.fulfill({ json: { reply: `Retired reply ${index}.` } });
      finished.push(index);
      return;
    }
    return route.fulfill({ json: { reply: 'Latest third reply.' } });
  });
  try {
    await ask(page, 'First question.');
    await expect.poll(() => calls).toBe(1);
    await ask(page, 'Second question.');
    await expect.poll(() => aborted).toBe(1);
    await expect.poll(() => calls).toBe(2);
    await ask(page, 'Third question.');
    await expect.poll(() => aborted).toBe(2);
    await expect(page.locator('#text')).toHaveValue('Latest third reply.');
    releases.forEach(release => release());
    await expect.poll(() => finished.length).toBe(2);
    expect(await page.evaluate(() => window.state.history.filter(turn => turn.role === 'assistant'))).toEqual([
      { role: 'assistant', content: 'Latest third reply.' },
    ]);
    expect(fixture.speech).toEqual(['Latest third reply.']);
    await page.locator('#stopBtn').click();
    expect(fixture.errors).toEqual([]);
  } finally { releases.forEach(release => release()); }
});

test('Stop Live leaves a current typed Ask connected', async ({ page }) => {
  let release, entered = false;
  const held = new Promise(resolve => { release = resolve; });
  const fixture = await ready(page, async route => {
    entered = true;
    await held;
    return route.fulfill({ json: { reply: 'Typed reply after Stop Live.' } });
  });
  try {
    await ask(page, 'Typed question.');
    await expect.poll(() => entered).toBe(true);
    await page.evaluate(() => window.stopLive());
    release();
    await expect(page.locator('#text')).toHaveValue('Typed reply after Stop Live.');
    await expect.poll(() => fixture.speech).toEqual(['Typed reply after Stop Live.']);
    await page.locator('#stopBtn').click();
    expect(fixture.errors).toEqual([]);
  } finally { release(); }
});

test('repeated typed yes coalesces confirmation once, clears the question, and permits a new Ask during its speech', async ({ page }) => {
  let release, answers = 0, chats = 0;
  const held = new Promise(resolve => { release = resolve; });
  const fixture = await ready(page, route => {
    chats++;
    return route.fulfill({ json: { reply: 'Next ordinary reply.' } });
  }, async route => {
    answers++;
    await held;
    return route.fulfill({ json: { ok: true, accepted: true } });
  });
  try {
    await page.evaluate(() => {
      document.getElementById('agentEnabled').checked = true;
      window.state.awaitingAgentPermission = { id: 71, report: 'Permission request: continue?' };
      window.state.history = [];
      window.state.agentTurnCount = 0;
    });
    await ask(page, 'yes');
    await expect.poll(() => answers).toBe(1);
    await page.locator('#askBtn').click();
    release();
    await expect.poll(() => page.evaluate(() => window.state.awaitingAgentPermission)).toBeNull();
    expect(answers).toBe(1);
    expect(await page.evaluate(() => window.state.history)).toEqual([
      { role: 'user', content: 'Permission answer to Korina Agent: yes' },
      { role: 'assistant', content: 'Okay. I told Korina Agent it has permission.' },
    ]);
    await expect.poll(() => page.evaluate(() => window.state.ttsSpeaking)).toBe(true);
    await ask(page, 'Next ordinary question.');
    await expect(page.locator('#text')).toHaveValue('Next ordinary reply.');
    expect(chats).toBe(1);
    expect(fixture.speech).toEqual(['Okay. I told Korina Agent it has permission.', 'Next ordinary reply.']);
    await page.locator('#stopBtn').click();
    expect(fixture.errors).toEqual([]);
  } finally { release(); }
});
