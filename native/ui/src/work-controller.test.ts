import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkspaceSession } from './session.ts';
import { WorkController } from './work-controller.ts';
import { RetainedInputJournal } from './retained-input.ts';
import type { LocalDraftPort } from './retained-input.ts';
import { decodeSnapshot, isWorkRevision } from './protocol.ts';
import type { Command, Invite, MeetingWorkState, Receipt, Resource, Snapshot, SyncReport, WorkRevision } from './protocol.ts';
import type { Host } from './host.ts';
import { decodePreparedMeeting } from './meeting-preparation.ts';
import type { MeetingPreparationPort, MeetingRequest, PreparedMeeting } from './meeting-preparation.ts';
import type { ConnectionState } from '../../../browser/host/src/connections.ts';
import type { LocalDraft, LocalDraftJournal, LocalDraftReceipt, LocalDraftRemoval } from '../../../browser/host/src/local-drafts.ts';

// This host projects controlled command receipts. It is not a Turso/Iroh or live-provider proof.
const device = 'aa'.repeat(32), otherDevice = 'bb'.repeat(32);
const source = { workspace_id: 'work', resource_id: 'transcript', revision_id: 'transcript-original' };
const quote = 'Alex will send the budget by next Friday.';
const citation = { source, quote }, basis = { type: 'transcript' as const, citations: [citation] };
const generated = () => decodePreparedMeeting({ notes: { title: 'Meeting notes', body: 'The budget needs revision.', citations: [citation] },
  actions: [{ text: 'Send the budget.', owner: { type: 'assigned', name: 'Alex', basis }, deadline: { type: 'stated', text: 'next Friday', basis }, basis }],
  follow_up: { title: 'Follow-up text', body: 'Alex will send the budget by next Friday.', citations: [citation] } });
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void; const promise = new Promise<T>((done, failed) => { resolve = done; reject = failed; }); return { promise, resolve, reject }; }
const ids = () => { let id = 0; return () => 'operation-' + ++id; };
function initial(state: MeetingWorkState = { type: 'requested' }): Snapshot {
  return decodeSnapshot({ device_id: device, device_name: 'Fixture host', operation_count: 2, network: { type: 'active' }, peers: [],
    workspaces: [{ id: 'work', title: 'Work', members: [device], heads: ['requested'], placements: [{ resource_id: 'meeting', source_workspace_id: 'work' }] }, { id: 'other', title: 'Other workspace', members: [device], heads: [], placements: [] }],
    resources: [{ id: 'transcript', workspace_id: 'work', heads: [{ id: source.revision_id, author: device, title: 'Transcript', body: quote, parents: [] }], history: [] },
      { id: 'meeting', workspace_id: 'work', heads: [{ id: 'requested', author: device, title: 'Budget meeting', parents: [], work: { type: 'meeting', outcome: 'Prepare notes and follow-up.', sources: [source], state } }], history: [] }],
  });
}
class HostFixture implements Host {
  available = true; value = initial(); commands: Command[] = [];
  ledger = new Map<string, { bytes: string; receipt: Extract<Receipt, { type: 'applied' }> }>();
  snapshotHandler: () => Promise<Snapshot> = async () => this.value;
  commandHandler: (command: Command) => Promise<Receipt> = async command => this.apply(command);
  snapshot(): Promise<Snapshot> { return this.snapshotHandler(); }
  async command(command: Command): Promise<Receipt> { this.commands.push(command); return this.commandHandler(command); }
  async invite(): Promise<Invite> { throw new Error('Unused fixture operation'); }
  async join(): Promise<Snapshot> { throw new Error('Unused fixture operation'); }
  async sync(): Promise<SyncReport> { return { peers: [] }; }
  work(): WorkRevision { const revision = this.value.resources.find(item => item.id === 'meeting')?.heads[0]; assert.ok(revision && isWorkRevision(revision)); return revision; }
  replaceWork(revision: WorkRevision): void {
    this.value = decodeSnapshot({ ...this.value, resources: this.value.resources.map(item => item.id === 'meeting' ? { ...item, heads: [revision], history: [...item.history, ...item.heads] } : item) });
  }
  apply(command: Command): Receipt {
    const prior = this.ledger.get(command.operation_id);
    if (prior) { assert.deepEqual(command, JSON.parse(prior.bytes), 'Fixture exact-retry mismatch'); return { ...prior.receipt, replayed: true }; }
    if (command.type !== 'create_work' && command.type !== 'edit_work' && command.type !== 'publish_meeting_materials') throw new Error('Fixture supports Work commands only');
    const existing = this.value.resources.find(item => item.id === command.resource_id), current = existing?.heads[0];
    if (command.type !== 'create_work' && (!current || JSON.stringify(command.expected_heads) !== JSON.stringify(existing!.heads.map(item => item.id)))) {
      return { type: 'conflict', resource_id: command.resource_id, expected_heads: command.expected_heads, actual_heads: existing?.heads.map(item => item.id) ?? [] };
    }
    let next: WorkRevision, outputs: Resource[] = [];
    if (command.type === 'create_work') {
      next = { id: command.operation_id, author: this.value.device_id, title: command.title, parents: [], work: { type: 'meeting', outcome: command.outcome, sources: command.sources, state: { type: 'requested' } } };
    } else {
      assert.ok(current && isWorkRevision(current));
      let state = current.work.state, work = current.work, title = current.title;
      if (command.type === 'edit_work') {
        switch (command.change.type) {
          case 'begin_preparation': state = { type: 'preparing', run: { id: command.change.run_id, originating_device: this.value.device_id, started_at_ms: command.change.started_at_ms } }; break;
          case 'need_input': assert.ok(state.type === 'preparing'); state = { type: 'needs_input', run: state.run, question: command.change.question }; break;
          case 'revise_request': title = command.change.title; work = { type: 'meeting', outcome: command.change.outcome, sources: command.change.sources, state: { type: 'requested' } }; state = work.state; break;
          case 'correct_actions': assert.ok(state.type === 'materials_ready'); state = { ...state, actions: command.change.actions }; break;
        }
      } else {
        assert.ok(state.type === 'preparing'); assert.equal(state.run.originating_device, this.value.device_id); assert.equal(state.run.id, command.run_id);
        const reference = (resourceId: string) => ({ workspace_id: command.workspace_id, resource_id: resourceId, revision_id: command.operation_id });
        state = { type: 'materials_ready', run: state.run, notes: { reference: reference(command.notes.resource_id), citations: command.notes.citations }, actions: command.actions,
          follow_up: { type: 'unsent_text', material: { reference: reference(command.follow_up.resource_id), citations: command.follow_up.citations } } };
        outputs = [command.notes, command.follow_up].map(item => ({ id: item.resource_id, workspace_id: command.workspace_id, heads: [{ id: command.operation_id, author: this.value.device_id, title: item.title, body: item.body, parents: [] }], history: [] }));
      }
      next = { id: command.operation_id, author: this.value.device_id, title, parents: command.expected_heads, work: { ...work, state } };
    }
    const changed: Resource = { id: command.resource_id, workspace_id: command.workspace_id, heads: [next], history: [...(existing?.history ?? []), ...(existing?.heads ?? [])] };
    this.value = decodeSnapshot({ ...this.value, operation_count: this.value.operation_count + 1,
      resources: [...this.value.resources.filter(item => item.id !== command.resource_id), changed, ...outputs] });
    const receipt = { type: 'applied' as const, operation_id: command.operation_id, workspace_id: command.workspace_id, replayed: false };
    this.ledger.set(command.operation_id, { bytes: JSON.stringify(command), receipt }); return receipt;
  }
}
class DraftFixture implements LocalDraftPort {
  drafts = new Map<string, LocalDraft>(); removed = new Map<string, LocalDraftRemoval>(); writes: LocalDraft[] = [];
  write: (draft: LocalDraft) => Promise<LocalDraftReceipt> = async draft => { this.drafts.set(draft.id, draft); this.removed.delete(draft.id); return { type: 'retained', id: draft.id, generation: draft.generation, replayed: false }; };
  removal: (item: LocalDraftRemoval) => Promise<LocalDraftReceipt> = async item => { this.drafts.delete(item.id); this.removed.set(item.id, item); return { type: 'removed', id: item.id, generation: item.generation, replayed: false }; };
  async listLocalDrafts(): Promise<LocalDraftJournal> { return { drafts: [...this.drafts.values()], removed: [...this.removed.values()] }; }
  async upsertLocalDraft(draft: LocalDraft): Promise<LocalDraftReceipt> { this.writes.push(draft); return this.write(draft); }
  removeLocalDraft(item: LocalDraftRemoval): Promise<LocalDraftReceipt> { return this.removal(item); }
}
class ProviderFixture implements MeetingPreparationPort {
  lookups: string[] = []; requests: MeetingRequest[] = []; signals: (AbortSignal | undefined)[] = [];
  connectionHandler: (workspace: string) => Promise<ConnectionState> = async workspace => ({ workspace_id: workspace, provider: 'openai', status: 'ready', version: 'cc'.repeat(32), model: 'gpt-4o-transcribe' });
  generation: () => Promise<PreparedMeeting> = async () => generated();
  async connection(workspace: string): Promise<ConnectionState> { this.lookups.push(workspace); return this.connectionHandler(workspace); }
  async prepareMeeting(request: MeetingRequest, signal?: AbortSignal): Promise<PreparedMeeting> { this.requests.push(request); this.signals.push(signal); return this.generation(); }
}
async function setup(state?: MeetingWorkState) {
  const host = new HostFixture(); if (state) host.value = initial(state);
  const session = new WorkspaceSession(host), port = new DraftFixture(), journal = await RetainedInputJournal.open(port), provider = new ProviderFixture();
  await session.refresh(); const controller = new WorkController(session, { journal, provider, id: ids(), now: () => 100 });
  await controller.restore(); return { host, session, port, journal, provider, controller };
}
const newMeeting = { workspaceId: 'work', title: 'New meeting', outcome: 'Prepare notes.', sources: [source] };

test('prepare flows through one actor-bound begin and one atomic material publication fixture', async () => {
  const { host, provider, controller, journal } = await setup();
  await controller.prepare('meeting');
  assert.equal(controller.state.type, 'idle'); assert.equal(controller.hasPendingInput(), false);
  assert.deepEqual(host.commands.map(item => item.type), ['edit_work', 'publish_meeting_materials'], 'MATERIALS_SPLIT_ACROSS_COMMANDS');
  const begin = host.commands[0], publish = host.commands[1]; assert.ok(begin?.type === 'edit_work' && begin.change.type === 'begin_preparation'); assert.ok(publish?.type === 'publish_meeting_materials');
  assert.deepEqual(provider.requests, [{ workspace_id: 'work', resource_id: 'meeting', revision_id: begin.operation_id, run_id: begin.change.run_id, connection_version: 'cc'.repeat(32) }]);
  assert.deepEqual(publish.expected_heads, [begin.operation_id], 'PUBLICATION_WORK_ADDRESS_CHANGED');
  assert.equal(publish.run_id, begin.change.run_id);
  assert.equal(host.value.resources.length, 4); assert.equal(host.value.operation_count, 4);
  const work = host.work().work; assert.equal(work.state.type, 'materials_ready');
  if (work.state.type === 'materials_ready') {
    assert.equal(work.state.follow_up.type, 'unsent_text');
    assert.equal(work.state.notes.reference.revision_id, publish.operation_id);
    assert.equal(work.state.follow_up.material.reference.revision_id, publish.operation_id);
  }
  assert.equal(journal.read('meeting-attempt', 'meeting'), null);
});

test('navigation while preparing cannot retarget provider or publication to another workspace', async () => {
  const { host, session, provider, controller } = await setup(), pending = deferred<PreparedMeeting>(), entered = deferred<void>();
  provider.generation = async () => { entered.resolve(); return pending.promise; };
  const preparing = controller.prepare('meeting'); await entered.promise;
  session.navigate({ type: 'workspace', id: 'other' });
  pending.resolve(generated()); await preparing;
  assert.equal(session.state.route.type === 'workspace' && session.state.route.id, 'other');
  assert.deepEqual(provider.lookups, ['work'], 'PREPARATION_FOLLOWED_NAVIGATION');
  assert.ok(host.commands.every(command => command.workspace_id === 'work'), 'PUBLICATION_FOLLOWED_NAVIGATION');
  assert.equal(controller.state.type, 'idle');
});

test('human revision during generation produces a conflict and retains the original publication bytes', async () => {
  const { host, session, provider, controller, journal } = await setup(), pending = deferred<PreparedMeeting>(), entered = deferred<void>();
  provider.generation = async () => { entered.resolve(); return pending.promise; };
  const preparing = controller.prepare('meeting'); await entered.promise;
  const started = host.work(); host.replaceWork({ ...started, id: 'human-revision', parents: [started.id], work: { ...started.work, outcome: 'A corrected request.', state: { type: 'requested' } } });
  await session.refresh(); pending.resolve(generated()); await preparing;
  assert.equal(controller.state.type, 'conflict', 'HUMAN_REVISION_OVERWRITTEN');
  const publish = host.commands.at(-1); assert.ok(publish?.type === 'publish_meeting_materials');
  assert.deepEqual(publish.expected_heads, [started.id], 'UNREVIEWED_WORK_HEAD_ADOPTED');
  assert.equal(host.value.resources.length, 2); assert.equal(host.work().id, 'human-revision');
  assert.ok(journal.read('meeting-attempt', 'meeting')?.includes(publish.operation_id), 'CONFLICTED_MATERIALS_DISCARDED');
});

test('unknown publication retries exact identities after reopen without provider rerun or duplicate outputs', async () => {
  const { host, provider, controller, port } = await setup(); let lost = false;
  host.commandHandler = async command => { const receipt = host.apply(command); if (command.type === 'publish_meeting_materials' && !lost) { lost = true; throw new Error('Receipt lost after commit'); } return receipt; };
  await controller.prepare('meeting'); assert.equal(controller.state.type, 'uncertain');
  const first = host.commands.at(-1); assert.ok(first?.type === 'publish_meeting_materials');
  const bytes = JSON.stringify(first); assert.equal(host.value.resources.length, 4);
  await controller.discardInterrupted(); assert.equal(controller.state.type, 'uncertain', 'UNKNOWN_PUBLICATION_DISCARDED');
  controller.dispose();
  const session = new WorkspaceSession(host); await session.refresh(); const journal = await RetainedInputJournal.open(port);
  const reopened = new WorkController(session, { journal, provider, id: () => 'must-not-allocate' }); await reopened.restore();
  assert.equal(reopened.state.type, 'uncertain'); await reopened.retry();
  assert.deepEqual(host.commands.at(-1), JSON.parse(bytes), 'PUBLICATION_RETRY_CHANGED_AFTER_REOPEN');
  assert.equal(provider.requests.length, 1, 'UNKNOWN_PUBLICATION_REGENERATED');
  assert.equal(host.value.resources.length, 4, 'PUBLICATION_RETRY_DUPLICATED_OUTPUTS');
  assert.equal(host.value.operation_count, 4); assert.equal(reopened.state.type, 'idle');
});

test('another device cannot resume or implicitly take over a preparing run', async () => {
  const { host, provider, controller } = await setup({ type: 'preparing', run: { id: 'remote-run', originating_device: otherDevice, started_at_ms: 100 } });
  await controller.resume('meeting'); await controller.prepare('meeting');
  assert.equal(host.commands.length, 0, 'REMOTE_PREPARATION_TAKEN_OVER');
  assert.equal(provider.requests.length, 0); assert.equal(controller.state.type, 'failed');
});

test('missing provider or connection records needs-input without publishing fictitious material', async () => {
  for (const mode of ['provider', 'connection']) {
    const context = await setup();
    const controller = mode === 'provider' ? new WorkController(context.session, { journal: context.journal, id: ids(), now: () => 100 }) : context.controller;
    context.provider.connectionHandler = async workspace => ({ workspace_id: workspace, provider: 'openai', status: 'missing' });
    await controller.prepare('meeting');
    assert.equal(context.host.commands.filter(item => item.type === 'publish_meeting_materials').length, 0, 'MISSING_PROVIDER_PUBLISHED_MATERIALS');
    assert.equal(context.host.work().work.state.type, 'needs_input'); assert.equal(context.provider.requests.length, 0);
    assert.equal(controller.state.type, 'failed');
  }
});

test('invalid generated provenance cannot reach atomic publication', async () => {
  const { host, provider, controller } = await setup();
  provider.generation = async () => ({ ...generated(), notes: { ...generated().notes, citations: [{ source, quote: 'A fabricated agreement.' }] } });
  await controller.prepare('meeting');
  assert.equal(host.commands.some(item => item.type === 'publish_meeting_materials'), false, 'INVALID_PROVENANCE_PUBLISHED');
  assert.equal(host.work().work.state.type, 'needs_input');
  assert.equal(host.value.resources.length, 2);
});

test('local retention must complete before command dispatch and concurrent clicks are gated during it', async () => {
  const { host, port, controller } = await setup(), gate = deferred<void>(), entered = deferred<void>(), write = port.write;
  port.write = async draft => { entered.resolve(); await gate.promise; return write(draft); };
  const first = controller.create(newMeeting); await entered.promise;
  const second = controller.create({ ...newMeeting, title: 'Second click' });
  assert.equal(host.commands.length, 0, 'WORK_DISPATCHED_BEFORE_RETENTION');
  assert.equal(controller.busy, true, 'WORK_NOT_RESERVED_DURING_RETENTION');
  gate.resolve(); const result = await Promise.all([first, second]);
  assert.equal(host.commands.length, 1, 'CONCURRENT_WORK_CLICK_DISPATCHED');
  assert.equal(result[1], null); assert.ok(result[0]);
});

test('local retention failure sends no host command and keeps pending input visible', async () => {
  const { host, port, controller, journal } = await setup();
  port.write = async () => { throw new Error('Local disk unavailable'); };
  assert.equal(await controller.create(newMeeting), null);
  assert.equal(host.commands.length, 0, 'UNRETAINED_WORK_DISPATCHED');
  assert.equal(controller.state.type, 'failed'); assert.equal(controller.hasPendingInput(), true);
  assert.ok(journal.read('meeting-attempt', 'meeting'));
});

test('interruption is not swallowed when connection or provider resolves after abort', async () => {
  for (const phase of ['connection', 'provider']) {
    const { host, provider, controller } = await setup(), entered = deferred<void>(), gate = deferred<void>();
    if (phase === 'connection') {
      const connection = provider.connectionHandler;
      provider.connectionHandler = async workspace => { entered.resolve(); await gate.promise; return connection(workspace); };
    } else provider.generation = async () => { entered.resolve(); await gate.promise; return generated(); };
    const preparing = controller.prepare('meeting'); await entered.promise;
    controller.interrupt(); gate.resolve(); await preparing;
    assert.equal(controller.busy, false, 'INTERRUPTED_WORK_STUCK_PREPARING');
    assert.notEqual(controller.state.type, 'idle', 'INTERRUPTION_REPORTED_AS_SUCCESS');
    assert.equal(host.commands.some(item => item.type === 'publish_meeting_materials'), false, 'ABORTED_MATERIALS_PUBLISHED');
  }
});

test('failed refresh after a committed command cannot clear recovery or report creation success', async () => {
  const { host, controller, journal } = await setup();
  host.snapshotHandler = async () => { throw new Error('Snapshot read failed'); };
  assert.equal(await controller.create(newMeeting), null, 'FAILED_REFRESH_REPORTED_SUCCESS');
  assert.notEqual(controller.state.type, 'idle', 'FAILED_REFRESH_CLEARED_WORK_STATE');
  assert.ok(journal.read('meeting-attempt', 'meeting'), 'FAILED_REFRESH_DROPPED_RECOVERY');
  assert.equal(host.commands.length, 1);
});

test('known committed recovery after reopen only refreshes and cleans without redispatching', async () => {
  const { host, controller, port } = await setup();
  host.snapshotHandler = async () => { throw new Error('Snapshot read failed'); };
  await controller.create(newMeeting);
  assert.equal(controller.state.type, 'committed', 'KNOWN_COMMIT_RELABELED_UNCERTAIN');
  assert.ok(port.drafts.get('meeting-attempt')?.payload.includes('"type":"committed"'));
  await controller.discardInterrupted(); assert.equal(controller.state.type, 'committed');
  controller.dispose(); host.snapshotHandler = async () => host.value;
  const session = new WorkspaceSession(host); await session.refresh();
  const journal = await RetainedInputJournal.open(port), reopened = new WorkController(session, { journal });
  await reopened.restore(); assert.equal(reopened.state.type, 'committed');
  await reopened.retry();
  assert.equal(host.commands.length, 1, 'KNOWN_COMMIT_REDISPATCHED');
  assert.equal(reopened.state.type, 'idle'); assert.equal(reopened.hasPendingInput(), false);
});

test('committed recovery is reserved before reading and cannot overlap or clear later input', async () => {
  const { host, controller, journal, port } = await setup();
  const phase = () => controller.state.type;
  host.snapshotHandler = async () => { throw new Error('Read failed after commit'); };
  await controller.create(newMeeting);
  assert.equal(phase(), 'committed');
  const read = deferred<Snapshot>(); let reads = 0;
  host.snapshotHandler = async () => { reads++; return read.promise; };
  const first = controller.retry();
  try {
    assert.equal(controller.busy, true, 'WORK_RECOVERY_NOT_RESERVED');
    await controller.retry();
    assert.equal(reads, 1, 'CONCURRENT_COMMITTED_RECOVERY_READ');
    assert.equal(await controller.create({ ...newMeeting, title: 'Too early' }), null);
  } finally { read.resolve(host.value); await first; }
  assert.equal(phase(), 'idle');
  const entered = deferred<void>(), reply = deferred<Receipt>();
  host.commandHandler = async () => { entered.resolve(); return reply.promise; };
  const later = controller.create({ ...newMeeting, title: 'A later meeting' });
  await entered.promise;
  await controller.retry();
  assert.equal(phase(), 'saving', 'RECOVERY_ERASED_NEW_ATTEMPT_STATE');
  const pending = host.commands.at(-1); assert.ok(pending);
  assert.equal(JSON.parse(journal.read('meeting-attempt', 'meeting')!).command.operation_id, pending.operation_id,
    'RECOVERY_ERASED_NEW_ATTEMPT_INPUT');
  assert.equal(JSON.parse(port.drafts.get('meeting-attempt')!.payload).command.operation_id, pending.operation_id);
  host.snapshotHandler = async () => host.value;
  reply.resolve(host.apply(pending)); await later;
});

test('confirmed receipt retention keeps recovery reserved before cleanup can begin', async () => {
  const { host, port, controller } = await setup(), entered = deferred<void>(), retained = deferred<void>(), write = port.write;
  port.write = async draft => {
    if (JSON.parse(draft.payload).type === 'committed') { entered.resolve(); await retained.promise; }
    return write(draft);
  };
  const creation = controller.create(newMeeting); await entered.promise;
  try {
    assert.equal(controller.busy, true, 'COMMITTED_RETENTION_UNRESERVED');
    await controller.retry();
    assert.equal(host.commands.length, 1, 'COMMITTED_RETENTION_REDISPATCHED');
    assert.equal(await controller.create({ ...newMeeting, title: 'Another click' }), null);
  } finally { retained.resolve(); await creation; }
  assert.equal(controller.state.type, 'idle');
});

test('preparation yields to a newer command started after its begin save', async () => {
  const { host, controller, provider, journal } = await setup(), entered = deferred<void>(), reply = deferred<Receipt>();
  let later: Promise<string | null> | undefined;
  host.commandHandler = async command => {
    if (command.type === 'create_work') { entered.resolve(); return reply.promise; }
    return host.apply(command);
  };
  const unsubscribe = controller.subscribe(state => {
    if (state.type === 'idle' && !later && host.commands.at(-1)?.type === 'edit_work') {
      later = controller.create({ ...newMeeting, title: 'Newer addressed work' });
    }
  });
  const preparation = controller.prepare('meeting'); await entered.promise;
  try {
    await preparation;
    assert.equal(provider.requests.length, 0, 'PREPARATION_STARTED_OVER_NEW_ATTEMPT');
    assert.equal(controller.state.type, 'saving', 'PREPARATION_ERASED_NEW_COMMAND_STATE');
    const pending = host.commands.at(-1); assert.ok(pending?.type === 'create_work');
    assert.equal(JSON.parse(journal.read('meeting-attempt', 'meeting')!).command.operation_id, pending.operation_id,
      'PREPARATION_ERASED_NEW_COMMAND_INPUT');
    reply.resolve(host.apply(pending));
  } finally { unsubscribe(); controller.dispose(); reply.reject(new Error('End controlled effect')); await later; }
});

test('discarding interrupted input reserves cleanup against repeated clicks and new commands', async () => {
  const { host, controller, port, journal } = await setup();
  host.snapshotHandler = async () => { throw new Error('Unknown save read'); };
  host.commandHandler = async command => {
    assert.ok(command.type === 'edit_work');
    return { type: 'conflict', resource_id: command.resource_id, expected_heads: ['requested'], actual_heads: ['newer'] };
  };
  await controller.revise({ ...newMeeting, resourceId: 'meeting', expectedHeads: ['requested'] });
  assert.equal(controller.state.type, 'conflict');
  const entered = deferred<void>(), removed = deferred<void>(), removal = port.removal; let removals = 0;
  port.removal = async value => { removals++; entered.resolve(); await removed.promise; return removal(value); };
  const first = controller.discardInterrupted(); await entered.promise;
  try {
    assert.equal(controller.busy, true, 'DISCARD_CLEANUP_NOT_RESERVED');
    await controller.discardInterrupted();
    assert.equal(removals, 1, 'CONCURRENT_DISCARD_CLEANUP');
    assert.equal(await controller.create(newMeeting), null);
  } finally { removed.resolve(); await first; }
  assert.equal(controller.state.type, 'idle'); assert.equal(journal.read('meeting-attempt', 'meeting'), null);
});

test('an applied receipt without its observed revision keeps committed recovery pending', async () => {
  const { host, controller, journal } = await setup();
  host.commandHandler = async command => ({ type: 'applied', operation_id: command.operation_id, workspace_id: command.workspace_id, replayed: false });
  await controller.create(newMeeting);
  assert.equal(controller.state.type, 'committed', 'UNOBSERVED_COMMIT_REPORTED_READY');
  await controller.retry();
  assert.equal(host.commands.length, 1); assert.equal(controller.state.type, 'committed');
  assert.ok(journal.read('meeting-attempt', 'meeting'), 'UNOBSERVED_COMMIT_RECOVERY_DISCARDED');
});

test('cleanup failure after material commit preserves pending recovery and cannot report success', async () => {
  const { host, port, controller } = await setup(), removal = port.removal;
  port.removal = async item => { if (host.commands.at(-1)?.type === 'publish_meeting_materials') throw new Error('Recovery cleanup failed'); return removal(item); };
  await controller.prepare('meeting');
  assert.equal(host.work().work.state.type, 'materials_ready');
  assert.notEqual(controller.state.type, 'idle', 'FAILED_CLEANUP_REPORTED_SUCCESS');
  assert.equal(controller.hasPendingInput(), true, 'FAILED_CLEANUP_DROPPED_PENDING_INPUT');
  assert.ok(port.drafts.has('meeting-attempt'));
  port.removal = removal; await controller.retry();
  assert.equal(host.commands.length, 2, 'CLEANUP_RETRY_REDISPATCHED_PUBLICATION');
  assert.equal(controller.state.type, 'idle');
});

test('uncorrelated host receipt retains the exact request as uncertain', async () => {
  const { host, controller, journal } = await setup();
  host.commandHandler = async command => ({ type: 'applied', operation_id: 'unrelated', workspace_id: command.workspace_id, replayed: false });
  await controller.create(newMeeting);
  assert.equal(controller.state.type, 'uncertain', 'UNCORRELATED_WORK_RECEIPT_ACCEPTED');
  assert.ok(journal.read('meeting-attempt', 'meeting'));
});

test('dispose during provider work keeps local recovery without publishing or claiming completion', async () => {
  const { host, provider, controller, port } = await setup(), entered = deferred<void>(), pending = deferred<PreparedMeeting>();
  provider.generation = async () => { entered.resolve(); return pending.promise; };
  const preparing = controller.prepare('meeting'); await entered.promise; controller.dispose();
  pending.resolve(generated()); await preparing;
  assert.equal(provider.signals[0]?.aborted, true);
  assert.equal(host.commands.some(item => item.type === 'publish_meeting_materials'), false, 'DISPOSED_CONTROLLER_PUBLISHED');
  assert.ok(port.drafts.get('meeting-attempt')?.payload.includes('preparation'), 'DISPOSED_PREPARATION_NOT_RETAINED');
});

test('restored command belonging to another host cannot expose exact retry', async () => {
  const { host, session, journal } = await setup();
  await journal.put('meeting-attempt', 'meeting', { type: 'command', originatingDevice: otherDevice, command: { type: 'create_work', operation_id: 'remote-request', workspace_id: 'work', resource_id: 'remote-work', title: 'Remote', outcome: 'Prepare', sources: [source] } });
  const controller = new WorkController(session, { journal }); await controller.restore(); await controller.retry();
  assert.equal(controller.state.type, 'failed'); assert.equal(host.commands.length, 0, 'FOREIGN_RETAINED_COMMAND_RETRIED');
  assert.equal(controller.hasPendingInput(), true);
});

test('restored preparation stays interrupted until explicit origin-host resume', async () => {
  const { host, session, journal, provider } = await setup({ type: 'preparing', run: { id: 'retained-run', originating_device: device, started_at_ms: 100 } });
  const work = host.work();
  await journal.put('meeting-attempt', 'meeting', { type: 'preparation', workspaceId: 'work', resourceId: 'meeting', revisionId: work.id, runId: 'retained-run', originatingDevice: device, title: work.title, work: work.work });
  const controller = new WorkController(session, { journal, provider }); await controller.restore();
  assert.equal(controller.state.type, 'interrupted'); assert.equal(provider.requests.length, 0, 'RESTORE_AUTOMATICALLY_REGENERATED');
  assert.equal(await controller.create(newMeeting), null, 'INTERRUPTED_ATTEMPT_OVERWRITTEN');
  await controller.resume('meeting');
  assert.equal(controller.state.type, 'idle');
  assert.deepEqual(host.commands.map(item => item.type), ['publish_meeting_materials'], 'RESUME_CREATED_REPLACEMENT_RUN');
  assert.equal(provider.requests[0]?.run_id, 'retained-run');
});
