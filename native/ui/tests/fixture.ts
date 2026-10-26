/** Browser test host only. Not imported by the production entry or build. */
import '../src/style.css';
import { mountApp } from '../src/app.ts';
import { isWorkRevision, isNoteRevision } from '../src/protocol.ts';
import type { MeetingPreparationPort, MeetingRequest, PreparedMeeting } from '../src/meeting-preparation.ts';
import type { LocalDraft } from '../../../browser/host/src/local-drafts.ts';
import type { LocalDraftPort } from '../src/retained-input.ts';
import { sameHeads } from '../src/session.ts';
import type { ConnectionState, ConnectionReceipt, SetConnection, RemoveConnection } from '../../../browser/host/src/connections.ts';
import type { VoiceEvent } from '../src/voice-session.ts';
import type { Host } from '../src/host.ts';
import type { Command, Invite, Receipt, Snapshot, SyncReport, Resource, JoinStage } from '../src/protocol.ts';
type SnapshotHold = Readonly<{type:'none'|'after_applied'}> | Readonly<{type:'holding';pending:readonly Readonly<{snapshot:Snapshot;resolve:(snapshot:Snapshot)=>void}>[]}>;
class ContractFixture implements Host {
  available=true; commands:Command[]=[]; inviteSelections:string[][]=[];joinTickets:string[]=[];failJoin=false;delayJoin=false;pendingJoin:{progress:((stage:JoinStage)=>void)|undefined;resolve:(value:Snapshot)=>void;reject:(error:Error)=>void}|null=null; joins=0; scanCalls=0; loseNextReceipt=false;rejectNext=false;
  value:Snapshot={device_id:'d'.repeat(64),device_name:'UI contract fixture',workspaces:[],resources:[],operation_count:0,network:{type:'active'},peers:[]};
  receipts=new Map<string,Receipt>();
  private snapshotHold:SnapshotHold=Object.freeze({type:'none'});
  async snapshot():Promise<Snapshot>{
    const snapshot=structuredClone(this.value),hold=this.snapshotHold;
    if(hold.type!=='holding')return snapshot;
    return new Promise(resolve=>{this.snapshotHold=Object.freeze({type:'holding',pending:Object.freeze([...hold.pending,Object.freeze({snapshot,resolve})])})});
  }
  holdSnapshotsAfterApplied():void{if(this.snapshotHold.type!=='none')throw new Error('Snapshot delivery is already held');this.snapshotHold=Object.freeze({type:'after_applied'})}
  pendingSnapshots():number{return this.snapshotHold.type==='holding'?this.snapshotHold.pending.length:0}
  releaseFirstSnapshot():void{
    const hold=this.snapshotHold;if(hold.type!=='holding'||!hold.pending.length)throw new Error('No pending snapshot');
    const [first,...pending]=hold.pending;this.snapshotHold=Object.freeze({type:'holding',pending:Object.freeze(pending)});first!.resolve(first!.snapshot);
  }
  resumeSnapshots():void{const hold=this.snapshotHold;this.snapshotHold=Object.freeze({type:'none'});if(hold.type==='holding')for(const read of hold.pending)read.resolve(read.snapshot)}
  async command(c:Command):Promise<Receipt>{
    this.commands.push(structuredClone(c));if(this.rejectNext){this.rejectNext=false;return{type:'rejected',operation_id:c.operation_id,workspace_id:c.workspace_id,message:'Correct the note title before saving.'}}const old=this.receipts.get(c.operation_id);if(old)return old;
    let resources=[...this.value.resources],workspaces=[...this.value.workspaces];
    switch(c.type){
      case'create_workspace':workspaces.push({id:c.workspace_id,title:c.title,placements:[],members:['d'.repeat(64)],heads:[c.operation_id]});break;
      case'create_resource':case'create_surface':{const h={id:c.operation_id,author:'d'.repeat(64),title:c.title,...(c.type==='create_resource'?{body:c.body}:{surface:c.surface}),parents:[]};resources.push({id:c.resource_id,workspace_id:c.workspace_id,heads:[h],history:[h]});workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:[...w.placements,{resource_id:c.resource_id,source_workspace_id:c.workspace_id}]}:w);break}
      case'edit_resource':case'edit_surface':{const r=resources.find(r=>r.id===c.resource_id);if(!r)throw new Error('Missing fixture note');if(!sameHeads(c.expected_heads,r.heads.map(h=>h.id)))return{type:'conflict',resource_id:r.id,expected_heads:c.expected_heads,actual_heads:r.heads.map(h=>h.id)};const h={id:c.operation_id,author:'d'.repeat(64),title:c.title,...(c.type==='edit_resource'?{body:c.body}:{surface:c.surface}),parents:[...c.expected_heads]};resources=resources.map(x=>x.id===r.id?{...x,heads:[h],history:[...x.history,h]}:x);break}
      case'create_work':{const h={id:c.operation_id,author:'d'.repeat(64),title:c.title,parents:[],work:{type:'meeting' as const,outcome:c.outcome,sources:c.sources,state:{type:'requested' as const}}};resources.push({id:c.resource_id,workspace_id:c.workspace_id,heads:[h],history:[h]});workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:[...w.placements,{resource_id:c.resource_id,source_workspace_id:c.workspace_id}]}:w);break;}
      case'edit_work':case'publish_meeting_materials':{
        const r=resources.find(r=>r.id===c.resource_id),head=r?.heads[0];if(!r||!head||!isWorkRevision(head))throw new Error('Missing fixture work');
        if(!sameHeads(c.expected_heads,r.heads.map(h=>h.id)))return{type:'conflict',resource_id:r.id,expected_heads:c.expected_heads,actual_heads:r.heads.map(h=>h.id)};
        let work=head.work,title=head.title;
        if(c.type==='edit_work'){const change=c.change;switch(change.type){
          case'revise_request':title=change.title;work={type:'meeting',outcome:change.outcome,sources:change.sources,state:{type:'requested'}};break;
          case'begin_preparation':work={...work,state:{type:'preparing',run:{id:change.run_id,originating_device:'d'.repeat(64),started_at_ms:change.started_at_ms}}};break;
          case'need_input':if(work.state.type!=='preparing')throw new Error('Not preparing');work={...work,state:{type:'needs_input',run:work.state.run,question:change.question}};break;
          case'correct_actions':if(work.state.type!=='materials_ready')throw new Error('Not ready');work={...work,state:{...work.state,actions:change.actions}};break;
        }}else{
          if(work.state.type!=='preparing')throw new Error('Not preparing');
          const material=(input:typeof c.notes)=>({reference:{workspace_id:c.workspace_id,resource_id:input.resource_id,revision_id:c.operation_id},citations:input.citations});
          for(const input of [c.notes,c.follow_up]){const output={id:c.operation_id,author:'d'.repeat(64),title:input.title,body:input.body,parents:[]};resources.push({id:input.resource_id,workspace_id:c.workspace_id,heads:[output],history:[output]});workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:[...w.placements,{resource_id:input.resource_id,source_workspace_id:c.workspace_id}]}:w);}
          work={...work,state:{type:'materials_ready',run:work.state.run,notes:material(c.notes),actions:c.actions,follow_up:{type:'unsent_text',material:material(c.follow_up)}}};
        }
        const h={id:c.operation_id,author:'d'.repeat(64),title,work,parents:c.expected_heads};resources=resources.map(item=>item.id===r.id?{...item,heads:[h],history:[...item.history,h]}:item);break;
      }
      case'add_placement':workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:[...w.placements,{resource_id:c.resource_id,source_workspace_id:c.source_workspace_id}]}:w);break;
      case'remove_placement':workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:w.placements.filter(p=>p.resource_id!==c.resource_id)}:w);break;
    }
    this.value={...this.value,workspaces,resources,operation_count:this.value.operation_count+1};const receipt:Receipt={type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:false};this.receipts.set(c.operation_id,receipt);
    if(this.loseNextReceipt){this.loseNextReceipt=false;throw new Error('Fixture lost the response after commit')}
    if(this.snapshotHold.type==='after_applied')this.snapshotHold=Object.freeze({type:'holding',pending:Object.freeze([])});
    return receipt;
  }
  async invite(selection:readonly string[]):Promise<Invite>{this.inviteSelections.push([...selection]);return{ticket:'workspace1:ui-contract-fixture-only',expires_at_ms:Date.now()+900000}}
  async join(ticket:string,progress?:(stage:JoinStage)=>void):Promise<Snapshot>{this.joins++;this.joinTickets.push(ticket);if(this.failJoin){this.failJoin=false;throw new Error('The peer could not be reached')}if(this.delayJoin){this.delayJoin=false;return new Promise((resolve,reject)=>{this.pendingJoin={progress,resolve,reject}})}return this.value}
  async sync():Promise<SyncReport>{return{peers:[]}}
  remoteEdit(resourceId:string):void{this.value={...this.value,resources:this.value.resources.map(r=>{if(r.id!==resourceId)return r;const h={id:'remote-head',author:'remote-fixture',title:'Remote revision',body:'Writing from another device',parents:r.heads.map(h=>h.id)};return{...r,heads:[h],history:[...r.history,h]}})}}
}
const host=new ContractFixture();
const speechSessions: {id:string;emit:(event:VoiceEvent)=>void}[]=[];
let failSpeech = false,updateOpens=0;
const connectionValues=new Map<string,ConnectionState>(),connectionReceipts=new Map<string,ConnectionReceipt>(),connectionWrites:(SetConnection|RemoveConnection)[]=[];
let loseConnectionReceipt=false;
const connectionPort={async connection(workspaceId:string):Promise<ConnectionState>{return connectionValues.get(workspaceId)??{workspace_id:workspaceId,provider:'openai',status:'missing'}},async setConnection(input:SetConnection):Promise<ConnectionReceipt>{connectionWrites.push(structuredClone(input));const old=connectionReceipts.get(input.operation_id);if(old)return old;const state:ConnectionState={workspace_id:input.workspace_id,provider:'openai',status:'ready',version:input.operation_id,model:'gpt-4o-transcribe'};const receipt={operation_id:input.operation_id,replayed:false,state};connectionValues.set(input.workspace_id,state);connectionReceipts.set(input.operation_id,receipt);if(loseConnectionReceipt){loseConnectionReceipt=false;throw new Error('Synthetic lost receipt')}return receipt},async removeConnection(input:RemoveConnection):Promise<ConnectionReceipt>{connectionWrites.push(input);const state:ConnectionState={workspace_id:input.workspace_id,provider:'openai',status:'removed',version:input.operation_id};connectionValues.set(input.workspace_id,state);return{operation_id:input.operation_id,replayed:false,state}}};

// Synthetic host-local persistence and provider. These prove UI contracts, not real
// browser storage, authenticated OpenAI execution, peer delivery, or hardware.
const localDrafts=new Map<string,LocalDraft>(),removedDrafts=new Map<string,number>();let failRetention=false;
const drafts:LocalDraftPort={
  async listLocalDrafts(){return{drafts:structuredClone([...localDrafts.values()]),removed:[...removedDrafts].map(([id,generation])=>({id,generation}))};},
  async upsertLocalDraft(input){if(failRetention)throw new Error('Synthetic storage failure');localDrafts.set(input.id,structuredClone(input));return{type:'retained',id:input.id,generation:input.generation,replayed:false};},
  async removeLocalDraft(input){if(failRetention)throw new Error('Synthetic storage failure');localDrafts.delete(input.id);removedDrafts.set(input.id,input.generation);return{type:'removed',id:input.id,generation:input.generation,replayed:false};},
};
const meetingRequests:MeetingRequest[]=[];let holdPreparation=false,finishPreparation:(()=>void)|null=null;
const meetings:MeetingPreparationPort={
  async connection(workspaceId){return{workspace_id:workspaceId,provider:'openai',status:'ready',version:'a'.repeat(64),model:'gpt-4o-transcribe'};},
  async prepareMeeting(request){
    meetingRequests.push(structuredClone(request));
    const revision=host.value.resources.find(r=>r.id===request.resource_id)?.history.find(h=>h.id===request.revision_id);
    if(!revision||!isWorkRevision(revision)||!revision.work.sources[0])throw new Error('Missing fixture source');
    const source=revision.work.sources[0],transcript=host.value.resources.find(r=>r.id===source.resource_id)?.history.find(h=>h.id===source.revision_id);
    if(!transcript||!isNoteRevision(transcript))throw new Error('Missing fixture transcript');
    const quote=transcript.body.slice(0,256),citations=[{source,quote}],basis={type:'transcript' as const,citations};
    const result:PreparedMeeting={notes:{title:'Meeting notes',body:'Synthetic meeting notes for UI review.',citations},actions:[{text:'Share the revised identity',owner:quote.includes('Lea')?{type:'assigned',name:'Lea',basis}:{type:'unassigned'},deadline:quote.includes('Friday')?{type:'stated',text:'Friday',basis}:{type:'unspecified'},basis}],follow_up:{title:'Meeting follow-up',body:'Synthetic follow-up text. Please review the revised identity.',citations}};
    if(holdPreparation){holdPreparation=false;await new Promise<void>(resolve=>{finishPreparation=resolve;});}
    return result;
  }
};

const options={host,drafts,meetings,openUpdate:()=>{updateOpens++},...(new URLSearchParams(location.search).has('provider')?{connections:connectionPort}:{}),voice:{async capability(){return new URLSearchParams(location.search).has('provider')?{type:'provider_transcription' as const,locale:'',provider_label:'OpenAI'}:new URLSearchParams(location.search).has('dictation')?{type:'system_dictation' as const}:{type:'browser_speech' as const,locale:'en-US'}},async start(id:string,emit:(event:VoiceEvent)=>void){if(failSpeech){failSpeech=false;throw new Error('Fixture microphone unavailable')}speechSessions.push({id,emit});emit({type:new URLSearchParams(location.search).has('dictation')?'system_dictation':'listening',session_id:id})},async stop(id:string){speechSessions.find(s=>s.id===id)?.emit({type:new URLSearchParams(location.search).has('provider')?'transcribing':'stopped',session_id:id})},async cancel(){}},scanner:{async available(){return true},async scan(){host.scanCalls++;return'workspace1:ui-contract-fixture-only'},async cancel(){}},pollMs:60000};
let app=mountApp(document.getElementById('app')!,options);
declare global {interface Window{fixture:{snapshots:{holdAfterApplied:()=>void;pending:()=>number;releaseFirst:()=>void;resume:()=>void};peerCorrection:()=>Promise<void>;drafts:()=>readonly LocalDraft[];failRetention:(fail:boolean)=>void;meetingRequests:()=>readonly MeetingRequest[];holdPreparation:()=>void;finishPreparation:()=>void;offerUpdate:(available?:boolean)=>void;updateOpens:()=>number;hasUnsubmittedInput:()=>boolean;connectionWrites:()=>readonly(SetConnection|RemoveConnection)[];loseConnectionReceipt:()=>void;finishSpeech:(text:string)=>void;commands:()=>Command[];resources:()=>readonly Resource[];loseReceipt:()=>void;rejectNext:()=>void;remoteEdit:()=>Promise<void>;joins:()=>number;scanCalls:()=>number;inviteSelections:()=>readonly string[][];joinTickets:()=>readonly string[];failNextJoin:()=>void;refresh:()=>Promise<void>;holdNextJoin:()=>void;joinStage:(stage:JoinStage)=>void;finishJoin:(titles?:string[])=>void;restart:()=>void;speech:(text:string,index?:number)=>void;failSpeech:()=>void;offline:()=>Promise<void>;failHeldJoin:()=>void}}}
window.fixture={snapshots:{holdAfterApplied:()=>host.holdSnapshotsAfterApplied(),pending:()=>host.pendingSnapshots(),releaseFirst:()=>host.releaseFirstSnapshot(),resume:()=>host.resumeSnapshots()},peerCorrection:async()=>{
  const r=host.value.resources.find(resource=>resource.heads.some(isWorkRevision)),head=r?.heads[0];if(!r||!head||!isWorkRevision(head)||head.work.state.type!=='materials_ready')throw new Error('No fixture work to correct');
  await host.command({type:'edit_work',operation_id:'fixture-peer-correction',workspace_id:r.workspace_id,resource_id:r.id,expected_heads:r.heads.map(h=>h.id),change:{type:'correct_actions',actions:head.work.state.actions.map(action=>({...action,deadline:{type:'stated',text:'Next Tuesday',basis:{type:'user'}}}))}});await app.session.refresh();
},drafts:()=>[...localDrafts.values()],failRetention:fail=>{failRetention=fail},meetingRequests:()=>meetingRequests,holdPreparation:()=>{holdPreparation=true},finishPreparation:()=>{finishPreparation?.();finishPreparation=null},offerUpdate:(available=true)=>app.setUpdateAvailable(available),updateOpens:()=>updateOpens,hasUnsubmittedInput:()=>app.hasUnsubmittedInput(),connectionWrites:()=>connectionWrites,loseConnectionReceipt:()=>{loseConnectionReceipt=true},finishSpeech:text=>{const s=speechSessions.at(-1);if(s){s.emit({type:'transcript',session_id:s.id,text,is_final:true});s.emit({type:'stopped',session_id:s.id})}},restart:()=>{app.dispose();app=mountApp(document.getElementById('app')!,options)},speech:(text,index=speechSessions.length-1)=>{const s=speechSessions[index];s?.emit({type:'transcript',session_id:s.id,text,is_final:true})},failSpeech:()=>{failSpeech=true},offline:async()=>{host.value={...host.value,network:{type:'unavailable',detail:'Fixture network loss'}};await app.session.refresh()},commands:()=>host.commands,resources:()=>host.value.resources,loseReceipt:()=>{host.loseNextReceipt=true},rejectNext:()=>{host.rejectNext=true},remoteEdit:async()=>{const id=host.value.resources[0]?.id;if(id){host.remoteEdit(id);await app.session.refresh()}},joins:()=>host.joins,scanCalls:()=>host.scanCalls,inviteSelections:()=>host.inviteSelections,joinTickets:()=>host.joinTickets,failNextJoin:()=>{host.failJoin=true},refresh:()=>app.session.refresh(),holdNextJoin:()=>{host.delayJoin=true},joinStage:stage=>host.pendingJoin?.progress?.(stage),finishJoin:(titles=[])=>{host.value={...host.value,workspaces:[...host.value.workspaces,...titles.map((title,i)=>({id:'received-'+i,title,placements:[],members:['d'.repeat(64)],heads:['received-head-'+i]}))]};host.pendingJoin?.resolve(host.value);host.pendingJoin=null},failHeldJoin:()=>{host.pendingJoin?.reject(new Error('Untrusted remote details'));host.pendingJoin=null}};
