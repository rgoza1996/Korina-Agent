import { test, expect } from '@playwright/test';

test('a browser chat sends prior history separately from the current interrupted user turn', async ({ page }) => {
  const errors = [];
  const chats = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request();
    if (new URL(request.url()).pathname === '/api/chat') {
      chats.push(request.postDataJSON());
      // This contract check deliberately avoids model inference and playback.
      return route.fulfill({ json: { reply: '' } });
    }
    if (request.method() === 'GET') return route.continue();
    return route.fulfill({ status: 409, json: { detail: 'Unexpected mutation in chat-history check' } });
  });
  await page.goto('/');
  await expect(page.locator('#pageHealth')).not.toHaveText('');
  const history = await page.evaluate(async () => {
    const { state } = await import('/js/state.js');
    const { askAndSpeak } = await import('/js/agent-ui.js');
    document.getElementById('agentEnabled').checked = false;
    state.history = [
      { role: 'user', content: 'The earlier question.' },
      { role: 'assistant', content: 'The earlier spoken answer.' },
    ];
    state.pendingAgentStateReport = 'The background task is ready.';
    await askAndSpeak('Change direction now.', 'The user interrupted halfway through the reply.');
    return state.history;
  });

  expect(chats).toHaveLength(1);
  expect(chats[0].history).toEqual([
    { role: 'user', content: 'The earlier question.' },
    { role: 'assistant', content: 'The earlier spoken answer.' },
  ]);
  expect(chats[0].message).toContain('Injection: Korina Agent state report');
  expect(chats[0].message).toContain('The user interrupted halfway');
  expect(chats[0].message.match(/Change direction now\./g)).toHaveLength(1);
  expect(history.at(-2)).toEqual({ role: 'user', content: 'Change direction now.' });
  expect(errors).toEqual([]);
});
