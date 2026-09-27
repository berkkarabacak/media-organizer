"""Premium dark theme for Media Organizer.

Calm, cinematic near-black surfaces with a restrained amber accent.
All styling lives here; widgets are addressed via object names.
"""

# Palette (kept as constants for reuse in QPainter code)
BG_APP = "#0A0A0B"
BG_PANEL = "#16161A"
BG_PILL = "#101014"
BG_HOVER = "#1C1C21"
BORDER = "#26262C"
BORDER_SOFT = "#1E1E24"
TEXT = "#E8E6E1"
TEXT_DIM = "#9B978F"
TEXT_FAINT = "#6E6B64"
AMBER = "#D99E3F"
AMBER_HOVER = "#E8B45C"
AMBER_PRESS = "#B98530"
AMBER_TINT = "#241B0E"          # selected-card fill
GREEN = "#5FA87C"
RED = "#C0594F"

DARK_QSS = f"""
* {{
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog {{
    background: {BG_APP};
}}
QWidget {{
    background: {BG_APP};
}}
QStackedWidget, QStackedWidget > QWidget {{
    background: {BG_APP};
}}

/* ------------------------------------------------ typographic hierarchy */
QLabel#pageTitle {{
    font-family: "Segoe UI Light", "Segoe UI", sans-serif;
    font-size: 28px;
    font-weight: 300;
    color: {TEXT};
}}
QLabel#pageSubtitle {{
    font-size: 14px;
    color: {TEXT_DIM};
}}
QLabel#sectionLabel {{
    font-size: 11px;
    font-weight: 700;
    color: {TEXT_FAINT};
}}
QLabel#wordmark {{
    font-size: 17px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#versionLabel {{
    font-size: 12px;
    color: {TEXT_FAINT};
}}
QLabel#muted {{ color: {TEXT_DIM}; }}

/* -------------------------------------------------------------- header */
QFrame#headerBar {{
    background: {BG_APP};
    border-bottom: 1px solid {BORDER_SOFT};
}}

/* --------------------------------------------------------------- cards */
QFrame#card {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#card QLabel, QFrame#card QCheckBox, QFrame#card QRadioButton {{
    background: transparent;
}}
QLabel#cardQuestion {{
    font-size: 16px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#cardHint {{ color: {TEXT_DIM}; }}
QLabel#iconBadge {{
    background: rgba(217, 158, 63, 0.10);
    border: 1px solid rgba(217, 158, 63, 0.22);
    border-radius: 12px;
}}

/* --------------------------------------------------- strategy cards */
QFrame#strategyCard {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#strategyCard:hover {{
    background: {BG_HOVER};
    border-color: #34343C;
}}
QFrame#strategyCard[selected="true"] {{
    background: {AMBER_TINT};
    border: 1px solid {AMBER};
}}
QFrame#strategyCard QLabel {{ background: transparent; }}
QLabel#strategyName {{
    font-size: 15px;
    font-weight: 600;
    color: {TEXT};
}}
QFrame#strategyCard[selected="true"] QLabel#strategyName {{
    color: {AMBER_HOVER};
}}
QLabel#examplePill {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    color: {AMBER};
    background: rgba(217, 158, 63, 0.10);
    border: 1px solid rgba(217, 158, 63, 0.22);
    border-radius: 8px;
    padding: 3px 10px;
}}

/* ------------------------------------------------------------- buttons */
QPushButton {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 0 20px;
    min-height: 40px;
    color: {TEXT};
}}
QPushButton:hover {{
    background: rgba(255, 255, 255, 0.05);
    border-color: #3A3A42;
}}
QPushButton:pressed {{ background: rgba(255, 255, 255, 0.03); }}
QPushButton:disabled {{
    color: {TEXT_FAINT};
    border-color: {BORDER_SOFT};
    background: transparent;
}}
QPushButton#primaryButton {{
    background: {AMBER};
    border: 1px solid {AMBER};
    color: #1A1206;
    font-weight: 700;
    font-size: 15px;
}}
QPushButton#primaryButton:hover {{ background: {AMBER_HOVER}; border-color: {AMBER_HOVER}; }}
QPushButton#primaryButton:pressed {{ background: {AMBER_PRESS}; border-color: {AMBER_PRESS}; }}
QPushButton#primaryButton:disabled {{
    background: #3A3325;
    border-color: #3A3325;
    color: #7A7060;
}}
QPushButton#dangerButton {{
    border: 1px solid rgba(192, 89, 79, 0.55);
    color: {RED};
}}
QPushButton#dangerButton:hover {{ background: rgba(192, 89, 79, 0.12); }}
QPushButton#chip {{
    background: rgba(217, 158, 63, 0.10);
    border: 1px solid rgba(217, 158, 63, 0.30);
    border-radius: 12px;
    color: {AMBER_HOVER};
    min-height: 26px;
    padding: 0 12px;
    font-size: 12px;
}}
QPushButton#chip:hover {{ background: rgba(217, 158, 63, 0.18); }}
QPushButton#chip:disabled {{ color: {GREEN}; border-color: rgba(95, 168, 124, 0.35); background: rgba(95, 168, 124, 0.08); }}

/* -------------------------------------------------------------- inputs */
QLineEdit {{
    background: {BG_PILL};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 8px 14px;
    min-height: 24px;
    selection-background-color: {AMBER};
    selection-color: #1A1206;
}}
QLineEdit:focus {{ border-color: {AMBER}; }}
QComboBox {{
    background: {BG_PILL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px 12px;
}}
QComboBox:focus {{ border-color: {AMBER}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {AMBER_TINT};
}}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: 1px solid #4A4A52;
    border-radius: 5px;
    background: {BG_PILL};
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {AMBER};
    border-color: {AMBER};
    image: none;
}}
QToolButton {{
    background: transparent;
    border: none;
    color: {AMBER_HOVER};
    font-weight: 600;
    padding: 8px 4px;
    text-align: left;
}}
QToolButton:hover {{ color: {AMBER}; }}

/* --------------------------------------------------------------- table */
QTableWidget, QTableView {{
    background: {BG_PANEL};
    alternate-background-color: #131318;
    border: 1px solid {BORDER};
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: rgba(217, 158, 63, 0.18);
    selection-color: {TEXT};
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QHeaderView::section {{
    background: {BG_PANEL};
    color: {TEXT_FAINT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 10px 8px;
    font-size: 11px;
    font-weight: 700;
}}

/* ------------------------------------------------------------- progress */
QProgressBar {{
    background: {BG_PILL};
    border: none;
    border-radius: 7px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    color: transparent;          /* percent shown as big number instead */
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {AMBER_PRESS}, stop:1 {AMBER_HOVER});
    border-radius: 7px;
}}
QLabel#progressPercent {{
    font-family: "Segoe UI Light", "Segoe UI", sans-serif;
    font-size: 40px;
    font-weight: 300;
    color: {TEXT};
}}
QLabel#progressEta {{ color: {TEXT_DIM}; }}

/* ----------------------------------------------------------- chrome */
QMenuBar {{
    background: {BG_APP};
    border-bottom: 1px solid {BORDER_SOFT};
    color: {TEXT_DIM};
}}
QMenuBar::item {{ padding: 6px 12px; }}
QMenuBar::item:selected {{ background: {BG_PANEL}; border-radius: 6px; color: {TEXT}; }}
QMenu {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 8px 28px; border-radius: 6px; }}
QMenu::item:selected {{ background: {AMBER_TINT}; color: {AMBER_HOVER}; }}
QStatusBar {{ background: {BG_APP}; border-top: 1px solid {BORDER_SOFT}; color: {TEXT_DIM}; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: #33333B; min-height: 32px; border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: #45454E; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: #33333B; min-width: 32px; border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{ background: #45454E; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QToolTip {{
    background: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
}}
QDialogButtonBox QPushButton {{ min-width: 90px; }}
"""
