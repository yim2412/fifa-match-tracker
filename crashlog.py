"""처리 안 된 예외를 파일로 남긴다 — exe 는 콘솔이 없어(console=False) 그냥 사라진다.

PyQt6 는 슬롯 안 예외를 sys.excepthook 으로 넘기고, 훅이 기본값이면 앱을 바로
끝낸다. 여기서 훅을 바꿔 두면 기록만 남기고 앱은 계속 돈다.
남의 PC 에서 난 오류는 이 파일 말고는 원인을 알 길이 없다.
"""
from __future__ import annotations

import faulthandler
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

LOG_NAME = "crash.log"
# 시작할 때 이보다 크면 crash.log.1 로 밀어낸다(하나만 남긴다). 실행 중에는 안 민다 —
# faulthandler 가 파일을 열고 있어 윈도우에서 이름을 못 바꾼다.
MAX_BYTES = 512 * 1024

_state: dict = {}


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            old = path.with_name(path.name + ".1")
            old.unlink(missing_ok=True)
            path.rename(old)
    except OSError:
        pass  # 밀어내기 실패해도 기록은 이어서 쓴다


def _write(kind: str, exc_type, exc, tb) -> None:
    path: Path = _state["path"]
    body = "".join(traceback.format_exception(exc_type, exc, tb))
    head = (f"=== {datetime.now():%Y-%m-%d %H:%M:%S} · {kind} · {_state['version']} · "
            f"Python {platform.python_version()} · {platform.platform()}\n")
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(head + body + "\n")
    except OSError:
        pass  # 기록 실패가 또 다른 크래시가 되면 안 된다


def _on_main_exc(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _write("main" if threading.current_thread() is threading.main_thread() else "thread",
           exc_type, exc, tb)
    # 알림 창은 GUI 스레드에서, 한 세션에 한 번만 — 같은 오류가 반복되면 창이 쌓인다
    notify = _state.get("notify")
    if (notify and not _state.get("notified")
            and threading.current_thread() is threading.main_thread()):
        _state["notified"] = True
        try:
            notify(_state["path"])
        except Exception:
            pass


def _on_thread_exc(args) -> None:
    _write(f"thread {args.thread.name if args.thread else '?'}",
           args.exc_type, args.exc_value, args.exc_traceback)


def install(log_dir: Path, version: str,
            notify: Callable[[Path], None] | None = None) -> Path:
    """훅을 건다. notify(path) 는 첫 오류 때 GUI 스레드에서 한 번 불린다."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_NAME
    _state.update(path=path, version=version, notify=notify, notified=False)
    sys.excepthook = _on_main_exc
    threading.excepthook = _on_thread_exc
    # 파이썬 예외가 아닌 진짜 크래시(접근 위반 등)는 faulthandler 만 잡는다.
    # 파일을 열어 둔 채여야 하므로 _state 에 쥔다.
    try:
        _rotate(path)
        fh = open(path, "a", encoding="utf-8", errors="replace")
        _state["fault_file"] = fh
        faulthandler.enable(file=fh)
    except (OSError, RuntimeError, ValueError):
        pass
    return path
