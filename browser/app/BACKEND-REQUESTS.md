# Engine requests from the browser UI experiments

The current UI uses real `BrowserHost.snapshot/command/invite/join/sync`, durable
operation receipts, retained resource revisions and foreground lifecycle. This
supports shared notes/workspaces. General agent execution and native application
surfaces are not implemented by the browser UI.

The next slice should let a person capture a task on the phone, inspect its
inputs/results on the desktop, and answer a question from either device. The
following requests are for the runtime owner to review and implement beneath
the existing shared Host contract. They are proposed additions, not promises
that existing peers already support them.

| User flow | Required engine behavior | UI acceptance criterion |
| --- | --- | --- |
| “Prepare this from these notes” | A durable task identity and immutable captured address: workspace, selected resource IDs, observed revision IDs, requested outcome, initiating peer. Validate all addresses and access at intake. Return a durable acceptance receipt distinct from execution completion. | Navigating elsewhere or receiving a late voice transcript cannot retarget the task. A missing receipt permits retry of the same task command identity. |
| Phone hands work to a capable desktop | Advertise available capabilities with observation times; record the chosen execution owner and an attempt/lease identity. Specify claim, expiry, recovery and fencing semantics before enabling automatic reassignment. | Show “waiting for a device”, “accepted by desktop”, “working” and “connection unknown” from observed facts. Offline status never looks like task completion. |
| Follow work across devices | Persist task state transitions and their causality. Model queued, running, waiting for input, proposed result, completed, failed and cancelled as payload-bearing variants, including which attempt emitted each transition. | Reopening either device reconstructs the same workflow. Local focus/scroll do not become shared workflow state. |
| Answer a question or approve a change on the phone | A durable question/approval ID with captured target revisions, bounded allowed response, expiry/supersession and command identity. Revalidate when consumed; explicitly reject an obsolete response. | The phone can explain the exact proposed effect. A delayed approval cannot authorize a newer or different proposal. |
| Agent result appears alongside its sources | Commit results as addressable resources, with source revision references and task/attempt provenance. Distinguish a proposed revision from a revision applied to the original document. | Desktop can compare sources and result; phone opens the same result in focus. Stale human edits remain available for reconciliation. |
| Open or control an application window | Native host owns the capability and external effects. Report supported operations, target identity, expected-state checks and observed outcome. A browser card holds a reference plus truthful availability. | “Open on desktop” is an acknowledged handoff. Unsupported embedding or remote input is never shown as a working app surface. |
| Cancel or retry while disconnected | Durable intent, idempotent local acceptance, observed acknowledgement by the execution owner. Define cancellation boundaries for effects already in flight and uncertain outcomes. | “Cancellation requested” stays distinct from “stopped”. Retrying cannot silently run a second external effect. |

No new network/protocol implementation is made here. These requirements need
joint agreement with the domain, peer-sync and runtime owners, including
protocol versioning and compatibility between browser and native hosts.

Recommended next experiment: one real bounded task that reads selected note
revisions and produces a new draft resource. Keep external application writes
out of that first acceptance case. The phone captures and reviews; desktop
shows input/result side by side. Both views address the same task and resources.

Current code seams: [`Host`](../../native/ui/src/host.ts),
[`WorkspaceSession`](../../native/ui/src/session.ts),
[`captured commands`](../../native/ui/src/commands.ts),
[`BrowserHost`](../host/src/host.ts). Existing direction:
[`canvas work design`](../../docs/canvas-workspace-design.md) and
[`host architecture`](../../docs/native-architecture.md).
