"""Organize preflight uses the plan Resume or Discard will actually run.

Free space used to be summed before the crash-journal question, so files
Resume will skip could block the run. A destination that already holds
the source is journaled only and must not inflate ``needed`` either.
Move mode omits a same-volume rename. Copy mode, and a move onto another
device, still count those bytes.
"""

from __future__ import annotations

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
    find_unfinished_journal,
)
from media_organizer.core.metadata import CaptureDate, Confidence, DateSource
from media_organizer.core.organizer import OrganizeOptions, PlannedFile, build_plan
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
        plan = [PlannedFile(done, dst / "done.jpg", 10**15, capture, "image")]
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done), str(dst / "done.jpg"))

        events = _install_dialogs(monkeypatch, resume=QMessageBox.Yes)
        seen = _install_space(monkeypatch, free=1000)
        discarded = []
        from media_organizer.gui import main_window as mw
        real_discard = mw.discard_journal

        def spy(dest):
            discarded.append(dest)
            return real_discard(dest)

        monkeypatch.setattr(mw, "discard_journal", spy)
        _arm(window, src, dst, plan)
        window.start_organize()

        assert seen == []
        assert _texts(events, "critical") == []
        assert events[0][0] == "question"
        assert "already organized" in _texts(events, "information")[0]
        assert discarded
        assert find_unfinished_journal(dst) is None
        assert window.org_worker is None

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
