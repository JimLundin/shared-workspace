# Equal device hosts with one shared native workspace core

The distributed workspace uses a Rust core with embedded **Turso Database**
(the new Rust engine, not libSQL), Iroh transport and a TypeScript presentation
inside Tauri 2. The same core runs on Windows and iPhone, so durable revisions,
pairing and authorization do not have independent platform implementations.
Tauri replaces the Windows-only shell choice in ADR 0002 for this new host:
its supported Windows/iOS packaging and native bridge reduce the amount of
platform glue we must maintain, at the cost of another build-time framework.
The system WebViews remain WebView2 and WKWebView; no Node server, Python runtime
or bundled Chromium engine is required by the new application.

Each device owns its identity, durable permitted workspace records and local
view. Signed operations retain their causal parents; concurrent resource
revisions remain separate until an explicit resolution observes them. Workspace
membership can be extended by authorized members, and paired peers exchange
retained records without a permanently designated leader. Iroh relays route
encrypted traffic; they do not own workspace content or decide which work runs.

The first delivery owns workspace notes and synchronization. Existing assistant
tasks, keyboard hooks, audio capture and uncertain application effects remain
with their current owners. This native app is not a second executor for the
same work. Pi runtime/provider integration and migration of those effects need
their own accepted interface and recovery checks before removing the legacy
runtime. Platform signing and actual iPhone lifecycle/network verification are
delivery requirements, not guarantees supplied by compiling a simulator app.

The user reported a policy restriction on local phone HTTP preview sharing.
This app has a native peer transport and must use a permitted installation and
network path; it does not bypass that policy through a tunnel or public preview.
