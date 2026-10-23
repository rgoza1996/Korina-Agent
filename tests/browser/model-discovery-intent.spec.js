import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';

async function webOrigin() {
  const calls = [];
  const server = createServer((request, response) => {
    if (request.url.endsWith('/models')) {
      calls.push({ path: request.url, credential: request.headers.authorization === 'Bearer synthetic-discovery-key' });
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ data: [{ id: 'fixture-model' }] }));
    } else {
      response.writeHead(200, { 'Content-Type': 'text/html' });
      response.end('<!doctype html><title>Separate browser origin</title>');
    }
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  return { base: `http://127.0.0.1:${server.address().port}`, calls, async close() {
    const closed = once(server, 'close');
    server.close(); server.closeAllConnections(); await closed;
  } };
}

async function backend(trustedOrigin) {
  const child = spawn('python3', [fileURLToPath(new URL('./fixtures/model_intent_server.py', import.meta.url)), trustedOrigin], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '', diagnostic = '';
  child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-8192); });
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error(`Model intent fixture timed out: ${diagnostic}`)); }, 15000);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Model intent fixture exited ${code}: ${diagnostic}`)); });
    child.stdout.on('data', chunk => {
      output += chunk;
      for (const line of output.split('\n')) {
        try {
          const ready = JSON.parse(line);
          if (ready.base_url) { clearTimeout(timer); resolve(ready.base_url); return; }
        } catch { /* Process diagnostics are not readiness. */ }
      }
    });
  });
  return { base, async close() {
    if (child.exitCode !== null) return;
    const exited = once(child, 'exit');
    child.kill();
    const force = setTimeout(() => child.kill('SIGKILL'), 5000);
    try { await exited; } finally { clearTimeout(force); }
  } };
}

test('foreign simple GETs cannot forward candidate credentials; a trusted origin can preflight', async ({ page, request }) => {
  const trusted = await webOrigin(), foreign = await webOrigin();
  const server = await backend(trusted.base);
  try {
    await page.goto(foreign.base);
    for (const field of ['llm', 'stt_llm']) {
      const url = `${server.base}/api/models?${new URLSearchParams({
        [`${field}_base_url`]: foreign.base + '/candidate/v1', [`${field}_api_key_env`]: 'FIXTURE_DISCOVERY_KEY',
      })}`;
      expect(await page.evaluate(async url => {
        const response = await fetch(url, { mode: 'no-cors', credentials: 'omit' });
        return response.type;
      }, url)).toBe('opaque');
      expect(await page.evaluate(async url => {
        try { await fetch(url, { headers: { 'X-Korina-Intent': 'model-discovery' } }); return 'unexpected success'; }
        catch (error) { return error.name; }
      }, url)).toBe('TypeError');
    }
    expect(foreign.calls).toEqual([]);
    expect(trusted.calls).toEqual([]);
    const attempts = await (await request.get(`${server.base}/test-discovery-requests`)).json();
    expect(attempts.filter(item => item.method === 'GET')).toHaveLength(2);
    expect(attempts.filter(item => item.method === 'GET').every(item => item.origin === '' && item.intent === '' && item.fetch_mode === 'no-cors')).toBe(true);
    expect(attempts.filter(item => item.method === 'OPTIONS')).toHaveLength(2);

    await page.goto(trusted.base);
    const query = new URLSearchParams({ llm_base_url: trusted.base + '/candidate/v1', llm_api_key_env: 'FIXTURE_DISCOVERY_KEY',
      stt_llm_base_url: trusted.base + '/candidate/v1', stt_llm_api_key_env: 'FIXTURE_DISCOVERY_KEY' });
    expect(await page.evaluate(async url => {
      const response = await fetch(url, { headers: { 'X-Korina-Intent': 'model-discovery' } });
      const result = await response.json();
      return { status: response.status, models: result.llm_models, sttModels: result.stt_llm_models,
        responseError: result.llm_error, sttError: result.stt_llm_error };
    }, `${server.base}/api/models?${query}`)).toEqual({
      status: 200, models: ['fixture-model'], sttModels: ['fixture-model'], responseError: null, sttError: null,
    });
    expect(trusted.calls).toEqual([{ path: '/candidate/v1/models', credential: true }]);
  } finally { await server.close(); await trusted.close(); await foreign.close(); }
});

test('the actual same-origin picker sends intent and retains credentialed response/STT discovery', async ({ page, request }) => {
  const provider = await webOrigin();
  const server = await backend(provider.base);
  try {
    await page.goto(server.base + '/js/state.js');
    await page.setContent(`<input id="llmProvider" value="openai-compatible"><input id="llmBaseUrl" value="${provider.base}/candidate/v1">
      <input id="llmApiKeyEnv" value="FIXTURE_DISCOVERY_KEY"><input id="sttLlmProvider" value="openai-compatible">
      <input id="sttLlmBaseUrl" value="${provider.base}/candidate/v1"><input id="sttLlmApiKeyEnv" value="FIXTURE_DISCOVERY_KEY">
      <select id="sttModel"></select><select id="lmModel"></select><select id="sttLlmModel"><option value=""></option></select>
      <p id="modelOptionsStatus"></p>`);
    await page.evaluate(async () => {
      (await import('/js/state.js')).state.capabilityFilterOverride = true;
      await (await import('/js/providers-ui.js')).loadModelOptions(true);
    });
    await expect(page.locator('#lmModel option[value="fixture-model"]')).toHaveCount(1);
    await expect(page.locator('#sttLlmModel option[value="fixture-model"]')).toHaveCount(1);
    await expect(page.locator('#modelOptionsStatus')).not.toContainText('failed');
    expect(provider.calls).toEqual([{ path: '/candidate/v1/models', credential: true }]);
    const requests = await (await request.get(`${server.base}/test-discovery-requests`)).json();
    expect(requests).toHaveLength(1);
    expect(requests[0].intent).toBe('model-discovery');
    expect(requests[0].method).toBe('GET');
  } finally { await server.close(); await provider.close(); }
});

test('an untyped browser body cannot change saved settings', async ({ page, request }) => {
  const foreign = await webOrigin();
  const server = await backend('http://trusted.test');
  try {
    const before = await (await request.get(`${server.base}/api/config`)).json();
    await page.goto(foreign.base);
    expect(await page.evaluate(async url => {
      const response = await fetch(url, {
        method: 'POST', mode: 'no-cors', credentials: 'omit',
        body: new Blob([JSON.stringify({ speed: 1.75 })]),
      });
      return response.type;
    }, `${server.base}/api/config`)).toBe('opaque');
    const after = await (await request.get(`${server.base}/api/config`)).json();
    expect(after).toEqual(before);
    const rejected = await request.post(`${server.base}/api/config`, { data: Buffer.from('{"speed":1.75}') });
    expect(rejected.status()).toBe(415);
    expect(foreign.calls).toEqual([]);
  } finally { await server.close(); await foreign.close(); }
});
