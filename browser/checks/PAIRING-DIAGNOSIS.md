# iPhone pairing timeout investigation

The reproduced WebKit Join timeout is resolved in the focused desktop engine
tests: Chromium → WebKit and WebKit → Chromium each pass **17 assertions**,
including legacy invitations, synchronization and browser restart. The original
Chromium → WebKit Join timed out after 30,008 ms; the corrected build completes
it in 1,364 ms. Physical iPhone verification and the deployed PWA startup path
remain separate results.

The user reported the phone timeout while the laptop invitation remained
visible. The reproduction retained an active network and a visible document on
the inviter. Transport investigation narrowed the failure to WebKit rejecting
relay hostnames ending with a dot. Browser relay routes and old invitation
address hints are now normalized by the transport implementation. Its
[WebSocket comparison](../host-wasm/evidence/relay-websocket-compatibility.json)
and [normalization-reversion mutation](../host-wasm/evidence/relay-mutation.json)
record the independent transport evidence.

## Feedback loop

The local machine is Ubuntu 20.04, which does not provide the supported WebKit
runtime. The official `mcr.microsoft.com/playwright:v1.64.1-noble` container runs
WebKit 27.2 and Chromium 156. Source is mounted read-only; reports go to a separate
writable mount. The test builds the same BrowserHost facade and Turso/Iroh WASM
assets as the browser acceptance, with a static server bound only to container
loopback. It does not navigate to or mutate a user's existing workspace.

```sh
mkdir -p /tmp/workspace-phone-diagnosis
docker run --rm \
  --mount type=bind,source=/home/jim/toolkit-hosts,target=/work,readonly \
  --mount type=bind,source=/tmp/workspace-phone-diagnosis,target=/evidence \
  --workdir /work mcr.microsoft.com/playwright:v1.64.1-noble \
  node browser/checks/pairing-diagnose.mjs \
  --laptop-engine chromium --phone-engine webkit \
  --legacy-retry --exercise-sync --require-compact \
  --evidence /evidence/chromium-webkit
```

Evidence directories must be new. `--empty-workspace` removes the note for the
minimal Join reproduction. `--legacy-retry` reconstructs the old JSON ticket
format in memory, including old dotted relay hints, and checks an idempotent
same-device retry. `--exercise-sync` checks a receiving-engine edit, offline
conflict and reconciliation, then a complete receiving-browser restart with
retained identity/history and a new exchange. Reverse the two engine arguments
to test WebKit as inviter. `--laptop-state paused` is an explicit offline control;
it does not establish that the reported visible laptop was backgrounded.
`--mode measure` opens only the inviter and records invitation size, using the
production HTTPS base, a UUID workspace ID and the UI's QR encoder/correction
level. The harness uses the installed `browser/app` QR dependency and Node's
TypeScript support for the public invitation decoder.

The exported observations contain stage timings, browser capability booleans,
network-state variants, safe error categories and relay traffic counts/origins.
They contain no invitation text, QR image, device seed, raw payload, database,
profile or unrestricted browser error text. Worker WebSocket events may be
absent in WebKit; a zero event count does not prove there was no connection
attempt. Browser profiles, temporary bundles and the test server are cleaned up.

## Before-fix evidence

- [Chromium → WebKit with a note](evidence/pairing-before/chromium-webkit.json):
  Join timed out after **30,008 ms**; both hosts opened successfully, with the
  inviter active and visible.
- [Minimized empty workspace](evidence/pairing-before/chromium-webkit-empty.json):
  Join timed out after **30,014 ms**. Document payload is not needed to reproduce
  the failure. The report also flags concurrent source changes from compact
  invitation work; this exploratory observation is not a frozen release baseline.
- A separate run by the transport investigator observed the same timeout after
  30,011 ms. The deployed-PWA startup check is a separate path and must not be
  conflated with this reproduced Join failure.

The [original invitation measurement](evidence/pairing-before/invitation-size.json)
is **541 URL bytes**, including a **488-byte encoded ticket**. The UI's level-M
QR has **89 × 89 modules**, version 18, for one UUID workspace. This establishes
the original density; it does not explain the Join timeout or show that scanning
failed.

## Corrected-build evidence

| Focused run | First Join | Legacy retry | Result |
| --- | --- | --- | --- |
| [Chromium → WebKit](evidence/pairing-after/chromium-webkit-fixed.json) | 1,364 ms | 196 ms | 17 assertions passed |
| [WebKit → Chromium](evidence/pairing-after/webkit-chromium-fixed.json) | 1,358 ms | 182 ms | 17 assertions passed |
| [Minimized empty workspace](evidence/pairing-after/chromium-webkit-empty-fixed.json) | 1,373 ms | Not attempted | 4 assertions passed |
| [Explicitly paused inviter](evidence/pairing-after/paused-control.json) | Timeout at 31,144 ms | Not attempted | Expected offline control failure |

Both successful runs observe the actual public `JoinStage` callbacks in order:
`checking_invitation`, `connecting_relay`, `contacting_device`, `saving_workspace`.
The test page records the number received at the instant the final Host promise
resolves. The returned snapshot must then contain the selected workspace and
exact note; the inviter must confirm membership. This verifies callback ordering
and committed result separately. The paused control reaches `contacting_device`
and never reports `saving_workspace`, preserving the last observed stage on the
timeout. It does not establish backgrounding as the user's reported cause.
This control predates a strict optional-property typing correction in the host
facade; its recorded hashes identify that earlier source. Both 17-assertion
flows were rerun after the correction, and all source hashes for those flows
and the minimized successful run match the final reviewed implementation.

The actual compact invitation is **287 URL bytes**, with a **234-byte encoded
ticket**, producing a level-M QR of **65 × 65 modules**, version 12. The
`--require-compact` regression bounds this one-UUID-workspace, standard-relay
fixture at 300 URL bytes and 69 modules. No invitation or QR image is exported.
All corrected-run sources and generated WASM assets remained unchanged during
their runs; profiles, browsers and static servers were cleaned up.

Transport diagnosis and changes belong to the transport implementation owner.
No production host/API implementation changes are made by this harness. The
public progress callback is observed through the agreed test-only page seam.
Physical iPhone verification remains separate from these desktop WebKit results.
