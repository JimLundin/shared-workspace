# Independent validation of durable work

This review covers the implementation in [the accepted plan](agentic-implementation.md): durable tasks, action recovery, routines, saved context, work views, and the supervised work process. The orchestrator owns the combined integration gate, keyboard measurements, packaging, and Windows deployment. Canvas rendering and desktop-shell changes from the other coordinated session are outside these targeted campaigns; the shared work API used by Canvas is included.

The independent targeted review and combined integration checks are complete. The [mutation evidence](agentic-mutations.json) preserves 37 main campaigns and one separately scoped Canvas addendum, with executed baselines, exact commands, source and snapshot hashes, individual mutations, detecting tests, survivor dispositions, and unsuccessful checks. The [combined check record](agentic-release-checks.json) adds the final portable/native gates and all keyboard measurements. Deployment receipts are recorded separately after packaging.

## End-to-end acceptance

`tests/test_work_acceptance.py` uses the actual `Meetings`, `TranscriptEvents`, `RoutineStore`, `TaskStore`, `WorkController`, and `AgentExecution` interfaces. Generated audio and an explicit transcriber produce a real published transcript. An enabled standing instruction creates one durable task. A local Responses endpoint requests a transcript read and a notes write. The result contains a source reference and assigned actions, its file digest is verified, and the task becomes `Completed`. Reopening all journals and redelivering the transcript does not create another task or rewrite the notes.

The final focused `selfcheck.run_tests` invocation passed this scenario and seven external-input tests: **8 passed, no skips, and no pytest import**, in 12.475 seconds. The acceptance scenario itself took 10.497 seconds. Its Ruff, strict Mypy, and explicit-interpreter Pyright checks also pass. Its model response is controlled: it establishes the execution and durability path, not the quality of a model-generated summary. It performs no foreground input and contacts no company endpoint.

## Design and test review

Task, run, waiting, response, routine, and action alternatives use immutable slotted values with enum variants. Important transitions use exhaustive matching. SQLite stores validate their external rows, own transactions, and keep decisions in the shared task model. The controller owns execution threads; native and UI adapters retain their own effects. Browser, Teams, and SDK histories are not persisted as task checkpoints.

Behavioral tests exercise real SQLite journals, files, local transport, and the public controller/view adapters. Concurrency uses events or observable transport completion. The native worker tests use an isolated profile and hidden window; they do not drive the user's desktop. Native pytest dependencies were installed in a separate temporary development directory, leaving the deployed interpreter and runtime untouched.

The pre-change baseline passed 474 fast tests, Ruff, Mypy, and Pyright with the project interpreter. The initial plain Pyright invocation chose an interpreter without the project dependencies; its 1,135 import errors are retained as an environment failure, followed by the successful explicit-interpreter check.

The controller tests originally used pytest parameterization, which the installed selfcheck cannot supply. The owner converted them to ordinary tests covering both `RuntimeError` and `SystemExit`, and reported all 12 passing through plain selfcheck. The final mutation baseline uses those converted tests. The earlier parameterized campaign remains historical evidence and is not installation-gate evidence.

## Findings resolved during review

* Routine notes initially targeted the transcript's folder. They now use the task's own results directory.
* A separate enablement check and task submission allowed an instruction to change before handoff. The routine outbox now commits an `Accepted` instruction snapshot before submission; disabling or editing cancels only pending handoffs. An interrupted submission acknowledgement remains recoverable.
* Context retrieval initially selected an unrelated project's note through a shared connective word. Explicit project scope and connective filtering now keep those facts separate.
* A prior observation of another browser tab could reconcile a later action. A real-store reproduction established the defect. Reconciliation now requires a later observation of the intended target, and task completion checks all unverified actions rather than a display page. Scripts require explicit result inspection; file writes retain digest verification.
* `check_changes` initially counted as an observation although it executes candidate source. Its execution now has an action record before candidate import, with an observed check-process outcome.
* The public execution/controller log protocols disagreed about keyword arguments. Their shared positional contract now passes strict type checking without a cast in the acceptance test.
* A reply callback could finish after its view request had already been restored or replaced. `AgentWindow` now retires the replaced request and resolves only the exact in-flight submission it still owns. Public callback-interleaving regressions detect both faults.
* A queued answer failure could restore the conversation while leaving a separate Canvas task card awaiting acknowledgement. The frontend now publishes a bounded reply-problem receipt only after a fresh journal read confirms the exact task and pending request. Each failed attempt has its own receipt identity, even when the error text repeats. Successful answers and replaced requests retire those receipts.

## Targeted mutation evidence

Each campaign runs an unmodified passing baseline before testing disposable mutations. A runner timeout, collection error, or empty/skipped baseline is not a detected regression. These are narrow contract checks, not a whole-project mutation percentage.

| Scope | Evidence so far |
| --- | --- |
| Native machine boundary | All 3 seeds detected: import isolation, question kind, and cleanup after a browser-close failure. |
| Work controller | All 6 seeds detected against the final 12-test baseline: run ownership, cleanup before replacement work, shutdown interruption, live-error waiting, refused completion, and final publication before `wait()` returns. |
| Task decisions | All 17 selected faults detected across the original and follow-up selections. Two original survivors needed the existing approval test added to the selection; one needed the same-clock-tick outcome regression. |
| Task-store SQL | All 7 seeds detected: atomic execution ownership, occurrence deduplication, completion gates, goal revision, approval consumption, and verification beyond a display page. |
| Batch task lookup | All 6 seeds detected against three public tests: supplied batch bound, identifier validation, deduplication, requested-row filtering, pending-input validation, and requested order. |
| Routine handoff | All 4 seeds detected: accepted delivery recovery, disablement, file-watch re-enablement, and interrupted acknowledgement. |
| Routine decisions | The original 24-mutation campaign detected 16 faults. Both substantive scheduling survivors are detected by their follow-up checks. |
| Context selection | Stale-content, expiry, and project-scope seeds detected. Clearing connective filtering is also detected; removing either of its redundant filters alone is equivalent. |
| Action effects | All 5 seeds detected: effect before commit, revoked ownership after commit, cancellation while waiting for resources, unresolved-action gating, and digest mismatch. The strengthened single-test follow-up also detects bypassing the unresolved-action gate for a different operation. |
| Execution recovery | All 6 initial seeds detected: stale observation, wrong target, reused approval, unverified completion, old sent-message timestamp, and claiming an absent message was not applied. A separate truncated-message seed is also detected. |
| SDK conversation state | Both seeds detected: shared histories and lost source-workspace drafts after restart. |
| Frontend service | All 4 seeds detected: registration before eligibility, conversation-scoped Stop, worker-epoch approval freshness, and exact originating run. |
| Frontend live events | Stale run and duplicate meeting effects detected. Removing the additional conversation guard alone is redundant with current-task selection. |
| Submission receipts and retained tasks | All 8 faults detected across the initial six-test selection and its follow-up: prompt/source-kind collisions, changed invocation registration, eviction of watched failures, discarded cached tasks, treating a failed read as absence, omission of retained tasks, and spurious retry failure feedback. |
| Queue, watches, and reply recovery | All 6 seeds detected against seven public workflows: LIFO command ordering, cumulative watches, rejecting Stop at ordinary capacity, lost reply restoration, restoring from an unconfirmed read, and suppressing failed-read publication. |
| Canvas reply receipts | All 5 seeds detected across service and observer campaigns: repeated attempt identity, exporting receipts during a failed read, wrong task binding, wrong request binding, and lost failed-command handoff. |
| Work view | All 4 seeds detected: request identity, display before approval, coalesced publication, and complete result text. |
| External input restoration | Both seeds detected against seven public input tests: retirement of a replaced in-flight request, and refusal to resolve a restored request when its previous callback returns. |
| IPC codec | All 4 seeds detected across two campaigns: unknown version, mixed worker epoch, otherwise-valid oversized JSON, and duplicate/decreasing event sequence. |
| IPC bridge | All 5 seeds detected: revoked run ownership, wrong meeting request ID, reconnect registration, distinct presentation event kinds, and HMAC authentication. |
| Native worker lifetime | All 3 seeds detected against 13 native/IPC checks: exclusive profile ownership, HWND cleanup, and recovery before readiness. |
| Native approval paint | The visibility seed is detected: an approval cannot be accepted while its card is still fading in. |
| Keeper | All 4 seeds detected: worker presence, zero-exit recovery, worker readiness, and shutdown order. |
| Transcript delivery | All 4 seeds detected: publication existence, historical time, complete initial scan, and the final callback. |

No completed campaign has a runner timeout or collection error. The evidence retains survivors rather than interpreting them as successes.

## Survivor investigation

The task timestamp survivor would discard an action result occurring within the same clock tick as its attempt. A new public-store test records returned and verified outcomes at that timestamp, reopens the journal, and completes the task. It detects the fault without changing production behavior.

The one-shot due-time survivor was not merely a redundant check. With a deadline of 100, enabling at 2, polling at 50, disabling at 60, and re-enabling at 110, current code creates one overdue task. The mutation reserves and cancels the occurrence early, then loses the later work. Separate subprocesses with real journals reproduced one task versus zero; the owner added that workflow as a regression.

The supplied-time enablement survivor also changed behavior. An instruction created at 1 for time 90, then enabled at 100, must wait when the next clock observation moves backward to 95. A real-store test reopens the journal and checks no task at 95, followed by one task at 100. It detects the changed guard with production code unchanged.

The six remaining routine survivors concern negative sentinels outside stored input contracts, the arbitrary one-shot ordinal, or repeated occurrence selection already prevented from creating duplicate tasks by the outbox and task-origin keys. No tests assert an arbitrary ordinal or private SQL implementation simply to detect these mutations.

The context filters remove connective words from both query and content terms. Removing either filter alone preserves their intersection. Removing the shared connective set changes retrieval and is detected. Similarly, the frontend's selected task must already belong to the current conversation; deleting a second conversation check on a live event leaves that selection fence intact.

The identified-submission retry test initially checked the preserved task, original invocation, and keyed receipts without checking the global status. A wrong-source mutation retains the correct task and then reports a spurious identity conflict. The later receipt projection clears its keyed problem because that task is already saved. A public reproduction confirms empty status and no errors for current code, versus an identity-conflict status and one reported error for the mutant. The successful-retry test now asserts that status and error behavior and detects the mutation. Production code already retained the correct source.

## Native view checks

The first new native WorkPanel/approval run had six passes and two failures. Both failures compared a native Tcl state object with a Python string after `typing.cast`, which does not convert runtime values. Replacing those assertions with the native `ttk.instate` interface produced **8 passes in 51.35 seconds**, with production code unchanged. The longest actual test took 2.94 seconds; startup and imports account for most of the elapsed time.

Those tests cover task controls, editor drafts, selected task identity, source references, scrolling, hidden-window behavior, and approval refusal before the exact preview is visible or painted. Their initial failed run, corrected run, logs, and frozen hashes are retained. The corrected baseline is the only one eligible for subsequent mutation checks.

A subsequent isolated native worker campaign passed 13 baseline tests in 9.40 seconds and detected all three seeded lifetime faults. A separate native approval campaign passed its single-test baseline in 5.66 seconds and detected removal of the opacity guard. These campaigns ran with one worker and completed without timeouts or collection errors.

## Narrow Canvas addendum

The separately authorized follow-up checks only the running-result guard in `assistant.canvas_model.delete`. The public `test_deleting_saved_items_reclaims_capacity_and_keeps_embedded_evidence` workflow verifies saved-item deletion, preserved embedded evidence, reclaimed capacity, and refusal to delete a running result. Its unmodified baseline passed one test in 1.00 seconds. Removing that guard was detected, with no survivor, runner timeout, or collection error. This does not establish a global Canvas mutation result; its exact command and frozen hashes are stored separately under `canvas_addenda` in the evidence file.

## First combined Windows gate

The orchestrator's initial staged Windows selfcheck reported **770 passed, 19 failed, and 3 modules skipped in 359.297 seconds**. The [original failure log](/mnt/c/Users/Jim.Lundin/AppData/Local/Temp/toolkit-agentic-final-1792743371/initial-integration/windows-selfcheck.log), command metadata, source-manifest pointer, and their hashes are retained under `combined_windows_gates` in the evidence file. This failed integration run is not counted as a passing mutation baseline or a detected mutation.

Eighteen failures were backend test portability defects: five assertions searched JSON text for unescaped Windows paths, twelve SQLite fixtures retained connections until garbage collection, and one text fixture acquired Windows CRLF bytes that differed from its expected digest. Owners corrected these with decoded JSON comparisons, explicit connection closure around the existing transactions, and exact fixture bytes. The relevant backend production files still match the failed gate's source manifest. Earlier mutation reports retain their original frozen test versions; the final combined gate below checks the corrected fixtures.

The nineteenth failure exposed WorkPanel focus behavior. Quiet reopening after an explicitly activated window had been hidden could activate it again. Mapping a withdrawn/iconic window through Tk's normal/zoomed state under the existing no-activate guard preserves the current foreground. The native regression first demonstrated the defect and then executed successfully in a **9-test passing run**; it was not skipped. The background-render test also captures the foreground after Hide has settled. The focused recheck of all **19 originally failing Windows cases passed**.

## Final combined checks

The source freeze includes the coordinated desktop/canvas implementation and its shared work API. The full Windows check ran against a staged source copy with the extracted pinned Python and dependency folders and an isolated local profile. No concurrent native test, preview or mutation job was running during that final full check.

| Check | Result |
| --- | --- |
| Portable selfcheck | **654 passed**, zero failures; 20 native modules deferred |
| Full staged Windows selfcheck | **794 passed**, zero failures; three opt-in desktop E2E modules skipped; 419.657 seconds |
| Ruff | Passed |
| Strict Mypy | Passed, 190 source files |
| Strict Pyright with the project interpreter | Zero errors or warnings |
| Python 3.12.5 bootstrap imports | Passed for `runtime` and `keeper` |
| `git diff --check` | Passed |

The full log retains three SDK `Error streaming response` lines and two Tcl cross-thread interpreter teardown warnings. The process completed with exit code zero; those diagnostics remain visible in the evidence. They are not mutation detections. Ordinary full selfcheck does not enable the opt-in suites that control the user's keyboard/mouse or use company endpoints. Check elapsed times are observations, not a performance comparison.

## Keyboard measurements

Each process used Python 3.15.1, 100,000 events per round, five rounds, and enabled GC. The final unrestricted measurements were slower than the earlier baseline, so the orchestrator repeated baseline/current pairs immediately on the same logical processor, with all test and mutation jobs stopped. Every run retained the same **1,780-byte peak live memory**, **855 retained bytes**, **zero observed collections**, and **no assistant imports**.

Each percentile cell is the median of five round percentiles, in microseconds. The last column retains the worst individual event across all five rounds, rather than the median round maximum.

| Comparison | p50 before → after | p95 before → after | p99 before → after | p99.9 before → after | Worst event before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Linux, earlier baseline/final unrestricted | 3.318 → 5.187 | 8.597 → 13.512 | 12.214 → 28.114 | 33.866 → 161.046 | 2,460.187 → 9,283.028 |
| Windows, earlier baseline/final unrestricted | 3.0 → 4.9 | 7.4 → 12.6 | 10.1 → 24.2 | 23.3 → 136.1 | 704.2 → 4,492.9 |
| Linux, immediate pair on logical processor 2 | 5.937 → 5.803 | 15.664 → 15.600 | 34.114 → 33.805 | 218.650 → 180.457 | 8,241.690 → 11,009.073 |
| Windows, immediate pair on logical processor 2 | 6.6 → 5.1 | 20.8 → 12.9 | 86.8 → 23.5 | 2,865.1 → 328.6 | 57,953.8 → 17,623.7 |

The immediate pairs do not show a consistent code-related increase, and substantial scheduling outliers remain. These observations establish neither a latency improvement nor a guarantee against a small regression. Engine, hook and event-data source hashes are unchanged; `protocol.py` and `keeper.py` are the changed measured dependencies. The workload measures synthetic engine decisions with a silent host, not physical hook or `SendInput` latency. Raw rounds, source hashes, commands and both original and follow-up runs are retained in the combined check record.

## Packaging and scope

The runtime pins match the installed and staged copies. Packaging preserves `.python` and `.runtime`, verifies their bytes against the archive, and includes the checked source. The orchestrator owns the single shared Windows deployment: clean `WM_CLOSE`, source/ZIP verification, fresh readiness markers, stable child processes, and visible desktop bar/canvas. Actual receipts are written beside the [staged release records](/mnt/c/Users/Jim.Lundin/AppData/Local/Temp/toolkit-agentic-final-1792743371) after the evidence documents are frozen, so recording a release does not change the deployed source.

The worker currently executes one task at a time. The [desktop canvas](desktop-canvas.md) implements live windows, file/table observations, Edge excerpts and local drafts; dedicated Excel writes, Teams projections and a verified Outlook object adapter remain adapter work. When launched through WSL, Windows blocks the independent taskbar recovery helper, so Explorer stays visible. The bar, launcher and canvas remain usable; taskbar takeover requires launching Toolkit directly from Windows.
