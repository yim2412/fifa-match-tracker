"""구단주 분포 예약·토글·동의(49단계 조각 ③a) — OwnerDistScheduler · tray.AppShell · [정보] 토글 · .env 스위치.

로더는 가짜로 바꿔 끼운다(실제 받기는 test_ownerdist.py). 네트워크 없이 offscreen 으로 돈다.
`python tests/test_ownerdist_sched.py` 로 실행.
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import config  # noqa: E402
import ownerdist as od  # noqa: E402
import store  # noqa: E402
import tray  # noqa: E402

NOW = datetime(2026, 10, 13, 12, 0, 0)
ON_DAY = "2026-10-10"
_REAL_LOADER = app_main.OwnerDistLoader
_KEYS = ("ENV_PATH", "DB_PATH", "RANK_DB_PATH", "WEB_DATA", "RANK_COLLECT", "NOTICE_ACCEPTED", "API_KEY",
         "OWNER_DIST_ON", "OWNER_DIST_OFF", "OWNER_DIST_TICK_S")


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

    def __init__(self):
        self.done = _Sig()
        self.running = self.cancelled = self.terminated = False
        FakeLoader.made.append(self)

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=0):
        return True

    def terminate(self):
        self.terminated, self.running = True, False

    def finish(self, res):
        self.running = False
        self.done.emit(res)


class Env:
    def __enter__(self):
        self._d = tempfile.TemporaryDirectory()
        p = Path(self._d.name)
        self._saved = {k: getattr(config, k) for k in _KEYS}
        self._environ = dict(os.environ)
        self._loader = app_main.OwnerDistLoader
        config.ENV_PATH, config.DB_PATH, config.RANK_DB_PATH = p / ".env", p / "fifa.db", p / "rank.db"
        app_main.OwnerDistLoader = FakeLoader
        FakeLoader.made = []
        self.set()
        self.conn = store.open_db(config.DB_PATH)
        return self

    def set(self, web=True, collect=True, notice=config.OWNER_DIST_NOTICE_VERSION, on=ON_DAY, key="k", off="",
            disk: dict | None = None):
        """이 프로세스 값과 .env 를 같이 맞춘다. disk 로 .env 쪽만 다르게(손 수정·다른 실행본)."""
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.API_KEY = web, collect, notice, key
        config.OWNER_DIST_ON, config.OWNER_DIST_OFF = on, off
        vals = {config.WEB_DATA_VAR: int(web), config.RANK_COLLECT_VAR: int(collect), config.NOTICE_VAR: notice,
                config.OWNER_DIST_VAR: on, config.OWNER_DIST_OFF_VAR: off, config.API_KEY_VAR: key}
        vals.update(disk or {})
        config.ENV_PATH.write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")

    def env_line(self, var):
        for ln in config.ENV_PATH.read_text(encoding="utf-8").splitlines():
            if ln.startswith(f"{var}="):
                return ln.split("=", 1)[1]
        return None

    def seed_done(self, n=3) -> int:
        rid = store.owner_round_start(self.conn, 1, "2026-10-01T00:00:00", "s")
        for i in range(n):
            store.owner_dist_add(self.conn, rid, f"h{i}", {"stratum": 1, "n_games": 900, "last_match": "2026-10-01T00:00:00"})
        store.owner_round_end(self.conn, rid, "done", "2026-10-02T00:00:00")
        return rid

    def seed_running(self) -> int:
        rid = store.owner_round_start(self.conn, 2, "2026-10-12T00:00:00", "salt-x")
        store.owner_dist_add(self.conn, rid, "hr", {"stratum": 2, "n_games": 900, "last_match": "2026-10-12T00:00:00"})
        return rid

    def rows(self, rid):
        return len(store.owner_dist_rows(self.conn, rid))

    def __exit__(self, *exc):
        self.conn.close()
        app_main.OwnerDistLoader = self._loader
        for k, v in self._saved.items():
            setattr(config, k, v)
        os.environ.clear()
        os.environ.update(self._environ)
        self._d.cleanup()


def _sched():
    return app_main.OwnerDistScheduler(now_fn=lambda: NOW)


# ── 예약 · 셸 ─────────────────────────────────────────────────────────

def test_tray_shell_without_window_ticks_starts_and_quit_keeps_the_round():
    with Env() as e:
        config.OWNER_DIST_TICK_S = 7   # 기본값(600)이 아닌 값 — 간격 배선을 잰다
        s = _sched()
        assert s._timer.interval() == 7000, "틱 간격이 설정과 이어져 있지 않다"
        sh = tray.AppShell(_app, owner_sched=s)
        quits = []
        sh.app = type("A", (), {"quit": lambda self: quits.append(1), "windowIcon": lambda self: _app.windowIcon()})()
        sh.start(tray_mode=True)   # --tray: 창 없음
        assert sh.window is None and s._timer.isActive(), "셸이 예약 타이머를 안 켰다"
        s.tick()
        assert len(FakeLoader.made) == 1 and FakeLoader.made[0].running, "창 없이 틱이 바퀴를 안 시작했다"
        s.tick()
        assert len(FakeLoader.made) == 1, "도는 중에 로더를 하나 더 띄웠다"
        rid = e.seed_running()
        sh._tray_timer.stop()
        sh.quit_app()
        ld = FakeLoader.made[0]
        assert ld.cancelled and not s._timer.isActive() and quits == [1], "종료가 예약을 안 멈췄다"
        run = store.owner_round_running(e.conn)
        assert run is not None and run["round_id"] == rid and run["salt"] == "salt-x", "종료가 진행 바퀴를 끝내 버렸다(이어 받기 불가)"


def test_shell_attaches_owner_sched_to_window_and_main_builds_it():
    with Env():
        s = _sched()
        sh = tray.AppShell(_app, owner_sched=s)
        got = []
        win = type("W", (), {"attach_owner_sched": lambda self, x: got.append(x)})()
        sh.attach_window(win)
        assert got == [s], "셸이 창에 구단주 분포 예약을 안 붙였다(끄는 길이 예약에 안 닿는다)"
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    main = ast.get_source_segment(src, next(n for n in ast.parse(src).body
                                            if isinstance(n, ast.FunctionDef) and n.name == "main"))
    assert "owner_sched=OwnerDistScheduler()" in main, "main 이 셸에 구단주 분포 예약을 안 넘긴다"


def test_fast_shutdown_returns_the_running_loader():
    with Env():
        s = _sched()
        s.tick()
        ld = FakeLoader.made[0]
        assert s.shutdown(fast=True) == [ld] and ld.cancelled and not ld.terminated
        s2 = _sched()
        assert s2.shutdown(fast=True) == [], "돌지 않는데 기다릴 스레드를 넘겼다"


def test_tick_starts_only_when_allowed_due_and_disk_agrees():
    cases = [
        ("토글 꺼짐", dict(on="")),
        ("동의 9", dict(notice=config.OWNER_DIST_NOTICE_VERSION - 1)),
        ("웹 데이터 꺼짐", dict(web=False)),
        ("수집 꺼짐", dict(collect=False)),
        ("키 없음", dict(key="")),
        (".env 손 수정 — 토글 꺼짐", dict(disk={config.OWNER_DIST_VAR: ""})),
        (".env 손 수정 — 수집 꺼짐", dict(disk={config.RANK_COLLECT_VAR: 0})),
        (".env 손 수정 — 동의 9", dict(disk={config.NOTICE_VAR: config.OWNER_DIST_NOTICE_VERSION - 1})),
        ("이 프로세스만 동의 9", dict(notice=config.OWNER_DIST_NOTICE_VERSION - 1,
                                     disk={config.NOTICE_VAR: config.OWNER_DIST_NOTICE_VERSION})),
    ]
    for name, kw in cases:
        with Env() as e:
            e.set(**kw)
            _sched().tick()
            assert FakeLoader.made == [], f"{name}: 로더를 띄웠다"
    with Env() as e:
        _sched().tick()
        assert len(FakeLoader.made) == 1, "다 켜져 있는데 안 띄웠다(대조군)"
    with Env() as e:
        e.set(disk={config.OWNER_DIST_VAR: ""})
        _sched().tick()
        assert config.OWNER_DIST_ON == "", "디스크에서 읽은 토글을 이 프로세스에 안 맞췄다"
    with Env() as e:
        rid = store.owner_round_start(e.conn, 1, "2026-10-12T00:00:00", "s")
        store.owner_round_end(e.conn, rid, "done", "2026-10-12T00:00:00")   # 바로 어제 끝남 — 14일 전엔 차례가 아니다
        _sched().tick()
        assert FakeLoader.made == [], "새 바퀴 차례가 아닌데 띄웠다"


def test_prereq_off_purges_distribution_toggle_off_keeps_it():
    with Env() as e:
        done, run = e.seed_done(), e.seed_running()
        s = _sched()
        e.set(on="")
        s.sync()
        assert store.owner_round_running(e.conn) is None, "토글 끔이 진행 바퀴를 안 끝냈다"
        assert e.rows(done) == 3 and e.rows(run) == 0, "토글 끔이 받아 둔 분포를 지웠거나 진행분을 남겼다"
        e.set(collect=False)
        s.sync()
        assert e.rows(done) == 0, "전제(수집)가 꺼졌는데 분포를 안 지웠다"


def test_purge_waits_for_the_running_loader():
    with Env() as e:
        s = _sched()
        s.tick()
        ld = FakeLoader.made[0]
        done, _run = e.seed_done(), e.seed_running()
        e.set(on="")
        s.sync()                    # 토글 끔(진행만)
        e.set(collect=False)
        s.sync()                    # 그 사이 전제도 꺼짐 — 전부로 합친다
        assert ld.cancelled, "로더를 안 멈췄다"
        assert store.owner_round_running(e.conn) is not None and e.rows(done) == 3, "로더가 도는 중에 지웠다(저장과 겹친다)"
        ld.finish(od.RoundResult(cancelled=True))
        assert store.owner_round_running(e.conn) is None and e.rows(done) == 0, "로더가 끝난 뒤 미뤄 둔 지우기를 안 했다"


def test_unreadable_env_does_nothing():
    with Env() as e:
        done = e.seed_done()
        e.set(collect=False)
        keep = config._read_env_disk
        try:
            config._read_env_disk = lambda: None
            _sched().sync()
            assert e.rows(done) == 3, ".env 를 못 읽은 것만으로 분포를 지웠다"
        finally:
            config._read_env_disk = keep
        _sched().sync()
        assert e.rows(done) == 0, "읽을 수 있을 때는 지워야 한다(대조군)"


def _ended(conn, result, started):
    rid = store.owner_round_start(conn, 1, started, "s")
    store.owner_round_end(conn, rid, result, started)


def test_three_failures_turn_the_toggle_off():
    with Env() as e:
        s = _sched()
        offs = []
        s.switched_off.connect(offs.append)
        e.seed_running()   # 이어 받을 바퀴가 있어야 틱이 로더를 띄운다(끝난 실패 바퀴는 14일 기준점이다)
        _ended(e.conn, "aborted", "2026-10-10T01:00:00")
        _ended(e.conn, "dropped", "2026-10-11T01:00:00")
        s.tick()
        FakeLoader.made[0].finish(od.RoundResult(result="dropped"))
        assert offs == [] and config.OWNER_DIST_ON == ON_DAY, "실패 두 번에 껐다"
        _ended(e.conn, "aborted", "2026-10-12T01:00:00")
        s.tick()
        FakeLoader.made[-1].finish(od.RoundResult(result="aborted"))
        assert offs == ["fail"], offs
        assert config.OWNER_DIST_ON == "" and config.OWNER_DIST_OFF == "fail"
        assert e.env_line(config.OWNER_DIST_VAR) == "" and e.env_line(config.OWNER_DIST_OFF_VAR) == "fail", ".env 에 안 남겼다"
        n = len(FakeLoader.made)
        s.tick()
        assert len(FakeLoader.made) == n, "끈 뒤에도 틱이 바퀴를 띄웠다"


def test_devkey_429_turns_off_and_drops_the_running_round():
    with Env() as e:
        s = _sched()
        offs = []
        s.switched_off.connect(offs.append)
        s.tick()
        run = e.seed_running()
        store.budget_mark_429(e.conn, NOW.date().isoformat(), store.BUDGET_OWNER_DIST)
        FakeLoader.made[0].finish(od.RoundResult(round_id=run, quota=True, requests=2))
        assert offs == ["devkey"] and config.OWNER_DIST_OFF == "devkey", offs
        assert store.owner_round_running(e.conn) is None, "개발 키로 끈 뒤 진행 바퀴가 남았다"
    with Env() as e:
        s = _sched()
        offs = []
        s.switched_off.connect(offs.append)
        s.tick()
        run = e.seed_running()   # 켠 뒤 시작한 바퀴가 이미 5,000건을 보냈다 — 서비스 키
        _spend(e.conn, NOW.date().isoformat(), run, config.OWNER_DIST_SELFCHECK_REQ)
        store.budget_mark_429(e.conn, NOW.date().isoformat(), store.BUDGET_OWNER_DIST)
        FakeLoader.made[0].finish(od.RoundResult(round_id=run, quota=True, requests=2))
        assert offs == [] and config.OWNER_DIST_ON == ON_DAY, "켠 뒤 누적이 많은 서비스 키를 껐다"


def _spend(conn, day: str, rid: int, n: int) -> None:
    """바퀴 rid 가 그날 n 건을 보냈다 — 바퀴 계수와 하루 계수를 같이."""
    store.owner_round_add_req(conn, rid, n)
    with conn:
        conn.execute("INSERT INTO api_budget (day, kind, attempts, hit_429) VALUES (?, ?, ?, 0)"
                     " ON CONFLICT(day, kind) DO UPDATE SET attempts = attempts + excluded.attempts",
                     (day, store.BUDGET_OWNER_DIST, n))


def test_devkey_after_same_day_key_change():
    """키를 바꿔 같은 날 다시 켰는데 새 키가 개발 키 — 옛 키의 그날 요청이 새 키 누적에 섞이면 서비스 키로 보여 안 꺼진다
    (그러면 날마다 새 키의 하루 1,000건을 먼저 다 써 사용자 검색이 429). 켠 시각 이후 시작한 바퀴만 센다."""
    with Env() as e:
        s = _sched()
        offs = []
        s.switched_off.connect(offs.append)
        today = datetime.now().date().isoformat()
        old = store.owner_round_start(e.conn, 1, f"{today}T00:00:00", "s")   # 옛 키 A(서비스 단계)의 오늘 바퀴
        _spend(e.conn, today, old, 3 * config.OWNER_DIST_SELFCHECK_REQ)
        config.save_api_key("new")
        s.sync()
        assert config.OWNER_DIST_OFF == "key" and store.owner_round_running(e.conn) is None
        config.set_owner_dist(True)   # 같은 날 다시 켬 — B 는 사실 개발 키
        s.tick()
        assert len(FakeLoader.made) == 1, "다시 켰는데 바퀴를 안 띄웠다"
        new = store.owner_round_start(e.conn, 2, datetime.now().isoformat(timespec="seconds"), "s")
        _spend(e.conn, today, new, 1000)
        store.budget_mark_429(e.conn, today, store.BUDGET_OWNER_DIST)
        FakeLoader.made[0].finish(od.RoundResult(round_id=new, quota=True, requests=1000))
        assert offs == ["devkey"], f"옛 키의 같은 날 누적이 개발 키를 가렸다: {offs}"


def test_done_emits_dist_changed_only_for_done():
    with Env():
        s = _sched()
        got = []
        s.dist_changed.connect(lambda: got.append(1))
        for res, want in ((od.RoundResult(result="thin"), []), (od.RoundResult(cancelled=True), []),
                          (od.RoundResult(result="done"), [1])):
            got.clear()
            s.tick()
            FakeLoader.made[-1].finish(res)
            assert got == want, (res, got)


def test_key_change_turns_the_toggle_off():
    with Env() as e:
        config.save_api_key("k")   # 같은 키
        assert config.OWNER_DIST_ON == ON_DAY, "같은 키를 다시 저장했는데 껐다"
        config.save_api_key("new")
        assert config.OWNER_DIST_ON == "" and config.OWNER_DIST_OFF == "key" and config.API_KEY == "new"
        assert e.env_line(config.OWNER_DIST_VAR) == "" and e.env_line(config.OWNER_DIST_OFF_VAR) == "key"
        e.set(on="")
        config.save_api_key("other")
        assert config.OWNER_DIST_OFF == "", "꺼져 있는데 키 사유를 덮어썼다"


def test_set_owner_dist_writes_the_moment_and_clears_reason():
    with Env() as e:
        e.set(on="", off="fail")
        before = datetime.now().isoformat(timespec="seconds")
        config.set_owner_dist(True)
        after = datetime.now().isoformat(timespec="seconds")
        at = config.OWNER_DIST_ON
        assert before <= at <= after and "T" in at, f"켠 시각(초까지)이 아니다: {at!r} — 날짜만이면 같은 날 옛 바퀴가 섞인다"
        assert e.env_line(config.OWNER_DIST_VAR) == at
        assert config.OWNER_DIST_OFF == "" and e.env_line(config.OWNER_DIST_OFF_VAR) == ""


def test_real_loader_wires_cancel_rank_open_and_hides_error_text():
    with Env():
        seen = {}

        def fake_run(db_path, rank_conn, api_factory, **kw):
            seen.update(db=db_path, rank=rank_conn, kw=kw, api=api_factory())
            return od.RoundResult(result="done")
        keep = (od.run_round, app_main.FCOnlineAPI)
        try:
            od.run_round = fake_run
            app_main.FCOnlineAPI = lambda key, cache_dir=None: ("api", key)
            real = _REAL_LOADER()   # Env 가 바꿔 끼우기 전의 진짜 로더
            got = []
            real.done.connect(got.append)
            real.cancel()
            real.run()   # 스레드 없이 그 자리에서
            assert got and got[0].result == "done", got
            assert seen["db"] == config.DB_PATH and seen["rank"] is None, seen
            assert seen["kw"]["rank_open"] is app_main.rankcollect.open_rank_db_ro, "rank.db 를 바퀴 내내 붙잡는다"
            assert seen["kw"]["cancel"](), "cancel 이 run_round 로 안 이어졌다"
            assert seen["api"] == ("api", "k"), seen["api"]

            def boom(*a, **k):
                raise ValueError("구단주닉네임 이 URL 에")
            od.run_round = boom
            got.clear()
            real.run()
            assert got[0].error == "ValueError" and "닉네임" not in got[0].error, got[0].error
        finally:
            od.run_round, app_main.FCOnlineAPI = keep


# ── 창과의 경계 ──────────────────────────────────────────────────────

def _main_window_methods() -> dict[str, str]:
    src = Path(_ROOT, "app_main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    return {f.name: ast.get_source_segment(src, f) for f in cls.body if isinstance(f, ast.FunctionDef)}


def test_main_window_never_yields_or_stops_the_owner_loader():
    """양보·우선순위 사슬을 넣지 않는다(ROADMAP 조각 ③ 설계) — 창에서 예약을 만지는 곳은 붙이기·끄는 길 동기화뿐."""
    meths = _main_window_methods()
    touching = {name for name, body in meths.items() if "_owner_sched" in body}
    assert touching == {"__init__", "attach_owner_sched", "_sync_owner_dist"}, touching
    for name in ("_api_background_busy", "_api_loader_busy", "stop_api_loaders", "busy_for_release", "hideEvent",
                 "release_memory"):
        assert name in meths, f"{name} 이 없어졌다 — 검사 목록을 고친다"
        assert "owner" not in meths[name].lower(), f"{name} 이 구단주 분포 로더를 건드린다"
    for name in ("sync_ranker_pick_data", "_ask_new_key"):
        assert "self._sync_owner_dist()" in meths[name], f"{name} 이 구단주 분포 예약에 알리지 않는다"


class _Parent(QWidget):
    def __init__(self):
        super().__init__()
        self.synced = []
        self._owner_sched = type("S", (), {"sync": lambda _s, start=False: self.synced.append(start)})()


def test_about_dialog_toggle_and_block_reasons():
    with Env() as e:
        e.set(on="")
        p = _Parent()
        dlg = app_main.AboutDialog(p)
        assert dlg.chk_owner.isEnabled() and not dlg.chk_owner.isChecked()
        assert "아직" in dlg.lb_owner.text(), dlg.lb_owner.text()
        dlg.chk_owner.setChecked(True)
        today = datetime.now().date().isoformat()
        assert e.env_line(config.OWNER_DIST_VAR).startswith(today) and p.synced == [True], (p.synced,)
        dlg.chk_owner.setChecked(False)
        assert e.env_line(config.OWNER_DIST_VAR) == "" and p.synced == [True, False]
        dlg.chk_web.setChecked(False)   # 웹 데이터를 끄면 수집도 꺼진다 — 토글이 잠긴다
        assert not dlg.chk_owner.isEnabled() and "랭킹 수집" in dlg.lb_owner.text(), dlg.lb_owner.text()
        dlg.deleteLater()
    for kw, word in ((dict(notice=config.OWNER_DIST_NOTICE_VERSION - 1), "동의"), (dict(collect=False), "랭킹 수집"),
                     (dict(key=""), "키")):
        with Env() as e:
            e.set(**kw)
            dlg = app_main.AboutDialog(_Parent())
            assert not dlg.chk_owner.isEnabled() and not dlg.chk_owner.isChecked(), kw
            assert word in dlg.lb_owner.text(), (kw, dlg.lb_owner.text())
            dlg.deleteLater()
    with Env() as e:
        e.set(on="", off="devkey")
        dlg = app_main.AboutDialog(_Parent())
        assert "개발 단계 키" in dlg.lb_owner.text(), dlg.lb_owner.text()
        dlg.deleteLater()


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


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
