# Shared workspace frontend

TypeScript UI mounted by the browser/PWA and optional Tauri hosts. It renders
actual host snapshots, with no UI framework, HTTP data service or seeded work.
The approved adaptive layout uses desktop leading navigation/page toolbar/inspector
and lower touch navigation separate from contextual voice/page actions.

```sh
npm ci
npm run build
npm test
npm run test:browser
npm run test:mutations
```

`dist/` is consumed by `native/app`. The dev/test server binds only
`127.0.0.1:1420`; it is not a phone preview or deployment route.

The native bridge follows [PROTOCOL.md](../PROTOCOL.md). Native replies are
validated in `src/protocol.ts`; `src/session.ts` owns native calls, retained
editor heads and stable in-session retries. Known no-commit rejections permit
correction. Unconfirmed failures retain the exact command for retry. Shared
records are shown only from native snapshots. LocalStorage contains presentation
preferences only, never work records or pairing tickets. Unsaved editor/command
buffers and unconfirmed UI retries are retained by an optional host-owned local
input journal in the browser. Hosts without that port keep memory-only input.
Confirmed work remains in the host store and is read again on launch.

Browser dictation uses the workspace OpenAI provider port; optional native
speech uses the `workspace-voice` plugin with session identities. iOS
transcripts retain their addressed context; Windows opens its OS dictation
control after the command field is focused. QR rendering is local. Camera scans
only fill a ticket for explicit joining; tickets are not logged. Invitations stay
under a copy/paste disclosure by default, with explicit workspace selection. The
shared Host interface accepts optional observed joining stages; adapters without
them retain a generic waiting state. Retry guidance uses the last observed stage,
and saving progress does not stand in for a committed result. Network
unavailability is separate from a readable local store.

The [meeting workflow](../../docs/work-continuity-implementation.md) adds durable
work, pinned input, explicit provider preparation, cited results, dictated action
corrections and origin-host recovery. It keeps application/code surfaces reachable.
Provider, storage and execution effects stay behind their owning interfaces.

Package scripts and the browser workflow define current validation. Browser
fixtures are exclusively under `tests/`, not imported by the production entry.
They validate UI behavior, not real Turso/Iroh/native speech/camera operation.
The ordinary-browser check verifies that a missing bridge stays unavailable.
Physical iPhone safe areas, software keyboard, dictation, camera and actual host
synchronization require the shell/core integration runs. No such hardware result
is claimed here.
