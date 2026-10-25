# Meeting follow-up validation

The meeting follow-up backend passes its final focused checks: **65 portable
tests** with one Windows-only spelling check skipped, and **26 native Windows
tests** with no skips. All **59 distinct targeted regressions** are detected in
their latest applicable campaigns. Root's latest full portable check also passed
**723 tests**, with no failures and 22 native modules skipped.

This is a **pre-deployment audit**. The final full Windows check passed
**886 tests**, with zero failures and three opt-in end-to-end modules skipped,
in **225.62 seconds**. Earlier 866/4 and 885/1 native attempts remain recorded.
Keyboard measurements and the read-only import preview are included below.
Final ZIP, installation, canonical import, and reload receipts remain external
to this audit; those operations had not run at this gate.

| Source | Applicable campaign baseline | Distinct regressions detected |
| --- | ---: | ---: |
| `meeting_requests.py` | 6 tests | 4 |
| `meeting_followup.py` | 19 tests | 8 |
| `meeting_followup_store.py` | 21 portable; 26 native | 13 |
| `meeting_workflow.py` | 13 tests | 10 |
| `outlook.py` | 16 tests | 9 |
| `agent_execution.py` | 8 tests | 4 |
| `agent.py` meeting tool gates | 8 tests | 2 |
| `meeting_execution.py` | 8 tests; 9 for package binding | 7 |
| `automation/files.py` | 26 native tests | 1 |
| `automation/actions.py` FileVerifier | 26 native tests | 1 |

This is targeted contract coverage, not a project-wide mutation score.
Unchanged guards retain their earlier frozen campaign hashes. The final focused
portable suite includes the path and rename-retry corrections and passed in
**15.39 seconds**; its recorded source hashes were unchanged throughout.

Tests exercise public interfaces with actual SQLite databases, files, a local
HTTP model endpoint, and a local JSON-command child at the Outlook transport
boundary. Concurrency uses public observations and events. Portable campaigns
first establish a passing, nonempty baseline. Native batches collect both test
modules through Toolkit's public `selfcheck.run_tests`, reject skips or missing
cases, and require a passing baseline before running mutants. All mutations use
disposable frozen source copies; no working source was mutated.

Native source, drivers, working directories, temporary files, logs, and
faulthandler output are on `C:`. The final batch passed all 26 baseline tests and
detected all five path/retry mutations in **52.93 seconds** overall. Each child
exited, and source hashes stayed unchanged. The validator performed no GUI,
foreground input, Outlook save, application opening, or deployment operation.

The initial review reproduced two storage defects. An event-controlled public
read showed draft and action saves committing revision 1 but returning another
writer's revision 2. Saves now resolve the exact revision returned by their
transaction. A second probe showed that reading missing history silently
recreated it from the current row. Missing history is now reported as missing,
and inconsistent current state cannot advance. Reintroducing either defect is
detected.

Six early mutation survivors exposed missing assertions for exact transcript
hashes, due evidence attached to its action, citation hashes/offsets/line numbers,
unpacked artifact readback, and current/history consistency. Public corruption
and provenance cases now detect all six. The domain guards already enforced
these contracts.

Integration checks require every draft to have a verified mail receipt for the
selected package. They verify the actual fallback path and distinguish the
saved revision from a newer local edit. An SDK case saves V1, selects V2 with
identical draft content, and requires an explicit V2 save; that save reuses the
unchanged file. UI review also found ambiguous identical retries. Changes now
carry explicit attempt IDs, and bounded receipt history retains every queued
attempt. An artifact-only receipt-key mutation is detected by the event-controlled
retry scenario.

The execution checks also detect unrelated conversation/project context entering
a meeting task, general desktop tools being offered or called directly, narrative
completion without artifacts, partial mail saving, lost-save replay, a changed
Outlook folder, incomplete readback, and recovery that reads the current edit
instead of the exact historical revision.

Windows testing found that final artifact paths of 261–268 characters could fail
ordinary access even though the shorter staging directory verified correctly.
Filesystem effects now use extended Windows spelling while public paths,
manifests, rendered bytes, task identities, and package versions stay canonical.
Deep publication and edit-journal tests verify real filesystem access, failure
cleanup, retry, immutable history, and unchanged bytes. Their inspection and
cleanup use independent native spelling. Removing production normalization
fails the actual deep filesystem operations before cleanup; bypassing it only
in FileVerifier fails the verification assertion.

Intermittent `WinError 5` at atomic directory rename exposed a second issue.
A real directory handle opened for `GENERIC_READ` without delete sharing
reproduced `WinError 32`: recovery failed, and a persistent reader received only
one attempt. The store now makes at most six attempts for native errors 5, 32,
or 33, with five delays totaling 0.46 seconds. It reuses the same complete
staging directory, verifies any competing destination, preserves prior versions
on exhaustion, and propagates unrelated errors immediately.

The native tests verify recovery after the handle closes, bounded exhaustion,
original error propagation, cleanup, and readback. Separate native error
instances for codes 5 and 33 are injected at the public installer seam, followed
by an actual rename and readback. This verifies their classification without
claiming that the real sharing handle produces every code. Removing retries,
retrying every error, or omitting code 5 is detected at the intended failure site.

The complete history contains **103 mutant executions** across 16 campaign
batches: 87 confirmed detections, seven earlier survivors, eight timeouts, and
one excluded failure. All survivors and timeout cases were subsequently detected.
The records retain the following unsuccessful attempts:

- The first path-fix portable store baseline took 56.58 seconds against a
  60-second limit. Two receipt mutants completed; eight timed out after test
  execution began. The eight were rerun serially on the final frozen store:
  all detected, no timeouts/errors, 33.61 seconds overall, with a 180-second
  subprocess limit and a 3.33-second passing baseline.
- The first native store baseline passed 21 of 22 tests and failed an atomic
  rename with `WinError 5`. No mutants ran from that failed baseline. An
  unchanged-source rerun passed all 22 tests.
- An early FileVerifier mutant encountered another rename failure before its
  verification assertion. The initial runner matched the expected test name;
  independent trace review rejected that result as a kill. The final runner
  requires the actual failure site, and the final FileVerifier mutation reaches
  the correct assertion.
- The first real-handle fixture requested metadata access only, which did not
  deny rename. Its assertion failures are retained as fixture failures, not
  production red evidence. The corrected `GENERIC_READ` fixture produced the
  actual sharing error before the retry fix.

The latest broader portable selfcheck passed **723 tests**, with zero failures
and 22 native modules skipped, in **66.45 seconds**. Root recorded unchanged
Python source hashes and exit code zero. Its evidence is
`/tmp/toolkit-meeting-portable-release-1792757857561805508.json` and `.log`.
The earlier 721-pass portable gate remains in the machine-readable history.

The first full Windows failure is retained at `/tmp/toolkit-qt-full-windows.log`:
866 passed, four failed, and three opt-in modules skipped. Its failures were
`test_saved_mail_receipt_must_belong_to_the_selected_package_version`,
`test_sdk_publishes_sourced_notes_complete_email_and_verified_durable_actions`,
`test_wrong_bundle_or_changed_source_cannot_publish_and_generated_edits_are_preserved`,
and `test_assistant_queries_a_layout_that_started_before_it`.

The next full native attempt passed 885 tests and failed the quiet-reopen focus
fixture, with three opt-in modules skipped. The fixture sampled foreground 0
after Hide. Only `tests/test_windows_work_panel.py` changed; root verified that
production Python stayed unchanged and the revised fixture compiled. A deliberate
forced-activation control was detected. The failed gate is retained in
`/tmp/toolkit-qt-full-windows-focus-baseline.log`, and the control is in
`/tmp/toolkit-qt-focus-negative-control.log`.

The final native gate passed **886/886 executed tests**, with three opt-in modules
skipped, in **225.62 seconds**, and exited zero. Its 222-file code/pin manifest
stayed unchanged; its SHA-256 is
`d075de3a16fc1eefa84c5e38a0b5c1e6e5329eefdac91a5d54f3f1143ad3139c`.
Root also recorded Ruff passing and 210 files passing both Mypy and Pyright,
with zero Pyright errors/warnings. The native UI navigation and responsiveness
checks passed and their process exited zero. These are coordinator results,
retained in `/tmp/toolkit-qt-final-validation.json` and
`/tmp/toolkit-qt-full-windows-final.log`.

Root measured the Windows keyboard engine in **before / after / after / before**
order with Python 3.15.1, affinity mask 4, GC enabled, and 100,000 events per
round over five rounds per sample. Each row below gives the median of its five
rounds' quantiles; the last column gives the worst event across those rounds.
All individual round measurements and source hashes are retained in the JSON.

| Sample | Elapsed s | p50 µs | p95 µs | p99 µs | p99.9 µs | Median round max µs | Worst event µs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 — before | 11.289 | 4.5 | 11.6 | 20.7 | 140.5 | 10,575.5 | 59,168.7 |
| 2 — after | 7.847 | 4.7 | 12.0 | 20.4 | 184.6 | 7,129.3 | 25,710.1 |
| 3 — after | 8.227 | 4.8 | 12.5 | 22.1 | 255.7 | 10,712.1 | 52,966.3 |
| 4 — before | 7.405 | 4.8 | 12.2 | 20.0 | 133.3 | 7,186.4 | 30,209.3 |

Each sample recorded **1,780 bytes traced peak**, **855 bytes retained**, and
**zero GC collections during latency measurement**. Assistant modules were not
imported. Central quantiles are close in these runs, but both after samples have
higher p99.9 values than both before samples. **No speedup is claimed.**

These measurements exercise synthetic engine events with a silent host. They
do not measure the live low-level hook, native input, capture, agent work, or
end-to-end desktop latency. Engine and hook source hashes were unchanged in the
pair; the referenced changes were in `keeper.py` and `winapi/windows.py`.
Traced peak measures live memory, not total allocation churn. The four
`keyboard-windows-*.json` files and `keyboard-paired-summary.json` are in the
coordinator's stage:
`/mnt/c/Users/Jim.Lundin/AppData/Local/Temp/toolkit-meeting-final-1792758885965628692`.

At this pre-deployment gate, root's prepare-import and read-only verify-import
passed without a canonical import. `meeting-import-preview.json` in that stage
verifies **one completed task, three recordings, one draft, one email-file
destination, and zero Outlook destinations**. It records **zero mail writes and
zero sends**. This preview does not establish a live import or native Outlook
Save; later operational receipts remain with the release coordinator.

The actual Windows real-model run used `gpt-6.1-sol` with low effort, existing
meeting journals, and the real execution/controller/machine path. It completed
in **79.38 seconds** on an earlier frozen staging tree. This validator inspected
its aggregate verification fields and evidence hashes. Later completion,
receipt, path, and retry fixes have the separate validation above.

| Real-run observation | Verified result |
| --- | --- |
| Selected sources | 3 recordings in order; 2 capture notices retained |
| Generated result | 6 notes, 1 process decision, 1 definite action, 1 complete draft |
| Source provenance | Original, frozen, and packaged bytes match; 34 citation references resolve |
| Package integrity | 8 ZIP entries and 9 artifact files verified |
| Mail | 1 complete 719-character body, no guessed recipient address |
| Delivery | 1 verified `.eml` fallback; 0 native Outlook saves |
| Durable state | 2 verified actions; no unresolved actions or open runs; SQLite integrity OK |

The actual PowerShell status check found no accessible running classic Outlook
session. The complete `.eml` fallback was verified. **Native Outlook Save and COM
readback remain untested**; portable command-boundary tests and a real unavailable
status check do not establish that native success path.

Root reviewed the real result against its sources: the action retained its
unknown owner and relative due wording, conditional discussion was not converted
into assignments, and the output made no candidate scoring or selection decision.
Literal citations establish provenance, not semantic entailment. No personal
transcript text, draft text, or recipient identity is copied here.

Two redundant `reconcile_action` calls were declined after their effects were
already verified. They caused no repeated effect or execution exception and
remain recorded. Root clarified the durable-work instruction text afterward;
its later baselines include that prose change, while earlier mutation campaigns
retain their original source hashes.

[Machine-readable evidence](meeting-followup-mutations.json) includes exact
focused-suite and mutation commands, source/snapshot hashes, passing baseline
counts, required failure sites,
all earlier unsuccessful attempts, the receipt reproducer, and sanitized real-run
evidence. The external evidence directory is
`/tmp/toolkit-meeting-review-1792752568910866821`.

To repeat the focused portable suite with the project's Python 3.15 environment:

```sh
.venv/bin/python -B -m pytest -q tests/test_meeting_followup.py tests/test_meeting_requests.py tests/test_meeting_workflow.py tests/test_outlook.py tests/test_meeting_execution.py
```

The earlier [durable-work validation](agentic-validation.md) and its mutation
evidence remain unchanged. This audit is frozen at the pre-deployment gate;
root retains subsequent ZIP, installation, canonical import, and reload receipts
externally.
