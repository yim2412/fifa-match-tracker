"""랭커가 게임에 저장한 팀 전술·개인 전술(110 ④) — 스쿼드메이커 칸 해석·판정·받기. 화면은 app_main(조각 ④-2), 저장은 하지 않습니다.

한 사람의 흐름(`collect`):
  마지막 경기 선발 11명 (카드, 자리) → 팀마다 전술 1(6요청) → 선발 카드가 그 칸 18명 안에 다 있는 팀만 전술 2~10(팀마다 9요청) →
  칸의 선발 (카드, 자리) 11쌍이 경기와 정확히 같은 칸을 고르고, 내용이 같은 칸은 하나로 합칩니다.
  맞는 팀이 없으면 6요청에서 끝냅니다(관문 실측: 60칸을 다 받아도 늘지 않았습니다).

규칙은 ROADMAP 110 "조각 ④ 관문 측정 — 규칙" 과 같은 식이고, `tools/measure_110.py --app` 이 받아 둔 70명으로 대조합니다.

| 규칙 | 어디 |
|---|---|
| 강화는 안 봅니다. 자리는 `role` 과 경기 `spPosition` 을 `tactic_codes.POSITION_ROLE` 로 견줍니다(칸의 `formation` 글자는 안 씁니다) | `match` |
| 내용 = 팀 전술 10값 + 선발의 (자리, 개인 전술, 참여도). 개인 전술 0 = 기본값 = 키 없음 · 참여도 키 없음·빈 목록 = (2, 2) | `parse_cell` |
| 특성 형식이 바뀌어도 전술은 삽니다 — `traitcollect.parse_team` 을 안 거치고 필요한 값만 읽습니다. 교체 선수의 전술 값은 안 봅니다 | `parse_cell` |
| 차례: 키(경기를 넘겨받지 않았을 때만) → 관문 → 막힘·형식 표시 → 오픈API 로 경기 → 기억해 둔 결과(`cached`) → 남은 몫 → 받기 | `_collect` |
| 경기를 넘겨받으면(구단주 비교) 오픈API 0 · 그날 429 도 안 봅니다 | `_lineup` |
| 선발이 11명이 아닌 판(몰수)은 건너뛰고 `TACTIC_RECENT` 판까지 · `RANKER_PICK_MAX_AGE_DAYS` 넘은 판에서 멈춥니다 | `_pick_game` |
| 저장해 둔 경기는 DB 에서 읽고, 없으면 상세를 캐시 없이 받아 버립니다(남의 경기를 이 PC 에 남기지 않습니다) | `_lineup` |
| 요청 직전마다 취소 → 간격 → 취소 → 관문 → 하루 계수(`tactic_web`) | `_Web.cell` |
| 남은 몫이 6 미만이면 요청 0 · 6요청 뒤 남은 팀 × 9 가 남은 몫보다 크면 거기서 끝(받다 버리는 요청을 줄입니다) | `_collect` |
| 막힘(rate) → `tactic_web_block`(카드·스펙업·특성도 그날 쉽니다 — `store.card_block_kinds`) · 형식 → `tactic_web_format`(특성은 안 멈춥니다) | `_Web.cell` |
| 오픈API 는 `rankerpick._Session`(재시도 없음 · 429 면 `TACTIC_429_WAIT_S` 쉬고 한 번 더) · 두 번째 429 는 `openapi` 에도 표시 | `_lineup` |
| 예외 → 상태는 한 곳 | `state_of` |
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import islice
from typing import Any, Callable

import config
import matchtime
import rankerpick
import store
import tactic_codes
import traitcollect
from nexon_api import KEY_INVALID_CODE, NETWORK_CODE, QUOTA_CODE, NexonAPIError
from stats import formation_of
from traitcollect import RC_EMPTY, RC_OK, TraitError

Team = tuple[int, int]                 # (n1TeamType, n1TeamPart)
CellKey = tuple[int, int, int]         # (n1TeamType, n1TeamPart, n4TeamSeq)
Pair = tuple[int, str]                 # (카드, 스쿼드메이커 자리)

SEQS = tuple(range(10))                # 한 팀의 전술 1~10
FIRST_REQ = len(config.TRAIT_TEAM_ORDER)
TEAM_REQ = len(SEQS) - 1
DEFAULT_PART = (2, 2)

# 끝난 상태 전부 — "ok" 말고는 글이 하나씩 있다(notice.TACTIC_TEXTS · test_tactics 가 양쪽을 대조)
STATES = ("ok", "none", "old", "nomatch", "outside", "noraw", "limit", "notice", "off_web", "off_collect", "nokey", "key",
          "nouser", "quota", "net", "server", "rate", "format", "down", "dbbusy", "busy", "clearing", "cancelled", "error")
FINAL_STATES = ("ok", "none")          # 끝까지 받은 것 — 화면이 이 실행 동안 기억해도 되는 상태


# ── 칸 해석(순수 함수) ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class TacticPlayer:
    role: str
    spid: int
    strategy: tuple[tuple[str, int], ...]   # (코드, 단계) — 기본값(0)은 뺀다 · 코드 순
    part: tuple[int, int]                   # 참여도 (공격, 수비) 1~3


@dataclass(frozen=True)
class Cell:
    cards: frozenset[int]                   # 18명
    players: tuple[TacticPlayer, ...]       # 선발
    team: tuple[tuple[str, int], ...]       # tactic_codes.TEAM_KEYS 순

    @property
    def pairs(self) -> frozenset[Pair]:
        return frozenset((p.spid, p.role) for p in self.players)

    @property
    def content(self) -> tuple:
        return self.team, tuple(sorted((p.role, p.strategy, p.part) for p in self.players))


def _bad(what: str) -> TraitError:
    return TraitError(f"전술 칸 형식이 다릅니다: {what}", "format")


def _int(v, what: str) -> int:
    if isinstance(v, str) and v.isdecimal():
        return int(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    raise _bad(what)


def _strategy(raw) -> tuple[tuple[str, int], ...]:
    """[["AS","1"],…] → ((코드, 단계),…). 기본값 0 은 뺀다 · 같은 코드가 또 오면 앞의 것(번들 `vR`)."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _bad("strategy")
    out: dict[str, int] = {}
    for x in raw:
        if not isinstance(x, (list, tuple)) or len(x) != 2 or not isinstance(x[0], str):
            raise _bad("strategy")
        out.setdefault(tactic_codes.code_of(x[0]), _int(x[1], "strategy"))
    return tuple(sorted((c, lv) for c, lv in out.items() if lv))


def _part(raw) -> tuple[int, int]:
    """참여도 — 키 없음·빈 목록·범위 밖은 2(번들 `yR`). 목록이 아니면 형식 이상."""
    if raw is None:
        return DEFAULT_PART
    if not isinstance(raw, list):
        raise _bad("participation")
    if len(raw) < 2:
        return DEFAULT_PART
    a, b = (_int(x, "participation") for x in raw[:2])
    return (a if 1 <= a <= 3 else 2, b if 1 <= b <= 3 else 2)


def parse_cell(resp) -> Cell | None:
    """getingameinfo 응답 → Cell · 저장 안 된 칸(ResultCode -1)은 None · 구조 이상은 TraitError("format")."""
    if not isinstance(resp, dict):
        raise _bad("응답")
    code = resp.get("ResultCode")
    if code == RC_EMPTY:
        return None
    if code != RC_OK:
        raise _bad(f"ResultCode {code!r}")
    data = resp.get("ResultData")
    squad = data.get("Squadinfo") if isinstance(data, dict) else None
    players = squad.get("players") if isinstance(squad, dict) else None
    if not isinstance(players, list):
        raise _bad("players")
    st = squad.get("teamSt")
    if not isinstance(st, dict) or any(k not in st for k in tactic_codes.TEAM_KEYS):
        raise _bad("teamSt")
    cards, starters = set(), []
    for p in players:
        if not isinstance(p, dict):
            raise _bad("선수")
        spid, state = p.get("spid"), p.get("state")
        if not isinstance(spid, int) or isinstance(spid, bool) or state not in (0, 1) or isinstance(state, bool):
            raise _bad("spid·state")
        cards.add(spid)
        if state != 1:
            continue
        role = p.get("role")
        if not isinstance(role, str):
            raise _bad("role")
        starters.append(TacticPlayer(role.strip().lower(), spid, _strategy(p.get("strategy")), _part(p.get("participation"))))
    return Cell(frozenset(cards), tuple(starters), tuple((k, _int(st[k], "teamSt")) for k in tactic_codes.TEAM_KEYS))


# ── 판정(순수 함수) ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Tactic:
    cells: tuple[CellKey, ...]              # 같은 내용이 저장된 칸 — 칸 순서
    team: tuple[tuple[str, int], ...]
    players: tuple[TacticPlayer, ...]


def lineup_of(players: list[dict]) -> frozenset[Pair] | None:
    """경기 선발 → (카드, 자리) 11쌍. 11명이 아니거나 모르는 자리가 있으면 None(그 판은 견줄 수 없다)."""
    pairs = {(p["spId"], tactic_codes.POSITION_ROLE[p["spPosition"]]) for p in players
             if p["spPosition"] in tactic_codes.POSITION_ROLE}
    return frozenset(pairs) if len(players) == 11 == len(pairs) else None


def candidate_teams(first: dict[Team, Cell | None], lineup: frozenset[Pair]) -> list[Team]:
    """전술 1 칸의 18명 안에 선발 카드가 다 있는 팀 — config.TRAIT_TEAM_ORDER 순(대표 A → 클럽 C)."""
    want = {spid for spid, _role in lineup}
    return [t for t in config.TRAIT_TEAM_ORDER if want and (c := first.get(t)) is not None and want <= c.cards]


def match(lineup: frozenset[Pair], cells: dict[CellKey, Cell | None]) -> list[Tactic]:
    """선발 11쌍이 같은 칸 → 서로 다른 전술(칸 순서 · 같은 내용은 하나로). 여럿이어도 칸 수로 줄 세우지 않는다(대개 동률이다)."""
    order = {t: i for i, t in enumerate(config.TRAIT_TEAM_ORDER)}
    groups: dict[tuple, list[CellKey]] = {}
    for key in sorted(cells, key=lambda k: (order.get(k[:2], len(order)), k[2])):
        c = cells[key]
        if c is not None and lineup and c.pairs == lineup:
            groups.setdefault(c.content, []).append(key)
    return [Tactic(tuple(keys), cells[keys[0]].team, cells[keys[0]].players) for keys in groups.values()]


def cell_label(key: CellKey) -> str:
    kind, part, seq = key
    return f"{'대표' if kind == 1 else '클럽'} {'ABC'[part]}팀 전술 {seq + 1}"


# ── 받기 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Target:
    sn: int
    nick: str
    ouid: str | None = None
    details: tuple | None = None            # 이미 가진 경기 상세(새것부터) — 있으면 오픈API 0


@dataclass
class TacticResult:
    state: str = "error"
    tactics: list[Tactic] = field(default_factory=list)
    formation: str = ""                     # 경기 쪽 포메이션
    match_id: str = ""
    match_at: datetime | None = None        # KST
    requests: int = 0                       # 스쿼드메이커 요청
    total: int | None = None                # 다 받으면 몇 요청인가 — 6요청 뒤에 정해진다


class _End(Exception):
    """상태가 정해져 그만둔다."""

    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


_TRAIT_STATE = {"off": "off_web", "down": "down", "format": "format", "rate": "rate"}
_NO_USER_CODES = ("OPENAPI00004", "OPENAPI00009")


def state_of(exc: BaseException) -> str:
    """받다가 난 예외 → 상태(STATES). 없는 닉네임은 여기서 못 가린다 — 같은 코드가 다른 조회에서도 온다(`_find_ouid`)."""
    if isinstance(exc, _End):
        return exc.state
    if isinstance(exc, TraitError):
        return _TRAIT_STATE.get(exc.kind, "error")
    if isinstance(exc, rankerpick._Stop):
        return "cancelled"
    if isinstance(exc, rankerpick._Limit):
        return "limit"
    if isinstance(exc, rankerpick._Quota):
        return "quota"
    if isinstance(exc, NexonAPIError):
        if exc.code == KEY_INVALID_CODE:
            return "key"
        if exc.code == QUOTA_CODE or exc.status == 429:
            return "quota"
        return "net" if exc.code == NETWORK_CODE else "server"
    return "dbbusy" if isinstance(exc, sqlite3.Error) else "error"


def _mark(conn, day: str, kind: str) -> None:
    """그날 멈춤 표시 — 못 적어도 원래 까닭을 덮지 않는다(DB 잠김이 "막힘" 글을 지우지 않게)."""
    try:
        store.budget_mark_429(conn, day, kind)
    except sqlite3.Error:
        pass


class _Web:
    """스쿼드메이커 칸 하나씩 — 취소 → 간격 → 취소 → 관문 → 하루 계수 → 요청."""

    def __init__(self, session, conn, day, sn, res: TacticResult, *, allowed, cancel, wait, clock, fetch, progress):
        self.session, self.conn, self.day, self.sn, self.res = session, conn, day, sn, res
        self.allowed, self.cancel, self.wait, self.clock, self.fetch, self.progress = allowed, cancel, wait, clock, fetch, progress
        self._last: float | None = None

    def left(self) -> int:
        return config.TACTIC_WEB_DAILY_REQ - store.budget_used(self.conn, self.day, store.BUDGET_TACTIC_WEB)

    def cell(self, team: Team, seq: int) -> Cell | None:
        if self.cancel():
            raise _End("cancelled")
        if self._last is not None:
            rest = self._last + config.RANKER_PICK_GAP_S - self.clock()
            if rest > 0:
                self.wait(rest)
        if self.cancel():
            raise _End("cancelled")
        why = self.allowed()
        if why:
            raise _End(why)
        if not store.budget_take(self.conn, self.day, store.BUDGET_TACTIC_WEB, config.TACTIC_WEB_DAILY_REQ):
            raise _End("limit")
        self._last = self.clock()
        self.res.requests += 1
        try:
            out = parse_cell(self.fetch(self.session, self.sn, team[0], team[1], seq))
        except TraitError as e:
            if e.kind == "rate":
                _mark(self.conn, self.day, store.BUDGET_TACTIC_WEB_BLOCK)
            elif e.kind == "format":
                _mark(self.conn, self.day, store.BUDGET_TACTIC_WEB_FORMAT)
            raise
        if self.progress is not None:
            self.progress(self.res.requests, self.res.total)
        return out


def _pick_game(details, ouid: str, now: datetime) -> tuple[dict, list[dict], datetime]:
    """새것부터 온 상세에서 견줄 수 있는 첫 경기 → (상세, 선발, KST 시각). 없으면 _End("old" · "nomatch")."""
    oldest = now - timedelta(days=config.RANKER_PICK_MAX_AGE_DAYS)
    for d in islice(details, config.TACTIC_RECENT):
        at = matchtime.parse((d or {}).get("matchDate"))
        if at is None:
            continue
        if at < oldest:
            raise _End("old")
        players = rankerpick._starters(d, ouid)
        if lineup_of(players) is not None:
            return d, players, at
    raise _End("nomatch")


def _find_ouid(call, conn, api, target: Target) -> str:
    """넘겨받은 것 → 랭커 픽이 받아 둔 것(닉네임이 그대로일 때 — 스냅숏 뒤에 바뀌었어도 찾는다) → 닉네임으로 1요청."""
    if target.ouid:
        return target.ouid
    h = store.ranker_squads(conn).get(target.sn) or {}
    if h.get("ouid") and h.get("nickname") == target.nick:
        return h["ouid"]
    try:
        return call(lambda: api.get_ouid(target.nick, attempts=1))
    except NexonAPIError as e:
        if e.code in _NO_USER_CODES or (not e.code and e.status is None):
            raise _End("nouser") from e
        raise


def _lineup(conn, day: str, target: Target, api, *, allowed, cancel, wait, clock, now: datetime):
    if target.details is not None:
        return _pick_game(target.details, target.ouid or "", now)
    if store.budget_hit_429(conn, day, (store.BUDGET_OPENAPI, store.BUDGET_TACTIC_API)):
        raise _End("quota")
    # 요청 수를 따로 센다 — 같이 세면 진행 글자가 (6/6) 을 넘어 시작한다
    s = rankerpick._Session(conn, day, cancel, wait, clock, rankerpick.PickResult(), store.BUDGET_TACTIC_API,
                            config.TACTIC_API_DAILY_REQ, wait_429=config.TACTIC_429_WAIT_S)

    def call(fn):
        why = allowed()
        if why:
            raise _End(why)
        return s.call(fn)

    def details(ouid: str):
        for mid in call(lambda: api.get_match_ids(ouid, config.DEFAULT_MATCH_TYPE, 0, config.TACTIC_RECENT, attempts=1)):
            yield store.load_match(conn, mid) or call(lambda m=mid: api.get_match_detail_nocache(m, attempts=1))

    try:
        ouid = _find_ouid(call, conn, api, target)
        return _pick_game(details(ouid), ouid, now)
    except rankerpick._Quota:
        _mark(conn, day, store.BUDGET_OPENAPI)   # 내 검색·거래도 그날 쉬게 — 같은 키다
        raise


def _collect(res: TacticResult, session, conn, target: Target, api, *, allowed, cancel, wait, clock, now_fn, fetch, cached,
             progress) -> TacticResult:
    if target.details is None and not config.API_KEY:
        raise _End("nokey")
    why = allowed()
    if why:
        raise _End(why)
    now = now_fn()
    day = now.date().isoformat()
    if store.budget_hit_429(conn, day, store.card_block_kinds()):
        raise _End("rate")
    if store.budget_hit_429(conn, day, (store.BUDGET_TACTIC_WEB_FORMAT,)):
        raise _End("format")
    detail, players, at = _lineup(conn, day, target, api, allowed=allowed, cancel=cancel, wait=wait, clock=clock, now=now)
    res.formation, res.match_id, res.match_at = formation_of(players), str(detail.get("matchId") or ""), at
    hit = cached(res.match_id) if cached is not None else None
    if hit is not None:
        return hit
    web = _Web(session, conn, day, target.sn, res, allowed=allowed, cancel=cancel, wait=wait, clock=clock, fetch=fetch,
               progress=progress)
    if web.left() < FIRST_REQ:
        raise _End("limit")
    lineup = lineup_of(players)
    first = {t: web.cell(t, SEQS[0]) for t in config.TRAIT_TEAM_ORDER}
    teams = candidate_teams(first, lineup)
    res.total = FIRST_REQ + TEAM_REQ * len(teams)
    if TEAM_REQ * len(teams) > web.left():
        raise _End("limit")
    cells: dict[CellKey, Cell | None] = {(*t, SEQS[0]): first[t] for t in teams}
    for t in teams:
        for seq in SEQS[1:]:
            cells[(*t, seq)] = web.cell(t, seq)
    res.tactics = match(lineup, cells)
    res.state = "ok" if res.tactics else "none"
    return res


def collect(session, conn, target: Target, api, *, allowed: Callable[[], str] | None = None,
            cancel: Callable[[], bool] = lambda: False, wait: Callable[[float], Any] = time.sleep,
            clock: Callable[[], float] = time.monotonic, now_fn: Callable[[], datetime] = datetime.now, fetch=None,
            cached: Callable[[str], TacticResult | None] | None = None,
            progress: Callable[[int, int | None], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None) -> TacticResult:
    """한 구단주의 저장 전술을 받는다(머리말 표). 예외를 올리지 않고 상태로 돌려준다.

    allowed = 막는 까닭("" 이면 받아도 된다 · 기본 config.tactic_block) · wait = 취소 이벤트의 wait(닫으면 바로 돌아온다) ·
    cached(경기 번호) = 이 실행 동안 기억해 둔 결과 · progress(받은 수, 전체 수 또는 None) · on_error = 모르는 예외(crash.log 몫)."""
    res = TacticResult()
    try:
        return _collect(res, session, conn, target, api, allowed=allowed or config.tactic_block, cancel=cancel, wait=wait,
                        clock=clock, now_fn=now_fn, fetch=fetch or traitcollect.fetch_cell, cached=cached, progress=progress)
    except Exception as e:  # noqa: BLE001 — 갈래는 state_of 한 곳
        res.state = state_of(e)
        if res.state == "error" and on_error is not None:
            on_error(e)
        return res
