# Runtime footprint proposal

Recorded 2026-10-23. This records the requested investigation target. The current
browser-card implementation continues while isolated probes evaluate it; this
note does not perform a production source or runtime migration.

[Open the interactive architecture and package comparison](runtime-footprint.html).

The [24 October direction review](runtime-direction-review.md) and
[shared-workspace decision](adr/0002-shared-agent-workspace.md) connect this
runtime target to the product's missing conversation/canvas control. The next
implementation milestone is one persistent conversation and a shared workspace
control interface using the current owner, followed by migration of that same
interface to TypeScript/WebView2. These documents recommend a direction; they do
not claim that the runtime or conversation integration has been shipped.

The target is **Pi Durable inside trusted WebView2 JavaScript**, with a small
native desktop bridge and the shared Evergreen WebView2 Runtime. TypeScript
builds the browser application. The distributed application should contain no
Python interpreter, Node runtime, Qt or private browser-engine bundle. The
current Python workers are a transitional implementation, not the end state.

[`@earendil-works/pi-durable` 1.4.0][pi-package] would own conversation, task and
JSON-document execution in a trusted browser context. Native code would own
desktop APIs, keyboard/audio work, supervision and the validated effect bridge.
Separate untrusted application views would retain agent interaction without
receiving that privileged bridge. One shared WebView2 installation can support
multiple views and browser processes; this target does not imply one renderer
or zero browser memory.

## Measured archive baseline

The inventory in `/tmp/toolkit-browser-runtime-footprint.json` describes the
installed previous canvas bundle. The browser-card change adds source while
leaving runtime pins unchanged. This is an archive inventory, not a measurement
of running memory or CPU.

The ZIP is **313,585,846 bytes**. Its file entries break down as follows:

| Content | Compressed file bytes | Share of whole ZIP | Uncompressed file bytes |
| --- | ---: | ---: | ---: |
| Qt and Qt WebEngine / PySide6 | 241,192,314 | 76.91% | 657,741,669 |
| Python interpreter and standard library | 40,613,038 | 12.95% | 128,419,061 |
| Other Python packages and native dependencies | 26,707,200 | 8.52% | 112,834,502 |
| Toolkit source and launch files | 2,824,704 | 0.90% | 6,827,614 |

Compressed entries total 311,337,256 bytes; the remaining 2,248,590 bytes are the
difference between those entries and the complete ZIP, which also contains
archive structure. Uncompressed entries total 905,822,846 bytes. These are file
sizes, not allocated disk usage or resident memory. The PySide6 group includes
Qt widgets and graphics modules as well as WebEngine; it is not an estimate of
the savings from replacing one renderer.

`Qt6WebEngineCore.dll` is 203.5 MB uncompressed / 83.4 MB compressed.
`qtwebengine_devtools_resources.debug.pak` is 75.8 MB / 14.7 MB. Python HTML
documentation is also included: `Doc/html/whatsnew/changelog.html` alone is
7.4 MB / 1.3 MB. Here MB means 1,000,000 bytes. Documentation, debug resources
and unused Qt components are candidates for a packaging audit, with supported
deployment requirements and runtime checks established before removing files.
Their presence does not mean they all consume that amount of memory at runtime.

## Separate language, host and agent runtime

TypeScript emits JavaScript; WebView2 supplies its execution environment in the
target design. Node or another build tool can be used in development without
shipping a Node runtime. Moving source to TypeScript alone does not remove the
current native/runtime dependencies; those consumers must also be replaced.
[TypeScript documentation][ts]

| Candidate | Runtime/deployment implication |
| --- | --- |
| TypeScript UI in the current Qt host | A transitional step: Qt WebEngine and Python remain, so it does not achieve the requested distribution target. |
| Target: native WebView2 host and browser Pi Durable | A native executable hosts browser JavaScript and the typed desktop bridge. Evergreen supplies the shared engine; Pi's selected dependency graph must run without Node APIs. |
| Tauri as the native host | A Rust core can host the web frontend and native bindings. Windows uses WebView2, macOS WKWebView and Linux WebKitGTK. It is a possible host choice; the Pi runtime would still need the browser integration, and Python sidecars would be transitional only. [Architecture][tauri], [webviews][webviews]. |
| Electron / TypeScript | Electron embeds Chromium and Node.js, with a main process and web renderer processes, so it does not meet the requested absence of a shipped Node/private-browser runtime. [Introduction][electron-intro], [process model][electron]. |

Microsoft distinguishes **Evergreen**, a shared, automatically updated WebView2
Runtime, from **Fixed Version**, whose binaries ship with the application and
whose updates belong to its distributor. A small Toolkit download using an
already installed shared runtime is a distribution benefit; it does not mean
the runtime occupies no disk or memory on the machine. Measure first-time setup
when the runtime is absent separately from installation on an equipped machine.
[WebView2 distribution][distribution]

WebView2 uses Chromium's multiple-process model, including browser, renderer
and helper processes. Process groups are associated with user data folders;
counting only the native host misses much of the cost. Tauri uses the platform
webview, and its Windows installer supports both Evergreen and Fixed Version
deployment. Neither TypeScript nor a shared runtime establishes a RAM or CPU
reduction by itself. [WebView2 processes][processes], [Tauri architecture][tauri],
[Tauri webviews][webviews], [Tauri Windows installers][tauri-windows]

Replacing the canvas renderer alone cannot remove all Qt from Toolkit.
Notifications, conversation/history, Work, dialogs, keyboard views and keeper
controls also use Qt; see [Qt ownership](qt-interface.md). A temporary WebView2
adapter hosted alongside Qt can answer compatibility questions, but its package
size includes both stacks. A later experiment must replace every remaining Qt
consumer before claiming the entire PySide6 footprint has been removed.

## Pi Durable candidate

The package/source assessment is pinned to **1.4.0** and repository commit
`4a080252f06caf63a525d7873c5d94d3c23887e0`, identified by the published package's
provenance. The downloaded artifact matched the registry integrity digest;
package facts below come from that source inspection. Executed probe results
are recorded separately below.
[Package metadata][pi-metadata], [provenance][pi-provenance]

Pi Durable is an embeddable ESM JavaScript library with TypeScript declarations
for conversations, checkpointed tasks and typed JSON documents. Its browser
IndexedDB and portable adapters make browser hosting a concrete investigation
path. The manifest's **Node >=22.19.0** declaration and Node SQLite adapter do
not establish a Node requirement for every browser entrypoint. They also do not
prove that all environment, filesystem, shell or code-mode packages work in a
browser. The spike must bundle and execute the selected graph without a Node
runtime, identifying any browser adapters or polyfills explicitly.
[Manifest][pi-manifest], [types][pi-types],
[storage adapters][pi-storage]

| Concern | Version 1.4.0 behavior and Toolkit consequence |
| --- | --- |
| Store ownership | One owner controls a store; the library provides no cross-process ownership lock. Its browser guidance calls for a lifetime Web Lock. The native host must also fence old/new runtime owners across restart; UI and app views must not become competing schedulers. [Ownership rule][pi-owner], [storage][pi-storage] |
| Record commits | Session serializes mutations, awaits storage, then publishes. IndexedDB is the intended browser persistence adapter; its transactions cover Pi records, not external native or remote actions. Profile deletion, origin changes and browser/host failure need explicit recovery tests. [Session][pi-session], [storage][pi-storage] |
| Resume and shutdown | Opening restores surviving running tasks to pending without dispatch; `resume()` and progress-seeking operations enable scheduling. `close()` signals and joins invocations, leaving interrupted checkpoints pending. Cancellation is cooperative, so a tool ignoring its signal can prevent orderly shutdown. The embedding host still owns supervision and wakeups. [Open][pi-open], [resume][pi-resume], [close][pi-close], [host options][pi-host] |
| Tool replay | Execution intent is committed before calling a tool. Replay defaults to unsafe; crash recovery reruns only when both the stored and current tool policies say `replay: "safe"`. Otherwise the result records interruption and possible partial execution. The API explicitly does not promise exactly-once external effects. [Intent/recovery][pi-tool], [API contract][pi-tool-api] |

The intended seam is browser Pi tools forwarding validated requests and
cancellation through a small native bridge. Stable call/action IDs, persisted
receipts and outcome reconciliation remain necessary when an external effect
succeeds before its result is committed. Clicks, typing and file writes must not
be marked replay-safe solely because the runtime recorded their intent.

First prove the browser graph, IndexedDB recovery and native bridge with
disposable data. Then map Toolkit's durable task, source, approval and effect
contracts onto Pi, and replace Python/Qt consumers in measured slices. The
existing journal remains authoritative during that transition until a defined
migration transfers ownership; the target does not include permanent Python or
Node services. Count all browser assets, native dependencies, views and process
groups in the comparison. Packaging and compatibility with existing persisted
tasks remain separate acceptance work.

## Browser lifetime and native duties

Use a stable HTTPS virtual-host origin and persistent profile for Pi's IndexedDB.
`NavigateToString` has a null origin and cannot supply this storage. Hold the
Web Lock through the complete Harness lifetime and shutdown. A worker can use
IndexedDB and Web Locks, but still depends on its owner and browser lifecycle;
it is not an independently persistent daemon. Any worker-to-native messaging
adapter needs its own verification. [Local content][local-content],
[Web Locks][web-locks], [workers][workers]

`IsVisible=false` stops rendering and throttles some page work. `TrySuspend()`
pauses script timers/animations; `Resume()` can resume a hidden view. Pi uses
`setTimeout` for foreground progress and declines background housekeeping timers
when handles lack `unref()`, as normal browser handles do. That housekeeping
rule does not mean all background tasks are disabled. With `onWake`, native
code owns sleeping-task wake delivery: `wake()` reads the requirement and
`resume()` delivers the wake. Native supervision must also arrange recovery of
active work. A hidden page timer alone is insufficient for that contract.
[WebView2 lifecycle][wv-lifecycle], [visibility][wv-controller],
[Pi timers][pi-timers], [Pi wake contract][pi-wake]

Keep the runtime unsuspended while active work needs it; coordinate idle/close
before suspending or replacing its owner. WebView2 controller `Close()` does not
run `beforeunload`, so orderly Harness close must be explicit. The separate LOW
memory-target policy keeps scripts running but is best effort and does not
remove ordinary background throttling; Microsoft advises against mixing it
with suspend/resume. [Controller][wv-controller], [memory policy][wv-memory]

The native bridge must own global hotkeys, desktop-window operations and
timing-sensitive input/audio work. Low-level keyboard callbacks run on their
installing message-loop thread and must return promptly; remap decisions and
bounded buffers cannot wait for JavaScript. WASAPI capture packets must be
acquired/released on the same thread and can be batched to the runtime. Ordinary
browser microphone APIs may serve other use cases. Preserve keyboard process
isolation when replacing Python with native code. [Keyboard hook][keyboard],
[hotkeys][hotkeys], [audio capture][audio], [desktop windows][windows]

Profiles separate browser data while potentially sharing a browser instance;
separate user-data folders create separate process groups. Keep Pi's trusted
state and privileges separate from remote app profiles, with explicit data
retention/recovery rules. Validate the originating view, URI and message shape
before native effects. Remote views receive no privileged host objects or
message routing. Sharing Evergreen binaries does not imply sharing personal
Edge credentials or eliminating renderer memory. [Profiles][profiles],
[user-data folders][udf], [embedding security][wv-security]

## Isolated probe evidence

The spike lives under `/tmp/toolkit-pi-webview-spike/`; it does not replace the
installed application. Frozen `browser/verdict.json` records three successful
Linux Chromium scenarios using actual Pi Durable 1.4.0 and `IndexedDbStorage`:

| Scenario | Passed assertions | Evidence directory under `browser/evidence/` |
| --- | ---: | --- |
| Page renderer crash and persistent-profile reopen | 19 / 19 | `portable-1792776664220` |
| Dedicated worker termination and replacement ownership | 21 / 21 | `portable-1792776812161` |
| Graceful Harness close with an active unsafe invocation | 21 / 21 | `portable-1792776812029` |

The runs cover submission/deduplication, owner locking and transcript recovery.
Each retained one simulated scratch-effect increment, reported an interrupted
unsafe tool instead of replaying it, and resolved two pending tasks to zero.
These runs do not prove Windows-native effects or background timer punctuality;
an actual model could choose to request another action after the interruption.
`navigator.storage.persisted()` was false, so the observed restart persistence
does not establish protection from eviction, profile deletion or disk failure.

The frozen page bundle is **1,004,017 bytes** (**190,548 gzip**), with SHA-256
`e0dbca30bc45bcd0bdfab101afce60c756e3405864282d754c1589627d5cb733`.
The worker is 1,000,632 bytes (189,667 gzip), plus a 7,959-byte page relay.
`browser/build-audit.json` records the outputs. The browser had no `process`,
`require` or `Buffer` globals; the exercised graph used no static imports,
polyfills or package patches and made no foreign network requests. Guarded `node:fs/promises` and
`node:os` auth fallback strings remain dormant because an explicit browser
`authContext` is supplied. These are probe-bundle figures, not a full product
or shared-runtime footprint.

The native Win32 C++20 host built and staged with WebView2 SDK 1.0.4314.35.
`host/build-report.json` records a 408,064-byte executable, a 194,912-byte SDK
loader and **1,611,847 uncompressed bytes of prototype payload** including the
page assets and SDK license. Its imports are Windows system/UCRT libraries. It ships
no Python, Node, Qt, CLR or browser runtime; installed Evergreen is a prerequisite.
That build-only total excludes the shared runtime and is not full Toolkit size.
Windows rejected `Process.Start` with **Access denied** before
the host had a PID, window or log; the run list is empty. The failure receipt is
`native-run-report.json` in
`%LOCALAPPDATA%\Temp\toolkit-pi-webview-probe-7zw3_qac`.

`host/launch-diagnostics.json` matched Defender ASR event **1121** to the staged
executable: rule `01443614-CD74-433A-B99E-2ECDC07BFC25`, **Block executable files
from running unless they meet a prevalence, age, or trusted list criterion**.
The diagnostic ruled out a staged-file path/ACL or Zone.Identifier issue; the
denial was explicitly IT-enforced. Diagnostics stopped without policy changes
or a bypass. **Only the new compiled executable was blocked**; this does not
establish that WebView2 or Pi's browser JavaScript cannot run on Windows. A
policy-approved execution path is still needed to validate that compiled
artifact. [Microsoft rule reference][asr-rule]

The frozen native handoff is `host/VERDICT.md`, with `build-report.json`,
`native-run-report.json`, `launch-diagnostics.json` and `final-hashes.json`.

A separate Qt-free host has now run the existing canvas using the accepted
Python runtime. It calls the official WebView2 loader and COM interfaces through
`ctypes`; it does not launch or load the blocked executable. Native run 3 passed
13 interaction checks and exited zero on Windows with Python 3.15.1 and
WebView2 155.0.4283.62 at 125% display scaling. The checks cover independent app
views, real pointer/Unicode input, clipping, zoom, launcher focus, workspace
retention and stale-layout rejection. The host imported no Qt, Tk or CLR.
Three separate native Qt compatibility checks passed against the same canvas
transport assets. See [the UI investigation](webview2-ui-investigation.md) for
the exact receipts, earlier failures and remaining migration boundaries.

After one user sign-in, three newly created Office documents reopened
concurrently in independent views with the same app data folder and no new
prompt. One Word document retained its exact neutral sample text and filename;
the Excel workbook retained its exact A1 value and filename. Both reported
saved state on the independent reopen. The second Word document opened with
the shared account, but its rename had a focus failure and retained trial
fragments; its guard refused further editing. That case is not a successful
rename/edit trial. Word and Excel offered the supported Show tabs only ribbon
option; it was invoked in both, with Excel's checked state verified.

These later operations used commands directed to the companion's own views,
document frames and browser-owned dialogs, without global typing. They follow
an earlier foreground-input attempt that was stopped after the user changed
applications; that burst was not guarded against a mid-action focus change,
so absence of misdirected characters is not claimed. The UI investigation
retains the separate attempts and their input methods. Trusted Toolkit assets
use a separate data folder from the app views. Shared account reuse is proved
for this tested tenant/runtime; full-profile restart persistence and other
tenants' account policies remain separate checks.

The same host also loaded the real `vscode.dev` workbench and opened its native
file picker through a scoped mouse action. The picker belonged to the verified
WebView2 browser process rather than the Python host. Its filename/Open controls
did not expose the Value/Invoke patterns used by the test helper; no file was
selected, so editing, saving and reopening a local file remain unverified.
This is a bounded host/dialog automation gap, not evidence that the VS Code
workbench cannot embed or that a human cannot use its picker.

The first Python-hosted native Pi run stopped before entering the host message
loop: Windows denied replacement of the probe's already published status
receipt. A native held-reader reproduction passed without a reader and failed
with either reader-sharing mode; the exact holder in the original run is
unknown. Publishing each phase to a new immutable file passes all three native
controls. The retained original receipt records no native effect and no browser
run; it remains a failed harness run, not a recovery result. The correction
changes only the probe's receipt publication/selection; its adapter, browser
assets, host, watchdog and effect decisions are unchanged. The original failure,
red/green regressions and native controls are retained under
`/tmp/toolkit-pi-webview-spike/debug/receipt-sharing/`.

The subsequent **Windows Pi trial passed both scenarios** on
**2026-10-23 at 19:43 UTC**, using the accepted Python 3.15.1 host and installed
WebView2 **155.0.4283.62**:

| Scenario | Observed native and durable outcome |
| --- | --- |
| Unsafe tool interrupted after its effect | The browser made a real native screen observation and committed one scratch-file counter increment. The host closed while the tool result was still pending. The exact run, task, call and effect receipt remained awaiting recovery. Native exit 0; final counter 1. |
| Fresh host with the same persistent profile | Pi recovered the matching interrupted tool, performed a new native observation, and consumed the exact recovery receipt. The counter remained 1 and the recovery host invoked no scratch effect. Native exit 0. |

Both hosts returned cleanly from their message loop, recorded fresh close
events, and had empty stderr and final error/rejection lists. The final scoped
process observations found no remaining processes attributable to the unique
probe stage. The hosts exited before the live process sampler observed them,
so these receipts do not establish process topology or a memory measurement.
The native observation reported the actual host's process ID and a
1920 × 1200 screen; it did not capture desktop contents or send input.

The complete launch receipt is
`%LOCALAPPDATA%\Temp\tpi_a92b56d8\launch-summary.json`, SHA-256
`8fc97b85f20f15bea58af34a49d3e77853a0a55d7455a754f518521c9c624b8b`.
The same stage retains the two immutable finished receipts, native state,
stdout/stderr, input hashes and process observations. The corrected runner is
`b52df2cde427a0a5f5d774ee6ff57b49c177f47dbf354a7a75693de717ebe68d`;
the unchanged adapter is
`88376c112f73db7d194c8545d96b5cf270efa64437c8170aee66f41249a18725`.
An independent review passed 32 focused recorder/launcher checks on both Python
3.12.5 and 3.15.1 and verified the native inputs, phase receipts, exact counter
binding and fresh host-event slices. Its signoff is
`/tmp/toolkit-pi-v3-receipt-review-1792784534219470047/signoff.json`, SHA-256
`008169bbdc41fc2d04c17e958aac5c7e6e0acab245167f7adb9cb85c4941a46d`.

This native trial runs Pi in the trusted **page**. Worker interruption and
ownership have separate portable Chromium evidence; a Windows worker-to-native
relay is not established here. The trial also remains separate from the live
canvas/backend and production journal. This transitional Python host does not
validate the blocked executable or its final footprint; removing Python remains
separate implementation work.

The model is scripted. Actual provider HTTP transport, credential handling,
CORS and streaming remain unverified, as do unexercised environment/code-mode
packages. The target needs a browser-compatible provider transport or a narrow
native HTTP/credential adapter; the scripted-model success does not settle that
choice. Pi's tested Windows page/native recovery path now has runtime evidence;
a comparable full-product process/memory measurement remains outstanding. No
RAM, CPU or final package-size improvement is claimed.

## Documents as the canvas unit

The intended surface is a document or code workspace with a small title and
essential controls. Toolkit should hide its own address bar, back/forward
buttons and general browser navigation in that mode. Direct document identity
and the shared app profile belong to the saved card; transient authentication
redirects must not replace its durable destination. Auth popups and newly opened
documents need distinct ownership so a new document can become another card.

The current browser owner already reuses a persistent profile across independent
cards, but it saves the last navigation URL and treats new windows as native
popups. Stable document destinations and adoption of document windows into saved
cards are remaining integration work. The current Qt owner also creates supplied
app views eagerly, up to its 32-view bound; it does not implement the proposed
lazy-view policy below.

An Office card still loads Microsoft's web editor for that document. Cards can
share account data, cache and installed browser binaries; each active document
still consumes editor/renderer resources. Removing visible chrome alone does
not reduce those costs. Prefer the editor's supported ribbon/focus controls
over rewriting its internal DOM. An embedded `vscode.dev` editor similarly has
a different capability boundary from a locally running VS Code server.

The open product question is how much editor UI should remain visible by
default, and how users regain controls such as formatting, files and extensions.
The requested starting point is the smallest usable document surface, with
additional controls available on demand. Record actual Office and VS Code trial
results separately from these design choices. See
[document and editor surface research](document-app-surfaces.md).

## Preserve the agent and application contracts

The candidate host must keep agents able to list, open, navigate, inspect and
act on registered web-app pages. Retain the typed `app_list`, `app_open`,
`app_navigate`, `app_snapshot` and `app_action` contracts, exact document targets,
navigation invalidation and bounded observations. Remote application pages must
remain separate from privileged shell bridges. Keep global remote debugging
disabled in the privileged shell process.

An agent-runtime adapter can change separately from this host interface. Any
candidate must be evaluated against current durable task identities, exclusive
run ownership, cancellation, resume, captured-source scope, approval binding,
and effect receipts. Preserve the distinction between verified and uncertain
effects after a lost acknowledgment; do not automatically replay an external
action because its result was not received. Keep the current journal
authoritative during evaluation and define its ownership/data migration before
transferring live work. See
[functional design](functional-design.md) and [canvas integration](desktop-canvas.md).

## Proposed measured experiment

1. Record the current release's archive, extracted files and runtime versions.
   Audit packaging candidates independently of host or language changes.
2. Prove Pi Durable with IndexedDB and the desktop bridge inside isolated
   Windows WebView2, using the accepted Python host for this functional proof.
   Independently validate an accepted compiled host before claiming a runnable
   distribution without Python or Node. Preserve the typed broker and
   keyboard/audio isolation contracts; the current native input implementation
   remains the comparison baseline.
3. Compare page lifecycle policies. Create a saved card's browser view when the
   user opens it or an agent explicitly requests it. Retain edited pages when
   hidden or parked. Discarding them changes the unsaved-state behavior and
   needs a separate comparison; profiles cannot guarantee recovery of that state.
4. Run equivalent **0, 1 and 3 app** workloads: shell only; one local editor;
   three local app fixtures. Use the same machine, workload, display scale,
   network conditions and profile state, with repeated cold and warm runs.
   Include typing, selection, navigation, popups, hide/return and orderly exit.

| Measurement | Record separately |
| --- | --- |
| Distribution/storage | Toolkit ZIP/installer bytes, browser bundle/native dependencies, required shared runtime installation, and profile/cache growth. Include runtime-present and runtime-absent setup; audit that the target ships no Python, Node or Qt. |
| Memory | Per-process and aggregate private bytes, working set and private working set, including the native host and all associated browser/renderers/helpers. Report any transitional Python processes separately. Record baseline, peak and settled values; summed working sets can double-count shared resident pages. |
| CPU | CPU time and utilization over the same startup, idle and interaction intervals; distinguish app scripts from Toolkit work. |
| Startup | Time from launch to shell readiness, then to a usable app document. Distinguish process cold start from warmed profile/cache behavior. |
| Lifecycle/cleanup | Process counts, exit latency, remaining owned processes, handles and pending broker requests; check state after repeated open/hide/return/close cycles. |
| Behavior | Exact-target action/capture correctness, stale/cancelled reply rejection, profile persistence, input latency, clipping/overlap, mixed DPI and popup/focus behavior. |

Working set describes resident pages; private bytes describe private committed
memory, including memory that need not be resident. Report both and identify
the process attribution method. A shared installation does not make all browser
processes on the machine Toolkit-owned. [Windows working set][working-set],
[memory counters][memory]

Actual Microsoft tenant sign-in, Office web editing/autosave and the required
VS Code features are separate compatibility checks. Local fixtures do not
establish authenticated Office compatibility. `vscode.dev` alone is not a full
execution backend; terminal/debugging needs a suitable backend such as
Codespaces or Remote Tunnels. [VS Code for the Web][vscode]

Set acceptance criteria from these measurements and required behavior; no
resource budget or improvement percentage has been assumed. Use the results to
validate the requested target, identify missing native capabilities and scope
the migration into verified slices.

[ts]: https://www.typescriptlang.org/docs/handbook/typescript-from-scratch.html#erased-types
[distribution]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
[processes]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model
[tauri]: https://v2.tauri.app/concept/architecture/
[webviews]: https://v2.tauri.app/reference/webview-versions/
[tauri-windows]: https://v2.tauri.app/distribute/windows-installer/#webview2-installation-options
[electron-intro]: https://www.electronjs.org/docs/latest/
[electron]: https://www.electronjs.org/docs/latest/tutorial/process-model
[working-set]: https://learn.microsoft.com/en-us/windows/win32/memory/working-set
[memory]: https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex
[vscode]: https://code.visualstudio.com/docs/remote/vscode-web
[pi-package]: https://www.npmjs.com/package/@earendil-works/pi-durable/v/1.4.0
[pi-metadata]: https://registry.npmjs.org/@earendil-works%2fpi-durable/1.4.0
[pi-provenance]: https://registry.npmjs.org/-/npm/v1/attestations/@earendil-works%2fpi-durable@1.4.0
[pi-manifest]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/package.json
[pi-types]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/types.ts
[pi-storage]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/README.md#L759
[pi-owner]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/README.md#L790
[pi-session]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/session/session.ts#L716
[pi-open]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/scheduler.ts#L370
[pi-resume]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/scheduler.ts#L419
[pi-close]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/scheduler.ts#L515
[pi-host]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/types.ts#L609
[pi-tool]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/tool.ts#L119
[pi-tool-api]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/types.ts#L206
[local-content]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/working-with-local-content
[web-locks]: https://www.w3.org/TR/web-locks/
[workers]: https://html.spec.whatwg.org/multipage/workers.html#the-worker's-lifetime
[wv-controller]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller
[wv-lifecycle]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2_3
[wv-memory]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2_19
[pi-timers]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/timers.ts#L17
[pi-wake]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/types.ts#L636
[keyboard]: https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc
[hotkeys]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey
[audio]: https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudiocaptureclient-getbuffer
[windows]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumwindows
[profiles]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/multi-profile-support
[udf]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder
[wv-security]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security
[asr-rule]: https://learn.microsoft.com/en-us/defender-endpoint/attack-surface-reduction-rules-reference#block-executable-files-from-running-unless-they-meet-a-prevalence-age-or-trusted-list-criterion
