# What makes a canvas a working environment?

Primary-source research, 24 October 2026. This note supports the next Toolkit
canvas design; it does not describe newly implemented behavior. Sources were
read on this date. Research prototypes, product documentation and our design
hypotheses are distinguished below. No external application was installed or
operated to validate these sources.

The question is what a user can accomplish through the workspace that remains
awkward with windows and an assistant conversation. Rendering applications on a
large surface is insufficient. The strongest mechanisms found here concern
shared data, reusable operations, explicit inputs, meaningful revisions and a
durable record of why the work took its current form.

## Findings from existing systems

### 1. Potluck: let structure grow out of useful material

**Documented mechanism.** Potluck begins with an ordinary text document. Live
searches extract data, formulas compute with it, and annotations put computed
values or controls back into the document. Examples include scaling recipe
quantities and tracking chores. Its authors call this gradual enrichment: the
document is useful before anyone creates an application around it. The October
2022 report explicitly says the prototype expects basic JavaScript knowledge
and its test users were mostly skilled programmers. [P1]

**Transfer hypothesis.** A Toolkit workspace should accept a rough note, a
couple of sources and a question immediately. Structure can emerge when the
user compares costs, chooses an option, records a constraint or saves a draft.
Requiring everyone to create a project schema or wire a graph before doing
useful work would give up this benefit.

**Limit.** A model extracting a number from prose has not established a reliable
data contract. Toolkit must expose the relevant passage and interpretation
when a calculation depends on it. Potluck is evidence of a mechanism, not proof
that arbitrary documents can become dependable applications automatically.
Embark's subsequent report also says Potluck produced many demos but saw little
authentic use in serious contexts. [P1, P2]

### 2. Embark: share data and computations, not only rectangles

**Documented mechanism.** Embark combines structured mentions, formulas and
views in an outline. A route result contains distance and geometry: another
formula can calculate its cost, while a map can display it. A calendar can
combine weather data and opening hours because they are available in a common
data representation. These are compositions across capabilities, rather than
two complete apps being displayed beside each other. [P2]

The report identifies a limit of shallow embedding: separate embedded maps do
not become one combined map when their host cannot access the underlying data.
Its early widget-composition prototypes also failed to preserve the planning
thought process; users still needed a separate notes application. The later
document model retained informal notes alongside computations and their
results. [P2, sections “Transclusion” and “Documents capture thought process and
context”]

**Especially relevant interaction.** Appendix I makes each computation's
arguments concrete and visible. Autosuggest and repeat can propose arguments,
but after insertion each operation refers to specific inputs. The authors
acknowledge that changing an itinerary date then requires updating some formula
arguments: convenient inference and predictable binding are different things.
[P2, Appendix I]

**Transfer hypothesis.** A budget range, a budget limit and a venue constraint
should become addressable inputs to a comparison. The result should expose
useful structured facts and explanations that can feed a decision or draft.
The user should not repeatedly copy a prose answer into the next prompt.
Toolkit can suggest a relevant set of sources, then show which exact sources
and revisions a submitted operation uses. Moving cards must not silently alter
that binding. The workspace needs a place for reasoning and decisions as well
as content.

**Limits.** Embark deliberately assumed compatible schemas, hardcoded a small
set of formulas and views, and did not solve integration with arbitrary real
applications. It reports use by its authors on real trips and acknowledges
usability and collaboration problems; this is not a controlled productivity
study. It treats history of changing live formula values as a possible addition,
not a completed feature. Its outline provides hierarchy that a free spatial
canvas would need to obtain through some other interaction. [P2]

### 3. Upwelling: treat a proposed revision as a unit of work

**Documented mechanism.** Upwelling's writing model uses named draft layers.
Related edits can be reviewed and merged as one unit; unrelated edits can stay
in separate drafts. Change tracking is retained independently from whether
change highlighting is currently visible. People can write comfortably and
inspect the difference when it matters. [P3]

When another layer is merged, a writer looking at an active draft gets a
notification and can delay incorporating the changes until convenient. The
report argues that collaboration needs both continuous editing inside a draft
and deliberate integration of a completed set of changes. [P3, sections
“Updating a Draft” and “Both real-time and asynchronous collaboration are
needed”]

**Transfer hypothesis.** An agent should be able to propose “update these three
parts of the launch plan for the revised budget,” with a single stated purpose
and inspectable changes. The UI should preserve the user's active draft,
selection and layout. A pending revision belongs next to the affected material;
the user should not have to discover it by rereading a conversation. Reversible
local work can follow existing authorization without making every edit a
permission dialog.

**Limits.** This is a document-collaboration prototype, not an agent system or a
transaction engine for Office, browsers and files. The authors explicitly show
that automatic CRDT merging can produce text needing human correction and
cannot resolve every semantic conflict. They also disclose that their
prototype does not enforce the latest-version prerequisite needed to prevent
surprises from simultaneous final merges. Partial revert within a draft is
listed as unimplemented future work. Toolkit must retain its own exact revision
checks and action receipts; a visual change group cannot promise universal
atomic commit or undo. [P3]

### 4. Muse / Allume: a board can expose actual objects to an agent

**Documented mechanism.** The current Muse site redirects to Allume, which
identifies itself as formerly Muse. Its product page describes nested boards
containing notes, images, PDFs, links and other files. Its first-party CLI and
MCP documentation expose workspace IDs, board content, card content, search,
and creation/movement of cards. An agent can work with addressable board
objects rather than only receive a screenshot. [P4, P5, P6]

The documented integrations distinguish read-only and read-write modes. They
depend on the Mac application being running, including when it is in the
background. The documentation explicitly says there are currently no per-board
controls: enabling access exposes all local workspaces and boards to that
integration. [P5, P6]

**Transfer hypothesis.** Toolkit's agent-facing workspace API should expose
stable object identities, scoped search, readable contents and narrow
operations such as placing a result or attaching a source. The UI and agent
should address the same objects. A generic “canvas attached to chat” is already
available elsewhere and is not a sufficient product distinction.

**Limits.** These are documented product capabilities, not our independent
runtime verification. Board access is not the same as durable backend work,
source-version tracking or arbitrary external-app editing. Toolkit's explicit
selected-source context is useful and should not be replaced by global board
access merely because another product exposes that mode. Allume's guide
redirected to a JavaScript-only Notion page, so no claims here rely on its
unread guide contents.

### 5. Obsidian / JSON Canvas: separate the view from its referenced material

**Documented mechanism.** JSON Canvas 1.0 separates text, file, link and group
nodes. Each node has an identity and geometry. A file node stores a file path
and can point to a heading or block through a subpath. Edges have endpoints,
visual attributes and optional labels. Obsidian's own Canvas documentation
describes independent text cards, file cards, labeled connections, groups and
navigation along a connection. [P7, P8]

**Transfer hypothesis.** A document's identity and its representation on a
workspace should be separate. A card can show the useful part while preserving
a route to its source. One resource can have more than one useful view without
becoming multiple unrelated documents. Informal grouping and annotation should
remain possible without declaring an executable workflow.

**Limit.** JSON Canvas's edge schema does not define execution, dependency
invalidation, authorization or verified effects. A line with the label
“depends on” is a visual annotation unless Toolkit gives it an explicit domain
meaning. File paths alone are also not a complete identity scheme for remote
documents, replaced files or live application instances. [P7]

### 6. tldraw: semantic actions matter more than an agent-shaped cursor

**Documented mechanism.** tldraw's Agent starter kit gives an agent both
screenshots and structured shape data, including selection, viewport, recent
user actions and session history. Its action system supplies validation,
execution and presentation for each action. Modes select what context the
agent receives and which actions it can perform. The agent can create, update,
delete and arrange shapes as well as inspect other parts of the canvas. [P9]

Its separate Workflow starter kit makes connections computational: typed node
shapes, ports, bindings, data flow and an execution engine are additional
machinery beyond ordinary drawing edges. [P10]

**Transfer hypothesis.** Toolkit should define meaningful commands such as
“compare these observations,” “propose changes to this draft” and “place this
result beside its source.” Those commands should have inspectable arguments
and explicit resulting state. Drawing arbitrary arrows or simulating the
agent's mouse does not substitute for those contracts. Most connections should
arise from useful work, so users do not have to program a graph by hand.

**Limits.** The agent kit targets drawings and visual applications. Its broad
viewport/session context is not a reason to pass every visible work document to
a Toolkit request. Shape undo cannot reverse an email send or guarantee the
state of an external service. The workflow kit is a programming interface; its
complexity is a tradeoff, not evidence that node wiring should become the
default desktop experience. [P9, P10]

### 7. Fluid Framework: reference the same thing in several places

**Documented mechanism.** Microsoft's Fluid Framework synchronizes shared data
structures and is independent of the chosen UI framework. Its handle model
exists in part so several structures can refer to the same collaborative
object, rather than each contain a copy. A handle encapsulates retrieval of
that object. [P11, P12]

**Transfer hypothesis.** A draft shown beside a meeting and on a project
workspace should retain one document identity with several placements. Captured
evidence remains a separate, versioned observation of that document. This
distinction makes editing, history and “where else is this used?” understandable.

**Limit.** A synchronized data substrate does not establish the meaning of a
relationship or solve integration with a service that owns different data and
permissions. These sources do not demonstrate a universal live embed for
Word, Excel or arbitrary Windows applications. They also do not justify adding
Fluid itself as a Toolkit dependency. [P11, P12]

### 8. Omarchy / niri: make navigation direct and placement dependable

**Documented mechanism.** Omarchy's current navigation manual defines direct
keyboard actions for launching, focus, workspaces, moving windows and grouping.
Its workspace layout can be dwindle, scrolling or floating, and the selected
layout persists per workspace. These are concrete interaction choices behind
its coherent desktop experience, separate from its appearance. [P13]

niri's scrollable tiling arranges windows in columns on a strip; its README
explicitly states that opening a new window never resizes existing windows.
Its workspace guide distinguishes a workspace from its current position:
an index addresses whichever workspace occupies that position, while a named
workspace can be addressed by name and retained when empty. [P14, P15]

**Transfer hypothesis.** Preserve Toolkit's familiar launcher, top bar and
keyboard access while letting people choose overview, side-by-side work or
focused editing. New agent output should have a predictable destination without
shrinking or moving what the user is reading. Work must bind to a durable
workspace identity, not the workspace's current display index or whichever
workspace is visible when a result arrives.

**Limits.** These sources describe Linux desktop/window navigation; they do not
establish Windows embedding behavior or shared resource semantics. Exact
keybindings and dynamic empty-workspace removal should not be copied blindly:
Toolkit has existing keyboard layers and an apparently empty workspace may
still own ongoing work. Spatial stability is a useful design constraint, not
proof that an infinite surface improves task completion.

## Cross-source implications for the existing Toolkit design

These are proposed design directions, not external research results:

| Question | Direction supported by the mechanisms above |
| --- | --- |
| What is a workspace for? | Preserve a piece of work's relevant material, reasoning, operations and unfinished state; spatial arrangement is one way to use that record. |
| What is an app on the canvas? | Sometimes the full app; sometimes a view of a resource; sometimes a capability that operates on resource data. These levels must be distinguishable. |
| What does a connection mean? | Record exact source/result and revision relationships automatically when work creates them. Keep informal user annotations distinct from executable dependencies. |
| How does context become convenient? | Suggest named inputs using the workspace, then retain concrete references and observations. Selection and source chips remain inspectable. Proximity is not implicit consent or a stable binding. |
| How do changes propagate? | Current observations may change; previous evidence remains fixed. Mark affected results, offer a new calculation or proposed revision, and preserve reviewed writing. |
| What does the agent leave behind? | Useful content and named changes linked to their inputs, with real destinations and saved state where applicable. Progress text alone is not a deliverable. |
| How much structure is required? | Start with a few useful objects and notes. Offer reusable operations when a pattern proves useful; avoid mandatory graphs, forms and taxonomy. |
| How do we return after interruption? | Present outstanding decisions, changed inputs and the next useful action in the same workspace. Treat this as a hypothesis requiring testing, not a known benefit of spatial memory. |

The existing [canvas contract](desktop-canvas.md) already documents immutable
selected evidence, source-change indicators, deliberately refreshed results,
preserved editable drafts, exact draft-save receipts, parking and durable task
reattachment. The [meeting workflow](meeting-followup.md) already connects
transcripts, quotations, decisions, actions and unsent drafts. The next design
should compose and make these capabilities legible rather than announce them
as new inventions. The approved sketch's budget/brief/venue example is a good
starting task, but must be exercised under real revisions and interruptions.

## Three workflow hypotheses to test

These are proposed experiments. Thresholds below are design targets, not
measured performance claims. Compare the same task with the same underlying
agent and source access in ordinary windows plus chat, so that model quality or
extra tools are not confused with benefits of the workspace.

### A. Make and revisit a decision using several sources

Use the approved launch-review materials: budget rows, a spending limit and a
venue constraint. Ask the user to choose and explain a viable reduction, then
prepare the resulting communication. Record the chosen option, the rejected
alternative and the exact supporting sources together. Introduce a revision to
one expense after the initial decision.

**Hypothesis:** Addressable facts and recorded source/result relations reduce
repeated copying and make it easier to identify what the revision affects.

**Measure:** Manual copies and context-restatement episodes; time to identify
the changed fact, affected recommendation and relevant draft; incorrect claims
that an old conclusion used new data. Count time spent arranging cards too.

**Falsification:** The canvas requires as much reconstruction as windows plus
chat, its layout overhead erases the savings, or users cannot reliably tell
which revision supports a recommendation. More attractive output alone does
not pass.

### B. Revise a meeting follow-up while the user is writing

Start with a real saved meeting package. The user edits a follow-up draft while
an agent incorporates a corrected owner or due date into a proposed update.
Present the changed action, supporting passage and proposed draft difference
together. Save only the exact intended revision to the supported destination
and retain that receipt.

**Hypothesis:** A named group of changes attached to affected objects is easier
to review than a fresh answer in chat, and preserves the user's writing.

**Measure:** Time to understand the proposed difference; whether the user can
explain its source and saved destination; keystrokes or edits lost; mismatches
between reviewed and saved text. Existing standing authorizations still apply.

**Falsification:** The UI overwrites a manual edit, misidentifies the saved
revision, requires rereading the full transcript to understand the change, or
the user must manually reconcile every affected object despite the proposed
grouping. Local acceptance must not be presented as a verified external save.

### C. Resume unfinished work without reconstructing it

Leave the launch or meeting workspace with one task still running and one
decision waiting. Work in an unrelated workspace, then return after a realistic
interruption. Include an offscreen source revision and a task needing input.
For a second trial, restart the UI while the work continues in the backend.

**Hypothesis:** A compact, source-linked account of what changed and what needs
attention gets the user back to the next useful action faster than scanning
application windows and chat history.

**Measure:** Time to correctly state what completed, what changed, what is
waiting and what to do next; unnecessary navigation; accidental duplicate work.
A useful initial target is correct orientation within 30 seconds, but compare
against the baseline rather than treating that number as evidence.

**Falsification:** The user must reread the conversation anyway, selects stale
material without realizing it, loses the active draft, or a result appears in
whichever workspace happens to be open instead of its originating workspace.
The task must also remain understandable in a compact list/focus presentation;
passing should not depend on remembering precise card coordinates.

## Primary sources

All accessed 24 October 2026. Links are to the authors, product owners or
official source repositories. Product descriptions establish documented
mechanisms, not independently measured usability or interoperability.

- **[P1]** Ink & Switch, [Potluck: Dynamic documents as personal software](https://www.inkandswitch.com/potluck/), October 2022. Mechanisms, examples and programmer-oriented prototype limitations.
- **[P2]** Ink & Switch, [Embark: Dynamic documents for making plans](https://www.inkandswitch.com/embark/), November 2023. In particular: Transclusion; Conceptual model; Documents capture thought process and context; Limitations and future work; Appendix I.
- **[P3]** Ink & Switch, [Upwelling: Combining real-time collaboration with version control for writers](https://www.inkandswitch.com/upwelling/), March 2023. In particular: Layers; Updating a Draft; Automatic merging is necessary but not sufficient; Drafts should float on top of the stack; Future work.
- **[P4]** Allume, [official product site](https://allume.com/). Identifies the Muse rename and describes nested boards and supported material.
- **[P5]** Allume, [CLI](https://allume.com/cli-setup/). Read/write modes, object-oriented commands, workspace IDs and running-app dependency.
- **[P6]** Allume, [MCP Setup](https://allume.com/mcp-setup/). Tool access and current lack of per-board controls.
- **[P7]** Obsidian, [JSON Canvas Specification 1.0](https://jsoncanvas.org/spec/1.0/), 11 March 2024. Node, file/subpath, group and edge schemas.
- **[P8]** Obsidian, [Canvas help source](https://github.com/obsidianmd/obsidian-help/blob/master/en/Plugins/Canvas.md). Official documentation retrieved from its [raw source](https://raw.githubusercontent.com/obsidianmd/obsidian-help/master/en/Plugins/Canvas.md); includes card conversion and connection navigation.
- **[P9]** tldraw, [Agent starter kit](https://tldraw.dev/starter-kits/agent). Visual and structured context, action validation/execution/presentation and mode-scoped capabilities.
- **[P10]** tldraw, [Workflow starter kit](https://tldraw.dev/starter-kits/workflow). Explicit port bindings, node execution and data flow.
- **[P11]** Microsoft, [Fluid Framework documentation](https://fluidframework.com/docs). Shared-state synchronization and UI-framework independence.
- **[P12]** Microsoft, [Fluid handles](https://fluidframework.com/docs/concepts/handles). References to the same collaborative object from several data structures.
- **[P13]** Omarchy, [Navigation manual](https://omarchy.org/manual/navigation/). Direct focus/workspace navigation and persistent per-workspace layout choices.
- **[P14]** niri, [official README](https://github.com/YaLTeR/niri/blob/main/README.md), retrieved from its [raw source](https://raw.githubusercontent.com/YaLTeR/niri/main/README.md). Scrollable tiling without resizing existing windows when new windows open.
- **[P15]** niri, [Workspaces](https://niri-wm.github.io/niri/Workspaces.html). Dynamic positions, named workspaces and monitor movement.
