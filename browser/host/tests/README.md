# Invitation codec checks

Run from the repository root with the project's supported Node version:

```sh
node --experimental-strip-types --test browser/host/tests/invitation.test.mjs
node browser/host/tests/mutate-invitation.mjs --report /tmp/toolkit-invitation-codec-mutations.json
```

These checks exercise the public `encodeTicket` / `decodeTicket` interface.
Independent binary fixtures represent untrusted compact inputs; they do not
rely on the encoder accepting malformed records. Coverage includes every peer
and nonce byte, exact selected scope, boundary lengths and expiry, Unicode name
preservation, malformed UTF-8/UTF-16, canonical base64url, trailing data, legacy
JSON tickets, ambiguous links and bounded routing payloads.

The mutation command first requires the complete codec baseline to pass, then
copies only the codec, shared validators and tests into disposable folders.
Seven deliberately broken variants must each fail its named test at its
intended assertion: truncated peer identity, replaced nonce, duplicate scope,
trailing bytes, imprecise expiry, malformed UTF-8 replacement, and base64 aliases.
A syntax/import failure, timeout, signal or unrelated assertion does not count.
The JSON report retains exact source/test/harness hashes and process outcomes;
the originals are compared again at the end. Temporary copies are removed.

This is targeted behavior validation, not a mutation-coverage percentage or a
network/pairing test. Routing variant validation remains with the native contact
validator. Expiry policy, single-use grants, authenticated-peer binding and
workspace authorization remain with the host and store; compact encoding does
not itself authenticate an invitation.

# Workspace connection checks

```sh
npm --prefix browser/host test
node browser/host/tests/mutate-connections.mjs --report /tmp/workspace-connections-mutants.json
CARGO_TARGET_DIR=/tmp/browser-host-domain-target cargo test --manifest-path browser/host-wasm/Cargo.toml --locked connection_signatures_require
```

Connection tests cover causal conflicts and explicit resolution, exact retries,
removal tombstones, selected scope, public metadata, actual WebCrypto encryption,
device identity binding and a provider HTTP adapter with injected responses.
Synthetic test credentials are the only key material used. Provider tests verify
captured workspace/version, fixed endpoint, no browser cookies/cache/redirects,
abort propagation, safe errors, and stale-response refusal.

Seventeen targeted mutations run only in disposable copies, each with its named
behavioral assertion. Setup errors, timeouts and unrelated failures do not count
as kills. Source/test/harness hashes and unchanged-source checks are recorded.
These are not network, Turso, iPhone or authenticated OpenAI acceptance tests;
those require separate browser/peer and device evidence. The Rust signature test
uses the actual retained domain identity and validates signer membership and
workspace/payload binding independently of transport authentication.

Identity storage tests exercise the checkpoint-before-read barrier, residual seeds
in either current physical file (including across stream boundaries), missing
files and fixed error handling. They use an injected file provider. Separate real
OPFS acceptance covers the pinned Turso checkpoint and restart behavior; these
unit tests alone do not prove a physical storage migration.
