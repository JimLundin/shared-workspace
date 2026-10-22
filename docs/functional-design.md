# Functional design

Toolkit models domain alternatives like Rust enums: a union of frozen, slotted
dataclasses for variants with payloads, and `Enum` for variants without payloads.
`match` and `assert_never` make handlers explicit and let the type checkers find
missing cases when a union grows. `Literal` describes fixed external values.
Decisions are pure where practical, with effects owned by small controllers.

The main places to read are:

| Module | Pure model and decisions | Effect owner |
| --- | --- | --- |
| `assistant/llm.py` | System, user, assistant, and tool-result message variants; request serialization | HTTP client functions |
| `assistant/agent_session.py` | Typed events, messages, questions, approvals, session transitions, and presentation | `agent_window.py`, `agent_input.py`, `agent_toasts.py`, and `agent_panel.py` |
| `assistant/notifications.py`, `notification_reading.py`, `toast_style.py` | Bounded projection, reading states, lifetimes, semantic colors, and motion sampling | `agent_toasts.py`, `winapi/appearance.py`, `winapi/notification_input.py` |
| `assistant/browser.py` | Browser source and ownership variants; bounded endpoint validation | `Browser`, `Cdp` and a cancellable connection wait |
| `assistant/recording_state.py` | Audio batches, stored observations, completion rules, stale-update rejection, and recording controls | `meetings.py` and `meeting_store.py` |
| `assistant/activity.py` | Bounded window history reduced from a window or desktop-state observation | `ActivityTracker` and the Windows foreground reader |
| `assistant/geometry.py`, `panel.py`, `rich_text.py` | Geometry, preferences, and Markdown values | Tk views and preference files |

Session events include their timestamps. IDs and clocks are supplied by the
controller or input broker, so a sequence of events can be replayed or branched
without changing its source state. Views receive the immutable session itself.
Questions carry a pending, answered, or cancelled reply; approvals carry a
decision. A reply for the wrong request kind or an already resolved request
leaves the session unchanged.

Live replies and reasoning summaries carry an identity and stream state; tools
carry a working, succeeded, failed, or cancelled outcome. The session replaces
these values in place within its immutable message tuple and preserves each
entry's timestamp. Completed entries reject late updates. The SDK boundary
owns the mutable text fragments and only publishes public response text and
reasoning summaries. Request-bound publishers reject events from an earlier
turn or a reset conversation before they reach the pure transition.

Notifications retain the last observed content separately from the visible
stack, so an expired reply does not reappear on an unrelated session update.
Current conversation entries stay while work or pending input is active;
completion starts monotonic reading timers. Hovering preserves their remaining
duration. Overflowing entries use `Held.READING` until their last line is shown,
then receive the ordinary expiry delay. `Waiting`, `Scrolling`, `Paused` and
`Finished` carry a pixel offset; time, content height and reading pace enter a
pure transition. Replies take turns, and waiting replies retain their lifetime.
Reduced motion advances pages with an overlapping line and keeps a manually
chosen starting position. None of these presentation transitions remove conversation messages.
`Entry` uses the session index as its stable key; an answered question adds a
separate `Response` keyed by that question's index. New tool calls get their own
entries, while updates to one tool or streaming reply retain its identity.
The compact `Working` control stays below the conversation. Its `Busy` enum
distinguishes thinking, waiting, and stopping. Reasoning summaries never become
notification text or trigger text layout.

The Tk owner schedules one expiry callback and animates at most five cards,
retargeting from the current geometry. Each notification fits its rendered content
up to a maximum width, independent of the history window. Headers, the stop icon,
and voice hints contribute to that width. The pure `allocate` function reserves readable
previews and gives pending questions and recent messages the remaining height.
On very small work areas it removes older entries from the projection. A hidden
Tk text widget measures lines using the same Markdown fonts and margins. A line
that wraps at the maximum width already needs that width; shorter lines get an
exact pixel measurement. Keeping wrapping enabled lets Tk retain unchanged
paragraph layout during long streams. Text wraps at the chosen width before
any visible card changes size.
Its tall, hidden viewport supplies cached display-line bounds
for preview fitting; an exact-count fallback handles unusually tall work areas.
Body, header and footer get separate rectangles; viewports end on complete
display lines. All reply text remains in the native text widget, with no character
cutoff. A small scroll marker tracks its position. `Frame` carries top, width, height and opacity together. Every active
card retargets on the same clock when the layout changes, so growing and shrinking
neighbors retain their gaps throughout the transition. Native positioning applies
each sampled rectangle with a fixed right edge, keeping text and glass attached.
The text wraps at its destination width once per content update; moving bounds
reveal it without rewrapping words on every frame. The stop icon follows the
visible right edge throughout the transition.

`rich_text.changed_tail` preserves the unchanged styled prefix, including a
partially expanded text span. Closing Markdown can replace the affected suffix
without deleting completed paragraphs or their selection. New reply text gets
a bounded set of short-lived ink tags; later packets do not dim earlier text.
Motion follows Vault of Glass's 280 ms base and 160 ms fast durations, with its
`cubic-bezier(0.32, 0.72, 0, 1)` movement curve. The frame timer accounts for drawing
time and requests Windows' finer timer resolution only while movement or text
reveals run. Reduced motion, opening history, and closing release that request.
A shared callback rotates existing spinner shapes while work runs. Windows owns
the glass composition; Tk retains opaque text and controls. A bounded pointer
observer preserves hover-to-pause over color-keyed glass. Motion, pointer
observation, and window creation stay in the assistant process. A UI-owned mouse
hook routes only vertical wheel events inside overflowing card bounds. Its
dedicated thread forwards a bounded queue to Tk without waiting for UI work;
other input passes through. The hook is removed when no scrolling cards remain,
and the thread closes with the view. It never runs in the keyboard process.

Browser ownership is `Stopped | Managed | Attached`. A managed variant carries
the process, optional Windows job and visibility. An attached variant identifies
the borrowed browser; closing it only disconnects CDP. The source selection
persists separately across disconnects. Toolkit-created target IDs are tracked
apart from personal tabs for creation limits; navigation and explicit tab closure
use the requested ID. Pending consent is a single WebSocket connection, whose
socket is closed on cancellation. Status inspection never starts that connection.

Recording state is a projection for the UI. The journal can still be absent
while a capture starts. Publication decisions use immutable observations read
by the recording worker. The worker applies the result under its existing lock,
including the checks that prevent an older observation from undoing progress.
SQLite transactions, audio buffer ownership, and capture shutdown remain the
responsibility of their existing effect owners. A transcription batch reads its
audio, completion flag and prior text in one SQLite snapshot, so capture cannot
finish between queries and pair the finished flag with an incomplete tail.
The read transaction ends before audio assembly or network work.

Frozen dataclasses are shallow: nested state also uses immutable values such as
tuples, bytes, and other frozen dataclasses. External JSON and operating-system
results are validated when they enter the domain. Threading events, native
handles, SDK-owned histories, and mutable buffers retain a single explicit owner.

## Keyboard variants

The keyboard uses the same data definitions. Layout actions are an `Action`
union; the engine's mapped key events are `KeyDown | KeyUp`, each carrying a
cell and timestamp. `Decision.TAP` and `Decision.HOLD` name the two tap-hold
outcomes. The last completed tap is a frozen `LastTap` record.

`Engine.on_key` accepts native scancode, direction, and timestamp fields. Once a
key is mapped to a layout cell, it becomes one event variant. The engine handles
that variant with `match` and reuses the same object when buffering or replaying
it. Raw Windows flags stay in the input adapter; ordinary boolean properties,
such as whether caps word is enabled, remain booleans.

The engine owns bounded mutable state and sends effects through `Host`. Its
handler updates held keys and modifiers in place. Using enum-style data does not
require constructing a new engine state or a list of effects for every press.

## Keyboard and audio performance

The keyboard hook and engine run in the layout process. The agent and UI run in
the assistant process, with a separate Python heap and garbage collector. Agent
collections cannot stop the keyboard interpreter, although both processes still
share CPU and other operating-system resources.

The live hook already collects and freezes startup objects, then disables cyclic
GC until shutdown. Event data must remain acyclic under that policy. Benchmarks
deliberately enable GC to expose collection pressure rather than hide it behind
the live loop's guard.

The keyboard's fixed tables, bounded mutable state, and direct buffer updates
are deliberate. An immutable dataclass is useful for a layout description;
copying the full keyboard state on each press would need measured justification.
The same distinction applies to immutable audio formats and mutable sample
buffers. Keyboard variants use the existing allocation point for mapped events;
indicator refreshes and some other paths also allocate small objects. Audio
sample loops keep their existing buffer ownership.

Run the keyboard benchmark in a fresh process using the same Python build for
both revisions:

```powershell
.python\python.exe scripts\benchmark_keyboard.py --root C:\path\to\baseline --output before.json
.python\python.exe scripts\benchmark_keyboard.py --output after.json
```

The workload covers plain typing, home-row rolls, modifiers, layers, repeat,
voice keys, and tap-hold timers without sending input. It uses GC normally and
reports latency percentiles, maximum observed latency, collection counts and
pauses, and peak traced live memory. Inputs and timing storage are allocated
before timing; allocation tracing runs separately so it does not distort the
latency measurements. Peak live memory is not cumulative allocation churn.

Compare the distributions over multiple rounds and retain the worst observed
latency. The benchmark also records source hashes and whether the engine imported
the assistant. It measures engine decisions with a silent host; native hook,
`SendInput`, and operating-system scheduling require separate desktop evidence.
