"""스쿼드 타임라인(squad_timeline.py) · 이적시장 가계부(trade_book.py) 테스트 — 지어낸 경기·거래로, 네트워크 없이.

pytest 없이 `python tests/test_timeline.py`. 카드 번호·금액·saleSn 은 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import squad_timeline as st
import trade_book as tb

T0 = datetime(2026, 6, 1, 12, 0, 0)
TODAY = date(2026, 10, 7)


def _match(when: datetime, result: str = "승", gf: int = 1, ga: int = 0, starters=(), subs=()) -> dict:
    players = [{"spId": s, "spGrade": g, "spPosition": 5} for s, g in starters]
    players += [{"spId": s, "spGrade": g, "spPosition": st.SUB_POSITION} for s, g in subs]
    return {"matchId": when.isoformat(), "matchDate": when.isoformat(timespec="seconds"),
            "matchInfo": [
                {"ouid": "me", "matchDetail": {"matchResult": result}, "shoot": {"goalTotal": gf}, "player": players},
                {"ouid": "opp", "matchDetail": {"matchResult": "패"}, "shoot": {"goalTotal": ga},
                 "player": [{"spId": 999, "spGrade": 9, "spPosition": 5}]}]}


def _trade(kind: str, when: datetime, spid: int, value: int, grade: int = 5, sn: str | None = None) -> st.Trade:
    return st.Trade(kind, sn or f"{kind}{when.isoformat()}{spid}", when, spid, grade, value)


def _days(n: int) -> datetime:
    return T0 + timedelta(days=n)


def _build(details, trades, today=TODAY):
    trades = None if trades is None else sorted(trades, key=lambda t: (t.date, t.kind != "buy"))
    return st.build_timeline(list(reversed(details)), "me", trades, today=today)  # 화면처럼 최신순으로 준다


# ── 짝(선입선출) ─────────────────────────────────────────────────────────

def test_fifo_pairs_first_in_first_out():
    tr = [_trade("buy", _days(1), 1, 100), _trade("buy", _days(2), 1, 200), _trade("sell", _days(3), 1, 500),
          _trade("sell", _days(4), 2, 70)]
    pairs, orphans, left = st.fifo_pairs(tr)
    assert [(p.buy.value, p.sell.value, p.profit) for p in pairs] == [(100, 500, 400)]
    assert [t.value for t in orphans] == [70]          # 산 기록 없는 판매 = 취득가 없음
    assert [t.value for t in left[1]] == [200]


def test_same_second_buy_pairs_but_earlier_sell_does_not():
    same = st.parse_trades([
        {"kind": "sell", "sale_sn": "s", "trade_date": "2026-06-02T10:00:00", "spid": 1, "grade": 5, "value": 9},
        {"kind": "buy", "sale_sn": "b", "trade_date": "2026-06-02T10:00:00", "spid": 1, "grade": 5, "value": 4}])
    assert [t.kind for t in same] == ["buy", "sell"]   # 같은 초면 구매 먼저 — 판매일 ≥ 구매일
    assert len(st.fifo_pairs(same)[0]) == 1
    before = [_trade("sell", _days(1), 1, 9), _trade("buy", _days(2), 1, 4)]
    pairs, orphans, left = st.fifo_pairs(before)
    assert not pairs and len(orphans) == 1 and len(left[1]) == 1  # 판 뒤에 산 것과는 짝짓지 않는다


def test_parse_trades_drops_broken_rows():
    rows = st.parse_trades([{"kind": "buy", "sale_sn": "a", "trade_date": None, "spid": 1},
                            {"kind": "buy", "sale_sn": "b", "trade_date": "2026-06-02T10:00:00", "spid": None},
                            {"kind": "gift", "sale_sn": "c", "trade_date": "2026-06-02T10:00:00", "spid": 1},
                            {"kind": "buy", "sale_sn": "d", "trade_date": "2026-06-02T10:00:00", "spid": 3,
                             "grade": 2, "value": 5}])
    assert [t.sale_sn for t in rows] == ["d"]


# ── 출전 · 사건 ──────────────────────────────────────────────────────────

def test_subs_are_not_appearances():
    details = [_match(_days(i), starters=[(1, 5)], subs=[(2, 5)]) for i in range(5)]
    tl = _build(details, [_trade("buy", _days(-1), 2, 100)])
    assert {e.spid for e in tl.events} == {1}               # 교체 명단만 있던 카드는 사건이 없다
    assert [h.status for h in tl.holdings] == [st.NEVER_PLAYED]  # 보유 중으로도 안 센다


def test_events_first_grade_last_and_estimate():
    # 60경기: 카드 7 은 0~9 경기(5번째부터 강화 6), 카드 8 은 끝까지
    details = [_match(_days(i), starters=[(8, 3)] + ([(7, 5 if i < 5 else 6)] if i < 10 else []))
               for i in range(60)]
    tl = _build(details, None)
    ev = {(e.spid, e.kind): e for e in tl.events}
    assert ev[(7, st.KIND_FIRST)].estimated                  # 거래 없음(남의 계정) — 첫 출전은 추정
    g = ev[(7, st.KIND_GRADE)]
    assert (g.grade_from, g.grade_to, g.date) == (5, 6, _days(5))
    assert ev[(7, st.KIND_LAST)].date == _days(9)            # 최근 50경기 밖이라 마지막 출전이 사건
    assert (8, st.KIND_LAST) not in ev                       # 아직 쓰는 카드의 마지막 출전은 안 낸다
    assert tl.events == sorted(tl.events, key=lambda e: e.date, reverse=True)


def test_segments_split_by_trades():
    # 카드 7: 산 뒤 0~4 경기 출전 → 판매 → 팩으로 다시 받아 20~24 경기 출전
    details = [_match(_days(i), starters=[(7, 5)] if i < 5 or 20 <= i < 25 else [(8, 1)]) for i in range(100)]
    trades = [_trade("buy", _days(-1), 7, 100), _trade("sell", _days(10), 7, 300)]
    tl = _build(details, trades)
    firsts = sorted((e.date, e.estimated) for e in tl.events if e.spid == 7 and e.kind == st.KIND_FIRST)
    assert firsts == [(_days(0), False), (_days(20), True)]  # 앞 구간은 산 카드, 뒤 구간은 산 기록 없음
    lasts = sorted(e.date for e in tl.events if e.spid == 7 and e.kind == st.KIND_LAST)
    assert lasts == [_days(4), _days(24)]                    # 판매 앞 구간 · 최근 창 밖 구간
    kinds = sorted(e.kind for e in tl.events if e.spid == 7)
    assert kinds.count("buy") == 1 and kinds.count("sell") == 1


def test_events_only_for_played_cards():
    details = [_match(_days(i), starters=[(1, 5)]) for i in range(3)]
    trades = [_trade("buy", _days(-5), 50, 10), _trade("sell", _days(-2), 50, 30)]
    tl = _build(details, trades)
    assert all(e.spid == 1 for e in tl.events)               # 출전 안 한 카드의 되팔기는 사건 표에 없다
    assert len(tl.pairs) == 1                                # 가계부 짝에는 있다


def test_window_before_after():
    # 0~29 경기는 승, 30~59 는 패. 카드 7 은 30번째 경기에 처음 나온다
    details = [_match(_days(i), "승" if i < 30 else "패", gf=2 if i < 30 else 0, ga=1,
                      starters=[(7, 5)] if i >= 30 else [(8, 5)]) for i in range(60)]
    tl = _build(details, None)
    e = next(e for e in tl.events if e.spid == 7 and e.kind == st.KIND_FIRST)
    n = config.TIMELINE_WINDOW
    assert (e.before.games, e.before.win, e.before.gf, e.before.ga) == (n, n, 2 * n, n)
    assert (e.after.games, e.after.lose, e.after.gf) == (n, n, 0)  # 뒤 창은 그 경기부터
    first8 = next(e for e in tl.events if e.spid == 8 and e.kind == st.KIND_FIRST)
    assert first8.before.games == 0 and first8.before.weak and not first8.after.weak


def test_window_weak_threshold():
    assert st.Window(games=config.TIMELINE_MIN_GAMES - 1).weak
    assert not st.Window(games=config.TIMELINE_MIN_GAMES).weak


# ── 짝 안 맞은 구매의 상태 ───────────────────────────────────────────────

def test_holding_status_order():
    # 60경기(하루 한 경기, 마지막 경기 = 59일째): 카드 1 은 끝까지(강화 5→7), 카드 4 는 0~5 경기만
    details = [_match(_days(i), starters=[(1, 5 if i < 40 else 7)] + ([(4, 2)] if i < 6 else [])) for i in range(60)]
    today = _days(60).date()
    trades = [
        _trade("buy", _days(-10), 1, 100, grade=5, sn="old1"),   # 같은 카드 둘 — 최근 한 장만 보유
        _trade("buy", _days(-5), 1, 200, grade=5, sn="new1"),
        _trade("buy", _days(57), 2, 300, sn="recent"),           # 3일 전 · 안 씀
        _trade("buy", _days(20), 3, 400, sn="never"),            # 40일 전 · 한 번도 안 씀
        _trade("buy", _days(-3), 4, 500, sn="gone"),             # 썼지만 최근 50경기 밖
    ]
    tl = _build(details, trades, today=today)
    got = {h.trade.sale_sn: (h.status, h.cur_grade) for h in tl.holdings}
    assert got == {"new1": (st.HELD, 7), "old1": (st.NOT_RECENT, None), "recent": (st.RECENT, None),
                   "never": (st.NEVER_PLAYED, None), "gone": (st.NOT_RECENT, None)}, got
    assert tl.games_basis == config.HOLD_RECENT_GAMES


def test_held_needs_buy_before_recent_appearance():
    # 마지막 출전 뒤에 산 같은 카드(강화 재료 등)는 보유로 안 본다 — 스쿼드의 그 카드는 산 기록이 아니다
    details = [_match(_days(i), starters=[(1, 5)]) for i in range(10)]
    tl = _build(details, [_trade("buy", _days(30), 1, 100)], today=_days(60).date())
    assert [h.status for h in tl.holdings] == [st.NEVER_PLAYED]


def test_holding_boundaries_same_second_and_grace_day():
    # 같은 초는 "산 뒤 썼다"로 본다(보유·미출전 판정 둘 다) · 산 지 정확히 HOLD_GRACE_DAYS 일이면 아직 최근 구매
    details = [_match(_days(i), starters=[(1, 5)] + ([(2, 5)] if i == 0 else [])) for i in range(60)]
    today = _days(60).date()
    trades = [_trade("buy", _days(59), 1, 100, sn="held"),      # 마지막 출전과 같은 초
              _trade("buy", _days(0), 2, 100, sn="played"),     # 유일한 출전과 같은 초 · 최근 50경기 밖
              _trade("buy", datetime.combine(today - timedelta(days=config.HOLD_GRACE_DAYS), T0.time()), 3, 100,
                     sn="grace")]
    got = {h.trade.sale_sn: h.status for h in _build(details, trades, today=today).holdings}
    assert got == {"held": st.HELD, "played": st.NOT_RECENT, "grace": st.RECENT}, got


def test_last_event_window_boundary():
    # 마지막 출전이 최근 50경기 창의 첫 경기면 아직 쓰는 카드 — "마지막 출전" 사건을 안 낸다
    n = 60
    details = [_match(_days(i), starters=[(8, 1)] + ([(7, 5)] if i <= n - config.HOLD_RECENT_GAMES else []))
               for i in range(n)]
    assert not [e for e in _build(details, None).events if e.spid == 7 and e.kind == st.KIND_LAST]


def test_games_basis_when_few_games():
    tl = _build([_match(_days(i), starters=[(1, 5)]) for i in range(7)], [])
    assert tl.games_basis == 7


# ── 가계부 ───────────────────────────────────────────────────────────────

def _season(d: datetime) -> bool:
    return d >= _days(100)


def test_ledger_pairs_before_scope():
    # 지난 시즌에 사서 이번 시즌에 판 카드 — 짝을 먼저 맞추고 범위는 판매 날짜로
    trades = [_trade("buy", _days(10), 1, 100), _trade("sell", _days(120), 1, 250),
              _trade("buy", _days(110), 2, 40), _trade("sell", _days(130), 9, 60)]
    tl = _build([], trades)
    lg = tb.ledger(tl, {}, in_scope=_season)
    assert (lg.spent, lg.spent_n, lg.income, lg.income_n) == (40, 1, 310, 2)
    assert (lg.realized, lg.realized_n) == (150, 1)          # "취득가 없음"이 되지 않는다
    assert (lg.no_cost, lg.no_cost_n) == (60, 1)
    assert lg.net == 40 - 310
    whole = tb.ledger(tl, {})
    assert (whole.spent, whole.realized) == (140, 150)


def test_ledger_incomplete_blanks_realized():
    tl = _build([], [_trade("buy", _days(1), 1, 100), _trade("sell", _days(2), 1, 300)])
    lg = tb.ledger(tl, {}, complete=False)
    assert lg.realized is None and lg.no_cost is None        # 0 이 아니라 빈칸
    assert lg.spent == 100 and lg.income == 300               # 지출·수입은 그대로


def test_ledger_valuation_groups():
    details = [_match(_days(i), starters=[(1, 5 if i < 40 else 7)]) for i in range(60)]
    trades = [_trade("buy", _days(-5), 1, 200, grade=5), _trade("buy", _days(57), 2, 300, grade=3),
              _trade("buy", _days(20), 3, 400), _trade("buy", _days(21), 6, 50)]
    tl = _build(details, trades, today=_days(60).date())
    prices = {(1, 7): (1000, "d"), (1, 5): (1, "d"), (2, 3): (350, "d"), (3, 5): (9999, "d")}
    lg = tb.ledger(tl, prices)
    assert (lg.held.count, lg.held.priced, lg.held.gain) == (1, 1, 800)  # 지금 강화(7) 시세 기준
    assert (lg.recent.count, lg.recent.gain) == (1, 50)                  # 산 강화(3) 시세 기준 · 따로 소계
    assert lg.unknown[st.NEVER_PLAYED] == (2, 450)                       # 평가 안 함 — 건수·산 값만
    assert lg.unknown[st.NOT_RECENT] == (0, 0)
    assert tb.ledger(tl, {}).held.gain is None                           # 시세가 없으면 빈칸(0 아님)


def test_price_targets_held_then_recent_only():
    details = [_match(_days(i), starters=[(1, 5), (5, 5)]) for i in range(60)]
    trades = [_trade("buy", _days(57), 2, 1), _trade("buy", _days(-1), 5, 1), _trade("buy", _days(-2), 1, 1),
              _trade("buy", _days(20), 3, 1)]
    tl = _build(details, trades, today=_days(60).date())
    got = tb.price_targets(tl)
    assert set(got[:2]) == {1, 5} and got[2:] == [2], got    # 보유 중 먼저 · 미출전(3)은 안 읽는다


# ── 예산 ─────────────────────────────────────────────────────────────────

def test_timeline_budget_10k():
    """1만 경기 + 거래 1.6만 줄 — 타임라인·가계부 한 번 300ms 이하(ROADMAP 예산 ⑦)."""
    details = [_match(T0 + timedelta(minutes=15 * i), "승" if i % 3 else "패",
                      starters=[(100 + (i // 200 + k) % 60, 1 + k % 8) for k in range(11)],
                      subs=[(300 + k, 1) for k in range(7)]) for i in range(10_000)]
    details.reverse()
    trades = sorted((_trade("buy" if j % 3 == 0 else "sell", T0 + timedelta(minutes=10 * j), 100 + j % 400,
                            1000 + j, sn=f"t{j}") for j in range(16_000)),
                    key=lambda t: (t.date, t.kind != "buy"))
    best = None
    for _ in range(3):
        t = time.perf_counter()
        tl = st.build_timeline(details, "me", trades, today=TODAY)
        tb.ledger(tl, {})
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    print(f"       타임라인+가계부 1만 경기·거래 1.6만: {best * 1000:.0f}ms · 사건 {len(tl.events)}")
    assert best < 0.3, f"{best * 1000:.0f}ms"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
