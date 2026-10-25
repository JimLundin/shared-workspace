# Browser workspace host

This package implements a real local browser host. The UI facade in
`src/host.ts` starts a dedicated module worker; that worker owns the database,
the signed workspace domain, and the Iroh endpoint. No native bridge or Python
runtime is present in this path.

```ts
import { BrowserHost, takePairingTicketFromLocation } from './src/host.ts';

const pendingTicket = takePairingTicketFromLocation(); // Clear URL before explicit Join.
const host = await BrowserHost.open({ deviceName: 'My browser' });
const snapshot = await host.snapshot();
// Show pendingTicket in a confirmation flow; do not join automatically.
```

`BrowserHost` matches the native UI's `Host` interface: `available`, `snapshot`,
`command`, `invite`, `join`, and `sync`. It adds `setForeground(boolean)` and
`close()`. Command, receipt, snapshot, and peer-status contracts reuse
`native/ui/src/protocol.ts`, which is a pure decoder module and imports no Tauri
runtime. The UI owns voice interaction and browser visibility events.

## Owners and durable decisions

- `worker.ts` acquires an exclusive, non-stealing Web Lock for the database name
  before loading Turso or starting the endpoint. A second tab using the same
  database receives an explicit ownership error. Closing the host drains its
  storage queue and releases the lock; a lost worker is terminated by the proxy.
- `store.ts` uses the actual `@tursodatabase/database-wasm@0.8.3` engine and OPFS
  database/WAL files. Tables for the device seed, signed operations, original
  command requests, peer contacts, and invitations are `STRICT`. No libSQL,
  in-memory fallback, IndexedDB copy, or hosted database is used.
- A 32-byte random seed is committed before creating the Iroh endpoint. The same
  seed initializes Iroh and the WASM domain's Ed25519 signatures. Reopening loads
  the retained seed; corruption fails instead of silently replacing identity.
- `browser/host-wasm` imports the native pure domain model. It validates retained
  history and original requests, prepares immutable candidate changes, and keeps
  candidate state separate from committed state. Browser storage writes signed
  operations and the exact original command in one SQL transaction, then commits
  that prepared domain candidate. Replays and conflicts retain the same receipts
  as the native implementation.
- A database error after preparing a write is uncertain. The host refuses further
  operations until reopened; it never turns such an error into a definitive
  command rejection. Reopening reconstructs state from durable rows. An
  interrupted response can therefore be retried with the same operation identity.
- The storage promise queue never waits for network I/O. Peer requests may wait
  on storage, but cannot hold its queue across a relay connection or response.

## Pairing and synchronization

The WASM transport authenticates the actual remote Iroh identity and bounds
messages and in-flight requests. `owner.ts` treats claimed contact identities as
untrusted and checks them against that authenticated identity before processing.
The browser protocol has its own ALPN; native/browser wire interoperability is
not claimed by this package.

Invitations select 1–32 workspaces and expire after 15 minutes. Only a SHA-256
hash of their random nonce is stored. Acceptance commits membership grants,
the accepted device, and its contact in one transaction. A used invitation rejects
another device; the same authenticated device can retry after expiry to recover a
lost acceptance response. The receiver verifies the invitation's exact scope and
requires an explicitly selected bootstrap before accepting unfamiliar roots.

With an HTTPS `baseUrl`, the returned ticket is a link containing the enrollment
secret in its fragment. The fragment is never sent to the static server. The UI
must call `takePairingTicketFromLocation` before displaying its explicit Join
flow. This package does not log tickets or place them in browser persistent
storage. A shared link remains a bearer enrollment capability until used/expired.

Every ordinary import is anchored to an already enrolled workspace and verifies
signed causal membership. A newly granted member can carry its signed grant to
another member while its inviter is offline. Every peer export separately checks
both the local and recipient membership, including zero-operation requests.
Contacts are validated and only retained/shared for members of selected
workspaces. No peer is permanently authoritative.

Synchronization uses cached signed-history fingerprints, fetches history only
when needed, and batches shared workspaces in groups of at most 32. Concurrent
offline edits retain multiple revision heads. A five-second foreground loop
retries reachable peers. Explicit pause/resume intent is fenced against older
background work; it closes and frees retired endpoints. Browser suspension still
stops execution, so this is not an always-running mobile daemon.

## Building and validation

Build `browser/host-wasm` first, then install this package and run `npm run check`.
A consuming Vite application needs `worker.format: 'es'` and an ESNext build
target, and must include the generated WASM assets. The page needs a secure origin,
cross-origin isolation, Web Locks, OPFS, WebAssembly, and workers. Isolation can
come from server COOP/COEP headers or the separately tested same-origin isolation
service worker. This package itself owns neither static deployment nor PWA caching.

The public-API acceptance harness is in `browser/checks`. Its production bundle
must be served as actual static resources: Playwright route fulfillment aborted
the nested Turso worker in this environment, while the identical bundle opened
correctly from a loopback static server. This is a fixture limitation, not a reason
to bypass browser security.

See the harness's final report for completed assertions. The earlier independent
storage and transport probes do not establish all host-level guarantees by
themselves. Physical iPhone Safari/PWA installation remains a separate acceptance
step.

## Current limits

- Workspace history is bounded to 4,096 retained operations and 7 MiB of signed
  content; a document body is limited to 128 KiB. There is no history compaction,
  membership revocation, attachment transfer, or large-document claim.
- OPFS belongs to this exact origin/profile. Browser eviction, clearing site
  storage, uninstalling an app, or profile loss can remove data and identity.
  Replication helps only for content already copied to another peer. There is no
  identity backup or restoration flow yet.
- The seed is unencrypted in OPFS and accessible to code at the same origin.
  There is no Keychain/DPAPI, OS credential vault, or device-bound key protection.
- The pinned Turso wrapper initializes 250 MiB of shared linear memory, despite
  the binary's smaller 63.0625 MiB import minimum. This is not a measured resident
  memory figure or a claim of low phone resource use; see the storage probe's
  reproducible footprint audit.
- Relays provide routing, not authority. Availability outside the current
  browser session depends on browser lifecycle and connectivity. Native desktop
  controls, Office embedding, voice processing, and UI features are separate
  integrations.
