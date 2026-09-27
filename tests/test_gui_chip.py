"""GUI tests (offscreen): real-widget click tests via QTest."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from media_organizer.core.metadata import CaptureDate, Confidence, DateSource
from media_organizer.core.organizer import PlannedFile


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


def _demo_plan(tmp_path: Path, dst: Path, n: int = 5) -> list:
    plan = []
    for i in range(n):
        src = tmp_path / "src" / f"IMG_{i:02d}.jpg"
        capture = CaptureDate(datetime(2024, 7, 15, 10, i, 0),
                              DateSource.EXIF, Confidence.HIGH, "EXIF")
        dest = dst / "2024" / "07 July" / src.name
        plan.append(PlannedFile(src, dest, 1024 * 1024 * (i + 1), capture,
                                "image"))
    return plan


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


class TestSuggestionChip:
    def _show_chip(self, w, suggested="C:/Photos_Organized"):
        """Simulate the state right after the user picked a source folder."""
        w.source_card.edit.setText("C:/Photos")
        chip = w.dest_card.chip
        chip.setEnabled(True)
        chip.setText(f"Use suggested:  {suggested}")
        chip.setProperty("suggestedPath", suggested)
        chip.setVisible(True)
        return chip

    def test_chip_click_sets_destination(self, qapp, window):
        """Regression: clicking the chip must set the destination field.

        (Was broken: the slot read MainWindow.chip instead of
        dest_card.chip -> AttributeError swallowed by Qt.)"""
        chip = self._show_chip(window)
        QTest.mouseClick(chip, Qt.LeftButton)
        qapp.processEvents()
        assert window.dest_card.edit.text() == "C:/Photos_Organized"

    def test_chip_visibly_confirms(self, qapp, window):
        chip = self._show_chip(window)
        QTest.mouseClick(chip, Qt.LeftButton)
        qapp.processEvents()
        assert "Using suggested" in chip.text()
        assert not chip.isEnabled()

    def test_chip_not_visible_initially(self, window):
        assert not window.dest_card.chip.isVisibleTo(window)


class TestScanProgressContract:
    def test_scan_progress_signal_has_total(self, qapp, window, tmp_path):
        """ScanWorker.progress must deliver (done, total, name)."""
        from media_organizer.core.organizer import OrganizeOptions
        from media_organizer.gui.workers import ScanWorker
        options = OrganizeOptions(source_dir=tmp_path, dest_dir=tmp_path / "o")
        worker = ScanWorker(options, window)
        received = []
        worker.progress.connect(lambda *args: received.append(args))
        worker.progress.emit(1, 2, "x.jpg")
        qapp.processEvents()
        assert received == [(1, 2, "x.jpg")]

    def test_initial_scan_state_is_indeterminate(self, qapp, window):
        window._show_progress_panel("Scanning files…", indeterminate=True)
        assert window.progress.maximum() == 0
        window._hide_progress_panel()


class TestSummaryTotals:
    def test_summary_shows_total_and_copy_sizes(self, window, tmp_path):
        dst = tmp_path / "dst"
        window.dest_card.edit.setText(str(dst))
        window._on_plan_ready(_demo_plan(tmp_path, dst, n=5))
        text = window.plan_summary.text()
        # 5 files, 1+2+3+4+5 MB = 15.0 MB total and to copy
        assert "5 files" in text
        assert "15.0 MB total" in text
        assert "15.0 MB to copy" in text
        assert "1 folder" in text

    def test_summary_copy_size_excludes_duplicates(self, window, tmp_path):
        dst = tmp_path / "dst"
        plan = _demo_plan(tmp_path, dst, n=2)
        plan[1].is_duplicate = True
        plan[1].destination = None
        window.dest_card.edit.setText(str(dst))
        window._on_plan_ready(plan)
        text = window.plan_summary.text()
        assert "3.0 MB total" in text       # 1 + 2 MB
        assert "1.0 MB to copy" in text     # duplicate excluded from copy size
        assert "1 exact duplicate will be skipped" in text


class TestSortableColumns:
    def _click_header(self, window, col):
        header = window.table.horizontalHeader()
        x = header.sectionViewportPosition(col) + 10
        from PySide6.QtCore import QPoint
        # QHeaderView paints on its viewport, like any QAbstractItemView
        QTest.mouseClick(header.viewport(), Qt.LeftButton, pos=QPoint(x, 10))

    def test_size_header_sorts_numerically(self, qapp, window, tmp_path):
        dst = tmp_path / "dst"
        window.dest_card.edit.setText(str(dst))
        # sizes 5,4,3,2,1 MB reversed so scan order differs from size order
        plan = list(reversed(_demo_plan(tmp_path, dst, n=5)))
        window._on_plan_ready(plan)
        first_before = window.table.item(0, 4).text()
        self._click_header(window, 4)  # SIZE asc
        qapp.processEvents()
        sizes_asc = [window.table.item(r, 4).text() for r in range(5)]
        assert sizes_asc[0] == "1.0 MB"
        assert sizes_asc[-1] == "5.0 MB"
        assert sizes_asc[0] != first_before
        self._click_header(window, 4)  # SIZE desc
        qapp.processEvents()
        assert window.table.item(0, 4).text() == "5.0 MB"

    def test_file_header_sorts_case_insensitive(self, qapp, window, tmp_path):
        dst = tmp_path / "dst"
        plan = _demo_plan(tmp_path, dst, n=2)
        plan[0].source = tmp_path / "b.jpg"
        plan[1].source = tmp_path / "A.jpg"
        window.dest_card.edit.setText(str(dst))
        window._on_plan_ready(plan)
        self._click_header(window, 0)
        qapp.processEvents()
        assert window.table.item(0, 0).text() == "A.jpg"

    def test_date_header_sorts_chronologically(self, qapp, window, tmp_path):
        dst = tmp_path / "dst"
        plan = _demo_plan(tmp_path, dst, n=3)
        plan[0].capture = CaptureDate(datetime(2020, 5, 1), DateSource.EXIF)
        window.dest_card.edit.setText(str(dst))
        window._on_plan_ready(plan)
        self._click_header(window, 1)
        qapp.processEvents()
        assert window.table.item(0, 1).text().startswith("2020-05-01")


class TestColumnWidthPersistence:
    def test_widths_survive_restart(self, qapp, tmp_path):
        from media_organizer.gui.main_window import MainWindow
        QSettings("MediaOrganizer", "MediaOrganizer").clear()
        w1 = MainWindow()
        w1.show()
        header = w1.table.horizontalHeader()
        header.resizeSection(0, 333)
        w1._save_settings()
        w1.close()
        w1.deleteLater()
        qapp.processEvents()

        w2 = MainWindow()   # same QSettings -> widths must be restored
        w2.show()
        assert w2.table.horizontalHeader().sectionSize(0) == 333
        w2.close()
        w2.deleteLater()

    def test_sort_choice_survives_restart(self, qapp, tmp_path):
        from media_organizer.gui.main_window import MainWindow
        QSettings("MediaOrganizer", "MediaOrganizer").clear()
        w1 = MainWindow()
        w1._sort_col, w1._sort_desc = 4, True
        w1._save_settings()
        w1.close(); w1.deleteLater()
        qapp.processEvents()
        w2 = MainWindow()
        assert w2._sort_col == 4 and w2._sort_desc is True
        w2.close(); w2.deleteLater()


class TestExcludeFlow:
    def test_excluded_file_not_organized(self, qapp, window, tmp_path):
        from tests.helpers import make_jpeg_with_exif
        QMessageBox.exec = lambda self: None
        QMessageBox.clickedButton = lambda self: None
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        files = [make_jpeg_with_exif(src / f"IMG_{i}.jpg",
                                     datetime(2024, 7, 15, 10, i, 0))
                 for i in range(3)]
        window.source_card.edit.setText(str(src))
        window.dest_card.edit.setText(str(dst))
        window._goto_step(2)
        window.start_scan()
        window.scan_worker.wait(30000)
        qapp.processEvents()
        assert window.table.rowCount() == 3

        # exclude IMG_1 via the same path the context menu uses
        victim = next(p for p in window.plan if p.source.name == "IMG_1.jpg")
        window._set_excluded(victim, True)
        qapp.processEvents()
        assert "1 excluded" in window.plan_summary.text()

        window.start_organize()
        window.org_worker.wait(30000)
        qapp.processEvents()
        copied = {p.name for p in (dst / "2024" / "07 July").glob("*.jpg")}
        assert copied == {"IMG_0.jpg", "IMG_2.jpg"}

    def test_include_restores_file(self, qapp, window, tmp_path):
        dst = tmp_path / "dst"
        plan = _demo_plan(tmp_path, dst, n=3)
        window.dest_card.edit.setText(str(dst))
        window._on_plan_ready(plan)
        window._set_excluded(plan[0], True)
        assert len(window._active_plan()) == 2
        window._set_excluded(plan[0], False)
        assert len(window._active_plan()) == 3
        assert "excluded" not in window.plan_summary.text()
