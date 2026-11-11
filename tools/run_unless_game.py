"""게임이 켜져 있으면 안 띄우고, 도는 중 켜지면 내리는 실행기(개발 PC 전용 · 2026-11-08 — 머리말 근거는 gameguard.py).

    pythonw tools/run_unless_game.py [--once-a-day] <스크립트> [인자…]

작업 스케줄러 `FifaMeasureCards` 가 이것을 부른다. `tools/measure_cards.py` 는 51 측정이 해시를 재서
11-26 판정 전엔 못 고치므로 밖에서 감싼다. 자식은 같은 파이썬으로 띄우고, GAME_CHECK_S 마다 게임을 본다 —
켜지면 자식을 끝낸다(카드 정보는 한 장씩 저장되어 끊겨도 깨지지 않고 다음 실행이 잇는다). 기록은 logs/run_unless_game.log.

`--once-a-day`(2026-11-11): 예약이 10:00 부터 10분마다 부르고, 오늘 자식이 **끝까지 돈 적이 있으면** 안 띄운다.
그 전엔 로그온 10분 뒤·10:00 두 번뿐이라 그때 게임 중이면 그날 몫이 통째로 빠졌다(11-08~11 "게임 중 — 건너뜀" 6번).
끝난 것으로 치는 것은 자식이 스스로 끝났을 때만이다 — 종료 코드는 안 본다(`--daily` 는 측정일이 아니면 늘 2).
게임 때문에 안 띄웠거나 도중에 내렸으면 표시를 안 남겨 다음 부름(10분 뒤)이 다시 띄운다.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import gameguard  # noqa: E402

LOG_PATH = config.DATA_DIR / "logs" / "run_unless_game.log"
GAME_CHECK = gameguard.game_running   # 테스트가 바꿔 끼운다
TODAY = date.today                     # 테스트가 바꿔 끼운다
ONCE_FLAG = "--once-a-day"


def _done_path(name: str) -> Path:
    return LOG_PATH.with_name(f"run_unless_game.{Path(name).stem}.done")


def _done_today(name: str) -> bool:
    try:
        return _done_path(name).read_text(encoding="utf-8").strip() == TODAY().isoformat()
    except (OSError, ValueError):
        return False


def _mark_done(name: str) -> None:
    try:
        _done_path(name).write_text(TODAY().isoformat(), encoding="utf-8")
    except OSError:
        pass


def _log(line: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def run(argv: list[str], *, popen=subprocess.Popen, sleep=time.sleep, every_s: float | None = None) -> int:
    """→ 자식의 종료 코드 · 게임 중이라 안 띄웠으면 0 · 도중에 내렸으면 3 · 오늘 이미 돌았으면 0."""
    once = bool(argv) and argv[0] == ONCE_FLAG
    if once:
        argv = argv[1:]
    if not argv:
        _log("[FAIL] 실행할 스크립트가 없다")
        return 2
    name = Path(argv[0]).name
    if once and _done_today(name):
        return 0          # 10분마다 불린다 — 로그에 안 남긴다(하루 80줄)
    if GAME_CHECK():
        _log(f"[OK]   {name}: 게임 중 — 건너뜀")
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # pythonw 에서 띄워도 콘솔 창이 안 뜨게
    child = popen([sys.executable, *argv], creationflags=flags)
    every = config.GAME_CHECK_S if every_s is None else every_s
    while child.poll() is None:
        sleep(every)
        if child.poll() is None and GAME_CHECK():
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            _log(f"[OK]   {name}: 게임이 켜져 내렸다 — 다음 실행이 잇는다")
            return 3
    _log(f"[OK]   {name}: 끝 · 종료 코드 {child.returncode}")
    if once:
        _mark_done(name)
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
