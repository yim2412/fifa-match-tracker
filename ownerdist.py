"""구단주 분포 v2(49단계 · 조각 ①) — 사용자 앱이 자기 서비스 키로 1~3000위 구단주 150명의 최근 1000판을 받아 요약한다.

화면 없이 테스트한다(tests/test_ownerdist.py). 받기·저장·지우기(조각 ①)와 분포 읽기·백분위(`load_dist`·`OwnerDist.pct` — 조각 ②).
칩 판정은 `habits.strength_chips(…, basis)`, 문장은 `analysis._creation_rules` 가 이 분포를 쓴다.

받는 규칙(ROADMAP "49단계") — 위반하면 조용히 넥슨 한도·약속을 넘으므로 표로 둔다:

| 규칙 | 어디 |
|---|---|
| 실제 HTTP 요청 직전에만 하루 계수(`api_budget` owner_dist) · 바퀴 요청 상한 · cancel | `_Gate.take` |
| 재시도 끔(`attempts=1`) — 재시도도 넥슨 한도를 먹는데 계수에 안 잡힌다 | `_Gate.call` |
| 429 → 전원이 `OWNER_DIST_429_WAIT_S` 쉬고(1초씩 쪼개 cancel 을 본다) 한 번 더 → 또 429 면 그날 멈춤 + `openapi` 표시도(다른 로더가 같이 멈춘다) | `_Gate.call` |
| 시작 전 (openapi · ranker_pick · owner_dist) 429 표시가 있으면 시작 안 함 | `run_round` |
| 한 명분(`OWNER_DIST_PERSON_REQ`)이 하루·바퀴 상한에 남을 때만 시작 | `run_round` |
| 구단주 한 명이 끝났을 때만 저장(요약 줄 + 해시, 한 트랜잭션). 도중에 끊기면 그 사람은 버린다 | `run_round` |
| 상세는 `get_match_detail_nocache` — `.cache` 를 읽지도 쓰지도 않는다 | `_fetch_owner` |
| 이어 받기 표시는 sha256(profile_sn + 바퀴 소금). 바퀴가 끝나면 해시·소금 삭제 | `owner_hash` · `store.owner_round_end` |
| 닉네임·ouid·profile_sn·경기 상세는 저장하지 않는다 | `summarize_owner` |
| 이어 받기는 **바퀴를 시작한 rank.db 스냅숏**(`snap_id`)에서 뽑는다 — 같은 시드도 새 스냅숏이면 같은 순위에 다른 사람이 서서 층 균형이 조용히 깨진다. 그 스냅숏이 없거나 원본이 지워졌으면 이어 붙이지 않고 `off` 로 끝내 새로 연다 | `run_round` |
| "끝남"은 **닉네임이 사라진 경우(`OPENAPI00004`)만**. 네트워크·5xx·키 거절·깨진 응답·한 사람의 상세 전부 실패는 `_Transient` — 그 사람을 끝낸 걸로 치지 않고 이번 호출을 멈춘다(바퀴는 running) | `_fetch_owner` |

동시 요청은 구단주 한 명 **안에서** `OWNER_DIST_WORKERS` 개(상세 1000건). 구단주끼리는 차례로 — 끊기면 버리는 몫이 한 명분(약 50초)이다.
풀은 바퀴 내내 하나다(구단주마다 새로 만들면 스레드마다 DB 연결이 새로 열려 바퀴가 끝날 때까지 쌓인다). 스레드마다 DB 연결을 따로 연다
(`store.open_db` 규칙) — 계수는 `budget_take` 의 BEGIN IMMEDIATE 가 지킨다. 한 경기의 어떤 예외도(깨진 JSON 포함) 그 경기만 건너뛴다.
"""
from __future__ import annotations

import hashlib
import random
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import config
import habits
import store
from nexon_api import QUOTA_CODE, NexonAPIError


@dataclass
class Target:
    stratum: int        # 1 · 2 · 3
    rank: int
    profile_sn: int
    nickname: str


@dataclass
class RoundResult:
    round_id: int | None = None
    result: str | None = None     # 끝났으면 done|thin|dropped|aborted, 이어 받을 상태면 None
    owners_done: int = 0          # 이 바퀴에서 끝난 구단주(이전 실행분 포함)
    owners_total: int = 0
    requests: int = 0             # 이번 호출에서 보낸 요청
    limit: bool = False           # 하루 상한에 걸려 멈춤
    quota: bool = False           # 429 로 멈춤(이번 또는 그날 앞서)
    cancelled: bool = False
    not_due: bool = False         # 직전 종료 뒤 아직 새 바퀴를 열 때가 아님
    no_sample: bool = False       # rank.db 스냅숏이 없어 표본을 못 뽑음
    error: str = ""


class _Halt(Exception):
    """이번 호출을 끝낸다(원인은 Gate.reason)."""


class _Transient(Exception):
    """네트워크·5xx·키 거절처럼 그 사람 탓이 아닌 실패 — 그 구단주를 "끝남"으로 치지 않고 이번 호출을 멈춘다(다음에 이어 받는다)."""

    def __init__(self, cause: BaseException):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause


NICKNAME_GONE_CODE = "OPENAPI00004"   # 맞는 키로 없는 닉네임을 물으면 온다(nexon_api.check_key 주석) — 이것만 "그 사람이 사라짐"


# ── 표본 · 요약 ─────────────────────────────────────────────────────────────

def stratum_of(rank: int) -> int | None:
    for i, top in enumerate(config.OWNER_DIST_STRATA, 1):
        if rank <= top:
            return i
    return None


def plan_round(rank_conn, seed: int, per_stratum: int | None = None, snap_id: int | None = None,
               snap_taken: str | None = None) -> list[Target]:
    """rank.db 1~3000위에서 층마다 같은 수를 시드로 뽑는다(읽기만). snap_id 를 주면 그 스냅숏에서(이어 받기 — 같은 시드라도
    스냅숏이 바뀌면 같은 순위 자리에 다른 사람이 뽑힌다), 안 주면 마지막 스냅숏. 스냅숏이 없거나 원본이 지워졌으면 [].
    snap_taken 을 같이 주면 그 스냅숏의 찍은 시각이 같을 때만 쓴다 — id 는 rank.db 를 지웠다 다시 만들면 재사용된다."""
    import rankerpick   # 순환 import 를 피한다(rankerpick 이 store·config 만 쓰지만 이쪽이 더 늦게 불린다)
    n = config.OWNER_DIST_PER_STRATUM if per_stratum is None else per_stratum
    taken, rows = rankerpick.top_rankers(rank_conn, limit=config.OWNER_DIST_STRATA[-1], snapshot_id=snap_id)
    if snap_taken is not None and taken != snap_taken:
        return []
    by: dict[int, list[Target]] = {}
    for r in rows:
        st = stratum_of(r["rank"])
        if st is not None and r.get("nickname"):
            by.setdefault(st, []).append(Target(st, r["rank"], r["profile_sn"], r["nickname"]))
    out: list[Target] = []
    for st in sorted(by):
        pool = sorted(by[st], key=lambda t: t.rank)
        out += random.Random(seed + st).sample(pool, min(n, len(pool)))
    return out


def owner_hash(profile_sn: int, salt: str) -> str:
    return hashlib.sha256(f"{profile_sn}:{salt}".encode("utf-8")).hexdigest()


def summarize_owner(totals: habits.SideTotals, stratum: int, last_match: str | None) -> dict:
    """구단주 한 명의 최근 판들 → owner_dist 한 줄. 지표는 habits.paired_diff 의 diff(나 − 같은 경기 상대),
    분모가 0 이라 못 재면 None(그 지표 분포에서만 빠진다)."""
    row: dict = {"stratum": stratum, "n_games": totals.games, "last_match": last_match}
    for k in store.OWNER_METRICS:
        d = habits.paired_diff(totals, k)
        row[k] = d.diff if d.n and d.se != float("inf") else None
    return row


def eligible(row: dict, now: datetime) -> bool:
    """분포에 넣을 구단주 — 판수가 모자라거나 마지막 경기가 너무 오래면 뺀다."""
    if (row.get("n_games") or 0) < config.OWNER_DIST_MIN_GAMES:
        return False
    last = row.get("last_match") or ""
    try:
        t = datetime.fromisoformat(last[:19])
    except ValueError:
        return False
    return t >= now - timedelta(days=config.OWNER_DIST_MAX_AGE_DAYS)


def _strata_ok(strata: list[int]) -> bool:
    """합계 OWNER_DIST_MIN_OWNERS 이상 · 층마다 OWNER_DIST_MIN_PER_STRATUM 이상(층이 비면 가중이 그 층 인구를 잃는다)."""
    return (len(strata) >= config.OWNER_DIST_MIN_OWNERS
            and all(strata.count(i) >= config.OWNER_DIST_MIN_PER_STRATUM
                    for i in range(1, len(config.OWNER_DIST_STRATA) + 1)))


# ── 분포 읽기 · 백분위(조각 ②) ──────────────────────────────────────────────

@dataclass(frozen=True)
class OwnerDist:
    """최신 done 바퀴의 구단주 분포. values[지표] = ((구단주 diff, 층 가중), …) — 쓸 수 없는 지표는 키가 없다."""
    round_id: int
    ended_at: str
    n_owners: int
    values: dict

    def pct(self, key: str, x: float) -> float | None:
        """x 의 층 가중 경험 CDF 백분위(0~100) — 같은 값은 반만 센다(새 표본 검증 verify_new.wpct 와 같은 식).
        그 지표를 쓸 수 없으면 None(쓰는 쪽이 41단계 판정으로)."""
        vw = self.values.get(key)
        if not vw:
            return None
        tot = lo = eq = 0.0
        for v, w in vw:
            tot += w
            if v < x:
                lo += w
            elif v == x:
                eq += w
        return 100 * (lo + eq / 2) / tot


def build_dist(rows: list[dict], round_id: int, ended_at: str) -> OwnerDist | None:
    """owner_dist 줄 → OwnerDist. 거르기 기준 시각은 바퀴 종료 시각이다 — 읽는 날(now)로 재면 낡아 가는 분포에서
    '마지막 경기 30일 안' 조건에 구단주가 하나둘 빠져 같은 분포가 날마다 달라진다(바퀴를 끝낼 때 run_round 도 그 시각으로 쟀다)."""
    ref = datetime.fromisoformat(ended_at[:19])
    ok = [r for r in rows if eligible(r, ref)]
    if not _strata_ok([r["stratum"] for r in ok]):
        return None
    pop = config.OWNER_DIST_STRATA_POP
    values = {}
    for k in store.OWNER_METRICS:
        have = [r for r in ok if r.get(k) is not None]
        strata = [r["stratum"] for r in have]
        if not _strata_ok(strata):
            continue   # 이 지표만 41단계로
        cnt = {i: strata.count(i) for i in set(strata)}
        values[k] = tuple((float(r[k]), pop[r["stratum"] - 1] / cnt[r["stratum"]]) for r in have)
    return OwnerDist(round_id, ended_at, len(ok), values)


def load_dist(conn, now: datetime) -> OwnerDist | None:
    """화면이 쓸 분포 — 최신 done 바퀴. 없거나 OWNER_DIST_STALE_DAYS 넘게 낡았거나 표본이 모자라면 None(41단계 판정)."""
    last = store.owner_round_last_end(conn, ("done",))
    if last is None or not last.get("ended_at"):
        return None
    if datetime.fromisoformat(last["ended_at"][:19]) + timedelta(days=config.OWNER_DIST_STALE_DAYS) < now:
        return None
    return build_dist(store.owner_dist_rows(conn, last["round_id"]), last["round_id"], last["ended_at"])


# ── 요청 게이트 ──────────────────────────────────────────────────────────────

class _Gate:
    """요청 하나마다: cancel → 429 쉼 대기 → 바퀴 상한 → 하루 계수 → 요청(재시도 없음) → 429 면 전원 쉬고 한 번 더.
    여러 스레드가 함께 부른다 — 상태는 lock 안에서만 바꾼다."""

    def __init__(self, db_path, cancel, sleep, clock, today: Callable[[], str], round_left: int):
        self.db_path, self.cancel, self.sleep, self.clock, self.today = db_path, cancel, sleep, clock, today
        self.round_left = round_left
        self.requests = 0
        self.reason = ""       # "" | cancel | limit | round | quota
        self._pause_until = 0.0
        self._lock = threading.Lock()
        self._local = threading.local()
        self._conns: list = []

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = store.open_db(self.db_path, close_elsewhere=True)
            self._local.conn = c
            with self._lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        for c in self._conns:
            try:
                c.close()
            except Exception:
                pass

    def _halt(self, reason: str) -> None:
        with self._lock:
            self.reason = self.reason or reason
        raise _Halt

    def take(self) -> None:
        while True:
            if self.reason:
                raise _Halt
            if self.cancel():
                self._halt("cancel")
            wait = self._pause_until - self.clock()
            if wait <= 0:
                break
            self.sleep(min(1.0, wait))
        with self._lock:
            if self.requests >= self.round_left:
                self.reason = self.reason or "round"
                raise _Halt
            self.requests += 1
        if not store.budget_take(self._conn(), self.today(), store.BUDGET_OWNER_DIST, config.OWNER_DIST_DAILY_REQ):
            with self._lock:
                self.requests -= 1
            self._halt("limit")

    def call(self, fn):
        for attempt in (0, 1):
            self.take()
            try:
                return fn()
            except NexonAPIError as e:
                if e.code != QUOTA_CODE and e.status != 429:
                    raise
                if attempt == 1:
                    day, c = self.today(), self._conn()
                    store.budget_mark_429(c, day, store.BUDGET_OWNER_DIST)
                    store.budget_mark_429(c, day, store.BUDGET_OPENAPI)
                    self._halt("quota")
                with self._lock:
                    now = self.clock()
                    if self._pause_until <= now:   # 같은 순간 여러 스레드가 429 를 받아도 쉼은 한 번만 건다
                        self._pause_until = now + config.OWNER_DIST_429_WAIT_S
        raise AssertionError("unreachable")


# ── 한 명 받기 ───────────────────────────────────────────────────────────────

def _fetch_owner(gate: _Gate, api_for_thread, t: Target, pool: ThreadPoolExecutor) -> tuple[habits.SideTotals, str | None] | None:
    """구단주 한 명의 최근 판 → (SideTotals, 마지막 경기 시각). 닉네임이 사라졌으면 None. _Halt 는 그대로 올라가고,
    그 사람 탓이 아닌 실패(네트워크·5xx·키 거절·깨진 응답)는 _Transient 로 올린다 — "끝남"으로 치면 그 사람이 바퀴에서 영구히 빠진다.
    pool 은 바퀴 내내 쓰는 풀이다 — 구단주마다 새로 만들면 스레드마다 DB 연결이 새로 열려 바퀴가 끝날 때까지 쌓인다."""
    api = api_for_thread()
    try:
        ouid = gate.call(lambda: api.get_ouid(t.nickname, attempts=1))
    except _Halt:
        raise
    except NexonAPIError as e:
        if e.code == NICKNAME_GONE_CODE:
            return None
        raise _Transient(e) from e
    except Exception as e:  # noqa: BLE001 — 깨진 JSON(ValueError)·dict 가 아닌 응답(AttributeError)
        raise _Transient(e) from e
    ids: list[str] = []
    while len(ids) < config.OWNER_DIST_MATCHES:
        want = min(config.OWNER_DIST_PAGE, config.OWNER_DIST_MATCHES - len(ids))
        off = len(ids)
        try:
            page = gate.call(lambda: api.get_match_ids(ouid, matchtype=config.DEFAULT_MATCH_TYPE,
                                                       offset=off, limit=want, attempts=1))
        except _Halt:
            raise
        except Exception as e:  # noqa: BLE001
            raise _Transient(e) from e
        ids += page
        if len(page) < want:
            break

    def one(mid: str):
        try:
            d = gate.call(lambda: api_for_thread().get_match_detail_nocache(mid))
            rows = habits.side_totals([d], ouid).rows   # 상세 원본은 여기서 버린다 — 요약 재료(SideRow)만 남는다
            return rows, (d.get("matchDate") or "")
        except _Halt:
            raise
        except Exception as e:  # noqa: BLE001 — 한 경기 실패(응답·파싱)가 나머지를 막지 않는다
            return e

    totals = habits.SideTotals()
    last = ""
    failed: list[Exception] = []
    futs = [pool.submit(one, mid) for mid in ids]
    try:
        for f in futs:
            got = f.result()
            if isinstance(got, Exception):
                failed.append(got)
                continue
            totals.rows += got[0]
            last = max(last, got[1])
    finally:
        for f in futs:
            f.cancel()
        wait(futs)   # 돌던 요청이 다 끝난 뒤에야 gate.requests 를 센다
    if ids and len(failed) == len(ids):
        raise _Transient(failed[0])   # 전부 실패 — 그 사람이 경기가 없는 게 아니라 받는 쪽이 막힌 것
    return totals, (last or None)


# ── 바퀴 ─────────────────────────────────────────────────────────────────────

def new_round_due(conn, now: datetime) -> bool:
    """직전 종료(done|thin|dropped|aborted — off 는 기준점이 아니다) 뒤 OWNER_DIST_ROUND_DAYS 가 지났나."""
    last = store.owner_round_last_end(conn, ("done", "thin", "dropped", "aborted"))
    if last is None:
        return True
    return datetime.fromisoformat(last["ended_at"]) + timedelta(days=config.OWNER_DIST_ROUND_DAYS) <= now


def devkey_suspected(conn) -> bool:
    """개발 키를 서비스 키로 잘못 신고했나 — 누적 요청이 OWNER_DIST_SELFCHECK_REQ 미만인데 최종 429 가 났다.
    개발 키(하루 1,000)는 첫 1,000건 안에 429 가 나고, 서비스 키는 한도가 500배라 점검 말고는 거의 안 난다."""
    total, hit = store.owner_req_total(conn)
    return hit and total < config.OWNER_DIST_SELFCHECK_REQ


def run_round(db_path, rank_conn, api_factory, *, cancel: Callable[[], bool] = lambda: False,
              sleep=time.sleep, clock=time.monotonic, now_fn=datetime.now) -> RoundResult:
    """바퀴를 이어 받거나(running) 새로 열어(due 일 때만) 구단주를 차례로 받는다. 끊기면 running 을 그대로 두고 돌아온다.

    api_factory() → FCOnlineAPI 꼴(스레드마다 하나씩 부른다). db_path 는 fifa.db — 연결은 여기서·스레드마다 연다."""
    res = RoundResult()
    conn = store.open_db(db_path)
    try:
        now = now_fn()
        day = now.date().isoformat()
        if store.budget_hit_429(conn, day):
            res.quota = True
            return res
        run = store.owner_round_running(conn)
        if run and datetime.fromisoformat(run["started_at"]) + timedelta(days=config.OWNER_DIST_ROUND_MAX_DAYS) <= now:
            store.owner_round_end(conn, run["round_id"], "dropped", now.isoformat(timespec="seconds"))
            res.round_id, res.result = run["round_id"], "dropped"
            return res
        targets: list[Target] = []
        if run is not None:
            # 이어 받기는 바퀴를 시작한 스냅숏에서 뽑는다. 스냅숏 id 가 없는 옛 줄이거나 그 스냅숏의 원본이 지워졌으면
            # 같은 표본을 다시 만들 수 없다 — 다른 사람들로 이어 붙이면 층 균형이 조용히 깨지므로 버리고 새로 연다.
            # off 로 끝낸다(기준점이 아니라서 아래에서 바로 새 바퀴가 열린다 — dropped 였다면 14일을 기다린다).
            if rank_conn is None:   # 열기가 한 번 실패한 것일 수 있다 — 진행분을 버리지 않고 다음에 이어 받는다
                res.no_sample = True
                return res
            pinned = run["snap_id"] is not None and run["snap_taken"] is not None
            targets = (plan_round(rank_conn, run["seed"], snap_id=run["snap_id"], snap_taken=run["snap_taken"])
                       if pinned else [])
            if not targets:
                store.owner_round_end(conn, run["round_id"], "off", now.isoformat(timespec="seconds"))
                run = None
        if run is None:
            if not new_round_due(conn, now):
                res.not_due = True
                return res
            seed = config.OWNER_DIST_SEED + now.date().toordinal()
            import rankerpick
            snap = rankerpick.latest_raw_snapshot(rank_conn)
            targets = plan_round(rank_conn, seed, snap_id=snap[0], snap_taken=snap[1]) if snap else []
            if not targets:
                res.no_sample = True
                return res
            salt = secrets.token_hex(16)
            rid = store.owner_round_start(conn, seed, now.isoformat(timespec="seconds"), salt,
                                          snap[0], snap[1])
            used = 0
        else:
            salt, rid, used = run["salt"], run["round_id"], run["req_count"]
        res.round_id, res.owners_total = rid, len(targets)
        done = store.owner_done_hashes(conn, rid)
        res.owners_done = len(done)

        gate = _Gate(db_path, cancel, sleep, clock, lambda: now_fn().date().isoformat(),
                     max(0, config.OWNER_DIST_ROUND_REQ - used))
        local = threading.local()

        def api_for_thread():
            if not hasattr(local, "api"):
                local.api = api_factory()
            return local.api

        pool = ThreadPoolExecutor(max_workers=config.OWNER_DIST_WORKERS)
        try:
            for t in targets:
                if res.owners_done >= res.owners_total:
                    break
                h = owner_hash(t.profile_sn, salt)
                if h in done:
                    continue
                left_day = config.OWNER_DIST_DAILY_REQ - store.budget_used(conn, gate.today(), store.BUDGET_OWNER_DIST)
                if gate.round_left - gate.requests < config.OWNER_DIST_PERSON_REQ:
                    gate.reason = gate.reason or "round"
                    break
                if left_day < config.OWNER_DIST_PERSON_REQ:
                    gate.reason = gate.reason or "limit"
                    break
                if cancel():
                    gate.reason = gate.reason or "cancel"
                    break
                before = gate.requests
                try:
                    got = _fetch_owner(gate, api_for_thread, t, pool)
                except (_Halt, _Transient) as stop:
                    store.owner_round_add_req(conn, rid, gate.requests - before)   # 버린 사람의 요청도 바퀴 상한에는 든다
                    if isinstance(stop, _Transient):
                        gate.reason = gate.reason or "error"   # 바퀴는 running 으로 남아 다음에 이어 받는다
                        res.error = f"{type(stop.cause).__name__} {getattr(stop.cause, 'code', '')}".strip()   # 메시지에는 남의 닉네임이 URL 로 실린다
                    break
                store.owner_round_add_req(conn, rid, gate.requests - before)
                if got is None or not got[0].games:
                    store.owner_done_add(conn, rid, h)
                else:
                    store.owner_dist_add(conn, rid, h, summarize_owner(got[0], t.stratum, got[1]))
                done.add(h)
                res.owners_done += 1
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            gate.close()
        res.requests = gate.requests
        res.cancelled = gate.reason == "cancel"
        res.limit = gate.reason == "limit"
        res.quota = gate.reason == "quota"

        end = now_fn().isoformat(timespec="seconds")
        if gate.reason == "round":
            store.owner_round_end(conn, rid, "aborted", end)
            res.result = "aborted"
        elif not gate.reason and res.owners_done >= res.owners_total:
            ref = datetime.fromisoformat(end)   # 읽는 쪽(build_dist)도 이 종료 시각으로 거른다 — 같은 판정이 나와야 한다
            ok = [r["stratum"] for r in store.owner_dist_rows(conn, rid) if eligible(r, ref)]
            res.result = "done" if _strata_ok(ok) else "thin"
            store.owner_round_end(conn, rid, res.result, end)
        return res
    finally:
        conn.close()
