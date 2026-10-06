"""랭커 기록 받기(1.4.1 13단계) — 하루 캐시(store.ranker_stats) 우선, 모자란 쌍만 넥슨에 묻는다.

화면 없이 돈다 — 띄우기는 app_main.RankerStatsLoader(N1 메뉴 · N2 선수 카드 탭이 같이 쓴다).
값은 랭커들의 **경기당 평균**이라 다시 나누지 않는다(nexon_api.EP_RANKER_STATS 주석).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

import store
from nexon_api import RANKER_STATS_BATCH


def collect(api, conn: sqlite3.Connection, pairs, matchtype: int, day: str,
            cancel: Callable[[], bool] = lambda: False) -> dict[tuple[int, int], dict | None]:
    """(spid, po) → 응답 한 줄 | None(넥슨에 데이터 없음). 끊기면 받은 데까지 — 묶음마다 한 트랜잭션.
    요청이 실패하면(429·네트워크) 그 예외를 그대로 올린다 — 그때까지 받은 묶음은 저장돼 있다."""
    want = list(dict.fromkeys((int(s), int(p)) for s, p in pairs))
    have = store.load_ranker_stats(conn, want, matchtype, day)
    missing = [k for k in want if k not in have]
    for i in range(0, len(missing), RANKER_STATS_BATCH):
        if cancel():
            break
        batch = missing[i:i + RANKER_STATS_BATCH]
        rows = api.get_ranker_stats(matchtype, batch)
        store.save_ranker_stats(conn, batch, rows, matchtype, day)
        have.update(store.load_ranker_stats(conn, batch, matchtype, day))
    return have
