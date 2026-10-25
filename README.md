# Toolkit

A personal toolkit for a Windows laptop: extract and run, with its own Python
and no admin installation. It changes itself: ask its agent for a new feature or a fix,
and it edits, tests, installs and reloads the change without another approval.
It undoes the update by itself if it fails. Four main processes:

- **the layout** (`miryoku/`): Colemak Mod-DH in the Miryoku style on the
  laptop's own ISO keyboard: home row mods, layers, caps word, snippets;
- **the assistant** (`assistant/`): dictation, rewriting, questions, screen
  reading, app switching, window placement, and an agent, all through your
  company's OpenAI-style endpoint, with tray controls and a shared desktop canvas;
- **the automation worker** (`automation/`): runs durable tasks, standing
  instructions, and recovery independently of the conversation window;
- **the keeper** (`keeper.py`): runs the other three, restarts them if they crash,
  and installs the agent's changes to them (see *Changing the toolkit*).

The agent uses **OpenAI Agents SDK 0.24.0**. Every interface uses **Qt 6.12**:
the desktop canvas, launcher, conversation bubbles, History, Work, prompts,
keyboard overlays, and keeper dialogs. The assistant has one Qt event loop;
the keeper owns its own protected Qt UI. Tkinter and Tcl/Tk are excluded from
the bundled interpreter. The keyboard process uses only the standard library
and our typed Win32 bindings (`winapi/`), independently of the assistant.
The [Qt implementation notes](docs/qt-interface.md) include native canvas captures
and the migration checks.

## Start

1. Extract **`toolkit-sdk-python315-win64.zip`** into a folder you can write to.
   It includes **Windows x64 Python 3.15.1** in `.python`, with
   matching agent and Qt dependencies in `.runtime`. Keep both folders beside
   `StartToolkit.cmd` and `toolkit.pyw`. Python, Qt and the dependencies require
   no installer or separate downloads on the laptop.
   Set the Windows keyboard layout to **English (US)**.
2. Set these environment variables (Start → "Edit environment variables for your
   account"; no admin needed):

   | Variable | |
   |---|---|
   | `OPENAI_BASE_URL` | your endpoint, ending in `/v1` |
   | `OPENAI_API_KEY` | its key |
   | `TOOLKIT_CHAT_MODEL` | optional; default `gpt-5.4-mini` (quick jobs) |
   | `TOOLKIT_AGENT_MODEL` | optional; default `gpt-6.1-sol` (agent tasks and self-modification) |
   | `TOOLKIT_AGENT_REASONING_EFFORT` | optional; default `low` for quicker replies; use an effort supported by your selected model |
   | `TOOLKIT_AGENT_APPROVALS` | optional; default `never`; set `ask` to opt in to action and update approvals |
   | `TOOLKIT_AGENT_CONTEXT` | optional; default `recent`; set `off` to disable idle activity tracking and automatic request context |
   | `TOOLKIT_GRAPH_CLIENT_ID` | optional; approved Entra app ID for Teams; can also be saved through the agent |
   | `TOOLKIT_GRAPH_TENANT_ID` | optional; the matching Entra tenant ID |
   | `TOOLKIT_TRANSCRIBE_MODEL` | optional; default `gpt-4o-transcribe` |
   | `TOOLKIT_VOCABULARY` | optional; names and jargon to expect in dictation |
   | `TOOLKIT_LANGUAGE` | optional; `sv` or `en`, default detected |

   Without the first two the layout, snippets, apps and window keys still work;
   the AI keys tell you what's missing.
3. Turn on Settings → Privacy & security → Microphone → **Let desktop apps access
   your microphone**.
4. Double-click **`StartToolkit.cmd`**. A dot appears in the tray. To start it at login,
   put a shortcut to it in `shell:startup` (Win+R).

Existing `toolkit.pyw` shortcuts still work when Windows has a Python file
association: the launcher hands off to the bundled interpreter. The bundle
does not change the system Python installation or file associations.
`.python\pythonw.exe miryoku.pyw` runs the layout alone, without the assistant
or keeper.

## What you see

- **The tray dot**: green: running, layout on · grey: layout off or not running ·
  red: listening · amber: working or waiting for your reply · blue: the agent is on a task. Hover for words;
  click for the menu. Actions with hotkeys show them alongside their names, using
  physical keycaps: **Hold V + P** means hold V (Tools), then tap P to ask.
  (Windows 11 may tuck it behind the ^ at first: drag it out.)
- **Layout: on/off** (tray menu): the status is also a toggle. Click it or tap
  **§** to switch; a checkmark means the layout is on. If the layout has quit,
  choose **Start layout** first.
- **Keyboard overlay**: a faded, click-through key map near the bottom of the
  main screen. It stays visible while the layout is on and follows held or
  locked layers. Small labels identify the printed keycaps; large labels show
  their new outputs, with hold actions below. Keys glow gold while you press them,
  including every key held in a chord; quick taps leave a brief flash.
  Hide or show it with **Keyboard
  overlay** in the tray menu. Use the **Drag to move** handle above it to place it
  on any monitor; the keys remain click-through. Your position and visibility
  choice are remembered across restarts.
- **The layer indicator**, at the bottom of the screen: the layer you're holding or
  have locked (`NAV`, `NUM · locked`), and `CAPS WORD`. It flashes *Layout on/off*
  when you tap §. It can't be clicked and never takes the keyboard.
- **Key map** (tray menu): your keyboard drawn as each layer has it. Keys 1–6 or
  the buttons switch layer; the thumb holding the layer is highlighted.
- Sounds for layout on/off and caps word; a warning sound and a notification
  when something fails (with the reason).

## The keys

Physical keys are named by their Nordic keycaps.

| | |
|---|---|
| **§** | tap: layout on/off · hold 2 s: quit the layout |
| **B** | hold and speak: dictation, typed where the cursor is |
| **N** (next to B) | hold and speak an agent task; release to send it |
| thumbs **X C V** | Esc · Space/**Nav** · Tab/**Tools** |
| thumbs **M , .** | Enter/**Sym** · Backspace/**Num** · Delete/**Apps** |

The dictation microphone is opened when Toolkit starts and kept ready between
captures, so you can speak as soon as you press a talk key. Dictation records only
while the key is held, and the microphone is released when Toolkit quits. On release,
Toolkit waits for Windows to return the complete recording before transcribing
it. A failed or incomplete buffer is reported instead of sending partial audio.

**Tools** (hold the Tab thumb, then the right hand):

| | | | | | |
|---|---|---|---|---|---|
| top **8 9 0 + ´** | rewrite by voice (hold, say how) | fix spelling | translate sv↔en | shorter | more formal |
| home **I O P Å ¨** | dictate, tidied (hold) | dictate a command (hold) | ask… | copy text on screen | ask about the screen |
| bottom **K L Ö** | agent, by voice (hold) | agent, typed | stop the agent | | |
| thumb **M** | dictate (hold), as on B | | | | |

Rewrites replace the selected text. In terminals they don't press Ctrl+C (it
would stop the running program), so they don't work there.

**Apps** (hold the Delete thumb, then the left hand):

| | | | | | |
|---|---|---|---|---|---|
| top **2 3 4 5 6** | terminal | browser | Outlook | Teams | Explorer |
| home **Q W E R T** | VS Code | Notepad | Excel | Word | PowerPoint |
| bottom **Caps A S D F** | snap left | maximize/restore | snap right | next monitor | minimize |

An app key brings the app forward, starts it if it isn't running, and pressed
again goes through its windows. Change the apps in `config.py`.

**Snippets**: type the trigger and it's replaced: `,,date` `,,time` `,,now`
`,,week` `,,shrug` `,,thx` `,,mvh` `,,br`. Add your own in `config.py`. Physical navigation, window changes, pointer movement
and injected toolkit text reset a partially typed trigger.

## Meeting transcripts

Choose **Record a meeting** from the tray or the agent timeline's **⋯** menu,
or tell the agent to start a meeting transcript. Recording continues until you
choose **Stop**, tell the agent to stop recording, or quit Toolkit. A red tray
dot and a small elapsed-time bar show when capture is active. Other agent tasks
and the B/N voice keys keep working during a meeting.

The default records the **Windows default microphone and default playback
device** together, so it includes remote participants heard through that playback
device. For a room-only recording, choose **Record microphone only** in **⋯**.
An app using a different output device needs to use the Windows default output
to be included. Headphones prevent computer playback from also echoing through
the microphone. Speaker names are not inferred.

Timestamped text appears in the existing timeline in roughly 20–30 second
chunks, as requests finish. The timeline keeps a short recent preview; click the
recording bar or **Open** for the complete Markdown transcript. **Transcripts**
opens `%LOCALAPPDATA%\toolkit\meetings`; each meeting has its own dated folder.
There is no five-minute limit or fixed meeting duration.
Brief microphone dropouts leave a gap at their original time; recording and
computer-audio capture continue from the next valid packet.

Only audio awaiting transcription is kept locally, in a recovery journal.
Successful chunks are replaced by text and their audio storage is erased and
reclaimed. Audio capture and network requests run independently. A connection
failure retries while capture continues; **Retry** resumes a paused transcript.
The pending-audio limit is 256 MiB (about 2 hours 20 minutes of untranscribed
speech). Reaching it stops capture visibly and retains the saved part instead of
using unlimited memory or disk space. Device errors also stop capture visibly.

Stop saves the final partial chunk and lets transcription finish. On quit, saved
pending audio is retained for transcription after the next launch. A restart
never starts the microphone or playback recording automatically. A crash can
lose the most recent audio still in memory; committed text and
audio recover from the journal, including if a transcript-file write was interrupted.

This uses Windows WASAPI and the existing configured transcription endpoint;
there are no new dependencies. Chunk sizing and previous-chunk context follow
the [official OpenAI speech-to-text documentation](https://developers.openai.com/api/docs/guides/speech-to-text).

Choose **Meeting follow-up…** from the tray, or **Meetings** in Work, to select
completed recording segments and prepare sourced notes, actions and email drafts.
The Qt editors retain unfinished writing across background updates, link claims
to the exact saved transcript lines, and can put the notes on the shared canvas.
See [meeting follow-through](docs/meeting-followup.md) for source selection,
saved versions, editing and Outlook handoff.

## The agent

Hold **N**, beside B, to speak a task and release it to send. This works on every
layer. The agent appears as up to five narrow smoked-glass notification bubbles at the
bottom-right of the screen. New bubbles move earlier ones upward; the desktop
between them remains visible and clickable. There is no standing toolbar or
message field, and notifications never take keyboard focus.
Windows supplies the background blur, rounded glass edges and soft shadows;
text remains opaque. Your requests have a subtle blue tint; assistant bubbles
use lilac. Uniform dark and light backgrounds keep the glass highly transparent,
with light or dark text to match. Mixed backgrounds use a stronger tint for
legibility. Captions stay inside the glass, with the same adaptive contrast.
Disabling Windows transparency uses solid surfaces; high-contrast mode uses the
system's foreground and background colors.

The current conversation stays visible while the task runs. Your request comes
first, followed by one assistant bubble with a compact **Thinking** heading and
an **×** stop button inside it. Click the heading to expand the steps in order.
Collapsing hides only those steps: the latest reply or question stays attached
inside the same bubble, including after the task finishes. The stop icon shares
the surrounding glass, including when pressed. Meeting recording has its own control.
Replies stream with Markdown, a brief reveal for new text, and smooth bubble
growth: short text expands sideways up to the width limit, then wraps downward.
Text and glass move together. Long replies retain all their text and scroll
upward automatically at a reading pace after a short pause. Overflowing replies
take turns; each stays until its ending has been shown. Hover to pause scrolling
and expiry; the mouse wheel lets you move through the text, including over the
glass background. Leaving resumes after a short pause. Short replies, and the
end of long replies, stay for another 12–30 seconds after the task finishes,
then fade upward and disappear. Reduced motion advances overlapping pages.
The stack disappears completely when idle. **Hold V + Ö** also stops the agent and
clears queued follow-ups. The recording bubble's **×** ends only the recording.

Each follow-up request starts its own turn. Replies, questions, voice answers,
and tool activities retain their history within that turn. Thinking appears as a small spinner,
without a stream of reasoning text; reduced motion keeps the indicator static. Public reasoning
summaries remain available in the full history; private reasoning is not shown.
Actions show readable targets and result summaries instead of JSON payloads.
Expanded steps and the attached reply scroll independently, with a small
scroll indicator when their content overflows.
Questions stay visible until answered or cancelled: hold **N** to speak your
reply. If you opt in to approval mode, say exactly **approve** or **decline**
with the same hotkey. Other speech does not decide an approval. When it is
working without a pending question, another voice request queues a follow-up.

**Hold V + L** or **Agent session** in the tray opens the complete, selectable
timeline, including expired bubbles, tool results and interrupted replies.
Its **⋯** menu contains **Write a message…**, **Show activity**, **New chat**,
and recording controls. **Enter** sends a typed message; **Shift+Enter** adds a
line. Closing history returns to any active notifications. Voice and typed
requests share the same conversation until **New chat** or Toolkit exits.

The history window can still be moved, resized, snapped to either screen edge,
or given a transparent background from its menu. Notifications use its remembered
monitor and always stack in the right corner. Each bubble fits its content up to
360 logical pixels wide (280 for your requests), keeping its right edge anchored as it grows. Longer
entries share the available height, with space reserved for the
voice-reply hint. A small scroll marker shows progress through overflowing text.

The SDK loads in the background and the notification view is prepared on the
existing UI thread after Toolkit starts. History widgets are created when you
open it. The source workspace is prepared only when a source tool is used.
Failed requests show the reason in a bubble and the timeline;
they also notify you and are recorded in
`assistant.log`. Agent tasks use **GPT-6.1 Sol with low reasoning effort** for
quicker replies, independently of the fast model used for dictation and quick questions. The
endpoint must support the selected model through the Responses API.

By default, file writes, commands, opening files, desktop control and self-updates
run without approval prompts. Requests to change Toolkit authorize the agent to
edit its source and model settings, test the result, install it and reload.
The agent is instructed to publish or send messages only when you request that.
The **×** stop button remains available throughout the task.

The agent can work across desktop applications: inspect accessible controls,
read their text, fill fields, invoke buttons, select list items, toggle options,
expand menus and set sliders. It can also hover, click, drag with modifiers,
scroll in either direction, paste text, use keyboard shortcuts, and move,
resize, snap, minimize, maximize or close windows. Screenshots can cover a
window, monitor, region or the whole desktop. PowerShell supports working
directories and timeouts; Stop terminates its running command and child
processes. The agent can use installed applications' APIs and COM interfaces
through PowerShell when those suit the task.

For web tasks, it defaults to **your running, signed-in Edge**, and can attach to
Chrome on request. Enable **Allow remote debugging for this browser instance** at
`edge://inspect/#remote-debugging` (or `chrome://inspect/#remote-debugging`), then
approve the browser's connection dialog. Toolkit keeps that connection for
subsequent work; the browser may ask again after a disconnect or restart.
New Edge tabs open in a **Toolkit** group, created collapsed so the agent can
work while you browse. If you expand the group to collaborate, it stays expanded.
The agent can open and close tabs, group and ungroup them, rename and collapse
groups, move tabs or groups between windows, pin tabs and reload them. Ask it to
show a page when you want that tab brought forward. It can also work on your
existing tabs when requested, read page text and controls, fill forms, handle
frames and open shadow roots, and capture browser screenshots. Stop and Toolkit
exit disconnect, leaving your browser, tabs and groups open.

This reuses the running browser's session; it is not a second headless process
sharing an open profile. The group's tabs remain accessible in the tab strip.
Chrome retains the page tools; native tab/group management currently uses Edge.
The separate headless Edge profile is still available when explicitly selected.
See [browser session reuse](docs/browser-session-reuse.md) for supported setup
and the browser's consent requirements.

The **Teams API adapter is ready to configure later**. Once an approved Microsoft
Entra public application's client and tenant IDs are available, ask the agent to
configure Teams and sign in. It can then find recipients, list chats, get or
create a one-to-one chat and send a requested plain-text message. Sign-in uses
Microsoft's device flow, and saved tokens are encrypted for your Windows account.
See [background automation and Teams setup](docs/background-automation.md) for
permissions, tool details and current limitations.

While idle, Toolkit keeps recent app and window-title changes in memory: up to
128 changes over the last 30 minutes. Each request receives recent activity and
a screenshot of the last work window when the request starts. This helps it
understand "this window" and "what I was just doing". Opening Toolkit's timeline
does not replace that work window. Queued requests retain their own invocation
context; their screenshot is taken when they start running. Activity while the
agent is working is marked so it can distinguish its own actions.

The idle tracker collects no screenshots, keystrokes or clipboard history, and
makes no model calls. Request context goes to your configured endpoint and stays
in the conversation until **New chat** or exit; only the latest two screenshots
are retained. The idle activity buffer expires separately. Set
`TOOLKIT_AGENT_CONTEXT=off` and restart to turn off this automatic context.

Desktop automation uses the permissions of your Windows account. A locked
desktop or an elevated application can block screen capture and input. Some
applications expose few accessibility controls; the agent can use screenshots,
input or an application API instead. It checks results and reports an actual
blocker when Windows refuses access.

With `TOOLKIT_AGENT_APPROVALS=ask`, writes and opens ask each time, commands ask
unless they match `ALLOWED_COMMANDS` in `config.py`, and desktop control asks once
per task. The keeper also reviews self-updates in that optional mode.

It stops after 80 steps (`AGENT_MAX_STEPS`). The journal records tool names and
success status. Browser and Teams arguments and results stay out of persistent
journals and activity details, including form values and sign-in codes.

## Durable work

The [shared desktop canvas](docs/desktop-canvas.md) attaches requests, selected
sources, and results to these same durable task identities. Saved cards reconnect
after a restart, including tasks outside the recent-work list. An unconfirmed
submission stays attached and can be retried with its original identity.

Open **Work…** from the tray or conversation menu. Voice requests, typed
requests, and standing instructions use the same saved task journal. The Work
window has three tabs:

- **Tasks** shows instructions, progress, questions, results, and verification
  evidence. Pause, resume, cancel, redirect a task, or create a follow-up.
- **Standing instructions** runs saved instructions once, at an interval, when
  a selected file changes, or after a transcript finishes. New instructions
  start disabled. Enable the editable meeting-notes template to produce notes
  and action items from future completed transcripts.
- **Context** stores preferences and project notes you choose to save. Notes
  retain their source and expiry; expired text is excluded from fresh context.

Closing a view or choosing **New chat** keeps saved work. **Stop agent** cancels
the current conversation's active and queued tasks; individual tasks have their
own controls in Work. On restart, interrupted tasks recover from saved progress.
A task with an unknown action outcome checks what happened before repeating an
effect. Endpoint failures and other blockers wait for an explicit resume.

The worker executes one task at a time, and a task waiting for a reply releases
its execution slot. Questions survive restarts. In optional approval mode,
approvals identify an exact operation and require its visible preview; use
**Request fresh preview** if that preview was lost during a restart.

Completed transcripts trigger only after their final text has been saved.
Duplicate delivery creates one task, missed intervals coalesce, and disabling
an instruction prevents new handoffs. A handoff already accepted remains a
saved task that can be cancelled separately. Meeting-note outputs live under
`%LOCALAPPDATA%\toolkit\work\work-results`, each in its task's folder.

The [interactive architecture](docs/agentic-pc.html) explains the work flow.
The [implementation guide](docs/agentic-implementation.md) records the interfaces
and verification requirements.

## Changing the toolkit

Ask the agent, by voice or typed: "add a snippet for my email address", "make
the indicator smaller", "add a key that opens my timesheet". It works on a copy
of the source in its own folder under `%LOCALAPPDATA%\toolkit\workspaces`. Draft
edits stay available for follow-up requests in the same conversation, then:

1. **Check and fix**: every Python file, including the `.pyw` launchers, compiles.
   The agent imports production modules and runs fast tests on a fresh copy of
   its draft. Windows integration tests are deferred until submission, so it can
   fix failures and check again quickly while editing.
2. **Submit**: the agent sends the checked change to the keeper. There is no
   approval step by default. With `TOOLKIT_AGENT_APPROVALS=ask`, the agent runs
   syntax checks first and the keeper shows the complete diff before running
   the proposed code and tests.
3. **Verify**: the keeper checks its own frozen copy of the source: every module
   imports and the full suite, including Windows integrations, passes. Failed
   checks or changes to the candidate during checks prevent installation.
4. **Install and restart**: the keeper saves the current version and durable
   recovery state, installs the checked copy, clears old bytecode, and restarts
   the layout, assistant, and automation worker. File-write failures restore the previous version.
5. **A ten-minute trial** offers Keep and Undo, usable with the mouse. Startup
   failure or a crash undoes the change. Quitting/rebooting during the trial also
   restores the previous version on the next launch; choose Keep before quitting
   if you want the update to stay.
6. **Later**, tray menu → Undo the last update still works after restarting.
   Repeated crashes restore the previous version too.

The last 20 versions are kept in `%LOCALAPPDATA%\toolkit\versions`. The agent
can't change `keeper.py`, `selfcheck.py`, `runtime.py` or `toolkit.pyw`, and the keeper uses
nothing but Python's standard library, so the normal source-edit tools cannot
replace the updater. The private `.python` and `.runtime` directories are also
protected and stay outside source snapshots and updates. Code and tests run with
your account permissions.

## Publishing the agent's changes to GitHub

Ask the agent to "make the change and publish it to GitHub". It has a
`publish_changes` tool: it freezes its workspace, runs the runtime checks,
commits, and pushes a new
`toolkit/agent-*` branch. It never force-pushes or updates `main`. Publishing does
not install the change; ask it to publish before applying, since installation
restarts the agent. In optional approval mode it asks you to approve the diff
and destination first. Merge the branch on GitHub after review.

Publishing requires **Git for Windows on PATH** and credentials already set up
for the repository (for example through Git Credential Manager). The Windows bundle includes the SDK dependencies for the other features. A missing Git installation,
failed authentication, failed test or changed remote base produces an error and
stops the push. Credentials are not written into the source or journal.

Optional environment settings:

| Variable | Default |
|---|---|
| `TOOLKIT_GIT_REMOTE` | `https://github.com/JimLundin/toolkit.git` |
| `TOOLKIT_GIT_BASE` | `main` |
| `TOOLKIT_GIT_AUTHOR_NAME` | `Toolkit Agent` |
| `TOOLKIT_GIT_AUTHOR_EMAIL` | `toolkit@localhost` |

The running source must match the selected remote base before publishing. After
merging, update the running source to that base; if you installed an unmerged
change, either select its published branch as the base or merge it first.

## Agent implementation

Toolkit uses the [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents/quickstart)
through `assistant/sdk.py`. Its `Runner` owns model turns and tool execution;
`assistant/agent.py` retains Toolkit's tools and permissions. The supervised
automation process owns execution, browser sessions, and the durable action
journal; the assistant owns views, human input, and audio capture. The SDK
uses your configured OpenAI-compatible
[Responses API](https://platform.openai.com/docs/api-reference/responses) endpoint
for agent tasks and tool calling, with streaming enabled.
The configured reasoning effort and `summary: "auto"` are sent on every agent request. Encrypted
reasoning and completed tool results stay in the conversation across turns.
Only public summary and response-text events reach the view. The SDK adapter
assembles content parts; immutable timeline entries replace each live row.
Publishers belong to one request, so late events cannot enter another turn.
Tools run
one at a time, enforced locally so endpoints that reject `parallel_tool_calls`
still work. Automatic request images and screenshot tool results share a limit
of two retained images.
Response storage and tracing are disabled. Quick chat and transcription still
use `assistant/llm.py` and their existing endpoints.

The notifications in `assistant/agent_toasts.py` and conversation history
in `assistant/agent_panel.py` share the main UI thread in `assistant/ui.py` with
prompts, answers, reviews, the key map and overlays. Qt owns the event loop;
`assistant/qt_ui.py` manages shared composition and UI timers. Closing a view
leaves that loop available to the remaining views. The hidden Windows message
window and its message loop have their own application control thread.
`assistant/agent_window.py` owns the session controller and queues
immutable snapshots for painting. The model and inline questions stay on worker
threads. `assistant/notifications.py` projects bounded notifications with pure
lifetime transitions; expiry never removes session messages. Background updates
preserve keyboard focus; history preserves selection and scroll position.
`assistant/toast_card.py` draws the text and controls on a per-pixel transparent
Qt surface. `winapi/composition.py` places native background blur and a soft
shadow beneath it, clipped to a 26-pixel rounded shape. The glass tracks Qt's
actual bounds after display scaling. `winapi/appearance.py` samples neighboring
background pixels on one bounded worker to select readable text and tint.
A shared timer rotates existing spinner shapes only while needed; it does not
rebuild the text. Reading timers also observe hover over transparent pixels.
`assistant/qt_text.py` measures Markdown in separate text documents before each
final card layout, so updates never resize visible windows just to measure text.
Agent messages render
Markdown headings, nested emphasis, lists and checklists, quotes, code blocks,
strikethrough and links. Tables use compact labelled rows to fit the narrow
timeline. Text stays selectable, code stays literal, and questions stay inline.
There is no browser runtime, local UI server or separate agent UI process.

`assistant/activity.py` samples foreground app/title changes once a second.
`assistant/commands.py` runs cancellable commands with bounded output.
`assistant/uia.py` and `assistant/uia.ps1` bridge the installed Windows UI
Automation API through PowerShell/.NET, scoped to a window with stable runtime
control identifiers. This adds no Python packages or browser dependencies.

The full Python distribution and its SHA-256 are pinned in
`requirements/python-win64.json`; matching dependency artifacts are pinned in
`requirements/windows-py315.lock`. `.runtime/manifest.json` records both the
Python version, included wheels, and excluded interpreter paths. The bundle
uses the official full Python archive with the pinned exclusions applied:
Tkinter, Tcl/Tk DLLs, IDLE, and Turtle are omitted.
The runtime contains the Agents SDK, `markdown-it-py`, PySide6 Essentials,
Shiboken and the win32more Windows composition bindings, with their dependencies.
Updating either pin requires rebuilding the bundle. The protected
bootstrap selects the bundled interpreter and checks the dependency ABI before
loading native modules. Child commands use the same private Python.
Workspace checks reuse that installed bundle, including
its Windows import hooks, so a source copy can run the same checks as the live app.

## Logs

All in `%LOCALAPPDATA%\toolkit` (tray menu → Open logs folder):

| File | |
|---|---|
| `usage.jsonl` | which features were used, how long they took, whether they failed. Voice uses include held/audio durations, transcription/submission timing, and transcript/requested/submitted character counts; agent uses include preparation, first response and first tool timings. Never what you typed or said. Tray menu → **Usage report** lists every feature by use, and the ones never used, so they can go. |
| `work/work.sqlite3` | saved tasks, checkpoints, questions, action fingerprints and outcomes, standing instructions, and explicit context |
| `assistant.log`, `automation.log`, `layout.log` | problems and crashes |
| `work/worker.log` | bounded work-process diagnostics; automation failures point here |
| `agent-ui.log` | desktop view startup timing and errors |
| `keeper.log` | starts, installs, trials, undos |

What leaves the laptop: recordings (for transcription), the text, selection
or screen area you used a tool on, and the recent app/window context and request
screenshot when automatic context is enabled. These go to your configured
endpoint. The idle activity buffer is not written to disk.
Task instructions and public progress/results are saved locally for recovery.
The action journal does not store arbitrary command text, browser/Teams
payloads, screenshots, credentials, encrypted reasoning, or raw SDK history.

## How it's built

```
keeper.py       runs the programs; installs, tries and undoes changes (protected)
selfcheck.py    compile, import, test: the gate every change passes (protected)
protocol.py     what the layout and the assistant say to each other (window messages)
config.py       your settings: apps, snippets, rewrite prompts, allowed commands
usage.py        the usage log
winapi/         typed Win32 bindings: the only ctypes code
miryoku/        the layout: layout.py (the tables), engine.py (the logic), win.py
automation/     task.py and store.py (durable work), controller.py (execution),
                actions.py and execution.py (attempts and verification),
                routines.py and context.py (standing work and saved knowledge),
                ipc.py, bridge.py, app.py (the supervised worker)
assistant/      llm.py (the endpoint), features.py (what each key does), agent.py,
                workspace.py (the agent's copy of the source), keymap.py;
                agent_session.py, agent_window.py (conversation and UI scheduling),
                agent_toasts.py, toast_card.py, qt_text.py, qt_ui.py (Qt bubbles),
                agent_panel.py, ui.py, theme.py, rich_text.py (the shared Qt UI);
                desktop.py and app.py on Windows
```

The logic never calls Windows directly: the layout's engine goes through a
`Host`, the assistant's features through a `Desktop`, the agent through a
`Machine`, so all of it is tested with fakes. The code is strictly typed, with no
`Any` outside the boundaries to untyped code (`winapi` for ctypes,
the assistant's Qt view modules for GUI bindings,
`assistant/sdk.py` for SDK integration, and
`selfcheck.py` for looking inside modules). Our classes use `__slots__`; SDK
lifecycle subclasses and Qt objects inherit their libraries' storage. Behaviour changes come with tests.

## Development

The canonical source is the private repository `JimLundin/toolkit`. Clone it and
run checks with Python 3.15 or later. Install source dependencies first:
`python -m pip install -e .`. The prebuilt Windows bundle needs no pip step.
Development tools are optional locally and required by CI.
The [functional design notes](docs/functional-design.md) explain the domain
models, effect owners, and keyboard performance measurements.
The source uses deferred annotations directly, `type` aliases and scoped generic
type parameters. It does not need `from __future__ import annotations`. The small
launcher bootstrap remains readable by older Python associations so they can
hand off to the bundled 3.15 interpreter before importing the application.

GitHub Actions checks Python 3.15 on Windows and Linux, plus Mypy,
Pyright and Ruff on Windows with the native UI dependencies. Desktop end-to-end tests are available through the manual
**Windows desktop tests** workflow on a dedicated self-hosted Windows runner
labelled `toolkit-desktop`; they take control of its keyboard and mouse.

If Windows refuses injected keys in an elevated application, the layout switches
off and passes physical keys through. Use the plain keyboard there, then tap §
to re-enable the layout when you return to a normal application.

## Tests

```
python selfcheck.py --fast --durations 10 # editing: defer Windows integration modules
python selfcheck.py --durations 10        # full gate, required before install
python -m pytest -q tests/test_pcm.py     # one area, with optional pytest installed
mypy . && pyright && ruff check .         # if you have them
TOOLKIT_E2E=1 pytest tests/test_windows_*e2e.py
python scripts/bundle.py dist/toolkit-sdk-python315-win64.zip
```

Both check profiles compile every Python source and import production modules.
`--fast` defers `tests/test_windows_*.py`; the keeper and CI use the full profile.
There is no pytest dependency in the installed bundle. CI runs the suite once
through selfcheck for each platform and checks the extracted Windows bundle too.

For focused mutation testing, install pytest in your development environment:

```
python scripts/mutate.py assistant/recording_state.py --tests tests/test_recording_state.py --limit 0 --workers 2 --output dist/recording-mutations.json
```

The runner freezes the source, makes one change per disposable copy, and records
which tests detect it. Timeouts and collection failures are reported separately.
Use `--function NAME` to narrow the scope and `--replace BEFORE AFTER` to seed a
specific regression. Native desktop tests require `--workers 1`. Mutation runs
are an explicit audit command; they do not slow editing checks or installation.
See the [test audit](docs/testing-audit.md) for measurements and surviving
mutations, and the [dependency inventory](docs/testing-dependencies.md) for the
implemented interfaces, replacement tests, and remaining native instrumentation.

Inside an extracted Windows bundle, use `.python\python.exe selfcheck.py` to run
checks with its matching interpreter. The bundle builder can run on Windows or
Linux and accepts `--cache FOLDER` to reuse a verified Python download.

The `TOOLKIT_E2E=1` command runs on Windows only: it starts the real toolkit, presses real
keys and drags the real mouse, and has the keeper install, undo and refuse
changes on a copy of the toolkit (a few minutes, against a fake endpoint), so
leave the keyboard alone while it runs.

`tests/test_windows_agent_panel.py` exercises the actual Qt panel, including
queued input, inline responses, cancellation and updates while hidden.
`tests/test_windows_agent_toasts.py` exercises the Qt bubbles and native glass,
including attached results, text selection, scrolling, stop targets, display
scaling, reduced motion and cleanup.
