import type { Command } from './protocol.ts';
import { decodeMeetingActions, decodeMeetingMaterialInput, decodeSurface, decodeWork, decodeWorkChange, string } from './protocol.ts';
import { exactRecord, identity } from './meeting-preparation.ts';

function text(value: unknown, maximum: number, title = false): string {
  const result = string(value);
  if (new TextEncoder().encode(result).byteLength > maximum || result.includes('\0') ||
    (title && (!result.trim() || /\p{Cc}/u.test(result)))) throw new Error('The retained input contains invalid text.');
  return result;
}
function heads(value: unknown): readonly string[] {
  if (!Array.isArray(value) || !value.length || value.length > 64) throw new Error('The retained revision address is invalid.');
  const result = value.map(identity);
  if (new Set(result).size !== result.length) throw new Error('The retained revision address has duplicates.');
  return Object.freeze(result);
}

/** Local recovery crosses a typed seam before a retained command may be replayed. */
export function decodeRetainedCommand(value: unknown): Command {
  const base = value as { type?: unknown } | null;
  const kind = base && typeof base === 'object' ? base.type : undefined;
  const decode = (fields: readonly string[]) => {
    const result = exactRecord(value, ['type', 'operation_id', 'workspace_id', ...fields]);
    return { result, operation_id: identity(result.operation_id), workspace_id: identity(result.workspace_id) };
  };
  switch (kind) {
    case 'create_workspace': {
      const { result, ...address } = decode(['title']);
      return Object.freeze({ ...address, type: kind, title: text(result.title, 512, true) });
    }
    case 'create_resource': case 'edit_resource': {
      const { result, ...address } = decode(['resource_id', 'title', 'body', ...(kind === 'edit_resource' ? ['expected_heads'] : [])]);
      const resource_id = identity(result.resource_id), title = text(result.title, 512, true), body = text(result.body, 128 * 1024);
      return kind === 'create_resource' ? Object.freeze({ ...address, type: kind, resource_id, title, body }) :
        Object.freeze({ ...address, type: kind, resource_id, expected_heads: heads(result.expected_heads), title, body });
    }
    case 'create_surface': case 'edit_surface': {
      const { result, ...address } = decode(['resource_id', 'title', 'surface', ...(kind === 'edit_surface' ? ['expected_heads'] : [])]);
      const resource_id = identity(result.resource_id), title = text(result.title, 512, true), surface = decodeSurface(result.surface);
      return kind === 'create_surface' ? Object.freeze({ ...address, type: kind, resource_id, title, surface }) :
        Object.freeze({ ...address, type: kind, resource_id, expected_heads: heads(result.expected_heads), title, surface });
    }
    case 'add_placement': case 'remove_placement': {
      const { result, ...address } = decode(['resource_id', 'source_workspace_id']);
      return Object.freeze({ ...address, type: kind, resource_id: identity(result.resource_id), source_workspace_id: identity(result.source_workspace_id) });
    }
    case 'create_work': {
      const { result, ...address } = decode(['resource_id', 'title', 'outcome', 'sources']);
      const work = decodeWork({ type: 'meeting', outcome: result.outcome, sources: result.sources, state: { type: 'requested' } });
      return Object.freeze({ ...address, type: kind, resource_id: identity(result.resource_id), title: text(result.title, 512, true), outcome: work.outcome, sources: work.sources });
    }
    case 'edit_work': {
      const { result, ...address } = decode(['resource_id', 'expected_heads', 'change']);
      return Object.freeze({ ...address, type: kind, resource_id: identity(result.resource_id), expected_heads: heads(result.expected_heads), change: decodeWorkChange(result.change) });
    }
    case 'publish_meeting_materials': {
      const { result, ...address } = decode(['resource_id', 'expected_heads', 'run_id', 'notes', 'actions', 'follow_up']);
      return Object.freeze({ ...address, type: kind, resource_id: identity(result.resource_id), expected_heads: heads(result.expected_heads), run_id: identity(result.run_id),
        notes: decodeMeetingMaterialInput(result.notes), actions: decodeMeetingActions(result.actions), follow_up: decodeMeetingMaterialInput(result.follow_up) });
    }
    default: throw new Error('This retained command needs a newer application version.');
  }
}
