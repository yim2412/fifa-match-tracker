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

import autostart  # noqa: E402
import config  # noqa: E402
import tray  # noqa: E402


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


def test_single_server_quit_message_requests_quit_without_activating():
    si = tray.SingleInstance("x")
    shown, quit_req = [], []
    si.activated.connect(lambda: shown.append(1))
    si.quit_requested.connect(lambda: quit_req.append(1))

    class _Sock:
        out = b""

        def readAll(self):
            return b"quit\n"

        def write(self, b):
            _Sock.out += b

        def flush(self):
            pass

    si._on_message(_Sock())
    assert quit_req == [1] and shown == [] and _Sock.out == b"ok", (quit_req, shown, _Sock.out)


def test_request_quit_does_nothing_when_not_running():
    sent = []
    assert tray.request_quit("x", exists=lambda n: False, send=sent.append) == "none"
    assert sent == [], "떠 있지 않은데 종료를 보냈다"


def test_request_quit_waits_until_owner_is_gone_or_limit():
    alive = {"n": 3}  # 보낸 뒤 세 번 더 '있음'으로 보이다 끝난다

    def exists(_n):
        if not sent:
            return True
        alive["n"] -= 1
        return alive["n"] >= 0

    sent, slept = [], []
    assert tray.request_quit("x", wait_s=10, exists=exists, send=sent.append, sleep=slept.append) == "done"
    assert sent == ["x"] and len(slept) == 3, (sent, slept)
    t = {"now": 0.0}

    def tick(s):
        t["now"] += s

    assert tray.request_quit("x", wait_s=1, exists=lambda n: True, send=lambda n: None,
                             sleep=tick, clock=lambda: t["now"]) == "timeout"
    assert 1 <= t["now"] < 1.5, t


def test_request_quit_real_mutex_seen_and_released():
    # 실제 뮤텍스 — 만들지 않고 있는지만 보는 함수가 주인이 있을 때만 참인지
    if sys.platform != "win32":
        return
    import ctypes
    name = f"FifaMatchTracker-quit-{os.getpid()}"
    assert not tray._win_mutex_exists(name), "없는 뮤텍스를 있다고 본다"
    h, existed = tray._win_mutex(name)
    assert h and not existed
    assert tray._win_mutex_exists(name), "주인이 있는데 못 본다"
    released = []

    def send(n):
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
        released.append(n)

    assert tray.request_quit(name, wait_s=3, send=send) == "done" and released == [name]


def test_shell_quits_fast_when_uninstaller_asks():
    si = tray.SingleInstance("x")
    sh = tray.AppShell(_app, sched=_FakeSched(), single=si)
    w = _FakeWin(False)
    sh.attach_window(w)
    sh.tray = _FakeTray()
    quits = []
    sh.app = type("A", (), {"quit": lambda self: quits.append(1)})()
    si.quit_requested.emit()
    assert w.calls[1] == ("shutdown", True, True) and quits == [1], (w.calls, quits)


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


def test_hide_starts_release_timer_without_balloon():
    sh, w, _q = _shell()
    sh.hide_window()
    assert "hide" in w.calls and sh._release_timer.isActive()
    assert sh._release_timer.interval() == config.RELEASE_AFTER_HIDE_MIN * 60 * 1000
    assert sh.tray.msgs == [], f"트레이 알림을 띄웠다(사용자: 알림 안 보냄): {sh.tray.msgs}"


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


def test_crash_while_hidden_waits_for_open_then_modal():
    sh, w, _q = _shell(visible=False)
    modal = []
    sh.crash_modal = modal.append
    w.show, w.raise_, w.activateWindow = (lambda: None), (lambda: None), (lambda: None)
    sh.crash_while_hidden("crash.log")
    assert sh.tray.msgs == [] and not modal, "숨긴 중에 알림이나 안내 창을 띄웠다"
    sh.show_window()
    assert modal == ["crash.log"]
    sh.show_window()
    assert modal == ["crash.log"], "안내가 두 번 떴다"


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
