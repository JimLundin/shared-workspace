/* One worker owns app caching and isolation. Workspace data stays in OPFS. */
const VERSION = '__VERSION__';
const PREFIX = 'workspace-app-assets:' + self.registration.scope;
const CACHE = PREFIX + VERSION;
const ASSETS = __ASSETS__.map(path => new URL(path, self.registration.scope).href);
const allowed = new Set(ASSETS);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
  // A newer build waits for an explicit user request or for old tabs to close.
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const older = (await caches.keys()).filter(key => key.startsWith(PREFIX) && key !== CACHE);
    // Keep the immediately previous build for old tabs and lazy chunks.
    await Promise.all(older.slice(0, -1).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});
self.addEventListener('message', event => {
  if (event.data?.type === 'ACTIVATE_UPDATE') event.waitUntil(self.skipWaiting());
});

function isolated(response) {
  const headers = new Headers(response.headers);
  headers.set('Cross-Origin-Opener-Policy', 'same-origin');
  headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
  headers.set('Cross-Origin-Resource-Policy', 'same-origin');
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !url.href.startsWith(self.registration.scope)) return;
  // Ignore query-bearing data URLs and never cache invitations, RPC or records.
  const entry = request.mode === 'navigate' && (url.pathname === new URL(self.registration.scope).pathname || url.pathname === new URL('index.html', self.registration.scope).pathname);
  const asset = entry ? new URL('index.html', self.registration.scope).href : url.href;
  const builtAsset = url.pathname.startsWith(new URL('assets/', self.registration.scope).pathname);
  if ((!allowed.has(asset) && !builtAsset) || (!entry && url.search)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    let response = await cache.match(asset);
    // Other tabs may still refer to a previous build's asset filenames.
    if (!response) for (const key of await caches.keys()) {
      if (key.startsWith(PREFIX)) { response = await (await caches.open(key)).match(asset); if (response) break; }
    }
    if (!response) response = await fetch(asset);
    return isolated(response);
  })());
});
