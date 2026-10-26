import type { MeetingAction, Receipt, SourceReference } from './protocol.ts';
import { decodeWork, string } from './protocol.ts';
import type { WorkspaceSession } from './session.ts';
import { sameHeads } from './session.ts';
import { decodePreparedMeeting, exactRecord, identity, type MeetingPreparationPort } from './meeting-preparation.ts';
import { decodeRetainedCommand } from './retained-commands.ts';
import { RetainedInputJournal } from './retained-input.ts';
import { bindPreparation, currentWork, preparedMeetingFits, preparationProblem, publication, type PreparationBinding, type WorkCommand } from './work.ts';

export type WorkActivity =
  | Readonly<{ type: 'idle' }>
  | Readonly<{ type: 'saving'; command: WorkCommand }>
  | Readonly<{ type: 'recovering'; command: WorkCommand }>
  | Readonly<{ type: 'discarding' }>
  | Readonly<{ type: 'preparing'; workspaceId: string; resourceId: string; runId: string }>
  | Readonly<{ type: 'uncertain'; command: WorkCommand; message: string }>
  | Readonly<{ type: 'committed'; command: WorkCommand; message: string }>
  | Readonly<{ type: 'conflict'; command: WorkCommand; receipt: Extract<Receipt, { type: 'conflict' }> }>
  | Readonly<{ type: 'interrupted'; workspaceId: string; resourceId: string; runId: string; message: string }>
  | Readonly<{ type: 'failed'; message: string }>;
type SavedCommand = Readonly<{ originatingDevice: string; command: WorkCommand }>;
type RecoveryActivity = Extract<WorkActivity, { type: 'recovering' }>;
type RetainedAttempt = (Readonly<{ type: 'command' }> & SavedCommand) | (Readonly<{ type: 'committed' }> & SavedCommand) |
  (Readonly<{ type: 'preparation' }> & PreparationBinding);
export type NewMeeting = Readonly<{ workspaceId: string; title: string; outcome: string; sources: readonly SourceReference[] }>;
export type RevisedMeeting = NewMeeting & Readonly<{ resourceId: string; expectedHeads: readonly string[] }>;
export type ActionCorrection = Readonly<{ workspaceId: string; resourceId: string; expectedHeads: readonly string[]; actions: readonly MeetingAction[] }>;
const SLOT = 'meeting-attempt';

function retainedAttempt(value: unknown): RetainedAttempt {
  const kind = value !== null && typeof value === 'object' ? (value as { type?: unknown }).type : undefined;
  if (kind === 'command' || kind === 'committed') {
    const input = exactRecord(value, ['type', 'originatingDevice', 'command']);
    const command = decodeRetainedCommand(input.command);
    if (command.type !== 'create_work' && command.type !== 'edit_work' && command.type !== 'publish_meeting_materials') {
      throw new Error('The retained attempt is not addressed to work.');
    }
    return kind === 'command' ? Object.freeze({ type: 'command', originatingDevice: string(input.originatingDevice), command }) :
      Object.freeze({ type: 'committed', originatingDevice: string(input.originatingDevice), command });
  }
  if (kind === 'preparation') {
    const input = exactRecord(value, ['type', 'workspaceId', 'resourceId', 'revisionId', 'runId', 'originatingDevice', 'title', 'work']);
    const work = decodeWork(input.work), runId = identity(input.runId), originatingDevice = string(input.originatingDevice);
    if (work.state.type !== 'preparing' || work.state.run.id !== runId || work.state.run.originating_device !== originatingDevice) {
      throw new Error('The retained run identity does not match its work.');
    }
    return Object.freeze({ type: kind, workspaceId: identity(input.workspaceId), resourceId: identity(input.resourceId),
      revisionId: identity(input.revisionId), runId, originatingDevice, title: string(input.title), work });
  }
  throw new Error('The retained work attempt needs a newer application version.');
}

/** Owns one explicitly started preparation on this host. Peers share work, not execution rights. */
export class WorkController {
  readonly #session: WorkspaceSession;
  readonly #provider: MeetingPreparationPort | undefined;
  readonly #id: () => string;
  readonly #now: () => number;
  readonly #listeners = new Set<(state: WorkActivity) => void>();
  #journal: RetainedInputJournal | undefined;
  #disposed = false;
  #restored = false;
  #capture: AbortController | null = null;
  #state: WorkActivity = Object.freeze({ type: 'idle' });

  constructor(session: WorkspaceSession, options: Readonly<{ provider?: MeetingPreparationPort; journal?: RetainedInputJournal; id?: () => string; now?: () => number }> = {}) {
    this.#session = session; this.#provider = options.provider; this.#journal = options.journal;
    this.#id = options.id ?? (() => crypto.randomUUID()); this.#now = options.now ?? (() => Date.now());
  }
  get state(): WorkActivity { return this.#state; }
  get busy(): boolean { return this.#state.type === 'saving' || this.#state.type === 'preparing' || this.#state.type === 'recovering' || this.#state.type === 'discarding'; }
  hasPendingInput(): boolean { return this.busy || this.#state.type === 'uncertain' || this.#state.type === 'committed' || this.#state.type === 'conflict' || this.#state.type === 'interrupted' ||
    Boolean(this.#journal?.read(SLOT, 'meeting')) || Boolean(this.#journal?.failed) || Boolean(this.#journal?.pending); }
  attachJournal(journal: RetainedInputJournal): void {
    if (this.busy || this.#restored) throw new Error('Local retention must be attached before restoring or beginning work.');
    this.#journal = journal;
  }
  subscribe(listener: (state: WorkActivity) => void): () => void { this.#listeners.add(listener); return () => this.#listeners.delete(listener); }
  #update(state: WorkActivity): void {
    if (this.#disposed) return;
    this.#state = Object.freeze(state);
    for (const listener of this.#listeners) listener(this.#state);
  }
  #fail(message: string): false { this.#update({ type: 'failed', message }); return false; }
  #recover(command: WorkCommand): RecoveryActivity {
    const activity: RecoveryActivity = Object.freeze({ type: 'recovering', command });
    this.#update(activity);
    return activity;
  }
  #available(allowInterrupted = false): boolean {
    if (this.#disposed || this.busy || this.#state.type === 'uncertain' || this.#state.type === 'committed' || this.#state.type === 'conflict') return false;
    if (this.#state.type === 'interrupted' && !allowInterrupted) return false;
    if (this.#state.type === 'failed' && this.#journal?.read(SLOT, 'meeting')) return false;
    if (!this.#session.snapshot()) return this.#fail('Wait for the saved workspace to open.');
    if (this.#session.state.mutation.type === 'sending' || this.#session.state.mutation.type === 'uncertain') {
      return this.#fail('Finish or recover the pending document save before changing work.');
    }
    return true;
  }

  async restore(): Promise<void> {
    if (this.#restored || this.#disposed) return;
    if (!this.#session.snapshot()) await this.#session.refresh();
    const snapshot = this.#session.snapshot();
    if (!snapshot || this.#disposed) return;
    this.#restored = true;
    const raw = this.#journal?.read(SLOT, 'meeting');
    if (!raw) return;
    try {
      const retained = retainedAttempt(JSON.parse(raw));
      if (retained.originatingDevice !== snapshot.device_id) throw new Error('The retained attempt belongs to another host.');
      if (retained.type === 'command' || retained.type === 'committed') {
        this.#update({ type: retained.type === 'committed' ? 'committed' : 'uncertain', command: retained.command,
          message: retained.type === 'committed' ? 'This work was saved. Refresh to load its committed result and finish local recovery.' :
            'The previous save has no retained receipt. Retry this exact change to inspect its outcome.' });
      } else {
        const selection = currentWork(snapshot, retained.resourceId);
        if (selection.type === 'ready' && selection.current.revision.work.state.type === 'materials_ready' &&
          selection.current.revision.work.state.run.id === retained.runId) {
          await this.#journal!.remove(SLOT);
          this.#update({ type: 'idle' });
        } else {
          this.#update({ type: 'interrupted', workspaceId: retained.workspaceId, resourceId: retained.resourceId, runId: retained.runId,
            message: 'Preparation was interrupted. Inspect the saved work, then resume deliberately on this device.' });
        }
      }
    } catch { this.#fail('Retained work could not be restored safely. Keep this device’s saved data and review the interrupted input.'); }
  }

  async #send(command: WorkCommand): Promise<boolean> {
    const snapshot = this.#session.snapshot();
    if (!snapshot || this.#disposed) return false;
    this.#update({ type: 'saving', command });
    // The input is durable locally before the effect begins. A retention failure does not send a command.
    try { await this.#journal?.put(SLOT, 'meeting', { type: 'command', originatingDevice: snapshot.device_id, command }); }
    catch { return this.#fail('This work request could not be retained. Keep this window open and try again.'); }
    if (this.#disposed) return false;
    let receipt: Receipt;
    try {
      receipt = await this.#session.host.command(command);
      if (this.#disposed) return false;
      if (receipt.type !== 'conflict' && (receipt.operation_id !== command.operation_id || receipt.workspace_id !== command.workspace_id)) {
        throw new Error('Uncorrelated work receipt.');
      }
      if (receipt.type === 'conflict' && (command.type === 'create_work' || receipt.resource_id !== command.resource_id || !sameHeads(receipt.expected_heads, command.expected_heads))) {
        throw new Error('Uncorrelated work conflict.');
      }
    } catch {
      this.#update({ type: 'uncertain', command, message: 'No save receipt was received. Retry this same change; a timeout does not establish that it failed.' });
      return false;
    }
    if (receipt.type === 'applied') {
      // Retain the observed receipt separately from an unknown result. Cleanup/read errors cannot erase that evidence.
      const recovery = this.#recover(command);
      try { await this.#journal?.put(SLOT, 'meeting', { type: 'committed', originatingDevice: snapshot.device_id, command }); }
      catch {
        if (this.#state === recovery) this.#update({ type: 'committed', command, message: 'The work was saved, but retaining its receipt was not confirmed. Refresh to finish recovery.' });
        return false;
      }
      return this.#finishCommitted(recovery);
    }
    if (receipt.type === 'rejected') {
      try { await this.#journal?.remove(SLOT); }
      catch { return this.#fail('The change was rejected before saving, but local recovery cleanup was not confirmed.'); }
      return this.#fail('The work change was not saved. Review its sources and values before trying again.');
    }
    this.#update({ type: 'conflict', command, receipt });
    await this.#session.refresh();
    return false;
  }

  async #finishCommitted(recovery: RecoveryActivity): Promise<boolean> {
    if (this.#disposed || this.#state !== recovery) return false;
    await this.#session.refresh();
    if (this.#disposed || this.#state !== recovery) return false;
    const command = recovery.command;
    const snapshot = this.#session.snapshot(), resource = snapshot?.resources.find(resource => resource.id === command.resource_id && resource.workspace_id === command.workspace_id);
    const observed = resource && [...resource.heads, ...resource.history].some(revision => revision.id === command.operation_id);
    if (this.#session.state.host.type !== 'ready' || !observed) {
      this.#update({ type: 'committed', command, message: 'The work was saved, but its new revision could not be loaded. Refresh to inspect it.' });
      return false;
    }
    try { await this.#journal?.remove(SLOT); }
    catch {
      if (this.#state === recovery) this.#update({ type: 'committed', command, message: 'The work was saved, but local recovery cleanup was not confirmed. Refresh to finish recovery.' });
      return false;
    }
    if (this.#disposed || this.#state !== recovery) return false;
    this.#update({ type: 'idle' });
    return true;
  }

  async create(input: NewMeeting): Promise<string | null> {
    if (!this.#available()) return null;
    const command = decodeRetainedCommand({ type: 'create_work', operation_id: this.#id(), workspace_id: input.workspaceId,
      resource_id: this.#id(), title: input.title.trim(), outcome: input.outcome, sources: input.sources });
    if (command.type !== 'create_work') return null;
    return await this.#send(command) ? command.resource_id : null;
  }
  async revise(input: RevisedMeeting): Promise<boolean> {
    if (!this.#available()) return false;
    const command = decodeRetainedCommand({ type: 'edit_work', operation_id: this.#id(), workspace_id: input.workspaceId,
      resource_id: input.resourceId, expected_heads: input.expectedHeads,
      change: { type: 'revise_request', title: input.title.trim(), outcome: input.outcome, sources: input.sources } });
    return command.type === 'edit_work' && this.#send(command);
  }
  async correctActions(input: ActionCorrection): Promise<boolean> {
    if (!this.#available()) return false;
    const command = decodeRetainedCommand({ type: 'edit_work', operation_id: this.#id(), workspace_id: input.workspaceId,
      resource_id: input.resourceId, expected_heads: input.expectedHeads, change: { type: 'correct_actions', actions: input.actions } });
    return command.type === 'edit_work' && this.#send(command);
  }

  async prepare(resourceId: string): Promise<void> {
    if (!this.#available()) return;
    const snapshot = this.#session.snapshot()!, selected = currentWork(snapshot, resourceId);
    if (selected.type !== 'ready') { this.#fail(selected.reason); return; }
    const problem = preparationProblem(snapshot, selected.current);
    if (problem) { this.#fail(problem); return; }
    const command: WorkCommand = Object.freeze({ type: 'edit_work', operation_id: this.#id(), workspace_id: selected.current.resource.workspace_id,
      resource_id: resourceId, expected_heads: Object.freeze([selected.current.revision.id]),
      change: Object.freeze({ type: 'begin_preparation', run_id: this.#id(), started_at_ms: this.#now() }) });
    if (!await this.#send(command) || this.#disposed) return;
    // Use the exact committed begin revision. A peer's newer request cannot retarget the run.
    const current = currentWork(this.#session.snapshot()!, resourceId);
    if (current.type !== 'ready' || current.current.revision.id !== command.operation_id) {
      this.#fail('The work changed while preparation began. Review the current request.'); return;
    }
    const binding = bindPreparation(current.current);
    if (!binding) { this.#fail('The saved work did not confirm this preparation.'); return; }
    await this.#run(binding);
  }
  async resume(resourceId: string): Promise<void> {
    if (!this.#available(true)) return;
    const snapshot = this.#session.snapshot()!, selected = currentWork(snapshot, resourceId);
    if (selected.type !== 'ready') { this.#fail(selected.reason); return; }
    const problem = preparationProblem(snapshot, selected.current, true);
    if (problem) { this.#fail(problem); return; }
    const binding = bindPreparation(selected.current);
    if (binding) await this.#run(binding);
  }

  async #run(binding: PreparationBinding): Promise<void> {
    if (!this.#available(true)) return;
    const capturedSnapshot = this.#session.snapshot();
    if (!capturedSnapshot || binding.originatingDevice !== capturedSnapshot.device_id || this.#disposed) return;
    const controller = new AbortController(); this.#capture = controller;
    this.#update({ type: 'preparing', workspaceId: binding.workspaceId, resourceId: binding.resourceId, runId: binding.runId });
    try { await this.#journal?.put(SLOT, 'meeting', { type: 'preparation', ...binding }); }
    catch { if (this.#capture === controller) this.#capture = null; this.#fail('Preparation could not be retained on this device. Keep the saved work open.'); return; }
    let question = 'OpenAI preparation stopped. Check this workspace’s connection and resume when ready.';
    try {
      if (!this.#provider) {
        question = 'Open this work on a device with OpenAI preparation enabled.';
        throw new Error('Preparation capability unavailable.');
      }
      const connection = await this.#provider.connection(binding.workspaceId);
      if (connection.workspace_id !== binding.workspaceId || connection.status !== 'ready') {
        question = connection.status === 'conflict' ? 'Resolve this workspace’s OpenAI connection before preparing material.' :
          'Add an OpenAI connection to this workspace before preparing material.';
        throw new Error('Workspace connection unavailable.');
      }
      if (this.#disposed) return;
      if (controller.signal.aborted) throw new Error('Preparation interrupted.');
      const response = await this.#provider.prepareMeeting({ workspace_id: binding.workspaceId, resource_id: binding.resourceId,
        revision_id: binding.revisionId, run_id: binding.runId, connection_version: connection.version }, controller.signal);
      if (this.#disposed) return;
      if (controller.signal.aborted) throw new Error('Preparation interrupted.');
      const result = decodePreparedMeeting(response);
      if (!preparedMeetingFits(capturedSnapshot, binding, result)) {
        question = 'The generated material did not retain valid transcript evidence. Review the sources and prepare again.';
        throw new Error('Invalid generated provenance.');
      }
      await this.#send(publication(binding, result));
    } catch {
      if (this.#disposed) return;
      if (controller.signal.aborted) question = 'Preparation was interrupted. Resume when ready.';
      const command: WorkCommand = Object.freeze({ type: 'edit_work', operation_id: binding.runId + '-input', workspace_id: binding.workspaceId,
        resource_id: binding.resourceId, expected_heads: Object.freeze([binding.revisionId]),
        change: Object.freeze({ type: 'need_input', run_id: binding.runId, question }) });
      if (await this.#send(command)) this.#fail(question);
    } finally { if (this.#capture === controller) this.#capture = null; }
  }

  async retry(): Promise<void> {
    if (this.#disposed) return;
    if (this.#state.type === 'committed') { await this.#finishCommitted(this.#recover(this.#state.command)); return; }
    if (this.#state.type !== 'uncertain') return;
    const command = this.#state.command;
    // Original request receipts are host-local; restore() verifies the origin before exposing retry.
    await this.#send(command);
  }
  interrupt(): void { this.#capture?.abort(); }
  async discardInterrupted(): Promise<void> {
    if (this.#disposed || this.busy || this.#state.type === 'uncertain' || this.#state.type === 'committed') return;
    const activity: Extract<WorkActivity, { type: 'discarding' }> = Object.freeze({ type: 'discarding' });
    this.#update(activity);
    try { await this.#journal?.remove(SLOT); if (this.#state === activity) this.#update({ type: 'idle' }); }
    catch { if (this.#state === activity) this.#fail('Discarding local interrupted input was not confirmed. Keep this window open.'); }
  }
  dispose(): void { this.#disposed = true; this.#capture?.abort(); this.#listeners.clear(); }
}
