"""켤 때 내 계정(108 C) — **실제 QThread 로** 켤 때 길을 잰다. `python tests/test_startup_108.py` 로 실행(네트워크 없음).

화면 스모크(`test_ui_smoke`)는 파일 머리에서 `QThread.start` 를 막아 이 단계의 결함을 못 본다. 3·4·5회차 검토의 [상]
대부분이 신호 순서(`finished` 와 `finished_ok`)·`isRunning`·취소한 로더의 뒷정리였고, start 를 run 으로 바꿔 끼우면
`finished_ok` 때 `isRunning` 이 늘 거짓이라 그 종류를 못 잡는다(ROADMAP 108 5회차 "여기서 멈추는 이유").
그래서 이 파일은 스레드를 막지 않고, 넥슨만 바꿔 끼운다:
- 오픈API: 둘러보기 가짜 API(`demodata.DemoAPI`)를 물려받아 길마다 센다. 상세는 진짜 디스크 캐시 길을 탄다.
- 홈페이지·CDN: `requests.adapters.HTTPAdapter.send` 에서 주소를 적고 끊는다(세션·`requests.get` 둘 다 여기를 지난다).
- 웹 데이터는 **켠 채로** 잰다. 끈 채로 재면 홈페이지 요청이 늘 0 이라 공허하다(환경 행렬 "홈페이지 데이터 켬/끔").
- 게임 감시는 띄우지 않고 `app_main._GAME` 을 고친 뒤 `on_game_changed` 를 직접 부른다(감시가 하는 일 그대로).
"""
from __future__ import annotations

import gc
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="fifa108_"))
os.environ["FIFA_DATA_DIR"] = str(SCRATCH)
for _k in ("NEXON_API_KEY", "FIFA_SERVER_URL", "FIFA_DEMO"):
    os.environ.pop(_k, None)

import requests  # noqa: E402
import requests.adapters  # noqa: E402

SENT: list[str] = []
_SENT_LOCK = threading.Lock()


def _record_send(self, request, *a, **k):
    with _SENT_LOCK:
        SENT.append(request.url)
    raise requests.ConnectionError("테스트 — 네트워크 없음")


requests.adapters.HTTPAdapter.send = _record_send

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import config  # noqa: E402
import app_main  # noqa: E402
import demodata  # noqa: E402
import nexon_api  # noqa: E402
import store  # noqa: E402
import tray  # noqa: E402
import updatecheck  # noqa: E402

assert config.DATA_DIR == SCRATCH, config.DATA_DIR
config.SINGLE_INSTANCE = False
config.UPDATE_CHECK = False          # GitHub 확인은 이 단계 밖 — 켜 두면 기록기에 섞인다
config.API_KEY = "test_key"

NICK, OUID, MT = demodata.NICK, demodata.OUID, demodata.MATCH_TYPE
N_ALL, N_SAVED = 80, 50              # 넥슨에는 80판 · 저장은 50판 → 켤 때 확인이 새 경기 30판을 받는다
N_NEW = N_ALL - N_SAVED
assert N_NEW > app_main.DETAIL_WORKERS, "새 경기가 상세 슬롯보다 많아야 '취소 뒤 새로 시작하는 요청 0' 을 잰다"
DATA = demodata.generate(n=N_ALL)


def _homepage() -> list[str]:
    with _SENT_LOCK:
        return [u for u in SENT if "fconline.nexon.com" in u]


def _openapi_http() -> list[str]:
    with _SENT_LOCK:
        return [u for u in SENT if "api.nexon.com" in u]


class CountingAPI(demodata.DemoAPI):
    """가짜 넥슨 오픈API — 길마다 센다. hold 가 있으면 상세 요청이 그 자리에서 기다린다(확인 도중을 만든다)."""

    def __init__(self, data):
        super().__init__(data, cache_dir=config.CACHE_DIR)
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.meta_calls: list[str] = []
        self.detail_started: list[str] = []
        self.hold: threading.Event | None = None
        self.fail_id: Exception | None = None

    def _get(self, path, attempts=3, **params):
        with self.lock:
            self.calls.append(path)
            if path == nexon_api.EP_MATCH_DETAIL:
                self.detail_started.append(params.get("matchid"))
        if path == nexon_api.EP_ID and self.fail_id is not None:
            raise self.fail_id
        if path == nexon_api.EP_MATCH_DETAIL and self.hold is not None:
            self.hold.wait(30)
        return super()._get(path, attempts, **params)

    def get_match_detail(self, match_id, attempts=3):
        return nexon_api.FCOnlineAPI.get_match_detail(self, match_id, attempts)   # 진짜 디스크 캐시 길

    def get_meta(self, name):
        # 진짜 API 는 주간 캐시가 낡았을 때만 넥슨에 묻는다 — 여기 오는 것은 "낡았으면 요청이 됐을" 부름이다
        with self.lock:
            self.meta_calls.append(name)
        return super().get_meta(name)

    def count(self, path) -> int:
        with self.lock:
            return self.calls.count(path)

    def searches(self) -> int:
        return self.count(nexon_api.EP_ID)


class FakeNotice:
    """다시 묻는 안내 창 — accept 면 동의(최신 안내)로 친다. offscreen 에선 모달이 안 닫힌다."""
    accept = False
    shown = 0

    def __init__(self, parent=None, reask=False):
        pass

    def exec(self):
        FakeNotice.shown += 1
        if FakeNotice.accept:
            config.NOTICE_ACCEPTED = config.NOTICE_VERSION
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected


app_main.NoticeDialog = FakeNotice
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
_API: list[CountingAPI] = []
app_main.FCOnlineAPI = lambda *a, **k: _API[0]      # _open_window 이 만드는 검색 API 를 가짜로
_OPEN: list = []                                    # (셸, 창) — 다음 테스트 전에 닫는다
_REAL_LOAD_SAVED = app_main.load_saved
_REAL_SHOW_INITIAL = app_main.MainWindow.show_initial


def _pump(until, limit_s: float = 30.0) -> bool:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        _app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return False


_LOADERS = ("_loader", "_teamcolor_loader", "_trade_loader", "_pick_loader", "_trait_loader", "_ranker_loader")


def _busy(win) -> bool:
    return any(getattr(win, n, None) is not None and getattr(win, n).isRunning() for n in _LOADERS)


def _quiet(win, limit_s: float = 40.0) -> bool:
    """도는 로더가 없는 채로 0.4초 — finished 처리가 새 로더를 띄우는 길까지 기다린다."""
    end, calm = time.monotonic() + limit_s, 0
    while time.monotonic() < end:
        _app.processEvents()
        calm = 0 if _busy(win) else calm + 1
        if calm >= 40:
            return True
        time.sleep(0.01)
    return False


def _close_all() -> None:
    """quit_app 의 앞부분 그대로(app.quit 만 뺀다) — 창이 로더를 멈추고 기다린다. 남은 연결이 있으면 fifa.db 를 못 지운다."""
    while _OPEN:
        shell, win = _OPEN.pop()
        for t in (shell._release_timer, shell._update_timer, shell._tray_timer):
            t.stop()
        api = _API[0] if _API else None
        if api is not None and api.hold is not None:
            api.hold.set()
        win._quitting = True
        win.shutdown(fast=False)
        _pump(lambda w=win: not _busy(w), 20)
        win.hide()
        win.deleteLater()
    _app.processEvents()
    gc.collect()


def _wipe(p: Path) -> None:
    for _ in range(50):
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
            return
        except OSError:
            _app.processEvents()
            gc.collect()
            time.sleep(0.1)
    raise AssertionError(f"지우지 못했다(아직 열려 있다): {p}")


def _fresh(my: bool = True) -> CountingAPI:
    """빈 데이터 폴더에 50판 저장 · 시즌표(신선) · 메타 캐시 · 계정 줄 · 내 계정."""
    _close_all()
    for p in list(SCRATCH.iterdir()):
        _wipe(p)
    saved = demodata.DemoData([DATA.details[i] for i in DATA.ids[N_NEW:]], DATA.meta, DATA.best_at, DATA.seasons)
    demodata.install(saved, config.DB_PATH, config.CACHE_DIR)
    conn = store.open_db(config.DB_PATH)
    try:
        if my:
            store.set_trade_state(conn, my_ouid=OUID)
    finally:
        conn.close()
    app_main.mark_intro_seen()
    s = app_main.QSettings(str(config.SETTINGS_PATH), app_main.QSettings.Format.IniFormat)
    s.setValue(updatecheck.SEEN_VERSION_KEY, config.APP_VERSION)   # "바뀐 점" 창을 안 띄운다
    s.setValue("view/page", "이적시장 가계부")                       # 저장된 보기가 [가계부] 여도 대시보드(J8)
    s.sync()
    with _SENT_LOCK:
        SENT.clear()
    api = CountingAPI(DATA)
    _API[:] = [api]
    app_main.load_saved = _REAL_LOAD_SAVED
    app_main.MainWindow.show_initial = _REAL_SHOW_INITIAL
    FakeNotice.accept, FakeNotice.shown = False, 0
    app_main._GAME["on"] = False
    config.WEB_DATA = True
    config.RANK_COLLECT = False
    config.NOTICE_ACCEPTED = config.NOTICE_VERSION
    return api


def _open(show: bool = True):
    shell = tray.AppShell(_app, open_window=None)
    win = app_main._open_window(shell, show=show)
    assert win is not None
    _OPEN.append((shell, win))
    return shell, win


def _game(shell, on: bool) -> None:
    app_main._GAME["on"] = on
    app_main.on_game_changed(shell, on)


def _chips(win) -> list[str]:
    return [win.row_recent.itemAt(i).widget().text() for i in range(win.row_recent.count())
            if win.row_recent.itemAt(i).widget() is not None]


def _page(win) -> str | None:
    return win._current_page_name() if win.stack.currentIndex() == win.PAGE_MAIN else None


def _search_enabled(win) -> bool:
    return win.ed_search.isEnabled() and all(b.isEnabled() for b in win._search_btns)


# ── 대조 — 보호가 없으면 일어났을 것 ─────────────────────────────────────────────


def test_counters_see_what_the_startup_path_must_block():
    """사용자가 누른 검색은 홈페이지(순위·팀컬러)와 거래를 부른다 — 기록기가 그걸 본다. 켤 때 길이 이걸 안 부르는 것을
    아래 테스트가 단언하므로, 여기서 0 이 아니어야 그 단언이 빈 검사가 아니다."""
    api = _fresh(my=False)
    shell, win = _open()
    assert _quiet(win)
    assert api.searches() == 0 and _page(win) is None, "내 계정이 없는데 검색했다"
    win._api_search(NICK)
    assert _quiet(win)
    assert win._ouid == OUID and len(win._matches_all) == N_ALL, len(win._matches_all)
    assert api.searches() == 1
    home = _homepage()
    assert any("Rank" in u or "rank" in u for u in home), f"순위 조회가 안 보였다: {home[:3]}"
    assert len(home) >= 2, f"팀컬러 조회가 안 보였다(캐시에 없는 상대 {len(set(m.opponent for m in win._matches_all))}명): {home[:3]}"
    assert api.count(nexon_api.EP_USER_TRADE) >= 1, "검색 뒤 거래 받기가 안 돌았다"
    assert _openapi_http() == [], "가짜 API 를 안 거친 오픈API 요청"


def test_auto_loader_reads_meta_from_disk_only_in_game():
    """메타 진입점 셋(3회차 S1) — 앱이 스스로 띄운 로더(auto)만 게임 중 디스크로. 사용자 로더는 게임 중에도 get_meta."""
    api = _fresh()
    app_main._GAME["on"] = True
    try:
        before = list(api.meta_calls)
        app_main.MatchLoader(api, NICK, MT, offline_ouid=OUID).run()
        user = [m for m in api.meta_calls[len(before):]]
        assert {"division", "spid", "spposition", "seasonid"} <= set(user), f"보호 없을 때의 값: {user}"
        mark = len(api.meta_calls)
        got = []
        ld = app_main.MatchLoader(api, NICK, MT, offline_ouid=OUID, auto=True)
        ld.finished_ok.connect(lambda *a: got.append(a))
        ld.run()
        _app.processEvents()
        assert api.meta_calls[mark:] == [], api.meta_calls[mark:]
        assert got and got[0][4] and got[0][9] not in ("", "-"), "디스크 메타로 이름·등급을 못 채웠다"
        app_main._GAME["on"] = False
        mark = len(api.meta_calls)
        app_main.MatchLoader(api, NICK, MT, offline_ouid=OUID, auto=True).run()
        assert "division" in api.meta_calls[mark:], "게임이 꺼졌는데 auto 로더가 메타를 안 물었다"
    finally:
        app_main._GAME["on"] = False


# ── 켤 때 ───────────────────────────────────────────────────────────────────


def test_startup_opens_my_account_on_dashboard():
    api = _fresh()
    shell, win = _open()
    assert _quiet(win)
    assert win._ouid == OUID and len(win._matches_all) == N_ALL, (win._ouid, len(win._matches_all))
    assert api.searches() == 1, api.searches()
    assert _page(win) == "대시보드", _page(win)
    assert _homepage() == [], _homepage()[:3]
    assert api.count(nexon_api.EP_USER_TRADE) == 0, "켤 때 거래를 받았다"
    assert win._teamcolor_loader is None, "켤 때 팀컬러 로더가 떴다"
    assert not win._startup_auto, "켤 때 확인이 끝났는데 표시가 안 꺼졌다"
    assert _search_enabled(win)
    assert _chips(win)[0] == f"내 계정 · {NICK}", _chips(win)
    # 표시가 꺼진 뒤 사용자가 누른 것은 지금처럼 홈페이지에 묻는다(팀컬러 · 순위)
    win._api_search(NICK)
    assert _quiet(win)
    assert api.searches() == 2 and _homepage(), "사용자 검색이 켤 때 규칙에 묶였다"


def test_no_my_account_keeps_search_screen():
    api = _fresh(my=False)
    shell, win = _open()
    assert _quiet(win)
    assert api.calls == [] and _homepage() == [], (api.calls, _homepage())
    assert win.stack.currentIndex() == win.PAGE_SEARCH and not win._ouid
    assert not any(c.startswith("내 계정") for c in _chips(win)), _chips(win)


def test_notice_14_waits_then_opens_on_accept():
    api = _fresh()
    config.NOTICE_ACCEPTED = 14       # 기본값이 아닌 값 — 수집을 끈 14 동의자
    shell, win = _open()
    assert _quiet(win)
    assert api.calls == [] and win.stack.currentIndex() == win.PAGE_SEARCH, api.calls
    assert config.notice_update_pending(), "수집을 끈 14 동의자에게 다시 안 묻는다(U6)"
    assert FakeNotice.shown == 1, "창이 보일 때 다시 묻지 않았다"
    win.ask_notice_update()            # 취소 — 옛 동의 그대로
    assert _quiet(win) and api.calls == []
    FakeNotice.accept = True
    assert win.ask_notice_update()
    assert _quiet(win)
    assert api.searches() == 1 and win._ouid == OUID and _page(win) == "대시보드"
    assert _homepage() == []


def test_search_running_when_consent_arrives_is_kept():
    """검색이 도는 중에 [정보]에서 동의해도 그 검색을 덮어쓰지 않는다(3회차 M2)."""
    api = _fresh()
    config.NOTICE_ACCEPTED = 14
    shell, win = _open()
    assert _quiet(win)
    api.hold = threading.Event()
    win._api_search(NICK)
    ld = win._loader
    assert _pump(lambda: len(api.detail_started) >= 1)
    FakeNotice.accept = True
    win.ask_notice_update()
    assert win._loader is ld, "도는 검색을 켤 때 길이 바꿔 끼웠다"
    api.hold.set()
    assert _quiet(win)
    assert api.searches() == 1, api.searches()


def test_fail_path_turns_marker_off_and_frees_search():
    api = _fresh()
    api.fail_id = nexon_api.NexonAPIError("연결 실패", code=nexon_api.NETWORK_CODE)
    shell, win = _open()
    assert _quiet(win)
    assert win._ouid == OUID and api.searches() == 1
    assert not win._startup_auto, "실패 길에서 표시가 안 꺼졌다"
    assert _search_enabled(win)
    assert "새 경기를 확인하지 못해" in win.statusBar().currentMessage(), win.statusBar().currentMessage()


def test_visible_api_loader_resumes_after_startup_check():
    """켤 때 확인도 _yield_trades 로 랭커 픽·특성을 멈춘다 — 끝난 뒤 다시 켜는 짝이 있어야 한다(5회차 S1)."""
    api = _fresh()
    api.hold = threading.Event()
    seen = []
    real = app_main.MainWindow.start_visible_api_loader
    app_main.MainWindow.start_visible_api_loader = lambda self: (seen.append(time.monotonic()), real(self))[1]
    try:
        shell, win = _open()
        assert _pump(lambda: len(api.detail_started) >= 1)
        _pump(lambda: False, 0.3)
        seen.clear()                       # 창이 보일 때(showEvent) 부른 것은 뺀다
        api.hold.set()
        assert _quiet(win)
        assert seen, "켤 때 확인이 끝난 뒤 랭커 픽·특성을 다시 켜지 않았다"
    finally:
        app_main.MainWindow.start_visible_api_loader = real


# ── 게임 중(진입 · DB 읽기 뒤 · 확인 도중 · 끝) ──────────────────────────────────


def test_game_on_at_open_draws_only_then_checks_when_off():
    api = _fresh()
    app_main._GAME["on"] = True
    shell, win = _open()
    assert _quiet(win)
    assert win._ouid == OUID and len(win._matches_all) == N_SAVED
    assert api.calls == [] and api.meta_calls == [], (api.calls, api.meta_calls)
    assert "게임" in win.statusBar().currentMessage(), win.statusBar().currentMessage()
    assert _search_enabled(win)
    _game(shell, False)
    assert _quiet(win)
    assert api.searches() == 1 and len(win._matches_all) == N_ALL
    assert _homepage() == []


def test_late_season_table_on_startup_path_keeps_homepage_zero():
    """켤 때 길(_startup_auto)에서 시즌표가 늦게 와 고른 시즌이 바뀌면 _apply_season 이 다시 그린다 — 그때 팀컬러는
    캐시만(U5). 대조: 표시를 끄고 같은 _apply_season 을 부르면 홈페이지 로더가 뜬다(막지 않았으면 일어났을 것)."""
    _fresh()
    app_main._GAME["on"] = True      # 확인을 남겨 _startup_auto 가 켜진 채로 둔다
    shell, win = _open()
    assert _quiet(win)
    assert win._startup_auto and win._ouid == OUID
    win._rank_seasons = []           # 시즌표 없이 켠 것처럼 — 콤보는 번호 줄(DataSeason)뿐
    win._season_picked = False
    win._rebuild_season_combo()
    win._apply_season(render=False)
    before = win._season_key(win.cb_season.currentData())
    win._on_seasons_loaded(list(DATA.seasons))
    assert win._season_key(win.cb_season.currentData()) != before, "시즌표가 와도 고른 줄이 안 바뀌었다(검사가 비었다)"
    assert win._teamcolor_loader is None, "켤 때 길에서 시즌표 도착이 팀컬러 홈페이지 조회를 띄웠다"
    win._startup_auto = False
    win._apply_season()
    assert win._teamcolor_loader is not None, "대조: 표시가 꺼지면 팀컬러 조회가 떠야 한다"
    assert _quiet(win)
    app_main._GAME["on"] = False


def test_game_on_during_db_read_skips_check():
    api = _fresh()
    gate = threading.Event()

    def slow_load(*a, **k):
        gate.wait(30)
        return _REAL_LOAD_SAVED(*a, **k)

    app_main.load_saved = slow_load
    shell, win = _open()
    assert _pump(lambda: win._loader is not None and win._loader.isRunning())
    _game(shell, True)
    gate.set()
    assert _quiet(win)
    assert win._ouid == OUID and len(win._matches_all) == N_SAVED, "DB 읽기가 게임 때문에 취소됐다"
    assert api.calls == [], api.calls
    _game(shell, False)
    assert _quiet(win)
    assert api.searches() == 1


def test_game_on_during_check_cancels_keeps_marker_and_frees_search():
    api = _fresh()
    api.hold = threading.Event()
    shell, win = _open()
    assert _pump(lambda: len(api.detail_started) >= app_main.DETAIL_WORKERS)
    _pump(lambda: False, 0.2)
    _game(shell, True)
    started = len(api.detail_started)
    assert started == app_main.DETAIL_WORKERS, started
    api.hold.set()
    assert _quiet(win)
    assert len(api.detail_started) == started, "취소 뒤 새 상세 요청을 시작했다"
    assert win._startup_auto, "게임 때문에 취소한 길에서 표시를 껐다"
    assert _search_enabled(win), "취소한 뒤 검색 칸이 꺼진 채다(4회차 S2)"
    assert "게임" in win.statusBar().currentMessage(), win.statusBar().currentMessage()
    assert api.searches() == 1
    # 게임 중 사용자 검색 — 그대로 돌고(취소 안 함) 메타는 get_meta
    mark = len(api.meta_calls)
    win._api_search(NICK)
    assert not win._startup_auto, "사용자 검색이 시작됐는데 표시가 남았다"
    assert _quiet(win)
    assert api.searches() == 2 and len(win._matches_all) == N_ALL
    assert "division" in api.meta_calls[mark:], api.meta_calls[mark:]
    _game(shell, False)
    assert _quiet(win) and api.searches() == 2, "사용자가 이미 검색했는데 켤 때 확인을 또 했다"


def test_game_off_before_cancelled_loader_ends_still_checks_once():
    """취소한 로더가 상세를 끝낼 때까지 살아 있는 사이 게임이 꺼진다 — 그 finished 에서 다시 한 번(4회차 M)."""
    api = _fresh()
    api.hold = threading.Event()
    shell, win = _open()
    assert _pump(lambda: len(api.detail_started) >= app_main.DETAIL_WORKERS)
    _game(shell, True)
    _game(shell, False)                       # 로더가 아직 돈다 — 여기서는 못 띄운다
    assert win._loader.isRunning()
    api.hold.set()
    assert _quiet(win)
    assert api.searches() == 2, api.searches()
    assert len(win._matches_all) == N_ALL
    # 앞에서 받은 상세는 디스크 캐시라 다시 안 묻는다 — 상세 요청은 새 경기 수 그대로
    assert api.count(nexon_api.EP_MATCH_DETAIL) == N_NEW, api.count(nexon_api.EP_MATCH_DETAIL)
    assert not win._startup_auto and _search_enabled(win)


def test_game_on_while_hidden_then_off_waits_for_show():
    api = _fresh()
    app_main._GAME["on"] = True
    shell, win = _open()
    assert _quiet(win)
    shell.hide_window()
    _game(shell, False)
    assert _quiet(win) and api.calls == [], "숨긴 채로 확인했다"
    shell.show_window()
    assert _quiet(win)
    assert api.searches() == 1


def test_release_then_game_off_then_show_checks_after_reload():
    """메모리를 내려놓은 뒤 게임이 꺼지고 창을 보인다 — 다시 읽기(offline)가 **끝난 뒤** 한 번(3회차 M3 · 4회차 S3)."""
    api = _fresh()
    app_main._GAME["on"] = True
    shell, win = _open()
    assert _quiet(win)
    shell.hide_window()
    win.release_memory()
    assert win._released
    _game(shell, False)
    assert _quiet(win) and api.calls == []
    shell.show_window()
    assert _quiet(win)
    assert not win._released and api.searches() == 1, (win._released, api.searches())
    assert len(win._matches_all) == N_ALL


def test_release_while_cancelled_loader_runs_reloads_and_checks():
    """취소했지만 안 끝난 확인 로더가 도는 사이 내려놓고 다시 보인다 — 다시 읽기도 그 뒤 확인도 해야 한다(5회차 M5)."""
    api = _fresh()
    api.hold = threading.Event()
    shell, win = _open()
    assert _pump(lambda: len(api.detail_started) >= app_main.DETAIL_WORKERS)
    _game(shell, True)
    shell.hide_window()
    win.release_memory()
    _game(shell, False)
    shell.show_window()
    assert win._loader.isRunning()
    api.hold.set()
    assert _quiet(win)
    assert not win._released, "다시 읽기를 건너뛰었다"
    assert len(win._matches_all) == N_ALL, len(win._matches_all)
    assert api.searches() == 2


# ── 창이 안 보일 때(최소화 · 트레이) ─────────────────────────────────────────


def test_minimized_start_waits_for_restore():
    api = _fresh()

    def minimized(self):
        self._shown_once = True
        self.showMinimized()

    app_main.MainWindow.show_initial = minimized
    shell, win = _open()
    assert _quiet(win)
    assert win.isMinimized() and api.calls == [], api.calls
    win.showNormal()
    assert _quiet(win)
    assert api.searches() == 1 and win._ouid == OUID


def test_tray_start_checks_on_first_show_only():
    api = _fresh()
    shell, win = _open(show=False)
    assert _quiet(win)
    assert api.calls == [] and not win.isVisible()
    shell.show_window()
    assert _quiet(win)
    assert api.searches() == 1 and win._ouid == OUID
    shell.hide_window()
    shell.show_window()
    assert _quiet(win) and api.searches() == 1, "두 번째로 열 때도 확인했다"


def test_tray_show_inactive_then_restore():
    api = _fresh()
    shell, win = _open(show=False)
    assert _quiet(win)
    shell.show_inactive()
    assert _quiet(win) and api.calls == [], "최소화로 띄운 채 확인했다"
    win.showNormal()
    assert _quiet(win)
    assert api.searches() == 1


# ── 칩 ─────────────────────────────────────────────────────────────────────


def test_my_account_chip_follows_setting_and_wipe():
    api = _fresh(my=False)
    shell, win = _open()
    assert _quiet(win)
    assert not any(c.startswith("내 계정") for c in _chips(win))
    win._set_my_account(OUID)
    assert _chips(win)[0] == f"내 계정 · {NICK}", _chips(win)
    conn = store.open_db(config.DB_PATH)
    try:
        store.set_trade_state(conn, my_ouid=None)    # 키가 바뀌어 거래 받기가 지운 것처럼
    finally:
        conn.close()
    win._on_trades_wiped()
    assert not any(c.startswith("내 계정") for c in _chips(win)), _chips(win)
    assert api.calls == []


def test_my_account_needs_saved_matches():
    """내 계정은 그 경기 종류의 저장 경기가 1판 이상일 때만 — 0판이면 켤 때 열 게 없다(빈 대시보드로 가지 않게)."""
    _fresh()
    conn = store.open_db(config.DB_PATH)
    try:
        got = store.my_account(conn, MT)
        assert got is not None and got["ouid"] == OUID, f"대조: 저장 경기가 있는 종류면 내 계정이 나와야 한다 {got}"
        empty = MT + 1000
        assert not store.match_count(conn, OUID, empty)
        assert store.my_account(conn, empty) is None, "저장 경기 0판인데 내 계정이 나왔다"
    finally:
        conn.close()


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__), watchdog.slot_errors():
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    _close_all()
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
