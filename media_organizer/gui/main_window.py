"""Main window for Media Organizer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.metadata import Confidence, DateSource
from ..core.organizer import OrganizeOptions, PATTERNS
from ..core.plan import load_log, undo_log
from .workers import OrganizeWorker, ScanWorker

_CONFIDENCE_COLORS = {
    Confidence.HIGH: "#3fb950",
    Confidence.MEDIUM: "#d29922",
    Confidence.LOW: "#8b949e",
}

_SOURCE_LABELS = {
    DateSource.EXIF: "EXIF",
    DateSource.PNG_TEXT: "PNG text",
    DateSource.VIDEO: "Video meta",
    DateSource.FILENAME: "Filename",
    DateSource.MTIME: "File mtime",
    DateSource.NONE: "—",
}

COL_NAME, COL_DATE, COL_SOURCE, COL_DEST, COL_SIZE = range(5)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        title = QLabel(f"📁 {APP_NAME}")
        title.setObjectName("heading")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Version {__version__}"))
        layout.addWidget(QLabel(
            "Organise photos and videos into date-based folders using their "
            "real capture dates from EXIF, video metadata, and filenames."
        ))
        license_label = QLabel("License: <commercial license placeholder>")
        license_label.setObjectName("muted")
        layout.addWidget(license_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1080, 720)
        self.settings = QSettings("MediaOrganizer", "MediaOrganizer")
        self.plan: list = []
        self.scan_worker: ScanWorker | None = None
        self.org_worker: OrganizeWorker | None = None

        self._build_menu()
        self._build_ui()
        self._restore_settings()

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
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        heading = QLabel(f"📁 {APP_NAME}")
        heading.setObjectName("heading")
        root.addWidget(heading)
        sub = QLabel("Sort photos & videos by their real capture date.")
        sub.setObjectName("muted")
        root.addWidget(sub)

        # Folders
        folders = QGroupBox("Folders")
        form = QFormLayout(folders)
        self.source_edit = QLineEdit()
        src_btn = QPushButton("Browse…")
        src_btn.clicked.connect(lambda: self._pick_folder(self.source_edit))
        row = QHBoxLayout()
        row.addWidget(self.source_edit, 1)
        row.addWidget(src_btn)
        form.addRow("Source folder:", row)

        self.dest_edit = QLineEdit()
        dst_btn = QPushButton("Browse…")
        dst_btn.clicked.connect(lambda: self._pick_folder(self.dest_edit))
        row = QHBoxLayout()
        row.addWidget(self.dest_edit, 1)
        row.addWidget(dst_btn)
        form.addRow("Destination folder:", row)
        root.addWidget(folders)

        # Options
        options = QGroupBox("Options")
        opt = QHBoxLayout(options)
        self.recursive_cb = QCheckBox("Include subfolders")
        self.recursive_cb.setChecked(True)
        self.copy_radio = QRadioButton("Copy files (safe)")
        self.move_radio = QRadioButton("Move files")
        self.copy_radio.setChecked(True)
        self.dupes_cb = QCheckBox("Skip duplicates")
        self.images_cb = QCheckBox("Images")
        self.images_cb.setChecked(True)
        self.videos_cb = QCheckBox("Videos")
        self.videos_cb.setChecked(True)
        self.pattern_combo = QComboBox()
        self.pattern_combo.addItem("Year / Month  (2024/01 (January))", PATTERNS["year_month"])
        self.pattern_combo.addItem("Year / Quarter  (2024/Q1)", PATTERNS["year_quarter"])
        self.pattern_combo.addItem("Year / Month / Quarter  (2024/01/Q1)", PATTERNS["year_month_quarter"])
        for w in (self.recursive_cb, self.copy_radio, self.move_radio, self.dupes_cb,
                  self.images_cb, self.videos_cb):
            opt.addWidget(w)
        opt.addStretch(1)
        opt.addWidget(QLabel("Folder pattern:"))
        opt.addWidget(self.pattern_combo)
        root.addWidget(options)

        # Actions
        actions = QHBoxLayout()
        self.scan_btn = QPushButton("🔍  Scan && Preview")
        self.scan_btn.setObjectName("primaryButton")
        self.scan_btn.clicked.connect(self.start_scan)
        self.organize_btn = QPushButton("▶  Organize")
        self.organize_btn.setEnabled(False)
        self.organize_btn.clicked.connect(self.start_organize)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("dangerButton")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_work)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        actions.addWidget(self.scan_btn)
        actions.addWidget(self.organize_btn)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.progress, 1)
        root.addLayout(actions)

        # Filter + preview table
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter preview… (type to match filename or destination)")
        self.filter_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.filter_edit)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["File", "Detected date", "Source / confidence", "Destination", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)

        self.status_label = QLabel("Choose a source folder and press Scan & Preview.")
        self.statusBar().addWidget(self.status_label, 1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------- settings

    def _restore_settings(self):
        self.source_edit.setText(self.settings.value("source_dir", ""))
        self.dest_edit.setText(self.settings.value("dest_dir", ""))
        self.recursive_cb.setChecked(self.settings.value("recursive", True, type=bool))
        self.move_radio.setChecked(self.settings.value("move_mode", False, type=bool))
        self.copy_radio.setChecked(not self.move_radio.isChecked())
        self.dupes_cb.setChecked(self.settings.value("skip_duplicates", False, type=bool))
        self.images_cb.setChecked(self.settings.value("images", True, type=bool))
        self.videos_cb.setChecked(self.settings.value("videos", True, type=bool))
        self.pattern_combo.setCurrentIndex(self.settings.value("pattern_index", 0, type=int))

    def _save_settings(self):
        self.settings.setValue("source_dir", self.source_edit.text())
        self.settings.setValue("dest_dir", self.dest_edit.text())
        self.settings.setValue("recursive", self.recursive_cb.isChecked())
        self.settings.setValue("move_mode", self.move_radio.isChecked())
        self.settings.setValue("skip_duplicates", self.dupes_cb.isChecked())
        self.settings.setValue("images", self.images_cb.isChecked())
        self.settings.setValue("videos", self.videos_cb.isChecked())
        self.settings.setValue("pattern_index", self.pattern_combo.currentIndex())

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    # -------------------------------------------------------------- actions

    def _pick_folder(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if path:
            edit.setText(path)

    def _options(self) -> OrganizeOptions | None:
        src = Path(self.source_edit.text().strip())
        dst = Path(self.dest_edit.text().strip())
        if not src.is_dir():
            QMessageBox.warning(self, APP_NAME, "Please choose a valid source folder.")
            return None
        if not dst or str(dst) == ".":
            QMessageBox.warning(self, APP_NAME, "Please choose a destination folder.")
            return None
        if self.move_radio.isChecked():
            answer = QMessageBox.question(
                self, APP_NAME,
                "Move mode removes files from the source folder. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return None
        return OrganizeOptions(
            source_dir=src,
            dest_dir=dst,
            recursive=self.recursive_cb.isChecked(),
            pattern=self.pattern_combo.currentData(),
            copy_mode=self.copy_radio.isChecked(),
            skip_duplicates=self.dupes_cb.isChecked(),
            include_images=self.images_cb.isChecked(),
            include_videos=self.videos_cb.isChecked(),
        )

    def start_scan(self):
        options = self._options()
        if options is None:
            return
        self._set_busy(True, "Scanning…")
        self.progress.setRange(0, 0)
        self.scan_worker = ScanWorker(options, self)
        self.scan_worker.progress.connect(
            lambda i, name: self.status_label.setText(f"Scanning: {name}"))
        self.scan_worker.finished_plan.connect(self._on_plan_ready)
        self.scan_worker.failed.connect(self._on_worker_failed)
        self.scan_worker.start()

    def _on_plan_ready(self, plan: list):
        self.plan = plan
        self._set_busy(False)
        self.progress.setVisible(False)
        self._populate_table(plan)
        dupes = sum(1 for p in plan if p.is_duplicate)
        undated = sum(1 for p in plan if not p.capture.found and not p.is_duplicate)
        self.status_label.setText(
            f"Preview ready: {len(plan)} files · {dupes} duplicates · {undated} undated. "
            "Review the table, then press Organize.")
        self.organize_btn.setEnabled(bool(plan))

    def _populate_table(self, plan: list):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(plan))
        for row, item in enumerate(plan):
            name_item = QTableWidgetItem(item.source.name)
            name_item.setToolTip(str(item.source))
            self.table.setItem(row, COL_NAME, name_item)

            if item.is_duplicate:
                date_text, src_text = "duplicate", "Skipped (duplicate)"
                conf = Confidence.LOW
            elif item.capture.found:
                date_text = item.capture.date.strftime("%Y-%m-%d %H:%M:%S")
                src_text = _SOURCE_LABELS[item.capture.source]
                conf = item.capture.confidence
            else:
                date_text, src_text, conf = "—", "No date", Confidence.LOW
            self.table.setItem(row, COL_DATE, QTableWidgetItem(date_text))

            src_item = QTableWidgetItem(f"● {src_text}")
            src_item.setForeground(QColor(_CONFIDENCE_COLORS[conf]))
            src_item.setToolTip(item.capture.detail)
            self.table.setItem(row, COL_SOURCE, src_item)

            dest_text = str(item.destination) if item.destination else "—"
            dest_item = QTableWidgetItem(dest_text)
            dest_item.setToolTip(dest_text)
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

    def start_organize(self):
        if not self.plan:
            return
        options = self._options()
        if options is None:
            return
        self._set_busy(True, "Organizing…")
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self.plan))
        self.org_worker = OrganizeWorker(self.plan, options, self)
        self.org_worker.progress.connect(self._on_org_progress)
        self.org_worker.finished_run.connect(self._on_run_finished)
        self.org_worker.failed.connect(self._on_worker_failed)
        self.org_worker.start()

    def _on_org_progress(self, i: int, total: int, name: str):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(i)
        if name:
            self.status_label.setText(f"Processing {i + 1}/{total}: {name}")

    def _on_run_finished(self, log, summary: dict):
        self._set_busy(False)
        self.progress.setVisible(False)
        self.plan = []
        self.organize_btn.setEnabled(False)
        mode = "copied" if summary["copied"] or not summary["moved"] else "moved"
        done_n = summary["copied"] + summary["moved"]
        text = (
            f"Done — {done_n} files {mode}.\n\n"
            f"• Skipped duplicates: {summary['skipped_duplicates']}\n"
            f"• Undated files (placed in 'Undated'): {summary['undated']}\n"
            f"• Errors: {summary['errors']}\n"
        )
        if summary["cancelled"]:
            text += "\nRun was cancelled part-way."
        self.status_label.setText(f"Finished: {done_n} {mode}, "
                                  f"{summary['skipped_duplicates']} duplicates skipped, "
                                  f"{summary['undated']} undated, {summary['errors']} errors.")
        QMessageBox.information(self, f"{APP_NAME} — Summary", text)

    def cancel_work(self):
        for worker in (self.scan_worker, self.org_worker):
            if worker and worker.isRunning():
                worker.cancel()
        self.status_label.setText("Cancelling…")

    def undo_last_run(self):
        dst = Path(self.dest_edit.text().strip())
        if not dst.is_dir():
            QMessageBox.warning(self, APP_NAME,
                                "Choose the destination folder used by the run first.")
            return
        log = load_log(dst)
        if log is None:
            QMessageBox.information(self, APP_NAME, "No operation log found in that folder.")
            return
        if log.undone:
            QMessageBox.information(self, APP_NAME, "That run has already been undone.")
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
        self.scan_btn.setEnabled(not busy)
        self.organize_btn.setEnabled(not busy and bool(self.plan))
        self.cancel_btn.setEnabled(busy)
        self.progress.setVisible(busy)
        if message:
            self.status_label.setText(message)

    def _on_worker_failed(self, message: str):
        self._set_busy(False)
        self.progress.setVisible(False)
        QMessageBox.critical(self, APP_NAME, f"Operation failed:\n{message}")
