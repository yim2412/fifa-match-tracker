"""스펙업 순서(121단계) 계산 — 손으로 센 지어낸 후보·시세 골든 · 팀컬러 번호 실측 픽스처(네트워크 없음). `python tests/test_specup.py`"""
from __future__ import annotations

import contextlib
import json
import os
import random
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import requests  # noqa: E402
import cardcollect  # noqa: E402
import config  # noqa: E402
import core_api as core  # noqa: E402
import rankcollect  # noqa: E402
import rankerpick  # noqa: E402
import specup as sp  # noqa: E402
import store  # noqa: E402
import teamcolor  # noqa: E402

TEAM, OTHER = 1005, 2006
POS = [0, 3, 5, 7, 10, 13, 15, 23, 25, 27, 9]        # GK · RB CB LB · CDM CM CM · RW ST LW · CDM
RB, CB, RW = 1, 2, 7                                 # 위 목록의 자리 번호
CELLS = sorted(set(config.SIM_POS_OF.values()))
MINE = [100_000_001 + i for i in range(11)]          # 지금 선발 — 전부 1강 100 · 11강 = 121 · 적응도까지 125
ADAPT = 4                                            # 적응도 5 의 몫(122) — 손으로 적는다. 걸음의 차이와 순서(골든)는 그대로다
B11 = config.GRADE_OVR_BONUS[11] + ADAPT
EOK = sp.EOK


def extra(ovr=100, cells=None, club=(TEAM,), rel=(), sim="2026-11-20"):
    """cells 를 주면 그 칸만 ovr, 나머지는 50."""
    po = {c: ovr if cells is None or c in cells else 50 for c in CELLS}
    return {"sim_on": sim, "pos_ovr": json.dumps(po), "club_tc": json.dumps([[i, "x"] for i in club]),
            "rel_tc": json.dumps([[i, "x"] for i in rel])}


def make(cards: dict, salary=28, **kw) -> sp.Inputs:
    """cards: spid → (줄 묶음, extra, {강화: 억}, 급여). 지금 선발은 실효 121 · 급여 salary 씩."""
    inp = sp.Inputs(color_id=TEAM, lineup=tuple(sp.Slot(po, s, 11) for po, s in zip(POS, MINE)))
    inp.extras = {s: extra() for s in MINE}
    inp.salaries = {s: salary for s in MINE}
    cands = []
    for spid, (lines, ex, prices, pay) in cards.items():
        inp.extras[spid] = ex
        if pay is not None:
            inp.salaries[spid] = pay
        inp.prices.update({(spid, g): (int(v * EOK), "2026-11-20") for g, v in prices.items()})
        cands += [sp.CandCard(spid, ln, 9) for ln in lines]
    inp.cands = tuple(cands)
    for k, v in kw.items():
        setattr(inp, k, v)
    return inp


def brief(steps):
    return [(s.slot, s.spid, s.grade, s.ovr_gain, round(s.per_eok, 4), s.spent // EOK) for s in steps]


@contextlib.contextmanager
def cfg(**kw):
    keep = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in keep.items():
            setattr(config, k, v)


# ── 팀컬러 번호 ──────────────────────────────────────────────────────────────

def test_resolve_color_reasons():
    meta = [{"id": 1, "name": "아스널", "emblem": "crests/l1.png"}, {"id": 2, "name": "Spartan", "emblem": "teamcolorboost/a.png"},
            {"id": 3, "name": "Spartan", "emblem": "crests/b.png"}, {"id": 4, "name": "금빛 물결", "emblem": "teamcolor/tc_u_g.png"}]
    r = lambda n, e="": sp.resolve_color(n, e, meta)   # noqa: E731
    assert r("아스널", "crests/l1.png") == (1, "") and r("아스널") == (1, "")
    assert r("Spartan", "crests/b.png") == (3, "") and r("Spartan", "teamcolorboost/a.png") == (2, "")
    assert r("Spartan") == (None, sp.TC_SPLIT), "이름이 겹치는데 엠블럼을 모른다"
    assert r("Spartan", "crests/zzz.png") == (None, sp.TC_SPLIT)
    assert r("아스널", "crests/new.png") == (None, sp.TC_SPLIT), "목록 뒤에 새로 생긴 짝일 수 있다 — 이름만 보고 고르지 않는다"
    assert r("없는팀", "crests/l9.png") == (None, sp.TC_LIST) and sp.resolve_color("아스널", "", []) == (None, sp.TC_LIST)
    assert r("금빛 물결", "teamcolor/tc_u_g.png") == (None, sp.TC_ENHANCE) and r("금빛 물결") == (None, sp.TC_ENHANCE)
    assert r("단일 팀", "teamcolor/default.png") == (None, sp.TC_ENHANCE)
    assert r("") == (None, sp.NO_COLOR) and r(None) == (None, sp.NO_COLOR)
    assert teamcolor.ENHANCE_EMBLEM == cardcollect._ENHANCE_EMBLEM


def test_resolve_color_on_measured_snapshot():
    """11-24 실측 1만 명 — 팀컬러가 있는 9,996명 중 "단일 팀" 38명 말고 전부 번호 하나로 바뀐다."""
    fx = json.loads((Path(ROOT) / "tests" / "fixtures" / "specup_colors.json").read_text(encoding="utf-8"))
    meta = [{"id": i, "name": n, "emblem": e} for i, n, e in fx["meta"]]
    why: Counter = Counter()
    ids = set()
    for name, emblem, n in fx["pairs"]:
        tid, reason = sp.resolve_color(name, emblem, meta)
        why[reason] += n
        if tid is not None:
            assert tid not in ids, f"{name} 과 다른 팀컬러가 같은 번호다"
            ids.add(tid)
    assert why == {"": 9958, sp.TC_ENHANCE: 38, sp.NO_COLOR: 4}, why
    dup = [p for p in fx["pairs"] if sum(m[1] == p[0] for m in fx["meta"]) > 1]
    assert len(dup) >= 5 and all(sp.resolve_color(n, e, meta)[0] for n, e, _c in dup), dup   # 겹치는 이름이 실제로 들어 있다


def test_rankers_are_picked_by_number_not_name():
    meta = [{"id": 2, "name": "Spartan", "emblem": "teamcolorboost/a.png"}, {"id": 3, "name": "Spartan", "emblem": "crests/b.png"}]
    rows = [{"nickname": n, "profile_sn": i, "team_color": "Spartan"} for i, n in enumerate(("가", "나", "다", "나나"))]
    emblems = {0: "crests/b.png", 1: "teamcolorboost/a.png", 2: "crests/b.png"}   # 나나는 엠블럼을 모른다
    assert [r["nickname"] for r in sp.same_color_rows(rows, emblems, meta, 3, "다")] == ["가"]


# ── 기준 선발 · 후보 ─────────────────────────────────────────────────────────

def match(mid, day, sides):
    """sides = [(ouid, 닉네임, spid 11개, 강화)]"""
    info = []
    for ouid, nick, spids, grade in sides:
        ps = [{"spId": s, "spPosition": po, "spGrade": grade} for s, po in zip(spids, POS)]
        ps.append({"spId": 999_000_001, "spPosition": 28, "spGrade": 1})
        info.append({"ouid": ouid, "nickname": nick, "player": ps, "matchDetail": {"matchResult": "승"}})
    return {"matchId": mid, "matchType": config.DEFAULT_MATCH_TYPE, "matchDate": f"{day.isoformat()}T03:00:00", "matchInfo": info}


def test_lineup_is_latest_game_with_eleven_starters():
    d = date(2026, 11, 20)
    blank = match("m4", d, [("me", "나", [], 5), ("o", "남", MINE, 5)])
    ten = match("m3", d, [("me", "나", MINE[:10], 5), ("o", "남", MINE, 5)])
    full = match("m2", d, [("me", "나", [s + 50 for s in MINE], 7), ("o", "남", MINE, 5)])
    old = match("m1", d, [("me", "나", MINE, 5)])
    notmine = match("m5", d, [("o", "남", MINE, 5)])
    got = sp.pick_lineup([notmine, blank, ten, full, old], "me")
    assert [(s.po, s.spid, s.grade) for s in got] == [(po, s + 50, 7) for po, s in zip(POS, MINE)], got
    assert sp.pick_lineup([blank, ten], "me") == ()
    assert [s.line for s in got] == ["GK", "DF", "DF", "DF", "MF", "MF", "MF", "FW", "FW", "FW", "MF"]
    # 표시한 판은 빼고 고른다(109) — 감싼 것만
    core.set_excluded({"m2"})
    try:
        assert sp.pick_lineup([full, old], "me")[0].spid == MINE[0] + 50
        assert core.specup_lineup([full, old], "me")[0].spid == MINE[0]
    finally:
        core.set_excluded(())


def cand(spids, pos=None, nick="r"):
    return rankerpick.Candidate(nick, 1, rankerpick.SRC_OPP, None,
                                [{"spId": s, "spPosition": po, "spGrade": 8} for s, po in zip(spids, pos or POS)])


def test_candidates_threshold_per_line_once_per_person():
    five = [cand([7, 8], [25, 5]) for _ in range(5)]
    four = [cand([9], [25]) for _ in range(4)]
    same_person_twice = [cand([9, 9], [25, 23])]           # 한 사람이 같은 줄에 두 번 — 5명째가 아니라 5번째 사람으로 한 번
    assert [(c.spid, c.line, c.users) for c in sp.candidates(five + four)] == [(8, "DF", 5), (7, "FW", 5)]
    got = {(c.spid, c.line): c.users for c in sp.candidates(five + four + same_person_twice)}
    assert got[(9, "FW")] == 5, got
    # 줄이 다르면 따로 센다 — 3명 공격 + 2명 미드는 어느 줄에도 5명이 아니다
    split = [cand([3], [25]) for _ in range(3)] + [cand([3], [13]) for _ in range(2)]
    assert sp.candidates(split) == ()
    with cfg(SPECUP_MIN_USERS=2):                           # 배선 — 기본값이 아닌 값으로
        assert {(c.line, c.users) for c in sp.candidates(split)} == {("FW", 3), ("MF", 2)}


def test_slot_options_filters_and_counts():
    ok = 200_000_101
    cards = {ok: (["FW"], extra(105, cells={"RW"}), {8: 1, 9: 2, 11: 9}, 20),
             200_000_102: (["FW"], extra(club=(OTHER,), rel=(TEAM,)), {11: 5}, 20),      # 관계 팀컬러로 받아도 된다
             200_000_103: (["FW"], extra(club=(OTHER,)), {11: 5}, 20),                   # 다른 팀컬러
             200_000_104: (["FW"], extra(sim=""), {11: 5}, 20),                          # 시뮬레이터를 안 읽었다
             200_000_105: (["DF"], extra(), {11: 5}, 20),                                # 다른 줄
             200_000_106: (["FW"], extra(), {11: 5}, None),                              # 급여 모름
             300_000_001: (["FW"], extra(120), {11: 5}, 20)}                             # 지금 선발 다른 자리(GK)와 같은 선수
    inp = make(cards)
    got, skip = sp.slot_options(inp.lineup[RW], inp.cands, inp, (8, 9, 10, 11), True)
    bonus = {g: b + ADAPT for g, b in config.GRADE_OVR_BONUS.items()}
    assert [(o.spid, o.grade, o.ovr) for o in got] == [(ok, 8, 105 + bonus[8]), (ok, 9, 105 + bonus[9]),
                                                       (200_000_102, 11, 100 + B11), (ok, 11, 105 + B11)], got
    assert skip == {(200_000_103, None): "color_out", (200_000_104, None): "color_unknown", (200_000_106, None): "salary_unknown",
                    (300_000_001, None): "same_player", (ok, 10): "price", **{(200_000_102, g): "price" for g in (8, 9, 10)}}, skip
    # 순서표의 수는 어느 자리에도 못 세운 카드만 — 공격 세 자리에서 세 번 빼도 한 장이다
    assert sp.plan(inp).skipped == {"color_out": 1, "color_unknown": 1, "salary_unknown": 1, "same_player": 1, "price": 1 + 3 + 3}, sp.plan(inp).skipped
    free = {ok: (["FW", "MF"], extra(105), {11: 1}, 20), 300_000_000 + MINE[RW] % store.PLAYER_ID_MOD: (["FW"], extra(), {11: 1}, 20)}
    assert sp.plan(make(free), grades=(11,)).skipped == {}, "제 자리에는 세울 수 있는 선수 — 다른 자리에서 막혀도 뺀 것이 아니다"
    assert any(o.spid == 200_000_106 for o in sp.slot_options(inp.lineup[RW], inp.cands, inp, (11,), False)[0]), "급여를 안 걸면 모르는 카드도"
    # 강화 범위는 인자대로 · OVR 은 내 선발 그 자리(RW 105 · LW 는 50 짜리 칸)
    assert [o.grade for o in sp.slot_options(inp.lineup[RW], inp.cands, inp, (9,), True)[0]] == [9]
    lw = sp.slot_options(inp.lineup[9], inp.cands, inp, (11,), True)[0]
    assert [o.ovr for o in lw if o.spid == ok] == [50 + B11], lw
    # 같은 자리의 같은 선수(더 높은 강화·다른 시즌)는 살 수 있다
    mine = 300_000_000 + MINE[RW] % store.PLAYER_ID_MOD
    inp2 = make({mine: (["FW"], extra(110), {11: 5}, 20)})
    assert len(sp.slot_options(inp2.lineup[RW], inp2.cands, inp2, (11,), True)[0]) == 1
    # 자리를 시뮬레이터 칸으로 못 잇거나 강화를 모르면 OVR 모름으로 센다
    bad = make({ok: (["FW"], extra(105), {11: 1}, 20)})
    bad.extras[ok]["pos_ovr"] = "{"
    assert sp.slot_options(bad.lineup[RW], bad.cands, bad, (11, 12), True)[1] == {(ok, 11): "ovr_none", (ok, 12): "ovr_none"}


# ── 거르기 · 묶음 ────────────────────────────────────────────────────────────

def opt(spid, price, ovr):
    return sp.Option(spid, 11, int(price * EOK), ovr, 20)


def test_frontier_starts_from_current_card():
    o = [opt(1, 1, 100), opt(2, 2, 121), opt(3, 3, 123), opt(4, 4, 123), opt(5, 5, 122), opt(6, 6, 126)]
    assert [x.spid for x in sp.frontier(o, 121)] == [3, 6]
    assert [x.spid for x in sp.frontier(o, 0)] == [1, 2, 3, 6]          # 원점이 없으면 지금보다 낮은 카드까지 남는다
    assert sp.frontier(o, 126) == []


def test_chain_forced_merge_repeats_until_monotone():
    plain = [opt(1, 10, 123), opt(2, 20, 125), opt(3, 40, 127)]          # 바로 사도 0.2 · 0.2 · 0.1(2 다음) — 낫지 않으니 그대로
    assert [(o.spid, g, s) for o, g, s in sp.chain(plain, 121)] == [(1, 2, ()), (2, 2, ()), (3, 2, ())], "같으면 안 묶는다"
    # 앞 카드 값을 버리는 걸음 — 5억 +1 뒤 8억 +1 은 앞보다 낮은 0.125 지만, 바로 사면 8억 +2(0.25)라 앞(0.2)을 건너뛴다
    assert [(o.spid, g, tuple(x.spid for x in s)) for o, g, s in sp.chain([opt(1, 5, 122), opt(2, 8, 123)], 121)] == [(2, 2, (1,))]
    # 0.1 뒤 0.133 → 앞을 건너뛰고 바로(+5/30)
    a, b = opt(1, 10, 122), opt(2, 30, 126)
    assert [(o.spid, g, tuple(x.spid for x in s)) for o, g, s in sp.chain([a, b], 121)] == [(2, 5, (1,))]
    # 되풀이: 1(0.3) · 2(바로 4/20 = 0.2 라 남음 · 1 다음이면 0.05) · 3 은 2를 건너뛰고(10/30 = 0.333 > 0.05), 1도 건너뛴다(13/30 = 0.433 > 0.3)
    c = [opt(1, 10, 124), opt(2, 20, 125), opt(3, 30, 134)]
    got = sp.chain(c, 121)
    assert [(o.spid, g, tuple(x.spid for x in s)) for o, g, s in got] == [(3, 13, (1, 2))], got
    # 멈출 자리에서 멈춘다 — 3 이 2만 건너뛴다(1 부터 바로 7/30 = 0.233 < 0.3)
    got = sp.chain([opt(1, 10, 124), opt(2, 20, 125), opt(3, 30, 128)], 121)
    assert [(o.spid, g, tuple(x.spid for x in s)) for o, g, s in got] == [(1, 3, ()), (3, 4, (2,))], got
    # 굵기 — 바로 사는 효율(3/20 = 0.15)이 앞(0.2)의 그 배수보다 높으면 묶는다(딱 0.75배는 안 묶는다)
    two = [opt(1, 10, 123), opt(2, 20, 124)]
    assert [o.spid for o, _g, _s in sp.chain(two, 121, 0.75)] == [1, 2] and [o.spid for o, _g, _s in sp.chain(two, 121, 0.7)] == [2]
    assert [o.spid for o, _g, _s in sp.chain(plain, 121, 0.4)] == [3]


def test_chain_is_monotone_on_random_fronts():
    rnd = random.Random(121)
    for _ in range(300):
        n = rnd.randint(1, 12)
        front = [opt(i, p, 121 + o) for i, (p, o) in enumerate(
            zip(sorted(rnd.sample(range(1, 400), n)), sorted(rnd.sample(range(1, 40), n))))]
        for ratio in (1.0, 0.7):
            got = sp.chain(front, 121, ratio)
            eff = [sp.per_eok(g, o.price) for o, g, _s in got]
            assert all(b <= ratio * a for a, b in zip(eff, eff[1:])), eff
            base = 121
            for (o, g, _s), (o2, _g2, _s2) in zip(got, got[1:]):   # 남은 앞 걸음은 뒤 카드를 바로 사는 것보다 못하지 않다
                assert sp.per_eok(o2.ovr - base, o2.price) <= ratio * sp.per_eok(g, o.price), (o, o2)
                base = o.ovr
            assert got[-1][0] is front[-1] and sum(g for _o, g, _s in got) == front[-1].ovr - 121
            assert sum(1 + len(s) for _o, _g, s in got) == n, "건너뛴 카드까지 합치면 처음 목록"


# ── 병합 골든 ────────────────────────────────────────────────────────────────

A, B, C, D = 200_000_101, 200_000_102, 200_000_103, 200_000_104
GOLD = {A: (["DF"], extra(102), {11: 10}, 28), B: (["DF"], extra(106), {10: 20, 11: 40}, 28),
        C: (["FW"], extra(101), {11: 10}, 28), D: (["FW"], extra(105), {11: 30}, 28)}


def test_plan_golden_order_chunk_and_budget():
    p = sp.plan(make(GOLD))
    assert brief(p.steps) == [(RB, A, 11, 2, 0.2, 10), (CB, B, 10, 4, 0.2, 30), (RW, D, 11, 5, 0.1667, 60),
                              (8, C, 11, 1, 0.1, 70), (CB, B, 11, 2, 0.05, 110)], brief(p.steps)
    assert p.steps[2].chunk == ((C, 11),) and p.steps[0].chunk == ()
    assert (p.steps[1].from_spid, p.steps[1].from_grade) == (MINE[CB], 11)
    assert (p.steps[4].from_spid, p.steps[4].from_grade) == (B, 10), "같은 자리 뒤 걸음의 기준은 앞에서 산 카드"
    assert [s.spent_before // EOK for s in p.steps] == [0, 10, 30, 60, 70]
    assert p.salary_on and p.salary_now == 308 and [s.salary_sum for s in p.steps] == [308] * 5
    assert [s.state for s in p.slots] == [sp.SLOT_EMPTY, "", "", sp.SLOT_EMPTY, *[sp.SLOT_EMPTY] * 3, "", "", sp.SLOT_EMPTY, sp.SLOT_EMPTY]
    assert all(s.ovr == 121 + ADAPT for s in p.slots)
    assert [sp.budget_cut(p.steps, b * EOK) for b in (0, 10, 59, 60, 1000)] == [0, 1, 2, 3, 5]
    assert p.steps[0].price_on == "2026-11-20"


def test_ties_are_settled_by_price_ovr_and_users():
    # 효율이 같으면(0.2) 싼 걸음 먼저 — 자리 번호로는 DF 가 앞이다
    fw, df = 200_000_151, 200_000_152
    p = sp.plan(make({df: (["DF"], extra(104, cells={"RB"}), {11: 20}, 28), fw: (["FW"], extra(102, cells={"RW"}), {11: 10}, 28)}))
    assert brief(p.steps) == [(RW, fw, 11, 2, 0.2, 10), (RB, df, 11, 4, 0.2, 30)], brief(p.steps)
    # 값이 같으면 OVR 높은 것 하나만 — 낮은 것을 먼저 세우면 같은 값을 두 번 쓴다
    lo, hi, pop = 200_000_153, 200_000_154, 200_000_155
    cards = {lo: (["FW"], extra(102, cells={"RW"}), {11: 10}, 28), hi: (["FW"], extra(104, cells={"RW"}), {11: 10}, 28),
             pop: (["FW"], extra(104, cells={"RW"}), {11: 10}, 28)}
    inp = make(cards)
    inp.cands = tuple(sp.CandCard(c.spid, c.line, {lo: 99, hi: 5, pop: 6}[c.spid]) for c in inp.cands)
    assert brief(sp.plan(inp).steps) == [(RW, pop, 11, 4, 0.4, 10)], "값·OVR 이 같으면 많이 쓴 카드(번호 순이면 hi 가 먼저다)"


def test_plan_knobs_lines_grades_ratio_manual():
    inp = make(GOLD)
    assert {s.slot for s in sp.plan(inp, lines={"FW"}).steps} == {RW, 8}
    assert sp.plan(inp, grades=(10,)).steps[0].spid == B and len(sp.plan(inp, grades=(10,)).steps) == 1
    with cfg(SPECUP_GRADES=(10,)):
        assert len(sp.plan(inp).steps) == 1, "기본 강화 범위는 config"
    rough = sp.plan(inp, lines={"DF"}, ratio=0.4)
    assert rough.steps[0].spid == B and rough.steps[0].chunk, brief(rough.steps)
    # 직접 넣은 카드 — 줄과 5명 문턱만 면제, 팀컬러는 그대로 거친다
    x, y = 200_000_301, 200_000_302
    inp.extras[x], inp.extras[y] = extra(130), extra(130, club=(OTHER,))
    inp.salaries.update({x: 28, y: 28})
    inp.prices.update({(x, 11): (EOK, "d"), (y, 11): (EOK, "d")})
    assert x not in {s.spid for s in sp.plan(inp).steps}
    got = sp.plan(inp, manual=(x, y, x))
    assert got.steps[0].spid == x and y not in {s.spid for s in got.steps}, brief(got.steps)
    assert sum(s.spid == x for s in got.steps) == 1
    # 후보(공격 줄)인 카드를 직접 넣으면 다른 줄에서도 — 후보 줄로는 안 되던 것부터 잰다
    inp2 = make({A: (["FW"], extra(110, cells={"RB"}), {11: 1}, 28)})
    assert sp.plan(inp2).steps == []
    assert [s.slot for s in sp.plan(inp2, manual=(A,)).steps] == [RB]
    # 사유가 있는 재료면 아무것도 안 세운다
    assert sp.plan(make(GOLD, reason=sp.FEW_RANKERS)).steps == []


def test_same_player_taken_by_other_slot_recomputes():
    """RW 사다리 Q → P → R 에서 P 선수를 RB 가 먼저 사면, RW 는 Q 에서 R 로 바로 다시 센다(5a ⓒ)."""
    q, p_rb, p_rw, r = 200_000_401, 200_000_402, 300_000_402, 200_000_403   # p 둘은 같은 선수의 다른 시즌 카드
    cards = {q: (["FW"], extra(102, cells={"RW"}), {11: 4}, 28), p_rw: (["FW"], extra(104, cells={"RW"}), {11: 10}, 28),
             r: (["FW"], extra(106, cells={"RW"}), {11: 20}, 28)}
    alone = sp.plan(make(cards))
    assert brief(alone.steps) == [(RW, q, 11, 2, 0.5, 4), (RW, p_rw, 11, 2, 0.2, 14), (RW, r, 11, 2, 0.1, 34)]   # 안 막으면 이렇다
    cards[p_rb] = (["DF"], extra(104, cells={"RB"}), {11: 10}, 28)
    got = sp.plan(make(cards))
    assert brief(got.steps) == [(RW, q, 11, 2, 0.5, 4), (RB, p_rb, 11, 4, 0.4, 14), (RW, r, 11, 4, 0.2, 34)], brief(got.steps)
    # 산 자리가 나중에 다른 카드로 바꿔도 그 선수는 다른 자리로 안 풀린다
    cards[200_000_404] = (["DF"], extra(110, cells={"RB"}), {11: 100}, 28)
    got = sp.plan(make(cards))
    assert got.steps[-1].spid == 200_000_404 and p_rw not in {s.spid for s in got.steps}, brief(got.steps)


def test_salary_replaces_skips_and_comes_back():
    """급여 합 308 · 상한 315 — 더하기가 아니라 바꾼 카드 급여를 뺀다 · 넘는 칸은 건너뛰고 · 다른 자리에서 내려가면 다시 들어온다(M4)."""
    q, p, r, s = 200_000_501, 200_000_502, 200_000_503, 200_000_504
    cards = {q: (["FW"], extra(102, cells={"RW"}), {11: 4}, 28), p: (["FW"], extra(108, cells={"RW"}), {11: 10}, 36),
             r: (["FW"], extra(106, cells={"RW"}), {11: 20}, 30)}
    free = sp.plan(make(cards), cap=999)
    assert brief(free.steps) == [(RW, p, 11, 8, 0.8, 10)], "안 막으면 P 로 바로 간다(R 은 P 에 밀린다)"
    got = sp.plan(make(cards))
    assert [(x.spid, x.ovr_gain, x.salary_sum) for x in got.steps] == [(q, 2, 308), (r, 4, 310)], brief(got.steps)
    cards[s] = (["DF"], extra(102, cells={"RB"}), {11: 100}, 20)
    got = sp.plan(make(cards))
    assert [(x.slot, x.spid, x.ovr_gain, x.salary_sum) for x in got.steps] == \
        [(RW, q, 2, 308), (RW, r, 4, 310), (RB, s, 2, 302), (RW, p, 2, 308)], brief(got.steps)
    with cfg(SPECUP_SALARY_CAP=999):
        assert sp.plan(make(cards)).steps[0].spid == p, "상한 기본값은 config"
    # 딱 상한까지는 된다 · 1 넘으면 안 된다
    edge = {q: (["FW"], extra(110, cells={"RW"}), {11: 1}, 35)}
    assert sp.plan(make(edge)).steps and not sp.plan(make({q: (*edge[q][:3], 36)})).steps


def test_salary_over_cap_at_start_and_unknown():
    q, r = 200_000_601, 200_000_602
    cards = lambda a, b: {q: (["FW"], extra(105, cells={"RW"}), {11: 1}, a), r: (["DF"], extra(105, cells={"RB"}), {11: 1}, b)}   # noqa: E731
    over = sp.plan(make(cards(30, 31), salary=30))            # 330 > 315 — 안 늘어나는 걸음만
    assert [s.spid for s in over.steps] == [q] and over.salary_now == 330
    # 지금 카드 하나라도 급여를 모르면 안 막는다
    inp = make(cards(99, 99))
    del inp.salaries[MINE[0]]
    p = sp.plan(inp)
    assert not p.salary_on and p.salary_now is None and len(p.steps) == 2 and p.steps[0].salary_sum is None
    assert sp.plan(make(cards(99, 99))).steps == [], "다 알면 막는다 — 위가 거저가 아니다"
    assert sp.plan(make(cards(None, 28))).skipped["salary_unknown"] == 1


def test_unknown_current_ovr_makes_gray_slot():
    inp = make({A: (["FW"], extra(105), {11: 1}, 28)})
    inp.extras.pop(MINE[RW])
    p = sp.plan(inp)
    assert p.slots[RW].state == sp.SLOT_NO_OVR and p.slots[RW].ovr is None and RW not in {s.slot for s in p.steps}
    assert p.steps and p.steps[0].slot == 8, "다른 공격 자리는 그대로"


def test_plan_invariants_random():
    rnd = random.Random(7)
    for _ in range(25):
        cards = {}
        for k in range(40):
            spid = rnd.choice((200, 300, 400)) * 1_000_000 + rnd.randint(1, 60)
            cards[spid] = (rnd.sample(["GK", "DF", "MF", "FW"], rnd.randint(1, 2)), extra(rnd.randint(98, 112)),
                           {g: rnd.randint(1, 300) for g in (8, 9, 10, 11)}, rnd.randint(20, 36))
        inp = make(cards)
        p = sp.plan(inp, ratio=rnd.choice((1.0, 0.7)))
        now = [(s.spid, 121, 28) for s in inp.lineup]
        spent = 0
        for st in p.steps:
            assert (st.from_spid, st.ovr_gain > 0) == (now[st.slot][0], True)
            ovr = json.loads(inp.extras[st.spid]["pos_ovr"])["RW"] + config.GRADE_OVR_BONUS[st.grade]
            assert ovr - now[st.slot][1] == st.ovr_gain
            now[st.slot] = (st.spid, ovr, inp.salaries[st.spid])
            spent += st.price
            assert st.spent == spent and st.salary_sum == sum(x[2] for x in now) <= config.SPECUP_SALARY_CAP
            ids = [x[0] % store.PLAYER_ID_MOD for x in now]
            assert len(set(ids)) == 11, "같은 선수가 두 자리에"


# ── 읽기(끝에서 끝) ──────────────────────────────────────────────────────────

TODAY = date.today()
CREST, BOOST = "crests/l11.png", "teamcolorboost/x.png"


class _DB:
    """임시 fifa.db · rank.db — 나(ME) + 같은 팀컬러 랭커 n 명(최근 상대) + 이름만 같은 다른 팀컬러 랭커."""

    def __init__(self, n=10, rival=3):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.f = store.open_db(d / "f.db")
        self.r = rankcollect.open_rank_db(d / "r.db")
        store.upsert_account(self.f, "me", "나")
        store.set_trade_state(self.f, my_ouid="me")
        games = [match("g0", TODAY - timedelta(days=5), [("me", "나", [s + 50 for s in MINE], 9), ("z", "옛상대", MINE, 5)]),
                 match("g1", TODAY - timedelta(days=1), [("me", "나", MINE, 11), ("z", "옛상대", MINE, 5)])]
        rows = [(1, 1, "나", "Spartan", 27_000, CREST)]
        for i in range(n):
            rows.append((2 + i, 10 + i, f"랭커{i}", "Spartan", 5, CREST))
            games.append(match(f"a{i}", TODAY - timedelta(days=2), [("me", "나", MINE, 11), (f"o{i}", f"랭커{i}", [A] + [700 + i * 20 + j for j in range(10)], 8)]))
        for i in range(rival):
            rows.append((500 + i, 100 + i, f"딴색{i}", "Spartan", 5, BOOST))
            games.append(match(f"b{i}", TODAY - timedelta(days=2), [("me", "나", MINE, 11), (f"q{i}", f"딴색{i}", [B] * 1 + [9000 + i * 20 + j for j in range(10)], 8)]))
        store.save_matches(self.f, games)
        self.r.execute("INSERT INTO snapshots (id, taken_at, row_count, dup_count, season_seq, new_season)"
                       " VALUES (1, '2026-11-23T10:00:00', 1, 0, 1, 0)")
        for rank, sn, nick, color, value, emblem in rows:
            self.r.execute("INSERT INTO snapshot_rows (snapshot_id, rank, profile_sn, nickname, team_color, team_value)"
                           " VALUES (1, ?, ?, ?, ?, ?)", (rank, sn, nick, color, value))
            self.r.execute("INSERT INTO snapshot_emblems VALUES (1, ?, ?)", (sn, emblem))
        self.r.commit()
        self.meta()
        store.save_card_prices(self.f, A, {8: 5 * EOK}, "2026-11-20")
        store.save_card_salary(self.f, [(s, 28) for s in (*MINE, A)])
        self.f.execute("INSERT INTO card_extra (spid, pos_ovr, club_tc, rel_tc, sim_on) VALUES (?, ?, ?, '[]', '2026-11-20')",
                       (A, extra()["pos_ovr"], extra()["club_tc"]))
        self.f.commit()

    def meta(self, *items):
        items = items or (teamcolor.TeamColorMeta(TEAM, "Spartan", CREST, "", 4, 11), teamcolor.TeamColorMeta(OTHER, "Spartan", BOOST, "", 4, 11))
        store.save_teamcolor_meta(self.f, items, TODAY.isoformat())

    def close(self):
        self.f.close()
        self.r.close()
        self.tmp.cleanup()


@contextlib.contextmanager
def db(**kw):
    d = _DB(**kw)
    keep = requests.sessions.Session.request

    def no(*a, **k):
        raise AssertionError("요청을 보냈다")
    requests.sessions.Session.request = no
    try:
        yield d
    finally:
        requests.sessions.Session.request = keep
        d.close()


def test_load_reads_one_person_without_requests():
    with db() as d:
        inp = core.specup_load(d.f, d.r, pick=core.specup_lineup)
        assert inp.reason == "" and (inp.nickname, inp.color, inp.color_id, inp.value) == ("나", "Spartan", TEAM, 27_000), inp
        assert [s.spid for s in inp.lineup] == MINE and inp.lineup[0].grade == 11
        assert inp.rankers == 10 and dict(inp.by_source) == {rankerpick.SRC_OPP: 10}, "이름만 같은 다른 팀컬러 3명은 안 센다"
        assert [(c.spid, c.line, c.users) for c in inp.cands] == [(A, "GK", 10)]
        assert inp.want == tuple(sorted({*MINE, A}))
        assert inp.prices == {(A, 8): (5 * EOK, "2026-11-20")} and inp.salaries[A] == 28 and sp.keeps_color(inp.extras[A], TEAM)
        assert d.f.execute("SELECT COUNT(*) FROM ranker_stats").fetchone()[0] == 0 and \
            d.f.execute("SELECT COUNT(*) FROM api_budget").fetchone()[0] == 0, "랭커 기록도 새로 안 받는다(U16)"
        limits, keep = [], store.load_details
        store.load_details = lambda *a, **k: limits.append(k.get("limit")) or keep(*a, **k)
        try:
            with cfg(SPECUP_LINEUP_SCAN=3):
                sp.load(d.f, d.r)
        finally:
            store.load_details = keep
        assert limits == [3], f"앞의 몇 판에서 찾았으면 전체를 안 읽는다: {limits}"
        # 표시한 판은 기준 선발로 안 쓴다 · 앞의 판이 전부 표시한 판이면 저장 전체에서 찾는다
        core.set_excluded({"g1", *(f"a{i}" for i in range(10)), *(f"b{i}" for i in range(3))})
        try:
            with cfg(SPECUP_LINEUP_SCAN=2):
                assert core.specup_load(d.f, d.r, pick=core.specup_lineup).lineup[0].spid == MINE[0] + 50
                core.set_excluded({*core.excluded(), "g0"})
                assert core.specup_load(d.f, d.r, pick=core.specup_lineup).reason == sp.NO_LINEUP
        finally:
            core.set_excluded(())


def test_load_manual_and_ranker_records():
    """직접 넣은 카드는 시세·카드 정보를 같이 읽고 받는 순서는 선발 다음 · 후보가 아니라서 5명 셈에 안 든다 · 랭커 기록은 오늘 받아 둔 것만."""
    x = 200_000_777
    with db() as d:
        store.save_card_prices(d.f, x, {11: 3 * EOK}, "2026-11-21")
        store.save_ranker_stats(d.f, [(A, 0)], [{"spId": A, "spPosition": 0, "status": {"matchCount": 4, "goal": 1}}],
                                config.DEFAULT_MATCH_TYPE, TODAY.isoformat())
        store.save_ranker_stats(d.f, [(x, 0)], [{"spId": x, "spPosition": 0, "status": {"matchCount": 4}}],
                                config.DEFAULT_MATCH_TYPE, (TODAY - timedelta(days=1)).isoformat())
        inp = core.specup_load(d.f, d.r, pick=core.specup_lineup, manual=(x, x), today=TODAY)
        assert inp.manual == (x,) and x in inp.want and inp.prices[x, 11] == (3 * EOK, "2026-11-21")
        assert inp.fetch_order[:11] == tuple(MINE) and inp.fetch_order[11:] == (x, A), inp.fetch_order
        assert [c.spid for c in inp.cands] == [A]
        assert list(inp.records) == [(A, 0)] and inp.records[A, 0]["status"]["matchCount"] == 4, inp.records
        later = core.specup_load(d.f, d.r, pick=core.specup_lineup, today=TODAY + timedelta(days=1))
        assert later.records == {} and later.manual == (), "어제 받은 랭커 기록을 썼다"


def test_why_keeps_reason_per_card():
    """뺀 카드의 사유 — 다른 자리에 세울 수 있으면 뺀 것이 아니다 · 사유 수와 이유 묶음이 같다."""
    x, y = 200_000_801, 200_000_802
    inp = make({x: (["FW"], extra(120, club=(OTHER,)), {11: 1}, 28), y: (["FW"], extra(120), {}, 28)})
    p = sp.plan(inp, grades=(11,))
    assert p.why == {x: "color_out", y: "price"}, p.why
    inp.prices[y, 11] = (EOK, "d")
    got = sp.plan(inp, grades=(10, 11))
    assert got.why == {x: "color_out"} and got.skipped["price"] == 1, (got.why, got.skipped)   # 11강은 살 수 있는 카드다


def test_value_band_uses_money_spent_before():
    """추천 구단가치(U8) = 지금 구단가치 + 앞 칸까지 쓴 금액. 그 칸의 값은 안 든다. 경계값은 위 구간."""
    with cfg(SPECUP_VALUE_EDGES=(10 * EOK, 20 * EOK)):
        assert [sp.value_band(v) for v in (0, 10 * EOK - 1, 10 * EOK, 20 * EOK)] == [0, 0, 1, 2]
        steps = sp.plan(make(GOLD)).steps
        assert len(steps) > 1 and steps[0].spent_before == 0
        assert sp.band_value(steps[0], 9 * EOK) == 9 * EOK
        assert sp.band_value(steps[1], 9 * EOK) == 9 * EOK + steps[0].price


def test_load_reasons():
    with db() as d:
        go = lambda: sp.load(d.f, d.r)   # noqa: E731
        assert go().reason == ""
        with cfg(SPECUP_OPPONENT_MAX=3):
            got = go()
            assert got.reason == sp.FEW_RANKERS and got.rankers == 3 and got.cands == (), got
        with cfg(RECOMMEND_MIN_RANKERS=11):
            assert go().reason == sp.FEW_RANKERS
        assert sp.load(d.f, None).reason == sp.NO_SNAPSHOT
        d.meta(teamcolor.TeamColorMeta(9, "다른팀", "crests/l9.png", "", 4, 11))
        assert go().reason == sp.TC_LIST
        d.meta(teamcolor.TeamColorMeta(OTHER, "Spartan", BOOST, "", 4, 11))
        assert go().reason == sp.TC_SPLIT
        d.r.execute("UPDATE snapshot_emblems SET emblem = 'teamcolor/tc_u_g.png' WHERE profile_sn = 1")
        assert go().reason == sp.TC_ENHANCE
        d.r.execute("UPDATE snapshot_rows SET team_color = '' WHERE profile_sn = 1")
        assert go().reason == sp.NO_COLOR and go().nickname == "나"
        d.r.execute("DELETE FROM snapshot_rows WHERE profile_sn = 1")
        assert go().reason == sp.NO_COLOR
        store.set_trade_state(d.f, my_ouid=None)
        assert go().reason == sp.NO_ME


def test_load_plan_end_to_end():
    with db() as d:
        for s in MINE:
            d.f.execute("INSERT INTO card_extra (spid, pos_ovr, club_tc, rel_tc, sim_on) VALUES (?, ?, '[]', '[]', 'd')",
                        (s, extra(80)["pos_ovr"]))
        d.f.commit()
        p = sp.plan(sp.load(d.f, d.r))                  # GK 80+21 = 101 → A 8강 100+15 = 115
        assert brief(p.steps) == [(0, A, 8, 14, 2.8, 5)], brief(p.steps)
        assert p.salary_now == 308 and p.steps[0].salary_sum == 308


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
