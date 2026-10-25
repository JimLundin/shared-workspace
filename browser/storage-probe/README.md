# Browser storage proof

Question: can a website host save its own identity and workspace using the actual
Turso engine, without a native bridge, libSQL, or a hosted database?

**Yes, verified in Chromium 156 on Linux on 2026-10-25.** The development and
production builds both recovered the same Ed25519 identity, workspace, note, and
revision after a page reload and a complete browser-process restart. The recovered
private key signed a fresh challenge that its public key verified. A separate
browser context received an independent identity. The same checks passed with
**no isolation headers from the static server**, using the pinned
`coi-serviceworker@0.1.7` response wrapper at a project-like `/proof/` URL prefix.
This is a disposable storage
experiment, not the signed workspace domain implementation or a synchronization
implementation.

## Run

```sh
cd browser/storage-probe
npm ci
npx playwright install chromium
npm run dev
```

Open `http://127.0.0.1:4387` on the same computer. The server only binds loopback;
this does not expose a phone preview or publish anything. Edit the note, save,
reload, and compare the displayed identity and revision. The database is named
`workspace-storage-prototype-wipe-me.db` so its disposable scope is explicit.

Automated verification uses a temporary Chromium profile, closes the complete
browser process, and opens a new process against that same profile:

```sh
npm test
npm run build
npm run test:built
npm run test:static
npm run audit:footprint
```

The harness also deliberately writes text into a `STRICT INTEGER` column and
requires rejection without changing the saved revision. It verifies an OPFS
database file exists and checks that the primary host makes no external HTTP
requests. Generated JSON and a screenshot appear under ignored `artifacts/`.
The temporary browser profile is deleted after the test.
Checked results are in [production evidence](evidence/chromium-production.json),
[static-host evidence](evidence/chromium-static.json), and
[footprint evidence](evidence/footprint.json). They contain only a temporary public
device identifier and sample note, not its private key or browser profile.

The static-hosting test serves `dist/` with a tiny loopback HTTP server that sends
no COOP or COEP headers. A separate raw HTTP request verifies their absence. The
browser registers the same-origin isolation service worker, reloads, and becomes
cross-origin isolated; the complete persistence test then runs. This supports a
GitHub Pages-style delivery path without requiring those headers from the server.
Nothing was published, and GitHub Pages itself and iPhone Safari remain untested.
The worker strengthens browser isolation; it does not grant desktop or native
permissions. It deliberately uses `COEP: require-corp`, matching the header-based
test. It must be a separate same-origin file at or above the application scope.

## What actually runs

- `@tursodatabase/database-wasm@0.8.3`, pinned with its npm lockfile, is the actual
  Rust Turso engine compiled to WebAssembly. No `@libsql/*`, cloud service, native
  runtime, or IndexedDB adapter is involved.
- `connect("workspace-storage-prototype-wipe-me.db")` opens **OPFS**, the origin
  private file system. The package's worker opens database and `-wal` files with
  `createSyncAccessHandle`; writes are flushed through that file handle.
- Device identity, workspaces, and notes are real `STRICT` tables. The private key
  is a BLOB; revision is INTEGER. SQL type enforcement rejects
  `"not an integer"` with `cannot store TEXT value in INTEGER column notes.revision`.
- The proof uses Web Crypto Ed25519. It does not adapt or replace the native
  signed operation model. Sample keys and content stay in the browser profile.

The production test observed a 4,096-byte database and 57,712-byte write-ahead log
after restart. It does not manually checkpoint or copy the database into another
storage system to obtain that result.

## Delivery requirements and limits

1. Serve from a secure origin (HTTPS in deployment; loopback is suitable for this
   local experiment). The current package requires `SharedArrayBuffer` and
   cross-origin isolation. Both dev and production preview configure
   `Cross-Origin-Opener-Policy: same-origin` and
   `Cross-Origin-Embedder-Policy: require-corp`. A production host must serve those
   headers, or use the verified isolation service-worker path included in the
   static build. A static host still needs correct JavaScript/WASM MIME types and
   HTTPS. The service worker causes a first-visit reload and is not an offline
   cache. These policies also constrain cross-origin frames and resources;
   embedding Office separately needs an explicit architecture decision.
2. OPFS is scoped to the exact origin and browser profile. Changing domain, port,
   or profile creates different storage. The test's private context demonstrated
   isolation, not long-term private-mode durability.
3. Storage survived an orderly process restart. This does **not** prove resilience
   to power loss, forced browser termination, device restore, quota exhaustion, or
   browser eviction. No silent fallback to an in-memory database is permitted.
4. `navigator.storage.persist()` is a separate request to the browser, exposed by
   the page's button. The automated run had `persistent_storage_granted: false`.
   Restart persistence is still real, but browser eviction and user clearing site
   data can remove the key and all local content. A real host needs replication
   and recoverability. This proof has neither.
5. The OPFS synchronous handle is exclusive. This proof runs one database owner
   per profile at a time. It does not implement same-origin multi-tab leadership.
6. The private key is stored unencrypted in OPFS and is available to JavaScript of
   the same origin. There is no Keychain, DPAPI, device-bound protection, or key
   recovery. No real account credentials are used here.
7. This is not an installable PWA: the service worker only provides isolation; no
   install manifest, offline shell cache, voice
   commands, application integration, transport, or iPhone installation is added.
   Physical iPhone Safari/PWA behavior remains untested. Playwright's WebKit
   installer does not support this environment (`ubuntu20.04-x64`), so even
   simulated WebKit validation was unavailable.

## Footprint

The production build on 2026-10-25 produced approximately 11.61 MB of WASM
(3.99 MB gzipped), 171 KB worker JavaScript, 203 KB engine glue, and 7 KB application
JavaScript. These are output asset sizes, not download measurements or resident
memory measurements.

The pinned wrapper requests 4,000 WASM pages = **250 MiB of initial shared linear
memory**, with a 4 GiB growth limit. This does not establish a 250 MiB resident-RAM
requirement. The [runtime audit](memory-audit-20261025/README.md) observes actual
`Memory.buffer.byteLength`, process-family RSS/PSS, and official SQLite WASM under
the same workload. Its five-run Linux Chromium startup comparison found about
81 MiB additional PSS for Turso relative to the empty app.

A separately named disposable build lowered the wrapper to the binary's minimum
1,009 pages = 63.0625 MiB, preserving the exact engine binary. It passed durable
write/reload/restart checks and grew normally; startup PSS fell by about 11.5 MiB,
much less than the logical-size reduction. No supported Turso consumer memory
option was found in stable 0.8.3 or next 0.9.0-pre.1. This original probe and the
application remain unmodified; the audit patch is experimental and has not been
validated on iPhone Safari. `npm run audit:footprint` still reproduces the original
static binary limits and asset sizes.

## Primary sources

- [Pinned npm release](https://www.npmjs.com/package/@tursodatabase/database-wasm/v/0.8.3)
- [Turso source at the release's git revision](https://github.com/tursodatabase/turso/tree/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript)
- [Turso JavaScript API](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/docs/javascript-api-reference.md)
- [OPFS](https://developer.mozilla.org/en-US/docs/Web/API/File_System_API/Origin_private_file_system)
- [Persistent storage request](https://developer.mozilla.org/en-US/docs/Web/API/StorageManager/persist)
- [Cross-origin isolation](https://developer.mozilla.org/en-US/docs/Web/API/Window/crossOriginIsolated)
- [Pinned isolation service worker](https://github.com/gzuidhof/coi-serviceworker/tree/v0.1.7)

Runtime evidence takes precedence over assuming API availability implies Safari
or iPhone support.
