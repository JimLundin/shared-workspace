"""Bounded conversation bubbles and history on the assistant UI thread."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import time
from collections.abc import Callable
from dataclasses import replace

from winapi import appearance, inputs, screen, windows
from winapi.notification_input import Region, WheelRouter
from winapi.shapes import Rect

from . import notification_reading as reading
from .agent_panel import AgentPanel
from .agent_session import Approval, Session, Snapshot, pending
from .agent_window import AgentWindow
from .notifications import (
    MAX_VISIBLE,
    WIDTH,
    Entry,
    Frame,
    Key,
    Recording,
    Space,
    Stack,
    Turn,
    advance,
    allocate,
    hover,
    next_expiry,
    order_of,
    priority,
    reading_hold,
    results,
    spinning,
)
from .overlay import nearest_work_area
from .panel import PanelPreferences, load_preferences
from .qt import QApplication, QFontMetricsF, QtCore
from .qt_text import font
from .qt_ui import EventLoop, cancel, later
from .toast_card import Card, Measure


class AgentToasts:
    __slots__ = (
        "agent",
        "animation_timer",
        "cards",
        "clock",
        "closed",
        "expiry_timer",
        "history",
        "history_visible",
        "loop",
        "material_preference",
        "materials",
        "measure",
        "motion_enabled",
        "next_material",
        "pointer_timer",
        "preferences",
        "remember",
        "root",
        "scale",
        "session",
        "stack",
        "timing",
        "wheel",
        "work",
    )

    def __init__(
        self, root: QApplication, agent: AgentWindow, *, preferences: PanelPreferences | None = None, remember: bool = True,
        clock: Callable[[], float] = time.monotonic, motion_enabled: Callable[[], bool] = appearance.animations_enabled,
        material_preference: Callable[[], appearance.Preference] = appearance.preference,
    ) -> None:
        self.root, self.agent, self.clock, self.motion_enabled = root, agent, clock, motion_enabled
        self.material_preference = material_preference
        self.materials = appearance.FrostSampler()
        self.preferences = preferences if preferences is not None else load_preferences()
        self.remember = remember
        self.session = Session()
        self.stack = Stack()
        self.loop = EventLoop(root, self.sync_layers)
        self.measure = Measure()
        self.timing = appearance.TimerResolution()
        self.wheel = WheelRouter()
        self.cards: dict[Key, Card] = {}
        self.history: AgentPanel | None = None
        self.history_visible = False
        self.closed = False
        self.work: Rect | None = None
        self.scale = 0.0
        self.expiry_timer: QtCore.QTimer | None = None
        self.animation_timer: QtCore.QTimer | None = None
        self.pointer_timer: QtCore.QTimer | None = None
        self.next_material = 0.0

    def render(self, state: Snapshot) -> None:
        if self.closed:
            return
        if self.stack.identity != state.identity:
            self.clear_cards()
        self.session = state
        if self.history is not None:
            self.history.render(state)
        self.refresh()

    def refresh(self) -> None:
        if self.closed:
            return
        now = self.clock()
        self.stack = advance(self.stack, self.session, now)
        if not self.history_visible:
            self.sync(now)
        self.schedule_pointer()
        self.schedule_expiry()

    def schedule_expiry(self) -> None:
        if self.expiry_timer is not None:
            cancel(self.expiry_timer)
            self.expiry_timer = None
        if (deadline := next_expiry(self.stack)) is not None:
            self.expiry_timer = later(self.root, max(1, round((deadline - self.clock()) * 1000)), self.expire)

    def expire(self) -> None:
        self.expiry_timer = None
        if not self.closed and self.cards:
            self.observe_pointer()  # include an arrival since the last glass hover sample
        self.refresh()

    def set_hover(self, key: Key, inside: bool) -> None:
        if not self.closed:
            card = self.cards.get(key)
            changed = card is not None and card.hovered != inside
            if card is not None:
                card.hovered = inside
                if changed and card.needs_reading():
                    card.reader = reading.Paused(card.reader.offset)
            updated = hover(self.stack, key, inside, self.clock())
            if updated != self.stack:
                self.stack = updated
                self.schedule_expiry()
            if changed:
                self.tick()

    def schedule_pointer(self) -> None:
        # One observer handles hover, wheel routing, and occasional background
        # observations. Desktop pixel reads run on the bounded worker.
        watching = not self.closed and (bool(self.wheel.regions) or any(not card.retiring for card in self.cards.values()))
        if watching and self.pointer_timer is None:
            self.pointer_timer = later(self.root, 16 if self.wheel.regions else 125, self.poll_pointer)
        elif not watching and self.pointer_timer is not None:
            cancel(self.pointer_timer)
            self.pointer_timer = None

    def observe_pointer(self) -> None:
        x, y = inputs.cursor_position()
        for card in self.cards.values():
            if not card.retiring:
                bounds = windows.outer_rect(card.handle)
                self.set_hover(card.key, bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom)
        now = self.clock()
        if not any(card.motion is not None or card.reveals for card in self.cards.values()):
            samples = {sample.bounds: sample for sample in self.materials.read()}
            if samples:
                preference = self.material_preference()
                for card in self.cards.values():
                    if card.bounds is not None and (sample := samples.get(card.bounds)) is not None:
                        card.match_background(sample, preference)
            if now >= self.next_material:
                self.next_material = now + .75
                self.materials.submit(tuple(card.bounds for card in self.cards.values()
                                            if card.glass and not card.retiring and card.bounds is not None))

    def poll_pointer(self) -> None:
        self.pointer_timer = None
        if not self.closed:
            targets = {id(card.text): card.scroll_delta for card in self.cards.values() if not card.retiring}
            targets.update({id(card.details): card.scroll_details for card in self.cards.values() if not card.retiring})
            for event in self.wheel.drain():
                if (scroll := targets.get(event.target)) is not None:
                    scroll(event.delta)
            self.observe_pointer()
            self.schedule_pointer()

    def area(self) -> Rect:
        areas = [monitor.work for monitor in screen.monitors()]
        if self.work is not None:
            return nearest_work_area((self.work.right - 1, self.work.bottom - 1), areas)
        if self.preferences.bounds is not None:
            bounds = self.preferences.bounds
            return nearest_work_area((bounds.left, bounds.top), areas)
        return screen.monitor_of(windows.foreground()).work

    def height_limits(self, width: int, scale: float, available: int) -> dict[Key, int]:
        line_height = round(QFontMetricsF(font()).height() * scale * 1.38)
        spaces: list[Space] = []
        for toast in self.stack.toasts:
            prepared = self.cards[toast.key].measure(toast.content, width)
            rank = priority(toast.content)
            preferred = min(prepared.preferred, round((480 if rank == 2 else 360) * scale))
            preview = min(prepared.body_height, line_height * 2 + 16)
            minimum = min(preferred, prepared.header_height + preview + prepared.hint_height + prepared.padding)
            spaces.append(Space(minimum, preferred, rank))
        return dict(zip((toast.key for toast in self.stack.toasts), allocate(tuple(spaces), available), strict=True))

    def sync_layers(self) -> None:
        # Qt rounds device-independent bounds during its layered paint. Align
        # the blur and shadow with those final physical pixels at fractional DPI.
        for card in self.cards.values():
            if not card.closed and card.window.isVisible():
                card.bounds = windows.outer_rect(card.handle)
                if card.glass and card.layer is not None:
                    card.layer.position(card.bounds, self.scale, card.alpha)
                if card.tail_pending:
                    card.text.verticalScrollBar().setValue(card.text.verticalScrollBar().maximum())
                    card.tail_pending = False
        for message in self.session.messages:
            if isinstance(message, Approval) and pending(message) and self.approval_visible(message.request_id):
                self.agent.input_presented(message.request_id)

    def approval_visible(self, request_id: str) -> bool:
        """A painted, opaque card or visible history entry contains this review."""
        if self.closed:
            return False
        if self.history_visible:
            return self.history is not None and self.history.approval_visible(request_id)
        for card in self.cards.values():
            if card.closed or card.retiring or card.alpha < .99 or not card.window.isVisible() or not card.text.isVisible():
                continue
            content = card.content
            entries = results(content) if isinstance(content, Turn) else (content,) if isinstance(content, Entry) else ()
            if any(isinstance(entry, Entry) and isinstance(entry.message, Approval)
                   and entry.message.request_id == request_id and pending(entry.message) for entry in entries):
                return True
        return False

    def sync(self, now: float, *, animate: bool | None = None) -> None:
        if not self.cards and not self.stack.toasts:
            self.work = None
            return
        animate = self.motion_enabled() if animate is None else animate
        preference = self.material_preference()
        work = self.area()
        scale = self.loop.scale(work)
        immediate = self.work is not None and (self.work != work or self.scale != scale)
        self.work, self.scale = work, scale
        self.measure.scale = scale
        margin, gap = round(16 * scale), round(10 * scale)
        width = min(round(WIDTH * scale), max(1, self.work.width - margin * 2))
        desired = {toast.key for toast in self.stack.toasts}
        created: set[Key] = set()
        # Create and measure before changing any visible card's contents/size.
        for toast in self.stack.toasts:
            if toast.key not in self.cards:
                self.cards[toast.key] = Card(self, toast, width)
                created.add(toast.key)
        retiring = sorted((card for key, card in self.cards.items() if key not in desired),
                          key=lambda card: card.order, reverse=True)
        reserved = retiring[:max(0, MAX_VISIBLE - len(desired))]
        available = self.work.height - margin * 2 - gap * max(0, len(desired) + len(reserved) - 1) - sum(card.height for card in reserved)
        limits = self.height_limits(width, scale, available)
        self.stack = replace(self.stack, toasts=tuple(toast for toast in self.stack.toasts if limits[toast.key]))
        desired = {toast.key for toast in self.stack.toasts}
        for key, card in tuple(self.cards.items()):
            if key in desired:
                card.retiring = False
            elif not card.retiring:
                card.retiring = True
                card.target(card.y - round(16 * scale), 0, now, animate=animate)
                if not animate or key in created:
                    card.close()
                    del self.cards[key]
        retiring = sorted((card for card in self.cards.values() if card.retiring), key=lambda card: card.order, reverse=True)
        for card in retiring[max(0, MAX_VISIBLE - len(desired)):]:
            card.close()
            del self.cards[card.key]
        for toast in self.stack.toasts:
            current = self.cards[toast.key]
            before = current.width, current.height
            current.render(toast.content, width, limits[toast.key], preference)
            # A recording's controls become usable immediately when it starts.
            immediate = immediate or (toast.key not in created and before != (current.width, current.height)
                                      and isinstance(toast.content, Recording))
            if toast.key in created:
                current.position(self.work.right - margin,
                                 Frame(self.work.bottom - margin - current.height + round(16 * scale), current.width, current.height, 0,
                                       current.layout.details_height if current.layout is not None else 0))
                current.reveal()
            current.order = order_of(toast)
            current.check_pointer()
        self.layout(now, animate=animate and not immediate)
        self.tick()
        self.loop.watch(bool(self.cards))
        self.loop.pump()

    def layout(self, now: float, *, animate: bool) -> None:
        if self.work is None:
            return
        scale = self.scale
        margin, gap = round(16 * scale), round(10 * scale)
        bottom = self.work.bottom - margin
        targets: list[tuple[Card, Frame]] = []
        for card in sorted(self.cards.values(), key=lambda card: card.order, reverse=True):
            bottom -= card.height
            right = self.work.right - margin
            if card.right != right:
                card.position(right, card.frame)
            if not card.retiring:
                targets.append((card, Frame(bottom, card.width, card.height, 1,
                                            card.layout.details_height if card.layout is not None else 0)))
            bottom -= gap
        changed = any(target != (card.motion.end if card.motion is not None else card.frame) for card, target in targets)
        # All active rectangles retarget on the same clock. Interpolating top
        # and bottom together preserves the gap between neighboring cards;
        # applying target heights before moving their tops would overlap them.
        for card, target in targets:
            card.target(target.y, target.alpha, now, animate=animate, retarget=changed)

    def sync_wheel(self) -> None:
        regions: list[Region] = []
        for card in self.cards.values():
            if card.retiring or card.bounds is None:
                continue
            if card.frame.details and card.prepared is not None:
                bounds = card.bounds
                top = bounds.top + card.prepared[2].header_height
                regions.append(Region(id(card.details), Rect(bounds.left, top, bounds.right, top + round(card.frame.details))))
            if card.overflow():
                regions.append(Region(id(card.text), card.bounds))
        self.wheel.update(tuple(regions))

    def tick(self) -> None:
        if self.animation_timer is not None:
            cancel(self.animation_timer)
            self.animation_timer = None
        if self.closed:
            return
        now = self.clock()
        removed = False
        spin = False
        revealing = False
        animate = self.motion_enabled()
        reader = next((card for card in sorted(self.cards.values(), key=lambda card: card.order) if card.needs_reading()), None)
        before = self.stack
        for key, card in tuple(self.cards.items()):
            if card.motion is not None:
                card.position(card.right, card.motion.sample(now) if animate else card.motion.end)
                if card.motion.finished(now) or not animate:
                    card.motion = None
                    if card.retiring:
                        card.close()
                        del self.cards[key]
                        removed = True
            if not card.closed:
                revealing = card.fade_text(now, animate=animate and card.look is not None
                                           and not isinstance(card.look[1], appearance.Contrast)) or revealing
                reading_now = card.read(now, paused=card.hovered or card is not reader, smooth=animate)
                self.stack = reading_hold(self.stack, key, reading_now, now, busy=self.session.busy or self.session.waiting, hovered=card.hovered)
            if not card.closed and not card.retiring and card.content is not None and spinning(card.content):
                card.pulse(now if animate else 0)
                spin = spin or animate
        if removed:
            self.layout(now, animate=self.motion_enabled())
        self.sync_wheel()
        self.schedule_pointer()
        moving = any(card.motion is not None for card in self.cards.values())
        scrolling = reader is not None and not reader.hovered
        sliding = reader is not None and scrolling and animate and isinstance(reader.reader, reading.Scrolling)
        self.timing.update(moving or revealing or sliding)
        if self.stack is not before:
            self.schedule_expiry()
            self.schedule_pointer()
        if moving or revealing or spin or scrolling:
            interval = 1 / 60 if moving or revealing or sliding else .05
            delay = max(1, round((now + interval - self.clock()) * 1000))
            self.animation_timer = later(self.root, delay, self.tick)
        self.loop.watch(bool(self.cards))
        self.loop.pump()
        if not self.cards:
            self.work = None

    def show(self, *, focus: bool = False) -> None:
        if self.closed:
            return
        if not focus:
            self.refresh()
            return
        if self.history is None:
            self.history = AgentPanel(self.root, self.agent, preferences=self.preferences, remember=self.remember, on_hide=self.resume)
        self.history_visible = True
        for toast in self.stack.toasts:
            self.stack = reading_hold(self.stack, toast.key, False, self.clock(),
                                      busy=self.session.busy or self.session.waiting, hovered=False)
            self.stack = hover(self.stack, toast.key, False, self.clock())
        self.schedule_expiry()
        self.clear_cards()
        self.history.render(self.session)
        self.history.show(focus=True)

    def resume(self) -> None:
        if self.history is not None:
            self.preferences = self.history.preferences
        self.history_visible = False
        self.refresh()

    def clear_cards(self) -> None:
        self.timing.update(False)
        self.wheel.update(())
        if self.pointer_timer is not None:
            cancel(self.pointer_timer)
            self.pointer_timer = None
        if self.animation_timer is not None:
            cancel(self.animation_timer)
            self.animation_timer = None
        for card in self.cards.values():
            card.close()
        self.cards.clear()
        self.loop.watch(False)
        self.loop.pump()
        self.work = None
        self.next_material = 0.0

    def close(self) -> None:
        self.closed = True
        if self.expiry_timer is not None:
            cancel(self.expiry_timer)
            self.expiry_timer = None
        self.clear_cards()
        self.wheel.close()
        self.materials.close()
        self.measure.close()
        if self.history is not None:
            self.history.close()
        self.loop.close()
