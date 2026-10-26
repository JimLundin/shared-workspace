import type { ConnectionState } from '../../../browser/host/src/connections.ts';
import type { MeetingAction, MeetingMaterialInput } from './protocol.ts';
import { decodeMeetingActions, decodeMeetingMaterialInput, object, string } from './protocol.ts';

export type MeetingRequest = Readonly<{
  workspace_id: string;
  resource_id: string;
  revision_id: string;
  run_id: string;
  connection_version: string;
}>;
export type PreparedMaterial = Omit<MeetingMaterialInput, 'resource_id'>;
export type PreparedAction = Omit<MeetingAction, 'id'>;
export type PreparedMeeting = Readonly<{
  notes: PreparedMaterial;
  actions: readonly PreparedAction[];
  follow_up: PreparedMaterial;
}>;
export interface MeetingPreparationPort {
  connection(workspaceId: string): Promise<ConnectionState>;
  prepareMeeting(input: MeetingRequest, signal?: AbortSignal): Promise<PreparedMeeting>;
}

export function exactRecord(value: unknown, fields: readonly string[]): Record<string, unknown> {
  const result = object(value);
  if (Object.keys(result).length !== fields.length || !fields.every(field => field in result)) {
    throw new Error('The retained work record has invalid fields.');
  }
  return result;
}
export function identity(value: unknown): string {
  const result = string(value);
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(result)) throw new Error('The work address is invalid.');
  return result;
}
export function decodeMeetingRequest(value: unknown): MeetingRequest {
  const input = exactRecord(value, ['workspace_id', 'resource_id', 'revision_id', 'run_id', 'connection_version']);
  const version = string(input.connection_version);
  if (!/^[a-f0-9]{64}$/.test(version)) throw new Error('The captured workspace connection is invalid.');
  return Object.freeze({
    workspace_id: identity(input.workspace_id), resource_id: identity(input.resource_id),
    revision_id: identity(input.revision_id), run_id: identity(input.run_id), connection_version: version,
  });
}

/** Validate worker replies without exposing the provider's request implementation. */
export function decodePreparedMeeting(value: unknown): PreparedMeeting {
  const result = exactRecord(value, ['notes', 'actions', 'follow_up']);
  const material = (value: unknown): PreparedMaterial => {
    const input = exactRecord(value, ['title', 'body', 'citations']);
    const decoded = decodeMeetingMaterialInput({ ...input, resource_id: 'prepared-material' });
    return Object.freeze({ title: decoded.title, body: decoded.body, citations: decoded.citations });
  };
  if (!Array.isArray(result.actions)) throw new Error('The prepared actions are invalid.');
  const actions = decodeMeetingActions(result.actions.map((value: unknown, index: number) => {
    const action = exactRecord(value, ['text', 'owner', 'deadline', 'basis']);
    return { ...action, id: 'prepared-action-' + index };
  })).map(({ id: _id, ...action }) => {
    if (action.basis.type !== 'transcript' || (action.owner.type === 'assigned' && action.owner.basis.type !== 'transcript') ||
      (action.deadline.type === 'stated' && action.deadline.basis.type !== 'transcript')) {
      throw new Error('Generated actions must retain their transcript evidence.');
    }
    return Object.freeze(action);
  });
  return Object.freeze({ notes: material(result.notes), actions: Object.freeze(actions), follow_up: material(result.follow_up) });
}
