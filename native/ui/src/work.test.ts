import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeSnapshot, isWorkRevision } from './protocol.ts';
import type { MeetingAction, MeetingWorkState, Snapshot, SourceReference } from './protocol.ts';
import { decodeMeetingRequest, decodePreparedMeeting } from './meeting-preparation.ts';
import { decodeRetainedCommand } from './retained-commands.ts';
import { bindPreparation, correctedAction, currentWork, preparationProblem, preparedMeetingFits, publication, sourceText, workInWorkspace } from './work.ts';

const device = 'aa'.repeat(32), peer = 'bb'.repeat(32);
const source: SourceReference = { workspace_id: 'work', resource_id: 'transcript', revision_id: 'original' };
const quote = 'Alex will send the budget by next Friday.';
const citation = { source, quote }, basis = { type: 'transcript' as const, citations: [citation] };
const prepared = () => decodePreparedMeeting({
  notes: { title: 'Meeting notes', body: 'A revised budget was requested.', citations: [citation] },
  actions: [{ text: 'Send the budget.', owner: { type: 'assigned', name: 'Alex', basis }, deadline: { type: 'stated', text: 'next Friday', basis }, basis }],
  follow_up: { title: 'Follow-up text', body: 'Alex will send the budget by next Friday.', citations: [citation] },
});
function snapshot(state: MeetingWorkState = { type: 'preparing', run: { id: 'run', originating_device: device, started_at_ms: 100 } }): Snapshot {
  return decodeSnapshot({ device_id: device, device_name: 'This host', operation_count: 3, network: { type: 'active' }, peers: [],
    workspaces: [{ id: 'work', title: 'Work', heads: ['begin'], members: [device], placements: [{ resource_id: 'meeting', source_workspace_id: 'work' }] }],
    resources: [
      { id: 'transcript', workspace_id: 'work', heads: [{ id: 'latest', author: device, title: 'Transcript', body: 'A revised transcript without the original quote.', parents: ['original'] }], history: [{ id: 'original', author: device, title: 'Transcript', body: quote, parents: [] }] },
      { id: 'meeting', workspace_id: 'work', heads: [{ id: 'begin', author: device, title: 'Budget review', parents: [], work: { type: 'meeting', outcome: 'Prepare the meeting.', sources: [source], state } }], history: [] },
    ],
  });
}
function selection(value = snapshot()) {
  const selected = currentWork(value, 'meeting'); assert.equal(selected.type, 'ready');
  if (selected.type !== 'ready') throw new Error('Fixture has no current work.');
  return selected.current;
}

test('source lookup preserves exact workspace, resource and historical revision identity', () => {
  const value = snapshot();
  assert.equal(sourceText(value, source), quote, 'PINNED_SOURCE_REPLACED_BY_CURRENT_HEAD');
  assert.equal(sourceText(value, { ...source, workspace_id: 'other' }), null, 'FOREIGN_SOURCE_ACCEPTED');
  assert.equal(sourceText(value, { ...source, resource_id: 'meeting', revision_id: 'begin' }), null, 'WORK_USED_AS_TRANSCRIPT');
  assert.equal(sourceText(value, { ...source, revision_id: 'missing' }), null);
});

test('selection requires one available Work head and local placement enumeration preserves origins', () => {
  const value = snapshot();
  assert.equal(currentWork(value, 'transcript').type, 'unavailable');
  assert.equal(currentWork(value, 'missing').type, 'unavailable');
  const resource = selection(value).resource;
  assert.equal(currentWork({ ...value, resources: [{ ...resource, heads: [...resource.heads, ...resource.heads] }] }, 'meeting').type, 'unavailable', 'CONFLICTED_WORK_PREPARED');
  assert.equal(currentWork({ ...value, workspaces: [] }, 'meeting').type, 'unavailable', 'UNAVAILABLE_WORKSPACE_PREPARED');
  assert.deepEqual(workInWorkspace(value, 'work').map(item => item.id), ['meeting']);
  const workspace = value.workspaces[0]!;
  assert.equal(workInWorkspace({ ...value, workspaces: [{ ...workspace, placements: [{ resource_id: 'meeting', source_workspace_id: 'other' }] }] }, 'work').length, 0);
});

test('preparation requires pinned transcripts and resume belongs to the originating device', () => {
  const value = snapshot();
  assert.equal(preparationProblem(value, selection(value), true), null);
  assert.ok(preparationProblem({ ...value, device_id: peer }, selection(value), true), 'PEER_PREPARATION_TAKEN_OVER');
  assert.ok(preparationProblem(value, selection(value)), 'PREPARING_RUN_RESTARTED');
  const requested = snapshot({ type: 'requested' });
  assert.equal(preparationProblem(requested, selection(requested)), null);
  assert.ok(preparationProblem(requested, selection(requested), true), 'REQUESTED_WORK_RESUMED');
  const current = selection(requested);
  assert.ok(preparationProblem(requested, { ...current, revision: { ...current.revision, work: { ...current.revision.work, sources: [] } } }));
  assert.ok(preparationProblem(requested, { ...current, revision: { ...current.revision, work: { ...current.revision.work, sources: [{ ...source, workspace_id: 'other' }] } } }), 'CROSS_WORKSPACE_PREPARATION_ALLOWED');
});

test('publication pins captured Work heads and run while allocating stable distinct material identities', () => {
  const value = snapshot(), binding = bindPreparation(selection(value)); assert.ok(binding);
  const first = publication(binding, prepared()), retry = publication(binding, prepared());
  assert.deepEqual(first, retry, 'PUBLICATION_RETRY_REALLOCATED_IDENTITIES');
  assert.equal(first.workspace_id, 'work'); assert.equal(first.resource_id, 'meeting');
  assert.deepEqual(first.expected_heads, ['begin'], 'PUBLICATION_ADOPTED_UNREVIEWED_HEADS');
  assert.equal(first.run_id, 'run');
  assert.equal(new Set([first.operation_id, first.notes.resource_id, first.follow_up.resource_id, ...first.actions.map(item => item.id)]).size, 4);
  assert.ok(Object.isFrozen(first) && Object.isFrozen(first.actions));
  assert.equal(bindPreparation(selection(snapshot({ type: 'requested' }))), null);
});

test('preparation reports empty or oversized sources before beginning a provider run', () => {
  const value = snapshot({ type: 'requested' }), current = selection(value);
  const transcript = value.resources.find(resource => resource.id === 'transcript')!;
  const empty = { ...value, resources: value.resources.map(resource => resource.id === transcript.id ?
    { ...transcript, history: transcript.history.map(revision => 'body' in revision ? { ...revision, body: '   ' } : revision) } : resource) };
  assert.match(preparationProblem(empty, current)!, /empty/, 'EMPTY_SOURCE_SENT_TO_PROVIDER');
  const sources = Array.from({ length: 5 }, (_, index) => ({ workspace_id: 'work', resource_id: 'part-' + index, revision_id: 'part-revision-' + index }));
  const resources = sources.map(source => ({ id: source.resource_id, workspace_id: source.workspace_id, heads: [],
    history: [{ id: source.revision_id, author: device, title: 'Transcript part', parents: [], body: 'x'.repeat(128 * 1024) }] }));
  const largeCurrent = { ...current, revision: { ...current.revision, work: { ...current.revision.work, sources } } };
  const oversized = { ...value, resources: [...value.resources, ...resources] };
  assert.match(preparationProblem(oversized, largeCurrent)!, /512 KiB/, 'OVERSIZED_SOURCES_MISREPORTED_AS_CONNECTION');
  assert.equal(preparationProblem(oversized, { ...largeCurrent, revision: { ...largeCurrent.revision, work: { ...largeCurrent.revision.work, sources: sources.slice(0, 4) } } }), null);
});

test('prepared provenance rejects invented quotes, foreign revisions and unsupported owner or deadline wording', () => {
  const value = snapshot(), binding = bindPreparation(selection(value)); assert.ok(binding);
  assert.equal(preparedMeetingFits(value, binding, prepared()), true);
  const result = prepared(), action = result.actions[0]!;
  const invalid = [
    { ...result, notes: { ...result.notes, citations: [{ source, quote: 'Nobody said this.' }] } },
    { ...result, notes: { ...result.notes, citations: [{ source: { ...source, revision_id: 'latest' }, quote }] } },
    { ...result, follow_up: { ...result.follow_up, body: ' ' } },
    { ...result, actions: [{ ...action, owner: { type: 'assigned' as const, name: 'Morgan', basis } }] },
    { ...result, actions: [{ ...action, deadline: { type: 'stated' as const, text: '2026-10-30', basis } }] },
    { ...result, actions: [{ ...action, basis: { type: 'user' as const } }] },
  ];
  for (const invalidResult of invalid) assert.equal(preparedMeetingFits(value, binding, invalidResult), false, 'INVALID_PREPARED_PROVENANCE_ACCEPTED');
});

test('worker request and response decoders reject malformed authority and retain immutable evidence', () => {
  const request = { workspace_id: 'work', resource_id: 'meeting', revision_id: 'begin', run_id: 'run', connection_version: 'cc'.repeat(32) };
  assert.ok(Object.isFrozen(decodeMeetingRequest(request)));
  for (const invalid of [{ ...request, endpoint: 'https://untrusted.invalid/' }, { ...request, connection_version: 'unversioned' }, { ...request, resource_id: '../meeting' }]) assert.throws(() => decodeMeetingRequest(invalid));
  const result = prepared(), action = result.actions[0]!;
  for (const invalid of [{ ...result, sent: true }, { ...result, notes: { ...result.notes, resource_id: 'provider-owned' } }, { ...result, actions: [{ ...action, id: 'provider-owned' }] }, { ...result, actions: [{ ...action, basis: { type: 'user' } }] }]) {
    assert.throws(() => decodePreparedMeeting(invalid), Error, 'PROVIDER_AUTHORITY_FIELDS_ACCEPTED');
  }
  assert.ok(Object.isFrozen(result.actions[0]?.basis) && Object.isFrozen(result.notes.citations[0]?.source));
});

test('retained publication decoder preserves exact retries and rejects mixed payload variants', () => {
  const binding = bindPreparation(selection()); assert.ok(binding);
  const command = publication(binding, prepared());
  assert.deepEqual(decodeRetainedCommand(JSON.parse(JSON.stringify(command))), command, 'RETAINED_PUBLICATION_CHANGED');
  for (const invalid of [
    { ...command, expected_heads: [] }, { ...command, expected_heads: ['begin', 'begin'] },
    { ...command, run_id: '../run' }, { ...command, body: 'mixed payload' },
    { ...command, type: 'send_email' },
  ]) assert.throws(() => decodeRetainedCommand(invalid), Error, 'INVALID_RETAINED_COMMAND_ACCEPTED');
});

test('editing only action text preserves unchanged owner and deadline transcript evidence', () => {
  const action: MeetingAction = { id: 'action', ...prepared().actions[0]! };
  const changed = correctedAction(action, 'Send the revised budget.', 'Alex', 'next Friday');
  assert.equal(changed.basis.type, 'user');
  assert.deepEqual(changed.owner, action.owner, 'UNCHANGED_OWNER_EVIDENCE_RELABELED');
  assert.deepEqual(changed.deadline, action.deadline, 'UNCHANGED_DEADLINE_EVIDENCE_RELABELED');
  const reassigned = correctedAction(action, action.text, 'Morgan', 'Monday');
  assert.equal(reassigned.basis.type, 'transcript');
  assert.deepEqual(reassigned.owner, { type: 'assigned', name: 'Morgan', basis: { type: 'user' } });
  assert.deepEqual(reassigned.deadline, { type: 'stated', text: 'Monday', basis: { type: 'user' } });
  const cleared = correctedAction(action, action.text, '', '');
  assert.deepEqual(cleared.owner, { type: 'unassigned' }); assert.deepEqual(cleared.deadline, { type: 'unspecified' });
  assert.ok(isWorkRevision(selection().revision));
});
