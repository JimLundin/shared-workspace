# Shared native frontend

Unbranded TypeScript UI for the Tauri Windows/iPhone hosts. No UI framework,
HTTP data service, seeded workspace data or browser speech fallback.

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
buffers and unconfirmed UI retries are memory-only in this first version; closing
the WebView does not preserve those buffers. Confirmed work remains in the
native store and is read again on launch.

Speech uses the native `workspace-voice` plugin with session identities. iOS
transcripts retain their addressed context; Windows opens its OS dictation
control after the command field is focused. QR rendering is local. Camera scans
only fill a ticket for explicit joining; tickets are not logged. Invitations stay
under a copy/paste disclosure by default, with explicit workspace selection. The
shared Host interface accepts optional observed joining stages; adapters without
them retain a generic waiting state. Retry guidance uses the last observed stage,
and saving progress does not stand in for a committed result. Network
unavailability is separate from a readable local store.

Validation at this checkpoint: production build; 21 public-interface unit tests;
8 browser checks; 3 targeted mutants caught by their intended assertions. Browser
fixtures are exclusively under `tests/`, not imported by the production entry.
They validate UI behavior, not real Turso/Iroh/native speech/camera operation.
The ordinary-browser check verifies that a missing bridge stays unavailable.
Physical iPhone safe areas, software keyboard, dictation, camera and actual host
synchronization require the shell/core integration runs. No such hardware result
is claimed here.
