"""개발 PC 경기 수집(tools/dev_matches.py) 테스트 — 가짜 API·가짜 시계·가짜 rank.db, 네트워크 없이.

pytest 없이 `python tests/test_dev_matches.py`. 닉네임·ouid·경기 id 는 전부 지어낸 값이다.
근거는 ROADMAP "개발 PC 수집 확장" 2판 "검증" — 커서 ⓐ~ⓔ · 같은 id 동시 · 429·5xx·키 거절 · 서비스 키 없음 · 잠금 · fifa.db 무쓰기 · deadline.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import config
import dev_matches as M
import rankcollect as rc
import ranker
from nexon_api import NexonAPIError

NOW = datetime(2026, 10, 20, 10, 0, 0, tzinfo=M.KST)
FLOOR = datetime(2026, 10, 19, 0, 0, 0)          # NOW 의 전날 00:00 — 커서 없는 사람의 바닥(DEV_MATCH_FLOOR_DAYS 1)


# ── 가짜 ──────────────────────────────────────────────────────────────────────

def detail(mid: str, at: datetime, a: str, b: str | None, *, res=("승", "패"), lineup=(1, 2)) -> dict:
    def side(ouid, r, base):
        return {"ouid": ouid, "nickname": "x", "division": 800,
                "matchDetail": {"matchResult": r, "matchEndType": 0},
                "shoot": {"goalTotal": 2 if r == "승" else 0},
                "player": [{"spId": 100000000 + base * 100 + k, "spPosition": k, "spGrade": 5} for k in range(11)]
                + [{"spId": 999, "spPosition": M.SUB_POSITION, "spGrade": 1}]}
    infos = [side(a, res[0], lineup[0])] + ([side(b, res[1], lineup[1])] if b else [])
    return {"matchId": mid, "matchDate": at.strftime("%Y-%m-%dT%H:%M:%S"), "matchType": 52, "matchInfo": infos}


class World:
    """사람마다 경기 목록(최신 먼저)과 경기 상세. 요청을 센다."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.details: dict[str, dict] = {}
        self.nick: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.fail_detail: dict[str, Exception] = {}   # 경기 id → 이 예외(한 번 쓰면 지운다 — once 가 참이면)
        self.fail_once = True
        self.all_fail: Exception | None = None        # 모든 요청이 이 예외
        self.detail_delay = 0.0

    def game(self, mid, at, a, b=None, **kw):
        self.details[mid] = detail(mid, at, a, b, **kw)
        for o in (a, b):
            if o:
                self.lists.setdefault(o, [])
                self.lists[o].append(mid)
                self.lists[o].sort(key=lambda m: self.details[m]["matchDate"], reverse=True)

    def person(self, nick, ouid):
        self.nick[nick] = ouid
        self.lists.setdefault(ouid, [])

    def _hit(self, kind, arg):
        with self.lock:
            self.calls.append((kind, arg))
        if self.all_fail is not None:
            raise self.all_fail

    def count(self, kind=None):
        return sum(1 for k, _ in self.calls if kind is None or k == kind)


class FakeAPI:
    def __init__(self, w: World):
        self.w = w

    def get_ouid(self, nickname, attempts=3):
        self.w._hit("ouid", nickname)
        if nickname not in self.w.nick:
            raise NexonAPIError("요청 파라미터가 잘못됐습니다.", code="OPENAPI00004", status=400)
        return self.w.nick[nickname]

    def get_match_ids(self, ouid, matchtype=50, offset=0, limit=20, attempts=3):
        self.w._hit("list", ouid)
        assert attempts == 1 and matchtype == config.DEFAULT_MATCH_TYPE
        return list(self.w.lists.get(ouid, [])[offset:offset + limit])

    def get_match_detail_nocache(self, match_id, attempts=1):
        self.w._hit("detail", match_id)
        if self.w.detail_delay:
            time.sleep(self.w.detail_delay)
        e = self.w.fail_detail.get(match_id)
        if e is not None:
            if self.w.fail_once:
                del self.w.fail_detail[match_id]
            raise e
        return self.w.details[match_id]

    def get_match_detail(self, *a, **k):
        raise AssertionError(".cache 를 쓰는 상세를 부르면 안 된다")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@contextmanager
def tmp(**cfg):
    d = Path(tempfile.mkdtemp(prefix="devmatch_"))
    saved = {k: getattr(config, k) for k in cfg}
    saved_paths = (M.LOG_PATH, M.PROGRESS_DIR)
    for k, v in cfg.items():
        setattr(config, k, v)
    M.LOG_PATH, M.PROGRESS_DIR = d / "log.txt", d / "prog"
    try:
        yield d
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
        M.LOG_PATH, M.PROGRESS_DIR = saved_paths
        shutil.rmtree(d, ignore_errors=True)


def rank_db(d: Path, nicks: list[str]):
    conn = rc.open_rank_db(d / "rank.db")
    rows = [ranker.RankRow(rank=i + 1, profile_sn=700000 + i, nickname=n, elo=5000.0 - i, win=10)
            for i, n in enumerate(nicks)]
    rc.save_snapshot(conn, rows, NOW.replace(tzinfo=None) - timedelta(hours=2))
    return conn


def go(d, rconn, w, *, now=NOW, cap=None, clock=None, **kw):
    clock = clock or Clock()
    return M.run_day(d / "dm.db", rconn, lambda: FakeAPI(w), clock=clock, sleep=clock.sleep,
                     now_fn=lambda: now, fifa_db=d / "nofifa.db", cap_override=cap, **kw)


def games_in(d) -> set[str]:
    c = sqlite3.connect(str(d / "dm.db"))
    try:
        return {r[0] for r in c.execute("SELECT match_id FROM games")}
    finally:
        c.close()


def q1(d, sql, *args):
    c = sqlite3.connect(str(d / "dm.db"))
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


def h(n):   # NOW 에서 n 시간 전(naive — 경기 시각은 naive KST)
    return NOW.replace(tzinfo=None) - timedelta(hours=n)


# ── next_cap ──────────────────────────────────────────────────────────────────

def _day(day, cap, *, blocked=0, key=0, fin=True, hours=2):
    s = datetime.fromisoformat(day + "T09:00:00")
    return {"day": day, "cap": cap, "blocked": blocked, "key_rejected": key, "started_at": s.isoformat(),
            "finished_at": (s + timedelta(hours=hours)).isoformat() if fin else None}


def test_next_cap_table():
    # 배선은 기본값이 아닌 값으로 — 코드 기본값과 같으면 끊겨도 참이다
    with tmp(DEV_MATCH_CAP_START=111, DEV_MATCH_CAP_GROWTH=2.0, DEV_MATCH_CAP_MAX=1000,
             DEV_MATCH_DAY_HOURS=3, DEV_MATCH_BLOCK_HOLD_DAYS=2):
        assert M.next_cap([], "2026-10-20") == 111, "줄이 없으면 시작 상한"
        assert M.next_cap([_day("2026-10-19", 100)], "2026-10-20") == 200, "좋은 어제 → ×배율"
        assert M.next_cap([_day("2026-10-19", 600)], "2026-10-20") == 1000, "끝 상한에서 멈춘다"
        assert M.next_cap([_day("2026-10-19", 100, fin=False)], "2026-10-20") == 100, "못 끝냄 → 그대로"
        assert M.next_cap([_day("2026-10-19", 100, hours=4)], "2026-10-20") == 100, "제시간(3시간) 넘김 → 그대로"
        assert M.next_cap([_day("2026-10-19", 100, key=1)], "2026-10-20") == 100, "키 거절 → 그대로"
        assert M.next_cap([_day("2026-10-17", 100)], "2026-10-20") == 100, "어제 줄 없음 → 마지막 줄 상한"
        b = [_day("2026-10-15", 400, blocked=1, fin=False)]
        assert M.next_cap(b, "2026-10-16") == 200, "막힘 → 절반"
        assert M.next_cap(b + [_day("2026-10-16", 200)], "2026-10-17") == 200, "유지 중엔 좋은 날이어도 안 올린다"
        assert M.next_cap(b, "2026-10-17") == 200, "달력 날 — 꺼진 날도 센다(2일째)"
        assert M.next_cap(b + [_day("2026-10-17", 200)], "2026-10-18") == 400, "유지(2일) 뒤엔 다시 배율"
        assert M.next_cap(b + [_day("2026-10-20", 5)], "2026-10-16") == 200, "오늘 이후 줄은 안 본다"


# ── 요약 ──────────────────────────────────────────────────────────────────────

def test_summarize_and_lineup():
    d = detail("m1", h(1), "A", "B")
    g = M.summarize(d)
    assert g and g.match_id == "m1" and len(g.sides) == 2
    assert g.sides[0].result == 1 and g.sides[1].result == -1 and g.sides[0].goals == 2 and g.sides[0].division == 800
    assert len(g.sides[0].lineup) == 66, "선발 11명 × 6바이트 — 교체 명단은 뺀다"
    p = d["matchInfo"][0]["player"]
    assert M.lineup_body(list(reversed(p))) == g.sides[0].lineup, "순서가 달라도 같은 선발이면 같은 바이트"
    assert M.summarize({"matchId": "x"}) is None and M.summarize({"matchId": "x", "matchDate": "?", "matchInfo": []}) is None
    assert M.summarize("깨짐") is None, "dict 가 아니어도 그 경기만 None"
    wide = M.lineup_body([{"spId": 2 ** 33, "spPosition": 1, "spGrade": 1}])
    assert wide[:1] == b"\x01" and len(wide) % 2 == 1, "u32 를 넘으면 넓은 꼴"


# ── 커서 ──────────────────────────────────────────────────────────────────────

def test_cursor_a_opponent_stored_first_does_not_stop_scan():
    """ⓐ 상대가 먼저 넣은 경기가 목록 중간에 있어도 그 아래를 받는다."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.person("나", "B")
        w.game("a3", h(1), "A")
        w.game("ab", h(2), "A", "B")     # 나(B) 쪽이 먼저 넣는다
        w.game("a1", h(3), "A")
        r = rank_db(d, ["나", "가"])     # 나가 1위 — 먼저 처리
        res = go(d, r, w)
        assert res.finished and res.stage_done == "done", res
        assert {"a3", "ab", "a1"} <= games_in(d), f"ab 아래 a1 이 빠지면 안 된다: {games_in(d)}"
        assert w.count("detail") == 3, f"ab 는 한 번만: {w.calls}"


def test_cursor_b_stage1_then_stage2_no_gap_and_c_cap_resume():
    """ⓑ 단 1 뒤 단 2 가 빠짐 없이 · ⓒ 상한에 걸려 중간에서 멈추면 커서가 안 오르고 다음 실행이 채운다."""
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_PAGE=2) as d:
        w = World()
        w.person("가", "A")
        for i in range(7):
            w.game(f"g{i}", h(1 + i), "A")
        r = rank_db(d, ["가"])
        # 단 1: ouid 1 + 목록 1 + 상세 1 = 3 · 단 2: 첫 쪽은 다시 씀 → g1 상세 · 다음 쪽 목록 · g2 상세 → 상한 6 에서 멈춤
        res = go(d, r, w, cap=6)
        assert res.reason == "cap" and res.finished, res
        assert q1(d, "SELECT COUNT(*) FROM cursor")[0] == 0, "중간에 끊긴 사람의 커서는 그대로(없음)"
        assert games_in(d) == {"g0", "g1", "g2"}, games_in(d)
        assert w.count("list") == 2, f"단 1 의 첫 쪽을 단 2 가 다시 쓴다(L2): {w.calls}"
        res2 = go(d, r, w, cap=100, force=True)
        assert res2.stage_done == "done" and not res2.reason, res2
        assert games_in(d) == {f"g{i}" for i in range(7)}, f"빈칸 없이: {games_in(d)}"
        assert q1(d, "SELECT top_id FROM cursor")[0] == "g0", "다 훑은 뒤 맨 앞으로"
        # 다음 날: 새 경기 하나만 받고 커서에서 멈춘다
        w.game("g_new", h(0.5), "A")
        before = w.count("detail")
        res3 = go(d, r, w, now=NOW + timedelta(days=1))
        assert games_in(d) >= {"g_new"} and w.count("detail") - before == 1, w.calls[-6:]
        assert q1(d, "SELECT top_id FROM cursor")[0] == "g_new"


def test_cursor_d_floor_for_new_person():
    """ⓓ 커서 없는 사람은 바닥(실행 날 전날 00:00)보다 옛 경기에서 멈춘다."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("new", h(2), "A")
        w.game("y", FLOOR + timedelta(hours=1), "A")
        w.game("old1", FLOOR - timedelta(hours=1), "A")
        w.game("old2", FLOOR - timedelta(hours=2), "A")
        r = rank_db(d, ["가"])
        go(d, r, w)
        got = games_in(d)
        assert {"new", "y", "old1"} <= got and "old2" not in got, f"바닥 아래 첫 경기에서 멈춘다: {got}"
        assert q1(d, "SELECT top_id FROM cursor")[0] == "new"


def test_cursor_e_missing_then_retry():
    """ⓔ 상세 실패 → missing(커서는 넘어간다) → 다음 실행에서 다시."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("m2", h(1), "A")
        w.game("m1", h(2), "A")
        w.fail_detail["m1"] = NexonAPIError("x", code="OPENAPI00009", status=404)
        w.fail_once = False
        r = rank_db(d, ["가"])
        go(d, r, w)
        assert "m1" not in games_in(d) and q1(d, "SELECT tries FROM missing WHERE match_id='m1'")[0] == 1
        assert q1(d, "SELECT top_id FROM cursor")[0] == "m2", "커서는 넘어간다"
        w.fail_detail.clear()
        go(d, r, w, now=NOW + timedelta(days=1))
        assert "m1" in games_in(d), "다음 실행이 missing 부터 다시"
        assert q1(d, "SELECT COUNT(*) FROM missing")[0] == 0


def test_missing_gives_up_after_tries():
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_MISSING_TRIES=2) as d:
        w = World()
        w.person("가", "A")
        w.game("m1", h(1), "A")
        w.fail_detail["m1"] = NexonAPIError("x", code="OPENAPI00009", status=404)
        w.fail_once = False
        r = rank_db(d, ["가"])
        for k in range(4):
            go(d, r, w, now=NOW + timedelta(days=k))
        assert w.count("detail") == 2, f"단 1 에서 1번 + 다음 날 다시 받기 1번 — 단 2·포기 뒤에는 안 묻는다: {w.calls}"
        assert q1(d, "SELECT tries FROM missing")[0] == 2


def test_gap_when_api_ends_before_cursor():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("m1", h(30), "A")
        r = rank_db(d, ["가"])
        go(d, r, w, now=NOW - timedelta(days=1))
        assert q1(d, "SELECT top_id FROM cursor")[0] == "m1"
        w.lists["A"] = []                       # 오픈API 가 옛 커서를 더는 안 준다(한 달 넘게 못 받음)
        w.game("m3", h(1), "A")
        w.game("m2", h(2), "A")
        go(d, r, w)
        assert q1(d, "SELECT top_id FROM cursor")[0] == "m3"
        gap = q1(d, "SELECT gap_at FROM owners WHERE profile_sn IS NOT NULL")[0]
        assert gap == M.kst_epoch(h(2).isoformat()), f"가장 옛 본 경기 시각: {gap}"


# ── 동시 · 멈춤 ───────────────────────────────────────────────────────────────

def test_same_match_two_workers_detail_once():
    with tmp(DEV_MATCH_WORKERS=4) as d:
        w = World()
        nicks = [f"p{i}" for i in range(4)]
        for i, n in enumerate(nicks):
            w.person(n, f"O{i}")
        w.game("shared", h(1), "O0", "O1")
        w.game("shared2", h(1.5), "O2", "O3")
        w.detail_delay = 0.2                      # 일부러 늦춰 겹치게
        r = rank_db(d, nicks)
        go(d, r, w)
        n = [m for k, m in w.calls if k == "detail"]
        assert n.count("shared") == 1 and n.count("shared2") == 1, f"상세는 한 번: {n}"
        assert games_in(d) == {"shared", "shared2"}


def test_429_twice_blocks_day():
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_429_WAIT_S=77) as d:
        w = World()
        w.person("가", "A")
        w.all_fail = NexonAPIError("x", code="OPENAPI00007", status=429)
        r = rank_db(d, ["가"])
        c = Clock()
        res = go(d, r, w, clock=c)
        assert res.blocked and res.reason == "blocked" and not res.finished, res
        assert w.count() == 2 and c.t >= 1077, f"쉬고(77초 — 설정 배선) 한 번 더: {w.count()} · {c.t}"
        assert q1(d, "SELECT blocked, stop FROM days") == (1, "blocked")
        w.all_fail = None
        res2 = go(d, r, w)
        assert res2.skipped and w.count() == 2, "그날 다시 안 돈다"


def test_403_blocks_immediately():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.all_fail = NexonAPIError("x", status=403)
        res = go(d, rank_db(d, ["가"]), w)
        assert res.blocked and w.count() == 1


def test_5xx_streak_stops_but_not_blocked():
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_FAIL_STREAK=5) as d:
        w = World()
        nicks = [f"p{i}" for i in range(10)]
        for i, n in enumerate(nicks):
            w.person(n, f"O{i}")
        w.all_fail = NexonAPIError("x", code="OPENAPI00001", status=500)
        res = go(d, rank_db(d, nicks), w)
        assert res.reason == "outage" and not res.blocked and w.count() == 5, (res, w.count())
        assert q1(d, "SELECT blocked, stop FROM days") == (0, "outage")


def test_key_rejected_stops_after_one_request():
    with tmp(DEV_MATCH_WORKERS=4) as d:
        w = World()
        nicks = [f"p{i}" for i in range(20)]
        for i, n in enumerate(nicks):
            w.person(n, f"O{i}")
        w.all_fail = NexonAPIError("x", code="OPENAPI00005", status=400)
        w.detail_delay = 0
        res = go(d, rank_db(d, nicks), w)
        assert res.key_rejected and not res.blocked, res
        assert w.count() <= 4, f"동시 4명이 이미 낸 것 말고는 안 보낸다: {w.count()}"
        with tmp(DEV_MATCH_WORKERS=1) as d2:
            w2 = World()
            w2.person("가", "A")
            w2.all_fail = NexonAPIError("x", code="OPENAPI00005", status=400)
            go(d2, rank_db(d2, ["가", "나", "다"]), w2)
            assert w2.count() == 1, f"요청 1번 뒤 멈춘다(M1): {w2.calls}"


def test_gone_nickname_skips_only_that_person():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("m1", h(1), "A")
        res = go(d, rank_db(d, ["없는사람", "가"]), w)
        assert "m1" in games_in(d) and res.stage_done == "done"
        assert [a for k, a in w.calls if k == "ouid"].count("없는사람") == 1, "단 2 가 다시 묻지 않는다"


def test_broken_detail_does_not_block_others():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("m2", h(1), "A")
        w.game("m1", h(2), "A")
        w.details["m2"] = {"matchId": "m2", "matchInfo": "깨짐"}
        go(d, rank_db(d, ["가"]), w)
        assert "m1" in games_in(d) and q1(d, "SELECT tries FROM missing WHERE match_id='m2'")[0] == 1


def test_deadline_finishes_current_person_then_stops():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        for i in range(3):
            w.person(f"p{i}", f"O{i}")
            w.game(f"m{i}a", h(1), f"O{i}")
            w.game(f"m{i}b", h(2), f"O{i}")
        c = Clock()
        orig = FakeAPI.get_match_detail_nocache

        def slow(self, mid, attempts=1):
            c.t += 100   # 상세 하나에 100초 — 첫 사람 도중에 deadline 을 넘긴다
            return orig(self, mid, attempts)
        FakeAPI.get_match_detail_nocache = slow
        try:
            res = go(d, rank_db(d, ["p0", "p1", "p2"]), w, clock=c, deadline=c.t + 50)
        finally:
            FakeAPI.get_match_detail_nocache = orig
        assert res.reason == "deadline" and not res.finished, res
        assert q1(d, "SELECT finished_at FROM days")[0] is None
        assert games_in(d) == {"m0a"}, f"돌던 사람(단 1 의 p0)은 마치고 새 사람은 안 받는다: {games_in(d)}"


# ── 키 · 잠금 · fifa.db ───────────────────────────────────────────────────────

def test_no_service_key_no_requests():
    with tmp() as d:
        from dev_archive import DEV_KEY_VAR
        saved_env, saved_key = os.environ.pop(DEV_KEY_VAR, None), config.API_KEY
        config.API_KEY = "앱키-있음"
        made = []
        try:
            rc_ = M.run(db_path=d / "dm.db", lock_path=d / "l", fifa_db=d / "f.db",
                        rank_open=lambda: made.append("rank") or None, api_cls=lambda k: made.append(k))
        finally:
            config.API_KEY = saved_key
            if saved_env is not None:
                os.environ[DEV_KEY_VAR] = saved_env
        assert rc_ == 0 and made == [], f"서비스 키가 없으면 앱 키로 넘어가지 않는다(H4): {made}"
        assert "없음 — 건너뜀" in (d / "log.txt").read_text(encoding="utf-8")


def test_run_with_key_uses_service_key():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        from dev_archive import DEV_KEY_VAR
        saved = os.environ.get(DEV_KEY_VAR)
        os.environ[DEV_KEY_VAR] = "서비스키"
        w = World()
        w.person("가", "A")
        w.game("m1", h(1), "A")
        keys = []
        r = rank_db(d, ["가"])
        try:
            rcode = M.run(db_path=d / "dm.db", lock_path=d / "l", fifa_db=d / "f.db", rank_open=lambda: r,
                          api_cls=lambda k: keys.append(k) or FakeAPI(w), force=True)
        finally:
            if saved is None:
                os.environ.pop(DEV_KEY_VAR, None)
            else:
                os.environ[DEV_KEY_VAR] = saved
        assert rcode == 0 and keys and set(keys) == {"서비스키"}, keys
        assert "m1" in games_in(d)
        assert not list((d / "prog").glob("*")), "진행 줄은 끝나면 지운다"


_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/tools")
import dev_matches as M
f = M.acquire_lock(sys.argv[2])
print("got" if f else "none", flush=True)
if sys.argv[3] == "hold":
    time.sleep(30)
import os; os._exit(0)
"""


def test_lock_live_and_dead():
    with tmp() as d:
        lp = str(d / "x.lock")
        live = subprocess.Popen([sys.executable, "-c", _CHILD, ROOT, lp, "hold"], stdout=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        try:
            assert live.stdout.readline().strip() == "got"
            assert M.acquire_lock(lp) is None, "산 잠금이면 두 번째 실행은 아무것도 안 한다"
        finally:
            live.kill()
            live.wait()
        f = M.acquire_lock(lp)
        assert f is not None, "잠근 채 죽은 프로세스의 잠금은 넘겨받는다(M7)"
        M.release_lock(f)
        out = subprocess.run([sys.executable, "-c", _CHILD, ROOT, lp, "exit"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace").stdout.strip()
        assert out == "got", "놓으면 다음 실행이 잡는다"


def test_fifa_db_untouched_and_pick_429_skips():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        f = d / "fifa.db"
        c = sqlite3.connect(str(f))
        c.execute("CREATE TABLE api_budget (day TEXT, kind TEXT, attempts INTEGER, hit_429 INTEGER)")
        c.commit()
        c.close()
        before = f.read_bytes()
        w = World()
        w.person("가", "A")
        w.game("m1", h(1), "A")
        r = rank_db(d, ["가"])
        res = M.run_day(d / "dm.db", r, lambda: FakeAPI(w), now_fn=lambda: NOW, fifa_db=f)
        assert "m1" in games_in(d) and not res.skipped
        assert f.read_bytes() == before, "fifa.db 바이트가 그대로"
        assert not Path(str(f) + "-wal").exists() and not Path(str(f) + "-shm").exists(), "-wal·-shm 도 안 생긴다"
        c = sqlite3.connect(str(f))
        c.execute("INSERT INTO api_budget VALUES (?, ?, 0, 1)", (NOW.date().isoformat(), M.PICK_DEV_KIND))
        c.commit()
        c.close()
        n = w.count()
        res2 = M.run_day(d / "dm.db", r, lambda: FakeAPI(w), now_fn=lambda: NOW + timedelta(days=0), fifa_db=f,
                         force=True)
        assert res2.skipped and w.count() == n, "③ 이 오늘 429 면 시작 안 함"


def test_finished_day_skips_and_used_persists():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("m1", h(1), "A")
        r = rank_db(d, ["가"])
        res = go(d, r, w)
        assert res.finished and q1(d, "SELECT used FROM days")[0] == res.used == w.count()
        n = w.count()
        res2 = go(d, r, w)
        assert res2.skipped and w.count() == n, "끝난 날은 바로 나간다"


def test_lineups_stored_once_and_missing_side_null():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.game("m2", h(1), "A", "B")
        w.game("m1", h(2), "A", "B")          # 같은 선발 두 경기
        w.details["m1"]["matchInfo"][1]["player"] = []   # 상대 선발이 빈 경기
        go(d, rank_db(d, ["가"]), w)
        assert q1(d, "SELECT COUNT(*) FROM lineups")[0] == 2, "같은 선발은 한 벌만(내 것 · 상대 것)"
        assert q1(d, "SELECT l1 IS NOT NULL, l2 IS NULL FROM games WHERE match_id='m1'") == (1, 1)
        assert q1(d, "SELECT l1 FROM games WHERE match_id='m1'") == q1(d, "SELECT l1 FROM games WHERE match_id='m2'")


def test_person_without_games_gets_cursor_from_now():
    """경기가 없던 사람도 커서(top_id NULL · 그 시각)를 세운다 — 다음엔 그 시각 뒤 경기만, 바닥으로 되돌아가지 않는다."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        r = rank_db(d, ["가"])
        go(d, r, w)
        assert q1(d, "SELECT top_id, top_at FROM cursor") == (None, int(NOW.timestamp()))
        later = NOW + timedelta(days=3)
        w.game("new", later.replace(tzinfo=None) - timedelta(hours=1), "A")
        w.game("before", NOW.replace(tzinfo=None) - timedelta(hours=1), "A")   # 커서 시각보다 옛것 — 바닥(전날)보다는 새것
        w.game("before2", NOW.replace(tzinfo=None) - timedelta(hours=2), "A")
        go(d, r, w, now=later)
        assert "new" in games_in(d) and "before2" not in games_in(d), f"커서 시각에서 멈춘다: {games_in(d)}"


def test_unexpected_error_counts_and_skips_person():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("가", "A")
        w.person("나", "B")
        w.game("m1", h(1), "B")
        orig = FakeAPI.get_ouid

        def boom(self, nickname, attempts=3):
            if nickname == "가":
                raise NexonAPIError("x", code="NETWORK")   # 네트워크 실패 — 그 사람만 이번에 건너뜀
            return orig(self, nickname, attempts)
        FakeAPI.get_ouid = boom
        try:
            res = go(d, rank_db(d, ["가", "나"]), w)
        finally:
            FakeAPI.get_ouid = orig
        assert res.errors == 2 and res.notes and "m1" in games_in(d), res


def test_run_log_and_status_cli():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        import io
        from contextlib import redirect_stdout
        from dev_archive import DEV_KEY_VAR
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert M.status(d / "없음.db") == 0
        assert "아직 없음" in buf.getvalue()
        w = World()
        w.person("가", "A")
        w.game("m1", h(1), "A")
        r = rank_db(d, ["가"])
        go(d, r, w)
        saved_env, saved_db = os.environ.get(DEV_KEY_VAR), M.DB_PATH
        os.environ[DEV_KEY_VAR] = "k"
        M.DB_PATH = d / "dm.db"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert M.main(["--status"]) == 0
                for _ in range(2):   # run 은 실제 시계 — 첫 번째가 오늘을 끝내고 두 번째가 건너뛴다
                    M.run(db_path=d / "dm.db", lock_path=d / "l", fifa_db=d / "f.db", rank_open=lambda: rank_db(d, ["가"]),
                          api_cls=lambda k: FakeAPI(w))
        finally:
            M.DB_PATH = saved_db
            if saved_env is None:
                os.environ.pop(DEV_KEY_VAR, None)
            else:
                os.environ[DEV_KEY_VAR] = saved_env
        out = buf.getvalue()
        assert "경기 1" in out and "날" in out, out
        assert "끝남 — 건너뜀" in (d / "log.txt").read_text(encoding="utf-8"), "끝난 날은 로그에 건너뜀"


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
