/** Run assertion-targeted mutants against a captured source snapshot. */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = resolve(import.meta.dirname, '..');
const sourcePath = 'src/provider-voice.ts', testPath = 'src/provider-voice.test.ts';
const harnessPath = 'tests/provider-voice-mutations.mjs';
const inputPaths = [sourcePath, testPath, harnessPath, 'package.json', 'package-lock.json'].sort();
const hash = value => createHash('sha256').update(value).digest('hex');
const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const files = Object.freeze(Object.fromEntries(await Promise.all(inputPaths.map(async path => [path, await readFile(join(root, path))]))));
const source = files[sourcePath].toString('utf8');
const mutations = [
  {
    id: 'share-mutable-context',
    from: 'context = Object.freeze({ workspaceId: snapshot.workspaceId, connectionVersion: snapshot.connectionVersion });', to: 'context = snapshot;',
    test: 'workspace and connection are copied before microphone permission resolves', marker: 'CAPTURE_CONTEXT_RETARGETED',
  },
  {
    id: 'accept-late-transcription',
    from: 'if (this.#active !== active) return;\n      if (typeof text', to: 'if (typeof text',
    test: 'cancel aborts provider work and fences a late result after a new session starts', marker: 'CANCELLED_TRANSCRIPT_PUBLISHED',
  },
  {
    id: 'leak-late-microphone',
    from: 'if (this.#active !== active) { capture.release(); return; }', to: 'if (this.#active !== active) { return; }',
    test: 'cancelling a pending permission request releases its late microphone without recording', marker: 'LATE_MICROPHONE_NOT_RELEASED',
  },
  {
    id: 'ignore-audio-size', from: 'active.bytes + chunk.size > PROVIDER_VOICE_LIMITS.audioBytes', to: 'false',
    test: 'oversized recording is rejected and never uploaded, including late final chunks', marker: 'OVERSIZED_RECORDING_UPLOADED',
  },
  {
    id: 'omit-provider-abort', from: 'active.abort.abort();', to: '',
    test: 'cancel aborts provider work and fences a late result after a new session starts', marker: 'PROVIDER_UPLOAD_NOT_ABORTED',
  },
  {
    id: 'accept-empty-transcript', from: '!text.trim() || text.length', to: 'text.length',
    test: 'empty and oversized provider output are errors rather than partial or successful transcripts', marker: 'INVALID_PROVIDER_TRANSCRIPT_ACCEPTED',
  },
  {
    id: 'upload-before-final-chunk', from: 'try { capture.stop(); }\n    catch', to: 'try { capture.stop(); this.#recordingFinished(active); }\n    catch',
    test: 'provider capture negotiates Safari MP4 and does not report success before the final upload result', marker: 'AUDIO_UPLOADED_BEFORE_FINAL_CHUNK',
  },
  {
    id: 'force-language-by-default', from: "this.#locale = options.locale ?? '';", to: "this.#locale = options.locale ?? 'en-US';",
    test: 'default transcription language is automatic rather than the browser interface language', marker: 'AUTOMATIC_TRANSCRIPTION_LANGUAGE_REPLACED',
  },
];

const hashes = values => Object.fromEntries(Object.entries(values).map(([path, value]) => [path, hash(value)]));
const sameHashes = (left, right) => JSON.stringify(left) === JSON.stringify(right);
async function hashesAt(folder) {
  return hashes(Object.fromEntries(await Promise.all(inputPaths.map(async path => [path, await readFile(join(folder, path))]))));
}
async function materialize(folder) {
  for (const [path, value] of Object.entries(files)) {
    await mkdir(dirname(join(folder, path)), { recursive: true });
    await writeFile(join(folder, path), value);
  }
}
function run(folder, test) {
  const args = ['--experimental-strip-types', '--test', '--test-reporter=tap'];
  if (test) args.push('--test-name-pattern', '^' + escapePattern(test) + '$');
  args.push(testPath);
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
  kind: 'provider-voice-targeted-mutations-v2', started_at: new Date().toISOString(), node: process.version,
  root: 'browser/app', source_hashes: hashes(files), harness_sha256: hash(files[harnessPath]),
  scope: 'ProviderVoice with injected capture and transcription effects. No real microphone, authenticated provider, peer or browser lifecycle evidence.',
  rule: 'A passing unchanged baseline precedes every campaign. Only the exact selected test and assertion message count; setup errors, timeouts, signals and unrelated failures do not.',
  baseline: null, mutations: [], final_hashes: null, source_unchanged: false, temporary_copies_removed: false, passed: false,
};
const scratch = await mkdtemp(join(tmpdir(), 'provider-voice-mutations-'));
try {
  const baselineFolder = join(scratch, 'baseline');
  await materialize(baselineFolder);
  const baseline = run(baselineFolder);
  report.baseline = { ...baseline, source_unchanged: sameHashes(report.source_hashes, await hashesAt(baselineFolder)) };
  if (!baselinePassed(baseline) || !report.baseline.source_unchanged) throw new Error('Passing unchanged baseline required before mutation checks.');
  for (const mutation of mutations) {
    if (source.split(mutation.from).length !== 2) throw new Error('Missing or ambiguous mutation anchor: ' + mutation.id);
    if (files[testPath].toString('utf8').split(mutation.marker).length !== 2) throw new Error('Missing or ambiguous assertion marker: ' + mutation.id);
    const folder = join(scratch, mutation.id), changed = source.replace(mutation.from, mutation.to);
    await materialize(folder);
    await writeFile(join(folder, sourcePath), changed);
    const observed = run(folder, mutation.test);
    const mutatedHashes = { ...report.source_hashes, [sourcePath]: hash(changed) };
    const sourceUnchanged = sameHashes(mutatedHashes, await hashesAt(folder));
    const intended = sourceUnchanged && intendedAssertion(observed, mutation);
    const outcome = intended ? 'killed-by-intended-assertion' : sourceUnchanged && observed.exit_code === 0 ? 'survived' : 'invalid-or-unrelated-failure';
    report.mutations.push({ ...mutation, ...observed, mutated_source_sha256: hash(changed), source_unchanged: sourceUnchanged, outcome });
    console.log(JSON.stringify({ id: mutation.id, outcome }));
  }
} catch (error) {
  report.failure = String(error);
} finally {
  try {
    report.final_hashes = await hashesAt(root);
    report.source_unchanged = sameHashes(report.source_hashes, report.final_hashes);
  } catch (error) { report.failure ??= String(error); }
  try { await rm(scratch, { recursive: true, force: true }); report.temporary_copies_removed = true; }
  catch (error) { report.failure ??= String(error); }
  report.passed = !report.failure && report.source_unchanged && report.temporary_copies_removed
    && report.baseline?.source_unchanged === true && baselinePassed(report.baseline)
    && report.mutations.length === mutations.length && report.mutations.every(item => item.outcome === 'killed-by-intended-assertion');
  const path = resolve(process.argv[2] ?? '/tmp/provider-voice-mutation-evidence.json');
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, killed: report.mutations.filter(item => item.outcome === 'killed-by-intended-assertion').length, total: mutations.length, source_unchanged: report.source_unchanged, report: path }));
  if (!report.passed) process.exitCode = 1;
}
