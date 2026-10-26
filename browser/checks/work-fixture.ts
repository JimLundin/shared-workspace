import { BrowserHost } from '../host/src/host.ts';
import type { Host } from '../../native/ui/src/host.ts';
import type { Command, Receipt } from '../../native/ui/src/protocol.ts';
import { WorkspaceSession } from '../../native/ui/src/session.ts';
import { RetainedInputJournal } from '../../native/ui/src/retained-input.ts';
import { WorkController } from '../../native/ui/src/work-controller.ts';

type CommandObservation = Readonly<{ command: Command; receipt: Receipt; reply: 'delivered' | 'withheld' }>;
let host: BrowserHost | undefined;
let port: Host | undefined;
let session: WorkspaceSession | undefined;
let journal: RetainedInputJournal | undefined;
let controller: WorkController | undefined;
let preparation: Promise<void> | undefined;
let preparationFailed = false;
let withholdPublication = false;
const commands: CommandObservation[] = [];
const activity: string[] = [];

function current() {
  if (!host || !port || !session || !journal || !controller) throw new Error('Open the work fixture first.');
  return { host, port, session, journal, controller };
}
const methods = {
  async open(options: Parameters<typeof BrowserHost.open>[0]) {
    if (host) throw new Error('Close the work fixture before reopening.');
    const opened = await BrowserHost.open(options);
    try {
      const forwarding: Host = {
        available: opened.available,
        snapshot: () => opened.snapshot(),
        async command(command) {
          const receipt = await opened.command(command);
          const withheld = withholdPublication && command.type === 'publish_meeting_materials' && receipt.type === 'applied';
          commands.push(Object.freeze({ command: structuredClone(command), receipt, reply: withheld ? 'withheld' : 'delivered' }));
          if (commands.length > 128) commands.shift();
          if (withheld) {
            withholdPublication = false;
            // Only the receipt between the real facade and controller is lost.
            // The worker has already committed and returned its genuine receipt.
            throw new Error('Synthetic loss of the publication reply.');
          }
          return receipt;
        },
        invite: ids => opened.invite(ids),
        join: (ticket, progress) => opened.join(ticket, progress),
        sync: () => opened.sync(),
      };
      const retained = await RetainedInputJournal.open(opened);
      const workspace = new WorkspaceSession(forwarding);
      await workspace.refresh();
      const work = new WorkController(workspace, { provider: opened, journal: retained });
      work.subscribe(state => { activity.push(state.type); if (activity.length > 128) activity.shift(); });
      host = opened; port = forwarding; session = workspace; journal = retained; controller = work;
      await work.restore();
      return { snapshot: await opened.snapshot(), activity: work.state };
    } catch (error) { await opened.close(); throw error; }
  },
  snapshot() { return current().host.snapshot(); },
  async refresh() { const value = current(); await value.session.refresh(); return value.session.snapshot(); },
  async command(command: Command) {
    const value = current();
    const receipt = await value.port.command(command);
    await value.session.refresh();
    return receipt;
  },
  connection(workspaceId: string) { return current().host.connection(workspaceId); },
  setConnection(input: Parameters<BrowserHost['setConnection']>[0]) { return current().host.setConnection(input); },
  prepareMeeting(input: Parameters<BrowserHost['prepareMeeting']>[0]) { return current().host.prepareMeeting(input); },
  invite(workspaceIds: readonly string[]) { return current().host.invite(workspaceIds); },
  async join(ticket: string) {
    const value = current(), snapshot = await value.host.join(ticket);
    await value.session.refresh();
    return snapshot;
  },
  async sync() { const value = current(), report = await value.host.sync(); await value.session.refresh(); return report; },
  setForeground(foreground: boolean) { return current().host.setForeground(foreground); },
  async createWork(input: Parameters<WorkController['create']>[0]) {
    const value = current(); await value.session.refresh();
    return { resourceId: await value.controller.create(input), activity: value.controller.state };
  },
  async correctActions(input: Parameters<WorkController['correctActions']>[0]) {
    const value = current(); await value.session.refresh();
    return { applied: await value.controller.correctActions(input), activity: value.controller.state };
  },
  async startPreparation(resourceId: string, resume = false) {
    if (preparation) throw new Error('The fixture already has pending preparation.');
    const value = current(); await value.session.refresh(); preparationFailed = false;
    preparation = (resume ? value.controller.resume(resourceId) : value.controller.prepare(resourceId)).catch(() => { preparationFailed = true; });
  },
  async finishPreparation() {
    await preparation; preparation = undefined;
    if (preparationFailed) throw new Error('The production controller threw during preparation.');
    return current().controller.state;
  },
  async resumeWork(resourceId: string) {
    const value = current(); await value.session.refresh(); await value.controller.resume(resourceId); return value.controller.state;
  },
  async retryWork() { const value = current(); await value.controller.retry(); return value.controller.state; },
  async discardInterrupted() { const value = current(); await value.controller.discardInterrupted(); return value.controller.state; },
  activity() { return current().controller.state; },
  activityHistory() { return [...activity]; },
  commandObservations() { return [...commands]; },
  loseNextPublicationReply() { withholdPublication = true; },
  async putDraft(id: string, kind: Parameters<RetainedInputJournal['put']>[1], value: unknown) {
    const retained = current().journal; await retained.put(id, kind, value); await retained.flush(); return retained.entries();
  },
  drafts() { return current().host.listLocalDrafts(); },
  async editNote(resourceId: string, body: string) {
    const workspace = current().session;
    await workspace.refresh(); workspace.edit(resourceId); workspace.updateEditor('body', body); await workspace.saveEditor();
    return workspace.state.mutation;
  },
  async close() {
    const closing = host;
    controller?.dispose(); session?.dispose();
    host = undefined; port = undefined; session = undefined; controller = undefined; journal = undefined;
    await closing?.close();
    await preparation; preparation = undefined;
  },
};

declare global { interface Window { workspaceWorkCheck: typeof methods; } }
// Only this dedicated acceptance entry installs a test global.
window.workspaceWorkCheck = methods;
