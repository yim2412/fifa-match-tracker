"""sessions.py · habits 요일 표 — 97단계 조각 1(세션 · 경기를 돌린 시간 · 연패 끊는 패턴 · 요일 표). 손으로 센 지어낸 경기(네트워크 없음).

    python tests/test_sessions.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import habits  # noqa: E402
import sessions as ss  # noqa: E402
import stats  # noqa: E402
from models import MatchSummary  # noqa: E402

D = datetime(2026, 9, 7)   # 월요일


def _m(i, result, when, dp=True, opp_div=None):
    return MatchSummary(match_id=f"m{i}", match_date=when, match_type=52, my_nickname="me", opponent="x", result=result,
                        my_goals=0, opp_goals=0, possession=50, shoot_total=0, shoot_effective=0, pass_try=0,
                        pass_success=0, rating=0.0, draw_possible=dp, opp_division=opp_div)


def _at(*times, day=D):
    """(시, 분) 목록 → 날짜가 같은 시각들."""
    return [day.replace(hour=h, minute=mi) for h, mi in times]


def _games(times, results=None):
    results = results or ["승"] * len(times)
    return [_m(i, r, t) for i, (t, r) in enumerate(zip(times, results))]


# ── Y3 세션 끊기 ────────────────────────────────────────────────────────────
def test_split_cuts_on_gap_over_30_min_and_midnight():
    t = _at((10, 0), (10, 30), (11, 1)) + [datetime(2026, 9, 7, 23, 50), datetime(2026, 9, 8, 0, 10)]
    got = ss.split_sessions(_games(t))
    # 10:00→10:30 간격 30분은 같은 세션('넘게'만 끊는다) · 10:30→11:01 은 31분이라 끊김 · 23:50→00:10 은 20분인데 0시를 넘어 끊김
    assert [s.games for s in got] == [2, 1, 1, 1], [s.games for s in got]
    assert [s.start.hour for s in got] == [10, 11, 23, 0]
    # 입력 순서와 무관 · 날짜 없는 경기는 뺀다
    shuffled = list(reversed(_games(t))) + [_m(99, "승", None)]
    assert [s.games for s in ss.split_sessions(shuffled)] == [2, 1, 1, 1]


def test_split_midnight_alone_cuts_even_with_a_one_minute_gap():
    got = ss.split_sessions(_games([datetime(2026, 9, 7, 23, 59), datetime(2026, 9, 8, 0, 0)]))
    assert [s.games for s in got] == [1, 1], "0시에서 안 끊었다"


# ── 이어지는 중(ongoing) ────────────────────────────────────────────────────
def test_ongoing_boundary_with_fallback_game_length():
    ms = _games(_at((9, 0), (10, 0)))                     # 간격 1개 < Y2_MIN_GAPS → 한 판 길이 기본값 12분 → 문턱 30+12=42분
    last = D.replace(hour=10)
    lim = config.SESSION_GAP_MIN + config.Y2_GAME_MIN_FALLBACK
    assert ss.split_sessions(ms, now=last + timedelta(minutes=lim))[-1].ongoing, "경계 안(=42분)인데 아니라고 했다"
    assert not ss.split_sessions(ms, now=last + timedelta(minutes=lim, seconds=1))[-1].ongoing, "경계 밖인데 이어진다고 했다"
    assert not ss.split_sessions(ms)[-1].ongoing, "now 를 안 줬는데 참"
    # 날이 바뀌면(이어 해도 새 세션) 아니다
    late = _games([datetime(2026, 9, 7, 23, 50)])
    assert not ss.split_sessions(late, now=datetime(2026, 9, 8, 0, 5))[-1].ongoing
    # 마지막 세션만 참
    two = _games(_at((9, 0), (13, 0)))
    got = ss.split_sessions(two, now=D.replace(hour=13, minute=5))
    assert [s.ongoing for s in got] == [False, True]


def test_ongoing_uses_measured_game_length_when_enough_gaps():
    t = [D.replace(hour=8) + timedelta(minutes=10 * k) for k in range(25)]   # 간격 24개 · 중앙값 10분 → 문턱 40분
    last = t[-1]
    assert ss.split_sessions(_games(t), now=last + timedelta(minutes=40))[-1].ongoing
    assert not ss.split_sessions(_games(t), now=last + timedelta(minutes=41))[-1].ongoing


def test_ongoing_session_left_out_of_size_bins_and_luck():
    # 막지 않았으면: 마지막 세션(3판)이 첫 구간에 세어지고 운 판정에도 들어갔을 것
    sessions = ss.split_sessions(_games(_at((9, 0), (9, 10), (9, 20), (9, 30), (9, 40)) + _at((14, 0), (14, 10), (14, 20))),
                                 now=D.replace(hour=14, minute=25))
    assert [s.ongoing for s in sessions] == [False, True]
    bins = ss.size_bins(sessions)
    assert [b.sessions for b in bins] == [0, 1, 0, 0], [(b.label, b.sessions) for b in bins]   # 5판 세션만(4~10판 구간)
    done = ss.split_sessions(sessions[0].matches + sessions[1].matches)
    assert [b.sessions for b in ss.size_bins(done)] == [1, 1, 0, 0]
    lk = ss.session_luck(sessions, False)
    assert lk.judged == 1, lk       # 5판 세션 하나(D1_MIN_GAMES=5) — 이어지는 중 세션은 3판이라 어차피 미달이니 아래서 다시
    rows = ss.session_rows(sessions, False)
    assert [r.ongoing for r in rows] == [False, True]


def test_ongoing_session_not_counted_in_luck_when_big_enough():
    t10 = [D.replace(hour=8) + timedelta(minutes=10 * k) for k in range(10)]
    t10b = [D.replace(hour=15) + timedelta(minutes=10 * k) for k in range(10)]
    ms = _games(t10, ["승"] * 10) + [_m(100 + i, "패", t) for i, t in enumerate(t10b)]
    now = t10b[-1] + timedelta(minutes=5)
    sessions = ss.split_sessions(ms, now=now)
    assert [s.ongoing for s in sessions] == [False, True]
    lk = ss.session_luck(sessions, False)
    assert lk.judged == 1 and lk.p == 1.0, lk      # 이어지는 중인 세션(전패)을 넣었으면 judged 2 · p 0.5


# ── 세션 판 수 구간 · 운 범위 · 줄 ──────────────────────────────────────────
def _sess(n, wins, base_minute, day=D):
    """n판짜리 세션 하나 — 앞 wins 판 승, 나머지 패. 10분 간격."""
    t0 = day.replace(hour=0) + timedelta(minutes=base_minute)
    return [_m(f"{base_minute}-{k}", "승" if k < wins else "패", t0 + timedelta(minutes=10 * k)) for k in range(n)]


def test_size_bins_edges():
    ms = []
    for i, n in enumerate((3, 4, 10, 11, 30, 31)):     # 모서리: 4 부터 둘째 구간 · 11 부터 셋째 · 31 부터 넷째
        ms += _sess(n, n, 0, day=D + timedelta(days=i))
    bins = ss.size_bins(ss.split_sessions(ms))
    assert [b.sessions for b in bins] == [1, 2, 2, 1], [(b.label, b.sessions) for b in bins]
    assert [b.win for b in bins] == [3, 14, 41, 31]
    assert bins[0].label == "1~3판" and bins[3].label == "31판 넘게"


def test_session_luck_counts_outside_and_expected_by_hand():
    ms = (_sess(10, 10, 0) + _sess(10, 0, 0, day=D + timedelta(days=1)) + _sess(10, 5, 0, day=D + timedelta(days=2))
          + _sess(10, 5, 0, day=D + timedelta(days=3)))
    lk = ss.session_luck(ss.split_sessions(ms), False)
    # p = 20/40 · 띠(10판) = 0.5 ± 2·√(0.25/10) = [0.184, 0.816] · 10-0 과 0-10 만 밖 · 밖일 확률 = 2·(C(10,0)+C(10,1))/1024
    assert (lk.judged, lk.outside) == (4, 2) and lk.p == 0.5, lk
    assert abs(lk.expected - 4 * 22 / 1024) < 1e-9, lk.expected
    # D1_MIN_GAMES 미만 세션은 판정하지 않는다
    small = ss.session_luck(ss.split_sessions(_sess(config.D1_MIN_GAMES - 1, 0, 0)), False)
    assert small.judged == 0


def test_session_rows_delta_only_when_one_session_that_day():
    ms = _sess(3, 2, 8 * 60) + _sess(2, 1, 14 * 60) + _sess(2, 2, 9 * 60, day=D + timedelta(days=1))
    sessions = ss.split_sessions(ms)
    pts = [(date(2026, 9, 6), 1000.0), (date(2026, 9, 7), 1030.0), (date(2026, 9, 8), 1010.0)]
    rows = ss.session_rows(sessions, False, pts)
    assert [r.delta for r in rows] == [None, None, -20.0], [r.delta for r in rows]   # 월요일은 세션 둘이라 모름
    assert [r.delta for r in ss.session_rows(sessions, False)] == [None, None, None]
    r0 = rows[0]
    assert (r0.games, r0.win, r0.draw, r0.lose) == (3, 2, 0, 1) and abs(r0.rate - 200 / 3) < 1e-9


# ── Y2 경기를 돌린 시간 ─────────────────────────────────────────────────────
def test_play_time_fallback_length_by_hand():
    # 세션 A 10:00 10:10 10:20 · B 14:00 한 판 — 간격 2개 < Y2_MIN_GAPS → 한 판 12분 · A = 20+12 · B = 0+12
    sessions = ss.split_sessions(_games(_at((10, 0), (10, 10), (10, 20), (14, 0))))
    pt = ss.play_time(sessions)
    assert pt.estimated and pt.game_min == config.Y2_GAME_MIN_FALLBACK and pt.gaps == 2
    assert pt.days == {date(2026, 9, 7): 44.0}, pt.days
    assert pt.average_day_min == 44.0 and pt.longest_day == (date(2026, 9, 7), 44.0)


def test_play_time_uses_median_gap_when_enough():
    t = [D.replace(hour=8) + timedelta(minutes=10 * k) for k in range(21)]   # 간격 20개 = Y2_MIN_GAPS · 모두 10분
    pt = ss.play_time(ss.split_sessions(_games(t)))
    assert not pt.estimated and pt.game_min == 10.0 and pt.gaps == 20
    assert pt.days[date(2026, 9, 7)] == 200 + 10.0
    # 경계: 간격이 하나 모자라면(19개) 기본값
    pt19 = ss.play_time(ss.split_sessions(_games(t[:20])))
    assert pt19.estimated and pt19.game_min == config.Y2_GAME_MIN_FALLBACK


def test_play_time_day_never_exceeds_24h():
    t = [D + timedelta(minutes=12 * k) for k in range(120)] + [D.replace(hour=23, minute=59)]   # 00:00~23:48 12분마다 + 23:59
    sessions = ss.split_sessions(_games(t))
    assert len(sessions) == 1
    s = sessions[0]
    raw = (s.end - s.start).total_seconds() / 60 + 12.0
    assert raw == 1451.0, raw                      # 막지 않았으면 하루가 24시간(1440분)을 넘는다
    pt = ss.play_time(sessions)
    assert pt.game_min == 12.0 and pt.days[date(2026, 9, 7)] == 1440.0
    assert all(m <= 1440.0 for m in pt.days.values())


def test_period_totals_day_week_month():
    ms = []
    for k, day in enumerate((date(2026, 9, 7), date(2026, 9, 9), date(2026, 9, 14), date(2026, 10, 1))):
        ms += _sess(1, 1, 0, day=datetime(day.year, day.month, day.day))   # 하루 한 판 → 12분
    pt = ss.play_time(ss.split_sessions(ms))
    assert [m for _, m in ss.period_totals(pt, "일")] == [12.0] * 4
    assert ss.period_totals(pt, "주") == [(date(2026, 9, 7), 24.0), (date(2026, 9, 14), 12.0), (date(2026, 9, 28), 12.0)]
    assert ss.period_totals(pt, "월") == [(date(2026, 9, 1), 36.0), (date(2026, 10, 1), 12.0)]


# ── N1 연패 끊는 패턴 ───────────────────────────────────────────────────────
FORM_A = [{"spPosition": p} for p in (0, 3, 5, 6, 7, 9, 12, 14, 16, 20, 25)]
FORM_B = [{"spPosition": p} for p in (0, 3, 5, 6, 7, 9, 12, 14, 16, 17, 18)]


def test_match_context_reads_division_and_formation():
    me = {"ouid": "me", "division": 900, "player": FORM_A}
    empty = {"ouid": "me", "division": "x", "player": []}
    got = ss.match_context([{"matchId": "a", "matchInfo": [me, {"ouid": "o"}]}, {"matchId": "b", "matchInfo": [empty]},
                            {"matchId": "c", "matchInfo": [{"ouid": "o"}]}], "me")
    assert got == {"a": (900, stats.formation_of(FORM_A)), "b": (None, None)}, got
    assert stats.formation_of(FORM_A) != stats.formation_of(FORM_B)


def _seq(results, gaps=None):
    t = D.replace(hour=1)
    out = []
    for i, r in enumerate(results):
        if i:
            t += timedelta(minutes=(gaps[i] if gaps else 10))
        out.append(_m(i, r, t))
    return out


def test_streak_break_targets_by_hand():
    #            0    1    2    3    4    5    6    7
    ms = _seq(["패", "패", "승", "패", "패", "패", "무", "승"])
    sb = ss.streak_break(ms, {})
    # 대상 = 두 판 연패 직후: 2(승) · 5(패, 앞에 패 패) · 6(무, 앞에 패 패 패) — 7 은 무 뒤라 아니다
    assert sb.after == (1, 1, 1), sb.after
    assert sb.base == (2, 1, 5) and sb.games == 8
    assert sb.alpha == config.N20_ALPHA / config.N1_TESTS and len(sb.tables) == config.N1_TESTS == 3
    # 오류·무승부는 연패를 끊는다
    assert ss.streak_break(_seq(["패", "패", "오류", "승"]), {}).after == (0, 0, 0)
    assert ss.streak_break(_seq(["패", "무", "패", "승"]), {}).after == (0, 0, 0)
    # 대상이 아니면 아무것도 안 센다
    assert ss.streak_break([], {}).tables[0].games == 0


def test_streak_break_condition_tables_by_hand():
    ms = _seq(["패", "패", "승", "패", "패", "패", "무", "승"], gaps=[0, 10, 10, 10, 10, 10, 90, 10])
    ms[2].opp_division, ms[5].opp_division, ms[6].opp_division = 800, 900, 1000
    fa, fb = stats.formation_of(FORM_A), stats.formation_of(FORM_B)
    forms = ["A", "A", "A", "A", "A", "B", "B", "B"]
    ctx = {m.match_id: (900, {"A": fa, "B": fb}[f]) for m, f in zip(ms, forms)}
    ctx["m6"] = (900, None)                         # 6 번 경기는 포메이션을 모른다 - 그 표에서만 빠진다
    sb = ss.streak_break(ms, ctx)
    gap, form, div = sb.tables
    # 간격: 대상 2(10분)·5(10분) → '13분 이하' 칸 · 대상 6(90분) → '60분 넘게'
    assert [b.games for b in gap.bins] == [2, 0, 0, 1], [b.games for b in gap.bins]
    # 포메이션: 2 는 앞(1)과 같음 · 5 는 앞(4)이 A 인데 B → 바꿈 · 6 은 모름 → 둘 합쳐 2판
    assert [b.games for b in form.bins] == [1, 1] and (form.bins[1].win, form.bins[1].lose) == (0, 1)
    # 상대 등급(작을수록 높다): 2 는 800 < 내 900 → 더 높음 · 5 는 같음 · 6 은 1000 → 더 낮음
    assert [b.games for b in div.bins] == [1, 1, 1] and div.bins[0].win == 1 and div.bins[2].draw == 1
    # 상대 등급을 모르는 경기는 그 표에서만 빠진다
    ms[2].opp_division = None
    assert [b.games for b in ss.streak_break(ms, ctx).tables[2].bins] == [0, 1, 1]


def _blocks(k=30):
    """연패 두 판 뒤 다음 판의 간격에 따라 결과가 갈리는 자료 — 10분이면 승, 90분이면 패(뒤따르는 승은 10분 간격)."""
    out, t, n = [], D.replace(hour=0), 0

    def add(res, gap):
        nonlocal t, n
        t += timedelta(minutes=gap)
        out.append(_m(n, res, t))
        n += 1
    for i in range(k):
        for res, gap in (("패", 1000), ("패", 10), ("승", 10)):                # 대상 하나: 10분 뒤 승
            add(res, gap if i or res != "패" else 0)
        for res, gap in (("패", 1000), ("패", 10), ("패", 90), ("승", 10)):    # 대상 둘: 90분 뒤 패 · 이어 10분 뒤 승
            add(res, gap)
    return out


def test_streak_break_threshold_is_alpha_over_tests():
    ms = _blocks()
    sb = ss.streak_break(ms, {})
    gap = sb.tables[0]
    assert gap.tested and gap.significant and gap.p < 1e-6, gap          # 효과가 큰 자료 — 문턱이 보호 장치로 작동하는지 재는 바탕
    keep = config.N1_TESTS
    config.N1_TESTS = 10 ** 25
    try:
        sb2 = ss.streak_break(ms, {})
    finally:
        config.N1_TESTS = keep
    g2 = sb2.tables[0]
    assert g2.p == gap.p and not g2.significant and all(b.flag == 0 for b in g2.bins), "문턱이 N1_TESTS 로 안 나뉜다"
    assert any(b.flag for b in gap.bins), "유의한 표에 강조가 없다"


def test_chi_table_alpha_argument():
    ms = _blocks()
    sb = ss.streak_break(ms, {})
    rows = [(0 if i % 2 else 3, m) for i, m in enumerate(ms)]
    t_default = habits._chi_table("x", habits.gap_labels(), rows, False)
    t_tight = habits._chi_table("x", habits.gap_labels(), rows, False, alpha=t_default.p / 2 if t_default.p else 1e-300)
    assert t_default.significant == (t_default.p < config.N20_ALPHA)
    assert not t_tight.significant and all(b.flag == 0 for b in t_tight.bins) and sb.tables[0].tested


# ── N3 요일 표 (요일×시간대 지도는 [승부처 분석 › 선제골·역전] 에 이미 있어 뺐다) ─────────────
def test_weekday_table_by_hand():
    mon = [_m(i, "승" if i < 15 else "패", D.replace(hour=10, minute=i)) for i in range(25)]            # 월 25판 15승
    tue = [_m(100 + i, "승", D.replace(day=8, hour=20, minute=i)) for i in range(5)]                    # 화 5판
    sun = [_m(200 + i, "패", D.replace(day=13, hour=0, minute=30 + i % 20)) for i in range(20)]         # 일 0시 20판(시각이 겹쳐도 센다)
    ms = mon + tue + sun
    day = next(t for t in habits.habit_tables(ms) if t.name == "요일")
    assert [b.label for b in day.bins] == ["월", "화", "수", "목", "금", "토", "일"]
    assert [b.games for b in day.bins] == [25, 5, 0, 0, 0, 0, 20]
    assert (day.bins[0].win, day.bins[0].lose, day.bins[6].lose) == (15, 10, 20)
    assert [t.name for t in habits.habit_tables(ms)] == ["직전 경기와의 간격", "세션 안 몇 번째 판", "시간대", "요일"]


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
