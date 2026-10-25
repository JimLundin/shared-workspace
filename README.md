# Shared Workspace

An experiment in work that continues across applications and devices. Open the
[website](https://jimlundin.github.io/shared-workspace/), pair the hosts you choose,
and keep a local copy of the workspaces they share with you.

A piece of work is the product's organizing idea: an outcome, its material,
decisions and actual results. The current application implements shared material,
dictation and editing. Cross-application agent execution is the
[next proof](docs/work-continuity-thesis.md), not an implemented browser capability.

## Use the app

The website and installed PWA use the same application. Installation adds a
convenient launcher and an app window. Offline startup, local editing and controlled
updates already work in a browser tab after the first successful load.

On iPhone or iPad, use Safari's Share menu → **Add to Home Screen**, then open the
installed app and join your workspace there. Its storage can be separate from
Safari's; keep the existing copy until the installed host has received your work.
See [installation capabilities and limits](docs/pwa-installation.md).

The interface prioritizes voice, with editable transcripts and optional typing.
Workspace notes and code documents retain revisions and conflicts. CodeMirror is
an actual embedded editor; Office and VS Code links open externally under the
[application surface contract](docs/shared-application-surfaces.md).

## Architecture

- TypeScript, HTML and CSS provide the interface shared by browser and optional
  native hosts.
- Actual **Turso Database** in WebAssembly stores browser records locally in OPFS.
- **Iroh** in WebAssembly exchanges accepted operations between authorized peers.
  Browser transport uses relays; they do not own the workspace or queue offline jobs.
- Each host retains its identity and data. No particular device is a permanent
  authority; disconnected peers reconcile when they can communicate again.
- Static HTTPS assets and a service worker deliver the browser application.
  Python, Node and a separate browser engine are not installed application runtimes.

The [browser host](browser/README.md), [interface](browser/app/README.md),
[architecture](docs/browser-host-direction.md) and [native experiment](native/README.md)
describe the implementation. Native Rust/Tauri hosts are optional experiments;
website participation does not require an executable or an App Store application.
Installing a PWA does not grant arbitrary desktop access or a continuously running
phone agent.

## Separate projects

[Toolkit](https://github.com/JimLundin/toolkit) owns the Windows keyboard layout,
system dictation, desktop assistant, local automation and their Python/Qt bundle.
It has its own source, checks and releases. This repository owns Shared Workspace's
browser application and optional native hosts. Their current execution systems are
not connected. A future integration needs an explicit capability and action contract.

The [project boundary](docs/project-boundaries.md) records the split and the
[product thesis](docs/work-continuity-thesis.md) explains the next useful experiment.
The earlier combined repository remains available in Git history.

## Development and evidence

Start with [browser build instructions](browser/app/README.md). The
[browser workflow](.github/workflows/browser.yml) defines the pinned build tools
and checks. Tests cover real Turso storage, Iroh exchange, scope, conflicts,
provider capture, UI behavior and update recovery; targeted mutations check that
behavioral assertions detect regressions.

The user has confirmed iPhone joining and browser speech. New layout, installed-app
lifecycle and authenticated provider dictation still need physical-device checks.
Desktop WebKit tests do not establish those results. The
[Turso memory audit](browser/storage-probe/memory-audit-20261025/README.md) separates
logical WASM memory from measured process memory; phone memory remains unmeasured.
