import { test, expect } from '@playwright/test';

test('failed permission confirmation retains the question for retry and accepted retry preserves a newer question', async ({ page }) => {
  const errors = [];
  const answers = [];
  let confirmRetry;
  const retryBarrier = new Promise(resolve => { confirmRetry = resolve; });
  let speechRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/agent/permission-answer', async route => {
    answers.push(route.request().postDataJSON());
    if (answers.length === 1) return route.fulfill({ status: 503, json: { detail: 'Agent unavailable for permission confirmation' } });
    await retryBarrier;
    return route.fulfill({ json: { ok: true, accepted: true, queued_as: 'permission_answer' } });
  });
  await page.route('**/api/chat', route => route.fulfill({ status: 409, json: { detail: 'Permission answers must not start a chat request' } }));
  for (const endpoint of ['**/v1/audio/speech', '**/stream/speech']) {
    await page.route(endpoint, route => {
      speechRequests++;
      return route.fulfill({ status: 409, body: 'Unexpected old permission speech' });
    });
  }
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('page server…', { timeout: 15000 });
  await page.evaluate(() => {
    document.getElementById('agentEnabled').checked = true;
    window.state.awaitingAgentPermission = { id: 71, report: 'Permission request: continue?' };
    window.state.history = [];
    window.state.agentTurnCount = 0;
    document.getElementById('text').value = 'yes';
  });
  await page.locator('#askBtn').click();
  await expect(page.locator('#sttStatus')).toContainText('Permission answer failed: Agent unavailable');
  expect(await page.evaluate(() => window.state.awaitingAgentPermission.id)).toBe(71);
  expect(await page.evaluate(() => window.state.history)).toEqual([]);
  expect(await page.evaluate(() => window.state.agentTurnCount)).toBe(0);
  await expect(page.locator('#text')).toHaveValue('yes');
  await expect(page.locator('#log')).not.toContainText('I told Korina Agent');

  await page.locator('#askBtn').click();
  await expect.poll(() => answers.length).toBe(2);
  await page.evaluate(() => {
    window.state.awaitingAgentPermission = { id: 72, report: 'Permission request: a newer question?' };
    document.getElementById('text').value = 'New permission question.';
  });
  confirmRetry();
  await expect.poll(() => page.evaluate(() => window.state.agentTurnCount)).toBe(1);
  expect(await page.evaluate(() => window.state.awaitingAgentPermission.id)).toBe(72);
  expect(await page.evaluate(() => window.state.history)).toEqual([
    { role: 'user', content: 'Permission answer to Korina Agent: yes' },
  ]);
  await expect(page.locator('#text')).toHaveValue('New permission question.');
  expect(answers[1]).toEqual(answers[0]);
  expect(speechRequests).toBe(0);
  expect(errors).toEqual([]);
});
