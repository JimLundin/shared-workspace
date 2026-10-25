import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  timeout: 20000,
  fullyParallel: true,
  use: { baseURL: 'http://127.0.0.1:1420', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run dev', url: 'http://127.0.0.1:1420', reuseExistingServer: !process.env.CI },
});
