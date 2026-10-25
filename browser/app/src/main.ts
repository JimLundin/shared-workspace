import '../../../native/ui/src/style.css';
import './style.css';
import { observeAppearance } from '../../../native/ui/src/theme.ts';
import { mountApp } from '../../../native/ui/src/app.ts';
import { ProviderVoice } from './provider-voice.ts';
import { BrowserVoice } from './voice.ts';
import { consumeInvitation, invitationLink } from './invitation.ts';
import { prepareBrowser } from './pwa.ts';

const root = document.getElementById('app')!;
const controls = document.getElementById('browser-controls')!;
const base = new URL(import.meta.env.BASE_URL, location.origin).href;
const startupAppearance = observeAppearance(() => {});
let app: ReturnType<typeof mountApp> | undefined;
let closeHost: (() => Promise<void>) | undefined;
let ready = false;

function action(label: string, work: () => void | Promise<void>): HTMLButtonElement {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
  button.addEventListener('click', () => { void Promise.resolve(work()).catch(() => { button.textContent = 'Could not complete. Try again.'; }); });
  return button;
}
// Install/storage are occasional actions in a stable top-layer panel.
const optionsDialog = document.createElement('dialog'); optionsDialog.className = 'workspace-dialog browser-options';
optionsDialog.setAttribute('aria-label', 'App options');
const optionsHeader = document.createElement('header'); optionsHeader.className = 'dialog-heading';
const optionsTitle = document.createElement('h2'); optionsTitle.textContent = 'App options';
optionsHeader.append(optionsTitle, action('Close app options', () => optionsDialog.close()));
const optionsBody = document.createElement('div'); optionsBody.className = 'dialog-body';
optionsBody.append(controls); optionsDialog.append(optionsHeader, optionsBody); document.body.append(optionsDialog);
let modalHistory = false, updateClosing = false;
function openBrowserDialog(panel: HTMLDialogElement): void {
  if (!modalHistory) { history.pushState({ ...history.state, browserDialog: true }, ''); modalHistory = true; }
  panel.showModal();
}
function closeBrowserDialog(): void { if (modalHistory) { modalHistory = false; history.back(); } }
window.addEventListener('popstate', () => { if(updateClosing){history.pushState({...history.state,browserDialog:true},'');modalHistory=true;document.querySelector<HTMLDialogElement>('.browser-notice')?.querySelector('button')?.focus();return}if (!modalHistory) return; modalHistory = false; optionsDialog.close(); document.querySelector<HTMLDialogElement>('.browser-notice')?.close(); });
optionsDialog.addEventListener('close', () => {
  if (document.querySelector('.browser-notice[open]')) return;
  closeBrowserDialog();
  root.querySelector<HTMLButtonElement>('#workspace-picker')?.focus();
});
let updateWaiting = false;
function offerUpdate(registration: ServiceWorkerRegistration): void {
  if (controls.querySelector('[data-update]')) return;
  updateWaiting = true; app?.setUpdateAvailable();
  const button = action('Update available', () => {
    const panel = document.createElement('dialog'); panel.className = 'browser-notice workspace-dialog'; panel.setAttribute('aria-label', 'Update application');
    const heading = document.createElement('h2'); heading.textContent = 'Ready when you are.';
    const message = document.createElement('p'); message.textContent = 'Save your work before reloading to the new version.';
    let closing = false;
    panel.addEventListener('cancel', event => { if (closing) event.preventDefault(); });
    panel.addEventListener('close', () => { panel.remove(); closeBrowserDialog(); root.querySelector<HTMLButtonElement>('#workspace-picker')?.focus(); });
    panel.append(heading, message, action('Reload with update', async () => {
      if (app?.session.state.mutation.type === 'sending' || app?.session.state.mutation.type === 'uncertain') { message.textContent = 'Finish or recover the pending save before updating.'; return; }
      if (app?.hasUnsubmittedInput()) { message.textContent = 'You have an unfinished edit or captured input. Choose Later to save or discard it first.'; return; }
      closing = true; updateClosing = true;
      for (const control of panel.querySelectorAll('button')) control.disabled = true;
      root.inert = true;
      app?.dispose();
      try { await closeHost?.(); }
      catch {
        message.textContent = 'Closing this workspace was not confirmed. Reload to recover saved work.';
        panel.replaceChildren(message, action('Reload and recover', () => location.reload()));
        panel.querySelector('button')?.focus();
        return;
      }
      if (!registration.waiting) { location.reload(); return; }
      navigator.serviceWorker.addEventListener('controllerchange', () => location.reload(), { once: true });
      registration.waiting.postMessage({ type: 'ACTIVATE_UPDATE' });
    }), action('Later', () => panel.close()));
    document.body.append(panel); panel.showModal(); optionsDialog.close();
  }); button.dataset.update = ''; controls.append(button);
}
type InstallEvent = Event & { prompt(): Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> };
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); const install = event as InstallEvent;
  const button = action('Install on this device', async () => { await install.prompt(); await install.userChoice; button.remove(); });
  controls.append(button);
});

async function start(): Promise<void> {
  await prepareBrowser(base, offerUpdate);
  const initialInvite = consumeInvitation(location, history);
  const { BrowserHost } = await import('@shared-workspace/browser-host');
  const host = await BrowserHost.open({ deviceName: 'Web browser', baseUrl: base, startPaused: document.hidden });
  closeHost = () => host.close();
  app = mountApp(root, {
    host, connections:host, voice: new ProviderVoice({locale:'',captureContext:()=>{if(!app)throw new Error('Workspace is opening');return app.voiceContext()},transcribe:(audio,{signal,locale,context})=>host.transcribe({workspace_id:context.workspaceId,connection_version:context.connectionVersion,audio,locale},signal)}),fallbackVoice:new BrowserVoice(), openAppOptions: () => openBrowserDialog(optionsDialog), invitationUrl: ticket => invitationLink(base, ticket),
    ...(typeof navigator.share === 'function' ? { shareInvitation: async (url: string): Promise<'shared' | 'cancelled'> => {
      try { await navigator.share({ title: 'Shared workspaces', url }); return 'shared'; }
      catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return 'cancelled'; throw error; }
    } } : {}),
    ...(initialInvite ? { initialInvite } : {}),
    scanner: { async available() { return false; }, async scan() { throw new Error('Open the invitation with your camera app, or paste a ticket.'); }, async cancel() {} },
  });
  startupAppearance.dispose();
  ready = true; if(updateWaiting) app.setUpdateAvailable();
  const foreground = () => { void host.setForeground(!document.hidden).then(() => { if (!document.hidden) return app?.session.refresh(); }).catch(() => app?.session.refresh()); };
  document.addEventListener('visibilitychange', foreground);
  window.addEventListener('online', foreground);
  window.addEventListener('pagehide', event => {
    if (event.persisted) { void host.setForeground(false); return; }
    document.removeEventListener('visibilitychange', foreground); window.removeEventListener('online', foreground);
    app?.dispose(); void host.close();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) foreground(); });
  if (navigator.storage.persist && !await navigator.storage.persisted()) {
    const button = action('Keep work on this device', async () => {
      const granted = await navigator.storage.persist();
      button.textContent = granted ? 'Persistent storage granted' : 'Saved locally · browser controls retention';
      button.disabled = true;
    }); controls.append(button);
  }
}
void start().catch(error => {
  if (ready) return;
  const panel = document.createElement('main'); panel.className = 'boot';
  const heading = document.createElement('h1'); heading.textContent = 'Your workspace could not open.';
  const message = document.createElement('p'); message.setAttribute('role', 'alert');
  message.textContent = error instanceof Error ? error.message.slice(0, 320) : 'Storage or connection setup failed.';
  panel.append(heading, message, action('Retry opening', () => location.reload())); root.replaceChildren(panel);
});
