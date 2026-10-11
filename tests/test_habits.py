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
