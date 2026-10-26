import type { Host } from './host.ts';
import type { Command, Invite, Receipt, Resource, Snapshot, JoinStage, Surface } from './protocol.ts';
import { isNoteRevision, isSurfaceRevision, decodeSurface } from './protocol.ts';
import { advanceJoinStage, joinFailure } from './pairing.ts';
export type Route = Readonly<{ type: 'home' }> | Readonly<{ type: 'devices' }> | Readonly<{ type: 'workspace'; id: string }>;
export type View = Readonly<{ mode: 'canvas' | 'focus' | 'overview'; focused: string | null; closed: readonly string[]; order: readonly string[] }>;
export type HostState = Readonly<{ type: 'loading' | 'unavailable' }> | Readonly<{ type: 'ready'; snapshot: Snapshot }> | Readonly<{ type: 'failed'; message: string; last: Snapshot | null }>;
export type Editor = Readonly<{draftId:string}> & (Readonly<{ type: 'new'; workspaceId: string; title: string; body: string }> | Readonly<{ type: 'existing'; workspaceId: string; resourceId: string; baseHeads: readonly string[]; title: string; body: string }> | Readonly<{ type:'surface_new'; workspaceId:string; title:string; surface:Surface }> | Readonly<{ type:'surface_existing'; workspaceId:string; resourceId:string; baseHeads:readonly string[]; title:string; surface:Surface }>);
export type Mutation = Readonly<{ type: 'idle' }> | Readonly<{ type: 'rejected'; command: Command; message: string }> | Readonly<{ type: 'sending'; command: Command }> | Readonly<{ type: 'uncertain'; command: Command; message: string }> | Readonly<{ type: 'conflict'; command: Command; receipt: Extract<Receipt, { type: 'conflict' }> }>;
export type Pairing = Readonly<{ type: 'idle' | 'creating' }> | Readonly<{ type: 'joining'; stage: JoinStage | null }> | Readonly<{ type: 'invite'; invite: Invite; workspaceIds: readonly string[] }> | Readonly<{ type: 'failed'; action: 'invite'; message: string }> | Readonly<{ type: 'failed'; action: 'join'; stage: JoinStage | null; message: string }>;
export type State = Readonly<{ host: HostState; route: Route; views: Readonly<Record<string, View>>; editor: Editor | null; mutation: Mutation; pairing: Pairing; syncing: boolean; message: string }>;
export type Change = 'data' | 'ui' | 'operation';
export const emptyView = (): View => ({ mode: 'canvas', focused: null, closed: [], order: [] });
export const snapshotOf = (s: State): Snapshot | null => s.host.type === 'ready' ? s.host.snapshot : s.host.type === 'failed' ? s.host.last : null;
export const sameHeads = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i]);
export const titleOf = (r: Resource): string => r.heads.length === 1 ? r.heads[0]!.title : `${r.heads.length} versions to review`;
export function errorText(error: unknown): string { return error instanceof Error ? error.message : typeof error === 'string' ? error : 'The native host did not return a usable result.'; }
export function decodeEditor(value:unknown):Editor|null {
  if(value===null)return null;
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Invalid retained editor');
  const e=value as Record<string,unknown>;
  const text=(value:unknown):string=>{if(typeof value!=='string'||new TextEncoder().encode(value).length>4_000_000)throw new Error('Invalid retained editor text');return value;};
  const id=(value:unknown):string=>{if(typeof value!=='string'||!/^[a-zA-Z0-9_-]{1,128}$/.test(value))throw new Error('Invalid retained editor identity');return value;};
  const existing=e.type==='existing'||e.type==='surface_existing',surface=e.type==='surface_new'||e.type==='surface_existing';
  if(!['new','existing','surface_new','surface_existing'].includes(String(e.type)))throw new Error('Unknown retained editor');
  const fields=['draftId','type','workspaceId','title',surface?'surface':'body',...(existing?['resourceId','baseHeads']:[])];
  if(Object.keys(e).length!==fields.length||fields.some(field=>!(field in e)))throw new Error('Invalid retained editor fields');
  const base={draftId:id(e.draftId),workspaceId:id(e.workspaceId),title:text(e.title)};
  // Drafts can contain incomplete URLs or oversized code. Validate the shape without
  // imposing publication rules; saving still validates the final external command.
  const draftSurface=():Surface=>{
    if(!e.surface||typeof e.surface!=='object'||Array.isArray(e.surface))throw new Error('Invalid retained surface');
    const raw=e.surface as Record<string,unknown>,code=raw.type==='code_document';
    const content=text(raw[code?'text':'url']);
    const checked=decodeSurface({...raw,...(code?{text:''}:{url:'https://example.invalid/'})});
    return checked.type==='code_document'?Object.freeze({...checked,text:content}):Object.freeze({...checked,url:content});
  };
  if(e.type==='new')return Object.freeze({...base,type:'new',body:text(e.body)});
  if(e.type==='surface_new')return Object.freeze({...base,type:'surface_new',surface:draftSurface()});
  if(!Array.isArray(e.baseHeads)||!e.baseHeads.length||e.baseHeads.length>1024)throw new Error('Invalid retained editor revisions');
  const baseHeads=Object.freeze(e.baseHeads.map(id));if(new Set(baseHeads).size!==baseHeads.length)throw new Error('Duplicate retained editor revision');
  const addressed={...base,resourceId:id(e.resourceId),baseHeads};
  return e.type==='existing'?Object.freeze({...addressed,type:'existing',body:text(e.body)}):Object.freeze({...addressed,type:'surface_existing',surface:draftSurface()});
}
export function splitTranscript(text:string,limit=128*1024):readonly string[] {
  if(!Number.isSafeInteger(limit)||limit<4)throw new Error('A text chunk must accommodate a Unicode code point.');
  const parts:string[]=[];let part='',bytes=0;
  for(const char of text){const point=char.codePointAt(0)!;const size=point<128?1:point<2048?2:point<65536?3:4;if(bytes+size>limit){parts.push(part);part='';bytes=0;}part+=char;bytes+=size;}
  if(part)parts.push(part);return Object.freeze(parts);
}

function commandMatchesEditor(command:Command,editor:Editor):boolean {
  if(command.type==='create_resource'&&editor.type==='new')return command.workspace_id===editor.workspaceId&&command.title===editor.title.trim()&&command.body===editor.body;
  if(command.type==='edit_resource'&&editor.type==='existing')return command.resource_id===editor.resourceId&&command.title===editor.title.trim()&&command.body===editor.body;
  if(command.type==='create_surface'&&editor.type==='surface_new')return command.workspace_id===editor.workspaceId&&command.title===editor.title.trim()&&JSON.stringify(command.surface)===JSON.stringify(editor.surface);
  if(command.type==='edit_surface'&&editor.type==='surface_existing')return command.resource_id===editor.resourceId&&command.title===editor.title.trim()&&JSON.stringify(command.surface)===JSON.stringify(editor.surface);
  return false;
}

/** Owns native effects. Editors keep the head IDs observed when they opened. */
export class WorkspaceSession {
  readonly host: Host;
  private readonly id: () => string;
  private listeners = new Set<(state: State, change: Change) => void>();
  private refreshEpoch = 0;
  private pairingEpoch = 0;
  private disposed = false;
  private retention:((editor:Editor|null,pending:Command|null)=>Promise<void>)|null=null;
  state: State;
  constructor(host: Host, id: () => string = () => crypto.randomUUID()) {
    this.host = host; this.id = id;
    this.state = Object.freeze<State>({ host: { type: host.available ? 'loading' : 'unavailable' }, route: { type: 'home' }, views: {}, editor: null, mutation: { type: 'idle' }, pairing: { type: 'idle' }, syncing: false, message: '' });
  }
  subscribe(listener: (state: State, change: Change) => void): () => void { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  private update(patch: Partial<State>, change: Change = 'ui', notify = true): void { if (this.disposed) return; this.state = Object.freeze({ ...this.state, ...patch }); if (notify) for (const listener of this.listeners) listener(this.state, change); }
  dispose(): void { this.disposed = true; this.refreshEpoch++; this.pairingEpoch++; this.listeners.clear(); }
  snapshot(): Snapshot | null { return snapshotOf(this.state); }
  view(): View { return this.state.route.type === 'workspace' ? this.state.views[this.state.route.id] ?? emptyView() : emptyView(); }
  retainWith(retain:(editor:Editor|null,pending:Command|null)=>Promise<void>):void {this.retention=retain;}
  restoreInput(editor:Editor|null,pending:Command|null):void {if(this.state.editor||this.state.mutation.type!=='idle')return;this.update({editor,mutation:pending?{type:'uncertain',command:pending,message:'An earlier save needs its exact receipt checked.'}:{type:'idle'}});}
  restoreViews(views: Readonly<Record<string, View>>): void { this.update({ views }); }
  navigate(route: Route): void {
    if (route.type === 'workspace' && !this.snapshot()?.workspaces.some(w => w.id === route.id)) { this.update({ message: 'This workspace is unavailable on this device.' }); return; }
    this.update({ route });
  }
  private changeView(patch: Partial<View>): void { const route = this.state.route; if (route.type !== 'workspace') return; this.update({ views: { ...this.state.views, [route.id]: Object.freeze({ ...this.view(), ...patch }) } }); }
  mode(mode: View['mode']): void { const route = this.state.route; const focus = this.view().focused ?? (route.type === 'workspace' ? this.snapshot()?.workspaces.find(w => w.id === route.id)?.placements.find(p => !this.view().closed.includes(p.resource_id) && this.snapshot()?.resources.some(r => r.id === p.resource_id))?.resource_id : null) ?? null; this.changeView({ mode, focused: mode === 'focus' ? focus : this.view().focused }); }
  focus(resourceId: string): void {
    const route = this.state.route, snapshot = this.snapshot();
    if (route.type !== 'workspace' || !snapshot?.workspaces.find(w => w.id === route.id)?.placements.some(p => p.resource_id === resourceId) || !snapshot.resources.some(r => r.id === resourceId)) { this.update({ message: 'The referenced resource is unavailable on this device.' }); return; }
    this.changeView({ focused: resourceId, mode: 'focus', closed: this.view().closed.filter(id => id !== resourceId) });
  }
  close(resourceId: string): void { const view = this.view(); this.changeView({ closed: [...new Set([...view.closed, resourceId])], focused: view.focused === resourceId ? null : view.focused, mode: view.focused === resourceId ? 'canvas' : view.mode }); }
  reopen(): void { this.changeView({ closed: [] }); }
  move(resourceId: string): void { const route = this.state.route; if (route.type !== 'workspace') return; const ids = this.snapshot()?.workspaces.find(w => w.id === route.id)?.placements.map(p => p.resource_id) ?? []; const order = [...this.view().order.filter(id => ids.includes(id)), ...ids.filter(id => !this.view().order.includes(id))]; const index = order.indexOf(resourceId); if (index < 0) return; order.splice(index, 1); order.unshift(resourceId); this.changeView({ order }); }
  newNote(workspaceId: string): void { if (this.state.editor || !this.snapshot()?.workspaces.some(w => w.id === workspaceId)) return; this.update({ editor: { draftId:this.id(), type: 'new', workspaceId, title: '', body: '' } }); }
  edit(resourceId: string, revisionId?: string): void {
    if(this.state.editor)return;
    const resource = this.snapshot()?.resources.find(r => r.id === resourceId); if (!resource) return;
    const revision = revisionId ? resource.heads.find(h => h.id === revisionId) : resource.heads.length === 1 ? resource.heads[0] : undefined;
    if (!revision) { this.update({ message: 'Open the note and choose a version to begin reconciliation.' }); return; }
    if(!isNoteRevision(revision)&&!isSurfaceRevision(revision)){this.update({message:'Open this work to change its outcome or actions.'});return;}
    this.update({ editor: isNoteRevision(revision) ? { draftId:this.id(), type: 'existing', workspaceId: resource.workspace_id, resourceId, baseHeads: resource.heads.map(h => h.id), title: revision.title, body: revision.body } : {draftId:this.id(),type:'surface_existing',workspaceId:resource.workspace_id,resourceId,baseHeads:resource.heads.map(h=>h.id),title:revision.title,surface:revision.surface} });
  }
  updateEditor(field: 'title' | 'body', value: string): void { if (this.state.editor && (field==='title'||this.state.editor.type==='new'||this.state.editor.type==='existing')) this.update({ editor: { ...this.state.editor, [field]: value } }, 'ui', false); }
  newSurface(workspaceId:string,surface:Surface,title='Untitled document'):void { if(!this.state.editor&&this.snapshot()?.workspaces.some(w=>w.id===workspaceId))this.update({editor:{draftId:this.id(),type:'surface_new',workspaceId,title,surface}}); }
  updateSurface(surface:Surface):void {const editor=this.state.editor;if(editor&&(editor.type==='surface_new'||editor.type==='surface_existing'))this.update({editor:{...editor,surface}},'ui',false);}
  async createSurface(workspaceId:string,title:string,surface:Surface):Promise<void> {await this.submit({type:'create_surface',operation_id:this.id(),workspace_id:workspaceId,resource_id:this.id(),title,surface});}
  closeEditor(): void { if (this.state.mutation.type === 'sending' || this.state.mutation.type === 'uncertain') return; this.update({ editor: null, mutation: { type: 'idle' } }); }
  async refresh(): Promise<void> {
    if (!this.host.available || this.disposed) return; const epoch = ++this.refreshEpoch;
    try { const snapshot = await this.host.snapshot(); if (epoch !== this.refreshEpoch || this.disposed) return; this.acceptSnapshot(snapshot); }
    catch (error) { if (epoch === this.refreshEpoch) this.update({ host: { type: 'failed', message: errorText(error), last: this.snapshot() } }, 'data'); }
  }
  private acceptSnapshot(snapshot: Snapshot): void {
    const currentRoute = this.state.route;
    const route: Route = currentRoute.type === 'workspace' && !snapshot.workspaces.some(w => w.id === currentRoute.id) ? { type: 'home' } : currentRoute;
    this.update({ host: { type: 'ready', snapshot }, route }, 'data');
  }
  async submit(command: Command): Promise<Receipt | null> {
    if (!this.host.available || this.disposed || this.state.mutation.type === 'sending') return null;
    if (this.state.mutation.type === 'uncertain' && this.state.mutation.command !== command) { this.update({ message: 'Retry the unconfirmed change before starting another save.' }); return null; }
    this.refreshEpoch++; this.update({ mutation: { type: 'sending', command }, message: 'Waiting for the local save receipt…' }, 'operation');
    let sent=false;
    try {
      await this.retention?.(this.state.editor,command);if(this.disposed)return null;sent=true;
      const receipt = await this.host.command(command); if (this.disposed) return null;
      if (receipt.type === 'applied') {
        if (receipt.operation_id !== command.operation_id || receipt.workspace_id !== command.workspace_id) throw new Error('The native receipt does not identify this change.');
        const editor = this.state.editor;
        const sameEditor = editor && commandMatchesEditor(command,editor);
        this.update({ mutation: { type: 'idle' }, editor: sameEditor ? null : editor, message: command.type === 'remove_placement' ? 'Reference removed. The note and its history remain.' : 'Saved here.' }, 'operation');
        await this.refresh();
        if(command.type==='create_surface'&&this.state.route.type==='workspace'&&this.state.route.id===command.workspace_id)this.focus(command.resource_id);
        if (command.type === 'create_workspace' && this.snapshot()?.workspaces.some(w => w.id === command.workspace_id)) this.navigate({ type: 'workspace', id: command.workspace_id });
      } else if (receipt.type === 'rejected') {
        if (receipt.operation_id !== command.operation_id || receipt.workspace_id !== command.workspace_id) throw new Error('The native rejection does not identify this change.');
        this.update({ mutation: { type: 'rejected', command, message: receipt.message }, message: 'Change not saved: ' + receipt.message }, 'operation');
      } else {
        if ((command.type !== 'edit_resource' && command.type !== 'edit_surface') || receipt.resource_id !== command.resource_id || !sameHeads(receipt.expected_heads, command.expected_heads)) throw new Error('The native conflict does not identify this edit.');
        this.update({ mutation: { type: 'conflict', command, receipt }, message: 'Another revision arrived. Your edit is retained; compare before saving again.' }, 'operation'); await this.refresh();
      }
      // A correlated receipt remains evidence even when a newer UI read is pending.
      return receipt;
    } catch (error) { this.update(sent?{ mutation: { type: 'uncertain', command, message: errorText(error) }, message: 'No save receipt was received. Retry this same change to learn its outcome.' }:{mutation:{type:'idle'},message:'The input could not be retained on this device. No request was sent; keep this window open and try again.'}, 'operation'); return null; }
  }
  async retry(): Promise<void> { const pending = this.state.mutation; if (pending.type === 'uncertain') await this.submit(pending.command); }
  async createWorkspace(title: string): Promise<void> { if (!title.trim()) return; await this.submit({ type: 'create_workspace', operation_id: this.id(), workspace_id: this.id(), title: title.trim() }); }
  async capture(workspaceId: string, body: string): Promise<void> { if (!body.trim()) return; await this.submit({ type: 'create_resource', operation_id: this.id(), workspace_id: workspaceId, resource_id: this.id(), title: body.trim().split('\n')[0]!.slice(0,120), body: body.trim() }); }
  async saveEditor(reviewedHeads?: readonly string[]): Promise<void> {
    const e = this.state.editor; if (!e || !e.title.trim()) return;
    if(e.type==='surface_new'){await this.createSurface(e.workspaceId,e.title.trim(),e.surface);return}
    if(e.type==='surface_existing'){await this.submit({type:'edit_surface',operation_id:this.id(),workspace_id:e.workspaceId,resource_id:e.resourceId,expected_heads:[...(reviewedHeads??e.baseHeads)],title:e.title.trim(),surface:e.surface});return}
    const command: Command = e.type === 'new' ? { type: 'create_resource', operation_id: this.id(), workspace_id: e.workspaceId, resource_id: this.id(), title: e.title.trim(), body: e.body } : { type: 'edit_resource', operation_id: this.id(), workspace_id: e.workspaceId, resource_id: e.resourceId, expected_heads: [...(reviewedHeads ?? e.baseHeads)], title: e.title.trim(), body: e.body };
    await this.submit(command);
  }
  async useVersion(resourceId: string, revisionId: string, reviewedHeads: readonly string[]): Promise<void> {
    const resource = this.snapshot()?.resources.find(r => r.id === resourceId), revision = resource?.heads.find(h => h.id === revisionId); if (!resource || !revision) return;
    if(!isNoteRevision(revision)&&!isSurfaceRevision(revision)){this.update({message:'Review this work through its outcome and action controls.'});return;}
    await this.submit(isNoteRevision(revision)?{ type: 'edit_resource', operation_id: this.id(), workspace_id: resource.workspace_id, resource_id: resource.id, expected_heads: [...reviewedHeads], title: revision.title, body: revision.body }:{type:'edit_surface',operation_id:this.id(),workspace_id:resource.workspace_id,resource_id:resource.id,expected_heads:[...reviewedHeads],title:revision.title,surface:revision.surface});
  }
  async placement(type: 'add_placement' | 'remove_placement', workspaceId: string, resourceId: string, sourceWorkspaceId: string): Promise<void> { await this.submit({ type, operation_id: this.id(), workspace_id: workspaceId, resource_id: resourceId, source_workspace_id: sourceWorkspaceId }); }
  async createInvite(workspaceIds: readonly string[]): Promise<void> {
    if (!workspaceIds.length || this.state.pairing.type === 'creating' || this.state.pairing.type === 'joining') return;
    this.update({ pairing: { type: 'creating' }, message: '' }, 'operation');
    try { const capturedIds = [...workspaceIds]; const invite = await this.host.invite(capturedIds); this.update({ pairing: { type: 'invite', invite, workspaceIds: capturedIds } }, 'operation'); }
    catch { this.update({ pairing: { type: 'failed', action: 'invite', message: 'An invitation could not be created. Check the connection and try again.' } }, 'operation'); }
  }
  clearInvite(): void { if (this.state.pairing.type !== 'joining' && this.state.pairing.type !== 'creating') this.update({ pairing: { type: 'idle' } }); }
  async join(ticket: string): Promise<void> {
    if (this.disposed || !ticket.trim() || this.state.pairing.type === 'joining' || this.state.pairing.type === 'creating') return;
    const beforeWorkspaces = new Set(this.snapshot()?.workspaces.map(w => w.id) ?? []);
    const attempt = ++this.pairingEpoch; this.refreshEpoch++;
    let observedStage: JoinStage | null = null;
    this.update({ pairing: { type: 'joining', stage: null }, message: '' }, 'operation');
    try {
      const snapshot = await this.host.join(ticket.trim(), stage => {
        if (this.disposed || attempt !== this.pairingEpoch || this.state.pairing.type !== 'joining') return;
        observedStage = advanceJoinStage(observedStage, stage);
        this.update({ pairing: { type: 'joining', stage: observedStage } }, 'operation');
      });
      if (this.disposed || attempt !== this.pairingEpoch) return;
      this.refreshEpoch++;
      const received = snapshot.workspaces.filter(w => !beforeWorkspaces.has(w.id));
      this.acceptSnapshot(snapshot);
      if (received.length === 1) this.navigate({ type: 'workspace', id: received[0]!.id });
      this.update({ pairing: { type: 'idle' }, message: 'Connected. Your shared work is saved here.' }, 'operation');
    } catch {
      if (this.disposed || attempt !== this.pairingEpoch) return;
      this.update({ pairing: { type: 'failed', action: 'join', stage: observedStage, message: joinFailure(observedStage) } }, 'operation');
    }
  }
  async sync(): Promise<void> {
    if (this.state.syncing) return; this.update({ syncing: true }, 'operation');
    try { const report = await this.host.sync(); const failed = report.peers.filter(p => p.state.type === 'unreachable').length; this.update({ message: failed ? `${failed} peer exchange(s) did not complete. Local work is retained.` : report.peers.length ? 'Peer exchange completed. Other offline devices may still have newer work.' : 'There are no paired peers to synchronize.' }, 'operation'); await this.refresh(); }
    catch (error) { this.update({ message: 'Synchronization did not complete: ' + errorText(error) }, 'operation'); }
    finally { this.update({ syncing: false }, 'operation'); }
  }
}
