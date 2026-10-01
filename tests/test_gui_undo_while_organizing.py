"""File → Undo must not run while OrganizeWorker is still writing.

A queued or direct undo during that window used to delete copies, move
files back, and discard the unfinished crash journal under the worker.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from media_organizer.core.executor import execute_plan
from media_organizer.core.journal import find_unfinished_journal, journal_path_for
from media_organizer.core.organizer import OrganizeOptions, build_plan
from media_organizer.core.plan import load_log, log_path_for
from media_organizer.gui.workers import OrganizeWorker
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
    worker = w.org_worker
    if worker is not None and worker.isRunning():
        worker.wait(30000)
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _partial_run(tmp_path, *, copy_mode: bool):
    """One file written, then cancel, so undo would reverse it.

    The operation log is saved and the crash journal stays unfinished.
    Undo of that log removes the copy or moves the file back, and drops
    the journal because the restored source is still listed there.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    make_jpeg_with_exif(src / "IMG_0.jpg", datetime(2024, 7, 15, 10, 0, 0))
    make_jpeg_with_exif(src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 1, 0))
    options = OrganizeOptions(
        source_dir=src, dest_dir=dst, copy_mode=copy_mode)
    plan = build_plan(options)
    calls = {"n": 0}

    def cancel_after_one():
        calls["n"] += 1
        return calls["n"] > 1

    log, summary = execute_plan(plan, options, cancel=cancel_after_one)
    assert summary["cancelled"] is True
    done = [op for op in log.operations if op.status == "done"]
    assert len(done) == 1
    journal = find_unfinished_journal(dst)
    assert journal is not None
    assert journal_path_for(dst).is_file()
    assert log_path_for(dst).is_file()
    assert Path(done[0].destination).is_file()
    return src, dst, plan, log, done[0]


def _snapshot(src, dst) -> dict:
    def files(root):
        found = {}
        if not root.exists():
            return found
        for path in root.rglob("*"):
            if path.is_file():
                found[str(path.relative_to(root))] = path.read_bytes()
        return found

    journal = journal_path_for(dst)
    log_file = log_path_for(dst)
    return {
        "src": files(src),
        "dst": files(dst),
        "journal": journal.read_bytes() if journal.is_file() else None,
        "log": log_file.read_bytes() if log_file.is_file() else None,
    }


def _dialogs(monkeypatch):
    events = []

    def question(*args, **kwargs):
        text = args[2]
        events.append(("question", text))
        if "Undo the newest" in text:
            return QMessageBox.Yes
        if "Resume" in text or "Move mode removes" in text:
            return QMessageBox.Yes
        return QMessageBox.No

    def information(*args, **kwargs):
        events.append(("information", args[2]))
        return QMessageBox.Ok

    def critical(*args, **kwargs):
        events.append(("critical", args[2]))
        return QMessageBox.Ok

    def warning(*args, **kwargs):
        events.append(("warning", args[2]))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(QMessageBox, "critical", critical)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    return events


def _plenty_of_space(monkeypatch):
    def usage(_path):
        return type("Usage", (), {"total": 10**18, "used": 0, "free": 10**18})()

    monkeypatch.setattr(
        "media_organizer.core.executor.shutil.disk_usage", usage)


class _HeldOrganizeWorker(OrganizeWorker):
    """Reports isRunning() without copying. wait() is what finish/fail call."""

    def start(self, *args, **kwargs):
        self._held = True

    def isRunning(self):
        return bool(getattr(self, "_held", False))

    def wait(self, msecs=-1):
        self._held = False
        return True


def _hold_organize(monkeypatch, window, src, dst, plan, *, copy_mode: bool):
    monkeypatch.setattr(
        "media_organizer.gui.main_window.OrganizeWorker", _HeldOrganizeWorker)
    _plenty_of_space(monkeypatch)
    window.source_card.edit.setText(str(src))
    window.dest_card.edit.setText(str(dst))
    window.plan = list(plan)
    window.excluded.clear()
    window.dry_run_cb.setChecked(False)
    window.copy_radio.setChecked(copy_mode)
    window.move_radio.setChecked(not copy_mode)
    assert window.undo_action.isEnabled()
    window.start_organize()
    assert window.org_worker is not None
    assert window.org_worker.isRunning()
    assert window.undo_action.isEnabled() is False


def _assert_refused(events, before, src, dst, op, *, copy_mode: bool):
    questions = [text for kind, text in events if kind == "question"]
    notes = [text for kind, text in events if kind == "information"]
    assert not any("Undo the newest" in text for text in questions)
    assert any("still running" in text.lower() for text in notes)
    assert _snapshot(src, dst) == before
    assert find_unfinished_journal(dst) is not None
    saved = load_log(dst)
    assert saved is not None and saved.undone is False
    if copy_mode:
        assert Path(op.source).is_file()
        assert Path(op.destination).is_file()
    else:
        assert not Path(op.source).exists()
        assert Path(op.destination).is_file()


class TestUndoWhileOrganizeRuns:
    def test_file_undo_does_not_delete_a_copy_or_drop_the_journal(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, plan, log, op = _partial_run(tmp_path, copy_mode=True)
        events = _dialogs(monkeypatch)
        _hold_organize(monkeypatch, window, src, dst, plan, copy_mode=True)
        before = _snapshot(src, dst)
        # Drop the Resume question from start_organize. Only Undo counts now.
        events.clear()

        window.undo_action.trigger()
        window.undo_last_run()
        window._undo_log(log, dst)
        qapp.processEvents()

        _assert_refused(events, before, src, dst, op, copy_mode=True)
        assert log.undone is False

    def test_undo_does_not_move_a_file_back_or_drop_the_journal(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, plan, log, op = _partial_run(tmp_path, copy_mode=False)
        assert not Path(op.source).exists()
        assert Path(op.destination).is_file()
        events = _dialogs(monkeypatch)
        _hold_organize(monkeypatch, window, src, dst, plan, copy_mode=False)
        before = _snapshot(src, dst)
        events.clear()

        window.undo_last_run()
        window._undo_log(log, dst)

        _assert_refused(events, before, src, dst, op, copy_mode=False)
        assert not Path(op.source).exists()
        assert Path(op.destination).is_file()

    def test_failure_reenables_undo_and_undo_is_allowed(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, plan, log, op = _partial_run(tmp_path, copy_mode=True)
        events = _dialogs(monkeypatch)
        _hold_organize(monkeypatch, window, src, dst, plan, copy_mode=True)
        before = _snapshot(src, dst)
        events.clear()
        window.undo_last_run()
        assert _snapshot(src, dst) == before

        window._on_worker_failed("disk full")
        assert window.org_worker.isRunning() is False
        assert window.undo_action.isEnabled()
        assert _snapshot(src, dst) == before
        assert any("disk full" in text for kind, text in events if kind == "critical")

        events.clear()
        window.undo_last_run()

        assert any("Undo the newest" in text
                   for kind, text in events if kind == "question")
        assert not any("still running" in text.lower()
                       for kind, text in events if kind == "information")
        assert find_unfinished_journal(dst) is None
        assert not journal_path_for(dst).exists()
        assert not Path(op.destination).exists()
        assert Path(op.source).is_file()
        saved = load_log(dst)
        assert saved is not None and saved.undone is True

    def test_done_dialog_undo_works_after_the_worker_finishes(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        make_jpeg_with_exif(
            src / "IMG_0.jpg", datetime(2024, 7, 15, 10, 0, 0))
        options = OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=True)
        plan = build_plan(options)
        log, summary = execute_plan(plan, options)
        assert summary["cancelled"] is False
        assert summary["dry_run"] is False
        assert find_unfinished_journal(dst) is None
        op = next(item for item in log.operations if item.status == "done")
        assert Path(op.destination).is_file()
        events = _dialogs(monkeypatch)
        _hold_organize(monkeypatch, window, src, dst, plan, copy_mode=True)
        assert window.undo_action.isEnabled() is False
        clicked = {}

        def fake_exec(self):
            clicked["buttons"] = [
                button.text().replace("&", "") for button in self.buttons()]
            clicked["button"] = next(
                (button for button in self.buttons()
                 if button.text().replace("&", "") == "Undo"),
                None)
            return 0

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(
            QMessageBox, "clickedButton", lambda self: clicked.get("button"))

        window._on_run_finished(log, summary)

        assert "Undo" in clicked["buttons"]
        assert "Resume" not in clicked["buttons"]
        assert "Open folder" in clicked["buttons"]
        assert window.undo_action.isEnabled()
        assert window.org_worker.isRunning() is False
        assert not Path(op.destination).exists()
        assert Path(op.source).is_file()
        assert find_unfinished_journal(dst) is None
        assert any("Undo the newest" in text
                   for kind, text in events if kind == "question")

    def test_stopped_dialog_does_not_undo_finished_copies(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, plan, log, op = _partial_run(tmp_path, copy_mode=True)
        payload = Path(op.destination).read_bytes()
        events = _dialogs(monkeypatch)
        _hold_organize(monkeypatch, window, src, dst, plan, copy_mode=True)
        assert window.undo_action.isEnabled() is False
        before = _snapshot(src, dst)
        clicked = {}

        def fake_exec(self):
            clicked["title"] = self.windowTitle()
            clicked["buttons"] = [
                button.text().replace("&", "") for button in self.buttons()]
            # A regression that puts Undo back would press it.
            clicked["button"] = next(
                (button for button in self.buttons()
                 if button.text().replace("&", "") == "Undo"),
                None)
            return 0

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(
            QMessageBox, "clickedButton", lambda self: clicked.get("button"))

        window._on_run_finished(log, {
            "copied": 1, "moved": 0, "skipped_duplicates": 0,
            "undated": 0, "uncertain": 0, "errors": 0,
            "cancelled": True, "dry_run": False, "action": "copy",
        })

        assert clicked["title"].endswith("Organize stopped")
        assert "Resume" in clicked["buttons"]
        assert "Undo" not in clicked["buttons"]
        assert "Open folder" in clicked["buttons"]
        assert window.undo_action.isEnabled()
        assert window.org_worker.isRunning() is False
        assert _snapshot(src, dst) == before
        assert Path(op.destination).is_file()
        assert Path(op.destination).read_bytes() == payload
        assert Path(op.source).is_file()
        assert find_unfinished_journal(dst) is not None
        assert not any("Undo the newest" in text
                       for kind, text in events if kind == "question")

    def test_a_running_scan_does_not_block_undo(
            self, qapp, window, tmp_path, monkeypatch):
        dst = tmp_path / "dst"
        dst.mkdir()
        window.dest_card.edit.setText(str(dst))
        window.scan_worker = type("Scan", (), {"isRunning": lambda self: True})()
        window._set_busy(True, "Looking at your photos…")
        assert window._busy()
        assert window.undo_action.isEnabled()
        events = _dialogs(monkeypatch)
        window.undo_last_run()
        notes = [text for kind, text in events if kind == "information"]
        assert notes
        assert "still running" not in notes[0].lower()
        assert "no operation log" in notes[0].lower()
