# Readiness and validation

The downloaded snapshot is preserved in the first Git commit. The subsequent
changes address the review's snippet corruption, clipboard failure, input
injection, unapproved code execution, persistent Undo, partial installation and
Git external-command findings.

Updates compile and pass runtime checks before installation. With the default
approval setting, a request to change Toolkit authorizes this workflow. Optional
approval mode displays the frozen candidate's full diff before executing it.
Python code runs with the user's account permissions. A content digest rejects
changes made during review or tests. Protected source aliases and escaping paths
are refused.

Recovery metadata is saved before changing live files. Failed installs restore
the snapshot; interrupted installations/trials restore on next launch. Completed
trials retain an Undo target across restarts. Cache clearing prevents stale
bytecode from hiding an installed change. Individual file replacements are atomic;
the durable recovery marker handles interruption between files.

Regression tests exercise native navigation, blocked clipboard writes, partial
input, pre-review execution, retained rollback state, interrupted trials, failed
installs/restores, source changes during review, and windowless launcher syntax.
Windows-only boundary tests mock input functions without sending real keystrokes.

GitHub Actions runs the full selfcheck once on Windows/Linux Python 3.15,
checks the extracted Windows bundle, and runs Mypy, Pyright and Ruff. Editing
uses `selfcheck.py --fast`; the keeper always uses the full Windows gate before
installation. The manual desktop workflow requires an interactive,
dedicated self-hosted Windows runner labelled toolkit-desktop. Do not run it on a
workstation someone is using.

The actual company endpoint, microphone, and physical ISO keyboard still require
a local smoke test. Full desktop end-to-end workflows have not been exercised on
the user's active desktop. Automated checks do not replace that final validation.
