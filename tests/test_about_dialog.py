"""About dialog shows the shipped MIT license, not a commercial placeholder."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_about_dialog_shows_mit_license(qapp):
    from media_organizer.gui.main_window import AboutDialog

    dialog = AboutDialog()
    text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "License: MIT" in text
    assert "commercial license placeholder" not in text
    dialog.deleteLater()
    qapp.processEvents()
