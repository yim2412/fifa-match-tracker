"""records.py — 88 C1·U1 업적. 지어낸 경기의 손으로 센 값으로 단언한다(네트워크 없음).
업적마다 "그 조건이 없으면 안 생긴다" 를 먼저 단언한다(지키는 줄을 빼도 통과하는 빈 검사가 안 되게).

    python tests/test_records.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import records  # noqa: E402
from seasons import Season  # noqa: E402

T0 = datetime(2026, 10, 1, 12)   # KST


def _d(i: int, outcome: str, gf: int = 1, ga: int = 0, *, goals=None, div=None, forfeit=False, when=None, mt=None):
    """경기 하나. goals = [(내 골인가, 분, spId)] — 없으면 점수대로 지어낸다(내 골 먼저). 원문 시각은 UTC(63 — 글자 그대로 9시간 뺀다)."""
    when = when or T0 + timedelta(hours=i)
    if goals is None:
        goals = [(True, 10 + k, 101) for k in range(gf)] + [(False, 60 + k, 201) for k in range(ga)]

    def shots(mine: bool):
        return [{"result": 3, "goalTime": (0 if m < 45 else 1) << 24 | int((m - (0 if m < 45 else 45)) * 60), "spId": sp,
                 "x": 0.9, "y": 0.5, "type": 1} for mm, m, sp in goals if mm == mine]
    res = ("몰수" if forfeit else "") + outcome
    opp_res = ("몰수" if forfeit else "") + {"승": "패", "패": "승", "무": "무"}[outcome]
    d = {"matchId": f"m{i}", "matchDate": (when - timedelta(hours=9)).isoformat(timespec="seconds"),
         "matchInfo": [{"ouid": "me", "division": div, "matchDetail": {"matchResult": res},
                        "shoot": {"goalTotal": gf}, "shootDetail": shots(True)},
                       {"ouid": "opp", "matchDetail": {"matchResult": opp_res},
                        "shoot": {"goalTotal": ga}, "shootDetail": shots(False)}]}
    if mt is not None:
        d["matchType"] = mt
    return d


def _keys(achs, key=None):
    return [(a.key, a.match_id) for a in achs if key is None or a.key == key]


def test_streaks_follow_longest_streaks_rule():
    two = [_d(0, "승"), _d(1, "승")]
    assert _keys(records.achievements(two, "me"), "streak3") == [], "2연승에 3연승이 붙었다"
    ds = [_d(0, "승"), _d(1, "승"), _d(2, "승", forfeit=True),          # 몰수승도 연승에 센다(longest_streaks 와 같다)
          _d(3, "무"),                                                 # 무승부는 끊는다
          _d(4, "승"), _d(5, "승"), _d(6, "승"), _d(7, "승"), _d(8, "승"),
          _d(9, "패"), _d(10, "패"), _d(11, "패")]                     # 연패는 업적이 아니다
    got = records.achievements(list(reversed(ds)), "me")             # 최신순으로 줘도 시간순으로 센다
    assert _keys(got, "streak3") == [("streak3", "m2"), ("streak3", "m6")], _keys(got)
    assert _keys(got, "streak5") == [("streak5", "m8")], _keys(got)
    assert _keys(got, "streak10") == []


def test_big_win_and_forfeit():
    four = [_d(0, "승", 4, 0)]
    assert _keys(records.achievements(four, "me"), "bigwin") == [], "4골 승리에 붙었다"
    ds = [_d(0, "승", 5, 2), _d(1, "패", 5, 6), _d(2, "승", 6, 0, forfeit=True)]
    got = records.achievements(ds, "me")
    assert _keys(got, "bigwin") == [("bigwin", "m0")], _keys(got)
    assert got[0].note == "5 : 2"


def test_clean_run_skips_forfeit_and_breaks_on_concede():
    near = [_d(0, "승", 1, 0), _d(1, "승", 1, 0), _d(2, "승", 2, 1)]
    assert _keys(records.achievements(near, "me"), "clean") == [], "2연속 무실점에 붙었다"
    ds = [_d(0, "승", 1, 0), _d(1, "무", 0, 0), _d(2, "패", 0, 3, forfeit=True), _d(3, "승", 2, 0),
          _d(4, "승", 1, 0), _d(5, "패", 0, 1), _d(6, "승", 1, 0)]
    got = records.achievements(ds, "me")
    assert _keys(got, "clean") == [("clean", "m3")], _keys(got)    # 몰수는 건너뛴다 · 넷째 무실점에 다시 붙지 않는다


def test_comeback_needs_two_goal_deficit_and_matching_score():
    one = [_d(0, "승", 2, 1, goals=[(False, 5, 201), (True, 20, 101), (True, 30, 101)])]
    assert _keys(records.achievements(one, "me"), "comeback") == [], "1골 차 역전에 붙었다"
    two = [(False, 5, 201), (False, 10, 201), (True, 50, 101), (True, 60, 102), (True, 80, 103)]
    got = records.achievements([_d(0, "승", 3, 2, goals=two)], "me")
    assert _keys(got, "comeback") == [("comeback", "m0")], _keys(got)
    assert got[0].note.startswith("2골 뒤지다")
    # 자책골(슛 기록에 없는 골)이 끼면 판정 안 함 — 점수 4:2 인데 슛 기록 내 골은 셋
    assert _keys(records.achievements([_d(0, "승", 4, 2, goals=two)], "me"), "comeback") == []
    # 뒤지다 비긴 경기는 아니다
    tie = [(False, 5, 201), (False, 10, 201), (True, 50, 101), (True, 60, 102)]
    assert _keys(records.achievements([_d(0, "무", 2, 2, goals=tie)], "me"), "comeback") == []


def test_hattrick_one_player():
    spread = [(True, 10, 101), (True, 20, 101), (True, 30, 102)]
    assert _keys(records.achievements([_d(0, "승", 3, 0, goals=spread)], "me"), "hattrick") == [], "두 선수 3골에 붙었다"
    three = [(True, 10, 101), (True, 20, 101), (True, 30, 101), (False, 40, 201)]
    got = records.achievements([_d(0, "패", 3, 4, goals=three + [(False, 41, 201)] * 3)], "me")
    hat = [a for a in got if a.key == "hattrick"]
    assert [(a.match_id, a.sp_id) for a in hat] == [("m0", 101)], hat   # 지고도 해트트릭은 해트트릭
    opp = [(False, 10, 201), (False, 20, 201), (False, 30, 201)]
    assert _keys(records.achievements([_d(0, "패", 0, 3, goals=opp)], "me"), "hattrick") == [], "상대 해트트릭을 셌다"


def test_boundaries():
    """경계값 정의 — 반개구간·같은 점수·정각(boundary 변이가 초록이던 여섯 줄)."""
    # 승부차기 승(정규 2:2)은 0:2 에서 따라붙었어도 역전승이 아니다
    tie = [(False, 5, 201), (False, 10, 201), (True, 50, 101), (True, 60, 102)]
    assert _keys(records.achievements([_d(0, "승", 2, 2, goals=tie)], "me"), "comeback") == [], "승부차기 승을 역전승으로 셌다"
    s = _season(1, 20)
    # 시즌 끝 경계가 지금과 같으면 끝난 시즌 · 시작 정각 점은 그 시즌 첫 점 · 같은 점수는 갱신이 아니다
    rows = [{"taken_at": s.start_at.isoformat(), "elo": 1000.0}, _elo(3, 12, 1000.0), _elo(4, 12, 1001.0)]
    got = records.achievements([], "me", seasons=[s], elo_rows=rows, now=s.end_at)
    assert [a.note for a in got if a.key == "peak"] == ["1,001.00"], got
    high_start = [{"taken_at": s.start_at.isoformat(), "elo": 1005.0}, _elo(3, 12, 1004.0), _elo(4, 12, 1004.5)]
    got = records.achievements([], "me", seasons=[s], elo_rows=high_start, now=s.end_at)
    assert _keys(got, "peak") == [], "시작 정각 점을 시즌 밖으로 봤다(1004.5 를 갱신으로 셌다)"
    # 첫 경기가 정확히 시작 + N12_START_DAYS 이면 시작부터 저장한 것
    at = s.start_at + timedelta(days=config.N12_START_DAYS)
    got = records.achievements([_d(0, "승", div=900, when=at)], "me", seasons=[s], now=datetime(2026, 10, 30))
    assert _keys(got, "champ") == [("champ", "m0")], _keys(got)
    # 칩 — 정확히 7일 전과 지금은 안에 든다
    now = T0 + timedelta(days=config.RECORD_RECENT_DAYS)
    a_old = records.Achievement("bigwin", "x", T0, "a")
    a_now = records.Achievement("bigwin", "x", now, "b")
    assert [a.match_id for a in records.recent([a_old, a_now], now)] == ["b", "a"]


def test_goal_flow_only_where_it_can_matter():
    """역전승(이기고 실점 ≥ 차)·해트트릭(득점 ≥ 3)이 될 수 없는 경기는 골 흐름을 안 푼다 — 1만 4천 경기 약 400 → 160ms(11-09)."""
    calls = []
    keep = records.match_goals
    records.match_goals = lambda d, o: calls.append(d["matchId"]) or keep(d, o)
    try:
        ds = [_d(0, "승", 1, 0), _d(1, "승", 2, 1), _d(2, "패", 0, 3), _d(3, "승", 3, 2), _d(4, "패", 2, 5), _d(5, "승", 4, 1)]
        records.achievements(ds, "me")
    finally:
        records.match_goals = keep
    assert calls == ["m3", "m5"], calls   # m3 = 역전 가능 · m5 = 해트트릭 가능(4골) · m4 는 졌고 2골이라 둘 다 아님


def test_milestones_skip_forfeit():
    old = config.RECORD_MILESTONES
    config.RECORD_MILESTONES = (3,)
    try:
        ds = [_d(0, "승"), _d(1, "승", forfeit=True), _d(2, "패", 0, 1), _d(3, "승"), _d(4, "승")]
        got = records.achievements(ds, "me")
        assert _keys(got, "games3") == [("games3", "m3")], _keys(got)   # 몰수 뺀 셋째 경기
        assert _keys(got, "wins3") == [("wins3", "m4")], _keys(got)
        assert [k for k, _t in records.kinds()][-2:] == ["games3", "wins3"]
    finally:
        config.RECORD_MILESTONES = old


def test_other_mode_and_other_account_ignored():
    ds = [_d(i, "승", mt=config.DEFAULT_MATCH_TYPE + 1) for i in range(5)]
    assert records.achievements(ds, "me") == [], "다른 모드 경기를 셌다"
    assert records.achievements([_d(i, "승") for i in range(5)], "남") == [], "내 줄이 없는 경기를 셌다"


def _season(start_day: int, end_day: int) -> Season:
    return Season(1, "시즌 1", datetime(2026, 10, start_day).date(), datetime(2026, 10, end_day).date())


def _elo(day: int, hour: int, elo: float) -> dict:
    return {"taken_at": datetime(2026, 10, day, hour).isoformat(timespec="seconds"), "elo": elo}


def test_season_peak_needs_known_season():
    rows = [_elo(2, 12, 1000), _elo(3, 12, 1100), _elo(4, 12, 1050), _elo(5, 12, 1200)]
    now = datetime(2026, 10, 30)
    assert _keys(records.achievements([], "me", elo_rows=rows, now=now), "peak") == [], "시즌을 모르는데 셌다"
    got = records.achievements([], "me", seasons=[_season(1, 20)], elo_rows=rows, now=now)
    peaks = [a for a in got if a.key == "peak"]
    assert [(a.when.day, a.note, a.match_id) for a in peaks] == [(3, "1,100.00", None), (5, "1,200.00", None)], peaks
    # 지금 시즌 — season_span 이 안다고 할 때만
    cur = records.achievements([], "me", current_start=datetime(2026, 10, 1, 8), current_known=False, elo_rows=rows, now=now)
    assert _keys(cur, "peak") == []
    cur = records.achievements([], "me", current_start=datetime(2026, 10, 1, 8), current_known=True, elo_rows=rows, now=now)
    assert len(_keys(cur, "peak")) == 2
    # 시즌 경계 — 다음 시즌 첫 점은 갱신이 아니다(앞 시즌 최고와 견주지 않는다)
    two = [_season(1, 4), _season(4, 20)]
    got = records.achievements([], "me", seasons=two, elo_rows=rows, now=now)
    assert [a.when.day for a in got if a.key == "peak"] == [3, 5], [a.when for a in got]


def test_first_champions_needs_season_from_start():
    s = _season(1, 20)                       # 시작 경계 10-01 08시
    now = datetime(2026, 10, 30)
    ds = [_d(0, "승", div=1100, when=datetime(2026, 10, 1, 12)), _d(1, "승", div=900, when=datetime(2026, 10, 2, 12)),
          _d(2, "승", div=800, when=datetime(2026, 10, 3, 12)), _d(3, "승", div=900, when=datetime(2026, 10, 4, 12))]
    assert _keys(records.achievements(ds, "me", now=now), "champ") == [], "시즌을 모르는데 셌다"
    got = records.achievements(ds, "me", seasons=[s], now=now)
    assert _keys(got, "champ") == [("champ", "m1")] and _keys(got, "superchamp") == [("superchamp", "m2")], _keys(got)
    late = [_d(i, "승", div=900, when=datetime(2026, 10, 1, 12) + timedelta(days=config.N12_START_DAYS + 1, hours=i))
            for i in range(2)]
    assert _keys(records.achievements(late, "me", seasons=[s], now=now), "champ") == [], "저장이 시즌 중간부터인데 셌다"


def test_summary_by_match_recent():
    ds = [_d(i, "승", 5, 0) for i in range(6)]
    achs = records.achievements(ds, "me")
    rows = {r.key: r for r in records.summary(achs, seasons_known=False)}
    assert "peak" not in rows and "champ" not in rows, "시즌을 모르는데 ②③ 줄이 있다"
    assert (rows["bigwin"].count, rows["bigwin"].last_match_id) == (6, "m5")
    assert rows["bigwin"].first == T0 and rows["streak10"].count == 0 and rows["streak10"].first is None
    assert "peak" in {r.key for r in records.summary(achs)}
    tags = records.by_match(achs)
    # 종류 순서표 순 · 지어낸 다섯 골이 전부 spId 101 이라 해트트릭도 붙는다
    assert tags["m2"] == ["3연승", "5골 이상 넣고 승리", "3경기 연속 무실점", "내 선수 해트트릭"], tags["m2"]
    rec = records.recent(achs, T0 + timedelta(hours=5))
    assert len(rec) == config.RECORD_RECENT_MAX and rec[0].match_id == "m5", rec
    assert records.recent(achs, T0 + timedelta(days=config.RECORD_RECENT_DAYS + 1)) == [], "7일 지난 것을 칩에 올렸다"


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
