"""라인업으로 켜진 팀컬러(113 N29 · K2 · K4) — 계산만(Qt·DB·네트워크 없음 · 화면은 `core_api`).

경기 기록에는 팀컬러가 없어 선발 11장의 카드 → 팀컬러(카드 백필 `card_extra`)와 팀컬러 → 단계별 최소 인원(`teamcolor_steps`)으로 센다.

| 무엇 | 규칙 |
|---|---|
| 종류 | 소속("club")과 관계("rel")를 따로 배분한다 — 넥슨 능력치 시뮬레이터의 칸이 따로다(`playerinfo` n4TeamColorId / _Feature). 강화 팀컬러는 카드가 몇 강부터 드는지 자료가 없어 뺀다 |
| 배분 | 같은 종류 안에서 카드 하나는 팀컬러 하나에만 · 인원 많은 것 → 단계 높은 것 → id 작은 것. 주 팀컬러(소속 첫째)는 51 측정 B 에서 순위표와 98.5% 맞았다. 관계는 확인할 자료가 없다 |
| 모름(카드) | 시뮬레이터를 아직 안 읽은 카드(`sim_on` 없음 · 깨진 JSON). 빈 목록으로 치지 않는다 |
| 모름(단계) | 이름표에 있는데 단계표가 없거나 이름표 밖인 id 를 3장 이상이 가졌다 — 주 팀컬러가 그것일 수 있다(측정 B 의 관문과 같은 정의 · 종류별). "단일팀"·"단일국가"는 상세가 없는 칸이라 뺀다 |
| 이름표가 비었다 | 전부 모름(2회차 N2) — 안 그러면 "팀컬러 없음"이 거짓으로 나온다 |
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import cardstats
import config
import matchtime
from models import RateMixin
from stats import _me_opp, _set_mixed, draw_possible_of, result_of

CLUB, REL = "club", "rel"
GENERIC = frozenset({"단일팀", "단일국가"})   # 카드마다 붙는 자리 표시 — 목록에도 상세에도 없다(2026-11-23 실측 4,649칸 전부 이 둘)
NONE_ON = "소속 없음"                             # 다 아는데 켜진 소속 팀컬러가 없는 판(모름과 다르다 · 2회차 N4)
_UNKNOWN_MIN = 3                             # 단계를 모르는 팀컬러를 이만큼 가지면 그 종류는 모름(첫 단계 인원의 최솟값 근처)


def greedy_cards(pool: dict[int, set[int]], need: dict[int, list[int]]) -> list[tuple[int, int, int, tuple[int, ...]]]:
    """카드 하나는 팀컬러 하나에만 — 적용 인원 많은 것부터(같으면 단계 높은 것 · 같으면 id 작은 것) 차례로 카드를 가져간다.
    pool: 카드 → 걸 수 있는 팀컬러 id · need: id → 단계별 최소 인원(오름차순). → [(id, 인원, 단계, 적용 카드)]."""
    left = {c: set(ts) for c, ts in pool.items()}
    out = []
    while True:
        cnt: Counter = Counter()
        for ts in left.values():
            cnt.update(ts)
        best = None
        for tid, m in cnt.items():
            steps = need.get(tid)
            if not steps:
                continue
            step = sum(1 for s in steps if m >= s)
            if step and (best is None or (m, step, -tid) > (best[1], best[2], -best[0])):
                best = (tid, m, step)
        if best is None:
            return out
        out.append((*best, tuple(c for c, ts in left.items() if best[0] in ts)))
        left = {c: ts for c, ts in left.items() if best[0] not in ts}


def greedy(pool: dict[int, set[int]], need: dict[int, list[int]]) -> list[tuple[int, int, int]]:
    """`greedy_cards` 의 앞 세 칸 — 측정 도구(`tools/measure_cards.py`)가 같은 식을 쓴다."""
    return [x[:3] for x in greedy_cards(pool, need)]


@dataclass(frozen=True)
class Active:
    id: int
    kind: str                 # CLUB · REL
    members: int
    step: int
    idx: tuple[int, ...]      # 적용 카드의 자리 번호(넘긴 cards 순서)


@dataclass(frozen=True)
class Lineup:
    actives: tuple[Active, ...] = ()
    unknown: int = 0                 # 카드 정보를 모르는 장수
    steps_unknown_club: bool = False
    steps_unknown_rel: bool = False

    @property
    def main(self) -> Active | None:
        """주 팀컬러 — 소속 중 인원이 가장 많은 것."""
        return next((a for a in self.actives if a.kind == CLUB), None)

    @property
    def club_known(self) -> bool:
        return not self.unknown and not self.steps_unknown_club


@dataclass(frozen=True)
class Material:
    """화면이 한 번 읽어 넘기는 재료 — cards: spid → (소속 id 들, 관계 id 들) 또는 None(모름)."""
    cards: dict[int, tuple[tuple[int, ...], tuple[int, ...]] | None] = field(default_factory=dict)
    need: dict[int, list[int]] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)


def card_colors(extra: dict | None) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """card_extra 줄 → (소속, 관계) 팀컬러 id. 시뮬레이터를 안 읽었거나 깨졌으면 None."""
    if not extra or not extra.get("sim_on"):
        return None
    out = []
    for col in ("club_tc", "rel_tc"):
        try:
            items = json.loads(extra.get(col) or "[]")
        except ValueError:
            return None
        if not isinstance(items, list):
            return None
        out.append(tuple(it[0] for it in items if isinstance(it, list) and it and isinstance(it[0], int)
                         and not (len(it) > 1 and it[1] in GENERIC)))
    return out[0], out[1]


def material(extras: dict[int, dict], steps, meta) -> Material:
    """extras: `store.load_card_extras` · steps: `store.load_step_members` [(id, 단계, 인원)] · meta: [{id, name, …}]."""
    names = {m["id"]: m["name"] for m in meta}
    need: dict[int, list[int]] = {}
    for tid, _step, members in steps:
        if tid in names and isinstance(members, int):
            need.setdefault(tid, []).append(members)
    return Material({s: card_colors(x) for s, x in extras.items()}, {k: sorted(v) for k, v in need.items()}, names)


def active(cards, need: dict[int, list[int]], names) -> Lineup:
    """cards: 선발 카드마다 `card_colors` 값. names: 이름표에 있는 id(담겼는지만 본다)."""
    cards = list(cards)
    if not names:
        return Lineup(unknown=len(cards))
    out: list[Active] = []
    flags = []
    for k, kind in ((0, CLUB), (1, REL)):
        pool = {i: {t for t in c[k] if t in names} for i, c in enumerate(cards) if c is not None}
        seen = Counter(t for c in cards if c is not None for t in set(c[k]))
        flags.append(any(n >= _UNKNOWN_MIN and t not in need for t, n in seen.items()))
        out += [Active(t, kind, m, s, idx) for t, m, s, idx in greedy_cards(pool, need)]
    return Lineup(tuple(out), sum(c is None for c in cards), flags[0], flags[1])


# ── 경기 기록 → 판마다 켜진 소속 팀컬러(K2 · K4) ────────────────────────────────

def lineup_spids(side: dict) -> tuple[int, ...] | None:
    """선발 11장의 카드 번호(정렬) — 11장이 아니면 None."""
    ids = [p.get("spId") for p in cardstats.starters(side)]
    return tuple(sorted(ids)) if len(ids) == 11 and all(isinstance(i, int) for i in ids) else None


def my_lineups(details: list[dict], ouid: str) -> dict[str, tuple[int, ...]]:
    """경기 번호 → 내 선발 11장 — 11장이 아닌 판은 열쇠가 없다. 아래 셈들이 같이 쓴다(1만 5천 판에 0.1초라 한 번만)."""
    out: dict[str, tuple[int, ...]] = {}
    for d in details:
        me, _opp = _me_opp(d, ouid)
        ids = lineup_spids(me) if me is not None else None
        if ids is not None:
            out[d.get("matchId")] = ids
    return out


class _Memo:
    """같은 라인업은 한 번만 센다(1만 5천 판이 서로 다른 라인업 45개였다)."""

    def __init__(self, mat: Material):
        self.mat, self.memo = mat, {}

    def get(self, ids: tuple[int, ...]) -> Lineup:
        got = self.memo.get(ids)
        if got is None:
            m = self.mat
            got = self.memo[ids] = active([m.cards.get(i) for i in ids], m.need, m.names)
        return got


def club_values(lineups: dict[str, tuple[int, ...]], mat: Material) -> dict[str, str | None]:
    """K4 — 경기 번호 → 주 팀컬러 이름 · `NONE_ON` · None(모름). 선발이 11장이 아닌 판은 열쇠가 없다."""
    memo, out = _Memo(mat), {}
    for mid, ids in lineups.items():
        lu = memo.get(ids)
        out[mid] = None if not lu.club_known else (mat.names[lu.main.id] if lu.main else NONE_ON)
    return out


@dataclass
class ComboRow(RateMixin):
    """K2 — 켜진 소속 팀컬러 묶음 하나의 성적."""
    ids: tuple[int, ...]
    label: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    mixed: bool | None = None
    first: datetime | None = None
    last: datetime | None = None


@dataclass(frozen=True)
class ComboTable:
    rows: list
    known: int             # 팀컬러를 아는 판
    total: int             # 선발이 11장인 판
    steps_only: int = 0    # 카드는 다 아는데 단계를 모르는 팀컬러 때문에 뺀 판


def combo_rows(details: list[dict], ouid: str, lineups: dict[str, tuple[int, ...]], mat: Material,
               mixed: bool | None = None) -> ComboTable:
    """lineups 는 `my_lineups`(표시한 판도 들어 있다 — 거르기는 details 쪽에서). 켜진 소속 팀컬러 묶음마다 승·무·패 — 판 많은 순. 모르는 판은 세지 않는다(빈 칸을 채우지 않는다)."""
    memo = _Memo(mat)
    acc: dict[tuple[int, ...], ComboRow] = {}
    orders: dict[tuple[int, ...], Counter] = {}
    flags, total, known, steps_only = [], 0, 0, 0
    for d in details:
        ids = lineups.get(d.get("matchId"))
        if ids is None:
            continue
        total += 1
        lu = memo.get(ids)
        if not lu.club_known:
            steps_only += not lu.unknown
            continue
        known += 1
        flags.append(draw_possible_of(d, ouid))
        clubs = tuple(a.id for a in lu.actives if a.kind == CLUB)
        key = tuple(sorted(clubs))   # 에이 6·비 5 와 비 6·에이 5 는 같은 묶음 — 이름 순서는 더 많이 쓴 쪽으로
        row = acc.get(key)
        if row is None:
            row = acc[key] = ComboRow(clubs, "")
        orders.setdefault(key, Counter())[clubs] += 1
        row.games += 1
        res = result_of(*_me_opp(d, ouid))
        if "승" in res:
            row.win += 1
        elif "무" in res:
            row.draw += 1
        elif "패" in res:
            row.lose += 1
        at = matchtime.parse(d.get("matchDate"))
        if at is not None:
            row.first = at if row.first is None or at < row.first else row.first
            row.last = at if row.last is None or at > row.last else row.last
    for key, row in acc.items():
        row.ids = max(orders[key].items(), key=lambda kv: (kv[1], kv[0]))[0]
        row.label = " · ".join(mat.names[i] for i in row.ids) or NONE_ON
    _set_mixed(acc.values(), flags, mixed)
    return ComboTable(sorted(acc.values(), key=lambda r: (-r.games, r.label)), known, total, steps_only)


# ── 경기 상세 한 쪽(N29) ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SideColors:
    """경기 상세 한 쪽 — state: "ok" · "lineup"(선발 11장이 아님) · "no_meta"(팀컬러 이름표를 아직 못 받았다) · "unknown"(모르는 카드가 많다)."""
    state: str
    actives: tuple[Active, ...] = ()
    spids: tuple[int, ...] = ()      # Active.idx 가 가리키는 카드
    unknown: int = 0
    steps_unknown: tuple[str, ...] = ()   # 단계를 모르는 팀컬러가 있는 종류


def side_colors(side: dict, mat: Material) -> SideColors:
    ids = lineup_spids(side)
    if ids is None:
        return SideColors("lineup")
    if not mat.names:
        return SideColors("no_meta", spids=ids)
    lu = active([mat.cards.get(i) for i in ids], mat.need, mat.names)
    steps = tuple(k for k, f in ((CLUB, lu.steps_unknown_club), (REL, lu.steps_unknown_rel)) if f)
    if lu.unknown >= config.TC_UNKNOWN_HIDE:
        return SideColors("unknown", spids=ids, unknown=lu.unknown, steps_unknown=steps)
    return SideColors("ok", lu.actives, ids, lu.unknown, steps)
