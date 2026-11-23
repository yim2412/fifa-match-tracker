"""스펙업 순서 받기(121 조각 ②) — 관문 · 캐시 나이 · 계수 · 간격 · 막힘 · 점검 · 시도 기록 · 다른 길과의 막힘(가짜 넥슨, 네트워크 없음).
창 쪽(예약 겹침 · 게임 · 숨김 · 다시 읽기)은 test_ui_smoke 의 `_specup_` 테스트. `python tests/test_specupcollect.py`"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import cardcollect  # noqa: E402
import config  # noqa: E402
import playerinfo  # noqa: E402
import specup  # noqa: E402
import specupcollect as sc  # noqa: E402
import store  # noqa: E402
import teamcolor  # noqa: E402

TODAY = date(2026, 11, 24)
DAY = TODAY.isoformat()
CELLS = sorted(set(config.SIM_POS_OF.values()))
KIND = store.BUDGET_SPECUP


@contextlib.contextmanager
def cfg(**kw):
    keep = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in keep.items():
            setattr(config, k, v)


class Net:
    """가짜 넥슨 — fail: {("page"|"sim"|"list", 카드): 갈래} · bad: 형식이 빈 응답."""

    def __init__(self, path):
        self.path = path
        self.calls: list[tuple[str, int]] = []
        self.waits: list[float] = []
        self.fail: dict[tuple[str, int], str] = {}
        self.bad: set[tuple[str, int]] = set()
        self.no_price: set[int] = set()
        self.hook = lambda what, spid: None

    def _ask(self, what, spid):
        self.calls.append((what, spid))
        self.hook(what, spid)
        k = self.fail.get((what, spid)) or self.fail.get((what, -1))
        if k == "bug":
            raise ValueError("파싱 버그")
        if k:
            raise (teamcolor.TeamColorError if what == "list" else playerinfo.PlayerInfoError)("x", k)

    def page(self, spid):
        self._ask("page", spid)
        if ("page", spid) in self.bad:
            return playerinfo.PlayerInfo(sp_id=spid)
        prices = {g: "-" for g in range(1, 14)} if spid in self.no_price else {g: f"{g},000,000 BP" for g in range(1, 14)}
        return playerinfo.PlayerInfo(sp_id=spid, name="선수", ovr=100, salary=28, height="181cm", prices=prices)

    def sim(self, spid):
        self._ask("sim", spid)
        return playerinfo.AbilitySim(ovr=100, groups={}, abilities={}, club_options=[(1005, "팀")],
                                     position_ovrs={} if ("sim", spid) in self.bad else {c: 100 for c in CELLS})

    def lst(self):
        self._ask("list", 0)
        return [teamcolor.TeamColorMeta(1005, "팀", "crests/l1.png", "", 1, 3, [])]

    def run(self, spids, today=TODAY, **kw):
        self.calls.clear()
        self.waits.clear()
        return sc.run(self.path, spids, today, fetch_page=self.page, fetch_sim=self.sim, fetch_list=self.lst,
                      wait=self.waits.append, **kw)

    def backfill(self, spids, today=TODAY):
        """카드 자동 채우기(측정 모드 — 고정 집합) — 다른 웹 길의 막힘을 보는지."""
        self.calls.clear()
        return cardcollect.run(self.path, today, 600, window=spids, fetch_page=self.page, fetch_sim=self.sim,
                               fetch_list=self.lst, fetch_detail=lambda t: ("", []), sleep=lambda s: None, gap_s=0)


@contextlib.contextmanager
def env(meta=True, web=True):
    with tempfile.TemporaryDirectory() as d, cfg(WEB_DATA=web, NOTICE_ACCEPTED=config.NOTICE_VERSION):
        path = Path(d) / "f.db"
        conn = store.open_db(path)
        if meta:
            store.save_teamcolor_meta(conn, [teamcolor.TeamColorMeta(1005, "팀", "crests/l1.png", "", 1, 3, [])], DAY)
        try:
            yield Net(path), conn
        finally:
            conn.close()


def used(conn, day=DAY, kind=KIND):
    return store.budget_used(conn, day, kind)


def blocked(conn, kind=KIND, day=DAY):
    return store.budget_hit_429(conn, day, (f"{kind}_block",))


# ── 대상 · 캐시 나이 ─────────────────────────────────────────────────────────

def test_fetch_order_lineup_first_then_popular():
    inp = specup.Inputs(lineup=(specup.Slot(0, 50, 11), specup.Slot(3, 7, 11)),
                        cands=(specup.CandCard(1, "DF", 5), specup.CandCard(9, "DF", 8), specup.CandCard(7, "MF", 30), specup.CandCard(1, "MF", 6)))
    assert inp.fetch_order == (50, 7, 9, 1), inp.fetch_order
    assert set(inp.fetch_order) == set(inp.want)
    ok = {r for r in ("", specup.NO_ME, specup.NO_SNAPSHOT, specup.NO_COLOR, specup.TC_LIST, specup.TC_SPLIT, specup.TC_ENHANCE,
                      specup.NO_LINEUP, specup.FEW_RANKERS) if specup.Inputs(reason=r).fetchable}
    assert ok == {"", specup.TC_LIST, specup.TC_SPLIT}, ok


def test_page_then_sim_per_card_and_saved_once_a_day():
    with env() as (net, conn):
        seen = []
        r = net.run([3, 1, 2], on_progress=lambda n, m: seen.append((n, m)))
        assert net.calls == [("page", 3), ("sim", 3), ("page", 1), ("sim", 1), ("page", 2), ("sim", 2)], net.calls
        assert (r.requests, r.saved, r.total, r.cards, r.stop) == (6, 6, 3, 3, ""), r
        assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)], seen
        assert used(conn) == 6
        assert store.load_card_prices(conn, [1])[1, 8] == (8_000_000, DAY)
        assert store.load_card_info(conn, [1])[1]["salary"] == 28
        ex = store.load_card_extras(conn, [1])[1]
        assert ex["sim_on"] == DAY and specup.keeps_color(ex, 1005)
        r = net.run([3, 1, 2])
        assert net.calls == [] and (r.requests, r.saved, r.total) == (0, 0, 0), "같은 날 다시 읽었다"
        # 다음 날 — 시세(하루)만 · 30일째 — 능력치도
        assert net.run([1], TODAY + timedelta(days=1)).saved == 1 and net.calls == [("page", 1)]
        net.run([2], TODAY + timedelta(days=config.CARD_INFO_TTL_DAYS - 1))
        assert net.calls == [("list", 0), ("page", 2)], net.calls
        net.run([3], TODAY + timedelta(days=config.CARD_INFO_TTL_DAYS))
        assert net.calls == [("page", 3), ("sim", 3)], net.calls


def test_fresh_from_other_paths_is_not_asked_again():
    """카드 자동 채우기·칩·가계부가 오늘 받은 카드는 요청 0 · 급여만 없으면(가계부 시세만) 페이지를 읽는다."""
    with env() as (net, conn):
        day = DAY
        store.save_card_prices(conn, 1, {8: 5}, day)
        store.save_card_info(conn, 1, "a", "ST", 100, 28, day)
        playerinfo.save_card_extra_sim(conn, 1, net.sim(1), day)
        store.save_card_prices(conn, 2, {8: 5}, day)                       # 카드 정보 없음
        playerinfo.save_card_extra_sim(conn, 2, net.sim(2), day)
        store.save_card_prices(conn, 3, {8: 5}, "2026-11-23")              # 어제 시세
        store.save_card_info(conn, 3, "a", "ST", 100, 28, day)
        playerinfo.save_card_extra_sim(conn, 3, net.sim(3), day)
        net.run([1, 2, 3])
        assert net.calls == [("page", 2), ("page", 3)], net.calls


def test_same_try_keys_as_other_paths():
    """페이지 "p:" 는 칩·가계부와, 시뮬레이터 "c:…:sim" 은 카드 자동 채우기와 같은 기록 — 한쪽이 오늘 시도했으면 다른 쪽도 안 묻는다."""
    with env() as (net, conn):
        store.try_record(conn, "p:1", DAY, ok=False)
        store.try_record(conn, "c:2:sim", DAY, ok=False)
        store.try_record(conn, "c:3:page", DAY, ok=False)                  # 카드 자동 채우기의 페이지 기록은 따로
        net.run([1, 2, 3])
        assert net.calls == [("sim", 1), ("page", 2), ("page", 3), ("sim", 3)], net.calls
        store.try_record(conn, "p:9", DAY, ok=True)
        store.try_record(conn, "c:9:sim", DAY, ok=True)
        r = net.run([9])
        assert net.calls == [] and r.total == 0, "오늘 시도한 카드를 받을 카드로 셌다(n/m장이 안 준다)"
        net.bad = {("page", 5), ("sim", 5)}
        net.run([5])
        assert store.try_failed(conn, "p:5") and store.try_failed(conn, "c:5:sim") and not store.try_failed(conn, "p:3")
        assert store.load_card_extras(conn, [5]) == {} and store.load_card_info(conn, [5]) == {}, "형식이 깨진 응답을 저장했다"


def test_team_color_list_weekly_and_alone():
    with env(meta=False) as (net, conn):
        r = net.run([])                                                    # 재료가 안 섰을 때(TC_LIST) — 목록만
        assert net.calls == [("list", 0)] and (r.saved, r.total) == (1, 0) and used(conn) == 1, r
        assert store.load_teamcolor_meta(conn, TODAY)[1]
        net.run([])
        assert net.calls == []
        net.run([], TODAY + timedelta(days=config.TEAMCOLOR_CACHE_DAYS - 1))
        assert net.calls == [], "7일 안인데 다시 받았다"
        net.fail[("list", 0)] = "format"
        net.run([1], TODAY + timedelta(days=config.TEAMCOLOR_CACHE_DAYS))
        assert net.calls[0] == ("list", 0) and len(net.calls) == 3, "목록이 실패해도 카드는 받는다"
        assert store.try_failed(conn, "t:list") and store.load_teamcolor_meta(conn, TODAY)[0], "옛 목록은 남는다"
        net.run([1], TODAY + timedelta(days=config.TEAMCOLOR_CACHE_DAYS))
        assert net.calls == [], "그날 다시 묻지 않는다"
        net.fail.clear()
        net.run([], TODAY + timedelta(days=9), want_list=False)
        assert net.calls == []
        net.run([], TODAY + timedelta(days=9))
        assert net.calls == [("list", 0)] and not store.try_failed(conn, "t:list"), "받았는데 실패가 남았다"


# ── 계수 · 간격 · 멈춤 ───────────────────────────────────────────────────────

def test_daily_cap_counts_list_page_sim_together():
    with env(meta=False) as (net, conn), cfg(SPECUP_FETCH_MAX=4):
        r = net.run([1, 2, 3])
        assert net.calls == [("list", 0), ("page", 1), ("sim", 1), ("page", 2)], net.calls
        assert (r.requests, r.stop, r.cards, r.total) == (4, "cap", 1, 3) and used(conn) == 4, r
        r = net.run([1, 2, 3])
        assert net.calls == [] and r.stop == "cap" and used(conn) == 4, "그날 상한을 넘었다"
        assert not blocked(conn), "상한은 막힘이 아니다"
        net.run([2], TODAY + timedelta(days=1))
        assert net.calls == [("page", 2), ("sim", 2)], "다음 날 이어서"
    with env() as (net, conn):   # 칩 길 — 상한에 걸려 못 읽은 수
        assert playerinfo.collect_cards(conn, [1, 2, 3], DAY, playerinfo.KIND_CHIP, 1, fetch=net.page) == (1, 2)
    with env() as (net, conn):   # 다른 쓰임의 계수는 안 본다
        for _ in range(config.SPECUP_FETCH_MAX):
            store.budget_take(conn, DAY, playerinfo.KIND_CHIP, None)
        net.run([1])
        assert len(net.calls) == 2


def test_failures_and_empty_answers_are_counted():
    with env() as (net, conn):
        net.bad, net.no_price, net.fail = {("page", 1)}, {2}, {("sim", 3): "bug", ("page", 4): "down"}
        r = net.run([1, 2, 3, 4])
        assert r.requests == len(net.calls) == used(conn) == 8, (r, net.calls)
        assert store.try_failed(conn, "c:3:sim"), "파싱 버그는 그 카드의 형식 실패"
        assert r.saved == 5, r   # sim1 · page2(시세가 비어도 카드 정보를 적었다)·sim2 · page3 · sim4


def test_gap_between_requests_only_for_specup():
    with env(meta=False) as (net, conn), cfg(CARD_BACKFILL_GAP_S=7):
        net.run([1, 2])
        assert net.waits == [7] * 4, net.waits      # 다섯 요청 사이 넷
    with env() as (net, conn):                      # 칩·가계부는 지금처럼 쉬지 않는다(M5)
        keep = playerinfo.time.sleep

        def boom(s):
            raise AssertionError("칩 길이 쉬었다")
        playerinfo.time.sleep = boom
        try:
            with cfg(CARD_BACKFILL_GAP_S=7):
                assert playerinfo.collect_cards(conn, [1, 2], DAY, playerinfo.KIND_CHIP, 60, fetch=net.page) == (2, 0)
                assert playerinfo.collect_prices(conn, [3, 4], DAY, 80, fetch=net.page) == (2, 0)
        finally:
            playerinfo.time.sleep = keep


def test_cancel_and_switch_between_requests():
    with env() as (net, conn):
        stop = []
        net.hook = lambda what, spid: stop.append(1)
        r = net.run([1, 2], cancel=lambda: bool(stop))
        assert net.calls == [("page", 1)] and r.stop == "cancel" and used(conn) == 1, (net.calls, r)
        assert store.load_card_prices(conn, [1]), "끊겨도 받은 것은 저장한다"
        net.hook = lambda what, spid: setattr(config, "WEB_DATA", False)
        r = net.run([2, 3])
        assert net.calls == [("page", 2)] and r.stop == "cancel" and used(conn) == 2, net.calls
        r = net.run([4])
        assert net.calls == [] and (r.stop, used(conn)) == ("cancel", 2), "꺼졌으면 요청도 계수도 0"


def test_db_locked_ends_quietly():
    import sqlite3
    with env() as (net, conn):
        keep = store.save_card_info

        def locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        store.save_card_info = locked
        try:
            r = net.run([1, 2])
        finally:
            store.save_card_info = keep
        assert r.error == "OperationalError" and net.calls == [("page", 1)], r


# ── 막힘 · 점검 ──────────────────────────────────────────────────────────────

def test_block_stops_the_run_and_the_day():
    for what, key in (("page", 1), ("sim", 1), ("list", 0)):
        with env(meta=what != "list") as (net, conn):
            net.fail[what, key] = "rate"
            r = net.run([1, 2])
            assert net.calls[-1] == (what, key) and r.stop == "blocked" and blocked(conn), (what, net.calls, r)
            assert not store.try_skip(conn, f"p:{key}" if what == "page" else f"c:{key}:sim", DAY) or what == "list", "막힘을 시도로 적었다"
            net.fail.clear()
            r = net.run([1, 2])
            assert net.calls == [] and r.stop == "blocked", "막힌 날 다시 물었다"
            net.run([1], TODAY + timedelta(days=1))
            assert net.calls, "다음 날엔 다시 받는다"


def test_chip_and_ledger_also_stop_on_block():
    """가계부·칩 — 막힘 뒤 남은 카드를 계속 묻던 것(121 전). 막지 않았으면 물었을 카드가 남아 있다."""
    for kind, call in ((playerinfo.KIND_CHIP, lambda c, s, n: playerinfo.collect_cards(c, s, DAY, playerinfo.KIND_CHIP, 60, fetch=n.page)),
                       (playerinfo.KIND_LEDGER, lambda c, s, n: playerinfo.collect_prices(c, s, DAY, 80, fetch=n.page))):
        with env() as (net, conn):
            net.fail["page", 2] = "rate"
            assert call(conn, [1, 2, 3, 4], net) == (1, 0)
            assert net.calls == [("page", 1), ("page", 2)] and blocked(conn, kind), (kind, net.calls)
            net.calls.clear()
            net.fail.clear()
            assert call(conn, [3, 4], net) == (0, 0) and net.calls == [], f"{kind}: 막힌 날 두 번째 호출이 물었다"
            assert used(conn, kind=kind) == 2
            assert playerinfo.collect_cards(conn, [3], DAY, "다른쓰임", 60, fetch=net.page) == (1, 0), "자기 막힘만 본다"


def test_down_twice_on_different_cards_ends_without_block():
    for what in ("page", "sim"):
        with env() as (net, conn):
            net.fail[what, -1] = "down"
            r = net.run([1, 2, 3, 4])
            asked = [s for w, s in net.calls if w == what]
            assert asked == [1, 2] and r.stop == "down" and not blocked(conn), (what, net.calls, r)
            assert not store.try_skip(conn, "p:1", DAY) or what == "sim", "점검을 시도로 적었다"
            net.fail.clear()
            net.run([1, 2])
            assert (what, 1) in net.calls, "점검이 끝나면 같은 날 이어 받는다"


def test_one_always_down_card_does_not_stop_the_others():
    with env() as (net, conn):
        net.fail["page", 2] = net.fail["page", 4] = "down"
        r = net.run([1, 2, 3, 4, 5])
        assert [s for w, s in net.calls if w == "page"] == [1, 2, 3, 4, 5] and r.stop == "", (net.calls, r)
        got, skipped = playerinfo.collect_cards(conn, [12, 13], DAY, playerinfo.KIND_CHIP, 60,
                                                fetch=lambda s: net.page(2) if s == 12 else net.page(s))
        assert (got, skipped) == (1, 0), "칩: 점검 하나 뒤의 카드도 받는다"
    with env() as (net, conn):     # 한 카드의 두 길이 모두 점검 — 이어진 두 번이 아니다(길마다 센다)
        net.fail = {("page", 1): "down", ("sim", 1): "down"}
        assert net.run([1, 2]).stop == "" and ("sim", 2) in net.calls, net.calls
    with env() as (net, conn):     # 형식 실패도 "서버가 답했다" — 이어진 점검이 아니다
        net.fail = {("page", 1): "down", ("page", 2): "format", ("page", 3): "down"}
        assert net.run([1, 2, 3, 4]).stop == ""
        assert store.try_failed(conn, "p:2")


def test_other_paths_block_specup_and_back():
    kinds = store.card_block_kinds()
    assert {f"{k}_block" for k in (store.BUDGET_CARD, store.BUDGET_CARD_DEV, playerinfo.KIND_CHIP, playerinfo.KIND_LEDGER, KIND)} \
        | {store.BUDGET_TRAIT_WEB_BLOCK} == set(kinds), kinds
    with env() as (net, conn):
        net.run([1])
        assert len(net.calls) == 2, "막힘이 없으면 받는다(아래가 빈 검사)"
        net.backfill([90])
        assert net.calls, "막힘이 없으면 카드 자동 채우기도 받는다"
    for k in kinds:
        with env() as (net, conn):
            store.budget_mark_429(conn, DAY, k)
            r = net.run([1])
            assert net.calls == [] and r.stop == "blocked", k
            net.backfill([90])
            assert net.calls == [], f"{k} 인데 카드 자동 채우기가 돌았다"


def test_blocked_elsewhere_in_the_middle_stops_next_card():
    with env() as (net, conn):
        net.hook = lambda what, spid: what == "sim" and store.budget_mark_429(conn, DAY, "trait_web_block")
        r = net.run([1, 2])
        assert net.calls == [("page", 1), ("sim", 1)] and r.stop == "blocked", net.calls


# ── 관문 ────────────────────────────────────────────────────────────────────

def test_gate_needs_web_data_and_notice_17():
    assert config.SPECUP_NOTICE_VERSION == 17 == config.NOTICE_VERSION
    for web, acc, want in ((True, 17, True), (True, 16, False), (False, 17, False), (True, 0, False), (True, 18, True)):
        with cfg(WEB_DATA=web, NOTICE_ACCEPTED=acc):
            assert config.specup_allowed() is want, (web, acc)
    with cfg(WEB_DATA=True, NOTICE_ACCEPTED=17, NOTICE_BASE_VERSION=99):
        assert not config.specup_allowed(), "처음 동의 전"
    with cfg(WEB_DATA=True, NOTICE_ACCEPTED=17, CARD_BACKFILL_ON=""):
        assert config.specup_allowed(), "카드 자동 채우기 토글은 빌리지 않는다(U12)"


def test_no_request_when_web_data_off():
    with env(meta=False, web=False) as (net, conn):
        r = net.run([1, 2])
        assert net.calls == [] and used(conn) == 0 and r.requests == 0


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
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
