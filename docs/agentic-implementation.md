# Automated PC implementation

The user approved the [interactive architecture](agentic-pc.html) and authorized
an orchestrator to oversee implementation agents. This plan defines the
integration sequence and acceptance criteria. The architecture is a target;
only checked, integrated behavior is reported as implemented.

## Product contract

Voice, typed requests, a work overview, and enabled standing instructions submit
to the same persistent work system. Tasks retain their identity, requested
outcome, progress, waiting reason, action records, and result across application
restarts and conversation resets.

The first complete routine reads a finished meeting transcript, prepares notes
and action items in a task-owned output location, and verifies the saved result.
It is a disabled template until explicitly enabled. Scheduled times and
specified file changes use the same task submission and recovery path.

The work overview can inspect, pause, resume, cancel, answer, and redirect a
task. Closing the overview hides the view. Quitting Toolkit interrupts active
execution for recovery; the user's Stop and Cancel actions end their selected
work. Existing conversation follow-ups and source-workspace drafts retain their
meaning.

## Implementation stages

| Stage | Delivered behavior | Acceptance |
| --- | --- | --- |
| 1. Durable work | Typed task/run/action states, SQLite journal, checkpoint and response records, atomic claims, cancellation and restart recovery | Reopen a real journal; retain progress and waiting requests; reject stale run updates; record an action before effects; inspect an uncertain file write without duplicating it |
| 2. Assistant controls | Voice/typed submission, task overview, stable selection/drafts, exact-ID answers, task controls and conversation compatibility | Use the public runner/view interfaces; new chat leaves task records intact; hidden views remain hidden; cancellation releases execution resources |
| 3. Standing work and context | Disabled routine template, completed-transcript events, explicit schedules/file triggers, transactional outbox, saved preferences/project references | Duplicate delivery/restart creates one task; incomplete/unpublished transcripts do not fire; missed intervals coalesce; stale context is identified; unrelated tasks do not share live histories |
| 4. Supervised work process | Worker-owned execution/native adapters, bounded validated local IPC, UI-owned interaction/audio, keeper supervision and readiness | Restart the worker independently; recover committed tasks; reject stale messages; keep keyboard input isolated; full Windows and extracted-bundle checks pass |

A stage is integrated only after the affected interfaces and behavior pass
review. The application composes the durable frontend and the separately
supervised worker. The legacy runner remains covered for compatibility.
Deployment is owned by the orchestrator.

The runtime currently executes one task at a time. Waiting releases the
execution slot; cancellation waits for actual effect cleanup before a replacement
run starts. Existing dictation and meeting transcription retain their independent
owners. Raw SDK history remains volatile, so recovery uses recorded progress and
fresh inspection instead of replaying a previous model request.

## Ownership

| Owner | Files / responsibility |
| --- | --- |
| work_core | automation/task.py, automation/store.py, core public-interface tests |
| execution_actions | automation/actions.py, automation/execution.py, assistant/agent.py, assistant/agent_execution.py, assistant/sdk.py, execution tests |
| work_interface | assistant/work_window.py, assistant/work_panel.py, coordinated view entry points and UI tests |
| automations_context | automation/routines.py, automation/context.py, routine/context tests |
| process_integration | assistant/machine.py, then explicitly assigned worker/IPC implementation and process tests |
| work_controller | automation/controller.py and public lifecycle/concurrency tests |
| assistant_frontend | assistant/work_agent.py, work_service.py and conversation integration tests |
| supervision | keeper/protocol third-child supervision and readiness tests |
| meeting_integration | final transcript callbacks, startup event recovery and integration tests |
| validation | Independent acceptance review and targeted mutation evidence |
| Coordinated desktop session | assistant/canvas_*.py, winapi/desktop_shell.py, shared UI lifetime hooks and native canvas tests |
| Orchestrator | Shared contract decisions, composition, packaging, final review/gates, documentation, Windows deployment |

Agents edit only their assigned files. Changes to shared contracts are
communicated to their consumers before implementation changes. Existing local
UI work is part of the baseline and is preserved.

The desktop session shares the durable frontend through identified submissions,
cached snapshots, and a bounded replacement set of watched task IDs. Its saved
requests reconnect without resubmission. A lost acknowledgement remains
unconfirmed; explicit retry preserves the original task and invocation context.
The sessions share one final integration check and one Windows deployment.

## Python design requirements

Read [functional design](functional-design.md) and the root AGENTS.md.
The [glossary](../GLOSSARY.md) fixes the domain vocabulary.

- Payload alternatives use unions of frozen, slotted dataclasses; alternatives
  without payloads use enums. External fixed contract values use Literal.
- Important variant handling uses match and assert_never. Nested domain
  values are immutable, not only their containing dataclass.
- Pure functions receive time, identity, and observations. Modules explicitly
  own threads, locks, SQLite transactions, files, network calls, and native/UI
  effects. No worker calls Qt directly.
- Validate external records and IPC at their typed interfaces. Bounded
  messages, lists, and buffers retain one explicit owner.
- New source follows Python 3.15 annotation/type-alias syntax. Bootstrap stays
  importable with system Python 3.12.
- Preserve the keyboard process and acyclic event data. Before/after keyboard
  measurements use the same interpreter, enabled GC, allocation tracing in its
  separate pass, and report the worst event as well as percentiles. Audio sample
  loops and buffer ownership receive the same care.

## Testing requirements

Read [testing through explicit dependencies](testing-dependencies.md) and the
[test cost and mutation audit](testing-audit.md). Their testing approach is an
acceptance requirement, not optional cleanup.

- Test behavior through the same public interfaces used by application
  callers: task store/controller, execution/tool seam, routine/context module,
  view controller, and process transport. A test should survive an internal
  refactor that preserves that contract.
- Use explicit execution, clock, native, and publication adapters. Do not
  replace global patching with assignments to private fields. Native-boundary
  instrumentation must establish an observable native contract.
- Use real SQLite journals, temporary files, local sockets, and local HTTP/SDK
  fixtures where they establish durability or transport behavior. Synchronize
  concurrency with events and observed completion, not sleeps or private
  thread inspection.
- Expected results come from the requested behavior or a worked example.
  Do not assert dataclass defaults, mirror formulas, or add tests solely to
  make a mutation percentage larger.
- Editing uses focused tests and the fast selfcheck. Installation retains the
  full Windows selfcheck and its native integrations. Ordinary validation does
  not control the user's foreground desktop or contact their company endpoint.
- Ruff, strict Mypy, strict Pyright, slots coverage, package discovery, copied
  runtime checks, and the existing startup/Stop/streaming behavior remain gates.
  Type exceptions belong only at real native/SDK/UI adapters and need a reason.

## Targeted mutation review

Use scripts/mutate.py against narrow source/test selections in frozen
disposable source copies. Mutation jobs are an explicit review activity,
separate from normal editing and check-performance measurements.

Before a campaign, its unmodified baseline must execute tests and pass.
Record source/snapshot hashes, commands, selected mutations, killing tests,
survivors, timeouts, and runner/collection errors. A timeout, empty/skipped
baseline, or collection failure is not a detected regression.

The initial review targets task transition eligibility, run fencing,
cancellation versus interruption, exact request-ID answers, routine occurrence
deduplication, completed-transcript gating, stale context selection, and
record-before-effect/recovery decisions. Seed SQL or ordering regressions
explicitly when expression mutations cannot exercise them.

Investigate meaningful survivors and improve behavior assertions where a gap
is real. Document equivalent or out-of-contract survivors rather than adding
tests of arbitrary internals. Reports describe targeted evidence, not a
whole-project score or proof of correctness.

## Integration and deployment

The pre-change baseline passes 474 fast tests, Ruff, Mypy, and Pyright. Source
and verification evidence are frozen outside the checkout before implementation.
The orchestrator reviews each agent's code and tests, integrates the flows, then
runs final gates against a stable working tree.

Deploy the checked tree and refresh the Windows Downloads ZIP. Preserve the
pinned Python and dependency folders when their pins are unchanged. Close a
running copy via its assistant's WM_CLOSE, wait for clean process exit, replace
files, clear bytecode for replaced modules, and relaunch with the bundled
interpreter. Verify file/ZIP hashes, new readiness markers, and all expected
child processes. Report actual failures and untested limitations separately
from passing checks.
