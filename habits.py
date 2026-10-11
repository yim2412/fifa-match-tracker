"""내 경기 습관·기준선 계산(2.6.1 — ROADMAP "2.6.1" 절). 화면 없음 · 순수 계산 · 화면은 core_api 로.

지금 있는 것:
| 함수 | 화면 | 메모 |
|---|---|---|
| `division_stays(points, name_of)` | [성적 진단 › 등급 체류](N2) | 재료는 `stats.division_trend` — (일시, division) 오래된 것부터 |
| `side_totals(details, ouid)` | (N4 기준선 — N19 칩 · A1 · N17 이 쓴다) | 나 / 같은 경기 상대의 경기마다 값 · 승·무·패 경기만 |
| `paired_diff(rows, metric)` | 〃 | 지표 하나의 나 − 상대 차와 95% 구간(경기 단위 짝) · 지표 이름은 `METRICS` |

기준선의 구간(N4 · ROADMAP "구간 방법"): 나와 상대는 **같은 경기의 짝**이고 슛은 경기 안에서 뭉친다 — 두 비율을
독립으로 놓고 정규 근사하면 구간이 좁게 나온다. 그래서 전부 경기 단위로 짝지은 값의 평균·표준편차로 낸다.
- 개수·점유(경기당 슛 등): 경기마다 dᵢ = 나 − 상대, 차 = 평균 d, 표준오차 = sd(d)/√n.
- 비율(결정력 = Σ골÷Σ슛 등): 비율 추정량의 선형화 uᵢ = (gᵢ − R·sᵢ)/s̄ − (g'ᵢ − R'·s'ᵢ)/s̄' 의 sd(u)/√n.
  분모가 0 인 경기(슛 0)도 빼지 않는다 — 그 경기의 항이 0 으로 들어가 n 에 그대로 센다(빼면 n 이 지표마다 달라진다).
- 크기 문턱(`config.N19_MIN_GAP`·`A1_MIN_GAP`)은 여기서 안 건다 — 판정은 쓰는 쪽(칩·유형)이 `Diff.clear(gap)` 로.

등급 체류의 규칙(N2):
- division id 는 **작을수록 상위**(800 슈퍼챔피언스 · 900 챔피언스 · … 1300 → 1700 도 같은 방향 — 1400~1600 은 없다).
  앞 경기보다 id 가 작아지면 승격, 커지면 강등.
- 기록 시작 때 이미 있던 등급은 "들어온 횟수"에 안 센다(언제 올랐는지 모른다 — `stats.division_entries` 와 같다).
- **기록 빈 구간**: 이웃 경기 사이가 `config.N2_GAP_DAYS` 일을 넘으면(API 는 약 한 달까지만 주므로 앱을 오래 안 켜면
  경기가 빈다) 그 앞뒤 등급 차이는 승격·강등에도, 들어온 횟수에도 안 센다 — 빈 사이에 몇 번 오갔는지 모른다.
  머문 날 수는 경기가 있던 날만 세므로 빈 사이 날은 원래 안 들어간다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config
from stats import _me_opp, _num, _result_of, _wdl_of


@dataclass
class DivisionStay:
    """등급 하나에 머문 기록."""
    division: int
    name: str
    games: int = 0      # 그 등급으로 뛴 경기 수
    days: int = 0       # 그 등급으로 끝난 날 수(그날 마지막 경기의 등급)
    entries: int = 0    # 다른 등급에서 들어온 횟수(기록 시작·빈 구간 너머는 안 센다)


@dataclass
class DivisionStays:
    rows: list[DivisionStay] = field(default_factory=list)   # 높은 등급(id 작은 것)부터
    promotions: int = 0
    demotions: int = 0
    gaps: int = 0               # 기록 빈 구간 수 — 그 사이 승강은 모른다
    start_division: int | None = None   # 기록 시작 때 등급(들어온 횟수에 안 센 것)


def division_stays(points: list[tuple[datetime, int]], name_of=None) -> DivisionStays:
    """points 는 (경기 일시, 내 division) — 순서는 상관없다(시각순으로 다시 놓는다). division 없는 경기는 재료에 이미 없다."""
    seq = sorted(points, key=lambda t: t[0])
    out = DivisionStays()
    if not seq:
        return out
    gap = timedelta(days=config.N2_GAP_DAYS)
    rows: dict[int, DivisionStay] = {}

    def row(div: int) -> DivisionStay:
        r = rows.get(div)
        if r is None:
            r = rows[div] = DivisionStay(div, name_of(div) if name_of else str(div))
        return r

    out.start_division = seq[0][1]
    last_of_day: dict = {}
    prev_t, prev_div = None, None
    for t, div in seq:
        r = row(div)
        r.games += 1
        last_of_day[t.date()] = div
        if prev_div is not None and div != prev_div:
            if t - prev_t > gap:
                pass   # 빈 구간 너머 — 승강·진입 모름(아래에서 빈 구간으로만 센다)
            else:
                r.entries += 1
                if div < prev_div:
                    out.promotions += 1
                else:
                    out.demotions += 1
        if prev_t is not None and t - prev_t > gap:
            out.gaps += 1
        prev_t, prev_div = t, div
    for div in last_of_day.values():
        row(div).days += 1
    out.rows = [rows[k] for k in sorted(rows)]
    return out


# ── N4 기준선 — 나 / 같은 경기 상대 ────────────────────────────────────────
SIDE_FIELDS = ("shots", "on_target", "goals", "tackle_try", "tackle_ok", "possession",
               "long_through", "pass_try")


@dataclass
class SideRow:
    """경기 하나의 나·상대 값(키는 `SIDE_FIELDS`)."""
    match_id: str
    result: int          # 0 승 · 1 무 · 2 패(stats._wdl_of)
    mine: dict
    opp: dict


@dataclass
class SideTotals:
    rows: list[SideRow] = field(default_factory=list)

    @property
    def games(self) -> int:
        return len(self.rows)

    def total(self, side: str, key: str) -> float:
        return sum(getattr(r, side)[key] for r in self.rows)


def _side_values(p: dict) -> dict:
    shoot = p.get("shoot") or {}
    pas = p.get("pass") or {}
    dfn = p.get("defence") or {}
    return {
        "shots": _num(shoot, "shootTotal"),
        "on_target": _num(shoot, "effectiveShootTotal"),
        "goals": _num(shoot, "goalTotal"),
        "tackle_try": _num(dfn, "tackleTry"),
        "tackle_ok": _num(dfn, "tackleSuccess"),
        "possession": _num(p.get("matchDetail") or {}, "possession"),
        "long_through": _num(pas, "longPassTry") + _num(pas, "throughPassTry"),
        "pass_try": _num(pas, "passTry"),
    }


def side_totals(details: list[dict], ouid: str) -> SideTotals:
    """같은 경기들의 나 / 상대 값 — 승·무·패가 아닌 경기("오류")와 상대가 없는 경기는 뺀다(team_profile 과 같은 기준)."""
    out = SideTotals()
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None or not opp:
            continue
        res = _wdl_of(_result_of(me))
        if res is None:
            continue
        out.rows.append(SideRow(d.get("matchId") or "", res, _side_values(me), _side_values(opp)))
    return out


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    num: str
    den: str | None = None      # None 이면 경기당 값(개수·점유) — 있으면 Σnum÷Σden 비율(%)


METRICS = {m.key: m for m in (
    Metric("finish", "결정력", "goals", "shots"),
    Metric("on_target_rate", "유효슛 비율", "on_target", "shots"),
    Metric("goals_per_on_target", "유효슛당 골", "goals", "on_target"),
    Metric("tackle_rate", "태클 성공", "tackle_ok", "tackle_try"),
    Metric("long_through_rate", "롱·스루 패스 비율", "long_through", "pass_try"),
    Metric("shots", "경기당 슛", "shots"),
    Metric("on_target", "경기당 유효슛", "on_target"),
    Metric("tackles", "경기당 태클", "tackle_try"),
    Metric("possession", "점유율", "possession"),
)}

Z95 = 1.96


@dataclass
class Diff:
    """지표 하나의 나 − 상대 — 비율이면 %p, 경기당 값이면 그 단위."""
    metric: Metric
    n: int
    mine: float
    opp: float
    se: float                   # 경기 단위 짝 표준오차(n < 2 면 inf)
    mine_den: float = 0.0       # 비율 지표의 분모 합(표본 하한 판정용) — 경기당 값이면 0
    opp_den: float = 0.0

    @property
    def diff(self) -> float:
        return self.mine - self.opp

    @property
    def lo(self) -> float:
        return self.diff - Z95 * self.se

    @property
    def hi(self) -> float:
        return self.diff + Z95 * self.se

    def clear(self, min_gap: float) -> bool:
        """95% 구간이 0 을 안 품고 차이가 min_gap 이상 — 칩·유형 글자를 띄우는 조건(U2·U6)."""
        return (self.lo > 0 or self.hi < 0) and abs(self.diff) >= min_gap


def _sd(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return math.inf
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def paired_diff(totals: SideTotals, key: str) -> Diff:
    """경기 단위로 짝지은 나 − 상대(모듈 머리말 "기준선의 구간")."""
    m = METRICS[key]
    rows = totals.rows
    n = len(rows)
    if m.den is None:
        ds = [r.mine[m.num] - r.opp[m.num] for r in rows]
        mine = sum(r.mine[m.num] for r in rows) / n if n else 0.0
        opp = sum(r.opp[m.num] for r in rows) / n if n else 0.0
        return Diff(m, n, mine, opp, _sd(ds) / math.sqrt(n) if n else math.inf)
    g, s = totals.total("mine", m.num), totals.total("mine", m.den)
    g2, s2 = totals.total("opp", m.num), totals.total("opp", m.den)
    if not n or not s or not s2:
        return Diff(m, n, 0.0, 0.0, math.inf, s, s2)
    r1, r2 = g / s, g2 / s2
    sbar, sbar2 = s / n, s2 / n
    us = [(r.mine[m.num] - r1 * r.mine[m.den]) / sbar - (r.opp[m.num] - r2 * r.opp[m.den]) / sbar2 for r in rows]
    return Diff(m, n, r1 * 100, r2 * 100, _sd(us) / math.sqrt(n) * 100, s, s2)
