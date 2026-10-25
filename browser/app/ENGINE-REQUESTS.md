# Engine requests from the current-work UI experiment

25 October 2026. These requests define follow-up contracts for the engine owner.
They are not capabilities claimed by the current UI or changes to its Host protocol.

The implemented surface uses the real host for workspaces, notes, references,
version reconciliation and scoped peer joining. Native and browser clients share
presentation. The phone displays the same material with one reading/editing context;
the desktop gives related material more room. Voice and typing use the same bounded
command planner and captured destination. App/document/task surfaces will belong in
this working context when their real adapters are available.

## 1. Identify the result of joining

Today `Host.join(ticket)` returns a complete Snapshot. Comparing workspace IDs before
and after identifies newly received spaces. It cannot identify which already-present
spaces a repeated grant concerns.

Request: return a committed join receipt with its snapshot and **exact workspace IDs
covered by this invitation**, correlated to the join attempt. Distinguish the receipt
from progress events. Do not grant every space on the inviting device. Device
membership and workspace access remain separate. A single returned space opens
immediately; multiple spaces produce a picker limited to those spaces.

Current fallback: open one newly received space; choose among multiple newly received
spaces; if no new IDs identify scope, explain that the connection is confirmed and
ask which existing space to continue. No destination is guessed.

Acceptance: join Work+Private, join only Work, retry a committed-but-unacknowledged
join, rejoin an existing Work, and add a second grant without exposing unrelated
spaces. `saving_workspace` must never be treated as the committed receipt.

## 2. Retain addressed drafts through suspension and restart

The UI keeps editor drafts and capture in memory, including original workspace,
resource and observed revision heads. Navigating does not retarget them; cancelling
speech fences late results. Browser update is blocked while input is unfinished.
Tab termination can still lose unsubmitted input.

Request: host-owned draft operations with immutable captured address, draft identity,
text, source and observed heads. A save receipt identifies the committed operation
and the draft it consumed. Retrying a draft submission must recover the same operation,
not create another resource. Draft retention/replication scope must be explicit:
private local input must not silently become shared work.

Acceptance: type on phone, suspend/reopen, navigate before submission, fail a save
after commit, retry, receive a conflicting revision. Preserve input and destination
through each transition. Let users explicitly discard a draft.

## 3. Describe executable resource capabilities

Request: typed resource variants for documents, app surfaces and tasks, with a
versioned representation and explicit capabilities (`read`, `edit`, `open`, and
supported actions). An unsupported resource needs a readable fallback and a reason
an action cannot run here. References preserve source access and resource identity.
Do not send raw native-window handles as a portable representation.

The phone should be able to read a result or request an action without pretending it
hosts a Windows application. Desktop may offer the live application surface; phone
can show its meaningful material and continuation controls. Distinguish a live surface,
a last-observed preview and a durable artifact in the contract and UI.

Acceptance: the same referenced item on desktop/phone; unavailable source; stale
preview; editing through a reference; capability disappearing mid-action. Shared
content must not contain local window positions or focus state.

## 4. Correlate an action through remote execution and result

Request: submit an addressed action with a stable operation identity and a typed
state stream: accepted locally, awaiting a capable host, executing, awaiting user
input, completed with durable result references, failed or outcome uncertain.
Expose observed location and progress only when the engine has evidence. A retry
must preserve action identity, and stale events must not overwrite newer state.

A phone handoff should remain attached to the work that initiated it. Device choice
is an exception when multiple eligible hosts or a missing capability require it;
the normal user journey is to continue work. Cancellation must distinguish requested
from acknowledged. Approval requests need the actual proposed effect and exact scope.

Acceptance: desktop unavailable, phone backgrounded, interrupted result delivery,
concurrent command from a second device, and retry after unknown outcome. Never turn
local acceptance into a claim that the desktop executed the action.

## 5. Report status the interface can honestly summarize

The current UI says Saved here after a local receipt, Offline when observed, and
Needs attention for rejected/unknown saves or unreachable peers. Details remain
under Connections. A successful peer exchange is not proof every device has the
latest version.

Request: separate local durability, observed delivery to each authorized peer, and
workflow execution state. Surface actionable failure categories and recovery actions
without showing raw transport diagnostics by default. Provide an event subscription
or observation version to avoid replacing the view when nothing relevant changed.

Acceptance: an offline local save, an unreachable peer, a completed exchange with an
older third device, and an execution result that has not reached the phone yet.

## What the UI experiments established

- An on-demand picker leaves phone space for actual material. Selection closes it.
- A compact capture dock keeps voice reachable without pushing work below the fold.
- Opening material must reset reading position; periodic observations must preserve it.
- Editing a reference must immediately open the original resource’s editor.
- Modal lifetime and focus must survive observation refresh; top-layer dialogs cover
  the viewport independently of canvas scroll.
- Updates are an occasional explicit operation, and must retain a recovery path after
  the host is closed. Browser Back cannot dismiss that sole recovery control.

The executable checks live in `native/ui/tests/interface.spec.ts`, browser production
acceptance and update tests. Fixture tests are UI evidence; production acceptance
uses independent persistent browser profiles and real storage/peer exchange.
Physical iPhone keyboard, microphone and installed-app behavior need a published
HTTPS check. Viewport emulation does not establish those results.
