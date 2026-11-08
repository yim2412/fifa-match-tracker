"""게임 중 수집 멈춤(gameguard · tools/run_unless_game) — 가짜 프로세스 목록·가짜 자식(네트워크 없음).
`python tests/test_gameguard.py`"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import config  # noqa: E402
import gameguard  # noqa: E402
import run_unless_game as R  # noqa: E402
import watchdog  # noqa: E402


def test_game_running_by_name():
    assert config.GAME_PROCESSES, "게임 이름이 비면 늘 '꺼짐' 이라 수집이 게임 중에도 돈다"
    game = config.GAME_PROCESSES[0]
    assert gameguard.game_running(["explorer.exe", game.upper()]) is True, "대소문자 무시"
    assert gameguard.game_running(["explorer.exe", "python.exe"]) is False
    assert gameguard.game_running([], games=("a.exe",)) is False
    assert gameguard.game_running(["x.exe"], games=("x.exe",)) is True


def test_unreadable_list_counts_as_running():
    keep = gameguard.process_names
    try:
        gameguard.process_names = lambda: None
        assert gameguard.game_running() is True, "목록을 못 읽으면 멈추는 쪽"
    finally:
        gameguard.process_names = keep


def test_real_process_list_reads_without_opening():
    names = gameguard.process_names()
    if sys.platform != "win32":
        assert names == []
        return
    assert names and any(n.startswith("python") for n in names), "이 테스트를 돌리는 파이썬이 목록에 없다 — 읽기가 비었다"


def test_watcher_checks_every_n_seconds_and_sticks():
    now = {"t": 0.0}
    calls = []
    answers = iter([False, False, True, False])

    def check():
        calls.append(now["t"])
        return next(answers)
    cancel = gameguard.watcher(check, every_s=15, clock=lambda: now["t"])
    assert cancel() is False and calls == [0.0]
    now["t"] = 10
    assert cancel() is False and calls == [0.0], "15초 안에는 다시 안 본다"
    now["t"] = 15
    assert cancel() is False and calls == [0.0, 15]
    now["t"] = 30
    assert cancel() is True
    now["t"] = 45
    assert cancel() is True and len(calls) == 3, "한 번 켜졌으면 계속 멈춤 — 다시 묻지 않는다"


class FakeChild:
    def __init__(self, polls_before_exit, code=0):
        self.left, self.code = polls_before_exit, code
        self.returncode = None
        self.terminated = False

    def poll(self):
        if self.terminated:
            self.returncode = -15
        elif self.left <= 0:
            self.returncode = self.code
        else:
            self.left -= 1
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.poll()

    def kill(self):
        self.terminated = True


def _launcher(game_answers, child):
    made = []
    answers = iter(game_answers)
    keep = R.GAME_CHECK, R.LOG_PATH
    tmp = tempfile.TemporaryDirectory()
    R.GAME_CHECK = lambda: next(answers)
    R.LOG_PATH = Path(tmp.name) / "log.txt"

    def popen(cmd, creationflags=0):
        made.append((cmd, creationflags))
        return child

    def restore():
        R.GAME_CHECK, R.LOG_PATH = keep
        tmp.cleanup()
    return made, popen, restore


def test_launcher_skips_when_game_running():
    made, popen, restore = _launcher([True], FakeChild(0))
    try:
        assert R.run(["measure_cards.py", "--daily"], popen=popen, sleep=lambda s: None) == 0
        log = R.LOG_PATH.read_text(encoding="utf-8")
    finally:
        restore()
    assert made == [] and "게임 중 — 건너뜀" in log


def test_launcher_runs_and_passes_exit_code():
    child = FakeChild(2, code=2)
    made, popen, restore = _launcher([False, False, False, False], child)
    try:
        rc = R.run(["measure_cards.py", "--daily"], popen=popen, sleep=lambda s: None)
    finally:
        restore()
    assert rc == 2 and not child.terminated
    cmd, flags = made[0]
    assert cmd[0] == sys.executable and cmd[1:] == ["measure_cards.py", "--daily"]
    if sys.platform == "win32":
        assert flags == R.subprocess.CREATE_NO_WINDOW, "pythonw 에서 띄운 자식이 콘솔 창을 띄우면 안 된다"


def test_launcher_stops_child_when_game_starts():
    child = FakeChild(100)
    made, popen, restore = _launcher([False, False, True], child)
    try:
        rc = R.run(["measure_cards.py"], popen=popen, sleep=lambda s: None)
        log = R.LOG_PATH.read_text(encoding="utf-8")
    finally:
        restore()
    assert rc == 3 and child.terminated and "게임이 켜져 내렸다" in log, (rc, log)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
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
