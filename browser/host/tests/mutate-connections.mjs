/** Targeted codec mutants; all executions use disposable copies, never live source. */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';

const root = resolve(import.meta.dirname, '../../..');
const model = 'browser/host/src/connections.ts';
const vault = 'browser/host/src/connection-vault.ts';
const provider = 'browser/host/src/transcription.ts';
const identityStorage = 'browser/host/src/identity-storage.ts';
const types = 'browser/host/src/types.ts';
const protocol = 'native/ui/src/protocol.ts';
const tests = ['browser/host/tests/connections.test.mjs', 'browser/host/tests/connection-vault.test.mjs', 'browser/host/tests/transcription.test.mjs', 'browser/host/tests/identity-storage.test.mjs'];
const hash = value => createHash('sha256').update(value).digest('hex');
const files = Object.fromEntries(await Promise.all([model, vault, provider, identityStorage, types, protocol, ...tests].map(async path => [path, await readFile(join(root, path), 'utf8')])));
const options = parseArgs({ options: { report: { type: 'string' } } }).values;
const mutations = [
  { id: 'skip-identity-checkpoint', file: identityStorage, from: 'await checkpoint();', to: 'await Promise.resolve();', test: 'checkpoint completes before inspecting current physical identity files', marker: 'CHECKPOINT_NOT_RUN' },
  { id: 'allow-raw-identity-residue', file: identityStorage, from: 'await containsIdentity(file, [seed, encoded])', to: 'false', test: 'residual plaintext seed in database or WAL blocks startup even across stream chunks', marker: 'DATABASE_RESIDUE_ALLOWED' },
  { id: 'ignore-wal-identity-residue', file: identityStorage, from: '[database, `${database}-wal`]', to: '[database]', test: 'residual plaintext seed in database or WAL blocks startup even across stream chunks', marker: 'WAL_RESIDUE_ALLOWED' },

  { id: 'cache-plaintext-revision-key', file: model, from: 'parents: [...record.parents], action }, signature', to: 'parents: [...record.parents], action: record.action }, signature', test: 'retained public revision metadata holds no plaintext credential cache', marker: 'CREDENTIAL_CACHED_IN_PUBLIC_REVISION' },
  { id: 'remove-identity-database-binding', file: vault, from: "'shared-workspace/device-identity-at-rest/1', database, 1", to: "'shared-workspace/device-identity-at-rest/1', 'ignored', 1", test: 'encrypted device identity preserves every seed byte and binds database and slot', marker: 'IDENTITY_DATABASE_BINDING_REMOVED' },

  { id: 'overwrite-stale-connection', file: model, from: 'json(connectionHeads(records, input.workspace_id)) !== json(record.parents)', to: 'false', test: 'stale expected versions cannot overwrite a newer connection', marker: 'STALE_CONNECTION_OVERWRITE' },
  { id: 'reuse-request-for-changed-key', file: model, from: 'connectionPayload(previous.record) !== connectionPayload(record)', to: 'false', test: 'exact retry survives later edits but changed identity input rejects', marker: 'CHANGED_RETRY_ACCEPTED' },
  { id: 'resolve-another-version', file: model, from: "state.status !== 'ready' || state.version !== version", to: "state.status !== 'ready'", test: 'upload resolves only the selected workspace and exact active version', marker: 'WRONG_VERSION_USED' },
  { id: 'import-unselected-secret', file: model, from: 'result.every(item => selected.includes(item.record.workspace_id))', to: 'true', test: 'unselected connection history is rejected before import', marker: 'UNSELECTED_SECRET_IMPORTED' },
  { id: 'borrow-cross-workspace-parent', file: model, from: 'item.record.parents.every(parent => merged.get(parent)?.record.workspace_id === item.record.workspace_id)', to: 'true', test: 'a connection cannot borrow a parent from another workspace', marker: 'CROSS_WORKSPACE_PARENT_ACCEPTED' },
  { id: 'accept-extractable-device-key', file: vault, from: '!key.extractable', to: 'true', test: 'exportable or inappropriate encryption keys are refused', marker: 'EXTRACTABLE_DEVICE_KEY_ACCEPTED' },
  { id: 'use-ambient-cookies', file: provider, from: "credentials: 'omit'", to: "credentials: 'include'", test: 'audio uploads only to fixed OpenAI endpoint with selected workspace version and no ambient browser credentials', marker: 'AMBIENT_CREDENTIALS_INCLUDED' },
  { id: 'cache-provider-response', file: provider, from: "cache: 'no-store'", to: "cache: 'default'", test: 'audio uploads only to fixed OpenAI endpoint with selected workspace version and no ambient browser credentials', marker: 'PROVIDER_RESPONSE_CACHEABLE' },
  { id: 'follow-credential-redirect', file: provider, from: "redirect: 'error'", to: "redirect: 'follow'", test: 'audio uploads only to fixed OpenAI endpoint with selected workspace version and no ambient browser credentials', marker: 'CREDENTIAL_REDIRECT_ALLOWED' },
  { id: 'retarget-provider-endpoint', file: provider, from: 'https://api.openai.com/v1/audio/transcriptions', to: 'https://other.invalid/v1/audio/transcriptions', test: 'audio uploads only to fixed OpenAI endpoint with selected workspace version and no ambient browser credentials', marker: 'PROVIDER_ENDPOINT_CHANGED' },
  { id: 'publish-stale-transcript', file: provider, from: '\n    await resolve(input.workspace_id, input.connection_version);', to: '\n    Promise.resolve();', test: 'changed credentials during upload refuse stale transcription publication', marker: 'STALE_TRANSCRIPT_PUBLISHED' },
  { id: 'reflect-key-as-transcript', file: provider, from: '!result.text.includes(connection.api_key)', to: 'true', test: 'a provider success body cannot reflect the workspace key into generic voice history', marker: 'KEY_REFLECTED_AS_TRANSCRIPT' },
];
const report = {
  kind: 'workspace-connections-targeted-mutations-v1', node: process.version,
  sourceHashes: Object.fromEntries(Object.entries(files).map(([path, value]) => [path, hash(value)])),
  harnessSha256: hash(await readFile(import.meta.filename)),
  scope: 'Pure connection transitions, actual WebCrypto AES-GCM, and provider request adapter with injected HTTP effects. No real OpenAI credential, peer networking, Turso or browser lifecycle exercised; those have separate acceptance evidence.',
  rule: 'Only the named test failing with its intended assertion marker counts. Setup errors, syntax/import failures, timeouts, signals, and unrelated failures do not count.',
  baseline: null, mutations: [], originalSourceAndChecksUnchanged: false, passed: false,
};
const scratch = await mkdtemp(join(tmpdir(), 'workspace-connection-mutants-'));
function run(folder, testName) {
  const args = ['--experimental-strip-types', '--test', '--test-reporter=tap'];
  if (testName) args.push('--test-name-pattern', '^' + testName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$');
  args.push(...(testName ? tests.filter(path => files[path].includes("test('" + testName + "'")) : tests));
  const child = spawnSync(process.execPath, args, { cwd: folder, encoding: 'utf8', timeout: 10_000, maxBuffer: 1024 * 1024 });
  return { exitCode: child.status, signal: child.signal, errorCode: child.error?.code ?? null, output: (child.stdout ?? '') + (child.stderr ?? '') };
}
async function materialize(folder) {
  await mkdir(folder, { recursive: true });
  await writeFile(join(folder, 'package.json'), '{"type":"module"}\n');
  for (const [path, source] of Object.entries(files)) {
    await mkdir(dirname(join(folder, path)), { recursive: true });
    await writeFile(join(folder, path), source);
  }
}
try {
  const baselineDir = join(scratch, 'baseline');
  await materialize(baselineDir);
  const baseline = run(baselineDir);
  report.baseline = { exitCode: baseline.exitCode, signal: baseline.signal, errorCode: baseline.errorCode, outputSha256: hash(baseline.output), output: baseline.output };
  if (baseline.exitCode !== 0 || baseline.signal !== null || baseline.errorCode !== null) throw Error('Passing baseline required before mutation checks.');
  for (const mutation of mutations) {
    const original = files[mutation.file];
    if (original.split(mutation.from).length !== 2) throw Error('Missing or ambiguous mutation anchor: ' + mutation.id);
    const folder = join(scratch, mutation.id);
    await materialize(folder);
    const changed = original.replace(mutation.from, mutation.to);
    await writeFile(join(folder, mutation.file), changed);
    const observed = run(folder, mutation.test);
    const intended = observed.exitCode === 1 && observed.signal === null && observed.errorCode === null
      && observed.output.includes(mutation.test)
      && observed.output.includes('ERR_ASSERTION') && observed.output.includes(mutation.marker)
      && /# tests 1\b/.test(observed.output) && /# fail 1\b/.test(observed.output);
    const result = { ...mutation, mutatedSourceSha256: hash(changed), exitCode: observed.exitCode, signal: observed.signal,
      errorCode: observed.errorCode, outcome: intended ? 'killed-by-intended-assertion' : observed.exitCode === 0 ? 'survived' : 'invalid-or-unrelated-failure', output: observed.output };
    report.mutations.push(result);
    console.log(JSON.stringify({ mutation: mutation.id, outcome: result.outcome }));
  }
  report.originalSourceAndChecksUnchanged = (await Promise.all(Object.entries(files).map(async ([path, source]) => await readFile(join(root, path), 'utf8') === source))).every(Boolean);
  report.passed = report.originalSourceAndChecksUnchanged && report.mutations.length === mutations.length && report.mutations.every(result => result.outcome === 'killed-by-intended-assertion');
} catch (error) {
  report.error = String(error);
} finally {
  await rm(scratch, { recursive: true, force: true });
  if (options.report) {
    const path = resolve(options.report);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, JSON.stringify(report, null, 2) + '\n');
  }
}
console.log(JSON.stringify({ passed: report.passed, baselinePassed: report.baseline?.exitCode === 0, intendedKills: report.mutations.filter(result => result.outcome === 'killed-by-intended-assertion').length, totalMutations: mutations.length, sourceUnchanged: report.originalSourceAndChecksUnchanged, error: report.error ?? null }));
process.exitCode = report.passed ? 0 : 1;
