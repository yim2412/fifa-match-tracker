"""같은 OVR 비교 자료(122단계 조각 ①) 받기·정리·지우기·예약 테스트 — 지어낸 구단주·가짜 넥슨, 네트워크 없이.

`python tests/test_squadcmp.py`. 상수는 일부러 config 기본값이 아닌 값으로 줄여 돌린다(기본값을 코드에 박아도 통과하지 않게).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import app_main  # noqa: E402
import config  # noqa: E402
import matchtime  # noqa: E402
import nexon_api  # noqa: E402
import notice  # noqa: E402
import rankcollect  # noqa: E402
import ranker  # noqa: E402
import squadcmp as sc  # noqa: E402
import storage  # noqa: E402
import store  # noqa: E402
import tray  # noqa: E402
from nexon_api import QUOTA_CODE, NexonAPIError  # noqa: E402

NOW = datetime(2026, 11, 25, 12, 0, 0)
DAY = NOW.date().isoformat()
ON = "2026-11-24T10:00:00"
V = 7                       # 시험용 안내 번호(진짜 99 로 닫혀 있다)
SMALL = dict(SQUAD_CMP_STREAK=4, SQUAD_CMP_FAIL_STREAK=5, SQUAD_CMP_WORKERS=3, SQUAD_CMP_SERIAL_REQ=0, SQUAD_CMP_429_WAIT_S=9,
             SQUAD_CMP_DAILY_REQ=10_000, SQUAD_CMP_SELFCHECK_REQ=100, SQUAD_CMP_REFRESH_DAYS=5, SQUAD_CMP_FAIL_DAYS=2,
             SQUAD_CMP_KEEP_DAYS=11, SQUAD_CMP_SNAP_MAX_H=20, SQUAD_CMP_TRY_MAX=2, SQUAD_CMP_TOP=3000, SQUAD_CMP_PERSON_REQ=5)
_KEYS = ("ENV_PATH", "DB_PATH", "RANK_DB_PATH", "WEB_DATA", "RANK_COLLECT", "NOTICE_ACCEPTED", "NOTICE_VERSION",
         "API_KEY", "KEY_STATE", "SQUAD_CMP_ON", "SQUAD_CMP_OFF", "SQUAD_CMP_NOTICE_VERSION", "SERVER_URL",
         "SQUAD_CMP_TICK_S", "SQUAD_CMP_FIRST_S", "SQUAD_CMP_HALT_WAIT_MS") + tuple(SMALL)


def ts(dt: datetime) -> int:
    return int(dt.timestamp())


KST = timezone(timedelta(hours=9))   # config 오프셋을 쓰지 않는다. 쓰면 오프셋 변이가 초록이 된다


def raw_of(at: datetime) -> str:
    """시각 → 넥슨 원문(UTC). 시간대가 없으면 `ts`·앱의 `datetime.now()` 처럼 PC 시간대로 읽는다(9시간을 빼면 UTC 인 CI 에서 어긋난다)."""
    return at.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def cards(seed: int = 0) -> list[dict]:
    """선발 11 + 교체 7. 자리 0·1… 가 아니라 흔한 자리로."""
    pos = (0, 3, 4, 6, 7, 10, 13, 15, 20, 25, 27)
    out = [{"spId": 300_000_000 + seed * 100 + i, "spPosition": p, "spGrade": 5 + i % 4} for i, p in enumerate(pos)]
    return out + [{"spId": 400_000_000 + i, "spPosition": 28, "spGrade": 1} for i in range(7)]


def match(ouid: str, at: datetime = NOW - timedelta(hours=3), seed: int = 0, mine: list | None = None, opp: list | None = None) -> dict:
    return {"matchId": "m", "matchDate": raw_of(at),
            "matchInfo": [{"ouid": "상대", "player": cards(99) if opp is None else opp},
                          {"ouid": ouid, "player": cards(seed) if mine is None else mine}]}


class FakeAPI:
    """닉네임 → ouid → 목록 → 상세. who[닉네임] = 동작 이름 또는 예외(늘 그 사람에게) — 나머지는 정상."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.by_n: dict[int, object] = {}      # 몇 번째 요청에서 무엇을 던지나
        self.who: dict[str, object] = {}
        self.lists: dict[str, object] = {}     # 닉네임 → 목록 응답(원본)
        self.details: dict[str, object] = {}   # 경기 id → 상세(또는 예외)
        self.owner: dict[str, object] = {}     # 닉네임 → 계정 응답(원본)
        self.hook = None
        self.delay = 0.0
        self.inflight = self.peak = 0
        self._lock = threading.Lock()

    def _hit(self, kind: str, arg: str):
        with self._lock:
            self.calls.append((kind, arg))
            n = len(self.calls)
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.hook:
                self.hook(kind, n)
            e = self.by_n.get(n)
            if e is not None:
                raise e
        finally:
            with self._lock:
                self.inflight -= 1

    def get_raw(self, path, **p):
        if path == nexon_api.EP_ID:
            nick = p["nickname"]
            self._hit("id", nick)
            w = self.who.get(nick)
            if isinstance(w, Exception):
                raise w
            return self.owner.get(nick, {"ouid": "ou-" + nick})
        assert path == nexon_api.EP_USER_MATCH, path
        assert p["matchtype"] == 52 and p["offset"] == 0 and p["limit"] == 3, p   # 감독모드 최근 3개
        nick = p["ouid"][3:]
        self._hit("list", nick)
        return self.lists.get(nick, [nick + "#0", nick + "#1", nick + "#2"])

    def get_match_detail_nocache(self, mid, attempts=1):
        self._hit("detail", mid)
        d = self.details.get(mid)
        if isinstance(d, Exception):
            raise d
        nick, i = mid.rsplit("#", 1)
        return match("ou-" + nick, seed=int(re.sub(r"\D", "", nick) or 0)) if d is None else d

    def get_match_detail(self, *a, **k):
        raise AssertionError("남의 경기를 .cache 에 남기는 길을 불렀다")

    def get_ouid(self, *a, **k):
        raise AssertionError("재시도·꼴 바꿈이 있는 길을 불렀다")

    get_match_ids = get_ouid


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []
        self._lock = threading.Lock()

    def now(self):
        return self.t

    def sleep(self, s):
        with self._lock:
            self.slept.append(s)
            self.t += s


class Env:
    def __init__(self, n: int = 6, taken: datetime = NOW - timedelta(hours=2)):
        self._d = tempfile.TemporaryDirectory()
        p = Path(self._d.name)
        self.dir = p
        self._saved = {k: getattr(config, k) for k in _KEYS}
        self._environ = dict(os.environ)
        for k, v in SMALL.items():
            setattr(config, k, v)
        config.ENV_PATH, config.DB_PATH, config.RANK_DB_PATH = p / ".env", p / "fifa.db", p / "rank.db"
        config.SQUAD_CMP_NOTICE_VERSION = config.NOTICE_VERSION = V
        config.SERVER_URL, config.KEY_STATE = "", "ok"
        self.set()
        self.api, self.clock, self.now, self.tries = FakeAPI(), Clock(), NOW, {}
        self.n = n
        if n:
            self.snap(range(1, n + 1), taken)
        self.conn = store.open_db(config.DB_PATH)

    def snap(self, ranks, taken: datetime):
        rc = rankcollect.open_rank_db(config.RANK_DB_PATH)
        try:
            rankcollect.save_snapshot(rc, [ranker.RankRow(rank=r, profile_sn=900 + r, nickname=f"구단주{r}", team_color="팀",
                                                          elo=3000.0 - r) for r in ranks], taken)
        finally:
            rc.close()

    def set(self, web=True, collect=True, notice_ok=V, on=ON, key="k", off="", disk: dict | None = None):
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.API_KEY = web, collect, notice_ok, key
        config.SQUAD_CMP_ON, config.SQUAD_CMP_OFF = on, off
        vals = {config.WEB_DATA_VAR: int(web), config.RANK_COLLECT_VAR: int(collect), config.NOTICE_VAR: notice_ok,
                config.SQUAD_CMP_VAR: on, config.SQUAD_CMP_OFF_VAR: off, config.API_KEY_VAR: key}
        vals.update(disk or {})
        config.ENV_PATH.write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")

    def env_line(self, var):
        for ln in config.ENV_PATH.read_text(encoding="utf-8").splitlines():
            if ln.startswith(f"{var}="):
                return ln.split("=", 1)[1]

    def run(self, **kw) -> sc.Result:
        kw.setdefault("tries", self.tries)
        return sc.collect(config.squads_db_path(), config.DB_PATH, rankcollect.open_rank_db_ro, lambda: self.api,
                          sleep=self.clock.sleep, clock=self.clock.now, now_fn=lambda: self.now, **kw)

    def rows(self) -> dict[int, sqlite3.Row]:
        c = sc.open_existing(config.squads_db_path())
        if c is None:
            return {}
        try:
            return {r["profile_sn"]: r for r in c.execute("SELECT * FROM squads")}
        finally:
            c.close()

    def meta(self, key):
        c = sc.open_existing(config.squads_db_path())
        if c is None:
            return None
        try:
            return sc.meta_get(c, key)
        finally:
            c.close()

    def put(self, sn, checked: datetime, fail=None, lineup_at: datetime | None = None, at: datetime | None = None, body=b"x" * 66):
        c = sc.open_db(config.squads_db_path())
        with c:
            c.execute("INSERT OR REPLACE INTO squads VALUES (?, ?, ?, ?, ?, ?)",
                      (sn, ts(at) if at else None, body if lineup_at else None, ts(lineup_at) if lineup_at else None, ts(checked), fail))
        c.close()

    def used(self):
        return store.budget_used(self.conn, self.now.date().isoformat(), store.BUDGET_SQUAD)

    def hit(self, kind):
        return store.budget_hit_429(self.conn, self.now.date().isoformat(), (kind,))

    def close(self):
        self.conn.close()
        for k, v in self._saved.items():
            setattr(config, k, v)
        os.environ.clear()
        os.environ.update(self._environ)
        self._d.cleanup()


@contextmanager
def env(**kw):
    e = Env(**kw)
    try:
        yield e
    finally:
        e.close()


def err(code="", status=None):
    return NexonAPIError("x", code=code, status=status)


def all_bytes() -> bytes:
    return b"".join(p.read_bytes() for p in sc.db_files(config.squads_db_path())[:2] if p.exists())


# ── 선발 · 꼴 ─────────────────────────────────────────────────────────

def test_lineup_matches_dev_matches_format_and_roundtrips():
    from tools import dev_matches
    body = sc.pack_lineup(sc.starters({"player": cards(3)}))
    assert len(body) == 66 and body == dev_matches.lineup_body(cards(3)), "개발 수집과 묶는 꼴이 다르다(조각 ③ 대조가 안 된다)"
    assert sc.unpack_lineup(body) == sorted((p["spId"], p["spPosition"], p["spGrade"]) for p in cards(3) if p["spPosition"] != 28)
    wide = [(5_000_000_000, 3, 1)] + [(i, 4, 1) for i in range(10)]
    assert sc.unpack_lineup(sc.pack_lineup(wide)) == sorted(wide), "넓은 꼴이 안 돌아온다"


def test_lineup_of_picks_my_side_and_time_is_kst_epoch():
    kst = datetime(2026, 11, 25, 1, 30, tzinfo=KST)
    at, body = sc.lineup_of(match("me", kst, seed=5), "me")
    assert body == sc.pack_lineup(sc.starters({"player": cards(5)})), "상대 쪽 선발을 저장했다"
    assert at == matchtime.epoch("2026-11-24T16:30:00") == ts(kst), "경기 시각이 KST 가 아니다"
    assert sc.lineup_of(match("me", opp=[]), "me")[1] is None, "상대가 빈 판(몰수)인데 선발로 쳤다"
    assert sc.lineup_of(match("me", mine=cards()[:10]), "me")[1] is None, "10명 선발을 받았다"
    for bad in (None, [], {"matchDate": None, "matchInfo": []}, {**match("me"), "matchInfo": "x"}, match("남"),
                match("me", mine=[{"id": 1}]), match("me", opp=[{"spId": 1, "spPosition": "ST", "spGrade": 1}]),
                {"matchDate": raw_of(NOW), "matchInfo": [{"ouid": "me", "player": None}, {"ouid": "o", "player": []}]}):
        try:
            sc.lineup_of(bad, "me")
        except sc.BadFormat:
            continue
        raise AssertionError(f"꼴이 다른 응답을 읽었다: {bad!r:.80}")


# ── 차례 ──────────────────────────────────────────────────────────────

def test_targets_order_and_due_boundaries():
    with env(n=0):
        d = timedelta(days=1)
        rows = [{"profile_sn": i, "nickname": f"n{i}"} for i in range(1, 14)] + [{"profile_sn": 99, "nickname": ""}]
        have = {1: (ts(NOW - 5 * d), None), 2: (ts(NOW - 5 * d) + 1, None),                 # 5일 = 시험용 갱신 주기
                3: (ts(NOW - 8 * d), None), 4: (ts(NOW - 2 * d), "gone"), 5: (ts(NOW - 2 * d) + 1, "gone"),
                6: (ts(NOW - 2 * d), "nogame"), 7: (ts(NOW - timedelta(hours=13)), "format"),  # 어제 23시
                8: (ts(NOW - timedelta(hours=11)), "format"),                                  # 오늘 01시
                9: (ts(NOW + 2 * d), None),                                                     # 미래(시계를 되돌린 PC)
                10: (ts(NOW + timedelta(hours=1)), None)}
        got = [t.profile_sn for t in sc.targets(rows, have, NOW)]
        assert set(got) == {1, 3, 4, 6, 7, 9, 11, 12, 13}, got
        assert set(got[:3]) == {11, 12, 13}, f"줄이 없는 사람이 맨 앞이 아니다 {got}"
        assert got[3:6] == [3, 1, 9], f"지난 사람은 오래된 순 {got}"
        assert set(got[6:]) == {4, 6, 7}, f"다시 물을 실패가 맨 뒤가 아니다 {got}"
        prev = {t.profile_sn: t.prev_fail for t in sc.targets(rows, have, NOW)}
        assert prev[4] == "gone" and prev[7] == "format" and prev[11] is None and prev[1] is None
        big = [{"profile_sn": i, "nickname": "n"} for i in range(50)]
        a = [t.profile_sn for t in sc.targets(big, {}, NOW)]
        assert a == [t.profile_sn for t in sc.targets(big, {}, NOW)] != [t.profile_sn for t in sc.targets(big, {}, NOW + d)]
        assert a != sorted(a), "순위 순으로 받으면 하루 몫이 모자란 사람에게 늘 같은 구간만 찬다"


# ── 받기 ──────────────────────────────────────────────────────────────

def test_budget_counts_every_request_and_saves_no_names():
    with env() as e:
        r = e.run()
        assert (r.stop, r.ok, r.due, r.requests) == ("", 6, 6, 18) and len(e.api.calls) == 18, r
        assert e.used() == 18 and store.squad_req(e.conn, ON) == 18, "하루 계수·누적이 요청 수와 다르다"
        rows = e.rows()
        assert set(rows) == {901, 902, 903, 904, 905, 906}
        assert rows[903]["lineup"] == sc.pack_lineup(sc.starters({"player": cards(3)}))
        assert rows[903]["match_at"] == ts(NOW - timedelta(hours=3)) and rows[903]["lineup_at"] == rows[903]["checked_at"] == ts(NOW)
        blob = all_bytes()
        assert "구단주".encode() not in blob and b"ou-" not in blob, "닉네임·계정 번호를 저장했다"
        assert rows[903].keys() == ["profile_sn", "match_at", "lineup", "lineup_at", "checked_at", "fail"]
        assert e.run().due == 0, "방금 받은 사람을 또 묻는다"


def test_gates_make_zero_requests():
    with env() as e:
        for name, kw in (("토글", dict(on="")), ("동의", dict(notice_ok=V - 1)), ("웹", dict(web=False)),
                         ("수집", dict(collect=False)), ("키", dict(key=""))):
            e.set(**kw)
            assert e.run().stop == "off" and not e.api.calls, name
        e.set()
        config.KEY_STATE = "fail"
        assert e.run().stop == "off" and not e.api.calls, "키를 못 읽은 상태"
        config.KEY_STATE = "ok"
        for kind in (store.BUDGET_SQUAD, store.BUDGET_OPENAPI):
            store.budget_mark_429(e.conn, DAY, kind)
            assert e.run().stop == "marked" and not e.api.calls, kind
            e.conn.execute("DELETE FROM api_budget")
            e.conn.commit()
        for key in (sc.KEY_DAY, sc.FORMAT_DAY):
            c = sc.open_db(config.squads_db_path())
            sc._meta_set(c, key, DAY)
            c.close()
            assert e.run().stop == "marked" and not e.api.calls, key
            assert not sc.should_run(config.DB_PATH, config.squads_db_path(), NOW)
            c = sc.open_db(config.squads_db_path())
            sc._meta_set(c, key, "2026-11-24")   # 어제 표시
            c.close()
        assert sc.should_run(config.DB_PATH, config.squads_db_path(), NOW), "어제 표시에 막혔다"
        assert e.run().ok == 6, "대조군 — 다 열리면 받는다"


def test_shipped_version_is_closed():
    """조각 ①② 동안은 닫혀 있다 — 지금 배포되는 값 그대로면 어떤 동의로도 관문이 안 열린다."""
    assert config.SQUAD_CMP_NOTICE_VERSION == 99 > config.NOTICE_VERSION and not config.squad_cmp_open()
    with env() as e:
        config.SQUAD_CMP_NOTICE_VERSION = e._saved["SQUAD_CMP_NOTICE_VERSION"]
        config.NOTICE_VERSION = e._saved["NOTICE_VERSION"]
        e.set(notice_ok=config.NOTICE_VERSION)
        assert config.read_squad_cmp() is False and e.run().stop == "off" and not e.api.calls


def test_snapshot_must_be_fresh():
    with env(taken=NOW - timedelta(hours=20)) as e:
        assert e.run().ok == 6, "딱 20시간(시험용 한도)"
    with env(taken=NOW - timedelta(hours=20, minutes=1)) as e:
        r = e.run()
        assert r.stop == "snapshot" and not e.api.calls, "낡은 명단에 하루 몫을 쓴다"
        assert not config.squads_db_path().exists(), "받지도 않는데 파일을 만들었다"
        e.snap(range(1, 4), NOW - timedelta(hours=1))
        assert e.run().due == 3, "새 명단"
    with env(n=0) as e:
        assert e.run().stop == "snapshot" and not e.api.calls and not config.RANK_DB_PATH.exists()


def test_one_person_rules():
    with env(n=1) as e:
        nick = "구단주1"

        def go(**kw) -> sqlite3.Row | None:
            if config.squads_db_path().exists():
                sc.purge(config.squads_db_path())
            e.api.calls.clear()
            e.api.who, e.api.lists, e.api.details, e.api.owner = {}, {}, {}, {}
            for k, v in kw.items():
                setattr(e.api, k, v)
            e.tries.clear()
            e.result = e.run()
            return e.rows().get(901)

        empty, old = match("ou-" + nick, opp=[]), match("ou-" + nick, NOW - timedelta(days=11, minutes=1))
        edge = match("ou-" + nick, NOW - timedelta(days=11), seed=2)
        r = go(details={nick + "#0": empty, nick + "#1": match("ou-" + nick, seed=4)})
        assert r["fail"] is None and r["lineup"] == sc.pack_lineup(sc.starters({"player": cards(4)})), "빈 판을 건너뛰지 않았다"
        r = go(details={nick + f"#{i}": empty for i in range(3)})
        assert (r["fail"], r["lineup"], len(e.api.calls)) == ("nogame", None, 5), "셋 다 몰수"
        r = go(details={nick + f"#{i}": err("OPENAPI00009", 400) for i in range(3)})
        assert (r["fail"], len(e.api.calls)) == ("nogame", 5), "셋 다 없는 자료"
        r = go(details={nick + "#0": err("OPENAPI00009", 400)})
        assert r["fail"] is None and r["lineup"], "없는 자료면 다음 경기로"
        r = go(details={nick + "#0": old})
        assert (r["fail"], r["lineup"], len(e.api.calls)) == ("nogame", None, 3), "오래된 선발을 저장했다(14일 정리와 되풀이)"
        assert go(details={nick + "#0": edge})["lineup"], "딱 14일(시험용 11일)은 쓴다"
        r = go(who={nick: err("OPENAPI00004", 400)})
        assert (r["fail"], len(e.api.calls)) == ("gone", 1)
        r = go(lists={nick: []})
        assert (r["fail"], len(e.api.calls)) == ("nogame", 2), "빈 목록"
        for bad in ({"a": 1}, None, [1, 2], [""]):
            assert go(lists={nick: bad})["fail"] == "format", f"목록 꼴 {bad!r}"
        for bad in ([], {}, {"ouid": ""}, {"ouid": 3}):
            assert go(owner={nick: bad})["fail"] == "format", f"계정 꼴 {bad!r}"
        assert go(details={nick + "#0": {"matchInfo": []}})["fail"] == "format"
        # 저장하지 않는 것 — 목록·상세의 표에 없는 코드(목록의 00009·00004 도) · 5xx · 연결 · 깨진 JSON
        for kw in (dict(details={nick + "#0": err("OPENAPI00004", 400)}), dict(details={nick + "#0": err("", 500)}),
                   dict(who={nick: err("OPENAPI00009", 400)}), dict(who={nick: err(nexon_api.NETWORK_CODE)}),
                   dict(who={nick: ValueError("JSON")})):
            assert go(**kw) is None and e.result.transient == 1 and e.result.stop == "", kw
        assert "구단주" not in e.result.error, "오류 글에 닉네임"


def test_failure_keeps_previous_lineup():
    with env(n=1) as e:
        old = NOW - timedelta(days=6)
        e.put(901, old, lineup_at=old, at=old, body=b"L" * 66)
        e.api.who["구단주1"] = err("OPENAPI00004", 400)
        e.run()
        r = e.rows()[901]
        assert (r["fail"], r["checked_at"]) == ("gone", ts(NOW))
        assert (r["lineup"], r["match_at"], r["lineup_at"]) == (b"L" * 66, ts(old), ts(old)), "실패가 앞 선발을 덮었다"
        e.api.who.clear()
        e.now = NOW + timedelta(days=2)
        e.snap([1], e.now - timedelta(hours=1))
        assert e.run().due == 1
        r = e.rows()[901]
        assert r["fail"] is None and r["lineup_at"] == ts(e.now), "다시 받았는데 실패가 남았다"


def test_streaks_stop_without_saving():
    # 막지 않았다면 여섯 명 모두 실패로 적힌다(전제: 한 명짜리 test_one_person_rules 가 그렇게 적는다)
    for kind, kw, stop in (("gone", "who", "gone"), ("format", "lists", "format"), ("empty", "lists", "empty")):
        with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
            val = {"gone": err("OPENAPI00004", 400), "format": {}, "empty": []}[kind]
            setattr(e.api, kw, {f"구단주{i}": val for i in range(1, 7)})
            r = e.run()
            assert r.stop == stop and r.saved == 0 and e.rows() == {}, (kind, r)
            assert e.meta(sc.FORMAT_DAY) == (DAY if kind == "format" else None), kind
            n = len(e.api.calls)
            assert n == 4 * (1 if kind == "gone" else 2), "4명(시험용)에서 멈추지 않았다"
            e.run()
            assert (len(e.api.calls) > n) == (kind != "format"), "응답 꼴로 멈춘 날 다시 물었다"


def test_streak_under_limit_saves_and_good_result_flushes():
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        e.api.who = {f"구단주{i}": err("OPENAPI00004", 400) for i in (1, 2, 3)}
        r = e.run()
        assert r.stop == "" and r.gone == 3 and r.ok == 3
        assert sorted(sn for sn, x in e.rows().items() if x["fail"] == "gone") == [901, 902, 903], "20명 미만인데 안 적었다"


def test_book_rules_by_hand():
    saved = []
    with env(n=0):
        b = sc._Book(lambda t, g: saved.append((t.profile_sn, g.kind)))
        T = lambda i, p=None: sc.Target(i, "n", p)  # noqa: E731
        G, N, EMPTY, OK, F = sc.Got("gone"), sc.Got("nogame"), sc.Got("nogame", empty=True), sc.Got("ok", 1, b""), sc.Got("format")
        assert b.add(T(1), G) == "" and b.add(T(2), EMPTY) == "" and b.add(T(3), F) == "" and saved == [], "보류하지 않았다"
        assert b.add(T(4), N) == "" and saved == [(1, "gone"), (3, "format"), (2, "nogame"), (4, "nogame")], saved   # 빈 목록 아닌 nogame 이 푼다
        saved.clear()
        for i in range(3):
            assert b.add(T(10 + i), G) == ""
        assert b.add(T(20), EMPTY) == "" and b.add(T(21), F) == "", "다른 종류의 실패는 이어짐을 풀지 않는다"
        assert b.add(T(30, "gone"), G) == "" and saved == [(30, "gone")], "지난번에도 같은 실패였던 사람을 셌다"
        assert b.add(T(31, "format"), G) == "gone" and saved == [(30, "gone")], "네 번째에 멈추지 않았다 · 다른 실패였던 사람은 센다"
        b.flush()
        assert saved == [(30, "gone")], "버린 보류가 저장됐다"
        for i in range(3):
            b.add(T(40 + i), EMPTY)
        b.add(T(50), OK)
        assert b.add(T(51), EMPTY) == "", "정상 뒤 다시 0 부터"
        assert (50, "ok") in saved and (40, "nogame") in saved


def test_nogame_after_old_match_does_not_count_as_empty():
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        e.api.details = {f"구단주{i}#0": match(f"ou-구단주{i}", NOW - timedelta(days=30)) for i in range(1, 7)}
        r = e.run()
        assert r.stop == "" and r.nogame == 6, f"오래 쉰 사람들이 이어짐에 걸렸다 {r}"


def test_key_and_maintenance_codes_stop_at_once():
    for code, stop, mark in (("OPENAPI00005", "key", True), ("OPENAPI00010", "down", False), ("OPENAPI00011", "down", False)):
        with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
            e.api.who = {f"구단주{i}": err(code, 400) for i in range(1, 7)}
            r = e.run()
            assert (r.stop, r.saved, len(e.api.calls)) == (stop, 0, 1), (code, r, len(e.api.calls))
            assert e.meta(sc.KEY_DAY) == (DAY if mark else None), code
            assert not e.hit(store.BUDGET_SQUAD) and not e.hit(store.BUDGET_OPENAPI)
            e.api.who.clear()
            assert (e.run().ok == 6) is (not mark), "점검은 다음 틱에 다시 · 키 거절은 그날 쉼"


def test_held_rows_are_dropped_when_maintenance_begins():
    """점검이 시작되는 순간의 00004 몇 개를 사라진 사람으로 3일 적지 않는다."""
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        e.api.who = {f"구단주{i}": err("OPENAPI00004", 400) for i in range(1, 7)}
        e.api.by_n = {3: err("OPENAPI00010", 503)}
        r = e.run()
        assert r.stop == "down" and e.rows() == {}, (r, e.rows())


def test_transient_streak_and_per_person_tries():
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        e.api.who = {f"구단주{i}": err("", 500) for i in range(1, 7)}
        r = e.run()
        assert (r.stop, r.transient, len(e.api.calls)) == ("error", 5, 5) and e.rows() == {}, r
        assert not e.hit(store.BUDGET_SQUAD), "연결 실패로 그날을 멈췄다"
        e.run()
        n = len(e.api.calls)
        r = e.run()   # 두 번(시험용 하루 한도)씩 물은 사람은 오늘 더 안 묻는다
        assert len(e.api.calls) - n <= 2 and r.due <= 2, (r, len(e.api.calls) - n)
        assert e.run(tries={}).due == 6, "날이 바뀌면(새 표) 다시"
    with env(n=12) as e, _cfg(SQUAD_CMP_WORKERS=1):   # 사이에 정상이 오면 이어짐이 아니다 — 여섯 번 실패해도(한도 5) 끝까지 간다
        order = [t.nickname for t in sc.targets([{"profile_sn": 900 + i, "nickname": f"구단주{i}"} for i in range(1, 13)], {}, NOW)]
        e.api.who = {n: err("", 500) for n in order[::2]}
        r = e.run()
        assert r.stop == "" and r.transient == 6 and r.ok == 6, r


def test_one_429_waits_then_continues_second_stops():
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        e.api.by_n = {2: err(QUOTA_CODE, 429)}
        r = e.run()
        assert r.stop == "" and r.ok == 6 and sum(e.clock.slept) == 9, (r, e.clock.slept)
        assert not e.hit(store.BUDGET_SQUAD) and e.used() == 19, "쉰 뒤 다시 낸 요청도 센다"
    # 켠 뒤 누적이 적다 → 개발 키
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        e.api.by_n = {2: err(QUOTA_CODE, 429), 3: err(QUOTA_CODE, 429)}
        r = e.run()
        assert r.stop == "quota" and r.devkey and e.hit(store.BUDGET_SQUAD) and not e.hit(store.BUDGET_OPENAPI), r
        assert e.run().stop == "marked"
    # 누적이 많다 → 서비스 키의 진짜 한도. 다른 로더도 쉰다
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1, SQUAD_CMP_SELFCHECK_REQ=4):
        e.api.by_n = {5: err(QUOTA_CODE, 429), 6: err(QUOTA_CODE, 429)}
        r = e.run()
        assert r.stop == "quota" and not r.devkey and e.hit(store.BUDGET_SQUAD) and e.hit(store.BUDGET_OPENAPI), r


def test_devkey_judgement_uses_rows_when_counter_was_lost():
    """복원으로 누적이 0 이 돼도 이미 받은 사람 수로 서비스 키를 알아본다(3회차 [중]6)."""
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        for i in range(40):
            e.put(5000 + i, NOW, lineup_at=NOW, at=NOW)   # 40 × 3 = 120 ≥ 100
        e.api.by_n = {1: err(QUOTA_CODE, 429), 2: err(QUOTA_CODE, 429)}
        r = e.run()
        assert r.stop == "quota" and not r.devkey, "서비스 키를 개발 키로 오판했다"


def test_counter_restarts_when_turned_on_again_and_survives_reset():
    with env() as e:
        e.run()
        assert store.squad_req(e.conn, ON) == 18 and store.squad_req(e.conn, "2026-11-25T11:00:00") == 0
        e.conn.close()
        assert storage.reset_db(config.DB_PATH) == "ok"
        e.conn = store.open_db(config.DB_PATH)
        assert store.squad_req(e.conn, ON) == 18, "내 기록 전부 지우기가 누적을 지웠다(서비스 키의 429 를 개발 키로 읽는다)"
        e.set(on="2026-11-25T11:00:00")
        sc.purge(config.squads_db_path())
        e.run()
        assert store.squad_req(e.conn, "2026-11-25T11:00:00") == 18, "옛 키의 몫이 섞였다"
        store._put_state(e.conn, {store.SQUAD_REQ_KEY: "엉망"})
        e.conn.commit()
        assert store.squad_req(e.conn, ON) == 0


def test_daily_cap_stops_and_resumes_next_day():
    with env() as e, _cfg(SQUAD_CMP_DAILY_REQ=8, SQUAD_CMP_WORKERS=1):
        r = e.run()
        assert r.stop == "limit" and r.ok == 2 and e.used() == 8, (r, e.used())   # 막지 않았다면 18
        assert not sc.should_run(config.DB_PATH, config.squads_db_path(), NOW), "한 명분도 안 남았는데 또 띄운다"
        e.now = NOW + timedelta(hours=12, minutes=1)   # 다음 날
        assert sc.should_run(config.DB_PATH, config.squads_db_path(), e.now)
        assert e.run().ok == 2 and len(e.rows()) == 4, "이어 받지 않았다"


def test_concurrency_serial_first_then_workers():
    with env(n=30) as e, _cfg(SQUAD_CMP_WORKERS=3, SQUAD_CMP_SERIAL_REQ=30):
        e.api.delay = 0.01
        peaks = {}

        def hook(kind, n):
            peaks[n] = e.api.inflight
        e.api.hook = hook
        e.run()
        assert max(v for n, v in peaks.items() if n <= 30) == 1, "첫 요청들이 겹쳤다"
        assert 2 <= e.api.peak <= 3, e.api.peak


def test_cancel_keeps_whole_people_only():
    with env() as e, _cfg(SQUAD_CMP_WORKERS=1):
        stop = {"v": False}
        e.api.hook = lambda kind, n: stop.__setitem__("v", n >= 8)
        r = e.run(cancel=lambda: stop["v"])
        assert r.stop == "cancel" and r.ok == len(e.rows()) == 2, (r, len(e.rows()))
        e.api.hook = None
        assert e.run().ok == 4 and len(e.rows()) == 6


def test_rank_db_is_closed_before_requests():
    with env() as e:
        seen = []
        e.api.hook = lambda kind, n: seen.append(_can_delete(config.RANK_DB_PATH)) if n == 1 else None
        e.run()
        assert seen == [True], "받는 동안 rank.db 를 열고 있다(지우기가 막힌다)"


def _can_delete(p: Path) -> bool:
    if os.name != "nt":
        return True
    t = p.with_name("renamed.db")
    try:
        p.rename(t)
        t.rename(p)
        return True
    except OSError:
        return False


# ── 정리 · 지우기 · 깨짐 ─────────────────────────────────────────────────

def test_prune_boundaries_use_two_columns():
    with env(n=0) as e:
        k, s = timedelta(days=11), timedelta(seconds=1)
        e.put(1, NOW - k, lineup_at=NOW - k, at=NOW)                       # 딱 11일 — 둘 다 남는다
        e.put(2, NOW, fail="gone", lineup_at=NOW - k - s, at=NOW)          # 실패가 이어지는 사람: 줄은 남고 선발만 비운다
        e.put(3, NOW - k - s, lineup_at=NOW - k - s, at=NOW)               # 더 안 묻는 사람: 줄째
        e.put(4, NOW + timedelta(days=1, seconds=1))                       # 미래
        e.put(5, NOW, lineup_at=NOW + timedelta(days=2), at=NOW)
        assert sc.prune(config.squads_db_path(), NOW) == 4
        rows = e.rows()
        assert set(rows) == {1, 2, 5} and rows[1]["lineup"], rows.keys()
        for sn in (2, 5):
            assert rows[sn]["lineup"] is rows[sn]["match_at"] is rows[sn]["lineup_at"] is None, sn
        assert rows[2]["fail"] == "gone"
        assert sc.prune(config.squads_db_path(), NOW) == 0
    with env() as e:   # 실행을 시작할 때도 — 도는 동안(첫 번은 십여 분) 틱의 정리가 쉬므로
        e.put(5, NOW - timedelta(days=12))
        e.run()
        assert set(e.rows()) == {901, 902, 903, 904, 905, 906}, "1만 위 밖으로 나간 옛 줄이 남았다"


def test_purge_leaves_no_bytes_and_creates_no_file():
    with env() as e:
        assert sc.purge(config.squads_db_path()) is False and not config.squads_db_path().exists(), "없는 파일을 만들었다"
        assert sc.prune(config.squads_db_path(), NOW) == 0 and sc.status_text(config.squads_db_path(), NOW) == ""
        assert not config.squads_db_path().exists()
        e.run()
        body = e.rows()[901]["lineup"]
        assert body[:12] in all_bytes(), "전제: 받은 선발이 파일에 있다"
        c = sc.open_db(config.squads_db_path())
        sc._meta_set(c, sc.KEY_DAY, DAY)
        c.close()
        idle = sc.open_db(config.squads_db_path())   # 다른 연결(다른 실행본·작업 스레드)이 열려 있어 닫을 때 저절로 안 비워진다
        idle.execute("SELECT COUNT(*) FROM squads").fetchone()
        try:
            assert sc.purge(config.squads_db_path()) is True
            blob = all_bytes()
            assert body[:12] not in blob and body[6:18] not in blob, "지운 선발의 바이트가 남았다"
            assert sc.db_files(config.squads_db_path())[1].stat().st_size == 0, "WAL 을 안 비웠다"
        finally:
            idle.close()
        assert e.meta(sc.KEY_DAY) is None, "그날 멈춤 표시가 남았다 — 키를 바꿔 다시 켜도 그날 안 돈다"
        assert sc.purge(config.squads_db_path()) is False, "지울 것이 없는데 지웠다고 한다"


def test_purge_retries_while_wal_is_blocked():
    """다른 연결이 읽는 중이면 WAL 이 안 비워진다 — 다음 틱이 다시 지워야 한다(3회차 [중]11)."""
    with env() as e:
        e.run()
        body = e.rows()[901]["lineup"]
        reader = sqlite3.connect(config.squads_db_path(), isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM squads").fetchone()
        try:
            sc.purge(config.squads_db_path(), 0.2)
            left = body[:12] in all_bytes()
        finally:
            reader.rollback()
        assert left and sc.has_data(config.squads_db_path()), "옛 바이트가 남았는데 지울 것이 없다고 한다"
        reader.close()
        sc.purge(config.squads_db_path())
        assert body[:12] not in all_bytes()


def test_corrupt_file_is_dropped_but_lock_is_not():
    with env() as e:
        e.run()
        c = sqlite3.connect(config.squads_db_path(), isolation_level=None)
        c.execute("BEGIN IMMEDIATE")
        try:
            assert sc.drop_if_corrupt(config.squads_db_path()) is False and config.squads_db_path().exists(), "잠긴 파일을 지웠다"
        finally:
            c.rollback()
            c.close()
        config.squads_db_path().write_bytes(b"not a database" * 300)
        for p in sc.db_files(config.squads_db_path())[1:]:
            p.unlink(missing_ok=True)
        r = e.run()
        assert r.stop == "corrupt" and not config.squads_db_path().exists(), r
        assert e.run().ok == 6, "다시 받지 않는다"


def test_status_text_lines():
    with env() as e:
        e.run()
        assert sc.status_text(config.squads_db_path(), NOW) == "선발 6명을 받았습니다"
        c = sc.open_db(config.squads_db_path())
        sc._meta_set(c, sc.KEY_DAY, DAY)
        sc._meta_set(c, sc.FORMAT_DAY, DAY)
        c.close()
        t = sc.status_text(config.squads_db_path(), NOW)
        assert "API 키를 받지 않아" in t and "형식이 달라" in t
        assert "형식" not in sc.status_text(config.squads_db_path(), NOW + timedelta(days=1)), "어제 사유를 오늘 보인다"
        e.set(on="", off="devkey")
        assert sc.OFF_REASON_TEXT["devkey"] in sc.status_text(config.squads_db_path(), NOW)
        e.set(on=ON, off="devkey")
        assert sc.OFF_REASON_TEXT["devkey"] not in sc.status_text(config.squads_db_path(), NOW), "켜졌는데 옛 사유"


# ── 예약 · 셸 · 창 ──────────────────────────────────────────────────────

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

    def __init__(self, tries):
        self.tries, self.done = tries, _Sig()
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

    def finish(self, res=None):
        self.running = False
        self.done.emit(res or sc.Result(stop="cancel"))


@contextmanager
def sched_env(**kw):
    keep = app_main.SquadCmpLoader
    app_main.SquadCmpLoader, FakeLoader.made = FakeLoader, []
    app_main._GAME["on"] = False
    try:
        with env(**kw) as e:
            e.s = app_main.SquadCmpScheduler(now_fn=lambda: e.now)
            e.offs, e.purges = [], []
            e.s.switched_off.connect(e.offs.append)
            e.s.purged.connect(lambda: e.purges.append(1))
            yield e
    finally:
        app_main.SquadCmpLoader = keep
        app_main._GAME["on"] = False


@contextmanager
def _cfg(**kw):
    keep = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in keep.items():
            setattr(config, k, v)


def test_shell_runs_without_window_and_quit_stops():
    with sched_env() as e:
        config.SQUAD_CMP_TICK_S, config.SQUAD_CMP_FIRST_S = 7, 3
        s = app_main.SquadCmpScheduler(now_fn=lambda: e.now)
        assert (s._timer.interval(), s._first.interval(), s._first.isSingleShot()) == (7000, 3000, True)
        rank = app_main.RankCollectScheduler()
        sh = tray.AppShell(_app, squad_sched=s, sched=rank)
        sh.app = type("A", (), {"quit": lambda self: None, "windowIcon": lambda self: _app.windowIcon()})()
        rank.start = lambda *a, **k: None
        sh.start(tray_mode=True)
        assert sh.window is None and s._timer.isActive() and s._first.isActive(), "셸이 예약을 안 켰다"
        rank.outcome.emit(rankcollect.Outcome("failed"))
        assert FakeLoader.made == [], "실패한 회차에 시작했다"
        rank.outcome.emit(rankcollect.Outcome("ok"))
        assert len(FakeLoader.made) == 1, "수집 회차가 끝났는데 바로 안 받는다(창 없이)"
        s.tick()
        assert len(FakeLoader.made) == 1, "도는 중에 하나 더 띄웠다"
        sh._tray_timer.stop()
        rank.shutdown = lambda *a, **k: []
        sh.quit_app()
        assert FakeLoader.made[0].cancelled and not s._timer.isActive() and not s._first.isActive()
        FakeLoader.made[0].finish()
        s.tick()
        assert len(FakeLoader.made) == 1, "끝내는 중에 다시 시작했다"
    with sched_env() as e:
        e.s.tick()
        assert e.s.shutdown(fast=True) == [FakeLoader.made[0]] and not FakeLoader.made[0].terminated
        assert app_main.SquadCmpScheduler().shutdown(fast=True) == []
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    main = ast.get_source_segment(src, next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "main"))
    assert "squad_sched=SquadCmpScheduler()" in main


def test_tick_needs_every_gate():
    cases = [("토글", dict(on="")), ("동의", dict(notice_ok=V - 1)), ("웹", dict(web=False)), ("수집", dict(collect=False)),
             ("키", dict(key="")), (".env 토글", dict(disk={config.SQUAD_CMP_VAR: ""})),
             (".env 웹", dict(disk={config.WEB_DATA_VAR: 0})), (".env 수집", dict(disk={config.RANK_COLLECT_VAR: 0})),
             (".env 동의", dict(disk={config.NOTICE_VAR: V - 1}))]
    for name, kw in cases:
        with sched_env() as e:
            e.set(**kw)
            e.s.tick()
            assert FakeLoader.made == [], name
            e.set()
            e.s.tick()
            assert len(FakeLoader.made) == 1, f"{name} — 다시 켜면 시작"
    with sched_env() as e:
        app_main._GAME["on"] = True
        e.s.tick()
        assert FakeLoader.made == [], "게임 중"
        app_main._GAME["on"] = False
        e.s.resume()
        assert len(FakeLoader.made) == 1, "게임이 끝났는데 안 잇는다"
        e.s.pause()
        assert FakeLoader.made[0].cancelled
    with sched_env() as e:
        app_main.CLEARING.add("fifa")
        try:
            e.s.tick()
            assert FakeLoader.made == [], "지우는 동안 시작했다"
        finally:
            app_main.CLEARING.discard("fifa")
        store.budget_mark_429(e.conn, DAY, store.BUDGET_OPENAPI)
        e.s.tick()
        assert FakeLoader.made == [], "그날 429"
    with sched_env() as e:   # 게임 길 배선
        from types import SimpleNamespace
        e.s.tick()
        app_main.on_game_changed(SimpleNamespace(sched=None, owner_sched=None, card_sched=None, squad_sched=e.s, window=None), True)
        assert FakeLoader.made[0].cancelled


def test_turning_off_purges_every_way():
    ways = [("토글", dict(on="")), ("수집", dict(collect=False)), ("웹", dict(web=False)), ("동의", dict(notice_ok=V - 1)),
            ("키", dict(key="")), (".env 손 수정", dict(disk={config.SQUAD_CMP_VAR: ""}))]
    for name, kw in ways:
        with sched_env() as e:
            e.run()
            body = e.rows()[901]["lineup"][:12]
            e.s.sync()
            assert len(e.rows()) == 6 and not e.purges, "대조군 — 켜져 있으면 안 지운다"
            e.set(**kw)
            e.s.sync()
            assert e.rows() == {} and body not in all_bytes() and e.purges == [1], name
            e.s.sync()
            assert e.purges == [1], "지운 뒤에도 매번 지웠다고 알린다"


def test_purge_waits_for_the_running_loader():
    with sched_env() as e:
        e.run()
        e.put(950, NOW - timedelta(days=9))
        e.s.tick()
        ld = FakeLoader.made[0]
        e.set(on="")
        e.s.sync()
        assert ld.cancelled and e.rows(), "도는 로더의 저장과 겹쳐 지웠다"
        ld.finish()
        assert e.rows() == {} and e.purges == [1], "로더가 끝났는데 안 지웠다"
    with sched_env() as e:   # 끝내는 중이면 다음 실행의 첫 틱이
        e.run()
        e.put(950, NOW - timedelta(days=9))
        e.s.tick()
        e.set(on="")
        e.s.sync()
        e.s.shutdown(fast=True)
        FakeLoader.made[0].finish()
        s2 = app_main.SquadCmpScheduler(now_fn=lambda: e.now)
        s2.tick()
        assert e.rows() == {}


def test_unknown_state_neither_collects_nor_purges_but_prunes():
    for name in ("env", "key"):
        with sched_env() as e:
            e.run()
            e.put(950, NOW - timedelta(days=12), lineup_at=NOW - timedelta(days=12), at=NOW)
            keep = config._read_env_disk
            if name == "env":
                config._read_env_disk = lambda: None
            else:
                config.KEY_STATE, config.API_KEY = "fail", ""
            try:
                e.s.tick()
                e.s.sync()
                assert FakeLoader.made == [] and set(e.rows()) == {901, 902, 903, 904, 905, 906}, \
                    f"{name}: 14일 정리는 돌고 나머지는 그대로여야 한다 {sorted(e.rows())}"
            finally:
                config._read_env_disk = keep
    with sched_env() as e:   # 꺼져 있어도(설정과 무관) 정리는 전부 지우기가 대신한다 · 켜져 있으면 정리만
        e.put(950, NOW - timedelta(days=12))
        e.s._start_loader = lambda: None
        e.s.tick()
        assert e.rows() == {}


def test_devkey_switches_off_and_purges():
    with sched_env() as e:
        e.s.tick()
        e.run()
        FakeLoader.made[0].finish(sc.Result(stop="quota", devkey=True))
        assert e.offs == ["devkey"] and config.SQUAD_CMP_ON == "" and config.SQUAD_CMP_OFF == "devkey" and e.rows() == {}
        assert e.env_line(config.SQUAD_CMP_VAR) == "" and e.env_line(config.SQUAD_CMP_OFF_VAR) == "devkey"
    with sched_env() as e:   # .env 를 못 쓰면 켜진 채 · 그날 표시가 막는다
        e.s.tick()
        e.run()
        keep = config.set_squad_cmp

        def boom(*a):
            raise OSError
        config.set_squad_cmp = boom
        try:
            FakeLoader.made[0].finish(sc.Result(stop="quota", devkey=True))
        finally:
            config.set_squad_cmp = keep
        assert e.offs == [] and len(e.rows()) == 6
        store.budget_mark_429(e.conn, DAY, store.BUDGET_SQUAD)
        e.s.tick()
        assert len(FakeLoader.made) == 1
    with sched_env() as e:
        e.s.tick()
        FakeLoader.made[0].finish(sc.Result(stop="quota"))
        assert e.offs == [] and config.SQUAD_CMP_ON == ON, "서비스 키를 껐다"


def test_tries_table_is_per_day():
    with sched_env() as e:
        e.s.tick()
        FakeLoader.made[0].tries[901] = 3
        FakeLoader.made[0].finish()
        e.s.tick()
        assert FakeLoader.made[1].tries == {901: 3}, "틱마다 표가 비면 하루 3번 한도가 없다"
        FakeLoader.made[1].finish()
        e.now = NOW + timedelta(days=1)
        e.s.tick()
        assert FakeLoader.made[2].tries == {}


def test_key_change_turns_off():
    with env() as e:
        config.save_api_key("k")
        assert config.SQUAD_CMP_ON == ON, "같은 키를 다시 저장했는데 껐다"
        config.save_api_key("new")
        assert config.SQUAD_CMP_ON == "" and config.SQUAD_CMP_OFF == "key" and e.env_line(config.SQUAD_CMP_OFF_VAR) == "key"
        before = datetime.now().isoformat(timespec="seconds")
        config.set_squad_cmp(True)
        assert before <= config.SQUAD_CMP_ON and config.SQUAD_CMP_OFF == "" and e.env_line(config.SQUAD_CMP_VAR) == config.SQUAD_CMP_ON


def test_halt_for_delete_stops_before_rank_db_is_removed():
    with sched_env() as e:
        config.SQUAD_CMP_HALT_WAIT_MS = 123
        e.s.tick()
        ld = FakeLoader.made[0]
        keep = config._read_env_disk
        config._read_env_disk = lambda: None   # .env 를 못 읽어도 멈춤은 한다
        try:
            e.s.halt_for_delete()
        finally:
            config._read_env_disk = keep
        assert ld.cancelled and ld.waits == [123]
    with sched_env() as e:
        order = []
        sq = type("S", (), {"halt_for_delete": lambda self: order.append("halt")})()
        keep = rankcollect.delete_db
        rankcollect.delete_db = lambda *a, **k: order.append("delete") or True
        try:
            app_main.clear_rank_records(None, None, None, sq)
        finally:
            rankcollect.delete_db = keep
        assert order == ["halt", "delete"], order
        p = _Parent()
        p._squad_sched.halt_for_delete = lambda: order.append("button")
        dlg = app_main.AboutDialog(p)
        dlg._ask_clear = lambda k: "all"
        dlg._on_clear_rank()
        dlg.deleteLater()
        assert "button" in order, "[수집 기록 지우기] 가 예약에 안 닿는다"


def test_real_loader_is_direct_and_hides_names():
    with env() as e:
        seen = {}

        def fake(squads, fifa, rank_open, factory, **kw):
            seen.update(squads=squads, fifa=fifa, rank=rank_open, api=factory(), kw=kw)
            return sc.Result(ok=3)
        keep = sc.collect, app_main.FCOnlineAPI
        config.SERVER_URL = "http://127.0.0.1:9"
        try:
            app_main.FCOnlineAPI = lambda key, *a, **k: ("api", key, a, k)
            sc.collect = fake
            tries = {1: 1}
            w = app_main.SquadCmpLoader(tries)
            got = []
            w.done.connect(got.append)
            w.cancel()
            w.run()
            assert got[0].ok == 3 and seen["squads"] == config.squads_db_path() and seen["fifa"] == config.DB_PATH
            assert seen["rank"] is rankcollect.open_rank_db_ro and seen["kw"]["cancel"]() and seen["kw"]["tries"] is tries
            assert seen["api"] == ("api", "k", (), {}), "중앙 서버 주소를 타거나 캐시 폴더를 준다"

            def boom(*a, **k):
                raise ValueError("구단주닉네임")
            sc.collect = boom
            w.run()
            assert got[1].error == "ValueError" and got[1].stop == "error"
        finally:
            sc.collect, app_main.FCOnlineAPI = keep
        real = nexon_api.FCOnlineAPI("k")
        urls = []

        class S:
            def get(self, url, **kw):
                urls.append(url)
                raise nexon_api.requests.ConnectionError("막음")
        for name in ("_session", "session"):
            if hasattr(real, name):
                setattr(real, name, S())
        try:
            real.get_raw(nexon_api.EP_ID, nickname="x")
        except NexonAPIError:
            pass
        assert len(urls) == 1 and urls[0].startswith("https://open.api.nexon.com"), f"직접·재시도 없음이 아니다 {urls}"
    assert app_main.SquadCmpLoader.WRITES == {app_main.STORE_SQUADS, app_main.STORE_FIFA}


def test_real_thread_end_to_end():
    """진짜 QThread 로 한 번 — 신호가 화면 스레드로 오고 파일이 찬다."""
    with env() as e:
        keep = app_main.FCOnlineAPI
        app_main.FCOnlineAPI = lambda key: e.api
        real_now = datetime.now()
        e.snap(range(1, 7), real_now - timedelta(hours=1))
        e.api.details = {f"구단주{i}#0": match(f"ou-구단주{i}", real_now - timedelta(hours=2), seed=i) for i in range(1, 7)}
        s = app_main.SquadCmpScheduler()
        got = []
        s.finished.connect(got.append)
        try:
            s.tick()
            end = time.time() + 20
            while not got and time.time() < end:
                _app.processEvents()
                time.sleep(0.01)
        finally:
            app_main.FCOnlineAPI = keep
            s.shutdown()
        assert got and got[0].ok == 6 and len(e.rows()) == 6, got


def test_storage_kind_and_measure():
    with env() as e:
        assert "squads" in storage.KINDS and app_main.MainWindow.CLEAR_STORES["squads"] == {app_main.STORE_SQUADS}
        assert storage.clear_squads(config.squads_db_path()) == 0 and not config.squads_db_path().exists()
        e.run()
        z = storage.measure(config.DB_PATH, config.RANK_DB_PATH, e.dir / "c", squads_path=config.squads_db_path())
        base = storage.measure(config.DB_PATH, config.RANK_DB_PATH, e.dir / "c")
        assert z.bytes["squads"] > 0 and base.bytes["squads"] == 0 and z.bytes["all"] == base.bytes["all"] + z.bytes["squads"]
        w = app_main.ClearWorker("squads", [])
        assert w._clear().state == "ok" and e.rows() == {}, "[저장 공간] 작업자가 못 지운다"
        e.run()
        e.conn.close()
        config.CACHE_DIR = e.dir / "c"
        w = app_main.ClearWorker("all", [])
        assert w._clear().state == "ok" and e.rows() == {}, "내 기록 전부가 선발을 남겼다"
        e.conn = store.open_db(config.DB_PATH)
        e.set()
        e.run()
        assert storage.clear_squads(config.squads_db_path()) == 1 and e.rows() == {} and config.SQUAD_CMP_ON == ON, "토글까지 껐다"


def test_backup_has_no_squads():
    with env() as e:
        e.run()
        import zipfile
        out = e.dir / "b.zip"
        res = storage.backup(config.DB_PATH, e.dir / "settings.ini", out)
        assert out.exists(), res
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        assert "fifa.db" in names and not any("squad" in n for n in names), names
        body = e.rows()[901]["lineup"]
        assert body[:12] not in out.read_bytes()


class _Parent(QWidget):
    def __init__(self):
        super().__init__()
        self.synced = []
        self._squad_sched = type("S", (), {"sync": lambda _s, start=False: self.synced.append(start),
                                           "halt_for_delete": lambda _s: None})()


def test_about_dialog_toggle():
    with env() as e:
        e.set(on="")
        p = _Parent()
        dlg = app_main.AboutDialog(p)
        assert dlg.chk_squad.isVisibleTo(dlg) and dlg.chk_squad.isEnabled() and not dlg.chk_squad.isChecked()
        assert dlg.chk_squad.text() == notice.SQUAD_CMP_CHECK
        dlg.chk_squad.setChecked(True)
        assert config.SQUAD_CMP_ON and p.synced == [True] and "40,000" not in dlg.lb_msg.text() and "10,000" in dlg.lb_msg.text()
        dlg.chk_squad.setChecked(False)
        assert config.SQUAD_CMP_ON == "" and p.synced == [True, False]
        keep = config.set_squad_cmp

        def boom(*a):
            raise OSError("읽기 전용")
        config.set_squad_cmp = boom
        try:
            dlg.chk_squad.setChecked(True)
        finally:
            config.set_squad_cmp = keep
        assert not dlg.chk_squad.isChecked() and "저장하지 못했습니다" in dlg.lb_msg.text()
        dlg.deleteLater()
        for kw in (dict(collect=False), dict(web=False), dict(notice_ok=V - 1), dict(key="")):
            e.set(on=ON, **kw)
            dlg = app_main.AboutDialog(p)
            assert not dlg.chk_squad.isEnabled() and not dlg.chk_squad.isChecked() and dlg.lb_squad.text(), kw
            dlg.deleteLater()
        e.set()
        config.SQUAD_CMP_NOTICE_VERSION = 99   # 조각 ①② — 줄째 숨긴다
        dlg = app_main.AboutDialog(p)
        assert not dlg.chk_squad.isVisibleTo(dlg) and not dlg.lb_squad.isVisibleTo(dlg) and not dlg._storage_btn["squads"].isVisibleTo(dlg)
        assert not dlg._storage_btn["dist"].isHidden() and dlg._storage_btn["squads"].isHidden(), "다른 줄까지 숨겼다"
        dlg.deleteLater()


def test_window_paths_reach_the_scheduler():
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    meths = {f.name: ast.get_source_segment(src, f) for f in cls.body if isinstance(f, ast.FunctionDef)}
    for name in ("sync_ranker_pick_data", "_ask_new_key"):
        assert "self._sync_squad_cmp()" in meths[name], name
    assert "self._squad_sched)" in meths["clear_storage"]
    got = []
    sh = tray.AppShell(_app, squad_sched="S")
    sh.attach_window(type("W", (), {"attach_squad_sched": lambda self, x: got.append(x)})())
    assert got == ["S"]


# ── 구단주 분포 쪽이 그대로인가 ─────────────────────────────────────────────

def test_gate_defaults_are_owner_dist():
    import ownerdist
    with env(n=0) as e:
        g = ownerdist._Gate(config.DB_PATH, lambda: False, e.clock.sleep, e.clock.now, lambda: DAY, 10)
        try:
            g.call(lambda: 1)
            assert store.budget_used(e.conn, DAY, store.BUDGET_OWNER_DIST) == 1 and e.used() == 0

            def boom():
                raise err("OPENAPI00005", 400)
            try:
                g.call(boom)
                raise AssertionError
            except NexonAPIError:
                pass
            assert g.reason == "", "구단주 분포가 새 멈춤 코드를 쓴다"
        finally:
            g.close()


def test_notice_gate_constant_is_json_safe():
    """trade_state 의 누적 꼴 — 다른 열쇠를 건드리지 않는다."""
    with env(n=0) as e:
        store.set_trade_state(e.conn, key_fp="fp")
        assert store.squad_take(e.conn, DAY, 2, ON) == 1 and store.squad_take(e.conn, DAY, 2, ON) == 2
        assert store.squad_take(e.conn, DAY, 2, ON) is None and store.squad_req(e.conn, ON) == 2, "상한인데 누적이 올랐다"
        v = dict(e.conn.execute("SELECT key, value FROM trade_state").fetchall())
        assert v["key_fp"] == "fp" and json.loads(v[store.SQUAD_REQ_KEY]) == {"on": ON, "n": 2}


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    only = sys.argv[1:]
    failed = 0
    for t in tests:
        if only and not any(o in t.__name__ for o in only):
            continue
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
