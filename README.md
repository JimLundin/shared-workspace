# Toolkit

A personal toolkit for a locked-down Windows laptop: no admin rights, nothing to
install but Python. Two programs:

- **the layout** (`miryoku/`): Colemak Mod-DH in the Miryoku style on the
  laptop's own ISO keyboard: home row mods, layers, caps word, snippets;
- **the assistant** (`assistant/`): dictation, rewriting, questions, screen
  reading, app switching, window placement, and an agent, all through your
  company's OpenAI-style endpoint. It starts the layout, restarts it if it
  crashes, and lives in the notification area (the tray).

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
5. For the `ai` command, add this folder to your user `PATH`.

`miryoku.pyw` runs the layout alone, without the assistant.

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
`,,week` `,,shrug` `,,thx` `,,mvh` `,,br`. Add your own in `config.py`.

## The agent

Give it a task by voice or typed. It works step by step in a window of its own,
with a Stop button (and Stop on the Tools layer). What it may do is decided by
the toolkit, not the model:

- **Reading** files, folders, the clipboard, windows and the screen: free.
- **Writing a file, opening something, running a command**: asks every time,
  except commands on `ALLOWED_COMMANDS` in `config.py` that can't chain another.
- **Mouse and keyboard**: asks once per task.

It stops after 40 steps (`AGENT_MAX_STEPS`). Everything it does goes to its journal.

## `ai` in the terminal

```
ai what does EADDRINUSE mean
git diff | ai write a commit message
ai -c find files over 100MB here       # suggests a command; runs it if you say y
ai -m gpt-6-astra explain this regex
```

## Logs

All in `%LOCALAPPDATA%\toolkit` (tray menu → Open logs folder):

| File | |
|---|---|
| `usage.jsonl` | which features were used, how long they took, whether they failed. Never what you typed or said. Tray menu → **Usage report** lists every feature by use, and the ones never used, so they can go. |
| `agent.jsonl` | every action the agent took, with its arguments |
| `assistant.log`, `layout.log` | problems and crashes |

What leaves the laptop: recordings (for transcription), and the text, selection
or screen area you used a tool on, sent to your endpoint and nowhere else.

## How it's built

```
protocol.py     what the two programs say to each other (window messages)
config.py       your settings: apps, snippets, rewrite prompts, allowed commands
usage.py        the usage log
winapi/         typed Win32 bindings: the only ctypes code
miryoku/        the layout: layout.py (the tables), engine.py (the logic), win.py
assistant/      llm.py (the endpoint), features.py (what each key does),
                agent.py, cli.py; desktop.py, ui.py (Tk) and app.py on Windows
```

The logic never calls Windows directly: the layout's engine goes through a
`Host`, the assistant's features through a `Desktop`, the agent through a
`Machine`, so all of it is tested with fakes. The code is strictly typed, with no
`Any` outside the two boundaries to untyped libraries (`winapi` for ctypes,
`assistant/ui.py` for Tk), and every class has `__slots__`.

## Tests

```
pytest                                   # everything, anywhere
mypy . && pyright && ruff check .
TOOLKIT_E2E=1 pytest tests/test_windows_e2e.py tests/test_windows_assistant_e2e.py
```

The last line runs on Windows only: it starts the real layout and assistant,
presses real keys and drags the real mouse for about half a minute (against a
fake endpoint), so leave the keyboard alone while it runs.
