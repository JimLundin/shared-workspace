import type { Snapshot } from './protocol.ts';
import type { Route } from './session.ts';
import { titleOf } from './session.ts';
export type Address = Readonly<{ workspaceId: string | null; workspaceTitle: string; resourceId: string | null; resourceTitle: string | null; heads: readonly string[] }>;
export type CommandDraft = Readonly<{ text: string; address: Address; source: 'typed' | 'voice' }>;
export type Intent = Readonly<{ type: 'navigate'; route: Route }> | Readonly<{ type: 'open'; workspaceId: string; resourceId: string }> | Readonly<{ type: 'new_note'; workspaceId: string }> | Readonly<{ type: 'capture'; workspaceId: string; body: string }> | Readonly<{ type: 'create_workspace'; title: string }> | Readonly<{ type: 'close'; resourceId: string }> | Readonly<{ type: 'sync' }>;
export type Plan = Readonly<{ type: 'empty' }> | Readonly<{ type: 'unavailable'; reason: string }> | Readonly<{ type: 'ready'; label: string; intent: Intent }>;
export function captureAddress(snapshot: Snapshot, route: Route, focused: string | null): Address {
  const workspace = route.type === 'workspace' ? snapshot.workspaces.find(w => w.id === route.id) : undefined;
  const resource = focused ? snapshot.resources.find(r => r.id === focused) : undefined;
  return Object.freeze({ workspaceId: workspace?.id ?? null, workspaceTitle: workspace?.title ?? (route.type === 'devices' ? 'Devices' : 'Home'), resourceId: resource?.id ?? null, resourceTitle: resource ? titleOf(resource) : null, heads: resource?.heads.map(h => h.id) ?? [] });
}
export function commandPlan(snapshot: Snapshot, route: Route, draft: CommandDraft | null): Plan {
  if (!draft?.text.trim()) return { type: 'empty' };
  const unavailable = (reason: string): Plan => ({ type: 'unavailable', reason });
  const ready = (label: string, intent: Intent): Plan => ({ type: 'ready', label, intent });
  const currentId = route.type === 'workspace' ? route.id : null;
  if (draft.address.workspaceId !== currentId || (currentId === null && draft.address.workspaceTitle !== (route.type === 'devices' ? 'Devices' : 'Home'))) return unavailable('This command belongs to its captured context. Return there or start a new command.');
  const text = draft.text.trim(), normalized = text.toLocaleLowerCase().replace(/[.!?]+$/, '').trim();
  if (/^(?:open |go |go to )?home$/.test(normalized)) return ready('Open Home', { type: 'navigate', route: { type: 'home' } });
  if (/^(?:open |show )?(?:devices|connections)$/.test(normalized)) return ready('Open devices', { type: 'navigate', route: { type: 'devices' } });
  if (/^(?:sync|synchronize)(?: my devices)?$/.test(normalized)) return ready('Attempt exchange with paired peers', { type: 'sync' });
  const create = /^(?:create|make) (?:a )?workspace(?: called| named)?\s+(.+)$/i.exec(text);
  if (create?.[1]?.trim()) return ready(`Create workspace “${create[1].trim()}”`, { type: 'create_workspace', title: create[1].trim() });
  if (/^(?:create |open |a )?new note$/.test(normalized)) return currentId ? ready('Open a note editor in '+draft.address.workspaceTitle, { type: 'new_note', workspaceId: currentId }) : unavailable('Choose a workspace before opening a note editor.');
  if (/^close (?:this |the )?view$/.test(normalized)) return draft.address.resourceId ? ready('Close '+draft.address.resourceTitle+' on this device', { type: 'close', resourceId: draft.address.resourceId }) : unavailable('No focused resource was captured with this command.');
  const open = /^(?:open|show|go to) (.+)$/.exec(normalized);
  if (open?.[1]) {
    const workspaces = snapshot.workspaces.filter(w => w.title.toLocaleLowerCase() === open[1]);
    if (workspaces.length > 1) return unavailable('Several workspaces have that name. Choose one in navigation.');
    if (workspaces[0]) return ready('Open '+workspaces[0].title, { type: 'navigate', route: { type: 'workspace', id: workspaces[0].id } });
    const resources = snapshot.resources.filter(r => r.heads.length === 1 && r.heads[0]?.title.toLocaleLowerCase() === open[1]);
    if (resources.length > 1) return unavailable('Several notes have that title. Open the intended note from its workspace.');
    const resource = resources[0], workspace = resource ? snapshot.workspaces.find(w => w.id === currentId && w.placements.some(p => p.resource_id === resource.id)) ?? snapshot.workspaces.find(w => w.placements.some(p => p.resource_id === resource.id)) : undefined;
    if (resource && workspace) return ready('Open '+titleOf(resource), { type: 'open', workspaceId: workspace.id, resourceId: resource.id });
    return unavailable('No available workspace or note matches that name.');
  }
  const namedNote = /^note in (.+?):\s*(.+)$/is.exec(text);
  if (namedNote?.[1] && namedNote[2]?.trim()) {
    const workspaces = snapshot.workspaces.filter(w => w.title.toLocaleLowerCase() === namedNote[1]!.trim().toLocaleLowerCase());
    if (workspaces.length !== 1) return unavailable('Name one available workspace, or choose it in navigation.');
    return ready('Save a note in '+workspaces[0]!.title, { type: 'capture', workspaceId: workspaces[0]!.id, body: namedNote[2].trim() });
  }
  const note = /^(?:remember|note|take a note)[: ,]+(.+)$/is.exec(text);
  if (note?.[1]?.trim()) return currentId ? ready('Save a note in '+draft.address.workspaceTitle, { type: 'capture', workspaceId: currentId, body: note[1].trim() }) : unavailable('Choose a workspace, or say “Note in [workspace name]: [your note]”.');
  return unavailable('This host currently supports workspace navigation, notes, and peer exchange. Use one of the phrases shown below.');
}
