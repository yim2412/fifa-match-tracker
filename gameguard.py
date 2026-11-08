"""게임이 켜져 있나 — 개발 PC 수집이 게임 중에 돌지 않게(2026-11-08).

11-06 09:48 게임이 "불법 프로그램이 감지되어 게임을 종료합니다" 로 꺼졌다. 그 시각 `tools/dev_matches.py` 가
자정부터 넥슨 오픈API 에 초당 약 40번 요청하던 중이었고(00:00~10:31), 사용자 기억으로도 파이썬을 끄거나 수집이
끝나면 안 떴다. 보안 프로그램 로그는 암호화돼 원인을 직접 확인하지는 못했다 — 시간이 맞는다는 근거다.

**게임 프로세스를 열지 않는다.** 이름 목록만 읽는다(`CreateToolhelp32Snapshot` — 다른 프로세스에 핸들을 안 연다).
게임 보안 프로그램을 피하려다 게임을 건드리면 안 되기 때문이다. 목록을 못 읽으면 "켜져 있음" 으로 본다(안전한 쪽).
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Iterable

import config


def process_names() -> list[str] | None:
    """지금 도는 프로세스 이름(소문자). 못 읽으면 None. Windows 가 아니면 빈 목록."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    k32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        names: list[str] = []
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return names or None
    finally:
        k32.CloseHandle(snap)


def game_running(names: Iterable[str] | None = None, games: Iterable[str] | None = None) -> bool:
    """config.GAME_PROCESSES 중 하나라도 떠 있나. names 를 못 읽었으면(None) 참 — 수집을 멈추는 쪽."""
    if names is None:
        names = process_names()
        if names is None:
            return True
    want = {g.lower() for g in (config.GAME_PROCESSES if games is None else games)}
    return any(n.lower() in want for n in names)


def watcher(check: Callable[[], bool] = game_running, every_s: float | None = None,
            clock: Callable[[], float] = time.monotonic) -> Callable[[], bool]:
    """취소 함수 — 매번 묻지 않고 every_s 마다만 다시 본다(요청 반복 안에서 불린다). 한 번 참이면 계속 참."""
    every = config.GAME_CHECK_S if every_s is None else every_s
    state = {"at": None, "on": False}

    def cancel() -> bool:
        if state["on"]:
            return True
        now = clock()
        if state["at"] is None or now - state["at"] >= every:
            state["at"] = now
            state["on"] = bool(check())
        return state["on"]
    return cancel
