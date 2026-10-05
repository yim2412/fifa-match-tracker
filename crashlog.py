"""처리 안 된 예외를 파일로 남긴다 — exe 는 콘솔이 없어(console=False) 그냥 사라진다.

PyQt6 는 슬롯 안 예외를 sys.excepthook 으로 넘기고, 훅이 기본값이면 앱을 바로
끝낸다. 여기서 훅을 바꿔 두면 기록만 남기고 앱은 계속 돈다.
남의 PC 에서 난 오류는 이 파일 말고는 원인을 알 길이 없다.
"""
from __future__ import annotations

import atexit
import faulthandler
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

LOG_NAME = "crash.log"
FAULT_PREFIX = "crash.fault."  # + PID — 실행마다 faulthandler 가 쓰는 파일
# 시작할 때 이보다 크면 crash.log.1 로 밀어낸다(하나만 남긴다).
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
    # 그런데 윈도우에선 **처리된** 네이티브 예외도 "Windows fatal exception" 으로 적는다 — 1.1.1 exe 실측에서
    # COM 의 0x8001010d 가 앱이 멀쩡한데 crash.log 에 크래시처럼 남았다(2026-10-05). 그래서 실행마다 따로 받고,
    # 정상 종료면 버리고, 다음 실행 때 남아 있으면(그 실행이 실제로 죽었다) crash.log 로 옮긴다.
    try:
        _rotate(path)
        _harvest_faults(log_dir, path)
        fault = log_dir / f"{FAULT_PREFIX}{os.getpid()}"
        fh = open(fault, "w", encoding="utf-8", errors="replace")
        _state.update(fault_file=fh, fault_path=fault)
        faulthandler.enable(file=fh)
        if not _state.get("atexit"):
            atexit.register(discard_fault)
            _state["atexit"] = True
    except (OSError, RuntimeError, ValueError):
        pass
    return path


def _harvest_faults(log_dir: Path, path: Path) -> None:
    """앞 실행이 남긴 faulthandler 파일을 crash.log 로. 지울 수 없는 건 살아 있는 실행(두 번째 실행 등)이 쥔 것이라 둔다."""
    for f in sorted(log_dir.glob(FAULT_PREFIX + "*")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            when = datetime.fromtimestamp(f.stat().st_mtime)
            f.unlink()
        except OSError:
            continue
        if not text.strip():
            continue
        try:
            with open(path, "a", encoding="utf-8", errors="replace") as out:
                out.write(f"=== {when:%Y-%m-%d %H:%M:%S} · 이전 실행이 비정상 종료 · faulthandler\n"
                          + text.rstrip() + "\n\n")
        except OSError:
            pass


def discard_fault() -> None:
    """정상 종료 — 이번 실행의 faulthandler 기록은 처리된 예외뿐이라 버린다."""
    fh = _state.pop("fault_file", None)
    fault = _state.pop("fault_path", None)
    if fh is None:
        return
    try:
        faulthandler.disable()
        fh.close()
        if fault is not None:
            fault.unlink(missing_ok=True)
    except OSError:
        pass
