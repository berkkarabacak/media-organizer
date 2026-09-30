"""A cancelled scan must not look like a finished plan.

The user can press Esc after files are listed, while duplicate hashing,
metadata extraction, or plan assembly is still running. Those steps stop
early and return a subset. That subset must not enable Organize.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from media_organizer.core.metadata import CaptureDate, Confidence, DateSource
from media_organizer.core.organizer import OrganizeOptions, PlannedFile
import media_organizer.gui.workers as workers


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


def _capture() -> CaptureDate:
    return CaptureDate(datetime(2024, 7, 15, 10, 0, 0),
                       DateSource.EXIF, Confidence.HIGH, "EXIF")


def _partial_plan(src: Path, dest_root: Path) -> list[PlannedFile]:
    """One file, as if plan assembly had stopped after the first row."""
    return [PlannedFile(
        src, dest_root / "2024" / "07 July" / src.name,
        1234, _capture(), "image")]


def _listed_files(tmp_path: Path, n: int = 4) -> list[Path]:
    return [tmp_path / "src" / f"IMG_{i}.jpg" for i in range(n)]


def _options(tmp_path: Path, *, skip_duplicates: bool) -> OrganizeOptions:
    return OrganizeOptions(
        source_dir=tmp_path / "src",
        dest_dir=tmp_path / "dst",
        skip_duplicates=skip_duplicates,
    )


def _run(worker: workers.ScanWorker):
    plans = []
    cancels = []
    fails = []
    worker.finished_plan.connect(lambda plan: plans.append(list(plan)))
    worker.cancelled.connect(lambda: cancels.append(True))
    worker.failed.connect(fails.append)
    worker.run()
    return plans, cancels, fails


def _assert_discarded(plans, cancels, fails):
    assert plans == []
    assert cancels == [True]
    assert fails == []


class TestScanWorkerDropsPartialPlan:
    def test_cancel_during_listing_is_not_an_empty_plan(
            self, qapp, tmp_path, monkeypatch):
        files = _listed_files(tmp_path, 4)
        options = _options(tmp_path, skip_duplicates=True)
        worker = workers.ScanWorker(options)

        def fake_scan(_options):
            worker.cancel()
            return iter(files)

        def fail(*_args, **_kwargs):
            raise AssertionError("scan continued after cancel")

        monkeypatch.setattr(workers, "scan_media_files", fake_scan)
        monkeypatch.setattr(workers, "find_duplicates", fail)
        monkeypatch.setattr(workers, "analyze_media_batch", fail)
        monkeypatch.setattr(workers, "build_plan", fail)

        plans, cancels, fails = _run(worker)
        _assert_discarded(plans, cancels, fails)

    def test_cancel_during_duplicates_discards_partial_plan(
            self, qapp, tmp_path, monkeypatch):
        files = _listed_files(tmp_path, 4)
        options = _options(tmp_path, skip_duplicates=True)
        worker = workers.ScanWorker(options)
        later = {"analyze": 0, "build": 0}

        def fake_scan(_options):
            return iter(files)

        def fake_dupes(paths, progress=None, cancel=None):
            assert len(list(paths)) == 4
            worker.cancel()
            return {files[1]}

        def fake_analyze(*_args, **_kwargs):
            later["analyze"] += 1
            return {}

        def fake_build(*_args, **_kwargs):
            later["build"] += 1
            return _partial_plan(files[0], options.dest_dir)

        monkeypatch.setattr(workers, "scan_media_files", fake_scan)
        monkeypatch.setattr(workers, "find_duplicates", fake_dupes)
        monkeypatch.setattr(workers, "analyze_media_batch", fake_analyze)
        monkeypatch.setattr(workers, "build_plan", fake_build)

        plans, cancels, fails = _run(worker)
        _assert_discarded(plans, cancels, fails)
        assert later == {"analyze": 0, "build": 0}

    def test_cancel_during_analyze_discards_partial_plan(
            self, qapp, tmp_path, monkeypatch):
        files = _listed_files(tmp_path, 4)
        options = _options(tmp_path, skip_duplicates=True)
        worker = workers.ScanWorker(options)
        built = {"n": 0}

        def fake_scan(_options):
            return iter(files)

        def fake_dupes(paths, progress=None, cancel=None):
            assert len(list(paths)) == 4
            return {files[1]}

        def fake_analyze(paths, need_gps=False, progress=None, cancel=None):
            assert len(list(paths)) == 4
            worker.cancel()
            return {files[0]: (_capture(), None)}

        def fake_build(*_args, **_kwargs):
            built["n"] += 1
            return _partial_plan(files[0], options.dest_dir)

        monkeypatch.setattr(workers, "scan_media_files", fake_scan)
        monkeypatch.setattr(workers, "find_duplicates", fake_dupes)
        monkeypatch.setattr(workers, "analyze_media_batch", fake_analyze)
        monkeypatch.setattr(workers, "build_plan", fake_build)

        plans, cancels, fails = _run(worker)
        _assert_discarded(plans, cancels, fails)
        assert built["n"] == 0

    def test_cancel_during_build_plan_discards_partial_plan(
            self, qapp, tmp_path, monkeypatch):
        files = _listed_files(tmp_path, 4)
        options = _options(tmp_path, skip_duplicates=False)
        worker = workers.ScanWorker(options)
        partial = _partial_plan(files[0], options.dest_dir)

        def fake_scan(_options):
            return iter(files)

        def fake_analyze(paths, need_gps=False, progress=None, cancel=None):
            assert len(list(paths)) == 4
            return {}

        def fake_build(options, duplicates=None, progress=None, cancel=None,
                       files=None, analysis=None):
            assert files is not None and len(files) == 4
            worker.cancel()
            assert cancel is not None and cancel()
            return list(partial)

        monkeypatch.setattr(workers, "scan_media_files", fake_scan)
        monkeypatch.setattr(workers, "analyze_media_batch", fake_analyze)
        monkeypatch.setattr(workers, "build_plan", fake_build)

        plans, cancels, fails = _run(worker)
        _assert_discarded(plans, cancels, fails)
        assert partial not in plans

    def test_finished_scan_still_emits_the_plan(self, qapp, tmp_path):
        from tests.helpers import make_jpeg_with_exif

        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        make_jpeg_with_exif(src / "IMG_0.jpg", datetime(2024, 7, 15, 10, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst, skip_duplicates=True)
        worker = workers.ScanWorker(options)
        plans, cancels, fails = _run(worker)
        assert cancels == []
        assert fails == []
        assert len(plans) == 1
        assert len(plans[0]) == 1
        assert plans[0][0].source.name == "IMG_0.jpg"


def _wait_scan(window, qapp):
    worker = window.scan_worker
    if worker is not None and worker.isRunning():
        worker.wait(30000)
    qapp.processEvents()


def _library(tmp_path: Path, n: int = 4):
    from tests.helpers import make_jpeg_with_exif

    src = tmp_path / "photos"
    dst = tmp_path / "organized"
    src.mkdir()
    dst.mkdir()
    files = [
        make_jpeg_with_exif(src / f"IMG_{i}.jpg", datetime(2024, 7, 15, 10, i, 0))
        for i in range(n)
    ]
    return src, dst, files


def _seed_stale_plan(window, tmp_path: Path, dst: Path):
    stale_src = tmp_path / "old" / "OLD.jpg"
    window.dest_card.edit.setText(str(dst))
    window._on_plan_ready(_partial_plan(stale_src, dst))
    assert window.organize_btn.isEnabled()
    assert window.table.rowCount() == 1
    assert window.plan[0].source.name == "OLD.jpg"


def _assert_scan_cancelled(window):
    status = window.status_label.text().lower()
    summary = window.plan_summary.text().lower()
    assert window.plan == []
    assert window.table.rowCount() == 0
    assert not window.organize_btn.isEnabled()
    assert "scan cancelled" in status
    assert "plan ready" not in status
    assert "no files found" not in summary
    assert "scan cancelled" in summary


class TestMainWindowScanCancel:
    def test_cancel_during_listing_is_not_no_files_found(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, files = _library(tmp_path, 4)
        _seed_stale_plan(window, tmp_path, dst)

        def fake_scan(_options):
            window.scan_worker.cancel()
            return iter(files)

        monkeypatch.setattr(workers, "scan_media_files", fake_scan)
        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(dst))
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)
        _assert_scan_cancelled(window)

    def test_cancel_during_duplicates_keeps_organize_off(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, files = _library(tmp_path, 4)
        _seed_stale_plan(window, tmp_path, dst)
        later = {"analyze": 0, "build": 0}

        def fake_dupes(paths, progress=None, cancel=None):
            assert len(list(paths)) == 4
            window.scan_worker.cancel()
            return {files[1]}

        def fake_analyze(*_args, **_kwargs):
            later["analyze"] += 1
            return {}

        def fake_build(*_args, **_kwargs):
            later["build"] += 1
            return _partial_plan(files[0], dst)

        monkeypatch.setattr(workers, "find_duplicates", fake_dupes)
        monkeypatch.setattr(workers, "analyze_media_batch", fake_analyze)
        monkeypatch.setattr(workers, "build_plan", fake_build)

        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(dst))
        window.dupes_cb.setChecked(True)
        window.copy_radio.setChecked(True)
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)

        _assert_scan_cancelled(window)
        assert later == {"analyze": 0, "build": 0}

    def test_cancel_during_analyze_keeps_organize_off(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, files = _library(tmp_path, 4)
        _seed_stale_plan(window, tmp_path, dst)
        built = {"n": 0}

        def fake_dupes(paths, progress=None, cancel=None):
            assert len(list(paths)) == 4
            return set()

        def fake_analyze(paths, need_gps=False, progress=None, cancel=None):
            assert len(list(paths)) == 4
            window.scan_worker.cancel()
            return {files[0]: (_capture(), None)}

        def fake_build(*_args, **_kwargs):
            built["n"] += 1
            return _partial_plan(files[0], dst)

        monkeypatch.setattr(workers, "find_duplicates", fake_dupes)
        monkeypatch.setattr(workers, "analyze_media_batch", fake_analyze)
        monkeypatch.setattr(workers, "build_plan", fake_build)

        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(dst))
        window.dupes_cb.setChecked(True)
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)

        _assert_scan_cancelled(window)
        assert built["n"] == 0

    def test_cancel_during_build_plan_in_move_mode_keeps_organize_off(
            self, qapp, window, tmp_path, monkeypatch):
        src, dst, files = _library(tmp_path, 4)
        _seed_stale_plan(window, tmp_path, dst)
        partial = _partial_plan(files[0], dst)

        def fake_analyze(paths, need_gps=False, progress=None, cancel=None):
            assert len(list(paths)) == 4
            return {}

        def fake_build(options, duplicates=None, progress=None, cancel=None,
                       files=None, analysis=None):
            assert files is not None and len(files) == 4
            window.scan_worker.cancel()
            return list(partial)

        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
        monkeypatch.setattr(workers, "analyze_media_batch", fake_analyze)
        monkeypatch.setattr(workers, "build_plan", fake_build)

        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(dst))
        window.dupes_cb.setChecked(False)
        window.move_radio.setChecked(True)
        window.copy_radio.setChecked(False)
        window.dry_run_cb.setChecked(False)
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)

        _assert_scan_cancelled(window)
        assert window.move_radio.isChecked()
        assert partial[0].source.name not in {
            p.source.name for p in window.plan
        }

    def test_empty_folder_is_still_an_empty_plan(self, qapp, window, tmp_path):
        src = tmp_path / "empty"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(dst))
        window.dupes_cb.setChecked(False)
        window._goto_step(2)
        window.start_scan()
        _wait_scan(window, qapp)
        status = window.status_label.text().lower()
        summary = window.plan_summary.text().lower()
        assert "plan ready" in status
        assert "scan cancelled" not in status
        assert "no files found" in summary
        assert window.plan == []
        assert not window.organize_btn.isEnabled()
