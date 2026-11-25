"""CI 흉내 도구(tools/ci_local.py) — 임시 폴더의 가짜 워크플로·가짜 테스트로. 네트워크 없이.

    python tests/test_ci_local.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import watchdog  # noqa: E402 — 109 J8
import ci_local as cl  # noqa: E402


@contextlib.contextmanager
def repo(steps: list[str], **files: str):
    """steps = 워크플로 run 줄 · files = {이름: 내용} → tests/test_이름.py."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / cl.WORKFLOW).parent.mkdir(parents=True)
        (root / "tests").mkdir()
        body = "".join(f"      - name: 단계\n        run: {s}\n" for s in steps)
        (root / cl.WORKFLOW).write_text("jobs:\n  test:\n    steps:\n" + body, encoding="utf-8")
        for k, v in files.items():
            (root / "tests" / f"test_{k}.py").write_text(v, encoding="utf-8")
        yield root


def go(root: Path, *argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cl.main(list(argv), root)
    return rc, buf.getvalue()


def test_reads_the_real_workflow():
    """진짜 워크플로의 모든 run 줄을 읽는다 — 빠진 것이 있으면 여기서 초록이어도 CI 에서 깨진다."""
    text = (ROOT / cl.WORKFLOW).read_text(encoding="utf-8")
    files = cl.ci_files(ROOT)
    assert len(files) == text.count("run: python tests/") and "tests/test_ui_smoke.py" in files, files
    assert all((ROOT / f).exists() for f in files)
    every = cl.pick(ROOT, True, [])
    assert {p.name for p in (ROOT / "tests").glob("test_*.py")} == {Path(f).name for f in every if f.startswith("tests/")}
    assert len(every) == len(set(every)) and cl.pick(ROOT, False, ["squadcmp"]) == ["tests/test_squadcmp.py"]


def test_unreadable_step_or_nothing_is_not_green():
    ok = "print('통과')"
    with repo(["python -m pip install x", "python tests/test_a.py"], a=ok) as r:
        assert cl.ci_files(r) == ["tests/test_a.py"] and go(r)[0] == 0
        assert go(r, "없는이름")[0] == 2, "돌린 것이 0개인데 통과라고 한다"
    with repo(["python tests/test_a.py", "python tests/test_a.py --only x"], a=ok) as r:
        assert go(r)[0] == 2, "읽지 못한 줄을 조용히 건너뛰었다"
    with repo([], a=ok) as r:
        assert go(r)[0] == 2
        assert go(r, "--all")[0] == 0


def test_failures_and_silent_deaths_are_caught():
    fail = "print('[OK]   x')\nprint('[FAIL] test_x: 값이 다릅니다')\nraise SystemExit(1)"
    died = "import os\nos._exit(5)"          # 출력 한 줄 없이 죽는다(11-25 CI)
    with repo(["python tests/test_a.py", "python tests/test_b.py", "python tests/test_c.py"], a="print('통과')", b=fail, c=died) as r:
        rc, out = go(r)
        assert rc == 1 and "실패 2 / 3" in out and "tests/test_a.py" not in out.split("실패 2 / 3")[1], out
        assert "값이 다릅니다" in out and "출력 없음" in out and "0x5" in out, out
        assert go(r, "test_a")[0] == 0


def test_children_run_at_low_priority():
    """같이 쓰는 PC 를 멈추지 않게(2026-11-25) — 자식이 실제로 낮은 우선순위인가."""
    look = ("import ctypes, os\n"
            "k = ctypes.windll.kernel32 if os.name == 'nt' else None\n"
            "if k: k.GetCurrentProcess.restype = ctypes.c_void_p; k.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
            "raise SystemExit(0 if not k or k.GetPriorityClass(k.GetCurrentProcess()) == 0x4000 else 9)\n")
    with repo(["python tests/test_a.py"], a=look) as r:
        rc, out = go(r)
        assert rc == 0, out
    assert cl.WORKERS <= 2


def test_child_sees_ci_conditions():
    """UTC · 파일마다 빈 데이터 폴더 · 자격 증명 안 씀. 앞 파일이 남긴 것이 다음 파일에 보이지 않는다."""
    body = ("import os, sys\nfrom datetime import datetime\nfrom pathlib import Path\n"
            "d = Path(os.environ['FIFA_DATA_DIR'])\n"
            "ok = datetime.fromtimestamp(86400).hour == 0 and not list(d.iterdir()) and os.environ['FIFA_NO_CRED'] == '1'\n"
            "(d / 'left').write_text('x', encoding='utf-8')\nraise SystemExit(not ok)")
    with repo(["python tests/test_a.py", "python tests/test_b.py"], a=body, b=body) as r:
        rc, out = go(r, "--workers", "1")
        assert rc == 0, out


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
