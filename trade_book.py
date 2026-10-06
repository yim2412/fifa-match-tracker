"""이적시장 가계부(N3 · 1.4.1 · 12단계) — 타임라인의 짝·보유 상태 → 지출·수입·손익. 내 계정만.

| 무엇 | 왜 |
|---|---|
| 짝은 타임라인이 **전체 이력**으로 맞춘 것, 범위는 그 뒤에 | 실현 손익은 판매 날짜로, 지출은 구매 날짜로 범위에 넣는다 |
| 옛 거래를 다 못 받았으면 실현 손익·취득가 없음을 비운다(None) | 판매가 뒤 구매와 잘못 짝지어진다 — 0 으로 보이면 사실처럼 읽힌다 |
| 실현(판매 금액)과 평가(홈페이지 시세)를 한 합계로 안 더한다 | 수수료 기준이 다를 수 있다(R7 — 사용자 대조 전) |
| 평가는 보유 중(HELD)만 — 최근 구매(RECENT)는 따로 소계, 나머지는 건수·산 값만 | 강화 재료·방출 카드가 보유로 평가되면 손익이 부푼다(검토 1·3회차) |
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from squad_timeline import HELD, NEVER_PLAYED, NOT_RECENT, RECENT, Holding, Timeline


@dataclass
class Valued:
    """평가 묶음 — 시세가 있는 카드만 손익에 더한다."""
    rows: list[tuple[Holding, int | None]] = field(default_factory=list)  # (보유 한 장, 오늘 시세 또는 None)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def priced(self) -> int:
        return sum(1 for _, p in self.rows if p is not None)

    @property
    def cost(self) -> int:
        return sum(h.trade.value or 0 for h, _ in self.rows)

    @property
    def gain(self) -> int | None:
        vals = [p - (h.trade.value or 0) for h, p in self.rows if p is not None]
        return sum(vals) if vals else None


@dataclass
class Ledger:
    spent: int = 0
    spent_n: int = 0
    income: int = 0
    income_n: int = 0
    realized: int | None = None      # 옛 거래를 다 못 받았으면 None
    realized_n: int = 0
    no_cost: int | None = None       # 짝 없는 판매(취득가 없음) 합계
    no_cost_n: int = 0
    held: Valued = field(default_factory=Valued)
    recent: Valued = field(default_factory=Valued)
    unknown: dict[int, tuple[int, int]] = field(default_factory=dict)  # 상태 → (건수, 산 값 합계)

    @property
    def net(self) -> int:
        """순지출(+면 쓴 돈이 더 많다)."""
        return self.spent - self.income


def ledger(tl: Timeline, prices: dict[tuple[int, int], tuple[int, str]],
           in_scope: Callable[[datetime], bool] | None = None, complete: bool = True) -> Ledger:
    """prices: store.load_card_prices 결과. in_scope 가 None 이면 전체 기간.
    보유·평가는 지금 상태라 범위와 무관하다."""
    ok = in_scope or (lambda _d: True)
    lg = Ledger()
    for t in tl.trades:
        if t.value is None or not ok(t.date):
            continue
        if t.kind == "buy":
            lg.spent += t.value
            lg.spent_n += 1
        else:
            lg.income += t.value
            lg.income_n += 1
    if complete:
        lg.realized = lg.no_cost = 0
        for p in tl.pairs:
            if p.profit is not None and ok(p.sell.date):
                lg.realized += p.profit
                lg.realized_n += 1
        for t in tl.unmatched_sells:
            if t.value is not None and ok(t.date):
                lg.no_cost += t.value
                lg.no_cost_n += 1
    for h in tl.holdings:
        if h.status == HELD:
            grade = h.cur_grade if h.cur_grade is not None else h.trade.grade
            lg.held.rows.append((h, _price(prices, h.trade.spid, grade)))
        elif h.status == RECENT:
            lg.recent.rows.append((h, _price(prices, h.trade.spid, h.trade.grade)))
        else:
            n, s = lg.unknown.get(h.status, (0, 0))
            lg.unknown[h.status] = (n + 1, s + (h.trade.value or 0))
    for st in (NEVER_PLAYED, NOT_RECENT):
        lg.unknown.setdefault(st, (0, 0))
    return lg


def _price(prices, spid: int, grade: int | None) -> int | None:
    if grade is None:
        return None
    hit = prices.get((spid, grade))
    return hit[0] if hit else None


def price_targets(tl: Timeline) -> list[int]:
    """시세를 읽을 카드 — 보유 중 먼저, 그다음 최근 구매(나머지는 평가 안 한다). 겹치면 한 번."""
    order = [h.trade.spid for h in tl.holdings if h.status == HELD]
    order += [h.trade.spid for h in tl.holdings if h.status == RECENT]
    return list(dict.fromkeys(order))
