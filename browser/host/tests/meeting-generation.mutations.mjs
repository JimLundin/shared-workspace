/** Provider-fixture mutations use a complete, isolated dependency capture. */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = resolve(import.meta.dirname, '../../..');
const modulePath = 'browser/host/src/meeting-generation.ts';
const testPath = 'browser/host/tests/meeting-generation.test.mjs';
const harnessPath = 'browser/host/tests/meeting-generation.mutations.mjs';
// These are the entire transitive runtime imports; no import points into live src.
const paths = [modulePath, testPath, harnessPath, 'browser/host/src/types.ts',
  'browser/host/src/connections.ts', 'native/ui/src/protocol.ts',
  'browser/host/package.json', 'native/ui/package.json'].sort();
const hash = value => createHash('sha256').update(value).digest('hex');
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const capture = async folder => Object.fromEntries(await Promise.all(paths.map(async path => [path, await readFile(join(folder, path))])));
const hashes = files => Object.fromEntries(Object.entries(files).map(([path, value]) => [path, hash(value)]));
const sameHashes = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const files = Object.freeze(await capture(root));
const mutations = [
  { id: 'provider-storage', from: 'store: false', to: 'store: true', test: 'meeting request uses a fixed scoped Responses request and returns reviewable cited materials', marker: 'MEETING_PROVIDER_STORAGE_ENABLED' },
  { id: 'fixed-endpoint', from: "request('https://api.openai.com/v1/responses'", to: "request('https://untrusted.invalid/v1/responses'", test: 'meeting request uses a fixed scoped Responses request and returns reviewable cited materials', marker: 'MEETING_ENDPOINT_CHANGED' },
  { id: 'quotation-validation', from: 'pinned !== undefined && pinned.text.includes(quote)', to: 'pinned !== undefined', test: 'invented quotations and source identities cannot become published evidence', marker: 'INVENTED_MEETING_CITATION_ACCEPTED', prefix: 'Missing expected exception: ' },
  { id: 'owner-deadline-evidence', from: 'wording === undefined || evidence.some(item => item.quote.includes(wording))', to: 'true', test: 'assigned owners and stated deadlines need their own verbatim supporting quotations', marker: 'INVENTED_MEETING_OWNER_OR_DATE_ACCEPTED', prefix: 'Missing expected exception: ' },
  { id: 'final-connection-validation', from: '    await abortable(resolve(input.workspace_id, input.connection_version), controller.signal);', to: '    // Mutation removes final connection validation.', test: 'stale connection prevents upload and changed connection prevents returning materials', marker: 'STALE_MEETING_RETURNED', prefix: 'Missing expected rejection: ' },
  { id: 'credential-reflection', from: '!JSON.stringify(result).includes(key)', to: 'true', test: 'provider errors, credential echoes and network exceptions stay key-free', marker: 'MEETING_KEY_PUBLISHED', prefix: 'Missing expected rejection: ' },
  { id: 'workspace-source-scope', from: 'workspace === undefined || source.workspace_id === workspace', to: 'true', test: 'bad source scope, capture shape, text limits and duplicate references fail before resolving credentials', marker: 'INVALID_MEETING_RESOLVED_KEY' },
  { id: 'request-byte-limit', from: 'bytes(body) <= MAX_REQUEST_BYTES', to: 'true', test: 'bad source scope, capture shape, text limits and duplicate references fail before resolving credentials', marker: 'INVALID_MEETING_RESOLVED_KEY' },
];
async function materialize(folder) {
  for (const [path, value] of Object.entries(files)) {
    await mkdir(dirname(join(folder, path)), { recursive: true });
    await writeFile(join(folder, path), value);
  }
}
function run(folder, mutation) {
  const args = ['--experimental-strip-types', '--test', '--test-reporter=tap'];
  if (mutation) args.push('--test-name-pattern', '^' + escape(mutation.test) + '$');
  args.push(testPath);
  const result = spawnSync(process.execPath, args, { cwd: folder, encoding: 'utf8', timeout: 15_000, maxBuffer: 4 * 1024 * 1024 });
  const output = (result.stdout ?? '') + (result.stderr ?? '');
  return { exit_code: result.status, signal: result.signal, error_code: result.error?.code ?? null, output_sha256: hash(output), output };
}
function baselinePassed(result) {
  return result.exit_code === 0 && result.signal === null && result.error_code === null
    && ['fail 0', 'cancelled 0', 'skipped 0', 'todo 0'].every(summary => result.output.includes('\n# ' + summary + '\n'))
    && mutations.every(item => new RegExp('^ok \\d+ - ' + escape(item.test) + '$', 'm').test(result.output));
}
function intendedAssertion(result, mutation) {
  if (result.exit_code !== 1 || result.signal !== null || result.error_code !== null) return false;
  const diagnostic = result.output.match(new RegExp('^not ok \\d+ - ' + escape(mutation.test) + '\\n([\\s\\S]*?)^  \\.\\.\\.$', 'm'))?.[1];
  if (!diagnostic) return false;
  const expected = escape((mutation.prefix ?? '') + mutation.marker);
  const message = new RegExp('^  error: (?:[\'"]' + expected + '[\'"]|\\|[-+]?\\n    ' + expected + ')$', 'm');
  return /^  failureType: 'testCodeFailure'$/m.test(diagnostic) && /^  code: 'ERR_ASSERTION'$/m.test(diagnostic)
    && /^  name: 'AssertionError'$/m.test(diagnostic) && message.test(diagnostic)
    && ['tests 1', 'pass 0', 'fail 1', 'cancelled 0', 'skipped 0', 'todo 0'].every(summary => result.output.includes('\n# ' + summary + '\n'));
}
/** Negative controls prove that marker text alone cannot manufacture a kill. */
function rejectionControls(observed, mutation) {
  const controls = [
    ['non-assertion-exit', { ...observed, exit_code: 2 }],
    ['terminated-child', { ...observed, signal: 'SIGTERM' }],
    ['timeout', { ...observed, error_code: 'ETIMEDOUT' }],
    ['setup-error', { ...observed, output: observed.output.replace("code: 'ERR_ASSERTION'", "code: 'ERR_MODULE_NOT_FOUND'") }],
    ['wrong-test', { ...observed, output: observed.output.replace(' - ' + mutation.test + '\n', ' - unrelated test\n') }],
    ['marker-outside-diagnostic', { ...observed, output: observed.output.replaceAll(mutation.marker, 'UNRELATED_ASSERTION') + '\n# AssertionError: ' + mutation.marker + '\n' }],
    ['multiple-tests', { ...observed, output: observed.output.replace('# tests 1\n', '# tests 2\n') }],
    ['cancelled-test', { ...observed, output: observed.output.replace('# cancelled 0\n', '# cancelled 1\n') }],
  ];
  return controls.map(([id, result]) => ({ id, rejected: !intendedAssertion(result, mutation) }));
}
const report = {
  kind: 'meeting-provider-targeted-mutations-v2', started_at: new Date().toISOString(), node: process.version,
  source_hashes: hashes(files), harness_sha256: hash(files[harnessPath]),
  evidence: 'Synthetic transcripts, credentials and controlled provider responses. No authenticated provider, real peer, physical-device, or published-work evidence.',
  rule: 'The captured unchanged baseline must pass. A kill requires exit 1, no signal/timeout, exactly one selected named test, its exact AssertionError message, and unchanged captured files except the declared mutation.',
  baseline: null, mutations: [], rejection_controls: [], final_hashes: null, source_unchanged: false, temporary_copies_removed: false, passed: false,
};
const scratch = await mkdtemp(join(tmpdir(), 'meeting-provider-mutations-'));
try {
  const baselineFolder = join(scratch, 'baseline'); await materialize(baselineFolder);
  const baseline = run(baselineFolder);
  report.baseline = { ...baseline, source_unchanged: sameHashes(report.source_hashes, hashes(await capture(baselineFolder))) };
  if (!baselinePassed(baseline) || !report.baseline.source_unchanged) throw new Error('Passing unchanged captured baseline required.');
  for (const mutation of mutations) {
    const source = files[modulePath].toString('utf8');
    if (source.split(mutation.from).length !== 2) throw new Error('Missing or ambiguous mutation anchor: ' + mutation.id);
    if (files[testPath].toString('utf8').split(mutation.marker).length !== 2) throw new Error('Missing or ambiguous assertion marker: ' + mutation.id);
    const folder = join(scratch, mutation.id); await materialize(folder);
    const changed = source.replace(mutation.from, mutation.to); await writeFile(join(folder, modulePath), changed);
    const expectedHashes = { ...report.source_hashes, [modulePath]: hash(changed) };
    const beforeHashes = hashes(await capture(folder)), observed = run(folder, mutation), afterHashes = hashes(await capture(folder));
    const unchanged = sameHashes(expectedHashes, beforeHashes) && sameHashes(beforeHashes, afterHashes);
    const intended = unchanged && intendedAssertion(observed, mutation);
    const outcome = intended ? 'killed-by-intended-assertion' : unchanged && observed.exit_code === 0 ? 'survived' : 'invalid-or-unrelated-failure';
    report.mutations.push({ ...mutation, ...observed, mutated_source_sha256: hash(changed), source_hashes: beforeHashes, final_hashes: afterHashes, source_unchanged: unchanged, outcome });
    if (intended && report.rejection_controls.length === 0) report.rejection_controls = rejectionControls(observed, mutation);
    process.stdout.write(JSON.stringify({ id: mutation.id, outcome }) + '\n');
  }
} catch (error) { report.failure = String(error); }
finally {
  try { report.final_hashes = hashes(await capture(root)); report.source_unchanged = sameHashes(report.source_hashes, report.final_hashes); }
  catch (error) { report.failure ??= String(error); }
  try { await rm(scratch, { recursive: true, force: true }); report.temporary_copies_removed = true; }
  catch (error) { report.failure ??= String(error); }
  report.passed = !report.failure && report.source_unchanged && report.temporary_copies_removed && report.baseline?.source_unchanged === true && baselinePassed(report.baseline)
    && report.mutations.length === mutations.length && report.mutations.every(item => item.outcome === 'killed-by-intended-assertion')
    && report.rejection_controls.length === 8 && report.rejection_controls.every(item => item.rejected);
  const path = resolve(process.argv[2] ?? join(root, 'browser/host/test-results/meeting-generation-mutations.json'));
  await mkdir(dirname(path), { recursive: true }); await writeFile(path, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ passed: report.passed, killed: report.mutations.filter(item => item.outcome === 'killed-by-intended-assertion').length, total: mutations.length,
    source_unchanged: report.source_unchanged, rejection_controls_passed: report.rejection_controls.every(item => item.rejected), report: path }) + '\n');
  if (!report.passed) process.exitCode = 1;
}
