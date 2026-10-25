import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readFile, writeFile, readdir, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { dirname, resolve, join, extname, sep } from 'node:path';
import { tmpdir, release, platform, arch } from 'node:os';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { build } from 'vite';
import { chromium } from 'playwright';

const root = dirname(fileURLToPath(import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), 'workspace-memory-audit-'));
const repetitions = Number(process.argv.find(value => value.startsWith('--repetitions='))?.split('=')[1] ?? 3);
const only = process.argv.find(value => value.startsWith('--only='))?.split('=')[1];
const startupOnly = process.argv.includes('--startup-only');
const gracefulClose = process.argv.includes('--graceful-close');
const variants = ['empty', 'turso-default', 'turso-minimum', 'sqlite'].filter(value => !only || value === only);
const commonPath = join(root, 'node_modules/@tursodatabase/database-wasm-common/dist/index.js');
const originalCommon = await readFile(commonPath, 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const report = {
  kind: 'database-wasm-memory-audit-20261025', started_at: new Date().toISOString(),
  platform: { platform: platform(), release: release(), architecture: arch(), node: process.version },
  versions: { turso: '0.8.3', sqlite_wasm: '3.54.1-build1' },
  source_wrapper_sha256: sha(originalCommon), repetitions, startup_only: startupOnly, graceful_close: gracefulClose,
  harness_sources: Object.fromEntries(await Promise.all(['run.mjs', 'worker.js', 'page.js', 'package.json', 'package-lock.json'].map(async path => [path, sha(await readFile(join(root, path)))]))),
  units: 'Memory figures are bytes unless field name says otherwise.',
  interpretation: 'Logical WASM memory and process residency are distinct. Process-family RSS sums shared pages repeatedly; PSS apportions them. These are Linux Chromium measurements, not iPhone results.',
  workload: { notes: 128, note_bytes: 8192, blob_bytes: [1048576, 8388608, 33554432], journal_requested: 'WAL', synchronous_requested: 'FULL', locking_requested: 'EXCLUSIVE', page_size_requested: 4096, cache_size_requested: -2000, gc_forced: false },
  builds: {}, trials: [],
};
let serving;
let context;
let activeDist;
let origin;
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function buildVariant(variant) {
  const destination = join(temporary, 'dist-' + variant);
  let patches = 0;
  await build({
    configFile: false, root, base: './', logLevel: 'error', worker: { format: 'es', plugins: () => [{
      name: 'explicit-disposable-turso-memory-experiment',
      transform(code, id) {
        if (variant !== 'turso-minimum' || !id.replaceAll('\\', '/').endsWith('/@tursodatabase/database-wasm-common/dist/index.js')) return;
        assert.equal(sha(code), sha(originalCommon));
        assert.equal(code.split('initial: 4000,').length, 2);
        patches++;
        return code.replace('initial: 4000,', 'initial: 1009,');
      },
    }] },
    build: { target: 'esnext', outDir: destination, emptyOutDir: true, reportCompressedSize: false },
    optimizeDeps: { exclude: ['@tursodatabase/database-wasm', '@sqlite.org/sqlite-wasm'] },
  });
  if (variant === 'turso-minimum') assert.ok(patches > 0);
  else assert.equal(patches, 0);
  const assets = [];
  for (const file of await readdir(join(destination, 'assets'))) {
    const contents = await readFile(join(destination, 'assets', file));
    assets.push({ file, bytes: contents.length, gzip_bytes: gzipSync(contents).length, sha256: sha(contents) });
  }
  report.builds[variant] = { wrapper_transform_applications: patches, patch: patches ? 'Disposable build transform only: wrapper initial:4000 -> initial:1009. Maximum65536/shared:true and engine WASM unchanged.' : null, assets };
  return destination;
}

async function startServer() {
  serving = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const path = resolve(activeDist, '.' + (pathname === '/' ? '/index.html' : pathname));
      assert.ok(path.startsWith(activeDist + sep));
      response.setHeader('Content-Type', { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm' }[extname(path)] ?? 'application/octet-stream');
      response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
      response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => serving.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${serving.address().port}`;
}

async function processes() {
  const list = [];
  for (const pid of (await readdir('/proc')).filter(value => /^\d+$/.test(value))) {
    try {
      const [status, cmdline] = await Promise.all([readFile(`/proc/${pid}/status`, 'utf8'), readFile(`/proc/${pid}/cmdline`, 'utf8')]);
      list.push({ pid: Number(pid), parent: Number(/^PPid:\s+(\d+)/m.exec(status)[1]), cmdline, status });
    } catch { /* Processes may exit between observations. */ }
  }
  return list;
}

async function processMemory(profile) {
  const list = await processes();
  const browser = list.find(value =>
    (value.cmdline.includes(`--user-data-dir=${profile}\0`) || value.cmdline.includes(`--user-data-dir=${profile} `))
      && !value.cmdline.includes('--type='));
  assert.ok(browser, 'Identify only this audit Chromium process tree');
  const members = new Set([browser.pid]);
  for (let changed = true; changed;) {
    changed = false;
    for (const value of list) if (members.has(value.parent) && !members.has(value.pid)) { members.add(value.pid); changed = true; }
  }
  const children = [];
  const total = { rss_bytes: 0, pss_bytes: 0, private_bytes: 0, virtual_bytes: 0 };
  for (const value of list.filter(value => members.has(value.pid))) {
    try {
      const smaps = await readFile(`/proc/${value.pid}/smaps_rollup`, 'utf8');
      const metric = key => Number(new RegExp(`^${key}:\\s+(\\d+)`, 'm').exec(smaps)?.[1] ?? 0) * 1024;
      const memory = {
        role: /--type=([^\0 ]+)/.exec(value.cmdline)?.[1] ?? 'browser',
        rss_bytes: metric('Rss'), pss_bytes: metric('Pss'),
        private_bytes: metric('Private_Clean') + metric('Private_Dirty'),
        virtual_bytes: Number(/^VmSize:\s+(\d+)/m.exec(value.status)?.[1] ?? 0) * 1024,
      };
      children.push(memory);
      for (const key of Object.keys(total)) total[key] += memory[key];
    } catch { /* Process ended during measurement. */ }
  }
  return { ...total, processes: children };
}

async function call(page, method, value) {
  return page.evaluate(async ({ method, value }) => Promise.race([
    window.auditCall(method, value),
    new Promise((_, reject) => setTimeout(() => reject(new Error(`Audit ${method} timed out`)), 90_000)),
  ]), { method, value });
}

async function trial(variant, repetition) {
  const profile = join(temporary, `profile-${variant}-${repetition}`);
  activeDist = join(temporary, 'dist-' + variant);
  const result = { variant, repetition, stages: [], passed: false };
  const pageErrors = [];
  try {
    context = await chromium.launchPersistentContext(profile, { headless: true });
    report.browser = context.browser().version();
    let page = context.pages()[0];
    page.on('pageerror', error => pageErrors.push(String(error)));
    const engine = variant.startsWith('turso') ? 'turso' : variant;
    await page.goto(origin + '/?engine=' + engine);
    await page.evaluate(() => window.auditReady);
    async function capture(stage, state) {
      await delay(startupOnly ? 2000 : 500);
      const memory = await processMemory(profile);
      assert.ok(memory.rss_bytes > 0 && memory.pss_bytes > 0 && memory.processes.length > 0, 'Residency accounting must be measurable.');
      result.stages.push({ stage, state: state ?? await call(page, 'state'), process_family: memory });
      console.log(JSON.stringify({ variant, repetition, stage, rss_mib: +(memory.rss_bytes / 1048576).toFixed(1), pss_mib: +(memory.pss_bytes / 1048576).toFixed(1) }));
    }
    await capture('opened');
    if (engine !== 'empty' && !startupOnly) {
      const initial = await call(page, 'state');
      assert.equal(initial.created_identity, true);
      const strict = await call(page, 'strict');
      assert.equal(strict.rejected, true);
      await capture('128-notes-written', await call(page, 'notes', { count: report.workload.notes, size: report.workload.note_bytes }));
      for (const size of report.workload.blob_bytes) {
        await capture(`blob-${size}-bytes-written`, await call(page, 'blob', size));
      }
      const saved = await call(page, 'state');
      assert.equal(saved.note_count, 129);
      if (gracefulClose) await call(page, 'close');
      await page.reload();
      await page.evaluate(() => window.auditReady);
      const reloaded = await call(page, 'state');
      assert.equal(reloaded.created_identity, false);
      assert.equal(reloaded.identity_fingerprint, initial.identity_fingerprint);
      assert.deepEqual(reloaded.sample, saved.sample);
      assert.deepEqual(reloaded.payload_sizes, saved.payload_sizes);
      await capture('page-reloaded', reloaded);
      if (gracefulClose) await call(page, 'close');
      await context.close();
      context = await chromium.launchPersistentContext(profile, { headless: true });
      page = context.pages()[0];
      await page.goto(origin + '/?engine=' + engine);
      await page.evaluate(() => window.auditReady);
      const restarted = await call(page, 'state');
      assert.equal(restarted.created_identity, false);
      assert.equal(restarted.identity_fingerprint, initial.identity_fingerprint);
      assert.deepEqual(restarted.sample, saved.sample);
      assert.deepEqual(restarted.payload_sizes, saved.payload_sizes);
      await capture('browser-restarted', restarted);
      result.strict_passed = result.reload_passed = result.browser_restart_passed = true;
    }
    assert.deepEqual(pageErrors, []);
    result.passed = true;
  } catch (error) {
    result.error = String(error);
    console.log(JSON.stringify({ variant, repetition, error: String(error) }));
  } finally {
    await context?.close(); context = undefined;
    await rm(profile, { recursive: true, force: true });
  }
  report.trials.push(result);
}

try {
  for (const variant of variants) await buildVariant(variant);
  await startServer();
  // Rotate order to reduce systematic warm-up/order effects.
  for (let repeat = 0; repeat < repetitions; repeat++) {
    const order = [...variants.slice(repeat % variants.length), ...variants.slice(0, repeat % variants.length)];
    for (const variant of order) await trial(variant, repeat + 1);
  }
  assert.equal(sha(await readFile(commonPath, 'utf8')), report.source_wrapper_sha256);
  report.source_wrapper_unchanged = true;
  report.passed = report.trials.every(value => value.passed);
} finally {
  await context?.close();
  if (serving) { serving.closeAllConnections(); await new Promise(resolve => serving.close(resolve)); }
  report.finished_at = new Date().toISOString();
  await mkdir(join(root, 'artifacts'), { recursive: true });
  const destination = join(root, 'artifacts', `measurements-${only ?? 'all'}-${repetitions}${startupOnly ? '-startup' : ''}${gracefulClose ? '-graceful' : ''}.json`);
  await writeFile(destination, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ report: destination, passed: report.passed }));
  await rm(temporary, { recursive: true, force: true });
}
if (!report.passed) process.exitCode = 1;
