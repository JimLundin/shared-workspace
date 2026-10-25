"""Completed recordings and sourced follow-through, with retained local edits."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from email.utils import getaddresses
from html import escape
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .canvas_widgets import button, label
from .meeting_followup import (
    ActionContent,
    ActionStatus,
    Addressee,
    AssignedDue,
    AssignedOwner,
    CaptureNotice,
    Citation,
    DraftContent,
    EmailAddress,
    TranscriptBundle,
    TranscriptSource,
    Unspecified,
)
from .meeting_followup_store import ActionRevision, DraftRevision
from .meeting_workflow import (
    ActionChange,
    ActionSaveProblem,
    DraftChange,
    DraftSaved,
    DraftSaveProblem,
    FollowupPackage,
    MeetingSnapshot,
    MeetingWorkflow,
    PackagesUnavailable,
    WorkUnavailable,
    package_values,
    previous_task,
)
from .qt import Qt, QtCore, QtGui, QtWidgets
from .recording_state import duration
from .widgets import Browser, Editor, column, replace_text, row
from .work_window import task_label


@dataclass(frozen=True, slots=True)
class ArtifactKey:
    task: str
    version: str
    item: str


@dataclass(frozen=True, slots=True)
class ActionEdit:
    text: str
    owner: str
    due: str
    done: bool
    revision: int

    def content(self) -> ActionContent:
        return ActionContent(self.text.strip(), AssignedOwner(self.owner.strip()) if self.owner.strip() else Unspecified.NOT_STATED,
                             AssignedDue(self.due.strip()) if self.due.strip() else Unspecified.NOT_STATED,
                             ActionStatus.DONE if self.done else ActionStatus.OPEN)


@dataclass(frozen=True, slots=True)
class DraftEdit:
    subject: str
    body: str
    recipients: str
    revision: int

    def content(self) -> DraftContent:
        return DraftContent(self.subject, self.body, parse_recipients(self.recipients))


def parse_recipients(value: str) -> tuple[Addressee, ...]:
    if not value.strip():
        return ()
    parts: list[str] = []
    quoted = escaped = False
    start = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    if quoted or any(not part or any(char in part for char in ";:\r\n\t()") for part in parts):
        raise ValueError("Use comma-separated names or Name <email@example.com>; check each recipient.")
    recipients: list[Addressee] = []
    for part in parts:
        if any(char in part for char in "@<>"):
            parsed = getaddresses([part], strict=True)
            if len(parsed) != 1 or not parsed[0][1] or "@" not in parsed[0][1]:
                raise ValueError("A recipient address is incomplete; correct it or enter just the person's name.")
            name, address = parsed[0]
            recipients.append(Addressee(name or address, EmailAddress(address)))
        else:
            # getaddresses treats an unquoted full name as an incomplete email
            # and can silently discard all but its first word.
            name = part
            if '"' in part:
                parsed = getaddresses([part], strict=True)
                if len(parsed) != 1 or not parsed[0][1] or not (part.startswith('"') and part.endswith('"')):
                    raise ValueError("Check the quotation marks around the recipient name.")
                name = parsed[0][1]
            recipients.append(Addressee(name, Unspecified.NOT_STATED))
    return tuple(recipients)


def action_edit(value: ActionRevision) -> ActionEdit:
    content = value.content
    return ActionEdit(content.text, content.owner.name if isinstance(content.owner, AssignedOwner) else "",
                      content.due.text if isinstance(content.due, AssignedDue) else "", content.status is ActionStatus.DONE, value.revision)


def draft_edit(value: DraftRevision) -> DraftEdit:
    return DraftEdit(value.content.subject, value.content.body, ", ".join(
        recipient_label(person) for person in value.content.recipients
    ), value.revision)


def recipient_label(person: Addressee) -> str:
    name = person.name
    if any(char in name for char in ',"\\'):
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{person.email.value}>" if isinstance(person.email, EmailAddress) else name


def choice(combo: QtWidgets.QComboBox) -> str:
    value: object = combo.currentData()
    return value if isinstance(value, str) else ""


def choices(combo: QtWidgets.QComboBox, values: tuple[tuple[str, str], ...]) -> None:
    if tuple((combo.itemData(index), combo.itemText(index)) for index in range(combo.count())) == values:
        return
    selected = choice(combo)
    combo.blockSignals(True)
    combo.clear()
    for identity, title in values:
        combo.addItem(title, identity)
    index = combo.findData(selected)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def citations_html(citations: tuple[Citation, ...], sources: TranscriptBundle) -> str:
    parts = {transcript.source.id: index for index, transcript in enumerate(sources.transcripts, 1)}
    return "<br>".join(
        f'<a href="source:{quote(value.source_id, safe="")}#{value.line}">Part {parts[value.source_id]} · line {value.line}</a>'
        f' <span style="color:#94a5b7">{escape(value.quote)}</span>' for value in citations
    )


class MeetingPanel(QtWidgets.QWidget):
    def __init__(self, workflow: MeetingWorkflow, *, open_file: Callable[[Path], None], add_to_canvas: Callable[[Path], None]) -> None:  # noqa: PLR0915
        super().__init__()
        self.workflow, self.open_file, self.add_to_canvas = workflow, open_file, add_to_canvas
        self.snapshot = MeetingSnapshot()
        self.checked: dict[str, TranscriptSource] = {}
        self.loading = False
        self.action_edits: dict[ArtifactKey, ActionEdit] = {}
        self.draft_edits: dict[ArtifactKey, DraftEdit] = {}
        self.pending_actions: dict[ArtifactKey, tuple[ActionChange, ActionEdit]] = {}
        self.pending_drafts: dict[ArtifactKey, tuple[DraftChange, DraftEdit]] = {}
        self.action_errors: dict[ArtifactKey, str] = {}
        self.draft_errors: dict[ArtifactKey, str] = {}
        layout = row(self)
        layout.setContentsMargins(16, 16, 16, 16)
        left = QtWidgets.QWidget()
        left.setMaximumWidth(310)
        sources = column(left)
        sources.addWidget(label("Completed recordings", "title"))
        sources.addWidget(label("Select the segments of one meeting.", "muted"))
        self.recordings = QtWidgets.QTreeWidget()
        self.recordings.setHeaderLabels(["Recorded", "Length"])
        self.recordings.setRootIsDecorated(False)
        self.recordings.setColumnWidth(0, 215)
        self.recordings.setAccessibleName("Completed recording segments")
        self.recordings.itemChanged.connect(self.select_recordings)
        sources.addWidget(self.recordings, 1)
        self.selection = label("No recordings selected", "muted")
        self.selection.setWordWrap(True)
        sources.addWidget(self.selection)
        self.prepare_button = button("Prepare notes & follow-ups", self.prepare, name="primary")
        sources.addWidget(self.prepare_button)
        sources.addWidget(button("Refresh recordings", self.refresh))
        layout.addWidget(left, 2)
        right = QtWidgets.QWidget()
        content = column(right)
        self.status = label("", "muted")
        self.status.setWordWrap(True)
        content.addWidget(self.status)
        selectors = row()
        self.preparations = QtWidgets.QComboBox()
        self.preparations.setAccessibleName("Meeting follow-through")
        self.versions = QtWidgets.QComboBox()
        self.versions.setAccessibleName("Saved package version")
        selectors.addWidget(self.preparations, 3)
        selectors.addWidget(self.versions, 1)
        content.addLayout(selectors)
        self.tabs = QtWidgets.QTabWidget()
        content.addWidget(self.tabs, 1)
        self.notes = Browser()
        self.notes.setOpenLinks(False)
        self.notes.anchorClicked.connect(self.trace_source)
        self.tabs.addTab(self.notes, "Notes & decisions")
        self.action_panel = self.build_action()
        self.tabs.addTab(self.action_panel, "Actions")
        self.draft_panel = self.build_draft()
        self.tabs.addTab(self.draft_panel, "Drafts")
        self.source_panel = QtWidgets.QWidget()
        source_layout = column(self.source_panel)
        self.source_choice = QtWidgets.QComboBox()
        self.source_choice.setAccessibleName("Original recording transcript")
        source_layout.addWidget(self.source_choice)
        self.source_notice = label("", "status")
        self.source_notice.setWordWrap(True)
        source_layout.addWidget(self.source_notice)
        self.source_text = Editor(readonly=True)
        self.source_text.setAccessibleName("Sourced transcript")
        source_layout.addWidget(self.source_text, 1)
        self.tabs.addTab(self.source_panel, "Sources")
        files = row()
        self.open_notes = button("Open notes", self.open_package_notes)
        self.canvas_notes = button("Put notes on canvas", self.canvas_package_notes)
        files.addWidget(self.open_notes)
        files.addWidget(self.canvas_notes)
        files.addStretch()
        content.addLayout(files)
        layout.addWidget(right, 5)
        self.preparations.currentIndexChanged.connect(self.render_package)
        self.versions.currentIndexChanged.connect(self.show_package)
        self.source_choice.currentIndexChanged.connect(self.show_source)
        self.render_snapshot(workflow.snapshot())

    def build_action(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = column(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.action_choice = QtWidgets.QComboBox()
        self.action_choice.setAccessibleName("Action to edit")
        layout.addWidget(self.action_choice)
        self.action_text = Editor()
        self.action_text.setAccessibleName("Action description")
        layout.addWidget(self.action_text, 1)
        fields = QtWidgets.QFormLayout()
        self.owner = QtWidgets.QLineEdit()
        self.owner.setPlaceholderText("Unassigned")
        self.due = QtWidgets.QLineEdit()
        self.due.setPlaceholderText("No due date stated")
        self.done = QtWidgets.QCheckBox("Done")
        fields.addRow("Owner", self.owner)
        fields.addRow("Due", self.due)
        fields.addRow("", self.done)
        layout.addLayout(fields)
        self.action_evidence = Browser()
        self.action_evidence.setMaximumHeight(125)
        self.action_evidence.setOpenLinks(False)
        self.action_evidence.anchorClicked.connect(self.trace_source)
        layout.addWidget(self.action_evidence)
        self.action_status = label("", "muted")
        self.action_status.setWordWrap(True)
        layout.addWidget(self.action_status)
        actions = row()
        self.save_action_button = button("Save action", self.save_action, name="primary")
        actions.addWidget(self.save_action_button)
        actions.addWidget(button("Reload saved", self.reload_action))
        actions.addStretch()
        layout.addLayout(actions)
        self.action_choice.currentIndexChanged.connect(self.show_action)
        for field in (self.action_text, self.owner, self.due):
            field.textChanged.connect(self.change_action)
        self.done.toggled.connect(self.change_action)
        return panel

    def build_draft(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = column(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.draft_choice = QtWidgets.QComboBox()
        self.draft_choice.setAccessibleName("Follow-up draft to edit")
        layout.addWidget(self.draft_choice)
        fields = QtWidgets.QFormLayout()
        self.recipients = QtWidgets.QLineEdit()
        self.recipients.setPlaceholderText("Name <email>, or a name to resolve later")
        self.subject = QtWidgets.QLineEdit()
        fields.addRow("To", self.recipients)
        fields.addRow("Subject", self.subject)
        layout.addLayout(fields)
        self.draft_body = Editor()
        self.draft_body.setAccessibleName("Follow-up email body")
        layout.addWidget(self.draft_body, 1)
        self.draft_evidence = Browser()
        self.draft_evidence.setMaximumHeight(110)
        self.draft_evidence.setOpenLinks(False)
        self.draft_evidence.anchorClicked.connect(self.trace_source)
        layout.addWidget(self.draft_evidence)
        self.draft_status = label("", "muted")
        self.draft_status.setWordWrap(True)
        layout.addWidget(self.draft_status)
        actions = row()
        self.save_draft_button = button("Save draft", self.save_draft, name="primary")
        self.open_draft_button = button("Open saved email draft", self.open_draft)
        actions.addWidget(self.save_draft_button)
        actions.addWidget(self.open_draft_button)
        actions.addWidget(button("Reload saved", self.reload_draft))
        actions.addStretch()
        layout.addLayout(actions)
        self.draft_choice.currentIndexChanged.connect(self.show_draft)
        for field in (self.recipients, self.subject, self.draft_body):
            field.textChanged.connect(self.change_draft)
        return panel

    def refresh(self) -> None:
        try:
            self.workflow.refresh()
        except ValueError as error:
            self.status.setText(str(error))

    def select_recordings(self) -> None:
        if self.loading:
            return
        sources = {source.id: source for source in self.snapshot.transcripts}
        self.checked.clear()
        for index in range(self.recordings.topLevelItemCount()):
            item = self.recordings.topLevelItem(index)
            if item is not None and item.checkState(0) == Qt.CheckState.Checked:
                identity: object = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(identity, str) and identity in sources:
                    self.checked[identity] = sources[identity]
        self.selection.setText(f"{len(self.checked)} selected · combined in recording order" if self.checked else "No recordings selected")
        self.prepare_button.setEnabled(1 <= len(self.checked) <= 8)

    def prepare(self) -> None:
        selected = tuple(sorted(self.checked.values(), key=lambda value: (value.started_at, value.id)))
        try:
            self.workflow.prepare(selected)
            self.status.setText("Preparing notes, actions and follow-up drafts…")
        except ValueError as error:
            self.status.setText(str(error))

    def render_snapshot(self, snapshot: MeetingSnapshot) -> None:
        previous = self.snapshot
        self.snapshot = snapshot
        self.receipts(snapshot)
        if snapshot.transcripts != previous.transcripts:
            self.loading = True
            self.recordings.clear()
            for source in snapshot.transcripts:
                when = datetime.fromtimestamp(source.started_at).strftime("%d %b · %H:%M")
                item = QtWidgets.QTreeWidgetItem([f"{source.title}\n{when}", duration(source.duration_seconds)])
                item.setData(0, Qt.ItemDataRole.UserRole, source.id)
                item.setCheckState(0, Qt.CheckState.Checked if self.checked.get(source.id) == source else Qt.CheckState.Unchecked)
                item.setToolTip(0, source.notice.text if isinstance(source.notice, CaptureNotice) else source.title)
                self.recordings.addTopLevelItem(item)
            self.loading = False
        self.select_recordings()
        choices(self.preparations, tuple((progress.request.task_id,
                package_values(progress.packages)[0].publication.followup.title if package_values(progress.packages) else
                " + ".join(transcript.source.title for transcript in progress.request.bundle.transcripts)) for progress in snapshot.preparations))
        self.render_package()

    def receipts(self, snapshot: MeetingSnapshot) -> None:
        for result in snapshot.changes:
            change = result.change
            if isinstance(result, (DraftSaved, DraftSaveProblem)):
                key = ArtifactKey(change.task_id, change.version, result.change.draft_id)
                pending = self.pending_drafts.get(key)
                if pending is None or pending[0] != change:
                    continue
                self.pending_drafts.pop(key)
                if isinstance(result, DraftSaveProblem):
                    self.draft_errors[key] = result.reason
                elif (current := self.draft_edits.get(key)) == pending[1]:
                    self.draft_edits.pop(key, None)
                elif current is not None:
                    self.draft_edits[key] = replace(current, revision=result.revision.revision)
            else:
                key = ArtifactKey(change.task_id, change.version, result.change.action_id)
                pending_action = self.pending_actions.get(key)
                if pending_action is None or pending_action[0] != change:
                    continue
                self.pending_actions.pop(key)
                if isinstance(result, ActionSaveProblem):
                    self.action_errors[key] = result.reason
                elif (current_action := self.action_edits.get(key)) == pending_action[1]:
                    self.action_edits.pop(key, None)
                elif current_action is not None:
                    self.action_edits[key] = replace(current_action, revision=result.revision.revision)

    def package(self) -> FollowupPackage | None:
        progress = next((value for value in self.snapshot.preparations if value.request.task_id == choice(self.preparations)), None)
        return next((value for value in package_values(progress.packages)
                     if value.publication.version == choice(self.versions)), None) if progress else None

    def render_package(self) -> None:
        progress = next((value for value in self.snapshot.preparations if value.request.task_id == choice(self.preparations)), None)
        packages = package_values(progress.packages) if progress else ()
        versions = tuple((value.publication.version, f"Version · {value.publication.version[:12]}") for value in packages)
        if len(versions) > 1:
            versions = (("", "Choose a saved version…"), *versions)
        choices(self.versions, versions)
        problems = [problem.reason for problem in self.snapshot.problems]
        if progress is not None:
            task = previous_task(progress.work)
            state = task_label(task) if task else "Waiting for the work process"
            if isinstance(progress.work, WorkUnavailable):
                problems.append(progress.work.reason)
            if isinstance(progress.packages, PackagesUnavailable):
                problems.append(progress.packages.reason)
        else:
            state = "Choose completed recordings to prepare notes, actions and drafts."
        self.status.setText("\n".join(problems) if problems else state)
        self.show_package()

    def show_package(self) -> None:
        package = self.package()
        self.open_notes.setEnabled(package is not None)
        self.canvas_notes.setEnabled(package is not None)
        self.action_panel.setEnabled(package is not None)
        self.draft_panel.setEnabled(package is not None)
        if package is None:
            self.notes.setProperty("rendered", "")
            self.notes.setPlainText("Choose a saved version above to review its notes and edits." if self.versions.count() > 1 else
                                    "The verified meeting package will appear here when it is ready.")
            choices(self.action_choice, ())
            choices(self.draft_choice, ())
            choices(self.source_choice, ())
        else:
            followup = package.publication.followup
            sections = [f"<h2>{escape(followup.title)}</h2>"]
            for title, statements in (("Notes", followup.notes), ("Decisions", followup.decisions)):
                if statements:
                    sections.append(f"<h3>{title}</h3>")
                    sections.extend(f"<p>{escape(statement.text)}</p><p>{citations_html(statement.citations, followup.sources)}</p>"
                                    for statement in statements)
            rendered = "".join(sections)
            if self.notes.property("rendered") != rendered:
                self.notes.setProperty("rendered", rendered)
                self.notes.setHtml(rendered)
            choices(self.action_choice, tuple((value.id, value.content.text[:120]) for value in package.actions))
            choices(self.draft_choice, tuple((value.id, value.content.subject) for value in package.drafts))
            choices(self.source_choice, tuple((value.source.id,
                    f"Part {index} · {value.source.title} · {datetime.fromtimestamp(value.source.started_at):%d %b %H:%M}")
                    for index, value in enumerate(followup.sources.transcripts, 1)))
        self.show_action()
        self.show_draft()
        self.show_source()

    def action(self) -> tuple[ArtifactKey, ActionRevision] | None:
        package = self.package()
        if package is not None:
            value = next((value for value in package.actions if value.id == choice(self.action_choice)), None)
            if value is not None:
                return ArtifactKey(package.publication.task_id, package.publication.version, value.id), value
        return None

    def draft(self) -> tuple[ArtifactKey, DraftRevision] | None:
        package = self.package()
        if package is not None:
            value = next((value for value in package.drafts if value.id == choice(self.draft_choice)), None)
            if value is not None:
                return ArtifactKey(package.publication.task_id, package.publication.version, value.id), value
        return None

    def line(self, field: QtWidgets.QLineEdit, text: str) -> None:
        if field.text() != text:
            field.setText(text)

    def show_action(self) -> None:
        selected, package = self.action(), self.package()
        self.loading = True
        try:
            if selected is not None and package is not None:
                key, value = selected
                draft = self.action_edits.get(key, action_edit(value))
                replace_text(self.action_text, draft.text)
                self.line(self.owner, draft.owner)
                self.line(self.due, draft.due)
                self.done.setChecked(draft.done)
                original = next(value for value in package.publication.followup.actions if value.id == key.item)
                self.action_evidence.setHtml(citations_html(original.citations, package.publication.followup.sources))
            else:
                self.action_text.clear()
                self.owner.clear()
                self.due.clear()
                self.done.setChecked(False)
                self.action_evidence.clear()
        finally:
            self.loading = False
        self.edit_status()

    def show_draft(self) -> None:
        selected, package = self.draft(), self.package()
        self.loading = True
        try:
            if selected is not None and package is not None:
                key, value = selected
                draft = self.draft_edits.get(key, draft_edit(value))
                self.line(self.subject, draft.subject)
                self.line(self.recipients, draft.recipients)
                replace_text(self.draft_body, draft.body)
                original = next(value for value in package.publication.followup.drafts if value.id == key.item)
                self.draft_evidence.setHtml(citations_html(original.citations, package.publication.followup.sources))
            else:
                self.subject.clear()
                self.recipients.clear()
                self.draft_body.clear()
                self.draft_evidence.clear()
        finally:
            self.loading = False
        self.edit_status()

    def change_action(self) -> None:
        selected = self.action()
        if not self.loading and selected is not None:
            key, value = selected
            previous = self.action_edits.get(key, action_edit(value))
            self.action_edits[key] = ActionEdit(self.action_text.toPlainText(), self.owner.text(), self.due.text(),
                                                self.done.isChecked(), previous.revision)
            self.action_errors.pop(key, None)
            self.edit_status()

    def change_draft(self) -> None:
        selected = self.draft()
        if not self.loading and selected is not None:
            key, value = selected
            previous = self.draft_edits.get(key, draft_edit(value))
            self.draft_edits[key] = DraftEdit(self.subject.text(), self.draft_body.toPlainText(), self.recipients.text(), previous.revision)
            self.draft_errors.pop(key, None)
            self.edit_status()

    def edit_status(self) -> None:
        action, draft = self.action(), self.draft()
        for selected, edits, pending, errors, status, save in (
            (action, self.action_edits, self.pending_actions, self.action_errors, self.action_status, self.save_action_button),
            (draft, self.draft_edits, self.pending_drafts, self.draft_errors, self.draft_status, self.save_draft_button),
        ):
            key = selected[0] if selected else None
            text = "Saving…" if key in pending else "Unsaved changes" if key in edits else "Saved"
            status.setText(errors.get(key, text) if key is not None else "No items in this package")
            save.setEnabled(key is not None and key in edits and key not in pending and key not in errors)
        key = draft[0] if draft else None
        self.open_draft_button.setEnabled(key is not None and key not in self.draft_edits and key not in self.pending_drafts)

    def save_action(self) -> None:
        selected = self.action()
        if selected is None:
            return
        key, _ = selected
        if key not in self.action_edits or key in self.pending_actions:
            return
        edited = self.action_edits[key]
        try:
            change = ActionChange(key.task, key.version, key.item, edited.content(), edited.revision, uuid4().hex)
            self.workflow.save_action(change)
            self.pending_actions[key] = change, edited
        except ValueError as error:
            self.action_errors[key] = str(error)
        self.edit_status()

    def save_draft(self) -> None:
        selected = self.draft()
        if selected is None:
            return
        key, _ = selected
        if key not in self.draft_edits or key in self.pending_drafts:
            return
        edited = self.draft_edits[key]
        try:
            change = DraftChange(key.task, key.version, key.item, edited.content(), edited.revision, uuid4().hex)
            self.workflow.save_draft(change)
            self.pending_drafts[key] = change, edited
        except ValueError as error:
            self.draft_errors[key] = str(error)
        self.edit_status()

    def reload_action(self) -> None:
        selected = self.action()
        if selected is not None and selected[0] not in self.pending_actions:
            self.action_edits.pop(selected[0], None)
            self.action_errors.pop(selected[0], None)
            self.show_action()
            self.refresh()

    def reload_draft(self) -> None:
        selected = self.draft()
        if selected is not None and selected[0] not in self.pending_drafts:
            self.draft_edits.pop(selected[0], None)
            self.draft_errors.pop(selected[0], None)
            self.show_draft()
            self.refresh()

    def show_source(self) -> None:
        package = self.package()
        source = next((value for value in package.publication.followup.sources.transcripts
                       if value.source.id == choice(self.source_choice)), None) if package else None
        replace_text(self.source_text, source.text if source else "")
        notice = source.source.notice if source is not None else None
        self.source_notice.setText(notice.text if isinstance(notice, CaptureNotice) else "")
        self.source_notice.setVisible(isinstance(notice, CaptureNotice))

    def trace_source(self, url: QtCore.QUrl) -> None:
        if url.scheme() != "source":
            return
        index = self.source_choice.findData(url.path())
        if index < 0:
            return
        try:
            line = int(url.fragment())
        except ValueError:
            return
        self.source_choice.setCurrentIndex(index)
        self.show_source()
        self.tabs.setCurrentWidget(self.source_panel)
        block = self.source_text.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            cursor = QtGui.QTextCursor(block)
            cursor.select(QtGui.QTextCursor.SelectionType.BlockUnderCursor)
            self.source_text.setTextCursor(cursor)
            self.source_text.centerCursor()

    def open_draft(self) -> None:
        selected = self.draft()
        if selected is not None and selected[0] not in self.draft_edits and selected[0] not in self.pending_drafts:
            self.open_file(selected[1].files.eml)

    def open_package_notes(self) -> None:
        if (package := self.package()) is not None:
            self.open_file(package.publication.notes_path)

    def canvas_package_notes(self) -> None:
        if (package := self.package()) is not None:
            self.add_to_canvas(package.publication.notes_path)
