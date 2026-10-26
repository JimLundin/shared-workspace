import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { isDeepStrictEqual, parseArgs } from 'node:util';
import { chromium } from 'playwright';
import { build } from 'vite';

const checks = import.meta.dirname, root = resolve(checks, '../..');
const LEGACY = '05ab68532fc291dffe523543c5b1642f81111b13';
const { values: options } = parseArgs({ options: { evidence: { type: 'string' }, 'target-cache': { type: 'string' }, 'wasm-bindgen': { type: 'string' } } });
const evidence = resolve(options.evidence ?? join(checks, 'evidence', 'work-compatibility-' + Date.now()));
await mkdir(dirname(evidence), { recursive: true }); await mkdir(evidence);
const scratch = await mkdtemp(join(tmpdir(), 'workspace-work-compatibility-'));
const trees = { current: join(scratch, 'current'), legacy: join(scratch, 'legacy') }, dist = { current: join(scratch, 'current-dist'), legacy: join(scratch, 'legacy-dist') };
const fixtureNames = ['work-compatibility.mjs', 'work-compatibility-fixture.html', 'work-compatibility-fixture.ts', 'work-compatibility-worker.ts', 'WORK-COMPATIBILITY.md'];
const peers = [], relayOrigins = new Set();
const hash = value => createHash('sha256').update(value).digest('hex');
const report = { kind: 'work-wire-compatibility-v1', started_at: new Date().toISOString(), passed: false, legacy_commit: LEGACY,
  assertions: [], stages: [], tools: [], transport: [], launches: [], diagnostics: [], provider_requests: 0,
  scope: 'Production old/new HostOwner, BrowserStore, Domain and Iroh in dedicated workers, real Turso OPFS, separate persistent Chromium profiles. Frozen old sources are rebuilt with their own Domain/WASM.',
  limits: ['Fixture worker adds transparent transport observers; production BrowserHost facade and renderer are outside this check.',
    'Synthetic notes and Work only; no API key, business data, or provider request.', 'Desktop Chromium evidence is not physical iPhone validation.',
    'Legacy sync refusal prevents unsupported outgoing Work; it does not promise rollback of authorized incoming note history.'],
};
let server, origin, stageName = 'setup', sourceBefore, legacyBefore, currentBefore;
function check(name, condition) { if (!condition) { const error = new Error(name); error.name = 'AssertionFailure'; throw error; } report.assertions.push(name); }
async function stage(name, action) { stageName = name; process.stdout.write(JSON.stringify({ stage: name }) + '\n'); const began = Date.now(); await action(); report.stages.push({ name, elapsed_ms: Date.now() - began }); }
async function files(folder) {
  const result = [];
  for (const item of await readdir(folder, { withFileTypes: true })) {
    const path = join(folder, item.name);
    if (item.isDirectory()) result.push(...await files(path)); else if (item.isFile()) result.push(path);
  }
  return result.sort();
}
async function sourcePaths(folder, legacy = false) {
  const paths = ['browser/host/package.json', 'browser/host/package-lock.json', 'browser/host-wasm/Cargo.toml', 'browser/host-wasm/Cargo.lock', 'browser/host-wasm/build.sh', 'native/ui/package.json', 'native/ui/src/protocol.ts'];
  if (!legacy) paths.push('native/ui/src/meeting-preparation.ts', ...fixtureNames.map(name => 'browser/checks/' + name), 'browser/checks/package.json', 'browser/checks/package-lock.json');
  for (const name of ['browser/host/src', 'browser/host-wasm/src', 'native/core/src']) paths.push(...(await files(join(folder, name))).map(path => relative(folder, path)));
  return paths.sort();
}
async function fingerprints(folder, paths) { return Object.fromEntries(await Promise.all(paths.map(async path => [path, hash(await readFile(join(folder, path)))]))); }
async function captureCurrent() {
  const paths = await sourcePaths(root); paths.push(...(await files(join(root, 'browser/host-wasm/pkg'))).map(path => relative(root, path))); paths.sort();
  const captured = Object.fromEntries(await Promise.all(paths.map(async path => [path, await readFile(join(root, path))])));
  sourceBefore = Object.fromEntries(Object.entries(captured).map(([path, value]) => [path, hash(value)])); report.source_hashes = sourceBefore;
  for (const [path, value] of Object.entries(captured)) { await mkdir(dirname(join(trees.current, path)), { recursive: true }); await writeFile(join(trees.current, path), value); }
  currentBefore = await fingerprints(trees.current, paths); check('current-source-capture-exact', isDeepStrictEqual(sourceBefore, currentBefore));
}
async function command(label, executable, args, configuration = {}) {
  process.stdout.write(JSON.stringify({ tool: label }) + '\n');
  const output = await new Promise((done, failed) => {
    const child = spawn(executable, args, { cwd: root, ...configuration, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = []; let length = 0, failure;
    const terminate = message => {
      failure ??= new Error(message);
      try { if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGTERM'); else child.kill('SIGTERM'); } catch { /* Closing may already be in progress. */ }
    };
    const collect = chunk => { length += chunk.length; if (length > 64 * 1024 * 1024) terminate(label + ' output exceeded limit'); else chunks.push(chunk); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    const timer = setTimeout(() => terminate(label + ' exceeded build deadline'), 15 * 60_000);
    child.once('error', error => { clearTimeout(timer); failed(error); });
    child.once('close', async (code, signal) => {
      clearTimeout(timer); const data = Buffer.concat(chunks); report.tools.push({ label, exit_code: code, signal, output_sha256: hash(data) });
      try { await writeFile(join(evidence, label + '.log'), data); } catch (error) { failed(error); return; }
      if (!failure && code === 0 && signal === null) done(data); else failed(failure ?? new Error(label + ' failed; see isolated build log'));
    });
  });
  return output;
}
async function prepareLegacy() {
  await mkdir(trees.legacy, { recursive: true });
  const archive = join(scratch, 'legacy.tar');
  await command('archive-frozen-legacy', 'git', ['archive', '--format=tar', '--output=' + archive, LEGACY, 'browser/host', 'browser/host-wasm', 'native/core/src', 'native/ui/src', 'native/ui/package.json']);
  await command('extract-frozen-legacy', 'tar', ['-xf', archive, '-C', trees.legacy]);
  const legacyPaths = await sourcePaths(trees.legacy, true);
  legacyBefore = await fingerprints(trees.legacy, legacyPaths); report.legacy_source_hashes = legacyBefore;
  for (const name of fixtureNames) { await mkdir(join(trees.legacy, 'browser/checks'), { recursive: true }); await writeFile(join(trees.legacy, 'browser/checks', name), await readFile(join(trees.current, 'browser/checks', name))); }
  const target = join(scratch, 'legacy-target');
  if (options['target-cache']) await command('copy-build-cache', 'cp', ['-a', '--reflink=auto', resolve(options['target-cache']), target]);
  else await mkdir(target);
  const manifest = join(trees.legacy, 'browser/host-wasm/Cargo.toml');
  await command('clean-legacy-crate-output', 'cargo', ['clean', '--manifest-path', manifest, '--target-dir', target, '--target', 'wasm32-unknown-unknown', '--release', '--package', 'browser-workspace-host']);
  // Copied caches can retain release fingerprints whose mtimes are newer than a
  // git archive. Remove every local-crate fingerprint/output from this private
  // target explicitly; only dependency artifacts may be reused.
  report.removed_legacy_crate_artifacts = 0;
  for (const prefix of ['release', 'wasm32-unknown-unknown/release']) {
    for (const suffix of ['', '/.fingerprint', '/deps']) {
      const folder = join(target, prefix + suffix);
      for (const item of await readdir(folder, { withFileTypes: true }).catch(() => [])) {
        if (!/^browser[-_]workspace[-_]host(?:[-_.]|$)/.test(item.name)) continue;
        await rm(join(folder, item.name), { recursive: true, force: true }); report.removed_legacy_crate_artifacts++;
      }
    }
  }
  const bindgen = options['wasm-bindgen'] ?? process.env.WASM_BINDGEN ?? 'wasm-bindgen';
  report.bindgen_version = (await command('bindgen-version', bindgen, ['--version'])).toString('utf8').trim();
  const log = await command('build-frozen-legacy-wasm', 'bash', [join(trees.legacy, 'browser/host-wasm/build.sh')], { env: { ...process.env, HOST_WASM_TARGET_DIR: target, WASM_BINDGEN: bindgen } });
  await writeFile(join(evidence, 'legacy-build.log'), log);
  report.legacy_wasm_hashes = await fingerprints(trees.legacy, (await files(join(trees.legacy, 'browser/host-wasm/pkg'))).map(path => relative(trees.legacy, path)));
  check('legacy-domain-wasm-differs-from-work-aware-domain', report.legacy_wasm_hashes['browser/host-wasm/pkg/browser_workspace_host_bg.wasm'] !== sourceBefore['browser/host-wasm/pkg/browser_workspace_host_bg.wasm']);
  check('legacy-source-unchanged-by-build', isDeepStrictEqual(legacyBefore, await fingerprints(trees.legacy, legacyPaths)));
}
async function compile() {
  for (const version of ['current', 'legacy']) {
    const tree = trees[version];
    await symlink(join(root, 'browser/host/node_modules'), join(tree, 'browser/host/node_modules'), 'dir');
    const declared = JSON.parse(await readFile(join(tree, 'browser/host/package.json'), 'utf8')).dependencies['@tursodatabase/database-wasm'];
    const installed = JSON.parse(await readFile(join(root, 'browser/host/node_modules/@tursodatabase/database-wasm/package.json'), 'utf8')).version;
    report.database_versions ??= {}; report.database_versions[version] = installed;
    check(version + '-actual-turso-package-matches-pin', declared === installed);
    await build({ configFile: false, root: join(tree, 'browser/checks'), cacheDir: join(scratch, version + '-vite-cache'), logLevel: 'error', base: './', worker: { format: 'es' },
      build: { outDir: dist[version], emptyOutDir: true, target: 'esnext', sourcemap: false, rollupOptions: { input: join(tree, 'browser/checks/work-compatibility-fixture.html') } }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
  }
  report.asset_hashes = {};
  for (const version of ['current', 'legacy']) report.asset_hashes[version] = await fingerprints(dist[version], (await files(dist[version])).map(path => relative(dist[version], path)));
}
async function serve() {
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname), version = pathname.split('/')[1];
      if (version !== 'current' && version !== 'legacy') { response.writeHead(404).end(); return; }
      const suffix = pathname.slice(version.length + 1), path = resolve(dist[version], '.' + (suffix === '/' ? '/work-compatibility-fixture.html' : suffix));
      if (!path.startsWith(dist[version] + sep)) { response.writeHead(403).end(); return; }
      response.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm' }[extname(path)] ?? 'application/octet-stream',
        'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin', 'Cache-Control': 'no-store' }).end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((done, failed) => { server.once('error', failed); server.listen(0, '127.0.0.1', done); }); origin = 'http://127.0.0.1:' + server.address().port;
}
async function deadline(promise, label, milliseconds = 90_000) { let timer; try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label + ' deadline')), milliseconds); })]); } finally { clearTimeout(timer); } }
class Peer {
  constructor(name, version) { this.name = name; this.version = version; peers.push(this); }
  async start() {
    this.context = await chromium.launchPersistentContext(join(scratch, 'profile-' + this.name), { headless: true });
    await this.context.route('https://api.openai.com/**', async route => { report.provider_requests++; await route.abort(); });
    this.page = await this.context.newPage(); this.page.on('websocket', socket => { try { relayOrigins.add(new URL(socket.url()).origin); } catch {} });
    await this.page.goto(origin + '/' + this.version + '/'); await this.page.waitForFunction(() => Boolean(window.workspaceCompatibilityCheck));
    check(this.name + '-secure-isolated-context', await this.page.evaluate(() => isSecureContext && crossOriginIsolated));
    report.launches.push({ peer: this.name, version: this.version, browser: this.context.browser().version() });
    return this.call('open', { deviceName: this.name, databaseName: 'compatibility.db', baseUrl: origin + '/' + this.version + '/', startPaused: false });
  }
  tryCall(method, value) { return deadline(this.page.evaluate(async ({ method, value }) => { try { return { ok: true, value: await window.workspaceCompatibilityCheck.call(method, value) }; } catch (error) { return { ok: false, error_name: error.name }; } }, { method, value }), this.name + '.' + method); }
  async call(method, value) { const result = await this.tryCall(method, value); if (!result.ok) throw new Error(this.name + '.' + method + ' failed (' + result.error_name + ')'); return result.value; }
  snapshot() { return this.call('snapshot'); }
  async command(value) { const receipt = await this.call('command', value); check(this.name + '-applied-' + value.operation_id, receipt.type === 'applied'); return receipt; }
  async record(label) { const observations = await this.call('observations'); report.transport.push({ peer: this.name, stage: label, observations }); return observations; }
  async stop() { if (!this.context) return; try { await this.call('close'); } finally { await deadline(this.context.close(), 'browser-close', 30_000); this.context = undefined; } }
}
const workspace = (snapshot, id) => snapshot.workspaces.find(value => value.id === id);
const resource = (snapshot, id) => snapshot.resources.find(value => value.id === id);
async function createWorkspace(peer, id) { await peer.command({ type: 'create_workspace', operation_id: id + '-create', workspace_id: id, title: 'Synthetic ' + id }); }
async function note(peer, workspaceId, resourceId, revision, body) { await peer.command({ type: 'create_resource', operation_id: revision, workspace_id: workspaceId, resource_id: resourceId, title: 'Synthetic transcript', body }); }
async function work(peer, workspaceId, id, sourceId, revision) { await peer.command({ type: 'create_work', operation_id: id + '-create', workspace_id: workspaceId, resource_id: id, title: 'Synthetic meeting', outcome: 'Review synthetic meeting notes.', sources: [{ workspace_id: workspaceId, resource_id: sourceId, revision_id: revision }] }); }

try {
  await stage('capture-current-and-frozen-legacy-sources', async () => { await captureCurrent(); await prepareLegacy(); });
  await stage('compile-isolated-hosts-and-start-loopback-server', async () => { await compile(); await serve(); });
  const a = new Peer('new-inviter', 'current'), b = new Peer('legacy-member', 'legacy'), c = new Peer('new-member', 'current');
  let initialB, initialC, ticket;
  await stage('open-three-real-turso-iroh-hosts', async () => { const snapshots = await Promise.all([a.start(), b.start(), c.start()]); [, initialB, initialC] = snapshots; check('all-profiles-start-empty', snapshots.every(value => value.resources.length === 0)); });
  await stage('legacy-client-joins-and-synchronizes-note-only-new-host', async () => {
    await createWorkspace(a, 'notes'); await note(a, 'notes', 'note', 'note-original', 'Synthetic original note.');
    const invite = await a.call('invite', ['notes']); const joined = await b.call('join', invite.ticket);
    check('legacy-join-receives-note', resource(joined, 'note')?.heads[0]?.body === 'Synthetic original note.');
    await b.command({ type: 'edit_resource', operation_id: 'legacy-edit', workspace_id: 'notes', resource_id: 'note', expected_heads: ['note-original'], title: 'Legacy edited note', body: 'Synthetic edit from the legacy host.' });
    await b.call('sync'); check('legacy-initiated-note-sync-reaches-new-host', resource(await a.snapshot(), 'note')?.heads[0]?.body === 'Synthetic edit from the legacy host.');
    await a.command({ type: 'edit_resource', operation_id: 'new-edit', workspace_id: 'notes', resource_id: 'note', expected_heads: ['legacy-edit'], title: 'New edited note', body: 'Synthetic edit from the new host.' });
    await b.call('sync'); check('legacy-pull-receives-new-note-edit', resource(await b.snapshot(), 'note')?.heads[0]?.body === 'Synthetic edit from the new host.');
    const audit = await b.record('legacy-note-only'); check('legacy-note-wire-has-no-work-capability-field', audit.filter(item => item.direction === 'outgoing-request' && ['pair', 'sync'].includes(item.type)).every(item => !item.fields.includes('work')));
  });
  await stage('legacy-work-join-refused-before-grant-invitation-or-payload', async () => {
    await createWorkspace(a, 'meetings'); await note(a, 'meetings', 'transcript', 'transcript-original', 'Synthetic meeting discussed testing.');
    await work(a, 'meetings', 'meeting', 'transcript', 'transcript-original');
    ticket = (await a.call('invite', ['meetings'])).ticket;
    const before = await a.snapshot(); await Promise.all([a.call('clear_observations'), b.call('clear_observations')]);
    const rejected = await b.tryCall('join', ticket), after = await a.snapshot();
    check('legacy-work-join-is-rejected', !rejected.ok);
    check('legacy-work-join-appends-no-grant-operation', before.operation_count === after.operation_count);
    check('legacy-work-join-does-not-change-workspace-members', isDeepStrictEqual(workspace(before, 'meetings'), workspace(after, 'meetings')) && !workspace(after, 'meetings').members.includes(initialB.device_id));
    check('legacy-has-no-unsupported-workspace', !workspace(await b.snapshot(), 'meetings'));
    const observed = await b.record('legacy-work-join-refused');
    check('legacy-work-join-receives-rejected-envelope', observed.some(item => item.direction === 'incoming-reply' && item.type === 'rejected'));
    check('legacy-work-join-receives-zero-work-payload', observed.every(item => item.work_operations === 0));
    await a.record('inviter-refuses-legacy-work');
  });
  await stage('supported-peer-reuses-rejected-invitation-and-receives-work', async () => {
    const joined = await c.call('join', ticket);
    check('rejected-invitation-was-not-consumed', Boolean(resource(joined, 'meeting')?.heads[0]?.work));
    const current = await a.snapshot(); check('only-supported-peer-is-granted-workspace', workspace(current, 'meetings').members.includes(initialC.device_id) && !workspace(current, 'meetings').members.includes(initialB.device_id));
    const observed = await c.record('supported-work-join');
    check('supported-pair-declares-work-per-request', observed.some(item => item.direction === 'outgoing-request' && item.type === 'pair' && item.work_declared));
    check('work-probe-is-scope-free', observed.filter(item => item.type === 'work_capabilities' && item.direction === 'outgoing-request').every(item => isDeepStrictEqual(item.fields, ['type'])));
    check('supported-peer-receives-work-over-real-transport', observed.some(item => item.direction === 'incoming-reply' && item.type === 'paired' && item.work_operations > 0));
  });
  await stage('legacy-member-sync-refused-after-new-peer-adds-work', async () => {
    await work(a, 'notes', 'new-meeting-in-notes', 'note', 'new-edit');
    await b.call('clear_observations'); const sync = await b.call('sync');
    check('legacy-member-sync-reports-unreachable', sync.peers.some(item => item.state.type === 'unreachable'));
    check('legacy-member-never-imports-work', !resource(await b.snapshot(), 'new-meeting-in-notes'));
    const observed = await b.record('legacy-member-work-sync-refused');
    check('legacy-member-sync-receives-rejected-envelope', observed.some(item => item.direction === 'incoming-reply' && item.type === 'rejected'));
    check('legacy-member-sync-exchanges-zero-work-payload', observed.every(item => item.work_operations === 0));
  });
  await stage('new-peers-still-synchronize-work-in-both-directions', async () => {
    await c.call('join', (await a.call('invite', ['notes'])).ticket);
    check('supported-peer-receives-work-in-formerly-note-only-workspace', Boolean(resource(await c.snapshot(), 'new-meeting-in-notes')?.heads[0]?.work));
    await work(c, 'meetings', 'member-created-work', 'transcript', 'transcript-original'); await c.call('sync');
    check('supported-member-work-reaches-inviter', Boolean(resource(await a.snapshot(), 'member-created-work')?.heads[0]?.work));
    const observed = await c.record('supported-work-sync');
    check('supported-sync-carries-work-with-request-capability', observed.some(item => item.direction === 'outgoing-request' && item.type === 'sync' && item.work_declared && item.work_operations > 0));
  });
  await stage('new-client-falls-back-to-legacy-inviter-for-notes', async () => {
    await createWorkspace(b, 'legacy-owned'); await note(b, 'legacy-owned', 'legacy-owned-note', 'legacy-owned-note-create', 'Synthetic legacy-owned note.');
    await c.call('clear_observations'); await c.call('join', (await b.call('invite', ['legacy-owned'])).ticket);
    check('new-client-can-join-legacy-note-inviter', Boolean(resource(await c.snapshot(), 'legacy-owned-note')));
    const observed = await c.record('new-client-legacy-inviter');
    check('legacy-inviter-pair-omits-unknown-work-field', observed.some(item => item.direction === 'outgoing-request' && item.type === 'pair' && !item.fields.includes('work')));
  });
  check('no-provider-traffic', report.provider_requests === 0); report.passed = true;
} catch (error) { report.failure = { stage: stageName, name: error.name, message: error.message }; process.exitCode = 1; }
finally {
  for (const peer of peers.reverse()) {
    try { if (peer.page) report.diagnostics.push({ peer: peer.name, ...await peer.page.evaluate(() => window.workspaceCompatibilityCheck?.diagnostics()) }); } catch { /* A closed page has no remaining worker diagnostic. */ }
    try { await peer.stop(); } catch { report.cleanup_error = 'A browser did not close cleanly.'; report.passed = false; process.exitCode = 1; }
  }
  if (server) await new Promise(done => server.close(done));
  try {
    if (sourceBefore) { report.final_source_hashes = await fingerprints(root, Object.keys(sourceBefore)); report.source_unchanged = isDeepStrictEqual(sourceBefore, report.final_source_hashes); }
    if (currentBefore) report.current_capture_unchanged = isDeepStrictEqual(currentBefore, await fingerprints(trees.current, Object.keys(currentBefore)));
    if (legacyBefore) report.legacy_capture_unchanged = isDeepStrictEqual(legacyBefore, await fingerprints(trees.legacy, Object.keys(legacyBefore)));
    if (!report.source_unchanged || !report.current_capture_unchanged || !report.legacy_capture_unchanged) { report.passed = false; process.exitCode = 1; }
  } catch { report.passed = false; report.hash_check_failed = true; process.exitCode = 1; }
  report.relay_origins = [...relayOrigins].sort();
  try { await rm(scratch, { recursive: true, force: true }); report.temporary_copies_removed = true; } catch { report.passed = false; report.temporary_copies_removed = false; process.exitCode = 1; }
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ passed: report.passed, assertions: report.assertions.length, evidence, failure: report.failure }) + '\n');
}
