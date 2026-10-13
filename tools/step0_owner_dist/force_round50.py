"""50단계 조각 ② 끝 기준 — 개발 키로 **강제 v2 바퀴** 하나(ownerdist.run_round(force=True))를 스크래치 fifa.db 에 받는다.
앱의 진짜 fifa.db 는 열지 않는다(지금 쓰는 분포를 바꾸지 않게). rank.db 는 읽기만(rankcollect.open_rank_db_ro).

재는 것(ROADMAP 50 "조각 ②" 끝 기준 · 6회차 Y1): 바퀴 결과 · 요청 수·시간 · 층별 pos_json NULL 비율(높으면 FAIL).
--workers 로 구단주 한 명 안의 동시 상세 수를 늘린다(앱 기본 4 — 측정만 빠르게). 끊겨도 같은 --db 로 다시 돌리면 이어 받는다.
    python force_round50.py --db <스크래치>/force50/fifa.db [--workers 8] [--per-stratum 50]
"""
import argparse, json, sys, threading, time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config, ownerdist, rankcollect, store  # noqa: E402
from nexon_api import FCOnlineAPI  # noqa: E402
import collect0  # noqa: E402

PROG = Path.home() / ".claude" / "bg-progress.d" / "force50.txt"
NULL_MAX = 0.05     # pos_json NULL 이 이 비율을 넘으면 FAIL — 측정 C 의 빈 자리 경기 0.16% 에 비해 넉넉히


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--per-stratum", type=int, default=config.OWNER_DIST_PER_STRATUM)
    a = ap.parse_args()
    db = Path(a.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.resolve() == Path(config.DB_PATH).resolve():
        raise SystemExit("앱의 fifa.db 에는 돌리지 않는다")
    config.OWNER_DIST_WORKERS = a.workers
    config.OWNER_DIST_PER_STRATUM = a.per_stratum
    key = collect0.dev_key()
    total = a.per_stratum * len(config.OWNER_DIST_STRATA)
    stop = threading.Event()
    t0 = time.time()

    def progress():
        while not stop.wait(10):
            try:
                c = store.open_db(db)
                try:
                    run = store.owner_round_running(c)
                    n = len(store.owner_done_hashes(c, run["round_id"])) if run else 0
                    req = run["req_count"] if run else 0
                finally:
                    c.close()
                el = time.time() - t0
                PROG.parent.mkdir(parents=True, exist_ok=True)
                PROG.write_text(f"강제 v2 바퀴 {n}/{total}명 · 경과 {int(el // 60)}분 · 요청 {req}", encoding="utf-8")
            except Exception:  # noqa: BLE001 — 진행 줄 실패가 받기를 막지 않게
                pass

    th = threading.Thread(target=progress, daemon=True)
    th.start()
    try:
        res = ownerdist.run_round(db, None, lambda: FCOnlineAPI(key, cache_dir=None),
                                  rank_open=rankcollect.open_rank_db_ro, force=True)
    finally:
        stop.set()
        th.join()
        try:
            PROG.unlink()
        except OSError:
            pass
    el = time.time() - t0
    print(f"결과 {res.result} · 구단주 {res.owners_done}/{res.owners_total} · 이번 요청 {res.requests} · {int(el)}초"
          f" · 이유 limit={res.limit} quota={res.quota} cancel={res.cancelled} error={res.error!r}", flush=True)
    c = store.open_db(db)
    try:
        last = store.owner_round_last_end(c, ("done", "thin"))
        if res.result not in ("done", "thin") or last is None:
            print("[FAIL] 바퀴가 끝나지 않았다 — 같은 명령으로 이어 받는다")
            return 1
        rows = store.owner_dist_rows(c, last["round_id"])
        v2 = c.execute("SELECT v2, mixed FROM owner_dist_state WHERE round_id = ?", (last["round_id"],)).fetchone()
        print(f"바퀴 {last['round_id']} · v2={v2[0]} · mixed={v2[1]} · 줄 {len(rows)}")
        bad = False
        for s in range(1, len(config.OWNER_DIST_STRATA) + 1):
            rs = [r for r in rows if r["stratum"] == s]
            nul = sum(r["pos_json"] is None for r in rs)
            ns = [json.loads(r["pos_json"])["DF.def"]["n"] for r in rs if r["pos_json"]]
            ratio = nul / len(rs) if rs else 1.0
            bad |= ratio > NULL_MAX
            print(f"  층 {s}: {len(rs)}명 · pos_json NULL {nul} ({ratio:.1%}) · DF.def 판수 최소 {min(ns, default=0)}")
        when = datetime.now().isoformat(timespec="seconds")
        print(f"[{'FAIL' if bad else 'OK'}] pos_json NULL 비율 ≤ {NULL_MAX:.0%} · {when}")
        return 1 if bad else 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
