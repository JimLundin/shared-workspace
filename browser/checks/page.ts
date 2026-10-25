import { BrowserHost } from '../host/src/host.ts';
import type { JoinStage } from '../../native/ui/src/protocol.ts';

let host: BrowserHost | undefined;
const joinEvents: Array<Readonly<{ stage: JoinStage; at_ms: number }>> = [];
let joinReceiptEvents: number | null = null;
function current(): BrowserHost {
  if (!host) throw new Error('The test page has no open host.');
  return host;
}

const methods = {
  async open(options: Parameters<typeof BrowserHost.open>[0]) {
    if (host) throw new Error('Close the test page host before reopening it.');
    const opened = await BrowserHost.open(options);
    try {
      const snapshot = await opened.snapshot();
      host = opened;
      return snapshot;
    } catch (error) {
      await opened.close();
      throw error;
    }
  },
  snapshot() { return current().snapshot(); },
  command(command: Parameters<BrowserHost['command']>[0]) { return current().command(command); },
  invite(workspaceIds: readonly string[]) { return current().invite(workspaceIds); },
  async join(ticket: string) {
    joinEvents.length = 0;
    joinReceiptEvents = null;
    const snapshot = await current().join(ticket, stage => {
      if (joinEvents.length === 16) joinEvents.shift();
      joinEvents.push({ stage, at_ms: Date.now() });
    });
    joinReceiptEvents = joinEvents.length;
    return snapshot;
  },
  joinProgress() { return [...joinEvents]; },
  joinReceiptStageCount() { return joinReceiptEvents; },
  sync() { return current().sync(); },
  setForeground(foreground: boolean) { return current().setForeground(foreground); },
  async close() {
    const closing = host;
    host = undefined;
    await closing?.close();
  },
};

declare global { interface Window { workspaceCheck: typeof methods; } }
// This entry is built only for acceptance. The production UI has no test global.
window.workspaceCheck = methods;
