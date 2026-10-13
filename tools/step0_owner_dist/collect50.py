"""50단계 조각 ② 측정 C·D(ROADMAP "50단계 › 측정으로 먼저 정할 것") — 구단주 최근 1000판의 **자리 줄별 선수 값**과
**카드별 평점**을 받는다. 49 단계 0 의 collect1k.py 를 넓힌 사본이다(그쪽 rows1k*.jsonl 엔 팀 단위 요약뿐이라 쓸 수 없다).

표본: rank.db 최신 스냅숏에서 config.OWNER_DIST_STRATA 층(1~200 · 201~1000 · 1001~3000)마다 --per-stratum 명(기본 40 → 120명).
저장: --out(이 PC 스크래치 — 판정 뒤 지운다). 닉네임·ouid·경기 id 는 안 남긴다. 카드 번호(spid)는 측정 D 에 필요해 남긴다.
재개: --out 에 있는 (stratum, rank) 는 건너뛴다.
    python collect50.py --out <스크래치>/rows50.jsonl [--limit 2] [--per-stratum 40] [--workers 8]
"""
import argparse, json, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config, nexon_api, stats  # noqa: E402
from stats import _me_opp, _num, _played, _result_of, _wdl_of  # noqa: E402
import collect0  # noqa: E402  (dev_key · MATCHTYPE 재사용)

PROG = Path.home() / ".claude" / "bg-progress.d" / "owner50.txt"
SEED = 20261017
PAGE = 100
PAUSE_429 = 60
LINES = ("FW", "MF", "DF")            # GK 는 뺀다(계획 C4 ⓒ)
ATK = ("goal", "effectiveShoot", "assist", "dribbleSuccess")
DEF = ("tackle", "block", "intercept")
KEYS = ATK + DEF


class Stop(Exception):
    pass


def _line_means(side: dict):
    """한 팀의 줄별 선수 한 명 평균(KEYS 순서) · 줄별 선수 수 · 카드 목록 [spid, 줄, 강화, 평점]."""
    acc = {L: [] for L in LINES}
    cards = []
    for p in side.get("player") or []:
        if not isinstance(p.get("spId"), int) or not _played(p):
            continue
        L = stats.position_line(p.get("spPosition"))
        if L not in acc:
            continue
        st = p.get("status") or {}
        acc[L].append([_num(st, k) for k in KEYS])
        cards.append([p["spId"], L, int(_num(p, "spGrade")), _num(st, "spRating")])
    means = {L: [sum(c) / len(v) for c in zip(*v)] for L, v in acc.items() if v}
    return means, cards


def game_record(d: dict, ouid: str):
    """경기 하나 → {"res", "date", "m": {줄: 평균}, "o": {줄: 평균}, "cards": [...]} · 승무패가 아니거나 상대가 없으면 None.
    자리 데이터가 빈 경기(선수 목록 없음)는 m·o 가 빈 dict 로 남는다 — 실패가 아니다(6회차 Y1)."""
    me, opp = _me_opp(d, ouid)
    if me is None or not opp:
        return None
    res = _wdl_of(_result_of(me))
    if res is None:
        return None
    m, cards = _line_means(me)
    o, _ = _line_means(opp)
    return {"res": res, "date": d.get("matchDate") or "", "m": m, "o": o, "cards": cards}


def pick(per_stratum):
    rc = sqlite3.connect(f"file:{config.DATA_DIR / 'rank.db'}?mode=ro", uri=True)
    snap = rc.execute("SELECT max(id) FROM snapshots").fetchone()[0]
    out, lo = [], 1
    for i, hi in enumerate(config.OWNER_DIST_STRATA, 1):
        rows = rc.execute("SELECT rank, nickname FROM snapshot_rows WHERE snapshot_id=? AND rank BETWEEN ? AND ? ORDER BY rank",
                          (snap, lo, hi)).fetchall()
        out += [(i, r, n) for r, n in random.Random(SEED + i).sample(rows, min(per_stratum, len(rows)))]
        lo = hi + 1
    return snap, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--n", type=int, default=config.OWNER_DIST_MATCHES)
    ap.add_argument("--per-stratum", type=int, default=40)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    out_path = Path(a.out)
    key = collect0.dev_key()
    done = ({(j["stratum"], j["rank"]) for j in map(json.loads, out_path.read_text(encoding="utf-8").splitlines())}
            if out_path.exists() else set())
    snap, sample = pick(a.per_stratum)
    todo = [s for s in sample if (s[0], s[1]) not in done]
    by = {k: [s for s in todo if s[0] == k] for k in (1, 2, 3)}
    order = [by[k][i] for i in range(max(map(len, by.values()), default=0)) for k in (1, 2, 3) if i < len(by[k])]
    if a.limit:
        order = order[: a.limit]
    total = len(order)
    print(f"50 · 스냅숏 {snap} · 최근 {a.n}판 · 받은 것 {len(done)} · 이번에 {total}명 · 동시 {a.workers}", flush=True)

    local = threading.local()
    lock = threading.Lock()
    st = {"req": 0, "sec": 0.0, "done": 0, "pause_until": 0.0, "stop": None, "summ": 0.0}
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
        stratum, rank, nick = item
        if st["stop"]:
            return
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
                games, empty = [], 0
                t_sum = 0.0
                for mid in ids:
                    try:
                        d = call(a_.get_match_detail_nocache, mid)
                    except nexon_api.NexonAPIError:
                        continue
                    t0 = time.perf_counter()
                    g = game_record(d, ouid)
                    t_sum += time.perf_counter() - t0
                    if g is None:
                        continue
                    if not g["m"]:
                        empty += 1
                    games.append(g)
                games.sort(key=lambda g: g["date"])
                rec = {"stratum": stratum, "rank": rank, "n_ids": len(ids), "n_games": len(games),
                       "empty_pos": empty, "extract_s": round(t_sum, 3), "games": games}
                with lock:
                    st["summ"] += t_sum
        except Stop:
            return
        except Exception as e:
            print(f"건너뜀 층{stratum} {rank}위: {type(e).__name__} {str(e)[:80]}", flush=True)
            return
        with lock:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            st["done"] += 1
            el = time.time() - t_start
            msg = (f"owner50 {st['done']}/{total} · 경과 {int(el)}초 · 요청 {st['req']} · "
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
    print(f"[OK] 끝 · {st['done']}/{total} · 요청 {st['req']} · {int(time.time()-t_start)}초 · 추출 합계 {st['summ']:.1f}초", flush=True)


if __name__ == "__main__":
    main()
