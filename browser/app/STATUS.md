# Adaptive glass surfaces and dedicated Settings — UI lane frozen

UI SOURCE is frozen for independent review. No further edits or builds by this lane
without coordinating a reopen. Browser DIST was not rebuilt by this lane in this
scope and is not declared frozen. Root/peer owns browser integration, repository
split, final validation, commit/publication and deployment. No renames, file moves,
main.ts/pwa.ts/workflow edits, commits or deployment by this lane.

Baseline ac9e45c in /home/jim/toolkit-hosts. UI-owned files changed: native/ui app.ts,
style.css, interface.spec.ts and browser/app style.css. Theme values remain neutral.

## Design rationale

| Before | After | Why |
| --- | --- | --- |
| Settings mixed with workspace selection | Dedicated Settings gear, with grouped device/workspace preferences | Give preferences a predictable location |
| Opaque bars occupy fixed layout rows | Floating translucent navigation and capture surfaces | Retain the context of material scrolling beneath controls |
| Fixed card columns and one modal placement | Content-driven columns; desktop anchored panels; compact bottom sheets | Adapt to available window space without resetting work |
| Effects applied indiscriminately | Solid readable documents/editors; restrained glass on controls | Keep long-form work legible |
| Glass assumes all visual capabilities | Solid unsupported/reduced-transparency/contrast/forced-color fallbacks | Keep controls readable and usable |

Installed apple-design, emil-design-eng and mobile-native guidance applied. Glass is
an HTML/CSS treatment, not a native Apple API claim. Chrome uses 18px backdrop blur;
heavy sheets use 20px. No animated blur, resize listener, UA sniffing, device model,
posture inference or hinge API was added. The card grid fits available width; resize
does not recreate the editor or change route. Reduced-motion rules remain in effect.

The main scroll area reserves initial and final reading space around floating
controls. Documents, notes, forms and CodeMirror remain solid. Every main control
retains at least 44px targeting; microphone stays 48×48. Safe areas and dynamic
viewport height remain. The compact update label is Update while its accessible
name remains Update available.

## Settings and integration contract

- Settings gear: `#workspace-settings`, `data-action="settings"`, accessible name
  Settings; it opens the Settings dialog. Close/Escape/Back restore the gear.
- Workspace picker contains switching, Create workspace and Join existing work only.
- Settings separates This device (Appearance, Connections, Install and storage) from
  the named current workspace (OpenAI connection).
- Install and storage invokes the existing openAppOptions callback. Peer owns the
  matching browser dialog title, focus restoration to the gear, install guidance,
  and production/update/public harness navigation.
- Update remains conditional/top-level with the agreed openUpdate and
  setUpdateAvailable contract. Empty recording/transcription still blocks update.
- Opening credentials from Settings uses its current named workspace, independently
  of an unfinished capture elsewhere. A pending key draft or mutation remains pinned
  to its original workspace, with an explanation when another workspace requests
  setup. Both Settings and microphone-initiated setup use that same guard.
- Existing exact retry, receipts, scoped credentials, captured draft identities,
  modal history and CodeMirror lifecycle remain unchanged.

## Verification

- 32/32 Chromium fixture journeys pass. New coverage includes Settings/picker
  separation; resize retaining editor DOM, draft text, dialog and focus; touch
  navigation during tablet split-window resize; accessibility preferences; and
  separately scoped capture/key input through both Settings and microphone setup.
- Live resize covers 1440×900, 1024×768, 744×1133, 540×720, 320×568 and 844×390.
  All top controls stay within the viewport, with minimum 44px targets.
- Reduced transparency and reduced motion are exercised via Chromium's media
  emulation; high contrast and forced colors via Playwright. Glass becomes solid,
  filters are disabled, and keyboard Settings access/focus remain usable.
- Dedicated touch context verifies coarse pointer navigation and theme choice after
  resize. Ordinary journeys exercise mouse and keyboard. No user-agent branching.
- Strict native UI and browser app typechecks pass; native UI build passes;
  git diff --check passes. Browser build/real integration checks remain root/peer-owned.
- 320×568 narrow measurement: page width 320, header width 300, all controls fit,
  microphone 48×48, keyboard/share/update 44px high, settings rows 52px high.
  Measurements are in artifacts/glass-layout.json.

Inspected synthetic-fixture screenshots:
- artifacts/glass-desktop-light-fixture.png
- artifacts/glass-desktop-dark-fixture.png
- artifacts/glass-tablet-fixture.png
- artifacts/glass-split-window-fixture.png
- artifacts/glass-phone-dark-fixture.png
- artifacts/glass-scrolled-phone.png
- artifacts/glass-settings-phone.png
- artifacts/glass-settings-small-phone.png
- artifacts/glass-appearance-phone.png
- artifacts/glass-recording-phone-fixture.png
- artifacts/glass-transcript-phone-fixture.png
- artifacts/glass-small-phone.png

These are browser/emulation layout and interaction checks, not physical iPhone,
tablet keyboard, touch-feel, foldable hinge or installed-PWA verification. Physical
device checks and final Chromium/WebKit integration remain root's release work.

## Frozen hashes

- native/ui/src/app.ts: `777fd9758a8c497a847a51b3cc0e556a7c632d49ad1deaa9c2e23ff8df571593`
- native/ui/src/style.css: `bb32c2810a73d4d4a9f06dd4335bc69a903c65c1d356870c7231ce7308ef7bf3`
- browser/app/src/style.css: `3740622bf719a44bfbc250bc6632a72c8900f8c1b38dea56485ee652c32bc665`
- Native candidate: index-D2vuWiVj.js / index-DwCHL36p.css.

Fixture preview remains on 127.0.0.1:1463 for independent review (owned exec session
51422). Root can request closure after review. No further source/build actions from
this lane without coordinated feedback.

## Targeted scope mutations after freeze

Passed baseline plus three disposable-copy mutants. Each mutant failed an immediate
cross-workspace equality assertion (expected Private, received Work), not a setup,
locator, or test timeout:
1. Settings incorrectly takes the unrelated captured-workspace address.
2. Removing the retained-key destination guard.
3. Microphone setup bypasses the same destination guard.

The existing scoped-settings fixture journey was copied unchanged except its scope
text assertions use immediate textContent/toBe comparisons instead of retrying
locator assertions. Thus a mismatch is a direct assertion failure; the unmutated
baseline passes with that same assertion policy. Only synthetic credentials and
local disposable Vite servers were used. All mutation servers have been closed.

Evidence: `/tmp/glass-settings-scope-mutants-larvZG/report.json`; each case's runner
JSON/stderr and disposable source copy are retained beside it. Reproducer:
`/tmp/check-settings-scope-mutants.mjs`. Frozen app source, the original test, the
entire native/ui/src tree and all browser/app/dist files have identical before/after
hashes. Only this STATUS document changed in the working tree from these checks.

Fixture preview 1463 is still retained for the independent reviewer; awaiting root's
review-finished notification before closing it. No source or dist edits/rebuilds.
