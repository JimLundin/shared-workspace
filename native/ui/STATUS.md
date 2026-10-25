# Frontend checkpoint ready for native integration

`npm ci && npm run build` is ready. Output: native/ui/dist (71.15kB JS / 12.73kB
CSS before gzip; no framework). Current validation: **19 unit tests, 5 browser
checks, 3 targeted mutation checks passed**. Production output contains no test
fixture data. Root owns actual app builds, native integration and deployment.

Implemented exact protocol including `network:unavailable` (local editing stays
available), and matching `rejected` no-commit receipts (editor retained and
correctable). Unknown receipt/transport failures keep exact in-session command
identity for retry. Confirmed receipt identity checked; stale draft heads cannot
silently adopt later revisions. Source state is exclusively native snapshots.

Native voice final snake_case Channel schema and stop/cancel {session_id} are
used. Captured context and late callback fencing tested. Windows focuses input
before native OS dictation; no claimed app microphone control for that mode.
Scanner uses host_capabilities.pairing_camera and official plugin2.5.3. Local
QR invitations retain the original selected workspace list, even if checkbox
selection later changes. Scan results fill the ticket only; explicit Join is
required. Late/cancelled scan results cannot replace another input. No tickets
in logs or persistent browser storage.

Browser checks use a declared public Host fixture plus the real missing-bridge
path. These are not native sync/speech/camera or iPhone-hardware evidence. Mobile
CSS covers safe areas, dynamic viewport, 16px inputs, native scrolling and
capability-gated hover; hardware verification stays with root/shell.

In-session edit/input buffers and uncertain UI retries are memory-only; only
presentation preferences use localStorage. Confirmed work is persisted by the
native core. This first frontend does not add a second durable UI command journal.
README documents the limit explicitly.

No further feature work planned. No Python/prior sketch edits or independent
Windows deployment. Dev/test HTTP was loopback-only and Playwright stops its
server. Outgoing MCP remains unavailable; this is the shared completion handoff.

Root documentation note: core/src/model.rs now includes Rejected and UI accepts it;
PROTOCOL.md receipt examples still need that additive variant recorded.
