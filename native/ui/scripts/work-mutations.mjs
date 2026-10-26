/** Work/recovery fixture mutations run against isolated, hashed source captures. */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = resolve(import.meta.dirname, '..');
const paths = ['package.json', 'src/work-controller.ts', 'src/work.ts', 'src/retained-input.ts', 'src/retained-commands.ts',
  'src/meeting-preparation.ts', 'src/protocol.ts', 'src/session.ts', 'src/pairing.ts',
  'src/work-controller.test.ts', 'src/work.test.ts', 'src/retained-input.test.ts', 'scripts/work-mutations.mjs'];
const hash = value => createHash('sha256').update(value).digest('hex');
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const capture = async folder => Object.fromEntries(await Promise.all(paths.map(async path => [path, await readFile(join(folder, path))])));
const hashes = files => Object.fromEntries(Object.entries(files).map(([path, value]) => [path, hash(value)]));
const files = Object.freeze(await capture(root));
const mutations = [
  { id: 'replace-pinned-transcript', file: 'src/work.ts', test_file: 'src/work.test.ts', test: 'source lookup preserves exact workspace, resource and historical revision identity', marker: 'PINNED_SOURCE_REPLACED_BY_CURRENT_HEAD', from: 'revision.id === source.revision_id', to: 'true' },
  { id: 'allow-peer-resume', file: 'src/work.ts', test_file: 'src/work.test.ts', test: 'preparation requires pinned transcripts and resume belongs to the originating device', marker: 'PEER_PREPARATION_TAKEN_OVER', from: 'work.state.run.originating_device !== snapshot.device_id', to: 'false' },
  { id: 'skip-generated-provenance', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'invalid generated provenance cannot reach atomic publication', marker: 'INVALID_PROVENANCE_PUBLISHED', from: '!preparedMeetingFits(capturedSnapshot, binding, result)', to: 'false' },
  { id: 'allow-concurrent-retention-click', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'local retention must complete before command dispatch and concurrent clicks are gated during it', marker: 'WORK_NOT_RESERVED_DURING_RETENTION', from: "    this.#update({ type: 'saving', command });", to: '    // Mutation removes reservation before retention.' },
  { id: 'dispatch-before-retention', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'local retention must complete before command dispatch and concurrent clicks are gated during it', marker: 'WORK_DISPATCHED_BEFORE_RETENTION', from: "try { await this.#journal?.put(SLOT, 'meeting', { type: 'command'", to: "try { void this.#journal?.put(SLOT, 'meeting', { type: 'command'" },
  { id: 'ignore-refresh-failure', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'failed refresh after a committed command cannot clear recovery or report creation success', marker: 'FAILED_REFRESH_REPORTED_SUCCESS', from: "this.#session.state.host.type !== 'ready' || !observed", to: 'false' },
  { id: 'redispatch-known-commit', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'known committed recovery after reopen only refreshes and cleans without redispatching', marker: 'KNOWN_COMMIT_REDISPATCHED', from: 'await this.#finishCommitted(this.#recover(this.#state.command))', to: 'await this.#send(this.#state.command)' },
  { id: 'allow-new-input-during-committed-recovery', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'committed recovery is reserved before reading and cannot overlap or clear later input', marker: 'WORK_RECOVERY_NOT_RESERVED', from: " || this.#state.type === 'recovering'", to: '' },
  { id: 'allow-new-input-during-discard', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'discarding interrupted input reserves cleanup against repeated clicks and new commands', marker: 'DISCARD_CLEANUP_NOT_RESERVED', from: " || this.#state.type === 'discarding'", to: '' },
  { id: 'prepare-over-newer-command', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'preparation yields to a newer command started after its begin save', marker: 'PREPARATION_STARTED_OVER_NEW_ATTEMPT', from: '  async #run(binding: PreparationBinding): Promise<void> {\n    if (!this.#available(true)) return;', to: '  async #run(binding: PreparationBinding): Promise<void> {' },
  { id: 'swallow-connection-interruption', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'interruption is not swallowed when connection or provider resolves after abort', marker: 'INTERRUPTED_WORK_STUCK_PREPARING', occurrences: 2, from: "if (controller.signal.aborted) throw new Error('Preparation interrupted.');", to: 'if (controller.signal.aborted) return;' },
  { id: 'change-unknown-retry-id', file: 'src/work-controller.ts', test_file: 'src/work-controller.test.ts', test: 'unknown publication retries exact identities after reopen without provider rerun or duplicate outputs', marker: 'PUBLICATION_RETRY_CHANGED_AFTER_REOPEN', from: '    await this.#send(command);', to: '    await this.#send({ ...command, operation_id: this.#id() });' },
  { id: 'clear-other-slot-failure', file: 'src/retained-input.ts', test_file: 'src/retained-input.test.ts', test: 'success in another slot cannot erase a failed input or deduplicate its retry', marker: 'UNRELATED_SAVE_CLEARED_RETENTION_FAILURE', from: 'this.#errors.delete(id);', to: 'this.#errors.clear();' },
  { id: 'relabel-unchanged-owner', file: 'src/work.ts', test_file: 'src/work.test.ts', test: 'editing only action text preserves unchanged owner and deadline transcript evidence', marker: 'UNCHANGED_OWNER_EVIDENCE_RELABELED', from: 'ownerName === currentOwner ? action.owner : ownerName ?', to: 'ownerName ?' },
];
async function materialize(folder) {
  for (const [path, value] of Object.entries(files)) { await mkdir(dirname(join(folder, path)), { recursive: true }); await writeFile(join(folder, path), value); }
}
function run(folder, mutation) {
  const args = ['--experimental-strip-types', '--test', '--test-reporter=tap'];
  if (mutation) args.push('--test-name-pattern', '^' + escape(mutation.test) + '$', mutation.test_file);
  else args.push('src/work.test.ts', 'src/retained-input.test.ts', 'src/work-controller.test.ts');
  const result = spawnSync(process.execPath, args, { cwd: folder, encoding: 'utf8', timeout: 15_000, maxBuffer: 4 * 1024 * 1024 });
  const output = (result.stdout ?? '') + (result.stderr ?? '');
  return { exit_code: result.status, signal: result.signal, error_code: result.error?.code ?? null, output_sha256: hash(output), output };
}
function baselinePassed(result) {
  return result.exit_code === 0 && result.signal === null && result.error_code === null && /^# fail 0$/m.test(result.output) && /^# cancelled 0$/m.test(result.output)
    && mutations.every(item => new RegExp('^ok \\d+ - ' + escape(item.test) + '$', 'm').test(result.output));
}
function intendedAssertion(result, mutation) {
  if (result.exit_code !== 1 || result.signal !== null || result.error_code !== null) return false;
  const diagnostic = result.output.match(new RegExp('^not ok \\d+ - ' + escape(mutation.test) + '\\n([\\s\\S]*?)^  \\.\\.\\.$', 'm'))?.[1];
  if (!diagnostic) return false;
  const message = new RegExp('^  error: (?:[\'"]' + escape(mutation.marker) + '[\'"]|\\|[-+]?\\n    ' + escape(mutation.marker) + ')$', 'm');
  return /^  failureType: 'testCodeFailure'$/m.test(diagnostic) && /^  code: 'ERR_ASSERTION'$/m.test(diagnostic) && /^  name: 'AssertionError'$/m.test(diagnostic)
    && message.test(diagnostic) && ['tests 1', 'pass 0', 'fail 1', 'cancelled 0', 'skipped 0', 'todo 0'].every(summary => result.output.includes('\n# ' + summary + '\n'));
}
const report = {
  kind: 'work-continuity-fixture-mutations-v1', started_at: new Date().toISOString(), node: process.version, source_hashes: hashes(files),
  evidence: 'Controlled host/provider/local-draft fixtures with real UI decisions and decoders. No authenticated provider, real Turso/Iroh peer, rendered UI or physical-device claim.',
  rule: 'Passing captured baseline required; only one exact intended AssertionError counts. Setup failures, timeouts, unrelated assertions, and cancellation do not.',
  baseline: null, mutations: [], source_unchanged: false, temporary_copies_removed: false, passed: false,
};
const scratch = await mkdtemp(join(tmpdir(), 'work-continuity-mutations-'));
try {
  const baselineFolder = join(scratch, 'baseline'); await materialize(baselineFolder);
  report.baseline = run(baselineFolder);
  if (!baselinePassed(report.baseline)) throw new Error('The unchanged captured baseline must pass first.');
  for (const mutation of mutations) {
    const source = files[mutation.file].toString('utf8');
    if (source.split(mutation.from).length !== (mutation.occurrences ?? 1) + 1) throw new Error('Missing or ambiguous mutation anchor: ' + mutation.id);
    if (files[mutation.test_file].toString('utf8').split(mutation.marker).length !== 2) throw new Error('Missing or ambiguous assertion marker: ' + mutation.id);
    const folder = join(scratch, mutation.id); await materialize(folder);
    // For a repeated guard only the first occurrence is changed, as documented by the mutation ID.
    const changed = source.replace(mutation.from, mutation.to); await writeFile(join(folder, mutation.file), changed);
    const observed = run(folder, mutation), outcome = intendedAssertion(observed, mutation) ? 'killed-by-intended-assertion' : observed.exit_code === 0 ? 'survived' : 'invalid-or-unrelated-failure';
    report.mutations.push({ ...mutation, ...observed, mutated_source_sha256: hash(changed), outcome });
    process.stdout.write(JSON.stringify({ id: mutation.id, outcome }) + '\n');
  }
} catch (error) { report.failure = String(error); }
finally {
  try { report.final_hashes = hashes(await capture(root)); report.source_unchanged = JSON.stringify(report.source_hashes) === JSON.stringify(report.final_hashes); }
  catch (error) { report.failure ??= String(error); }
  try { await rm(scratch, { recursive: true, force: true }); report.temporary_copies_removed = true; }
  catch (error) { report.failure ??= String(error); }
  report.passed = !report.failure && report.source_unchanged && report.temporary_copies_removed && report.baseline && baselinePassed(report.baseline)
    && report.mutations.length === mutations.length && report.mutations.every(item => item.outcome === 'killed-by-intended-assertion');
  const path = resolve(process.argv[2] ?? join(root, 'test-results/work-mutations.json'));
  await mkdir(dirname(path), { recursive: true }); await writeFile(path, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ passed: Boolean(report.passed), killed: report.mutations.filter(item => item.outcome === 'killed-by-intended-assertion').length, total: mutations.length, source_unchanged: report.source_unchanged, report: path }) + '\n');
  if (!report.passed) process.exitCode = 1;
}
