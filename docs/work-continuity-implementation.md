# Meeting work that continues between devices

The first implemented workflow turns saved meeting transcripts into cited notes,
assigned or unassigned actions, and editable **unsent follow-up text**. It retains
one work identity from the requested outcome through preparation, review, and
corrections. The existing website/PWA, Turso Database, Iroh peers, application
references, CodeMirror editor, and workspace OpenAI connection provide the runtime.

## Try it

1. Open a workspace. Under Settings, configure its **OpenAI** connection privately
   in the app. The same workspace connection powers dictation and preparation;
   accepted workspace members receive it through the existing scoped exchange.
2. Choose **+ → Meeting work**. Dictate the desired outcome. Select existing
   transcript notes or import UTF-8 text files, including several recordings from
   one meeting. File parts become ordinary shared notes and are pinned to their
   exact saved revisions. A title is optional to change.
3. Choose **Create work**, then **Prepare meeting material**. Creation saves the
   request; preparation is a separate explicit action. Nothing starts because a
   peer merely receives a requested or preparing work record.
4. Review the result and its citations. **Open current notes** and **Open and edit
   follow-up** open each result's latest document, preserving later human edits.
   **Correct owners or deadlines** supports dictation and optional typing. A human
   correction records a user basis instead of inventing transcript evidence.
5. Open the same work on another paired device to read the results or correct its
   actions. Corrections save locally while offline and exchange when authorized
   peers can communicate again. Concurrent revisions require review.

Update the app on participating devices before sharing meeting work. Existing
note-only hosts cannot validate the added work variants. The current host probes
work support before exporting a workspace's work history and reports that the
other device needs an update. Existing notes and signed serialization retain their
original format; unknown work is never stripped or disguised as a note.
Each pair or sync request declares its work version after a scope-free probe.
Incoming legacy requests are checked too; an incompatible invitation is refused
before its grant is saved. Notes and application surfaces can still exchange with
the previous client when their workspace has no work history.

## What persists and what executes

The Rust domain is shared between browser WASM and optional native hosts. A work
revision holds its outcome, exact source references, actor-bound preparation run,
state, result references, and structured actions. One signed publication operation
atomically creates the notes, follow-up document, and ready work revision. The
database commits that operation and its originating command together. Other peers
replicate signed operations; originating command receipts remain local.

The browser worker reads the specified transcript revisions and uses the exact
captured workspace connection version for OpenAI Responses. It checks quotations,
source identity, and stated owner/deadline wording before returning material. A
quotation match establishes provenance; the person reviewing the material assesses
whether it supports the generated claim. Missing owners/deadlines remain explicit.
Relative deadline wording is preserved without inferring a calendar date.

Preparation publishes against the work revision that began the run. Revising the
request while generation is underway causes a conflict rather than overwriting
the revised outcome. The generated publication is retained locally for review.
User edits to a follow-up use ordinary document revisions and remain separate from
the original publication revision kept for provenance.

## Recovery

The browser host retains private editor input, captured dictation, work forms, and
exact pending commands in a separate local-only Turso journal. These records do
not enter peer history, snapshots, or credential exchange. Explicit submission
shares their contents through normal workspace commands. API keys and audio are
excluded from UI input retention. Ordered generations and deletion tombstones
prevent late writes from resurrecting discarded input.

An unknown save result retains its operation ID and complete command for an exact
origin-host retry. A correlated save receipt followed by a failed read or cleanup
remains a **confirmed save awaiting recovery**; retry loads/cleans the result and
does not send the command again. An interrupted preparation requires an explicit
resume on its originating host. Another device cannot infer permission to resume
from a timeout. Deliberately revising the request can begin a new run; the older
run cannot publish over that revision.

Input already confirmed during a multi-part import remains in Material if import
stops. Recover an unconfirmed part's exact save before selecting remaining files
or adding its saved note as a source. No text is silently truncated. A document
part is bounded to 128 KiB, one work to 16 pinned sources, and one provider request
to 512 KiB of transcript text. Larger meetings need separate work requests.
Each imported source is pinned from its correlated local save receipt; an
overlapping UI refresh cannot turn that confirmed save into an unconfirmed import.

## Scope of this slice

The follow-up is shared text. It is not an Outlook item, and no email is sent.
Structured actions are proposed commitments for review, not executed operations.
CodeMirror remains an embedded editor; Office and VS Code application/document
links keep their existing external-open behavior and vendor permissions.

There is no generic planner, automatic executor takeover, permanent coordinator,
Toolkit execution bridge, or durable relay mailbox. Iroh routes encrypted peer
traffic; it does not retain jobs for offline recipients. Browser suspension and
peers whose online periods never overlap still require a temporal carrier or a
separately implemented durable mailbox.

## Implementation and checks

The [work protocol](meeting-work-contract.md), [provider contract](meeting-provider.md),
and [browser workflow](../.github/workflows/browser.yml) document the runnable
interfaces and checks. Production UI mounts `native/ui/src/app.ts`; it projects
real snapshots and never imports the earlier standalone HTML proposal's simulated
records. Desktop has leading material navigation, an attached page toolbar and an
optional inspector. Touch navigation and page/voice tools keep separate lower-edge
positions, with a local preferred-hand setting. System/light/dark appearance,
safe areas, accessibility fallbacks, and captured input identity apply throughout.

Unit and mutation checks exercise source, actor, run, stale-head, retry, retention,
cancellation, and result-evidence rules. Rendered UI checks use controlled
host/voice fixtures. The dedicated browser work check uses real persisted Turso
profiles and Iroh exchange across Chromium/WebKit, with a **controlled OpenAI
response**. These are distinct from authenticated OpenAI calls, model quality on
the user's meeting, and physical iPhone interaction, which require a real user run.

The native work campaign is runnable with Python 3.8 or later and the pinned Rust
toolchain, without Python packages:

```sh
python3 native/core/scripts/work-mutations.py --report /tmp/work-mutations/report.json
```

It uses disposable source copies and a dedicated Cargo target, checks the
unchanged baseline, requires each intended behavioral assertion, then checks the
restored baseline and cleanup. JavaScript campaigns likewise retain captured
sources and exact failure markers. Compiler errors, setup failures and timeouts
are failed checks rather than mutation kills.
