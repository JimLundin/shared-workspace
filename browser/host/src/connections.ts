/** Pure connection decisions. Secret records never enter generic workspace data. */
import { fields, hex, json, requireThat, scope } from './types.ts';
import { string } from '../../../native/ui/src/protocol.ts';

export const CONNECTION_MODEL = 'gpt-4o-transcribe' as const;
export const MAX_CONNECTION_RECORDS = 1024;
export type ConnectionState =
  | Readonly<{ workspace_id: string; provider: 'openai'; status: 'missing' }>
  | Readonly<{ workspace_id: string; provider: 'openai'; status: 'ready'; version: string; model: typeof CONNECTION_MODEL }>
  | Readonly<{ workspace_id: string; provider: 'openai'; status: 'removed'; version: string }>
  | Readonly<{ workspace_id: string; provider: 'openai'; status: 'conflict'; versions: readonly string[] }>;
export type ConnectionReceipt = Readonly<{ operation_id: string; replayed: boolean; state: ConnectionState }>;
export type ConnectionPeer = Readonly<{ peer_id: string; status: 'unknown' | 'upgrade_required' | 'synced' | 'unreachable' }>;
export type SetConnection = Readonly<{ workspace_id: string; operation_id: string; expected_versions: readonly string[]; api_key: string; model: typeof CONNECTION_MODEL }>;
export type RemoveConnection = Readonly<{ workspace_id: string; operation_id: string; expected_versions: readonly string[] }>;
export type Transcription = Readonly<{ workspace_id: string; connection_version: string; audio: Blob; locale: string }>;
export type ConnectionAction = Readonly<{ type: 'set'; api_key: string; model: typeof CONNECTION_MODEL }> | Readonly<{ type: 'remove' }>;
export type ConnectionRecord = Readonly<{ version: 1; id: string; workspace_id: string; author: string; parents: readonly string[]; action: ConnectionAction }>;
export type SignedConnection = Readonly<{ record: ConnectionRecord; signature: string }>;
export type PublicConnection = Readonly<{ record: Omit<ConnectionRecord, 'action'> & { readonly action: Readonly<{ type: 'set'; model: typeof CONNECTION_MODEL }> | Readonly<{ type: 'remove' }> }; signature: string }>;
export function publicConnection(item: SignedConnection): PublicConnection {
  const { record, signature } = item;
  const action = record.action.type === 'set' ? { type: 'set' as const, model: record.action.model } : { type: 'remove' as const };
  return { record: { version: 1, id: record.id, workspace_id: record.workspace_id, author: record.author, parents: [...record.parents], action }, signature };
}

export class ConnectionError extends Error {
  override readonly name = 'ConnectionError';
}
export function connectionError(message = 'The workspace connection could not be processed.'): ConnectionError { return new ConnectionError(message); }
export function connectionFailure(error: unknown): string {
  return error instanceof ConnectionError ? error.message : 'The workspace connection could not be processed.';
}
export function workspaceId(value: unknown): string { return scope([value])[0]!; }
export function versions(value: unknown): readonly string[] {
  requireThat(Array.isArray(value) && value.length <= 128, 'Invalid connection versions.');
  const result = value.map(hex).sort();
  requireThat(new Set(result).size === result.length, 'Duplicate connection versions.');
  return result;
}
function action(value: unknown): ConnectionAction {
  const variant = fields(value, ['type', ...(typeof value === 'object' && value !== null && 'type' in value && value.type === 'set' ? ['api_key', 'model'] : [])]);
  if (variant.type === 'remove') return { type: 'remove' };
  requireThat(variant.type === 'set' && variant.model === CONNECTION_MODEL, 'Unsupported workspace provider configuration.');
  const key = string(variant.api_key);
  requireThat(/^[!-~]{16,4096}$/.test(key), 'Invalid provider credential.');
  return { type: 'set', api_key: key, model: CONNECTION_MODEL };
}
export function setConnection(value: unknown): SetConnection {
  const input = fields(value, ['workspace_id', 'operation_id', 'expected_versions', 'api_key', 'model']);
  const parsed = action({ type: 'set', api_key: input.api_key, model: input.model });
  requireThat(parsed.type === 'set', 'Invalid connection action.');
  return { workspace_id: workspaceId(input.workspace_id), operation_id: hex(input.operation_id), expected_versions: versions(input.expected_versions), api_key: parsed.api_key, model: parsed.model };
}
export function removeConnection(value: unknown): RemoveConnection {
  const input = fields(value, ['workspace_id', 'operation_id', 'expected_versions']);
  return { workspace_id: workspaceId(input.workspace_id), operation_id: hex(input.operation_id), expected_versions: versions(input.expected_versions) };
}
export function signedConnection(value: unknown): SignedConnection {
  const signed = fields(value, ['record', 'signature']);
  const input = fields(signed.record, ['version', 'id', 'workspace_id', 'author', 'parents', 'action']);
  requireThat(input.version === 1 && typeof signed.signature === 'string' && /^[a-f0-9]{128}$/.test(signed.signature), 'Invalid signed connection.');
  return { record: { version: 1, id: hex(input.id), workspace_id: workspaceId(input.workspace_id), author: hex(input.author), parents: versions(input.parents), action: action(input.action) }, signature: signed.signature };
}
export function connectionRecords(value: unknown, selected: readonly string[]): readonly SignedConnection[] {
  requireThat(Array.isArray(value) && value.length <= MAX_CONNECTION_RECORDS, 'Too much connection history.');
  const result = value.map(signedConnection);
  requireThat(result.every(item => selected.includes(item.record.workspace_id)), 'Connection history contains an unselected workspace.');
  return result;
}
export const connectionPayload = (record: ConnectionRecord): string => json(record);
export function connectionHeads(records: readonly PublicConnection[], workspace: string): readonly string[] {
  const selected = records.filter(item => item.record.workspace_id === workspace);
  const parents = new Set(selected.flatMap(item => item.record.parents));
  return selected.map(item => item.record.id).filter(id => !parents.has(id)).sort();
}
export function connectionState(records: readonly PublicConnection[], workspace: string): ConnectionState {
  const base = { workspace_id: workspace, provider: 'openai' as const };
  const heads = connectionHeads(records, workspace);
  if (heads.length === 0) return { ...base, status: 'missing' };
  if (heads.length > 1) return { ...base, status: 'conflict', versions: heads };
  const head = records.find(item => item.record.id === heads[0])!;
  return head.record.action.type === 'remove' ? { ...base, status: 'removed', version: head.record.id }
    : { ...base, status: 'ready', version: head.record.id, model: head.record.action.model };
}
/** Validate the whole candidate before committing any incoming revision. */
export function mergeConnections(retained: readonly SignedConnection[], incoming: readonly SignedConnection[]): readonly SignedConnection[] {
  const merged = new Map(retained.map(item => [item.record.id, item]));
  for (const item of incoming) {
    const prior = merged.get(item.record.id);
    requireThat(!prior || json(prior) === json(item), 'Connection identity was reused for different content.');
    merged.set(item.record.id, item);
  }
  requireThat(merged.size <= MAX_CONNECTION_RECORDS, 'Connection history limit reached.');
  const pending = new Set(merged.keys()), seen = new Set<string>();
  while (pending.size) {
    const before = pending.size;
    for (const id of pending) {
      const item = merged.get(id)!;
      requireThat(item.record.parents.every(parent => merged.get(parent)?.record.workspace_id === item.record.workspace_id), 'Connection parent is missing or belongs to another workspace.');
      if (item.record.parents.every(parent => seen.has(parent))) { seen.add(id); pending.delete(id); }
    }
    requireThat(pending.size < before, 'Connection history contains a cycle.');
  }
  const result = [...merged.values()].sort((a, b) => a.record.id.localeCompare(b.record.id));
  for (const workspace of new Set(result.map(item => item.record.workspace_id))) {
    requireThat(connectionHeads(result, workspace).length <= 128, 'Too many concurrent connection revisions.');
  }
  return result;
}
export function prepareConnection(records: readonly SignedConnection[], author: string, input: RemoveConnection, next: ConnectionAction): Readonly<{ record: ConnectionRecord; replayed: boolean }> {
  const record: ConnectionRecord = { version: 1, id: input.operation_id, workspace_id: input.workspace_id, author, parents: [...input.expected_versions].sort(), action: next };
  const previous = records.find(item => item.record.id === record.id);
  if (previous) {
    if (connectionPayload(previous.record) !== connectionPayload(record)) throw connectionError('This connection request identity was already used for different input.');
    return { record: previous.record, replayed: true };
  }
  if (json(connectionHeads(records, input.workspace_id)) !== json(record.parents)) throw connectionError('The workspace connection changed. Refresh its settings before saving.');
  return { record, replayed: false };
}
export function activeConnection(records: readonly SignedConnection[], workspace: string, version: string): ConnectionAction & { type: 'set' } {
  const state = connectionState(records, workspace);
  if (state.status !== 'ready' || state.version !== version) throw connectionError('The workspace connection changed or is unavailable. Record again after reviewing its settings.');
  const record = records.find(item => item.record.id === version)!;
  requireThat(record.record.action.type === 'set', 'Connection is unavailable.');
  return record.record.action;
}
export function decodeConnectionState(value: unknown): ConnectionState {
  const candidate = value as Record<string, unknown>;
  const input = fields(value, ['workspace_id', 'provider', 'status', ...(candidate?.status === 'ready' ? ['version', 'model'] : candidate?.status === 'removed' ? ['version'] : candidate?.status === 'conflict' ? ['versions'] : [])]);
  const base = { workspace_id: workspaceId(input.workspace_id), provider: 'openai' as const };
  requireThat(input.provider === 'openai', 'Invalid connection provider.');
  switch (input.status) {
    case 'missing': return { ...base, status: 'missing' };
    case 'ready': requireThat(input.model === CONNECTION_MODEL, 'Invalid connection model.'); return { ...base, status: 'ready', version: hex(input.version), model: CONNECTION_MODEL };
    case 'removed': return { ...base, status: 'removed', version: hex(input.version) };
    case 'conflict': { const items = versions(input.versions); requireThat(items.length > 1, 'Invalid connection conflict.'); return { ...base, status: 'conflict', versions: items }; }
    default: throw connectionError();
  }
}
export function decodeConnectionReceipt(value: unknown): ConnectionReceipt {
  const input = fields(value, ['operation_id', 'replayed', 'state']);
  requireThat(typeof input.replayed === 'boolean', 'Invalid connection receipt.');
  return { operation_id: hex(input.operation_id), replayed: input.replayed, state: decodeConnectionState(input.state) };
}
export function decodeConnectionPeers(value: unknown): readonly ConnectionPeer[] {
  requireThat(Array.isArray(value) && value.length <= 64, 'Invalid connection device status.');
  return value.map(item => {
    const input = fields(item, ['peer_id', 'status']);
    requireThat(input.status === 'unknown' || input.status === 'upgrade_required' || input.status === 'synced' || input.status === 'unreachable', 'Invalid connection device status.');
    return { peer_id: hex(input.peer_id), status: input.status };
  });
}
