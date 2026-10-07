"""랭킹 수집기(rankcollect.py) 테스트 — 가짜 랭킹 목록으로, 네트워크 없이.

pytest 없이 `python tests/test_rankcollect.py`. 실제 넥슨에 붙는 스모크는 `python rankcollect.py --pages 3`.
닉네임·프로필 번호는 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import ranker
import rankcollect as rc
import seasons


def _no_season_fetch():
    raise seasons.SeasonError("테스트 — 네트워크 없음")


rc._fetch_seasons = _no_season_fetch   # 메타 처리가 낡은 시즌표를 넥슨에서 다시 받지 않게

DATE = "Sun, 04 Oct 2026 13:42:06 GMT"
NOW = datetime(2026, 10, 4, 22, 42, 0)


def _row(rank, sn=None, elo=None, value=1000, color="", formation="4-2-3-1"):
    return ranker.RankRow(rank=rank, profile_sn=sn if sn is not None else 900000 + rank, nickname=f"n{rank}",
                          elo=elo if elo is not None else 5000.0 - rank, team_value=value,
                          team_color=color, formation=formation)


class World:
    """가짜 랭킹 — people 명을 20명씩. 목록 끝을 넘은 쪽은 마지막 쪽을 되풀이한다(실측: 501쪽 = 500쪽)."""

    def __init__(self, people=200, date=DATE, sleep=0.0):
        self.people, self.date, self.sleep = people, date, sleep
        self.calls, self.fail = [], {}          # fail: 쪽 → 던질 예외 또는 rows 를 바꾸는 함수
        self.dates = {}
        self.ref, self.refs = None, {}        # 넥슨 기준 시각(2.3.1) — 쪽마다 바꿀 수 있다
        self.lock = threading.Lock()
        self.now = self.peak = 0

    def __call__(self, page):
        with self.lock:
            self.calls.append(page)
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            if self.sleep:
                time.sleep(self.sleep)
            f = self.fail.get(page)
            if isinstance(f, Exception):
                raise f
            last = max(1, -(-self.people // ranker.RANK_PAGE_SIZE))
            p = min(page, last)
            lo = (p - 1) * ranker.RANK_PAGE_SIZE + 1
            rows = [_row(r) for r in range(lo, min(lo + ranker.RANK_PAGE_SIZE, self.people + 1))]
            if callable(f):
                rows = f(rows)
            return ranker.RankPageResult(page, rows, self.dates.get(page, self.date), self.refs.get(page, self.ref))
        finally:
            with self.lock:
                self.now -= 1


class Env:
    """임시 데이터 폴더 — .env(스위치)·rank.db. 끝나면 config 를 되돌린다."""

    def __init__(self, web="1", collect="1"):
        self.dir = Path(tempfile.mkdtemp(prefix="rankcollect_"))
        self.db = self.dir / "rank.db"
        self.saved = (config.ENV_PATH, config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED,
                      os.environ.get(config.RANK_COLLECT_VAR), os.environ.get(config.WEB_DATA_VAR))
        self.saved_db = config.DB_PATH
        config.ENV_PATH = self.dir / ".env"
        # 수집이 따라가기 ELO 를 fifa.db 에 옮긴다(1.3.1) — 실제 사용자 DB 를 열지 않게
        config.DB_PATH = self.fifa = self.dir / "fifa.db"
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        self.write(web, collect)

    def write(self, web="1", collect="1", notice=None, on_at=None):
        # 사람별 표(2.3.1)는 디스크의 동의 값·켠 시각을 다시 읽는다 — 기본은 지금 안내에 동의, 켠 시각 없음(옛 버전에서 켬)
        notice = config.NOTICE_VERSION if notice is None else notice
        text = f"{config.WEB_DATA_VAR}={web}\n{config.RANK_COLLECT_VAR}={collect}\n{config.NOTICE_VAR}={notice}\n"
        if on_at:
            text += f"{config.RANK_COLLECT_ON_AT_VAR}={on_at}\n"
        config.ENV_PATH.write_text(text, encoding="utf-8")

    def conn(self):
        return rc.open_rank_db(self.db)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        (config.ENV_PATH, config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, rc_env, web_env) = self.saved
        config.DB_PATH = self.saved_db
        for var, val in ((config.RANK_COLLECT_VAR, rc_env), (config.WEB_DATA_VAR, web_env)):
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        shutil.rmtree(self.dir, ignore_errors=True)


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ── 한 회차 ────────────────────────────────────────────────────────────────

def test_collect_saves_snapshot_and_aggregates():
    with Env() as env:
        out = rc.collect(db_path=env.db, fetch=World(200), pages=10, now_fn=lambda: NOW)
        assert (out.kind, out.rows) == ("ok", 200), out
        c = env.conn()
        assert _count(c, "snapshot_rows") == 200
        s = c.execute("SELECT * FROM snapshots").fetchone()
        assert (s["row_count"], s["top_elo"], s["season_seq"], s["new_season"]) == (200, 4999.0, 1, 0), dict(s)
        assert rc.cut_elo(c) == {1: 4999.0, 10: 4990.0, 50: 4950.0, 100: 4900.0, 200: 4800.0}
        assert rc.last_snapshot_at(c) == NOW
        assert rc.get_state(c)["last_success_at"] == NOW.isoformat()
        assert _count(c, "collect_lock") == 0, "끝나고 잠금을 풀지 않았다"
        c.close()


def test_season_cuts_failure_keeps_round_ok():
    """회차 끝에 빠진 지난 시즌 컷을 받는다 — 실패(넥슨 막음 포함)해도 회차는 ok, 다음 회차가 빠진 시즌만 다시."""
    keep = ranker.fetch_season_cut
    calls = []
    try:
        with Env() as env:
            def broken(no, rank):
                calls.append(no)
                raise ranker.RankBlocked("막힘")
            ranker.fetch_season_cut = broken
            out = rc.collect(db_path=env.db, fetch=World(200), pages=10, now_fn=lambda: NOW, ended_season=90)
            assert out.kind == "ok", out
            assert calls and rc.season_cuts(env.conn()) == {}, calls
            assert rc.get_state(env.conn()).get("fail_count") in (None, "0"), "컷 실패가 회차 실패로 셌다"
            ranker.fetch_season_cut = lambda no, rank: 4000.0 + no
            later = NOW + timedelta(days=1, hours=1)
            out = rc.collect(db_path=env.db, fetch=World(200), pages=10, now_fn=lambda: later, ended_season=90)
            assert out.kind == "ok", out
            got = rc.season_cuts(env.conn())
            assert sorted(got) == list(range(90 - config.PREDICT_FETCH_SEASONS + 1, 91)), sorted(got)
    finally:
        ranker.fetch_season_cut = keep


def test_aggregate_values():
    rows = [_row(1, value=100, color="가 FC"), _row(2, value=300, color=""), _row(3, value=None, color="가 FC"),
            _row(250, value=10, formation="4-4-2"), _row(12000)]          # 1만 위 밖은 어느 구간에도 안 든다
    counts, values, cuts = rc.aggregate(rows)
    assert values == [(200, 3, 200, 200), (1000, 1, 10, 10)], values   # 값 없는 행은 평균에서 빠지되 인원엔 든다
    assert (200, "team_color", "", 1) in counts and (200, "team_color", "가 FC", 2) in counts, counts
    assert (1000, "formation", "4-4-2", 1) in counts
    assert not any(t == 10000 for t, *_ in counts)
    # 10위가 빠졌으면 그 아래 사람(250위) — 위 사람(3위)을 쓰면 컷이 부풀려진다
    assert dict(cuts)[10] == 5000.0 - 250, cuts
    assert 20000 not in dict(cuts) and dict(cuts)[10000] == 5000.0 - 12000


def test_overlap_removed_by_profile_number():
    w = World(60)
    # 순위가 움직여 2쪽 첫 사람이 1쪽 끝 사람과 같다
    w.fail[2] = lambda rows: [_row(21, sn=900020)] + rows[1:]
    rows, dups = rc.run_round(w, pages=3)
    assert dups == 1 and len(rows) == 59 and len({r.profile_sn for r in rows}) == 59, (dups, len(rows))


def test_end_of_list_stops_at_repeat():
    with Env() as env:
        out = rc.collect(db_path=env.db, fetch=World(150), pages=12, now_fn=lambda: NOW)
        assert (out.kind, out.rows) == ("ok", 150), out     # 8쪽(10명) 뒤는 되풀이 — 두 번 세지 않는다


def test_first_failure_cancels_the_rest_and_saves_nothing():
    with Env() as env:
        w = World(2000, sleep=0.01)
        w.fail[3] = ranker.RankerError("x")
        out = rc.collect(db_path=env.db, fetch=w, pages=100, now_fn=lambda: NOW)
        assert out.kind == "failed", out
        assert len(w.calls) < 30, f"실패 뒤에도 {len(w.calls)}쪽을 요청했다"
        c = env.conn()
        assert _count(c, "snapshots") == 0 and _count(c, "snapshot_rows") == 0
        c.close()


def test_structure_change_mid_list_is_failure():
    with Env() as env:
        w = World(200)
        w.fail[5] = lambda rows: [ranker.RankRow(rank=r.rank, profile_sn=None, nickname=r.nickname) for r in rows]
        out = rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW)
        assert out.kind == "failed" and "빠진" in out.message, out
        w2 = World(200)
        w2.fail[4] = lambda rows: []
        with _no_retry_wait():
            assert rc.collect(db_path=env.db, fetch=w2, pages=10, now_fn=lambda: NOW).kind == "failed"
        # 끝까지 비면 다시 받은 뒤에도 실패 — 다시 받기가 구조 변경 보호를 풀지 않는다
        assert w2.calls.count(4) == 1 + config.RANK_EMPTY_RETRIES, w2.calls.count(4)
        c = env.conn()
        assert _count(c, "snapshots") == 0
        c.close()


class _no_retry_wait:
    def __enter__(self):
        self.saved = config.RANK_EMPTY_RETRY_WAIT_S
        config.RANK_EMPTY_RETRY_WAIT_S = 0.0

    def __exit__(self, *exc):
        config.RANK_EMPTY_RETRY_WAIT_S = self.saved


def test_transient_empty_page_is_refetched_alone():
    # 10-05 실측: 500쪽 중 2쪽 하나가 한 번 비어 회차 전체가 실패로 셌다. 그 쪽만 다시 받으면 정상이었다.
    with Env() as env:
        w = World(200)
        hits = {"n": 0}

        def once_empty(rows):
            hits["n"] += 1
            return [] if hits["n"] == 1 else rows
        w.fail[2] = once_empty
        with _no_retry_wait():
            out = rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW)
        assert (out.kind, out.rows) == ("ok", 200), out
        assert w.calls.count(2) == 2, f"2쪽 요청 {w.calls.count(2)}번 — 한 번만 다시 받아야"
        assert all(w.calls.count(p) == 1 for p in range(1, 11) if p != 2), "빈 쪽 말고 다른 쪽까지 다시 받았다"
        c = env.conn()
        assert rc.get_state(c).get("fail_count", "0") in ("0", ""), "다시 받아 성공했는데 실패로 셌다"
        c.close()


def test_empty_page_retry_waits_and_cancels():
    # 다시 받기 전 대기를 실제로 하고, 취소되면 대기 중에 빠져나온다
    w = World(200)
    w.fail[2] = lambda rows: []
    saved = config.RANK_EMPTY_RETRY_WAIT_S
    config.RANK_EMPTY_RETRY_WAIT_S = 0.3
    try:
        t0 = time.monotonic()
        try:
            rc.run_round(w, pages=3)
        except ranker.RankStructureError:
            pass
        took = time.monotonic() - t0
        assert took >= 0.3 * config.RANK_EMPTY_RETRIES - 0.05, f"대기 없이 바로 다시 받았다({took:.2f}초)"
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        config.RANK_EMPTY_RETRY_WAIT_S = 5.0
        t0 = time.monotonic()
        try:
            rc.run_round(w, pages=3, cancel=cancel)
            raise AssertionError("취소했는데 끝까지 돌았다")
        except rc.Cancelled:
            pass
        assert time.monotonic() - t0 < 2.0, "취소가 대기를 끊지 못했다"
    finally:
        config.RANK_EMPTY_RETRY_WAIT_S = saved


def test_failure_kinds_map_to_outcomes():
    for exc, kind in [(ranker.RankBlocked("403"), "blocked"), (ranker.RankOffline("x"), "offline"),
                      (ranker.RankStructureError("x"), "failed"), (ranker.RankerError("x"), "failed")]:
        with Env() as env:
            w = World(200)
            w.fail[2] = exc
            assert rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW).kind == kind, exc


def test_straddling_the_hour_is_discarded_not_counted():
    with Env() as env:
        w = World(200)
        w.dates[7] = "Sun, 04 Oct 2026 14:00:01 GMT"
        out = rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW)
        assert out.kind == "straddle", out
        c = env.conn()
        assert _count(c, "snapshots") == 0
        assert rc.get_state(c).get("fail_count") in (None, "0"), "정각 걸침을 실패로 셌다"
        c.close()


def test_workers_capped_by_config():
    w = World(400, sleep=0.02)
    old = config.RANK_COLLECT_WORKERS
    config.RANK_COLLECT_WORKERS = 3          # 기본값(6)이 아닌 값으로 — 배선이 끊겨도 기본값이면 통과한다
    try:
        rc.run_round(w, pages=20)
    finally:
        config.RANK_COLLECT_WORKERS = old
    assert w.peak == 3, w.peak


def test_cancel_leaves_state_and_releases_lock():
    with Env() as env:
        cancel = threading.Event()
        w = World(2000, sleep=0.01)
        out = rc.collect(db_path=env.db, fetch=w, pages=100, now_fn=lambda: NOW, cancel=cancel,
                         progress=lambda d, n: d >= 5 and cancel.set())
        assert out.kind == "cancelled", out
        assert len(w.calls) < 40, len(w.calls)
        c = env.conn()
        assert _count(c, "snapshots") == 0 and _count(c, "collect_lock") == 0
        assert "last_result" not in rc.get_state(c)
        c.close()


def test_workers_run_below_normal_priority():
    # 게임 중에도 돈다 — 작업자는 QThread 가 아니라 우선순위를 직접 낮춘다
    if sys.platform != "win32":
        return
    k = rc._kernel32()
    assert k.GetThreadPriority(k.GetCurrentThread()) == 0, "재는 도구가 틀렸다(주 스레드는 보통 우선순위)"
    seen = []
    w = World(100)

    def fetch(page):
        seen.append(k.GetThreadPriority(k.GetCurrentThread()))
        return w(page)
    rc.run_round(fetch, pages=5)
    assert set(seen) == {-1}, seen      # THREAD_PRIORITY_BELOW_NORMAL


def test_block_outcome_reports_disable():
    with Env() as env:
        outs = []
        for _ in range(config.RANK_BLOCK_ROUNDS):
            w = World(200)
            w.fail[1] = ranker.RankBlocked("429")
            outs.append(rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW))
        assert [o.disabled_by_block for o in outs] == [False] * (config.RANK_BLOCK_ROUNDS - 1) + [True], outs
        assert outs[-1].fail_notice


def test_heartbeat_every_n_pages():
    beats = []
    rc.run_round(World(3000), pages=120, heartbeat=lambda: beats.append(1))
    assert len(beats) == 120 // config.RANK_LOCK_HEARTBEAT_PAGES, len(beats)


# ── 스위치 ─────────────────────────────────────────────────────────────────

def test_disabled_unless_both_switches_on_in_env():
    for web, on in (("0", "1"), ("1", "0")):
        with Env(web, on) as env:
            config.WEB_DATA = config.RANK_COLLECT = True   # 메모리 값은 켜짐 — 다른 실행본이 .env 에서 껐다
            w = World(200)
            out = rc.collect(db_path=env.db, fetch=w, pages=10)
            assert out.kind == "disabled" and not w.calls, (web, on, out)
            assert not env.db.exists(), "꺼져 있는데 rank.db 를 만들었다"
            assert (config.WEB_DATA, config.RANK_COLLECT) == (web == "1", on == "1"), "다시 읽은 값을 반영 안 함"


def test_disabled_before_notice_accepted():
    # v1(1.0.x) 동의자는 v2 의 수집 안내를 못 봤다 — 막힌다. 처음(0)도 같다
    for accepted in (0, config.NOTICE_BASE_VERSION - 1):
        with Env() as env:
            config.NOTICE_ACCEPTED = accepted
            w = World(200)
            assert rc.collect(db_path=env.db, fetch=w, pages=10).kind == "disabled" and not w.calls, accepted


def test_old_notice_keeps_collecting_but_not_tracking():
    # v2(1.1.1~1.2.1) 동의자는 다시 묻기 전에도 수집이 계속 돈다 — 새로 더한 따라가기 기록만 동의 뒤부터
    import store
    with Env() as env:
        config.NOTICE_ACCEPTED = config.NOTICE_BASE_VERSION
        assert config.notice_update_pending() and not config.notice_needed()
        c = store.open_db(env.fifa)
        store.track_add(c, "o1", 900001, "n1")
        c.close()
        out = rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW)
        assert out.kind == "ok", out
        c = store.open_db(env.fifa)
        assert store.elo_history(c, "o1") == [], "동의 전에 따라가기 기록을 적었다"
        c.close()
        config.NOTICE_ACCEPTED = config.TRACK_NOTICE_VERSION
        rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW + timedelta(days=1, hours=2))
        c = store.open_db(env.fifa)
        assert len(store.elo_history(c, "o1")) == 2, "동의 뒤 회차가 14일치(스냅숏 둘)를 채우지 않았다"
        c.close()


def test_read_env_switches_handles_bom_and_korean():
    with Env() as env:
        # 남의 PC(CP949)에서 메모장으로 고친 .env 가 BOM 을 달고 오는 경우 — 읽기는 utf-8-sig
        # BOM 이 첫 키에 붙는 경우 — utf-8 로 읽으면 키가 '﻿FIFA_WEB_DATA' 가 돼 꺼진 것으로 읽힌다
        config.ENV_PATH.write_bytes("﻿FIFA_WEB_DATA=1\n# 한글 주석\nFIFA_RANK_COLLECT=1\n".encode("utf-8"))
        assert config.read_env_switches() == (True, True)
        config.ENV_PATH.unlink()
        assert config.read_env_switches() == (False, False)


# ── 실패 대기 · D6 ──────────────────────────────────────────────────────────

def test_backoff_doubles_and_resets_on_success():
    with Env() as env:
        c = env.conn()
        waits, notices = [], []
        for _ in range(8):
            flags = rc.record_result(c, "failed", NOW)
            waits.append(datetime.fromisoformat(rc.get_state(c)["retry_at"]) - NOW)
            notices.append(flags["fail_notice"])
        assert [w.total_seconds() / 3600 for w in waits] == [1, 2, 4, 8, 16, 24, 24, 24], waits
        assert notices == [False, False, True, True, True, True, True, True], notices
        for kind in ("offline", "straddle", "locked"):           # 세지 않는 것
            rc.record_result(c, kind, NOW)
        assert rc.get_state(c)["fail_count"] == "8"
        rc.record_result(c, "ok", NOW)
        st = rc.get_state(c)
        assert (st["fail_count"], st["retry_at"]) == ("0", ""), st
        assert rc.record_result(c, "failed", NOW) == {"disabled": False, "fail_notice": False}
        assert rc.get_state(c)["retry_at"] == (NOW + timedelta(hours=1)).isoformat()
        c.close()


def test_three_blocked_rounds_turn_collection_off():
    with Env() as env:
        config.RANK_COLLECT = True
        c = env.conn()
        assert not rc.record_result(c, "blocked", NOW)["disabled"]
        assert not rc.record_result(c, "blocked", NOW)["disabled"]
        rc.record_result(c, "failed", NOW)                        # 차단 아닌 실패가 끼면 연속이 끊긴다
        rc.record_result(c, "offline", NOW)                       # 연결 안 됨은 끊지도 세지도 않는다
        assert not rc.record_result(c, "blocked", NOW)["disabled"]
        assert not rc.record_result(c, "blocked", NOW)["disabled"]
        assert rc.record_result(c, "blocked", NOW)["disabled"]
        assert config.RANK_COLLECT is False and config.read_env_switches()[1] is False, "D6 가 .env 를 안 껐다"
        assert rc.get_state(c)["enabled"] == "0" and rc.get_state(c)["off_reason"] == "blocked"
        assert not rc.is_due(c, NOW + timedelta(days=30))
        c.close()
        # .env 를 누가 다시 켜도 rank.db 사본이 막는다 — 다시 켜기는 앱 토글(set_enabled)로
        env.write("1", "1")
        w = World(200)
        assert rc.collect(db_path=env.db, fetch=w, pages=10).kind == "disabled" and not w.calls
        c = env.conn()
        rc.set_enabled(c, True)
        st = rc.get_state(c)
        assert (st["enabled"], st["block_rounds"], st["fail_count"], st["retry_at"]) == ("1", "0", "0", "")
        c.close()
        assert rc.collect(db_path=env.db, fetch=World(200), pages=10, now_fn=lambda: NOW).kind == "ok"


def test_d6_survives_env_write_failure():
    with Env() as env:
        c = env.conn()
        orig = config._save_env
        config._save_env = lambda *a: (_ for _ in ()).throw(PermissionError("busy"))
        try:
            for _ in range(config.RANK_BLOCK_ROUNDS):
                flags = rc.record_result(c, "blocked", NOW)
        finally:
            config._save_env = orig
        assert flags["disabled"] and rc.get_state(c)["enabled"] == "0"
        c.close()
        w = World(200)
        assert rc.collect(db_path=env.db, fetch=w, pages=10).kind == "disabled" and not w.calls


def test_is_due_follows_interval_and_backoff():
    with Env() as env:
        c = env.conn()
        assert rc.is_due(c, NOW)
        rc.save_snapshot(c, [_row(1)], NOW)
        h = config.RANK_COLLECT_INTERVAL_H
        # 마지막 회차의 정각(22:00)부터 h − 1시간 — 지터(+42분)가 쌓이지 않는다(2.3.1 2회차 A)
        due_at = NOW.replace(minute=0) + timedelta(hours=h - 1)
        assert not rc.is_due(c, due_at - timedelta(seconds=1))
        assert rc.is_due(c, due_at)
        rc.record_result(c, "failed", due_at)
        assert not rc.is_due(c, due_at + timedelta(minutes=59))
        assert rc.is_due(c, due_at + timedelta(hours=1))
        c.close()


def _simulate(on_hours, days, phase_min, start_hour=18, start_min=30, seed=1):
    """수집 예약을 흉내 낸다 — 켜져 있는 시(on_hours)에 앱이 켜질 때 한 번 + RANK_CHECK_EVERY_MIN 마다 is_due 를 보고,
    맞으면 pick_start 에 시작(start_still_valid 가 아니면 다시 고름). → 회차 시각들. 회차는 1분 걸린다고 본다."""
    rng = random.Random(seed)
    with Env() as env:
        c = env.conn()
        t0 = datetime(2026, 10, 1, start_hour, start_min)
        rc.save_snapshot(c, [_row(1)], t0)
        taken, planned = [t0], None
        t = t0.replace(minute=0) + timedelta(hours=1, minutes=phase_min)
        end = t0 + timedelta(days=days)
        was_on = True
        while t < end:
            on = t.hour in on_hours
            if on and not was_on:
                t = t.replace(minute=0)          # 앱을 켠 순간 한 번 확인
            if on:
                if planned is not None and planned <= t + timedelta(minutes=config.RANK_CHECK_EVERY_MIN):
                    if rc.start_still_valid(planned, planned):
                        rc.save_snapshot(c, [_row(1)], planned)
                        taken.append(planned)
                    planned = None
                if planned is None and rc.is_due(c, t):
                    planned = rc.pick_start(t, rng)
                    if planned.hour not in on_hours:
                        planned = None           # 그 시각엔 꺼져 있다
            else:
                planned = None
            was_on = on
            t = t.replace(minute=0) + timedelta(hours=1, minutes=phase_min) if on else t + timedelta(hours=1)
        c.close()
    return taken


def test_evening_only_pc_never_skips_a_day():
    # 매일 18~23시만 켜는 PC 열흘 — 옛 규칙(마지막 + 24시간)은 지터가 쌓여 켜 둔 시간 밖으로 밀려 하루를 건너뛰었다
    for phase in (0, 20, 55):
        taken = _simulate(range(18, 24), 10, phase)
        days = sorted({t.date() for t in taken})
        span = (days[-1] - days[0]).days + 1
        assert len(days) == span and span >= 10, (phase, [str(t) for t in taken])


def test_always_on_pc_misses_no_day_and_doubles_rarely():
    for phase in (0, 10, 30, 49, 51, 59):
        taken = _simulate(range(24), 30, phase, start_hour=12)
        days = [t.date() for t in taken]
        span = (days[-1] - days[0]).days + 1
        assert len(set(days)) == span, (phase, "날짜가 빠졌다")
        doubles = len(days) - len(set(days))
        assert doubles <= 2, (phase, doubles, [str(t) for t in taken])   # 자정을 돌아 넘을 때만


def test_start_hours_do_not_pile_up_across_many_pcs():
    # 상시 PC 1,000대(첫 시각·확인 위상 무작위) 30일 뒤 — 시작 시(時)가 한 시각에 몰리지 않는다(3회차: 0시대로 몰렸다)
    rng = random.Random(7)
    last_hours = Counter()
    for i in range(1000):
        h = rng.randrange(24)
        taken = _simulate_fast(rng.randrange(60), h, 30)
        last_hours[taken.hour] += 1
    assert max(last_hours.values()) < 1000 * 0.12, last_hours


def _simulate_fast(phase, start_hour, days):
    """_simulate 의 상시 켬판 — DB 없이 is_due 규칙만(1,000대를 빠르게). 같은 규칙인지는 위 테스트가 DB 로 잰다."""
    rng = random.Random(phase * 100 + start_hour)
    last = datetime(2026, 10, 1, start_hour, 30)
    t = last.replace(minute=0) + timedelta(hours=1, minutes=phase)
    end = last + timedelta(days=days)
    while t < end:
        if t >= last.replace(minute=0) + timedelta(hours=config.RANK_COLLECT_INTERVAL_H - 1):
            p = rc.pick_start(t, rng)
            last = p
            t = p.replace(minute=0) + timedelta(hours=1, minutes=phase)
        else:
            t += timedelta(hours=1)
    return last


# ── 시작 시각 ───────────────────────────────────────────────────────────────

def test_start_window_constants():
    lo, hi = config.RANK_START_JITTER_MIN
    assert (lo, hi) == (5, 50), "본문(ROADMAP·주석)의 범위와 다르다"
    assert hi + 1.5 < 60, "끝에 시작해도 수집(약 1.5분)이 다음 정각 전에 끝나야 한다"


def test_pick_start_range():
    rng = random.Random(1)
    base = datetime(2026, 10, 4, 14, 0, 0)
    for now, lo, hi in [(base, base + timedelta(minutes=5), base + timedelta(minutes=50)),
                        (base + timedelta(minutes=30), base + timedelta(minutes=30), base + timedelta(minutes=50)),
                        (base + timedelta(minutes=55), base + timedelta(hours=1, minutes=5),
                         base + timedelta(hours=1, minutes=50))]:
        picks = [rc.pick_start(now, rng) for _ in range(300)]
        assert all(lo <= p <= hi for p in picks), (now, min(picks), max(picks))
        assert max(picks) - min(picks) > timedelta(minutes=10), "무작위가 아니다"


def test_late_timer_is_rechosen():
    planned = datetime(2026, 10, 4, 14, 20)
    assert rc.start_still_valid(planned, planned + timedelta(minutes=1))
    assert not rc.start_still_valid(planned, datetime(2026, 10, 4, 15, 21)), "절전 복귀로 늦게 터졌는데 시작했다"
    assert not rc.start_still_valid(planned, datetime(2026, 10, 4, 14, 55)), "창(50분)이 지났는데 시작했다"


# ── 잠금 ───────────────────────────────────────────────────────────────────

def test_lock_blocks_second_and_expires_without_heartbeat():
    with Env() as env:
        t = [NOW]
        a, b = rc.CollectLock(env.conn(), lambda: t[0]), rc.CollectLock(env.conn(), lambda: t[0])
        assert a.acquire() and not b.acquire()
        t[0] += timedelta(minutes=config.RANK_LOCK_STALE_MIN - 1)
        assert a.heartbeat()
        t[0] += timedelta(minutes=config.RANK_LOCK_STALE_MIN - 1)
        assert not b.acquire(), "하트비트가 살아 있는데 뺏었다"
        t[0] += timedelta(minutes=config.RANK_LOCK_STALE_MIN + 1)
        assert b.acquire(), "멈춘 잠금이 영영 남았다"
        assert not a.heartbeat()
        a.release()                                    # 남의 잠금은 안 지운다
        assert not rc.CollectLock(env.conn(), lambda: t[0]).acquire()
        b.release()
        assert rc.CollectLock(env.conn(), lambda: t[0]).acquire()


def test_collect_returns_locked_when_other_instance_collects():
    with Env() as env:
        other = rc.CollectLock(env.conn(), datetime.now)
        assert other.acquire()
        w = World(200)
        out = rc.collect(db_path=env.db, fetch=w, pages=10)
        assert out.kind == "locked" and not w.calls, out
        c = env.conn()
        assert rc.get_state(c).get("fail_count") in (None, "0")
        assert _count(c, "collect_lock") == 1, "남의 잠금을 지웠다"
        c.close()


# ── 시즌 경계 · 원본 보관 ────────────────────────────────────────────────────

def test_season_boundary():
    with Env() as env:
        c = env.conn()
        full = [_row(r) for r in range(1, 101)]
        rc.save_snapshot(c, full, NOW, ended_season=88)
        rc.save_snapshot(c, full[:60], NOW + timedelta(days=1))           # 절반 위 — 같은 시즌
        rc.save_snapshot(c, full[:20], NOW + timedelta(days=2))           # 절반 밑 — 새 시즌
        rc.save_snapshot(c, full, NOW + timedelta(days=3))                # 시즌표 모름 → 앞 값(88) 이어받아 같은 시즌
        rc.save_snapshot(c, full, NOW + timedelta(days=4), ended_season=89)  # 새로 끝난 시즌
        got = [(r["season_seq"], r["new_season"], r["ended_season"])
               for r in c.execute("SELECT * FROM snapshots ORDER BY id")]
        assert got == [(1, 0, 88), (1, 0, 88), (2, 1, 88), (2, 0, 88), (3, 1, 89)], got
        c.close()


def test_rank_trend_series():
    """랭킹 추이(P2) — 지금 시즌만 · 하루에 둘이면 그날 마지막 · 원본을 지워도(14일) 집계로 그린다 · 비율의 분모는 구간 인원."""
    assert rc.rank_trend_series(None).taken == []
    with Env() as env:
        c = env.conn()
        assert rc.rank_trend_series(c).taken == [], "스냅숏 없음"
        full = [_row(r, color="팀A" if r <= 150 else "", formation="4-4-2" if r % 2 else "4-3-3")
                for r in range(1, 1201)]
        rc.save_snapshot(c, full, NOW - timedelta(days=5))                 # 지난 시즌(아래에서 인원이 절반 밑으로)
        rc.save_snapshot(c, full[:500], NOW - timedelta(days=3))           # 새 시즌 첫 스냅숏
        rc.save_snapshot(c, [_row(r, elo=1.0) for r in range(1, 501)], NOW - timedelta(days=2, hours=5))  # 같은 날 앞 것 — 버린다
        later = [_row(r, elo=4000.0 - r, color="팀A" if r <= 50 else "팀B") for r in range(1, 501)]
        rc.save_snapshot(c, later, NOW - timedelta(days=2))
        rc.prune_raw(c, NOW + timedelta(days=30))                          # 원본은 다 지워져도
        t = rc.rank_trend_series(c)
        assert [x[:10] for x in t.taken] == [(NOW - timedelta(days=3)).date().isoformat(),
                                            (NOW - timedelta(days=2)).date().isoformat()], t.taken
        assert t.cuts[1] == [4999.0, 3999.0] and t.cuts[200] == [4800.0, 3800.0], t.cuts
        assert 1000 not in t.cuts, "목록이 1,000위까지 안 가면 그 컷은 없다(화면은 빈 계열을 건너뛴다)"
        top = t.shares[(200, "team_color")]
        assert top["팀A"] == [75.0, 25.0] and top[""][0] == 25.0 and top["팀B"] == [0.0, 75.0], top
        assert t.shares[(1000, "team_color")]["팀B"] == [0.0, 100.0], "201~1,000 구간 — 분모는 그 구간 인원(300)"
        assert t.value_avg[200] == [1000, 1000] and t.value_median[1000] == [1000, 1000]
        c.close()


def test_prune_keeps_aggregates():
    with Env() as env:
        c = env.conn()
        keep = config.RANK_RAW_KEEP_DAYS
        old = rc.save_snapshot(c, [_row(1), _row(2)], NOW - timedelta(days=keep, hours=1))
        new = rc.save_snapshot(c, [_row(1), _row(2)], NOW - timedelta(days=keep - 1))
        assert rc.prune_raw(c, NOW) == 2
        left = {r[0] for r in c.execute("SELECT DISTINCT snapshot_id FROM snapshot_rows")}
        assert left == {new}, left
        assert rc.cut_elo(c, old) == {1: 4999.0}, "집계까지 지웠다"
        assert _count(c, "snapshots") == 2
        c.close()


def test_collect_prunes_old_raw():
    with Env() as env:
        c = env.conn()
        old = rc.save_snapshot(c, [_row(1)], NOW - timedelta(days=config.RANK_RAW_KEEP_DAYS + 1))
        c.close()
        assert rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW).kind == "ok"
        c = env.conn()
        assert c.execute("SELECT COUNT(*) FROM snapshot_rows WHERE snapshot_id=?", (old,)).fetchone()[0] == 0
        assert _count(c, "snapshot_rows") == 40
        c.close()


def test_queries_use_indexes():
    # 규칙: 새 쿼리는 TEMP B-TREE(통째 정렬)가 없어야 한다 — 원본이 14일 × 1만 행
    with Env() as env:
        c = env.conn()
        for sql, args in [("SELECT row_count, season_seq, ended_season FROM snapshots "
                           "ORDER BY taken_at DESC, id DESC LIMIT 1", ()),
                          ("SELECT MAX(taken_at) AS t FROM snapshots", ()),
                          ("DELETE FROM snapshot_rows WHERE snapshot_id IN "
                           "(SELECT id FROM snapshots WHERE taken_at < ?)", ("x",)),
                          ("SELECT * FROM snapshot_rows WHERE profile_sn = ?", (1,))]:
            plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            assert "TEMP B-TREE" not in plan and "SCAN snapshot_rows" not in plan, (sql, plan)
        c.close()


# ── 3단계: 앱 연결 ─────────────────────────────────────────────────────────

def _snap(env, when, people=40, color_of=None, emblem_of=None):
    c = env.conn()
    rows = [_row(r, color=(color_of or {}).get(r, "")) for r in range(1, people + 1)]
    for r in rows:
        r.team_color_emblem = (emblem_of or {}).get(r.rank, "")
    sid = rc.save_snapshot(c, rows, when)
    c.close()
    return sid


def test_snapshot_colors_from_fresh_snapshot_only():
    with Env() as env:
        assert rc.snapshot_colors({"n1"}, env.db, NOW) is None, "스냅숏이 없는데 뭔가 줬다"
        assert not env.db.exists(), "없는 rank.db 를 만들었다(수집을 안 켠 사람에게 빈 파일)"
        _snap(env, NOW - timedelta(hours=25), color_of={1: "옛날"})
        assert rc.snapshot_colors({"n1"}, env.db, NOW) is None, "간격(24h) 지난 스냅숏을 썼다"
        _snap(env, NOW - timedelta(hours=2), color_of={1: "네덜란드", 3: "Spartan"},
              emblem_of={1: "countries/14.png", 2: "crests/x.png", 3: "crests/l130634.png"})
        colors, taken = rc.snapshot_colors({"n1", "n2", "n3", "밖"}, env.db, NOW)
        assert taken == NOW - timedelta(hours=2), taken
        # 팀컬러 없는 행은 구단가치·엠블럼 없음 · 목록에 없으면 '랭킹 밖' · 엠블럼은 옆 표에서 붙여 읽는다(2.2.1)
        assert colors == {"n1": ("네덜란드", 1000, "countries/14.png"), "n2": ("", None, ""),
                          "n3": ("Spartan", 1000, "crests/l130634.png"), "밖": ("", None, "")}, colors
        c = env.conn()
        assert _count(c, "snapshot_emblems") == 2, "팀컬러가 없는 행(n2)의 엠블럼까지 적었다"
        rc.prune_raw(c, NOW + timedelta(days=30))   # 원본이 지워진 스냅숏은 못 쓴다
        assert _count(c, "snapshot_emblems") == 0, "원본을 지우면서 엠블럼을 남겼다"
        c.close()
        assert rc.fresh_snapshot(env.db, NOW) is None


def test_snapshot_emblems_beside_rows_old_version_still_writes():
    # 옛 버전(2.1.1 이하)이 새 DB 로 수집해도 된다 — snapshot_rows 는 그대로 16칸(T13) · 옛 버전이 원본만 지우고 남긴
    # 엠블럼 줄은 snapshot_rows 에 없는 snapshot_id 로 지운다(snapshots 는 영구라 날짜로는 안 잡힌다)
    with Env() as env:
        sid = _snap(env, NOW - timedelta(hours=2), color_of={1: "X"}, emblem_of={1: "crests/a.png"})
        c = env.conn()
        assert len(c.execute("PRAGMA table_info(snapshot_rows)").fetchall()) == 16
        c.execute("INSERT INTO snapshot_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, 99, 99, "옛", None, None, None, 0, 0, 0, "", None, "", None, None, None))
        c.execute("DELETE FROM snapshot_rows WHERE snapshot_id = ?", (sid,))   # 옛 prune_raw — 엠블럼은 그대로
        c.commit()
        assert _count(c, "snapshot_emblems") == 1
        rc.prune_raw(c, NOW)   # 날짜 조건엔 안 걸리는 스냅숏
        assert _count(c, "snapshot_emblems") == 0, "옛 버전이 남긴 엠블럼 줄을 안 지웠다"
        c.close()


def test_list_read_is_shared_waiter_uses_collect_snapshot():
    # 수집이 읽는 동안 팀컬러가 오면 기다렸다 그 스냅숏을 쓴다 — 같은 500쪽을 두 번 읽지 않는다
    with Env() as env:
        world = World(200, sleep=0.02)
        res = {}
        t = threading.Thread(target=lambda: res.setdefault(
            "out", rc.collect(db_path=env.db, fetch=world, pages=10, now_fn=lambda: NOW)))
        t.start()
        for _ in range(100):
            if world.calls:
                break
            time.sleep(0.01)
        waited = []
        got, w = rc.acquire_list_read(on_wait=lambda: waited.append(True))
        try:
            assert got and w and waited == [True], (got, w, waited)
            assert res["out"].kind == "ok", "차례를 얻었는데 수집이 아직 안 끝났다"
            assert rc.snapshot_colors({"n1"}, env.db, NOW) is not None
        finally:
            rc.release_list_read()
        t.join(5)
        assert len(world.calls) == 10, world.calls


def test_collect_after_waiting_skips_when_snapshot_appeared():
    with Env() as env:
        got, _ = rc.acquire_list_read()
        assert got
        res = {}
        t = threading.Thread(target=lambda: res.setdefault(
            "out", rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW)))
        t.start()
        time.sleep(0.3)
        assert t.is_alive(), "목록 읽기 차례를 기다리지 않았다"
        _snap(env, NOW)   # 기다리는 사이 팀컬러 쪽이 스냅숏을 남겼다
        rc.release_list_read()
        t.join(5)
        assert res["out"].kind == "fresh", res["out"]
        c = env.conn()
        assert _count(c, "snapshots") == 1
        c.close()
        # 대조군 — 기다리지 않았으면(차례가 비어 있으면) 간격과 무관하게 그대로 읽는다(예약은 부르는 쪽 몫)
        assert rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW).kind == "ok"


def test_collect_waiting_can_be_cancelled():
    with Env() as env:
        got, _ = rc.acquire_list_read()
        try:
            ev = threading.Event()
            threading.Timer(0.2, ev.set).start()
            out = rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW, cancel=ev)
            assert out.kind == "cancelled", out
        finally:
            rc.release_list_read()


def _pages(world, n):
    return {p: world(p) for p in range(1, n + 1)}


def test_save_from_pages_only_when_collect_on_and_due():
    with Env(collect="0") as env:
        pages = _pages(World(40), 2)
        assert rc.save_from_pages(pages, NOW, pages=2, db_path=env.db, now_fn=lambda: NOW) is None
        assert not env.db.exists(), "수집이 꺼졌는데 rank.db 를 만들었다"
        env.write(collect="1")
        out = rc.save_from_pages(pages, NOW, pages=2, db_path=env.db, now_fn=lambda: NOW)
        assert out is not None and (out.kind, out.rows) == ("ok", 40), out
        c = env.conn()
        assert rc.get_state(c)["last_success_at"] == NOW.isoformat()
        assert _count(c, "collect_lock") == 0
        c.close()
        # 간격 안이면 또 저장하지 않는다
        assert rc.save_from_pages(pages, NOW, pages=2, db_path=env.db, now_fn=lambda: NOW) is None
        # 구조 변경은 실패로 센다(저장 안 함)
        later = NOW + timedelta(days=2)
        bad = _pages(World(40), 2)
        bad[2].rows[0].profile_sn = None
        out = rc.save_from_pages(bad, later, pages=2, db_path=env.db, now_fn=lambda: later)
        assert out.kind == "failed", out
        c = env.conn()
        assert _count(c, "snapshots") == 1 and rc.get_state(c)["fail_count"] == "1"
        c.close()
        # 빈 쪽은 일시적일 수 있어 세지 않는다(저장도 안 함) — 수집이 다시 받아 판정한다
        later2 = later + timedelta(days=2)
        empty = _pages(World(40), 2)
        empty[2].rows.clear()
        assert rc.save_from_pages(empty, later2, pages=2, db_path=env.db, now_fn=lambda: later2) is None
        c = env.conn()
        assert _count(c, "snapshots") == 1 and rc.get_state(c)["fail_count"] == "1", "빈 쪽을 실패로 셌다"
        c.close()


def test_set_enabled_at_writes_env_and_rank_db_copy():
    with Env(collect="0") as env:
        c = env.conn()
        rc.set_enabled(c, False, "blocked")       # D6 로 꺼진 사본 — .env 만 켜면 계속 disabled
        c.close()
        rc.set_enabled_at(True, db_path=env.db)
        assert config.RANK_COLLECT and "FIFA_RANK_COLLECT=1" in config.ENV_PATH.read_text(encoding="utf-8")
        c = env.conn()
        assert rc.get_state(c)["enabled"] == "1"
        c.close()
        out = rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW)
        assert out.kind == "ok", out
        rc.set_enabled_at(False, "user", db_path=env.db)
        c = env.conn()
        assert rc.get_state(c)["enabled"] == "0" and not config.RANK_COLLECT
        c.close()
    with Env(collect="0") as env:
        rc.set_enabled_at(False, "user", db_path=env.db)
        assert not env.db.exists(), "끄기만 했는데 rank.db 를 만들었다"


def test_web_data_off_turns_collect_off():
    with Env() as env:
        config.read_env_switches()
        assert config.RANK_COLLECT
        config.set_web_data(False)
        text = config.ENV_PATH.read_text(encoding="utf-8")
        assert "FIFA_RANK_COLLECT=0" in text and not config.RANK_COLLECT, text
        config.set_web_data(True)   # 웹 데이터를 다시 켜도 수집은 꺼진 채(다시 묻는다)
        assert config.read_env_switches() == (True, False)


def test_delete_db_and_pending_marker():
    with Env() as env:
        _snap(env, NOW)
        assert env.db.exists()
        assert rc.delete_db(env.db) is True
        assert not any(env.dir.glob("rank.db*")), list(env.dir.iterdir())
        # 다른 실행본이 열고 있다 — 지우지 못하면 표시를 남기고 다음에 켤 때 지운다
        _snap(env, NOW)
        orig = Path.unlink

        def busy(self, missing_ok=False):
            if self.name == "rank.db":
                raise PermissionError("열려 있음")
            return orig(self, missing_ok=missing_ok)
        Path.unlink = busy
        try:
            assert rc.delete_db(env.db) is False
        finally:
            Path.unlink = orig
        assert env.db.exists() and (env.dir / "rank.db.delete-pending").exists()
        assert rc.delete_pending(env.db) is True
        assert not env.db.exists() and not (env.dir / "rank.db.delete-pending").exists()
        assert rc.delete_pending(env.db) is False   # 표시가 없으면 아무것도 안 한다
        _snap(env, NOW)
        assert rc.delete_pending(env.db) is False and env.db.exists(), "표시 없이 지웠다"


def test_read_status_does_not_create_db():
    with Env() as env:
        assert rc.read_status(env.db) == {} and not env.db.exists()
        _snap(env, NOW, people=30)
        st = rc.read_status(env.db)
        assert (st["snapshots"], st["last_rows"], st["last_taken_at"]) == (1, 30, NOW.isoformat()), st


def test_elo_history_store():
    import store
    d = Path(tempfile.mkdtemp())
    try:
        c = store.open_db(d / "f.db")
        assert store.save_elo(c, "o1", 1500.0, 30, profile_sn=9, nickname="가", taken_at=NOW)
        assert not store.save_elo(c, "o1", 1500.0, 30, taken_at=NOW + timedelta(hours=1)), "같은 값을 또 적었다"
        assert store.save_elo(c, "o1", 1510.0, 28, taken_at=NOW + timedelta(hours=2))
        assert store.save_elo(c, "o2", 1400.0, None, taken_at=NOW)
        h = store.elo_history(c, "o1")
        assert [(r["elo"], r["rank"], r["source"]) for r in h] == [(1500.0, 30, "search"), (1510.0, 28, "search")], h
        assert h[0]["profile_sn"] == 9 and h[0]["nickname"] == "가"
        assert store.clear_elo(c, keep_ouid="o1") == 1 and len(store.elo_history(c, "o1")) == 2
        assert store.clear_elo(c) == 2 and store.elo_history(c, "o1") == []
        plan = " | ".join(r[3] for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT elo, rank FROM elo_history WHERE ouid = ? ORDER BY taken_at DESC LIMIT 1",
            ("o1",)))
        assert "TEMP B-TREE" not in plan and "SCAN" not in plan, plan
        c.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 9단계: ELO 따라가기 (1.3.1) ──────────────────────────────────────────────

def _fifa(env):
    import store
    return store.open_db(env.fifa)


def test_track_list_caps_at_five():
    import store
    with Env() as env:
        c = _fifa(env)
        for i in range(config.ELO_TRACK_MAX):
            store.track_add(c, f"o{i}", 900000 + i, f"n{i}")
        store.track_add(c, "o0", None, "새이름")            # 이미 있는 계정은 자리를 안 먹는다(번호는 그대로)
        try:
            store.track_add(c, "o9", 1, "x")
            raise AssertionError("6명째를 받았다")
        except store.TrackFull:
            pass
        lst = store.track_list(c)
        assert len(lst) == config.ELO_TRACK_MAX and lst[0]["nickname"] == "새이름" and lst[0]["profile_sn"] == 900000
        store.track_remove(c, "o1")
        store.track_add(c, "o9", 1, "x")                     # 하나 빼면 다시 들어간다
        c.close()


def test_sync_tracked_elo_only_tracked_and_idempotent():
    import store
    with Env() as env:
        _snap(env, NOW - timedelta(days=1), people=40)
        _snap(env, NOW, people=40)
        c = _fifa(env)
        store.track_add(c, "me", 900003, "n3")
        store.save_elo(c, "searched", 4000.0, 7, profile_sn=900007, taken_at=NOW)   # 검색만 한 계정
        c.close()
        r = env.conn()
        assert rc.sync_tracked_elo(r, env.fifa) == 2
        assert rc.sync_tracked_elo(r, env.fifa) == 0, "두 번째에 또 적었다(멱등 아님)"
        r.close()
        c = _fifa(env)
        h = store.elo_history(c, "me")
        assert [(x["rank"], x["elo"], x["source"]) for x in h] == [(3, 4997.0, "snapshot")] * 2, h
        assert [x["source"] for x in store.elo_history(c, "searched")] == ["search"], "목록 밖 계정까지 옮겼다"
        c.close()


def test_sync_survives_prune_and_new_track_gets_14_days():
    import store
    with Env() as env:
        for d in range(3):
            _snap(env, NOW - timedelta(days=2 - d), people=40)
        c = _fifa(env)
        store.track_add(c, "late", 900005, "n5")      # 스냅숏이 쌓인 뒤 넣은 계정 — 다음 회차에 지난 것까지
        c.close()
        r = env.conn()
        assert rc.sync_tracked_elo(r, env.fifa) == 3
        rc.prune_raw(r, NOW + timedelta(days=config.RANK_RAW_KEEP_DAYS + 5))
        assert _count(r, "snapshot_rows") == 0
        r.close()
        c = _fifa(env)
        assert len(store.elo_history(c, "late")) == 3, "원본을 지운 뒤 ELO 기록이 사라졌다(R11)"
        c.close()


def test_sync_finds_profile_by_nickname_when_missing():
    import store
    with Env() as env:
        _snap(env, NOW, people=40)
        c = _fifa(env)
        store.track_add(c, "me", None, "n12")
        c.close()
        r = env.conn()
        assert rc.sync_tracked_elo(r, env.fifa) == 1
        r.close()
        c = _fifa(env)
        assert store.track_list(c)[0]["profile_sn"] == 900012
        c.close()


def test_sync_skips_when_fifa_locked():
    import sqlite3
    import store
    with Env() as env:
        _snap(env, NOW, people=40)
        c = _fifa(env)
        store.track_add(c, "me", 900003, "n3")
        c.close()
        hold = sqlite3.connect(str(env.fifa))
        hold.execute("BEGIN EXCLUSIVE")
        saved = store.OPEN_TIMEOUT_S
        store.OPEN_TIMEOUT_S = 0.2
        try:
            r = env.conn()
            assert rc.sync_tracked_elo(r, env.fifa) == 0
            r.close()
            # 회차 전체도 성공으로 끝난다
            out = rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW + timedelta(days=2))
            assert out.kind == "ok", out
        finally:
            store.OPEN_TIMEOUT_S = saved
            hold.rollback()
            hold.close()


def test_sync_cancel_stops_between_accounts():
    import store
    with Env() as env:
        _snap(env, NOW, people=40)
        c = _fifa(env)
        store.track_add(c, "a", 900001, "n1")
        store.track_add(c, "b", 900002, "n2")
        c.close()
        ev = threading.Event()
        ev.set()
        r = env.conn()
        assert rc.sync_tracked_elo(r, env.fifa, cancel=ev) == 0
        r.close()
        # 끊은 뒤 바로 지우기가 잠금 없이 된다
        c = _fifa(env)
        store.clear_elo(c)
        c.close()


def test_both_entry_points_sync_tracked_elo():
    import store
    with Env() as env:
        c = _fifa(env)
        store.track_add(c, "me", 900003, "n3")
        c.close()
        assert rc.collect(db_path=env.db, fetch=World(40), pages=2, now_fn=lambda: NOW).kind == "ok"
        c = _fifa(env)
        assert len(store.elo_history(c, "me")) == 1, "수집 회차 끝에서 안 옮겼다"
        c.close()
        later = NOW + timedelta(days=1, hours=1)
        w = World(40)
        results = {p: w(p) for p in (1, 2)}
        out = rc.save_from_pages(results, later, pages=2, db_path=env.db, now_fn=lambda: later)
        assert out is not None and out.kind == "ok", out
        c = _fifa(env)
        assert len(store.elo_history(c, "me")) == 2, "팀컬러 목록 저장 끝에서 안 옮겼다"
        c.close()


def test_clear_elo_keeps_mine_including_snapshot_points_and_track():
    import store
    with Env() as env:
        _snap(env, NOW, people=40)
        c = _fifa(env)
        store.track_add(c, "me", 900003, "n3")
        store.track_add(c, "other", 900004, "n4")
        c.close()
        r = env.conn()
        rc.sync_tracked_elo(r, env.fifa)
        r.close()
        c = _fifa(env)
        store.clear_elo(c, keep_ouid="me")
        assert len(store.elo_history(c, "me")) == 1 and store.elo_history(c, "other") == []
        assert [t["ouid"] for t in store.track_list(c)] == ["me"], "지운 계정이 따라가기에 남았다"
        c.close()


def test_save_elo_compares_only_with_last_search_row():
    import store
    d = Path(tempfile.mkdtemp())
    try:
        c = store.open_db(d / "f.db")
        assert store.save_elo(c, "o", 4000.0, 9, taken_at=NOW)
        store.save_elo_snapshots(c, "o", [((NOW + timedelta(hours=1)).isoformat(), 4100.0, 5, 1, "n")])
        assert not store.save_elo(c, "o", 4000.0, 9, taken_at=NOW + timedelta(hours=2)), \
            "마지막 줄이 스냅숏이라 같은 검색 값을 또 적었다"
        # 같은 초 같은 출처는 DB 가 막는다 — 예외 없이 False
        assert not store.save_elo(c, "o", 4200.0, 3, taken_at=NOW)
        c.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_old_db_with_duplicate_elo_rows_opens():
    import sqlite3
    import store
    d = Path(tempfile.mkdtemp())
    try:
        p = d / "old.db"
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE elo_history (ouid TEXT NOT NULL, profile_sn INTEGER, nickname TEXT,"
                  " taken_at TEXT NOT NULL, elo REAL NOT NULL, rank INTEGER, source TEXT NOT NULL)")
        c.executemany("INSERT INTO elo_history VALUES (?,?,?,?,?,?,?)",
                      [("o", 1, "n", "2026-10-01T00:00:00", 1.0, 1, "search")] * 3)
        c.commit()
        c.close()
        c = store.open_db(p)
        assert len(store.elo_history(c, "o")) == 1
        c.close()
        c = store.open_db(p)          # 두 번째 열기는 정리를 다시 안 한다(인덱스가 있다)
        c.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_open_rank_db_ro_does_not_create_and_reads():
    with Env() as env:
        assert rc.open_rank_db_ro(env.db) is None and not env.db.exists()
        _snap(env, NOW - timedelta(days=1), people=1200)
        _snap(env, NOW, people=1200)
        r = rc.open_rank_db_ro(env.db)
        s = rc.cut_series(r)
        assert [e for _, e in s[200]] == [4800.0, 4800.0] and len(s[1000]) == 2, s
        assert rc.season_start_by_snapshots(r) == (NOW - timedelta(days=1)).isoformat()
        try:
            r.execute("DELETE FROM snapshots")
            raise AssertionError("읽기 전용인데 지워졌다")
        except Exception as e:
            assert "readonly" in str(e).lower() or "read-only" in str(e).lower() or "query_only" in str(e).lower(), e
        r.close()
        assert rc.delete_db(env.db), "읽기 연결을 닫았는데 못 지웠다"

# ── 2.3.1 랭커 메타 — 기준 시각 · 안 바뀐 데이터 · 따라잡기 처리 · 사람별 표 · 지우기 ────────────────

T0 = datetime(2026, 10, 10, 9, 0)
ENDS = ["2026-08-27", "2026-10-01"]       # 끝난 시즌 종료일 — 지금 시즌은 10-01 시작


def _p(sn, grade=2, elo=3000.0, games=10, value=1000, nick=None):
    return ranker.RankRow(rank=sn, profile_sn=sn, nickname=nick or f"p{sn}", elo=elo, grade=grade, win=games,
                          team_value=value)


def _msnap(c, when, rows, ref=None, ended=None):
    return rc.save_snapshot(c, rows, when, ended_season=ended, ref_time=ref)


def _proc(c, now=datetime(2026, 12, 1), ends=ENDS):
    return rc.process_meta(c, now, season_ends=ends)


def _rows_of(c, table):
    return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]


def _res(page, ref, date=DATE):
    return ranker.RankPageResult(page, [_row(page)], date, ref)


def test_parse_ref_time_iso():
    html = '<p class="rank_advice">※ 2026-10-07 07:00:00 기준 데이터로 현재와 다를 수 있으며</p>'
    assert ranker.parse_ref_time(html) == "2026-10-07T07:00:00"       # taken_at 과 같은 꼴(공백 → T)
    assert ranker.parse_ref_time("<p>점검 중</p>") is None and ranker.parse_ref_time("") is None


def test_ref_time_decides_straddle_when_every_page_has_it():
    ref = "2026-10-07T06:00:00"
    h13, h14 = "Sun, 04 Oct 2026 13:59:59 GMT", "Sun, 04 Oct 2026 14:00:01 GMT"
    try:
        rc.rows_from_pages({1: _res(1, ref), 2: _res(2, "2026-10-07T07:00:00")}, 2)
        raise AssertionError("같은 시(時)인데 기준 시각이 달라 두 데이터가 섞였다(R3)")
    except rc.Straddle as e:
        assert "갱신" in str(e)
    rows, _ = rc.rows_from_pages({1: _res(1, ref, h13), 2: _res(2, ref, h14)}, 2)   # 시는 걸쳤지만 데이터는 하나
    assert len(rows) == 2
    try:
        rc.rows_from_pages({1: _res(1, ref, h13), 2: _res(2, None, h14)}, 2)        # 하나라도 없으면 Date 의 시
        raise AssertionError("기준 시각이 빠진 쪽이 있는데 Date 규칙을 안 썼다")
    except rc.Straddle as e:
        assert "정각" in str(e)
    assert rc.round_ref({1: _res(1, ref), 2: _res(2, ref)}) == ref
    assert rc.round_ref({1: _res(1, ref), 2: _res(2, None)}) is None


def test_same_data_reads_one_page_and_is_not_counted():
    with Env() as env:
        w = World(200)
        w.ref = "2026-10-04T21:00:00"
        assert rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW).kind == "ok"
        later = NOW + timedelta(days=1)
        for i, when in enumerate((later, later + timedelta(hours=1))):
            w2 = World(200)
            w2.ref = w.ref
            out = rc.collect(db_path=env.db, fetch=w2, pages=10, now_fn=lambda: when)
            assert out.kind == "same" and w2.calls == [1], (out, w2.calls)   # 나머지 쪽은 요청 0(U3)
            st = rc.get_state(env.conn())
            assert st.get("fail_count") in (None, "0") and not st.get("retry_at"), "안 바뀐 데이터를 실패로 셌다"
            assert st["same_since"] == later.isoformat(), "처음 같다고 본 시각이 아니다"
            assert rc.is_due(env.conn(), when + timedelta(hours=1)), "다음 확인에 다시 보지 않는다"
        w3 = World(200)
        w3.ref = "2026-10-05T21:00:00"
        assert rc.collect(db_path=env.db, fetch=w3, pages=10, now_fn=lambda: later + timedelta(hours=2)).kind == "ok"
        c = env.conn()
        assert rc.get_state(c)["same_since"] == "", "저장했는데 '멈춰 있음'이 남았다"
        assert [(m["ref_time"], m["rank_gaps"]) for m in _rows_of(c, "snapshot_meta")] == \
            [(w.ref, 0), (w3.ref, 0)]
        # 팀컬러 목록이 읽은 500쪽 — 같은 판정으로 저장 안 함
        results = {p: w3(p) for p in range(1, 11)}
        n = _count(c, "snapshots")
        assert rc.save_from_pages(results, later + timedelta(days=3), pages=10, db_path=env.db,
                                  now_fn=lambda: later + timedelta(days=3)) is None
        assert _count(c, "snapshots") == n
        w3.ref = "2026-10-08T21:00:00"
        results = {p: w3(p) for p in range(1, 11)}
        out = rc.save_from_pages(results, later + timedelta(days=3), pages=10, db_path=env.db,
                                 now_fn=lambda: later + timedelta(days=3), season_ends=ENDS)
        assert out is not None and out.kind == "ok"
        assert rc.last_ref_time(c) == w3.ref and _count(c, "elo_hist") > 0, "save_from_pages 길이 meta·처리를 안 거쳤다"
        c.close()


def test_rank_gaps():
    assert rc.rank_gaps([_p(1), _p(2), _p(4), _p(5)]) == 1 and rc.rank_gaps([]) == 0


def test_process_meta_catches_up_old_version_snapshots_once():
    with Env() as env:
        c = env.conn()
        rows = [_p(i, elo=3000 + i * 10) for i in range(1, 21)]
        a = _msnap(c, T0, rows)
        _msnap(c, T0 + timedelta(days=1), rows)
        c.execute("DELETE FROM snapshot_meta")       # 옛 버전이 쓴 스냅숏 — meta 줄이 없다
        c.commit()
        out = _proc(c)
        assert (out["anon"], out["person"]) == (2, 2), out
        assert all(m["ref_time"] is None and m["anon_done"] == m["person_done"] == 1 for m in _rows_of(c, "snapshot_meta"))
        hist = dict(c.execute("SELECT bin, n FROM elo_hist WHERE snapshot_id = ?", (a,)).fetchall())
        assert hist == {3000: 4, 3050: 5, 3100: 5, 3150: 5, 3200: 1}, hist
        again = _proc(c)
        assert (again["anon"], again["person"]) == (0, 0), "처리한 스냅숏을 다시 셌다"
        c.close()


def test_process_meta_failure_redoes_only_that_snapshot():
    with Env() as env:
        c = env.conn()
        for i, g2 in enumerate((3, 3, 1)):
            _msnap(c, T0 + timedelta(days=i), [_p(1, grade=0), _p(2, grade=g2)])
        orig, calls = rc._person_apply, [0]

        def boom(*a):
            calls[0] += 1
            if calls[0] == 2:
                raise sqlite3.OperationalError("잠김")
            return orig(*a)
        rc._person_apply = boom
        try:
            _proc(c)
            raise AssertionError("심은 실패가 안 올라왔다 — 아래 검사가 공허")
        except sqlite3.OperationalError:
            pass
        finally:
            rc._person_apply = orig
        assert [r[0] for r in c.execute("SELECT person_done FROM snapshot_meta ORDER BY snapshot_id")] == [1, 0, 0]
        _proc(c)
        run = _rows_of(c, "run_open")
        assert [(r["profile_sn"], r["start_at"], r["last_at"]) for r in run] == \
            [(1, T0.isoformat(), (T0 + timedelta(days=2)).isoformat())], run
        assert len(_rows_of(c, "champ_first")) == 1 and _count(c, "elo_season") == 2, "실패한 스냅숏을 두 번 셌다"
        c.close()


def test_runs_follow_elapsed_time_not_calendar_days():
    with Env() as env:
        c = env.conn()
        times = [T0.replace(hour=22) + timedelta(hours=24.5 * i) for i in range(10)]   # 22시 시작 — 나흘째 자정을 넘긴다
        assert len({t.date() for t in times}) == 10 and (times[-1].date() - times[0].date()).days == 10, \
            "달력 날짜가 비는 간격이 아니다 — 아래 검사가 공허"
        rows = [_p(1, grade=0), _p(2)]
        for t in times:
            _msnap(c, t, rows)
        _proc(c)
        assert [(r["profile_sn"], r["start_at"], r["last_at"], r["censored_start"]) for r in _rows_of(c, "run_open")] \
            == [(1, times[0].isoformat(), times[-1].isoformat(), 1)], "24시간 30분 간격이 끊겼다"
        assert _count(c, "run_done") == 0
        gap = times[-1] + timedelta(hours=config.RANK_CONT_MAX_H + 1)
        _msnap(c, gap, rows)
        _proc(c)
        done = _rows_of(c, "run_done")
        assert [(round(d["hours"], 1), d["censored_start"], d["censored_end"]) for d in done] == [(24.5 * 9, 1, 1)], done
        assert [(r["start_at"], r["censored_start"]) for r in _rows_of(c, "run_open")] == [(gap.isoformat(), 1)]
        c.close()


def test_two_snapshots_same_day_count_length_once():
    with Env() as env:
        c = env.conn()
        for h in (0, 3, 24):
            _msnap(c, T0 + timedelta(hours=h), [_p(1, grade=0), _p(2)])
        _proc(c)
        r = _rows_of(c, "run_open")[0]
        assert rc._hours(r["start_at"], r["last_at"]) == 24, r
        c.close()


def test_super_champ_run_censoring():
    with Env() as env:
        c = env.conn()
        t1, t2, t3 = T0, T0 + timedelta(days=1), T0 + timedelta(days=2)
        _msnap(c, t1, [_p(1, grade=0), _p(2, grade=2), _p(3, grade=0)])
        _msnap(c, t2, [_p(1, grade=0), _p(2, grade=0), _p(3, grade=0)])
        _msnap(c, t3, [_p(1, grade=1), _p(2, grade=0), _p(9, grade=2)])   # 3 은 목록에서 빠졌다
        _proc(c)
        opened = {r["profile_sn"]: (r["start_at"], r["censored_start"]) for r in _rows_of(c, "run_open")}
        assert opened == {2: (t2.isoformat(), 0)}, opened                 # 앞에서 2 였다 — 시작을 안다
        done = sorted((d["hours"], d["censored_start"], d["censored_end"]) for d in _rows_of(c, "run_done"))
        assert done == [(24.0, 1, 0), (24.0, 1, 0)], done                 # 1·3 — 첫 처리에 이미 0 이라 시작 잘림
        c.close()


def test_first_champ_states():
    with Env() as env:
        c = env.conn()
        t1, t2, t3 = T0, T0 + timedelta(days=1), T0 + timedelta(days=2)
        _msnap(c, t1, [_p(1, grade=1), _p(2, grade=3, games=40, value=7), _p(3, grade=3), _p(4, grade=3)])
        _msnap(c, t2, [_p(1, grade=1), _p(2, grade=1, games=55, value=8), _p(5, grade=3), _p(4, grade=3)])
        _msnap(c, t3, [_p(1, grade=1), _p(2, grade=1), _p(3, grade=1), _p(4, grade=3)])
        _proc(c, ends=["2026-10-09"])                                  # 시즌 시작 10-09 — 10-10 첫 관측은 3일 안
        states = {r["profile_sn"]: r["state"] for r in _rows_of(c, "champ_watch")}
        assert states == {1: 0, 2: 2, 3: 3, 4: 1, 5: 1}, states        # 3 = 빠졌다 들어와 처음 닿은 때를 모른다
        first = [(f["games"], f["team_value"], f["at"], f["first_seen_late"]) for f in _rows_of(c, "champ_first")]
        assert first == [(55, 8, t2.isoformat(), 0)], first
        c.close()
    with Env() as env:
        c = env.conn()
        _msnap(c, T0, [_p(2, grade=3)])
        _msnap(c, T0 + timedelta(days=1), [_p(2, grade=1)])
        _proc(c)                                                       # 시즌 시작 10-01 — 열흘 뒤에 처음 봤다
        assert [f["first_seen_late"] for f in _rows_of(c, "champ_first")] == [1]
        c.close()


def test_season_peak_and_drawdown():
    with Env() as env:
        c = env.conn()
        for i, e in enumerate((3000.0, 3200.0, 3100.0, 3150.0)):
            _msnap(c, T0 + timedelta(days=i), [_p(1, elo=e)])
        _proc(c)
        r = _rows_of(c, "elo_season")[0]
        assert (r["peak"], r["peak_at"], r["max_dd"], r["last_elo"]) == \
            (3200.0, (T0 + timedelta(days=1)).isoformat(), 100.0, 3150.0), r
        c.close()


def test_person_tables_wait_for_consent_then_catch_up():
    with Env() as env:
        env.write(notice=config.META_NOTICE_VERSION - 1)              # 옛 동의자(5)
        c = env.conn()
        _msnap(c, T0, [_p(1, grade=0)])
        _msnap(c, T0 + timedelta(days=1), [_p(1, grade=0)])
        out = _proc(c)
        assert out["anon"] == 2 and out["person"] == 0
        assert all(_count(c, t) == 0 for t in rc.PERSON_TABLES), "동의 전에 사람별 표에 썼다"
        assert [m["person_done"] for m in _rows_of(c, "snapshot_meta")] == [0, 0], "동의 전에 깃발을 올렸다"
        env.write()                                                    # 동의
        assert _proc(c)["person"] == 2
        assert [r["censored_start"] for r in _rows_of(c, "run_open")] == [1], "따라잡기 첫 처리는 잘림으로 연다"
        c.close()


def test_turning_off_mid_processing_stops_and_next_run_purges():
    with Env() as env:
        c = env.conn()
        for i in range(3):
            _msnap(c, T0 + timedelta(days=i), [_p(1, grade=0)])
        orig = rc._person_one

        def once_then_off(*a):
            v = orig(*a)
            env.write(collect="0")
            return v
        rc._person_one = once_then_off
        try:
            assert _proc(c)["person"] == 1, "처리 중 .env 끔을 안 봤다"
        finally:
            rc._person_one = orig
        _proc(c)
        assert all(_count(c, t) == 0 for t in rc.PERSON_TABLES), "꺼졌는데 사람별 줄이 남았다"
        assert not rc.get_state(c).get("person_prev") and not rc.get_state(c).get("person_season")
        c.close()


def test_turning_collect_off_then_on_restarts_runs():
    with Env() as env:
        env.write(on_at="2026-10-10T08:00:00.000001")
        c = env.conn()
        _msnap(c, T0, [_p(1, grade=0)])
        _proc(c)
        assert rc.get_state(c)["person_epoch"] == "2026-10-10T08:00:00.000001"
        env.write(on_at="2026-10-10T20:00:00.000001")                  # 껐다 켰다 — 켠 시각이 바뀐다
        _msnap(c, T0 + timedelta(days=1), [_p(1, grade=0)])
        out = _proc(c)
        assert out["purged"], "껐다 켠 사이를 이어진 것으로 읽었다"
        r = _rows_of(c, "run_open")[0]
        assert (r["start_at"], r["censored_start"]) == ((T0 + timedelta(days=1)).isoformat(), 1), r
        c.close()
    with Env() as env:                                                 # 켠 시각이 둘 다 없음 = 같음(옛 버전에서 켬)
        c = env.conn()
        _msnap(c, T0, [_p(1, grade=0)])
        _proc(c)
        _msnap(c, T0 + timedelta(days=1), [_p(1, grade=0)])
        assert not _proc(c)["purged"], "옛 버전에서 켠 사람의 기록을 매번 지운다"
        assert _rows_of(c, "run_open")[0]["start_at"] == T0.isoformat()
        # ⚠ .env 를 손으로 껐다 확인 전에 다시 켜면 켠 시각이 그대로라 이어진 것으로 읽힌다(앱이 꺼짐을 본 적이 없다) — 받아들임
        env.write(collect="0")
        env.write(collect="1")
        assert not _proc(c)["purged"]
        c.close()


def test_season_verdict_compares_like_with_like():
    v = rc._season_verdict
    ps = {"seq": 9, "start": "2026-10-01", "at": "2026-10-02T09:00:00", "via": "init"}
    assert v(ps, 10, "2026-10-01") == ("roll", "seq"), "seq 10 > 9 를 숫자로 안 봤다"
    assert v(ps, 8, "2026-10-01")[0] == "older" and v(ps, 9, "2026-08-27")[0] == "older"
    assert v(ps, 9, "2026-11-20") == ("roll", "start") and v(ps, 10, "2026-11-20") == ("roll", "both")
    assert v({**ps, "start": None}, 9, "2026-10-01")[0] == "update", "시즌표를 처음 받는 날 넘기거나 건너뛰었다"
    assert v(ps, 9, None)[0] == "same" and v({**ps, "start": None}, 9, None)[0] == "same"
    seq_first = {**ps, "seq": 10, "at": "2026-11-20T09:00:00", "via": "seq"}
    assert v(seq_first, 10, "2026-11-20")[0] == "update" and v(seq_first, 10, "2026-11-21")[0] == "update"
    assert v(seq_first, 10, "2026-11-22")[0] == "roll", "늦은 신호 창(at + 1일)을 넘긴 시작일도 같은 넘김으로 봤다"
    start_first = {**ps, "start": "2026-11-20", "via": "start"}
    assert v(start_first, 10, "2026-11-20")[0] == "update", "시즌표로 넘긴 뒤 따라온 seq 로 한 번 더 넘겼다"


def test_season_rolls_by_table_without_seq_change():
    with Env() as env:
        c = env.conn()
        rows = [_p(1, grade=0, elo=3500), _p(2, elo=3100)]
        _msnap(c, T0, rows)
        _msnap(c, T0 + timedelta(days=1), rows)
        _proc(c)
        new_ends = ENDS + ["2026-11-20"]
        out = _proc(c, now=datetime(2026, 11, 21, 9), ends=new_ends)  # 스냅숏 없이 앱만 켬 — seq 는 그대로
        assert out["rolled"]
        assert all(_count(c, t) == 0 for t in rc.PERSON_TABLES), "새 시즌인데 옛 사람별 줄이 남았다"
        assert _count(c, "elo_season_done") == 2 and _rows_of(c, "run_done")[0]["censored_end"] == 1
        assert _count(c, "elo_hist") > 0
        _msnap(c, datetime(2026, 11, 21, 10), rows)
        _proc(c, now=datetime(2026, 11, 22), ends=new_ends)
        assert _count(c, "elo_season_done") == 2, "같은 넘김을 두 번 옮겼다"
        assert {r["season_at"] for r in _rows_of(c, "elo_season")} == {"2026-11-21T09:00:00"}
        c.close()


def test_no_season_table_keeps_accumulating():
    with Env() as env:
        c = env.conn()
        for i in range(3):
            _msnap(c, T0 + timedelta(days=i), [_p(1, grade=0)])
        _proc(c, ends=[])
        assert [m["person_done"] for m in _rows_of(c, "snapshot_meta")] == [1, 1, 1]
        _msnap(c, T0 + timedelta(days=3), [_p(1, grade=0)])
        _proc(c, ends=ENDS)                                            # 시즌표가 생겼다 — 시작일만 적고 이어 간다
        assert _count(c, "elo_season_done") == 0 and _count(c, "run_done") == 0
        assert _rows_of(c, "run_open")[0]["start_at"] == T0.isoformat()
        c.close()


def test_seq_roll_then_late_table_moves_once():
    for lag in (3, 10):
        with Env() as env:
            c = env.conn()
            full = [_p(i, grade=0 if i == 1 else 2) for i in range(1, 41)]
            _msnap(c, T0, full)
            _msnap(c, T0 + timedelta(days=1), full)
            _proc(c)
            t_roll = datetime(2026, 11, 20, 9)
            _msnap(c, t_roll, full[:10])                                # 행 수 급감 — seq 가 먼저 넘긴다(시즌표는 낡음)
            _proc(c, now=t_roll)
            assert _count(c, "elo_season_done") == 40, lag
            later = t_roll + timedelta(days=lag)
            _proc(c, now=later, ends=ENDS + ["2026-11-20"])            # 앱만 켬(새 스냅숏 없음) — 같은 늦은 신호
            assert _count(c, "elo_season_done") == 40, (lag, "앱 켤 때 늦은 시즌표로 한 번 더 넘겼다")
            _msnap(c, later, full[:10])
            _proc(c, now=later, ends=ENDS + ["2026-11-20"])            # 시즌표가 lag 일 늦게 따라왔다
            assert _count(c, "elo_season_done") == 40, (lag, "늦은 시즌표로 한 번 더 넘겼다")
            c.close()


def test_no_table_falls_back_to_70_days():
    with Env() as env:
        c = env.conn()
        _msnap(c, T0, [_p(1, grade=0)])
        _proc(c, ends=[])
        assert not _proc(c, now=T0 + timedelta(days=config.ELO_FALLBACK_DAYS - 1), ends=[])["rolled"]
        assert _proc(c, now=T0 + timedelta(days=config.ELO_FALLBACK_DAYS + 1), ends=[])["rolled"]
        assert all(_count(c, t) == 0 for t in rc.PERSON_TABLES)
        c.close()


def _secret_rows(n=200, tag="비밀닉"):
    return [ranker.RankRow(rank=i, profile_sn=i, nickname=f"{tag}{i:04d}", elo=3000.0) for i in range(1, n + 1)]


def _has_bytes(env, needle: bytes) -> list[str]:
    return [f.name for f in (env.db, env.db.with_name(env.db.name + "-wal")) if f.exists() and needle in f.read_bytes()]


def test_pruned_nicknames_leave_no_bytes_in_db_or_wal():
    with Env() as env:
        c = env.conn()
        _msnap(c, NOW - timedelta(days=config.RANK_RAW_KEEP_DAYS + 1), _secret_rows())
        _msnap(c, NOW, _secret_rows(tag="남는닉"))
        assert _has_bytes(env, "비밀닉".encode()), "심은 닉네임이 파일에 없다 — 아래 검사가 공허"
        rc.prune_raw(c, NOW)
        assert rc.checkpoint(c)
        c.close()
        assert not _has_bytes(env, "비밀닉".encode()), _has_bytes(env, "비밀닉".encode())
        assert _has_bytes(env, "남는닉".encode())
        c = env.conn()
        assert [m["raw_pruned"] for m in _rows_of(c, "snapshot_meta")] == [1, 0]
        assert not rc.vacuum_if_needed(c, NOW), "새 버전만 지운 DB 를 켤 때마다 VACUUM 한다"
        c.close()


def test_wal_is_emptied_after_prune_while_a_reader_is_open():
    """화면의 읽기 연결이 열려 있으면 마지막 연결이 닫혀도 SQLite 가 WAL 을 안 비운다 — 지운 닉네임이 -wal 프레임에 남는다.
    정리(maintenance)·회차 끝(collect) 둘 다 TRUNCATE 로 비우는지 — 연결 하나를 열어 둔 채로 잰다."""
    for path in ("maintenance", "collect"):
        with Env() as env:
            c = env.conn()
            _msnap(c, NOW - timedelta(days=config.RANK_RAW_KEEP_DAYS + 1), _secret_rows())
            c.close()
            reader = rc.open_rank_db_ro(env.db)                  # 쉬는 읽기 연결(트랜잭션 없음) — 화면이 잠깐 연 것
            reader.execute("SELECT COUNT(*) FROM snapshots").fetchone()
            try:
                assert _has_bytes(env, "비밀닉".encode()), "심은 닉네임이 없다 — 아래 검사가 공허"
                if path == "maintenance":
                    out = rc.maintenance(env.db, lambda: NOW, season_ends=ENDS)
                    assert out["pruned"] == 200, out
                else:
                    w = World(200)
                    w.ref = "2026-10-04T21:00:00"
                    assert rc.collect(db_path=env.db, fetch=w, pages=10, now_fn=lambda: NOW,
                                      season_ends=ENDS).kind == "ok"
                assert not _has_bytes(env, "비밀닉".encode()), (path, _has_bytes(env, "비밀닉".encode()))
            finally:
                reader.close()


def test_load_season_ends_refreshes_stale_table_only_with_web_data():
    import store
    from datetime import date as _d
    calls = []

    def fake():
        calls.append(1)
        return [seasons.Season(no=90, name="시즌 9", start=_d(2026, 10, 1), end=_d(2026, 11, 20))]
    keep = rc._fetch_seasons, config.WEB_DATA
    try:
        rc._fetch_seasons = fake
        with Env() as env:
            config.WEB_DATA = False
            assert rc.load_season_ends(env.fifa) == [] and calls == [], "웹 데이터가 꺼졌는데 넥슨에 물었다"
            config.WEB_DATA = True
            assert rc.load_season_ends(env.fifa) == ["2026-11-20"] and calls == [1], "낡은 시즌표를 다시 안 받았다"
            assert rc.load_season_ends(env.fifa) == ["2026-11-20"] and calls == [1], "새 시즌표를 또 받았다"
            f = store.open_db(env.fifa)
            f.execute("UPDATE seasons SET fetched_at = '2000-01-01T00:00:00'")
            f.commit()
            f.close()
            rc.load_season_ends(env.fifa)
            assert calls == [1, 1], "TTL 이 지났는데 다시 안 받았다"
    finally:
        rc._fetch_seasons, config.WEB_DATA = keep


def test_old_style_prune_is_vacuumed_at_startup():
    with Env() as env:
        c = env.conn()
        _msnap(c, NOW - timedelta(days=20), _secret_rows())
        _msnap(c, NOW, _secret_rows(tag="남는닉"))
        c.close()
        raw = sqlite3.connect(env.db)                                  # 옛 버전 — secure_delete 없이 지웠다
        raw.execute("PRAGMA secure_delete=OFF")
        raw.execute("DELETE FROM snapshot_rows WHERE snapshot_id = 1")
        raw.commit()
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw.close()
        assert _has_bytes(env, "비밀닉".encode()), "옛 방식 지우기가 바이트를 안 남긴다 — 아래 검사가 공허"
        out = rc.maintenance(env.db, lambda: NOW, vacuum=True, season_ends=ENDS)
        assert out.get("vacuumed"), out
        assert not _has_bytes(env, "비밀닉".encode()), _has_bytes(env, "비밀닉".encode())
        assert not rc.maintenance(env.db, lambda: NOW, vacuum=True, season_ends=ENDS).get("vacuumed"), "다시 VACUUM 했다"


def test_maintenance_never_creates_rank_db_and_prunes_when_off():
    with Env() as env:
        missing = env.dir / "없음.db"
        assert rc.maintenance(missing, lambda: NOW) == {"missing": True} and not missing.exists()
        assert rc.open_rank_db_existing(missing) is None and not missing.exists()
        c = env.conn()
        _msnap(c, NOW - timedelta(days=20), [_p(1, grade=0)])
        _msnap(c, NOW - timedelta(days=19), [_p(1, grade=0)])
        _msnap(c, NOW, [_p(1, grade=0)])
        _proc(c)
        assert _count(c, "run_open") == 1
        c.close()
        env.write(collect="0")                                         # 수집을 끔 — 회차는 더 안 돈다(R12)
        out = rc.maintenance(env.db, lambda: NOW, season_ends=ENDS)
        c = env.conn()
        assert out["purged"] and all(_count(c, t) == 0 for t in rc.PERSON_TABLES)
        assert out["pruned"] == 2 and _count(c, "snapshot_rows") == 1, "수집이 꺼졌다고 14일 지난 원본을 안 지웠다"
        assert _count(c, "collect_lock") == 0
        c.close()


def test_maintenance_waits_for_collect_lock():
    with Env() as env:
        c = env.conn()
        _msnap(c, T0, [_p(1, grade=0)])
        other = rc.CollectLock(env.conn(), datetime.now)
        assert other.acquire()
        assert rc.maintenance(env.db, lambda: NOW, season_ends=ENDS) == {"locked": True}
        assert _count(c, "elo_hist") == 0, "남이 잠갔는데 처리했다"
        other.release()
        assert rc.maintenance(env.db, lambda: NOW, season_ends=ENDS)["anon"] == 1
        c.close()


def test_meta_trend_compares_within_season():
    def rows(n_a):
        return [ranker.RankRow(rank=i, profile_sn=i, nickname=f"m{i}", elo=3000.0, team_color="A" if i <= n_a else "B",
                               formation="4-2-3-1" if i <= 3 else "4-4-2") for i in range(1, 101)]
    assert rc.meta_trend(None, 200).collecting
    with Env() as env:
        c = env.conn()
        _msnap(c, T0, rows(20))
        _msnap(c, T0 + timedelta(days=2), rows(30))
        assert rc.meta_trend(c, 200).collecting, "이틀 차로 비교했다(3일 미만은 표 없음)"
        _msnap(c, T0 + timedelta(days=4), rows(40))
        t = rc.meta_trend(c, 200)
        assert not t.collecting and round(t.days) == 4, t                # 7일 전이 없어 시즌 가장 옛것(4일 전)
        _msnap(c, T0 + timedelta(days=9), rows(50))
        t = rc.meta_trend(c, 200)
        assert (t.then_at, round(t.days)) == ((T0 + timedelta(days=2)).isoformat(), 7), t
        a = next(r for r in t.rows["team_color"] if r.key == "A")
        assert (a.now_n, a.then_n, round(a.diff_pp, 1), a.thin) == (50, 30, 20.0, False), a
        f = next(r for r in t.rows["formation"] if r.key == "4-2-3-1")
        assert f.thin and t.rows["team_color"][0].key == "A", "적은 줄 흐림 · 변화 내림차순"
        c.close()


def test_elo_hist_series_and_marker():
    with Env() as env:
        c = env.conn()
        assert not rc.elo_hist_series(c).bins
        rows = [_p(i, elo=3000.0 + i) for i in range(1, 251)]
        first = _msnap(c, T0, rows, ref="2026-10-10T08:00:00")
        _msnap(c, T0 + timedelta(days=7), [_p(i, elo=3100.0 + i) for i in range(1, 251)], ref="2026-10-17T08:00:00")
        _proc(c)
        h = rc.elo_hist_series(c, "7d")
        assert h.now_at == "2026-10-17T08:00:00" and h.cmp_at == "2026-10-10T08:00:00", h
        assert sum(h.bins.values()) == 250 and sum(h.cmp.values()) == 250 and 200 in h.cuts
        assert rc.elo_hist_series(c, "none").cmp == {}
        assert rc.elo_hist_series(c, "season").cmp_at == "2026-10-10T08:00:00"
        elo_rows = [{"taken_at": "2026-10-03T10:00:00", "elo": 2900.0},          # 시즌 전 · 2주 전
                    {"taken_at": "2026-10-16T21:30:00", "elo": 3333.0},          # 막대 데이터 시각 ±1일 안
                    {"taken_at": "2026-10-19T10:00:00", "elo": 3400.0}]          # 이틀 뒤
        assert rc.elo_marker(elo_rows, h) == (3333.0, "2026-10-16T21:30:00")
        assert rc.elo_marker(elo_rows[:1] + elo_rows[2:], h) is None, "몇 주 전·지난 시즌 값을 지금 분포 위에 그렸다"
        assert first == 1
        c.close()


def test_process_meta_budget_10k_rows():
    """⑦ — 1만 행 · 하루 1,156명 바뀜(R8) 스냅숏 30개, 하나 0.5초 이하."""
    rng = random.Random(3)
    with Env() as env:
        c = env.conn()
        people = list(range(1, 10001))
        nxt = 10001
        worst = 0.0
        for d in range(30):
            rows = [_p(sn, grade=rng.choice((0, 1, 1, 1, 2)), elo=3000.0 + rng.random() * 1500, nick=f"n{sn}")
                    for sn in people]
            for i, r in enumerate(rows, 1):
                r.rank = i
            _msnap(c, T0 + timedelta(days=d), rows)
            t = time.perf_counter()
            _proc(c)
            worst = max(worst, time.perf_counter() - t)
            for _ in range(1156):
                people[rng.randrange(len(people))] = nxt
                nxt += 1
            people = list(dict.fromkeys(people))
            while len(people) < 10000:
                people.append(nxt)
                nxt += 1
        print(f"       메타 처리 1만 행 최악 {worst:.2f}s · 사람별 {_count(c, 'elo_season'):,}줄")
        assert worst <= 0.5, worst
        c.close()


import sqlite3  # noqa: E402
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
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
