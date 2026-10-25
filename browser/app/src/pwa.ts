import { discoverUpdates, type UpdateDiscovery } from './update-discovery.ts';
type Update = (registration: ServiceWorkerRegistration | null) => void;
const RELOAD_MARK = 'workspace-isolation-reload';
export async function prepareBrowser(base: string, update: Update): Promise<UpdateDiscovery> {
  if (!isSecureContext || !('serviceWorker' in navigator)) throw new Error('Open this website over HTTPS in a browser with service worker support.');
  if (import.meta.env.DEV) {
    if (!crossOriginIsolated) throw new Error('Development server isolation headers are missing.');
    return { dispose() {} };
  }
  const registration = await navigator.serviceWorker.register(new URL('sw.js', base), { scope: base, updateViaCache: 'none' });
  let announced: ServiceWorker | null = null, disposed = false;
  const installing = new Set<ServiceWorker>();
  const checkUpdate = () => {
    if (disposed) return;
    const waiting = navigator.serviceWorker.controller && registration.waiting?.state === 'installed' ? registration.waiting : null;
    if (waiting === announced) return;
    announced = waiting; update(waiting ? registration : null);
  };
  const watchInstalling = () => {
    const worker = registration.installing;
    if (worker && !installing.has(worker)) { installing.add(worker); worker.addEventListener('statechange', stateChanged); }
    checkUpdate();
  };
  const stateChanged = () => {
    checkUpdate();
    for (const worker of installing) {
      if (worker.state === 'installed' || worker.state === 'redundant' || worker.state === 'activated') {
        worker.removeEventListener('statechange', stateChanged); installing.delete(worker);
      }
    }
  };
  registration.addEventListener('updatefound', watchInstalling);
  navigator.serviceWorker.addEventListener('controllerchange', checkUpdate);
  window.addEventListener('pageshow', checkUpdate);
  watchInstalling();
  const discovery = discoverUpdates(() => registration.update(), checkUpdate);
  const dispose = () => {
    if (disposed) return;
    disposed = true; discovery.dispose();
    registration.removeEventListener('updatefound', watchInstalling);
    navigator.serviceWorker.removeEventListener('controllerchange', checkUpdate);
    window.removeEventListener('pageshow', checkUpdate);
    window.removeEventListener('pagehide', onHide);
    for (const worker of installing) worker.removeEventListener('statechange', stateChanged);
    installing.clear();
  };
  const onHide = (event: PageTransitionEvent) => { if (!event.persisted) dispose(); };
  window.addEventListener('pagehide', onHide);
  try {
    await deadline(navigator.serviceWorker.ready, 45000, 'The offline application could not be prepared. Check your connection and retry.');
    if (!navigator.serviceWorker.controller) {
      let controlled: (() => void) | undefined;
      try {
        await deadline(new Promise<void>(resolve => {
          controlled = resolve; navigator.serviceWorker.addEventListener('controllerchange', controlled, { once: true });
        }), 10000, 'The application worker could not take control. Reload this page to retry.');
      } finally { if (controlled) navigator.serviceWorker.removeEventListener('controllerchange', controlled); }
    }
    if (!crossOriginIsolated) {
      if (sessionStorage.getItem(RELOAD_MARK)) {
        sessionStorage.removeItem(RELOAD_MARK);
        throw new Error('This browser could not enable isolated storage. Try a current browser or reopen this page.');
      }
      sessionStorage.setItem(RELOAD_MARK, '1');
      // Keep the invitation fragment until this necessary startup reload is over.
      dispose(); location.reload();
      await new Promise<void>(() => {});
    }
    sessionStorage.removeItem(RELOAD_MARK);
    checkUpdate();
    return { dispose };
  } catch (error) { dispose(); throw error; }
}
async function deadline<T>(task: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([task, new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); })]); }
  finally { clearTimeout(timer); }
}
