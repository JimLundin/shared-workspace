# Browser app validation

Validation completed on 2026-10-23 for the live browser-card, action/IPC,
WorkAgent delivery and canvas changes. The independent combined baseline passed
**244 unique Python tests across 22 modules** and **15 JavaScript DOM fixtures**.
All **122 selected fault seeds** have reviewed detectors across **131 recorded
mutation executions**. The final native access diff was reviewed without a
correctness finding, and its owner’s **12-test native batch** passed.

This is a scoped validation record. The root full-tree release gate, vendor smoke,
Windows Downloads/ZIP deployment and reload have separate receipts and are not
claimed complete here.

## Subsequent release fixes

The full Windows gate exposed a verified-draft retry that unnecessarily replaced
an already Ready metadata binding. A concurrent Windows reader could deny that
replacement even though the draft was saved and independently verified. The
Outlook owner now publishes metadata only for the Pending-to-Ready transition;
every Ready retry still verifies the artifact, including edits and deletion.
No exception suppression, retry loop or longer deadline was added.

The correction passed 30 portable checks, 31 Windows checks and ten rounds of
130 repeated saves. A real Windows held-reader regression failed before the
change and passed afterward. Four targeted mutations detect both redundant Ready
writes and missing required Pending commits. The Windows controls also exercise
replacement denial, uncertain commit recovery and preservation of edited or
deleted artifacts. These checks are separate from the earlier browser campaign.

The shared canvas transport bootstrap also passed eight dependency-free Node
tests. Five independently seeded faults in protocol, version, command size,
pagehide detachment and duplicate attachment each failed the intended behavioral
assertion while the seven unaffected checks remained passing. CI runs the same
suite using Node 24; it adds no shipped Node runtime. The three canvas assets
match the isolated host copies behind three passing native Qt checks and thirteen
passing WebView2 checks. Production still launches Qt.

An independent nine-file review found no remaining correctness issues and
verified the tested source hashes and native exit receipts. Its frozen signoff
is `/tmp/toolkit-final-outlook-bridge-review-1792783598601540431/signoff.json`,
SHA-256 `17c352aaa8a6f4dd73d20a27bfe8b4cebbf6549d534267bb9d2a442a88af1079`.
The corresponding four Outlook files, three canvas assets, Node test and CI job
are covered; this does not replace the full release gate or vendor trials.

## Background screenshot validation

Windows release validation encountered an Edge screenshot timeout while the
page, frame tree and tab controller remained responsive. A preceding native UI
check separately observed the Windows lock screen. A later normal-GPU run also
timed out while Windows reported unlocked at every sampled observation, so the
lock screen alone does not explain the capture failure. Temporary isolated
trials compared focus emulation, occlusion handling, software rendering and
Chromium's new screenshot-surface feature. These are diagnostic observations,
not new mutation-test detections.

GPU acceleration remains enabled. Toolkit's own headless Windows browser uses
Chromium's new screenshot surface together with focus emulation scoped to one
capture. The GPU-enabled prototype completed the original attachment workflow
and the red-to-blue pixel check, with three captures taking 0.088–0.119 seconds.
Neither change alone resolved the observed behavior. The capture owner releases
emulation before returning an image; if reset fails it disconnects using the
existing browser ownership policy. A primary operation failure is preserved
when cleanup also fails. The Page notification subscription during attachment
is removed. Committed-frame commands work without it, and the normal navigation
loop already waits through provisional-frame errors. Direct DOM calls racing a
provisional frame can now fail promptly rather than waiting on the subscription's
internal frame hook; the caller must inspect the settled document before retrying.

The native browser suite now also repaints an inactive document red and blue,
checks five actual PNG pixels after each change, and verifies that document
visibility, the selected tab and the collapsed group are restored or unchanged.
This guards against stale frames, capturing the wrong page and retaining the
temporary visible state. Normal GPU validation records the Windows session's
lock state separately; the original command deadlines and full release gate
remain in force. These isolated headless checks do not establish foreground
focus behavior for every already-running personal browser.

The final production source passed both focused Windows checks: the existing
attachment workflow and the pixel/visibility regression. Windows reported locked
in all 27 sampled observations. The full portable editing profile passed 874
checks, and both type checkers passed. The independent capture baseline passed
all 18 portable browser tests. These results use the production capture path,
without the prototype's runtime patches or a GPU-disable flag.

Three isolated mutations were detected at their intended behavior: leaving a
borrowed connection open after reset failure, allowing a shutdown error to hide
the reset error, and retaining focus emulation after a successful capture. The
native mutation failed the document-visibility restoration assertion; no timeout
or setup failure was counted. [browser-capture-mutations.json](browser-capture-mutations.json)
records the exact replacements, source hashes, detector sites and execution
receipts as a separate campaign from the 122 browser-app faults below. A failed
shutdown now remains a note on the original reset error. The full Windows
release gate and deployment remain separate receipts.

## Mutation evidence

[browser-app-mutations.json](browser-app-mutations.json) contains all 122 exact
fault definitions, the actual detector assertions or DOM cases, original and
mutant source hashes, selected test hashes, frozen snapshot identities, every
recorded execution, and the resolved findings. It is self-contained; its original
`/tmp` and Windows stage paths identify supplementary raw logs and source copies.

| Slice | Reviewed fault seeds |
|---|---:|
| Browser control and IPC | 21 |
| Domain values, profile persistence and captured sources | 30 |
| Agent tools, receipts and durable action evidence | 17 |
| WorkAgent / WorkService asynchronous delivery | 20 |
| Canvas persistence and wire boundary | 15 |
| JavaScript layout, occlusion and modal behavior | 19 |

Eight coverage gaps were closed with owner-written regression tests: named
profile persistence, private payload omission from generic logs/previews, exact
request expiry, replay-history capacity, exact in-flight save acknowledgment,
recovery after a failed save, the 12/13-slot boundary, and hidden-versus-modal
suspension. Their original survivors remain in the JSON. Nine surviving
executions include an extra W12 run accidentally retaining the original test
selection; the final repeat selects the added exact-expiry test and detects it.

Every counted detection was inspected at its behavioral failure site. Five seeds
are detected because a valid public operation is wrongly rejected; the JSON
explains those paths. Missing configuration, malformed mutations, import errors,
collection errors and timeouts are not counted as detections. All source
mutations ran in isolated `/tmp` copies, and all previous validation reports
remain unchanged.

## Resolved implementation findings

- Cancellation accepted during a blocked observer poll now precedes the first
  delivery of a newly returned browser request.
- The shared deadline-aware Event checks the original request expiry at eventual
  Qt/native dispatch, including equality, while the observer is blocked.
- Saved app and navigation URLs now use the same strict whitespace/port checks as
  app operations, preserving prior captured-page semantics.
- Parent app close now consults edited owned popups. A refusal preserves the app
  and reports `CloseCancelled` for the original parent request.

The broader checks cover exact card/instance/document identity, stale requests
and owner epochs, cancellation and one-time delivery, bounded replay history,
private tool payloads, honest persisted receipts, and recovery with fresh evidence
from the correct card. Canvas checks cover full page geometry versus clipped
input masks, CSS stacking, foreground controls/cards, suspension, focused-card
priority, fragmentation limits, epoch/sequence changes, preview clearing,
retired-card reload, and exact save observations.

## Native evidence and final diff review

The latest browser-owner batch passed all 12 tests, including real input/source
capture, edited-popup close refusal, chosen upload/export destinations, site
permission Allow/Deny, selected-file refusal and successful write, navigation
retirement, UTF-16 capture bounds and navigation races, and profile storage/cookie
restore and isolation across fresh processes. The native completion logs and
source/test hashes were inspected. No native job was executed by the independent
validation lane.

Earlier owner-run suites also passed ten UI/interface lifecycle tests and eight
integrated canvas tests. Their exact source versions remain distinct in the JSON.
The canvas tests verify full modal coverage, corner input interception at desktop
and compact sizes, inert background/focus behavior, native page suspension,
transient preview clearing, persistence-aware receipts, and self-close/reload.
The earlier six-test browser-owner run is a historical subset of the final twelve;
it is not an additional test total.

The final diff requires a chosen download destination before acceptance, tracks
at most eight downloads, cleans finished requests and cancels them at shutdown.
Supported site permissions show the requesting origin and access purpose as
plain text with default No. A pending prompt is bound to its page/instance,
generation and URL; navigation, close or crash retires it without deciding for a
later document. File access shows the requesting origin, local selected path and
read/write scope, and requires Allow. The existing upload chooser and remote-page
isolation remain intact.

## Reproducibility and limits

- Combined portable snapshot:
  `3dbb9a5ea5cb6ff360a70c460c299fb49fc9ab54c1c44d69d89eeefe115bf70c`.
  Python 3.15.1; 244 tests in 42.77 seconds. Per-owner baseline counts are not added.
- DOM oracle: Node v24.16.0, 15 exact cases executing the shipped `apps.js` against
  observed DOM fixtures. It does not emulate Qt painting, focus or navigation.
- Final native source:
  `4e908ea9cb7f16cf5fbd9062ed7f0b44434173328d2f0b7c510140dbb1d3646b`;
  native tests:
  `7d52b8938a88a2b01990fc30924b76609a4c5131cbb922ed224e54248c4e37ff`.
  This later two-file native diff is separately frozen and reviewed; it is not
  presented as part of the earlier portable execution snapshot.
- The download capacity bound was inspected in code; the native fixture tests a
  chosen export rather than eight simultaneous transfers. Native file-choice
  tests use actual Qt requests/dialogs with platform-native dialog presentation
  disabled only in the fixture for automation.
- These checks do not establish authenticated Microsoft 365/VS Code workflows or
  every site’s compatibility. Desktop/screen capture remains unsupported pending
  a chooser that shows the actual requested access. The separate root release,
  deployment and reload receipts determine release readiness.
