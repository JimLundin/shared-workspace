import qrcode from 'qrcode-generator';
import type { Host } from './host.ts';
import type { Revision, Snapshot, Surface, CodeLanguage, WebApplication, SourceReference, MeetingAction, EvidenceCitation, WorkRevision, Resource } from './protocol.ts';
import { WorkspaceSession, snapshotOf, titleOf, sameHeads, decodeEditor, splitTranscript } from './session.ts';
import type { Route, State } from './session.ts';
import { captureAddress, commandPlan } from './commands.ts';
import type { CommandDraft, Intent } from './commands.ts';
import { VoiceSession } from './voice-session.ts';
import type { VoiceCapability, VoicePort } from './voice-session.ts';
import type { ScannerPort } from './scanner.ts';
import { loadPresentation, savePresentation, loadLayoutPreferences, saveLayoutPreferences } from './presentation.ts';
import { joinStageCopy, joinStages } from './pairing.ts';
import { isNoteRevision, isSurfaceRevision, isWorkRevision, decodeSurface, decodeWork, decodeMeetingActions } from './protocol.ts';
import { WorkController } from './work-controller.ts';
import { exactRecord, identity, type MeetingPreparationPort } from './meeting-preparation.ts';
import { RetainedInputJournal, type LocalDraftPort } from './retained-input.ts';
import { decodeRetainedCommand } from './retained-commands.ts';
import { surfacePresets, surfacePreset, surfaceLabel, surfacePresentation, surfacePreview, createWebSurface } from './surfaces.ts';
import { ConnectionSession, type ConnectionPort } from './connections-ui.ts';
import { mountCodeEditor } from './code-editor.ts';
import { observeAppearance, type Appearance } from './theme.ts';

const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]!));
const attr = (value: unknown): string => escape(JSON.stringify(value));
const button = (text: string, action: string, data: Record<string, unknown> = {}, className = '', disabled = false, label?:string): string => `<button type="button" ${label?`aria-label="${escape(label)}"`:''} class="${className}" data-action="${action}" data-args="${attr(data)}" ${disabled ? 'disabled' : ''}>${text}</button>`;
const mic = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M8 21h8"/></svg>';
const shareIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3m-4 4 4-4 4 4M7 10H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-2"/></svg>';
const keyboardIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h1m4 0h1m4 0h1M6 12h1m4 0h1m4 0h1M7 16h10"/></svg>';
const settingsIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 3-.6 2.1-1.8 1-2.1-.5-2 3.4 1.5 1.6v2.1L2.5 14.3l2 3.4 2.1-.5 1.8 1L9 20.3h4l.6-2.1 1.8-1 2.1.5 2-3.4-1.5-1.6v-2.1L19.5 9l-2-3.4-2.1.5-1.8-1L13 3Z"/><circle cx="11" cy="11.7" r="3"/></svg>';
const plusIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
const shellIcon = (name:'navigation'|'inspector'|'more'|'back'|'note'|'grid'):string => '<svg viewBox="0 0 24 24" aria-hidden="true">'+({navigation:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18M5 7h2m-2 4h2m-2 4h2"/>',inspector:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M15 3v18"/>',more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',back:'<path d="m14 5-7 7 7 7"/>',note:'<path d="M6 3h8l4 4v14H6zM14 3v5h5M9 12h6m-6 4h6"/>',grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'}[name])+'</svg>';
const shortId = (value: string): string => value.length > 14 ? value.slice(0, 10)+'…' : value;
const time = (ms: number): string => new Date(ms).toLocaleString();
const count = (n: number, singular: string, plural = singular + 's'): string => `${n} ${n === 1 ? singular : plural}`;

export type AppOptions = Readonly<{ host: Host; voice: VoicePort; fallbackVoice?: VoicePort; connections?: ConnectionPort; meetings?:MeetingPreparationPort; drafts?:LocalDraftPort; scanner: ScannerPort; openAppOptions?: () => void; openUpdate?: () => void; pollMs?: number; id?: () => string; initialInvite?: string; invitationUrl?: (ticket: string) => string; shareInvitation?: (url: string) => Promise<'shared' | 'cancelled'> }>;
export function mountApp(root: HTMLElement, options: AppOptions): { session: WorkspaceSession; voiceContext(): {workspaceId:string;connectionVersion:string}; hasUnsubmittedInput(): boolean; setUpdateAvailable(available?: boolean): void; dispose(): void } {
  const session = new WorkspaceSession(options.host, options.id);
  // The modal owns its top-layer lifetime; only its contents refresh with state.
  const frame = document.createElement('div'); frame.className = 'workspace-frame';
  const dialog = document.createElement('dialog'); dialog.className = 'workspace-dialog';
  root.replaceChildren(frame, dialog);
  type Panel = 'settings' | 'picker' | 'create' | 'capture' | 'connections' | 'share' | 'join' | 'appearance' | 'add' | 'voice-settings' | 'navigator' | 'page-actions' | 'inspector' | 'meeting' | 'action-correction' | 'source';
  let panel: Panel | null = null, returnFocus: HTMLElement | null = null;
  let returnFocusId = '', returnFocusAction = '', returnFocusArgs = '';
  let editorOpen = true, updateAvailable = false;
  let layout=loadLayoutPreferences();
  const touchInput=matchMedia('(pointer:coarse)');
  const shellInput=(): 'desktop'|'phone'|'tablet' => touchInput.matches ? root.clientWidth<=590?'phone':'tablet' : 'desktop';
  let renderedEditorId:string|null=null,renderedPanel:Panel|null=null;
  let journal:RetainedInputJournal|null=null,workController:WorkController|null=null,unsubscribeWork:(()=>void)|null=null,inputReady=!options.drafts;
  type MeetingDraft=Readonly<{draftId:string;workspaceId:string;title:string;outcome:string;sources:readonly SourceReference[]}> & (Readonly<{type:'create'}>|Readonly<{type:'revise';resourceId:string;expectedHeads:readonly string[]}>);
  type ActionCorrection=Readonly<{draftId:string;workspaceId:string;resourceId:string;expectedHeads:readonly string[];actions:readonly MeetingAction[]}>;
  type MeetingCapture=Readonly<{workspaceId:string;before:string;start:number;end:number}> & (Readonly<{type:'request';draftId:string;field:'title'|'outcome'}>|Readonly<{type:'action';draftId:string;resourceId:string;expectedHeads:readonly string[];actionId:string;field:'owner'|'deadline'}>);
  let captureMeeting:MeetingCapture|null=null;
  let meetingDraft:MeetingDraft|null=null,actionCorrection:ActionCorrection|null=null,sourcePreview:SourceReference|null=null,importingTranscript=false;
  let inputMode: 'voice' | 'typing' = 'voice';
  let selectedVoice=options.voice, voiceMethod:'provider'|'system'='provider';
  let connectionWorkspace:string|null=null, keyDraft='';
  const codeNode=document.createElement('div');codeNode.className='code-editor';
  let codeEditor:ReturnType<typeof mountCodeEditor>|null=null,codeIdentity:string|null=null;
  let captureRecovery=false,captureAsNote=false;
  type EditorCapture=Readonly<{draftId:string;workspaceId:string;resourceId:string|null;type:string;field:'title'|'body'|'code';before:string;start:number;end:number}>;
  let captureEditor:EditorCapture|null=null;
  let lastEditorField:'title'|'body'|'code'='body';
  let historyOwned = false, historyReturning = false;
  let afterPanelClose:(()=>void)|null=null;
  let pickerScope: readonly string[] | null = null;
  let capturedSubmission: CommandDraft | null = null;
  type ReviewFocus=Readonly<{command:CommandDraft;editor:EditorCapture|null;meeting:MeetingCapture|null;activation:number|null}>;
  let reviewFocus:ReviewFocus|null=null;
  const listScroll = new Map<string,number>();
  function focusResource(resourceId:string):void {
    const route=session.state.route,main=frame.querySelector('.main');
    if(route.type==='workspace'&&session.view().mode!=='focus')listScroll.set(route.id,main?.scrollTop??0);
    session.focus(resourceId);const current=frame.querySelector('.main');if(current)current.scrollTop=0;byId('reader-title')?.focus({preventScroll:true});
  }
  function openPanel(next: Panel, focusInput = true): void {
    reviewFocus=null;
    if(historyReturning)afterPanelClose=null;
    if (!panel) {
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      returnFocusId = returnFocus?.id ?? '';
      returnFocusAction = returnFocus?.dataset.action ?? '';
      returnFocusArgs = returnFocus?.dataset.args ?? '';
      if(!historyReturning){history.pushState({ ...history.state, workspaceDialog: true }, ''); historyOwned = true;}
    }
    panel = next; render(true);
    const first = dialog.querySelector<HTMLElement>(focusInput ? '[autofocus], input:not([type=checkbox]), textarea, .picker-option' : '[data-action=stop-voice]') ?? dialog.querySelector<HTMLElement>('[data-action=close-dialog]');
    first?.focus({ preventScroll: true });
  }
  function closePanel(fromBack = false): void {
    reviewFocus=null;
    if (!panel) return;
    cancelControlPress();
    panel = null; void voice.cancel(); dialog.close();
    if (historyOwned && !fromBack) { historyReturning=true; history.back(); }
    historyOwned = false;
    render(true);
    const actionMatch = Array.from(frame.querySelectorAll<HTMLElement>('[data-action]')).find(el => el.dataset.action === returnFocusAction && (el.dataset.args ?? '') === returnFocusArgs);
    const target = returnFocus?.isConnected ? returnFocus : (returnFocusId ? byId(returnFocusId) : null) ?? actionMatch ?? byId('navigation-toggle');
    (target?.getClientRects().length?target:byId('navigation-toggle'))?.focus({ preventScroll: true });
  }

  let lastVoicePhase='idle';
  let capability: VoiceCapability = { type: 'unavailable', reason: 'Checking this host’s voice input…' };
  let command: CommandDraft | null = null, workspaceTitleDraft = '', joinTicket = options.initialInvite ?? '', scanAvailable = false, scanning = false, scanMessage = '', localMessage = '';
  let scanEpoch = 0;
  const disclosures = new Set<string>();
  let sharingInvitation = false;
  let selectedSpaces = new Set<string>(), presentationDevice: string | null = null, deferredRender = false, composing = false, disposed = false;
  type ControlPress=Readonly<{sequence:number;phase:'pressed'|'released'}> & (Readonly<{type:'pointer';pointerId:number}>|Readonly<{type:'key';key:' '|'Enter'}>);
  let controlPress:ControlPress|null=null,pressSequence=0,pressReleaseTimer:number|undefined,pressFlushTimer:number|undefined,userActionDepth=0;
  const protectedInput=():boolean=>{const active=document.activeElement;return composing||active instanceof HTMLInputElement||active instanceof HTMLTextAreaElement||active instanceof HTMLElement&&active.isContentEditable;};
  function capturedUserAction<T>(action:()=>T):T {
    // Click/submit may finish the same press during which voice completed.
    if(!controlPress||reviewFocus?.activation!==controlPress.sequence)reviewFocus=null;
    userActionDepth++;try{return action();}finally{userActionDepth--;}
  }
  function finishControlPress(sequence:number):void {
    if(controlPress?.sequence!==sequence)return;
    controlPress=null;window.clearTimeout(pressReleaseTimer);pressReleaseTimer=undefined;
    window.clearTimeout(pressFlushTimer);
    pressFlushTimer=window.setTimeout(()=>{pressFlushTimer=undefined;if(!disposed&&!controlPress&&sequence===pressSequence&&!composing&&!document.hidden&&deferredRender)render();},0);
  }
  function releaseAfterActivation(sequence:number):void {
    window.clearTimeout(pressReleaseTimer);
    // Native click can checkpoint microtasks between listeners. Keep passive
    // observers fenced through the click's default action and submit dispatch.
    pressReleaseTimer=window.setTimeout(()=>finishControlPress(sequence),0);
  }
  function clearPressTimers():void {window.clearTimeout(pressReleaseTimer);window.clearTimeout(pressFlushTimer);pressReleaseTimer=undefined;pressFlushTimer=undefined;}
  const cancelControlPress=():void=>{if(controlPress)finishControlPress(controlPress.sequence);};
  const onControlPointerDown=(event:PointerEvent):void=>{
    reviewFocus=null;
    if(!event.isPrimary||event.button!==0)return;
    const target=event.target instanceof Element?event.target.closest('button,summary,input,textarea,select,a,[role=button],[contenteditable]'):null;
    if(!target||!root.contains(target)){cancelControlPress();return;}
    clearPressTimers();controlPress=Object.freeze({type:'pointer',pointerId:event.pointerId,sequence:++pressSequence,phase:'pressed'});
  };
  const onControlPointerUp=(event:PointerEvent):void=>{
    if(controlPress?.type!=='pointer'||event.pointerId!==controlPress.pointerId)return;
    const sequence=controlPress.sequence;controlPress=Object.freeze({...controlPress,phase:'released'});
    // Pointerup precedes click. Drag/outside releases may produce no click;
    // those still release the passive redraw fence within a bounded interval.
    window.clearTimeout(pressReleaseTimer);pressReleaseTimer=window.setTimeout(()=>finishControlPress(sequence),500);
  };
  const onControlPointerCancel=(event:PointerEvent):void=>{if(controlPress?.type==='pointer'&&controlPress.pointerId===event.pointerId)finishControlPress(controlPress.sequence);};
  const onActivationKeyDown=(event:KeyboardEvent):void=>{
    reviewFocus=null;
    const key=event.key;if((key!==' '&&key!=='Enter')||event.isComposing||composing||event.keyCode===229||event.altKey||event.ctrlKey||event.metaKey)return;
    const target=event.target instanceof Element?event.target.closest('button,summary,input,a,[role=button]'):null;
    if(!target||!root.contains(target)||target instanceof HTMLElement&&target.isContentEditable)return;
    if(key===' '&&(target.tagName==='A'||target instanceof HTMLInputElement&&!['button','submit','reset','checkbox','radio'].includes(target.type)))return;
    if(event.repeat&&controlPress?.type==='key'&&controlPress.key===key)return;
    clearPressTimers();controlPress=Object.freeze({type:'key',key,sequence:++pressSequence,phase:'pressed'});
  };
  const onActivationKeyUp=(event:KeyboardEvent):void=>{
    if(controlPress?.type!=='key'||controlPress.key!==event.key)return;
    const sequence=controlPress.sequence;controlPress=Object.freeze({...controlPress,phase:'released'});
    // Space activates on keyup; releasing here could detach its button before
    // click/default submission. A prevented activation has the same fallback.
    window.clearTimeout(pressReleaseTimer);pressReleaseTimer=window.setTimeout(()=>finishControlPress(sequence),500);
  };
  const onControlClick=(event:MouseEvent):void=>{
    const press=controlPress;if(!press)return;
    if(press.type==='pointer'){
      if(press.phase!=='released'||event instanceof PointerEvent&&event.pointerId>=0&&event.pointerId!==press.pointerId)return;
    }else if(press.key===' '&&press.phase!=='released')return;
    releaseAfterActivation(press.sequence);
  };
  let inviteQr: { ticket: string; svg: string | null } | null = null;
  const byId = <T extends HTMLElement>(id: string): T | null => root.querySelector<T>('#'+id);
  const voice = new VoiceSession({capability:()=>selectedVoice.capability(),start:(id,emit,locale)=>selectedVoice.start(id,emit,locale),stop:id=>selectedVoice.stop(id),cancel:id=>selectedVoice.cancel(id)}, (text, address) => { command = { text, address, source: 'voice' }; void retainInput().catch(retentionFailure); const field = byId<HTMLTextAreaElement>('command-text'); if (field) field.value = text; const label=root.querySelector('label[for=command-text]');if(label)label.textContent='Spoken transcript · editable'; if(!field)render();updateCommand(); }, () => {
    const finished=voice.state.type==='idle'&&lastVoicePhase!=='idle';lastVoicePhase=voice.state.type;
    // Completion can arrive inside Stop's native activation, while passive
    // rendering is fenced. Keep its captured identity until a real render.
    if(!disposed&&finished&&command?.text&&panel==='capture')reviewFocus=Object.freeze({command,editor:captureEditor,meeting:captureMeeting,activation:controlPress?.sequence??null});
    render();
  }, options.id);
  function focusReview():void {
    const pending=reviewFocus;if(!pending)return;
    if(panel!=='capture'||voice.state.type!=='idle'||command!==pending.command||captureEditor!==pending.editor||captureMeeting!==pending.meeting){reviewFocus=null;return;}
    const review=byId<HTMLButtonElement>('run-command');if(!review||review.disabled)return;
    reviewFocus=null;review.focus({preventScroll:true});
  }
  const availableSnapshot = (): Snapshot | null => session.snapshot();
  const writing = (): boolean => session.state.mutation.type === 'sending' || session.state.mutation.type === 'uncertain';
  const captureBusy = (): boolean => ['starting','listening','stopping','transcribing'].includes(voice.state.type);

  const sameSource=(a:SourceReference,b:SourceReference):boolean=>a.workspace_id===b.workspace_id&&a.resource_id===b.resource_id&&a.revision_id===b.revision_id;
  function decodeSource(value:unknown):SourceReference {
    const input=exactRecord(value,['workspace_id','resource_id','revision_id']);
    return Object.freeze({workspace_id:identity(input.workspace_id),resource_id:identity(input.resource_id),revision_id:identity(input.revision_id)});
  }
  function retainedText(value:unknown,max=4_000_000):string {
    if(typeof value!=='string'||new TextEncoder().encode(value).length>max)throw new Error('Invalid retained text');return value;
  }
  function retainedHeads(value:unknown):readonly string[] {
    if(!Array.isArray(value)||!value.length||value.length>1024)throw new Error('Invalid retained revisions');
    const heads=value.map(identity);if(new Set(heads).size!==heads.length)throw new Error('Duplicate retained revision');return Object.freeze(heads);
  }
  function decodeMeetingDraft(value:unknown):MeetingDraft|null {
    if(value===null)return null;
    const record=value as Record<string,unknown>,revise=record?.type==='revise';
    const input=exactRecord(value,['type','draftId','workspaceId','title','outcome','sources',...(revise?['resourceId','expectedHeads']:[])]);
    if(input.type!=='create'&&input.type!=='revise'||!Array.isArray(input.sources)||input.sources.length>16)throw new Error('Invalid retained meeting');
    const sources=Object.freeze(input.sources.map(decodeSource)),workspaceId=identity(input.workspaceId);
    if(sources.some(source=>source.workspace_id!==workspaceId)||sources.some((source,index)=>sources.slice(0,index).some(other=>sameSource(source,other))))throw new Error('Invalid retained sources');
    const base={draftId:identity(input.draftId),workspaceId,title:retainedText(input.title),outcome:retainedText(input.outcome),sources};
    return Object.freeze(input.type==='create'?{...base,type:'create'}:{...base,type:'revise',resourceId:identity(input.resourceId),expectedHeads:retainedHeads(input.expectedHeads)});
  }
  function decodeCorrection(value:unknown):ActionCorrection|null {
    if(value===null)return null;const input=exactRecord(value,['draftId','workspaceId','resourceId','expectedHeads','actions']);
    return Object.freeze({draftId:identity(input.draftId),workspaceId:identity(input.workspaceId),resourceId:identity(input.resourceId),expectedHeads:retainedHeads(input.expectedHeads),actions:decodeCorrectionActions(input.actions)});
  }
  function decodeCorrectionActions(value:unknown):readonly MeetingAction[] {
    if(!Array.isArray(value)||value.length>64||new Set(value.map(action=>action?.id)).size!==value.length)throw new Error('Invalid retained corrections');
    return Object.freeze(value.map(action=>{
      const raw=exactRecord(action,['id','text','owner','deadline','basis']),owner=raw.owner as Record<string,unknown>,deadline=raw.deadline as Record<string,unknown>;
      const name=owner?.type==='assigned'?retainedText(owner.name):null,text=deadline?.type==='stated'?retainedText(deadline.text):null;
      const checked=decodeMeetingActions([{...raw,owner:name!==null&&(owner.basis as {type?:unknown})?.type==='user'?{...owner,name:'Draft'}:owner,deadline:text!==null&&(deadline.basis as {type?:unknown})?.type==='user'?{...deadline,text:'Draft'}:deadline}])[0]!;
      return Object.freeze({...checked,owner:checked.owner.type==='assigned'?Object.freeze({...checked.owner,name:name!}):checked.owner,deadline:checked.deadline.type==='stated'?Object.freeze({...checked.deadline,text:text!}):checked.deadline});
    }));
  }
  function decodeCapture(value:unknown):CommandDraft|null {
    if(value===null)return null;
    const input=exactRecord(value,['text','address','source']),address=exactRecord(input.address,['workspaceId','workspaceTitle','resourceId','resourceTitle','heads']);
    if(input.source!=='typed'&&input.source!=='voice'||!Array.isArray(address.heads))throw new Error('Invalid retained capture');
    const workspaceId=address.workspaceId===null?null:identity(address.workspaceId),resourceId=address.resourceId===null?null:identity(address.resourceId);
    if(resourceId!==null&&workspaceId===null)throw new Error('Missing capture workspace');
    return Object.freeze({text:retainedText(input.text),source:input.source,address:Object.freeze({workspaceId,resourceId,workspaceTitle:retainedText(address.workspaceTitle),resourceTitle:address.resourceTitle===null?null:retainedText(address.resourceTitle),heads:Object.freeze(address.heads.map(identity))})});
  }
  function decodeCaptureTarget(value:unknown,meeting:true):MeetingCapture|null;
  function decodeCaptureTarget(value:unknown,meeting:false):typeof captureEditor;
  function decodeCaptureTarget(value:unknown,meeting:boolean):MeetingCapture|typeof captureEditor {
    if(value===null)return null;
    if(meeting){
      const raw=value as Record<string,unknown>,request=raw?.type==='request';
      const input=exactRecord(value,['type','workspaceId','field','before','start','end','draftId',...(request?[]:['resourceId','expectedHeads','actionId'])]);
      const before=retainedText(input.before),start=input.start,end=input.end;
      if(typeof start!=='number'||typeof end!=='number'||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<start||end>before.length)throw new Error('Invalid work selection');
      const base={workspaceId:identity(input.workspaceId),before,start,end};
      if(request){if(input.field!=='title'&&input.field!=='outcome')throw new Error('Invalid request field');return Object.freeze({...base,type:'request',draftId:identity(input.draftId),field:input.field});}
      if(input.type!=='action'||input.field!=='owner'&&input.field!=='deadline')throw new Error('Invalid action field');
      return Object.freeze({...base,type:'action',draftId:identity(input.draftId),resourceId:identity(input.resourceId),expectedHeads:retainedHeads(input.expectedHeads),actionId:identity(input.actionId),field:input.field});
    }
    const input=exactRecord(value,['draftId','workspaceId','field','before','start','end','resourceId','type']);
    const before=retainedText(input.before),start=input.start,end=input.end;
    if(typeof start!=='number'||typeof end!=='number'||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<start||end>before.length)throw new Error('Invalid captured selection');
    const base={draftId:identity(input.draftId),workspaceId:identity(input.workspaceId),before,start,end};
    if(meeting)throw new Error('Use the work field decoder');
    if(input.field!=='title'&&input.field!=='body'&&input.field!=='code'||!['new','existing','surface_new','surface_existing'].includes(String(input.type)))throw new Error('Invalid editor field');
    return Object.freeze({...base,field:input.field,type:String(input.type),resourceId:input.resourceId===null?null:identity(input.resourceId)});
  }
  const retentionFailure=():void=>{if(disposed)return;const message='Private input could not be retained on this device. Keep this window open and try saving again.';if(localMessage!==message){localMessage=message;render();}};
  async function retainInput():Promise<void> {
    if(!inputReady||!journal||disposed)return;
    const state=session.state,pending=state.mutation.type==='sending'||state.mutation.type==='uncertain'?state.mutation.command:null;
    await Promise.all([
      journal.put('editor-current','editor',{editor:state.editor,pending}),
      journal.put('capture-current','capture',{command,captureEditor,captureMeeting,captureRecovery,captureAsNote,inputMode,capturedSubmission}),
      journal.put('meeting-ui','meeting',{draft:meetingDraft,correction:actionCorrection}),
    ]);
  }
  if(options.drafts)session.retainWith(async(editor,pending)=>{
    if(!inputReady||!journal)throw new Error('Local input recovery is not ready');
    await journal.put('editor-current','editor',{editor,pending});
  });
  async function initializeInput():Promise<void> {
    try {
      await session.refresh();if(disposed)return;
      if(options.drafts){
        const opened=await RetainedInputJournal.open(options.drafts);if(disposed)return;
        const read=(id:string,kind:'editor'|'capture'|'meeting')=>{const raw=opened.read(id,kind);return raw===null?null:JSON.parse(raw) as unknown;};
        const editorValue=read('editor-current','editor'),captureValue=read('capture-current','capture'),meetingValue=read('meeting-ui','meeting');
        const editorInput=editorValue===null?null:exactRecord(editorValue,['editor','pending']);
        const editor=editorInput?decodeEditor(editorInput.editor):null,pending=editorInput&&editorInput.pending!==null?decodeRetainedCommand(editorInput.pending):null;
        const meeting=meetingValue===null?null:exactRecord(meetingValue,['draft','correction']);
        const restoredDraft=meeting?decodeMeetingDraft(meeting.draft):null,restoredCorrection=meeting?decodeCorrection(meeting.correction):null;
        const captured=captureValue===null?null:exactRecord(captureValue,['command','captureEditor','captureMeeting','captureRecovery','captureAsNote','inputMode','capturedSubmission']);
        const restoredCommand=captured?decodeCapture(captured.command):null,restoredSubmission=captured?decodeCapture(captured.capturedSubmission):null;
        const restoredEditorTarget=captured?decodeCaptureTarget(captured.captureEditor,false):null,restoredMeetingTarget=captured?decodeCaptureTarget(captured.captureMeeting,true):null;
        if(captured&&(typeof captured.captureRecovery!=='boolean'||typeof captured.captureAsNote!=='boolean'||captured.inputMode!=='typing'&&captured.inputMode!=='voice'||restoredEditorTarget&&restoredMeetingTarget))throw new Error('Invalid retained capture state');
        // Validate every slot before installing any of it. Invalid data is left intact for recovery.
        journal=opened;meetingDraft=restoredDraft;actionCorrection=restoredCorrection;command=restoredCommand;
        capturedSubmission=restoredSubmission&&JSON.stringify(restoredSubmission)===JSON.stringify(restoredCommand)?restoredCommand:restoredSubmission;
        captureEditor=restoredEditorTarget;captureMeeting=restoredMeetingTarget;captureRecovery=captured?.captureRecovery===true;captureAsNote=captured?.captureAsNote===true;inputMode=captured?.inputMode==='typing'?'typing':'voice';
        session.restoreInput(editor,pending);
        if(editor){session.navigate({type:'workspace',id:editor.workspaceId});editorOpen=true;}
      }
      workController=new WorkController(session,{...(options.meetings?{provider:options.meetings}:{}),...(journal?{journal}:{}),...(options.id?{id:options.id}:{})});
      unsubscribeWork=workController.subscribe(()=>render());await workController.restore();if(disposed)return;
      inputReady=true;render();
    } catch {if(disposed)return;localMessage='Saved private input could not be restored safely. Its stored data has been kept; reopen with recovery support before making changes.';render();}
  }
  async function importTranscripts(files:readonly File[]):Promise<void> {
    const draft=meetingDraft;if(!draft||!files.length||importingTranscript||!inputReady||writing()||workController?.busy)return;
    if(files.reduce((size,file)=>size+file.size,0)>16*128*1024){localMessage='The transcripts exceed the 16-source limit. Split them into separate work requests; no text was imported.';render();return;}
    importingTranscript=true;render();
    try {
      // Validate the complete selection before sending its first note. A bad file
      // cannot silently disappear from a multi-recording request.
      const imports=await Promise.all(files.map(async file=>{
        let text:string;try{text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}catch{throw new Error('Could not read '+file.name+' as UTF-8 text. No files were imported.');}
        const parts=splitTranscript(text);if(!parts.length||text.includes('\0'))throw new Error('Choose nonempty UTF-8 text transcripts. '+file.name+' could not be imported; no files were imported.');
        return{file,parts};
      }));
      const total=imports.reduce((count,item)=>count+item.parts.length,0);
      if(total+draft.sources.length>16)throw new Error('These transcripts need more than the 16 available source slots. No text was imported.');
      let confirmed=0;
      for(const {file,parts} of imports){
        const stem=splitTranscript(file.name,80)[0]??'Transcript';
        for(let index=0;index<parts.length;index++){
          if(disposed)return;
          const id=options.id??(()=>crypto.randomUUID()),resourceId=id(),operationId=id();
          const command=Object.freeze({type:'create_resource' as const,operation_id:operationId,workspace_id:draft.workspaceId,resource_id:resourceId,title:stem+(parts.length>1?' · '+(index+1)+' of '+parts.length:''),body:parts[index]!});
          const receipt=await session.submit(command);
          if(disposed)return;
          if(receipt?.type!=='applied'){localMessage=`Import paused after ${confirmed} confirmed parts. Check the pending save; confirmed notes remain in Material. Select their saved revisions here to continue.`;return;}
          confirmed++;
          if(meetingDraft?.draftId===draft.draftId){meetingDraft=Object.freeze({...meetingDraft,sources:Object.freeze([...meetingDraft.sources,Object.freeze({workspace_id:receipt.workspace_id,resource_id:command.resource_id,revision_id:receipt.operation_id})])});await retainInput();}
        }
      }
      localMessage=total===1?'Transcript imported and pinned.':`${total} transcript parts imported and pinned from ${files.length} ${files.length===1?'file':'files'}. No text was truncated.`;
    } catch(error){localMessage=error instanceof Error?error.message:'The transcripts could not be imported.';}
    finally {importingTranscript=false;render();}
  }
  const onFileChange=(event:Event):void=>{const field=event.target;if(field instanceof HTMLInputElement&&field.id==='transcript-file'&&field.files?.length)void importTranscripts([...field.files]);};
  root.addEventListener('change',onFileChange);

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
  function correctedAction(action:MeetingAction,field:'owner'|'deadline',value:string):MeetingAction {
    const current=field==='owner'?action.owner.type==='assigned'?action.owner.name:'':action.deadline.type==='stated'?action.deadline.text:'';if(current===value)return action;
    return Object.freeze(field==='owner'?{...action,owner:value?{type:'assigned' as const,name:value,basis:{type:'user' as const}}:{type:'unassigned' as const}}:{...action,deadline:value?{type:'stated' as const,text:value,basis:{type:'user' as const}}:{type:'unspecified' as const}});
  }
  function workFieldValue(target:MeetingCapture):string|null {
    if(target.type==='request')return meetingDraft?.draftId===target.draftId&&meetingDraft.workspaceId===target.workspaceId?meetingDraft[target.field]:null;
    if(!actionCorrection||actionCorrection.draftId!==target.draftId||actionCorrection.resourceId!==target.resourceId||actionCorrection.workspaceId!==target.workspaceId||!sameHeads(actionCorrection.expectedHeads,target.expectedHeads))return null;
    const action=actionCorrection.actions.find(action=>action.id===target.actionId);return !action?null:target.field==='owner'?action.owner.type==='assigned'?action.owner.name:'':action.deadline.type==='stated'?action.deadline.text:'';
  }
  async function insertDictation(append=false):Promise<void> {
    if(captureMeeting&&command?.text){
      const target=captureMeeting,current=workFieldValue(target);
      if(current===null||!append&&current!==target.before){captureRecovery=true;localMessage='The original work draft changed. Your transcript is kept for review.';render();return;}
      const text=append?current+(current&&!/\s$/.test(current)?' ':'')+command.text:current.slice(0,target.start)+command.text+current.slice(target.end);
      if(target.type==='request'&&meetingDraft)meetingDraft=Object.freeze({...meetingDraft,[target.field]:text});
      if(target.type==='action'&&actionCorrection)actionCorrection=Object.freeze({...actionCorrection,actions:Object.freeze(actionCorrection.actions.map(action=>action.id!==target.actionId?action:correctedAction(action,target.field,text)))});
      command=null;captureMeeting=null;captureRecovery=false;captureAsNote=false;
      await retainInput();openPanel(target.type==='request'?'meeting':'action-correction');return;
    }
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
    const action=captureEditor||captureMeeting?'Insert into draft':plan.type==='ready'?(plan.intent.type==='capture'?'Save as note':plan.label):'Use this input';
    return `<section class="voice" aria-label="Voice capture"><p class="capture-address">${escape(address.workspaceTitle)}${address.resourceId ? ` / ${escape(address.resourceTitle)}` : ''}</p>
      ${isStale ? `<div class="notice">This input belongs to ${escape(address.workspaceTitle)}. ${button('Return to '+escape(address.workspaceTitle),'return-command')}</div>` : ''}
      ${inputMode==='voice'?`<div class="voice-stage ${completed?'complete':''}" data-phase="${phase}"><div class="voice-disc" aria-hidden="true">${mic}</div><h3>${heading}</h3><p class="voice-status" role="status">${escape(phase==='idle'&&!command?.text&&voice.state.message==='Voice starts only when you choose it.'?(capability.type==='unavailable'?capability.reason:'Speak naturally. Stop when you’re ready to review.'):voice.state.message)}</p>
      ${!completed?`<div class="capture-actions">${phase==='listening'||phase==='starting'||phase==='stopping'?button('Stop recording','stop-voice',{},'primary'):phase==='transcribing'?'':phase==='system_dictation'?button('Finish input','stop-voice',{},'primary'):button(command?.text?'Record again':'Start speaking','speak',{},'primary',capability.type==='unavailable'||Boolean(isStale))}${captureBusy()?button('Cancel recording','cancel-voice',{},'quiet'):''}</div>`:''}</div>`:''}
      ${showText?`<form id="command-form"><label for="command-text">${command?.source==='voice'?'Spoken transcript · editable':'Your words'}</label><textarea id="command-text" maxlength="12000" placeholder="An idea, a note, or something to open…" enterkeyhint="done" ${captureBusy()||writing()?'readonly':''}>${escape(command?.text??'')}</textarea><div id="command-preview" class="command-preview">${escape(captureMeeting?'Insert literally into the captured work field.':captureEditor?'Insert into the original '+captureEditor.field+' field.':plan.type==='ready'?plan.label:plan.type==='unavailable'?plan.reason:'Capture a thought or name something to open.')}</div><button id="run-command" class="primary" type="submit" ${(!command?.text.trim()||!captureEditor&&!captureMeeting&&plan.type!=='ready')||writing()||captureBusy()?'disabled':''}>${escape(action)}</button></form>`:''}
      ${captureRecovery&&(captureEditor||captureMeeting)?`<div class="notice"><p>Your transcript is retained.</p>${(captureEditor&&session.state.editor?.draftId===captureEditor.draftId||captureMeeting&&workFieldValue(captureMeeting)!==null)?button('Append to current draft','append-dictation',{},'primary'):''}${button('Keep as a separate note','dictation-note',{},'quiet')}</div>`:''}
      <div class="capture-secondary">${completed?button('Record again','speak',{},'quiet'): ''}${options.connections&&voiceMethod==='provider'?button('OpenAI connection','configure-voice',{},'quiet connection-entry'):''}${inputMode==='voice'?button(keyboardIcon+'<span>Type instead</span>','type-command',{},'quiet'):button(mic+'<span>Use voice</span>','voice-mode',{},'quiet')}${command?.text?button('New capture','new-command',{},'quiet'):''}${options.fallbackVoice?button(voiceMethod==='provider'?'Use device speech':'Use OpenAI transcription','switch-voice',{},'quiet'):''}</div>
      <details class="examples" data-disclosure="voice-help" ${disclosures.has('voice-help')?'open':''}><summary>Voice & available actions</summary><p>Speak a note, or say “Open [name]”, “Create workspace [name]”, “New note”, or “Synchronize”. The action is shown before you confirm it.</p><p>${escape(nativeDetail)}</p></details>
    </section>`;
  }
  function captureDock(snapshot:Snapshot): string {
    const route=session.state.route,view=session.view(),workspaceId=route.type==='workspace'?route.id:null;
    const resource=view.focused?snapshot.resources.find(r=>r.id===view.focused):null;
    return `<div class="capture-dock" aria-label="Current page controls">${updateAvailable&&options.openUpdate?'<button type="button" id="workspace-update" data-action="update" aria-label="Update available"><span class="update-full">Update available</span><span class="update-short" aria-hidden="true">Update</span></button>':''}${workspaceId?`${view.mode==='focus'?button(shellIcon('back')+'<span>All material</span>','all-work',{},'page-primary',false,'All material'):button(plusIcon+'<span>Add material</span>','add',{},'page-primary',writing(),'Add material')}<button type="button" class="page-actions icon-button" data-action="page-actions" aria-label="Page actions" aria-haspopup="dialog">${shellIcon('more')}</button>${button(mic+`<span>${command?.text?'Review capture':'Speak'}</span>`,command?.text?'review-capture':'speak',{},'primary voice-primary'+(command?.text?' has-capture':''))}${resource?`<button type="button" class="inspector-toggle icon-button" data-action="inspector" aria-label="Related details" aria-controls="workspace-inspector" aria-expanded="${layout.inspector==='shown'}">${shellIcon('inspector')}</button>`:''}`:''}</div>`;
  }
  function materialNavigation(snapshot:Snapshot):string {
    const route=session.state.route,workspace=route.type==='workspace'?snapshot.workspaces.find(w=>w.id===route.id):null,view=session.view();
    const resources=workspace?.placements.flatMap(p=>{const r=snapshot.resources.find(r=>r.id===p.resource_id);return r?[r]:[]})??[];
    const group=(label:string,items:readonly Resource[])=>`<h2 class="navigation-heading">${label} <span>${items.length}</span></h2>${items.map(r=>`<button type="button" class="navigation-row" data-action="focus" data-args="${attr({resourceId:r.id})}" ${view.mode==='focus'&&view.focused===r.id?'aria-current="page"':''}>${shellIcon(label==='Work'?'grid':'note')}<span>${escape(titleOf(r))}</span></button>`).join('')}`;
    return `<div class="navigation-routes">${button(shellIcon('grid')+'<span>Workspaces</span>','picker',{},'navigation-row')}${workspace?`<h2 class="navigation-heading">${escape(workspace.title)}</h2><button type="button" class="navigation-row" data-action="all-work" ${view.mode!=='focus'?'aria-current="page"':''}>${shellIcon('grid')}<span>All material</span></button>${group('Work',resources.filter(r=>r.heads.some(isWorkRevision)))}${group('Material',resources.filter(r=>!r.heads.some(isWorkRevision)))}`:''}</div>`;
  }
  function navigatorPanel(snapshot:Snapshot):string {
    const route=session.state.route;
    return `<nav aria-label="Work navigation">${materialNavigation(snapshot)}<div class="navigation-utilities">${route.type==='workspace'?button(shareIcon+'<span>Share workspace</span>','share',{workspaceId:route.id},'navigation-row'):''}${button(settingsIcon+'<span>Settings</span>','settings',{},'navigation-row')}</div></nav>`;
  }
  function pageActions(snapshot:Snapshot):string {
    const route=session.state.route,view=session.view(),r=snapshot.resources.find(r=>r.id===view.focused);
    return `<div class="page-menu">${button(keyboardIcon+'<span>Type instead</span>','type-command',{},'navigation-row')}${route.type==='workspace'?button(plusIcon+'<span>Add to workspace</span>','add',{},'navigation-row'):''}${view.mode==='focus'&&r?`${r.heads.length===1&&!isWorkRevision(r.heads[0]!)?button('Edit','edit',{resourceId:r.id},'navigation-row',writing()):''}${button('Related details','inspector',{},'navigation-row')}`:''}${view.closed.length?button('Reopen views','reopen',{},'navigation-row'):''}</div>`;
  }
  function inspectorContent(snapshot:Snapshot):string {
    const view=session.view(),resource=snapshot.resources.find(r=>r.id===view.focused);
    if(!resource)return '<p class="context-note">Open material to inspect its source and revisions.</p>';
    const workspace=snapshot.workspaces.find(w=>w.id===resource.workspace_id);
    return `<h2>${escape(titleOf(resource))}</h2><dl><dt>Source workspace</dt><dd>${escape(workspace?.title??'Unavailable workspace')}</dd><dt>Revision history</dt><dd>${count(resource.history.length,'revision')}</dd></dl>${resource.heads.length>1?'<p class="notice">Several versions need review. Every version is retained.</p>':''}<h3>Referenced in</h3><ul>${snapshot.workspaces.filter(w=>w.placements.some(p=>p.resource_id===resource.id)).map(w=>`<li>${escape(w.title)}</li>`).join('')}</ul><p class="context-note">A reference preserves the source workspace’s access.</p>${button('Organize & history','organize',{resourceId:resource.id},'quiet')}`;
  }
  function applyLayout():void {
    const shell=frame.querySelector<HTMLElement>('.app-shell');if(!shell)return;
    const input=shellInput();shell.dataset.input=input;shell.dataset.hand=layout.hand;shell.dataset.navigation=layout.navigation;shell.dataset.inspector=layout.inspector;
    const sidebar=frame.querySelector<HTMLElement>('.workspace-navigation'),toggle=byId('navigation-toggle');
    const visible=Boolean(sidebar&&getComputedStyle(sidebar).display!=='none');
    toggle?.setAttribute('aria-expanded',String(visible));toggle?.setAttribute('aria-label',input==='desktop'&&root.clientWidth>1000?visible?'Hide navigation':'Show navigation':'Open navigation');
    if(sidebar&&!visible&&sidebar.contains(document.activeElement))toggle?.focus({preventScroll:true});
    const viewport=window.visualViewport,keyboard=viewport?Math.max(0,innerHeight-viewport.height-viewport.offsetTop):0;
    shell.style.setProperty('--keyboard-inset',keyboard+'px');
    shell.style.setProperty('--page-controls-height',(frame.querySelector('.capture-dock')?.getBoundingClientRect().height??56)+'px');
    frame.querySelector('.inspector-toggle')?.setAttribute('aria-expanded',String(input==='desktop'&&root.clientWidth>740&&layout.inspector==='shown'));
    dialog.dataset.input=input;dialog.dataset.hand=layout.hand;
    if(panel&&(input==='desktop')){
      const trigger=panel==='navigator'?byId('navigation-toggle'):panel==='picker'?byId('workspace-picker'):panel==='page-actions'?frame.querySelector<HTMLElement>('.page-actions'):null;
      if(trigger){const r=trigger.getBoundingClientRect(),width=Math.min(360,innerWidth-24);dialog.style.width=width+'px';dialog.style.left=Math.max(12,Math.min(innerWidth-width-12,r.left))+'px';dialog.style.top=(r.bottom+8)+'px';dialog.style.bottom='auto';dialog.style.right='auto';dialog.style.margin='0';}
      else dialog.removeAttribute('style');
    }else dialog.removeAttribute('style');
  }
  function updateCommand(): void {
    const snapshot = availableSnapshot(); if (!snapshot) return;
    const plan = planCapture(snapshot), preview = byId('command-preview'), run = byId<HTMLButtonElement>('run-command');
    if (preview) preview.textContent = captureMeeting?'Insert literally into the captured work field.':captureEditor?'Insert into the original '+captureEditor.field+' field.':plan.type === 'ready' ? plan.label : plan.type === 'unavailable' ? plan.reason : 'Speak or type a supported command.';
    if (run) {run.disabled = !command?.text.trim() || (!captureEditor&&!captureMeeting&&plan.type !== 'ready') || writing() || captureBusy();run.textContent=captureEditor||captureMeeting?'Insert into draft':plan.type==='ready'?(plan.intent.type==='capture'?'Save as note':plan.label):'Use this input';}
  }
  function settingsRow(label:string,action:string,detail=''):string {
    return `<button type="button" class="settings-row" data-action="${action}" aria-label="${escape(label)}"><span>${escape(label)}</span><span class="settings-row-end">${detail?`<span class="settings-row-value">${escape(detail)}</span>`:''}<span class="settings-chevron" aria-hidden="true">›</span></span></button>`;
  }
  function picker(snapshot: Snapshot): string {
    const route=session.state.route;
    return `${pickerScope ? '<p>Choose which received workspace to open.</p>' : ''}
      <section class="settings-group" aria-labelledby="workspace-group-title"><h3 id="workspace-group-title">Workspaces</h3><div class="workspace-list">${snapshot.workspaces.filter(w => !pickerScope || pickerScope.includes(w.id)).map(w => `<button class="picker-option" type="button" aria-label="${escape(w.title)}" data-action="navigate" data-args="${attr({type:'workspace', id:w.id})}" ${session.state.route.type === 'workspace' && session.state.route.id === w.id ? 'aria-current="true"' : ''}><span class="space-mark" aria-hidden="true">${escape(w.title.slice(0,1).toUpperCase())}</span><span>${escape(w.title)}</span></button>`).join('')}</div></section>
      ${route.type==='workspace'?`<section class="settings-group"><h3>Current workspace</h3>${button('Share this workspace','share',{workspaceId:route.id},'settings-row')}</section>`:''}<section class="settings-group" aria-labelledby="workspace-actions-title"><h3 id="workspace-actions-title">Add workspace</h3>${settingsRow('Create workspace','create-space')}${settingsRow('Join existing work','join-work')}</section>`;
  }
  function settingsPanel(snapshot:Snapshot):string {
    const selected=appearance.get(),appearanceLabel=selected[0]!.toUpperCase()+selected.slice(1),route=session.state.route;
    const workspace=route.type==='workspace'?snapshot.workspaces.find(w=>w.id===route.id):null;
    return `<section class="settings-group" aria-labelledby="device-settings-title"><h3 id="device-settings-title">This device</h3>${settingsRow('Appearance','appearance',appearanceLabel)}<label class="settings-row" for="preferred-hand">Preferred hand<select id="preferred-hand"><option value="right" ${layout.hand==='right'?'selected':''}>Right</option><option value="left" ${layout.hand==='left'?'selected':''}>Left</option></select></label>${settingsRow('Connections','connections')}${options.openAppOptions?settingsRow('Install and storage','app-options'):''}</section>${options.connections&&workspace?`<section class="settings-group" aria-labelledby="current-workspace-settings-title"><h3 id="current-workspace-settings-title">${escape(workspace.title)}</h3>${settingsRow('OpenAI','configure-voice','Dictation & meeting preparation')}</section>`:''}`;
  }
  function appearancePanel():string {
    const selected=appearance.get();
    const descriptions={system:'Match this device',light:'A bright background',dark:'A dim background'};
    return `<p class="context-note">Choose the appearance for this device.</p><div class="appearance-options" role="group" aria-label="Color theme">${(['system','light','dark'] as const).map(value=>`<button type="button" class="appearance-row" data-action="set-theme" data-args="${attr({value})}" aria-label="${value[0]!.toUpperCase()+value.slice(1)}" aria-pressed="${selected===value}"><span class="theme-swatch" data-preview="${value}" aria-hidden="true"></span><span class="appearance-copy"><span>${value[0]!.toUpperCase()+value.slice(1)}</span><small>${descriptions[value]}</small></span><span class="appearance-check" aria-hidden="true">${selected===value?'✓':''}</span></button>`).join('')}</div>`;
  }
  function addPanel(_snapshot:Snapshot):string {
    const route=session.state.route;if(route.type!=='workspace')return '';
    return `<div class="material-choices">${button('<span>Meeting work</span><small>Prepare notes, actions and an unsent follow-up from transcripts</small>','new-meeting',{workspaceId:route.id},'material-choice',!workController||!inputReady)}${button('<span>Note</span><small>Capture a thought or write something down</small>','new-note',{workspaceId:route.id},'material-choice')}${surfacePresets.map(preset=>button(`<span>${escape(preset.title)}</span><small>${escape(preset.description)}</small>`,'add-surface',{preset:preset.id,workspaceId:route.id},'material-choice')).join('')}${button('<span>Document or website link</span><small>Bring an existing document into this workspace</small>','add-link',{workspaceId:route.id},'material-choice')}</div>`;
  }
  function citations(values:readonly EvidenceCitation[],snapshot:Snapshot):string {
    return values.map(c=>`<blockquote class="work-citation"><p>${escape(c.quote)}</p>${button(escape(snapshot.resources.find(r=>r.id===c.source.resource_id)?.history.find(h=>h.id===c.source.revision_id)?.title??'Open pinned source'),'inspect-source',{source:c.source},'quiet')}</blockquote>`).join('');
  }
  function workNotice():string {
    const state=workController?.state;if(!state||state.type==='idle')return '';
    if(state.type==='saving')return '<p class="work-status" role="status">Saving the addressed work change…</p>';
    if(state.type==='recovering')return '<p class="work-status" role="status">Loading the confirmed saved work…</p>';
    if(state.type==='discarding')return '<p class="work-status" role="status">Discarding the interrupted input on this device…</p>';
    if(state.type==='preparing')return `<p class="work-status" role="status">Preparing meeting material. The result will need your review. ${button('Stop preparation','interrupt-work',{},'quiet')}</p>`;
    if(state.type==='committed')return `<div class="notice" role="status">${escape(state.message)} ${button('Refresh saved work','retry-work',{},'primary')}</div>`;
    if(state.type==='uncertain')return `<div class="notice" role="status">The last work change has no confirmed receipt. ${button('Check the same change','retry-work',{},'primary')}</div>`;
    if(state.type==='conflict')return `<div class="notice">This work changed before your change could be saved. Your local attempt is retained. ${button('Review current work','review-work',{},'quiet')}</div>`;
    return `<div class="notice" role="status">${escape(state.message)}</div>`;
  }
  function meetingWork(resource:Resource,revision:WorkRevision,snapshot:Snapshot,focused:boolean):string {
    const work=revision.work,state=work.state;
    const label=state.type==='requested'?'Ready to prepare':state.type==='preparing'?'Preparation started':state.type==='needs_input'?'Needs your input':'Material ready for review';
    if(!focused)return `<div class="paper work-preview"><span class="work-status">${label}</span><h2>${button(escape(revision.title),'focus',{resourceId:resource.id},'material-title')}</h2><p>${escape(work.outcome)}</p><p class="context-note">${count(work.sources.length,'pinned source')}</p></div>`;
    const busy=!inputReady||workController?.busy||workController?.state.type==='uncertain'||workController?.state.type==='committed'||workController?.state.type==='conflict';
    return `<section class="paper meeting-work" data-work-id="${escape(resource.id)}"><p class="work-status">${label}</p><h1 id="reader-title" tabindex="-1">${escape(revision.title)}</h1><p class="work-outcome">${escape(work.outcome)}</p><div class="row work-actions">${state.type==='requested'||state.type==='needs_input'?button('Prepare meeting material','prepare-work',{resourceId:resource.id},'primary',busy||!workController||!work.sources.length):state.type==='preparing'&&state.run.originating_device===snapshot.device_id?button('Resume this preparation','resume-work',{resourceId:resource.id},'primary',busy||!workController):''}${button('Change outcome or sources','revise-work',{resourceId:resource.id},'quiet',busy)}</div>${state.type==='needs_input'?`<div class="notice">${escape(state.question)}</div>`:''}${state.type==='preparing'?`<p class="context-note">Preparation was started on ${escape(state.run.originating_device===snapshot.device_id?'this device':snapshot.peers.find(p=>p.id===state.run.originating_device)?.name??'another device')}. No completed result is recorded yet. Resuming requires an explicit action on that originating host.</p>`:''}
      ${state.type==='materials_ready'?`<section class="work-results"><h2>Meeting notes</h2>${button('Open current notes','open-resource',{workspaceId:state.notes.reference.workspace_id,resourceId:state.notes.reference.resource_id},'border-button')}${citations(state.notes.citations,snapshot)}<div class="row between"><h2>Assigned actions</h2>${button('Correct owners or deadlines','correct-actions',{resourceId:resource.id},'quiet',busy)}</div>${state.actions.length?state.actions.map(action=>`<article class="meeting-action"><h3>${escape(action.text)}</h3><dl><dt>Owner</dt><dd>${escape(action.owner.type==='assigned'?action.owner.name:'Unassigned')}${action.owner.type==='assigned'&&action.owner.basis.type==='user'?' · user correction':''}</dd><dt>Deadline</dt><dd>${escape(action.deadline.type==='stated'?action.deadline.text:'Not specified')}${action.deadline.type==='stated'&&action.deadline.basis.type==='user'?' · user correction':''}</dd></dl><details><summary>Evidence for this action</summary>${action.basis.type==='transcript'?citations(action.basis.citations,snapshot):'<p>Action text corrected by a person.</p>'}${action.owner.type==='assigned'&&action.owner.basis.type==='transcript'?citations(action.owner.basis.citations,snapshot):''}${action.deadline.type==='stated'&&action.deadline.basis.type==='transcript'?citations(action.deadline.basis.citations,snapshot):''}</details></article>`).join(''):'<p>No actions were identified in the source.</p>'}<h2>Follow-up · unsent text</h2><p class="context-note">Shared text for review. No email or Outlook item has been created.</p>${button('Open and edit follow-up','open-resource',{workspaceId:state.follow_up.material.reference.workspace_id,resourceId:state.follow_up.material.reference.resource_id},'border-button')}${citations(state.follow_up.material.citations,snapshot)}</section>`:''}
      <section class="work-sources"><h2>Pinned sources</h2>${work.sources.length?work.sources.map(source=>button(escape(snapshot.resources.find(r=>r.id===source.resource_id)?.history.find(h=>h.id===source.revision_id)?.title??'Retained source'),'inspect-source',{source},'source-link')).join(''):'<p>Add transcript notes or import text before preparing this work.</p>'}</section></section>`;
  }
  function meetingForm(snapshot:Snapshot):string {
    const draft=meetingDraft;if(!draft)return '<p>Choose a workspace for this work.</p>';
    const sources=snapshot.resources.filter(r=>r.workspace_id===draft.workspaceId).flatMap(r=>r.heads.filter(isNoteRevision).map(h=>({source:{workspace_id:r.workspace_id,resource_id:r.id,revision_id:h.id},title:h.title,body:h.body})));
    const same=(a:SourceReference,b:SourceReference)=>a.workspace_id===b.workspace_id&&a.resource_id===b.resource_id&&a.revision_id===b.revision_id;
    return `<form id="meeting-form"><p class="capture-address">${escape(snapshot.workspaces.find(w=>w.id===draft.workspaceId)?.title??'Original workspace')}</p><label for="meeting-title">Work title</label><input id="meeting-title" required maxlength="120" value="${escape(draft.title)}">${button(mic+'<span>Dictate title</span>','dictate-title',{},'quiet')}<label for="meeting-outcome">Desired outcome</label><textarea id="meeting-outcome" required maxlength="4096" placeholder="What should this meeting produce?">${escape(draft.outcome)}</textarea>${button(mic+'<span>Dictate outcome</span>','dictate-outcome',{},'quiet')}<h3>Transcript sources</h3><p class="context-note">Select exact saved revisions. Later source edits won’t silently change this request.</p>${[...sources,...draft.sources.filter(source=>!sources.some(item=>same(source,item.source))).map(source=>({source,title:'Earlier revision: '+(snapshot.resources.find(r=>r.id===source.resource_id)?.history.find(h=>h.id===source.revision_id)?.title??'Unavailable source'),body:''}))].map((item,index)=>`<label class="workspace-check"><input type="checkbox" id="meeting-source-${index}" data-meeting-source="${attr(item.source)}" ${draft.sources.some(source=>same(source,item.source))?'checked':''}>${escape(item.title)}</label>`).join('')}<label for="transcript-file">Import transcript files</label><input id="transcript-file" type="file" multiple accept="text/plain,.txt,.md,.vtt,.srt" ${importingTranscript?'disabled':''}><p class="context-note">Choose one or more UTF-8 text transcripts. Every file becomes real notes in this workspace; long text is split without truncation. You can add more files afterward.</p><button type="submit" class="primary" ${!inputReady||importingTranscript||workController?.state.type==='saving'?'disabled':''}>${draft.type==='create'?'Create work':'Save revised request'}</button>${button('Discard work draft','discard-meeting',{},'quiet')}</form>`;
  }
  function correctionForm():string {
    const draft=actionCorrection;if(!draft)return '';
    const current=availableSnapshot()?.resources.find(resource=>resource.id===draft.resourceId&&resource.workspace_id===draft.workspaceId),changed=current&&!sameHeads(draft.expectedHeads,current.heads.map(head=>head.id));
    const comparison=changed?`<section class="comparison"><h3>Saved actions changed</h3><p>Your corrections are kept. Review every current version before saving against it.</p>${current.heads.map(head=>`<article><h4>${escape(head.title)}</h4>${isWorkRevision(head)&&head.work.state.type==='materials_ready'?`<ul>${head.work.state.actions.map(action=>`<li>${escape(action.text)} · ${escape(action.owner.type==='assigned'?action.owner.name:'Unassigned')} · ${escape(action.deadline.type==='stated'?action.deadline.text:'No deadline')}</li>`).join('')}</ul>`:'<p>This version has a different request or preparation state. Return to the work to review it.</p>'}</article>`).join('')}${button('Save my reviewed corrections','reconcile-actions',{heads:current.heads.map(head=>head.id)},'primary',Boolean(workController?.busy))}</section>`:'';
    return `<form id="action-correction-form"><p>Corrections retain their work and observed revision. Changed values are attributed to you, not to transcript evidence.</p>${draft.actions.map((action,index)=>`<fieldset class="action-edit"><legend>${escape(action.text)}</legend><label for="owner-${index}">Owner</label><input id="owner-${index}" data-correction="owner" data-action-index="${index}" value="${escape(action.owner.type==='assigned'?action.owner.name:'')}" placeholder="Unassigned">${button('Dictate owner','dictate-action',{actionId:action.id,field:'owner'},'quiet')}<label for="deadline-${index}">Deadline</label><input id="deadline-${index}" data-correction="deadline" data-action-index="${index}" value="${escape(action.deadline.type==='stated'?action.deadline.text:'')}" placeholder="Not specified">${button('Dictate deadline','dictate-action',{actionId:action.id,field:'deadline'},'quiet')}</fieldset>`).join('')}${comparison}<button type="submit" class="primary" ${changed?'disabled':''}>Save corrections</button>${button('Discard corrections','discard-correction',{},'quiet')}</form>`;
  }
  function pinnedSource(snapshot:Snapshot):string {
    const source=sourcePreview,r=source&&snapshot.resources.find(r=>r.id===source.resource_id&&r.workspace_id===source.workspace_id),revision=r&&source&&r.history.find(h=>h.id===source.revision_id);
    return revision&&isNoteRevision(revision)?`<article class="pinned-source"><h3>${escape(revision.title)}</h3><p class="context-note">The exact revision cited by this work.</p><div class="note-body">${escape(revision.body)}</div></article>`:'<p>This pinned source revision is unavailable on this device.</p>';
  }
  function openVoiceSettings(requested:string|null):void {
    const pending=connections?.mutation,locked=pending&&pending.type!=='idle'?pending.change.input.workspace_id:keyDraft?connectionWorkspace:null;
    connectionWorkspace=locked??requested;
    if(!connectionWorkspace)return;
    if(locked&&locked!==requested)localMessage='Your unfinished connection change belongs to the workspace shown here. Finish it before configuring another.';
    openPanel('voice-settings',false);void connections?.refresh(connectionWorkspace);void connections?.refreshPeers(connectionWorkspace);
  }
  function voiceSettings(snapshot:Snapshot):string {
    const id=connectionWorkspace;if(!id||!connections)return '<p>Choose a workspace first.</p>';
    const title=snapshot.workspaces.find(w=>w.id===id)?.title??'Original workspace',state=connections.current(id),pending=connections.mutation;
    return `<section class="voice-settings"><p class="capture-address">${escape(title)}</p><h3>OpenAI</h3><p>This workspace’s connection powers dictation and meeting preparation. Review transcribed words and prepared material before using them.</p>
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
    return `<section class="version"><h3>${escape(revision.title)}</h3><small>${escape(author)}</small><p>${escape(isNoteRevision(revision)?revision.body:isSurfaceRevision(revision)?surfacePreview(revision.surface):revision.work.outcome)}</p></section>`;
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
    const content = revision&&isWorkRevision(revision)?meetingWork(r,revision,snapshot,focused):r.heads.every(isWorkRevision)&&focused?`<section class="comparison"><h2>Review concurrent work changes</h2><p>These requests arrived independently. Choose one to revise against all observed work versions; no preparation starts automatically.</p>${r.heads.map(h=>`<article>${revisionView(h,snapshot)}${isWorkRevision(h)&&h.work.state.type==='materials_ready'?`<ul>${h.work.state.actions.map(action=>`<li>${escape(action.text)} · ${escape(action.owner.type==='assigned'?action.owner.name:'Unassigned')} · ${escape(action.deadline.type==='stated'?action.deadline.text:'No deadline')}</li>`).join('')}</ul>${button('Review these actions','correct-actions',{resourceId:r.id,revisionId:h.id},'quiet')}`:''}${button('Revise from this request','revise-work',{resourceId:r.id,revisionId:h.id},'quiet')}</article>`).join('')}</section>`:revision && isSurfaceRevision(revision) ? renderSurface(revision.surface,focused,revision.title) : revision && isNoteRevision(revision) ? `<div class="paper"><h2 ${focused ? 'id="reader-title" tabindex="-1"' : ''}>${focused ? escape(revision.title) : button(escape(revision.title),'focus',{resourceId:r.id},'material-title')}</h2><div class="note-body">${escape(revision.body) || '<span class="muted">An empty page, ready for your next thought.</span>'}</div></div>` : focused ? `<div class="comparison"><h2>Choose how this work continues</h2><p>Different edits arrived. Every version is kept; choose one or combine them.</p><div class="comparison-grid">${r.heads.map(h => `<div>${revisionView(h,snapshot)}<div class="row">${button('Use this version','use-version',{resourceId:r.id,revisionId:h.id,heads:r.heads.map(h=>h.id)},'primary',writing())}${button('Edit from this version','edit',{resourceId:r.id,revisionId:h.id},'',writing())}</div></div>`).join('')}</div></div>` : `<div class="paper"><h2>${escape(title)}</h2><p>Open to compare the edits and continue.</p></div>`;
    return `<article class="resource-card ${focused?'reading':''}" data-resource="${escape(r.id)}"><header class="card-header"><span class="material-kind">${revision ? isNoteRevision(revision)?'Note':isSurfaceRevision(revision)?escape(surfaceLabel(revision.surface)):'Meeting work' : 'Needs review'}</span>${focused && revision&&!isWorkRevision(revision) ? button('Edit','edit',{resourceId:r.id},'primary',writing()) : ''}</header>${content}
      ${!focused ? `<footer class="card-footer"><button type="button" data-action="focus" data-args="${attr({resourceId:r.id})}" class="open-material" aria-label="Open ${escape(title)}">${revision&&isWorkRevision(revision)?'Open work':revision&&isSurfaceRevision(revision)?'Open':'Open note'}</button><span aria-hidden="true">↗</span></footer>` : `<details class="card-options" data-disclosure="options-${escape(r.id)}" ${disclosures.has('options-'+r.id)?'open':''}><summary>Organize & history</summary><div class="row">${button('Move to start on this device','move',{resourceId:r.id})}${button('Close local view '+escape(title),'close-view',{resourceId:r.id})}${route.type === 'workspace' ? button('Remove reference','remove-reference',{workspaceId:route.id,resourceId:r.id,sourceWorkspaceId},'',writing()) : ''}</div><form data-form="add-reference" data-resource="${escape(r.id)}" data-source="${escape(r.workspace_id)}"><label>Reference in<select name="destination" aria-label="Destination workspace for ${escape(title)}">${snapshot.workspaces.filter(w=>!w.placements.some(p=>p.resource_id===r.id)).map(w=>`<option value="${escape(w.id)}">${escape(w.title)}</option>`).join('')}</select></label><button type="submit" ${writing() || snapshot.workspaces.every(w=>w.placements.some(p=>p.resource_id===r.id))?'disabled':''}>Add reference</button></form><p class="local-note">A reference keeps the source’s access. Closing a view only changes this device.</p><details><summary>Retained history · ${r.history.length} revisions</summary>${r.history.map(h=>revisionView(h,snapshot)).join('')}</details></details>`}</article>`;
  }
  function workspace(snapshot: Snapshot, workspaceId: string): string {
    const workspace = snapshot.workspaces.find(w=>w.id===workspaceId); if (!workspace) return '<div class="notice">This workspace is unavailable.</div>';
    const view = session.view(), placements = [...workspace.placements].sort((a,b)=> { const ai=view.order.indexOf(a.resource_id),bi=view.order.indexOf(b.resource_id);return (ai<0?Infinity:ai)-(bi<0?Infinity:bi); }).filter(p=>!view.closed.includes(p.resource_id));
    const focused = view.mode==='focus' && view.focused && placements.some(p=>p.resource_id===view.focused), visible=focused?placements.filter(p=>p.resource_id===view.focused):placements;
    const editor = session.state.editor;
    if (editor && editorOpen && editor.workspaceId === workspaceId) return `<div class="work-content editing">${renderEditor(snapshot)}</div>`;
    return `<section class="work-content">
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

  function render(userInitiated=false): void {
    if(disposed)return;
    if(composing||controlPress&&!userInitiated&&!userActionDepth){deferredRender=true;return;}
    // This render fulfills prior deferred observations. A stale flag otherwise
    // schedules an unnecessary focusout render between pointerdown and click.
    deferredRender=false;
    const oldFocus=document.activeElement instanceof HTMLElement&&root.contains(document.activeElement)?document.activeElement:null;
    const focusAction=oldFocus?.dataset.action,focusArgs=oldFocus?.dataset.args,focusForm=oldFocus?.closest('form')?.id,focusText=oldFocus?.textContent;
    const focusId=oldFocus?.id,selection=oldFocus instanceof HTMLInputElement||oldFocus instanceof HTMLTextAreaElement?{start:oldFocus.selectionStart,end:oldFocus.selectionEnd}:null;
    const scroll=frame.querySelector('.main')?.scrollTop??0, dialogScroll=dialog.scrollTop, state=session.state,snapshot=snapshotOf(state);
    if(!snapshot){frame.innerHTML=`<div class="app-shell"><header class="topbar"><span class="brand">Shared Workspace</span></header><main class="main"><section class="gate"><h1>${state.host.type==='unavailable'?'Open your native workspace':state.host.type==='loading'?'Opening your work…':'Your work could not open'}</h1><p>${state.host.type==='unavailable'?'Open the installed app to use this native interface.':state.host.type==='failed'?escape(state.host.message):'Reading the work saved on this device.'}</p>${state.host.type==='failed'?button('Retry connection','refresh',{},'primary'):''}</section></main></div>`;return}
    const workspaceId = state.route.type === 'workspace' ? state.route.id : null;
    const currentTitle = snapshot.workspaces.find(w => w.id === workspaceId)?.title ?? 'Your work';
    const selected=snapshot.resources.find(r=>r.id===session.view().focused),pageTitle=state.editor&&editorOpen?state.editor.title||'New material':session.view().mode==='focus'&&selected?titleOf(selected):currentTitle;
    const preservedEditorFields=renderedEditorId===state.editor?.draftId?[...frame.querySelectorAll<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>('.editor input[id],.editor textarea[id],.editor select[id]')]:[];
    const preservedPanelFields=renderedPanel===panel?[...dialog.querySelectorAll<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>('input[id],textarea[id],select[id]')]:[];
    frame.innerHTML=`<div class="app-shell"><aside class="workspace-navigation" id="workspace-navigation"><div class="workspace-context"><button type="button" id="workspace-picker" data-action="picker" aria-label="Current workspace: ${escape(currentTitle)}" aria-haspopup="dialog"><span>${escape(currentTitle)}</span><span aria-hidden="true">⌄</span></button></div>${materialNavigation(snapshot)}<div class="navigation-utilities"><button type="button" id="workspace-settings" data-action="settings" class="navigation-row" aria-label="Settings" aria-haspopup="dialog">${settingsIcon}<span>Settings</span></button></div></aside><header class="topbar"><div class="page-heading"><span class="page-title">${escape(pageTitle)}</span></div>${captureDock(snapshot)}</header><button type="button" id="navigation-toggle" class="navigation-toggle" data-action="navigation" aria-label="Open navigation" aria-controls="workspace-navigation" aria-expanded="false">${shellIcon('navigation')}</button><main class="main">${state.host.type==='failed'?`<div class="notice">Showing the last saved view. ${button('Retry read','refresh')}</div>`:''}${mutationNotice(state)}${workNotice()}${!inputReady&&options.drafts?'<p class="notice">Restoring private input on this device…</p>':''}${meetingDraft||actionCorrection?`<div class="draft-banner"><span>Unfinished ${actionCorrection?'action corrections':'meeting request'}</span>${button('Continue work draft',actionCorrection?'resume-correction':'resume-meeting',{},'quiet')}</div>`:''}${snapshot.network.type==='unavailable'?`<div class="notice">Connections are unavailable. You can still save work on this device. ${button('Check connections','connections',{},'quiet')}</div>`:''}<div class="status-message ${!localMessage&&['Saved here.','Waiting for the local save receipt…','Connected. Your shared work is saved here.'].includes(state.message)?'routine-status':''}" role="status" aria-live="polite">${escape(localMessage||state.message)}</div>${state.route.type==='workspace'?workspace(snapshot,state.route.id):home(snapshot)}</main><aside id="workspace-inspector" class="workspace-inspector" aria-label="Related details">${inspectorContent(snapshot)}</aside></div>`;
    frame.inert=Boolean(panel);if(panel)frame.setAttribute('aria-hidden','true');else frame.removeAttribute('aria-hidden');
    if(panel){
      const titles: Record<Panel,string> = {settings:'Settings',picker:'Switch workspace',create:'Create workspace',capture:'Capture',connections:'Connections',share:'Share workspace',join:'Join workspaces',appearance:'Appearance',add:'Add to workspace','voice-settings':'OpenAI connection',navigator:'Navigation','page-actions':'Page actions',inspector:'Related details',meeting:'Meeting work','action-correction':'Correct actions',source:'Pinned transcript'};
      dialog.setAttribute('aria-label',titles[panel]); dialog.dataset.panel=panel;
      dialog.innerHTML=`<header class="dialog-heading"><h2>${titles[panel]}</h2><button type="button" data-action="close-dialog" aria-label="Close dialog">×</button></header><div class="dialog-body">${panel==='meeting'?meetingForm(snapshot):panel==='action-correction'?correctionForm():panel==='source'?pinnedSource(snapshot):panel==='navigator'?navigatorPanel(snapshot):panel==='page-actions'?pageActions(snapshot):panel==='inspector'?inspectorContent(snapshot):panel==='settings'?settingsPanel(snapshot):panel==='picker'?picker(snapshot):panel==='create'?createWorkspaceForm():panel==='capture'?renderCommand(snapshot):panel==='appearance'?appearancePanel():panel==='add'?addPanel(snapshot):panel==='voice-settings'?voiceSettings(snapshot):devices(snapshot)}${mutationNotice(state)}${workNotice()}<div class="dialog-status" role="status">${escape(localMessage||((panel==='connections'||panel==='share'||panel==='join')?state.message:''))}</div></div>`;
      if(!dialog.open)dialog.showModal(); dialog.scrollTop=dialogScroll;
    }
    for(const field of [...preservedEditorFields,...preservedPanelFields]){
      const replacement=byId<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>(field.id);if(!replacement||field.tagName!==replacement.tagName)continue;
      const value=replacement.value;
      for(const attribute of [...field.attributes])if(!replacement.hasAttribute(attribute.name))field.removeAttribute(attribute.name);
      for(const attribute of [...replacement.attributes])field.setAttribute(attribute.name,attribute.value);
      if(field.value!==value)field.value=value;if(field instanceof HTMLInputElement&&replacement instanceof HTMLInputElement)field.checked=replacement.checked;
      replacement.replaceWith(field);
    }
    renderedEditorId=state.editor?.draftId??null;renderedPanel=panel;
    const keyField=byId<HTMLInputElement>('connection-key');if(keyField&&connections?.mutation.type==='idle')keyField.value=keyDraft;
    const slot=byId('code-editor-slot'),editor=state.editor;
    if(slot&&editor&&(editor.type==='surface_new'||editor.type==='surface_existing')&&editor.surface.type==='code_document'){
      const identity=editor.draftId;
      if(codeIdentity!==identity){codeEditor?.dispose();codeNode.replaceChildren();codeIdentity=identity;codeEditor=mountCodeEditor(codeNode,editor.surface.text,editor.surface.language,text=>{lastEditorField='code';const current=session.state.editor;if(current&&(current.type==='surface_new'||current.type==='surface_existing')&&current.surface.type==='code_document'){session.updateSurface({...current.surface,text});void retainInput().catch(retentionFailure)}});}
      slot.append(codeNode);codeEditor?.setText(editor.surface.text);codeEditor?.setLanguage(editor.surface.language);codeEditor?.setTheme(document.documentElement.dataset.theme==='dark'?'dark':'light');codeEditor?.setReadonly(writing());if(oldFocus?.isContentEditable)codeEditor?.focus();
    }else if(!editor||!(editor.type==='surface_new'||editor.type==='surface_existing')){codeEditor?.dispose();codeEditor=null;codeIdentity=null;}
    const main=frame.querySelector('.main');if(main)main.scrollTop=scroll;
    if(oldFocus){const replacement=focusId?byId(focusId):Array.from(root.querySelectorAll<HTMLElement>('button,summary,select')).find(el=>focusAction?el.dataset.action===focusAction&&el.dataset.args===focusArgs:el.tagName===oldFocus.tagName&&el.textContent===focusText&&el.closest('form')?.id===focusForm);replacement?.focus({preventScroll:true});if(selection&&(replacement instanceof HTMLInputElement||replacement instanceof HTMLTextAreaElement)&&selection.start!==null&&selection.end!==null){try{replacement.setSelectionRange(selection.start,selection.end)}catch{}}}
    applyLayout();
    focusReview();
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
    if(!panel&&!(shellInput()==='desktop'&&(target.dataset.action==='navigation'&&root.clientWidth>1000||target.dataset.action==='inspector'&&root.clientWidth>740)))target.focus({preventScroll:true});
    const action=target.dataset.action,args:Record<string,unknown>=JSON.parse(target.dataset.args??'{}');localMessage='';
    if(!inputReady&&['new-meeting','prepare-work','resume-work','revise-work','correct-actions','add-surface','add-link','new-note','edit','speak','dictate-outcome','dictate-title','dictate-action','type-command','new-command','discard-editor','use-version','remove-reference','resolve-editor'].includes(action??'')){localMessage='Wait for private input recovery before making changes.';render();return;}
    switch(action){
      case'navigation':if(shellInput()==='desktop'&&root.clientWidth>1000){layout={...layout,navigation:layout.navigation==='shown'?'hidden':'shown'};saveLayoutPreferences(layout);applyLayout();}else openPanel('navigator',false);break;
      case'new-meeting':if(!inputReady)break;if(!meetingDraft)meetingDraft=Object.freeze({type:'create',draftId:options.id?.()??crypto.randomUUID(),workspaceId:String(args.workspaceId),title:'',outcome:'',sources:Object.freeze([])});await retainInput();openPanel('meeting');break;
      case'resume-meeting':if(meetingDraft)openPanel('meeting');break;
      case'resume-correction':if(actionCorrection)openPanel('action-correction');break;
      case'discard-meeting':if(!workController?.busy&&workController?.state.type!=='uncertain'&&workController?.state.type!=='committed'){meetingDraft=null;await retainInput();closePanel();}break;
      case'revise-work':{
        if(!inputReady)break;if(meetingDraft){openPanel('meeting');break;}
        const r=availableSnapshot()?.resources.find(r=>r.id===args.resourceId),revision=args.revisionId?r?.heads.find(h=>h.id===args.revisionId):r?.heads.length===1?r.heads[0]:null;
        if(r&&revision&&isWorkRevision(revision)){meetingDraft=Object.freeze({type:'revise',draftId:options.id?.()??crypto.randomUUID(),workspaceId:r.workspace_id,resourceId:r.id,expectedHeads:Object.freeze(r.heads.map(h=>h.id)),title:revision.title,outcome:revision.work.outcome,sources:revision.work.sources});await retainInput();openPanel('meeting');}break;
      }
      case'interrupt-work':workController?.interrupt();break;
      case'prepare-work':if(inputReady)await workController?.prepare(String(args.resourceId));break;
      case'resume-work':if(inputReady)await workController?.resume(String(args.resourceId));break;
      case'retry-work':{
        const state=workController?.state;if(!state||state.type!=='uncertain'&&state.type!=='committed')break;const command=state.command;
        await workController?.retry();if(workController?.state.type==='idle'){
          const draft=meetingDraft;
          if(draft&&command.workspace_id===draft.workspaceId&&((command.type==='create_work'&&draft.type==='create'&&command.title===draft.title.trim()&&command.outcome===draft.outcome&&JSON.stringify(command.sources)===JSON.stringify(draft.sources))||(command.type==='edit_work'&&draft.type==='revise'&&command.resource_id===draft.resourceId&&command.change.type==='revise_request'&&command.change.title===draft.title.trim()&&command.change.outcome===draft.outcome&&JSON.stringify(command.change.sources)===JSON.stringify(draft.sources))))meetingDraft=null;
          if(actionCorrection&&command.type==='edit_work'&&command.change.type==='correct_actions'&&command.workspace_id===actionCorrection.workspaceId&&command.resource_id===actionCorrection.resourceId&&JSON.stringify(command.change.actions)===JSON.stringify(actionCorrection.actions))actionCorrection=null;
          await retainInput();if(panel)closePanel();await changeRoute({type:'workspace',id:command.workspace_id});focusResource(command.resource_id);
        }break;
      }
      case'review-work':{const state=workController?.state;if(state?.type==='conflict'){await changeRoute({type:'workspace',id:state.command.workspace_id});focusResource(state.command.resource_id);await workController?.discardInterrupted();}break;}
      case'correct-actions':{
        if(!inputReady)break;if(actionCorrection){openPanel('action-correction');break;}
        const r=availableSnapshot()?.resources.find(r=>r.id===args.resourceId),h=typeof args.revisionId==='string'?r?.heads.find(h=>h.id===args.revisionId):r?.heads.length===1?r.heads[0]:null;
        if(r&&h&&isWorkRevision(h)&&h.work.state.type==='materials_ready'){actionCorrection=Object.freeze({draftId:options.id?.()??crypto.randomUUID(),workspaceId:r.workspace_id,resourceId:r.id,expectedHeads:Object.freeze(r.heads.map(h=>h.id)),actions:h.work.state.actions});await retainInput();openPanel('action-correction');}break;
      }
      case'reconcile-actions':{
        const draft=actionCorrection,current=draft&&availableSnapshot()?.resources.find(resource=>resource.id===draft.resourceId&&resource.workspace_id===draft.workspaceId);
        if(!draft||!current||!Array.isArray(args.heads)||!sameHeads(current.heads.map(head=>head.id),args.heads as string[])){render();break;}
        if(workController?.state.type==='conflict')await workController.discardInterrupted();
        const reviewed=Object.freeze({...draft,expectedHeads:Object.freeze(current.heads.map(head=>head.id))});actionCorrection=reviewed;await retainInput();
        if(await workController?.correctActions(reviewed)){if(actionCorrection===reviewed)actionCorrection=null;await retainInput();closePanel();await changeRoute({type:'workspace',id:reviewed.workspaceId});focusResource(reviewed.resourceId);}break;
      }
      case'discard-correction':if(!workController?.busy&&workController?.state.type!=='uncertain'&&workController?.state.type!=='committed'){actionCorrection=null;await retainInput();closePanel();}break;
      case'inspect-source':sourcePreview=decodeSource(args.source);openPanel('source',false);break;
      case'dictate-title':case'dictate-outcome':case'dictate-action':{
        if(command?.text){localMessage='Review or discard your earlier capture before recording another.';openPanel('capture',false);break;}
        const snapshot=availableSnapshot();if(!snapshot)break;
        let target:MeetingCapture,resourceId:string|null=null;
        if(action==='dictate-action'){
          const draft=actionCorrection,item=draft?.actions.find(item=>item.id===args.actionId),field=args.field;if(!draft||!item||field!=='owner'&&field!=='deadline')break;
          const value=field==='owner'?item.owner.type==='assigned'?item.owner.name:'':item.deadline.type==='stated'?item.deadline.text:'',index=draft.actions.indexOf(item),input=byId<HTMLInputElement>(field+'-'+index);
          resourceId=draft.resourceId;target=Object.freeze({type:'action',draftId:draft.draftId,workspaceId:draft.workspaceId,resourceId:draft.resourceId,expectedHeads:draft.expectedHeads,actionId:item.id,field,before:value,start:input?.selectionStart??value.length,end:input?.selectionEnd??value.length});
        }else{
          const draft=meetingDraft;if(!draft)break;const field=action==='dictate-title'?'title':'outcome',input=byId<HTMLInputElement|HTMLTextAreaElement>('meeting-'+field),value=draft[field];resourceId=draft.type==='revise'?draft.resourceId:null;
          target=Object.freeze({type:'request',draftId:draft.draftId,workspaceId:draft.workspaceId,field,before:value,start:input?.selectionStart??value.length,end:input?.selectionEnd??value.length});
        }
        captureMeeting=target;captureEditor=null;captureRecovery=false;captureAsNote=false;command={text:'',address:captureAddress(snapshot,{type:'workspace',id:target.workspaceId},resourceId),source:'typed'};inputMode='voice';
        await retainInput();if(connections&&voiceMethod==='provider'&&connections.current(target.workspaceId)?.status!=='ready'){openVoiceSettings(target.workspaceId);break;}openPanel('capture',capability.type==='system_dictation');await voice.start(command.address,capability);break;
      }
      case'page-actions':openPanel('page-actions',false);break;
      case'inspector':if(panel)closePanel();if(shellInput()==='desktop'&&root.clientWidth>740){layout={...layout,inspector:layout.inspector==='shown'?'hidden':'shown'};saveLayoutPreferences(layout);applyLayout();}else openPanel('inspector',false);break;
      case'organize':if(panel)closePanel();{const id='options-'+String(args.resourceId);disclosures.add(id);const details=[...frame.querySelectorAll<HTMLDetailsElement>('details[data-disclosure]')].find(el=>el.dataset.disclosure===id);if(details){details.open=true;details.scrollIntoView({block:'nearest'});details.querySelector('summary')?.focus()}}break;
      case'update':if(historyOwned){afterPanelClose=()=>options.openUpdate?.();closePanel()}else{closePanel();options.openUpdate?.()}break;
      case'app-options':if(historyOwned){afterPanelClose=()=>options.openAppOptions?.();closePanel()}else{closePanel();options.openAppOptions?.()}break;
      case'continue-voice':inputMode='voice';openPanel('capture',false);break;
      case'configure-voice':{const route=session.state.route,routeId=route.type==='workspace'?route.id:null;openVoiceSettings(panel==='capture'?command?.address.workspaceId??routeId:routeId);break;}
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
      case'settings':openPanel('settings');break;
      case'picker':pickerScope=null;openPanel('picker');break;
      case'create-space':openPanel('create');break;
      case'connections':openPanel('connections');break;
      case'join-work':openPanel('join');break;
      case'close-dialog':closePanel();break;
      case'navigate':closePanel();await changeRoute(args.type==='workspace'?{type:'workspace',id:String(args.id)}:{type:args.type==='devices'?'devices':'home'});break;
      case'focus':if(panel)closePanel();focusResource(String(args.resourceId));break;
      case'open-resource':if(panel)closePanel();await changeRoute({type:'workspace',id:String(args.workspaceId)});focusResource(String(args.resourceId));break;
      case'all-work':if(panel)closePanel();session.mode('canvas');{const route=session.state.route,main=frame.querySelector('.main');if(main&&route.type==='workspace')main.scrollTop=listScroll.get(route.id)??0}break;
      case'close-view':session.close(String(args.resourceId));break;
      case'reopen':session.reopen();break;
      case'move':session.move(String(args.resourceId));break;
      case'new-note':closePanel();editorOpen=true;if(session.state.editor){await changeRoute({type:'workspace',id:session.state.editor.workspaceId});render()}else session.newNote(String(args.workspaceId));byId('note-title')?.focus();break;
      case'edit':if(panel)closePanel();editorOpen=true;if(session.state.editor){await changeRoute({type:'workspace',id:session.state.editor.workspaceId});localMessage='Finish or discard your current edit before starting another.';render()}else {session.edit(String(args.resourceId),typeof args.revisionId==='string'?args.revisionId:undefined);const source=availableSnapshot()?.resources.find(r=>r.id===String(args.resourceId));if(source)await changeRoute({type:'workspace',id:source.workspace_id})}const main=frame.querySelector('.main');if(main)main.scrollTop=0;byId('note-title')?.focus({preventScroll:true});break;
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
      case'dictation-note':captureEditor=null;captureMeeting=null;captureRecovery=false;captureAsNote=true;render();break;
      case'new-command':await voice.cancel();command=null;captureEditor=null;captureMeeting=null;captureRecovery=false;captureAsNote=false;render();if(inputMode==='typing')byId('command-text')?.focus();break;
      case'return-command':if(command)await changeRoute(command.address.workspaceId?{type:'workspace',id:command.address.workspaceId}:{type:'home'});break;
      case'speak':{inputMode='voice';const snapshot=availableSnapshot();if(!snapshot)break;const scope=command?.address.workspaceId??(session.state.route.type==='workspace'?session.state.route.id:null);if(connections&&voiceMethod==='provider'&&(!scope||connections.current(scope)?.status!=='ready')){openVoiceSettings(scope);return;}if(!command){captureMeeting=null;captureEditor=editorTarget();captureRecovery=false;captureAsNote=false;command={text:'',address:currentAddress(snapshot),source:'typed'}}openPanel('capture',capability.type==='system_dictation');await voice.start(command.address,capability);break}
      case'stop-voice':await voice.stop();break;
      case'cancel-voice':await voice.cancel();break;
      case'scan':if(!scanning){const epoch=++scanEpoch;scanning=true;scanMessage='';render();try{const ticket=await options.scanner.scan();if(epoch===scanEpoch&&!disposed){disclosures.delete('incoming');joinTicket=ticket;scanMessage='Invitation read. Review it, then choose Join.'}}catch{if(epoch===scanEpoch)scanMessage='No invitation was accepted from the camera. You can paste a ticket instead.'}finally{if(epoch===scanEpoch){scanning=false;render()}}}break;
      case'cancel-scan':{const epoch=++scanEpoch;scanning=false;try{await options.scanner.cancel();if(epoch===scanEpoch)scanMessage='Scan cancelled.'}catch{if(epoch===scanEpoch)scanMessage='Camera cancellation was not confirmed.'}render();break;}
    }
  };
  const onInput=(event:Event):void=>{
    const field=event.target;
    if(field instanceof HTMLSelectElement&&field.id==='preferred-hand'){layout={...layout,hand:field.value==='left'?'left':'right'};saveLayoutPreferences(layout);applyLayout();return;}
    if(field instanceof HTMLSelectElement&&field.id==='code-language'){const e=session.state.editor;if(e&&(e.type==='surface_new'||e.type==='surface_existing')&&e.surface.type==='code_document'){session.updateSurface({...e.surface,language:field.value as CodeLanguage});codeEditor?.setLanguage(field.value as CodeLanguage)}return}
    if(!(field instanceof HTMLInputElement||field instanceof HTMLTextAreaElement))return;
    if(meetingDraft&&(field.id==='meeting-title'||field.id==='meeting-outcome'))meetingDraft=Object.freeze({...meetingDraft,[field.id==='meeting-title'?'title':'outcome']:field.value});
    if(meetingDraft&&field instanceof HTMLInputElement&&field.dataset.meetingSource){const source=decodeSource(JSON.parse(field.dataset.meetingSource));const sources=meetingDraft.sources.filter(item=>!sameSource(item,source));if(field.checked)sources.push(source);if(sources.length>16){field.checked=false;localMessage='Select at most 16 transcript revisions.';return;}meetingDraft=Object.freeze({...meetingDraft,sources:Object.freeze(sources)});}
    if(actionCorrection&&(field.dataset.correction==='owner'||field.dataset.correction==='deadline')){const index=Number(field.dataset.actionIndex),kind=field.dataset.correction;actionCorrection=Object.freeze({...actionCorrection,actions:Object.freeze(actionCorrection.actions.map((action,i)=>i!==index?action:correctedAction(action,kind,field.value)))});}
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
    if(!inputReady){localMessage='Wait for private input recovery before saving.';render();return;}
    if(form.id==='meeting-form'){
      const draft=meetingDraft;if(!draft||!workController||importingTranscript)return;await retainInput();
      const result=draft.type==='create'?await workController.create(draft):await workController.revise(draft);
      if(result){if(meetingDraft===draft)meetingDraft=null;await retainInput();closePanel();await changeRoute({type:'workspace',id:draft.workspaceId});focusResource(typeof result==='string'?result:draft.type==='revise'?draft.resourceId:'');}return;
    }
    if(form.id==='action-correction-form'){
      const draft=actionCorrection;if(!draft||!workController)return;await retainInput();if(await workController.correctActions(draft)){if(actionCorrection===draft)actionCorrection=null;await retainInput();closePanel();await changeRoute({type:'workspace',id:draft.workspaceId});focusResource(draft.resourceId);}return;
    }
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
      if(captureEditor||captureMeeting){await voice.cancel();await insertDictation();return}
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
    if(inputReady)void retainInput().catch(retentionFailure);
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
    if(change==='data'&&protectedInput()){deferredRender=true;return}render(change==='ui');
  });
  const onDisclosure=(event:Event):void=>{const detail=event.target;if(detail instanceof HTMLDetailsElement&&root.contains(detail)&&detail.dataset.disclosure){if(detail.open)disclosures.add(detail.dataset.disclosure);else disclosures.delete(detail.dataset.disclosure)}};
  const onFocusOut=():void=>{setTimeout(()=>{if(!disposed&&deferredRender&&!protectedInput())render()},0)};
  const onEditorFocus=(event:FocusEvent):void=>{const field=event.target;if(field instanceof HTMLElement){if(field.id==='note-title')lastEditorField='title';else if(field.id==='note-body')lastEditorField='body';else if(field.closest('.code-editor'))lastEditorField='code'}};
  const onCompositionStart=():void=>{reviewFocus=null;composing=true},onCompositionEnd=():void=>{composing=false;setTimeout(()=>{if(deferredRender&&!composing&&!disposed){deferredRender=false;render()}},0)};
  const onLayoutPointer=(event:PointerEvent):void=>{if(event.button===0&&shellInput()==='desktop'&&((event.target as Element).closest('[data-action=navigation]')&&root.clientWidth>1000||(event.target as Element).closest('[data-action=inspector]')&&root.clientWidth>740)&&document.activeElement?.matches('input,textarea,[contenteditable]'))event.preventDefault()};
  root.addEventListener('pointerdown',onLayoutPointer);
  window.addEventListener('pointerdown',onControlPointerDown,true);window.addEventListener('pointerup',onControlPointerUp,true);window.addEventListener('pointercancel',onControlPointerCancel,true);window.addEventListener('click',onControlClick,true);window.addEventListener('blur',cancelControlPress);window.addEventListener('keydown',onActivationKeyDown,true);window.addEventListener('keyup',onActivationKeyUp,true);
  window.addEventListener('resize',applyLayout);window.visualViewport?.addEventListener('resize',applyLayout);window.visualViewport?.addEventListener('scroll',applyLayout);touchInput.addEventListener('change',applyLayout);
  const clickHandler=(e:MouseEvent):void=>{void capturedUserAction(()=>onClick(e)).then(()=>retainInput()).catch(()=>{localMessage='The requested action did not complete.';render()})};
  const inputHandler=(event:Event):void=>{reviewFocus=null;onInput(event);void retainInput().catch(retentionFailure)};
  const submitHandler=(e:SubmitEvent):void=>{void capturedUserAction(()=>onSubmit(e)).then(()=>retainInput()).catch(()=>{localMessage='The requested action did not complete.';render()})};
  root.addEventListener('toggle',onDisclosure,true);root.addEventListener('click',clickHandler);root.addEventListener('input',inputHandler);root.addEventListener('submit',submitHandler);root.addEventListener('focusout',onFocusOut);root.addEventListener('focusin',onEditorFocus);root.addEventListener('compositionstart',onCompositionStart);root.addEventListener('compositionend',onCompositionEnd);
  const onCancel=(event:Event):void=>{event.preventDefault();closePanel()};
  const onBackdrop=(event:MouseEvent):void=>{if(event.target!==dialog)return;const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closePanel()};
  const onBack=():void=>{if(historyReturning){historyReturning=false;if(panel){history.pushState({...history.state,workspaceDialog:true},'');historyOwned=true}const after=afterPanelClose;afterPanelClose=null;after?.();return}if(panel)closePanel(true)};
  dialog.addEventListener('cancel',onCancel);dialog.addEventListener('click',onBackdrop);window.addEventListener('popstate',onBack);
  const visibility=():void=>{if(document.hidden){cancelControlPress();void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})}}else void session.refresh()};document.addEventListener('visibilitychange',visibility);
  const poll=setInterval(()=>{if(!document.hidden&&session.state.mutation.type!=='sending'&&!session.state.syncing)void session.refresh()},options.pollMs??5000);
  const connections=options.connections?new ConnectionSession(options.connections,()=>{if(protectedInput()){deferredRender=true;return}render()}):null;
  const appearance=observeAppearance(()=>{codeEditor?.setTheme(document.documentElement.dataset.theme==='dark'?'dark':'light');if(panel==='appearance'||panel==='settings')render()});
  render();void initializeInput();void options.voice.capability().then(value=>{capability=value;render()}).catch(()=>{});void options.scanner.available().then(value=>{scanAvailable=value;if(session.state.route.type==='devices')render()}).catch(()=>{});
  return {session,voiceContext(){const route=session.state.route,workspaceId=command?.address.workspaceId??(route.type==='workspace'?route.id:null),state=workspaceId?connections?.current(workspaceId):null;if(!workspaceId||state?.status!=='ready')throw new Error('No configured workspace connection');return{workspaceId,connectionVersion:state.version}},hasUnsubmittedInput:()=>Boolean(!inputReady||journal?.failed||journal?.pending||meetingDraft||actionCorrection||importingTranscript||workController?.hasPendingInput()||session.state.mutation.type==='sending'||session.state.mutation.type==='uncertain'||voice.state.type!=='idle'||session.state.editor||command?.text||workspaceTitleDraft.trim()||keyDraft||connections&&connections.mutation.type!=='idle'),setUpdateAvailable(available=true){if(updateAvailable===available)return;updateAvailable=available;render()},dispose(){disposed=true;reviewFocus=null;window.clearTimeout(pressReleaseTimer);window.clearTimeout(pressFlushTimer);controlPress=null;window.removeEventListener('pointerdown',onControlPointerDown,true);window.removeEventListener('pointerup',onControlPointerUp,true);window.removeEventListener('pointercancel',onControlPointerCancel,true);window.removeEventListener('click',onControlClick,true);window.removeEventListener('blur',cancelControlPress);window.removeEventListener('keydown',onActivationKeyDown,true);window.removeEventListener('keyup',onActivationKeyUp,true);unsubscribeWork?.();workController?.dispose();root.removeEventListener('pointerdown',onLayoutPointer);window.removeEventListener('resize',applyLayout);window.visualViewport?.removeEventListener('resize',applyLayout);window.visualViewport?.removeEventListener('scroll',applyLayout);touchInput.removeEventListener('change',applyLayout);root.removeEventListener('change',onFileChange);codeEditor?.dispose();connections?.dispose();appearance.dispose();dialog.close();dialog.removeEventListener('cancel',onCancel);dialog.removeEventListener('click',onBackdrop);window.removeEventListener('popstate',onBack);clearInterval(poll);unsubscribe();session.dispose();void voice.cancel();if(scanning){scanEpoch++;scanning=false;void options.scanner.cancel().catch(()=>{})};root.removeEventListener('toggle',onDisclosure,true);root.removeEventListener('click',clickHandler);root.removeEventListener('input',inputHandler);root.removeEventListener('submit',submitHandler);root.removeEventListener('focusout',onFocusOut);root.removeEventListener('focusin',onEditorFocus);root.removeEventListener('compositionstart',onCompositionStart);root.removeEventListener('compositionend',onCompositionEnd);document.removeEventListener('visibilitychange',visibility)}};
}
