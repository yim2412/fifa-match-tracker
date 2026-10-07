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


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
랭커 픽(2.1.1 · 16단계 · 2.3.1 N15 `record_pick_days` — 한 바퀴 끝에 그날 아는 픽을 rank.db `pick_days` 에 **더한다**: 날짜 = 마지막 경기 날짜 ·
출처 PICK 상위 200만 · 센 것은 `pick_counted`("경기/프로필 번호" · 14일) · 14일보다 옛 경기는 안 셈.
띄우기 `RankerPickLoader._record_days`(만들지 않는 열기)) — `top_rankers`(rank.db 원본이 남은 마지막 스냅숏 상위 200) → `collect`(파일 머리말 표: 실제 요청 직전에만 `api_budget` 계수 ·
`attempts=1` · 간격 0.25초 · 429 는 60초를 1초씩 쉬고 한 번 더 → 또 429 면 그날 `hit_429` · 그날 다른 로더 최종 429 면 시작 안 함 · 3일 ·
닉네임 바뀌면 바로) → 상세는 `store.save_matches` 그대로(**`accounts` 엔 안 넣는다**) + `ranker_matches` 표시.
화면 집계 `ranker_pick_summary`(core_api).
띄우기는 `app_main.RankerPickLoader`/`start_ranker_pick` — **랭커 픽 화면과 창이 보일 때만**(메뉴 이동·숨김·최소화면 cancel · 다시 보이면 잇는다) ·
오픈API 백그라운드는 하나씩(검색·비교·거래·랭커 기록 뒤).
지우기는 화면 스레드 한 함수 `purge_ranker_pick_data`(로더가 돌면 끝난 뒤) — 끄는 길은 전부 `sync_ranker_pick_data` 를 거친다(수집이 꺼져 있으면 전부, 켜져 있으면 14일 ·
켤 때도 한 번). 추천(17단계 ·
[추천] 탭): `my_team_color`(스냅숏 내 행 → 검색 때 읽은 팀컬러·캐시 → 모르면 None, 짐작 안 함) → `recommend_candidates`(① 받아 둔 상위 200 ·
② 색인의 내 상대 중 1만 위 안 · 30일 — 요청 0 · ③ 받아 둔 201~1,000) → `recommend`(문턱 `RECOMMEND_MIN_RANKERS` 미만이면 표 없음).
③ 받기는 `recommend_targets` — 로더가 **[추천] 이 보일 때만 싣고 상위 200보다 먼저**(200명이 하루 상한을 혼자 다 쓴다).
[픽] 로더가 도는 중 [추천] 을 열면 멈추고 실어서 다시(`_pick_restart`)
"""
from __future__ import annotations

import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config
import rankcollect
import store
import teamcolor
from nexon_api import NETWORK_CODE, QUOTA_CODE, NexonAPIError
from stats import PITCH_ROWS, SUB_POSITION, formation_of, pitch_rows


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

    def __init__(self, conn, day: str, cancel, sleep, clock, res: PickResult,
                 kind: str = store.BUDGET_RANKER_PICK, cap: int | None = None):
        self.conn, self.day, self.cancel, self.sleep, self.clock, self.res = conn, day, cancel, sleep, clock, res
        self.kind, self.cap = kind, (config.RANKER_PICK_DAILY_REQ if cap is None else cap)
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
            if not store.budget_take(self.conn, self.day, self.kind, self.cap):
                raise _Limit
            self._gap()
            self.res.requests += 1
            try:
                return fn()
            except NexonAPIError as e:
                if e.code != QUOTA_CODE and e.status != 429:
                    raise
                if attempt == 1:
                    store.budget_mark_429(self.conn, self.day, self.kind)
                    raise _Quota from e
                # 초당 429 와 하루 429 가 같은 코드라 구분이 안 된다 — 쉬고 한 번 더. 통잠이면 종료가 기다림 시간을 넘긴다
                for _ in range(int(config.RANKER_PICK_429_WAIT_S)):
                    if self.cancel():
                        raise _Stop from e
                    self.sleep(1.0)
        raise AssertionError("unreachable")


def top_rankers(rank_conn, limit: int = config.RANKER_PICK_TOP) -> tuple[str | None, list[dict]]:
    """원본이 남은 마지막 스냅숏의 상위 limit 명 → (찍은 시각, [{rank, profile_sn, nickname, team_color, formation, team_value}]).
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
        "SELECT rank, profile_sn, nickname, team_color, formation, team_value FROM snapshot_rows"
        " WHERE snapshot_id = ? AND rank <= ?", (snap[0], limit))]
    rows.sort(key=lambda r: r["rank"])
    return snap[1], rows


def snapshot_index(rank_conn) -> dict[str, tuple[int, str]]:
    """원본이 남은 마지막 스냅숏의 닉네임 → (순위, 팀컬러 글자 teamcolor.label) — 12 찾기 결과의 팀컬러·순위 칸(요청 0).
    글자는 화면 콤보(상대 팀컬러 글자)와 맞대려고 여기서만 붙인다 — top_rankers 는 랭커 픽 비율·추천도 쓰므로 이름 그대로."""
    _taken, rows = top_rankers(rank_conn, limit=config.RANK_TIERS[-1])
    if not rows:
        return {}
    sid = rank_conn.execute(
        "SELECT s.id FROM snapshots s WHERE EXISTS (SELECT 1 FROM snapshot_rows r WHERE r.snapshot_id = s.id)"
        " ORDER BY s.taken_at DESC, s.id DESC LIMIT 1").fetchone()
    try:
        emblems = rankcollect.snapshot_emblem_map(rank_conn, sid[0]) if sid else {}
    except sqlite3.Error:   # 옛 rank.db 를 읽기 전용으로 열었으면 표가 아직 없다
        emblems = {}
    return {r["nickname"]: (r["rank"], teamcolor.label(r.get("team_color") or "", emblems.get(r["profile_sn"], "")))
            for r in rows}


def due(targets: list[dict], have: dict[int, dict], now: datetime, stale_days: float | None = None) -> list[dict]:
    """다시 물을 랭커 — 처음 보는 사람 · 닉네임이 바뀐 사람 먼저, 그다음 오래전에 받은 순. 3일 안에 받은 사람은 뺀다."""
    days = config.RANKER_PICK_STALE_DAYS if stale_days is None else stale_days
    stale_before = (now - timedelta(days=days)).isoformat(timespec="seconds")
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
            on_ranker: Callable[[], None] | None = None, budget_kind: str = store.BUDGET_RANKER_PICK,
            daily_cap: int | None = None, stale_days: float | None = None) -> PickResult:
    """다시 물을 랭커를 차례로 받는다. 한 랭커 = 저장 몇 번(각각 한 트랜잭션) — 끊겨도 다음 기회에 이어서.

    budget_kind·daily_cap·stale_days 는 다른 키로 도는 개발용 수집(tools/dev_archive.py)만 바꾼다 — 그 키의 계수·429 는
    앱 키와 따로 센다(앱 키의 429 가 그쪽을 막거나, 그쪽 429 가 앱을 막지 않게)."""
    res = PickResult()
    day = now_fn().date().isoformat()
    kinds = (store.BUDGET_OPENAPI, store.BUDGET_RANKER_PICK) if budget_kind == store.BUDGET_RANKER_PICK else (budget_kind,)
    if store.budget_hit_429(conn, day, kinds):
        res.quota = True
        return res
    have = store.ranker_squads(conn)
    s = _Session(conn, day, cancel, sleep, clock, res, budget_kind, daily_cap)
    for t in due(targets, have, now_fn(), stale_days):
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


def record_pick_days(rank_conn, conn, targets: list[dict], today: date | None = None) -> int:
    """N15(2.3.1) — 한 바퀴 끝에 그날 아는 픽을 rank.db pick_days 에 **더한다**(익명 · 영구). → 센 경기 수.

    - 모집단: 이 바퀴의 대상(스냅숏 상위 RANKER_PICK_TOP) 중 출처 PICK 줄만(1회차 A — ranker_squads 엔 추천 201~1,000위와
      개발용 수집 몫이 섞여 날마다 모집단이 바뀐다).
    - 날짜 = 랭커의 마지막 경기 날짜(1회차 B — 받은 날로 세면 3일 규칙 때문에 같은 스쿼드가 사흘 들어간다).
    - 덮어쓰지 않고 안 센 경기만 +1(2회차 A — ranker_squads 는 랭커마다 마지막 경기 하나라, 다시 세면 다음 날 또 경기한
      랭커가 앞 날짜에서 빠졌다). 센 것은 pick_counted("경기/프로필 번호" — 랭커 둘이 맞붙은 경기도 둘 다 센다).
      그 표는 원본과 같이 14일 뒤 지우므로, 그보다 옛 경기는 세지 않는다(3회차 — 표시가 지워진 뒤 다시 +1)."""
    today = today or date.today()
    oldest = (today - timedelta(days=config.RANK_RAW_KEEP_DAYS)).isoformat()
    have = store.ranker_squads(conn)
    counted = {r[0] for r in rank_conn.execute("SELECT match_id FROM pick_counted")}
    todo = []
    for t in targets:
        if (t.get("rank") or 0) > config.RANKER_PICK_TOP:
            continue
        h = have.get(t["profile_sn"])
        if (h is None or h.get("source") != store.PICK or not h.get("match_id") or h.get("nickname") != t["nickname"]
                or not h.get("match_day") or h["match_day"] < oldest):
            continue
        key = f"{h['match_id']}/{t['profile_sn']}"
        if key in counted:
            continue
        detail = store.load_match(conn, h["match_id"])
        players = _starters(detail, h.get("ouid")) if detail else []
        if not players:
            continue
        keys = {("card", str(p["spId"])) for p in players}
        keys |= {("card_grade", f"{p['spId']}:{p.get('spGrade')}") for p in players}
        keys.add(("team_color", t.get("team_color") or ""))
        todo.append((key, h["match_day"], sorted(keys)))
    if not todo:
        return 0
    with rank_conn:
        for key, day, keys in todo:
            rank_conn.execute("UPDATE pick_days SET rankers = rankers + 1 WHERE day = ?", (day,))
            row = rank_conn.execute("SELECT rankers FROM pick_days WHERE day = ? LIMIT 1", (day,)).fetchone()
            total = row[0] if row else 1
            rank_conn.executemany("INSERT INTO pick_days (day, kind, key, n, rankers) VALUES (?, ?, ?, 1, ?)"
                                  " ON CONFLICT(day, kind, key) DO UPDATE SET n = n + 1",
                                  [(day, k, v, total) for k, v in keys])
            rank_conn.execute("INSERT INTO pick_counted (match_id, day) VALUES (?, ?)", (key, day))
    return len(todo)


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


# ── 11-lite 랭커 기반 추천(2.1.1 · 17단계) ────────────────────────────────
# 후보(ROADMAP U3) — 내 팀컬러인 랭커만:
#   ① pick      상위 200 중 랭커 픽이 받아 둔 사람(요청은 랭커 픽 몫)
#   ② opp       마지막 스냅숏 1만 안 · 내 DB 에 최근 CARD_OWNER_DAYS 일 경기가 있는 상대(요청 0)
#   ③ recommend ①+② 가 RECOMMEND_MIN_RANKERS 미만일 때만 201~1,000위에서 RECOMMEND_FETCH_MAX 명을 더 받는다
# ②는 ①과 뽑힌 방식이 다르다(내가 만난 사람) — 섞이면 편향되니 출처별 인원을 화면에 적는다.
SRC_PICK, SRC_OPP, SRC_RECOMMEND = "pick", "opp", "recommend"


@dataclass
class Candidate:
    nickname: str
    rank: int
    source: str
    team_value: int | None
    players: list[dict]          # 선발(교체 28 제외) — spId · spPosition · spGrade


@dataclass
class RecCard:
    spid: int
    users: int                                         # 이 줄에 이 카드를 쓴 후보 수
    rate: float                                        # users / 후보 수
    grades: Counter = field(default_factory=Counter)


@dataclass
class Standing:
    """내 스쿼드가 후보 분포의 어디쯤 — above 는 나보다 높은 후보 비율(0~1). 모르면 None."""
    name: str
    mine: float | None
    median: float | None
    above: float | None
    n: int


@dataclass
class Recommend:
    color: str | None = None
    by_source: Counter = field(default_factory=Counter)  # 출처 → 후보 수
    ranks: tuple[int, int] | None = None                # 쓰인 순위 범위
    enough: bool = False                                # 후보가 RECOMMEND_MIN_RANKERS 이상
    lines: list[tuple[str, list[RecCard]]] = field(default_factory=list)  # 내가 안 쓰는 카드만
    standings: list[Standing] = field(default_factory=list)
    formation: tuple[str | None, int, int] = (None, 0, 0)   # (내 포메이션, 같은 후보 수, 후보 수)

    @property
    def total(self) -> int:
        return sum(self.by_source.values())


def _ranker_players(conn, h: dict | None, t: dict, oldest: str) -> list[dict] | None:
    """받아 둔 랭커 줄 → 선발. 못 쓰면(안 받음·실패·닉네임 바뀜·경기 지워짐·오래됨) None."""
    if h is None or not h.get("match_id") or h.get("nickname") != t["nickname"]:
        return None
    detail = store.load_match(conn, h["match_id"])
    if detail is None or str(detail.get("matchDate") or "")[:10] < oldest:
        return None
    return _starters(detail, h.get("ouid")) or None


def _latest_detail(conn, ouid: str) -> dict | None:
    """그 구단주의 마지막 감독모드 경기 — MAX 와 같이 고른 줄이라 정렬이 없다. 기간은 부르는 쪽이 색인의
    last_day(감독모드 경기만 센다)로 이미 걸렀다."""
    r = conn.execute("SELECT m.match_id, MAX(m.match_date) FROM match_players p JOIN matches m ON m.match_id = p.match_id"
                     " WHERE p.ouid = ? AND m.match_type = ?", (ouid, config.DEFAULT_MATCH_TYPE)).fetchone()
    if r is None or r[0] is None:
        return None
    return store.load_match(conn, r[0])


def my_team_color(rows: list[dict], nickname: str | None, cached: str | None) -> str | None:
    """마지막 스냅숏의 내 행 → 없으면(1만 밖) 팀컬러 캐시 → 둘 다 없으면 None.
    최근 스쿼드로 추정하지 않는다 — 틀린 추천보다 없음이 낫다(ROADMAP 11-lite)."""
    if nickname:
        row = next((r for r in rows if r["nickname"] == nickname), None)
        if row is not None and row.get("team_color"):
            return row["team_color"]
    return cached or None


def recommend_targets(rows: list[dict], color: str | None, have_count: int, me: str | None = None) -> list[dict]:
    """③ 더 받을 랭커 — 후보(①+②)가 모자랄 때만, 내 팀컬러 201~RANKER_RECOMMEND_TOP 위에서 순위 순으로
    RECOMMEND_FETCH_MAX 명(이미 받은 사람도 넘긴다 — 3일 안이면 collect 의 due 가 거른다)."""
    if not color or have_count >= config.RECOMMEND_MIN_RANKERS:
        return []
    out = [r for r in rows if r.get("team_color") == color and r["nickname"] != me
           and config.RANKER_PICK_TOP < r["rank"] <= config.RANKER_RECOMMEND_TOP]
    out.sort(key=lambda r: r["rank"])
    return out[:config.RECOMMEND_FETCH_MAX]


def recommend_candidates(conn, rows: list[dict], color: str | None, me: str | None = None,
                         today: date | None = None) -> list[Candidate]:
    """내 팀컬러 후보 ①②③ — rows 는 마지막 스냅숏(1만). 요청 없음(DB 만 읽는다). 나는 닉네임으로 뺀다
    (스냅숏 행에서 빼면 ② 의 색인 쪽으로도 안 간다)."""
    if not color:
        return []
    today = today or date.today()
    oldest = (today - timedelta(days=config.RANKER_PICK_MAX_AGE_DAYS)).isoformat()
    since_opp = (today - timedelta(days=config.CARD_OWNER_DAYS)).isoformat()
    mine = [r for r in rows if r.get("team_color") == color and r["nickname"] != me]
    have = store.ranker_squads(conn)
    out: list[Candidate] = []
    rest: list[dict] = []
    for r in mine:
        players = _ranker_players(conn, have.get(r["profile_sn"]), r, oldest)
        if players:
            src = SRC_PICK if r["rank"] <= config.RANKER_PICK_TOP else SRC_RECOMMEND
            out.append(Candidate(r["nickname"], r["rank"], src, r.get("team_value"), players))
        else:
            rest.append(r)
    # ② 내 DB 의 상대 — 닉네임으로 색인의 구단주를 찾는다(마지막 사용일이 기간 안인 사람만)
    by_nick = {r["nickname"]: r for r in rest}
    found: list[tuple[dict, str]] = []
    names = list(by_nick)
    since_day = date.fromisoformat(since_opp).toordinal()
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        q = ",".join("?" * len(chunk))
        for ouid, nick in conn.execute(f"SELECT ouid, nickname FROM squad_owner WHERE nickname IN ({q})"
                                       f" AND last_day >= ?", (*chunk, since_day)):
            found.append((by_nick[nick], ouid))
    found.sort(key=lambda t: t[0]["rank"])
    for r, ouid in found[:config.RECOMMEND_OPPONENT_MAX]:
        detail = _latest_detail(conn, ouid)
        players = _starters(detail, ouid) if detail else []
        if players:
            out.append(Candidate(r["nickname"], r["rank"], SRC_OPP, r.get("team_value"), players))
    out.sort(key=lambda c: c.rank)
    return out


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _standing(name: str, mine: float | None, theirs: list[float]) -> Standing:
    above = (sum(1 for v in theirs if v > mine) / len(theirs)) if (mine is not None and theirs) else None
    return Standing(name, mine, _median(theirs), above, len(theirs))


def _grade_avg(players: list[dict]) -> float | None:
    g = [p.get("spGrade") for p in players if isinstance(p.get("spGrade"), int)]
    return sum(g) / len(g) if g else None


def recommend(cands: list[Candidate], color: str | None, my_players: list[dict], my_value: int | None,
              top_per_line: int = 5) -> Recommend:
    """후보 → 줄별 대체 카드(내가 안 쓰는 것) · 내 스쿼드 위치. 후보가 RECOMMEND_MIN_RANKERS 미만이면 enough=False
    (화면은 "추천 안 함"과 인원만 — 표는 비운다)."""
    out = Recommend(color=color)
    out.by_source.update(c.source for c in cands)
    if cands:
        out.ranks = (min(c.rank for c in cands), max(c.rank for c in cands))
    out.enough = len(cands) >= config.RECOMMEND_MIN_RANKERS
    if not out.enough:
        return out
    n = len(cands)
    mine = {p.get("spId") for p in my_players}
    per_line: list[dict[int, RecCard]] = [{} for _ in PITCH_ROWS]
    for c in cands:
        codes = [p["spPosition"] for p in c.players]
        for ri, row in enumerate(_row_index(codes)):
            seen: set[int] = set()
            for i in row:
                p = c.players[i]
                if p["spId"] in mine or p["spId"] in seen:
                    continue
                seen.add(p["spId"])
                rc = per_line[ri].setdefault(p["spId"], RecCard(p["spId"], 0, 0.0))
                rc.users += 1
                rc.grades[p.get("spGrade")] += 1
    for ri, (name, _rng) in enumerate(PITCH_ROWS):
        cards = [rc for rc in per_line[ri].values() if rc.users >= config.RECOMMEND_MIN_USERS]
        for rc in cards:
            rc.rate = rc.users / n
        cards.sort(key=lambda rc: (-rc.users, rc.spid))
        if cards:
            out.lines.append((name, cards[:top_per_line]))
    out.standings = [
        _standing("구단가치", my_value, [c.team_value for c in cands if c.team_value is not None]),
        _standing("선발 강화 평균", _grade_avg(my_players),
                  [g for g in (_grade_avg(c.players) for c in cands) if g is not None]),
    ]
    my_form = formation_of(my_players) if my_players else None
    out.formation = (my_form, sum(1 for c in cands if formation_of(c.players) == my_form) if my_form else 0, n)
    return out
