# Browser workspace hosts

The primary delivery path is a static HTTPS website. Each participating browser
owns its identity, local workspace records, and encrypted peer connections. The
website distributes application assets; it holds no workspace database and is
not the coordinator of the devices.

## Components

| Directory | Responsibility |
| --- | --- |
| `app` | Responsive workspace UI, voice input, explicit pairing, offline application assets |
| `host` | Typed UI facade and dedicated worker owning Turso, lifecycle, invitations and synchronization |
| `host-wasm` | The existing signed Rust workspace model and an Iroh browser transport adapter |
| `checks` | Acceptance through real browser hosts and separate persistent profiles |
| `storage-probe`, `iroh-probe` | Retained component experiments and measured evidence |

The browser downloads HTML, CSS, JavaScript, worker scripts and two WebAssembly
engines. Participating devices need no Rust, Node or Python installation.
Rust/Cargo, wasm-bindgen, Clang and Node are build tools. Capabilities outside the
browser sandbox, such as desktop automation, require a permitted local host.
Connecting that optional host is a separate integration; the first browser slice
pairs browsers with browsers.

## Shared state and local ownership

Workspaces, notes, resource revisions and signed membership history are shared.
Browser identity, login sessions, microphone state and the current view remain
local. Each browser profile and website origin has its own actual Turso Database
store in OPFS. One tab owns that store through a Web Lock; another tab must not
take the lock or rotate the identity.

Commands first prepare an immutable domain change. The storage worker commits
the new operations and retry identity in one SQL transaction before exposing the
change. An uncertain storage result requires reopening and reading retained
state. Network calls never hold the SQL queue.

Invitations select workspaces and expire. Enrollment checks the authenticated
Iroh identity; a successful enrollment can be retried by that same identity if
its reply was lost. A workspace member can introduce another device while the
original device is absent. This experimental membership model gives enrolled
members sharing rights; separate roles and revocation are future work.

Peers exchange signed operations, not database files. Concurrent revisions are
retained for explicit resolution. A known operation cannot be replayed with
different content, and a stale edit cannot silently replace newer heads.

## Connectivity and device limits

Browser Iroh uses encrypted WebSocket relays, including on the same Wi-Fi.
Relays route encrypted traffic and do not become authoritative workspace hosts.
Peers must overlap online to exchange updates; this version has no offline
mailbox. Losing a connection leaves acknowledged local edits available.

iPhone Safari can suspend a page. Returning to the foreground reconnects and
reconciles retained work; this is not an always-running background phone agent.
The browser controls storage retention, and deleting website data deletes that
local identity and copy. Keep another paired copy of important work.

Voice input is started explicitly and produces an editable command. Availability
and speech processing depend on the browser; typing and keyboard dictation
remain available. Application embedding is subject to the target website's
frame and login policies. Native document-hosting experiments do not establish
that Office can be embedded in this public website.

The pinned Turso package starts with **250 MiB of WASM linear memory**. That is
not a measurement of resident memory. Safari, physical iPhone behavior, resource
usage and managed-network compatibility require device testing.

See [the architecture research](../docs/browser-host-direction.md), component
evidence and each implementation directory for exact build and test commands.
