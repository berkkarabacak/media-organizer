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

from media_organizer.core.display import finished_run_lines
from media_organizer.core.executor import execute_plan
from media_organizer.core.metadata import CaptureDate, Confidence, DateSource
from media_organizer.core.organizer import OrganizeOptions, PlannedFile, build_plan
from media_organizer.core.plan import (
    UNDO_STACK_LIMIT, Operation, RunLog, load_log, log_path_for,
)
from tests.helpers import make_jpeg_with_exif


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


def _button_labels(box) -> list[str]:
    return [button.text().replace("&", "") for button in box.buttons()]


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
            captured["buttons"] = _button_labels(self)
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
        assert "Open folder" in captured["buttons"]
        assert "Undo" not in captured["buttons"]


class TestUndoLimitationIsVisible:
    def test_step3_says_how_many_recent_runs_can_be_undone(self, qapp, window):
        window._goto_step(2)
        qapp.processEvents()
        text = _labels(window)
        lowered = text.lower()
        assert str(UNDO_STACK_LIMIT) in text
        assert "newest first" in lowered
        assert "keeps those undo logs" in lowered
        assert "only remaining copy" in lowered
        assert "older run" in lowered
        assert "replaces the undo log" not in lowered
        assert "replaces this undo log" not in lowered

    def test_finished_dialog_names_the_same_undo_stack(
            self, qapp, window, monkeypatch):
        captured = {}

        def fake_exec(self):
            captured["text"] = self.text()
            captured["buttons"] = _button_labels(self)
            return None

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
        log = RunLog(started_at="2024-07-15T10:00:00", dest_dir="/dst",
                     operations=[
                         Operation("copy", "/src/a.jpg", "/dst/2024/a.jpg",
                                   status="done"),
                     ])
        summary = {
            "copied": 1,
            "moved": 0,
            "skipped_duplicates": 0,
            "undated": 0,
            "uncertain": 0,
            "errors": 0,
            "cancelled": False,
            "dry_run": False,
            "action": "copy",
        }
        window._last_active_bytes = 1024
        window._on_run_finished(log, summary)
        text = captured["text"]
        _status, plain = finished_run_lines(
            summary, folders=1, total_bytes=1024)
        assert f"last {UNDO_STACK_LIMIT} organize runs" in text
        assert f"last {UNDO_STACK_LIMIT} organize runs" in plain
        assert "newest first" in text.lower()
        assert "replaces" not in text.lower()
        assert "Undo" in captured["buttons"]
        assert "Open folder" in captured["buttons"]


def _dry_log_over_existing_copy(tmp_path):
    """Dry-run log whose destination is a file that is already there.

    The plan is built before that file exists, then the bytes are placed
    at the planned path. ``execute_plan`` sees a match and records the
    existing path as done without writing it. That is the log Undo would
    treat as a copy to delete.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    photo = make_jpeg_with_exif(
        src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 30, 0))
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
    plan = build_plan(options)
    dest = plan[0].destination
    dest.parent.mkdir(parents=True)
    payload = photo.read_bytes()
    dest.write_bytes(payload)
    dry = OrganizeOptions(
        source_dir=src, dest_dir=dst, copy_mode=True, dry_run=True)
    log, summary = execute_plan(plan, dry)
    return src, dst, photo, dest, payload, log, summary


class TestDryRunDoneDoesNotUndo:
    def test_dialog_and_forced_undo_leave_the_matching_copy(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, photo, dest, payload, log, summary = (
            _dry_log_over_existing_copy(tmp_path))
        assert summary["dry_run"] is True
        assert log.operations[0].size == -1
        assert log.operations[0].sha256 == ""
        assert log.operations[0].destination == str(dest)
        window.dest_card.edit.setText(str(dst))
        window._last_active_bytes = len(payload)
        captured = {}

        def fake_exec(self):
            captured["buttons"] = _button_labels(self)
            captured["clicked"] = next(
                (button for button in self.buttons()
                 if button.text().replace("&", "") == "Undo"),
                None)
            return 0

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(
            QMessageBox, "clickedButton",
            lambda self: captured.get("clicked"))
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
        monkeypatch.setattr(
            QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)

        window._on_run_finished(log, summary)
        # The button is absent, so the dialog cannot choose Undo. Call the
        # handler anyway: a dry-run log must not delete the file or be saved.
        window._undo_log(log, dst)

        assert "Open folder" in captured["buttons"]
        assert "Undo" not in captured["buttons"]
        assert dest.is_file()
        assert dest.read_bytes() == payload
        assert photo.read_bytes() == payload
        assert not any(src.rglob("*restored*"))
        assert load_log(dst) is None
        assert not log_path_for(dst).exists()
        assert log.undone is False
