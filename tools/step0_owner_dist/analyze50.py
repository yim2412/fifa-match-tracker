"""50단계 조각 ② 측정 C·D 판정 — collect50.py 결과(--rows) + 개발 PC fifa.db 계정(읽기 전용).
**판정 규칙은 ROADMAP "50단계 › 측정으로 먼저 정할 것" C·D 그대로이고, 문장이 비워 둔 세부는 아래에 적어 이 파일을 데이터를 보기 전에 커밋한다.**

측정 C (C4 관문 · 칸 = 줄 FW·MF·DF × 공격·수비 = 6칸)
- 경기 값: 그 줄 내 선수 한 명 평균 − 같은 경기 상대 같은 줄 선수 한 명 평균(지표마다). 양쪽에 그 줄 선수가 있는 경기만.
- 구단주 값 v[지표] = 경기 값 평균. 분포 = 표본 구단주(받은 판 ≥ OWNER_DIST_MIN_GAMES)의 층 가중 평균 μ·층 가중 SD σ.
- 지수 = 해당 지표 (v−μ)/σ 의 평균(공격: ATK · 수비: DEF). 경기 단위 지수 = 같은 식을 경기 값에 — 평균이 곧 지수라 se = SD/√n.
- 5단계: 표본 구단주 지수의 층 가중 백분위(경험 CDF) 20·40·60·80 경계.
- ⓐ 시간 순 반분: 구단주마다 오래된 절반 ↔ 최근 절반으로 지수를 따로(μ·σ 도 그 절반 값들로) → 스피어먼 r → 2r/(1+r) ≥ 0.6.
- 개발 PC 계정(감독모드 500판 이상)의 최근부터 겹치지 않는 500판 창: 줄 출전 300판 미만이면 보류 · 지수 ± 1.645·se 의
  양 끝 백분위가 서로 다른 단계면 보류(경계 하나라도 걸치면 — 엄격한 쪽으로 읽는다).
- ⓑ 같은 계정 두 창이 모두 단계가 나온 쌍 중 단계 차 ≤ 1 인 비율(전체 쌍) ≥ 80% — 쌍이 0 이면 미달. 계정별 값은 참고로 찍는다.
- ⓒ 보류 아닌 (계정, 창) 비율 > 0.
- 칸 통과 = ⓐ·ⓑ·ⓒ 모두. 통과 칸이 2개 미만이면 C4 를 뺀다.

측정 D (N27 관문)
- 칸 = (카드 spid, 줄, 강화대 1~4·5~7·8+). 구단주가 그 칸에서 30판 넘게(>30) 쓴 경우만 그 구단주의 평균 평점이 칸에 들어간다.
  강화를 섞어 쓴 카드는 경기마다 그 경기 강화대로 나눈다. 칸 구단주 수는 **층을 합쳐** 센다(비교 기준이 "상위 3,000위").
- ① 개발 PC 계정 중 감독모드 800~1,500판 계정마다 최근 1000판에서 30판 넘게 쓴 내 (카드, 줄, 강화대) 중
  구단주 수 ≥ T 인 칸이 5장 이상. T 는 5부터 올려 모든 그런 계정에서 만족하는 가장 큰 값(T=5 에서 미달이면 미달).
- ② 구단주를 무작위 반으로 나눠 두 무리의 같은 칸 평균(구단주 평균의 평균) 피어슨 상관 — 칸은 양쪽 모두 1명 이상이고 합 ≥ T.
  나누기 20회 중앙값. 부트스트랩(구단주 다시 뽑기 200회)으로 그 중앙값의 5% 분위 > 0.2.
- 통과 = ①·②.
    python analyze50.py --rows <스크래치>/rows50.jsonl
"""
import argparse, json, math, random, sqlite3, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\준\Desktop\피파전적관리")
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from collect50 import ATK, DEF, KEYS, LINES, game_record  # noqa: E402

POP = dict(zip((1, 2, 3), config.OWNER_DIST_STRATA_POP))
CUTS = (20, 40, 60, 80)
SIDES = {"atk": [KEYS.index(k) for k in ATK], "def": [KEYS.index(k) for k in DEF]}
CELLS = [(L, s) for L in LINES for s in SIDES]
WIN = 500
MIN_LINE = 300
Z90 = 1.645
SB_MIN, STABLE_MIN = 0.6, 0.8
CARD_MIN_GAMES = 30
CARD_MIN_CARDS = 5
SPLITS, BOOT = 20, 200
CORR_MIN = 0.2
SEED = 20261017


def band(g):
    return 0 if g <= 4 else 1 if g <= 7 else 2


def diffs(games, L):
    """그 줄의 경기 값 목록(지표 7개씩)."""
    return [[a - b for a, b in zip(g["m"][L], g["o"][L])] for g in games if L in g["m"] and L in g["o"]]


def mean_vec(rows):
    return [sum(c) / len(rows) for c in zip(*rows)] if rows else None


def wstats(vals, ws):
    sw = sum(ws)
    mu = sum(v * w for v, w in zip(vals, ws)) / sw
    var = sum(w * (v - mu) ** 2 for v, w in zip(vals, ws)) / sw
    return mu, math.sqrt(var)


class Dist:
    """한 칸의 기준 — 지표별 층 가중 μ·σ 와 표본 구단주 지수의 층 가중 경험 CDF."""

    def __init__(self, owners, L, side, games_of=lambda o: o["games"]):
        idx = SIDES[side]
        vs, ws = [], []
        for o in owners:
            v = mean_vec(diffs(games_of(o), L))
            if v is None:
                continue
            vs.append(v)
            ws.append(o["w"])
        # σ = 0 인 지표(그 줄에서 늘 같은 값 — 정보가 없다)는 지수에서 뺀다. 빠진 지표는 dropped 로 찍는다(fb796f1 뒤 버그 수정)
        params = [(i, wstats([v[i] for v in vs], ws)) for i in idx]
        self.dropped = [KEYS[i] for i, (_, sd) in params if sd <= 0]
        self.idx = [i for i, (_, sd) in params if sd > 0]
        self.params = [p for _, p in params if p[1] > 0]
        self.ok = bool(self.idx)
        self.scores = sorted(zip((self.index(v) for v in vs), ws)) if self.ok else []
        self.ws = sum(ws)

    def index(self, v):
        return sum((v[i] - mu) / sd for i, (mu, sd) in zip(self.idx, self.params)) / len(self.idx)

    def pct(self, x):
        return 100 * sum(w for s, w in self.scores if s <= x) / self.ws


def stage(p):
    return sum(p > c for c in CUTS)


def spearman(a, b):
    def ranks(x):
        o = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for k, i in enumerate(o):
            r[i] = k
        return r
    return statistics.correlation(ranks(a), ranks(b))


def load_owners(path):
    owners = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        j = json.loads(line)
        if j.get("error") or j["n_games"] < config.OWNER_DIST_MIN_GAMES:
            continue
        owners.append(j)
    n = defaultdict(int)
    for o in owners:
        n[o["stratum"]] += 1
    for o in owners:
        o["w"] = POP[o["stratum"]] / n[o["stratum"]]
    return owners, dict(n)


def dev_accounts():
    c = sqlite3.connect(f"file:{config.DATA_DIR / 'fifa.db'}?mode=ro", uri=True)
    ids = [r[0] for r in c.execute(
        "SELECT p.ouid FROM match_players p JOIN matches m ON m.match_id=p.match_id WHERE m.match_type=? "
        "GROUP BY p.ouid HAVING count(*) >= ?", (52, WIN))]
    out = []
    for k, ouid in enumerate(ids):
        games = []
        for (pl,) in c.execute("SELECT m.payload FROM matches m JOIN match_players p ON p.match_id=m.match_id "
                               "WHERE p.ouid=? AND m.match_type=52 ORDER BY m.match_date DESC", (ouid,)):
            try:
                g = game_record(json.loads(pl), ouid)
            except Exception:
                continue
            if g:
                games.append(g)
        out.append({"name": f"계정{k + 1}", "games": games})  # 최신순
    return out


def measure_c(owners, accounts):
    print("\n== 측정 C (C4) ==")
    passed = 0
    for L, side in CELLS:
        d = Dist(owners, L, side)
        if not d.ok:
            print(f"[FAIL] {L}·{side}: 쓸 지표 없음(σ=0: {d.dropped})")
            continue
        # ⓐ 반분
        def half(o, h):
            gs = o["games"]
            m = len(gs) // 2
            return gs[:m] if h == 0 else gs[m:]
        d0 = Dist(owners, L, side, lambda o: half(o, 0))
        d1 = Dist(owners, L, side, lambda o: half(o, 1))
        a0, a1 = [], []
        for o in (owners if d0.ok and d1.ok else []):
            v0, v1 = mean_vec(diffs(half(o, 0), L)), mean_vec(diffs(half(o, 1), L))
            if v0 and v1:
                a0.append(d0.index(v0))
                a1.append(d1.index(v1))
        r = spearman(a0, a1) if len(a0) >= 3 else 0.0
        sb = 2 * r / (1 + r) if r > -1 else 0.0
        # 창
        per_acc, held, tot = {}, 0, 0
        for acc in accounts:
            stages = []
            gs = acc["games"]
            for s in range(0, len(gs) - WIN + 1, WIN):
                rows = diffs(gs[s:s + WIN], L)
                tot += 1
                if len(rows) < MIN_LINE:
                    stages.append(None)
                    continue
                per = [d.index(r_) for r_ in rows]
                x = sum(per) / len(per)
                se = statistics.stdev(per) / math.sqrt(len(per))
                lo, hi = stage(d.pct(x - Z90 * se)), stage(d.pct(x + Z90 * se))
                stages.append(stage(d.pct(x)) if lo == hi else None)
            got = [s for s in stages if s is not None]
            held += len(got)
            pairs = [(a, b) for i, a in enumerate(got) for b in got[i + 1:]]
            per_acc[acc["name"]] = (stages, sum(abs(a - b) <= 1 for a, b in pairs), len(pairs))
        good = sum(v[1] for v in per_acc.values())
        npairs = sum(v[2] for v in per_acc.values())
        stab = good / npairs if npairs else 0.0
        ok = sb >= SB_MIN and npairs > 0 and stab >= STABLE_MIN and held > 0
        passed += ok
        print(f"[{'OK' if ok else 'FAIL'}] {L}·{side}: ⓐ r={r:.3f} SB={sb:.3f}(≥{SB_MIN}) · "
              f"ⓑ {good}/{npairs}={stab:.0%}(≥{STABLE_MIN:.0%}) · ⓒ 판정 {held}/{tot} 창 · 뺀 지표 {d.dropped or '없음'}")
        for name, (stages, g_, n_) in per_acc.items():
            print(f"      {name}: 창 단계 {['보류' if s is None else s + 1 for s in stages]} · 쌍 {g_}/{n_}")
    verdict = passed >= 2
    print(f"[{'OK' if verdict else 'FAIL'}] C4 — 통과 칸 {passed}/6 (2칸 이상이면 낸다)")
    return verdict


def owner_cards(games):
    """(spid, 줄, 강화대) → (판수, 평점 합) — 그 사람이 30판 넘게 쓴 칸만 평균으로."""
    acc = defaultdict(lambda: [0, 0.0])
    for g in games:
        for spid, L, grade, rating in g["cards"]:
            a = acc[(spid, L, band(grade))]
            a[0] += 1
            a[1] += rating
    return {k: s / n for k, (n, s) in acc.items() if n > CARD_MIN_GAMES}


def measure_d(owners, accounts):
    print("\n== 측정 D (N27) ==")
    per_owner = [owner_cards(o["games"]) for o in owners]
    cell_n = defaultdict(int)
    for oc in per_owner:
        for k in oc:
            cell_n[k] += 1
    targets = [a for a in accounts if 800 <= len(a["games"]) <= 1500]
    mine = {a["name"]: owner_cards(a["games"][:config.OWNER_DIST_MATCHES]) for a in targets}
    for T in (3, 5, 10, 20):
        print(f"   T={T}: " + " · ".join(f"{n} {sum(cell_n[k] >= T for k in m)}/{len(m)}장" for n, m in mine.items()))
    T = 0
    for t in range(5, 200):
        if targets and all(sum(cell_n[k] >= t for k in m) >= CARD_MIN_CARDS for m in mine.values()):
            T = t
        else:
            break
    ok1 = T >= 5
    print(f"[{'OK' if ok1 else 'FAIL'}] ① 대상 계정 {len(targets)}개 · 문턱 T={T if ok1 else '없음'}")
    if not ok1:
        print("[FAIL] N27 — ① 미달")
        return False, T

    def split_corr(sel, rng):
        corrs = []
        for _ in range(SPLITS):
            idx = list(range(len(sel)))
            rng.shuffle(idx)
            h = len(idx) // 2
            groups = [defaultdict(list), defaultdict(list)]
            for gi, part in enumerate((idx[:h], idx[h:])):
                for i in part:
                    for k, v in sel[i].items():
                        groups[gi][k].append(v)
            xs, ys = [], []
            for k in set(groups[0]) & set(groups[1]):
                if len(groups[0][k]) + len(groups[1][k]) >= T:
                    xs.append(statistics.fmean(groups[0][k]))
                    ys.append(statistics.fmean(groups[1][k]))
            if len(xs) >= 3:
                corrs.append(statistics.correlation(xs, ys))
        return statistics.median(corrs) if corrs else float("nan"), len(xs)
    rng = random.Random(SEED)
    med, ncell = split_corr(per_owner, rng)
    boots = []
    for _ in range(BOOT):
        sel = [per_owner[rng.randrange(len(per_owner))] for _ in per_owner]
        m, _ = split_corr(sel, rng)
        if not math.isnan(m):
            boots.append(m)
    boots.sort()
    lo = boots[int(0.05 * len(boots))] if boots else float("nan")
    ok2 = lo > CORR_MIN
    print(f"[{'OK' if ok2 else 'FAIL'}] ② 반쪽 상관 중앙값 {med:.3f} · 칸 {ncell} · 부트스트랩 5% {lo:.3f}(>{CORR_MIN})")
    print(f"[{'OK' if ok2 else 'FAIL'}] N27")
    return ok2, T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    a = ap.parse_args()
    owners, n = load_owners(a.rows)
    print(f"표본 구단주 {len(owners)}명(층별 {n}) · 자리 빈 경기 {sum(o['empty_pos'] for o in owners)}/"
          f"{sum(o['n_games'] for o in owners)} · 추출 {sum(o['extract_s'] for o in owners):.1f}초")
    accounts = dev_accounts()
    print("개발 PC 계정: " + " · ".join(f"{x['name']} {len(x['games'])}판" for x in accounts))
    measure_c(owners, accounts)
    measure_d(owners, accounts)


if __name__ == "__main__":
    main()
