"""랭커 픽(2.1.1 · 6) — 마지막 랭킹 스냅숏 상위 200명이 지금 쓰는 선발 카드.

팀컬러·포메이션은 스냅숏 행에 이미 있어 요청이 0 이다. 카드·강화는 오픈API 로 랭커마다 받는다:
ouid(닉네임이 그대로면 캐시) → 최근 경기 id 1 → 상세(이미 matches 에 있거나 .cache 면 0요청) = 최대 3요청(ROADMAP R6).
상세는 store.save_matches 그대로 저장한다(색인·match_players 가 같이 잡힌다) — accounts 에는 넣지 않는다(R11).

받는 규칙(ROADMAP 2.1.1 "하루 요청 · 동의 · 보관") — 여기 있는 것은 화면 없이 테스트한다(tests/test_rankerpick.py):

| 규칙 | 어디 |
|---|---|
| 실제 HTTP 요청 직전에만 하루 계수(`api_budget` ranker_pick) · 상한 `RANKER_PICK_DAILY_REQ` | `_Session.call` |
| 재시도 끔(`attempts=1`) — 재시도도 넥슨 한도를 먹는데 계수에 안 잡힌다 | `_Session.call` |
| 요청 사이 `RANKER_PICK_GAP_S` | `_Session._gap` |
| 429 → `RANKER_PICK_429_WAIT_S` 쉬고(1초씩 쪼개 cancel 을 본다) 한 번 더 → 또 429 면 그날 `hit_429` | `_Session.call` |
| 그날 다른 로더의 최종 429(`openapi`) 나 랭커 픽 429 가 있으면 시작 안 함 | `collect` 머리 |
| 3일 안에 받은 랭커는 다시 안 묻는다 — 단 스냅숏 닉네임이 바뀌었으면 바로 | `due` |
| 실패(닉네임 바뀜 등)도 `fail`·`fetched_at` 을 적는다 — 탭을 열 때마다 다시 시도하지 않게 | `collect` |
| 같은 경기를 다시 확인하면 `ranker_matches.fetched_on` 을 오늘로 — 14일 정리에 안 지워지게 | `store.mark_ranker_match` |
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config
import store
from nexon_api import NETWORK_CODE, QUOTA_CODE, NexonAPIError
from stats import PITCH_ROWS, SUB_POSITION, pitch_rows


@dataclass
class PickResult:
    checked: int = 0        # 이번에 확인한 랭커
    requests: int = 0       # 이번에 보낸 요청
    limit: bool = False     # 오늘 상한에 걸려 멈춤
    quota: bool = False     # 429 로 멈춤(이번 또는 그날 앞서)
    cancelled: bool = False
    error: str = ""         # 네트워크 등 — 다음 기회에 이어서


class _Stop(Exception):
    pass


class _Limit(Exception):
    pass


class _Quota(Exception):
    pass


class _Session:
    """요청 하나마다: cancel → 하루 계수 → 간격 → 요청(재시도 없음) → 429 면 쉬고 한 번 더."""

    def __init__(self, conn, day: str, cancel, sleep, clock, res: PickResult):
        self.conn, self.day, self.cancel, self.sleep, self.clock, self.res = conn, day, cancel, sleep, clock, res
        self._last: float | None = None

    def _gap(self) -> None:
        if self._last is not None:
            wait = self._last + config.RANKER_PICK_GAP_S - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def call(self, fn):
        for attempt in (0, 1):
            if self.cancel():
                raise _Stop
            if not store.budget_take(self.conn, self.day, store.BUDGET_RANKER_PICK, config.RANKER_PICK_DAILY_REQ):
                raise _Limit
            self._gap()
            self.res.requests += 1
            try:
                return fn()
            except NexonAPIError as e:
                if e.code != QUOTA_CODE and e.status != 429:
                    raise
                if attempt == 1:
                    store.budget_mark_429(self.conn, self.day, store.BUDGET_RANKER_PICK)
                    raise _Quota from e
                # 초당 429 와 하루 429 가 같은 코드라 구분이 안 된다 — 쉬고 한 번 더. 통잠이면 종료가 기다림 시간을 넘긴다
                for _ in range(int(config.RANKER_PICK_429_WAIT_S)):
                    if self.cancel():
                        raise _Stop from e
                    self.sleep(1.0)
        raise AssertionError("unreachable")


def top_rankers(rank_conn, limit: int = config.RANKER_PICK_TOP) -> tuple[str | None, list[dict]]:
    """원본이 남은 마지막 스냅숏의 상위 limit 명 → (찍은 시각, [{rank, profile_sn, nickname, team_color, formation}]).
    스냅숏이 없으면 (None, [])."""
    if rank_conn is None:
        return None, []
    snap = rank_conn.execute(
        "SELECT s.id, s.taken_at FROM snapshots s WHERE EXISTS (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id)"
        " ORDER BY s.taken_at DESC, s.id DESC LIMIT 1").fetchone()
    if snap is None:
        return None, []
    # 정렬은 여기서 — ORDER BY rank 는 PK(snapshot_id, profile_sn) 순이 아니라 1만 행을 임시 정렬한다(test_rules)
    rows = [dict(r) for r in rank_conn.execute(
        "SELECT rank, profile_sn, nickname, team_color, formation FROM snapshot_rows"
        " WHERE snapshot_id = ? AND rank <= ?", (snap[0], limit))]
    rows.sort(key=lambda r: r["rank"])
    return snap[1], rows


def snapshot_index(rank_conn) -> dict[str, tuple[int, str]]:
    """원본이 남은 마지막 스냅숏의 닉네임 → (순위, 팀컬러) — 12 찾기 결과의 팀컬러·순위 칸(요청 0)."""
    _taken, rows = top_rankers(rank_conn, limit=config.RANK_TIERS[-1])
    return {r["nickname"]: (r["rank"], r.get("team_color") or "") for r in rows}


def due(targets: list[dict], have: dict[int, dict], now: datetime) -> list[dict]:
    """다시 물을 랭커 — 처음 보는 사람 · 닉네임이 바뀐 사람 먼저, 그다음 오래전에 받은 순. 3일 안에 받은 사람은 뺀다."""
    stale_before = (now - timedelta(days=config.RANKER_PICK_STALE_DAYS)).isoformat(timespec="seconds")
    out = []
    for t in targets:
        h = have.get(t["profile_sn"])
        if h is None or h.get("nickname") != t["nickname"]:
            out.append(("", t))
        elif (h.get("fetched_at") or "") < stale_before:
            out.append((h.get("fetched_at") or "", t))
    out.sort(key=lambda x: x[0])
    return [t for _, t in out]


def collect(api, conn, targets: list[dict], *, source: str = store.PICK, now_fn=datetime.now,
            cancel: Callable[[], bool] = lambda: False, sleep=time.sleep, clock=time.monotonic,
            on_ranker: Callable[[], None] | None = None) -> PickResult:
    """다시 물을 랭커를 차례로 받는다. 한 랭커 = 저장 몇 번(각각 한 트랜잭션) — 끊겨도 다음 기회에 이어서."""
    res = PickResult()
    day = now_fn().date().isoformat()
    if store.budget_hit_429(conn, day):
        res.quota = True
        return res
    have = store.ranker_squads(conn)
    s = _Session(conn, day, cancel, sleep, clock, res)
    for t in due(targets, have, now_fn()):
        if cancel():
            res.cancelled = True
            break
        sn, nick = t["profile_sn"], t["nickname"]
        h = have.get(sn) or {}
        ouid = h.get("ouid") if h.get("nickname") == nick else None
        mid = mday = fail = None
        try:
            try:
                if not ouid:
                    ouid = s.call(lambda: api.get_ouid(nick, attempts=1))
                ids = s.call(lambda: api.get_match_ids(ouid, config.DEFAULT_MATCH_TYPE, 0, 1, attempts=1))
                if not ids:
                    fail = "최근 감독모드 경기 없음"
                else:
                    mid = ids[0]
                    if not store.has_match(conn, mid):
                        detail = api.cached_detail(mid)  # .cache 적중은 요청이 아니라 계수 안 함
                        if detail is None:
                            detail = s.call(lambda: api.get_match_detail(mid, attempts=1))
                        store.save_matches(conn, [detail])
                        api.forget_details([mid])
                    stored = store.load_match(conn, mid)
                    mday = str((stored or {}).get("matchDate") or "")[:10] or None
                    store.mark_ranker_match(conn, mid, day)
            except NexonAPIError as e:
                if e.code == NETWORK_CODE:
                    res.error = e.message
                    break  # 연결이 없다 — 이 랭커를 실패로 적지 않고 다음 기회에
                fail = e.message  # 닉네임 바뀜(00004·00009) 등 — 그 랭커만
        except _Stop:
            res.cancelled = True
            break
        except _Limit:
            res.limit = True
            break
        except _Quota:
            res.quota = True
            break
        store.save_ranker_squad(conn, sn, nickname=nick, ouid=ouid, rank=t.get("rank"), match_id=mid,
                                match_day=mday, fetched_at=now_fn().isoformat(timespec="seconds"), fail=fail,
                                source=source)
        res.checked += 1
        if on_ranker is not None:
            on_ranker()
    return res


# ── 집계(화면은 core_api 로) ──────────────────────────────────────────────

@dataclass
class PickCard:
    spid: int
    users: int                                   # 이 카드를 이 줄에 쓴 랭커 수
    grades: Counter = field(default_factory=Counter)  # 강화 → 사람 수
    positions: Counter = field(default_factory=Counter)


@dataclass
class PickSummary:
    taken_at: str | None = None
    total: int = 0                # 대상 랭커(스냅숏 상위 N)
    used: int = 0                 # 집계에 든 랭커(마지막 경기가 RANKER_PICK_MAX_AGE_DAYS 안)
    old: int = 0                  # 마지막 경기가 오래돼 뺀 랭커
    refetch: int = 0              # 받아 둔 경기가 지워져 다시 받는 중
    pending: int = 0              # 아직 안 받음 · 실패
    colors: Counter = field(default_factory=Counter)      # 팀컬러 → 사람 수(스냅숏 — 요청 0)
    formations: Counter = field(default_factory=Counter)
    lines: list[tuple[str, list[PickCard]]] = field(default_factory=list)  # 줄 이름(위=공격) → 많이 쓴 카드
    rate: dict[int, float] = field(default_factory=dict)  # 카드 → 그 카드를 (어느 자리든) 선발로 쓴 랭커 비율


def _starters(detail: dict, ouid: str) -> list[dict]:
    side = next((s for s in detail.get("matchInfo") or [] if s.get("ouid") == ouid), None)
    if side is None:
        return []
    return [p for p in side.get("player") or []
            if isinstance(p.get("spId"), int) and isinstance(p.get("spPosition"), int)
            and p.get("spPosition") != SUB_POSITION]


def ranker_pick_summary(conn, taken_at: str | None, targets: list[dict], today: date | None = None,
                        top_per_line: int = 8) -> PickSummary:
    """받아 둔 랭커 경기 → 화면 집계. 경기 본문은 matches 에서 읽는다(요청 없음)."""
    today = today or date.today()
    out = PickSummary(taken_at=taken_at, total=len(targets))
    out.colors.update(t.get("team_color") or "없음" for t in targets)
    out.formations.update(t.get("formation") or "모름" for t in targets)
    have = store.ranker_squads(conn)
    oldest = (today - timedelta(days=config.RANKER_PICK_MAX_AGE_DAYS)).isoformat()
    per_line: list[dict[int, PickCard]] = [{} for _ in PITCH_ROWS]
    users: Counter = Counter()
    for t in targets:
        h = have.get(t["profile_sn"])
        if h is None or not h.get("match_id") or h.get("nickname") != t["nickname"]:
            out.pending += 1
            continue
        detail = store.load_match(conn, h["match_id"])
        if detail is None:
            out.refetch += 1
            continue
        if str(detail.get("matchDate") or "")[:10] < oldest:
            out.old += 1
            continue
        players = _starters(detail, h.get("ouid"))
        if not players:
            out.pending += 1
            continue
        out.used += 1
        codes = [p["spPosition"] for p in players]
        seen: set[int] = set()
        for ri, row in enumerate(_row_index(codes)):
            for i in row:
                p = players[i]
                c = per_line[ri].setdefault(p["spId"], PickCard(p["spId"], 0))
                c.users += 1
                c.grades[p.get("spGrade")] += 1
                c.positions[p["spPosition"]] += 1
                seen.add(p["spId"])
        users.update(seen)
    for ri, (name, _rng) in enumerate(PITCH_ROWS):
        cards = sorted(per_line[ri].values(), key=lambda c: (-c.users, c.spid))[:top_per_line]
        if cards:
            out.lines.append((name, cards))
    if out.used:
        out.rate = {spid: n / out.used for spid, n in users.items()}
    return out


def _row_index(codes: list[int]) -> list[list[int]]:
    """pitch_rows 는 빈 줄을 빼고 돌려준다 — 줄 번호(PITCH_ROWS 순서)를 잃지 않게 다시 맞춘다."""
    rows: list[list[int]] = [[] for _ in PITCH_ROWS]
    for row in pitch_rows(codes):
        code = codes[row[0]]
        ri = next(i for i, (_n, rng) in enumerate(PITCH_ROWS) if code in rng)
        rows[ri] = row
    return rows
