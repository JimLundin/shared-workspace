import type { ConnectionState, ConnectionReceipt, SetConnection, RemoveConnection, ConnectionPeer } from '../../../browser/host/src/connections.ts';
export type { ConnectionState };
export interface ConnectionPort {
  connectionPeers?(workspaceId:string):Promise<readonly ConnectionPeer[]>;
  connection(workspaceId:string):Promise<ConnectionState>;
  setConnection(input:SetConnection):Promise<ConnectionReceipt>;
  removeConnection(input:RemoveConnection):Promise<ConnectionReceipt>;
}
type Change = Readonly<{type:'set';input:SetConnection}> | Readonly<{type:'remove';input:RemoveConnection}>;
export type ConnectionMutation = Readonly<{type:'idle'}> | Readonly<{type:'saving'|'uncertain';change:Change}>;
export const observedVersions=(state:ConnectionState):readonly string[]=>state.status==='missing'?[]:state.status==='conflict'?state.versions:[state.version];
const operationId=():string=>Array.from(crypto.getRandomValues(new Uint8Array(32)),value=>value.toString(16).padStart(2,'0')).join('');
/** Owns settings effects. Only an unacknowledged in-memory write retains entered key material. */
export class ConnectionSession {
  readonly #port:ConnectionPort;
  readonly #changed:()=>void;
  readonly #values=new Map<string,ConnectionState>();
  readonly #peerValues=new Map<string,readonly ConnectionPeer[]>();
  readonly #peerEpochs=new Map<string,number>();
  readonly #observations=new Map<string,number>();
  readonly #epochs=new Map<string,number>();
  #disposed=false;
  mutation:ConnectionMutation={type:'idle'};
  failedReads=new Set<string>();
  constructor(port:ConnectionPort,changed:()=>void){this.#port=port;this.#changed=changed;}
  current(workspaceId:string):ConnectionState|undefined{return this.#values.get(workspaceId)}
  peers(workspaceId:string):readonly ConnectionPeer[]{return this.#peerValues.get(workspaceId)??[]}
  async refreshPeers(workspaceId:string):Promise<void>{
    if(!this.#port.connectionPeers)return;
    const epoch=(this.#peerEpochs.get(workspaceId)??0)+1;this.#peerEpochs.set(workspaceId,epoch);
    try{const value=await this.#port.connectionPeers(workspaceId);if(!this.#disposed&&this.#peerEpochs.get(workspaceId)===epoch){this.#peerValues.set(workspaceId,value);this.#changed()}}catch{/* Connection settings remain usable if peer observations are unavailable. */}
  }
  async refresh(workspaceId:string):Promise<boolean>{
    const epoch=(this.#epochs.get(workspaceId)??0)+1;this.#epochs.set(workspaceId,epoch);
    try {const value=await this.#port.connection(workspaceId);if(this.#disposed||this.#epochs.get(workspaceId)!==epoch)return false;if(value.workspace_id!==workspaceId)throw new Error('Wrong connection scope');this.#values.set(workspaceId,value);this.#observations.set(workspaceId,(this.#observations.get(workspaceId)??0)+1);this.failedReads.delete(workspaceId);this.#changed();return true;}
    catch {if(!this.#disposed&&this.#epochs.get(workspaceId)===epoch){this.failedReads.add(workspaceId);this.#changed();}return false;}
  }
  async set(workspaceId:string,key:string):Promise<void>{
    const state=this.current(workspaceId);if(!state||this.mutation.type!=='idle')return;
    await this.#save({type:'set',input:{operation_id:operationId(),workspace_id:workspaceId,expected_versions:[...observedVersions(state)],api_key:key,model:'gpt-4o-transcribe'}});
  }
  async remove(workspaceId:string):Promise<void>{
    const state=this.current(workspaceId);if(!state||this.mutation.type!=='idle')return;
    await this.#save({type:'remove',input:{operation_id:operationId(),workspace_id:workspaceId,expected_versions:[...observedVersions(state)]}});
  }
  async retry():Promise<void>{if(this.mutation.type==='uncertain')await this.#save(this.mutation.change)}
  async reviewCurrent():Promise<void>{
    const pending=this.mutation;if(pending.type!=='uncertain')return;
    const accepted=await this.refresh(pending.change.input.workspace_id);
    if(!this.#disposed&&this.mutation===pending&&accepted){this.mutation={type:'idle'};this.#changed();}
  }
  async #save(change:Change):Promise<void>{
    if(this.#disposed)return;
    const id=change.input.workspace_id,observation=this.#observations.get(change.input.workspace_id)??0;this.#epochs.set(id,(this.#epochs.get(id)??0)+1);
    const attempt:ConnectionMutation={type:'saving',change};this.mutation=attempt;this.#changed();
    try {const receipt=await (change.type==='set'?this.#port.setConnection(change.input):this.#port.removeConnection(change.input));
      if(this.#disposed||this.mutation!==attempt)return;if(receipt.operation_id!==change.input.operation_id||receipt.state.workspace_id!==id)throw new Error('Wrong receipt');
      this.#epochs.set(id,(this.#epochs.get(id)??0)+1);
      if((this.#observations.get(id)??0)===observation)this.#values.set(id,receipt.state);this.mutation={type:'idle'};this.failedReads.delete(id);this.#changed();
    }catch {if(!this.#disposed&&this.mutation===attempt){this.mutation={type:'uncertain',change};this.#changed();}}
  }
  dispose():void{this.#disposed=true;this.#values.clear();this.mutation={type:'idle'}}
}
