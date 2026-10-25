# Voice and application workspace — UI source frozen

UI SOURCE is frozen for root integration. No further source edits or builds will be
made by this lane without coordinating a reopen. DIST is an integration candidate,
not the final release snapshot: root still owns the backend identity-storage gate,
regenerated WASM, independent release validation, commit/publication, and mandatory
Windows Downloads/ZIP sync and reload. This lane has not committed or deployed.

Source: `/home/jim/toolkit-hosts`, baseline 241c02e.
- `native/ui/src/app.ts`: `45c0a0f322d49931cbd9c100fbfc675ad45770a82513df4ffffefe69538d60e8`
- `native/ui/src/connections-ui.ts`: `01b06cd07cde63521c2c2e0e41870ff6a7b3c52374c47f52be4099b5e546cd86`
- Latest local browser candidate: `index-bfYVcFOu.js`, `index-Dp2WxMqt.css`
- Latest local native candidate: `index-D3HlFX_o.js`, `index-BtkEL0Bz.css`
- Latest local SW: `14a49f2c7c5d26a3e8ca31d652451f287e829f90cf908be6708f2b0567a13086`

## Implemented interactions

| Before | After | Why |
| --- | --- | --- |
| Typing dominated capture | Wide Speak action, secondary keyboard icon | Dictation is primary |
| Microphone start opened a text field | Listening/processing, then editable transcript | Avoid opening the phone keyboard |
| Completed capture retained large recording chrome | Compact result, visible Save/Insert action, non-input focus | The next step fits a 390×667 phone |
| Workspace/status/options header | Workspace picker and Share icon | Routine work stays quiet |
| Light only | Persistent System, Light, Dark | Appearance belongs to this display |
| Only notes | Plus picker for code, Office, VS Code, diagrams and links | Work includes applications and documents |
| Dictation always created another note | Insert at captured editor field/selection | Continue editing the current material |

Free speech defaults to a clearly labelled note, without a Remember prefix. Known
navigation and preset application commands show their bounded action before use.
Open Word/VS Code can add their preset; existing named resources open first. Creating
an app through voice retains its exact operation through a lost receipt and retry.

Editor dictation captures a local draft identity, workspace, resource, field, value
and selection. One Insert into draft confirmation updates that draft. CodeMirror's
selection/setText API preserves the editor. A changed draft offers explicit append;
a replaced editor retains the transcript and offers a separate note. Speech-looking
commands inside editing are dictation, not application execution. New note/code/link
flows cannot overwrite a retained editor. All-work capture ignores stale focus.

Code documents use an actual CodeMirror editor and shared revision/conflict handling.
Office/VS Code/diagrams/document links open externally with a clear explanation.
No iframe embedding, vendor credentials, remote execution or general agent action
is invented. CodeMirror helper remains process_integration-owned; dependencies,
including direct @lezer/highlight, are installed in native/ui.

OpenAI settings use metadata-only reads; exact operation/key/heads retry; receipt
scope/correlation; attempt-bound review and save completion; and read/receipt fences.
A late receipt cannot replace an already observed newer removal. Pending key input
and uncertain writes block app updates. Ready settings offer Continue to voice.
Upgrade-required/unreachable peers appear only in these settings. Sharing discloses
saved workspace connections. Missing configuration never silently selects device
speech. Provider capture snapshots workspace/connection before permission awaits;
language is automatic. No raw stored-key read API or secret-bearing HTML attributes.

Errors remain actionable: unavailable networking shows Check connections; failed
rerecording preserves the previous transcript and visibly reports its error.
The startup theme observer is disposed after mount. App options live in the picker,
wait for the shared modal's history close, and restore picker focus after dismissal.

## Validation and evidence

- 36 shared UI unit tests pass, including eight settings race/retry/scope cases.
- 20 browser unit tests pass (provider tests belong to platform lane).
- 26 fixture journeys pass, including short-phone result bounds, non-input result
  focus, selected code dictation, retained/replaced drafts, uncertain app creation,
  missing provider, themes/OS changes and failed rerecording.
- Eight targeted behavioral mutations fail their intended assertions; report is
  `native/ui/test-results/mutations.json`.
- Strict native/browser builds and `git diff --check` pass.
- Production integration candidate passed 13 real-host assertions: authenticated
  joining, bidirectional edits, conflicts, offline restart/reconnect, retained capture,
  startup theme preference, caching and browser-only bundle boundary.
- Seven real update assertions pass, including private key drafts and a lost actual
  worker set_connection acknowledgement blocking reload until exact retry completes.
  Evidence: `evidence/production-ui.json`, `evidence/update.json`.
- Those production reports identify the prior integration candidate, before the
  final small rerecording-status visibility fix and latest backend rebuild. The final
  source has all 26 fixture regressions; root must rerun release acceptance against
  its immutable final backend/UI build rather than treating these as release hashes.

Inspected screenshots (synthetic fixture data, not live enrollment/provider proof):
- `artifacts/voice-desktop-light-fixture.png`
- `artifacts/voice-desktop-dark-fixture.png`
- `artifacts/voice-phone-dark-fixture.png`
- `artifacts/voice-recording-phone-fixture.png`
- `artifacts/voice-transcript-phone-fixture.png` (390×667)
- `artifacts/voice-code-editor-fixture.png`

Physical iPhone microphone, keyboard, touch and installed-app behavior still require
published HTTPS validation. Fixture transcripts do not prove provider recognition.
OpenAI worker/vault/exchange and recording adapters are owned by the backend/platform
lanes; their security and real capture release gates remain root's responsibility.

## Engine handoff

[ENGINE-REQUESTS.md](ENGINE-REQUESTS.md) now distinguishes integrated voice/surface
contracts from still-required host-retained addressed drafts, exact join scope,
portable capabilities, correlated remote execution/results, and separate durability,
peer delivery and execution status. It also requests causal observation metadata
for settings: two opaque version IDs alone do not establish ancestry. No remote
execution or draft persistence is claimed by the current UI.

## Root integration verification

Root corrected modal-opener matching for buttons without data arguments and
explicitly focuses pointer-activated main buttons. The real Chromium/WebKit
static-app check now passes all eight outcome groups, including exact dock focus
restoration, bidirectional edits, addressed capture and reload.

Settings and sharing now explicitly disclose that removed keys remain in shared
history, including for future members. The credentials engine is unchanged.
All 26 fixture journeys and eight UI mutations pass; final built-site evidence is
[retained here](../checks/evidence/voice-applications-ui.json).

Final app source SHA-256: `68aa15e0dda4e7194f44503ab7b8ab2cd7c0f26b087ab5a9e351ad71ab4aaa4e`. Final browser bundle: `index-CmbAyo7_.js`,
`index-Dp2WxMqt.css`. The root release owns publication and Windows verification.
