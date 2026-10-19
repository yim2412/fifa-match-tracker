"""카드 정보 자동 채우기(51단계 조각 ①) — 검색한 구단주 경기에 나온 선수 카드의 키·체형·자리별 OVR·팀컬러·시세를 넥슨 데이터센터에서.

요청 길 셋(새 fetch 함수 없음 — 셋 다 이미 `config.WEB_DATA` 스위치·`ranker.web_get` 동시 상한 뒤):
    카드 페이지     playerinfo.fetch_player_info      키·몸무게·체형·시세(강화 1~13) — 칩·가계부 캐시(card_info·card_prices)도 같이 채운다(⑧)
    시뮬레이터 1강  playerinfo.fetch_player_ability   포지션 16칸 OVR · 소속·관계 팀컬러(강화 팀컬러는 강화 단계로만 정해진다 — F2)
    팀컬러 상세     teamcolor.fetch_detail(·fetch_list) 단계별 최소 인원 · 효과(7일 · 효과 창과 같은 표)

규칙(ROADMAP "## 51단계" — 표 번호는 거기 것):
| 무엇 | 규칙 | 어디 |
|---|---|---|
| 관문 | 처음 동의 · 웹 데이터 · 안내 12 · 토글 "카드 정보 자동 채우기" — **랭킹 수집은 안 본다**(U5) | `config.card_backfill_allowed`·`read_card_backfill` |
| 수요(G5) | `accounts` 구단주의 최근 `CARD_BACKFILL_DAYS` 경기 양쪽 선발 · 등장 순 — 랭커 픽·구단주 분포는 안 넣는다 · 측정 도구는 고정 집합(`window`) | `demand` |
| 요청 순서 | ① 내 카드 시세 → ② 팀컬러 상세(목록 7일) → ④ C6 표본 시세 → ③ 처음 보는·반쪽 카드 → ⑤ 30일 갱신 · 측정 모드는 ②③만 | `plan` |
| 상한 | 하루 `CARD_BACKFILL_DAILY_REQ`(그중 팀컬러 `CARD_BACKFILL_TC_DAILY_REQ`) · 틱 `CARD_BACKFILL_TICK_REQ` · 간격 `CARD_BACKFILL_GAP_S` · **요청 직전에만** 계수(실패·빈 응답도 센다) | `run` |
| 시도 기록 | 그날 시도한 대상은 성공·실패 모두 그날 다시 안 고른다 · 형식 실패만 `fails` · 3번이면 7일 쉼 · 막힘·점검은 안 센다 | `store.try_skip`·`try_record` |
| 막힘 | 403·429·Cloudflare → 그 바퀴 끝 + 그날 `<kind>_block` · 그날 다른 웹 길(특성·측정 도구)이 막혔으면 시작 안 함 · 켠 날 뒤로 서로 다른 날 `TRAIT_BLOCK_DAYS` 이어지면 스스로 끔(창 쪽 예약이) | `run`·`store.card_block_streak` |
| 점검 | 점검·오류 페이지 → 그 바퀴만 끝(막힘으로 안 센다) | `run` |
| 형식 | 페이지에 이름·OVR·키·체형이 없거나 시뮬레이터가 16칸이 아니면 저장 안 함 + `fails` · 서로 다른 대상 `CARD_BACKFILL_FORMAT_STOP` 이 이어지면 그날 멈춤 `<kind>_format` | `run` |
| 저장 | 한 요청 = 한 트랜잭션 · 페이지·시뮬레이터 따로(G4) · 날짜 열로 판정(진행 표 없음) · 끄거나 지워도 안 지운다(G3) | `playerinfo.save_card_extra_*` |
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import config
import playerinfo
import store
import teamcolor

OFF_REASON_TEXT = {"blocked": "넥슨이 요청을 계속 막아 스스로 껐습니다 — 다시 켜면 이어서 채웁니다"}
_MATCH_CHUNK = 500   # IN (…) 한 번에 넣는 경기 수 — SQLite 변수 상한(32,766) 안에서 넉넉히
_ENHANCE_EMBLEM = "teamcolor/"   # 강화 팀컬러(금빛 물결 등 5개)의 엠블럼 종류 — 2026-10-18 목록 실측


@dataclass(frozen=True)
class Job:
    step: str          # mine(①) · tc_list · tc(②) · c6(④) · fill(③) · refresh(⑤)
    key: int           # spid · 팀컬러 id(목록은 0)
    page: bool = False
    sim: bool = False


@dataclass
class Ctx:
    """한 바퀴의 계획과, 저장할 때 다시 보는 것(내 카드 · C6 표본 · 대표 강화)."""
    jobs: list[Job] = field(default_factory=list)
    mine: dict[int, set[int]] = field(default_factory=dict)
    panel: set[int] = field(default_factory=set)
    rep: dict[int, int | None] = field(default_factory=dict)
    demand: int = 0


@dataclass
class RunResult:
    requests: int = 0
    done: int = 0            # 저장까지 간 요청 수
    skipped_cap: int = 0     # 하루·틱 상한에 걸려 못 한 일 수
    blocked: bool = False    # 403·429·Cloudflare(오늘 막힘 — 다른 길의 막힘 포함)
    format_err: bool = False
    down: bool = False       # 점검·연결 실패 — 그 바퀴만
    error: str = ""          # DB 잠김 등(종류만)
    planned: int = 0
    status: "Status | None" = None   # 바퀴 끝 상태(앱 수요) — [정보] 줄이 화면 스레드에서 수요를 다시 세지 않게(0.4초 · 리뷰 [중]2)


# ── 수요 · 계획 ─────────────────────────────────────────────────────────

def demand(conn: sqlite3.Connection, today: date, *, days: int = config.CARD_BACKFILL_DAYS,
           window=None) -> list[tuple[int, int, int | None]]:
    """→ [(spid, 등장 수, 대표 강화)] 등장 많은 순(같으면 spid). window(측정 도구의 고정 집합 — 우선순위 순 spid)를 주면 그대로.

    잇는 길: accounts.ouid → squad_owner.id → match_squads(owner, day) 로 그 구단주 경기 → 같은 match 의 모든 줄(양쪽 선발).
    matches 에는 계정 열이 없다(3판 A[중]7). 정렬은 파이썬에서(ORDER BY count 는 임시 정렬)."""
    if window is not None:
        return [(int(s), 0, None) for s in dict.fromkeys(window)]
    since = today.toordinal() - days
    owners = [r[0] for r in conn.execute(
        "SELECT so.id FROM accounts a JOIN squad_owner so ON so.ouid = a.ouid")]
    matches: set[int] = set()
    for o in owners:
        matches.update(r[0] for r in conn.execute(
            "SELECT match FROM match_squads WHERE owner = ? AND day > ?", (o, since)))
    seen: Counter = Counter()
    grades: dict[int, Counter] = {}
    ms = sorted(matches)
    for i in range(0, len(ms), _MATCH_CHUNK):
        part = ms[i:i + _MATCH_CHUNK]
        q = ",".join("?" * len(part))
        for spid, grade in conn.execute(f"SELECT spid, grade FROM match_squads WHERE match IN ({q})", part):
            seen[spid] += 1
            grades.setdefault(spid, Counter())[grade] += 1
    out = []
    for spid, n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])):
        g = [x for x in grades[spid].items() if x[0] is not None]
        out.append((spid, n, min(g, key=lambda kv: (-kv[1], kv[0]))[0] if g else None))
    return out


def my_cards(conn: sqlite3.Connection, today: date) -> dict[int, set[int]]:
    """확정 내 계정(trade_state my_ouid — N23 과 같은 정의)의 최근 CARD_MY_DAYS 선발 → {spid: {강화}}. 확정 전이면 {}."""
    mine = store.trade_state(conn).get("my_ouid")
    if not mine:
        return {}
    r = conn.execute("SELECT id FROM squad_owner WHERE ouid = ?", (mine,)).fetchone()
    if r is None:
        return {}
    out: dict[int, set[int]] = {}
    for spid, grade in conn.execute("SELECT spid, grade FROM match_squads WHERE owner = ? AND day > ?",
                                    (r[0], today.toordinal() - config.CARD_MY_DAYS)):
        if grade is not None:
            out.setdefault(spid, set()).add(grade)
    return out


def c6_panel(dem: list[tuple[int, int, int | None]], extras: dict[int, dict]) -> set[int]:
    """C6 표본 — 등장의 C6_PANEL_SHARE 를 덮는 상위 카드 + 한 번 든 카드(c6_grade 가 있는 것)는 수요에 있는 동안 남긴다."""
    total = sum(n for _s, n, _g in dem)
    out, acc = set(), 0
    for spid, n, _g in dem:
        if total and acc >= config.C6_PANEL_SHARE * total:
            break
        out.add(spid)
        acc += n
    out.update(s for s, _n, _g in dem if (extras.get(s) or {}).get("c6_grade") is not None)
    return out


def _extras(conn: sqlite3.Connection) -> dict[int, dict]:
    return {r["spid"]: dict(r) for r in conn.execute(
        "SELECT spid, page_on, sim_on, club_tc, rel_tc, c6_grade FROM card_extra")}


def _days_since(day: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(day or "")).days
    except ValueError:
        return None


def _tc_ids(extras: dict[int, dict]) -> Counter:
    """백필한 카드의 소속·관계 팀컬러 id → 나온 카드 수."""
    c: Counter = Counter()
    for ex in extras.values():
        for col in ("club_tc", "rel_tc"):
            try:
                items = json.loads(ex.get(col) or "[]")
            except ValueError:
                continue
            for it in items:
                if isinstance(it, list) and it and isinstance(it[0], int):
                    c[it[0]] += 1
    return c


def plan(conn: sqlite3.Connection, today: date, *, dem, mine: dict[int, set[int]], measure: bool = False) -> Ctx:
    """요청 순서 ①②④③⑤(측정 모드는 ②③)를 일 목록으로. 오늘 시도·쉼인 대상(card_tries)은 뺀다."""
    day = today.isoformat()
    extras = _extras(conn)
    mine = {} if measure else mine   # 측정 모드는 내 카드 시세(①)도 C6(④)도 없다 — 측정 수요가 앱의 표본을 정하지 않게
    ctx = Ctx(mine=mine, demand=len(dem), rep={s: g for s, _n, g in dem})
    ctx.panel = set() if measure else c6_panel(dem, extras)
    jobs = ctx.jobs

    def page_free(spid):
        return not store.try_skip(conn, f"c:{spid}:page", day)

    def sim_free(spid):
        return not store.try_skip(conn, f"c:{spid}:sim", day)

    # ① 내 카드 시세(C5) — 오늘 그 카드의 내 강화 줄이 다 있지 않으면
    for spid, grades in sorted(mine.items()):
        have = {r[0] for r in conn.execute("SELECT grade FROM card_price_days WHERE spid = ? AND day = ?", (spid, day))}
        if not grades <= have and page_free(spid):
            jobs.append(Job("mine", spid, page=True))
    # ② 팀컬러 상세 — 목록(강화 팀컬러·있는 id 를 안다)이 7일 넘었으면 목록부터
    meta, fresh = store.load_teamcolor_meta(conn, today, config.TEAMCOLOR_CACHE_DAYS)
    if not fresh and not store.try_skip(conn, "t:list", day):
        jobs.append(Job("tc_list", 0))
    if extras and meta:
        known = {m["id"] for m in meta}
        want = _tc_ids(extras)
        for m in meta:
            if str(m["emblem"]).startswith(_ENHANCE_EMBLEM):
                want[m["id"]] += len(extras)   # 강화 팀컬러는 어느 라인업에나 걸릴 수 있다 — 먼저
        got = {r[0]: r[1] for r in conn.execute("SELECT id, MIN(fetched_on) FROM teamcolor_steps GROUP BY id")}
        for tid, _n in sorted(want.items(), key=lambda kv: (-kv[1], kv[0])):
            if tid not in known:
                continue   # 목록에 없는 id(단일팀 등)는 상세가 없다
            age = _days_since(got.get(tid), today)
            if (age is None or age >= config.TEAMCOLOR_CACHE_DAYS) and not store.try_skip(conn, f"t:{tid}", day):
                jobs.append(Job("tc", tid))
    order = [s for s, _n, _g in dem]
    if not measure:
        # ④ C6 표본 시세 — 이미 페이지를 읽은 카드 중 마지막 관측이 C6_REFRESH_DAYS 이상 지난 것(처음 보는 카드는 ③ 이 읽으며 적는다)
        for spid in order:
            if spid not in ctx.panel or not (extras.get(spid) or {}).get("page_on"):
                continue
            g = extras[spid].get("c6_grade")
            g = ctx.rep.get(spid) if g is None else g
            if g is None:
                continue
            last = conn.execute("SELECT MAX(day) FROM card_price_days WHERE spid = ? AND grade = ?", (spid, g)).fetchone()[0]
            age = _days_since(last, today)
            if (age is None or age >= config.C6_REFRESH_DAYS) and page_free(spid):
                jobs.append(Job("c6", spid, page=True))
    # ③ 처음 보는 카드 · 반쪽 카드(빈 쪽만)
    for spid in order:
        ex = extras.get(spid) or {}
        p = not ex.get("page_on") and page_free(spid)
        s = not ex.get("sim_on") and sim_free(spid)
        if p or s:
            jobs.append(Job("fill", spid, page=p, sim=s))
    if not measure:
        # ⑤ 30일 갱신 — 낡은 쪽만 · 수요에서 빠진 카드는 안 한다
        for spid in order:
            ex = extras.get(spid)
            if not ex:
                continue
            pa, sa = _days_since(ex.get("page_on"), today), _days_since(ex.get("sim_on"), today)
            p = pa is not None and pa >= config.CARD_INFO_TTL_DAYS and page_free(spid)
            s = sa is not None and sa >= config.CARD_INFO_TTL_DAYS and sim_free(spid)
            if p or s:
                jobs.append(Job("refresh", spid, page=p, sim=s))
    # 같은 카드 페이지를 한 바퀴에 두 번 읽지 않는다(① 과 ④ 가 겹칠 때 — 저장이 두 쓰임을 같이 적는다)
    seen_page: set[int] = set()
    out = []
    for j in jobs:
        if j.page and j.key in seen_page:
            if not j.sim:
                continue
            j = Job(j.step, j.key, page=False, sim=True)
        if j.page:
            seen_page.add(j.key)
        out.append(j)
    ctx.jobs = out
    return ctx


# ── 저장 ────────────────────────────────────────────────────────────────

def _save_page(conn, ctx: Ctx, info: playerinfo.PlayerInfo, day: str) -> None:
    spid = info.sp_id
    playerinfo.save_card(conn, info, day)           # 칩·가계부 캐시도 같이(⑧ — 그쪽 중복 요청을 없앤다)
    playerinfo.save_card_extra_page(conn, info, day)
    prices = playerinfo.prices_as_int(info)
    rows = [(spid, g, day, prices.get(g), 1) for g in sorted(ctx.mine.get(spid, ()))]
    if spid in ctx.panel:
        r = conn.execute("SELECT c6_grade FROM card_extra WHERE spid = ?", (spid,)).fetchone()
        g = r[0] if r is not None and r[0] is not None else ctx.rep.get(spid)
        if g is not None:
            if r is None or r[0] is None:
                with conn:   # 처음 표본에 든 날 고른 강화를 바꾸지 않는다(대표 강화가 바뀌면 비가 튄다 — A[중]8)
                    conn.execute("UPDATE card_extra SET c6_grade = ? WHERE spid = ? AND c6_grade IS NULL", (g, spid))
            rows.append((spid, g, day, prices.get(g), 2))
    store.save_card_price_days(conn, rows)


# ── 한 바퀴 ─────────────────────────────────────────────────────────────

class _Stop(Exception):
    pass


def run(db_path: Path | str, today: date, cap: int, cancel: Callable[[], bool] = lambda: False, *,
        kind: str = store.BUDGET_CARD, demand_days: int = config.CARD_BACKFILL_DAYS, window=None,
        tick_req: int | None = config.CARD_BACKFILL_TICK_REQ, tc_cap: int = config.CARD_BACKFILL_TC_DAILY_REQ,
        fetch_page=None, fetch_sim=None, fetch_detail=None, fetch_list=None,
        gap_s: float = config.CARD_BACKFILL_GAP_S, sleep: Callable[[float], object] = time.sleep) -> RunResult:
    """한 바퀴(틱 하나) — 일 목록을 위부터 요청 tick_req 개까지. 요청 **직전에만** 그날 kind 계수(상한 cap) — 실패·빈 응답도 센다.
    웹 데이터가 꺼졌으면 요청도 계수도 없다. window 가 있으면 측정 모드(수요 = 그 고정 집합 · ①④⑤ 없음 · c6_grade 안 씀).
    sleep 은 요청 사이 간격 — 로더는 취소 이벤트의 wait 를 넘겨 끊기게 한다."""
    fetch_page = fetch_page or playerinfo.fetch_player_info
    fetch_sim = fetch_sim or (lambda s: playerinfo.fetch_player_ability(s, 1))
    fetch_detail = fetch_detail or teamcolor.fetch_detail
    fetch_list = fetch_list or teamcolor.fetch_list
    out = RunResult()
    if not config.WEB_DATA:
        return out
    day = today.isoformat()
    tc_kind, block_kind, format_kind = f"{kind}_tc", f"{kind}_block", f"{kind}_format"
    conn = store.open_db(db_path)
    measure = window is not None   # 측정 모드는 [정보] 줄 상태를 안 싣는다(앱 수요가 아니다)
    try:
        if store.budget_hit_429(conn, day, store.card_block_kinds()):
            out.blocked = True
            if not measure:
                _attach_status(conn, today, out)
            return out
        if store.budget_hit_429(conn, day, (format_kind,)):
            out.format_err = True
            if not measure:
                _attach_status(conn, today, out)
            return out
        dem = demand(conn, today, days=demand_days, window=window)
        ctx = plan(conn, today, dem=dem, mine={} if measure else my_cards(conn, today), measure=measure)
        out.planned = len(ctx.jobs)
        fails: list[tuple[str, int]] = []   # 이어진 형식 실패(서로 다른 대상)

        def failed(key: str, target: tuple[str, int]) -> None:
            store.try_record(conn, key, day, ok=False)
            if target not in fails:
                fails.append(target)
            if len(fails) >= config.CARD_BACKFILL_FORMAT_STOP:
                store.budget_mark_429(conn, day, format_kind)
                out.format_err = True
                raise _Stop("format")

        def ok(key: str) -> None:
            store.try_record(conn, key, day, ok=True)
            fails.clear()
            out.done += 1

        def call(fn, *a, is_tc=False):
            """→ 응답 · 팀컬러 몫이 찼으면 None(요청 없음). 막힘·점검은 _Stop, 형식(TeamColorError format)은 그대로 올린다."""
            if tick_req is not None and out.requests >= tick_req:
                raise _Stop("tick")
            if is_tc and store.budget_used(conn, day, tc_kind) >= tc_cap:
                return None
            if out.requests:
                sleep(gap_s)
            if cancel() or not config.WEB_DATA:
                raise _Stop("cancel")
            if not store.budget_take(conn, day, kind, cap):   # 요청 직전에만
                raise _Stop("cap")
            if is_tc:
                store.budget_take(conn, day, tc_kind, None)
            out.requests += 1
            try:
                return fn(*a)
            except (playerinfo.PlayerInfoError, teamcolor.TeamColorError) as e:
                if e.kind == "rate":
                    store.budget_mark_429(conn, day, block_kind)
                    out.blocked = True
                    raise _Stop("blocked") from e
                if e.kind == "format":
                    raise
                out.down = e.kind == "down"
                raise _Stop(e.kind) from e
            except Exception as e:  # noqa: BLE001 — 파싱 버그 등: 그 대상만 형식 실패로. 그냥 올리면 시도 기록이 안 남아
                # 줄 맨 앞의 같은 카드가 틱마다 요청 하나를 쓰고 바퀴를 끝내 백필 전체가 선다(리뷰 [하]1)
                err = teamcolor.TeamColorError if is_tc else playerinfo.PlayerInfoError
                raise err(f"{type(e).__name__}: {e}", "format") from e

        i = 0
        try:
            for i, job in enumerate(ctx.jobs):
                if job.step == "tc_list":
                    try:
                        items = call(fetch_list, is_tc=True)
                    except teamcolor.TeamColorError:
                        failed("t:list", ("t", 0))
                        continue
                    if items is not None:
                        store.save_teamcolor_meta(conn, items, day)
                        ok("t:list")
                    continue
                if job.step == "tc":
                    try:
                        got = call(fetch_detail, job.key, is_tc=True)
                    except teamcolor.TeamColorError:
                        failed(f"t:{job.key}", ("t", job.key))
                        continue
                    if got is not None:
                        store.save_teamcolor_steps(conn, job.key, got[1], day)
                        ok(f"t:{job.key}")
                    continue
                if job.page:
                    key = f"c:{job.key}:page"
                    try:
                        info = call(fetch_page, job.key)
                    except playerinfo.PlayerInfoError:
                        failed(key, ("c", job.key))
                        info = None
                    else:
                        if not playerinfo.page_ok(info) or info.body_type == "-":
                            failed(key, ("c", job.key))
                        else:
                            _save_page(conn, ctx, info, day)
                            ok(key)
                if job.sim:
                    key = f"c:{job.key}:sim"
                    try:
                        sim = call(fetch_sim, job.key)
                    except playerinfo.PlayerInfoError:
                        failed(key, ("c", job.key))
                        continue
                    if not playerinfo.sim_ok(sim):
                        failed(key, ("c", job.key))
                    else:
                        playerinfo.save_card_extra_sim(conn, job.key, sim, day)
                        ok(key)
            i = len(ctx.jobs)
        except _Stop as e:
            if str(e) in ("cap", "tick"):
                out.skipped_cap = len(ctx.jobs) - i
        except sqlite3.Error as e:
            out.error = type(e).__name__   # 잠김 — 그 장 버리고 바퀴 끝(다음 틱이 날짜 열로 다시 고른다)
        if not measure:
            _attach_status(conn, today, out)
        return out
    finally:
        conn.close()


# ── 상태 · 빈 화면 ───────────────────────────────────────────────────────

@dataclass
class Status:
    known: int        # 수요 카드 중 페이지·시뮬레이터 둘 다 있는 장수
    need: int         # 수요 장수
    used: int         # 오늘 쓴 요청
    blocked: bool     # 오늘 막힘(다른 웹 길 포함)


def status(conn: sqlite3.Connection, today: date) -> Status:
    dem = demand(conn, today)
    extras = _extras(conn)
    known = sum(1 for s, _n, _g in dem if (extras.get(s) or {}).get("page_on") and extras[s].get("sim_on"))
    day = today.isoformat()
    return Status(known, len(dem), store.budget_used(conn, day, store.BUDGET_CARD),
                  store.budget_hit_429(conn, day, store.card_block_kinds()))


def _attach_status(conn: sqlite3.Connection, today: date, out: RunResult) -> None:
    try:
        out.status = status(conn, today)
    except sqlite3.Error:
        pass   # 잠김 — 줄은 지난 값 그대로


def status_text(st: Status) -> str:
    """[정보] 토글 아래 한 줄."""
    parts = [f"카드 정보 {st.known:,}/{st.need:,}장", f"오늘 요청 {st.used:,}/{config.CARD_BACKFILL_DAILY_REQ:,}"]
    if st.blocked:
        parts.append("넥슨이 오늘 막아 내일 다시 받습니다")
    return " · ".join(parts)


def screen_state(conn: sqlite3.Connection | None, today: date):
    """새 화면의 빈 화면 사유(notice.card_empty_text 에 넘긴다) — web_off · toggle_off · blocked · ("filling", 아는 장수, 수요 장수)."""
    if not config.WEB_DATA:
        return "web_off"
    if not config.card_backfill_allowed():
        return "toggle_off"
    if conn is None:
        return ("filling", 0, 0)
    st = status(conn, today)
    if st.blocked:
        return "blocked"
    return ("filling", st.known, st.need)


def prune(db_path: Path | str, today: date) -> int:
    """켤 때 한 번(CachePruneWorker) — 날짜별 시세 보관을 넘긴 줄. 관문과 무관."""
    conn = store.open_db(db_path)
    try:
        return store.prune_card_price_days(conn, today)
    finally:
        conn.close()


def on_day(on_at: str) -> str:
    """토글 값(켠 시각 ISO) → 날짜 — 막힘이 이어진 날은 이 날 **뒤**만 센다."""
    return (on_at or "")[:10]


def should_turn_off(conn: sqlite3.Connection, on_at: str, kind: str = store.BUDGET_CARD) -> bool:
    """켠 날 뒤로 막힌 날이 TRAIT_BLOCK_DAYS 번 이어졌나 — 예약이 바퀴 끝마다 본다."""
    return bool(on_at) and store.card_block_streak(conn, kind, on_day(on_at)) >= config.TRAIT_BLOCK_DAYS


__all__ = ["Job", "Ctx", "RunResult", "Status", "demand", "my_cards", "c6_panel", "plan", "run", "status", "status_text",
           "screen_state", "prune", "on_day", "should_turn_off", "OFF_REASON_TEXT"]
