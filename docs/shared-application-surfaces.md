# Shared application and document surfaces

The shared workspace carries three kinds of application surface alongside
existing notes:

| Surface | Shared state | Editing and access |
| --- | --- | --- |
| Application | App identity, HTTPS launch URL, title and revision history | The real application owns its files, account and session. |
| Web document | App identity, the exact HTTPS document URL, title and revision history | The document's existing service permissions still apply. Saving its link does not grant access or copy the document. |
| Code document | Language, text, title and revision history | The workspace's own editor reads and writes the shared text. It does not run that code or provide a terminal. |

The resource ID is stable; workspaces contain placements referring to it.
Removing a placement retains the resource and all revisions. App page state,
focus, navigation, cookies, open tabs and unsaved editor drafts remain local.
Adding a surface on one peer does not automatically launch it on another.

## Shared interface and compatibility

`native/core/src/model.rs` is compiled into both the native host and browser
WASM host. `native/ui/src/protocol.ts` validates snapshots and exposes the same
variants to the UI. `native/ui/src/surfaces.ts` owns preset targets and local
presentation decisions.

New commands use the existing durable commit, retry and conflict receipts:

```json
{"type":"create_surface","operation_id":"create-code","workspace_id":"work","resource_id":"script","title":"script.py","surface":{"type":"code_document","language":"python","text":"print(42)\n"}}
{"type":"edit_surface","operation_id":"edit-code","workspace_id":"work","resource_id":"script","expected_heads":["create-code"],"title":"script.py","surface":{"type":"code_document","language":"python","text":"print(43)\n"}}
{"type":"create_surface","operation_id":"create-word","workspace_id":"work","resource_id":"word","title":"Word","surface":{"type":"application","application":"word","url":"https://word.cloud.microsoft/"}}
```

App identifiers are `word`, `excel`, `powerpoint`, `vscode`, `diagrams` and
`website`. A web document has `type: "document"` with the same `application` and
`url` fields. Code languages are `plaintext`, `markdown`, `javascript`,
`typescript`, `python`, `rust`, `json`, `html` and `css`.

A revision has its common `id`, `author`, `title` and `parents`, plus exactly one
of `body` (a note) or `surface` (an application/document payload). Note revisions
keep their original JSON shape. Notes cannot become surfaces through an edit,
or surfaces become notes. Concurrent edits remain separate heads until an edit
names all observed heads; this also applies to code text and changed URLs.

Existing `create_resource` and `edit_resource` note commands and signed actions
are unchanged. The signing domain, operation envelope and causal history remain
v1; no stored-history rewrite is needed. Raw legacy JSON signed independently
of the current serializer is verified, imported, reopened and mixed with new
surfaces in `native/core/tests/surfaces.rs`.

This preserves old **note** history and note-only exchanges. An older host that
does not understand the additive surface variants must be upgraded before it
can import a workspace containing them. It rejects unknown operations; the new
host does not disguise a surface as a note or discard it for an older peer.

Web targets require absolute HTTPS URLs with no embedded username/password,
control characters, whitespace or backslashes, up to 8 KiB in UTF-8. Code text
uses the existing 128 KiB document bound and cannot contain a NUL character.
Both local commands and imported signed operations enforce those checks.

## Browser embedding and the earlier native trials

The browser workspace runs with COOP `same-origin` and COEP `require-corp` for
its database worker. A third-party iframe must satisfy both the target's frame
policy and that embedding policy. Chromium's `credentialless` iframe can relax
the latter for a credential-free frame; it cannot override CSP `frame-ancestors`
or `X-Frame-Options`, and it does not share the user's signed-in application
profile. Linux WebKit 27.2 does not expose that iframe capability in these checks.

Unauthenticated checks on 2026-10-25 found:

| Target | Observed frame policy | Browser presentation |
| --- | --- | --- |
| `https://vscode.dev/` | CSP `frame-ancestors 'none'` | Real workbench in an external browser tab. |
| Microsoft 365 Word entry / `https://word.cloud.microsoft/` | Redirected Word page: XFO `DENY` and restricted CSP frame ancestors | External browser tab with the user's own service authentication. |
| Microsoft 365 Excel entry / `https://excel.cloud.microsoft/` | Redirected Excel page: XFO `DENY` and restricted CSP frame ancestors | External browser tab with the user's own service authentication. |
| `https://embed.diagrams.net/` with the documented JSON embed parameters | No observed frame-ancestor denial; ordinary iframe blocked by parent isolation | Real editing/save/export/restore verified in a headed Chromium credentialless frame. The current release keeps the Diagrams preset external; it has no shared diagram-XML adapter. |

Actual Chromium 156 iframe attempts confirmed the Office/VS Code denials with and without
`credentialless`; the isolated parent remained isolated. These checks use fresh
test profiles and public URLs, not existing Office documents or accounts. They
do not establish physical iPhone Safari behavior. A permitted external app can
be added as an embedded surface only after its real interaction and save
behavior are checked under these policies. The [retained embedding probe and
evidence](application-embedding/README.md) contain the working diagrams.net URL,
validated message handshake, saved/exported XML hashes and fresh-context restore
result. Headless Chromium received HTTP 403 where headed Chromium succeeded;
neither result removes the need for a fallback on unsupported engines.

The earlier native application-view path is different: it navigates separate
WebView2 application views directly. Its
[Office and VS Code evidence](https://github.com/JimLundin/toolkit/blob/main/docs/webview2-ui-investigation.md) remains applicable:
Word A and Excel A passed edit/save/reopen, Word B failed persistence, and VS
Code's workbench loaded while file save/reopen remained unproved. Those results
do not imply that the website can iframe the same services. The
[document-surface research](https://github.com/JimLundin/toolkit/blob/main/docs/document-app-surfaces.md) also keeps Office's
read-only embedding products and CSPP/WOPI rules distinct from normal editing.

## Verification

The native Store tests cover durable surface reopen, exact retry, changed-payload
retry rejection, placement removal, peer import, concurrent note/code edits, stale
resolution, legacy signatures and invalid signed targets. The browser Domain
test checks exact durable request replay for surface creates and edits. UI
decoder tests reject mixed/unknown variants, dangerous URL schemes, credentials,
oversize code and NUL text. Real UI editor/save/peer verification is a separate
acceptance check; passing these domain tests alone does not establish it.

The current Chromium → WebKit [browser-host surface acceptance](../browser/checks/SURFACE-ACCEPTANCE.md)
passed 34 assertions using actual Turso OPFS and Iroh peers. The
[retained report](../browser/checks/evidence/surfaces.json) records stable source
hashes, shared code/Office targets, full browser-process recovery, retained
offline branches, explicit resolution and exact retry. The CodeMirror helper's
real-browser tests separately passed on Chromium and Linux WebKit for typing,
undo, selection/focus retention, theme changes, read-only commands, Tab exit and
disposal. Full product UI acceptance remains separately owned.
