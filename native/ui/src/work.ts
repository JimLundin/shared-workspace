import type { Command, MeetingAction, MeetingWork, Resource, Snapshot, SourceReference, WorkRevision } from './protocol.ts';
import { isNoteRevision, isWorkRevision } from './protocol.ts';
import type { PreparedMeeting } from './meeting-preparation.ts';

export type WorkCommand = Extract<Command, { type: 'create_work' | 'edit_work' | 'publish_meeting_materials' }>;
export type CurrentWork = Readonly<{ resource: Resource; revision: WorkRevision }>;
export type WorkSelection = Readonly<{ type: 'ready'; current: CurrentWork }> | Readonly<{ type: 'unavailable'; reason: string }>;
export type PreparationBinding = Readonly<{
  workspaceId: string;
  resourceId: string;
  revisionId: string;
  runId: string;
  originatingDevice: string;
  title: string;
  work: MeetingWork;
}>;

export function currentWork(snapshot: Snapshot, resourceId: string): WorkSelection {
  const resource = snapshot.resources.find(resource => resource.id === resourceId);
  if (!resource || !snapshot.workspaces.some(workspace => workspace.id === resource.workspace_id)) {
    return Object.freeze({ type: 'unavailable', reason: 'This work is unavailable on this device.' });
  }
  if (resource.heads.length !== 1) return Object.freeze({ type: 'unavailable', reason: 'Review the work’s versions before preparing material.' });
  const revision = resource.heads[0]!;
  if (!isWorkRevision(revision)) return Object.freeze({ type: 'unavailable', reason: 'Choose a piece of meeting work to prepare.' });
  return Object.freeze({ type: 'ready', current: Object.freeze({ resource, revision }) });
}

export function workInWorkspace(snapshot: Snapshot, workspaceId: string): readonly Resource[] {
  const workspace = snapshot.workspaces.find(workspace => workspace.id === workspaceId);
  return Object.freeze((workspace?.placements ?? []).flatMap(placement => {
    const resource = snapshot.resources.find(resource => resource.id === placement.resource_id && resource.workspace_id === placement.source_workspace_id);
    return resource?.heads.some(isWorkRevision) ? [resource] : [];
  }));
}

export function sourceText(snapshot: Snapshot, source: SourceReference): string | null {
  const resource = snapshot.resources.find(resource => resource.id === source.resource_id && resource.workspace_id === source.workspace_id);
  const revision = [...(resource?.heads ?? []), ...(resource?.history ?? [])].find(revision => revision.id === source.revision_id);
  return revision && isNoteRevision(revision) ? revision.body : null;
}
export function sameSource(left: SourceReference, right: SourceReference): boolean {
  return left.workspace_id === right.workspace_id && left.resource_id === right.resource_id && left.revision_id === right.revision_id;
}

export function preparationProblem(snapshot: Snapshot, current: CurrentWork, resume = false): string | null {
  const work = current.revision.work;
  if (!work.sources.length) return 'Add a meeting transcript before preparing notes.';
  if (work.sources.some(source => source.workspace_id !== current.resource.workspace_id || sourceText(snapshot, source) === null)) {
    return 'A saved transcript revision is unavailable. Review this work’s sources.';
  }
  const transcripts = work.sources.map(source => sourceText(snapshot, source)!);
  if (transcripts.some(text => !text.trim())) return 'A selected transcript is empty. Choose a saved transcript with meeting content.';
  const lengths = transcripts.map(text => new TextEncoder().encode(text).byteLength);
  if (lengths.some(length => length > 128 * 1024) || lengths.reduce((total, length) => total + length, 0) > 512 * 1024) {
    return 'Select up to 512 KiB of transcript text for one preparation. Split a larger meeting into separate work requests; no text will be truncated.';
  }
  if (resume) {
    if (work.state.type !== 'preparing') return 'This work no longer has an interrupted preparation to resume.';
    if (work.state.run.originating_device !== snapshot.device_id) return 'Preparation started on another device. Reconnect to inspect its result before starting a new run.';
  } else if (work.state.type !== 'requested' && work.state.type !== 'needs_input') {
    return work.state.type === 'materials_ready' ? 'The material is already ready. Revise the request to prepare new material.' :
      'Preparation is already attached to this work. Resume it on its originating device or revise the request deliberately.';
  }
  return null;
}

export function bindPreparation(current: CurrentWork): PreparationBinding | null {
  if (current.revision.work.state.type !== 'preparing') return null;
  return Object.freeze({
    workspaceId: current.resource.workspace_id, resourceId: current.resource.id, revisionId: current.revision.id,
    runId: current.revision.work.state.run.id, originatingDevice: current.revision.work.state.run.originating_device,
    title: current.revision.title, work: current.revision.work,
  });
}

/** Provenance check is a decision over the captured sources, independent of fetch/storage. */
export function preparedMeetingFits(snapshot: Snapshot, binding: PreparationBinding, result: PreparedMeeting): boolean {
  const validCitation = (citation: PreparedMeeting['notes']['citations'][number]): boolean =>
    binding.work.sources.some(source => sameSource(source, citation.source)) && Boolean(citation.quote.trim()) &&
    (sourceText(snapshot, citation.source)?.includes(citation.quote) ?? false);
  const validCitations = (citations: PreparedMeeting['notes']['citations']): boolean => citations.length > 0 && citations.every(validCitation);
  if (!result.notes.body.trim() || !result.follow_up.body.trim() || !validCitations(result.notes.citations) || !validCitations(result.follow_up.citations)) return false;
  return result.actions.every(action => {
    if (action.basis.type !== 'transcript' || !validCitations(action.basis.citations)) return false;
    if (action.owner.type === 'assigned' && (action.owner.basis.type !== 'transcript' ||
      !validCitations(action.owner.basis.citations) || !action.owner.basis.citations.some(citation => citation.quote.includes(action.owner.type === 'assigned' ? action.owner.name : '')))) return false;
    if (action.deadline.type === 'stated' && (action.deadline.basis.type !== 'transcript' ||
      !validCitations(action.deadline.basis.citations) || !action.deadline.basis.citations.some(citation => citation.quote.includes(action.deadline.type === 'stated' ? action.deadline.text : '')))) return false;
    return true;
  });
}

export function publication(binding: PreparationBinding, result: PreparedMeeting): Extract<WorkCommand, { type: 'publish_meeting_materials' }> {
  return Object.freeze({
    type: 'publish_meeting_materials', operation_id: binding.runId + '-materials', workspace_id: binding.workspaceId,
    resource_id: binding.resourceId, expected_heads: Object.freeze([binding.revisionId]), run_id: binding.runId,
    notes: Object.freeze({ ...result.notes, resource_id: binding.runId + '-notes' }),
    actions: Object.freeze(result.actions.map((action, index) => Object.freeze({ ...action, id: binding.runId + '-action-' + index }))),
    follow_up: Object.freeze({ ...result.follow_up, resource_id: binding.runId + '-follow-up' }),
  });
}

export function correctedAction(action: MeetingAction, text: string, owner: string, deadline: string): MeetingAction {
  const basis = Object.freeze({ type: 'user' as const });
  const ownerName = owner.trim(), deadlineText = deadline.trim();
  const currentOwner = action.owner.type === 'assigned' ? action.owner.name : '';
  const currentDeadline = action.deadline.type === 'stated' ? action.deadline.text : '';
  return Object.freeze({ ...action, text,
    basis: text === action.text ? action.basis : basis,
    owner: ownerName === currentOwner ? action.owner : ownerName ? Object.freeze({ type: 'assigned', name: ownerName, basis }) : Object.freeze({ type: 'unassigned' }),
    deadline: deadlineText === currentDeadline ? action.deadline : deadlineText ? Object.freeze({ type: 'stated', text: deadlineText, basis }) : Object.freeze({ type: 'unspecified' }),
  });
}
