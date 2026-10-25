# Two independently released projects

25 October 2026. Shared Workspace and Toolkit now have separate source trees and
repositories because they serve different purposes.

| Project | Owns | Delivery |
| --- | --- | --- |
| [Shared Workspace](https://github.com/JimLundin/shared-workspace) | Shared work and material, peer membership, Turso/Iroh hosts, voice-oriented interface, optional native workspace shells | Website/PWA first; separately built native experiments |
| [Toolkit](https://github.com/JimLundin/toolkit) | Windows keyboard layout, system dictation, desktop assistant, local execution and recovery, Python/Qt runtime | Its existing Windows source bundle and keeper |

Shared Workspace retains `browser/` and `native/`, their tests, dependency pins,
build workflows and relevant research. Toolkit retains the Python modules,
`assistant/`, `automation/`, `miryoku/`, `winapi/`, root tests, bundle tooling and
the evidence for those implementations. Each project has its own development
instructions, glossary and README. Historical implementation evidence lives with
the code it describes; references across projects are explicit links.

The existing Shared Workspace website address and manifest identity remain stable.
Repository separation does not move a browser's database, change invitations or
pair an installed app automatically. See [PWA installation](pwa-installation.md)
for the separate Safari/Home Screen storage boundary.

Toolkit retains its working Python/Qt implementation. The older proposals to
replace its runtime were made while these were one product; they do not direct
either project's current release. Shared Workspace does not depend on Toolkit's
Python packages, and its UI releases do not redeploy the keyboard or local assistant.
Python used by native process tests is a build/test tool, not that dependency.

The projects have no implemented remote execution connection today. A future
Toolkit adapter could offer an authorized capability to Shared Workspace through
versioned requests and results. It must carry the captured work address, operation
identity, permission scope and observed outcome. A shared database, copied source
tree or ambient desktop access is not that contract.

The user experience centers on [a piece of work](work-continuity-thesis.md).
Distribution supplies continuity and access to available capabilities. It does
not make a disconnected device safe to replace during an uncertain external action.

The combined source through `ac9e45c` remains in Git history. The split changes the
current trees and future releases; it does not erase the earlier development record.
