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
    return {"matchId": mid, "matchDate": _raw(at), "matchType": 52, "matchInfo": infos}


def _raw(at: datetime) -> str:
    """at 은 KST 벽시계 — 넥슨 원문은 UTC 라 글자 그대로 9시간 뺀다(63 · 오프셋 상수로 빼면 오프셋 변이가 초록이 된다)."""
    return (at - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S")


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
    saved_paths = (M.LOG_PATH, M.PROGRESS_DIR, M.ARCHIVE_PATH)
    for k, v in cfg.items():
        setattr(config, k, v)
    M.LOG_PATH, M.PROGRESS_DIR, M.ARCHIVE_PATH = d / "log.txt", d / "prog", d / "archive.db"
    try:
        yield d
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
        M.LOG_PATH, M.PROGRESS_DIR, M.ARCHIVE_PATH = saved_paths
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
        assert M.next_cap([_day("2026-10-19", 100, hours=3)], "2026-10-20") == 200, "딱 3시간은 제시간"
        assert M.next_cap([_day("2026-10-19", 100, key=1)], "2026-10-20") == 100, "키 거절 → 그대로"
        assert M.next_cap([_day("2026-10-19", 100, hours=11) | {"run_s": 3 * 3600}], "2026-10-20") == 200, \
            "돈 시간(run_s)이 있으면 벽시계 대신 그것 — 딱 3시간은 제시간"
        assert M.next_cap([_day("2026-10-19", 100, hours=1) | {"run_s": 3 * 3600 + 1}], "2026-10-20") == 100, \
            "돈 시간이 넘으면 벽시계가 짧아도 그대로"
        assert M.next_cap([_day("2026-10-17", 100)], "2026-10-20") == 100, "어제 줄 없음 → 마지막 줄 상한"
        b = [_day("2026-10-15", 400, blocked=1, fin=False)]
        assert M.next_cap(b, "2026-10-16") == 200, "막힘 → 절반"
        assert M.next_cap(b + [_day("2026-10-16", 200)], "2026-10-17") == 200, "유지 중엔 좋은 날이어도 안 올린다"
        assert M.next_cap(b, "2026-10-17") == 200, "달력 날 — 꺼진 날도 센다(2일째)"
        assert M.next_cap(b + [_day("2026-10-17", 200)], "2026-10-18") == 400, "유지(2일) 뒤엔 다시 배율"
        assert M.next_cap(b + [_day("2026-10-20", 5)], "2026-10-16") == 200, "오늘 이후 줄은 안 본다"
        assert M.next_cap([_day("2026-10-19", 100), _day("2026-10-20", 5, blocked=1)], "2026-10-20") == 200, \
            "오늘 줄도 안 본다(오늘 막혀도 오늘 상한은 이미 정해졌다)"


# ── 요약 ──────────────────────────────────────────────────────────────────────

def test_summarize_and_lineup():
    d = detail("m1", h(1), "A", "B")
    g = M.summarize(d)
    assert g and g.match_id == "m1" and len(g.sides) == 2
    assert g.at == int(h(1).replace(tzinfo=M.KST).timestamp()), "at 은 진짜 epoch(원문 UTC · 63)"
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
        w.game("at", FLOOR, "A")   # 딱 바닥 — 옛 경기가 아니라 멈추지 않는다(멈추면 old1 이 빠진다)
        w.game("old1", FLOOR - timedelta(hours=1), "A")
        w.game("old2", FLOOR - timedelta(hours=2), "A")
        r = rank_db(d, ["가"])
        go(d, r, w)
        got = games_in(d)
        assert {"new", "y", "at", "old1"} <= got and "old2" not in got, f"바닥 아래 첫 경기에서 멈춘다: {got}"
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


def test_open_db_adds_run_s_to_old_days_table():
    """run_s 전의 dm.db(이 PC 에 이미 있다)를 열면 열이 붙고 옛 줄은 NULL — 벽시계 판정으로 남는다."""
    with tmp() as d:
        c = sqlite3.connect(str(d / "dm.db"))
        c.execute("CREATE TABLE days (day TEXT PRIMARY KEY, cap INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0,"
                  " blocked INTEGER NOT NULL DEFAULT 0, key_rejected INTEGER NOT NULL DEFAULT 0, stop TEXT,"
                  " started_at TEXT NOT NULL, finished_at TEXT, stage TEXT)")
        c.execute("INSERT INTO days (day, cap, started_at) VALUES ('2026-10-19', 5, '2026-10-19T09:00:00')")
        c.commit()
        c.close()
        M.open_db(d / "dm.db").close()
        assert q1(d, "SELECT run_s FROM days") == (None,)


def test_run_seconds_exclude_gap_between_runs():
    """제시간은 실행들이 실제로 돈 시간의 합 — 끊긴 뒤 11시간 지나 이어 끝내도 벽시계로 재지 않는다(10-22~24 상한 정체)."""
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_DAY_HOURS=6, DEV_MATCH_CAP_GROWTH=2.0) as d:
        w = World()
        for i in range(3):
            w.person(f"p{i}", f"O{i}")
            w.game(f"m{i}a", h(1), f"O{i}")
        c = Clock()
        n = [0]
        orig = FakeAPI.get_match_detail_nocache

        def slow(self, mid, attempts=1):
            c.t += 100
            n[0] += 1
            return orig(self, mid, attempts)
        FakeAPI.get_match_detail_nocache = slow
        try:
            r = rank_db(d, ["p0", "p1", "p2"])
            res1 = go(d, r, w, clock=c, deadline=c.t + 50)
            assert not res1.finished, res1
            c.t += 11 * 3600          # PC 꺼짐 — 다음 실행까지 11시간
            res2 = go(d, r, w, clock=c, now=NOW + timedelta(hours=11))
            assert res2.finished, res2
        finally:
            FakeAPI.get_match_detail_nocache = orig
        row = dict(zip(("cap", "run_s", "started_at", "finished_at"),
                       q1(d, "SELECT cap, run_s, started_at, finished_at FROM days")))
        assert n[0] == 3 and row["run_s"] == 300, f"상세 셋 × 100초만 센다(사이 11시간은 빼고): {row} · 상세 {n[0]}"
        assert (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])
                ).total_seconds() > 6 * 3600, "벽시계로는 제시간을 넘긴 날이어야 이 테스트가 뜻이 있다"
        nxt = (NOW + timedelta(days=1)).date().isoformat()
        assert M.next_cap([row | {"day": NOW.date().isoformat(), "blocked": 0, "key_rejected": 0}], nxt) == row["cap"] * 2


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
        # 개수만으로는 못 잰다 — 조건을 뒤집으면 "나" 의 성공 두 번이 세여 2 가 그대로 나온다
        errs = [n for n in res.notes if n.startswith(("단 1:", "단 2:")) and "건너뜀" not in n]
        assert len(errs) == 2 and all("NETWORK" in n for n in errs), res.notes


def test_run_log_and_status_cli():
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_MISSING_TRIES=2) as d:
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
        with sqlite3.connect(d / "dm.db") as c:   # 포기 = tries 가 상한에 닿은 것(상한과 같으면 포기)
            c.executemany("INSERT INTO missing (match_id, owner, tries) VALUES (?, 1, ?)", [("g", 2), ("r", 1)])
        saved_env, saved_db = os.environ.get(DEV_KEY_VAR), M.DB_PATH
        os.environ[DEV_KEY_VAR] = "k"
        M.DB_PATH = d / "dm.db"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert M.main(["--status"]) == 0
                for _ in range(2):   # 날짜는 실제 — 첫 번째가 오늘을 끝내고 두 번째가 건너뛴다
                    # 시계는 멈춤 — 걸린 시간 0 이어도 초당 요청 계산이 0 으로 나누지 않아야(Windows 시계는 15ms 단위)
                    M.run(db_path=d / "dm.db", lock_path=d / "l", fifa_db=d / "f.db", rank_open=lambda: rank_db(d, ["가"]),
                          api_cls=lambda k: FakeAPI(w), clock=lambda: 100.0)
        finally:
            M.DB_PATH = saved_db
            if saved_env is None:
                os.environ.pop(DEV_KEY_VAR, None)
            else:
                os.environ[DEV_KEY_VAR] = saved_env
        out = buf.getvalue()
        assert "경기 1" in out and "날" in out, out
        assert "missing (2, 1)" in out, out
        assert "끝남 — 건너뜀" in (d / "log.txt").read_text(encoding="utf-8"), "끝난 날은 로그에 건너뜀"


# ── 조각 ② — 단 3 · 단 4 · 시즌 시작 · --check-fc ─────────────────────────────

def ep(n):   # NOW 에서 n 시간 전의 epoch(KST)
    return int((NOW - timedelta(hours=n)).timestamp())


def cursor_row(d, ouid):
    c = sqlite3.connect(str(d / "dm.db"))
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT c.* FROM cursor c JOIN owners o ON o.id = c.owner WHERE o.ouid = ?", (ouid,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def archive(d, snaps):
    """snaps = [(taken_at, new_season, [(rank, profile_sn, nickname)])] — WAL 로 만든다(실파일과 같은 꼴)."""
    c = sqlite3.connect(str(d / "archive.db"))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE snap (id INTEGER PRIMARY KEY, taken_at TEXT UNIQUE, season_seq INTEGER, new_season INTEGER)")
    c.execute("CREATE TABLE snap_rows (snap_id INTEGER, rank INTEGER, profile_sn INTEGER, nickname TEXT,"
              " PRIMARY KEY (snap_id, profile_sn)) WITHOUT ROWID")
    for i, (taken, new, rows) in enumerate(snaps, 1):
        c.execute("INSERT INTO snap VALUES (?, ?, 1, ?)", (i, taken, new))
        c.executemany("INSERT INTO snap_rows VALUES (?, ?, ?, ?)", [(i, *r) for r in rows])
    c.commit()
    c.close()


def manual_run(d, w, day_now=NOW, season=None):
    conn = M.open_db(d / "dm.db")
    run = M._Run(M._Db(conn), M._Gate(10 ** 6, 0, lambda: False, lambda s: None, lambda: 0.0),
                 lambda: FakeAPI(w), lambda: day_now, 0)
    run.season_at = season
    return conn, run


def test_open_db_adds_cursor_columns_to_old_table():
    with tmp() as d:
        c = sqlite3.connect(str(d / "dm.db"))
        c.execute("CREATE TABLE cursor (owner INTEGER PRIMARY KEY, top_id TEXT, top_at INTEGER,"
                  " back_done INTEGER NOT NULL DEFAULT 0)")
        c.execute("INSERT INTO cursor (owner, top_id, top_at) VALUES (1, 'm', 5)")
        c.commit()
        c.close()
        M.open_db(d / "dm.db").close()
        assert q1(d, "SELECT depth, base_id, back_day, back_req FROM cursor") == (None, None, None, 0), \
            "옛 커서는 depth 모름(NULL) — 단 4 가 걸어서 잰다"


def test_plan_outside_uses_last_new_season_not_max_seq():
    with tmp() as d:
        archive(d, [("2026-10-01T09:00:00", 0, [(5, 1, "옛시즌")]),          # rank.db 를 지우기 전 시즌(seq 2 였다)
                    ("2026-10-10T09:00:00", 1, [(9, 2, "둘"), (7, 3, "셋")]),
                    ("2026-10-12T09:00:00", 0, [(4, 3, "셋"), (8, 4, "넷옛"), (3, 2, "둘")]),
                    ("2026-10-13T09:00:00", 0, [(6, 4, "넷")])])
        before = (d / "archive.db").read_bytes()
        out, note = M.plan_outside(d / "archive.db", exclude={3})
        assert not note, note
        assert [(t.profile_sn, t.rank, t.nickname) for t in out] == [(2, 3, "둘"), (4, 6, "넷")], \
            f"마지막 새 시즌부터 · 지금 1만 명(3) 빼고 · 가장 높았던 순위 · 마지막 닉네임: {out}"
        wal = d / "archive.db-wal"   # 읽기 전용으로 WAL 파일을 열면 SQLite 가 빈 -wal·-shm 을 남긴다(내용은 0)
        assert (d / "archive.db").read_bytes() == before and (not wal.exists() or wal.stat().st_size == 0), "보관에 안 쓴다"
        assert M.plan_outside(d / "없음.db", set())[0] == []


def test_season_start_prefers_later_of_table_and_new_season_snapshot():
    with tmp() as d:
        f = sqlite3.connect(str(d / "f.db"))
        f.execute("CREATE TABLE seasons (no INTEGER PRIMARY KEY, name TEXT, start_date TEXT, end_date TEXT, fetched_at TEXT)")
        f.executemany("INSERT INTO seasons VALUES (?, 'x', ?, ?, '')", [(1, "2026-07-01", "2026-08-01"),
                                                                          (2, "2026-08-01", "2026-09-10")])
        f.commit()
        f.close()
        r = rank_db(d, ["가"])   # 첫 스냅숏 · new_season 0 — 시즌 중간에 수집을 시작했다
        sep10 = int(datetime(2026, 9, 10, tzinfo=M.KST).timestamp())
        assert M.season_start_at(d / "f.db", r) == sep10, "시즌표의 마지막 종료일 = 지금 시즌 시작"
        r.execute("UPDATE snapshots SET new_season = 1")
        r.commit()
        snap_at = M.kst_epoch(r.execute("SELECT taken_at FROM snapshots").fetchone()[0])
        assert snap_at > sep10 and M.season_start_at(d / "f.db", r) == snap_at, "시즌표가 늦으면 새 시즌 스냅숏 쪽"
        assert M.season_start_at(d / "없음.db", r) == snap_at, "시즌표가 없으면 새 시즌 스냅숏만"
        r.execute("UPDATE snapshots SET new_season = 0")
        assert M.season_start_at(d / "없음.db", r) is None, "둘 다 모르면 None(단 4 안 함)"


def b_world():
    """B 의 목록(최신 먼저): h1 · h5 · h20 · [바닥 h34] · h40 · h45 · h55 · ab(h65 · A 와) · h80. 시즌 시작 h70."""
    w = World()
    w.person("가", "B")
    w.person("나", "A")
    for n in (1, 5, 20, 40, 45, 55, 80):
        w.game(f"b{n}", h(n), "B")
    w.game("ab", h(65), "A", "B")
    return w


def test_back_starts_from_own_base_not_games_min():
    """H1 — 남(A)이 먼저 넣은 A 대 B 경기(h65)가 B 의 가장 옛 경기로 보여도, 단 4 는 B 의 base(h40)에서 옛날로 간다."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        res = go(d, rank_db(d, ["나", "가"]), w, season_at=ep(70))
        assert res.stage_done == "done", res
        assert {"b45", "b55", "ab", "b80"} <= games_in(d), f"base 와 시즌 시작 사이 B 경기 전부: {games_in(d)}"
        c = cursor_row(d, "B")
        assert c["back_done"] == 1 and c["top_id"] == "b1" and c["top_at"] == ep(1), f"단 4 는 top 을 안 건드린다: {c}"
        assert c["base_id"] == "b80" and c["depth"] == 8, c


def test_back_finds_base_after_list_shifts():
    """H2 — 단 4 사이에 새 경기가 맨 앞에 쌓여도 base 를 그 쪽에서 찾아 이어 간다(빠짐·겹침 없음)."""
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_BACK_PER_PERSON=2) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=ep(70))
        c = cursor_row(d, "B")
        assert c["base_id"] == "b45" and c["back_done"] == 0, f"목록 1 + 상세 1 에서 멈춤: {c}"
        for n in (0.5, 0.2):                       # 새 경기 둘이 맨 앞에(커서는 그대로 — 단 2 를 안 돌린다)
            w.game(f"new{n}", NOW.replace(tzinfo=None) - timedelta(hours=n) + timedelta(hours=3), "B")
        conn, run = manual_run(d, w, NOW + timedelta(days=1), ep(70))
        run.day = "2026-10-21"
        lists = w.count("list")
        run.back(M.Target(2, 700001, "가"))
        conn.close()
        c = cursor_row(d, "B")
        # 목록 1 + b55 상세 1 · ab 는 A 가 받아 요청 없이 지나간다 → b80 에서 상한. 목록 자리 8 − 밀림 2 + 1
        assert c["base_id"] == "ab" and c["depth"] == 7, f"밀린 두 칸을 빼고 depth 를 잰다: {c}"
        assert w.count("list") == lists + 1 and "b55" in games_in(d) and "new0.5" not in games_in(d), \
            "base 쪽 하나만 읽고 새 경기(단 2 몫)는 안 받는다"


def test_back_per_person_cap_holds_within_day():
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_BACK_PER_PERSON=2) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=ep(70))
        n = w.count()
        conn, run = manual_run(d, w, NOW, ep(70))
        run.day = NOW.date().isoformat()
        run.back(M.Target(2, 700001, "가"))
        assert w.count() == n, "같은 날 다시 돌아도 사람별 상한이 다시 안 찬다"
        run.day = "2026-10-21"
        run.back(M.Target(2, 700001, "가"))
        conn.close()
        assert w.count() > n and cursor_row(d, "B")["back_req"] == 2, "다음 날은 새로"


def test_back_walks_when_depth_unknown():
    """옛 커서(depth NULL)는 맨 앞부터 걸어 top 자리로 depth 를 다시 잰다 — 아는 경기는 상세 없이."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=None)   # 시즌 시작을 몰라 단 4 는 안 돈다
        c = sqlite3.connect(str(d / "dm.db"))
        c.execute("UPDATE cursor SET depth = NULL, base_id = NULL, base_at = NULL")
        c.commit()
        c.close()
        details = w.count("detail")
        conn, run = manual_run(d, w, NOW, ep(70))
        run.day = NOW.date().isoformat()
        run.back(M.Target(2, 700001, "가"))
        conn.close()
        cr = cursor_row(d, "B")
        assert cr["depth"] == 8 and cr["base_id"] == "b80" and cr["back_done"] == 1, cr
        assert w.count("detail") - details == 3, "모르던 b45·b55·b80 만 받는다(ab 는 A 가 이미)"


def test_back_base_aged_out_marks_done_and_gap():
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_BACK_PER_PERSON=2) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=ep(70))
        assert cursor_row(d, "B")["base_id"] == "b45"
        w.lists["B"] = w.lists["B"][:4]           # 오픈API 창이 밀려 b40 뒤가 빠졌다
        conn, run = manual_run(d, w, NOW, ep(70))
        run.day = "2026-10-21"
        run.back(M.Target(2, 700001, "가"))
        conn.close()
        assert cursor_row(d, "B")["back_done"] == 1
        assert q1(d, "SELECT gap_at FROM owners WHERE ouid = 'B'")[0] == ep(45), "그 앞이 빈다"


def test_stage_3_and_4_wait_for_stage_2():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        archive(d, [("2026-10-10T09:00:00", 1, [(1, 900, "밖")])])
        w.person("밖", "X")
        res = go(d, rank_db(d, ["나", "가"]), w, cap=8, season_at=ep(70))
        assert res.stage_done == "1" and res.reason == "cap", res
        assert q1(d, "SELECT COUNT(*) FROM cursor WHERE back_day IS NOT NULL")[0] == 0, "단 4 요청 0"
        assert ("ouid", "밖") not in w.calls, "단 3 요청 0"
        res = go(d, rank_db(d, ["나", "가"]), w, cap=500, season_at=ep(70), force=True)
        assert res.stage_done == "done" and ("ouid", "밖") in w.calls, res
        assert all(res.notes), f"빈 사유를 적지 않는다: {res.notes}"


def test_scan_records_base_first_then_adds_new_games():
    """_new_base — 첫 훑기는 바닥 밖 첫 경기까지(depth 4) · 다음 날 top 을 만나면 depth += 새 경기 수 · base 는 그대로."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        r = rank_db(d, ["나", "가"])
        go(d, r, w, season_at=None)
        c = cursor_row(d, "B")
        assert (c["depth"], c["base_id"], c["base_at"], c["back_done"]) == (4, "b40", ep(40), 0), c
        for n in (2, 1):
            w.game(f"n{n}", NOW.replace(tzinfo=None) + timedelta(hours=24 - n), "B")
        go(d, r, w, now=NOW + timedelta(days=1), season_at=None)
        c = cursor_row(d, "B")
        assert (c["depth"], c["base_id"], c["top_id"]) == (6, "b40", "n1"), c
        a = cursor_row(d, "A")
        assert (a["depth"], a["base_id"]) == (1, "ab"), f"A 는 첫 경기가 바닥 밖: {a}"


def test_scan_to_api_end_marks_back_done():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = World()
        w.person("다", "C")
        for n in (1, 3, 9):
            w.game(f"c{n}", h(n), "C")
        go(d, rank_db(d, ["다"]), w, season_at=None)
        c = cursor_row(d, "C")
        assert (c["depth"], c["base_id"], c["back_done"]) == (3, "c9", 1), f"목록을 끝까지 다 봤다: {c}"


def test_back_hits_api_end_before_season_start():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=ep(500))
        c = cursor_row(d, "B")
        assert c["back_done"] == 1 and c["base_id"] == "b80", c
        assert q1(d, "SELECT gap_at FROM owners WHERE ouid = 'B'")[0] == ep(80), "시즌 시작 전에 끝났다 — 그 앞이 빈다"


def test_back_season_start_is_inclusive():
    """시즌 시작 시각과 같은 경기는 이번 시즌 — 거기서 멈추지 않고 하나 더 옛 경기에서 멈춘다."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        w.game("b90", h(90), "B")
        go(d, rank_db(d, ["나", "가"]), w, season_at=ep(80))
        c = cursor_row(d, "B")
        assert c["base_id"] == "b90" and c["back_done"] == 1, c


def test_back_paging_counts_requests_and_page_edges():
    """쪽이 꽉 찼으면 다음 쪽을 읽는다(끝이 아니다) · 사람별 상한은 목록 요청도 센다 · 꽉 찬 쪽에 base 가 없으면 끝 아님."""
    with tmp(DEV_MATCH_WORKERS=1, DEV_MATCH_PAGE=2, DEV_MATCH_BACK_PER_PERSON=2) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=None)          # B: depth 4 · base b40
        c = sqlite3.connect(str(d / "dm.db"))
        c.execute("UPDATE cursor SET depth = NULL")                  # 걸어야 하는 옛 커서 — 목록만 든다
        c.commit()
        c.close()
        conn, run = manual_run(d, w, NOW, ep(70))
        run.day = NOW.date().isoformat()
        lists = w.count("list")
        run.back(M.Target(2, 700001, "가"))
        assert w.count("list") - lists == 2, "상한 2 — 목록 두 쪽까지만"
        cr = cursor_row(d, "B")
        assert cr["back_done"] == 0 and cr["depth"] == 4 and cr["base_id"] == "b40", f"쪽 4칸이 꽉 차 끝이 아니다: {cr}"
        w.lists["B"] = ["z1", "z2"] + w.lists["B"]                   # 꽉 찬 쪽에 base 가 없게 — 끝으로 치면 안 된다
        w.lists["B"].remove("b40")
        w.lists["B"].insert(9, "b40")
        run.day = "2026-10-21"
        run.back(M.Target(2, 700001, "가"))
        conn.close()
        assert cursor_row(d, "B")["back_done"] == 0, "꽉 찬 쪽에 없으면 건너뜀(끝 아님)"


def test_main_check_fc_flag_runs_only_check():
    seen = []
    orig_c, orig_r = M.check_fc, M.run
    M.check_fc, M.run = (lambda: seen.append("check") or 7), (lambda **k: seen.append("run") or 0)
    try:
        assert M.main(["--check-fc"]) == 7 and seen == ["check"], seen
        assert M.main([]) == 0 and seen == ["check", "run"], seen
    finally:
        M.check_fc, M.run = orig_c, orig_r


def fifa_for_check(d, my, rows):
    f = sqlite3.connect(str(d / "f.db"))
    f.execute("CREATE TABLE trade_state (key TEXT PRIMARY KEY, value TEXT)")
    f.execute("CREATE TABLE matches (match_id TEXT PRIMARY KEY, match_type INTEGER, match_date TEXT, payload TEXT)")
    f.execute("CREATE TABLE match_players (match_id TEXT, ouid TEXT, PRIMARY KEY (match_id, ouid))")
    if my:
        f.execute("INSERT INTO trade_state VALUES ('my_ouid', ?)", (my,))
    for mid, when, mt in rows:
        f.execute("INSERT INTO matches VALUES (?, ?, ?, '{}')", (mid, mt, _raw(when)))
        f.execute("INSERT INTO match_players VALUES (?, ?)", (mid, my))
    f.commit()
    f.close()


def test_check_fc_counts_matches_dev_missed():
    with tmp(DEV_MATCH_WORKERS=1) as d:
        w = b_world()
        go(d, rank_db(d, ["나", "가"]), w, season_at=ep(70))   # B 는 base b80 ~ top b1 을 끝까지 훑었다
        mt = config.DEFAULT_MATCH_TYPE
        fifa_for_check(d, "B", [("b1", h(1), mt), ("b45", h(45), mt), ("b80", h(80), mt), ("lost", h(30), mt),
                                ("other", h(30), mt + 1), ("old", h(200), mt)])
        out = []
        assert M.check_fc(d / "dm.db", d / "f.db", out=out.append) == 1, out
        assert any("빠뜨린 경기 1" in s and "lost" in s for s in out), f"창 밖(old)·다른 종류(other)는 안 센다: {out}"
        assert any("앱에 없는 dev 경기 5" in s for s in out), f"창 양 끝(top b1 · base b80)도 창 안: {out}"
        c = sqlite3.connect(str(d / "f.db"))
        c.execute("DELETE FROM matches WHERE match_id = 'lost'")
        c.commit()
        c.close()
        out = []
        assert M.check_fc(d / "dm.db", d / "f.db", out=out.append) == 0, out
        c = sqlite3.connect(str(d / "f.db"))
        c.execute("DELETE FROM trade_state")
        c.commit()
        c.close()
        out = []
        assert M.check_fc(d / "dm.db", d / "f.db", out=out.append) == 1 and "확인 전" in out[0], out


def _old_db(d):
    """63 전 dev_matches.db — meta 표 없이 9시간 이른 시각 열."""
    c = sqlite3.connect(str(d / "dm.db"))
    c.executescript(M.SCHEMA.replace("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);", ""))
    c.executescript("""
        INSERT INTO owners (id, ouid, gap_at) VALUES (1, 'A', 1000), (2, 'B', NULL);
        INSERT INTO games (match_id, at, o1) VALUES ('g1', 5000, 1);
        INSERT INTO cursor (owner, top_id, top_at, base_id, base_at) VALUES (1, 'g1', 5000, 'g0', 4000), (2, NULL, 7000, NULL, NULL);
    """)
    c.commit()
    c.close()


def test_fix_at_moves_match_times_once():
    with tmp() as d:
        _old_db(d)
        conn = M.open_db(d / "dm.db")
        try:
            assert M.fix_at(conn) is True
            assert M.fix_at(conn) is False, "두 번째는 표시가 있어 안 옮긴다"
        finally:
            conn.close()
        assert q1(d, "SELECT at FROM games")[0] == 5000 + 32400
        assert q1(d, "SELECT top_at, base_at FROM cursor WHERE owner = 1") == (5000 + 32400, 4000 + 32400)
        assert q1(d, "SELECT top_at FROM cursor WHERE owner = 2")[0] == 7000, \
            "경기가 없던 사람의 top_at 은 진짜 지금 epoch — 더하면 9시간 미래"
        assert q1(d, "SELECT gap_at FROM owners WHERE id = 1")[0] == 1000 + 32400
        assert q1(d, "SELECT gap_at FROM owners WHERE id = 2")[0] is None


def test_run_day_moves_times_but_status_and_check_do_not():
    """옮기기는 잠금을 쥔 수집(run_day)에서만 — --status·--check-fc 는 안 옮긴다(M5: 옮기기 전엔 check 가 거부)."""
    with tmp(DEV_MATCH_WORKERS=1) as d:
        _old_db(d)
        fifa_for_check(d, "A", [])
        M.status(d / "dm.db")
        out = []
        assert M.check_fc(d / "dm.db", d / "f.db", out=out.append) == 1 and "안 옮겼다" in out[0], out
        assert q1(d, "SELECT at FROM games")[0] == 5000, "status·check 가 옮겼다"
        w = World()
        go(d, rank_db(d, []), w)
        assert q1(d, "SELECT at FROM games WHERE match_id = 'g1'")[0] == 5000 + 32400, "수집 실행이 안 옮겼다"


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
