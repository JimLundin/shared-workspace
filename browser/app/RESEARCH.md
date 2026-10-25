# Browser flow constraints

Primary sources checked 2026-10-25. These findings guide the browser application;
they do not establish compatibility on a physical iPhone.

## Storage and installation

WebKit describes website storage as best-effort by default. Origins can lose
data through storage pressure, quota pressure or inactivity. `persisted()`
reports the mode; `persist()` requests persistent storage. WebKit decides using
heuristics, including whether the website runs as a Home Screen app. Standalone
apps receive browser-class quotas, but installation is not a promise that every
persistence request succeeds. [WebKit storage policy][storage]

**Flow:** opening the website must work before installation. Offer installation
and “Keep work on this device” after the first successful opening; display the
actual persistence result. Distinguish “saved on this device” from “received by
another device.” A durable local receipt does not establish a second retained
copy. Never describe browser storage as a backup.

WebKit documents the manifest's `standalone`/`fullscreen` display modes and the
Share → Add to Home Screen route. An installed web app has its own App Switcher
entry. Its supported push events do not imply an always-running page or peer
connection. [WebKit Home Screen apps][homescreen]

## Foreground continuity

The service-worker specification ties worker lifetime to event execution; a
browser may terminate a worker when it has no event to handle. Chrome's
lifecycle documentation also describes frozen/discarded pages and warns that
unload events are unreliable, particularly on mobile. These sources support a
resume-and-reconcile design, not a continuous-background execution promise.
[Service worker lifetime][workers] · [Chrome lifecycle][lifecycle]

**Flow:** reopening shows retained work immediately, reconnects peers, and then
refreshes observed state. Keep request/resource addresses stable while navigating.
Show outstanding work as last observed until a fresh host receipt arrives. Desktop
can expose several related resources on a canvas; phone presents the same work
as a list with one focused resource and its next action. Layout is local;
identity, content and operation history belong to the host.

## Speech

The Web Speech draft requires explicit, informed consent before capture and a
browser-controlled recording indication. Its `processLocally` default is `false`,
which permits either local or remote processing. The presence of the API alone
does not establish on-device recognition or language availability. [Web Speech
specification][speech]

**Flow:** explicit Speak → visible capture → editable transcript → explicit Run.
Disclose that the browser's provider may process audio online. Keep typing/paste
available. Cancelled or superseded sessions must not alter another workspace's
input. Our adapter does not request or claim on-device recognition.

## Offline assets, isolation and updates

Cross-origin isolation is determined using document response policies, including
COOP and COEP. Check `crossOriginIsolated` before starting dependent WASM;
manifest metadata is insufficient. [HTML standard][isolation]

Service workers can supply responses through `respondWith()`. Cache entries are
not automatically updated when the worker changes; authors manage versions and
lifetimes. Activation normally waits for existing clients, unless the waiting
worker skips that wait. [Service workers][workers]

**Flow:** one worker handles isolation headers and versioned application assets.
A first controlled reload may be necessary. Retain the invitation fragment until
that reload finishes, then clear it and ask the user to Join. URI fragments are
separated before dereferencing, but remain readable by page scripts; this is not
protection from a compromised page. [URI fragment standard][fragment]

Cache only shipped assets. Preserve previous chunks for older tabs. Offer an
update action that waits for the user to finish edits; do not silently reload an
editor. Host records and invitations do not enter the app cache.

## Project evidence

The [storage probe](../storage-probe/evidence/chromium-static.json) established
headers-free service-worker isolation and OPFS survival across Chromium process
restart. The [production UI check](evidence/production-ui.json) separately records
real-host flow assertions; consult its overall `passed` field and failure stage,
not just individual successes. Neither establishes Safari touch/keyboard behavior,
Home Screen identity continuity, background reconnection, or microphone results.

[storage]: https://webkit.org/blog/14403/updates-to-storage-policy/
[homescreen]: https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
[workers]: https://www.w3.org/TR/service-workers/
[lifecycle]: https://developer.chrome.com/docs/web-platform/page-lifecycle-api
[speech]: https://webaudio.github.io/web-speech-api/
[isolation]: https://html.spec.whatwg.org/multipage/browsers.html#cross-origin-isolation
[fragment]: https://www.rfc-editor.org/rfc/rfc3986#section-3.5
