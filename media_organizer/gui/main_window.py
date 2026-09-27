"""Main window for Media Organizer — a friendly 3-step guided flow.

Step 1  Choose your folders
Step 2  How should we sort them?
Step 3  Check the plan  ->  Organize now
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QRectF
from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QStackedWidget,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.display import elide_middle, relative_destination
from ..core.eta import ThroughputEstimator, format_eta, format_rate
from ..core.metadata import Confidence, DateSource
from ..core.organizer import STRATEGIES, OrganizeOptions
from ..core.plan import load_log, undo_log
from ..core.strategies import DEFAULT_STRATEGY_KEY
from . import theme
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

_STEP_TITLES = ("Choose your folders", "How should we sort them?",
                "Check the plan")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _icon_pixmap(kind: str, size: int = 44) -> QPixmap:
    """Hand-drawn amber line icons (photo / folder), no font dependency."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    # tinted disc background
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.AMBER_TINT))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    amber = QPen(QColor(theme.AMBER), 2.0, Qt.SolidLine,
                 Qt.RoundCap, Qt.RoundJoin)
    p.setPen(amber)
    p.setBrush(Qt.NoBrush)
    m = size * 0.24  # margin of the glyph inside the disc
    if kind == "photo":
        frame = QRectF(m, m, size - 2 * m, size - 2 * m)
        p.drawRoundedRect(frame, 4, 4)
        # sun
        p.drawEllipse(QRectF(size * 0.60, size * 0.32, size * 0.10, size * 0.10))
        # mountain ridge
        path = QPainterPath()
        path.moveTo(m + 2, size - m - 3)
        path.lineTo(size * 0.46, size * 0.52)
        path.lineTo(size * 0.58, size * 0.66)
        path.lineTo(size * 0.66, size * 0.58)
        path.lineTo(size - m - 2, size - m - 3)
        p.drawPath(path)
    else:  # folder
        path = QPainterPath()
        x0, y0 = m, size * 0.34
        x1, y1 = size - m, size - m
        path.moveTo(x0, y0 + 3)
        path.lineTo(x0, y1 - 3)
        path.quadTo(x0, y1, x0 + 3, y1)
        path.lineTo(x1 - 3, y1)
        path.quadTo(x1, y1, x1, y1 - 3)
        path.lineTo(x1, y0 + 6)
        path.quadTo(x1, y0 + 3, x1 - 3, y0 + 3)
        path.lineTo(size * 0.55, y0 + 3)
        path.lineTo(size * 0.48, y0)
        path.lineTo(x0 + 3, y0)
        path.quadTo(x0, y0, x0, y0 + 3)
        p.drawPath(path)
    p.end()
    return pm


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
    """Drop-zone style folder picker with painted icon and pill path field."""

    def __init__(self, icon: str, question: str, hint: str, button_text: str,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        top = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(_icon_pixmap(icon))
        top.addWidget(icon_label)
        text_col = QVBoxLayout()
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
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        layout.addLayout(row)

        self.chip = QPushButton()
        self.chip.setObjectName("chip")
        self.chip.setVisible(False)
        self.chip.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.chip, 0, Qt.AlignLeft)


class _StrategyCard(QFrame):
    """Selectable strategy card (radio-button semantics for compatibility)."""

    def __init__(self, strategy, parent=None):
        super().__init__(parent)
        self.setObjectName("strategyCard")
        self.setProperty("strategyKey", strategy.key)
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = False
        self.on_toggled = None  # set by MainWindow

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 12, 18, 12)
        row.setSpacing(14)
        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        name = QLabel(strategy.name)
        name.setObjectName("strategyName")
        example = QLabel(f"e.g.  {strategy.example}")
        example.setObjectName("examplePill")
        text_col.addWidget(name)
        text_col.addWidget(example, 0, Qt.AlignLeft)
        row.addLayout(text_col, 1)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if self._checked == checked:
            return
        self._checked = checked
        self.setProperty("selected", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        if checked and self.on_toggled:
            self.on_toggled(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(True)
        super().mouseReleaseEvent(event)


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
        title = QLabel(f"◆ {APP_NAME}")
        title.setObjectName("wordmark")
        layout.addWidget(title)
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 760)
        self.resize(1240, 820)
        self.settings = QSettings("MediaOrganizer", "MediaOrganizer")
        self.plan: list = []
        self.scan_worker: ScanWorker | None = None
        self.org_worker: OrganizeWorker | None = None
        self._last_run: tuple | None = None  # (RunLog, dest_dir, summary)
        self._throughput = ThroughputEstimator()

        self._build_menu()
        self._build_ui()
        self._restore_settings()
        self._goto_step(0)
        self._center_on_screen()

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.frameGeometry().center())

    # ------------------------------------------------------------------ UI

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        undo_action = QAction("Undo last run…", self)
        undo_action.triggered.connect(self.undo_last_run)
        file_menu.addAction(undo_action)
        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction(f"About {APP_NAME}", self)
        about_action.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(about_action)

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar: wordmark + version left, step indicator right
        header = QFrame()
        header.setObjectName("headerBar")
        h = QHBoxLayout(header)
        h.setContentsMargins(24, 14, 24, 14)
        wordmark = QLabel(f"◆ {APP_NAME}")
        wordmark.setObjectName("wordmark")
        version = QLabel(f"v{__version__}")
        version.setObjectName("versionLabel")
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
        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self._go_back)
        self.next_btn = QPushButton("Continue →")
        self.next_btn.setObjectName("primaryButton")
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
            "photo",
            "Where are your messy photos?",
            "The folder (and its subfolders) with the photos and videos "
            "you want to sort.",
            "Browse…")
        self.source_card.button.clicked.connect(
            lambda: self._pick_folder(self.source_card.edit, suggest_dest=True))
        layout.addWidget(self.source_card)

        self.dest_card = _FolderCard(
            "folder",
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
        text = self.chip.property("suggestedPath") or ""
        if text:
            self.dest_card.edit.setText(text)
            self.dest_card.chip.setVisible(False)

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
        self.advanced_toggle.setText("Advanced options ▾")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
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
        self.images_cb = QCheckBox("Photos")
        self.images_cb.setChecked(True)
        self.videos_cb = QCheckBox("Videos")
        self.videos_cb.setChecked(True)
        adv.addWidget(self.recursive_cb)
        adv.addWidget(self.copy_radio)
        adv.addWidget(self.move_radio)
        adv.addWidget(self.dupes_cb)
        types_row = QHBoxLayout()
        types_row.addWidget(QLabel("Include:"))
        types_row.addWidget(self.images_cb)
        types_row.addWidget(self.videos_cb)
        types_row.addStretch(1)
        adv.addLayout(types_row)
        self.advanced_panel.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        layout.addWidget(self.advanced_panel)
        layout.addStretch(1)
        return page

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
        self.plan_summary = QLabel("Building the plan…")
        self.plan_summary.setObjectName("muted")
        top.addWidget(self.plan_summary, 1)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.setMaximumWidth(260)
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit)
        layout.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["FILE", "DATE TAKEN", "FOUND VIA", "NEW LOCATION", "SIZE"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_DEST, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeToContents)
        self.table.setItemDelegateForColumn(COL_DEST, _ElideMiddleDelegate(self.table))
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
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
        self.progress_status = QLabel("")
        pp.addWidget(self.progress_status)
        self.progress_detail = QLabel("")
        self.progress_detail.setObjectName("progressEta")
        pp.addWidget(self.progress_detail)
        self.progress_panel.setVisible(False)
        layout.addWidget(self.progress_panel)

        actions = QHBoxLayout()
        self.organize_btn = QPushButton("✔  Organize now")
        self.organize_btn.setObjectName("primaryButton")
        self.organize_btn.setEnabled(False)
        self.organize_btn.clicked.connect(self.start_organize)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("dangerButton")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_work)
        actions.addWidget(self.organize_btn)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    # --------------------------------------------------------- navigation

    def _goto_step(self, index: int):
        self.stack.setCurrentIndex(index)
        self.step_indicator.set_current(index)
        self.back_btn.setEnabled(index > 0)
        self.next_btn.setVisible(index < 2)
        if index == 0:
            self.next_btn.setText("Continue →")
        elif index == 1:
            self.next_btn.setText("Check the plan →")

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
        self.images_cb.setChecked(self.settings.value("images", True, type=bool))
        self.videos_cb.setChecked(self.settings.value("videos", True, type=bool))
        self._select_strategy(self.settings.value(
            "strategy", self.settings.value("pattern_key", DEFAULT_STRATEGY_KEY)))

    def _save_settings(self):
        self.settings.setValue("source_dir", self.source_card.edit.text())
        self.settings.setValue("dest_dir", self.dest_card.edit.text())
        self.settings.setValue("recursive", self.recursive_cb.isChecked())
        self.settings.setValue("move_mode", self.move_radio.isChecked())
        self.settings.setValue("skip_duplicates", self.dupes_cb.isChecked())
        self.settings.setValue("images", self.images_cb.isChecked())
        self.settings.setValue("videos", self.videos_cb.isChecked())
        self.settings.setValue("strategy", self._selected_strategy())

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    # -------------------------------------------------------------- actions

    def _pick_folder(self, edit: QLineEdit, suggest_dest: bool = False):
        path = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if not path:
            return
        edit.setText(path)
        if suggest_dest and not self.dest_card.edit.text().strip():
            suggested = f"{path.rstrip('/\\\\')}_Organized"
            self.dest_card.chip.setText(f"Use suggested:  {suggested}")
            self.dest_card.chip.setProperty("suggestedPath", suggested)
            self.dest_card.chip.setVisible(True)

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
        return True

    def _options(self) -> OrganizeOptions | None:
        if not self._validate_folders():
            return None
        src = Path(self.source_card.edit.text().strip())
        dst = Path(self.dest_card.edit.text().strip())
        if self.move_radio.isChecked():
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
        )

    # ---------------------------------------------------------- scan/plan

    def start_scan(self):
        options = self._options()
        if options is None:
            self._goto_step(0)
            return
        self._set_busy(True, "Looking at your photos…")
        self._show_progress_panel("Scanning files…", indeterminate=True)
        self.progress_status.setText("Reading your photos and videos…")
        self.progress_detail.setText("")
        self.plan_summary.setText("Building the plan…")
        self.scan_worker = ScanWorker(options, self)
        self.scan_worker.progress.connect(
            lambda i, name: self.progress_status.setText(
                f"Scanning files… {name}"))
        self.scan_worker.finished_plan.connect(self._on_plan_ready)
        self.scan_worker.failed.connect(self._on_worker_failed)
        self.scan_worker.start()

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
        self.plan = plan
        self._set_busy(False)
        self._hide_progress_panel()
        self._populate_table(plan)
        dupes = sum(1 for p in plan if p.is_duplicate)
        undated = sum(1 for p in plan if not p.capture.found and not p.is_duplicate)
        folders = len({p.destination.parent for p in plan if p.destination})
        dupe_txt = ("1 exact duplicate will be skipped" if dupes == 1
                    else f"{dupes} exact duplicates will be skipped")
        undated_txt = ("1 without a date" if undated == 1
                       else f"{undated} without a date")
        self.plan_summary.setText(
            f"{len(plan)} files · {folders} folders · {dupe_txt} · {undated_txt}")
        self.status_label.setText("Plan ready — take a look, then press "
                                  "\"Organize now\".")
        self.organize_btn.setEnabled(bool(plan))

    def _populate_table(self, plan: list):
        dest_root = self.dest_card.edit.text().strip()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(plan))
        for row, item in enumerate(plan):
            name_item = QTableWidgetItem(item.source.name)
            name_item.setToolTip(str(item.source))
            self.table.setItem(row, COL_NAME, name_item)

            if item.is_duplicate:
                date_text, src_text = "duplicate", "exact duplicate (same content)"
                conf = Confidence.LOW
            elif item.capture.found:
                date_text = item.capture.date.strftime("%Y-%m-%d %H:%M:%S")
                src_text = _SOURCE_LABELS[item.capture.source]
                conf = item.capture.confidence
            else:
                date_text, src_text, conf = "—", "no date found", Confidence.LOW
            self.table.setItem(row, COL_DATE, QTableWidgetItem(date_text))

            src_item = QTableWidgetItem(f"● {src_text}")
            src_item.setForeground(QColor(_CONFIDENCE_COLORS[conf]))
            src_item.setToolTip(item.capture.detail)
            self.table.setItem(row, COL_SOURCE, src_item)

            # Show the path relative to the destination root; full path in tooltip
            rel = relative_destination(item.destination, dest_root or None)
            dest_item = QTableWidgetItem(elide_middle(rel, 120))
            if item.destination:
                dest_item.setToolTip(str(item.destination))
            self.table.setItem(row, COL_DEST, dest_item)

            size_item = QTableWidgetItem(_fmt_size(item.size))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, COL_SIZE, size_item)
        self.table.setSortingEnabled(True)
        self._apply_filter(self.filter_edit.text())

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            if not needle:
                self.table.setRowHidden(row, False)
                continue
            hay = " ".join(
                (self.table.item(row, c).text() if self.table.item(row, c) else "")
                for c in range(self.table.columnCount())
            ).lower()
            self.table.setRowHidden(row, needle not in hay)

    # ---------------------------------------------------------- organize

    def start_organize(self):
        if not self.plan:
            return
        options = self._options()
        if options is None:
            return
        self._set_busy(True, "Organizing…")
        self._throughput.reset()
        self._show_progress_panel("Organizing your photos…", indeterminate=False)
        self.progress_status.setText("Getting ready…")
        self.progress_detail.setText("")
        self.org_worker = OrganizeWorker(self.plan, options, self)
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
        self._set_busy(False)
        self._hide_progress_panel()
        self.plan = []
        self.organize_btn.setEnabled(False)
        dest_dir = self.dest_card.edit.text().strip()
        self._last_run = (log, dest_dir, summary)

        mode = "copied" if summary["copied"] or not summary["moved"] else "moved"
        done_n = summary["copied"] + summary["moved"]
        folders = len({str(Path(op.destination).parent) for op in log.operations
                       if op.status == "done"})
        self.status_label.setText(f"Finished: {done_n} {mode}.")

        box = QMessageBox(self)
        box.setWindowTitle(f"{APP_NAME} — Done")
        box.setIcon(QMessageBox.Information)
        text = f"Done! {done_n} photos/videos {mode} into {folders} folders."
        details = []
        if summary["skipped_duplicates"]:
            details.append(f"{summary['skipped_duplicates']} exact duplicates skipped")
        if summary["undated"]:
            details.append(f"{summary['undated']} without a date (in _undated)")
        if summary["errors"]:
            details.append(f"{summary['errors']} couldn't be read (skipped)")
        if summary["cancelled"]:
            details.append("the run was cancelled part-way")
        if details:
            text += "\n\n" + "\n".join(f"• {d}" for d in details)
        box.setText(text)
        open_btn = box.addButton("Open folder", QMessageBox.AcceptRole)
        undo_btn = box.addButton("Undo", QMessageBox.DestructiveRole)
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
        log = load_log(dst)
        if log is None:
            QMessageBox.information(self, APP_NAME,
                                    "No operation log found in that folder.")
            return
        self._undo_log(log, dst)

    def _undo_log(self, log, dst):
        if log.undone:
            QMessageBox.information(self, APP_NAME,
                                    "That run has already been undone.")
            return
        answer = QMessageBox.question(
            self, APP_NAME,
            f"Undo the run from {log.started_at} "
            f"({len(log.operations)} operations)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        result = undo_log(log, dst)
        QMessageBox.information(
            self, APP_NAME,
            f"Undo complete: {result['undone']} files restored, "
            f"{result['skipped']} skipped, {result['failed']} failed.")

    # -------------------------------------------------------------- helpers

    def _set_busy(self, busy: bool, message: str = ""):
        self.organize_btn.setEnabled(not busy and bool(self.plan))
        self.cancel_btn.setEnabled(busy)
        self.next_btn.setEnabled(not busy)
        self.back_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    def _on_worker_failed(self, message: str):
        self._set_busy(False)
        self._hide_progress_panel()
        QMessageBox.critical(self, APP_NAME, f"Something went wrong:\n{message}")
