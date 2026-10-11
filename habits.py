"""내 경기 습관·기준선 계산(2.6.1 — ROADMAP "2.6.1" 절). 화면 없음 · 순수 계산 · 화면은 core_api 로.

지금 있는 것:
| 함수 | 화면 | 메모 |
|---|---|---|
| `division_stays(points, name_of)` | [성적 진단 › 등급 체류](N2) | 재료는 `stats.division_trend` — (일시, division) 오래된 것부터 |
| `side_totals(details, ouid)` | (N4 기준선 — N19 칩 · A1 · N17 이 쓴다) | 나 / 같은 경기 상대의 경기마다 값 · 승·무·패 경기만 |
| `paired_diff(rows, metric)` | 〃 | 지표 하나의 나 − 상대 차와 95% 구간(경기 단위 짝) · 지표 이름은 `METRICS` |
| `strength_chips(totals)` | [흐름 분석] 맨 위 칩 줄(N19) | 결정력·유효슛 비율·태클 성공 중 구간이 0 을 안 품고 크기 문턱을 넘은 것 |
| `half_share_diff(details, ouid)` | 흐름 분석 문장 "전반·후반 어디서 새나"(N19) | 내 실점의 전반 비중 − 같은 경기 상대 실점(= 내 득점)의 전반 비중 |
| `goal_flow(details, ouid)` | [승부처 분석 › 리드·골 흐름](N17) | 최대 리드별 결과 · 골의 성격 · 지고 있을 때 다음 골 · 골 사이 시간 |

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
from models import RateMixin, draw_possible_of
from stats import (_goal_events, _me_opp, _num, _result_of, _set_mixed, _timeline_matches_board, _wdl_of)


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


def paired_diff(totals: SideTotals, key: str | Metric) -> Diff:
    """경기 단위로 짝지은 나 − 상대(모듈 머리말 "기준선의 구간"). key 는 `METRICS` 이름 또는 Metric 자체."""
    m = key if isinstance(key, Metric) else METRICS[key]
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


# ── N19 칩 · 문장 ─────────────────────────────────────────────────────────────

# 칩 셋 — 키는 METRICS · (분모 하한 이름은 config) · 높을수록 좋은 지표뿐이다
N19_CHIP_KEYS = (("finish", "N19_MIN_SHOTS"), ("on_target_rate", "N19_MIN_SHOTS"), ("tackle_rate", "N19_MIN_TACKLES"))


@dataclass(frozen=True)
class Chip:
    """강점·약점 칩 하나 — 나 − 같은 경기 상대가 뚜렷한 지표."""
    diff: Diff

    @property
    def label(self) -> str:
        return self.diff.metric.label

    @property
    def strong(self) -> bool:
        return self.diff.diff > 0

    @property
    def text(self) -> str:
        return f"{self.label} {self.diff.diff:+.1f}%p"


def strength_chips(totals: SideTotals) -> list[Chip]:
    """칩 목록(차이가 큰 순). 분모(슛·태클 시도)가 하한 밑이면 그 칩은 없다 — 표본이 모자라 구간 자체를 믿기 어렵다."""
    out = []
    for key, floor_name in N19_CHIP_KEYS:
        d = paired_diff(totals, key)
        floor = getattr(config, floor_name)
        if min(d.mine_den, d.opp_den) < floor:
            continue
        if d.clear(config.N19_MIN_GAP):
            out.append(Chip(d))
    out.sort(key=lambda c: -abs(c.diff.diff))
    return out


HALF_SHARE = Metric("half_share", "전반 비중", "half", "all")


def _first_half(period: int) -> bool:
    return period == 0


def half_share_diff(details: list[dict], ouid: str) -> Diff:
    """내 실점 중 전반(정규시간) 비중 − 같은 경기 상대 실점(= 내 득점) 중 전반 비중. 연장 골은 뺀다.

    50:50 을 기준으로 두지 않는 이유: 추가시간 때문에 전반 비중이 구조적으로 약 50.7% 라 큰 계정에서 늘 뜬다(ROADMAP N19).
    mine_den = 내 실점 수 · opp_den = 내 득점 수(정규시간) — 문장 하한이 이 둘을 본다."""
    rows = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None or not opp:
            continue
        res = _wdl_of(_result_of(me))
        if res is None:
            continue
        mine_in = [g for g in _goal_events(opp) if g[0] < 2]    # 내가 먹은 골 = 상대 골
        opp_in = [g for g in _goal_events(me) if g[0] < 2]      # 상대가 먹은 골 = 내 골
        rows.append(SideRow(d.get("matchId") or "", res,
                            {"half": float(sum(_first_half(g[0]) for g in mine_in)), "all": float(len(mine_in))},
                            {"half": float(sum(_first_half(g[0]) for g in opp_in)), "all": float(len(opp_in))}))
    return paired_diff(SideTotals(rows), HALF_SHARE)


# ── N17 골 흐름 ──────────────────────────────────────────────────────────────

MAX_LEAD = 5     # 최대 리드 5골 이상은 한 줄("5+")
GOAL_KINDS = ("앞서는", "동점", "추격", "쐐기", "벌리기")


@dataclass
class LeadRow(RateMixin):
    """최대 리드가 lead 골이던 경기들의 결과(5 는 5골 이상)."""
    lead: int
    win: int = 0
    draw: int = 0
    lose: int = 0
    mixed: bool | None = None

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


@dataclass
class GoalFlow:
    leads: list[LeadRow] = field(default_factory=list)       # 리드 1~5
    never_led: int = 0           # 한 번도 앞선 적 없는 경기(결과 있는 것 중)
    kinds: dict = field(default_factory=lambda: {k: 0 for k in GOAL_KINDS})   # 내 골의 성격 — 합 = 되짚은 내 골 수
    trail_goals: int = 0         # 내가 지고 있던 동안 나온 골 수
    trail_mine: int = 0          #   그중 내가 넣은 것
    lead_goals: int = 0          # 내가 앞서 있던 동안 나온 골 수
    lead_theirs: int = 0         #   그중 상대가 넣은 것(= 지는 쪽이 다음 골을 넣은 것)
    gap_bins: list[int] = field(default_factory=list)        # 골 사이 시간 — config.N17_GAP_EDGES 로 가른 칸별 수(칸 수 = 모서리 + 1)
    gap_median: float | None = None                          # 골 사이 시간 중앙값(분)
    replayed: int = 0            # 되짚은 경기 수
    unreplayable: int = 0        # 슛 기록 골 수가 점수판과 달라 못 되짚은 경기(자책골 등 — clutch_summary 와 같은 기준)
    held: int = 0                # 양쪽이 같은 시각에 넣은 골이 있어 순서를 못 정한 경기
    mixed: bool | None = None


def _minute_of(period: int, sec: int) -> float | None:
    """정규시간 분(전반은 44.99 로 가둔다 — goal_minute_buckets 와 같다). 연장은 None."""
    if period == 0:
        return min(sec / 60, 44.99)
    if period == 1:
        return 45 + sec / 60
    return None


def goal_flow(details: list[dict], ouid: str, mixed: bool | None = None) -> GoalFlow:
    """경기마다 골을 시간순으로 되짚어 리드·골의 성격·골 사이 시간을 센다(N17).

    되짚기는 `clutch_summary` 와 같은 기준(자책골 등 점수판과 안 맞는 경기는 뺀다). 양쪽 골이 정확히 같은 시각이면
    그 경기는 순서를 못 정해 뺀다. 연장 골은 시간 분포에서 빼고 리드·성격에는 넣는다."""
    gf = GoalFlow()
    _set_mixed([gf], (draw_possible_of(d, ouid) for d in details), mixed)
    rows = {k: LeadRow(k, mixed=gf.mixed) for k in range(1, MAX_LEAD + 1)}
    gaps: list[float] = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None or not opp:
            continue
        res = _wdl_of(_result_of(me))
        if res is None:
            continue
        if not (_timeline_matches_board(me) and _timeline_matches_board(opp)):
            gf.unreplayable += 1
            continue
        mine, theirs = _goal_events(me), _goal_events(opp)
        if set(mine) & set(theirs):
            gf.held += 1
            continue
        events = sorted([(g, True) for g in mine] + [(g, False) for g in theirs])
        gf.replayed += 1
        diff = peak = 0
        prev_min = None
        for g, is_mine in events:
            if diff < 0:
                gf.trail_goals += 1
                gf.trail_mine += is_mine
            elif diff > 0:
                gf.lead_goals += 1
                gf.lead_theirs += not is_mine
            if is_mine:
                kind = ("앞서는" if diff == 0 else "동점" if diff == -1 else "추격" if diff <= -2
                        else "쐐기" if diff == 1 else "벌리기")
                gf.kinds[kind] += 1
            diff += 1 if is_mine else -1
            peak = max(peak, diff)
            minute = _minute_of(*g)
            if minute is not None and prev_min is not None:
                gaps.append(minute - prev_min)
            prev_min = minute
        if peak == 0:
            gf.never_led += 1
        else:
            row = rows[min(peak, MAX_LEAD)]
            if res == 0:
                row.win += 1
            elif res == 1:
                row.draw += 1
            else:
                row.lose += 1
    gf.leads = [rows[k] for k in range(1, MAX_LEAD + 1)]
    edges = config.N17_GAP_EDGES
    gf.gap_bins = [0] * (len(edges) + 1)
    for g in gaps:
        gf.gap_bins[sum(g >= e for e in edges)] += 1
    if gaps:
        s = sorted(gaps)
        mid = len(s) // 2
        gf.gap_median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
    return gf


def pct(part: int | float, total: int | float) -> float:
    """part 이 total 의 몇 %인가 — 분모 0 이면 0.0. 화면(app_main)의 `* 100` 규칙 검사 밖에서 센다(승률이 아닌 비율)."""
    return part / total * 100 if total else 0.0


GOAL_KIND_NOTE = {
    "앞서는": "동점에서 앞서가는 골",
    "동점": "한 골 뒤진 채 따라붙는 골",
    "추격": "두 골 이상 뒤진 채 넣는 골",
    "쐐기": "한 골 앞선 채 달아나는 골",
    "벌리기": "두 골 이상 앞선 채 더 넣는 골",
}
