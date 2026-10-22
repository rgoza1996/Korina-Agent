import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  outputDir: process.env.KORINA_BROWSER_TEST_OUTPUT || 'test-results',
  reporter: 'list',
  // Activate the Python environment before invoking the browser suite.
  webServer: process.env.KORINA_TEST_BASE_URL ? undefined : {
    command: 'python3 -m korina.app',
    env: { KORINA_HOST: '127.0.0.1', KORINA_PORT: '8765' },
    url: 'http://127.0.0.1:8765/api/health',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  use: {
    baseURL: process.env.KORINA_TEST_BASE_URL || 'http://127.0.0.1:8765',
    browserName: 'chromium',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
