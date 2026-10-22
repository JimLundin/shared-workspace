# Testing through explicit dependencies

The dependency refactor replaces global patching with small interfaces for
execution, browser sessions, transcription, publication, configuration, and
time. Tests pass observations or adapters through the same interfaces as the
application, and continue to use real SQLite, temporary files, sockets, and SDK
requests where those establish the contract.

Portable tests now have **5 patch call sites, down from 49**. The changed test
areas remove 65 sites in total, including the former Windows-only agent-runner
tests. This counts calls to `patch`, `patch.object`, and `patch.dict`, rather
than runtime invocations. It does not count subclasses or ordinary adapters;
replacing a patch with a private-field assignment would not establish a better
interface.

## Implemented seams

| Area | Interface and owner | Test surface |
| --- | --- | --- |
| Agent lifecycle | `AgentRunner` accepts `Execution` and a window factory. `Task`, `Active`, `Stopping`, and `Idle` have a pure transition. `AgentExecution` owns SDK history, workspace, journal, metrics, and cancellation cleanup. `AssistantApp` composes them. | Portable controller tests use a recording execution and the real `AgentWindow` with its recording view. Two local-HTTP SDK scenarios cover history/reset and cancellation cleanup. |
| Meetings | `Meetings` accepts a transcriber, `MeetingArchive`, and `RetryPolicy`. The archive supplies consistent capacity and transcript-publication policy when creating or reopening a journal. Retry decisions return `Retry` or `Exhausted`. Threads, locks, waits, capture, and SQLite stay with their owners. | Real journals and files cover recovery, durable text, audio deletion, failed publication, and immediate retry. A controlled transcriber covers slow and failed requests. Native file-lock coverage remains. |
| Browser sessions | `SessionOpener.open` returns a `BrowserSession` containing CDP and `Managed` or `Attached` ownership. `NativeSessions` owns discovery, consent, launch, and process cleanup. `Browser.connect` and `close` own session transitions. `TabStrip` accepts its clock. | Scripted sessions and CDP channels cover commands and ownership without assigning a connection or owner after construction. Real sockets cover consent and cancellation; Windows Edge tests retain native process and tab behavior. |
| Keyboard | Each `Engine` accepts a frozen `Configuration` containing action tables, snippet definitions, and timing. Construction snapshots the table with built-in `frozendict`, normalizes snippet tuples, and computes the buffer bound once. | Existing emitted-key assertions use explicit configuration. Separate engines cannot share mutable layout state; timing, modifier ownership, snippets, and disabled-snippet bounds remain observable. |
| Teams and HTTP | `Teams` captures just its two Graph environment settings in a `frozendict` and accepts a clock. Frozen `DeviceFlow`, `sign_in_action`, and `defer_poll` express polling decisions. `SystemClock` performs real waits. `Http` accepts validated request/socket timeouts. | A manual clock drives exact poll times and token expiry through the existing HTTP and token-store interfaces. The HTTP deadline test uses its real local server. |
| Bootstrap | `bootstrap_plan` takes platform, interpreter, arguments, environment, and bundle observations. Its result is `Continue`, `Relaunch`, or `InvalidBundle`; `execute_bootstrap` performs the handoff. `validate_dependencies` checks the dependency ABI before activation. | Pure plan/validation tests replace all 12 runtime patches. Real children establish console exit/environment behavior and the Windows windowed handoff. Copied-source SDK/Tk integration remains. |
| Files and fixtures | `find_files` accepts a bounded result limit. Test desktop/machine adapters accept the failures they model. Activity tests record their observations directly. | Real filesystem results, cancelled mouse releases, unavailable screenshots, and failed voice input replace patches of globals and fake classes. Reset checks the actual workspace path. |

The task runner also uses one completion event per run. Waiting during a later
startup cannot accidentally join the previous worker, and completion includes
the callback. Tests reproduce startup, queueing, cancellation, and completion
interleavings using events rather than sleeps or thread-field inspection.

The bootstrap remains importable on Python 3.12; `frozendict` is used only in
application modules requiring Python 3.15. Pyright 1.1.414 incorrectly declares
`frozendict.__init__` as accepting no arguments. The two constructor calls have
narrow, documented `reportCallIssue` suppressions; their input and result types
remain checked. Mypy and Python 3.15 on Linux and Windows accept those constructors.

## Remaining instrumentation

The five portable patch sites each observe a specific external operation:

| Test | Reason retained |
| --- | --- |
| `test_browser.py` | Verifies that the WebSocket library receives `proxy=None`, independent of machine proxy configuration. |
| `test_browser_attachment.py` | Observes the real socket's timeout while consent is pending and after approval. The actual socket, handshake, and commands still run. |
| `test_meetings.py` | Interleaves a real SQLite writer between reader queries to prove that batch data and capture completion come from one transaction snapshot. |
| `test_keeper.py`, `test_selfcheck.py` | Instrument directory traversal to prove excluded dependency/cache trees are never entered. A timing assertion would be weaker. |

Windows tests still inject native failures and instrument Tk/Win32/COM effects.
These include input rejection, partially initialized audio, focus, placement,
and application wiring. Native appearance tests also contain concurrent work.
The remaining count is an inventory, not a claim that every Windows patch is
essential. Further changes should move decisions behind an existing seam and
retain native ownership/cleanup checks; adding a constructor argument for every
Win32 call would only move the coupling.

## Keyboard measurements

The before/after runs use the same Windows Python 3.15.1 build, 100,000 events
per round, five rounds per process, and enabled GC. No run imports the assistant.
Traced peak live memory is **1,780 bytes**, retained memory **855 bytes**, and
observed collections **zero** on both sides. Audio sample loops and copying
were not changed.

Each latency cell below is the median of five round percentiles, in
microseconds; the maximum retains the worst individual event across all five
rounds. The initial mapping-proxy implementation is retained as intermediate
evidence, followed by the requested built-in `frozendict` implementation.

| Comparison | p50 before → after | p95 before → after | p99 before → after | p99.9 before → after | Worst event before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial paired run, mapping proxy | 4.8 → 5.2 | 12.5 → 13.4 | 17.2 → 20.9 | 42.3 → 73.4 | 9,679.7 → 1,216.6 |
| `frozendict`, after then before | 3.2 → 3.5 | 8.0 → 8.8 | 11.1 → 13.0 | 25.4 → 28.6 | 8,628.6 → 31,989.2 |
| Same CPU, after then before | 4.4 → 4.5 | 11.7 → 11.2 | 18.5 → 18.0 | 71.4 → 74.0 | 11,496.5 → 29,086.8 |
| Same CPU, before then after | 3.5 → 3.5 | 7.9 → 7.7 | 10.9 → 10.1 | 18.5 → 17.8 | 11,666.1 → 21,152.8 |

The same-CPU runs restrict only the benchmark process to the third eligible
logical processor, in after/before/before/after order. Central latency does not
show a consistent increase in those pairs, while earlier unrestricted runs
do show small increases. Large outliers and changing distributions remain;
these observations do not establish a latency improvement or rule out a small
regression. They measure engine decisions with a silent host, not native hook
or `SendInput` latency. Raw rounds and source hashes are retained with the
verification evidence in [dependency-refactor-checks.json](dependency-refactor-checks.json).

## Verification

Twelve seeded regressions across the runner, SDK history, browser ownership,
transcript publication, retry exhaustion, Teams polling, bootstrap environment,
and keyboard configuration are all detected by the replacement tests. All eight
campaign baselines pass, with no surviving mutations, timeouts, or collection
errors. These are targeted contract checks, not a whole-project mutation score.

Final checks pass: **473 portable/Linux tests** with 15 skipped native modules,
and **586 Windows tests** with three opt-in desktop E2E modules skipped. Mypy,
Pyright, Ruff, and `git diff --check` pass. Python 3.12.8 imports the bootstrap.
The first Windows run's three failures and their focused recheck are retained
in the evidence rather than counted as successful full validation.
