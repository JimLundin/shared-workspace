# What installing the web app changes

Research date: 25 October 2026. Current implementation inspected at
`ac9e45c8c0906e8e49dc69dcdc47bb1fe2e9f2a2`. This describes the current shared
website; the [product split](project-boundaries.md) preserves its origin, manifest identity
and local records.

Installing is useful for a dedicated app window and convenient launching. It
also enables some additional integrations on supporting platforms. It does not
grant unrestricted operating-system access or create an always-running agent.
The website remains the application, and direct installation does not require
an App Store submission. [1][2][3]

## Current behavior and possible extensions

| Capability | Current browser tab | Current app when installed | Optional future work |
| --- | --- | --- | --- |
| Launching and presentation | Open the URL in a tab. | Manifest requests a standalone window; desktop launcher/taskbar integration or an iPhone/iPad Home Screen icon and separate App Switcher entry. [1][2][3] | Platform-specific launch shortcuts and navigation polish. |
| Offline workspace | Cached application assets and Turso/OPFS records already support reopening and local edits after a successful initial load. | Same implemented offline capability; installation is not what supplies it. | Explicit export/recovery and stronger installation onboarding. |
| Updates | Service worker discovers new builds; the UI offers an explicit update after unfinished work is handled. | Same website release and update mechanism, without an app-store review step. | Clearer per-device update status. |
| Voice, sharing and permissions | Provider dictation and invitation sharing use supported browser APIs and explicit user actions. | Same features; installation does not automatically grant microphone, file or other permissions. [4] | More browser-supported integrations with their own permission flows. |
| Local retention and identity | Origin/storage-context-specific identity and records; “Keep work on this device” requests persistence. | Persistence still depends on browser policy. On iPhone/iPad, the Home Screen app can have separate data from Safari. [5][6] | Verify identity continuity and guide pairing/recovery after installation. |
| Notifications and badges | Not implemented. | Not implemented. iOS/iPadOS 16.4+ supports Web Push for Home Screen web apps after a user-initiated permission request. [2] | Push subscription, notification handling, badges and a delivery design; installation alone does not supply them. |
| Opening local files and protocol links | No filesystem file-open/save integration or registered protocol handlers. Code documents currently live in the workspace database. | No handlers are declared or implemented. | Installed desktop Chrome/Edge can register supported file types and protocols, with explicit handling code and user consent. [7][8] |
| Background execution and device control | Peer networking pauses when the page is hidden and resumes when visible. | Same application behavior; installing does not change it. | Event-driven notifications or optional explicitly authorized native capabilities. A continuously running desktop/phone agent is a separate design. [9][10] |

Remote Office/application pages and OpenAI transcription are not made offline
by installing this app. The service worker caches this application's shipped
assets; it does not cache remote applications or turn provider requests into
local processing.

## Desktop versus iPhone/iPad

**Windows desktop:** Edge and Chrome support installing suitable web apps into
their own windows. Use the browser's install action, or this app's **Install on
this device** button when the browser exposes `beforeinstallprompt`. That button
is conditional; its absence is not evidence that the workspace cannot be used.
The current manifest supplies an app identity, icons, launch URL and
`display: standalone`. [1][3]

**iPhone/iPad:** use Safari's Share menu → **Add to Home Screen**; on versions
showing **Open as Web App**, enable it. Open the resulting icon to use the
standalone app. The current code does not provide a Safari-specific installation
prompt, and Safari should not be expected to expose the Chromium install-button
event. Apple's documented Home Screen flow needs no App Store packaging. [3]

For a new phone host, **install first, then pair from the installed app**.
WebKit's Safari 17.2 documentation says installation copies login cookies, but
“No other kind of local storage is copied over”; after installation, other
website data is not shared. This app keeps its identity and workspace records
in OPFS/IndexedDB, not login cookies. Therefore do not promise that an existing
Safari tab's identity, notes or keys automatically appear in the Home Screen
app. Keep existing data until the installed host has obtained and confirmed its
workspace copy, for example by pairing with the already connected desktop. [6]

Storage persistence is a separate decision. WebKit uses best-effort storage by
default and can evict website data. A Home Screen installation influences its
`navigator.storage.persist()` heuristics, but is not a guaranteed grant or a
backup. The application displays the actual result of **Keep work on this
device**. Changing browser/profile, origin or storage context can change which
local identity and records are available. [5]

## What additional access actually requires

Some integrations are available to ordinary browser pages already. For example,
supported File System Access pickers require HTTPS and a user gesture, and file
writes require the relevant permission. Installed file handlers are a different
feature: a supporting desktop browser can register an installed app for selected
file types; the app must declare `file_handlers` and process `launchQueue`
events. Neither grants automatic access to the whole disk. [4][7]

Similarly, supported desktop PWA protocol handling needs `protocol_handlers`
entries and launch handling. It registers the permitted schemes, rather than
granting control over arbitrary native applications. The current app has neither
file nor protocol handlers. [8]

iPhone/iPad Web Push is a meaningful installed-app capability. It requires the
Home Screen web app, a direct user action to request notification permission,
subscription/delivery code and service-worker event handling. Apple Developer
Program membership is not required for standards-based Web Push. A push event
does not keep the page, microphone, Iroh connection or agent running forever:
service workers have event-driven lifetimes and may be terminated when idle.
Browser pages can also be frozen or discarded. [2][9][10]

## Code and evidence inspected

- [Manifest](../browser/app/public/manifest.webmanifest): standalone display,
  stable relative `id`, `start_url` and `scope`, icons; no file/protocol/share-target
  declarations.
- [Browser entry point](../browser/app/src/main.ts): conditional install button,
  actual persistence request/result, provider voice and invitation sharing;
  visibility events pause/resume the host.
- [PWA startup](../browser/app/src/pwa.ts),
  [worker template](../browser/app/scripts/worker-template.js) and
  [update discovery](../browser/app/src/update-discovery.ts): asset caching,
  isolation headers and controlled updates; no push/notification handlers.
- [Host lifecycle](../browser/host/src/owner.ts): hidden state retires the Iroh
  endpoint; visible state restarts maintenance and synchronization.
- [Production UI evidence](../browser/app/evidence/production-ui.json) verifies
  offline browser-process restart, retained work and later synchronization.
  [Update evidence](../browser/app/evidence/update.json) verifies controlled
  service-worker updates. These are Chromium tests, not physical iPhone
  installation or Safari-to-Home-Screen identity-transfer tests.

## Primary sources

1. Microsoft Edge: [PWA overview](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/)
   and [installation experience](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/ux#installing-a-pwa).
2. WebKit: [Web Push for web apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).
3. Apple: [Turn a website into an iPhone web app](https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios)
   and [iPad Home Screen instructions](https://support.apple.com/guide/ipad/bookmark-a-website-ipadc602b75b/ipados).
4. [Permissions standard](https://www.w3.org/TR/permissions/)
   and Chrome's [File System Access API permission model](https://developer.chrome.com/docs/capabilities/web-apis/file-system-access).
5. WebKit: [Storage policy, eviction and persistence](https://webkit.org/blog/14403/updates-to-storage-policy/).
6. WebKit: [Safari 17.2 web app login-cookie copying and separate storage](https://webkit.org/blog/14787/webkit-features-in-safari-17-2/).
7. Microsoft Edge: [Handle files](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/handle-files);
   Chrome: [File Handling API, desktop support and installation requirement](https://developer.chrome.com/docs/capabilities/web-apis/file-handling).
8. Microsoft Edge: [Handle protocols](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/handle-protocols);
   Chrome: [PWA protocol-handler registration and permission](https://developer.chrome.com/docs/web-platform/best-practices/url-protocol-handler).
9. W3C: [Service worker lifetime](https://www.w3.org/TR/service-workers/#service-worker-lifetime).
10. Chrome: [Page lifecycle, freezing and discarding](https://developer.chrome.com/docs/web-platform/page-lifecycle-api).
