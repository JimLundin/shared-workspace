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
