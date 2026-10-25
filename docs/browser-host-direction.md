# Browser host direction

Research date: 25 October 2026. This note follows the user's decision to make a
website/PWA the primary delivery. The existing native hosts remain available as
source and as a possible later desktop-capability adapter. This changes the
delivery decision in [native-architecture.md](native-architecture.md); it does not
invalidate the native protocol or storage work.

## Architecture and bundle

A real browser host is feasible. Turso publishes its Rust database engine as
WebAssembly, and Iroh supports browser peers through an application-specific
WebAssembly wrapper. Neither requires the user to install Python, Node, a
separate browser engine, or a native executable. Rust, Cargo, Node and Vite remain
build tools. [1][2][5]

| Part | Proposed implementation | Ownership |
| --- | --- | --- |
| Interface | TypeScript, HTML and CSS; responsive workspace/resource views; voice command entry | Browser tab or installed Home Screen web app |
| Durable local records | Actual Turso Database WASM 0.8.3, OPFS files and database worker | Each browser host |
| Domain and protocol | Reuse validated signed-operation contracts and pure projection rules; extract browser-compatible Rust where practical | Each browser host |
| Peer transport | Iroh 1.4.1 compiled to WASM with `wasm-bindgen` | Each active browser host |
| Distribution | Versioned static HTTPS assets, manifest, optional service worker for offline assets | Static website host |
| Connectivity | Configured Iroh relays; address lookup or explicit peer addresses | Routing infrastructure, without workspace authority |

The downloaded application comprises web assets, Turso WASM and its module
worker, the Iroh wrapper's JavaScript/WASM, and PWA assets. It need not be one
physical file. Separate WASM modules do not imply separate installed runtimes.
Keep dependencies explicit instead of claiming that compiling everything to
TypeScript removes the database or networking engine.

Multiple workspaces share the host's local store. Signed workspace history,
resource revisions and membership replicate. Device identity, application login
sessions, microphone state, active selection and screen geometry remain local.
Send validated operations over Iroh; do not replicate the database file or WAL
between devices. The existing membership and conflict rules remain the authority
for accepting an operation, regardless of which device is currently active.

## Actual Turso browser API and storage

The package is **`@tursodatabase/database-wasm@0.8.3`**, not libSQL and not the
Turso Cloud/serverless client. Its stable release resolves to source commit
`607bff9fd0e547a25a97b3bf06e83bcc5d99d53c`. `0.9.0-pre.1` is a prerelease, not
the stable pin used by this experiment. [1]

```typescript
import { connect } from "@tursodatabase/database-wasm";

const db = await connect("workspace.db");
await db.exec(`CREATE TABLE IF NOT EXISTS workspace (
  id TEXT PRIMARY KEY NOT NULL,
  title TEXT NOT NULL
) STRICT`);
await db.prepare("INSERT INTO workspace (id, title) VALUES (?, ?)")
  .run(workspaceId, title);
const rows = await db.prepare("SELECT id, title FROM workspace").all();
```

The async API also provides `transactionAsync(async (txn, args) => ...)`.
Parameters must be bound and external rows validated at the typed boundary.
`STRICT`, constraints and checked domain types provide the required typing;
ordinary SQLite-compatible affinity alone is not strict typing. Use only Turso
extensions that are verified against the pinned release. [2][3]

The implementation opens both `workspace.db` and `workspace.db-wal` in OPFS using
`navigator.storage.getDirectory()`, `getFileHandle()` and
`createSyncAccessHandle()` in a worker. Writes use synchronous access handles;
flush calls are implemented. There is no IndexedDB fallback in this storage
path. Opening `:memory:` is not durable storage. [2]

Exclusive OPFS file handles make simultaneous tabs an explicit ownership problem.
The initial application should permit one database-owning tab per browser
profile/origin, with a clear second-tab state. Releasing a lock or reloading must
not generate a new identity when a valid identity already exists. An origin or
profile change is a different local storage location, so choose a stable HTTPS
origin before real pairing. [2][7]

### Hosting and memory requirements

Turso 0.8.3 creates a shared `WebAssembly.Memory`. The upstream browser test
server explicitly sets: [2]

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

The direct hosting path serves the application and worker/WASM assets over HTTPS
with these headers. A second path has now passed the storage experiment in
Chromium: **`coi-serviceworker@0.1.7`** adds isolation headers to same-origin
responses inside the browser, then reloads the page under service-worker
control. The test server supplied neither COOP nor COEP, verified by a separate
HTTP request. The worker used `COEP: require-corp`, and the application reached
`crossOriginIsolated === true`. See the [static-host evidence](../browser/storage-probe/evidence/chromium-static.json).
[15]

The service-worker path was tested on secure-context loopback at a `/proof/`
URL prefix. It supports investigating a GitHub Pages-style host, but does not
establish that the deployed website or iPhone Safari works. Deployment still
requires HTTPS, correct JavaScript/WASM MIME types, and a same-origin worker
file at the correct scope. It introduces a first-visit reload; this worker is
not an offline asset cache and does not grant native or desktop permissions.
Integrate isolation and offline caching deliberately instead of registering
competing service workers for the same scope.

For either path, verify `crossOriginIsolated === true` in the deployed page.
Same-origin assets avoid unnecessary cross-origin resource-policy complications.
Do not silently replace durable storage with in-memory state if isolation or
OPFS is unavailable.

The released wrapper requests **4,000 WASM pages = 250 MiB of initial linear
memory**, with a 4 GiB growth limit. This is not an unavoidable resident-RAM
minimum. The [runtime audit](../browser/storage-probe/memory-audit-20261025/README.md)
measured actual linear size and process-family RSS/PSS against an empty app and
official SQLite WASM. Five Linux Chromium startup trials found about 81 MiB
additional PSS for a small Turso database. Those are database-probe results,
not full-application or iPhone measurements. [2]

A disposable wrapper patch to the binary's 1,009-page minimum (63.0625 MiB)
passed write/reload/restart checks with the unchanged engine binary. It reduced
startup PSS by about 11.5 MiB, despite reducing initial logical memory by about
187 MiB. The public package has no supported consumer memory-size option in
stable 0.8.3 or next 0.9.0-pre.1. Keep the application on unmodified upstream
Turso while pursuing a supported setting and testing Safari/iPhone lifecycle
and resource behavior. The experimental rewrite is not a production dependency.

Isolation headers also affect cross-origin resources and popup relationships.
Validate login and document integrations in the chosen hosting environment.
Native WebView document-hosting results do not prove that a public website may
iframe the same application. A browser host remains subject to the target's
embedding and permission policies. [8]

## Iroh browser integration

Use `iroh = { version = "=1.4.1", default-features = false, ... }` in a small
Rust wrapper compiled for `wasm32-unknown-unknown`. Expose just the commands and
events needed by the TypeScript host through `wasm-bindgen`. Iroh does not
currently publish a ready-made browser npm package. Its native NAPI bindings
are a different delivery path. [5][6]

The official echo example demonstrates the relevant API shape:
`Endpoint::builder(presets::N0).alpns(...).bind().await`, a protocol `Router`,
`endpoint.connect(...)`, `connection.remote_id()`, `open_bi()` / `accept_bi()`,
and finishing a send stream. Preserve the endpoint secret across restarts and
bind accepted peer identity to workspace membership. The example currently
uses older Iroh/wasm-bindgen pins; copy the API concepts, not its entire lockfile.
[6]

**Every browser Iroh connection uses a relay**, including two browsers on the
same Wi-Fi. Browsers cannot perform Iroh's direct UDP hole punching. Connections
remain end-to-end encrypted; a relay cannot decrypt workspace content, although
it observes connection metadata. Iroh relays are routing infrastructure, not a
durable offline mailbox or a permanent authoritative workspace host. [5][9]

The required outbound route includes HTTPS upgraded to WebSocket on TCP 443.
Ordinary website access does not prove that these connections are allowed.
Public n0 relays are documented for development/testing; choose an appropriate
relay service/configuration for a deployed product. The existing ban on the LAN
preview is not a reason to bypass device/network policy. [9]

When one host disconnects, surviving hosts can retain and edit their own copies.
They exchange missing accepted operations when a permitted connection returns.
If no authorized peers overlap online, pure relay transport cannot deliver the
changes until they do. An optional encrypted store-and-forward service would be
a separate feature and must not be implied by the first browser sync proof.

## iPhone storage, lifecycle and voice

WebKit documents OPFS and worker-only synchronous file access from iOS 15.2.
Safari 15.2 also added COOP/COEP support enabling SharedArrayBuffer and WASM
threading. These establish platform prerequisites, not a successful Turso
package test on this user's phone. Turso's published test configuration exercises
Chromium and Firefox; it does not include a Safari gate. [2][7][8]

WebKit's full storage-management API is available from Safari/iOS 17:
`navigator.storage.estimate()`, `persisted()` and `persist()`. Storage starts in
best-effort mode. WebKit can evict an origin under storage pressure or its other
documented policies; Home Screen installation is one input to its persistence
grant heuristics. Check the returned persistence result, handle quota errors,
and retain recovery/export and multiple-peer-copy options. A persistence grant
does not prevent the user deleting website data. [10]

Treat foreground resume as a reconnect/reconciliation event. Neither a PWA nor
a service worker establishes an always-running phone agent; service workers have
event-driven lifetimes and may be terminated when there is no event to handle.
Cache assets for offline startup, persist every committed edit, and recover
without depending on an unload callback. Test Safari and Home Screen execution
separately rather than assuming identical storage identity or lifecycle. [11]

For voice, feature-detect `SpeechRecognition` and `webkitSpeechRecognition` and
start from a visible user action. WebKit documents Safari speech recognition as
using the Siri engine and requiring Siri to be enabled. The Web Speech API
requires informed consent and recording indication; current specification APIs
must not be assumed implemented by every browser. Show listening/error state,
allow cancellation, and put the transcript into the same command path as typing.
Do not promise offline recognition merely because the constructor exists.
[12][13]

An ordinary editable command field also supports the iPhone keyboard's Dictation
feature, which Apple documents as available wherever the user can type. This
provides a useful fallback when web speech is absent, denied or restricted.
Dictation availability and offline processing depend on language/device support.
No provider secret belongs in publicly downloaded JavaScript. A future hosted
speech/model adapter would require its own authentication path. [14]

## Evidence and acceptance gates

The separate [Turso storage probe](../browser/storage-probe/) uses the actual
0.8.3 engine. Checked evidence records successful Chromium 156.0.8078.4 runs with
both [server isolation headers](../browser/storage-probe/evidence/chromium-production.json)
and the [isolation service worker](../browser/storage-probe/evidence/chromium-static.json).
Both retain an Ed25519 identity and strict workspace/note rows across page
reload and an orderly full browser-process restart; the recovered identity still
signs/verifies. A distinct browser context receives a distinct identity. A write
of text into a strict integer column is rejected without changing the revision.
Neither run makes external HTTP requests. Both record
`persistent_storage_granted: false`: restart recovery passed, while eviction
protection was not granted. This is desktop Chromium evidence, not iPhone or
abrupt-termination evidence.

The current [footprint report](../browser/storage-probe/evidence/footprint.json)
records an 11,613,778-byte WASM file (3,963,233 bytes gzip), a 170,916-byte worker,
203,037 bytes of engine JavaScript glue and 7,180 bytes of application JavaScript.
These are individual probe assets, not a complete final download measurement or
resident-memory measurement.

The separate [Iroh browser probe](../browser/iroh-probe/) also has a
[passing runtime receipt](../browser/iroh-probe/evidence/browser-exchange.json).
Three isolated Chromium contexts create distinct identities. Two exchange UTF-8
payloads in both directions over real Iroh 1.4.1 WASM and an external WSS relay;
receivers verify the authenticated remote identities. A third authenticated
identity is rejected because it was not selected. The page is cross-origin
isolated. The built Iroh WASM is 5,153,622 bytes; observed linear memories range
from 1,835,008 to 2,031,616 bytes across the three contexts. These values are not
browser-process RAM measurements.

These are separate storage and transport proofs. The subsequent
[integrated host checks](../browser/checks/) and
[production UI checks](../browser/app/) exercise signed workspace replication,
durable identity recovery, selected pairing, offline conflicts and reconciliation
through the actual browser host. Their retained reports distinguish Chromium
results from physical iPhone and public-hosting evidence.

Required completion evidence for a real browser release:

1. On the deployed HTTPS origin, assert isolation and durable storage; retain the
   same identity, workspace and edits through reload and full browser restart.
   Exercise quota/unsupported storage and simultaneous-tab behavior explicitly.
2. Pair two independent browser profiles with distinct persisted identities.
   Transfer a signed workspace edit using real Iroh and prove it exists in the
   recipient's durable database after restart. Reject an unpaired peer.
3. Disconnect both sides, make conflicting revisions, reconnect, retain both
   branches and resolve the conflict through the existing domain rules. Retry
   delivery and prove an operation does not apply twice.
4. Repeat on the user's iPhone in Safari and as a Home Screen web app. Exercise
   lock/unlock, app switching, network changes, restart and storage persistence.
   Measure launch time and memory, rather than extrapolating from WASM sizes.
5. Exercise permission granted/denied, unavailable recognition, interrupted
   speech, editable transcript and keyboard Dictation. Verify the visible command
   changes the same persisted workspace that peers receive.
6. Verify no private identity, pairing credential, app credential or workspace
   content enters the public build. Test application integrations separately;
   peer byte exchange alone does not demonstrate document embedding or agent
   application control.

The separate real browser storage and Iroh exchanges are established within the
limits above. A completed browser workspace, deployed website, iPhone host and
signed Apple build require their own implementation and runtime evidence.

## Primary sources

1. [Turso WASM npm registry metadata](https://registry.npmjs.org/@tursodatabase%2fdatabase-wasm),
   stable version and release source commit checked on the research date.
2. [Turso browser package at the 0.8.3 release source](https://github.com/tursodatabase/turso/tree/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/wasm),
   especially `promise-default.ts`, `index-default.ts` and `vitest.config.ts`;
   [WASM common implementation](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/wasm-common/index.ts)
   defines OPFS handling and shared-memory limits.
3. [Turso SQL data types](https://docs.turso.tech/sql-reference/data-types).
4. [Turso Database project](https://github.com/tursodatabase/turso).
5. [Iroh: WebAssembly and Browsers](https://docs.iroh.computer/languages/wasm-browser).
6. [Iroh browser echo source](https://github.com/n0-computer/iroh-examples/tree/main/browser-echo)
   and [Iroh 1.4.1 crate configuration](https://github.com/n0-computer/iroh/blob/v1.4.1/iroh/Cargo.toml).
7. [WebKit: File System API with Origin Private File System](https://webkit.org/blog/12257/the-file-system-access-api-with-origin-private-file-system/).
8. [WebKit: Safari 15.2 COOP/COEP and shared memory](https://webkit.org/blog/12140/new-webkit-features-in-safari-15-2/);
   [HTML cross-origin isolation specification](https://html.spec.whatwg.org/multipage/webappapis.html#cross-origin-isolated).
9. [Iroh: Configuring networks](https://docs.iroh.computer/configuring-networks)
   and [Security and privacy](https://docs.iroh.computer/concepts/security-privacy).
10. [WebKit: Updates to storage policy](https://webkit.org/blog/14403/updates-to-storage-policy/).
11. [Service Workers specification, lifetime](https://w3c.github.io/ServiceWorker/#service-worker-lifetime).
12. [WebKit: Safari 14.1 speech recognition](https://webkit.org/blog/11648/new-webkit-features-in-safari-14-1/).
13. [Web Speech API specification](https://webaudio.github.io/web-speech-api/).
14. [Apple: Dictate text on iPhone](https://support.apple.com/guide/iphone/dictate-text-iph2c0651d2/ios).
15. [Isolation service worker v0.1.7 source](https://github.com/gzuidhof/coi-serviceworker/tree/v0.1.7),
    with the tested configuration and HTTP/header assertions in the
    [storage probe](../browser/storage-probe/).
