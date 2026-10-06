"""거래 기록 받기(tradecollect.py) · 카드 시세 캐시 테스트 — 가짜 넥슨 거래 목록으로, 네트워크 없이.

pytest 없이 `python tests/test_trades.py`. 실제 넥슨에 붙는 스모크는 `python check_api.py <닉네임>` 의 거래 줄.
카드 번호·금액·saleSn 은 전부 지어낸 값이다.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import playerinfo
import store
import tradecollect as tc
from nexon_api import QUOTA_CODE, NexonAPIError

DAY = date(2026, 10, 7)
T0 = datetime(2026, 9, 30, 17, 0, 0)


def _rows(kind: str, n: int, start: datetime = T0, prefix: str = "") -> list[dict]:
    """최신순 n 줄 — 한 시간 간격으로 옛날로."""
    return [{"tradeDate": (start - timedelta(hours=i)).isoformat(timespec="seconds"),
             "saleSn": f"{prefix}{kind}{i:06d}", "spid": 100000000 + i % 50, "grade": 1 + i % 8,
             "value": 1000 * (i + 1)} for i in range(n)]


class FakeAPI:
    """kind → 최신순 목록. 넥슨처럼 offset/limit 로 자르고, 끝을 지나면 빈 목록."""

    def __init__(self, lists: dict[str, list[dict]]):
        self.lists = lists
        self.calls: list[tuple[str, int]] = []
        self.fail_at: int | None = None    # 이 번째 요청(1부터)에서 실패
        self.fail_exc: Exception | None = None
        self.on_call = None                # 요청마다 부르는 함수(목록에 새 거래를 끼우는 등)

    def get_trades(self, kind, offset=0, limit=100):
        self.calls.append((kind, offset))
        if self.on_call:
            self.on_call(self, len(self.calls))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise self.fail_exc or NexonAPIError("한도", code=QUOTA_CODE, status=429)
        return [dict(r) for r in self.lists[kind][offset:offset + limit]]

    def push(self, kind: str, rows: list[dict]) -> None:
        """새 거래(또는 늦게 반영된 거래)를 넣고 날짜 최신순으로 다시 정렬."""
        self.lists[kind] = sorted(self.lists[kind] + rows, key=lambda r: r["tradeDate"], reverse=True)


def _db():
    return store.open_db(":memory:")


def _stored(conn, kind):
    return {r[0] for r in conn.execute("SELECT sale_sn FROM trades WHERE kind = ?", (kind,))}


def _all(api, kind):
    return {r["saleSn"] for r in api.lists[kind]}


class CancelAfter:
    """n 번째 요청을 보낸 뒤 멈춤 — 쪽 사이에서 본다."""

    def __init__(self, api, n):
        self.api, self.n = api, n

    def __call__(self):
        return len(self.api.calls) >= self.n


def test_trade_first_fetch_complete():
    api = FakeAPI({"buy": _rows("buy", 250), "sell": _rows("sell", 430)})
    c = _db()
    r = tc.collect(api, c, "key-A", today=DAY)
    assert r.complete and not r.wiped and not r.quota, r
    assert _stored(c, "buy") == _all(api, "buy") and _stored(c, "sell") == _all(api, "sell")
    st = store.trade_state(c)
    assert tc.is_complete(st) and st["fetched_at"] == DAY.isoformat(), st
    assert not any(k.startswith("top_stop_") for k in st), st
    # 첫 수집은 위쪽이 빈 쪽까지 간다 — 아래쪽을 따로 다시 읽지 않는다(구매 3쪽+빈 쪽 · 판매 5쪽+빈 쪽)
    assert r.requests == 4 + 6, (r.requests, api.calls)


def test_trade_same_day_no_requests():
    api = FakeAPI({"buy": _rows("buy", 120), "sell": _rows("sell", 30)})
    c = _db()
    tc.collect(api, c, "key-A", today=DAY)
    n = len(api.calls)
    r = tc.collect(api, c, "key-A", today=DAY)
    assert r.requests == 0 and len(api.calls) == n and r.complete, r


def test_trade_interrupted_first_fetch_resumes_old():
    """1회차 [상] — 첫 수집이 끊기면 옛 거래가 영영 빠졌다."""
    api = FakeAPI({"buy": _rows("buy", 1000), "sell": _rows("sell", 50)})
    c = _db()
    r = tc.collect(api, c, "key-A", cancel=CancelAfter(api, 3), today=DAY)
    assert r.cancelled and len(_stored(c, "buy")) == 300, (r, len(_stored(c, "buy")))
    assert not tc.is_complete(store.trade_state(c))
    # 다음 날 — 그사이 새 거래 20건
    api.push("buy", _rows("buy", 20, start=T0 + timedelta(days=1), prefix="n"))
    api.calls.clear()
    r = tc.collect(api, c, "key-A", today=DAY + timedelta(days=1))
    assert r.complete, r
    assert _stored(c, "buy") == _all(api, "buy"), len(_all(api, "buy") - _stored(c, "buy"))
    assert tc.is_complete(store.trade_state(c))
    # 이어 받기 — 처음부터 다시 읽지 않는다(1020건이면 11쪽+빈 쪽; 위쪽 겹침 몇 쪽 + 아래쪽 나머지)
    buy_calls = [o for k, o in api.calls if k == "buy"]
    assert len(buy_calls) < 14, buy_calls


def test_trade_interrupted_top_fetch_no_gap():
    """2회차 [상] — 위쪽 받기가 끊긴 뒤 "저장된 saleSn 을 만나면 멈춤"이면 가운데 구멍이 영구히 남았다."""
    api = FakeAPI({"buy": _rows("buy", 300), "sell": _rows("sell", 10)})
    c = _db()
    tc.collect(api, c, "key-A", today=DAY)
    # 다음 날: 새 거래 350건(쪽 셋 반) — 첫 쪽만 받고 끊긴다
    api.push("buy", _rows("buy", 350, start=T0 + timedelta(days=20), prefix="b"))
    api.calls.clear()
    r = tc.collect(api, c, "key-A", cancel=CancelAfter(api, 1), today=DAY + timedelta(days=1))
    assert r.cancelled and len(_stored(c, "buy")) == 400, len(_stored(c, "buy"))
    assert store.trade_state(c).get("top_stop_buy"), "끊긴 위쪽의 경계가 남아 있어야 이어 받는다"
    # 그사이 또 새 거래 — 다음 실행의 첫 쪽이 이 새 묶음 + 직전 실행이 넣은 줄로 채워진다
    api.push("buy", _rows("buy", 60, start=T0 + timedelta(days=30), prefix="c"))
    r = tc.collect(api, c, "key-A", today=DAY + timedelta(days=1))
    assert r.complete, r
    missing = _all(api, "buy") - _stored(c, "buy")
    assert not missing, f"가운데 구멍 {len(missing)}건"


def test_trade_overlap_catches_late():
    """R6 — 반영이 늦게 와서 저장된 최신보다 옛 날짜로 끼는 거래도 겹쳐 받는다(TRADE_OVERLAP_DAYS 안)."""
    api = FakeAPI({"buy": _rows("buy", 500), "sell": _rows("sell", 10)})
    c = _db()
    tc.collect(api, c, "key-A", today=DAY)
    late_in = [{"tradeDate": (T0 - timedelta(days=config.TRADE_OVERLAP_DAYS - 2)).isoformat(timespec="seconds"),
                "saleSn": "late-in", "spid": 1, "grade": 1, "value": 5}]
    api.push("buy", late_in)
    api.calls.clear()
    r = tc.collect(api, c, "key-A", today=DAY + timedelta(days=1))
    assert "late-in" in _stored(c, "buy"), r
    # 경계에서 멈춘다 — 500건 전부를 다시 읽지 않는다(한 시간 간격이라 7일 = 168줄 → 두 쪽)
    buy_calls = [o for k, o in api.calls if k == "buy"]
    assert buy_calls == [0, 100], buy_calls


def test_trade_429_keeps_state():
    api = FakeAPI({"buy": _rows("buy", 400), "sell": _rows("sell", 10)})
    api.fail_at = 2
    c = _db()
    r = tc.collect(api, c, "key-A", today=DAY)
    assert r.quota and not r.complete and r.error is None, r
    st = store.trade_state(c)
    assert "fetched_at" not in st and not tc.is_complete(st), st
    api.fail_at = None
    r = tc.collect(api, c, "key-A", today=DAY)
    assert r.complete and _stored(c, "buy") == _all(api, "buy"), r


def test_trade_network_error_keeps_state():
    api = FakeAPI({"buy": _rows("buy", 150), "sell": _rows("sell", 10)})
    api.fail_at, api.fail_exc = 1, NexonAPIError("네트워크 오류: x")
    c = _db()
    r = tc.collect(api, c, "key-A", today=DAY)
    assert r.error and not r.quota and not r.complete, r
    assert store.trade_state(c).get("fetched_at") is None


def test_trade_key_change_wipes_all():
    a = FakeAPI({"buy": _rows("buy", 150, prefix="A"), "sell": _rows("sell", 20, prefix="A")})
    c = _db()
    tc.collect(a, c, "key-A", today=DAY)
    store.set_trade_state(c, my_ouid="me-ouid")
    b = FakeAPI({"buy": _rows("buy", 30, prefix="B"), "sell": []})
    seen = {}

    def on_wiped():
        seen["calls_at_wipe"] = len(b.calls)
        seen["rows_at_wipe"] = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        seen["state_at_wipe"] = store.trade_state(c)

    r = tc.collect(b, c, "key-B", today=DAY, on_wiped=on_wiped)
    assert r.wiped and r.complete, r
    # 지우기는 받기보다 먼저 · 거래·done·top_stop·fetched_at 전부
    assert seen["calls_at_wipe"] == 0 and seen["rows_at_wipe"] == 0, seen
    ws = seen["state_at_wipe"]
    assert not any(k.startswith(("done_", "top_stop_")) or k == "fetched_at" for k in ws), ws
    assert ws["key_fp"] == store.key_fingerprint("key-B") and ws["my_ouid_unconfirmed"] == "me-ouid", ws
    assert "my_ouid" not in ws, ws
    # 새 주인 것만 남는다
    assert _stored(c, "buy") == _all(b, "buy") and not _stored(c, "sell")
    # 확인 전에 키가 또 바뀌어도 확인 전 계정은 그대로
    tc.collect(FakeAPI({"buy": [], "sell": []}), c, "key-C", today=DAY)
    st = store.trade_state(c)
    assert st.get("my_ouid_unconfirmed") == "me-ouid" and "my_ouid" not in st, st


def _prev(conn, fp):
    return {r[0] for r in conn.execute("SELECT sale_sn FROM trades_prev WHERE key_fp = ?", (fp,))}


def test_trade_key_change_keeps_old_owner_rows():
    """키가 바뀌어도 옛 주인 거래는 지우지 않는다 — 그 키로 돌아와 다 받으면, 넥슨이 그사이 버린 옛 거래가 되살아난다."""
    a = FakeAPI({"buy": _rows("buy", 150, prefix="A"), "sell": _rows("sell", 20, prefix="A")})
    c = _db()
    tc.collect(a, c, "key-A", today=DAY)
    fa = store.key_fingerprint("key-A")
    a_buys = _all(a, "buy")
    tc.collect(FakeAPI({"buy": _rows("buy", 30, prefix="B"), "sell": []}), c, "key-B", today=DAY)
    assert _prev(c, fa) == a_buys | _all(a, "sell"), "옛 주인 거래를 옮겨 두지 않았다"
    assert not (_stored(c, "buy") & a_buys)  # 화면 표에는 새 주인 것만
    # 넥슨이 A 의 옛 거래 50줄을 버렸다 — 다시 받아도 안 온다
    a.lists["buy"] = a.lists["buy"][:100]
    # A 로 돌아와 받다 끊기면 아직 되돌리지 않는다(이어 받기의 쪽 계산을 흐리지 않게)
    a.calls.clear()
    r = tc.collect(a, c, "key-A", today=DAY + timedelta(days=1), cancel=CancelAfter(a, 1))
    assert r.cancelled and _prev(c, fa), r
    assert not (_stored(c, "buy") - _all(a, "buy")), "다 받기 전에 되돌렸다"
    r = tc.collect(a, c, "key-A", today=DAY + timedelta(days=1))
    assert r.complete, r
    assert _stored(c, "buy") == a_buys, "넥슨이 버린 옛 거래가 되살아나지 않았다"
    assert not _prev(c, fa), "되돌린 줄이 trades_prev 에 남았다"
    assert _prev(c, store.key_fingerprint("key-B")), "B 의 거래를 옮겨 두지 않았다"


def test_trade_first_key_is_not_a_wipe():
    """처음 지문을 적는 건 '키가 바뀜'이 아니다 — 화면에 지웠음 신호를 보내지 않는다."""
    c = _db()
    called = []
    r = tc.collect(FakeAPI({"buy": [], "sell": []}), c, "key-A", today=DAY, on_wiped=lambda: called.append(1))
    assert not r.wiped and not called, r
    assert store.trade_state(c)["key_fp"] == store.key_fingerprint("key-A")
    assert store.key_fingerprint("key-A") != store.key_fingerprint("key-B")
    assert "key-A" not in str(store.trade_state(c)), "키가 DB 에 남았다"


def test_trade_duplicates_and_bad_rows():
    rows = _rows("sell", 5)
    rows.append(dict(rows[2]))                      # R5 — 완전히 같은 줄
    rows.append({"tradeDate": None, "saleSn": "x"})  # 날짜 없음 — 그 줄만 버린다
    rows.append({"tradeDate": "2026-09-01T00:00:00", "saleSn": "ok", "spid": "bad", "grade": None, "value": "7"})
    c = _db()
    assert store.save_trades(c, "sell", rows) == 6
    assert store.save_trades(c, "sell", rows) == 0
    row = c.execute("SELECT spid, grade, value FROM trades WHERE sale_sn = 'ok'").fetchone()
    assert tuple(row) == (None, None, 7), tuple(row)


def test_load_trades_feeds_timeline():
    """저장된 거래 → 타임라인 입력(시간순 · 같은 초면 구매 먼저) — 가계부의 짝이 여기서 시작한다."""
    import squad_timeline
    c = _db()
    store.save_trades(c, "sell", [{"tradeDate": "2026-09-01T10:00:00", "saleSn": "s1", "spid": 7, "grade": 3,
                                   "value": 500}])
    store.save_trades(c, "buy", [{"tradeDate": "2026-09-01T10:00:00", "saleSn": "b1", "spid": 7, "grade": 3,
                                  "value": 200}, {"tradeDate": "2026-08-01T10:00:00", "saleSn": "b0", "spid": 8,
                                                  "grade": 1, "value": 10}])
    trades = squad_timeline.parse_trades(store.load_trades(c))
    assert [t.sale_sn for t in trades] == ["b0", "b1", "s1"], trades
    pairs, orphans, left = squad_timeline.fifo_pairs(trades)
    assert [p.profit for p in pairs] == [300] and not orphans and list(left) == [8]


def test_bought_cards_for_hint():
    c = _db()
    store.save_trades(c, "buy", [{"tradeDate": "2026-09-01T00:00:00", "saleSn": "1", "spid": 7, "grade": 5, "value": 1}])
    store.save_trades(c, "sell", [{"tradeDate": "2026-09-02T00:00:00", "saleSn": "2", "spid": 8, "grade": 1, "value": 1}])
    assert store.bought_cards(c) == {(7, 5)}


# ── 카드 시세 캐시 ───────────────────────────────────────────────────────────

def test_parse_bp():
    assert playerinfo.parse_bp("3,660,000,000,000 BP") == 3660000000000
    assert playerinfo.parse_bp("308,000 BP") == 308000
    for bad in ("-", "", None, "BP"):
        assert playerinfo.parse_bp(bad) is None, bad
    info = playerinfo.PlayerInfo(sp_id=1, prices={0: "-", 1: "308,000 BP", 2: "0 BP"})
    assert playerinfo.prices_as_int(info) == {1: 308000}


def _fake_fetch(calls, empty=()):
    def fetch(spid):
        calls.append(spid)
        if spid in empty:
            return playerinfo.PlayerInfo(sp_id=spid, prices={1: "-"})
        return playerinfo.PlayerInfo(sp_id=spid, prices={1: f"{spid},000 BP", 5: f"{spid * 3},000 BP"})
    return fetch


def test_price_cache_daily():
    c = _db()
    calls = []
    done, skipped = playerinfo.collect_prices(c, [11, 12, 11], "2026-10-07", cap=10, fetch=_fake_fetch(calls))
    assert (done, skipped) == (2, 0) and calls == [11, 12], (done, skipped, calls)
    assert store.load_card_prices(c, [11])[(11, 5)] == (33000, "2026-10-07")
    playerinfo.collect_prices(c, [11, 12], "2026-10-07", cap=10, fetch=_fake_fetch(calls))
    assert calls == [11, 12], "같은 날 다시 읽었다"
    playerinfo.collect_prices(c, [11], "2026-10-08", cap=10, fetch=_fake_fetch(calls))
    assert calls == [11, 12, 11], "다음 날 다시 안 읽었다"


def test_price_fetch_respects_cap():
    c = _db()
    calls = []
    done, skipped = playerinfo.collect_prices(c, range(1, 8), "2026-10-07", cap=3, fetch=_fake_fetch(calls))
    assert (done, skipped) == (3, 4) and len(calls) == 3, (done, skipped, calls)
    # 같은 날 다음 실행도 상한 안 — 이미 3장을 읽었다
    done, skipped = playerinfo.collect_prices(c, range(1, 8), "2026-10-07", cap=3, fetch=_fake_fetch(calls))
    assert done == 0 and skipped == 4 and len(calls) == 3, (done, skipped, calls)
    # 시세가 비어 오는 카드도 상한에 센다 — 저장 수만 세면 상한 밖에서 계속 요청한다
    c2, calls2 = _db(), []
    playerinfo.collect_prices(c2, range(1, 10), "2026-10-07", cap=3, fetch=_fake_fetch(calls2, empty=set(range(1, 10))))
    assert len(calls2) == 3, calls2


def test_price_cancel_between_cards():
    c = _db()
    calls = []
    playerinfo.collect_prices(c, range(1, 6), "2026-10-07", cap=10, fetch=_fake_fetch(calls),
                              cancel=lambda: len(calls) >= 2)
    assert calls == [1, 2], calls


def test_price_failure_skips_one():
    c = _db()

    def fetch(spid):
        if spid == 2:
            raise playerinfo.PlayerInfoError("x")
        return playerinfo.PlayerInfo(sp_id=spid, prices={1: "1,000 BP"})
    done, _ = playerinfo.collect_prices(c, [1, 2, 3], "2026-10-07", cap=10, fetch=fetch)
    assert done == 2


def test_price_auto_needs_notice_4_and_web_data():
    """자동 시세 읽기는 새 동작 — 4 안내 동의 + 웹 데이터 켜짐 둘 다(1.3.1 track_allowed 와 같은 꼴)."""
    keep = (config.NOTICE_ACCEPTED, config.WEB_DATA)
    try:
        for accepted, web, want in ((3, True, False), (4, False, False), (4, True, True), (5, True, True)):
            config.NOTICE_ACCEPTED, config.WEB_DATA = accepted, web
            assert config.price_auto_allowed() is want, (accepted, web)
        # 옛 동의(3)는 막지 않고 다시 묻기만 — 거래(본인 데이터, 오픈API)엔 게이트가 없다
        config.NOTICE_ACCEPTED = 3
        assert config.notice_update_pending() and not config.notice_needed()
    finally:
        config.NOTICE_ACCEPTED, config.WEB_DATA = keep


def _match(ouid, players):
    return {"matchInfo": [{"ouid": ouid, "player": [{"spId": s, "spGrade": g} for s, g in players]},
                          {"ouid": "opp", "player": [{"spId": 999, "spGrade": 9}]}]}


def test_trade_hint_card_and_grade():
    import core_api as core
    details = [_match("me", [(1, 5), (2, 3)]), _match("me", [(1, 5), (3, 1)]), _match("x", [(7, 7)])]
    # (카드, 강화) 단위 — 같은 카드 다른 강화는 안 맞는다(R3) · 상대 선수는 안 센다
    assert core.trade_hint(details, "me", {(1, 5), (2, 9), (999, 9)}) == (1, 3)
    assert core.trade_hint(details, "me", {(1, 5)}, games=1) == (1, 2)   # 최근 games 경기만
    assert core.trade_hint([], "me", {(1, 5)}) == (0, 0)


# ── 랭커 기록 캐시(13단계 · rankerstats.collect) ─────────────────────────────
class FakeRankerAPI:
    """넥슨처럼 데이터 있는 쌍만 돌려준다 — have: (spid, po) → 경기당 status."""

    def __init__(self, have: dict):
        self.have = have
        self.calls: list[list] = []
        self.fail: Exception | None = None

    def get_ranker_stats(self, matchtype, pairs):
        self.calls.append(list(pairs))
        if self.fail is not None:
            raise self.fail
        return [{"spid": s, "spPosition": p, "status": dict(self.have[(s, p)]), "createDate": "2026-10-05T17:30:00"}
                for s, p in pairs if (s, p) in self.have]


def test_ranker_cache_daily():
    import rankerstats
    conn = _db()
    st = {"shoot": 0.75, "matchCount": 20}
    api = FakeRankerAPI({(1, 19): st, (2, 10): st})
    pairs = [(1, 19), (2, 10), (3, 5)]   # (3, 5)는 넥슨에 없다 — 응답에서 빠진다
    got = rankerstats.collect(api, conn, pairs, 52, "2026-10-07")
    assert len(api.calls) == 1, api.calls
    assert got[(1, 19)]["status"]["shoot"] == 0.75 and got[(3, 5)] is None, got
    # 같은 날 다시 — 빠진 쌍까지 기억해 요청 0(안 그러면 화면을 열 때마다 같은 쌍을 다시 묻는다)
    again = rankerstats.collect(api, conn, pairs, 52, "2026-10-07")
    assert len(api.calls) == 1, f"같은 날 다시 물었다: {api.calls}"
    assert again == got
    # 다음 날엔 다시 · 다른 matchtype 은 따로
    rankerstats.collect(api, conn, pairs, 52, "2026-10-08")
    rankerstats.collect(api, conn, pairs, 50, "2026-10-08")
    assert len(api.calls) == 3, api.calls


def test_ranker_batches_and_failure_keeps_done_batches():
    import rankerstats
    from nexon_api import RANKER_STATS_BATCH
    conn = _db()
    pairs = [(i, 1) for i in range(RANKER_STATS_BATCH + 5)]
    api = FakeRankerAPI({p: {"matchCount": 10} for p in pairs})
    rankerstats.collect(api, conn, pairs[:RANKER_STATS_BATCH], 52, "2026-10-07")
    api.fail = NexonAPIError("한도", code=QUOTA_CODE, status=429)
    try:
        rankerstats.collect(api, conn, pairs, 52, "2026-10-07")
        raise AssertionError("429 를 삼켰다")
    except NexonAPIError:
        pass
    # 앞 묶음은 저장돼 있어 다시 안 묻고, 남은 5쌍만 물었다
    assert [len(c) for c in api.calls] == [RANKER_STATS_BATCH, 5], [len(c) for c in api.calls]
    assert len(store.load_ranker_stats(conn, pairs, 52, "2026-10-07")) == RANKER_STATS_BATCH


def test_ranker_api_splits_into_batches():
    """URL 길이 상한(실측 81쌍) — nexon_api 가 RANKER_STATS_BATCH 개씩 나눠 묻는다."""
    import json as _json
    import nexon_api
    api = nexon_api.FCOnlineAPI("k")
    sent = []
    api._get = lambda path, **kw: sent.append(_json.loads(kw["players"])) or []
    api.get_ranker_stats(52, [(100000000 + i, i % 28) for i in range(120)])
    assert nexon_api.RANKER_STATS_BATCH <= 81
    assert [len(b) for b in sent] == [50, 50, 20] if nexon_api.RANKER_STATS_BATCH == 50 else sent
    assert all(len(b) <= nexon_api.RANKER_STATS_BATCH for b in sent)
    assert sent[0][0] == {"id": 100000000, "po": 0}


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
