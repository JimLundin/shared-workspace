"""Cancellable waiting for typed questions and approvals; no widget calls."""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import assert_never

from .agent_session import Approval, ApprovalDecided, Event, InputCancelled, Question, QuestionAnswered, Resolution


@dataclass(slots=True)
class _Input:
    request: Question | Approval
    ready: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None


def acceptable(request: Question | Approval, answer: str) -> bool:
    match request:
        case Question():
            return bool(answer.strip())
        case Approval():
            return answer in ("approve", "decline")
        case _:
            assert_never(request)


def resolution(request: Question | Approval, answer: str | None) -> Resolution:
    if answer is None:
        return InputCancelled(request.request_id)
    match request:
        case Question(request_id=identity):
            return QuestionAnswered(identity, answer)
        case Approval(request_id=identity):
            return ApprovalDecided(identity, "approved" if answer == "approve" else "declined")
        case _:
            assert_never(request)


class Inputs:
    """Only an explicit response to this exact request can approve it."""

    __slots__ = ("lock", "pending", "publish")

    def __init__(self, publish: Callable[[Event], None]) -> None:
        self.publish = publish
        self.lock = threading.Lock()
        self.pending: dict[str, _Input] = {}

    def ask(self, title: str, detail: str, *, question: bool, cancel: threading.Event | None) -> str | None:
        if cancel is not None and cancel.is_set():
            return None
        identity, created = uuid.uuid4().hex, time.time()
        message = Question(identity, title.strip(), detail, created) if question else Approval(identity, title.strip(), detail, created)
        request = _Input(message)
        with self.lock:
            self.pending[identity] = request
        try:
            self.publish(message)
            while not request.ready.wait(0.05):
                if cancel is not None and cancel.is_set():
                    break
            answer = None if cancel is not None and cancel.is_set() else request.answer
            self.publish(resolution(message, answer))
            return answer
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def respond(self, identity: str, answer: str) -> bool:
        with self.lock:
            request = self.pending.get(identity)
            if request is None or request.ready.is_set() or not acceptable(request.request, answer):
                return False
            request.answer = answer
            request.ready.set()
            return True

    def cancel_all(self) -> None:
        with self.lock:
            for request in self.pending.values():
                request.answer = None
                request.ready.set()

    def cancel(self, identity: str) -> None:
        with self.lock:
            if (request := self.pending.get(identity)) is not None:
                request.answer = None
                request.ready.set()
