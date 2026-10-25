import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('.', import.meta.url));
const origin = 'https://iroh-probe.invalid';
const browser = await chromium.launch({ headless: true });
const contexts = [];
const pages = [];
const sockets = [];
const errors = [];
const observations = [];
let memoryBytes = [];
const started = Date.now();
let failure;
try {
  for (let index = 0; index < 3; index++) {
    const context = await browser.newContext();
    contexts.push(context);
    // Serve only this probe's static assets inside Playwright. No HTTP listener,
    // tunnel, phone preview, or message/relay mock exists in this check.
    await context.route(`${origin}/**`, async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      const path = resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
      if (!path.startsWith(root)) return route.abort();
      const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm' }[extname(path)] ?? 'application/octet-stream';
      await route.fulfill({ body: await readFile(path), contentType, headers: {
        'Cross-Origin-Opener-Policy': 'same-origin',
        'Cross-Origin-Embedder-Policy': 'require-corp',
      } });
    });
    const page = await context.newPage();
    pages.push(page);
    page.on('websocket', (socket) => sockets.push({ context: index, origin: new URL(socket.url()).origin }));
    page.on('pageerror', (error) => errors.push({ context: index, error: error.message }));
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.probe), { timeout: 30_000 });
    assert.equal(await page.evaluate(() => crossOriginIsolated), true);
  }
  const peers = await Promise.all(pages.map((page) => page.evaluate(() => window.probe.start())));
  assert.equal(new Set(peers.map((peer) => peer.id)).size, 3);
  observations.push('Three isolated browser contexts created distinct Iroh identities');
  await pages[0].evaluate((address) => window.probe.select(address), peers[1].address);
  await pages[1].evaluate((address) => window.probe.select(address), peers[0].address);
  const first = await pages[0].evaluate(() => window.probe.send('A to B: synthetic workspace update'));
  assert.equal(first.peer_id, peers[1].id);
  assert.equal(first.text, 'A to B: synthetic workspace update');
  const second = await pages[1].evaluate(() => window.probe.send('B to A: synthetic reply ✓'));
  assert.equal(second.peer_id, peers[0].id);
  assert.equal(second.text, 'B to A: synthetic reply ✓');
  const firstEvents = await pages[0].evaluate(() => window.probe.events());
  const secondEvents = await pages[1].evaluate(() => window.probe.events());
  assert(firstEvents.some((event) => event.type === 'received' && event.peer_id === peers[1].id && event.text === second.text));
  assert(secondEvents.some((event) => event.type === 'received' && event.peer_id === peers[0].id && event.text === first.text));
  observations.push('Both peers initiated an exchange; authenticated identities and UTF-8 payloads matched at both receivers');
  await pages[2].evaluate((address) => window.probe.select(address), peers[1].address);
  await assert.rejects(pages[2].evaluate(() => window.probe.send('Unselected peer must be rejected')));
  const rejected = await pages[1].evaluate(() => window.probe.events());
  assert(rejected.some((event) => event.type === 'rejected' && event.peer_id === peers[2].id));
  assert(!rejected.some((event) => event.type === 'received'));
  observations.push('Receiver rejected an authenticated third identity that was not selected');
  assert(sockets.length >= 3);
  assert(sockets.every((socket) => socket.origin.startsWith('wss://')));
  assert.deepEqual(errors, []);
  memoryBytes = await Promise.all(pages.map((page) => page.evaluate(() => window.probe.memory_bytes())));
} catch (error) {
  failure = error.stack ?? String(error);
} finally {
  await Promise.allSettled(pages.map((page) => page.evaluate(() => window.probe?.close())));
  await browser.close();
  const wasm = await readFile(resolve(root, 'pkg/browser_iroh_probe_bg.wasm'));
  const sourceHashes = {};
  for (const path of ['Cargo.toml', 'Cargo.lock', 'src/lib.rs', 'page.js', 'check.mjs']) {
    sourceHashes[path] = createHash('sha256').update(await readFile(resolve(root, path))).digest('hex');
  }
  const report = {
    status: failure ? 'failed' : 'passed',
    timestamp: new Date().toISOString(),
    duration_ms: Date.now() - started,
    browser: 'Chromium, three isolated Playwright browser contexts',
    transport: 'Iroh 1.4.1 WASM, actual external WSS relays',
    cross_origin_isolated: true,
    wasm_sha256: createHash('sha256').update(wasm).digest('hex'),
    source_sha256: sourceHashes,
    wasm_bytes: wasm.length,
    wasm_linear_memory_bytes: memoryBytes,
    observations, relay_connections: sockets, browser_errors: errors,
    ...(failure ? { failure } : {}),
  };
  await mkdir(resolve(root, 'evidence'), { recursive: true });
  await writeFile(resolve(root, 'evidence/browser-exchange.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  if (failure) process.exitCode = 1;
}
