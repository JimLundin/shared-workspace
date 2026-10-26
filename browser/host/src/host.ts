import { decodeInvite, decodeReceipt, decodeSnapshot, decodeSync, decodeJoinStage, object, string, type JoinStage } from '../../../native/ui/src/protocol.ts';
import type { Command, HostOptions, Invite, Receipt, Snapshot, SyncReport } from './types.ts';
import { decodeConnectionPeers, decodeConnectionReceipt, decodeConnectionState, type ConnectionPeer, type ConnectionReceipt, type ConnectionState, type SetConnection, type RemoveConnection, type Transcription } from './connections.ts';
import { decodeMeetingRequest, decodePreparedMeeting, type MeetingRequest, type PreparedMeeting } from '../../../native/ui/src/meeting-preparation.ts';
import { decodeLocalDraftJournal, decodeLocalDraftReceipt, type LocalDraft, type LocalDraftRemoval, type LocalDraftJournal, type LocalDraftReceipt } from './local-drafts.ts';
export type { Command, HostOptions, Invite, Receipt, Snapshot, SyncReport } from './types.ts';
export type { ConnectionPeer, ConnectionReceipt, ConnectionState, SetConnection, RemoveConnection, Transcription } from './connections.ts';
export type { MeetingRequest, PreparedMeeting } from '../../../native/ui/src/meeting-preparation.ts';
export type { LocalDraft, LocalDraftRemoval, LocalDraftJournal, LocalDraftReceipt } from './local-drafts.ts';

type Pending = Readonly<{ resolve: (value: unknown) => void; reject: (reason: unknown) => void; progress?: (stage: JoinStage) => void }>;

/** Typed UI facade. SQL, signing, and transport live in one dedicated worker. */
export class BrowserHost {
  readonly available = true;
  readonly #worker: Worker;
  readonly #pending = new Map<string, Pending>();
  #closed = false;
  #closing: Promise<void> | null = null;

  private constructor(worker: Worker) {
    this.#worker = worker;
    worker.addEventListener('message', event => {
      const response = object(event.data), id = string(response.id), pending = this.#pending.get(id);
      if (!pending) return;
      if (response.type === 'join_progress') {
        pending.progress?.(decodeJoinStage(response.stage));
        return;
      }
      this.#pending.delete(id);
      if (response.ok === true) pending.resolve(response.value);
      else pending.reject(new Error(string(response.error)));
    });
    worker.addEventListener('error', event => {
      const error = new Error(event.message || 'Browser host worker ended unexpectedly. Saved work will be recovered when reopened.');
      this.#fail(error);
    });
    worker.addEventListener('messageerror', () => this.#fail(new Error('Browser host returned an unreadable message.')));
  }

  static async open(options: HostOptions = {}): Promise<BrowserHost> {
    if (!isSecureContext || !crossOriginIsolated || !navigator.locks || !navigator.storage?.getDirectory) {
      throw new Error('Open this workspace on a secure isolated page in a browser with OPFS and Web Locks.');
    }
    const host = new BrowserHost(new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'workspace-host' }));
    let timeout: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        host.#call('open', { ...options, baseUrl: options.baseUrl ?? new URL('.', location.href).href }),
        new Promise<never>((_, reject) => {
          timeout = setTimeout(() => reject(new Error('Browser host did not finish opening. Close and reopen this tab to recover saved work.')), 60_000);
        }),
      ]);
      return host;
    } catch (error) {
      host.#worker.terminate();
      host.#fail(error);
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  #fail(error: unknown): void {
    this.#worker.terminate();
    this.#closed = true;
    for (const pending of this.#pending.values()) pending.reject(error);
    this.#pending.clear();
  }

  #call(method: string, value?: unknown, progress?: (stage: JoinStage) => void): Promise<unknown> {
    if (this.#closed) return Promise.reject(new Error('Browser host is closed.'));
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject, ...(progress ? { progress } : {}) });
      try { this.#worker.postMessage({ id, method, value }); }
      catch (error) { this.#pending.delete(id); reject(error); }
    });
  }

  async snapshot(): Promise<Snapshot> { return decodeSnapshot(await this.#call('snapshot')); }
  async command(command: Command): Promise<Receipt> { return decodeReceipt(await this.#call('command', command)); }
  async invite(workspaceIds: readonly string[]): Promise<Invite> { return decodeInvite(await this.#call('invite', [...workspaceIds])); }
  async join(ticket: string, onProgress?: (stage: JoinStage) => void): Promise<Snapshot> { return decodeSnapshot(await this.#call('join', ticket, onProgress)); }
  async sync(): Promise<SyncReport> { return decodeSync(await this.#call('sync')); }
  async setForeground(foreground: boolean): Promise<void> { await this.#call('foreground', foreground); }
  async connection(workspaceId: string): Promise<ConnectionState> { return decodeConnectionState(await this.#call('connection', workspaceId)); }
  async connectionPeers(workspaceId: string): Promise<readonly ConnectionPeer[]> { return decodeConnectionPeers(await this.#call('connection_peers', workspaceId)); }
  async setConnection(input: SetConnection): Promise<ConnectionReceipt> { return decodeConnectionReceipt(await this.#call('set_connection', input)); }
  async removeConnection(input: RemoveConnection): Promise<ConnectionReceipt> { return decodeConnectionReceipt(await this.#call('remove_connection', input)); }
  async listLocalDrafts(): Promise<LocalDraftJournal> { return decodeLocalDraftJournal(await this.#call('list_local_drafts')); }
  async upsertLocalDraft(input: LocalDraft): Promise<LocalDraftReceipt> { return decodeLocalDraftReceipt(await this.#call('upsert_local_draft', input)); }
  async removeLocalDraft(input: LocalDraftRemoval): Promise<LocalDraftReceipt> { return decodeLocalDraftReceipt(await this.#call('remove_local_draft', input)); }
  async prepareMeeting(value: MeetingRequest, signal?: AbortSignal): Promise<PreparedMeeting> {
    if (this.#closed) throw new Error('Browser host is closed.');
    if (signal?.aborted) throw new Error('Preparation was cancelled.');
    const input = decodeMeetingRequest(value), id = crypto.randomUUID();
    const cancel = () => {
      this.#pending.get(id)?.reject(new Error('Preparation was cancelled.'));
      this.#pending.delete(id);
      if (!this.#closed) this.#worker.postMessage({ id: crypto.randomUUID(), method: 'cancel_preparation', value: id });
    };
    signal?.addEventListener('abort', cancel, { once: true });
    try {
      return decodePreparedMeeting(await new Promise<unknown>((resolve, reject) => {
        this.#pending.set(id, { resolve, reject });
        try { this.#worker.postMessage({ id, method: 'prepare_meeting', value: input }); }
        catch { this.#pending.delete(id); reject(new Error('The preparation could not reach the workspace host.')); }
      }));
    } finally { signal?.removeEventListener('abort', cancel); }
  }
  async transcribe(input: Transcription, signal?: AbortSignal): Promise<string> {
    if (this.#closed) throw new Error('Browser host is closed.');
    if (signal?.aborted) throw new Error('Transcription was cancelled.');
    const id = crypto.randomUUID();
    const cancel = () => {
      this.#pending.get(id)?.reject(new Error('Transcription was cancelled.'));
      this.#pending.delete(id);
      if (!this.#closed) this.#worker.postMessage({ id: crypto.randomUUID(), method: 'cancel_transcription', value: id });
    };
    signal?.addEventListener('abort', cancel, { once: true });
    try {
      return string(await new Promise<unknown>((resolve, reject) => {
        this.#pending.set(id, { resolve, reject });
        try { this.#worker.postMessage({ id, method: 'transcribe', value: input }); }
        catch { this.#pending.delete(id); reject(new Error('The audio capture could not reach the workspace host.')); }
      }));
    } finally { signal?.removeEventListener('abort', cancel); }
  }

  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    if (this.#closed) return Promise.resolve();
    this.#closing = this.#call('close').then(() => undefined).finally(() => {
      this.#worker.terminate();
      this.#fail(new Error('Browser host is closed.'));
    });
    return this.#closing;
  }
}

/** Remove enrollment secrets from the address bar before asking the user to Join. */
export function takePairingTicketFromLocation(): string | null {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const ticket = fragment.get('invite');
  if (ticket !== null) history.replaceState(history.state, '', location.pathname + location.search);
  return ticket;
}
