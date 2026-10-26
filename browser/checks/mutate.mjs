import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cp, mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const checks = dirname(fileURLToPath(import.meta.url));
const source = resolve(checks, '../..');
const argument = name => {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return resolve(process.argv[index + 1]);
};
const baselinePath = argument('--baseline-report');
const evidence = argument('--evidence');
await mkdir(dirname(evidence), { recursive: true });
await mkdir(evidence);
const privateRoot = await mkdtemp(join(tmpdir(), 'workspace-browser-mutations-'));
const copied = join(privateRoot, 'source');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
const baselineBytes = await readFile(baselinePath);
const baseline = JSON.parse(baselineBytes);
const planPath = join(checks, 'mutations.json');
const planBytes = await readFile(planPath);
const plan = JSON.parse(planBytes);
const report = {
  kind: 'browser-workspace-boundary-mutations-v1', passed: false,
  started_at: new Date().toISOString(), baseline_report_sha256: hash(baselineBytes),
  harness_sha256: hash(await readFile(fileURLToPath(import.meta.url))), plan_sha256: hash(planBytes),
  source_hashes: baseline.source_hashes, mutations: [],
  policy: 'Only the named behavioral failure after the required acknowledged-work assertions counts. Startup, compilation, transport, timeout, source changes and unrelated failures do not count.',
};
async function verifyHashes(root, expected) {
  for (const [path, expectedHash] of Object.entries(expected)) {
    if (hash(await readFile(join(root, path))) !== expectedHash) throw new Error(`Source changed: ${path}`);
  }
}
async function execute(label, selectedCase) {
  const directory = join(privateRoot, label);
  await mkdir(directory);
  const output = join(directory, 'evidence');
  const command = [join(copied, 'browser/checks/check.mjs'), '--case', selectedCase, '--evidence', output];
  console.log(JSON.stringify({ stage: label, case: selectedCase }));
  const child = spawn(process.execPath, command, { cwd: copied, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '', timedOut = false;
  child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
  child.stdout.on('data', text => { stdout += text; });
  child.stderr.on('data', text => { stderr += text; });
  const timer = setTimeout(() => {
    timedOut = true;
    try { process.platform === 'win32' ? child.kill('SIGKILL') : process.kill(-child.pid, 'SIGKILL'); } catch { /* Already exited. */ }
  }, 240_000);
  let outcome;
  try {
    outcome = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => resolve({ code, signal }));
    });
  } finally { clearTimeout(timer); }
  await writeFile(join(directory, 'stdout.log'), stdout);
  await writeFile(join(directory, 'stderr.log'), stderr);
  if (timedOut) throw new Error('Browser mutation check timed out; timeout is not a detection');
  const run = await readJson(join(output, 'report.json'));
  return { ...outcome, report: run, command: [process.execPath, ...command], stdout_sha256: hash(stdout), stderr_sha256: hash(stderr), report_sha256: hash(await readFile(join(output, 'report.json'))) };
}

try {
  if (!baseline.passed || !baseline.source_unchanged || baseline.case !== 'all') throw new Error('A passing unchanged combined browser baseline is required');
  await verifyHashes(source, baseline.source_hashes);
  const ignored = new Set(['node_modules', 'target', 'dist', 'evidence']);
  for (const path of ['browser/checks', 'browser/host', 'browser/host-wasm']) {
    await cp(join(source, path), join(copied, path), { recursive: true, filter: path => !ignored.has(basename(path)) });
  }
  for (const path of ['native/core/src/model.rs', 'native/core/src/work.rs', 'native/ui/src/protocol.ts', 'native/ui/src/meeting-preparation.ts']) {
    await mkdir(dirname(join(copied, path)), { recursive: true });
    await cp(join(source, path), join(copied, path));
  }
  for (const path of ['browser/checks', 'browser/host']) await symlink(join(source, path, 'node_modules'), join(copied, path, 'node_modules'), 'dir');
  await verifyHashes(copied, baseline.source_hashes);
  const clean = await execute('unmodified-copy', 'durability');
  if (clean.code !== 0 || !clean.report.passed || !clean.report.source_unchanged) throw new Error('Unmodified disposable browser boundary check did not pass');
  report.unmodified_copy = { passed: true, assertions: clean.report.assertions, report_sha256: clean.report_sha256, assets: clean.report.assets };
  for (const mutation of plan.mutations) {
    if (mutation.path !== 'browser/host/src/store.ts') throw new Error('This focused campaign may mutate only the browser storage boundary');
    const path = join(copied, mutation.path), original = await readFile(path, 'utf8');
    if (original.split(mutation.before).length !== 2) throw new Error('Mutation must match exactly one site');
    const changed = original.replace(mutation.before, mutation.after);
    await writeFile(path, changed);
    try {
      const result = await execute(mutation.id, mutation.case);
      const intended = result.code !== 0 && result.report.source_unchanged === true && result.report.failure?.type === 'behavioral' && result.report.failure.assertion === mutation.expected_assertion && mutation.required_prior_assertions.every(name => result.report.assertions.includes(name));
      report.mutations.push({
        id: mutation.id, description: mutation.description, path: mutation.path, before: mutation.before, after: mutation.after,
        source_before_sha256: hash(original), mutant_source_sha256: hash(changed), expected_assertion: mutation.expected_assertion,
        outcome: intended ? 'detected' : result.code === 0 ? 'survived' : 'unrelated_or_setup_failure',
        observed_failure: result.report.failure, prior_assertions: result.report.assertions,
        command: result.command, returncode: result.code, report_sha256: result.report_sha256,
        stdout_sha256: result.stdout_sha256, stderr_sha256: result.stderr_sha256, assets: result.report.assets,
        browser_launches: result.report.browser_launches, browser_closes: result.report.browser_closes,
        profiles_removed: result.report.profiles_removed, static_server_closed: result.report.static_server_closed,
      });
    } finally { await writeFile(path, original); }
  }
  await verifyHashes(copied, baseline.source_hashes);
  report.disposable_source_restored = true;
  report.passed = report.mutations.length > 0 && report.mutations.every(value => value.outcome === 'detected');
} catch (error) {
  report.failure = { type: 'setup', detail: error.message };
} finally {
  try { await verifyHashes(source, baseline.source_hashes); report.source_unchanged = true; }
  catch (error) { report.source_unchanged = false; report.passed = false; report.failure ??= { type: 'setup', detail: error.message }; }
  report.finished_at = new Date().toISOString();
  await writeFile(join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, mutations: report.mutations.map(({ id, outcome }) => ({ id, outcome })), failure: report.failure, report: join(evidence, 'report.json'), private_logs: privateRoot }, null, 2));
  if (!report.passed) process.exitCode = 1;
}
