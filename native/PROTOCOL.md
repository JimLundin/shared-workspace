# Native workspace interface v1

The local shell invokes the native host directly. There is no HTTP server. JSON
field names are snake_case; variants carry a `type` discriminator. Shared work
lives in Turso Database on every device. UI focus, selected workspace, editors,
scroll and microphone sessions remain local.

## Rust facade and shell commands

Crate `workspace-core` exports `Host`, `NetworkMode`, `Command`,
`CommandReceipt`, `HostSnapshot`, `PairingInvite` and `SyncReport`.

- `Host::open(data_dir: impl AsRef<Path>, device_name: String, mode: NetworkMode)`
  is async and returns `Result<Arc<Host>>`. `NetworkMode::Default` enables Iroh's
  standard native direct/relay discovery; `Loopback` is isolated test routing.
  Device names must contain non-whitespace text, have no control characters,
  and fit within 120 UTF-8 bytes; startup and peer contacts use the same validator.
- `host.command(Command)` -> `CommandReceipt` after the local durable commit.
- `host.snapshot()` -> `HostSnapshot`.
- `host.create_invite(workspace_ids: Vec<String>, ttl_seconds: u32)` ->
  `PairingInvite { ticket: String, expires_at_ms: i64 }`.
- `host.join(ticket: String)` -> `HostSnapshot` after paired bootstrap commits.
- `host.sync()` -> `SyncReport` with current peer observations after one pass.
- `host.set_foreground(bool)` suspends/resumes the native endpoint. Local work
  remains on disk. Foreground resumes catch-up; iOS is not always running.
  Background transport recovery cannot reverse an explicit pause.
- `host.shutdown()` closes network activity cleanly.

All methods are async. Shell command names are `host_snapshot`, `host_command`
(argument `command`), `host_invite` (`workspace_ids`, `ttl_seconds`), `host_join`
(`ticket`), `host_sync`, and `host_set_foreground` (`foreground`). Shell errors
are strings suitable for visible error messages, never a success receipt.

## Workspace commands

Caller-generated IDs are stable for an intended operation and retries. A reused
operation ID with different content is rejected. Create IDs are opaque strings
validated at the native interface; use UUIDs from the shell/UI.

```json
{"type":"create_workspace","operation_id":"uuid","workspace_id":"uuid","title":"Launch review"}
{"type":"create_resource","operation_id":"uuid","workspace_id":"uuid","resource_id":"uuid","title":"Brief","body":"Text"}
{"type":"edit_resource","operation_id":"uuid","workspace_id":"uuid","resource_id":"uuid","expected_heads":["revision-id"],"title":"Brief","body":"Revised text"}
{"type":"create_surface","operation_id":"uuid","workspace_id":"uuid","resource_id":"uuid","title":"script.py","surface":{"type":"code_document","language":"python","text":"print(42)\n"}}
{"type":"edit_surface","operation_id":"uuid","workspace_id":"uuid","resource_id":"uuid","expected_heads":["revision-id"],"title":"script.py","surface":{"type":"code_document","language":"python","text":"print(43)\n"}}
{"type":"add_placement","operation_id":"uuid","workspace_id":"destination-id","resource_id":"resource-id","source_workspace_id":"origin-id"}
{"type":"remove_placement","operation_id":"uuid","workspace_id":"destination-id","resource_id":"resource-id","source_workspace_id":"origin-id"}
```

An edit with all observed head IDs resolves a conflict. A stale edit returns a
conflict rather than silently replacing unobserved material. Removing a placement
never deletes a resource. References do not grant access to the original work.

Application/document surfaces use the same resource IDs, placements, signed
history and receipts as notes. A revision contains either the legacy `body`
field or a typed `surface` payload, never both. Surface targets can identify an
application, an external document or editable shared code text. The
[surface interface](../docs/shared-application-surfaces.md) specifies those
variants, validation and older-host compatibility. Vendor application sessions
and document permissions are not transferred with a link.

Receipts:

```json
{"type":"applied","operation_id":"uuid","workspace_id":"uuid","replayed":false}
{"type":"conflict","resource_id":"uuid","expected_heads":["old"],"actual_heads":["new"]}
{"type":"rejected","operation_id":"uuid","workspace_id":"uuid","message":"The change was rejected before any transaction began."}
```

A `rejected` receipt is a definitive no-commit result from command preparation;
the editor can be corrected and resubmitted. A thrown storage/commit error or a
lost reply remains uncertain and must not be relabelled as rejection. Retrying
an uncertain intended change retains its original operation identity and payload.

## Snapshot

`HostSnapshot` contains the Store snapshot fields plus network/peer observations:

```json
{
  "device_id":"iroh-public-key",
  "device_name":"My phone",
  "workspaces":[{"id":"uuid","title":"Launch","placements":[{"resource_id":"uuid","source_workspace_id":"uuid"}],"members":["device-id"],"heads":["operation-id"]}],
  "resources":[{"id":"uuid","workspace_id":"uuid","heads":[{"id":"revision-id","author":"device-id","title":"Brief","body":"Text","parents":[]}],"history":[]}],
  "operation_count":1,
  "network":{"type":"active"},
  "peers":[{"id":"device-id","name":"Laptop","workspace_ids":["uuid"],"state":{"type":"never_synced"}}]
}
```

Network variants: `active`, `paused`, `closed`, `unavailable { detail }`. An
unavailable transport leaves local work usable and retries in foreground. Active means the local transport
is running; it does not assert another peer is reachable. Peer state variants:
`never_synced`, `syncing`, `synced { at_ms, imported }`,
`unreachable { at_ms, detail }`. Snapshot times and availability are observations,
not workspace authority. `SyncReport { peers: [PeerStatus] }` uses the same peer
shape. Each pass attempts the contacts known at its start. An exchange may teach
it about another authorized member: that newly discovered contact appears as
`never_synced` until the next explicit or automatic foreground pass. This is
pending work, not a completed exchange or a transport failure. The UI must not
label the entire mesh synchronized while any member is pending or unreachable.
A completed peer sync only acknowledges that exchange; another offline device
may hold newer work. The foreground owner repeats catch-up every five seconds
and after local changes.

## Pairing and peer transfer

A pairing ticket is a versioned base64url JSON payload with the inviter's public
endpoint address, a random one-use nonce, selected workspace IDs and expiry.
The nonce is hashed before local persistence. Do not log tickets. Authenticated
Iroh endpoint identity must match the ticket before bootstrap. Consuming an
invite atomically records membership and peer contact. An exact retry by the
same accepted peer recovers the result; another peer cannot reuse it.

Replicated operations are signed by their original authors. Their workspace
causal history establishes membership; connected peer identity and requested
workspace grants are checked independently. Each peer can originate and receive
work. No permanently privileged desktop or permanent server is required.

Foreground peers first compare cached content fingerprints for the selected
workspaces. Identical histories exchange only a small summary and member contact
updates; they do not reload, revalidate or transfer the full operation log.
Each invitation and exchange selects at most 32 workspaces. Cumulative membership
may exceed that limit: synchronization exchanges consecutive bounded selections
without removing earlier grants.
Changed histories exchange bounded signed operation sets (up to 4,096 operations
and an 8 MiB wire envelope; retained history has its own smaller store limit).
A summary mismatch never itself establishes permission. Every requested scope
must be authorized by the resulting anchored signed membership before any
history is exported, including when the sender supplied zero operations.

Member contact records are shared only within signed workspace membership.
Addresses and names confer no rights; outgoing Iroh identity is always pinned.
This allows a newly enrolled phone to contact an existing desktop even after
the inviting device stops. New member grants can travel with their signed causal
proof anchored to the receiver's already-enrolled workspace root.

The host does not synchronize screen geometry, credentials, microphone audio or
external-application sessions. Durable local commits remain available when
delivery fails. External agent/application execution is outside this first
native synchronization slice. Default routing uses Iroh's n0 native address
lookup and encrypted relay infrastructure; these services route traffic and
never own the workspace. Loopback mode disables them for deterministic checks.

## Independent-process CLI

`workspace-peer --data-dir PATH --name NAME --network loopback|default` owns
one database and endpoint. It emits one `{"event":"ready","device_id":"..."}`
line, then accepts newline-delimited JSON on stdin:

```json
{"id":"read-1","method":"snapshot"}
{"id":"write-1","method":"command","params":{"command":{"type":"create_workspace","operation_id":"op-1","workspace_id":"space-1","title":"Planning"}}}
{"id":"invite-1","method":"invite","params":{"workspace_ids":["space-1"],"ttl_seconds":600}}
{"id":"join-1","method":"join","params":{"ticket":"workspace1:..."}}
{"id":"sync-1","method":"sync"}
{"id":"pause-1","method":"foreground","params":{"foreground":false}}
{"id":"resume-1","method":"foreground","params":{"foreground":true}}
{"id":"close-1","method":"shutdown"}
```

Each response is `{"id":"...","ok":true,"result":...}` or
`{"id":"...","ok":false,"error":"..."}`. Shutdown responds before exiting.

## Native voice and pairing camera

`host_capabilities` returns `{ platform: "ios" | "windows" | "linux" | "macos",
 pairing_camera: boolean }`. Camera pairing is true only in the iPhone host;
this is independent of speech availability. Other platform strings may be
returned by a future build, so unknown platforms must keep pasted-ticket input.

Only the bundled UI can call the `workspace-voice` plugin. Voice is explicitly
started and never executes a workspace command by itself. The UI captures its
workspace/resource context before asking for voice input, then submits the
reviewed transcript through `host_command`.

`invoke("plugin:workspace-voice|capability")` returns one of:

```json
{"type":"native_speech","locale":"en_US","on_device":true,"authorization":"not_determined"}
{"type":"system_dictation"}
{"type":"unavailable","reason":"..."}
```

On iOS, `authorization` is `not_determined`, `authorized`, `denied`, `restricted`
or `unavailable`. Capability observation does not request permission.
`on_device` describes support for the reported locale; starting a different
locale can require Apple's recognition service. The UI must explain this before
capture. The plugin prefers on-device recognition whenever that locale supports it.

To start, create a Tauri `Channel` and call:

```ts
await invoke("plugin:workspace-voice|start", {
  session_id: crypto.randomUUID(),
  locale: "en-US",
  on_event: channel,
});
```

The channel receives `type`-tagged events carrying that `session_id`:
`starting`, `listening`, `transcript { text, is_final }`, `stopped`, `cancelled`,
`error { message }`, or `system_dictation`. Ignore events for abandoned sessions.
On iPhone, `plugin:workspace-voice|stop` with `{ session_id }` ends capture and
allows a short final transcription interval; `...|cancel` with the same identity
abandons that session immediately. A stale session cannot stop a later capture. The plugin
also cancels on application deactivation or audio interruption. Microphone and
speech permissions are requested only by `start`. The transcript is never logged
or automatically submitted as work.

On Windows, focus the editable command field **before** calling `start`. This
asks Windows to open its own Win+H dictation control and emits `system_dictation`;
it does not claim that recording or recognition succeeded. Words arrive as
normal input in that field. Windows owns stopping and its microphone UI. Do not
show the iOS start/stop lifecycle or expect transcript channel callbacks for this
mode. The native launcher refuses to send the shortcut if another process has
become foreground.

On iPhone, the official barcode scanner plugin can read an invite QR after an
explicit user action:

```ts
const scanned = await invoke("plugin:barcode-scanner|scan", {
  formats: ["QR_CODE"], cameraDirection: "back", windowed: false,
});
```

Its result has `content`, `format`, and optional `bounds`. Treat `content` as
untrusted input and pass it through the same `host_join` validation as a pasted
ticket. `plugin:barcode-scanner|cancel` ends the camera. Camera permissions can
be observed/requested through `...|check_permissions` and `...|request_permissions`
(the native plugin's standard permission commands). Ticket text must not be
logged. Desktop can display the QR and retain pasted-ticket pairing; no remote
URL or HTTP preview service is involved.

## Native launch receipt

The shell writes `native-host-ready.json` in its application-local data directory
only after the real host opens its database and returns a successful snapshot.
It contains `schema_version`, `state: "ready"`, `device_id`, `pid`,
`started_at_ms`, and `app_version`; no workspace content or private identity.
A prior receipt is removed before startup. This establishes local host readiness,
not a reachable peer or completed synchronization.
