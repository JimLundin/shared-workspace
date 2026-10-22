# Design

- Model domain alternatives like Rust enums: use unions of frozen, slotted
  dataclasses for variants with payloads and `Enum` for variants without payloads.
  Handle variants with `match` and `typing.assert_never` where exhaustive handling
  matters. Use `Literal` for fixed values in external contracts.
- Put decisions and state transitions in pure functions. Pass timestamps and
  observations in; return immutable values. Keep threads, locks, files, network
  calls, and UI updates in the modules that own those effects.
- Validate external data when it enters a typed interface. Use the variant's
  fields to express required data instead of unrelated flags and empty defaults.
- Preserve the keyboard process's isolation from the assistant. Its bounded
  mutable buffers and direct updates use the same variant definitions as the
  rest of the code: convert native event flags to variants once, then reuse those
  events during buffering and replay. Measure key-event latency,
  allocation peaks, and GC with `scripts/benchmark_keyboard.py` before and after
  changing the hook, engine, or their shared dependencies. Keep GC enabled during
  measurement and keep event data acyclic: the live hook disables cyclic GC.
  Apply the same care to audio sample loops and buffer copying.
- Prefer Python 3.15's native annotation and type-alias syntax. Keep the runtime
  bootstrap importable on the system Python 3.12 interpreter.
- When changing domain models or profiling input, read
  [docs/functional-design.md](docs/functional-design.md) for the module map and
  measurement scope.

# Windows deployment

After each completed change, update the Windows Downloads copy as part of the
task. The user has authorized this sync and a reload whenever Toolkit is already
running; no separate confirmation is needed.

- Deploy the checked working tree to
  `C:\Users\Jim.Lundin\Downloads\toolkit-sdk\toolkit`
  (`/mnt/c/Users/Jim.Lundin/Downloads/toolkit-sdk/toolkit` from WSL).
- Refresh `C:\Users\Jim.Lundin\Downloads\toolkit-sdk-python315-win64.zip` too.
  Preserve `.python` and `.runtime` when their pins in `requirements/` are
  unchanged; rebuild with `scripts/bundle.py` when either pin changes.
- If that copy is running, close its `toolkit assistant` window with `WM_CLOSE`
  and wait for its keeper and layout to exit cleanly before replacing files.
  Relaunch `toolkit.pyw` using that copy's `.python\pythonw.exe` and folder.
  Leave a stopped copy stopped.
- Clear replaced modules' bytecode caches. Verify the deployed files and ZIP
  match the working tree, and verify fresh readiness markers and running child
  processes after a reload. Report any deployment or reload failure explicitly.
