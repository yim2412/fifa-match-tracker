"""칩 판정(상·하위 10%) — rows1k 138명.
③ 시간 순 반분 재현성: 최근 500판 vs 그 앞 500판 (같은 날 효과 제거 — 홀짝보다 엄격)
④ 칩(백분위 ≤10 또는 ≥90)이 내 1000판 창마다 어떻게 뜨나: 뜨는 창 비율 · 방향이 일관된 비율"""
import os, sys, json, math, sqlite3, statistics
ROOT = r"C:\Users\준\Desktop\피파전적관리"; TOOLS = ROOT + r"\tools\step0_owner_dist"
sys.path.insert(0, ROOT); sys.path.insert(0, TOOLS)
sys.stdout.reconfigure(encoding="utf-8")
import config, habits, store

SHOW = ["finish", "goals_per_on_target", "on_target_rate", "tackle_rate", "long_through_rate", "possession", "shots"]
owners = [json.loads(l) for l in open(TOOLS + r"\rows1k.jsonl", encoding="utf-8")]
owners = [o for o in owners if "error" not in o and o["n_games"] >= 800]
POP = {1: 200, 2: 800, 3: 2000}
cnt = {t: sum(1 for o in owners if o["stratum"] == t) for t in POP}
ws = [POP[o["stratum"]] / cnt[o["stratum"]] for o in owners]


def totals(rows):
    t = habits.SideTotals()
    for i, r in enumerate(rows):
        t.rows.append(habits.SideRow(str(i), r["res"], r["mine"], r["opp"]))
    return t


def dval(rows, k):
    return habits.paired_diff(totals(rows), k).diff


def corr(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den


def wpct(vals, ws, x):
    tot = sum(ws)
    lo = sum(w for v, w in zip(vals, ws) if v < x)
    eq = sum(w for v, w in zip(vals, ws) if v == x)
    return 100 * (lo + eq / 2) / tot


print(f"구단주 {len(owners)}명")
print("\n③ 시간 순 반분 — 최근 절반 vs 이전 절반 d 의 상관 (r) · 스피어먼-브라운 · 상위10% 그룹이 다음 절반에서도 상위 절반 이상인 비율")
for k in SHOW:
    a = [dval(o["rows"][: len(o["rows"]) // 2], k) for o in owners]
    b = [dval(o["rows"][len(o["rows"]) // 2:], k) for o in owners]
    r = corr(a, b); sb = 2 * r / (1 + r)
    # 앞 절반 상위 10% / 하위 10% 사람이 뒷 절반 분포에서 어디에 있나
    order = sorted(range(len(a)), key=lambda i: a[i])
    n10 = max(1, len(a) // 10)
    low, high = order[:n10], order[-n10:]
    med_b = statistics.median(b)
    hold_high = sum(1 for i in high if b[i] > med_b) / n10
    hold_low = sum(1 for i in low if b[i] < med_b) / n10
    print(f"  {habits.METRICS[k].label[:8]:8s} r={r:+.2f} → {sb:.2f} · 상위10% 가 다음 절반에서 중앙값 위 {hold_high:.0%} · 하위10% 가 중앙값 아래 {hold_low:.0%}")

dist = {k: [dval(o["rows"], k) for o in owners] for k in SHOW}
conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
accts = sorted(((store.match_count(conn, r["ouid"], 52), r["ouid"]) for r in conn.execute("SELECT ouid FROM accounts")), reverse=True)[:4]
print("\n④ 내 1000판 창: 칩(백분위 ≤10 또는 ≥90)이 뜬 창 수 / 전체 창 · 같은 방향이 이어진 창 비율 · 구간(내 값±1.96se)도 10/90 밖이면 '확실' 칩")
tot_windows = tot_chip = tot_sure = 0
for i, (n, ou) in enumerate(accts, 1):
    det = store.load_details(conn, ou, config.DEFAULT_MATCH_TYPE)
    wins = [det[j:j + 1000] for j in range(0, len(det), 1000) if len(det[j:j + 1000]) >= 1000][:8]
    if len(wins) < 2:
        print(f"=== 계정{i}: 창 {len(wins)}개(2 미만)"); continue
    print(f"=== 계정{i} 창 {len(wins)}개")
    for k in SHOW:
        sides = []; sure = 0
        for w in wins:
            dd = habits.paired_diff(habits.side_totals(w, ou), k)
            p = wpct(dist[k], ws, dd.diff)
            plo = wpct(dist[k], ws, dd.diff - 1.96 * dd.se); phi = wpct(dist[k], ws, dd.diff + 1.96 * dd.se)
            s = "↓" if p <= 10 else ("↑" if p >= 90 else "·")
            sides.append(s)
            if (s == "↓" and phi <= 50) or (s == "↑" and plo >= 50):
                sure += 1
        chip = sum(1 for s in sides if s != "·")
        main = max(("↓", "↑"), key=sides.count)
        consistent = sides.count(main) / len(sides)
        tot_windows += len(sides); tot_chip += chip; tot_sure += sure
        print(f"  {habits.METRICS[k].label[:6]:6s} {''.join(sides)}  칩 {chip}/{len(sides)} · 최다 방향 {main} {consistent:.0%} · 확실 {sure}")
print(f"\n전체: 창×지표 {tot_windows}개 중 칩 {tot_chip} ({tot_chip/tot_windows:.0%}) · 구간까지 확실 {tot_sure} ({tot_sure/tot_windows:.0%})")
