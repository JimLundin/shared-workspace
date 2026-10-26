import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  MAX_LOCAL_DRAFT_BYTES, MAX_LOCAL_DRAFT_TOTAL_BYTES, MAX_LOCAL_DRAFTS, MAX_LOCAL_DRAFT_ROWS,
  decodeLocalDraft, decodeLocalDraftJournal, decodeLocalDraftReceipt, decodeLocalDraftRemoval,
  listLocalDrafts, prepareLocalDraft, prepareLocalDraftRemoval, restoreLocalDrafts,
} from '../src/local-drafts.ts';

const draft = (generation = 1, payload = '{"text":"Synthetic unfinished input"}', id = 'capture:workspace-a') => ({ id, kind: 'capture', payload, generation });
const empty = () => restoreLocalDrafts([]);
const retain = (state, value) => {
  const plan = prepareLocalDraft(state, value);
  assert.equal(plan.type, 'write');
  return plan.state;
};
const payloadBytes = size => JSON.stringify({ text: 'x'.repeat(size - Buffer.byteLength('{"text":""}')) });

test('local input decoding preserves exact pending identity and freezes its envelope', () => {
  const payload = '{ "pending": { "operation_id": "synthetic-command-42", "workspace_id": "workspace-a" }, "text": "Draft 🌲" }';
  const input = draft(7, payload), decoded = decodeLocalDraft(input);
  input.payload = '{}'; input.generation = 8;
  assert.equal(decoded.payload, payload, 'PENDING_COMMAND_BYTES_CHANGED');
  assert.equal(JSON.parse(decoded.payload).pending.operation_id, 'synthetic-command-42');
  assert.equal(decoded.generation, 7);
  assert.ok(Object.isFrozen(decoded));
  assert.throws(() => { decoded.generation = 8; }, TypeError);
});

test('local input boundary rejects invalid envelopes JSON documents and imprecise generations', () => {
  for (const value of [
    null, {}, { ...draft(), extra: true }, { ...draft(), kind: 'credential' }, { ...draft(), kind: 'audio' },
    { ...draft(), id: '../other' }, { ...draft(), id: '' }, { ...draft(), id: 'x'.repeat(129) },
    ...[-1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, '1'].map(generation => ({ ...draft(), generation })),
    ...['null', '[]', 'true', '1', '"text"', '{not json}', '{"text":"\ud800"}'].map(payload => ({ ...draft(), payload })),
    { ...draft(), payload: { text: 'not a serialized document' } },
  ]) assert.throws(() => decodeLocalDraft(value));
  assert.throws(() => decodeLocalDraftRemoval({ id: 'capture', generation: 1, payload: '{}' }));
  assert.throws(() => decodeLocalDraft(draft(1, 'PRIVATE_SYNTHETIC_INVALID_JSON')), error => !error.message.includes('PRIVATE_SYNTHETIC'));
});

test('UTF-8 payload bytes are bounded before storage without truncating input', () => {
  const exact = payloadBytes(MAX_LOCAL_DRAFT_BYTES);
  assert.equal(decodeLocalDraft(draft(1, exact)).payload, exact);
  assert.throws(() => decodeLocalDraft(draft(1, payloadBytes(MAX_LOCAL_DRAFT_BYTES + 1))), undefined, 'OVERSIZED_INPUT_RETAINED');
  const multiByte = JSON.stringify({ text: 'é'.repeat(MAX_LOCAL_DRAFT_BYTES / 2) });
  assert.ok(multiByte.length < MAX_LOCAL_DRAFT_BYTES);
  assert.throws(() => decodeLocalDraft(draft(1, multiByte)), undefined, 'UTF8_BYTE_LIMIT_IGNORED');
});

test('exact retention retries are idempotent while an equal generation cannot change content', () => {
  const first = draft(1), state = retain(empty(), first);
  assert.deepEqual(prepareLocalDraft(state, first), { type: 'unchanged', receipt: { type: 'retained', id: first.id, generation: 1, replayed: true } });
  assert.throws(() => prepareLocalDraft(state, { ...first, payload: '{"text":"different"}' }), undefined, 'GENERATION_REUSED_FOR_OTHER_CONTENT');
  assert.throws(() => prepareLocalDraft(state, { ...first, kind: 'editor' }));
  assert.throws(() => prepareLocalDraftRemoval(state, { id: first.id, generation: 1 }));
  assert.deepEqual(listLocalDrafts(state).drafts, [first]);
});

test('a later retained generation makes delayed writes and removals stale', () => {
  const initial = retain(empty(), draft(1)), latest = draft(3, '{"text":"Newest input"}');
  const state = retain(initial, latest);
  for (const plan of [prepareLocalDraft(state, draft(2)), prepareLocalDraftRemoval(state, { id: latest.id, generation: 2 })]) {
    assert.deepEqual(plan, { type: 'unchanged', receipt: { type: 'stale', id: latest.id, generation: 3 } }, 'STALE_WRITE_ACCEPTED');
  }
  assert.deepEqual(listLocalDrafts(state).drafts, [latest]);
  assert.deepEqual(listLocalDrafts(initial).drafts, [draft(1)], 'PREVIOUS_STATE_MUTATED');
});

test('a persisted tombstone fences old input and can be deliberately reused with a higher generation', () => {
  const removed = restoreLocalDrafts([{ id: 'capture:workspace-a', generation: 9, kind: null, payload: null }]);
  assert.deepEqual(listLocalDrafts(removed), { drafts: [], removed: [{ id: 'capture:workspace-a', generation: 9 }] });
  const stale = prepareLocalDraft(removed, draft(8));
  assert.equal(stale.receipt.type, 'stale', 'DELETED_INPUT_RESURRECTED');
  assert.throws(() => prepareLocalDraft(removed, draft(9)), undefined, 'DELETION_GENERATION_REUSED');
  assert.deepEqual(prepareLocalDraftRemoval(removed, { id: 'capture:workspace-a', generation: 9 }).receipt,
    { type: 'removed', id: 'capture:workspace-a', generation: 9, replayed: true });
  const next = retain(removed, draft(10));
  assert.deepEqual(listLocalDrafts(next), { drafts: [draft(10)], removed: [] });
  assert.equal(prepareLocalDraftRemoval(next, { id: 'capture:workspace-a', generation: 9 }).receipt.type, 'stale');
});

test('removal of an absent slot records its fence before a delayed first retention', () => {
  const removal = prepareLocalDraftRemoval(empty(), { id: 'capture:workspace-a', generation: 2 });
  assert.equal(removal.type, 'write');
  assert.equal(prepareLocalDraft(removal.state, draft(1)).receipt.type, 'stale', 'ABSENT_SLOT_DELETION_LOST');
  assert.equal(removal.state.active, 0);
  assert.equal(removal.state.bytes, 0);
});

test('the active count cap preserves old drafts and deletion releases capacity', () => {
  const rows = Array.from({ length: MAX_LOCAL_DRAFTS }, (_, index) => draft(1, '{}', `slot-${index}`));
  const full = restoreLocalDrafts(rows);
  assert.throws(() => prepareLocalDraft(full, draft(1, '{}', 'one-too-many')), undefined, 'ACTIVE_LIMIT_IGNORED');
  const removed = prepareLocalDraftRemoval(full, { id: 'slot-0', generation: 2 });
  assert.equal(removed.type, 'write');
  const replaced = retain(removed.state, draft(1, '{}', 'replacement'));
  assert.equal(listLocalDrafts(replaced).drafts.length, MAX_LOCAL_DRAFTS);
  assert.equal(listLocalDrafts(full).drafts.length, MAX_LOCAL_DRAFTS, 'FAILED_RETENTION_EVICTED_INPUT');
  assert.deepEqual(listLocalDrafts(replaced).removed, [{ id: 'slot-0', generation: 2 }]);
});

test('the aggregate payload cap accounts for replacement and deletion', () => {
  const large = payloadBytes(MAX_LOCAL_DRAFT_BYTES);
  const count = MAX_LOCAL_DRAFT_TOTAL_BYTES / MAX_LOCAL_DRAFT_BYTES;
  const full = restoreLocalDrafts(Array.from({ length: count }, (_, index) => draft(1, large, `slot-${index}`)));
  assert.equal(full.bytes, MAX_LOCAL_DRAFT_TOTAL_BYTES);
  assert.throws(() => prepareLocalDraft(full, draft(1, '{}', 'excess')), undefined, 'AGGREGATE_LIMIT_IGNORED');
  const smaller = retain(full, draft(2, '{}', 'slot-0'));
  assert.equal(smaller.bytes, MAX_LOCAL_DRAFT_TOTAL_BYTES - MAX_LOCAL_DRAFT_BYTES + 2);
  const removed = prepareLocalDraftRemoval(smaller, { id: 'slot-1', generation: 2 });
  assert.equal(removed.type, 'write');
  assert.equal(removed.state.bytes, MAX_LOCAL_DRAFT_TOTAL_BYTES - 2 * MAX_LOCAL_DRAFT_BYTES + 2);
  assert.equal(full.bytes, MAX_LOCAL_DRAFT_TOTAL_BYTES);
});

test('bounded tombstone history is never pruned to admit an unsafe new identity', () => {
  const full = restoreLocalDrafts(Array.from({ length: MAX_LOCAL_DRAFT_ROWS }, (_, index) => ({ id: `slot-${index}`, generation: 2, kind: null, payload: null })));
  assert.throws(() => prepareLocalDraft(full, draft(1, '{}', 'new-slot')), undefined, 'HISTORY_LIMIT_IGNORED');
  assert.throws(() => prepareLocalDraftRemoval(full, { id: 'new-slot', generation: 1 }));
  const reused = retain(full, draft(3, '{}', 'slot-0'));
  assert.equal(reused.records.length, MAX_LOCAL_DRAFT_ROWS);
  assert.equal(prepareLocalDraft(reused, draft(1, '{}', 'slot-1')).receipt.type, 'stale', 'TOMBSTONE_PRUNED');
});

test('persisted rows reject ambiguity and corruption before exposing recoverable input', () => {
  const row = draft();
  for (const rows of [
    [row, row], [{ ...row, generation: Number.MAX_SAFE_INTEGER + 1 }],
    [{ ...row, kind: null }], [{ ...row, payload: null }], [{ ...row, payload: '[]' }],
    [{ ...row, extra: true }], Array.from({ length: MAX_LOCAL_DRAFT_ROWS + 1 }, (_, index) => ({ id: `slot-${index}`, generation: 1, kind: null, payload: null })),
  ]) assert.throws(() => restoreLocalDrafts(rows));
});

test('journal snapshots are immutable and keep deleted slots out of recoverable drafts', () => {
  const state = restoreLocalDrafts([draft(), { id: 'editor', generation: 4, kind: null, payload: null }]);
  const journal = listLocalDrafts(state);
  assert.ok(Object.isFrozen(journal) && Object.isFrozen(journal.drafts) && Object.isFrozen(journal.removed));
  assert.ok(Object.isFrozen(journal.drafts[0]) && Object.isFrozen(journal.removed[0]));
  assert.deepEqual(journal.removed, [{ id: 'editor', generation: 4 }]);
  assert.equal(journal.drafts.length, 1);
  assert.throws(() => journal.drafts.push(draft()), TypeError);
  assert.throws(() => { journal.removed[0].generation = 0; }, TypeError);
});

test('worker reply decoders reject invalid receipts and overlapping active and removed identities', () => {
  const journal = { drafts: [draft()], removed: [{ id: 'editor', generation: 7 }] };
  assert.deepEqual(decodeLocalDraftJournal(journal), journal);
  assert.throws(() => decodeLocalDraftJournal({ ...journal, removed: [{ id: draft().id, generation: 8 }] }));
  assert.throws(() => decodeLocalDraftJournal({ ...journal, remote: true }));
  for (const receipt of [
    { type: 'retained', id: 'editor', generation: 3, replayed: false },
    { type: 'removed', id: 'editor', generation: 4, replayed: true },
    { type: 'stale', id: 'editor', generation: 4 },
  ]) assert.deepEqual(decodeLocalDraftReceipt(receipt), receipt);
  for (const receipt of [
    { type: 'executed', id: 'editor', generation: 3, replayed: false },
    { type: 'retained', id: 'editor', generation: 3, replayed: 'false' },
    { type: 'stale', id: 'editor', generation: 4, replayed: false },
    { type: 'removed', id: 'editor', generation: -1, replayed: true },
  ]) assert.throws(() => decodeLocalDraftReceipt(receipt));
});
