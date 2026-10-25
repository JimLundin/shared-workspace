# Native distributed workspace architecture

This implementation turns the accepted workspace model into an installed app
with real local storage and peer synchronization. The app's visible title is
“Workspace” as a descriptive label; no replacement product brand is selected.

## Tools and responsibilities

| Layer | Tool | Responsibility |
| --- | --- | --- |
| Shared interface | TypeScript and Vite | Workspace navigation, material editing, visible revision conflicts, pairing, peer observations and voice command entry. Vite/Node run during the build only. |
| Native shell | Tauri 2 | Application lifecycle, bundled web assets, native command bridge and platform packaging. |
| System web rendering | WebView2 on Windows; WKWebView on iPhone | Render the shared interface using the operating system's engine. |
| Shared host | Rust | Validate commands, own durable records and identity, project workspace state, reconcile signed histories and manage peer connections. |
| Embedded storage | Turso Database | Each host's local database. This is the Rust database engine, not libSQL and not a dependency on Turso Cloud. Exact type support is verified against the pinned engine. |
| Peer transport | Iroh | Authenticated encrypted direct connections, with relay/discovery support where permitted. Relays carry traffic; they do not own or settle workspace data. |
| Voice | Native platform adapters | Explicit microphone/system-dictation interaction. Transcribed or typed commands address the same workspace operations. Audio is not part of peer replication. |
| Builds | Cargo, npm and platform CI | Compile the shared code and each platform shell. Apple signing is a separate installation requirement. |

## What is inside the app

A Windows installation contains a compiled native executable, bundled web assets
and ordinary platform integration. The new host requires no Python or Node
runtime and carries no independent Chromium distribution. If WebView2 is absent,
the installer uses Microsoft's bootstrapper. The runtime uses a separate local
app-data directory for its identity and embedded database.

The iPhone package contains the same Rust host compiled for iOS, bundled shared
web assets and the native shell/plugins. WKWebView belongs to iOS. The app has
its own identity and database inside its sandbox; it is not a remote browser
session served by the laptop. It can read and edit retained work without the
laptop. Foregrounding the app resumes peer catch-up. iOS may suspend it in the
background, so continuous phone execution is not promised.

The existing Python assistant remains a separate installed implementation until
its application execution, providers, voice and input responsibilities have a
verified migration. The new native workspace does not take over those effects
or create another executor for their current journal. Pi integration is a later
locally owned agent-runtime adapter beneath this shared work interface.

## Shared work and local views

A workspace is durable context. A resource has a stable identity and one or more
revision heads. Placements reference resources; deleting a placement does not
delete its material. A resource's access follows its source workspace even when
a reference appears elsewhere. Screen geometry, active selection, unsent editing
state, microphone sessions and app credentials stay local to each device.

Every mutation has an operation identity. Replicated operations are signed by
their authors and retain causal parents. A device can accept a valid offline
branch alongside another branch instead of overwriting either. Resolution names
all heads the user reviewed; if those observations are stale, the save reports
a conflict. An exact repeated operation retains its original effect. Reusing its
identity with different content is rejected.

Authorized workspace members can extend membership. Creating the first workspace
does not make that device the permanent coordinator. Pairing grants chosen
workspaces; pairing itself does not copy everything from either device.

## Pairing, delivery and disconnects

The inviting app creates an expiring one-use ticket for selected workspaces.
The joining app obtains that ticket, connects to the specified Iroh identity,
and completes an authenticated exchange. Consuming the ticket records the new
membership/contact durably. An exact retry by the accepted peer can recover the
same result; a different peer cannot reuse it.

Peers exchange bounded retained operation sets for their permitted workspaces.
Each validates provenance and membership before import. The database commit,
transport delivery and peer's observed state remain separate facts. “Transport
active” means the local endpoint exists; it does not mean every other device is
reachable or has acknowledged the latest edit.

A disconnected host retains local work. When a permitted route returns, hosts
exchange missing history. There is no timeout-based takeover of external actions
in this first release: the delivered capability is workspace synchronization,
not distributed exactly-once application execution.

## Build and installation evidence

Linux tests can establish shared-core behavior and real isolated Iroh process
exchanges. Windows native builds and execution establish the Windows host.
Unsigned iOS simulator/device compilation establishes build compatibility;
simulator execution establishes only that simulator's behavior. None proves a
signed installation, camera, speech or network behavior on the user's iPhone.

The user has no confirmed Mac access or paid Apple Developer membership.
Physical installation still requires signing, but the documented third-party
AltServer route can sign a local IPA on Windows with a free Apple Account and
a trusted USB-connected phone. It requires permitted device trust/Developer
Mode and periodic renewal; it has not been exercised here. Xcode/Personal Team
and TestFlight are the other documented paths. See the [platform and installation
notes](native-platforms.md#documented-windows--usb-signing-route). No unsigned
artifact will be presented as directly installable on the phone.
The previously prohibited local HTTP preview has been stopped. This project will
not use a tunnel or public preview to bypass that restriction.

Iroh also supports genuine browser peers through WASM and relay transport. The
native shell is selected for this implementation's operating-system capabilities,
voice adapters, embedded engine and platform lifecycle; it is not a claim that
peer synchronization is categorically impossible in a browser. A browser host
would require its own durable-storage/lifecycle work and a permitted HTTPS
installation path. It is not being substituted for the requested native app.
