# Browser host transport build and check

The parent crate exposes `Domain` and `BrowserEndpoint` through one generated
`pkg/browser_workspace_host.js` module. `build.sh` requires the same user-local
Rust/Wasm/Clang and wasm-bindgen 0.2.128 tools as
[`iroh-probe`](../../iroh-probe/README.md); it reuses that dependency cache.
Set `WASM_BINDGEN`, `CC_wasm32_unknown_unknown`, and
`AR_wasm32_unknown_unknown` when the tools are outside PATH. The generated
assets need no native bridge in the browser.

```js
const endpoint = await BrowserEndpoint.start(seedHex, async (authenticatedPeerId, payload) => {
  // The host validates invitation/membership and returns serialized response text.
  return host.handle(authenticatedPeerId, payload);
});
endpoint.id();
const publicAddress = await endpoint.address();
const canonical = BrowserEndpoint.validate_address(otherAddress, expectedPeerId);
const { peer_id, payload } = JSON.parse(await endpoint.request(canonical, requestJson));
await endpoint.close();
endpoint.free();
```

The host must persist the random 32-byte seed before using it here. Passing that
same seed to `Domain` gives signatures and Iroh the same identity. The transport
does not inspect claimed author IDs or assign workspace authority; the callback
receives the actual authenticated connection identity first. Unknown invited
peers can reach the host's enrollment handler.

Messages contain 1 byte to 8 MiB of UTF-8. Public address JSON is bounded to 4
KiB and 16 locators. There are at most 16 inbound handlers, each with a 30-second
deadline covering handshake, request, callback, and reply. Requests use the
separate `shared-workspace/browser/1` ALPN; native wire compatibility is not
claimed.

`address`, `request`, and `close` return promises owning cloned endpoint state;
they retain no borrowed WebAssembly receiver across an await. `close` retires
the listener, incoming handlers, pending outgoing/address waiters and endpoint.
Offline address waits reject promptly as closed. An
already-invoked JavaScript promise cannot be cancelled by dropping its Rust
waiter. The durable owner must allow that transaction to finish, retain retry
identities, and treat a lost acknowledgement as uncertain. No storage is closed
or rolled back by this transport.

Run `node tools/check-transport.mjs` after building and installing the retained
probe's Playwright development dependency. This uses actual external Iroh WSS
relays and three isolated browser contexts under COOP/COEP. Only static assets
are supplied through Playwright, with no HTTP listener or transport fixture.
The check exercises the callback seam, shared signing/transport identity,
malformed/unauthorized callback rejection, address/message validation, same-seed
endpoint reopening, the incoming concurrency cap and shutdown. Its synthetic
callback is not a substitute for the full Turso host's durable invitation,
membership, restart and workspace-convergence acceptance tests.

`node tools/check-lifetime.mjs` loads the real module, switches its browser
context offline, starts an unresolved address lookup, and closes/frees the
endpoint. It requires a prompt closed rejection, including when the instance is
dropped before its address future starts. The pre-fix borrowed-object failure
is retained separately in `evidence/lifetime-red.json`.

`node tools/mutate-transport.mjs` compiles a disposable variant that passes a
payload's claimed identity to the callback. The real third browser then forges
the selected identity, so the rejection assertion must fail after both positive
exchanges succeeded. The live source and generated production assets remain
unchanged. The result is retained in `evidence/transport-mutation.json`.

`node tools/check-public-join.mjs --url https://jimlundin.github.io/shared-workspace/ --inviter chromium --joiner webkit --evidence /tmp/public-workspace-smoke`
checks the served production UI, explicit invitation acceptance, and note edits
in both directions over real Iroh. Run from `browser/host-wasm`, using the
matching Playwright browsers; the official `mcr.microsoft.com/playwright:v1.64.1-noble`
image supplies Linux WebKit 27.2. `--url` also accepts an existing permitted
loopback static origin for checking a build before publication.

Each participant uses a separate fresh temporary persistent profile, closed and
removed before the report is written. The check handles the invitation's
copy/paste disclosure and entry URL, and stops on either workspace navigation
or a handled boot failure. It exports redacted stage/transport counts and verdicts,
without screenshots, traces, invitations, keys, database files, or profiles.
The [startup comparison](../evidence/public-startup-comparison.json) explains why:
on the same public URL, WebKit's ephemeral context rejected OPFS `getDirectory()`
while its fresh persistent context opened successfully. Page and worker isolation
worked in both. This was a test-context failure, separate from the Iroh Join
failure, and is not a physical iPhone or Safari Private Browsing result.
