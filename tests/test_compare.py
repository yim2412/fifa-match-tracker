"""구단주 비교 확장(50단계 D1) — 차이 요약 z · 표 값 방향 · 자리 줄 짝짓기. 지어낸 경기 상세(네트워크 없음).

    python tests/test_compare.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stats as stats_  # noqa: E402


def _side(ouid, *, goals=2, conceded=1, poss=50, rating=6.5, shots=10, on=None, pass_try=100, pass_ok=80,
          tk_try=20, tk_ok=10, result="승", players=()):
    if on is None:
        on = shots // 2   # 슛 수가 바뀌어도 유효슛 비율은 그대로(50%) — 한 행만 움직이는 테스트용
    return {
        "ouid": ouid,
        "matchDetail": {"matchResult": result, "possession": poss, "averageRating": rating},
        "shoot": {"goalTotal": goals, "shootTotal": shots, "effectiveShootTotal": on},
        "pass": {"passTry": pass_try, "passSuccess": pass_ok},
        "defence": {"tackleTry": tk_try, "tackleSuccess": tk_ok},
        "player": list(players),
    }


def _match(me, opp):
    return {"matchId": "m", "matchInfo": [me, opp]}


def _games(ouid, n, **vary):
    """n 경기 — vary 의 값은 (짝수 판 값, 홀수 판 값). 같은 값이 아니라 번갈아야 분산이 0 이 아니다."""
    out = []
    for i in range(n):
        kw = {k: (v[i % 2] if isinstance(v, tuple) else v) for k, v in vary.items()}
        out.append(_match(_side(ouid, **kw), _side("x", goals=kw.get("conceded", 1), conceded=kw.get("goals", 2))))
    return out


def test_picks_only_rows_over_threshold_and_ranks_by_z():
    mine = _games("me", 12, shots=(14, 16), goals=(2, 3))          # 슛 15 평균
    theirs = _games("op", 12, shots=(6, 8), goals=(2, 3))          # 슛 7 평균, 득점은 같다
    got = stats_.compare_summary(mine, "me", theirs, "op")
    labels = [d.label for d in got]
    assert "경기당 슛" in labels, labels
    assert "평균 득점" not in labels, "같은 평균인데 올렸다"
    d = next(x for x in got if x.label == "경기당 슛")
    assert d.z > stats_.COMPARE_Z_MIN and d.mine_better and abs(d.mine - 15) < 1e-9 and abs(d.opp - 7) < 1e-9
    # 손으로 센 z: 14·16 을 6번씩 → 평균 15, 표본분산 12/11 · 6·8 도 같은 분산 → SE = √(2·(12/11)/12), z = 8/SE
    assert abs(d.z - 8 / (2 * (12 / 11) / 12) ** 0.5) < 1e-9, d.z


def test_small_difference_is_not_listed():
    # 평균 0.5 차이 · 분산 1 안팎 → z ≈ 1.2 — 문턱(2.5) 아래라 요약에 없다
    mine = _games("me", 12, poss=(50, 52))
    theirs = _games("op", 12, poss=(50.5, 52.5))
    assert stats_.compare_summary(mine, "me", theirs, "op") == []


def test_lower_is_better_row_flips_tone():
    # 평균 실점: 클수록 나쁨 — 내 실점이 훨씬 많으면 z>0 이어도 "내게 아쉬움"
    mine = [_match(_side("me", conceded=c), _side("op", goals=c)) for c in ((4, 5) * 6)]
    theirs = [_match(_side("op", conceded=c), _side("x", goals=c)) for c in ((0, 1) * 6)]
    got = {d.label: d for d in stats_.compare_summary(mine, "me", theirs, "op")}
    d = got["평균 실점"]
    assert d.z > 0 and not d.mine_better and not d.higher_is_better, d


def test_nothing_when_same_or_constant_or_too_few():
    a = _games("me", 12, shots=(10, 12))
    b = _games("op", 12, shots=(10, 12))
    assert stats_.compare_summary(a, "me", b, "op") == [], "같은 분포인데 요약이 나왔다"
    # 값이 모두 같아 분산 0 → 분모 0 이면 행을 뺀다(0 으로 나누지 않는다)
    c = _games("me", 12, shots=10)
    e = _games("op", 12, shots=3)
    assert "경기당 슛" not in [d.label for d in stats_.compare_summary(c, "me", e, "op")]
    # 한쪽이 9경기면 전부 뺀다 — 같은 큰 차이라도
    few = _games("op", stats_.COMPARE_MIN_GAMES - 1, shots=(1, 2))
    big = _games("me", 12, shots=(14, 16))
    assert stats_.compare_summary(big, "me", few, "op") == []
    ok = _games("op", stats_.COMPARE_MIN_GAMES, shots=(1, 2))
    assert stats_.compare_summary(big, "me", ok, "op"), "문턱 경기 수에서는 나와야 한다"


def test_table_value_direction_must_agree():
    mine = _games("me", 12, shots=(14, 16))
    theirs = _games("op", 12, shots=(6, 8))
    right = stats_.compare_summary(mine, "me", theirs, "op", {"경기당 슛": (15.0, 7.0)})
    assert [d.label for d in right] == ["경기당 슛"] and (right[0].mine, right[0].opp) == (15.0, 7.0)
    # 표(합계 비율)가 경기별 평균과 반대 방향이면 고르지 않는다
    assert stats_.compare_summary(mine, "me", theirs, "op", {"경기당 슛": (7.0, 15.0)}) == []
    # 표 값이 같으면(방향 없음) 역시 뺀다
    assert stats_.compare_summary(mine, "me", theirs, "op", {"경기당 슛": (9.0, 9.0)}) == []


def test_at_most_four_rows_biggest_z_first():
    mine = _games("me", 12, shots=(14, 16), goals=(5, 6), poss=(60, 62), rating=(8.0, 8.2), on=(9, 10),
                  pass_ok=(90, 92), tk_ok=(15, 16))
    theirs = _games("op", 12, shots=(6, 8), goals=(1, 2), poss=(40, 42), rating=(5.0, 5.2), on=(1, 2),
                    pass_ok=(60, 62), tk_ok=(3, 4))
    got = stats_.compare_summary(mine, "me", theirs, "op")
    assert len(got) == stats_.COMPARE_TOP == 4, [d.label for d in got]
    zs = [abs(d.z) for d in got]
    assert zs == sorted(zs, reverse=True)


def test_non_result_matches_are_dropped():
    # "오류" 경기는 summarize 와 같이 뺀다 — 10경기에서 하나가 오류면 9경기라 요약이 없다
    mine = _games("me", 10, shots=(14, 16))
    mine[0]["matchInfo"][0]["matchDetail"]["matchResult"] = "오류"
    theirs = _games("op", 12, shots=(6, 8))
    assert stats_.compare_summary(mine, "me", theirs, "op") == []


def test_ratio_rows_skip_zero_attempt_games():
    # 슛이 없는 경기는 유효슛 비율 0% 가 아니라 그 행에서 빠진다
    pm = stats_._per_match([_match(_side("me", shots=0, on=0), _side("x")),
                            _match(_side("me", shots=10, on=5), _side("x"))], "me")
    assert pm["유효슛 비율"] == [50.0], pm["유효슛 비율"]
    assert pm["경기당 슛"] == [0.0, 10.0]


def _pl(sp, pos, **st):
    return {"spId": sp, "spPosition": pos, "status": st}


def test_line_pairs_pick_most_used_card_per_line():
    codes = {"GK": 0, "DF": 3, "MF": 12, "FW": 24}
    assert [stats_.position_line(c) for c in codes.values()] == list(codes), "테스트의 자리 코드가 틀렸다"
    mk = lambda sp_fw, sp_df: _match(_side("me", players=[
        _pl(1, 0), _pl(sp_df, 3, tackle=2, block=1), _pl(sp_fw, 24, shoot=4, goal=1, effectiveShoot=2),
        _pl(99, stats_.SUB_POSITION, shoot=9), _pl(77, 999, shoot=1)]), _side("x"))   # 77 = 모르는 자리 코드 — 세 경기 다 나와도 어느 줄에도 안 든다
    mine = [mk(200, 300), mk(200, 300), mk(201, 301)]
    opp = [_match(_side("op", players=[_pl(500, 24, shoot=2)]), _side("x"))]
    pairs = {p.line: p for p in stats_.line_pairs(mine, "me", opp, "op")}
    assert list(pairs) == ["FW", "DF", "GK"], list(pairs)
    fw = pairs["FW"]
    assert (fw.mine.sp_id, fw.mine.games) == (200, 2), "가장 많이 세운 카드가 아니다"
    assert abs(fw.mine.values["슛"] - 4.0) < 1e-9 and abs(fw.mine.values["골"] - 1.0) < 1e-9
    assert (fw.opp.sp_id, fw.opp.games) == (500, 1)
    assert pairs["DF"].opp is None, "상대는 수비수 기록이 없다"
    assert {99, 77}.isdisjoint({c.sp_id for p in pairs.values() for c in (p.mine, p.opp) if c}), "교체 명단·모르는 자리가 들어갔다"


def test_line_pairs_tie_goes_to_smaller_card_whatever_the_order():
    for first, second in ((400, 300), (300, 400)):
        mine = [_match(_side("me", players=[_pl(first, 24, shoot=1)]), _side("x")),
                _match(_side("me", players=[_pl(second, 24, shoot=1)]), _side("x"))]
        assert stats_.line_pairs(mine, "me", [], "op")[0].mine.sp_id == 300, (first, second)


def test_line_pairs_merges_same_card_across_slots_in_line():
    # 같은 카드가 줄 안 두 자리(LB·RB 가 아니라 LCB·RCB)에 번갈아 섰어도 한 카드로 센다
    mine = [_match(_side("me", players=[_pl(7, 3, tackle=2)]), _side("x")),
            _match(_side("me", players=[_pl(7, 6, tackle=4)]), _side("x"))]
    assert stats_.position_line(3) == stats_.position_line(6) == "DF"
    card = stats_.line_pairs(mine, "me", [], "op")[0].mine
    assert (card.sp_id, card.games) == (7, 2) and abs(card.values["태클"] - 3.0) < 1e-9, card


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
