import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../iroh-probe/node_modules/playwright/index.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const origin = 'https://browser-host-transport.invalid';
const browser = await chromium.launch({ headless: true });
const pages = [];
const observations = [];
const sockets = [];
const browserErrors = [];
let failure;
try {
  for (let index = 0; index < 3; index++) {
    const context = await browser.newContext();
    await context.route(`${origin}/**`, async (route) => {
      const path = new URL(route.request().url()).pathname;
      const headers = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };
      if (path === '/') return route.fulfill({ contentType: 'text/html', headers, body: '<!doctype html><title>Transport acceptance</title>' });
      const local = resolve(root, '.' + path);
      if (!local.startsWith(root)) return route.abort();
      await route.fulfill({ body: await readFile(local), headers,
        contentType: extname(path) === '.wasm' ? 'application/wasm' : 'text/javascript' });
    });
    const page = await context.newPage();
    pages.push(page);
    page.on('pageerror', (error) => browserErrors.push(error.message));
    page.on('websocket', (socket) => sockets.push({ context: index, origin: new URL(socket.url()).origin }));
    await page.goto(origin);
    await page.evaluate(async () => {
      const module = await import('/pkg/browser_workspace_host.js');
      await module.default();
      window.api = module;
      const seed = crypto.getRandomValues(new Uint8Array(32));
      // Caller persistence is exercised separately with Turso by the full host.
      window.seed = Array.from(seed, (byte) => byte.toString(16).padStart(2, '0')).join('');
      window.allowed = undefined;
      window.calls = [];
      window.holdRequests = false;
      window.startEndpoint = async () => {
        window.endpoint = await module.BrowserEndpoint.start(window.seed, async (authenticatedId, payload) => {
          window.calls.push({ authenticatedId, payload });
          if (window.holdRequests) return new Promise(() => {});
          const request = JSON.parse(payload);
          if (authenticatedId !== window.allowed || request.capability !== 'synthetic-test-capability') {
            throw new Error('not authorized');
          }
          return JSON.stringify({ authenticated_id: authenticatedId, value: request.value });
        });
        return { id: window.endpoint.id(), address: await window.endpoint.address() };
      };
    });
    assert.equal(await page.evaluate(() => crossOriginIsolated), true);
  }
  await assert.rejects(pages[0].evaluate(() => window.api.BrowserEndpoint.start('00', () => '')));
  const peers = await Promise.all(pages.map((page) => page.evaluate(() => window.startEndpoint())));
  assert.equal(new Set(peers.map((peer) => peer.id)).size, 3);
  for (let index = 0; index < pages.length; index++) {
    const signingId = await pages[index].evaluate(() => {
      const domain = new window.api.Domain(window.seed, 'Transport test', '[]', '[]');
      const id = domain.id();
      domain.free();
      return id;
    });
    assert.equal(signingId, peers[index].id);
  }
  observations.push('Persisted-seed interface gives domain signatures and Iroh the same device identity');
  await pages[0].evaluate((id) => { window.allowed = id; }, peers[1].id);
  await pages[1].evaluate((id) => { window.allowed = id; }, peers[0].id);
  for (const [source, target] of [[0, 1], [1, 0]]) {
    const receipt = JSON.parse(await pages[source].evaluate(({ address }) => window.endpoint.request(address, JSON.stringify({ capability: 'synthetic-test-capability', value: 'host transport ✓' })), peers[target]));
    assert.equal(receipt.peer_id, peers[target].id);
    assert.deepEqual(JSON.parse(receipt.payload), { authenticated_id: peers[source].id, value: 'host transport ✓' });
  }
  observations.push('Separate real browser endpoints exchange authenticated messages in both directions');
  await assert.rejects(pages[2].evaluate(({ address, claimed }) => window.endpoint.request(address, JSON.stringify({ capability: 'synthetic-test-capability', claimed_id: claimed, value: 'forged claim' })), { address: peers[1].address, claimed: peers[0].id }));
  const last = await pages[1].evaluate(() => window.calls.at(-1));
  assert.equal(last.authenticatedId, peers[2].id);
  await assert.rejects(pages[0].evaluate((address) => window.endpoint.request(address, 'malformed JSON'), peers[1].address));
  await assert.rejects(pages[0].evaluate((address) => window.endpoint.request(address, JSON.stringify({ capability: 'wrong' })), peers[1].address));
  observations.push('Callback receives transport identity despite a forged payload ID; handler rejection and malformed requests fail the exchange');
  await assert.rejects(pages[0].evaluate(({ address, expected }) => window.api.BrowserEndpoint.validate_address(address, expected), { address: peers[1].address, expected: peers[2].id }));
  await assert.rejects(pages[0].evaluate((address) => window.endpoint.request(address, 'x'.repeat(8 * 1024 * 1024 + 1)), peers[1].address));
  observations.push('Wrong contact identity and payloads above 8 MiB are rejected at the transport boundary');
  await pages[1].evaluate(async () => { await window.endpoint.close(); window.endpoint.free(); });
  const restarted = await pages[1].evaluate(() => window.startEndpoint());
  assert.equal(restarted.id, peers[1].id);
  const again = JSON.parse(await pages[0].evaluate((address) => window.endpoint.request(address, JSON.stringify({ capability: 'synthetic-test-capability', value: 'after restart' })), restarted.address));
  assert.equal(again.peer_id, peers[1].id);
  assert.equal(JSON.parse(again.payload).value, 'after restart');
  observations.push('Reopening the endpoint with the same supplied seed preserves identity and allows reconnection');
  await pages[1].evaluate(() => { window.holdRequests = true; window.calls = []; });
  await pages[0].evaluate((address) => {
    window.finished = [];
    window.pending = Promise.allSettled(Array.from({ length: 20 }, (_, index) =>
      window.endpoint.request(address, JSON.stringify({ index })).then(
        (value) => { window.finished.push('fulfilled'); return value; },
        (error) => { window.finished.push('rejected'); throw error; },
      )));
  }, restarted.address);
  await pages[1].waitForFunction(() => window.calls.length === 16, undefined, { timeout: 10_000 });
  await pages[0].waitForFunction(() => window.finished.length === 4 && window.finished.every((state) => state === 'rejected'), undefined, { timeout: 10_000 });
  await pages[0].evaluate(async () => {
    await window.endpoint.close();
    window.endpoint.free();
    window.endpoint = undefined;
  });
  await pages[1].evaluate(async () => { await window.endpoint.close(); });
  const retired = await pages[0].evaluate(() => window.pending);
  assert.equal(retired.length, 20);
  assert(retired.every((result) => result.status === 'rejected'));
  assert.equal(await pages[1].evaluate(() => window.calls.length), 16);
  observations.push('Incoming work is limited to 16 handlers; closing the endpoint retires all pending exchanges');
  assert.deepEqual(browserErrors, []);
} catch (error) { failure = error.stack ?? String(error); }
finally {
  await Promise.allSettled(pages.map((page) => page.evaluate(async () => { await window.endpoint?.close(); })));
  await browser.close();
  const report = {
    status: failure ? 'failed' : 'passed', timestamp: new Date().toISOString(),
    protocol: 'shared-workspace/browser/1',
    scope: 'Transport callback seam; full Turso host invitation/membership tests are separate',
    source_sha256: createHash('sha256').update(await readFile(resolve(root, 'src/transport.rs'))).digest('hex'),
    wasm_sha256: createHash('sha256').update(await readFile(resolve(root, 'pkg/browser_workspace_host_bg.wasm'))).digest('hex'),
    observations, relay_connections: sockets, browser_errors: browserErrors,
    ...(failure ? { failure } : {}),
  };
  await mkdir(resolve(root, 'evidence'), { recursive: true });
  await writeFile(resolve(root, 'evidence/transport.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  if (failure) process.exitCode = 1;
}
