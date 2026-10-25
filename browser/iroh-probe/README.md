# Browser Iroh transport probe

This isolated experiment gives each open browser page its own **Iroh 1.4.1
WebAssembly endpoint**, fresh cryptographic identity and selected remote peer.
Either page can initiate a bounded UTF-8 request and verify the echoed bytes.
The receiver checks the authenticated connection identity before reading data.
No device is a permanent coordinator. This is a transport feasibility probe,
not the shared-workspace protocol or a durable agent runtime.

The retained [browser exchange report](evidence/browser-exchange.json) records
a passing real-relay run in three isolated Chromium contexts with COOP/COEP
enabled. The module was 5,153,622 bytes; linear memory buffers were about 1.8–2.0
MiB per context. The [focused mutation](evidence/identity-mutation.json) removed
the incoming identity check in a disposable copy: ordinary exchanges still
worked, but acceptance correctly failed when an unselected third peer got in.

## Build

Prerequisites: Rust with `wasm32-unknown-unknown`, a Clang build that supports
WebAssembly, an archiver, Node, and `wasm-bindgen-cli` **0.2.128**. Build tools
are development dependencies; the deployed page needs only the generated JS
and Wasm assets. Iroh's default features are disabled, with `tls-ring` enabled
explicitly for its TLS implementation.

```sh
rustup target add wasm32-unknown-unknown
# Install wasm-bindgen-cli 0.2.128 through its official release or Cargo.
bash build.sh
npm ci --ignore-scripts
npx playwright install chromium
npm run check
# Optional focused mutation; uses the same build-tool environment:
node mutate.mjs
```

`WASM_BINDGEN`, `CC_wasm32_unknown_unknown`, and `AR_wasm32_unknown_unknown`
can identify user-local tools. `PROBE_TARGET_DIR` changes the build cache path.
The build sets the official `getrandom_backend="wasm_js"` configuration.
`Cargo.lock` and `package-lock.json` pin the resolved dependencies. Generated
`pkg/` files are deliberately ignored; `build.sh` reproduces them.

The checker launches three isolated browser contexts. Playwright provides only
the static assets at reserved `https://iroh-probe.invalid`; it opens no HTTP
listener or tunnel. It never intercepts Iroh's actual external relay traffic.
The test supplies COOP/COEP headers and verifies `crossOriginIsolated`, matching
the isolation needed by the separate Turso browser storage experiment.
Peers A and B exchange test text in both directions; B then rejects the separate,
unselected identity C. The evidence report contains no private keys, enrollment
secrets, saved browser profile, or user work. The public relay origins, synthetic
check descriptions and binary hash are safe to retain.

For a hosted manual experiment, the static assets are `index.html`, `page.js`,
and `pkg/`. Hosting/deployment is owned by the parent project. Both people click
**Start**, exchange public addresses, choose **Select peer**, then send test
text. The address carries routing information and a public key, not a secret.
Selecting a peer here is a manual demonstration of authorization, not a complete
user-friendly enrollment protocol.

## JavaScript API

```js
import init, { BrowserPeer } from './pkg/browser_iroh_probe.js';
await init();
const peer = await BrowserPeer.start();
const id = peer.id();
const publicAddress = await peer.address();
peer.select_peer(otherPublicId);
const receipt = JSON.parse(await peer.send(otherPublicAddress, 'synthetic text'));
// receipt: { peer_id, text, bytes }
const incoming = JSON.parse(peer.take_events());
// Tagged events: received { peer_id, text }, rejected { peer_id }, failed { detail }
await peer.close();
peer.free();
```

Payloads are limited to 64 KiB, address JSON to 4 KiB, retained events to 32, and
individual exchanges to 30 seconds. Closing a page discards its identity and
state. There is no database or workspace replication in this probe. The Wasm
linear-memory observation is the current buffer length, not total browser memory
or a peak resident-memory measurement.

## Primary sources and boundaries

- [Iroh browser support](https://docs.iroh.computer/languages/wasm-browser):
  browser endpoints are supported through a custom `wasm-bindgen` wrapper;
  there is no official ready-made browser npm package. Browser traffic always
  uses a relay because browser code cannot send arbitrary UDP packets. Iroh
  encrypts peer traffic end to end; the relay routes it without owning workspace
  state or decrypting its contents.
- [Iroh 1.4.1 Cargo configuration](https://github.com/n0-computer/iroh/blob/v1.4.1/iroh/Cargo.toml):
  confirms the dedicated browser target dependencies and feature selection.
- [Official browser echo example](https://github.com/n0-computer/iroh-examples/tree/main/browser-echo):
  demonstrates browser-owned endpoints, incoming connections, bidirectional
  streams and JavaScript bindings. This probe pins Iroh 1.4.1 instead of copying
  the example's older dependency pins.
- [Iroh endpoint API](https://docs.rs/iroh/1.4.1/iroh/endpoint/index.html):
  connects to endpoint public identities and exposes authenticated `remote_id`.

This check cannot establish iPhone Safari behavior, device suspension/resume,
cross-network policy compatibility, workspace durability, revocation, application
control, or operation deduplication. An unavailable relay means browser peers
cannot exchange Iroh traffic. An offline or suspended page cannot keep processing
work. These constraints apply to the planned PWA even if its static shell can
launch offline. Native hosts can provide additional platform capabilities without
becoming the permanent authority.
