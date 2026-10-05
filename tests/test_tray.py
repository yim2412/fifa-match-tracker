"""트레이 상주(1.1.1) — 화면 없이 도는 부분: 자동 실행(가짜 winreg) · 한 번만 실행 판정 · 종료 진입점 · 스레드 마무리.

창(MainWindow)과 엮이는 부분(X 숨김·내려놓기·업데이트·예외)은 test_ui_smoke.py 에 있다.
`python tests/test_tray.py` 로 실행.
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PyQt6.QtCore import QThread  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import tempfile  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import autostart  # noqa: E402
import config  # noqa: E402
import tray  # noqa: E402

# 실제 설정·rank.db 를 건드리지 않게(알림 하루 한 번 기록이 rank.db 에 간다) · 테스트 도중 게임을 켜 둬도 결과가 같게
_TMP = Path(tempfile.mkdtemp(prefix="test_tray_"))
config.SETTINGS_PATH = _TMP / "settings.ini"
config.RANK_DB_PATH = _TMP / "rank.db"
_REAL_USER_BUSY = tray.user_busy
tray.user_busy = lambda: False


# ── 자동 실행 ─────────────────────────────────────────────────────────
class FakeReg:
    """winreg 흉내 — HKCU Run 키 하나만."""
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self):
        self.values: dict[str, str] = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path, res, access):
        assert root == "HKCU" and path == autostart.RUN_KEY, (root, path)
        return self._Key()

    CreateKeyEx = OpenKey

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, k, name, res, typ, val):
        self.values[name] = val

    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def _with_install(installed: bool):
    orig = autostart.updatecheck.install_dir
    autostart.updatecheck.install_dir = (lambda: "C:/x") if installed else (lambda: None)
    return orig


def test_autostart_round_trip_with_korean_path():
    reg, exe = FakeReg(), r"C:\Users\준\AppData\Local\Programs\피파 전적\피파전적관리.exe"
    orig = _with_install(True)
    try:
        assert not autostart.is_enabled(reg)
        autostart.set_enabled(True, reg=reg, exe=exe)
        cmd = reg.values[autostart.VALUE_INSTALLED]
        assert cmd == f'"{exe}" --tray', cmd   # 큰따옴표 — 공백 경로가 둘로 갈리지 않게
        assert autostart.exe_of(cmd) == exe
        assert autostart.is_enabled(reg)
        autostart.set_enabled(False, reg=reg)
        assert not reg.values and not autostart.is_enabled(reg)
        autostart.set_enabled(False, reg=reg)  # 이미 꺼져 있어도 오류 없음
    finally:
        autostart.updatecheck.install_dir = orig


def test_autostart_installed_and_portable_use_different_names():
    # 둘 다 쓰는 PC — 포터블을 켜고 끈다고 설치판 등록이 바뀌면 안 된다(제거기는 설치판 값만 지운다)
    reg = FakeReg()
    orig = _with_install(True)
    try:
        autostart.set_enabled(True, reg=reg, exe="C:/inst/a.exe")
        autostart.updatecheck.install_dir = lambda: None
        autostart.set_enabled(True, reg=reg, exe="D:/port/a.exe")
        autostart.set_enabled(False, reg=reg)
        assert reg.values == {autostart.VALUE_INSTALLED: '"C:/inst/a.exe" --tray'}, reg.values
    finally:
        autostart.updatecheck.install_dir = orig
    assert autostart.VALUE_INSTALLED != autostart.VALUE_PORTABLE


def test_autostart_repair_only_when_path_is_gone():
    reg = FakeReg()
    orig = _with_install(False)
    try:
        assert autostart.repair(reg=reg, exe="D:/new.exe") is False  # 꺼져 있으면 아무것도 안 한다(켜지 않는다)
        assert not reg.values
        reg.values[autostart.VALUE_PORTABLE] = '"D:/old.exe" --tray'
        assert autostart.repair(reg=reg, exe="D:/new.exe", exists=lambda p: True) is False
        assert reg.values[autostart.VALUE_PORTABLE] == '"D:/old.exe" --tray', "있는 경로를 덮어썼다"
        assert autostart.repair(reg=reg, exe="D:/new.exe", exists=lambda p: p != "D:/old.exe") is True
        assert reg.values[autostart.VALUE_PORTABLE] == '"D:/new.exe" --tray'
    finally:
        autostart.updatecheck.install_dir = orig


def test_autostart_off_in_source_run():
    # 소스 실행은 python.exe 를 등록하게 된다 — 막는다
    assert not getattr(sys, "frozen", False)
    assert autostart.available() is False and autostart.is_enabled() is False
    try:
        autostart.set_enabled(True)
        raise AssertionError("소스 실행에서 자동 실행을 등록했다")
    except OSError:
        pass


# ── 한 번만 실행 ──────────────────────────────────────────────────────
def test_instance_name_depends_on_user_and_path():
    a = tray.instance_name("준", r"C:\a\x.exe")
    assert a == tray.instance_name("준", r"c:\A\X.EXE"), "대소문자만 다른 같은 경로"
    assert a != tray.instance_name("other", r"C:\a\x.exe"), "다른 윈도우 사용자와 겹친다(파이프는 PC 전체에 하나)"
    assert a != tray.instance_name("준", r"D:\a\x.exe"), "설치판·포터블이 서로 막는다"


def _single(mutex_results, ping_results, wait_s=60):
    t = [0.0]
    calls = {"ping": 0, "listen": 0}
    mres, pres = list(mutex_results), list(ping_results)

    def mutex(name):
        existed = mres.pop(0)
        return (None if existed else "h"), existed

    def ping_fn(name):
        calls["ping"] += 1
        return pres.pop(0)

    def sleep(s):
        t[0] += s

    si = tray.SingleInstance("test", mutex=mutex, ping_fn=ping_fn, sleep=sleep, clock=lambda: t[0])
    si._listen = lambda: calls.__setitem__("listen", calls["listen"] + 1)
    return si, si.claim(wait_s), calls, t


def test_single_first_instance_becomes_owner():
    si, ok, calls, _t = _single([False], [])
    assert ok and calls == {"ping": 0, "listen": 1} and si._handle == "h", calls


def test_single_second_instance_shows_first_and_exits():
    _si, ok, calls, _t = _single([True], ["ok"])
    assert ok is False and calls["listen"] == 0, calls


def test_single_waits_while_old_one_is_quitting():
    # 업데이트 뒤 재실행 — 옛 프로세스가 끝나는 중(또는 정리하느라 답이 늦음)이면 끝날 때까지 기다렸다 주인이 된다
    _si, ok, calls, t = _single([True, True, True, False], ["quitting", "busy", None])
    assert ok and calls["listen"] == 1 and t[0] == 1.5, (calls, t)


def test_single_gives_up_after_wait_limit():
    _si, ok, calls, t = _single([True] * 10, ["quitting"] * 10, wait_s=2)
    assert ok is False and calls["listen"] == 0 and t[0] == 2.0, (calls, t)


def test_single_server_answers_quitting_and_does_not_activate():
    si = tray.SingleInstance("x")
    seen = []
    si.activated.connect(lambda: seen.append(1))

    class _Sock:
        def __init__(self):
            self.out = b""

        def readAll(self):
            return b"show\n"

        def write(self, b):
            self.out += b

        def flush(self):
            pass

    for quitting, reply, n in ((False, b"ok", 1), (True, b"quitting", 1)):
        si.is_quitting = lambda q=quitting: q
        s = _Sock()
        si._on_message(s)
        assert s.out == reply and len(seen) == n, (quitting, s.out, seen)


def test_single_real_mutex_detects_second_claim():
    # 윈도우에선 같은 이름 QLocalServer 의 두 번째 listen 도 성공한다(실측) — 그래서 뮤텍스로 판정한다
    if sys.platform != "win32":
        return
    name = f"FifaMatchTracker-test-{os.getpid()}"
    h1, e1 = tray._win_mutex(name)
    h2, e2 = tray._win_mutex(name)
    assert h1 and not e1, (h1, e1)
    assert e2 and not h2, (h2, e2)


# ── 종료 진입점 ───────────────────────────────────────────────────────
class _Thread:
    def __init__(self, ends_after_s: float | None):
        self.ends_after = ends_after_s
        self.t0 = time.monotonic()
        self.terminated = False

    def isRunning(self):
        return not self.terminated and (self.ends_after is None or time.monotonic() - self.t0 < self.ends_after)

    def wait(self, ms):
        end = time.monotonic() + ms / 1000
        while self.isRunning() and time.monotonic() < end:
            time.sleep(0.01)
        return not self.isRunning()

    def terminate(self):
        self.terminated = True


def test_finish_threads_shares_one_budget_then_terminates():
    stuck = [_Thread(None), _Thread(None), _Thread(None)]
    quick = _Thread(0.05)
    t0 = time.monotonic()
    tray.finish_threads(stuck + [quick, None], 300)
    took = time.monotonic() - t0
    assert took < 0.8, f"스레드마다 따로 기다렸다 — {took:.2f}초"  # 셋이 각자 300ms 면 0.9초+
    assert all(t.terminated for t in stuck) and not quick.terminated


class _FakeWin:
    def __init__(self, visible=True):
        self.calls, self._visible, self._quitting = [], visible, False
        self._released, self._shown_once = False, True

    def shutdown(self, fast=False):
        self.calls.append(("shutdown", fast, self._quitting))
        return []

    def isVisible(self):
        return self._visible

    def isMinimized(self):
        return False

    def close(self):
        self.calls.append(("close", self._quitting))

    def hide(self):
        self._visible = False
        self.calls.append("hide")

    def attach_rank_sched(self, s):
        self.calls.append("sched")


class _FakeSched:
    def __init__(self):
        self.calls = []

    def shutdown(self, fast=False):
        self.calls.append(fast)
        return []

    def running(self):
        return False

    def start(self):
        pass


class _FakeTray:
    def __init__(self):
        self.visible, self.msgs = True, []

    def isVisible(self):
        return self.visible

    def hide(self):
        self.visible = False

    def showMessage(self, title, text, *a):
        self.msgs.append(title)


def _shell(visible=True):
    sh = tray.AppShell(_app, sched=_FakeSched())
    w = _FakeWin(visible)
    sh.attach_window(w)
    sh.tray = _FakeTray()
    quits = []
    sh.app = type("A", (), {"quit": lambda self: quits.append(1)})()
    return sh, w, quits


def test_quit_app_cleans_up_once_then_quits():
    for visible in (True, False):
        sh, w, quits = _shell(visible)
        sh.quit_app()
        sh.quit_app()  # 두 번 불려도(트레이 [종료] 뒤 commitData) 정리는 한 번
        # 창 정리는 _quitting 을 세운 뒤 — closeEvent 가 다시 불려도 숨김·정리를 안 하게
        assert w.calls[1] == ("shutdown", False, True), w.calls  # [0] 은 attach 의 "sched"
        assert sh.sched.calls == [False] and quits == [1] and not sh.tray.visible
        # 보이는 창은 닫아 준다(받기만 하는 closeEvent) · 숨긴 창은 그대로(숨긴 상태 종료에서도 정리는 돌았다)
        assert (("close", True) in w.calls) is visible, w.calls


def test_commit_data_quits_fast():
    sh, w, quits = _shell()
    sh._on_commit_data(None)
    assert w.calls[1] == ("shutdown", True, True) and sh.sched.calls == [True] and quits == [1], w.calls


def test_fast_quit_finishes_left_threads_within_budget():
    # 윈도우 종료 — 창·수집이 넘긴 '아직 도는 스레드'를 합계 FAST_QUIT_WAIT_S 만 기다리고 끝낸다
    sh, w, quits = _shell()
    stuck_w, stuck_s = _Thread(None), _Thread(None)
    w.shutdown = lambda fast=False: [stuck_w] if fast else []
    sh.sched.shutdown = lambda fast=False: [stuck_s] if fast else []
    t0 = time.monotonic()
    sh.quit_app(fast=True)
    took = time.monotonic() - t0
    assert stuck_w.terminated and stuck_s.terminated, "남은 스레드를 끝내지 않았다"
    assert took < config.FAST_QUIT_WAIT_S + 0.5, f"{took:.2f}초 — 종료 요청을 오래 붙잡았다"
    assert quits == [1]


def test_should_hide_rule_d1():
    keep = config.RANK_COLLECT, config.WEB_DATA, autostart.is_enabled
    try:
        sh, _w, _q = _shell()
        autostart.is_enabled = lambda: False
        for collect, web, want in ((True, True, True), (True, False, False), (False, True, False)):
            config.RANK_COLLECT, config.WEB_DATA = collect, web
            assert sh.should_hide() is want, (collect, web)
        config.RANK_COLLECT = False
        autostart.is_enabled = lambda: True
        assert sh.should_hide() is True, "자동 실행이 켜져 있으면 숨긴다"
        sh.tray = None
        assert sh.should_hide() is False, "트레이가 없는데 숨기면 다시 열 길이 없다"
    finally:
        config.RANK_COLLECT, config.WEB_DATA, autostart.is_enabled = keep


def test_hide_notifies_once_and_starts_release_timer():
    import tempfile
    from pathlib import Path
    keep = config.SETTINGS_PATH
    config.SETTINGS_PATH = Path(tempfile.mkdtemp()) / "settings.ini"
    try:
        sh, w, _q = _shell()
        sh.hide_window()
        assert "hide" in w.calls and sh._release_timer.isActive()
        assert sh._release_timer.interval() == config.RELEASE_AFTER_HIDE_MIN * 60 * 1000
        w._visible = True
        sh.hide_window()
        assert len(sh.tray.msgs) == 1, f"숨김 안내가 매번 뜬다: {sh.tray.msgs}"
    finally:
        config.SETTINGS_PATH = keep


def test_release_waits_while_busy():
    sh, w, _q = _shell(visible=False)
    released = []
    w.release_memory = lambda: released.append(1)
    w.busy_for_release = lambda: True
    sh._release_due()
    assert not released and sh._release_timer.interval() == config.RELEASE_RETRY_MIN * 60 * 1000
    w.busy_for_release = lambda: False
    sh.sched.running = lambda: True   # 수집 중에도 미룬다
    sh._release_due()
    assert not released
    sh.sched.running = lambda: False
    sh._release_due()
    assert released == [1]
    w._visible = True
    sh._release_due()
    assert released == [1], "보이는 창의 기록을 내려놓았다"


def test_update_timer_respects_switch_and_consent():
    keep = config.UPDATE_CHECK, config.NOTICE_ACCEPTED
    try:
        for on in (True, False):
            config.UPDATE_CHECK = on
            sh, w, _q = _shell()
            sh.start()
            assert sh._update_timer.isActive() is on, on
            assert sh._update_timer.interval() == config.UPDATE_CHECK_EVERY_H * 3600 * 1000
            sh._update_timer.stop()
        seen = []
        w.start_update_check = lambda: seen.append(1)
        config.UPDATE_CHECK, config.NOTICE_ACCEPTED = True, config.NOTICE_VERSION - 1
        sh._update_due()
        assert not seen, "동의 전에 GitHub 에 물었다"
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        sh._update_due()
        assert seen == [1]
    finally:
        config.UPDATE_CHECK, config.NOTICE_ACCEPTED = keep


def test_tray_wait_then_inactive_window_or_keep_waiting():
    keep = tray.QSystemTrayIcon.isSystemTrayAvailable
    tray.QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: False)
    try:
        for has_window in (True, False):
            sh = tray.AppShell(_app)
            shown = []
            if has_window:
                sh.window = _FakeWin(visible=False)
                sh.show_inactive = lambda: shown.append(1)
            sh.start(tray_mode=True)
            assert sh._tray_timer.isActive() and sh._tray_timer.interval() == config.TRAY_POLL_S * 1000
            for _ in range(config.TRAY_WAIT_S // config.TRAY_POLL_S):
                sh._retry_tray()
            if has_window:
                assert shown == [1] and not sh._tray_timer.isActive()
            else:
                # 동의가 필요해 창이 없다 — 막는 창을 게임 위에 띄우지 않고 1분마다 다시 본다
                assert sh._tray_timer.isActive() and sh._tray_timer.interval() == config.TRAY_RETRY_MIN * 60 * 1000
            sh._tray_timer.stop()
            sh._update_timer.stop()
    finally:
        tray.QSystemTrayIcon.isSystemTrayAvailable = keep


def test_crash_while_hidden_goes_to_tray_then_modal_on_open():
    sh, w, _q = _shell(visible=False)
    modal = []
    sh.crash_modal = modal.append
    w.show, w.raise_, w.activateWindow = (lambda: None), (lambda: None), (lambda: None)
    sh.crash_while_hidden("crash.log")
    assert sh.tray.msgs == ["예기치 못한 오류"] and not modal
    sh.show_window()
    assert modal == ["crash.log"]
    sh.show_window()
    assert modal == ["crash.log"], "안내가 두 번 떴다"
    # 오류 알림도 같은 길 — 게임 중이면 미루고 끝난 뒤에(풍선을 직접 띄우면 게임 위에 뜬다)
    sh, w, _q = _shell(visible=False)
    busy = [True]
    sh.notifier._busy = lambda: busy[0]
    sh.crash_while_hidden("crash.log")
    assert sh.tray.msgs == [] and "crash" in sh.notifier.pending, "게임 중인데 오류 알림을 바로 띄웠다"
    busy[0] = False
    sh.notifier.flush()
    assert sh.tray.msgs == ["예기치 못한 오류"]
    sh._update_timer.stop()


# ── 알림(8절) ─────────────────────────────────────────────────────────
class _Box:
    """Notifier 의 바깥 — 띄운 알림·게임 중 여부·켜짐·하루 기록을 손으로 쥔다."""

    def __init__(self, busy=False, enabled=True, ready=True, claim=None):
        self.shown, self.busy, self.enabled, self.ready = [], busy, enabled, ready
        self.day = date(2026, 10, 5)
        self.n = tray.Notifier(lambda t, x: self.shown.append(t) or True, ready=lambda: self.ready,
                               busy=lambda: self.busy, enabled=lambda: self.enabled,
                               today=lambda: self.day, claim_daily=claim)


def _rank_db():
    return Path(tempfile.mkdtemp(prefix="notify_")) / "rank.db"


def test_notifier_defers_while_gaming_then_shows_latest_once():
    b = _Box(busy=True)
    assert b.n.post("update", "v1", "") == "deferred" and b.n.post("update", "v2", "") == "deferred"
    assert b.n.post("crash", "오류", "") == "deferred"
    assert b.shown == [] and b.n._timer.isActive(), "게임 중인데 띄웠거나 다시 볼 타이머가 없다"
    assert b.n._timer.interval() == config.NOTIFY_RETRY_S * 1000
    b.n.flush()
    assert b.shown == [], "아직 게임 중인데 띄웠다"
    b.busy = False
    b.n.flush()
    assert b.shown == ["v2", "오류"], b.shown          # 종류마다 마지막 것 하나씩
    assert not b.n._timer.isActive() and b.n.pending == {}
    b.n.flush()
    assert b.shown == ["v2", "오류"], "두 번 띄웠다"
    # 미뤄 둔 사이 알림을 끄면 끝난 뒤에도 안 띄운다
    b2 = _Box(busy=True)
    b2.n.post("update", "v3", "")
    b2.enabled, b2.busy = False, False
    b2.n.flush()
    assert b2.shown == []


def test_notifier_daily_once_across_two_instances():
    import rankcollect
    db = _rank_db()
    claim = lambda k, d: rankcollect.claim_daily_notice(k, d, db)  # noqa: E731
    a, b = _Box(claim=claim), _Box(claim=claim)   # 설치판·포터블이 같은 rank.db 를 본다
    assert a.n.post("update", "새 버전", "", daily=True) == "shown"
    assert b.n.post("update", "새 버전", "", daily=True) == "skipped", "다른 실행본이 같은 날 또 알렸다"
    assert a.n.post("update", "새 버전", "", daily=True) == "skipped"
    assert a.n.post("rank_fail", "실패", "", daily=True) == "shown", "종류가 다르면 따로 센다"
    assert a.n.post("crash", "오류", "") == "shown" and a.n.post("crash", "오류", "") == "shown", "daily 아닌데 막았다"
    a.day = b.day = date(2026, 10, 6)
    assert b.n.post("update", "새 버전", "", daily=True) == "shown", "다음 날인데 안 알렸다"
    assert a.shown == ["새 버전", "실패", "오류", "오류"] and b.shown == ["새 버전"]


def test_notifier_off_no_tray_and_broken_record():
    b = _Box(enabled=False, busy=True)
    assert b.n.post("update", "x", "") == "off" and b.n.pending == {}, "꺼졌는데 미뤄 뒀다"
    claimed = []
    b = _Box(ready=False, claim=lambda k, d: claimed.append(k) or True)
    assert b.n.post("update", "x", "", daily=True) == "no-tray"
    assert claimed == [], "트레이가 없어 못 띄웠는데 오늘 몫을 써 버렸다"

    def broken(k, d):
        raise OSError("rank.db 잠김")
    b = _Box(claim=broken)
    assert b.n.post("update", "x", "", daily=True) == "shown", "기록을 못 남겼다고 알림을 버렸다"


def test_user_busy_answers_without_error():
    assert _REAL_USER_BUSY() in (True, False)


class _OutcomeSched(_FakeSched):
    def __init__(self):
        super().__init__()
        from PyQt6.QtCore import QObject, pyqtSignal

        class S(QObject):
            outcome = pyqtSignal(object)
        self._s = S()
        self.outcome = self._s.outcome


def test_collect_outcome_notifies_only_while_hidden():
    from types import SimpleNamespace as O
    for visible in (True, False):
        sh = tray.AppShell(_app, sched=_OutcomeSched())
        sh.attach_window(_FakeWin(visible))
        sh.tray = _FakeTray()
        sh.notifier._claim = lambda k, d: True
        sh.sched.outcome.emit(O(kind="blocked", message="403", disabled_by_block=True, fail_notice=False))
        sh.sched.outcome.emit(O(kind="failed", message="점검", disabled_by_block=False, fail_notice=True))
        sh.sched.outcome.emit(O(kind="failed", message="한 번", disabled_by_block=False, fail_notice=False))
        sh.sched.outcome.emit(O(kind="ok", rows=10000, message="", disabled_by_block=False, fail_notice=False))
        want = [] if visible else ["랭킹 수집을 껐습니다", "랭킹 수집이 계속 실패합니다"]
        assert sh.tray.msgs == want, (visible, sh.tray.msgs)
        sh._update_timer.stop()


def test_tray_mode_without_window_asks_for_consent_and_click_opens():
    keep = tray.QSystemTrayIcon.isSystemTrayAvailable
    tray.QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
    try:
        for tray_mode, want in ((True, ["확인이 필요합니다"]), (False, [])):
            sh = tray.AppShell(_app)
            shown, opened = [], []
            sh.notifier._show = lambda t, x: shown.append(t) or True
            sh.notifier._claim = lambda k, d: True
            sh.show_window = lambda: opened.append(1)
            sh.start(tray_mode=tray_mode)
            assert shown == want, (tray_mode, shown)
            sh.tray.messageClicked.emit()
            assert opened == [1], "알림을 눌러도 창을 열지 않았다"
            sh.tray.hide()
            sh._update_timer.stop()
    finally:
        tray.QSystemTrayIcon.isSystemTrayAvailable = keep


def test_notify_toggle_round_trip():
    assert tray.notify_enabled(), "기본은 켬"
    tray.set_notify_enabled(False)
    try:
        assert not tray.notify_enabled()
        sh = tray.AppShell(_app)
        sh.tray = _FakeTray()
        assert sh.post("crash", "오류", "") == "off" and sh.tray.msgs == []
    finally:
        tray.set_notify_enabled(True)
    assert tray.notify_enabled()


def test_constants_sane():
    assert config.TRAY_WAIT_S % config.TRAY_POLL_S == 0
    assert config.FAST_QUIT_WAIT_S <= 2, "윈도우 종료를 오래 붙잡으면 '종료를 막고 있습니다'가 뜬다"


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
