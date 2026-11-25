"""122단계 조각 ③ 대조 준비 — 선발을 새로 받고(`--prepare`) 모자란 카드의 시뮬레이터를 하루 200장씩 채운다(`--fill` · 이 PC 전용 · 배포판 밖).

판정 규칙은 ROADMAP "## 122단계" 의 **대조**. 이 PC 는 11장을 다 아는 사람이 89.4% 라 관문 B(95%)에 모자라 먼저 채운다.
받기와 채우기는 앱과 **같은 함수**(`squadcmp.collect` · `squadcmp.fill_cards`)를 부른다.

| 규칙 | 어디 |
|---|---|
| 진짜 데이터 폴더에는 한 줄도 안 쓴다 — 카드표(`card_extra`)와 시도 기록("c:")·rank.db 를 **읽기 전용**으로 복사해 온다 | `sync_cards` · `copy_rank_db` |
| 폴더는 `FIFA_DATA_DIR` — 비어 있거나 이 도구가 만든 표시 파일(`MARK`)이 있어야 한다 | `folder_why` |
| 하루 `CAP` 요청(스크래치 fifa.db 의 하루 계수) · 간격 2초 | `fill` |
| 앱 쪽 웹 길이 오늘 막혔거나(도는 중에 막혀도) 못 읽으면 요청 0 | `real_blocked` |
| 진짜 `.env` 의 홈페이지 데이터가 꺼져 있으면 요청 0 · 서비스 키는 그 파일의 `DEV_NEXON_API_KEY`(앱 키로 안 넘어간다) | `main` |
| 게임이 켜져 있으면 안 한다(도중에 켜지면 멈춤) | `gameguard` |
| 이쪽에서 막히면 **앱은 모른다**(진짜 폴더에 안 적는다) — 출력에 적는다 | `main` |

    $env:FIFA_DATA_DIR = "$env:LOCALAPPDATA\\squadcmp_check"
    python tools/squadcmp_check.py --prepare   # 선발 받기(오픈API 약 3만 요청 · 약 13분). 대조하는 날 한 번 더(24시간 안이어야 한다)
    python tools/squadcmp_check.py --fill      # 하루 한 번(약 10분)
    python tools/squadcmp_check.py --status    # 요청 없음
    python tools/squadcmp_check.py --delete    # 다 쓴 뒤 폴더째

출력에 닉네임·계정 번호는 없다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

os.environ["FIFA_NO_CRED"] = "1"

from measure_squadcmp import copy_rank_db, is_real_folder  # noqa: E402 — config 를 import 하지 않는다

MARK = "squadcmp_check.txt"
CAP = 200                 # 하루 시뮬레이터 요청(ROADMAP 대조 — 이 PC 의 홈페이지 합계를 하루 1,300 아래로)
DEV_KEY_VAR = "DEV_NEXON_API_KEY"
CARD_COLS = "spid, height, weight, body, page_on, pos_ovr, club_tc, rel_tc, sim_on, c6_grade"


def folder_why(data_dir: str, default_dir: Path, files: list[str]) -> str:
    """쓰면 안 되는 폴더인 까닭 — 없으면 "". files 는 config 를 import 하기 **전에** 본 폴더 안 이름들."""
    if not data_dir:
        return "FIFA_DATA_DIR 이 없습니다"
    if is_real_folder(Path(data_dir), default_dir):
        return "진짜 데이터 폴더입니다"
    if files and MARK not in files:
        return "비어 있지 않고 이 도구가 만든 폴더도 아닙니다"
    return ""


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def real_blocked(real: Path, day: str) -> bool:
    """앱 쪽 카드 길이 오늘 막혔나 — 못 읽으면 막힌 것으로 본다."""
    import store
    try:
        conn = _ro(real)
        try:
            return store.budget_hit_429(conn, day, store.card_block_kinds())
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return True


def sync_cards(real: Path, fifa: Path) -> int:
    """앱이 그동안 채운 카드를 가져온다(요청을 아낀다) → 새로 온 카드 수. 여기서 더 늦게 읽은 카드는 안 덮는다."""
    import store
    src = _ro(real)
    try:
        cards = src.execute(f"SELECT {CARD_COLS} FROM card_extra").fetchall()
        tries = src.execute("SELECT key, day, fails FROM card_tries WHERE key LIKE 'c:%'").fetchall()
    finally:
        src.close()
    cols = [c.strip() for c in CARD_COLS.split(",")]
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols[1:])
    conn = store.open_db(fifa)
    try:
        with conn:
            before = conn.total_changes
            conn.executemany(f"INSERT INTO card_extra ({CARD_COLS}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT(spid) DO UPDATE SET {sets}"
                             " WHERE COALESCE(excluded.sim_on, '') > COALESCE(card_extra.sim_on, '')", cards)
            n = conn.total_changes - before
            conn.executemany("INSERT OR REPLACE INTO card_tries (key, day, fails) VALUES (?, ?, ?)", tries)
        return n
    finally:
        conn.close()


def status(squads: Path, fifa: Path) -> dict:
    """선발 수 · 11장을 다 아는 사람 · 모르는 카드. 관문 B 의 어림이다(판수·시즌 조건은 보지 않는다)."""
    import squadcmp
    import store
    conn = store.open_db(fifa)
    try:
        known = {r[0] for r in conn.execute("SELECT spid FROM card_extra WHERE sim_on IS NOT NULL")}
    finally:
        conn.close()
    people = full = 0
    miss: set[int] = set()
    sq = squadcmp.open_existing(squads)
    if sq is not None:
        try:
            for (body,) in sq.execute("SELECT lineup FROM squads WHERE lineup IS NOT NULL"):
                gone = {c[0] for c in squadcmp.unpack_lineup(body)} - known
                people += 1
                full += not gone
                miss |= gone
        finally:
            sq.close()
    return {"people": people, "full": full, "missing": len(miss), "known": len(known)}


def status_line(st: dict) -> str:
    pct = 100 * st["full"] / st["people"] if st["people"] else 0.0
    return f"선발 {st['people']:,}명 · 11장을 다 아는 사람 {st['full']:,}명({pct:.1f}%) · 모르는 카드 {st['missing']:,}장"


def open_gate(key: str) -> None:
    """이 프로세스의 config 값만 바꿔 관문을 연다 — 앱 모듈에는 관문을 건너뛰는 길이 없다."""
    import config
    config.WEB_DATA = config.RANK_COLLECT = True
    config.SQUAD_CMP_NOTICE_VERSION = config.NOTICE_ACCEPTED = config.NOTICE_VERSION
    config.SQUAD_CMP_ON, config.API_KEY, config.KEY_STATE = "2026-01-01T00:00:00", key, "ok"
    config.SQUAD_CMP_CARD_DAILY_REQ = config.SQUAD_CMP_CARD_TICK_REQ = CAP


def fill(real: Path, squads: Path, fifa: Path, today: date, cancel, *, fetch_sim=None, wait=time.sleep, log=print):
    """오늘 몫 → squadcmp.FillResult. 앱 쪽이 막혔으면 요청 없이 stop "real_blocked"."""
    import playerinfo
    import squadcmp
    day = today.isoformat()
    if real_blocked(real, day):
        return squadcmp.FillResult(stop="real_blocked")
    fetch = fetch_sim or (lambda s: playerinfo.fetch_player_ability(s, 1))
    n = [0]

    def counted(spid):
        n[0] += 1
        if n[0] % 20 == 0:
            log(f"요청 {n[0]}/{CAP}")
        return fetch(spid)

    return squadcmp.fill_cards(squads, fifa, today, cancel=lambda: cancel() or real_blocked(real, day), fetch_sim=counted, wait=wait)


def prepare(squads: Path, fifa: Path, api_key: str, cancel, *, log=print):
    """선발을 처음부터 다시 받는다 → squadcmp.Result."""
    import rankcollect
    import squadcmp
    from nexon_api import FCOnlineAPI
    squadcmp.purge(squads)
    log("선발을 받습니다")
    return squadcmp.collect(squads, fifa, rankcollect.open_rank_db_ro, lambda: FCOnlineAPI(api_key), cancel=cancel)


def delete(folder: Path) -> bool:
    if not (folder / MARK).exists():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for name in ("prepare", "fill", "status", "delete"):
        g.add_argument(f"--{name}", action="store_true")
    args = ap.parse_args(argv)
    data_dir = os.environ.get("FIFA_DATA_DIR", "").strip()
    folder = Path(data_dir)
    files = [p.name for p in folder.iterdir()] if data_dir and folder.is_dir() else []   # config 를 import 하면 폴더와 파일이 생긴다 — 그 전에

    import config
    import gameguard
    default_dir = config._default_data_dir()
    why = folder_why(data_dir, default_dir, files)
    if not why and not args.prepare and MARK not in files:
        why = "먼저 --prepare 를 돌립니다"
    if not why and (args.prepare or args.fill) and gameguard.game_running():
        why = "게임이 켜져 있습니다"
    env = {}
    if not why and (args.prepare or args.fill):
        from dotenv import dotenv_values
        env = dotenv_values(default_dir / ".env", encoding="utf-8-sig")
        if args.fill and (env.get(config.WEB_DATA_VAR) or "").strip() != "1":
            why = "홈페이지 데이터가 꺼져 있습니다"
        if args.prepare and not (env.get(DEV_KEY_VAR) or "").strip():
            why = f"{DEV_KEY_VAR} 가 없습니다"
    if why:
        print(f"[거부] {why}")
        return 2
    if args.delete:
        ok = delete(folder)
        print("폴더를 지웠습니다" if ok else "[FAIL] 폴더가 남았습니다. 다른 연결이 열고 있습니다")
        return 0 if ok else 1

    real, fifa, squads = default_dir / "fifa.db", config.DB_PATH, config.squads_db_path()
    if args.status:
        print(status_line(status(squads, fifa)))
        return 0
    open_gate((env.get(DEV_KEY_VAR) or "").strip() or "fill-only")
    (folder / MARK).write_text("tools/squadcmp_check.py 가 만든 폴더입니다. --delete 로 지웁니다.\n", encoding="utf-8")
    print(f"앱에서 가져온 카드 {sync_cards(real, fifa):,}장")
    cancel = gameguard.watcher()
    if args.prepare:
        copy_rank_db(default_dir / "rank.db", config.RANK_DB_PATH)
        res = prepare(squads, fifa, config.API_KEY, cancel)
        print(f"[{'FAIL' if res.stop else 'OK'}] 멈춤 '{res.stop}' · 정상 {res.ok:,} · 저장 안 함 {res.transient} · 요청 {res.requests:,} · {res.seconds:.0f}초")
        bad = bool(res.stop)
    else:
        res = fill(real, squads, fifa, date.today(), cancel)
        bad = res.stop not in ("", "cap") or bool(res.error)
        print(f"[{'FAIL' if bad else 'OK'}] 멈춤 '{res.stop}' {res.error} · 요청 {res.requests} · 저장 {res.done} · 시작할 때 남은 카드 {res.planned}")
        if res.blocked:
            print("[주의] 넥슨이 막았습니다. 앱은 이 막힘을 모르니 오늘은 앱의 카드 자동 채우기도 끄는 것이 좋습니다")
    print(status_line(status(squads, fifa)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
