# Shared Workspace website

The browser application mounts the existing responsive canvas UI against the
real `BrowserHost`. Opening the website creates/opens this browser's durable
host. There are no sample workspaces or fallback stores. Desktop shows a canvas;
phone widths show a work list with a focused note. View arrangement stays local.

Create a workspace and note, then choose **Share this workspace**. Its QR and
**Copy invitation link** open the website with a fragment invitation on another
device. Startup clears the fragment and prefills Devices; only **Join selected
workspaces** calls the host. The inviting peer must be reachable. The invitation
grants its selected scope, not every workspace.

Speech is optional and starts explicitly. The browser's provider may process
audio online. The transcript remains editable and requires **Run command**.
Typing/paste and keyboard dictation remain available. Supported commands are
bounded workspace/note/navigation actions; general agent execution is not wired.
See [engine requests](BACKEND-REQUESTS.md) for the next real workflow slice.

## Build and check

From the repository root, with Node 24, Rust and wasm-bindgen installed:

```sh
bash browser/host-wasm/build.sh
npm --prefix browser/host ci
npm --prefix native/ui ci
npm --prefix browser/app ci
npm --prefix browser/app run check
npm --prefix browser/app test
npm --prefix browser/app run build
npm --prefix browser/app exec -- playwright install chromium
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
An update waits; users can finish their edits before choosing to reload. The
immediately previous asset cache remains available for older open clients.

## Verified behavior and limits

`test:built` serves the built application under `/shared-workspace/` on an
ephemeral loopback port **without isolation headers**. It uses separate real
persistent Chromium profiles, actual Turso OPFS and Iroh peers. It covers empty
startup, UI saves, explicit fragment joining, two-way edits, addressed phone
input, preservation/reconciliation of a stale editor, offline process restart
and editing, reconnect delivery, asset-only caches, and absence of native calls.
A second test changes the served build and checks waiting updates, preservation
of unsaved input, explicit reload and old-client asset recovery. It also withholds
one real host close acknowledgement and verifies the explicit reload/recovery
action reopens committed work.

Results: [production UI](evidence/production-ui.json), [updates](evidence/update.json).
Seven unit tests cover invitation handling and browser speech lifecycle, including
cancelled-session fencing and captured addresses. Native UI regressions remain
in `native/ui`. Screenshots and redacted diagnostics are local ignored artifacts.
The icon PNGs are checked in; `node scripts/icons.mjs` regenerates them from SVG.

These checks establish Chromium behavior, including phone-sized layouts. They
do not establish physical iPhone safe areas, software keyboard, microphone
recognition, Safari compatibility or Home Screen identity continuity. Browser
storage persistence is conditional, peer activity resumes in the foreground,
and no continuous phone background execution is promised. Installation is
optional; supported browsers offer an install action. Safari users use Share →
Add to Home Screen. [Research and primary sources](RESEARCH.md).
