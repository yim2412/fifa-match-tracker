"""예측 되짚기(predict_check.py · 50단계 B8) 테스트 — 가짜 fifa.db·가짜 순위 조회로, 네트워크 없이.

pytest 없이 `python tests/test_predict_check.py`. 닉네임·번호는 전부 지어낸 값이다.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import predict_check as PC
import ranker
import store
from seasons import Season

MINE = "ouid-mine"
SN = 777
START = date(2026, 7, 9)
SEASON = Season(90, "시즌 9", START, date(2026, 9, 10))
TODAY = date(2026, 9, 12)
KEY = "test-key"


class Env:
    def __init__(self, *, mine=MINE):
        self.dir = Path(tempfile.mkdtemp(prefix="pcheck_"))
        self.db = self.dir / "fifa.db"
        c = store.open_db(self.db)
        store.upsert_account(c, MINE, "내닉")
        store.upsert_account(c, "ouid-other", "남닉")
        store.save_seasons(c, [SEASON])
        for ouid, sn in ((MINE, SN), ("ouid-other", 888)):
            for day in (date(2026, 8, 1), date(2026, 8, 2)):
                store.save_prediction(c, ouid, datetime.combine(day, datetime.min.time()), profile_sn=sn,
                                      season_start=START.isoformat(), end_source="estimate", p200=0.3, p1000=0.9,
                                      lo=150, hi=400)
        if mine:
            store.set_trade_state(c, my_ouid=mine, key_fp=store.key_fingerprint(KEY))
        c.close()
        self.calls: list = []

    def fetch_with(self, rank, sn=SN):
        def fetch(nick, no):
            self.calls.append((nick, no))
            return ranker.RankerInfo(nickname=nick, rank=rank, profile_sn=sn if rank else None)
        return fetch

    def state(self):
        c = store.open_db(self.db)
        try:
            return store.trade_state(c), store.predictions(c)
        finally:
            c.close()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def with_env(fn, **kw):
    env = Env(**kw)
    old_allowed, old_key = config.predict_check_allowed, config.API_KEY
    config.predict_check_allowed, config.API_KEY = (lambda: True), KEY
    try:
        return fn(env)
    finally:
        config.predict_check_allowed, config.API_KEY = old_allowed, old_key
        env.close()


def run(env, fetch, today=TODAY, cancel=None):
    return PC.run(fifa_path=env.db, today=today, fetch=fetch, cancel=cancel)


def test_found_writes_state_key_and_copies_columns():
    def go(env):
        out = run(env, env.fetch_with(123))
        assert out == {"asked": 1, "found": 1}, out
        st, preds = env.state()
        rec = json.loads(st[f"predict_final:{MINE}:{START.isoformat()}"])
        assert rec["state"] == "found" and rec["rank"] == 123
        mine = [p for p in preds if p["ouid"] == MINE]
        assert len(mine) == 2 and all(p["final_rank"] == 123 and p["final_state"] == "found" for p in mine)
        assert all(p["final_state"] is None for p in preds if p["ouid"] != MINE), "남의 계정은 건드리지 않는다"
        assert env.calls == [("내닉", 90)]
        assert run(env, env.fetch_with(1)) == {"asked": 0, "found": 0}, "찾았으면 다시 묻지 않는다"
    with_env(go)


def test_not_found_retries_daily_then_settles():
    def go(env):
        f = env.fetch_with(None)
        assert run(env, f, TODAY)["asked"] == 1
        assert run(env, f, TODAY)["asked"] == 0, "같은 날은 한 번"
        _st, preds = env.state()
        assert all(p["final_state"] is None for p in preds), "다시 묻는 동안은 사본을 안 채운다"
        assert run(env, f, date(2026, 9, 13))["asked"] == 1
        # 처음 물은 날(09-12)부터 RETRY_DAYS 가 지나면 굳는다 — 더 묻지 않고 사본이 채워진다
        settle = date.fromordinal(TODAY.toordinal() + config.PREDICT_CHECK_RETRY_DAYS)
        assert run(env, f, settle)["asked"] == 0
        _st, preds = env.state()
        assert [p["final_state"] for p in preds if p["ouid"] == MINE] == ["not_found"] * 2
        assert all(p["final_rank"] is None for p in preds)
        # 굳은 뒤 늦게 찾아도 바꾸지 않는다
        assert run(env, env.fetch_with(50), date(2026, 9, 30))["asked"] == 0
    with_env(go)


def test_profile_sn_mismatch_is_not_found():
    def go(env):
        run(env, env.fetch_with(99, sn=SN + 1))     # 같은 닉네임인데 다른 사람(닉네임 바꿈) — 남을 내 순위로 읽지 않는다
        st, _p = env.state()
        assert json.loads(st[f"predict_final:{MINE}:{START.isoformat()}"])["state"] == "not_found"
    with_env(go)


def test_errors_write_nothing():
    def go(env):
        def boom(nick, no):
            raise ranker.RankerError("점검")
        assert run(env, boom) == {"asked": 0, "found": 0}
        st, _p = env.state()
        assert not any(k.startswith(store.PREDICT_FINAL_PREFIX) for k in st)
        assert run(env, env.fetch_with(10))["found"] == 1, "다음 회차가 다시 묻는다"
    with_env(go)


def test_gate_and_account_and_unfinished_season():
    env = Env()
    try:
        # 관문 닫힘(기본: 값이 NOTICE_VERSION 보다 크다) — 요청이 안 나간다. 이게 없으면 아래 "안 나감"이 공허하다
        assert config.predict_check_allowed() is False
        assert run(env, env.fetch_with(5)) == {"skipped": "gate"} and env.calls == []
    finally:
        env.close()
    def no_acct(env):
        assert run(env, env.fetch_with(5)) == {"skipped": "no_account"} and env.calls == []
    with_env(no_acct, mine=None)
    def key_changed(env):
        config.API_KEY = "다른 키"           # 키 지문이 다르면 내 계정 확인 전 — 묻지 않는다
        assert run(env, env.fetch_with(5)) == {"skipped": "no_account"} and env.calls == []
    with_env(key_changed)
    def not_ended(env):
        assert run(env, env.fetch_with(5), today=date(2026, 9, 9)) == {"asked": 0, "found": 0} and env.calls == []
    with_env(not_ended)


def test_cancel_before_write_leaves_no_state():
    def go(env):
        ev = threading.Event()
        def fetch(nick, no):
            ev.set()                     # 묻는 사이 [수집 기록 지우기] 가 왔다
            return ranker.RankerInfo(nickname=nick, rank=5, profile_sn=SN)
        run(env, fetch, cancel=ev)
        st, preds = env.state()
        assert not any(k.startswith(store.PREDICT_FINAL_PREFIX) for k in st)
        assert all(p["final_state"] is None for p in preds)
    with_env(go)


def test_clear_elo_removes_final_keys_and_rows():
    def go(env):
        run(env, env.fetch_with(7))
        c = store.open_db(env.db)
        try:
            store.set_trade_state(c, **{f"predict_final:ouid-other:{START.isoformat()}": json.dumps({"state": "found"})})
            before = [k for k in store.trade_state(c) if k.startswith(store.PREDICT_FINAL_PREFIX)]
            assert len(before) == 2, before
            store.clear_elo(c, keep_ouid=MINE)
            assert [k for k in store.trade_state(c) if k.startswith(store.PREDICT_FINAL_PREFIX)] == [
                f"predict_final:{MINE}:{START.isoformat()}"]
            store.clear_elo(c)
            assert not [k for k in store.trade_state(c) if k.startswith(store.PREDICT_FINAL_PREFIX)]
            assert store.predictions(c) == []
        finally:
            c.close()
    with_env(go)


def test_old_schema_gets_columns_and_replace_is_refilled():
    def go(env):
        c = sqlite3.connect(env.db)
        c.execute("ALTER TABLE predictions DROP COLUMN final_rank")
        c.execute("ALTER TABLE predictions DROP COLUMN final_state")
        c.execute("ALTER TABLE predictions DROP COLUMN final_checked_at")
        c.commit()
        c.close()
        c = store.open_db(env.db)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(predictions)")}
        c.close()
        assert {"final_rank", "final_state", "final_checked_at"} <= cols
        run(env, env.fetch_with(7))
        c = store.open_db(env.db)       # 옛 버전의 REPLACE 가 그날 줄의 열을 비운다
        store.save_prediction(c, MINE, datetime(2026, 8, 1), profile_sn=SN, season_start=START.isoformat(),
                              end_source="estimate", p200=0.4, p1000=0.9, lo=1, hi=2)
        by_day = {p["day"]: p["final_state"] for p in store.predictions(c, MINE)}
        assert by_day == {"2026-08-01": None, "2026-08-02": "found"}, "REPLACE 가 그날 줄의 열을 비웠어야 아래가 의미 있다"
        c.close()
        run(env, env.fetch_with(1))     # 키가 있으니 묻지 않고 비어 있는 줄만 다시 채운다
        _st, preds = env.state()
        assert all(p["final_rank"] == 7 for p in preds if p["ouid"] == MINE), preds
        assert env.calls == [("내닉", 90)]
    with_env(go)


def test_sn_taken_from_latest_line_that_has_one():
    """마지막 줄의 프로필 번호가 비어도 앞 줄 것으로 대조한다 · 전부 비면 묻지 않는다(결과가 not_found 로 정해져 있다)."""
    def go(env):
        c = store.open_db(env.db)
        store.save_prediction(c, MINE, datetime(2026, 8, 3), profile_sn=None, season_start=START.isoformat(),
                              end_source="estimate", p200=0.3, p1000=0.9, lo=1, hi=2)
        c.close()
        assert run(env, env.fetch_with(123))["found"] == 1
    with_env(go)
    def none_at_all(env):
        c = store.open_db(env.db)
        c.execute("UPDATE predictions SET profile_sn = NULL")
        c.commit()
        c.close()
        assert run(env, env.fetch_with(123)) == {"asked": 0, "found": 0} and env.calls == []
    with_env(none_at_all)


def test_results_pending_flag():
    def go(env):
        c = store.open_db(env.db)
        try:
            run(env, env.fetch_with(None))
            assert PC.results(c, MINE, TODAY)[0]["pending"] is True, "다시 묻는 동안은 아직 굳지 않았다"
            settle = date.fromordinal(TODAY.toordinal() + config.PREDICT_CHECK_RETRY_DAYS)
            assert PC.results(c, MINE, settle)[0]["pending"] is False
        finally:
            c.close()
    with_env(go)


def test_gate_reads_disk_env_with_notice_version():
    """관문 배선 — 디스크 .env 의 동의 버전이 PREDICT_CHECK_NOTICE_VERSION 이상일 때만 참(이 PC 의 실제 .env 에 달리지 않게 가짜로)."""
    keep = config._read_env_disk
    want = config.PREDICT_CHECK_NOTICE_VERSION
    def fake(notice, web="1", collect="1"):
        return lambda: {config.NOTICE_VAR: str(notice), config.WEB_DATA_VAR: web, config.RANK_COLLECT_VAR: collect}
    try:
        config._read_env_disk = fake(want)
        assert config.predict_check_allowed() is True
        config._read_env_disk = fake(want - 1)
        assert config.predict_check_allowed() is False, "한 칸 낮은 동의로는 못 연다"
        config._read_env_disk = fake(want, web="0")
        assert config.predict_check_allowed() is False, "웹 데이터가 꺼지면 닫힌다"
        config._read_env_disk = fake(want, collect="0")
        assert config.predict_check_allowed() is False, "수집이 꺼지면 닫힌다"
    finally:
        config._read_env_disk = keep


def test_process_meta_calls_run_with_cancel_and_paths():
    """수집 정리가 되짚기를 실제로 부르고(배선) cancel·DB 경로·오늘을 그대로 넘긴다 · 되짚기가 죽어도 정리는 계속."""
    import rankcollect as rc
    seen = []
    keep = PC.run
    ev = threading.Event()
    env = Env()
    try:
        PC.run = lambda **kw: seen.append(kw) or {"asked": 1}
        conn = rc.open_rank_db(env.dir / "rank.db")
        try:
            out = rc.process_meta(conn, datetime(2026, 9, 12, 12, 0), season_ends=[], fifa_path=env.db, cancel=ev)
        finally:
            conn.close()
        assert len(seen) == 1 and seen[0]["cancel"] is ev and seen[0]["fifa_path"] == env.db, seen
        assert seen[0]["today"] == date(2026, 9, 12) and out["predict_check"] == {"asked": 1}
        PC.run = lambda **kw: 1 / 0
        conn = rc.open_rank_db(env.dir / "rank2.db")
        try:
            out = rc.process_meta(conn, datetime(2026, 9, 12, 12, 0), season_ends=[])
        finally:
            conn.close()
        assert "predict_check" not in out and out["rolled"] is False, "되짚기 예외가 정리를 막았다"
    finally:
        PC.run = keep
        env.close()


def test_results_for_screen():
    def go(env):
        c = store.open_db(env.db)
        try:
            assert PC.results(c, MINE)[0]["state"] is None
            run(env, env.fetch_with(7))
            r = PC.results(c, MINE)
            assert r[0]["state"] == "found" and r[0]["rank"] == 7 and len(r[0]["days"]) == 2
        finally:
            c.close()
    with_env(go)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as e:      # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
