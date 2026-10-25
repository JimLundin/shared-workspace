# Distributed workspace hosts

This folder contains the first native workspace implementation: a separate
Windows/iPhone application using a shared Rust core and a TypeScript interface.
The previous HTML study remains a product-model reference, not this app's data.

The core uses **Turso Database**, the Rust SQLite-compatible engine, not libSQL.
Iroh connects equal device hosts. Each device retains its own durable permitted
workspaces and stable identity; there is no permanent master computer.
Tauri provides the system WebView shell (WebView2 on Windows, WKWebView on iOS).
Node/Vite are build tools, not an installed Node server or browser engine.

Initial functionality: named workspaces, notes/resources, shared references,
causal revisions and explicit conflict resolution, selected-workspace pairing,
reconnect/catch-up and device-local presentation. The native host owns all data
and network effects. Voice and typed input address the same operations.

The existing Python assistant retains its own keyboard/audio/task owners during
this first native deployment. This app does not introduce a competing executor
for those tasks or claim that Pi/provider integration has been completed.

The user reported a policy restriction on local phone HTTP preview sharing.
This implementation does not use that delivery route. iPhone native installation
requires Apple's signing/provisioning path; unsigned CI artifacts cannot be
installed on a physical phone. Build and install evidence will distinguish
Linux/Windows process tests, simulator runs and actual device installation.

Implementation interface: [PROTOCOL.md](PROTOCOL.md). Public command/snapshot
and peer interfaces are the behavioral test seams. Targeted mutations must fail
the intended behavior assertion; compiler/setup failures do not count.

Build instructions are in [app/README.md](app/README.md). The
[architecture](../docs/native-architecture.md) describes the installed bundle;
the [platform record](../docs/native-platforms.md) distinguishes available build
and signing routes, and the [validation record](../docs/native-validation.md)
tracks actual results.

Once installed, create a workspace and save a note. In Devices, select the
workspaces to share and create an invitation. The other installed host scans or
pastes the ticket and explicitly joins. Each keeps its own local database.
Edit while a peer is unavailable, then reconnect; competing edits appear for
explicit review. The invitation is temporary and grants access to its selected
workspaces, so share it only with the device you intend to pair.

The command field accepts speech or typing: “Create a workspace called Planning,”
“Open Planning,” “Note: Review the launch brief,” and “Sync my devices.” Commands
are reviewed before submission. General agent requests and embedded Office/code
applications are not yet implemented in this native host.
