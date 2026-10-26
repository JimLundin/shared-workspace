import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from 'playwright';
import { build } from 'vite';

const checks = dirname(fileURLToPath(import.meta.url)), source = resolve(checks, '../..');
function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index < 0) return fallback;
  const value = process.argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`Missing ${name} value`);
  return value;
}
const engineNames = [argument('--inviter', 'chromium'), argument('--joiner', 'webkit')];
if (engineNames.some(name => !['chromium', 'webkit'].includes(name))) throw new Error('Select chromium or webkit');
const evidence = resolve(argument('--evidence', join(checks, 'evidence', `surface-${Date.now()}`)));
const temporary = await mkdtemp(join(tmpdir(), 'workspace-surface-acceptance-'));
const dist = join(temporary, 'dist'), engines = { chromium, webkit }, peers = [];
const report = {
  kind: 'browser-surface-acceptance-v1', passed: false, started_at: new Date().toISOString(),
  scope: 'Current BrowserHost facade, real Turso OPFS and Iroh WASM in separate persistent browser profiles.',
  engine_names: engineNames, assertions: [], stages: [], launches: [],
  limits: ['No physical iPhone Safari result.', 'No production UI interaction result.', 'Office targets are shared links; no Office login, embedded editor or vendor document write is claimed.'],
};
let server, origin, activeStage = 'setup', beforeSources;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function check(name, condition) {
  if (!condition) { const error = new Error(name); error.name = 'AssertionFailure'; throw error; }
  report.assertions.push(name);
}
async function stage(name, action) {
  activeStage = name;
  console.log(JSON.stringify({ stage: name }));
  const started = Date.now();
  await action();
  report.stages.push({ name, elapsed_ms: Date.now() - started });
}
async function files(directory) {
  const paths = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) paths.push(...await files(path));
    else if (entry.isFile()) paths.push(path);
  }
  return paths.sort();
}
async function sourceHashes() {
  const paths = ['native/core/src/model.rs', 'native/core/src/work.rs', 'native/ui/src/protocol.ts', 'native/ui/src/meeting-preparation.ts', 'browser/checks/page.ts', 'browser/checks/index.html', 'browser/checks/surface-acceptance.mjs', 'browser/host-wasm/Cargo.toml', 'browser/host-wasm/Cargo.lock'];
  for (const path of ['browser/host/src', 'browser/host-wasm/src', 'browser/host-wasm/pkg']) {
    for (const file of await files(join(source, path))) paths.push(relative(source, file));
  }
  return Object.fromEntries(await Promise.all(paths.sort().map(async path => [path, hash(await readFile(join(source, path)))])));
}
async function deadline(work, label, milliseconds = 90000) {
  let timeout;
  try {
    return await Promise.race([work, new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error(`${label} deadline`)), milliseconds); })]);
  } finally { clearTimeout(timeout); }
}
async function serve() {
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const file = resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
      if (!file.startsWith(dist + sep)) { response.writeHead(403).end(); return; }
      const data = await readFile(file);
      response.writeHead(200, {
        'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css', '.json': 'application/json' }[extname(file)] ?? 'application/octet-stream',
        'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin', 'Cache-Control': 'no-store',
      }).end(data);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((done, failed) => { server.once('error', failed); server.listen(0, '127.0.0.1', done); });
  origin = `http://127.0.0.1:${server.address().port}`;
}
class Peer {
  constructor(name, engine) { this.name = name; this.engine = engine; this.profile = join(temporary, name); peers.push(this); }
  async start(paused = false) {
    this.context = await engines[this.engine].launchPersistentContext(this.profile, { headless: true });
    this.page = await this.context.newPage();
    await this.page.goto(origin);
    await this.page.waitForFunction(() => Boolean(window.workspaceCheck));
    check(`${this.name}-isolated-${report.launches.length}`, await this.page.evaluate(() => crossOriginIsolated && typeof SharedArrayBuffer === 'function'));
    report.launches.push({ peer: this.name, engine: this.engine, version: this.context.browser().version() });
    return this.call('open', [{ deviceName: this.name, databaseName: 'surface-acceptance.db', baseUrl: origin + '/', startPaused: paused }]);
  }
  async call(method, args = []) {
    const result = await deadline(this.page.evaluate(async ({ method, args }) => {
      try { return { ok: true, value: await window.workspaceCheck[method](...args) }; }
      catch (error) { return { ok: false, error_name: error instanceof Error ? error.name : 'UnknownError' }; }
    }, { method, args }), `${this.name}.${method}`);
    if (!result.ok) throw new Error(`${this.name}.${method} failed (${result.error_name})`);
    return result.value;
  }
  snapshot() { return this.call('snapshot'); }
  command(command) { return this.call('command', [command]); }
  async stop(graceful = false) {
    if (!this.context) return;
    const context = this.context;
    try { if (graceful) await this.call('close'); }
    finally { await deadline(context.close(), `${this.name}.close`, 30000); this.context = undefined; this.page = undefined; }
  }
}
function resource(snapshot, id) {
  const matches = snapshot.resources.filter(resource => resource.id === id);
  if (matches.length !== 1) throw new Error(`Missing unique fixture resource ${id}`);
  return matches[0];
}
const heads = (snapshot, id) => resource(snapshot, id).heads.map(head => head.id).sort();
const codeTexts = snapshot => resource(snapshot, 'code').heads.map(head => head.surface?.type === 'code_document' ? head.surface.text : null).sort();
const code = text => ({ type: 'code_document', language: 'python', text });
const work = 'surface-work';
async function applied(peer, command) {
  const receipt = await peer.command(command);
  check(`${command.operation_id}-committed`, receipt.type === 'applied' && receipt.operation_id === command.operation_id && receipt.workspace_id === command.workspace_id && receipt.replayed === false);
}
const createSurface = (operation, id, title, surface) => ({ type: 'create_surface', operation_id: operation, workspace_id: work, resource_id: id, title, surface });
const editSurface = (operation, id, expected, title, surface) => ({ type: 'edit_surface', operation_id: operation, workspace_id: work, resource_id: id, expected_heads: expected, title, surface });
async function exchange(a, b) {
  for (let attempt = 0; attempt < 4; attempt++) {
    await a.call('sync'); await b.call('sync');
    const [left, right] = await Promise.all([a.snapshot(), b.snapshot()]);
    if (equal(left.resources, right.resources) && equal(left.workspaces, right.workspaces)) return [left, right];
    await new Promise(done => setTimeout(done, 250));
  }
  throw new Error('Paired surface histories did not converge after bounded real exchanges');
}

try {
  await mkdir(evidence, { recursive: true });
  beforeSources = await sourceHashes();
  report.source_hashes = beforeSources;
  await stage('disposable-build', async () => {
    await build({ configFile: false, root: checks, cacheDir: join(temporary, 'vite-cache'), logLevel: 'error', base: './', worker: { format: 'es' }, build: { outDir: dist, emptyOutDir: true, target: 'esnext', sourcemap: false }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
    report.asset_hashes = Object.fromEntries(await Promise.all((await files(dist)).map(async path => [relative(dist, path), hash(await readFile(path))])));
    await serve();
  });
  const a = new Peer('Inviter', engineNames[0]), b = new Peer('Joining-peer', engineNames[1]);
  let firstA, firstB, peerEdit;
  await stage('open-real-hosts', async () => {
    [firstA, firstB] = await Promise.all([a.start(), b.start()]);
    check('independent-device-identities', firstA.device_id !== firstB.device_id);
  });
  await stage('create-code-office-and-note', async () => {
    await applied(a, { type: 'create_workspace', operation_id: 'create-work', workspace_id: work, title: 'Surface acceptance' });
    await applied(a, createSurface('create-code', 'code', 'script.py', code('print("original")\n')));
    await applied(a, createSurface('create-word', 'word', 'Word', { type: 'application', application: 'word', url: 'https://word.cloud.microsoft/' }));
    await applied(a, { type: 'create_resource', operation_id: 'create-note', workspace_id: work, resource_id: 'note', title: 'Existing note', body: 'The legacy note stays a note.' });
    let invitation = await a.call('invite', [[work]]);
    const joined = await b.call('join', [invitation.ticket]);
    invitation = undefined;
    check('joined-peer-has-code-text', equal(codeTexts(joined), ['print("original")\n']));
    check('joined-peer-has-exact-office-target', equal(resource(joined, 'word').heads[0].surface, { type: 'application', application: 'word', url: 'https://word.cloud.microsoft/' }));
    check('legacy-note-shape-preserved', resource(joined, 'note').heads[0].body === 'The legacy note stays a note.' && !('surface' in resource(joined, 'note').heads[0]));
  });
  await stage('peer-edits-and-app-target-edit', async () => {
    peerEdit = editSurface('peer-code-edit', 'code', heads(await b.snapshot(), 'code'), 'script.py', code('print("written on joining peer")\n'));
    await applied(b, peerEdit);
    let [left] = await exchange(a, b);
    check('joining-peer-code-edit-reached-inviter', equal(codeTexts(left), ['print("written on joining peer")\n']));
    await applied(a, editSurface('office-target-edit', 'word', heads(left, 'word'), 'Word web', { type: 'application', application: 'word', url: 'https://word.cloud.microsoft/en-us/' }));
    const [, right] = await exchange(a, b);
    check('updated-office-target-reached-peer', resource(right, 'word').heads[0].surface.url === 'https://word.cloud.microsoft/en-us/');
  });
  await stage('full-browser-reopen', async () => {
    const [beforeA, beforeB] = await Promise.all([a.snapshot(), b.snapshot()]);
    await Promise.all([a.stop(), b.stop()]);
    const [afterA, afterB] = await Promise.all([a.start(true), b.start(true)]);
    check('browser-reopen-retains-identities', afterA.device_id === firstA.device_id && afterB.device_id === firstB.device_id);
    check('browser-reopen-retains-all-surface-revisions', equal(afterA.resources, beforeA.resources) && equal(afterB.resources, beforeB.resources));
    check('browser-reopen-retains-operation-counts', afterA.operation_count === beforeA.operation_count && afterB.operation_count === beforeB.operation_count);
    report.reopen_without_explicit_host_flush = true;
  });
  await stage('offline-branches-converge-and-persist', async () => {
    const left = await a.snapshot(), right = await b.snapshot();
    check('both-endpoints-paused-before-branching', left.network.type === 'paused' && right.network.type === 'paused');
    const base = heads(left, 'code');
    check('offline-code-base-matches', equal(base, heads(right, 'code')));
    await applied(a, editSurface('inviter-offline-code', 'code', base, 'script.py', code('print("inviter branch")\n')));
    await applied(b, editSurface('peer-offline-code', 'code', base, 'script.py', code('print("peer branch")\n')));
    await Promise.all([a.call('setForeground', [true]), b.call('setForeground', [true])]);
    const [mergedA, mergedB] = await exchange(a, b);
    check('both-concurrent-code-branches-retained', equal(codeTexts(mergedA), ['print("inviter branch")\n', 'print("peer branch")\n']) && equal(codeTexts(mergedB), codeTexts(mergedA)));
    check('code-history-retains-create-peer-edit-and-both-branches', resource(mergedA, 'code').history.length === 4);
    const stale = await a.command(editSurface('stale-code-edit', 'code', ['inviter-offline-code'], 'script.py', code('must not overwrite')));
    check('stale-code-edit-is-conflict', stale.type === 'conflict' && equal([...stale.actual_heads].sort(), heads(mergedA, 'code')));
    check('stale-edit-did-not-commit', (await a.snapshot()).operation_count === mergedA.operation_count);
    await b.stop();
    const reopened = await b.start(true);
    check('both-code-branches-survive-browser-reopen', equal(resource(reopened, 'code'), resource(mergedB, 'code')));
    await applied(a, editSurface('resolve-code', 'code', heads(mergedA, 'code'), 'script.py', code('print("both branches reviewed")\n')));
    await b.call('setForeground', [true]);
    const [resolvedA, resolvedB] = await exchange(a, b);
    check('reviewed-code-resolution-reaches-both-peers', equal(codeTexts(resolvedA), ['print("both branches reviewed")\n']) && equal(codeTexts(resolvedB), codeTexts(resolvedA)));
    check('resolution-retains-all-earlier-revisions', resource(resolvedB, 'code').history.length === 5);
    const replay = await b.command(peerEdit);
    check('reopened-code-request-replays-without-overwrite', replay.type === 'applied' && replay.replayed === true && equal(codeTexts(await b.snapshot()), codeTexts(resolvedB)));
    check('office-and-note-remain-after-code-reconciliation', resource(resolvedB, 'word').heads[0].surface.url === 'https://word.cloud.microsoft/en-us/' && resource(resolvedB, 'note').heads[0].body === 'The legacy note stays a note.');
  });
  report.passed = true;
} catch (error) {
  report.failure = { stage: activeStage, kind: error?.name ?? 'Error', detail: error?.name === 'AssertionFailure' ? error.message : 'Build, browser or host operation failed; no private payload was exported.' };
} finally {
  const cleanup = await Promise.allSettled(peers.map(peer => peer.stop(true)));
  report.contexts_closed = cleanup.every(result => result.status === 'fulfilled');
  if (!report.contexts_closed) report.passed = false;
  if (server) { server.closeAllConnections(); await new Promise(done => server.close(done)); }
  report.static_server_closed = true;
  if (beforeSources) {
    try { report.source_unchanged = equal(beforeSources, await sourceHashes()); }
    catch { report.source_unchanged = false; }
    if (!report.source_unchanged) { report.passed = false; report.failure ??= { stage: 'cleanup', kind: 'SourceChanged', detail: 'Source changed during acceptance.' }; }
  }
  await rm(temporary, { recursive: true, force: true });
  report.disposable_build_and_profiles_removed = true;
  report.finished_at = new Date().toISOString();
  await mkdir(evidence, { recursive: true });
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, assertions: report.assertions.length, failure: report.failure, report: join(evidence, 'report.json') }));
  if (!report.passed) process.exitCode = 1;
}
