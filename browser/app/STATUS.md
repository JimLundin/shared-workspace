# Browser app — FROZEN, READY FOR ROOT PUBLICATION

ROOT COMMIT/PUSH CONFIRMATION 2026-10-25T15:10:38.680539+00:00: UI/app code is FROZEN. No further edits planned. Browser unit 7 PASS; native unit 19 PASS; native browser 5 PASS; real production UI 11 PASS; update/recovery 5 PASS. Current tested artifact hash is recorded below. Ready for root commit/push and Pages CI.

Final UI source/evidence complete, 2026-10-25. Rebuilt after root's final
transport freeze notice. Exact frozen output matches passing evidence. No app
edits remain. Outgoing MCP targets an unavailable old endpoint; this shared file
is the handoff. Root owns publication, Pages/CI and Windows packaging/deployment.

## Delivered

- Existing responsive canvas mounted on real BrowserHost/OPFS/Iroh. No seeded
  work, fixtures, fallback persistence or native bridge/plugin calls in web app.
- One generated COI/offline asset worker; startup reload preserves invitation
  fragment, then clears URL and prefills explicit Join. URL QR + copy/paste.
- Explicit browser speech, editable transcript + Run, stable captured address,
  typing fallback. Optional install/persistence requests and foreground resume.
- Updates wait for user; previous assets remain available for older clients.
  Failed host close offers explicit Reload and recover, not disposed controls.
- Root-approved shared changes: native/ui/src/app.ts, voice.ts and new
  voice-session.ts. Native adapters preserved; Host/protocol unchanged here.

## Final evidence and counts

- browser/app TypeScript/build and 7 unit tests pass.
- native/ui TypeScript, 19 unit + 5 browser regressions pass.
- evidence/production-ui.json: 11 real production UI assertions PASS. Includes
  empty startup without server isolation headers, durable UI saves, explicit
  cross-profile pairing, two-way edits, stable phone command address, stale
  editor/reconciliation, offline process restart/save and reconnect delivery.
- evidence/update.json: 5 update assertions PASS. Waiting/deferring update
  preserves edits; explicit reload reopens work; old client asset recovery;
  withheld real close acknowledgement exposes reload recovery and retains work.
- Update test reload checks wait for DOMContentLoaded before reading the new UI.
- npm run test:built runs both scripts. Corrected-binary UI run and focused
  update rerun pass; final rebuild was byte-identical to those tested assets.
- Frozen WASM: dfaaa3616cb9a49ffe4f9149e23a60450bc1f62285f506c40eb55ddfeb94d1dc
- dist/sw.js SHA-256: 303cbfed10e1253cbb205089a2ba9be6f862102214f762e5274deb8b0dbcd1b2
  (worker version hashes every asset and worker template).
- 16 output files, 18,191,081 raw bytes, mainly two WASM modules.
- Public failures are generic/category only. Ignored diagnostics redact pairing
  capabilities. Screenshots: artifacts/desktop.png and artifacts/phone.png.
- All app test servers/processes closed. git diff --check passed.

## Build/hosting contract

Build browser/host-wasm; npm ci in browser/host, native/ui, browser/app. Run app
check, test, build, test:built. Default production base /shared-workspace/
(override WORKSPACE_BASE). Output browser/app/dist includes all worker/WASM
assets, generated sw.js and checked-in icons. No separate asset copy/app server.
Root's browser.yml matches this contract.

## Backend requests and remaining scope

BACKEND-REQUESTS.md requests addressed durable task intake, capability discovery
and fenced execution ownership, causal workflow state, revision-bound approvals,
addressable results, acknowledged native handoffs and honest cancellation and
uncertain effects. These are requests to the runtime owner, not implemented Host
capabilities. Next bounded experiment: selected notes -> real draft result shared
between phone and desktop.

RESEARCH.md cites primary WebKit/W3C/Chrome/Web Speech sources. README.md explains
flows/build/evidence. Physical iPhone/Safari, Home Screen identity continuity and
microphone recognition are not established. No continuous phone background
execution claim. Current delivery supports shared notes/workspaces; full agent
execution and native application surfaces remain separate. Root handles final
backend checks, publication and Windows deployment; this lane did not publish.
