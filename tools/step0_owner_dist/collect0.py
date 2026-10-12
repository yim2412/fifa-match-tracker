"""42단계 단계 0 실측 수집 — 구단주 층 4개 × 최근 100판의 지표 9개 재료(나/상대 값)만 jsonl 로.
닉네임·ouid·경기 상세는 디스크에 안 남긴다. 재개: 이미 rows100.jsonl 에 있는 (stratum, rank) 는 건너뜀.
    python collect0.py [--limit 8] [--workers 8]
표본은 3차 R4(ROADMAP 42 "3차 수정 R4") — 층 1 은 200명 전원, 층 2~4 는 240명. 앞 83명은 R3 때 뽑은 그대로.
"""
import argparse, json, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config, habits, nexon_api  # noqa: E402

OUT = Path(__file__).parent / "rows100.jsonl"
PROG = Path.home() / ".claude" / "bg-progress.d" / "owner0.txt"
SEED = 20261012
TIERS = [(1, 200), (201, 1000), (1001, 3000), (3001, 10000)]
MATCHTYPE = 52
N_MATCH = 100
# 표본을 뽑는 스냅숏 고정(10-12 단계 0 당시 최신). "최신" 으로 두면 수집 도중 새 스냅숏이 쌓일 때 재개분이 다른 사람들로 섞인다.
# 실측: (층, 순위) 키는 스냅숏이 달라도 같지만 그 자리의 닉네임은 스냅숏 6 대 7 에서 160명 전부 달랐다 —
# 그래서 rows 의 (stratum, rank) 만으로는 표본이 섞였는지 못 잡는다. 이 상수가 유일한 방어다
SNAPSHOT_ID = 7
PREFIX = 83                              # R3 때 뽑은 앞부분 — 이 수만큼은 옛 방식 그대로 뽑아야 같은 사람이 나온다
TARGET = {1: 200, 2: 240, 3: 240, 4: 240}  # R4
WORKERS = 8                              # R4 — 앱의 넥슨 웹 동시 상한(RANK_MAX_CONCURRENT)과 같은 기준
PAUSE_429 = 60


def dev_key() -> str:
    for line in (config.DATA_DIR / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DEV_NEXON_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("DEV_NEXON_API_KEY 없음")


def sample(target=TARGET, prefix=PREFIX):
    """층마다 옛 방식(rng.sample(rows, prefix), 시드 SEED+층번호-1)으로 앞부분을 뽑고, 나머지에서 별도 시드로 이어 뽑는다.
    random.sample 은 뽑는 수가 커지면 pool/set 방식이 바뀌어 같은 시드라도 앞부분이 안 이어질 수 있어서 둘로 나눴다."""
    rc = sqlite3.connect(f"file:{config.DATA_DIR / 'rank.db'}?mode=ro", uri=True)
    out = []
    for i, (lo, hi) in enumerate(TIERS):
        rows = rc.execute("SELECT rank, profile_sn, nickname FROM snapshot_rows WHERE snapshot_id=? AND rank BETWEEN ? AND ? ORDER BY rank",
                          (SNAPSHOT_ID, lo, hi)).fetchall()
        want = min(target[i + 1], len(rows))
        head = random.Random(SEED + i).sample(rows, min(prefix, want))
        taken = {r[0] for r in head}
        rest = [r for r in rows if r[0] not in taken]
        tail = random.Random(SEED + 100 + i).sample(rest, want - len(head))
        for r in head + tail:
            out.append((i + 1, r[0], r[1], r[2]))
    rc.close()
    return out


class Stop(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args()
    key = dev_key()
    fc0 = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    known_ouid = {r[0] for r in fc0.execute("SELECT ouid FROM ranker_squads WHERE ouid IS NOT NULL")}
    fc0.close()
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            j = json.loads(line)
            done.add((j["stratum"], j["rank"]))
    todo = [s for s in sample() if (s[0], s[1]) not in done]
    # 층을 번갈아 — 중간에 멈춰도 층 균형이 유지된다
    by = {k: [s for s in todo if s[0] == k] for k in range(1, 5)}
    order = [by[k][i] for i in range(max(map(len, by.values()), default=0)) for k in range(1, 5) if i < len(by[k])]
    if a.limit:
        order = order[: a.limit]
    total = len(order)
    print(f"표본 {sum(TARGET.values())}명 중 받은 것 {len(done)} · 이번에 {total}명 · 동시 {a.workers}", flush=True)

    local = threading.local()
    lock = threading.Lock()
    st = {"req": 0, "sec": 0.0, "done": 0, "pause_until": 0.0, "stop": None}
    t_start = time.time()

    def api():
        if not hasattr(local, "api"):
            local.api = nexon_api.FCOnlineAPI(key, cache_dir=None)
            local.fc = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        return local.api

    def call(fn, *args, **kw):
        for attempt in (0, 1):
            if st["stop"]:
                raise Stop()
            wait = st["pause_until"] - time.time()
            if wait > 0:
                time.sleep(wait)
            t0 = time.time()
            try:
                return fn(*args, attempts=1, **kw)
            except nexon_api.NexonAPIError as e:
                if e.status == 429:
                    with lock:
                        if attempt == 0:
                            st["pause_until"] = max(st["pause_until"], time.time() + PAUSE_429)
                            print(f"429 — 전원 {PAUSE_429}초 쉼", flush=True)
                        else:
                            st["stop"] = "429 두 번 — 멈춤(같은 명령으로 이어 받는다)"
                    if attempt == 0:
                        continue
                    raise Stop()
                raise
            finally:
                with lock:
                    st["req"] += 1
                    st["sec"] += time.time() - t0

    def one(item):
        stratum, rank, sn, nick = item
        if st["stop"]:
            return
        r0 = time.time()
        a_ = api()
        try:
            try:
                ouid = call(a_.get_ouid, nick)
            except nexon_api.NexonAPIError as e:
                rec = {"stratum": stratum, "rank": rank, "error": f"ouid {e.code}"}
            else:
                ids = call(a_.get_match_ids, ouid, matchtype=MATCHTYPE, limit=N_MATCH)
                hits = 0
                if ids:
                    ph = ",".join("?" * len(ids))
                    hits = len({r[0] for r in local.fc.execute(f"SELECT match_id FROM matches WHERE match_id IN ({ph})", ids)})
                details = []
                n_req = 2
                for mid in ids:
                    try:
                        details.append(call(a_.get_match_detail, mid))
                    except nexon_api.NexonAPIError:
                        pass
                    n_req += 1
                tot = habits.side_totals(details, ouid)
                dates = sorted(d.get("matchDate", "") for d in details)
                rec = {"stratum": stratum, "rank": rank, "n_ids": len(ids), "n_games": tot.games, "ouid_hit": ouid in known_ouid,
                       "detail_hits": hits, "reqs": n_req, "secs": round(time.time() - r0, 2),
                       "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                       "dates": [d[:10] for d in dates],
                       "rows": [{"res": r.result, "mine": r.mine, "opp": r.opp} for r in tot.rows]}
        except Stop:
            return                      # 끝까지 못 받은 사람은 안 적는다 — 다음 실행에서 다시
        except Exception as e:          # 네트워크 끊김 등 — 한 명 때문에 나머지를 멈추지 않는다(안 적었으니 다음 실행에서 다시)
            print(f"건너뜀 층{stratum} {rank}위: {type(e).__name__} {str(e)[:80]}", flush=True)
            return
        with lock:
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            st["done"] += 1
            el = time.time() - t_start
            msg = (f"owner0 3차 {st['done']}/{total} · 경과 {int(el)}초 · 요청 {st['req']} · "
                   f"초당 {st['req']/max(el,1):.0f}요청 · 응답 {st['sec']/max(st['req'],1):.2f}초")
            PROG.parent.mkdir(parents=True, exist_ok=True)
            PROG.write_text(msg, encoding="utf-8")
            print(msg, flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in [ex.submit(one, it) for it in order]:
            fut.result()
    try:
        PROG.unlink()
    except OSError:
        pass
    if st["stop"]:
        print(f"[FAIL] {st['stop']} · 이번에 받은 {st['done']}/{total}", flush=True)
        sys.exit(1)
    print(f"[OK] 끝 · {st['done']}/{total} · 요청 {st['req']} · {int(time.time()-t_start)}초", flush=True)


if __name__ == "__main__":
    main()
