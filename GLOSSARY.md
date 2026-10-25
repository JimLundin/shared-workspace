# Shared work

Shared Workspace organizes outcomes and their material across applications,
conversations and participating hosts.

## Language

**Work**:
An intended outcome together with its material, constraints, decisions, progress
and results. It retains its identity across conversations, applications and hosts.
_Avoid_: Card, chat turn, application

**Run**:
One period of execution of work. Work can have several runs as it waits,
resumes, or recovers from an interruption.
_Avoid_: Task, session

**Conversation**:
The context shared by a sequence of requests and replies. Starting a new
conversation does not erase its work.
_Avoid_: Task history

**Action attempt**:
A recorded intention to change something outside the work itself. An attempt
does not establish that the intended effect occurred.
_Avoid_: Completed action

**Observed outcome**:
Evidence about what an action actually did. An interrupted attempt can retain
an uncertain outcome until the affected state is inspected.
_Avoid_: Assumed success

**Checkpoint**:
A bounded account of progress that helps a later run continue from
known facts.
_Avoid_: Conversation dump

**Workspace**:
A named working context containing resources and their arrangement. Showing a
different workspace does not change the identity of its resources or work.
_Avoid_: Conversation, source checkout

**Host**:
A participating instance that keeps its own identity and a local copy of the
work it is permitted to access. One device can run more than one host.
_Avoid_: Permanent coordinator, master device

**Device connection**:
A recognized relationship between hosts through which they can exchange
authorized work. It does not grant access to every workspace.
_Avoid_: Joining a device to exactly one workspace

**Workspace access**:
Authorization to participate in a specified workspace. A host can have access
to several workspaces without making their contents visible to each other.
_Avoid_: Device ownership, access to all work

**Workspace invitation**:
An offer to grant access to explicitly selected workspaces. Accepting it does
not authorize access to unrelated workspaces.
_Avoid_: Universal device access

**Resource**:
A document, application, note, source, or result that can be worked with and
shown in more than one workspace.
_Avoid_: Placement, task

**Placement**:
The presentation of a resource within one workspace. Removing that presentation
does not delete the resource or cancel its work.
_Avoid_: Resource, application instance

**Standing instruction**:
A saved instruction that authorizes work when a specified trigger occurs.
Enabling it determines when it begins responding to those occurrences.
_Avoid_: Background prompt, permanent task

**Trigger occurrence**:
One identifiable event or scheduled time to which a standing instruction
responds. Repeated delivery of an occurrence refers to the same work.
_Avoid_: New task

**Accepted handoff**:
A standing instruction's commitment to carry out a trigger occurrence. Later
editing or disabling the instruction does not revoke work already accepted;
that work can be cancelled separately.
_Avoid_: Unobserved event, pending trigger

**Cancellation**:
The user's decision to end work. An effect already underway can still acquire
an observed outcome without reviving the cancelled work.
_Avoid_: Shutdown, interruption

**Interruption**:
An execution break that leaves work unfinished and recoverable. It does not
authorize blindly repeating an action with an uncertain outcome.
_Avoid_: Cancellation, failure
