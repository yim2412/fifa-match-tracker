"""구단주 분포 v2(49단계 · 조각 ①) — 사용자 앱이 자기 서비스 키로 1~3000위 구단주 150명의 최근 1000판을 받아 요약한다.

화면 없이 테스트한다(tests/test_ownerdist.py). 받기·저장·지우기(조각 ①)와 분포 읽기·백분위(`load_dist`·`OwnerDist.pct` — 조각 ②).
칩 판정은 `habits.strength_chips(…, basis)`, 문장은 `analysis._creation_rules` 가 이 분포를 쓴다.
C4 자리별 수준(50단계 ③)은 `load_pos_dist`(같은 바퀴 + v2 + 칸마다 층 하한) → 판정 `habits.pos_level`.

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
| C4 재료(`pos_json` · 50단계)는 바퀴 시작 때 정한 v2 바퀴에서만 · 계산은 경기 단위·구단주 단위 **두 자리 다 따로 막아** 실패하면 그 구단주의 재료만 NULL(기본 6지표·바퀴는 그대로) | `_fetch_owner.one` · `pos_json_of` |
| done 바퀴 줄은 무작위 순서로 다시 넣고 마지막 경기는 날짜만(50단계 V1) | `store.owner_dist_mix` · `store.owner_dist_add` |
| 이어 받기는 **바퀴를 시작한 rank.db 스냅숏**(`snap_id`)에서 뽑는다 — 같은 시드도 새 스냅숏이면 같은 순위에 다른 사람이 서서 층 균형이 조용히 깨진다. 그 스냅숏이 없거나 원본이 지워졌으면 이어 붙이지 않고 `off` 로 끝내 새로 연다 | `run_round` |
| "끝남"은 **닉네임이 사라진 경우(`OPENAPI00004`)만**. 네트워크·5xx·키 거절·깨진 응답·한 사람의 상세 전부 실패는 `_Transient` — 그 사람을 끝낸 걸로 치지 않고 이번 호출을 멈춘다(바퀴는 running) | `_fetch_owner` |

동시 요청은 구단주 한 명 **안에서** `OWNER_DIST_WORKERS` 개(상세 1000건). 구단주끼리는 차례로 — 끊기면 버리는 몫이 한 명분(약 50초)이다.
풀은 바퀴 내내 하나다(구단주마다 새로 만들면 스레드마다 DB 연결이 새로 열려 바퀴가 끝날 때까지 쌓인다). 스레드마다 DB 연결을 따로 연다
(`store.open_db` 규칙) — 계수는 `budget_take` 의 BEGIN IMMEDIATE 가 지킨다. 한 경기의 어떤 예외도(깨진 JSON 포함) 그 경기만 건너뛴다.
"""
from __future__ import annotations

import hashlib
import json
import math
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
    """분포에 넣을 구단주 — 판수가 모자라거나 마지막 경기가 너무 오래면 뺀다.
    날짜끼리 비교한다 — 저장값은 날짜만이다(store._day_only · 50단계 V1). 옛 시각 문자열 줄도 앞 10자로 같이 읽는다."""
    if (row.get("n_games") or 0) < config.OWNER_DIST_MIN_GAMES:
        return False
    last = row.get("last_match") or ""
    try:
        d = date.fromisoformat(last[:10])
    except ValueError:
        return False
    return d >= now.date() - timedelta(days=config.OWNER_DIST_MAX_AGE_DAYS)


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


# ── C4 자리별 수준 분포(50단계 ③) — 칸은 config.C4_CELLS · 식은 측정 C 판정기(analyze50.Dist)와 같다 ──────────

@dataclass(frozen=True)
class PosCellDist:
    """한 칸의 기준 — params = ((지표 자리, 층 가중 μ, 층 가중 σ), …)(σ = 0 인 지표는 정보가 없어 뺀다),
    scores = ((구단주 지수, 층 가중), …). 지수 = 그 칸 지표들 z 의 평균."""
    params: tuple
    scores: tuple
    n_owners: int

    def index(self, v: list[float]) -> float:
        return sum((v[i] - mu) / sd for i, mu, sd in self.params) / len(self.params)

    def pct(self, x: float) -> float:
        """층 가중 경험 CDF 백분위 — OwnerDist.pct 와 같은 식(같은 값은 반만)."""
        tot = lo = eq = 0.0
        for s, w in self.scores:
            tot += w
            if s < x:
                lo += w
            elif s == x:
                eq += w
        return 100 * (lo + eq / 2) / tot


@dataclass(frozen=True)
class PosDist:
    """최신 done v2 바퀴의 칸별 기준 — 층 하한을 못 넘은 칸은 cells 에 없다."""
    round_id: int
    ended_at: str
    cells: dict


def _wstats(vals: list[float], ws: list[float]) -> tuple[float, float]:
    sw = sum(ws)
    mu = sum(v * w for v, w in zip(vals, ws)) / sw
    return mu, math.sqrt(sum(w * (v - mu) ** 2 for v, w in zip(vals, ws)) / sw)


def build_pos_dist(rows: list[dict], round_id: int, ended_at: str) -> PosDist:
    """owner_dist 줄 → PosDist. 대상은 build_dist 와 같은 eligible 구단주 중 그 칸 재료(pos_json)가 있는 사람이고,
    **칸마다 층 하한을 다시 본다** — v2 바퀴라도 재료 계산이 실패한 구단주(NULL)나 다운그레이드 사이 구간(6회차 Y4)으로
    한 층이 비면 가중이 그 층 인구를 잃는다. 깨진 JSON 줄은 그 사람만 뺀다."""
    ref = datetime.fromisoformat(ended_at[:19])
    have: dict[str, list[tuple[int, list[float]]]] = {habits.c4_cell(L, s): [] for L, s in config.C4_CELLS}
    for r in rows:
        if not r.get("pos_json") or not eligible(r, ref):
            continue
        try:
            pj = json.loads(r["pos_json"])
            for cell, got in have.items():
                if cell in pj and pj[cell].get("n"):
                    got.append((r["stratum"], [float(x) for x in pj[cell]["d"]]))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    pop = config.OWNER_DIST_STRATA_POP
    cells = {}
    for cell, got in have.items():
        strata = [s for s, _ in got]
        if not got or not _strata_ok(strata):
            continue
        cnt = {i: strata.count(i) for i in set(strata)}
        ws = [pop[s - 1] / cnt[s] for s in strata]
        vs = [v for _, v in got]
        params = []
        for i in range(len(vs[0])):
            mu, sd = _wstats([v[i] for v in vs], ws)
            if sd > 0:
                params.append((i, mu, sd))
        if not params:
            continue
        scores = [sum((v[i] - mu) / sd for i, mu, sd in params) / len(params) for v in vs]   # = PosCellDist.index
        cells[cell] = PosCellDist(tuple(params), tuple(zip(scores, ws)), len(got))
    return PosDist(round_id, ended_at, cells)


def load_pos_dist(conn, now: datetime) -> tuple[PosDist | None, str]:
    """C4 가 쓸 분포 — load_dist 가 고르는 **같은** 최신 done 바퀴(낡음·thin 규칙도 같다)이고 그 바퀴가 v2 일 때만(5판 V2 —
    load_dist 자체는 v2 를 안 본다: 49 칩이 회귀하지 않게). → (분포, 이유) — 이유: "" | none(쓸 바퀴 없음) | v1(재료 없는
    바퀴) | thin(재료가 있는 구단주가 층 하한 미달)."""
    last = store.owner_round_last_end(conn, ("done",))
    if last is None or not last.get("ended_at"):
        return None, "none"
    if datetime.fromisoformat(last["ended_at"][:19]) + timedelta(days=config.OWNER_DIST_STALE_DAYS) < now:
        return None, "none"
    rows = store.owner_dist_rows(conn, last["round_id"])
    if build_dist(rows, last["round_id"], last["ended_at"]) is None:
        return None, "none"
    if last.get("v2") != 1:
        return None, "v1"
    pd = build_pos_dist(rows, last["round_id"], last["ended_at"])
    return (pd, "") if pd.cells else (None, "thin")


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

_V2_FAIL = object()   # 한 경기의 C4 재료 계산이 예외로 실패했다는 표시


def _fetch_owner(gate: _Gate, api_for_thread, t: Target, pool: ThreadPoolExecutor,
                 v2: bool = False) -> tuple[habits.SideTotals, str | None, habits.PosTotals | None] | None:
    """구단주 한 명의 최근 판 → (SideTotals, 마지막 경기 시각, C4 재료). 닉네임이 사라졌으면 None. _Halt 는 그대로 올라가고,
    C4 재료는 v2 바퀴일 때만 — 한 경기라도 그 계산이 **예외**로 실패하면 그 구단주는 None(남은 경기로 조용히 만들면 판수가
    엇나간다 · 7판 M1). 그 계산은 따로 try 라 기본 6지표(rows)는 그대로 남는다(4회차 W2). 자리 데이터가 빈 경기는 실패가 아니다(Y1).
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
            rows = habits.side_totals([d], ouid).rows   # 상세 원본은 여기서 버린다 — 요약 재료(SideRow · C4 칸 값)만 남는다
            pos = None
            if v2:
                try:
                    pos = habits.pos_diff(d, ouid)
                except Exception:  # noqa: BLE001 — 새 계산의 실패가 기본 지표를 깎지 않게
                    pos = _V2_FAIL
            return rows, (d.get("matchDate") or ""), pos
        except _Halt:
            raise
        except Exception as e:  # noqa: BLE001 — 한 경기 실패(응답·파싱)가 나머지를 막지 않는다
            return e

    totals = habits.SideTotals()
    pos_tot: habits.PosTotals | None = habits.PosTotals() if v2 else None
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
            if pos_tot is not None:
                if got[2] is _V2_FAIL:
                    pos_tot = None
                elif got[2]:
                    pos_tot.add(got[2])
    finally:
        for f in futs:
            f.cancel()
        wait(futs)   # 돌던 요청이 다 끝난 뒤에야 gate.requests 를 센다
    if ids and len(failed) == len(ids):
        raise _Transient(failed[0])   # 전부 실패 — 그 사람이 경기가 없는 게 아니라 받는 쪽이 막힌 것
    return totals, (last or None), pos_tot


def pos_json_of(pos: habits.PosTotals | None) -> str | None:
    """구단주 단위 C4 재료 → pos_json 글자. 실패하면 None — **부르는 쪽 try 대신 여기서 막는다**: 감싸지 않으면 예외가 바퀴 밖으로
    올라가 running 으로 남고, 같은 구단주에서 틱마다 다시 터져 dropped → 토글 꺼짐이 된다(5회차 X1)."""
    if pos is None:
        return None
    try:
        s = pos.summary()
        return json.dumps(s, separators=(",", ":")) if s else None
    except Exception:  # noqa: BLE001
        return None


# ── 바퀴 ─────────────────────────────────────────────────────────────────────

def new_round_due(conn, now: datetime) -> bool:
    """직전 종료(done|thin|dropped|aborted — off 는 기준점이 아니다) 뒤 OWNER_DIST_ROUND_DAYS 가 지났나."""
    last = store.owner_round_last_end(conn, ("done", "thin", "dropped", "aborted"))
    if last is None:
        return True
    return datetime.fromisoformat(last["ended_at"]) + timedelta(days=config.OWNER_DIST_ROUND_DAYS) <= now


def devkey_suspected(conn, res: RoundResult, since: str) -> bool:
    """개발 키를 서비스 키로 잘못 신고했나 — 이번 호출이 요청을 보내다 최종 429 로 멈췄는데, 켠 시각(since) 이후 시작한
    바퀴들의 요청 합이 OWNER_DIST_SELFCHECK_REQ 미만. 개발 키(하루 1,000)는 첫 1,000건 안에 429 가 나고, 서비스 키는 한도가
    500배라 점검 말고는 거의 안 난다. 요청 0 인 quota(그날 앞서 다른 로더·내 검색의 429 표시로 시작도 안 함)는 판정하지 않는다."""
    return (res.quota and res.requests > 0
            and store.owner_req_since(conn, since) < config.OWNER_DIST_SELFCHECK_REQ)


def should_run(conn, now: datetime) -> bool:
    """예약 틱에서 로더를 띄울까 — 진행 바퀴가 있거나 새 바퀴 차례이고, 오늘 (openapi·랭커 픽·구단주 분포) 429 표시가 없고,
    오늘 하루 상한에 한 명분이 남았을 때. run_round 도 같은 것을 다시 보지만, 여기서 걸러 10분마다 스레드를 헛되이 띄우지 않는다."""
    day = now.date().isoformat()
    if store.budget_hit_429(conn, day):
        return False
    if config.OWNER_DIST_DAILY_REQ - store.budget_used(conn, day, store.BUDGET_OWNER_DIST) < config.OWNER_DIST_PERSON_REQ:
        return False   # run_round 의 left_day 와 같은 판정 — 상한에 닿은 날 자정까지 틱마다 rank.db 를 열지 않게
    return store.owner_round_running(conn) is not None or new_round_due(conn, now)


def off_reason_after(conn, res: RoundResult, on_at: str) -> str:
    """바퀴 호출 하나가 끝난 뒤 토글을 스스로 끌 사유 — "devkey" · "fail" · ""(그대로). on_at = 토글을 켠 시각.
    devkey: devkey_suspected(이번 호출의 최종 429 일 때만 — 429 가 아니면 판정 재료가 안 바뀐다).
    fail: 켠 시각 이후 시작한 바퀴가 끝에서부터 dropped|aborted 로 OWNER_DIST_FAIL_MAX 번 이어졌다(off 는 실패도 성공도 아니라 건너뛴다)."""
    if devkey_suspected(conn, res, on_at):
        return "devkey"
    if res.result not in ("dropped", "aborted"):
        return ""
    streak = 0
    for r in reversed(store.owner_round_results(conn, on_at)):
        if r == "off":
            continue
        if r not in ("dropped", "aborted"):
            break
        streak += 1
    return "fail" if streak >= config.OWNER_DIST_FAIL_MAX else ""


def purge(db_path, *, everything: bool, now: datetime) -> bool:
    """돌던 바퀴를 off 로 끝내고, everything 이면 받은 분포까지(store.purge_owner_dist). 지울 게 없으면 열기만 하고 안 쓴다.
    로더가 도는 동안 부르면 안 된다(그 저장과 겹친다) — 예약이 로더가 끝난 뒤에 부른다. → 무언가 지웠나."""
    conn = store.open_db(db_path)
    try:
        if not store.owner_dist_has_data(conn):
            return False
        if not everything and store.owner_round_running(conn) is None:
            return False
        store.purge_owner_dist(conn, everything=everything, now=now.isoformat(timespec="seconds"))
        return True
    finally:
        conn.close()


OFF_REASON_TEXT = {
    "devkey": "넥슨이 호출 한도를 알려 껐습니다 — 개발 단계 키로 보입니다(서비스 단계 키가 필요합니다)",
    "fail": f"바퀴가 {config.OWNER_DIST_FAIL_MAX}번 이어서 끝나지 못해 껐습니다",
    "key": "API 키를 바꿔 껐습니다 — 새 키가 서비스 단계면 다시 켜세요",
}
RESULT_TEXT = {"done": "완료", "thin": "인원 부족", "dropped": "기한 넘김", "aborted": "요청 상한", "off": "중단"}


def status_text(conn, now: datetime) -> str:
    """[정보] 창의 구단주 분포 상태 한 줄 — 받는 중 N/전체 · 최근 바퀴 결과·날짜 · 쓰는 분포 기준일·인원 · 꺼진 사유.
    창을 열 때 한 번(화면 스레드의 fifa.db 읽기)."""
    parts = []
    run = store.owner_round_running(conn)
    if run is not None:
        n = len(store.owner_done_hashes(conn, run["round_id"]))
        parts.append(f"받는 중 {n}/{config.OWNER_DIST_PER_STRATUM * len(config.OWNER_DIST_STRATA)}명"
                     f" ({run['started_at'][:10]} 시작)")
    last = store.owner_round_last_end(conn)
    if last is not None and last.get("ended_at"):
        parts.append(f"최근 바퀴 {RESULT_TEXT.get(last['result'], last['result'])} {last['ended_at'][:10]}")
    dist = load_dist(conn, now)
    if dist is not None:
        parts.append(f"쓰는 분포 {dist.ended_at[:10]} 기준 · 구단주 {dist.n_owners}명")
    elif run is None and last is None:
        parts.append("아직 받은 분포가 없습니다")
    if not config.OWNER_DIST_ON and config.OWNER_DIST_OFF in OFF_REASON_TEXT:
        parts.append(OFF_REASON_TEXT[config.OWNER_DIST_OFF])
    return " · ".join(parts)


def run_round(db_path, rank_conn, api_factory, *, cancel: Callable[[], bool] = lambda: False,
              sleep=time.sleep, clock=time.monotonic, now_fn=datetime.now,
              rank_open: Callable[[], object] | None = None, force: bool = False) -> RoundResult:
    """바퀴를 이어 받거나(running) 새로 열어(due 일 때만) 구단주를 차례로 받는다. 끊기면 running 을 그대로 두고 돌아온다.

    api_factory() → FCOnlineAPI 꼴(스레드마다 하나씩 부른다). db_path 는 fifa.db — 연결은 여기서·스레드마다 연다.
    rank_conn 대신 rank_open(열기 함수)을 주면 표본을 뽑는 동안만 열고 바로 닫는다 — 바퀴는 몇 시간이고, 열린 rank.db
    연결이 [수집 기록 지우기]의 파일 삭제를 막는다(rankcollect.open_rank_db_ro 주석).

    v2(C4 재료 pos_json)는 **새 바퀴를 열 때 한 번** config.owner_dist_v2_allowed()(디스크 .env)로 정해 바퀴에 적고, 이어 받을 때는
    그 값을 읽는다 — 도중 동의·업그레이드로 한 바퀴에 재료가 있는/없는 구단주가 섞이지 않게(2회차 T2).
    force: **개발·측정 전용**(앱은 안 쓴다) — new_round_due 를 무시하고 새 바퀴를 v2 로 연다(동의 판정도 넘긴다 · 조각 ② 끝 기준의 강제 바퀴)."""
    res = RoundResult()
    conn = store.open_db(db_path)
    own_rank = None
    try:
        now = now_fn()
        day = now.date().isoformat()
        if store.budget_hit_429(conn, day):
            res.quota = True
            return res
        if rank_conn is None and rank_open is not None:
            try:
                own_rank = rank_conn = rank_open()
            except Exception:  # noqa: BLE001 — 못 열면 None 과 같다(아래 no_sample)
                rank_conn = None
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
            if not force and not new_round_due(conn, now):
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
            v2 = force or config.owner_dist_v2_allowed()
            rid = store.owner_round_start(conn, seed, now.isoformat(timespec="seconds"), salt,
                                          snap[0], snap[1], v2=v2)
            used = 0
        else:
            salt, rid, used = run["salt"], run["round_id"], run["req_count"]
            v2 = run.get("v2") == 1
        if own_rank is not None:
            own_rank.close()   # 표본은 뽑았다 — 남은 몇 시간 동안 rank.db 를 붙잡지 않는다
            own_rank = None
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
                    got = _fetch_owner(gate, api_for_thread, t, pool, v2)
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
                    row = summarize_owner(got[0], t.stratum, got[1])
                    row["pos_json"] = pos_json_of(got[2])
                    store.owner_dist_add(conn, rid, h, row)
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
        if own_rank is not None:
            own_rank.close()
        conn.close()
