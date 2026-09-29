"""GUI regressions: explain a bad destination, keep the plan responsive,
and say move / _uncertain when that is what the run will do.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTableWidgetItem

from media_organizer.core.metadata import CaptureDate, Confidence, DateSource
from media_organizer.core.organizer import PlannedFile
from media_organizer.core.plan import Operation, RunLog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, tmp_path):
    from media_organizer.gui.main_window import MainWindow

    QSettings("MediaOrganizer", "MediaOrganizer").clear()
    w = MainWindow()
    w.show()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _labels(window) -> str:
    return "\n".join(lbl.text() for lbl in window.findChildren(QLabel))


def _wait_scan(window, qapp):
    worker = window.scan_worker
    if worker is not None and worker.isRunning():
        worker.wait(30000)
    qapp.processEvents()


class TestDestinationExplanation:
    def test_same_folder_is_explained_before_a_run(self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "photos"
        src.mkdir()
        (src / "a.jpg").write_bytes(b"jpeg")
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: warnings.append(args) or QMessageBox.Ok)
        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(src))
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)
        shown = _labels(window)
        if warnings:
            shown += "\n" + "\n".join(str(a) for a in warnings)
        assert "same folder" in shown.lower()
        assert window.table.rowCount() == 0
        assert (src / "a.jpg").exists()

    def test_parent_destination_is_explained(self, qapp, window, tmp_path, monkeypatch):
        parent = tmp_path / "photos"
        src = parent / "messy"
        src.mkdir(parents=True)
        (src / "a.jpg").write_bytes(b"jpeg")
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: warnings.append(args) or QMessageBox.Ok)
        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(parent))
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)
        shown = _labels(window)
        if warnings:
            shown += "\n" + "\n".join(str(a) for a in warnings)
        assert "parent" in shown.lower()
        assert window.table.rowCount() == 0


class TestPlanTableStaysLight:
    def test_thousands_of_rows_are_not_built_as_widgets(self, qapp, window, tmp_path, monkeypatch):
        created = {"n": 0}
        real_init = QTableWidgetItem.__init__

        def wrapped(self, *args, **kwargs):
            created["n"] += 1
            return real_init(self, *args, **kwargs)

        monkeypatch.setattr(QTableWidgetItem, "__init__", wrapped)
        dst = tmp_path / "dst"
        plan = []
        for i in range(2500):
            src = tmp_path / "src" / f"IMG_{i:04d}.jpg"
            capture = CaptureDate(datetime(2024, 7, 15, 10, 0, 0),
                                  DateSource.EXIF, Confidence.HIGH, "EXIF")
            plan.append(PlannedFile(
                src, dst / "2024" / "07 July" / src.name,
                1024 * (i + 1), capture, "image"))
        window.dest_card.edit.setText(str(dst))
        window._on_plan_ready(plan)
        qapp.processEvents()
        assert window.table.rowCount() == 2500
        # Filling a QTableWidget builds 5 items per file (12,500 here) on the
        # UI thread. A responsive plan only asks the model for visible cells.
        assert created["n"] < 200
        assert window.table.item(0, 0).text().startswith("IMG_")
        window._on_header_clicked(4)  # size
        qapp.processEvents()
        assert window.table.item(0, 4).text() != window.table.item(2499, 4).text()
        assert created["n"] < 200


class TestMoveAndUncertainWording:
    def test_plan_summary_says_move(self, window, tmp_path):
        dst = tmp_path / "dst"
        window.move_radio.setChecked(True)
        window.dest_card.edit.setText(str(dst))
        src = tmp_path / "src" / "IMG_00.jpg"
        capture = CaptureDate(datetime(2024, 7, 15), DateSource.EXIF,
                              Confidence.HIGH, "EXIF")
        plan = [PlannedFile(src, dst / "2024" / "07 July" / src.name,
                            1024, capture, "image")]
        window._on_plan_ready(plan)
        text = window.plan_summary.text()
        assert "to move" in text
        assert "to copy" not in text

    def test_dry_run_dialog_says_move_and_uncertain(self, qapp, window, monkeypatch):
        captured = {}

        def fake_exec(self):
            captured["text"] = self.text()
            captured["status"] = window.status_label.text()
            return None

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
        log = RunLog(started_at="2024-07-15T10:00:00", dest_dir="/dst",
                     operations=[
                         Operation("move", "/src/a.jpg", "/dst/_uncertain/a.jpg",
                                   status="done"),
                     ])
        summary = {
            "copied": 0,
            "moved": 1,
            "skipped_duplicates": 0,
            "undated": 0,
            "uncertain": 1,
            "uncertain_aside": True,
            "errors": 0,
            "cancelled": False,
            "dry_run": True,
            "action": "move",
        }
        window._last_active_bytes = 1024
        window._on_run_finished(log, summary)
        text = captured["text"].lower()
        status = captured["status"].lower()
        assert "would move" in text
        assert "would copy" not in text
        assert "would move" in status
        assert "_uncertain" in captured["text"]
        assert "_undated" not in captured["text"]


class TestUndoLimitationIsVisible:
    def test_step3_says_only_the_latest_run_can_be_undone(self, qapp, window):
        window._goto_step(2)
        qapp.processEvents()
        text = _labels(window).lower()
        assert "most recent" in text or "latest" in text
        assert "cannot be undone" in text or "replaces" in text
        assert "only" in text and "copy" in text
