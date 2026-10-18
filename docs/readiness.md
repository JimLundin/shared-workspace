# Readiness and validation

The downloaded snapshot is preserved in the first Git commit. The subsequent
changes address the review's snippet corruption, clipboard failure, input
injection, unapproved code execution, persistent Undo, partial installation and
Git external-command findings.

Updates now compile without execution before review. The protected keeper freezes
the candidate, displays its full diff, and runs runtime tests only after approval.
Approved Python code runs with the user's account permissions; this is an explicit
execution approval, not a sandbox. A content digest rejects changes made during
review or tests. Protected source aliases and escaping paths are refused.

Recovery metadata is saved before changing live files. Failed installs restore
the snapshot; interrupted installations/trials restore on next launch. Completed
trials retain an Undo target across restarts. Cache clearing prevents stale
bytecode from hiding an installed change. Individual file replacements are atomic;
the durable recovery marker handles interruption between files.

Regression tests exercise native navigation, blocked clipboard writes, partial
input, pre-review execution, retained rollback state, interrupted trials, failed
installs/restores, source changes during review, and windowless launcher syntax.
Windows-only boundary tests mock input functions without sending real keystrokes.

GitHub Actions runs selfcheck and pytest on Windows/Linux Python 3.10 and 3.12,
plus Mypy, Pyright and Ruff. The manual desktop workflow requires an interactive,
dedicated self-hosted Windows runner labelled toolkit-desktop. Do not run it on a
workstation someone is using.

The actual company endpoint, microphone, and physical ISO keyboard still require
a local smoke test. Full desktop end-to-end workflows have not been exercised on
the user's active desktop. Automated checks do not replace that final validation.
