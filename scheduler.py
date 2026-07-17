"""Windows 작업 스케줄러 등록/해제 — 앱에서 자동 수집을 껐다 켤 수 있게.

schtasks.exe 를 부른다. 현재 사용자 계정의 작업이라 관리자 권한은 필요 없다.

창이 뜨지 않게 pythonw.exe 로 돌린다(python.exe 면 6시간마다 검은 창이 번쩍인다).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import config

TASK_NAME = "피파전적관리 자동수집"
DEFAULT_HOURS = 6

# 작업 스케줄러가 부를 실행 파일. 콘솔 창을 띄우지 않는 pythonw 를 쓴다.
_PYW = Path(sys.executable).with_name("pythonw.exe")
PYTHON = _PYW if _PYW.exists() else Path(sys.executable)
SCRIPT = config.ROOT / "collect.py"

# 자식 프로세스의 콘솔 창을 숨긴다 — GUI 에서 부를 때 창이 번쩍이지 않게.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SchedulerError(Exception):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        # schtasks 출력은 한국어 Windows 에서 cp949 다. 깨져도 죽지 않게.
        encoding="cp949", errors="replace",
        creationflags=_NO_WINDOW,
    )


def is_supported() -> bool:
    """Windows 가 아니거나 schtasks 가 없으면 기능을 숨긴다."""
    if sys.platform != "win32":
        return False
    try:
        return _run(["/Query", "/?"]).returncode == 0
    except FileNotFoundError:
        return False


def is_enabled() -> bool:
    return _run(["/Query", "/TN", TASK_NAME]).returncode == 0


def enable(hours: int = DEFAULT_HOURS) -> None:
    """등록(이미 있으면 덮어쓴다). 주기는 시간 단위."""
    if not SCRIPT.exists():
        raise SchedulerError(f"{SCRIPT.name} 을 찾지 못했습니다.")
    # 경로에 공백이 있어도 되게 통째로 따옴표. schtasks /TR 은 문자열 하나를 받는다.
    command = f'"{PYTHON}" "{SCRIPT}"'
    r = _run(["/Create", "/TN", TASK_NAME, "/TR", command,
              "/SC", "HOURLY", "/MO", str(hours), "/F"])
    if r.returncode != 0:
        raise SchedulerError((r.stderr or r.stdout or "").strip()
                             or f"등록 실패 (코드 {r.returncode})")


def disable() -> None:
    r = _run(["/Delete", "/TN", TASK_NAME, "/F"])
    if r.returncode != 0 and is_enabled():
        raise SchedulerError((r.stderr or r.stdout or "").strip()
                             or f"해제 실패 (코드 {r.returncode})")


def run_now() -> None:
    """등록된 작업을 지금 한 번 실행 — 켜자마자 되는지 확인용."""
    r = _run(["/Run", "/TN", TASK_NAME])
    if r.returncode != 0:
        raise SchedulerError((r.stderr or r.stdout or "").strip()
                             or f"실행 실패 (코드 {r.returncode})")


def describe() -> str:
    """상태를 사람이 읽는 한 줄로."""
    if not is_supported():
        return "이 환경에서는 작업 스케줄러를 쓸 수 없습니다."
    if not is_enabled():
        return "자동 수집 꺼짐 — 앱으로 조회할 때만 쌓입니다."
    r = _run(["/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    nxt = ""
    for line in (r.stdout or "").splitlines():
        if "다음 실행 시간" in line or "Next Run Time" in line:
            nxt = line.split(":", 1)[1].strip()
            break
    return f"자동 수집 켜짐 — 다음 실행: {nxt}" if nxt else "자동 수집 켜짐"
