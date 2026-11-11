"""스쿼드 타임라인(1.4.1 · 12단계) — 경기 기록 + (내 계정일 때만) 거래 기록 → 카드가 들고 난 사건들.

규칙은 ROADMAP "스쿼드 타임라인" 절. 함정만 여기 적는다:

| 무엇 | 왜 |
|---|---|
| 출전은 **선발만**(교체 명단 spPosition 28 은 안 센다) | 사용자 결정(2026-10-06). 교체 명단은 평점이 늘 0 이라 뛰었는지도 모른다 |
| 짝(구매↔판매)은 **같은 spid 끼리 선입선출**, 전체 이력으로 | 강화는 산 뒤 바뀔 수 있어 grade 는 짝 기준이 아니다. 범위로 먼저 자르면 지난 시즌 산 카드가 "취득가 없음"이 된다 |
| 같은 시각이면 구매를 판매보다 먼저 | 판매일 ≥ 구매일인 구매하고만 짝짓는다 — 같은 초는 짝이 될 수 있게 |
| 보유 중은 spid 마다 **한 장**(최근 출전보다 앞선 가장 최근 구매) | 스쿼드엔 한 장 — 나머지는 강화 재료일 수 있어 평가가 두 배가 된다(검토 3회차) |
| 사건 표에는 **출전한 카드**의 거래만 | 키 주인 거래가 1.6만 줄이고 대부분 출전 안 한 카드다(되팔기·팩 정리) — 그건 가계부가 맡는다 |


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
스쿼드 타임라인 · 이적시장 가계부(1.4.1 · 12단계) — 화면 없이 계산만, 화면은 `core_api` 로.
경기(선발만 — 교체 명단 28 은 출전 아님) + 거래 → 사건(구매·판매·첫/마지막 출전·강화 변화) · 앞뒤 `TIMELINE_WINDOW` 경기 ·
구매↔판매 **같은 spid 선입선출, 전체 이력으로 짝 맞춘 뒤 범위로 자른다** · 짝 없는 구매의 상태(보유 중 → 최근 구매 → 미출전 → 최근 경기에 안 씀, 위에서부터 먼저 맞는 것 ·
보유는 spid 마다 한 장). **같은 초는 "산 뒤 썼다"**, 산 지 정확히 `HOLD_GRACE_DAYS` 일이면 아직 최근 구매(경계 테스트가 지킨다).
거래는 **내 계정으로 정했을 때만** 붙인다 — 판정은 `app_main._timeline_for_screen` 한 곳(가계부·타임라인이 같이 씀, 같은 입력이면 다시 안 계산).
거래가 바뀌는 길은 `_invalidate_trades`(가계부·타임라인 둘 다). 시세(`PriceLoader`)는 가계부를 그릴 때 하루 한 번 · 보유·최근 구매만 · 거래 받기가 돌면 그 뒤
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import NamedTuple

import config
import habits
import matchtime
from models import RateMixin, draw_possible, is_mixed, is_weak, mixed_of_details
from stats import formation_of
from store import PLAYER_ID_MOD

SUB_POSITION = 28  # 교체 명단 — 출전으로 안 센다

KIND_BUY, KIND_SELL, KIND_FIRST, KIND_LAST, KIND_GRADE = "buy", "sell", "first", "last", "grade"

# 짝 안 맞은 구매의 상태 — 위에서부터 먼저 맞는 것(ROADMAP 2회차)
HELD, RECENT, NEVER_PLAYED, NOT_RECENT = 1, 2, 3, 4


class Trade(NamedTuple):
    kind: str          # "buy" | "sell"
    sale_sn: str
    date: datetime
    spid: int
    grade: int | None
    value: int | None  # BP


@dataclass(frozen=True)
class Window(RateMixin):
    """사건 앞이나 뒤 경기 묶음의 성적. mixed 는 타임라인 하나 전체로 한 번 정한 식(모든 창이 같은 값)."""
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    gf: int = 0
    ga: int = 0
    mixed: bool | None = None

    @property
    def weak(self) -> bool:
        return is_weak(self.rated, config.TIMELINE_MIN_GAMES)


@dataclass
class TimelineEvent:
    date: datetime
    spid: int
    kind: str
    grade_from: int | None
    grade_to: int | None
    before: Window
    after: Window
    value: int | None = None   # 거래 금액(BP)
    estimated: bool = False    # 첫 출전인데 바로 앞 거래가 구매가 아니다 — 팩·보상·기록 전 구매·남의 계정


@dataclass
class Pair:
    spid: int
    buy: Trade
    sell: Trade

    @property
    def profit(self) -> int | None:
        if self.buy.value is None or self.sell.value is None:
            return None
        return self.sell.value - self.buy.value


@dataclass
class Holding:
    """판 기록이 없는 구매 한 장."""
    trade: Trade
    status: int
    cur_grade: int | None = None  # 보유 중이면 마지막 출전 강화(평가 기준)


@dataclass
class Timeline:
    events: list[TimelineEvent] = field(default_factory=list)  # 최신순
    trades: list[Trade] = field(default_factory=list)          # 시간순
    pairs: list[Pair] = field(default_factory=list)
    unmatched_sells: list[Trade] = field(default_factory=list)  # 취득가 없음(팩·보상·기록 전 구매)
    holdings: list[Holding] = field(default_factory=list)
    games_basis: int = 0        # "최근 N경기 기준" — 경기가 HOLD_RECENT_GAMES 보다 적으면 있는 만큼
    has_trades: bool = False    # 내 계정이라 거래를 붙였다


def _goals(p: dict) -> int:
    v = (p.get("shoot") or {}).get("goalTotal")
    return int(v) if isinstance(v, (int, float)) else 0


def parse_trades(rows) -> list[Trade]:
    """store.load_trades 줄 → Trade(시간순). 날짜·카드가 빠진 줄만 버린다."""
    out = []
    for r in rows:
        when, spid = matchtime.parse(r.get("trade_date")), r.get("spid")   # 거래 원문도 UTC(10-25 게임 거래 기록과 대조)
        if when is None or spid is None or r.get("kind") not in (KIND_BUY, KIND_SELL):
            continue
        out.append(Trade(r["kind"], str(r.get("sale_sn")), when, int(spid), r.get("grade"), r.get("value")))
    out.sort(key=lambda t: (t.date, t.kind != KIND_BUY))
    return out


class _Games:
    """경기(오래된 것부터) + 누적합 — 사건 앞뒤 창을 bisect 로."""

    def __init__(self, details: list[dict], ouid: str):
        rows = []
        for d in details:
            infos = d.get("matchInfo") or []
            me = next((p for p in infos if p.get("ouid") == ouid), None)
            when = matchtime.parse(d.get("matchDate"))
            if me is None or when is None:
                continue
            opp = next((p for p in infos if p.get("ouid") != ouid), {})
            res = (me.get("matchDetail") or {}).get("matchResult") or ""
            starters = [(p.get("spId"), p.get("spGrade")) for p in me.get("player") or []
                        if p.get("spId") is not None and p.get("spPosition") != SUB_POSITION]
            rows.append((when, res, _goals(me), _goals(opp), starters,
                         draw_possible(me.get("division"), opp.get("division")) if opp else None))
        rows.sort(key=lambda r: r[0])
        # 묶음 = 타임라인 하나 — 앞 대 뒤 · 사건끼리 견주므로 한 번 정해 모든 창에(누적합에 칸을 더하지 않는다)
        self.mixed = is_mixed(r[5] for r in rows)
        self.dates = [r[0] for r in rows]
        self.starters = [r[4] for r in rows]
        self._acc = [(0, 0, 0, 0, 0)]
        w = dr = lo = gf = ga = 0
        for _, res, f, a, _, _ in rows:
            if "승" in res:
                w += 1
            elif "무" in res:
                dr += 1
            elif "패" in res:
                lo += 1
            gf += f
            ga += a
            self._acc.append((w, dr, lo, gf, ga))

    def __len__(self) -> int:
        return len(self.dates)

    def window(self, lo: int, hi: int) -> Window:
        lo, hi = max(0, lo), min(len(self.dates), hi)
        if hi <= lo:
            return Window(mixed=self.mixed)
        a, b = self._acc[lo], self._acc[hi]
        return Window(hi - lo, b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3], b[4] - a[4], self.mixed)

    def around(self, when: datetime) -> tuple[Window, Window]:
        """앞 = 그 시각 전 N경기, 뒤 = 그 시각부터 N경기(출전 사건이면 그 경기 포함)."""
        n = config.TIMELINE_WINDOW
        i = bisect_left(self.dates, when)
        return self.window(i - n, i), self.window(i, i + n)


def fifo_pairs(trades: list[Trade]) -> tuple[list[Pair], list[Trade], dict[int, list[Trade]]]:
    """같은 spid 끼리 선입선출 → (짝, 짝 없는 판매, spid → 남은 구매(오래된 것부터)).
    trades 는 시간순(같은 시각은 구매 먼저) 전제 — 그래서 큐에 있는 구매는 늘 판매일 ≥ 구매일이다."""
    queues: dict[int, list[Trade]] = defaultdict(list)
    pairs, orphans = [], []
    for t in trades:
        if t.kind == KIND_BUY:
            queues[t.spid].append(t)
        elif queues[t.spid]:
            pairs.append(Pair(t.spid, queues[t.spid].pop(0), t))
        else:
            orphans.append(t)
    return pairs, orphans, {s: q for s, q in queues.items() if q}


def _holdings(left: dict[int, list[Trade]], games: _Games, apps: dict[int, list[tuple[int, int | None]]],
              today: date) -> tuple[list[Holding], int]:
    n = len(games)
    start = max(0, n - config.HOLD_RECENT_GAMES)
    recent: dict[int, tuple[datetime, int | None]] = {}  # spid → 최근 창의 마지막 출전(시각, 강화)
    for i in range(start, n):
        for sp, gr in games.starters[i]:
            recent[sp] = (games.dates[i], gr)
    out = []
    for spid, buys in left.items():
        last_app = games.dates[apps[spid][-1][0]] if apps.get(spid) else None
        held = False
        for b in sorted(buys, key=lambda t: t.date, reverse=True):
            r = recent.get(spid)
            if not held and r is not None and b.date <= r[0]:
                out.append(Holding(b, HELD, r[1]))
                held = True
            elif (today - b.date.date()).days <= config.HOLD_GRACE_DAYS:
                out.append(Holding(b, RECENT))
            elif last_app is None or last_app < b.date:
                out.append(Holding(b, NEVER_PLAYED))
            else:
                out.append(Holding(b, NOT_RECENT))
    return out, n - start


def build_timeline(details: list[dict], ouid: str, trades: list[Trade] | None,
                   today: date | None = None) -> Timeline:
    """trades 가 None 이면 거래 없이(남의 계정) — 첫 출전은 전부 추정, 짝·보유는 비어 있다."""
    today = today or date.today()
    games = _Games(details, ouid)
    tl = Timeline(trades=list(trades or []), has_trades=trades is not None)
    apps: dict[int, list[tuple[int, int | None]]] = defaultdict(list)  # spid → [(경기 번호, 강화)]
    for i, st in enumerate(games.starters):
        for sp, gr in st:
            apps[sp].append((i, gr))

    by_spid: dict[int, list[Trade]] = defaultdict(list)
    for t in tl.trades:
        by_spid[t.spid].append(t)
    if tl.has_trades:
        tl.pairs, tl.unmatched_sells, left = fifo_pairs(tl.trades)
        tl.holdings, tl.games_basis = _holdings(left, games, apps, today)
    else:
        tl.games_basis = min(len(games), config.HOLD_RECENT_GAMES)

    n = len(games)
    events = []

    def add(when, spid, kind, g_from, g_to, value=None, estimated=False):
        before, after = games.around(when)
        events.append(TimelineEvent(when, spid, kind, g_from, g_to, before, after, value, estimated))

    for spid, seq in apps.items():
        ts = by_spid.get(spid, [])
        t_dates = [t.date for t in ts]
        for t in ts:
            add(t.date, spid, t.kind, None, t.grade, t.value)
        # 거래 시각으로 출전을 구간으로 나눈다 — 같은 카드를 여러 번 사고팔면 구간마다 첫·마지막 출전
        groups: dict[int, list[tuple[int, int | None]]] = defaultdict(list)
        for i, gr in seq:
            groups[bisect_right(t_dates, games.dates[i])].append((i, gr))
        for k, grp in groups.items():
            i0, g0 = grp[0]
            est = not tl.has_trades or k == 0 or ts[k - 1].kind != KIND_BUY
            add(games.dates[i0], spid, KIND_FIRST, None, g0, estimated=est)
            prev = g0
            for i, gr in grp[1:]:
                if gr is not None and prev is not None and gr != prev:
                    add(games.dates[i], spid, KIND_GRADE, prev, gr)
                prev = gr if gr is not None else prev
            i_last, g_last = grp[-1]
            # 아직 쓰는 카드의 "마지막 출전"은 어제 경기라 소음 — 뒤에 판매가 있거나 최근 창 밖일 때만
            followed_by_sell = k < len(ts) and ts[k].kind == KIND_SELL
            if i_last != i0 and (followed_by_sell or i_last < n - config.HOLD_RECENT_GAMES):
                add(games.dates[i_last], spid, KIND_LAST, None, g_last)
    events.sort(key=lambda e: e.date, reverse=True)
    tl.events = events
    return tl


# ── 영입 전후 기록(82) ─────────────────────────────────────────────
# 선발 기록만 쓴다(거래 없음 — 내 계정이 아니어도 본다). "산 때" 가 아니라 "선발로 처음 쓴 때" 다.
# 효과가 아니라 기록이다: 선수 한 명을 바꾼 시점의 차이는 우연과 구분이 안 됐다(ROADMAP "## 82단계" 실측).
@dataclass
class ArrivalRow:
    """새로 쓰기 시작한 선수 한 줄. before = 그 선수가 없던 앞 판들 · after = 그 선수가 뛴 뒤 판들."""
    at: datetime
    sp_id: int
    pos: int
    prev_sp: int | None       # 앞 선수 — 그 자리에 있다가 빠진 사람. 모르면 None
    before: Window
    after: Window
    together: int             # 같은 판에 같이 새로 든 다른 선수(직전 ARRIVAL_RECENT 판에 없던 사람)
    window_changed: int       # 앞 창 첫 판과 뒤 창 끝 판의 선발이 다른 사람 수(이 선수 빼고)
    z: float | None           # 경기당 득실차 차이 ÷ 표준오차

    @property
    def outside(self) -> bool:
        return self.z is not None and abs(self.z) >= config.ARRIVAL_Z

    @property
    def squad_swap(self) -> bool:
        return self.together + 1 >= config.ARRIVAL_SQUAD_SWAP


@dataclass
class MoveRow:
    """같은 사람이 선발 자리를 옮긴 한 줄(경기 중 자리는 기록에 없다 — 선수마다 spPosition 하나)."""
    at: datetime
    sp_id: int
    from_pos: int
    to_pos: int
    before: Window
    after: Window
    together: int             # 그 판에 새로 든 선수(직전 ARRIVAL_RECENT 판에 없던 사람)
    z: float | None

    @property
    def outside(self) -> bool:
        return self.z is not None and abs(self.z) >= config.ARRIVAL_Z


@dataclass
class ArrivalReport:
    arrivals: list[ArrivalRow] = field(default_factory=list)
    moves: list[MoveRow] = field(default_factory=list)
    games: int = 0            # 선발 11명이 온전한 승·무·패 판


class _Lineup(NamedTuple):
    at: datetime
    wdl: int           # 0 승 · 1 무 · 2 패
    gf: int
    ga: int
    slots: dict        # 자리 → spId
    by_pid: dict       # 사람 → 자리


def pid_of(sp_id: int) -> int:
    """카드 번호 → 사람(뒤 6자리) — 시즌이 다른 같은 선수를 한 사람으로."""
    return sp_id % PLAYER_ID_MOD


def _goal(side: dict) -> int:
    v = (side.get("shoot") or {}).get("goalTotal")
    return int(v) if isinstance(v, (int, float)) else 0


def _lineups(details: list[dict], ouid: str) -> list[_Lineup]:
    """오래된 판부터. 승·무·패가 아닌 판 · 선발 11명이 안 되는 판은 뺀다(자리 비교가 안 된다)."""
    out = []
    for d in details:
        infos = d.get("matchInfo") or []
        me = next((p for p in infos if p.get("ouid") == ouid), None)
        opp = next((p for p in infos if p.get("ouid") != ouid), {})
        at = matchtime.parse(d.get("matchDate"))
        if me is None or at is None:
            continue
        res = (me.get("matchDetail") or {}).get("matchResult") or ""
        wdl = 0 if "승" in res else 1 if "무" in res else 2 if "패" in res else None
        if wdl is None:
            continue
        slots = {}
        for p in me.get("player") or []:
            pos, sp = p.get("spPosition"), p.get("spId")
            if isinstance(pos, int) and pos != SUB_POSITION and isinstance(sp, int):
                slots[pos] = sp
        if len(slots) < 11:
            continue
        out.append(_Lineup(at, wdl, _goal(me), _goal(opp), slots, {pid_of(sp): pos for pos, sp in slots.items()}))
    out.sort(key=lambda g: g.at)
    return out


def _side(games: list[_Lineup], ids: list[int], mixed: bool) -> Window:
    picked = [games[i] for i in ids]
    wdl = [sum(g.wdl == k for g in picked) for k in range(3)]
    return Window(len(picked), wdl[0], wdl[1], wdl[2], sum(g.gf for g in picked), sum(g.ga for g in picked), mixed)


def _gd_z(games: list[_Lineup], before: list[int], after: list[int]) -> float | None:
    """경기당 득실차의 뒤 − 앞 ÷ 표준오차(분산이 다른 두 묶음). 두 쪽 다 변화가 없으면 None."""
    a = [games[i].gf - games[i].ga for i in after]
    b = [games[i].gf - games[i].ga for i in before]
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = (va / len(a) + vb / len(b)) ** 0.5
    return (ma - mb) / se if se else None


def _spells(ids: list[int]) -> list[list[int]]:
    """쓴 판 번호 → 이어 쓴 구간들. 빈 판이 ARRIVAL_GAP 을 넘으면 끊는다."""
    out = [[ids[0]]]
    for i in ids[1:]:
        if i - out[-1][-1] - 1 > config.ARRIVAL_GAP:
            out.append([i])
        else:
            out[-1].append(i)
    return out


def _recent(games: list[_Lineup], i: int) -> list[_Lineup]:
    """직전 ARRIVAL_RECENT 판 — 바로 앞 한 판만 보면 로테이션에 속는다(대타가 앞 선수로 · 쉬고 돌아온 주전이 새 선수로)."""
    return games[max(0, i - config.ARRIVAL_RECENT):i]


def _newcomers(games: list[_Lineup], i: int) -> set[int]:
    seen: set[int] = set()
    for g in _recent(games, i):
        seen.update(g.by_pid)
    return set(games[i].by_pid) - seen


def _previous_player(games: list[_Lineup], s: int, pos: int) -> int | None:
    """앞 선수 — 직전 판들에서 그 자리를 가장 많이 맡았고 이번 판에 빠진 사람. 그 사람이 자리만 옮겼으면(안 빠졌으면)
    직전 판들의 주전(절반 넘게 뜀) 중 빠진 사람이 하나일 때 그 사람. 그래도 모르면 None."""
    recent, cur = _recent(games, s), games[s].by_pid
    in_slot = Counter(g.slots[pos] for g in recent if pos in g.slots)
    if in_slot:
        sp = in_slot.most_common(1)[0][0]
        if pid_of(sp) not in cur:
            return sp
    plays = Counter(p for g in recent for p in g.by_pid)
    gone = [p for p, n in plays.items() if n * 2 > len(recent) and p not in cur]
    if len(gone) != 1:
        return None
    last = next(g for g in reversed(recent) if gone[0] in g.by_pid)
    return last.slots[last.by_pid[gone[0]]]


def _arrival(games: list[_Lineup], pid: int, played: list[int], mixed: bool) -> ArrivalRow | None:
    lo, w = config.ARRIVAL_MIN_GAMES, config.ARRIVAL_WINDOW
    s = played[0]
    if len(played) < lo:
        return None
    before = []
    for i in range(s - 1, -1, -1):
        if pid in games[i].by_pid:     # 다시 데려온 줄 — 앞은 빠져 있던 판까지. 넘어가면 시대가 다른 옛 판과 견주게 된다
            break
        before.append(i)
        if len(before) == w:
            break
    before.reverse()
    if len(before) < lo:           # 기록 첫 판부터 있던 선수는 "앞" 이 없어 여기서 빠진다
        return None
    after = played[:w]
    cur = games[s]
    pos = cur.by_pid[pid]
    return ArrivalRow(at=cur.at, sp_id=cur.slots[pos], pos=pos, prev_sp=_previous_player(games, s, pos),
                      before=_side(games, before, mixed), after=_side(games, after, mixed),
                      together=len(_newcomers(games, s) - {pid}),
                      window_changed=len(set(games[after[-1]].by_pid) - set(games[before[0]].by_pid) - {pid}),
                      z=_gd_z(games, before, after))


def _moves(games: list[_Lineup], pid: int, played: list[int], mixed: bool) -> list[MoveRow]:
    runs: list[list] = []
    for i in played:
        pos = games[i].by_pid[pid]
        if runs and runs[-1][0] == pos:
            runs[-1][1].append(i)
        else:
            runs.append([pos, [i]])
    merged: list[list] = []
    for pos, ids in runs:
        if merged and (len(ids) < config.ARRIVAL_BLIP or merged[-1][0] == pos):
            merged[-1][1].extend(ids)
        else:
            merged.append([pos, list(ids)])
    out = []
    lo, w = config.ARRIVAL_MIN_GAMES, config.ARRIVAL_WINDOW
    for (p0, a), (p1, b) in zip(merged, merged[1:]):
        first = b[0]
        if len(a) < lo or len(b) < lo:
            continue
        # 포메이션이 달라졌으면 자리 번호만 바뀐 것일 수 있다 — 앞 자리에서 실제로 뛴 마지막 판과 견준다(그 사이 결장·깜빡임이 있어도)
        last = max(i for i in a if games[i].by_pid[pid] == p0)
        if set(games[first].slots) != set(games[last].slots):
            continue
        before, after = a[-w:], b[:w]
        out.append(MoveRow(at=games[first].at, sp_id=games[first].slots[p1], from_pos=p0, to_pos=p1,
                           before=_side(games, before, mixed), after=_side(games, after, mixed),
                           together=len(_newcomers(games, first)),
                           z=_gd_z(games, before, after)))
    return out


def arrival_report(details: list[dict], ouid: str) -> ArrivalReport:
    """선발 기록 → 새로 쓴 선수 · 자리를 옮긴 선수. 최신순."""
    games = _lineups(details, ouid)
    rep = ArrivalReport(games=len(games))
    if not games:
        return rep
    mixed = mixed_of_details(details, ouid)
    ids: dict[int, list[int]] = defaultdict(list)
    for i, g in enumerate(games):
        for pid in g.by_pid:
            ids[pid].append(i)
    for pid, lst in ids.items():
        for played in _spells(lst):
            row = _arrival(games, pid, played, mixed)
            if row is not None:
                rep.arrivals.append(row)
            rep.moves.extend(_moves(games, pid, played, mixed))
    rep.arrivals.sort(key=lambda r: (r.at, r.pos), reverse=True)
    rep.moves.sort(key=lambda r: (r.at, r.to_pos), reverse=True)
    return rep


# ── 포메이션을 바꾼 전후(97 X2) ────────────────────────────────────
# 82 영입 전후와 같은 틀 · 같은 말 — 효과가 아니라 기록이다(포메이션이 바뀌면 선수도 같이 바뀐다 · `together` 칸).
@dataclass
class FormationRow:
    """포메이션이 바뀐 한 번. before = 앞 구간 끝 ARRIVAL_WINDOW 판 · after = 새 구간 첫 ARRIVAL_WINDOW 판(그 구간 안에서만)."""
    at: datetime              # 새 포메이션으로 처음 뛴 때(history_runs 구간 시작)
    from_form: str
    to_form: str
    before: Window
    after: Window
    together: int             # 새 구간 첫 판 선발 중 직전 ARRIVAL_RECENT 판에 없던 선수 수
    z: float | None           # 경기당 득실차 차이 ÷ 표준오차(영입 전후와 같은 식)

    @property
    def outside(self) -> bool:
        return self.z is not None and abs(self.z) >= config.ARRIVAL_Z


@dataclass
class FormationReport:
    rows: list[FormationRow] = field(default_factory=list)
    changes: int = 0          # 포메이션이 바뀐 횟수(양쪽 판수가 모자라 줄이 빠진 것까지)
    games: int = 0            # 선발 11명이 온전한 승·무·패 판


def _form_of(g: _Lineup) -> str:
    return formation_of([{"spPosition": pos} for pos in g.slots])


def formation_report(details: list[dict], ouid: str) -> FormationReport:
    """포메이션이 바뀐 곳마다 앞뒤 기록 — 최신순. 바뀜은 `habits.history_runs` 의 이웃 구간(깜빡임을 앞 구간에 붙인 뒤)이고,
    구간은 시각이라 `_lineups`(선발 11명인 판만 모은 번호 공간)에는 **시각으로** 맞춘다. 양쪽이 X2_MIN_GAMES 판 미만이면 줄을 안 낸다."""
    games = _lineups(details, ouid)
    rep = FormationReport(games=len(games))
    runs = habits.history_runs(details, ouid, kinds=("formation",))
    rep.changes = max(len(runs) - 1, 0)
    if not games or len(runs) < 2:
        return rep
    mixed = mixed_of_details(details, ouid)
    times = [g.at for g in games]
    forms = [_form_of(g) for g in games]

    def inside(run) -> list[int]:
        # 구간 안 판 중 그 포메이션인 것만 — 구간 끝에 붙은 깜빡임 판이 앞 창에 섞이지 않게
        return [i for i in range(bisect_left(times, run.start), bisect_right(times, run.end)) if forms[i] == run.value]

    lo, w = config.X2_MIN_GAMES, config.ARRIVAL_WINDOW
    for a, b in zip(runs, runs[1:]):
        before, after = inside(a)[-w:], inside(b)[:w]
        if len(before) < lo or len(after) < lo:
            continue
        rep.rows.append(FormationRow(at=b.start, from_form=a.value, to_form=b.value,
                                     before=_side(games, before, mixed), after=_side(games, after, mixed),
                                     together=len(_newcomers(games, after[0])), z=_gd_z(games, before, after)))
    rep.rows.sort(key=lambda r: r.at, reverse=True)
    return rep
