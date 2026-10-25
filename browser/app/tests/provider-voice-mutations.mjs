import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const source = await readFile(new URL('../src/provider-voice.ts', import.meta.url), 'utf8');
const tests = await readFile(new URL('../src/provider-voice.test.ts', import.meta.url), 'utf8');
const mutations = [
  ['share-mutable-context', 'context = Object.freeze({ workspaceId: snapshot.workspaceId, connectionVersion: snapshot.connectionVersion });', 'context = snapshot;'],
  ['accept-late-transcription', 'if (this.#active !== active) return;\n      if (typeof text', 'if (typeof text'],
  ['leak-late-microphone', 'if (this.#active !== active) { capture.release(); return; }', 'if (this.#active !== active) { return; }'],
  ['ignore-audio-size', 'active.bytes + chunk.size > PROVIDER_VOICE_LIMITS.audioBytes', 'false'],
  ['omit-provider-abort', 'active.abort.abort();', ''],
  ['accept-empty-transcript', '!text.trim() || text.length', 'text.length'],
  ['upload-before-final-chunk', 'try { capture.stop(); }\n    catch', 'try { capture.stop(); this.#recordingFinished(active); }\n    catch'],
  ['force-language-by-default', "this.#locale = options.locale ?? '';", "this.#locale = options.locale ?? 'en-US';"],
];
const directory = await mkdtemp(join(tmpdir(), 'provider-voice-mutations-'));
const outcomes = [];
function run() {
  return spawnSync(process.execPath, ['--experimental-strip-types', '--test', join(directory, 'provider-voice.test.ts')],
    { encoding: 'utf8', timeout: 30_000 });
}
try {
  await writeFile(join(directory, 'provider-voice.test.ts'), tests);
  await writeFile(join(directory, 'provider-voice.ts'), source);
  const baseline = run();
  assert.equal(baseline.status, 0, baseline.stdout + baseline.stderr);
  for (const [name, before, after] of mutations) {
    assert.equal(source.split(before).length, 2, `Mutation ${name} must match exactly once`);
    await writeFile(join(directory, 'provider-voice.ts'), source.replace(before, after));
    const result = run();
    const killed = result.status !== 0 && /AssertionError|ERR_ASSERTION/.test(result.stdout + result.stderr);
    outcomes.push({ mutation: name, killed });
    assert.equal(killed, true, `${name}: ${result.stdout}${result.stderr}`);
  }
  const report = { verified_at: new Date().toISOString(), baseline_passed: true, mutations: outcomes };
  await writeFile('/tmp/provider-voice-mutation-evidence.json', JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally { await rm(directory, { recursive: true, force: true }); }
