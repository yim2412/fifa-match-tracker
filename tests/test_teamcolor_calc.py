"""라인업으로 켜진 팀컬러(113 N29 · K2 · K4) — 손으로 센 지어낸 카드·경기(네트워크·DB 없음). `python tests/test_teamcolor_calc.py`"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import config  # noqa: E402
import habits  # noqa: E402
import teamcolor_calc as tc  # noqa: E402

A, B, DUO, TRIO, NEW = 1, 2, 10, 11, 77
META = [{"id": A, "name": "에이"}, {"id": B, "name": "비"}, {"id": DUO, "name": "듀오"}, {"id": TRIO, "name": "삼총사"}]
STEPS = [(A, 1, 3), (A, 2, 6), (A, 3, 11), (B, 1, 3), (B, 2, 6), (DUO, 1, 2), (TRIO, 1, 3)]


def ex(club=(), rel=(), sim="2026-11-01"):
    return {"sim_on": sim, "club_tc": json.dumps([[i, str(i)] for i in club]), "rel_tc": json.dumps([[i, str(i)] for i in rel])}


def mat(extras, steps=STEPS, meta=META):
    return tc.material(extras, steps, meta)


def names(lu):
    return [(a.id, a.kind, a.members, a.step) for a in lu.actives]


def look(m, ids):
    return tc.active([m.cards.get(i) for i in ids], m.need, m.names)


def test_kinds_are_separate_and_card_counts_once():
    # 11장 전부 에이 · 그중 4장은 비도 · 3장은 삼총사(관계) · 그 셋 중 둘은 듀오도
    e = {i: ex([A, B] if i <= 4 else [A], [TRIO, DUO] if i <= 2 else [TRIO] if i == 3 else []) for i in range(1, 12)}
    lu = look(mat(e), range(1, 12))
    assert names(lu) == [(A, "club", 11, 3), (TRIO, "rel", 3, 1)], names(lu)   # 비는 에이가, 듀오는 삼총사가 카드를 다 가져갔다
    assert lu.main.id == A and lu.unknown == 0 and lu.club_known
    assert lu.actives[1].idx == (0, 1, 2), lu.actives[1]
    # 통틀어 하나(측정 B 의 식)면 관계가 안 나온다 — 종류를 안 가르면 무엇이 일어났을지
    assert tc.greedy({i: set(c[0]) | set(c[1]) for i, c in mat(e).cards.items()}, mat(e).need) == [(A, 11, 3)]


def test_split_six_five_and_tie_order():
    e = {i: ex([A] if i <= 6 else [B]) for i in range(1, 12)}
    assert names(look(mat(e), range(1, 12))) == [(A, "club", 6, 2), (B, "club", 5, 1)]
    tie = {i: ex([A, B]) for i in range(1, 12)}
    assert look(mat(tie), range(1, 12)).main.id == A   # 인원 같으면 단계 높은 것(에이 3단계)
    tie6 = {i: ex([A, B]) if i <= 6 else ex() for i in range(1, 12)}
    assert look(mat(tie6), range(1, 12)).main.id == A  # 인원·단계가 같으면 id 작은 것


def test_unknown_cards_are_not_empty_cards():
    e = {i: ex([A]) for i in range(1, 11)}
    assert look(mat(e), range(1, 12)).unknown == 1                        # 백필 전
    half = {**e, 11: ex([A], sim=None)}
    assert tc.card_colors(half[11]) is None and look(mat(half), range(1, 12)).unknown == 1   # 페이지만 읽은 반쪽 카드
    broken = {**e, 11: {"sim_on": "x", "club_tc": "{", "rel_tc": "[]"}}
    assert look(mat(broken), range(1, 12)).unknown == 1
    none = {**e, 11: ex()}
    assert look(mat(none), range(1, 12)).unknown == 0, "팀컬러 없는 아는 카드"


def test_steps_unknown_is_per_kind():
    e = {i: ex([A], [TRIO] if i <= 3 else []) for i in range(1, 12)}
    full = look(mat(e), range(1, 12))
    assert not full.steps_unknown_club and not full.steps_unknown_rel
    no_a = look(mat(e, [s for s in STEPS if s[0] != A]), range(1, 12))
    assert no_a.steps_unknown_club and not no_a.steps_unknown_rel and not no_a.club_known   # 에이를 모르면 다른 것이 주 팀컬러가 된다
    no_trio = look(mat(e, [s for s in STEPS if s[0] != TRIO]), range(1, 12))
    assert no_trio.steps_unknown_rel and not no_trio.steps_unknown_club and no_trio.club_known, "관계 미상이 소속까지 막았다"
    two = {i: ex([A], [TRIO] if i <= 2 else []) for i in range(1, 12)}
    assert not look(mat(two, [s for s in STEPS if s[0] != TRIO]), range(1, 12)).steps_unknown_rel, "2장은 미상으로 안 친다"


def test_out_of_list_ids():
    gen = {i: {"sim_on": "x", "club_tc": json.dumps([[A, "에이"], [9000, "단일팀"]]), "rel_tc": "[]"} for i in range(1, 12)}
    assert look(mat(gen), range(1, 12)).club_known, "단일팀은 상세가 없는 칸이라 뺀다"
    new = {i: ex([A, NEW]) for i in range(1, 12)}
    assert look(mat(new), range(1, 12)).steps_unknown_club, "이름표에 없는 새 팀컬러가 조용히 빠졌다"
    empty = look(mat(gen, meta=[]), range(1, 12))
    assert empty.unknown == 11 and not empty.actives, "이름표가 비었는데 팀컬러 없음이라고 했다"
    # 단계표에만 있고 이름표에서 빠진 id 는 켜지지 않는다(이름이 없다)
    assert A not in mat(gen, meta=[{"id": B, "name": "비"}]).need


# ── 경기 ────────────────────────────────────────────────────────────────

T0 = datetime(2026, 11, 1, 12)
ELEVEN = range(1, 12)


def game(n, spids=ELEVEN, res="승", opp_res=None, ouid="me", subs=True):
    def sd(o, ids, r):
        ps = [{"spId": i, "spPosition": 4, "spGrade": 5} for i in ids]
        if subs:
            ps.append({"spId": 999, "spPosition": 28, "spGrade": 1})
        return {"ouid": o, "nickname": o, "division": 800, "player": ps,
                "matchDetail": {"matchResult": r, "matchEndType": 0}, "shoot": {"goalTotal": 1}}
    flip = {"승": "패", "패": "승", "무": "무"}
    return {"matchId": f"m{n:05d}", "matchDate": (T0 + timedelta(hours=n)).isoformat(), "matchType": 52,
            "matchInfo": [sd(ouid, spids, res), sd("opp", range(500, 511), opp_res or flip[res])]}


def cards():
    e = {i: ex([A]) for i in range(1, 12)}
    e.update({i: ex([B]) for i in range(21, 32)})
    e.update({i: ex() for i in range(41, 52)})
    return e


BS, NS, US = range(21, 32), range(41, 52), range(61, 72)   # 비 · 팀컬러 없음 · 모르는 카드


def combo(ds, m):
    return tc.combo_rows(ds, "me", tc.my_lineups(ds, "me"), m)


def test_combo_rows_counts():
    ds = [game(0), game(1, res="패"), game(2, BS, "무"), game(3, NS), game(4, US), game(5, range(1, 10)),
          game(6, ELEVEN, ouid="남")]
    t = combo(ds, mat(cards()))
    assert (t.known, t.total) == (4, 5), (t.known, t.total)      # 모르는 판은 분모에만 · 11장이 아닌 판과 남의 판은 밖
    got = [(r.label, r.games, r.win, r.draw, r.lose) for r in t.rows]
    assert got == [("에이", 2, 1, 0, 1), ("비", 1, 0, 1, 0), (tc.NONE_ON, 1, 1, 0, 0)], got
    assert all(isinstance(r.mixed, bool) for r in t.rows) and len({r.mixed for r in t.rows}) == 1
    a = t.rows[0]
    assert (a.first, a.last) == (T0 + timedelta(hours=9), T0 + timedelta(hours=10)), (a.first, a.last)   # 원문은 UTC — KST 로 9시간
    # 표시한 판을 뺀 경기 목록에 전체 라인업을 줘도 남은 판만 센다(109 거르기는 details 쪽)
    assert tc.combo_rows(ds[1:], "me", tc.my_lineups(ds, "me"), mat(cards())).rows[0].games == 1
    # 단계를 모르는 소속이면 그 판은 모름
    t2 = combo(ds, mat(cards(), [s for s in STEPS if s[0] != A]))
    assert t2.known == 2 and all(r.label != "에이" for r in t2.rows), [(r.label, r.games) for r in t2.rows]


def runs(ds, **kw):
    v = tc.club_values(tc.my_lineups(ds, "me"), mat(cards()))
    return [(r.value, r.games, r.unknown) for r in habits.history_runs(ds, "me", ("teamcolor",), {"teamcolor": v})]


def seq(*parts):
    out, n = [], 0
    for ids, k in parts:
        for _ in range(k):
            out.append(game(n, ids))
            n += 1
    return out


def test_history_breaks_on_long_unknown():
    cut = config.K4_MIN_RUN
    assert runs(seq((ELEVEN, 10), (US, cut - 1), (ELEVEN, 5))) == [("에이", 15, cut - 1)]      # 짧으면 잇고 적는다
    assert runs(seq((ELEVEN, 10), (US, cut), (ELEVEN, 5))) == [("에이", 10, 0), ("에이", 5, 0)], "모르는 판을 건너 이었다"
    # 2회차 N1 — 끊긴 뒤 짧은 구간을 깜빡임으로 되잇지 않는다
    assert runs(seq((ELEVEN, 100), (US, 300), (BS, 2), (US, 300), (ELEVEN, 50))) == [("에이", 100, 0), ("비", 2, 0), ("에이", 50, 0)]
    assert runs(seq((ELEVEN, 100), (US, 300), (ELEVEN, 2))) == [("에이", 100, 0), ("에이", 2, 0)]
    # 끊김이 없으면 예전 규칙 그대로 — 깜빡임
    assert runs(seq((ELEVEN, 10), (BS, 1), (ELEVEN, 5))) == [("에이", 15, 0)]
    # 앞이 모르는 판으로 시작해도 첫 구간은 끊김 표시가 없다
    v = tc.club_values(tc.my_lineups(seq((US, 9), (ELEVEN, 3)), "me"), mat(cards()))
    r = habits.history_runs(seq((US, 9), (ELEVEN, 3)), "me", ("teamcolor",), {"teamcolor": v})
    assert len(r) == 1 and not r[0].gap_before and r[0].unknown == 0


def test_short_unknown_belongs_inside_only():
    two = config.K4_MIN_RUN - 1
    assert runs(seq((ELEVEN, 10), (US, two), (BS, 5))) == [("에이", 10, 0), ("비", 5, 0)], "구간 사이의 모름을 뒤 구간이 셌다"
    assert runs(seq((ELEVEN, 10), (US, two), (BS, 1), (US, 1), (ELEVEN, 5))) == [("에이", 15, two + 1)], "깜빡임에 낀 모름"


def test_combo_groups_ignore_order():
    # 에이 6·비 5 와 에이 5·비 6 은 같은 묶음 — 라벨은 더 많이 쓴 순서
    e = {i: ex([A] if i <= 6 else [B]) for i in range(1, 12)}
    e.update({i: ex([A] if i <= 105 else [B]) for i in range(101, 112)})
    ds = [game(0), game(1, range(101, 112)), game(2, range(101, 112))]
    t = combo(ds, mat(e))
    assert [(r.label, r.games) for r in t.rows] == [("비 · 에이", 3)], [(r.label, r.games) for r in t.rows]
    assert combo(ds, mat(e, [s for s in STEPS if s[0] != A])).steps_only == 3 and combo([game(0, US)], mat(e)).steps_only == 0


def test_history_none_is_a_value_and_not_eleven_is_skipped():
    assert runs(seq((ELEVEN, 10), (NS, 200), (ELEVEN, 10))) == [("에이", 10, 0), (tc.NONE_ON, 200, 0), ("에이", 10, 0)]
    assert runs(seq((ELEVEN, 10), (range(1, 10), 50), (ELEVEN, 10))) == [("에이", 20, 0)], "11장이 아닌 판은 건너뛴다(모름이 아니다)"


def test_history_default_kinds_unchanged():
    ds = seq((ELEVEN, 10), (BS, 5))
    assert habits.history_runs(ds, "me") == habits.history_runs(ds, "me", ("nickname", "formation"), None)
    assert {r.kind for r in habits.history_runs(ds, "me")} == {"nickname", "formation"}


def test_side_colors_states():
    m = mat({**cards(), **{i: ex([A]) for i in range(1, 12)}})
    side = game(0)["matchInfo"][0]
    s = tc.side_colors(side, m)
    assert s.state == "ok" and [(a.id, a.members) for a in s.actives] == [(A, 11)] and sorted(s.spids) == list(ELEVEN)
    assert tc.side_colors(game(0, range(1, 10))["matchInfo"][0], m).state == "lineup"
    assert tc.side_colors(side, mat(cards(), meta=[])).state == "no_meta", "이름표가 없는데 선수를 모른다고 했다"
    k = config.TC_UNKNOWN_HIDE
    few = tc.side_colors(game(0, [*range(1, 12 - (k - 1)), *range(61, 60 + k)])["matchInfo"][0], m)
    many = tc.side_colors(game(0, [*range(1, 12 - k), *range(61, 61 + k)])["matchInfo"][0], m)
    assert (few.state, few.unknown) == ("ok", k - 1) and few.actives, few
    assert (many.state, many.unknown, many.actives) == ("unknown", k, ()), many


def test_ten_thousand_games_under_budget():
    ds = [game(i, ELEVEN if i % 2 else BS) for i in range(10_000)]
    m = mat(cards())
    t0 = time.process_time()
    lines = tc.my_lineups(ds, "me")
    v = tc.club_values(lines, m)
    habits.history_runs(ds, "me", ("teamcolor",), {"teamcolor": v})
    tc.combo_rows(ds, "me", lines, m)
    used = time.process_time() - t0
    assert used < 1.0, f"{used:.2f}초"   # 이 PC 0.2초 안팎 — CI 가 느려 넉넉히. 라인업마다 한 번(_Memo)을 빼면 넘는다
    print(f"        (1만 판 {used:.2f}초)")


def test_memo_counts_each_lineup_once():
    calls = []
    keep = tc.active
    tc.active = lambda *a: calls.append(1) or keep(*a)
    try:
        combo([game(i, ELEVEN if i % 2 else BS) for i in range(40)], mat(cards()))
    finally:
        tc.active = keep
    assert len(calls) == 2, len(calls)


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
