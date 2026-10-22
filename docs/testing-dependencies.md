# Testing dependency inventory

The October 2026 audit found **177 calls to `patch`, `patch.object`, or
`patch.dict` across 23 test files**. This counts static call sites, not runtime
invocations: a call in a loop can patch many cases. Subclasses, hand-written
adapters, `__new__`, and assignment to private fields are not included in that
number. Several patches replace test helpers rather than production effects;
those need fixture improvements rather than new production interfaces.

The desired change is to pass observations and dependencies explicitly, put
decisions in pure functions, and test observable results through the same
interface callers use. Giving every global function a constructor parameter
would preserve the coupling in a different form. Prefer a small interface that
owns a useful operation, with real and deterministic adapters where needed.

## Prioritized changes

### 1. Agent execution and task lifecycle

**Evidence:** [SDK execution](../assistant/sdk.py) creates its HTTP client,
model, and streamed runner inside `run`. [AgentRunner](../assistant/app.py)
creates an `Agent` and uses concrete `WinMachine` and `Ui` types. Tests in
[test_windows_agent.py](../tests/test_windows_agent.py) allocate `Ui` and
`WinMachine` with `__new__`, cast `FakeMachine` to `WinMachine`, and patch
`Agent.run`, `Ui.agent`, and usage recording.

**Change:** make the task controller accept the existing `Machine` interface,
a session view/publisher, and an execution adapter. That adapter owns one
complete streamed turn, including cancellation and history, while the SDK
adapter owns client creation and disposal. Give the controller immutable task
events and a pure transition for queued follow-ups, stop, completion, and reset.
Supply the usage recorder at application composition.

**Replacement tests:** replay completion, failure, stop, and queued-input events
through the controller without Tk, SDK HTTP, private fields, or constructor
bypasses. Keep a small set of real SDK tests against the local HTTP fixture for
wire serialization, streaming, history, and cancellation. In this audit, 15
tool-only cases already moved to `Agent.call` with the existing `Machine`
adapter. Their file, command, approval, and cancellation assertions no longer
require an SDK round trip.

### 2. Recording, transcription, and recovery

**Evidence:** [Meetings](../assistant/meetings.py) accepts capture but directly
calls `transcribe`, constructs `MeetingStore`, creates worker threads, and waits
on retry timers. [test_meetings.py](../tests/test_meetings.py) patches those
operations, `sqlite3.connect`, and `Path.replace` to trigger failures.

**Change:** accept a transcription operation and move retry decisions to a pure
transition supplied with time and success/failure observations. Keep worker,
lock, and wake ownership in `Meetings`. Give transcript publication an explicit
operation that owns replace/retry behavior. Continue to use the real
`MeetingStore` for storage; it already accepts a folder and capacity.

**Replacement tests:** check retry deadlines and capture/transcription state
without sleeping. Use a deterministic transcriber to exercise the controller
against temporary SQLite databases. Keep actual transaction, duplicate-batch,
recovery, and audio-erasure assertions against SQLite; replacing SQL with a
mock would lose their value. Use a failing publication adapter to exercise
recovery, plus one native file-lock test for the Windows replace operation.
The recovery test now uses the existing `on_update` publication signal to wait
for export completion, removing a Windows race caused by polling the file.

### 3. Browser discovery, attachment, and ownership

**Evidence:** [Browser](../assistant/browser.py) has useful source/ownership
variants and `Cdp(channel)`, but `_attach` and `_start` obtain profile paths,
endpoint files, clocks, connections, processes, and jobs internally.
[test_browser_attachment.py](../tests/test_browser_attachment.py) patches
`user_data_root`, `read_endpoint`, `edge_path`, and `Cdp.connect`.

**Change:** compose a browser-session adapter that discovers or launches a
session and returns its connection and ownership. Express endpoint readiness,
retry, timeout, and cancellation decisions using observations and timestamps.
The Windows adapter retains real profile discovery, socket consent, and process
job ownership; CDP request/reply tests continue to use the existing channel
interface.

**Replacement tests:** exercise attached versus managed session transitions
with explicit session adapters; prove attached browsers are left running on
close and managed browsers are cleaned up. Retain real-socket consent and
cancellation checks, plus Windows Edge integration and native file locking.
The audit found a real temporary `DevToolsActivePort` lock that the startup
retry loop did not handle; a native lock test now covers the fix without patching.

### 4. Windows application wiring and desktop observations

**Evidence:** [test_windows_bindings.py](../tests/test_windows_bindings.py)
contains 45 patch calls. Tray commands, startup queries, input dispatch, and
overlay preferences require replacing global window, thread, and message-loop
operations. [WinMachine and AssistantApp](../assistant/app.py) own those effects
alongside application decisions. Some tests build incomplete instances with
`__new__`.

**Change:** reduce tray/menu and startup-message decisions from explicit layout,
preference, and window observations. Return immutable command variants for the
Windows owner to execute. Make desktop context selection accept an observation
provider instead of patching `windows.foreground`, `visible_rect`, and monitor
enumeration. Keep the existing `Machine` interface as the agent-facing seam.

**Replacement tests:** table-driven decision tests should cover absent,
minimized, stopped, and active windows on either OS. Retain a compact Windows
adapter check for message IDs, input tags, and actual window operations. The UIA
integration now batches operations inside one PowerShell process but still uses
element IDs across processes and independently observes the resulting UI.

### 5. Notification and panel environment

**Evidence:** [AgentToasts](../assistant/agent_toasts.py) already accepts a clock,
motion preference, and material preference; session, reading, geometry, and
lifetime decisions already have pure modules. Native tests still patch pointer
position, material application, focus, work areas, and internal card methods.
[Overlay tests](../tests/test_windows_overlay.py) patch global time and placement.

**Change:** extend the existing observation model to include pointer position,
work area, scale, and focus. Let one native owner apply placement/material and
schedule callbacks. Keep card layout and reading decisions pure. Use existing
injected clocks consistently before adding any new abstraction.

**Replacement tests:** pure transitions cover hover, paging, expiry, retargeting,
and screen changes. Native tests verify actual Tk text layout, glass hit areas,
focus preservation, and rendering under real system settings. Tests patching
`Card` or `AgentToasts` methods should move to observable widget outcomes or the
pure decision interface, rather than preserving internal call sequences.

### 6. Keyboard configuration and native input

**Evidence:** [Engine](../miryoku/engine.py) accepts a `Host`, but obtains action
tables and snippets from globals. [test_engine.py](../tests/test_engine.py)
patches `action` and `SNIPPETS`, including newly strengthened audit scenarios.
The Windows binding tests also patch `WinHost` methods and native `_SendInput`.

**Change:** provide immutable per-engine configuration for actions, snippets,
and timing. Resolve configuration once, outside the key-event loop. Pure native
event conversion and input-buffer construction can be tested separately from
the owner that sends those buffers to Windows.

**Replacement tests:** supply small layouts directly and assert emitted key
events for layer ownership, overlapping modifiers, chords, and snippets. Keep a
native adapter check for Windows flags and rejected input. Before any production
refactor, measure key-event latency, allocations, and GC with
`scripts/benchmark_keyboard.py`, then repeat after the change. Preserve process
isolation, bounded mutable buffers, acyclic event data, and the live hook's GC
policy. This audit changed keyboard tests only.

### 7. Bootstrap and bundled-runtime selection

**Evidence:** [runtime.py](../runtime.py) reads process-global platform,
executable, arguments, environment, and its own `__file__`. The 12 patch calls in
[test_runtime.py](../tests/test_runtime.py) replace these observations plus
`Popen` and `site.addsitedir`.

**Change:** compute a bootstrap plan from explicit platform, executable, source
folder, bundle observations, arguments, and environment. Represent continue,
relaunch, and invalid-bundle results explicitly; a small effect owner performs
the chosen launch or activation.

**Replacement tests:** assert the chosen interpreter, cleaned environment,
arguments, and exit behavior from the plan. Retain a real copied-source child
process importing SDK/Tk and constructing a Tk window. The bootstrap must
remain importable with system Python 3.12; keep its syntax compatible.

### 8. Audio device and COM lifecycle

**Evidence:** [native audio tests](../tests/test_windows_audio.py) and
[recording tests](../tests/test_windows_recording.py) patch 27 native call sites
to force partial initialization, wait failures, and cleanup. Format and PCM
processing already have direct value-based tests.

**Change:** separate lifecycle decisions from the native adapter that owns
device handles, COM methods, memory, and waits. Pass initialization outcomes and
packet observations to the decision code; keep copying and sample loops in
their current performance-sensitive owner.

**Replacement tests:** replay partial initialization and failure observations
and verify required cleanup commands without patching native symbols. Retain
real device/COM adapter checks. Measure buffer copying and audio loop cost
before and after refactoring; avoid allocating domain objects per sample.

### 9. Teams configuration, clocks, and HTTP timeout

**Evidence:** [Teams](../assistant/teams.py) already accepts HTTP and token-store
adapters, and `TokenStore` accepts encryption/decryption operations. Config and
token expiry still read environment and wall/monotonic clocks internally.
[test_teams.py](../tests/test_teams.py) patches the environment;
[test_graph_http.py](../tests/test_graph_http.py) patches `REQUEST_TIMEOUT`.

**Change:** validate configuration at composition; supply it and time to token
and device-flow decisions. Make request timeout an HTTP adapter setting.

**Replacement tests:** run expiry, polling, and cancellation decisions with
explicit times. Continue to test HTTP behavior through the local server and
token persistence through temporary files. No extra transport abstraction is
needed where the existing interfaces already work.

### 10. Files, workspace checks, and test fixtures

**Evidence:** [Agent file tools](../assistant/agent.py) own real `Path` operations
and global result limits. The strengthened file-search test patches `MAX_FILES`.
Workspace tests patch `Workspace.check`; selfcheck and keeper tests patch
`os.scandir` to prove excluded trees are never entered.

**Change:** give file search an explicit result limit; keep real temporary-file
tests for read/write behavior. Extract check-command construction and result
classification as pure decisions if more execution variants are added. Retain
candidate-process isolation, source digests, and the protected full install
gate. The scandir patches are traversal instrumentation, not substitutes for
filesystem outcomes; replace them only with a meaningful traversal interface
or a dedicated instrumentation check, not a flaky timing assertion.

**Replacement tests:** compare real file results and actual candidate check
outcomes. Patches against `FakeMachine`, `FakeDesktop`, or a fake window should
become explicit fixture behavior or a small subclass; they do not justify new
production dependencies. The stop-during-confirmation test now uses this form.

## Complete static patch count

| Test file | Calls |
| --- | ---: |
| `test_windows_bindings.py` | 45 |
| `test_windows_agent.py` | 16 |
| `test_windows_agent_toasts.py` | 14 |
| `test_windows_audio.py` | 14 |
| `test_windows_recording.py` | 13 |
| `test_runtime.py` | 12 |
| `test_browser_attachment.py` | 11 |
| `test_meetings.py` | 9 |
| `test_windows_computer.py` | 8 |
| `test_windows_overlay.py` | 8 |
| `test_windows_agent_panel.py` | 5 |
| `test_windows_agent_approvals.py` | 4 |
| `test_teams.py` | 3 |
| `test_agent.py`, `test_browser.py`, `test_engine.py`, `test_features.py`, `test_windows_browser.py` | 2 each |
| `test_agent_streaming.py`, `test_computer_tools.py`, `test_graph_http.py`, `test_keeper.py`, `test_selfcheck.py` | 1 each |
| **Total** | **177** |

For each refactor, replace the patch-heavy cases once the new interface tests
detect the same seeded regressions. Delete assertions that only repeat fields
or verify internal call order. Preserve tests that establish a real contract,
especially Windows ownership, cleanup, transaction durability, and the install
gate. Mutation evidence and measured runtime, rather than test count alone,
determine whether the replacement is better.
