import test from 'node:test';import assert from 'node:assert/strict';
import {captureAddress,commandPlan} from './commands.ts';import type {Snapshot} from './protocol.ts';
const data:Snapshot={device_id:'d',device_name:'Host',workspaces:[{id:'w',title:'Work',placements:[{resource_id:'n',source_workspace_id:'w'},{resource_id:'n2',source_workspace_id:'w'}],members:['d'],heads:[]}],resources:[{id:'n',workspace_id:'w',heads:[{id:'r',author:'d',title:'Brief',body:'Text',parents:[]}],history:[]},{id:'n2',workspace_id:'w',heads:[{id:'r2',author:'d',title:'Draft',body:'Text',parents:[]}],history:[]}],operation_count:1,network:{type:'active'},peers:[]};
test('a deictic command retains captured resource after focus changes',()=>{const address=captureAddress(data,{type:'workspace',id:'w'},'n');const plan=commandPlan(data,{type:'workspace',id:'w'},{text:'Close this view',address,source:'typed'});assert.equal(plan.type,'ready');if(plan.type==='ready'){assert.deepEqual(plan.intent,{type:'close',resourceId:'n'});assert.match(plan.label,/Brief/)}assert.deepEqual(address.heads,['r'])});
test('workspace navigation cannot retarget a pending note command',()=>{const address=captureAddress(data,{type:'workspace',id:'w'},null);const pending={text:'Remember this decision',address,source:'voice' as const};assert.equal(commandPlan(data,{type:'home'},pending).type,'unavailable');const plan=commandPlan(data,{type:'workspace',id:'w'},pending);assert.equal(plan.type,'ready');if(plan.type==='ready')assert.deepEqual(plan.intent,{type:'capture',workspaceId:'w',body:'this decision'})});
test('duplicate workspace titles require a choice instead of guessing',()=>{const duplicate={...data,workspaces:[...data.workspaces,{...data.workspaces[0]!,id:'other'}]};const plan=commandPlan(duplicate,{type:'home'},{text:'Open Work',address:captureAddress(duplicate,{type:'home'},null),source:'typed'});assert.equal(plan.type,'unavailable')});
test('unsupported agent requests never become pretend execution',()=>{const plan=commandPlan(data,{type:'home'},{text:'Send my email',address:captureAddress(data,{type:'home'},null),source:'typed'});assert.equal(plan.type,'unavailable')});

test('voice can add real app presets, opens existing material first and never executes arbitrary agents',()=>{
  const route={type:'workspace',id:'w'} as const,address=captureAddress(data,route,null);
  for(const [text,preset] of [['Open Word','word'],['Open VS Code','vscode'],['New code document','code']]){
    const plan=commandPlan(data,route,{text:text!,address,source:'voice'});assert.equal(plan.type,'ready');if(plan.type==='ready')assert.deepEqual(plan.intent,{type:'surface',workspaceId:'w',preset});
  }
  const existing={...data,resources:[{...data.resources[0]!,heads:[{...data.resources[0]!.heads[0]!,title:'Word'}]},data.resources[1]!]};
  const plan=commandPlan(existing,route,{text:'Open Word',address,source:'voice'});assert.equal(plan.type,'ready');if(plan.type==='ready')assert.deepEqual(plan.intent,{type:'open',workspaceId:'w',resourceId:'n'});
  assert.equal(commandPlan(data,route,{text:'Open constructor',address,source:'voice'}).type,'unavailable');
  const dictation=commandPlan(data,route,{text:'Send my email',address,source:'voice'});assert.equal(dictation.type,'ready');if(dictation.type==='ready')assert.deepEqual(dictation.intent,{type:'capture',workspaceId:'w',body:'Send my email'});
});
