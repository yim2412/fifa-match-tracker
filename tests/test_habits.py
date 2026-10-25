"""habits.py — 내 경기 습관·기준선 계산(2.6.1). 지어낸 입력의 손으로 센 값으로 단언한다(네트워크 없음).

    python tests/test_habits.py
"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import habits  # noqa: E402
import models  # noqa: E402
import stats as stats_  # noqa: E402
from models import MatchSummary, PeriodRate  # noqa: E402


def _t(day, hour=12):
    return datetime(2026, 9, day, hour)


# ── N2 등급 체류 ─────────────────────────────────────────────────────────────

def test_division_stays_counts_by_hand():
    # 9/1 챌린지1(1100) 2판 → 같은 날 슈퍼챌린지(1000) 승격 1판(9/1 은 1000 으로 끝) · 9/2 1000 → 1100 강등(9/2 는 1100 으로 끝)
    pts = [(_t(1, 9), 1100), (_t(1, 10), 1100), (_t(1, 11), 1000), (_t(2, 9), 1000), (_t(2, 10), 1100)]
    r = habits.division_stays(pts, name_of={1000: "슈퍼챌린지", 1100: "챌린지1"}.get)
    assert [x.division for x in r.rows] == [1000, 1100], "높은 등급(id 작은 것)부터"
    by = {x.division: x for x in r.rows}
    assert (by[1100].games, by[1100].days, by[1100].entries) == (3, 1, 1), by[1100]   # 시작 등급은 진입에 안 센다
    assert (by[1000].games, by[1000].days, by[1000].entries) == (2, 1, 1), by[1000]   # 날은 그날 마지막 경기 등급
    assert (r.promotions, r.demotions, r.gaps, r.start_division) == (1, 1, 0, 1100)
    assert by[1000].name == "슈퍼챌린지"


def test_division_stays_order_independent_and_empty():
    pts = [(_t(2, 10), 900), (_t(1, 10), 1000)]
    r = habits.division_stays(pts)
    assert (r.promotions, r.demotions) == (1, 0), "시각순으로 다시 놓지 않았다"
    assert r.rows[0].name == "900"
    e = habits.division_stays([])
    assert e.rows == [] and (e.promotions, e.demotions, e.gaps) == (0, 0, 0)


def test_division_gap_not_counted_as_move():
    # 막지 않았으면: 5일 빈 사이의 1100 → 1000 이 승격 1 · 진입 1 로 셌을 것
    n = config.N2_GAP_DAYS
    pts = [(_t(1), 1100), (_t(1 + n + 2), 1000)]
    r = habits.division_stays(pts)
    assert (r.promotions, r.demotions, r.gaps) == (0, 0, 1), r
    assert {x.division: x.entries for x in r.rows} == {1000: 0, 1100: 0}
    # 경계: 정확히 N2_GAP_DAYS 일이면 빈 구간이 아니다("넘으면")
    r = habits.division_stays([(_t(1), 1100), (_t(1 + n), 1000)])
    assert (r.promotions, r.gaps) == (1, 0), r
    # 빈 구간은 등급이 같아도 센다(화면 "기록 빈 구간 N곳")
    r = habits.division_stays([(_t(1), 1100), (_t(1 + n + 2), 1100)])
    assert r.gaps == 1 and r.promotions == r.demotions == 0


# ── N4 기준선 — side_totals · paired_diff ─────────────────────────────────────

def _side(ouid, res, shots, on, goals, tk_try, tk_ok, poss, lt=0, pas=0):
    return {"ouid": ouid, "matchDetail": {"matchResult": res, "possession": poss},
            "shoot": {"shootTotal": shots, "effectiveShootTotal": on, "goalTotal": goals},
            "defence": {"tackleTry": tk_try, "tackleSuccess": tk_ok},
            "pass": {"passTry": pas, "longPassTry": lt, "throughPassTry": 0}}


def _game(mid, me, opp):
    return {"matchId": mid, "matchInfo": [me, opp]}


def _three_games():
    # 손으로 센 3경기 — 나(슛·유효·골·태클 시도·성공·점유) / 상대
    return [
        _game("g1", _side("me", "승", 4, 3, 2, 10, 7, 60), _side("op", "패", 2, 1, 0, 8, 6, 40)),
        _game("g2", _side("me", "패", 2, 2, 1, 6, 3, 45), _side("op", "승", 5, 4, 3, 10, 8, 55)),
        _game("g3", _side("me", "무", 6, 4, 1, 8, 6, 50), _side("op", "무", 3, 2, 1, 6, 3, 50)),
        _game("err", _side("me", "오류", 9, 9, 9, 9, 9, 50), _side("op", "오류", 0, 0, 0, 0, 0, 50)),   # 승·무·패 아님 → 뺀다
        {"matchId": "solo", "matchInfo": [_side("me", "승", 9, 9, 9, 9, 9, 100)]},                    # 상대 없음 → 뺀다
    ]


def test_side_totals_by_hand():
    t = habits.side_totals(_three_games(), "me")
    assert t.games == 3 and [r.result for r in t.rows] == [0, 2, 1], [r.match_id for r in t.rows]
    assert (t.total("mine", "shots"), t.total("mine", "goals"), t.total("opp", "shots"), t.total("opp", "goals")) == (12, 4, 10, 4)
    assert (t.total("mine", "tackle_ok"), t.total("opp", "tackle_try")) == (16, 24)


def test_paired_diff_by_hand():
    t = habits.side_totals(_three_games(), "me")
    # 경기당 슛: 차 2, −3, 3 → 평균 2/3 · 편차² 합 20.667 → sd 3.2146 · se 1.8559
    d = habits.paired_diff(t, "shots")
    assert abs(d.diff - 2 / 3) < 1e-9 and abs(d.se - 1.8559) < 1e-3, (d.diff, d.se)
    assert (d.mine, d.opp) == (4.0, 10 / 3)
    # 결정력: 나 4/12 · 상대 4/10 → −6.667%p. 선형화 u = 0.40667, −0.21667, −0.19 → sd 0.35243 → se 20.348%p
    f = habits.paired_diff(t, "finish")
    assert abs(f.diff - (100 / 3 - 40)) < 1e-9, f.diff
    assert abs(f.se - 20.348) < 1e-2, f.se
    assert (f.mine_den, f.opp_den) == (12, 10)
    # 한 경기 슛을 바꾸면 값이 달라진다(재료가 그 경기 값을 정말 읽는지)
    gs = _three_games()
    gs[0]["matchInfo"][0]["shoot"]["shootTotal"] = 8
    assert abs(habits.paired_diff(habits.side_totals(gs, "me"), "finish").diff - f.diff) > 1


def test_paired_diff_wider_than_independent_when_clustered():
    # 나: 5경기는 슛 20개가 다 골 · 5경기는 다 실패(경기 안 뭉침) / 상대: 매 경기 20개 중 10골 — 둘 다 결정력 50%
    games = [_game(f"c{i}", _side("me", "승", 20, 20, 20 if i < 5 else 0, 0, 0, 50),
                   _side("op", "패", 20, 20, 10, 0, 0, 50)) for i in range(10)]
    d = habits.paired_diff(habits.side_totals(games, "me"), "finish")
    indep = 100 * (0.5 * 0.5 / 200 + 0.5 * 0.5 / 200) ** 0.5     # 독립 정규 근사 5%p
    assert abs(d.diff) < 1e-9
    assert d.se > 3 * indep, (d.se, indep)      # 막지 않았으면(독립 근사) 5%p 로 좁게 나왔을 것


def test_paired_diff_possession_sqrt2_wider():
    poss = [40, 55, 60, 45, 52, 48, 70, 30]
    games = [_game(f"p{i}", _side("me", "승", 1, 1, 0, 0, 0, p), _side("op", "패", 1, 1, 0, 0, 0, 100 - p))
             for i, p in enumerate(poss)]
    d = habits.paired_diff(habits.side_totals(games, "me"), "possession")
    n = len(poss)
    m = sum(poss) / n
    var = sum((p - m) ** 2 for p in poss) / (n - 1)      # 나·상대 분산이 같다
    indep = (2 * var / n) ** 0.5
    assert abs(d.se / indep - 2 ** 0.5) < 1e-9, d.se / indep
    assert abs(d.diff - (2 * m - 100)) < 1e-9


def test_diff_clear_needs_interval_and_gap():
    m = habits.METRICS["finish"]
    assert habits.Diff(m, 100, 40, 36, 1.0).clear(3)            # 구간 4 ± 1.96 · 차 4 ≥ 3
    assert not habits.Diff(m, 100, 40, 36, 1.0).clear(5)        # 문턱 아래
    assert not habits.Diff(m, 100, 40, 36, 2.5).clear(3)        # 구간이 0 을 품는다
    assert habits.Diff(m, 100, 36, 40, 1.0).clear(3)            # 음의 차도
    assert not habits.Diff(m, 1, 40, 0, float("inf")).clear(0)  # 한 경기 — 구간 무한
    assert habits.Diff(m, 100, 40, 36, 1.0).clear(4)            # 경계: 차 == 문턱이면 띄운다("이상")
    assert not habits.Diff(m, 100, 40, 40, 0.0).clear(0)        # 경계: 구간 끝이 정확히 0 이면 0 을 품는다
    two = habits.paired_diff(habits.side_totals(_three_games()[:2], "me"), "shots")
    assert two.n == 2 and two.se < float("inf"), two             # 두 경기부터 표준오차가 있다
    empty = habits.paired_diff(habits.side_totals([], "me"), "finish")
    assert empty.n == 0 and not empty.clear(0)
    assert not habits.paired_diff(habits.side_totals([], "me"), "shots").clear(0)


# ── N19 칩 ──────────────────────────────────────────────────────────────────

def _chip_games(n=30, me_goals=(1, 1, 2), opp_goals=(3, 3, 4), shots=8):
    """슛 `shots` 개씩 — 내 골은 me_goals 를 돌려 가며, 상대는 opp_goals. 유효슛·태클은 양쪽이 같다."""
    return [_game(f"k{i}", _side("me", "승", shots, 5, me_goals[i % len(me_goals)], 10, 7, 50),
                  _side("op", "패", shots, 5, opp_goals[i % len(opp_goals)], 10, 7, 50)) for i in range(n)]


def test_strength_chips_finish_weak_only():
    t = habits.side_totals(_chip_games(), "me")
    chips = habits.strength_chips(t)
    # 손 계산: 나 40골/240슛 = 16.67% · 상대 100골/240슛 = 41.67% → −25.0%p. 유효슛·태클은 같아 칩이 아니다
    assert [c.label for c in chips] == ["결정력"], [c.text for c in chips]
    assert not chips[0].strong and chips[0].text == "결정력 -25.0%p", chips[0].text
    mirrored = habits.strength_chips(habits.side_totals(_chip_games(me_goals=(3, 3, 4), opp_goals=(1, 1, 2)), "me"))
    assert [(c.label, c.strong) for c in mirrored] == [("결정력", True)]


def test_strength_chips_floor_and_gap_are_wired():
    t = habits.side_totals(_chip_games(), "me")
    assert habits.strength_chips(t), "기본값으로는 칩이 있어야 아래 두 단언이 뜻이 있다"
    old_shots, old_gap = config.N19_MIN_SHOTS, config.N19_MIN_GAP
    try:
        config.N19_MIN_SHOTS = 241          # 슛 240개 — 하한 밑이면 구간이 깔끔해도 칩이 없다
        assert habits.strength_chips(t) == []
        config.N19_MIN_SHOTS = 240          # 경계: 슛이 정확히 하한만큼이면 칩이 있다("이상")
        assert habits.strength_chips(t)
        config.N19_MIN_SHOTS = old_shots
        config.N19_MIN_GAP = 26.0           # 차이 25%p 보다 큰 문턱
        assert habits.strength_chips(t) == []
    finally:
        config.N19_MIN_SHOTS, config.N19_MIN_GAP = old_shots, old_gap


def test_strength_chips_tackle_uses_its_own_floor():
    games = [_game(f"t{i}", _side("me", "승", 1, 1, 0, 20, 18 if i % 2 else 16, 50),
                   _side("op", "패", 1, 1, 0, 20, 10 if i % 2 else 12, 50)) for i in range(30)]
    t = habits.side_totals(games, "me")
    assert [c.label for c in habits.strength_chips(t)] == ["태클 성공"]    # 슛은 30개뿐이라 슛 칩은 하한에 걸린다
    old = config.N19_MIN_TACKLES
    try:
        config.N19_MIN_TACKLES = 601
        assert habits.strength_chips(t) == []
    finally:
        config.N19_MIN_TACKLES = old


# ── 49단계 조각 ② 구단주 분포 칩 ─────────────────────────────────────────────

class FakeDist:
    """pct(key, x) 를 표로 — (지표, 값) → 백분위, 없으면 50. absent 지표는 None(그 지표 분포를 못 씀). 물은 지표를 적는다."""

    def __init__(self, table=None, absent=()):
        self.table, self.absent, self.asked = dict(table or {}), set(absent), set()
        self.n_owners, self.ended_at = 120, "2026-10-01T09:00:00"

    def pct(self, key, x):
        self.asked.add(key)
        if key in self.absent:
            return None
        return self.table.get((key, x), 50.0)


def _cfg_set(**kw):
    keep = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    return keep


def test_dist_chip_boundaries_and_no_fallback_to_pair():
    t = habits.side_totals(_chip_games(opp_goals=(4, 3, 3)), "me")   # 골이 엇갈려야 se > 0 (diff·hi·lo 가 다른 값)
    assert [c.label for c in habits.strength_chips(t)] == ["결정력"], "전제: 같은 경기 상대 판정이면 결정력 칩이 뜬다"
    d = habits.paired_diff(t, "finish")
    weak = lambda p, hi: habits.strength_chips(t, habits.DistBasis(FakeDist({("finish", d.diff): p, ("finish", d.hi): hi}), t))
    chips = weak(10.0, 50.0)
    assert [(c.label, c.basis, c.strong, c.pct) for c in chips] == [("결정력", "dist", False, 10.0)], chips
    assert chips[0].text == "결정력 하위 10%", chips[0].text
    assert weak(10.001, 1.0) == [], "백분위가 문턱 밖인데 칩이 떴다 — 또는 같은 경기 상대 판정으로 되돌아갔다"
    assert weak(1.0, 50.001) == [], "구간 안쪽 끝이 중앙값을 넘었는데 칩이 떴다"
    strong = lambda p, lo: habits.strength_chips(t, habits.DistBasis(FakeDist({("finish", d.diff): p, ("finish", d.lo): lo}), t))
    c = strong(90.0, 50.0)
    assert [(x.strong, x.text) for x in c] == [(True, "결정력 상위 10%")], c
    assert strong(89.999, 99.0) == [] and strong(99.0, 49.999) == []
    assert habits.Chip(d, 0.2).text == "결정력 하위 1%", "0% 라고 쓰면 안 된다"


def test_dist_chip_thresholds_are_wired_to_config():
    t = habits.side_totals(_chip_games(opp_goals=(4, 3, 3)), "me")
    d = habits.paired_diff(t, "finish")
    keep = _cfg_set(OWNER_DIST_CHIP_P=20, OWNER_DIST_SURE_P=40)   # 기본값(10·50)이 아닌 값
    try:
        go = lambda p, hi: habits.strength_chips(t, habits.DistBasis(FakeDist({("finish", d.diff): p, ("finish", d.hi): hi}), t))
        assert go(15.0, 40.0), "CHIP_P 가 설정과 안 이어졌다(기본 10 이면 15 는 칩이 아니다)"
        assert go(15.0, 45.0) == [], "SURE_P 가 설정과 안 이어졌다(기본 50 이면 45 는 칩이다)"
        up = lambda p, lo: habits.strength_chips(t, habits.DistBasis(FakeDist({("finish", d.diff): p, ("finish", d.lo): lo}), t))
        assert up(85.0, 60.0), "상위 쪽 CHIP_P 가 설정과 안 이어졌다(100 − 20 = 80 이상이면 칩)"
        assert up(85.0, 59.0) == [], "상위 쪽 SURE_P 가 100 − 40 = 60 이 아니다(50 이나 40 으로 재면 59 가 칩이 된다)"
    finally:
        _cfg_set(**keep)


def test_dist_chip_unusable_metric_falls_back_and_on_target_stays_pair():
    scope = habits.side_totals(_chip_games(), "me")
    mine = habits.side_totals(_chip_games(me_goals=(3, 3, 3), opp_goals=(3, 3, 3)), "me")   # 최근 1000판은 차이 없음
    f = FakeDist(absent={"finish"})
    chips = habits.strength_chips(scope, habits.DistBasis(f, mine))
    assert [(c.label, c.basis, c.text) for c in chips] == [("결정력", "pair", "결정력 -25.0%p")], \
        "분포를 못 쓰는 지표가 화면 범위의 같은 경기 상대 판정으로 돌아가지 않았다"
    assert "on_target_rate" not in f.asked, "유효슛 비율(분포에서 뺀 지표)을 분포에 물었다"
    assert f.asked == {"finish", "tackle_rate"}


def test_dist_chips_sorted_by_group():
    games = [_game(f"o{i}", _side("me", "승", 8, 6, (1, 1, 2)[i % 3], 10, 7, 50),
                   _side("op", "패", 8, 2, (4, 3, 3)[i % 3], 10, 7, 50)) for i in range(30)]
    t = habits.side_totals(games, "me")
    fin, tk = habits.paired_diff(t, "finish"), habits.paired_diff(t, "tackle_rate")
    table = {("finish", fin.diff): 5.0, ("finish", fin.hi): 10.0, ("tackle_rate", tk.diff): 97.0, ("tackle_rate", tk.lo): 97.0}
    chips = habits.strength_chips(t, habits.DistBasis(FakeDist(table), t))
    assert [(c.label, c.basis) for c in chips] == [("태클 성공", "dist"), ("결정력", "dist"), ("유효슛 비율", "pair")], \
        [(c.label, c.basis) for c in chips]


def test_dist_basis_needs_full_window_of_manager_games():
    g = lambda i, mt=52: dict(_chip_games(1)[0], matchId=f"b{i}", matchType=mt)
    keep = _cfg_set(OWNER_DIST_MATCHES=5)   # 기본값(1000)이 아닌 값
    try:
        dist = FakeDist()
        assert habits.dist_basis(None, [g(i) for i in range(9)], "me") is None
        assert habits.dist_basis(dist, [g(i) for i in range(4)] + [g(9, 50)] * 3, "me") is None, "감독모드가 아닌 경기를 셌다"
        b = habits.dist_basis(dist, [g(i) for i in range(4)] + [g(7, 50)] + [g(i) for i in range(4, 9)], "me")
        assert b is not None and [r.match_id for r in b.my.rows] == ["b0", "b1", "b2", "b3", "b4"], "최신 N판이 아니다"
    finally:
        _cfg_set(**keep)


# ── C4 자리별 수준(50단계 ③) ─────────────────────────────────────────────────

class _Cell:
    """지수 = 첫 지표 그대로 · 백분위 = 50 + 10·x (0~100 으로 자름) — 단계 경계 20·40·60·80 은 x = −3·−1·1·3."""
    def index(self, v):
        return v[0]

    def pct(self, x):
        return max(0.0, min(100.0, 50 + 10 * x))


class _PosDist:
    def __init__(self, cells=("MF.atk", "DF.atk", "DF.def")):
        self.cells = {c: _Cell() for c in cells}


def _c4_game(mid, goal, mt=52, opp_df=True):
    """MF 줄 골 차 = goal(상대 MF 0) · DF 줄은 상대에 수비수가 없으면 그 칸만 빠진다."""
    me = {"ouid": "me", "matchDetail": {"matchResult": "승"},
          "player": [{"spId": 1, "spPosition": 14, "status": {"goal": goal}}, {"spId": 2, "spPosition": 5, "status": {}}]}
    op = {"ouid": "op", "matchDetail": {"matchResult": "패"},
          "player": [{"spId": 3, "spPosition": 14, "status": {}}] + ([{"spId": 4, "spPosition": 5, "status": {}}] if opp_df else [])}
    return {"matchId": mid, "matchType": mt, "matchInfo": [me, op]}


def test_pos_level_stage_hold_and_guards():
    keep = _cfg_set(C4_MIN_LINE_GAMES=6, OWNER_DIST_MATCHES=8)   # 기본값(300·1000)이 아닌 값
    try:
        games = [_c4_game(f"g{i}", 2) for i in range(8)]
        lv = habits.pos_level(_PosDist(), games, "me")
        mf = lv.cells[0]
        assert (mf.line, mf.side, mf.n, mf.pct, mf.stage, mf.why) == ("MF", "atk", 8, 70.0, 3, ""), mf
        assert config.C4_STAGES[mf.stage] == "에이스"
        assert lv.cells[2].stage == 2 and lv.cells[2].pct == 50.0, "DF 차 0 은 가운데 단계(잘함)"
        # 감독모드 밖 경기는 안 세고, 최신 OWNER_DIST_MATCHES 판까지만
        mixed = [_c4_game("x", 9, mt=50)] + games + [_c4_game("old", -9)] * 5
        assert habits.pos_level(_PosDist(), mixed, "me").cells[0].pct == 70.0, "감독모드 밖이나 창 밖 경기를 셌다"
        # 판수 미달 — 숫자는 내고 단계는 보류
        few = habits.pos_level(_PosDist(), games[:5], "me").cells[0]
        assert (few.n, few.pct, few.stage, few.why) == (5, 70.0, None, "games"), few
        # 범위가 경계(x = 1)에 걸침 — 0 과 2.4 를 번갈아(평균 1.2 · se 0.45 → 1.645se 아래 끝이 1 밑)
        wob = [_c4_game(f"w{i}", (0, 2.4)[i % 2]) for i in range(8)]
        c = habits.pos_level(_PosDist(), wob, "me").cells[0]
        assert c.why == "range" and c.stage is None and c.lo < 60 < c.hi, c
        # 상대에 수비수가 없는 경기는 DF 칸만 빠진다(실패가 아니다)
        no_df = games[:6] + [_c4_game(f"n{i}", 2, opp_df=False) for i in range(2)]
        lv = habits.pos_level(_PosDist(), no_df, "me")
        assert lv.cells[0].n == 8 and lv.cells[1].n == 6 and lv.cells[1].why == "", lv.cells
        # 분포에 없는 칸
        assert habits.pos_level(_PosDist(("MF.atk",)), games, "me").cells[1].why == "dist"
    finally:
        _cfg_set(**keep)


def test_pos_level_z_and_cuts_are_wired_to_config():
    keep = _cfg_set(C4_MIN_LINE_GAMES=6, OWNER_DIST_MATCHES=8)
    try:
        wob = [_c4_game(f"w{i}", (1.6, 2.4)[i % 2]) for i in range(8)]   # 평균 2 · se ≈ 0.151
        assert habits.pos_level(_PosDist(), wob, "me").cells[0].stage == 3, "전제: 기본 Z 면 판정이 난다"
        k2 = _cfg_set(C4_Z=8.0)
        try:
            assert habits.pos_level(_PosDist(), wob, "me").cells[0].why == "range", "C4_Z 가 설정과 안 이어졌다"
        finally:
            _cfg_set(**k2)
        k3 = _cfg_set(C4_CUTS=(20, 40, 60, 65))
        try:
            assert habits.pos_level(_PosDist(), wob, "me").cells[0].stage == 4, "C4_CUTS 가 설정과 안 이어졌다(70 은 65 위)"
        finally:
            _cfg_set(**k3)
    finally:
        _cfg_set(**keep)


# ── N19 전반 비중 ───────────────────────────────────────────────────────────

def _gt(period, sec):
    return (period << 24) | sec


def _goal_game(mid, mine, theirs, res="승"):
    """mine / theirs = (period, sec) 목록 — 골 기록이 점수판과 같게 goalTotal 도 맞춘다."""
    def side(ouid, goals, r):
        s = _side(ouid, r, len(goals), len(goals), len(goals), 0, 0, 50)
        s["shootDetail"] = [{"result": 3, "goalTime": _gt(*g)} for g in goals]
        return s
    inv = {"승": "패", "패": "승", "무": "무"}[res]
    return _game(mid, side("me", mine, res), side("op", theirs, inv))


def test_half_share_diff_by_hand():
    # 20경기: 내 득점은 전부 후반(1) · 내 실점은 전부 전반(0) → 전반 비중 나(실점) 100 − 상대(= 내 득점) 0
    games = [_goal_game(f"h{i}", [(1, 1200 + i)], [(0, 600 + i), (0, 900 + i)]) for i in range(20)]
    d = habits.half_share_diff(games, "me")
    assert (d.mine, d.opp, d.mine_den, d.opp_den) == (100.0, 0.0, 40, 20), d
    assert d.diff == 100.0
    # 연장(2) 골은 뺀다: 연장 골 하나를 더해도 분모가 안 변한다
    games[0] = _goal_game("h0", [(1, 1200), (2, 100)], [(0, 600), (0, 900), (2, 50), (3, 60)])
    d2 = habits.half_share_diff(games, "me")
    assert (d2.mine_den, d2.opp_den) == (40, 20), d2


# ── N17 골 흐름 ─────────────────────────────────────────────────────────────

def test_goal_flow_by_hand():
    G = _goal_game
    games = [
        G("a", [(0, 100), (0, 200), (1, 100)], [(0, 300)], "승"),              # 1:0 2:0 2:1 3:1 → 최대 리드 +2 · 승
        G("b", [(0, 100)], [(0, 200), (1, 100)], "패"),                        # 1:0 1:1 1:2 → 최대 +1 · 패
        G("c", [], [(0, 100)], "패"),                                          # 0:1 → 리드 없음
        G("d", [(0, 100), (1, 100)], [(1, 50)], "무"),                         # 1:0 1:1(후반 50) 2:1(후반 100) → 최대 +1 · 무로 둔다
        G("e", [(0, 100)], [(0, 200), (0, 300)], "패"),                        # 1:0 1:1 1:2 → 최대 +1 · 패 (무·패가 같은 수면 둘을 맞바꿔도 안 걸린다)
    ]
    gf = habits.goal_flow(games, "me", mixed=False)
    assert gf.replayed == 5 and gf.never_led == 1, (gf.replayed, gf.never_led)
    by = {r.lead: (r.win, r.draw, r.lose) for r in gf.leads}
    assert by[2] == (1, 0, 0) and by[1] == (0, 1, 2), by
    # 내 골 직전 점수 차 — a: 0(앞서는) +1(쐐기) +1(쐐기: 2:1 에서) · b: 0 · d: 0, 0(1:1 에서 넣음) · e: 0 → 앞서는 5 · 쐐기 2
    assert gf.kinds["앞서는"] == 5 and gf.kinds["쐐기"] == 2 and gf.kinds["동점"] == 0, gf.kinds
    assert sum(gf.kinds.values()) == 7, "다섯의 합 = 되짚은 내 골 수"


def test_goal_flow_held_and_unreplayable():
    same_time = _goal_game("s", [(0, 100)], [(0, 100)], "무")        # 양쪽이 같은 시각 → 순서를 못 정한다
    own_goal = _goal_game("o", [(0, 100)], [], "승")
    own_goal["matchInfo"][1]["shoot"]["goalTotalDisplay"] = 1         # 점수판에만 얹힌 자책골 → 못 되짚는다
    own_goal["matchInfo"][1]["shoot"]["goalTotal"] = 0
    own_goal["matchInfo"][0]["shoot"]["goalTotalDisplay"] = 1
    ok = _goal_game("k", [(0, 100)], [], "승")
    gf = habits.goal_flow([same_time, own_goal, ok], "me", mixed=False)
    assert (gf.held, gf.unreplayable, gf.replayed) == (1, 1, 1), (gf.held, gf.unreplayable, gf.replayed)


def test_goal_flow_trailing_and_gap_bins():
    # 0:1(20초) 1:1(600초) 2:1(1500초) 2:2(후반 600초) — 전반 분 단위: 0.33 · 10 · 25 · 후반 55
    g = _goal_game("x", [(0, 600), (0, 1500)], [(0, 20), (1, 600)], "무")
    gf = habits.goal_flow([g], "me", mixed=False)
    # 뒤진 동안 나온 골: 1:1 이 나온 직전 0:1(뒤짐) → 1골 중 내가 넣은 1골
    assert (gf.trail_goals, gf.trail_mine) == (1, 1), (gf.trail_goals, gf.trail_mine)
    # 앞서 있는 동안 나온 골: 2:1 상태(1500초 뒤)에서 상대 골 1개
    assert (gf.lead_goals, gf.lead_theirs) == (1, 1)
    # 간격 분: 9.67 · 15 · 30 → 모서리 (5, 10, 20): [<5, 5~10, 10~20, 20+] = [0, 1, 1, 1]
    assert gf.gap_bins == [0, 1, 1, 1], gf.gap_bins
    assert abs(gf.gap_median - 15.0) < 1e-9, gf.gap_median
    assert gf.kinds == {"앞서는": 1, "동점": 1, "추격": 0, "쐐기": 0, "벌리기": 0}, gf.kinds


def test_goal_flow_gap_exactly_on_an_edge_goes_up():
    # 간격이 정확히 5분 / 10분 / 20분 — 모서리는 윗칸("이상"): [<5, 5~10, 10~20, 20+] 에 하나씩 셋
    games = [_goal_game("e5", [(0, 360)], [(0, 60)], "패"),
             _goal_game("e10", [(0, 1200)], [(0, 600)], "승"),
             _goal_game("e20", [(1, 1500)], [(1, 300)], "승")]
    gf = habits.goal_flow(games, "me", mixed=False)
    assert gf.gap_bins == [0, 1, 1, 1], gf.gap_bins


def test_goal_flow_five_kinds_sum_to_my_goals():
    # 3:0 → 3 골 모두 · 0:3 → 내 골 0 · 1:3 꼴(내가 2골 뒤진 채 1골) 도 빠짐없이 한 갈래에 든다
    games = [
        _goal_game("p", [(0, 100), (0, 200), (0, 300)], [], "승"),
        _goal_game("q", [], [(0, 100), (0, 200), (0, 300)], "패"),
        _goal_game("r", [(1, 500)], [(0, 100), (0, 200), (0, 300)], "패"),
        _goal_game("s", [(0, 300)], [(0, 100), (0, 200)], "패"),          # 정확히 두 골 뒤진 채 → 추격(경계)
    ]
    gf = habits.goal_flow(games, "me", mixed=False)
    assert sum(gf.kinds.values()) == 5, gf.kinds
    assert gf.kinds["추격"] == 2 and gf.kinds["벌리기"] == 1, gf.kinds     # p 의 셋째 골 d=+2 → 벌리기 · r 의 골 d=−3 → 추격


# ── N16 하루 승률 운 범위 ────────────────────────────────────────────────────

def _pr(day, win, draw, lose, mixed):
    return PeriodRate(label=f"09/{day:02d}", win=win, draw=draw, lose=lose, day=date(2026, 9, day), mixed=mixed)


def test_daily_luck_expected_is_exact_binomial_not_4_55_percent():
    # 하루 20판 10승 10패 → p = 0.5 · 띠 0.5 ± 2√(0.25/20) = 0.2764 ~ 0.7236 → 승 수 5 이하·15 이상이 띠 밖
    # P = 2·P(X ≤ 5) = 2·21700/2^20 = 0.041389 (정규 근사의 4.55% 가 아니다)
    lk = habits.daily_luck([_pr(1, 10, 0, 10, False)])
    assert (lk.judged, lk.outside) == (1, 0), lk
    assert abs(lk.expected - 2 * 21700 / 2 ** 20) < 1e-9, lk.expected


def test_daily_luck_weak_day_is_dimmed_not_counted():
    days = [_pr(1, 10, 0, 10, False), _pr(2, 9, 0, 10, False)]        # 둘째 날 19판 < N16_MIN_DAY(20)
    lk = habits.daily_luck(days)
    assert [d.weak for d in lk.days] == [False, True], lk.days
    assert lk.judged == 1, "띠 밖 날 수 분모에서도 뺀다"
    one = habits.daily_luck([_pr(1, 10, 0, 10, False)])
    # 둘째 날은 기대값에도 안 들어간다(평균 p 는 둘 다로 구하므로 첫날 기대값은 같지 않아도 둘째 날 몫이 더해지지는 않는다)
    assert lk.expected < 2 * one.expected, lk.expected


def test_daily_luck_counts_outside_days():
    # 40판씩: 하루는 36승 4패(90%), 하루는 4승 36패(10%) → 평균 50% · 띠 ±15.8%p → 둘 다 밖
    lk = habits.daily_luck([_pr(1, 36, 0, 4, False), _pr(2, 4, 0, 36, False)])
    assert (lk.judged, lk.outside) == (2, 2), lk
    assert [d.outside for d in lk.days] == [True, True]
    assert abs(lk.days[0].lo - 34.19) < 0.05 and abs(lk.days[0].hi - 65.81) < 0.05, lk.days[0]


def test_daily_luck_mixed_uses_win_over_win_plus_lose():
    # 섞인 묶음: 하루 10승 100무 10패 → 분모 20 · 승률 50%. 한 집단이면 분모 120 · 8.3%
    mx = habits.daily_luck([_pr(1, 10, 100, 10, True)])
    sg = habits.daily_luck([_pr(1, 10, 100, 10, False)])
    assert (mx.days[0].n, round(mx.days[0].rate, 1)) == (20, 50.0), mx.days[0]
    assert (sg.days[0].n, round(sg.days[0].rate, 1)) == (120, 8.3), sg.days[0]


def test_daily_luck_required_games_table():
    # n = 2·(2σ)²·p(1−p)/d² = 8·0.25/0.0009 = 2222.2 → 2223 (3%p) · 후보 표의 "약 250판"이 아니다
    lk = habits.daily_luck([_pr(1, 10, 0, 10, False)])
    by = {r.diff: r for r in lk.required}
    assert by[3].n == 2223 and by[3].games == 2223, by[3]
    assert by[1].n == 20000 and by[5].n == 800, (by[1], by[5])
    # 섞인 계정 — 무승부가 전체의 절반이면 실제 경기 수는 두 배
    mx = habits.daily_luck([_pr(1, 10, 20, 10, True)])
    r3 = {r.diff: r for r in mx.required}[3]
    assert r3.games == math.ceil(r3.n / 0.5), r3


def test_daily_luck_config_is_wired():
    old = (config.N16_MIN_DAY, config.N16_BAND_SIGMA)
    try:
        config.N16_MIN_DAY = 21
        assert habits.daily_luck([_pr(1, 10, 0, 10, False)]).days[0].weak, "N16_MIN_DAY 배선"
        config.N16_MIN_DAY = 20
        config.N16_BAND_SIGMA = 1.0
        d = habits.daily_luck([_pr(1, 10, 0, 10, False)]).days[0]
        assert abs((d.hi - d.lo) / 2 - 100 * math.sqrt(0.25 / 20)) < 1e-6, "N16_BAND_SIGMA 배선"
    finally:
        config.N16_MIN_DAY, config.N16_BAND_SIGMA = old


def test_daily_luck_empty():
    assert habits.daily_luck([]).days == []


# ── A3 연승·연패 우연 기준 ───────────────────────────────────────────────────

def _ms(i, result, dp, when=None):
    base = datetime(2026, 9, 1, 10) + timedelta(minutes=10 * i)
    return MatchSummary(match_id=f"m{i}", match_date=when or base, match_type=52, my_nickname="me", opponent="x",
                        result=result, my_goals=0, opp_goals=0, possession=50, shoot_total=0, shoot_effective=0,
                        pass_try=0, pass_success=0, rating=0.0, draw_possible=dp)


def test_streak_actual_matches_models_longest_streaks():
    res = ["승", "승", "무", "승", "승", "승", "패", "패", "몰수승", "패", "오류", "패", "패", "패", "패"]
    ms = [_ms(i, r, True) for i, r in enumerate(res)]
    sb = habits.streak_baseline(ms, shuffles=5)
    assert (sb.win_run, sb.lose_run) == models.longest_streaks(ms) == (3, 4), sb


def test_streak_shuffle_stays_inside_layer():
    # 챔스 층(draw_possible 거짓)은 무승부가 날 수 없다 — 섞은 순서마다 그 자리에 무승부가 없어야 한다
    vals = ["승", "패"] * 15 + ["승", "무", "패", "무", "승", "패"] * 5
    strata = {False: list(range(30)), True: list(range(30, 60))}
    rng = random.Random(1)
    moved = 0
    for _ in range(40):
        cur = habits.shuffle_within_strata(vals, strata, rng)
        assert all(cur[i] != "무" for i in strata[False]), "무승부가 챔스 층 자리로 샜다"
        assert sorted(cur[:30]) == sorted(vals[:30]) and sorted(cur[30:]) == sorted(vals[30:])
        moved += cur != vals
    assert moved > 30, "섞이지 않았다"
    # 층을 합쳐 통째로 섞으면 샌다 — 위 단언에 이빨이 있는지(막지 않았다면 무슨 일이 일어났을까)
    leaked = 0
    for _ in range(40):
        cur = habits.shuffle_within_strata(vals, {"all": list(range(60))}, rng)
        leaked += any(cur[i] == "무" for i in range(30))
    assert leaked > 30, leaked


def test_streak_baseline_none_layer_stays_put():
    vals = ["승", "무", "패", "승"]
    cur = habits.shuffle_within_strata(vals, {True: [0, 1]}, random.Random(3))
    assert cur[2:] == ["패", "승"], "층 밖(None) 자리는 제자리"


def test_streak_baseline_deterministic_and_outside_judgement():
    # 승 25 연속 뒤 패 25 연속(전부 같은 층) — 섞으면 길이가 6 안팎이라 둘 다 우연 밖
    ms = [_ms(i, "승" if i < 25 else "패", True) for i in range(50)]
    a = habits.streak_baseline(ms)
    b = habits.streak_baseline(ms)
    assert a == b, "시드 고정 — 같은 목록이면 같은 값"
    assert (a.win_run, a.lose_run) == (25, 25)
    assert a.win_outside and a.lose_outside and a.win_longer == 0 and a.lose_longer == 0, a
    assert a.win_cut < 25 and a.shuffles == config.A3_SHUFFLES
    # 번갈아 나오는 순서 — 우연 안
    alt = [_ms(i, "승" if i % 2 == 0 else "패", True) for i in range(50)]
    c = habits.streak_baseline(alt)
    assert not c.win_outside and not c.lose_outside and c.win_run == 1, c


def test_streak_baseline_unshufflable_is_not_outside():
    # 전부 승 — 섞어도 같은 길이라 "우연 밖"이 아니다(97.5분위와 같다)
    ms = [_ms(i, "승", True) for i in range(30)]
    s = habits.streak_baseline(ms)
    assert s.win_run == 30 and s.win_cut == 30 and not s.win_outside, s


def test_streak_baseline_cancel_and_key():
    ms = [_ms(i, "승" if i % 3 else "패", True) for i in range(20)]
    calls = []
    out = habits.streak_baseline(ms, cancelled=lambda: calls.append(1) or True)
    assert out.win_cut == 0 and len(calls) == 1, "취소하면 섞기를 하지 않고 돌아온다"
    k1 = habits.streak_key(ms)
    ms2 = list(ms)
    ms2[3] = _ms(3, "승", True)
    assert habits.streak_key(ms2) != k1, "결과가 바뀌면 키도 바뀐다"
    assert habits.streak_key(list(ms)) == k1


def test_streak_baseline_budget_ten_thousand_games():
    import random as _r
    rnd = _r.Random(5)
    ms = [_ms(i, rnd.choice(["승", "패", "무"]), i % 3 != 0) for i in range(10000)]
    t0 = time.perf_counter()
    habits.streak_baseline(ms)
    dt = time.perf_counter() - t0
    assert dt < 6.0, f"1만 판 섞기 {dt:.2f}초 — 작업 스레드 예산(참고: 계획 R21 은 0.54초)"
    print(f"        (1만 판 × {config.A3_SHUFFLES}회 섞기 {dt:.2f}초)")


# ── N20 경기 습관 ────────────────────────────────────────────────────────────

def test_chi2_sf_known_values():
    for x, df, want in [(3.841459, 1, 0.05), (5.991465, 2, 0.05), (9.487729, 4, 0.05), (11.0705, 5, 0.05),
                        (18.307, 10, 0.05), (30.0, 10, 0.000857), (0.5, 3, 0.91893)]:
        got = habits.chi2_sf(x, df)
        assert abs(got - want) < 2e-4, (x, df, got, want)
    assert habits.chi2_sf(0, 3) == 1.0 and habits.chi2_sf(500, 3) < 1e-50


def _hour_games(spec, dp=True):
    """spec = {시(0~23 중 칸 대표): (승, 무, 패)} — 날마다 한 판씩 다른 날에."""
    out, n = [], 0
    for hour, (w, d, l) in spec.items():
        for res, k in (("승", w), ("무", d), ("패", l)):
            for _ in range(k):
                out.append(_ms(n, res, dp, when=datetime(2026, 1, 1, hour, 0) + timedelta(days=n)))
                n += 1
    return out


def _table(ms, name="시간대"):
    return next(t for t in habits.habit_tables(ms) if t.name == name)


def test_habit_no_effect_says_no_difference():
    spec = {h: (20, 0, 20) for h in range(0, 24, 3)}                  # 여덟 칸 모두 50%
    t = _table(_hour_games(spec))
    assert t.tested and not t.significant and t.stat == 0.0 and t.p == 1.0, t
    assert all(b.flag == 0 for b in t.bins)
    assert (t.df, len(set(b.group for b in t.bins))) == (7, 8), "칸이 안 합쳐졌다"


def test_habit_effect_in_one_cell_is_flagged():
    spec = {h: (20, 0, 20) for h in range(0, 24, 3)}
    spec[0] = (36, 0, 4)
    t = _table(_hour_games(spec))
    assert t.significant and t.p < 0.01, t
    assert [b.flag for b in t.bins] == [1] + [0] * 7, [b.flag for b in t.bins]


def _a6(fn):
    """A6 섞임 규칙 자체를 잰다 — 앱은 2026-10-19 부터 승÷전체(config.WIN_RATE_DRAWS_IN)라 스위치를 끈 채로."""
    def run():
        keep = config.WIN_RATE_DRAWS_IN
        config.WIN_RATE_DRAWS_IN = False
        try:
            fn()
        finally:
            config.WIN_RATE_DRAWS_IN = keep
    run.__name__ = fn.__name__
    return run


def test_habit_classes_with_draws_in_are_win_vs_rest():
    """승÷전체(기본)에선 챔스 층 한 판이 섞여도 한 집단 — 칸 0 의 무 20 은 '안 이김' 으로 세어 차이가 없다."""
    spec = {h: (20, 0, 20) for h in range(0, 24, 3)}
    spec[0] = (20, 20, 0)
    ms = _hour_games(spec) + [_ms(999, "승", False, when=datetime(2026, 1, 1, 21) + timedelta(days=999))]
    t = _table(ms)
    assert not t.mixed and not t.significant, t


@_a6
def test_habit_composition_only_difference_is_corrected_by_layers():
    # 층(무승부 가능 참·거짓)마다 승률이 다르고(30% / 70%) 칸마다 층 구성이 달라 칸 승률이 갈리지만 층 안 효과는 없다
    # 칸 0~3 에 여덟 칸 중 앞 넷만 쓴다 — 나머지 칸은 비어 합쳐진다
    ms, n = [], 0
    for hour, t_true in zip((0, 3, 6, 9), (280, 240, 200, 160)):
        t_false = 400 - t_true
        for dp, cnt, rate in ((True, t_true, 0.3), (False, t_false, 0.7)):
            wins = round(cnt * rate)
            for k in range(cnt):
                ms.append(_ms(n, "승" if k < wins else "패", dp, when=datetime(2026, 1, 1, hour) + timedelta(days=n)))
                n += 1
    t = _table(ms)
    assert t.mixed and t.tested and not t.significant and t.stat < 1e-9, t
    # 이빨: 층을 무시하고 한 덩이로 보면 같은 자료가 뚜렷하게 갈린다 — 층 보정을 빼면 이 테스트가 FAIL
    pooled = _table([_ms(i, m.result, True, when=m.match_date) for i, m in enumerate(ms)])
    assert pooled.significant, "층 보정을 안 했다면 차이가 났을 자료여야 한다"
    assert pooled.p < 0.05 <= t.p


@_a6
def test_habit_result_classes_follow_the_visible_rate():
    # 한 집단(전부 무승부 가능): 승 대 (무+패). 칸 0: 승20 무20 패0 · 나머지 승20 패20 → 칸마다 승 20/40 이라 차이 없음
    spec = {h: (20, 0, 20) for h in range(0, 24, 3)}
    spec[0] = (20, 20, 0)
    one = _table(_hour_games(spec))
    assert not one.mixed and one.tested and not one.significant, one
    # 챔스 층 한 판이 섞이면 섞임 — 승 대 패(무 뺌): 칸 0 은 승20 패0 이라 갈린다
    ms = _hour_games(spec) + [_ms(999, "승", False, when=datetime(2026, 1, 1, 21) + timedelta(days=999))]
    mx = _table(ms)
    assert mx.mixed and mx.significant and mx.bins[0].flag == 1, (mx, mx.bins[0])
    assert mx.dropped_strata == 1, "한 판뿐인 챔스 층은 검정에서 뺀다"


def test_habit_small_cells_merge_and_tiny_table_is_not_tested():
    spec = {0: (30, 0, 30), 3: (2, 0, 1), 6: (30, 0, 30), 9: (30, 0, 30)}     # 칸 1 은 기대 빈도 < 5
    t = _table(_hour_games(spec))
    assert t.bins[1].group in (t.bins[0].group, t.bins[2].group), "작은 칸은 이웃과 합친다"
    assert t.tested
    tiny = _table(_hour_games({0: (2, 0, 2), 3: (1, 0, 3)}))
    assert not tiny.tested and tiny.note, tiny


def test_habit_gap_and_session_bins_by_hand():
    #  9:00 · 9:10(간격 10) · 9:25(15) · 10:30(65 — 새 세션) · 10:35(5)
    times = [(9, 0), (9, 10), (9, 25), (10, 30), (10, 35)]
    ms = [_ms(i, "승", True, when=datetime(2026, 9, 1, h, m)) for i, (h, m) in enumerate(times)]
    gap = _table(ms, "직전 경기와의 간격")
    assert [b.games for b in gap.bins] == [2, 1, 0, 1], [b.games for b in gap.bins]    # 첫 경기는 간격이 없어 뺀다
    ses = _table(ms, "세션 안 몇 번째 판")
    assert [b.games for b in ses.bins] == [2, 3, 0, 0], [b.games for b in ses.bins]     # 순번 1,2,3,1,2
    assert ses.bins[0].label == "첫 판"


def test_habit_gap_boundaries():
    # 간격: 정확히 13분은 '13분 이하' · 13분 1초는 위 칸. 세션: 정확히 30분은 안 끊기고 30분 1초는 끊긴다
    base = datetime(2026, 9, 1, 9, 0)
    offs = [timedelta(0), timedelta(minutes=13), timedelta(minutes=26, seconds=1)]       # 간격 13분 · 13분 1초
    ms = [_ms(i, "승", True, when=base + o) for i, o in enumerate(offs)]
    gap = _table(ms, "직전 경기와의 간격")
    assert [b.games for b in gap.bins] == [1, 1, 0, 0], [b.games for b in gap.bins]
    t0 = datetime(2026, 9, 2, 9, 0)
    ms = [_ms(0, "승", True, when=t0), _ms(1, "승", True, when=t0 + timedelta(minutes=30)),
          _ms(2, "승", True, when=t0 + timedelta(minutes=60, seconds=1))]                # 간격 30분 · 30분 1초
    ses = _table(ms, "세션 안 몇 번째 판")
    assert [b.games for b in ses.bins] == [2, 1, 0, 0], [b.games for b in ses.bins]      # 순번 1, 2, 1


# ── 변이 검증(2026-10-11)이 짚은 경계 — 위 테스트가 안 지키던 줄 ─────────────────────────

def test_binom_outside_prob_edges_by_hand():
    f = habits._binom_outside_prob
    assert f(0, 0.5, 0.0, 1.0) == 0.0, "판수 0 이면 확률 0(0 나누기 없음)"
    assert f(10, 0.0, 0.0, 1.0) == 0.0 and f(10, 1.0, 0.0, 1.0) == 0.0     # p 가 0·1 이어도 로그 오류 없음
    assert f(10, 1.0, 1.0, 1.0) == 0.0, "k/n 이 띠 끝과 딱 같으면 안쪽"
    assert f(10, 1.0, 0.0, 0.5) == 1.0
    # 띠 끝이 격자 위(0.25 · 0.75)인 n = 20 — 승 5·15 는 안쪽: 밖 = 2·P(X ≤ 4) = 2·6196/2^20
    assert abs(f(20, 0.5, 0.25, 0.75) - 2 * 6196 / 2 ** 20) < 1e-12


def test_daily_luck_all_draw_mixed_has_no_division_by_zero():
    lk = habits.daily_luck([_pr(1, 0, 30, 0, True)])
    assert lk.days[0].n == 0 and lk.days[0].weak and lk.judged == 0, lk
    assert all(r.n == 0 and r.games == 0 for r in lk.required), lk.required


def test_chi2_sf_zero_degrees_of_freedom():
    assert habits.chi2_sf(5.0, 0) == 1.0


def test_streak_baseline_unshufflable_counts_nothing_longer():
    ms = [_ms(i, "승", True) for i in range(30)]
    s = habits.streak_baseline(ms)
    assert s.win_longer == 0 and s.lose_longer == 0 and not s.lose_outside, s    # 같은 길이는 '더 긴'이 아니다


def test_habit_possible_counts_only_draw_possible_games():
    spec = [(0, "승", True)] * 6 + [(0, "무", True)] * 3 + [(0, "패", True)] * 1 + [(0, "승", False)] * 5 + [(0, "패", False)] * 5
    ms = [_ms(i, r, dp, when=datetime(2026, 1, 1, h) + timedelta(days=i)) for i, (h, r, dp) in enumerate(spec)]
    b = _table(ms).bins[0]
    assert (b.games, b.possible, b.possible_draw) == (20, 10, 3), b       # 무승부가 날 수 있던 10판 중 무 3


def test_habit_expected_exactly_at_threshold_is_kept():
    # 두 칸 모두 5승 5패 → 기대 빈도가 딱 5(문턱과 같음) — 합치지도 층을 빼지도 않고 판정한다(p = 1)
    spec = {0: (5, 0, 5), 3: (5, 0, 5)}
    t = _table(_hour_games(spec))
    assert t.tested and t.df == 1 and t.p == 1.0 and not t.significant, t
    assert t.bins[0].group != t.bins[1].group


def test_habit_small_bin_merges_into_smaller_neighbor_left_on_tie():
    spec = {0: (30, 0, 30), 3: (2, 0, 1), 6: (30, 0, 30)}              # 가운데가 작고 양옆 판 수가 같다 → 왼쪽
    t = _table(_hour_games(spec))
    assert t.bins[1].group == t.bins[0].group != t.bins[2].group, [b.group for b in t.bins]
    spec = {0: (30, 0, 30), 3: (2, 0, 1), 6: (40, 0, 40)}              # 오른쪽이 크면 왼쪽(작은 쪽)
    t = _table(_hour_games(spec))
    assert t.bins[1].group == t.bins[0].group, [b.group for b in t.bins]
    spec = {0: (40, 0, 40), 3: (2, 0, 1), 6: (30, 0, 30)}              # 왼쪽이 크면 오른쪽
    t = _table(_hour_games(spec))
    assert t.bins[1].group == t.bins[2].group, [b.group for b in t.bins]


def test_habit_two_groups_are_tested():
    t = _table(_hour_games({0: (30, 0, 10), 3: (10, 0, 30)}))
    assert t.tested and t.df == 1 and t.significant, t       # 나머지 빈 칸이 합쳐져 두 덩이 — 자유도 1


# ── N18 공격 루트 ───────────────────────────────────────────────────────────

def _shot(type_=1, inpen=True, ax=None, ay=None, x=0.9, y=0.5, result=1):
    """shootDetail 한 칸. ax 가 None 이면 어시 없음(넥슨 값 그대로 assist 거짓 + (0.5, 0.5))."""
    sd = {"type": type_, "inPenalty": inpen, "x": x, "y": y, "result": result, "assist": ax is not None,
          "assistX": 0.5 if ax is None else ax, "assistY": 0.5 if ay is None else ay}
    return sd


def test_classify_route_order_and_edges():
    cr = habits.classify_route
    assert cr(_shot(14)) == "기타" and cr(_shot(99, inpen=False)) == "기타", "이름 없는 종류는 어디에도 안 끼운다"
    assert cr(_shot(14, inpen=True, ax=0.8, ay=0.1)) == "기타", "기타가 박스·어시보다 먼저"
    assert cr(_shot(8, inpen=False)) == cr(_shot(9)) == "세트피스"
    assert cr(_shot(3, inpen=False)) == "헤더", "박스 밖 헤더도 헤더(중거리보다 먼저)"
    assert cr(_shot(3, ax=0.9, ay=0.1)) == "헤더", "헤더는 크로스보다 먼저"
    assert cr(_shot(1, inpen=False, ax=0.8, ay=0.1)) == "중거리", "박스 밖은 어시가 있어도 중거리"
    assert cr(_shot(1)) == "어시 없음" and cr(_shot(2)) == "어시 없음"
    both = _shot(1, ax=0.5, ay=0.5)
    both["assist"] = True
    assert cr(both) == "어시 없음", "assist 참이어도 (0.5, 0.5) 면 어시 없음(R13)"
    assert cr(_shot(1, ax=0.8, ay=0.5)) == "중앙 연계"
    # 측면 경계: |ay − 0.5| 이 config.N18_WIDE_Y(0.30) 이상이면 측면 — 정확히 0.30 은 측면
    assert cr(_shot(1, ax=0.8, ay=0.2)) == "크로스" and cr(_shot(1, ax=0.8, ay=0.8)) == "크로스"
    assert cr(_shot(1, ax=0.8, ay=0.21)) == "중앙 연계"
    # 컷백: 어시 x ≥ 0.90 이고 슛 x 보다 골라인 쪽(ax > x) — 경계 0.90 은 컷백, 슛이 더 골라인 쪽이면 크로스
    assert cr(_shot(1, ax=0.95, ay=0.1, x=0.9)) == "컷백"
    assert cr(_shot(1, ax=0.90, ay=0.1, x=0.85)) == "컷백"
    assert cr(_shot(1, ax=0.89, ay=0.1, x=0.85)) == "크로스"
    assert cr(_shot(1, ax=0.95, ay=0.1, x=0.97)) == "크로스"
    assert cr(_shot(1, ax=0.95, ay=0.1, x=0.95)) == "크로스", "같은 x 는 뒤로 내준 게 아니다"


def test_classify_route_thresholds_are_wired():
    # 기본값이 아닌 값으로 재야 배선이 끊겨도 참이 되는 일이 없다
    old = config.N18_WIDE_Y, config.N18_BYLINE_X
    try:
        config.N18_WIDE_Y = 0.45
        assert habits.classify_route(_shot(1, ax=0.8, ay=0.1)) == "중앙 연계", "N18_WIDE_Y 를 넓히면 같은 어시가 중앙이 된다"
        config.N18_WIDE_Y = old[0]
        config.N18_BYLINE_X = 0.99
        assert habits.classify_route(_shot(1, ax=0.95, ay=0.1, x=0.9)) == "크로스", "N18_BYLINE_X 를 올리면 컷백이 크로스로"
    finally:
        config.N18_WIDE_Y, config.N18_BYLINE_X = old


def _route_game(mid, mine, theirs, res="승"):
    inv = {"승": "패", "패": "승", "무": "무"}.get(res, res)
    a = _side("me", res, len(mine), 0, 0, 0, 0, 50)
    b = _side("op", inv, len(theirs), 0, 0, 0, 0, 50)
    a["shootDetail"], b["shootDetail"] = mine, theirs
    return _game(mid, a, b)


def test_attack_routes_by_hand():
    cut = _shot(1, ax=0.95, ay=0.1, x=0.9, result=3)          # 컷백 골
    games = [
        _route_game("a", [cut, _shot(1, ax=0.95, ay=0.1, x=0.9, result=1), _shot(3, result=3), _shot(1, inpen=False, result=2)],
                    [cut, _shot(8, result=3)]),
        _route_game("b", [_shot(1, ax=0.8, ay=0.5, result=3), _shot(99, result=1)], [_shot(1, result=1)], "패"),
    ]
    games.append(_route_game("bad", [_shot(1, ax=0.95, ay=0.1, x=0.9)], [], "오류"))            # 승·무·패가 아닌 경기는 뺀다
    games.append(_route_game("odd", [dict(_shot(1), x=None), dict(_shot(1), result=7)], []))      # 좌표 없음 · 모르는 result 는 슛이 아니다
    ar = habits.attack_routes(games, "me")
    by = {r.route: (r.mine_shots, r.mine_goals, r.opp_shots, r.opp_goals) for r in ar.rows}
    assert [r.route for r in ar.rows] == list(habits.ROUTES)
    assert by["컷백"] == (2, 1, 1, 1), by                     # 상대 슛에도 같은 분류기
    assert by["헤더"] == (1, 1, 0, 0) and by["중거리"] == (1, 0, 0, 0), by
    assert by["중앙 연계"] == (1, 1, 0, 0) and by["기타"] == (1, 0, 0, 0), by
    assert by["세트피스"] == (0, 0, 1, 1) and by["어시 없음"] == (0, 0, 1, 0), by
    assert (ar.mine_total, ar.opp_total, ar.games) == (6, 3, 3), (ar.mine_total, ar.opp_total, ar.games)
    assert sum(r.mine_shots for r in ar.rows) == ar.mine_total, "모든 슛이 한 루트에 든다"
    cb = next(r for r in ar.rows if r.route == "컷백")
    assert (cb.mine_finish, cb.opp_finish) == (50.0, 100.0)
    assert cb.diff.mine_den == 2 and cb.diff.opp_den == 1, "차이 구간의 분모 = 루트 슛 수"


def test_attack_routes_enough_floor_is_wired():
    games = [_route_game(f"g{i}", [_shot(1)] * 5, [_shot(1)] * 5) for i in range(4)]       # 내 슛 20개
    old = config.N18_MIN_SHOTS
    try:
        config.N18_MIN_SHOTS = 21
        assert not habits.attack_routes(games, "me").enough
        config.N18_MIN_SHOTS = 20                       # 경계: 정확히 하한이면 된다("이상")
        assert habits.attack_routes(games, "me").enough
    finally:
        config.N18_MIN_SHOTS = old
    assert not habits.attack_routes([], "me").enough


# ── A2 FC 보상 (43단계) ───────────────────────────────────────────────────────

def _det(day, hour, result, division, mt=52, ouid="me"):
    return {"matchId": f"{day}-{hour}", "matchType": mt, "matchDate": f"2026-09-{day:02d}T{hour:02d}:00:00",
            "matchInfo": [{"ouid": ouid, "division": division, "matchDetail": {"matchResult": result}},
                          {"ouid": "x", "division": 1000, "matchDetail": {"matchResult": "패"}}]}


def test_win_rewards_counts_by_hand():
    ds = [_det(1, 9, "승", 900), _det(1, 10, "승", 800), _det(1, 11, "패", 900), _det(1, 12, "무", 800),
          _det(2, 9, "몰수승", 900), _det(2, 10, "승", 1000), _det(2, 11, "승", None), _det(2, 12, "오류", 900)]
    r = stats_.win_rewards(stats_.win_divisions(ds, "me"))
    # 900 승 2(일반 1 + 몰수 1) = 30 · 800 승 1 = 20 · 1000 승은 0 · division 없는 승 1 은 모름 · "오류" 는 분모에서도 뺀다
    assert (r.games, r.wins, r.rewarded_wins, r.fc) == (7, 5, 3, 50), r
    assert (r.unknown_wins, r.unknown_games, r.forfeit_wins) == (1, 1, 1), r
    assert r.by_division == {900: (2, 30), 800: (1, 20)}, r.by_division
    # division 없는 판은 FC 합계에 못 넣으니 100판당 분모에서도 뺀다(7판이 아니라 6판)
    assert abs(r.per_100 - 50 / 6 * 100) < 1e-9, r.per_100


def test_win_rewards_ignores_other_match_types_and_other_players():
    ds = [_det(1, 9, "승", 800, mt=40), _det(1, 10, "승", 800), _det(1, 11, "승", 800, ouid="other")]
    r = stats_.win_rewards(stats_.win_divisions(ds, "me"))
    assert (r.games, r.fc) == (1, 20), "다른 경기 종류·내가 안 낀 경기는 센 것이 없어야"


def test_reward_table_dates_and_first_row_is_date_min():
    assert all(start == date.min for start, _d, _fc in config.FC_REWARDS), "첫 줄 시작일은 맨 앞이어야 그 전 승리가 0 이 안 된다"
    assert {d for _s, d, _f in config.FC_REWARDS} == {models.CHAMPION_DIVISION_ID, models.SUPER_CHAMPION_DIVISION_ID}
    ds = [_det(1, 9, "승", 900), _det(10, 9, "승", 900)]
    table = ((date.min, 900, 15), (date(2026, 9, 5), 900, 25))   # 9/5 부터 값이 바뀜
    r = stats_.win_rewards(stats_.win_divisions(ds, "me"), table)
    assert r.fc == 15 + 25, "경기 당시의 날짜로 값을 골라야(같은 표를 지난 경기에 소급하지 않는다)"
    assert stats_.reward_for(900, date(2026, 9, 5), table) == 25 and stats_.reward_for(900, date(2026, 9, 4), table) == 15
    assert stats_.reward_for(1000, date(2026, 9, 5), table) == 0


def test_win_divisions_skips_unreadable():
    ds = [_det(1, 9, "승", 900), {"matchInfo": [{"ouid": "me", "matchDetail": {"matchResult": "승"}}]},
          {"matchDate": "엉망", "matchInfo": [{"ouid": "me", "matchDetail": {"matchResult": "승"}}]}]
    assert len(stats_.win_divisions(ds, "me")) == 1


# ── 55 구단 머리 줄 · 점수 리포트 ────────────────────────────────────────────

def _wd(day, hour, div, outcome):
    return stats_.WinDivision(datetime(2026, 10, day, hour), div, outcome, False)


def test_club_day_by_hand():
    rows = [_wd(17, 23, 900, "승"), _wd(18, 0, 900, "승"), _wd(18, 9, 800, "승"), _wd(18, 10, 1000, "승"),
            _wd(18, 11, 900, "무"), _wd(18, 23, 900, "패"), _wd(19, 0, 900, "승")]
    d = habits.club_day(rows, date(2026, 10, 18))           # 이 PC 날짜 0~24시 — 17일 23시·19일 0시는 밖
    assert (d.win, d.draw, d.lose, d.games) == (3, 1, 1, 5), d
    assert d.fc == 15 + 20, d.fc                              # 1000 승리는 보상 없음
    assert d.rate(False) == 60.0 and d.rate(True) == 75.0
    assert habits.club_day(rows, date(2026, 10, 16)).games == 0


def test_day_delta_uses_last_point_before():
    pts = [(date(2026, 10, 10), 4000.0), (date(2026, 10, 13), 4050.0), (date(2026, 10, 14), 4020.0)]
    assert habits.day_delta(pts, date(2026, 10, 14)) == -30.0
    assert habits.day_delta(pts, date(2026, 10, 13)) == 50.0, "전날이 없으면 더 앞 점과"
    assert habits.day_delta(pts, date(2026, 10, 12)) is None and habits.day_delta(pts, date(2026, 10, 10)) is None


def test_first_reach_counts_through_the_hit_game():
    start = datetime(2026, 10, 1)
    rows = [_wd(1, 9, 1100, "승"), _wd(1, 10, 1000, "패"), _wd(2, 9, 900, "승"), _wd(3, 9, 800, "무"),
            _wd(3, 10, 900, "승")]
    ch = habits.first_reach(rows + [stats_.WinDivision(datetime(2026, 9, 30), 900, "승", False)], start, 900)
    assert (ch.games, ch.at, ch.from_start, ch.gap) == (3, datetime(2026, 10, 2, 9), True, False), ch  # 시즌 전 경기는 안 센다
    sc = habits.first_reach(list(reversed(rows)), start, 800)            # 순서 무관
    assert sc.games == 4, sc
    assert habits.first_reach(rows, start, 700) is None
    late = habits.first_reach(rows, datetime(2026, 9, 1), 900)            # 저장이 시즌 시작보다 한 달 늦다
    assert not late.from_start
    edge = habits.first_reach(rows, datetime(2026, 10, 1, 9) - timedelta(days=config.N12_START_DAYS), 900)
    assert edge.from_start, "정확히 N12_START_DAYS 뒤는 처음부터로 본다"


def test_first_reach_gap_flag():
    start = datetime(2026, 10, 1)
    g = config.H5_GAP_DAYS
    rows = [_wd(1, 9, 1100, "승"), stats_.WinDivision(datetime(2026, 10, 1, 9) + timedelta(days=g, seconds=1), 900, "승", False)]
    assert habits.first_reach(rows, start, 900).gap
    rows[1] = stats_.WinDivision(datetime(2026, 10, 1, 9) + timedelta(days=g), 900, "승", False)
    assert not habits.first_reach(rows, start, 900).gap, "정확히 H5_GAP_DAYS 는 빈 것이 아니다"


def test_score_report_by_hand():
    d0 = date(2026, 10, 1)
    vals = [4000, 4100, 4050, 4200, 3900, 3950, 4300]
    pts = [(d0 + timedelta(days=i), float(v)) for i, v in enumerate(vals)]
    r = habits.score_report(pts)
    assert r.points == 7 and r.avg == sum(vals) / 7 and r.now == 4300
    assert (r.peak, r.peak_day) == (4300, d0 + timedelta(days=6)), "시즌 최고점"
    assert (r.max_dd, r.dd_day) == (300, d0 + timedelta(days=4)), "최고점(그때까지) 뒤 가장 깊은 하락 — 4200 → 3900"
    assert r.dd_peak == 4200, ("하락이 시작된 고점은 시즌 최고점(4300)이 아니라 그때까지의 고점 — 57 ② 리뷰 [상]", r.dd_peak)
    assert r.day_avg == 300 / 6 and (r.up_days, r.steps) == (4, 6), r
    assert habits.score_report(pts[:config.R_MIN_POINTS - 1]) is None, "R_MIN_POINTS 미만인데 냈다"
    # 하루 안 최고점(그날 마지막 점이 아닌 기록)은 marks 로 — 머리 줄의 시즌 최고점과 같은 값이 나와야
    marks = sorted(pts + [(d0 + timedelta(days=3), 4400.0)])
    r = habits.score_report(pts, marks)
    assert (r.peak, r.peak_day, r.max_dd, r.dd_day) == (4400, d0 + timedelta(days=3), 500, d0 + timedelta(days=4)), r
    assert r.dd_peak == 4400, r.dd_peak
    assert r.avg == sum(vals) / 7, "평균은 하루 한 점으로"
    flat = [(d0 + timedelta(days=i), float(v)) for i, v in enumerate([4000, 4000, 4010, 4010, 4005, 4005, 4020])]
    assert habits.score_report(flat).up_days == 2, "제자리인 날을 오른 날로 셌다"


def test_decisive_rate_drops_draws():
    rows = [_wd(1, 9, 900, "승"), _wd(2, 9, 900, "무"), _wd(3, 9, 900, "패"), _wd(4, 9, 900, "승"),
            _wd(1, 8, 900, "패")]
    rate, n = habits.decisive_rate(rows, datetime(2026, 10, 1, 9))
    assert abs(rate - 200 / 3) < 1e-9 and n == 3, (rate, n)            # 10-01 08시 패는 시즌 전
    assert habits.decisive_rate([_wd(1, 9, 900, "무")], datetime(2026, 10, 1)) is None


# ── 55 D1 달력 ────────────────────────────────────────────────────────────

def _day_games(day, w, d, l, div=900):
    out = []
    for i, k in enumerate(["승"] * w + ["무"] * d + ["패"] * l):
        out.append(stats_.WinDivision(datetime(2026, 10, day, 9) + timedelta(minutes=i), div, k, False))
    return out


def test_calendar_days_by_hand():
    rows = _day_games(1, 9, 0, 1) + _day_games(2, 1, 0, 9) + _day_games(3, 4, 0, 0) + _day_games(4, 3, 1, 1)
    one = lambda _d: 0  # noqa: E731 — 한 시즌
    days = habits.calendar_days(rows, date(2026, 10, 1), date(2026, 10, 31), one, False)
    assert [d.day.day for d in days] == [1, 2, 3, 4]
    p = 17 / 29                                                    # 승 9+1+4+3 / 29경기
    d1, d2, d3, d4 = days
    assert abs(d1.base - p * 100) < 1e-9
    half = config.N16_BAND_SIGMA * math.sqrt(p * (1 - p) / 10)
    assert abs(d1.hi - (p + half) * 100) < 1e-9 and abs(d1.lo - (p - half) * 100) < 1e-9
    assert (d1.rate, d1.arrow, d1.fc) == (90.0, 1, 9 * 15), d1     # 90% > 띠 위 끝(약 89.0%)
    assert (d2.rate, d2.arrow) == (10.0, -1), d2
    assert (d3.judged, d3.arrow) == (False, 0), "D1_MIN_GAMES 미만은 100% 라도 안 잰다"
    assert (d4.judged, d4.games, d4.n, d4.arrow) == (True, 5, 5, 0), "정확히 D1_MIN_GAMES 판은 잰다 · 무승부도 분모"
    calls = []
    habits.calendar_days(rows, date(2026, 10, 1), date(2026, 10, 31), lambda d: calls.append(d) or 0, False)
    assert sorted(calls) == [date(2026, 10, i) for i in (1, 2, 3, 4)], "season_key 를 날짜마다 한 번이 아니라 경기마다 불렀다(시즌표 훑기 · 1만 경기 167ms)"
    only = habits.calendar_days(rows, date(2026, 10, 2), date(2026, 10, 3), one, False)
    assert [d.day.day for d in only] == [2, 3], "first~last 둘 다 포함"
    assert abs(only[0].base - p * 100) < 1e-9, "기준선은 고른 구간이 아니라 그 시즌 전부"


def test_calendar_min_games_comes_from_config():
    rows = _day_games(1, 9, 0, 1) + _day_games(3, 4, 0, 0)
    old = config.D1_MIN_GAMES
    try:
        config.D1_MIN_GAMES = 4
        d3 = habits.calendar_days(rows, date(2026, 10, 1), date(2026, 10, 31), lambda _d: 0, False)[1]
        assert d3.judged, "D1_MIN_GAMES 를 config 에서 안 읽는다"
    finally:
        config.D1_MIN_GAMES = old


def test_calendar_baseline_is_the_days_own_season():
    # 옛 시즌 18승 2패(90%) · 새 시즌 이틀 1승 9패씩(10%) — 같은 달 안 시즌 경계(1회차 [중])
    rows = _day_games(1, 18, 0, 2) + _day_games(11, 1, 0, 9) + _day_games(12, 1, 0, 9)
    key = lambda d: d < date(2026, 10, 10)  # noqa: E731
    days = habits.calendar_days(rows, date(2026, 10, 1), date(2026, 10, 31), key, False)
    assert [(d.day.day, round(d.base), d.arrow) for d in days] == [(1, 90, 0), (11, 10, 0), (12, 10, 0)], \
        [(d.day, d.base, d.arrow) for d in days]                   # 둘을 합친 기준선(50%)이면 새 시즌 날이 ▼


def test_cal_summary_per_day():
    rows = _day_games(1, 9, 0, 1) + _day_games(2, 1, 1, 0, div=800)
    s = habits.cal_summary(rows)
    assert (s.days, s.games, s.win, s.draw, s.lose, s.fc) == (2, 12, 10, 1, 1, 9 * 15 + 20), s
    assert s.per_day == 6.0 and s.fc_per_day == (9 * 15 + 20) / 2
    assert abs(s.rate(False) - 1000 / 12) < 1e-9
    assert habits.cal_summary([]).per_day == 0.0


# ── 57 K4 이력 ────────────────────────────────────────────────────────────────

def _hist_detail(i: int, nick: str, form_positions: list[int], ouid="me"):
    when = datetime(2026, 10, 1, 9) + timedelta(hours=i) - timedelta(hours=9)   # 원문은 UTC(63) — 단언은 KST 9시부터
    return {"matchId": f"h{i}", "matchDate": when.isoformat(timespec="seconds"),
            "matchInfo": [{"ouid": ouid, "nickname": nick, "player": [{"spPosition": p} for p in form_positions]},
                          {"ouid": "opp", "nickname": "상대", "player": []}]}


def test_history_runs_merge_blips_and_rejoin():
    """K4 — 손으로 센 순서: 닉네임 A×5 → B×1(깜빡임) → A×4 → C×K4_MIN_RUN. 포메이션은 4백 → 3백 바꿈 한 번."""
    import habits
    four = [0, 3, 4, 5, 6, 13, 14, 15, 16, 25, 21]        # 수비 4 · 미드 4 · 공격 2(GK·자리 번호는 stats._LINES)
    three = [0, 4, 5, 6, 13, 14, 15, 16, 18, 25, 21]
    names = ["A"] * 5 + ["B"] + ["A"] * 4 + ["C"] * config.K4_MIN_RUN
    ds = [_hist_detail(i, n, four if i < 7 else three) for i, n in enumerate(names)]
    ds.reverse()                                              # 최신순으로 줘도 시간순으로 센다
    runs = habits.history_runs(ds, "me")
    nick = [r for r in runs if r.kind == "nickname"]
    assert [(r.value, r.games, r.blips) for r in nick] == [("A", 9, [("B", 1)]), ("C", config.K4_MIN_RUN, [])], \
        [(r.value, r.games, r.blips) for r in nick]
    assert nick[0].start.hour == 9 and nick[0].end == datetime(2026, 10, 1, 18), (nick[0].start, nick[0].end)
    form = [r for r in runs if r.kind == "formation"]
    assert len(form) == 2 and form[0].games == 7 and form[1].games == len(names) - 7, [(r.value, r.games) for r in form]
    assert form[0].value != form[1].value
    short = habits.history_runs([_hist_detail(0, "Z", four)], "me", kinds=("nickname",))
    assert [(r.value, r.games) for r in short] == [("Z", 1)], "맨 앞 짧은 구간을 버렸다"
    assert habits.history_runs([_hist_detail(0, "Z", four, ouid="남")], "me") == [], "내 줄이 없는 경기를 셌다"
    # 빈 선발(몰수 등 — 0-0-0-0-0) 경기가 사이에 끼어도 구간을 안 끊는다(이 PC 실측 28경기 · 안 빼면 1경기짜리 구간이 생긴다)
    gap = [_hist_detail(i, "A", [] if i in (3, 4, 5) else four) for i in range(10)]
    runs = habits.history_runs(gap, "me", kinds=("formation",))
    assert [(r.games, r.blips) for r in runs] == [(7, [])], [(r.value, r.games, r.blips) for r in runs]


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
