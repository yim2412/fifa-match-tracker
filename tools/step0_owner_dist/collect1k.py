"""42단계 후속(ROADMAP "구단주 분포 › 다시 볼 조건" 2번) 단계 0 — 구단주 최근 N판(기본 1000)의 재료만 rows1k.jsonl 로.
이미 rows100/rows4 에 받은 1~3000위 구단주 중에서 층(1·2·3)마다 같은 수를 뽑는다 — 같은 사람이라 100판 값과 대조된다.
닉네임·ouid·경기 상세는 디스크에 안 남긴다. 재개: rows1k.jsonl 에 있는 (stratum, rank) 는 건너뜀.
    python collect1k.py [--limit 2] [--n 1000] [--per-stratum 50] [--workers 8]
"""
import argparse, json, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config, habits, nexon_api  # noqa: E402
import collect0  # noqa: E402  (sample/sample4/dev_key 재사용)

HERE = Path(__file__).parent
OUT = HERE / "rows1k.jsonl"
OUT_NEW = HERE / "rows1k_new.jsonl"       # 49단계 조각 ① 새 표본 검증 — OUT 에 있는 사람은 뽑지 않는다
PROG = Path.home() / ".claude" / "bg-progress.d" / "owner1k.txt"
SEED = 20261013
SEED_NEW = 20261014
PAGE = 100
PAUSE_429 = 60


class Stop(Exception):
    pass


def _keys(path):
    return {(j["stratum"], j["rank"]) for j in map(json.loads, path.read_text(encoding="utf-8").splitlines())} if path.exists() else set()


def pick(per_stratum, fresh=False):
    """collect0 의 3·4차 표본(닉네임 포함, rank.db 스냅숏 7 고정)에서 1~3층만, 층마다 per_stratum 명.
    fresh: 첫 1k 표본(rows1k.jsonl)에 쓴 사람을 뺀 나머지에서 새 시드로 — 조각 ① 새 표본 검증용."""
    pool = collect0.sample() + collect0.sample4()
    got = {}
    for s in pool:
        if s[0] in (1, 2, 3):
            got.setdefault((s[0], s[1]), s)
    if fresh:
        used = _keys(OUT)
        got = {k: v for k, v in got.items() if k not in used}
    out = []
    for st in (1, 2, 3):
        rows = sorted(v for k, v in got.items() if k[0] == st)
        out += random.Random((SEED_NEW if fresh else SEED) + st).sample(rows, min(per_stratum, len(rows)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--per-stratum", type=int, default=50)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fresh", action="store_true", help="새 표본 검증 — rows1k_new.jsonl 로, 첫 표본 사람은 뺀다")
    a = ap.parse_args()
    key = collect0.dev_key()
    out_path = OUT_NEW if a.fresh else OUT
    done = _keys(out_path)
    todo = [s for s in pick(a.per_stratum, a.fresh) if (s[0], s[1]) not in done]
    by = {k: [s for s in todo if s[0] == k] for k in (1, 2, 3)}
    order = [by[k][i] for i in range(max(map(len, by.values()), default=0)) for k in (1, 2, 3) if i < len(by[k])]
    if a.limit:
        order = order[: a.limit]
    total = len(order)
    print(f"1k · 최근 {a.n}판 · 받은 것 {len(done)} · 이번에 {total}명 · 동시 {a.workers} → {out_path.name}", flush=True)

    local = threading.local()
    lock = threading.Lock()
    st = {"req": 0, "sec": 0.0, "done": 0, "pause_until": 0.0, "stop": None}
    t_start = time.time()

    def api():
        if not hasattr(local, "api"):
            local.api = nexon_api.FCOnlineAPI(key, cache_dir=None)
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
                ids = []
                while len(ids) < a.n:
                    want = min(PAGE, a.n - len(ids))
                    page = call(a_.get_match_ids, ouid, matchtype=collect0.MATCHTYPE, offset=len(ids), limit=want)
                    ids += page
                    if len(page) < want:
                        break
                details = []
                n_req = 1 + (len(ids) + PAGE - 1) // PAGE
                for mid in ids:
                    try:
                        details.append(call(a_.get_match_detail, mid))
                    except nexon_api.NexonAPIError:
                        pass
                    n_req += 1
                tot = habits.side_totals(details, ouid)
                dates = sorted(d.get("matchDate", "") for d in details)
                rec = {"stratum": stratum, "rank": rank, "n_ids": len(ids), "n_games": tot.games,
                       "reqs": n_req, "secs": round(time.time() - r0, 2),
                       "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                       "rows": [{"res": r.result, "mine": r.mine, "opp": r.opp} for r in tot.rows]}
        except Stop:
            return
        except Exception as e:
            print(f"건너뜀 층{stratum} {rank}위: {type(e).__name__} {str(e)[:80]}", flush=True)
            return
        with lock:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            st["done"] += 1
            el = time.time() - t_start
            msg = (f"owner1k {st['done']}/{total} · 경과 {int(el)}초 · 요청 {st['req']} · "
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
