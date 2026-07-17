"""다크 테마 — 색상과 스타일시트를 한곳에.

색은 참고한 전적 사이트 화면에 맞춘 값이다. 바꿀 일이 생기면 여기만 고친다.
"""
from __future__ import annotations

BG = "#0f1216"          # 창 배경
PANEL = "#171b21"       # 카드·패널
PANEL_2 = "#1c2128"     # 표 헤더·교차행
BORDER = "#2a313a"
TEXT = "#e6edf3"
TEXT_DIM = "#8b949e"

GREEN = "#3fb950"       # 승·득점·강조
RED = "#f85149"         # 패·실점
BLUE = "#388bfd"        # 수비력
YELLOW = "#d29922"
PURPLE = "#a371f7"

WIN = GREEN
DRAW = "#8b949e"
LOSE = RED

QSS = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 12px;
}}
QLabel {{ background: transparent; }}

QLineEdit {{
    background: {PANEL_2};
    border: 2px solid {GREEN};
    border-radius: 6px;
    padding: 8px 12px;
    color: {TEXT};
    selection-background-color: {GREEN};
}}
QLineEdit::placeholder {{ color: {TEXT_DIM}; }}

QPushButton {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {GREEN}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QPushButton#primary {{
    background: {GREEN};
    border: none;
    color: #06240d;
    font-weight: bold;
}}
QPushButton#primary:hover {{ background: #4ac95d; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {TEXT_DIM}; }}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT};
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: {BG};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 18px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {PANEL};
    color: {GREEN};
    font-weight: bold;
}}

QTableWidget {{
    background: {PANEL};
    alternate-background-color: {PANEL_2};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
}}
QTableWidget::item:selected {{ background: #24303d; color: {TEXT}; }}
QHeaderView::section {{
    background: {PANEL_2};
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px 4px;
    font-weight: bold;
}}
QTableCornerButton::section {{ background: {PANEL_2}; border: none; }}

QComboBox, QSpinBox {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    color: {TEXT};
}}
QComboBox:hover, QSpinBox:hover {{ border-color: {GREEN}; }}
QComboBox QAbstractItemView {{
    background: {PANEL_2};
    color: {TEXT};
    selection-background-color: {GREEN};
    selection-color: #06240d;
    border: 1px solid {BORDER};
}}
QCheckBox {{ color: {TEXT}; }}
QProgressBar {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 5px; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QToolTip {{
    background: {PANEL_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #3d4753; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
"""
