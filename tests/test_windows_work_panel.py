"""Native work controls, drafts, reading position, and exact approval previews."""

import sys
import threading
import time
import unittest
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native work overview")

from assistant.qt import Qt, QtCore, QtGui, QtWidgets
from assistant.qt_ui import application
from assistant.work_panel import WorkPanel
from assistant.work_window import (
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
    UpdateRoutine,
    WorkCommand,
    WorkSnapshot,
    WorkWindow,
)
from automation.context import Preference, ProjectNote
from automation.routines import Disabled, Enabled, Every, Routine, RoutineSummary
from automation.task import ApprovalDecision, Completed, Control, Question, TaskStatus, TextAnswer, Waiting
from winapi import windows

from .test_work_window import approval_task, work_task


def button(parent: QtWidgets.QWidget, caption: str) -> QtWidgets.QToolButton:
    for candidate in parent.findChildren(QtWidgets.QToolButton):
        if candidate.text() == caption:
            return candidate
    raise AssertionError(f"No {caption!r} button in {parent}")


def enter(widget: QtWidgets.QLineEdit, text: str) -> None:
    widget.setText(text)


def select(tree: QtWidgets.QTreeWidget, identity: str) -> None:
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item is None:
            continue
        value: object = item.data(0, Qt.ItemDataRole.UserRole)
        if value == identity:
            tree.setCurrentItem(item)
            tree.scrollToItem(item)
            return
    raise AssertionError(f"No row {identity}")


def scroll_position(widget: QtWidgets.QAbstractScrollArea) -> int:
    return widget.verticalScrollBar().value()


class NativeWork:
    def __init__(self) -> None:
        self.root = application()
        self.jobs: deque[Callable[[], None]] = deque()
        self.commands: list[WorkCommand] = []
        self.view: WorkPanel
        self.work = WorkWindow(self.commands.append, post=self.jobs.append, create_view=self.build)
        self.work.show(focus=False)
        self.pump()
        assert not self.work.state.status, self.work.state.status

    def build(self, work: WorkWindow) -> WorkPanel:
        self.view = WorkPanel(self.root, work, clock=lambda: 1000.0)
        return self.view

    def pump(self) -> None:
        while self.jobs:
            self.jobs.popleft()()
        self.root.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def publish(self, snapshot: WorkSnapshot) -> None:
        self.work.publish(snapshot)
        self.pump()

    def last_command(self) -> WorkCommand:
        return self.commands[-1]

    def close(self) -> None:
        self.work.close()
        self.pump()
        self.root.processEvents()


@contextmanager
def native_work() -> Generator[NativeWork]:
    panel = NativeWork()
    try:
        yield panel
    finally:
        panel.close()


def activate_work(panel: NativeWork) -> bool:
    completed = threading.Event()
    focused: list[bool] = []
    errors: list[Exception] = []

    def activate() -> None:
        try:
            focused.append(windows.focus(panel.view.handle, timeout_s=3.0))
        except Exception as error:
            errors.append(error)
        finally:
            completed.set()

    worker = threading.Thread(target=activate, name="native work focus")
    worker.start()
    deadline = time.monotonic() + 5.0
    while not completed.is_set() and time.monotonic() < deadline:
        panel.pump()
    worker.join(timeout=1.0)
    assert completed.is_set() and not worker.is_alive(), "Native focus did not finish before testing Hide."
    if errors:
        raise errors[0]
    return focused == [True] and windows.foreground() == panel.view.handle


def test_quiet_reopen_does_not_reactivate_a_previously_focused_work_window() -> None:
    with native_work() as panel, native_work() as background:
        if not activate_work(panel):
            raise unittest.SkipTest("Foreground activation is unavailable on this desktop.")
        panel.view.hide()
        panel.pump()
        # Hiding the active window briefly leaves GetForegroundWindow at zero.
        # Establish another owned window before measuring quiet reopening.
        if not activate_work(background):
            raise unittest.SkipTest("Foreground activation is unavailable on this desktop.")
        foreground = background.view.handle
        assert not windows.is_visible(panel.view.handle)
        panel.work.show(focus=False)
        panel.pump()
        assert windows.is_visible(panel.view.handle)
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            panel.pump()
            assert windows.foreground() == foreground, f"Quiet Show changed foreground: before={foreground}; after={windows.foreground()}"
            time.sleep(0.01)


def test_native_task_controls_and_composer_send_the_selected_task_identity() -> None:
    with native_work() as panel:
        view = panel.view
        task = work_task()
        panel.publish(WorkSnapshot(tasks=(task,)))
        view.resume.click()
        assert not panel.commands
        view.pause.click()
        assert panel.commands == [ControlTask(task.id, Control.PAUSE)]
        panel.publish(WorkSnapshot(tasks=(replace(task, state=TaskStatus.PAUSED),)))
        view.pause.click()
        view.resume.click()
        view.cancel.click()
        assert panel.commands[-2:] == [ControlTask(task.id, Control.RESUME), ControlTask(task.id, Control.CANCEL)]
        view.follow.click()
        view.prompt.insertPlainText("Explain the total\nwith the complete ending. Åäö.")
        button(view.prompt_frame, "Send").click()
        assert panel.last_command() == FollowUp(task.id, "Explain the total\nwith the complete ending. Åäö.")
        view.redirect.click()
        view.prompt.insertPlainText("Use the 2026 workbook instead.")
        button(view.prompt_frame, "Send").click()
        assert panel.last_command() == Redirect(task.id, "Use the 2026 workbook instead.")
        button(view.window, "New task").click()
        view.prompt.insertPlainText("Open the saved result.")
        button(view.prompt_frame, "Send").click()
        assert panel.last_command() == Submit("Open the saved result.")
        panel.publish(WorkSnapshot(tasks=(replace(task, state=Completed("Done", ("File verified",))),)))
        before = len(panel.commands)
        for widget in (view.pause, view.resume, view.cancel, view.redirect):
            widget.click()
        assert len(panel.commands) == before


def test_background_render_preserves_selection_scroll_draft_and_hidden_state() -> None:
    with native_work() as panel:
        view = panel.view
        result = "A line of complete analysis.\n" * 160 + "The complete ending. Åäö."
        tasks = tuple(replace(work_task(Completed(result, ("Verified",)), identity=f"task-{index}"), title=f"Workbook {index}")
                      for index in range(80))
        panel.publish(WorkSnapshot(tasks=tasks))
        select(view.tasks, "task-40")
        panel.pump()
        view.follow.click()
        view.prompt.insertPlainText("Preserve this unfinished follow-up. Åäö.")
        cursor = view.detail.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(8, QtGui.QTextCursor.MoveMode.KeepAnchor)
        view.detail.setTextCursor(cursor)
        selected = cursor.selectedText()
        view.detail.verticalScrollBar().setValue(round(view.detail.verticalScrollBar().maximum() * .4))
        panel.pump()
        detail_scroll = scroll_position(view.detail)
        list_scroll = scroll_position(view.tasks)
        before_hide = windows.foreground()
        view.hide()
        panel.pump()
        assert not windows.is_visible(view.handle), "Hide did not withdraw the Work window."
        # Hiding an active window hands focus elsewhere. Measure background work
        # after that explicit user action and its Qt events have completed.
        foreground = windows.foreground()
        revised = tuple(replace(task, state=Completed(result + "!", ("Verified",))) if task.id == "task-40" else task for task in tasks)
        panel.publish(WorkSnapshot(tasks=revised, status="A background update"))
        current = view.tasks.currentItem()
        assert current is not None
        identity: object = current.data(0, Qt.ItemDataRole.UserRole)
        assert identity == "task-40"
        assert view.detail.textCursor().selectedText() == selected
        assert abs(scroll_position(view.detail) - detail_scroll) < .01
        assert abs(scroll_position(view.tasks) - list_scroll) < .01
        assert view.prompt.toPlainText() == "Preserve this unfinished follow-up. Åäö."
        assert "The complete ending. Åäö.!" in view.detail.toPlainText()
        after_render = windows.foreground()
        assert not windows.is_visible(view.handle), "Background publication revealed the Work window."
        assert after_render == foreground, f"Background changed foreground: work={view.handle}; {before_hide=}; {foreground=}; {after_render=}"
        panel.work.show(focus=False)
        panel.pump()
        assert windows.is_visible(view.handle), "Explicit Show did not reveal the Work window."
        assert windows.foreground() == foreground, f"Quiet Show changed foreground: before={foreground}; after={windows.foreground()}"
        view.window.resize(740, 500)
        panel.pump()
        for widget in (view.pause, view.resume, view.cancel, view.follow, view.redirect):
            assert widget.mapTo(view.window, QtCore.QPoint(widget.width(), 0)).x() <= view.window.width()


def test_routine_editor_retains_edits_and_validates_before_submitting() -> None:
    with native_work() as panel:
        view = panel.view.routines
        panel.view.notebook.setCurrentWidget(view.frame)
        routine = Routine("routine", 2, "Read the workbook", "Summarize changes.", Every(100.0, 120), Disabled.DISABLED, 100.0, 200.0)
        summary = RoutineSummary(routine, None, None)
        panel.publish(WorkSnapshot(routines=(summary,)))
        button(view.frame, "Edit").click()
        enter(view.title, "A revised name")
        panel.publish(WorkSnapshot(routines=(summary,), status="Background refresh"))
        assert view.title.text() == "A revised name" and view.parameter.text() == "2"
        button(view.editor, "Save").click()
        assert panel.last_command() == UpdateRoutine(routine.id, 2, "A revised name", routine.instruction, Every(100.0, 120))
        view.toggle.click()
        assert panel.last_command() == EnableRoutine(routine.id)
        enabled = replace(summary, routine=replace(routine, state=Enabled(200.0)))
        panel.publish(WorkSnapshot(routines=(enabled,)))
        view.toggle.click()
        assert panel.last_command() == DisableRoutine(routine.id)
        button(view.frame, "New instruction").click()
        enter(view.title, "New routine")
        view.instruction.insertPlainText("Read the latest workbook.")
        view.trigger.setCurrentIndex(2)
        enter(view.parameter, "0.5")
        before = len(panel.commands)
        button(view.editor, "Save").click()
        assert len(panel.commands) == before and not view.editor.isHidden()
        assert "one minute" in view.message.text()
        enter(view.parameter, "5")
        button(view.editor, "Save").click()
        assert panel.last_command() == CreateRoutine("New routine", "Read the latest workbook.", Every(1000.0, 300))


def test_context_editor_preserves_source_and_explicit_project_refresh_time() -> None:
    with native_work() as panel:
        view = panel.view.context
        panel.view.notebook.setCurrentWidget(view.frame)
        button(view.frame, "Add context").click()
        enter(view.name, "Preferred language")
        view.value.insertPlainText("Swedish")
        button(view.editor, "Save").click()
        assert panel.last_command() == AddPreference("Preferred language", "Swedish", "User")
        preference = Preference("preference", "Preferred language", "Swedish", "User", 100.0)
        panel.publish(WorkSnapshot(context=(preference,)))
        button(view.frame, "Edit").click()
        view.value.clear()
        view.value.insertPlainText("English")
        enter(view.source, "User correction")
        panel.publish(WorkSnapshot(context=(preference,), status="Background refresh"))
        assert view.value.toPlainText() == "English" and not view.kind.isEnabled()
        button(view.editor, "Save").click()
        assert panel.last_command() == UpdatePreference(preference.id, preference.name, "English", "User correction")
        button(view.frame, "Add context").click()
        view.kind.setCurrentIndex(1)
        enter(view.name, "Toolkit")
        view.value.insertPlainText("The workbook is in the project folder.")
        enter(view.source, "Project README")
        enter(view.expires, "30")
        button(view.editor, "Save").click()
        assert panel.last_command() == AddProjectNote("Toolkit", "The workbook is in the project folder.", "Project README", 1800)
        note = ProjectNote("note", "Toolkit", "The workbook is in the project folder.", "Project README", 100.0, 1900.0)
        panel.publish(WorkSnapshot(context=(preference, note)))
        select(view.listing, note.id)
        panel.pump()
        button(view.frame, "Remove").click()
        assert panel.last_command() == RemoveContext(note.id)


def test_approval_is_disabled_until_its_live_preview_is_actually_visible() -> None:
    with native_work() as panel:
        view = panel.view
        task, review = approval_task(detail="The full reviewed file content. Åäö.")
        task = replace(task, prompt="A long instruction before the preview.\n" * 80)
        panel.publish(WorkSnapshot(tasks=(task,), reviews=(review,)))
        approve = AnswerTask(task.id, review.request.id, ApprovalDecision.APPROVED)
        assert review.detail in view.detail.toPlainText()
        assert not panel.work.send(approve)
        view.approve.click()
        assert not panel.commands and not view.approve.isEnabled()
        view.refresh.click()
        panel.pump()
        assert view.approve.isEnabled()
        view.approve.click()
        assert panel.commands == [approve]
        view.hide()
        fresh, fresh_review = approval_task(nonce="fresh")
        panel.publish(WorkSnapshot(tasks=(fresh,), reviews=(fresh_review,)))
        assert not panel.work.send(AnswerTask(fresh.id, fresh_review.request.id, ApprovalDecision.APPROVED))
        assert not windows.is_visible(view.handle)
        panel.publish(WorkSnapshot(tasks=(fresh,)))
        assert "fresh preview" in view.detail.toPlainText()
        assert fresh_review.request.detail not in view.detail.toPlainText()
        view.refresh.click()
        assert panel.last_command() == RefreshApproval(fresh.id, fresh_review.request.id)
        view.decline.click()
        assert panel.last_command() == AnswerTask(fresh.id, fresh_review.request.id, ApprovalDecision.DECLINED)


def test_question_drafts_survive_refresh_and_clear_only_for_a_different_request() -> None:
    with native_work() as panel:
        view = panel.view
        task = work_task(Waiting(Question("first", "Which workbook?", "Choose the source.")))
        panel.publish(WorkSnapshot(tasks=(task,)))
        view.reply.insertPlainText("Revenue\nwith the complete ending. Åäö.")
        panel.publish(WorkSnapshot(tasks=(task,), status="Background refresh"))
        view.pause.click()
        view.resume.click()
        assert not panel.commands
        assert view.reply.toPlainText() == "Revenue\nwith the complete ending. Åäö."
        view.answer.click()
        assert panel.commands == [AnswerTask(task.id, "first", TextAnswer("Revenue\nwith the complete ending. Åäö."))]
        view.reply.insertPlainText("A draft for the old question")
        replacement = replace(task, state=Waiting(Question("second", "Which year?", "")))
        panel.publish(WorkSnapshot(tasks=(replacement,)))
        assert view.reply.toPlainText() == "" and view.question.text() == "Which year?"
        view.reply.insertPlainText("2026")
        view.answer.click()
        assert panel.last_command() == AnswerTask(task.id, "second", TextAnswer("2026"))
