# Native workspace host

This is a Tauri 2 application for Windows and iPhone. It loads bundled
TypeScript assets into WebView2 or WKWebView and calls the shared Rust workspace
owner directly. There is no HTTP server and no Python or Node runtime in the
installed application. Turso Database and Iroh are compiled into the host.

The visible application name is **Workspace**, a provisional descriptive label.
The bundle identifier is `dev.jimlundin.workspace`.

## Build

From the repository root:

```sh
npm --prefix native/ui ci
npm --prefix native/app ci
npm --prefix native/app run build
```

The Tauri build hook builds `native/ui/dist`. Windows release builds require
Microsoft's C++ build tools and the Rust MSVC target. The NSIS installer uses the
system WebView2 runtime; it installs Microsoft's small bootstrapper only when
that runtime is absent. Node/npm, Cargo and the C++ tools are build tools, not
application dependencies shipped to the user.

On a Mac with full Xcode and its command-line tools:

```sh
npm --prefix native/app run ios:init
npm --prefix native/app run tauri -- ios build --ci --no-sign --target aarch64-sim
```

The `ios:init` script generates the Xcode project from the pinned Tauri CLI and
refreshes the custom icon assets. `gen/apple` is generated build output, not a
second maintained implementation. Use `x86_64` for an Intel iOS simulator and
`aarch64` for a physical-device build. `--no-sign` produces an **unsigned** build;
it does not make the app installable on an ordinary physical iPhone.

Physical installation needs an Apple signing/provisioning route. Personal-team
Xcode deployment needs access to a Mac and an Apple account; TestFlight requires
Apple Developer Program membership and a signed distribution build. No signing
identity, membership, provisioned phone or permitted alternative distribution
route has been supplied for this implementation. CI can verify unsigned device
and simulator builds without claiming phone installation.

The parent [platform record](../../docs/native-platforms.md) describes CI output
and the evidence that is actually available. Exact application dependencies are
pinned in this directory and the native workspace lockfile.

## Native ownership

The `workspace-core::Host` owns the durable local database, device identity,
paired peers and synchronization. The shell exposes only the commands in
[the native protocol](../PROTOCOL.md). Remote navigation is rejected, and native
permissions are granted only to the bundled `main` webview. No outside web page
receives the bridge.

Opening the native host does not wait for a peer or relay to become reachable.
Its snapshot distinguishes transport unavailability from retained local data.
The iPhone application forwards foreground/suspension observations to the host;
it does not promise a process that runs continuously while iOS suspends it.
Desktop exit waits for the owner to shut down. Each launch removes its prior
readiness marker and writes a new `native-host-ready.json` only after opening the
real host and obtaining a snapshot. This marker contains a public device ID,
process ID and timestamp, never workspace material or private keys.

## Voice and pairing

The iPhone voice plugin owns AVAudioEngine and SFSpeechRecognizer on the main
actor. It requests microphone/speech permission only after an explicit start,
prefers on-device recognition where supported, emits a bounded editable
transcript, and cancels on deactivation or audio interruption. A transcript is
not automatically a command. The UI retains the original addressed material and
submits through the same command interface as typing.

Windows opens the operating system's Win+H dictation control only while this
application is foreground. The UI first focuses its command field. Windows owns
its speech UI and stopping; the plugin does not invent recording or transcript
callbacks. Other platforms report unavailable native voice and retain typing.

The official barcode-scanner plugin handles explicit iPhone QR scans. A scanned
invite is validated by the same native `host_join` command as a pasted ticket.
There is no pairing web preview, deep-link relay or camera capture on startup.

The implementation and platform APIs are distinct from evidence of actual
microphone quality, iPhone lifecycle behavior, or phone installation. Those
require a permitted physical-device test and are not established by source
compilation or simulator startup.

## Sources

- [Tauri architecture](https://v2.tauri.app/concept/architecture/)
- [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/)
- [Tauri iOS distribution](https://v2.tauri.app/distribute/app-store/)
- [Tauri mobile plugins](https://v2.tauri.app/develop/plugins/develop-mobile/)
- [Tauri 2.12.5 app lifecycle source](https://docs.rs/tauri/2.12.5/tauri/enum.WindowEvent.html)
- [Apple SFSpeechRecognizer](https://developer.apple.com/documentation/speech/sfspeechrecognizer)
- [Apple AVAudioEngine](https://developer.apple.com/documentation/avfaudio/avaudioengine)
- [Microsoft SendInput](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput)
- [Tauri barcode scanner](https://v2.tauri.app/plugin/barcode-scanner/)
- [Iroh Apple linking requirements](https://docs.iroh.computer/languages/swift)
