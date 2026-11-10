"""테마 — 색상과 스타일시트를 한곳에.

창 배경(BG) 위에 카드(PANEL)를 띄우는 대시보드형. 팔레트는 어두운 것과 밝은 것
둘을 두고 MODE 로 고른다 — 2026-10-01 하루에 다크 → 밝은 → 다크로 두 번 바뀌어서,
다시 바뀔 때 값을 git 에서 캐내지 않게 둘 다 남긴다. 앱 정체성: 초록 강조(3.2.2 — 3.1.1 한 판만 보라였다).
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
        CARD_BORDER="#3a3a41",  # 카드 테두리만(86 B1) — 표 격자선(BORDER)보다 한 단계 밝아 카드가 바탕에서 떠 보인다
        TEXT="#fafafa",
        TEXT_DIM="#a1a1aa",
        # 글자 토큰(ACCENT·GOOD·BAD·WARN)은 PANEL·PANEL_2 둘 다 대비 4.5 이상 — 테스트가 잰다.
        # *_FILL 은 막대·점·채움용(글자로 안 쓴다).
        # 강조는 초록(3.2.2 — 사용자 요청으로 3.1.1 의 보라를 되돌림 · 3.1.1 전 GREEN 값 그대로). 밝은 초록 위 흰 글자는
        # 대비 2.3 이라 버튼 글자는 진한 색(6.70).
        ACCENT="#3fb950",      # 강조 글자 — 제목·구분선·OVR·선택된 메뉴
        ACCENT_FILL="#3fb950",
        ACCENT_SOFT="#173322", # 선택된 메뉴·탭 바탕(위 글자 ACCENT)
        PRIMARY="#3fb950",     # 꽉 칠한 버튼·선택 바탕(위 글자 ON_PRIMARY)
        PRIMARY_HOVER="#4fc960",
        ON_PRIMARY="#08210f",
        # 좋음 = 게임 초록(95) · 나쁨 = 빨강(3.7.2 사용자: 게임 파랑이 익숙하지 않다 — 95 전 값). 패도 빨강(LOSE).
        GOOD="#07f468",        # 좋음 — 승·득점·▲
        BAD="#ff637e",         # 나쁨 — 패·실점·▼·적음
        WARN="#ffb900",        # 주의
        ERROR="#ff637e",       # 오류 글 — 95 에 나쁨과 떼어 낸 토큰(3.7.2 부터 값은 다시 같다)
        GOOD_FILL="#07f468",
        BAD_FILL="#ff637e",
        LOSE_FILL="#ff637e",   # 패 칸·띠 — 빨강(3.7.2 · 95 의 진회색은 무 회색과 헷갈렸다)
        DRAW_FILL="#cecec8",   # 무 칸·띠 — 회색
        LEVEL="#159aff",       # 감독 레벨 숫자 — 게임 랭킹 #0894fc 는 머리 칸(PANEL_2) 위 4.41 이라 한 단계 밝게(4.7)
        WARN_FILL="#ffb900",
        BLUE="#5b8cff",
        PURPLE="#a08cff",
        # 선수 카드 포지션 칩 — 태그 꼴(글자 = 그 색 · 바탕 = 그 색 10%). 게임 값 그대로(95 · 사용자 결정) — 어두운 바탕
        # 대비 2.7~4.0 으로 4.5 미달이지만 포지션 글자(ST·CB…)가 같이 있어 색은 보조다(test 는 2.5 로 잰다)
        CHIP_GK="#f4b400",
        CHIP_DF="#306cfc",
        CHIP_MF="#248434",
        CHIP_FW="#d81c28",
        # 작은 축구장(포메이션 그림) — 판 위→아래 그라데이션 · 선
        FORMATION_PITCH_TOP="#0d2818",
        FORMATION_PITCH_BOTTOM="#0a1f12",
        FORMATION_LINE="#9bbeac",
        DRAW="#71717a",
        WIN_BAR="#0b6b4d",     # 위에 밝은 글자를 겹쳐 쓰므로 진하게
        LOSE_BAR="#a02c42",
        ROW_TINT=0.28,         # 95 — 게임 초록 승 카드 위 흐린 글자(날짜) 3.46 · 0.35 면 2.79(옛 초록 3.24)
        SCROLL="#3f3f46",
        SCROLL_HOVER="#52525b",
        CHART_UP="#07f468",
        CHART_DOWN="#e66767",
        CHART_CUT="#e66767",   # 점수 그래프 200위 컷 선 — 옛 CHART_DOWN(파랑이 되면 보라 점수선과 색약 2.9)
        CHART_NEUTRAL="#6e7681",
        CHART_GRID="#262b33",
        CHART_AXIS="#3a414b",
        # 부분-전체 조각(득점·실점 유형 도넛) — Okabe-Ito 색약 안전 팔레트에서 바탕과 구분되는 다섯. 여섯째는 "기타"=회색.
        CHART_CATS=("#56b4e9", "#e69f00", "#009e73", "#cc79a7", "#d55e00"),
        CHART_RANK="#e69f00",
        CHART_VALUE="#f59e0b",
        # 추이 그래프 선·달력 칠 — 강조(초록)와 따로(3.2.2). 초록 선은 순위(주황)·200위 컷(빨강) 선과 색약 색차가
        # 3.4·2.6 이라(6 미만) 3.1.1 의 보라 그대로 · 초록 칠 위 빨강 ▼ 는 2.99 라 달력도 그대로
        CHART_LINE="#8689ea",
        CAL_FILL="#8689ea",
        # 기간 표 승률 칸 — PANEL 에서 이 색까지 섞는다. 끝 색은 '모든 단계에서 TEXT 대비 ≥ 4.6' 을 만족하는 값(1.2.1).
        # 원색은 그 위 밝은 글자가 안 읽힌다. 79 — 높음 = 초록 · 낮음 = 빨강(예전 높음 빨강 · 낮음 파랑은 높은 승률이
        # 나쁨으로 읽혔다). 초록은 빨강과 PANEL 대비가 비슷한 값(빨강 2.98 · 초록 3.02)으로 골라 한쪽만 튀지 않게.
        HEAT_GOOD="#117438",
        HEAT_BAD="#c41c1c",   # 낮음 = 빨강(3.7.2 — 95 전 값)
        # 포지션 글자색 — 게임 값(칩과 같다 · 95)
        POS_FW="#d81c28",
        POS_MF="#248434",
        POS_DF="#306cfc",
        POS_GK="#f4b400",
    ),
    "light": dict(
        BG="#f6f7f9",
        PANEL="#ffffff",
        PANEL_2="#e7e9ee",
        BORDER="#d3d7de",
        CARD_BORDER="#cdd2da",
        TEXT="#09090b",
        TEXT_DIM="#616875",
        # 강조 초록(3.2.2) — 3.1.1 전 #1f9d55 는 글자로 3.4 라 글자·버튼만 진하게(칸 위 5.87 · 흰 글자 5.02)
        ACCENT="#166534",
        ACCENT_FILL="#1f9d55",
        ACCENT_SOFT="#e6f5ec",
        PRIMARY="#15803d",
        PRIMARY_HOVER="#166534",
        ON_PRIMARY="#ffffff",
        # 흰 바탕에서 채움 값(#009966 3.65 · #e17100 3.20 · #ec003f 칸 위 3.73)은 글자로 안 읽혀 글자만 진하게
        # 3.7.2 — 좋음·나쁨은 95 전 초록·빨강 짝 그대로(게임 초록을 남기면 이 빨강과 채움 색약 색차가 2.9)
        GOOD="#065f46",
        BAD="#be123c",
        WARN="#92400e",
        ERROR="#be123c",
        GOOD_FILL="#009966",
        BAD_FILL="#ec003f",
        LOSE_FILL="#ec003f",
        DRAW_FILL="#8c8c86",
        LEVEL="#0b63c4",
        WARN_FILL="#e17100",
        BLUE="#3b6fe0",
        PURPLE="#7c5cf0",
        # 칩 글자 — 게임 값(95) · GK 만 게임 노랑이 흰 바탕 1.85 라 진한 값 그대로
        CHIP_GK="#973c00",
        CHIP_DF="#306cfc",
        CHIP_MF="#248434",
        CHIP_FW="#d81c28",
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
        CHART_CUT="#e34948",
        # 무승부·도넛 "기타"·경기 수 막대 — 흰 카드 위 그래픽 대비 3.0(옛 #9aa1b0 은 2.59 · 67 R12) → 3.38
        CHART_NEUTRAL="#858c9b",
        CHART_GRID="#eceff4",
        CHART_AXIS="#d5dae3",
        CHART_CATS=("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#d55e00"),
        # 흰 카드 위 그래픽 대비 3.0 — 어두운 쪽 주황(#e69f00 · 2.25)·#f59e0b(2.15)는 미달이라 한 단계 진하게(66 E2).
        # 순위선은 같은 그래프의 200위 컷(CHART_DOWN)과 색약 6 이상이어야 해 붉은 쪽(#b45309 는 5.9)
        CHART_RANK="#c2410c",
        CHART_VALUE="#b45309",
        CHART_LINE="#373cbe",   # 위 어두운 쪽 주석과 같은 이유(3.2.2)
        CAL_FILL="#373cbe",
        HEAT_GOOD="#18a550",   # 밝은 테마는 반대로 — 진한 글자가 읽히는 값(PANEL 대비 빨강 3.27 · 초록 3.21)
        HEAT_BAD="#e96363",
        POS_FW="#d81c28",
        POS_MF="#248434",
        POS_DF="#306cfc",
        POS_GK="#8f6e19",
    ),
}
_P = _PALETTES[MODE]

BG = _P["BG"]
PANEL = _P["PANEL"]
PANEL_2 = _P["PANEL_2"]
BORDER = _P["BORDER"]
CARD_BORDER = _P["CARD_BORDER"]   # 카드·묶음 테두리(86 B1) — PANEL 은 안 바꾼다(얼린 추이 그래프 · 기준 화면 P1)
TEXT = _P["TEXT"]
TEXT_DIM = _P["TEXT_DIM"]

# 강조(앱 정체성 · 초록)와 좋음/나쁨(승·패)은 다른 토큰이다 — 한 색에 두 뜻을 싣던 시절엔 강조를 바꾸면
# 승·골까지 같이 바뀌었다. 좋음/나쁨은 3.7.2 부터 게임 초록 · 빨강(색약 색차 어두운 글자 14.0 · 그래프 17.1 — 95 전 초록·빨강은
# 5.3 · 95 의 초록·파랑은 36.1). 색만으로 승패를 말하지 않게 쓰는 자리마다 글자·부호(승/패 · +/− · ▲▼)를 같이 둔다.
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

ERROR = _P["ERROR"]                 # 오류 글(검색 실패·키 거절) — 뜻이 달라 나쁨과 따로 둔다
LEVEL = _P["LEVEL"]                 # 감독 레벨 숫자

# 승·무·패(3.7.2 사용자): 승 = 초록 · 무 = 회색 · 패 = 빨강(나쁨과 같은 색). 95 의 패 진회색은 무와 헷갈렸다
WIN = GOOD
DRAW = _P["DRAW"]
LOSE = BAD
LOSE_FILL = _P["LOSE_FILL"]
DRAW_FILL = _P["DRAW_FILL"]

# 대시보드 그래프(charts.py) 색 — 옛 앱 초록/빨강 짝(#3fb950/#ef6268)은 적록 색약에서 색차 2.0 으로
# 구분이 안 된다(dataviz 검증기, 기준 6). 3.7.2 부터 게임 초록 · 빨강(다크 17.1) — 범례·2px 틈·글자 표기는
# 그대로 같이 둔다. 무승부·상대는 회색.
CHART_UP = _P["CHART_UP"]            # 득점·승·나
CHART_DOWN = _P["CHART_DOWN"]        # 실점·패·적음
CHART_CUT = _P["CHART_CUT"]          # 점수 그래프 200위 컷 선(95 에 실점 색과 떼어 냈다 — 3.7.2 부터 값은 다시 같다)
CHART_NEUTRAL = _P["CHART_NEUTRAL"]  # 무승부·상대 평균
CHART_GRID = _P["CHART_GRID"]        # 격자(1px 실선, 바탕보다 한 단계)
CHART_AXIS = _P["CHART_AXIS"]        # 기준선
CHART_CATS = _P["CHART_CATS"]        # 도넛 조각 색(순서대로) — 넘치는 조각은 CHART_NEUTRAL "기타"
CHART_LINE = _P["CHART_LINE"]        # 추이 그래프 기본 선(66 G0 · 3.2.2 부터 강조와 따로)
CAL_FILL = _P["CAL_FILL"]            # 달력 칸 FC 진하기(66 G5 · 3.2.2 부터 강조와 따로)
CHART_RANK = _P["CHART_RANK"]        # 점수 그래프의 순위 점선·오른쪽 축(57 K5) — 초록 점수선·컷 선과 색약에서도 갈리는 주황
CHART_VALUE = _P["CHART_VALUE"]      # 구단가치 그래프 선·띠(66 G3)

CHART_ON_MARK = "#ffffff"            # 색 점·골대 맞은 슛 위의 흰 글자·테두리(두 테마 같음)
CHART_ON_MARK_DARK = "#151616"       # 밝은 칸(게임 초록·무 연회색) 위 글자 — 게임 결과 칸 글자색(95)


def ink_on(bg: str) -> str:
    """색 칸 위 글자 — 흰색과 진한 글자 중 대비가 큰 쪽(게임 초록 #07f468 위 흰색은 1.3)."""
    return max((CHART_ON_MARK, CHART_ON_MARK_DARK), key=lambda c: contrast(c, bg))

# 테마와 무관한 고정색 — 게임 화면의 색을 그대로 옮긴 것
PITCH_LINE = "#82ffffff"             # 축구장 선(흰색 · 알파 130)
# 강화 배지(83 · 3.7.1 부터 모든 강화 배지 — widgets.paint_grade_badge) — (최소 등급, 바탕 그러데이션, 글자, 위·왼쪽 테두리, 아래·오른쪽 테두리). 넥슨 웹 CSS `en_level1~13` 그대로(2026-11-06) ·
# 11~13 은 그 CSS 가 쓰는 바탕 그림 bg_plt.png 의 대각선 색을 뽑았다(공지 표의 아이콘은 표 그림 안에 박혀 따로 받을 수 없다) ·
# 글자색과 11~13 테두리는 공지 "강화 성공 확률 안내" 그림에서 뽑았다(2026-11-09 — 진한 굵은 숫자 · 회색 테두리)
GRADE_ICONS = ((11, ("#f8e7f2", "#dfbac0", "#67f9fd"), "#141414", "#efe9ee", "#9a9ea6"),
               (8, ("#f9dd62", "#dca908"), "#552800", "#e9d36c", "#cda000"),
               (5, ("#d8d9dc", "#b8bdca"), "#2e3135", "#d8dadc", "#a5a8ae"),
               (2, ("#de946b", "#ad5f42"), "#521d02", "#e4b7a2", "#864229"),
               (1, ("#51545a", "#42464d"), "#ffffff", "#62676d", "#393a3c"))

# 작은 축구장(포메이션 점) — 두 테마 같은 고정색
# 작은 축구장 — 점 색은 그림 속 높이 띠(stats.PITCH_ROWS 순서: 공격 · 공미 · 미드 · 수미 · 수비 · GK). GK 는 흰색(선 색과 갈리게)
FORMATION_PITCH_TOP = _P["FORMATION_PITCH_TOP"]
FORMATION_PITCH_BOTTOM = _P["FORMATION_PITCH_BOTTOM"]
FORMATION_LINE = _P["FORMATION_LINE"]
FORMATION_LINE_COLORS = ("#f87171", "#fb923c", "#facc15", "#84cc16", "#38bdf8", "#ffffff")
FORMATION_DOT_EDGE = "#66000000"    # 점 테두리(검정 · 알파 40%)

HEAT_GOOD = _P["HEAT_GOOD"]   # 기간 표 승률 칸(평균보다 높음) 끝 색 — 선수 지표 칸은 3.1.1 부터 G8(아래 g8_cell)
HEAT_BAD = _P["HEAT_BAD"]     # 같은 표 낮음 쪽
# 선수 지표 공격력·수비력 칸(65 M9 · G8) — 다른 기준 화면 코드에서 읽은 값(ROADMAP "G8 색 규칙"). 어두운 화면은 바탕에서
# 끝 색까지 직선, 밝은 화면은 값 ÷ G8_LIGHT_SPAN 으로 채널마다 따로 — 두 식이 다르다(기준 화면 그대로)
G8_END = {"atk": "#c63444", "def": "#3062cd"}   # rgb(198,52,68) · rgb(48,98,205)
G8_LIGHT_SPAN = 900
# 95 ③ — 기준 화면과 똑같이(사용자 2026-11-10). 67 에서 대비 때문에 바꿨던 바탕·글자를 되돌렸다 — 값은 같은 날 그 화면의
# 계산된 칸 색 36칸(두 테마)을 읽어 확인했다. 대비 4.5 미달 칸(밝은 공격 끝 흰 글자 등)은 사용자 결정으로 받아들인다.
G8_DARK_BASE = "#171d28"      # rgb(23,29,40) — 어두운 칸 바탕(아래 끝 밑도 이 색으로 칠한다)
G8_DARK_TEXT = "#f2f6f1"
G8_DARK_DIM = "#9aa79d"       # 아래 끝 밑 글자
G8_LIGHT_TEXT = ("#000000", "#ffffff")   # 값 > G8_LIGHT_WHITE_FROM 이면 흰색
G8_LIGHT_WHITE_FROM = 700
POS_COLORS = {"FW": _P["POS_FW"], "MF": _P["POS_MF"], "DF": _P["POS_DF"], "GK": _P["POS_GK"]}

FONT_FAMILY = "Malgun Gothic"  # 한글이 대부분이라 명시 — 미지정이면 플랫폼 따라 들쭉날쭉
BASE_FONT_PX = 16
# 글씨 크기 단계 — 78 ② 15→16px 때 옛 값 × 16/15 반올림 · 이 밖에서 숫자로 박지 않는다(test_rules)
# 줄임 하한(FLOOR)은 안 키운다 — 좁을 때 넘침을 막는 바닥이라, 올렸더니 1280×720 에서 선수 지표 표 2px ·
# 승부차기 GK 카드 넷 52px 가 넘쳤다(78 ② offscreen 실측). 읽히는 한계는 기본 크기와 상관없다.
PX_FLOOR = 9            # 표·경기 카드 축소 하한
PX_FLOOR_BADGE = 10     # 선수 칩 축소 하한 · 특성 칸 글자 기본
PX_XS = 12           # 옛 11
PX_S = 13            # 옛 12
PX_M = 14            # 옛 13
PX_L = 15            # 옛 14
PX_XL = 19           # 옛 18
PX_XXL = 23          # 옛 22
PT_XS = 10           # 옛 9
PT_S = 12            # 옛 11
PT_M = 13            # 옛 12
PT_L = 14            # 옛 13
PT_XL = 15           # 옛 14
PT_2XL = 17          # 옛 16
PT_3XL = 19          # 옛 18
PT_4XL = 20          # 옛 19
PT_5XL = 23          # 옛 22
PT_TITLE = 32        # 옛 30
PT_FLOOR = 8            # 승부차기 GK 이름 하한
SHOOTOUT_GK_NAME_PT = (PT_S, PT_FLOOR)   # 이름 글꼴(기본 · 줄여도 되는 하한) — 긴 이름이 카드 폭을 밀지 않게
TABLE_CELL_PX = PX_L       # 표 칸 글씨(옛 14)
TABLE_HEADER_PX = PX_M     # 표 머리 글씨(옛 13)
# 모서리(86 A1) — 카드는 크게, 그 안에 든 조작 부품은 작게(안쪽이 바깥보다 작아야 겹친 모서리가 어색하지 않다).
RADIUS_CARD = 12        # 카드·묶음·탭 판·떠 있는 박스
RADIUS_CTRL = 8         # 입력·버튼·콤보·표·메뉴 줄·카드 안 타일·줄 카드
FOCUS_RING_W = 2        # Tab 으로 온 포커스 테두리 굵기(96 K3 · widgets.FocusRing — 색은 ACCENT · 글 칸은 QSS :focus 가 따로)
# 알약(86 A2) — QSS 는 모서리가 높이 절반을 넘으면 그 값을 버리고 각지게 그린다. 칩마다 높이가 달라(태그 23 · 칩 27 ·
# 전환 단추 25 — offscreen 실측) 상수 하나로는 안 맞는다 → widgets.pill_radius 가 제 높이를 재 고정하고 그 절반을 쓴다.
BAR_RADIUS = 4          # 그래프·목록 막대 끝(86 A3 — 숫자로 박던 3·4 를 하나로) · 도넛 끝은 안 둥글린다(작은 조각·2px 틈)
# 글자를 위에 겹쳐 쓰는 막대(승부처·시간대 등) — 원색이면 글자가 묻힌다.
WIN_BAR = _P["WIN_BAR"]
LOSE_BAR = _P["LOSE_BAR"]
ROW_TINT = _P["ROW_TINT"]       # 승/패 행 배경 — PANEL 에 섞는 비율

# 표본이 모자란 막대 — 원래 색을 바탕 쪽으로 이만큼만 섞는다(불투명). 색만으로 구분하지 않게
# 쓰는 쪽이 글자 "표본 N" 도 같이 붙인다(widgets.wdl_rate_bar · charts.HBarList).
WEAK_MIX = 0.35


def blend(base_hex: str, target_hex: str, mix: float) -> str:
    """base 에서 target 쪽으로 mix(0~1)만큼 간 불투명 색 — '#rrggbb'.

    반투명(alpha) 대신 이걸 쓴다: 표의 교차 행 색 위에 알파를 얹으면 값이 같아도 행마다
    진하기가 달라 보였다(CLAUDE.md PyQt 규칙 2번)."""
    b = [int(base_hex[i:i + 2], 16) for i in (1, 3, 5)]
    t = [int(target_hex[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * mix):02x}" for x, y in zip(b, t))


ROW_FILL_MIX = 0.12                 # 지표 줄 바탕 채움(67 R0 — 점유율만큼) · 0.22 이면 밝은 쪽 흐린 글자 4.34(미달 — test_palette_text_contrast · 초록 채움 3.2.2)
ROW_FILL = blend(PANEL, ACCENT_FILL, ROW_FILL_MIX)
# 빈 화면 아이콘 선(86 C4) — 글자보다 한참 흐리게(아이콘은 장식이고 읽을 것은 문구다)
EMPTY_ICON = blend(PANEL, TEXT_DIM, 0.45)
# 눌러서 들어가는 카드에 마우스를 올렸을 때 테두리(86 C2) — 강조색 그대로면 버튼과 구분이 안 돼 반쯤만
CARD_HOVER_BORDER = blend(CARD_BORDER, ACCENT_FILL, 0.55)


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


def g8_cell(v: float, kind: str, scale: tuple[float, float], mode: str = MODE) -> tuple[str | None, str]:
    """선수 지표 공격력(kind "atk")·수비력("def") 칸 → (배경 '#rrggbb' — None 이면 안 칠함, 글자색). 표 안 값과 무관한 고정 눈금.
    기준 화면과 똑같이(95 ③).

    어두운: 값 < 아래 끝 → G8_DARK_BASE + 흐린 글자 · 그 위 t = min((값 − 아래) / (위 − 아래), 1) 만큼 G8_DARK_BASE 에서
    G8_END 로(채널마다 반올림). 밝은: 값 < 아래 끝 → 안 칠함(흰 카드) + 검정 · 그 위는 값 ÷ G8_LIGHT_SPAN 식 · 글자는
    값 > 700 흰색, 아니면 검정."""
    lo, hi = scale
    if mode == "dark":
        if v < lo:
            return G8_DARK_BASE, G8_DARK_DIM
        t = min((v - lo) / (hi - lo), 1.0)
        b, e = G8_DARK_BASE, G8_END[kind]
        rgb = [int(b[i:i + 2], 16) + (int(e[i:i + 2], 16) - int(b[i:i + 2], 16)) * t for i in (1, 3, 5)]
        return "#" + "".join(f"{int(c + 0.5):02x}" for c in rgb), G8_DARK_TEXT   # 0.5 는 올림(round() 는 짝수 쪽 — 기준 화면과 1 어긋난다)
    if v < lo:
        return None, G8_LIGHT_TEXT[0]
    # 공격 R 은 기준 화면 식 그대로 "200 + 값/900·255/55" — 200~205 에 머문다(67 에서 깨진 식으로 읽어 ·55 로 고쳤던 것을
    # 95 ③ 에 그 화면 칸 색으로 확인: 912.8 → rgb(205,74,100) · 213.6 → rgb(201,213,219))
    f = v / G8_LIGHT_SPAN
    rgb = ((200 + f * 255 / 55, 255 - f * 178.5, 255 - f * 153) if kind == "atk"
           else (255 - f * 255, 255 - f * 255, 200 + f * 55))
    bg = "#" + "".join(f"{int(max(0.0, min(c, 255.0)) + 0.5):02x}" for c in rgb)
    return bg, G8_LIGHT_TEXT[1 if v > G8_LIGHT_WHITE_FROM else 0]


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
PITCH = "#1e5c34"                    # 슛 맵 판 — 게임 잔디(아래)는 밝아 골 점이 2.4~3.0 으로 묻혀 그대로(95)
# 선수 카드 축구장(PitchWidget) — 게임 스쿼드 관리 화면 잔디 두 줄무늬(95 · 스크린숏 픽셀). 카드가 불투명이라 밝아도 된다
PITCH_GRASS = ("#487c20", "#648c2c")
PITCH_STRIPES = 8                    # 가로 줄무늬 수(게임 화면 원근판의 띠를 평평하게 셈)
PITCH_GOAL = "#5ee08f"
PITCH_MISS = "#c3c9d4"
TIMELINE_SHOT_ALPHA = 0.75   # 경기 흐름 줄의 막힌 유효슛 점 진하기(88 P2 — 골은 불투명 · 색은 내 몫 ACCENT_FILL·상대 몫 OPP_FILL 그대로)
TIMELINE_MISS_ALPHA = 0.45   # 빗나간 슛 점 — 유효슛보다 흐리게(0.3 은 어두운 바탕에서 거의 안 보였다 — 11-09 그림)
PITCH_ASSIST = "#8ec9ff"   # 어시스트 위치 → 슛 선(슛 맵 [어시스트]) — 골 초록·빗나감 회색과 갈리게 파랑
SIDEBAR_W = 230
SCROLL = _P["SCROLL"]
SCROLL_HOVER = _P["SCROLL_HOVER"]

# "적용"/"불러오기" 류 강조 버튼(윤곽선만, 채우지 않음) — 3곳 이상에서 반복 사용.
OUTLINE_BUTTON_QSS = f"""
QPushButton {{ background: transparent; color: {ACCENT};
    border: 1px solid {ACCENT}; border-radius: {RADIUS_CTRL}px; padding: 6px 16px;
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
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#card[hot="true"] {{ border-color: {CARD_HOVER_BORDER}; }}
QLabel#cardTitle {{ font-size: {PX_XL}px; font-weight: bold; color: {TEXT}; }}
QFrame#topbar {{
    background: {PANEL};
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_CARD}px;
}}

QFrame#sidebar {{
    background: {PANEL};
    border: none;
    border-right: 1px solid {BORDER};
}}
QLabel#brand {{ color: {ACCENT}; font-size: {PX_XXL}px; font-weight: bold; }}
QLabel#brandSub {{ color: {TEXT_DIM}; font-size: {PX_S}px; }}
QListWidget#nav {{
    background: transparent;
    border: none;
    outline: none;
    font-size: {PX_L}px;
}}
QListWidget#nav::item {{
    padding: 5px 12px;
    margin: 0 10px;
    border-radius: {RADIUS_CTRL}px;
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
    border-radius: {RADIUS_CTRL}px;
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLineEdit {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CTRL}px;
    padding: 7px 12px;
    color: {TEXT};
    selection-background-color: {PRIMARY};
    selection-color: {ON_PRIMARY};
}}
QLineEdit:focus {{ border: 2px solid {ACCENT}; }}
QLineEdit#bigSearch {{ border: 2px solid {ACCENT}; border-radius: {RADIUS_CTRL}px; }}

QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CTRL}px;
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
    border-radius: {RADIUS_CTRL}px;
    padding: 5px 16px;
    color: {TEXT_DIM};
}}
QPushButton#pageTab:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#pageTab:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_CARD}px;
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
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_CARD}px;
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: {PANEL_2};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: {RADIUS_CTRL}px;
    border-top-right-radius: {RADIUS_CTRL}px;
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
    border-radius: {RADIUS_CTRL}px;
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
}}
QTableCornerButton::section {{ background: {PANEL_2}; border: none; }}

QComboBox, QSpinBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CTRL}px;
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


# 75 — 플랫폼이 클래스마다 따로 주는 글꼴(Qt 의 클래스 글꼴 표 — qapplication.cpp initializeWidgetFontHash 의 이름들).
# 윈도우는 목록(QAbstractItemView)·상태 줄·말풍선 등에 맑은 고딕 9pt(=12px)를 준다. QApplication.setFont(글꼴) 만으로는
# 이 표가 안 바뀌어 줄 카드 목록이 BASE_FONT_PX 가 아니라 12px 로 그려졌다(2026-11-02 실제 창 촬영 — offscreen 엔 이 표가
# 비어 있어 스모크가 못 잡는다). 그래서 이 이름들에 앱 글꼴을 다시 건다.
PLATFORM_FONT_CLASSES = (
    "QMenu", "QMenuBar", "QMenuItem", "QMessageBox", "QLabel", "QTipLabel", "QStatusBar",
    "QMdiSubWindowTitleBar", "QDockWidgetTitle", "QPushButton", "QCheckBox", "QRadioButton", "QToolButton",
    "QAbstractItemView", "QListView", "QHeaderView", "QListBox", "QComboMenuItem", "QComboLineEdit",
)


def unify_class_fonts(app, font) -> None:
    """PLATFORM_FONT_CLASSES 전부에 font — 위젯이 setFont 로 직접 준 글꼴은 이것보다 앞선다(표 칸 크기 등은 그대로)."""
    for name in PLATFORM_FONT_CLASSES:
        app.setFont(font, name)


def apply(app) -> None:
    """앱 전체에 테마를 건다.

    Qt 6.5+ 의 windows 스타일은 OS 다크 모드를 따라 기본 팔레트를 바꾼다.
    위 QSS 는 일반 위젯 배경을 일부러 비워 두므로, 그대로 두면 OS 설정과 MODE 가
    다른 PC 에서 군데군데 다른 색이 비친다 — Fusion + MODE 팔레트로 고정한다.
    색 체계도 MODE 로 맞춘다 — 안 맞추면 OS 설정을 따라 제목 표시줄만 다른 색이 된다.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPalette

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
    # 맨 끝에서 — 스타일(시트)을 거는 동안 Qt 가 클래스 글꼴 표를 플랫폼 값으로 다시 채운다. setFont 바로 뒤에 두었더니
    # 실제 창 경기 목록이 그대로 12px 였다(2026-11-02 실측 — 고침 있음/없음이 같았다).
    unify_class_fonts(app, font)
