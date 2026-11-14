"""스쿼드 타임라인(squad_timeline.py) · 이적시장 가계부(trade_book.py) 테스트 — 지어낸 경기·거래로, 네트워크 없이.

pytest 없이 `python tests/test_timeline.py`. 카드 번호·금액·saleSn 은 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import config
import squad_timeline as st
import trade_book as tb

T0 = datetime(2026, 6, 1, 12, 0, 0)
TODAY = date(2026, 10, 7)


def _match(when: datetime, result: str = "승", gf: int = 1, ga: int = 0, starters=(), subs=()) -> dict:
    players = [{"spId": s, "spGrade": g, "spPosition": 5} for s, g in starters]
    players += [{"spId": s, "spGrade": g, "spPosition": st.SUB_POSITION} for s, g in subs]
    raw = when - timedelta(hours=9)   # when 은 KST — 넥슨 원문은 UTC(63)
    return {"matchId": when.isoformat(), "matchDate": raw.isoformat(timespec="seconds"),
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
    n = config.TIMELINE_MIN_GAMES
    assert st.Window(games=n - 1, win=n - 1, mixed=False).weak
    assert not st.Window(games=n, win=n, mixed=False).weak
    # A6 — 표본은 승률 분모(rated). 섞인 묶음이면 무승부는 표본이 아니다
    assert not st.Window(games=n, win=n - 1, draw=1, mixed=False).weak
    assert st.Window(games=n, win=n - 1, draw=1, mixed=True).weak
    try:
        st.Window(games=n, win=n).weak
    except ValueError:
        pass
    else:
        raise AssertionError("mixed 를 안 채운 창이 조용히 옛 식으로 셌다")


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

import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


# ── 영입 전후 기록(82) ────────────────────────────────────────────
_BASE = {10 + k: k + 1 for k in range(11)}     # 자리 → 사람


def _game(i, slots, gd=0, res=None, season=100):
    """slots: 자리 → 사람. 득실 gd 는 나만 넣는다. 카드 번호 = season × 100만 + 사람."""
    gf, ga = max(gd, 0), max(-gd, 0)
    r = res or ("승" if gd > 0 else "무" if gd == 0 else "패")
    me = {"ouid": "me", "nickname": "나", "shoot": {"goalTotal": gf}, "matchDetail": {"matchResult": r},
          "player": [{"spPosition": pos, "spId": season * 1_000_000 + pid} for pos, pid in slots.items()]
          + [{"spPosition": 28, "spId": season * 1_000_000 + 99}]}
    opp = {"ouid": "상대", "nickname": "상대", "shoot": {"goalTotal": ga}, "matchDetail": {"matchResult": "패"},
           "player": []}
    t = datetime(2026, 1, 1) + timedelta(hours=i)
    return {"matchId": f"m{i}", "matchDate": t.strftime("%Y-%m-%dT%H:%M:%S"), "matchInfo": [me, opp]}


def _games(*parts):
    """parts: (판수, 자리표[, 득실 함수]) — 차례로 잇는다. 최신순으로 돌려준다(저장 순서와 같게)."""
    out, i = [], 0
    for part in parts:
        n, slots = part[0], part[1]
        fn = part[2] if len(part) > 2 else (lambda k: k % 3 - 1)
        for k in range(n):
            out.append(_game(i, slots, fn(k)))
            i += 1
    return list(reversed(out))


def _swap(pos, pid, base=_BASE):
    return {**base, pos: pid}


def _rep(*parts):
    return st.arrival_report(_games(*parts), "me")


def test_arrival_basic_row_by_hand():
    # 앞 150판 득실 (0,1 반복) · 뒤 120판 (2,3 반복) — 손으로 센 값
    rep = _rep((150, _BASE, lambda k: k % 2), (120, _swap(20, 12), lambda k: 2 + k % 2))
    assert rep.games == 270 and len(rep.arrivals) == 1 and rep.moves == [], rep
    r = rep.arrivals[0]
    assert r.sp_id == 100_000_012 and r.pos == 20 and r.prev_sp == 100_000_011, r
    assert (r.before.games, r.after.games) == (150, 120)
    assert (r.before.gf, r.after.gf) == (75, 300), (r.before.gf, r.after.gf)
    assert (r.before.win, r.before.draw, r.after.win) == (75, 75, 120)
    assert r.together == 0 and not r.squad_swap and r.window_changed == 0
    # z = (2.5 − 0.5) ÷ √(0.2521/120 + 0.2517/150) — 분산은 표본 분산(n−1)
    va, vb = 0.25 * 120 / 119, 0.25 * 150 / 149
    want = 2.0 / (va / 120 + vb / 150) ** 0.5
    assert abs(r.z - want) < 1e-9 and r.outside, (r.z, want)


def test_arrival_thresholds_are_exact_and_wired():
    ok = _rep((100, _BASE), (100, _swap(20, 12)))
    assert len(ok.arrivals) == 1, "앞뒤 정확히 100판은 줄을 낸다"
    assert not _rep((100, _BASE), (99, _swap(20, 12))).arrivals, "뒤 99판"
    assert not _rep((99, _BASE), (100, _swap(20, 12))).arrivals, "앞 99판"
    many = _rep((500, _BASE), (450, _swap(20, 12))).arrivals[0]
    assert (many.before.games, many.after.games) == (400, 400), "창은 앞뒤 400판까지"
    keep = config.ARRIVAL_MIN_GAMES, config.ARRIVAL_WINDOW
    try:                                         # 배선 — 기본값이 아닌 값으로
        config.ARRIVAL_MIN_GAMES, config.ARRIVAL_WINDOW = 7, 9
        r = _rep((20, _BASE), (8, _swap(20, 12))).arrivals[0]
        assert (r.before.games, r.after.games) == (9, 8), (r.before.games, r.after.games)
    finally:
        config.ARRIVAL_MIN_GAMES, config.ARRIVAL_WINDOW = keep


def test_arrival_first_lineup_and_season_card_are_not_arrivals():
    # 기록 첫 판부터 있던 11명은 "앞" 이 없다 · 같은 사람의 다른 시즌 카드는 새 선수가 아니다
    games = [_game(i, _BASE, i % 3 - 1, season=100) for i in range(150)]
    games += [_game(150 + i, _BASE, i % 3 - 1, season=200) for i in range(150)]
    rep = st.arrival_report(list(reversed(games)), "me")
    assert rep.arrivals == [] and rep.moves == [], rep


def test_arrival_gap_boundary_and_before_excludes_him():
    # 빈 판 정확히 30 은 같은 구간(새 줄 없음) · 31 이면 다시 들어온 것
    same = _rep((120, _swap(20, 13)), (120, _BASE), (30, _swap(20, 13)), (120, _BASE))
    pid11 = [r for r in same.arrivals if r.sp_id == 100_000_011]
    assert len(pid11) == 1 and pid11[0].after.games == 240, [(r.sp_id, r.after.games) for r in same.arrivals]
    back = _rep((120, _swap(20, 13)), (120, _BASE), (150, _swap(20, 13)), (120, _BASE))
    pid11 = sorted((r for r in back.arrivals if r.sp_id == 100_000_011), key=lambda r: r.at)
    assert len(pid11) == 2, [(r.sp_id, r.after.games) for r in back.arrivals]
    # 다시 온 줄의 "앞" 은 그가 빠져 있던 150판까지 — 그 너머(그가 뛰기 전 옛 판)로 건너뛰지 않는다
    assert pid11[1].before.games == 150, pid11[1].before.games
    # 31판만 쉬고 돌아오면 앞이 100판이 안 돼 줄이 없다(앞에 옛 판을 끌어다 채우지 않는다)
    short = _rep((120, _swap(20, 13)), (120, _BASE), (31, _swap(20, 13)), (120, _BASE))
    assert len([r for r in short.arrivals if r.sp_id == 100_000_011]) == 1, [(r.sp_id, r.before.games) for r in short.arrivals]


def test_arrival_result_filter_and_short_lineup():
    # 오류 판 · 선발 10명 판은 뺀다 — 넣으면 앞 판수가 달라진다
    games = [_game(i, _BASE, i % 3 - 1) for i in range(100)]
    games.append(_game(100, _BASE, 5, res="오류"))
    short = dict(_BASE)
    short.pop(20)
    games.append(_game(101, short, 5))
    games += [_game(102 + i, _swap(20, 12), i % 3 - 1) for i in range(100)]
    rep = st.arrival_report(list(reversed(games)), "me")
    assert rep.games == 200 and rep.arrivals[0].before.games == 100, (rep.games, rep.arrivals[0].before.games)
    assert rep.arrivals[0].before.gf == sum(max(i % 3 - 1, 0) for i in range(100))
    assert st.arrival_report([], "me").games == 0
    assert st.arrival_report(list(reversed(games)), "남").arrivals == [], "내가 안 낀 경기"


def test_arrival_squad_swap_and_previous_player():
    # 5명 동시 — 스쿼드 교체 · 앞 선수는 그 자리에 있다 빠진 사람
    new = {**_BASE, 16: 21, 17: 22, 18: 23, 19: 24, 20: 25}
    rep = _rep((100, _BASE), (100, new))
    assert len(rep.arrivals) == 5 and all(r.together == 4 and r.squad_swap for r in rep.arrivals)
    assert {r.pos: r.prev_sp for r in rep.arrivals}[17] == 100_000_008
    four = {**_BASE, 17: 22, 18: 23, 19: 24, 20: 25}
    assert not _rep((100, _BASE), (100, four)).arrivals[0].squad_swap, "4명은 교체 표시 문턱 밑"
    # 앞 선수가 자리만 옮겼으면(안 빠졌으면) 그 사람이 아니다 — 빠진 한 사람
    shift = {**_BASE, 20: 12, 19: 11}          # 11번이 20 → 19 로, 10번이 빠짐
    r = _rep((100, _BASE), (100, shift)).arrivals[0]
    assert r.sp_id == 100_000_012 and r.prev_sp == 100_000_010, r.prev_sp
    # 빠진 사람이 둘이고 그 자리 사람이 안 빠졌으면 모른다
    amb = {**_BASE, 20: 12, 19: 13, 18: 11}    # 11번이 20 → 18 · 9·10 번이 빠짐
    r = next(x for x in _rep((100, _BASE), (100, amb)).arrivals if x.sp_id == 100_000_012)
    assert r.prev_sp is None, r.prev_sp
    # 창 전체에서 바뀐 사람 — 뒤 창 안의 다른 교체까지
    later = _rep((100, _BASE), (60, _swap(20, 12)), (60, {**_swap(20, 12), 19: 13}))
    r = next(x for x in later.arrivals if x.sp_id == 100_000_012)
    assert r.together == 0 and r.window_changed == 1, (r.together, r.window_changed)


def test_arrival_previous_player_ignores_one_game_rotation():
    # 직전 한 판만 대타가 뛰었다 — 앞 선수는 그 자리 주전이고 대타가 아니다
    rep = _rep((150, _BASE), (1, _swap(20, 60)), (120, _swap(20, 12)))
    r = next(x for x in rep.arrivals if x.sp_id == 100_000_012)
    assert r.prev_sp == 100_000_011, r.prev_sp
    # 직전 한 판만 5명을 쉬게 했다 — 돌아온 주전은 "같이 바뀐 선수" 가 아니다 · 스쿼드 교체도 아니다
    rest = {**_BASE, 16: 31, 17: 32, 18: 33, 19: 34, 15: 35}
    r = next(x for x in _rep((150, _BASE), (1, rest), (120, _swap(20, 12))).arrivals if x.sp_id == 100_000_012)
    assert r.together == 0 and not r.squad_swap, (r.together, r.squad_swap)
    # 그 자리 사람이 자리만 옮겼을 때 — 빠진 주전을 찾는다. 한 판 뛴 대타는 "빠진 주전" 이 아니다
    shift = {**_BASE, 20: 12, 19: 11}                # 11번이 20 → 19 · 10번이 빠짐
    r = next(x for x in _rep((150, _BASE), (1, {**_BASE, 18: 60}), (120, shift)).arrivals if x.sp_id == 100_000_012)
    assert r.prev_sp == 100_000_010, r.prev_sp
    # 최근 판에 없던 사람은 센다 · 배선 — 창을 줄이면 오래 쉰 주전도 새로 든 사람이 된다
    keep = config.ARRIVAL_RECENT
    try:
        config.ARRIVAL_RECENT = 1
        r = next(x for x in _rep((150, _BASE), (1, rest), (120, _swap(20, 12))).arrivals if x.sp_id == 100_000_012)
        assert r.together == 5 and r.squad_swap, (r.together, r.squad_swap)
    finally:
        config.ARRIVAL_RECENT = keep


def test_arrival_rows_are_newest_first():
    rep = _rep((120, _BASE), (120, _swap(20, 12)), (120, _swap(19, 13, _swap(20, 12))))
    assert [r.sp_id for r in rep.arrivals] == [100_000_013, 100_000_012], [r.sp_id for r in rep.arrivals]
    a = {**_BASE, 10: 2, 11: 1}    # 앞번호 사람이 먼저 옮긴다 — 안 정렬하면 옛 줄이 앞에 온다
    b = {**a, 17: 9, 18: 8}
    m = _rep((120, _BASE), (120, a), (120, b)).moves
    assert [x.at for x in m] == sorted((x.at for x in m), reverse=True) and m[0].at > m[-1].at, [x.at for x in m]


def test_arrival_z_none_when_flat_and_inside():
    flat = _rep((100, _BASE, lambda k: 0), (100, _swap(20, 12), lambda k: 0)).arrivals[0]
    assert flat.z is None and not flat.outside, "차이도 변화도 없는데 범위 밖"
    near = _rep((200, _BASE), (200, _swap(20, 12))).arrivals[0]
    assert near.z is not None and abs(near.z) < 1 and not near.outside
    keep = config.ARRIVAL_Z
    try:
        r = _rep((150, _BASE, lambda k: k % 2), (120, _swap(20, 12), lambda k: 2 + k % 2)).arrivals[0]
        assert r.outside
        config.ARRIVAL_Z = abs(r.z) + 1          # 배선 — 문턱을 값 위로 올리면 안으로
        assert not r.outside
        config.ARRIVAL_Z = abs(r.z)              # 경계 — 같으면 밖
        assert r.outside
    finally:
        config.ARRIVAL_Z = keep


def test_move_rows():
    swap_pos = {**_BASE, 19: 11, 20: 10}         # 10번·11번이 자리를 맞바꿈(포메이션 그대로)
    rep = _rep((120, _BASE), (130, swap_pos))
    assert rep.arrivals == [] and len(rep.moves) == 2, rep.moves
    m = {r.sp_id: r for r in rep.moves}[100_000_011]
    assert (m.from_pos, m.to_pos, m.before.games, m.after.games, m.together) == (20, 19, 120, 130, 0), m
    assert m.z is not None
    # 앞이나 뒤가 문턱 밑이면 없다
    assert not _rep((99, _BASE), (130, swap_pos)).moves
    assert not _rep((120, _BASE), (99, swap_pos)).moves
    # 깜빡임(BLIP 미만)은 앞 구간에 합친다 · 정확히 BLIP 이면 이동
    blip = config.ARRIVAL_BLIP
    assert not _rep((120, _BASE), (blip - 1, swap_pos), (120, _BASE)).moves, "깜빡임을 이동으로 셌다"
    rows = _rep((120, _BASE), (blip, swap_pos), (120, _BASE)).moves
    assert rows == [], "앞뒤 다 100판이 아니라 짧은 구간 자체는 줄이 안 된다"
    # 깜빡임은 구간을 안 끊는다 — 앞 구간 60 + 깜빡 + 60 이 합쳐져 뒤 120 과 견줘진다(안 합치면 앞이 60 이라 줄이 없다)
    joined = _rep((60, _BASE), (blip - 1, swap_pos), (60, _BASE), (120, swap_pos)).moves
    assert len(joined) == 2 and joined[0].before.games == 120 + blip - 1, [(m.sp_id, m.before.games) for m in joined]
    # 정확히 BLIP 이면 깜빡임이 아니다 — 구간이 끊겨 앞이 60 씩이라 줄이 없다
    assert not _rep((60, _BASE), (blip, swap_pos), (60, _BASE), (120, swap_pos)).moves, "BLIP 경계"
    # 포메이션이 바뀐 판은 뺀다 — 자리 번호만 달라진 것일 수 있다
    other_shape = {k if k != 19 else 5: v for k, v in _BASE.items()}
    assert set(other_shape) != set(_BASE)
    assert not _rep((120, _BASE), (120, other_shape)).moves, "포메이션 변경을 자리 이동으로 셌다"
    # 결장·깜빡임을 사이에 두고 포메이션이 바뀌어도 자리 이동이 아니다 — 앞 자리에서 실제로 뛴 마지막 판과 견준다
    shape = {k if k != 20 else 6: v for k, v in _BASE.items()}     # 11번 자리 번호만 20 → 6
    without = {k: (v if v != 11 else 70) for k, v in shape.items()}
    assert not _rep((120, _BASE), (3, without), (120, shape)).moves, "결장 뒤 포메이션 변경을 자리 이동으로 셌다"
    flick = {**shape, 6: 10, 19: 11}                               # 새 포메이션 첫 2판만 10·11 번이 다른 자리
    assert not _rep((120, _BASE), (2, flick), (120, shape)).moves, "깜빡임 뒤 포메이션 변경을 자리 이동으로 셌다"
    # 같이 바뀜 — 직전 한 판 로테이션을 세지 않는다
    rest = {**_BASE, 16: 31, 17: 32, 18: 33}
    mv = next(x for x in _rep((120, _BASE), (1, rest), (120, swap_pos)).moves if x.sp_id == 100_000_011)
    assert mv.together == 0, mv.together
    # 창 — 앞뒤 400판까지
    big = _rep((500, _BASE), (450, swap_pos)).moves[0]
    assert (big.before.games, big.after.games) == (400, 400)


# ── 포메이션을 바꾼 전후(97 X2) ───────────────────────────────────
_ALT = {(5 if k == 11 else k): v for k, v in _BASE.items()}     # 2번 선수만 수미(11) → 수비(5): 0-2-5-3-1 → 1-1-5-3-1
_FORM_A, _FORM_B = "0-2-5-3-1", "1-1-5-3-1"


def _frep(*parts):
    return st.formation_report(_games(*parts), "me")


def test_formation_basic_row_by_hand():
    # 앞 120판 득실 (0,1 반복) · 뒤 130판 (2,3 반복) — 손으로 센 값
    rep = _frep((120, _BASE, lambda k: k % 2), (130, _ALT, lambda k: 2 + k % 2))
    assert rep.games == 250 and rep.changes == 1 and len(rep.rows) == 1, rep
    r = rep.rows[0]
    assert (r.from_form, r.to_form) == (_FORM_A, _FORM_B), (r.from_form, r.to_form)
    assert (r.before.games, r.after.games) == (120, 130)
    assert (r.before.gf, r.after.gf) == (60, 130 // 2 * 2 + 130 // 2 * 3), (r.before.gf, r.after.gf)   # 앞 1골 60번 · 뒤 2골 65번 + 3골 65번
    assert (r.before.win, r.before.draw, r.after.win) == (60, 60, 130)
    assert r.together == 0, "선수는 그대로인데 같이 바뀜이 셌다"
    va, vb = 0.25 * 130 / 129, 0.25 * 120 / 119
    want = (2.5 - 0.5) / (va / 130 + vb / 120) ** 0.5
    assert abs(r.z - want) < 1e-9 and r.outside, (r.z, want)


def test_formation_row_time_is_first_game_of_new_formation():
    # 판 i 는 2026-01-01 + i 시간(KST 로 읽은 값 + 9시간 — 테스트 자료의 원문에 오프셋이 없어 읽는 쪽이 9시간을 더한다)
    r = _frep((60, _BASE), (60, _ALT))
    assert len(r.rows) == 1
    first_new = matchtime_of(60)
    assert r.rows[0].at == first_new, (r.rows[0].at, first_new)


def matchtime_of(i):
    import matchtime
    return matchtime.parse((datetime(2026, 1, 1) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S"))


def test_formation_min_games_is_exact_and_wired():
    mn = config.X2_MIN_GAMES
    assert len(_frep((mn, _BASE), (mn, _ALT)).rows) == 1, "앞뒤 정확히 X2_MIN_GAMES 판은 줄을 낸다"
    assert not _frep((mn, _BASE), (mn - 1, _ALT)).rows, "뒤가 한 판 모자란데 줄이 났다"
    assert not _frep((mn - 1, _BASE), (mn, _ALT)).rows, "앞이 한 판 모자란데 줄이 났다"
    assert _frep((mn - 1, _BASE), (mn, _ALT)).changes == 1, "줄이 빠져도 바뀐 횟수는 센다"
    old = config.X2_MIN_GAMES
    try:
        config.X2_MIN_GAMES = 30      # 기본값이 아닌 값 — 배선이 끊기면 아래가 비어 FAIL
        assert len(_frep((30, _BASE), (30, _ALT)).rows) == 1, "문턱이 설정과 이어져 있지 않다"
        assert not _frep((29, _BASE), (30, _ALT)).rows
    finally:
        config.X2_MIN_GAMES = old
    assert config.X2_MIN_GAMES < config.ARRIVAL_MIN_GAMES, "영입 전후 문턱을 그대로 쓰면 줄이 대부분 빠진다(실측)"
    big = _frep((500, _BASE), (450, _ALT)).rows[0]
    assert (big.before.games, big.after.games) == (config.ARRIVAL_WINDOW,) * 2, "창은 ARRIVAL_WINDOW 판까지"


def test_formation_window_cap_keeps_the_games_nearest_the_change():
    w = config.ARRIVAL_WINDOW
    # 앞 구간은 끝에서 w 판(바꾸기 바로 앞) · 뒤 구간은 처음 w 판(바꾼 바로 뒤) — 멀리 있는 판은 값이 달라 자리가 바뀌면 골 합이 다르다
    a, b = (w + 50, _BASE, lambda k: 0 if k < 50 else 1), (w + 50, _ALT, lambda k: 1 if k < w else 5)
    r = _frep(a, b).rows[0]
    assert (r.before.games, r.after.games) == (w, w)
    assert (r.before.gf, r.after.gf) == (w, w), "앞 창은 구간 끝 w 판 · 뒤 창은 구간 처음 w 판이어야 한다"


def test_formation_after_window_stays_inside_the_run():
    # A(120) → B(60) → A(120): 뒤 창이 다음 구간으로 넘어가면 A→B 줄의 뒤가 180판이 된다
    rep = _frep((120, _BASE), (60, _ALT), (120, _BASE))
    assert rep.changes == 2 and len(rep.rows) == 2, rep
    new, old = rep.rows                      # 최신순
    assert (old.from_form, old.to_form) == (_FORM_A, _FORM_B) and (new.from_form, new.to_form) == (_FORM_B, _FORM_A)
    assert (old.before.games, old.after.games) == (120, 60), "뒤 창이 다음 포메이션 구간으로 넘어갔다"
    assert (new.before.games, new.after.games) == (60, 120), "앞 창이 그 앞 구간으로 넘어갔다"
    assert new.at > old.at, "최신순이 아니다"


def test_formation_blips_join_the_run_but_stay_out_of_the_windows():
    k4 = config.K4_MIN_RUN
    # 깜빡임(K4_MIN_RUN 미만) — 구간을 안 끊는다
    assert not _frep((120, _BASE), (k4 - 1, _ALT), (120, _BASE)).rows
    assert _frep((120, _BASE), (k4 - 1, _ALT), (120, _BASE)).changes == 0
    # 구간 끝에 붙은 깜빡임 판은 창에 안 들어간다(구간의 시각 범위 안이지만 다른 포메이션)
    rep = _frep((60, _ALT), (120, _BASE), (k4 - 1, _ALT))
    assert len(rep.rows) == 1 and rep.rows[0].after.games == 120, [(r.before.games, r.after.games) for r in rep.rows]
    rep = _frep((120, _BASE), (k4 - 1, _ALT), (30, _BASE), (60, _ALT))
    assert len(rep.rows) == 1 and (rep.rows[0].before.games, rep.rows[0].after.games) == (150, 60), \
        [(r.before.games, r.after.games) for r in rep.rows]


def test_formation_aligns_by_time_when_lineups_are_fewer_than_games():
    # 선발 11명이 아니거나 승·무·패가 아닌 판은 _lineups 에 없다 — 번호가 아니라 시각으로 맞춰야 앞 창이 제 판만 센다
    out, i = [], 0
    for n, slots, res in ((60, _BASE, None), (30, _BASE, "오류"), (60, _ALT, None), (20, _ALT, "오류")):
        for k in range(n):
            out.append(_game(i, slots, k % 3 - 1, res))
            i += 1
    rep = st.formation_report(list(reversed(out)), "me")
    assert rep.games == 120 and len(rep.rows) == 1, rep
    r = rep.rows[0]
    assert (r.before.games, r.after.games) == (60, 60), "오류 판이 창에 섞였거나 번호가 어긋났다"
    assert r.before.gf == sum(max(k % 3 - 1, 0) for k in range(60)), "앞 창이 엉뚱한 판을 셌다"


def test_formation_together_counts_new_starters_only():
    alt_new = {**_ALT, 20: 12}                       # 포메이션이 바뀌며 12번 선수가 처음 든다
    r = _frep((60, _BASE), (60, alt_new)).rows[0]
    assert r.together == 1, r.together
    # 직전 ARRIVAL_RECENT 판 안에 뛴 적 있는 선수는 새로 든 것이 아니다(바뀌기 5판 전부터 12번이 뛰었다)
    r = _frep((50, _BASE), (5, {**_BASE, 20: 12}), (60, alt_new)).rows[0]
    assert r.together == 0, r.together
    assert config.ARRIVAL_RECENT > 5


def test_formation_flat_and_empty_inputs():
    flat = _frep((60, _BASE, lambda k: 0), (60, _ALT, lambda k: 0)).rows[0]
    assert flat.z is None and not flat.outside, "득실차가 하나도 안 움직였는데 범위 밖이다"
    assert st.formation_report([], "me").rows == [] and st.formation_report([], "me").games == 0
    assert _frep((60, _BASE), (60, _ALT)).rows
    assert st.formation_report(_games((60, _BASE), (60, _ALT)), "남").rows == [], "내가 안 낀 경기"
    assert not _frep((200, _BASE)).rows and _frep((200, _BASE)).changes == 0


def test_formation_row_outside_boundary_is_exactly_arrival_z():
    w = st.Window()
    row = lambda z: st.FormationRow(T0, "a", "b", w, w, 0, z)   # noqa: E731
    z = config.ARRIVAL_Z
    assert row(z).outside and row(-z).outside, "정확히 문턱이면 범위 밖"
    assert not row(z - 1e-9).outside and not row(-z + 1e-9).outside and not row(None).outside


def test_formation_budget_10k():
    parts = [(1000, _BASE if j % 2 == 0 else _ALT) for j in range(10)]
    games = _games(*parts)
    t = time.perf_counter()
    rep = st.formation_report(games, "me")
    dt = time.perf_counter() - t
    print(f"       포메이션 전후 1만 경기: {dt * 1000:.0f}ms · 줄 {len(rep.rows)}")
    assert len(rep.rows) == 9 and dt < 1.0, (len(rep.rows), dt)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
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
