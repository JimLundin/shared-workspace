# Reusing a signed-in browser session

Researched on 2026-10-22 against vendor documentation and public source. This
note describes integration options and requirements; it is not evidence that a
live authenticated session has been tested.

## Recommendation

Toolkit can reuse the user's signed-in **running Edge or Chrome** through the
browser's opt-in remote-debugging connection. Microsoft explicitly documents
this for Edge, including discovery through `DevToolsActivePort`. Chrome documents
the equivalent flow from version 144. Attach directly with Toolkit's existing
Python WebSocket/CDP layer; adding Chrome DevTools MCP, Node.js or Puppeteer is
unnecessary for the existing browser tools. [Sources: Edge guide][edge-mcp],
[Chrome announcement][chrome-connect], [MCP connection implementation][mcp-connect].

This provides **background work in the user's running browser**. It does not
create a second headless browser that can concurrently share the live profile.
Normal background tabs remain in the tab strip. An experimental CDP option can
create targets outside the tab strip, but those still belong to the running
browser and are not an independent headless instance. [Sources: headless
mode][headless], [profile ownership][profiles], [CDP target creation][target-api].

Read-only Windows executable metadata during this investigation showed:

| Browser | Installed version | HTTPS default |
|---|---|---|
| Microsoft Edge | `155.0.4283.62` | Yes (`MSEdgeHTM`) |
| Google Chrome | `155.0.8059.85` | No |

These observations identify installed products, not which browser or profile has
a particular website signed in. No personal browser was launched, closed,
connected to, or reconfigured during this research. Cookies, credentials,
tokens, history and profile contents were not read.

## Browser setup required from the user

For the user's default Edge browser:

1. Open the intended signed-in Edge profile normally and leave it running.
2. Open `edge://inspect/#remote-debugging`, or `edge://inspect` and then
   **Remote debugging**.
3. Select **Allow remote debugging for this browser instance**.
4. When Toolkit requests a connection, respond to the browser's permission
   dialog. Chrome's documented flow asks for permission for each new connection;
   Chromium's approval-mode server waits for that decision before accepting the
   WebSocket. [Sources: Edge setup][edge-mcp], [Chrome consent][chrome-connect],
   [approval handshake][http-handler].

For Chrome 144 or later, use `chrome://inspect/#remote-debugging` instead. The
initial opt-in and approval of a connection are separate steps. Do not describe
the setup as completely unattended or promise that one approval authorizes all
future reconnects. Chromium's consent dialog activates the browser window;
later background page operations need not bring it forward. The browser also
displays an automation indicator during the connection. [Sources: Chrome
consent][chrome-connect], [connection dialog implementation][connection-dialog].

Toolkit must leave these browser decisions to the user. Enabling debugging or
accepting its dialog is not a site sign-in. The connection can use existing site
sessions, but expired sessions, MFA and company access requirements can still require
interaction. The Edge guide specifically recommends attachment for existing
authenticated sessions and complex authentication flows. [Source: Edge
guide][edge-mcp].

Enterprise policy still applies. Edge's `RemoteDebuggingAllowed=false` disables
remote debugging, and Chromium checks that policy before starting the
approval-mode server. A blocked setting or denied connection should produce an
explanation, not a browser relaunch, policy edit or alternate route around the
restriction. [Sources: Edge policy][edge-policy], [server policy checks][remote-server].

## Why a second headless process is not the solution

Headless mode means the browser does not display its platform windows. Chrome's
modern headless mode shares the normal browser implementation, and Microsoft
documents headless Edge launch as well. This does not change profile ownership:
two independent browser instances cannot share the same user-data directory
concurrently. A retained dedicated profile can switch between visible and
headless runs when one instance exits before the other starts, as described in
[background-automation.md](background-automation.md). It is still a separate
sign-in profile. [Sources: Chrome headless][headless], [Edge launch options][edge-mcp],
[Chromium profile documentation][profiles], [MCP profile exclusivity][mcp-advanced].

From Chrome 136, `--remote-debugging-port` and `--remote-debugging-pipe` are not
respected for Chrome's default user-data directory; a nonstandard directory is
required. Changing `--profile-directory` within the same default root does not
satisfy that requirement. Chrome for Testing retains its automation behavior,
but installing it does not supply the user's existing authenticated session.
The newer user-approved attachment flow is the appropriate supported mechanism
for that session. [Sources: Chrome 136 change][chrome-136], [profile/root
distinction][profiles], [Chrome attachment flow][chrome-connect].

That Chrome announcement should not be treated as proof of identical Edge flag
behavior. Microsoft's current Edge guide documents its own live-browser opt-in
flow, which avoids needing to restart the user's browser with debugging flags.
Copying a profile or extracting authentication material is unnecessary for this
design. [Source: Edge guide][edge-mcp].

## Smallest integration with Toolkit

The reusable layers are `Cdp`, the frame/session handling and operations in
[assistant/browser.py](../assistant/browser.py), and the DOM scripts in
[assistant/browser_dom.py](../assistant/browser_dom.py). The main change is the
connection and ownership boundary.

Model a Toolkit-managed browser separately from an attached user browser. The
managed variant owns its process, Windows job and dedicated profile. The
attached variant owns only its connection, CDP sessions and records of pages
Toolkit created. Both can use the same snapshots, actions, evaluation and
screenshots. This is an implementation recommendation based on the current
module and the attachment APIs, not a requirement to adopt an MCP server.

### Endpoint discovery and connection

- For standard Windows stable installations, look for `DevToolsActivePort` in
  `%LOCALAPPDATA%\Microsoft\Edge\User Data` or
  `%LOCALAPPDATA%\Google\Chrome\User Data`. The file belongs at the **user-data
  root**, not inside `Default` or `Profile 1`. Custom locations and other release
  channels need their actual root. [Sources: Edge locations][edge-mcp],
  [Chrome roots][profiles].
- Read only the endpoint file. It contains a port on the first line and a
  browser WebSocket path on the second. Validate bounded content, a port in
  `1..65535`, and the expected `/devtools/browser` path shape; construct a native
  WebSocket connection to `127.0.0.1`. Use the published path instead of assuming
  port 9222 or inventing a target ID. The MCP implementation does the same
  discovery before calling Puppeteer. [Sources: endpoint writer][http-handler],
  [MCP endpoint reader][mcp-connect].
- Never create, unlink or rewrite that file in attachment mode. The browser can
  reuse a remembered port from it and fall back to an available port. Its
  existence is not proof of a currently reachable or approved connection; handle
  missing, malformed, stale and refused endpoints without modifying the profile.
  [Source: server startup and port reuse][remote-server].
- Do not use `/json/version` or `/json/list` as approval-mode discovery probes:
  Chromium's `OnJsonRequest` returns HTTP 404 in this mode. Connect to the
  published WebSocket, then use CDP after approval. [Source: HTTP
  implementation][http-handler].
- The permission decision occurs **before the WebSocket upgrade completes**.
  Toolkit's original two-second `Cdp.connect` opening timeout is unsuitable for
  that interaction. Give explicit attachment a bounded human-length deadline
  (for example, 60 seconds), expose that it is waiting for browser permission,
  and make Stop close the pending socket. This is an implementation
  recommendation derived from `HandleDebuggingApproval`; it is not a required
  vendor timeout. [Source: approval handshake][http-handler].
- Keep a successful connection for the task. Reconnecting repeatedly can show
  repeated permission dialogs. A denial should end that request; status polling
  should not silently start connection attempts. The browser owns its dialog,
  so Toolkit should not promise to dismiss it when cancelling its own socket.
  [Sources: per-connection consent][chrome-connect], [dialog lifetime][connection-dialog].
- Run the browser-facing transport on Windows alongside the deployed Toolkit.
  Do not assume WSL's loopback is Windows' loopback or introduce network exposure
  to solve a local attachment problem. The upstream MCP troubleshooting guide
  distinguishes WSL networking and recommends Windows shells as one supported
  route. [Source: WSL troubleshooting][mcp-troubleshooting].

The endpoint root does not select an individual named profile. Chrome's
documentation says auto-connect uses the default profile chosen by Chrome and
can access its open windows; current Chromium selects the last-used loaded
profile for the default browser context. Do not equate `User Data` discovery
with a particular work account or silently create an incognito context, which
would lose that session. [Sources: multi-profile behavior][mcp-advanced],
[default-context implementation][browser-context].

A new background tab also need not reproduce an existing tab's application
state. In particular, each tab or window has its own `sessionStorage`, while
`localStorage` spans windows. Explicitly use an existing target
when the workflow needs that page's current state. Do not promise that profile
reuse makes every new tab already authenticated. [Source: Web Storage
specification][web-storage].

### Ownership, disconnect and Stop

- In attached mode, never call `Browser.close`, terminate the browser process,
  assign it to Toolkit's kill-on-close job, or restart it to switch modes.
  Disconnect CDP and clear Toolkit's session handles instead. The browser-level
  close operation closes the browser, while detaching a debugging session is a
  distinct operation. [Source: CDP lifecycle APIs][browser-api],
  [target/session APIs][target-api].
- Create a new Toolkit-owned target unless the caller explicitly supplies an
  existing target. The old `_open` shortcut that reused any `about:blank` page
  must not take over a user's blank tab. Apply tab limits to Toolkit-created
  pages, not all personal tabs. These are ownership requirements for adapting
  the existing module.
- Ordinary user tabs survive detachment. Cleanup may close pages Toolkit
  created, but must distinguish those from personal pages. Closing a socket is
  not a rollback: navigation, requests or JavaScript already started in a user
  page can continue. Report an interrupted action as uncertain instead of
  automatically replaying it. [Sources: detach versus close APIs][target-api],
  [target-handler cleanup][target-handler].
- Hidden targets are an exception: the creating CDP session owns their lifetime,
  and they are destroyed when that session is disconnected. Chromium tests this
  explicitly. [Sources: hidden-target contract][target-api],
  [hidden-target lifecycle test][protocol-tests].

### Background pages and screenshots

For the first supported integration, use
`Target.createTarget({"url": "about:blank", "background": true})` in the existing
browser context. Chromium maps this to a background tab and an inactive window
show action. Continue addressing the target through CDP; do not add
`Page.bringToFront` or desktop input to normal DOM operations. This preserves the
session without requiring a second profile. [Sources: target API][target-api],
[Chrome target implementation][chrome-target-handler].

`Page.captureScreenshot` supports an inactive tab. Chromium's
`ScreenshotInactiveTab` test attaches to a blue page, opens a red foreground tab,
then verifies that the screenshot still shows the blue page. Its implementation
requests frames with `stay_hidden=true` and `stay_awake=true`. Keep
`fromSurface=true` (the default) and Toolkit's existing viewport-bounded capture;
do not capture the desktop or select the tab. This is upstream evidence, not a
claim that every installed browser state or company site was exercised here.
Missing render views, unloaded pages and browser refusals must still be reported.
[Sources: screenshot API][page-api], [inactive-tab test][protocol-tests],
[screenshot implementation][page-handler].

The optional `hidden:true` target feature is **experimental**. It creates a page
that CDP can see but the normal tab strip does not show. It requires remote
debugging, cannot be combined with `forTab:true`, `newWindow:true` or
`background:false`, and lasts only as long as the creating session. The inspected
content handler actually rejects `newWindow` whenever that field is present, so
omit `newWindow` and `forTab` entirely if trying
`{"url":"about:blank","background":true,"hidden":true}`. Validate hidden-target
navigation, DOM interaction, screenshots and cleanup against a disposable
profile before making it a product default. [Sources: API contract][target-api],
[hidden-target implementation][target-handler], [visibility/lifecycle tests][protocol-tests].

Hidden targets use an existing browser context and do not make the personal
browser headless. They also do not eliminate the initial browser approval UI.
Their implementation keeps WebContents hidden and enables rendering for explicit
captures. [Sources: hidden contents implementation][hidden-manager],
[approval dialog][connection-dialog].

## Alternatives

| Option | Session reuse | Fit for Toolkit |
|---|---|---|
| Direct CDP attachment | Uses the running opted-in browser | Smallest addition; preserves the existing Python tools. |
| Chrome DevTools MCP `--autoConnect` | Same attachment mechanism; Edge additionally supplies its user-data root | Useful as a reference or for another MCP client, but adds Node/Puppeteer and another tool layer here. |
| Browser extension plus native messaging | Extension runs inside the already signed-in browser | Supported alternative with more installation, permissions, transport and target-routing work. |
| Separate headless profile | Retains only that profile's own authentication | Useful isolated fallback, but does not inherit the user's normal session. |

The first two rows follow the vendor attachment documentation and connection
source; the last row follows the documented profile ownership rules.
[Sources: Edge guide][edge-mcp], [MCP reader][mcp-connect], [profiles][profiles].

For an extension, `chrome.debugger` is an official alternative CDP transport, and
Microsoft lists the `debugger` API as supported by Edge MV3 extensions on Windows.
It exposes the `Page`, `Runtime`, `DOM`, `Input` and `Target` domains used by much
of Toolkit, but not every CDP domain. Use extension tab APIs and debugger
attachment to replace browser-level target discovery/lifecycle; it is not simply
the same WebSocket channel. Opening DevTools or a user cancellation can detach
the extension, which must handle `onDetach`. [Sources: debugger API][extension-debugger],
[Edge extension API support][edge-extensions].

Native messaging supplies the desktop connection: an extension declares
`nativeMessaging`, a registered host accepts only its allowed extension IDs, and
the browser exchanges length-prefixed JSON with that host through standard I/O.
The extension also needs its debugger permission and user installation.
Enterprise extension restrictions can block debugger attachment or native
messaging; this is not a workaround for a browser or organization denying
automation. [Sources: Chrome native messaging][native-messaging], [Edge host
registration][edge-native], [debugger policy restrictions][extension-debugger],
[Edge native-host policy][native-policy].

Microsoft documents using Chrome DevTools MCP with Edge, while that project's own
README limits its official support commitment to Chrome and Chrome for Testing.
Those statements are distinct: the Microsoft documentation supports the Edge
attachment mechanism, but it does not change Google's MCP support policy.
Direct CDP avoids adding a dependency on that MCP product's support scope.
[Sources: Microsoft guide][edge-mcp], [MCP support statement][mcp-readme].

## Attachment timeout regression

The attachment path gave the WebSocket handshake 60 seconds but supplied a TCP
socket that still had its two-second connection timeout. `websockets` 16.1.1
clears that timeout only on sockets it creates itself. Waiting more than two
seconds for approval therefore failed with `did not receive a valid HTTP
response`; an immediately approved connection also died after two idle seconds.
The next connection attempt could then trigger another browser permission dialog.

`Cdp.connect` now clears the TCP timeout before passing its socket to
`websockets`. The handshake keeps its explicit opening deadline and Stop keeps
its cancellation watcher. Regression tests in
[test_browser_attachment.py](../tests/test_browser_attachment.py) exercise the
real WebSocket transport with approval delayed by 2.5 seconds and with an
approved connection idle for 2.5 seconds. Both failures reproduced on Linux and
the bundled Windows Python before the fix. The tests require subsequent attach
requests to reuse the one approved connection.
Separate checks require Stop and an expired opening deadline to close the
pending socket, so clearing the TCP timeout cannot create an unbounded wait.

## Evidence limits

Verified here: current vendor setup instructions; the native CDP connection seam;
permission timing; endpoint discovery behavior; upstream inactive-tab screenshot
and hidden-target tests; installed product versions and default-browser
registration. Chromium source citations below are pinned to commit
`2ff94f6770e6ca118092da6092592116f6e97753` from 2026-10-22; the fetched files were
checked against that snapshot.

Not exercised here: the user's opt-in or policy state, a live approved
connection, the selected authenticated profile, company sites, hidden targets on
the installed binaries, or authentication after expiry. Those require the
browser's supported user setup and narrowly scoped runtime verification. Existing
dedicated-profile tests cannot by themselves establish those outcomes.

[edge-mcp]: https://learn.microsoft.com/en-us/microsoft-edge/web-platform/devtools-mcp-server
[chrome-connect]: https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session
[chrome-136]: https://developer.chrome.com/blog/remote-debugging-port
[headless]: https://developer.chrome.com/docs/automation-and-testing/headless
[profiles]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/docs/user_data_dir.md
[edge-policy]: https://learn.microsoft.com/en-us/deployedge/microsoft-edge-browser-policies/remotedebuggingallowed
[mcp-readme]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/106d6728583e3dfb18dc34ce27fc9b7c1151c86e/README.md
[mcp-advanced]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/106d6728583e3dfb18dc34ce27fc9b7c1151c86e/docs/advanced-usage.md
[mcp-connect]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/106d6728583e3dfb18dc34ce27fc9b7c1151c86e/src/BrowserManager.ts#L342
[mcp-troubleshooting]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/106d6728583e3dfb18dc34ce27fc9b7c1151c86e/docs/troubleshooting.md#L103
[remote-server]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/chrome/browser/devtools/remote_debugging_server.cc
[http-handler]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/content/browser/devtools/devtools_http_handler.cc
[connection-dialog]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/chrome/browser/devtools/devtools_connection_dialog.cc
[browser-context]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/chrome/browser/devtools/devtools_browser_context_manager.cc
[browser-api]: https://github.com/ChromeDevTools/devtools-protocol/blob/7245ec551bc9d450170193456ab3965bb7e716cb/pdl/domains/Browser.pdl
[target-api]: https://github.com/ChromeDevTools/devtools-protocol/blob/7245ec551bc9d450170193456ab3965bb7e716cb/pdl/domains/Target.pdl
[page-api]: https://github.com/ChromeDevTools/devtools-protocol/blob/7245ec551bc9d450170193456ab3965bb7e716cb/pdl/domains/Page.pdl
[chrome-target-handler]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/chrome/browser/devtools/protocol/target_handler.cc
[target-handler]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/content/browser/devtools/protocol/target_handler.cc
[hidden-manager]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/content/browser/devtools/protocol/hidden_target_manager.cc
[protocol-tests]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/chrome/browser/devtools/protocol/devtools_protocol_browsertest.cc
[page-handler]: https://github.com/chromium/chromium/blob/2ff94f6770e6ca118092da6092592116f6e97753/content/browser/devtools/protocol/page_handler.cc
[extension-debugger]: https://developer.chrome.com/docs/extensions/reference/api/debugger
[edge-extensions]: https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/api-support
[native-messaging]: https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
[edge-native]: https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging
[native-policy]: https://learn.microsoft.com/en-us/deployedge/microsoft-edge-browser-policies/nativemessagingblocklist
[web-storage]: https://html.spec.whatwg.org/multipage/webstorage.html
