"""윈도우 시작 프로그램 등록(1.1.1 자동 실행) — HKCU Run 에 `"<exe>" --tray` 한 줄.

- exe(frozen)에서만. 소스 실행은 python.exe 를 등록하게 되니 막는다.
- 값 이름을 설치판·포터블로 나눈다 — 둘 다 쓰는 PC 에서 서로 덮어쓰지 않게. 설치판 제거기는 설치판 값만 지운다(.iss).
- 등록된 경로는 그 파일이 없어졌을 때만 고친다(repair) — 켤 때마다 덮어쓰면 포터블을 잠깐 켠 것만으로 설치판 등록이 바뀐다.
- 옛 버전(1.0.x)으로 되돌리면 `--tray` 를 몰라 부팅마다 창이 뜬다 — CHANGELOG 에 "되돌리기 전에 끄세요".
"""
from __future__ import annotations

import sys
from pathlib import Path

import updatecheck

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_INSTALLED = "FifaMatchTracker"          # installer/*.iss 제거기가 지우는 이름과 같아야 한다
VALUE_PORTABLE = "FifaMatchTrackerPortable"
TRAY_ARG = "--tray"


def _winreg():
    import winreg  # 윈도우 밖에선 없다 — available() 가 먼저 막는다
    return winreg


def available() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def value_name() -> str:
    return VALUE_INSTALLED if updatecheck.install_dir() is not None else VALUE_PORTABLE


def command(exe: str | Path | None = None) -> str:
    # 큰따옴표 — 한글·공백 경로(실측: C:\Users\준\… 왕복 정상)
    return f'"{exe or sys.executable}" {TRAY_ARG}'


def exe_of(cmd: str) -> str:
    """등록된 명령에서 실행 파일 경로만."""
    cmd = (cmd or "").strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    return cmd.split(" ", 1)[0]


def _read(reg, name: str) -> str | None:
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as k:
            val, _typ = reg.QueryValueEx(k, name)
            return str(val)
    except OSError:
        return None


def is_enabled(reg=None) -> bool:
    if reg is None:
        if not available():
            return False
        reg = _winreg()
    return _read(reg, value_name()) is not None


def set_enabled(on: bool, reg=None, exe: str | Path | None = None) -> None:
    """켜고 끈다. 못 쓰면 OSError 를 그대로 올린다(호출부가 사람 말로 보여 준다)."""
    if reg is None:
        if not available():
            raise OSError("자동 실행은 설치판·포터블 실행 파일에서만 켤 수 있습니다")
        reg = _winreg()
    name = value_name()
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as k:
        if on:
            reg.SetValueEx(k, name, 0, reg.REG_SZ, command(exe))
        else:
            try:
                reg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


def repair(reg=None, exe: str | Path | None = None, exists=lambda p: Path(p).is_file()) -> bool:
    """등록돼 있는데 그 exe 가 없어졌으면(폴더를 옮김) 지금 exe 로 고친다 → 고쳤나."""
    if reg is None:
        if not available():
            return False
        reg = _winreg()
    cur = _read(reg, value_name())
    if cur is None or exists(exe_of(cur)):
        return False
    try:
        set_enabled(True, reg=reg, exe=exe)
    except OSError:
        return False
    return True
