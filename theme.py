"""테마 — 색상과 스타일시트를 한곳에.

창 배경(BG) 위에 카드(PANEL)를 띄우는 대시보드형. 팔레트는 어두운 것과 밝은 것
둘을 두고 MODE 로 고른다 — 2026-10-01 하루에 다크 → 밝은 → 다크로 두 번 바뀌어서,
다시 바뀔 때 값을 git 에서 캐내지 않게 둘 다 남긴다. 앱 정체성: 보라 강조(3.1.1 — 그전엔 초록).
카드는 모서리 6px · 1px 테두리 · 그림자 없음.
바꿀 일이 생기면 여기만 고친다.
"""
from __future__ import annotations

import math

MODE = "dark"           # "dark" | "light"

_PALETTES = {
    "dark": dict(
        BG="#09090b",          # 창 배경(카드 사이)
        PANEL="#18181b",       # 카드·패널
        PANEL_2="#2c2c30",     # 표 헤더·교차행·카드 안 타일
        BORDER="#313135",
        TEXT="#fafafa",
        TEXT_DIM="#a1a1aa",
        # 글자 토큰(ACCENT·GOOD·BAD·WARN)은 PANEL·PANEL_2 둘 다 대비 4.5 이상 — 테스트가 잰다.
        # *_FILL 은 막대·점·채움용(글자로 안 쓴다). 어두운 쪽은 채움 값을 글자로 써도 되는데 ACCENT 만 칸 위
        # 대비(4.49)가 모자라 한 단계 밝혔다.
        ACCENT="#9194ee",      # 강조 글자 — 제목·구분선·OVR·선택된 메뉴
        ACCENT_FILL="#8689ea",
        ACCENT_SOFT="#272737", # 선택된 메뉴·탭 바탕(위 글자 ACCENT)
        PRIMARY="#373cbe",     # 꽉 칠한 버튼·선택 바탕(위 글자 ON_PRIMARY)
        PRIMARY_HOVER="#4549cc",
        ON_PRIMARY="#ffffff",
        GOOD="#00d492",        # 좋음 — 승·득점·▲
        BAD="#ff637e",         # 나쁨 — 패·실점·▼
        WARN="#ffb900",        # 주의
        GOOD_FILL="#00d492",
        BAD_FILL="#ff637e",
        WARN_FILL="#ffb900",
        BLUE="#5b8cff",
        PURPLE="#a08cff",
        # 선수 카드 포지션 칩 — 태그 꼴(글자 = 그 색 · 바탕 = 그 색 10%). 글자라 PANEL·PANEL_2·10% 바탕 모두 4.5 이상
        CHIP_GK="#ffb900",
        CHIP_DF="#50a2ff",
        CHIP_MF="#00d492",
        CHIP_FW="#ff637e",
        # 작은 축구장(포메이션 그림) — 판 위→아래 그라데이션 · 선
        FORMATION_PITCH_TOP="#0d2818",
        FORMATION_PITCH_BOTTOM="#0a1f12",
        FORMATION_LINE="#9bbeac",
        DRAW="#71717a",
        WIN_BAR="#0b6b4d",     # 위에 밝은 글자를 겹쳐 쓰므로 진하게
        LOSE_BAR="#a02c42",
        ROW_TINT=0.35,
        SCROLL="#3f3f46",
        SCROLL_HOVER="#52525b",
        CHART_UP="#199e70",
        CHART_DOWN="#e66767",
        CHART_NEUTRAL="#6e7681",
        CHART_GRID="#262b33",
        CHART_AXIS="#3a414b",
        # 부분-전체 조각(득점·실점 유형 도넛) — Okabe-Ito 색약 안전 팔레트에서 바탕과 구분되는 다섯. 여섯째는 "기타"=회색.
        CHART_CATS=("#56b4e9", "#e69f00", "#009e73", "#cc79a7", "#d55e00"),
        CHART_RANK="#e69f00",
        CHART_VALUE="#f59e0b",
        # 선수 지표 공격력·수비력 칸 — PANEL 에서 이 색까지 섞는다. 끝 색은 '모든 단계에서 TEXT 대비 ≥ 4.6'
        # 을 만족하는 가장 밝은 값으로 계산해 골랐다(1.2.1). 원색 빨강은 그 위 밝은 글자가 안 읽힌다.
        HEAT_ATK="#c41c1c",
        HEAT_DEF="#2055df",
        # 포지션 글자색 — PANEL 위 대비 ≥ 4.6. 글자(ST·CB…)가 같이 있어 색은 보조다(적록 색약).
        POS_FW="#e25a5a",
        POS_MF="#1c9c30",
        POS_DF="#4d88e0",
        POS_GK="#a9821e",
    ),
    "light": dict(
        BG="#f6f7f9",
        PANEL="#ffffff",
        PANEL_2="#e7e9ee",
        BORDER="#d3d7de",
        TEXT="#09090b",
        TEXT_DIM="#616875",
        ACCENT="#373cbe",
        ACCENT_FILL="#373cbe",
        ACCENT_SOFT="#ebebf8",
        PRIMARY="#373cbe",
        PRIMARY_HOVER="#4549cc",
        ON_PRIMARY="#ffffff",
        # 흰 바탕에서 채움 값(#009966 3.65 · #e17100 3.20 · #ec003f 칸 위 3.73)은 글자로 안 읽혀 글자만 진하게
        GOOD="#065f46",
        BAD="#be123c",
        WARN="#92400e",
        GOOD_FILL="#009966",
        BAD_FILL="#ec003f",
        WARN_FILL="#e17100",
        BLUE="#3b6fe0",
        PURPLE="#7c5cf0",
        # 칩 글자 — 기준 한 단계(GK·MF 는 두 단계) 진한 값: 흰 칸 위 #bb4d00 4.14 · #007a55 4.42 라 글자로 미달
        CHIP_GK="#973c00",
        CHIP_DF="#1447e6",
        CHIP_MF="#006045",
        CHIP_FW="#c70036",
        FORMATION_PITCH_TOP="#2d6045",
        FORMATION_PITCH_BOTTOM="#214a34",
        FORMATION_LINE="#e8f0e8",
        DRAW="#9aa1b0",
        WIN_BAR="#8fdcbc",     # 위에 진한 글자를 겹쳐 쓰므로 옅게
        LOSE_BAR="#f4a3b1",
        ROW_TINT=0.18,
        SCROLL="#cfd5df",
        SCROLL_HOVER="#b4bcc9",
        CHART_UP="#138a60",
        CHART_DOWN="#e34948",
        CHART_NEUTRAL="#9aa1b0",
        CHART_GRID="#eceff4",
        CHART_AXIS="#d5dae3",
        CHART_CATS=("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#d55e00"),
        # 흰 카드 위 그래픽 대비 3.0 — 어두운 쪽 주황(#e69f00 · 2.25)·#f59e0b(2.15)는 미달이라 한 단계 진하게(66 E2).
        # 순위선은 같은 그래프의 200위 컷(CHART_DOWN)과 색약 6 이상이어야 해 붉은 쪽(#b45309 는 5.9)
        CHART_RANK="#c2410c",
        CHART_VALUE="#b45309",
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

# 강조(앱 정체성 · 보라)와 좋음/나쁨(승·패)은 다른 토큰이다 — 한 색에 두 뜻을 싣던 시절엔 강조를 바꾸면
# 승·골까지 같이 바뀌었다. 좋음/나쁨 짝도 적록 색약 색차가 6 안팎(cvd_delta: 어두운 5.3 · 밝은 글자 5.0 · 채움 6.6)이라
# 색만으로 승패를 말하지 않는다 — 쓰는 자리마다 글자·부호(승/패 · +/− · ▲▼)가 같이 있다.
ACCENT = _P["ACCENT"]               # 강조 글자·테두리
ACCENT_FILL = _P["ACCENT_FILL"]     # 강조 채움(진행 막대 등)
ACCENT_SOFT = _P["ACCENT_SOFT"]     # 선택된 메뉴·탭 바탕
PRIMARY = _P["PRIMARY"]             # 꽉 칠한 버튼·선택 바탕
PRIMARY_HOVER = _P["PRIMARY_HOVER"]
ON_PRIMARY = _P["ON_PRIMARY"]       # PRIMARY 위 글자
GOOD = _P["GOOD"]                   # 좋음 글자 — 승·득점
BAD = _P["BAD"]                     # 나쁨 글자 — 패·실점
WARN = _P["WARN"]                   # 주의 글자
GOOD_FILL = _P["GOOD_FILL"]         # 위 셋의 막대·점·채움 값
BAD_FILL = _P["BAD_FILL"]
WARN_FILL = _P["WARN_FILL"]
BLUE = _P["BLUE"]                   # 수비력
OPP_FILL = BLUE                     # 경기 상세 창 비교 막대·골 점의 상대 몫(65 M3 — 내 몫은 ACCENT_FILL)
PURPLE = _P["PURPLE"]
CHIP_GK = _P["CHIP_GK"]             # 선수 카드 포지션 칩 글자·테두리(바탕은 CHIP_SOFT_MIX 만큼 섞음)
CHIP_DF = _P["CHIP_DF"]
CHIP_MF = _P["CHIP_MF"]
CHIP_FW = _P["CHIP_FW"]
CHIP_SOFT_MIX = 0.10                # 태그 꼴 바탕 — widgets.tag_qss 와 같은 비율

WIN = GOOD
DRAW = _P["DRAW"]
LOSE = BAD

# 대시보드 그래프(charts.py) 색 — 옛 앱 초록/빨강 짝(#3fb950/#ef6268)은 적록 색약에서 색차 2.0 으로
# 구분이 안 된다(dataviz 검증기, 기준 6). 그래서 그래프만 청록 쪽 초록 + 밝은
# 빨강으로 다시 골랐다: 다크 6.5 · 밝은 6.9 — 6~8 은 '보조 표시가 있을 때만'
# 허용이라 그래프에는 범례·2px 틈·글자 표기를 항상 같이 둔다. 무승부·상대는 회색.
CHART_UP = _P["CHART_UP"]            # 득점·승·나
CHART_DOWN = _P["CHART_DOWN"]        # 실점·패
CHART_NEUTRAL = _P["CHART_NEUTRAL"]  # 무승부·상대 평균
CHART_GRID = _P["CHART_GRID"]        # 격자(1px 실선, 바탕보다 한 단계)
CHART_AXIS = _P["CHART_AXIS"]        # 기준선
CHART_CATS = _P["CHART_CATS"]        # 도넛 조각 색(순서대로) — 넘치는 조각은 CHART_NEUTRAL "기타"
CHART_LINE = ACCENT_FILL             # 추이 그래프 기본 선(3.1.1 — 66 G0)
CHART_RANK = _P["CHART_RANK"]        # 점수 그래프의 순위 점선·오른쪽 축(57 K5) — 보라 점수선·컷 선과 색약에서도 갈리는 주황
CHART_VALUE = _P["CHART_VALUE"]      # 구단가치 그래프 선·띠(66 G3)

CHART_ON_MARK = "#ffffff"            # 색 점·골대 맞은 슛 위의 흰 글자·테두리(두 테마 같음)

# 테마와 무관한 고정색 — 게임 화면의 색을 그대로 옮긴 것
PITCH_LINE = "#82ffffff"             # 축구장 선(흰색 · 알파 130)
# 강화 등급 배지 (최소 등급, 바탕, 글자) — 1~4 브론즈 · 5~7 실버 · 8~10 골드 · 11~13 홀로그램
GRADE_BADGES = ((11, "#6dd5e8", "#0a2a30"),
                (8, "#e8c545", "#3a2c00"),
                (5, "#b8bfc7", "#20242a"),
                (1, "#c17a4a", "#2b1608"))

# 특성 아이콘 뒤 육각 판(넥슨 원본이 아니라 QPainter 로 그린다) · 작은 축구장(포메이션 점) — 두 테마 같은 고정색
TRAIT_PAD_GOLD = "#e8c545"   # 일반·신규 특성 판
TRAIT_PAD_GRAY = "#8a929c"   # 훈련 특성·코치 판
# 작은 축구장 — 점 색은 그림 속 높이 띠(stats.PITCH_ROWS 순서: 공격 · 공미 · 미드 · 수미 · 수비 · GK). GK 는 흰색(선 색과 갈리게)
FORMATION_PITCH_TOP = _P["FORMATION_PITCH_TOP"]
FORMATION_PITCH_BOTTOM = _P["FORMATION_PITCH_BOTTOM"]
FORMATION_LINE = _P["FORMATION_LINE"]
FORMATION_LINE_COLORS = ("#f87171", "#fb923c", "#facc15", "#84cc16", "#38bdf8", "#ffffff")
FORMATION_DOT_EDGE = "#66000000"    # 점 테두리(검정 · 알파 40%)

HEAT_ATK = _P["HEAT_ATK"]   # 기간 표 승률 칸(평균보다 높음) 끝 색 — 선수 지표 칸은 3.1.1 부터 G8(아래 g8_cell)
HEAT_DEF = _P["HEAT_DEF"]   # 같은 표 낮음 쪽
# 선수 지표 공격력·수비력 칸(65 M9 · G8) — 다른 기준 화면 코드에서 읽은 값(ROADMAP "G8 색 규칙"). 어두운 화면은 바탕에서
# 끝 색까지 직선, 밝은 화면은 값 ÷ G8_LIGHT_SPAN 으로 채널마다 따로 — 두 식이 다르다(기준 화면 그대로)
G8_END = {"atk": "#c63444", "def": "#3062cd"}   # rgb(198,52,68) · rgb(48,98,205)
G8_LIGHT_SPAN = 900
# 밝은 화면 칸 글자 후보 — TEXT(#09090b)·흰색 둘 다 4.5 에 모자라는 구간이 있다(수비 535 언저리 #6767e9 위 4.46·4.47).
# 순수 검정이면 4.7 — 세 후보 중 대비 큰 쪽(test_g8_cells_match_reference 가 0~1600 전부 잰다)
G8_LIGHT_INKS = ("#000000",)
POS_COLORS = {"FW": _P["POS_FW"], "MF": _P["POS_MF"], "DF": _P["POS_DF"], "GK": _P["POS_GK"]}

FONT_FAMILY = "Malgun Gothic"  # 한글이 대부분이라 명시 — 미지정이면 플랫폼 따라 들쭉날쭉
BASE_FONT_PX = 15
RADIUS = 6              # 카드·표·입력·버튼 모서리 — 그림 안 작은 막대·칩(2~4px)은 각자
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


def g8_cell(v: float, kind: str, scale: tuple[float, float], mode: str = MODE,
            base: str | None = None) -> tuple[str | None, str]:
    """선수 지표 공격력(kind "atk")·수비력("def") 칸 → (배경 '#rrggbb' — None 이면 안 칠함, 글자색). 표 안 값과 무관한 고정 눈금.

    어두운: 값 < 아래 끝 → 안 칠함 + 흐린 글자 · 그 위 t = min((값 − 아래) / (위 − 아래), 1) 만큼 바탕(base · 기본 PANEL)에서
    G8_END 로(채널마다 반올림). 밝은: 값 < 아래 끝 → 안 칠함 · 그 위는 값 ÷ G8_LIGHT_SPAN 식(기준 화면) — 글자는 기준 화면의
    "700 넘으면 흰색" 대신 TEXT·ON_PRIMARY·G8_LIGHT_INKS 중 대비가 큰 쪽(그 규칙은 공격 끝에서 흰 글자 3.9 · 수비 600 근처 검정 3.9 로 4.5 미달)."""
    p = _PALETTES[mode]
    lo, hi = scale
    if v < lo:
        return None, p["TEXT_DIM"] if mode == "dark" else p["TEXT"]
    if mode == "dark":
        t = min((v - lo) / (hi - lo), 1.0)
        b, e = base or p["PANEL"], G8_END[kind]
        rgb = [int(b[i:i + 2], 16) + (int(e[i:i + 2], 16) - int(b[i:i + 2], 16)) * t for i in (1, 3, 5)]
        return "#" + "".join(f"{int(c + 0.5):02x}" for c in rgb), p["TEXT"]   # 0.5 는 올림(round() 는 짝수 쪽 — 기준 화면과 1 어긋난다)
    # 기준 화면 글에는 공격 R 이 "200 + 값/900·255/55" 로 적혀 있다 — 수비 B 와 같은 "·55" 가 깨진 것으로 읽었다
    # (그대로면 R 이 200~205 에 머물러 빨강이 안 된다 · min(…, 255) 가 붙은 까닭도 ·55 일 때만 선다)
    f = v / G8_LIGHT_SPAN
    rgb = ((200 + f * 55, 255 - f * 178.5, 255 - f * 153) if kind == "atk"
           else (255 - f * 255, 255 - f * 255, 200 + f * 55))
    bg = "#" + "".join(f"{int(max(0.0, min(c, 255.0)) + 0.5):02x}" for c in rgb)
    fg = max((p["TEXT"], p["ON_PRIMARY"], *G8_LIGHT_INKS), key=lambda c: contrast(c, bg))
    return bg, fg


# Machado·Oliveira·Fernandes(2009) 심도 1.0 — 선형 RGB 에 곱한다.
_CVD = {"protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
        "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881))}


def cvd_delta(a_hex: str, b_hex: str) -> float:
    """적록 색약(1형·2형 중 나쁜 쪽)에서 두 색의 차이 — OKLab 거리 × 100.

    8 이상이면 색만으로 갈린다 · 6~8 은 글자·부호가 같이 있을 때만 · 6 미만은 색으로 구분 안 됨.
    그래프 색을 고를 때 쓴 dataviz 검증기와 같은 식이다(옛 초록/빨강 2.0 · CHART_UP/DOWN 6.5·6.9 를 그대로 낸다)."""
    def lin(h: str) -> list[float]:
        out = []
        for i in (1, 3, 5):
            c = int(h[i:i + 2], 16) / 255
            out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        return out

    def oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
        l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
        m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
        s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
        return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)

    def sim(h: str, mat) -> tuple[float, float, float]:
        rgb = lin(h)
        return tuple(max(0.0, min(1.0, sum(k * c for k, c in zip(row, rgb)))) for row in mat)

    return min(100 * math.dist(oklab(*sim(a_hex, m)), oklab(*sim(b_hex, m))) for m in _CVD.values())


# 잔디 위 색 — 밝은 테마의 진한 초록은 잔디와 명도가 비슷해 골 점이 묻힌다.
PITCH = "#1e5c34"
PITCH_GOAL = "#5ee08f"
PITCH_MISS = "#c3c9d4"
PITCH_ASSIST = "#8ec9ff"   # 어시스트 위치 → 슛 선(슛 맵 [어시스트]) — 골 초록·빗나감 회색과 갈리게 파랑
SIDEBAR_W = 230
SCROLL = _P["SCROLL"]
SCROLL_HOVER = _P["SCROLL_HOVER"]

# "적용"/"불러오기" 류 강조 버튼(윤곽선만, 채우지 않음) — 3곳 이상에서 반복 사용.
OUTLINE_BUTTON_QSS = f"""
QPushButton {{ background: transparent; color: {ACCENT};
    border: 1px solid {ACCENT}; border-radius: {RADIUS}px; padding: 6px 16px;
    font-weight: bold; }}
QPushButton:hover {{ background: {ACCENT_SOFT}; }}
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
QLabel#brand {{ color: {ACCENT}; font-size: 22px; font-weight: bold; }}
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
    border-radius: {RADIUS}px;
    color: {TEXT};
}}
QListWidget#nav::item:hover {{ background: {PANEL_2}; }}
QListWidget#nav::item:selected {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
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
    border-radius: {RADIUS}px;
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLineEdit {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 7px 12px;
    color: {TEXT};
    selection-background-color: {PRIMARY};
    selection-color: {ON_PRIMARY};
}}
QLineEdit:focus {{ border: 2px solid {ACCENT}; }}
QLineEdit#bigSearch {{ border: 2px solid {ACCENT}; border-radius: {RADIUS}px; }}

QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 7px 16px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QPushButton#primary {{
    background: {PRIMARY};
    border: none;
    color: {ON_PRIMARY};
    font-weight: bold;
}}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; color: {ON_PRIMARY}; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {TEXT_DIM}; }}
QPushButton#pageTab {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 5px 16px;
    color: {TEXT_DIM};
}}
QPushButton#pageTab:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#pageTab:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
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
    border-radius: {RADIUS}px;
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: {PANEL_2};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: {RADIUS}px;
    border-top-right-radius: {RADIUS}px;
    padding: 9px 20px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {PANEL};
    color: {ACCENT};
    font-weight: bold;
}}

QTableWidget {{
    background: {PANEL};
    alternate-background-color: {PANEL_2};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
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
    border-radius: {RADIUS}px;
    padding: 5px 8px;
    color: {TEXT};
}}
QComboBox:hover, QSpinBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {TEXT};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {ACCENT};
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
QProgressBar::chunk {{ background: {ACCENT_FILL}; border-radius: 5px; }}
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
        QPalette.ColorRole.Highlight: PRIMARY,
        QPalette.ColorRole.HighlightedText: ON_PRIMARY,
        QPalette.ColorRole.PlaceholderText: TEXT_DIM,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
