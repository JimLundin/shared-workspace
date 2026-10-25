# Embedded workspace storage

The native workspace host uses **Turso Database 0.8.3**, the embedded Rust engine
from `tursodatabase/turso`. It does not use libSQL, `rusqlite`, or Turso's hosted
synchronization service. `native/core/Cargo.toml` pins the Rust `turso` crate and
disables its default allocator/full-text-search features. Iroh transports the
application's signed workspace records between peers.

Primary references:

- [Turso 0.8.3 crate](https://crates.io/crates/turso/0.8.3) and
  [its Rust API](https://docs.rs/turso/0.8.3/turso/).
- [Turso Database source](https://github.com/tursodatabase/turso), which identifies
  this as the embedded Rust engine compatible with SQLite.
- [Turso data types](https://docs.turso.tech/sql-reference/data-types): ordinary
  tables use dynamic affinity; `STRICT` tables enforce supported base types.
  User-defined types are an additional experimental feature, not enabled here.
- [`Builder`](https://docs.rs/turso/0.8.3/turso/struct.Builder.html) and
  [`Connection`](https://docs.rs/turso/0.8.3/turso/struct.Connection.html): local
  database creation, parameterized statements and transaction operations.

## Typed local owner

`Store` owns one Turso connection behind an asynchronous mutex and an exclusive
process lock on the application data directory. Its tables are `STRICT`:

| Table | Durable meaning |
| --- | --- |
| `operations` | Original signed operations indexed by operation and workspace ID. |
| `requests` | Original local command, committed together with its operation. |
| `peers` | Known device contacts; contacts alone confer no workspace rights. |
| `invitations` | Hashed invitation capability, selected workspaces, expiry and accepted device. |

Structured payloads remain typed Rust variants serialized into TEXT. `STRICT`
does not type-check arbitrary JSON: every retained payload is parsed with unknown
fields rejected and validated before publication. Device IDs, bounded strings,
resource identities, signatures, causal parents and workspace membership are
validated explicitly. The SQL INTEGER expiry column adds an independent engine
constraint. The public Turso contract test rejects nonnumeric text in an INTEGER
column and reads back a valid integer.

The owner requests `PRAGMA synchronous = FULL`. Operations and their local request
receipt commit together. Pure command preparation runs before any transaction:
invalid titles, absent workspaces and other definitive domain failures return a
`rejected` receipt, so the UI can retain and correct the input. A transaction or
commit failure remains an uncertain error and must not be relabelled rejection.
Import batches and invitation acceptance/grants also commit atomically. A caller dropping its future does not cancel the Store-owned
mutation task between commit and cache publication. Reopening validates retained
state before returning a snapshot; a saved request without its matching signed
operation is rejected.

Validated operations, projections, contacts and per-workspace content digests
are cached under this same owner. An unchanged snapshot does not reread SQL,
reverify signatures or reconstruct ancestry. A content digest covers sorted full
signed operations, including their payloads. Equal IDs/counts cannot conceal
changed content. Ordinary idle peer checks exchange these digests rather than
full document history.

## Shared history and conflicts

Each operation is signed by its device identity over a versioned domain and a
canonical serialized payload. Global workspace parents establish causal history.
Resource edits independently name the observed resource revision heads. Two
offline branches remain two heads; an explicit revision names both to reconcile
them. Previous revisions are retained. No wall clock or device priority chooses
a text winner.

The creating device establishes a historical workspace origin. Every authorized
member can grant membership; the creator is not a permanent operational owner.
A new device may carry an existing member's signed grant to another peer while
the inviter is offline. Import requires an already-enrolled workspace origin,
valid signed causal history, and authenticated sender membership in that history.
New workspace origins enter only through explicit invitation bootstrap.

A placement refers to a resource and its source workspace; it contains no copied
protected title/content. Exporting one workspace does not export a referenced
resource from an unselected source workspace. Removing a placement is an
observed-remove operation and does not delete the resource. Local focus and
window geometry are not workspace operations.

## Limits of this first host

The retained application history is limited to 4,096 operations and 7 MiB of
serialized signed records; each document revision is at most 128 KiB. Commands
that exceed the limit fail explicitly. There is no silent truncation, history
compaction or general semantic text merge. Later pagination/compaction must
preserve signature, authorization and conflict semantics.

Membership grants currently have no revocation operation. Deleting a contact
would not revoke its signed membership. Device keys are retained in the
application's private data directory, with restrictive Unix file permissions;
Windows inherits the user application-directory ACL. This implementation does
not claim Keychain/DPAPI storage or database-level encryption. The iOS sandbox
and platform build/signing checks are separate from the Linux engine evidence.

This store is for the new native workspace data. It does not migrate or take
over the existing Python work journal, microphone, task scheduler, credentials,
or external application effects.
