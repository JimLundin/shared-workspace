/** Native interface v1; see ../PROTOCOL.md. Validate every native reply here. */
export type WebApplication = 'word' | 'excel' | 'powerpoint' | 'vscode' | 'diagrams' | 'website';
export type CodeLanguage = 'plaintext' | 'markdown' | 'javascript' | 'typescript' | 'python' | 'rust' | 'json' | 'html' | 'css';
export type Surface =
  | Readonly<{ type: 'application'; application: WebApplication; url: string }>
  | Readonly<{ type: 'document'; application: WebApplication; url: string }>
  | Readonly<{ type: 'code_document'; language: CodeLanguage; text: string }>;
export type SourceReference = Readonly<{ workspace_id: string; resource_id: string; revision_id: string }>;
export type EvidenceCitation = Readonly<{ source: SourceReference; quote: string }>;
export type EvidenceBasis = Readonly<{ type: 'transcript'; citations: readonly EvidenceCitation[] }> | Readonly<{ type: 'user' }>;
export type ActionOwner = Readonly<{ type: 'unassigned' }> | Readonly<{ type: 'assigned'; name: string; basis: EvidenceBasis }>;
export type ActionDeadline = Readonly<{ type: 'unspecified' }> | Readonly<{ type: 'stated'; text: string; basis: EvidenceBasis }>;
export type MeetingAction = Readonly<{ id: string; text: string; owner: ActionOwner; deadline: ActionDeadline; basis: EvidenceBasis }>;
export type WorkRun = Readonly<{ id: string; originating_device: string; started_at_ms: number }>;
export type MeetingMaterial = Readonly<{ reference: SourceReference; citations: readonly EvidenceCitation[] }>;
export type MeetingFollowUp = Readonly<{ type: 'unsent_text'; material: MeetingMaterial }>;
export type MeetingWorkState =
  | Readonly<{ type: 'requested' }>
  | Readonly<{ type: 'preparing'; run: WorkRun }>
  | Readonly<{ type: 'needs_input'; run: WorkRun; question: string }>
  | Readonly<{ type: 'materials_ready'; run: WorkRun; notes: MeetingMaterial; actions: readonly MeetingAction[]; follow_up: MeetingFollowUp }>;
export type MeetingWork = Readonly<{ type: 'meeting'; outcome: string; sources: readonly SourceReference[]; state: MeetingWorkState }>;
export type Work = MeetingWork;
export type WorkChange =
  | Readonly<{ type: 'revise_request'; title: string; outcome: string; sources: readonly SourceReference[] }>
  | Readonly<{ type: 'begin_preparation'; run_id: string; started_at_ms: number }>
  | Readonly<{ type: 'need_input'; run_id: string; question: string }>
  | Readonly<{ type: 'correct_actions'; actions: readonly MeetingAction[] }>;
export type MeetingMaterialInput = Readonly<{ resource_id: string; title: string; body: string; citations: readonly EvidenceCitation[] }>;

type RevisionIdentity = Readonly<{ id: string; author: string; title: string; parents: readonly string[] }>;
export type NoteRevision = RevisionIdentity & Readonly<{ body: string }>;
export type SurfaceRevision = RevisionIdentity & Readonly<{ surface: Surface }>;
export type WorkRevision = RevisionIdentity & Readonly<{ work: MeetingWork }>;
export type Revision = NoteRevision | SurfaceRevision | WorkRevision;
export const isWorkRevision = (revision: Revision): revision is WorkRevision => 'work' in revision;
export const isNoteRevision = (revision: Revision): revision is NoteRevision => 'body' in revision;
export const isSurfaceRevision = (revision: Revision): revision is SurfaceRevision => 'surface' in revision;
export type Placement = Readonly<{ resource_id: string; source_workspace_id: string }>;
export type Workspace = Readonly<{ id: string; title: string; placements: readonly Placement[]; members: readonly string[]; heads: readonly string[] }>;
export type Resource = Readonly<{ id: string; workspace_id: string; heads: readonly Revision[]; history: readonly Revision[] }>;
export type PeerState = Readonly<{ type: 'never_synced' }> | Readonly<{ type: 'syncing' }> | Readonly<{ type: 'synced'; at_ms: number; imported: number }> | Readonly<{ type: 'unreachable'; at_ms: number; detail: string }>;
export type Peer = Readonly<{ id: string; name: string; workspace_ids: readonly string[]; state: PeerState }>;
export type Snapshot = Readonly<{ device_id: string; device_name: string; workspaces: readonly Workspace[]; resources: readonly Resource[]; operation_count: number; network: Readonly<{ type: 'active' | 'paused' | 'closed' }> | Readonly<{ type: 'unavailable'; detail: string }>; peers: readonly Peer[] }>;
type Identified = Readonly<{ operation_id: string; workspace_id: string }>;
export type WorkCommand = Identified & (
  | Readonly<{ type: 'create_work'; resource_id: string; title: string; outcome: string; sources: readonly SourceReference[] }>
  | Readonly<{ type: 'edit_work'; resource_id: string; expected_heads: readonly string[]; change: WorkChange }>
  | Readonly<{ type: 'publish_meeting_materials'; resource_id: string; expected_heads: readonly string[]; run_id: string; notes: MeetingMaterialInput; actions: readonly MeetingAction[]; follow_up: MeetingMaterialInput }>
);
export type Command = WorkCommand | Identified & (
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
  if ('body' in r && !('surface' in r) && !('work' in r)) {
    exactFields(r, ['id', 'author', 'title', 'parents', 'body']);
    return Object.freeze({ ...identity, body: string(r.body) });
  }
  if ('surface' in r && !('body' in r) && !('work' in r)) {
    exactFields(r, ['id', 'author', 'title', 'parents', 'surface']);
    return Object.freeze({ ...identity, surface: decodeSurface(r.surface) });
  }
  if ('work' in r && !('body' in r) && !('surface' in r)) {
    exactFields(r, ['id', 'author', 'title', 'parents', 'work']);
    return Object.freeze({ ...identity, work: decodeWork(r.work) });
  }
  throw new Error('A resource revision must contain exactly one supported payload. Upgrade this client if it cannot read this work.');
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

function workText(value: unknown, maximum = 4096): string {
  const text = string(value);
  if (!text.trim() || byteLength(text) > maximum || text.includes('\0')) throw new Error('The host returned invalid work text.');
  return text;
}
function workId(value: unknown): string {
  const text = string(value);
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(text)) throw new Error('The host returned an invalid work identity.');
  return text;
}
function workTitle(value: unknown, maximum = 512): string {
  const text = workText(value, maximum);
  if (/[\p{Cc}]/u.test(text)) throw new Error('The host returned an invalid work title.');
  return text;
}
function boundedList<T>(value: unknown, maximum: number, decode: (item: unknown) => T, minimum = 0): readonly T[] {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) throw new Error('The host returned an invalid work list.');
  return Object.freeze(value.map(decode));
}
export function decodeSourceReference(value: unknown): SourceReference {
  const source = object(value); exactFields(source, ['workspace_id', 'resource_id', 'revision_id']);
  return Object.freeze({ workspace_id: workId(source.workspace_id), resource_id: workId(source.resource_id), revision_id: workId(source.revision_id) });
}
function sourceReferences(value: unknown): readonly SourceReference[] {
  const refs = boundedList(value, 16, decodeSourceReference);
  if (new Set(refs.map(ref => JSON.stringify(ref))).size !== refs.length) throw new Error('The host returned duplicate transcript references.');
  return refs;
}
export function decodeEvidenceCitation(value: unknown): EvidenceCitation {
  const citation = object(value); exactFields(citation, ['source', 'quote']);
  return Object.freeze({ source: decodeSourceReference(citation.source), quote: workText(citation.quote, 2048) });
}
function evidenceCitations(value: unknown): readonly EvidenceCitation[] { return boundedList(value, 16, decodeEvidenceCitation, 1); }
function evidenceBasis(value: unknown, generated: boolean, literal?: string): EvidenceBasis {
  const basis = object(value);
  switch (basis.type) {
    case 'transcript': {
      exactFields(basis, ['type', 'citations']); const citations = evidenceCitations(basis.citations);
      if (literal !== undefined && !citations.some(citation => citation.quote.includes(literal))) throw new Error('The stated owner or deadline is absent from its evidence.');
      return Object.freeze({ type: 'transcript', citations });
    }
    case 'user':
      exactFields(basis, ['type']); if (generated) throw new Error('Generated work cannot claim a user correction.');
      return Object.freeze({ type: 'user' });
    default: throw new Error('The host returned an unknown evidence basis.');
  }
}
export function decodeMeetingActions(value: unknown, generated = false): readonly MeetingAction[] {
  const actions = boundedList(value, 64, raw => {
    const action = object(raw); exactFields(action, ['id', 'text', 'owner', 'deadline', 'basis']);
    const rawOwner = object(action.owner), rawDeadline = object(action.deadline);
    let owner: ActionOwner, deadline: ActionDeadline;
    switch (rawOwner.type) {
      case 'unassigned': exactFields(rawOwner, ['type']); owner = Object.freeze({ type: 'unassigned' }); break;
      case 'assigned': {
        exactFields(rawOwner, ['type', 'name', 'basis']); const name = workTitle(rawOwner.name, 120);
        owner = Object.freeze({ type: 'assigned', name, basis: evidenceBasis(rawOwner.basis, generated, name) }); break;
      }
      default: throw new Error('The host returned an unknown action owner.');
    }
    switch (rawDeadline.type) {
      case 'unspecified': exactFields(rawDeadline, ['type']); deadline = Object.freeze({ type: 'unspecified' }); break;
      case 'stated': {
        exactFields(rawDeadline, ['type', 'text', 'basis']); const text = workText(rawDeadline.text);
        deadline = Object.freeze({ type: 'stated', text, basis: evidenceBasis(rawDeadline.basis, generated, text) }); break;
      }
      default: throw new Error('The host returned an unknown action deadline.');
    }
    return Object.freeze({ id: workId(action.id), text: workText(action.text), owner, deadline, basis: evidenceBasis(action.basis, generated) });
  });
  if (new Set(actions.map(action => action.id)).size !== actions.length) throw new Error('The host returned duplicate action identities.');
  return actions;
}
function workRun(value: unknown): WorkRun {
  const run = object(value); exactFields(run, ['id', 'originating_device', 'started_at_ms']); const device = string(run.originating_device);
  if (!/^[a-f0-9]{64}$/.test(device)) throw new Error('The host returned an invalid run origin.');
  return Object.freeze({ id: workId(run.id), originating_device: device, started_at_ms: number(run.started_at_ms) });
}
function meetingMaterial(value: unknown): MeetingMaterial {
  const material = object(value); exactFields(material, ['reference', 'citations']);
  return Object.freeze({ reference: decodeSourceReference(material.reference), citations: evidenceCitations(material.citations) });
}
export function decodeWork(value: unknown): MeetingWork {
  const work = object(value); exactFields(work, ['type', 'outcome', 'sources', 'state']);
  if (work.type !== 'meeting') throw new Error('Upgrade this client to open this kind of work.');
  const raw = object(work.state); let state: MeetingWorkState;
  switch (raw.type) {
    case 'requested': exactFields(raw, ['type']); state = Object.freeze({ type: 'requested' }); break;
    case 'preparing': exactFields(raw, ['type', 'run']); state = Object.freeze({ type: 'preparing', run: workRun(raw.run) }); break;
    case 'needs_input': exactFields(raw, ['type', 'run', 'question']); state = Object.freeze({ type: 'needs_input', run: workRun(raw.run), question: workText(raw.question) }); break;
    case 'materials_ready': {
      exactFields(raw, ['type', 'run', 'notes', 'actions', 'follow_up']); const follow = object(raw.follow_up); exactFields(follow, ['type', 'material']);
      if (follow.type !== 'unsent_text') throw new Error('The host returned an unsupported follow-up artifact.');
      state = Object.freeze({ type: 'materials_ready', run: workRun(raw.run), notes: meetingMaterial(raw.notes), actions: decodeMeetingActions(raw.actions), follow_up: Object.freeze({ type: 'unsent_text', material: meetingMaterial(follow.material) }) }); break;
    }
    default: throw new Error('Upgrade this client to open this work state.');
  }
  const sources = sourceReferences(work.sources);
  if (state.type !== 'requested' && !sources.length) throw new Error('Preparation requires pinned transcript sources.');
  if (sources.some(source => source.workspace_id !== sources[0]!.workspace_id)) throw new Error('Work sources must share one workspace.');
  if (state.type === 'materials_ready') {
    const refs = [state.notes.reference, state.follow_up.material.reference];
    if (refs.some(ref => ref.workspace_id !== sources[0]!.workspace_id) || refs[0]!.resource_id === refs[1]!.resource_id) throw new Error('Work material references have invalid scope.');
    const evidence = [...state.notes.citations, ...state.follow_up.material.citations];
    for (const action of state.actions) {
      const bases = [action.basis, ...(action.owner.type === 'assigned' ? [action.owner.basis] : []), ...(action.deadline.type === 'stated' ? [action.deadline.basis] : [])];
      for (const basis of bases) if (basis.type === 'transcript') evidence.push(...basis.citations);
    }
    if (evidence.some(citation => !sources.some(source => source.workspace_id === citation.source.workspace_id && source.resource_id === citation.source.resource_id && source.revision_id === citation.source.revision_id))) throw new Error('Work evidence is outside its captured sources.');
  }
  return Object.freeze({ type: 'meeting', outcome: workText(work.outcome), sources, state });
}
export function decodeWorkChange(value: unknown): WorkChange {
  const change = object(value);
  switch (change.type) {
    case 'revise_request': exactFields(change, ['type', 'title', 'outcome', 'sources']); return Object.freeze({ type: 'revise_request', title: workTitle(change.title), outcome: workText(change.outcome), sources: sourceReferences(change.sources) });
    case 'begin_preparation': exactFields(change, ['type', 'run_id', 'started_at_ms']); return Object.freeze({ type: 'begin_preparation', run_id: workId(change.run_id), started_at_ms: number(change.started_at_ms) });
    case 'need_input': exactFields(change, ['type', 'run_id', 'question']); return Object.freeze({ type: 'need_input', run_id: workId(change.run_id), question: workText(change.question) });
    case 'correct_actions': exactFields(change, ['type', 'actions']); return Object.freeze({ type: 'correct_actions', actions: decodeMeetingActions(change.actions) });
    default: throw new Error('The host returned an unknown work change.');
  }
}
export function decodeMeetingMaterialInput(value: unknown): MeetingMaterialInput {
  const material = object(value); exactFields(material, ['resource_id', 'title', 'body', 'citations']);
  return Object.freeze({ resource_id: workId(material.resource_id), title: workTitle(material.title), body: workText(material.body, 128 * 1024), citations: evidenceCitations(material.citations) });
}
