import '../../../native/ui/src/style.css';
import './style.css';
import { mountApp } from '../../../native/ui/src/app.ts';
import { BrowserVoice } from './voice.ts';
import { consumeInvitation, invitationLink } from './invitation.ts';
import { prepareBrowser } from './pwa.ts';

const root = document.getElementById('app')!;
const controls = document.getElementById('browser-controls')!;
const base = new URL(import.meta.env.BASE_URL, location.origin).href;
let app: ReturnType<typeof mountApp> | undefined;
let closeHost: (() => Promise<void>) | undefined;
let ready = false;

function action(label: string, work: () => void | Promise<void>): HTMLButtonElement {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
  button.addEventListener('click', () => { void Promise.resolve(work()).catch(() => { button.textContent = 'Could not complete. Try again.'; }); });
  return button;
}
function offerUpdate(registration: ServiceWorkerRegistration): void {
  if (controls.querySelector('[data-update]')) return;
  const button = action('Update available', () => {
    const panel = document.createElement('div'); panel.className = 'browser-notice';
    const message = document.createElement('p'); message.textContent = 'Save or finish your edits before reloading. Unsubmitted input stays only in this tab.';
    panel.append(message, action('Reload with update', async () => {
      if (app?.session.state.mutation.type === 'sending' || app?.session.state.mutation.type === 'uncertain') { message.textContent = 'Finish or recover the pending save before updating.'; return; }
      for (const control of panel.querySelectorAll('button')) control.disabled = true;
      root.inert = true;
      app?.dispose();
      try { await closeHost?.(); }
      catch {
        message.textContent = 'Closing this workspace was not confirmed. Reload to recover saved work. Unsubmitted input is not restored.';
        panel.replaceChildren(message, action('Reload and recover', () => location.reload()));
        panel.querySelector('button')?.focus();
        return;
      }
      if (!registration.waiting) { location.reload(); return; }
      navigator.serviceWorker.addEventListener('controllerchange', () => location.reload(), { once: true });
      registration.waiting.postMessage({ type: 'ACTIVATE_UPDATE' });
    }), action('Later', () => panel.remove()));
    controls.append(panel);
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
    host, voice: new BrowserVoice(), invitationUrl: ticket => invitationLink(base, ticket),
    ...(typeof navigator.share === 'function' ? { shareInvitation: async (url: string): Promise<'shared' | 'cancelled'> => {
      try { await navigator.share({ title: 'Shared workspaces', url }); return 'shared'; }
      catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return 'cancelled'; throw error; }
    } } : {}),
    ...(initialInvite ? { initialInvite } : {}),
    scanner: { async available() { return false; }, async scan() { throw new Error('Open the invitation with your camera app, or paste a ticket.'); }, async cancel() {} },
  });
  ready = true;
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
