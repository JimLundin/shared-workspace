# Native workspace validation

Status: acceptance plan recorded; implementation and first runs are in progress.
No new test pass, native package or physical-phone execution is claimed yet.

The [acceptance plan](../native/checks/README.md) identifies the agreed public
command/snapshot/import and authenticated peer interfaces. The first process
run will use independent Turso Database stores and Iroh endpoints. It must
establish selected-workspace pairing, symmetric edits, durable restart,
retained offline conflicts and identity-safe retry through those interfaces.

Final reports will pin source, harness, dependency lockfile and executable
hashes. Targeted mutations must fail their intended behavioral assertion;
compilation, setup, transport timeout and unrelated failures remain separate.

The existing Python keyboard/audio/task owners are not modified by these
checks. The stopped phone HTTP preview is not used as a networking workaround.
Physical iPhone testing still requires a permitted signed application install.
