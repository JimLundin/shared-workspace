import { decodeInvite, decodeReceipt, decodeSnapshot, decodeSync, decodeJoinStage, object, string, type JoinStage } from '../../../native/ui/src/protocol.ts';
import type { Command, HostOptions, Invite, Receipt, Snapshot, SyncReport } from './types.ts';
export type { Command, HostOptions, Invite, Receipt, Snapshot, SyncReport } from './types.ts';

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
