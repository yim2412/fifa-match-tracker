"""포지션 특성(32단계) — 랭커가 카드에 넣어 둔 특성·훈련 코치를 넥슨 스쿼드메이커에서 읽는다. 화면 없음.

    구단주 번호  POST https://fconline.nexon.com/squadmakerapi/SquadMakerProc  strMethod=getownerinfo · strCharacterName=<닉네임> → ResultData.sn
    팀 칸        〃                                                              strMethod=getingameinfo · n1TeamType · n1TeamPart · n4TeamSeq · n8TargetNexonSN=<sn>

문서 없는 주소라 형식이 바뀌면 깨진다 — 주소·키는 전부 이 파일 상단과 config(TRAIT_*)에. `X-Requested-With` 가 있어야 하고
(teamcolor 와 같다) 넘김은 따라가지 않고 오류로 본다(allow_redirects=False). 로그인 없이 남의 구단주도 열려 있다(ROADMAP 32단계 "단계" 0).
요청은 `ranker.web_get`(동시 상한) 경유 · 첫 줄에서 `config.WEB_DATA` 가 꺼져 있으면 요청 0.

한 사람의 흐름(`collect` — 로더는 app_main.TraitLoader):
  마지막 경기 선발 11 (spid, 자리, 강화) → 팀 칸(대표·클럽 × A·B·C = 6) 을 차례로 받아 **경기 선발 카드가 다 있는 팀**을 찾는다 →
  경기 카드마다 (spid, 자리, 강화, 특성3, 코치3). 맞는 팀을 찾으면 **거기서 멈춘다**(`find_team`) · 6칸 다 안 맞으면 `stale`(낡은 웹 값).
  구단주 번호는 묻지 않는다 — 랭킹 스냅숏의 profile_sn 이 스쿼드메이커 sn 과 같다(2026-10-07 실측 3/3 · getownerinfo 는 check_api 만).
  마지막 경기는 랭커 픽 길(rankerpick.collect)이 ranker_squads·matches 에 둔 것을 읽는다 — 이 파일은 오픈API 를 안 부른다.

받는 규칙(`collect`):
| 규칙 | 어디 |
|---|---|
| 웹을 보는 사람: 특성 줄이 없음 · 경기 id 가 바뀜 · 낡은 값(stale)이고 TRAIT_STALE_RETRY_DAYS 지남 | `_web_due` |
| 경기가 그대로면 요청 0 — 오픈API 다시 확인이 지나갔으면 checked_at 만 새로(14일 정리에 안 지워지게) | `collect` |
| 마지막 경기가 RANKER_PICK_MAX_AGE_DAYS 넘은 사람은 웹을 안 본다(집계가 어차피 뺀다) | `_web_due` |
| 지난번 팀 칸을 먼저(대개 1요청) | `collect` → `find_team(order=…)` |
| 실제 요청 직전에만 하루 계수(`trait_web` < TRAIT_WEB_DAILY_REQ) · 간격 RANKER_PICK_GAP_S | `_fetcher` |
| 막힘(rate) → 그날 멈춤 + `trait_web_block` 표시 · 형식 바뀜(format) → 그날 멈춤 + `trait_web_format` · 넘김·연결(down)은 이번만 | `collect` |
| 한 사람 = 한 트랜잭션(끊겨도 반쪽 줄이 안 남는다) · cancel 은 요청 사이에서 | `store.save_trait_squad` |

함정(2026-10-07 실측 — 25명 × 60칸 1,500요청):
- **`traits` 는 길이 3 이거나 `[-2]` 하나다**(칸이 전부 잠긴 카드 — 8,297칸). 길이 3 만 허용하면 그 카드마다 "형식 바뀜"으로 오판한다 → `[-2]` 는 (-2,-2,-2) 로 편다.
- **`trainer` 키는 코치를 안 넣은 카드엔 아예 없다**(선발 2/3 가 그렇다) — 교체 선수도 가질 수 있다. 없으면 None(= 코치 없음) · 형식 이상이 아니다.
- 칸의 `formation` 글자·`role` 은 자리와 안 맞는다(R2) → 포메이션·자리는 **경기 쪽**(`spPosition`)을 쓴다. 이 파일은 칸의 글자를 읽지 않는다.
- 저장 안 된 칸은 `ResultCode -1`(정상 빈칸 — 오류 아님) · 그 밖의 코드는 형식 이상.
- 선발 여부(`state` 1)로 팀을 가린다: 경기 선발 11 카드가 칸의 **선발** 카드에 다 있어야 그 팀이다(교체 명단 카드가 우연히 겹쳐 틀린 팀이 채택되지 않게).
- 경기 `spGrade` ≠ 칸 `buildUp` 인 카드는 그 카드만 뺀다 — 그 사이 강화를 바꿨으면 열린 칸이 달라 값이 그 경기의 것이 아니다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable

import requests

import config
import ranker  # web_get(동시 상한)
import rankerpick
import store
import trait_codes
from stats import formation_of

URL = "https://fconline.nexon.com/squadmakerapi/SquadMakerProc"
_XHR = {"X-Requested-With": "XMLHttpRequest"}
M_OWNER = "getownerinfo"
M_TEAM = "getingameinfo"
RC_OK = 1
RC_EMPTY = -1   # 저장 안 된 칸 — 정상
# Cloudflare 가 사람 확인을 요구하는 페이지(JSON 이 아닌 HTML) — 429·403 과 같은 "막힘"으로 본다
_CHALLENGE_MARKERS = ("Just a moment", "cf-chl", "challenge-platform", "cf_chl")


class TraitError(Exception):
    """kind: "off"(웹 데이터 꺼짐) · "down"(넘김·5xx·연결 실패·점검) · "format"(응답 구조가 바뀜) · "rate"(403·429·Cloudflare 확인)."""

    def __init__(self, message: str, kind: str = "down"):
        super().__init__(message)
        self.kind = kind


MESSAGES = {
    "off": config.WEB_DATA_OFF_MSG,
    "down": "넥슨 홈페이지가 응답하지 않습니다(점검 중일 수 있음)",
    "format": "넥슨 응답이 바뀌었습니다 — 특성 받기를 멈춥니다",
    "rate": "요청이 많아 넥슨이 잠시 막았습니다 — 오늘은 더 받지 않습니다",
}

def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = config.WEB_USER_AGENT
    return s


# ── 응답 → 값(순수 함수 — 네트워크 없음) ──────────────────────────────────────

@dataclass(frozen=True)
class TeamCard:
    """팀 칸의 선수 한 칸. traits = (일반, 신규, 훈련) · trainer = 코치 셋 또는 None(안 넣음)."""
    spid: int
    state: int                      # 1 선발 · 0 교체
    build_up: int | None            # 칸의 강화(`buildUp`) — 모르면 None(경기 강화와 같다고 볼 수 없다)
    traits: tuple[int, int, int]
    trainer: tuple[int, int, int] | None


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _traits(raw) -> tuple[int, int, int] | None:
    """길이 3 → 그대로 · [-2](칸 전부 잠김) → (-2,-2,-2) · 그 밖은 None(형식 이상)."""
    if not isinstance(raw, list) or not all(_is_int(x) for x in raw):
        return None
    if len(raw) == 3:
        return trait_codes.order_slots((raw[0], raw[1], raw[2]))
    if len(raw) == 1 and raw[0] == -2:
        return (-2, -2, -2)
    return None


def _trainer(raw) -> tuple[int, int, int] | None:
    if isinstance(raw, list) and len(raw) == 3 and all(_is_int(x) for x in raw):
        return (raw[0], raw[1], raw[2])
    return None   # 키가 없음·None = 코치 없음. 이상한 모양도 코치 없음으로 본다(필수 필드가 아니다)


def parse_team(resp) -> list[TeamCard] | None:
    """getingameinfo 응답 → 선수 칸 목록 · 저장 안 된 칸(ResultCode -1)은 None.
    구조 이상(ResultCode 1 인데 Squadinfo.players 없음 · 선수의 spid·state·traits 이상)은 TraitError("format").
    필요한 칸(spid·state·buildUp·traits·trainer)만 읽는다."""
    if not isinstance(resp, dict):
        raise TraitError("팀 칸 응답이 객체가 아닙니다", "format")
    code = resp.get("ResultCode")
    if code == RC_EMPTY:
        return None
    if code != RC_OK:
        raise TraitError(f"팀 칸 ResultCode {code!r}", "format")
    data = resp.get("ResultData")
    squad = data.get("Squadinfo") if isinstance(data, dict) else None
    players = squad.get("players") if isinstance(squad, dict) else None
    if not isinstance(players, list):
        raise TraitError("Squadinfo.players 가 없습니다", "format")
    out: list[TeamCard] = []
    for p in players:
        if not isinstance(p, dict):
            raise TraitError("선수 칸이 객체가 아닙니다", "format")
        spid, state = p.get("spid"), p.get("state")
        traits = _traits(p.get("traits"))
        if not _is_int(spid) or state not in (0, 1) or not _is_int(state) or traits is None:
            raise TraitError("선수 칸에 spid·state·traits 가 없거나 모양이 다릅니다", "format")
        build = p.get("buildUp")
        out.append(TeamCard(spid, state, build if _is_int(build) else None, traits, _trainer(p.get("trainer"))))
    return out


def parse_owner(resp) -> int | None:
    """getownerinfo 응답 → sn · 그 닉네임이 없으면 None · ResultCode 1 인데 sn 이 없으면 format."""
    if not isinstance(resp, dict):
        raise TraitError("구단주 응답이 객체가 아닙니다", "format")
    if resp.get("ResultCode") != RC_OK:
        return None
    data = resp.get("ResultData")
    sn = data.get("sn") if isinstance(data, dict) else None
    if not _is_int(sn):
        raise TraitError("구단주 응답에 sn 이 없습니다", "format")
    return sn


# ── 판정(순수 함수) ──────────────────────────────────────────────────────────

Starter = tuple[int, int, int | None]   # (spid, spPosition, spGrade) — 경기 선발 한 명


@dataclass(frozen=True)
class PickedCard:
    spid: int
    position: int                   # 경기의 spPosition
    grade: int
    traits: tuple[int, int, int]
    trainer: tuple[int, int, int] | None


@dataclass
class TeamPick:
    team: tuple[int, int]           # (n1TeamType, n1TeamPart)
    cards: list[PickedCard]
    dropped: int                    # 강화가 달라 뺀 카드 수


def team_matches(starters: Iterable[Starter], cards: list[TeamCard]) -> bool:
    """경기 선발 카드가 칸의 **선발(state 1)** 카드에 전부 있나. 선발이 비었으면(데이터 없음) 거짓."""
    want = {s[0] for s in starters}
    if not want:
        return False
    have = {c.spid for c in cards if c.state == 1}
    return want <= have


def pick_cards(team: tuple[int, int], starters: list[Starter], cards: list[TeamCard]) -> TeamPick:
    """채택한 팀 칸에서 경기 카드마다 (spid, 자리, 강화, 특성, 코치) — 강화가 다른 카드만 뺀다."""
    by_spid = {c.spid: c for c in cards if c.state == 1}
    out: list[PickedCard] = []
    dropped = 0
    for spid, pos, grade in starters:
        c = by_spid.get(spid)
        if c is None:
            dropped += 1
            continue
        if grade is None or c.build_up != grade:
            dropped += 1
            continue
        out.append(PickedCard(spid, pos, grade, c.traits, c.trainer))
    return TeamPick(team, out, dropped)


@dataclass
class FindResult:
    state: str                      # "ok" · "stale"
    pick: TeamPick | None
    calls: int                      # 칸 요청을 몇 번 불렀나
    blank: int = 0                  # 저장 안 된 칸(ResultCode -1) 수
    seen: list[tuple[int, int]] = field(default_factory=list)


def find_team(starters: list[Starter], fetch: Callable[[int, int], list[TeamCard] | None],
              order: Iterable[tuple[int, int]] | None = None) -> FindResult:
    """팀을 차례로 받아 맞는 팀을 찾으면 **거기서 멈춘다**. fetch(team_type, part) → 칸 목록 또는 None(빈칸).
    order 를 주면 그 순서(지난번 팀을 맨 앞에 — 다음 단계) · 아니면 config.TRAIT_TEAM_ORDER. 6칸 다 안 맞으면 stale.
    fetch 의 TraitError 는 그대로 올린다(그날 멈출지는 부르는 쪽이 정한다)."""
    res = FindResult("stale", None, 0)
    for team in (tuple(order) if order is not None else config.TRAIT_TEAM_ORDER):
        cards = fetch(team[0], team[1])
        res.calls += 1
        res.seen.append(team)
        if cards is None:
            res.blank += 1
            continue
        if team_matches(starters, cards):
            res.state = "ok"
            res.pick = pick_cards(team, starters, cards)
            return res
    return res


# ── 요청 ─────────────────────────────────────────────────────────────────────

def _post(session, data: dict) -> dict:
    if not config.WEB_DATA:
        raise TraitError(config.WEB_DATA_OFF_MSG, "off")
    try:
        res = ranker.web_get(session, URL, method="post", data=data, headers=_XHR,
                             timeout=config.TRAIT_TIMEOUT_S, allow_redirects=False)
    except requests.RequestException as e:
        raise TraitError(f"특성 조회 실패: {e}", "down") from e
    status = getattr(res, "status_code", 200)
    headers = getattr(res, "headers", None) or {}
    if status in (403, 429) or str(headers.get("cf-mitigated", "")).lower() == "challenge":
        raise TraitError(f"특성 조회가 막혔습니다(HTTP {status})", "rate")
    if status != 200:   # 302(오류 페이지로 넘김) · 5xx 등 — 넘김은 따라가지 않는다
        raise TraitError(f"특성 조회 실패(HTTP {status})", "down")
    try:
        return res.json()
    except ValueError as e:
        body = (getattr(res, "text", "") or "")[:4096]
        if any(m in body for m in _CHALLENGE_MARKERS):
            raise TraitError("Cloudflare 확인 페이지가 왔습니다", "rate") from e
        raise TraitError("응답이 JSON 이 아닙니다", "format") from e


def fetch_owner_sn(session, nickname: str) -> int | None:
    """닉네임 → 구단주 번호(sn). 없는 닉네임이면 None."""
    return parse_owner(_post(session, {"strMethod": M_OWNER, "strCharacterName": nickname}))


def fetch_team(session, sn: int, team_type: int, part: int, seq: int | None = None) -> list[TeamCard] | None:
    """한 팀 칸(전술 seq — 기본 config.TRAIT_TACTIC_SEQ) → 선수 목록 · 저장 안 된 칸이면 None."""
    return parse_team(_post(session, {
        "strMethod": M_TEAM, "n1TeamType": team_type, "n1TeamPart": part,
        "n4TeamSeq": config.TRAIT_TACTIC_SEQ if seq is None else seq, "n8TargetNexonSN": sn}))


# ── 받기(로더가 부른다 — 파일 머리말 "받는 규칙") ─────────────────────────────

@dataclass
class TraitResult:
    checked: int = 0        # 웹을 본 사람(저장까지)
    touched: int = 0        # 경기가 그대로라 요청 없이 checked_at 만 새로
    requests: int = 0
    limit: bool = False     # 오늘 웹 상한(trait_web)
    stop: str = ""          # 멈춘 까닭 — TraitError.kind(rate·format 은 그날 · down·off 는 이번만)
    error: str = ""         # 그 문구(MESSAGES 꼴)
    cancelled: bool = False
    stale: int = 0          # 카드가 다 있는 팀이 없던 사람(낡은 웹 값)
    cards: int = 0          # 저장한 카드
    dropped: int = 0        # 강화가 달라 뺀 카드(재지 않은 것 — 비율을 이 값으로 잰다)


class _Stop(Exception):
    pass


class _WebLimit(Exception):
    pass


def _starters_of(conn, h: dict) -> tuple[list[dict], str | None]:
    detail = store.load_match(conn, h["match_id"])
    if detail is None:
        return [], None
    return rankerpick._starters(detail, h.get("ouid")), (str(detail.get("matchDate") or "")[:10] or None)


def _web_due(t: dict, h: dict | None, tr: dict | None, now: datetime) -> bool | None:
    """True = 웹을 본다 · False = 경기가 그대로(요청 0) · None = 볼 수 없다(마지막 경기 없음·실패·닉네임 바뀜·오래된 경기)."""
    if h is None or not h.get("match_id") or h.get("nickname") != t["nickname"]:
        return None
    oldest = (now.date() - timedelta(days=config.RANKER_PICK_MAX_AGE_DAYS)).isoformat()
    if (h.get("match_day") or "") < oldest:
        return None
    if tr is None or tr.get("match_id") != h["match_id"]:
        return True
    if tr.get("state") != "ok":
        retry = (now - timedelta(days=config.TRAIT_STALE_RETRY_DAYS)).isoformat(timespec="seconds")
        return (tr.get("checked_at") or "") < retry
    return False


def _team_key(team: tuple[int, int]) -> str:
    return f"{team[0]}-{team[1]}"


def _team_order(key: str | None) -> list[tuple[int, int]]:
    """지난번 팀을 맨 앞에 — 나머지는 config 순서."""
    order = list(config.TRAIT_TEAM_ORDER)
    try:
        last = tuple(int(x) for x in (key or "").split("-"))
    except ValueError:
        last = ()
    if last in order:
        order.remove(last)
        order.insert(0, last)
    return order


def collect(session, conn, targets: list[dict], *, on_at: str | None, now_fn=datetime.now,
            cancel: Callable[[], bool] = lambda: False, sleep=time.sleep, clock=time.monotonic,
            on_person: Callable[[], None] | None = None, fetch=None) -> TraitResult:
    """대상(스냅숏 상위 TRAIT_TOP — 순위 순)마다 웹을 볼지 정해 차례로 받는다. on_at = 저장 때 켠 시각(D5 —
    다르면 켤 때 정리가 지운다). fetch(session, sn, team_type, part) 는 테스트가 바꿔 끼운다(기본 fetch_team)."""
    fetch = fetch or fetch_team
    res = TraitResult()
    day = now_fn().date().isoformat()
    for kind, why in ((store.BUDGET_TRAIT_WEB_BLOCK, "rate"), (store.BUDGET_TRAIT_WEB_FORMAT, "format")):
        if store.budget_hit_429(conn, day, (kind,)):
            res.stop, res.error = why, MESSAGES[why]
            return res
    picks = store.ranker_squads(conn)
    traits = store.trait_squads(conn)
    last: list[float | None] = [None]

    def fetcher(sn: int):
        def one(team_type: int, part: int):
            if cancel():
                raise _Stop
            if not store.budget_take(conn, day, store.BUDGET_TRAIT_WEB, config.TRAIT_WEB_DAILY_REQ):
                raise _WebLimit
            if last[0] is not None:
                wait = last[0] + config.RANKER_PICK_GAP_S - clock()
                if wait > 0:
                    sleep(wait)
            last[0] = clock()
            res.requests += 1
            return fetch(session, sn, team_type, part)
        return one

    for t in targets:
        if cancel():
            res.cancelled = True
            break
        sn = t["profile_sn"]
        h, tr = picks.get(sn), traits.get(sn)
        due = _web_due(t, h, tr, now_fn())
        if due is None:
            continue
        stamp = now_fn().isoformat(timespec="seconds")
        if not due:
            if (h.get("fetched_at") or "") > (tr.get("checked_at") or ""):
                store.touch_trait_squad(conn, sn, rank=t.get("rank"), checked_at=stamp)
                res.touched += 1
            continue
        players, mday = _starters_of(conn, h)
        if not players:
            continue
        starters = [(p["spId"], p["spPosition"], p.get("spGrade")) for p in players]
        try:
            found = find_team(starters, fetcher(sn), _team_order((tr or {}).get("team")))
        except _Stop:
            res.cancelled = True
            break
        except _WebLimit:
            res.limit = True
            break
        except TraitError as e:
            res.stop, res.error = e.kind, MESSAGES.get(e.kind, str(e))
            if e.kind == "rate":
                store.budget_mark_429(conn, day, store.BUDGET_TRAIT_WEB_BLOCK)
            elif e.kind == "format":
                store.budget_mark_429(conn, day, store.BUDGET_TRAIT_WEB_FORMAT)
            break
        body = None
        if found.pick is not None:
            body = [[c.spid, c.position, c.grade, list(c.traits), list(c.trainer) if c.trainer else None]
                    for c in found.pick.cards]
            res.cards += len(body)
            res.dropped += found.pick.dropped
        else:
            res.stale += 1
        store.save_trait_squad(conn, sn, source=store.source_for_rank(t.get("rank"), store.TRAIT), rank=t.get("rank"),
                               match_id=h["match_id"], formation=formation_of(players),
                               team=_team_key(found.pick.team) if found.pick else None, body=body, state=found.state,
                               collect_on_at=on_at, match_day=mday or h.get("match_day"), checked_at=stamp)
        res.checked += 1
        if on_person is not None:
            on_person()
    return res


def api_targets(rows: list[dict]) -> list[dict]:
    """스냅숏 상위 TRAIT_TOP → 오픈API 대상(rankerpick.collect 한 번에) — 1~200 은 랭커 픽 그대로(3일 · 몫 없음),
    201~500 은 TRAIT_STALE_DAYS 주기 + 특성 몫. 구간마다 따로 부르지 않는다(rankerpick.due 머리말)."""
    out = []
    for r in rows:
        rank = r.get("rank") or 0
        if rank <= config.RANKER_PICK_TOP:
            out.append(dict(r))
        elif rank <= config.TRAIT_TOP:
            out.append(dict(r, stale_days=config.TRAIT_STALE_DAYS, share="trait"))
    return out


@dataclass
class TraitRun:
    """로더 한 바퀴 — 웹(이미 경기가 있는 사람) → 오픈API → 웹(새로 받은 경기)."""
    web: list[TraitResult] = field(default_factory=list)
    pick: rankerpick.PickResult | None = None
    block_streak: int = 0       # 막힌 날이 이어진 수 — TRAIT_BLOCK_DAYS 면 화면 스레드가 특성 받기를 끈다
    error: str = ""             # DB 잠김 등

    @property
    def last_web(self) -> TraitResult | None:
        return self.web[-1] if self.web else None

    @property
    def requests(self) -> int:
        return sum(w.requests for w in self.web)

    @property
    def cancelled(self) -> bool:
        return any(w.cancelled for w in self.web) or bool(self.pick and self.pick.cancelled)


def run(api, session, conn, rows: list[dict], *, on_at: str | None, cancel: Callable[[], bool] = lambda: False,
        on_person: Callable[[], None] | None = None, record_days: Callable[[list[dict]], None] | None = None,
        now_fn=datetime.now, sleep=time.sleep, clock=time.monotonic, fetch=None) -> TraitRun:
    """한 바퀴. 웹을 먼저 — 랭커 픽이 이미 받아 둔 1~200 은 오픈API 없이 바로 찬다. 웹이 멈췄으면(상한·막힘·형식·연결)
    오픈API 도 안 쓴다(오늘 읽지 못할 경기를 받는 셈). 오픈API 바퀴가 끝까지 돌았으면(cancel 아님) 1~200 을 rank.db
    날짜별 픽에 더한다(record_days — N15: 특성 로더가 1~200 의 경기를 새로 덮으면 랭커 픽 로더가 앞 경기를 영영 못 센다)."""
    out = TraitRun()
    kw = dict(now_fn=now_fn, cancel=cancel, sleep=sleep, clock=clock)
    first = collect(session, conn, rows, on_at=on_at, on_person=on_person, fetch=fetch, **kw)
    out.web.append(first)
    if not (first.cancelled or first.limit or first.stop):
        out.pick = rankerpick.collect(api, conn, api_targets(rows), on_ranker=on_person, **kw)
        if not (cancel() or out.pick.cancelled):
            if record_days is not None:
                record_days([r for r in rows if (r.get("rank") or 0) <= config.RANKER_PICK_TOP])
            if out.pick.checked:
                out.web.append(collect(session, conn, rows, on_at=on_at, on_person=on_person, fetch=fetch, **kw))
    if out.last_web is not None and out.last_web.stop == "rate":
        out.block_streak = store.trait_block_streak(conn)
    return out
