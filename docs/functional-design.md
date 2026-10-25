# Functional design

Toolkit models domain alternatives like Rust enums: a union of frozen, slotted
dataclasses for variants with payloads, and `Enum` for variants without payloads.
`match` and `assert_never` make handlers explicit and let the type checkers find
missing cases when a union grows. `Literal` describes fixed external values.
Decisions are pure where practical, with effects owned by small controllers.

The main places to read are:

| Module | Pure model and decisions | Effect owner |
| --- | --- | --- |
| `assistant/llm.py` | System, user, assistant, and tool-result message variants; request serialization | HTTP client functions |
| `assistant/agent_session.py` | Typed events, messages, questions, approvals, session transitions, and presentation | `agent_window.py`, `agent_input.py`, `agent_toasts.py`, and `agent_panel.py` |
| `assistant/agent_runner.py` | Tasks, FIFO queueing, completion, and stopping transitions | `AgentRunner` owns threads and views; `agent_execution.py` owns SDK history and turn cleanup |
| `automation/task.py`, `store.py` | Durable task, wait, reply, action, and run variants; controls and stale-run decisions | `TaskStore` owns SQLite transactions and validates stored records |
| `automation/controller.py`, `execution.py`, `actions.py` | Work outcomes, recovery context, action classification, and verification decisions | `WorkController` owns execution lifetime; `Actions` owns cancellable effect coordination |
| `automation/routines.py`, `context.py` | Trigger occurrences, accepted handoffs, context relevance and freshness | The routine/context stores own persistence; `meeting_events.py` owns transcript delivery and startup scanning |
| `assistant/work_window.py`, `work_agent.py`, `work_service.py` | Work commands, immutable projections, conversation/task selection and run-scoped RPC acceptance | `WorkAgent` owns frontend observation and asynchronous UI delivery; `WorkService` composes journal/transport calls and RPC replies; `WorkWindow` schedules its native view |
| `assistant/canvas_model.py`, `canvas_controller.py`, `canvas_work.py` | Resources, source observations, requests, results, selection, placement and saved-work pinning | Canvas adapters own native resources and persistence; the local HTML page renders through a validated Qt WebEngine bridge |
| `assistant/canvas_requests.py`, `canvas_execution.py` | Captured request identity, prompt compilation and execution scope | `CanvasRequests` commits preparations before work submission; `AgentExecution` verifies each run and owns separate task history |
| `assistant/browser_surface_values.py`, `browser_surface_operations.py` | Live page states, exact document targets, viewport/close/capture decisions and bounded app operations | `browser_surfaces.BrowserSurfaces` owns Qt pages, profiles, input and clipping; `browser_surface_control.AppBrowserControl` borrows typed RPC for a worker run |
| `assistant/page_source_values.py`, `page_sources.py` | Exact browser references, UTF-16 text coverage and content provenance | `PageSources` borrows the explicitly connected browser; `BrowserSurfaces` captures its registered pages; `NativeResources` owns canvas resource I/O |
| `assistant/canvas_drafts.py` | Accepted draft revisions, source evidence and per-attempt receipts | `CanvasDrafts` retains exact preparations before asking `OutlookDrafts` to save and verify unsent mail |
| `assistant/meeting_canvas.py`, `meeting_web.py` | Versioned meeting cards, citation links, edit validation and JSON projection | The existing `MeetingWorkflow` owns reads and saves; the canvas observes the same snapshots and receipts |
| `automation/ipc.py`, `bridge.py` | Validated process messages and bounded event projections | Bridge client/server own authenticated byte transport; `automation/app.py` composes the native worker |
| `assistant/action_text.py` | Readable action targets and results, formatted before preview limits | `agent.py` publishes previews; session presentation handles raw payloads too |
| `assistant/notifications.py`, `notification_text.py`, `notification_reading.py`, `toast_style.py` | Grouped turns, attached results, optional steps, reading states, lifetimes, tints, and motion sampling | `agent_toasts.py`, `toast_card.py`, `qt_text.py`, `qt_ui.py`, `winapi/composition.py`, `winapi/appearance.py`, `winapi/notification_input.py` |
| `assistant/browser_values.py`, `browser_session.py`, `browser.py` | Browser ownership, immutable page/tab/group observations and command variants, group selection, typed paginated projections | `Browser`, `NativeSessions`, `Cdp`, `browser_tabs.TabStrip` and a cancellable connection wait |
| `assistant/recording_state.py` | Audio batches, stored observations, completion rules, retry policy, stale-update rejection, and recording controls | `meetings.py` and `meeting_store.py` |
| `assistant/meeting_followup.py`, `meeting_requests.py` | Frozen transcript bundles, exact citations, sourced actions and drafts, preparation identity | `meeting_followup_store.py` owns verified publications and revision-checked edits; `MeetingRequests` owns saved preparations |
| `assistant/meeting_workflow.py` | Catalog, work and package observations; identified edit commands and receipts | `MeetingWorkflow` owns background I/O; `meeting_panel.py` owns Qt selection, local edits and explicit handoff |
| `assistant/meeting_execution.py`, `meeting_tools.py`, `outlook.py` | Constrained meeting execution, bounded source reads and verified draft outcomes | The work process owns generation; `outlook.ps1` owns Outlook COM observation and draft operations |
| `assistant/teams.py` | Frozen device flow, polling deadlines, and retry intervals from clock observations | `Teams`, `SystemClock`, `TokenStore`, and `graph_http.Http` |
| `runtime.py` | Bootstrap handoff plan and dependency ABI validation | `execute_bootstrap`, `bootstrap`, and `activate` |
| `assistant/activity.py` | Bounded window history reduced from a window or desktop-state observation | `ActivityTracker` and the Windows foreground reader |
| `assistant/geometry.py`, `panel.py`, `rich_text.py` | Geometry, preferences, and Markdown values | Qt views and preference files |

Session events include their timestamps. IDs and clocks are supplied by the
controller or input broker, so a sequence of events can be replayed or branched
without changing its source state. Views receive the immutable session itself.
Questions carry a pending, answered, or cancelled reply; approvals carry a
decision. A reply for the wrong request kind or an already resolved request
leaves the session unchanged.

The legacy task controller accepts an execution adapter and a window factory. Its
immutable `Active` state carries the current task and pending tasks with their
invocation context; `Stopping` discards the queue until completion. The execution
adapter owns mutable SDK history, workspace, telemetry, and cancellation cleanup.
Each run has its own completion event, including its completion callback, so a
later startup cannot observe the previous worker's completion. Application
composition supplies the concrete machine, native view, and observation reader.

The application now submits durable work through `WorkAgent`. Conversation
identity stays separate from task identity. UI callbacks enqueue commands;
one frontend observer commits them and publishes immutable snapshots. Neither
closing a view nor resetting its conversation deletes a task. The keyboard
still communicates only with the assistant and imports no automation module.

Saved canvas cards subscribe by task identity through a bounded replacement set.
Subscriptions and snapshot reads perform no caller-side journal I/O. The
observer merges watched tasks with the recent overview. Failed reads retain
the last known tasks and publish explicit unconfirmed submissions; they cannot
establish that a commit never happened. Exact retries preserve the original
request context, and a conflicting identity cannot control an unrelated task.
Reply failures carry their task, question, and attempt identities. A fresh
journal observation must confirm that exact question is still waiting before
a view reoffers it; an old receipt cannot resolve a later attempt.

Canvas requests carry an explicit `CanvasSource` whose fingerprint binds the
captured evidence, compiled prompt and preparation recipe. Preparation commits
before the task becomes eligible. Each run and resume revalidates that binding;
missing or mismatched preparations suspend execution. These tasks have separate
history and use only selected observations and recorded answers. They receive
no ambient window, screenshot, shared conversation or saved project context,
and expose only question and task-status tools. Generic follow-up and redirect
controls cannot turn them into ordinary contextual work; the canvas composer
or result refresh creates a new captured request. Work schema version 2 retains
version 1 rows and marks the new source alternative without rewriting tables.

Pinning completed work never submits another run. Available original evidence
stays attached, and an unavailable source history is identified explicitly.
Existing results and edited drafts retain their snapshots when later work
completes. Browser observations retain an exact connection, tab, URL and text
coverage; a lost connection or navigated tab requires a new explicit selection.
Failed reads keep the previous content, and late reads cannot replace a source
whose identity or revision has changed.

Live browser cards save `WebApp(url, profile_id)` independently of their Qt page
lifetime. A ready `BrowserTarget` identifies the card, page instance and current
document; navigation or recreation invalidates that target. `ViewportFrame`
acceptance checks workspace, epoch and sequence before changing visible
surfaces. The Qt owner retains pages while hidden or parked, with remote
profiles separate from the local shell and no shell `QWebChannel`. It uses
supported page APIs without enabling a global CDP listener.

Ordinary work borrows `AppBrowserControl` through `CompositeBrowser`; only
`app_list`, `app_open`, `app_navigate`, `app_snapshot` and `app_action` reach the
registered cards. The public tool names have the `browser_` prefix. Operations
and IPC replies are validated immutable values. `WorkService` checks the current
run, expiry, worker epoch and request identity; `WorkAgent` owns asynchronous
delivery to `UI.browser_app` and consumes its matching result once. Stopping or
losing the request retires that control without closing the user's pages. A
queued open is not proof of persistence: `browser_app_receipts.opened_card`
requires a matching saved-card receipt, independently of page readiness.

Capturing a live page creates a separate `BrowserKind.TOOLKIT` source with its
exact binding, document, URL, content version and UTF-16 coverage. Stored text
must match that provenance; a partial excerpt cannot claim a whole document.
Later live edits leave saved evidence unchanged. Captured-source tasks do not
borrow app control, and refreshing a Toolkit capture cannot fall back to an
external browser tab. A new observation requires another explicit capture.

Canvas mail handoffs freeze the accepted draft revision and evidence before
queueing. The owner persists that exact version before the mail effect, while
the canvas can continue accepting edits. Native or file readback establishes a
saved unsent draft; a queued attempt or lost acknowledgment does not. Retries
inspect the original binding, and later mail edits are preserved. Meeting cards
reuse the existing versioned package and edit owner, with explicit package
selection and matching save receipts; their generated revision 0 is independent
of canvas item revisions.

The separately supervised work process acquires exclusive ownership before
startup recovery. `TaskStore` atomically claims one task, and its `RunRef` fences
progress, answers, and completion. A pause or cancellation retires the claim
immediately; the controller waits for that execution's cleanup before starting
another. Shutdown parks work as interrupted. Live endpoint failures wait for
explicit resume so one unavailable task cannot spin or hold up unrelated work.

Consequential tools record an attempt after validation and any required
approval, immediately before their effect. The journal retains fingerprints
and bounded outcomes, without raw tool arguments or SDK histories. File writes
can be checked by destination and expected digest. Other unknown effects require
fresh observations before reconciliation. Completion checks the full action
record, independently of the UI's display page. A late callback cannot revive a
cancelled task or replace an already verified action with a weaker outcome.

Questions and redacted approval requests are durable. Full approval previews
remain volatile, tied to their run, goal revision, exact operation, and request
identity. A view must display the matching preview before approval is accepted.
After a lost preview, refreshing creates a new request identity. Local IPC uses
authenticated, bounded JSON byte frames with worker epochs and event cursors;
it never deserializes Python objects. UI, microphone, meeting capture and live
web-app pages remain with the assistant. The worker owns Teams, native actions
and its external-browser session; web-app operations cross typed RPC.

Standing instructions start disabled. An atomic accepted handoff separates an
authorized occurrence from an unaccepted one that disable or edit can invalidate.
Retrying a handoff submits the same routine occurrence, and missed intervals
coalesce. Transcript delivery follows final successful export, independently
of which recording owns the UI. Startup scanning repairs missed delivery and
baselines pre-existing completed transcripts on first installation. Saved
preferences and expiring project notes are explicit; fresh retrieval excludes
expired text while retaining its source for reinspection.

Live replies and reasoning summaries carry an identity and stream state; tools
carry a working, succeeded, failed, or cancelled outcome. The session replaces
these values in place within its immutable message tuple and preserves each
entry's timestamp. Completed entries reject late updates. The SDK boundary
owns the mutable text fragments and only publishes public response text and
reasoning summaries. Request-bound publishers reject events from an earlier
turn or a reset conversation before they reach the pure transition.

Notifications retain the last observed content separately from the visible
stack, so an expired reply does not reappear on an unrelated session update.
Current conversation entries stay while work or pending input is active;
completion starts monotonic reading timers. Hovering preserves their remaining
duration. Overflowing entries use `Held.READING` until their last line is shown,
then receive the ordinary expiry delay. `Waiting`, `Scrolling`, `Paused` and
`Finished` carry a pixel offset; time, content height and reading pace enter a
pure transition. Replies take turns, and waiting replies retain their lifetime.
Reduced motion advances pages with an overlapping line and keeps a manually
chosen starting position. None of these presentation transitions remove conversation messages.
`Entry` uses the session index as its stable key; an answered question adds a
`Response` keyed by that question's index. `Turn` groups these immutable entries
under a stable `TurnKey(request)`, immediately after its user request. Its state
is `Working(generation, phase, queued)` or `Settled.FINISHED | STOPPED | ATTENTION`.
The `Busy` enum distinguishes thinking, waiting, and stopping. `results` keeps
the latest reply and pending input attached; `steps` contains earlier replies,
resolved questions and action activity. An answered question stays with its
response until another reply supersedes it. Follow-ups cannot acquire an earlier
turn's entries or revive its expired terminal state. `notification_text` formats
the attached result and expanded steps separately. Reasoning summaries never become notification text or
trigger text layout. Action previews format complete payloads before shortening
them, omit transport IDs and search controls, and leave the model's tool results
unchanged. Both conversation views use the same readable presentation.

Qt owns the assistant's main thread, its expiry and animation callbacks, and all
assistant windows. A separate control thread creates the hidden Windows message
window and runs its message loop. `assistant/qt_ui.py` shares Windows composition between
canvas and notification views without tying the application lifetime to either.
`keeper_ui.py` owns the keeper's separate protected Qt event loop. The bundled
interpreter excludes Tcl/Tk; no GUI objects cross the keyboard process boundary.

Meeting follow-through shares the work journal and selected saved transcript
versions. Its owner queues catalog reads, preparation and revision-checked edits
off the UI thread, and closes before WorkAgent. The Meetings view retains raw
editor buffers by task, package version and artifact. Only the matching save
receipt clears a submitted edit; newer typing remains against the acknowledged
revision. Multiple saved packages require an explicit choice when no selection
is retained. Opening an email uses the saved draft file, after local edits have
been saved or explicitly reloaded.

The owner animates at most five cards, retargeting from current geometry. Each
notification fits its rendered content up to 360 logical pixels (280 for user
requests), independent of the history window. Headers, the stop icon and voice
hints contribute to that width. The pure `allocate` function reserves readable
previews and gives pending questions and recent messages the remaining height.
On very small work areas it removes older entries from the projection.
`qt_text.Document` measures Markdown using separate `QTextDocument` instances,
with the same fonts, paragraph margins and wrapping as the visible text. A line
that wraps at the maximum width already needs that width; shorter lines get an
exact pixel measurement. No visible window is resized for measurement.

Steps, result, header and footer get separate rectangles; viewports end on
complete display lines. All reply text remains in the native text document,
with no character cutoff. The result and steps have independent scroll positions
and small overflow markers. Steps use smaller type and a 200-logical-pixel
height budget, reserving room for the attached result. Collapsing only changes
the steps' viewport; the result retains its widget, selection and reading
position. `Frame` carries top, width, height, opacity and steps height together.
Every active card retargets on the same clock, so growing and shrinking neighbors
retain their gaps. Native positioning anchors the right edge; the text wraps at
its destination width, and the stop icon follows the visible right edge.
Unchanged frames do not repeat native movement after fractional DPI rounding.

`rich_text.changed_tail` preserves the unchanged styled prefix, including a
partially expanded span. Qt cursor offsets count UTF-16 units, so astral emoji
and closing Markdown preserve earlier text and selection. The display cursor
stays at its existing position when text arrives; new replies start at the top.
Recording previews explicitly follow their newest line after Qt resolves layout.
New reply text gets a bounded set of short-lived extra selections to reveal its
ink; later packets do not dim earlier text. Motion uses a 280 ms base and 160 ms
fast duration with `cubic-bezier(0.32, 0.72, 0, 1)`. Keyboard disclosure is immediate.
The frame timer accounts for drawing time and requests Windows' finer timer
resolution only while movement, text reveals or smooth reading run. Reduced
motion, opening history and closing release that request. A shared callback
rotates existing spinner shapes without measuring text again.

Qt supplies a per-pixel transparent foreground. `winapi.composition` uses
win32more bindings to create a separate, nonactivating, input-transparent HWND
directly below it. A Windows host backdrop brush is clipped to a rounded
rectangle with a 26-logical-pixel radius. A matching visual-surface mask supplies
the native soft shadow. The layer follows the foreground's final physical
rectangle after Qt paints, including at fractional DPI; it updates geometry only
when size or scale changes. Rendering uses neither a color key nor desktop images.
The solid fallback has the same rounded shape. A 1/255 foreground alpha gives
empty areas and icon padding real hit targets; rounded corners and the shadow
pass clicks through. The stop icon adds no background in any state.

Blue user and lilac assistant tints use 22% opacity over uniform backgrounds;
foreground ink remains opaque. `Frost.LIGHT` uses light ink over dark backgrounds,
`BRIGHT` uses dark ink over light backgrounds, and `SMOKED` uses an 82% tint for
mixed or unavailable observations. Captions remain inside the same surface.
High-contrast mode uses the system foreground and background colors; disabling
Windows transparency selects solid surfaces. `FrostSampler` owns one background
worker and one pending batch. Four neighboring pixel reads never block the UI.
The pointer observer submits at most every 750 ms, rejects observations for old
bounds, and defers material changes during geometry or text animation. No captured
image is retained. Recoloring resolves text layout before restoring scroll offsets.

A bounded pointer observer preserves hover-to-pause over the transparent glass.
A UI-owned mouse hook routes only vertical wheel events inside overflowing card
bounds. Its dedicated thread forwards a bounded queue without waiting for UI
work; other input passes through. The hook is removed when no scrolling cards
remain, and the thread closes with the view. Motion, pointer observation and
window creation stay in the assistant process.

External browser ownership is `Stopped | Managed | Attached`. A managed variant
carries the process, optional Windows job and visibility. An attached variant
identifies the borrowed browser; closing it only disconnects CDP. The source
selection persists separately across disconnects. Toolkit-created target IDs are tracked
apart from personal tabs for session metadata; navigation and explicit tab closure
use the requested ID. Pending consent is a single WebSocket connection, whose
socket is closed on cancellation. Status inspection never starts that connection.

`BrowserSession` pairs a connection with its ownership. `Browser` obtains it
through `SessionOpener`; `NativeSessions` owns endpoint discovery, consent and
process creation. The same `connect`/`close` interface is used by callers and
tests. Attached sessions only disconnect; managed sessions close and reap the
owned process. Tab mutation waits use the clock supplied to `TabStrip`.

Browser page, tab and group state uses frozen, slotted dataclasses. Tab edits,
group creation/changes, placement and closure use payload variants; required
data lives on its variant. `TypedDict` defines structured tool responses and
native command wire shapes, with `Literal` values for fixed protocol fields.
Raw JSON is decoded and validated at the transport boundary. Tool and future UI
projections can share the concrete state and response types. Group selection
and pagination are pure functions of observations and arguments. The Edge tab
controller owns one hidden Favorites page, whose API calls remain separate from
ordinary page DOM evaluation. Only explicit activation brings a tab forward;
default opens reuse Toolkit's group without changing its expanded state.

Recording state is a projection for the UI. The journal can still be absent
while a capture starts. Publication decisions use immutable observations read
by the recording worker. The worker applies the result under its existing lock,
including the checks that prevent an older observation from undoing progress.
SQLite transactions, audio buffer ownership, and capture shutdown remain the
responsibility of their existing effect owners. A transcription batch reads its
audio, completion flag and prior text in one SQLite snapshot, so capture cannot
finish between queries and pair the finished flag with an incomplete tail.
The read transaction ends before audio assembly or network work.

`MeetingArchive` keeps creation and reopening on the same storage/publication
policy. `Meetings` accepts that archive and a transcription operation; retry
decisions return a delay with the new failure count or an exhausted state.
The worker retains cancellation and wait ownership. Teams similarly captures
configuration at composition and passes clock observations to device-flow
decisions, while the HTTP adapter owns request deadlines and cancellation.

Frozen dataclasses are shallow: nested state also uses immutable values such as
tuples, bytes, and other frozen dataclasses. External JSON and operating-system
results are validated when they enter the domain. Threading events, native
handles, SDK-owned histories, and mutable buffers retain a single explicit owner.

## Keyboard variants

The keyboard uses the same data definitions. Layout actions are an `Action`
union; the engine's mapped key events are `KeyDown | KeyUp`, each carrying a
cell and timestamp. `Decision.TAP` and `Decision.HOLD` name the two tap-hold
outcomes. The last completed tap is a frozen `LastTap` record.

`Engine.on_key` accepts native scancode, direction, and timestamp fields. Once a
key is mapped to a layout cell, it becomes one event variant. The engine handles
that variant with `match` and reuses the same object when buffering or replaying
it. Raw Windows flags stay in the input adapter; ordinary boolean properties,
such as whether caps word is enabled, remain booleans.

The engine owns bounded mutable state and sends effects through `Host`. Its
handler updates held keys and modifiers in place. Using enum-style data does not
require constructing a new engine state or a list of effects for every press.

Each engine accepts a frozen `Configuration`. Construction snapshots action
tables in built-in `frozendict`, freezes snippet definitions, validates timing,
and precomputes the longest trigger. The default configuration is built once.
Event handling reads it without rebuilding tables or wrapping each event in a
new controller state. An empty snippet set keeps the typing buffer empty.
See [testing through explicit dependencies](testing-dependencies.md) for the
replacement tests and before/after keyboard measurements.

## Keyboard and audio performance

The keyboard hook and engine run in the layout process. The agent and UI run in
the assistant process, with a separate Python heap and garbage collector. Agent
collections cannot stop the keyboard interpreter, although both processes still
share CPU and other operating-system resources.

The live hook already collects and freezes startup objects, then disables cyclic
GC until shutdown. Event data must remain acyclic under that policy. Benchmarks
deliberately enable GC to expose collection pressure rather than hide it behind
the live loop's guard.

The keyboard's fixed tables, bounded mutable state, and direct buffer updates
are deliberate. An immutable dataclass is useful for a layout description;
copying the full keyboard state on each press would need measured justification.
The same distinction applies to immutable audio formats and mutable sample
buffers. Keyboard variants use the existing allocation point for mapped events;
indicator refreshes and some other paths also allocate small objects. Audio
sample loops keep their existing buffer ownership.

Run the keyboard benchmark in a fresh process using the same Python build for
both revisions:

```powershell
.python\python.exe scripts\benchmark_keyboard.py --root C:\path\to\baseline --output before.json
.python\python.exe scripts\benchmark_keyboard.py --output after.json
```

The workload covers plain typing, home-row rolls, modifiers, layers, repeat,
voice keys, and tap-hold timers without sending input. It uses GC normally and
reports latency percentiles, maximum observed latency, collection counts and
pauses, and peak traced live memory. Inputs and timing storage are allocated
before timing; allocation tracing runs separately so it does not distort the
latency measurements. Peak live memory is not cumulative allocation churn.

Compare the distributions over multiple rounds and retain the worst observed
latency. The benchmark also records source hashes and whether the engine imported
the assistant. It measures engine decisions with a silent host; native hook,
`SendInput`, and operating-system scheduling require separate desktop evidence.
