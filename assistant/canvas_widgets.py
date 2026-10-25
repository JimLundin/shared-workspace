"""Native canvas cards and direct manipulation. Qt objects stay on the UI thread."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import math
from collections.abc import Callable
from functools import partial
from typing import assert_never

from . import canvas_model as m
from .canvas_icons import icon
from .qt import Qt, QtCore, QtGui, QtWidgets

INK = "#e5eaf1"
MUTED = "#a5b2c4"
BLUE = "#b8d2ff"
LILAC = "#d9c8f3"
GREEN = "#aad4bf"
GOLD = "#f0cc8c"

STYLE = """
QWidget { color: #d8e0ea; font-family: 'Segoe UI'; font-size: 12px; }
QWidget#toolkitWindow { background: #202831; }
QWidget#desktop { background: #212a31; }
QWidget#shellBar { background: #181e25; border-bottom: 1px solid #303740; }
QWidget#shellBar QToolButton { font-size: 11px; padding: 5px 8px; }
QWidget#shellBar QLabel { font-size: 10px; color: #b3bdc9; }
QFrame#card { background: #252c34; border: 1px solid #485766; border-radius: 12px; }
QFrame#card[role='agent'] {
 background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #3b3449,stop:1 #2c2b3b); border-color: #665678;
}
QFrame#card[role='draft'] { background: #e6e7e6; border-color: #6d87a5; }
QFrame#card[selected='true'] { border: 1px solid #a7bde1; }
QFrame#header { border: none; border-bottom: 1px solid #3b454f; background: rgba(30,38,46,110);
 border-top-left-radius: 11px; border-top-right-radius: 11px; }
QFrame#card[role='agent'] QFrame#header { background: transparent; border-color: #61516e; }
QFrame#card[role='draft'] QFrame#header { background: #253b55; }
QFrame#header QToolButton { padding: 3px; }
QLabel { background: transparent; border: none; }
QLabel#muted { color: #95a5b7; font-size: 10px; }
QLabel#eyebrow { color: #95a5b7; font-size: 9px; }
QLabel#title { font-size: 12px; font-weight: 600; }
QLabel#status { color: #f0cc8c; font-size: 10px; }
QLabel#appMark { background: #244c42; color: #bfe9d1; border-radius: 5px; font-size: 12px; font-weight: 600; }
QLabel#appMark[kind='page'] { background: #203f53; color: #b9e4f3; }
QLabel#appMark[kind='agent'] { background: #4e4265; color: #e5d7fe; }
QLabel#appMark[kind='draft'] { background: #254b76; color: #d5e9ff; }
QToolButton, QPushButton { border: 1px solid transparent; border-radius: 6px; background: transparent; padding: 6px 9px; font-size: 11px; }
QToolButton:hover, QPushButton:hover { background: rgba(255,255,255,15); }
QToolButton:pressed, QPushButton:pressed { background: rgba(184,210,255,35); }
QToolButton:focus, QPushButton:focus, QComboBox:focus { border: 1px solid #b8d2ff; }
QToolButton:disabled, QPushButton:disabled { color: #687584; }
QToolButton#primary, QPushButton#primary { background: #d9c8f3; color: #272534; padding: 8px 12px; font-weight: 600; }
QToolButton#primary:hover, QPushButton#primary:hover { background: #ecddff; }
QToolButton#chip { background: rgba(200,212,240,12); border: 1px solid rgba(188,204,228,32); padding: 3px 6px; font-size: 9px; }
QToolButton#select { border: 1px solid #8396aa; border-radius: 4px; padding: 0; }
QToolButton#select:checked { background: #c4d6f5; border-color: #d1e0fc; color: #263442; }
QToolButton:checked { background: rgba(255,255,255,14); color: #dce5f3; }
QWidget#body { background: transparent; }
QWidget#body QTextBrowser, QWidget#body QPlainTextEdit { background: transparent; border: none; padding: 0; border-radius: 0; }
QWidget#body[paper='true'] { background: #e3e2d9; color: #3e4f55; }
QWidget#body[paper='true'] QTextBrowser { color: #3e4f55; }
QFrame#card[role='draft'] QPlainTextEdit { color: #3e4f5d; selection-background-color: #b2c7dd; }
QFrame#card[role='draft'] QToolButton#chip { color: #416282; border-color: #bcc6cd; }
QFrame#card[role='draft'] QWidget#cardFooter { background: #d9dfe3; border-top: 1px solid #bcc6cd; }
QFrame#card[role='draft'] QWidget#cardFooter QToolButton { color: #416282; }
QWidget#cardFooter { border-top: 1px solid #3b454f; background: rgba(30,38,46,60);
 border-bottom-left-radius: 11px; border-bottom-right-radius: 11px; }
QWidget#cardFooter QToolButton { font-size: 9px; padding: 5px 3px; }
QLineEdit, QPlainTextEdit, QTextBrowser, QTableWidget {
 background: #202832; border: 1px solid #3c4754; border-radius: 7px; padding: 8px;
}
QPlainTextEdit:focus, QLineEdit:focus { border: 1px solid #a5bde4; }
QLineEdit { selection-background-color: #536888; }
QTextBrowser { selection-background-color: #67567d; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
QScrollBar::handle:vertical { background: #606c7a; border-radius: 2px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 6px; }
QScrollBar::handle:horizontal { background: #606c7a; border-radius: 2px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QComboBox { background: #26313d; border: 1px solid #44515f; border-radius: 6px; padding: 5px 12px; }
QComboBox QAbstractItemView { background: #26313d; selection-background-color: #40516b; }
QListWidget, QTreeWidget { background: #232b34; border: none; outline: none; }
QTreeWidget::item { padding: 8px 4px; }
QTreeWidget::item:selected { background: #35465b; }
QListWidget::item { padding: 10px 14px; border-radius: 8px; }
QListWidget::item:selected { background: #35465b; color: #eaf0fa; }
QListWidget::item:hover { background: #29394b; }
QDialog, QMenu { background: #252d37; }
QMenu { border: 1px solid #5b6980; padding: 6px; }
QMenu::item { padding: 8px 22px; border-radius: 5px; }
QMenu::item:selected { background: #435371; }
QHeaderView::section { background: #252c34; color: #8fa2b5; padding: 8px; border: none; font-size: 10px; }
QTableWidget { gridline-color: #39454e; selection-background-color: #344b51; border: none; background: transparent; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #323d47; }
QCheckBox { spacing: 7px; }
QTabWidget::pane { border: 1px solid #3b4755; border-radius: 8px; }
QTabBar::tab { background: #232b34; color: #a3b0c1; padding: 10px 16px; }
QTabBar::tab:selected { background: #354252; color: #e9e8e4; }
QToolTip { background: #253140; color: #eef2f7; border: 1px solid #71819c; padding: 6px; }
QWidget#selectionComposer { background: #2b2c3a; border: 1px solid #746780; border-radius: 15px; }
QWidget#selectionComposer QLineEdit { border: none; background: transparent; padding: 0; font-size: 12px; }
QWidget#selectionComposer QLineEdit:focus { border: none; }
QWidget#canvasTools, QWidget#canvasTools QLabel { background: transparent; }
QWidget#viewSwitch, QWidget#zoomBar { background: #1d2630; border: 1px solid #394350; border-radius: 8px; }
QWidget#viewSwitch QToolButton, QWidget#zoomBar QToolButton { font-size: 10px; padding: 5px 8px; }
QWidget#sourceTray { background: rgba(200,220,240,4); border: 1px dashed #4b5c6a; border-radius: 11px; }
"""


def button(text: str, callback: Callable[[], None], *, tip: str = "", name: str = "") -> QtWidgets.QToolButton:
    widget = QtWidgets.QToolButton()
    widget.setText(text)
    widget.setAccessibleName(tip or text)
    widget.setToolTip(tip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    if name:
        widget.setObjectName(name)
    # Plain slotted controllers need not support weak references just because a
    # Qt signal owns a callback. Qt retains this small closure with the button.
    widget.clicked.connect(lambda: callback())  # noqa: PLW0108  # Qt cannot weak-reference every slotted callback owner.
    return widget


def label(text: str, name: str = "") -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class DragHeader(QtWidgets.QFrame):
    def __init__(self, move: Callable[[float, float], None], finished: Callable[[], None], scale: Callable[[], float]) -> None:
        super().__init__()
        self.setObjectName("header")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.move_card, self.finished, self.scale = move, finished, scale
        self.previous: QtCore.QPointF | None = None

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.previous = event.globalPosition()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self.previous is not None:
            delta = (event.globalPosition() - self.previous) / self.scale()
            self.previous = event.globalPosition()
            self.move_card(delta.x(), delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self.previous is not None:
            self.previous = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.finished()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class Card(QtWidgets.QFrame):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        # Translucent proxy widgets suppress Qt's automatic background fill.
        # Paint the stylesheet surface explicitly, retaining transparent corners.
        option = QtWidgets.QStyleOption()
        option.initFrom(self)
        painter = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        painter.end()

    def __init__(  # noqa: PLR0915  # Declarative card layout; native children keep their editing state.
        self, item: m.Item, action: Callable[[str, str], None], edit: Callable[[str, str], None],
        *, move: Callable[[float, float], None], finished: Callable[[], None], scale: Callable[[], float],
    ) -> None:
        super().__init__()
        self.identity = item.id
        self.action, self.edit = action, edit
        self.content = item.content
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Proxy widgets are top-level Qt widgets; styles on the containing view
        # do not propagate through QGraphicsScene to the cards.
        self.setStyleSheet(STYLE)
        self.setProperty("role", "agent" if isinstance(item.content, m.Result) else "draft" if isinstance(item.content, m.Draft) else "source")
        self.setAccessibleName(item.title)
        self.root = QtWidgets.QVBoxLayout(self)
        self.root.setContentsMargins(1, 1, 1, 1)
        self.root.setSpacing(0)
        self.header = DragHeader(move, finished, scale)
        header_layout = QtWidgets.QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 10, 0)
        header_layout.setSpacing(8)
        self.header.setFixedHeight(47)
        self.use = button("", lambda: action(item.id, "select"), tip="Use as context", name="select")
        self.use.setFixedSize(17, 17)
        self.use.setCheckable(True)
        header_layout.addWidget(self.use)
        self.mark = label("", "appMark")
        self.mark.setFixedSize(23, 25)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.mark)
        headings = QtWidgets.QVBoxLayout()
        headings.setSpacing(3)
        self.kind = label("", "eyebrow")
        self.title = label(item.title, "title")
        self.title.setWordWrap(False)
        self.title.setMaximumHeight(18)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.kind.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        headings.addWidget(self.title)
        headings.addWidget(self.kind)
        header_layout.addLayout(headings, 1)
        for name, command, tip in (("expand", "focus", "Focus this item"), ("park", "park", "Park this item")):
            control = button("", partial(action, item.id, command), tip=tip)
            control.setFixedSize(27, 27)
            control.setIcon(icon(name, size=13))
            control.setIconSize(QtCore.QSize(13, 13))
            header_layout.addWidget(control)
        menu = button("···", lambda: action(item.id, "menu"), tip="Item actions")
        menu.setFixedSize(23, 27)
        header_layout.addWidget(menu)
        self.root.addWidget(self.header)
        self.notice = label("", "status")
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(18, 8, 18, 4)
        self.root.addWidget(self.notice)
        self.body = QtWidgets.QWidget()
        self.body.setObjectName("body")
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 18, 20, 18)
        self.body_layout.setSpacing(8)
        self.root.addWidget(self.body, 1)
        self.editor: QtWidgets.QPlainTextEdit | None = None
        self.table: QtWidgets.QTableWidget | None = None
        self.text: QtWidgets.QTextBrowser | None = None
        self.preview: QtWidgets.QWidget | None = None
        match item.content:
            case m.Note() | m.Draft():
                self.editor = QtWidgets.QPlainTextEdit()
                self.editor.setPlaceholderText("Write here. Your changes are saved on this canvas.")
                self.editor.setAccessibleName(f"Edit {item.title}")
                self.editor.setPlainText(item.content.text)
                self.editor.textChanged.connect(self._edited)
                self.body_layout.addWidget(self.editor, 1)
            case m.Source(origin=m.LiveWindow() | m.SavedWindow()):
                self.preview = QtWidgets.QWidget()
                preview_layout = QtWidgets.QVBoxLayout(self.preview)
                preview_layout.addStretch()
                app_label = label(m.origin_label(item.content.origin), "title")
                app_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview_layout.addWidget(app_label)
                caption = label("Open the app to use its controls", "muted")
                caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview_layout.addWidget(caption)
                preview_layout.addStretch()
                self.preview.setMinimumHeight(70)
                self.body_layout.addWidget(self.preview, 1)
            case m.Source(table=m.Table()):
                self.table = QtWidgets.QTableWidget()
                self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
                self.table.verticalHeader().hide()
                self.table.setShowGrid(False)
                self.table.verticalHeader().setDefaultSectionSize(39)
                self.table.horizontalHeader().setStretchLastSection(True)
                self.body_layout.addWidget(self.table, 1)
            case _:
                self.text = QtWidgets.QTextBrowser()
                font = QtGui.QFont("Segoe UI")
                font.setPixelSize(12)
                self.text.document().setDefaultFont(font)
                self.text.setOpenLinks(False)
                self.text.anchorClicked.connect(self.open_link)
                self.body_layout.addWidget(self.text, 1)
        self.source_row = QtWidgets.QHBoxLayout()
        self.source_row.setSpacing(5)
        self.source_button = button("Sources", lambda: action(item.id, "sources"), tip="Inspect the source versions used by this result", name="chip")
        self.source_row.addWidget(self.source_button)
        self.source_row.addStretch()
        self.source_row.setContentsMargins(20, 5, 20, 5)
        self.root.addLayout(self.source_row)
        self.steps_button = button("Steps", self.toggle_steps)
        self.steps = QtWidgets.QTextBrowser()
        self.steps.setMaximumHeight(130)
        self.steps.setOpenLinks(False)
        self.steps.hide()
        self.root.addWidget(self.steps_button)
        self.root.addWidget(self.steps)
        self.question = QtWidgets.QTextBrowser()
        self.question.setAccessibleName("The assistant's question or approval preview")
        self.question.setOpenLinks(False)
        self.question.setMinimumHeight(65)
        self.question.setMaximumHeight(140)
        self.root.addWidget(self.question)
        self.answer_row = QtWidgets.QWidget()
        answers = QtWidgets.QHBoxLayout(self.answer_row)
        answers.setContentsMargins(0, 0, 0, 0)
        self.answer = QtWidgets.QLineEdit()
        self.answer.setPlaceholderText("Your reply…")
        self.answer.setMaxLength(8_000)
        self.answer.returnPressed.connect(lambda: action(item.id, "answer:" + self.answer.text()))
        self.send = button("Reply", lambda: action(item.id, "answer:" + self.answer.text()), name="primary")
        self.approve = button("Approve", lambda: action(item.id, "answer:approve"), name="primary")
        self.decline = button("Decline", lambda: action(item.id, "answer:decline"))
        for widget in (self.answer, self.send, self.approve, self.decline):
            answers.addWidget(widget)
        self.root.addWidget(self.answer_row)
        self._question_id = ""
        footer_widget = QtWidgets.QWidget()
        footer_widget.setObjectName("cardFooter")
        footer_widget.setMinimumHeight(37)
        footer = QtWidgets.QHBoxLayout(footer_widget)
        footer.setContentsMargins(14, 3, 14, 3)
        self.origin = label("", "eyebrow")
        footer.addWidget(self.origin, 1)
        self.primary = button("", lambda: action(item.id, self.primary_action))
        self.secondary = button("", lambda: action(item.id, self.secondary_action))
        self.primary_action, self.secondary_action = "", ""
        footer.addWidget(self.secondary)
        footer.addWidget(self.primary)
        self.root.addWidget(footer_widget)
        self._rendered: m.Content | None = None
        self.update_item(item, selected=False, stale=False)

    def _edited(self) -> None:
        if self.editor is not None:
            value = self.editor.toPlainText()
            if len(value) <= m.MAX_TEXT:
                self.edit(self.identity, value)
            else:
                self.editor.undo()

    def open_link(self, url: QtCore.QUrl) -> None:
        self.action(self.identity, "link:" + url.toString())

    def toggle_steps(self) -> None:
        self.steps.setVisible(not self.steps.isVisible())

    def update_item(self, item: m.Item, *, selected: bool, stale: bool) -> None:  # noqa: PLR0915
        content = item.content
        self.content = content
        self.title.setText(item.title)
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)
        self.use.setChecked(selected)
        self.use.setText("✓" if selected else "")
        self.use.setVisible(not isinstance(content, m.WebApp))
        self.origin.setText(f"v{item.revision}")
        self.source_button.setVisible(bool(m.sources(content)))
        count = len(m.sources(content))
        self.source_button.setText(f"{count} {'source' if count == 1 else 'sources'} ↗")
        self.question.hide()
        self.answer_row.hide()
        self.steps_button.setVisible(isinstance(content, m.Result))
        notice = "Source changed · refresh when ready" if stale else ""
        match content:
            case m.WebApp():
                self.kind.setText("Web application")
                self.mark.setText("e")
                self.mark.setProperty("kind", "page")
                self.primary.hide()
                self.secondary.hide()
            case m.Source(origin=origin, table=table):
                self.kind.setText(m.origin_label(origin) + (" · App window" if isinstance(origin, m.LiveWindow) else " · Observed source"))
                self.mark.setText("▦" if table is not None else "e" if isinstance(origin, m.WebPage) else "↗")
                self.mark.setProperty("kind", "page" if isinstance(origin, m.WebPage) else "source")
                self.body.setProperty("paper", table is None and not isinstance(origin, (m.LiveWindow, m.SavedWindow)))
                self.primary_action, self.secondary_action = "open", "refresh_source"
                self.primary.setText("Open original ↗")
                self.secondary.setText("Read again")
                local = isinstance(origin, m.LocalSource)
                self.primary.setVisible(not local)
                self.secondary.setVisible(not local)
                if self.table is not None and table is not None and content != self._rendered:
                    self.table.setColumnCount(len(table.columns))
                    self.table.setRowCount(len(table.rows))
                    self.table.setHorizontalHeaderLabels(table.columns)
                    for row_index, row in enumerate(table.rows):
                        for column, value in enumerate(row):
                            self.table.setItem(row_index, column, QtWidgets.QTableWidgetItem(value))
                    self.table.resizeColumnsToContents()
                if isinstance(origin, m.SavedWindow):
                    notice = "Saved window · reconnect through Open original"
            case m.Note():
                self.kind.setText("Toolkit · Your note")
                self.mark.setText("✎")
                self.primary_action, self.secondary_action = "copy", "draft"
                self.primary.setText("Copy")
                self.secondary.setText("Make draft")
            case m.Draft():
                self.kind.setText("Toolkit · Editable draft")
                self.mark.setText("D")
                self.mark.setProperty("kind", "draft")
                self.primary_action, self.secondary_action = "mail", "copy"
                self.primary.setText("Open email ↗")
                self.secondary.setText("Copy")
                if stale:
                    notice = "A source has changed · your writing is preserved"
            case m.Result(state=state, steps=steps):
                self.kind.setText("Toolkit · Linked result")
                self.mark.setPixmap(icon("agent", "#e5d7fe", 15).pixmap(15, 15))
                self.mark.setProperty("kind", "agent")
                self.primary_action, self.secondary_action = "draft", "refresh"
                self.primary.setText("Make draft")
                self.primary.setEnabled(bool(content.text))
                self.secondary.setText("Refresh")
                if isinstance(state, m.Running):
                    self.secondary_action = "stop"
                    self.secondary.setText("Stop")
                    notice = state.label or ("Queued…" if state.stage is m.Stage.QUEUED else
                                              "Working…" if not content.text else "Updating · previous result stays here")
                    steps = state.steps
                    if isinstance(state.stage, m.Attention):
                        notice = "Submission needs attention"
                        self.question.setPlainText(state.stage.reason)
                        self.question.show()
                        self.secondary_action = "retry"
                        self.secondary.setText("Retry submission")
                    if isinstance(state.stage, (m.Question, m.Approval)):
                        question = state.stage
                        notice = state.label or ("Review this action" if isinstance(question, m.Approval) else "Your input is needed")
                        detail = question.title + ("\n" + question.detail if question.detail else "")
                        if self.question.toPlainText() != detail:
                            self.question.setPlainText(detail)
                        self.question.show()
                        self.answer_row.show()
                        approval = isinstance(question, m.Approval)
                        self.approve.setEnabled(False)
                        for widget in (self.answer, self.send):
                            widget.setVisible(not approval)
                        for widget in (self.approve, self.decline):
                            widget.setVisible(approval)
                        if question.id != self._question_id:
                            self.answer.clear()
                            self._question_id = question.id
                elif isinstance(state, m.Failure):
                    notice = state.reason
                elif state is m.Settled.STOPPED:
                    notice = "Stopped · saved output is preserved"
                elif state is m.Settled.INTERRUPTED:
                    notice = "Session ended · refresh to run this again"
                self.steps_button.setText(f"Steps · {len(steps)}")
                steps_text = "\n\n".join(steps)
                if self.steps.toPlainText() != steps_text:
                    offset = self.steps.verticalScrollBar().value()
                    self.steps.setPlainText(steps_text)
                    self.steps.verticalScrollBar().setValue(offset)
            case _:
                assert_never(content)
        self.notice.setText(notice)
        self.notice.setVisible(bool(notice))
        if self.text is not None and content != self._rendered:
            value = ("Open this web application in the Toolkit canvas.\n\n" + content.url) if isinstance(content, m.WebApp) else m.body(content)
            if isinstance(content, m.Result) and isinstance(content.state, m.Running) and not value:
                value = content.state.preview or "The assistant's result will appear here."
            offset = self.text.verticalScrollBar().value()
            cursor = self.text.textCursor()
            position, anchor = cursor.position(), cursor.anchor()
            if isinstance(content, m.Result):
                self.text.setMarkdown(value)
            else:
                self.text.setPlainText(value or "This item opens in its original application.")
            if max(position, anchor) < self.text.document().characterCount():
                cursor = QtGui.QTextCursor(self.text.document())
                cursor.setPosition(anchor)
                cursor.setPosition(position, QtGui.QTextCursor.MoveMode.KeepAnchor)
                self.text.setTextCursor(cursor)
            self.text.verticalScrollBar().setValue(offset)
        # Editors are initialized once. Incoming sources/results must not replace
        # user text, selection, undo history or keyboard focus.
        self._rendered = content
        self.body.style().unpolish(self.body)
        self.body.style().polish(self.body)
        self.mark.style().unpolish(self.mark)
        self.mark.style().polish(self.mark)


class CanvasView(QtWidgets.QGraphicsView):
    def __init__(self, moved: Callable[[], None], escape: Callable[[], None]) -> None:
        super().__init__()
        self.moved, self.escape = moved, escape
        self.group = QtCore.QRectF()
        self.group_count = 0
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setSceneRect(-m.CANVAS_EXTENT, -m.CANVAS_EXTENT, 2 * m.CANVAS_EXTENT, 2 * m.CANVAS_EXTENT)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Shared workspace canvas. Drag empty space to pan. Control and wheel to zoom.")
        self.horizontalScrollBar().valueChanged.connect(moved)
        self.verticalScrollBar().valueChanged.connect(moved)

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF | QtCore.QRect) -> None:  # noqa: N802
        painter.save()
        painter.resetTransform()
        viewport = self.viewport().rect()
        width, height = viewport.width(), viewport.height()
        background = QtGui.QLinearGradient(0, 0, width, height)
        background.setColorAt(0, QtGui.QColor("#212a31"))
        background.setColorAt(.54, QtGui.QColor("#1d242c"))
        background.setColorAt(1, QtGui.QColor("#292933"))
        painter.fillRect(viewport, background)
        for x, y, radius, color in ((0, height, width * .85, QtGui.QColor(59, 92, 80, 150)),
                                     (width * .95, height, width * .83, QtGui.QColor(75, 54, 91, 185))):
            glow = QtGui.QRadialGradient(x, y, radius)
            glow.setColorAt(0, color)
            glow.setColorAt(1, QtGui.QColor(color.red(), color.green(), color.blue(), 0))
            painter.fillRect(viewport, glow)
        painter.restore()
        painter.setPen(QtGui.QPen(QtGui.QColor(205, 216, 231, 17), .8))
        spacing = 24 if self.transform().m11() >= .5 else 48
        left, top = math.floor(rect.left() / spacing) * spacing, math.floor(rect.top() / spacing) * spacing
        for x in range(left, math.ceil(rect.right()), spacing):
            for y in range(top, math.ceil(rect.bottom()), spacing):
                painter.drawPoint(QtCore.QPointF(x, y))
        if not self.group.isEmpty():
            painter.setPen(QtGui.QPen(QtGui.QColor(187, 207, 225, 35), 1))
            painter.setBrush(QtGui.QColor(210, 228, 250, 3))
            painter.drawRoundedRect(self.group, 24, 24)
            painter.drawLine(QtCore.QLineF(self.group.left() + 25, self.group.top() + 52,
                                           self.group.right() - 25, self.group.top() + 52))
            font = QtGui.QFont("Segoe UI")
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(QtGui.QColor("#c5d0df"))
            painter.drawText(QtCore.QPointF(self.group.left() + 28, self.group.top() + 31), "Working set")
            font.setPixelSize(10)
            painter.setFont(font)
            painter.setPen(QtGui.QColor("#8f9eaf"))
            painter.drawText(QtCore.QPointF(self.group.left() + 114, self.group.top() + 31), "Connected to their sources")
            painter.drawText(QtCore.QRectF(self.group.right() - 120, self.group.top() + 14, 94, 28),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self.group_count} resources")

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            current = self.transform().m11()
            target = min(1.5, max(.25, current * 1.15 ** (event.angleDelta().y() / 120)))
            self.scale(target / current, target / current)
            self.moved()
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.escape()
            event.accept()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.moved()

    def camera(self) -> m.Camera:
        center = self.mapToScene(self.viewport().rect().center())
        return m.Camera(center.x(), center.y(), min(1.5, max(.25, self.transform().m11())))

    def restore(self, camera: m.Camera) -> None:
        self.resetTransform()
        self.scale(camera.zoom, camera.zoom)
        self.centerOn(camera.x, camera.y)


class MiniMap(QtWidgets.QWidget):
    def __init__(self, navigate: Callable[[float, float], None]) -> None:
        super().__init__()
        self.navigate = navigate
        self.rectangles: tuple[QtCore.QRectF, ...] = ()
        self.viewport = QtCore.QRectF()
        self.bounds = QtCore.QRectF(0, 0, 1, 1)
        self.setFixedSize(150, 72)
        self.setAccessibleName("Workspace overview map")
        self.setToolTip("Click to move around the workspace")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#151e29"))
        bounds = QtCore.QRectF(self.viewport)
        for rectangle in self.rectangles:
            bounds = bounds.united(rectangle)
        self.bounds = bounds.adjusted(-60, -60, 60, 60)
        sx, sy = self.width() / self.bounds.width(), self.height() / self.bounds.height()
        painter.scale(sx, sy)
        painter.translate(-self.bounds.left(), -self.bounds.top())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#849ab5"))
        for rectangle in self.rectangles:
            painter.drawRect(rectangle)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(LILAC), 1 / sx))
        painter.drawRect(self.viewport)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.navigate(self.bounds.left() + event.position().x() / self.width() * self.bounds.width(),
                      self.bounds.top() + event.position().y() / self.height() * self.bounds.height())
