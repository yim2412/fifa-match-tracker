"""이 PC 시험 실행기(1단계 ⓐ) — `python -m server.run_local`.

- 게임(settings.GAME_PROCESSES)이 떠 있으면 안 띄우고, 도는 중 뜨면 내린다. **목록이 비면 띄우기를 거부한다**
  (이름이 틀리거나 비면 감시가 조용히 안 걸린다 — 관대해지는 쪽으로 틀리지 않게)
- dev_matches·measure_cards 가 돌면 거부/내림 — 같은 서비스 키·같은 IP 라 측정과 막힘 판정을 흐린다
- 서비스 키는 settings.ENV_FILE(앱 .env 와 따로)에서 읽어 이 프로세스 환경에만 · 127.0.0.1 에만 묶는다
- 상태줄 한 줄(~/.claude/bg-progress.d/server-<PID>.txt — 그 폴더가 있을 때만) · 끝나면 지운다
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import settings

BLOCKING_SCRIPTS = ("dev_matches.py", "measure_cards.py")


def read_key(path: Path = settings.ENV_FILE) -> str:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return ""
    for ln in lines:
        if ln.strip().startswith(f"{settings.KEY_VAR}="):
            return ln.split("=", 1)[1].strip()
    return ""


def processes() -> list[tuple[str, str]]:
    """(이름, 명령줄) — Windows 만. 못 읽으면 빈 목록이 아니라 예외(감시가 조용히 비지 않게)."""
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.Name)`t$($_.CommandLine)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"프로세스 목록을 못 읽었다: {out.stderr.strip()[:200]}")
    rows = []
    for ln in out.stdout.splitlines():
        name, _, cmd = ln.partition("\t")
        if name:
            rows.append((name, cmd))
    if not rows:
        raise RuntimeError("프로세스 목록이 비었다")
    return rows


def blockers(procs: list[tuple[str, str]], games=None) -> list[str]:
    """띄우면 안 되는 이유들 — 비면 띄워도 된다. 게임 목록이 비면 그것 자체가 이유다."""
    games = settings.GAME_PROCESSES if games is None else games
    if not games:
        return ["게임 프로세스 목록(settings.GAME_PROCESSES)이 비어 있다 — 게임을 켜고 이름을 확인해 채운다"]
    want = {g.lower() for g in games}
    why = []
    for name, cmd in procs:
        if name.lower() in want:
            why.append(f"게임 실행 중: {name}")
        low = (cmd or "").lower()
        why.extend(f"{s} 실행 중" for s in BLOCKING_SCRIPTS if s in low)
    return sorted(set(why))


def _status_path() -> Path | None:
    d = Path.home() / ".claude" / "bg-progress.d"
    return d / f"server-{os.getpid()}.txt" if d.is_dir() else None


def main() -> int:
    why = blockers(processes())
    if why:
        print("[거부] " + " · ".join(why))
        return 2
    key = read_key()
    if not key:
        print(f"[거부] {settings.ENV_FILE} 에 {settings.KEY_VAR} 가 없다")
        return 2
    os.environ[settings.KEY_VAR] = key  # 이 프로세스에만
    from .app import Service, make_server
    from .cache import Cache
    from .upstream import Upstream
    svc = Service(Cache(settings.DB_PATH), Upstream(key), log_path=settings.DB_PATH.parent / "server.log")
    srv = make_server(svc)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[OK]   중앙 서버 http://{settings.HOST}:{settings.PORT} · 저장소 {settings.DB_PATH}", flush=True)
    status = _status_path()
    started = time.monotonic()
    try:
        while True:
            if status is not None:
                mins = int((time.monotonic() - started) // 60)
                try:
                    status.write_text(f"중앙 서버 · 요청 {svc.requests:,} · 넥슨 {svc.upstream.sent:,} · "
                                      f"적중 {svc.hits:,} · {mins}분", encoding="utf-8")
                except OSError:
                    pass
            time.sleep(settings.WATCH_S)
            why = blockers(processes())
            if why:
                print("[내림] " + " · ".join(why), flush=True)
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        srv.shutdown()
        srv.server_close()
        svc.cache.close()
        if status is not None:
            status.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
