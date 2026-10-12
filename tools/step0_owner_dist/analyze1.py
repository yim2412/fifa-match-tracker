"""단계 0 — 문턱(모의실험) · 안정성 · 칩 발생률 · 정규 근사. 규칙은 ROADMAP 4c35486 그대로."""
import json, math, random, sqlite3, statistics
from datetime import datetime, timedelta
import analyze0 as A
import config, habits, store

rng = random.Random(20261012)
owners = [o for o in A.load_owners() if o["n_games"] >= 20]
D = [A.owner_diffs(o) for o in owners]
ws = A.weights(owners)
strata = [o["stratum"] for o in owners]
res = json.load(open(A.HERE / "res.json", encoding="utf-8"))
G = res["G"]
KEYS = A.KEYS
LAB = {k: habits.METRICS[k].label for k in KEYS}


def sim_rel_err(ses_by_stratum, r, reps=300, per=75):
    """참 σ²/v = r 로 고정, 층마다 per 명(설계 크기), 층 가중. σ̂ 의 상대 sd(= sd(σ̂)/σ) 를 돌려준다."""
    # 층 가중 평균 se² 를 s2 로, 거기서 σ² = r/(1-r)*s2
    s2_true = sum(A.POP[st] * statistics.mean(s * s for s in ss) for st, ss in ses_by_stratum.items()) / sum(A.POP[st] for st in ses_by_stratum)
    sig2 = r / (1 - r) * s2_true
    out = []
    for _ in range(reps):
        ds, ses, w = [], [], []
        for st, ss in ses_by_stratum.items():
            for _i in range(per):
                s = rng.choice(ss)
                ds.append(rng.gauss(0, math.sqrt(sig2)) + rng.gauss(0, s)); ses.append(s); w.append(A.POP[st] / per)
        out.append(A.stats_of(ds, ses, w)["sigma"])
    sig = math.sqrt(sig2)
    return statistics.pstdev(out) / sig, statistics.mean(out) / sig - 1


print("[⑥ min_ratio — σ̂ 상대 오차(sd÷σ) ≤ 30% 가 되는 최소 r, 설계 크기 4층×75명]")
min_ratio = {}
for k in KEYS:
    by = {}
    for o, d in zip(owners, D):
        by.setdefault(o["stratum"], []).append(max(d[k].se, 1e-9))
    found = None
    for r in [x / 100 for x in range(5, 96, 5)]:
        e, bias = sim_rel_err(by, r)
        if e <= 0.30:
            found = r; break
    min_ratio[k] = found
    print(f"  {LAB[k]:10s} min_ratio={found}")

print("\n[⑥ min_sigma = G/1.28]")
min_sigma = {k: G[k] / A.Z for k in KEYS}
print("  " + " · ".join(f"{LAB[k][:4]} {min_sigma[k]:.2f}" for k in KEYS))

# ── 판정 대상(분리비 점추정 ≥ min_ratio AND σ ≥ min_sigma) — 정규 근사 검사는 아래
print("\n[⑤ 정규 근사: 가중 경험 CDF 대 Φ((x−m)/√v) 최대 차 (%p)] — 참값이 정규일 때 같은 가중·같은 표본에서 나오는 KS 의 95% 점을 넘으면 뺀다")
print("   (고정 5%p 는 n_eff 75 에서 표본 오차만으로 약 15%p 라 통과 불가였다 — 10-12 단계 0 1차)")


def weighted_ks(xs, w, m, sd):
    ds = sorted(zip(xs, w)); tot = sum(w); acc = 0; mx = 0
    for x, wi in ds:
        acc += wi
        p = A.PHI((x - m) / sd)
        mx = max(mx, abs(acc / tot - p), abs((acc - wi) / tot - p))
    return mx


KS_REPS = 500
ks, ks95 = {}, {}
for k in KEYS:
    r = res["res"][k]; sd = math.sqrt(r["v"])
    ks[k] = 100 * weighted_ks([d[k].diff for d in D], ws, r["m"], sd)
    null = sorted(100 * weighted_ks([rng.gauss(r["m"], sd) for _ in ws], ws, r["m"], sd) for _ in range(KS_REPS))
    ks95[k] = null[int(0.95 * KS_REPS) - 1]
print("  " + " · ".join(f"{LAB[k][:4]} {ks[k]:.1f}(95%점 {ks95[k]:.1f})" for k in KEYS))

target = {}
for k in KEYS:
    r = res["res"][k]
    ok_ratio = min_ratio[k] is not None and r["ratio"] >= min_ratio[k]
    ok_sig = r["sigma"] >= min_sigma[k]
    ok_norm = ks[k] <= ks95[k]
    target[k] = ok_ratio and ok_sig and ok_norm
    print(f"  {LAB[k]:10s} 분리비 {r['ratio']:.2f}≥{min_ratio[k]} {'O' if ok_ratio else 'X'} · σ {r['sigma']:.2f}≥{min_sigma[k]:.2f} {'O' if ok_sig else 'X'} · 정규 {ks[k]:.1f} {'O' if ok_norm else 'X'}  → {'판정 대상' if target[k] else '41단계 유지'}")
print("  판정 대상:", [LAB[k] for k in KEYS if target[k]])
CHIP = ("finish", "on_target_rate", "tackle_rate")
AX = ("possession", "shots", "long_through_rate", "tackles")
print(f"  칩 지표 셋 중 {sum(target[k] for k in CHIP)}/3 · A1 축 넷 중 {sum(target[k] for k in AX)}/4 (과반 = 각각 2 이상 / 3 이상)")

# ── 안정성: 층 균형 반쪽 20회, 같은 계정 백분위(축소 전) 차이 중앙값 ≤ 8%p
conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
accts = sorted(((store.match_count(conn, r["ouid"], 52), r["ouid"]) for r in conn.execute("SELECT ouid FROM accounts")), reverse=True)[:4]
mine = {i: A.mine_diffs(conn, ou) for i, (n, ou) in enumerate(accts, 1)}
by_st = {t: [i for i, s in enumerate(strata) if s == t] for t in A.POP}
print("\n[② 안정성: 반쪽 두 개로 나눈 20회, 같은 계정·지표 백분위 차이 (%p) 중앙값]")
stab = {}
for k in KEYS:
    diffs = []
    for _ in range(20):
        halves = ([], [])
        for t, ii in by_st.items():
            ii = ii[:]; rng.shuffle(ii); h = len(ii) // 2
            halves[0].extend(ii[:h]); halves[1].extend(ii[h:2 * h])
        ps = []
        for H in halves:
            s = A.stats_of([D[i][k].diff for i in H], [D[i][k].se for i in H], [ws[i] for i in H])
            ps.append([100 * A.PHI((mine[a][1][k].diff - s["m"]) / s["sigma"]) if s["sigma"] > 0 else float("nan") for a in mine])
        for a in range(4):
            if not (math.isnan(ps[0][a]) or math.isnan(ps[1][a])):
                diffs.append(abs(ps[0][a] - ps[1][a]))
    stab[k] = statistics.median(diffs) if diffs else float("nan")
print("  " + " · ".join(f"{LAB[k][:4]} {stab[k]:.1f}" for k in KEYS))
final = {k: target[k] and stab[k] <= 8.0 for k in KEYS}
print("  안정성 ≤8 && 위 대상 →", [LAB[k] for k in KEYS if final[k]])

# ── 칩 발생률: 판정 대상 지표 × 4계정 (전체 범위 / 최근 14일 / 최근 3일)
today = datetime(2026, 10, 12, 23, 59)
print("\n[③ 칩 발생률 — 축소 백분위 ≤10 또는 ≥90 AND |d−m| ≥ 2√(se_me²+se_m²)]")
for label, days in (("전체 범위", None), ("최근 14일", 14), ("최근 3일", 3)):
    tot = chips = 0; per = {}
    for i, (n, ou) in enumerate(accts, 1):
        g, md = A.mine_diffs(conn, ou, days=days, today=today) if days else (mine[i][0], mine[i][1])
        for k in KEYS:
            if not final[k] or g < 20:
                continue
            r = res["res"][k]; d = md[k].diff; se = md[k].se
            if not math.isfinite(se):
                continue
            sg = r["sigma"]; dstar = r["m"] + (d - r["m"]) * sg * sg / (sg * sg + se * se)
            p = 100 * A.PHI((dstar - r["m"]) / sg)
            se_m = 1.253 * math.sqrt(r["v"]) / math.sqrt(r["neff"])
            tot += 1
            if (p <= 10 or p >= 90) and abs(d - r["m"]) >= 2 * math.sqrt(se * se + se_m * se_m):
                chips += 1; per.setdefault(i, []).append(LAB[k])
    print(f"  {label}: 칩 {chips}/{tot} = {100*chips/tot if tot else float('nan'):.0f}%  계정별 {per}")
json.dump({"min_ratio": min_ratio, "min_sigma": min_sigma, "ks": ks, "ks95": ks95, "target": target, "stab": stab, "final": final}, open(A.HERE / "res1.json", "w"), ensure_ascii=False, indent=1)
