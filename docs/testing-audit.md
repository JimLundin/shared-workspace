# Test cost and mutation audit

Toolkit now uses fast checks while editing and full Windows checks before
installation. This follows the requested workflow: shorten the iteration loop,
retain real integrations at the install gate, and improve assertions using
mutation evidence. Remaining hard-coded dependencies are listed in the
[dependency inventory](testing-dependencies.md), alongside the implemented
interfaces and replacement tests.

## Measured check cost

These are observed wall times on this workstation with Python 3.15.1. Windows
used the Downloads bundle's interpreter and runtime with a separate source
snapshot. Final timing runs were sequential, without mutation jobs running.

| Workflow | Before | After |
| --- | ---: | ---: |
| Windows editing check | 194.6s, full profile | 46.8s, fast profile |
| Windows full installation gate | 194.6s | 120.4s |
| Linux selfcheck | 31.0s | 18.6s |
| Linux pytest | 44.9s | 29.5s |

The first Windows baseline ended with 524 passed and one failure: Edge briefly
locked `DevToolsActivePort` during startup. A first verification of the changed
full suite took 149.2s and exposed an existing recovery-test race; that failing
run is not counted as successful verification. These are individual runs, not
a statistical benchmark. The working tree also contains concurrent UI work,
so test counts and UI timing are not held constant between snapshots.

The final full Windows run passed 544 tests with three opt-in E2E modules
skipped. The fast run passed 433 tests and deferred 15 native modules. Both
profiles still compile every Python source, including native tests and `.pyw`
launchers, and import production modules. `Workspace.check(fast=True)` is used
by the editing tool. The default remains full, and the protected keeper's
invocation is unchanged. Tests seed a Windows-only failure to prove that fast
editing can pass while the full installation gate refuses the candidate.

The explicitly enabled desktop E2E suite remains separate. Selfcheck clears
`TOOLKIT_E2E`; those tests start a second Toolkit and control the keyboard/mouse.
This audit ran ordinary Windows integrations, not those manual desktop E2Es.

## Changes that removed work or improved its signal

* **Source discovery:** selfcheck now prunes bundled interpreters, dependencies,
  and caches before descending into them. Previously it enumerated those trees
  and then discarded their files.
* **CI duplication:** the OS matrix runs the suite once through full selfcheck.
  The second install-and-pytest execution of the same tests was removed. The
  extracted Windows bundle still gets its own full check, and the optional
  desktop workflow still uses pytest.
* **Agent tools:** 15 tool-only cases now use `Agent.call` and the existing
  `Machine` interface directly. SDK serialization, streaming, conversation
  history, and install-loop cases still exercise the real SDK with a local HTTP
  fixture. Direct cases assert success/failure, approval refusal, command
  arguments, cancellation, file limits, and nested directory creation.
* **Audio:** packet-continuity tests use 97 stereo frames and several uneven
  packet sizes instead of a second of audio at each rate. A separate known
  resampling result catches interpolation errors that a whole-versus-split
  self-comparison missed. Scaling, packet clocks, capacity, overlap, and quiet
  thresholds now have independent expected values.
* **Browser consent:** one real-socket scenario replaces two 2.5-second waits.
  An event controls consent; the test observes the socket's timeout before and
  after approval and checks that the connection is reused. A seeded removal of
  `transport.settimeout(None)` is detected. Socket construction is still
  instrumented with a patch and is explicitly recorded as architectural debt.
* **Command termination:** a socket closing proves that Stop terminated the
  command's child. This replaces waiting for a delayed marker file and handles
  Windows connection-reset behavior.
* **UI Automation:** operations run in fresh scopes inside one PowerShell
  process. IDs come from an earlier process, and a third process observes the
  resulting UI. This retains action, error, pagination, escaping, and long-text
  assertions while avoiding repeated shell startup. Expanded/collapsed states
  are now asserted rather than merely exercised. The native UIA scenario fell
  from 42.6s to 10.0s in the recorded runs.
* **Copied runtime:** the native smoke test starts a fresh copied-source process,
  imports the inherited SDK and panel, and constructs a real Tk window. It no
  longer reruns the entire panel scenario that the suite already executes.
* **Recovery synchronization:** the transcript recovery test waits for the
  existing public `on_update` completion signal, then closes the worker and
  reads the published transcript. Polling SQLite completion and immediately
  opening the file raced its replacement on Windows. The corrected test passed
  20 consecutive native runs in 2.2s without a new patch or sleep.
* **Low-value assertions:** two tests that merely echoed chord/tap-hold fields
  were removed. Engine scenarios now verify emitted key events, layer ownership,
  overlapping modifiers, held-key repeats, and shifted snippet triggers. A new
  assertion that only echoed recording defaults was also removed during review;
  its three extra mutation kills did not justify keeping it.

The only application defect fixed in this audit is the transient Edge endpoint
read failure. `read_endpoint` now turns missing or temporarily locked endpoint
files into `BrowserError`, which the existing bounded, cancellable startup loop
already retries. A real `msvcrt.locking` test failed against the original source
and passes with the fix. Keyboard and PCM production loops were not changed.

## Targeted mutation evidence

The same deterministic samples were compared before and after. A kill means a
test assertion failed when the source was changed. Timeouts, collection errors,
and empty/skipped baselines are not kills. None of the previously detected
mutations became surviving after the test changes.

| Source | Generated | Tested | Detected before | Detected after |
| --- | ---: | ---: | ---: | ---: |
| `assistant/recording_state.py` | 44 | 44 | 30 | 41 |
| `assistant/notification_reading.py` | 31 | 31 | 25 | 29 |
| `assistant/pcm.py` | 104 | 60 | 48 | 60 |
| `miryoku/engine.py` | 153 | 80 | 71 | 77 |
| Six selected `Agent` methods | 27 | 20 | 11 | 19 |
| **Total** | **359** | **235** | **185** | **226** |

The final campaigns had nine survivors, zero timeouts, and zero runner errors.
Two additional explicit regressions were detected: removing the browser socket
timeout reset, and applying fast-profile deferral to the full install gate.
These two are recorded separately from the 235 decision mutations.

This is **targeted mutation coverage, not a whole-project mutation score**.
It does not mutate all application methods, native APIs, PowerShell scripts,
SQL, UI behavior, or possible concurrency schedules. It is evidence for the
specific assertions and samples below, not proof that other tests are redundant.

The [machine-readable evidence](testing-mutations.json) preserves the actual
before/after results and killing test names. Early baseline reports predate
source hashing and byte-offset reporting; later reports include source and
snapshot hashes, and the final runner includes offsets to distinguish equal
literals on one line. Per-mutant durations include campaign concurrency and
must not be used as check-performance measurements.
The [timing evidence](testing-timings.json) includes command totals and native
per-test measurements. Linux verification passed 433 tests; Mypy, Pyright, and
Ruff passed, and the final recovery-test change also passed its targeted checks.

## Surviving mutations and their interpretation

| Location | Mutation | Assessment / follow-up |
| --- | --- | --- |
| `RecordingState.pending_seconds` | Default `0` → `1` | Initial pending-audio presentation is not covered through a controller/view scenario. Add that behavior at the presentation interface if it must be guaranteed; a bare default-field assertion adds little value. |
| `RecordingState.revision`, `exported_revision` | Default `-1` → `-2` (two mutations) | Both are before any valid nonnegative stored revision. No behavior difference was established through publication. Keep these as sentinel-equivalence candidates, without tests requiring an arbitrary sentinel number. |
| `notification_reading.pace` | Minimum word count `1` → `2` | The line-speed clamp masks this for the current renderer, where content height is at least a line. Tiny synthetic geometry outside that caller's behavior can distinguish it. |
| `notification_reading.displayed` | Offset clamp `0.0` → `1.0` | Whole-page rounding masks the difference for rendered line/page sizes greater than one pixel. If one-pixel pages become supported, add a real geometry scenario. |
| `Modifiers.release` | Missing-count default `0` → `1` | Would emit an extra release for an unheld modifier. The tested balanced engine event sequences do not reach that condition. Revisit through the engine's configuration/event interface, rather than adding a test of its internal dictionary. |
| `Engine.in_streak` | `STREAK_MS > 0` → `>= 0` | Equivalent with nonnegative elapsed time: at zero, the later `elapsed < STREAK_MS` condition is already false. |
| `Engine.in_streak` | Guard threshold `0` → `1` | Current `STREAK_MS` is fixed at 150. A one-millisecond configuration would distinguish it; cover configurable timing after removing the global configuration dependency. |
| `Agent.approve` | Early stopped return `False` → `True` | `Agent.call` covers Stop before dispatch, and tests cover Stop during confirmation. Stop arriving between dispatch and this inner guard remains an untested scheduling case. A controllable task-execution seam is preferable to patching call order merely to kill it. |

## Reproduce or extend the audit

Install pytest in the development environment, then use narrow source and test
selections. No mutation command is added to the normal edit or install loop.

```sh
python scripts/mutate.py assistant/recording_state.py --tests tests/test_recording_state.py --limit 0 --workers 2 --output dist/recording-mutations.json
python scripts/mutate.py assistant/notification_reading.py --tests tests/test_notification_reading.py --limit 0 --workers 2 --output dist/reading-mutations.json
python scripts/mutate.py assistant/pcm.py --tests tests/test_pcm.py --limit 60 --workers 2 --output dist/pcm-mutations.json
python scripts/mutate.py miryoku/engine.py --tests tests/test_engine.py --limit 80 --workers 2 --output dist/engine-mutations.json
python scripts/mutate.py assistant/agent.py --function Agent.read_file --function Agent.find_files --function Agent.write_file --function Agent.run_powershell --function Agent.approve --function Agent.may_control --tests tests/test_agent.py --limit 20 --workers 2 --timeout 90 --output dist/agent-mutations.json
```

The default seed is zero. The runner freezes one source snapshot, verifies a
passing baseline, and runs each change in its own disposable copy without
bytecode or external pytest plugins. It does not edit the checkout. Use
`--replace BEFORE AFTER` for a specific unique source fragment. A nonzero exit
means at least one mutation survived or did not complete successfully; inspect
the result classification before interpreting it as a coverage defect.

Use one worker for tests that share the Windows desktop. Timeout cleanup
terminates the candidate's process tree. Throwaway validation exercised a
passing baseline, a detected boundary mutation, an undetected unused-function
mutation, collection failure, an all-skipped suite, invalid replacement syntax,
UTF-8 offsets, partial timeout diagnostics, and source preservation. Real
parent/child processes verified timeout cleanup on Linux and Windows.

For future test reduction, seed regressions at the behavior being preserved,
replace patch-heavy tests through the proposed interface, and compare both the
detected mutations and elapsed time. A matching sampled kill set alone is not
enough reason to delete a distinct user-visible contract.

## Conversation UI follow-up

The conversation UI work builds on merged commit `9a0ba60`. Its tool-preview test
now uses `ToolRig` and `Agent.call`, following this audit's direct tool fixture.
It verifies readable notifications and the original model-facing result without
starting an unused HTTP server. The existing SDK streaming scenarios remain.

The merged mutation runner exposed missing assertions for control/window action
wording, empty searches, omitted result counts, compact nested details, previews
that fit exactly, standalone questions, and voice answers followed by more
actions. Tests exercise these through conversation presentation and session
transitions. Those coverage improvements did not change production code. The
following comparison records that earlier design; the combined-bubble campaign
below covers its replacement.

| Scope | Tested | Detected before | Detected after |
| --- | ---: | ---: | ---: |
| `assistant/action_text.py` | 67 | 40 | 66 |
| `notifications.observe`, `order_of`, `timelines` | 32 | 24 | 27 |
| **Total** | **99** | **64** | **93** |

All generated mutations in those scopes ran. No previously detected mutation
survived the new tests; there were no timeouts or runner errors. Two additional
seeded regressions were detected: shortening raw action JSON before formatting
it, and returning the display preview to the model in place of its full tool
result. The [conversation mutation evidence](conversation-mutations.json)
records both campaigns, source hashes, exact replacements, and detecting tests.

Six mutations remain:

* `shortened` changing `limit > 0` to `limit > 1` differs only for a one-character
  preview. Production callers use unlimited text or 240/360-character previews.
  Revisit this uncovered boundary if the UI introduces one-character previews.
* Five `order_of` literal changes preserve the relative order of all valid
  cards: the response and working tertiary ranks still follow their request;
  the sole recording card still follows the conversation. Tests assert visible
  order rather than arbitrary numeric sort keys.

The accepted design subsequently replaced the separate activity cards and rail
with one `Turn` bubble. Its result remains attached when the steps collapse.
The current campaign includes `observe.finish_turn` explicitly: the runner's
function selection is exact and does not automatically include nested functions.

| Current scope | Tested | Detected before | Detected after |
| --- | ---: | ---: | ---: |
| Turn grouping, result/step selection, captions, ordering, and expiry | 83 | 72 | 74 |

The additional assertion keeps a reading hold through a content update while
the agent is still busy. It catches two mutations that would let an unread
answer expire after completion. Tests also cover captions for earlier replies
without tools, terminal turns without output, follow-up isolation, queued work,
and successful retries after a failed action. A separate seeded reversal of
terminal-state precedence is detected. These runs had no timeouts or runner
errors; the same evidence file retains both the historical and current reports.

Nine mutations in the current campaign preserve observable behavior:

* Changing the `results` sentinel from -1 to -2 cannot match a session index.
* Increasing the serial increment from one to two preserves its monotonic order.
* Seven sort-key literal changes preserve projected order. Responses now live
  inside turns; requests still precede their turn, and the sole recording card
  still follows the conversation. Tests do not require arbitrary numeric ranks.

The 37 native conversation checks cover the attached result, reversing a
disclosure transition without losing selection, immediate keyboard disclosure,
independent step scrolling and its overflow marker, full glass hit targets,
solid/high-contrast fallback, and asynchronous material observation. A live
50-sample desktop measurement found a median 28.0 ms and p95 34.5 ms for the four
background pixels with GC enabled. Those reads therefore run in one bounded
worker, not in a Tk callback; observations from an old rectangle are discarded.
A follow-up run measured the nonblocking result poll at 0.0012 ms median and
0.0119 ms maximum; the first worker submission took 2.89 ms.
Native validation also exposed Tk invalidating its wrapped text-height cache
after a material color change. Paging could then use an incorrect, even negative,
scroll range. A failing regression now checks that a material change preserves
the long reply's range and reading position; resolving text metrics after the
style update fixes the failure without changing the reading model.

Reproduce the current decision campaigns with pytest installed:

```sh
python scripts/mutate.py assistant/action_text.py --tests tests/test_action_text.py --limit 0 --workers 2 --output dist/action-mutations.json
python scripts/mutate.py assistant/notifications.py --function observe --function observe.finish_turn --function results --function steps --function settled --function caption --function order_of --function advance --tests tests/test_notifications.py --limit 0 --workers 4 --output dist/turn-mutations.json
python scripts/mutate.py assistant/notifications.py --tests tests/test_notifications.py --replace 'for entry in reversed(entries):' 'for entry in entries:' --output dist/turn-precedence-mutation.json
```

These commands exit nonzero for the documented survivors. Mutation runs remain
an explicit audit; editing uses the fast profile, and deployment retains the
full Windows integration gate, including native glass, stop-target, and attached-result
checks.
