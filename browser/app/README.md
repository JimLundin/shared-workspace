# Shared Workspace website

The browser application mounts the existing responsive canvas UI against the
real `BrowserHost`. Opening the website creates/opens this browser's durable
host. There are no sample workspaces or fallback stores. Desktop has leading
work/material navigation, an attached page toolbar and optional inspector;
touch devices keep navigation separate from lower page/voice actions. View
arrangement and preferred hand stay local.

Create a workspace and note, then choose **Share this workspace**. Select the
workspaces to include: your phone can share several, while a guest can receive
just one. The QR and **Copy invitation link** open the website on the other
device; **Share invitation** uses the device's share sheet where supported. The
full invitation stays collapsed under a copy/paste disclosure. The compact token
is supplied by the host and stays opaque to the UI.

Startup clears the invitation fragment and leads with explicit **Join selected
workspaces**. Host events report checking the invitation, connecting this device,
contacting the inviting device, and saving the workspace. Progress never means
completion: the host must return the committed snapshot. Failure guidance uses
the last observed stage and retains the invitation for an explicit retry. Native
adapters without progress events show a generic waiting state.

Connecting devices grants only the workspaces selected in that invitation.
It does not automatically share other or newly created workspaces. The inviting
peer must be reachable.

Voice is the primary input and starts explicitly. Workspace OpenAI dictation
uploads audio when recording stops; the transcript remains editable before use.
Optional typing and device speech remain available. **+ → Meeting work** retains
a requested outcome and pinned transcripts, then explicitly prepares cited notes,
actions and unsent follow-up text. Owner/deadline corrections support the same
captured dictation flow. See [the implemented workflow](../../docs/work-continuity-implementation.md)
for recovery, bounds and what is shared. General application execution still needs
the contracts in [engine requests](BACKEND-REQUESTS.md).

## Build and check

From the repository root, with Node 24, Rust, wasm-bindgen and OpenSSL installed:

```sh
bash browser/host-wasm/build.sh
npm --prefix browser/host ci
npm --prefix native/ui ci
npm --prefix browser/app ci
npm --prefix browser/app run check
npm --prefix browser/app test
npm --prefix browser/app run build
npm --prefix browser/app exec -- playwright install chromium webkit
npm --prefix browser/app run test:built
```

WASM build prerequisites and pinned bindgen version are in the repository's
browser CI workflow. Shared source type checking currently needs native UI's
dependencies installed, but the production browser bundle has no native bridge,
speech plugin or scanner calls.

Output is `browser/app/dist`. Production base defaults to `/shared-workspace/`;
set `WORKSPACE_BASE` when building for another path. Serve the complete output
over HTTPS. Hosting/publication is owned by the root deployment workflow.
Development `npm --prefix browser/app run dev` binds only to loopback and
supplies COOP/COEP headers; it does not test the offline worker.

The build generates a single `sw.js` for both isolation response headers and
versioned asset caching. On a host without custom headers, first opening may
reload once to obtain cross-origin isolation. Host startup occurs afterward.
The worker precaches JS, CSS, WASM, icons and the application page. Workspace
records remain in OPFS; invitations and workspace data never enter app caches.
An update waits; users can finish their edits before choosing to reload. The app
checks for updates on startup, when returning to the foreground or coming online
(at most once per minute), and every five minutes while visible and online. A
top-level **Update available** action appears only when a replacement worker is
waiting. Discovery does not interrupt recording, transcription, unfinished input
or an unconfirmed save. The immediately previous asset cache remains available
for older open clients.

## Verified behavior and limits

`test:built` serves the built application under `/shared-workspace/` on an
ephemeral loopback port **without isolation headers**. It uses separate real
persistent Chromium profiles, actual Turso OPFS and Iroh peers. It covers empty
startup, UI saves, explicit fragment joining, observed Host progress through the
real worker into the UI, two-way edits, addressed phone
input, preservation/reconciliation of a stale editor, offline process restart
and editing, reconnect delivery, asset-only caches, and absence of native calls.
A second test changes the served build and checks waiting updates, preservation
of unsaved input, explicit reload and old-client asset recovery. It also withholds
one real host close acknowledgement and verifies the explicit reload/recovery
action reopens committed work.

A third gate runs the served meeting workflow once each in Chromium and WebKit:
import three transcripts, create work, explicitly prepare cited material, open
a pinned citation, correct an owner/deadline, edit the unsent follow-up, then
reopen the persistent profile in a new offline browser process. It uses the real
renderer, worker, Turso journal and Work commands. A local TLS provider fixture
accepts only a synthetic credential and returns a controlled Responses payload;
it forwards no provider traffic and rejects other proxy destinations. This
transport fixture also captures service-worker/worker requests that WebKit can
send outside Playwright route interception. Its temporary certificate exception
is confined to isolated test profiles, which are removed with the certificate
and closed servers. Reports retain provider acceptance/rejection counts and
SHA-256 hashes of every emitted asset before and after the checks.

Run this gate separately with `node browser/app/tests/meeting.mjs`. Optional
`--dist`, `--evidence` and `--engine chromium|webkit|both` select the artifact,
report directory and engines; the default and `test:built` run both engines.

Results: [production UI](evidence/production-ui.json), [updates](evidence/update.json),
[meeting workflow](evidence/meeting.json).
Unit tests cover invitation handling and browser speech lifecycle, including
cancelled-session fencing and captured addresses. Native UI regressions remain
in `native/ui`. Screenshots and redacted diagnostics are local ignored artifacts.
The icon PNGs are checked in; `node scripts/icons.mjs` regenerates them from SVG.

These checks establish Chromium behavior and the meeting workflow in WebKit,
including phone-sized layouts. The controlled meeting response does not establish
authenticated provider behavior or live model quality. They do not establish
physical iPhone safe areas, software keyboard, microphone recognition, complete
Safari compatibility or Home Screen identity continuity. Browser
storage persistence is conditional, peer activity resumes in the foreground,
and no continuous phone background execution is promised. Installation is
optional; supported browsers offer an install action. Safari users use Share →
Add to Home Screen. [Research and primary sources](RESEARCH.md).
