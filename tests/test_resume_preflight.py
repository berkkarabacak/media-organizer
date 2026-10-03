"""Organize preflight uses the plan Resume or Discard will actually run.

Free space used to be summed before the crash-journal question, so files
Resume will skip could block the run. A destination that already holds
the source is journaled only and must not inflate ``needed`` either.
Move mode omits a same-volume rename. Copy mode, and a move onto another
device, still count those bytes.
"""

from __future__ import annotations

import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from media_organizer.core.display import format_bytes
from media_organizer.core.executor import bytes_still_needed, free_space_status
from media_organizer.core.journal import (
    JournalWriter, atomic_copy, completed_sources, exclude_completed_sources,
    find_unfinished_journal, path_identity,
)
from media_organizer.core.metadata import CaptureDate, Confidence, DateSource
from media_organizer.core.organizer import OrganizeOptions, PlannedFile, build_plan
from media_organizer.core.plan import (
    list_run_logs, load_log, load_undoable_log, undo_log,
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
    worker = w.org_worker
    if worker is not None and worker.isRunning():
        worker.wait(30000)
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _capture() -> CaptureDate:
    return CaptureDate(datetime(2024, 7, 15, 10, 0, 0),
                       DateSource.EXIF, Confidence.HIGH, "EXIF")


def _arm(window, src, dst, plan):
    window.source_card.edit.setText(str(src))
    window.dest_card.edit.setText(str(dst))
    window.plan = list(plan)
    window.excluded.clear()
    window.dry_run_cb.setChecked(False)
    window.copy_radio.setChecked(True)
    window.move_radio.setChecked(False)


def _install_dialogs(monkeypatch, *, resume, tight=QMessageBox.No,
                     move=QMessageBox.Yes):
    events = []

    def question(*args, **kwargs):
        text = args[2]
        events.append(("question", text))
        if "Resume" in text:
            return resume
        if "Move mode removes" in text:
            return move
        return tight

    def critical(*args, **kwargs):
        events.append(("critical", args[2]))
        return QMessageBox.Ok

    def information(*args, **kwargs):
        events.append(("information", args[2]))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "critical", critical)
    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    return events


def _install_space(monkeypatch, free):
    seen = []
    real = free_space_status

    def usage(_path):
        return type("Usage", (), {"total": 10**18, "used": 0, "free": free})()

    def spy(dest, needed):
        seen.append(needed)
        return real(dest, needed)

    monkeypatch.setattr(
        "media_organizer.core.executor.shutil.disk_usage", usage)
    monkeypatch.setattr(
        "media_organizer.gui.main_window.free_space_status", spy)
    return seen


def _texts(events, kind):
    return [text for name, text in events if name == kind]


def _done_ids(log):
    return {path_identity(op.source)
            for op in log.operations if op.status == "done"}


def _wait(window, qapp):
    worker = window.org_worker
    if worker is not None:
        assert worker.wait(30000)
    qapp.processEvents()


class TestResumePreflight:
    def test_resume_fits_when_only_the_full_plan_does_not(
            self, qapp, window, tmp_path, monkeypatch):
        """Completed rows and an already-published copy must not block."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        when = datetime(2024, 7, 15, 10, 0, 0)
        done = make_jpeg_with_exif(src / "done.jpg", when)
        published = make_jpeg_with_exif(src / "published.jpg", when)
        fresh = make_jpeg_with_exif(src / "fresh.jpg", when)
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst))
        by_source = {item.source: item for item in plan}
        by_source[done].size = 10**15
        by_source[published].size = 10**15
        by_source[published].destination.parent.mkdir(parents=True)
        atomic_copy(published, by_source[published].destination)
        with JournalWriter(dst) as writer:
            writer.record(
                "copy", str(done), str(by_source[done].destination))
        remaining = exclude_completed_sources(plan, completed_sources(
            find_unfinished_journal(dst)))
        # The published source is not a done line, so it stays in the plan.
        assert by_source[published].source in {item.source for item in remaining}
        needed = bytes_still_needed(remaining)
        assert needed == by_source[fresh].size
        assert needed < 1_000_000
        assert not free_space_status(dst, bytes_still_needed(plan))["ok"]

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.start_organize()

        assert _texts(events, "question")
        assert "Resume" in _texts(events, "question")[0]
        assert events[0][0] == "question"
        assert seen == [needed]
        assert _texts(events, "critical") == []
        assert "Not enough free space" not in " ".join(
            text for _name, text in events)
        assert "909.5 TB" not in " ".join(text for _name, text in events)
        _wait(window, qapp)
        assert by_source[fresh].destination.is_file()
        assert by_source[fresh].destination.read_bytes() == fresh.read_bytes()
        assert by_source[published].destination.read_bytes() == published.read_bytes()
        assert not list(dst.rglob("*_1*"))

    def test_resume_blocks_when_remaining_work_does_not_fit(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        done = src / "done.jpg"
        fresh = src / "fresh.jpg"
        capture = _capture()
        plan = [
            PlannedFile(done, dst / "done.jpg", 111, capture, "image"),
            PlannedFile(fresh, dst / "fresh.jpg", 10**14, capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(dst / "done.jpg"))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.start_organize()

        remaining = exclude_completed_sources(
            plan, [str(done)])
        assert bytes_still_needed(remaining) == 10**14
        assert seen == [10**14]
        assert events[0][0] == "question"
        assert "Resume" in events[0][1]
        critical = _texts(events, "critical")
        assert len(critical) == 1
        assert "Not enough free space" in critical[0]
        assert format_bytes(10**14) in critical[0]
        assert format_bytes(1_000_000) in critical[0]
        assert format_bytes(10**15) not in critical[0]
        assert window.org_worker is None
        assert find_unfinished_journal(dst) is not None

    def test_resume_nearly_full_warning_uses_remaining_bytes(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        done = src / "done.jpg"
        fresh = src / "fresh.jpg"
        plan = [
            PlannedFile(done, dst / "done.jpg", 10**15, capture, "image"),
            PlannedFile(fresh, dst / "fresh.jpg", 960, capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(dst / "done.jpg"))

        events = _install_dialogs(
            monkeypatch, resume=QMessageBox.Yes, tight=QMessageBox.No)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.start_organize()

        assert seen == [960]
        questions = _texts(events, "question")
        assert "Resume" in questions[0]
        assert any("nearly fill" in text and "960 B needed" in text
                   for text in questions)
        assert _texts(events, "critical") == []
        blob = " ".join(text for _name, text in events)
        assert "909.5 TB" not in blob
        assert window.org_worker is None
        assert find_unfinished_journal(dst) is not None

    def test_resume_of_a_finished_plan_asks_before_preflight(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        done = src / "done.jpg"
        final = dst / "done.jpg"
        payload = b"already-copied"
        done.write_bytes(payload)
        final.write_bytes(payload)
        plan = [PlannedFile(done, final, 10**15, capture, "image")]
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(final))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.start_organize()

        assert seen == []
        assert _texts(events, "critical") == []
        assert events[0][0] == "question"
        assert "already organized" in _texts(events, "information")[0]
        assert find_unfinished_journal(dst) is None
        assert window.org_worker is None
        log = load_log(dst)
        assert log is not None
        done_ops = [op for op in log.operations if op.status == "done"]
        assert [path_identity(op.source) for op in done_ops] == [
            path_identity(done)]
        assert done_ops[0].action == "copy"
        result = undo_log(log, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert done.read_bytes() == payload
        assert not final.exists()
        assert find_unfinished_journal(dst) is None

    def test_discard_blocks_on_the_plan_that_would_run(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        done = src / "done.jpg"
        fresh = src / "fresh.jpg"
        plan = [
            PlannedFile(done, dst / "done.jpg", 10**15, capture, "image"),
            PlannedFile(fresh, dst / "fresh.jpg", 512, capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(dst / "done.jpg"))
        assert not (dst / "done.jpg").exists()

        events = _install_dialogs(monkeypatch, resume=QMessageBox.No)
        seen = _install_space(monkeypatch, free=1_000_000)
        from media_organizer.gui import main_window as mw
        discarded = []
        monkeypatch.setattr(
            mw, "discard_journal",
            lambda dest: discarded.append(dest))
        _arm(window, src, dst, plan)
        window.start_organize()

        assert seen == [bytes_still_needed(plan)]
        assert seen == [10**15 + 512]
        assert events[0][0] == "question"
        critical = _texts(events, "critical")
        assert len(critical) == 1
        assert "Not enough free space" in critical[0]
        assert format_bytes(10**15 + 512) in critical[0]
        assert format_bytes(512) not in critical[0].split("Need ", 1)[-1].split(",", 1)[0]
        assert discarded == []
        assert find_unfinished_journal(dst) is not None
        assert window.org_worker is None

    def test_discard_ignores_identical_bytes_and_then_drops_the_journal(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        when = datetime(2024, 7, 15, 10, 0, 0)
        done = make_jpeg_with_exif(src / "done.jpg", when)
        fresh = make_jpeg_with_exif(src / "fresh.jpg", when)
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst))
        by_source = {item.source: item for item in plan}
        by_source[done].destination.parent.mkdir(parents=True)
        atomic_copy(done, by_source[done].destination)
        by_source[done].size = 10**15
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(by_source[done].destination))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.No)
        seen = _install_space(monkeypatch, free=1_000_000)
        from media_organizer.gui import main_window as mw
        discarded = []
        real_discard = mw.discard_journal

        def spy(dest):
            discarded.append(dest)
            assert seen == [by_source[fresh].size]
            return real_discard(dest)

        monkeypatch.setattr(mw, "discard_journal", spy)
        _arm(window, src, dst, plan)
        window.start_organize()

        assert seen == [bytes_still_needed(plan)]
        assert seen == [by_source[fresh].size]
        assert _texts(events, "critical") == []
        assert discarded
        _wait(window, qapp)
        assert by_source[fresh].destination.is_file()
        assert by_source[fresh].destination.read_bytes() == fresh.read_bytes()
        assert not list(dst.rglob("*_1*"))

    def test_dry_run_skips_free_space_and_the_journal_question(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        done = src / "done.jpg"
        plan = [PlannedFile(done, dst / "out.jpg", 10**15, capture, "image")]
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(dst / "out.jpg"))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.dry_run_cb.setChecked(True)
        window.start_organize()

        assert seen == []
        assert events == []
        assert find_unfinished_journal(dst) is not None
        _wait(window, qapp)
        assert window.org_worker is not None
        assert not window.org_worker.isRunning()
        assert not (dst / "out.jpg").exists()

    def test_move_same_volume_does_not_block_on_rename_bytes(
            self, qapp, window, tmp_path, monkeypatch):
        """Huge same-volume Move rows must not fail preflight."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg-bytes")
        capture = _capture()
        plan = [
            PlannedFile(photo, dst / "2024" / "a.jpg", 10**15, capture, "image"),
        ]
        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.start_organize()

        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 0
        assert seen == [0]
        assert _texts(events, "critical") == []
        blob = " ".join(text for _name, text in events)
        assert "Move mode removes" in blob
        assert "Not enough free space" not in blob
        assert "nearly fill" not in blob
        _wait(window, qapp)
        moved = dst / "2024" / "a.jpg"
        assert moved.is_file()
        assert moved.read_bytes() == b"jpeg-bytes"
        assert not photo.exists()

    def test_move_other_volume_still_blocks_and_keeps_the_journal(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        done = src / "done.jpg"
        fresh = src / "fresh.jpg"
        done.write_bytes(b"done")
        fresh.write_bytes(b"fresh")
        plan = [
            PlannedFile(done, dst / "done.jpg", 111, capture, "image"),
            PlannedFile(fresh, dst / "fresh.jpg", 10**15, capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("move", str(done), str(dst / "done.jpg"))

        def volume(path):
            if Path(path) == fresh:
                return 11
            return 3

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", volume)
        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.start_organize()

        assert events[0][1].startswith("Move mode removes")
        assert events[1][0] == "question"
        assert "Resume" in events[1][1]
        assert seen == [10**15]
        critical = _texts(events, "critical")
        assert len(critical) == 1
        assert "Not enough free space" in critical[0]
        assert format_bytes(10**15) in critical[0]
        assert window.org_worker is None
        assert find_unfinished_journal(dst) is not None
        assert done.is_file() and fresh.is_file()

    def test_resume_move_same_volume_measures_only_the_remainder(
            self, qapp, window, tmp_path, monkeypatch):
        """A cross-volume completed row must not block a same-volume Resume."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        done = src / "done.jpg"
        fresh = src / "fresh.jpg"
        done.write_bytes(b"done")
        fresh.write_bytes(b"fresh")
        plan = [
            PlannedFile(done, dst / "done.jpg", 10**15, capture, "image"),
            PlannedFile(fresh, dst / "2024" / "fresh.jpg", 10**15,
                        capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("move", str(done), str(dst / "done.jpg"))

        def volume(path):
            if Path(path) == done:
                return 11
            return 3

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", volume)
        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.start_organize()

        assert events[0][1].startswith("Move mode removes")
        assert "Resume" in events[1][1]
        assert seen == [0]
        assert _texts(events, "critical") == []
        assert "nearly fill" not in " ".join(text for _name, text in events)
        _wait(window, qapp)
        moved = dst / "2024" / "fresh.jpg"
        assert moved.is_file()
        assert moved.read_bytes() == b"fresh"
        assert not fresh.exists()
        # The journaled source was not part of this resume.
        assert done.is_file()

    def test_discard_move_same_volume_drops_the_journal(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg-bytes")
        capture = _capture()
        plan = [
            PlannedFile(photo, dst / "a.jpg", 10**15, capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("move", str(photo), str(dst / "a.jpg"))
        assert not (dst / "a.jpg").exists()

        events = _install_dialogs(monkeypatch, resume=QMessageBox.No)
        seen = _install_space(monkeypatch, free=1000)
        from media_organizer.gui import main_window as mw
        discarded = []
        real_discard = mw.discard_journal

        def spy(dest):
            discarded.append(dest)
            assert seen == [0]
            return real_discard(dest)

        monkeypatch.setattr(mw, "discard_journal", spy)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.start_organize()

        assert events[0][1].startswith("Move mode removes")
        assert "Resume" in events[1][1]
        assert seen == [0]
        assert _texts(events, "critical") == []
        assert discarded
        _wait(window, qapp)
        assert (dst / "a.jpg").is_file()
        assert (dst / "a.jpg").read_bytes() == b"jpeg-bytes"
        assert not photo.exists()
        assert find_unfinished_journal(dst) is None

    def test_resume_move_unlinks_journaled_source_when_nothing_else_remains(
            self, qapp, window, tmp_path, monkeypatch):
        """Last-file crash: Resume's plan is empty, but the source is still there."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        when = datetime(2024, 7, 15, 10, 0, 0)
        photo = make_jpeg_with_exif(src / "done.jpg", when)
        payload = photo.read_bytes()
        plan = build_plan(OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=False))
        assert len(plan) == 1
        final = plan[0].destination
        final.parent.mkdir(parents=True)
        atomic_copy(photo, final)
        with JournalWriter(dst) as writer:
            writer.record(
                "move", str(photo), str(final),
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.start_organize()

        assert seen == []
        assert events[0][1].startswith("Move mode removes")
        assert "Resume" in events[1][1]
        assert "already organized" in _texts(events, "information")[0]
        assert not photo.exists()
        assert final.read_bytes() == payload
        assert not list(dst.rglob("*_1*"))
        assert find_unfinished_journal(dst) is None
        assert window.org_worker is None
        log = load_log(dst)
        assert log is not None
        done_ops = [op for op in log.operations if op.status == "done"]
        assert [path_identity(op.source) for op in done_ops] == [
            path_identity(photo)]
        assert done_ops[0].action == "move"
        result = undo_log(log, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert photo.read_bytes() == payload
        assert not final.exists()
        assert find_unfinished_journal(dst) is None

    def test_dry_run_move_skips_free_space_and_the_move_question(
            self, qapp, window, tmp_path, monkeypatch):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        capture = _capture()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg-bytes")
        plan = [
            PlannedFile(photo, dst / "a.jpg", 10**15, capture, "image"),
        ]
        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.dry_run_cb.setChecked(True)
        window.start_organize()

        assert seen == []
        assert events == []
        _wait(window, qapp)
        assert photo.is_file()
        assert not (dst / "a.jpg").exists()

    @pytest.mark.parametrize("copy_mode", [True, False])
    def test_empty_resume_disarms_plan_and_a_second_organize_keeps_the_library(
            self, qapp, window, tmp_path, monkeypatch, copy_mode):
        """Empty Resume must leave Organize off, in Copy mode and Move mode.

        Every source is already journaled. Resume seeds the undo log and,
        for a move, unlinks the source. The plan is then cleared and
        Organize is disabled, the same post-state as ``_on_run_finished``.
        A second Organize used to re-journal destinations that still
        match. Undo of that new log deleted the organized copies.
        """
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        when = datetime(2024, 7, 15, 10, 0, 0)
        photo = make_jpeg_with_exif(src / "done.jpg", when)
        payload = photo.read_bytes()
        plan = build_plan(OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=copy_mode))
        assert len(plan) == 1
        final = plan[0].destination
        final.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(photo, final)
        action = "copy" if copy_mode else "move"
        with JournalWriter(dst) as writer:
            writer.record(
                action, str(photo), str(final),
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(copy_mode)
        window.move_radio.setChecked(not copy_mode)
        window._refresh_table()
        window.organize_btn.setEnabled(True)

        window.start_organize()

        assert seen == []
        if copy_mode:
            assert "Resume" in _texts(events, "question")[0]
        else:
            assert events[0][1].startswith("Move mode removes")
            assert "Resume" in events[1][1]
        note = _texts(events, "information")[0]
        assert "already organized" in note
        assert "Undo" in note
        assert "completed run" in note
        assert find_unfinished_journal(dst) is None
        assert window.org_worker is None
        assert window.plan == []
        assert window._active_plan() == []
        assert window.organize_btn.isEnabled() is False
        assert "already organized" in window.status_label.text()
        if copy_mode:
            assert photo.read_bytes() == payload
        else:
            assert not photo.exists()
        assert final.read_bytes() == payload
        seeded = load_log(dst)
        assert seeded is not None and seeded.undone is False
        done_ops = [op for op in seeded.operations if op.status == "done"]
        assert [path_identity(op.source) for op in done_ops] == [
            path_identity(photo)]
        assert done_ops[0].action == action
        seeded_id = seeded.run_id

        events.clear()
        window.start_organize()
        _wait(window, qapp)

        assert window.org_worker is None
        assert window.plan == []
        assert window.organize_btn.isEnabled() is False
        assert _texts(events, "information") == []
        assert _texts(events, "question") == []
        assert final.is_file() and final.read_bytes() == payload
        if copy_mode:
            assert photo.is_file() and photo.read_bytes() == payload
        else:
            assert not photo.exists()
        logs = list_run_logs(dst)
        assert [item.run_id for item in logs] == [seeded_id]
        assert all(not item.undone for item in logs)
        assert not list(dst.rglob("*_1*"))

    def test_discard_keeps_undo_of_matching_copies_out_of_the_new_log(
            self, qapp, window, tmp_path, monkeypatch):
        """Discard must not make Undo delete copies the interrupted run finished.

        A matching destination used to be journaled again as a done op in
        the restart's log. Undo of that log deleted it. A journaled file
        whose destination is missing is still written, and Undo of the
        restart removes that new copy only. The seeded log from the open
        journal still reverses the copy that was already there.
        """
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        kept = make_jpeg_with_exif(
            src / "kept.jpg", datetime(2024, 7, 15, 10, 0, 0))
        gone = make_jpeg_with_exif(
            src / "gone.jpg", datetime(2024, 7, 15, 10, 1, 0))
        fresh = make_jpeg_with_exif(
            src / "new.jpg", datetime(2024, 7, 15, 10, 2, 0))
        kept_payload = kept.read_bytes()
        gone_payload = gone.read_bytes()
        fresh_payload = fresh.read_bytes()
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst))
        by_source = {item.source: item for item in plan}
        kept_final = by_source[kept].destination
        gone_final = by_source[gone].destination
        fresh_final = by_source[fresh].destination
        kept_final.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(kept, kept_final)
        with JournalWriter(dst) as writer:
            writer.record(
                "copy", str(kept), str(kept_final),
                sha256=hashlib.sha256(kept_payload).hexdigest(),
                size=len(kept_payload))
            writer.record("copy", str(gone), str(gone_final))
        assert not gone_final.exists()

        events = _install_dialogs(monkeypatch, resume=QMessageBox.No)
        _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.organize_btn.setEnabled(True)
        window.start_organize()
        _wait(window, qapp)

        assert _texts(events, "critical") == []
        assert _texts(events, "information") == []
        assert "Resume" in _texts(events, "question")[0]
        assert kept_final.read_bytes() == kept_payload
        assert kept.read_bytes() == kept_payload
        assert gone_final.read_bytes() == gone_payload
        assert fresh_final.read_bytes() == fresh_payload
        assert not list(dst.rglob("*_1*"))
        assert find_unfinished_journal(dst) is None
        logs = list_run_logs(dst)
        assert len(logs) == 2
        newest, seeded = logs
        assert _done_ids(newest) == {
            path_identity(gone), path_identity(fresh)}
        assert _done_ids(seeded) == {
            path_identity(kept), path_identity(gone)}
        assert {op.action for op in newest.operations} == {"copy"}
        assert {op.action for op in seeded.operations} == {"copy"}
        assert all(op.size >= 0 and op.sha256
                   for op in seeded.operations
                   if path_identity(op.source) == path_identity(kept))

        result = undo_log(newest, dst)
        assert result["undone"] == 2
        assert result["failed"] == 0
        assert kept_final.read_bytes() == kept_payload
        assert kept.read_bytes() == kept_payload
        assert not gone_final.exists()
        assert not fresh_final.exists()
        assert gone.read_bytes() == gone_payload
        assert fresh.read_bytes() == fresh_payload

        older = load_undoable_log(dst)
        assert older is not None and older.run_id == seeded.run_id
        result = undo_log(older, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert not kept_final.exists()
        assert kept.read_bytes() == kept_payload
        assert gone.is_file() and fresh.is_file()

    def test_discard_move_keeps_undo_of_matching_destination(
            self, qapp, window, tmp_path, monkeypatch):
        """A matching Move stays in the seeded log, not the restart's log.

        The source is still on disk, as in a cross-volume move journaled
        before the unlink. Discard finishes that unlink while saving Undo,
        then moves only the file the journal had not finished.
        """
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        kept = make_jpeg_with_exif(
            src / "kept.jpg", datetime(2024, 7, 15, 10, 0, 0))
        fresh = make_jpeg_with_exif(
            src / "new.jpg", datetime(2024, 7, 15, 10, 2, 0))
        kept_payload = kept.read_bytes()
        fresh_payload = fresh.read_bytes()
        plan = build_plan(OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=False))
        by_source = {item.source: item for item in plan}
        kept_final = by_source[kept].destination
        fresh_final = by_source[fresh].destination
        kept_final.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(kept, kept_final)
        with JournalWriter(dst) as writer:
            writer.record(
                "move", str(kept), str(kept_final),
                sha256=hashlib.sha256(kept_payload).hexdigest(),
                size=len(kept_payload))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.No)
        _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(False)
        window.move_radio.setChecked(True)
        window.organize_btn.setEnabled(True)
        window.start_organize()
        _wait(window, qapp)

        assert events[0][1].startswith("Move mode removes")
        assert "Resume" in events[1][1]
        assert _texts(events, "critical") == []
        assert _texts(events, "information") == []
        assert not kept.exists()
        assert kept_final.read_bytes() == kept_payload
        assert not fresh.exists()
        assert fresh_final.read_bytes() == fresh_payload
        assert not list(dst.rglob("*_1*"))
        assert find_unfinished_journal(dst) is None
        logs = list_run_logs(dst)
        assert len(logs) == 2
        newest, seeded = logs
        assert _done_ids(newest) == {path_identity(fresh)}
        assert _done_ids(seeded) == {path_identity(kept)}
        assert newest.operations[0].action == "move"
        assert seeded.operations[0].action == "move"
        assert seeded.operations[0].size >= 0
        assert seeded.operations[0].sha256

        result = undo_log(newest, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert fresh.read_bytes() == fresh_payload
        assert not fresh_final.exists()
        assert not kept.exists()
        assert kept_final.read_bytes() == kept_payload

        older = load_undoable_log(dst)
        assert older is not None and older.run_id == seeded.run_id
        result = undo_log(older, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert kept.read_bytes() == kept_payload
        assert not kept_final.exists()

    @pytest.mark.parametrize("copy_mode", [True, False])
    def test_discard_of_settled_files_disarms_and_undo_restores(
            self, qapp, window, tmp_path, monkeypatch, copy_mode):
        """Every journaled file already matches. Discard must not re-log it.

        Organize is turned off, the same way an empty Resume disarms.
        A second Organize must not start a worker or write another log.
        File → Undo still reverses the interrupted run.
        """
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        photo = make_jpeg_with_exif(
            src / "kept.jpg", datetime(2024, 7, 15, 10, 0, 0))
        payload = photo.read_bytes()
        plan = build_plan(OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=copy_mode))
        assert len(plan) == 1
        final = plan[0].destination
        final.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(photo, final)
        action = "copy" if copy_mode else "move"
        with JournalWriter(dst) as writer:
            writer.record(
                action, str(photo), str(final),
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.No)
        _install_space(monkeypatch, free=1_000_000)
        _arm(window, src, dst, plan)
        window.copy_radio.setChecked(copy_mode)
        window.move_radio.setChecked(not copy_mode)
        window._refresh_table()
        window.organize_btn.setEnabled(True)
        window.start_organize()

        assert window.org_worker is None
        assert window.plan == []
        assert window._active_plan() == []
        assert window.organize_btn.isEnabled() is False
        assert "already organized" in window.status_label.text()
        assert "nothing new" in window.status_label.text()
        note = _texts(events, "information")[0]
        assert "already in the destination" in note
        assert "Undo" in note
        assert "interrupted run" in note
        assert find_unfinished_journal(dst) is None
        assert final.read_bytes() == payload
        if copy_mode:
            assert photo.read_bytes() == payload
        else:
            assert not photo.exists()
        seeded = load_log(dst)
        assert seeded is not None and seeded.undone is False
        done_ops = [op for op in seeded.operations if op.status == "done"]
        assert [path_identity(op.source) for op in done_ops] == [
            path_identity(photo)]
        assert done_ops[0].action == action
        assert done_ops[0].size >= 0 and done_ops[0].sha256
        seeded_id = seeded.run_id

        events.clear()
        window.start_organize()

        assert window.org_worker is None
        assert window.plan == []
        assert window.organize_btn.isEnabled() is False
        assert _texts(events, "information") == []
        assert _texts(events, "question") == []
        assert final.read_bytes() == payload
        logs = list_run_logs(dst)
        assert [item.run_id for item in logs] == [seeded_id]
        assert not list(dst.rglob("*_1*"))

        result = undo_log(seeded, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert photo.read_bytes() == payload
        assert not final.exists()
        assert find_unfinished_journal(dst) is None
