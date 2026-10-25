import assert from 'node:assert/strict';
import { test } from 'node:test';
import { verifyIdentityStorage } from '../src/identity-storage.ts';
const seed = Uint8Array.from({ length: 32 }, (_, index) => index + 7);
const safe = new Uint8Array(1024).fill(255);
const notFound = () => new DOMException('Synthetic missing file', 'NotFoundError');
const file = bytes => new File([bytes], 'synthetic-file');
const directory = files => async () => ({ async getFileHandle(name) {
  if (!files.has(name)) throw notFound();
  return { async getFile() { return files.get(name); } };
} });
const verify = (files, checkpoint = async () => {}) => verifyIdentityStorage('work.db', seed, checkpoint, directory(files));

test('checkpoint completes before inspecting current physical identity files', async () => {
  const files = new Map([['work.db', file(safe)], ['work.db-wal', file(seed)]]);
  await assert.doesNotReject(verify(files, async () => { files.set('work.db-wal', file(new Uint8Array())); }), 'CHECKPOINT_NOT_RUN');
});

test('residual plaintext seed in database or WAL blocks startup even across stream chunks', async () => {
  for (const name of ['work.db', 'work.db-wal']) {
    const chunks = [safe, seed.slice(0, 1), seed.slice(1, 16), seed.slice(16), safe];
    const streamFile = { size: 2080, stream() { return new ReadableStream({ start(controller) { chunks.forEach(chunk => controller.enqueue(chunk)); controller.close(); } }); } };
    const files = new Map([['work.db', file(safe)], ['work.db-wal', file(safe)], [name, streamFile]]);
    await assert.rejects(verify(files), undefined, name === 'work.db' ? 'DATABASE_RESIDUE_ALLOWED' : 'WAL_RESIDUE_ALLOWED');
  }
});

test('textual seed representation is also excluded from current physical files', async () => {
  const hex = Buffer.from(seed).toString('hex');
  await assert.rejects(verify(new Map([['work.db', file(safe)], ['work.db-wal', new File([hex], 'synthetic-wal')]])), undefined, 'HEX_SEED_RESIDUE_ALLOWED');
});

test('missing or empty main database fails closed while absent WAL is allowed', async () => {
  await assert.rejects(verify(new Map()));
  await assert.rejects(verify(new Map([['work.db', file(new Uint8Array())]])));
  await verify(new Map([['work.db', file(safe)]]));
});

test('file and checkpoint failures remain fixed safe errors without fallback or new files', async () => {
  const message = 'Device identity protection could not be verified. Close and reopen this tab before sharing connections.';
  await assert.rejects(verify(new Map([['work.db', file(safe)]]), async () => { throw new Error('SYNTHETIC_PRIVATE_STORAGE_DETAIL'); }), { message });
  const names = [];
  await assert.rejects(verifyIdentityStorage('work.db', seed, async () => {}, async () => ({ async getFileHandle(...args) {
    names.push(args); throw new DOMException('SYNTHETIC_PRIVATE_STORAGE_DETAIL', 'NotAllowedError');
  } })), { message });
  assert.deepEqual(names, [['work.db']]);
});
