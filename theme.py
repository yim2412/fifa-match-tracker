"""테마 — 색상과 스타일시트를 한곳에.

창 배경(BG) 위에 카드(PANEL)를 띄우는 대시보드형. 팔레트는 어두운 것과 밝은 것
둘을 두고 MODE 로 고른다 — 2026-10-01 하루에 다크 → 밝은 → 다크로 두 번 바뀌어서,
다시 바뀔 때 값을 git 에서 캐내지 않게 둘 다 남긴다. 강조색은 앱 정체성인 초록.
바꿀 일이 생기면 여기만 고친다.
"""
from __future__ import annotations

MODE = "dark"           # "dark" | "light"

_PALETTES = {
    "dark": dict(
        BG="#111418",          # 창 배경(카드 사이)
        PANEL="#1a1e24",       # 카드·패널
        PANEL_2="#22272e",     # 표 헤더·교차행·카드 안 타일
        BORDER="#2e343d",
        TEXT="#dfe3e8",        # 순백 대신 — 검은 바탕에서 눈부심을 줄인다
        TEXT_DIM="#8a919c",
        GREEN="#3fb950",
        GREEN_HOVER="#4fc960",
        GREEN_SOFT="#173322",
        RED="#ef6268",
        BLUE="#5b8cff",
        YELLOW="#e3b341",
        PURPLE="#a08cff",
        ON_ACCENT="#08210f",   # 밝은 초록 위 흰 글자는 대비가 2.3:1 이라 진한 글자
        DRAW="#6e7681",
        WIN_BAR="#2c6a40",     # 위에 밝은 글자를 겹쳐 쓰므로 진하게
        LOSE_BAR="#7a3438",
        ROW_TINT=0.35,
        SCROLL="#3a414b",
        SCROLL_HOVER="#4b535f",
        SHADOW_RGB=(0, 0, 0),
        SHADOW_ALPHA=90,
        CHART_UP="#199e70",
        CHART_DOWN="#e66767",
        CHART_NEUTRAL="#6e7681",
        CHART_GRID="#262b33",
        CHART_AXIS="#3a414b",
        # 부분-전체 조각(득점·실점 유형 도넛) — Okabe-Ito 색약 안전 팔레트에서 바탕과 구분되는 다섯. 여섯째는 "기타"=회색.
        CHART_CATS=("#56b4e9", "#e69f00", "#009e73", "#cc79a7", "#d55e00"),
        # 선수 지표 공격력·수비력 칸 — PANEL 에서 이 색까지 섞는다. 끝 색은 '모든 단계에서 TEXT 대비 ≥ 4.6'
        # 을 만족하는 가장 밝은 값으로 계산해 골랐다(1.2.1). 원색 RED 는 그 위 밝은 글자가 안 읽힌다.
        HEAT_ATK="#c41c1c",
        HEAT_DEF="#2055df",
        # 포지션 글자색 — PANEL 위 대비 ≥ 4.6. 글자(ST·CB…)가 같이 있어 색은 보조다(적록 색약).
        POS_FW="#e25a5a",
        POS_MF="#1c9c30",
        POS_DF="#4d88e0",
        POS_GK="#a9821e",
    ),
    "light": dict(
        BG="#f3f5f9",
        PANEL="#ffffff",
        PANEL_2="#f6f8fb",
        BORDER="#e3e7ee",
        TEXT="#1f2533",
        TEXT_DIM="#7a8193",
        GREEN="#1f9d55",       # 흰 바탕에서 글자로도 읽히게 한 단계 진하게
        GREEN_HOVER="#23b05f",
        GREEN_SOFT="#e6f5ec",
        RED="#e5484d",
        BLUE="#3b6fe0",
        YELLOW="#d99a00",
        PURPLE="#7c5cf0",
        ON_ACCENT="#ffffff",
        DRAW="#9aa1b0",
        WIN_BAR="#93d6ae",     # 위에 진한 글자를 겹쳐 쓰므로 옅게
        LOSE_BAR="#f2a3a6",
        ROW_TINT=0.18,
        SCROLL="#cfd5df",
        SCROLL_HOVER="#b4bcc9",
        SHADOW_RGB=(16, 24, 40),
        SHADOW_ALPHA=22,
        CHART_UP="#138a60",
        CHART_DOWN="#e34948",
        CHART_NEUTRAL="#9aa1b0",
        CHART_GRID="#eceff4",
        CHART_AXIS="#d5dae3",
        CHART_CATS=("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#d55e00"),
        HEAT_ATK="#e96363",    # 밝은 테마는 반대로 — 진한 글자가 읽히는 가장 어두운 값
        HEAT_DEF="#678ce9",
        POS_FW="#db3333",
        POS_MF="#188629",
        POS_DF="#2b71da",
        POS_GK="#8f6e19",
    ),
}
_P = _PALETTES[MODE]

BG = _P["BG"]
PANEL = _P["PANEL"]
PANEL_2 = _P["PANEL_2"]
BORDER = _P["BORDER"]
TEXT = _P["TEXT"]
TEXT_DIM = _P["TEXT_DIM"]

GREEN = _P["GREEN"]             # 강조·승·득점
GREEN_HOVER = _P["GREEN_HOVER"]
GREEN_SOFT = _P["GREEN_SOFT"]   # 선택된 메뉴·강조 배경
RED = _P["RED"]                 # 패·실점
BLUE = _P["BLUE"]               # 수비력
YELLOW = _P["YELLOW"]
PURPLE = _P["PURPLE"]
ON_ACCENT = _P["ON_ACCENT"]     # 초록 등 강조색 위의 글자

WIN = GREEN
DRAW = _P["DRAW"]
LOSE = RED

# 대시보드 그래프(charts.py) 색 — 앱의 GREEN/RED 짝은 적록 색약에서 색차 2.0 으로
# 구분이 안 된다(dataviz 검증기, 기준 6). 그래서 그래프만 청록 쪽 초록 + 밝은
# 빨강으로 다시 골랐다: 다크 6.5 · 밝은 6.9 — 6~8 은 '보조 표시가 있을 때만'
# 허용이라 그래프에는 범례·2px 틈·글자 표기를 항상 같이 둔다. 무승부·상대는 회색.
CHART_UP = _P["CHART_UP"]            # 득점·승·나
CHART_DOWN = _P["CHART_DOWN"]        # 실점·패
CHART_NEUTRAL = _P["CHART_NEUTRAL"]  # 무승부·상대 평균
CHART_GRID = _P["CHART_GRID"]        # 격자(1px 실선, 바탕보다 한 단계)
CHART_AXIS = _P["CHART_AXIS"]        # 기준선
CHART_CATS = _P["CHART_CATS"]        # 도넛 조각 색(순서대로) — 넘치는 조각은 CHART_NEUTRAL "기타"

CHART_ON_MARK = "#ffffff"            # 색 점·골대 맞은 슛 위의 흰 글자·테두리(두 테마 같음)

# 테마와 무관한 고정색 — 게임 화면의 색을 그대로 옮긴 것
PITCH_LINE = "#82ffffff"             # 축구장 선(흰색 · 알파 130)
# 강화 등급 배지 (최소 등급, 바탕, 글자) — 1~4 브론즈 · 5~7 실버 · 8~10 골드 · 11~13 홀로그램
GRADE_BADGES = ((11, "#6dd5e8", "#0a2a30"),
                (8, "#e8c545", "#3a2c00"),
                (5, "#b8bfc7", "#20242a"),
                (1, "#c17a4a", "#2b1608"))

HEAT_ATK = _P["HEAT_ATK"]   # 선수 지표 공격력 칸 끝 색
HEAT_DEF = _P["HEAT_DEF"]   # 수비력 칸 끝 색
POS_COLORS = {"FW": _P["POS_FW"], "MF": _P["POS_MF"], "DF": _P["POS_DF"], "GK": _P["POS_GK"]}

FONT_FAMILY = "Malgun Gothic"  # 한글이 대부분이라 명시 — 미지정이면 플랫폼 따라 들쭉날쭉
BASE_FONT_PX = 15
RADIUS = 14             # 카드 모서리
# 글자를 위에 겹쳐 쓰는 막대(승부처·시간대 등) — 원색이면 글자가 묻힌다.
WIN_BAR = _P["WIN_BAR"]
LOSE_BAR = _P["LOSE_BAR"]
ROW_TINT = _P["ROW_TINT"]       # 승/패 행 배경 — PANEL 에 섞는 비율

# 표본이 모자란 막대 — 원래 색을 바탕 쪽으로 이만큼만 섞는다(불투명). 색만으로 구분하지 않게
# 쓰는 쪽이 글자 "표본 N" 도 같이 붙인다(widgets.win_rate_bar · charts.HBarList).
WEAK_MIX = 0.35


def blend(base_hex: str, target_hex: str, mix: float) -> str:
    """base 에서 target 쪽으로 mix(0~1)만큼 간 불투명 색 — '#rrggbb'.

    반투명(alpha) 대신 이걸 쓴다: 표의 교차 행 색 위에 알파를 얹으면 값이 같아도 행마다
    진하기가 달라 보였다(CLAUDE.md PyQt 규칙 2번)."""
    b = [int(base_hex[i:i + 2], 16) for i in (1, 3, 5)]
    t = [int(target_hex[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * mix):02x}" for x, y in zip(b, t))


def contrast(a_hex: str, b_hex: str) -> float:
    """WCAG 대비(1~21). 글자는 4.5 이상이어야 읽힌다 — 테스트가 팔레트를 이걸로 잰다."""
    def lum(h: str) -> float:
        out = []
        for i in (1, 3, 5):
            c = int(h[i:i + 2], 16) / 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    hi, lo = sorted((lum(a_hex), lum(b_hex)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# 잔디 위 색 — 밝은 테마의 진한 GREEN 은 잔디와 명도가 비슷해 골 점이 묻힌다.
PITCH = "#1e5c34"
PITCH_GOAL = "#5ee08f"
PITCH_MISS = "#c3c9d4"
PITCH_ASSIST = "#8ec9ff"   # 어시스트 위치 → 슛 선(슛 맵 [어시스트]) — 골 초록·빗나감 회색과 갈리게 파랑
SIDEBAR_W = 230
SHADOW_BLUR = 24        # 카드 그림자 — widgets.add_shadow
SHADOW_Y = 3
SHADOW_RGB = _P["SHADOW_RGB"]
SHADOW_ALPHA = _P["SHADOW_ALPHA"]
SCROLL = _P["SCROLL"]
SCROLL_HOVER = _P["SCROLL_HOVER"]

# "적용"/"불러오기" 류 강조 버튼(윤곽선만, 채우지 않음) — 3곳 이상에서 반복 사용.
OUTLINE_BUTTON_QSS = f"""
QPushButton {{ background: transparent; color: {GREEN};
    border: 1px solid {GREEN}; border-radius: 8px; padding: 6px 16px;
    font-weight: bold; }}
QPushButton:hover {{ background: {GREEN_SOFT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
"""

# 일반 QWidget 에는 배경을 주지 않는다 — 자식이 부모(카드)의 색을 그대로
# 비치게 하려는 것. 다크 테마 때처럼 QWidget 전체에 배경을 깔면 카드 안의
# 모든 컨테이너가 BG 로 덮인다.
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
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 5px; min-width: 24px; }}
"""


def apply(app) -> None:
    """앱 전체에 테마를 건다.

    Qt 6.5+ 의 windows 스타일은 OS 다크 모드를 따라 기본 팔레트를 바꾼다.
    위 QSS 는 일반 위젯 배경을 일부러 비워 두므로, 그대로 두면 OS 설정과 MODE 가
    다른 PC 에서 군데군데 다른 색이 비친다 — Fusion + MODE 팔레트로 고정한다.
    색 체계도 MODE 로 맞춘다 — 안 맞추면 OS 설정을 따라 제목 표시줄만 다른 색이 된다.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QFont, QPalette

    app.setStyle("Fusion")
    app.styleHints().setColorScheme(
        Qt.ColorScheme.Dark if MODE == "dark" else Qt.ColorScheme.Light)
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
