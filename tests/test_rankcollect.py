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
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import ranker
import rankcollect as rc

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
            return ranker.RankPageResult(page, rows, self.dates.get(page, self.date))
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
        config.ENV_PATH = self.dir / ".env"
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        self.write(web, collect)

    def write(self, web="1", collect="1"):
        config.ENV_PATH.write_text(f"{config.WEB_DATA_VAR}={web}\n{config.RANK_COLLECT_VAR}={collect}\n",
                                   encoding="utf-8")

    def conn(self):
        return rc.open_rank_db(self.db)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        (config.ENV_PATH, config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, rc_env, web_env) = self.saved
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
    with Env() as env:
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION - 1
        w = World(200)
        assert rc.collect(db_path=env.db, fetch=w, pages=10).kind == "disabled" and not w.calls


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
        assert not rc.is_due(c, NOW + timedelta(hours=h - 1))
        assert rc.is_due(c, NOW + timedelta(hours=h))
        rc.record_result(c, "failed", NOW + timedelta(hours=h))
        assert not rc.is_due(c, NOW + timedelta(hours=h, minutes=59))
        assert rc.is_due(c, NOW + timedelta(hours=h + 1))
        c.close()


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

def _snap(env, when, people=40, color_of=None):
    c = env.conn()
    rows = [_row(r, color=(color_of or {}).get(r, "")) for r in range(1, people + 1)]
    sid = rc.save_snapshot(c, rows, when)
    c.close()
    return sid


def test_snapshot_colors_from_fresh_snapshot_only():
    with Env() as env:
        assert rc.snapshot_colors({"n1"}, env.db, NOW) is None, "스냅숏이 없는데 뭔가 줬다"
        assert not env.db.exists(), "없는 rank.db 를 만들었다(수집을 안 켠 사람에게 빈 파일)"
        _snap(env, NOW - timedelta(hours=25), color_of={1: "옛날"})
        assert rc.snapshot_colors({"n1"}, env.db, NOW) is None, "간격(24h) 지난 스냅숏을 썼다"
        _snap(env, NOW - timedelta(hours=2), color_of={1: "네덜란드"})
        colors, taken = rc.snapshot_colors({"n1", "n2", "밖"}, env.db, NOW)
        assert taken == NOW - timedelta(hours=2), taken
        # 팀컬러 없는 행은 구단가치 None · 목록에 없으면 '랭킹 밖'
        assert colors == {"n1": ("네덜란드", 1000), "n2": ("", None), "밖": ("", None)}, colors
        c = env.conn()
        rc.prune_raw(c, NOW + timedelta(days=30))   # 원본이 지워진 스냅숏은 못 쓴다
        c.close()
        assert rc.fresh_snapshot(env.db, NOW) is None


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
