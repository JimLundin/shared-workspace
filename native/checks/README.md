# Native host acceptance checks

These checks exercise the public host/Store interfaces already selected for the
workspace implementation. They never inspect Turso tables to infer a UI result.
The process harness drives the same commands and snapshots exposed to the native
shell, using separate process lifetimes, private state directories and real
Iroh loopback transport. No HTTP preview service is involved.

## Acceptance cases

| Case | Required observation | What it does not establish |
| --- | --- | --- |
| Selected-workspace pairing | A's invite admits B to the selected workspace; B's snapshot excludes A's unshared workspace and note. Reusing B's consumed invite from C is denied. | Enterprise device policy, physical QR scanning or general revocation. |
| Equal peers | A and B each create and edit shared material; exact identities and content converge after a real exchange. | A permanent leader, remote-application execution or a global transaction. |
| Local durability | Reopening the host with the same state directory preserves its device identity, workspace IDs, content, history and membership. | Recovery from storage corruption or an unverified filesystem implementation. |
| Offline divergence | B's endpoint is paused; A and B edit the same prior head independently; reconnect preserves both revision heads and bodies. | Automatic semantic merging of prose. |
| Reconciliation and stale input | An edit naming all observed heads creates one successor; a stale expected-head set cannot overwrite it. Prior alternatives remain in history. | External application's edit atomicity. |
| Replay and conflicting reuse | The same operation has one durable effect; exact replay is identified; changed content under its ID is rejected without changing the snapshot. | Exactly-once external effects. |
| Third member | B grants C the shared workspace while A is absent; C catches up and can contribute to the same signed history. | Arbitrary transitive trust between unpaired strangers. |
| Signature and membership | Public Store import rejects altered signatures and operations without valid causal workspace membership. | A complete cryptographic audit. |

Command errors, transport unavailability, process crashes and harness/setup
failures remain distinct from failed behavioral assertions. Pairing tickets,
private keys and native databases remain in temporary private directories and
are never included in public evidence.

## Targeted mutation checks

Mutations run only against a frozen passing source and harness.
Each campaign uses a disposable source copy and its own build target. The
workspace-core package is cleaned before every mutant build while dependency
artifacts remain reusable. Its
record includes source, test and binary hashes, exact mutation, command and the
intended assertion. Compiler errors, setup failures and timeouts are not kills.
The production checkout is never mutated. Baseline source must still match
its original digest after the campaign.

The target must be new or carry the campaign ownership marker. The harness
initializes Cargo's standard cache tag in that owned directory, then requires
a package clean before the baseline and every mutant build. Cleaning preserves
dependency artifacts while preventing a restored source from reusing a mutant.

The checked-in [process report](evidence/process.json) records 2 scenarios and
32 assertions. The [mutation report](evidence/mutations.json) records 9 passing
baseline Store tests and all 4 declared faults detected. Both reports bind the
same production source; the mutation report also hashes the process report.
Only these JSON reports are exported, without private state or raw logs.

The declared faults in [mutations.json](mutations.json) bypass signature
verification, causal author membership, imported operation payload checking,
and both independent stale-head protections. Each fault maps to a named
assertion in the public Store integration tests. The last mutation deliberately
removes both checks so a downstream defense cannot hide the exercised defect.

## Platform evidence

Linux process acceptance, Windows native process/app execution, iOS simulator
execution and installation on the physical iPhone are separate results. An
unsigned iOS artifact is not installable-device evidence. Apple signing is an
explicit remaining requirement until an actual signed installation is observed.

## Run

Build the native peer binary first, then run the process acceptance driver:

```sh
python3 -B native/checks/process_check.py \
  --binary /absolute/path/to/workspace-peer \
  --source native --evidence /tmp/workspace-process-evidence
```

Use `workspace-peer.exe` on Windows and Python 3.12 or newer. The evidence
directory must be new. A failed transport/setup step remains a failed run,
separate from an intended behavioral assertion.

After the root freezes a passing source and process report:

```sh
python3 -B native/checks/mutate.py --source native \
  --baseline-report /tmp/workspace-process-evidence/report.json \
  --evidence /tmp/workspace-mutation-evidence
```

The mutator verifies the process baseline's source hashes, runs the unmodified
Store tests in the disposable build, and then evaluates each declared fault.
It never treats a compilation error, zero-test run, timeout or unrelated panic
as a successful detection. Raw build/test logs remain outside the exported
source and reports.
