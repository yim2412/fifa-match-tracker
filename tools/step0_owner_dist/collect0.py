"""42단계 단계 0 실측 수집 — 스크래치 전용. 구단주 층 4 x N명 x 최근 30판의 지표 9개 재료(나/상대 값)만 jsonl 로.
닉네임·ouid·경기 상세는 디스크에 안 남긴다. 재개: 이미 rows.jsonl 에 있는 (stratum, rank) 는 건너뜀.
    python collect0.py --per-stratum 40 [--limit 2]
"""
import argparse, json, random, sqlite3, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config, habits, nexon_api  # noqa: E402

OUT = Path(__file__).parent / "rows100.jsonl"
PROG = Path.home() / ".claude" / "bg-progress.d" / "owner0.txt"
GAP = 0.25
SEED = 20261012
TIERS = [(1, 200), (201, 1000), (1001, 3000), (3001, 10000)]
MATCHTYPE = 52
N_MATCH = 100


def dev_key() -> str:
    for line in (config.DATA_DIR / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DEV_NEXON_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("DEV_NEXON_API_KEY 없음")


def sample(per: int):
    rc = sqlite3.connect(f"file:{config.DATA_DIR / 'rank.db'}?mode=ro", uri=True)
    sid = rc.execute("SELECT id FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1").fetchone()[0]
    out = []
    for i, (lo, hi) in enumerate(TIERS):
        rows = rc.execute("SELECT rank, profile_sn, nickname FROM snapshot_rows WHERE snapshot_id=? AND rank BETWEEN ? AND ? ORDER BY rank",
                          (sid, lo, hi)).fetchall()
        rng = random.Random(SEED + i)
        for r in rng.sample(rows, per):
            out.append((i + 1, r[0], r[1], r[2]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-stratum", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    api = nexon_api.FCOnlineAPI(dev_key(), cache_dir=None)
    fc = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    known_ouid = {r[0] for r in fc.execute("SELECT ouid FROM ranker_squads WHERE ouid IS NOT NULL")}
    known_match = None  # 필요할 때만 질의
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            j = json.loads(line)
            done.add((j["stratum"], j["rank"]))
    todo = [s for s in sample(a.per_stratum) if (s[0], s[1]) not in done]
    # 층을 번갈아 — 중간에 멈춰도 층 균형이 유지된다
    todo.sort(key=lambda s: (s[1] and 0, ))
    by = {k: [s for s in todo if s[0] == k] for k in range(1, 5)}
    order = [by[k][i] for i in range(max(map(len, by.values()), default=0)) for k in range(1, 5) if i < len(by[k])]
    if a.limit:
        order = order[: a.limit]
    t_start = time.time()
    last = [0.0]
    stats = {"req": 0, "sec": 0.0}

    def call(fn, *args, **kw):
        wait = GAP - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        for attempt in (0, 1):
            t0 = time.time()
            try:
                r = fn(*args, attempts=1, **kw)
                return r
            except nexon_api.NexonAPIError as e:
                if e.status == 429 and attempt == 0:
                    print("429 — 60초 쉼", flush=True)
                    time.sleep(60)
                    continue
                raise
            finally:
                last[0] = time.time()
                stats["req"] += 1
                stats["sec"] += last[0] - t0

    with OUT.open("a", encoding="utf-8") as f:
        for n, (stratum, rank, sn, nick) in enumerate(order, 1):
            r0, s0 = stats["req"], stats["sec"]
            try:
                ouid = call(api.get_ouid, nick)
            except nexon_api.NexonAPIError as e:
                f.write(json.dumps({"stratum": stratum, "rank": rank, "error": f"ouid {e.code}"}) + "\n"); f.flush()
                continue
            ids = call(api.get_match_ids, ouid, matchtype=MATCHTYPE, limit=N_MATCH)
            hits = 0
            details = []
            if ids:
                ph = ",".join("?" * len(ids))
                have = {r[0] for r in fc.execute(f"SELECT match_id FROM matches WHERE match_id IN ({ph})", ids)}
                hits = len(have)
            for mid in ids:
                try:
                    details.append(call(api.get_match_detail, mid))
                except nexon_api.NexonAPIError:
                    pass
            tot = habits.side_totals(details, ouid)
            dates = sorted(d.get("matchDate", "") for d in details)
            rec = {"stratum": stratum, "rank": rank, "n_ids": len(ids), "n_games": tot.games, "ouid_hit": ouid in known_ouid,
                   "detail_hits": hits, "reqs": stats["req"] - r0, "secs": round(stats["sec"] - s0, 2),
                   "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                   "dates": [d[:10] for d in dates],
                   "rows": [{"res": r.result, "mine": r.mine, "opp": r.opp} for r in tot.rows]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            el = time.time() - t_start
            msg = f"owner0 수집 {n}/{len(order)} · 경과 {int(el)}초 · 요청 {stats['req']} · 요청당 {stats['sec']/max(stats['req'],1):.2f}초"
            PROG.parent.mkdir(parents=True, exist_ok=True)
            PROG.write_text(msg, encoding="utf-8")
            print(msg, flush=True)
    try:
        PROG.unlink()
    except OSError:
        pass
    print("[OK] 끝", stats, flush=True)


if __name__ == "__main__":
    main()
