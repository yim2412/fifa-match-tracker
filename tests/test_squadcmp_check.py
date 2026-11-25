"""122단계 대조 준비 도구(tools/squadcmp_check) — 지어낸 fifa.db·squads.db, 가짜 시뮬레이터(네트워크 없음).
`python tests/test_squadcmp_check.py`"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import watchdog  # noqa: E402 — 109 J8 · config 보다 먼저(FIFA_NO_CRED)
import config  # noqa: E402
import gameguard  # noqa: E402
import playerinfo  # noqa: E402
import squadcmp  # noqa: E402
import squadcmp_check as C  # noqa: E402
import store  # noqa: E402

TODAY = datetime(2026, 11, 26).date()
DAY = TODAY.isoformat()
KEYS = ("DB_PATH", "RANK_DB_PATH", "WEB_DATA", "RANK_COLLECT", "NOTICE_ACCEPTED", "SQUAD_CMP_NOTICE_VERSION", "SQUAD_CMP_ON", "API_KEY",
        "KEY_STATE", "SQUAD_CMP_CARD_DAILY_REQ", "SQUAD_CMP_CARD_TICK_REQ", "_default_data_dir")


class E:
    def __init__(self, tmp: Path):
        self.home, self.box = tmp / "home", tmp / "box"   # 진짜 폴더 · 스크래치
        self.home.mkdir()
        self.box.mkdir()
        self.real, self.fifa, self.squads = self.home / "fifa.db", self.box / "fifa.db", self.box / "squads.db"
        self.asked: list[int] = []
        self.waits: list[float] = []
        self.rate_at = 0
        self.hook = None
        store.open_db(self.real).close()

    def run(self, path: Path, sql: str, rows=()) -> None:
        c = store.open_db(path)
        c.executemany(sql, rows) if rows else c.execute(sql)
        c.commit()
        c.close()

    def card(self, path: Path, spid: int, day: str | None, ovr: str = "{}") -> None:
        self.run(path, "INSERT INTO card_extra (spid, pos_ovr, sim_on) VALUES (?, ?, ?)", [(spid, ovr, day)])

    def people(self, *lineups) -> None:
        c = squadcmp.open_db(self.squads)
        t = int(datetime.now().timestamp())
        cols = [r[1] for r in c.execute("PRAGMA table_info(squads)")]
        for i, spids in enumerate(lineups):
            row = {"lineup": squadcmp.pack_lineup([(s, n, 5) for n, s in enumerate(spids)]), "lineup_at": t, "match_at": t, "checked_at": t}
            row[cols[0]] = 100 + i
            c.execute(f"INSERT INTO squads ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        c.commit()
        c.close()

    def sim(self, spid):
        self.asked.append(spid)
        if self.hook:
            self.hook(len(self.asked))
        if len(self.asked) == self.rate_at:
            raise playerinfo.PlayerInfoError("x", "rate")
        return playerinfo.AbilitySim(ovr=1, groups={}, abilities={}, club_options=[(1, "팀")], feature_options=[],
                                     position_ovrs={p: 100 for p in set(config.SIM_POS_OF.values())})

    def fill(self, today=TODAY, cancel=lambda: False):
        return C.fill(self.real, self.squads, self.fifa, today, cancel, fetch_sim=self.sim, wait=self.waits.append, log=lambda s: None)

    def digest(self) -> str:
        return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(self.home.iterdir()) if not p.name.endswith("-shm"))).hexdigest()

    def main(self, *argv, box: Path | None = None, game=False) -> tuple[int, str]:
        keep = os.environ.get("FIFA_DATA_DIR"), gameguard.game_running, sys.stdout
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        try:
            os.environ["FIFA_DATA_DIR"] = str(self.box if box is None else box)
            gameguard.game_running = lambda: game
            with contextlib.redirect_stdout(out):
                rc = C.main(list(argv))
            out.flush()
            return rc, out.buffer.getvalue().decode("utf-8")
        finally:
            gameguard.game_running = keep[1]
            os.environ.pop("FIFA_DATA_DIR", None)
            if keep[0] is not None:
                os.environ["FIFA_DATA_DIR"] = keep[0]


@contextmanager
def env(open_=True):
    keep = {k: getattr(config, k) for k in KEYS}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        e = E(Path(t))
        try:
            config.DB_PATH, config.RANK_DB_PATH = e.fifa, e.box / "rank.db"
            config._default_data_dir = lambda: e.home
            if open_:
                C.open_gate("k")
            yield e
        finally:
            for k, v in keep.items():
                setattr(config, k, v)


def test_folder_must_be_scratch_and_empty_or_marked():
    with tempfile.TemporaryDirectory() as t:
        home = Path(t) / "a" / "home"
        ok = str(Path(t) / "box")
        assert C.folder_why("", home, []) and C.folder_why(str(home), home, []) and C.folder_why(str(home / "x"), home, [])
        assert C.folder_why(str(home.parent), home, []), "진짜 폴더의 위"
        assert C.folder_why(ok, home, ["fifa.db"]), "남의 파일이 든 폴더"
        assert C.folder_why(ok, home, []) == "" and C.folder_why(ok, home, ["fifa.db", C.MARK]) == ""


def test_sync_takes_newer_cards_and_never_writes_real():
    with env() as e:
        e.card(e.real, 1, "2026-11-20", '{"ST": 120}')
        e.card(e.real, 2, "2026-11-20", '{"ST": 121}')
        e.card(e.real, 3, None)
        e.run(e.real, "INSERT INTO card_tries VALUES ('c:9:sim', '2026-11-25', 1), ('p:9', '2026-11-25', 1)")
        e.card(e.fifa, 2, "2026-11-24", '{"ST": 99}')   # 여기서 더 늦게 읽은 카드
        before = e.digest()
        assert C.sync_cards(e.real, e.fifa) == 2, "1 과 3 만 새로 온다"
        assert e.digest() == before, "진짜 폴더가 바뀌었다"
        c = store.open_db(e.fifa)
        got = {r[0]: (r[1], r[2]) for r in c.execute("SELECT spid, pos_ovr, sim_on FROM card_extra")}
        keys = {r[0] for r in c.execute("SELECT key FROM card_tries")}
        c.close()
        assert got[1] == ('{"ST": 120}', "2026-11-20") and got[2] == ('{"ST": 99}', "2026-11-24") and got[3][1] is None
        assert keys == {"c:9:sim"}, keys
        e.run(e.real, "UPDATE card_extra SET sim_on = '2026-11-26', pos_ovr = '{\"ST\": 130}' WHERE spid = 2")
        assert C.sync_cards(e.real, e.fifa) == 1, "앱이 더 늦게 읽은 카드를 안 가져왔다"
        c = store.open_db(e.fifa)
        got = tuple(c.execute("SELECT pos_ovr, sim_on FROM card_extra WHERE spid = 2").fetchone())
        c.close()
        assert got == ('{"ST": 130}', "2026-11-26"), got


def test_fill_daily_cap_and_next_day():
    with env() as e:
        n = C.CAP + 30
        e.people(*[range(1000 + i * 11, 1011 + i * 11) for i in range(n // 11 + 1)])
        before = e.digest()
        r = e.fill()
        assert (r.stop, r.requests, r.done, len(e.asked)) == ("", C.CAP, C.CAP, C.CAP), r
        assert e.waits and set(e.waits) == {config.CARD_BACKFILL_GAP_S} and len(e.waits) == C.CAP - 1
        r = e.fill()
        assert (r.stop, r.requests) == ("cap", 0) and len(e.asked) == C.CAP, "하루 몫을 넘겼다"
        r = e.fill(TODAY + timedelta(days=1))
        assert r.requests == len(set(range(1000, 1000 + (n // 11 + 1) * 11))) - C.CAP and len(set(e.asked)) == len(e.asked)
        assert e.digest() == before, "진짜 폴더에 적었다"


def test_fill_stops_when_app_side_is_blocked():
    kinds = store.card_block_kinds()
    assert len(kinds) > 3
    for kind in (kinds[0], f"{store.BUDGET_CARD}_block"):
        with env() as e:
            e.people(range(1, 12))
            e.run(e.real, "INSERT INTO api_budget (day, kind, attempts, hit_429) VALUES (?, ?, 0, 1)", [(DAY, kind)])
            assert e.fill().stop == "real_blocked" and not e.asked, kind
            assert e.fill(TODAY + timedelta(days=1)).done == 11, "어제의 막힘이 오늘까지 막는다"
    with env() as e:   # 도는 중에 앱이 막혔다
        e.people(range(1, 12))
        e.hook = lambda n: n == 3 and e.run(e.real, "INSERT INTO api_budget (day, kind, attempts, hit_429) VALUES (?, ?, 0, 1)",
                                            [(DAY, "card_backfill_block")])
        assert e.fill().stop == "cancel" and len(e.asked) == 3
    with env() as e:   # 못 읽으면 막힌 것으로
        e.people(range(1, 12))
        assert C.real_blocked(e.home / "none.db", DAY) is True and C.real_blocked(e.real, DAY) is False


def test_fill_own_block_and_cancel():
    with env() as e:
        e.people(range(1, 12))
        e.rate_at = 4
        r = e.fill()
        assert (r.stop, r.blocked, r.done) == ("blocked", True, 3)
        assert e.fill().stop == "blocked" and len(e.asked) == 4, "막힌 날 다시 물었다"
    with env() as e:
        e.people(range(1, 12))
        assert e.fill(cancel=lambda: len(e.asked) >= 2).stop == "cancel" and len(e.asked) == 2


def test_status_counts_people_with_all_cards():
    with env() as e:
        e.people(range(1, 12), range(6, 17))
        for s in range(1, 12):
            e.card(e.fifa, s, "2026-11-20")
        e.card(e.fifa, 12, None)   # 시뮬레이터를 안 읽은 반쪽 카드
        st = C.status(e.squads, e.fifa)
        assert (st["people"], st["full"], st["missing"], st["known"]) == (2, 1, 5, 11), st
        assert "50.0%" in C.status_line(st) and C.status_line({"people": 0, "full": 0, "missing": 0})


def test_main_refusals_send_nothing():
    real_fill, real_prepare = C.fill, C.prepare
    hit = []
    C.fill = lambda *a, **k: hit.append("fill")
    C.prepare = lambda *a, **k: hit.append("prepare")
    try:
        with env(open_=False) as e:
            def said(text, *argv, **kw):
                rc, out = e.main(*argv, **kw)
                assert rc == 2 and text in out and not hit, (rc, out, hit)
            said("진짜 데이터 폴더", "--prepare", box=e.home)
            said("--prepare 를", "--fill")
            said(C.DEV_KEY_VAR, "--prepare")
            said("게임", "--prepare", game=True)
            (e.box / "x").write_text("x", encoding="utf-8")
            said("이 도구가 만든 폴더도 아닙니다", "--prepare")
            said("이 도구가 만든 폴더도 아닙니다", "--delete")
            assert C.delete(e.box) is False and (e.box / "x").exists(), "표시가 없는 폴더를 지웠다"
            (e.box / C.MARK).write_text("", encoding="utf-8")
            said("홈페이지 데이터", "--fill")
            (e.home / ".env").write_text(f"{config.WEB_DATA_VAR}=0\n", encoding="utf-8")
            said("홈페이지 데이터", "--fill")
            said("게임", "--fill", game=True)
    finally:
        C.fill, C.prepare = real_fill, real_prepare


def test_main_fill_and_delete():
    keep = playerinfo.fetch_player_ability, config.CARD_BACKFILL_GAP_S
    with env(open_=False) as e:
        try:
            playerinfo.fetch_player_ability = lambda spid, strong: e.sim(spid)
            config.CARD_BACKFILL_GAP_S = 0
            (e.box / C.MARK).write_text("", encoding="utf-8")
            (e.home / ".env").write_text(f"{config.WEB_DATA_VAR}=1\n{C.DEV_KEY_VAR}=secret-key\n", encoding="utf-8-sig")
            e.card(e.real, 1, "2026-11-20")
            e.people(range(1, 12))
            before = e.digest()
            config.CARD_INFO_TTL_DAYS, ttl = 100_000, config.CARD_INFO_TTL_DAYS   # 오늘 날짜와 무관하게
            try:
                rc, out = e.main("--fill")
            finally:
                config.CARD_INFO_TTL_DAYS = ttl
            assert rc == 0 and sorted(e.asked) == list(range(2, 12)) and "100.0%" in out and "secret" not in out, (rc, out, e.asked)
            assert e.digest() == before
            assert e.main("--status")[0] == 0
            assert e.main("--delete")[0] == 0 and not e.box.exists() and e.real.exists()
        finally:
            playerinfo.fetch_player_ability, config.CARD_BACKFILL_GAP_S = keep


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
