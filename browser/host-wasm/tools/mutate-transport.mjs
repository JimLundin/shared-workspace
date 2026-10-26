import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { cp, mkdtemp, mkdir, readFile, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const source = await readFile(resolve(root, 'src/transport.rs'), 'utf8');
const baseline = JSON.parse(await readFile(resolve(root, 'evidence/transport.json')));
assert.equal(baseline.status, 'passed');
assert.equal(baseline.source_sha256, createHash('sha256').update(source).digest('hex'));
const site = '&JsValue::from_str(&peer_id),';
assert.equal(source.split(site).length, 2);
const scratch = await mkdtemp(resolve(tmpdir(), 'browser-host-transport-mutant-'));
const crate = resolve(scratch, 'browser/host-wasm');
await mkdir(crate, { recursive: true });
for (const path of ['Cargo.toml', 'Cargo.lock', 'build.sh', 'src', 'tools/check-transport.mjs']) {
  await cp(resolve(root, path), resolve(crate, path), { recursive: true });
}
await mkdir(resolve(scratch, 'native/core/src'), { recursive: true });
await cp(resolve(root, '../../native/core/src/model.rs'), resolve(scratch, 'native/core/src/model.rs'));
await cp(resolve(root, '../../native/core/src/work.rs'), resolve(scratch, 'native/core/src/work.rs'));
await mkdir(resolve(scratch, 'browser/iroh-probe'), { recursive: true });
await symlink(resolve(root, '../iroh-probe/node_modules'), resolve(scratch, 'browser/iroh-probe/node_modules'), 'dir');
await writeFile(resolve(crate, 'src/transport.rs'), source.replace(site,
  '&JsValue::from_str(serde_json::from_str::<serde_json::Value>(&payload).ok().and_then(|v| v.get("claimed_id").and_then(|id| id.as_str()).map(str::to_owned)).as_deref().unwrap_or(&peer_id)),'));
for (const path of ['Cargo.toml', 'Cargo.lock']) {
  const text = await readFile(resolve(crate, path), 'utf8');
  await writeFile(resolve(crate, path), text.replace('name = "browser-workspace-host"', 'name = "browser-transport-mutant"'));
}
const buildScript = await readFile(resolve(crate, 'build.sh'), 'utf8');
await writeFile(resolve(crate, 'build.sh'), buildScript.replace('release/browser_workspace_host.wasm', 'release/browser_transport_mutant.wasm'));
const build = spawnSync('bash', ['build.sh'], { cwd: crate, env: process.env, encoding: 'utf8', timeout: 300_000 });
assert.equal(build.status, 0, `Mutant must compile: ${build.stderr}`);
const check = spawnSync(process.execPath, ['tools/check-transport.mjs'], { cwd: crate, env: process.env, encoding: 'utf8', timeout: 120_000 });
const result = JSON.parse(await readFile(resolve(crate, 'evidence/transport.json')));
assert.notEqual(check.status, 0);
assert.match(result.failure, /Missing expected rejection/);
assert.equal(result.observations.length, 2, 'Shared identity and both positive exchanges must pass first');
assert.equal(result.browser_errors.length, 0);
assert.equal(await readFile(resolve(root, 'src/transport.rs'), 'utf8'), source);
const report = {
  status: 'killed',
  mutation: 'Supply a claimed payload identity to the host callback instead of the authenticated Iroh identity',
  baseline_source_sha256: baseline.source_sha256,
  baseline_wasm_sha256: baseline.wasm_sha256,
  mutant_wasm_sha256: result.wasm_sha256,
  observed_failure: 'The real third browser forged the allowed peer ID and was accepted; rejection assertion failed',
  timestamp: new Date().toISOString(),
};
await writeFile(resolve(root, 'evidence/transport-mutation.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
