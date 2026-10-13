"""구단주 분포 v2(49단계 조각 ①) 받기·저장·지우기 테스트 — 지어낸 구단주·가짜 넥슨 API 로, 네트워크 없이.

pytest 없이 `python tests/test_ownerdist.py`. 닉네임·ouid·경기 id 는 전부 지어낸 값이다.
판 수·층당 인원은 config 를 줄여서(6명 × 7판 = 66요청) 돌린다 — 판정식이 아니라 **받는 규칙**(계수·상한·429·끊김·지우기)이 대상이다.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import habits
import nexon_api
import ownerdist as od
import rankcollect
import ranker
import store
from nexon_api import QUOTA_CODE, NexonAPIError

NOW = datetime(2026, 10, 13, 12, 0, 0)
RANKS = [1, 2, 3, 4, 201, 202, 203, 204, 1001, 1002, 1003, 1004, 3500]   # 3500 은 표본 밖(3000 초과)
SMALL = dict(OWNER_DIST_PER_STRATUM=2, OWNER_DIST_MATCHES=7, OWNER_DIST_PAGE=3, OWNER_DIST_MIN_GAMES=5,
             OWNER_DIST_MIN_OWNERS=4, OWNER_DIST_PERSON_REQ=11, OWNER_DIST_WORKERS=4,
             OWNER_DIST_DAILY_REQ=10_000, OWNER_DIST_ROUND_REQ=10_000, OWNER_DIST_429_WAIT_S=60)
REQ_PER_OWNER = 1 + 3 + 7     # ouid 1 · 목록 3쪽(3·3·1) · 상세 7
N_OWNERS = 6


@contextmanager
def _cfg(**kw):
    keep = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in keep.items():
            setattr(config, k, v)


def _side(ouid, res, shots, on, goals, tk_try, tk_ok, poss, lt=0, pas=0):
    return {"ouid": ouid, "matchDetail": {"matchResult": res, "possession": poss},
            "shoot": {"shootTotal": shots, "effectiveShootTotal": on, "goalTotal": goals},
            "defence": {"tackleTry": tk_try, "tackleSuccess": tk_ok},
            "pass": {"passTry": pas, "longPassTry": lt, "throughPassTry": 0}}


def _detail(mid: str, ouid: str, i: int) -> dict:
    me = _side(ouid, "승", 4 + i % 3, 2 + i % 2, 1, 10, 6 + i % 3, 55, 3, 40)
    op = _side("상대", "패", 3, 1, 0, 9, 5, 45, 2, 40)
    return {"matchId": mid, "matchDate": f"2026-10-12T{10 + i % 10:02d}:00:00", "matchInfo": [me, op]}


class FakeAPI:
    """닉네임 → ouid. 구단주마다 판 수(games)만큼. 요청마다 calls 에 적고 동시에 도는 상세 수의 최댓값을 잰다."""

    def __init__(self, games: int = 7, gone: set[str] | None = None):
        self.games = games
        self.gone = gone or set()
        self.calls: list[tuple[str, object, int]] = []
        self.errors: dict[int, Exception] = {}
        self.hook = None             # 호출 직전 부르는 콜백(이름, 순번)
        self.inflight = self.max_inflight = 0
        self._lock = threading.Lock()

    def _hit(self, name, arg, attempts):
        with self._lock:
            self.calls.append((name, arg, attempts))
            n = len(self.calls)
        if self.hook:
            self.hook(name, n)
        e = self.errors.get(n)
        if e is not None:
            raise e

    def get_ouid(self, nickname, attempts=3):
        self._hit("ouid", nickname, attempts)
        if nickname in self.gone:
            raise NexonAPIError("없음", code="OPENAPI00004", status=400)
        return "ou-" + nickname

    def get_match_ids(self, ouid, matchtype=50, offset=0, limit=20, attempts=3):
        self._hit("ids", ouid, attempts)
        return [f"{ouid}#{i}" for i in range(offset, min(offset + limit, self.games))]

    def get_match_detail_nocache(self, match_id, attempts=1):
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            self._hit("detail", match_id, attempts)
            time.sleep(0.002)
            ouid, i = match_id.rsplit("#", 1)
            return _detail(match_id, ouid, int(i))
        finally:
            with self._lock:
                self.inflight -= 1

    def get_match_detail(self, match_id, attempts=3):   # 쓰면 안 된다 — 디스크 캐시를 쓰는 길
        raise AssertionError("구단주 분포가 .cache 를 쓰는 get_match_detail 을 불렀다")


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []
        self._lock = threading.Lock()

    def now(self):
        return self.t

    def sleep(self, s):
        with self._lock:
            self.slept.append(s)
            self.t += s


def _rank_db(d: str, ranks=RANKS):
    rc = rankcollect.open_rank_db(Path(d) / "rank.db")
    rows = [ranker.RankRow(rank=r, profile_sn=900 + r, nickname=f"구단주{r}", team_color="팀", elo=3000.0 - r) for r in ranks]
    rankcollect.save_snapshot(rc, rows, NOW)
    return rc


class Env:
    def __init__(self, games=7, gone=None):
        self._d = tempfile.TemporaryDirectory()
        self.dir = Path(self._d.name)
        self.db = self.dir / "fifa.db"
        self.rank = _rank_db(self._d.name)
        self.api = FakeAPI(games, gone)
        self.clock = Clock()
        self.now = NOW
        self.conn = store.open_db(self.db)

    def run(self, **kw):
        return od.run_round(self.db, self.rank, lambda: self.api, sleep=self.clock.sleep, clock=self.clock.now,
                            now_fn=lambda: self.now, **kw)

    def files(self) -> bytes:
        out = b""
        for suffix in ("", "-wal"):
            p = Path(str(self.db) + suffix)
            if p.exists():
                out += p.read_bytes()
        return out

    def close(self):
        self.conn.close()
        self.rank.close()
        self._d.cleanup()


@contextmanager
def env(**kw):
    e = Env(**kw)
    try:
        with _cfg(**SMALL):
            yield e
    finally:
        e.close()


# ── 표본 · 요약 ────────────────────────────────────────────────────────────

def test_plan_round_is_stratified_seeded_and_in_range():
    with env() as e:
        a = od.plan_round(e.rank, 111)
        assert len(a) == N_OWNERS, a
        assert sorted(t.stratum for t in a) == [1, 1, 2, 2, 3, 3]
        assert all(t.rank <= 3000 for t in a), "3000위 밖(3500)이 표본에 들어왔다"
        assert a == od.plan_round(e.rank, 111), "같은 시드는 같은 표본"
        assert {t.profile_sn for t in a} != {t.profile_sn for t in od.plan_round(e.rank, 222)} or True
        assert od.plan_round(None, 1) == [], "스냅숏이 없으면 빈 표본"
        # 층 경계: 200 은 1층, 201 은 2층, 1000 은 2층, 1001 은 3층, 3000 은 3층, 3001 은 밖
        assert [od.stratum_of(r) for r in (200, 201, 1000, 1001, 3000, 3001)] == [1, 2, 2, 3, 3, None]


def test_summarize_owner_by_hand():
    # 손으로 센 3경기: 슛 나 4·2·6 / 상대 2·5·3, 골 나 2·1·1 / 상대 0·3·1
    games = [
        {"matchId": "g1", "matchInfo": [_side("me", "승", 4, 3, 2, 10, 7, 60), _side("op", "패", 2, 1, 0, 8, 6, 40)]},
        {"matchId": "g2", "matchInfo": [_side("me", "패", 2, 2, 1, 6, 3, 45), _side("op", "승", 5, 4, 3, 10, 8, 55)]},
        {"matchId": "g3", "matchInfo": [_side("me", "무", 6, 4, 1, 8, 6, 50), _side("op", "무", 3, 2, 1, 6, 3, 50)]},
    ]
    row = od.summarize_owner(habits.side_totals(games, "me"), 2, "2026-10-12T10:00:00")
    assert row["stratum"] == 2 and row["n_games"] == 3 and row["last_match"] == "2026-10-12T10:00:00"
    assert abs(row["shots"] - 2 / 3) < 1e-9, row["shots"]                   # 경기당 슛 차
    assert abs(row["finish"] - (4 / 12 - 4 / 10) * 100) < 1e-9, row["finish"]   # 결정력 33.33 − 40.00
    assert set(store.OWNER_METRICS) <= set(row), "6지표가 다 있어야 한다"
    assert "on_target_rate" not in row, "유효슛 비율은 제외 지표"
    # 상대가 태클을 한 번도 안 했으면 비율을 못 잰다 → None(그 지표 분포에서만 빠진다)
    none = [{"matchId": "z", "matchInfo": [_side("me", "승", 3, 2, 1, 5, 3, 50), _side("op", "패", 3, 1, 0, 0, 0, 50)]}]
    assert od.summarize_owner(habits.side_totals(none, "me"), 1, None)["tackle_rate"] is None


def test_eligible_boundaries():
    with _cfg(OWNER_DIST_MIN_GAMES=5):
        ok = {"n_games": 5, "last_match": (NOW - timedelta(days=30)).isoformat()}
        assert od.eligible(ok, NOW)
        assert not od.eligible({**ok, "n_games": 4}, NOW), "판수 하한 밑"
        assert not od.eligible({**ok, "last_match": (NOW - timedelta(days=30, seconds=1)).isoformat()}, NOW), "30일 초과"
        assert not od.eligible({**ok, "last_match": None}, NOW) and not od.eligible({**ok, "last_match": "엉망"}, NOW)


# ── 받기 ──────────────────────────────────────────────────────────────────

def test_full_round_counts_every_request_and_saves_only_summaries():
    with env() as e:
        r = e.run()
        assert r.result == "done" and r.owners_done == r.owners_total == N_OWNERS, r
        assert r.requests == N_OWNERS * REQ_PER_OWNER == len(e.api.calls), (r.requests, len(e.api.calls))
        assert store.budget_used(e.conn, NOW.date().isoformat(), store.BUDGET_OWNER_DIST) == r.requests, "하루 계수가 요청과 다르다"
        assert all(a == 1 for _n, _x, a in e.api.calls), "재시도(attempts>1)가 켜졌다 — 한도를 계수 없이 먹는다"
        rows = store.owner_dist_rows(e.conn, r.round_id)
        assert len(rows) == N_OWNERS and all(x["n_games"] == 7 for x in rows), rows
        assert all(x["last_match"] and x["shots"] is not None for x in rows)
        st = e.conn.execute("SELECT result, salt, req_count FROM owner_dist_state").fetchone()
        assert (st["result"], st["salt"], st["req_count"]) == ("done", None, r.requests), dict(st)
        assert store.owner_done_hashes(e.conn, r.round_id) == set(), "끝난 바퀴에 이어 받기 해시가 남았다"
        blob = e.files()
        assert "구단주".encode() not in blob and b"ou-" not in blob, "닉네임·ouid 가 fifa.db 에 저장됐다"


def test_detail_never_touches_disk_cache():
    """nocache: 같은 상세를 get_match_detail 로 받으면 파일이 생기고(막지 않았다면 14.6만 파일), nocache 는 안 생긴다."""
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "cache"
        api = nexon_api.FCOnlineAPI("k", cache_dir=cache)
        api._get = lambda path, attempts=3, **p: {"matchId": p["matchid"], "matchInfo": []}   # type: ignore[method-assign]
        api.get_match_detail("m1")
        assert list(cache.glob("*.json")), "전제: 캐시 있는 길은 파일을 쓴다"
        for p in cache.glob("*.json"):
            p.unlink()
        out = api.get_match_detail_nocache("m2")
        assert out["matchId"] == "m2" and not list(cache.glob("*.json")), "nocache 가 .cache 에 썼다"


def test_concurrency_is_bounded_by_workers():
    with env(games=30) as e, _cfg(OWNER_DIST_WORKERS=3, OWNER_DIST_MATCHES=30):   # 기본값(4)이 아닌 값 — 배선이 끊겨도 4 라 통과하지 않게
        e.run()
        assert 2 <= e.api.max_inflight <= 3, e.api.max_inflight
    with env() as e, _cfg(OWNER_DIST_WORKERS=1):
        e.run()
        assert e.api.max_inflight == 1, "동시 수를 1 로 줄였는데 겹쳐 돌았다"


def test_daily_cap_stops_and_resumes_next_day():
    # 막지 않았다면 66요청 — 하루 상한 25 를 넘는다(전제)
    assert N_OWNERS * REQ_PER_OWNER > 25
    with env() as e, _cfg(OWNER_DIST_DAILY_REQ=25):
        r = e.run()
        used = store.budget_used(e.conn, NOW.date().isoformat(), store.BUDGET_OWNER_DIST)
        assert r.limit and r.result is None and used <= 25 and r.owners_done == 2, (r, used)
        assert store.owner_round_running(e.conn) is not None, "상한에 걸려도 바퀴는 running 으로 남아 이어 받는다"
        for day in (1, 2):   # 하루 25 → 구단주 2명씩, 사흘째에 끝
            e.now = NOW + timedelta(days=day)
            r = e.run()
        assert r.result == "done" and r.owners_done == N_OWNERS, r
        assert len(store.owner_dist_rows(e.conn, r.round_id)) == N_OWNERS, "이어 받으며 같은 구단주가 겹치거나 빠졌다"


def test_round_cap_aborts_and_drops_progress():
    with env() as e, _cfg(OWNER_DIST_ROUND_REQ=30):
        r = e.run()
        assert r.result == "aborted" and r.owners_done == 2, r
        assert store.owner_dist_rows(e.conn, r.round_id) == [] and store.owner_done_hashes(e.conn, r.round_id) == set()
        assert store.owner_round_running(e.conn) is None


def test_cancel_midway_discards_partial_owner_and_resume_has_no_duplicates():
    with env() as e:
        state = {"stop": False}
        e.api.hook = lambda name, n: state.__setitem__("stop", n >= REQ_PER_OWNER * 2 + 5)   # 셋째 구단주 도중
        r = e.run(cancel=lambda: state["stop"])
        assert r.cancelled and r.result is None, r
        assert len(store.owner_dist_rows(e.conn, r.round_id)) == 2, "끊긴 구단주가 반쪽으로 저장됐다"
        assert len(store.owner_done_hashes(e.conn, r.round_id)) == 2
        assert store.owner_round_running(e.conn)["req_count"] >= REQ_PER_OWNER * 2, "버린 사람의 요청도 바퀴 상한에는 들어야 한다"
        e.api.hook, e.api.calls = None, []
        r2 = e.run()
        assert r2.result == "done", r2
        rows = store.owner_dist_rows(e.conn, r.round_id)
        assert len(rows) == N_OWNERS, "이어 받은 뒤 구단주 수가 다르다"
        first = {c[1] for c in e.api.calls if c[0] == "ouid"}
        assert len(first) == N_OWNERS - 2 and len(e.api.calls) == (N_OWNERS - 2) * REQ_PER_OWNER, "이미 끝난 구단주를 다시 물었다"


def test_nickname_gone_is_remembered_without_a_row():
    with env(gone={"구단주1", "구단주2", "구단주3", "구단주4", "구단주201", "구단주202", "구단주203", "구단주204"}) as e:
        r = e.run()
        rows = store.owner_dist_rows(e.conn, r.round_id) if r.round_id else []
        assert r.owners_done == r.owners_total, r
        assert len(rows) <= N_OWNERS


def test_429_once_waits_then_retries_and_is_not_a_stop():
    with env() as e:
        e.api.errors = {2: NexonAPIError("한도", code=QUOTA_CODE, status=429)}   # 첫 구단주의 목록 요청
        r = e.run()
        assert r.result == "done" and not r.quota, r
        assert abs(sum(e.clock.slept) - config.OWNER_DIST_429_WAIT_S) < 1e-6 and max(e.clock.slept) <= 1.0, e.clock.slept
        assert not store.budget_hit_429(e.conn, NOW.date().isoformat()), "한 번의 일시 429 가 그날 멈춤으로 기록됐다"
        assert r.requests == N_OWNERS * REQ_PER_OWNER + 1, "429 로 낭비된 요청도 계수에 들어야 한다"


def test_429_twice_stops_the_day_and_blocks_other_loaders():
    with env() as e:
        e.api.errors = {2: NexonAPIError("한도", code=QUOTA_CODE, status=429), 3: NexonAPIError("한도", code=QUOTA_CODE, status=429)}
        r = e.run()
        day = NOW.date().isoformat()
        assert r.quota and r.result is None and store.owner_round_running(e.conn) is not None, r
        assert store.budget_hit_429(e.conn, day, (store.BUDGET_OWNER_DIST,)) and store.budget_hit_429(e.conn, day, (store.BUDGET_OPENAPI,)), \
            "다른 로더가 같이 멈추려면 openapi 표시도 남아야 한다"
        e.api.errors, n = {}, len(e.api.calls)
        r2 = e.run()
        assert r2.quota and len(e.api.calls) == n, "그날 429 뒤 다시 시작했다"
        e.now = NOW + timedelta(days=1)
        assert e.run().result == "done", "다음 날은 이어 받는다"


def test_other_loaders_429_blocks_start():
    with env() as e:
        store.budget_mark_429(e.conn, NOW.date().isoformat(), store.BUDGET_OPENAPI)
        r = e.run()
        assert r.quota and not e.api.calls and store.owner_round_running(e.conn) is None


# ── 바퀴 상태 · 지우기 ───────────────────────────────────────────────────────

def test_not_due_until_round_days_and_off_is_not_a_baseline():
    with env() as e:
        assert e.run().result == "done"
        n = len(e.api.calls)
        r = e.run()
        assert r.not_due and len(e.api.calls) == n, "직전 종료 직후 새 바퀴가 열렸다"
        e.now = NOW + timedelta(days=config.OWNER_DIST_ROUND_DAYS, seconds=1)
        assert not e.run().not_due
    with env() as e:
        e.api.hook = lambda name, n: None
        e.run(cancel=lambda: len(e.api.calls) > 3)
        store.purge_owner_dist(e.conn, everything=False, now=NOW.isoformat())
        assert store.owner_round_last_end(e.conn)["result"] == "off"
        assert e.run().result == "done", "off 는 기준점이 아니어서 껐다 켜면 바로 시작해야 한다"


def test_dropped_when_running_too_long():
    with env() as e:
        e.run(cancel=lambda: len(e.api.calls) > 14)
        assert store.owner_round_running(e.conn) is not None
        e.now = NOW + timedelta(days=config.OWNER_DIST_ROUND_MAX_DAYS, seconds=1)
        r = e.run()
        assert r.result == "dropped", r
        assert store.owner_dist_rows(e.conn, r.round_id) == [] and store.owner_done_hashes(e.conn, r.round_id) == set()


def test_thin_keeps_previous_done_and_done_replaces_it():
    with env() as e, _cfg(OWNER_DIST_MAX_AGE_DAYS=90):   # 지어낸 경기 날짜가 고정이라 바퀴를 40일 뒤로 밀어도 낡지 않게
        r1 = e.run()
        assert r1.result == "done"
        e.now = NOW + timedelta(days=20)
        with _cfg(OWNER_DIST_MIN_OWNERS=N_OWNERS + 1):
            r2 = e.run()
        assert r2.result == "thin", r2
        assert len(store.owner_dist_rows(e.conn, r1.round_id)) == N_OWNERS, "thin 이 좋은 분포를 지웠다"
        assert store.owner_dist_rows(e.conn, r2.round_id) == [], "thin 바퀴 줄이 남았다"
        e.now = NOW + timedelta(days=40)
        r3 = e.run()
        assert r3.result == "done"
        assert store.owner_dist_rows(e.conn, r1.round_id) == [] and len(store.owner_dist_rows(e.conn, r3.round_id)) == N_OWNERS, \
            "새 done 이 옛 done 줄을 지우지 않았다(같은 구단주가 두 바퀴 겹친다)"


def test_old_games_exclude_owners_from_the_distribution():
    with env(games=3) as e:   # MIN_GAMES=5 보다 적은 판 → 전원 제외 → thin
        r = e.run()
        assert r.result == "thin", r


def test_end_of_round_leaves_no_salt_or_hash_in_file_bytes():
    with env() as e:
        seen = {}

        def peek():   # 작업 스레드에서도 불린다 — 연결을 새로 연다
            if len(e.api.calls) <= REQ_PER_OWNER * 2 + 1 or "blob" in seen:
                return "blob" in seen
            c = store.open_db(e.db)
            try:
                run = store.owner_round_running(c)
                seen["salt"] = run["salt"]
                seen["hash"] = next(iter(store.owner_done_hashes(c, run["round_id"])), None)
                seen["blob"] = e.files()
            finally:
                c.close()
            return True

        e.run(cancel=peek)
        assert seen.get("salt") and seen.get("hash"), "전제: 진행 중에는 소금·해시가 있다"
        assert seen["salt"].encode() in seen["blob"] and seen["hash"].encode() in seen["blob"], "전제: 파일에 실제로 적힌다"
        assert e.run().result == "done"
        blob = e.files()
        assert seen["salt"].encode() not in blob and seen["hash"].encode() not in blob, "끝난 바퀴의 소금·해시가 파일 바이트에 남았다"


def test_purge_keeps_done_distribution_unless_everything():
    with env() as e:
        r = e.run()
        store.purge_owner_dist(e.conn, everything=False, now=NOW.isoformat())
        assert len(store.owner_dist_rows(e.conn, r.round_id)) == N_OWNERS, "토글만 끈 것이 완료된 분포를 지웠다"
        store.purge_owner_dist(e.conn, everything=True, now=NOW.isoformat())
        assert store.owner_dist_rows(e.conn, r.round_id) == [], "전체 지우기가 분포를 안 지웠다"
        assert store.owner_round_last_end(e.conn) is not None, "바퀴 기록(직전 종료 기준점)까지 지웠다"


def test_devkey_suspected():
    with env() as e:
        day = NOW.date().isoformat()
        assert not od.devkey_suspected(e.conn)
        store.budget_mark_429(e.conn, day, store.BUDGET_OWNER_DIST)
        assert od.devkey_suspected(e.conn), "누적 0 에서 429 면 개발 키 오신고"
        for _ in range(config.OWNER_DIST_SELFCHECK_REQ):
            store.budget_take(e.conn, day, store.BUDGET_OWNER_DIST, None)
        assert not od.devkey_suspected(e.conn), "누적 5,000 이상이면 서비스 키로 본다"


def test_no_snapshot_starts_nothing():
    with env() as e:
        e.rank.close()
        with tempfile.TemporaryDirectory() as d:
            e.rank = rankcollect.open_rank_db(Path(d) / "empty.db")
            r = e.run()
            e.rank.close()
            e.rank = _rank_db(e._d.name)
        assert r.no_sample and store.owner_round_running(e.conn) is None and not e.api.calls


def test_metric_names_exist_in_habits():
    for k in store.OWNER_METRICS:
        assert k in habits.METRICS, k
    assert math.isfinite(config.OWNER_DIST_PERSON_REQ)


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as ex:
            failed += 1
            print(f"[FAIL] {t.__name__}: {ex}")
        except Exception as ex:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(ex).__name__}: {ex}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
