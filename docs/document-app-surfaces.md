# Document and editor surfaces in canvas cards

Researched **2026-10-23** against the primary sources linked below. This note
records the requested presentation and documented integration choices. It does
not implement them or claim completion of the UI lane's document-workflow
trials. The current [canvas guide](desktop-canvas.md) and
[WebView2 investigation](webview2-ui-investigation.md) describe the implemented
host and its execution evidence; [runtime footprint](runtime-footprint.md)
describes the separate runtime target.

The requested default is the document or editor itself, with a small Toolkit
card header and menu. Persistent Toolkit address, Back, Forward and Reload bars
should be absent. The actual document identity and recovery controls can remain
available through the card menu. Application commands should use the vendor's
supported compact layouts instead of Toolkit injecting CSS or removing pieces
of a third-party application's DOM.

**The open design question is how tightly a card represents one document.** A
direct document URL in an application with collapsed controls still permits
application-level commands and navigation. A dedicated editor component can
expose only one document, but Toolkit then owns more of its editing, saving and
recovery behavior. The proposed starting point is one resource per card with
the smallest supported editing UI; a full project/workbench remains a distinct
capability when the task needs it. Hiding controls alone does not enforce this
distinction.

| Owner | What can change | Contract to preserve |
| --- | --- | --- |
| Toolkit card | Remove its own address/navigation form and reclaim its space. The inspected form is in `assistant/canvas_assets/apps.js`; its `shell.css` layout reserves 39 pixels above the application viewport. | Update the viewport geometry used by native clipping and hit testing; merely hiding the form would leave unused space. Keep the real resource identity available. |
| Office or VS Code page | Select supported ribbon, layout or Zen settings through the application's controls. | Commands, editing mode, errors and a way to restore controls must remain usable. A documented option is still subject to verification in the actual service build and WebView2 host. |
| Native application-view owner | Retain the correct profile, document target, navigation generation and view lifetime. | Collapsed UI must not give remote applications the privileged Toolkit bridge or make an old document target valid after navigation. These boundaries already exist in the canvas design. |

**Office: start with the real document and its own ribbon controls.**

A document's existing Office link identifies the resource more directly than
the Microsoft 365 start page. Where an authorized Graph integration already
exists, `driveItem.webUrl` is the documented browser URL for the resource;
“Read-only” in that API property description describes the field, not the
document's editing permissions. Office's own Copy Link is another documented
source. Saving an already authorized link does not require creating a new
sharing link or changing permissions. Word web's Editing mode still depends on
the document's actual sharing permissions and restrictions. [Document URL][driveitem],
[Word sharing][word-share], [Word web editing][word-editing]

Microsoft documents the following ways to reduce Office UI:

| Option | Documented scope and limit |
| --- | --- |
| Word web Single Line Ribbon | A compact ribbon retaining commands through search, dropdowns and overflow. Word can switch back to the classic ribbon. Narrow cards change command placement, so fixed coordinates are not a stable command interface. [Word web ribbon][word-ribbon] |
| Show tabs only / Adjust automatically | Microsoft's ribbon article has an explicit **Web** tab. Show tabs only hides ribbon commands; Adjust automatically reveals the ribbon near the top of the page. Verify the exact choices offered by Word and Excel in the trial account. These options do not promise removal of title, account, mode, status or sidebar UI. [Office ribbon][ribbon] |
| Word Focus / full screen | The documented Focus mode that hides ribbon and status bar is in the **Windows desktop** instructions. The Word **Web** instructions describe browser full screen using F11. Browser full screen is not a documented web-editor ribbon-removal API and need not remain confined to a canvas card. [Word navigation][word-navigation] |
| Word web Immersive Reader | A documented full-screen reading presentation. The inspected web instructions do not establish normal-editor parity or unchanged page layout. It is a separate reading experience to test, not a replacement for the required editing workflow. [Immersive Reader][immersive] |

The `Full-screen mode` ribbon option in Microsoft's general ribbon article
belongs to its desktop panel; do not combine its desktop and web instructions.
Office's own commands provide a concrete first experiment, but the inspected
sources do not offer a general supported API that exposes the complete Word or
Excel web editor with every application control removed. [Office ribbon][ribbon]

Website embeds are different products of the sharing flow. Microsoft's Excel
embed guide explicitly says an embedded workbook cannot be edited. Word's
embed guide describes an embedded view. These can show documents, but they do
not establish a live native-card editing component. [Excel embed][excel-embed],
[Word embed][word-embed]

Keep the official CSPP/WOPI route separate from ordinary document navigation.
Its required UI guidelines say: “Embedding the Microsoft 365 for the web
viewers or editors into native applications using a webview is not allowed.”
They also require editors to be edge-to-edge without surrounding host UI. A
top-level WebView2 document is technically different from an HTML iframe, but
does not provide an exception to that CSPP restriction. Conversely, that rule
alone does not establish that normal navigation to Microsoft's own document
website is technically blocked or supported. Authenticated editing, saving and
reopening in Toolkit remain actual workflow tests. [CSPP rules][cspp],
[WebView2 navigation][navigate]

**VS Code: test the browser editor directly, with a deliberate file destination.**

Microsoft documents `https://vscode.dev` as a free, zero-install browser editor.
It can create files, open selected local files/folders, and browse repositories.
The supported-browser list names current Chrome, Edge, Firefox and Safari;
that is not an explicit certification of a custom WebView2 host. Rendering a
workspace shell establishes loading; editor actions and file persistence need
their own evidence. [VS Code for the Web][vscode-web]

VS Code documents **View: Toggle Zen Mode**, with preferences for full screen,
tabs, activity/status bars, centered layout and notifications. An illustrative
trial configuration is below; it has not been applied by this research:

```json
{
  "zenMode.fullScreen": false,
  "zenMode.centerLayout": false,
  "zenMode.showTabs": "none",
  "zenMode.hideActivityBar": true,
  "zenMode.hideStatusBar": true,
  "zenMode.silentNotifications": false
}
```

Disabling Zen's full-screen transition keeps that mode from requesting the full
display; the host still owns the card's bounds. Keeping notifications available
preserves a route for actionable errors. VS Code also documents toggles for
sidebars, panels, minimap, breadcrumbs and sticky scroll. Its settings and
commands are supported application controls, not a single-document permission
boundary. Do not assume native-window title/menu settings behave identically
in the web version. [Zen Mode][vscode-ui], [custom layout][vscode-layout]

Invoke Zen through the application's menu or Command Palette during the trial:
VS Code's `Ctrl+K Z` chord conflicts with Toolkit's current `Ctrl+K` launcher
shortcut. VS Code documents double Escape to exit Zen; Toolkit also uses Escape
for focus/hide, so routing needs explicit verification. Hiding editor tabs also
removes a familiar document/dirty-state cue; the card must retain an honest way
to identify the active resource and see unresolved saves. [Zen Mode][vscode-ui],
[current canvas shortcuts](desktop-canvas.md)

| File state | Persistence meaning |
| --- | --- |
| Named local scratch file | Open or create it through the app's file UI, then use Save/Auto Save to an actual selected destination. Browser File System Access APIs limit which files/folders the page can use. Prove the destination bytes and reopen them. [Web files][vscode-web] |
| Remembered file/folder handle | The inspected VS Code browser provider stores handles in IndexedDB when available and checks/requests permission when restoring them. A remembered handle is not unrestricted filesystem access or a promise that permission survives every restart. [Provider source][vscode-files] |
| Untitled editor or browser session state | Treat a restored buffer separately from a saved host file. Toolkit's persistent browser profile alone is not proof of a durable file or protection against profile deletion/eviction. Test buffer recovery and actual file saving separately. |
| Pending repository changes | VS Code documents browser-local storage until the changes are committed. A repository's initial URL or an editor's Save action is not evidence that those edits reached the remote repository. No commit is part of the disposable local-file trial. [Web saving][vscode-web] |

Plain `vscode.dev` does not provide an OS terminal, compiler or normal host
debugger. Only browser-compatible extensions run locally in its web extension
host; they have browser/worker restrictions, no normal Node APIs, and cannot
create child processes. A specifically browser-capable extension can provide
its own capabilities, but this does not imply desktop-extension parity.
Microsoft points to desktop VS Code, Codespaces or Remote Tunnels when a real
execution environment is needed. [Web limitations][vscode-web],
[web extension contract][vscode-extensions], [Remote Tunnels][tunnels]

**Local server variants add a backend.**

| Variant | Additional ownership |
| --- | --- |
| `vscode.dev` with Codespaces or Remote Tunnels | The browser becomes the client of an actual development environment. Files, commands and runtime-dependent extensions live in that environment; the connection and its authentication are separate from the canvas renderer. [Remote Tunnels][tunnels] |
| OpenVSCode Server | Its documented Linux/Docker workflow starts a server and opens its URL in a browser. The inspected launcher runs its bundled `node` with `server-main.js`. Running it on this PC adds a server process, runtime, workspace access and lifecycle to manage. [Project README][openvscode], [launcher][openvscode-launcher] |
| Coder `code-server` | Another server-backed VS Code derivative. Its standalone archives bundle the Node binary and modules; it must be running for the browser client to work. The installation docs currently publish no native Windows releases. A local deployment therefore needs an explicitly selected supported environment and its associated dependencies. [Project docs][code-server], [installation][code-server-install] |

An extra server process need not be installed as an operating-system service,
but it remains additional running software. Do not count either local server
variant as a browser-only or no-Node runtime replacement. If selected as an
optional connected development tool, include its payload and processes in that
scenario's measurements. This research installed and launched none of them.

For a narrower code-document surface, Microsoft's **Monaco Editor** exposes an
editor and text models that Toolkit could host with its own controls. A model
need not represent an existing filesystem file, and ordinary VS Code extensions
do not run in Monaco. Toolkit would have to implement file/save receipts,
recovery and the surrounding document behavior. This is a separate component
choice, not evidence of VS Code workbench compatibility. [Monaco][monaco]

**Suggested acceptance observations for the UI owner**

1. Render the document with Toolkit's address/navigation form absent; verify
   reclaimed height, clipping, input, resize and canvas zoom. Document identity
   and recovery controls remain reachable without a permanent browser bar.
2. For explicitly named disposable Word/Excel documents, record the actual
   editor and supported ribbon states, restore its controls, make distinct edits,
   observe saves, and reopen each exact document to verify the changes. Record
   ribbon preference persistence separately from document persistence.
3. In `vscode.dev`, create/edit a named disposable text or Markdown file through
   the app's file UI. Save, verify the destination bytes, close/reopen, then
   repeat after restarting with the same profile. Check folder access separately
   from single-file access, including any renewed permission prompt.
4. Verify Zen and the selected UI toggles in the actual web build; exercise
   Command Palette, Escape and launcher routing without leaving the card in an
   unrecoverable layout. Keep the app confined to the native view's bounds.
5. Report browser-only editing separately from any backend-dependent feature.
   A workspace shell, local fixture or Monaco example is not proof of a terminal,
   a required extension, authenticated Office editing or a confirmed save.

These are proposed checks, not execution receipts. Use the
[WebView2 investigation](webview2-ui-investigation.md) for current live evidence,
including account reuse and the separate document/edit/save/reopen results.
Shared sign-in and a visible DOM change do not establish a committed document
edit: the exact saved resource must reopen with the change. This research
performed only repository/source reads and public documentation requests.
Supplementary source captures are under `/tmp/toolkit-document-app-sources/`;
the background Office research is
`/tmp/toolkit-document-office-surfaces-research.md`.

[driveitem]: https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0
[word-share]: https://support.microsoft.com/en-us/word/training/share-and-collaborate-with-word-for-the-web
[word-editing]: https://support.microsoft.com/en-us/word/enable-editing-in-your-document
[word-ribbon]: https://support.microsoft.com/en-us/word/using-the-simplified-ribbon-in-word-for-the-web
[ribbon]: https://support.microsoft.com/en-us/office/foundations-experiences/show-or-hide-the-ribbon-in-office
[word-navigation]: https://support.microsoft.com/en-us/accessibility/word/use-a-screen-reader-to-explore-and-navigate-word
[immersive]: https://support.microsoft.com/en-us/accessibility/word/use-immersive-reader-in-word
[excel-embed]: https://support.microsoft.com/en-us/excel/embed-your-excel-workbook-on-your-web-page-or-blog-from-sharepoint-or-onedrive-for-business
[word-embed]: https://support.microsoft.com/en-us/word/embed-a-word-document-in-your-blog
[cspp]: https://learn.microsoft.com/en-us/microsoft-365/cloud-storage-partner-program/online/build-test-ship/ui-guidelines
[navigate]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2#navigate
[vscode-web]: https://code.visualstudio.com/docs/remote/vscode-web
[vscode-ui]: https://code.visualstudio.com/docs/editing/getting-started/userinterface#_zen-mode
[vscode-layout]: https://code.visualstudio.com/docs/configure/custom-layout
[vscode-files]: https://github.com/microsoft/vscode/blob/main/src/vs/platform/files/browser/htmlFileSystemProvider.ts
[vscode-extensions]: https://code.visualstudio.com/api/extension-guides/web-extensions
[tunnels]: https://code.visualstudio.com/docs/remote/tunnels
[openvscode]: https://github.com/gitpod-io/openvscode-server/blob/main/README.md
[openvscode-launcher]: https://github.com/gitpod-io/openvscode-server/blob/main/resources/server/bin/code-server-linux.sh
[code-server]: https://coder.com/docs/code-server
[code-server-install]: https://coder.com/docs/code-server/install
[monaco]: https://github.com/microsoft/monaco-editor/blob/main/README.md
