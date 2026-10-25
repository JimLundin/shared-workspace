import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdtemp, mkdir, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { build } from 'vite';

const checks = dirname(fileURLToPath(import.meta.url));
const source = resolve(checks, '../..');
let origin = 'https://workspace-checks.invalid';
const loopback = !process.argv.includes('--route');
let staticServer;
const cases = new Set(['durability', 'pairing', 'offline', 'all']);
const caseArgument = process.argv.indexOf('--case');
const selected = caseArgument < 0 ? 'all' : process.argv[caseArgument + 1];
const evidenceArgument = process.argv.indexOf('--evidence');
const evidence = evidenceArgument < 0 ? join(checks, 'evidence', `run-${Date.now()}`) : resolve(process.argv[evidenceArgument + 1]);
const privateRoot = await mkdtemp(join(tmpdir(), 'workspace-browser-acceptance-'));
const dist = join(privateRoot, 'dist');
const report = {
  kind: 'browser-workspace-acceptance-v1', passed: false, started_at: new Date().toISOString(),
  case: selected, assertions: [], cases: [], browser_launches: [], browser_closes: [], relay_origins: [],
  scope: 'Real BrowserHost public facade, Turso OPFS and Iroh WASM in separate persistent Chromium profiles.',
  limits: ['No physical iPhone result.', 'No production UI or voice interaction result.', 'No LAN listener or public deployment.', 'No managed-device policy approval or storage-eviction guarantee.'],
};
const diagnostics = [];
function diagnose(entry) {
  diagnostics.push(entry);
  // Private diagnostics contain no method arguments or invitation contents.
  void writeFile(join(privateRoot, 'live-diagnostics.json'), JSON.stringify(diagnostics, null, 2) + '\n');
}
const peers = [];
let sourceBefore;

await mkdir(dirname(evidence), { recursive: true });
await mkdir(evidence);

class SetupError extends Error { constructor(message) { super(message); this.name = 'SetupError'; } }
class BehavioralError extends Error { constructor(assertion) { super(assertion); this.name = 'BehavioralError'; this.assertion = assertion; } }
function check(assertion, condition, observed) {
  if (!condition) {
    if (observed !== undefined) diagnose({ kind: 'assertion_observation', assertion, observed });
    throw new BehavioralError(assertion);
  }
  report.assertions.push(assertion);
}
function hash(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function files(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await files(path));
    else if (entry.isFile()) result.push(path);
  }
  return result.sort();
}
async function sourceHashes() {
  const roots = ['browser/host/src', 'browser/host-wasm/src', 'browser/host-wasm/pkg'];
  const paths = ['browser/host/package.json', 'browser/host/package-lock.json', 'browser/host-wasm/Cargo.toml', 'browser/host-wasm/Cargo.lock', 'browser/host-wasm/build.sh', 'native/core/src/model.rs', 'native/ui/src/protocol.ts'];
  for (const root of roots) for (const path of await files(join(source, root))) paths.push(relative(source, path));
  for (const entry of ['check.mjs', 'page.ts', 'index.html', 'package.json', 'package-lock.json', 'tsconfig.json']) paths.push(relative(source, join(checks, entry)));
  return Object.fromEntries(await Promise.all(paths.sort().map(async (path) => [path, hash(await readFile(join(source, path)))])));
}
async function assetHashes() {
  return Object.fromEntries(await Promise.all((await files(dist)).map(async (path) => {
    const bytes = await readFile(path);
    return [relative(dist, path), { sha256: hash(bytes), bytes: bytes.length }];
  })));
}
async function startStaticServer() {
  staticServer = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const path = resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
      if (!path.startsWith(dist + sep)) { response.writeHead(403).end(); return; }
      const body = await readFile(path);
      response.writeHead(200, {
        'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css', '.json': 'application/json' }[extname(path)] ?? 'application/octet-stream',
        'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cache-Control': 'no-store',
      }).end(body);
    } catch (error) {
      diagnose({ kind: 'static_server_failure', path: new URL(request.url, 'http://127.0.0.1').pathname, detail: String(error) });
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve, reject) => {
    staticServer.once('error', reject);
    staticServer.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${staticServer.address().port}`;
}
async function within(promise, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new SetupError(`${label} exceeded its deadline`)), milliseconds); })]);
  } finally { clearTimeout(timer); }
}
function hostOptions(name, startPaused = false) {
  return { deviceName: name, databaseName: 'workspace-acceptance.db', baseUrl: origin + '/', startPaused };
}
class Peer {
  constructor(name) { this.name = name; this.profile = join(privateRoot, name); this.launches = 0; peers.push(this); }
  async start(startPaused = false) {
    if (this.context) throw new SetupError('Cannot reopen a running browser profile');
    this.context = await chromium.launchPersistentContext(this.profile, { headless: true });
    this.launches += 1;
    report.browser_launches.push({ peer: this.name, launch: this.launches, browser: this.context.browser().version() });
    if (!loopback) await this.context.route(`${origin}/**`, async (route) => {
      try {
        const pathname = decodeURIComponent(new URL(route.request().url()).pathname);
        const path = resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
        if (!path.startsWith(dist + sep)) return route.abort();
        const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css', '.json': 'application/json' }[extname(path)] ?? 'application/octet-stream';
        await route.fulfill({ body: await readFile(path), contentType, headers: {
          'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cache-Control': 'no-store',
        } });
      } catch (error) {
        diagnose({ peer: this.name, kind: 'static_asset_failure', path: new URL(route.request().url()).pathname, detail: String(error) });
        await route.abort();
      }
    });
    this.page = await this.newPage();
    return this.success('open', [hostOptions(this.name, startPaused)]);
  }
  async newPage() {
    const page = await this.context.newPage();
    page.on('pageerror', (error) => diagnose({ peer: this.name, kind: 'pageerror', detail: String(error) }));
    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) diagnose({ peer: this.name, kind: `console_${message.type()}`, detail: message.text() });
    });
    page.on('requestfailed', (request) => diagnose({ peer: this.name, kind: 'request_failed', origin: new URL(request.url()).origin, path: new URL(request.url()).pathname, detail: request.failure()?.errorText }));
    page.on('websocket', (socket) => {
      const relay = new URL(socket.url()).origin;
      if (!report.relay_origins.includes(relay)) report.relay_origins.push(relay);
    });
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.workspaceCheck), undefined, { timeout: 60_000 });
    if (!await page.evaluate(() => crossOriginIsolated)) throw new SetupError('Static test page lacks cross-origin isolation');
    return page;
  }
  async call(method, args = [], page = this.page) {
    if (process.argv.includes('--verbose')) console.log(JSON.stringify({ stage: 'host_call', peer: this.name, method }));
    diagnose({ kind: 'host_call', peer: this.name, method });
    const response = await within(page.evaluate(async ({ method, args }) => {
      try { return { ok: true, value: await window.workspaceCheck[method](...args) }; }
      catch (error) { return { ok: false, error: error instanceof Error ? error.message : String(error) }; }
    }, { method, args }), 90_000, `${this.name}.${method}`);
    if (!response || typeof response.ok !== 'boolean') throw new SetupError('Invalid test facade response');
    if (!response.ok) diagnose({ peer: this.name, method, kind: 'host_error', detail: response.error });
    return response;
  }
  async success(method, args = []) {
    const result = await this.call(method, args);
    if (!result.ok) throw new SetupError(`${this.name}.${method} returned a host error`);
    return result.value;
  }
  async snapshot() { return this.success('snapshot'); }
  async command(command) { return this.success('command', [command]); }
  async stop() {
    if (!this.context) return;
    const context = this.context;
    this.context = undefined;
    this.page = undefined;
    // Closing the browser, without a host-level flush, tests already acknowledged commits.
    await within(context.close(), 30_000, `${this.name} browser close`);
    report.browser_closes.push({ peer: this.name, launch: this.launches });
  }
}
function resource(snapshot, id) {
  const matches = snapshot.resources.filter((item) => item.id === id);
  if (matches.length !== 1) throw new BehavioralError(`exactly-one-resource-${id}`);
  return matches[0];
}
function bodies(snapshot, id) { return resource(snapshot, id).heads.map((head) => head.body).sort(); }
function heads(snapshot, id) { return resource(snapshot, id).heads.map((head) => head.id).sort(); }
function equal(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function createWorkspace(peer, id, title) {
  return peer.command({ type: 'create_workspace', operation_id: `create-${id}`, workspace_id: id, title });
}
function createNote(peer, id, body, workspaceId = 'launch') {
  return peer.command({ type: 'create_resource', operation_id: `create-${id}`, workspace_id: workspaceId, resource_id: id, title: id, body });
}
function editNote(peer, operationId, expectedHeads, body) {
  return peer.command({ type: 'edit_resource', operation_id: operationId, workspace_id: 'launch', resource_id: 'brief', expected_heads: expectedHeads, title: 'Brief', body });
}
function rejected(response, message, assertion) {
  if (response.ok) throw new BehavioralError(assertion);
  const detail = response.error.replace(/^Error: /, '');
  if (detail !== message) throw new SetupError(`${assertion}: unexpected error instead of a confirmed rejection`);
  check(assertion, true);
}
async function sync(peer, allowedUnreachable = new Set()) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await peer.success('sync');
    diagnose({ kind: 'sync_report', peer: peer.name, attempt, response });
    let discovered = false;
    for (const contact of response.peers) {
      const kind = contact.state.type;
      if (kind === 'synced' || (kind === 'unreachable' && allowedUnreachable.has(contact.id))) continue;
      if (kind === 'never_synced' || kind === 'unreachable') {
        discovered = true;
        if (kind === 'unreachable') diagnose({ kind: 'sync_unavailable', peer: peer.name, attempt, state: contact.state });
        continue;
      }
      diagnose({ kind: 'sync_unavailable', peer: peer.name, state: contact.state });
      throw new SetupError(`${peer.name} did not complete a real peer exchange (${kind})`);
    }
    if (!discovered) return;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new SetupError(`${peer.name} did not complete reachable peer exchanges in three bounded passes`);
}

async function durability() {
  const peer = new Peer('Durable-device');
  const initial = await peer.start(true);
  await createWorkspace(peer, 'launch', 'Launch review');
  await createNote(peer, 'brief', 'An acknowledged browser note survives a complete restart.');
  const saved = await peer.snapshot();
  check('local-note-has-acknowledged-content', equal(bodies(saved, 'brief'), ['An acknowledged browser note survives a complete restart.']));
  const second = await peer.newPage();
  const duplicate = await peer.call('open', [hostOptions(peer.name, true)], second);
  if (!duplicate.ok && !/^(Error: )?This workspace is already open in another tab\.$/.test(duplicate.error)) {
    throw new SetupError('Duplicate-tab open failed for a reason other than verified existing ownership');
  }
  check('duplicate-tab-rejects-without-stealing-lock', !duplicate.ok);
  await second.close();
  await createNote(peer, 'owner-still-active', 'The original tab remains the owner.');
  check('first-tab-remains-usable-after-duplicate-open', equal(bodies(await peer.snapshot(), 'owner-still-active'), ['The original tab remains the owner.']));
  const beforeRestart = await peer.snapshot();
  await peer.stop();
  const restarted = await peer.start(true);
  check('browser-restart-retains-device-identity', restarted.device_id === initial.device_id);
  check('browser-restart-retains-acknowledged-resources', equal(restarted.resources, beforeRestart.resources));
  check('browser-restart-retains-workspace-history', equal(restarted.workspaces, beforeRestart.workspaces));
  await peer.stop();
}

async function pairing() {
  const a = new Peer('Laptop'), b = new Peer('Phone'), c = new Peer('Other-device');
  const initial = await Promise.all([a.start(), b.start(), c.start()]);
  check('independent-browser-profiles-have-distinct-identities', new Set(initial.map(value => value.device_id)).size === 3);
  await createWorkspace(a, 'launch', 'Shared launch review');
  await createWorkspace(a, 'private', 'Unshared work');
  await createNote(a, 'brief', 'Start with three teams.');
  await createNote(a, 'private-note', 'This content belongs only to the unshared workspace.', 'private');
  const invitation = await a.success('invite', [['launch']]);
  const joined = await b.success('join', [invitation.ticket]);
  check('pairing-shares-only-selected-workspace', equal(joined.workspaces.map(value => value.id).sort(), ['launch']));
  check('pairing-transfers-selected-note', equal(bodies(joined, 'brief'), ['Start with three teams.']));
  check('pairing-excludes-private-resource', joined.resources.every(value => value.id !== 'private-note'));
  rejected(await c.call('join', [invitation.ticket]), 'Invitation could not be accepted; it may be expired or already used.', 'consumed-invite-rejects-a-different-authenticated-browser');
  check('denied-browser-has-no-workspace', equal((await c.snapshot()).workspaces, []));
  rejected(await c.call('join', ['not-a-browser-invitation']), 'Invalid browser invitation.', 'malformed-invitation-is-rejected');
  await createNote(b, 'phone-note', 'Created by the phone browser host.');
  await sync(b); await sync(a);
  check('second-browser-contributes-to-shared-workspace', equal(bodies(await a.snapshot(), 'phone-note'), ['Created by the phone browser host.']));
  await editNote(a, 'laptop-revision', heads(await a.snapshot(), 'brief'), 'Decision from the laptop browser.');
  await sync(a); await sync(b);
  check('first-browser-edit-reaches-second-browser', equal(bodies(await b.snapshot(), 'brief'), ['Decision from the laptop browser.']));
  await Promise.all([a.stop(), b.stop(), c.stop()]);
}

async function offline() {
  const a = new Peer('Editing-laptop'), b = new Peer('Editing-phone');
  const [originalA, originalB] = await Promise.all([a.start(), b.start()]);
  await createWorkspace(a, 'launch', 'Shared offline work');
  await createNote(a, 'brief', 'Shared starting point.');
  const invitation = await a.success('invite', [['launch']]);
  await b.success('join', [invitation.ticket]);
  await sync(b); await sync(a);
  const base = heads(await a.snapshot(), 'brief');
  await Promise.all([a.success('setForeground', [false]), b.success('setForeground', [false])]);
  check('offline-browser-endpoints-report-paused', (await a.snapshot()).network.type === 'paused' && (await b.snapshot()).network.type === 'paused');
  await editNote(a, 'laptop-offline-edit', base, 'Laptop draft.');
  await editNote(b, 'phone-offline-edit', base, 'Phone draft.');
  check('offline-local-work-is-retained', equal(bodies(await a.snapshot(), 'brief'), ['Laptop draft.']) && equal(bodies(await b.snapshot(), 'brief'), ['Phone draft.']));
  await Promise.all([a.success('setForeground', [true]), b.success('setForeground', [true])]);
  await sync(b); await sync(a);
  check('reconnect-preserves-both-offline-revisions', equal(bodies(await a.snapshot(), 'brief'), ['Laptop draft.', 'Phone draft.']));
  const conflictHeads = heads(await b.snapshot(), 'brief');
  check('both-browsers-observe-the-same-conflict', equal(heads(await a.snapshot(), 'brief'), conflictHeads));
  const resolution = { type: 'edit_resource', operation_id: 'reviewed-resolution', workspace_id: 'launch', resource_id: 'brief', expected_heads: conflictHeads, title: 'Brief', body: 'Both choices reviewed.' };
  const resolved = await b.command(resolution);
  check('explicit-reconciliation-applies', resolved.type === 'applied' && resolved.replayed === false);
  await sync(b); await sync(a);
  check('reconciled-revision-reaches-other-browser', equal(bodies(await a.snapshot(), 'brief'), ['Both choices reviewed.']));
  check('reconciliation-retains-previous-writing', equal(resource(await b.snapshot(), 'brief').history.map(value => value.body).sort(), ['Both choices reviewed.', 'Laptop draft.', 'Phone draft.', 'Shared starting point.']));
  const before = (await b.snapshot()).operation_count;
  const stale = await editNote(b, 'stale-edit', base, 'An old view must not overwrite reviewed work.');
  check('stale-edit-reports-conflict', stale.type === 'conflict');
  check('stale-edit-adds-no-operation', (await b.snapshot()).operation_count === before);
  const replay = await b.command(resolution);
  check('exact-command-replay-is-identified', replay.type === 'applied' && replay.replayed === true);
  check('exact-command-replay-adds-no-operation', (await b.snapshot()).operation_count === before);
  const changed = await b.command({ ...resolution, body: 'A different body reusing the same operation identity.' });
  check('changed-command-identity-is-rejected', changed.type === 'rejected');
  check('changed-command-replay-adds-no-operation', (await b.snapshot()).operation_count === before);
  const invalid = { type: 'create_resource', operation_id: 'correctable-note', workspace_id: 'launch', resource_id: 'corrected', title: '', body: 'A corrected command can be submitted after definitive rejection.' };
  const invalidReceipt = await b.command(invalid);
  check('invalid-command-has-definitive-rejection', invalidReceipt.type === 'rejected');
  check('rejected-command-adds-no-operation', (await b.snapshot()).operation_count === before);
  const corrected = await b.command({ ...invalid, title: 'Corrected title' });
  check('corrected-uncommitted-command-can-succeed', corrected.type === 'applied' && corrected.replayed === false);
  await sync(b); await sync(a);
  const beforeRestart = await b.snapshot();
  await b.stop();
  const restarted = await b.start();
  check('paired-browser-restart-retains-identity', restarted.device_id === originalB.device_id);
  check('paired-browser-restart-retains-resource-history', equal(restarted.resources, beforeRestart.resources));
  const replayAfterRestart = await b.command(resolution);
  check('paired-browser-restart-retains-replay-receipt', replayAfterRestart.type === 'applied' && replayAfterRestart.replayed === true);
  await sync(b); await sync(a);
  await a.stop();
  const c = new Peer('Third-browser');
  const originalC = await c.start();
  const memberInvitation = await b.success('invite', [['launch']]);
  const third = await c.success('join', [memberInvitation.ticket]);
  check('member-enrolls-third-browser-while-origin-stopped', equal(bodies(third, 'brief'), ['Both choices reviewed.']));
  await editNote(c, 'third-browser-edit', heads(third, 'brief'), 'Contribution while the origin browser is stopped.');
  await sync(c, new Set([originalA.device_id]));
  const memberAfterThirdEdit = await b.snapshot();
  check('third-browser-contributes-without-origin', equal(bodies(memberAfterThirdEdit, 'brief'), ['Contribution while the origin browser is stopped.']), memberAfterThirdEdit);
  const returning = await a.start();
  check('returning-origin-retains-browser-identity', returning.device_id === originalA.device_id);
  await sync(a); await sync(b); await sync(c);
  const final = await a.snapshot();
  check('origin-catches-up-with-new-member-work', equal(bodies(final, 'brief'), ['Contribution while the origin browser is stopped.']));
  check('three-browser-hosts-converge', equal(heads(final, 'brief'), heads(await b.snapshot(), 'brief')) && equal(heads(final, 'brief'), heads(await c.snapshot(), 'brief')));
  const identities = [originalA.device_id, originalB.device_id, originalC.device_id];
  for (const [peer, id] of [[a, originalA.device_id], [b, originalB.device_id], [c, originalC.device_id]]) {
    check(`browser-members-discover-each-other-${peer.name}`, equal((await peer.snapshot()).peers.map(value => value.id).sort(), identities.filter(value => value !== id).sort()));
  }
  await Promise.all([a.stop(), b.stop(), c.stop()]);
}

try {
  if (!cases.has(selected)) throw new SetupError(`Unknown case: ${selected}`);
  sourceBefore = await sourceHashes();
  report.source_hashes = sourceBefore;
  console.log(JSON.stringify({ stage: 'build', case: selected }));
  await build({ configFile: false, root: checks, logLevel: 'error', base: './', worker: { format: 'es' }, build: { outDir: dist, emptyOutDir: true, target: 'esnext', sourcemap: false }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
  report.assets = await assetHashes();
  if (loopback) await startStaticServer();
  report.hosting = loopback ? 'Strictly loopback static asset server, ephemeral port, isolated responses' : 'Playwright static HTTPS response fulfillment';
  for (const [name, action] of [['durability', durability], ['pairing', pairing], ['offline', offline]]) {
    if (selected !== 'all' && selected !== name) continue;
    console.log(JSON.stringify({ stage: name }));
    await action();
    report.cases.push({ name, passed: true });
  }
  report.passed = true;
} catch (error) {
  report.failure = error instanceof BehavioralError ? { type: 'behavioral', assertion: error.assertion } : { type: 'setup', detail: error instanceof SetupError ? error.message : 'Browser/build/protocol setup failed; see private diagnostics.' };
  diagnostics.push({ kind: 'failure', detail: error.stack ?? String(error) });
} finally {
  for (const peer of peers) {
    try { await peer.stop(); }
    catch (error) { report.passed = false; report.failure = { type: 'setup', detail: 'A browser did not close cleanly' }; diagnostics.push({ kind: 'cleanup', detail: String(error) }); }
  }
  if (staticServer) {
    staticServer.closeAllConnections();
    await new Promise((resolve, reject) => staticServer.close(error => error ? reject(error) : resolve()));
    report.static_server_closed = true;
  }
  if (sourceBefore) {
    report.source_unchanged = equal(sourceBefore, await sourceHashes());
    if (!report.source_unchanged) { report.passed = false; report.failure ??= { type: 'setup', detail: 'Source changed during the browser run' }; }
  }
  report.finished_at = new Date().toISOString();
  await writeFile(join(privateRoot, 'diagnostics.json'), JSON.stringify(diagnostics, null, 2) + '\n');
  for (const peer of peers) await rm(peer.profile, { recursive: true, force: true });
  report.profiles_removed = true;
  await mkdir(evidence, { recursive: true });
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, assertions: report.assertions.length, failure: report.failure, report: join(evidence, 'report.json'), private_diagnostics: join(privateRoot, 'diagnostics.json') }, null, 2));
  if (!report.passed) process.exitCode = 1;
}
