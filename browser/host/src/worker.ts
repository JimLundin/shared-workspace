/// <reference lib="webworker" />
import { object, string, type JoinStage } from '../../../native/ui/src/protocol.ts';
import { requireThat, safeError, type Command, type HostOptions } from './types.ts';
import type { HostOwner } from './owner.ts';

declare const self: DedicatedWorkerGlobalScope;
let owner: HostOwner | undefined;
let opening = false;
let releaseLock: (() => void) | undefined;
let lockTask: Promise<void> | undefined;

async function open(options: HostOptions): Promise<void> {
  requireThat(!opening && !owner, 'Browser host is already open.');
  requireThat(self.crossOriginIsolated && self.isSecureContext && self.navigator.locks && self.navigator.storage?.getDirectory, 'This browser needs a secure isolated page with Web Locks and OPFS.');
  const database = options.databaseName ?? 'shared-workspaces.db';
  requireThat(/^[a-zA-Z0-9_-]{1,80}\.db$/.test(database), 'Invalid browser database name.');
  opening = true;
  await new Promise<void>((resolve, reject) => {
    lockTask = navigator.locks.request(`workspace-host:${database}`, { mode: 'exclusive', ifAvailable: true }, async lock => {
      if (!lock) throw new Error('This workspace is already open in another tab.');
      const held = new Promise<void>(unlock => { releaseLock = unlock; });
      const { HostOwner } = await import('./owner.ts');
      owner = await HostOwner.open(options);
      resolve();
      await held;
    });
    void lockTask.catch(reject);
  });
}

async function dispatch(method: string, value: unknown, progress: (stage: JoinStage) => void): Promise<unknown> {
  if (method === 'open') {
    const options = object(value);
    await open({
      ...(options.deviceName === undefined ? {} : { deviceName: string(options.deviceName) }),
      ...(options.databaseName === undefined ? {} : { databaseName: string(options.databaseName) }),
      ...(options.baseUrl === undefined ? {} : { baseUrl: string(options.baseUrl) }),
      startPaused: options.startPaused === true,
    });
    return;
  }
  requireThat(owner, 'Browser host is not open.');
  switch (method) {
    case 'snapshot': return owner.snapshot();
    case 'command': return owner.command(value as Command); // Rust validates the command at the typed boundary.
    case 'invite':
      requireThat(Array.isArray(value), 'Invalid workspace selection.');
      return owner.invite(value.map(string));
    case 'join': return owner.join(string(value), progress);
    case 'sync': return owner.sync();
    case 'foreground':
      requireThat(typeof value === 'boolean', 'Invalid foreground state.');
      return owner.setForeground(value);
    case 'close':
      // On cleanup failure keep the lock until the UI proxy terminates this
      // worker. Another tab must not enter while database closure is uncertain.
      await owner.close();
      releaseLock?.();
      await lockTask;
      owner = undefined;
      return;
    default: throw new Error('Unknown browser host command.');
  }
}

self.addEventListener('message', (event: MessageEvent<unknown>) => {
  const message = object(event.data), id = string(message.id), method = string(message.method);
  void dispatch(method, message.value, stage => self.postMessage({ id, type: 'join_progress', stage })).then(
    value => self.postMessage({ id, ok: true, value }),
    error => self.postMessage({ id, ok: false, error: safeError(error) }),
  );
});
