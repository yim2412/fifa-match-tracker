"""푸시 전에 CI 와 같은 조건으로 테스트를 전부 돌린다(배포판 밖).

이 PC 에서는 초록인데 CI 에서만 깨진 일이 네 번 있었다(2026-11-15 · 11-16 · 11-25 두 건). 까닭은 셋이었고 여기서 흉내 낸다.

| CI 가 다른 점 | 이 PC 에서 안 보인 까닭 | 흉내 |
|---|---|---|
| 시간대가 UTC | 이 PC 는 KST 라 지어낸 KST 시각과 PC 시각이 우연히 맞는다 | `TZ=UTC0`(윈도우 파이썬도 읽는다) |
| 데이터 폴더가 비었다 | 이 PC 의 `meta_*` 캐시·DB 가 빠진 준비를 메워 준다 | 파일마다 빈 `FIFA_DATA_DIR` |
| 고친 파일만 돌리지 않는다 | 가짜 창에 새 함수가 없는 다른 테스트를 안 돌렸다 | 워크플로의 `run: python …` 을 그대로 읽어 전부 |

출력 없이 죽은 프로세스(접근 위반)도 종료 코드로 잡고 faulthandler 로 멈춘 자리를 남긴다. 파이썬 버전과 글꼴은 흉내 내지 않는다 —
여기서 초록이어도 CI 초록을 대신하지는 않는다.

    python tools/ci_local.py                # CI 가 돌리는 것 전부(2026-11-25 실측 29개 · 4개 동시 · 227초)
    python tools/ci_local.py --all          # CI 밖의 tests/test_*.py 까지
    python tools/ci_local.py squadcmp ui    # 이름에 이 글자가 든 것만
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = Path(".github/workflows/test.yml")
WORKERS = 4
TAIL_LINES = 25


def ci_files(root: Path) -> list[str]:
    """워크플로가 돌리는 파이썬 파일(순서대로). `python -m …`(패키지 설치)은 뺀다. 인자가 붙거나 읽지 못한 줄이 있으면 ValueError —
    빠뜨린 채 "전부 통과" 라고 하지 않게."""
    out = []
    for ln in (root / WORKFLOW).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(?:-\s+)?run:\s*(.*\S)", ln)
        if not m or m[1].startswith("python -m pip "):
            continue
        got = re.fullmatch(r"python ((?:tests|tools)/\w+\.py)", m[1])
        if not got:
            raise ValueError(f"읽지 못한 워크플로 줄입니다: {m[1]}")
        out.append(got[1])
    return list(dict.fromkeys(out))


def pick(root: Path, every: bool, names: list[str]) -> list[str]:
    files = ci_files(root)
    if every:
        files += sorted(f"tests/{p.name}" for p in (root / "tests").glob("test_*.py") if f"tests/{p.name}" not in files)
    return [f for f in files if not names or any(n in f for n in names)]


def child_env(data_dir: str) -> dict[str, str]:
    return dict(os.environ, TZ="UTC0", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", FIFA_DATA_DIR=data_dir, FIFA_NO_CRED="1")


def run_one(root: Path, f: str) -> tuple[int, float, list[str]]:
    """→ (종료 코드, 초, 출력 줄들). 출력은 파일로 받는다 — 프로세스째 죽어도 거기까지는 남게(-u)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        t = time.monotonic()
        log = Path(d) / "out.log"
        data = Path(d) / "data"
        data.mkdir()
        with open(log, "wb") as out:
            rc = subprocess.run([sys.executable, "-u", "-X", "faulthandler", f], cwd=root, env=child_env(str(data)),
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT).returncode
        lines = [x for x in log.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        return rc, time.monotonic() - t, lines


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="이름에 이 글자가 든 파일만")
    ap.add_argument("--all", action="store_true", help="CI 밖의 tests/test_*.py 까지")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args(argv)
    try:
        files = pick(root, args.all, args.names)
    except (OSError, ValueError) as e:
        print(f"[FAIL] {e}")
        return 2
    if not files:
        print("[FAIL] 돌릴 파일이 없습니다")
        return 2
    t0, bad = time.monotonic(), []
    with ThreadPoolExecutor(args.workers) as ex:
        jobs = {ex.submit(run_one, root, f): f for f in files}
        for i, j in enumerate(as_completed(jobs), 1):
            f, (rc, s, lines) = jobs[j], j.result()
            print(f"{i}/{len(files)} {'[OK]  ' if rc == 0 else '[FAIL]'} {f} · {s:.0f}초 · {lines[-1][:80] if lines else '출력 없음'}", flush=True)
            if rc:
                bad.append(f)
                shown = [x for x in lines if x.startswith(("[FAIL]", "[ERR]"))] or lines[-TAIL_LINES:]
                print(f"    종료 코드 {rc & 0xFFFFFFFF:#x}", *(f"    {x[:300]}" for x in shown), sep="\n", flush=True)
    print(f"\n실패 {len(bad)} / {len(files)} · {time.monotonic() - t0:.0f}초" + (f" · {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
