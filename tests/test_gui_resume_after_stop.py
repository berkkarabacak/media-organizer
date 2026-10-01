"""Cancel and per-file errors must leave Resume on the plan already loaded.

A partial organize used to close the Done dialog, clear the plan, and
turn Organize off even though the crash journal was still open. The
next copy then required a full Scan. The dialog also called those
failures "couldn't be read (skipped)" and offered Undo without saying
Resume was available.
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
from media_organizer.core.journal import atomic_copy, find_unfinished_journal
from media_organizer.core.organizer import OrganizeOptions, build_plan
from media_organizer.core.plan import list_run_logs
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


def _library(tmp_path, n=2):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    for i in range(n):
        make_jpeg_with_exif(
            src / f"IMG_{i}.jpg", datetime(2024, 7, 15, 10, i, 0))
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
    plan = build_plan(options)
    assert len(plan) == n
    return src, dst, options, plan


def _arm(window, src, dst, plan):
    window.source_card.edit.setText(str(src))
    window.dest_card.edit.setText(str(dst))
    window.plan = list(plan)
    window.excluded.clear()
    window.dry_run_cb.setChecked(False)
    window.copy_radio.setChecked(True)
    window.move_radio.setChecked(False)
    window._last_active_bytes = sum(item.size for item in plan)
    window.organize_btn.setEnabled(True)


def _dialogs(monkeypatch):
    """Record the finish dialog and answer Resume / Move with Yes."""
    shown = []
    events = []
    chosen = {"name": None, "once": False}

    def question(*args, **kwargs):
        text = args[2]
        events.append(("question", text))
        if "Resume" in text or "Move mode removes" in text:
            return QMessageBox.Yes
        if "Undo the newest" in text:
            return QMessageBox.No
        return QMessageBox.No

    def information(*args, **kwargs):
        events.append(("information", args[2]))
        return QMessageBox.Ok

    def critical(*args, **kwargs):
        events.append(("critical", args[2]))
        return QMessageBox.Ok

    def fake_exec(self):
        labels = [button.text().replace("&", "") for button in self.buttons()]
        shown.append({
            "title": self.windowTitle(),
            "text": self.text(),
            "buttons": labels,
        })
        picked = None
        name = chosen["name"] if chosen["once"] else None
        chosen["once"] = False
        if name:
            picked = next(
                (button for button in self.buttons()
                 if button.text().replace("&", "") == name),
                None)
        chosen["button"] = picked
        return 0

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(QMessageBox, "critical", critical)
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(
        QMessageBox, "clickedButton", lambda self: chosen.get("button"))
    return shown, events, chosen


def _forbid_rescan(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("partial finish must not require a full scan")

    monkeypatch.setattr("media_organizer.core.organizer.build_plan", boom)
    monkeypatch.setattr("media_organizer.gui.workers.build_plan", boom)


def _wait(window, qapp):
    worker = window.org_worker
    if worker is not None:
        assert worker.wait(30000)
    qapp.processEvents()


def _jpg_names(dst: Path) -> list[str]:
    return sorted(path.name for path in dst.rglob("*.jpg"))


class TestPartialFinishLeavesResume:
    def test_cancel_keeps_the_plan_and_resume_finishes_without_a_rescan(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, options, plan = _library(tmp_path)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        log, summary = execute_plan(plan, options, cancel=cancel_after_one)
        assert summary["cancelled"] is True
        assert summary["errors"] == 0
        assert summary["copied"] == 1
        assert find_unfinished_journal(dst) is not None
        done = [op for op in log.operations if op.status == "done"]
        assert len(done) == 1
        kept = Path(done[0].destination)
        assert kept.is_file()
        payload = kept.read_bytes()
        natural = {item.destination.name: item.destination for item in plan}
        assert kept.name in natural
        missing_name = next(name for name in natural if name != kept.name)
        assert not natural[missing_name].exists()

        _arm(window, src, dst, plan)
        shown, events, _chosen = _dialogs(monkeypatch)
        window._on_run_finished(log, summary)

        assert window.plan == list(plan)
        assert window._active_plan() == list(plan)
        assert window.organize_btn.isEnabled()
        assert find_unfinished_journal(dst) is not None
        assert "Organize again to Resume" in window.status_label.text()
        assert "skipped" not in window.status_label.text().lower()
        dialog = shown[-1]
        assert dialog["title"].endswith("Organize stopped")
        assert "Done!" not in dialog["text"]
        assert "couldn't be read" not in dialog["text"]
        assert "skipped" not in dialog["text"].lower()
        assert "cancelled part-way" in dialog["text"]
        assert "Organize again to Resume" in dialog["text"]
        assert "Undo reverses" in dialog["text"]
        assert "Resume" in dialog["buttons"]
        assert "Undo" in dialog["buttons"]
        assert "Open folder" in dialog["buttons"]

        _forbid_rescan(monkeypatch)
        window.start_organize()
        _wait(window, qapp)

        assert any("Resume" in text for _kind, text in events
                   if _kind == "question")
        assert natural[missing_name].is_file()
        assert kept.read_bytes() == payload
        # Natural names only. A collision copy would be IMG_0_1.jpg.
        assert _jpg_names(dst) == ["IMG_0.jpg", "IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None
        assert window.plan == []
        assert window.organize_btn.isEnabled() is False
        assert shown[-1]["title"].endswith("Done")
        assert "Done!" in shown[-1]["text"]
        assert "Resume" not in shown[-1]["buttons"]
        assert window.scan_worker is None

    def test_resume_button_continues_without_a_rescan(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, options, plan = _library(tmp_path)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        log, summary = execute_plan(plan, options, cancel=cancel_after_one)
        _arm(window, src, dst, plan)
        shown, events, chosen = _dialogs(monkeypatch)
        chosen["name"] = "Resume"
        chosen["once"] = True
        _forbid_rescan(monkeypatch)
        window._on_run_finished(log, summary)
        _wait(window, qapp)

        assert "Resume" in shown[0]["buttons"]
        assert any("Resume" in text for kind, text in events if kind == "question")
        assert _jpg_names(dst) == ["IMG_0.jpg", "IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None
        assert window.plan == []
        assert window.organize_btn.isEnabled() is False
        assert window.scan_worker is None

    def test_copy_oserror_keeps_the_plan_and_resume_retries_that_file(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, options, plan = _library(tmp_path)
        real_copy = atomic_copy
        calls = {"n": 0}

        def fail_second(source, final):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError("injected copy failure")
            return real_copy(source, final)

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_second)
        log, summary = execute_plan(plan, options)
        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", real_copy)

        assert summary["errors"] == 1
        assert summary["cancelled"] is False
        assert summary["copied"] == 1
        assert find_unfinished_journal(dst) is not None
        landed = [item for item in plan if item.destination.is_file()]
        missing = [item for item in plan if not item.destination.exists()]
        assert len(landed) == 1 and len(missing) == 1
        payload = landed[0].destination.read_bytes()

        _arm(window, src, dst, plan)
        shown, events, _chosen = _dialogs(monkeypatch)
        window._on_run_finished(log, summary)

        assert window.plan == list(plan)
        assert window.organize_btn.isEnabled()
        dialog = shown[-1]
        assert dialog["title"].endswith("Organize stopped")
        assert "couldn't be read" not in dialog["text"]
        assert "skipped" not in dialog["text"].lower()
        assert "1 file could not be organized" in dialog["text"]
        assert "Organize again to Resume" in dialog["text"]
        assert "permanently" not in dialog["text"].lower()
        assert "Resume" in dialog["buttons"]
        assert "Finished:" not in window.status_label.text()

        _forbid_rescan(monkeypatch)
        window.start_organize()
        _wait(window, qapp)

        assert any("Resume" in text for kind, text in events if kind == "question")
        assert missing[0].destination.is_file()
        assert missing[0].destination.read_bytes() == missing[0].source.read_bytes()
        assert landed[0].destination.read_bytes() == payload
        assert _jpg_names(dst) == ["IMG_0.jpg", "IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None
        assert window.plan == []
        assert window.organize_btn.isEnabled() is False

    def test_clean_finish_disarms_so_organize_cannot_run_again(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, options, plan = _library(tmp_path, n=1)
        log, summary = execute_plan(plan, options)
        assert summary["errors"] == 0
        assert summary["cancelled"] is False
        assert find_unfinished_journal(dst) is None
        final = plan[0].destination
        payload = final.read_bytes()
        logs_before = [item.run_id for item in list_run_logs(dst)]

        _arm(window, src, dst, plan)
        shown, events, _chosen = _dialogs(monkeypatch)
        window._on_run_finished(log, summary)

        assert window.plan == []
        assert window._active_plan() == []
        assert window.organize_btn.isEnabled() is False
        assert find_unfinished_journal(dst) is None
        dialog = shown[-1]
        assert dialog["title"].endswith("Done")
        assert "Done!" in dialog["text"]
        assert "Resume" not in dialog["text"]
        assert "Resume" not in dialog["buttons"]
        assert "Undo" in dialog["buttons"]
        assert "couldn't be read" not in dialog["text"]
        assert window.status_label.text().startswith("Finished:")

        window.start_organize()
        _wait(window, qapp)

        assert events == []
        assert window.org_worker is None
        assert window.plan == []
        assert window.organize_btn.isEnabled() is False
        assert final.read_bytes() == payload
        assert not list(dst.rglob("*_1*"))
        assert [item.run_id for item in list_run_logs(dst)] == logs_before

    def test_dry_run_still_disarms_and_does_not_offer_undo(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, options, plan = _library(tmp_path)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        execute_plan(plan, options, cancel=cancel_after_one)
        assert find_unfinished_journal(dst) is not None
        dry = OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=True, dry_run=True)
        log, summary = execute_plan(plan, dry)
        assert summary["dry_run"] is True
        assert find_unfinished_journal(dst) is not None

        _arm(window, src, dst, plan)
        window.dry_run_cb.setChecked(True)
        shown, _events, _chosen = _dialogs(monkeypatch)
        window._on_run_finished(log, summary)

        assert window.plan == []
        assert window.organize_btn.isEnabled() is False
        # The older crash journal is not this dry run's, and the dialog
        # must not complete it or throw it away.
        assert find_unfinished_journal(dst) is not None
        dialog = shown[-1]
        assert dialog["title"].endswith("Dry run complete")
        assert "Undo" not in dialog["buttons"]
        assert "Resume" not in dialog["buttons"]
        assert "Resume" not in dialog["text"]
        assert "undo" not in dialog["text"].lower()
