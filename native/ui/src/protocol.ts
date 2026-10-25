/** Native interface v1; see ../PROTOCOL.md. Validate every native reply here. */
export type WebApplication = 'word' | 'excel' | 'powerpoint' | 'vscode' | 'diagrams' | 'website';
export type CodeLanguage = 'plaintext' | 'markdown' | 'javascript' | 'typescript' | 'python' | 'rust' | 'json' | 'html' | 'css';
export type Surface =
  | Readonly<{ type: 'application'; application: WebApplication; url: string }>
  | Readonly<{ type: 'document'; application: WebApplication; url: string }>
  | Readonly<{ type: 'code_document'; language: CodeLanguage; text: string }>;
type RevisionIdentity = Readonly<{ id: string; author: string; title: string; parents: readonly string[] }>;
export type NoteRevision = RevisionIdentity & Readonly<{ body: string }>;
export type SurfaceRevision = RevisionIdentity & Readonly<{ surface: Surface }>;
export type Revision = NoteRevision | SurfaceRevision;
export const isNoteRevision = (revision: Revision): revision is NoteRevision => 'body' in revision;
export const isSurfaceRevision = (revision: Revision): revision is SurfaceRevision => 'surface' in revision;
export type Placement = Readonly<{ resource_id: string; source_workspace_id: string }>;
export type Workspace = Readonly<{ id: string; title: string; placements: readonly Placement[]; members: readonly string[]; heads: readonly string[] }>;
export type Resource = Readonly<{ id: string; workspace_id: string; heads: readonly Revision[]; history: readonly Revision[] }>;
export type PeerState = Readonly<{ type: 'never_synced' }> | Readonly<{ type: 'syncing' }> | Readonly<{ type: 'synced'; at_ms: number; imported: number }> | Readonly<{ type: 'unreachable'; at_ms: number; detail: string }>;
export type Peer = Readonly<{ id: string; name: string; workspace_ids: readonly string[]; state: PeerState }>;
export type Snapshot = Readonly<{ device_id: string; device_name: string; workspaces: readonly Workspace[]; resources: readonly Resource[]; operation_count: number; network: Readonly<{ type: 'active' | 'paused' | 'closed' }> | Readonly<{ type: 'unavailable'; detail: string }>; peers: readonly Peer[] }>;
type Identified = Readonly<{ operation_id: string; workspace_id: string }>;
export type Command = Identified & (
  | Readonly<{ type: 'create_workspace'; title: string }>
  | Readonly<{ type: 'create_resource'; resource_id: string; title: string; body: string }>
  | Readonly<{ type: 'edit_resource'; resource_id: string; expected_heads: readonly string[]; title: string; body: string }>
  | Readonly<{ type: 'create_surface'; resource_id: string; title: string; surface: Surface }>
  | Readonly<{ type: 'edit_surface'; resource_id: string; expected_heads: readonly string[]; title: string; surface: Surface }>
  | Readonly<{ type: 'add_placement' | 'remove_placement'; resource_id: string; source_workspace_id: string }>
);
export type Receipt = Readonly<{ type: 'rejected'; operation_id: string; workspace_id: string; message: string }> | Readonly<{ type: 'applied'; operation_id: string; workspace_id: string; replayed: boolean }> | Readonly<{ type: 'conflict'; resource_id: string; expected_heads: readonly string[]; actual_heads: readonly string[] }>;
export type Invite = Readonly<{ ticket: string; expires_at_ms: number }>;
export type SyncReport = Readonly<{ peers: readonly Peer[] }>;
export type JoinStage = 'checking_invitation' | 'connecting_relay' | 'contacting_device' | 'saving_workspace';

export function decodeJoinStage(value: unknown): JoinStage {
  switch (value) {
    case 'checking_invitation': case 'connecting_relay': case 'contacting_device': case 'saving_workspace': return value;
    default: throw new Error('The host returned an unknown joining stage.');
  }
}

export function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('The native host returned an invalid record.');
  return value as Record<string, unknown>;
}
export function string(value: unknown): string { if (typeof value !== 'string') throw new Error('The native host returned invalid text.'); return value; }
export function number(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('The native host returned an invalid number.'); return value; }
function boolean(value: unknown): boolean { if (typeof value !== 'boolean') throw new Error('The native host returned an invalid flag.'); return value; }
function list<T>(value: unknown, decode: (item: unknown) => T): readonly T[] { if (!Array.isArray(value)) throw new Error('The native host returned an invalid list.'); return Object.freeze(value.map(decode)); }
const strings = (value: unknown) => list(value, string);
const byteLength = (value: string): number => new TextEncoder().encode(value).byteLength;
function exactFields(value: Record<string, unknown>, fields: readonly string[]): void {
  if (Object.keys(value).some(key => !fields.includes(key))) throw new Error('The host returned an unknown resource field.');
}
export function validateSurfaceUrl(value: unknown): string {
  const text = string(value);
  if (byteLength(text) > 8192 || /[\s\u0000-\u001f\u007f-\u009f\\]/u.test(text)) throw new Error('Use an HTTPS application or document URL without spaces.');
  let url: URL;
  try { url = new URL(text); } catch { throw new Error('Use a complete HTTPS application or document URL.'); }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) throw new Error('Application URLs must use HTTPS without embedded credentials.');
  return text;
}
export function decodeSurface(value: unknown): Surface {
  const surface = object(value);
  switch (surface.type) {
    case 'application': case 'document': {
      exactFields(surface, ['type', 'application', 'url']);
      const application = surface.application;
      switch (application) {
        case 'word': case 'excel': case 'powerpoint': case 'vscode': case 'diagrams': case 'website':
          return Object.freeze({ type: surface.type, application, url: validateSurfaceUrl(surface.url) });
        default: throw new Error('The host returned an unknown web application.');
      }
    }
    case 'code_document': {
      exactFields(surface, ['type', 'language', 'text']);
      const language = surface.language, text = string(surface.text);
      if (byteLength(text) > 128 * 1024 || text.includes('\0')) throw new Error('The code document is too large or contains invalid text.');
      switch (language) {
        case 'plaintext': case 'markdown': case 'javascript': case 'typescript': case 'python': case 'rust': case 'json': case 'html': case 'css':
          return Object.freeze({ type: 'code_document', language, text });
        default: throw new Error('The host returned an unknown code language.');
      }
    }
    default: throw new Error('The host returned an unknown application surface.');
  }
}
function revision(value: unknown): Revision {
  const r = object(value);
  const identity = { id: string(r.id), author: string(r.author), title: string(r.title), parents: strings(r.parents) };
  if ('body' in r && !('surface' in r)) {
    exactFields(r, ['id', 'author', 'title', 'parents', 'body']);
    return Object.freeze({ ...identity, body: string(r.body) });
  }
  if ('surface' in r && !('body' in r)) {
    exactFields(r, ['id', 'author', 'title', 'parents', 'surface']);
    return Object.freeze({ ...identity, surface: decodeSurface(r.surface) });
  }
  throw new Error('A resource revision must contain exactly one note or application surface.');
}
function workspace(value: unknown): Workspace { const w = object(value); return Object.freeze({ id: string(w.id), title: string(w.title), members: strings(w.members), heads: strings(w.heads), placements: list(w.placements, value => { const p = object(value); return Object.freeze({ resource_id: string(p.resource_id), source_workspace_id: string(p.source_workspace_id) }); }) }); }
function resource(value: unknown): Resource { const r = object(value); return Object.freeze({ id: string(r.id), workspace_id: string(r.workspace_id), heads: list(r.heads, revision), history: list(r.history, revision) }); }
function peer(value: unknown): Peer {
  const p = object(value), s = object(p.state); let state: PeerState;
  switch (s.type) {
    case 'never_synced': case 'syncing': state = Object.freeze({ type: s.type }); break;
    case 'synced': state = Object.freeze({ type: s.type, at_ms: number(s.at_ms), imported: number(s.imported) }); break;
    case 'unreachable': state = Object.freeze({ type: s.type, at_ms: number(s.at_ms), detail: string(s.detail) }); break;
    default: throw new Error('The native host returned an unknown peer state.');
  }
  return Object.freeze({ id: string(p.id), name: string(p.name), workspace_ids: strings(p.workspace_ids), state });
}
export function decodeSnapshot(value: unknown): Snapshot {
  const s = object(value), network = object(s.network);
  if (network.type !== 'active' && network.type !== 'paused' && network.type !== 'closed' && network.type !== 'unavailable') throw new Error('The native host returned an unknown network state.');
  return Object.freeze({ device_id: string(s.device_id), device_name: string(s.device_name), operation_count: number(s.operation_count), workspaces: list(s.workspaces, workspace), resources: list(s.resources, resource), peers: list(s.peers, peer), network: network.type === 'unavailable' ? Object.freeze({ type: network.type, detail: string(network.detail) }) : Object.freeze({ type: network.type }) });
}
export function decodeReceipt(value: unknown): Receipt {
  const r = object(value);
  switch (r.type) {
    case 'rejected': return Object.freeze({ type: r.type, operation_id: string(r.operation_id), workspace_id: string(r.workspace_id), message: string(r.message) });
    case 'applied': return Object.freeze({ type: r.type, operation_id: string(r.operation_id), workspace_id: string(r.workspace_id), replayed: boolean(r.replayed) });
    case 'conflict': return Object.freeze({ type: r.type, resource_id: string(r.resource_id), expected_heads: strings(r.expected_heads), actual_heads: strings(r.actual_heads) });
    default: throw new Error('The native host returned an unknown receipt.');
  }
}
export function decodeInvite(value: unknown): Invite { const i = object(value); return Object.freeze({ ticket: string(i.ticket), expires_at_ms: number(i.expires_at_ms) }); }
export function decodeSync(value: unknown): SyncReport { return Object.freeze({ peers: list(object(value).peers, peer) }); }
