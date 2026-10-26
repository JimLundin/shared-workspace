/** Assertion-targeted session mutants use one captured source snapshot. */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = resolve(import.meta.dirname, '..');
const harnessPath = 'scripts/check-mutations.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
async function sourcePaths(folder, relative = 'src') {
  const paths = [];
  for (const entry of await readdir(join(folder, relative), { withFileTypes: true })) {
    const path = join(relative, entry.name);
    if (entry.isDirectory()) paths.push(...await sourcePaths(folder, path));
    else if (entry.isFile()) paths.push(path);
    else throw new Error('Source snapshot contains an unsupported entry: ' + path);
  }
  return paths.sort();
}
async function snapshot(folder) {
  const paths = [...await sourcePaths(folder), 'package.json', 'package-lock.json', harnessPath].sort();
  return Object.fromEntries(await Promise.all(paths.map(async path => [path, await readFile(join(folder, path))])));
}
const files = Object.freeze(await snapshot(root));
const hashes = values => Object.fromEntries(Object.entries(values).map(([path, value]) => [path, hash(value)]));
const sameHashes = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const mutations = [
  {
    id: 'review-clears-newer-retry', file: 'src/connections-ui.ts', test_file: 'src/connections-ui.test.ts',
    test: 'late settings review cannot clear a pending retry or enable overlapping writes', marker: 'LATE_REVIEW_CLEARED_RETRY',
    from: '!this.#disposed&&this.mutation===pending&&accepted', to: '!this.#disposed',
  },
  {
    id: 'receipt-overwrites-newer-removal', file: 'src/connections-ui.ts', test_file: 'src/connections-ui.test.ts',
    test: 'late save acknowledgement preserves a newer removal already observed during the save', marker: 'LATE_RECEIPT_OVERWROTE_REMOVAL',
    from: 'if((this.#observations.get(id)??0)===observation)this.#values.set(id,receipt.state);', to: 'this.#values.set(id,receipt.state);',
  },
  {
    id: 'accept-another-operation-receipt', file: 'src/connections-ui.ts', test_file: 'src/connections-ui.test.ts',
    test: 'receipt correlation rejects another operation or workspace without dropping retry material', marker: 'UNRELATED_CONNECTION_RECEIPT_ACCEPTED',
    from: 'receipt.operation_id!==change.input.operation_id||receipt.state.workspace_id!==id', to: 'receipt.state.workspace_id!==id',
  },
  {
    id: 'late-read-overwrites-confirmed-write', file: 'src/connections-ui.ts', test_file: 'src/connections-ui.test.ts',
    test: 'read started while save is pending cannot overwrite its confirmed receipt', marker: 'LATE_READ_OVERWROTE_RECEIPT',
    from: 'this.#epochs.set(id,(this.#epochs.get(id)??0)+1);\n      if((this.#observations.get(id)??0)===observation)this.#values.set(id,receipt.state);',
    to: 'if((this.#observations.get(id)??0)===observation)this.#values.set(id,receipt.state);',
  },
  {
    id: 'replace-retained-editor', file: 'src/session.ts', test_file: 'src/session.test.ts',
    test: 'new application or note editors cannot replace retained drafts', marker: 'RETAINED_EDITOR_REPLACED',
    from: 'if(!this.state.editor&&this.snapshot()?.workspaces.some(w=>w.id===workspaceId))', to: 'if(this.snapshot()?.workspaces.some(w=>w.id===workspaceId))',
  },
  {
    id: 'adopt-unreviewed-heads', file: 'src/session.ts', test_file: 'src/session.test.ts',
    test: 'stale editor preserves its original heads until explicit reconciliation', marker: 'UNREVIEWED_HEADS_ADOPTED',
    from: 'expected_heads: [...(reviewedHeads ?? e.baseHeads)]',
    to: 'expected_heads: [...(reviewedHeads ?? (this.snapshot()?.resources.find(r => r.id === e.resourceId)?.heads.map(h => h.id) ?? e.baseHeads))]',
  },
  {
    id: 'substitute-retry-operation', file: 'src/session.ts', test_file: 'src/session.test.ts',
    test: 'retry an unconfirmed save preserves exact operation identity and content', marker: 'EXACT_RETRY_NOT_SUBMITTED',
    from: 'await this.submit(pending.command);', to: 'await this.submit({ ...pending.command, operation_id: this.id() });',
  },
  {
    id: 'discard-applied-submit-receipt', file: 'src/session.ts', test_file: 'src/session.test.ts',
    test: 'a submit result retains its Applied receipt while a newer snapshot is pending', marker: 'APPLIED_SUBMIT_RECEIPT_LOST_DURING_OVERLAPPING_REFRESH',
    from: '      return receipt;', to: '      return null;',
  },
  {
    id: 'expose-another-operation-receipt', file: 'src/session.ts', test_file: 'src/session.test.ts',
    test: 'receipt for another operation cannot report this edit saved', marker: 'MISMATCHED_SUBMIT_RECEIPT_EXPOSED',
    from: "if (receipt.type === 'applied') {\n        if (receipt.operation_id !== command.operation_id || receipt.workspace_id !== command.workspace_id)",
    to: "if (receipt.type === 'applied') {\n        if (receipt.workspace_id !== command.workspace_id)",
  },
  {
    id: 'accept-cancelled-recognition', file: 'src/voice-session.ts', test_file: 'src/voice.test.ts',
    test: 'late recognition cannot alter input after cancellation or a newer session', marker: 'CANCELLED_RECOGNITION_PUBLISHED',
    from: 'if (this.epoch !== epoch || event.session_id !== sessionId) return;', to: 'if (event.session_id !== sessionId) return;',
  },
];

async function materialize(folder) {
  for (const [path, value] of Object.entries(files)) {
    await mkdir(dirname(join(folder, path)), { recursive: true });
    await writeFile(join(folder, path), value);
  }
  await symlink(join(root, 'node_modules'), join(folder, 'node_modules'), 'dir');
}
function run(folder, mutation) {
  const args = ['--experimental-strip-types', '--test', '--test-reporter=tap'];
  if (mutation) args.push('--test-name-pattern', '^' + escapePattern(mutation.test) + '$', mutation.test_file);
  else args.push(...new Set(mutations.map(item => item.test_file)));
  const child = spawnSync(process.execPath, args, { cwd: folder, encoding: 'utf8', timeout: 10_000, maxBuffer: 4 * 1024 * 1024 });
  const output = (child.stdout ?? '') + (child.stderr ?? '');
  return { exit_code: child.status, signal: child.signal, error_code: child.error?.code ?? null, output_sha256: hash(output), output };
}
function baselinePassed(result) {
  return result.exit_code === 0 && result.signal === null && result.error_code === null
    && /^# fail 0$/m.test(result.output) && /^# cancelled 0$/m.test(result.output)
    && mutations.every(mutation => new RegExp('^ok \\d+ - ' + escapePattern(mutation.test) + '$', 'm').test(result.output));
}
function intendedAssertion(result, mutation) {
  if (result.exit_code !== 1 || result.signal !== null || result.error_code !== null) return false;
  const diagnostic = result.output.match(new RegExp('^not ok \\d+ - ' + escapePattern(mutation.test) + '\\n([\\s\\S]*?)^  \\.\\.\\.$', 'm'))?.[1];
  if (!diagnostic) return false;
  const marker = escapePattern(mutation.marker);
  // Match the assertion's message, not a marker printed by another test or stack trace.
  const assertionMessage = new RegExp('^  error: (?:[\'"]' + marker + '[\'"]|\\|[-+]?\\n    ' + marker + ')$', 'm');
  return /^  failureType: 'testCodeFailure'$/m.test(diagnostic) && /^  code: 'ERR_ASSERTION'$/m.test(diagnostic)
    && /^  name: 'AssertionError'$/m.test(diagnostic) && assertionMessage.test(diagnostic)
    && ['tests 1', 'pass 0', 'fail 1', 'cancelled 0', 'skipped 0', 'todo 0'].every(summary => result.output.includes('\n# ' + summary + '\n'));
}

const report = {
  kind: 'workspace-ui-targeted-mutations-v2', started_at: new Date().toISOString(), node: process.version,
  root: 'native/ui', source_hashes: hashes(files), harness_sha256: hash(files[harnessPath]),
  scope: 'WorkspaceSession, ConnectionSession and VoiceSession tests with injected host, credential and speech effects. No real Turso, peers, microphone, provider or rendered UI evidence.',
  rule: 'A passing unchanged baseline precedes every campaign. Only the exact selected test and assertion message count; setup errors, timeouts, signals and unrelated failures do not.',
  baseline: null, mutations: [], final_hashes: null, source_unchanged: false, temporary_copies_removed: false, passed: false,
};
const scratch = await mkdtemp(join(tmpdir(), 'workspace-ui-mutations-'));
try {
  const baselineFolder = join(scratch, 'baseline');
  await materialize(baselineFolder);
  const baseline = run(baselineFolder);
  report.baseline = { ...baseline, source_unchanged: sameHashes(report.source_hashes, hashes(await snapshot(baselineFolder))) };
  if (!baselinePassed(baseline) || !report.baseline.source_unchanged) throw new Error('Passing unchanged baseline required before mutation checks.');
  for (const mutation of mutations) {
    const source = files[mutation.file].toString('utf8');
    if (source.split(mutation.from).length !== 2) throw new Error('Missing or ambiguous mutation anchor: ' + mutation.id);
    if (files[mutation.test_file].toString('utf8').split(mutation.marker).length !== 2) throw new Error('Missing or ambiguous assertion marker: ' + mutation.id);
    const folder = join(scratch, mutation.id), changed = source.replace(mutation.from, mutation.to);
    await materialize(folder);
    await writeFile(join(folder, mutation.file), changed);
    const observed = run(folder, mutation);
    const mutatedHashes = { ...report.source_hashes, [mutation.file]: hash(changed) };
    const sourceUnchanged = sameHashes(mutatedHashes, hashes(await snapshot(folder)));
    const intended = sourceUnchanged && intendedAssertion(observed, mutation);
    const outcome = intended ? 'killed-by-intended-assertion' : sourceUnchanged && observed.exit_code === 0 ? 'survived' : 'invalid-or-unrelated-failure';
    report.mutations.push({ ...mutation, ...observed, mutated_source_sha256: hash(changed), source_unchanged: sourceUnchanged, outcome });
    console.log(JSON.stringify({ id: mutation.id, outcome }));
  }
} catch (error) {
  report.failure = String(error);
} finally {
  try {
    report.final_hashes = hashes(await snapshot(root));
    report.source_unchanged = sameHashes(report.source_hashes, report.final_hashes);
  } catch (error) { report.failure ??= String(error); }
  try { await rm(scratch, { recursive: true, force: true }); report.temporary_copies_removed = true; }
  catch (error) { report.failure ??= String(error); }
  report.passed = !report.failure && report.source_unchanged && report.temporary_copies_removed
    && report.baseline?.source_unchanged === true && baselinePassed(report.baseline)
    && report.mutations.length === mutations.length && report.mutations.every(item => item.outcome === 'killed-by-intended-assertion');
  const path = resolve(process.argv[2] ?? join(root, 'test-results/mutations.json'));
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, killed: report.mutations.filter(item => item.outcome === 'killed-by-intended-assertion').length, total: mutations.length, source_unchanged: report.source_unchanged, report: path }));
  if (!report.passed) process.exitCode = 1;
}
