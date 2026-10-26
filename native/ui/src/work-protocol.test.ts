import test from 'node:test';
import assert from 'node:assert/strict';
import {decodeWork,decodeWorkChange,decodeMeetingActions,decodeMeetingMaterialInput,decodeSnapshot,isWorkRevision} from './protocol.ts';
const source=()=>({workspace_id:'work',resource_id:'transcript',revision_id:'source-1'});
const citation=()=>({source:source(),quote:'Alice will send the brief on Friday.'});
const actions=()=>[{id:'action-1',text:'Send the brief',owner:{type:'assigned',name:'Alice',basis:{type:'transcript',citations:[citation()]}},deadline:{type:'stated',text:'Friday',basis:{type:'transcript',citations:[citation()]}},basis:{type:'transcript',citations:[citation()]}}];
const ready=()=>({type:'meeting',outcome:'Prepare the follow-up',sources:[source()],state:{type:'materials_ready',run:{id:'run-1',originating_device:'ab'.repeat(32),started_at_ms:1234},notes:{reference:{workspace_id:'work',resource_id:'notes',revision_id:'publish'},citations:[citation()]},actions:actions(),follow_up:{type:'unsent_text',material:{reference:{workspace_id:'work',resource_id:'follow-up',revision_id:'publish'},citations:[citation()]}}}});
test('work reads retain run, source and publication identity as immutable values',()=>{
  const input=ready(),work=decodeWork(input);assert.equal(work.state.type,'materials_ready');
  if(work.state.type!=='materials_ready')throw new Error('missing ready state');
  assert.equal(work.state.run.started_at_ms,1234);assert.equal(work.state.notes.reference.revision_id,'publish');assert.equal(work.state.follow_up.type,'unsent_text');
  assert.ok(Object.isFrozen(work)&&Object.isFrozen(work.sources)&&Object.isFrozen(work.sources[0]));assert.ok(Object.isFrozen(work.state.actions[0]?.owner));
  input.sources[0]!.revision_id='retargeted';assert.equal(work.sources[0]?.revision_id,'source-1');
  assert.deepEqual(decodeWork({type:'meeting',outcome:'Retain the intent',sources:[],state:{type:'requested'}}).sources,[]);
  const snapshot=decodeSnapshot({device_id:'ab'.repeat(32),device_name:'A',workspaces:[],resources:[{id:'meeting',workspace_id:'work',heads:[{id:'publish',author:'ab'.repeat(32),title:'Meeting',parents:['begin'],work:ready()}],history:[]}],operation_count:1,network:{type:'paused'},peers:[]});assert.ok(isWorkRevision(snapshot.resources[0]!.heads[0]!));
});
test('decoding preserves user corrections without labeling them transcript claims',()=>{
  const user=[{id:'action-1',text:'Changed by user',owner:{type:'assigned',name:'Person absent from transcript',basis:{type:'user'}},deadline:{type:'stated',text:'Next Wednesday',basis:{type:'user'}},basis:{type:'user'}}];
  assert.equal(decodeMeetingActions(user)[0]?.owner.type,'assigned');
  assert.equal(decodeWorkChange({type:'correct_actions',actions:user}).type,'correct_actions');
  assert.throws(()=>decodeMeetingActions(user,true),/user correction/);
  const invented=actions();invented[0]!.owner.name='Invented';assert.throws(()=>decodeMeetingActions(invented,true),/absent/);
  const inferred=actions();inferred[0]!.deadline.text='2028-01-01';assert.throws(()=>decodeMeetingActions(inferred,true),/absent/);
});
test('unsupported or malformed work cannot become a successful decoded observation',()=>{
  const invalid:unknown[]=[{...ready(),type:'unknown'},{...ready(),state:{type:'completed'}},{...ready(),sources:[]},{...ready(),extra:'hidden'}, {...ready(),state:{type:'preparing',run:{id:'run',originating_device:'bad',started_at_ms:0}}}];
  for(const item of invalid)assert.throws(()=>decodeWork(item));
  const foreign=ready();foreign.state.follow_up.material.citations[0]!.source.workspace_id='private';assert.throws(()=>decodeWork(foreign),/outside/);
  const duplicate=ready();duplicate.state.actions.push(duplicate.state.actions[0]!);assert.throws(()=>decodeWork(duplicate),/duplicate/);
  assert.throws(()=>decodeWorkChange({type:'begin_preparation',run_id:'run',started_at_ms:-1}));
  assert.throws(()=>decodeWorkChange({type:'need_input',run_id:'run',question:''}));
  assert.throws(()=>decodeMeetingMaterialInput({resource_id:'output',title:'Notes',body:'text',citations:[]}));
  assert.throws(()=>decodeMeetingMaterialInput({resource_id:'output',title:'Notes',body:'x'.repeat(128*1024+1),citations:[citation()]}));
  const mixed={id:'revision',author:'ab'.repeat(32),title:'Mixed',parents:[],work:ready(),body:'Pretend note'};
  assert.throws(()=>decodeSnapshot({device_id:'A',device_name:'A',workspaces:[],resources:[{id:'meeting',workspace_id:'work',heads:[mixed],history:[]}],operation_count:1,network:{type:'paused'},peers:[]}),/exactly one/);
});
