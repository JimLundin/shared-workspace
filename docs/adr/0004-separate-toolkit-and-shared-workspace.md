---
status: accepted
---

# Separate the work environment from local desktop tools

Shared Workspace owns the distributed browser application and optional native
workspace hosts; Toolkit owns the Windows keyboard, dictation, desktop assistant
and local executor. They have independent repositories and releases, while the
existing website origin remains stable to preserve browser data and pairing.

The earlier combined-product migration proposals do not require replacing
Toolkit's working Python/Qt runtime. Any future connection is an explicit
capability adapter. See [project boundaries](../project-boundaries.md).
