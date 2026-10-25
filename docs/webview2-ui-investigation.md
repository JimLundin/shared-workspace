# Existing canvas UI in Windows WebView2

Research and execution evidence recorded **2026-10-23**. Sources below were
accessed on that date. Microsoft Win32 reference pages resolved to SDK documentation
`1.0.4258.31`; ABI details were also checked against the already downloaded
Microsoft SDK `1.0.4314.35` header.

The existing HTML/CSS canvas runs in native Windows WebView2. The isolated host
passed 13 Windows interaction checks using the existing design, including two
live app views, dragging, zoom, overlap and workspace retention. The production
foundation adds a shared transport bootstrap; production still launches the Qt
host. Replacing Qt's remaining view, input, lifetime and browser-profile
ownership is a separate migration.

Authenticated trials also reopened two Word documents and one Excel workbook in
separate cards using the same account profile. **Word A and Excel A passed
edit/save/reopen; Word B failed filename/content persistence.** Word and Excel
offered their supported **Show tabs only** ribbon option. `vscode.dev` loaded,
but its scratch-file trial stopped at an automation limitation in the native
file picker; file edit/save/reopen and Zen mode remain unproved. These are
individual workflow results, not a completed production migration.
`apps.js` already publishes native browser-card bounds, disjoint clip rectangles,
zoom, stacking order, workspace/epoch and monotonic sequence. Preserve that
contract for the first host proof. A browser engine rendering `index.html` alone
does not establish browser-card parity.

Current implementation inspected: `assistant/canvas_web.py`,
`assistant/browser_surfaces.py`, `assistant/canvas_assets/{index.html,apps.js,canvas.js,shell.css}`,
`docs/qt-interface.md` and `docs/runtime-footprint.md`.

The production change is limited to `assistant/canvas_assets/bridge.js`, the
script load in `index.html`, the connection at the end of `canvas.js`, and
`tests/test_canvas_bridge.mjs`. CSS, card rendering and the application geometry
publisher are preserved. The bridge uses the current Qt channel when hosted by
Qt and the versioned WebView2 messages when hosted by WebView2. Neither the
runtime backend nor the isolated keyboard process changes.

Run the focused transport checks with `node --test tests/test_canvas_bridge.mjs`.
They use Node's built-in test runner without packages; Node is a development
check tool, not a Toolkit runtime dependency.

## Mapping the current duties

| Current Qt duty | WebView2 / Windows mapping | Practical consequence |
| --- | --- | --- |
| Local `index.html`, asset interceptor and CSP | `ICoreWebView2_3::SetVirtualHostNameToFolderMapping`, with `DENY` cross-origin resource access; navigate to a stable HTTPS origin such as `https://toolkit-ui.example/index.html`. [Local content][local], [mapping][mapping] | Map only bundled assets. Replace the `qrc:` WebChannel bootstrap; keep the restrictive CSP and exact trusted-document navigation policy. HTTPS mapping supports origin storage; `NavigateToString` has a null origin and cannot supply IndexedDB/localStorage. |
| `Bridge.command`, `changed`, `display`, readiness handshake | `window.chrome.webview.postMessage(object)` → `WebMessageReceived`; native → `PostWebMessageAsJson`. [Messages][core], [sender URI][messageargs], [security][security] | Keep the typed command variants and validate bounds, sizes, epochs and IDs at entry. Bind authority to the native view identity plus parsed sender URI; a payload's claimed view name is insufficient. Native replies must recheck the intended document. |
| Browser card geometry, `QRegion` mask, `raise_()`, `setZoomFactor()` | One borderless child HWND wrapper per windowed controller; `SetWindowRgn`, native sibling ordering and `SetBoundsAndZoomFactor`. [Controller][controller], [window regions][region] | This is the closest first probe to the existing design. Use the wrapper HWND, avoiding private WebView child-window internals. A CSS `z-index` or `overflow` rule does not itself configure native view geometry; continue sending the clip contract. Correct painting and hit testing remain execution proofs. |
| Monitor scaling versus canvas zoom | `ICoreWebView2Controller3` rasterization scale / bounds mode; controller page `ZoomFactor`. [Scale][scale], [controller][controller] | Keep DPI/text scale separate from canvas zoom. Choose raw-pixel or rasterization-scale bounds explicitly and convert CSS rectangles once. `SetBoundsAndZoomFactor` updates size and zoom atomically from the host's perspective; equal scaling avoids an intermediate page reflow. |
| Focus, keyboard shortcuts and native drag/drop | `MoveFocus`, `MoveFocusRequested`, `AcceleratorKeyPressed`; ordinary Win32 input for windowed views. Composition additionally requires explicit mouse/pointer/cursor and OLE drop forwarding. [Controller][controller], [composition][composition], [composition drops][drops] | Test focus transfer and shortcuts across shell and app views, including IME, wheel, selection and dragging outside a view. Keep global keyboard processing in its isolated owner. |
| Modal/hidden cards and previews | `IsVisible`, `CapturePreview`; explicit controller lifetime. [Controller][controller], [core API][core] | Hide both controller and wrapper as appropriate. Hidden views throttle some work; hiding is not destruction. Preserve the current preview-and-hide behavior for shell modals and reject stale layout frames. |
| Browser profiles, cookies and persistent state | Explicit writable user-data folder (UDF), `Environment10::Create...ControllerWithOptions`, stable `ProfileName`, non-private mode. [Profiles][profiles], [UDF][udf] | Put state outside replaceable application assets. Same-profile views share browser data; different profiles can share a browser instance. Keep trusted UI/Pi data and privileges separate from remote applications; use a separate trusted UDF/process group for this design. |
| `newWindowRequested` / `openIn`, popup ownership | `NewWindowRequested` + deferral + `put_NewWindow`; `WindowFeatures`; native owned window. [Popup contract][popup] | The target must be previously unnavigated, on the **same Environment and profile** as the opener. Install settings/handlers before assignment. Preserve opener relationships, user-initiated policy, page limits and descendant ownership. WebView2's Edge popup blocker is disabled; the host applies its policy. |
| `RequestClose`, unsaved confirmation, descendant-first close | `ScriptDialogOpening` with `BEFOREUNLOAD`, `GetDeferral`/`Accept`, `WindowCloseRequested`; candidate in-process CDP `Page.close`. [Core API][core], [dialogs][dialogs], [CDP contract][cdp] | `Controller.Close()` skips `beforeunload`. A close request must finish its veto/lifecycle protocol before removing the card. Preserve the card on refusal or timeout. See the unproved close path below. |
| Site permissions, downloads and local-file workflows | `PermissionRequested`; `DownloadStarting` with deferral, destination and cancel; browser file pickers. [Permission args][permissions], [permission kinds][enums], [downloads][downloads] | Attribute prompts to the requesting origin and view. `Handled=true` hides download UI; it does **not** cancel the download. Set `Cancel` on refusal and choose a destination deliberately: an existing `ResultFilePath` is overwritten. Exact path-aware file permission UI still needs proof. |
| Isolated-world DOM capture/control | `ExecuteScript` / `ExecuteScriptWithResult`; in-process CDP for an explicitly created isolated world if needed. [Core API][core], [CDP contract][cdp] | Ordinary `ExecuteScript` is not a drop-in replacement for Qt's `ApplicationWorld`. Preserve document identity, navigation-generation and target guards. Do not expose arbitrary script/CDP entry points or a remote debugging endpoint to the page or agent. |
| Native dialogs, appbar, hotkeys, windows and remaining Qt widgets | Win32 ownership plus purpose-specific host commands; later HTML/native replacements for other Qt screens. [Threading][threading]; current `docs/qt-interface.md` | A WebView2 canvas adapter alone does not remove all Qt. Native effects stay behind validated commands and remain outside the presentation code. |

## Constraints that shape the host

**Clipping and composition.** A child HWND plus `SetWindowRgn` can express the
current region representation without changing the HTML. Region coordinates are
window-relative; Windows owns the HRGN after a successful call. WebView2's
composition controller instead attaches to an `IDCompositionVisual` or
`Windows.UI.Composition.ContainerVisual`, while still requiring controller
bounds. The simple DirectComposition `SetClip` rectangle does not represent the
current union of disjoint rectangles. Windows Composition has a
`CompositionGeometricClip` alternative, but reproducing this canvas's shape and
input policy is additional work. Visual clipping must agree with host hit tests,
capture and coordinate transforms. [Regions][region], [composition][composition],
[DirectComposition clip][dcompclip], [geometric clip][geometricclip]

**Trusted transport.** Only the trusted shell/runtime receives privileged
message handling. Remote app views have no privileged host objects and no
desktop-command route; disable web messaging where unused. Deny unexpected
trusted-view navigations and frame navigations, and preserve CSP/resource
restrictions. Messages are asynchronous and ordered within one posting page;
their order relative to DOM operations such as `window.open` is not guaranteed.
Use explicit request IDs/acknowledgements where ordering matters. Current mapping
documentation also says service-worker mappings are merged across WebViews
sharing a browser instance: keep mappings consistent, and do not treat a
per-view map alone as the security boundary. [Security][security],
[messages][core], [mapping][mapping]

**No nested dialog loop in an event callback.** WebView2 creation, callbacks and
calls belong to a UI STA with a running message pump. Synchronous nested modal
loops inside WebView2 callbacks are unsupported. The existing Qt `dialog.exec()`
approach therefore needs a deferral and queued/asynchronous UI, with completion
on the owner thread and revalidation after navigation or destruction. This
applies to permissions, downloads, popups and script dialogs. [Threading][threading]

**Unsaved close is an explicit unresolved proof.** The checked SDK header has
no `TryClose` API. `WindowCloseRequested` documents a page's `window.close()`
request; it does not establish the entire desired veto protocol. Chromium's
primary CDP definition says `Page.close` tries to close the page while running
its `beforeunload` hooks. WebView2 exposes `CallDevToolsProtocolMethod`, making
that a concrete candidate for the installed runtime. Verify accepted, refused,
repeated and timeout cases, including popup families. Do not equate the CDP call's
completion with confirmed closure, and do not use final `Controller.Close()` as
an unsaved-state check. [Controller][controller], [core API][core], [dialogs][dialogs],
[Chromium protocol][cdp]

**Office and VS Code need real workflows.** Persistent profiles provide browser
storage, not proof of a successful Microsoft 365 tenant sign-in or an editor's
file workflows. Optional OS-account SSO is a separate environment setting,
disabled by default. `LaunchingExternalUriScheme` can attribute, defer and cancel
registered protocol launches. `FILE_READ_WRITE` exists, but the documented
permission event exposes origin/kind, not Qt's selected-file path and handle
type. Keep browser-managed picker/permission behavior until custom prompt parity
is proved. Native `CreateWebFileSystemFileHandle/DirectoryHandle` can deliberately
grant access to a cooperating page; sending these handles grants the specified
permission without the usual prompt and is not a general workaround for
third-party app integration. [SSO][sso], [external schemes][external],
[permission kinds][enums], [permissions][permissions], [file handles][handles],
[object messages][objects]

## Isolated host stages and execution evidence

Use the architecture-matching official loader with the shared Evergreen
Runtime. Production WebView2 does not use the installed Edge Stable browser as
its runtime. Detect the Runtime using documented registration or
`GetAvailableCoreWebView2BrowserVersionString`; the latter can also report an
Edge preview channel, so record the actual selection. If absent, Microsoft's
Evergreen bootstrapper serves online setup and its standalone installer serves
offline setup. Fixed Version would ship private browser binaries and misses the
distribution target. Runtime provisioning remains the runtime lane's task.
[Distribution][distribution]

1. **Python host proof:** use the already accepted Windows Python runtime,
   `ctypes` Win32/COM and the official signed WebView2 loader with the installed
   Evergreen runtime. Host the actual canvas assets and keep domain commands
   behind a narrow adapter. This tests a Qt-free canvas host; it does not prove a
   Python-free distribution. Keep the prototype in `/tmp/toolkit-canvas-webview2`.
2. **Behavioral proof:** exercise the same geometry/transport contract with
   independent child views before deciding whether composition is necessary.
   Preserve production state and UI during these disposable trials.
3. **Native packaging:** once the hosting behavior is established, move the same
   boundary to an accepted native artifact and replace remaining Python/Qt
   consumers in separately measured changes. Evergreen supplies shared binaries;
   it does not imply one renderer or zero browser memory. [Distribution][distribution],
   [process model][processes]

The previous C++ host under `/tmp/toolkit-pi-webview-spike/host` compiled but was
blocked before launch by IT-enforced Defender ASR rule
`01443614-CD74-433A-B99E-2ECDC07BFC25`, as recorded in
`docs/runtime-footprint.md` and that host's diagnostics. It supplies no Windows
rendering evidence. This investigation makes no policy changes and does not
attempt to execute or load the blocked artifact. The runtime lane retains
ownership of the Windows-access and Pi Durable investigation.

| Area | Observed in the isolated host | Remaining migration proof |
| --- | --- | --- |
| Canvas boot and transport | Actual canvas, four original example cards, readiness/snapshot/command round trip; remote views have no privileged bridge. | Bind the production store/backend, test every host-bound command and origin/lifecycle rejection path. |
| Child-view geometry | Two independent native app views, real pointer drag, atomic canvas zoom, disjoint clipping under an overlapping note, stale workspace epoch rejected. | Full occlusion matrix, resize/pan edge cases, 100%, 150%, 200% DPI and monitor transitions; measured frame/input latency. The observed Windows scale was 125%. |
| Input and focus | Real Windows pointer and Unicode `SendInput` edits; Ctrl+K from app to launcher; modal hides/blocks app view; Escape restores it. | Tab/IME, selection/capture across bounds, wheel policy, context menus, pen/touch and file drops. |
| Page lifetime | Workspace round trip preserves the exact live page and edits; navigation updates address/back state; `Page.close` yields `WindowCloseRequested`. | Unsaved-close accept/refuse/timeout, popup family close, concurrent navigation, crash recovery. |
| Profiles and Office | Three direct-document cards reused the signed-in app UDF without per-card login; fresh companion hosts verified saved Word A text and Excel A1 text. Both editors exposed Show tabs only. | Word B rename/content failure, full browser-process restart with persistent profile, deliberately separate-account isolation, native foreground input parity and ribbon restoration/persistence. |
| Other workflows and distribution | Python 3.15.1 + Microsoft loader + installed WebView2 155.0.4283.62; VS Code web workbench loaded; owned probe processes closed normally. | VS Code picker automation/file saves, folder access, Zen, downloads, appbar/taskbar ownership, resource budget, missing-runtime setup and removal of remaining Qt consumers. |

### Native execution evidence

Run 3 completed on **2026-10-23 at 18:46:57 UTC**, **13 checks passed**, host exit
**0**. Both the trusted-shell and separate application Environments selected
WebView2 **155.0.4283.62**. The canvas reported its original title, four example
cards, `devicePixelRatio=1.25`, and a 1186 × 707 CSS-pixel viewport; Windows
reported **120 DPI**. The host used Python **3.15.1**, standard-library `ctypes`,
Win32/COM and Microsoft's official loader. It imported no Qt, Tk, CLR or agent
runtime. No global debugging listener was enabled.

The checks exercised real native pointer and keyboard input, not just DOM
mutation. They confirmed launcher focus transfer, app-input suspension during a
modal, card drag, controller zoom, an overlapping canvas note, retained page
identity/edits across workspaces, navigation/back state and closure notification.
The close check establishes notification before controller teardown only; it
does not establish cancellation of an unsaved Office document.

Evidence is in the Windows scratch stage
`C:\Users\Jim.Lundin\AppData\Local\Temp\toolkit-canvas-webview2-1792779155`:

- `run-3/results.json`, `host-events.jsonl`, `loaded-modules.json` and
  `exit-3.json` (at the stage root).
- `run-3/native-overlap.png`: visually inspected native capture with both app
  pages and the foreground canvas note. `native-launcher.png` captures the
  launcher state. Early `native-canvas.png` and `native-app-canvas.png` captured
  initial/prepaint frames; they are not final visual evidence.
- Selected final-run receipts/captures are also preserved under
  `/tmp/toolkit-canvas-webview2/evidence/native-run-3/` without browser profiles.

Earlier failures are retained. Run 1 reached the actual native canvas but its
harness treated a false `SetForegroundWindow` return as failure. The corrected
harness observes the actual foreground HWND and, if necessary, clicks a verified
title-bar target of its own window. Run 2 reached native app input but the old
`keybd_event` Unicode check failed. Run 3 uses normal `SendInput`, records actual
editor focus/content and verifies no stuck Ctrl/Shift/Alt. No Windows security
policy changed. These harness fixes do not hide product failures.

**Input incident correction:** later Office trials initially used global typing
with a foreground check only at the start of an action. The user switched to
another browser during that work. Stray characters reaching that browser cannot
be ruled out; the user and runtime owner were told. Global input was stopped.
The subsequent Office companions and VS Code probes used only their owned
WebView2 frame/CDP interfaces and bound picker inspection. Their
`global_input_used=false` receipts apply only to those later runs, not the entire
investigation. An unchanged foreground HWND is not proof that earlier input was
never misdirected.

### Qt and portable regression evidence

**Three actual Windows Qt compatibility checks passed**, exit **0**, with empty
stderr, on the adapted assets: lazy `qrc:` WebChannel script load renders the
canvas; command/snapshot round trip; and display signal opens/focuses the existing
launcher. Receipts are `qt-bridge-results.json` and `qt-bridge-exit.json` in the
same Windows stage. This checks the production host's bootstrap path before
replacing its assets.

The initial isolated checks separately passed six transport checks, nine pure
fixture/protocol/ABI checks and eight Chromium scenario/comparison checks. The
repository transport suite adds coverage for deferred Qt script loading and
script-load failure: **eight repository transport tests passed**, alongside
**seven existing canvas contract checks**. These counts are separate from the
13 native checks and three actual Windows Qt checks. The transport suite is
runnable independently with the command above.

Canvas and launcher screenshots are byte-identical between the original Qt
bootstrap and the new WebView2 bootstrap **in the same Chromium test browser
using mocked host transports**. This is a visual regression check of the
bootstrap, not a comparison between native engines. `canvas.css`, `shell.css`,
`apps.js` and `meeting.js` match the original SHA-256 hashes; all eight original
asset hashes are in the prototype's `source-manifest.json`.

### One document per card, one profile per account

A card identifies a document and its live view. The account profile is shared
across cards: opening another Word document or Excel workbook must not create a
new login profile. Store the direct document URL and account-profile ID with the
card, and reuse the same app Environment/profile when opening it. Keep separate
work/personal profiles only when requested. The trusted Toolkit shell uses a
separate Environment/UDF and has different privileges. The current Qt owner
already caches `QWebEngineProfile` by `profile_id`; preserve that ownership
boundary when porting it. [Profiles][profiles], [UDF][udf], [security][security]

The prototype currently puts all remote app controllers in one shared
`app-profile` UDF and puts the shell in `shell-profile`. It verifies the native
UDF locations. The user completed and confirmed authentication in the owned
**Toolkit Office trial · sign in** window. Credential values were not inspected.
Subsequent companion hosts reused that app UDF while the original host remained
alive. A generic Excel creation route initially showed a remembered-account
chooser; selecting the already-used account required no password. Later direct
document reopens reached all three editors without a login or account chooser.
That distinction matters: generic app launch and direct document reopening are
different observations.

The fixed companion recipes targeted three newly created trial documents,
registered by immutable SharePoint document ID. They canonicalized each URL to
its document ID and edit action, excluding transient login/session parameters.
Creation stayed in the same view; no popup event was observed. The final fresh
companion (`office-final-15`) concurrently reopened all three documents at
navigation generation 1 and observed the following:

| Registered trial | Independent saved-state observation | Result |
| --- | --- | --- |
| Word A | Filename `Toolkit WebView2 trial 2026-10-23 Word A`; full text `Toolkit WebView2 trial Word A saved content 23 October 2026 proof 7319`; Office status **Sparat**. | Edit/save/reopen passed. Text was entered in companion 9 and independently observed in companions 10 and 15. |
| Excel A | Filename `Toolkit WebView2 trial 2026-10-23 Excel A`; cell **A1** contains exactly `Toolkit WebView2 trial Excel A saved content 23 October 2026 proof 7319`; **Sparat**. | Edit/save/reopen passed. Companion 11 wrote it; companion 15 independently reopened it. |
| Word B | The new document reopened as **T.docx**, containing fragments of earlier trial filename text. | Creation/account reuse passed; intended filename and neutral saved-content check failed. Companion 13's apparent successful rename did not persist. Companion 14 refused further editing when its saved-filename guard failed; companion 15 confirmed the failure. |

All three trial documents are preserved, including Word B's failed state. Exact
private URL registrations remain only under `office-user-sign-in`; authenticated
profiles and private URL receipts are excluded from the repository, evidence
archive and deployment ZIP. The final observation is read-only with respect to
document content. It does not turn the Word B failure into a passing test.

These trials establish account reuse across cards and fresh companion hosts,
with independent server-backed document reopening. The original host kept the
shared browser profile alive until cleanup, so **complete browser-process
restart persistence and separate-account isolation remain untested**.

The later editing recipes used `Controller.MoveFocus`, in-process focus
emulation, frame-scoped inspection and browser input events. Editor readiness
lagged navigation completion; the recipes waited for the correct editor and
filename, with at least five seconds of editor readiness and 15 seconds after
launch. Excel also needed 600 ms after formula-field focus. Word AutoCorrect
changed a spaced hyphen to an en dash in an earlier fixture. DOM editing could
appear correct without updating Office's saved model, as the failed Word B
rename demonstrates. These are scoped scripted-workflow observations;
foreground typing, IME and complete keyboard-routing parity remain host work.

### Minimal document cards and supported ribbon controls

The separate scratch `document-assets` implements an explicit
`application.presentation = 'document'` variant, retaining the canvas design
while showing a 36 px title/focus/menu header. Toolkit's address/navigation bar,
footer, origin subtitle and park button are absent. The body starts immediately
below the header and the application viewport fills it, so the native geometry
publisher receives the reclaimed height. Ordinary application cards retain
their existing browser controls. No URL-based presentation heuristic is used.

**Two portable document-presentation checks passed:** reclaimed viewport bounds
match the published geometry, and application views still suspend beneath the
launcher. Evidence is `evidence/document-mode-browser-results.json` and
`document-mode-browser.png` in the prototype. The final Office companion loaded
these assets with real native views. Production `apps.js` and CSS remain
unchanged; the document variant is a prototype to port with its typed contract.

The final companion opened each application's actual `RibbonModeToggle` and
selected **Visa endast flikar** (Show tabs only). Word's measured ribbon height
fell from about **76 to 36 CSS pixels**. Excel's option changed to
`aria-checked=true`; its ribbon was already about 36 px high under the previous
automatic setting. No Office DOM was removed and no style override was
injected. `office-final-15/ribbon-results.json` records the menu choices and
before/after states. Restoring controls and persistence after a full restart
were not exercised. The supported options and their limits are documented in
[document and editor surfaces](document-app-surfaces.md).

### VS Code web: loaded; local file workflow incomplete

`vscode-run-1` loaded the actual `https://vscode.dev/` workbench in an independent
native app card/profile and captured its preview. File, directory and save
picker APIs were present. Later probes used the visible **Open File...** command
through target-scoped browser mouse input. Runs 5 and 6 opened the browser's
**Välj en fil som den här webbplatsen kan läsa** dialog. The dialog belonged to
the verified WebView2 browser process, not the Python host, and was found through
that process/owner chain.

The only intended file was the disposable
`C:\Users\Jim.Lundin\Downloads\Toolkit-VSCode-trial-20261023-a1be1735\canvas-trial.txt`.
Bound UI Automation verified the dialog HWND, PID, title, class, host command and
app-profile identity. In this non-activating/offscreen trial, filename controls
with automation ID `1148` and the **Öppna** button appeared as `ControlType.Pane`
and exposed no Value/Invoke patterns. No file was selected. Run 6 reached its
120-second `opening_file` deadline and closed normally; expected saved bytes
were absent. **Edit, save, fresh-profile reopen, folder access and Zen mode are
not passed.** This is a specific automation gap, not evidence that a human
cannot use the picker or that VS Code cannot save in WebView2.

Runs 2–4 retain earlier incomplete/failed attempts: an unused Temp destination,
an Open File command attempted before welcome controls were ready, and a
Ctrl+O attempt that did not open the picker. No alternative editor server was
installed. Plain VS Code web does not establish an OS terminal, native debugger
or desktop-extension support; those require a separate execution environment.

### Probe closure and the next production slice

After saved-state verification, the original Office host received guarded
`WM_CLOSE` for its recorded PID **5476**, HWND **31853588** and exact title. All
owned probes closed normally. The scoped Windows process audit at
**2026-10-23 20:08:07 UTC** found **zero remaining** probe Python/WebView2
processes. UDFs, trial documents and the scratch file were retained. The receipt
is the stage's `cleanup.json`; selected sanitized results and its SHA-256 are
included in the prototype evidence manifest. No further desktop trial is part
of this evidence freeze.

The next production slice is to replace the Qt canvas host/view owner with a
WebView2 adapter against the existing store, typed commands and geometry
contract. It must preserve profile ownership, origin/navigation guards, focus
and launcher routing, popup/permission/download handling and unsaved-close
vetoes. Port the explicit document presentation with its viewport geometry, then
exercise real save/recovery workflows rather than treating DOM changes as save
receipts. Other Qt windows and runtime packaging have their own remaining
owners. The runtime lane performs the joint Windows release checks and Downloads
tree/ZIP deployment; this isolated proof does not silently replace production.

### Ownership and reusable host

The isolated reusable host is `native/win32_webview2.py`:
`Host(LocalDocument, loader, data, title, receive_json)` with `post`, `defer`,
`close` and `run`. `canvas_probe.py` owns canvas-specific view geometry;
`probe_state.py` owns pure, disposable fixture transitions. Fixture results are
not production backend/save receipts.

The UI lane owns canvas presentation, transport, WebView2 view/input/lifetime
ownership and application geometry. The runtime lane owns Pi Durable, Windows
capabilities, backend contracts and runtime provisioning. It can adapt the same
reusable host to its Pi bundle while the UI migration preserves the canvas.
The frozen reusable host SHA-256 is
`fc68cd9a9489c6254fb4ca2adcbcaca950a01875506503032fac880687356f19`;
its `abi.json` SHA-256 is
`35bcc78435431ecb8c62ab8e4034b5a7a9946812ec13534a9bf05ab6438ab72e`.

## Small ctypes ABI reference

Source: Microsoft SDK `1.0.4314.35`,
`/tmp/toolkit-pi-webview-spike/host/vendor/webview2/build/native/include/WebView2.h`.
Slots below are zero-based, including `IUnknown` slots 0–2. Query the required
interface; do not assume that a newer interface pointer has been returned.

| Interface | Slots used by the first host |
| --- | --- |
| `ICoreWebView2Environment` | `CreateCoreWebView2Controller` 3; `get_BrowserVersionString` 5. |
| `ICoreWebView2Controller` | `get/put_IsVisible` 3/4; `get/put_Bounds` 5/6; `get/put_ZoomFactor` 7/8; `SetBoundsAndZoomFactor` 11; `MoveFocus` 12; `NotifyParentWindowPositionChanged` 23; `Close` 24; `get_CoreWebView2` 25. |
| `ICoreWebView2` | `get_Settings` 3; `get_Source` 4; `Navigate` 5; `add_NavigationStarting` 7; `add_NavigationCompleted` 15; `add_ScriptDialogOpening` 21; `PostWebMessageAsJson` 32; `add_WebMessageReceived` 34; `add_NewWindowRequested` 44; `add_WindowCloseRequested` 59. |
| `ICoreWebView2_3` | IID `A0D6DF20-3B92-416D-AA0C-437A9C727857`; `SetVirtualHostNameToFolderMapping` 71. |
| `ICoreWebView2Settings` | `put_IsWebMessageEnabled` 6; `put_AreDefaultScriptDialogsEnabled` 8; `put_AreHostObjectsAllowed` 16. |
| Message / navigation args | Message `get_Source` 3, `get_WebMessageAsJson` 4; navigation `get_Uri` 3, `put_Cancel` 8. |

Use Windows ABI types and calling conventions, an explicit `this` pointer,
32-bit `HRESULT`/`BOOL`/`LONG`, pointer-sized HWND/interface pointers and a 64-bit
`EventRegistrationToken`. `put_Bounds` and `SetBoundsAndZoomFactor` take `RECT`
**by value**; `get_Bounds` takes `RECT*`. Callback `Invoke` is slot 3: creation
callbacks take `(HRESULT, interface*)`; event callbacks take `(sender*, args*)`.
Keep Python callback functions, interface storage and vtables alive while COM
holds them, implement reference ownership, and free returned LPWSTR strings
with `CoTaskMemFree`. Keep all controller work on the STA/message-pump thread.
[API conventions][api], [threading][threading]

[local]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/working-with-local-content
[mapping]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2_3#setvirtualhostnametofoldermapping
[core]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2
[messageargs]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2webmessagereceivedeventargs
[security]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security
[controller]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller
[scale]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller3
[region]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowrgn
[composition]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2compositioncontroller
[drops]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2compositioncontroller3
[dcompclip]: https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositionvisual-setclip(constd2d_rect_f_)
[geometricclip]: https://learn.microsoft.com/en-us/uwp/api/windows.ui.composition.compositiongeometricclip
[profiles]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/multi-profile-support
[udf]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder
[popup]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2newwindowrequestedeventargs
[dialogs]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2scriptdialogopeningeventargs
[permissions]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2permissionrequestedeventargs
[enums]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/webview2-idl#corewebview2_permission_kind
[downloads]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2downloadstartingeventargs
[threading]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model
[cdp]: https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json
[sso]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2environmentoptions#get_allowsinglesignonusingosprimaryaccount
[external]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2_18#add_launchingexternalurischeme
[handles]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2environment14
[objects]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2_23#postwebmessageasjsonwithadditionalobjects
[distribution]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
[processes]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model
[api]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/win32-api-conventions
