"""2번 단계 0 분석 — rows1k.jsonl (구단주 최근 ~1000판).
① 재현성: 같은 구단주의 앞 절반(최근) vs 뒤 절반 d 의 상관 — 백분위가 '사람의 성향'인가 잡음인가
② 내 계정을 1000판 창으로 나눠 백분위가 창마다 얼마나 흔들리나 (100판 때 sd 15~30점이었다)
읽기 전용."""
import os, sys, json, math, sqlite3, statistics
ROOT = r"C:\Users\준\Desktop\피파전적관리"; TOOLS = ROOT + r"\tools\step0_owner_dist"
sys.path.insert(0, ROOT); sys.path.insert(0, TOOLS)
sys.stdout.reconfigure(encoding="utf-8")
import config, habits, store

MINGAMES = int(os.environ.get("MINGAMES", "800"))
WIN = int(os.environ.get("WIN", "1000"))
SHOW = ["finish", "goals_per_on_target", "on_target_rate", "tackle_rate", "long_through_rate", "possession", "shots"]

owners = []
for line in open(TOOLS + r"\rows1k.jsonl", encoding="utf-8"):
    j = json.loads(line)
    if "error" in j or j["n_games"] < MINGAMES:
        continue
    owners.append(j)
print(f"구단주 {len(owners)}명 (판수 {MINGAMES}+) · 층별 { {t: sum(1 for o in owners if o['stratum']==t) for t in (1,2,3)} } · 판수 중앙값 {statistics.median(o['n_games'] for o in owners):.0f}")
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
    return num / den if den else float("nan")


def wpct(vals, ws, x):
    tot = sum(ws)
    lo = sum(w for v, w in zip(vals, ws) if v < x)
    eq = sum(w for v, w in zip(vals, ws) if v == x)
    return 100 * (lo + eq / 2) / tot


# ① 재현성: 홀수/짝수 판으로 가른다 (기간 효과 제거 — 앞/뒤로 가르면 시간 변화가 섞인다)
print("\n① 재현성 — 홀수번째 판 vs 짝수번째 판 d 의 상관 (1에 가까울수록 '사람의 성향', 0이면 잡음) · 스피어먼-브라운 보정 포함")
for k in SHOW:
    a = [dval(o["rows"][0::2], k) for o in owners]; b = [dval(o["rows"][1::2], k) for o in owners]
    r = corr(a, b); sb = 2 * r / (1 + r) if r == r else float("nan")
    print(f"  {habits.METRICS[k].label[:8]:8s} r={r:+.2f} → 전체 판수 신뢰도 {sb:.2f}")

# ② 내 창별 백분위
dist = {k: [dval(o["rows"], k) for o in owners] for k in SHOW}
conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
accts = sorted(((store.match_count(conn, r["ouid"], 52), r["ouid"]) for r in conn.execute("SELECT ouid FROM accounts")), reverse=True)[:4]
print(f"\n② 내 계정 {WIN}판 창별 백분위(최근→과거) · 구간 = 내 값±1.96se 를 분포에 놓은 백분위")
for i, (n, ou) in enumerate(accts, 1):
    det = store.load_details(conn, ou, config.DEFAULT_MATCH_TYPE)
    wins = [det[j:j + WIN] for j in range(0, len(det), WIN) if len(det[j:j + WIN]) >= WIN][:8]
    if not wins:
        print(f"=== 계정{i}: {WIN}판 창 없음 (상세 {len(det)}판)"); continue
    print(f"=== 계정{i} (상세 {len(det)}판 · 창 {len(wins)}개)")
    for k in SHOW:
        cells = []; pcts = []
        for w in wins:
            dd = habits.paired_diff(habits.side_totals(w, ou), k)
            p = wpct(dist[k], ws, dd.diff)
            lo = wpct(dist[k], ws, dd.diff - 1.96 * dd.se); hi = wpct(dist[k], ws, dd.diff + 1.96 * dd.se)
            pcts.append(p); cells.append(f"{p:.0f}[{lo:.0f}-{hi:.0f}]")
        sd = statistics.pstdev(pcts) if len(pcts) > 1 else float("nan")
        print(f"  {habits.METRICS[k].label[:6]:6s} " + " ".join(cells[:6]) + f"  | 창 {len(wins)}개 sd {sd:.1f}")
