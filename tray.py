"""트레이 상주(1.1.1) — 트레이 아이콘 · 종료 진입점 하나(`AppShell.quit_app`) · 한 번만 실행 · 숨긴 창 기록 내려놓기.

종료 진입점 표(ROADMAP 1.1.1 P1) — 모든 "종료"는 quit_app 하나를 거친다:

| 진입점 | 창 보임 | 창 숨김 |
|---|---|---|
| X — 숨김 조건(should_hide) | 숨김(처음 한 번 알림) | — |
| X — 그 밖(트레이 없음 포함) | quit_app() | — |
| 트레이 [종료] | quit_app() | quit_app() |
| 앱 안 [업데이트] | quit_app() | (숨긴 상태엔 버튼 없음) |
| 윈도우 종료·로그오프·설치기 | commitDataRequest → quit_app(fast=True) | 같음 |
| 처리 안 된 예외 | 기록 + 안내 창(app_main) | 기록 + 트레이 알림, 창을 열 때 안내 |

⚠ app.quit() 은 보이는 창의 closeEvent 를 한 번 더 부른다(PyQt 6.11 실측) → 창은 `_quitting` 이면 숨김·정리 없이 받기만.
정리는 closeEvent 가 아니라 quit_app 에서 한다 — 숨긴 상태로 끝낼 때도 돌게.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import autostart
import config

HIDE_NOTICE_KEY = "tray/hide_notice_shown"
_MSG_SHOW, _MSG_OK, _MSG_QUITTING = b"show", "ok", "quitting"


# ── 한 번만 실행 ──────────────────────────────────────────────────────
def instance_name(user: str | None = None, exe: str | None = None) -> str:
    """윈도우 사용자 + 실행 파일 경로의 해시 — 네임드 파이프·뮤텍스는 PC 전체에 하나라 사용자를 넣는다.
    설치판·포터블·소스 실행은 경로가 달라 서로 막지 않는다(서로 다른 실행본은 collect_lock 이 지킨다)."""
    user = user or os.environ.get("USERNAME") or os.environ.get("USER") or "?"
    if exe is None:
        exe = sys.executable if getattr(sys, "frozen", False) else f"{sys.executable}|{Path(__file__).resolve()}"
    h = hashlib.sha1(f"{user.lower()}|{str(exe).lower()}".encode("utf-8")).hexdigest()[:16]
    return f"FifaMatchTracker-{h}"


def _win_mutex(name: str):
    """이름 있는 뮤텍스를 만든다 → (핸들, 이미 있었나). 윈도우가 아니면 (None, False).

    윈도우에선 같은 이름 QLocalServer 의 두 번째 listen 도 성공한다(2026-10-04 실측) — 그래서 '이미 떠 있나'는
    파이프가 아니라 뮤텍스로 판정한다. 핸들은 프로세스가 끝나면 OS 가 닫는다."""
    if sys.platform != "win32":
        return None, False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.CreateMutexW(None, False, f"Local\\{name}")
    existed = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    if existed and h:
        k32.CloseHandle(h)
        h = None
    return h, existed


def _allow_foreground() -> None:
    """두 번째 프로세스가 앞 실행본에게 '창을 앞으로 가져와도 된다'고 넘긴다(윈도우 포그라운드 제한)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass


def ping(name: str, timeout_ms: int = 2000) -> str | None:
    """떠 있는 실행본에 '창 앞으로' → "ok" · "quitting" · "busy"(답이 늦음) · None(연결 안 됨)."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return None
    _allow_foreground()
    sock.write(_MSG_SHOW + b"\n")
    sock.flush()
    reply = ""
    if sock.waitForReadyRead(timeout_ms):
        reply = bytes(sock.readAll()).decode("utf-8", "replace").strip()
    sock.disconnectFromServer()
    return reply or "busy"


class SingleInstance(QObject):
    """한 번만 실행 — claim() 이 True 면 이 프로세스가 주인, False 면 앞 실행본을 앞으로 불렀으니 끝내면 된다."""

    activated = pyqtSignal()

    def __init__(self, name: str, is_quitting: Callable[[], bool] = lambda: False,
                 mutex=_win_mutex, ping_fn=ping, sleep=time.sleep, clock=time.monotonic):
        super().__init__()
        self.name = name
        self.is_quitting = is_quitting
        self._mutex, self._ping, self._sleep, self._clock = mutex, ping_fn, sleep, clock
        self._handle = None
        self.server: QLocalServer | None = None

    def claim(self, wait_s: float | None = None) -> bool:
        """앞 실행본이 '끝나는 중'이거나 답이 늦으면(업데이트 직후 재실행 — 옛 프로세스가 정리 중) 그것이 끝날 때까지
        wait_s 까지 기다렸다 주인이 된다. 그래도 남아 있으면 끝낸다(둘이 같이 뜨는 것보다 낫다)."""
        wait_s = config.SINGLE_WAIT_OLD_S if wait_s is None else wait_s
        deadline = self._clock() + wait_s
        while True:
            handle, existed = self._mutex(self.name)
            if not existed:
                self._handle = handle
                self._listen()
                return True
            if self._ping(self.name) == _MSG_OK:
                return False
            if self._clock() >= deadline:
                return False
            self._sleep(0.5)

    def _listen(self) -> None:
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)  # 유닉스에서 죽은 소켓 파일 — 윈도우는 해당 없음
            self.server.listen(self.name)  # 그래도 안 되면 '앞으로 부르기'만 안 된다

    def _on_connection(self) -> None:
        while self.server is not None and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._on_message(s))
            sock.disconnected.connect(sock.deleteLater)

    def _on_message(self, sock: QLocalSocket) -> None:
        if _MSG_SHOW not in bytes(sock.readAll()):
            return
        quitting = self.is_quitting()
        sock.write((_MSG_QUITTING if quitting else _MSG_OK).encode())
        sock.flush()
        if not quitting:
            self.activated.emit()


# ── 스레드 마무리 ─────────────────────────────────────────────────────
def finish_threads(threads, budget_ms: int) -> None:
    """멈춤 요청을 보낸 스레드들을 합계 budget_ms 만 기다리고, 남은 것은 terminate.
    남긴 채 끝내면 "Destroyed while thread is still running" 으로 비정상 종료될 수 있다."""
    deadline = time.monotonic() + budget_ms / 1000
    alive = [t for t in threads if t is not None]
    for t in alive:
        t.wait(max(0, int((deadline - time.monotonic()) * 1000)))
    for t in alive:
        if t.isRunning():
            t.terminate()
            t.wait(100)


# ── 앱 껍데기 ─────────────────────────────────────────────────────────
class AppShell(QObject):
    """창 밖에서 사는 것들 — 트레이 · 랭킹 수집 예약 · 상주 중 업데이트 확인 · 종료 · 숨긴 창 내려놓기.
    창(MainWindow)은 없을 수도 있다(--tray 로 켰는데 안내 동의·키가 필요할 때 — 열 때 만든다)."""

    def __init__(self, app: QApplication, open_window: Callable[[], object] | None = None, sched=None,
                 single: SingleInstance | None = None):
        super().__init__()
        self.app = app
        self._open_window = open_window
        self.sched = sched
        self.window = None
        self.tray: QSystemTrayIcon | None = None
        self._quitting = False
        self._pending_crash = None
        self.crash_modal: Callable[[object], None] | None = None  # app_main 이 건다 — 창을 열 때 미뤄 둔 오류 안내
        self._tray_waited_s = 0
        app.setQuitOnLastWindowClosed(False)  # 창을 숨겨도 앱은 산다 — 끝내는 건 quit_app 만
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(self._release_due)
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(config.UPDATE_CHECK_EVERY_H * 3600 * 1000)
        self._update_timer.timeout.connect(self._update_due)
        self._tray_timer = QTimer(self)
        self._tray_timer.timeout.connect(self._retry_tray)
        app.commitDataRequest.connect(self._on_commit_data)
        if single is not None:
            single.is_quitting = lambda: self._quitting
            single.activated.connect(self.show_window)

    # ── 시작 ──
    def attach_window(self, win) -> None:
        self.window = win
        win.shell = self
        if self.sched is not None:
            win.attach_rank_sched(self.sched)

    def start(self, tray_mode: bool = False) -> None:
        """tray_mode(--tray): 트레이가 아직 없으면(부팅 직후) TRAY_WAIT_S 까지 다시 보고, 끝내 없으면 창을 활성화하지 않고
        최소화로 띄운다. 동의·키가 필요해 창이 없으면 띄우지 않고 TRAY_RETRY_MIN 마다 계속 본다(게임 위에 막는 창 금지)."""
        if not self._ensure_tray() and tray_mode:
            self._tray_timer.start(config.TRAY_POLL_S * 1000)
        if self.sched is not None:
            self.sched.start()   # 도는 조건(동의·웹 데이터·토글)은 예약이 매번 본다
        if config.UPDATE_CHECK:
            self._update_timer.start()

    def _ensure_tray(self) -> bool:
        if self.tray is not None:
            return True
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        tray = QSystemTrayIcon(self.app.windowIcon(), self)
        tray.setToolTip(config.APP_NAME)
        menu = QMenu()
        menu.addAction("열기", self.show_window)
        menu.addSeparator()
        menu.addAction("종료", self.quit_app)
        tray.setContextMenu(menu)
        self._menu = menu  # 메뉴는 부모가 없어 쥐고 있어야 산다
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray = tray
        return True

    def _retry_tray(self) -> None:
        if self._ensure_tray():
            self._tray_timer.stop()
            return
        self._tray_waited_s += self._tray_timer.interval() // 1000
        if self._tray_waited_s < config.TRAY_WAIT_S:
            return
        if self.window is not None:
            self._tray_timer.stop()
            self.show_inactive()
        else:
            self._tray_timer.setInterval(config.TRAY_RETRY_MIN * 60 * 1000)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    # ── 창 보이기·숨기기 ──
    def window_visible(self) -> bool:
        return self.window is not None and self.window.isVisible() and not self.window.isMinimized()

    def should_hide(self) -> bool:
        """D1 — 트레이가 있고, 수집이 실제로 돌 수 있거나(토글·웹 데이터) 자동 실행이 켜져 있을 때만 X 가 숨긴다."""
        if self.tray is None or not self.tray.isVisible():
            return False
        if config.RANK_COLLECT and config.WEB_DATA:
            return True
        try:
            return autostart.is_enabled()
        except OSError:
            return False

    def hide_window(self) -> None:
        w = self.window
        w.hide()
        self._release_timer.start(config.RELEASE_AFTER_HIDE_MIN * 60 * 1000)
        try:
            s = QSettings(str(config.SETTINGS_PATH), QSettings.Format.IniFormat)
            first = s.value(HIDE_NOTICE_KEY) is None
            if first:
                s.setValue(HIDE_NOTICE_KEY, 1)
                s.sync()
        except Exception:
            first = False
        if first:
            self.notify("트레이에서 계속 돕니다",
                        "창을 닫아도 랭킹 수집·자동 실행을 위해 여기 남습니다. 완전히 끄려면 이 아이콘을 오른쪽 클릭 → [종료].")

    def show_window(self) -> None:
        if self._quitting:
            return
        if self.window is None:
            if self._open_window is None or self._open_window() is None:
                return  # 동의·키 창에서 취소 — 트레이로 남는다
        self._release_timer.stop()
        w = self.window
        if getattr(w, "_released", False):
            w.reload_after_release()
        if not getattr(w, "_shown_once", True):
            w.show_initial()
        elif w.isMinimized():
            w.showNormal()
        else:
            w.show()
        w.raise_()
        w.activateWindow()
        path, self._pending_crash = self._pending_crash, None
        if path is not None and self.crash_modal is not None:
            self.crash_modal(path)

    def show_inactive(self) -> None:
        """트레이를 끝내 못 찾았을 때 — 게임 중일 수 있으니 초점을 빼앗지 않고 최소화로."""
        w = self.window
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        w.showMinimized()
        w._shown_once = True

    # ── 알림 ──
    def notify(self, title: str, text: str) -> bool:
        """트레이 풍선 — 모달을 띄우지 않는다(숨긴 창 위·게임 위). 트레이가 없으면 False."""
        if self.tray is None:
            return False
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 10000)
        return True

    def crash_while_hidden(self, path) -> None:
        self._pending_crash = path
        self.notify("예기치 못한 오류", "오류 기록을 남겼습니다. 창을 열면 자세히 알려 드립니다.")

    # ── 주기 일 ──
    def _update_due(self) -> None:
        w = self.window
        if w is not None and config.UPDATE_CHECK and not config.notice_needed():
            w.start_update_check()

    def _release_due(self) -> None:
        w = self.window
        if w is None or self._quitting or w.isVisible():
            return
        if w.busy_for_release() or (self.sched is not None and self.sched.running()):
            self._release_timer.start(config.RELEASE_RETRY_MIN * 60 * 1000)
            return
        w.release_memory()

    # ── 종료 ──
    def _on_commit_data(self, _manager) -> None:
        self.quit_app(fast=True)

    def quit_app(self, fast: bool = False) -> None:
        """모든 종료가 거치는 한 곳. fast: 윈도우 종료·로그오프 — 멈춤 요청만 하고 합계 FAST_QUIT_WAIT_S 만 기다린다."""
        if self._quitting:
            return
        self._quitting = True
        for t in (self._release_timer, self._update_timer, self._tray_timer):
            t.stop()
        w = self.window
        left = []
        if w is not None:
            w._quitting = True
            left += w.shutdown(fast=fast) or []   # 설정 저장이 멈춤 요청보다 먼저
        if self.sched is not None:
            left += self.sched.shutdown(fast=fast) or []
        if fast:
            finish_threads(left, config.FAST_QUIT_WAIT_S * 1000)
        if self.tray is not None:
            self.tray.hide()
        if w is not None and w.isVisible():
            w.close()  # _quitting 이라 closeEvent 는 받기만 한다
        self.app.quit()
