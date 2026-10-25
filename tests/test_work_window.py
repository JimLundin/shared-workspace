"""Saved work reaches a lazy native view through its public immutable boundary."""

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from assistant.work_window import (
    AnswerTask,
    Submit,
    WorkCommand,
    WorkSnapshot,
    WorkView,
    WorkWindow,
    task_detail,
)
from automation.execution import ApprovalReview
from automation.task import (
    Approval,
    ApprovalDecision,
    Checkpoint,
    Completed,
    Interrupted,
    Question,
    RequestSource,
    RunRef,
    State,
    Task,
    TaskStatus,
    TextAnswer,
    Waiting,
)


def work_task(state: State = TaskStatus.READY, *, identity: str = "task-one") -> Task:
    return Task(identity, "Prepare the workbook", "Read every sheet and explain the total.", RequestSource("conversation"),
                0, 100.0, 200.0, 0, state, None)


def approval_task(*, nonce: str = "one", detail: str = "Write the complete reviewed workbook.") -> tuple[Task, ApprovalReview]:
    fingerprint = "a" * 64
    request = Approval(f"approval:0:{fingerprint}:{nonce}", "Review the operation", "Persisted summary cannot authorize execution.")
    task = work_task(Waiting(request))
    return task, ApprovalReview(task.id, 0, request, fingerprint, "Write the workbook", detail)


class RecordingWorkView:
    def __init__(self, *, rendering: Callable[[WorkSnapshot], None] | None = None) -> None:
        self.states: list[WorkSnapshot] = []
        self.shown: list[bool] = []
        self.threads: list[int] = []
        self.closed = False
        self.rendering = rendering

    def render(self, snapshot: WorkSnapshot) -> None:
        if self.rendering is not None:
            self.rendering(snapshot)
        self.states.append(snapshot)
        self.threads.append(threading.get_ident())

    def show(self, *, focus: bool = True) -> None:
        self.shown.append(focus)
        self.threads.append(threading.get_ident())

    def close(self) -> None:
        self.closed = True


class WorkUi:
    def __init__(self, view: RecordingWorkView | None = None) -> None:
        self.jobs: deque[Callable[[], None]] = deque()
        self.view = view or RecordingWorkView()
        self.created = 0
        self.commands: list[WorkCommand] = []
        self.problems: list[str] = []
        self.work = WorkWindow(self.commands.append, post=self.jobs.append, create_view=self.create, on_problem=self.problems.append)

    def create(self, _work: WorkWindow) -> WorkView:
        self.created += 1
        return self.view

    def paint(self) -> None:
        while self.jobs:
            self.jobs.popleft()()

    def close(self) -> None:
        self.work.close()
        self.paint()


def test_publication_is_lazy_coalesced_and_never_reveals_or_focuses() -> None:
    ui = WorkUi()
    first = WorkSnapshot(tasks=(work_task(),))
    ui.work.publish(first)
    assert ui.created == 0 and not ui.jobs
    ui.work.show(focus=False)
    ui.paint()
    assert ui.view.states == [first] and ui.view.shown == [False]
    thread_id = threading.get_ident()

    def publish() -> None:
        for number in range(100):
            ui.work.publish(replace(first, status=f"Update {number}"))

    publisher = threading.Thread(target=publish)
    publisher.start()
    publisher.join(timeout=2)
    assert not publisher.is_alive()
    assert len(ui.jobs) == 1
    ui.paint()
    assert ui.view.states[-1].status == "Update 99"
    assert ui.view.shown == [False] and set(ui.view.threads) == {thread_id}
    ui.work.publish(ui.work.state)
    assert not ui.jobs
    ui.work.show(focus=True)
    ui.paint()
    assert ui.created == 1 and ui.view.shown == [False, True]
    ui.close()
    assert ui.view.closed and ui.work.closed_ready.is_set()


def test_a_slow_renderer_cannot_block_publication_or_lose_the_latest_snapshot() -> None:
    rendering, release, published = threading.Event(), threading.Event(), threading.Event()

    def render(snapshot: WorkSnapshot) -> None:
        if snapshot.status == "Reading":
            rendering.set()
            assert release.wait(timeout=3)

    ui = WorkUi(RecordingWorkView(rendering=render))
    ui.work.show(focus=False)
    ui.paint()
    ui.work.publish(WorkSnapshot(status="Reading"))
    painter = threading.Thread(target=ui.paint)
    painter.start()
    assert rendering.wait(timeout=2)

    def publish() -> None:
        ui.work.publish(WorkSnapshot(tasks=(work_task(Completed("Verified", ("Workbook reopened",))),)))
        published.set()

    publisher = threading.Thread(target=publish)
    publisher.start()
    try:
        assert published.wait(timeout=2), "Rendering held the work publication lock."
    finally:
        release.set()
        publisher.join(timeout=2)
        painter.join(timeout=2)
        ui.close()
    assert not painter.is_alive() and not publisher.is_alive()
    assert ui.view.states[-1].tasks[0].state == Completed("Verified", ("Workbook reopened",))


def test_close_before_or_during_creation_prevents_late_windows_and_commands() -> None:
    ui = WorkUi()
    ui.work.show()
    ui.work.close()
    ui.work.publish(WorkSnapshot(tasks=(work_task(),)))
    assert not ui.work.send(Submit("Too late"))
    ui.paint()
    assert ui.created == 0 and not ui.commands and ui.work.closed_ready.is_set()

    jobs: deque[Callable[[], None]] = deque()
    view = RecordingWorkView()

    def create(work: WorkWindow) -> WorkView:
        work.close()
        return view

    work = WorkWindow(lambda _: None, post=jobs.append, create_view=create)
    work.show()
    while jobs:
        jobs.popleft()()
    assert view.closed and not view.states and not view.shown and work.closed_ready.is_set()


def test_failed_creation_can_be_retried_without_losing_saved_work() -> None:
    jobs: deque[Callable[[], None]] = deque()
    view = RecordingWorkView()
    errors: list[str] = []
    attempts = 0

    def create(_work: WorkWindow) -> WorkView:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Fixture native view unavailable")
        return view

    work = WorkWindow(lambda _: None, post=jobs.append, create_view=create, on_problem=errors.append)
    work.publish(WorkSnapshot(tasks=(work_task(),)))
    work.show(focus=False)
    while jobs:
        jobs.popleft()()
    assert len(errors) == 1 and work.state.tasks and not view.shown
    work.show(focus=False)
    while jobs:
        jobs.popleft()()
    assert view.states[-1].tasks == work.state.tasks and view.shown == [False]
    work.close()
    while jobs:
        jobs.popleft()()


def test_question_answers_require_the_exact_current_task_request_and_answer_kind() -> None:
    ui = WorkUi()
    task = work_task(Waiting(Question("question-one", "Which sheet?", "Choose the source.")))
    ui.work.publish(WorkSnapshot(tasks=(task,)))
    assert not ui.work.send(AnswerTask("another-task", "question-one", TextAnswer("Revenue")))
    assert not ui.work.send(AnswerTask(task.id, "question-two", TextAnswer("Revenue")))
    assert not ui.work.send(AnswerTask(task.id, "question-one", ApprovalDecision.APPROVED))
    answer = AnswerTask(task.id, "question-one", TextAnswer("Revenue\nwith the complete ending. Åäö."))
    assert ui.work.send(answer) and ui.commands == [answer]
    replacement = replace(task, state=Waiting(Question("question-two", "Which year?", "")), revision=1)
    ui.work.publish(WorkSnapshot(tasks=(replacement,)))
    assert not ui.work.send(answer)
    latest = AnswerTask(task.id, "question-two", TextAnswer("2026"))
    assert ui.work.send(latest) and ui.commands == [answer, latest]
    ui.work.publish(WorkSnapshot(tasks=(replace(replacement, state=TaskStatus.READY),)))
    assert not ui.work.send(latest)
    ui.close()


def test_approval_requires_a_matching_live_preview_and_its_own_display_acknowledgment() -> None:
    ui = WorkUi()
    task, review = approval_task()
    approve = AnswerTask(task.id, review.request.id, ApprovalDecision.APPROVED)
    ui.work.publish(WorkSnapshot(tasks=(task,)))
    assert not ui.work.send(approve)
    assert not ui.work.approval_presented(task.id, review.request.id)
    assert ui.work.send(AnswerTask(task.id, review.request.id, ApprovalDecision.DECLINED))
    assert not ui.work.send(AnswerTask(task.id, review.request.id, TextAnswer("approve")))
    ui.work.publish(WorkSnapshot(tasks=(task,), reviews=(review,)))
    assert not ui.work.send(approve), "Receiving a review is not native display."
    assert not ui.work.approval_presented("another-task", review.request.id)
    assert not ui.work.approval_presented(task.id, "another-request")
    assert ui.work.approval_presented(task.id, review.request.id)
    assert ui.work.send(approve)
    ui.work.publish(WorkSnapshot(tasks=(task,)))
    ui.work.publish(WorkSnapshot(tasks=(task,), reviews=(review,)))
    assert not ui.work.send(approve), "A lost live preview must retire its display acknowledgment."
    assert ui.work.approval_presented(task.id, review.request.id)
    ui.work.publish(WorkSnapshot(tasks=(task,), reviews=(replace(review, detail="A changed preview"),)))
    assert not ui.work.send(approve), "An old display cannot authorize changed review content."
    replacement, replacement_review = approval_task(nonce="two")
    ui.work.publish(WorkSnapshot(tasks=(replacement,), reviews=(review, replacement_review)))
    assert not ui.work.approval_presented(task.id, review.request.id) and not ui.work.send(approve)
    assert ui.work.approval_presented(task.id, replacement_review.request.id)
    latest = AnswerTask(task.id, replacement_review.request.id, ApprovalDecision.APPROVED)
    assert ui.work.send(latest)
    ui.work.publish(WorkSnapshot(tasks=(replace(replacement, revision=1, goal_revision=1),), reviews=(replacement_review,)))
    assert not ui.work.approval_presented(task.id, replacement_review.request.id) and not ui.work.send(latest)
    ui.close()


def test_work_details_preserve_complete_results_evidence_and_recovery_instructions() -> None:
    result = "Workbook analysis\n" * 300 + "Complete ending. Åäö."
    task = replace(work_task(Completed(result, ("Total independently verified", "C:\\work\\results.xlsx"))),
                   checkpoint=Checkpoint("All sheets read", 150.0))
    detail = task_detail(task)
    assert result in detail and "Total independently verified" in detail and "C:\\work\\results.xlsx" in detail
    assert task.prompt in detail and "All sheets read" in detail
    interrupted = replace(task, state=Interrupted(RunRef(task.id, "run-one"), "The worker stopped after writing the file."))
    recovery = task_detail(interrupted)
    assert "The worker stopped after writing the file." in recovery and "checks the interrupted work" in recovery
    waiting, review = approval_task(detail="Full reviewed content\n" * 500 + "The actual ending.")
    without_review = task_detail(waiting)
    assert "fresh preview" in without_review and review.request.detail not in without_review
    assert review.detail in task_detail(waiting, (review,))


def test_command_failure_reports_a_problem_without_discarding_current_tasks() -> None:
    errors: list[str] = []

    def reject(_command: WorkCommand) -> None:
        raise RuntimeError("Work process unavailable")

    work = WorkWindow(reject, post=lambda _: None, create_view=lambda _: RecordingWorkView(), on_problem=errors.append)
    snapshot = WorkSnapshot(tasks=(work_task(),))
    work.publish(snapshot)
    assert not work.send(Submit("Read the workbook"))
    assert errors == ["Work process unavailable"] and work.state.status == errors[0]
    assert work.state.tasks == snapshot.tasks
