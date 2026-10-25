---
status: proposed
---

# One agent workspace, with replaceable runtime adapters

The new distributed Windows/iPhone host uses the shared native core and shell
selected in [ADR 0003](0003-distributed-native-workspaces.md). The work ownership,
application isolation and migration requirements below remain relevant.

The recommended product is an agent-managed workspace on Windows: a continuous
conversation and a canvas showing the same resources, tasks and observed
outcomes. Its target implementation is TypeScript compiled to JavaScript in
shared Evergreen WebView2, with Pi Durable as the candidate agent runtime and a
small compiled Win32/WebView2 host for desktop capabilities and supervision.
Build on the existing direct native-host approach; introducing another web
server or desktop framework is unnecessary for this migration. Native input
and audio keep their isolation. Removing bundled Python and Qt is an explicit
completion condition, subject to the compatibility gates below.

First establish a shared `WorkspaceControl` interface through the existing
Python owner. The continuous conversation and canvas issue validated commands
and observe the same immutable projections. Then move that interface's
implementation into the trusted TypeScript runtime. The existing Qt/WebView2
transport seam remains an adapter. This order delivers workspace control before
the language migration and prevents two competing agent runtimes or journals
from owning production work.

The canvas is the human's visible working surface. The agent operates the
underlying workspace and application interfaces: `observe(query)` and
`command(command)` with explicit workspace/resource identities, appropriate
expected revisions, and correlated receipts. It does not manipulate Toolkit's
own HTML to implement workspace changes. Application adapters retain exact
live-document targets; native effects retain cancellation, authorization,
outcome verification and uncertain-effect recovery. Untrusted app pages have
no privileged workspace or desktop bridge.

Conversation association and execution scope are separate facts. An analysis
of captured evidence can appear in the continuous conversation while retaining
its existing restricted tools and immutable inputs. Live workspace operations
use an explicitly authorized operation scope. Switching the visible workspace
must not retarget a delayed command. A queued save is not a persisted receipt;
a saved card is not proof that its application finished loading or saved an edit.

The first visible acceptance scenario is: from the existing conversation,
"Summarize this page into a note beside it," followed by "Shorten that note."
The result and its progress appear in the canvas and conversation under the
same task/resource identities. Test switching workspaces mid-run, concurrent
manual edits, closing/reopening the canvas, cancellation, duplicate delivery,
and restart recovery. Preserve captured-evidence behavior while adding this
operator path. Use local app fixtures for deterministic interface checks and a
separate real workflow to establish user-visible value.

Carry the existing functional design into both implementations: immutable
payload variants, exhaustive handling, validated external data, and pure
transitions with supplied IDs, clocks and observations. In TypeScript, use
discriminated unions and readonly nested values; parse `unknown` inputs rather
than trusting erased annotations. Test through the same public interface used
by UI and agent. Targeted mutations must detect wrong-workspace routing, stale
revisions/documents, expired runs, falsely confirmed saves and replay after a
lost effect acknowledgement. Keep the original failures and require the intended
behavioral assertion, not a timeout or setup error, to count as a detection.

Pi's proven Windows path currently uses a trusted page with a native host.
Start the target agent owner from that path, independently of canvas visibility;
a Windows worker relay remains a later integration until tested. Before retiring
the current implementation, demonstrate the real model/provider and native
credential path, supported compiled-host deployment on this PC, background
lifetime, native view/input/file-dialog behavior, journal ownership transfer,
keyboard/audio parity, and comparable whole-process resource measurements.
The [runtime review](../runtime-direction-review.md) records evidence and limits;
the [interactive diagram](../runtime-footprint.html) separates current, next and
target states. The task recovery policy in [ADR 0001](0001-durable-work.md)
continues to apply.
