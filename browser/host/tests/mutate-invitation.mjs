/** Targeted codec mutants; all executions use disposable copies, never live source. */
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';

const root = resolve(import.meta.dirname, '../../..');
const codec = 'browser/host/src/invitation.ts';
const types = 'browser/host/src/types.ts';
const protocol = 'native/ui/src/protocol.ts';
const testFile = 'browser/host/tests/invitation.test.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
const files = Object.fromEntries(await Promise.all([codec, types, protocol, testFile].map(async path => [path, await readFile(join(root, path), 'utf8')])));
const options = parseArgs({ options: { report: { type: 'string' } } }).values;
const identityTest = 'compact records preserve every peer identity byte, nonce byte, and selected workspace';
const mutations = [
  { id: 'truncate-peer-identity', file: codec, from: 'rawHex(ticket.inviter.address.id)', to: "rawHex(ticket.inviter.address.id.slice(0, 32) + '00'.repeat(16))", test: identityTest, marker: 'PEER_ID_BYTES_CHANGED' },
  { id: 'replace-pairing-nonce', file: codec, from: 'rawHex(ticket.nonce)', to: "rawHex('00'.repeat(32))", test: identityTest, marker: 'NONCE_BYTES_CHANGED' },
  { id: 'accept-duplicate-workspace-scope', file: types, from: 'new Set(values).size === values.length', to: 'true', test: 'compact decoder rejects duplicate selected workspaces', marker: 'DUPLICATE_SCOPE_ACCEPTED' },
  { id: 'ignore-trailing-payload', file: codec, from: 'position === data.length', to: 'true', test: 'truncated records and trailing bytes never produce a partial invitation', marker: 'TRAILING_BYTES_ACCEPTED' },
  { id: 'accept-unsafe-expiry-integer', file: protocol, from: '!Number.isSafeInteger(value)', to: '!Number.isFinite(value)', test: 'compact decoder rejects expiry outside the exact integer range', marker: 'UNSAFE_EXPIRY_ACCEPTED' },
  { id: 'replace-malformed-utf8', file: codec, from: 'fatal: true', to: 'fatal: false', test: 'compact text rejects invalid UTF8 instead of replacing malformed bytes', marker: 'INVALID_UTF8_NAME_ACCEPTED' },
  { id: 'accept-base64-alias', file: codec, from: 'base64(decoded) === value', to: 'true', test: 'noncanonical base64 aliases and raw padding are rejected', marker: 'NONCANONICAL_ENCODING_ACCEPTED' },
];
const report = {
  kind: 'invitation-codec-targeted-mutations-v1', node: process.version,
  sourceHashes: Object.fromEntries(Object.entries(files).map(([path, value]) => [path, hash(value)])),
  harnessSha256: hash(await readFile(import.meta.filename)),
  scope: 'Pure invitation codec and shared validators in isolated Node processes. No network, host store, pairing grant, browser lifecycle, or application effects exercised.',
  rule: 'Only the named test failing with its intended assertion marker counts. Setup errors, syntax/import failures, timeouts, signals, and unrelated failures do not count.',
  baseline: null, mutations: [], originalSourceAndChecksUnchanged: false, passed: false,
};
const scratch = await mkdtemp(join(tmpdir(), 'toolkit-invitation-mutants-'));
function run(folder, testName) {
  const args = ['--experimental-strip-types', '--test', '--test-reporter=tap'];
  if (testName) args.push('--test-name-pattern', '^' + testName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$');
  args.push(testFile);
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
      && observed.output.includes('not ok 1 - ' + mutation.test)
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
