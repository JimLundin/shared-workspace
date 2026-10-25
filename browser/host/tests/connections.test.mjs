import assert from 'node:assert/strict';
import { test } from 'node:test';
import { activeConnection, connectionRecords, connectionState, mergeConnections, prepareConnection, setConnection, removeConnection, signedConnection, decodeConnectionState, publicConnection } from '../src/connections.ts';
const id = value => value.toString(16).padStart(64, '0');
const synthetic = 'SYNTHETIC_ONLY_CONNECTION_CREDENTIAL';
const action = { type: 'set', api_key: synthetic, model: 'gpt-4o-transcribe' };
const input = (operation = 1, parents = [], workspace = 'work') => ({ operation_id: id(operation), expected_versions: parents, workspace_id: workspace });
const revision = (operation, parents = [], next = action, workspace = 'work', author = id(99)) => ({ record: { version: 1, id: id(operation), workspace_id: workspace, author, parents, action: next }, signature: 'ef'.repeat(64) });

test('offline connection edits converge to an explicit conflict in either arrival order', () => {
  const base = revision(1), a = revision(2, [id(1)]), b = revision(3, [id(1)], { ...action, api_key: synthetic + '_B' });
  const left = mergeConnections([base, a], [b]), right = mergeConnections([base, b], [a]);
  assert.deepEqual(left, right);
  assert.deepEqual(connectionState(left, 'work'), { workspace_id: 'work', provider: 'openai', status: 'conflict', versions: [id(2), id(3)] });
  assert.throws(() => activeConnection(left, 'work', id(2)), undefined, 'CONFLICT_USED_FOR_UPLOAD');
  const resolved = prepareConnection(left, id(99), input(4, [id(3), id(2)]), action);
  assert.deepEqual(resolved.record.parents, [id(2), id(3)]);
  const next = mergeConnections(left, [{ record: resolved.record, signature: 'ef'.repeat(64) }]);
  assert.equal(activeConnection(next, 'work', id(4)).api_key, synthetic);
});
test('stale expected versions cannot overwrite a newer connection', () => {
  assert.throws(() => prepareConnection([revision(1), revision(2, [id(1)])], id(99), input(3, [id(1)]), action), undefined, 'STALE_CONNECTION_OVERWRITE');
});
test('exact retry survives later edits but changed identity input rejects', () => {
  const retained = [revision(1), revision(2, [id(1)])];
  assert.equal(prepareConnection(retained, id(99), input(1), action).replayed, true);
  assert.throws(() => prepareConnection(retained, id(99), input(1), { ...action, api_key: synthetic + '_CHANGED' }), undefined, 'CHANGED_RETRY_ACCEPTED');
  assert.throws(() => prepareConnection(retained, id(98), input(1), action));
});
test('removal remains authoritative after replaying all older history', () => {
  const old = revision(1), removed = revision(2, [id(1)], { type: 'remove' });
  const merged = mergeConnections([old, removed], [old]);
  assert.equal(connectionState(merged, 'work').status, 'removed', 'REMOVED_CREDENTIAL_RESURRECTED');
  assert.throws(() => activeConnection(merged, 'work', id(1)));
  const concurrent = mergeConnections(merged, [revision(3, [id(1)])]);
  assert.equal(connectionState(concurrent, 'work').status, 'conflict');
  assert.throws(() => activeConnection(concurrent, 'work', id(3)));
});
test('upload resolves only the selected workspace and exact active version', () => {
  const retained = [revision(1), revision(2, [], action, 'private')];
  assert.throws(() => activeConnection(retained, 'work', id(2)), undefined, 'WRONG_VERSION_USED');
  assert.throws(() => activeConnection(retained, 'absent', id(1)));
  assert.equal(activeConnection(retained, 'private', id(2)).api_key, synthetic);
});
test('unselected connection history is rejected before import', () => {
  assert.throws(() => connectionRecords([revision(1, [], action, 'private')], ['work']), undefined, 'UNSELECTED_SECRET_IMPORTED');
});
test('a connection cannot borrow a parent from another workspace', () => {
  assert.throws(() => mergeConnections([], [revision(1), revision(2, [id(1)], action, 'private')]), undefined, 'CROSS_WORKSPACE_PARENT_ACCEPTED');
});
test('missing parents cycles and conflicting signed identities reject atomically', () => {
  const retained = [revision(1)];
  assert.throws(() => mergeConnections(retained, [revision(2, [id(3)])]));
  assert.throws(() => mergeConnections(retained, [revision(2, [id(3)]), revision(3, [id(2)])]));
  assert.throws(() => mergeConnections(retained, [revision(1, [], { type: 'remove' })]), undefined, 'SIGNED_IDENTITY_COLLISION_ACCEPTED');
  assert.deepEqual(retained, [revision(1)]);
});
test('public metadata never includes key material or encrypted record details', () => {
  const state = connectionState([revision(1)], 'work');
  assert.deepEqual(Object.keys(state).sort(), ['model', 'provider', 'status', 'version', 'workspace_id']);
  assert.ok(!JSON.stringify(state).includes(synthetic));
  assert.throws(() => decodeConnectionState({ ...state, api_key: synthetic }));
});
test('connection boundary refuses extra URL fields malformed credentials and model changes', () => {
  const valid = { ...input(), api_key: synthetic, model: 'gpt-4o-transcribe' };
  assert.deepEqual(setConnection(valid), valid);
  assert.deepEqual(removeConnection(input()), input());
  assert.throws(() => setConnection({ ...valid, endpoint: 'https://untrusted.invalid/' }));
  assert.throws(() => setConnection({ ...valid, api_key: synthetic + '\n' }));
  assert.throws(() => setConnection({ ...valid, model: 'unrecognized' }));
  assert.throws(() => signedConnection({ ...revision(1), record: { ...revision(1).record, parents: [id(2), id(2)] } }));
});

test('retained public revision metadata holds no plaintext credential cache', () => {
  const retained = publicConnection(revision(1));
  assert.deepEqual(retained.record.action, { type: 'set', model: 'gpt-4o-transcribe' }, 'CREDENTIAL_CACHED_IN_PUBLIC_REVISION');
  assert.ok(!JSON.stringify(retained).includes(synthetic));
  assert.deepEqual(connectionState([retained], 'work'), connectionState([revision(1)], 'work'));
});
