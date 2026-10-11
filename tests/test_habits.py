"""habits.py — 내 경기 습관·기준선 계산(2.6.1). 지어낸 입력의 손으로 센 값으로 단언한다(네트워크 없음).

    python tests/test_habits.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import habits  # noqa: E402


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
