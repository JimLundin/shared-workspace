import '../../../native/ui/src/style.css';
import './style.css';
import { observeAppearance } from '../../../native/ui/src/theme.ts';
import { mountApp } from '../../../native/ui/src/app.ts';
import { ProviderVoice } from './provider-voice.ts';
import { BrowserVoice } from './voice.ts';
import { consumeInvitation, invitationLink } from './invitation.ts';
import { prepareBrowser } from './pwa.ts';
import type { UpdateDiscovery } from './update-discovery.ts';

const root = document.getElementById('app')!;
const controls = document.getElementById('browser-controls')!;
const base = new URL(import.meta.env.BASE_URL, location.origin).href;
const startupAppearance = observeAppearance(() => {});
let app: ReturnType<typeof mountApp> | undefined;
let closeHost: (() => Promise<void>) | undefined;
let ready = false;
let browserPreparation: UpdateDiscovery | undefined;

function action(label: string, work: () => void | Promise<void>): HTMLButtonElement {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
  button.addEventListener('click', () => { void Promise.resolve(work()).catch(() => { button.textContent = 'Could not complete. Try again.'; }); });
  return button;
}
// Install/storage are occasional actions in a stable top-layer panel.
const optionsDialog = document.createElement('dialog'); optionsDialog.className = 'workspace-dialog browser-options';
optionsDialog.setAttribute('aria-label', 'Install and storage');
const optionsHeader = document.createElement('header'); optionsHeader.className = 'dialog-heading';
const optionsTitle = document.createElement('h2'); optionsTitle.textContent = 'Install and storage';
optionsHeader.append(optionsTitle, action('Close install and storage', () => optionsDialog.close()));
const optionsBody = document.createElement('div'); optionsBody.className = 'dialog-body';
const installStatus = document.createElement('p'); installStatus.id = 'installation-state'; installStatus.setAttribute('role', 'status');
const installBenefits = document.createElement('p'); installBenefits.textContent = 'Install for an app icon and a separate window. Saved workspace notes also work offline in a browser tab.';
const installInstructions = document.createElement('p'); installInstructions.textContent = 'On iPhone or iPad: in Safari, choose Share → Add to Home Screen, then open the new icon.';
const installStorage = document.createElement('p'); installStorage.textContent = 'On iPhone and iPad, the Home Screen app may have separate storage and need pairing again. Keep your Safari work; if the app opens empty, pair it from another connected device. Installation does not transfer Safari’s saved work.';
const installPermissions = document.createElement('p'); installPermissions.textContent = 'Browser permissions still apply. Installation does not add desktop control or continuous background operation.';
optionsBody.append(installStatus, installBenefits, installInstructions, controls, installStorage, installPermissions); optionsDialog.append(optionsHeader, optionsBody); document.body.append(optionsDialog);
let modalHistory = false, updateClosing = false;
function openBrowserDialog(panel: HTMLDialogElement): void {
  if (!modalHistory) { history.pushState({ ...history.state, browserDialog: true }, ''); modalHistory = true; }
  panel.showModal();
}
function closeBrowserDialog(): void { if (modalHistory) { modalHistory = false; history.back(); } }
function focusAppControl(selectors: readonly string[]): void {
  for (const selector of selectors) {
    const control = root.querySelector<HTMLButtonElement>(selector);
    if (control && !control.disabled && control.getClientRects().length > 0) { control.focus(); return; }
  }
}
window.addEventListener('popstate', () => { if(updateClosing){history.pushState({...history.state,browserDialog:true},'');modalHistory=true;document.querySelector<HTMLDialogElement>('.browser-notice')?.querySelector('button')?.focus();return}if (!modalHistory) return; modalHistory = false; optionsDialog.close(); document.querySelector<HTMLDialogElement>('.browser-notice')?.close(); });
optionsDialog.addEventListener('close', () => {
  if (document.querySelector('.browser-notice[open]')) return;
  closeBrowserDialog();
  focusAppControl(['#workspace-settings', '#navigation-toggle', '#workspace-picker']);
});
let waitingRegistration: ServiceWorkerRegistration | null = null;
function offerUpdate(registration: ServiceWorkerRegistration | null): void {
  waitingRegistration = registration;
  app?.setUpdateAvailable(registration !== null);
}
function focusUpdateAction(): void {
  focusAppControl(['[data-action="update"]', '#navigation-toggle', '#workspace-picker']);
}
function openUpdate(): void {
  if (updateClosing) return;
  if (waitingRegistration?.waiting?.state !== 'installed') { offerUpdate(null); return; }
  const existing = document.querySelector<HTMLDialogElement>('.browser-notice');
  if (existing) { if (!existing.open) openBrowserDialog(existing); return; }
  const panel = document.createElement('dialog'); panel.className = 'browser-notice workspace-dialog'; panel.setAttribute('aria-label', 'Update application');
  const heading = document.createElement('h2'); heading.textContent = 'Ready when you are.';
  const message = document.createElement('p'); message.textContent = 'Save your work before reloading to the new version.';
  let closing = false;
  panel.addEventListener('cancel', event => { if (closing) event.preventDefault(); });
  panel.addEventListener('close', () => { panel.remove(); closeBrowserDialog(); focusUpdateAction(); });
  const recover = (detail: string) => {
    message.textContent = detail;
    panel.replaceChildren(message, action('Reload and recover', () => location.reload()));
    panel.querySelector('button')?.focus();
  };
  panel.append(heading, message, action('Reload with update', async () => {
    if (app?.session.state.mutation.type === 'sending' || app?.session.state.mutation.type === 'uncertain') { message.textContent = 'Finish or recover the pending save before updating.'; return; }
    if (app?.hasUnsubmittedInput()) { message.textContent = 'You have an unfinished edit or captured input. Choose Later to save or discard it first.'; return; }
    closing = true; updateClosing = true;
    for (const control of panel.querySelectorAll('button')) control.disabled = true;
    root.inert = true;
    browserPreparation?.dispose(); disposeInstallGuidance();
    app?.dispose();
    try { await closeHost?.(); }
    catch { recover('Closing this workspace was not confirmed. Reload to recover saved work.'); return; }
    const waiting = waitingRegistration?.waiting;
    // A different tab may already have activated the update. This reload still
    // follows this tab's explicit consent and completed local close.
    if (!waiting || waiting.state !== 'installed') { location.reload(); return; }
    let timer: ReturnType<typeof setTimeout>;
    const activated = () => { clearTimeout(timer); location.reload(); };
    navigator.serviceWorker.addEventListener('controllerchange', activated, { once: true });
    timer = setTimeout(() => {
      navigator.serviceWorker.removeEventListener('controllerchange', activated);
      recover('The update could not be confirmed. Reload to recover saved work.');
    }, 15_000);
    try { waiting.postMessage({ type: 'ACTIVATE_UPDATE' }); }
    catch {
      clearTimeout(timer); navigator.serviceWorker.removeEventListener('controllerchange', activated);
      recover('The update could not be started. Reload to recover saved work.');
    }
  }), action('Later', () => panel.close()));
  document.body.append(panel); openBrowserDialog(panel); if (optionsDialog.open) optionsDialog.close();
}
type InstallEvent = Event & { prompt(): Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> };
const installedDisplay = window.matchMedia('(display-mode: standalone)');
let pendingInstall: InstallEvent | undefined, installButton: HTMLButtonElement | undefined;
let installPromptOpen = false, installGuidanceDisposed = false;
let installNotice: 'none' | 'failed' = 'none';
const standalone = () => installedDisplay.matches || ('standalone' in navigator && navigator.standalone === true);
function renderInstallGuidance(): void {
  if (installGuidanceDisposed) return;
  const installed = standalone();
  installStatus.textContent = installed ? 'Running in an app window. Saved workspace notes are available offline.' : installNotice === 'failed' ? 'The browser did not complete installation. Try its install menu.' : 'Running in a browser tab.';
  installBenefits.hidden = installed;
  installInstructions.hidden = installed;
  if (installed) { pendingInstall = undefined; installButton?.remove(); installButton = undefined; }
  if (!installed && pendingInstall && !installButton && !installPromptOpen) {
    const button = action('Install on this device', async () => {
      const prompt = pendingInstall;
      if (!prompt || installPromptOpen || standalone()) return;
      pendingInstall = undefined; installPromptOpen = true; button.disabled = true;
      try { await prompt.prompt(); await prompt.userChoice; }
      catch { installNotice = 'failed'; }
      finally {
        installPromptOpen = false; button.remove();
        if (installButton === button) installButton = undefined;
        renderInstallGuidance();
      }
    });
    installButton = button; controls.prepend(button);
  }
}
function onInstallPrompt(event: Event): void {
  if (!('prompt' in event) || typeof event.prompt !== 'function' || !('userChoice' in event)) return;
  event.preventDefault();
  if (standalone()) return;
  pendingInstall = event as InstallEvent; installNotice = 'none';
  renderInstallGuidance();
}
function onInstalled(): void {
  pendingInstall = undefined; installButton?.remove(); installButton = undefined;
  renderInstallGuidance();
}
function disposeInstallGuidance(): void {
  installGuidanceDisposed = true; pendingInstall = undefined; installButton?.remove(); installButton = undefined;
  installedDisplay.removeEventListener('change', renderInstallGuidance);
  window.removeEventListener('beforeinstallprompt', onInstallPrompt);
  window.removeEventListener('appinstalled', onInstalled);
  window.removeEventListener('pageshow', renderInstallGuidance);
}
installedDisplay.addEventListener('change', renderInstallGuidance);
window.addEventListener('beforeinstallprompt', onInstallPrompt);
window.addEventListener('appinstalled', onInstalled);
window.addEventListener('pageshow', renderInstallGuidance);
renderInstallGuidance();

async function start(): Promise<void> {
  browserPreparation = await prepareBrowser(base, offerUpdate);
  const initialInvite = consumeInvitation(location, history);
  const { BrowserHost } = await import('@shared-workspace/browser-host');
  const host = await BrowserHost.open({ deviceName: 'Web browser', baseUrl: base, startPaused: document.hidden });
  closeHost = () => host.close();
  app = mountApp(root, {
    host, connections:host, meetings:host, drafts:host, voice: new ProviderVoice({locale:'',captureContext:()=>{if(!app)throw new Error('Workspace is opening');return app.voiceContext()},transcribe:(audio,{signal,locale,context})=>host.transcribe({workspace_id:context.workspaceId,connection_version:context.connectionVersion,audio,locale},signal)}),fallbackVoice:new BrowserVoice(), openAppOptions: () => openBrowserDialog(optionsDialog), openUpdate, invitationUrl: ticket => invitationLink(base, ticket),
    ...(typeof navigator.share === 'function' ? { shareInvitation: async (url: string): Promise<'shared' | 'cancelled'> => {
      try { await navigator.share({ title: 'Shared workspaces', url }); return 'shared'; }
      catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return 'cancelled'; throw error; }
    } } : {}),
    ...(initialInvite ? { initialInvite } : {}),
    scanner: { async available() { return false; }, async scan() { throw new Error('Open the invitation with your camera app, or paste a ticket.'); }, async cancel() {} },
  });
  startupAppearance.dispose();
  ready = true; if (waitingRegistration) app.setUpdateAvailable();
  const foreground = () => { if (updateClosing) return; void host.setForeground(!document.hidden).then(() => { if (!document.hidden) return app?.session.refresh(); }).catch(() => app?.session.refresh()); };
  document.addEventListener('visibilitychange', foreground);
  window.addEventListener('online', foreground);
  window.addEventListener('pagehide', event => {
    if (event.persisted) { void host.setForeground(false); return; }
    document.removeEventListener('visibilitychange', foreground); window.removeEventListener('online', foreground);
    browserPreparation?.dispose(); disposeInstallGuidance(); app?.dispose(); void host.close();
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
  browserPreparation?.dispose(); disposeInstallGuidance(); void closeHost?.().catch(() => undefined);
  const panel = document.createElement('main'); panel.className = 'boot';
  const heading = document.createElement('h1'); heading.textContent = 'Your workspace could not open.';
  const message = document.createElement('p'); message.setAttribute('role', 'alert');
  message.textContent = error instanceof Error ? error.message.slice(0, 320) : 'Storage or connection setup failed.';
  panel.append(heading, message, action('Retry opening', () => location.reload())); root.replaceChildren(panel);
});
