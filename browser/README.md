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

Workspaces, notes, application/document references, code documents, resource
revisions and signed membership history are shared. A workspace can also have an
OpenAI transcription connection, shared with its enrolled devices and members.
Browser identity, application login sessions, microphone state, appearance and
the current view remain local. Each browser profile and website origin has its own actual Turso Database
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

The microphone is the primary input. It records on explicit request and sends the
finished clip to OpenAI using the captured workspace's connection. The transcript
can become a note, enter the original draft, or propose a supported command for
review. Configure a direct OpenAI API key through the private connection settings;
the app does not reuse the desktop assistant's work-gateway credential. Device
speech is an explicit fallback. See [provider dictation](../docs/provider-dictation.md)
for capture limits and the distinction between tested Chromium behavior and
unverified physical iPhone recording.

Connection records are signed and encrypted locally, and travel only to enrolled
peers in their workspace. Concurrent changes require resolution. Removing a
connection does not revoke its key at OpenAI. Sharing a workspace also shares the
ability to use and change its connection; service account login cookies are not
copied. This is a trusted-browser-origin model, not protection from malicious code
running in that origin. See the [connection contract](host/CONNECTIONS-CONTRACT.md).

CodeMirror provides a real editor inside the workspace; saved text, language and
history synchronize. It provides no terminal or code execution. Office and
vscode.dev prevent ordinary browser embedding, so their cards open the service or
document in a browser tab. The service still owns document access and sign-in.
See [application surfaces](../docs/shared-application-surfaces.md) for the actual
embedding evidence. Older clients must update before synchronizing new surface
operations; existing signed note history is preserved.

Turso 0.8.3 requests **250 MiB of initial WASM linear memory**, not 250 MiB of
additional resident RAM. The [runtime audit](storage-probe/memory-audit-20261025/README.md)
measured about 81 MiB of additional process-family PSS for a small database in
Linux Chromium, relative to the same empty app; this is not a full-host or iPhone
measurement. A smaller experimental wrapper worked, but the application retains
the upstream package pending broader browser/device testing and a maintainable
configuration path. Safari and managed-network compatibility still need testing.

See [the architecture research](../docs/browser-host-direction.md), component
evidence and each implementation directory for exact build and test commands.
