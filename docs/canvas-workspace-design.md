# How the Toolkit canvas should work

Product and interaction research, 24 October 2026. **Proposed behavior, not a
description of a completed implementation.** The accompanying
[source investigation](canvas-workspace-sources.md) distinguishes documented
mechanisms, research-prototype limitations and hypotheses. No comparative
usability study has been run for Toolkit.

## The purpose of the canvas

Give a piece of work a durable place where its material, reasoning, operations
and unfinished changes remain connected. A useful workspace should help answer:

- What am I trying to achieve, and which material matters?
- What can I do with these things together?
- What did we decide, and what was that based on?
- What changed, what might it affect, and what needs my attention?
- Which changes actually reached their destination?

The spatial canvas makes these relationships visible and lets the user work
among them. Applications supply editing and domain capabilities. The assistant
helps interpret, connect and act on that material through the same workspace
operations available to the user. Conversation is another way to address the
work; useful state must remain understandable without rereading it.

The distinctive capability to pursue is **turning a working set into a small,
reusable tool for the job**. A launch budget becomes an adjustable comparison
with a recorded decision and related draft. A meeting becomes decisions,
assigned actions and follow-ups that can be revised together. The tool grows
out of actual work, without requiring the user to design an application first.

This is a product hypothesis, not a novelty claim. Allume already exposes board
objects to agents; tldraw already combines agents with structured canvas
actions. Embark demonstrates a more substantial mechanism: one structured
route result supplies distance to a cost calculation and geometry to a map.
Its authors also found that connected widgets alone failed to preserve their
reasoning. Toolkit needs shared facts, useful operations **and** the explanation
of how a decision was reached. [Embark][embark], [Allume][allume],
[tldraw][tldraw]

## Three approaches, and the recommended combination

| Approach | Useful for | What it leaves unresolved |
| --- | --- | --- |
| Spatial collection of apps and excerpts | Comparing material, keeping it nearby, returning to an arrangement | Applications still have unrelated data; the user carries the workflow between them. |
| Task queue with agent results | Delegation, progress, waiting questions and recovery | The documents, decisions and edits can remain detached from the task. |
| Workspace with shared resources and operations | Combining facts, exploring options, revising related artifacts and resuming work | Needs trustworthy adapters and careful change semantics; does not appear automatically when an app is embedded. |

Use the third model with the existing canvas as its spatial presentation, the
existing durable work system as its execution owner, and ordinary focused
applications for detailed work. Keep a compact list/activity presentation over
the same state. Remembering a card's coordinates must not be necessary to use
the workspace.

## Start with material; let structure emerge

Opening a document or dropping a note should be enough to begin. The existing
launcher, **Add to canvas**, and selection composer are good entry points.
Naming a workspace, specifying an outcome, grouping resources and saving an
operation can happen as those actions become useful. No compulsory project
form, taxonomy or graph wiring precedes the first useful action. This follows
Potluck's gradual enrichment of ordinary material, while acknowledging its
limited evidence of sustained real use. [Potluck][potluck]

A workspace may contain several tasks toward an outcome. “Prepare the autumn
launch” is a working context; comparing costs and preparing a follow-up are
individual tasks. A general scratch workspace can also remain loose. Do not
turn every open window into an agent task or infer that an apparently empty
workspace has no unfinished work.

The outcome can appear as one editable line near the workspace title. The
current header's resource counts can yield to useful state such as
“Budget options · 1 quote needed.” The user can keep informal notes and rejected
options alongside the structured result. A model may suggest a constraint or
decision, but it must identify that suggestion until it is adopted.

## Resource, view, observation and operation have different jobs

These are design distinctions behind the UI, not terminology users must learn.
They build on the existing [glossary](../GLOSSARY.md).

| Concept | Example | Required behavior |
| --- | --- | --- |
| Resource | The particular launch workbook | Keeps its identity when shown elsewhere, opened in another view or temporarily unavailable. Its application remains the content owner. |
| View / placement | Workbook editor, cost-range card, compact overview entry | Shows a useful presentation of that resource. Closing or moving the view does not delete the workbook. Multiple views must not create accidental copies. |
| Observation | The values read from that range at 09:14 | Records what was actually available, its coverage and version. Later edits do not rewrite evidence already used by a task. |
| Operation | Compare costs with the cap; prepare follow-up | Names its inputs, parameters and output. It can be rerun or reused without reconstructing a prompt from pasted prose. |
| Decision or draft | Choose option B; write the funding request | Retains reasoning, sources and human edits. It has a lifecycle distinct from the operation that first produced it. |
| Observed outcome | Draft revision 4 saved to a particular destination | Establishes what happened. A request to save and a model's completion message do not establish that fact. |

Support exact document sections or ranges where an adapter can identify them.
Otherwise show the whole-document observation or a clearly identified excerpt.
Do not imply a durable cell or paragraph binding from screen coordinates or a
changing DOM position. A remote document ID, a current live-page target and a
URL have different guarantees. If identity cannot be re-established, show the
saved observation and let the user reconnect it.

JSON Canvas illustrates file/subpath references separate from geometry; Fluid
handles illustrate several structures referring to one object. Neither solves
Toolkit's external document identity or application integration for us.
[JSON Canvas][jsoncanvas], [Fluid handles][fluid]

## Applications contribute at three levels

1. **Open and edit.** A native window or hosted web app provides the actual
   application, including its dialogs and specialized tools. Focus it at a
   useful size. A thumbnail is a preview, not an editable application.
2. **Expose useful material.** A supported adapter provides an addressable
   document, range, passage or message with a bounded observation. The canvas
   can show that alongside other resources while preserving a path to the
   original. A browser capture is readable evidence, not a general structured
   Office API.
3. **Operate on that material.** Where supported, an adapter can apply a
   precisely targeted edit and verify it. The shared operation retains that
   destination, the expected state and the outcome. Otherwise the user opens
   the original app or exports a draft to finish there.

The common frame supplies selection, origin, task state and navigation. The
application remains recognizable. Keep the requested small document header;
put recovery/navigation controls in its menu and use supported app layouts for
compact editing. See [document surfaces](document-app-surfaces.md) for the
Office and VS Code limits. This research does not add any integration claims
to the existing [WebView2 evidence](webview2-ui-investigation.md).

Saving many resource references must not launch every editor. Load a view when
the user or an authorized task needs it. Parking removes visual clutter;
preserving a resource does not inherently require keeping its editor running.
An unsaved, uncertain or in-flight application must remain alive unless its
state can be preserved reliably. If an adapter cannot establish that it is safe
to unload, do not discard it. Workspace recovery can restore identities,
observations and Toolkit drafts; it cannot promise recovery of arbitrary
unsaved third-party application memory.

## Connections must cause understandable behavior

Most connections should be recorded by useful actions. Selecting inputs and
creating a comparison establishes its evidence links. Making a draft records
its origin. Saving records its destination. Users can group and annotate
freely without programming a dependency graph.

| Connection | How it arises | What it means |
| --- | --- | --- |
| Used these observations | An operation reads explicit inputs | Inspect the exact evidence; identify a newer observed version. |
| Computed from these values | A validated calculation binds named inputs | Recompute its current view when those inputs change, if enabled; retain prior saved results. |
| Decision based on this | A user adopts an option or reasoning | Preserve the decision and flag a changed basis for review. Do not silently replace the decision. |
| Draft prepared from this | A result is used to create or revise writing | Suggest an update when relevant; preserve the user's current text. |
| Saved as this | The destination confirms a particular revision | Open the real destination and distinguish it from later local edits. |
| Waiting for this | A task records a concrete missing input or prerequisite | Explain the blocker and offer the action that can resolve it. |
| Related / grouped | The user or an accepted suggestion groups material | Support navigation; confer no computation, access or write authority. |

Visible wires can appear for the selected item and its related work. Origin
tags and “used by” navigation provide the same information without a wall of
crossing lines. A typed dependency can establish that an output used an older
input; it cannot establish that every conclusion is now false. Use “Budget
changed since this decision” rather than inventing a semantic judgment.

The current model already propagates source staleness through evidence links.
The missing behavior is richer explanation of the affected work, meaningful
operations and revision review. tldraw's workflow kit is instructive here:
computational edges require explicit ports, bindings and execution semantics
beyond ordinary canvas arrows. [Workflow kit][workflow]

## Context should be visible and convenient

Keep **Use as context** and the selection chips. Selecting a resource, focusing
its editor and selecting text inside it are separate actions. A focused app
does not silently become the whole context of the next request.

The assistant can suggest relevant workspace material, and the user can pin a
brief or constraint for this piece of work. A request should expose the named
inputs it will actually use. Retain their identities and observations when the
request starts. Moving a card, navigating an app, changing focus or switching
workspaces afterward must not change those bindings. Embark's explicit formula
arguments are a useful precedent: suggestions help choose inputs; committed
operations keep concrete references. [Embark, Appendix I][embark]

Keep these different scopes intelligible:

- **Analyze this selection:** use the captured evidence under today's existing
  restrictions. Showing its task in the conversation does not add live tools.
- **Work on these resources:** act through explicitly authorized live-resource
  operations, with fresh state where needed. An evidence link alone does not
  authorize writes to the original application.
- **Keep this operation current:** a separately enabled standing instruction
  with identified inputs and a bounded trigger. It is never inferred merely
  because a result has dependencies or the workspace is open.

The labels above express the scope choices, not a required extra dialog for
every command. Existing authorization carries through. “Shorten this local
note” can execute as an ordinary reversible edit. A request to save can use its
already authorized scope. Review is useful when selecting a proposed revision,
resolving ambiguity or satisfying an existing action policy.

For voice, show the addressed selection/task as recording begins and retain
that binding through submission. Respect the existing pending-question routing
and dictation behavior. The user should be able to see what “these” means
without a narration of internal IDs.

## Turn one useful answer into a reusable operation

The next budget result should offer more than another prose response. It can
contain a table with current values and proposed options, a total and gap, the
constraints it respects, and unresolved assumptions. The user adjusts a value
directly; a deterministic calculation updates the affected total immediately.
Another presentation can show the same values as a comparison or a paragraph
for a draft.

Begin with a small set of trusted views: document/excerpt, table, options,
comparison, proposed difference and task progress. The agent selects a view and
supplies validated data. It can propose an extraction or transformation, but
the UI must distinguish a source value, a computed value and an assumption.
An extracted “200,000 kr cap” stays connected to the passage that supports it.
Initial operations should use known calculation types, not arbitrary privileged
scripts generated into the shell.

There are three different update behaviors:

| Update | Proposed behavior |
| --- | --- |
| Deterministic local calculation | Update the current preview from explicit inputs; preserve saved revisions and expose the rule. |
| Agent interpretation or proposed rewrite | Run when requested or under an enabled instruction; create a new result/proposal with its exact evidence. |
| Change to an external document or service | Apply through its actual capability and existing policy; retain the expected state and verify the outcome. |

This prevents every small edit from becoming another model request, while
allowing an operation to retain useful reasoning. A user can reuse “compare
this budget against that brief” with new explicit inputs, or enable a specific
watch later. The initial goal is one dependable reusable operation, not a
general visual programming environment.

## A concrete launch workflow beyond the existing sketch

Use the approved design's existing material: **240,000 kr** of costs, a
**200,000 kr** cap and a captured statement that the venue is booked. The latter
is simulated material in the original sketch; it is not evidence of a shipped
Teams message adapter. The budget and brief already have bundled local files.

| Step | What the user does | What the workspace must retain or show |
| --- | --- | --- |
| 1. Establish the work | Select the budget, brief and venue statement: “Find a plan within the cap, keeping the venue.” | Named inputs, the 40,000 kr gap, the stated constraint, and a comparison bound to those observations. |
| 2. Explore options | Adjust the proposed travel and AV costs in the comparison. | Current costs remain intact. A proposed travel cost of 20,000 and AV cost of 30,000 yields 200,000 with venue 84,000 and catering 66,000. Those proposed prices say **Needs quote**; arithmetic does not establish feasibility. |
| 3. Make a decision | Choose “Proceed with this option, pending quotes,” and add a reason. | The chosen option, unresolved quotes and reason remain attached. Alternative options remain inspectable without creating a pile of duplicate documents. |
| 4. Prepare the next action | Ask for the related document changes and a follow-up draft. | A purposeful proposed revision: target changes, draft difference and their basis. Unsupported application writes stay an explicit handoff to the original app. |
| 5. Encounter a change | Read the source again after travel rises from 42,000 to 62,000. | Current total becomes 260,000; the old comparison still used 240,000. Show the changed 20,000 and the affected comparison/decision/draft. The chosen hypothetical option is preserved for reconsideration. |
| 6. Reconcile while writing | Keep editing the draft while the agent proposes the updated explanation. | Compare the proposal with the current writing. Preserve human edits and focus; show a conflict if both changed the same material. |
| 7. Continue in an app | Save the selected revision through a supported destination. | A matching saved receipt, linked to that exact revision. A later local edit visibly differs from the saved version. |
| 8. Leave and return | Switch workspaces, then reopen this work later. | Return to the last useful view with “Travel changed; decision needs review; draft revision 4 saved.” Show only facts established by observations and receipts. |

This is one connected decision-and-revision loop. It should require less
repeated source selection, copying, context explanation and manual
reconciliation than the same work in separate apps with the same assistant.
The unchanged venue constraint and the unresolved prices are just as important
as the arithmetic: the workspace must preserve why an option is acceptable or
still uncertain.

## Show proposed changes where their consequences are

An agent may propose a group of changes with one purpose, such as “Revise the
funding request for the higher travel estimate.” Present the affected objects
together with readable before/after differences and their evidence. The user
can inspect or select changes without reconstructing the proposal from chat.
Upwelling's named draft layers and deferred incorporation of other changes are
useful precedents. [Upwelling][upwelling]

Comparison and application must use the intended current revision. If the user
changes the same paragraph or cell, preserve both versions and resolve the
conflict. A stale review cannot authorize silently replacing newer content.
Unrelated edits can be preserved when the adapter supports that operation;
do not claim a general semantic merge for every app.

A group spanning several applications is not an atomic transaction. If the
workbook saves but the mail draft fails, show those two outcomes separately.
Retry must inspect an uncertain destination before repeating a possible
effect. Revert can restore supported local content or propose a compensating
edit; it cannot universally undo external actions. These limits should appear
at the affected item, rather than become a generic warning on every action.

The existing draft preservation, meeting edit conflicts and saved receipts
provide a foundation. A general proposed revision across resources is new work.

## Keep the approved visual language; make navigation do more

| Existing element | Evolve its role |
| --- | --- |
| Top bar | Fast workspace switching and a compact count of work needing attention. Detailed state belongs with the work it concerns. |
| Launcher | Find a document, result or unfinished piece of work and resume it. Show the relevant workspace and a useful next action. App launching remains available. |
| Canvas | A stable working arrangement of a few useful views, notes, options and drafts. New output appears at its reserved destination without moving the user's active material. |
| Focus | Edit a document at a useful size, or show the selected proposal beside its source/current version. Preserve editor state when returning. |
| Overview | Show compact representations of related work and pending decisions. Do not merely shrink every live application until it is unreadable. |
| Selection composer | Named input chips and an action about those objects. Full conversation history remains available on demand. |
| Agent bubble / task card | Proportionate progress or a concrete question linked to the task. A useful result has a durable resource identity. |
| Parked items | Retain a route back to the resource and explain an actual pending task when present. Visual parking and safe application suspension remain separate. |

A direct **Review changes** action can use the existing Focus presentation.
The spatial arrangement remains available; an activity/list view exposes the
same items to keyboard users and people who do not want to navigate a large
surface. Expose relationships and state text accessibly; color and connecting
lines cannot be the only indications. Spatial commands must stay scoped to the
shell, so native app editing shortcuts and Toolkit's keyboard layers keep
their roles.

Take Omarchy's direct navigation and coherent controls seriously. Take niri's
stable placement seriously: adding an output must not unexpectedly shrink or
rearrange what the user is editing. Exact Linux hotkeys or layout algorithms
need not be copied. [Omarchy][omarchy], [niri][niri]

## Resuming work is a core interaction

On return, provide a short factual account tied to the actual resources:
“You were editing the follow-up. The travel source changed. One task needs an
answer. The last saved draft is revision 4.” Each item opens its relevant
material or control. Keep the previous camera/focus unless the user chooses a
different view. Coalesce routine progress updates; highlight changed inputs,
unresolved decisions and actual failures.

The activity projection should derive from known task state, resource
observations and saved outcomes. An optional generated explanation can cite
those events. It must not claim a source is current merely because no change
was observed: show the last read time and unavailable sources honestly. A
whole-document adapter can flag only that broad observation; precise field
impact requires a more precise adapter.

Task identity and destination are independent of the visible workspace. A
completed task remains in the work that originated it, including after restart.
Opening another workspace cannot redirect its result, and restoring a view
cannot resubmit the task. The current backend executes one task at a time;
the proposed UI must show queuing honestly rather than imply concurrent
agents.

## Fit with the implementation already present

| Area | Existing foundation | Additional work implied by this design |
| --- | --- | --- |
| Context and task binding | `canvas_model.Request`, immutable evidence, captured-source restrictions and exact durable attachments | Shared conversation association with explicit scopes; live operations bound to the originating workspace. |
| Resources and presentation | `Item`, `Placement`, source observations, live app configuration, saved arrangements | Durable external-resource resolution where supported; several useful views of one resource; separate view/session lifetime. |
| Change awareness | `canvas_model.outdated`, deliberate refresh and frozen source evidence | Explain affected inputs, compare result revisions, preserve decisions and expose reusable operations. |
| Editable results | Local drafts, `canvas_drafts` verified handoffs, meeting edits with expected revisions | General proposed differences and conflict review across supported resource types. |
| Agent app access | Ordinary conversation already has registered browser tools | Common workspace commands for creating, relating and updating resources; exact destination binding. |
| App lifetime | Persistent profiles; hidden/parked pages remain alive; current browser cards load eagerly | Load on demand; safely suspend only recoverable views; retain unsaved and in-flight work. |
| Work and meetings | Durable task state; cited meeting notes, actions and draft packages | One useful attention/resume projection across those objects, with consistent task controls. |

Source anchors: [canvas model](../assistant/canvas_model.py),
[request scope](../assistant/canvas_requests.py),
[work agent](../assistant/work_agent.py),
[canvas host](../assistant/canvas_web.py),
[draft handoffs](../assistant/canvas_drafts.py),
[meeting projection](../assistant/meeting_canvas.py), and
[current guide](desktop-canvas.md).

The proposed [shared-workspace ADR](adr/0002-shared-agent-workspace.md) and
[runtime direction review](runtime-direction-review.md) own the implementation
boundary. Use one `WorkspaceControl` observe/command interface, implemented by
the existing Python owner first. The UI and agent address the same resources;
the runtime continues to own tasks and effects. Changing Qt to WebView2 or
Python to TypeScript is a separate implementation migration. This product
model must work through either host.

## What to build and what would count as evidence

1. **Establish the shared interface.** The ADR's “summarize this page into a
   note beside it; shorten that note” scenario is a useful correctness gate.
   Include workspace switching and concurrent manual edits. It does not by
   itself establish the canvas's product value.
2. **Build the launch decision loop above.** Use real imported text/CSV
   observations and a small validated budget operation. Add adjustable
   assumptions, an explicit decision, proposed draft differences and the
   existing verified unsent-draft handoff. CSV cards are currently read-only;
   proposed spreadsheet values must remain proposals until a capable write
   adapter is delivered. Do not simulate an Excel save and call it integration.
3. **Exercise the same behavior on meeting follow-up.** Use the existing saved
   package, quoted decisions, action edits and draft receipts. A corrected
   owner or due date should produce an understandable proposed change while
   preserving a user's current writing. This tests whether the model transfers
   beyond a bespoke budget demo.
4. **Evaluate interruption and repeated use.** Repeat after a source change,
   after switching workspaces, and after reopening Toolkit. Reuse the operation
   with fresh material; the user should not rebuild the working structure.

Compare against ordinary windows plus the **same agent, source access and
capabilities**, with comparable familiarity. Include time arranging cards,
choosing context and resolving ambiguity, not just generation time. Use the
[three workflow protocols](canvas-workspace-sources.md#three-workflow-hypotheses-to-test)
for decision/revision, concurrent writing and resumption.

Measure time to the next correct action; repeated copying or context
restatement; whether the user can explain a decision's evidence; correctness
of reviewed versus saved revisions; lost edits; duplicate actions; and view
load/resume cost. Test a small viewport and the keyboard/list path as well as
the spatial layout. A successful result needs both useful task performance and
honest state; an attractive demonstration does not substitute for either.

If the user still has to reconstruct the workflow in chat, arrange many cards
before making progress, or manually reconcile every changed artifact, the
design has not earned its complexity. Prefer the simpler focus/list behavior
where it works better. The next research decision should follow observed work,
not how much more can be put onto an infinite canvas.

[potluck]: https://www.inkandswitch.com/potluck/
[embark]: https://www.inkandswitch.com/embark/
[upwelling]: https://www.inkandswitch.com/upwelling/
[allume]: https://allume.com/mcp-setup/
[tldraw]: https://tldraw.dev/starter-kits/agent
[workflow]: https://tldraw.dev/starter-kits/workflow
[jsoncanvas]: https://jsoncanvas.org/spec/1.0/
[fluid]: https://fluidframework.com/docs/concepts/handles
[omarchy]: https://omarchy.org/manual/navigation/
[niri]: https://github.com/YaLTeR/niri/blob/main/README.md
