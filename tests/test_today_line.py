"""트레이 "오늘 전적" 한 줄(109 J10)과 최근 검색 칩 미리보기(U2). `python tests/test_today_line.py` 로 실행(offscreen · 네트워크 없음).

창 없이 `app_main.today_line()` 만 불러 fifa.db 를 직접 읽는 길을 잰다: 표시한 판 거르기(스위치) · 지우는 중 걸쇠 · 연결 닫기(바꿔 끼우기가 막히지 않는지)
· 옛 DB(notes 없음) · 자정 경계(now= 로 UTC 를 넣어 KST 가 아닌 시각을 흉내) · 몰수패 · DB 없으면 파일을 안 만든다.
메뉴는 `tray.AppShell` 에 가짜 함수를 넘겨 aboutToShow 로 잰다. 칩 미리보기는 실제 창에서 로더를 그 자리에서 돌려(QThread.start 를 run 으로) 잰다.
원문 시각은 UTC 라 KST 로 뜻한 시각에서 글자 그대로 9시간 뺀 값을 적는다(CLAUDE.md "규칙은 테스트로").
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="fifa109t_"))
os.environ["FIFA_DATA_DIR"] = str(SCRATCH)
for _k in ("NEXON_API_KEY", "FIFA_SERVER_URL", "FIFA_DEMO"):
    os.environ.pop(_k, None)

from PySide6.QtCore import QSettings, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import config  # noqa: E402
import core_api as core  # noqa: E402
import demodata  # noqa: E402
import storage  # noqa: E402
import store  # noqa: E402
import tray  # noqa: E402

config.SINGLE_INSTANCE = False
config.UPDATE_CHECK = False
ME, OPP = "me-ouid", "opp-ouid"
# KST 2026-11-15 12:00 에 "지금" 이 있다 — UTC 로는 03:00
NOW = datetime(2026, 11, 15, 3, 0, 0, tzinfo=timezone.utc)


def _d(mid: str, raw: str, result: str, forfeit: bool = False) -> dict:
    """경기 하나. raw = UTC 원문 시각. result = 승·무·패."""
    opp = {"승": "패", "패": "승", "무": "무"}[result]
    pre = "몰수" if forfeit else ""
    return {"matchId": mid, "matchDate": raw, "matchType": config.DEFAULT_MATCH_TYPE,
            "matchInfo": [{"ouid": ME, "nickname": "나", "matchDetail": {"matchResult": pre + result}, "shoot": {"goalTotal": 1},
                           "player": [{"spId": 1, "spPosition": 1, "spGrade": 1}]},
                          {"ouid": OPP, "nickname": "상대", "matchDetail": {"matchResult": pre + opp}, "shoot": {"goalTotal": 0},
                           "player": [{"spId": 2, "spPosition": 1, "spGrade": 1}]}]}


# KST 로 뜻한 시각 → 적는 원문은 9시간 뺀 UTC 글자
MATCHES = [
    _d("A", "2026-11-15T14:59:00", "승"),            # KST 11-15 23:59  — 오늘 끝자락
    _d("B", "2026-11-15T15:00:00", "무"),            # KST 11-16 00:00  — 내일
    _d("C", "2026-11-14T14:59:59", "승"),            # KST 11-14 23:59:59 — 어제 끝자락
    _d("D", "2026-11-14T15:00:00", "패"),            # KST 11-15 00:00  — 오늘 첫 순간
    _d("E", "2026-11-15T03:00:00", "패", forfeit=True),   # KST 11-15 12:00 몰수패 — 패로 센다
]
NOW_KST_15 = datetime(2026, 11, 15, 14, 59, 59, tzinfo=timezone.utc)   # KST 11-15 23:59:59
NOW_KST_16 = datetime(2026, 11, 15, 15, 0, 0, tzinfo=timezone.utc)     # KST 11-16 00:00:00


def _reset(notes: dict | None = None, my: bool = True, switch: bool = False) -> None:
    for f in storage.db_files(config.DB_PATH):
        f.unlink(missing_ok=True)
    conn = store.open_db(config.DB_PATH)
    try:
        store.save_matches(conn, MATCHES)
        store.upsert_account(conn, ME, "나")
        if my:
            store.set_trade_state(conn, my_ouid=ME)
        for k, mark in (notes or {}).items():
            store.save_note(conn, store.NOTE_MATCH, k, mark=mark)
    finally:
        conn.close()
    _set_switch(switch)
    config.NOTICE_ACCEPTED = config.NOTICE_VERSION
    app_main.CLEARING.clear()
    core.set_excluded(())


def _set_switch(on: bool) -> None:
    s = QSettings(str(config.SETTINGS_PATH), QSettings.Format.IniFormat)
    s.setValue(config.EXCLUDE_SHOWN_KEY, "true" if on else "false")
    s.sync()


class _CountConnect:
    """sqlite3.connect 를 세는 감시 — 걸쇠가 열기 전에 막았는지(열었다 닫는 것도 잡는다)."""
    def __init__(self):
        self.n = 0
        self._orig = sqlite3.connect

    def __enter__(self):
        def spy(*a, **k):
            self.n += 1
            return self._orig(*a, **k)
        sqlite3.connect = spy
        return self

    def __exit__(self, *a):
        sqlite3.connect = self._orig
        return False


# ── 한 줄 글 ────────────────────────────────────────────────────────────────
def test_line_counts_today_in_kst_with_forfeit_as_loss():
    _reset()
    got = app_main.today_line(NOW_KST_15)
    assert got == "오늘 1승 0무 2패", got    # A 승 · D 패 · E 몰수패(패) — C(어제)·B(내일)은 빠진다


def test_midnight_boundary_uses_kst_not_pc_or_utc():
    _reset()
    assert app_main.today_line(NOW_KST_16) == "오늘 0승 1무 0패", app_main.today_line(NOW_KST_16)   # B 하나(KST 11-16 00:00)
    assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 2패"
    # UTC 날짜(11-15)로 셌다면 A·B·E 가 들어가 다르다 — 위 둘이 갈리는 것이 KST 경계의 증거
    day_before = datetime(2026, 11, 14, 14, 59, 59, tzinfo=timezone.utc)   # KST 11-14 23:59:59 — C 만
    assert app_main.today_line(day_before) == "오늘 1승 0무 0패", app_main.today_line(day_before)


def test_zero_games_says_no_games():
    _reset()
    far = datetime(2026, 12, 1, 3, 0, 0, tzinfo=timezone.utc)
    assert app_main.today_line(far) == "오늘 경기 없음"


def test_marked_matches_drop_out_only_when_switch_is_on():
    _reset(notes={"D": "cut"}, switch=False)
    assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 2패", "스위치가 꺼졌는데 표시한 판이 빠졌다"
    _set_switch(True)
    assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 1패", "스위치를 켰는데 표시한 판이 안 빠졌다"
    _set_switch(False)
    assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 2패", "스위치를 다시 끄니 안 돌아왔다"
    _reset(notes={"D": "cut", "E": "practice", "A": "cut"}, switch=True)
    assert app_main.today_line(NOW_KST_15) == "오늘 경기 없음"


def test_window_global_exclude_does_not_leak_into_line():
    """창의 전역 묶음(core.set_excluded)이 비어 있어도, 켜 있어도 한 줄은 스위치+notes 로만 정한다(늘 exclude= 를 넘긴다)."""
    _reset(notes={}, switch=False)
    core.set_excluded({"A", "D"})
    try:
        assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 2패", "창 전역이 한 줄에 새어 들었다"
    finally:
        core.set_excluded(())


def test_core_wdl_default_uses_global_but_explicit_exclude_does_not():
    details = [_d("A", "2026-11-15T14:59:00", "승"), _d("D", "2026-11-14T15:00:00", "패")]
    assert core.today_wdl(details, ME, now=NOW_KST_15) == (1, 0, 1)
    core.set_excluded({"D"})
    try:
        assert core.today_wdl(details, ME, now=NOW_KST_15) == (1, 0, 0), "exclude= 를 안 넘기면 전역이 들어가야 한다"
        assert core.today_wdl(details, ME, exclude=frozenset(), now=NOW_KST_15) == (1, 0, 1), "넘겼는데 전역이 섞였다"
    finally:
        core.set_excluded(())


def test_old_db_without_notes_table_still_counts():
    _reset(switch=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    try:
        conn.execute("DROP TABLE notes")
        conn.commit()
    finally:
        conn.close()
    assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 2패"


# ── 줄이 없는 길 ────────────────────────────────────────────────────────────
def test_no_line_without_consent_without_account_or_without_db():
    _reset()
    config.NOTICE_ACCEPTED = 0
    assert app_main.today_line(NOW_KST_15) is None, "동의 전인데 줄이 나왔다"
    _reset(my=False)
    assert app_main.today_line(NOW_KST_15) is None, "내 계정이 없는데 줄이 나왔다"
    keep = config.DB_PATH
    config.DB_PATH = SCRATCH / "none" / "fifa.db"
    try:
        assert app_main.today_line(NOW_KST_15) is None
        assert not (SCRATCH / "none").exists() and not config.DB_PATH.exists(), "DB 가 없는데 파일·폴더를 만들었다"
    finally:
        config.DB_PATH = keep


def test_clearing_latch_blocks_before_any_connection():
    _reset()
    for name in (app_main.STORE_FIFA, "all"):
        app_main.CLEARING.clear()
        app_main.CLEARING.add(name)
        with _CountConnect() as c:
            assert app_main.today_line(NOW_KST_15) is None, f"{name} 를 지우는 중인데 줄이 나왔다"
        assert c.n == 0, f"{name} 를 지우는 중인데 연결을 {c.n}번 열었다"
    app_main.CLEARING.clear()
    app_main.CLEARING.add(app_main.STORE_IMAGES)    # fifa.db 와 무관한 저장소는 막지 않는다
    try:
        assert app_main.today_line(NOW_KST_15) == "오늘 1승 0무 2패"
    finally:
        app_main.CLEARING.clear()


def test_connection_is_closed_so_reset_db_swaps_in():
    _reset()
    assert app_main.today_line(NOW_KST_15) is not None
    assert storage.reset_db(config.DB_PATH) == "ok", "줄을 읽은 직후 바꿔 끼우기가 막혔다(연결이 안 닫혔다)"


def test_connection_is_closed_even_when_counting_raises():
    _reset()
    orig = store.today_payloads

    def boom(*a, **k):
        raise RuntimeError("셈 도중 예외")
    store.today_payloads = boom
    try:
        assert app_main.today_line(NOW_KST_15) is None
    finally:
        store.today_payloads = orig
    assert storage.reset_db(config.DB_PATH) == "ok", "셈 도중 예외 뒤 연결이 열린 채로 남았다"


def test_no_nexon_request_is_made():
    """오늘 한 줄은 fifa.db 읽기뿐 — requests 어댑터가 한 번이라도 불리면 실패."""
    import requests.adapters
    sent = []
    orig = requests.adapters.HTTPAdapter.send
    requests.adapters.HTTPAdapter.send = lambda self, req, *a, **k: sent.append(req.url) or (_ for _ in ()).throw(OSError("막음"))
    try:
        _reset()
        app_main.today_line(NOW_KST_15)
    finally:
        requests.adapters.HTTPAdapter.send = orig
    assert sent == [], sent


# ── 메뉴 ────────────────────────────────────────────────────────────────────
def _shell(fn):
    keep = tray.QSystemTrayIcon.isSystemTrayAvailable
    tray.QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
    try:
        sh = tray.AppShell(_app, today_line=fn)
        assert sh._ensure_tray()
    finally:
        tray.QSystemTrayIcon.isSystemTrayAvailable = keep
    return sh


def test_menu_shows_line_on_open_and_hides_on_none_or_error():
    state = {"v": "오늘 3승 1무 2패"}

    def fn():
        if isinstance(state["v"], Exception):
            raise state["v"]
        return state["v"]
    sh = _shell(fn)
    try:
        act, sep = sh._today_act, sh._today_sep
        assert not act.isVisible() and not sep.isVisible(), "열기 전에 줄이 보인다"
        assert not act.isEnabled(), "줄은 누를 수 없어야 한다"
        sh._menu.aboutToShow.emit()
        assert act.isVisible() and sep.isVisible() and act.text() == "오늘 3승 1무 2패", (act.isVisible(), act.text())
        state["v"] = "오늘 경기 없음"
        sh._menu.aboutToShow.emit()
        assert act.text() == "오늘 경기 없음", "다시 열었는데 글이 안 바뀌었다"
        state["v"] = None
        sh._menu.aboutToShow.emit()
        assert not act.isVisible() and not sep.isVisible(), "None 인데 줄이 남았다"
        state["v"] = "오늘 1승 0무 0패"
        sh._menu.aboutToShow.emit()
        assert act.isVisible()
        state["v"] = RuntimeError("터짐")
        sh._menu.aboutToShow.emit()          # 예외가 밖으로 나오면 여기서 실패한다 — 메뉴는 열려야 한다
        assert not act.isVisible() and not sep.isVisible(), "예외인데 줄이 남았다"
        names = [a.text() for a in sh._menu.actions() if a.isVisible() and not a.isSeparator()]
        assert names == ["열기", "종료"], names
    finally:
        sh.tray.hide()


def test_menu_has_no_today_line_when_not_given():
    keep = tray.QSystemTrayIcon.isSystemTrayAvailable
    tray.QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
    try:
        sh = tray.AppShell(_app)
        assert sh._ensure_tray()
        assert [a.text() for a in sh._menu.actions() if not a.isSeparator()] == ["열기", "종료"]
        sh.tray.hide()
    finally:
        tray.QSystemTrayIcon.isSystemTrayAvailable = keep


def test_real_shell_wiring_passes_today_line():
    """app_main 이 보통 실행·둘러보기 셸 둘 다 today_line 을 넘긴다(소스에서 대조 — 둘 다 만들려면 앱을 띄워야 해서)."""
    src = Path(app_main.__file__).read_text(encoding="utf-8")
    assert src.count("today_line=today_line") == 2, "셸을 만드는 두 자리에 today_line 이 안 붙었다"


# ── 칩 미리보기(U2) ─────────────────────────────────────────────────────────
def _window():
    data = demodata.generate(n=1)
    win = app_main.MainWindow(demodata.DemoAPI(data, cache_dir=SCRATCH / ".cache"))
    win._on_fetch_team_colors = lambda **k: None
    return win


def test_chip_preview_uses_worker_cache_switch_and_latch():
    _reset(notes={"D": "cut"})
    win = _window()
    for w in list(win._chip_workers):      # 창을 만들 때 "내 계정" 칩이 띄운 실제 스레드 - 끝나기를 기다려 비운다
        w.wait(5000)
    _app.processEvents()
    win._chip_workers = []
    made = []
    orig_start = QThread.start
    orig_init = app_main.ChipPreviewLoader.__init__

    def init(self, ouids, fv):
        made.append((tuple(ouids), fv))
        orig_init(self, ouids, fv)
    app_main.ChipPreviewLoader.__init__ = init
    app_main.ChipPreviewLoader.start = lambda self, *a: self.run()     # 그 자리에서 돈다(실제 스레드는 아래 별도)
    try:
        chip = QPushButton("나")
        win._chip_cache.clear()
        win._chip_btns = {ME: chip}
        win._released = False
        core.set_excluded(())
        win._chip_apply()
        want = "최근 5판 2승 1무 2패 승률 " + app_main.win_rate_text(2, 1, 2, False, short=True)
        assert chip.toolTip() == want, (chip.toolTip(), want)
        assert len(made) == 1
        win._chip_apply()
        assert len(made) == 1, "캐시가 있는데 다시 읽었다"
        # 거르기 판본이 바뀌면(표시한 판 빼고 보기) 새 값을 읽는다
        core.set_excluded({"D"})
        core.bump_filter_version()
        win._chip_apply()
        assert len(made) == 2, "거르기 판본이 바뀌었는데 캐시를 그대로 썼다"
        assert chip.toolTip().startswith("최근 4판 2승 1무 1패"), chip.toolTip()
        win._chip_apply()
        assert len(made) == 2, "판본이 같은데 캐시를 못 찾아 또 읽었다(키가 판본을 못 따라간다)"
        core.set_excluded(())
        win._chip_apply()
        assert chip.toolTip().startswith("최근 5판 2승 1무 2패"), "스위치를 끄니 옛 숫자로 안 돌아왔다"
        # 지우는 중이면 빈 줄 · 새로 안 읽음
        win._chip_cache.clear()
        app_main.CLEARING.add(app_main.STORE_FIFA)
        n = len(made)
        win._chip_apply()
        assert chip.toolTip() == "" and len(made) == n, "지우는 중인데 로더를 띄웠다"
        app_main.CLEARING.clear()
        # 메모리를 내려놓은 상태도 빈 줄
        win._released = True
        win._chip_apply()
        assert chip.toolTip() == "" and len(made) == n
    finally:
        app_main.ChipPreviewLoader.__init__ = orig_init
        del app_main.ChipPreviewLoader.start
        QThread.start = orig_start
        app_main.CLEARING.clear()
        core.set_excluded(())
        win._released = False
        win.close()


def test_chip_loader_runs_in_real_thread_and_closes_connection():
    _reset()
    ld = app_main.ChipPreviewLoader([ME], core.filter_version())
    got = []
    ld.ready.connect(lambda p: got.append(p))
    ld.start()
    ld.wait(5000)
    _app.processEvents()
    assert got and got[0][1][ME][:4] == (5, 2, 1, 2), got
    assert storage.reset_db(config.DB_PATH) == "ok", "미리보기 읽기 뒤 연결이 안 닫혔다"



def test_chip_loader_is_waited_for_and_blocked_by_clearing():
    """연결을 쥐는 로더라 지우기가 기다리고(running_writers) 지우는 중엔 뜨지 않아야 한다 - WRITES 가 비면 둘 다 빠진다."""
    _reset()
    assert app_main.running_writers({app_main.STORE_FIFA}) == [], "앞 테스트의 로더가 남았다"
    ld = app_main.ChipPreviewLoader([ME], core.filter_version())
    app_main.CLEARING.add(app_main.STORE_FIFA)
    try:
        ld.start()
        assert ld.blocked and not ld.isRunning(), "지우는 중인데 칩 로더가 떴다(바꿔 끼우기를 막는 연결이 생긴다)"
    finally:
        app_main.CLEARING.discard(app_main.STORE_FIFA)
        ld.wait(5000)   # 막지 못해 떴다면 끝까지 기다린다 - 도는 QThread 를 버리면 프로세스가 통째로 죽어 FAIL 이 안 찍힌다
    assert app_main.ChipPreviewLoader.WRITES & {app_main.STORE_FIFA}, "running_writers 가 도는 칩 로더를 못 찾는다"


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
