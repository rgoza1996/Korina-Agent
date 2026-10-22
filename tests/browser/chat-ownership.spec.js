import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';

async function backend(phase) {
  // The browser job activates the installed Python environment before running.
  const child = spawn('python3', [fileURLToPath(new URL('./fixtures/chat_owner_server.py', import.meta.url)), phase], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '', diagnostic = '';
  child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-8192); });
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error(`Chat fixture startup timed out: ${diagnostic}`)); }, 15000);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Chat fixture exited ${code}: ${diagnostic}`)); });
    child.stdout.on('data', chunk => {
      output += chunk;
      for (const line of output.split('\n')) {
        try {
          const ready = JSON.parse(line);
          if (ready.base_url) { clearTimeout(timer); resolve(ready.base_url); return; }
        } catch { /* Other process diagnostics are not readiness. */ }
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

for (const phase of ['headers', 'body']) {
  test(`Clear closes held provider ${phase} through the real backend and reuses its sole worker`, async ({ page, request }) => {
    const server = await backend(phase);
    const errors = [];
    let aborted = false;
    page.on('pageerror', error => errors.push(error.message));
    page.on('requestfailed', request => { if (new URL(request.url()).pathname === '/api/chat') aborted = true; });
    await page.route('**/api/health', route => route.fulfill({ json: { ok: true, whisper_loaded: true,
      whisper_available: true, response_llm_load: { ok: true, loaded: true } } }));
    try {
      await page.goto(server.base);
      await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
      await page.locator('#text').fill('Old turn.');
      await page.locator('#askBtn').click();
      const stats = async () => (await request.get(`${server.base}/test-chat-owner`)).json();
      await expect.poll(async () => (await stats()).entered).toBe(true);
      await page.locator('#clearSessionBtn').click();
      await expect.poll(async () => (await stats()).peer_closed, { timeout: 1000 }).toBe(true);
      await expect(page.locator('#clearSessionBtn')).toBeEnabled();
      expect(aborted).toBe(true);
      await expect(page.locator('#text')).toHaveValue('');
      expect(await page.evaluate(() => window.state.history)).toEqual([]);
      expect(await page.evaluate(() => window.askLM('Fresh turn.'))).toBe('Fresh reply.');
      const recovered = await stats();
      expect(recovered.workers).toHaveLength(2);
      expect(new Set(recovered.workers).size).toBe(1);
      expect(errors).toEqual([]);
    } finally { await server.close(); }
  });
}
