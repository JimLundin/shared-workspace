# Voice, connections and application surfaces

The browser release adds a primary microphone control, System/Light/Dark appearance,
workspace-scoped OpenAI transcription, code documents, and application/document
links. It retains actual Turso Database and authenticated Iroh exchange between
equal browser peers. No Python or Node runtime is installed on participating devices.

## Verified behavior

| Boundary | Evidence |
| --- | --- |
| Credentials and existing identities | [33 Chromium assertions](../browser/checks/evidence/connections-chromium.json), including migration from the previous built application, selected-workspace exchange, old-client compatibility, retries, conflicts, removal and restart |
| WebKit storage and credential behavior | [26 assertions](../browser/checks/evidence/connections-webkit.json), including missing keys/data, truncated encrypted identities and restoration of the same original identity |
| Portable legacy migration | [4 assertions](../browser/checks/evidence/connection-migration.json) using an explicitly synthetic legacy-format database |
| Lost migration commit reply | [5 assertions](../browser/checks/evidence/connection-migration-recovery.json): refuse uncertain admission, then reopen the same identity and verify current database/WAL bytes |
| Application revisions | [34 Chromium/WebKit assertions](../browser/checks/evidence/surfaces.json): code and Office links, peer edits, reopen, concurrent branches, explicit resolution and exact retry |
| Settings and provider invariants | [17 intended mutation kills](../browser/checks/evidence/connection-mutations.json), plus 46 host unit tests |
| Presentation | 36 UI unit tests and 26 browser journeys; short-phone transcript/save layout, editor-selection dictation, retained drafts, themes, setup continuation and scoped sharing |
| Static app and updates | [Production UI](../browser/app/evidence/production-ui.json) and [update recovery](../browser/app/evidence/update.json), including real joining, bidirectional edits and unfinished credential writes |

The shared Rust domain passes 22 native core tests, 12 WASM library tests, strict
clippy and formatting. Seven targeted surface/schema mutants fail their intended
assertions. The provider recorder passes 13 lifecycle tests and eight behavioral
mutants. These suites exercise distinct boundaries; their counts should not be
presented as one count of independent end-to-end scenarios.

## What these results do not establish

Physical iPhone provider recording remains to be checked. Chromium captured actual
MediaRecorder audio using a synthetic microphone. An invalid-key request reached
OpenAI and returned a readable CORS 401; it was not a successful authenticated
transcription. The user must configure a direct OpenAI key in private app settings.
The existing work-gateway credential is not sent to OpenAI. Linux WebKit lacks
MediaRecorder in the tested build and does not prove iPhone microphone behavior.

CodeMirror is an actual editor inside the workspace. Office and vscode.dev block
ordinary iframe embedding, so those cards open service/document links externally.
Their files, permissions and account cookies stay with the vendor. Saving a link
does not copy a document or synchronize unsaved vendor edits. The retained
[embedding probe](application-embedding/README.md) documents the separate diagrams
experiment without claiming that diagram contents synchronize in this release.

Credential removals are tombstones. Historical keys remain in shared history,
including for future authorized members; revocation at OpenAI is separate. Current
database/WAL scans demonstrate removal of superseded plaintext identity bytes,
not forensic erasure of old backups or protection from malicious same-origin code.
See the [connection contract](../browser/host/CONNECTIONS-CONTRACT.md).

Unsaved drafts remain local memory. Browser suspension can stop recording and peer
connections, and exchange requires peers to overlap online. This release does not
claim background iPhone execution, a general agent executor, a terminal, or control
of remote applications.
