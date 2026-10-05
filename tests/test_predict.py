"""시즌 말 순위 예측(predict.py) 테스트 — 가짜 스냅숏으로, 네트워크 없이.

pytest 없이 `python tests/test_predict.py`. 구단주·프로필 번호는 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import predict as P
import ranker
import rankcollect as rc
from seasons import Season

NOW = datetime(2026, 10, 20, 12, 0, 0)
START = date(2026, 9, 10)
CUTS = {1: 4700.0, 10: 4650.0, 50: 4600.0, 100: 4560.0, 200: 4500.0, 500: 4400.0,
        1000: 4300.0, 2000: 4100.0, 5000: 3700.0, 10000: 3000.0}
# 지난 시즌 — 길이 63일 둘. 시즌표의 마지막 끝난 시즌이 09-10 에 끝났다(지금 시즌 시작)
SEASONS = [Season(90, "시즌 9", date(2026, 7, 9), START), Season(89, "시즌 8", date(2026, 5, 7), date(2026, 7, 9)),
           Season(88, "시즌 7", date(2026, 3, 5), date(2026, 5, 7))]


def mean_step(e: float, g: int) -> float:
    """가짜 세상의 되돌림 — 3500점에선 경기당 +1, 4500점에선 0, 그 위는 내려간다."""
    return g * (1.0 - (e - 3500.0) / 1000.0)


class Env:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="predict_"))
        self.db = self.dir / "rank.db"

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def make_world(db: Path, *, days=7, people=4000, seed=1, gaps=None, season_seq=None, drop=None,
               lo=3000.0, hi=4600.0, cuts=CUTS, past=(90, 89, 88), past_cuts=None):
    """가짜 원본 스냅숏 days 개 — 하루 간격(gaps 로 바꿈) · season_seq 목록 · drop(e) → 이탈 확률."""
    rng = random.Random(seed)
    conn = rc.open_rank_db(db)
    elos = [rng.uniform(lo, hi) for _ in range(people)]
    games = [rng.randint(100, 900) for _ in range(people)]
    alive = [True] * people
    t = NOW - timedelta(days=days)
    gaps = gaps or [24] * (days - 1)
    seqs = season_seq or [1] * days
    with conn:
        for d in range(days):
            sid = d + 1
            conn.execute("INSERT INTO snapshots (id, taken_at, row_count, dup_count, top_elo, season_seq, new_season,"
                         " ended_season) VALUES (?, ?, ?, 0, ?, ?, 0, 90)",
                         (sid, t.isoformat(timespec="seconds"), people, max(elos), seqs[d]))
            order = sorted((i for i in range(people) if alive[i]), key=lambda i: -elos[i])
            conn.executemany("INSERT INTO snapshot_rows (snapshot_id, rank, profile_sn, nickname, elo, win, draw, lose)"
                             " VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                             [(sid, r + 1, 100000 + i, f"p{i}", elos[i], games[i]) for r, i in enumerate(order)])
            conn.executemany("INSERT INTO cut_elo (snapshot_id, rank, elo) VALUES (?, ?, ?)",
                             [(sid, r, e) for r, e in cuts.items()])
            for i in range(people):
                if not alive[i]:
                    continue
                if drop and rng.random() < drop(elos[i]):
                    alive[i] = False
                    continue
                g = rng.choice((0, 20, 60, 100, 140))
                if seqs[min(d + 1, days - 1)] != seqs[d]:
                    games[i] = 0          # 새 시즌 — 누적이 0 으로
                games[i] += g
                elos[i] += mean_step(elos[i], g) + rng.gauss(0, 2.0 * g ** 0.5)
            if d < days - 1:
                t += timedelta(hours=gaps[d])
        for no in past:
            c = (past_cuts or {}).get(no, cuts)
            conn.executemany("INSERT INTO season_cuts (season_no, rank, elo, fetched_at) VALUES (?, ?, ?, '')",
                             [(no, r, e) for r, e in c.items()])
    conn.close()
    P._steps_cache.clear()


def rows_for(elo: float, days=4) -> list[dict]:
    """내 elo_history — 하루 한 점, 마지막이 elo."""
    return [{"taken_at": (NOW - timedelta(days=days - 1 - i)).isoformat(timespec="seconds"),
             "elo": elo - (days - 1 - i) * 5, "profile_sn": 555, "rank": 900} for i in range(days)]


def dates_for(per_day=60, days=14) -> list[datetime]:
    return [NOW - timedelta(days=d, minutes=m) for d in range(days) for m in range(per_day)]


def run(env: Env, elo=4300.0, *, notice=None, seasons=SEASONS, n=400, per_day=60, **kw) -> P.Prediction:
    conn = rc.open_rank_db_ro(env.db)
    try:
        return P.predict_for(elo_rows=rows_for(elo), match_dates=dates_for(per_day), rank_conn=conn,
                             seasons=seasons, notice=notice, now=NOW, web_on=kw.get("web_on", True),
                             collect_on=kw.get("collect_on", True), rng=random.Random(7), n=n)
    finally:
        conn.close()


def with_world(fn, **world):
    env = Env()
    try:
        make_world(env.db, **world)
        return fn(env)
    finally:
        env.close()


# ── 걸음 표본 ─────────────────────────────────────────────────────────────

def _snaps(gaps, seqs=None):
    t = NOW
    out = []
    for i, g in enumerate([0] + list(gaps)):
        t += timedelta(hours=g)
        out.append({"id": i + 1, "taken_at": t.isoformat(), "season_seq": (seqs or [1] * (len(gaps) + 1))[i]})
    return out


def test_day_steps_uses_only_day_pairs_in_one_season():
    # 1→2 하루 · 2→3 12시간(같은 날 두 번) · 3→4 하루 · 4→5 시즌 바뀜
    snaps = _snaps([24, 12, 24, 24], seqs=[1, 1, 1, 1, 2])
    data = {1: {1: (4000.0, 100), 2: (3900.0, 50)},
            2: {1: (4010.0, 130)},                      # 2번은 1만 위 밖으로
            3: {1: (4015.0, 140)},
            4: {1: (4005.0, 170)},
            5: {1: (4000.0, 20)}}
    st = P.build_steps(snaps, data.get)
    assert st.pairs == 2, st.pairs
    assert [(s.elo0, s.d, s.games) for s in st.steps] == [(4000.0, 10.0, 30), (4015.0, -10.0, 30)], st.steps
    assert st.drops == [3900.0], "다음 스냅숏에 없는 구단주는 이탈로 센다"


def test_day_steps_prev3_and_blocks():
    snaps = _snaps([24] * 6)
    elo = [4000.0, 4010.0, 4030.0, 4020.0, 4040.0, 4045.0, 4050.0]
    data = {i + 1: {7: (e, 100 * (i + 1))} for i, e in enumerate(elo)}
    st = P.build_steps(snaps, data.get)
    by0 = {s.elo0: s for s in st.steps}
    assert by0[4000.0].prev3 is None and by0[4000.0].nxt == ((20.0, 100), (-10.0, 100))
    assert by0[4020.0].prev3 == 20.0, "앞 사흘 +10 +20 −10"
    assert by0[4040.0].nxt == ((5.0, 100),), "이어진 날까지만"


def test_day_steps_cached_by_raw_snapshots():
    def go(env):
        conn = rc.open_rank_db_ro(env.db)
        try:
            a = P.day_steps(conn)
            b = P.day_steps(conn)
        finally:
            conn.close()
        assert a is b, "원본 스냅숏이 같으면 다시 만들지 않는다"
        assert a.pairs == 4
    with_world(go, days=5, people=200)


# ── 시뮬레이션 ────────────────────────────────────────────────────────────

def test_reversion_keeps_paths_inside_sample():
    """하루 100판 × 60일이어도 되돌림이 있으면 표본 최고점 위로 폭주하지 않는다(검토 B 1)."""
    def go(env):
        conn = rc.open_rank_db_ro(env.db)
        try:
            st = P.day_steps(conn)
        finally:
            conn.close()
        top = max(s.elo0 + s.d for s in st.steps)
        res = P.simulate(P.SimInput(4300.0, (), (100,), {}), st, [(63, CUTS)], [60], 40,
                         random.Random(3), n=300)
        assert max(res.end_elos) <= top + 100, (max(res.end_elos), top)
        near = P.simulate(P.SimInput(4490.0, (), (100,), {}), st, [(63, CUTS)], [5], 40, random.Random(3), n=300)
        assert 0.05 < near.p[200] < 0.95, near.p
    with_world(go)


def _flow_steps():
    """흐름 3분위가 다음 걸음과 이어지되, 한 번 움직이면 가운데로 섞이게 만든 표본."""
    rng = random.Random(5)
    steps = []
    for _ in range(600):
        steps.append(P.Step(4000.0, 10.0 + rng.gauss(0, 1), 1, 300.0))
        steps.append(P.Step(4000.0, 0.0 + rng.gauss(0, 1), 1, 0.0))
        steps.append(P.Step(4000.0, -10.0 + rng.gauss(0, 1), 1, -300.0))
    return P.Steps(key=(), pairs=10, steps=steps, drops=[], edges=P._quantiles([1], 4), elos=[s.elo0 for s in steps])


def test_flow_matching_follows_path_not_my_past():
    """흐름 맞추기는 경로 자신의 직전 사흘 — 내 사흘에 고정하면 위로 쏠린다(검토 B 3-1)."""
    st = _flow_steps()
    up = P.simulate(P.SimInput(4000.0, (100.0, 100.0, 100.0), (1,), {}), st, [(63, CUTS)], [30], 40,
                    random.Random(1), n=200)
    none = P.simulate(P.SimInput(4000.0, (), (1,), {}), st, [(63, CUTS)], [30], 40, random.Random(1), n=200)
    a = sum(up.end_elos) / len(up.end_elos) - 4000.0
    b = sum(none.end_elos) / len(none.end_elos) - 4000.0
    assert abs(a - b) <= 60, (a, b)
    assert a > b, "첫날은 내 흐름을 맞춘다"


def test_extremes_saturate():
    def go(env):
        tomorrow = (NOW.date() + timedelta(days=1), START)
        far = run(env, CUTS[200] + 300, notice=tomorrow)
        assert far.ok and far.p[200] > 0.99, far
        low = run(env, CUTS[200] - 300, notice=tomorrow)
        assert low.ok and low.p[200] < 0.01, low
        assert "(공지)" in low.end_text
    with_world(go, hi=4900.0)


def test_golden_deterministic():
    def go(env):
        a = run(env, 4350.0)
        b = run(env, 4350.0)
        assert (a.p, a.lo, a.hi) == (b.p, b.lo, b.hi), "같은 입력·seed 면 같은 숫자"
        got = (round(a.p[200], 4), round(a.p[1000], 4), a.lo, a.hi, a.end_text)
        assert got == GOLDEN_VALUE, got
    with_world(go)


GOLDEN_VALUE = (0.07, 0.9975, 223, 615, "11/12(추정)")   # 의도한 변경이면 새 값으로 — 아니면 버그


def test_cut_spread_follows_seasons():
    """같은 컷 시즌 여덟 vs 흩어진 여덟 — 흩어지면 순위 범위가 넓어진다. 모든 경로에서 200위 컷 ≥ 1,000위 컷."""
    def spread(env, past_cuts):
        conn = rc.open_rank_db_ro(env.db)
        try:
            st = P.day_steps(conn)
        finally:
            conn.close()
        res = P.simulate(P.SimInput(4300.0, (), (60,), CUTS), st, [(63, c) for c in past_cuts], [20], 40,
                         random.Random(2), n=400)
        return res
    def go(env):
        same = spread(env, [CUTS] * 8)
        moved = [{r: e + off for r, e in CUTS.items()} for off in (-120, -80, -40, 0, 40, 80, 120, 160)]
        wide = spread(env, moved)
        q = lambda r: (r.ranks[int(0.9 * len(r.ranks))] - r.ranks[int(0.1 * len(r.ranks))])  # noqa: E731
        assert q(wide) > q(same), (q(wide), q(same))
        for c in moved:
            m = P._monotone({r: max(e, CUTS[r] + 500 if r == 1000 else e) for r, e in c.items()})
            assert m[200] >= m[1000], m
    with_world(go)


def test_late_season_cut_warning():
    same = []
    def go(env):
        pr = run(env, 4350.0)
        assert P.NOTE_LATE not in pr.notes, pr.notes
        same.append(pr)
    with_world(go)
    low = {r: e - 200 for r, e in CUTS.items()}
    def go2(env):
        pr = run(env, 4350.0)
        assert pr.ok and P.NOTE_LATE in pr.notes, pr.notes
        # 지난 시즌 컷이 지금보다 낮으면 순위마다 지금 컷 — 지난 컷이 지금과 같은 세상과 같은 숫자여야 한다
        assert (pr.p, pr.lo, pr.hi) == (same[0].p, same[0].lo, same[0].hi), (pr.p, same[0].p)
    with_world(go2, past_cuts={90: low, 89: low, 88: low})


def test_rank_of_interpolates_log_rank():
    assert P.rank_of(4800, CUTS) == 1
    assert P.rank_of(2999, CUTS) == P.OUTSIDE_RANK
    assert P.rank_of(4500, CUTS) == 200
    r = P.rank_of(4400 + 50, CUTS)        # 200(4500)과 500(4400) 사이 가운데 → log 가운데 ≈ 316
    assert 300 <= r <= 330, r


# ── 종료일 ───────────────────────────────────────────────────────────────

def test_end_notice_vs_estimate():
    today = date(2026, 10, 20)
    g = P.guess_end(today, START, [63, 28, 70, 42], date(2026, 11, 12))
    assert g.source == "notice" and g.days == (23,) and g.text == "11/12(공지)"
    g = P.guess_end(today, START, [63, 28, 70, 42], None)     # 경과 40일 — 42·63·70 만
    assert g.source == "estimate" and sorted(g.days) == [2, 23, 30], g
    assert g.text == "10/22~11/19(중앙 11/12, 추정)", g.text
    g = P.guess_end(date(2026, 11, 25), START, [63, 28, 70], None)   # 경과 76일 > 70
    assert g.source == "soon" and g.days == tuple(range(1, config.PREDICT_SOON_DAYS + 1))


def test_notice_vs_stale_season_table():
    new_start = date(2026, 11, 12)
    # 시즌표가 늦다(옛 시즌 시작) — 새 시즌 공지를 믿는다
    assert P.resolve_notice((date(2027, 1, 14), new_start), START) == (date(2027, 1, 14), new_start)
    # 시즌표가 공지보다 새 시즌 — 공지를 버린다
    assert P.resolve_notice((date(2026, 11, 12), START), new_start) == (None, new_start)
    assert P.resolve_notice(None, START) == (None, START)

    def go(env):
        # 새 시즌 첫날: 공지는 새 시즌(시작 11/12) · 시즌표 캐시는 옛 시즌 — "마감 처리 중"이 나오면 안 된다
        later = SEASONS
        pr = run(env, 4350.0, notice=(date(2027, 1, 14), NOW.date()), seasons=later)
        assert pr.message != P.MSG_CLOSING, pr
        # 공지 종료일이 지났는데 시즌표가 그대로 — 마감 처리 중
        pr = run(env, 4350.0, notice=(NOW.date(), START))
        assert pr.message == P.MSG_CLOSING, pr
    with_world(go)


# ── 원인별 문구 ───────────────────────────────────────────────────────────

def test_condition_messages():
    def go(env):
        assert run(env, collect_on=False).message == P.MSG_COLLECT_OFF
        assert run(env, web_on=False).message == P.MSG_COLLECT_OFF
        conn = rc.open_rank_db_ro(env.db)
        try:
            pr = P.predict_for(elo_rows=[], match_dates=dates_for(), rank_conn=conn, seasons=SEASONS, notice=None,
                               now=NOW, web_on=True, collect_on=True)
        finally:
            conn.close()
        assert pr.message == P.MSG_NO_ELO, pr
        assert run(env, 2900.0).message == P.MSG_OUTSIDE
        assert run(env, 4350.0, per_day=1).message == P.MSG_FEW_GAMES
        assert run(env, 4350.0, seasons=[]).message == P.MSG_NO_SEASONS
        ok = run(env, 4350.0)
        assert ok.ok, ok
        text = P.describe(ok)
        assert "200위 안" in text and P.NOTE_UNVERIFIED in text and "보정" not in text, text
    with_world(go)
    # 지난 시즌 컷이 하나뿐
    assert with_world(lambda env: run(env, 4350.0), past=(90,)).message == P.MSG_NO_SEASONS
    # 이웃 쌍이 둘 — 하루 뒤 / 5천 위 근처는 점수대가 얇다
    pr = with_world(lambda env: run(env, 4350.0), days=3)
    assert pr.message == P.MSG_GATHERING.format(n=1), pr.message
    pr = with_world(lambda env: run(env, 4350.0), days=1)
    assert pr.message == P.MSG_GATHERING_UNKNOWN, pr.message
    # 점수대 기록이 얇다(사람이 적다)
    assert with_world(lambda env: run(env, 4350.0), people=300).message == P.MSG_THIN


def test_drop_out_band_reads_thin():
    """그 점수대에서 3% 가 1만 위 밖으로 — 남은 사람만 보면 치우치므로 숫자를 안 낸다(검토 B 4)."""
    pr = with_world(lambda env: run(env, 3100.0), drop=lambda e: 0.03 if e < 3200 else 0.0)
    assert pr.message == P.MSG_THIN, pr.message
    pr = with_world(lambda env: run(env, 4350.0), drop=lambda e: 0.03 if e < 3200 else 0.0)
    assert pr.ok, pr.message


def test_my_state_and_daily_counts():
    rows = [{"taken_at": "2026-10-15T10:00:00", "elo": 4000.0, "profile_sn": 1},
            {"taken_at": "2026-10-17T10:00:00", "elo": 4010.0},    # 16일이 빔 — 그 앞은 버린다
            {"taken_at": "2026-10-18T09:00:00", "elo": 4020.0},
            {"taken_at": "2026-10-18T21:00:00", "elo": 4030.0},    # 같은 날은 마지막 값
            {"taken_at": "2026-10-19T10:00:00", "elo": 4025.0}]
    assert P.my_state(rows) == (4025.0, (20.0, -5.0), 1)
    ds = [datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 11), datetime(2026, 10, 4, 9)]
    c = P.daily_counts(ds, days=14)
    assert len(c) == 14 and c[-1] == 2 and sum(c) == 2, c
    assert P.daily_counts([]) == ()


# ── 지난 시즌 최종 컷 ─────────────────────────────────────────────────────

def test_fetch_season_cuts_skips_failures_and_fetches_missing():
    env = Env()
    try:
        conn = rc.open_rank_db(env.db)
        calls = []

        def fetch(no, rank):
            calls.append((no, rank))
            if no == 88 and rank == 1000:
                raise ranker.RankerError("일시 실패")
            return 5000.0 - rank / 10 - no
        n = rc.fetch_season_cuts(conn, 90, NOW, fetch=fetch)
        assert n == config.PREDICT_FETCH_SEASONS - 1, n
        have = rc.season_cuts(conn)
        assert 88 not in have and 90 in have and len(have[90]) == len(config.RANK_CUT_RANKS)
        calls.clear()
        assert rc.fetch_season_cuts(conn, 90, NOW, fetch=lambda no, r: 4000.0) == 1
        assert rc.season_cuts(conn)[88][1000] == 4000.0, "빠진 시즌만 다시 받는다"
        stop = threading.Event()
        stop.set()
        assert rc.fetch_season_cuts(conn, 91, NOW, fetch=fetch, cancel=stop) == 0
        assert rc.fetch_season_cuts(conn, None, NOW, fetch=fetch) == 0
        conn.close()
    finally:
        env.close()


def test_predict_is_behind_core_api():
    import core_api
    assert core_api.predict_for is P.predict_for and core_api.Prediction is P.Prediction


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
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
