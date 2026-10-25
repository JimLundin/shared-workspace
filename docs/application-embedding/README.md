# Public app embedding checks

Checked **2026-10-25** in Linux Chromium **156.0.8078.4** and Linux WebKit **27.2**, using Playwright **1.64.1** in `mcr.microsoft.com/playwright:v1.64.1-noble`.

**VS Code and the tested public Word/Excel entry routes need an external tab. Draw.io has a verified interactive credentialless embed in headed Chromium; it needs a fallback in engines without that feature.** This is evidence for a future Draw.io adapter, not a request to add an XML/preview surface to the current release. The probe does not modify the application or deploy it.

| Public surface | Concrete framing observation | Under Toolkit's current isolation headers |
| --- | --- | --- |
| `https://vscode.dev/` | HTTP 200; CSP `frame-ancestors 'none'`. The page's own COOP/COEP do not waive that rule. | Blocked in ordinary and credentialless Chromium frames and in the tested WebKit frames. The workspace shell loaded as a top-level page. |
| `https://www.microsoft365.com/launch/word` and `https://word.cloud.microsoft/` | Redirect to `https://word.cloud.microsoft/en-us/`; public page returns XFO `DENY` and CSP `frame-ancestors 'self' data: https://support.office.com`. | Blocked in tested frames. Public landing page loaded top-level; no authenticated document was opened. |
| `https://www.microsoft365.com/launch/excel` and `https://excel.cloud.microsoft/` | Redirect to `https://excel.cloud.microsoft/en-us/`; same XFO and framing CSP as Word. | Blocked in tested frames. Public landing page loaded top-level; no authenticated workbook was opened. |
| Draw.io supported embed | Headed Chromium received HTTP 200, with no XFO or CSP `frame-ancestors` denial and no response COEP/CORP. | Ordinary iframe blocked. Credentialless iframe completed real editing, save, export, and fresh-context restoration. Linux WebKit lacked the credentialless property and blocked the frame. |

The working Draw.io URL, matching its official integration example's parameters, is:

```text
https://embed.diagrams.net/?proto=json&spin=1&ui=min&libraries=1
```

The exact loopback fixture response headers were copied from `browser/app/scripts/worker-template.js`:

```http
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
```

The fixture used a random port on `127.0.0.1` inside Docker, without publishing a port or proxying third-party content. Each case used a fresh Playwright context; no authenticated profiles, storage-state imports, request/header rewrites, certificate bypass, or disabled web-security flags were used. The checked header boundary retained `crossOriginIsolated === true`, `SharedArrayBuffer`, and the OPFS API before and after iframe loading. This is not a production Turso/OPFS persistence test.

**Concrete Draw.io interaction and recovery**

1. Wait for the vendor frame's supported `init` event, checking both `event.source === iframe.contentWindow` and exact origin `https://embed.diagrams.net`.
2. Send `load` with a controlled diagram containing a rectangle labelled `Toolkit embed before`.
3. Double-click the actual diagram label, edit the app's contenteditable element to `Toolkit embed after`, finish with Control+Enter, and send Control+S through the app.
4. Receive `autosave` and `save` events containing the changed XML. The checked save receipt is **633 bytes**. Save it to the scratch directory, independently of the iframe's storage.
5. Request `export` with format `xmlsvg`. The received SVG was decoded and its visible edited text checked.
6. Close the context. Read the saved XML from disk, create a fresh context, initialize another real Draw.io frame, and load that XML. The edited label is visible again, with the parent still isolated.

The standalone probe also bounds message size, accepts only known protocol events, checks XML/export field types, and avoids reloading on duplicate `init` events. These guards illustrate the narrow supported bridge; the probe does not introduce a production Toolkit adapter or a privileged generic JavaScript interface.

**Browser and server limits observed**

Headless Chromium returned **HTTP 403** for the tested Draw.io URL variants, including the official URL as a top-level control. Headed Chromium, with default browser headers/security and a fresh context, returned **HTTP 200** and completed the interaction. The results establish that difference in this environment; they do not establish its server-side cause. The positive result was then reproduced using the retained standalone probe.

An iframe `load` event also fired for blocked/blank documents. It must not be treated as application readiness. A future Draw.io adapter should feature-detect credentialless support and require the validated `init`/`load` handshake, with a visible fallback on failure. Credentialless does not bypass XFO or CSP. The tested WebKit is a Linux build, **not a physical iPhone, Safari, or native WKWebView**. Windows WebView2 and authenticated Office editing/account reuse were not tested. Office's separate read-only sharing embeds are not classified as live editing surfaces by these checks.

**Files to retain**

- `probe-drawio.cjs`: the standalone, successfully rerun headed Chromium probe. It serves only a loopback fixture, uses the fixed public Draw.io URL, compares ordinary/credentialless loading, edits, saves, exports, and restores in a fresh context.
- `results.redacted.json`: selected framing headers, the browser matrix, public top-level controls, message sequence, save/export hashes, and explicit limitations. It contains no cookies, authorization headers, browser profiles, HAR, or private document data.
- This `README.md`.

The controlled `probe-chromium-drawio-credentialless-after.xml`, `drawio-edited-export.svg`, and public-fixture restore screenshot are optional supporting artifacts. They are not needed to retain browser state. The other screenshots and broad diagnostic runs can remain temporary.

**Tested reproduction command**

The dependency mount is read-only and contains the checkout's existing matching Playwright package. Only the scratch evidence directory is writable. `--init` lets the Xvfb wrapper receive its normal readiness signal instead of running as container PID 1.

```sh
docker run --rm --init --user 1000:1000 --shm-size=1g \
  --env NODE_PATH=/deps \
  --mount type=bind,src=/home/jim/toolkit-hosts/browser/app/node_modules,dst=/deps,readonly \
  --mount type=bind,src=/home/jim/toolkit-hosts/docs/application-embedding,dst=/probe,readonly \
  --mount type=bind,src=/tmp/toolkit-app-embed-checks-20261025,dst=/evidence \
  --workdir /evidence \
  mcr.microsoft.com/playwright:v1.64.1-noble \
  xvfb-run -a node /probe/probe-drawio.cjs
```

The retained probe completed with exit code **0**. It writes `probe-drawio-results.json`, controlled XML/export data, and public-fixture screenshots into the supplied scratch mount. No image or dependency installation was needed in this investigation.

Primary integration references: [Draw.io integration README](https://github.com/jgraph/drawio-integration/blob/master/README.md), [official editor wrapper and message protocol example](https://github.com/jgraph/drawio-integration/blob/master/examples/embed-mode/diagram-editor.js). The official README makes the host responsible for persistence, authentication, revisions, and file descriptors; communication with the editor is client-side. The measured URL/response and browser evidence is in the redacted JSON.
