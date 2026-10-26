import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit, firefox } from 'playwright';
import { build } from 'vite';
import qrcode from '../app/node_modules/qrcode-generator/dist/qrcode.mjs';
import { decodeTicket } from '../host/src/invitation.ts';

const checks = dirname(fileURLToPath(import.meta.url));
const source = resolve(checks, '../..');
const option = (name, fallback) => { const index = process.argv.indexOf(name); return index < 0 ? fallback : process.argv[index + 1]; };
const laptopEngine = option('--laptop-engine', 'chromium');
const phoneEngine = option('--phone-engine', 'webkit');
const mode = option('--mode', 'pair');
const laptopState = option('--laptop-state', 'active');
const emptyWorkspace = process.argv.includes('--empty-workspace');
const legacyRetry = process.argv.includes('--legacy-retry');
const exerciseSync = process.argv.includes('--exercise-sync');
const requireCompact = process.argv.includes('--require-compact');
const joinStages = ['checking_invitation', 'connecting_relay', 'contacting_device', 'saving_workspace'];
const invitationBase = option('--invitation-base', 'https://jimlundin.github.io/shared-workspace/');
const output = resolve(option('--evidence', join(tmpdir(), `workspace-pairing-diagnosis-${Date.now()}`)));
const joinTimeout = Number(option('--join-timeout-ms', '45000'));
const engines = { chromium, webkit, firefox };
if (!engines[laptopEngine] || !engines[phoneEngine] || !['pair', 'measure'].includes(mode) || !['active', 'paused'].includes(laptopState)) throw new Error('Unsupported diagnostic engine, mode or laptop state');
if (!Number.isInteger(joinTimeout) || joinTimeout < 1000 || joinTimeout > 120000) throw new Error('Invalid bounded Join timeout');
await mkdir(dirname(output), { recursive: true }); await mkdir(output);
const scratch = await mkdtemp(join(tmpdir(), 'workspace-pairing-diagnosis-'));
const dist = join(scratch, 'dist');
const opened = [];
let server;
let origin;
let stage = 'initializing';
let before;
const began = Date.now();
const report = {
  kind: 'browser-pairing-diagnosis-v1', passed: false, mode, laptop_state: laptopState, empty_workspace: emptyWorkspace,
  laptop_engine: laptopEngine, phone_engine: mode === 'pair' ? phoneEngine : null,
  started_at: new Date().toISOString(), stages: [], assertions: [], engines: [], relay_observations: [], join_attempts: [],
  limits: ['Desktop WebKit is not physical iPhone Safari evidence.', 'This isolates the real BrowserHost facade; the deployed PWA and camera scan are separate paths.', 'An explicitly paused host is a control, not proof that the reported visible laptop was backgrounded.', 'Worker WebSocket events may be absent in WebKit; zero observed frames do not prove no connection attempt.', 'No invitation text, QR image, seed, raw payload, database or browser profile is exported.'],
};
function mark(name, fields = {}) { stage = name; const event = { stage: name, elapsed_ms: Date.now() - began, ...fields }; report.stages.push(event); console.log(JSON.stringify(event)); }
function assert(name, condition) { if (!condition) { const error = new Error(name); error.assertion = name; throw error; } report.assertions.push(name); }
function category(error) {
  const text = String(error);
  if (/executable.*(exist|missing)|host system.*dependenc|browserType.launch|GLIBC|error while loading shared libraries/i.test(text)) return 'engine_environment';
  if (/timeout|timed out|deadline|respond before/i.test(text)) return 'timeout';
  if (/expired|already used|Invitation could not be accepted/i.test(text)) return 'invitation_rejected';
  if (/secure|isola|OPFS|Web Locks|not supported/i.test(text)) return 'browser_capability';
  if (/worker/i.test(text)) return 'worker_failure';
  if (/network|connection|relay|socket|stream/i.test(text)) return 'transport_failure';
  return 'unclassified_error';
}
async function bounded(promise, milliseconds, label) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => { const error = new Error('diagnostic deadline'); error.safeCategory = 'harness_timeout'; error.operation = label; reject(error); }, milliseconds); })]); }
  finally { clearTimeout(timer); }
}
async function files(directory) {
  const result = [];
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, item.name);
    if (item.isDirectory()) result.push(...await files(path)); else if (item.isFile()) result.push(path);
  }
  return result.sort();
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
async function fingerprints() {
  const paths = ['browser/checks/pairing-diagnose.mjs', 'browser/checks/page.ts', 'browser/checks/index.html', 'browser/checks/package-lock.json', 'browser/host/package-lock.json', 'browser/host-wasm/Cargo.lock', 'native/core/src/model.rs', 'native/core/src/work.rs', 'native/ui/src/protocol.ts', 'native/ui/src/meeting-preparation.ts', 'browser/app/src/invitation.ts', 'browser/app/package-lock.json'];
  for (const root of ['browser/host/src', 'browser/host-wasm/src', 'browser/host-wasm/pkg']) for (const path of await files(join(source, root))) paths.push(relative(source, path));
  return Object.fromEntries(await Promise.all(paths.sort().map(async path => [path, hash(await readFile(join(source, path)))])));
}
async function serve() {
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const path = resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
      if (!path.startsWith(dist + sep)) { response.writeHead(403).end(); return; }
      response.writeHead(200, {
        'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css' }[extname(path)] ?? 'application/octet-stream',
        'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cache-Control': 'no-store',
      }).end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;
}
async function invoke(peer, method, args = [], deadline = 90000) {
  mark(`${peer.role}.${method}.begin`, { traffic: { ...peer.traffic } });
  const started = Date.now();
  let result;
  try { result = await bounded(peer.page.evaluate(async ({ method, args }) => {
    try { return { ok: true, value: await window.workspaceCheck[method](...args) }; }
    catch (error) { return { ok: false, error: error instanceof Error ? error.message : String(error) }; }
  }, { method, args }), deadline, `${peer.role}.${method}`); }
  finally {
    if (method === 'join') {
      let events = [], receiptStageCount = null;
      try {
        const observed = await bounded(peer.page.evaluate(() => ({ events: window.workspaceCheck.joinProgress(), receiptStageCount: window.workspaceCheck.joinReceiptStageCount() })), 2000, 'join.progress.read');
        events = observed.events; receiptStageCount = observed.receiptStageCount;
      } catch { /* The browser may have crashed or become unavailable. */ }
      const stages = events.map(value => value.stage).filter(value => joinStages.includes(value));
      peer.lastJoinStage = stages.at(-1) ?? null;
      report.join_attempts.push({ role: peer.role, stages, last_stage: peer.lastJoinStage, elapsed_ms: events.map(value => value.at_ms - started), receipt_received: result?.ok === true, stages_observed_at_receipt: receiptStageCount });
      mark(`${peer.role}.join.progress`, { observed_stages: stages, last_stage: peer.lastJoinStage });
      if (result?.ok) {
        assert(`join-receipt-follows-saving-stage-${report.join_attempts.length}`, JSON.stringify(stages) === JSON.stringify(joinStages));
        assert(`join-progress-precedes-receipt-${report.join_attempts.length}`, receiptStageCount === joinStages.length);
      }
    }
  }
  if (!result.ok) {
    const error = new Error('Host returned an error'); error.safeCategory = category(result.error); error.operation = `${peer.role}.${method}`;
    mark(`${peer.role}.${method}.failed`, { duration_ms: Date.now() - started, category: error.safeCategory, traffic: { ...peer.traffic } });
    throw error;
  }
  mark(`${peer.role}.${method}.completed`, { duration_ms: Date.now() - started, traffic: { ...peer.traffic } });
  return result.value;
}
async function launch(role, engine) {
  mark(`${role}.browser.launch`, { engine });
  const profile = join(scratch, `${role}-profile`);
  const context = await engines[engine].launchPersistentContext(profile, { headless: true });
  const peer = { role, engine, context, profile, closed: false, traffic: { sockets: 0, sent_frames: 0, sent_bytes: 0, received_frames: 0, received_bytes: 0, socket_errors: 0, socket_closes: 0, page_errors: 0 } };
  opened.push(peer);
  peer.page = await context.newPage();
  peer.page.on('pageerror', () => { peer.traffic.page_errors++; });
  peer.page.on('websocket', socket => {
    peer.traffic.sockets++;
    const relay = { role, origin: new URL(socket.url()).origin };
    if (!report.relay_observations.some(value => value.role === role && value.origin === relay.origin)) report.relay_observations.push(relay);
    socket.on('framesent', frame => { peer.traffic.sent_frames++; peer.traffic.sent_bytes += typeof frame.payload === 'string' ? Buffer.byteLength(frame.payload) : frame.payload.length; });
    socket.on('framereceived', frame => { peer.traffic.received_frames++; peer.traffic.received_bytes += typeof frame.payload === 'string' ? Buffer.byteLength(frame.payload) : frame.payload.length; });
    socket.on('socketerror', () => { peer.traffic.socket_errors++; }); socket.on('close', () => { peer.traffic.socket_closes++; });
  });
  await peer.page.goto(origin);
  await peer.page.waitForFunction(() => Boolean(window.workspaceCheck), undefined, { timeout: 60000 });
  const capabilities = await peer.page.evaluate(() => ({ secure_context: isSecureContext, cross_origin_isolated: crossOriginIsolated, opfs: Boolean(navigator.storage?.getDirectory), web_locks: Boolean(navigator.locks), visibility: document.visibilityState }));
  report.engines.push({ role, engine, browser_version: context.browser().version(), capabilities });
  mark(`${role}.browser.ready`, { capabilities });
  const snapshot = await invoke(peer, 'open', [{ deviceName: 'Web browser', databaseName: 'pairing-diagnosis.db', baseUrl: invitationBase }]);
  mark(`${role}.host.ready`, { network: snapshot.network.type, workspaces: snapshot.workspaces.length });
  return peer;
}
function invitationMetrics(ticket) {
  const parsed = new URL(ticket);
  const fragment = new URLSearchParams(parsed.hash.slice(1)).get('invite');
  const qr = qrcode(0, 'M'); qr.addData(ticket); qr.make();
  return { url_bytes: Buffer.byteLength(ticket), url_characters: ticket.length, encoded_ticket_bytes: fragment === null ? null : Buffer.byteLength(fragment), qr_error_correction: 'M', qr_modules: qr.getModuleCount(), qr_version: (qr.getModuleCount() - 17) / 4, workspace_count: 1, workspace_id_format: 'UUID', device_name: 'Web browser' };
}
function legacyInvitation(ticket) {
  const decoded = decodeTicket(ticket);
  function oldRouting(value) {
    if (typeof value === 'string' && value.startsWith('https://')) { const url = new URL(value); if (!url.hostname.endsWith('.')) url.hostname += '.'; return url.href; }
    if (Array.isArray(value)) return value.map(oldRouting);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, oldRouting(item)]));
    return value;
  }
  const record = { ...decoded, inviter: { ...decoded.inviter, address: { ...decoded.inviter.address, addrs: oldRouting(decoded.inviter.address.addrs) } } };
  const encoded = 'workspace-browser-v1.' + Buffer.from(JSON.stringify(record)).toString('base64url');
  const url = new URL(invitationBase); url.hash = new URLSearchParams({ invite: encoded }).toString();
  return url.href;
}
function note(snapshot, resource) { return snapshot.resources.find(value => value.id === resource); }
function headIds(snapshot, resource) { return note(snapshot, resource).heads.map(value => value.id).sort(); }
function headBodies(snapshot, resource) { return note(snapshot, resource).heads.map(value => value.body).sort(); }
async function edit(peer, workspace, resource, expected, body) {
  return invoke(peer, 'command', [{ type: 'edit_resource', operation_id: randomUUID(), workspace_id: workspace, resource_id: resource, expected_heads: expected, title: 'Diagnostic note', body }]);
}
async function mixedEngineWork(laptop, phone, workspace, resource, joined) {
  if (emptyWorkspace) throw new Error('Mixed-engine edits require the diagnostic note');
  const identity = joined.device_id;
  await edit(phone, workspace, resource, headIds(joined, resource), 'Written by the receiving browser.');
  await invoke(phone, 'sync');
  const first = await invoke(laptop, 'snapshot');
  assert('receiving-engine-edit-reaches-inviter', JSON.stringify(headBodies(first, resource)) === JSON.stringify(['Written by the receiving browser.']));
  const heads = headIds(first, resource);
  await invoke(laptop, 'setForeground', [false]); await invoke(phone, 'setForeground', [false]);
  await edit(laptop, workspace, resource, heads, 'Offline laptop wording.');
  await edit(phone, workspace, resource, heads, 'Offline receiving-browser wording.');
  await invoke(laptop, 'setForeground', [true]); await invoke(phone, 'setForeground', [true]);
  await invoke(phone, 'sync'); await invoke(laptop, 'sync');
  const conflict = await invoke(phone, 'snapshot');
  assert('mixed-engines-preserve-offline-conflict', JSON.stringify(headBodies(conflict, resource)) === JSON.stringify(['Offline laptop wording.', 'Offline receiving-browser wording.']));
  await edit(phone, workspace, resource, headIds(conflict, resource), 'Both offline changes reviewed.');
  await invoke(phone, 'sync'); await invoke(laptop, 'sync');
  const final = await invoke(laptop, 'snapshot');
  assert('mixed-engine-reconciliation-reaches-inviter', JSON.stringify(headBodies(final, resource)) === JSON.stringify(['Both offline changes reviewed.']));
  const retained = await invoke(phone, 'snapshot');
  await phone.context.close(); phone.closed = true;
  const reopened = await launch('phone', phone.engine);
  const recovered = await invoke(reopened, 'snapshot');
  assert('receiving-browser-restart-retains-identity', recovered.device_id === identity);
  assert('receiving-browser-restart-retains-history', JSON.stringify(recovered.resources) === JSON.stringify(retained.resources));
  const reconnected = await invoke(reopened, 'sync');
  assert('restarted-receiving-browser-exchanges-with-inviter', reconnected.peers.length > 0 && reconnected.peers.every(value => value.state.type === 'synced'));
}

try {
  before = await fingerprints(); report.source_hashes = before;
  mark('fixture.build');
  await build({ configFile: false, root: checks, logLevel: 'error', base: './', worker: { format: 'es' }, build: { outDir: dist, emptyOutDir: true, target: 'esnext' }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
  report.assets = Object.fromEntries(await Promise.all((await files(dist)).map(async path => [relative(dist, path), hash(await readFile(path))])));
  await serve();
  const laptop = await launch('laptop', laptopEngine);
  const workspace = randomUUID(), resource = randomUUID();
  await invoke(laptop, 'command', [{ type: 'create_workspace', operation_id: randomUUID(), workspace_id: workspace, title: 'Pairing diagnosis' }]);
  if (!emptyWorkspace) await invoke(laptop, 'command', [{ type: 'create_resource', operation_id: randomUUID(), workspace_id: workspace, resource_id: resource, title: 'Diagnostic note', body: 'This note confirms a completed pairing exchange.' }]);
  const invite = await invoke(laptop, 'invite', [[workspace]]);
  report.invitation = invitationMetrics(invite.ticket);
  mark('invitation.measured', report.invitation);
  if (requireCompact) {
    assert('single-workspace-invitation-stays-within-300-bytes', report.invitation.url_bytes <= 300);
    assert('single-workspace-invitation-qr-stays-within-69-modules', report.invitation.qr_modules <= 69);
  }
  if (mode === 'pair') {
    const phone = await launch('phone', phoneEngine);
    if (laptopState === 'paused') await invoke(laptop, 'setForeground', [false]);
    const state = await invoke(laptop, 'snapshot');
    mark('laptop.before_join', { network: state.network.type, visibility: await laptop.page.evaluate(() => document.visibilityState) });
    const joined = await invoke(phone, 'join', [invite.ticket], joinTimeout);
    assert('join-transfers-selected-workspace', joined.workspaces.some(value => value.id === workspace));
    if (!emptyWorkspace) assert('join-transfers-exact-note', joined.resources.some(value => value.id === resource && value.heads.some(head => head.body === 'This note confirms a completed pairing exchange.')));
    const member = await invoke(laptop, 'snapshot');
    assert('inviter-confirms-joining-member', member.workspaces.find(value => value.id === workspace).members.includes(joined.device_id));
    mark('pairing.confirmed', { selected_workspaces: joined.workspaces.length, resources: joined.resources.length });
    if (legacyRetry) {
      const retried = await invoke(phone, 'join', [legacyInvitation(invite.ticket)], joinTimeout);
      assert('legacy-json-invite-with-dotted-relay-remains-compatible', retried.workspaces.some(value => value.id === workspace));
      assert('legacy-invite-retry-adds-no-operation', retried.operation_count === joined.operation_count);
    }
    if (exerciseSync) await mixedEngineWork(laptop, phone, workspace, resource, joined);
  }
  report.passed = true;
} catch (error) {
  report.failure = { stage, operation: error.operation ?? null, category: error.assertion ? 'behavioral' : error.safeCategory ?? category(error), assertion: error.assertion ?? null, last_join_stage: report.join_attempts.at(-1)?.last_stage ?? null };
  mark('diagnosis.failed', { failed_stage: report.failure.stage, operation: report.failure.operation, category: report.failure.category, assertion: report.failure.assertion });
} finally {
  report.final_traffic = opened.map(peer => ({ role: peer.role, ...peer.traffic }));
  report.browser_cleanup = [];
  for (const peer of opened.reverse()) {
    try { if (!peer.closed) await bounded(peer.context.close(), 30000, `${peer.role}.browser.close`); peer.closed = true; report.browser_cleanup.push({ role: peer.role, closed: true }); }
    catch { report.passed = false; report.browser_cleanup.push({ role: peer.role, closed: false }); }
    await rm(peer.profile, { recursive: true, force: true });
  }
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); report.static_server_closed = true; }
  report.profiles_removed = true;
  if (before) { report.source_unchanged = JSON.stringify(before) === JSON.stringify(await fingerprints()); if (!report.source_unchanged) { report.passed = false; report.integrity_failure = 'Source changed during diagnosis'; } }
  report.finished_at = new Date().toISOString(); report.duration_ms = Date.now() - began;
  await writeFile(join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  await rm(scratch, { recursive: true, force: true });
  console.log(JSON.stringify({ passed: report.passed, mode, failure: report.failure, invitation: report.invitation, report: join(output, 'report.json') }, null, 2));
  if (!report.passed) process.exitCode = 1;
}
