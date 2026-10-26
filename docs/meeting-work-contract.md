# Meeting work and atomic material publication

A work resource retains the requested outcome, pinned transcript revisions, a run
and its prepared material. It shares the existing Turso transaction, signed causal
history and Iroh workspace authorization. It is not an external-action scheduler.

## Resource and state

`Revision` has exactly one payload: legacy `body`, existing `surface`, or new `work`.
A work payload is `{type:"meeting", outcome, sources, state}`. Each source is
`{workspace_id, resource_id, revision_id}` identifying an existing note revision
in the same workspace and in the action's causal past. Work IDs are ordinary
resource IDs; selecting a different workspace does not change them.

| State | Retained data and meaning |
| --- | --- |
| `requested` | Outcome and 0–16 source refs are saved. Execution has not started. |
| `preparing` | `run:{id,originating_device,started_at_ms}`. The signed author owns this particular preparation. Time is an observation, not a lease. |
| `needs_input` | That run and a bounded question. No automatic replacement executor is chosen. |
| `materials_ready` | That run, notes reference/citations, assigned actions, and `follow_up:{type:"unsent_text",material}`. This does not mean a message was sent or an Outlook draft exists. |

A `MeetingMaterial` contains `{reference, citations}`. The reference pins its
publication revision for provenance. Opening/editing its ordinary note resource
uses current resource heads, preserving subsequent human edits.

## Commands

Every command carries `operation_id` and `workspace_id` as before. Exact local
retries retain all original fields.

- `create_work {resource_id,title,outcome,sources}` creates Requested.
- `edit_work {resource_id,expected_heads,change}` uses one of the changes below.
- `publish_meeting_materials {resource_id,expected_heads,run_id,notes,actions,follow_up}`
  atomically creates both output resources and the MaterialsReady work revision.

`change` is one of:

- `revise_request {title,outcome,sources}`: explicitly save revised intent under
  the same work ID and return to Requested. Prior material remains in history.
- `begin_preparation {run_id,started_at_ms}`: require one Requested/NeedsInput head
  and at least one transcript. The model derives the origin from the signed author.
  The run ID cannot reuse a run in this work's causal past.
- `need_input {run_id,question}`: require one Preparing head owned by the author.
- `correct_actions {actions}`: replace reviewed action values. Multiple action-only
  heads can be reconciled if their request, run and publication references agree.
  Different publication branches remain a conflict requiring review.

Publication input `notes` and `follow_up` each contains
`{resource_id,title,body,citations}`. New output IDs must be distinct and fresh.
The enclosing command's `resource_id` identifies the work. Both outputs are ordinary
notes; their initial revision IDs and the new work revision ID equal the one signed
publication operation ID. Revision IDs are interpreted together with resource IDs.

That single operation and its original request commit in one existing SQL
transaction. The model validates its complete projection before storage starts.
A stale work head, wrong run/actor, invalid evidence or output-ID collision creates
no output and appends no operation. Importing the signed operation similarly
projects both outputs and the work together. There is no partial multi-command
publication to recover.

## Assignments and evidence

Each action is `{id,text,owner,deadline,basis}`. Owner is `unassigned` or
`assigned {name,basis}`. Deadline is `unspecified` or `stated {text,basis}`; relative
wording remains wording, without inventing a meeting date or calendar deadline.

Evidence basis is `transcript {citations}` or `user`. Each citation is
`{source:{workspace_id,resource_id,revision_id},quote}`. The source must exactly
match one of the work's selected sources, and the nonempty quote must occur in that
immutable transcript revision. A generated assigned name or stated deadline must
also occur in its own supporting quote. These checks establish the source/quote
binding; they do not prove every semantic inference in generated prose.

Generated actions require transcript evidence. Later explicit corrections can use
`user` evidence for their text, owner or deadline. The signed edit attributes the
correction; a new owner or deadline need not occur in the transcript and is not
relabeled as a transcript claim. Notes and follow-up text use existing note editing
and revision-conflict behavior.

Bounds are 16 transcript refs, 64 distinct actions, 1–16 citations per generated
material or transcript basis, 2 KiB per quote, 4 KiB outcome/action/deadline/question
text, 120 bytes for an assigned owner, the existing 512-byte title and 128 KiB note
limits, and 512 KiB for publication material/actions. Existing 4,096-operation and
7 MiB retained-history limits still apply. Inputs are rejected rather than truncated.

## Concurrency, recovery and compatibility

Exact observed work heads are checked at command preparation. Import validates
causal heads, preserving independent offline edits without wall-clock winners.
Revising the request while preparation is underway makes its old publication stale.
Losing contact with the originating device does not authorize another device to
publish or restart that run.

Applied is a local durable receipt. Peer delivery and external execution remain
separate observations. Lost commit acknowledgment remains uncertain. Reopening
and retrying the same publication on its originating host recovers its exact receipt
without duplicate outputs. Original request records do not replicate: another host
must inspect signed work/results rather than treat its command API as a remote
receipt replay endpoint.

Existing note/surface serialization, signature version, operation envelope, SQL
schema and membership checks are unchanged. The new Rust work payload is boxed
in memory to keep ordinary note revisions compact; that does not change JSON.

**Meeting work requires an updated app on every sharing device.** Older clients
reject unknown work actions; they cannot safely import a workspace containing them.
Update those clients before exchanging such work. Never strip the new actions,
disguise them as text notes, clear storage or rotate identity to make a peer accept
an update. The browser host probes `work_capabilities` without workspace data and
supported pair/sync requests declare `work: 1` for that transfer. Both incoming
and outgoing requests gate work exports; incompatible invitations are refused
before their grants are committed or consumed. Note/surface-only workspaces retain
the previous request shape. A refused legacy sync may already have imported its
otherwise-authorized incoming notes; refusal is not a transaction rollback.
An arbitrary transport failure alone cannot establish that a peer needs upgrading.
New decoders also provide an upgrade message for unknown work kinds/states.

## Code and verification

- [Pure values and transitions](../native/core/src/work.rs), called by the
  [shared signed model](../native/core/src/model.rs).
- [Native request matching](../native/core/src/store.rs) and
  [browser domain matching](../browser/host-wasm/src/domain.rs) verify retained
  commands against their signed actions before replay.
- [UI wire types and decoders](../native/ui/src/protocol.ts) expose the same bounded
  immutable variants, including nested decoders for retained commands.
- [Native work tests](../native/core/tests/work.rs) exercise real Turso commits,
  reopen/replay, invalid publication, scope/run ownership, user corrections and
  concurrent peer histories. The browser domain separately checks that a prepared
  publication is invisible until commit and reopens with exact request identity.
- [Decoder tests](../native/ui/src/work-protocol.test.ts) reject malformed and mixed
  variants and distinguish generated transcript evidence from user corrections.

Real browser/provider/Iroh integration, interface behavior and physical iPhone
validation remain separate checks; pure-model tests do not establish them.
