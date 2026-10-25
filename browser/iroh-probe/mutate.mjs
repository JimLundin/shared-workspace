import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { cp, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('.', import.meta.url));
const source = await readFile(resolve(root, 'src/lib.rs'), 'utf8');
const baseline = JSON.parse(await readFile(resolve(root, 'evidence/browser-exchange.json')));
assert.equal(baseline.status, 'passed', 'Require a passing unchanged baseline first');
assert.equal(baseline.source_sha256['src/lib.rs'], createHash('sha256').update(source).digest('hex'));
const candidate = 'if observed.borrow().allowed_peer != Some(peer_id) {';
assert.equal(source.split(candidate).length, 2, 'Mutation must have exactly one site');
const scratch = await mkdtemp(resolve(tmpdir(), 'browser-iroh-mutant-'));
for (const path of ['Cargo.toml', 'Cargo.lock', 'build.sh', 'src', 'index.html', 'page.js', 'check.mjs']) {
  await cp(resolve(root, path), resolve(scratch, path), { recursive: true });
}
await symlink(resolve(root, 'node_modules'), resolve(scratch, 'node_modules'), 'dir');
await writeFile(resolve(scratch, 'src/lib.rs'), source.replace(candidate, 'if false {'));
// Keep the disposable artifact distinct even when it reuses dependency caches.
const manifest = await readFile(resolve(scratch, 'Cargo.toml'), 'utf8');
await writeFile(resolve(scratch, 'Cargo.toml'), manifest.replace('name = "browser-iroh-probe"', 'name = "browser-iroh-mutant"'));
const lockfile = await readFile(resolve(scratch, 'Cargo.lock'), 'utf8');
await writeFile(resolve(scratch, 'Cargo.lock'), lockfile.replace('name = "browser-iroh-probe"', 'name = "browser-iroh-mutant"'));
const buildScript = await readFile(resolve(scratch, 'build.sh'), 'utf8');
await writeFile(resolve(scratch, 'build.sh'), buildScript
  .replace('--out-dir pkg', '--out-dir pkg --out-name browser_iroh_probe')
  .replace('browser_iroh_probe.wasm', 'browser_iroh_mutant.wasm'));
const build = spawnSync('bash', ['build.sh'], { cwd: scratch, env: process.env, encoding: 'utf8', timeout: 300_000 });
assert.equal(build.status, 0, `Mutant must compile before counting a kill: ${build.stderr}`);
const check = spawnSync(process.execPath, ['check.mjs'], { cwd: scratch, env: process.env, encoding: 'utf8', timeout: 90_000 });
const result = JSON.parse(await readFile(resolve(scratch, 'evidence/browser-exchange.json')));
assert.notEqual(check.status, 0, 'Removing the incoming identity check must fail acceptance');
assert.match(result.failure, /Missing expected rejection/);
assert.equal(result.browser_errors.length, 0);
assert.equal(result.observations.length, 2, 'Positive peer exchanges must have completed before rejection failure');
assert.equal(await readFile(resolve(root, 'src/lib.rs'), 'utf8'), source, 'Live source must remain unchanged');
const report = {
  status: 'killed',
  mutation: 'Remove incoming selected-peer identity guard',
  baseline_wasm_sha256: baseline.wasm_sha256,
  baseline_source_sha256: baseline.source_sha256,
  mutant_wasm_sha256: result.wasm_sha256,
  observed_failure: 'Authenticated but unselected third browser peer was accepted; rejection assertion failed',
  timestamp: new Date().toISOString(),
};
await writeFile(resolve(root, 'evidence/identity-mutation.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
