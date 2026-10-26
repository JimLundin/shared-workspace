import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { isDeepStrictEqual, parseArgs } from 'node:util';
import { chromium, webkit } from 'playwright';
import { build } from 'vite';

const checks = import.meta.dirname, source = resolve(checks, '../..');
const { values: options } = parseArgs({ options: { evidence: { type: 'string' }, inviter: { type: 'string', default: 'chromium' }, joiner: { type: 'string', default: 'webkit' } } });
const engineNames = [options.inviter, options.joiner], engines = { chromium, webkit };
if (engineNames.some(name => !Object.hasOwn(engines, name))) throw new Error('Select Chromium or WebKit.');
const evidence = resolve(options.evidence ?? join(checks, 'evidence', 'work-' + engineNames.join('-') + '-' + Date.now()));
await mkdir(dirname(evidence), { recursive: true });
await mkdir(evidence); // Never overwrite an earlier run's evidence.
const temporary = await mkdtemp(join(tmpdir(), 'workspace-work-acceptance-'));
const dist = join(temporary, 'dist'), peers = [], relayOrigins = new Set();
const report = {
  kind: 'browser-work-acceptance-v1', started_at: new Date().toISOString(), passed: false,
  scope: 'Production BrowserHost, WorkspaceSession, RetainedInputJournal and WorkController with real Turso OPFS and Iroh in two persistent browser profiles.',
  engines: engineNames, assertions: [], stages: [], launches: [],
  provider: { type: 'synthetic-intercepted-openai-responses', network_policy: 'Every api.openai.com request is fulfilled or aborted; none is forwarded.', requests: [] },
  limits: [
    'Provider replies and credentials are synthetic; authenticated OpenAI behavior and generated semantic quality are not established.',
    'This dedicated fixture exercises production controllers, not the shipped renderer or physical iPhone Safari.',
    'The publication fault withholds a real Applied receipt between BrowserHost and WorkController; it does not interrupt a database transaction.',
    'Follow-up is shared unsent text, not an Outlook draft, delivered mail or external execution.',
  ],
};
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const equal = isDeepStrictEqual;
let server, origin, activeStage = 'setup', beforeSources;
function check(name, condition) {
  if (!condition) { const error = new Error(name); error.name = 'AssertionFailure'; throw error; }
  report.assertions.push(name);
}
async function stage(name, action) {
  activeStage = name; console.log(JSON.stringify({ stage: name }));
  const started = Date.now(); await action(); report.stages.push({ name, elapsed_ms: Date.now() - started });
}
async function files(folder) {
  const paths = [];
  for (const entry of await readdir(folder, { withFileTypes: true })) {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) paths.push(...await files(path));
    else if (entry.isFile()) paths.push(path);
  }
  return paths.sort();
}
async function sourceHashes() {
  const paths = ['browser/checks/work-acceptance.mjs', 'browser/checks/work-fixture.html', 'browser/checks/work-fixture.ts',
    'browser/checks/package.json', 'browser/checks/package-lock.json', 'browser/host/package.json', 'browser/host/package-lock.json',
    'native/ui/package.json', 'native/ui/package-lock.json', 'browser/host-wasm/Cargo.toml', 'browser/host-wasm/Cargo.lock'];
  for (const name of ['host', 'session', 'pairing', 'protocol', 'meeting-preparation', 'retained-input', 'retained-commands', 'work', 'work-controller']) paths.push('native/ui/src/' + name + '.ts');
  for (const folder of ['native/core/src', 'browser/host/src', 'browser/host-wasm/src', 'browser/host-wasm/pkg']) {
    for (const path of await files(join(source, folder))) paths.push(relative(source, path));
  }
  return Object.fromEntries(await Promise.all(paths.sort().map(async path => [path, hash(await readFile(join(source, path)))])));
}
async function deadline(promise, label, milliseconds = 90_000) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label + ' deadline')), milliseconds); })]); }
  finally { clearTimeout(timer); }
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
async function serve() {
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const path = resolve(dist, '.' + (pathname === '/' ? '/work-fixture.html' : pathname));
      if (!path.startsWith(dist + sep)) { response.writeHead(403).end(); return; }
      response.writeHead(200, {
        'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css', '.json': 'application/json' }[extname(path)] ?? 'application/octet-stream',
        'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin', 'Cache-Control': 'no-store',
      }).end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((done, failed) => { server.once('error', failed); server.listen(0, '127.0.0.1', done); });
  origin = 'http://127.0.0.1:' + server.address().port;
}

const SHARED = 'meeting-shared', PRIVATE = 'meeting-private', TRANSCRIPT = 'meeting-transcript';
const ORIGINAL = 'Mira will share the review brief next Friday. Keep the launch small.';
const CHANGED = 'The revised transcript records a discussion only. No delivery date was committed.';
const PRIVATE_TEXT = 'PRIVATE_WORKSPACE_ACCEPTANCE_SENTINEL';
const DRAFT_TEXT = 'LOCAL_UNSUBMITTED_INPUT_ACCEPTANCE_SENTINEL';
const SYNTHETIC_KEY = 'synthetic-work-acceptance-key-never-valid-at-a-provider';
const sourceReference = { workspace_id: SHARED, resource_id: TRANSCRIPT, revision_id: 'transcript-v1' };
const citation = { source: sourceReference, quote: 'Mira will share the review brief next Friday.' };
const basis = { type: 'transcript', citations: [citation] };
const materials = {
  notes: { title: 'Review notes', body: 'Mira will share the review brief next Friday.', citations: [citation] },
  actions: [{ text: 'Share the review brief', owner: { type: 'assigned', name: 'Mira', basis }, deadline: { type: 'stated', text: 'next Friday', basis }, basis }],
  follow_up: { title: 'Unsent review follow-up', body: 'For review: Mira will share the review brief next Friday.', citations: [citation] },
};
const providerResponse = () => JSON.stringify({ status: 'completed', error: null, incomplete_details: null,
  output: [{ type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: JSON.stringify(materials) }] }] });

class Peer {
  constructor(name, engine) {
    this.name = name; this.engine = engine; this.profile = join(temporary, name);
    this.providerPlans = []; this.providerRequests = 0; this.providerFailures = []; this.pageErrors = 0;
    peers.push(this);
  }
  planProvider(label) {
    const arrived = deferred(), gate = deferred();
    const plan = { label, arrived, gate, observed: null, handled: false, abandoned: false };
    this.providerPlans.push(plan); return plan;
  }
  async interceptProvider(route) {
    const request = route.request();
    const headers = { 'access-control-allow-origin': origin, 'access-control-allow-methods': 'POST, OPTIONS', 'access-control-allow-headers': 'authorization, content-type', 'content-type': 'application/json' };
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers, body: '' }); return; }
    const plan = this.providerPlans.find(plan => !plan.handled);
    if (!plan || request.url() !== 'https://api.openai.com/v1/responses' || request.method() !== 'POST') {
      this.providerFailures.push('unexpected-provider-request'); await route.abort(); return;
    }
    plan.handled = true; this.providerRequests++;
    try {
      const body = request.postDataJSON(), input = JSON.parse(body.input[0].content[0].text);
      const requestHeaders = await request.allHeaders();
      plan.observed = {
        exact_sources: equal(input.sources, [{ source: sourceReference, text: ORIGINAL }]),
        outcome: input.outcome, structured_output: body.text?.format?.type === 'json_schema' && body.text.format.strict === true,
        storage_disabled: body.store === false, synthetic_credential: requestHeaders.authorization === 'Bearer ' + SYNTHETIC_KEY,
      };
      report.provider.requests.push({ peer: this.name, label: plan.label, exact_sources: plan.observed.exact_sources,
        structured_output: plan.observed.structured_output, storage_disabled: plan.observed.storage_disabled, synthetic_credential: plan.observed.synthetic_credential });
      plan.arrived.resolve();
      const response = await plan.gate.promise;
      if (response === 'respond') await route.fulfill({ status: 200, headers, body: providerResponse() });
      else await route.abort();
    } catch {
      if (!plan.abandoned) this.providerFailures.push('provider-interception-failed');
      plan.arrived.resolve();
      await route.abort().catch(() => undefined);
    }
  }
  async start(paused = false) {
    this.context = await engines[this.engine].launchPersistentContext(this.profile, { headless: true });
    await this.context.route('https://api.openai.com/**', route => this.interceptProvider(route));
    this.page = await this.context.newPage();
    this.page.on('pageerror', () => { this.pageErrors++; });
    this.page.on('websocket', socket => { try { relayOrigins.add(new URL(socket.url()).origin); } catch {} });
    await this.page.goto(origin);
    await this.page.waitForFunction(() => Boolean(window.workspaceWorkCheck));
    check(this.name + '-isolated-launch-' + report.launches.length, await this.page.evaluate(() => crossOriginIsolated && typeof SharedArrayBuffer === 'function'));
    report.launches.push({ peer: this.name, engine: this.engine, version: this.context.browser().version() });
    return this.call('open', [{ deviceName: this.name, databaseName: 'work-acceptance.db', baseUrl: origin + '/', startPaused: paused }]);
  }
  async tryCall(method, args = []) {
    return deadline(this.page.evaluate(async ({ method, args }) => {
      try { return { ok: true, value: await window.workspaceWorkCheck[method](...args) }; }
      catch (error) { return { ok: false, error_name: error instanceof Error ? error.name : 'UnknownError' }; }
    }, { method, args }), this.name + '.' + method);
  }
  async call(method, args = []) {
    const response = await this.tryCall(method, args);
    if (!response.ok) throw new Error(this.name + '.' + method + ' failed (' + response.error_name + ')');
    return response.value;
  }
  snapshot() { return this.call('snapshot'); }
  command(command) { return this.call('command', [command]); }
  async stop(graceful = false) {
    if (!this.context) return;
    const context = this.context;
    for (const plan of this.providerPlans) plan.abandoned = true;
    try { if (graceful) await this.call('close'); }
    finally {
      try { await deadline(context.close(), this.name + '.close', 30_000); }
      finally {
        this.context = undefined; this.page = undefined;
        for (const plan of this.providerPlans) plan.gate.resolve('abandon');
      }
    }
  }
}
function resource(snapshot, id) {
  const selected = snapshot.resources.filter(value => value.id === id);
  if (selected.length !== 1) throw new Error('Expected one fixture resource.');
  return selected[0];
}
const heads = (snapshot, id) => resource(snapshot, id).heads.map(value => value.id).sort();
function workRevision(snapshot, id) {
  const value = resource(snapshot, id);
  if (value.heads.length !== 1 || !value.heads[0].work) throw new Error('Expected one work revision.');
  return value.heads[0];
}
const projection = snapshot => ({ workspaces: snapshot.workspaces.filter(value => value.id === SHARED), resources: snapshot.resources.filter(value => value.workspace_id === SHARED) });
async function exchange(a, b) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const leftReport = await a.call('sync'), rightReport = await b.call('sync');
    const [left, right] = await Promise.all([a.snapshot(), b.snapshot()]);
    const succeeded = [leftReport, rightReport].every(report => report.peers.length > 0 && report.peers.every(peer => peer.state.type === 'synced'));
    if (succeeded && equal(projection(left), projection(right))) return [left, right];
    await new Promise(done => setTimeout(done, 250));
  }
  throw new Error('Shared work did not converge after bounded real peer exchanges.');
}
async function applied(peer, command) {
  const receipt = await peer.command(command);
  check(command.operation_id + '-durably-applied', receipt.type === 'applied' && receipt.operation_id === command.operation_id && receipt.workspace_id === command.workspace_id && receipt.replayed === false);
  return receipt;
}
function privateStateAbsent(snapshot) {
  return !snapshot.workspaces.some(value => value.id === PRIVATE) && !snapshot.resources.some(value => value.workspace_id === PRIVATE)
    && !JSON.stringify(snapshot).includes(PRIVATE_TEXT) && !JSON.stringify(snapshot).includes(DRAFT_TEXT) && !JSON.stringify(snapshot).includes(SYNTHETIC_KEY);
}
function publishedTogether(snapshot, workId, command) {
  const work = workRevision(snapshot, workId), notes = resource(snapshot, command.notes.resource_id), follow = resource(snapshot, command.follow_up.resource_id);
  return work.work.state.type === 'materials_ready' && work.id === command.operation_id
    && notes.heads.length === 1 && follow.heads.length === 1 && notes.heads[0].id === command.operation_id && follow.heads[0].id === command.operation_id
    && work.work.state.notes.reference.resource_id === notes.id && work.work.state.notes.reference.revision_id === command.operation_id
    && work.work.state.follow_up.type === 'unsent_text' && work.work.state.follow_up.material.reference.resource_id === follow.id
    && work.work.state.follow_up.material.reference.revision_id === command.operation_id;
}

try {
  beforeSources = await sourceHashes(); report.source_hashes = beforeSources;
  await stage('build-dedicated-production-controller-fixture', async () => {
    await build({ configFile: false, root: checks, cacheDir: join(temporary, 'vite-cache'), logLevel: 'error', base: './', worker: { format: 'es' },
      build: { outDir: dist, emptyOutDir: true, target: 'esnext', sourcemap: false, rollupOptions: { input: join(checks, 'work-fixture.html') } }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
    report.asset_hashes = Object.fromEntries(await Promise.all((await files(dist)).map(async path => [relative(dist, path), hash(await readFile(path))])));
    await serve();
  });
  const a = new Peer('Origin', engineNames[0]), b = new Peer('Member', engineNames[1]);
  let initialA, initialB, workId, run, publicationCommand, published, resolvedActions;
  const outcome = 'Prepare meeting notes, explicit actions, and unsent follow-up text for review.';
  await stage('open-two-real-persistent-hosts', async () => {
    [initialA, initialB] = await Promise.all([a.start(), b.start()]);
    check('fresh-profiles-have-no-invented-work', initialA.snapshot.resources.length === 0 && initialB.snapshot.resources.length === 0);
    check('profiles-have-independent-device-identities', initialA.snapshot.device_id !== initialB.snapshot.device_id);
  });
  await stage('retain-source-request-and-private-input-before-execution', async () => {
    await applied(a, { type: 'create_workspace', operation_id: 'create-shared', workspace_id: SHARED, title: 'Meeting review' });
    await applied(a, { type: 'create_workspace', operation_id: 'create-private', workspace_id: PRIVATE, title: 'Private material' });
    await applied(a, { type: 'create_resource', operation_id: 'transcript-v1', workspace_id: SHARED, resource_id: TRANSCRIPT, title: 'Meeting transcript', body: ORIGINAL });
    await applied(a, { type: 'create_resource', operation_id: 'private-note', workspace_id: PRIVATE, resource_id: 'private-source', title: 'Private source', body: PRIVATE_TEXT });
    await a.call('putDraft', ['private-editor', 'editor', { draftId: 'local-editor', type: 'new', workspaceId: PRIVATE, title: 'Unsubmitted', body: DRAFT_TEXT }]);
    const created = await a.call('createWork', [{ workspaceId: SHARED, title: 'Prepare review material', outcome, sources: [sourceReference] }]);
    workId = created.resourceId;
    check('controller-retains-requested-work-before-preparation', typeof workId === 'string' && created.activity.type === 'idle' && workRevision(await a.snapshot(), workId).work.state.type === 'requested');
    const before = await a.snapshot(); await a.stop(); const reopened = await a.start(true);
    check('full-browser-reopen-retains-source-and-request', reopened.snapshot.device_id === initialA.snapshot.device_id && equal(reopened.snapshot.resources, before.resources));
    check('reopen-does-not-dispatch-preparation', reopened.activity.type === 'idle' && a.providerRequests === 0);
    check('local-unsubmitted-input-survives-reopen', (await a.call('drafts')).drafts.some(value => value.id === 'private-editor' && value.payload.includes(DRAFT_TEXT)));
    const rejected = await a.command({ type: 'create_work', operation_id: 'cross-workspace-source', workspace_id: SHARED, resource_id: 'invalid-work', title: 'Invalid source', outcome,
      sources: [{ workspace_id: PRIVATE, resource_id: 'private-source', revision_id: 'private-note' }] });
    const after = await a.snapshot();
    check('foreign-workspace-source-is-definitively-rejected-without-partial-work', rejected.type === 'rejected' && after.operation_count === before.operation_count && !after.resources.some(value => value.id === 'invalid-work'));
    await a.call('setConnection', [{ workspace_id: SHARED, operation_id: 'a'.repeat(64), expected_versions: [], api_key: SYNTHETIC_KEY, model: 'gpt-4o-transcribe' }]);
    await a.call('setForeground', [true]);
  });
  await stage('pair-only-selected-workspace', async () => {
    let invitation = await a.call('invite', [[SHARED]]);
    const joined = await b.call('join', [invitation.ticket]); invitation = undefined;
    check('member-receives-source-and-requested-work', resource(joined, TRANSCRIPT).heads[0].body === ORIGINAL && workRevision(joined, workId).work.state.type === 'requested');
    check('private-workspace-and-local-drafts-do-not-travel-with-grant', privateStateAbsent(joined) && !(await b.call('drafts')).drafts.some(value => value.payload.includes(DRAFT_TEXT)));
    check('member-receives-scoped-provider-connection', (await b.call('connection', [SHARED])).status === 'ready');
    check('member-cannot-read-private-workspace-connection', !(await b.tryCall('connection', [PRIVATE])).ok);
    await exchange(a, b);
  });
  await stage('interrupted-run-remains-bound-to-origin', async () => {
    const plan = a.planProvider('interrupted-origin-request');
    await a.call('startPreparation', [workId]); await deadline(plan.arrived.promise, 'First synthetic provider interception');
    check('worker-upload-uses-pinned-source-and-scoped-synthetic-key', plan.observed?.exact_sources && plan.observed.synthetic_credential && plan.observed.structured_output && plan.observed.storage_disabled && plan.observed.outcome === outcome);
    const [, member] = await exchange(a, b), revision = workRevision(member, workId);
    check('origin-run-is-visible-before-materials-exist', revision.work.state.type === 'preparing' && revision.work.state.run.originating_device === initialA.snapshot.device_id);
    run = revision.work.state.run;
    const before = await b.snapshot(), memberState = await b.call('resumeWork', [workId]);
    check('other-controller-cannot-resume-origin-run', memberState.type === 'failed' && b.providerRequests === 0 && (await b.snapshot()).operation_count === before.operation_count);
    const connection = await b.call('connection', [SHARED]);
    const direct = await b.tryCall('prepareMeeting', [{ workspace_id: SHARED, resource_id: workId, revision_id: revision.id, run_id: run.id, connection_version: connection.version }]);
    check('worker-also-refuses-another-hosts-run-before-provider-upload', !direct.ok && b.providerRequests === 0 && (await b.snapshot()).operation_count === before.operation_count);
    await a.stop(); const reopened = await a.start(true);
    check('terminated-origin-reopens-same-interrupted-run', reopened.snapshot.device_id === initialA.snapshot.device_id && reopened.activity.type === 'interrupted' && reopened.activity.runId === run.id && workRevision(reopened.snapshot, workId).work.state.type === 'preparing');
    check('restoring-interrupted-run-does-not-call-provider', a.providerRequests === 1);
    await a.call('setForeground', [true]);
  });
  await stage('pinned-source-generation-and-atomic-publication-with-lost-reply', async () => {
    const plan = a.planProvider('explicit-origin-resume');
    await a.call('startPreparation', [workId, true]); await deadline(plan.arrived.promise, 'Resumed synthetic provider interception');
    await applied(a, { type: 'edit_resource', operation_id: 'transcript-v2', workspace_id: SHARED, resource_id: TRANSCRIPT,
      expected_heads: ['transcript-v1'], title: 'Corrected meeting transcript', body: CHANGED });
    const before = await a.snapshot();
    check('source-head-can-change-with-captured-revision-retained', resource(before, TRANSCRIPT).heads[0].body === CHANGED && resource(before, TRANSCRIPT).history.some(value => value.id === 'transcript-v1' && value.body === ORIGINAL));
    await a.call('loseNextPublicationReply'); plan.gate.resolve('respond');
    const state = await a.call('finishPreparation');
    check('lost-publication-reply-remains-uncertain', state.type === 'uncertain' && state.command.type === 'publish_meeting_materials');
    publicationCommand = state.command; published = await a.snapshot();
    const observations = await a.call('commandObservations'), withheld = observations.find(value => value.reply === 'withheld');
    check('fault-withholds-a-genuine-correlated-applied-receipt', withheld?.receipt.type === 'applied' && withheld.receipt.operation_id === publicationCommand.operation_id && equal(withheld.command, publicationCommand));
    check('one-durable-operation-publishes-notes-followup-and-work-together', published.operation_count === before.operation_count + 1 && published.resources.length === before.resources.length + 2 && publishedTogether(published, workId, publicationCommand));
    const ready = workRevision(published, workId).work;
    check('generation-keeps-original-source-citation-after-transcript-edit', plan.observed?.exact_sources && equal(ready.sources, [sourceReference]) && equal(ready.state.notes.citations, [citation]) && ready.state.actions[0].owner.name === 'Mira' && ready.state.actions[0].deadline.text === 'next Friday');
    check('uncertain-publication-retains-exact-private-retry-command', (await a.call('drafts')).drafts.some(value => value.id === 'meeting-attempt' && equal(JSON.parse(value.payload).command, publicationCommand)));
    const [, member] = await exchange(a, b);
    check('real-peer-imports-all-publication-results-together', publishedTogether(member, workId, publicationCommand));
    check('peer-sees-reviewable-unsent-text-without-private-journal', resource(member, publicationCommand.follow_up.resource_id).heads[0].body === materials.follow_up.body && privateStateAbsent(member) && !(await b.call('drafts')).drafts.some(value => value.id === 'meeting-attempt'));
  });
  await stage('restart-and-exact-retry-do-not-duplicate-publication', async () => {
    await a.stop(); const reopened = await a.start(true);
    report.restored_publication = { activity: reopened.activity.type, command_matches: equal(reopened.activity.command, publicationCommand), resources_match: equal(reopened.snapshot.resources, published.resources) };
    check('unknown-publication-outcome-survives-process-restart', reopened.activity.type === 'uncertain');
    check('restored-publication-command-retains-every-field', equal(reopened.activity.command, publicationCommand));
    check('all-published-resources-survive-process-restart', equal(reopened.snapshot.resources, published.resources));
    const before = reopened.snapshot, state = await a.call('retryWork'), after = await a.snapshot();
    const retried = (await a.call('commandObservations')).at(-1);
    check('controller-retries-identical-publication-and-receives-replay', state.type === 'idle' && equal(retried?.command, publicationCommand) && retried.receipt.type === 'applied' && retried.receipt.replayed === true);
    check('retry-adds-no-operation-or-duplicate-output', after.operation_count === before.operation_count && equal(after.resources, before.resources));
    check('confirmed-retry-clears-only-meeting-attempt', !(await a.call('drafts')).drafts.some(value => value.id === 'meeting-attempt') && (await a.call('drafts')).drafts.some(value => value.id === 'private-editor'));
    check('retry-does-not-repeat-provider-generation', a.providerRequests === 2);
    const memberBefore = await b.snapshot(), replayElsewhere = await b.command(publicationCommand);
    check('replicated-result-is-not-another-hosts-replay-authority', replayElsewhere.type === 'rejected' && (await b.snapshot()).operation_count === memberBefore.operation_count);
    await a.call('setForeground', [true]); await exchange(a, b);
  });
  await stage('offline-owner-and-deadline-corrections-preserve-conflict', async () => {
    await Promise.all([a.call('setForeground', [false]), b.call('setForeground', [false])]);
    const [left, right] = await Promise.all([a.snapshot(), b.snapshot()]);
    check('both-peers-are-paused-with-identical-work-base', left.network.type === 'paused' && right.network.type === 'paused' && equal(heads(left, workId), heads(right, workId)));
    const originalAction = workRevision(left, workId).work.state.actions[0], expectedHeads = heads(left, workId);
    const ownerAction = { ...originalAction, owner: { type: 'assigned', name: 'Lea', basis: { type: 'user' } } };
    const deadlineAction = { ...originalAction, deadline: { type: 'stated', text: 'Monday afternoon', basis: { type: 'user' } } };
    const ownerResult = await a.call('correctActions', [{ workspaceId: SHARED, resourceId: workId, expectedHeads, actions: [ownerAction] }]);
    const deadlineResult = await b.call('correctActions', [{ workspaceId: SHARED, resourceId: workId, expectedHeads, actions: [deadlineAction] }]);
    check('each-offline-user-correction-is-durably-accepted', ownerResult.applied && deadlineResult.applied);
    const memberBranch = await b.snapshot(); await b.stop(); const reopened = await b.start(true);
    check('offline-member-correction-survives-browser-reopen', reopened.snapshot.device_id === initialB.snapshot.device_id && equal(resource(reopened.snapshot, workId), resource(memberBranch, workId)));
    await Promise.all([a.call('setForeground', [true]), b.call('setForeground', [true])]);
    const [mergedA, mergedB] = await exchange(a, b), branches = resource(mergedA, workId).heads;
    check('both-offline-action-branches-converge-without-last-writer-wins', branches.length === 2 && equal(resource(mergedA, workId), resource(mergedB, workId))
      && branches.some(value => value.work.state.actions[0].owner.name === 'Lea') && branches.some(value => value.work.state.actions[0].deadline.text === 'Monday afternoon'));
    const count = mergedB.operation_count;
    const stale = await b.call('correctActions', [{ workspaceId: SHARED, resourceId: workId, expectedHeads: heads(memberBranch, workId), actions: [deadlineAction] }]);
    check('stale-correction-retains-visible-conflict-without-commit', !stale.applied && stale.activity.type === 'conflict' && (await b.snapshot()).operation_count === count);
    await b.call('discardInterrupted');
    resolvedActions = [{ ...originalAction, owner: ownerAction.owner, deadline: deadlineAction.deadline }];
    const resolved = await b.call('correctActions', [{ workspaceId: SHARED, resourceId: workId, expectedHeads: heads(mergedB, workId), actions: resolvedActions }]);
    check('explicit-review-of-all-heads-accepts-combined-correction', resolved.applied);
    const [afterA, afterB] = await exchange(a, b), current = workRevision(afterA, workId).work;
    check('resolved-owner-deadline-and-user-evidence-reach-both-peers', equal(current.state.actions, resolvedActions) && equal(resource(afterA, workId), resource(afterB, workId)));
    check('correction-preserves-generated-material-and-original-evidence', equal(current.sources, [sourceReference]) && current.state.notes.reference.revision_id === publicationCommand.operation_id
      && current.state.follow_up.material.reference.revision_id === publicationCommand.operation_id && resource(afterB, publicationCommand.notes.resource_id).heads[0].body === materials.notes.body);
  });
  await stage('human-followup-edit-and-final-durable-private-boundary', async () => {
    const edited = await a.call('editNote', [publicationCommand.follow_up.resource_id, 'Human-shortened follow-up kept for review.']);
    check('production-note-session-edits-generated-followup', edited.type === 'idle');
    const [left, right] = await exchange(a, b), current = workRevision(right, workId).work;
    check('current-followup-edit-travels-with-original-publication-provenance', resource(right, publicationCommand.follow_up.resource_id).heads[0].body === 'Human-shortened follow-up kept for review.'
      && current.state.follow_up.material.reference.revision_id === publicationCommand.operation_id);
    check('private-source-key-and-local-input-remain-absent-on-member', privateStateAbsent(right) && !(await b.call('drafts')).drafts.some(value => value.payload.includes(DRAFT_TEXT)));
    await Promise.all([a.stop(), b.stop()]);
    const [afterA, afterB] = await Promise.all([a.start(true), b.start(true)]);
    check('final-browser-reopens-retain-identities-and-complete-history', afterA.snapshot.device_id === initialA.snapshot.device_id && afterB.snapshot.device_id === initialB.snapshot.device_id
      && equal(afterA.snapshot.resources, left.resources) && equal(afterB.snapshot.resources, right.resources));
    check('final-reopen-retains-corrected-actions-source-revisions-and-local-draft', equal(workRevision(afterB.snapshot, workId).work.state.actions, resolvedActions)
      && resource(afterB.snapshot, TRANSCRIPT).history.some(value => value.id === 'transcript-v1') && (await a.call('drafts')).drafts.some(value => value.payload.includes(DRAFT_TEXT)));
    check('no-provider-call-is-invented-by-peer-recovery-or-reopen', a.providerRequests === 2 && b.providerRequests === 0);
    check('all-provider-requests-were-handled-by-explicit-synthetic-plans', peers.every(peer => peer.providerFailures.length === 0));
    check('fixture-pages-reported-no-browser-errors', peers.every(peer => peer.pageErrors === 0));
  });
  report.passed = true;
} catch (error) {
  report.failure = { stage: activeStage, kind: error?.name ?? 'Error', detail: error?.name === 'AssertionFailure' ? error.message : 'Build, browser, provider fixture or real host operation failed; no private payload was exported.' };
} finally {
  const cleanup = await Promise.allSettled(peers.map(peer => peer.stop(true)));
  report.contexts_closed = cleanup.every(result => result.status === 'fulfilled');
  if (!report.contexts_closed) report.passed = false;
  if (server) { server.closeAllConnections(); await new Promise(done => server.close(done)); }
  report.static_server_closed = true; report.relay_origins = [...relayOrigins].sort();
  if (beforeSources) {
    try { report.final_source_hashes = await sourceHashes(); report.source_unchanged = equal(beforeSources, report.final_source_hashes); }
    catch { report.source_unchanged = false; }
    if (!report.source_unchanged) { report.passed = false; report.failure ??= { stage: 'cleanup', kind: 'SourceChanged', detail: 'Source changed during acceptance.' }; }
  }
  await rm(temporary, { recursive: true, force: true }); report.disposable_build_and_profiles_removed = true;
  report.finished_at = new Date().toISOString();
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, assertions: report.assertions.length, failure: report.failure, report: join(evidence, 'report.json') }));
  if (!report.passed) process.exitCode = 1;
}
