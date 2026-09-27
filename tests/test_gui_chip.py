"""GUI tests (offscreen): real-widget click tests via QTest."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


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
