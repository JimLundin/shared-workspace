"""Scoped canvas work keeps its controls without entering the generic composer."""

import sys
import unittest
from dataclasses import replace

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native canvas work controls")

from assistant.work_window import AnswerTask, ControlTask, FollowUp, Redirect, WorkSnapshot
from automation.task import CanvasSource, Control, Question, TaskStatus, TextAnswer, Waiting

from .test_windows_work_panel import NativeWork, button, native_work, select
from .test_work_window import work_task


def assert_scoped_composer_is_blocked(panel: NativeWork) -> None:
    view = panel.view
    assert not view.follow.isEnabled() and not view.redirect.isEnabled()
    selected = view.tasks.currentItem()
    assert selected is not None
    view.hide_composer()
    before = tuple(panel.commands)
    for activate in (
        view.follow.click,
        view.redirect.click,
        lambda: view.tasks.itemActivated.emit(selected, 0),
        view.compose_selected,
        lambda: view.compose_selected(redirect=True),
    ):
        activate()
        panel.pump()
        assert view.prompt_frame.isHidden(), "Scoped work opened the generic composer."
        assert tuple(panel.commands) == before, "Scoped composer activation submitted a command."


def test_scoped_work_blocks_generic_composition_but_keeps_controls_and_answers() -> None:
    with native_work() as panel:
        view = panel.view
        ordinary = work_task(identity="ordinary-task")
        scoped = replace(work_task(identity="canvas-task"), source=CanvasSource("a" * 64))
        panel.publish(WorkSnapshot(tasks=(ordinary, scoped)))
        select(view.tasks, scoped.id)
        panel.pump()
        assert_scoped_composer_is_blocked(panel)

        view.pause.click()
        assert panel.commands == [ControlTask(scoped.id, Control.PAUSE)]
        panel.publish(WorkSnapshot(tasks=(ordinary, replace(scoped, state=TaskStatus.PAUSED))))
        view.resume.click()
        assert panel.commands == [ControlTask(scoped.id, Control.PAUSE), ControlTask(scoped.id, Control.RESUME)]

        question = Question("canvas-question", "Which sheet should I use?", "Choose from the pinned workbook.")
        panel.publish(WorkSnapshot(tasks=(ordinary, replace(scoped, state=Waiting(question)))))
        assert view.question_frame.isVisible() and view.reply.isVisible() and view.answer.isEnabled()
        answer = "Use Revenue\nwith the complete ending. Åäö."
        view.reply.insertPlainText(answer)
        view.answer.click()
        assert panel.last_command() == AnswerTask(scoped.id, question.id, TextAnswer(answer))

        select(view.tasks, ordinary.id)
        panel.pump()
        assert view.follow.isEnabled() and view.redirect.isEnabled()
        view.follow.click()
        assert not view.prompt_frame.isHidden()
        view.prompt.insertPlainText("Explain the ordinary task result.")
        button(view.prompt_frame, "Send").click()
        assert panel.last_command() == FollowUp(ordinary.id, "Explain the ordinary task result.")
        view.redirect.click()
        assert not view.prompt_frame.isHidden()
        view.prompt.insertPlainText("Use the revised workbook.")
        button(view.prompt_frame, "Send").click()
        assert panel.last_command() == Redirect(ordinary.id, "Use the revised workbook.")

        selected = view.tasks.currentItem()
        assert selected is not None
        view.tasks.itemActivated.emit(selected, 0)
        panel.pump()
        assert not view.prompt_frame.isHidden(), "Ordinary task activation no longer opens its composer."
        view.prompt.insertPlainText("Summarize the ordinary task.")
        button(view.prompt_frame, "Send").click()
        assert panel.last_command() == FollowUp(ordinary.id, "Summarize the ordinary task.")
        select(view.tasks, scoped.id)
        panel.pump()
        assert_scoped_composer_is_blocked(panel)
