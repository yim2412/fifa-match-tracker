"""밝은 테마 — 색상과 스타일시트를 한곳에.

회색 바탕(BG) 위에 흰 카드(PANEL)를 띄우는 대시보드형. 강조색은 앱 정체성인
초록을 유지하되, 흰 바탕에서 글자로도 읽히게 다크 테마 때(#3fb950)보다 한 단계
진하게 잡았다. 바꿀 일이 생기면 여기만 고친다.
"""
from __future__ import annotations

BG = "#f3f5f9"          # 창 배경(카드 사이 회색)
PANEL = "#ffffff"       # 카드·패널
PANEL_2 = "#f6f8fb"     # 표 헤더·교차행·카드 안 타일
BORDER = "#e3e7ee"
TEXT = "#1f2533"
TEXT_DIM = "#7a8193"

GREEN = "#1f9d55"       # 강조·승·득점
GREEN_HOVER = "#23b05f"
GREEN_SOFT = "#e6f5ec"  # 선택된 메뉴·강조 배경
RED = "#e5484d"         # 패·실점
BLUE = "#3b6fe0"        # 수비력
YELLOW = "#d99a00"
PURPLE = "#7c5cf0"
ON_ACCENT = "#ffffff"   # 초록 등 진한 색 위의 글자

WIN = GREEN
DRAW = "#9aa1b0"
LOSE = RED

FONT_FAMILY = "Malgun Gothic"  # 한글이 대부분이라 명시 — 미지정이면 플랫폼 따라 들쭉날쭉
BASE_FONT_PX = 15
RADIUS = 14             # 카드 모서리
# 글자를 위에 겹쳐 쓰는 막대(승부처·시간대 등) — 원색이면 진한 글자가 묻힌다.
WIN_BAR = "#93d6ae"
LOSE_BAR = "#f2a3a6"
ROW_TINT = 0.18         # 승/패 행 배경 — 흰 바탕에 섞는 비율(다크 땐 0.35)

# 잔디 위 색 — 흰 카드용 초록(GREEN)은 잔디와 명도가 비슷해 골 점이 묻힌다.
PITCH = "#1e5c34"
PITCH_GOAL = "#5ee08f"
PITCH_MISS = "#c3c9d4"
SIDEBAR_W = 230
SHADOW_BLUR = 24        # 카드 그림자 — widgets.add_shadow
SHADOW_Y = 3
SHADOW_ALPHA = 22

# "적용"/"불러오기" 류 강조 버튼(윤곽선만, 채우지 않음) — 3곳 이상에서 반복 사용.
OUTLINE_BUTTON_QSS = f"""
QPushButton {{ background: transparent; color: {GREEN};
    border: 1px solid {GREEN}; border-radius: 8px; padding: 6px 16px;
    font-weight: bold; }}
QPushButton:hover {{ background: {GREEN_SOFT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
"""

# 일반 QWidget 에는 배경을 주지 않는다 — 자식이 부모(카드)의 흰색을 그대로
# 비치게 하려는 것. 다크 테마 때처럼 QWidget 전체에 배경을 깔면 카드 안의
# 모든 컨테이너가 회색으로 덮인다.
# 글꼴도 여기(QWidget 규칙)에 두지 않는다 — QSS 의 font 속성은 setFont 를 이긴다.
# 다크 테마 땐 QWidget 에 font-size 15px 가 있어서 코드의 setFont 크기(제목 30pt ·
# 랭커 타일 30pt · FitTableWidget 의 자동 축소)가 전부 15px 로 눌려 있었다.
# 기본 글꼴은 apply() 에서 QApplication.setFont 로 건다.
QSS = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}

QFrame#card {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QLabel#cardTitle {{ font-size: 18px; font-weight: bold; color: {TEXT}; }}
QFrame#topbar {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

QFrame#sidebar {{
    background: {PANEL};
    border: none;
    border-right: 1px solid {BORDER};
}}
QLabel#brand {{ color: {GREEN}; font-size: 22px; font-weight: bold; }}
QLabel#brandSub {{ color: {TEXT_DIM}; font-size: 12px; }}
QListWidget#nav {{
    background: transparent;
    border: none;
    outline: none;
    font-size: 14px;
}}
QListWidget#nav::item {{
    padding: 5px 12px;
    margin: 0 10px;
    border-radius: 8px;
    color: {TEXT};
}}
QListWidget#nav::item:hover {{ background: {PANEL_2}; }}
QListWidget#nav::item:selected {{
    background: {GREEN_SOFT};
    color: {GREEN};
    font-weight: bold;
}}
QListWidget#nav::item:disabled {{
    color: {TEXT_DIM};
    background: transparent;
    padding-top: 9px;
}}
QFrame#profileBox {{
    background: {PANEL_2};
    border: none;
    border-radius: 10px;
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLineEdit {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 12px;
    color: {TEXT};
    selection-background-color: {GREEN};
    selection-color: {ON_ACCENT};
}}
QLineEdit:focus {{ border: 2px solid {GREEN}; }}
QLineEdit#bigSearch {{ border: 2px solid {GREEN}; border-radius: 12px; }}

QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 16px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {GREEN}; color: {GREEN}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QPushButton#primary {{
    background: {GREEN};
    border: none;
    color: {ON_ACCENT};
    font-weight: bold;
}}
QPushButton#primary:hover {{ background: {GREEN_HOVER}; color: {ON_ACCENT}; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {TEXT_DIM}; }}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {TEXT};
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: {PANEL_2};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 9px 20px;
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
    border-radius: 10px;
    color: {TEXT};
}}
QTableWidget::item:selected {{ background: transparent; color: {TEXT}; }}
QHeaderView::section {{
    background: {PANEL_2};
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 8px 6px;
    font-weight: bold;
}}
QTableCornerButton::section {{ background: {PANEL_2}; border: none; }}

QComboBox, QSpinBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    color: {TEXT};
}}
QComboBox:hover, QSpinBox:hover {{ border-color: {GREEN}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {TEXT};
    selection-background-color: {GREEN_SOFT};
    selection-color: {GREEN};
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
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QToolTip {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 5px;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #cfd5df; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #b4bcc9; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #cfd5df; border-radius: 5px; min-width: 24px; }}
"""


def apply(app) -> None:
    """앱 전체에 테마를 건다.

    Qt 6.5+ 의 windows 스타일은 OS 다크 모드를 따라 기본 팔레트를 어둡게
    바꾼다. 위 QSS 는 일반 위젯 배경을 일부러 비워 두므로, 그대로 두면 OS 가
    다크 모드인 PC 에서 군데군데 검게 비친다 — Fusion + 밝은 팔레트로 고정한다.
    """
    from PyQt6.QtGui import QColor, QFont, QPalette

    app.setStyle("Fusion")
    font = QFont(FONT_FAMILY)
    font.setPixelSize(BASE_FONT_PX)
    app.setFont(font)
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: PANEL,
        QPalette.ColorRole.AlternateBase: PANEL_2,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: PANEL,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.ToolTipBase: PANEL,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Highlight: GREEN,
        QPalette.ColorRole.HighlightedText: ON_ACCENT,
        QPalette.ColorRole.PlaceholderText: TEXT_DIM,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
