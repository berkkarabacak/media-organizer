"""Dark theme stylesheet for Media Organizer."""

DARK_QSS = """
* {
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 14px;
}
QMainWindow, QDialog {
    background: #1b1e24;
}
QWidget {
    background: #1b1e24;
    color: #d7dce3;
}
QGroupBox {
    border: 1px solid #333a45;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 12px;
    font-weight: 600;
    color: #9fb2c8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QPushButton {
    background: #2b313b;
    border: 1px solid #3d4552;
    border-radius: 8px;
    padding: 10px 22px;
    color: #e6ebf2;
}
QPushButton:hover { background: #363e4b; border-color: #4d5869; }
QPushButton:pressed { background: #232830; }
QPushButton:disabled { color: #6b7480; background: #232830; }
QPushButton#primaryButton {
    background: #2f6fed;
    border-color: #2f6fed;
    font-weight: 700;
    font-size: 15px;
}
QPushButton#primaryButton:hover { background: #4480f5; }
QPushButton#primaryButton:pressed { background: #2659c4; }
QPushButton#dangerButton {
    background: #8c2f39;
    border-color: #8c2f39;
}
QPushButton#dangerButton:hover { background: #a53a45; }
QLineEdit, QComboBox {
    background: #12151a;
    border: 1px solid #333a45;
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: #2f6fed;
}
QLineEdit:focus, QComboBox:focus { border-color: #2f6fed; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #232830;
    border: 1px solid #333a45;
    selection-background-color: #2f6fed;
}
QCheckBox, QRadioButton { spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px; height: 16px;
    border: 1px solid #4d5869;
    border-radius: 4px;
    background: #12151a;
}
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: #2f6fed;
    border-color: #2f6fed;
}
QTableWidget, QTableView {
    background: #12151a;
    alternate-background-color: #171b21;
    gridline-color: #262c36;
    border: 1px solid #333a45;
    border-radius: 6px;
}
QHeaderView::section {
    background: #232830;
    color: #9fb2c8;
    border: none;
    border-right: 1px solid #333a45;
    border-bottom: 1px solid #333a45;
    padding: 6px 8px;
    font-weight: 600;
}
QTableWidget::item:selected, QTableView::item:selected {
    background: #2f4f8f;
}
QProgressBar {
    background: #12151a;
    border: 1px solid #333a45;
    border-radius: 6px;
    text-align: center;
    color: #d7dce3;
    height: 18px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2f6fed, stop:1 #57a0ff);
    border-radius: 5px;
}
QMenuBar {
    background: #16191f;
    border-bottom: 1px solid #2a3039;
}
QMenuBar::item:selected { background: #2b313b; border-radius: 4px; }
QMenu {
    background: #232830;
    border: 1px solid #333a45;
}
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background: #2f6fed; }
QStatusBar { background: #16191f; border-top: 1px solid #2a3039; }
QLabel#heading {
    font-size: 22px;
    font-weight: 700;
    color: #f0f4f9;
}
QLabel#stepLabel {
    font-size: 16px;
    font-weight: 600;
    color: #8ab4ff;
}
QLabel#muted { color: #7c8794; }
QFrame#card {
    background: #21262e;
    border: 1px solid #333a45;
    border-radius: 10px;
}
QFrame#card QLabel { background: transparent; }
QLabel#cardQuestion {
    font-size: 15px;
    font-weight: 600;
    color: #eef2f7;
}
QLabel#exampleLabel {
    color: #7fd1a8;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 13px;
}
QToolButton {
    background: transparent;
    border: none;
    color: #8ab4ff;
    font-weight: 600;
    padding: 6px 4px;
}
QToolButton:hover { color: #a9c6ff; }
QScrollBar:vertical {
    background: #12151a; width: 12px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #3d4552; min-height: 24px; border-radius: 6px; margin: 2px;
}
QScrollBar::handle:vertical:hover { background: #4d5869; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #12151a; height: 12px; margin: 0;
}
QScrollBar::handle:horizontal {
    background: #3d4552; min-width: 24px; border-radius: 6px; margin: 2px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QToolTip {
    background: #232830; color: #d7dce3;
    border: 1px solid #3d4552; padding: 4px 8px;
}
"""
