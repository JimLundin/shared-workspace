# Runtime direction review

Reviewed **2026-10-24** against the current architecture notes, preserved native
receipts and pinned Pi 1.4.0 source. This supports the proposed
[shared-workspace decision](adr/0002-shared-agent-workspace.md). No new browser
or native feasibility trial ran for this review.

**Proceed with a TypeScript product core, Pi Durable in a trusted WebView2
runtime, and a small compiled Win32 host using the official WebView2 loader/COM
interfaces.** Continue the existing native host seam and C++20 packaging path,
subject to an accepted execution route. The final Toolkit distribution should
contain native code and web assets, without bundled Python, Node, Qt or a private
browser engine. The accepted Python host remains an intentional transition.

TypeScript and WebView2 are complementary: TypeScript produces JavaScript;
WebView2 executes it and renders the UI. Type annotations do not validate
incoming JSON at runtime. Preserve discriminated domain variants, pure state
transitions, explicit effect owners and validation at native, storage and
provider boundaries. Build tools may use Node without shipping it.
[TypeScript][ts], [current domain contracts](functional-design.md)

| Owner | Recommended responsibility |
| --- | --- |
| TypeScript product core | Domain decisions, canvas/document presentation, task/source/approval projections, validated message contracts and agent-runtime integration. |
| One trusted Pi runtime | Initially run Pi in a dedicated trusted **page**, independently of canvas visibility/navigation, because that is the Windows-proven placement. Use a stable HTTPS origin/profile, one Harness/store owner and a lifetime Web Lock. A worker is an optional later placement behind the same contracts; its Windows relay is not yet proved. [Local content][local], [Pi storage][storage] |
| Native Windows layer | Own WebView2 controllers, clipping/input, process supervision, acknowledged wake scheduling, provider credential use/I/O, filesystem/desktop effects and their receipts. Preserve the isolated keyboard owner and native audio loops. A small host does not mean one process: WebView2 has browser/renderers/helpers, and input isolation remains necessary. [Threading][threading], [processes][processes] |

**What the evidence actually establishes**

| Evidence | Established | Limit |
| --- | --- | --- |
| Portable Pi 1.4.0 + IndexedDB | Three Chromium scenarios passed: page interruption, worker replacement and active graceful close. The exercised graph runs without Node globals; interrupted unsafe tools were not automatically replayed. | Scripted provider and simulated effect; not universal provider, OAuth or Windows-worker compatibility. |
| Windows Pi in accepted Python/WebView2 | Both preserved scenarios passed, exit 0, on Python 3.15.1 / WebView2 155.0.4283.62. The first host performed one native scratch-file effect; the fresh host consumed the matching recovery receipt, invoked zero scratch effects, and left the counter at 1. | Pi ran in the trusted **page**. Host teardown/reopen is not a power-loss proof or exactly-once guarantee. The live canvas/backend, real provider and production journal were separate. |
| Native canvas in the same host family | Thirteen checks passed: independent app controllers, pointer/Unicode input, zoom, disjoint clipping, modal/focus routing and retained pages across workspaces at 125% scale. | This proves the windowed-controller approach. It does not prove every DPI/input/close case, all Qt replacements, or a general CSS compositor for native views. |
| Compiled C++ host | A 408,064-byte executable and 194,912-byte loader were built; recorded prototype payload was 1,611,847 uncompressed bytes. | The executable never started. The recorded ASR event 1121 identifies the prevalence/age/trust rule; the preserved failure receipt has no runs. Build size is not runnable-product size. [ASR rule][asr] |
| Existing package inventory | The recorded 313.6 MB ZIP has about 77% Qt/PySide6/WebEngine and 13% Python compressed contents. | A concrete distribution opportunity, not a measured RAM/CPU reduction or a prediction of final savings. |

Exact evidence and scope are retained in [runtime footprint](runtime-footprint.md)
and [the UI investigation](webview2-ui-investigation.md). This review reread the
preserved Pi `launch-summary.json` and verified SHA-256
`8fc97b85f20f15bea58af34a49d3e77853a0a55d7455a754f518521c9c624b8b`,
both exit codes, counter values, zero recovery-side scratch invocations and
matching run/task/call/effect identities. It also reread the 13 canvas results
and the compiled host's failed launch receipt. No receipt is a new execution.

**Provider and lifetime work are real integration requirements.**

Pi AI's pinned browser documentation excludes Bedrock and says OAuth **login
flows** are Node-only. This does not imply every OAuth refresh path is Node-only,
but the required provider/auth-method inventory must be explicit. The scripted
trial's `authContext` does not settle HTTP, CORS, credentials, streaming or
refresh behavior. [Pi browser support][pi-browser]

Implement a **provider-specific native authentication and transport adapter**
behind an owned `Models`/provider boundary, covering generation, compaction,
hooks and tools. Pi exposes `HarnessOptions.models` for that integration.
Per-request `fetch` injection exists, but adapters may reject it and it does not
cover WebSocket transports; it is not a persisted conversation setting or a
universal transport replacement. Keep credential use/signing native if secrets
are to remain native: a store that returns raw keys/tokens to renderer-side
Models does not provide that boundary. Validate destinations and preserve ordered
streaming, bounded buffering, cancellation and terminal errors. Direct-browser
requests would still require provider-specific CORS/auth validation; disabling
browser protections is not the integration. [Models seam][models],
[request transport][fetch], [credential contract][credentials]

Native code must acknowledge a recovery wake before admitting an operation
that can start work; Pi's `onWake` runs after commit, so an unacknowledged later
message can leave work stranded after a crash. `wake()` reads the requirement;
`resume()` delivers progress. This does not replace every provider/progress
timer. Hiding can throttle a view, suspension pauses timers, and a dedicated
worker has no independent daemon lifetime. Keep active runtime work unsuspended;
supervise restart and fence old generations. Hold the Web Lock through shutdown.
Harness close is cooperative, and controller `Close()` skips
`beforeunload`; neither a timeout nor a missing reply proves an external effect
did not occur. [Wake protocol][wake], [wake example][wake-example],
[Pi timers][timers], [worker lifetime][workers], [suspension][suspend],
[Harness close][pi-close], [controller close][close]

Observed IndexedDB reopening does not establish protection from profile
deletion, eviction or disk failure. The portable trial reported
`persisted() === false`; define retention and export/recovery behavior before
transferring the authoritative journal. [Pi storage][storage]

**Go/no-go gates for replacement**

| Gate | Required evidence before proceeding | Current position |
| --- | --- | --- |
| Accepted compiled distribution | The intended compiled artifact must run through the required host scenarios on the target managed Windows setup. Reuse the existing conformance cases once an accepted build/distribution route exists; an ASR denial stops this cutover. | Open. Python proves functionality, not acceptance of the compiled executable. |
| Real provider path | One required provider **and auth method** must stream successfully through the chosen adapter, handle refresh/failure/cancellation, and honor the stated credential boundary without shipped Node. Verify every transport actually used, including WebSockets if applicable. | Open. Scripted model only. |
| Runtime lifecycle | Acknowledged wake admission, exclusive ownership, stale-owner reply rejection, hidden/idle behavior, shutdown and abrupt-loss recovery must preserve progress and uncertain-effect state. Prove a Windows worker relay only if adopting it. | Partial. Page/native reopen passed; the complete background-service contract has not. |
| Journal and effect migration | Define a versioned transfer and recovery/export policy; preserve task IDs, run fences, captured-source fingerprints, questions, exact approval binding, action receipts, cancellation and verified/uncertain outcomes. Demonstrate single scheduling ownership and a rollback that cannot replay already performed effects. | Open. Keep the existing SQLite journal authoritative until this gate passes; Pi records are not a drop-in replacement for Toolkit's domain contracts. |
| Desktop and canvas parity | Port the existing native geometry contract, then prove required DPI, focus, modal, popup, permission/download, unsaved-close and stable-document behavior. Replace remaining Qt screens; benchmark keyboard latency/allocations/GC and audio buffer handling against the current isolated owners. | Partial. Native app views work; production host ownership, document adoption and several workflows remain separate work. |
| Complete footprint and release | Audit all shipped/runtime-started dependencies; measure the complete host/browser/input process groups under comparable 0/1/3-app workloads, including startup, CPU, private bytes, working set and cleanup. Include shared-runtime-absent setup and set acceptance budgets before claiming improvement. | Open. No comparable full-product resource result or complete Python/Qt removal yet. |

Retain the current windowed native views and geometry publisher for the first
production adapter. CSS stacking alone cannot order independent native views;
changing to a composition controller would add its own input and clipping work.
Keep trusted runtime data/privileges separate from remote app profiles. Sharing
Evergreen binaries does not eliminate per-document execution or renderer memory.
[UI ownership and compositing](webview2-ui-investigation.md),
[embedding security][security], [Evergreen distribution][distribution]

The next implementation work is the shared `WorkspaceControl` interface through
the existing Python owner: connect the continuous conversation and canvas to
the same resource, task and outcome projections, as proposed in
[ADR 0002](adr/0002-shared-agent-workspace.md). Prove that integration before
porting the interface into TypeScript and closing the accepted-host and
real-provider gates. Basic browser/Pi feasibility does not need another broad
prototype. Production runtime removal remains a no-go until the relevant
ownership, compatibility and release gates pass. Keep the current journal
authoritative throughout the initial integration.

[ts]: https://www.typescriptlang.org/docs/handbook/typescript-from-scratch.html#erased-types
[local]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/working-with-local-content
[storage]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/README.md#L769
[threading]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model
[processes]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model
[asr]: https://learn.microsoft.com/en-us/defender-endpoint/attack-surface-reduction-rules-reference#block-executable-files-from-running-unless-they-meet-a-prevalence-age-or-trusted-list-criterion
[pi-browser]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/ai/README.md#browser-usage
[models]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/types.ts#L609
[fetch]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/ai/src/types.ts#L159
[credentials]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/ai/src/auth/types.ts#L17
[wake]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/harness/types.ts#L648
[wake-example]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/README.md#L164
[timers]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/src/timers.ts#L12
[workers]: https://html.spec.whatwg.org/multipage/workers.html#the-worker's-lifetime
[pi-close]: https://github.com/earendil-works/pi/blob/4a080252f06caf63a525d7873c5d94d3c23887e0/packages/durable/README.md#L827
[suspend]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2_3#trysuspend
[close]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller#close
[security]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security
[distribution]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
