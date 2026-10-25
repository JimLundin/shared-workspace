import qrcode from 'qrcode-generator';
import type { Host } from './host.ts';
import type { Resource, Revision, Snapshot } from './protocol.ts';
import { WorkspaceSession, snapshotOf, titleOf, sameHeads } from './session.ts';
import type { Route, State, View } from './session.ts';
import { captureAddress, commandPlan } from './commands.ts';
import type { CommandDraft, Intent } from './commands.ts';
import { VoiceSession } from './voice-session.ts';
import type { VoiceCapability, VoicePort } from './voice-session.ts';
import type { ScannerPort } from './scanner.ts';
import { loadPresentation, savePresentation } from './presentation.ts';

const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]!));
const attr = (value: unknown): string => escape(JSON.stringify(value));
const button = (text: string, action: string, data: Record<string, unknown> = {}, className = '', disabled = false): string => `<button type="button" class="${className}" data-action="${action}" data-args="${attr(data)}" ${disabled ? 'disabled' : ''}>${text}</button>`;
const mic = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M8 21h8"/></svg>';
const shortId = (value: string): string => value.length > 14 ? value.slice(0, 10)+'…' : value;
const time = (ms: number): string => new Date(ms).toLocaleString();
const count = (n: number, singular: string, plural = singular + 's'): string => `${n} ${n === 1 ? singular : plural}`;

export type AppOptions = Readonly<{ host: Host; voice: VoicePort; scanner: ScannerPort; pollMs?: number; id?: () => string; initialInvite?: string; invitationUrl?: (ticket: string) => string }>;
export function mountApp(root: HTMLElement, options: AppOptions): { session: WorkspaceSession; dispose(): void } {
  const session = new WorkspaceSession(options.host, options.id);
  let capability: VoiceCapability = { type: 'unavailable', reason: 'Checking this host’s voice input…' };
  let command: CommandDraft | null = null, commandOpen = false, workspaceTitleDraft = '', joinTicket = options.initialInvite ?? '', scanAvailable = false, scanning = false, scanMessage = '', localMessage = '';
  let scanEpoch = 0;
  let selectedSpaces = new Set<string>(), presentationDevice: string | null = null, deferredRender = false, composing = false, disposed = false;
  let inviteQr: { ticket: string; svg: string | null } | null = null;
  const byId = <T extends HTMLElement>(id: string): T | null => root.querySelector<T>('#'+id);
  const voice = new VoiceSession(options.voice, (text, address) => { command = { text, address, source: 'voice' }; commandOpen = true; const field = byId<HTMLTextAreaElement>('command-text'); if (field) field.value = text; updateCommand(); }, () => render(), options.id);
  const availableSnapshot = (): Snapshot | null => session.snapshot();
  const writing = (): boolean => session.state.mutation.type === 'sending' || session.state.mutation.type === 'uncertain';
  const captureBusy = (): boolean => ['starting','listening','stopping'].includes(voice.state.type);

  function renderCommand(snapshot: Snapshot): string {
    const address = command?.address ?? captureAddress(snapshot, session.state.route, session.view().focused), plan = commandPlan(snapshot, session.state.route, command);
    const isStale = command && ((session.state.route.type === 'workspace' ? session.state.route.id : null) !== address.workspaceId || (address.workspaceId === null && address.workspaceTitle !== (session.state.route.type === 'devices' ? 'Devices' : 'Home')));
    const nativeDetail = capability.type === 'native_speech' ? (capability.on_device ? 'On-device speech is available. Review the transcript before running.' : 'Speech may use Apple’s recognition service for this language.') : capability.type === 'system_dictation' ? 'Windows voice typing enters text here. Windows controls recording.' : capability.type === 'browser_speech' ? 'Your browser’s speech provider may process audio online. Start explicitly, then review the transcript before running.' : capability.reason;
    return `<section class="voice" aria-label="Voice command"><div class="voice-title"><div><span class="eyebrow">Your next step</span><h2>What would you like to do?</h2></div>${button(mic+(capability.type === 'system_dictation' ? 'Open voice typing' : 'Speak a command'),'speak',{},'primary speak',capability.type === 'unavailable' || voice.state.type !== 'idle' || Boolean(isStale))}</div><p class="voice-status">${escape(nativeDetail)}</p><div class="row"><span class="voice-status" role="status">${escape(voice.state.message === 'Voice starts only when you choose it.' && capability.type === 'browser_speech' ? '' : voice.state.message)}</span>${captureBusy() ? button('Stop','stop-voice',{},'small')+button('Cancel capture','cancel-voice',{},'small') : voice.state.type === 'system_dictation' ? button('Finish input','stop-voice',{},'small') : ''}</div><div class="scope"><span>Addressed to ${escape(address.workspaceTitle)}</span>${address.resourceId ? `<span class="pill">${escape(address.resourceTitle)} · ${count(address.heads.length,'observed version')}</span>` : ''}</div>${isStale ? `<div class="notice">Input kept in its original context. ${button('Return to '+escape(address.workspaceTitle),'return-command',{},'small')}</div>` : ''}<div class="row">${button(command?.text ? 'Edit input' : 'Type instead','type-command',{},'quiet small')}${commandOpen || command ? button('New command here','new-command',{},'quiet small') : ''}</div>${commandOpen || command ? `<form id="command-form"><label for="command-text">${command?.source === 'voice' ? 'Spoken transcript · editable' : 'Command text'}</label><textarea id="command-text" maxlength="12000" placeholder="Create workspace [name]" enterkeyhint="done" ${captureBusy() ? 'readonly' : ''}>${escape(command?.text ?? '')}</textarea><div id="command-preview" class="command-preview">${escape(plan.type === 'ready' ? plan.label : plan.type === 'unavailable' ? plan.reason : 'Speak or type a supported command.')}</div><button id="run-command" class="primary" type="submit" ${plan.type !== 'ready' || writing() || captureBusy() ? 'disabled' : ''}>Run command →</button></form>` : ''}<details class="examples"><summary>Commands available here</summary><p>“Create workspace [name]” · “Open [name]” · “Remember [your note]”<br>“Note in [workspace name]: [your note]” · “New note” · “Close this view” · “Synchronize”</p></details></section>`;
  }
  function updateCommand(): void {
    const snapshot = availableSnapshot(); if (!snapshot) return;
    const plan = commandPlan(snapshot, session.state.route, command), preview = byId('command-preview'), run = byId<HTMLButtonElement>('run-command');
    if (preview) preview.textContent = plan.type === 'ready' ? plan.label : plan.type === 'unavailable' ? plan.reason : 'Speak or type a supported command.';
    if (run) run.disabled = plan.type !== 'ready' || writing() || captureBusy();
  }
  function navigation(snapshot: Snapshot, route: Route): string {
    return `<nav class="navigation" aria-label="Workspaces">${button('⌂ &nbsp; Home','navigate',{ type:'home' },route.type === 'home' ? 'active' : '')}<span class="eyebrow">Workspaces</span>${snapshot.workspaces.map(w => button(escape(w.title),'navigate',{ type:'workspace', id:w.id },route.type === 'workspace' && route.id === w.id ? 'active' : '')).join('')}<div class="nav-foot">${button('Devices','navigate',{ type:'devices' },route.type === 'devices' ? 'active' : '')}<p class="local-note">Your work, across<br>your devices.<br><br>Views stay here.<br>Notes are shared.</p></div></nav>`;
  }
  function mutationNotice(state: State): string {
    if (state.mutation.type === 'uncertain') return `<div class="notice"><p>No save receipt was received. The change may already be on disk. Retrying uses the same operation identity.</p><p>${escape(state.mutation.message)}</p>${button('Retry this change','retry',{},'small primary')}</div>`;
    return '';
  }
  function createWorkspaceForm(): string { return `<form id="workspace-form" class="create-form"><label for="workspace-title">Workspace name<input id="workspace-title" name="title" required maxlength="180" placeholder="Name your workspace" value="${escape(workspaceTitleDraft)}" enterkeyhint="done"></label><button type="submit" class="primary" ${writing() ? 'disabled' : ''}>Create workspace</button></form>`; }
  function home(snapshot: Snapshot): string {
    const conflicts = snapshot.resources.filter(r => r.heads.length > 1);
    return `<section class="home"><header class="main-header"><div><span class="eyebrow">Your work</span><h1>Pick up where you left off.</h1><p>Workspaces and material retained on this device.</p></div></header>${renderCommand(snapshot)}${renderEditor(snapshot)}${snapshot.workspaces.length ? `<h2 class="section-title">Your workspaces</h2><div class="home-grid">${snapshot.workspaces.map(w => `<button class="workspace-tile" data-action="navigate" data-args="${attr({ type:'workspace',id:w.id })}"><span class="eyebrow">${w.placements.length} references</span><h2>${escape(w.title)}</h2><p>${count(w.members.length,'participating device')}</p></button>`).join('')}</div><details><summary>Create another workspace</summary>${createWorkspaceForm()}</details>` : `<div class="empty"><h2>A place for your work.</h2><p>Create a workspace here, or pair with a device that already has your work.</p>${createWorkspaceForm()}<div class="row" style="margin-top:16px">${button('Pair with another device','navigate',{ type:'devices' },'small')}</div></div>`}${conflicts.length ? `<h2 class="section-title">Versions need review</h2>${conflicts.map(r => `<div class="notice"><strong>${escape(titleOf(r))}</strong><p>Different edits are retained. Choose a result when you are ready.</p>${button('Review note','open-resource',{ workspaceId:r.workspace_id, resourceId:r.id },'small')}</div>`).join('')}` : ''}</section>`;
  }
  function renderEditor(snapshot: Snapshot): string {
    const e = session.state.editor; if (!e) return '';
    const resource = e.type === 'existing' ? snapshot.resources.find(r => r.id === e.resourceId) : undefined;
    const changed = e.type === 'existing' && resource && !sameHeads(e.baseHeads, resource.heads.map(h => h.id));
    const title = snapshot.workspaces.find(w => w.id === e.workspaceId)?.title ?? 'Original workspace';
    return `<section class="editor" aria-label="Note editor"><div class="row between"><h2>${e.type === 'new' ? 'New note' : 'Edit note'}</h2><span class="pill">${escape(title)}</span></div><p class="context-note">${e.type === 'existing' ? `Editing the same note, based on ${count(e.baseHeads.length,'observed version')}.` : 'This note will be committed to this workspace on this device.'}</p><form id="editor-form"><label for="note-title">Title</label><input id="note-title" name="title" maxlength="180" required value="${escape(e.title)}" ${writing() ? 'readonly' : ''}><label for="note-body">Content</label><textarea id="note-body" name="body" maxlength="1000000" ${writing() ? 'readonly' : ''}>${escape(e.body)}</textarea>${changed && resource ? `<div class="comparison"><h2>Newer material arrived</h2><p class="context-note">Your edit is still above. Compare these retained versions before saving it as the next revision.</p><div class="comparison-grid">${resource.heads.map(h => revisionView(h,snapshot)).join('')}</div>${button('Save my edit as a new revision','resolve-editor',{ heads:resource.heads.map(h => h.id) },'primary small',writing())}</div>` : ''}<div class="row"><button type="submit" class="primary" ${writing() || changed ? 'disabled' : ''}>${session.state.mutation.type === 'sending' ? 'Saving…' : 'Save note'}</button>${button('Discard local edit','discard-editor',{},'small',writing())}</div></form></section>`;
  }
  function revisionView(revision: Revision, snapshot: Snapshot): string {
    const author = revision.author === snapshot.device_id ? 'This device' : snapshot.peers.find(p => p.id === revision.author)?.name ?? shortId(revision.author);
    return `<section class="version"><h3>${escape(revision.title)}</h3><small>${escape(author)} · ${escape(shortId(revision.id))}</small><p>${escape(revision.body)}</p></section>`;
  }
  function card(resourceId: string, sourceWorkspaceId: string, snapshot: Snapshot, focused: boolean): string {
    const r = snapshot.resources.find(r => r.id === resourceId), route = session.state.route;
    if (!r) return `<article class="resource-card"><div class="card-header"><strong>Resource unavailable on this device</strong></div><div class="paper"><p>This reference does not grant access to the original resource.</p></div></article>`;
    const revision = r.heads.length === 1 ? r.heads[0] : undefined;
    return `<article class="resource-card" data-resource="${escape(r.id)}"><header class="card-header"><div><strong>${escape(titleOf(r))}</strong><small>Note · ${count(r.history.length,'retained revision')}</small></div><button type="button" data-action="close-view" data-args="${attr({resourceId:r.id})}" aria-label="Close local view ${escape(titleOf(r))}">×</button></header>${revision ? `<div class="paper"><h2>${escape(revision.title)}</h2><div class="note-body">${escape(revision.body)}</div></div>` : focused ? `<div class="comparison"><h2>Choose how this note continues</h2><p class="context-note">Every version is retained. A choice creates a new revision based on all versions shown here.</p><div class="comparison-grid">${r.heads.map(h => `<div>${revisionView(h,snapshot)}<div class="row" style="margin:10px 0">${button('Use this version','use-version',{resourceId:r.id,revisionId:h.id,heads:r.heads.map(h=>h.id)},'small primary',writing())}${button('Edit from this version','edit',{resourceId:r.id,revisionId:h.id},'small',writing())}</div></div>`).join('')}</div></div>` : `<div class="paper"><h2>${r.heads.length} versions need review</h2><p>Different edits remain available. Open the note to compare them.</p></div>`}<footer class="card-footer"><span class="resource-id">${escape(shortId(r.id))}</span><div class="row">${focused ? '' : button('Open','focus',{resourceId:r.id},'small')}${revision ? button('Edit','edit',{resourceId:r.id},'small',writing()) : ''}</div></footer><details class="card-options"><summary>Organize this note</summary><div class="row">${button('Move to start on this device','move',{resourceId:r.id},'small')}${route.type === 'workspace' ? button('Remove reference','remove-reference',{workspaceId:route.id,resourceId:r.id,sourceWorkspaceId},'small',writing()) : ''}</div><form data-form="add-reference" data-resource="${escape(r.id)}" data-source="${escape(r.workspace_id)}"><label>Reference in<select name="destination" aria-label="Destination workspace for ${escape(titleOf(r))}">${snapshot.workspaces.filter(w=>!w.placements.some(p=>p.resource_id===r.id)).map(w=>`<option value="${escape(w.id)}">${escape(w.title)}</option>`).join('')}</select></label><button type="submit" class="small" ${writing() || snapshot.workspaces.every(w=>w.placements.some(p=>p.resource_id===r.id))?'disabled':''}>Add reference</button></form><p class="local-note">The resource keeps its original access. Removing a reference leaves the note and its history.</p></details>${focused ? `<details class="history"><summary>Retained history · ${r.history.length} revisions</summary>${r.history.map(h=>revisionView(h,snapshot)).join('')}</details>` : ''}</article>`;
  }
  function workspace(snapshot: Snapshot, workspaceId: string): string {
    const workspace = snapshot.workspaces.find(w=>w.id===workspaceId); if (!workspace) return '<div class="notice">This workspace is unavailable.</div>';
    const view = session.view(), placements = [...workspace.placements].sort((a,b)=> { const ai=view.order.indexOf(a.resource_id),bi=view.order.indexOf(b.resource_id);return (ai<0?Infinity:ai)-(bi<0?Infinity:bi); }).filter(p=>!view.closed.includes(p.resource_id));
    const focused = view.mode==='focus' && view.focused && placements.some(p=>p.resource_id===view.focused), visible=focused?placements.filter(p=>p.resource_id===view.focused):placements;
    return `<header class="main-header"><div><h1>${escape(workspace.title)}</h1><p>Material retained on this device · ${count(workspace.members.length,'participating device')}</p></div><div class="modes" aria-label="Workspace view">${(['canvas','focus','overview'] as const).map(mode=>button(mode[0]!.toUpperCase()+mode.slice(1),'mode',{mode},view.mode===mode?'active':'')).join('')}</div></header>${renderCommand(snapshot)}${renderEditor(snapshot)}<div class="work-set"><span>${count(workspace.placements.length,'reference')}</span><div class="row">${button('Add a note','new-note',{workspaceId},'small primary',writing())}${button('Share this workspace','share',{workspaceId},'small')}</div></div>${view.closed.length?`<div class="row reopen"><span class="pill">${count(view.closed.length,'local view')} closed</span>${button('Reopen views','reopen',{},'small')}</div>`:''}${focused?`<div class="card-tabs">${button('← Work list','mode',{mode:'canvas'},'small')}${placements.filter(p=>p.resource_id!==view.focused).map(p=>{const r=snapshot.resources.find(r=>r.id===p.resource_id);return r?button(escape(titleOf(r)),'focus',{resourceId:r.id},'small quiet'):''}).join('')}</div>`:''}<div class="board ${focused?'focus':view.mode==='overview'?'overview':''}">${visible.map(p=>card(p.resource_id,p.source_workspace_id,snapshot,Boolean(focused))).join('')}</div>${!workspace.placements.length?'<div class="empty"><h2>Bring an idea into this workspace.</h2><p>Add a note or speak a command. Your first save is saved on this device.</p></div>':''}<p class="local-note">Focus, order and closed views stay on this device.</p>`;
  }
  function devices(snapshot: Snapshot): string {
    const network = snapshot.network.type==='active'?'Peer connections are active. Peer reachability is shown per exchange.':snapshot.network.type==='unavailable'?'Network unavailable: '+snapshot.network.detail:snapshot.network.type==='paused'?'Network is paused. Local notes remain available.':'Network is closed. Local notes remain available.';
    const pairing=session.state.pairing, expired=pairing.type==='invite'&&Date.now()>=pairing.invite.expires_at_ms;
    if(pairing.type==='invite'&&inviteQr?.ticket!==pairing.invite.ticket){let svg:string|null=null;try{const qr=qrcode(0,'M');qr.addData(options.invitationUrl?.(pairing.invite.ticket) ?? pairing.invite.ticket);qr.make();svg=qr.createSvgTag({cellSize:3,margin:0,scalable:true})}catch{}inviteQr={ticket:pairing.invite.ticket,svg}}
    return `<section class="devices"><header class="main-header"><div><h1>Your devices</h1><p>${escape(snapshot.device_name)} · ${escape(shortId(snapshot.device_id))}</p></div>${button(session.state.syncing?'Exchanging…':'Synchronize','sync',{},'small primary',session.state.syncing)}</header><p class="context-note">${escape(network)}</p>${renderCommand(snapshot)}${snapshot.peers.map(p=>`<article class="device-card"><div class="row between"><h2>${escape(p.name)}</h2><span class="resource-id">${escape(shortId(p.id))}</span></div><p>${p.workspace_ids.map(id=>escape(snapshot.workspaces.find(w=>w.id===id)?.title??'Unavailable workspace')).join(' · ')}</p><p class="${p.state.type==='unreachable'?'unreachable':''}">${p.state.type==='never_synced'?'No completed exchange observed.':p.state.type==='syncing'?'Exchange in progress.':p.state.type==='synced'?`Last exchange: ${escape(time(p.state.at_ms))} · ${count(p.state.imported,'imported operation')}.`:`Exchange failed at ${escape(time(p.state.at_ms))}: ${escape(p.state.detail)}`}</p></article>`).join('')}${!snapshot.peers.length?'<div class="empty"><h2>No devices paired yet.</h2><p>Create an invitation here, or join with a ticket from your other device.</p></div>':''}<h2 class="section-title">Pair another device</h2>${pairing.type==='failed'?`<div class="notice">${escape(pairing.message)}</div>`:''}<div class="pair-grid"><section class="pair-panel"><h2>Share selected workspaces</h2><p>The invitation authorizes another device to join only the workspaces you choose.</p><form id="invite-form">${snapshot.workspaces.map(w=>`<label class="workspace-check"><input type="checkbox" name="workspace" value="${escape(w.id)}" ${selectedSpaces.has(w.id)?'checked':''}>${escape(w.title)}</label>`).join('')}${!snapshot.workspaces.length?'<p>Create a workspace before making an invitation.</p>':''}<button id="create-invite" class="primary" type="submit" ${!selectedSpaces.size||pairing.type==='creating'||pairing.type==='joining'?'disabled':''}>${pairing.type==='creating'?'Creating invitation…':'Create invitation'}</button></form>${pairing.type==='invite'?`<div class="ticket-actions"><p>This invitation shares: ${pairing.workspaceIds.map(id=>escape(snapshot.workspaces.find(w=>w.id===id)?.title??'Original workspace')).join(', ')}.</p><p>${expired?'Invitation expired. Create another to continue.':'Expires '+escape(time(pairing.invite.expires_at_ms))+(options.invitationUrl ? '. Open with the camera on your other device.' : '. Scan with the native app on your other device.')}</p>${!expired&&inviteQr?.svg?`<div class="qr" role="img" aria-label="Pairing invitation QR code">${inviteQr.svg}</div>`:!expired?'<p>The ticket is too large for a QR code. Use copy and paste.</p>':''}<label for="invite-ticket">Pairing ticket</label><textarea id="invite-ticket" class="ticket" readonly spellcheck="false" autocomplete="off">${escape(pairing.invite.ticket)}</textarea><div class="row">${options.invitationUrl ? button('Copy invitation link','copy-link',{},'small',expired) : ''}${button('Copy ticket','copy-ticket',{},'small',expired)}${button('Hide invitation','hide-ticket',{},'small')}</div></div>`:''}</section><section class="pair-panel"><h2>Join from this device</h2><p>Scan or paste an invitation from a device you want to pair. Joining requires a confirmed exchange with the inviting device.</p>${scanAvailable?button(scanning?'Scanning…':'Scan invitation QR','scan',{},'small',scanning||pairing.type==='joining'):''}${scanning?button('Cancel scan','cancel-scan',{},'small'):''}${scanMessage?`<p class="scan-message">${escape(scanMessage)}</p>`:''}<form id="join-form"><label for="join-ticket">Invitation to join</label><textarea id="join-ticket" name="ticket" required spellcheck="false" autocomplete="off" autocapitalize="none" placeholder="Paste a pairing ticket">${escape(joinTicket)}</textarea><button class="primary" type="submit" ${pairing.type==='joining'||pairing.type==='creating'?'disabled':''}>${pairing.type==='joining'?'Joining…':'Join selected workspaces'}</button></form></section></div><p class="local-note">Each device keeps its own work on disk. An exchange acknowledges that peer only; an offline device may have newer material. Pairing tickets are not written to logs.</p></section>`;
  }

  function render(): void {
    if(disposed)return;
    const oldFocus=document.activeElement instanceof HTMLElement&&root.contains(document.activeElement)?document.activeElement:null;
    const focusId=oldFocus?.id,selection=oldFocus instanceof HTMLInputElement||oldFocus instanceof HTMLTextAreaElement?{start:oldFocus.selectionStart,end:oldFocus.selectionEnd}:null;
    const scroll=root.querySelector('.main')?.scrollTop??0,state=session.state,snapshot=snapshotOf(state);
    if(!snapshot){root.innerHTML=`<div class="app-shell"><header class="topbar"><span>Your work, across your devices</span></header><main class="main"><section class="gate"><h1>${state.host.type==='unavailable'?'Open your native workspace':state.host.type==='loading'?'Opening this device’s work…':'The workspace could not be read'}</h1><p>${state.host.type==='unavailable'?'This interface needs the installed native host for storage, pairing and synchronization. No workspace data is loaded in this browser.':state.host.type==='failed'?escape(state.host.message):'Waiting for the local store to return its retained records.'}</p>${state.host.type==='failed'?button('Retry connection','refresh',{},'primary'):''}</section></main></div>`;return}
    root.innerHTML=`<div class="app-shell"><header class="topbar"><span class="row"><span class="dot" aria-hidden="true"></span><span class="desktop-copy">Your work, across your devices</span></span><span class="row"><span class="local-device">${escape(snapshot.device_name)}</span>${button(state.syncing?'Exchanging…':'Sync','sync',{},'quiet',state.syncing)}</span></header><div class="shell-body">${navigation(snapshot,state.route)}<main class="main">${state.host.type==='failed'?`<div class="notice">The last observation remains visible. Refresh failed: ${escape(state.host.message)} ${button('Retry read','refresh',{},'small')}</div>`:''}${snapshot.network.type==='unavailable'&&state.route.type!=='devices'?`<div class="notice">Peer connections are unavailable. Local editing remains available. ${escape(snapshot.network.detail)}</div>`:''}${mutationNotice(state)}<div class="status-message" role="status" aria-live="polite">${escape(localMessage||state.message)}</div>${state.route.type==='home'?home(snapshot):state.route.type==='devices'?devices(snapshot):workspace(snapshot,state.route.id)}</main></div></div>`;
    const main=root.querySelector('.main');if(main)main.scrollTop=scroll;
    if(focusId){const replacement=byId(focusId);replacement?.focus({preventScroll:true});if(selection&&(replacement instanceof HTMLInputElement||replacement instanceof HTMLTextAreaElement)&&selection.start!==null&&selection.end!==null){try{replacement.setSelectionRange(selection.start,selection.end)}catch{}}}
  }
  async function changeRoute(route:Route):Promise<void>{void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})}session.navigate(route);const main=root.querySelector('.main');if(main)main.scrollTop=0}
  async function execute(intent:Intent):Promise<void>{
    switch(intent.type){
      case'navigate':await changeRoute(intent.route);break;
      case'open':await changeRoute({type:'workspace',id:intent.workspaceId});session.focus(intent.resourceId);break;
      case'new_note':session.newNote(intent.workspaceId);break;
      case'capture':await session.capture(intent.workspaceId,intent.body);break;
      case'create_workspace':await session.createWorkspace(intent.title);break;
      case'close':session.close(intent.resourceId);break;
      case'sync':await session.sync();break;
    }
  }
  const onClick=async(event:MouseEvent):Promise<void>=>{
    const target=(event.target as Element).closest<HTMLButtonElement>('button[data-action]');if(!target||target.disabled)return;
    const action=target.dataset.action,args:Record<string,unknown>=JSON.parse(target.dataset.args??'{}');localMessage='';
    switch(action){
      case'navigate':await changeRoute(args.type==='workspace'?{type:'workspace',id:String(args.id)}:{type:args.type==='devices'?'devices':'home'});break;
      case'focus':session.focus(String(args.resourceId));break;
      case'open-resource':await changeRoute({type:'workspace',id:String(args.workspaceId)});session.focus(String(args.resourceId));break;
      case'mode':session.mode(args.mode as View['mode']);break;
      case'close-view':session.close(String(args.resourceId));break;
      case'reopen':session.reopen();break;
      case'move':session.move(String(args.resourceId));break;
      case'new-note':session.newNote(String(args.workspaceId));break;
      case'edit':session.edit(String(args.resourceId),typeof args.revisionId==='string'?args.revisionId:undefined);break;
      case'discard-editor':session.closeEditor();break;
      case'resolve-editor':await session.saveEditor(args.heads as string[]);break;
      case'use-version':await session.useVersion(String(args.resourceId),String(args.revisionId),args.heads as string[]);break;
      case'remove-reference':await session.placement('remove_placement',String(args.workspaceId),String(args.resourceId),String(args.sourceWorkspaceId));break;
      case'retry':await session.retry();break;
      case'refresh':await session.refresh();break;
      case'sync':await session.sync();break;
      case'share':selectedSpaces=new Set([String(args.workspaceId)]);await changeRoute({type:'devices'});break;
      case'hide-ticket':inviteQr=null;session.clearInvite();break;
      case'copy-link':{const pairing=session.state.pairing;if(pairing.type==='invite'&&options.invitationUrl){try{await navigator.clipboard.writeText(options.invitationUrl(pairing.invite.ticket));localMessage='Invitation link copied.'}catch{localMessage='Copy is unavailable. Select and copy the pairing ticket instead.'}render()}break}
      case'copy-ticket':{const field=byId<HTMLTextAreaElement>('invite-ticket');if(field){try{await navigator.clipboard.writeText(field.value);localMessage='Invitation copied.'}catch{field.focus();field.select();localMessage='Select and copy the ticket from the field.'}render()}break}
      case'type-command':commandOpen=true;render();byId('command-text')?.focus();break;
      case'new-command':await voice.cancel();command=null;commandOpen=true;render();byId('command-text')?.focus();break;
      case'return-command':if(command)await changeRoute(command.address.workspaceId?{type:'workspace',id:command.address.workspaceId}:{type:command.address.workspaceTitle==='Devices'?'devices':'home'});break;
      case'speak':{const snapshot=availableSnapshot();if(!snapshot)break;commandOpen=true;if(!command)command={text:'',address:captureAddress(snapshot,session.state.route,session.view().focused),source:'typed'};render();byId('command-text')?.focus();await voice.start(command.address,capability);break}
      case'stop-voice':await voice.stop();break;
      case'cancel-voice':await voice.cancel();break;
      case'scan':if(!scanning){const epoch=++scanEpoch;scanning=true;scanMessage='';render();try{const ticket=await options.scanner.scan();if(epoch===scanEpoch&&!disposed){joinTicket=ticket;scanMessage='Invitation read. Review it, then choose Join.'}}catch{if(epoch===scanEpoch)scanMessage='No invitation was accepted from the camera. You can paste a ticket instead.'}finally{if(epoch===scanEpoch){scanning=false;render()}}}break;
      case'cancel-scan':{const epoch=++scanEpoch;scanning=false;try{await options.scanner.cancel();if(epoch===scanEpoch)scanMessage='Scan cancelled.'}catch{if(epoch===scanEpoch)scanMessage='Camera cancellation was not confirmed.'}render();break;}
    }
  };
  const onInput=(event:Event):void=>{
    const field=event.target;if(!(field instanceof HTMLInputElement||field instanceof HTMLTextAreaElement))return;
    if(field.id==='workspace-title')workspaceTitleDraft=field.value;
    if(field.id==='note-title')session.updateEditor('title',field.value);
    if(field.id==='note-body')session.updateEditor('body',field.value);
    if(field.id==='join-ticket')joinTicket=field.value;
    if(field.id==='command-text'){const snapshot=availableSnapshot();if(snapshot){command={text:field.value,address:command?.address??captureAddress(snapshot,session.state.route,session.view().focused),source:command?.source??'typed'};updateCommand()}}
    if(field instanceof HTMLInputElement&&field.name==='workspace'&&field.type==='checkbox'){if(field.checked)selectedSpaces.add(field.value);else selectedSpaces.delete(field.value);const create=byId<HTMLButtonElement>('create-invite');if(create)create.disabled=!selectedSpaces.size}
  };
  const onSubmit=async(event:SubmitEvent):Promise<void>=>{
    event.preventDefault();const form=event.target;if(!(form instanceof HTMLFormElement))return;localMessage='';
    if(form.id==='workspace-form'){await session.createWorkspace(String(new FormData(form).get('title')??''));if(session.state.mutation.type==='idle')workspaceTitleDraft='';return}
    if(form.id==='editor-form'){await session.saveEditor();return}
    if(form.id==='invite-form'){await session.createInvite([...selectedSpaces]);return}
    if(form.id==='join-form'){await session.join(joinTicket);if(session.state.pairing.type==='idle'){joinTicket='';render()}return}
    if(form.dataset.form==='add-reference'){const destination=new FormData(form).get('destination');if(destination)await session.placement('add_placement',String(destination),form.dataset.resource!,form.dataset.source!);return}
    if(form.id==='command-form'){const snapshot=availableSnapshot();if(!snapshot||captureBusy()||writing())return;const plan=commandPlan(snapshot,session.state.route,command);if(plan.type==='ready'){await voice.cancel();await execute(plan.intent);if((plan.intent.type!=='capture'&&plan.intent.type!=='create_workspace')||session.state.mutation.type==='idle'){command=null;commandOpen=false}render()}}
  };
  const unsubscribe=session.subscribe((state,change)=>{
    const snapshot=snapshotOf(state);
    if(snapshot){if(presentationDevice!==snapshot.device_id){presentationDevice=snapshot.device_id;const saved=loadPresentation(snapshot.device_id);if(saved)session.restoreViews(saved.views);if(options.initialInvite){session.navigate({type:'devices'});return}if(saved){session.navigate(saved.route);return}}savePresentation(snapshot.device_id,{route:state.route,views:state.views})}
    const focused=document.activeElement;
    if(change==='data'&&(composing||focused instanceof HTMLInputElement||focused instanceof HTMLTextAreaElement)){deferredRender=true;return}render();
  });
  const onFocusOut=():void=>{setTimeout(()=>{if(deferredRender&&!composing&&!(document.activeElement instanceof HTMLInputElement)&&!(document.activeElement instanceof HTMLTextAreaElement)){deferredRender=false;render()}},0)};
  const onCompositionStart=():void=>{composing=true},onCompositionEnd=():void=>{composing=false};
  const clickHandler=(e:MouseEvent):void=>{void onClick(e).catch(()=>{localMessage='The requested action did not complete.';render()})};
  const submitHandler=(e:SubmitEvent):void=>{void onSubmit(e).catch(()=>{localMessage='The requested action did not complete.';render()})};
  root.addEventListener('click',clickHandler);root.addEventListener('input',onInput);root.addEventListener('submit',submitHandler);root.addEventListener('focusout',onFocusOut);root.addEventListener('compositionstart',onCompositionStart);root.addEventListener('compositionend',onCompositionEnd);
  const visibility=():void=>{if(document.hidden){void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})}}else void session.refresh()};document.addEventListener('visibilitychange',visibility);
  const poll=setInterval(()=>{if(!document.hidden&&session.state.mutation.type!=='sending'&&!session.state.syncing)void session.refresh()},options.pollMs??5000);
  render();void session.refresh();void options.voice.capability().then(value=>{capability=value;render()}).catch(()=>{});void options.scanner.available().then(value=>{scanAvailable=value;if(session.state.route.type==='devices')render()}).catch(()=>{});
  return {session,dispose(){disposed=true;clearInterval(poll);unsubscribe();session.dispose();void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})};root.removeEventListener('click',clickHandler);root.removeEventListener('input',onInput);root.removeEventListener('submit',submitHandler);root.removeEventListener('focusout',onFocusOut);root.removeEventListener('compositionstart',onCompositionStart);root.removeEventListener('compositionend',onCompositionEnd);document.removeEventListener('visibilitychange',visibility)}};
}
