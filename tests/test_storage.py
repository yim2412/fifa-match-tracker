"""[정보 › 저장 공간](57 K9 · storage.py · store.RESET_*) · 지우는 중 걸쇠(app_main.Worker) · 내 스쿼드 특성(57 K12 ·
traitcollect.collect_mine · store.my_traits) 테스트 — 임시 폴더·가짜 응답, 네트워크 없이.

pytest 없이 `python tests/test_storage.py`. 화면 쪽(탭·확인창·바꿔 끼운 뒤 창 상태)은 test_ui_smoke 의 test_storage_*.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config
import storage
import store
import traitcollect as tc
import watchdog

NICK = "남의닉네임가나다"   # 지운 뒤 파일 바이트에 남으면 안 되는 글자
ME, OPP = "me-ouid", "opp-ouid"
NOW = datetime(2026, 10, 20, 12, 0, 0)


def _detail(mid: str, day: str = "2026-10-20T10:00:00", spids=range(101, 112)) -> dict:
    me = [{"spId": s, "spPosition": i + 1, "spGrade": 5} for i, s in enumerate(spids)]
    return {"matchId": mid, "matchDate": day, "matchType": config.DEFAULT_MATCH_TYPE,
            "matchInfo": [{"ouid": ME, "nickname": "나", "player": me},
                          {"ouid": OPP, "nickname": NICK, "player": []}]}


def _fill(db: Path) -> None:
    conn = store.open_db(db)
    try:
        store.save_matches(conn, [_detail("m1"), _detail("m2", "2026-10-19T10:00:00")])
        store.upsert_account(conn, OPP, NICK)
        store.set_trade_state(conn, key_fp="fp1", my_ouid=ME, done_buy="x", **{"predict_final:me:2026": "{}"})
        conn.execute("INSERT INTO api_budget (day, kind, attempts) VALUES ('2026-10-20', 'trait_web', 7)")
        conn.execute("INSERT INTO card_tries (key, day, fails) VALUES ('p:1', '2026-10-20', 0)")
        conn.commit()
    finally:
        conn.close()


def test_reset_tables_cover_schema():
    """스키마의 모든 표가 남김(RESET_KEEP)·버림(RESET_WIPE) 중 정확히 하나 — 새 표를 만들면 고르게(검토 기준 14)."""
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not r[0].startswith("sqlite_")}
        finally:
            conn.close()
    keep, wipe = set(store.RESET_KEEP), set(store.RESET_WIPE)
    assert not keep & wipe, keep & wipe
    assert tables == keep | wipe, ("빠짐", tables - keep - wipe, "없는 표", (keep | wipe) - tables)


def test_reset_db_keeps_only_listed_rows():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fifa.db"
        _fill(db)
        before = db.read_bytes() + (db.with_name("fifa.db-wal").read_bytes() if db.with_name("fifa.db-wal").exists() else b"")
        assert NICK.encode("utf-8") in before, "심은 닉네임이 원본에 없다 — 아래 단언이 비었다"
        assert storage.reset_db(db) == "ok"
        assert not db.with_name("fifa.db-wal").exists() or db.with_name("fifa.db-wal").stat().st_size == 0
        assert NICK.encode("utf-8") not in db.read_bytes(), "바꿔 끼운 파일에 남의 닉네임이 남았다"
        conn = store.open_db(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM card_tries").fetchone()[0] == 0
            assert store.trade_state(conn) == {"key_fp": "fp1"}, "키 지문 밖 trade_state 가 남았다(my_ouid·predict_final·커서)"
            assert store.budget_used(conn, "2026-10-20", "trait_web") == 7, "하루 계수가 지워졌다 — 상한이 풀린다"
        finally:
            conn.close()


def test_reset_db_busy_keeps_old_file():
    """다른 연결이 파일을 잡고 있으면(다른 실행본) 옛 파일 그대로 — 반쯤 지워지지 않는다."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fifa.db"
        _fill(db)
        hold = sqlite3.connect(str(db))
        hold.execute("BEGIN")
        hold.execute("SELECT COUNT(*) FROM matches").fetchone()   # 읽기 트랜잭션 — 체크포인트가 끝까지 못 간다
        try:
            assert storage.reset_db(db) == "busy"
        finally:
            hold.rollback()
            hold.close()
        conn = store.open_db(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2, "바꿔 끼우기가 실패했는데 경기가 사라졌다"
        finally:
            conn.close()
        assert not db.with_name("fifa.db.new").exists(), "반쯤 만든 새 파일이 남았다"


def test_reset_db_cancel_before_swap():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fifa.db"
        _fill(db)
        assert storage.reset_db(db, cancel=lambda: True) == "cancelled"
        conn = store.open_db(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2
        finally:
            conn.close()


def _cache(d: Path) -> Path:
    c = d / ".cache"
    (c / "player_images").mkdir(parents=True)
    (c / "player_images" / "1.png").write_bytes(b"x" * 10)
    (c / "icons").mkdir()
    (c / "icons" / "a.png").write_bytes(b"y" * 20)
    (c / "meta_spid.json").write_text("{}", encoding="utf-8")
    (c / "m1.json").write_text("{}", encoding="utf-8")      # DB 에 있는 경기
    (c / "m9.json").write_text("{}", encoding="utf-8")      # 아직 못 넣은 경기
    (c / "collect.log").write_text("log", encoding="utf-8")
    return c


def test_clear_images_and_details_leave_the_rest():
    with tempfile.TemporaryDirectory() as d:
        c = _cache(Path(d))
        assert storage.clear_images(c) == (2, 0)
        assert sorted(p.name for p in c.iterdir()) == ["collect.log", "m1.json", "m9.json", "meta_spid.json"]
        assert storage.clear_details(c) == (2, 0)
        assert sorted(p.name for p in c.iterdir()) == ["collect.log", "meta_spid.json"], "메타가 지워졌다(오프라인 등급·시즌 이름)"
        assert storage.clear_cache(c)[0] == 2 and c.exists() and not list(c.iterdir())


def test_measure_sizes_and_pending():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fifa.db"
        _fill(db)
        c = _cache(Path(d))
        s = storage.measure(db, Path(d) / "rank.db", c)
        assert s.bytes["images"] == 30 and s.bytes["rank"] == 0, s.bytes
        assert s.details == 2 and s.pending == 1, (s.details, s.pending)
        assert s.bytes["cards"] > 0 and s.bytes["all"] > db.stat().st_size, s.bytes


def test_clear_cards_only_card_tables():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fifa.db"
        _fill(db)
        assert storage.clear_cards(db) == 1
        conn = store.open_db(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM card_tries").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2
        finally:
            conn.close()


# ── 걸쇠 ──────────────────────────────────────────────────────────────

def _qt():
    from PyQt6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


def test_latch_blocks_only_writers_of_the_cleared_store():
    _qt()
    import app_main as am
    keep = set(am.CLEARING)
    try:
        am.CLEARING.clear()
        w = am.IconLoader([], Path(tempfile.gettempdir()))
        assert not am.clearing_blocks(w.WRITES), "지우는 중이 아닌데 막았다 — 아래 단언이 비었다"
        am.CLEARING.add(am.STORE_IMAGES)
        assert am.clearing_blocks(am.IconLoader.WRITES)
        assert not am.clearing_blocks(am.TradeLoader.WRITES), "그림을 지우는데 거래 받기까지 막았다"
        assert not am.clearing_blocks(am.HabitsWorker.WRITES), "아무것도 안 쓰는 작업을 막았다"
        w.start()
        assert w.blocked and not w.isRunning(), "걸쇠가 start 를 안 막았다"
        am.CLEARING.clear()
        am.CLEARING.add("all")
        assert am.clearing_blocks(am.TradeLoader.WRITES) and not am.clearing_blocks(am.KeyCheckWorker.WRITES)
    finally:
        am.CLEARING.clear()
        am.CLEARING.update(keep)


def test_every_qthread_is_a_worker_and_store_users_declare_writes():
    """새 로더가 QThread 를 바로 물려받으면 걸쇠 밖이다 · 저장소를 쓰는데 WRITES 가 비면 지우는 동안 안 멈춘다(2회차 S1)."""
    import re
    src = Path(__file__).resolve().parents[1].joinpath("app_main.py").read_text(encoding="utf-8")
    direct = re.findall(r"^class (\w+)\(QThread\):", src, re.M)
    assert direct == ["Worker"], direct
    import app_main as am
    starts = [m.start() for m in re.finditer(r"^class ", src, re.M)] + [len(src)]
    uses = re.compile(r"\b(store\.\w+|open_db|images\.fetch\w*|cardcollect\.run|rankcollect\.(collect|maintenance|save_from_pages)|"
                      r"ownerdist\.run_round|rankerpick\.collect|traitcollect\.run|tradecollect\.collect|playerinfo\.(save|collect)\w*|"
                      r"get_match_detail|prune_detail_cache)")
    readers = {"EloLoader", "PredictWorker"}   # 읽기지만 open_db 가 스키마를 쓴다 — 그래서 fifa 로 둔다(아래에서 같이 본다)
    missing = []
    for m in re.finditer(r"^class (\w+)\(Worker\):", src, re.M):
        name = m.group(1)
        body = src[m.start():min(s for s in starts if s > m.start())]
        if uses.search(body) and not getattr(am, name).WRITES:
            missing.append(name)
    assert not missing, ("저장소를 쓰는데 WRITES 가 없다", missing)
    for name in readers:
        assert am.STORE_FIFA in getattr(am, name).WRITES, name
    # 심은 위반을 잡나 — 검사가 비지 않았는지
    fake = "class X(Worker):\n    def run(self):\n        store.open_db(1)\n"
    assert uses.search(fake)


def test_clear_worker_waits_for_writers_then_reports_busy():
    _qt()
    import app_main as am

    class Slow:
        def __init__(self):
            self.ev = threading.Event()

        def wait(self, ms):
            self.ev.wait(ms / 1000)
            return self.ev.is_set()

        def isRunning(self):
            return not self.ev.is_set()

    keep = config.CLEAR_HALT_WAIT_S
    try:
        config.CLEAR_HALT_WAIT_S = 0.2
        t = Slow()
        w = am.ClearWorker("cards", [t])
        got = []
        w.done.connect(got.append)
        w.run()   # 같은 스레드에서 — 신호는 바로 온다
        assert got and got[0].state == "busy", "쓰는 쪽이 안 멈췄는데 지웠다"
        t.ev.set()
        with tempfile.TemporaryDirectory() as d:
            keep_db = config.DB_PATH
            try:
                config.DB_PATH = Path(d) / "fifa.db"
                _fill(config.DB_PATH)
                got.clear()
                w2 = am.ClearWorker("cards", [t])
                w2.done.connect(got.append)
                w2.run()
                assert got[0].state == "ok" and got[0].removed == 1, got
            finally:
                config.DB_PATH = keep_db
    finally:
        config.CLEAR_HALT_WAIT_S = keep


# ── 내 스쿼드 특성(57 K12) ───────────────────────────────────────────

def _team(spids, build=5):
    return [tc.TeamCard(s, 1, build, (28, 55, 13), (2, 12, 42)) for s in spids]


def _mine_env(d: Path):
    db = d / "fifa.db"
    _fill(db)
    conn = store.open_db(db)
    store.upsert_account(conn, ME, "나")
    return conn


def test_collect_mine_finds_team_and_caches_sn():
    with tempfile.TemporaryDirectory() as d:
        conn = _mine_env(Path(d))
        try:
            calls = {"sn": 0, "team": 0}

            def fetch_sn(_s, nick):
                calls["sn"] += 1
                assert nick == "나"
                return 77

            def fetch(_s, sn, tt, part):
                calls["team"] += 1
                assert sn == 77
                return _team(range(101, 112)) if (tt, part) == (0, 1) else _team([1, 2])

            st = tc.collect_mine(None, conn, ME, now_fn=lambda: NOW, fetch=fetch, fetch_sn=fetch_sn, sleep=lambda s: None)
            assert st == "ok", st
            row = store.my_traits(conn, ME)
            assert row["sn"] == 77 and row["team"] == "0-1" and len(row["body"]) == 11, row
            assert calls == {"sn": 1, "team": 5}, calls   # 기본 순서 (1,0)(1,1)(1,2)(0,0)(0,1)
            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW, fetch=fetch, fetch_sn=fetch_sn) == "fresh"
            assert calls == {"sn": 1, "team": 5}, "같은 날 다시 물었다"
            nxt = NOW.replace(day=21)
            assert tc.collect_mine(None, conn, ME, now_fn=lambda: nxt, fetch=fetch, fetch_sn=fetch_sn, sleep=lambda s: None) == "ok"
            assert calls == {"sn": 1, "team": 6}, ("다음 날: 번호는 줄에서 · 지난 팀부터", calls)
            assert store.budget_used(conn, nxt.date().isoformat(), store.BUDGET_TRAIT_WEB) == 1
        finally:
            conn.close()


def test_collect_mine_stops_on_block_and_limits():
    with tempfile.TemporaryDirectory() as d:
        conn = _mine_env(Path(d))
        try:
            def boom(*_a):
                raise tc.TraitError("막힘", "rate")

            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW, fetch=boom, fetch_sn=lambda *_: 1) == "rate"
            assert store.my_traits(conn, ME) is None
            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW, fetch=boom, fetch_sn=lambda *_: 1) == "rate", \
                "막힌 날 다시 요청했다"
            assert tc.collect_mine(None, conn, "nobody", now_fn=lambda: NOW.replace(day=22)) == "nomatch"
            conn.execute("INSERT OR REPLACE INTO api_budget (day, kind, attempts) VALUES (?, ?, ?)",
                         ("2026-10-23", store.BUDGET_TRAIT_WEB, config.TRAIT_WEB_DAILY_REQ))
            conn.commit()
            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW.replace(day=23), fetch_sn=lambda *_: 1) == "limit"
        finally:
            conn.close()


def test_collect_mine_nouser_asks_once_a_day():
    with tempfile.TemporaryDirectory() as d:
        conn = _mine_env(Path(d))
        try:
            asked = []

            def fetch_sn(_s, nick):
                asked.append(nick)
                return None

            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW, fetch_sn=fetch_sn) == "nouser"
            assert asked == ["나"], "처음엔 묻는다 — 아래 단언이 비었다"
            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW, fetch_sn=fetch_sn) == "nouser"
            assert asked == ["나"], ("못 찾은 날 탭을 다시 열 때 또 물었다", asked)
            assert tc.collect_mine(None, conn, ME, now_fn=lambda: NOW.replace(day=21), fetch_sn=fetch_sn) == "nouser"
            assert asked == ["나", "나"], "다음 날엔 다시 묻는다"
        finally:
            conn.close()


def test_prune_my_traits():
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            store.save_my_traits(conn, ME, sn=1, match_id="m", team="1:0", state="ok", body=[], fetched_at="t")
            assert store.prune_my_traits(conn, ME) == 0
            assert store.prune_my_traits(conn, "other") == 1 and store.my_traits(conn, ME) is None
            store.save_my_traits(conn, ME, sn=1, match_id="m", team="1:0", state="ok", body=[], fetched_at="t")
            store.save_my_traits(conn, "other", sn=2, match_id="m", team="1:0", state="ok", body=[], fetched_at="t")
            assert store.my_traits(conn, ME) is None, "내 계정은 하나 — 다른 계정 줄이 남았다"
            assert store.prune_my_traits(conn, None) == 1
        finally:
            conn.close()


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
