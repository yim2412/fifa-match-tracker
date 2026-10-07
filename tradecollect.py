"""거래 기록 받기(1.4.1) — 넥슨은 **API 키 주인 계정**의 거래만 준다(docs/DONE.md 1.4.1 R1·R2).

화면 없이 도는 한 번(`collect`)만 여기 있고, 띄우는 때·양보는 앱(`app_main.TradeLoader`)의 몫이다.
규칙은 docs/DONE.md 1.4.1 "내 계정 지정 · 거래 수집" 절 — 검토 1~5회차에서 [상]이 나온 자리라 바꾸기 전에 거기를 읽는다.

| 단계 | 무엇을 | 왜 |
|---|---|---|
| 키 지문 | 저장된 `key_fp` ≠ 지금 키 → 거래는 `trades_prev` 로 옮기고 받기 상태를 비워 첫 수집처럼 · 그 키로 돌아와 다 받으면 옮겨 둔 줄을 되돌린다 | 키 주인이 바뀌었는지 판정하려다 4회차 연속 [상] — 틀려도 잃는 건 다시 받기(약 160요청 · 19초). 지우지 않는 건 넥슨이 옛 거래를 버리는지 몰라서(재지 않은 것) |
| 위쪽(새 거래) | 0쪽부터 `top_stop − TRADE_OVERLAP_DAYS` 이하가 나올 때까지 · 하루 한 번 | 반영이 늦게 오는 옛 날짜 거래(R6)를 겹쳐 받는다. "저장된 saleSn 을 만나면 멈춤"은 끊긴 직전 실행의 새 묶음으로 채워져 가운데 구멍이 남았다(2회차 [상]) |
| 아래쪽(옛 거래) | `done_<kind>` 가 아니면 (저장 줄 수 − 100)쪽부터 빈 쪽까지 | 첫 수집이 끊겨도 옛 거래가 영구히 빠지지 않게(1회차 [상]). 새 거래가 위에 끼면 겹칠 뿐 건너뛰지 않는다 |
| 끊김 | 쪽마다 한 트랜잭션 · 쪽 사이에서 cancel · 429 면 그 자리에서 멈춤 | 다음 기회에 이어서 |


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
거래 기록 받기(1.4.1) — 넥슨 거래 API 는 **ouid 를 무시하고 API 키 주인 것만** 준다(docs/DONE.md 1.4.1 R1·R2) → `fifa.db trades` 에 ouid 열이 없고 `get_trades` 는 ouid 를 안 받는다.
`collect()` 한 번: 키 지문(`trade_state.key_fp`)이 다르면 **판정하지 않고 옛 주인 거래를 `trades_prev`(지문별)로 옮기고 다시**(옮긴 커밋 뒤 `on_wiped` → 화면 `_invalidate` ·
그 키로 돌아와 다 받으면 넥슨이 더는 안 주는 줄만 되돌린다 — 넥슨이 옛 거래를 버리는지 몰라 지우지 않는다) → 위쪽(0쪽부터 `top_stop` − 7일 이하까지 ·
하루 한 번 `fetched_at`) → 아래쪽(`done_<kind>` 가 아니면 저장 수 − 100쪽부터 빈 쪽까지). 받는 규칙은 검토 1~5회차 [상]이 나온 자리라 파일 머리말 표를 먼저 읽는다.
띄우기·양보는 `app_main.TradeLoader`/`start_trades`(검색·비교 끝 · 키 바꾼 직후 · 첫 실행 키 입력 뒤 · 거래 화면 열 때 / 새 검색·비교 시작 때 `cancel()` 만).
시세 캐시(`card_prices`)는 `playerinfo.collect_prices`(하루 상한은 **시도 수**로 — 시세가 빈 카드도 센다)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import config
import store
from nexon_api import QUOTA_CODE, TRADE_KINDS, TRADE_PAGE, NexonAPIError


@dataclass
class TradeResult:
    wiped: bool = False        # 키가 바뀌어 옛 주인 거래를 화면 표에서 뺐다(trades_prev 로 옮김)
    requests: int = 0
    added: int = 0
    quota: bool = False        # 429 — 상태는 그대로, 다음 기회에
    error: str | None = None   # 그 밖의 실패(네트워크 등) — 상태는 그대로
    cancelled: bool = False
    complete: bool = False     # 위쪽(오늘)·아래쪽 둘 다 끝


class _Stop(Exception):
    pass


def _overlap_floor(stop: str) -> str:
    """top_stop 날짜에서 겹침 일수를 뺀 경계(ISO 문자열 비교용)."""
    try:
        d = datetime.fromisoformat(stop)
    except ValueError:
        return ""  # 못 읽으면 경계 없음 — 빈 쪽까지(안전한 쪽)
    return (d - timedelta(days=config.TRADE_OVERLAP_DAYS)).isoformat(timespec="seconds")


def collect(api, conn, api_key: str, cancel: Callable[[], bool] = lambda: False,
            today: date | None = None, on_wiped: Callable[[], None] | None = None) -> TradeResult:
    """한 번 받기. on_wiped: 지우기를 커밋한 직후 — 화면이 들고 있던 옛 주인 거래를 버리게(받기보다 먼저)."""
    res = TradeResult()
    today_s = (today or date.today()).isoformat()
    fp = store.key_fingerprint(api_key)
    st = store.trade_state(conn)
    if st.get("key_fp") != fp:
        # 처음(지문 없음)이면 지울 게 없다 — 그래도 같은 길로(상태가 어떻든 깨끗하게 시작)
        res.wiped = st.get("key_fp") is not None
        store.reset_trades_for_key(conn, fp)
        if res.wiped and on_wiped:
            on_wiped()
        st = store.trade_state(conn)

    def page(kind: str, offset: int) -> list[dict]:
        if cancel():
            raise _Stop()
        res.requests += 1
        rows = api.get_trades(kind, offset=offset, limit=TRADE_PAGE)
        res.added += store.save_trades(conn, kind, rows)
        return rows

    try:
        if st.get("fetched_at") != today_s:
            for kind in TRADE_KINDS:
                _top(conn, kind, page)
            store.set_trade_state(conn, fetched_at=today_s)
        st = store.trade_state(conn)
        for kind in TRADE_KINDS:
            if st.get(f"done_{kind}") != "1":
                _bottom(conn, kind, page)
        res.complete = True
        res.added += store.restore_prev_trades(conn, fp)  # 넥슨이 더는 안 주는 옛 거래 — 다 받은 뒤라야 이어 받기를 안 흐린다
    except _Stop:
        res.cancelled = True
    except NexonAPIError as e:
        if e.code == QUOTA_CODE or e.status == 429:
            res.quota = True
        else:
            res.error = e.message
    return res


def _top(conn, kind: str, page) -> None:
    st = store.trade_state(conn)
    stop = st.get(f"top_stop_{kind}")
    if not stop:
        # 시작할 때 저장돼 있던 최신 날짜 — 끊긴 위쪽을 이을 때는 그대로 둔다(그사이 들어온 새 묶음으로 다시 잡으면 구멍).
        # 저장 0건(첫 수집)이면 "" — 빈 쪽까지. 첫 수집이 끊긴 뒤라면 저장된 건 맨 위부터 이어진 한 덩어리라 그 최신으로 잡아도 된다
        stop = store.trade_latest(conn, kind) or ""
        store.set_trade_state(conn, **{f"top_stop_{kind}": stop})
    floor = _overlap_floor(stop) if stop else ""
    offset = 0
    while True:
        rows = page(kind, offset)
        if not rows:
            store.set_trade_state(conn, **{f"done_{kind}": "1"})  # 맨 위부터 끝까지 이어 읽었다
            break
        oldest = min((r.get("tradeDate") or "" for r in rows if isinstance(r, dict)), default="")
        if floor and oldest and oldest <= floor:
            break
        offset += TRADE_PAGE
    store.set_trade_state(conn, **{f"top_stop_{kind}": None})


def _bottom(conn, kind: str, page) -> None:
    offset = max(0, store.trade_count(conn, kind) - TRADE_PAGE)
    while True:
        rows = page(kind, offset)
        if not rows:
            store.set_trade_state(conn, **{f"done_{kind}": "1"})
            return
        offset += TRADE_PAGE


def is_complete(st: dict) -> bool:
    """옛 거래까지 다 받았나 — 아니면 힌트·실현 손익을 비운다(뒤 구매와 잘못 짝짓지 않게)."""
    return all(st.get(f"done_{k}") == "1" for k in TRADE_KINDS)
