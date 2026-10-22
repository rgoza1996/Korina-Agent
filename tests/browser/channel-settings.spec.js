import { test, expect } from '@playwright/test';

async function openChannel(page) {
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => window.state?.settingsBootstrap?.loaded)).toBe(true);
  await page.locator('#settingsBtn').click();
  await page.locator('[data-settings-tab="channel"]').click();
}

test('the native Channel picker ignores an older active-channel reply after a newer load', async ({ page }) => {
  let reads = 0, release;
  const old = new Promise(resolve => { release = resolve; });
  await page.route('**/api/converse/channels', route => route.fulfill({ json: { channels: ['korina', 'testing'] } }));
  await page.route('**/api/converse/channel', async route => {
    const first = ++reads === 1;
    if (first) await old;
    await route.fulfill({ json: { channel: first ? 'korina' : 'testing' } });
  });
  try {
    await page.goto('/');
    await expect.poll(() => page.evaluate(() => window.state?.settingsBootstrap?.loaded)).toBe(true);
    await page.locator('#settingsBtn').click();
    await page.evaluate(() => {
      window.testOldChannelLoad = import('/js/channel-ui.js').then(module => module.populateChannelTab());
    });
    await expect.poll(() => reads).toBe(1);
    await page.locator('[data-settings-tab="channel"]').click();
    await expect(page.locator('#channelSelect')).toHaveValue('testing');
    release();
    await page.evaluate(() => window.testOldChannelLoad);
    expect(reads).toBe(2);
    await expect(page.locator('#channelSelectHint')).toContainText('Active: testing');
    await expect(page.locator('#channelSelect')).toHaveValue('testing');
  } finally { release(); }
});

test('a native channel switch stays disabled until confirmed and shows a failed save for retry', async ({ page }) => {
  let release, fail = true, posts = 0;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/converse/channels', route => route.fulfill({ json: { channels: ['korina', 'testing'] } }));
  await page.route('**/api/converse/channel', route => route.fulfill({ json: { channel: 'korina' } }));
  await page.route('**/api/converse/channel/testing', async route => {
    posts++;
    if (fail) {
      await pending;
      await route.fulfill({ status: 503, json: { detail: 'Selection disk unavailable' } });
    } else await route.fulfill({ json: { ok: true, channel: 'testing' } });
  });
  try {
    await openChannel(page);
    await expect(page.locator('#channelSelect')).toBeEnabled();
    await page.locator('#channelSelect').selectOption('testing');
    await expect(page.locator('#channelSelect')).toBeDisabled();
    await expect(page.locator('#channelSelectHint')).toContainText('Switching to: testing');
    release();
    await expect(page.locator('#channelSelectHint')).toContainText('Selection disk unavailable');
    await expect(page.locator('#channelSelect')).toBeEnabled();
    expect(posts).toBe(1);
    fail = false;
    await page.route('**/api/converse/channel', route => route.fulfill({ json: { channel: 'testing' } }));
    await page.locator('#channelSelect').selectOption('testing');
    await expect(page.locator('#channelSelectHint')).toContainText('Active: testing');
    expect(posts).toBe(2);
  } finally { release(); }
});

test('Escape and backdrop close native Settings without changing saved provider state', async ({ page }) => {
  const mutations = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', route => {
    if (route.request().method() !== 'GET' && !route.request().url().endsWith('/api/agent/models')) {
      mutations.push(route.request().url());
      return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation' } });
    }
    return route.continue();
  });
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => window.state?.settingsBootstrap?.loaded)).toBe(true);
  await page.locator('#settingsBtn').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  await page.locator('#settingsBtn').click();
  await page.locator('#settingsModal').click({ position: { x: 3, y: 3 } });
  await expect(page.locator('#settingsModal')).not.toHaveClass(/open/);
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
});
