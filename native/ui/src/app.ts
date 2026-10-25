import qrcode from 'qrcode-generator';
import type { Host } from './host.ts';
import type { Revision, Snapshot, Surface, CodeLanguage, WebApplication } from './protocol.ts';
import { WorkspaceSession, snapshotOf, titleOf, sameHeads } from './session.ts';
import type { Route, State } from './session.ts';
import { captureAddress, commandPlan } from './commands.ts';
import type { CommandDraft, Intent } from './commands.ts';
import { VoiceSession } from './voice-session.ts';
import type { VoiceCapability, VoicePort } from './voice-session.ts';
import type { ScannerPort } from './scanner.ts';
import { loadPresentation, savePresentation } from './presentation.ts';
import { joinStageCopy, joinStages } from './pairing.ts';
import { isNoteRevision, isSurfaceRevision, decodeSurface } from './protocol.ts';
import { surfacePresets, surfacePreset, surfaceLabel, surfacePresentation, surfacePreview, createWebSurface } from './surfaces.ts';
import { ConnectionSession, type ConnectionPort } from './connections-ui.ts';
import { mountCodeEditor } from './code-editor.ts';
import { observeAppearance, type Appearance } from './theme.ts';

const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]!));
const attr = (value: unknown): string => escape(JSON.stringify(value));
const button = (text: string, action: string, data: Record<string, unknown> = {}, className = '', disabled = false): string => `<button type="button" class="${className}" data-action="${action}" data-args="${attr(data)}" ${disabled ? 'disabled' : ''}>${text}</button>`;
const mic = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M8 21h8"/></svg>';
const shareIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3m-4 4 4-4 4 4M7 10H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-2"/></svg>';
const keyboardIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h1m4 0h1m4 0h1M6 12h1m4 0h1m4 0h1M7 16h10"/></svg>';
const plusIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
const shortId = (value: string): string => value.length > 14 ? value.slice(0, 10)+'…' : value;
const time = (ms: number): string => new Date(ms).toLocaleString();
const count = (n: number, singular: string, plural = singular + 's'): string => `${n} ${n === 1 ? singular : plural}`;

export type AppOptions = Readonly<{ host: Host; voice: VoicePort; fallbackVoice?: VoicePort; connections?: ConnectionPort; scanner: ScannerPort; openAppOptions?: () => void; openUpdate?: () => void; pollMs?: number; id?: () => string; initialInvite?: string; invitationUrl?: (ticket: string) => string; shareInvitation?: (url: string) => Promise<'shared' | 'cancelled'> }>;
export function mountApp(root: HTMLElement, options: AppOptions): { session: WorkspaceSession; voiceContext(): {workspaceId:string;connectionVersion:string}; hasUnsubmittedInput(): boolean; setUpdateAvailable(available?: boolean): void; dispose(): void } {
  const session = new WorkspaceSession(options.host, options.id);
  // The modal owns its top-layer lifetime; only its contents refresh with state.
  const frame = document.createElement('div'); frame.className = 'workspace-frame';
  const dialog = document.createElement('dialog'); dialog.className = 'workspace-dialog';
  root.replaceChildren(frame, dialog);
  type Panel = 'picker' | 'create' | 'capture' | 'connections' | 'share' | 'join' | 'appearance' | 'add' | 'voice-settings';
  let panel: Panel | null = null, returnFocus: HTMLElement | null = null;
  let returnFocusId = '', returnFocusAction = '', returnFocusArgs = '';
  let editorOpen = true, updateAvailable = false;
  let inputMode: 'voice' | 'typing' = 'voice';
  let selectedVoice=options.voice, voiceMethod:'provider'|'system'='provider';
  let connectionWorkspace:string|null=null, keyDraft='';
  const codeNode=document.createElement('div');codeNode.className='code-editor';
  let codeEditor:ReturnType<typeof mountCodeEditor>|null=null,codeIdentity:string|null=null;
  let captureRecovery=false,captureAsNote=false;
  let captureEditor: {draftId:string;workspaceId:string;resourceId:string|null;type:string;field:'title'|'body'|'code';before:string;start:number;end:number}|null=null;
  let lastEditorField:'title'|'body'|'code'='body';
  let historyOwned = false, historyReturning = false;
  let afterPanelClose:(()=>void)|null=null;
  let pickerScope: readonly string[] | null = null;
  let capturedSubmission: CommandDraft | null = null;
  const listScroll = new Map<string,number>();
  function focusResource(resourceId:string):void {
    const route=session.state.route,main=frame.querySelector('.main');
    if(route.type==='workspace'&&session.view().mode!=='focus')listScroll.set(route.id,main?.scrollTop??0);
    session.focus(resourceId);const current=frame.querySelector('.main');if(current)current.scrollTop=0;byId('reader-title')?.focus({preventScroll:true});
  }
  function openPanel(next: Panel, focusInput = true): void {
    if(historyReturning)afterPanelClose=null;
    if (!panel) {
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      returnFocusId = returnFocus?.id ?? '';
      returnFocusAction = returnFocus?.dataset.action ?? '';
      returnFocusArgs = returnFocus?.dataset.args ?? '';
      if(!historyReturning){history.pushState({ ...history.state, workspaceDialog: true }, ''); historyOwned = true;}
    }
    panel = next; render();
    const first = dialog.querySelector<HTMLElement>(focusInput ? '[autofocus], input:not([type=checkbox]), textarea, .picker-option' : '[data-action=stop-voice]') ?? dialog.querySelector<HTMLElement>('[data-action=close-dialog]');
    first?.focus({ preventScroll: true });
  }
  function closePanel(fromBack = false): void {
    if (!panel) return;
    panel = null; void voice.cancel(); dialog.close();
    if (historyOwned && !fromBack) { historyReturning=true; history.back(); }
    historyOwned = false;
    render();
    const actionMatch = Array.from(frame.querySelectorAll<HTMLElement>('[data-action]')).find(el => el.dataset.action === returnFocusAction && (el.dataset.args ?? '') === returnFocusArgs);
    const target = returnFocus?.isConnected ? returnFocus : (returnFocusId ? byId(returnFocusId) : null) ?? actionMatch ?? byId('workspace-picker');
    target?.focus({ preventScroll: true });
  }

  let lastVoicePhase='idle';
  let capability: VoiceCapability = { type: 'unavailable', reason: 'Checking this host’s voice input…' };
  let command: CommandDraft | null = null, workspaceTitleDraft = '', joinTicket = options.initialInvite ?? '', scanAvailable = false, scanning = false, scanMessage = '', localMessage = '';
  let scanEpoch = 0;
  const disclosures = new Set<string>();
  let sharingInvitation = false;
  let selectedSpaces = new Set<string>(), presentationDevice: string | null = null, deferredRender = false, composing = false, disposed = false;
  let inviteQr: { ticket: string; svg: string | null } | null = null;
  const byId = <T extends HTMLElement>(id: string): T | null => root.querySelector<T>('#'+id);
  const voice = new VoiceSession({capability:()=>selectedVoice.capability(),start:(id,emit,locale)=>selectedVoice.start(id,emit,locale),stop:id=>selectedVoice.stop(id),cancel:id=>selectedVoice.cancel(id)}, (text, address) => { command = { text, address, source: 'voice' }; const field = byId<HTMLTextAreaElement>('command-text'); if (field) field.value = text; const label=root.querySelector('label[for=command-text]');if(label)label.textContent='Spoken transcript · editable'; if(!field)render();updateCommand(); }, () => {const finished=voice.state.type==='idle'&&lastVoicePhase!=='idle';lastVoicePhase=voice.state.type;render();if(finished&&command?.text&&panel==='capture')byId('run-command')?.focus({preventScroll:true})}, options.id);
  const availableSnapshot = (): Snapshot | null => session.snapshot();
  const writing = (): boolean => session.state.mutation.type === 'sending' || session.state.mutation.type === 'uncertain';
  const captureBusy = (): boolean => ['starting','listening','stopping','transcribing'].includes(voice.state.type);

  function editorTarget():typeof captureEditor {
    const e=session.state.editor,route=session.state.route;
    if(!e||!editorOpen||route.type!=='workspace'||route.id!==e.workspaceId)return null;
    const field=lastEditorField==='title'?'title':e.type==='new'||e.type==='existing'?'body':e.surface.type==='code_document'?'code':'title';
    const before=field==='title'?e.title:field==='body'&&(e.type==='new'||e.type==='existing')?e.body:(e.type==='surface_new'||e.type==='surface_existing')&&e.surface.type==='code_document'?e.surface.text:'';
    const input=byId<HTMLInputElement|HTMLTextAreaElement>(field==='title'?'note-title':'note-body');
    const range=field==='code'?codeEditor?.selection():null;
    return {draftId:e.draftId,workspaceId:e.workspaceId,resourceId:e.type==='existing'||e.type==='surface_existing'?e.resourceId:null,type:e.type,field,before,start:range?.from??input?.selectionStart??before.length,end:range?.to??input?.selectionEnd??before.length};
  }
  function currentAddress(snapshot:Snapshot){
    const target=editorTarget(),view=session.view();
    return captureAddress(snapshot,session.state.route,target?target.resourceId:view.mode==='focus'?view.focused:null);
  }
  async function insertDictation(append=false):Promise<void> {
    const target=captureEditor,e=session.state.editor;if(!target||!command?.text)return;
    const id=e&&(e.type==='existing'||e.type==='surface_existing')?e.resourceId:null;
    if(!e||e.draftId!==target.draftId||e.type!==target.type||e.workspaceId!==target.workspaceId||id!==target.resourceId){captureRecovery=true;localMessage='The original editor is no longer open. Your transcript is kept for review.';render();return}
    const current=target.field==='title'?e.title:target.field==='body'&&(e.type==='new'||e.type==='existing')?e.body:(e.type==='surface_new'||e.type==='surface_existing')&&e.surface.type==='code_document'?e.surface.text:'';
    if(!append&&current!==target.before){captureRecovery=true;localMessage='The original draft changed. Your transcript is kept; review the draft before inserting.';render();return}
    const text=append?current+(current&&!/\s$/.test(current)?' ':'')+command.text:current.slice(0,target.start)+command.text+current.slice(target.end);
    if(target.field==='code'&&(e.type==='surface_new'||e.type==='surface_existing')&&e.surface.type==='code_document'){session.updateSurface({...e.surface,text});codeEditor?.setText(text);}
    else if(target.field==='title'||target.field==='body')session.updateEditor(target.field,text);
    command=null;captureEditor=null;captureRecovery=false;captureAsNote=false;editorOpen=true;closePanel();await changeRoute({type:'workspace',id:e.workspaceId});localMessage='Dictation added to your draft.';render();
  }
  function planCapture(snapshot:Snapshot):ReturnType<typeof commandPlan>{
    const plan=commandPlan(snapshot,session.state.route,command);
    if(captureAsNote&&command?.text.trim()&&command.address.workspaceId&&session.state.route.type==='workspace'&&session.state.route.id===command.address.workspaceId)return {type:'ready',label:'Save a note in '+command.address.workspaceTitle,intent:{type:'capture',workspaceId:command.address.workspaceId,body:command.text}};
    return plan;
  }
  function renderCommand(snapshot: Snapshot): string {
    const address = command?.address ?? currentAddress(snapshot), plan = planCapture(snapshot);
    const isStale = command && ((session.state.route.type === 'workspace' ? session.state.route.id : null) !== address.workspaceId);
    const nativeDetail = capability.type === 'provider_transcription' ? `Audio is sent to ${capability.provider_label} for transcription when you stop. Review the words before using them.` : capability.type === 'native_speech' ? (capability.on_device ? 'On-device speech is available.' : 'Speech may use Apple’s recognition service for this language.') : capability.type === 'system_dictation' ? 'Windows controls voice typing and its microphone.' : capability.type === 'browser_speech' ? 'Your browser’s speech provider may process audio online.' : capability.reason;
    const phase=voice.state.type,completed=phase==='idle'&&Boolean(command?.text), showText=inputMode==='typing'||Boolean(command?.text)||capability.type==='system_dictation';
    const heading=phase==='listening'?'Listening':phase==='transcribing'?'Transcribing':phase==='starting'?'Opening the microphone':phase==='stopping'?'Finishing recording':command?.text?'Ready to review':capability.type==='unavailable'?'Set up your voice':'What’s on your mind?';
    const action=captureEditor?'Insert into draft':plan.type==='ready'?(plan.intent.type==='capture'?'Save as note':plan.label):'Use this input';
    return `<section class="voice" aria-label="Voice capture"><p class="capture-address">${escape(address.workspaceTitle)}${address.resourceId ? ` / ${escape(address.resourceTitle)}` : ''}</p>
      ${isStale ? `<div class="notice">This input belongs to ${escape(address.workspaceTitle)}. ${button('Return to '+escape(address.workspaceTitle),'return-command')}</div>` : ''}
      ${inputMode==='voice'?`<div class="voice-stage ${completed?'complete':''}" data-phase="${phase}"><div class="voice-disc" aria-hidden="true">${mic}</div><h3>${heading}</h3><p class="voice-status" role="status">${escape(phase==='idle'&&!command?.text&&voice.state.message==='Voice starts only when you choose it.'?(capability.type==='unavailable'?capability.reason:'Speak naturally. Stop when you’re ready to review.'):voice.state.message)}</p>
      ${!completed?`<div class="capture-actions">${phase==='listening'||phase==='starting'||phase==='stopping'?button('Stop recording','stop-voice',{},'primary'):phase==='transcribing'?'':phase==='system_dictation'?button('Finish input','stop-voice',{},'primary'):button(command?.text?'Record again':'Start speaking','speak',{},'primary',capability.type==='unavailable'||Boolean(isStale))}${captureBusy()?button('Cancel recording','cancel-voice',{},'quiet'):''}</div>`:''}</div>`:''}
      ${showText?`<form id="command-form"><label for="command-text">${command?.source==='voice'?'Spoken transcript · editable':'Your words'}</label><textarea id="command-text" maxlength="12000" placeholder="An idea, a note, or something to open…" enterkeyhint="done" ${captureBusy()||writing()?'readonly':''}>${escape(command?.text??'')}</textarea><div id="command-preview" class="command-preview">${escape(captureEditor?'Insert into the original '+captureEditor.field+' field.':plan.type==='ready'?plan.label:plan.type==='unavailable'?plan.reason:'Capture a thought or name something to open.')}</div><button id="run-command" class="primary" type="submit" ${(!command?.text.trim()||!captureEditor&&plan.type!=='ready')||writing()||captureBusy()?'disabled':''}>${escape(action)}</button></form>`:''}
      ${captureRecovery&&captureEditor?`<div class="notice"><p>Your transcript is retained.</p>${session.state.editor?.draftId===captureEditor.draftId?button('Append to current draft','append-dictation',{},'primary'):''}${button('Keep as a separate note','dictation-note',{},'quiet')}</div>`:''}
      <div class="capture-secondary">${completed?button('Record again','speak',{},'quiet'): ''}${options.connections&&voiceMethod==='provider'?button('OpenAI connection','configure-voice',{},'quiet connection-entry'):''}${inputMode==='voice'?button(keyboardIcon+'<span>Type instead</span>','type-command',{},'quiet'):button(mic+'<span>Use voice</span>','voice-mode',{},'quiet')}${command?.text?button('New capture','new-command',{},'quiet'):''}${options.fallbackVoice?button(voiceMethod==='provider'?'Use device speech':'Use OpenAI transcription','switch-voice',{},'quiet'):''}</div>
      <details class="examples" data-disclosure="voice-help" ${disclosures.has('voice-help')?'open':''}><summary>Voice & available actions</summary><p>Speak a note, or say “Open [name]”, “Create workspace [name]”, “New note”, or “Synchronize”. The action is shown before you confirm it.</p><p>${escape(nativeDetail)}</p></details>
    </section>`;
  }
  function captureDock(): string {
    return `<footer class="capture-dock" aria-label="Quick capture">${button(mic+`<span>${command?.text?'Review capture':'Speak'}</span>`,command?.text?'review-capture':'speak',{},'primary voice-primary'+(command?.text?' has-capture':''))}<button type="button" class="keyboard-fallback" data-action="type-command" aria-label="Type instead">${keyboardIcon}</button></footer>`;
  }
  function updateCommand(): void {
    const snapshot = availableSnapshot(); if (!snapshot) return;
    const plan = planCapture(snapshot), preview = byId('command-preview'), run = byId<HTMLButtonElement>('run-command');
    if (preview) preview.textContent = captureEditor?'Insert into the original '+captureEditor.field+' field.':plan.type === 'ready' ? plan.label : plan.type === 'unavailable' ? plan.reason : 'Speak or type a supported command.';
    if (run) {run.disabled = !command?.text.trim() || (!captureEditor&&plan.type !== 'ready') || writing() || captureBusy();run.textContent=captureEditor?'Insert into draft':plan.type==='ready'?(plan.intent.type==='capture'?'Save as note':plan.label):'Use this input';}
  }
  function settingsRow(label:string,action:string,detail=''):string {
    return `<button type="button" class="settings-row" data-action="${action}" aria-label="${escape(label)}"><span>${escape(label)}</span><span class="settings-row-end">${detail?`<span class="settings-row-value">${escape(detail)}</span>`:''}<span class="settings-chevron" aria-hidden="true">›</span></span></button>`;
  }
  function picker(snapshot: Snapshot): string {
    const selectedAppearance=appearance.get(),appearanceLabel=selectedAppearance[0]!.toUpperCase()+selectedAppearance.slice(1);
    return `${pickerScope ? '<p>Choose which received workspace to open.</p>' : ''}
      <section class="settings-group" aria-labelledby="workspace-group-title"><h3 id="workspace-group-title">Workspaces</h3><div class="workspace-list">${snapshot.workspaces.filter(w => !pickerScope || pickerScope.includes(w.id)).map(w => `<button class="picker-option" type="button" aria-label="${escape(w.title)}" data-action="navigate" data-args="${attr({type:'workspace', id:w.id})}" ${session.state.route.type === 'workspace' && session.state.route.id === w.id ? 'aria-current="true"' : ''}><span class="space-mark" aria-hidden="true">${escape(w.title.slice(0,1).toUpperCase())}</span><span>${escape(w.title)}</span></button>`).join('')}</div></section>
      <section class="settings-group" aria-labelledby="workspace-actions-title"><h3 id="workspace-actions-title">Add workspace</h3>${settingsRow('Create workspace','create-space')}${settingsRow('Join existing work','join-work')}</section>
      <section class="settings-group" aria-labelledby="workspace-settings-title"><h3 id="workspace-settings-title">Settings</h3>${settingsRow('Appearance','appearance',appearanceLabel)}${settingsRow('Connections','connections')}${options.connections?settingsRow('OpenAI connection','configure-voice'):''}${options.openAppOptions?settingsRow('App options','app-options'):''}</section>`;
  }
  function appearancePanel():string {
    const selected=appearance.get();
    const descriptions={system:'Match this device',light:'A bright background',dark:'A dim background'};
    return `<p class="context-note">Choose the appearance for this device.</p><div class="appearance-options" role="group" aria-label="Color theme">${(['system','light','dark'] as const).map(value=>`<button type="button" class="appearance-row" data-action="set-theme" data-args="${attr({value})}" aria-label="${value[0]!.toUpperCase()+value.slice(1)}" aria-pressed="${selected===value}"><span class="theme-swatch" data-preview="${value}" aria-hidden="true"></span><span class="appearance-copy"><span>${value[0]!.toUpperCase()+value.slice(1)}</span><small>${descriptions[value]}</small></span><span class="appearance-check" aria-hidden="true">${selected===value?'✓':''}</span></button>`).join('')}</div>`;
  }
  function addPanel(_snapshot:Snapshot):string {
    const route=session.state.route;if(route.type!=='workspace')return '';
    return `<div class="material-choices">${button('<span>Note</span><small>Capture a thought or write something down</small>','new-note',{workspaceId:route.id},'material-choice')}${surfacePresets.map(preset=>button(`<span>${escape(preset.title)}</span><small>${escape(preset.description)}</small>`,'add-surface',{preset:preset.id,workspaceId:route.id},'material-choice')).join('')}${button('<span>Document or website link</span><small>Bring an existing document into this workspace</small>','add-link',{workspaceId:route.id},'material-choice')}</div>`;
  }
  function voiceSettings(snapshot:Snapshot):string {
    const id=connectionWorkspace;if(!id||!connections)return '<p>Choose a workspace first.</p>';
    const title=snapshot.workspaces.find(w=>w.id===id)?.title??'Original workspace',state=connections.current(id),pending=connections.mutation;
    return `<section class="voice-settings"><p class="capture-address">${escape(title)}</p><h3>OpenAI transcription</h3><p>Speak naturally, then review your words.</p>
      <p class="connection-state">${state?.status==='ready'?'A connection is saved for this workspace.':state?.status==='conflict'?'The connection changed on more than one device. Set a new key or remove the connection to resolve it.':state?.status==='removed'?'The previous connection was removed.':'Add an OpenAI API key to enable transcription.'}</p>
      <p class="scope-hint">This connection is shared with devices and people who can access ${escape(title)}. Other workspaces keep their own connections.</p><p class="scope-hint">Removed keys remain in shared history, including for future members. Revoke a key at OpenAI to prevent its use.</p>
      ${connections.peers(id).filter(peer=>peer.status==='upgrade_required'||peer.status==='unreachable').map(peer=>`<p class="notice">${peer.status==='upgrade_required'?'Update '+escape(snapshot.peers.find(p=>p.id===peer.peer_id)?.name??'the connected device')+' to share OpenAI connections. Other workspace material remains available.':'The latest connection exchange with '+escape(snapshot.peers.find(p=>p.id===peer.peer_id)?.name??'a connected device')+' did not finish. Open that device and synchronize from Connections.'}</p>`).join('')}
      ${pending.type==='uncertain'?`<div class="notice">The change was not confirmed. Retry the same change to learn its outcome.${button('Retry connection change','retry-connection',{},'primary')}${button('Review current settings','review-connection',{},'quiet')}</div>`:''}
      ${connections.failedReads.has(id)?`<div class="notice">Connection settings could not be read.${button('Refresh settings','refresh-connection')}</div>`:''}
      ${state?.status==='ready'&&pending.type==='idle'?button('Continue to voice','continue-voice',{},'primary'):''}
      <form id="connection-form"><label for="connection-key">${state?.status==='ready'?'Replace API key':'OpenAI API key'}</label><input id="connection-key" type="password" name="api-key" minlength="16" maxlength="4096" autocomplete="new-password" autocapitalize="none" spellcheck="false" placeholder="Paste your key" ${pending.type!=='idle'?'readonly':''}><button type="submit" class="primary" ${!state||pending.type!=='idle'?'disabled':''}>${pending.type==='saving'?'Saving connection…':'Save connection'}</button></form>
      ${state&&(state.status==='ready'||state.status==='conflict')?button('Remove connection','remove-connection',{},'quiet',pending.type!=='idle'):''}
      ${options.fallbackVoice?button('Use device speech instead','switch-voice',{},'quiet'):''}
    </section>`;
  }
  function mutationNotice(state: State): string {
    if (state.mutation.type === 'uncertain') return `<div class="notice"><p>No save receipt was received. The change may already be on disk. Retrying uses the same operation identity.</p><p>${escape(state.mutation.message)}</p>${button('Retry this change','retry',{},'small primary')}</div>`;
    return '';
  }
  function createWorkspaceForm(): string { return `<form id="workspace-form" class="create-form"><label for="workspace-title">Workspace name<input id="workspace-title" name="title" required maxlength="180" placeholder="Name your workspace" value="${escape(workspaceTitleDraft)}" enterkeyhint="done"></label><button type="submit" class="primary" ${writing() ? 'disabled' : ''}>Create workspace</button></form>`; }
  function home(_snapshot: Snapshot): string {
    return `<section class="welcome"><span class="welcome-mark" aria-hidden="true">✳</span><h1>A place for what’s next.</h1><p>Bring your work together. Capture an idea here, then pick it up on another device.</p>${createWorkspaceForm()}<div class="welcome-join"><span>Already have your work elsewhere?</span>${button('Join existing work','join-work',{},'quiet')}</div></section>`;
  }
  function renderEditor(snapshot: Snapshot): string {
    const e = session.state.editor; if (!e || !editorOpen) return '';
    if(e.type==='surface_new'||e.type==='surface_existing')return renderSurfaceEditor(snapshot,e);
    const resource = e.type === 'existing' ? snapshot.resources.find(r => r.id === e.resourceId) : undefined;
    const changed = e.type === 'existing' && resource && !sameHeads(e.baseHeads, resource.heads.map(h => h.id));
    const title = snapshot.workspaces.find(w => w.id === e.workspaceId)?.title ?? 'Original workspace';
    return `<section class="editor" aria-label="Note editor"><div class="row between"><h2>${e.type === 'new' ? 'New note' : 'Edit note'}</h2><span class="pill">${escape(title)}</span></div><p class="context-note">Your edit stays in ${escape(title)}. Save when you’re ready.</p><form id="editor-form"><label for="note-title">Title</label><input id="note-title" name="title" maxlength="180" required value="${escape(e.title)}" ${writing() ? 'readonly' : ''}><label for="note-body">Content</label><textarea id="note-body" name="body" maxlength="1000000" ${writing() ? 'readonly' : ''}>${escape(e.body)}</textarea>${changed && resource ? `<div class="comparison"><h2>Newer material arrived</h2><p class="context-note">Your edit is still above. Compare these retained versions before saving it as the next revision.</p><div class="comparison-grid">${resource.heads.map(h => revisionView(h,snapshot)).join('')}</div>${button('Save my edit as a new revision','resolve-editor',{ heads:resource.heads.map(h => h.id) },'primary small',writing())}</div>` : ''}<div class="row"><button type="submit" class="primary" ${writing() || changed ? 'disabled' : ''}>${session.state.mutation.type === 'sending' ? 'Saving…' : 'Save note'}</button>${button('Keep draft & go back','hide-editor',{},'quiet')}${button('Discard local edit','discard-editor',{},'quiet',writing())}</div></form></section>`;
  }
  function revisionView(revision: Revision, snapshot: Snapshot): string {
    const author = revision.author === snapshot.device_id ? 'This device' : snapshot.peers.find(p => p.id === revision.author)?.name ?? shortId(revision.author);
    return `<section class="version"><h3>${escape(revision.title)}</h3><small>${escape(author)}</small><p>${escape(isNoteRevision(revision)?revision.body:surfacePreview(revision.surface))}</p></section>`;
  }
  function renderSurface(surface:Surface,focused:boolean,title:string):string {
    const presentation=surfacePresentation(surface);
    if(presentation.type==='code_editor')return `<div class="paper code-preview"><h2 ${focused?'id="reader-title" tabindex="-1"':''}>${escape(title)}</h2><pre>${escape(presentation.text)||'An empty code document.'}</pre></div>`;
    return `<div class="paper application-paper"><span class="application-mark" aria-hidden="true">${escape(presentation.label.slice(0,1))}</span><h2 ${focused?'id="reader-title" tabindex="-1"':''}>${escape(title)}</h2><p class="application-address">${escape(new URL(presentation.url).hostname)}</p>${focused?`<p class="context-note">${escape(presentation.reason)}</p><a class="external-action" href="${escape(presentation.url)}" target="_blank" rel="noopener noreferrer">Open ${escape(presentation.label)} ↗</a>`:''}</div>`;
  }
  function renderSurfaceEditor(snapshot:Snapshot,e:Extract<State['editor'],{type:'surface_new'|'surface_existing'}>):string {
    const resource=e.type==='surface_existing'?snapshot.resources.find(r=>r.id===e.resourceId):undefined;
    const changed=resource&&e.type==='surface_existing'&&!sameHeads(e.baseHeads,resource.heads.map(h=>h.id));
    return `<section class="editor surface-editor" aria-label="Document editor"><h2>${e.type==='surface_new'?'New document':'Edit document'}</h2><form id="editor-form"><label for="note-title">Title</label><input id="note-title" required maxlength="180" value="${escape(e.title)}" ${writing()?'readonly':''}>
      ${e.surface.type==='code_document'?`<label for="code-language">Language</label><select id="code-language" ${writing()?'disabled':''}>${(['plaintext','markdown','javascript','typescript','python','rust','json','html','css'] as const).map(value=>`<option value="${value}" ${e.surface.type==='code_document'&&e.surface.language===value?'selected':''}>${value==='plaintext'?'Plain text':value}</option>`).join('')}</select><div id="code-editor-slot"></div>`:`<label for="surface-url">Document or application link</label><input id="surface-url" type="url" required value="${escape(e.surface.url)}" ${writing()?'readonly':''}>`}
      ${changed&&resource?`<div class="comparison"><h3>Newer material arrived</h3><p>Your draft is kept. Compare these versions before saving.</p>${resource.heads.map(h=>revisionView(h,snapshot)).join('')}${button('Save my edit as a new revision','resolve-editor',{heads:resource.heads.map(h=>h.id)},'primary',writing())}</div>`:''}
      <div class="row"><button type="submit" class="primary" ${writing()||changed?'disabled':''}>Save document</button>${button('Keep draft & go back','hide-editor',{},'quiet')}${button('Discard local edit','discard-editor',{},'quiet',writing())}</div></form></section>`;
  }
  function card(resourceId: string, sourceWorkspaceId: string, snapshot: Snapshot, focused: boolean): string {
    const r = snapshot.resources.find(r => r.id === resourceId), route = session.state.route;
    if (!r) return `<article class="resource-card unavailable"><h2>Material not available here</h2><p>This reference doesn’t include access to its source.</p></article>`;
    const revision = r.heads.length === 1 ? r.heads[0] : undefined;
    const title = titleOf(r);
    const content = revision && isSurfaceRevision(revision) ? renderSurface(revision.surface,focused,revision.title) : revision && isNoteRevision(revision) ? `<div class="paper"><h2 ${focused ? 'id="reader-title" tabindex="-1"' : ''}>${focused ? escape(revision.title) : button(escape(revision.title),'focus',{resourceId:r.id},'material-title')}</h2><div class="note-body">${escape(revision.body) || '<span class="muted">An empty page, ready for your next thought.</span>'}</div></div>` : focused ? `<div class="comparison"><h2>Choose how this work continues</h2><p>Different edits arrived. Every version is kept; choose one or combine them.</p><div class="comparison-grid">${r.heads.map(h => `<div>${revisionView(h,snapshot)}<div class="row">${button('Use this version','use-version',{resourceId:r.id,revisionId:h.id,heads:r.heads.map(h=>h.id)},'primary',writing())}${button('Edit from this version','edit',{resourceId:r.id,revisionId:h.id},'',writing())}</div></div>`).join('')}</div></div>` : `<div class="paper"><h2>${escape(title)}</h2><p>Open to compare the edits and continue.</p></div>`;
    return `<article class="resource-card ${focused?'reading':''}" data-resource="${escape(r.id)}"><header class="card-header"><span class="material-kind">${revision ? isNoteRevision(revision)?'Note':escape(surfaceLabel(revision.surface)) : 'Needs review'}</span>${focused && revision ? button('Edit','edit',{resourceId:r.id},'primary',writing()) : ''}</header>${content}
      ${!focused ? `<footer class="card-footer"><button type="button" data-action="focus" data-args="${attr({resourceId:r.id})}" class="open-material" aria-label="Open ${escape(title)}">${revision&&isSurfaceRevision(revision)?'Open':'Open note'}</button><span aria-hidden="true">↗</span></footer>` : `<details class="card-options" data-disclosure="options-${escape(r.id)}" ${disclosures.has('options-'+r.id)?'open':''}><summary>Organize & history</summary><div class="row">${button('Move to start on this device','move',{resourceId:r.id})}${button('Close local view '+escape(title),'close-view',{resourceId:r.id})}${route.type === 'workspace' ? button('Remove reference','remove-reference',{workspaceId:route.id,resourceId:r.id,sourceWorkspaceId},'',writing()) : ''}</div><form data-form="add-reference" data-resource="${escape(r.id)}" data-source="${escape(r.workspace_id)}"><label>Reference in<select name="destination" aria-label="Destination workspace for ${escape(title)}">${snapshot.workspaces.filter(w=>!w.placements.some(p=>p.resource_id===r.id)).map(w=>`<option value="${escape(w.id)}">${escape(w.title)}</option>`).join('')}</select></label><button type="submit" ${writing() || snapshot.workspaces.every(w=>w.placements.some(p=>p.resource_id===r.id))?'disabled':''}>Add reference</button></form><p class="local-note">A reference keeps the source’s access. Closing a view only changes this device.</p><details><summary>Retained history · ${r.history.length} revisions</summary>${r.history.map(h=>revisionView(h,snapshot)).join('')}</details></details>`}</article>`;
  }
  function workspace(snapshot: Snapshot, workspaceId: string): string {
    const workspace = snapshot.workspaces.find(w=>w.id===workspaceId); if (!workspace) return '<div class="notice">This workspace is unavailable.</div>';
    const view = session.view(), placements = [...workspace.placements].sort((a,b)=> { const ai=view.order.indexOf(a.resource_id),bi=view.order.indexOf(b.resource_id);return (ai<0?Infinity:ai)-(bi<0?Infinity:bi); }).filter(p=>!view.closed.includes(p.resource_id));
    const focused = view.mode==='focus' && view.focused && placements.some(p=>p.resource_id===view.focused), visible=focused?placements.filter(p=>p.resource_id===view.focused):placements;
    const editor = session.state.editor;
    if (editor && editorOpen && editor.workspaceId === workspaceId) return `<div class="work-content editing">${renderEditor(snapshot)}</div>`;
    return `<section class="work-content"><header class="work-heading"><div>${focused ? button('← All work','all-work',{},'quiet back') : '<h1>On your desk</h1>'}</div><div class="row"><button type="button" class="add-material icon-button" data-action="add" aria-label="Add to workspace" ${writing()?'disabled':''}>${plusIcon}</button></div></header>
      ${editor ? `<div class="draft-banner"><span>You have an unfinished edit.</span>${button('Continue edit','resume-editor',{},'quiet')}</div>` : ''}
      ${view.closed.length?`<div class="row reopen">${button('Reopen views','reopen',{},'quiet')}<span class="muted">${count(view.closed.length,'hidden view')}</span></div>`:''}
      <div class="board ${focused?'focus':''}">${visible.map(p=>card(p.resource_id,p.source_workspace_id,snapshot,Boolean(focused))).join('')}</div>
      ${!placements.length?'<div class="empty"><span class="empty-mark" aria-hidden="true">+</span><h2>Make room for an idea.</h2><p>Speak a thought, or add something to work with.</p></div>':''}
    </section>`;
  }
  function devices(snapshot: Snapshot): string {
    const pairing = session.state.pairing;
    const joining = pairing.type === 'joining';
    const stage = pairing.type === 'joining' ? pairing.stage : null;
    const progress = joinStageCopy(stage);
    const incoming = Boolean(joinTicket.trim());
    const joinFailed = pairing.type === 'failed' && pairing.action === 'join';
    const expired = pairing.type === 'invite' && Date.now() >= pairing.invite.expires_at_ms;
    const network = snapshot.network.type === 'active' ? 'Networking is enabled.'
      : snapshot.network.type === 'unavailable' ? 'Peer connections are unavailable. Local work remains here.'
      : snapshot.network.type === 'paused' ? 'Connections are paused while this page is in the background.' : 'Connections are closed.';
    if (pairing.type === 'invite' && inviteQr?.ticket !== pairing.invite.ticket) {
      let svg: string | null = null;
      try {
        const qr = qrcode(0, 'M');
        qr.addData(options.invitationUrl?.(pairing.invite.ticket) ?? pairing.invite.ticket);
        qr.make();
        svg = qr.createSvgTag({ cellSize: 6, margin: 24, scalable: true });
      } catch { /* The copy/paste invitation remains usable if QR capacity is exceeded. */ }
      inviteQr = { ticket: pairing.invite.ticket, svg };
    }
    const disclosure = (key: string): string => disclosures.has(key) ? 'open' : '';
    const selectedNames = pairing.type === 'invite' ? pairing.workspaceIds.map(id => snapshot.workspaces.find(w => w.id === id)?.title ?? 'Original workspace') : [];
    const outgoing = `<section class="pair-panel invite-panel" aria-label="Share workspaces">
      ${pairing.type === 'invite' ? `<span class="eyebrow">Invitation ready</span><h2>${expired ? 'Create a fresh invitation' : 'Open this on the other device'}</h2>
        <div class="access-scope"><span>Access included</span><div class="row">${selectedNames.map(name => `<span class="pill">${escape(name)}</span>`).join('')}</div></div>
        <p>Only these workspaces are shared. Your other workspaces stay private to their current members.</p>
        ${expired ? '<div class="notice">This invitation has expired. Choose Create invitation again to continue.</div>' : inviteQr?.svg ? `<div class="qr" role="img" aria-label="Pairing invitation QR code">${inviteQr.svg}</div>` : '<p>This invitation is too large for a QR code. Share or copy the link instead.</p>'}
        <p class="pair-instruction">${options.invitationUrl ? 'Use the camera on the other device, then choose Join.' : 'Scan with the native app on the other device, then choose Join.'} Keep this page open and the screen awake.</p>
        <div class="row invitation-actions">${options.shareInvitation && options.invitationUrl ? button(sharingInvitation ? 'Opening share…' : 'Share invitation','share-invitation',{},'primary',expired || sharingInvitation) : ''}${options.invitationUrl ? button('Copy invitation link','copy-link',{},options.shareInvitation ? 'small' : 'primary',expired) : button('Copy ticket','copy-ticket',{},'primary',expired)}</div>
        <p class="invite-expiry">${expired ? 'Expired' : 'Available until '+escape(time(pairing.invite.expires_at_ms))}</p>
        <details data-disclosure="outgoing" ${disclosure('outgoing')}><summary>Show invitation for copy and paste</summary><label for="invite-ticket">Pairing ticket</label><textarea id="invite-ticket" class="ticket" readonly spellcheck="false" autocomplete="off">${escape(pairing.invite.ticket)}</textarea>${button('Copy ticket','copy-ticket',{},'small',expired)}</details>
        <div class="row">${button('Change selected workspaces','hide-ticket',{},'quiet small')}</div>` : `<span class="eyebrow">Another device</span><h2>Choose what to share</h2><p>Your phone can share several workspaces; a guest can receive just one. Choose what this invitation includes.</p>`}
      <form id="invite-form" ${pairing.type === 'invite' && !expired ? 'hidden' : ''}>
        ${options.connections?'<p class="scope-hint">Selected workspaces include their saved OpenAI connections and retained key history, including removed keys. People receiving access can use or change those connections.</p>':''}${snapshot.workspaces.map(w => `<label class="workspace-check"><input type="checkbox" name="workspace" value="${escape(w.id)}" ${selectedSpaces.has(w.id) ? 'checked' : ''}>${escape(w.title)}</label>`).join('')}
        ${!snapshot.workspaces.length ? '<p>Create a workspace before making an invitation.</p>' : '<p class="scope-hint">Connecting a device does not give it access to every workspace. New workspaces are not added automatically.</p>'}
        ${pairing.type === 'failed' && pairing.action === 'invite' ? `<div class="notice">${escape(pairing.message)}</div>` : ''}
        <button id="create-invite" class="primary" type="submit" ${!selectedSpaces.size || pairing.type === 'creating' || joining ? 'disabled' : ''}>${pairing.type === 'creating' ? 'Creating invitation…' : 'Create invitation'}</button>
      </form>
    </section>`;
    const joinPanel = `<section class="pair-panel join-panel ${incoming ? 'incoming' : ''}" aria-label="Join workspaces">
      <span class="eyebrow">${incoming ? 'Invitation received' : 'This device'}</span>
      <h2>${joining ? 'Joining selected workspaces…' : joinFailed ? 'Connection not confirmed' : incoming ? 'Bring this work to your device' : 'Join from another device'}</h2>
      <p>${incoming ? 'Join only the workspaces selected by the inviting device. Your existing work here stays available.' : 'Open an invitation with your camera, or paste a link from the device that has your work.'}</p>
      ${joining ? `<div class="join-progress" role="status"><span class="status-dot" aria-hidden="true"></span><div><strong>${escape(progress.title)}</strong><p>${escape(progress.detail)}</p></div></div>${stage ? `<ol class="join-steps" aria-label="Joining progress">${joinStages.map(item => `<li ${item === stage ? 'aria-current="step"' : ''}>${escape(joinStageCopy(item).title)}</li>`).join('')}</ol>` : ''}` : ''}
      ${joinFailed ? `<div class="notice" role="alert"><p>${escape(pairing.message)}</p>${pairing.type === 'failed' && pairing.action === 'join' && (pairing.stage === null || pairing.stage === 'contacting_device' || pairing.stage === 'saving_workspace') ? '<p>The other device may have accepted the request already. Retrying the same invitation can recover its confirmation.</p>' : ''}</div>` : ''}
      ${scanAvailable ? `<div class="row">${button(scanning ? 'Scanning…' : 'Scan invitation QR','scan',{},'small',scanning || joining)}${scanning ? button('Cancel scan','cancel-scan',{},'small') : ''}</div>` : ''}
      ${scanMessage && !joining ? `<p class="scan-message">${escape(scanMessage)}</p>` : ''}
      <form id="join-form">
        <details data-disclosure="incoming" ${disclosure('incoming')}><summary>${incoming ? 'View or replace invitation' : 'Paste an invitation'}</summary><label for="join-ticket">Invitation to join</label><textarea id="join-ticket" name="ticket" required ${joining ? 'readonly' : ''} spellcheck="false" autocomplete="off" autocapitalize="none" placeholder="Paste an invitation link or ticket">${escape(joinTicket)}</textarea></details>
        <button id="join-submit" class="primary" type="submit" ${!incoming || joining || pairing.type === 'creating' ? 'disabled' : ''}>${joining ? 'Joining…' : joinFailed ? 'Try joining again' : 'Join selected workspaces'}</button>
      </form>
      ${incoming && !joining ? '<p class="scope-hint">An invitation connects devices for its selected workspaces. It does not combine all their data.</p>' : ''}
    </section>`;
    const peers = snapshot.peers.map(p => `<article class="device-card"><div class="row between"><h2>${escape(p.name)}</h2><span class="resource-id">${escape(shortId(p.id))}</span></div>
      <div class="access-scope"><span>Shared with this device</span><div class="row">${p.workspace_ids.map(id => `<span class="pill">${escape(snapshot.workspaces.find(w => w.id === id)?.title ?? 'Unavailable workspace')}</span>`).join('')}</div></div>
      <p class="${p.state.type === 'unreachable' ? 'unreachable' : ''}">${p.state.type === 'never_synced' ? 'No completed exchange observed.' : p.state.type === 'syncing' ? 'Exchange in progress.' : p.state.type === 'synced' ? `Last exchange ${escape(time(p.state.at_ms))}.` : `Last exchange could not finish at ${escape(time(p.state.at_ms))}. Keep the other device open, then synchronize again.`}</p></article>`).join('');
    if (panel === 'share') return outgoing;
    if (panel === 'join') return joinPanel;
    return `<section class="devices"><p>Choose which work to bring to another device.</p><div class="connection-actions">${snapshot.workspaces.length ? button('Share this workspace','share',{workspaceId:session.state.route.type==='workspace'?session.state.route.id:snapshot.workspaces[0]?.id},'primary') : ''}${button('Join existing work','join-work')}</div><h3>Connected devices</h3>${peers || '<p class="context-note">No devices connected yet.</p>'}<div class="row">${button(session.state.syncing ? 'Synchronizing…' : 'Synchronize','sync',{},'',session.state.syncing)}</div><details data-disclosure="connection-details" ${disclosure('connection-details')}><summary>Connection details</summary><p>${escape(network)}</p><p>${escape(snapshot.device_name)} · ${escape(snapshot.device_id)}</p></details></section>`;
  }

  function render(): void {
    if(disposed)return;
    const oldFocus=document.activeElement instanceof HTMLElement&&root.contains(document.activeElement)?document.activeElement:null;
    const focusAction=oldFocus?.dataset.action,focusArgs=oldFocus?.dataset.args,focusForm=oldFocus?.closest('form')?.id,focusText=oldFocus?.textContent;
    const focusId=oldFocus?.id,selection=oldFocus instanceof HTMLInputElement||oldFocus instanceof HTMLTextAreaElement?{start:oldFocus.selectionStart,end:oldFocus.selectionEnd}:null;
    const scroll=frame.querySelector('.main')?.scrollTop??0, dialogScroll=dialog.scrollTop, state=session.state,snapshot=snapshotOf(state);
    if(!snapshot){frame.innerHTML=`<div class="app-shell"><header class="topbar"><span class="brand">Shared Workspace</span></header><main class="main"><section class="gate"><h1>${state.host.type==='unavailable'?'Open your native workspace':state.host.type==='loading'?'Opening your work…':'Your work could not open'}</h1><p>${state.host.type==='unavailable'?'Open the installed app to use this native interface.':state.host.type==='failed'?escape(state.host.message):'Reading the work saved on this device.'}</p>${state.host.type==='failed'?button('Retry connection','refresh',{},'primary'):''}</section></main></div>`;return}
    const workspaceId = state.route.type === 'workspace' ? state.route.id : null;
    const currentTitle = snapshot.workspaces.find(w => w.id === workspaceId)?.title ?? 'Your work';
    frame.innerHTML=`<div class="app-shell"><header class="topbar"><div class="workspace-context"><button type="button" id="workspace-picker" data-action="picker" aria-label="Current workspace: ${escape(currentTitle)}" aria-haspopup="dialog"><span>${escape(currentTitle)}</span><span aria-hidden="true">⌄</span></button></div><div class="top-actions">${updateAvailable&&options.openUpdate?'<button type="button" id="workspace-update" data-action="update" aria-label="Update available">Update available</button>':''}${workspaceId?`<button type="button" data-action="share" data-args="${attr({workspaceId})}" class="share-work icon-button" aria-label="Share this workspace">${shareIcon}</button>`:''}</div></header><main class="main">${state.host.type==='failed'?`<div class="notice">Showing the last saved view. ${button('Retry read','refresh')}</div>`:''}${mutationNotice(state)}${snapshot.network.type==='unavailable'?`<div class="notice">Connections are unavailable. You can still save work on this device. ${button('Check connections','connections',{},'quiet')}</div>`:''}<div class="status-message ${!localMessage&&['Saved here.','Waiting for the local save receipt…','Connected. Your shared work is saved here.'].includes(state.message)?'routine-status':''}" role="status" aria-live="polite">${escape(localMessage||state.message)}</div>${state.route.type==='workspace'?workspace(snapshot,state.route.id):home(snapshot)}</main>${workspaceId ? captureDock() : ''}</div>`;
    frame.inert=Boolean(panel);if(panel)frame.setAttribute('aria-hidden','true');else frame.removeAttribute('aria-hidden');
    if(panel){
      const titles: Record<Panel,string> = {picker:'Switch workspace',create:'Create workspace',capture:'Capture',connections:'Connections',share:'Share workspace',join:'Join workspaces',appearance:'Appearance',add:'Add to workspace','voice-settings':'OpenAI connection'};
      dialog.setAttribute('aria-label',titles[panel]); dialog.dataset.panel=panel;
      dialog.innerHTML=`<header class="dialog-heading"><h2>${titles[panel]}</h2><button type="button" data-action="close-dialog" aria-label="Close dialog">×</button></header><div class="dialog-body">${panel==='picker'?picker(snapshot):panel==='create'?createWorkspaceForm():panel==='capture'?renderCommand(snapshot):panel==='appearance'?appearancePanel():panel==='add'?addPanel(snapshot):panel==='voice-settings'?voiceSettings(snapshot):devices(snapshot)}${mutationNotice(state)}<div class="dialog-status" role="status">${escape(localMessage||((panel==='connections'||panel==='share'||panel==='join')?state.message:''))}</div></div>`;
      if(!dialog.open)dialog.showModal(); dialog.scrollTop=dialogScroll;
    }
    const keyField=byId<HTMLInputElement>('connection-key');if(keyField&&connections?.mutation.type==='idle')keyField.value=keyDraft;
    const slot=byId('code-editor-slot'),editor=state.editor;
    if(slot&&editor&&(editor.type==='surface_new'||editor.type==='surface_existing')&&editor.surface.type==='code_document'){
      const identity=editor.draftId;
      if(codeIdentity!==identity){codeEditor?.dispose();codeNode.replaceChildren();codeIdentity=identity;codeEditor=mountCodeEditor(codeNode,editor.surface.text,editor.surface.language,text=>{lastEditorField='code';const current=session.state.editor;if(current&&(current.type==='surface_new'||current.type==='surface_existing')&&current.surface.type==='code_document')session.updateSurface({...current.surface,text})});}
      slot.append(codeNode);codeEditor?.setText(editor.surface.text);codeEditor?.setLanguage(editor.surface.language);codeEditor?.setTheme(document.documentElement.dataset.theme==='dark'?'dark':'light');codeEditor?.setReadonly(writing());if(oldFocus?.isContentEditable)codeEditor?.focus();
    }else if(!editor||!(editor.type==='surface_new'||editor.type==='surface_existing')){codeEditor?.dispose();codeEditor=null;codeIdentity=null;}
    const main=frame.querySelector('.main');if(main)main.scrollTop=scroll;
    if(oldFocus){const replacement=focusId?byId(focusId):Array.from(root.querySelectorAll<HTMLElement>('button,summary,select')).find(el=>focusAction?el.dataset.action===focusAction&&el.dataset.args===focusArgs:el.tagName===oldFocus.tagName&&el.textContent===focusText&&el.closest('form')?.id===focusForm);replacement?.focus({preventScroll:true});if(selection&&(replacement instanceof HTMLInputElement||replacement instanceof HTMLTextAreaElement)&&selection.start!==null&&selection.end!==null){try{replacement.setSelectionRange(selection.start,selection.end)}catch{}}}
  }
  async function changeRoute(route:Route):Promise<void>{void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})}if(route.type==='devices'){openPanel('connections');return}if(route.type==='home'&&session.snapshot()?.workspaces.length){openPanel('picker');return}session.navigate(route);const main=root.querySelector('.main');if(main)main.scrollTop=0}
  async function resumeEditor():Promise<boolean>{
    const editor=session.state.editor;if(!editor)return false;
    editorOpen=true;await changeRoute({type:'workspace',id:editor.workspaceId});localMessage='Finish or discard your current edit before starting another.';render();return true;
  }
  async function addSurface(workspaceId:string,presetId:string):Promise<void>{
    const preset=surfacePreset(presetId);if(!preset)return;
    if(preset.surface.type==='code_document'){if(await resumeEditor())return;editorOpen=true;session.newSurface(workspaceId,preset.surface,preset.title)}
    else await session.createSurface(workspaceId,preset.title,preset.surface);
  }
  async function execute(intent:Intent):Promise<void>{
    switch(intent.type){
      case'navigate':await changeRoute(intent.route);break;
      case'open':await changeRoute({type:'workspace',id:intent.workspaceId});focusResource(intent.resourceId);break;
      case'new_note':if(!await resumeEditor()){editorOpen=true;session.newNote(intent.workspaceId)}break;
      case'surface':await addSurface(intent.workspaceId,intent.preset);break;
      case'capture':await session.capture(intent.workspaceId,intent.body);break;
      case'create_workspace':await session.createWorkspace(intent.title);break;
      case'close':session.close(intent.resourceId);break;
      case'sync':await session.sync();break;
    }
  }
  const onClick=async(event:MouseEvent):Promise<void>=>{
    const summary=(event.target as Element).closest('summary'),detail=summary?.parentElement;
    if(detail instanceof HTMLDetailsElement&&detail.dataset.disclosure){if(detail.open)disclosures.delete(detail.dataset.disclosure);else disclosures.add(detail.dataset.disclosure);return}
    const target=(event.target as Element).closest<HTMLButtonElement>('button[data-action]');if(!target||target.disabled)return;
    // Safari pointer activation need not focus a button. Retain the actual
    // opener so dismissal can restore focus even after rendering replaces it.
    if(!panel)target.focus({preventScroll:true});
    const action=target.dataset.action,args:Record<string,unknown>=JSON.parse(target.dataset.args??'{}');localMessage='';
    switch(action){
      case'update':if(historyOwned){afterPanelClose=()=>options.openUpdate?.();closePanel()}else{closePanel();options.openUpdate?.()}break;
      case'app-options':if(historyOwned){afterPanelClose=()=>options.openAppOptions?.();closePanel()}else{closePanel();options.openAppOptions?.()}break;
      case'continue-voice':inputMode='voice';openPanel('capture',false);break;
      case'configure-voice':{const route=session.state.route;if(!keyDraft)connectionWorkspace=command?.address.workspaceId??(route.type==='workspace'?route.id:null);if(connectionWorkspace){openPanel('voice-settings',false);void connections?.refresh(connectionWorkspace);void connections?.refreshPeers(connectionWorkspace)}break;}
      case'refresh-connection':if(connectionWorkspace){await connections?.refresh(connectionWorkspace);await connections?.refreshPeers(connectionWorkspace)}break;
      case'retry-connection':await connections?.retry();break;
      case'review-connection':keyDraft='';await connections?.reviewCurrent();break;
      case'remove-connection':if(connectionWorkspace)await connections?.remove(connectionWorkspace);break;
      case'switch-voice':await voice.cancel();selectedVoice=voiceMethod==='provider'&&options.fallbackVoice?options.fallbackVoice:options.voice;voiceMethod=selectedVoice===options.voice?'provider':'system';capability=await selectedVoice.capability();inputMode='voice';openPanel('capture',false);break;
      case'add-surface':closePanel();await addSurface(String(args.workspaceId),String(args.preset));break;
      case'add-link':closePanel();if(await resumeEditor())break;editorOpen=true;session.newSurface(String(args.workspaceId),{type:'document',application:'website',url:'https://'},'Document');break;
      case'appearance':openPanel('appearance',false);break;
      case'set-theme':if(['system','light','dark'].includes(String(args.value)))appearance.set(args.value as Appearance);break;
      case'add':openPanel('add',false);break;
      case'picker':pickerScope=null;openPanel('picker');break;
      case'create-space':openPanel('create');break;
      case'connections':openPanel('connections');break;
      case'join-work':openPanel('join');break;
      case'close-dialog':closePanel();break;
      case'navigate':closePanel();await changeRoute(args.type==='workspace'?{type:'workspace',id:String(args.id)}:{type:args.type==='devices'?'devices':'home'});break;
      case'focus':focusResource(String(args.resourceId));break;
      case'open-resource':await changeRoute({type:'workspace',id:String(args.workspaceId)});focusResource(String(args.resourceId));break;
      case'all-work':session.mode('canvas');{const route=session.state.route,main=frame.querySelector('.main');if(main&&route.type==='workspace')main.scrollTop=listScroll.get(route.id)??0}break;
      case'close-view':session.close(String(args.resourceId));break;
      case'reopen':session.reopen();break;
      case'move':session.move(String(args.resourceId));break;
      case'new-note':closePanel();editorOpen=true;if(session.state.editor){await changeRoute({type:'workspace',id:session.state.editor.workspaceId});render()}else session.newNote(String(args.workspaceId));byId('note-title')?.focus();break;
      case'edit':editorOpen=true;if(session.state.editor){await changeRoute({type:'workspace',id:session.state.editor.workspaceId});localMessage='Finish or discard your current edit before starting another.';render()}else {session.edit(String(args.resourceId),typeof args.revisionId==='string'?args.revisionId:undefined);const source=availableSnapshot()?.resources.find(r=>r.id===String(args.resourceId));if(source)await changeRoute({type:'workspace',id:source.workspace_id})}const main=frame.querySelector('.main');if(main)main.scrollTop=0;byId('note-title')?.focus({preventScroll:true});break;
      case'discard-editor':session.closeEditor();break;
      case'hide-editor':editorOpen=false;render();break;
      case'resume-editor':if(session.state.editor){editorOpen=true;await changeRoute({type:'workspace',id:session.state.editor.workspaceId});render();byId('note-body')?.focus()}break;
      case'resolve-editor':await session.saveEditor(args.heads as string[]);break;
      case'use-version':await session.useVersion(String(args.resourceId),String(args.revisionId),args.heads as string[]);break;
      case'remove-reference':await session.placement('remove_placement',String(args.workspaceId),String(args.resourceId),String(args.sourceWorkspaceId));break;
      case'retry':await session.retry();if(session.state.mutation.type==='idle'&&capturedSubmission){if(command===capturedSubmission)command=null;capturedSubmission=null;closePanel()}break;
      case'refresh':await session.refresh();break;
      case'sync':await session.sync();break;
      case'share':if(connections)for(const workspace of availableSnapshot()?.workspaces??[])void connections.refresh(workspace.id);selectedSpaces=new Set([String(args.workspaceId)]);session.clearInvite();openPanel('share');break;
      case'hide-ticket':inviteQr=null;disclosures.delete('outgoing');session.clearInvite();break;
      case'share-invitation':{const pairing=session.state.pairing;if(pairing.type==='invite'&&options.invitationUrl&&options.shareInvitation&&!sharingInvitation){sharingInvitation=true;render();try{const outcome=await options.shareInvitation(options.invitationUrl(pairing.invite.ticket));localMessage=outcome==='shared'?'Invitation shared. Join on the other device to connect.':''}catch{localMessage='Sharing could not open. Copy the invitation link instead.'}finally{sharingInvitation=false;render()}}break}
      case'copy-link':{const pairing=session.state.pairing;if(pairing.type==='invite'&&options.invitationUrl){try{await navigator.clipboard.writeText(options.invitationUrl(pairing.invite.ticket));localMessage='Invitation link copied.'}catch{disclosures.add('outgoing');localMessage='Select and copy the invitation below.';render();const field=byId<HTMLTextAreaElement>('invite-ticket');field?.focus();field?.select();break}render()}break}
      case'copy-ticket':{const field=byId<HTMLTextAreaElement>('invite-ticket');if(field){try{await navigator.clipboard.writeText(field.value);localMessage='Invitation copied.'}catch{disclosures.add('outgoing');localMessage='Select and copy the invitation below.';render();const current=byId<HTMLTextAreaElement>('invite-ticket');current?.focus();current?.select();break}render()}break}
      case'type-command':inputMode='typing';openPanel('capture');break;
      case'review-capture':inputMode='voice';openPanel('capture',false);break;
      case'voice-mode':inputMode='voice';openPanel('capture',false);break;
      case'append-dictation':await insertDictation(true);break;
      case'dictation-note':captureEditor=null;captureRecovery=false;captureAsNote=true;render();break;
      case'new-command':await voice.cancel();command=null;captureEditor=null;captureRecovery=false;captureAsNote=false;render();if(inputMode==='typing')byId('command-text')?.focus();break;
      case'return-command':if(command)await changeRoute(command.address.workspaceId?{type:'workspace',id:command.address.workspaceId}:{type:'home'});break;
      case'speak':{inputMode='voice';const snapshot=availableSnapshot();if(!snapshot)break;const scope=command?.address.workspaceId??(session.state.route.type==='workspace'?session.state.route.id:null);if(connections&&voiceMethod==='provider'&&(!scope||connections.current(scope)?.status!=='ready')){connectionWorkspace=scope;if(scope){openPanel('voice-settings',false);void connections.refresh(scope)}return;}if(!command){captureEditor=editorTarget();captureRecovery=false;captureAsNote=false;command={text:'',address:currentAddress(snapshot),source:'typed'}}openPanel('capture',capability.type==='system_dictation');await voice.start(command.address,capability);break}
      case'stop-voice':await voice.stop();break;
      case'cancel-voice':await voice.cancel();break;
      case'scan':if(!scanning){const epoch=++scanEpoch;scanning=true;scanMessage='';render();try{const ticket=await options.scanner.scan();if(epoch===scanEpoch&&!disposed){disclosures.delete('incoming');joinTicket=ticket;scanMessage='Invitation read. Review it, then choose Join.'}}catch{if(epoch===scanEpoch)scanMessage='No invitation was accepted from the camera. You can paste a ticket instead.'}finally{if(epoch===scanEpoch){scanning=false;render()}}}break;
      case'cancel-scan':{const epoch=++scanEpoch;scanning=false;try{await options.scanner.cancel();if(epoch===scanEpoch)scanMessage='Scan cancelled.'}catch{if(epoch===scanEpoch)scanMessage='Camera cancellation was not confirmed.'}render();break;}
    }
  };
  const onInput=(event:Event):void=>{
    const field=event.target;
    if(field instanceof HTMLSelectElement&&field.id==='code-language'){const e=session.state.editor;if(e&&(e.type==='surface_new'||e.type==='surface_existing')&&e.surface.type==='code_document'){session.updateSurface({...e.surface,language:field.value as CodeLanguage});codeEditor?.setLanguage(field.value as CodeLanguage)}return}
    if(!(field instanceof HTMLInputElement||field instanceof HTMLTextAreaElement))return;
    if(field.id==='connection-key')keyDraft=field.value;
    if(field.id==='surface-url'){const e=session.state.editor;if(e&&(e.type==='surface_new'||e.type==='surface_existing')&&e.surface.type!=='code_document')session.updateSurface({...e.surface,url:field.value})}
    if(field.id==='workspace-title')workspaceTitleDraft=field.value;
    if(field.id==='note-title'){lastEditorField='title';session.updateEditor('title',field.value);}
    if(field.id==='note-body'){lastEditorField='body';session.updateEditor('body',field.value);}
    if(field.id==='join-ticket'){joinTicket=field.value;const submit=byId<HTMLButtonElement>('join-submit');if(submit)submit.disabled=!joinTicket.trim()||session.state.pairing.type==='joining'||session.state.pairing.type==='creating';}
    if(field.id==='command-text'){const snapshot=availableSnapshot();if(snapshot){command={text:field.value,address:command?.address??currentAddress(snapshot),source:command?.source??'typed'};updateCommand()}}
    if(field instanceof HTMLInputElement&&field.name==='workspace'&&field.type==='checkbox'){if(field.checked)selectedSpaces.add(field.value);else selectedSpaces.delete(field.value);const create=byId<HTMLButtonElement>('create-invite');if(create)create.disabled=!selectedSpaces.size}
  };
  const onSubmit=async(event:SubmitEvent):Promise<void>=>{
    event.preventDefault();const form=event.target;if(!(form instanceof HTMLFormElement))return;localMessage='';
    if(form.id==='connection-form'){if(connectionWorkspace&&connections&&connections.current(connectionWorkspace)&&connections.mutation.type==='idle'){const key=keyDraft;keyDraft='';await connections.set(connectionWorkspace,key)}return}
    if(form.id==='workspace-form'){await session.createWorkspace(String(new FormData(form).get('title')??''));if(session.state.mutation.type==='idle'){workspaceTitleDraft='';closePanel()}return}
    if(form.id==='editor-form'){const e=session.state.editor;if(e&&(e.type==='surface_new'||e.type==='surface_existing')){try{decodeSurface(e.surface)}catch(error){localMessage=error instanceof Error?error.message:'Check the document details.';render();return}}await session.saveEditor();return}
    if(form.id==='invite-form'){disclosures.delete('outgoing');await session.createInvite([...selectedSpaces]);root.querySelector('.invite-panel')?.scrollIntoView({block:'start'});return}
    if(form.id==='join-form'){
      const before = new Set(session.snapshot()?.workspaces.map(w=>w.id)??[]);
      disclosures.delete('incoming');await session.join(joinTicket);
      if(session.state.pairing.type==='idle'){
        joinTicket='';const received=session.snapshot()?.workspaces.filter(w=>!before.has(w.id))??[];
        if(received.length===1){closePanel()}
        else {pickerScope=received.length?received.map(w=>w.id):null;localMessage=received.length?'Your shared work is ready.':'Connected. Choose the workspace you want to continue.';openPanel('picker')}
        render();
      }else root.querySelector('.join-panel')?.scrollIntoView({block:'start'});return;
    }
    if(form.dataset.form==='add-reference'){const destination=new FormData(form).get('destination');if(destination)await session.placement('add_placement',String(destination),form.dataset.resource!,form.dataset.source!);return}
    if(form.id==='command-form'){
      const snapshot=availableSnapshot();if(!snapshot||captureBusy()||writing())return;
      if(captureEditor){await voice.cancel();await insertDictation();return}
      const plan=planCapture(snapshot);if(plan.type!=='ready')return;
      await voice.cancel();
      if(plan.intent.type==='capture'||plan.intent.type==='create_workspace'||plan.intent.type==='surface'&&plan.intent.preset!=='code'){
        capturedSubmission=command;await execute(plan.intent);
        if(session.state.mutation.type==='idle'){command=null;capturedSubmission=null;closePanel()}
      }else {command=null;closePanel();await execute(plan.intent)}
      render();
    }
  };
  const unsubscribe=session.subscribe((state,change)=>{
    const snapshot=snapshotOf(state);
    if(snapshot){
      if(connections&&change==='data'){const route=state.route;if(route.type==='workspace')void connections.refresh(route.id)}
      if(presentationDevice!==snapshot.device_id){
        presentationDevice=snapshot.device_id;const saved=loadPresentation(snapshot.device_id);
        if(saved)session.restoreViews(saved.views);
        const savedRoute = saved?.route;
        const route: Route = savedRoute?.type==='workspace' && snapshot.workspaces.some(w=>w.id===savedRoute.id) ? savedRoute : snapshot.workspaces[0] ? {type:'workspace',id:snapshot.workspaces[0].id} : {type:'home'};
        session.navigate(route);
        if(options.initialInvite)openPanel('join',false);
        return;
      }
      if(state.route.type==='workspace'||!snapshot.workspaces.length)savePresentation(snapshot.device_id,{route:state.route,views:state.views});
    }

    const focused=document.activeElement;
    if(change==='data'&&(composing||focused instanceof HTMLInputElement||focused instanceof HTMLTextAreaElement||focused instanceof HTMLElement&&focused.isContentEditable)){deferredRender=true;return}render();
  });
  const onDisclosure=(event:Event):void=>{const detail=event.target;if(detail instanceof HTMLDetailsElement&&root.contains(detail)&&detail.dataset.disclosure){if(detail.open)disclosures.add(detail.dataset.disclosure);else disclosures.delete(detail.dataset.disclosure)}};
  const onFocusOut=():void=>{setTimeout(()=>{if(deferredRender&&!composing&&!(document.activeElement instanceof HTMLInputElement)&&!(document.activeElement instanceof HTMLTextAreaElement)){deferredRender=false;render()}},0)};
  const onEditorFocus=(event:FocusEvent):void=>{const field=event.target;if(field instanceof HTMLElement){if(field.id==='note-title')lastEditorField='title';else if(field.id==='note-body')lastEditorField='body';else if(field.closest('.code-editor'))lastEditorField='code'}};
  const onCompositionStart=():void=>{composing=true},onCompositionEnd=():void=>{composing=false};
  const clickHandler=(e:MouseEvent):void=>{void onClick(e).catch(()=>{localMessage='The requested action did not complete.';render()})};
  const submitHandler=(e:SubmitEvent):void=>{void onSubmit(e).catch(()=>{localMessage='The requested action did not complete.';render()})};
  root.addEventListener('toggle',onDisclosure,true);root.addEventListener('click',clickHandler);root.addEventListener('input',onInput);root.addEventListener('submit',submitHandler);root.addEventListener('focusout',onFocusOut);root.addEventListener('focusin',onEditorFocus);root.addEventListener('compositionstart',onCompositionStart);root.addEventListener('compositionend',onCompositionEnd);
  const onCancel=(event:Event):void=>{event.preventDefault();closePanel()};
  const onBackdrop=(event:MouseEvent):void=>{if(event.target!==dialog)return;const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closePanel()};
  const onBack=():void=>{if(historyReturning){historyReturning=false;if(panel){history.pushState({...history.state,workspaceDialog:true},'');historyOwned=true}const after=afterPanelClose;afterPanelClose=null;after?.();return}if(panel)closePanel(true)};
  dialog.addEventListener('cancel',onCancel);dialog.addEventListener('click',onBackdrop);window.addEventListener('popstate',onBack);
  const visibility=():void=>{if(document.hidden){void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})}}else void session.refresh()};document.addEventListener('visibilitychange',visibility);
  const poll=setInterval(()=>{if(!document.hidden&&session.state.mutation.type!=='sending'&&!session.state.syncing)void session.refresh()},options.pollMs??5000);
  const connections=options.connections?new ConnectionSession(options.connections,()=>{const active=document.activeElement;if(composing||active instanceof HTMLInputElement||active instanceof HTMLTextAreaElement||active instanceof HTMLElement&&active.isContentEditable){deferredRender=true;return}render()}):null;
  const appearance=observeAppearance(()=>render());
  render();void session.refresh();void options.voice.capability().then(value=>{capability=value;render()}).catch(()=>{});void options.scanner.available().then(value=>{scanAvailable=value;if(session.state.route.type==='devices')render()}).catch(()=>{});
  return {session,voiceContext(){const route=session.state.route,workspaceId=command?.address.workspaceId??(route.type==='workspace'?route.id:null),state=workspaceId?connections?.current(workspaceId):null;if(!workspaceId||state?.status!=='ready')throw new Error('No configured workspace connection');return{workspaceId,connectionVersion:state.version}},hasUnsubmittedInput:()=>Boolean(voice.state.type!=='idle'||session.state.editor||command?.text||workspaceTitleDraft.trim()||keyDraft||connections&&connections.mutation.type!=='idle'),setUpdateAvailable(available=true){if(updateAvailable===available)return;updateAvailable=available;render()},dispose(){disposed=true;codeEditor?.dispose();connections?.dispose();appearance.dispose();dialog.close();dialog.removeEventListener('cancel',onCancel);dialog.removeEventListener('click',onBackdrop);window.removeEventListener('popstate',onBack);clearInterval(poll);unsubscribe();session.dispose();void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})};root.removeEventListener('toggle',onDisclosure,true);root.removeEventListener('click',clickHandler);root.removeEventListener('input',onInput);root.removeEventListener('submit',submitHandler);root.removeEventListener('focusout',onFocusOut);root.removeEventListener('focusin',onEditorFocus);root.removeEventListener('compositionstart',onCompositionStart);root.removeEventListener('compositionend',onCompositionEnd);document.removeEventListener('visibilitychange',visibility)}};
}
