"""스펙업 순서(121 조각 ②) 받기 — [랭커 픽 › 스펙업 순서] 탭이 보이는 동안 내 선발과 후보 카드의 시세·능력치·팀컬러를 넥슨 홈페이지에서.

요청 길 셋(새 fetch 함수 없음 — 셋 다 이미 `config.WEB_DATA` 스위치·`ranker.web_get` 동시 상한 뒤):
    팀컬러 목록     teamcolor.fetch_list              7일(`TEAMCOLOR_CACHE_DAYS`) — 스냅숏의 팀컬러 이름을 번호로 바꾸는 표
    카드 페이지     playerinfo.collect_cards          시세(하루) · 급여(30일)
    시뮬레이터 1강  playerinfo.fetch_player_ability   포지션 16칸 OVR · 소속·관계 팀컬러(30일)

| 무엇 | 규칙 |
|---|---|
| 관문 | 처음 동의 · 웹 데이터 · 안내 17(`config.specup_allowed`) — 로더가 요청마다 다시 본다 |
| 대상 | `specup.Inputs.fetch_order`(내 선발 → 많이 쓴 후보) · 캐시가 신선한 카드는 요청 0(카드 자동 채우기·칩·가계부가 받은 것도) |
| 순서 | 팀컬러 목록 → 카드마다 (페이지 → 시뮬레이터) — 도중에 다른 화면으로 가도 앞 카드는 온전하게 |
| 계수 · 간격 | 하루 `SPECUP_FETCH_MAX`(쓰임 `store.BUDGET_SPECUP` · 셋 합쳐 · 요청 직전에만) · 요청 사이 `CARD_BACKFILL_GAP_S` |
| 막힘 | 403·429·Cloudflare → 그날 `specup_block` + 끝 · 그날 다른 웹 길(`store.card_block_kinds`)이 막혔으면 시작 안 함 |
| 점검 | 같은 길에서 서로 다른 카드가 이어 두 번이면 이번 실행만 끝(`playerinfo.WebRun`) |
| 시도 기록 | 페이지 "p:<spid>"(칩·가계부와 같음) · 시뮬레이터 "c:<spid>:sim" · 목록 "t:list"(카드 자동 채우기와 같음 — 같은 대상을 두 길이 다르게 쉬지 않게) |
| 저장 | 한 요청 = 한 트랜잭션 · 끊겨도 반쪽이 안 남는다 |
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import config
import playerinfo
import store
import teamcolor


@dataclass
class Result:
    requests: int = 0
    saved: int = 0          # 저장까지 간 요청 — 0 이면 화면이 다시 읽을 것이 없다
    total: int = 0          # 이번에 받을 것이 있던 카드
    cards: int = 0          # 그중 끝까지 본 카드
    stop: str = ""          # playerinfo.WebRun.stop — "" · blocked · down · cap · cancel
    error: str = ""         # DB 잠김 등(종류만)


def _days_old(day: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(day or "")).days
    except ValueError:
        return None


def todo(conn: sqlite3.Connection, spids, today: date) -> list[tuple[int, bool, bool]]:
    """받을 것이 있는 카드만 → [(spid, 페이지, 시뮬레이터)]. 오늘 이미 시도했거나 쉬는 쪽은 뺀다."""
    day = today.isoformat()
    extras = store.load_card_extras(conn, spids)
    out = []
    for s in dict.fromkeys(spids):
        page = (not (store.card_price_fresh(conn, s, day) and store.card_info_fresh(conn, s, day, config.CARD_INFO_TTL_DAYS))
                and not store.try_skip(conn, f"p:{s}", day))
        age = _days_old((extras.get(s) or {}).get("sim_on"), today)
        sim = (age is None or age >= config.CARD_INFO_TTL_DAYS) and not store.try_skip(conn, f"c:{s}:sim", day)
        if page or sim:
            out.append((s, page, sim))
    return out


def _list(conn, run: playerinfo.WebRun, today: date, fetch) -> bool:
    """팀컬러 목록이 비었거나 7일 지났으면 받는다 → 저장했나."""
    day = today.isoformat()
    _meta, fresh = store.load_teamcolor_meta(conn, today, config.TEAMCOLOR_CACHE_DAYS)
    if fresh or store.try_skip(conn, "t:list", day):
        return False
    try:
        items = run.call("list", fetch)
    except (playerinfo.PlayerInfoError, teamcolor.TeamColorError) as e:
        if e.kind == "format":
            store.try_record(conn, "t:list", day, ok=False)
        return False
    if items is None:
        return False
    store.save_teamcolor_meta(conn, items, day)
    store.try_record(conn, "t:list", day, ok=True)
    return True


def _sim(conn, run: playerinfo.WebRun, spid: int, day: str, fetch) -> bool:
    key = f"c:{spid}:sim"
    try:
        sim = run.call("sim", fetch, spid)
    except playerinfo.PlayerInfoError as e:
        if e.kind == "format":
            store.try_record(conn, key, day, ok=False)
        return False
    if sim is None:
        return False
    ok = playerinfo.sim_ok(sim)
    if ok:
        playerinfo.save_card_extra_sim(conn, spid, sim, day)
    store.try_record(conn, key, day, ok=ok)
    return ok


def run(db_path: Path | str, spids, today: date, *, want_list: bool = True, cancel: Callable[[], bool] = lambda: False,
        on_progress: Callable[[int, int], object] = lambda n, m: None, fetch_page=None, fetch_sim=None, fetch_list=None,
        wait: Callable[[float], object] = time.sleep) -> Result:
    """한 실행 — 모자란 것만 위부터. wait 는 요청 사이 간격(로더는 취소 이벤트의 wait 를 넘겨 끊기게 한다).
    on_progress(끝까지 본 카드, 받을 카드) — "시세 모으는 중 n/m장"."""
    fetch_sim = fetch_sim or (lambda s: playerinfo.fetch_player_ability(s, 1))
    fetch_list = fetch_list or teamcolor.fetch_list
    out = Result()
    day = today.isoformat()
    conn = store.open_db(db_path)
    try:
        web = playerinfo.WebRun(conn, day, store.BUDGET_SPECUP, config.SPECUP_FETCH_MAX, cancel,
                                gap_s=config.CARD_BACKFILL_GAP_S, wait=wait, blocks=store.card_block_kinds())
        try:
            if web.ready():
                if want_list:
                    out.saved += _list(conn, web, today, fetch_list)
                jobs = todo(conn, spids, today)
                out.total = len(jobs)
                if jobs and not web.stop:
                    on_progress(0, out.total)
                for spid, page, sim in jobs:
                    if page:
                        out.saved += playerinfo.collect_cards(conn, [spid], day, web.kind, web.cap, fetch=fetch_page,
                                                              need=lambda s: True, run=web)[0]
                    if sim:
                        out.saved += _sim(conn, web, spid, day, fetch_sim)
                    if web.stop:
                        break
                    out.cards += 1
                    on_progress(out.cards, out.total)
        except sqlite3.Error as e:
            out.error = type(e).__name__   # 잠김 — 그 장 버리고 끝(다음에 날짜 열로 다시 고른다)
        out.requests, out.stop = web.requests, web.stop
        return out
    finally:
        conn.close()


__all__ = ["Result", "run", "todo"]
