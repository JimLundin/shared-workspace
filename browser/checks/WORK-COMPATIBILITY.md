# Work protocol compatibility check

`work-compatibility.mjs` compares the current host with Git commit
`05ab68532fc291dffe523543c5b1642f81111b13`. It copies current sources and the
current WASM package into a temporary tree, archives the older sources into a
separate tree, and rebuilds the older Domain/WASM. The working package is never
replaced.

The check requires the repository's Node dependencies, Playwright Chromium,
Rust's `wasm32-unknown-unknown` target, Clang, and wasm-bindgen 0.2.128. From the
repository root:

```sh
node browser/checks/work-compatibility.mjs \
  --wasm-bindgen /path/to/wasm-bindgen \
  --evidence /path/to/new-evidence-directory
```

An optional `--target-cache /path/to/cargo-target` copies cached dependencies into
the temporary target. It never builds into the supplied cache. The runner removes
all copied release artifacts and fingerprints for the host crate before building
the legacy code; this avoids accepting newer artifacts because an archived source
has an older timestamp. Set target-specific Clang/archiver environment variables
as required by the local Rust toolchain.

Three independent persistent Chromium profiles use real Turso OPFS and Iroh
connections through the configured default relays. The cases cover:

- An older client joining a new host's note-only workspace and synchronizing
  notes in both directions.
- An older client being refused a Work workspace before a grant is appended,
  workspace membership changes, or a Work operation crosses the transport.
- A supported peer successfully using that same invitation, proving that the
  refused attempt did not consume it.
- An existing older member being refused synchronization after Work is added to
  its previously compatible workspace.
- Supported peers exchanging Work, and a new client falling back to the older
  invitation format when joining an older note-only inviter.

A dedicated worker invokes production `HostOwner`, `BrowserStore`, Domain, and
Iroh. Transparent wrappers around the real endpoint record only request/reply
types, field names, and operation counts. They forward the original bytes without
modification. This distinguishes an actual refusal before transfer from an old
decoder receiving unsupported data and failing later. The production facade and
renderer are not exercised by this fixture.

The evidence directory is created fresh and contains `report.json`, build logs,
source/WASM/asset hashes, boolean assertions, and transport metadata. It contains
no invitation strings, identity secrets, API keys, database exports, or business
content. Provider requests are blocked and counted. Browser contexts, the local
HTTP server, profiles, source copies, and copied build cache are cleaned up.

The report must say `passed: true`, with unchanged source/capture hashes and
successful cleanup, before it counts as compatibility evidence. This is desktop
engine evidence, not a physical iPhone result. A refused legacy synchronization
prevents unsupported outgoing Work; it does not establish rollback of otherwise
authorized incoming note history.
