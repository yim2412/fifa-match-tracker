"""43단계 조각 ② 시작 측정 — A4 당김 상수 K 를 반쪽 공분산으로 (이 PC 전용 · fifa.db 읽기 전용 · 결과는 화면에만).

    python tools/gk_reliability.py                              # 실데이터 판정
    PLANT_SD=0.03 SIM_SEED=1 python tools/gk_reliability.py     # 자기 점검: 카드마다 효과를 심은 가짜 골(0 이면 효과 없음)

판정 기록은 ROADMAP 43단계 "측정 6 · K 결과"(2026-10-12 — 미달). U8 의 "다시 볼 조건" 때 이걸 그대로 다시 돌린다.

규칙(재기 전에 고정):
- 단위 = (구단주 ouid, 선발 GK spId). 슛 = 상대가 찬 유효슛(골·막힌 유효슛) · 페널티킥 제외 ·
  그 쪽 GK 0명 · 교체 명단(28)에 spRating>0 인 선수가 있는 쪽은 제외(측정 6).
- 잔차 r = 유효슛 C1o 예측 − 골(0/1). 카드의 경기들을 날짜순으로 놓고 홀·짝 경기로 반을 나눈다.
- 두 반 모두 유효슛 20개 이상인 카드만. 슛 하나의 잡음 분산 σ²_e = 평균 p(1−p) · K = σ²_e / σ²_t
  (n/(n+K) 당김의 표준식).
- **판정은 구단주 안 분산으로**: 당김은 그 사용자의 같은 구간 키퍼 평균 쪽이라, 필요한 σ²_t 는 같은 구단주 안
  카드끼리의 분산이다. 두 반 모두 20슛 넘는 카드가 2장 이상인 구단주만 · 반마다 그 구단주 카드 평균을 빼고
  공분산(자유도 = 카드 수 − 구단주 수). 구단주 단위 부트스트랩 1,000번(시드 20261012) 95% 하한 ≤ 0 이면
  카드끼리 차이가 잡음과 구분 안 되는 것 → A4 를 뺀다. 전체(구단주 사이 포함)는 보고만.
- ⚠ 카드·구단주 수가 적으면 부트스트랩이 관대하다(10-12: 효과 0 가짜 다섯 중 하나가 통과) — 통과가 나와도
  자기 점검의 효과 0 결과와 같이 본다.
"""
import os
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import xg_grid  # noqa: E402
import xgmodel  # noqa: E402

SUB = stats.SUB_POSITION
HALF_MIN = 20
SEED = 20261012
PLANT = float(os.environ["PLANT_SD"]) if os.environ.get("PLANT_SD") else None
sim = random.Random(int(os.environ.get("SIM_SEED", "7")))
card_eff = {}


def load():
    conn = sqlite3.connect("file:" + str(config.DB_PATH).replace("\\", "/") + "?mode=ro", uri=True)
    cards = defaultdict(list)   # (ouid, spId) -> [(date, [r...], [pq...])]
    n_side = n_skip_gk = n_skip_sub = 0
    for _mid, date, pl in conn.execute("SELECT match_id, match_date, payload FROM matches WHERE match_type=52"):
        try:
            d = store._loads(pl)
        except Exception:
            continue
        infos = d.get("matchInfo") or []
        if len(infos) != 2:
            continue
        for i in (0, 1):
            me, opp = infos[i], infos[1 - i]
            ps = me.get("player") or []
            if not ps:
                continue
            n_side += 1
            gks = [p for p in ps if p.get("spPosition") == 0]
            if len(gks) != 1:
                n_skip_gk += 1
                continue
            if any(p.get("spPosition") == SUB and ((p.get("status") or {}).get("spRating") or 0) > 0 for p in ps):
                n_skip_sub += 1
                continue
            key = (me.get("ouid"), gks[0].get("spId"))
            rs, pq = [], []
            for sd in opp.get("shootDetail") or []:
                res, x, y, t = sd.get("result"), sd.get("x"), sd.get("y"), sd.get("type")
                if res not in (stats.SHOT_GOAL, stats.SHOT_ON_TARGET):
                    continue
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    continue
                if t == xg_grid.PK_TYPE:
                    continue
                p = xgmodel.predict(xg_grid.ON_TARGET, float(x), float(y), t if isinstance(t, int) else None)
                goal = 1.0 if res == stats.SHOT_GOAL else 0.0
                if PLANT is not None:      # 자기 점검: 카드마다 고정 효과 e 를 심은 가짜 골
                    if key not in card_eff:
                        card_eff[key] = sim.gauss(0, PLANT)
                    goal = 1.0 if sim.random() < min(max(p - card_eff[key], 0.0), 1.0) else 0.0
                rs.append(p - goal)
                pq.append(p * (1 - p))
            if rs:
                cards[key].append((date, rs, pq))
    conn.close()
    return cards, n_side, n_skip_gk, n_skip_sub


def _means(h):
    n = len(h)
    return sum(x for x, *_ in h) / n, sum(y for _, y, *_ in h) / n


def cov(h):
    ma, mb = _means(h)
    return sum((x - ma) * (y - mb) for x, y, *_ in h) / (len(h) - 1)


def corr(h):
    ma, mb = _means(h)
    va = sum((x - ma) ** 2 for x, *_ in h) / (len(h) - 1)
    vb = sum((y - mb) ** 2 for _, y, *_ in h) / (len(h) - 1)
    return cov(h) / (va * vb) ** 0.5


def within_cov(groups):
    s = 0.0
    dof = 0
    for g in groups:
        ma, mb = _means(g)
        s += sum((x - ma) * (y - mb) for x, y, *_ in g)
        dof += len(g) - 1
    return s / dof


def main():
    cards, n_side, n_skip_gk, n_skip_sub = load()
    halves, pq_all = [], []
    for key, games in cards.items():
        games.sort(key=lambda g: g[0])
        a = [r for j, g in enumerate(games) if j % 2 == 0 for r in g[1]]
        b = [r for j, g in enumerate(games) if j % 2 == 1 for r in g[1]]
        if len(a) >= HALF_MIN and len(b) >= HALF_MIN:
            halves.append((sum(a) / len(a), sum(b) / len(b), len(a) + len(b), key[0]))
            pq_all.extend(q for g in games for q in g[2])
    if len(halves) < 3:
        print(f"[FAIL] 두 반 {HALF_MIN}슛 넘는 카드 {len(halves)}장 — 잴 재료가 없다")
        return 1
    by_owner = defaultdict(list)
    for h in halves:
        by_owner[h[3]].append(h)
    owners = [v for v in by_owner.values() if len(v) >= 2]
    s2e = sum(pq_all) / len(pq_all)
    print(f"쪽 {n_side} · GK≠1 제외 {n_skip_gk} · 교체 뛴 쪽 제외 {n_skip_sub} ({n_skip_sub / n_side:.2%})")
    print(f"카드(구단주×GK) 전체 {len(cards)} · 두 반 {HALF_MIN}슛 이상 {len(halves)} · 그 카드 슛 중앙값 "
          f"{sorted(h[2] for h in halves)[len(halves) // 2]}")
    print(f"반쪽 상관 r = {corr(halves):.3f} · (보고) 구단주 사이 포함 σ²_t = {cov(halves):.5f}")
    if len(owners) < 2:
        print(f"[FAIL] 카드 2장 이상인 구단주 {len(owners)}명 — 구단주 안 분산을 못 잰다")
        return 1
    s2t = within_cov(owners)
    rng = random.Random(SEED)
    boots = sorted(within_cov([owners[rng.randrange(len(owners))] for _ in owners]) for _ in range(1000))
    lo, hi = boots[24], boots[974]
    print(f"구단주 안 판정 재료: 구단주 {len(owners)} · 카드 {sum(len(g) for g in owners)}")
    print(f"σ²_t = {s2t:.5f} [95% {lo:.5f}, {hi:.5f}] · σ²_e = {s2e:.4f}")
    if lo <= 0:
        print("[FAIL] 하한 ≤ 0 — 카드 차이가 잡음과 구분 안 됨 → A4 를 뺀다")
        return 1
    print(f"[OK] K = {s2e / s2t:.1f} (구간 {s2e / hi:.1f} ~ {s2e / lo:.1f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
