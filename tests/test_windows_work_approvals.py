"""Only the actual native approval view may acknowledge a durable review."""

import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native approval visibility")

from assistant.agent_session import Approval

from .test_windows_agent_panel import native_panel
from .test_windows_agent_toasts import notifications


def test_hidden_history_and_offscreen_requests_do_not_authorize_voice_approval() -> None:
    replies: list[str] = []

    def accept(answer: str) -> bool:
        replies.append(answer)
        return True

    with native_panel() as panel:
        first = Approval("first", "Write the workbook", "The complete reviewed content. Åäö.", 1.0)
        panel.agent.request_input(first, accept)
        panel.pump()
        assert not panel.view.approval_visible(first.request_id)
        assert not panel.agent.respond(first.request_id, "approve")
        panel.agent.show(focus=False)
        panel.pump()
        assert panel.view.approval_visible(first.request_id)
        assert panel.agent.reply_by_voice("Approve.") and replies == ["approve"]
        panel.agent.say("Earlier conversation content.\n" * 100)
        panel.pump()
        panel.view.scroll.setValue(0)
        panel.pump()
        second = Approval("second", "Send the draft", "The complete recipients and content.", 2.0)
        panel.agent.request_input(second, accept)
        panel.pump()
        assert not panel.view.approval_visible(second.request_id)
        assert not panel.agent.respond(second.request_id, "approve")
        panel.pump()
        panel.view.scroll.setValue(panel.view.scroll.maximum())
        panel.pump()
        assert panel.view.approval_visible(second.request_id)
        assert panel.agent.respond(second.request_id, "approve") and replies == ["approve", "approve"]


def test_toast_approval_waits_for_a_visible_painted_card_and_a_fresh_request() -> None:
    replies: list[str] = []

    def accept(answer: str) -> bool:
        replies.append(answer)
        return True

    with notifications(animated=True) as panel:
        first = Approval("first", "Write the workbook", "The complete reviewed content. Åäö.", 1.0)
        panel.agent.request_input(first, accept)
        assert not panel.agent.respond(first.request_id, "approve")
        panel.pump()
        assert not panel.view.approval_visible(first.request_id)
        assert not panel.agent.respond(first.request_id, "approve")
        panel.advance(1.0)
        assert panel.view.approval_visible(first.request_id)
        assert panel.agent.reply_by_voice("Approve!") and replies == ["approve"]
        panel.pump()
        second = Approval("second", "Send the draft", "The complete recipients and content.", 2.0)
        panel.agent.request_input(second, accept)
        assert not panel.agent.respond(second.request_id, "approve"), "The previous visible card cannot acknowledge the new request."
        panel.pump()
        panel.advance(1.0)
        assert panel.view.approval_visible(second.request_id)
        assert panel.agent.respond(second.request_id, "approve") and replies == ["approve", "approve"]
