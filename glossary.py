"""용어 설명(96단계 T2) — 화면에 나오는 용어의 정의 문장은 **이 표 하나에만** 둔다.

말풍선은 `tip(key)`, 산출 방법 페이지(`methods.py`)는 같은 표에서 용어마다 머리 한 줄을 읽는다. 정의 문장을 다른 소스에
문자열로 다시 쓰면 `test_rules` 가 잡는다. 같은 말이 두 곳에 있으면 한쪽만 고쳐져 화면마다 뜻이 갈린다.
수치가 들어가는 긴 설명은 `methods.sections` 에 그대로 둔다(여기는 한 문장짜리 정의만).
"""
from __future__ import annotations

# 키 → (화면 이름, 정의 문장). 순서가 산출 방법 페이지 [용어] 의 순서다.
TERMS: dict[str, tuple[str, str]] = {
    "xg": ("기대득점(xG)",
           "슛 하나가 골이 될 확률을 슛 위치와 슛 종류로 어림한 값이고, 여러 슛이면 그 합입니다."),
    "elo": ("ELO(랭킹 점수)",
            "넥슨 데이터센터 감독모드 랭킹의 점수로, 이기면 오르고 지면 내려갑니다."),
    "percentile": ("백분위",
                   "같은 무리 100명 가운데 내 값보다 낮은 사람이 몇 명인지로 나타낸 자리입니다."),
    "sample": ("표본",
               "값을 낼 때 쓴 경기 수이고, 적을수록 값이 우연에 크게 흔들립니다."),
    "fc": ("FC",
           "경기에서 이기면 그 경기의 등급에 따라 받는 승리 보상입니다."),
    "club_value": ("구단가치",
                   "넥슨 데이터센터가 적은 구단 보유 카드 시세의 합으로, 실제로 뛴 11명 시세 합과는 다릅니다."),
    "effective_ovr": ("실효 OVR",
                      "그 자리에 섰을 때의 능력치에 강화 보너스를 더한 값입니다."),
    # 97 — 흐름·비교 분석
    "session": ("세션",
                "경기를 이어서 한 묶음으로 본 것이고, 한참 쉬거나 날이 바뀌면 끊습니다."),
    "play_time": ("경기를 돌린 시간(추정)",
                  "세션마다 첫 경기 시작부터 마지막 경기가 끝날 때까지를 어림한 시간이고, 화면 앞에 앉아 있던 시간과는 다릅니다."),
    "elapsed_days": ("경과 일수 비교",
                     "시즌이 시작된 뒤 같은 날수까지만 잘라서, 지금 시즌과 지난 시즌을 같은 조건으로 견준 것입니다."),
    "after_losses": ("연패 뒤 다음 판",
                     "연패가 끝난 바로 다음 경기의 결과이고, 이겼으면 끊음, 비기거나 졌으면 이어짐으로 셉니다."),
    "dist_compare": ("분포 대비",
                     "상위 구단주 표본이 같은 경기 상대와 견준 값의 분포 안에서 내 값이 어디쯤인지 본 것입니다. "
                     "내 층 평균은 등급대가 아니라 내 순위와 같은 순위 구간 표본의 평균입니다."),
}


# 표 머리글 이름 → 용어 키. `app_main._make_table` 이 모든 표에서 이 이름의 머리글에 말풍선을 단다
# (표마다 손으로 달면 빠지는 표가 생긴다). 그 표에서만 맞는 설명이 따로 있으면 표가 나중에 덮어쓴다.
HEADERS: dict[str, str] = {
    "xG": "xg", "ELO": "elo", "평균 ELO": "elo", "백분위": "percentile",
    "FC 합계": "fc", "실효 OVR": "effective_ovr", "구단가치": "club_value", "구단가치 중앙값": "club_value",
    "내 층 평균": "dist_compare",
}


def header_tip(column: str) -> str:
    key = HEADERS.get(column)
    return tip(key) if key else ""


def name(key: str) -> str:
    return TERMS[key][0]


def tip(key: str, extra: str = "") -> str:
    """말풍선 글 — 정의 문장 + (있으면) 그 화면에서만 맞는 덧붙임."""
    text = TERMS[key][1]
    return f"{text} {extra}" if extra else text


def head_html(key: str) -> str:
    """산출 방법 페이지의 용어 머리 한 줄."""
    title, text = TERMS[key]
    return f"<p><b>{title}</b>: {text}</p>"
