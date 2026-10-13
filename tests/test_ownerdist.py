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
             OWNER_DIST_MIN_OWNERS=4, OWNER_DIST_MIN_PER_STRATUM=2, OWNER_DIST_PERSON_REQ=11, OWNER_DIST_WORKERS=4,
             OWNER_DIST_DAILY_REQ=10_000, OWNER_DIST_ROUND_REQ=10_000,
             # 아래 넷은 일부러 config 기본값(60·14·7·30)이 아닌 값 — 코드에 기본값을 박아도 테스트가 통과하지 않게
             OWNER_DIST_429_WAIT_S=7, OWNER_DIST_ROUND_DAYS=5, OWNER_DIST_ROUND_MAX_DAYS=3, OWNER_DIST_MAX_AGE_DAYS=10)
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
        assert {t.profile_sn for t in a} != {t.profile_sn for t in od.plan_round(e.rank, 222)}, "다른 시드가 같은 표본을 뽑았다"
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
    with _cfg(OWNER_DIST_MIN_GAMES=5, OWNER_DIST_MAX_AGE_DAYS=9):   # 기본값(30)이 아닌 값
        ok = {"n_games": 5, "last_match": (NOW - timedelta(days=9)).isoformat()}
        assert od.eligible(ok, NOW)
        assert not od.eligible({**ok, "n_games": 4}, NOW), "판수 하한 밑"
        assert not od.eligible({**ok, "last_match": (NOW - timedelta(days=9, seconds=1)).isoformat()}, NOW), "9일 초과"
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


GONE = {"구단주1", "구단주2", "구단주3", "구단주4", "구단주201", "구단주202", "구단주203", "구단주204"}   # 1·2층 전원 — 3층 둘만 남는다


def test_nickname_gone_is_remembered_without_a_row():
    with env(gone=GONE) as e, _cfg(OWNER_DIST_MIN_OWNERS=2, OWNER_DIST_MIN_PER_STRATUM=0):   # 층 기준은 아래 별도 테스트
        r = e.run()
        assert r.owners_done == r.owners_total == N_OWNERS and r.result == "done", r
        assert r.requests == 4 + 2 * REQ_PER_OWNER == len(e.api.calls), "사라진 닉네임은 ouid 요청 1번으로 끝나야 한다"
        assert [x["stratum"] for x in store.owner_dist_rows(e.conn, r.round_id)] == [3, 3], "사라진 닉네임이 줄을 만들었다"
    with env(gone=GONE) as e:
        e.run(cancel=lambda: len(e.api.calls) >= 4)   # 사라진 넷을 처리하고 멈춘다
        run = store.owner_round_running(e.conn)
        assert store.owner_dist_rows(e.conn, run["round_id"]) == [], "사라진 닉네임이 줄을 만들었다"
        assert len(store.owner_done_hashes(e.conn, run["round_id"])) == 4, "사라진 닉네임이 이어 받기 표시에 안 남았다"
        e.api.calls = []
        r = e.run()
        assert not any(c[0] == "ouid" and c[1] in GONE for c in e.api.calls), "이어 받으며 사라진 닉네임을 다시 물었다"
        assert len(e.api.calls) == 2 * REQ_PER_OWNER and len(store.owner_done_hashes(e.conn, run["round_id"])) == 0 and r.result == "thin"


def _second_snapshot(e, hours=1):
    """같은 순위에 다른 사람(새N)이 선 새 스냅숏 — 랭킹 수집이 12시간마다 찍는 것."""
    rows = [ranker.RankRow(rank=r, profile_sn=5000 + r, nickname=f"새{r}", team_color="팀", elo=3000.0 - r) for r in RANKS]
    return rankcollect.save_snapshot(e.rank, rows, NOW + timedelta(hours=hours))


def _ouid_names(e) -> set[str]:
    return {c[1] for c in e.api.calls if c[0] == "ouid"}   # ouid 호출이 적는 인자는 닉네임이다


def test_resume_keeps_the_snapshot_the_round_started_on():
    """이어 받는 사이 새 스냅숏이 생겨도 같은 사람들을 받는다 — 아니면 앞 층이 두 번 채워지고 뒤 층이 빈다."""
    with env() as e:
        e.run(cancel=lambda: len(e.api.calls) >= 2 * REQ_PER_OWNER)
        run = store.owner_round_running(e.conn)
        assert run["snap_id"] is not None and len(store.owner_done_hashes(e.conn, run["round_id"])) == 2
        first = _ouid_names(e)
        _second_snapshot(e)
        # 전제: 막지 않았다면 — 같은 시드라도 새 스냅숏에서 뽑으면 전혀 다른 사람들이다
        assert all(t.nickname.startswith("새") for t in od.plan_round(e.rank, run["seed"])), "전제: 새 스냅숏이 마지막이어야 한다"
        e.api.calls = []
        r = e.run()
        assert r.result == "done" and r.owners_done == N_OWNERS, r
        assert not any(n.startswith("새") for n in _ouid_names(e)), "이어 받으며 새 스냅숏의 다른 사람을 받았다"
        assert not (_ouid_names(e) & first), "앞서 끝낸 사람을 다시 받았다"
        assert sorted(x["stratum"] for x in store.owner_dist_rows(e.conn, r.round_id)) == [1, 1, 2, 2, 3, 3], "층 균형이 깨졌다"
        e.now = NOW + timedelta(days=config.OWNER_DIST_ROUND_DAYS, seconds=1)   # 다음 바퀴는 새 스냅숏에서 시작한다
        e.api.calls = []
        e.run(cancel=lambda: len(e.api.calls) >= 1)
        assert store.owner_round_running(e.conn)["snap_id"] != run["snap_id"]


def test_resume_without_a_usable_snapshot_starts_a_fresh_round():
    """스냅숏 id 가 없는 옛 줄 · 그 스냅숏의 원본이 지워진 줄은 이어 붙이지 않고 off 로 끝내 바로 새로 연다."""
    for how in ("legacy", "pruned"):
        with env() as e:
            e.run(cancel=lambda: len(e.api.calls) >= 2 * REQ_PER_OWNER)
            old = store.owner_round_running(e.conn)
            if how == "legacy":
                e.conn.execute("UPDATE owner_dist_state SET snap_id = NULL")
                e.conn.commit()
            else:
                e.rank.execute("DELETE FROM snapshot_rows WHERE snapshot_id = ?", (old["snap_id"],))
                e.rank.commit()
            _second_snapshot(e)
            e.api.calls = []
            r = e.run()
            assert r.result == "done" and r.round_id != old["round_id"], (how, r)
            ended = e.conn.execute("SELECT result FROM owner_dist_state WHERE round_id = ?", (old["round_id"],)).fetchone()
            assert ended["result"] == "off", (how, dict(ended))
            assert store.owner_done_hashes(e.conn, old["round_id"]) == set() and store.owner_dist_rows(e.conn, old["round_id"]) == []
            assert _ouid_names(e) and all(n.startswith("새") for n in _ouid_names(e)), how


def test_recreated_rank_db_with_reused_snapshot_id_is_not_resumed():
    """snapshots.id 는 AUTOINCREMENT 가 아니라 "수집 기록 지우기" 뒤엔 1부터 다시 매겨진다 — id 만 대조하면 다른 사람들이 이어 붙는다."""
    with env() as e:
        e.run(cancel=lambda: len(e.api.calls) >= 2 * REQ_PER_OWNER)
        old = store.owner_round_running(e.conn)
        e.rank.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(e.dir / "rank.db") + suffix)
            if p.exists():
                p.unlink()
        e.rank = rankcollect.open_rank_db(e.dir / "rank.db")
        new_id = _second_snapshot(e)
        assert new_id == old["snap_id"], "전제: 새 DB 의 첫 스냅숏이 같은 번호를 받아야 한다"
        e.api.calls = []
        r = e.run()
        assert r.result == "done" and r.round_id != old["round_id"], r
        assert _ouid_names(e) and all(n.startswith("새") for n in _ouid_names(e))
        assert sorted(x["stratum"] for x in store.owner_dist_rows(e.conn, r.round_id)) == [1, 1, 2, 2, 3, 3], "층 균형이 깨졌다"


def test_unopenable_rank_db_keeps_the_running_round():
    with env() as e:
        e.run(cancel=lambda: len(e.api.calls) >= 2 * REQ_PER_OWNER)
        old = store.owner_round_running(e.conn)
        r = od.run_round(e.db, None, lambda: e.api, sleep=e.clock.sleep, clock=e.clock.now, now_fn=lambda: e.now)
        assert r.no_sample, r
        assert store.owner_round_running(e.conn)["round_id"] == old["round_id"], "한 번 못 연 것으로 진행분을 버렸다"
        assert len(store.owner_done_hashes(e.conn, old["round_id"])) == 2


def test_old_db_gets_the_snap_id_column():
    with tempfile.TemporaryDirectory() as d:
        import sqlite3
        p = Path(d) / "old.db"
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE owner_dist_state (round_id INTEGER PRIMARY KEY AUTOINCREMENT, seed INTEGER NOT NULL,"
                  " started_at TEXT NOT NULL, ended_at TEXT, result TEXT, salt TEXT, req_count INTEGER NOT NULL DEFAULT 0)")
        c.execute("INSERT INTO owner_dist_state (seed, started_at, salt) VALUES (1, '2026-10-01T00:00:00', 'x')")
        c.commit()
        c.close()
        conn = store.open_db(p)
        try:
            assert store.owner_round_running(conn)["snap_id"] is None, "옛 줄은 snap_id 가 비어 있어야 한다(→ 새 바퀴로 다시 시작)"
        finally:
            conn.close()


def test_transient_failure_does_not_finish_the_owner():
    """네트워크·5xx·키 거절·깨진 응답은 그 사람 탓이 아니다 — 끝낸 걸로 치면 바퀴가 몇 요청 만에 thin 으로 끝나 14일을 기다린다."""
    errs = {
        "network": NexonAPIError("네트워크 오류: url=/id?nickname=%EA%B5%AC%EB%8B%A8%EC%A3%BC1 구단주1", code=nexon_api.NETWORK_CODE),
        "key": NexonAPIError("키", code="OPENAPI00005", status=401),
        "5xx": NexonAPIError("서버", code="OPENAPI00001", status=500),
        "json": ValueError("깨진 JSON"),
        "shape": AttributeError("dict 가 아니다"),
    }
    for call_no, where in ((1, "ouid"), (2, "목록")):
        for name, err in errs.items():
            with env() as e:
                e.api.errors = {call_no: err}
                r = e.run()
                tag = (where, name)
                assert r.result is None and r.owners_done == 0 and r.error, (tag, r)
                assert "구단주" not in r.error and "nickname" not in r.error and "%EA" not in r.error, ("오류 글에 남의 닉네임이 실렸다", tag, r.error)
                run = store.owner_round_running(e.conn)
                assert run is not None and store.owner_done_hashes(e.conn, run["round_id"]) == set(), tag
                assert run["req_count"] == len(e.api.calls) == r.requests, ("바퀴 계수가 요청과 다르다", tag, run["req_count"], len(e.api.calls))
                assert store.budget_used(e.conn, NOW.date().isoformat(), store.BUDGET_OWNER_DIST) == len(e.api.calls), tag
                e.api.errors, e.api.calls = {}, []
                r2 = e.run()
                assert r2.result == "done" and len(store.owner_dist_rows(e.conn, r2.round_id)) == N_OWNERS, (tag, r2)


def test_one_failed_match_is_skipped_but_all_failed_is_transient():
    with env() as e:   # 호출 순번 1=ouid · 2~4=목록 · 5~11=첫 구단주의 상세 7건
        e.api.errors = {5: ValueError("깨진 JSON")}
        r = e.run()
        assert r.result == "done", r
        games = sorted(x["n_games"] for x in store.owner_dist_rows(e.conn, r.round_id))
        assert games == [6] + [7] * (N_OWNERS - 1), "한 경기 실패가 그 사람 전체나 이번 실행을 날렸다"
        assert store.budget_used(e.conn, NOW.date().isoformat(), store.BUDGET_OWNER_DIST) == len(e.api.calls)
    with env() as e:
        e.api.errors = {n: NexonAPIError("끊김", code=nexon_api.NETWORK_CODE) for n in range(5, 5 + 7)}
        r = e.run()
        assert r.result is None and r.owners_done == 0 and r.error, "상세가 전부 실패했는데 경기 없는 사람으로 쳐서 끝냈다"
        assert store.owner_done_hashes(e.conn, r.round_id) == set()


def test_db_connections_are_reused_across_owners():
    """구단주마다 풀을 새로 만들면 스레드마다 연결이 새로 열려 바퀴가 끝날 때까지 쌓인다(150명이면 수백 개)."""
    opened = []
    real = store.open_db

    def spy(path, *, close_elsewhere=False):
        if close_elsewhere:
            opened.append(1)
        return real(path, close_elsewhere=close_elsewhere)

    store.open_db = spy
    try:
        with env(games=30) as e, _cfg(OWNER_DIST_WORKERS=3, OWNER_DIST_MATCHES=30):   # 기본값(4)이 아닌 값
            assert e.run().result == "done"
    finally:
        store.open_db = real
    assert 0 < len(opened) <= 3 + 1, f"연결 {len(opened)}개 — 작업 스레드 3 + 호출 스레드 1 을 넘으면 구단주마다 새로 연 것이다"


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


def test_an_empty_stratum_makes_the_round_thin():
    """합계만 보면 층 하나가 비어도 done 이 되어 가중이 그 층 인구를 잃는다 — 층마다 OWNER_DIST_MIN_PER_STRATUM."""
    gone = {"구단주1", "구단주2", "구단주3", "구단주4"}   # 1층 전원 → 2층 2 · 3층 2 = 합계 4
    with env(gone=gone) as e, _cfg(OWNER_DIST_MIN_PER_STRATUM=0):
        assert e.run().result == "done", "전제: 층 기준이 없으면 합계 4(= MIN_OWNERS)로 done 이 된다"
    with env(gone=gone) as e:
        assert e.run().result == "thin", "1층이 비었는데 done 이 됐다"
    with env(gone={"구단주1", "구단주2", "구단주3"}) as e:   # 1층 넷 중 둘을 뽑으니 적어도 하나가 사라짐 → 1층 ≤1 < 2 · 합계 ≥4
        assert e.run().result == "thin", "층 하나가 기준 밑인데 done 이 됐다"


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


def test_devkey_counts_only_since_the_day_it_was_turned_on():
    with env() as e:
        old, on = "2026-10-01", NOW.date().isoformat()
        for _ in range(config.OWNER_DIST_SELFCHECK_REQ):
            store.budget_take(e.conn, old, store.BUDGET_OWNER_DIST, None)   # 옛 키(또는 앞서 켰던 때)의 누적
        store.budget_mark_429(e.conn, on, store.BUDGET_OWNER_DIST)
        assert not od.devkey_suspected(e.conn), "기준일 없이 재면 옛 누적이 서비스 키로 보이게 한다(대조군)"
        assert od.devkey_suspected(e.conn, on), "켠 날 이후 누적이 0 인데 429 — 옛 키의 누적이 새 키를 가렸다"


def test_should_run_needs_running_or_due_and_no_429_today():
    with env() as e:
        assert od.should_run(e.conn, NOW), "아무 바퀴도 없으면 첫 바퀴 차례"
        r = e.run()
        assert r.result == "done"
        assert not od.should_run(e.conn, NOW + timedelta(days=config.OWNER_DIST_ROUND_DAYS - 1)), "직전 종료 뒤 주기 전에 띄웠다"
        later = NOW + timedelta(days=config.OWNER_DIST_ROUND_DAYS)
        assert od.should_run(e.conn, later), "주기가 지났는데 안 띄웠다"
        store.owner_round_start(e.conn, 1, NOW.isoformat(), "s")   # 진행 바퀴 — 주기와 무관하게 이어 받는다
        assert od.should_run(e.conn, NOW + timedelta(days=1))
        store.budget_mark_429(e.conn, later.date().isoformat(), store.BUDGET_OPENAPI)
        assert not od.should_run(e.conn, later), "그날 다른 로더의 429 표시가 있는데 띄웠다"


def _ended(conn, result, started):
    rid = store.owner_round_start(conn, 1, started, "s")
    store.owner_round_end(conn, rid, result, started)


def test_off_reason_after_fail_streak_since_on_day_skipping_off():
    with env() as e:
        on = "2026-10-05"
        _ended(e.conn, "aborted", "2026-10-01T00:00:00")          # 켜기 전 — 안 센다
        _ended(e.conn, "dropped", "2026-10-05T00:00:00")
        _ended(e.conn, "off", "2026-10-06T00:00:00")              # 끊김(토글·스냅숏) — 실패도 성공도 아님
        _ended(e.conn, "aborted", "2026-10-07T00:00:00")
        fail = od.RoundResult(result="aborted")
        assert od.off_reason_after(e.conn, fail, on) == "", "켜기 전 바퀴까지 셌거나 실패 두 번에 껐다"
        assert od.off_reason_after(e.conn, fail, "2026-09-01") == "fail", "켜기 전 바퀴도 세면 셋 — 대조군"
        _ended(e.conn, "dropped", "2026-10-08T00:00:00")
        assert od.off_reason_after(e.conn, od.RoundResult(result="dropped"), on) == "fail", "off 를 건너 이어진 실패 셋인데 안 껐다"
        assert od.off_reason_after(e.conn, od.RoundResult(result="done"), on) == "", "실패가 아닌 결과에서 껐다"
        _ended(e.conn, "done", "2026-10-09T00:00:00")
        _ended(e.conn, "aborted", "2026-10-10T00:00:00")
        assert od.off_reason_after(e.conn, fail, on) == "", "done 이 연속을 끊지 않았다"
        with _cfg(OWNER_DIST_FAIL_MAX=1):
            assert od.off_reason_after(e.conn, fail, on) == "fail", "실패 한도가 설정과 이어져 있지 않다"


def test_off_reason_devkey_only_on_quota():
    with env() as e:
        day = NOW.date().isoformat()
        store.budget_mark_429(e.conn, day, store.BUDGET_OWNER_DIST)
        assert od.off_reason_after(e.conn, od.RoundResult(quota=True), day) == "devkey"
        assert od.off_reason_after(e.conn, od.RoundResult(), day) == "", "429 로 멈추지 않은 호출에서 개발 키로 껐다"


def test_purge_helper_skips_when_nothing_and_keeps_done_on_toggle_off():
    with env() as e:
        assert not od.purge(e.db, everything=True, now=NOW), "지울 게 없는데 썼다"
        r = e.run()
        assert not od.purge(e.db, everything=False, now=NOW), "진행 바퀴가 없는데 토글 끔이 무언가 지웠다"
        assert len(store.owner_dist_rows(e.conn, r.round_id)) == N_OWNERS
        rid = store.owner_round_start(e.conn, 1, NOW.isoformat(), "s")
        assert od.purge(e.db, everything=False, now=NOW)
        assert store.owner_round_running(e.conn) is None and len(store.owner_dist_rows(e.conn, r.round_id)) == N_OWNERS, \
            "토글 끔이 진행 바퀴를 안 끝냈거나 done 분포를 지웠다"
        assert store.owner_round_last_end(e.conn)["round_id"] == rid
        assert od.purge(e.db, everything=True, now=NOW) and store.owner_dist_rows(e.conn, r.round_id) == []
        assert not store.owner_dist_has_data(e.conn)


def test_rank_open_is_closed_before_fetching():
    with env() as e:
        state = {"opened": 0, "closed": 0, "seen_open": False}

        class _Rank:
            def __init__(self, real):
                self.real = real

            def execute(self, *a):
                return self.real.execute(*a)

            def close(self):
                state["closed"] += 1

        def opener():
            state["opened"] += 1
            return _Rank(e.rank)

        def hook(name, n):
            if name == "detail" and state["closed"] == 0:
                state["seen_open"] = True
        e.api.hook = hook
        r = od.run_round(e.db, None, lambda: e.api, sleep=e.clock.sleep, clock=e.clock.now, now_fn=lambda: e.now,
                         rank_open=opener)
        assert r.result == "done" and state["opened"] == 1, (r, state)
        assert not state["seen_open"], "경기를 받는 동안 rank.db 를 붙잡고 있었다 — [수집 기록 지우기]의 파일 삭제를 막는다"
        assert state["closed"] >= 1


def test_status_text_lines():
    with env() as e:
        assert "아직" in od.status_text(e.conn, NOW)
        rid = store.owner_round_start(e.conn, 1, NOW.isoformat(), "s")
        store.owner_done_add(e.conn, rid, "h1")
        t = od.status_text(e.conn, NOW)
        assert f"받는 중 1/{config.OWNER_DIST_PER_STRATUM * len(config.OWNER_DIST_STRATA)}명" in t, t
        store.purge_owner_dist(e.conn, everything=False, now=NOW.isoformat())
        r = e.run()
        t = od.status_text(e.conn, NOW)
        assert "최근 바퀴 완료" in t and f"구단주 {N_OWNERS}명" in t, t
        keep = (config.OWNER_DIST_ON, config.OWNER_DIST_OFF)
        try:
            config.OWNER_DIST_ON, config.OWNER_DIST_OFF = "", "devkey"
            assert "개발 단계 키" in od.status_text(e.conn, NOW)
            config.OWNER_DIST_ON = "2026-10-13"
            assert "개발 단계 키" not in od.status_text(e.conn, NOW), "다시 켰는데 옛 꺼진 사유를 보였다"
        finally:
            config.OWNER_DIST_ON, config.OWNER_DIST_OFF = keep
        assert r.round_id


def test_no_snapshot_starts_nothing():
    with env() as e:
        e.rank.close()
        with tempfile.TemporaryDirectory() as d:
            e.rank = rankcollect.open_rank_db(Path(d) / "empty.db")
            r = e.run()
            e.rank.close()
            e.rank = _rank_db(e._d.name)
        assert r.no_sample and store.owner_round_running(e.conn) is None and not e.api.calls


# ── 조각 ② 분포 읽기 · 백분위 ────────────────────────────────────────────────

E = "2026-10-01T09:00:00"     # 바퀴 종료 시각
DIST_CFG = dict(OWNER_DIST_STRATA_POP=(10, 20, 30), OWNER_DIST_MIN_OWNERS=6, OWNER_DIST_MIN_PER_STRATUM=1,
                OWNER_DIST_MIN_GAMES=5, OWNER_DIST_MAX_AGE_DAYS=30, OWNER_DIST_STALE_DAYS=45)   # 가중은 기본값(200·800·2000)이 아닌 값


def _orow(stratum, finish, last=E, n_games=9, **kw):
    r = {"stratum": stratum, "n_games": n_games, "last_match": last, "finish": finish}
    for k in store.OWNER_METRICS:
        r.setdefault(k, kw.get(k, 0.0 if k != "finish" else finish))
    return r


DIST_ROWS = [_orow(1, 1.0), _orow(1, 3.0), _orow(2, 2.0), _orow(3, 2.0), _orow(3, 4.0), _orow(3, 6.0)]


def test_pct_is_stratum_weighted_with_half_ties():
    with _cfg(**DIST_CFG):
        d = od.build_dist(DIST_ROWS, 7, E)
        assert d is not None and d.n_owners == 6
        # 층 가중: 1층 값마다 10/2=5 · 2층 20 · 3층 값마다 30/3=10, 합 60
        assert abs(d.pct("finish", 1.5) - 100 * 5 / 60) < 1e-9, "층 가중이 아니다(가중 없이 세면 16.7)"
        assert abs(d.pct("finish", 2.0) - 100 * (5 + 30 / 2) / 60) < 1e-9, "같은 값은 반만 세야 한다"
        assert d.pct("finish", -1) == 0 and d.pct("finish", 99) == 100
        assert d.pct("없는지표", 0) is None


def test_distribution_filters_by_round_end_not_today():
    old = [dict(r, last_match="2026-09-11T09:00:00") for r in DIST_ROWS]   # 종료 20일 전 경기
    now = datetime(2026, 11, 10, 9, 0, 0)                                   # 종료 40일 뒤에 읽는다
    with _cfg(**DIST_CFG):
        assert not any(od.eligible(r, now) for r in old), "전제: 읽는 날로 거르면 전원 빠진다"
        d = od.build_dist(old, 7, E)
        assert d is not None and d.n_owners == 6, "읽는 날로 걸러 낡아 가는 분포에서 구단주가 빠졌다"
        assert od.build_dist([dict(r, last_match="2026-08-31T09:00:00") for r in DIST_ROWS], 7, E) is None, \
            "종료 시각 기준 30일 넘은 경기도 남았다"


def test_distribution_needs_every_stratum_and_metric_falls_back_alone():
    with _cfg(**DIST_CFG):
        assert od.build_dist([r for r in DIST_ROWS if r["stratum"] != 2], 7, E) is None, "층 하나가 빈 분포를 썼다"
        with _cfg(OWNER_DIST_MIN_OWNERS=5):
            assert od.build_dist([r for r in DIST_ROWS if r["stratum"] != 2], 7, E) is None, "합계가 넘어도 빈 층이면 안 된다"
        rows = [dict(r, tackle_rate=None) if r["stratum"] == 2 else r for r in DIST_ROWS]
        d = od.build_dist(rows, 7, E)
        assert d is not None and d.pct("finish", 2.0) is not None, "지표 하나 때문에 분포 전체를 버렸다"
        assert d.pct("tackle_rate", 0.0) is None, "층이 빈 지표를 썼다"
        rows = [dict(r, shots=None) if i == 0 else r for i, r in enumerate(DIST_ROWS)]
        assert od.build_dist(rows, 7, E).pct("shots", 0.0) is None, "구단주가 MIN_OWNERS 밑인 지표를 썼다"
        assert od.build_dist([dict(r, n_games=4) if i == 0 else r for i, r in enumerate(DIST_ROWS)], 7, E) is None, \
            "판수 모자란 구단주를 셌다"


def _dist_db(d):
    c = store.open_db(Path(d) / "fifa.db")
    rid = store.owner_round_start(c, 1, "2026-09-28T00:00:00", "s")
    for i, r in enumerate(DIST_ROWS):
        store.owner_dist_add(c, rid, f"h{i}", r)
    store.owner_round_end(c, rid, "done", E)
    return c, rid


def test_load_dist_takes_latest_done_and_goes_stale():
    with tempfile.TemporaryDirectory() as d, _cfg(**DIST_CFG):
        c, rid = _dist_db(d)
        try:
            base = datetime.fromisoformat(E)
            assert od.load_dist(c, base).round_id == rid
            r2 = store.owner_round_start(c, 2, "2026-10-05T00:00:00", "t")
            store.owner_round_end(c, r2, "thin", "2026-10-06T00:00:00")
            assert od.load_dist(c, base).round_id == rid, "뒤에 온 thin 이 done 분포를 가렸다"
            edge = base + timedelta(days=config.OWNER_DIST_STALE_DAYS)
            assert od.load_dist(c, edge) is not None, "정확히 45일은 아직 쓴다"
            assert od.load_dist(c, edge + timedelta(seconds=1)) is None, "45일 넘게 낡은 분포를 썼다"
            with _cfg(OWNER_DIST_STALE_DAYS=10):
                assert od.load_dist(c, base + timedelta(days=11)) is None, "낡음 기준이 설정과 이어져 있지 않다"
        finally:
            c.close()
    with tempfile.TemporaryDirectory() as d:
        c = store.open_db(Path(d) / "fifa.db")
        try:
            assert od.load_dist(c, datetime.fromisoformat(E)) is None, "분포가 없는데 무언가를 돌려줬다"
        finally:
            c.close()


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
