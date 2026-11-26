"""122단계 조각 ③ 대조 — 선발을 새로 받고(`--prepare`) 모자란 카드의 시뮬레이터를 하루 200장씩 채운 뒤(`--fill`) 대조한다(`--check` · 이 PC 전용 · 배포판 밖).

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
    python tools/squadcmp_check.py --check     # 대조 ⓐⓑⓒ(ⓐ 만 시뮬레이터 최대 55요청 · ⓑ 는 dev_matches.db 를 읽기만)
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
from datetime import date, datetime
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


# ── 대조(ROADMAP 122 "대조" — 판정 규칙은 거기서 옮겼다. 결과를 보고 바꾸지 않는다) ─────────────────────────

A_PEOPLE = 5            # ⓐ 시뮬레이터에 직접 물을 사람 수(55장)
B_MIN_CELL = 400        # ⓑ 나를 뺀 인원이 이 이상인 칸만
B_MEAN_TOL = 15.0       # ⓑ 칸 평균 차 ±점
B_COUNT_TOL = 0.08      # ⓑ 인원 차 ±비율
FRESH_H = 24            # ⓑ 다 받은 지 이 시간 안
C_CUT_CARDS = 1200      # ⓒ 많이 쓰인 순으로 이만큼만 남긴 카드표
C_LOW_GATE = 0.40       # ⓒ 그 카드표로 낮춘 관문 B


def _inputs(squads: Path, fifa: Path, rank: Path):
    """(스냅숏 줄, 찍은 시각, {번호: (경기 시각, 받은 시각, 선발)}, 카드표) — 다 읽기만."""
    import squadcmp
    import store
    rc = _ro(rank)
    try:
        snap, rows = squadcmp.snapshot_rows(rc)
    finally:
        rc.close()
    sq = _ro(squads)
    try:
        got = {sn: (at, la, squadcmp.unpack_lineup(b)) for sn, at, la, b in sq.execute(
            "SELECT profile_sn, match_at, lineup_at, lineup FROM squads WHERE lineup IS NOT NULL")}
    finally:
        sq.close()
    fc = _ro(fifa)
    try:
        extras = store.load_card_extras(fc, {c[0] for v in got.values() for c in v[2]})
    finally:
        fc.close()
    return rows, snap[1] if snap else None, got, extras


def _ovr_of(rows, got, extras, now):
    import squadcmp
    return squadcmp.compare(rows, got, extras, me_sn=None, my_lineups=[], season_start=None, now=now, snap_at=None)


def check_a(got: dict, extras: dict, today: date, ask, *, adapt: int | None = None, cache: dict | None = None) -> list[tuple[float, float]]:
    """ⓐ — 날짜로 뽑은 5명. ask(spid, 강화) → 그 강화·적응도 5 의 시뮬레이터 16칸 {칸: OVR}. → [(앱 값, 정답)].
    cache 를 주면 같은 (카드, 강화)는 다시 묻지 않는다(ⓒ 가 요청 없이 되풀이한다). adapt = 앱 쪽 ADAPT_OVR 을 바꿔 끼울 때(ⓒ)."""
    import random
    import config
    import squadcmp
    keep = config.ADAPT_OVR
    if adapt is not None:
        config.ADAPT_OVR = adapt
    try:
        ok = sorted(sn for sn, v in got.items() if squadcmp.lineup_ovr(v[2], extras) is not None)
        pick = random.Random(today.toordinal()).sample(ok, min(A_PEOPLE, len(ok)))
        cache = {} if cache is None else cache
        out = []
        for sn in pick:
            cards = got[sn][2]
            truth = []
            for spid, po, grade in cards:
                if (spid, grade) not in cache:
                    cache[(spid, grade)] = ask(spid, grade)
                truth.append(cache[(spid, grade)][config.SIM_POS_OF[po]])
            out.append((squadcmp.lineup_ovr(cards, extras), sum(truth) / len(truth)))
        return out
    finally:
        config.ADAPT_OVR = keep


def dev_lineups(dev: Path, sns: set[int], now) -> dict:
    """ⓑ — dev_matches.db(읽기만)에서 사람마다 마지막 감독모드 경기 가운데 양쪽 선발 11명이 다 있는 첫 판의 내 쪽 선발.
    앱(squadcmp.lineup_of)과 같은 규칙이고 받은 시각은 지금으로 둔다(이 수집은 10분마다 돈다)."""
    import squadcmp
    conn = _ro(dev)
    try:
        owners = [(oid, sn) for oid, sn in conn.execute("SELECT id, profile_sn FROM owners WHERE profile_sn IS NOT NULL") if sn in sns]
        bodies: dict[int, list] = {}

        def lineup(lid):
            if lid is None:
                return None
            if lid not in bodies:
                row = conn.execute("SELECT body FROM lineups WHERE id = ?", (lid,)).fetchone()
                bodies[lid] = squadcmp.unpack_lineup(row[0]) if row else None
            return bodies[lid]

        out, t = {}, int(now.timestamp())
        for oid, sn in owners:
            games = []
            for mine, other in (("l1", "l2"), ("l2", "l1")):
                col = "o1" if mine == "l1" else "o2"
                games += conn.execute(f"SELECT at, {mine}, {other} FROM games WHERE {col} = ? ORDER BY at DESC LIMIT ?",
                                      (oid, config_matches())).fetchall()
            for at, lm, lo in sorted(games, key=lambda g: -g[0])[:config_matches()]:
                a, b = lineup(lm), lineup(lo)
                if a is not None and b is not None and len(a) == len(b) == squadcmp.LINEUP_N:
                    out[sn] = (at, t, a)
                    break
        return out
    finally:
        conn.close()


def config_matches() -> int:
    import config
    return config.SQUAD_CMP_MATCHES


def judge_b(app: dict, dev: dict) -> list[tuple[int, int, int, float, float, bool]]:
    """칸마다 (칸, 앱 인원, dev 인원, 앱 평균, dev 평균, 통과) — 앱 인원이 B_MIN_CELL 이상인 칸만."""
    out = []
    for c in sorted(app):
        n, m = app[c]
        if n < B_MIN_CELL:
            continue
        dn, dm = dev.get(c, (0, float("nan")))
        ok = dn > 0 and abs(m - dm) <= B_MEAN_TOL and abs(dn - n) <= B_COUNT_TOL * n
        out.append((c, n, dn, m, dm, ok))
    return out


def check(squads: Path, fifa: Path, rank: Path, dev: Path, today: date, now, ask, *, log=print) -> bool:
    """ⓐ ⓑ ⓒ 를 차례로 → 모두 통과했나. ask = None 이면 ⓐ 와 ⓐ 의 ⓒ 를 건너뛴다(요청 0)."""
    import config
    import squadcmp
    rows, taken, got, extras = _inputs(squads, fifa, rank)
    newest = max((v[1] for v in got.values()), default=0)
    oldest = min((v[1] for v in got.values()), default=0)
    log(f"스냅숏 {taken} · 선발 {len(got):,}명(받은 때 {datetime.fromtimestamp(oldest):%m-%d %H:%M} ~ "
        f"{datetime.fromtimestamp(newest):%m-%d %H:%M}) · 카드표 {len(extras):,}장")
    ok = True
    if now.timestamp() - oldest > FRESH_H * 3600:
        log(f"[FAIL] ⓑ 전제 — 선발을 받은 지 {FRESH_H}시간이 넘었습니다. --prepare 를 한 번 더 돌립니다")
        return False
    base = _ovr_of(rows, got, extras, now)
    gate = base.lineup_pct >= 100 * config.SQUAD_CMP_GATE_LINEUP and base.card_pct >= 100 * config.SQUAD_CMP_GATE_CARDS
    log(f"{'[OK]  ' if gate else '[FAIL]'} 관문 A {base.lineup_pct:.1f}% · B {base.card_pct:.1f}%")
    ok &= gate
    cache: dict = {}
    if ask is not None:
        a = check_a(got, extras, today, ask, cache=cache)
        good = len(a) == A_PEOPLE and all(x == y for x, y in a)
        log(f"{'[OK]  ' if good else '[FAIL]'} ⓐ 시뮬레이터 정답과 " + " · ".join(f"{x:.2f}/{y:.2f}" for x, y in a) + f" (요청 {len(cache)})")
        ok &= good
        bad3 = check_a(got, extras, today, ask, adapt=3, cache=cache)
        caught = any(x != y for x, y in bad3)
        log(f"{'[OK]  ' if caught else '[FAIL]'} ⓒ 적응도 몫을 3 으로 바꾸면 ⓐ 가 틀린다")
        ok &= caught
    app_cells = squadcmp.cell_means(rows, base.ovr_of)
    dev_got = dev_lineups(dev, {r["profile_sn"] for r in rows}, now)
    dev_cells = squadcmp.cell_means(rows, _ovr_of(rows, dev_got, {**extras, **_extras_for(fifa, dev_got, extras)}, now).ovr_of)
    b = judge_b(app_cells, dev_cells)
    for c, n, dn, m, dm, good in b:
        log(f"   칸 {c}: 앱 {n:,}명 {m:,.0f}점 · 수집 {dn:,}명 {dm:,.0f}점 · 차 {m - dm:+.1f}점 {dn - n:+,}명 {'' if good else '← 벗어남'}")
    good = bool(b) and all(x[-1] for x in b)
    log(f"{'[OK]  ' if good else '[FAIL]'} ⓑ 칸 {len(b)}개(인원 {B_MIN_CELL} 이상) · 평균 ±{B_MEAN_TOL:g}점 · 인원 ±{B_COUNT_TOL:.0%}")
    ok &= good
    shifted = {sn: (v[0], v[1], [(s, (p % 27) + 1, g) for s, p, g in v[2]]) for sn, v in got.items()}
    sb = judge_b(squadcmp.cell_means(rows, _ovr_of(rows, shifted, extras, now).ovr_of), dev_cells)
    caught = not sb or not all(x[-1] for x in sb)
    log(f"{'[OK]  ' if caught else '[FAIL]'} ⓒ 선발 자리 번호를 한 칸 밀면 ⓑ 가 틀린다")
    ok &= caught
    use: dict[int, int] = {}
    for v in got.values():
        for s, _p, _g in v[2]:
            use[s] = use.get(s, 0) + 1
    top = set(sorted(use, key=lambda s: -use[s])[:C_CUT_CARDS])
    cut = {s: e for s, e in extras.items() if s in top}
    cut_res = _ovr_of(rows, got, cut, now)
    cut_cells = squadcmp.cell_means(rows, cut_res.ovr_of)
    off = [x[0] for x in judge_b(app_cells, cut_cells) if not abs(x[3] - x[4]) <= B_MEAN_TOL]   # 칸이 사라져도(nan) 벗어남
    low_pass = cut_res.card_pct >= 100 * C_LOW_GATE
    log(f"{'[OK]  ' if low_pass and off else '[FAIL]'} ⓒ 카드표 {C_CUT_CARDS:,}장 · 관문 {C_LOW_GATE:.0%}({cut_res.card_pct:.1f}%) 이면 "
        f"±{B_MEAN_TOL:g}점을 벗어나는 칸 {len(off)}개")
    ok &= low_pass and bool(off)
    silent = cut_res.card_pct < 100 * config.SQUAD_CMP_GATE_CARDS
    log(f"{'[OK]  ' if silent else '[FAIL]'} ⓒ 같은 카드표로 관문 {config.SQUAD_CMP_GATE_CARDS:.0%} 이면 침묵")
    ok &= silent
    return ok


def asker(real: Path, day: str, cancel, *, fetch=None, wait=time.sleep):
    """ⓐ 의 묻기 — (카드, 강화) → 적응도 5 시뮬레이터 16칸. 요청 사이 간격 · 앱 쪽 웹 길이 오늘 막혔거나 게임이 켜지면 멈춘다(예외)."""
    import config
    import playerinfo
    fetch = fetch or (lambda s, g: playerinfo.fetch_player_ability(s, strong=g, adapt=5).position_ovrs)

    def ask(spid, grade):
        if cancel() or real_blocked(real, day):
            raise RuntimeError("멈춤 — 게임이 켜졌거나 앱 쪽 홈페이지 길이 오늘 막혔습니다")
        got = fetch(spid, grade)
        wait(config.CARD_BACKFILL_GAP_S)
        return got
    return ask


def _extras_for(fifa: Path, got: dict, have: dict) -> dict:
    """dev 쪽 선발에만 나온 카드의 카드표(같은 fifa.db · 읽기만)."""
    import store
    want = {c[0] for v in got.values() for c in v[2]} - set(have)
    if not want:
        return {}
    fc = _ro(fifa)
    try:
        return store.load_card_extras(fc, want)
    finally:
        fc.close()


def delete(folder: Path) -> bool:
    if not (folder / MARK).exists():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for name in ("prepare", "fill", "status", "delete", "check"):
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
    if not why and (args.prepare or args.fill or args.check) and gameguard.game_running():
        why = "게임이 켜져 있습니다"
    env = {}
    if not why and (args.prepare or args.fill or args.check):
        from dotenv import dotenv_values
        env = dotenv_values(default_dir / ".env", encoding="utf-8-sig")
        if (args.fill or args.check) and (env.get(config.WEB_DATA_VAR) or "").strip() != "1":
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
    if args.check:
        open_gate("check-only")   # ⓐ 의 시뮬레이터만 — 오픈API 는 부르지 않는다
        ok = check(squads, fifa, config.RANK_DB_PATH, default_dir / "dev_matches.db", date.today(), datetime.now(),
                   asker(real, date.today().isoformat(), gameguard.watcher()))
        print("\n[OK] 대조 통과" if ok else "\n[FAIL] 대조 실패 — 까닭을 찾기 전에는 공개하지 않습니다")
        return 0 if ok else 1
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
