type Update = (registration: ServiceWorkerRegistration) => void;
const RELOAD_MARK = 'workspace-isolation-reload';
export async function prepareBrowser(base: string, update: Update): Promise<void> {
  if (!isSecureContext || !('serviceWorker' in navigator)) throw new Error('Open this website over HTTPS in a browser with service worker support.');
  if (import.meta.env.DEV) {
    if (!crossOriginIsolated) throw new Error('Development server isolation headers are missing.');
    return;
  }
  const registration = await navigator.serviceWorker.register(new URL('sw.js', base), { scope: base, updateViaCache: 'none' });
  const checkUpdate = () => { if (registration.waiting && navigator.serviceWorker.controller) update(registration); };
  checkUpdate();
  registration.addEventListener('updatefound', () => registration.installing?.addEventListener('statechange', checkUpdate));
  await deadline(navigator.serviceWorker.ready, 45000, 'The offline application could not be prepared. Check your connection and retry.');
  if (!navigator.serviceWorker.controller) await deadline(new Promise<void>(resolve => {
    navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true });
  }), 10000, 'The application worker could not take control. Reload this page to retry.');
  if (!crossOriginIsolated) {
    if (sessionStorage.getItem(RELOAD_MARK)) {
      sessionStorage.removeItem(RELOAD_MARK);
      throw new Error('This browser could not enable isolated storage. Try a current browser or reopen this page.');
    }
    sessionStorage.setItem(RELOAD_MARK, '1');
    // Keep the invitation fragment until this necessary startup reload is over.
    location.reload();
    await new Promise<void>(() => {});
  }
  sessionStorage.removeItem(RELOAD_MARK);
}
async function deadline<T>(task: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([task, new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); })]); }
  finally { clearTimeout(timer); }
}
