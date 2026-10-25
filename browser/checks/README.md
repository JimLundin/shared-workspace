# Browser host acceptance

The agreed seam is `BrowserHost.open` and its public command, snapshot, invitation,
synchronization and lifecycle methods. A test-only page exposes that facade;
it neither accesses database tables/worker internals nor adds a production global.
The same real Turso OPFS worker, signed domain and Iroh WASM transport run here as
in the browser application.

## Verified result

The [combined browser report](evidence/browser-host.json) passed **3 scenarios
and 42 assertions** on 25 October 2026. Ten persistent Chromium browser lifetimes
opened and closed cleanly. The run used real Iroh WASM connections through
`wss://euc1-1.relay.n0.iroh.link.` and real Turso OPFS databases. It covers selected
sharing, two-way work, offline conflicts and explicit reconciliation, replay and
stale-input protection, full browser restart, and third-member work while the
origin browser is stopped.

The [storage mutation report](evidence/storage-mutation.json) detected its one
declared fault at `browser-restart-retains-acknowledged-resources`, after the
required successful in-memory saves, duplicate-tab protection and retained
identity. The unmodified disposable copy passed first. The mutation report
hashes the passing combined report; source, generated WASM/JS, dependency locks,
fixture and built-asset hashes are retained. Production source remained
unchanged, and the disposable mutated source was restored.

All browser profiles were removed and all test listeners were closed. The
reports contain no private keys, invitation tickets, database files or raw logs.
This proves the integrated browser host in Chromium; physical iPhone/Safari,
production UI/voice behavior, deployment policy and storage eviction remain
separate checks.

| Public seam | Required observation | Outside this evidence |
| --- | --- | --- |
| Commands and snapshots | Durable notes, exact replay, stale revision rejection and retained conflicts | Direct database inspection or external application actions |
| Invite, join and sync | Selected sharing, symmetric contributions and a third member while the origin is stopped | A mocked relay, permanent authoritative host or device policy approval |
| Foreground and close/open | Offline edits and reopening the same browser profile preserve identity and acknowledged data | iPhone background execution or arbitrary storage eviction |
| A second tab's open | Existing ownership is rejected without stealing the first tab's WebLock | Cross-origin storage sharing |

Each device uses its own persistent Chromium profile and browser process. A
production-built test page is served by an ephemeral static server bound strictly
to `127.0.0.1`, with isolation headers. The server closes after the test. No LAN
listener, phone preview or public deployment is created. The browser treats this
loopback origin as a secure context; physical HTTPS hosting is separate evidence.
Playwright's virtual HTTPS response fulfillment (`--route`) aborted Turso's
nested worker request while identical assets worked through the real loopback
server, so acceptance uses the server instead of altering production workers.
Actual Iroh relay connections remain untouched. The transport environment must
permit those connections, and an unavailable relay is a setup/transport failure,
not a successful authorization rejection or mutation detection.

Profiles, database files, private keys and invitation tickets are never exported.
Evidence contains named assertions, bounded failure categories, source/build
hashes, public relay origins and browser versions. Targeted mutations run only
after an unchanged baseline is frozen, in a disposable source/build copy.

## Run the checks

Build the combined WASM host and install the browser host dependencies as described
in [the host guide](../host/README.md), then install this harness's dependencies:

```sh
npm ci --prefix browser/checks
node browser/checks/check.mjs --case all --evidence /tmp/browser-host-acceptance
node browser/checks/mutate.mjs \
  --baseline-report /tmp/browser-host-acceptance/report.json \
  --evidence /tmp/browser-host-mutations
```

Evidence directories must be new. `--case durability`, `--case pairing`, and
`--case offline` select individual behavioral slices; `--verbose` prints public
method names without arguments. Profile directories are deleted after closing
all browsers. Private diagnostic files remain outside the exported reports.

A synchronization request must include a fresh exchange after any older in-flight
pass. The offline case caught a real regression where a newly joined third host
returned an older successful pass without sharing its subsequent edit; that same
assertion is retained. The harness allows at most three passes for transient
unreachable/newly discovered contacts, and ultimately requires all expected
reachable peers to report a successful exchange. Only the deliberately stopped
origin is allowed to remain unreachable during third-member work. Confirmed
invitation rejection is checked against its specific public error, separately
from transport failures.

The focused mutation changes Turso `COMMIT` to `ROLLBACK` while the host still
publishes the prepared in-memory domain state. The browser must first acknowledge
and display the notes, reject a duplicate tab, continue in the original tab and
retain its device identity after a full restart. Only then may the named retained
resource assertion detect the mutation. A build/startup/transport failure cannot
count. The campaign first runs the unmodified copied boundary, mutates only its
disposable copy, rebuilds the page, and restores/verifies that copy and the live
source afterward. It does not rerun the native mutation campaign.
