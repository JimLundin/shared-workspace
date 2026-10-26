import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from 'playwright';
import { build } from 'vite';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
function option(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index < 0) return fallback;
  const value = process.argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`Missing ${name} value`);
  return value;
}
const engineName = option('--engine', 'chromium');
assert(['chromium', 'webkit'].includes(engineName));
const temporary = await mkdtemp(join(tmpdir(), 'workspace-local-drafts-'));
const evidence = resolve(option('--evidence', join(tmpdir(), `workspace-local-drafts-${Date.now()}.json`)));
const fixture = join(temporary, 'fixture'), dist = join(temporary, 'dist'), profile = join(temporary, 'profile');
const report = {
  kind: 'local-draft-storage-acceptance-v1', passed: false, engine: engineName, assertions: [],
  scope: 'Actual BrowserStore, pinned Turso 0.8.3 OPFS, domain WASM and browser process restarts with synthetic input.',
  limits: ['No physical iPhone result.', 'Typed editor/capture/meeting payload validation belongs to the owning controller.', 'No credentials or raw audio are used. No peer or external action is executed.'],
};
const checkedFiles = ['browser/host/src/local-drafts.ts', 'browser/host/src/store.ts', 'browser/host/tests/local-drafts.test.mjs', 'browser/host/tests/local-drafts-storage.mjs'];
const hashes = async () => Object.fromEntries(await Promise.all(checkedFiles.map(async name => [name, createHash('sha256').update(await readFile(join(root, name))).digest('hex')])));
let server, context, page, origin;
function record(name, check) { check(); report.assertions.push(name); console.log(JSON.stringify({ passed: name })); }
async function deadline(work, label, milliseconds = 45000) {
  let timer;
  try { return await Promise.race([work, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label + ' deadline')), milliseconds); })]); }
  finally { clearTimeout(timer); }
}
async function request(method, args = []) {
  return deadline(page.evaluate(async ({ method, args }) => {
    try { return { ok: true, value: await window.localDraftCheck[method](...args) }; }
    catch (error) { return { ok: false, error: error instanceof Error ? error.message : 'Unknown failure' }; }
  }, { method, args }), method);
}
async function call(method, args = []) {
  const result = await request(method, args);
  assert(result.ok, `${method} failed: ${result.error}`);
  return result.value;
}
async function launch() {
  context = await { chromium, webkit }[engineName].launchPersistentContext(profile, { headless: true });
  await context.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  page = context.pages()[0] ?? await context.newPage();
  await page.goto(origin);
  await page.waitForFunction(() => Boolean(window.localDraftCheck), undefined, { timeout: 45000 });
  report.browser_version = context.browser().version();
  return call('open');
}
async function stop() {
  if (!context) return;
  const closing = context; context = undefined; page = undefined;
  await deadline(closing.close(), 'Browser close', 30000);
}

try {
  report.sources = await hashes();
  await mkdir(fixture);
  await writeFile(join(fixture, 'index.html'), '<!doctype html><html><body><script type="module" src="./fixture.js"></script></body></html>');
  await writeFile(join(fixture, 'fixture.js'), `
import init from ${JSON.stringify(join(root, 'browser/host-wasm/pkg/browser_workspace_host.js'))};
import { BrowserStore } from ${JSON.stringify(join(root, 'browser/host/src/store.ts'))};
import { connect } from ${JSON.stringify(join(root, 'browser/host/node_modules/@tursodatabase/database-wasm/dist/promise-default.js'))};
await init();
let store;
const databaseName = 'local-draft-check.db';
window.localDraftCheck = {
  async open() { store = await BrowserStore.open(databaseName, 'Synthetic local draft host'); return store.id; },
  async close() { await store.close(); store = undefined; },
  async simulatePriorSchema() {
    await store.close(); store = undefined;
    const database = await connect(databaseName);
    try { await database.exec('DROP TABLE local_drafts'); } finally { await database.close(); }
  },
  command(value) { return store.command(value); },
  list() { return store.listLocalDrafts(); },
  upsert(value) { return store.upsertLocalDraft(value); },
  remove(value) { return store.removeLocalDraft(value); },
  async projection() {
    return { snapshot: await store.snapshot(), operations: await store.exportFor(store.id, ['local-work']),
      fingerprint: await store.fingerprint(['local-work']), connections: await store.exportConnections(store.id, ['local-work']) };
  },
  async captureEnvelope(value) {
    const pending = store.upsertLocalDraft(value);
    value.generation += 100; value.payload = '{"text":"MUTATED_AFTER_QUEUE"}';
    return { receipt: await pending, journal: await store.listLocalDrafts() };
  },
  async loseReply(value) { await store.upsertLocalDraft(value); throw new Error('Synthetic reply lost after local commit'); },
  async ordered(actions) { return Promise.all(actions.map(action => action.type === 'remove' ? store.removeLocalDraft(action.value) : store.upsertLocalDraft(action.value))); },
  async closeWithWrite(value) {
    const pending = store.upsertLocalDraft(value), closing = store.close();
    const late = store.upsertLocalDraft({ ...value, id: 'rejected-after-close' }).then(() => false, () => true);
    const receipt = await pending; await closing; store = undefined;
    return { receipt, lateRejected: await late };
  },
};`);
  await build({ root: fixture, configFile: false, logLevel: 'error', base: './', worker: { format: 'es' },
    build: { outDir: dist, emptyOutDir: true, target: 'esnext' }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const file = resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
      if (!file.startsWith(dist + sep)) { response.writeHead(403).end(); return; }
      const body = await readFile(file);
      response.writeHead(200, {
        'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm' }[extname(file)] ?? 'application/octet-stream',
        'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cache-Control': 'no-store',
      }).end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;

  const identity = await launch();
  await call('command', [{ type: 'create_workspace', operation_id: 'create-local-work', workspace_id: 'local-work', title: 'Synthetic shared work' }]);
  const baseline = await call('projection');
  await call('simulatePriorSchema');
  const migrated = await call('open');
  record('Additive journal setup preserves the existing identity and signed workspace', () => assert.equal(migrated, identity));
  const migratedProjection = await call('projection');
  record('Existing tables retain their data when the new journal table is created', () => assert.deepEqual(migratedProjection, baseline));

  const editor = { id: 'editor:local-work', kind: 'editor', generation: 1, payload: '{ "title": "Synthetic draft", "body": "PRIVATE_LOCAL_DRAFT_ONLY" }' };
  const captured = await call('captureEnvelope', [editor]);
  record('The queued write snapshots its immutable envelope before awaiting storage', () => assert.deepEqual(captured.journal.drafts, [editor]));
  const pending = { id: 'pending:local-work', kind: 'capture', generation: 7, payload: JSON.stringify({
    text: 'PRIVATE_LOCAL_DRAFT_ONLY', pending: { type: 'create_resource', operation_id: 'synthetic-pending-command-42', workspace_id: 'local-work', resource_id: 'not-executed', title: 'Unfinished', body: 'Synthetic input' },
  }) };
  const lost = await request('loseReply', [pending]);
  record('A lost journal reply remains an unknown receipt to its caller', () => assert.equal(lost.ok, false));
  await stop();
  const reopened = await launch();
  record('A fresh browser process restores the same local identity', () => assert.equal(reopened, identity));
  const recovered = await call('list');
  record('Unfinished text and the exact pending command bytes survive browser restart', () => assert.deepEqual(recovered.drafts, [editor, pending]));
  record('The pending command identity survives without being replaced by a new attempt', () => assert.equal(JSON.parse(recovered.drafts.find(value => value.id === pending.id).payload).pending.operation_id, 'synthetic-pending-command-42'));
  const unchanged = await call('projection');
  record('Local input and pending commands do not enter snapshots operations fingerprints or connection exports', () => assert.deepEqual(unchanged, baseline));
  record('Retaining a pending command does not execute it', () => assert.equal(unchanged.snapshot.resources.some(resource => resource.id === 'not-executed'), false));

  const second = { ...editor, generation: 2, payload: '{"body":"Newer synthetic edit"}' };
  const ordered = await call('ordered', [[
    { type: 'upsert', value: second },
    { type: 'remove', value: { id: editor.id, generation: 3 } },
    { type: 'upsert', value: second },
  ]]);
  record('The actual SQL queue orders retention deletion and a late stale write', () => assert.deepEqual(ordered.map(value => value.type), ['retained', 'removed', 'stale']));
  const removed = await call('list');
  record('Deleted input leaves a generation fence instead of a recoverable draft', () => {
    assert.deepEqual(removed.drafts, [pending]);
    assert.deepEqual(removed.removed, [{ id: editor.id, generation: 3 }]);
  });
  const replay = await call('remove', [{ id: editor.id, generation: 3 }]);
  record('An exact deletion retry reports the same outcome', () => assert.deepEqual(replay, { type: 'removed', id: editor.id, generation: 3, replayed: true }));
  await call('close'); await stop(); await launch();
  const stale = await call('upsert', [second]);
  record('A tombstone still rejects stale input after a second browser restart', () => assert.deepEqual(stale, { type: 'stale', id: editor.id, generation: 3 }));
  const collision = await request('upsert', [{ ...editor, generation: 3 }]);
  record('The deletion generation cannot be reused for input', () => assert.equal(collision.ok, false));
  const next = { ...editor, generation: 4 };
  await call('upsert', [next]);
  const obsoleteRemoval = await call('remove', [{ id: editor.id, generation: 3 }]);
  record('Deliberate stable-slot reuse survives a delayed old deletion', () => assert.equal(obsoleteRemoval.type, 'stale'));
  const invalid = await request('upsert', [{ ...next, generation: 5, payload: '[]' }]);
  record('Invalid journal content rejects before changing retained state', () => assert.equal(invalid.ok, false));
  const afterInvalid = await call('list');
  record('A rejected input leaves existing data usable and unchanged', () => assert.deepEqual(afterInvalid.drafts, [next, pending]));

  const closing = { id: 'capture:closing', kind: 'capture', generation: 1, payload: '{"text":"Synthetic input queued before close"}' };
  const drained = await call('closeWithWrite', [closing]);
  record('Close drains an admitted write and rejects new writes', () => { assert.equal(drained.receipt.type, 'retained'); assert.equal(drained.lateRejected, true); });
  await call('open');
  const final = await call('list');
  record('The write drained during close is durable on reopen', () => assert.deepEqual(final.drafts, [closing, next, pending]));
  const finalProjection = await call('projection');
  record('All journal operations leave the replicated domain unchanged', () => assert.deepEqual(finalProjection, baseline));
  await call('close');
  assert.deepEqual(await hashes(), report.sources, 'Acceptance must not modify its production source');
  report.passed = true;
} catch (error) {
  report.failure = error instanceof Error ? error.stack : String(error);
  process.exitCode = 1;
} finally {
  await stop();
  if (server) await new Promise(resolve => server.close(resolve));
  await rm(temporary, { recursive: true, force: true });
  await mkdir(dirname(evidence), { recursive: true });
  await writeFile(evidence, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, assertions: report.assertions.length, evidence, failure: report.failure }));
}
