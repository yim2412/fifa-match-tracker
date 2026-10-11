"""42단계 단계 0 분석 — rows.jsonl(구단주 160명) + fifa.db(내 계정 4개, 읽기 전용). 판정 규칙은 ROADMAP 단계 0 시작 커밋(4c35486) 그대로.
    python analyze0.py [--boot 1000]
"""
import argparse, json, math, random, sqlite3, statistics, sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config, habits, store  # noqa: E402

HERE = Path(__file__).parent
POP = {1: 200, 2: 800, 3: 2000, 4: 7000}
KEYS = list(habits.METRICS)           # 지표 9개
G = {"finish": 2.5, "on_target_rate": 2.5, "goals_per_on_target": 2.5, "tackle_rate": 2.5,
     "long_through_rate": 2.8, "possession": 5.0, "shots": 0.4, "tackles": 0.9}   # on_target 은 실측에서 계산
Z = 1.281551565545
PHI = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def wmedian(xs, ws):
    z = sorted(zip(xs, ws)); tot = sum(ws); acc = 0.0
    for x, w in z:
        acc += w
        if acc >= tot / 2:
            return x
    return z[-1][0]


def stats_of(ds, ses, ws):
    sw = sum(ws)
    mean = sum(w * d for d, w in zip(ds, ws)) / sw
    v = sum(w * (d - mean) ** 2 for d, w in zip(ds, ws)) / sw
    s2 = sum(w * s * s for s, w in zip(ses, ws)) / sw
    sig2 = max(v - s2, 0.0)
    neff = sw * sw / sum(w * w for w in ws)
    return {"m": wmedian(ds, ws), "mean": mean, "v": v, "s2": s2, "sigma": math.sqrt(sig2),
            "ratio": (sig2 / v) if v > 0 else 0.0, "neff": neff}


def load_owners():
    out = []
    for line in (HERE / "rows.jsonl").read_text(encoding="utf-8").splitlines():
        j = json.loads(line)
        if "error" in j:
            continue
        out.append(j)
    seen = {}
    for j in out:                       # 첫 수집이 안 죽고 두 프로세스가 같은 구단주를 두 번 적었다 — 먼저 적힌 것만
        seen.setdefault((j["stratum"], j["rank"]), j)
    return list(seen.values())


def totals_of(rows):
    t = habits.SideTotals()
    for i, r in enumerate(rows):
        t.rows.append(habits.SideRow(str(i), r["res"], r["mine"], r["opp"]))
    return t


def owner_diffs(o):
    t = totals_of(o["rows"])
    return {k: habits.paired_diff(t, k) for k in KEYS}


def weights(sample):
    cnt = {k: sum(1 for o in sample if o["stratum"] == k) for k in POP}
    return [POP[o["stratum"]] / cnt[o["stratum"]] for o in sample]


def mine_diffs(conn, ouid, days=None, today=None):
    det = store.load_details(conn, ouid, config.DEFAULT_MATCH_TYPE)
    if days is not None:
        since = (today - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        det = [d for d in det if (d.get("matchDate") or "") >= since]
    t = habits.side_totals(det, ouid)
    return t.games, {k: habits.paired_diff(t, k) for k in KEYS}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--boot", type=int, default=1000); a = ap.parse_args()
    rng = random.Random(20261012)
    owners = load_owners()
    print(f"받은 구단주 {len(owners)}명 · 층별 { {k: sum(1 for o in owners if o['stratum']==k) for k in POP} }")
    # ── 제외(설계: 판수 20 미만 · 마지막 경기 30일 초과)
    snap_day = datetime(2026, 10, 12)
    keep = []
    for o in owners:
        last = o.get("last")
        old = last is None or (snap_day - datetime.fromisoformat(last)).days > 30
        if o["n_games"] < 20 or old:
            continue
        keep.append(o)
    print(f"제외 뒤 {len(keep)}명 (제외 {len(owners)-len(keep)}명 = {100*(len(owners)-len(keep))/len(owners):.1f}%)")
    D = [owner_diffs(o) for o in keep]
    ws = weights(keep)
    for k in KEYS:                 # on_target 의 G 는 층 가중 상대 쪽 평균의 10%
        if k not in G:
            G[k] = 0.1 * sum(w * d[k].opp for d, w in zip(D, ws)) / sum(ws)
    # ── ③ 효율 숫자
    reqs = [o["reqs"] for o in owners if "reqs" in o]
    secs = [o["secs"] for o in owners if "secs" in o]
    print(f"\n[③] 구단주당 요청 평균 {statistics.mean(reqs):.1f} · 요청당 응답 {sum(secs)/sum(reqs):.3f}초 · "
          f"ouid 적중 {100*sum(1 for o in owners if o.get('ouid_hit'))/len(owners):.0f}% · "
          f"상세 적중 {100*sum(o['detail_hits'] for o in owners)/max(1,sum(o['n_ids'] for o in owners)):.1f}%")
    # ── ④ 30판이 덮는 기간
    spans = []
    for o in keep:
        if o["first"] and o["last"]:
            spans.append((datetime.fromisoformat(o["last"]) - datetime.fromisoformat(o["first"])).total_seconds() / 86400)
    print(f"[④] 30판 덮는 기간 중앙값 {statistics.median(spans):.1f}일 (층별 중앙값 " +
          ", ".join(f"{k}:{statistics.median([s for s,o in zip(spans,keep) if o['stratum']==k]):.1f}" for k in POP) + ")")
    # ── ① 지표별 분리비
    res = {}
    print("\n[①] 지표별 (층 가중)  m · σ · σ²/v(분리비) · 부트스트랩 90% 구간 · 층 안 합동 분리비(참고) · n_eff")
    for k in KEYS:
        ds = [d[k].diff for d in D]; ses = [d[k].se for d in D]
        s = stats_of(ds, ses, ws); res[k] = s
        bs = []
        by = {t: [i for i, o in enumerate(keep) if o["stratum"] == t] for t in POP}
        for _ in range(a.boot):
            idx = [i for t in POP for i in rng.choices(by[t], k=len(by[t]))]
            bs.append(stats_of([ds[i] for i in idx], [ses[i] for i in idx], [ws[i] for i in idx])["ratio"])
        bs.sort(); lo, hi = bs[int(.05 * len(bs))], bs[int(.95 * len(bs)) - 1]
        # 층 안 합동: 층 평균 빼고 모은 분산
        num = den = 0.0; s2n = 0.0
        for t in POP:
            ii = by[t]; mu = sum(ds[i] for i in ii) / len(ii)
            num += sum((ds[i] - mu) ** 2 for i in ii); den += len(ii) - 1; s2n += sum(ses[i] ** 2 for i in ii)
        vin = num / den; s2in = s2n / len(keep)
        pooled = max(vin - s2in, 0) / vin if vin > 0 else 0
        s.update(lo=lo, hi=hi, pooled=pooled)
        print(f"  {habits.METRICS[k].label:10s} m={s['m']:+7.2f} σ={s['sigma']:5.2f} 분리비={s['ratio']:.2f} [{lo:.2f},{hi:.2f}] 합동={pooled:.2f} n_eff={s['neff']:.0f}")
    # ── 층별 중앙값(P4)
    print("\n[층별 결정력 diff 중앙값]", {t: round(statistics.median([d["finish"].diff for d, o in zip(D, keep) if o["stratum"] == t]), 2) for t in POP})
    # ── 내 계정
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    accts = sorted(((store.match_count(conn, r["ouid"], 52), r["ouid"]) for r in conn.execute("SELECT ouid FROM accounts")), reverse=True)[:4]
    my_days = statistics.median(spans)
    today = datetime(2026, 10, 12, 23, 59)
    mine_all = {}; mine_win = {}
    for i, (n, ou) in enumerate(accts, 1):
        mine_all[i] = mine_diffs(conn, ou)
        mine_win[i] = mine_diffs(conn, ou, days=my_days, today=today)
    print(f"\n[계정] 4개 판수 {[n for n,_ in accts]} · 최근 {my_days:.1f}일 창 판수 { {i: mine_win[i][0] for i in mine_win} }")
    print("계정별 전체 범위 d (축소 전 백분위 Φ((d−m)/σ))")
    for i in mine_all:
        line = []
        for k in KEYS:
            s = res[k]; d = mine_all[i][1][k].diff
            p = 100 * PHI((d - s["m"]) / s["sigma"]) if s["sigma"] > 0 else float("nan")
            line.append(f"{habits.METRICS[k].label[:4]} {d:+.1f}→{p:.0f}")
        print(f"  계정{i}: " + " · ".join(line))
    # ── 예측 판정
    print("\n[예측]")
    P = []
    P.append(("P1 계정1 결정력 d<m", mine_all[1][1]["finish"].diff < res["finish"]["m"]))
    P.append(("P2 계정1 유효슛당 골 d<m", mine_all[1][1]["goals_per_on_target"].diff < res["goals_per_on_target"]["m"]))
    P.append(("P3 m(결정력)>0", res["finish"]["m"] > 0))
    med = {t: statistics.median([d["finish"].diff for d, o in zip(D, keep) if o["stratum"] == t]) for t in POP}
    P.append(("P4 층1 결정력 중앙값 > 층4", med[1] > med[4]))
    P.append(("P5 분리비 점유율>결정력", res["possession"]["ratio"] > res["finish"]["ratio"]))
    P.append(("P6 분리비 경기당슛>유효슛당골", res["shots"]["ratio"] > res["goals_per_on_target"]["ratio"]))
    P.append(("P7 분리비 롱·스루>결정력", res["long_through_rate"]["ratio"] > res["finish"]["ratio"]))
    for n, ok in P:
        print(f"  {'맞음' if ok else '틀림'}  {n}")
    print(f"  → {sum(ok for _, ok in P)}/7 (통과 ≥6)")
    json.dump({"res": res, "G": G, "my_days": my_days}, open(HERE / "res.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
