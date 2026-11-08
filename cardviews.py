"""카드 정보로 만드는 화면 재료(68 일부) — 계산만(화면은 `core_api`). 선수 지표의 실효 OVR·키·체형 · 내 스쿼드 OVR ·
C5 선발 11장 시세 변화 `value_breakdown` · C6 주간 시세 변화 `price_index` · 빈 화면 사유 `screen_state`.

**`cardstats.py` 에 안 넣은 이유**: 51 측정(`tools/measure_cards.py`)이 그 파일의 해시를 D0 값과 견준다(`TOOL_FILES`) —
측정이 끝나기 전에 고치면 판정에 "도구 바뀜"이 남는다. 식은 `cardstats` 것을 부르기만 해서 측정과 어긋나지 않는다.
측정이 끝나면(11-26 뒤) 합쳐도 된다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

import cardcollect
import cardstats
import config
import matchtime
import store


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


# ── C5 선발 11장 시세 변화 ─────────────────────────────────────────────
@dataclass(frozen=True)
class ValueBreakdown:
    """고른 두 날 사이 선발 11장 시세 변화. total = 시세를 아는 카드만의 V2 − V1 = price + squad(항등식).
    카드는 (spid, 강화) 단위 — 강화만 올린 것은 뺀 카드 + 새 카드라 보강 효과에 든다(51 5판 B[중]8)."""
    day1: date
    day2: date
    game1: date | None          # 그 날에 쓴 선발이 실제로 나온 경기 날(그날이나 그 앞 가장 가까운 날)
    game2: date | None
    v1: int = 0
    v2: int = 0
    price: int = 0              # 두 날 다 쓴 카드의 시세 차 합
    squad: int = 0              # 새로 쓴 카드 − 뺀 카드
    unknown: int = 0            # 시세를 몰라 셈에서 뺀 장수
    kept: int = 0
    added: int = 0
    removed: int = 0

    @property
    def total(self) -> int:
        return self.v2 - self.v1


def _squads_by_day(details: list[dict], ouid: str) -> dict[date, frozenset[tuple[int, int]]]:
    """날마다 그날 마지막 경기 선발 11장 {(spid, 강화)} — 11장이 안 되는 판은 건너뛴다."""
    last: dict[date, tuple] = {}
    for d in details:
        me = _my_side(d, ouid)
        at = matchtime.parse(d.get("matchDate"))
        if me is None or at is None:
            continue
        cards = frozenset((p["spId"], p["spGrade"]) for p in cardstats.starters(me)
                          if isinstance(p.get("spId"), int) and isinstance(p.get("spGrade"), int))
        if len(cards) != 11:
            continue
        day = at.date()
        if day not in last or at > last[day][0]:
            last[day] = (at, cards)
    return {day: v[1] for day, v in last.items()}


def _squad_on(squads: dict[date, frozenset], day: date) -> tuple[date | None, frozenset]:
    days = [d for d in squads if d <= day]
    if not days:
        return None, frozenset()
    best = max(days)
    return best, squads[best]


def _price_on(obs: dict[str, int | None] | None, day: date) -> int | None:
    """그날 시세 — 없으면 그 앞 가장 가까운 관측일(C5_NEAREST_MAX_DAYS 까지). 매물 없음(None)은 모름."""
    if not obs:
        return None
    for back in range(config.C5_NEAREST_MAX_DAYS + 1):
        key = (day - timedelta(days=back)).isoformat()
        if key in obs:
            return obs[key]
    return None


def value_breakdown(details: list[dict], ouid: str, prices: dict[tuple[int, int], dict[str, int | None]],
                    day1: date, day2: date) -> ValueBreakdown:
    """prices: {(spid, 강화): {"YYYY-MM-DD": 시세 또는 None}} — `store.card_price_rows` 를 묶은 것."""
    squads = _squads_by_day(details, ouid)
    g1, s1 = _squad_on(squads, day1)
    g2, s2 = _squad_on(squads, day2)
    v1 = v2 = price = squad = unknown = 0
    for card in s1 & s2:
        p1, p2 = _price_on(prices.get(card), day1), _price_on(prices.get(card), day2)
        if p1 is None or p2 is None:
            unknown += 1
            continue
        v1, v2, price = v1 + p1, v2 + p2, price + p2 - p1
    for card in s2 - s1:
        p2 = _price_on(prices.get(card), day2)
        if p2 is None:
            unknown += 1
            continue
        v2, squad = v2 + p2, squad + p2
    for card in s1 - s2:
        p1 = _price_on(prices.get(card), day1)
        if p1 is None:
            unknown += 1
            continue
        v1, squad = v1 + p1, squad - p1
    return ValueBreakdown(day1, day2, g1, g2, v1, v2, price, squad, unknown,
                          len(s1 & s2), len(s2 - s1), len(s1 - s2))


def price_days(prices: dict[tuple[int, int], dict[str, int | None]]) -> list[date]:
    """시세 관측이 있는 날(오래된 날부터) — 날짜 고르기 칸에 쓴다."""
    return sorted({date.fromisoformat(d) for obs in prices.values() for d in obs})


# ── C6 주간 시세 변화 ─────────────────────────────────────────────────
@dataclass(frozen=True)
class CardMove:
    spid: int
    grade: int
    day: date
    before: int
    after: int

    @property
    def pct(self) -> float:
        return (self.after / self.before - 1) * 100


@dataclass(frozen=True)
class PriceIndex:
    """날마다 그날 관측된 표본 카드의 직전 관측(7~10일 전) 대비 변화율 **중앙값** — 누적 지수가 아니다(51 3판 B[중]9).
    recent = 최근 C6_RECENT_DAYS 안 간격 조건을 만족한 관측 수 · ready = recent ≥ C6_MIN_CARDS.
    wait_days = 모으는 중일 때 지금 쌓인 관측만으로 문턱에 닿는 가장 이른 날까지 남은 날(못 닿으면 None)."""
    days: list[tuple[date, float, int]] = field(default_factory=list)   # (날, 중앙값 %, 카드 수)
    up: list[CardMove] = field(default_factory=list)
    down: list[CardMove] = field(default_factory=list)
    recent: int = 0
    ready: bool = False
    wait_days: int | None = None


def price_index(rows, today: date) -> PriceIndex:
    """rows: (spid, 강화, "YYYY-MM-DD", 시세 또는 None) — C6 표본(src 비트 2)만. 같은 (spid, 고정 강화) 끼리,
    바로 앞 관측과 간격이 C6_GAP_MIN~C6_GAP_MAX 일인 것만 쓴다(앞뒤 시세가 다 있어야)."""
    series: dict[tuple[int, int], list[tuple[date, int | None]]] = {}
    for spid, grade, day, price in rows:
        series.setdefault((spid, grade), []).append((date.fromisoformat(day), price))
    moves: list[CardMove] = []
    pending: list[date] = []   # 다음 관측을 기다리는 마지막 관측 — 그 날 + GAP_MIN 부터 짝이 생길 수 있다
    for (spid, grade), obs in series.items():
        obs.sort()
        for (d0, p0), (d1, p1) in zip(obs, obs[1:]):
            if config.C6_GAP_MIN <= (d1 - d0).days <= config.C6_GAP_MAX and p0 and p1:
                moves.append(CardMove(spid, grade, d1, p0, p1))
        last_day, last_price = obs[-1]
        if last_price and today < last_day + timedelta(days=config.C6_GAP_MAX):
            pending.append(last_day)
    by_day: dict[date, list[float]] = {}
    for m in moves:
        by_day.setdefault(m.day, []).append(m.pct)
    days = [(d, median(v), len(v)) for d, v in sorted(by_day.items())]
    start = today - timedelta(days=config.C6_RECENT_DAYS - 1)
    recent = [m for m in moves if start <= m.day <= today]
    ready = len(recent) >= config.C6_MIN_CARDS
    latest: dict[tuple[int, int], CardMove] = {}
    for m in recent:
        if (m.spid, m.grade) not in latest or m.day > latest[(m.spid, m.grade)].day:
            latest[(m.spid, m.grade)] = m
    up = sorted((m for m in latest.values() if m.pct > 0), key=lambda m: (-m.pct, m.spid, m.grade))[:config.C6_TOP_N]
    down = sorted((m for m in latest.values() if m.pct < 0), key=lambda m: (m.pct, m.spid, m.grade))[:config.C6_TOP_N]
    wait = None
    if not ready:
        for k in range(1, config.C6_GAP_MAX + 1):
            day = today + timedelta(days=k)
            lo = day - timedelta(days=config.C6_RECENT_DAYS - 1)
            have = sum(1 for m in moves if lo <= m.day)
            have += sum(1 for d0 in pending if lo <= d0 + timedelta(days=config.C6_GAP_MIN) <= day)
            if have >= config.C6_MIN_CARDS:
                wait = k
                break
    return PriceIndex(days, up, down, len(recent), ready, wait)


# ── 빈 화면 사유 ──────────────────────────────────────────────────────
def screen_state(conn: sqlite3.Connection | None, today: date):
    """`cardcollect.screen_state` + 그날 형식 바뀜으로 멈춤("format") — 그 파일은 51 측정이 해시를 재서
    11-26 판정 전엔 여기서 감싼다(51 ① 리뷰 [하]: 형식 멈춤이 "채우는 중" 으로 보였다)."""
    base = cardcollect.screen_state(conn, today)
    if (conn is not None and isinstance(base, tuple)
            and store.budget_hit_429(conn, today.isoformat(), (f"{store.BUDGET_CARD}_format",))):
        return "format"
    return base
