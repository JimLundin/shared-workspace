# Browser pairing validation

Pairing presents QR, share and copy actions first, with the full invitation behind
an explicit disclosure. Workspace selection remains exact: a device may receive
several workspaces, and connecting it does not grant access to unrelated spaces.

The UI reports observed host stages: checking the invitation, connecting this
device, contacting the inviter and saving the workspace. A saving stage remains
pending until the host returns the committed snapshot. Old progress callbacks and
reads cannot overwrite a completed join. Retry guidance reflects the last stage,
retains the same invitation in memory and does not display raw remote errors.

The compact invitation codec and relay-address compatibility fix are owned by the
browser host. See [the cross-engine pairing evidence](../checks/PAIRING-DIAGNOSIS.md)
and [the transport diagnosis](../host-wasm/tools/WEBKIT-RELAY-DIAGNOSIS.md).

Validation on 25 October 2026:

- Strict TypeScript and production builds pass for the browser app and shared UI.
- 21 shared UI unit tests, 7 browser unit tests and 8 interface tests pass.
- Three focused UI mutations detect their intended failures.
- The final built application passes 12 real-host UI assertions, including all
  four observed progress stages, plus 5 update/recovery assertions.
- The final presentation fixes have focused checks for wording, progress guidance,
  retry and preserving an invitation disclosure across refresh.

[Production UI evidence](evidence/production-ui.json) and
[update evidence](evidence/update.json) use persistent Chromium profiles and real
Turso/Iroh hosts. Cross-engine host evidence is separate. These checks do not
establish physical iPhone camera, microphone, touch or Home Screen behavior.

Ignored screenshots under `artifacts/pairing-*-fixture.png` use clearly labelled
synthetic UI fixtures, with no live enrollment capabilities. Publication and the
physical iPhone retry remain separate from these local test results.
