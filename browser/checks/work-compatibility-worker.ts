/// <reference lib="webworker" />
import { BrowserEndpoint } from '../host-wasm/pkg/browser_workspace_host.js';
import type { HostOwner } from '../host/src/owner.ts';
import type { Command, HostOptions } from '../host/src/types.ts';

// Transparent transport observers: forward exactly the original bytes and result.
// Never retain seeds, invitations, contacts, API keys, or transcript text.
type Observation = Readonly<{ direction: string; type: string; fields: readonly string[]; work_declared: boolean; operations: number; work_operations: number }>;
const observations: Observation[] = [];
function observe(direction: string, payload: string): void {
  try {
    const body = JSON.parse(payload).body;
    const operations = Array.isArray(body.operations) ? body.operations : [];
    observations.push(Object.freeze({ direction, type: typeof body.type === 'string' ? body.type : 'invalid', fields: Object.keys(body).sort(),
      work_declared: body.work === 1, operations: operations.length,
      work_operations: operations.filter((item: { operation?: { action?: { type?: string } } }) => ['create_work', 'edit_work', 'publish_meeting_materials'].includes(item.operation?.action?.type ?? '')).length }));
    if (observations.length > 512) observations.shift();
  } catch { observations.push({ direction, type: 'unreadable', fields: [], work_declared: false, operations: 0, work_operations: 0 }); }
}
const start = BrowserEndpoint.start;
BrowserEndpoint.start = async (seed, handler) => {
  self.postMessage({ phase: 'starting-iroh-endpoint' });
  return start(seed, async (peer: string, payload: string) => {
  observe('incoming-request', payload);
  const result = await handler(peer, payload);
  observe('outgoing-reply', result);
  return result;
  });
};
const request = BrowserEndpoint.prototype.request;
BrowserEndpoint.prototype.request = async function (address, payload) {
  observe('outgoing-request', payload);
  const result = await request.call(this, address, payload);
  try { observe('incoming-reply', JSON.parse(result).payload); } catch { /* Production decoder still receives the unchanged result. */ }
  return result;
};
let owner: HostOwner | undefined;
async function dispatch(method: string, value: unknown): Promise<unknown> {
  if (method === 'open') {
    if (owner) throw new Error('Already open.');
    self.postMessage({ phase: 'importing-host-owner' });
    const { HostOwner } = await import('../host/src/owner.ts');
    self.postMessage({ phase: 'opening-host-owner' });
    owner = await HostOwner.open(value as HostOptions);
    self.postMessage({ phase: 'host-owner-opened' });
    return owner.snapshot();
  }
  if (method === 'observations') return [...observations];
  if (method === 'clear_observations') { observations.length = 0; return; }
  if (!owner) throw new Error('Open the fixture first.');
  switch (method) {
    case 'snapshot': return owner.snapshot();
    case 'command': return owner.command(value as Command);
    case 'invite': return owner.invite(value as readonly string[]);
    case 'join': return owner.join(String(value));
    case 'sync': return owner.sync();
    case 'foreground': return owner.setForeground(value === true);
    case 'close': await owner.close(); owner = undefined; return;
    default: throw new Error('Unknown fixture method.');
  }
}
self.addEventListener('message', event => {
  const { id, method, value } = event.data;
  void dispatch(method, value).then(result => self.postMessage({ id, ok: true, value: result }), error => self.postMessage({ id, ok: false, error: error instanceof Error ? error.name : 'UnknownError' }));
});
