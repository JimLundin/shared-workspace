import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkspaceSession } from './session.ts';
import { decodeSnapshot } from './protocol.ts';
import type { Command, Invite, Receipt, Snapshot, SyncReport, JoinStage } from './protocol.ts';
import type { Host } from './host.ts';
export const snapshot = (head='r1'): Snapshot => ({device_id:'local',device_name:'Local host',workspaces:[{id:'w1',title:'Work',placements:[{resource_id:'note1',source_workspace_id:'w1'}],members:['local'],heads:[head]}],resources:[{id:'note1',workspace_id:'w1',heads:[{id:head,author:'local',title:'Note',body:'Original',parents:[]}],history:[]}],operation_count:1,network:{type:'active'},peers:[]});
class HostFixture implements Host {
  available=true; value=snapshot(); commands:Command[]=[];
  commandHandler:(c:Command)=>Promise<Receipt>=async c=>({type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:false});
  snapshotHandler:()=>Promise<Snapshot>=async()=>this.value;
  snapshot(){return this.snapshotHandler()}
  async command(c:Command){this.commands.push(c);return this.commandHandler(c)}
  async invite(_selection:readonly string[]):Promise<Invite>{throw new Error('Not used')}
  joinHandler:(ticket:string,progress?: (stage:JoinStage)=>void)=>Promise<Snapshot>=async()=>{throw new Error('Not used')};
  join(ticket:string,progress?: (stage:JoinStage)=>void):Promise<Snapshot>{return this.joinHandler(ticket,progress)}
  async sync():Promise<SyncReport>{return {peers:[]}}
}
const ids=()=>{let n=0;return()=>`operation-${++n}`};
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(error:Error)=>void;const promise=new Promise<T>((done,failed)=>{resolve=done;reject=failed});return{promise,resolve,reject}}

test('retry an unconfirmed save preserves exact operation identity and content',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();let attempts=0;
  host.commandHandler=async c=>{if(attempts++===0)throw new Error('Receipt connection ended');return{type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:true}};
  await session.capture('w1','Remember the decision');assert.equal(session.state.mutation.type,'uncertain');await session.retry();
  assert.equal(host.commands.length,2,'EXACT_RETRY_NOT_SUBMITTED');assert.strictEqual(host.commands[0],host.commands[1]);assert.equal(session.state.mutation.type,'idle');
});
test('stale editor preserves its original heads until explicit reconciliation',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();session.edit('note1');session.updateEditor('body','My unsaved change');host.value=snapshot('r2');await session.refresh();
  host.commandHandler=async c=>c.type==='edit_resource'&&c.expected_heads[0]==='r1'?{type:'conflict',resource_id:'note1',expected_heads:['r1'],actual_heads:['r2']}:{type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:false};
  await session.saveEditor();assert.equal(session.state.mutation.type,'conflict','UNREVIEWED_HEADS_ADOPTED');assert.equal(session.state.editor?.type==='existing'?session.state.editor.body:null,'My unsaved change');const first=host.commands[0];assert.equal(first?.type,'edit_resource');if(first?.type==='edit_resource')assert.deepEqual(first.expected_heads,['r1']);
  await session.saveEditor(['r2']);const second=host.commands[1];assert.equal(second?.type,'edit_resource');if(second?.type==='edit_resource')assert.deepEqual(second.expected_heads,['r2']);assert.equal(session.state.mutation.type,'idle');
});
test('receipt for another operation cannot report this edit saved',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();host.commandHandler=async()=>({type:'applied',operation_id:'other',workspace_id:'w1',replayed:false});
  const receipt=await session.submit({type:'create_resource',operation_id:'pending-save',workspace_id:'w1',resource_id:'pending-note',title:'Pending',body:'Pending'});
  assert.equal(receipt,null,'MISMATCHED_SUBMIT_RECEIPT_EXPOSED');assert.equal(session.state.mutation.type,'uncertain');assert.match(session.state.message,/No save receipt/);
});
test('a delayed snapshot cannot undo a later committed observation',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();const delayed=deferred<Snapshot>();host.snapshotHandler=()=>delayed.promise;const older=session.refresh();
  host.snapshotHandler=async()=>({...snapshot('r2'),operation_count:2});await session.capture('w1','New work');delayed.resolve(snapshot('r1'));await older;
  assert.equal(session.snapshot()?.operation_count,2);assert.equal(session.snapshot()?.resources[0]?.heads[0]?.id,'r2');
});
test('local view changes neither remove shared placements nor issue commands',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();session.navigate({type:'workspace',id:'w1'});session.focus('note1');session.close('note1');session.navigate({type:'home'});
  assert.equal(host.commands.length,0);assert.equal(session.snapshot()?.workspaces[0]?.placements.length,1);session.navigate({type:'workspace',id:'w1'});assert.deepEqual(session.view().closed,['note1']);
});
test('transport unavailable still permits local committed work',async()=>{
  const host=new HostFixture();host.value=decodeSnapshot({...snapshot(),network:{type:'unavailable',detail:'No network route'}});const session=new WorkspaceSession(host,ids());await session.refresh();await session.capture('w1','Local writing');
  assert.equal(session.state.mutation.type,'idle');assert.equal(host.commands.length,1);assert.equal(session.snapshot()?.network.type,'unavailable');
});
test('failed refresh after a valid commit does not invent an uncertain write',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();host.snapshotHandler=async()=>{throw new Error('Read unavailable')};await session.capture('w1','Saved');assert.equal(session.state.mutation.type,'idle');assert.equal(session.state.host.type,'failed');assert.match(session.state.message,/Saved here/);
});
test('a submit result retains its Applied receipt while a newer snapshot is pending',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();
  const revision={id:'transcript-save',author:'local',title:'Transcript',body:'Lea will share the brief on Friday.',parents:[]};
  const committed=decodeSnapshot({...snapshot(),operation_count:2,resources:[...snapshot().resources,{id:'transcript',workspace_id:'w1',heads:[revision],history:[revision]}]});
  const ownRead=deferred<Snapshot>(),newerRead=deferred<Snapshot>(),started=deferred<void>();let reads=0;
  host.commandHandler=async()=>({type:'applied',operation_id:'transcript-save',workspace_id:'w1',replayed:false});
  host.snapshotHandler=()=>{if(reads++===0){started.resolve(undefined);return ownRead.promise}return newerRead.promise};
  const saving=session.submit({type:'create_resource',operation_id:'transcript-save',workspace_id:'w1',resource_id:'transcript',title:'Transcript',body:'Lea will share the brief on Friday.'});
  await started.promise;const refreshing=session.refresh();ownRead.resolve(committed);
  try {
    const receipt=await saving;
    assert.deepEqual(receipt,{type:'applied',operation_id:'transcript-save',workspace_id:'w1',replayed:false},'APPLIED_SUBMIT_RECEIPT_LOST_DURING_OVERLAPPING_REFRESH');
    assert.equal(session.snapshot()?.resources.some(resource=>resource.id==='transcript'),false);
  } finally {newerRead.resolve(committed);await refreshing;session.dispose()}
});
test('a returned invitation keeps the exact workspace selection that created it',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());const pending=deferred<Invite>();let sent:readonly string[]=[];
  host.invite=async(selection:readonly string[])=>{sent=selection;return pending.promise};
  const selected=['w1'];const work=session.createInvite(selected);selected.push('another-workspace');pending.resolve({ticket:'test-only-invite',expires_at_ms:1000});await work;
  assert.deepEqual(sent,['w1']);assert.equal(session.state.pairing.type,'invite');if(session.state.pairing.type==='invite')assert.deepEqual(session.state.pairing.workspaceIds,['w1']);
});
test('known no-commit rejection retains the editor and permits a corrected save',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();session.edit('note1');session.updateEditor('title','Invalid title');
  host.commandHandler=async c=>({type:'rejected',operation_id:c.operation_id,workspace_id:c.workspace_id,message:'Correct this title'});
  await session.saveEditor();assert.equal(session.state.mutation.type,'rejected');assert.equal(session.state.editor?.title,'Invalid title');
  session.updateEditor('title','Corrected title');host.commandHandler=async c=>({type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:false});await session.saveEditor();assert.equal(session.state.mutation.type,'idle');assert.notEqual(host.commands[0]?.operation_id,host.commands[1]?.operation_id);assert.equal(session.state.editor,null);
});

test('join progress belongs to one attempt and only a committed snapshot completes joining',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();
  const first=deferred<Snapshot>(),second=deferred<Snapshot>();let attempt=0;
  const callbacks:((stage:JoinStage)=>void)[]=[];
  host.joinHandler=async(_ticket,progress)=>{callbacks.push(progress!);return ++attempt===1?first.promise:second.promise};
  const joining=session.join('opaque-test-invitation');
  callbacks[0]!('connecting_relay');callbacks[0]!('checking_invitation');
  assert.deepEqual(session.state.pairing,{type:'joining',stage:'connecting_relay'});
  first.reject(new Error('untrusted backend detail must not reach UI'));await joining;
  assert.equal(session.state.pairing.type,'failed');
  assert.match(JSON.stringify(session.state.pairing),/relay connection/);
  assert.doesNotMatch(JSON.stringify(session.state.pairing),/untrusted backend detail/);
  const retry=session.join('opaque-test-invitation');
  callbacks[1]!('checking_invitation');callbacks[0]!('saving_workspace');
  assert.deepEqual(session.state.pairing,{type:'joining',stage:'checking_invitation'});
  callbacks[1]!('saving_workspace');
  assert.equal(session.state.pairing.type,'joining');
  second.resolve(snapshot('joined'));await retry;
  callbacks[1]!('connecting_relay');
  assert.deepEqual(session.state.pairing,{type:'idle'});
  assert.equal(session.snapshot()?.resources[0]?.heads[0]?.id,'joined');
});
test('a refresh started during join cannot replace the committed join snapshot',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();
  const join=deferred<Snapshot>(),read=deferred<Snapshot>();host.joinHandler=()=>join.promise;
  const joining=session.join('opaque-test-invitation');host.snapshotHandler=()=>read.promise;const refreshing=session.refresh();
  join.resolve(snapshot('joined'));await joining;read.resolve(snapshot('old'));await refreshing;
  assert.equal(session.snapshot()?.resources[0]?.heads[0]?.id,'joined');
});

test('new application or note editors cannot replace retained drafts',async()=>{
  const host=new HostFixture(),session=new WorkspaceSession(host,ids());await session.refresh();session.newNote('w1');session.updateEditor('body','Keep this draft');const original=session.state.editor;
  session.newSurface('w1',{type:'code_document',language:'python',text:'print(1)'});session.edit('note1');session.newNote('w1');assert.strictEqual(session.state.editor,original,'RETAINED_EDITOR_REPLACED');
  session.closeEditor();session.newSurface('w1',{type:'code_document',language:'python',text:'print(1)'});assert.notEqual(session.state.editor?.draftId,original?.draftId);
});
test('surface editor retains observed heads and local code through a conflict and exact retry',async()=>{
  const host=new HostFixture();host.value={...snapshot(),resources:[{...snapshot().resources[0]!,heads:[{id:'r1',author:'local',parents:[],title:'Code',surface:{type:'code_document',language:'python',text:'print(1)'}}]}]};
  const session=new WorkspaceSession(host,ids());await session.refresh();session.edit('note1');session.updateSurface({type:'code_document',language:'python',text:'print(2)'});
  host.value={...host.value,resources:[{...host.value.resources[0]!,heads:[{id:'r2',author:'peer',parents:['r1'],title:'Code',surface:{type:'code_document',language:'python',text:'print(3)'}}]}]};await session.refresh();
  host.commandHandler=async c=>c.type==='edit_surface'&&c.expected_heads[0]==='r1'?{type:'conflict',resource_id:'note1',expected_heads:['r1'],actual_heads:['r2']}:{type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:false};
  await session.saveEditor();assert.equal(session.state.mutation.type,'conflict');const draft=session.state.editor;assert.equal(draft?.type,'surface_existing');if(draft?.type==='surface_existing')assert.deepEqual(draft.surface,{type:'code_document',language:'python',text:'print(2)'});
  await session.saveEditor(['r2']);assert.equal(session.state.editor,null);const change=host.commands[1];assert.equal(change?.type,'edit_surface');if(change?.type==='edit_surface')assert.deepEqual(change.expected_heads,['r2']);
});
