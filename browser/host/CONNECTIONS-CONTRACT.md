# Workspace OpenAI connections

One OpenAI connection belongs to one workspace. Every enrolled member of that
workspace may use, replace, remove, and receive it. There is no global connection.
Metadata stays separate from generic workspace snapshots and resource history.

```ts
type ConnectionState =
  | { workspace_id: string; provider: 'openai'; status: 'missing' }
  | { workspace_id: string; provider: 'openai'; status: 'ready'; version: string; model: 'gpt-4o-transcribe' }
  | { workspace_id: string; provider: 'openai'; status: 'removed'; version: string }
  | { workspace_id: string; provider: 'openai'; status: 'conflict'; versions: readonly string[] };
type ConnectionReceipt = { operation_id: string; replayed: boolean; state: ConnectionState };

host.connection(workspaceId): Promise<ConnectionState>
host.connectionPeers(workspaceId): Promise<readonly {peer_id:string; status:'unknown'|'upgrade_required'|'synced'|'unreachable'}[]>
host.setConnection({ workspace_id, operation_id, expected_versions, api_key,
  model: 'gpt-4o-transcribe' }): Promise<ConnectionReceipt>
host.removeConnection({ workspace_id, operation_id, expected_versions }): Promise<ConnectionReceipt>
host.transcribe({ workspace_id, connection_version, audio: Blob, locale: string },
  signal?: AbortSignal): Promise<string>
```

`operation_id` is a random 32-byte lowercase hex identity, retained for retries.
Use `crypto.getRandomValues(new Uint8Array(32))` and hex encode each byte.
`expected_versions` is `[]` for missing, `[version]` for ready/removed, and all
`versions` for conflict. Exact retries acknowledge the same durable operation;
reusing its identity for changed input rejects. Concurrent offline changes yield
conflict, and recording refuses to use a conflicted or removed connection. Saving
against all conflict versions resolves it. Removal records persist as tombstones.
A receipt acknowledges the operation's durable commit; `state` is the latest
projection, which can be newer when an old operation is retried.

Capture `{workspaceId, connectionVersion}` before microphone permission awaits.
The worker checks the exact version again immediately before upload. Switching
workspaces does not retarget audio; changed credentials require recording again.
Audio stays in memory and is uploaded only to the fixed HTTPS OpenAI transcription
endpoint by the worker. Abort cancels the upload. No raw key read API exists.

Secrets are signed independently of workspace resource operations, exchanged only
after Iroh peer identity and every selected workspace membership are verified,
and encrypted independently by each receiving device. A non-extractable AES-GCM
key in IndexedDB protects ciphertext in Turso; authenticated metadata binds the
ciphertext to its workspace and revision. This protects a database export, not
malicious same-origin JavaScript or a compromised browser profile. Provider
failure responses and request headers are never forwarded to the UI or logs.
Old encrypted revisions remain for merge/retry provenance and continue to be
shared with authorized workspace members, including devices enrolled later.
Removal prevents the application from selecting that connection or resurrecting
it by replay, but does not delete historical key values or revoke them at OpenAI.
Revoke the provider key at OpenAI to prevent its use outside this application.


The browser profile retains both Turso data and the device key. The transport
and signing seed is encrypted with the same separate device key, with versioned
authenticated data bound to the database name and identity slot. A legacy seed
migrates before any endpoint starts, preserving its exact device identity. Every
startup checkpoints and truncates superseded WAL frames, then scans the actual
current database and WAL files for the original raw/hex seed. Residual plaintext
or unreadable files fail startup before networking or credential use. This is
verified current-file cleanup, not secure erasure of historical backups.
Encrypted-identity profiles require this upgraded build; no downgrade support is
claimed. A previously copied plaintext identity or historical backup remains a
previously disclosed device key; migration cannot revoke such a copy.

The worker retains only ciphertext and public, key-free revision metadata. It
decrypts selected records transiently for verified synchronization and retries,
or the exact active record for a provider request. JavaScript strings cannot be
reliably zeroized, and this does not promise protection against a live process
memory inspection.

Clearing either Turso data or the separate device key is data loss. Missing/corrupt key material fails closed; startup never silently
replaces a key needed by retained ciphertext. A retained device key with a
missing database identity also fails closed, rather than generating a different
device identity. An interrupted first initialization after key persistence is
conservatively treated the same way. Transport has no credential cache
or permanent relay authority. Foreground/reconnect synchronization reconciles
separate connection fingerprints even when resource operations are unchanged.

Connection synchronization probes a scope-free capability before sharing any
secret records. Older note-only peers remain usable for notes and report
`upgrade_required` through `connectionPeers`; their generic workspace sync result
does not claim connection delivery. Only a rejection of that initial capability
probe means unsupported. Rejected scoped summaries/transfers remain errors. Device
connection observations reset to unknown at restart and after local changes.
The separate application-surface variants also require peers upgraded to that
resource schema; connection capability negotiation does not backport surfaces.

Pass `locale: ''` for language auto-detection (the worker omits the provider
language field). Explicit locale only applies when the user selected one.
