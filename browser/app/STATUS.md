# Shared Workspace UI — frozen handoff

Source and dist are frozen. The last root-requested autofocus correction is included:
16 fixture journeys pass, including no transient textarea focus on microphone start
and explicit textarea focus for Windows dictation. Both final builds pass. No further
source edits or builds will be made by this lane. Independent validation can snapshot
this dist now.

- Browser: `index-CaWAdKi5.js`, `index-kMT_8nFt.css`
- Native: `index-mDUw4smW.js`, `index-CGBTeTAw.css`
- `native/ui/src/app.ts` SHA-256: `ecfd5317cce22f5a97bd0c337ca54edaed73e4051b871b6be7d2c8e255d02d7b`
- `browser/app/dist/sw.js` SHA-256: `59c2d86b45d5815b95d2320d00afe4e26801e2e806d6bb284b0aebcfda5b0000`

Root owns integration, commit, publication and Windows source/ZIP deployment.
This lane has not committed or deployed. Backend protocols, storage and transport
were not changed by this lane.

## Product and journey contract

| Before | After | Why |
| --- | --- | --- |
| Persistent phone workspace navigation | Current-workspace control opens a picker | Leaves the screen for the work |
| Large voice/status panel before content | Compact persistent capture dock | Voice stays reachable without crowding material |
| Canvas / Focus / Overview choices | Open title or Open note; Edit directly | Predictable reading and editing |
| Device dashboard after joining | One received space opens; multiple receive a scoped picker | Connecting leads into work |
| Dotted dashboard and nested boxes | Quiet neutral surface with readable material | Clear hierarchy and stronger contrast |

- Ready control: `Current workspace: <title>`. It opens `Switch workspace`; exact
  workspace-title buttons select and close it. No persistent workspace rail.
- Empty start: `Workspace name`, `Create workspace`, `Join existing work`.
- `Add a note`, `Open <title>` (visible label Open note), `Edit`, `Title`, `Content`,
  `Save note`. Opening resets reader scroll and focuses its heading; polls preserve
  position. Editing references immediately opens the original resource's editor.
- `Speak a command` / `Type a command` open `Capture`. `Command text` or
  `Spoken transcript · editable`, then `Run command →`. Dismissal retains the draft's
  original address; `Return to <workspace>` restores context. Exact retry clears
  successfully committed capture without duplicating it.
- `Share this workspace` is available on phone and desktop. Its dialog starts with
  the current space checked; additional grants are explicit. QR/share/copy are first;
  raw invitations remain disclosed only on request.
- `Connections: <status>` or Connections in the picker opens occasional connection
  controls. `Join existing work` opens `Join workspaces`; only explicit Join runs.
  One newly received space opens after the committed snapshot. Multiple new spaces
  offer only those spaces. An ambiguous repeated grant asks which space to continue.
- Native top-layer dialogs support Close, Escape and Back, retain focused controls
  across observations and restore current trigger focus. Background work stays inert.
- `App options` contains install/storage/update actions. Updates preserve unfinished
  note/capture/workspace-name input and pending saves. Back cannot dismiss the sole
  recovery control after the app is disposed for update.
- Shared Workspace is the only product branding. Light startup/status-bar colors,
  safe areas, 16px-or-larger inputs, reduced motion and 44px phone status target.

## Validation and review

- 21 shared UI unit tests; 7 browser unit tests; 3 targeted mutation checks pass.
- 16 fixture journeys pass, including cancellation/late voice, uncertainty, conflicts,
  exact scope, multiple joins, modal focus/poll/Back, scrolled reading, references,
  long titles, larger text, reduced viewports and Windows dictation focus.
- Strict builds and `git diff --check` pass.
- 12 real-host production assertions and 6 real update/recovery assertions pass
  against the final frozen build. Production evidence records the matching SW hash.
- Root's independent review confirmed the structural redesign and repaired scroll,
  reference editing, phone Share and honest offline status.
- Root reports independent immutable Chromium-to-WebKit smoke passed: 8 outcome
  groups covering committed joining, bidirectional edits, dialogs/poll/Back, sharing
  scope, addressed capture, mobile layout and reload. Evidence:
  `/tmp/overhaul-local-cw-20261025-3/report.json`. Source/assets match its snapshot.

Synthetic screenshots (UI evidence, not live enrollment/synchronization proof):
`artifacts/overhaul-desktop-fixture.png`, `artifacts/overhaul-phone-fixture.png`,
`artifacts/overhaul-picker-fixture.png`.

Production evidence: `evidence/production-ui.json` and `evidence/update.json`.
Real-host tests use separate persistent Chromium profiles, actual storage and peer
exchange. Physical iPhone keyboard, microphone, touch and installed-app behavior
still need the published HTTPS check; emulation does not establish those results.

## Engine handoff

[ENGINE-REQUESTS.md](ENGINE-REQUESTS.md) specifies exact granted-scope join receipts,
host-retained addressed drafts, typed resource capabilities for future app/document/
task surfaces, correlated remote execution/results, and separate local durability,
peer delivery and workflow progress. These are backend requests, not implemented
capabilities. Existing bounded commands stay honest; no fake general assistant.
