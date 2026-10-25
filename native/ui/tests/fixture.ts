/** Browser test host only. Not imported by the production entry or build. */
import '../src/style.css';
import { mountApp } from '../src/app.ts';
import { sameHeads } from '../src/session.ts';
import type { Host } from '../src/host.ts';
import type { Command, Invite, Receipt, Snapshot, SyncReport, Resource } from '../src/protocol.ts';
class ContractFixture implements Host {
  available=true; commands:Command[]=[]; joins=0; scanCalls=0; loseNextReceipt=false;rejectNext=false;
  value:Snapshot={device_id:'fixture-device',device_name:'UI contract fixture',workspaces:[],resources:[],operation_count:0,network:{type:'active'},peers:[]};
  receipts=new Map<string,Receipt>();
  async snapshot(){return structuredClone(this.value)}
  async command(c:Command):Promise<Receipt>{
    this.commands.push(structuredClone(c));if(this.rejectNext){this.rejectNext=false;return{type:'rejected',operation_id:c.operation_id,workspace_id:c.workspace_id,message:'Correct the note title before saving.'}}const old=this.receipts.get(c.operation_id);if(old)return old;
    let resources=[...this.value.resources],workspaces=[...this.value.workspaces];
    switch(c.type){
      case'create_workspace':workspaces.push({id:c.workspace_id,title:c.title,placements:[],members:['fixture-device'],heads:[c.operation_id]});break;
      case'create_resource':{const h={id:c.operation_id,author:'fixture-device',title:c.title,body:c.body,parents:[]};resources.push({id:c.resource_id,workspace_id:c.workspace_id,heads:[h],history:[h]});workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:[...w.placements,{resource_id:c.resource_id,source_workspace_id:c.workspace_id}]}:w);break}
      case'edit_resource':{const r=resources.find(r=>r.id===c.resource_id);if(!r)throw new Error('Missing fixture note');if(!sameHeads(c.expected_heads,r.heads.map(h=>h.id)))return{type:'conflict',resource_id:r.id,expected_heads:c.expected_heads,actual_heads:r.heads.map(h=>h.id)};const h={id:c.operation_id,author:'fixture-device',title:c.title,body:c.body,parents:[...c.expected_heads]};resources=resources.map(x=>x.id===r.id?{...x,heads:[h],history:[...x.history,h]}:x);break}
      case'add_placement':workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:[...w.placements,{resource_id:c.resource_id,source_workspace_id:c.source_workspace_id}]}:w);break;
      case'remove_placement':workspaces=workspaces.map(w=>w.id===c.workspace_id?{...w,placements:w.placements.filter(p=>p.resource_id!==c.resource_id)}:w);break;
    }
    this.value={...this.value,workspaces,resources,operation_count:this.value.operation_count+1};const receipt:Receipt={type:'applied',operation_id:c.operation_id,workspace_id:c.workspace_id,replayed:false};this.receipts.set(c.operation_id,receipt);
    if(this.loseNextReceipt){this.loseNextReceipt=false;throw new Error('Fixture lost the response after commit')}
    return receipt;
  }
  async invite():Promise<Invite>{return{ticket:'workspace1:ui-contract-fixture-only',expires_at_ms:Date.now()+900000}}
  async join():Promise<Snapshot>{this.joins++;return this.value}
  async sync():Promise<SyncReport>{return{peers:[]}}
  remoteEdit(resourceId:string):void{this.value={...this.value,resources:this.value.resources.map(r=>{if(r.id!==resourceId)return r;const h={id:'remote-head',author:'remote-fixture',title:'Remote revision',body:'Writing from another device',parents:r.heads.map(h=>h.id)};return{...r,heads:[h],history:[...r.history,h]}})}}
}
const host=new ContractFixture();
const app=mountApp(document.getElementById('app')!,{host,voice:{async capability(){return{type:'unavailable',reason:'Voice is not exercised by this browser fixture.'}},async start(){throw new Error('Not a native speech test')},async stop(){},async cancel(){}},scanner:{async available(){return true},async scan(){host.scanCalls++;return'workspace1:ui-contract-fixture-only'},async cancel(){}},pollMs:60000});
declare global {interface Window{fixture:{commands:()=>Command[];resources:()=>readonly Resource[];loseReceipt:()=>void;rejectNext:()=>void;remoteEdit:()=>Promise<void>;joins:()=>number;scanCalls:()=>number}}}
window.fixture={commands:()=>host.commands,resources:()=>host.value.resources,loseReceipt:()=>{host.loseNextReceipt=true},rejectNext:()=>{host.rejectNext=true},remoteEdit:async()=>{const id=host.value.resources[0]?.id;if(id){host.remoteEdit(id);await app.session.refresh()}},joins:()=>host.joins,scanCalls:()=>host.scanCalls};
