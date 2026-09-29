"""Regression: strategy cards must render at full height (v1.5.3 bug).

The accessible strategy cards are QPushButtons; QPushButton.sizeHint ignores
the widget's own layout and returned a ~16 px text-based hint, collapsing
every card to a thin strip in the shipped v1.5.3 build. The fix overrides
sizeHint to consult the layout — this test guards the rendered result.
"""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp):
    from media_organizer.gui.main_window import MainWindow
    QSettings("MediaOrganizer", "MediaOrganizer").clear()
    w = MainWindow()
    w.show()
    yield w
    w.close()
    w.deleteLater()


def test_strategy_cards_render_at_layout_height(window, qapp):
    window.resize(1400, 900)
    window._goto_step(1)
    qapp.processEvents()
    assert window.strategy_radios, "no strategy cards built"
    for card in window.strategy_radios:
        key = card.property("strategyKey")
        layout_hint = card.layout().sizeHint().height()
        assert card.height() >= layout_hint - 4, (
            f"card {key} collapsed: height={card.height()} "
            f"layout_hint={layout_hint}")
        assert card.height() > 40, (
            f"card {key} looks like the v1.5.3 thin-strip bug "
            f"(height={card.height()})")
