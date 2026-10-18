# Toolkit

A personal toolkit for a locked-down Windows laptop: no admin rights, nothing to
install but Python. It changes itself: ask its agent for a new feature or a fix,
approve the diff, and it installs and reloads the change, and undoes it by itself
if it fails. Three programs:

- **the layout** (`miryoku/`): Colemak Mod-DH in the Miryoku style on the
  laptop's own ISO keyboard: home row mods, layers, caps word, snippets;
- **the assistant** (`assistant/`): dictation, rewriting, questions, screen
  reading, app switching, window placement, and an agent, all through your
  company's OpenAI-style endpoint. It lives in the notification area (the tray);
- **the keeper** (`keeper.py`): runs the other two, restarts them if they crash,
  and installs the agent's changes to them (see *Changing the toolkit*).

Everything is the standard library plus our own typed Win32 bindings (`winapi/`).

## Start

1. Set the Windows keyboard layout to **English (US)**.
2. Set these environment variables (Start → "Edit environment variables for your
   account"; no admin needed):

   | Variable | |
   |---|---|
   | `OPENAI_BASE_URL` | your endpoint, ending in `/v1` |
   | `OPENAI_API_KEY` | its key |
   | `TOOLKIT_CHAT_MODEL` | optional; default `gpt-5.4-mini` (quick jobs) |
   | `TOOLKIT_AGENT_MODEL` | optional; default: the chat model |
   | `TOOLKIT_TRANSCRIBE_MODEL` | optional; default `gpt-4o-transcribe` |
   | `TOOLKIT_VOCABULARY` | optional; names and jargon to expect in dictation |
   | `TOOLKIT_LANGUAGE` | optional; `sv` or `en`, default detected |

   Without the first two the layout, snippets, apps and window keys still work;
   the AI keys tell you what's missing.
3. Turn on Settings → Privacy & security → Microphone → **Let desktop apps access
   your microphone**.
4. Double-click **`toolkit.pyw`**. A dot appears in the tray. To start it at login,
   put a shortcut to it in `shell:startup` (Win+R).

`miryoku.pyw` runs the layout alone, without the assistant or the keeper.

## What you see

- **The tray dot**: green: running, layout on · grey: layout off or not running ·
  red: listening · amber: working · blue: the agent is on a task. Hover for words;
  click for the menu. (Windows 11 may tuck it behind the ^ at first: drag it out.)
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
| thumbs **X C V** | Esc · Space/**Nav** · Tab/**Tools** |
| thumbs **, . -** | Enter/**Sym** · Backspace/**Num** · Delete/**Apps** |

**Tools** (hold the Tab thumb, then the right hand):

| | | | | | |
|---|---|---|---|---|---|
| top **8 9 0 + ´** | rewrite by voice (hold, say how) | fix spelling | translate sv↔en | shorter | more formal |
| home **I O P Å ¨** | dictate, tidied (hold) | dictate a command (hold) | ask… | copy text on screen | ask about the screen |
| bottom **K L Ö** | agent, by voice (hold) | agent, typed | stop the agent | | |
| thumb **,** | dictate (hold), as on B | | | | |

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

## The agent

Give it a task by voice or typed. It works step by step in a window of its own,
with a Stop button (and Stop on the Tools layer). What it may do is decided by
the toolkit, not the model:

- **Reading** files, folders, the clipboard, windows and the screen: free.
- **Writing a file, opening something, running a command**: asks every time,
  except commands on `ALLOWED_COMMANDS` in `config.py` that can't chain another.
- **Mouse and keyboard**: asks once per task.

It stops after 80 steps (`AGENT_MAX_STEPS`). Everything it does goes to its journal.

## Changing the toolkit

Ask the agent, by voice or typed: "add a snippet for my email address", "make
the indicator smaller", "add a key that opens my timesheet". It works on a copy
of the source (`%LOCALAPPDATA%\toolkit\workspace`), never the running files, then:

1. **Syntax checks**: every Python file, including the `.pyw` launchers, compiles.
   This step does not import or run proposed code.
2. **You review** the complete diff in a window owned by the protected keeper.
   Choose **Run checks and install** or Cancel. Approval allows this code and its
   tests to run with your Windows account: review only changes you trust. The
   updater provides recovery, not a sandbox for approved Python code.
3. **Runtime checks**: the keeper checks its own copy of the reviewed source:
   every module imports and every test passes. Cancelled or failing changes are
   not installed. A candidate changed during review or checks is refused.
4. **Install and restart**: the keeper saves the current version and durable
   recovery state, installs the checked copy, clears old bytecode, and restarts
   the layout and assistant. File-write failures restore the previous version.
5. **A ten-minute trial** offers Keep and Undo, usable with the mouse. Startup
   failure or a crash undoes the change. Quitting/rebooting during the trial also
   restores the previous version on the next launch; choose Keep before quitting
   if you want the update to stay.
6. **Later**, tray menu → Undo the last update still works after restarting.
   Repeated crashes restore the previous version too.

The last 20 versions are kept in `%LOCALAPPDATA%\toolkit\versions`. The agent
can't change `keeper.py`, `selfcheck.py` or `toolkit.pyw`, and the keeper uses
nothing but Python's standard library, so the normal source-edit tools cannot replace the updater. Approved code still
runs with your account permissions.

## Logs

All in `%LOCALAPPDATA%\toolkit` (tray menu → Open logs folder):

| File | |
|---|---|
| `usage.jsonl` | which features were used, how long they took, whether they failed. Never what you typed or said. Tray menu → **Usage report** lists every feature by use, and the ones never used, so they can go. |
| `agent.jsonl` | every action the agent took, with its arguments |
| `assistant.log`, `layout.log` | problems and crashes |
| `keeper.log` | starts, installs, trials, undos |

What leaves the laptop: recordings (for transcription), and the text, selection
or screen area you used a tool on, sent to your endpoint and nowhere else.

## How it's built

```
keeper.py       runs the programs; installs, tries and undoes changes (protected)
selfcheck.py    compile, import, test: the gate every change passes (protected)
protocol.py     what the layout and the assistant say to each other (window messages)
config.py       your settings: apps, snippets, rewrite prompts, allowed commands
usage.py        the usage log
winapi/         typed Win32 bindings: the only ctypes code
miryoku/        the layout: layout.py (the tables), engine.py (the logic), win.py
assistant/      llm.py (the endpoint), features.py (what each key does), agent.py,
                workspace.py (the agent's copy of the source), keymap.py;
                desktop.py, ui.py (Tk) and app.py on Windows
```

The logic never calls Windows directly: the layout's engine goes through a
`Host`, the assistant's features through a `Desktop`, the agent through a
`Machine`, so all of it is tested with fakes. The code is strictly typed, with no
`Any` outside the boundaries to untyped code (`winapi` for ctypes,
`assistant/ui.py` for Tk, `selfcheck.py` for looking inside modules), and every
class has `__slots__`. Behaviour changes come with tests.

## Development

The canonical source is the private repository `JimLundin/toolkit`. Clone it and
run checks with Python 3.10 or later. Runtime use still needs only Python; the
extra development tools below are optional locally and required by CI.

GitHub Actions checks Python 3.10 and 3.12 on Windows and Linux, plus Mypy,
Pyright and Ruff. Desktop end-to-end tests are available through the manual
**Windows desktop tests** workflow on a dedicated self-hosted Windows runner
labelled `toolkit-desktop`; they take control of its keyboard and mouse.

If Windows refuses injected keys in an elevated application, the layout switches
off and passes physical keys through. Use the plain keyboard there, then tap §
to re-enable the layout when you return to a normal application.

## Tests

```
python selfcheck.py                      # what every change must pass; needs only Python
pytest                                   # the same tests, if you have pytest
mypy . && pyright && ruff check .        # if you have them
TOOLKIT_E2E=1 pytest tests/test_windows_*e2e.py
```

The last line runs on Windows only: it starts the real toolkit, presses real
keys and drags the real mouse, and has the keeper install, undo and refuse
changes on a copy of the toolkit (a few minutes, against a fake endpoint), so
leave the keyboard alone while it runs.
