"""카드 정보 화면 재료(68 일부) — 지어낸 경기·카드 정보(네트워크 없음). `python tests/test_cardviews.py`"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import cardcollect as cc  # noqa: E402
import cardstats  # noqa: E402
import cardviews as cv  # noqa: E402
import config  # noqa: E402
import notice  # noqa: E402
import store  # noqa: E402
import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패)

GRADE = 5
ADAPT = 4   # 적응도 5 의 몫(122 R4 실측) — 상수를 읽지 않고 손으로 적는다
BONUS = config.GRADE_OVR_BONUS[GRADE] + ADAPT
POS = next(p for p in sorted(config.SIM_POS_OF) if p != 28)   # 시뮬레이터 칸이 있는 자리
CELL = config.SIM_POS_OF[POS]


def extra(ovr=100, **kw):
    return {"pos_ovr": json.dumps({CELL: ovr}), "height": 180, "body": "보통", **kw}


def side(spids, grade=GRADE, ouid="me", pos=POS):
    return {"ouid": ouid, "player": [{"spId": s, "spPosition": pos, "spGrade": grade} for s in spids]
            + [{"spId": 999, "spPosition": cardstats.SUB_POSITION, "spGrade": grade}]}


def game(spids, **kw):
    return {"matchInfo": [side(spids, **kw), side(range(500, 511), ouid="상대")]}


ELEVEN = list(range(1, 12))


def test_adapt_is_added_everywhere_from_one_constant():
    """적응도 몫은 상수 하나에서 — 기본값이 아닌 값으로 바꾸면 선수 지표 · 스쿼드 OVR · 판정식이 같이 움직인다. 말풍선의 숫자도 같다."""
    import glossary
    import methods
    ex = {7: extra(120)}
    keep = config.ADAPT_OVR
    try:
        config.ADAPT_OVR = 9
        assert cardstats.effective_ovr(POS, 1, ex[7]) == (129, "")
        assert cv.player_card(7, POS, 1, ex).ovr == 129
        assert "(+9)" in methods.page_html(), "산출 방법이 상수를 안 읽는다"
    finally:
        config.ADAPT_OVR = keep
    assert config.ADAPT_OVR == ADAPT and cardstats.effective_ovr(POS, 1, ex[7])[0] == 124
    assert f"카드 OVR보다 {ADAPT} 높습니다" in glossary.TERMS["effective_ovr"][1]


def test_player_card_values_and_unknown():
    ex = {7: extra(120, height=191, body="마름")}
    assert cv.player_card(7, POS, GRADE, ex) == cv.PlayerCard(120 + BONUS, 191, "마름")
    assert cv.player_card(8, POS, GRADE, ex) == cv.PlayerCard(), "카드 정보가 없는 카드"
    # 자리·강화를 몰라도 키·체형은 안다 · 빈 값은 None(빈 칸을 채우지 않는다)
    assert cv.player_card(7, cardstats.SUB_POSITION, GRADE, ex).ovr is None
    assert cv.player_card(7, POS, 99, ex) == cv.PlayerCard(None, 191, "마름")
    assert cv.player_card(1, POS, GRADE, {1: {"pos_ovr": None, "height": 0, "body": ""}}) == cv.PlayerCard()


def test_squad_ovr_by_hand():
    ex = {s: extra(100 + s) for s in ELEVEN}
    ex.update({s: extra(110) for s in range(20, 31)})
    first = sum(100 + s for s in ELEVEN) / 11 + BONUS          # 106 + 보너스
    second = 110 + BONUS
    r = cv.squad_ovr([game(ELEVEN), game(list(range(20, 31)))], "me", ex)
    assert (r.games, r.known) == (2, 2), r
    assert abs(r.avg - (first + second) / 2) < 1e-9, r
    want_sd = (((first - r.avg) ** 2 + (second - r.avg) ** 2) / 1) ** 0.5
    assert abs(r.sd - want_sd) < 1e-9, (r.sd, want_sd)


def test_squad_ovr_same_as_measure_formula():
    # 측정 도구가 쓰는 식과 어긋나면 앱의 Δ 가 측정과 달라진다 — 판마다 같은 값이어야 한다
    ex = {s: extra(90 + s * 2) for s in range(1, 40)}
    lineups = [list(range(k, k + 11)) for k in (1, 5, 13, 25)]
    for lu in lineups:
        for grade in (1, GRADE, 8):
            g = game(lu, grade=grade)
            one = cv.squad_ovr([g], "me", ex)
            want, _why = cardstats.lineup_strength(g["matchInfo"][0], ex)
            assert one.avg == want, (lu, grade, one.avg, want)


def test_squad_ovr_skips_unknown_games():
    ex = {s: extra(100) for s in ELEVEN}
    missing = ELEVEN[:-1] + [77]          # 한 장 모름 — 그 판은 뺀다(옛값으로 채우지 않는다)
    ten = ELEVEN[:-1]                     # 10장뿐 — 안 센다
    r = cv.squad_ovr([game(ELEVEN), game(missing), game(ten)], "me", ex)
    assert (r.games, r.known) == (3, 1) and r.avg == 100 + BONUS and r.sd is None, r
    empty = cv.squad_ovr([game(missing)], "me", ex)
    assert (empty.games, empty.known, empty.avg, empty.sd) == (1, 0, None, None), empty
    assert cv.squad_ovr([], "me", ex) == cv.SquadOvr()
    assert cv.squad_ovr([game(ELEVEN)], "남", ex).games == 0, "내가 안 낀 경기를 셌다"
    # 교체 명단(999)은 11장에 안 든다 — 들면 12장이라 모두 빠진다
    assert 999 not in cv.lineup_cards([game(ELEVEN)], "me")
    assert cv.lineup_cards([game(ELEVEN), game([40])], "me") == set(ELEVEN) | {40}


def test_squad_ovr_budget_many_games():
    import time
    ex = {s: extra(100 + s % 9) for s in range(1, 60)}
    games = [game(list(range(1 + i % 40, 12 + i % 40))) for i in range(20000)]
    t = time.perf_counter()
    r = cv.squad_ovr(games, "me", ex)
    ms = (time.perf_counter() - t) * 1000
    print(f"       2만 경기: {ms:.0f}ms")
    assert r.known == 20000 and ms < 1500, (r.known, ms)


def test_store_load_card_extras():
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            assert store.load_card_extras(conn, []) == {}
            ids = list(range(1000, 1000 + store.SQL_IN_CHUNK + 5))
            conn.executemany("INSERT INTO card_extra (spid, height, weight, body, pos_ovr) VALUES (?, ?, ?, ?, ?)",
                             [(s, 180, 70, "보통", json.dumps({CELL: 100})) for s in ids])
            conn.commit()
            got = store.load_card_extras(conn, ids + [5])
            assert set(got) == set(ids), "묶음 경계에서 빠졌거나 없는 카드가 들어왔다"
            assert got[1000]["height"] == 180 and got[1000]["body"] == "보통" and cardstats.pos_ovrs(got[1000]) == {CELL: 100}
        finally:
            conn.close()


# ── 68 C5 · C6 ──
from datetime import date, timedelta  # noqa: E402


def dated(spids, raw, grades=None):
    """raw = 넥슨 원문(UTC) — KST 로 뜻한 시각에서 글자 그대로 9시간 뺀다(test_rules 63)."""
    g = game(spids)
    g["matchDate"] = raw
    if grades:
        for p in g["matchInfo"][0]["player"]:
            p["spGrade"] = grades.get(p["spId"], p["spGrade"])
    return g


D1, D2 = date(2026, 11, 1), date(2026, 11, 8)


def test_c5_breakdown_by_hand():
    # 11-01 KST 12:00 선발 1~11 · 11-08 KST 12:00 선발: 1~8 그대로 · 9 를 강화만 올림 · 10·11 을 12·13 으로
    g1 = dated(ELEVEN, "2026-11-01T03:00:00")
    g2 = dated([*range(1, 10), 12, 13], "2026-11-08T03:00:00", grades={9: GRADE + 1})
    prices = {(s, GRADE): {"2026-11-01": 100, "2026-11-08": 110} for s in range(1, 9)}   # 8장 × +10
    prices[(9, GRADE)] = {"2026-11-01": 200}
    prices[(9, GRADE + 1)] = {"2026-11-08": 500}       # 강화만 올림 → 보강 +300
    prices[(10, GRADE)] = {"2026-11-01": 50}
    prices[(11, GRADE)] = {"2026-11-01": 60}           # 뺀 카드 −110
    prices[(12, GRADE)] = {"2026-11-08": 70}           # 새 카드 +70 · 13 은 시세 모름
    vb = cv.value_breakdown(cv.squads_by_day([g2, g1], "me"), prices, D1, D2)
    assert (vb.price, vb.squad, vb.unknown) == (80, 500 - 200 + 70 - 110, 1), vb
    assert vb.total == vb.price + vb.squad, "항등식 — 가격 + 보강 = 아는 카드 총변화"
    assert (vb.v1, vb.v2) == (800 + 200 + 50 + 60, 880 + 500 + 70), vb
    assert (vb.kept, vb.added, vb.removed) == (8, 3, 3), vb
    assert (vb.game1, vb.game2) == (D1, D2)


def test_c5_identity_random():
    import random
    rnd = random.Random(7)
    for _ in range(200):
        a = rnd.sample(range(1, 30), 11)
        b = rnd.sample(range(1, 30), 11)
        prices = {}
        for s in range(1, 30):
            obs = {}
            if rnd.random() < 0.8:
                obs["2026-11-01"] = rnd.randint(1, 10 ** 6)
            if rnd.random() < 0.8:
                obs["2026-11-08"] = rnd.choice([None, rnd.randint(1, 10 ** 6)])
            prices[(s, GRADE)] = obs
        vb = cv.value_breakdown(cv.squads_by_day([dated(a, "2026-11-01T03:00:00"), dated(b, "2026-11-08T03:00:00")], "me"), prices, D1, D2)
        assert vb.total == vb.price + vb.squad, vb
        assert vb.kept + vb.added + vb.removed >= vb.unknown


def test_c5_nearest_price_and_last_game():
    # 고른 날 관측이 없으면 앞 3일까지 · 4일 앞은 모름 · 같은 날 두 판이면 늦은 판
    early = dated(range(100, 111), "2026-11-07T23:00:00")   # KST 11-08 08:00
    late = dated(ELEVEN, "2026-11-08T12:00:00")             # KST 11-08 21:00
    base = dated(ELEVEN, "2026-11-01T03:00:00")
    prices = {(s, GRADE): {"2026-11-01": 100, "2026-11-05": 130} for s in range(1, 11)}
    prices[(11, GRADE)] = {"2026-11-01": 100, "2026-11-04": 999}   # 11-08 에서 4일 앞 — 못 쓴다
    short = dated(range(200, 210), "2026-11-08T13:00:00")   # KST 22:00 — 10장(몰수 등)이면 그날 선발로 안 친다
    vb = cv.value_breakdown(cv.squads_by_day([late, early, base, short], "me"), prices, D1, D2)
    assert vb.kept == 11 and vb.added == 0, "같은 날 늦은 판의 선발이어야 — 이른 판(100~110)·10장 판이 아니다"
    assert vb.price == 10 * 30 and vb.unknown == 1, vb
    # 그날 경기가 없으면 그 앞 가장 가까운 날의 선발
    vb2 = cv.value_breakdown(cv.squads_by_day([base], "me"), prices, D1, D2)
    assert vb2.game2 == D1 and vb2.kept == 11
    assert cv.value_breakdown(cv.squads_by_day([base], "me"), prices, date(2026, 10, 1), D2).game1 is None


def _c6_rows(today, pairs, gap=7, base=1000, pct=10.0, start=0):
    rows = []
    for i in range(pairs):
        d0 = today - timedelta(days=gap)
        rows += [(start + i, 1, d0.isoformat(), base), (start + i, 1, today.isoformat(), int(base * (1 + pct / 100)))]
    return rows


def test_c6_gap_rule_and_median():
    t = date(2026, 12, 10)
    rows = []
    for gap, s in ((6, 1), (7, 2), (10, 3), (11, 4)):
        rows += [(s, 1, (t - timedelta(days=gap)).isoformat(), 1000), (s, 1, t.isoformat(), 1200)]
    rows += [(5, 1, (t - timedelta(days=8)).isoformat(), None), (5, 1, t.isoformat(), 1500)]   # 매물 없음 → 안 씀
    rows += [(6, 1, (t - timedelta(days=8)).isoformat(), 1000), (6, 1, t.isoformat(), 900)]
    rows += [(6, 2, (t - timedelta(days=8)).isoformat(), 1000), (6, 2, t.isoformat(), 3000)]   # 다른 강화는 다른 쌍
    idx = cv.price_index(rows, t)
    assert [n for _d, _m, n in idx.days] == [4], idx.days   # 7·10일(2·3) + 6번 두 강화 — 6·11일·매물 없음은 뺀다
    assert abs(idx.days[0][1] - 20.0) < 1e-9, ("중앙값 — -10 · 20 · 20 · 200", idx.days)
    assert [(m.spid, m.grade) for m in idx.up] == [(6, 2), (2, 1), (3, 1)], idx.up   # 같은 % 는 spid 순
    assert [(m.spid, m.grade) for m in idx.down] == [(6, 1)]
    assert not idx.ready and idx.recent == 4


def test_c6_ready_threshold_and_top_n():
    t = date(2026, 12, 10)
    need = config.C6_MIN_CARDS
    rows = _c6_rows(t, need - 1, pct=5) + _c6_rows(t, 30, pct=-5, start=10_000)
    assert cv.price_index(_c6_rows(t, need - 1), t).ready is False
    assert cv.price_index(_c6_rows(t, need), t).ready is True, "문턱과 같으면 낸다(이상)"
    idx = cv.price_index(rows, t)
    assert idx.ready and idx.recent == need + 29
    assert len(idx.up) == config.C6_TOP_N and len(idx.down) == config.C6_TOP_N
    # 최근 7일 밖 관측은 문턱에 안 센다
    old = _c6_rows(t - timedelta(days=config.C6_RECENT_DAYS), need + 50)
    assert cv.price_index(old, t).recent == 0


def test_c6_wait_days_from_real_observations():
    t = date(2026, 11, 26)
    need = config.C6_MIN_CARDS
    one = [(s, 1, (t - timedelta(days=2)).isoformat(), 1000) for s in range(need)]   # 이틀 전 첫 관측 — 5일 뒤 짝이 생긴다
    idx = cv.price_index(one, t)
    assert not idx.ready and idx.wait_days == config.C6_GAP_MIN - 2, idx.wait_days
    assert cv.price_index(one[:need - 1], t).wait_days is None, "모자라면 날을 지어내지 않는다"
    assert cv.price_index([], t).wait_days is None


# ── 113 M3 · N21 ──
def test_m3_value_series_by_hand():
    # 11-01 선발 1~11 · 11-08 부터 11 을 12 로. 시세는 (카드, 강화)마다
    base, late = dated(ELEVEN, "2026-11-01T03:00:00"), dated([*range(1, 11), 12], "2026-11-08T03:00:00")
    prices = {(s, GRADE): {"2026-10-31": 1, "2026-11-01": 100, "2026-11-05": 110, "2026-11-08": 120} for s in range(1, 11)}
    prices[(11, GRADE)] = {"2026-11-01": 100, "2026-11-03": 300}
    prices[(12, GRADE)] = {"2026-11-08": None, "2026-11-09": 700}
    prices[(99, GRADE)] = {"2026-11-20": 5}          # 쓴 적 없는 카드만 읽은 날 — 세지 않는다
    vs = cv.value_series(cv.squads_by_day([late, base], "me"), prices)
    assert vs.days == [(date(2026, 11, 1), 1100),                 # 10-31 은 경기 전이라 선발이 없다
                       (date(2026, 11, 3), 1000 + 300),           # 11 만 읽은 날 — 나머지는 앞 3일 안 값
                       (date(2026, 11, 5), 1100 + 300),           # 경기 없는 날은 앞 선발 그대로 · 11 은 이틀 앞 값
                       (date(2026, 11, 9), 1200 + 700)], vs.days
    assert vs.skipped == 1, "11-08 은 12 가 매물 없음이라 뺀 날(부분합을 안 찍는다)"
    # 앞 C5_NEAREST_MAX_DAYS 일을 넘으면 모름
    far = {(s, GRADE): {"2026-11-01": 100} for s in range(1, 12)}
    far[(1, GRADE)]["2026-11-05"] = 100
    vs = cv.value_series(cv.squads_by_day([base], "me"), far)
    assert [d for d, _v in vs.days] == [date(2026, 11, 1)] and vs.skipped == 1, vs
    assert cv.value_series(cv.squads_by_day([], "me"), prices) == cv.ValueSeries() and cv.value_series(cv.squads_by_day([base], "남"), prices).days == []


def so_game(gk, won, mid="m", grade=GRADE, res=None):
    me = {"ouid": "me", "matchDetail": {"matchResult": res or ("승" if won else "패"), "matchEndType": 0},
          "shoot": {"shootOutScore": 4}}
    opp = {"ouid": "x", "matchDetail": {"matchResult": "패" if won else "승", "matchEndType": 0},
           "shoot": {"shootOutScore": 3}, "player": [{"spId": gk, "spPosition": 0, "spGrade": grade}]}
    return {"matchId": mid, "matchInfo": [me, opp]}


def gk_extra(ovr, height):
    return {"pos_ovr": json.dumps({config.SIM_POS_OF[0]: ovr}), "height": height}


def test_n21_split_by_hand():
    keep = config.SHOOTOUT_SPLIT_MIN
    try:
        config.SHOOTOUT_SPLIT_MIN = 2
        ex = {1: gk_extra(100, 180), 2: gk_extra(100, 188), 3: gk_extra(120, 190), 4: {"pos_ovr": None, "height": 0}}
        games = [so_game(1, True), so_game(1, False), so_game(2, True), so_game(3, False), so_game(3, True),
                 so_game(4, True), so_game(5, True), so_game(1, True, res="오류")]   # 4·5 는 카드를 모른다 · 오류는 승부차기가 아니다
        rows, total = cv.shootout_splits(games, "me", ex, {1: 20, 2: 22, 3: 24, 4: 0})
        assert total == 7, total
        got = {r.key: r for r in rows}
        assert list(got) == ["height", "ovr", "salary"]
        h = got["height"]
        assert (h.cut, h.low, h.high, h.known) == (188, (2, 1), (1, 1), 5), h   # 가운데 값은 이하에 든다
        o = got["ovr"]
        assert (o.cut, o.low, o.high, o.known) == (100 + BONUS, (2, 1), (1, 1), 5), o
        s = got["salary"]
        assert (s.cut, s.low, s.high, s.known) == (22, (2, 1), (1, 1), 5), s   # 급여 0 은 모름
        # 강화가 다르면 실효 OVR 이 갈린다 — 카드 번호만 보면 한 칸이 빈다
        mixed = [so_game(1, True, grade=1), so_game(1, True, grade=1), so_game(1, True, grade=8), so_game(1, False, grade=8)]
        rows, _n = cv.shootout_splits(mixed, "me", ex, {})
        assert [r.key for r in rows] == ["ovr"] and rows[0].cut == 100 + config.GRADE_OVR_BONUS[1] + ADAPT, rows
        assert (rows[0].low, rows[0].high) == ((2, 0), (1, 1))
        # 짝수면 아래쪽 실제 값(189.0 같은 없는 키를 안 보인다)
        even = [so_game(1, True), so_game(1, True), so_game(3, True), so_game(3, True)]
        assert cv.shootout_splits(even, "me", ex, {})[0][0].cut == 180
        assert cv.shootout_splits([], "me", ex, {}) == ([], 0)
    finally:
        config.SHOOTOUT_SPLIT_MIN = keep


def test_n21_min_games_both_sides():
    n = config.SHOOTOUT_SPLIT_MIN
    ex = {1: gk_extra(100, 180), 2: gk_extra(100, 190)}
    lows = [so_game(1, i % 2 == 0) for i in range(n + 5)]
    assert [r.key for r in cv.shootout_splits(lows + [so_game(2, True)] * n, "me", ex, {})[0]] == ["height"], "문턱과 같으면 낸다"
    assert cv.shootout_splits(lows + [so_game(2, True)] * (n - 1), "me", ex, {})[0] == []


def test_n21_color_threshold_is_divided_by_three():
    ex = {1: gk_extra(100, 180), 2: gk_extra(100, 190)}

    def run(w1, n1, w2, n2):
        games = [so_game(1, i < w1) for i in range(n1)] + [so_game(2, i < w2) for i in range(n2)]
        return next(r for r in cv.shootout_splits(games, "me", ex, {})[0] if r.key == "height")
    # 손계산: 60/100 대 45/100 → z = 0.15 / sqrt(0.525·0.475·0.02) = 2.124 → p 0.0337 (0.05 밑 · 0.0167 위)
    r = run(60, 100, 45, 100)
    assert abs(r.p - 0.0337) < 0.0005 and r.better == "", ("셋을 함께 보는 문턱은 α/3", r)
    assert r.p < config.N20_ALPHA
    assert run(70, 100, 45, 100).better == "low" and run(45, 100, 70, 100).better == "high"
    assert run(100, 100, 100, 100).p == 1.0, "폭이 0 이어도 안 터진다"


def test_collecting_days_text_counts_days():
    # C5 는 날 수를 센다 — "(1/2장)" 이라고 적던 것을 고쳤다
    text = notice.card_empty_text(("collecting_days", 1, 2))
    assert "1/2일" in text and "장" not in text, text
    assert "장" in notice.card_empty_text(("collecting", 3, 200, None))


def test_card_price_rows_src_filter():
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            store.save_card_price_days(conn, [(1, 5, "2026-11-01", 100, 1), (2, 5, "2026-11-01", 200, 2),
                                              (3, 5, "2026-11-01", None, 3)])
            assert sorted(r[0] for r in store.card_price_rows(conn, 1)) == [1, 3]
            assert sorted(r[0] for r in store.card_price_rows(conn, 2)) == [2, 3]
            assert store.card_price_rows(conn, 1, {(3, 5)}) == [(3, 5, "2026-11-01", None)]
        finally:
            conn.close()


def test_screen_state_format_stop():
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        keep = config.WEB_DATA, config.card_backfill_allowed
        try:
            config.WEB_DATA = True
            config.card_backfill_allowed = lambda: True
            today = date(2026, 12, 1)
            last = cc.Status(3, 9, 0, False)
            assert cv.screen_state(conn, today, last) == ("filling", 3, 9), "막힘·형식 없으면 채우는 중"
            assert cv.screen_state(conn, today) == "first_round", "예약이 아직 한 바퀴도 안 돌았다"
            assert notice.card_empty_text("first_round"), "사유에 글이 없다"
            store.budget_mark_429(conn, today.isoformat(), f"{store.BUDGET_CARD}_format")
            assert cv.screen_state(conn, today, last) == "format"
            assert isinstance(cv.screen_state(conn, today + timedelta(days=1), last), tuple), "그날만"
            store.budget_mark_429(conn, today.isoformat(), f"{store.BUDGET_CARD}_block")
            assert cv.screen_state(conn, today, last) == "blocked", "막힘이 형식보다 먼저"
            boom = cc.demand   # 화면 스레드에서 30일 수요를 세면 0.7초 멈춘다
            cc.demand = lambda *a, **k: (_ for _ in ()).throw(AssertionError("수요를 셌다"))
            try:
                cv.screen_state(conn, today + timedelta(days=1), last)
            finally:
                cc.demand = boom
            config.WEB_DATA = False
            assert cv.screen_state(conn, today) == "web_off", "웹 데이터 꺼짐이 먼저"
        finally:
            config.WEB_DATA, config.card_backfill_allowed = keep
            conn.close()


def test_screen_thread_never_counts_demand():
    """화면 쪽 소스가 30일 수요를 세는 함수(cardcollect.status·demand)를 부르지 않는다 — 화면 스레드에서 0.7초 멈춘다."""
    import re
    call = re.compile(r"cardcollect\.(status|demand)\(")
    assert call.search("x = cardcollect.status(conn, day)"), "심은 위반을 못 잡는다"
    root = Path(__file__).resolve().parent.parent
    for name in ("app_main.py", "dashboard.py", "cardviews.py", "core_api.py"):
        assert not call.search((root / name).read_text(encoding="utf-8")), name


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
