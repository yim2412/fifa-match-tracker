"""49단계 조각 ① 새 표본 검증 — rows1k_new.jsonl(새 구단주 약 90명)에 칩 규칙을 '고정된 대로' 적용한다.
규칙·통과 기준은 이 파일 상단 상수가 전부이고, **자료를 받기 전에 커밋한다**. 결과를 본 뒤 상수를 고치면
'결과를 본 뒤의 수정'이라 검증이 아니다 — 못 넘으면 상수가 아니라 판수·접기를 사용자와 다시 정한다.
    python verify_new.py            # 주 판정: 새 표본 분포만 (이번에 처음 본 자료)
    python verify_new.py --pooled   # 참고: 첫 표본(138명)과 합친 분포
"""
import json, math, sqlite3, statistics, sys
from datetime import datetime, timedelta
ROOT = r"C:\Users\준\Desktop\피파전적관리"; TOOLS = ROOT + r"\tools\step0_owner_dist"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import config, habits, store

# ── 고정 규칙 (2026-10-13 시작 커밋) ──────────────────────────────────────
METRICS6 = ["finish", "goals_per_on_target", "tackle_rate", "long_through_rate", "possession", "shots"]
EXCLUDED = ["on_target_rate"]                  # 41단계 판정 유지(첫 표본 신뢰도 0.36) — 참고로만 잰다
MIN_GAMES = 800                                # 이만큼 못 받은 구단주는 분포에서 뺀다
MAX_STALE_DAYS = 30                            # 마지막 경기가 수집일보다 이만큼 오래면 뺀다
POP = {1: 200, 2: 800, 3: 2000}                # 층 가중(모집단 크기)
CHIP_P = 10                                    # 칩: 백분위 ≤10 또는 ≥90
SURE_P = 25                                    # 구간 안쪽 끝이 하위 25% / 상위 25% 안 (임시값 — 여기서 검증)
SAME_DIR_P = 25                                # ⓑ 다른 창이 같은 방향이라 보는 기준: 백분위 ≤25 / ≥75
WINDOW = 1000                                  # 내 값 창(최근→과거로 1000판씩, 최대 8개)
MAX_WINDOWS = 8
# 통과 기준
A_MIN, A_TOL = 0.60, 0.15                      # ⓐ 반분 신뢰도 ≥0.6 이고 첫 표본 값 ±0.15 안
A_FIRST = {"finish": 0.75, "goals_per_on_target": 0.75, "tackle_rate": 0.85,
           "long_through_rate": 0.90, "possession": 0.88, "shots": 0.85}
B_MIN = 0.80                                   # ⓑ 칩이 뜬 (계정,지표)의 다른 창 방향 일치율
C_MAX = 0.40                                   # ⓒ 칩 발생률 0 초과 ~ 40% 이하
D_MIN_WINDOWS = 6                              # ⓓ 계정1 결정력·유효슛당 골이 하위 칩인 창 수(창 8개 중)
D_METRICS = ["finish", "goals_per_on_target"]
# ─────────────────────────────────────────────────────────────────────────


def load(path):
    out = []
    for line in open(path, encoding="utf-8"):
        o = json.loads(line)
        if "error" in o or o["n_games"] < MIN_GAMES:
            continue
        out.append(o)
    return out


def totals(rows):
    t = habits.SideTotals()
    for i, r in enumerate(rows):
        t.rows.append(habits.SideRow(str(i), r["res"], r["mine"], r["opp"]))
    return t


def dval(rows, k):
    return habits.paired_diff(totals(rows), k).diff


def corr(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / den if den else float("nan")


def wpct(vals, ws, x):
    tot = sum(ws)
    lo = sum(w for v, w in zip(vals, ws) if v < x)
    eq = sum(w for v, w in zip(vals, ws) if v == x)
    return 100 * (lo + eq / 2) / tot


def main():
    new = load(TOOLS + r"\rows1k_new.jsonl")
    old_all = load(TOOLS + r"\rows1k.jsonl")
    # 마지막 경기 기준일 = 새 표본에서 가장 늦은 마지막 경기(수집일 근처)
    ref = max(o["last"] for o in new if o.get("last"))
    cut = (datetime.fromisoformat(ref[:19]) - timedelta(days=MAX_STALE_DAYS)).isoformat()
    new = [o for o in new if o.get("last") and o["last"][:19] >= cut]
    owners = new + (old_all if "--pooled" in sys.argv else [])
    cnt = {t: sum(1 for o in owners if o["stratum"] == t) for t in POP}
    ws = [POP[o["stratum"]] / cnt[o["stratum"]] for o in owners]
    print(f"{'참고(합친 분포)' if '--pooled' in sys.argv else '주 판정(새 표본만)'} · 구단주 {len(owners)}명 · 층 {cnt} · 기준일 {ref[:10]}")
    if len(owners) < 60:
        print("[FAIL] 구단주 60명 미만 — 판정 불가"); sys.exit(1)

    # ⓐ 시간 순 반분 신뢰도 — 새 표본만(분포가 합쳐져도 신뢰도는 새 표본 자체의 것)
    print("\nⓐ 시간 순 반분 신뢰도 (스피어먼-브라운)")
    ok_a = True
    for k in METRICS6 + EXCLUDED:
        a = [dval(o["rows"][: len(o["rows"]) // 2], k) for o in new]
        b = [dval(o["rows"][len(o["rows"]) // 2:], k) for o in new]
        r = corr(a, b); sb = 2 * r / (1 + r)
        if k in A_FIRST:
            good = sb >= A_MIN and abs(sb - A_FIRST[k]) <= A_TOL
            ok_a &= good
            print(f"  {k:20s} {sb:.2f} (첫 표본 {A_FIRST[k]:.2f}) {'[OK]' if good else '[FAIL]'}")
        else:
            print(f"  {k:20s} {sb:.2f} (제외 지표 · 참고)")

    dist = {k: [dval(o["rows"], k) for o in owners] for k in METRICS6}
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    accts = sorted(((store.match_count(conn, r["ouid"], 52), r["ouid"]) for r in conn.execute("SELECT ouid FROM accounts")), reverse=True)[:4]
    chips = cells = 0
    same = pairs = 0
    d_hits = {}
    for i, (n, ou) in enumerate(accts, 1):
        det = store.load_details(conn, ou, config.DEFAULT_MATCH_TYPE)
        wins = [det[j:j + WINDOW] for j in range(0, len(det), WINDOW) if len(det[j:j + WINDOW]) >= WINDOW][:MAX_WINDOWS]
        print(f"\n=== 계정{i} 창 {len(wins)}개")
        if len(wins) < 2:
            continue
        for k in METRICS6:
            ps, ss = [], []
            for w in wins:
                dd = habits.paired_diff(habits.side_totals(w, ou), k)
                p = wpct(dist[k], ws, dd.diff)
                plo = wpct(dist[k], ws, dd.diff - 1.96 * dd.se); phi = wpct(dist[k], ws, dd.diff + 1.96 * dd.se)
                s = "·"
                if p <= CHIP_P and phi <= SURE_P: s = "↓"
                elif p >= 100 - CHIP_P and plo >= 100 - SURE_P: s = "↑"
                ps.append(p); ss.append(s)
            cells += len(ss); chips += sum(1 for s in ss if s != "·")
            for j, s in enumerate(ss):
                if s == "·": continue
                for j2, p2 in enumerate(ps):
                    if j2 == j: continue
                    pairs += 1
                    same += (p2 <= SAME_DIR_P) if s == "↓" else (p2 >= 100 - SAME_DIR_P)
            if i == 1 and k in D_METRICS:
                d_hits[k] = (ss.count("↓"), len(ss))
            print(f"  {k:20s} {''.join(ss)}  백분위 " + " ".join(f"{p:.0f}" for p in ps))

    rate = chips / cells if cells else float("nan")
    same_rate = same / pairs if pairs else float("nan")
    ok_b = pairs > 0 and same_rate >= B_MIN
    ok_c = 0 < rate <= C_MAX
    ok_d = all(h[0] >= D_MIN_WINDOWS for h in d_hits.values()) and len(d_hits) == len(D_METRICS)
    print(f"\nⓐ 신뢰도 {'[OK]' if ok_a else '[FAIL]'}")
    print(f"ⓑ 같은 방향 일치 {same}/{pairs} = {same_rate:.0%} (≥{B_MIN:.0%}) {'[OK]' if ok_b else '[FAIL]'}")
    print(f"ⓒ 칩 발생률 {chips}/{cells} = {rate:.0%} (0 초과 ~{C_MAX:.0%}) {'[OK]' if ok_c else '[FAIL]'}")
    print(f"ⓓ 계정1 하위 칩 창 수 {d_hits} (각 ≥{D_MIN_WINDOWS}) {'[OK]' if ok_d else '[FAIL]'}")
    allok = ok_a and ok_b and ok_c and ok_d
    print("\n[OK] 통과" if allok else "\n[FAIL] 통과 못 함 — 상수를 고치지 말고 사용자에게 다시 묻는다")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
