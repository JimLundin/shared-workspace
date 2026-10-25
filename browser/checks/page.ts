import { BrowserHost } from '../host/src/host.ts';

let host: BrowserHost | undefined;
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
  join(ticket: string) { return current().join(ticket); },
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
