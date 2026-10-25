# An operating environment for work

25 October 2026. Product thesis and proposed next proof, following the browser
workspace experiment. This describes intended behavior, not implemented execution
capabilities.

The promise worth testing is: **give it an outcome, leave, correct it from another
device, and return to the actual work completed—or one precise decision waiting.**
The benefit is less coordination, repeated explanation and manual reconciliation.
Peer synchronization, an agent runtime and embedded applications are supporting
mechanisms. Combining them does not itself establish novelty or useful autonomy.

## What exists

The browser product currently provides replicated workspace material, pairing,
dictation, bounded commands, shared revisions and an embedded code editor. Office
and VS Code links open externally. Sharing a URL neither grants access to the
vendor account nor gives an agent control of the application.

Separately, [Toolkit](https://github.com/JimLundin/toolkit) has durable Windows tasks, meeting preparation,
source-linked outputs and recovery for unsent mail. These are valuable foundations,
but they are not yet one browser-to-desktop execution path. The
[UI's engine requests](../browser/app/ENGINE-REQUESTS.md) identify missing retained
drafts, executable capabilities and correlated remote execution. We should not
describe the browser workspace as an implemented distributed agent executor.

## The organizing idea

The central product object is a durable piece of work: its requested outcome,
source evidence, constraints, decisions, intended and observed artifacts, unresolved
questions and recorded effect results. Central here describes the model, not a
permanent server or privileged device.

| Concept | Responsibility |
| --- | --- |
| Work | Preserve the outcome, context, decisions and verified progress across attempts |
| Workspace | Bound shared context, membership and access; organize related work |
| Application | Supply supported operations and editable artifacts |
| Device | Offer currently available, authorized capabilities |
| Agent | Plan and execute attempts against the work's explicit state |
| Interface | Let people inspect and change that same state, including by voice |

A conversation is one way to change work. It must not be the sole place its meaning
survives. Human edits should directly change the addressed instruction or artifact;
they should not require another prompt to copy those changes into an agent's world.
An edited status label cannot substitute for evidence that an external action
happened.

This implies a work-centered default screen: the current outcome, its useful
material, and the decision or action currently available. Workspaces are an
on-demand scope switch. Connections belong in settings unless access needs repair.
Phone and desktop can offer different arrangements of the same work; synchronized
pixel positions are not a useful requirement. Voice is the primary entry method,
with compact accessible controls and visible corrections before consequential use.

## The strongest objection

Much of this is already familiar from workflow engines and project management
software. Durable records, retries and replaceable executors do not resolve that
objection. Even a successful fixed meeting recipe only proves that particular
workflow.

The broader hypothesis is that a user can change an ongoing job in ordinary
language and through direct edits, and the system can reconcile those changes
with real applications without forcing the user to rebuild context or supervise
the integration. Start with one measurable proof. Only then test whether the same
contracts support a different job without another bespoke orchestration stack.

For the present product, **shared workspace** is an honest working name.
**Operating environment for work** describes the direction. **Agentic OS** remains
an ambition; this software does not replace the device's kernel or platform.

## Smallest useful proof

Reuse the existing completed-meeting and draft implementation rather than introduce
another runtime. Use the selected meeting segments once, with access limited to
that work and its intended outputs.

1. From the phone, request cited meeting notes, assigned actions and an actual
   unsent Outlook follow-up draft. Retain the addressed request durably.
2. An eligible, authorized host executes it. If none is available, retain the
   request and say what capability is missing. Local acceptance is not execution.
3. On the phone, correct an owner or deadline and shorten the follow-up. Disconnect
   before delivery; retain the correction with its real delivery state. Reconnect
   while a capable peer is available. The correction must reach the same work.
4. Interrupt execution after Outlook creates the draft but before confirmation
   returns. Recovery inspects the existing draft, avoids duplication and preserves
   intervening human edits. A conflict requiring a choice is an acceptable result.
5. Return on either device. See the accepted correction and any remaining decision
   without reconstructing the prompt. Open the actual artifacts where supported;
   otherwise show a verified representation with source identity, observation time
   and continuation on a capable host. Do not imply native Outlook runs on a phone.

Pass means no copied context, one verified mail draft, preserved human edits and
truthful waiting/unknown states. Fail means manually carrying the correction,
hunting for outputs, reconciling duplicate drafts or supervising blind retries.
An `.eml` fallback can demonstrate artifact recovery but does not prove Outlook
integration. Record user interventions and time spent coordinating alongside
technical receipts; completion latency alone misses the product claim.

The smallest implementation sequence is retained addressed input, a capability
and action contract, a bounded adapter to the existing meeting executor, and a
shared result/decision view. Test lost acknowledgments and user corrections across
that boundary before adding generic autonomous planning or more application cards.

## Distribution and delivery constraints

Equal peers may accept and reconcile workspace history without a permanent leader.
That does not make simultaneous external actions safe. Losing contact with an
executor does not prove it stopped. Execution rights must prevent conflicting
attempts, but acquiring those rights cannot resolve a previously uncertain effect.
Retry also requires an external idempotency mechanism or fresh reconciliation of
that effect. Preserve uncertainty when neither is available. Do not derive safe
takeover merely from a timeout.

Website-first remains the default for participation, browser-supported tools and
local material. Desktop operations require a permitted native capability host or
another authorized execution service. No particular device must be permanent, but
some capable host must be awake for execution to progress. Browser phone suspension
and peers that never overlap online remain real limits; an Iroh relay is not an
offline job queue. See [browser delivery](browser-host-direction.md) for the current
Turso, Iroh and platform constraints.

The next investment should establish this continuity proof. Another language
migration, canvas expansion or branding change will not answer the product question.
