# Native workspace build and phone installation

Implementation notes, 25 October 2026. These distinguish documented platform
support from results for this application. The native implementation uses one
shared Rust core with Iroh and **Turso Database**, plus a shared TypeScript UI in
Tauri's system WebView. The existing Python assistant remains a separate owner
of its existing tasks and desktop capabilities during this first delivery.

## Versions and runtime contents

| Part | Selected version or platform | Role in the delivered app |
| --- | --- | --- |
| Tauri | Rust `tauri` 2.12.5, `tauri-build` 2.7.4; CLI 2.12.4 | Native application lifecycle, WebView and typed command bridge. |
| Iroh | 1.4.1 | Authenticated, encrypted peer connections; direct UDP where permitted and configured relay routing when needed. |
| Turso Database | Rust `turso` 0.8.3 | Local embedded database engine on every participating device. This is not libSQL and does not use a Turso Cloud primary. |
| UI | TypeScript compiled to bundled JavaScript/CSS/HTML | Shared workspace views and command presentation. |
| Windows | x86-64, system WebView2 | Native executable and per-user NSIS installer. WebView2 is shared with the system; the installer can acquire its bootstrapper if the runtime is absent. |
| iPhone | iOS 16 or newer, arm64, system WKWebView | Native `.app` with the same Rust core and bundled web assets. Device installation needs Apple signing and provisioning. |

Node/npm, Rust/Cargo, CocoaPods and Xcode are build tools. They are not a Node
server, Python interpreter or separately bundled Chromium engine installed with
this native app. Exact installed/download sizes and process memory remain build
and device measurements, not estimates inferred from source size. Tauri still
has framework code and uses the OS web engine; “system WebView” does not mean
zero runtime cost.

Versions were checked against the published
[Tauri CLI package](https://www.npmjs.com/package/@tauri-apps/cli/v/2.12.4),
[Tauri crate](https://crates.io/crates/tauri/2.12.5),
[Turso crate](https://crates.io/crates/turso/0.8.3) and
[Iroh crate](https://crates.io/crates/iroh/1.4.1). The committed lockfiles define
the actual dependency graph; a package's platform claim does not prove that
this entire graph compiles on every target.

## What the storage choice means

The official Rust quickstart identifies `turso` as the recommended local engine,
a ground-up Rust rewrite of SQLite. It separately documents libSQL embedded
replicas; those replicas route writes to a cloud primary and are not the model
selected for equal device hosts. The application implements its own permitted
workspace record exchange over Iroh; using Turso locally does not automatically
supply that replication protocol.

Typing is explicit. Turso normally uses SQLite-compatible dynamic storage
classes and affinity. `STRICT` tables enforce the declared base types at storage;
custom types, composite types and arrays extend this system, with custom types
currently documented as experimental. The first durable schema uses verified
`STRICT` tables and constraints, alongside validated Rust values. Merely choosing
the engine does not make arbitrary SQL or JSON strictly typed.

Sources: [Rust quickstart](https://docs.turso.tech/sdk/rust/quickstart),
[data types and STRICT tables](https://docs.turso.tech/sql-reference/data-types),
[engine introduction](https://docs.turso.tech/introduction),
[engine source](https://github.com/tursodatabase/turso).

## iOS builds versus physical installation

Tauri's iOS toolchain requires macOS and full Xcode. The CLI creates the Apple
project with `ios init`; CI generates that project from committed configuration
rather than maintaining a hand-written `.pbxproj`. The selected CLI exposes
`ios build --no-sign`. Its implementation skips code signing and emits a
simulator `.app` for `aarch64-sim`/`x86_64`, or an unsigned device `.ipa` for
`aarch64`. No fictional development-team ID or signing identity is needed for
that build-validation path.

The distinction is important: **an unsigned device IPA cannot be installed
straight onto a physical iPhone**. It demonstrates a device-target build, not
installation, microphone access or phone-to-PC synchronization.

The user currently has no known Mac access or Apple Developer membership. The
available physical installation paths are:

| Path | What the user needs | Consequence |
| --- | --- | --- |
| Personal development | A Mac with Xcode, a free Apple Account signed into Xcode, access to the phone and its permitted development setup | Xcode provisions a Personal Team build. Apple's current limits include three installed development apps per device and profiles expiring after seven days, requiring rebuild/reinstallation. |
| Windows development signing through AltServer (third-party) | A permitted AltServer installation and Apple support software on Windows, a free Apple Account entered in AltServer, an unlocked/trusted USB-connected phone, and allowed Developer Mode | AltServer documents directly signing/installing a local IPA without installing AltStore first. Directly sideloaded apps need manual reinstallation every seven days and remain subject to the three-app free-account limit. This route has not been exercised here. |
| TestFlight | Apple Developer Program membership, an App Store Connect app, signing/provisioning and an uploaded beta | A signed beta can be installed through TestFlight. A Mac build runner can do compilation, but does not replace membership, credentials or Apple's distribution requirements. |

No membership purchase, account registration, App Store upload or signing secret
was performed as part of this implementation. User/device management policy
can impose further installation or network restrictions. Developer Mode does
not remove the code-signing requirement.

Sources: [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/#ios),
[Tauri App Store distribution](https://v2.tauri.app/distribute/app-store/),
[Tauri CLI 2.12.4 iOS build source](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.4/crates/tauri-cli/src/mobile/ios/build.rs),
[Apple membership comparison and Personal Team limits](https://developer.apple.com/support/compare-memberships/),
[Apple TestFlight](https://developer.apple.com/testflight/).

## Documented Windows + USB signing route

A Mac or paid membership is **not inherently required to sign a test build**.
AltStore's official documentation describes AltServer for Windows signing with
a free Apple Account and connecting by USB **or** the same Wi-Fi network. Its
release notes document a direct **Shift-click tray icon → Sideload .ipa…**
action, which installs a local IPA without installing AltStore on the phone.
This makes a downloaded unsigned CI IPA a candidate input to a Windows signing
step; it does not make the unsigned file directly installable.

The documented preparation includes Apple iTunes and iCloud support software
(the standard instructions use Apple's installers rather than Microsoft Store
packages), running AltServer, connecting/unlocking the phone, trusting the PC,
and entering the Apple Account in AltServer. The iOS device must permit trust
for that account's developer apps and enable Developer Mode on iOS 16 or later.
Credentials belong only in the installed signing application's own dialog,
never in chat, repository files or CI logs. AltStore states that it sends the
credentials only to Apple; this research has not audited that implementation.

Direct AltServer installs need manual reinstallation every seven days. AltStore
itself can automate refresh while connected to AltServer, but occupies one of
the three available free-account app slots. The direct route avoids needing
AltStore on the phone. Exact application capability/provisioning compatibility,
installation, in-place refresh and retained workspace identity still require
verification with this app and the actual device.

This is a documented **third-party development-signing option**, distinct from
Apple's supported Xcode and TestFlight workflows. The user reported a policy
restriction on the HTTP LAN preview; that does not establish that USB signing,
AltServer installation, device pairing or developer-app trust are allowed.
The normal Windows setup also mentions private-network permission and Wi-Fi
sync; we have not assumed those changes are permitted or necessary for an
untested USB-only setup. Apple's device-management schema can restrict local
app installation, host pairing and trust for free-account developer apps.
AltServer does not authorize bypassing any of those restrictions.

No AltServer installation, Apple-support-software replacement, account login,
firewall change or phone setting change was performed. Once the device IPA is
built, its exact hash and these prerequisites can be used for a concrete manual
installation walkthrough if the user's device policy permits it.

Primary sources were read from the official AltStore FAQ repository at commit
`bcbf9fc229391828e7bd7c47ad3a0cacc5c9ac06` (21 October 2026); the rendered FAQ
website returned HTTP 403 in this environment:

- [Official Windows setup](https://github.com/altstoreio/FAQ/blob/bcbf9fc229391828e7bd7c47ad3a0cacc5c9ac06/altstore-world/how-to-install-altstore-windows.md)
- [Official AltServer USB/Wi-Fi behavior](https://github.com/altstoreio/FAQ/blob/bcbf9fc229391828e7bd7c47ad3a0cacc5c9ac06/altstore-world/altserver.md)
- [Direct IPA sideloading release notes](https://github.com/altstoreio/FAQ/blob/bcbf9fc229391828e7bd7c47ad3a0cacc5c9ac06/release-notes/altserver.md)
- [Free-account app and expiration limits](https://github.com/altstoreio/FAQ/blob/bcbf9fc229391828e7bd7c47ad3a0cacc5c9ac06/altstore-world/your-altstore.md)
- [Apple device-management restriction schema](https://github.com/apple/device-management/blob/release/mdm/profiles/com.apple.applicationaccess.yaml)

## Build-validation workflow

[Native hosts CI](../.github/workflows/native-hosts.yml) is configured to run on
native-source pushes and manually when the workflow is available on GitHub.
It records the exact commit in its generated artifact reports.

- Linux builds and tests the real Turso/Iroh core, checks Rust formatting and
  warnings, then runs the independent-process acceptance harness against the
  release `workspace-peer` executable.
- Windows runs the frontend type checks and public-interface tests, repeats
  core and process checks on the real Windows target, and builds
  the system-WebView executable and NSIS installer. A hash manifest accompanies
  the artifacts. A CI compilation is distinct from launching it on the user's
  Windows desktop.
- Hosted macOS uses its installed Xcode and records Xcode, Swift, Rust and iOS
  runtime versions. It generates the Tauri iOS project, builds the simulator
  target without Apple signing credentials, launches the app twice, and checks
  a fresh native readiness receipt with the same device identity after restart.
  It captures simulator screenshots and builds a separately labelled unsigned
  physical-device IPA.

The startup receipt is written only after the real host opens and its public
snapshot succeeds. It contains identity/version/startup facts, no keys, pairing
tickets or workspace content. This smoke check is scoped to native startup and
identity persistence; it does not prove spoken recognition, physical iPhone
behavior, cross-network synchronization or long background execution.

**Current evidence:** actionlint 1.7.12 accepted the workflow, and its inline
Python parsed locally. [GitHub run 40195625077](https://github.com/JimLundin/toolkit/actions/runs/40195625077)
for commit `d21116de298f86c08a63a29dfc7ae229deecad54` was blocked before any
job steps executed. GitHub reported that recent account payments had failed or
the spending limit needed increasing. All three jobs have empty step lists;
this is an Actions account execution blocker, **not a compiler/test failure**.
No Windows or iOS artifact was produced by that run. This Linux/WSL machine has
no Xcode or Windows C++ toolchain, so those platform builds remain unverified
until an enabled runner executes them.

## Local Windows build and launch result

The local GNU Windows release build succeeded with Rust 1.97.1 and a user-local
LLVM-MinGW 20250402 compiler. Turso's GNU resource build invokes bare `windres`;
a wrapper routed that command to the actual target resource compiler. No
database or framework dependency was patched. This is separate from the
configured MSVC/NSIS installer job, which remains blocked by Actions billing.

The portable app contains `workspace-host.exe` (36,427,264 bytes) and
`WebView2Loader.dll` (160,880 bytes), plus documentation/build metadata. Its PE
imports require Windows system libraries and that loader; no additional compiler
runtime DLL was needed. The system must provide WebView2. The
[build evidence](../native/checks/evidence/windows-build.json) records hashes.

An actual launch on this laptop was denied before process creation. Windows
Defender recorded event **1121**, rule
**01443614-CD74-433A-B99E-2ECDC07BFC25**, against this exact executable. Microsoft
names the rule “Block executable files from running unless they meet a
prevalence, age, or trusted list criterion.” File ACLs permit execution; this
was the managed executable-trust control. The intended native UI save/restart
and competing-launch checks therefore did not execute. The portable build is
available for an approved device or an IT-approved trust path; it is not a
verified installation on this laptop. No policy was changed and no alternative
loading/launch method was used.

Source: [Microsoft ASR rule reference](https://learn.microsoft.com/en-us/defender-endpoint/attack-surface-reduction-rules-reference#block-executable-files-from-running-unless-they-meet-a-prevalence-age-or-trusted-list-criterion).
Observed evidence: [Windows launch](../native/checks/evidence/windows-launch.json).

## Connectivity and lifecycle constraints

Iroh explicitly supports iOS, including device and simulator builds. Its Swift
guide calls out linking `Network.framework` on iOS. The shared native Rust core
still needs this project's full iOS build to establish compatibility with its
other dependencies.

Native Iroh tries direct connectivity and can use an encrypted relay connection.
The relay routes traffic; it does not become the workspace database or permanent
work authority. Corporate networks may require permitted relay destinations,
WebSocket support on outbound TCP 443 and configured address lookup. The fact
that ordinary HTTPS works does not prove that relay traffic is allowed.

The earlier local HTTP phone preview was prohibited by policy and stopped. This
native implementation does not reopen that preview, publish a tunnel or treat
Iroh as permission to evade device/network policy. Permitted routing still
needs a real device check. iOS foreground/resume restores peer activity; no
always-running background process is assumed.

Sources: [Iroh compatibility](https://docs.iroh.computer/compatibility.md),
[Iroh Swift/iOS guide](https://docs.iroh.computer/languages/swift.md),
[Iroh enterprise network guidance](https://docs.iroh.computer/configuring-networks.md).

## Browser alternative: possible, with different limits

It would be incorrect to say that peer synchronization is categorically
impossible in a browser. Iroh currently compiles to WebAssembly and can act as
an encrypted peer there. All browser connections must use a relay because the
browser cannot send direct UDP, and the project recommends an application-specific
`wasm-bindgen` wrapper rather than a ready-made npm package. Turso also provides
browser WebAssembly bindings.

That establishes library capability, not this app's Safari/PWA compatibility or
persistent-storage behavior. A browser version would need its own storage,
lifecycle and permission validation, and would still lack the native desktop
capabilities targeted by the product. No PWA or public preview was deployed as
a workaround for the user's policy restriction. The current implementation
continues with the native host architecture.

Sources: [Iroh browser documentation](https://docs.iroh.computer/languages/wasm-browser.md),
[Turso WASM bindings](https://github.com/tursodatabase/turso/tree/main/bindings/javascript/packages/wasm).
