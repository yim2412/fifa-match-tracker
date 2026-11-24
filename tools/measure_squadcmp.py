"""122단계 조각 ① 실측 — 1만 명 선발 받기(`squadcmp.collect`)가 실제로 얼마나 걸리고 얼마나 커지나(이 PC 전용 · 배포판 밖).

진짜 데이터 폴더에는 한 줄도 쓰지 않는다: `FIFA_DATA_DIR` 로 **비어 있는 스크래치 폴더**를 받아 rank.db 사본만 만들고, 끝나면(중간에 실패해도)
폴더를 통째로 지운다. 앱 코드에는 관문을 건너뛰는 길이 없어 이 프로세스의 config 값만 바꿔 연다.

| 거부하는 것 | 왜 |
|---|---|
| `FIFA_DATA_DIR` 이 없거나 기본 폴더 · 이미 파일이 있음 | 끝나고 지우는 폴더라 남의 파일을 지우지 않게 |
| 게임이 켜져 있음(도중에 켜지면 멈춤) | 전역 규칙(gameguard) |
| `DEV_NEXON_API_KEY` 환경 변수가 없음 | 앱 키로 넘어가지 않는다 |

    FIFA_DATA_DIR=<빈 폴더> DEV_NEXON_API_KEY=... python tools/measure_squadcmp.py [--cap 요청수] [--again]

`--cap` 은 하루 상한을 줄인다(작게 시험). `--again` 은 다 받은 뒤 한 번 더 돌려 "다시 물을 사람 0" 을 본다. 출력에 닉네임·계정 번호는 없다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["FIFA_NO_CRED"] = "1"


def refuse(data_dir: str, default_dir: Path, key: str, game: bool) -> str:
    """돌리면 안 되는 까닭 — 없으면 ""."""
    if not data_dir:
        return "FIFA_DATA_DIR 이 없습니다"
    p = Path(data_dir).resolve()
    if p == default_dir.resolve() or default_dir.resolve() in p.parents or p in default_dir.resolve().parents:
        return "진짜 데이터 폴더입니다"
    if p.exists() and any(p.iterdir()):
        return "폴더가 비어 있지 않습니다(끝나면 통째로 지우는 폴더입니다)"
    if not key:
        return "DEV_NEXON_API_KEY 가 없습니다"
    return "게임이 켜져 있습니다" if game else ""


def copy_rank_db(src: Path, dst: Path) -> None:
    s = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=0)
    ap.add_argument("--again", action="store_true")
    args = ap.parse_args()
    data_dir, key = os.environ.get("FIFA_DATA_DIR", "").strip(), os.environ.get("DEV_NEXON_API_KEY", "").strip()
    was_there = bool(data_dir) and Path(data_dir).exists() and any(Path(data_dir).iterdir())   # config 를 import 하면 폴더를 만든다 — 그 전에

    import config
    import gameguard
    default_dir = config._default_data_dir()
    why = refuse(data_dir, default_dir, key, gameguard.game_running())
    if was_there and not why:
        why = "폴더가 비어 있지 않습니다"
    if why:
        print(f"[거부] {why}")
        return 2

    import rankcollect
    import squadcmp
    import store
    from nexon_api import FCOnlineAPI
    try:
        copy_rank_db(default_dir / "rank.db", config.RANK_DB_PATH)
        config.WEB_DATA = config.RANK_COLLECT = True
        config.SQUAD_CMP_NOTICE_VERSION = config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        config.SQUAD_CMP_ON, config.API_KEY, config.KEY_STATE = "2026-01-01T00:00:00", key, "ok"
        config.SQUAD_CMP_SNAP_MAX_H = max(config.SQUAD_CMP_SNAP_MAX_H, 48)   # 이 PC 가 하루 쉬었어도 잰다(받기 속도만 본다)
        if args.cap:
            config.SQUAD_CMP_DAILY_REQ = args.cap
        game = gameguard.watcher()
        done = threading.Event()
        conn = store.open_db(config.DB_PATH)
        conn.close()

        def watch() -> None:
            t0 = time.monotonic()
            while not done.wait(10):
                c = store.open_db(config.DB_PATH)
                try:
                    n = store.squad_req(c, config.SQUAD_CMP_ON)
                finally:
                    c.close()
                dt = time.monotonic() - t0
                print(f"요청 {n:,} · {dt:.0f}초 · 초당 {n / dt:.0f}")

        for turn in range(2 if args.again else 1):
            done.clear()
            th = threading.Thread(target=watch, daemon=True)
            th.start()
            res = squadcmp.collect(config.squads_db_path(), config.DB_PATH, rankcollect.open_rank_db_ro, lambda: FCOnlineAPI(key), cancel=game)
            done.set()
            th.join()
            size = sum(p.stat().st_size for p in squadcmp.db_files(config.squads_db_path()) if p.exists())
            print(f"[{turn + 1}번째] 멈춤 '{res.stop}' · 차례 {res.due:,} · 정상 {res.ok:,} · 사라짐 {res.gone} · 경기 없음 {res.nogame}"
                  f" · 꼴 {res.format} · 저장 안 함 {res.transient} ({res.error}) · 요청 {res.requests:,} · {res.seconds:.0f}초"
                  f" · 초당 {res.requests / max(res.seconds, 0.001):.1f} · squads.db {size / 1024:.0f}KB · 개발 키 판정 {res.devkey}")
        c = squadcmp.open_existing(config.squads_db_path())
        if c is not None:
            n, cards = 0, set()
            for r in c.execute("SELECT lineup FROM squads WHERE lineup IS NOT NULL"):
                n += 1
                cards.update(x[0] for x in squadcmp.unpack_lineup(r[0]))
            age = [time.time() - r[0] for r in c.execute("SELECT match_at FROM squads WHERE match_at IS NOT NULL")]
            c.close()
            age.sort()
            mid = age[len(age) // 2] / 86400 if age else 0
            p95 = age[int(len(age) * 0.95)] / 86400 if age else 0
            print(f"선발 {n:,}명 · 서로 다른 카드 {len(cards):,}장 · 선발 나이 절반 {mid:.1f}일 · 95% {p95:.1f}일")
            t = time.monotonic()
            squadcmp.purge(config.squads_db_path())
            size = sum(p.stat().st_size for p in squadcmp.db_files(config.squads_db_path()) if p.exists())
            print(f"지우기 {time.monotonic() - t:.2f}초 · 지운 뒤 {size / 1024:.0f}KB")
        return 0
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        print("스크래치 폴더를 지웠습니다" if not Path(data_dir).exists() else "[주의] 스크래치 폴더가 남았습니다: 다른 연결이 열고 있습니다")


if __name__ == "__main__":
    raise SystemExit(main())
