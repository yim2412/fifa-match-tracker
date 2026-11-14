"""표시한 판 거르기 감싸개 — 109단계 조각 ①-1(J1·J2). 손으로 만든 경기·가짜 함수로 잰다(네트워크 없음).

감싸개(core_api._wrap)가 하는 일을 **잰다**: F 는 적힌 인자에서 표시한 번호를 빼고(위치·이름 인자 모두), 묶음이 비면 목록을
새로 안 만들고, 섞임은 호출자가 exclude 를 안 넘겼을 때만 전역을 넣고, S 는 안 건드린다. 그리고 진짜 분석 함수 몇 개에서
"표시한 판은 승·무·패에서 빠지고 넥슨 기록(등급·보상)은 그대로" 인지 본다.

    python tests/test_filter.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import core_api as core  # noqa: E402
import habits  # noqa: E402
import records  # noqa: E402
import sessions as ss  # noqa: E402
import stats  # noqa: E402
from models import CHAMPION_DIVISION_ID, drop_ids  # noqa: E402
from test_records import _d as _detail  # noqa: E402
from test_sessions import NOW, _m, _pace_data  # noqa: E402

ME = "me"


@contextmanager
def excluding(*ids):
    """표시 묶음을 잠깐 바꿔 끼운다 — 끝나면 꼭 비운다(다른 테스트가 표시한 판을 물려받지 않게)."""
    core.set_excluded(ids)
    try:
        yield
    finally:
        core.set_excluded(())


def _ms(results):
    return [_m(i, r, datetime(2026, 10, 1) + timedelta(hours=i)) for i, r in enumerate(results)]


def _spy(log):
    def fn(matches=None, details=None, extra=0, exclude=frozenset()):
        log.append((matches, details, extra, exclude))
        return matches
    return fn


# ── 감싸개 자체 — 가짜 함수로 ──────────────────────────────────────────────────
def test_f_drops_by_position_and_by_name_for_both_item_shapes():
    log = []
    w = core._wrap("spy", _spy(log), "F", ("matches", "details"))
    ms = _ms(["승", "무", "패"])                                      # match_id m0 m1 m2 (MatchSummary)
    ds = [{"matchId": "m0"}, {"matchId": "m1"}, {"matchId": "m2"}]   # 경기 상세 dict
    with excluding("m1"):
        out = w(ms, ds)                                              # 위치 인자
        assert [m.match_id for m in out] == ["m0", "m2"] and [d["matchId"] for d in log[-1][1]] == ["m0", "m2"], log[-1]
        out = w(details=ds, matches=ms, extra=5)                     # 이름 인자 · 순서 바꿈
        assert [m.match_id for m in out] == ["m0", "m2"] and [d["matchId"] for d in log[-1][1]] == ["m0", "m2"]
        assert log[-1][2] == 5, "거를 인자가 아닌 인자를 건드렸다"
        w(ms, details=ds)                                            # 섞어서
        assert len(log[-1][0]) == 2 and len(log[-1][1]) == 2
        w(ms, None)                                                  # None 인 인자는 그대로
        assert log[-1][1] is None and len(log[-1][0]) == 2
        out = w(tuple(ms))                                           # 목록이 아니어도 돈다
        assert [m.match_id for m in out] == ["m0", "m2"]
    assert len(ms) == 3 and len(ds) == 3, "원래 목록을 고쳤다"


def test_f_reads_the_global_once_so_two_lists_use_the_same_version():
    seen = []

    def fn(matches, details):
        seen.append((len(matches), len(details)))
    w = core._wrap("spy2", fn, "F", ("matches", "details"))
    ms, ds = _ms(["승"] * 3), [{"matchId": f"m{i}"} for i in range(3)]
    flip = {"n": 0}
    real = core._drop_ids

    def flipping(items, ex):            # 첫 목록을 거른 직후 전역을 바꿔 끼운다 — 한 번만 읽었다면 둘째도 같은 묶음이어야
        out = real(items, ex)
        if not flip["n"]:
            flip["n"] = 1
            core.set_excluded(())
        return out
    core._drop_ids = flipping
    try:
        with excluding("m0"):
            w(ms, ds)
    finally:
        core._drop_ids = real
    assert seen == [(2, 2)], seen


def test_empty_set_calls_the_original_with_the_same_objects():
    log = []
    w = core._wrap("spy", _spy(log), "F", ("matches", "details"))
    ms, ds = _ms(["승", "패"]), [{"matchId": "m0"}]
    assert core.excluded() == frozenset()
    out = w(ms, ds)
    assert out is ms and log[-1][0] is ms and log[-1][1] is ds, "묶음이 빈데 목록을 새로 만들었다"
    # 보호 장치를 재는 테스트는 먼저 "막지 않았으면 무엇이 일어났을지" — 켠 채로는 새 목록이 나온다
    with excluding("m0"):
        assert w(ms, ds) is not ms


def test_signature_is_computed_once_not_per_call():
    log = []
    w = core._wrap("spy", _spy(log), "F", ("matches",))
    real = core._inspect.signature
    core._inspect.signature = lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출마다 시그니처를 다시 계산했다(M43)"))
    try:
        with excluding("m0"):
            w(_ms(["승", "패"]))
    finally:
        core._inspect.signature = real
    assert len(log) == 1


def test_mix_global_fills_exclude_only_when_the_caller_did_not_pass_one():
    log = []
    w = core._wrap("spy", _spy(log), "MIX", ("matches",))
    ms = _ms(["승"])
    # ① 전역이 비었고 exclude=X → X 가 그대로 간다
    w(ms, exclude=frozenset({"X"}))
    assert log[-1][3] == frozenset({"X"}), log[-1]
    # ② 전역이 Y 이고 exclude 를 안 넘김 → Y 가 간다 · 목록은 안 거른다(섞임은 함수 안에서 뺀다)
    with excluding("Y"):
        out = w(ms)
        assert log[-1][3] == frozenset({"Y"}) and out is ms
        # ③ 전역이 Y 이고 exclude=빈 것 → 아무것도 안 빠진다(덮지도 합치지도 않는다 · W4)
        w(ms, exclude=frozenset())
        assert log[-1][3] == frozenset(), "호출자가 넘긴 빈 묶음을 전역이 덮었다"
        # ④ 위치 인자로 넘긴 exclude 도 넘긴 것으로 본다(M77)
        w(ms, None, 0, frozenset({"Z"}))
        assert log[-1][3] == frozenset({"Z"}), log[-1]
        w(ms, None, 0, frozenset())
        assert log[-1][3] == frozenset(), "위치 인자 exclude 를 못 봤다"
        # ⑤ 합치지 않는다
        w(ms, exclude=frozenset({"Z"}))
        assert log[-1][3] == frozenset({"Z"})


def test_s_and_pass_are_not_touched():
    log = []
    ms = _ms(["승", "패"])
    with excluding("m0"):
        for kind in ("S", "PASS"):
            w = core._wrap("spy", _spy(log), kind, ())
            assert w(ms) is ms and log[-1][0] is ms
    # 표에서도: S·통과는 실제 함수 그대로(테스트 모드가 아닐 때) — 불리지 않는 함수까지 매번 감싸면 느려진다(M43)
    if core._CALLED is None:
        for name in ("parse_match", "win_pct", "win_divisions", "filter_matches", "window_of", "latest_match_with"):
            assert getattr(core, name) is getattr(core.raw, name), name
        for name in ("summarize", "side_totals", "club_day", "season_review"):
            assert getattr(core, name) is not getattr(core.raw, name) and hasattr(getattr(core, name), "__wrapped__"), name


def test_trace_mode_wraps_everything_and_records_names_even_for_name_imports():
    code = ("import core_api\n"
            "from core_api import pct, summarize\n"
            "pct(1, 2); summarize([])\n"
            "print(sorted(core_api.called_names()))\n"
            "print(hasattr(core_api.parse_match, '__wrapped__'), core_api.parse_match is core_api.raw.parse_match)\n"
            "core_api.reset_called(); print(sorted(core_api.called_names()))\n")
    env = {**os.environ, core.TRACE_ENV: "1", "FIFA_NO_CRED": "1"}
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=os.path.dirname(_HERE), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "['pct', 'summarize']", lines          # 이름으로 import 한 호출도 기록된다
    assert lines[1] == "True False", lines                    # S 도 감싼다 · raw 는 원래 함수
    assert lines[2] == "[]", lines
    off = subprocess.run([sys.executable, "-c", "import core_api; core_api.pct(1, 2); print(core_api.called_names())"],
                         env={k: v for k, v in env.items() if k != core.TRACE_ENV}, cwd=os.path.dirname(_HERE),
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert off.stdout.strip() == "set()", off.stdout       # 배포판은 기록하지 않는다


def test_filter_version_is_zero_when_nothing_is_excluded():
    v0 = core.bump_filter_version()
    assert core.filter_version() == 0
    with excluding("a"):
        assert core.filter_version() == v0
        v1 = core.bump_filter_version()
        assert core.filter_version() == v1 > v0
    assert core.filter_version() == 0
    core.set_excluded(["a", "b"])
    assert core.excluded() == frozenset({"a", "b"}) and isinstance(core.excluded(), frozenset)
    core.set_excluded(None)
    assert core.excluded() == frozenset()


def test_drop_ids_fails_loudly_on_a_blank_id():
    rows = [stats.WinDivision(datetime(2026, 10, 1), 900, "승", False)]          # match_id 기본값 ""
    assert drop_ids(rows, frozenset()) is rows, "묶음이 비었는데 id 를 따졌다"
    try:
        drop_ids(rows, frozenset({"m0"}))
    except ValueError:
        pass
    else:
        raise AssertionError("빈 id 줄이 있는데 조용히 지나갔다 — 표시한 판이 숫자에 남는다(M33)")
    with excluding("m0"):
        try:
            core.club_day(rows, date(2026, 10, 1))
        except ValueError:
            pass
        else:
            raise AssertionError("섞임 함수가 빈 id 를 못 잡았다")


# ── 진짜 분석 함수로 ───────────────────────────────────────────────────────────
def test_real_f_functions_drop_the_shown_match():
    ms = _ms(["승", "승", "패", "무"])                                # m0~m3 · 최신순이 아니어도 개수·종류만 본다
    base = core.summarize(ms)
    assert (base.win, base.draw, base.lose, base.total) == (2, 1, 1, 4)
    with excluding("m0"):
        s = core.summarize(ms)
        assert (s.win, s.draw, s.lose, s.total) == (1, 1, 1, 3), s
        assert core.wdl(ms) == (1, 1, 1)
        assert core.error_count(ms) == 0 and [m.match_id for m in core.recent_games(ms, 3)] == ["m3", "m2", "m1"]
        assert core.raw.summarize(ms).total == 4, "raw 가 걸러졌다"
        assert core.raw.wdl(ms) == (2, 1, 1)
    assert core.wdl(ms) == (2, 1, 1)
    # 최신순 연승 — 맨 앞 판을 빼면 연속이 한 판 줄어든다
    latest_first = _ms(["승", "승", "승", "패"])
    assert core.current_streak(latest_first) == ("승", 3)
    with excluding("m0"):
        assert core.current_streak(latest_first) == ("승", 2)
    # 상세 목록 — 표시한 판의 골이 합계에서 빠진다
    ds = [_detail(0, "승", 3, 0), _detail(1, "패", 0, 1)]
    full = core.side_totals(ds, ME)
    with excluding("m0"):
        assert core.side_totals(ds, ME) != full


def test_two_detail_lists_of_a_pair_function_are_both_filtered():
    mine = [_detail(0, "승", 2, 0), _detail(1, "승", 1, 0)]
    theirs = [{**_detail(0, "승", 2, 0), "matchId": "o0"}, {**_detail(1, "승", 1, 0), "matchId": "o1"}]
    plan = core.FILTER_PLAN["line_pairs"]
    assert plan == ("F", ("my_details", "opp_details"))
    seen = []
    w = core._wrap("pair", lambda my_details, my_ouid, opp_details, opp_ouid: seen.append((my_details, opp_details)), "F", plan[1])
    with excluding("m0", "o1"):
        w(mine, ME, theirs, "opp")
    assert [d["matchId"] for d in seen[0][0]] == ["m1"] and [d["matchId"] for d in seen[0][1]] == ["o0"], seen


def _rows(ds):
    return stats.win_divisions(ds, ME)


def test_win_divisions_carry_the_match_id():
    ds = [_detail(0, "승", div=900), _detail(1, "패", div=900)]
    assert [w.match_id for w in _rows(ds)] == ["m0", "m1"]


def test_mix_club_day_calendar_and_summary_drop_only_the_wdl_side():
    day = datetime(2026, 10, 1, 12)
    ds = [_detail(i, k, div=900, when=day + timedelta(minutes=i)) for i, k in enumerate(["승", "승", "패"])]
    rows = _rows(ds)
    full = habits.club_day(rows, day.date())
    assert (full.win, full.draw, full.lose) == (2, 0, 1) and full.fc > 0, "FC 가 0 이면 아래 단언이 비어 있다"
    with excluding("m0"):
        c = core.club_day(rows, day.date())
        assert (c.win, c.draw, c.lose) == (1, 0, 1), c
        assert c.fc == full.fc, "표시한 판의 FC 보상이 빠졌다 — 넥슨이 올려 준 값은 그대로여야 한다"
        # 호출자가 exclude 를 직접 넘기면 전역을 안 쓴다
        c2 = core.club_day(rows, day.date(), exclude=frozenset())
        assert (c2.win, c2.lose) == (2, 1)
        k = lambda d: "s"  # noqa: E731
        cal = core.calendar_days(rows, day.date(), day.date(), k, False)
        base = habits.calendar_days(rows, day.date(), day.date(), k, False)
        assert (cal[0].win, cal[0].lose) == (1, 1) and (base[0].win, base[0].lose) == (2, 1)
        assert cal[0].fc == base[0].fc and cal[0].n == 2 and base[0].n == 3
        s = core.cal_summary(rows)
        s0 = habits.cal_summary(rows)
        assert (s.win, s.lose, s.days, s.fc) == (1, 1, s0.days, s0.fc) and s.fc > 0
        fr = core.first_reach(rows, datetime(2026, 10, 1), 900)
        assert fr is not None and fr.games == habits.first_reach(rows, datetime(2026, 10, 1), 900).games, "H5 도달 판수는 S"
    # 모든 판을 빼도 날은 남고 FC 는 그대로(경기 있던 날) · 승·무·패만 0
    with excluding("m0", "m1", "m2"):
        c = core.club_day(rows, day.date())
        assert (c.win, c.draw, c.lose) == (0, 0, 0) and c.fc == full.fc


def test_nexon_records_do_not_depend_on_the_shown_set():
    day = datetime(2026, 10, 1, 12)
    ds = [_detail(i, k, div=900, when=day + timedelta(minutes=i)) for i, k in enumerate(["승", "승", "패"])]
    plain = (_rows(ds), core.win_rewards(_rows(ds)).fc, core.division_trend(ds, ME), core.match_formations(ds, ME))
    with excluding("m0", "m1"):
        shown = (core.win_divisions(ds, ME), core.win_rewards(core.win_divisions(ds, ME)).fc,
                 core.division_trend(ds, ME), core.match_formations(ds, ME))
    assert shown == plain and plain[1] > 0
    # 넥슨이 매긴 값이어도 결과 집계는 따로 — 같은 묶음에서 summarize 는 빠진다
    ms = _ms(["승", "승", "패"])
    with excluding("m0", "m1"):
        assert core.summarize(ms).total == 1


def test_records_drop_win_records_but_keep_division_milestones():
    champ = CHAMPION_DIVISION_ID
    ds = [_detail(0, "승", div=champ), _detail(1, "승"), _detail(2, "승")]
    kw = dict(current_start=datetime(2026, 10, 1, 11), current_known=True, now=datetime(2026, 10, 5))
    full = records.achievements(ds, ME, **kw)
    keys = [a.key for a in full]
    assert "streak3" in keys and "champ" in keys, keys
    with excluding("m0"):
        got = core.record_achievements(ds, ME, **kw)
    gk = [(a.key, a.match_id) for a in got]
    assert "streak3" not in [k for k, _ in gk], "표시한 판이 빠졌는데 3연승이 남았다"
    assert ("champ", "m0") in gk, "챔스 도달은 넥슨이 매긴 등급 기록이라 그대로여야 한다"
    # 호출자가 빈 exclude 를 넘기면 전역이 덮지 않는다
    with excluding("m0"):
        assert "streak3" in [a.key for a in core.record_achievements(ds, ME, exclude=frozenset(), **kw)]
    try:
        with excluding("m0"):
            core.record_achievements([{**ds[0], "matchId": None}] + ds[1:], ME, **kw)
    except ValueError:
        pass
    else:
        raise AssertionError("번호 없는 경기가 조용히 지나갔다")


def test_season_pace_and_review_drop_only_wdl_and_counts():
    sp, ms, wins, ctx, elo = _pace_data()
    now = NOW
    base = ss.season_pace(ms, wins, ctx, elo, sp, 0, now)
    with excluding("m2"):                                           # 이 시즌의 승 · 등급 800 판
        p = core.season_pace(ms, wins, ctx, elo, sp, 0, now)
    assert (base.cur.games, base.cur.win, base.cur.lose) == (4, 2, 1)
    assert (p.cur.games, p.cur.win, p.cur.draw, p.cur.lose) == (3, 1, 1, 1), p.cur
    assert p.cur.best_div == base.cur.best_div == 800, "표시한 판의 등급이 최고 등급에서 빠졌다(S)"
    assert p.cur.fc == base.cur.fc and p.cur.elo == base.cur.elo, "FC·점수는 넥슨 기록이다"
    assert p.prev.win == base.prev.win, "다른 시즌은 그대로"
    span = sp[0]
    r0 = ss.season_review(ms, [], ME, ctx, elo, span)
    with excluding("m2"):
        r = core.season_review(ms, [], ME, ctx, elo, span)
    assert (r0.games, r0.win) == (4, 2) and (r.games, r.win, r.draw, r.lose) == (3, 1, 1, 1), (r.games, r.win)
    assert r.best_div == r0.best_div == 800 and r.peak_elo == r0.peak_elo
    with excluding("m2"):
        r_own = core.season_review(ms, [], ME, ctx, elo, span, exclude=frozenset())
    assert r_own.games == 4, "넘긴 exclude 를 전역이 덮었다"


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


def test_timeline_windows_drop_the_shown_match():
    # build_timeline 은 사건 앞뒤 창의 승·무·패를 낸다 — S 로 두면 표시한 판이 창에 남는다(리뷰에서 찾음)
    from test_timeline import _days, _match
    ds = list(reversed([_match(_days(i), "승" if i % 2 else "패", starters=((1, 5),)) for i in range(6)]))
    raw = core.raw.build_timeline(ds, "me", None, today=date(2026, 10, 7))
    with excluding(ds[0]["matchId"]):
        got = core.build_timeline(ds, "me", None, today=date(2026, 10, 7))
    assert raw.games_basis == 6, raw.games_basis          # 막지 않았으면 6판 전부 센다
    assert got.games_basis == 5, got.games_basis


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
        finally:
            core.set_excluded(())
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
