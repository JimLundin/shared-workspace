# Meeting work acceptance

`work-acceptance.mjs` builds the dedicated `work-fixture.html` entry in a temporary
directory. The fixture imports the production `BrowserHost`, `WorkspaceSession`,
`RetainedInputJournal`, and `WorkController`. It does not use a fake database,
domain, transport, or controller. Each browser has its own persistent profile,
real Turso OPFS database, device identity, and Iroh endpoint.

Build the current WASM host and install the pinned browser dependencies first,
as described in [the host guide](../host/README.md). Use the project's supported
Node version and Playwright Chromium/WebKit with their system dependencies:

```sh
node browser/checks/work-acceptance.mjs \
  --inviter chromium --joiner webkit --evidence /tmp/meeting-work-cw
node browser/checks/work-acceptance.mjs \
  --inviter webkit --joiner chromium --evidence /tmp/meeting-work-wc
```

Evidence directories must be new. Both directions matter: the origin performs
provider preparation and recovery, while the member contributes independent
offline corrections. The static server binds only to `127.0.0.1` and supplies the
isolation headers needed by the real worker. No public deployment is involved.

The checks cover:

- Retained source notes, requested work, and private unsubmitted input across a
  full browser restart, with no automatic preparation.
- Selected workspace enrollment, peer receipt of the shared connection, and
  exclusion of private workspace content and the local draft journal.
- An explicitly started, actor-bound preparation interrupted by closing its
  browser; another member's controller and worker cannot resume that run.
- Explicit origin recovery through the worker-owned generator, with a transcript
  edit while generation retains the original revision and citations.
- One durable publication operation creating notes, unsent follow-up text, and
  the work revision together, followed by real peer delivery.
- A lost publication reply, retained unknown outcome, full process restart, and
  an identical operation/payload retry returning a replay without duplicate output.
- Offline owner/deadline corrections, retained conflicting branches, stale-write
  refusal, and explicit reconciliation of all observed heads with user evidence.
- An ordinary human edit to generated follow-up text, preserving the original
  publication reference while current content and full history reach the peer.

All OpenAI requests are intercepted by the browser context. The runner supplies
a synthetic credential and a bounded, explicitly synthetic Responses API payload;
provider requests are fulfilled or aborted, never forwarded. This exercises the
production worker's capture, parsing, provenance checks, and publication path. It
does not establish authenticated provider behavior or semantic extraction quality.

The reply-loss fault is confined to the fixture's public Host wrapper. It first
awaits the real `BrowserHost.command` Applied receipt, then withholds it from the
controller. Thus the check proves recovery after a real durable commit, not a
crash inside a transaction. Command equality compares every structural value;
JSON object field ordering after a typed decoder is not an operation identity.

Reports contain named assertions, browser versions, bounded outcomes, source and
asset hashes, and cleanup results. They include `native/core/src/work.rs`, the
executed session/controller modules, host sources, and generated WASM/JS. The
active application renderer and its separate UI fixture are outside this check's
source boundary. A changed source, failed build/host operation, or unavailable
relay is a failed run, never a successful behavior assertion. Temporary profiles,
database files, builds, and listeners are removed; invitation tickets and raw
workspace/provider payloads are not exported.

Supported work-capability negotiation is exercised by the upgraded peers. Old-host
upgrade refusal, shipped UI behavior, real OpenAI credentials, physical iPhone
interaction, Outlook drafts, and mail delivery require separate evidence.
