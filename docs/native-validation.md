# Native workspace validation

The Linux/WSL independent-process run passed **2 scenarios and 32 behavioral
assertions** on 25 October 2026. This is real workspace replication between
native processes with separate Turso Database stores and authenticated Iroh
loopback connections. It is not an installed-iPhone or native-WebView result.

The [process report](../native/checks/evidence/process.json) records source,
lockfile, harness and executable hashes. Both source and executable remained
unchanged during the run; all eight recorded process lifetimes exited with
code zero. Its public CLI observations establish:

- Pairing transfers only the selected workspace and its note; another device
  cannot reuse the consumed invitation.
- Each paired device contributes work to the same workspace.
- Pausing one endpoint retains local work; reconnect preserves both concurrent
  revisions. Explicit reconciliation retains previous writing, while stale
  edits and changed payloads under an existing operation identity are rejected.
- Exact retries preserve one durable effect, including after process restart.
  Identity, resource content and revision history survive reopening.
- An existing member enrolls a third device while the origin device is stopped.
  The third device contributes; the origin catches up when restarted; all three
  discover one another and converge on the same revision identities.

The [acceptance harness](../native/checks/process_check.py) distinguishes failed
behavioral assertions from setup and transport failures. A peer exchange may
introduce another member contact after that pass selected its targets; the
harness allows at most three passes for those `never_synced` contacts. Unexpected
`unreachable` states still fail immediately. This corrects the initial harness
assumption that a single pass also contacts every newly discovered member.

The native core's **16 Rust tests** passed separately, including Turso `STRICT`
type rejection, signed-history authorization, local durability, and real Iroh
exchanges. Review regressions additionally establish that background recovery
does not reverse an explicit network pause, cumulative workspace grants span
multiple transfer batches, and local/received device names have the same byte
limit. Core and CLI Clippy checks passed across all targets with warnings denied.

The [native mutation report](../native/checks/evidence/mutations.json) passed:
**all 4 declared faults were detected by their intended behavioral assertions**.
The unmodified disposable build first passed all 9 public Store tests. Each
mutant then received a package-clean rebuild before its selected test ran.

| Injected fault | Required rejecting assertion |
| --- | --- |
| Skip operation signature verification | `reject-invalid-signature` |
| Skip causal author membership | `reject-ungranted-author` |
| Accept changed content under an existing operation identity | `reject-operation-id-payload-reuse` |
| Remove both independent stale-head protections | `reject-stale-heads` |

The report binds exact source/test hashes, changed source, executable hashes,
commands, cleanup-log hashes and observed assertions to the passing process
baseline. The production source remained unchanged. This is targeted mutation
evidence for these four invariants; it is not a whole-project mutation score.
Compiler failures, zero-test runs, timeouts and unrelated failures do not count
as detections.

The shared frontend separately passed TypeScript checking, **19 unit tests,
5 Playwright tests and 3 targeted mutation checks**, followed by its production
build. Those browser tests use the host contract through fixtures; they do not
establish synchronization inside a native Windows or iPhone shell. The generated
main assets are 0.48 kB HTML, 71.14 kB JavaScript and 12.73 kB CSS before gzip;
source maps are separate. These are frontend build sizes, not application bundle
size or device-memory measurements.

Windows and iOS build/installation evidence is tracked separately in
[native-platforms.md](native-platforms.md). GitHub Actions run 40195625077 was
blocked by account billing before any job steps ran; it produced no platform
artifacts. This is an execution blocker, not a compiler or test failure.

A local **Windows GNU release build passed** using LLVM-MinGW. The
[build record](../native/checks/evidence/windows-build.json) pins the executable,
loader and reviewed source hashes. Their combined size is 36,588,144 bytes;
the operating system supplies WebView2. This does not establish runtime memory
use or the separate MSVC/NSIS installer build.

The real Windows WebView create/save/restart check was **blocked before process
creation**. [Windows launch evidence](../native/checks/evidence/windows-launch.json)
records WinError 5 and Defender event 1121 for ASR rule
`01443614-CD74-433A-B99E-2ECDC07BFC25` (executable prevalence, age or trust).
The file ACL grants the user full access. No native readiness marker or app
data was created; no UI, restart, competing-launch or Windows peer assertion
passed. No policy change or alternative execution route was attempted.

The existing Python keyboard/audio/task owners are not modified by these
checks. The stopped phone HTTP preview is not used as a networking workaround.
Physical iPhone testing still requires a permitted signed application install.
No internet/NAT/relay, managed-network permission, physical voice recognition,
long-running iOS background execution or external application effect is claimed
by the loopback acceptance run. Raw logs, pairing tickets, private identities
and native database files are excluded from the checked-in evidence.
