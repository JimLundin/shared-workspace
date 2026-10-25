import type { JoinStage } from './protocol.ts';

export const joinStages: readonly JoinStage[] = ['checking_invitation', 'connecting_relay', 'contacting_device', 'saving_workspace'];
export function advanceJoinStage(current: JoinStage | null, next: JoinStage): JoinStage {
  return current === null || joinStages.indexOf(next) >= joinStages.indexOf(current) ? next : current;
}
const stageCopy: Readonly<Record<JoinStage, Readonly<{ title: string; detail: string }>>> = {
  checking_invitation: { title: 'Checking invitation', detail: 'Checking the invitation before starting a connection.' },
  connecting_relay: { title: 'Connecting this device', detail: 'Preparing this device’s secure relay connection.' },
  contacting_device: { title: 'Contacting the inviting device', detail: 'Waiting for its confirmation and selected workspaces. Keep the inviting page visible.' },
  saving_workspace: { title: 'Saving work on this device', detail: 'The invitation was accepted. Saving its workspaces here before completing the connection.' },
};
export function joinStageCopy(stage: JoinStage | null): Readonly<{ title: string; detail: string }> {
  return stage === null ? { title: 'Waiting for a confirmed exchange', detail: 'Keep both pages visible and both screens awake. Work appears here after it is saved on this device.' } : stageCopy[stage];
}
export function joinFailure(stage: JoinStage | null): string {
  switch (stage) {
    case 'checking_invitation': return 'The invitation could not be checked. Paste it again, or ask the inviting device for a fresh invitation.';
    case 'connecting_relay': return 'This device did not finish preparing its relay connection. Keep this page open, check this device’s connection, then try again.';
    case 'contacting_device': return 'The inviting device did not confirm the exchange. Keep both pages open and screens awake, then retry this invitation.';
    case 'saving_workspace': return 'The shared work was not confirmed as saved here. Retry this invitation to recover its confirmation. Your existing work remains available.';
    case null: return 'Joining was not confirmed. Keep both pages open and screens awake, then retry this invitation.';
  }
}
