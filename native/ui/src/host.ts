import { invoke, isTauri } from '@tauri-apps/api/core';
import { decodeInvite, decodeReceipt, decodeSnapshot, decodeSync } from './protocol.ts';
import type { Command, Invite, Receipt, Snapshot, SyncReport } from './protocol.ts';
export interface Host {
  readonly available: boolean;
  snapshot(): Promise<Snapshot>;
  command(command: Command): Promise<Receipt>;
  invite(workspaceIds: readonly string[]): Promise<Invite>;
  join(ticket: string): Promise<Snapshot>;
  sync(): Promise<SyncReport>;
}
export type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;
export class NativeHost implements Host {
  readonly available: boolean;
  private readonly call: Invoke;
  constructor(call: Invoke = invoke, available: boolean = isTauri()) { this.call = call; this.available = available; }
  async snapshot(): Promise<Snapshot> { return decodeSnapshot(await this.call('host_snapshot')); }
  async command(command: Command): Promise<Receipt> { return decodeReceipt(await this.call('host_command', { command })); }
  async invite(workspaceIds: readonly string[]): Promise<Invite> { return decodeInvite(await this.call('host_invite', { workspace_ids: [...workspaceIds], ttl_seconds: 900 })); }
  async join(ticket: string): Promise<Snapshot> { return decodeSnapshot(await this.call('host_join', { ticket })); }
  async sync(): Promise<SyncReport> { return decodeSync(await this.call('host_sync')); }
}
