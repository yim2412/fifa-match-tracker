"""카드 정보 자동 채우기(51단계 조각 ①) — cardcollect · 예약 · [정보] 토글 · 시도 기록 · 막힘 · 형식 · 날짜별 시세.

넥슨 요청은 전부 가짜(Net). 시뮬레이터 칸 이름표(config.SIM_POS_OF)는 실측 응답 픽스처 셋(sim_*.html.gz — 2026-10-18)으로 잰다.
`python tests/test_cardcollect.py` 로 실행(offscreen · 네트워크 없음).
"""
from __future__ import annotations

import ast
import gzip
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import cardcollect as cc  # noqa: E402
import config  # noqa: E402
import playerinfo  # noqa: E402
import ranker  # noqa: E402
import store  # noqa: E402
import teamcolor  # noqa: E402
import tray  # noqa: E402

TODAY = date(2026, 10, 18)
ON_AT = "2026-10-01T09:00:00"
_FIX = Path(_ROOT, "tests", "fixtures")
_KEYS = ("ENV_PATH", "DB_PATH", "RANK_DB_PATH", "WEB_DATA", "RANK_COLLECT", "NOTICE_ACCEPTED", "API_KEY",
         "CARD_BACKFILL_ON", "CARD_BACKFILL_OFF", "CARD_BACKFILL_TICK_S", "CARD_BACKFILL_FIRST_S")
_ALL16 = sorted(set(config.SIM_POS_OF.values()))


# ── 환경 · 가짜 넥슨 ─────────────────────────────────────────────────────

class Env:
    def __enter__(self):
        self._d = tempfile.TemporaryDirectory()
        p = Path(self._d.name)
        self._saved = {k: getattr(config, k) for k in _KEYS}
        self._environ = dict(os.environ)
        self._loader = app_main.CardBackfillLoader
        config.ENV_PATH, config.DB_PATH, config.RANK_DB_PATH = p / ".env", p / "fifa.db", p / "rank.db"
        app_main.CardBackfillLoader = FakeLoader
        FakeLoader.made = []
        self.set()
        self.conn = store.open_db(config.DB_PATH)
        return self

    def set(self, web=True, collect=False, notice=config.CARD_BACKFILL_NOTICE_VERSION, on=ON_AT, off="",
            disk: dict | None = None):
        """이 프로세스 값과 .env 를 같이. disk 로 .env 쪽만 다르게(손 수정·다른 실행본)."""
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.API_KEY = web, collect, notice, "k"
        config.CARD_BACKFILL_ON, config.CARD_BACKFILL_OFF = on, off
        vals = {config.WEB_DATA_VAR: int(web), config.RANK_COLLECT_VAR: int(collect), config.NOTICE_VAR: notice,
                config.CARD_BACKFILL_VAR: on, config.CARD_BACKFILL_OFF_VAR: off, config.API_KEY_VAR: "k"}
        vals.update(disk or {})
        config.ENV_PATH.write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")

    def env_line(self, var):
        for ln in config.ENV_PATH.read_text(encoding="utf-8").splitlines():
            if ln.startswith(f"{var}="):
                return ln.split("=", 1)[1]
        return None

    def __exit__(self, *exc):
        self.conn.close()
        app_main.CardBackfillLoader = self._loader
        for k, v in self._saved.items():
            setattr(config, k, v)
        os.environ.clear()
        os.environ.update(self._environ)
        self._d.cleanup()


_mid = [0]


def add_match(conn, day: date, sides: dict[str, list[tuple[int, int, int]]]) -> None:
    """sides: {ouid: [(spid, 강화, 자리)]} — 선수 색인 줄을 직접(store._squad_rows 와 같은 모양)."""
    _mid[0] += 1
    m = conn.execute("INSERT INTO squad_match (match_id) VALUES (?)", (f"m{_mid[0]}",)).lastrowid
    for ouid, cards in sides.items():
        o = conn.execute("INSERT INTO squad_owner (ouid, nickname, last_day) VALUES (?, ?, ?) ON CONFLICT(ouid)"
                         " DO UPDATE SET last_day = excluded.last_day RETURNING id", (ouid, ouid, day.toordinal())).fetchone()[0]
        conn.executemany("INSERT OR IGNORE INTO match_squads (spid, day, match, owner, po, grade) VALUES (?, ?, ?, ?, ?, ?)",
                         [(s, day.toordinal(), m, o, po, g) for s, g, po in cards])
    conn.commit()


def account(conn, ouid="me"):
    store.upsert_account(conn, ouid, ouid)


def meta(conn, ids, day=TODAY, enhance=(), steps_fresh=True):
    """팀컬러 목록(신선) · steps_fresh 면 단계도 신선하게 — ② 가 끼지 않게."""
    items = [teamcolor.TeamColorMeta(i, f"팀{i}", f"crests/l{i}.png", "", 1, 3, []) for i in ids]
    items += [teamcolor.TeamColorMeta(i, f"강화{i}", "teamcolor/tc_u_g.png", "", 2, 8, []) for i in enhance]
    store.save_teamcolor_meta(conn, items, day.isoformat())
    if steps_fresh:
        for m in items:
            store.save_teamcolor_steps(conn, m.id, [teamcolor.Step(1, 3, ["e"])], day.isoformat())


class Net:
    def __init__(self, tc_of=lambda s: 1000):
        self.calls: list[tuple[str, int]] = []
        self.sleeps: list[float] = []
        self.bad_page: set[int] = set()
        self.bad_sim: set[int] = set()
        self.no_price: set[int] = set()
        self.no_club: set[int] = set()
        self.raise_kind: dict[tuple[str, int], str] = {}
        self.tc_of = tc_of
        self.hook = None

    def _raise(self, what, key):
        k = self.raise_kind.get((what, key))
        if k == "bug":
            raise ValueError("파싱 버그 흉내")
        if k is not None:
            raise (playerinfo.PlayerInfoError if what != "tc" else teamcolor.TeamColorError)("x", k)

    def page(self, spid):
        self.calls.append(("page", spid))
        if self.hook:
            self.hook(spid)
        self._raise("page", spid)
        if spid in self.bad_page:
            return playerinfo.PlayerInfo(sp_id=spid)   # 점검·깨진 페이지 — 이름·OVR·키 없음
        prices = {g: "-" for g in range(1, 14)} if spid in self.no_price else \
            {g: f"{spid % 1000 + g},000 BP" for g in range(1, 14)}
        return playerinfo.PlayerInfo(sp_id=spid, name="선수", ovr=100, height="181cm", weight="76kg", body_type="보통",
                                     prices=prices)

    def sim(self, spid):
        self.calls.append(("sim", spid))
        self._raise("sim", spid)
        pos = {} if spid in self.bad_sim else {c: 100 for c in _ALL16}
        return playerinfo.AbilitySim(ovr=100, groups={}, abilities={}, club_options=[] if spid in self.no_club else [(self.tc_of(spid), "팀")],
                                     feature_options=[], position_ovrs=pos)

    def detail(self, tid):
        self.calls.append(("tc", tid))
        self._raise("tc", tid)
        return "", [teamcolor.Step(1, 3, ["e"]), teamcolor.Step(2, 6, ["e"])]

    def lst(self):
        self.calls.append(("list", 0))
        return [teamcolor.TeamColorMeta(1000, "팀", "crests/l1.png", "", 1, 3, [])]

    def run(self, today=TODAY, cap=config.CARD_BACKFILL_DAILY_REQ, **kw):
        kw.setdefault("tick_req", None)
        return cc.run(config.DB_PATH, today, cap, fetch_page=self.page, fetch_sim=self.sim, fetch_detail=self.detail,
                      fetch_list=self.lst, sleep=self.sleeps.append, **kw)


def _cards(n, start=1, grade=1, po=25):
    return [(s, grade, po) for s in range(start, start + n)]


# ── 받기 · 계수 ─────────────────────────────────────────────────────────

def test_web_off_no_request_no_budget():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(3)})
        meta(e.conn, [1000])
        net = Net()
        config.WEB_DATA = False
        r = net.run()
        assert r.requests == 0 and net.calls == [] and store.budget_used(e.conn, TODAY.isoformat(), "card_backfill") == 0
        # 도는 중에 꺼져도 다음 요청부터 안 보낸다(계수도)
        config.WEB_DATA = True
        net.hook = lambda s: setattr(config, "WEB_DATA", False)
        r = net.run()
        assert len(net.calls) == 1 and store.budget_used(e.conn, TODAY.isoformat(), "card_backfill") == 1, net.calls


def test_budget_counts_every_request():
    """실패(형식)·시세 없는 응답도 센다 — 계수 = 실제로 보낸 요청."""
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(3)})
        meta(e.conn, [1000])
        net = Net()
        net.bad_page, net.no_price = {1}, {2}
        r = net.run()
        used = store.budget_used(e.conn, TODAY.isoformat(), "card_backfill")
        assert r.requests == len(net.calls) == used == 6, (r, net.calls, used)


def test_tick_cap_gap_and_daily_cap():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(5)})
        meta(e.conn, [1000])
        net = Net()
        r = net.run(tick_req=3, gap_s=7)
        assert r.requests == 3 and net.sleeps == [7, 7] and r.skipped_cap > 0, (r, net.sleeps)
        r = net.run(cap=4, tick_req=None)
        assert r.requests == 1 and store.budget_used(e.conn, TODAY.isoformat(), "card_backfill") == 4, r
        assert net.run(cap=4).requests == 0, "그날 상한을 넘었다"


def test_demand_accounts_only_both_sides_window():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": [(1, 5, 25), (2, 1, 0)], "opp": [(3, 1, 25)]})
        add_match(e.conn, TODAY - timedelta(days=1), {"me": [(1, 5, 25), (2, 3, 0)], "opp2": [(1, 8, 25)]})
        add_match(e.conn, TODAY, {"rk1": [(90, 1, 25)], "rk2": [(91, 1, 25)]})          # 랭커 픽 경기 — 계정 밖
        add_match(e.conn, TODAY - timedelta(days=40), {"me": [(80, 1, 25)]})             # 30일 밖
        dem = cc.demand(e.conn, TODAY)
        assert [s for s, _n, _g in dem] == [1, 2, 3], dem
        assert dem[0] == (1, 3, 5), dem[0]   # 등장 3 · 대표 강화 5(5·5·8)
        assert cc.demand(e.conn, TODAY, window=[9, 3, 9, 5]) == [(9, 0, None), (3, 0, None), (5, 0, None)]


def test_plan_order_mine_tc_c6_fill_refresh():
    """실행 순서 ①②④③⑤ — 표 번호와 다르다(④ 를 ③ 앞에 · 5판)."""
    with Env() as e:
        account(e.conn)
        store.set_trade_state(e.conn, my_ouid="me")
        add_match(e.conn, TODAY, {"me": [(1, 3, 25)] + _cards(4, start=10)})
        add_match(e.conn, TODAY, {"me": [(1, 3, 25), (2, 1, 25)], "opp": [(3, 1, 25)]})
        meta(e.conn, [1000], steps_fresh=False)
        old, yday = (TODAY - timedelta(days=40)).isoformat(), (TODAY - timedelta(days=1)).isoformat()
        with e.conn:
            e.conn.execute("INSERT INTO card_extra (spid, page_on, sim_on, club_tc, c6_grade) VALUES (1, ?, ?, '[[1000, \"팀\"]]', 3)",
                           (yday, yday))
            e.conn.execute("INSERT INTO card_extra (spid, page_on, sim_on, c6_grade) VALUES (2, ?, ?, 1)", (old, old))
            # 상대 카드 — 내 카드가 아니라 ① 이 페이지를 먼저 읽지 않는다 · 표본에 한 번 든 카드(c6_grade)
            e.conn.execute("INSERT INTO card_extra (spid, page_on, sim_on, c6_grade) VALUES (3, ?, ?, 1)", (yday, yday))
        ctx = cc.plan(e.conn, TODAY, dem=cc.demand(e.conn, TODAY), mine=cc.my_cards(e.conn, TODAY))
        steps = [j.step for j in ctx.jobs]
        rank = {"mine": 0, "tc_list": 1, "tc": 1, "c6": 2, "fill": 3, "refresh": 4}
        assert [rank[s] for s in steps] == sorted(rank[s] for s in steps), steps
        assert {"mine", "tc", "c6", "fill", "refresh"} <= set(steps), steps
        # 같은 카드 페이지를 한 바퀴에 두 번 안 읽는다(② 가 ④ 를 덮을 때)
        pages = [j.key for j in ctx.jobs if j.page]
        assert len(pages) == len(set(pages)), ctx.jobs
        # 측정 모드는 ②③ 만
        ctx = cc.plan(e.conn, TODAY, dem=cc.demand(e.conn, TODAY), mine=cc.my_cards(e.conn, TODAY), measure=True)
        assert {j.step for j in ctx.jobs} <= {"tc", "tc_list", "fill"}, ctx.jobs


def test_measure_demand_window_fixed():
    with Env() as e:
        account(e.conn)
        store.set_trade_state(e.conn, my_ouid="me")
        add_match(e.conn, TODAY, {"me": _cards(3, start=1)})
        meta(e.conn, [1000])
        net = Net()
        r = net.run(window=[7, 5, 6], kind=store.BUDGET_CARD_DEV)
        assert [k for w, k in net.calls if w == "page"] == [7, 5, 6], net.calls
        assert store.budget_used(e.conn, TODAY.isoformat(), "card_backfill_dev") == r.requests == 6
        assert store.budget_used(e.conn, TODAY.isoformat(), "card_backfill") == 0, "측정이 앱 장부를 썼다"
        n_c6 = e.conn.execute("SELECT COUNT(*) FROM card_extra WHERE c6_grade IS NOT NULL").fetchone()[0]
        n_pd = e.conn.execute("SELECT COUNT(*) FROM card_price_days").fetchone()[0]
        assert n_c6 == 0 and n_pd == 0, (n_c6, n_pd)


# ── 시도 기록 · 형식 · 막힘 ─────────────────────────────────────────────

def test_same_bad_card_does_not_stop_day():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(2)})
        meta(e.conn, [1000])
        net = Net()
        net.bad_page = {1}
        for _ in range(4):   # 같은 날 틱 넷
            r = net.run()
            assert not r.format_err, r
        assert net.calls.count(("page", 1)) == 1, net.calls
        assert ("page", 2) in net.calls and ("sim", 1) in net.calls
        page_on = e.conn.execute("SELECT page_on FROM card_extra WHERE spid = 1").fetchone()[0]
        assert page_on is None, "깨진 페이지를 저장했다"
        # 사흘 실패 → 7일 쉼 → 8일째 다시(처음 실패부터 1)
        day = TODAY
        for _ in range(2):
            day += timedelta(days=1)
            net.run(today=day)
        assert net.calls.count(("page", 1)) == 3
        for _ in range(config.CARD_TRY_REST_DAYS - 1):
            day += timedelta(days=1)
            net.run(today=day)
        assert net.calls.count(("page", 1)) == 3, "쉬는 동안 다시 읽었다"
        net.run(today=day + timedelta(days=1))
        assert net.calls.count(("page", 1)) == 4, "쉼이 끝났는데 다시 안 읽었다"
        assert e.conn.execute("SELECT fails FROM card_tries WHERE key = 'c:1:page'").fetchone()[0] == 1


def test_five_distinct_format_failures_stop_day():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(7)})
        meta(e.conn, [1000])
        net = Net()
        net.bad_page = net.bad_sim = set(range(1, 8))
        r = net.run()
        assert r.format_err and {k for _w, k in net.calls} == set(range(1, 6)), (r, net.calls)
        assert store.budget_hit_429(e.conn, TODAY.isoformat(), ("card_backfill_format",))
        n = len(net.calls)
        assert net.run().requests == 0 and len(net.calls) == n, "멈춘 날 다시 시작했다"


def test_no_price_is_success_not_retried():
    with Env() as e:
        account(e.conn)
        store.set_trade_state(e.conn, my_ouid="me")
        add_match(e.conn, TODAY, {"me": [(1, 5, 25)]})
        meta(e.conn, [1000])
        net = Net()
        net.no_price = {1}
        net.run()
        net.run()
        assert net.calls.count(("page", 1)) == 1, net.calls
        row = e.conn.execute("SELECT page_on FROM card_extra WHERE spid = 1").fetchone()
        tries = e.conn.execute("SELECT fails FROM card_tries WHERE key = 'c:1:page'").fetchone()[0]
        pd = e.conn.execute("SELECT price, src FROM card_price_days WHERE spid = 1 AND grade = 5").fetchone()
        assert row[0] == TODAY.isoformat() and tries == 0 and pd[0] is None and pd[1] & 1, (row, tries, pd)


def test_block_marks_day_and_other_path_block_stops_start():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(3)})
        meta(e.conn, [1000])
        net = Net()
        net.raise_kind[("page", 2)] = "rate"
        r = net.run()
        assert r.blocked and ("page", 3) not in net.calls, (r, net.calls)
        assert e.conn.execute("SELECT 1 FROM card_tries WHERE key = 'c:2:page'").fetchone() is None, "막힘을 실패로 셌다"
        assert net.run().requests == 0 and net.run(kind=store.BUDGET_CARD_DEV).requests == 0, "막힌 날 다시(같은 IP)"
        # 다른 웹 길(특성)이 그날 막혔으면 시작하지 않는다
        d2 = TODAY + timedelta(days=1)
        store.budget_mark_429(e.conn, d2.isoformat(), store.BUDGET_TRAIT_WEB_BLOCK)
        r = net.run(today=d2)
        assert r.blocked and r.requests == 0


def test_down_ends_round_without_fails():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(3)})
        meta(e.conn, [1000])
        net = Net()
        net.raise_kind[("page", 2)] = "down"
        r = net.run()
        assert r.down and not r.blocked and ("page", 3) not in net.calls, r
        assert not store.budget_hit_429(e.conn, TODAY.isoformat(), store.card_block_kinds())
        assert not store.try_skip(e.conn, "c:2:page", TODAY.isoformat()), "점검을 시도로 적었다"
        del net.raise_kind[("page", 2)]
        net.run()
        assert net.calls.count(("page", 2)) == 2, "점검 뒤 다음 틱에 다시 안 읽었다"


def test_page_and_sim_saved_separately():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": [(1, 1, 25)]})
        meta(e.conn, [1000])
        net = Net()
        net.bad_sim = {1}
        net.run()
        row = dict(e.conn.execute("SELECT height, weight, body, page_on, sim_on FROM card_extra WHERE spid = 1").fetchone())
        assert row == {"height": 181, "weight": 76, "body": "보통", "page_on": TODAY.isoformat(), "sim_on": None}, row
        net.bad_sim = set()
        net.calls.clear()
        net.run(today=TODAY + timedelta(days=1))
        assert net.calls == [("sim", 1)], net.calls
        po = e.conn.execute("SELECT pos_ovr, club_tc FROM card_extra WHERE spid = 1").fetchone()
        assert '"ST": 100' in po[0] and po[1] == '[[1000, "팀"]]', po


def test_teamcolor_steps_and_daily_share():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(30)})
        meta(e.conn, range(2000, 2030), enhance=[10001], steps_fresh=False)
        net = Net(tc_of=lambda s: 2000 + s % 30)
        net.run()   # 첫날 — 카드가 아직 없어 ② 없음
        assert not [c for c in net.calls if c[0] == "tc"], net.calls
        net.calls.clear()
        d2 = TODAY + timedelta(days=1)
        net.run(today=d2, tc_cap=5)
        tcs = [k for w, k in net.calls if w == "tc"]
        assert len(tcs) == 5 and tcs[0] == 10001, tcs   # 강화 팀컬러 먼저 · 하루 몫 5
        assert store.budget_used(e.conn, d2.isoformat(), "card_backfill_tc") == 5
        steps, day, fresh = store.load_teamcolor_steps(e.conn, 10001, d2)
        assert [s["members"] for s in steps] == [3, 6] and fresh


def test_price_days_src_bits_and_fixed_c6_grade():
    with Env() as e:
        account(e.conn)
        store.set_trade_state(e.conn, my_ouid="me")
        for _ in range(3):
            add_match(e.conn, TODAY, {"me": [(1, 5, 25)]})
        add_match(e.conn, TODAY, {"me": [(1, 3, 25)]})
        meta(e.conn, [1000])
        net = Net()
        net.run()
        rows = {(g, src) for g, src in e.conn.execute("SELECT grade, src FROM card_price_days WHERE spid = 1")}
        assert rows == {(3, 1), (5, 3)}, rows   # 내 카드(1) 3·5강 · 표본(2) 대표 강화 5
        assert e.conn.execute("SELECT c6_grade FROM card_extra WHERE spid = 1").fetchone()[0] == 5
        # 대표 강화가 바뀌어도 고정 강화는 그대로(비가 튀지 않게)
        later = TODAY + timedelta(days=config.C6_REFRESH_DAYS)
        for _ in range(9):
            add_match(e.conn, later, {"me": [(1, 8, 25)]})
        store.set_trade_state(e.conn, my_ouid=None)
        net.run(today=later)
        assert e.conn.execute("SELECT c6_grade FROM card_extra WHERE spid = 1").fetchone()[0] == 5
        assert e.conn.execute("SELECT src FROM card_price_days WHERE spid = 1 AND grade = 5 AND day = ?",
                              (later.isoformat(),)).fetchone()[0] == 2


def test_prune_keeps_70_days():
    with Env() as e:
        rows = [(1, 1, (TODAY - timedelta(days=d)).isoformat(), 10, 1) for d in (0, 70, 71, 200)]
        store.save_card_price_days(e.conn, rows)
        assert cc.prune(config.DB_PATH, TODAY) == 2
        left = sorted(r[0] for r in e.conn.execute("SELECT day FROM card_price_days"))
        assert left == [(TODAY - timedelta(days=70)).isoformat(), TODAY.isoformat()], left


def test_old_version_card_info_overwrite_keeps_extra():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": [(1, 1, 25)]})
        meta(e.conn, [1000])
        Net().run()
        # 옛 버전(2.11.1)의 save_card_info — INSERT OR REPLACE 로 card_info 줄을 통째로
        store.save_card_info(e.conn, 1, None, None, None, None, TODAY.isoformat())
        r = e.conn.execute("SELECT height, sim_on FROM card_extra WHERE spid = 1").fetchone()
        assert tuple(r) == (181, TODAY.isoformat()), tuple(r)


def test_save_card_rejects_broken_page():
    with Env() as e:
        good = playerinfo.PlayerInfo(sp_id=5, name="가", position="ST", ovr=110, salary=20, height="180cm",
                                     prices={1: "1,000 BP"})
        playerinfo.save_card(e.conn, good, "2026-10-01")
        assert playerinfo.save_card(e.conn, playerinfo.PlayerInfo(sp_id=5), TODAY.isoformat()) is False
        info = store.load_card_info(e.conn, [5])[5]
        assert (info["base_ovr"], info["fetched_on"]) == (110, "2026-10-01"), info


def test_chip_path_rejected_card_not_retried():
    with Env() as e:
        calls = []

        def fetch(s):
            calls.append(s)
            return playerinfo.PlayerInfo(sp_id=s)
        day = TODAY.isoformat()
        for _ in range(3):
            playerinfo.collect_cards(e.conn, [7], day, playerinfo.KIND_CHIP, 60, fetch=fetch)
        assert calls == [7], "깨진 카드를 같은 날 다시 읽었다"
        assert store.load_card_info(e.conn, [7]) == {}, "깨진 페이지를 카드 정보로 저장했다"
        playerinfo.collect_cards(e.conn, [7], (TODAY + timedelta(days=1)).isoformat(), playerinfo.KIND_CHIP, 60, fetch=fetch)
        assert calls == [7, 7]


def test_chip_try_does_not_block_backfill():
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": [(7, 1, 25)]})
        meta(e.conn, [1000])
        store.try_record(e.conn, "p:7", TODAY.isoformat(), ok=False)
        net = Net()
        net.run()
        assert ("page", 7) in net.calls, "칩 길의 시도가 백필을 막았다"


def test_block_streak_counts_days_after_on_day():
    with Env() as e:
        for d in (1, 2, 3):   # 10-01 = 켠 날 — 안 센다
            store.budget_mark_429(e.conn, f"2026-10-0{d}", "card_backfill_block")
        assert store.card_block_streak(e.conn, "card_backfill", "2026-10-01") == 2
        assert not cc.should_turn_off(e.conn, ON_AT)
        store.budget_mark_429(e.conn, "2026-10-04", "card_backfill_block")
        assert cc.should_turn_off(e.conn, ON_AT)
        store.budget_take(e.conn, "2026-10-05", "card_backfill", None)   # 막힘 없이 받은 날이 끊는다
        assert not cc.should_turn_off(e.conn, ON_AT)
        assert not cc.should_turn_off(e.conn, "2026-10-05T10:00:00"), "다시 켠 뒤 옛 막힘으로 바로 꺼졌다"


def test_fresh_db_backfill_days():
    """빈 DB + 주 계정 경기만 — 앱 경로(demand_days 30 · 600/일)로 날마다 요청 순서별 수를 잰다(가짜 시계 · 8판).
    둘째 날 ② 는 첫날 받은 카드의 팀컬러가 몰린다 — 몫(CARD_BACKFILL_TC_DAILY_REQ)이 막는다."""
    with Env() as e:
        account(e.conn)   # 내 계정 확정 전 — ① 은 따로(test_price_days_src_bits_and_fixed_c6_grade)
        n = 1200
        for i in range(0, n, 11):
            add_match(e.conn, TODAY - timedelta(days=(i // 11) % 25), {"me": _cards(min(11, n - i), start=i + 1)})
        meta(e.conn, range(2000, 2400), enhance=range(10001, 10006), steps_fresh=False)
        net = Net(tc_of=lambda s: 2000 + s % 400)
        per_day = []
        day, done_day = TODAY, None
        for k in range(14):
            before = len(net.calls)
            net.run(today=day)
            got = net.calls[before:]
            per_day.append({w: sum(1 for x, _ in got if x == w) for w in ("page", "sim", "tc", "list")})
            st = cc.status(e.conn, day)
            if done_day is None and st.need and st.known == st.need:
                done_day = k + 1
            day += timedelta(days=1)
        print("    하루 요청(page·sim·tc·list):", [tuple(d.values()) for d in per_day])
        assert all(sum(d.values()) <= config.CARD_BACKFILL_DAILY_REQ for d in per_day)
        assert per_day[0]["tc"] == 0 and per_day[1]["tc"] == config.CARD_BACKFILL_TC_DAILY_REQ, per_day[:2]
        # 카드 1,200장 × 2 + 팀컬러 400 + 강화 5 ≈ 2,805요청 ÷ 600 → 5일 이상 · 팀컬러 몫이 퍼뜨려 7일 안
        assert done_day is not None and 5 <= done_day <= 7, (done_day, per_day)
        assert sum(per_day[-1].values()) < 100, per_day[-1]   # 다 찬 뒤 하루는 작다


# ── 넥슨 응답 분류 · 시뮬레이터 칸 ──────────────────────────────────────

class _Res:
    def __init__(self, status=200, text="<html></html>", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


def _with_web(res):
    keep = ranker.web_get, config.WEB_DATA
    ranker.web_get = lambda *a, **k: res
    config.WEB_DATA = True
    return keep


def test_web_response_kinds():
    cases = [(_Res(403), "rate"), (_Res(429), "rate"), (_Res(200, headers={"cf-mitigated": "challenge"}), "rate"),
             (_Res(200, "<title>Just a moment...</title>"), "rate"), (_Res(302), "down"), (_Res(500), "down"),
             (_Res(200, "<div>점검 진행 중</div>"), "down")]
    for res, want in cases:
        assert ranker.classify_web_response(res) == want, (res.status_code, res.text, want)
        keep = _with_web(res)
        try:
            for fn in (lambda: playerinfo.fetch_player_info(1), lambda: playerinfo.fetch_player_ability(1),
                       lambda: teamcolor.fetch_detail(1)):
                try:
                    fn()
                    raise AssertionError("오류가 안 났다")
                except (playerinfo.PlayerInfoError, teamcolor.TeamColorError) as ex:
                    assert ex.kind == want, (res.status_code, want, ex.kind)
        finally:
            ranker.web_get, config.WEB_DATA = keep
    assert ranker.classify_web_response(_Res(200, "<html>정상</html>")) is None


def test_sim_without_club_teamcolor_is_format_fail():
    """16칸은 맞는데 소속 팀컬러가 비어 왔다(마크업 바뀜) — 저장하면 '팀컬러 없음'이 30일 신선하게 남는다(리뷰 [중]1)."""
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": [(1, 1, 25)]})
        meta(e.conn, [1000])
        net = Net()
        net.no_club = {1}
        net.run()
        assert ('sim', 1) in net.calls, net.calls   # 전제: 시뮬레이터를 실제로 읽었다
        row = e.conn.execute("SELECT sim_on, club_tc FROM card_extra WHERE spid = 1").fetchone()
        assert row is not None and row[0] is None and row[1] is None, tuple(row)
        assert e.conn.execute("SELECT fails FROM card_tries WHERE key = 'c:1:sim'").fetchone()[0] == 1


def test_unexpected_parse_error_records_and_moves_on():
    """요청 함수 안의 예상 못 한 예외 — 그 카드만 형식 실패로 적고 다음 카드로. 바퀴째 끝나면 맨 앞 카드가 틱마다 막는다(리뷰 [하]1)."""
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(2)})
        meta(e.conn, [1000])
        net = Net()
        net.raise_kind[("page", 1)] = "bug"
        r = net.run()
        assert ("page", 2) in net.calls and not r.error, (r, net.calls)
        assert e.conn.execute("SELECT fails FROM card_tries WHERE key = 'c:1:page'").fetchone()[0] == 1
        net.run()
        assert net.calls.count(("page", 1)) == 1, "같은 날 다시 읽었다"


def test_run_carries_status_and_info_row_reads_it():
    """[정보] 줄은 예약이 들고 있는 마지막 바퀴 상태만 읽는다 — 화면 스레드에서 수요를 세면 0.4초 멈춘다(리뷰 [중]2)."""
    with Env() as e:
        account(e.conn)
        add_match(e.conn, TODAY, {"me": _cards(2)})
        meta(e.conn, [1000])
        r = Net().run()
        assert r.status is not None and (r.status.known, r.status.need) == (2, 2), r.status
        s = _sched()

        class P(QWidget):
            _card_sched = s
        p = P()
        keep = cc.status

        def no_ui_status(*_a, **_k):
            raise AssertionError("화면 스레드에서 수요를 셌다")
        cc.status = no_ui_status
        try:
            dlg = app_main.AboutDialog(p)
            assert "첫 바퀴" in dlg.lb_card.text(), dlg.lb_card.text()
            s.tick()
            FakeLoader.made[-1].finish(cc.RunResult(status=cc.Status(5, 10, 3, False)))
            assert s.last == cc.Status(5, 10, 3, False) and "5/10장" in dlg.lb_card.text(), dlg.lb_card.text()
            dlg.done(0)
            s.tick()
            FakeLoader.made[-1].finish(cc.RunResult(status=cc.Status(7, 10, 3, False)))
            assert "5/10장" in dlg.lb_card.text(), "닫은 창을 계속 고쳤다(신호를 안 끊었다)"
            dlg.deleteLater()
        finally:
            cc.status = keep


def test_sim_pos_of_golden():
    """실측 응답 셋(GK 부폰 · ST 시어러 · 수비 카푸)을 경기 자리 코드로 읽으면 그 칸의 OVR — 표가 틀리면 C1 이 조용히 틀린다."""
    want = {100001179: {0: 125, 25: 68, 5: 73},
            100000051: {25: 124, 24: 124, 26: 124, 20: 121, 21: 121, 23: 119, 27: 119, 18: 119},
            268005003: {3: 107, 7: 107, 4: 104, 5: 104, 6: 104, 1: 103, 2: 107, 8: 107, 10: 104}}
    assert set(config.SIM_POS_OF) == set(range(28)), "경기 자리 28칸(0~27) 전부 잇지 않았다"
    for spid, by_po in want.items():
        text = gzip.open(_FIX / f"sim_{spid}.html.gz", "rt", encoding="utf-8").read()
        keep = _with_web(_Res(200, text))
        try:
            sim = playerinfo.fetch_player_ability(spid)
        finally:
            ranker.web_get, config.WEB_DATA = keep
        assert playerinfo.sim_ok(sim), sorted(sim.position_ovrs)
        got = {po: sim.position_ovrs[config.SIM_POS_OF[po]] for po in by_po}
        assert got == by_po, (spid, got)
        assert len(sim.club_options) == 5, sim.club_options


def test_parse_cm_kg():
    assert playerinfo.parse_cm("193cm") == 193 and playerinfo.parse_kg("82kg") == 82
    assert playerinfo.parse_cm("-") is None and playerinfo.parse_kg("") is None


# ── 예약 · 셸 · 토글 ─────────────────────────────────────────────────────

class _Sig:
    def __init__(self):
        self.cbs = []

    def connect(self, f):
        self.cbs.append(f)

    def emit(self, *a):
        for f in list(self.cbs):
            f(*a)


class FakeLoader:
    made: list["FakeLoader"] = []

    def __init__(self, today):
        self.today = today
        self.done = _Sig()
        self.running = self.cancelled = self.terminated = False
        self.waits: list[int] = []
        FakeLoader.made.append(self)

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=0):
        self.waits.append(ms)
        return True

    def terminate(self):
        self.terminated, self.running = True, False

    def finish(self, res):
        self.running = False
        self.done.emit(res)


def _sched():
    return app_main.CardBackfillScheduler(now_fn=lambda: datetime(2026, 10, 18, 12, 0, 0))


def test_tick_gate_off_sends_nothing():
    cases = [("토글 꺼짐", dict(on="")), ("웹 데이터 꺼짐", dict(web=False)),
             ("동의 11", dict(notice=config.CARD_BACKFILL_NOTICE_VERSION - 1)),
             (".env 손 수정 — 토글 꺼짐", dict(disk={config.CARD_BACKFILL_VAR: ""})),
             (".env 손 수정 — 웹 데이터 꺼짐", dict(disk={config.WEB_DATA_VAR: 0})),
             (".env 손 수정 — 동의 11", dict(disk={config.NOTICE_VAR: config.CARD_BACKFILL_NOTICE_VERSION - 1})),
             # 이 프로세스만 11(창을 아직 다시 안 본 실행본) — 디스크 값만 보면 이 칸이 빈다(변이 #18)
             ("이 프로세스만 동의 11", dict(notice=config.CARD_BACKFILL_NOTICE_VERSION - 1,
                                         disk={config.NOTICE_VAR: config.CARD_BACKFILL_NOTICE_VERSION}))]
    for name, kw in cases:
        with Env() as e:
            e.set(**kw)
            _sched().tick()
            assert FakeLoader.made == [], name
    with Env() as e:
        e.set(collect=False)   # 랭킹 수집은 안 본다(U5)
        s = _sched()
        s.tick()
        s.tick()
        assert len(FakeLoader.made) == 1 and FakeLoader.made[0].today == date(2026, 10, 18), "켜졌는데 안 돌았다/둘 띄웠다"


def test_unreadable_env_does_nothing():
    with Env():
        s = _sched()
        s.tick()
        keep = config._read_env_disk
        config._read_env_disk = lambda: None
        try:
            s.sync()
            assert not FakeLoader.made[0].cancelled, ".env 를 못 읽었는데 멈췄다"
        finally:
            config._read_env_disk = keep


def test_switch_off_cancels_without_purge():
    with Env() as e:
        with e.conn:
            e.conn.execute("INSERT INTO card_extra (spid, height) VALUES (1, 180)")
        s = _sched()
        s.tick()
        e.set(on="")
        s.sync()
        assert FakeLoader.made[0].cancelled, "꺼졌는데 로더를 안 멈췄다"
        assert e.conn.execute("SELECT COUNT(*) FROM card_extra").fetchone()[0] == 1, "카드 정보를 지웠다(G3)"


def test_accept_notice_starts_once_and_toggle_off_sends_nothing():
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "MainWindow"
              for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "ask_notice_update")
    assert "_sync_card_backfill(start=ok)" in ast.get_source_segment(src, fn), "안내 수락 뒤 예약을 안 깨운다"
    with Env() as e:
        e.set(on="")   # 업그레이드 사용자 — 동의해도 토글 꺼짐
        s = _sched()
        s.sync(start=True)
        assert FakeLoader.made == []
        e.set()
        s.sync(start=True)
        s.sync(start=True)
        assert len(FakeLoader.made) == 1


def test_block_streak_turns_off_only_backfill():
    with Env() as e:
        e.set(collect=True)
        for d in (2, 3, 4):
            store.budget_mark_429(e.conn, f"2026-10-0{d}", "card_backfill_block")
        s = _sched()
        offs = []
        s.switched_off.connect(offs.append)
        s.tick()
        FakeLoader.made[0].finish(cc.RunResult(blocked=True))
        assert offs == ["blocked"] and e.env_line(config.CARD_BACKFILL_VAR) == "" \
            and e.env_line(config.CARD_BACKFILL_OFF_VAR) == "blocked", (offs, config.ENV_PATH.read_text(encoding="utf-8"))
        assert e.env_line(config.RANK_COLLECT_VAR) == "1", "랭킹 수집까지 껐다"


def test_reenable_resets_block_streak():
    with Env() as e:
        for d in (2, 3, 4):
            store.budget_mark_429(e.conn, f"2026-10-0{d}", "card_backfill_block")
        config.set_card_backfill(True)   # [다시 켜기] — 켠 시각 새로
        assert config.CARD_BACKFILL_OFF == "" and config.CARD_BACKFILL_ON
        s = _sched()
        offs = []
        s.switched_off.connect(offs.append)
        s.tick()
        FakeLoader.made[0].finish(cc.RunResult(blocked=True))
        assert offs == [] and e.env_line(config.CARD_BACKFILL_VAR), "다시 켠 직후 옛 막힘으로 다시 꺼졌다"


def test_shell_five_places_and_quit():
    with Env():
        config.CARD_BACKFILL_TICK_S, config.CARD_BACKFILL_FIRST_S = 7, 3   # 기본값이 아닌 값 — 배선을 잰다
        s = _sched()
        assert s._timer.interval() == 7000 and s._first.interval() == 3000
        sh = tray.AppShell(_app, card_sched=s)
        quits = []
        sh.app = type("A", (), {"quit": lambda self: quits.append(1), "windowIcon": lambda self: _app.windowIcon()})()
        got = []
        sh.attach_window(type("W", (), {"attach_card_sched": lambda self, x: got.append(x)})())
        assert got == [s], "셸이 창에 예약을 안 붙였다"
        sh.window = None
        sh.start(tray_mode=True)
        assert s._timer.isActive() and s._first.isActive(), "셸이 예약 타이머를 안 켰다"
        s.tick()
        ld = FakeLoader.made[0]
        sh._tray_timer.stop()
        sh.quit_app()
        assert ld.cancelled and not ld.terminated and ld.waits and not s._timer.isActive() and quits == [1], ld.waits
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    main = ast.get_source_segment(src, next(n for n in ast.parse(src).body
                                            if isinstance(n, ast.FunctionDef) and n.name == "main"))
    assert "card_sched=CardBackfillScheduler()" in main, "main 이 셸에 카드 예약을 안 넘긴다"


def test_fast_shutdown_returns_running_loader():
    with Env():
        s = _sched()
        s.tick()
        ld = FakeLoader.made[0]
        assert s.shutdown(fast=True) == [ld] and ld.cancelled and not ld.terminated
        assert _sched().shutdown(fast=True) == []


class _Parent(QWidget):
    def __init__(self, accept=True):
        super().__init__()
        self.synced, self.asked = [], 0
        self._accept = accept
        self._card_sched = type("S", (), {"sync": lambda _s, start=False: self.synced.append(start)})()

    def ask_notice_update(self, parent=None):
        self.asked += 1
        if self._accept:
            config.accept_notice(config.WEB_DATA)
        return self._accept


def test_toggle_on_starts_once():
    with Env() as e:
        e.set(on="")
        p = _Parent()
        dlg = app_main.AboutDialog(p)
        assert dlg.chk_card.isEnabled() and not dlg.chk_card.isChecked()
        dlg.chk_card.setChecked(True)
        assert e.env_line(config.CARD_BACKFILL_VAR).startswith(datetime.now().date().isoformat()) and p.synced == [True]
        assert "카드 정보" in dlg.lb_card.text(), dlg.lb_card.text()
        dlg.chk_card.setChecked(False)
        assert e.env_line(config.CARD_BACKFILL_VAR) == "" and p.synced == [True, False]
        dlg.deleteLater()


def test_toggle_disabled_without_web_data():
    with Env() as e:
        e.set(web=False, on="")
        dlg = app_main.AboutDialog(_Parent())
        assert not dlg.chk_card.isEnabled() and "홈페이지 데이터" in dlg.lb_card.text()
        dlg.deleteLater()


def test_toggle_asks_notice_12_when_collect_off():
    """수집을 끈 8~11 동의자 — 다시 묻는 창이 안 뜨므로(notice_update_pending) 토글을 누를 때 묻는다."""
    for accept in (True, False):
        with Env() as e:
            e.set(notice=11, collect=False, on="")
            assert not config.notice_update_pending(), "전제가 바뀌었다 — 이 테스트가 재는 상황이 아니다"
            p = _Parent(accept)
            dlg = app_main.AboutDialog(p)
            assert dlg.chk_card.isEnabled(), "회색이면 영영 못 켠다"
            dlg.chk_card.setChecked(True)
            assert p.asked == 1
            if accept:
                assert e.env_line(config.CARD_BACKFILL_VAR) and p.synced == [True] and dlg.chk_card.isChecked()
            else:
                assert e.env_line(config.CARD_BACKFILL_VAR) == "" and p.synced == [] and not dlg.chk_card.isChecked()
            dlg.deleteLater()


def test_web_off_clears_card_toggle():
    with Env() as e:
        dlg = app_main.AboutDialog(_Parent())
        assert dlg.chk_card.isChecked()
        dlg.chk_web.setChecked(False)
        assert e.env_line(config.CARD_BACKFILL_VAR) == "" and not dlg.chk_card.isChecked() and not dlg.chk_card.isEnabled()
        dlg.chk_web.setChecked(True)
        assert not dlg.chk_card.isChecked(), "웹 데이터를 다시 켰더니 조용히 되살아났다"
        dlg.deleteLater()


def test_rank_off_and_clear_leave_backfill():
    with Env() as e:
        e.set(collect=True)
        with e.conn:
            e.conn.execute("INSERT INTO card_extra (spid, height) VALUES (1, 180)")
        store.save_card_price_days(e.conn, [(1, 1, TODAY.isoformat(), 10, 2)])
        config.set_rank_collect(False)
        app_main.clear_rank_records(None, None)
        assert config.read_card_backfill() is True, "랭킹 수집 끄기·지우기가 카드 백필을 껐다"
        assert e.conn.execute("SELECT COUNT(*) FROM card_extra").fetchone()[0] == 1
        assert e.conn.execute("SELECT COUNT(*) FROM card_price_days").fetchone()[0] == 1


def test_empty_screen_text_by_reason():
    import notice
    with Env() as e:
        e.set(web=False)
        assert "홈페이지 데이터" in notice.card_empty_text(cc.screen_state(e.conn, TODAY))
        e.set(on="")
        assert "자동 채우기" in notice.card_empty_text(cc.screen_state(e.conn, TODAY))
        e.set()
        store.budget_mark_429(e.conn, TODAY.isoformat(), "card_backfill_block")
        assert "막아" in notice.card_empty_text(cc.screen_state(e.conn, TODAY))
        assert "0/0장" in notice.card_empty_text(cc.screen_state(e.conn, TODAY + timedelta(days=1)))


import watchdog  # noqa: E402


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as ex:
            failed += 1
            print(f"[FAIL] {t.__name__}: {ex}")
        except Exception as ex:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(ex).__name__}: {ex}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
