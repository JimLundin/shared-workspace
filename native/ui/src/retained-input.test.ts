import test from 'node:test';
import assert from 'node:assert/strict';
import { RetainedInputJournal } from './retained-input.ts';
import type { LocalDraftPort } from './retained-input.ts';
import type { LocalDraft, LocalDraftJournal, LocalDraftReceipt, LocalDraftRemoval } from '../../../browser/host/src/local-drafts.ts';

function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void; const promise = new Promise<T>((done, failed) => { resolve = done; reject = failed; }); return { promise, resolve, reject }; }
class DraftFixture implements LocalDraftPort {
  drafts = new Map<string, LocalDraft>(); removed = new Map<string, LocalDraftRemoval>(); writes: LocalDraft[] = []; removals: LocalDraftRemoval[] = [];
  write: (draft: LocalDraft) => Promise<LocalDraftReceipt> = async draft => { this.drafts.set(draft.id, draft); this.removed.delete(draft.id); return { type: 'retained', id: draft.id, generation: draft.generation, replayed: false }; };
  removal: (draft: LocalDraftRemoval) => Promise<LocalDraftReceipt> = async draft => { this.drafts.delete(draft.id); this.removed.set(draft.id, draft); return { type: 'removed', id: draft.id, generation: draft.generation, replayed: false }; };
  async listLocalDrafts(): Promise<LocalDraftJournal> { return { drafts: [...this.drafts.values()], removed: [...this.removed.values()] }; }
  async upsertLocalDraft(draft: LocalDraft): Promise<LocalDraftReceipt> { this.writes.push(draft); return this.write(draft); }
  async removeLocalDraft(draft: LocalDraftRemoval): Promise<LocalDraftReceipt> { this.removals.push(draft); return this.removal(draft); }
}

test('retention caches input immediately but its barrier waits for a correlated durable receipt', async () => {
  const port = new DraftFixture(), journal = await RetainedInputJournal.open(port), saved = deferred<LocalDraftReceipt>();
  port.write = async () => saved.promise;
  let completed = false;
  const pending = journal.put('editor', 'editor', { text: 'Unsaved work' }).then(() => { completed = true; });
  assert.equal(journal.pending, true, 'PENDING_RETENTION_NOT_VISIBLE');
  assert.equal(journal.read('editor', 'editor'), '{"text":"Unsaved work"}');
  await Promise.resolve(); assert.equal(completed, false, 'LOCAL_RETENTION_BARRIER_SKIPPED');
  saved.resolve({ type: 'retained', id: 'editor', generation: 1, replayed: false }); await pending;
  assert.equal(completed, true); assert.equal(journal.failed, false); assert.equal(journal.pending, false);
});

test('writes, removal and replacement are serialized with increasing generations across reopen', async () => {
  const port = new DraftFixture(), journal = await RetainedInputJournal.open(port);
  const first = journal.put('capture', 'capture', { text: 'First' });
  const second = journal.put('capture', 'capture', { text: 'Second' });
  const removed = journal.remove('capture');
  const third = journal.put('capture', 'capture', { text: 'Third' });
  await Promise.all([first, second, removed, third]); await journal.flush();
  assert.deepEqual(port.writes.map(item => item.generation), [1, 2, 4], 'LOCAL_GENERATION_REUSED');
  assert.equal(port.removals[0]?.generation, 3);
  assert.equal(port.drafts.get('capture')?.payload, '{"text":"Third"}', 'OLDER_INPUT_OVERWROTE_NEWER');
  const reopened = await RetainedInputJournal.open(port); await reopened.remove('capture');
  assert.equal(port.removals[1]?.generation, 5);
  const afterRemoval = await RetainedInputJournal.open(port); await afterRemoval.put('capture', 'capture', { text: 'Fourth' });
  assert.equal(port.writes.at(-1)?.generation, 6, 'REMOVAL_FENCE_FORGOTTEN');
});

test('stale or mismatched retention receipts reject the durability barrier', async () => {
  for (const receipt of [
    { type: 'stale' as const, id: 'editor', generation: 2 },
    { type: 'retained' as const, id: 'other', generation: 1, replayed: false },
    { type: 'retained' as const, id: 'editor', generation: 2, replayed: false },
  ]) {
    const port = new DraftFixture(), journal = await RetainedInputJournal.open(port); port.write = async () => receipt;
    await assert.rejects(journal.put('editor', 'editor', { text: 'Keep this input' }), Error, 'UNRELATED_RETENTION_RECEIPT_ACCEPTED');
    assert.equal(journal.failed, true); assert.ok(journal.read('editor', 'editor'));
    await assert.rejects(journal.flush());
  }
});

test('success in another slot cannot erase a failed input or deduplicate its retry', async () => {
  const port = new DraftFixture(), journal = await RetainedInputJournal.open(port), write = port.write;
  port.write = async draft => { if (draft.id === 'editor') throw new Error('Storage interrupted'); return write(draft); };
  await assert.rejects(journal.put('editor', 'editor', { text: 'Must survive' }));
  await journal.put('capture', 'capture', { text: 'Unrelated input' });
  assert.equal(journal.failed, true, 'UNRELATED_SAVE_CLEARED_RETENTION_FAILURE');
  port.write = write;
  await journal.put('editor', 'editor', { text: 'Must survive' });
  assert.equal(port.drafts.get('editor')?.payload, '{"text":"Must survive"}', 'FAILED_INPUT_RETRY_DEDUPLICATED');
  assert.equal(journal.failed, false);
});

test('an identical pending write shares its durability barrier without sending duplicate bytes', async () => {
  const port = new DraftFixture(), journal = await RetainedInputJournal.open(port), pending = deferred<LocalDraftReceipt>();
  port.write = async () => pending.promise;
  const first = journal.put('meeting', 'meeting', { command: 'captured' }), second = journal.put('meeting', 'meeting', { command: 'captured' });
  pending.resolve({ type: 'retained', id: 'meeting', generation: 1, replayed: false }); await Promise.all([first, second]);
  assert.equal(port.writes.length, 1, 'IDENTICAL_PENDING_INPUT_DUPLICATED');
  assert.throws(() => journal.read('meeting', 'editor'));
});

test('failed discard remains visible after an unrelated save and can be retried', async () => {
  const port = new DraftFixture(), journal = await RetainedInputJournal.open(port), removal = port.removal;
  await journal.put('editor', 'editor', { text: 'Recoverable' });
  port.removal = async () => { throw new Error('Removal unavailable'); };
  await assert.rejects(journal.remove('editor'));
  await journal.put('capture', 'capture', { text: 'Other input' });
  assert.equal(journal.failed, true, 'FAILED_DISCARD_HIDDEN_BY_UNRELATED_SAVE');
  port.removal = removal; await journal.remove('editor');
  assert.equal(journal.failed, false); assert.equal(port.drafts.has('editor'), false);
});
