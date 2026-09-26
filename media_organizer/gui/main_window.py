"""Main window for Media Organizer — a friendly 3-step guided flow.

Step 1  Choose your folders
Step 2  How should we sort them?
Step 3  Check the plan  ->  Organize now
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QColor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QStackedWidget, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.display import elide_middle, relative_destination
from ..core.eta import ThroughputEstimator, format_eta, format_rate
from ..core.metadata import Confidence, DateSource
from ..core.organizer import STRATEGIES, OrganizeOptions
from ..core.plan import load_log, undo_log
from ..core.strategies import DEFAULT_STRATEGY_KEY
from .workers import OrganizeWorker, ScanWorker

_CONFIDENCE_COLORS = {
    Confidence.HIGH: "#3fb950",
    Confidence.MEDIUM: "#d29922",
    Confidence.LOW: "#8b949e",
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

_STEPS = ("1 · Choose your folders", "2 · How should we sort them?",
          "3 · Check the plan")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


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
        title = QLabel(f"📁 {APP_NAME}")
        title.setObjectName("heading")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Version {__version__}"))
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


class _FolderCard(QFrame):
    """Big friendly folder picker card."""

    def __init__(self, question: str, hint: str, button_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        q = QLabel(question)
        q.setObjectName("cardQuestion")
        layout.addWidget(q)
        h = QLabel(hint)
        h.setObjectName("muted")
        h.setWordWrap(True)
        layout.addWidget(h)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("No folder chosen yet")
        self.button = QPushButton(button_text)
        self.button.setObjectName("primaryButton")
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        layout.addLayout(row)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1120, 760)
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
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        heading = QLabel(f"📁 {APP_NAME}")
        heading.setObjectName("heading")
        root.addWidget(heading)
        self.step_label = QLabel()
        self.step_label.setObjectName("stepLabel")
        root.addWidget(self.step_label)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        root.addWidget(self.stack, 1)

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
        root.addLayout(nav)

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------- step 1

    def _build_step1(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        intro = QLabel("Pick two folders and we'll take care of the rest.")
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.source_card = _FolderCard(
            "Where are your messy photos?",
            "The folder (and its subfolders) with the photos and videos "
            "you want to sort.",
            "Choose folder…")
        self.source_card.button.clicked.connect(
            lambda: self._pick_folder(self.source_card.edit, suggest_dest=True))
        layout.addWidget(self.source_card)

        self.dest_card = _FolderCard(
            "Where should organized copies go?",
            "Your files are copied here, neatly sorted. Nothing is "
            "overwritten or deleted from the original folder.",
            "Choose folder…")
        self.dest_card.button.clicked.connect(
            lambda: self._pick_folder(self.dest_card.edit))
        layout.addWidget(self.dest_card)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------- step 2

    def _build_step2(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        intro = QLabel("Choose how your photos and videos should be grouped "
                       "into folders:")
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.strategy_radios: list[QRadioButton] = []
        for i, strategy in enumerate(STRATEGIES):
            radio = QRadioButton()
            radio.setProperty("strategyKey", strategy.key)
            card = QFrame()
            card.setObjectName("card")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 8, 14, 8)
            row.addWidget(radio)
            text_col = QVBoxLayout()
            name = QLabel(strategy.name)
            name.setObjectName("cardQuestion")
            example = QLabel(f"e.g.  {strategy.example}")
            example.setObjectName("exampleLabel")
            text_col.addWidget(name)
            text_col.addWidget(example)
            row.addLayout(text_col, 1)
            layout.addWidget(card)
            if strategy.key == DEFAULT_STRATEGY_KEY:
                radio.setChecked(True)
            self.strategy_radios.append(radio)
            # clicking anywhere on the card selects the radio
            card.mouseReleaseEvent = lambda _e, r=radio: r.setChecked(True)

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
        layout.setSpacing(8)

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
            ["File", "Date taken", "Found via", "New location", "Size"])
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
        layout.addWidget(self.table, 1)

        # Progress area: a large dedicated panel, always visible while working
        self.progress_panel = QFrame()
        self.progress_panel.setObjectName("card")
        pp = QVBoxLayout(self.progress_panel)
        pp.setContentsMargins(16, 14, 16, 14)
        pp.setSpacing(8)
        self.progress_title = QLabel("Working…")
        self.progress_title.setObjectName("cardQuestion")
        pp.addWidget(self.progress_title)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setMinimumHeight(28)
        pp.addWidget(self.progress)
        self.progress_status = QLabel("")
        pp.addWidget(self.progress_status)
        self.progress_detail = QLabel("")
        self.progress_detail.setObjectName("muted")
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
        self.step_label.setText(f"Step {index + 1} of 3 — {_STEPS[index].split('· ')[1]}")
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
            self.dest_card.edit.setText(f"{path.rstrip('/\\\\')}_Organized")

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
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
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
        self.plan_summary.setText(
            f"{len(plan)} files · {folders} folders · "
            f"{dupes} exact duplicates will be skipped · "
            f"{undated} without a date")
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
