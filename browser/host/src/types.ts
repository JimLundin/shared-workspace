import { decodeReceipt, object, string, number } from '../../../native/ui/src/protocol.ts';
import type { Command, Receipt, Snapshot, PeerState, Peer, Invite, SyncReport } from '../../../native/ui/src/protocol.ts';
export type { Command, Receipt, Snapshot, PeerState, Peer, Invite, SyncReport };

export const MAX_SCOPE = 32;
export const MAX_OPERATIONS = 4096;
export const MAX_MESSAGE_BYTES = 8 * 1024 * 1024;
export const MAX_TICKET_BYTES = 12 * 1024;
export type DataSnapshot = Omit<Snapshot, 'network' | 'peers'>;
export type SignedOperation = Readonly<{
  version: 1;
  operation: Readonly<{ id: string; workspace_id: string; author: string; parents: readonly string[]; action: unknown }>;
  signature: string;
}>;
export type Contact = Readonly<{ name: string; address: Readonly<{ id: string; addrs: readonly unknown[] }> }>;
export type PeerRecord = Readonly<{ id: string; name: string; address_json: string }>;
export type ImportReceipt = Readonly<{ imported: number; already_present: number }>;
export type Batch = Readonly<{ batch_id: string | null; operations: readonly SignedOperation[]; request?: Command; receipt?: Receipt | ImportReceipt }>;
export type Ticket = Readonly<{ version: 1; inviter: Contact; nonce: string; workspace_ids: readonly string[]; expires_at_ms: number }>;
export type RequestBody =
  | Readonly<{ type: 'pair'; nonce: string }>
  | Readonly<{ type: 'summary'; workspace_ids: readonly string[]; fingerprint: string; contacts: readonly Contact[] }>
  | Readonly<{ type: 'sync'; workspace_ids: readonly string[]; operations: readonly SignedOperation[]; contacts: readonly Contact[] }>;
export type ReplyBody =
  | Readonly<{ type: 'paired' | 'synced'; workspace_ids: readonly string[]; operations: readonly SignedOperation[]; contacts: readonly Contact[] }>
  | Readonly<{ type: 'current'; workspace_ids: readonly string[]; contacts: readonly Contact[] }>
  | Readonly<{ type: 'need_sync' | 'rejected' }>;
export type Envelope<T> = Readonly<{ version: 1; id: string; contact: Contact; body: T }>;
export type HostOptions = Readonly<{ deviceName?: string; databaseName?: string; baseUrl?: string; startPaused?: boolean }>;

export function requireThat(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
export const bytes = (value: string) => new TextEncoder().encode(value).length;
export const json = (value: unknown): string => JSON.stringify(value);
export function parse(value: string): unknown { return JSON.parse(value); }
export function fields(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const result = object(value);
  requireThat(Object.keys(result).every(key => keys.includes(key)) && keys.every(key => key in result), 'Invalid record fields.');
  return result;
}
export function hex(value: unknown): string {
  const result = string(value);
  requireThat(/^[a-f0-9]{64}$/.test(result), 'Invalid device, request, or secret identity.');
  return result;
}
export function deviceName(value: unknown): string {
  const result = string(value);
  requireThat(result.trim() && bytes(result) <= 120 && !/[\p{Cc}\uD800-\uDFFF]/u.test(result), 'Invalid device name.');
  return result;
}
export function scope(value: unknown): readonly string[] {
  requireThat(Array.isArray(value) && value.length > 0 && value.length <= MAX_SCOPE, 'Select between 1 and 32 workspaces.');
  const values = value.map(item => {
    const id = string(item);
    requireThat(/^[a-zA-Z0-9_-]{1,128}$/.test(id), 'Invalid workspace identity.');
    return id;
  });
  requireThat(new Set(values).size === values.length, 'Duplicate workspace selection.');
  return values;
}
export function sameScope(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every(id => right.includes(id));
}
export function signedOperations(value: unknown, selected?: readonly string[]): readonly SignedOperation[] {
  requireThat(Array.isArray(value) && value.length <= MAX_OPERATIONS, 'Too much operation history.');
  return value.map(item => {
    const signed = fields(item, ['version', 'operation', 'signature']);
    requireThat(signed.version === 1, 'Unsupported operation version.');
    const op = fields(signed.operation, ['id', 'workspace_id', 'author', 'parents', 'action']);
    const workspace_id = string(op.workspace_id);
    requireThat(!selected || selected.includes(workspace_id), 'History contains an unselected workspace.');
    requireThat(Array.isArray(op.parents), 'Invalid causal parents.');
    return { version: 1, operation: { id: string(op.id), workspace_id, author: string(op.author), parents: op.parents.map(string), action: op.action }, signature: string(signed.signature) };
  });
}
export function importReceipt(value: unknown): ImportReceipt {
  const result = fields(value, ['imported', 'already_present']);
  return { imported: number(result.imported), already_present: number(result.already_present) };
}
export function batch(value: string): Batch {
  const result = object(parse(value));
  requireThat(result.batch_id === null || typeof result.batch_id === 'string', 'Invalid prepared batch.');
  const receipt = result.receipt == null ? undefined : 'type' in object(result.receipt) ? decodeReceipt(result.receipt) : importReceipt(result.receipt);
  // Commands in this output have already passed the exact native Rust decoder.
  const request = result.request == null ? undefined : result.request as Command;
  return { batch_id: result.batch_id, operations: signedOperations(result.operations), ...(request ? { request } : {}), ...(receipt ? { receipt } : {}) };
}
export function randomHex(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), byte => byte.toString(16).padStart(2, '0')).join('');
}
export async function hashNonce(nonce: string): Promise<string> {
  hex(nonce);
  const raw = Uint8Array.from(nonce.match(/../g)!, byte => Number.parseInt(byte, 16));
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', raw)), byte => byte.toString(16).padStart(2, '0')).join('');
}
export function safeError(error: unknown): string {
  return String(error).replace(/[\p{Cc}]/gu, '').slice(0, 240);
}
