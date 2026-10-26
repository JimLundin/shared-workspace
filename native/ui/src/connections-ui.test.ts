import test from 'node:test';
import assert from 'node:assert/strict';
import { ConnectionSession } from './connections-ui.ts';
import type { ConnectionPort } from './connections-ui.ts';
import type { ConnectionState, ConnectionReceipt, SetConnection, RemoveConnection } from '../../../browser/host/src/connections.ts';
const ready=(workspace_id='work',version='a'.repeat(64)):ConnectionState=>({status:'ready',workspace_id,version,provider:'openai',model:'gpt-4o-transcribe'});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve}}
class Port implements ConnectionPort {
  writes:(SetConnection|RemoveConnection)[]=[];
  read: (id:string)=>Promise<ConnectionState>=async id=>ready(id);
  write:(input:SetConnection|RemoveConnection)=>Promise<ConnectionReceipt>=async input=>({operation_id:input.operation_id,replayed:false,state:ready(input.workspace_id,input.operation_id)});
  connection(id:string){return this.read(id)}
  setConnection(input:SetConnection){this.writes.push(input);return this.write(input)}
  removeConnection(input:RemoveConnection){this.writes.push(input);return this.write(input)}
}
test('uncertain settings retry preserves exact operation, workspace, key and observed heads',async()=>{
  const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');let first=true;
  port.write=async input=>{if(first){first=false;throw new Error('Lost receipt')}return {operation_id:input.operation_id,replayed:true,state:ready(input.workspace_id,input.operation_id)}};
  await session.set('work','synthetic-key-never-real');assert.equal(session.mutation.type,'uncertain');
  port.read=async id=>ready(id,'b'.repeat(64));await session.refresh('work');await session.refresh('private');
  await session.set('private','must-not-replace-pending');assert.equal(port.writes.length,1);
  await session.retry();assert.equal(port.writes.length,2);assert.strictEqual(port.writes[0],port.writes[1]);
  assert.deepEqual(port.writes[1]?.expected_versions,['a'.repeat(64)]);assert.equal(port.writes[1]?.workspace_id,'work');
  assert.equal(session.current('private')?.workspace_id,'private');assert.equal(session.mutation.type,'idle');
  assert.doesNotMatch(JSON.stringify(session.current('work')),/synthetic-key/);
});
test('read started while save is pending cannot overwrite its confirmed receipt',async()=>{
  const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');
  const receipt=deferred<ConnectionReceipt>(),observation=deferred<ConnectionState>();port.write=()=>receipt.promise;
  const save=session.remove('work');port.read=()=>observation.promise;const read=session.refresh('work');const sent=port.writes[0]!;
  const removed:ConnectionState={status:'removed',workspace_id:'work',version:'c'.repeat(64),provider:'openai'};
  receipt.resolve({operation_id:sent.operation_id,replayed:false,state:removed});await save;observation.resolve(ready());await read;
  assert.deepEqual(session.current('work'),removed,'LATE_READ_OVERWROTE_RECEIPT');assert.equal(session.mutation.type,'idle');
});
test('receipt correlation rejects another operation or workspace without dropping retry material',async()=>{
  for(const mismatch of ['operation','workspace']){
    const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');
    port.write=async input=>({operation_id:mismatch==='operation'?'d'.repeat(64):input.operation_id,replayed:false,state:ready(mismatch==='workspace'?'private':'work')});
    await session.set('work','synthetic-key');assert.equal(session.mutation.type,'uncertain','UNRELATED_CONNECTION_RECEIPT_ACCEPTED');assert.deepEqual(session.current('work'),ready());
  }
});
test('a replay receipt may observe a newer setting and remains correlated by operation identity',async()=>{
  const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');
  port.write=async input=>({operation_id:input.operation_id,replayed:true,state:ready('work','f'.repeat(64))});
  await session.set('work','synthetic-key');assert.equal(session.mutation.type,'idle');assert.deepEqual(session.current('work'),ready('work','f'.repeat(64)));
});
test('explicit settings review keeps uncertainty if reading fails, then releases the pending key',async()=>{
  const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');port.write=async()=>{throw new Error('Lost receipt')};await session.set('work','synthetic-key');
  port.read=async()=>{throw new Error('Disconnected')};await session.reviewCurrent();assert.equal(session.mutation.type,'uncertain');
  port.read=async()=>ready();await session.reviewCurrent();assert.equal(session.mutation.type,'idle');assert.equal(session.failedReads.has('work'),false);
});
test('disposed settings ignore late reads and confirmations',async()=>{
  const port=new Port();let changed=0;const session=new ConnectionSession(port,()=>{changed++});await session.refresh('work');const pending=deferred<ConnectionReceipt>();port.write=()=>pending.promise;const save=session.set('work','synthetic-key');const count=changed;session.dispose();pending.resolve({operation_id:port.writes[0]!.operation_id,replayed:false,state:ready()});await save;assert.equal(changed,count);assert.equal(session.current('work'),undefined);assert.equal(session.mutation.type,'idle');
});

test('late save acknowledgement preserves a newer removal already observed during the save',async()=>{
  const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');const pending=deferred<ConnectionReceipt>();port.write=()=>pending.promise;const save=session.set('work','synthetic-key');
  const removed:ConnectionState={status:'removed',workspace_id:'work',version:'c'.repeat(64),provider:'openai'};port.read=async()=>removed;await session.refresh('work');
  pending.resolve({operation_id:port.writes[0]!.operation_id,replayed:false,state:ready()});await save;assert.deepEqual(session.current('work'),removed,'LATE_RECEIPT_OVERWROTE_REMOVAL');assert.equal(session.mutation.type,'idle');
});
test('late settings review cannot clear a pending retry or enable overlapping writes',async()=>{
  const port=new Port(),session=new ConnectionSession(port,()=>{});await session.refresh('work');port.write=async()=>{throw new Error('Lost receipt')};await session.set('work','synthetic-key');
  const observation=deferred<ConnectionState>(),response=deferred<ConnectionReceipt>();port.read=()=>observation.promise;const review=session.reviewCurrent();port.write=()=>response.promise;const retry=session.retry();observation.resolve(ready());await review;
  assert.equal(session.mutation.type,'saving','LATE_REVIEW_CLEARED_RETRY');await session.set('work','another-key');assert.equal(port.writes.length,2);response.resolve({operation_id:port.writes[1]!.operation_id,replayed:true,state:ready()});await retry;assert.equal(session.mutation.type,'idle');
});
