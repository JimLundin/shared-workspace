import { bytes, fields, requireThat } from './types.ts';

export const MAX_LOCAL_DRAFT_BYTES = 1024 * 1024;
export const MAX_LOCAL_DRAFT_TOTAL_BYTES = 4 * 1024 * 1024;
export const MAX_LOCAL_DRAFTS = 64;
export const MAX_LOCAL_DRAFT_ROWS = 4096;

/** Local input only. The owning typed decoder validates payload semantics on read. */
export type LocalDraft = Readonly<{
  id: string;
  kind: 'editor' | 'capture' | 'meeting';
  payload: string;
  generation: number;
}>;
export type LocalDraftRemoval = Readonly<{ id: string; generation: number }>;
export type LocalDraftJournal = Readonly<{
  drafts: readonly LocalDraft[];
  removed: readonly LocalDraftRemoval[];
}>;
export type LocalDraftReceipt =
  | Readonly<{ type: 'retained' | 'removed'; id: string; generation: number; replayed: boolean }>
  | Readonly<{ type: 'stale'; id: string; generation: number }>;
export type LocalDraftRecord =
  | (LocalDraft & Readonly<{ type: 'retained' }>)
  | (LocalDraftRemoval & Readonly<{ type: 'removed' }>);
export type LocalDraftState = Readonly<{
  records: readonly LocalDraftRecord[];
  active: number;
  bytes: number;
}>;
export type LocalDraftPlan =
  | Readonly<{ type: 'unchanged'; receipt: LocalDraftReceipt }>
  | Readonly<{ type: 'write'; record: LocalDraftRecord; state: LocalDraftState; receipt: LocalDraftReceipt }>;

function identity(value: unknown): string {
  requireThat(typeof value === 'string' && /^[a-zA-Z0-9_.:-]{1,128}$/.test(value), 'Invalid local input identity.');
  return value;
}

function generation(value: unknown): number {
  requireThat(typeof value === 'number' && Number.isSafeInteger(value) && value >= 0, 'Invalid local input generation.');
  return value;
}

export function decodeLocalDraft(value: unknown): LocalDraft {
  const input = fields(value, ['id', 'kind', 'payload', 'generation']);
  const id = identity(input.id), version = generation(input.generation), kind = input.kind;
  requireThat(kind === 'editor' || kind === 'capture' || kind === 'meeting', 'Invalid local input kind.');
  const payload = input.payload;
  requireThat(typeof payload === 'string' && payload.length <= MAX_LOCAL_DRAFT_BYTES
    && bytes(payload) <= MAX_LOCAL_DRAFT_BYTES && !/[\uD800-\uDFFF]/u.test(payload), 'Local input exceeds its text limit or contains invalid text.');
  let document: unknown;
  try { document = JSON.parse(payload); }
  catch { throw new Error('Local input payload must be a JSON object.'); }
  requireThat(document !== null && typeof document === 'object' && !Array.isArray(document), 'Local input payload must be a JSON object.');
  return Object.freeze({ id, kind, payload, generation: version });
}

export function decodeLocalDraftRemoval(value: unknown): LocalDraftRemoval {
  const input = fields(value, ['id', 'generation']);
  return Object.freeze({ id: identity(input.id), generation: generation(input.generation) });
}

export function decodeLocalDraftReceipt(value: unknown): LocalDraftReceipt {
  const input = fields(value, typeof value === 'object' && value !== null && 'type' in value && value.type === 'stale'
    ? ['type', 'id', 'generation'] : ['type', 'id', 'generation', 'replayed']);
  const id = identity(input.id), version = generation(input.generation);
  if (input.type === 'stale') return Object.freeze({ type: 'stale', id, generation: version });
  requireThat((input.type === 'retained' || input.type === 'removed') && typeof input.replayed === 'boolean', 'Invalid local input receipt.');
  return Object.freeze({ type: input.type, id, generation: version, replayed: input.replayed });
}

/** Decode SQL rows without exposing tombstones as recoverable input. */
export function restoreLocalDrafts(rows: readonly unknown[]): LocalDraftState {
  requireThat(rows.length <= MAX_LOCAL_DRAFT_ROWS, 'Local input history exceeds its record limit.');
  const ids = new Set<string>();
  let active = 0, totalBytes = 0;
  const records = rows.map((value): LocalDraftRecord => {
    const row = fields(value, ['id', 'generation', 'kind', 'payload']);
    const id = identity(row.id), version = generation(row.generation);
    requireThat(!ids.has(id), 'Local input history contains duplicate identities.');
    ids.add(id);
    if (row.kind === null && row.payload === null) return Object.freeze({ type: 'removed', id, generation: version });
    const draft = decodeLocalDraft(row);
    active++; totalBytes += bytes(draft.payload);
    requireThat(active <= MAX_LOCAL_DRAFTS && totalBytes <= MAX_LOCAL_DRAFT_TOTAL_BYTES, 'Retained local input exceeds its storage limit.');
    return Object.freeze({ type: 'retained', ...draft });
  });
  return Object.freeze({ records: Object.freeze(records), active, bytes: totalBytes });
}

export function listLocalDrafts(state: LocalDraftState): LocalDraftJournal {
  const drafts: LocalDraft[] = [], removed: LocalDraftRemoval[] = [];
  for (const record of state.records) {
    if (record.type === 'retained') {
      drafts.push(Object.freeze({ id: record.id, kind: record.kind, payload: record.payload, generation: record.generation }));
    } else removed.push(Object.freeze({ id: record.id, generation: record.generation }));
  }
  drafts.sort((a, b) => a.id.localeCompare(b.id));
  removed.sort((a, b) => a.id.localeCompare(b.id));
  return Object.freeze({ drafts: Object.freeze(drafts), removed: Object.freeze(removed) });
}

export function decodeLocalDraftJournal(value: unknown): LocalDraftJournal {
  const input = fields(value, ['drafts', 'removed']);
  requireThat(Array.isArray(input.drafts) && Array.isArray(input.removed)
    && input.drafts.length <= MAX_LOCAL_DRAFTS && input.drafts.length + input.removed.length <= MAX_LOCAL_DRAFT_ROWS,
  'Invalid local input journal.');
  const rows: unknown[] = input.drafts.map(decodeLocalDraft);
  for (const value of input.removed) rows.push({ ...decodeLocalDraftRemoval(value), kind: null, payload: null });
  return listLocalDrafts(restoreLocalDrafts(rows));
}

function receipt(record: LocalDraftRecord, replayed: boolean): LocalDraftReceipt {
  return Object.freeze({ type: record.type, id: record.id, generation: record.generation, replayed });
}

function changeLocalDraft(state: LocalDraftState, next: LocalDraftRecord): LocalDraftPlan {
  const index = state.records.findIndex(item => item.id === next.id), previous = state.records[index];
  if (previous && next.generation < previous.generation) {
    return Object.freeze({ type: 'unchanged', receipt: Object.freeze({ type: 'stale', id: next.id, generation: previous.generation }) });
  }
  if (previous && next.generation === previous.generation) {
    const same = previous.type === 'removed' && next.type === 'removed'
      || previous.type === 'retained' && next.type === 'retained' && previous.kind === next.kind && previous.payload === next.payload;
    requireThat(same, 'A local input generation cannot identify different content or a different removal.');
    return Object.freeze({ type: 'unchanged', receipt: receipt(previous, true) });
  }
  const active = state.active - (previous?.type === 'retained' ? 1 : 0) + (next.type === 'retained' ? 1 : 0);
  const totalBytes = state.bytes - (previous?.type === 'retained' ? bytes(previous.payload) : 0) + (next.type === 'retained' ? bytes(next.payload) : 0);
  requireThat(previous || state.records.length < MAX_LOCAL_DRAFT_ROWS, 'Local input history limit reached.');
  requireThat(active <= MAX_LOCAL_DRAFTS && totalBytes <= MAX_LOCAL_DRAFT_TOTAL_BYTES, 'Retained local input limit reached.');
  const records = [...state.records];
  if (index < 0) records.push(next); else records[index] = next;
  return Object.freeze({ type: 'write', record: next, state: Object.freeze({ records: Object.freeze(records), active, bytes: totalBytes }), receipt: receipt(next, false) });
}

export function prepareLocalDraft(state: LocalDraftState, value: LocalDraft): LocalDraftPlan {
  return changeLocalDraft(state, Object.freeze({ type: 'retained', ...decodeLocalDraft(value) }));
}

export function prepareLocalDraftRemoval(state: LocalDraftState, value: LocalDraftRemoval): LocalDraftPlan {
  return changeLocalDraft(state, Object.freeze({ type: 'removed', ...decodeLocalDraftRemoval(value) }));
}
