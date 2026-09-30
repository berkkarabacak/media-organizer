"""Main window for Media Organizer — a friendly 3-step guided flow.

Step 1  Choose your folders
Step 2  How should we sort them?
Step 3  Check the plan  ->  Organize now
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QRectF, QTimer
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QUrl
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QKeySequence, QPainter, QPen, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QRadioButton, QStackedWidget, QStyledItemDelegate, QTableView,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.display import (elide_middle, finished_run_lines, format_bytes,
                            relative_destination, relative_destination_fast,
                            sorted_plan_items)
from ..core.eta import ThroughputEstimator, format_eta, format_rate
from ..core.executor import free_space_status
from ..core.journal import (completed_sources, discard_journal,
                            exclude_completed_sources,
                            find_unfinished_journal)
from ..core.metadata import Confidence, DateSource
from ..core.organizer import STRATEGIES, OrganizeOptions, destination_blocks_scan
from ..core.plan import (UNDO_LIMITATION, list_run_logs, undo_log,
                         undo_result_message)
from ..core.strategies import DEFAULT_STRATEGY_KEY
from . import icons, theme
from .workers import OrganizeWorker, ScanWorker

_CONFIDENCE_COLORS = {
    Confidence.HIGH: theme.GREEN,
    Confidence.MEDIUM: theme.AMBER,
    Confidence.LOW: theme.TEXT_FAINT,
}

_SOURCE_LABELS = {
    DateSource.EXIF: "camera info (EXIF)",
    DateSource.PNG_TEXT: "image metadata",
    DateSource.VIDEO: "video metadata",
    DateSource.FILENAME: "file name",
    DateSource.MTIME: "file date (guess)",
    DateSource.NONE: "—",
}

COL_NAME, COL_DATE, COL_SOURCE, COL_DEST, COL_SIZE = range(5)
_COL_KEYS = {COL_NAME: "file", COL_DATE: "date", COL_SOURCE: "source",
             COL_DEST: "dest", COL_SIZE: "size"}
#: sensible minimum widths per column (px)
_COL_MIN_WIDTHS = {COL_NAME: 180, COL_DATE: 130, COL_SOURCE: 150,
                   COL_DEST: 200, COL_SIZE: 80}
_COL_DEFAULT_WIDTHS = {COL_NAME: 280, COL_DATE: 160, COL_SOURCE: 210,
                       COL_SIZE: 90}


def _fmt_size(n: int) -> str:
    return format_bytes(n)


def _badge(icon_name: str, badge_px: int = 44, icon_px: int = 20,
           color: str = theme.AMBER) -> QLabel:
    """Amber-tinted rounded badge holding a centered SVG icon."""
    label = QLabel()
    label.setObjectName("iconBadge")
    label.setFixedSize(badge_px, badge_px)
    label.setAlignment(Qt.AlignCenter)
    label.setPixmap(icons.pixmap(icon_name, color, icon_px))
    return label


class _Cell:
    """Stand-in for QTableWidgetItem so existing callers can read .text()."""

    def __init__(self, model: "_PlanModel", row: int, column: int):
        self._model = model
        self._row = row
        self._column = column

    def text(self) -> str:
        value = self._model.data(
            self._model.index(self._row, self._column), Qt.DisplayRole)
        return "" if value is None else str(value)

    def data(self, role):
        return self._model.data(self._model.index(self._row, self._column), role)


class _PlanModel(QAbstractTableModel):
    """Plan rows for the preview. The view asks only for cells it paints.

    Building a QTableWidgetItem per cell froze the window after a few
    thousand files. Strings are prepared once; icons are created when a
    visible cell is painted.
    """

    HEADERS = ["FILE", "DATE TAKEN", "FOUND VIA", "NEW LOCATION", "SIZE"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return 5

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if (role == Qt.DisplayRole and orientation == Qt.Horizontal
                and 0 <= section < 5):
            return self.HEADERS[section]
        return None

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def haystack(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return self._rows[row].get("hay") or ""
        return ""

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._rows) and 0 <= col < 5):
            return None
        item = self._rows[row]
        if role == Qt.DisplayRole:
            return item["text"][col]
        if role == Qt.ToolTipRole:
            return item["tips"][col] or None
        if role == Qt.UserRole and col == COL_NAME:
            return item["hay"]
        if role == Qt.TextAlignmentRole and col == COL_SIZE:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if item["dim"]:
                return QColor(theme.TEXT_FAINT)
            if col == COL_SOURCE:
                return QColor(item["src_color"])
            return None
        if role == Qt.DecorationRole:
            if col == COL_NAME:
                color = theme.TEXT_FAINT if item["dim"] else theme.TEXT_DIM
                return icons.icon(item["name_icon"], color, 14)
            if col == COL_SOURCE:
                return icons.icon(item["src_icon"], item["src_color"], 13)
        return None


class PlanTableView(QTableView):
    """QTableView with the small QTableWidget reads the rest of the app uses."""

    def rowCount(self) -> int:
        model = self.model()
        return 0 if model is None else model.rowCount()

    def item(self, row: int, column: int):
        model = self.model()
        if model is None or row < 0 or column < 0:
            return None
        if row >= model.rowCount() or column >= model.columnCount():
            return None
        return _Cell(model, row, column)


class _ElideMiddleDelegate(QStyledItemDelegate):
    """Paints long paths elided in the middle ('C:\\...\\file.jpg'), never
    down to a bare drive prefix."""

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole) or ""
        metrics = option.fontMetrics
        width = option.rect.width() - 12
        option.displayText = metrics.elidedText(text, Qt.ElideMiddle, width)
        super().paint(painter, option, index)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.pixmap("diamond", theme.AMBER, 20))
        title_row.addWidget(logo)
        title = QLabel(APP_NAME)
        title.setObjectName("wordmark")
        title_row.addWidget(title)
        title_row.addStretch(1)
        layout.addLayout(title_row)
        ver = QLabel(f"Version {__version__}")
        ver.setObjectName("muted")
        layout.addWidget(ver)
        about = QLabel(
            "Sorts your photos and videos into tidy folders using their real "
            "capture dates (EXIF, video metadata, file names) or the place "
            "they were taken. Duplicates are detected by content "
            "(SHA-256), never by file name."
        )
        about.setWordWrap(True)
        layout.addWidget(about)
        license_label = QLabel("License: <commercial license placeholder>")
        license_label.setObjectName("muted")
        layout.addWidget(license_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class _StepIndicator(QWidget):
    """Numbered circles connected by lines; active=amber, done=green check."""

    def __init__(self, count: int = 3, parent=None):
        super().__init__(parent)
        self._count = count
        self._current = 0
        self.setFixedSize(300, 44)

    def set_current(self, index: int):
        self._current = index
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = 13
        cy = self.height() / 2
        step = (self.width() - 2 * r - 20) / (self._count - 1)
        xs = [10 + r + i * step for i in range(self._count)]
        # connecting lines
        for i in range(self._count - 1):
            done = i < self._current
            p.setPen(QPen(QColor(theme.GREEN if done else theme.BORDER),
                          2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(int(xs[i] + r + 5), int(cy), int(xs[i + 1] - r - 5), int(cy))
        for i, x in enumerate(xs):
            center = QRectF(x - r, cy - r, 2 * r, 2 * r)
            if i < self._current:      # completed
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.GREEN))
                p.drawEllipse(center)
                pen = QPen(QColor(theme.BG_APP), 2.2, Qt.SolidLine,
                           Qt.RoundCap, Qt.RoundJoin)
                p.setPen(pen)
                p.drawLine(int(x - 5), int(cy + 1), int(x - 1), int(cy + 5))
                p.drawLine(int(x - 1), int(cy + 5), int(x + 6), int(cy - 5))
            elif i == self._current:   # active
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.AMBER))
                p.drawEllipse(center)
                p.setPen(QColor("#1A1206"))
                f = p.font(); f.setBold(True); f.setPixelSize(14); p.setFont(f)
                p.drawText(center, Qt.AlignCenter, str(i + 1))
            else:                      # todo
                p.setPen(QPen(QColor(theme.BORDER), 1.5))
                p.setBrush(QColor(theme.BG_PANEL))
                p.drawEllipse(center)
                p.setPen(QColor(theme.TEXT_FAINT))
                f = p.font(); f.setBold(False); f.setPixelSize(13); p.setFont(f)
                p.drawText(center, Qt.AlignCenter, str(i + 1))
        p.end()


class _FolderCard(QFrame):
    """Drop-zone style folder picker with icon badge and pill path field."""

    def __init__(self, icon: str, question: str, hint: str, button_text: str,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(_badge(icon, badge_px=52, icon_px=26))
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        q = QLabel(question)
        q.setObjectName("cardQuestion")
        h = QLabel(hint)
        h.setObjectName("cardHint")
        h.setWordWrap(True)
        text_col.addWidget(q)
        text_col.addWidget(h)
        top.addLayout(text_col, 1)
        layout.addLayout(top)

        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("No folder chosen yet")
        self.button = QPushButton(button_text)
        self.button.setIcon(icons.icon("folder-open", theme.TEXT, 15))
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        layout.addLayout(row)

        self.chip = QPushButton()
        self.chip.setObjectName("chip")
        self.chip.setIcon(icons.icon("sparkles", theme.AMBER_HOVER, 13))
        self.chip.setVisible(False)
        self.chip.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.chip, 0, Qt.AlignLeft)


class _StrategyCard(QPushButton):
    """Checkable strategy card — a real button, so UIAutomation exposes
    Toggle/Invoke patterns and keyboard focus/Space work (a11y fix).

    Looks identical to the old QFrame card; child labels are transparent to
    mouse events so clicks anywhere hit the button."""

    def __init__(self, strategy, parent=None):
        super().__init__(parent)
        self.setObjectName("strategyCard")
        self.setProperty("strategyKey", strategy.key)
        self.setProperty("selected", False)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(strategy.name)
        self.setAccessibleDescription(
            f"Sorting strategy: {strategy.name}. Example: {strategy.example}")
        self.setCursor(Qt.PointingHandCursor)
        self.on_toggled = None  # set by MainWindow
        self._reselect_guard = False

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 18, 12)
        row.setSpacing(14)
        badge = _badge(icons.STRATEGY_ICONS.get(strategy.key, "calendar"),
                       badge_px=42, icon_px=20)
        row.addWidget(badge)
        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        name = QLabel(strategy.name)
        name.setObjectName("strategyName")
        example = QLabel(f"e.g.  {strategy.example}")
        example.setObjectName("examplePill")
        # force LTR: mixed-direction glyphs + font fallback produced garbled
        # pills on some scaled displays
        example.setLayoutDirection(Qt.LeftToRight)
        # let every pixel of the card click through to the button
        for w in (badge, name, example):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_col.addWidget(name)
        text_col.addWidget(example, 0, Qt.AlignLeft)
        row.addLayout(text_col, 1)

        self.toggled.connect(self._on_toggle)

    def sizeHint(self):
        # QPushButton::sizeHint ignores the widget's own layout and returns a
        # text-based hint (~16 px for a textless button), which collapsed the
        # cards to thin strips (v1.5.3 regression). QFrame consulted its
        # layout; the button must do so explicitly.
        layout = self.layout()
        if layout is not None:
            return layout.sizeHint()
        return super().sizeHint()

    def _on_toggle(self, checked: bool):
        self.setProperty("selected", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        if checked:
            if self.on_toggled:
                self.on_toggled(self)
        elif not self._reselect_guard:
            # radio semantics: a card can't be unselected by re-clicking it
            self._reselect_guard = True
            self.setChecked(True)
            self._reselect_guard = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 760)
        self.resize(1240, 820)
        self.settings = QSettings("MediaOrganizer", "MediaOrganizer")
        self.plan: list = []
        self._view: list = []              # plan rows in display (sorted) order
        self.excluded: set[str] = set()    # sources excluded from the plan
        self._sort_col: int = -1           # -1 = scan order
        self._sort_desc: bool = False
        self.scan_worker: ScanWorker | None = None
        self.org_worker: OrganizeWorker | None = None
        self._last_run: tuple | None = None  # (RunLog, dest_dir, summary)
        self._last_active_bytes: int = 0
        self._throughput = ThroughputEstimator()

        self._build_menu()
        self._build_ui()
        self._restore_settings()
        self._restore_geometry()
        self._build_shortcuts()
        self._goto_step(0)
        self._center_on_screen_if_no_geometry()

    def _restore_geometry(self):
        geo = self.settings.value("geometry")
        from PySide6.QtCore import QByteArray
        if not isinstance(geo, (bytes, bytearray, QByteArray)):
            return  # missing or corrupted value — keep default geometry
        try:
            self.restoreGeometry(geo)
        except (TypeError, RuntimeError):
            pass

    def _center_on_screen_if_no_geometry(self):
        if self.settings.value("geometry"):
            return
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.frameGeometry().center())

    def _build_shortcuts(self):
        open_src = QShortcut(QKeySequence("Ctrl+O"), self)
        open_src.setContext(Qt.WindowShortcut)
        open_src.activated.connect(
            lambda: self._pick_folder(self.source_card.edit, suggest_dest=True))
        enter = QShortcut(QKeySequence(Qt.Key_Return), self)
        enter.setContext(Qt.WindowShortcut)
        enter.activated.connect(self._primary_action)
        enter2 = QShortcut(QKeySequence(Qt.Key_Enter), self)
        enter2.setContext(Qt.WindowShortcut)
        enter2.activated.connect(self._primary_action)
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self.cancel_work)

    def _primary_action(self):
        if self._busy():
            return
        idx = self.stack.currentIndex()
        if idx < 2:
            self._go_next()
        elif self.organize_btn.isEnabled():
            self.start_organize()

    # ------------------------------------------------------------------ UI

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        undo_action = QAction(icons.icon("undo", theme.TEXT_DIM, 14),
                              "Undo last run…", self)
        undo_action.triggered.connect(self.undo_last_run)
        file_menu.addAction(undo_action)
        file_menu.addSeparator()
        quit_action = QAction(icons.icon("x", theme.TEXT_DIM, 14), "E&xit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction(icons.icon("info", theme.TEXT_DIM, 14),
                               f"About {APP_NAME}", self)
        about_action.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(about_action)

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar: logo + wordmark + version left, step indicator right
        header = QFrame()
        header.setObjectName("headerBar")
        h = QHBoxLayout(header)
        h.setContentsMargins(24, 14, 24, 14)
        logo = QLabel()
        logo.setPixmap(icons.pixmap("diamond", theme.AMBER, 20))
        wordmark = QLabel(APP_NAME)
        wordmark.setObjectName("wordmark")
        version = QLabel(f"v{__version__}")
        version.setObjectName("versionLabel")
        h.addWidget(logo)
        h.addSpacing(8)
        h.addWidget(wordmark)
        h.addSpacing(8)
        h.addWidget(version, 0, Qt.AlignBottom)
        h.addStretch(1)
        self.step_indicator = _StepIndicator(3)
        h.addWidget(self.step_indicator)
        root.addWidget(header)

        # Content area
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 16)
        content_layout.setSpacing(14)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        content_layout.addWidget(self.stack, 1)

        # Bottom navigation
        nav = QHBoxLayout()
        self.back_btn = QPushButton("Back")
        self.back_btn.setIcon(icons.icon("arrow-left", theme.TEXT, 15))
        self.back_btn.clicked.connect(self._go_back)
        self.next_btn = QPushButton("Continue")
        self.next_btn.setObjectName("primaryButton")
        self.next_btn.setIcon(icons.icon("arrow-right", "#1A1206", 15))
        self.next_btn.clicked.connect(self._go_next)
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.next_btn)
        content_layout.addLayout(nav)
        root.addWidget(content, 1)

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------- step 1

    def _page_header(self, title: str, subtitle: str) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        s = QLabel(subtitle)
        s.setObjectName("pageSubtitle")
        box.addWidget(t)
        box.addWidget(s)
        return box

    def _build_step1(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(18)
        layout.addLayout(self._page_header(
            "Choose your folders",
            "Pick two folders and we'll take care of the rest."))
        layout.addSpacing(4)

        self.source_card = _FolderCard(
            "image",
            "Where are your messy photos?",
            "The folder (and its subfolders) with the photos and videos "
            "you want to sort.",
            "Browse…")
        self.source_card.button.clicked.connect(
            lambda: self._pick_folder(self.source_card.edit, suggest_dest=True))
        layout.addWidget(self.source_card)

        self.dest_card = _FolderCard(
            "folder-input",
            "Where should organized copies go?",
            "Your files are copied here, neatly sorted. Nothing is "
            "overwritten or deleted from the original folder.",
            "Browse…")
        self.dest_card.button.clicked.connect(
            lambda: self._pick_folder(self.dest_card.edit))
        self.dest_card.chip.clicked.connect(self._apply_dest_suggestion)
        layout.addWidget(self.dest_card)
        layout.addStretch(1)
        return page

    def _apply_dest_suggestion(self):
        chip = self.dest_card.chip
        text = chip.property("suggestedPath") or ""
        if text:
            self.dest_card.edit.setText(text)
            chip.setText("Using suggested folder")
            chip.setIcon(icons.icon("check", theme.GREEN, 13))
            chip.setEnabled(False)

    # ------------------------------------------------------------- step 2

    def _build_step2(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addLayout(self._page_header(
            "How should we sort them?",
            "Choose how your photos and videos should be grouped into folders."))
        layout.addSpacing(2)

        self.strategy_radios: list[_StrategyCard] = []

        def _uncheck_others(selected_card):
            for card in self.strategy_radios:
                if card is not selected_card:
                    card.setChecked(False)

        for strategy in STRATEGIES:
            card = _StrategyCard(strategy)
            card.on_toggled = lambda _c: _uncheck_others(_c)
            layout.addWidget(card)
            if strategy.key == DEFAULT_STRATEGY_KEY:
                card.setChecked(True)
            self.strategy_radios.append(card)

        # Advanced options (collapsed by default)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced options")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setIcon(icons.icon("chevron-down",
                                                theme.AMBER_HOVER, 14))
        self.advanced_toggle.toggled.connect(self._on_advanced_toggled)
        layout.addWidget(self.advanced_toggle)

        self.advanced_panel = QFrame()
        self.advanced_panel.setObjectName("card")
        adv = QVBoxLayout(self.advanced_panel)
        adv.setContentsMargins(20, 16, 20, 16)
        adv.setSpacing(10)
        self.recursive_cb = QCheckBox("Also look inside subfolders (recommended)")
        self.recursive_cb.setChecked(True)
        self.copy_radio = QRadioButton("Copy files — originals stay put (safest)")
        self.move_radio = QRadioButton("Move files — originals are removed")
        self.copy_radio.setChecked(True)
        self.dupes_cb = QCheckBox("Skip exact duplicates (same content)")
        self.dupes_cb.setChecked(True)
        self.dry_run_cb = QCheckBox("Dry run — simulate everything, write nothing")
        self.dry_run_cb.setChecked(False)
        self.images_cb = QCheckBox("Photos")
        self.images_cb.setChecked(True)
        self.videos_cb = QCheckBox("Videos")
        self.videos_cb.setChecked(True)
        adv.addWidget(self.recursive_cb)
        adv.addWidget(self.copy_radio)
        adv.addWidget(self.move_radio)
        adv.addWidget(self.dupes_cb)
        adv.addWidget(self.dry_run_cb)
        uncertain_row = QHBoxLayout()
        uncertain_label = QLabel("When only the file's date is available:")
        uncertain_row.addWidget(uncertain_label)
        self.uncertain_combo = QComboBox()
        self.uncertain_combo.addItem(
            "Set them aside in an '_uncertain' folder (recommended)", "aside")
        self.uncertain_combo.addItem("Use the file date anyway", "use")
        uncertain_row.addWidget(self.uncertain_combo)
        uncertain_row.addStretch(1)
        adv.addLayout(uncertain_row)
        types_row = QHBoxLayout()
        types_row.addWidget(QLabel("Include:"))
        types_row.addWidget(self.images_cb)
        types_row.addWidget(self.videos_cb)
        types_row.addStretch(1)
        adv.addLayout(types_row)
        self.advanced_panel.setVisible(False)
        layout.addWidget(self.advanced_panel)
        layout.addStretch(1)
        return page

    def _on_advanced_toggled(self, on: bool):
        self.advanced_panel.setVisible(on)
        self.advanced_toggle.setIcon(icons.icon(
            "chevron-up" if on else "chevron-down", theme.AMBER_HOVER, 14))

    def _selected_strategy(self) -> str:
        for radio in self.strategy_radios:
            if radio.isChecked():
                return radio.property("strategyKey")
        return DEFAULT_STRATEGY_KEY

    def _select_strategy(self, key: str):
        for radio in self.strategy_radios:
            radio.setChecked(radio.property("strategyKey") == key)

    # ------------------------------------------------------------- step 3

    def _build_step3(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addLayout(self._page_header(
            "Check the plan",
            "Look over where everything will go — nothing has been moved yet."))

        top = QHBoxLayout()
        self.plan_summary = QLabel("No files yet — choose your folders and "
                                   "sorting, then the plan appears here.")
        self.plan_summary.setObjectName("muted")
        top.addWidget(self.plan_summary, 1)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.setMaximumWidth(260)
        self.filter_edit.addAction(
            icons.icon("search", theme.TEXT_FAINT, 14),
            QLineEdit.LeadingPosition)
        # debounce keystrokes: filtering is instant per keystroke even on
        # 10k-row plans, but typing shouldn't trigger it per character
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(
            lambda: self._apply_filter(self.filter_edit.text()))
        self.filter_edit.textChanged.connect(
            lambda _t: self._filter_timer.start())
        top.addWidget(self.filter_edit)
        layout.addLayout(top)

        self._plan_model = _PlanModel(self)
        self.table = PlanTableView()
        self.table.setModel(self._plan_model)
        header = self.table.horizontalHeader()
        # user-resizable columns; the destination column absorbs extra width
        for col in range(5):
            mode = (QHeaderView.Stretch if col == COL_DEST
                    else QHeaderView.Interactive)
            header.setSectionResizeMode(col, mode)
            header.resizeSection(col, _COL_DEFAULT_WIDTHS.get(col, 140))
        header.setMinimumSectionSize(70)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.setItemDelegateForColumn(COL_DEST, _ElideMiddleDelegate(self.table))
        self.table.setSortingEnabled(False)  # custom typed sorting instead
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_row_menu)
        self.table.doubleClicked.connect(
            lambda idx: self._open_file_location(idx.row()))
        layout.addWidget(self.table, 1)

        # Progress panel: large, always visible while working
        self.progress_panel = QFrame()
        self.progress_panel.setObjectName("card")
        pp = QVBoxLayout(self.progress_panel)
        pp.setContentsMargins(22, 18, 22, 18)
        pp.setSpacing(10)
        title_row = QHBoxLayout()
        self.progress_title = QLabel("Working…")
        self.progress_title.setObjectName("cardQuestion")
        self.progress_percent = QLabel("")
        self.progress_percent.setObjectName("progressPercent")
        title_row.addWidget(self.progress_title)
        title_row.addStretch(1)
        title_row.addWidget(self.progress_percent)
        pp.addLayout(title_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        pp.addWidget(self.progress)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_icon = QLabel()
        status_icon.setPixmap(icons.pixmap("gauge", theme.TEXT_DIM, 15))
        status_row.addWidget(status_icon)
        self.progress_status = QLabel("")
        status_row.addWidget(self.progress_status, 1)
        pp.addLayout(status_row)
        eta_row = QHBoxLayout()
        eta_row.setSpacing(8)
        eta_icon = QLabel()
        eta_icon.setPixmap(icons.pixmap("clock", theme.TEXT_DIM, 15))
        eta_row.addWidget(eta_icon)
        self.progress_detail = QLabel("")
        self.progress_detail.setObjectName("progressEta")
        eta_row.addWidget(self.progress_detail, 1)
        pp.addLayout(eta_row)
        self.progress_panel.setVisible(False)
        layout.addWidget(self.progress_panel)

        self.undo_limit_label = QLabel(UNDO_LIMITATION)
        self.undo_limit_label.setObjectName("muted")
        self.undo_limit_label.setWordWrap(True)
        layout.addWidget(self.undo_limit_label)

        actions = QHBoxLayout()
        self.dry_run_step3 = QCheckBox("Dry run (simulate)")
        self.dry_run_step3.setToolTip("Run the full pipeline but write nothing")
        actions.addWidget(self.dry_run_step3)
        actions.addSpacing(14)
        self.organize_btn = QPushButton("Organize now")
        self.organize_btn.setObjectName("primaryButton")
        self.organize_btn.setIcon(icons.icon("play", "#1A1206", 15))
        self.organize_btn.setEnabled(False)
        self.organize_btn.clicked.connect(self.start_organize)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("dangerButton")
        self.cancel_btn.setIcon(icons.icon("x", theme.RED, 15))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_work)
        actions.addWidget(self.organize_btn)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        # keep the two dry-run switches in sync (same value -> no re-entry)
        self.dry_run_cb.toggled.connect(self.dry_run_step3.setChecked)
        self.dry_run_step3.toggled.connect(self.dry_run_cb.setChecked)
        return page

    # --------------------------------------------------------- navigation

    def _goto_step(self, index: int):
        self.stack.setCurrentIndex(index)
        self.step_indicator.set_current(index)
        self.back_btn.setEnabled(index > 0)
        self.next_btn.setVisible(index < 2)
        if index == 0:
            self.next_btn.setText("Continue")
        elif index == 1:
            self.next_btn.setText("Check the plan")

    def _go_back(self):
        if self._busy():
            return
        self._goto_step(max(0, self.stack.currentIndex() - 1))

    def _go_next(self):
        idx = self.stack.currentIndex()
        if idx == 0:
            if not self._validate_folders():
                return
            self._goto_step(1)
        elif idx == 1:
            self._goto_step(2)
            self.start_scan()

    def _busy(self) -> bool:
        return bool((self.scan_worker and self.scan_worker.isRunning()) or
                    (self.org_worker and self.org_worker.isRunning()))

    # ------------------------------------------------------------- settings

    def _restore_settings(self):
        self.source_card.edit.setText(self.settings.value("source_dir", ""))
        self.dest_card.edit.setText(self.settings.value("dest_dir", ""))
        self.recursive_cb.setChecked(self.settings.value("recursive", True, type=bool))
        self.move_radio.setChecked(self.settings.value("move_mode", False, type=bool))
        self.copy_radio.setChecked(not self.move_radio.isChecked())
        self.dupes_cb.setChecked(self.settings.value("skip_duplicates", True, type=bool))
        self.dry_run_cb.setChecked(self.settings.value("dry_run", False, type=bool))
        idx = self.uncertain_combo.findData(
            self.settings.value("uncertain", "aside"))
        self.uncertain_combo.setCurrentIndex(max(idx, 0))
        self.images_cb.setChecked(self.settings.value("images", True, type=bool))
        self.videos_cb.setChecked(self.settings.value("videos", True, type=bool))
        self._select_strategy(self.settings.value(
            "strategy", self.settings.value("pattern_key", DEFAULT_STRATEGY_KEY)))
        self._restore_column_widths()

    def _save_settings(self):
        self.settings.setValue("source_dir", self.source_card.edit.text())
        self.settings.setValue("dest_dir", self.dest_card.edit.text())
        self.settings.setValue("recursive", self.recursive_cb.isChecked())
        self.settings.setValue("move_mode", self.move_radio.isChecked())
        self.settings.setValue("skip_duplicates", self.dupes_cb.isChecked())
        self.settings.setValue("dry_run", self.dry_run_cb.isChecked())
        self.settings.setValue("uncertain", self.uncertain_combo.currentData())
        self.settings.setValue("images", self.images_cb.isChecked())
        self.settings.setValue("videos", self.videos_cb.isChecked())
        self.settings.setValue("strategy", self._selected_strategy())
        self._save_column_widths()

    def closeEvent(self, event):
        self._save_settings()
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    # -------------------------------------------------------------- actions

    def _pick_folder(self, edit: QLineEdit, suggest_dest: bool = False):
        path = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if not path:
            return
        edit.setText(path)
        if suggest_dest and not self.dest_card.edit.text().strip():
            suggested = path.rstrip("/\\") + "_Organized"
            chip = self.dest_card.chip
            chip.setEnabled(True)
            chip.setIcon(icons.icon("sparkles", theme.AMBER_HOVER, 13))
            chip.setText(f"Use suggested:  {suggested}")
            chip.setProperty("suggestedPath", suggested)
            chip.setVisible(True)

    def _validate_folders(self) -> bool:
        src = Path(self.source_card.edit.text().strip())
        dst = self.dest_card.edit.text().strip()
        if not src.is_dir():
            QMessageBox.warning(
                self, APP_NAME,
                "Please choose the folder where your photos are now.")
            return False
        if not dst or dst == ".":
            QMessageBox.warning(
                self, APP_NAME,
                "Please choose where the organized copies should go.")
            return False
        blocked = destination_blocks_scan(src, dst)
        if blocked:
            self._explain_blocked_destination(blocked)
            return False
        return True

    def _explain_blocked_destination(self, reason: str) -> None:
        """Tell the user why this destination would scan nothing.

        Happens before any copy or move. The sentence stays in the plan
        summary after the dialog is dismissed.
        """
        self.plan = []
        self.excluded.clear()
        self._refresh_table()
        self.plan_summary.setText(reason)
        self.status_label.setText(reason)
        self.organize_btn.setEnabled(False)
        QMessageBox.warning(self, APP_NAME, reason)

    def _options(self) -> OrganizeOptions | None:
        if not self._validate_folders():
            return None
        src = Path(self.source_card.edit.text().strip())
        dst = Path(self.dest_card.edit.text().strip())
        if self.move_radio.isChecked() and not self.dry_run_cb.isChecked():
            answer = QMessageBox.question(
                self, APP_NAME,
                "Move mode removes files from the original folder.\n"
                "Are you sure you want to move instead of copy?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return None
        return OrganizeOptions(
            source_dir=src,
            dest_dir=dst,
            recursive=self.recursive_cb.isChecked(),
            strategy=self._selected_strategy(),
            copy_mode=self.copy_radio.isChecked(),
            skip_duplicates=self.dupes_cb.isChecked(),
            include_images=self.images_cb.isChecked(),
            include_videos=self.videos_cb.isChecked(),
            dry_run=self.dry_run_cb.isChecked(),
            uncertain=self.uncertain_combo.currentData(),
        )

    # ---------------------------------------------------------- scan/plan

    def start_scan(self):
        options = self._options()
        if options is None:
            self._goto_step(0)
            return
        self._set_busy(True, "Looking at your photos…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._show_progress_panel("Scanning files…", indeterminate=True)
        self._throughput.reset()
        self.progress_status.setText("Counting files…")
        self.progress_detail.setText("")
        self.plan_summary.setText("Building the plan…")
        self.scan_worker = ScanWorker(options, self)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished_plan.connect(self._on_plan_ready)
        self.scan_worker.cancelled.connect(self._on_scan_cancelled)
        self.scan_worker.failed.connect(self._on_worker_failed)
        self.scan_worker.start()

    def _on_scan_progress(self, done: int, total: int, name: str):
        if total <= 0:
            # still counting: keep the indeterminate bar
            self.progress_status.setText(name or "Counting files…")
            return
        if self.progress.maximum() == 0:
            # total known -> switch to a determinate bar
            self.progress.setRange(0, 100)
        done = min(done, total)
        percent = int(100 * done / total) if total else 0
        self.progress.setValue(percent)
        self.progress_percent.setText(f"{percent}%")
        self._throughput.add(done)  # files (works like bytes: just a counter)
        rate = self._throughput.rate()
        eta = self._throughput.eta_seconds(max(0, total - done))
        current = f" · {name}" if name else ""
        self.progress_status.setText(
            f"Scanning {done:,} of {total:,} files{current}")
        rate_txt = f"{rate:,.0f} files/s" if rate > 0 else "—"
        self.progress_detail.setText(
            f"{percent}%  ·  {rate_txt}  ·  {format_eta(eta)} left")

    def _show_progress_panel(self, title: str, indeterminate: bool = False):
        self.progress_title.setText(title)
        if indeterminate:
            self.progress.setRange(0, 0)  # busy-pulse style
            self.progress_percent.setText("")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress_percent.setText("0%")
        self.progress_panel.setVisible(True)

    def _hide_progress_panel(self):
        self.progress_panel.setVisible(False)

    def _on_plan_ready(self, plan: list):
        QApplication.restoreOverrideCursor()
        self.plan = plan
        self.excluded.clear()
        self._set_busy(False)
        self._hide_progress_panel()
        self._refresh_table()
        self._refresh_summary()
        self.status_label.setText("Plan ready — take a look, then press "
                                  "\"Organize now\".")
        self.organize_btn.setEnabled(bool(self._active_plan()))

    def _on_scan_cancelled(self):
        """A cancelled scan is not an empty library and not a finished plan.

        Organize stays off. A partial duplicate set or a half-built plan is
        dropped, including a plan left over from an earlier scan.
        """
        QApplication.restoreOverrideCursor()
        self.plan = []
        self.excluded.clear()
        self._set_busy(False)
        self._hide_progress_panel()
        self._refresh_table()
        self.plan_summary.setText("Scan cancelled — no plan was kept.")
        self.status_label.setText("Scan cancelled.")
        self.organize_btn.setEnabled(False)

    def _active_plan(self) -> list:
        """Plan rows not excluded by the user (duplicates already flagged)."""
        return [p for p in self.plan if str(p.source) not in self.excluded]

    def _refresh_summary(self):
        if not self.plan:
            self.plan_summary.setText(
                "No files found — nothing to organize.")
            return
        active = self._active_plan()
        total_bytes = sum(p.size for p in active)
        copy_bytes = sum(p.size for p in active
                         if not p.is_duplicate and p.destination)
        dupes = sum(1 for p in active if p.is_duplicate)
        undated = sum(1 for p in active
                      if not p.capture.found and not p.is_duplicate)
        uncertain = sum(1 for p in active
                        if p.capture.found and not p.is_duplicate
                        and p.capture.source is DateSource.MTIME)
        folders = len({p.destination.parent for p in active if p.destination})
        excl = len(self.excluded)
        parts = [f"{len(active):,} files",
                 f"{format_bytes(total_bytes)} total",
                 "1 folder" if folders == 1 else f"{folders} folders"]
        if dupes:
            parts.append("1 exact duplicate will be skipped" if dupes == 1
                         else f"{dupes} exact duplicates will be skipped")
        if undated:
            parts.append("1 without a date" if undated == 1
                         else f"{undated} without a date")
        if uncertain:
            aside = self.uncertain_combo.currentData() == "aside"
            parts.append(
                f"{uncertain} uncertain date{'s' if uncertain != 1 else ''} "
                + ("set aside" if aside else "used (file date)"))
        if excl:
            parts.append(f"{excl} excluded")
        verb = "move" if self.move_radio.isChecked() else "copy"
        parts.append(f"{format_bytes(copy_bytes)} to {verb}")
        self.plan_summary.setText("  ·  ".join(parts))

    # ----------------------------------------------------- sorting & columns

    def _on_header_clicked(self, col: int):
        if not self.plan:
            return
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        self.table.horizontalHeader().setSortIndicator(
            col, Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder)
        self._refresh_table()

    def _save_column_widths(self):
        header = self.table.horizontalHeader()
        widths = [header.sectionSize(c) for c in range(5)]
        self.settings.setValue("col_widths", widths)
        self.settings.setValue("sort_col", self._sort_col)
        self.settings.setValue("sort_desc", self._sort_desc)

    def _restore_column_widths(self):
        widths = self.settings.value("col_widths")
        if widths:
            header = self.table.horizontalHeader()
            for col in range(5):
                try:
                    w = int(widths[col])
                except (IndexError, TypeError, ValueError):
                    continue
                if w >= 70 and header.sectionResizeMode(col) != QHeaderView.Stretch:
                    header.resizeSection(col, w)
        self._sort_col = int(self.settings.value("sort_col", -1, type=int))
        self._sort_desc = self.settings.value("sort_desc", False, type=bool)
        if 0 <= self._sort_col < 5:
            self.table.horizontalHeader().setSortIndicator(
                self._sort_col,
                Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder)

    # --------------------------------------------------------- row actions

    def _item_at_row(self, row: int):
        if 0 <= row < len(self._view):
            return self._view[row]
        return None

    def _open_file_location(self, row: int):
        item = self._item_at_row(row)
        if item is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.source.parent)))

    def _set_excluded(self, item, excluded: bool):
        key = str(item.source)
        if excluded:
            self.excluded.add(key)
        else:
            self.excluded.discard(key)
        self._refresh_table()
        self._refresh_summary()
        self.organize_btn.setEnabled(bool(self._active_plan()))

    def _on_row_menu(self, pos):
        row = self.table.rowAt(pos.y())
        item = self._item_at_row(row)
        if item is None:
            return
        is_excluded = str(item.source) in self.excluded
        menu = QMenu(self)
        open_action = menu.addAction(
            icons.icon("external-link", theme.TEXT_DIM, 14),
            "Open file location")
        menu.addSeparator()
        exclude_action = menu.addAction(
            icons.icon("x", theme.RED, 14) if not is_excluded
            else icons.icon("check", theme.GREEN, 14),
            "Include in plan" if is_excluded else "Exclude from plan")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is open_action:
            self._open_file_location(row)
        elif chosen is exclude_action:
            self._set_excluded(item, not is_excluded)

    def _refresh_table(self):
        """Point the preview at the current sort order.

        The view is a table model: it does not build a widget for every
        file, so a few thousand rows stay responsive. Filter text is
        precomputed once per row.
        """
        plan = self.plan
        if 0 <= self._sort_col < 5 and plan:
            view = sorted_plan_items(plan, _COL_KEYS[self._sort_col],
                                     self._sort_desc)
        else:
            view = list(plan)
        self._view = view
        import os
        dest_root = self.dest_card.edit.text().strip()
        root_norm = (os.path.normcase(os.path.normpath(dest_root))
                     if dest_root else None)
        self._plan_model.set_rows(
            [self._row_presentation(item, root_norm) for item in view])
        self._apply_filter(self.filter_edit.text())

    def _row_presentation(self, item, root_norm) -> dict:
        is_excluded = str(item.source) in self.excluded
        kind_icon = "film" if item.kind == "video" else "image"
        name_tip = str(item.source) + (
            "\n(excluded from plan)" if is_excluded else "")
        if item.is_duplicate:
            date_text, src_text = "duplicate", "exact duplicate (same content)"
            conf = Confidence.LOW
            src_icon = "copy"
        elif item.capture.found:
            date_text = item.capture.date.strftime("%Y-%m-%d %H:%M:%S")
            src_text = _SOURCE_LABELS[item.capture.source]
            conf = item.capture.confidence
            src_icon = icons.SOURCE_ICONS.get(
                item.capture.source.value, "file-text")
        else:
            date_text, src_text, conf = "—", "no date found", Confidence.LOW
            src_icon = "alert-triangle"
        # String-only relative path — resolve() per row froze the UI.
        rel = relative_destination_fast(item.destination, root_norm)
        dest_text = elide_middle(rel, 120)
        dest_tip = str(item.destination) if item.destination else ""
        size_text = _fmt_size(item.size)
        haystack = " ".join(
            (item.source.name, date_text, src_text, rel, size_text)).lower()
        tips = (name_tip, "", item.capture.detail or "", dest_tip, "")
        return {
            "text": (item.source.name, date_text, src_text, dest_text, size_text),
            "tips": tips,
            "name_icon": kind_icon,
            "src_icon": src_icon,
            "src_color": _CONFIDENCE_COLORS[conf],
            "hay": haystack,
            "dim": is_excluded,
        }

    # backwards-compatible alias (used by older tools/tests)
    def _populate_table(self, plan: list):
        self.plan = plan
        self._refresh_table()

    def _apply_filter(self, text: str):
        """Show/hide rows by substring match against the precomputed haystack."""
        needle = text.strip().lower()
        model = self._plan_model
        self.table.setUpdatesEnabled(False)
        try:
            for row in range(model.rowCount()):
                if not needle:
                    self.table.setRowHidden(row, False)
                    continue
                hay = model.haystack(row)
                self.table.setRowHidden(row, needle not in (hay or ""))
        finally:
            self.table.setUpdatesEnabled(True)

    # ---------------------------------------------------------- organize

    def start_organize(self):
        active = self._active_plan()
        if not active:
            return
        options = self._options()
        if options is None:
            return

        # --- free-space preflight (skipped for dry runs) ---
        if not options.dry_run:
            needed = sum(p.size for p in active
                         if not p.is_duplicate and p.destination)
            space = free_space_status(options.dest_dir, needed)
            if not space["ok"]:
                QMessageBox.critical(
                    self, APP_NAME,
                    f"Not enough free space.\n\n"
                    f"Need {format_bytes(space['needed'])}, but only "
                    f"{format_bytes(space['free'])} free on {space['drive']}\n\n"
                    f"Free up space or choose another destination.")
                return
            if space["tight"]:
                answer = QMessageBox.question(
                    self, APP_NAME,
                    f"This will nearly fill {space['drive']} "
                    f"({format_bytes(space['needed'])} needed, "
                    f"{format_bytes(space['free'])} free).\nContinue anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return

        # --- crash-journal resume ---
            journal = find_unfinished_journal(options.dest_dir)
            if journal is not None:
                done_before = completed_sources(journal)
                answer = QMessageBox.question(
                    self, APP_NAME,
                    f"A previous run was interrupted (power loss?) — "
                    f"{len(done_before):,} files were already organized.\n\n"
                    f"Yes = Resume (skip them) · No = Discard and start over",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if answer == QMessageBox.Yes:
                    active = exclude_completed_sources(active, done_before)
                    if not active:
                        QMessageBox.information(
                            self, APP_NAME,
                            "Everything was already organized — nothing left "
                            "to resume.")
                        discard_journal(options.dest_dir)
                        return
                else:
                    discard_journal(options.dest_dir)

        self._set_busy(True, "Organizing…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._throughput.reset()
        self._last_active_bytes = sum(p.size for p in active
                                      if not p.is_duplicate and p.destination)
        title = ("Simulating (dry run)…" if options.dry_run
                 else "Organizing your photos…")
        self._show_progress_panel(title, indeterminate=False)
        self.progress_status.setText("Getting ready…")
        self.progress_detail.setText("")
        self.org_worker = OrganizeWorker(active, options, self)
        self.org_worker.progress.connect(self._on_org_progress)
        self.org_worker.finished_run.connect(self._on_run_finished)
        self.org_worker.failed.connect(self._on_worker_failed)
        self.org_worker.start()

    def _on_org_progress(self, i: int, total: int, name: str,
                         bytes_done: int, total_bytes: int):
        bytes_done = int(bytes_done)
        total_bytes = int(total_bytes)
        if total_bytes > 0:
            percent = int(100 * bytes_done / total_bytes)
        else:
            percent = int(100 * i / total) if total else 0
        percent = max(0, min(percent, 100))
        self.progress.setValue(percent)
        self.progress_percent.setText(f"{percent}%")
        self._throughput.add(bytes_done)
        rate = self._throughput.rate()
        eta = self._throughput.eta_seconds(max(0, total_bytes - bytes_done))
        current = f"  ·  {name}" if name else ""
        self.progress_status.setText(
            f"{min(i + 1, total):,} of {total:,} files{current}")
        self.progress_detail.setText(
            f"{percent}%  ·  {format_rate(rate)}  ·  {format_eta(eta)} left")
        self.status_label.setText(
            f"Organizing {min(i + 1, total):,} of {total:,}{current}")

    def _on_run_finished(self, log, summary: dict):
        QApplication.restoreOverrideCursor()
        self._set_busy(False)
        self._hide_progress_panel()
        self.plan = []
        self.organize_btn.setEnabled(False)
        dest_dir = self.dest_card.edit.text().strip()
        self._last_run = (log, dest_dir, summary)

        folders = len({str(Path(op.destination).parent) for op in log.operations
                       if op.status == "done"})
        status, text = finished_run_lines(
            summary, folders=folders, total_bytes=self._last_active_bytes)
        self.status_label.setText(status)

        box = QMessageBox(self)
        dry = summary.get("dry_run", False)
        box.setWindowTitle(f"{APP_NAME} — {'Dry run complete' if dry else 'Done'}")
        box.setIconPixmap(icons.pixmap(
            "scan" if dry else "check-circle",
            theme.AMBER if dry else theme.GREEN, 44))
        box.setText(text)
        open_btn = box.addButton("Open folder", QMessageBox.AcceptRole)
        open_btn.setIcon(icons.icon("external-link", theme.TEXT, 14))
        undo_btn = box.addButton("Undo", QMessageBox.DestructiveRole)
        undo_btn.setIcon(icons.icon("undo", theme.TEXT, 14))
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(dest_dir))
        elif clicked is undo_btn:
            self._undo_log(log, dest_dir)

    def cancel_work(self):
        for worker in (self.scan_worker, self.org_worker):
            if worker and worker.isRunning():
                worker.cancel()
        self.status_label.setText("Cancelling…")

    def undo_last_run(self):
        dst = Path(self.dest_card.edit.text().strip())
        if not dst.is_dir():
            QMessageBox.warning(
                self, APP_NAME,
                "Choose the destination folder used by the run first.")
            return
        logs = list_run_logs(dst)
        if not logs:
            QMessageBox.information(self, APP_NAME,
                                    "No operation log found in that folder.")
            return
        log = next((item for item in logs if not item.undone), None)
        if log is None:
            QMessageBox.information(
                self, APP_NAME,
                "The runs saved in that folder have already been undone.")
            return
        self._undo_log(log, dst)

    def _undo_log(self, log, dst):
        if log.undone:
            QMessageBox.information(self, APP_NAME,
                                    "That run has already been undone.")
            return
        answer = QMessageBox.question(
            self, APP_NAME,
            f"Undo the newest run that can still be undone?\n\n"
            f"It started at {log.started_at} "
            f"({len(log.operations)} operations).\n\n"
            f"{UNDO_LIMITATION}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        result = undo_log(log, dst)
        QMessageBox.information(self, APP_NAME, undo_result_message(result))

    # -------------------------------------------------------------- helpers

    def _set_busy(self, busy: bool, message: str = ""):
        self.organize_btn.setEnabled(not busy and bool(self._active_plan()))
        self.cancel_btn.setEnabled(busy)
        self.next_btn.setEnabled(not busy)
        self.back_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    def _on_worker_failed(self, message: str):
        QApplication.restoreOverrideCursor()
        self._set_busy(False)
        self._hide_progress_panel()
        QMessageBox.critical(self, APP_NAME, f"Something went wrong:\n{message}")
