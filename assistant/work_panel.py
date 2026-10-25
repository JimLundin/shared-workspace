"""The native work overview. Every widget and draft belongs to the UI thread."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import assert_never

from automation.context import ContextRecord, Preference, ProjectNote
from automation.routines import EventTrigger, Every, FileChanged, Once, Routine, RoutineSummary, Trigger
from automation.task import (
    Approval,
    ApprovalDecision,
    CanvasSource,
    Completed,
    Control,
    Question,
    Task,
    TaskStatus,
    TextAnswer,
    Waiting,
    allowed_controls,
)
from winapi import windows

from .canvas_widgets import button, label
from .meeting_panel import MeetingPanel
from .qt import Qt, QtGui, QtWidgets
from .qt_text import units
from .widgets import Editor, Window, column, replace_text, row, show_window
from .work_window import (
    AddPreference,
    AddProjectNote,
    AnswerTask,
    ControlTask,
    CreateRoutine,
    DisableRoutine,
    EnableRoutine,
    FollowUp,
    Redirect,
    RefreshApproval,
    RemoveContext,
    Submit,
    UpdatePreference,
    UpdateProjectNote,
    UpdateRoutine,
    WorkSnapshot,
    WorkWindow,
    current_review,
    task_detail,
    task_label,
    trigger_label,
    when,
)


def _tree(title: str) -> QtWidgets.QTreeWidget:
    tree = QtWidgets.QTreeWidget()
    tree.setHeaderLabels(["Name", title])
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(False)
    tree.setColumnWidth(0, 230)
    tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    tree.setAccessibleName(title)
    return tree


def _selected(tree: QtWidgets.QTreeWidget) -> str | None:
    item = tree.currentItem()
    value: object = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
    return value if isinstance(value, str) else None


def _rows(tree: QtWidgets.QTreeWidget, rows: Sequence[tuple[str, str, tuple[str, ...]]]) -> None:
    selected, scroll = _selected(tree), tree.verticalScrollBar().value()
    previous = {item.data(0, Qt.ItemDataRole.UserRole): item for i in range(tree.topLevelItemCount())
                if (item := tree.topLevelItem(i)) is not None}
    tree.blockSignals(True)
    try:
        wanted = {identity for identity, _, _ in rows}
        for identity, item in previous.items():
            if identity not in wanted:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        for index, (identity, title, values) in enumerate(rows):
            item = previous.get(identity)
            if item is None:
                item = QtWidgets.QTreeWidgetItem()
                item.setData(0, Qt.ItemDataRole.UserRole, identity)
            else:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
            for column_index, text in enumerate((title, *values)):
                item.setText(column_index, text)
            tree.insertTopLevelItem(index, item)
            if identity == selected or (selected not in wanted and index == 0):
                tree.setCurrentItem(item)
        tree.verticalScrollBar().setValue(scroll)
    finally:
        tree.blockSignals(False)


def _form(layout: QtWidgets.QVBoxLayout, title: str, widget: QtWidgets.QWidget) -> QtWidgets.QLabel:
    caption = label(title, "muted")
    layout.addWidget(caption)
    layout.addWidget(widget)
    return caption


def _required(value: str, label: str, maximum: int) -> str:
    value = value.strip()
    if not value or "\0" in value or len(value) > maximum:
        raise ValueError(f"{label} must contain 1 to {maximum:,} characters.")
    return value


def _seconds(value: str, label: str) -> int:
    try:
        minutes = float(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a positive number of minutes.") from error
    if not math.isfinite(minutes) or minutes <= 0 or minutes > 5_256_000:
        raise ValueError(f"{label} must be between one second and ten years.")
    seconds = round(minutes * 60)
    if seconds < 1:
        raise ValueError(f"{label} must be at least one second.")
    return seconds


class NewDraft(Enum):
    NEW = auto()


@dataclass(frozen=True, slots=True)
class FollowDraft:
    task_id: str


@dataclass(frozen=True, slots=True)
class RedirectDraft:
    task_id: str


type Draft = NewDraft | FollowDraft | RedirectDraft


class WorkPanel:
    """Qt owns presentation; WorkWindow owns command validation and durable work."""
    __slots__ = (
        "__weakref__",
        "answer",
        "approval_position",
        "approve",
        "cancel",
        "context",
        "decline",
        "detail",
        "detail_scroll",
        "draft",
        "drafts",
        "follow",
        "handle",
        "meetings",
        "notebook",
        "pause",
        "preview_painted",
        "prompt",
        "prompt_frame",
        "prompt_label",
        "question",
        "question_frame",
        "question_key",
        "redirect",
        "refresh",
        "reply",
        "resume",
        "routines",
        "snapshot",
        "status",
        "tasks",
        "window",
        "work",
    )


    def __init__(self, root: QtWidgets.QApplication, work: WorkWindow, *, clock: Callable[[], float] = time.time) -> None:  # noqa: PLR0915
        self.work = work
        self.meetings: MeetingPanel | None = None
        self.snapshot = WorkSnapshot()
        self.draft: Draft = NewDraft.NEW
        self.drafts: dict[Draft, str] = {}
        self.question_key: tuple[str, str] | None = None
        self.approval_position: int | None = None
        self.preview_painted = False
        self.window = Window("Toolkit · Work", self.hide, width=1080, height=740)
        self.window.setMinimumSize(740, 500)
        layout = column(self.window)
        layout.setContentsMargins(22, 18, 22, 18)
        heading = row()
        heading.addWidget(label("Your work", "title"))
        heading.addStretch()
        heading.addWidget(button("New task", lambda: self.compose(NewDraft.NEW), name="primary"))
        layout.addLayout(heading)
        self.status = label("", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notebook = QtWidgets.QTabWidget()
        layout.addWidget(self.notebook, 1)
        tasks = QtWidgets.QWidget()
        self.notebook.addTab(tasks, "Tasks")
        task_layout = row(tasks)
        self.tasks = _tree("State")
        task_layout.addWidget(self.tasks, 2)
        right = QtWidgets.QWidget()
        content = column(right)
        content.setContentsMargins(12, 10, 0, 0)
        task_layout.addWidget(right, 3)
        self.detail = Editor(readonly=True)
        self.detail.setAccessibleName("Task details and action preview")
        content.addWidget(self.detail, 1)
        self.detail_scroll = self.detail.verticalScrollBar()
        controls = row()
        self.pause = button("Pause", lambda: self.control(Control.PAUSE))
        self.resume = button("Resume", lambda: self.control(Control.RESUME))
        self.cancel = button("Stop", lambda: self.control(Control.CANCEL))
        self.follow = button("Follow up", self.compose_selected)
        self.redirect = button("Redirect", lambda: self.compose_selected(redirect=True))
        for control in (self.pause, self.resume, self.cancel, self.follow, self.redirect):
            controls.addWidget(control)
        controls.addStretch()
        content.addLayout(controls)
        self.question_frame = QtWidgets.QFrame()
        question_layout = column(self.question_frame)
        self.question = label("")
        self.question.setWordWrap(True)
        question_layout.addWidget(self.question)
        self.reply = Editor()
        self.reply.setMaximumHeight(100)
        self.reply.setAccessibleName("Answer the current question")
        question_layout.addWidget(self.reply)
        answers = row()
        self.answer = button("Send answer", self.answer_question, name="primary")
        self.approve = button("Approve", lambda: self.answer_approval(ApprovalDecision.APPROVED), name="primary")
        self.decline = button("Decline", lambda: self.answer_approval(ApprovalDecision.DECLINED))
        self.refresh = button("Read action preview", self.review_action)
        for control in (self.answer, self.approve, self.decline, self.refresh):
            answers.addWidget(control)
        answers.addStretch()
        question_layout.addLayout(answers)
        content.addWidget(self.question_frame)
        self.prompt_frame = QtWidgets.QFrame()
        composer = column(self.prompt_frame)
        self.prompt_label = label("")
        composer.addWidget(self.prompt_label)
        self.prompt = Editor()
        self.prompt.setAccessibleName("Task instructions")
        self.prompt.setMaximumHeight(140)
        composer.addWidget(self.prompt)
        send = row()
        send.addWidget(button("Send", self.submit, name="primary"))
        send.addWidget(button("Keep draft", self.hide_composer))
        send.addStretch()
        send.addWidget(label("Ctrl+Enter to send", "muted"))
        composer.addLayout(send)
        content.addWidget(self.prompt_frame)
        self.prompt_frame.hide()
        self.routines = RoutinesPanel(self.notebook, work, clock)
        self.notebook.addTab(self.routines.frame, "Standing instructions")
        self.context = ContextPanel(self.notebook, work)
        self.notebook.addTab(self.context.frame, "Context")
        self.tasks.itemSelectionChanged.connect(self.render_task)
        self.tasks.itemActivated.connect(lambda: self.compose_selected())  # noqa: PLW0108  # Discard the signal's row and column arguments.
        self.prompt.submitted.connect(self.submit)
        self.reply.submitted.connect(self.answer_question)
        self.detail.painted.connect(self.preview_paint)
        self.detail_scroll.valueChanged.connect(self.invalidate_preview)
        self.notebook.currentChanged.connect(self.invalidate_preview)
        self.notebook.currentChanged.connect(self.meeting_changed)
        self.handle = int(self.window.winId())
        self.render(self.snapshot)

    def selected_task(self) -> Task | None:
        identity = _selected(self.tasks)
        return next((task for task in self.snapshot.tasks if task.id == identity), None)

    def render(self, snapshot: WorkSnapshot) -> None:
        self.snapshot = snapshot
        self.status.setText(snapshot.status or "Tasks stay with you across conversations and restarts.")
        _rows(self.tasks, tuple((task.id, task.title, (task_label(task),)) for task in snapshot.tasks))
        self.render_task()
        self.routines.render(snapshot.routines)
        self.context.render(snapshot.context)

    def render_task(self) -> None:
        task = self.selected_task()
        detail = task_detail(task, self.snapshot.reviews) if task is not None else "Start a task or create a standing instruction."
        self.preview_painted = False
        replace_text(self.detail, detail)
        review = current_review(task, self.snapshot.reviews) if task is not None else None
        self.approval_position = units(detail[:-len(f"Approval\n{review.title}\n\n{review.detail}")]) if review is not None else None
        allowed = allowed_controls(task.state) if task is not None else ()
        for widget, control in ((self.pause, Control.PAUSE), (self.resume, Control.RESUME), (self.cancel, Control.CANCEL)):
            widget.setEnabled(control in allowed)
        scoped = task is not None and isinstance(task.source, CanvasSource)
        self.follow.setEnabled(task is not None and not scoped)
        self.redirect.setEnabled(task is not None and not scoped and not isinstance(task.state, Completed)
                                 and task.state is not TaskStatus.CANCELLED)
        guidance = "Use the canvas composer or Refresh result to change this request." if scoped else ""
        self.follow.setToolTip(guidance)
        self.redirect.setToolTip(guidance)
        request = task.state.reason if task is not None and isinstance(task.state, Waiting) else None
        key = (task.id, request.id) if task is not None and isinstance(request, (Question, Approval)) else None
        if key != self.question_key:
            self.reply.clear()
            self.question_key = key
        asking = isinstance(request, Question)
        approving = isinstance(request, Approval)
        self.question_frame.setVisible(asking or approving)
        self.reply.setVisible(asking)
        self.answer.setVisible(asking)
        self.approve.setVisible(approving)
        self.decline.setVisible(approving)
        self.refresh.setVisible(approving)
        self.approve.setEnabled(False)
        if isinstance(request, Question):
            self.question.setText(request.text)
        elif isinstance(request, Approval):
            self.question.setText(review.title if review is not None else "A fresh action preview is required.")
            self.refresh.setText("Read action preview" if review is not None else "Request fresh preview")
        self.acknowledge_approval()

    def invalidate_preview(self) -> None:
        self.preview_painted = False
        self.detail.viewport().update()

    def preview_paint(self) -> None:
        self.preview_painted = True
        self.acknowledge_approval()

    def acknowledge_approval(self) -> None:
        task = self.selected_task()
        review = current_review(task, self.snapshot.reviews) if task is not None else None
        if (not self.preview_painted or review is None or self.approval_position is None
                or not self.detail.isVisible() or not windows.is_visible(self.handle)):
            return
        cursor = QtGui.QTextCursor(self.detail.document())
        cursor.setPosition(min(self.approval_position, self.detail.document().characterCount() - 1))
        visible = self.detail.viewport().rect().intersects(self.detail.cursorRect(cursor))
        if visible and self.work.approval_presented(review.task_id, review.request.id):
            self.approve.setEnabled(True)

    def control(self, control: Control) -> None:
        task = self.selected_task()
        if task is not None and control in allowed_controls(task.state):
            self.work.send(ControlTask(task.id, control))

    def compose_selected(self, *, redirect: bool = False) -> None:
        task = self.selected_task()
        if task is not None:
            if isinstance(task.source, CanvasSource):
                self.status.setText("Use the canvas composer or Refresh result to change this request.")
                return
            self.compose(RedirectDraft(task.id) if redirect else FollowDraft(task.id))

    def compose(self, draft: Draft) -> None:
        self.drafts[self.draft] = self.prompt.toPlainText()
        self.draft = draft
        match draft:
            case NewDraft.NEW:
                title = "New task"
            case FollowDraft(task_id) | RedirectDraft(task_id):
                task = next((item for item in self.snapshot.tasks if item.id == task_id), None)
                title = ("Follow up · " if isinstance(draft, FollowDraft) else "Updated instructions · ") + (task.title if task else task_id)
            case _:
                assert_never(draft)
        self.prompt_label.setText(title)
        self.prompt.setPlainText(self.drafts.get(draft, ""))
        self.prompt_frame.show()
        self.prompt.setFocus()

    def hide_composer(self) -> None:
        self.drafts[self.draft] = self.prompt.toPlainText()
        self.prompt_frame.hide()

    def submit(self) -> None:
        try:
            prompt = _required(self.prompt.toPlainText(), "Instructions", 64_000)
        except ValueError as error:
            self.status.setText(str(error))
            return
        command: Submit | FollowUp | Redirect
        match self.draft:
            case NewDraft.NEW:
                command = Submit(prompt)
            case FollowDraft(task_id):
                command = FollowUp(task_id, prompt)
            case RedirectDraft(task_id):
                command = Redirect(task_id, prompt)
            case _:
                assert_never(self.draft)
        if self.work.send(command):
            self.prompt.clear()
            self.drafts.pop(self.draft, None)
            self.prompt_frame.hide()

    def answer_question(self) -> None:
        if self.question_key is None:
            return
        try:
            answer = TextAnswer(self.reply.toPlainText().strip())
        except ValueError as error:
            self.status.setText(str(error))
            return
        task_id, request_id = self.question_key
        if self.work.send(AnswerTask(task_id, request_id, answer)):
            self.reply.clear()

    def answer_approval(self, decision: ApprovalDecision) -> None:
        if self.question_key is not None:
            task_id, request_id = self.question_key
            self.work.send(AnswerTask(task_id, request_id, decision))

    def review_action(self) -> None:
        task = self.selected_task()
        if task is not None and isinstance(task.state, Waiting) and isinstance(task.state.reason, Approval):
            if current_review(task, self.snapshot.reviews) is None:
                self.work.send(RefreshApproval(task.id, task.state.reason.id))
            elif self.approval_position is not None:
                cursor = self.detail.textCursor()
                cursor.setPosition(self.approval_position)
                self.detail.setTextCursor(cursor)
                self.detail.ensureCursorVisible()
                self.detail.viewport().update()

    def show(self, *, focus: bool = True) -> None:
        show_window(self.window, focus=focus, target=self.tasks)
        self.meeting_changed()

    def attach_meetings(self, meetings: MeetingPanel) -> None:
        if self.meetings is None:
            self.meetings = meetings
            self.notebook.addTab(meetings, "Meetings")

    def meeting_changed(self) -> None:
        if self.meetings is not None and self.notebook.currentWidget() is self.meetings:
            self.meetings.refresh()

    def show_meetings(self) -> None:
        if self.meetings is not None:
            self.notebook.setCurrentWidget(self.meetings)
            self.meetings.refresh()

    def hide(self) -> None:
        self.window.hide()

    def close(self) -> None:
        self.window.hide()
        self.window.deleteLater()


class RoutinesPanel:
    __slots__ = (
        "__weakref__",
        "clock",
        "editing",
        "editor",
        "frame",
        "instruction",
        "listing",
        "message",
        "parameter",
        "parameter_label",
        "records",
        "summary",
        "title",
        "toggle",
        "trigger",
        "work",
    )

    TRIGGERS = ("After a transcript finishes", "At a date and time", "Every interval", "When a file changes")

    def __init__(self, parent: QtWidgets.QTabWidget, work: WorkWindow, clock: Callable[[], float]) -> None:
        self.work, self.clock = work, clock
        self.records: tuple[RoutineSummary, ...] = ()
        self.editing: Routine | None = None
        self.frame = QtWidgets.QWidget(parent)
        layout = column(self.frame)
        layout.setContentsMargins(10, 12, 10, 8)
        toolbar = row()
        toolbar.addWidget(button("New instruction", self.create))
        toolbar.addWidget(button("Edit", self.edit))
        self.toggle = button("Enable", self.enable)
        toolbar.addWidget(self.toggle)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        body = row()
        layout.addLayout(body, 1)
        self.listing = _tree("State")
        body.addWidget(self.listing, 2)
        right = QtWidgets.QWidget()
        content = column(right)
        body.addWidget(right, 3)
        self.summary = Editor(readonly=True)
        content.addWidget(self.summary, 1)
        self.editor = QtWidgets.QWidget()
        editor = column(self.editor)
        self.title = QtWidgets.QLineEdit()
        _form(editor, "Name", self.title)
        self.instruction = Editor()
        _form(editor, "Standing instruction", self.instruction)
        self.trigger = QtWidgets.QComboBox()
        self.trigger.addItems(self.TRIGGERS)
        _form(editor, "Start when", self.trigger)
        self.parameter = QtWidgets.QLineEdit()
        self.parameter_label = _form(editor, "", self.parameter)
        buttons = row()
        buttons.addWidget(button("Save", self.save, name="primary"))
        buttons.addWidget(button("Cancel edit", self.cancel_edit))
        buttons.addStretch()
        editor.addLayout(buttons)
        content.addWidget(self.editor, 1)
        self.message = label("", "muted")
        self.message.setWordWrap(True)
        content.addWidget(self.message)
        self.editor.hide()
        self.listing.itemSelectionChanged.connect(self.render_selection)
        self.trigger.currentIndexChanged.connect(self.trigger_changed)

    def selected(self) -> RoutineSummary | None:
        identity = _selected(self.listing)
        return next((item for item in self.records if item.routine.id == identity), None)

    def render(self, records: tuple[RoutineSummary, ...]) -> None:
        self.records = records
        _rows(self.listing, tuple((item.routine.id, item.routine.title, ("Enabled" if item.routine.enabled else "Disabled",)) for item in records))
        self.render_selection()

    def render_selection(self) -> None:
        item = self.selected()
        self.toggle.setEnabled(item is not None)
        self.toggle.setText("Disable" if item is not None and item.routine.enabled else "Enable")
        if item is None:
            detail = "Standing instructions start work when their trigger occurs.\n\nSave an instruction, then enable it when ready."
        else:
            routine = item.routine
            detail = (f"{routine.title}\n\n{'Enabled' if routine.enabled else 'Disabled'} · {trigger_label(routine.trigger)}"
                      f"\nNext due: {when(item.next_due)}\n\nInstruction\n{routine.instruction}")
            if item.last_firing is not None:
                detail += "\n\nLast run\n" + (item.last_firing.task_id or "Preparing a task")
        replace_text(self.summary, detail)

    def enable(self) -> None:
        item = self.selected()
        if item is not None:
            self.work.send(DisableRoutine(item.routine.id) if item.routine.enabled else EnableRoutine(item.routine.id))

    def create(self) -> None:
        self.editing = None
        self.open_editor()

    def edit(self) -> None:
        item = self.selected()
        if item is not None:
            self.editing = item.routine
            self.open_editor()

    def open_editor(self) -> None:
        self.title.clear()
        self.instruction.clear()
        self.parameter.clear()
        item = self.editing
        message = "Saving does not enable a new instruction."
        if item is not None:
            self.title.setText(item.title)
            self.instruction.setPlainText(item.instruction)
        trigger = item.trigger if item is not None else EventTrigger.TRANSCRIPT_COMPLETED
        match trigger:
            case EventTrigger.TRANSCRIPT_COMPLETED:
                self.trigger.setCurrentIndex(0)
            case Once(at):
                self.trigger.setCurrentIndex(1)
                try:
                    self.parameter.setText(datetime.fromtimestamp(at).isoformat(sep=" ", timespec="seconds"))
                except (ValueError, OverflowError, OSError):
                    message = "This saved date is outside the local calendar. Choose a date and time before saving."
            case Every(seconds=seconds):
                self.trigger.setCurrentIndex(2)
                self.parameter.setText(f"{seconds / 60:g}")
            case FileChanged(path):
                self.trigger.setCurrentIndex(3)
                self.parameter.setText(path)
            case _:
                assert_never(trigger)
        self.trigger_changed()
        self.summary.hide()
        self.editor.show()
        self.message.setText(message)
        self.title.setFocus()

    def trigger_changed(self) -> None:
        index = self.trigger.currentIndex()
        labels = ("", "Local date and time · YYYY-MM-DD HH:MM", "Interval in minutes", "Absolute path to the file")
        self.parameter_label.setText(labels[index] if 0 <= index < len(labels) else "")
        self.parameter_label.setVisible(index != 0)
        self.parameter.setVisible(index != 0)

    def save(self) -> None:
        try:
            title = _required(self.title.text(), "Name", 160)
            instruction = _required(self.instruction.toPlainText(), "Instruction", 16_000)
            parameter = self.parameter.text().strip()
            match self.trigger.currentIndex():
                case 0:
                    trigger: Trigger = EventTrigger.TRANSCRIPT_COMPLETED
                case 1:
                    try:
                        at = datetime.fromisoformat(parameter).timestamp()
                    except (ValueError, OverflowError, OSError) as error:
                        raise ValueError("Enter a date and time such as 2026-11-01 09:00.") from error
                    trigger = Once(at)
                case 2:
                    seconds = _seconds(parameter, "Interval")
                    if not 60 <= seconds <= 365 * 24 * 60 * 60:
                        raise ValueError("Interval must be between one minute and one year.")
                    existing = self.editing.trigger if self.editing is not None else None
                    trigger = existing if isinstance(existing, Every) and existing.seconds == seconds else Every(self.clock(), seconds)
                case 3:
                    trigger = FileChanged(_required(parameter, "File path", 4096))
                case _:
                    raise ValueError("Choose a trigger.")
            command = (UpdateRoutine(self.editing.id, self.editing.revision, title, instruction, trigger)
                       if self.editing is not None else CreateRoutine(title, instruction, trigger))
        except ValueError as error:
            self.message.setText(str(error))
            return
        if self.work.send(command):
            self.cancel_edit()
            self.message.setText("Save requested. The list updates when the instruction is saved.")

    def cancel_edit(self) -> None:
        self.editor.hide()
        self.summary.show()
        self.editing = None
        self.message.clear()


class ContextPanel:
    __slots__ = (
        "__weakref__",
        "editing",
        "editor",
        "expires",
        "expires_label",
        "frame",
        "kind",
        "listing",
        "message",
        "name",
        "name_label",
        "records",
        "source",
        "summary",
        "value",
        "work",
    )

    def __init__(self, parent: QtWidgets.QTabWidget, work: WorkWindow) -> None:
        self.work = work
        self.records: tuple[ContextRecord, ...] = ()
        self.editing: ContextRecord | None = None
        self.frame = QtWidgets.QWidget(parent)
        layout = column(self.frame)
        layout.setContentsMargins(10, 12, 10, 8)
        toolbar = row()
        for title, callback in (("Add context", self.create), ("Edit", self.edit), ("Remove", self.remove)):
            toolbar.addWidget(button(title, callback))
        toolbar.addStretch()
        layout.addLayout(toolbar)
        body = row()
        layout.addLayout(body, 1)
        self.listing = _tree("Kind")
        body.addWidget(self.listing, 2)
        right = QtWidgets.QWidget()
        content = column(right)
        body.addWidget(right, 3)
        self.summary = Editor(readonly=True)
        content.addWidget(self.summary, 1)
        self.editor = QtWidgets.QWidget()
        editor = column(self.editor)
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(("Preference", "Project note"))
        _form(editor, "Kind", self.kind)
        self.name = QtWidgets.QLineEdit()
        self.name_label = _form(editor, "Name", self.name)
        self.value = Editor()
        _form(editor, "Value or note", self.value)
        self.source = QtWidgets.QLineEdit()
        _form(editor, "Source", self.source)
        self.expires = QtWidgets.QLineEdit()
        self.expires_label = _form(editor, "Refresh after this many minutes", self.expires)
        buttons = row()
        buttons.addWidget(button("Save", self.save, name="primary"))
        buttons.addWidget(button("Cancel edit", self.cancel_edit))
        buttons.addStretch()
        editor.addLayout(buttons)
        content.addWidget(self.editor, 1)
        self.message = label("", "muted")
        self.message.setWordWrap(True)
        content.addWidget(self.message)
        self.editor.hide()
        self.listing.itemSelectionChanged.connect(self.render_selection)
        self.kind.currentIndexChanged.connect(self.kind_changed)

    def selected(self) -> ContextRecord | None:
        identity = _selected(self.listing)
        return next((item for item in self.records if item.id == identity), None)

    def render(self, records: tuple[ContextRecord, ...]) -> None:
        self.records = records
        _rows(self.listing, tuple((item.id, item.name, ("Preference",)) if isinstance(item, Preference)
                                 else (item.id, item.project, ("Project note",)) for item in records))
        self.render_selection()

    def render_selection(self) -> None:
        item = self.selected()
        match item:
            case None:
                detail = "Save preferences and sourced project notes here.\n\nProject notes have an explicit refresh time."
            case Preference(_, name, value, source, at):
                detail = f"{name}\n\nPreference · updated {when(at)}\n\n{value}\n\nSource\n{source}"
            case ProjectNote(_, project, text, source, at, expires):
                detail = f"{project}\n\nProject note · observed {when(at)}\nRefresh after {when(expires)}\n\n{text}\n\nSource\n{source}"
            case _:
                assert_never(item)
        replace_text(self.summary, detail)

    def create(self) -> None:
        self.editing = None
        self.open_editor()

    def edit(self) -> None:
        if (item := self.selected()) is not None:
            self.editing = item
            self.open_editor()

    def remove(self) -> None:
        if (item := self.selected()) is not None:
            self.work.send(RemoveContext(item.id))

    def open_editor(self) -> None:
        self.name.clear()
        self.value.clear()
        self.source.clear()
        self.expires.clear()
        match self.editing:
            case None:
                self.kind.setCurrentIndex(0)
                self.source.setText("User")
                self.expires.setText("10080")
            case Preference(_, name, value, source, _):
                self.kind.setCurrentIndex(0)
                self.name.setText(name)
                self.value.setPlainText(value)
                self.source.setText(source)
            case ProjectNote(_, project, text, source, at, expires):
                self.kind.setCurrentIndex(1)
                self.name.setText(project)
                self.value.setPlainText(text)
                self.source.setText(source)
                self.expires.setText(f"{(expires - at) / 60:g}")
            case _:
                assert_never(self.editing)
        self.kind.setEnabled(self.editing is None)
        self.kind_changed()
        self.summary.hide()
        self.editor.show()
        self.message.clear()
        self.name.setFocus()

    def kind_changed(self) -> None:
        project = self.kind.currentIndex() == 1
        self.name_label.setText("Project" if project else "Preference name")
        self.expires_label.setVisible(project)
        self.expires.setVisible(project)

    def save(self) -> None:
        try:
            name = _required(self.name.text(), "Name", 160)
            value = _required(self.value.toPlainText(), "Value", 4000)
            source = _required(self.source.text(), "Source", 1000)
            command: AddPreference | UpdatePreference | AddProjectNote | UpdateProjectNote
            if self.kind.currentIndex() == 1:
                valid_for = _seconds(self.expires.text(), "Refresh interval")
                command = (UpdateProjectNote(self.editing.id, name, value, source, valid_for)
                           if self.editing is not None else AddProjectNote(name, value, source, valid_for))
            elif self.kind.currentIndex() == 0:
                command = (UpdatePreference(self.editing.id, name, value, source)
                           if self.editing is not None else AddPreference(name, value, source))
            else:
                raise ValueError("Choose a context kind.")
        except ValueError as error:
            self.message.setText(str(error))
            return
        if self.work.send(command):
            self.cancel_edit()
            self.message.setText("Save requested. The list updates when the context is saved.")

    def cancel_edit(self) -> None:
        self.editor.hide()
        self.summary.show()
        self.editing = None
        self.message.clear()
