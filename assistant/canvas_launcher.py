"""Search windows, configured apps, connected pages and saved canvas resources."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from collections.abc import Callable

from . import canvas_model as m
from .browser_values import Page
from .canvas_icons import icon
from .canvas_native import Application, Catalogue
from .canvas_widgets import STYLE, button, label
from .qt import Qt, QtCore, QtGui, QtWidgets

type Entry = m.Item | Application | Page


def entry_text(entry: Entry) -> tuple[str, str]:
    match entry:
        case m.Item(content=m.Source(origin=m.LiveWindow(app=app))):
            return entry.title, f"Window · {app.removesuffix('.exe')} · add to canvas"
        case m.Item(content=m.Source(origin=origin)):
            return entry.title, f"Source · {m.origin_label(origin)} · reveal on canvas"
        case m.Item(content=m.Note()):
            return entry.title, "Your note · reveal on canvas"
        case m.Item(content=m.Draft()):
            return entry.title, "Your draft · reveal on canvas"
        case m.Item():
            return entry.title, "Toolkit result · reveal on canvas"
        case Application(name=name):
            return name.capitalize(), "Application · open or switch to app"
        case Page(title=title, url=url):
            return title or url, f"Edge page · {url}"


class Launcher(QtWidgets.QDialog):
    def __init__(self, activate: Callable[[Entry], None], connect: Callable[[], None]) -> None:  # noqa: PLR0915  # One retained launcher layout.
        super().__init__()
        self.activate = activate
        self.entries: tuple[Entry, ...] = ()
        self.filtered: tuple[Entry, ...] = ()
        self.setWindowTitle("Toolkit launcher")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("launcher")
        self.setStyleSheet(STYLE + """
QDialog#launcher { background: transparent; }
QFrame#launcherPanel { background: #242b35; border: 1px solid #5e6678; border-radius: 15px; }
QFrame#launcherPanel QLineEdit { font-size: 17px; padding: 10px 12px; background: #20262f; }
QFrame#launcherPanel QListWidget { background: transparent; }
""")
        self.setMinimumSize(580, 400)
        self.resize(650, 500)
        outside = QtWidgets.QVBoxLayout(self)
        outside.setContentsMargins(12, 12, 12, 12)
        frame = QtWidgets.QFrame()
        frame.setObjectName("launcherPanel")
        outside.addWidget(frame)
        shadow = QtWidgets.QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QtGui.QColor(0, 0, 0, 95))
        frame.setGraphicsEffect(shadow)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(22, 20, 22, 15)
        layout.setSpacing(13)
        title = QtWidgets.QHBoxLayout()
        title.addWidget(label("toolkit", "title"))
        title.addStretch()
        title.addWidget(button("Esc", self.hide, tip="Close launcher"))
        layout.addLayout(title)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search windows, apps, pages, notes and results…")
        self.search.setAccessibleName("Search desktop resources")
        self.search.setMaxLength(256)
        self.search.setMinimumHeight(46)
        self.search.textChanged.connect(self.filter)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)
        options = QtWidgets.QHBoxLayout()
        self.scope = QtWidgets.QComboBox()
        self.scope.addItems(("Everything", "Windows", "Applications", "Canvas", "Edge pages"))
        self.scope.currentIndexChanged.connect(self.filter)
        options.addWidget(self.scope)
        options.addStretch()
        self.connect_button = button("Connect Edge", connect, tip="Connect your signed-in Edge browser to find and add page excerpts")
        options.addWidget(self.connect_button)
        layout.addLayout(options)
        self.results = QtWidgets.QListWidget()
        self.results.setIconSize(QtCore.QSize(23, 23))
        self.results.setSpacing(3)
        self.results.setAccessibleName("Matching resources")
        self.results.itemActivated.connect(self.choose)
        self.results.itemClicked.connect(self.choose)
        layout.addWidget(self.results, 1)
        self.status = label("Enter to open · ↑ ↓ to navigate", "muted")
        layout.addWidget(self.status)

    def update_entries(self, canvas: m.Canvas, catalogue: Catalogue) -> None:
        windows = tuple(value for value in catalogue.windows if canvas.item(value.id) is None)
        self.entries = (*canvas.items, *windows, *catalogue.apps, *catalogue.pages)
        self.filter()

    def filter(self) -> None:
        terms = self.search.text().casefold().split()
        scope = self.scope.currentText()
        selected = self.filtered[self.results.currentRow()] if 0 <= self.results.currentRow() < len(self.filtered) else None
        values: list[Entry] = []
        for entry in self.entries:
            if scope == "Windows" and not (isinstance(entry, m.Item) and isinstance(entry.content, m.Source)
                                            and isinstance(entry.content.origin, (m.LiveWindow, m.SavedWindow))):
                continue
            if scope == "Applications" and not isinstance(entry, Application):
                continue
            if scope == "Canvas" and not isinstance(entry, m.Item):
                continue
            if scope == "Edge pages" and not isinstance(entry, Page):
                continue
            title, subtitle = entry_text(entry)
            text = f"{title}\n{subtitle}\n{m.body(entry.content) if isinstance(entry, m.Item) else ''}".casefold()
            if all(term in text for term in terms):
                values.append(entry)
        self.filtered = tuple(values[:100])
        self.results.clear()
        for entry in self.filtered:
            title, subtitle = entry_text(entry)
            metrics = QtGui.QFontMetrics(self.results.font())
            width = max(380, self.width() - 90)
            title = metrics.elidedText(title, Qt.TextElideMode.ElideRight, width)
            subtitle = metrics.elidedText(subtitle, Qt.TextElideMode.ElideMiddle, width)
            row = QtWidgets.QListWidgetItem(f"{title}\n{subtitle}")
            glyph = "globe" if isinstance(entry, Page) else "grid" if isinstance(entry, Application) else (
                "agent" if isinstance(entry.content, m.Result) else "canvas" if isinstance(entry.content, m.Source)
                and isinstance(entry.content.origin, (m.LiveWindow, m.SavedWindow)) else "doc"
            )
            row.setIcon(icon(glyph, size=23))
            row.setToolTip("\n".join(entry_text(entry)))
            row.setSizeHint(QtCore.QSize(0, 67))
            self.results.addItem(row)
        self.results.setCurrentRow(self.filtered.index(selected) if selected in self.filtered else 0)
        self.status.setText("No matching resources. Try an app name or connect Edge." if not self.filtered else
                            f"{len(self.filtered)} resources · Enter to open · ↑ ↓ to navigate")

    def choose(self) -> None:
        index = self.results.currentRow()
        if 0 <= index < len(self.filtered):
            entry = self.filtered[index]
            self.hide()
            self.activate(entry)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if watched is self.search and isinstance(event, QtGui.QKeyEvent) and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.results.setCurrentRow(max(0, min(len(self.filtered) - 1,
                    self.results.currentRow() + (1 if event.key() == Qt.Key.Key_Down else -1))))
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.choose()
                return True
        return super().eventFilter(watched, event)

    def reveal(self) -> None:
        self.search.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
