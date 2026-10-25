# Compact controls and neutral settings — UI lane frozen

UI SOURCE is frozen for independent review. No further edits or builds by this lane
without a coordinated reopen. Browser/app DIST is not frozen by this lane and was
not rebuilt during this polish task. Root/peer owns update polling, its integration
build/tests, final release, commit and Windows Downloads/ZIP sync/reload. No commit
or deployment by this lane. Source baseline: 626289d in /home/jim/toolkit-hosts.

## Changes and reason

| Before | After | Why |
| --- | --- | --- |
| Wide microphone dominates the canvas | 48×48 microphone plus secondary 44×44 keyboard | Keep capture available without dominating work |
| Settings wrap like filter pills | Full-width rows grouped into Workspaces, Add workspace, Settings | Make destinations and actions easy to scan |
| Appearance choices resemble cards | Full-width choice rows with preview and selected check | Present one clear device preference |
| Dark surfaces have a green cast | Neutral charcoal with a restrained blue accent | Let content establish the hierarchy |
| Update hidden in settings/dot | Conditional top-level Update available button | Make the available action discoverable |

Installed emil-design-eng, mobile-native and apple-design guidance applied. Existing
voice capture, editor/draft identities, CodeMirror lifecycle, scoped credentials,
exact retry, modal focus/Back, safe areas, 16px inputs and reduced motion remain.

Dark background is #17181a; light is #f5f5f5. Theme-color tracks the same values.
Root/peer is matching browser index/manifest startup colors in its lane. Browser
options use row styling too; their empty state no longer claims the app is current,
because updates now have an independent top-level action.

## Agreed update contract

- `AppOptions.openUpdate?: () => void` opens root's guarded update/recovery dialog.
- `setUpdateAvailable(available = true)` supports existing calls and explicit false;
  unchanged values do not rebuild the UI.
- With a callback and available update, render `#workspace-update` in the top bar,
  visible text and accessible name `Update available`, `data-action="update"`.
- Click uses the same history-close ordering as App options if a shared modal exists.
  It never reloads itself or bypasses root's unfinished-input guard.
- Native hosts omit the callback. The old dot and App options update suffix are gone.
- `hasUnsubmittedInput()` also includes every non-idle VoiceSession state: empty
  listening/starting/stopping, system dictation and provider transcription.

## Verification

- All 27 fixture journeys pass; the added journey checks conditional top-level update,
  retained draft contents, explicit clearing, callback routing and empty live recording.
- The provider journey additionally asserts empty transcription blocks update. The
  targeted provider/update rerun passes both cases after this assertion was added.
- Strict native UI and browser app typechecks pass against the currently integrated
  source. Native UI build passes; no browser dist build by this lane during polish.
- `git diff --check` passes.
- At 320×568 with a long workspace title and a visible update button, no page/header
  overflow occurs. Mic is 48×48; keyboard/share/update meet 44px; rows are 52px high.
  Measurements: `artifacts/polish-layout.json` (emulation, not physical hardware).
- Root visually reviewed the phone, grouped settings and desktop screenshots and
  confirmed the palette/control hierarchy is consistent. Independent lifecycle
  review and final real update/browser checks are owned by root/peer.

Inspected synthetic-fixture screenshots:
- `artifacts/polish-desktop-light-fixture.png`
- `artifacts/polish-desktop-dark-fixture.png`
- `artifacts/polish-phone-dark-fixture.png` (visible top-level update)
- `artifacts/polish-settings-phone.png`
- `artifacts/polish-appearance-phone.png`
- `artifacts/polish-recording-phone-fixture.png`
- `artifacts/polish-transcript-phone-fixture.png`
- `artifacts/polish-small-phone.png`
- `artifacts/polish-settings-small-phone.png`

These verify composition and measured touch-target bounds. Physical iPhone touch,
keyboard/safe-area behavior and real OpenAI capture still require device validation;
no new claim of physical testing or provider recognition is made by this lane.

## Frozen hashes

- native/ui/src/app.ts: `870ab4782525f6de43dce9fa8ffbddb21e3f6a970011bea79a6cfb37b7f17ab9`
- native/ui/src/style.css: `957ba0ac207578615293d40356f6308d413b94c97b0c15354ff49aaa6f3ff94a`
- native/ui/src/theme.ts: `9fc90bea12f03f5a35fe435522cef4afd0fb9bc0fb45f45d2f2518de75916bd6`
- browser/app/src/style.css: `425018bc607d819c6a0b2e952911d66863d5d6e5f6cd5af56915135b85d73457`

Local native candidate: index-C72_gL4Y.js / index-DGwEWqqC.css. Root owns final
candidate asset hashes. Fixture server on 127.0.0.1:1463 remains available for the
independent reviewer (owned exec session 29546); it can be closed after review.
