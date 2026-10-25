# Browser surface exchange acceptance

`surface-acceptance.mjs` builds the current `BrowserHost` acceptance entry into a
temporary directory, serves it on an ephemeral loopback port with the production
isolation headers, and opens two independent persistent browser profiles. It
uses real Turso OPFS storage, the generated shared Rust model, and Iroh peer
transport through the public host interface.

```sh
node browser/checks/surface-acceptance.mjs \
  --inviter chromium --joiner webkit \
  --evidence /tmp/workspace-surface-evidence
```

The default pair is Chromium → WebKit. Both flags accept `chromium` or `webkit`.
The generated `browser/host-wasm/pkg` must already match current source. Existing
dependencies in `browser/checks` and `browser/host` are used; the script does not
install anything or change the application's shared distribution directory.

For the pinned Playwright Linux engines:

```sh
mkdir -p /tmp/workspace-surface-evidence
docker run --rm --init --shm-size=1g \
  --mount type=bind,source=/home/jim/toolkit-hosts,target=/work,readonly \
  --mount type=bind,source=/tmp/workspace-surface-evidence,target=/evidence \
  --workdir /work \
  mcr.microsoft.com/playwright:v1.64.1-noble \
  node browser/checks/surface-acceptance.mjs \
  --inviter chromium --joiner webkit --evidence /evidence/result
```

The loopback listener stays inside the container; no port is published.
Persistent profiles are required because Playwright's ephemeral WebKit contexts
can reject OPFS independently of application behavior.

The check covers:

- A code document, a real public Office application URL, and an unchanged note
  crossing an explicit Join.
- A code edit from the joining peer and a changed Office target reaching the
  other peer with durable commit receipts.
- Full browser-process close/reopen on both sides, without an explicit host
  flush before closing, preserving identity, resource history and operation count.
- Separate offline code edits becoming two retained heads after reconnect.
- A stale edit returning a conflict without a commit; both branches surviving
  another browser-process restart.
- Explicit resolution reaching both peers, with all older revisions retained.
- An exact replay of a previously acknowledged code edit after reopen leaving
  the newer resolution untouched.

The report exports assertions, timings, engine versions, source and built-asset
hashes, and cleanup results. It contains no invitations, endpoint identities,
private keys, databases or browser profiles. Sources are hashed before and after
the run; source drift fails the check. Browser contexts, profiles and the
temporary build are removed before writing the final report.

This is a real browser-host and peer exchange check. UI picker/editor behavior
has separate acceptance tests. The Office URL is a shared target: this check
does not sign in, embed Office, edit a vendor document, or transfer vendor
permissions. Linux WebKit is not physical iPhone Safari verification.
