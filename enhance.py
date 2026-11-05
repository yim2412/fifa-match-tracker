"""강화 비용 계산(83단계) — 계산만(요청은 `boostcollect`·`playerinfo`, 화면은 `core_api`). 넥슨 규칙 표가 전부 여기 상단에.

| 표 | 출처 |
|---|---|
| 성공 확률 `SUCCESS_PCT` · 실패 복구 `RECOVERY_PCT` | 넥슨 공지 「선수 강화 성공 확률…」(26.4 기준 — 2026-11-05 이미지와 대조) |
| 보호 `PROTECT_*` | 넥슨 공지 「BP 강화 등급 보호 적용 구간 확대」(5/28 · 10/29 갱신) · 「선수별 BP 강화 등급 보호 비용」(6/25) + 사용자 확인(성공해도 낸다 · 기준가 = 현재 단계 시세). 선수별 30% 조정 명단은 매주 바뀌어 안 넣는다 — 화면에서 고른다 |
| 게이지 `GAUGE_*` | 강화 부스트 도우미 응답 실측(2026-11-05 — 넥슨이 공식을 안 적어 둔다) |

**게이지 규칙(실측)**: 기준 OVR X = 대상 1강 OVR + 누적 강화 OVR + `GAUGE_ANCHOR`. 재료 OVR 이 X 면 89%, 한 칸 아래마다 약 1/1.35,
X 보다 높으면 100%, 1~3강 도전은 `GAUGE_LOW_GRADE` 로 나눈다. 재료 OVR 은 **첫 번째(주) 포지션 값**이다 — 가장 높은 포지션으로 읽으면 한 칸씩 어긋난다.
값은 카드에 따라 1%p 안쪽으로 다르다(88.7 vs 89) — 그래서 대략값이다. 재료 장수 한도는 5, 게이지가 100% 밑이면 성공 확률 = 표 값 × 게이지.

**재료비는 OVR 마다 낮은 값 `MATERIAL_AVG_LOWEST` 개의 평균** — 도우미의 "평균 가치" 와 같은 방식. 최저 한 장은 매물이 없을 수 있다.
같은 OVR 이어도 강화된 싼 카드가 1강보다 훨씬 쌀 때가 많고(실측 20,600 vs 4,380만), 높은 OVR 이 더 쌀 때도 있어 **모든 조합을 대조한다**(`best_materials`).
"""
from __future__ import annotations

import bisect
import itertools
import random
import re
from dataclasses import dataclass, replace

import config

SUCCESS_PCT = {1: 100, 2: 81, 3: 64, 4: 50, 5: 26, 6: 15, 7: 7, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
# 강화 대상과 같은 선수를 소모하지 않은 경우 · 현재 강화 → {복구 강화: %}
RECOVERY_PCT = {
    1: {1: 100}, 2: {1: 100}, 3: {1: 65, 2: 35}, 4: {1: 55, 2: 45},
    5: {1: 35, 2: 40, 3: 25}, 6: {1: 10, 2: 32, 3: 36, 4: 22},
    7: {1: 4, 2: 10, 3: 30, 4: 35, 5: 21}, 8: {1: 2, 2: 4, 3: 10, 4: 28, 5: 35, 6: 21},
    9: {1: 1, 2: 2, 3: 4, 4: 10, 5: 28, 6: 34, 7: 21},
    10: {1: 1, 2: 1, 3: 2, 4: 4, 5: 10, 6: 28, 7: 34, 8: 20},
    11: {1: 1, 2: 1, 3: 1, 4: 2, 5: 4, 6: 9, 7: 28, 8: 34, 9: 20},
    12: {1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 3, 7: 9, 8: 28, 9: 34, 10: 20},
}
CUM_OVR = config.GRADE_OVR_BONUS   # 1강 대비 누적 OVR — 공지 표와 같다(test_enhance 가 대조)
GRADES_MIN, GRADES_MAX = 1, 13

GAUGE_ANCHOR = 5
GAUGE_STEPS = (89.0, 66.0, 48.9, 36.3, 27.0, 20.0, 14.8, 11.0, 8.2, 6.0, 4.5, 3.3, 2.5)   # X, X-1, X-2 … 의 게이지 %
GAUGE_LOW_GRADE = {1: 0.4, 2: 0.6, 3: 0.8}   # 1~3강 도전은 필요량이 이 비율이라 게이지가 그만큼 빨리 찬다(실측 2026-11-05)
GAUGE_FULL = 100.0
MATERIAL_SLOTS = 5
MATERIAL_AVG_LOWEST = 10
MATERIAL_MIN_ROWS = 3         # 이보다 적은 OVR 은 믿지 않는다 — 한두 장 시세가 조합을 정해 버린다
SCAN_ABOVE = 3                # X 위로도 — 높은 OVR 이 더 쌀 때가 있다(사용자 2026-11-05) · 아래쪽 끝은 5장으로 100% 를 채울 수 있는 칸

PROTECT_FROM = 2              # 넥슨은 1~13강 전부 받지만(사용자 2026-11-05) 1강 도전은 실패해도 1강이라 보호비만 나간다 — 2강 도전부터 센다
PROTECT_TIERS = ((92, 95), (100, 80), (104, 60), (None, 40))   # (1강 OVR 상한, 기준가 대비 %) — 공식 표 8~12강 그룹. 7강까지는 표에 "30~95%" 뿐이거나 없어 같은 구분을 쓴다(화면에서 고칠 수 있다)
PROTECT_TABLE_FROM = 8        # 이 강화부터 공식 비율표가 있다 — 그 아래 비율은 범위(PROTECT_RANGE_PCTS)도 같이 보여 준다
PROTECT_RANGE_PCTS = (30, 95)
PROTECT_LIVE_PCT = 95
PROTECT_LIVE_SEASONS = frozenset({300, *range(317, 325)})   # 17~25 LIVE — seasonid 메타
PROTECT_ICON_TM_SEASONS = frozenset({100, 110})             # ICON TM · ICON TM(제한) — 5~7강 중 5→6 만
PROTECT_ICON_TM_SKIP = frozenset({6, 7})
# SH · HRT · UC · LKI2 — 5/28 공지 대상 목록(10/29 까지 갱신)에 없는 클래스. 공지와 메타를 2026-11-05 에 대조했다
PROTECT_EXCLUDED_SEASONS = frozenset({596, 849, 877, 879})

BUDGET_QS = (50, 75, 80, 90, 95, 99)   # 금액 구간 표와 기준 고르기의 "붙을 확률"(사용자 2026-11-05)
BUDGET_EXPECT_Q = 80          # 맨 위에 적는 기본 기준 — 부족해도 보호면 단계가 남아 더 모아 이으면 된다(판단이지 실측이 아니다)
BUDGET_TAIL_Q = 90            # 이 확률을 넘긴 나머지의 평균을 한 줄로 — 천장이 없어 "최대 비용" 을 적을 수 없다
CURVE_POINTS = 120            # 그래프 점 수 · 끝은 BUDGET_QS 마지막 확률에서 자른다(꼬리가 축을 늘여 곡선이 눌리지 않게)

SIM_RUNS = 20000
SIM_STEP_BUDGET = 3_000_000   # 시도 수 합 상한 — 평균 수백만 번인 구간은 흉내 내지 않고 평균만 낸다
SIM_MIN_RUNS = 200
SIM_TAIL_MIN = 20             # 그 확률 위쪽에 이만큼은 남아야 금액을 적는다 — 몇 번 안 되는 큰 값이 정하는 숫자는 믿지 않는다
SIM_SEED = 20261105           # 같은 입력이면 같은 답


class EnhanceError(Exception):
    """사람이 읽을 사유 — 화면에 그대로 뜬다."""


@dataclass(frozen=True)
class MaterialCard:
    ovr: int
    count: int
    price: int      # 한 장 값
    gauge: float    # 한 장 게이지 %


@dataclass(frozen=True)
class Materials:
    cards: tuple[MaterialCard, ...]
    cost: int
    gauge: float    # 합(100 으로 자르지 않은 값)


@dataclass(frozen=True)
class Step:
    grade: int
    success_pct: float     # 게이지 반영
    materials: Materials
    protect_cost: int      # 보호 안 하면 0
    protected: bool
    mean_attempts: float = 0.0   # 시작 → 목표 사이 이 강화에서 평균 몇 번 시도하나(떨어져 다시 온 횟수 포함)

    @property
    def attempt_cost(self) -> int:
        return self.materials.cost + self.protect_cost

    @property
    def mean_cost(self) -> float:
        return self.mean_attempts * self.attempt_cost


@dataclass(frozen=True)
class Estimate:
    start: int
    target: int
    protect: bool                # 보호를 켰고 제외 클래스가 아니다 — 그 구간에 실제로 걸렸는지는 steps 의 protected
    protect_excluded: bool       # 켰는데 제외 클래스
    protect_pct: int
    steps: tuple[Step, ...]
    mean_cost: float
    mean_material: float
    mean_protect: float
    mean_attempts: float
    costs: tuple[int, ...]       # 흉내 낸 총비용(오름차순) — 시도가 너무 많은 구간은 비어 평균만 낸다
    start_price: int | None
    target_price: int | None
    rate_range: tuple[float, float] | None = None   # 표에 없는 구간 비율이 PROTECT_RANGE_PCTS 끝값일 때 평균 — 비율을 직접 골랐거나 그 구간이 없으면 None

    @property
    def path_total(self) -> float | None:
        """시작 카드를 시세로 사서 강화했을 때."""
        return None if self.start_price is None else self.start_price + self.mean_cost

    def _q_index(self, q: int) -> int:
        """비용이 이 칸 이하인 판이 q% 이상이 되는 첫 칸."""
        return (len(self.costs) * q + 99) // 100 - 1

    def budget_at(self, q: int) -> int | None:
        """이 금액이면 q% 확률로 끝난다. 흉내 낸 수가 적어 그 위쪽이 몇 판 안 남으면 None."""
        if len(self.costs) * (100 - q) // 100 < SIM_TAIL_MIN:
            return None
        return self.costs[self._q_index(q)]

    def tail_mean(self, q: int) -> float | None:
        """q% 금액으로 못 끝낸 나머지 판들의 평균."""
        if self.budget_at(q) is None:
            return None
        tail = self.costs[self._q_index(q) + 1:]
        return sum(tail) / len(tail)

    def chance_within(self, amount: int) -> float | None:
        """이 금액으로 끝날 확률(0~1)."""
        if not self.costs:
            return None
        return bisect.bisect_right(self.costs, amount) / len(self.costs)

    @property
    def budgets(self) -> tuple[tuple[int, int], ...]:
        """(붙을 확률, 금액) — 금액 구간 표."""
        return tuple((q, self.budget_at(q)) for q in BUDGET_QS if self.budget_at(q) is not None)

    @property
    def curve(self) -> tuple[tuple[int, float], ...]:
        """(금액, 붙을 확률 %) — 그래프 점. 확률 칸을 고르게 나눠 뽑는다(금액으로 나누면 긴 꼬리에 점이 몰린다)."""
        top = max((q for q, _b in self.budgets), default=None)
        if top is None:
            return ()
        last = self._q_index(top)
        idx = sorted({last * i // CURVE_POINTS for i in range(CURVE_POINTS + 1)})
        return tuple((self.costs[i], 100 * (i + 1) / len(self.costs)) for i in idx)


_MONEY_PART = re.compile(r"(\d+(?:\.\d+)?)(조|억|천만|백만|만|천)?")
_MONEY_UNIT = {"조": 10 ** 12, "억": 10 ** 8, "천만": 10 ** 7, "백만": 10 ** 6, "만": 10 ** 4, "천": 10 ** 3, "": 1}


def parse_money(text: str) -> int | None:
    """"3000만" · "2천만" · "1.5억" · "1억 2,000만" · "12345678" → 정수. 못 읽거나 0 이하면 None."""
    s = re.sub(r"[\s,]|BP$", "", (text or "").strip(), flags=re.I)
    pos, total = 0, 0.0
    while pos < len(s):
        m = _MONEY_PART.match(s, pos)
        if m is None:
            return None
        total += float(m.group(1)) * _MONEY_UNIT[m.group(2) or ""]
        pos = m.end()
    return round(total) if total > 0 else None


def gauge_of(card_ovr: int, grade: int, material_ovr: int) -> float:
    """대상(1강 OVR·강화)에 그 OVR 재료 한 장을 넣었을 때 게이지 %."""
    step = card_ovr + CUM_OVR[grade] + GAUGE_ANCHOR - material_ovr
    if step < 0:
        return GAUGE_FULL
    if step >= len(GAUGE_STEPS):
        return 0.0
    return min(GAUGE_FULL, round(GAUGE_STEPS[step] / GAUGE_LOW_GRADE.get(grade, 1.0), 1))


def protect_applies(protect_from: int | None, season_id: int | None) -> bool:
    return protect_from is not None and season_id not in PROTECT_EXCLUDED_SEASONS


def protect_pct(card_ovr: int, season_id: int | None) -> int:
    """기준가의 몇 %가 100% 보호 비용인가. 40% 구간은 넥슨이 최대 10%p 낮출 수 있다 — 화면에서 고칠 수 있다."""
    if season_id in PROTECT_LIVE_SEASONS:
        return PROTECT_LIVE_PCT
    for limit, pct in PROTECT_TIERS:
        if limit is None or card_ovr <= limit:
            return pct
    raise AssertionError("PROTECT_TIERS 끝 줄은 상한이 없다")


def protected_at(grade: int, protect_from: int | None, season_id: int | None) -> bool:
    """그 강화에서 도전할 때 100% 보호가 걸리나. protect_from = 몇 강 도전부터(None = 안 함)."""
    if not protect_applies(protect_from, season_id) or grade < max(protect_from, PROTECT_FROM):
        return False
    return not (season_id in PROTECT_ICON_TM_SEASONS and grade in PROTECT_ICON_TM_SKIP)


def _cannot_fall(grade: int, protect_from: int | None, season_id: int | None) -> bool:
    return protected_at(grade, protect_from, season_id) or set(RECOVERY_PCT[grade]) == {grade}


def grades_needed(start: int, target: int, protect_from: int | None, season_id: int | None = None) -> list[int]:
    """시도가 일어날 수 있는 강화들. 가는 길 어디서도 내려갈 일이 없을 때만 시작 아래를 뺀다."""
    path = range(start, target)
    if all(_cannot_fall(g, protect_from, season_id) for g in path):
        return list(path)
    return list(range(GRADES_MIN, target))


def scan_levels(card_ovr: int, grades: list[int]) -> list[int]:
    """시세를 볼 재료 OVR 들."""
    levels: set[int] = set()
    for g in grades:
        x = card_ovr + CUM_OVR[g] + GAUGE_ANCHOR
        low = x
        while gauge_of(card_ovr, g, low - 1) >= GAUGE_FULL / MATERIAL_SLOTS:
            low -= 1
        levels.update(range(low, x + SCAN_ABOVE + 1))
    return sorted(levels)


def price_by_ovr(rows) -> dict[int, int]:
    """(spid, 강화, OVR, 값) 줄들 → OVR 마다 낮은 값 N 개 평균. 같은 카드·강화는 한 번만, 값 0(시세 없음)은 뺀다."""
    seen: dict[tuple[int, int], tuple[int, int]] = {}
    for sp, strong, ovr, price in rows:
        if price > 0:
            seen[(sp, strong)] = (ovr, price)
    by: dict[int, list[int]] = {}
    for ovr, price in seen.values():
        by.setdefault(ovr, []).append(price)
    out = {}
    for ovr, prices in by.items():
        if len(prices) >= MATERIAL_MIN_ROWS:
            low = sorted(prices)[:MATERIAL_AVG_LOWEST]
            out[ovr] = round(sum(low) / len(low))
    return out


def best_materials(card_ovr: int, grade: int, prices: dict[int, int]) -> Materials | None:
    """100% 를 채우는 가장 싼 조합(값이 같으면 장수가 적은 쪽). 못 채우면 게이지가 가장 높은 조합. 재료가 없으면 None."""
    cheapest: dict[float, tuple[int, int]] = {}
    for ovr, price in prices.items():
        g = gauge_of(card_ovr, grade, ovr)
        if g > 0 and (g not in cheapest or price < cheapest[g][0]):
            cheapest[g] = (price, ovr)
    pool = [(g, price, ovr) for g, (price, ovr) in cheapest.items()]
    best_key, best = None, None
    for n in range(1, MATERIAL_SLOTS + 1):
        for combo in itertools.combinations_with_replacement(pool, n):
            gauge = sum(c[0] for c in combo)
            cost = sum(c[1] for c in combo)
            full = gauge >= GAUGE_FULL
            key = (0, cost, n) if full else (1, -gauge, cost)
            if best_key is None or key < best_key:
                best_key, best = key, combo
    if best is None:
        return None
    counts: dict[int, MaterialCard] = {}
    for g, price, ovr in best:
        have = counts.get(ovr)
        counts[ovr] = MaterialCard(ovr, (have.count if have else 0) + 1, price, g)
    return Materials(tuple(sorted(counts.values(), key=lambda c: -c.ovr)),
                     sum(c[1] for c in best), sum(c[0] for c in best))


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _matrix(steps: dict[int, Step], target: int) -> list[list[float]]:
    """E_i = b_i + p·E_{i+1} + (1-p)·Σ r_ij·E_j(보호면 E_i 그대로) 의 왼쪽."""
    states = sorted(steps)
    idx = {s: i for i, s in enumerate(states)}
    a = [[0.0] * len(states) for _ in states]
    for s in states:
        st, i = steps[s], idx[s]
        p = st.success_pct / 100
        a[i][i] += 1
        if s + 1 < target:
            a[i][idx[s + 1]] -= p
        if st.protected:
            a[i][i] -= 1 - p
        else:
            for j, pct in RECOVERY_PCT[s].items():
                a[i][idx[j]] -= (1 - p) * pct / 100
    return a


def _visits(steps: dict[int, Step], target: int, start: int) -> dict[int, float]:
    """시작 → 목표 사이 강화마다 평균 시도 수 — 전이 행렬을 뒤집은 식 하나로 전부 나온다(비용마다 다시 풀지 않게)."""
    states = sorted(steps)
    a = _matrix(steps, target)
    flipped = [[a[j][i] for j in range(len(states))] for i in range(len(states))]
    return dict(zip(states, _solve(flipped, [1.0 if s == start else 0.0 for s in states])))


def _simulate(steps: dict[int, Step], start: int, target: int, mean_attempts: float) -> tuple[int, ...]:
    """흉내 낸 총비용(오름차순). 시도가 너무 많아 충분히 못 돌리면 빈 값."""
    runs = min(SIM_RUNS, int(SIM_STEP_BUDGET / max(mean_attempts, 1)))
    if runs < SIM_MIN_RUNS:
        return ()
    rng = random.Random(SIM_SEED)
    fall = {}
    for s in steps:
        cum, grades = [], []
        total = 0
        for g, pct in sorted(RECOVERY_PCT[s].items()):
            total += pct
            cum.append(total)
            grades.append(g)
        fall[s] = (cum, grades)
    costs = []
    for _ in range(runs):
        s, cost = start, 0
        while s < target:
            st = steps[s]
            cost += st.attempt_cost
            if rng.random() * 100 < st.success_pct:
                s += 1
            elif not st.protected:
                cum, grades = fall[s]
                s = grades[bisect.bisect_right(cum, rng.random() * 100)]
        costs.append(cost)
    return tuple(sorted(costs))


def _rate_range(steps: dict[int, Step], visits: dict[int, float], prices: dict[int, int], material: float) -> tuple[float, float] | None:
    """공식 비율표에 없는 구간(PROTECT_TABLE_FROM 아래)의 비율이 범위 끝값일 때 평균. 보호 구간은 시도 수가 비율과 무관하다."""
    guessed = [s for s in steps.values() if s.protected and s.grade < PROTECT_TABLE_FROM]
    if not guessed:
        return None
    base = material + sum(visits[s.grade] * s.protect_cost for s in steps.values() if s not in guessed)
    low, high = (base + sum(visits[s.grade] * round(prices[s.grade] * r / 100) for s in guessed) for r in PROTECT_RANGE_PCTS)
    return low, high


def estimate(card_ovr: int, season_id: int | None, start: int, target: int, prices: dict[int, int],
             material_prices: dict[int, int], protect_from: int | None = PROTECT_FROM, pct: int | None = None,
             simulate: bool = True) -> Estimate:
    """start 강 → target 강. prices = 강화 → 시세, material_prices = 재료 OVR → 한 장 값(price_by_ovr).
    protect_from = 몇 강 도전부터 보호(None = 안 함). simulate=False 면 평균만 — 보호 켠/끈 견주기는 평균만 쓴다."""
    if not GRADES_MIN <= start < target <= GRADES_MAX:
        raise EnhanceError("목표 강화는 현재 강화보다 높아야 합니다.")
    use_protect = protect_applies(protect_from, season_id)
    rate = protect_pct(card_ovr, season_id) if pct is None else pct
    steps: dict[int, Step] = {}
    for g in grades_needed(start, target, protect_from, season_id):
        mats = best_materials(card_ovr, g, material_prices)
        if mats is None:
            raise EnhanceError(f"{g}강 도전에 쓸 재료를 찾지 못했습니다.")
        protected = protected_at(g, protect_from, season_id)
        fee = 0
        if protected:
            if g not in prices:
                raise EnhanceError(f"{g}강 시세를 읽지 못해 보호 비용을 낼 수 없습니다.")
            fee = round(prices[g] * rate / 100)
        steps[g] = Step(g, SUCCESS_PCT[g] * min(mats.gauge, GAUGE_FULL) / GAUGE_FULL, mats, fee, protected)
    visits = _visits(steps, target, start)
    steps = {g: replace(st, mean_attempts=visits[g]) for g, st in steps.items()}
    mean_attempts = sum(visits.values())
    mean_material = sum(visits[g] * st.materials.cost for g, st in steps.items())
    mean_protect = sum(visits[g] * st.protect_cost for g, st in steps.items())
    return Estimate(start, target, use_protect, protect_from is not None and not use_protect, rate,
                    tuple(steps[g] for g in sorted(steps)), mean_material + mean_protect, mean_material, mean_protect,
                    mean_attempts, _simulate(steps, start, target, mean_attempts) if simulate else (),
                    prices.get(start), prices.get(target),
                    None if pct is not None else _rate_range(steps, visits, prices, mean_material))
