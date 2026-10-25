import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../iroh-probe/node_modules/playwright/index.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const origin = 'https://browser-host-lifetime.invalid';
const browser = await chromium.launch({ headless: true });
const observations = [];
let failure;
try {
  const context = await browser.newContext();
  await context.route(`${origin}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const headers = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };
    if (path === '/') return route.fulfill({ contentType: 'text/html', headers, body: '<!doctype html><title>Offline endpoint lifetime</title>' });
    const local = resolve(root, '.' + path);
    if (!local.startsWith(root)) return route.abort();
    await route.fulfill({ body: await readFile(local), headers, contentType: extname(path) === '.wasm' ? 'application/wasm' : 'text/javascript' });
  });
  const page = await context.newPage();
  await page.goto(origin);
  await page.evaluate(async () => {
    window.module = await import('/pkg/browser_workspace_host.js');
    await window.module.default();
    window.seed = Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) => byte.toString(16).padStart(2, '0')).join('');
  });
  // The actual browser network is offline before binding. No online-state fake
  // or transport substitution can resolve the endpoint's online watcher.
  await context.setOffline(true);
  await page.evaluate(async () => {
    window.endpoint = await window.module.BrowserEndpoint.start(window.seed, async () => '{}');
    window.addressStatus = 'pending';
    window.addressError = undefined;
    window.address = window.endpoint.address().then(
      () => { window.addressStatus = 'fulfilled'; },
      (error) => { window.addressStatus = 'rejected'; window.addressError = error.message; },
    );
  });
  assert.equal(await page.evaluate(() => window.addressStatus), 'pending');
  await page.evaluate(async () => {
    await window.endpoint.close();
    window.endpoint.free();
    window.endpoint = undefined;
  });
  await page.waitForFunction(() => window.addressStatus === 'rejected', undefined, { timeout: 3_000 });
  assert.equal(await page.evaluate(() => window.addressError), 'endpoint is closed');
  observations.push('Offline address wait is cancelled promptly by close; immediate free has no borrowed-object error');
  await page.evaluate(async () => {
    window.endpoint = await window.module.BrowserEndpoint.start(window.seed, async () => '{}');
    window.addressStatus = 'pending';
    window.addressError = undefined;
    window.address = window.endpoint.address().then(
      () => { window.addressStatus = 'fulfilled'; },
      (error) => { window.addressStatus = 'rejected'; window.addressError = error.message; },
    );
    window.endpoint.free();
    window.endpoint = undefined;
  });
  await page.waitForFunction(() => window.addressStatus === 'rejected', undefined, { timeout: 3_000 });
  assert.equal(await page.evaluate(() => window.addressError), 'endpoint is closed');
  observations.push('Dropping an endpoint before its address future runs also retires the owned wait safely');
} catch (error) { failure = error.stack ?? String(error); }
finally {
  await browser.close();
  const report = {
    status: failure ? 'failed' : 'passed', timestamp: new Date().toISOString(),
    environment: 'Actual Chromium context switched offline after static WASM assets load',
    source_sha256: createHash('sha256').update(await readFile(resolve(root, 'src/transport.rs'))).digest('hex'),
    wasm_sha256: createHash('sha256').update(await readFile(resolve(root, 'pkg/browser_workspace_host_bg.wasm'))).digest('hex'),
    observations, ...(failure ? { failure } : {}),
  };
  await mkdir(resolve(root, 'evidence'), { recursive: true });
  await writeFile(resolve(root, 'evidence/lifetime.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  if (failure) process.exitCode = 1;
}
