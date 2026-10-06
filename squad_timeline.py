"""스쿼드 타임라인(1.4.1 · 12단계) — 경기 기록 + (내 계정일 때만) 거래 기록 → 카드가 들고 난 사건들.

규칙은 ROADMAP "스쿼드 타임라인" 절. 함정만 여기 적는다:

| 무엇 | 왜 |
|---|---|
| 출전은 **선발만**(교체 명단 spPosition 28 은 안 센다) | 사용자 결정(2026-10-06). 교체 명단은 평점이 늘 0 이라 뛰었는지도 모른다 |
| 짝(구매↔판매)은 **같은 spid 끼리 선입선출**, 전체 이력으로 | 강화는 산 뒤 바뀔 수 있어 grade 는 짝 기준이 아니다. 범위로 먼저 자르면 지난 시즌 산 카드가 "취득가 없음"이 된다 |
| 같은 시각이면 구매를 판매보다 먼저 | 판매일 ≥ 구매일인 구매하고만 짝짓는다 — 같은 초는 짝이 될 수 있게 |
| 보유 중은 spid 마다 **한 장**(최근 출전보다 앞선 가장 최근 구매) | 스쿼드엔 한 장 — 나머지는 강화 재료일 수 있어 평가가 두 배가 된다(검토 3회차) |
| 사건 표에는 **출전한 카드**의 거래만 | 키 주인 거래가 1.6만 줄이고 대부분 출전 안 한 카드다(되팔기·팩 정리) — 그건 가계부가 맡는다 |
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import NamedTuple

import config

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
class Window:
    """사건 앞이나 뒤 경기 묶음의 성적."""
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    gf: int = 0
    ga: int = 0

    @property
    def win_rate(self) -> float:
        return self.win / self.games * 100 if self.games else 0.0

    @property
    def weak(self) -> bool:
        return self.games < config.TIMELINE_MIN_GAMES


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


def _parse(raw) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace(" ", "T")[:19])
    except ValueError:
        return None


def _goals(p: dict) -> int:
    v = (p.get("shoot") or {}).get("goalTotal")
    return int(v) if isinstance(v, (int, float)) else 0


def parse_trades(rows) -> list[Trade]:
    """store.load_trades 줄 → Trade(시간순). 날짜·카드가 빠진 줄만 버린다."""
    out = []
    for r in rows:
        when, spid = _parse(r.get("trade_date")), r.get("spid")
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
            when = _parse(d.get("matchDate"))
            if me is None or when is None:
                continue
            opp = next((p for p in infos if p.get("ouid") != ouid), {})
            res = (me.get("matchDetail") or {}).get("matchResult") or ""
            starters = [(p.get("spId"), p.get("spGrade")) for p in me.get("player") or []
                        if p.get("spId") is not None and p.get("spPosition") != SUB_POSITION]
            rows.append((when, res, _goals(me), _goals(opp), starters))
        rows.sort(key=lambda r: r[0])
        self.dates = [r[0] for r in rows]
        self.starters = [r[4] for r in rows]
        self._acc = [(0, 0, 0, 0, 0)]
        w = dr = lo = gf = ga = 0
        for _, res, f, a, _ in rows:
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
            return Window()
        a, b = self._acc[lo], self._acc[hi]
        return Window(hi - lo, b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3], b[4] - a[4])

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
