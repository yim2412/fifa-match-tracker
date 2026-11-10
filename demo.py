"""둘러보기(96 O2) 프로세스 — 임시 데이터 폴더 · 잠금 · 띄우기. **표준 라이브러리만**(config import 전에 돈다).

같은 exe 를 `--demo` 로 새 프로세스로 띄운다(같은 프로세스에서 config 의 재할당 전역을 바꿔 끼우면 이미 연 연결·예약이
실제 폴더를 계속 볼 수 있다 — ROADMAP 96 E2). 지어낸 자료는 `demodata.py`.

| 단계 | 누가 | 무엇을 |
|---|---|---|
| 띄우기 | 키 창 [키 없이 둘러보기] | `child_env` — env 에서 config 의 `*_VAR` 를 뺀다(실제 키·스위치를 안 물려준다). **아무것도 넣지 않는다** |
| 준비 | 자식 `app_main` 맨 위(`prepare`) | 원래 `FIFA_DATA_DIR`·`FIFA_DEMO` 를 적어 두고 → 낡은 데모 폴더 쓸기 → 자기 폴더를 만들고 바로 잠금 → 두 이름을 넣는다 |
| 다시 띄우기 | 띠 [키 넣고 시작] | `restart_env` — 적어 둔 원래 값으로 되살린 env(안 그러면 진짜 앱이 데모 폴더를 써서 넣은 키가 쓸릴 때 사라진다) |
| 지우기 | 닫을 때 시도 + 다음 실행(보통·데모)이 `sweep` | 판정은 pid 가 아니라 **잠금 파일**(열어 쥔 파일은 못 지운다) · 잠금 unlink 먼저 · 이름의 만든 시각이 60초 안이면 건너뜀 |
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEMO_ARG = "--demo"
DEMO_FLAG = "FIFA_DEMO"            # config.DEMO_VAR 와 같아야(test_demo 가 대조)
DATA_DIR_VAR = "FIFA_DATA_DIR"     # config._data_dir 이 읽는 이름
DIR_PREFIX = "피파전적관리-demo-"   # %TEMP% 아래 이 꼴만 쓸어 낸다
LOCK_NAME = "demo.lock"
FRESH_S = 60                       # 만든 지 이만큼 안 된 폴더는 잠금이 아직 없어도 건너뛴다(만들기와 잠금 사이 경쟁)

_session: "DemoSession | None" = None


class DemoSession:
    def __init__(self, folder: Path, lock, original: dict[str, str | None]):
        self.folder = folder
        self.lock = lock                 # 열어 쥔 파일 — 이 프로세스가 사는 동안 다른 실행이 폴더를 못 지운다
        self.original = original         # 넣기 전 값(없었으면 None)


def session() -> DemoSession | None:
    return _session


def is_demo_argv(argv) -> bool:
    return DEMO_ARG in list(argv or [])[1:]


def _created_at(folder: Path) -> float | None:
    """폴더 이름 `<접두어><pid>-<만든 epoch>` 의 만든 시각 — mtime 은 안에 파일이 생길 때마다 바뀌어 못 쓴다."""
    try:
        return float(folder.name[len(DIR_PREFIX):].rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def sweep(tmp: Path | None = None, now: float | None = None, keep: Path | None = None) -> list[Path]:
    """낡은 데모 폴더를 지운다 → 지운 폴더. 살아 있는 데모(잠금 파일을 열어 쥐었다)는 잠금 unlink 가 실패해 건너뛴다."""
    tmp = Path(tmp or tempfile.gettempdir())
    now = time.time() if now is None else now
    gone = []
    try:
        folders = [p for p in tmp.iterdir() if p.is_dir() and p.name.startswith(DIR_PREFIX)]
    except OSError:
        return gone
    for f in folders:
        if keep is not None and f == keep:
            continue
        lock = f / LOCK_NAME
        if lock.exists():
            try:
                lock.unlink()            # 먼저 — rmtree 먼저면 잠기지 않은 파일부터 지워 살아 있는 데모를 망가뜨린다
            except OSError:
                continue                 # 살아 있다
        else:
            made = _created_at(f)
            if made is None or now - made < FRESH_S:
                continue
        shutil.rmtree(f, ignore_errors=True)
        if not f.exists():
            gone.append(f)
    return gone


def prepare(argv=None, environ=None, tmp: Path | None = None, pid: int | None = None,
            now: float | None = None) -> DemoSession | None:
    """`--demo` 면 데모 폴더를 만들고 잠근 뒤 env 에 `FIFA_DATA_DIR`·`FIFA_DEMO` 를 넣는다 → 세션. 아니면 None(아무것도 안 함)."""
    global _session
    argv = sys.argv if argv is None else argv
    if not is_demo_argv(argv):
        return None
    environ = os.environ if environ is None else environ
    original = {k: environ.get(k) for k in (DATA_DIR_VAR, DEMO_FLAG)}
    tmp = Path(tmp or tempfile.gettempdir())
    now = time.time() if now is None else now
    sweep(tmp, now)
    folder = tmp / f"{DIR_PREFIX}{os.getpid() if pid is None else pid}-{int(now)}"
    folder.mkdir(parents=True, exist_ok=True)
    lock = open(folder / LOCK_NAME, "w", encoding="utf-8")   # 파이썬 open 은 FILE_SHARE_DELETE 없이 연다 — 쥐는 동안 못 지운다
    lock.write(str(os.getpid()))
    lock.flush()
    environ[DATA_DIR_VAR] = str(folder)
    environ[DEMO_FLAG] = "1"
    _session = DemoSession(folder, lock, original)
    return _session


def restart_env(sess: DemoSession, environ=None) -> dict[str, str]:
    """[키 넣고 시작] 으로 띄울 진짜 앱의 env — 데모가 넣은 두 이름을 넣기 전 값으로."""
    env = dict(os.environ if environ is None else environ)
    for k, v in sess.original.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def child_env(strip_vars, environ=None) -> dict[str, str]:
    """둘러보기 자식의 env — 실제 키·스위치(config 의 *_VAR)와 데모 표지를 뺀다. 넣는 것은 없다."""
    env = dict(os.environ if environ is None else environ)
    for k in (*strip_vars, DEMO_FLAG, DATA_DIR_VAR):
        env.pop(k, None)
    return env


def launch_cmd(demo: bool, app_main_path: str | None = None) -> list[str]:
    """exe 면 `<exe> [--demo]`, 소스면 `python app_main.py [--demo]`."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, app_main_path or str(Path(__file__).resolve().with_name("app_main.py"))]
    return cmd + ([DEMO_ARG] if demo else [])


def spawn(cmd: list[str], env: dict[str, str]):
    """콘솔 없이 따로 — 띄운 쪽이 바로 끝나도 산다. 경로는 리스트 인자라 한글 사용자명·공백도 그대로."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(cmd, env=env, close_fds=True, creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cleanup(sess: DemoSession | None = None) -> bool:
    """닫을 때 — 잠금을 놓고 폴더를 지워 본다(열린 WAL·crash 로그가 남으면 다음 실행의 sweep 이). → 지웠나."""
    sess = sess or _session
    if sess is None:
        return False
    try:
        sess.lock.close()
    except OSError:
        pass
    shutil.rmtree(sess.folder, ignore_errors=True)
    return not sess.folder.exists()
