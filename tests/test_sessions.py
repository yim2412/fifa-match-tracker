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


# ── 97 조각 2 — X1 지난 시즌 같은 시점 · F5 시즌 돌아보기 · Y1 연패 한 줄 ───────────────────────────
from seasons import Season  # noqa: E402
from stats import WinDivision  # noqa: E402

S_A = Season(90, "시즌 A", date(2026, 7, 30), date(2026, 9, 10))   # 42일
S_B = Season(91, "시즌 B", date(2026, 9, 10), date(2026, 10, 29))  # 49일
NOW = datetime(2026, 11, 2, 8, 0)                                  # 지금 시즌(10-29 08:00~)은 정확히 4일째


def _t(mo, d, h=9, mi=0):
    return datetime(2026, mo, d, h, mi)


def test_season_spans_newest_first_and_adds_the_ongoing_one():
    sp = ss.season_spans([S_A, S_B], NOW)
    assert [(s.label, s.start, s.end) for s in sp] == [
        ("지금 시즌", _t(10, 29, 8), None), (S_B.label, _t(9, 10, 8), _t(10, 29, 8)), (S_A.label, _t(7, 30, 8), _t(9, 10, 8))]
    assert [s.ongoing for s in sp] == [True, False, False] and sp[1].key == "2026-09-10"
    assert ss.season_spans([], NOW) == []                                   # 시즌표가 없으면(웹 데이터 꺼짐) 구간도 없다
    # 시즌표에 아직 안 끝난 시즌이 있으면 그것이 진행 중 — "지금 시즌" 을 더 만들지 않는다
    live = Season(92, "시즌 C", date(2026, 10, 29), date(2026, 12, 20))
    sp2 = ss.season_spans([S_A, S_B, live], NOW)
    assert [s.ongoing for s in sp2] == [True, False, False] and sp2[0].label == live.label
    # 아직 시작 안 한 시즌은 뺀다
    assert len(ss.season_spans([S_A, S_B, Season(93, "미래", date(2027, 1, 1), date(2027, 3, 1))], NOW)) == 3


def test_boundaries_are_half_open_start_in_end_out():
    # 시즌이 정확히 now 에 끝났으면 끝난 시즌 — 진행 중이 아니다(지금 시즌을 따로 더한다)
    sp = ss.season_spans([S_A, S_B], _t(10, 29, 8, 0))
    assert [(s.label, s.ongoing) for s in sp] == [("지금 시즌", True), (S_B.label, False), (S_A.label, False)]
    assert [s.ongoing for s in ss.season_spans([S_A, S_B], _t(10, 29, 7, 59))] == [True, False]   # 1분 전이면 B 가 진행 중
    # 점수 기록·승리 보상은 [시작, 끝) — 시작 정각은 이 시즌, 끝 정각은 다음 시즌
    span = ss.season_spans([S_A, S_B], NOW)[1]                       # B
    elo = [{"taken_at": "2026-09-10T08:00:00", "elo": 7000.0}, {"taken_at": "2026-10-29T08:00:00", "elo": 9000.0},
           {"taken_at": "2026-09-30T10:00:00", "elo": 4000.0}]
    r = ss.season_review([_m(1, "승", _t(9, 20))], [], "me", {}, elo, span)
    assert r.peak_elo == 7000.0, "시작 정각 기록이 빠졌거나 끝 정각 기록이 들어갔다"
    wins = [WinDivision(_t(9, 10, 8, 0), 900, "승", False), WinDivision(_t(10, 29, 8, 0), 800, "승", False)]
    sp3 = ss.season_spans([S_A, S_B], NOW)
    p = ss.season_pace([_m(1, "승", _t(9, 20)), _m(2, "승", _t(10, 29, 9))], wins, {}, elo, sp3, 1, NOW)   # B 를 A 와 — B 쪽 합산
    assert p.cur.fc == 15 and p.cur.elo == 4000.0, (p.cur.fc, p.cur.elo)       # 시작 정각 승리(900 → 15)만 · 끝 정각 승리(800 → 20)·기록(9000)은 지금 시즌 몫
    only = ss.season_pace([_m(1, "승", _t(9, 20))], wins, {}, elo[:2], sp3, 1, NOW)                     # 점수 기록이 시작 정각·끝 정각 둘뿐 — 이 시즌 몫은 시작 정각 것
    assert only.cur.elo == 7000.0, only.cur.elo
    # 저장이 시작 + N12_START_DAYS 정각에 시작하면 '시작부터' — 1분 늦으면 부분 저장
    d3 = S_B.start_at + timedelta(days=config.N12_START_DAYS)
    assert ss.season_review([_m(1, "승", d3)], [], "me", {}, [], span).from_day is None
    assert ss.season_review([_m(1, "승", d3 + timedelta(minutes=1))], [], "me", {}, [], span).from_day == (d3 + timedelta(minutes=1)).date()
    # 지금 시즌 경과 일수가 지난 시즌 길이와 정확히 같으면 '일찍 끝났다' 표시가 없다(같은 길이)
    eq = [Season(1, "P", date(2026, 1, 1), date(2026, 1, 11)), Season(2, "Q", date(2026, 1, 11), date(2026, 1, 21))]
    se = ss.season_spans(eq, datetime(2026, 3, 1))
    pe = ss.season_pace([_m(1, "승", datetime(2026, 1, 12)), _m(2, "승", datetime(2026, 1, 2))], [], {}, [], se, 1, datetime(2026, 3, 1))
    assert pe.days == 10.0 and pe.prev_state == "ok" and pe.prev_days is None, (pe.days, pe.prev_state, pe.prev_days)
    longer = [Season(1, "P", date(2026, 1, 1), date(2026, 1, 9)), Season(2, "Q", date(2026, 1, 9), date(2026, 1, 21))]
    pl = ss.season_pace([_m(1, "승", datetime(2026, 1, 10)), _m(2, "승", datetime(2026, 1, 2))], [], {}, [],
                        ss.season_spans(longer, datetime(2026, 3, 1)), 1, datetime(2026, 3, 1))
    assert pl.prev_days == 8, pl.prev_days                                         # 12일째인데 지난 시즌은 8일에 끝났다


def test_stored_spans_keep_only_seasons_with_saved_matches():
    sp = ss.season_spans([S_A, S_B], NOW)
    ms = [_m(1, "승", _t(9, 10, 7, 59)), _m(2, "승", _t(10, 29, 8, 0)), _m(3, "승", _t(9, 1)), _m(4, "승", None)]
    assert [s.label for s in ss.stored_spans(sp, ms)] == ["지금 시즌", S_A.label]       # B 에는 경기가 없다 · 날짜 없는 경기는 뺀다
    # 경계: 9-10 07:59 → A · 9-10 08:00 → B · 10-29 08:00 → 지금 시즌
    assert [s.label for s in ss.stored_spans(sp, [_m(1, "승", _t(9, 10, 7, 59))])] == [S_A.label]
    assert [s.label for s in ss.stored_spans(sp, [_m(1, "승", _t(9, 10, 8, 0))])] == [S_B.label]
    assert [s.label for s in ss.stored_spans(sp, [_m(1, "승", _t(10, 29, 8, 0))])] == ["지금 시즌"]
    assert ss.stored_spans(sp, []) == [] and ss.stored_spans([], ms) == []


def test_streak_note_calls_ctx_only_after_the_conditions_pass():
    now = _t(11, 2, 12)
    called = []

    def lazy():
        called.append(1)
        return {}
    assert ss.streak_note(_tail(["승", "패", "패"], now - timedelta(hours=1)), lazy, now) is None
    assert ss.streak_note(_tail(["패"] * 3, now - timedelta(hours=7)), lazy, now) is None
    assert called == [], "조건을 통과하기 전에 전 경기 맥락을 지었다"
    assert ss.streak_note(_tail(["패"] * 3, now - timedelta(hours=1)), lazy, now) is not None and called == [1]


def _pace_data():
    sp = ss.season_spans([S_A, S_B], NOW)
    cur = [_m(1, "승", _t(10, 29, 9)), _m(2, "승", _t(10, 29, 10)), _m(3, "패", _t(10, 30, 9)), _m(4, "무", _t(11, 1, 9))]
    prev = [_m(11, "승", _t(9, 10, 9)), _m(12, "패", _t(9, 11, 9)), _m(13, "패", _t(9, 12, 9)),
            _m(14, "승", _t(9, 14, 7, 59)),            # 컷오프(09-14 08:00) 1분 전 — 넣는다
            _m(15, "패", _t(9, 14, 8, 0)),             # 컷오프 정각 — [시작, 컷오프) 라 뺀다
            _m(16, "승", _t(9, 20, 9))]                # 한참 뒤 — 뺀다
    ctx = {"m1": (900, None), "m2": (800, None), "m3": (1100, None), "m4": (1100, None),
           "m11": (1000, None), "m12": (900, None), "m13": (1000, None), "m14": (1000, None), "m15": (200, None), "m16": (200, None)}
    wins = [WinDivision(_t(10, 29, 9), 900, "승", False), WinDivision(_t(10, 29, 10), 800, "승", False),
            WinDivision(_t(9, 10, 9), 900, "승", False), WinDivision(_t(9, 14, 7, 59), 1000, "승", False),   # 1000 은 보상 없음
            WinDivision(_t(9, 20, 9), 800, "승", False)]                                                      # 컷오프 뒤 — 뺀다
    elo = [{"taken_at": "2026-10-30T12:00:00", "elo": 5000.0}, {"taken_at": "2026-11-01T12:00:00", "elo": 4800.0},
           {"taken_at": "2026-09-12T10:00:00", "elo": 4000.0}, {"taken_at": "2026-09-14T07:00:00", "elo": 4100.0},
           {"taken_at": "2026-09-14T09:00:00", "elo": 9999.0}, {"taken_at": "garbage", "elo": 1.0}, {"taken_at": "2026-10-31T00:00:00", "elo": None}]
    return sp, cur + prev, wins, ctx, elo


def test_pace_compares_the_same_elapsed_days_by_hand():
    sp, ms, wins, ctx, elo = _pace_data()
    p = ss.season_pace(ms, wins, ctx, elo, sp, 0, NOW)
    assert p.days == 4.0 and p.prev_state == "ok" and p.prev_days is None and p.prev_span.label == S_B.label
    c, pv = p.cur, p.prev
    assert (c.games, c.win, c.draw, c.lose, c.rate) == (4, 2, 1, 1, 50.0)
    assert (pv.games, pv.win, pv.draw, pv.lose, pv.rate) == (4, 2, 0, 2, 50.0), pv      # 승 패 패 승 — 컷오프 정각·뒤 경기는 빠졌다
    assert (c.best_div, pv.best_div) == (800, 900)                                       # id 가 작을수록 높은 등급 · 컷오프 뒤 200 은 안 센다
    assert (c.elo, pv.elo) == (4800.0, 4100.0)                                           # 구간 안 마지막 점수 기록 · 읽을 수 없는 줄은 뺀다 · 컷오프 뒤 9999 는 뺀다
    assert (c.fc, pv.fc) == (35, 15)                                                     # 900→15 + 800→20 · 1000 은 보상이 없고 컷오프 뒤 슈챔 승리는 안 센다
    # 경과 일수 축 누적 승률: 이 시즌 100 → 66.7 → 66.7 → 50 · 지난 시즌 100 → 50 → 33.3 → 50
    assert [(k, round(a, 1), round(b, 1)) for k, a, b in p.curve] == [(1, 100.0, 100.0), (2, 66.7, 50.0), (3, 66.7, 33.3), (4, 50.0, 50.0)], p.curve


def test_pace_cutoff_is_start_plus_days_not_a_rounded_day():
    # 4일 + 1분째로 보면 컷오프 정각 경기(09-14 08:00)가 들어간다 · 1분 모자라면(3일 23시간 59분) 07:59 경기도 빠진다
    sp, ms, wins, ctx, elo = _pace_data()
    p = ss.season_pace(ms, wins, ctx, elo, sp, 0, NOW + timedelta(minutes=1))
    assert p.prev.games == 5 and p.cur.games == 4
    q = ss.season_pace(ms, wins, ctx, elo, sp, 0, NOW - timedelta(minutes=1))
    assert q.prev.games == 3, q.prev


def test_pace_ended_season_marks_where_the_previous_one_ended():
    sp = ss.season_spans([S_A, S_B], NOW)
    a = [_m(i, "승", _t(7, 31)) for i in range(2)] + [_m(10, "패", _t(9, 9, 20))]                 # A: 07-31 승승 · 09-09 패(마지막 날)
    b = [_m(20, "승", _t(9, 10, 9)), _m(21, "패", _t(10, 28, 9))]
    p = ss.season_pace(a + b, [], {}, [], sp, 1, NOW)                                              # B(49일)를 A(42일)와
    assert p.days == 49.0 and p.prev_days == 42, (p.days, p.prev_days)
    assert p.prev.games == 3 and len(p.curve) == 49
    assert p.curve[41][2] is not None and all(x[2] is None for x in p.curve[42:]), "지난 시즌이 끝난 뒤에도 선이 이어진다"
    assert p.curve[0][1] == 100.0 and p.curve[-1][1] == 50.0


def test_pace_previous_season_only_counts_when_stored_from_the_start():
    sp = ss.season_spans([S_A, S_B], NOW)
    cur = [_m(1, "승", _t(10, 29, 9))]
    edge = [_m(10, "승", _t(9, 13, 8, 0))]          # 시작(09-10 08:00) + 3일 정각 — 이만큼까지는 "시작부터"
    p = ss.season_pace(cur + edge, [], {}, [], sp, 0, NOW)
    assert p.prev_state == "ok" and p.prev.games == 1
    late = [_m(10, "승", _t(9, 13, 8, 1))]          # 1분 늦다 — 저장이 시즌 일부만
    q = ss.season_pace(cur + late, [], {}, [], sp, 0, NOW)
    assert q.prev_state == "late" and q.prev is None and q.prev_from == date(2026, 9, 13) and q.curve == []
    assert ss.season_pace(cur, [], {}, [], sp, 0, NOW).prev_state == "empty"                      # 지난 시즌 경기가 없다
    assert ss.season_pace(cur, [], {}, [], sp, 2, NOW).prev_state == "none"                       # 가장 오래된 시즌 — 지난 시즌이 없다


def test_review_season_boundary_is_8_oclock():
    sp = ss.season_spans([S_A, S_B], NOW)
    ms = [_m(1, "승", _t(10, 29, 7, 59)), _m(2, "패", _t(10, 29, 8, 0))]
    assert ss.season_review(ms, [], "me", {}, [], sp[1]).games == 1          # 07:59 는 B
    assert ss.season_review(ms, [], "me", {}, [], sp[0]).games == 1          # 08:00 은 지금 시즌
    assert ss.season_review(ms, [], "me", {}, [], sp[1]).win == 1 and ss.season_review(ms, [], "me", {}, [], sp[0]).lose == 1


def _detail(mid, when, goals_by_sp):
    return {"matchId": mid, "matchDate": when, "matchInfo": [
        {"ouid": "me", "player": [{"spId": sp, "spPosition": 9, "status": {"goal": g, "spRating": 7.0}} for sp, g in goals_by_sp.items()]},
        {"ouid": "o", "player": []}]}


def test_review_card_values_by_hand():
    sp = ss.season_spans([S_A, S_B], NOW)[1]       # B: 09-10 08:00 ~ 10-29 08:00
    ms = [_m(1, "승", _t(9, 20, 10)), _m(2, "승", _t(9, 20, 11)), _m(3, "승", _t(9, 21, 9)), _m(4, "패", _t(9, 22, 9)),
          _m(5, "무", _t(9, 23, 9)), _m(6, "승", _t(9, 24, 9)),
          _m(7, "승", _t(10, 29, 9)), _m(8, "패", _t(9, 1, 9))]                    # 7 은 지금 시즌 · 8 은 A — 둘 다 빠진다
    fa, fb = stats.formation_of(FORM_A), stats.formation_of(FORM_B)
    ctx = {"m1": (1000, fa), "m2": (900, fa), "m3": (900, fb), "m4": (1100, fa), "m5": (900, None), "m6": (1000, fa),
           "m7": (800, fb), "m8": (800, fb)}
    details = [_detail("m1", "2026-09-20T01:00:00", {101: 2, 102: 1}), _detail("m2", "2026-09-20T02:00:00", {101: 1, 102: 1}),
               _detail("m3", "2026-09-21T00:00:00", {102: 1}), _detail("m7", "2026-10-29T00:00:00", {103: 9})]
    elo = [{"taken_at": "2026-09-15T10:00:00", "elo": 4100.0}, {"taken_at": "2026-09-30T10:00:00", "elo": 4500.0},
           {"taken_at": "2026-10-10T10:00:00", "elo": 4300.0}, {"taken_at": "2026-10-29T09:00:00", "elo": 9000.0}]
    ach = [_t(9, 10, 8), _t(9, 12), _t(10, 28, 23), _t(10, 29, 8), _t(9, 10, 7, 59)]   # 시작 정각 포함 · 끝 정각·시작 전은 제외 → 3
    r = ss.season_review(ms, details, "me", ctx, elo, sp, finals={"2026-09-10": 150, "2026-07-30": 7}, ach_times=ach)
    assert (r.games, r.win, r.draw, r.lose) == (6, 4, 1, 1) and round(r.rate, 2) == 66.67
    assert (r.best_div, r.final_div) == (900, 1000)             # 마지막 경기(m6)의 등급 — 입력 순서와 무관
    assert r.peak_elo == 4500.0                                  # 시즌 안 최고(지금 시즌 9000 은 아니다)
    assert r.longest_win == 3                                    # m1 m2 m3 승 · 무승부가 끊는다 · 지금 시즌 승(m7)은 이어 붙지 않는다
    assert r.formation == (fa, 4)                                # A 4판 · B 1판 · 모름(m5) 제외
    assert r.scorer == (101, 3, 2)                               # 101: 3골(2경기) · 102: 3골(3경기) → 골이 같으면 덜 뛴 쪽 · 지금 시즌 103 의 9골은 안 센다
    assert r.final_rank == 150 and r.achievements == 3
    assert r.play_min > 0 and r.from_day == date(2026, 9, 20)    # 첫 경기가 시작+3일(09-13 08:00)보다 늦다 → 저장 시작일 표시
    assert r.rival.nickname == "x" and r.rival.games == 6        # 그 시즌 경기만(지금 시즌·A 의 같은 상대 경기는 안 센다)
    old = ss.season_review(ms, details, "me", ctx, elo, ss.season_spans([S_A, S_B], NOW)[2])   # A 시즌 — m8 한 판
    assert old.games == 1 and old.best_div == 800 and old.rival is None
    none = ss.season_review([], [], "me", {}, [], sp)
    assert none.games == 0 and none.scorer is None and none.final_rank is None and none.from_day is None


def test_top_scorer_ties_go_to_the_one_who_played_less_and_zero_goals_is_none():
    d = [_detail("a", "2026-09-20T01:00:00", {1: 1, 2: 1}), _detail("b", "2026-09-20T02:00:00", {2: 1}),
         _detail("c", "2026-09-21T01:00:00", {1: 1, 3: 5})]
    assert ss.top_scorer(d, "me", {"a", "b"}) == (2, 2, 2)                       # 2 번 선수 2골(2경기) · 1 번 1골
    assert ss.top_scorer(d, "me", {"a", "c"}) == (3, 5, 1)
    tie = [_detail("a", "2026-09-20T01:00:00", {1: 1, 2: 1}), _detail("b", "2026-09-20T02:00:00", {2: 0})]
    assert ss.top_scorer(tie, "me", {"a", "b"}) == (1, 1, 1)                     # 1·2 번 모두 1골 — 덜 뛴 1 번(2 번은 2경기)
    assert ss.top_scorer([_detail("z", "2026-09-20T01:00:00", {1: 0})], "me", {"z"}) is None
    assert ss.top_scorer(d, "me", set()) is None


def test_review_rival_uses_only_that_season():
    sp = ss.season_spans([S_A, S_B], NOW)[1]
    n = config.RIVAL_MIN_GAMES
    inside = [_m(i, "승", _t(9, 15, 9) + timedelta(hours=i)) for i in range(n)]
    outside = [_m(100 + i, "승", _t(9, 1, 9) + timedelta(hours=i)) for i in range(n)]
    assert ss.season_review(inside + outside, [], "me", {}, [], sp).rival.games == n          # 그 시즌 경기만 센다
    assert ss.season_review(inside[:-1] + outside, [], "me", {}, [], sp).rival is None        # 모자라면 숨김(밖의 경기는 안 센다)


# ── Y1 연패 한 줄 ──────────────────────────────────────────────────────────
def _tail(results, last, gap=10):
    """마지막 판이 last 인 경기 목록(gap 분 간격)."""
    n = len(results)
    return [_m(i, r, last - timedelta(minutes=gap * (n - 1 - i))) for i, r in enumerate(results)]


def test_streak_note_conditions_inside_and_outside():
    now = _t(11, 2, 12)
    ok = _tail(["승", "패", "패", "패"], now - timedelta(hours=1))
    n = ss.streak_note(ok, {}, now)
    assert n is not None and n.run == 3 and n.min_loss == config.Y1_MIN_LOSS
    assert ss.streak_note(_tail(["승", "패", "패"], now - timedelta(hours=1)), {}, now) is None                    # 2연패 — 모자란다
    assert ss.streak_note(_tail(["패", "패", "패", "무", "패", "패"], now - timedelta(hours=1)), {}, now) is None   # 무승부가 연패를 끊는다
    assert ss.streak_note(_tail(["패", "패", "패", "패"], now - timedelta(hours=1)), {}, now).run == 4
    h = config.Y1_RECENT_H
    assert ss.streak_note(_tail(["패"] * 3, now - timedelta(hours=h)), {}, now) is not None                        # 정확히 6시간 — 안
    assert ss.streak_note(_tail(["패"] * 3, now - timedelta(hours=h, seconds=1)), {}, now) is None                 # 1초 넘으면 밖
    assert ss.streak_note([], {}, now) is None


def test_streak_note_counts_games_after_three_or_more_losses_over_all_saved_games():
    now = _t(11, 2, 12)
    #            0    1    2    3    4    5    6    7    8    9   10
    seq = ["승", "패", "패", "패", "승", "패", "패", "패", "패", "무", "패", "패", "패"]
    ms = _tail(seq, now - timedelta(minutes=30))
    n = ss.streak_note(ms, {}, now)
    assert n.run == 3 and n.base == (2, 1, 10)                                  # 저장 전체(시즌 필터와 무관)
    # 3연패 이상 뒤 다음 판: idx4(승) · idx8(패, 앞에 패×3→4째가 아니라 idx5·6·7 3연패 뒤 idx8) · idx9(무, 4연패 뒤) · 마지막 연패는 다음 판이 아직 없다
    assert n.after == (1, 1, 1), n.after
    assert n.weak and not n.significant                                        # 3판 — 표본 모자람(MIN_COND) · 유의 아님


def test_streak_note_weak_flag_flips_at_min_cond_and_significance_follows_the_tables():
    from analysis import MIN_COND
    big = _blocks()
    last = big[-1].match_date
    for i in range(3):
        big.append(_m(900 + i, "패", last + timedelta(minutes=1000 + 10 * i)))
    now = big[-1].match_date + timedelta(minutes=30)
    n = ss.streak_note(big, {}, now)
    assert n is not None and n.significant, "표 셋 중 유의한 표(직전 간격)가 있는데 안 켜졌다"
    assert n.weak == (sum(n.after) < MIN_COND)
    # 판이 MIN_COND 개 딱 모이면 흐림이 풀린다 — 3연패 뒤 판을 MIN_COND 번 만든 자료
    now = _t(11, 2, 12)
    seq = (["패"] * 3 + ["승"]) * MIN_COND + ["패"] * 3
    w = ss.streak_note(_tail(seq, now - timedelta(hours=1)), {}, now)
    assert sum(w.after) == MIN_COND and not w.weak
    w1 = ss.streak_note(_tail(seq[4:], now - timedelta(hours=1)), {}, now)
    assert sum(w1.after) == MIN_COND - 1 and w1.weak


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
