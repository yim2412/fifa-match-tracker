"""선수 색인(12) · 랭커 픽 받기·지우기(6) 테스트 — 지어낸 경기·가짜 넥슨 API 로, 네트워크 없이.

pytest 없이 `python tests/test_rankerpick.py`. 닉네임·ouid·경기 id·카드 번호는 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import rankerpick as rp
import store
from nexon_api import NETWORK_CODE, QUOTA_CODE, NexonAPIError

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 7, 12, 0, 0)
TODAY = NOW.date()
FORMATION = [0, 3, 5, 7, 10, 13, 15, 23, 25, 27, 9]   # GK · RB CB LB · CDM · RCM LCM · RW ST LW · (9 = RDM)


def _match(mid: str, day: date, sides: list[tuple[str, str, list[int]]], match_type: int = config.DEFAULT_MATCH_TYPE,
           sub: bool = True) -> dict:
    """sides = [(ouid, nickname, spid 11개)] — 포지션은 FORMATION 순서, 강화는 spid 끝자리."""
    info = []
    for ouid, nick, spids in sides:
        players = [{"spId": s, "spPosition": po, "spGrade": 1 + s % 8} for s, po in zip(spids, FORMATION)]
        if sub:
            players.append({"spId": 999000001, "spPosition": 28, "spGrade": 1})
        info.append({"ouid": ouid, "nickname": nick, "player": players})
    return {"matchId": mid, "matchType": match_type, "matchDate": f"{day.isoformat()}T10:00:00", "matchInfo": info}


def _squad(base: int) -> list[int]:
    return [base + i for i in range(11)]


def _db():
    return store.open_db(":memory:")


def _owners(conn, spid, days=30):
    return store.owners_using_card(conn, spid, TODAY.toordinal() - days)


# ── 12 색인 ────────────────────────────────────────────────────────────

def test_save_indexes_squads():
    conn = _db()
    good = _match("m1", TODAY, [("o1", "가", _squad(100)), ("o2", "나", _squad(200))])
    forfeit = _match("m2", TODAY, [("o1", "가", []), ("o2", "나", [])])
    broken = dict(_match("m3", TODAY, [("o1", "가", _squad(300))]), matchDate="깨짐")
    other = _match("m4", TODAY, [("o1", "가", _squad(400))], match_type=50)
    assert store.save_matches(conn, [good, forfeit, broken, other]) == 4, "색인 실패가 경기 저장을 막으면 안 된다"
    assert conn.execute("SELECT COUNT(*) FROM squad_match").fetchone()[0] == 4, "몰수·깨진·다른 종류도 훑은 줄은 남긴다"
    assert conn.execute("SELECT COUNT(*) FROM match_squads").fetchone()[0] == 22, "선발만(28 제외) · 감독모드만"
    o = _owners(conn, 100)
    assert [(x["ouid"], x["nickname"], x["games"]) for x in o] == [("o1", "가", 1)], o
    assert o[0]["positions"] == {0: 1} and o[0]["grades"] == {1 + 100 % 8: 1}
    assert _owners(conn, 999000001) == [], "교체 명단은 색인에 없다"
    assert _owners(conn, 300) == [] and _owners(conn, 400) == []
    assert store.unindexed_count(conn) == (0, 4)


def test_index_keeps_latest_nickname():
    conn = _db()
    store.save_matches(conn, [_match("a", TODAY, [("o1", "새이름", _squad(100))]),
                              _match("b", TODAY - timedelta(days=3), [("o1", "옛이름", _squad(100))])])
    o = _owners(conn, 100)
    assert o[0]["nickname"] == "새이름" and o[0]["games"] == 2, o
    assert o[0]["last_day"] == TODAY.toordinal()


def test_owner_lookup_window_and_grade():
    conn = _db()
    store.save_matches(conn, [_match("a", TODAY - timedelta(days=40), [("old", "옛", _squad(100))]),
                              _match("b", TODAY, [("new", "새", _squad(100))])])
    assert [x["ouid"] for x in _owners(conn, 100)] == ["new"], "30일 밖은 빠진다"
    assert [x["ouid"] for x in _owners(conn, 100, 50)] == ["new", "old"], "최근 사용 순"
    g = 1 + 100 % 8
    assert store.owners_using_card(conn, 100, 0, grade=g + 1) == []
    assert len(store.owners_using_card(conn, 100, 0, grade=g)) == 2


def _raw_insert(conn, details):
    """옛 버전이 저장한 것처럼 — 색인 없이 matches 만."""
    import json
    for d in details:
        conn.execute("INSERT INTO matches (match_id, match_type, match_date, payload) VALUES (?, ?, ?, ?)",
                     (d["matchId"], d["matchType"], d["matchDate"], json.dumps(d)))
    conn.commit()


def test_backfill_resumes_and_skips_indexed():
    conn = _db()
    ds = [_match(f"m{i}", TODAY, [(f"o{i}", f"n{i}", _squad(1000 + i))]) for i in range(7)]
    store.save_matches(conn, ds[:2])            # 새 버전이 저장(색인됨)
    _raw_insert(conn, ds[2:])                   # 옛 버전이 저장
    _raw_insert(conn, [{"matchId": "junk", "matchType": 52, "matchDate": "x", "matchInfo": "x"}])
    assert store.unindexed_count(conn) == (6, 8)
    calls = []
    n = store.backfill_squads(conn, stop=lambda: len(calls) >= 1, on_batch=calls.append, batch=2)
    assert n == 2 and store.unindexed_count(conn)[0] == 4, "묶음 하나 뒤 멈춤 — 커밋된 만큼 남는다"
    n = store.backfill_squads(conn, batch=2)
    assert n == 4 and store.unindexed_count(conn) == (0, 8), "다음 실행이 이어서(깨진 본문도 훑은 줄)"
    assert conn.execute("SELECT COUNT(*) FROM match_squads").fetchone()[0] == 7 * 11
    assert store.backfill_squads(conn) == 0, "다 했으면 0"


def test_backfill_terminates_when_row_cannot_be_written():
    """squad_match 줄을 못 쓰는 경기만 남으면 — 같은 걸 다시 집어 끝나지 않으면 안 된다."""
    conn = _db()
    _raw_insert(conn, [_match("m1", TODAY, [("o", "n", _squad(1))])])
    conn.execute("CREATE TRIGGER no_sq BEFORE INSERT ON squad_match BEGIN SELECT RAISE(ABORT, 'x'); END")
    batches = []
    # 끝나지 않으면 멈춤 대신 실패로 — 묶음이 넘치면 stop 으로 끊고 아래 단언이 빨개진다(2026-10-06 변이가 10분 멈췄다)
    store.backfill_squads(conn, batch=10, stop=lambda: len(batches) > 5, on_batch=batches.append)
    assert len(batches) <= 1, f"같은 경기를 {len(batches)}번 다시 집었다"


def _synthetic(n: int) -> list[dict]:
    rnd = random.Random(7)
    pool = list(range(100000000, 100003000))
    out = []
    for i in range(n):
        d = TODAY - timedelta(days=i % 60)
        out.append(_match(f"s{i:06d}", d, [(f"me", "나", rnd.sample(pool, 11)),
                                            (f"op{i % 9000}", f"상대{i % 9000}", rnd.sample(pool, 11))]))
    return out


_SYN: list = []


def _syn_db():
    if not _SYN:
        conn = _db()
        _raw_insert(conn, _synthetic(20000))
        _SYN.append(conn)
    return _SYN[0]


def test_backfill_budget():
    """2만 경기 백필 ≤ 10초 CPU(ROADMAP ⑦) — 실제 DB 사본 2.1만 경기 4.2초(2026-10-06)."""
    conn = _syn_db()
    c = time.process_time()
    store.backfill_squads(conn)
    spent = time.process_time() - c
    assert store.unindexed_count(conn)[0] == 0
    assert spent <= 10, f"{spent:.1f}s"
    print(f"       백필 2만 경기 CPU {spent:.2f}s")


def test_owner_lookup_budget():
    """찾기 조회 ≤ 50ms(⑦ · 실제 사본 가장 많이 쓰인 카드 4.6ms)."""
    conn = _syn_db()
    if store.unindexed_count(conn)[0]:
        store.backfill_squads(conn)
    spid = conn.execute("SELECT spid FROM match_squads GROUP BY spid ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    best = min(_timed(lambda: store.owners_using_card(conn, spid, TODAY.toordinal() - 30)) for _ in range(3))
    assert best <= 0.05, f"{best * 1000:.1f}ms"


def _timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


# ── 6 랭커 픽 받기 ─────────────────────────────────────────────────────

class FakeAPI:
    """닉네임 → (ouid, 최근 경기 상세). 요청마다 calls 에 적는다. errors: 요청 순번(1부터) → 예외."""

    def __init__(self, players: dict[str, tuple[str, dict | None]]):
        self.players = players
        self.calls: list[tuple[str, object, int]] = []
        self.errors: dict[int, Exception] = {}
        self.cache: dict[str, dict] = {}
        self.forgot: list[str] = []

    def _hit(self, name, arg, attempts):
        self.calls.append((name, arg, attempts))
        e = self.errors.get(len(self.calls))
        if e is not None:
            raise e

    def get_ouid(self, nickname, attempts=3):
        self._hit("ouid", nickname, attempts)
        if nickname not in self.players:
            raise NexonAPIError("없음", code="OPENAPI00004", status=400)
        return self.players[nickname][0]

    def get_match_ids(self, ouid, matchtype=50, offset=0, limit=20, attempts=3):
        self._hit("ids", ouid, attempts)
        d = next((d for o, d in self.players.values() if o == ouid), None)
        return [d["matchId"]] if d else []

    def get_match_detail(self, match_id, attempts=3):
        self._hit("detail", match_id, attempts)
        return next(d for _o, d in self.players.values() if d and d["matchId"] == match_id)

    def cached_detail(self, match_id):
        return self.cache.get(match_id)

    def forget_details(self, ids):
        self.forgot.extend(ids)


def _rankers(n: int, day: date = TODAY) -> tuple[list[dict], FakeAPI]:
    targets, players = [], {}
    for i in range(n):
        nick = f"랭커{i}"
        targets.append({"rank": i + 1, "profile_sn": 5000 + i, "nickname": nick, "team_color": "팀A" if i % 2 else "팀B",
                        "formation": "4-1-2-3"})
        players[nick] = (f"r{i}", _match(f"rm{i}", day, [(f"r{i}", nick, _squad(7000 + (i % 3))),
                                                          (f"x{i}", f"상대{i}", _squad(8000))]))
    return targets, FakeAPI(players)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def __call__(self):
        return self.t


def _collect(api, conn, targets, **kw):
    clk = kw.pop("clk", None) or Clock()
    return rp.collect(api, conn, targets, now_fn=kw.pop("now_fn", lambda: NOW), sleep=clk.sleep, clock=clk, **kw)


def test_ranker_pick_collects_and_saves():
    conn = _db()
    targets, api = _rankers(3)
    res = _collect(api, conn, targets)
    assert res.checked == 3 and res.requests == 9, res
    assert all(a == 1 for *_x, a in api.calls), "재시도 끔(attempts=1)"
    assert store.budget_used(conn, TODAY.isoformat(), store.BUDGET_RANKER_PICK) == 9, "실제 요청만큼 계수"
    assert conn.execute("SELECT COUNT(*) FROM ranker_matches").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0, "최근 검색에 안 들어간다(R11)"
    assert len(_owners(conn, 7000)) == 1, "save_matches 그대로 — 12 색인이 같이 잡는다"
    assert conn.execute("SELECT COUNT(*) FROM match_players").fetchone()[0] == 6, "양쪽 ouid"
    # 3일 안이면 다시 안 묻는다
    n = len(api.calls)
    res = _collect(api, conn, targets, now_fn=lambda: NOW + timedelta(days=2))
    assert res.checked == 0 and len(api.calls) == n


def test_ranker_pick_gap_between_requests():
    conn = _db()
    targets, api = _rankers(2)
    clk = Clock()
    _collect(api, conn, targets, clk=clk)
    assert len(clk.slept) == 5 and all(abs(s - config.RANKER_PICK_GAP_S) < 1e-9 for s in clk.slept), clk.slept


def test_ranker_pick_existing_match_costs_nothing():
    conn = _db()
    targets, api = _rankers(2)
    store.save_matches(conn, [api.players["랭커0"][1]])     # 이미 검색으로 받은 경기
    api.cache["rm1"] = api.players["랭커1"][1]               # 한도에 걸려 .cache 에만 있는 경기
    res = _collect(api, conn, targets)
    assert [c[0] for c in api.calls] == ["ouid", "ids", "ouid", "ids"] and res.requests == 4, api.calls
    assert "rm1" in api.forgot


def test_ranker_pick_daily_cap():
    conn = _db()
    targets, api = _rankers(5)
    keep = config.RANKER_PICK_DAILY_REQ
    config.RANKER_PICK_DAILY_REQ = 7
    try:
        res = _collect(api, conn, targets)
        assert res.limit and res.requests == 7 and res.checked == 2, res
        assert store.budget_used(conn, TODAY.isoformat(), store.BUDGET_RANKER_PICK) == 7
        res = _collect(api, conn, targets)
        assert res.limit and res.requests == 0, "재시작해도 계수가 남는다"
    finally:
        config.RANKER_PICK_DAILY_REQ = keep


def test_ranker_pick_429_waits_then_retries_once():
    conn = _db()
    targets, api = _rankers(2)
    api.errors[2] = NexonAPIError("한도", code=QUOTA_CODE, status=429)
    clk = Clock()
    res = _collect(api, conn, targets, clk=clk)
    assert res.checked == 2 and not res.quota, "초당 429 하나로는 안 멈춘다"
    assert sum(1 for s in clk.slept if s == 1.0) == config.RANKER_PICK_429_WAIT_S, "1초씩 쪼개 쉰다"
    assert not store.budget_hit_429(conn, TODAY.isoformat())


def test_ranker_pick_second_429_stops_for_the_day():
    conn = _db()
    targets, api = _rankers(3)
    for i in (2, 3):
        api.errors[i] = NexonAPIError("한도", code=QUOTA_CODE, status=429)
    res = _collect(api, conn, targets)
    assert res.quota and res.checked == 0, res
    assert store.budget_hit_429(conn, TODAY.isoformat())
    n = len(api.calls)
    res = _collect(api, conn, targets)
    assert res.quota and len(api.calls) == n, "그날 다시 시작하지 않는다"
    res = _collect(api, conn, targets, now_fn=lambda: NOW + timedelta(days=1))
    assert not res.quota and res.checked == 3, "다음 날은 다시"


def test_ranker_pick_other_key_counts_separately():
    """개발용 수집(tools/dev_archive.py)은 다른 키 — 계수·상한·429·다시 묻는 간격이 앱 키와 따로다."""
    conn = _db()
    day = TODAY.isoformat()
    targets, api = _rankers(3)
    store.budget_mark_429(conn, day, store.BUDGET_OPENAPI)       # 앱 키가 오늘 429 로 멈췄다
    assert _collect(api, conn, targets).quota, "전제: 앱 쪽 기본값이면 막힌다"
    opts = {"budget_kind": "dev", "daily_cap": 7, "stale_days": 0.5}
    res = _collect(api, conn, targets, **opts)
    assert not res.quota and res.limit and res.requests == 7, f"앱 키의 429 에 안 막히고, 제 상한(7)에서 멈춘다: {res}"
    assert store.budget_used(conn, day, "dev") == 7 and store.budget_used(conn, day, store.BUDGET_RANKER_PICK) == 0
    keep, n = config.RANKER_PICK_DAILY_REQ, len(api.calls)
    config.RANKER_PICK_DAILY_REQ = 0
    try:   # 앱 상한이 바닥이어도 다른 키 쪽은 제 상한을 본다
        res = _collect(api, conn, targets, budget_kind="dev", daily_cap=100, stale_days=0.5)
        assert res.checked == 1 and not res.limit, res
    finally:
        config.RANKER_PICK_DAILY_REQ = keep
    n = len(api.calls)
    res = _collect(api, conn, targets, now_fn=lambda: NOW + timedelta(days=1), budget_kind="dev", daily_cap=100,
                   stale_days=0.5)
    assert res.checked == 3 and len(api.calls) > n, "하루 지나면 다시 묻는다(기본 3일이면 안 묻는다)"
    store.budget_mark_429(conn, (TODAY + timedelta(days=2)).isoformat(), "dev")
    res = _collect(api, conn, targets, now_fn=lambda: NOW + timedelta(days=2))
    assert not res.quota, "다른 키의 429 는 앱을 막지 않는다"
    # 다른 키가 실제로 429 두 번을 받으면 — 표시는 제 종류에만
    conn = _db()
    targets, api = _rankers(2)
    for i in (1, 2):
        api.errors[i] = NexonAPIError("한도", code=QUOTA_CODE, status=429)
    assert _collect(api, conn, targets, budget_kind="dev", daily_cap=100).quota
    assert store.budget_hit_429(conn, day, ("dev",)) and not store.budget_hit_429(conn, day), "앱 쪽 429 표시는 안 남긴다"


def test_ranker_pick_yields_to_final_429_of_other_loaders():
    conn = _db()
    targets, api = _rankers(2)
    store.budget_mark_429(conn, TODAY.isoformat(), store.BUDGET_OPENAPI)
    res = _collect(api, conn, targets)
    assert res.quota and api.calls == [], "내 검색이 먼저 — 요청 0"


def test_ranker_pick_cancel_during_429_wait_is_quick():
    conn = _db()
    targets, api = _rankers(2)
    api.errors[1] = NexonAPIError("한도", code=QUOTA_CODE, status=429)
    clk = Clock()
    res = _collect(api, conn, targets, clk=clk, cancel=lambda: len(clk.slept) >= 3)
    assert res.cancelled and len(clk.slept) <= 3, clk.slept


def test_ranker_pick_failure_recorded_not_retried():
    conn = _db()
    targets, api = _rankers(2)
    del api.players["랭커0"]                                       # 닉네임이 바뀐 사람 — OPENAPI00004
    res = _collect(api, conn, targets)
    row = store.ranker_squads(conn)[5000]
    assert res.checked == 2 and row["fail"] and row["fetched_at"], row
    n = len(api.calls)
    _collect(api, conn, targets, now_fn=lambda: NOW + timedelta(hours=1))
    assert len(api.calls) == n, "실패도 3일 막는다(탭을 열 때마다 다시 시도하지 않게)"


def test_ranker_pick_renamed_retries():
    conn = _db()
    targets, api = _rankers(1)
    _collect(api, conn, targets)
    api.players["새닉"] = api.players.pop("랭커0")
    targets[0]["nickname"] = "새닉"
    n = len(api.calls)
    res = _collect(api, conn, targets, now_fn=lambda: NOW + timedelta(hours=1))
    assert res.checked == 1 and api.calls[n][0:2] == ("ouid", "새닉"), "닉네임이 바뀌면 3일과 무관하게 · ouid 다시"


def test_ranker_pick_network_error_stops_without_marking():
    conn = _db()
    targets, api = _rankers(2)
    api.errors[1] = NexonAPIError("네트워크 오류", code=NETWORK_CODE)
    res = _collect(api, conn, targets)
    assert res.error and res.checked == 0 and store.ranker_squads(conn) == {}, "연결이 없으면 실패로 적지 않는다"


def test_ranker_pick_refreshes_fetched_on():
    conn = _db()
    targets, api = _rankers(1)
    _collect(api, conn, targets)
    later = NOW + timedelta(days=4)
    _collect(api, conn, targets, now_fn=lambda: later)
    got = conn.execute("SELECT fetched_on FROM ranker_matches").fetchone()[0]
    assert got == later.date().isoformat(), "같은 경기를 다시 확인하면 오늘로 — 14일 정리에 안 지워지게"


def test_pick_days_add_once_by_match_day_only_pick_top():
    """N15(2.3.1) — 날짜 = 마지막 경기 날짜 · 출처 PICK 상위 200만 · 안 센 경기만 더한다 · 14일보다 옛 경기는 안 센다."""
    import rankcollect
    with tempfile.TemporaryDirectory() as d:
        rconn = rankcollect.open_rank_db(Path(d) / "rank.db")
        try:
            conn = _db()
            day1 = TODAY - timedelta(days=1)
            targets, api = _rankers(3, day1)
            _collect(api, conn, targets)
            # 추천(201~1,000위) 랭커 — 선발이 있는 진짜 경기라 출처로만 걸러진다
            store.save_matches(conn, [_match("rec1", day1, [("rx", "추천", _squad(7200)), ("y", "상대", _squad(8000))])])
            store.save_ranker_squad(conn, 9999, nickname="추천", ouid="rx", rank=500, match_id="rec1", match_day=day1.isoformat(),
                                    fetched_at=NOW.isoformat(), fail=None, source=store.RECOMMEND)
            # 지금은 상위 200 안이지만 줄은 예전에 추천으로 받은 것(3일 안이라 PICK 이 아직 다시 안 받음) — 출처로 걸러진다
            mixed = targets + [{"rank": 150, "profile_sn": 9999, "nickname": "추천", "team_color": "팀C"}]
            assert rp.record_pick_days(rconn, conn, mixed, TODAY) == 3
            rows = {(r["day"], r["kind"], r["key"]): (r["n"], r["rankers"]) for r in rconn.execute("SELECT * FROM pick_days")}
            assert {k[0] for k in rows} == {day1.isoformat()}, "받은 날로 셌다"
            assert rows[(day1.isoformat(), "card", "7005")] == (3, 3) and rows[(day1.isoformat(), "card", "7000")] == (1, 3)
            assert rows[(day1.isoformat(), "team_color", "팀B")] == (2, 3), rows
            assert not any(k[2] == "팀C" for k in rows), "추천 출처(201~1,000위)가 모집단에 섞였다"
            assert all(v[1] == 3 for v in rows.values()), "그날 줄의 분모가 다르다"
            for k in (1, 2):                                       # 같은 스쿼드를 사흘 — 다시 안 센다
                assert rp.record_pick_days(rconn, conn, targets, TODAY + timedelta(days=k)) == 0
            # 다음 날 또 경기한 랭커 — 그 날짜에 더하고 앞 날짜는 그대로(덮어쓰면 빠졌다 — 2회차 A)
            api.players["랭커0"] = ("r0", _match("rm0b", TODAY, [("r0", "랭커0", _squad(7100)), ("x0", "상대0", _squad(8000))]))
            _collect(api, conn, targets[:1], now_fn=lambda: NOW + timedelta(days=4))
            assert rp.record_pick_days(rconn, conn, targets, TODAY) == 1
            again = {(r["day"], r["kind"], r["key"]): (r["n"], r["rankers"]) for r in rconn.execute("SELECT * FROM pick_days")}
            assert again[(day1.isoformat(), "card", "7005")] == (3, 3), "앞 날짜에서 빠졌다"
            assert again[(TODAY.isoformat(), "card", "7100")] == (1, 1)
            # 14일보다 옛 경기는 안 센다 — 센 표시(pick_counted)가 14일 뒤 지워지면 다시 +1 이 된다
            old_t, old_api = _rankers(1, TODAY - timedelta(days=config.RANK_RAW_KEEP_DAYS + 1))
            old_t[0]["profile_sn"], old_t[0]["nickname"] = 6000, "옛랭커"
            old_api.players = {"옛랭커": old_api.players["랭커0"]}
            conn2 = _db()
            _collect(old_api, conn2, old_t)
            assert rp.record_pick_days(rconn, conn2, old_t, TODAY) == 0
        finally:
            rconn.close()


def test_ranker_pick_summary():
    conn = _db()
    targets, api = _rankers(4)
    api.players["랭커3"] = ("r3", _match("rm3", TODAY - timedelta(days=30), [("r3", "랭커3", _squad(9000))]))
    _collect(api, conn, targets)
    targets.append({"rank": 5, "profile_sn": 5999, "nickname": "안받음", "team_color": "", "formation": ""})
    s = rp.ranker_pick_summary(conn, "t", targets, today=TODAY)
    assert (s.total, s.used, s.old, s.pending) == (5, 3, 1, 1), s
    assert s.colors["팀A"] == 2 and s.colors["없음"] == 1
    assert abs(s.rate[7000] - 1 / 3) < 1e-9 and 9000 not in s.rate, "오래된 경기는 집계에서 뺀다"
    gk = dict(s.lines)["GK"]
    assert {c.spid: c.users for c in gk} == {7000: 1, 7001: 1, 7002: 1}
    conn.execute("DELETE FROM matches WHERE match_id = 'rm0'")
    s = rp.ranker_pick_summary(conn, "t", targets, today=TODAY)
    assert s.refetch == 1 and s.used == 2


def test_ranker_pick_render_budget():
    """⑦ 랭커 픽 집계 ≤ 0.3초 — 200명 × 선발 11장(경기 본문을 DB 에서 읽어 해석하는 데까지)."""
    conn = _db()
    targets, api = _rankers(config.RANKER_PICK_TOP)
    keep = config.RANKER_PICK_DAILY_REQ
    config.RANKER_PICK_DAILY_REQ = 10 ** 6   # 받기는 200명 × 3요청 = 600 — 하루 상한(300)은 여기서 재는 게 아니다
    try:
        _collect(api, conn, targets)
    finally:
        config.RANKER_PICK_DAILY_REQ = keep
    assert conn.execute("SELECT COUNT(*) FROM ranker_matches").fetchone()[0] == config.RANKER_PICK_TOP
    best = min(_timed(lambda: rp.ranker_pick_summary(conn, "t", targets, today=TODAY)) for _ in range(3))
    s = rp.ranker_pick_summary(conn, "t", targets, today=TODAY)
    assert s.used == config.RANKER_PICK_TOP, s.used
    assert best <= 0.3, f"{best * 1000:.0f}ms"
    print(f"       랭커 픽 집계 200명 {best * 1000:.0f}ms")


# ── 11-lite 추천(17단계) ────────────────────────────────────────────────

def _snap_row(rank, nick, color="팀A", value=None):
    return {"rank": rank, "profile_sn": 6000 + rank, "nickname": nick, "team_color": color, "formation": "4-2-3-1",
            "team_value": value}


def test_owner_finder_dup_name_label():
    # 선수로 구단주 찾기 — 스냅숏 쪽만 엠블럼을 붙여 화면 콤보와 같은 글자("Spartan · 강화")로. top_rankers 는 이름 그대로
    # (랭커 픽 비율·추천이 쓰는 영구 집계 키를 안 바꾼다)
    import rankcollect
    import ranker
    with tempfile.TemporaryDirectory() as d:
        rc = rankcollect.open_rank_db(Path(d) / "rank.db")
        rows = [ranker.RankRow(rank=i, profile_sn=900 + i, nickname=n, team_color=c, team_color_emblem=e, elo=3000.0 - i)
                for i, (n, c, e) in enumerate([("강화", "Spartan", "teamcolorboost/4_l999867.png"),
                                               ("클럽", "Spartan", "crests/l130634.png"),
                                               ("옛", "Spartan", ""), ("리옹", "리옹", "crests/l66.png")], start=1)]
        try:
            rankcollect.save_snapshot(rc, rows, NOW)
            idx = rp.snapshot_index(rc)
            assert idx == {"강화": (1, "Spartan · 강화"), "클럽": (2, "Spartan · 클럽"), "옛": (3, "Spartan (구분 전)"),
                           "리옹": (4, "리옹")}, idx
            assert [r["team_color"] for r in rp.top_rankers(rc)[1]] == ["Spartan", "Spartan", "Spartan", "리옹"]
            # 옛 rank.db(표 없음)를 읽기 전용으로 열었어도 이름으로라도 돈다
            rc.execute("DROP TABLE snapshot_emblems")
            assert rp.snapshot_index(rc)["강화"] == (1, "Spartan (구분 전)")
        finally:
            rc.close()   # 단언이 실패해도 임시 폴더를 지울 수 있게(안 닫으면 실패가 PermissionError 로 가려진다)


def test_my_team_color_strips_label():
    # 추천의 내 팀컬러 — 상대 캐시 글자("이름 · 강화")는 이름으로 되돌려 랭커 쪽(이름)과 맞댄다
    import teamcolor
    rows = [{"nickname": "남", "team_color": "", "rank": 5}]
    assert rp.my_team_color(rows, "남", teamcolor.name_of("Spartan · 클럽")) == "Spartan"
    assert rp.my_team_color(rows, "남", teamcolor.name_of("20시즌 울산 (ACL 우승)")) == "20시즌 울산 (ACL 우승)"


def test_my_team_color_order():
    rows = [_snap_row(5, "나", "팀A"), _snap_row(6, "남", "")]
    assert rp.my_team_color(rows, "나", "팀C") == "팀A", "스냅숏의 내 행이 먼저"
    assert rp.my_team_color(rows, "남", "팀C") == "팀C", "스냅숏 행에 팀컬러가 없으면 캐시"
    assert rp.my_team_color(rows, "1만밖", None) is None, "모르면 None — 최근 스쿼드로 짐작하지 않는다"


def test_recommend_candidates_sources():
    """① 받아 둔 상위 200 · ② 내 DB 의 상대(30일 · 요청 0) · ③ 받아 둔 201~1,000 — 내 팀컬러만 · 나 빼고."""
    conn = _db()
    rows = [_snap_row(200, "랭커1"), _snap_row(2, "다른색", "팀B"), _snap_row(250, "추천1"),
            _snap_row(300, "상대최근"), _snap_row(400, "상대옛날"), _snap_row(500, "상대다른색", "팀B"),
            _snap_row(50, "나"), _snap_row(9000, "상대1만")]
    players = {"랭커1": ("r1", _match("rm1", TODAY, [("r1", "랭커1", _squad(100))])),
               "다른색": ("r2", _match("rm2", TODAY, [("r2", "다른색", _squad(200))])),
               "추천1": ("r3", _match("rm3", TODAY, [("r3", "추천1", _squad(300))]))}
    api = FakeAPI(players)
    _collect(api, conn, [rows[0], rows[1]])
    _collect(api, conn, [rows[2]], source=store.RECOMMEND)
    store.save_matches(conn, [   # 1만 위 쪽을 먼저 넣는다 — 색인 순서가 아니라 순위 순으로 자르는지 잰다
        _match("o4", TODAY, [("me", "나", _squad(1)), ("op9000", "상대1만", _squad(700))]),
        _match("o1", TODAY - timedelta(days=3), [("me", "나", _squad(1)), ("op300", "상대최근", _squad(400))]),
        _match("o2", TODAY - timedelta(days=40), [("me", "나", _squad(1)), ("op400", "상대옛날", _squad(500))]),
        _match("o3", TODAY, [("me", "나", _squad(1)), ("op500", "상대다른색", _squad(600))])])
    cands = rp.recommend_candidates(conn, rows, "팀A", me="나", today=TODAY)
    got = [(c.nickname, c.source) for c in cands]
    assert got == [("랭커1", rp.SRC_PICK), ("추천1", rp.SRC_RECOMMEND), ("상대최근", rp.SRC_OPP),
                   ("상대1만", rp.SRC_OPP)], got
    assert [p["spId"] for p in cands[2].players] == _squad(400), "상대의 선발(교체 28 제외)"
    assert rp.recommend_candidates(conn, rows, None) == [], "팀컬러를 모르면 후보 없음"
    keep = config.RECOMMEND_OPPONENT_MAX
    config.RECOMMEND_OPPONENT_MAX = 1
    try:
        cands = rp.recommend_candidates(conn, rows, "팀A", me="나", today=TODAY)
        assert [c.nickname for c in cands if c.source == rp.SRC_OPP] == ["상대최근"], "② 상한은 순위 순으로 자른다"
    finally:
        config.RECOMMEND_OPPONENT_MAX = keep


def test_recommend_targets_only_when_short():
    rows = [_snap_row(r, f"n{r}") for r in (150, 200, 201, 999, 1000, 1001)] + [_snap_row(205, "나"),
                                                                                _snap_row(210, "b", "팀B")]
    need = config.RECOMMEND_MIN_RANKERS
    got = [r["rank"] for r in rp.recommend_targets(rows, "팀A", need - 1, me="나")]
    assert got == [201, 999, 1000], ("201~1,000위 · 내 팀컬러 · 나 빼고 · 순위 순", got)
    assert rp.recommend_targets(rows, "팀A", need, me="나") == [], "후보가 문턱 이상이면 더 받지 않는다(경계)"
    assert rp.recommend_targets(rows, None, 0) == []
    many = [_snap_row(300 + i, f"m{i}") for i in range(config.RECOMMEND_FETCH_MAX + 5)]
    assert len(rp.recommend_targets(many, "팀A", 0)) == config.RECOMMEND_FETCH_MAX


def _cand(i, spids, value=None, grade=None, source=rp.SRC_PICK):
    players = [{"spId": s, "spPosition": po, "spGrade": grade if grade is not None else 1 + s % 8}
               for s, po in zip(spids, FORMATION)]
    return rp.Candidate(f"c{i}", i + 1, source, value, players)


def test_recommend_cards_and_standing():
    n = config.RECOMMEND_MIN_RANKERS
    mine = [{"spId": s, "spPosition": po, "spGrade": 5} for s, po in zip(_squad(100), FORMATION)]
    # 모두 GK(첫 자리)는 내 카드 100, ST(25 · 9번째 자리)는 사람마다 — 777 은 MIN_USERS 명, 888 은 그보다 하나 적게
    cands = []
    for i in range(n):
        spids = _squad(100)
        spids[8] = 777 if i < config.RECOMMEND_MIN_USERS else (888 if i < 2 * config.RECOMMEND_MIN_USERS - 1 else 5000 + i)
        cands.append(_cand(i, spids, value=(i + 1) * 100, grade=3 + i % 5))
    rec = rp.recommend(cands, "팀A", mine, my_value=n * 100 - 300)  # 후보 하나와 같은 값 — 같으면 "나보다 높다"가 아니다
    assert rec.enough and rec.total == n and rec.by_source[rp.SRC_PICK] == n
    allcards = {c.spid: c for _ln, cs in rec.lines for c in cs}
    assert set(allcards) == {777}, ("내가 쓰는 카드는 빼고 · MIN_USERS 미만(888)도 뺀다", set(allcards))
    assert dict(rec.lines)["공격"][0].users == config.RECOMMEND_MIN_USERS
    assert abs(allcards[777].rate - config.RECOMMEND_MIN_USERS / n) < 1e-9
    value = rec.standings[0]
    assert value.name == "구단가치" and value.n == n and abs(value.above - 3 / n) < 1e-9, value  # 위 셋만 나보다 높다
    grade = rec.standings[1]
    assert grade.mine == 5 and grade.median is not None, grade
    assert rec.formation == (rp.formation_of(mine), n, n), rec.formation
    assert rp.recommend(cands, "팀A", [], None).standings[0].above is None, "내 값을 모르면 위치도 모름"


def test_recommend_threshold_boundary():
    n = config.RECOMMEND_MIN_RANKERS
    cands = [_cand(i, _squad(200)) for i in range(n - 1)]
    rec = rp.recommend(cands, "팀A", [], None)
    assert not rec.enough and rec.lines == [] and rec.standings == [] and rec.total == n - 1, "문턱 미만은 추천 안 함"
    assert rec.ranks == (1, n - 1)
    assert rp.recommend(cands + [_cand(n, _squad(200))], "팀A", [], None).enough, "정확히 문턱이면 추천"


def test_recommend_render_budget():
    """⑦ 추천 후보 ② 상한(200명)까지 경기 본문을 읽어 묶는 데 ≤ 0.3초(랭커 픽 화면 그리기 예산과 같음)."""
    conn = _db()
    rows, ds = [], []
    for i in range(config.RECOMMEND_OPPONENT_MAX + 50):
        rows.append(_snap_row(300 + i, f"상대{i}"))
        ds.append(_match(f"m{i}", TODAY, [("me", "나", _squad(1)), (f"op{i}", f"상대{i}", _squad(1000 + i % 40))]))
    store.save_matches(conn, ds)
    best = min(_timed(lambda: rp.recommend(rp.recommend_candidates(conn, rows, "팀A", me="나",
                                                                    today=TODAY), "팀A", [], None)) for _ in range(3))
    cands = rp.recommend_candidates(conn, rows, "팀A", me="나", today=TODAY)
    assert len(cands) == config.RECOMMEND_OPPONENT_MAX, len(cands)
    assert best <= 0.3, f"{best * 1000:.0f}ms"
    print(f"       추천 후보 {len(cands)}명 {best * 1000:.0f}ms")


# ── 보관·지우기 ─────────────────────────────────────────────────────────

def _file_db():
    d = Path(tempfile.mkdtemp())
    return d, store.open_db(d / "fifa.db")


def _seed_purge(conn, api_targets=3):
    targets, api = _rankers(api_targets)
    # 내 계정이 랭커0 과 붙은 경기 — 랭커0 의 최근 경기가 이 경기
    store.upsert_account(conn, "me", "나")
    api.players["랭커0"] = ("r0", _match("mine", TODAY, [("me", "나", _squad(100)), ("r0", "랭커0", _squad(7000))]))
    store.save_matches(conn, [api.players["랭커0"][1]])
    _collect(api, conn, targets)
    return targets, api


def test_clear_removes_ranker_pick_data():
    d, conn = _file_db()
    _seed_purge(conn)
    raw = (d / "fifa.db").read_bytes() + (d / "fifa.db-wal").read_bytes()
    assert "랭커1".encode() in raw and b"r1" in raw, "지우기 전에 있어야 — 없으면 아래 단언이 빈 검사"
    assert conn.execute("SELECT COUNT(*) FROM squad_owner WHERE ouid = 'r1'").fetchone()[0] == 1
    n = store.purge_ranker_data(conn, everything=True, today=TODAY, cache_dir=d)
    assert n == 2, n
    for tbl, col in (("matches", "payload"), ("match_players", "ouid"), ("squad_owner", "ouid"),
                     ("ranker_squads", "ouid"), ("ranker_matches", "match_id")):
        hits = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col} LIKE '%r1%' OR {col} LIKE '%r2%'").fetchone()[0]
        assert hits == 0, tbl
    assert conn.execute("SELECT COUNT(*) FROM match_squads").fetchone()[0] == 22, "내 경기 색인은 남는다"
    assert store.has_match(conn, "mine"), "검색한 계정이 나온 경기는 남는다"
    assert conn.execute("SELECT COUNT(*) FROM ranker_squads").fetchone()[0] == 0
    conn.close()
    raw = (d / "fifa.db").read_bytes()
    wal = d / "fifa.db-wal"
    raw += wal.read_bytes() if wal.exists() else b""
    assert "랭커1".encode() not in raw and "랭커2".encode() not in raw, "지운 행이 파일에 남았다(secure_delete)"


def test_purge_on_start_catches_overdue():
    d, conn = _file_db()
    _seed_purge(conn)
    assert store.purge_ranker_data(conn, everything=False, today=TODAY + timedelta(days=13)) == 0, "14일 안은 둔다"
    # 랭커2 만 다시 확인(오늘로) — 나머지는 14일 지남
    store.mark_ranker_match(conn, "rm2", (TODAY + timedelta(days=10)).isoformat())
    n = store.purge_ranker_data(conn, everything=False, today=TODAY + timedelta(days=15))
    assert n == 1 and not store.has_match(conn, "rm1") and store.has_match(conn, "rm2"), n
    assert store.has_match(conn, "mine")


def test_purge_drops_out_of_range_by_source():
    conn = _db()
    for sn, rank, src in ((1, 150, store.PICK), (2, None, store.PICK), (3, 900, store.RECOMMEND), (4, 1200, store.RECOMMEND)):
        store.save_ranker_squad(conn, sn, nickname=f"n{sn}", ouid=None, rank=rank, match_id=None, match_day=None,
                                fetched_at=NOW.isoformat(), fail=None, source=src)
    # 출처 범위 밖 줄(옛 버전이 "pick 이 이긴다"로 남긴 것) — 지금 저장 함수로는 못 만든다
    conn.execute("INSERT INTO ranker_squads (profile_sn, rank, source) VALUES (5, 250, ?)", (store.PICK,))
    conn.commit()
    store.purge_ranker_data(conn, everything=False, today=TODAY)
    assert sorted(store.ranker_squads(conn)) == [1, 3], "pick 200 · recommend 1,000 — 추천 랭커가 매일 지워지면 안 된다"


def test_ranker_source_follows_rank_and_prune():
    """32단계 — 출처는 저장 때 순위의 가장 좁은 구간. 3판의 "pick 이 이긴다"면 150→300위로 내려간 사람이 pick 으로 남아
    켤 때 정리가 지우고 다음 날 처음 보는 사람으로 3요청을 다시 썼다(검토 A)."""
    conn = _db()
    kw = dict(nickname="n", ouid=None, match_id=None, match_day=None, fetched_at=NOW.isoformat(), fail=None)
    store.save_ranker_squad(conn, 1, rank=150, source=store.PICK, **kw)
    assert store.ranker_squads(conn)[1]["source"] == store.PICK
    store.save_ranker_squad(conn, 1, rank=300, source=store.PICK, **kw)       # 내려감 — 부른 쪽이 pick 이어도
    assert store.ranker_squads(conn)[1]["source"] == store.TRAIT
    store.purge_ranker_data(conn, everything=False, today=TODAY)
    assert 1 in store.ranker_squads(conn), "500위 안으로 내려간 사람을 정리가 지웠다"
    store.save_ranker_squad(conn, 1, rank=100, source=store.RECOMMEND, **kw)  # 추천 길로 받았어도 200 안이면 pick
    assert store.ranker_squads(conn)[1]["source"] == store.PICK
    store.save_ranker_squad(conn, 1, rank=700, source=store.PICK, **kw)
    assert store.ranker_squads(conn)[1]["source"] == store.RECOMMEND
    store.save_ranker_squad(conn, 2, rank=None, source=store.RECOMMEND, **kw)  # 순위 없음 — 부른 쪽 값
    assert store.ranker_squads(conn)[2]["source"] == store.RECOMMEND
    assert [store.source_for_rank(r, "x") for r in (200, 201, 500, 501, 1000, 1001)] == \
        [store.PICK, store.TRAIT, store.TRAIT, store.RECOMMEND, store.RECOMMEND, store.RECOMMEND]


def test_remove_account_unused():
    """purge 는 "검색한 계정은 accounts 에서 안 빠진다"에 기댄다 — remove_account 를 부르는 곳이 생기면 규칙을 다시 본다."""
    out = subprocess.run(["git", "grep", "-n", "remove_account(", "--", "*.py"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    callers = [ln for ln in out.splitlines() if not ln.startswith("store.py:") and not ln.startswith("tests/")]
    assert callers == [], callers

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
