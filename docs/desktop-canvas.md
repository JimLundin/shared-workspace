# Toolkit desktop and shared canvas

Toolkit's native desktop adds a top bar, resource launcher and saved workspaces.
It renders the connected-work sketch's HTML/CSS in Qt WebEngine on the assistant's
main thread, with the existing Windows bindings. The
keyboard remains a separate process; the canvas does not install a keyboard hook.

Open **Workspace canvas** from the Toolkit tray or press **Ctrl+Alt+D**. Open the
launcher with **Ctrl+Alt+Space** globally or **Ctrl+K** inside the canvas. The top
bar also opens the launcher, canvas, conversation, work overview and meeting
recording. Its BASE/TOOLS/APPS indicator follows the existing keyboard layer.

## Working on the canvas

Use the launcher to add an open window, a saved note/result, or a connected Edge
page. Application entries open or switch to the configured Apps-layer programs.
**Connect Edge** attaches to the signed-in browser through Toolkit's existing
browser adapter; a selected page becomes a readable excerpt with its origin.
Opening the desktop does not attach to Edge.

Add files with **Add source → Add a document** or drag them onto the canvas. UTF-8 text, Markdown, CSV
and TSV have readable canvas views; other documents retain an original-file
link. **Add source** also offers a named clipboard snapshot and an editable note.
CSV/TSV cells are observations; editing the source remains an application action.

- Drag a card's header to arrange it. Drag empty space to pan. Ctrl+wheel zooms.
- **Canvas** restores the working arrangement and camera. **Overview** / **0**
  fits visible cards. **Focus** fits the selection. Escape returns from focus,
  then hides the canvas. Drag a card's lower-right corner to resize it.
- **Use as context** includes an item in the next request. The composer lists the
  selected items. Selections carry exact observed contents and versions.
- Hold **N** to speak about the selection while the canvas or one of its selected
  app windows is active. A pending conversation question takes priority. **B**
  keeps dictating into the focused text field.
- Result cards show progress, questions, approvals, saved results and expandable
  steps. **Stop** addresses the corresponding durable task. The bar's Stop
  retains the existing conversation-wide behavior.
- A submission that cannot be confirmed stays attached and shows **Retry
  submission**. Retry uses the original task identity, prompt and source
  snapshots, including after a restart. It cannot attach a different same-ID
  task or approve, decline or stop that unrelated work.
- **Review action** opens the existing native Work view for the full approval
  preview. The canvas does not provide a shortcut around that review. Questions
  can be answered on their result card; reply failures remain visible.
- **Sources** shows the observations used, even after their originals change.
  **Read again** updates an app/file observation. A changed source marks derived
  results outdated; **Refresh** deliberately creates a new request for the same
  result card.
- **Make draft** copies an output into an editable card and records its origin.
  Later results preserve its text, selection and undo history. Copy, export, or
  **Save email draft** continue that draft outside Toolkit. A save freezes its
  exact revision and evidence, then verifies an unsent Outlook draft or a local
  EML file. Its receipt names the saved revision; later edits remain distinct.
- **Park** removes a card from view and selection; **Parked** restores it at its
  previous position. Each workspace retains its arrangement, zoom and unfinished
  composer text. Hidden and parked work continues in the backend.
- **Remove from this workspace** keeps an item available in the launcher.
  **Delete saved canvas item** removes it from every workspace and frees space
  in the saved collection. Running results must be stopped first. Existing
  results and drafts retain their quoted source snapshots.

Native application windows have DWM previews and **Open original**. Their own
input, dialogs and accessibility remain in the native app. **Arrange apps**
places one to three selected windows beside the canvas. The bar menu restores
their prior positions. Toolkit does not reparent arbitrary cross-process
windows into Qt widgets.

## Web apps in the canvas

Choose **Add source → Open a web app**, enter an HTTP or HTTPS address, or use
the **Microsoft 365** and **VS Code** shortcuts. The application accepts clicks
and typing inside its card. Its address bar provides Back, Forward and Reload;
**Open in external browser** opens the address outside Toolkit.

**Ask agent** opens the existing ordinary conversation with access to registered
live app tools. It does not prefill a request for the clicked card; write the
request in the conversation. The canvas composer keeps its explicit
captured-source scope.

Sign in normally inside Toolkit. Web-app cards use Toolkit's own persistent
browser profiles; they do not inherit personal Edge cookies or credentials.
Parking a card, switching workspaces, hiding the canvas or ending an agent task
retains the live page. The saved card and browser profile can reopen after a
restart, but profile persistence does not guarantee recovery of unsaved edits.

Downloads ask where to save; uploads use the native file chooser. Site
permission prompts identify the requesting origin and default to **No**.
Requests to access a chosen file also name its path and ask before allowing
the requested read or write. Browser restrictions on file access still apply.

**Use page as source** captures a separate observation of the current main
document. It retains the document identity, URL, observed text, content version
and coverage, measured in UTF-16 units. A partial capture is labeled as an
excerpt. Later navigation or editing leaves that source and its quoted evidence
unchanged. **Show app** can return to the originating page only while that page
instance still exists. After restart or page recreation, select the current
app card and capture again for a new observation. Selecting the live card alone
supplies its saved reference, not its unread page contents.

Microsoft sign-in and Office web behavior depend on the account, tenant policies
and browser support. Qt WebEngine is not listed among Microsoft's
[browsers for device-based Conditional Access](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-conditions#supported-browsers).
Anonymous smoke checks rendered the Microsoft 365 landing/sign-in UI and the
VS Code workspace shell. They do not verify account sign-in, tenant policies,
authenticated Office editing or autosave.
[VS Code for the Web](https://code.visualstudio.com/docs/remote/vscode-web)
supports browser editing; a terminal, debugger and extensions that need a runtime
require a backend such as Codespaces or Remote Tunnels. Its compatibility with
Toolkit must be checked for the features in use.

## Connected work and meetings

**All work** lists observed tasks. **Pin result** adds a confirmed completed
result without starting another run. Existing source evidence is retained; work
without original source snapshots says so explicitly.

**All work → Meeting workspaces** opens a specific saved package. Its canvas
contains transcripts, cited notes and decisions, actions, the task, and unsent
drafts. Source buttons show the exact quoted passage. **Review & edit** saves a
full draft or action through the existing meeting workflow; only its matching
saved receipt resolves the pending edit. Unconfirmed packages remain readable
but cannot accept changes. **Overview** includes all cards, including drafts to
the right of the source group.

Browser source footers distinguish main-document text from a partial excerpt.
The captured tab binding, URL, text scope, and version stay attached to its
source and evidence. A closed, navigated, or reconnected tab requires an explicit
new selection; refreshing does not silently pick another tab at the same URL.

The workspace menu also offers **Explore the example**, an explicitly labeled
local example using the same layout as the design sketch. Its sources are real
bundled files and its initial comparison is sample content.

## Taskbar mode and recovery

The top bar reserves space using the Windows appbar interface. Initially it
coexists with Explorer. Choose **Use Toolkit as the taskbar** in the bar's menu
to temporarily hide Explorer's taskbars. Turning it off or closing Toolkit
restores them. A small companion process restores the hidden taskbars if the
assistant exits unexpectedly. Toolkit waits for the companion to confirm that
it is watching the assistant before hiding Explorer. This does not change the
Windows shell registry. Launchers such as WSL can prevent an independent helper
from starting. In that case Toolkit leaves Explorer visible and explains that
taskbar replacement requires opening Toolkit directly from Windows. The top
bar, launcher and canvas remain usable.

`TOOLKIT_DESKTOP=off` disables automatic desktop startup. Existing conversation,
keyboard layers, tray and recording remain available in that configuration.

Arrangements, web-app configuration and local text live in
`usage.DIRECTORY/desktop/canvas.json`, written atomically. Native HWNDs are never
saved; native window cards require an exact app/title match or an explicit new
selection. Web-app cards save their URL and profile ID; agent control obtains a
fresh target from the reopened page. Corrupt or newer-version files are
preserved, and a recovery session saves to a separate file.

## Backend integration

`assistant.canvas_model` contains immutable resource, evidence, request and result
variants. Its functions decide selection, placement, edits, staleness and result
acceptance without clocks, files, Qt or Windows calls.

`assistant.canvas_controller.Backend` is the integration interface:

| Operation | Contract |
| --- | --- |
| `connect(publish)` | Install the event receiver before restoring attachments. |
| `submit(Request)` | Queue an identified request promptly; sources are exact immutable observations. Raise if it cannot be queued. |
| `attach(Request)` | Observe an already-issued durable identity after a UI restart. Do not submit it again. |
| `cancel(request_id)` | Address only that task; late events no longer replace its card. |
| `answer(request_id, question_id, text)` | Queue an answer to the exact current question once; return whether accepted for processing. |
| `presented(request_id, Approval)` | Acknowledge the exact preview after it is displayed; stale or disconnected previews cannot authorize an action. |
| `CanvasController.publish(Event)` | Publish progress, pending input, completion or failure from any thread. Only a matching running request can change its result. |
| `CanvasController.publish(Item)` | Publish a validated source observation under a stable resource ID. It cannot replace a local draft, note or result. |

`assistant.canvas_agent.WorkBackend` implements this interface using
`WorkAgent.submit_canvas(Request)`, cached immutable snapshots and existing work
commands. The exact source preparation is retained before a task can execute.
Canvas execution uses that captured context without inheriting ambient windows,
project notes, or an unrelated conversation history. Request
IDs are durable task IDs. Only a committed `Completed` task completes a result;
switching conversations cannot move an answer to another card. A saved running
card reconnects by ID. `WorkAgent.watch` retains those identities beyond the
recent-task page. `WorkSnapshot.submission_problems` takes precedence over a
same-ID row; the canvas shows nonterminal attention and retains its attachment
until the backend confirms the request. Retrying preserves the original source
observations in the work owner. Questions and current approval reviews
retain their backend validation. Reply failures carry per-attempt receipts in
`WorkSnapshot.reply_problems`; a receipt can reopen only its still-current input
and is consumed once. A snapshot update alone is never treated as a failed reply.
The existing Work overview provides pause/resume, routines and context
management. New canvas scope is submitted through the canvas composer or result
refresh; generic Work follow-up and redirect are disabled for captured requests.

Ordinary worker tasks can operate registered live web-app documents through
these tools. Captured-source tasks keep their existing immutable scope and do
not receive live app control.

| Tool | Contract |
| --- | --- |
| `browser_app_list` (`app_list`) | List saved cards and their current loading state; ready pages expose an exact document `target_id`. |
| `browser_app_open` (`app_open`) | Open and save an HTTP/HTTPS app card in the current workspace. A saved card may still be loading. |
| `browser_app_navigate` (`app_navigate`) | Navigate an exact current document; obtain its replacement target before further reads or actions. |
| `browser_app_snapshot` (`app_snapshot`) | Read bounded, paginated text and controls from that exact document. This does not create or alter a saved source card. |
| `browser_app_action` (`app_action`) | Click, fill, select or check a control from the current snapshot. Take a fresh snapshot to verify the result. |

`AppBrowserControl` binds these typed operations to the current worker run.
`WorkService` validates run ownership, expiry and request identity; `WorkAgent`
delivers the request through `UI.browser_app` to the Qt owner and returns its
matching completion once. Navigation and page recreation retire document
targets. A lost acknowledgment is not permission to replay an action.
`browser_app_open` is verified only by a matching saved-card receipt containing
the card ID and requested URL. Queuing an open is not a saved receipt, and saving
the card does not establish sign-in or successful edits in the remote service.

`assistant.browser_surfaces.BrowserSurfaces` owns a separate top-level
`QWebEnginePage` and view for each live card, with persistent remote profiles.
Remote pages do not receive the local shell's profile, resource interceptor or
privileged `QWebChannel`. The shell reports clipped card geometry and stacking
in logical coordinates; Qt owns the live widgets and their input. Typed app
operations use registered page objects through supported Qt APIs. They do not
enable a global CDP listener in the privileged shell process or expose arbitrary
scripts and debugger targets to callers.

`assistant.canvas_native` owns window discovery/focus/arrangement, local document
reads and the attached-browser session. `winapi.desktop_shell` owns DWM thumbnail,
appbar and hotkey resources. Clipboard reads, painting, editing and Qt lifetimes
stay on the UI thread. Slow resource operations and persistence have separate,
bounded queues. The assistant owns QApplication on Python's main thread for its
entire UI lifetime; canvas and conversation views share the Windows composition
dispatcher. A separate control thread owns the hidden Windows message window
and its message loop.

Excel range writes and Teams message projections can publish the same
Source/Table variants. These are adapter work;
the current native UI does not simulate their data or claim a successful write.
Native window previews, live web-app cards, file observations, connected Edge
excerpts and local drafts are the concrete paths implemented here.

The HTML bridge and live renderer are checked in `tests/test_canvas_web_contract.py`
and `tests/test_windows_canvas_web.py`, alongside the portable model/controller
checks in `tests/test_canvas.py`. Legacy widget checks retain their separate scope.
The portable checks cover immutable evidence, refresh and draft preservation,
late-event rejection, saved identities, retry recovery and approval matching.
Native checks exercise the actual Chromium canvas, pointer dragging/resizing,
workspace drafts, meeting edit receipts, and renderer cleanup. Windows adapter
checks also cover DWM resource release and the recovery handshake.
They use isolated storage, owned fixture windows and synthetic widget events;
they do not connect to personal apps, send foreground input, or hide Explorer.

Browser-card checks add saved profile/configuration, exact document targets,
capture coverage and cancellation/reply handling in the portable tests. Native
owner checks in `tests/test_windows_browser_surfaces.py` use local fixtures for
real page input, popups, close handling, permissions, chosen-file transfers and
Qt lifetimes. Anonymous public-site smoke checks are separate; they do not
certify authentication or a document-editing workflow.
