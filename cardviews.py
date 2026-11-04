"""카드 정보로 만드는 화면 재료(68 일부) — 계산만(화면은 `core_api`). 선수 지표의 실효 OVR·키·체형 · 내 스쿼드 OVR.

**`cardstats.py` 에 안 넣은 이유**: 51 측정(`tools/measure_cards.py`)이 그 파일의 해시를 D0 값과 견준다(`TOOL_FILES`) —
측정이 끝나기 전에 고치면 판정에 "도구 바뀜"이 남는다. 식은 `cardstats` 것을 부르기만 해서 측정과 어긋나지 않는다.
측정이 끝나면(11-26 뒤) 합쳐도 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cardstats


@dataclass(frozen=True)
class PlayerCard:
    """선수 지표 한 줄의 카드 정보 — 모르면 None."""
    ovr: int | None = None
    height: int | None = None
    body: str | None = None


@dataclass(frozen=True)
class SquadOvr:
    """내 선발 11장 실효 OVR(한 판 평균)의 범위 평균·변동. games = 내 경기 · known = 11장을 다 아는 경기."""
    games: int = 0
    known: int = 0
    avg: float | None = None
    sd: float | None = None     # 표본 표준편차 — 2판부터


def player_card(sp_id: int, pos_code, grade, extras: dict[int, dict]) -> PlayerCard:
    """pos_code·grade = 그 줄에서 가장 많이 선 자리·쓴 강화(`stats.PlayerStat`). 팀컬러·적응도는 안 넣는다(51 U3)."""
    extra = extras.get(sp_id)
    if extra is None:
        return PlayerCard()
    ovr, _why = cardstats.effective_ovr(pos_code, grade, extra)
    return PlayerCard(ovr, extra.get("height") or None, extra.get("body") or None)


def _my_side(detail: dict, ouid: str) -> dict | None:
    return next((p for p in detail.get("matchInfo") or [] if p.get("ouid") == ouid), None)


def lineup_cards(details: list[dict], ouid: str) -> set[int]:
    """이 경기들에서 내가 선발로 쓴 카드 — 카드 정보를 이만큼만 읽는다."""
    out: set[int] = set()
    for d in details:
        me = _my_side(d, ouid)
        if me is not None:
            out.update(p["spId"] for p in cardstats.starters(me) if isinstance(p.get("spId"), int))
    return out


def squad_ovr(details: list[dict], ouid: str, extras: dict[int, dict]) -> SquadOvr:
    """한 판의 값은 `cardstats.lineup_strength` 와 같은 식(11장 평균 · 한 장이라도 모르면 그 판은 뺀다).
    그쪽은 카드마다 JSON 을 다시 읽어 1만 판이면 1초가 넘는다 — 같은 (자리, 강화, 카드)는 한 번만 잰다."""
    memo: dict[tuple, int | None] = {}
    values: list[float] = []
    games = 0
    for d in details:
        me = _my_side(d, ouid)
        if me is None:
            continue
        games += 1
        ps = cardstats.starters(me)
        if len(ps) != 11:
            continue
        total = 0
        for p in ps:
            key = (p.get("spPosition"), p.get("spGrade"), p.get("spId"))
            if key not in memo:
                memo[key] = cardstats.effective_ovr(key[0], key[1], extras.get(key[2]))[0]
            if memo[key] is None:
                break
            total += memo[key]
        else:
            values.append(total / 11)
    if not values:
        return SquadOvr(games=games)
    avg = sum(values) / len(values)
    sd = (sum((v - avg) ** 2 for v in values) / (len(values) - 1)) ** 0.5 if len(values) > 1 else None
    return SquadOvr(games, len(values), avg, sd)
