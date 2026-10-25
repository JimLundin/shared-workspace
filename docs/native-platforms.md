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
Python parsed locally;
this Linux/WSL machine has no Xcode or Windows C++ toolchain. Results of the
actual GitHub jobs must be recorded by run URL and commit before describing
Windows or iOS builds as passed. No unrun job is a successful build.

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
