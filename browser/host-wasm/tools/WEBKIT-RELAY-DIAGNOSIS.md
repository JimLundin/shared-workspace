# WebKit joining timeout: relay hostname form

The real BrowserHost join failed after 30 seconds in WebKit 27.2 while a
Chromium 156 inviter remained active and visible. Host startup, OPFS, Web Locks
and browser isolation succeeded. Removing note content did not change the
failure. Removing the database, domain and invitation code narrowed it to
`BrowserEndpoint.address()` waiting for Iroh to come online; no peer request
had started. The same code failed on both the main thread and a dedicated
worker, while Chromium completed the address step in about 1.2 seconds.

The controlled raw-WebSocket comparison used Iroh's real `/relay` endpoint and
the `iroh-relay-v2`/`iroh-relay-v1` subprotocols. Chromium opened both spellings.
WebKit failed with `euc1-1.relay.n0.iroh.link.` and opened
`euc1-1.relay.n0.iroh.link` in about 120 ms. Repeating under COOP/COEP gave the
same result. No certificate exceptions or browser security overrides were used.
This establishes the hostname compatibility problem; the generic browser error
alone does not identify which internal TLS/URL check rejected the dotted form.

`browser_relay.rs` removes one terminal dot from DNS hostnames in the existing
Iroh production relay configuration and incoming peer relay hints. It preserves
the peer identity, IP/custom locators, relay configuration, scheme, port, path,
query and fragment. The incoming path matters for invitations created by an
older desktop build. Native hosts and the original probe remain unchanged.

After this change, the minimal WebKit endpoint became online in 1.16 seconds and
both engines exchanged authenticated messages. The full BrowserHost regression
then joined in about 1.4 seconds and recovered an accepted legacy invitation
carrying the old dotted relay spelling. Full host and lifecycle evidence belongs
to `browser/checks`; this folder retains the smaller transport evidence.

## Retained evidence

- [Minimal endpoint failure](../evidence/webkit-online-red.json)
- [Controlled WebSocket comparison](../evidence/relay-websocket-compatibility.json)
- [Minimal endpoint and exchange success](../evidence/webkit-online-green.json)
- [Normalization reversion mutation](../evidence/relay-mutation.json)

The reports contain stage names, elapsed times, public relay hostnames and
source/artifact hashes. They contain no invitation tokens, private seeds,
database records or browser profiles.

## Reproduce

The pinned Playwright development dependency is installed under `browser/checks`.
Linux WebKit requires a supported runtime; the official Ubuntu 24 image was
used because the workstation runs Ubuntu 20.04. Its digest was
`sha256:47b510dd9230bf0b9bfc9b20102241e577ea7a99334ab4c3ca19f19770e9b8ea`.

```sh
mkdir -p /tmp/iroh-engine-diagnosis
docker run --rm \
  --mount type=bind,source="$PWD",target=/work,readonly \
  --mount type=bind,source=/tmp/iroh-engine-diagnosis,target=/evidence \
  --workdir /work mcr.microsoft.com/playwright:v1.64.1-noble \
  node browser/host-wasm/tools/check-engine-stages.mjs \
  --mode worker --evidence /evidence/worker
```

`--mode main` tests the same module outside a worker. `check-relay-websocket.mjs`
performs the hostname-only comparison. `mutate-browser-relay.mjs` builds a
disposable variant that disables normalization and requires the real WebKit
online stage to time out while Chromium still succeeds. The live source and
production assets are checked for unchanged bytes.

`check-public-join.mjs` diagnoses the currently deployed website through its UI;
it follows a displayed HTTPS invitation directly, or wraps a raw token when
needed. It scrubs both legacy and compact tokens. It cannot validate an
unpublished local fix. Any public-site startup failure before the navigation
appears must be investigated separately from this joining timeout.

Linux WebKit is an actual second browser engine, but it is not physical iPhone
Safari. Device verification after publication remains necessary.
