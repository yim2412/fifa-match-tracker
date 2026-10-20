"""내 경기 습관·기준선 계산(2.6.1 — docs/DONE.md "2.6.1(조각 ①) · 2.7.1" 절). 화면 없음 · 순수 계산 · 화면은 core_api 로.

지금 있는 것:
| 함수 | 화면 | 메모 |
|---|---|---|
| `division_stays(points, name_of)` | [성적 진단 › 등급 체류](N2) | 재료는 `stats.division_trend` — (일시, division) 오래된 것부터 |
| `side_totals(details, ouid)` | (N4 기준선 — N19 칩 · A1 · N17 이 쓴다) | 나 / 같은 경기 상대의 경기마다 값 · 승·무·패 경기만 |
| `paired_diff(rows, metric)` | 〃 | 지표 하나의 나 − 상대 차와 95% 구간(경기 단위 짝) · 지표 이름은 `METRICS` |
| `pos_diff(detail, ouid)` · `PosTotals` | (C4 자리별 수준 — 50단계 · 구단주 분포 바퀴가 `pos_json` 으로) | 칸(`config.C4_CELLS`)마다 같은 줄 선수 한 명 평균의 나 − 상대 |
| `pos_level(pos_dist, details_all, ouid)` | [성적 진단 › 자리별 수준](C4) | 내 최신 1000판 칸별 지수 → 분포 백분위 · 5단계 또는 보류 — 실측 0.06~0.08초라 화면 스레드 + 캐시(`app_main._render_poslevel` · `_dist_basis` 와 같은 꼴) |
| `strength_chips(totals)` | [흐름 분석] 맨 위 칩 줄(N19) | 결정력·유효슛 비율·태클 성공 중 구간이 0 을 안 품고 크기 문턱을 넘은 것 |
| `half_share_diff(details, ouid)` | 흐름 분석 문장 "전반·후반 어디서 새나"(N19) | 내 실점의 전반 비중 − 같은 경기 상대 실점(= 내 득점)의 전반 비중 |
| `goal_flow(details, ouid)` | [승부처 분석 › 리드·골 흐름](N17) | 최대 리드별 결과 · 골의 성격 · 지고 있을 때 다음 골 · 골 사이 시간 |
| `daily_luck(periods, mixed)` | [성적 진단 › 운 범위](N16) | 하루 승률이 평균 ± 2σ(이항) 띠 밖인 날 수 대 운만이면 기대되는 날 수 · "몇 판부터" 표 |
| `streak_baseline(matches)` | 〃 (A3) | 최장 연승·연패 대 `draw_possible` 층 안에서 순서를 섞은 기준(97.5분위) — 느려서 작업 스레드 |
| `habit_tables(matches)` | [성적 진단 › 경기 습관](N20) | 간격·세션 순번·시간대별 승률 + 표마다 카이제곱(층 보정) |
| `club_day(wins, day)` · `day_delta(points, day)` · `first_reach(wins, start, division)` | 대시보드 맨 위 "이번 시즌 구단"(55 H1·H5) | 재료는 `stats.win_divisions` — 화면 글은 `app_main.club_cells` |
| `score_report(points, marks)` · `decisive_rate(wins, since)` | [승률 그래프 › 점수·예측] 리포트(55 R1~R3) | 평균은 하루 한 점 · 최고점·낙폭은 기록 전부 · 무승부 뺀 승률은 이 해설만(U1) |
| `calendar_days(wins, first, last, season_key, mixed)` · `cal_summary(wins)` | [승률 그래프 › 일별 기록](55 D1) | 화살표 = 그날 시즌 승률 ± `luck_band`(N16 과 같은 식) · 하한 `D1_MIN_GAMES` |
| `classify_route(shot)` · `attack_routes(details, ouid)` | [슛 맵 › 공격 루트](N18) | 슛을 `ROUTES` 8갈래로(순서가 곧 우선순위) · 내 슛과 같은 경기 상대 슛에 같은 분류기 · 루트별 결정력 차이는 `paired_diff` |

기준선의 구간(N4 · docs/DONE.md 41단계 "구간 방법"): 나와 상대는 **같은 경기의 짝**이고 슛은 경기 안에서 뭉친다 — 두 비율을
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
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config
from models import MatchSummary, PeriodRate, RateMixin, draw_possible_of, is_mixed, is_weak, rated, win_pct
from stats import (GOAL_TYPES, SHOT_GOAL, _SHOT_RESULTS, WinDivision, _goal_events, _me_opp, _num, _played, _result_of,
                   _set_mixed, _timeline_matches_board, _wdl_of, formation_of, position_line, win_rewards)


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


# ── C4 자리 줄 재료(50단계) — 칸은 config.C4_CELLS(측정 C 를 통과한 것만) ───────────────
def c4_cell(line: str, side: str) -> str:
    return f"{line}.{side}"


def _line_means(side: dict) -> dict[str, list[float]]:
    """한 팀의 자리 줄별 **선수 한 명 평균**(줄 합계가 아니다 — 공격수 1명 대 2명 포메이션에 안 흔들리게 · 계획 C4 ⓐ).
    지표는 C4_METRICS 의 공격 다음 수비 순서. 교체(SUB)·GK 는 줄이 없어 빠진다."""
    keys = config.C4_METRICS["atk"] + config.C4_METRICS["def"]
    acc: dict[str, list[list[float]]] = {}
    for p in side.get("player") or []:
        if not isinstance(p.get("spId"), int) or not _played(p):
            continue
        line = position_line(p.get("spPosition"))
        if line in (None, "GK"):
            continue
        st = p.get("status") or {}
        acc.setdefault(line, []).append([_num(st, k) for k in keys])
    return {L: [sum(c) / len(v) for c in zip(*v)] for L, v in acc.items()}


def pos_diff(d: dict, ouid: str) -> dict[str, list[float]] | None:
    """경기 하나 → {칸: [지표마다 나 − 같은 경기 상대(같은 줄 선수 한 명 평균)]}. side_totals 와 같은 경기만(승·무·패 · 상대 있음),
    아니면 None. 그 줄 선수가 어느 한쪽에 없으면 그 칸만 빠진다 — 몰수·튕김처럼 자리 데이터가 빈 경기는 실패가 아니다
    (6회차 Y1 · 측정 C 에서 0.16%). 예외는 그대로 올린다(부르는 쪽이 v2 실패로 센다)."""
    me, opp = _me_opp(d, ouid)
    if me is None or not opp or _wdl_of(_result_of(me)) is None:
        return None
    keys = config.C4_METRICS["atk"] + config.C4_METRICS["def"]
    m, o = _line_means(me), _line_means(opp)
    out = {}
    for line, side in config.C4_CELLS:
        if line in m and line in o:
            idx = [keys.index(k) for k in config.C4_METRICS[side]]
            out[c4_cell(line, side)] = [m[line][i] - o[line][i] for i in idx]
    return out


@dataclass
class PosTotals:
    """구단주 한 명의 칸별 경기 값 합 — 바퀴는 이 평균(pos_json)만 저장한다."""
    n: dict = field(default_factory=dict)
    s: dict = field(default_factory=dict)

    def add(self, pd: dict) -> None:
        for cell, v in pd.items():
            self.n[cell] = self.n.get(cell, 0) + 1
            acc = self.s.setdefault(cell, [0.0] * len(v))
            for i, x in enumerate(v):
                acc[i] += x

    def summary(self) -> dict:
        """{칸: {"n": 경기 수, "d": [지표 평균]}} — 경기가 없는 칸은 빠진다."""
        return {c: {"n": n, "d": [x / n for x in self.s[c]]} for c, n in self.n.items() if n}


@dataclass(frozen=True)
class PosCell:
    """C4 한 칸의 내 판정. why: "" 판정 남 · games(그 줄 출전 C4_MIN_LINE_GAMES 미만) · range(± C4_Z se 가 두 단계에 걸침)
    · dist(그 칸 분포가 층 하한 미달). pct·lo·hi 는 games 여도 n ≥ 2 면 낸다(화면이 숫자는 보여 준다)."""
    line: str
    side: str
    n: int
    pct: float | None = None
    lo: float | None = None
    hi: float | None = None
    stage: int | None = None     # 0~4 = config.C4_STAGES · 보류면 None
    why: str = ""


@dataclass(frozen=True)
class PosLevel:
    games: int                   # 쓴 내 감독모드 경기(최신 OWNER_DIST_MATCHES 판까지)
    cells: tuple                 # PosCell — config.C4_CELLS 순서


def c4_stage(p: float) -> int:
    return sum(p > c for c in config.C4_CUTS)


def pos_level(pos_dist, details_all: list[dict], ouid: str) -> PosLevel:
    """C4 자리별 수준(50단계 ③) — 내 감독모드 최신 OWNER_DIST_MATCHES 판(49 칩과 같은 창 · 계획 ⓕ)의 칸별 지수를 분포에 댄다.
    pos_dist 는 ownerdist.PosDist(cells[칸].index·pct 만 쓴다 — 순환 import 를 피해 형만 맞춘다). 측정 C 판정기와 같은 식:
    경기마다 지수(그 경기 나 − 상대 값을 분포의 μ·σ 로 z 평균) → 평균이 내 지수 · se = 경기 지수 SD/√n(해석적 구간 · 5판 V5).
    보류는 엄격하게 — ± C4_Z se 양 끝의 단계가 다르면(경계 하나라도 걸치면) 보류."""
    mine = [d for d in details_all if d.get("matchType") == config.DEFAULT_MATCH_TYPE][:config.OWNER_DIST_MATCHES]
    per: dict[str, list[list[float]]] = {c4_cell(L, s): [] for L, s in config.C4_CELLS}
    for d in mine:
        pd = pos_diff(d, ouid)
        for cell, v in (pd or {}).items():
            per[cell].append(v)
    cells = []
    for line, side in config.C4_CELLS:
        vs = per[c4_cell(line, side)]
        cd = pos_dist.cells.get(c4_cell(line, side))
        if cd is None:
            cells.append(PosCell(line, side, len(vs), why="dist"))
            continue
        if len(vs) < 2:
            cells.append(PosCell(line, side, len(vs), why="games"))
            continue
        xs = [cd.index(v) for v in vs]
        x = sum(xs) / len(xs)
        se = math.sqrt(sum((a - x) ** 2 for a in xs) / (len(xs) - 1) / len(xs))
        p, lo, hi = cd.pct(x), cd.pct(x - config.C4_Z * se), cd.pct(x + config.C4_Z * se)
        if len(vs) < config.C4_MIN_LINE_GAMES:
            why, stage = "games", None
        elif c4_stage(lo) != c4_stage(hi):
            why, stage = "range", None
        else:
            why, stage = "", c4_stage(p)
        cells.append(PosCell(line, side, len(vs), p, lo, hi, stage, why))
    return PosLevel(len(mine), tuple(cells))


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
    """강점·약점 칩 하나 — 나 − 같은 경기 상대가 뚜렷한 지표(basis "pair"), 또는 그 diff 가 구단주 분포의
    양 끝인 지표(basis "dist" — pct 가 내 백분위)."""
    diff: Diff
    pct: float | None = None

    @property
    def basis(self) -> str:
        return "pair" if self.pct is None else "dist"

    @property
    def label(self) -> str:
        return self.diff.metric.label

    @property
    def strong(self) -> bool:
        return self.diff.diff > 0 if self.pct is None else self.pct >= 50

    @property
    def text(self) -> str:
        if self.pct is None:
            return f"{self.label} {self.diff.diff:+.1f}%p"
        top = 100 - self.pct if self.strong else self.pct
        return f"{self.label} {'상위' if self.strong else '하위'} {max(1, round(top))}%"


# 구단주 분포(49단계)로 판정하는 칩 — 나머지 칩(유효슛 비율)은 분포에서 뺀 지표라 늘 같은 경기 상대 대비(ROADMAP 49단계)
DIST_CHIP_KEYS = ("finish", "tackle_rate")


@dataclass(frozen=True)
class DistBasis:
    """분포 판정 재료 — dist 는 ownerdist.OwnerDist(pct(key, x) 만 쓴다 · 순환 import 를 피해 형만 맞춘다),
    my 는 내 감독모드 최신 OWNER_DIST_MATCHES 판의 side_totals(분포와 같은 판수 규모 — 화면 범위와 무관)."""
    dist: object
    my: SideTotals


def dist_basis(dist, details_all: list[dict], ouid: str) -> DistBasis | None:
    """details_all 은 최신순 전체(시즌 범위 아님). 분포가 없거나 감독모드가 OWNER_DIST_MATCHES 판 미만이면 None(41단계)."""
    if dist is None or not ouid:
        return None
    mine = [d for d in details_all if d.get("matchType") == config.DEFAULT_MATCH_TYPE][:config.OWNER_DIST_MATCHES]
    if len(mine) < config.OWNER_DIST_MATCHES:
        return None
    return DistBasis(dist, side_totals(mine, ouid))


def dist_side(dist, key: str, d: Diff) -> tuple[int, float | None]:
    """분포 판정 — (−1 하위 칩 · 1 상위 칩 · 0 없음, 내 백분위). 백분위가 양 끝(OWNER_DIST_CHIP_P)이고 ±1.96se 의
    안쪽 끝도 OWNER_DIST_SURE_P 를 안 넘을 때만(새 표본 검증 규칙 그대로). 그 지표 분포를 못 쓰면 (0, None)."""
    p = dist.pct(key, d.diff)
    if p is None:
        return 0, None
    if p <= config.OWNER_DIST_CHIP_P and dist.pct(key, d.hi) <= config.OWNER_DIST_SURE_P:
        return -1, p
    if p >= 100 - config.OWNER_DIST_CHIP_P and dist.pct(key, d.lo) >= 100 - config.OWNER_DIST_SURE_P:
        return 1, p
    return 0, p


def dist_judged(basis: DistBasis | None) -> tuple[str, ...]:
    """strength_chips 가 분포로 판정하는 칩 키 — 층 표본이 모자라 분포에서 빠진 지표(pct None)는 같은 경기 상대로 돌아간다.
    화면의 "칩 없음" 문구가 판정과 같은 조건을 쓰게 여기 하나로(pct 가 None 인지는 x 와 무관하다)."""
    if basis is None:
        return ()
    return tuple(k for k in DIST_CHIP_KEYS if basis.dist.pct(k, 0.0) is not None)


def strength_chips(totals: SideTotals, basis: DistBasis | None = None) -> list[Chip]:
    """칩 목록 — 분포 칩 먼저(|백분위 − 50| 큰 순), 이어서 같은 경기 상대 칩(차이가 큰 순). 단위가 다른 둘을 한 정렬에 섞지 않는다.

    basis 가 있고 그 지표 분포를 쓸 수 있으면 DIST_CHIP_KEYS 는 분포로만 판정한다(같은 경기 상대 판정으로 되돌아가지 않는다).
    같은 경기 상대 판정: 분모(슛·태클 시도)가 하한 밑이면 그 칩은 없다 — 표본이 모자라 구간 자체를 믿기 어렵다."""
    dist_out, out = [], []
    by_dist = dist_judged(basis)
    for key, floor_name in N19_CHIP_KEYS:
        if key in by_dist:
            d = paired_diff(basis.my, key)
            side, p = dist_side(basis.dist, key, d)
            if side:
                dist_out.append(Chip(d, p))
            continue
        d = paired_diff(totals, key)
        floor = getattr(config, floor_name)
        if min(d.mine_den, d.opp_den) < floor:
            continue
        if d.clear(config.N19_MIN_GAP):
            out.append(Chip(d))
    dist_out.sort(key=lambda c: -abs(c.pct - 50))
    out.sort(key=lambda c: -abs(c.diff.diff))
    return dist_out + out


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


# ── N18 공격 루트 ────────────────────────────────────────────────────────────

# 표 순서 = 분류 순서(위에서 먼저 걸리는 것이 이긴다). 어시 없음 바로 앞이 중앙 연계, 이름 없는 종류는 맨 끝 "기타".
ROUTES = ("세트피스", "헤더", "중거리", "크로스", "컷백", "중앙 연계", "어시 없음", "기타")
_SET_PIECE_TYPES = (8, 9)        # 프리킥 · 페널티킥(stats.GOAL_TYPES)
_HEADER_TYPE = 3
_NO_ASSIST_XY = (0.5, 0.5)       # 어시 없는 슛의 좌표 자리값(R13 — assist 거짓이어도 이 값이 온다)


def classify_route(sd: dict) -> str:
    """슛(shootDetail 한 칸) 하나의 공격 루트. 순서: 이름 없는 종류 → 세트피스 → 헤더 → 중거리(박스 밖) → [어시 없음] → 크로스 → 컷백 → 중앙 연계.

    이름 없는 종류(R12 — 약 3%)는 헤더·세트피스로 추측하지 않고 "기타"로 따로 둔다.
    크로스·컷백은 박스 안 슛만(박스 밖은 위에서 중거리로 끝났다). 컷백 = 어시가 박스 폭 밖 · 골라인 쪽이고 슛보다 골라인에 가까울 때."""
    t = sd.get("type")
    if t not in GOAL_TYPES:
        return "기타"
    if t in _SET_PIECE_TYPES:
        return "세트피스"
    if t == _HEADER_TYPE:
        return "헤더"
    if not sd.get("inPenalty"):
        return "중거리"
    ax, ay = sd.get("assistX"), sd.get("assistY")
    if (not sd.get("assist") or not isinstance(ax, (int, float)) or not isinstance(ay, (int, float))
            or (ax, ay) == _NO_ASSIST_XY):
        return "어시 없음"
    if abs(ay - 0.5) < config.N18_WIDE_Y:
        return "중앙 연계"
    x = sd.get("x")
    if ax >= config.N18_BYLINE_X and isinstance(x, (int, float)) and ax > x:
        return "컷백"
    return "크로스"


@dataclass
class RouteRow:
    """루트 하나 — 내 슛과 같은 경기 상대의 같은 루트 슛."""
    route: str
    mine_shots: int
    mine_goals: int
    opp_shots: int
    opp_goals: int
    diff: Diff                   # 결정력(골÷슛 %) 나 − 상대 — 경기 단위 짝 구간

    @property
    def mine_finish(self) -> float:
        return pct(self.mine_goals, self.mine_shots)

    @property
    def opp_finish(self) -> float:
        return pct(self.opp_goals, self.opp_shots)


@dataclass
class AttackRoutes:
    rows: list[RouteRow] = field(default_factory=list)   # ROUTES 순서
    mine_total: int = 0
    opp_total: int = 0
    games: int = 0
    enough: bool = False         # 내 슛이 config.N18_MIN_SHOTS 이상인가 — 아니면 표 대신 "아직 못 가릅니다"


ROUTE_FINISH = Metric("route_finish", "루트 결정력", "goals", "shots")


def _route_counts(p: dict) -> dict:
    """한 쪽의 루트별 (슛, 골). 좌표·result 가 비정상인 슛은 슛 맵(`stats.shot_map`)과 같은 기준으로 뺀다."""
    out = {r: [0, 0] for r in ROUTES}
    for sd in p.get("shootDetail") or []:
        if (not isinstance(sd.get("x"), (int, float)) or not isinstance(sd.get("y"), (int, float))
                or sd.get("result") not in _SHOT_RESULTS):
            continue
        c = out[classify_route(sd)]
        c[0] += 1
        c[1] += sd.get("result") == SHOT_GOAL
    return out


def attack_routes(details: list[dict], ouid: str) -> AttackRoutes:
    """내 슛과 같은 경기 상대 슛을 같은 분류기로 루트에 나눈다(N18). 승·무·패가 아닌 경기와 상대 없는 경기는 뺀다(`side_totals` 와 같은 기준)."""
    per_route = {r: [] for r in ROUTES}
    out = AttackRoutes()
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None or not opp:
            continue
        res = _wdl_of(_result_of(me))
        if res is None:
            continue
        out.games += 1
        mc, oc = _route_counts(me), _route_counts(opp)
        mid = d.get("matchId") or ""
        for r in ROUTES:
            per_route[r].append(SideRow(mid, res, {"shots": float(mc[r][0]), "goals": float(mc[r][1])},
                                        {"shots": float(oc[r][0]), "goals": float(oc[r][1])}))
    for r in ROUTES:
        tot = SideTotals(per_route[r])
        ms, mg = int(tot.total("mine", "shots")), int(tot.total("mine", "goals"))
        os_, og = int(tot.total("opp", "shots")), int(tot.total("opp", "goals"))
        out.rows.append(RouteRow(r, ms, mg, os_, og, paired_diff(tot, ROUTE_FINISH)))
        out.mine_total += ms
        out.opp_total += os_
    out.enough = out.mine_total >= config.N18_MIN_SHOTS
    return out


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


# ── N16 하루 승률 운 범위 ───────────────────────────────────────────────────
@dataclass
class DayLuck:
    """하루 치 — 승률(A6 식)이 평균의 띠 안인가."""
    day: object
    win: int
    draw: int
    lose: int
    n: int                  # 그날 승률 분모(rated)
    rate: float             # 그날 승률(%)
    lo: float               # 띠(%) — 그날 판수 n 으로
    hi: float
    weak: bool              # n < N16_MIN_DAY — 점만 흐리고 띠 밖 날 수에서 뺀다
    outside: bool           # 띠 밖(weak 이면 늘 False)


@dataclass
class RequiredRow:
    diff: float             # 두 계정 승률 차(%p)
    n: int                  # 각 계정 승률 분모(rated)
    games: int              # 실제 경기 수 — 섞인 묶음이면 n ÷ (1 − 이 계정 무승부 비율)


@dataclass
class Luck:
    days: list[DayLuck] = field(default_factory=list)
    mixed: bool = False
    p: float = 0.0                  # 기간 전체 승률(0~1) — 띠의 가운데
    judged: int = 0                 # 판정한 날(weak 아님)
    outside: int = 0                # 띠 밖인 날
    expected: float = 0.0           # 운만이면 띠 밖일 날 수(날마다 이항 정확히)
    draw_ratio: float = 0.0         # 이 계정 무승부 / 전체
    required: list[RequiredRow] = field(default_factory=list)


def _binom_outside_prob(n: int, p: float, lo: float, hi: float) -> float:
    """n 판 중 승 수 k 의 비율 k/n 이 띠 [lo, hi] 밖일 확률(이항 정확히). lo·hi 는 비율(0~1)."""
    if n <= 0:
        return 0.0
    if p <= 0.0 or p >= 1.0:
        k = round(p * n)
        return 0.0 if lo <= k / n <= hi else 1.0
    lg = math.lgamma
    base = lg(n + 1)
    lp, lq = math.log(p), math.log(1 - p)
    tot = 0.0
    for k in range(n + 1):
        r = k / n
        if r < lo - 1e-12 or r > hi + 1e-12:
            tot += math.exp(base - lg(k + 1) - lg(n - k + 1) + k * lp + (n - k) * lq)
    return tot


def luck_band(p: float, n: int) -> tuple[float, float]:
    """n 판 승률의 우연 띠(비율 0~1) — p ± `N16_BAND_SIGMA`·√(p(1−p)/n). 운 범위(N16)·달력 화살표(D1)가 같은 식을 쓴다."""
    half = config.N16_BAND_SIGMA * math.sqrt(p * (1 - p) / n) if n else 0.0
    return max(0.0, p - half), min(1.0, p + half)


def daily_luck(periods: list[PeriodRate], mixed: bool | None = None) -> Luck:
    """하루 승률이 같은 기간 평균 ± `N16_BAND_SIGMA`σ 띠 밖인가. periods 는 `models.win_rate_trend` 결과(그 기간 하루씩)
    — 묶음은 그래프 전체 기간이라 mixed 도 거기서 온다. 하루 `N16_MIN_DAY` 판(rated) 미만은 점만 흐리게 두고 세지 않는다."""
    out = Luck()
    if not periods:
        return out
    out.mixed = periods[0].mixed if mixed is None else mixed
    w = sum(p.win for p in periods)
    d = sum(p.draw for p in periods)
    l = sum(p.lose for p in periods)
    rated_all = rated(w, d, l, out.mixed)
    out.p = w / rated_all if rated_all else 0.0
    total = w + d + l
    out.draw_ratio = d / total if total else 0.0
    sig = config.N16_BAND_SIGMA
    for p in periods:
        n = rated(p.win, p.draw, p.lose, out.mixed)
        lo, hi = luck_band(out.p, n)
        weak = is_weak(n, config.N16_MIN_DAY)
        rate = win_pct(p.win, p.draw, p.lose, out.mixed)
        outside = (not weak) and not (lo * 100 - 1e-9 <= rate <= hi * 100 + 1e-9)
        out.days.append(DayLuck(p.day, p.win, p.draw, p.lose, n, rate, lo * 100, hi * 100, weak, outside))
        if not weak:
            out.judged += 1
            out.outside += outside
            out.expected += _binom_outside_prob(n, out.p, lo, hi)
    pq = out.p * (1 - out.p)
    for diff in config.N16_REQUIRED_DIFFS:
        n = math.ceil(2 * sig * sig * pq / ((diff / 100) ** 2)) if pq else 0
        games = math.ceil(n / (1 - out.draw_ratio)) if out.mixed and out.draw_ratio < 1 else n
        out.required.append(RequiredRow(diff, n, games))
    return out


# ── A3 연승·연패 우연 기준 ──────────────────────────────────────────────────
@dataclass
class StreakBaseline:
    games: int = 0
    win_run: int = 0                # 실제 최장 연승
    lose_run: int = 0
    win_cut: int = 0                # 섞은 순서의 A3_QUANTILE 분위(이보다 길면 우연 밖)
    lose_cut: int = 0
    win_median: int = 0
    lose_median: int = 0
    win_longer: int = 0             # 섞은 N번 중 실제보다 긴 횟수
    lose_longer: int = 0
    shuffles: int = 0
    win_outside: bool = False
    lose_outside: bool = False


def _result_value(result: str) -> str:
    """승/무/패 — models.longest_streaks 와 같은 기준(몰수승/패도 승패). 그 밖(오류)은 빈 글자 = 연속을 끊는다."""
    return "승" if "승" in result else ("패" if "패" in result else ("무" if "무" in result else ""))


def _runs(vals: list[str]) -> tuple[int, int]:
    """(최장 연승, 최장 연패) — 무승부·오류는 연속을 끊는다."""
    best_w = best_l = run = 0
    prev = ""
    for v in vals:
        if v in ("승", "패"):
            run = run + 1 if v == prev else 1
        else:
            run = 0
        prev = v
        if v == "승":
            best_w = max(best_w, run)
        elif v == "패":
            best_l = max(best_l, run)
    return best_w, best_l


def shuffle_within_strata(vals: list[str], strata: dict, rng: random.Random) -> list[str]:
    """층(draw_possible 참·거짓)마다 그 층 자리끼리만 결과를 섞는다. 층에 안 든 자리(None)는 제자리."""
    cur = list(vals)
    for idx in strata.values():
        pool = [vals[i] for i in idx]
        rng.shuffle(pool)
        for i, v in zip(idx, pool):
            cur[i] = v
    return cur


def streak_key(matches: list[MatchSummary]) -> tuple:
    """캐시 키 — 같은 경기 목록(순서·결과·층)이면 같다."""
    return (len(matches), hash(tuple((m.match_id, m.result, m.draw_possible) for m in matches)))


def streak_baseline(matches: list[MatchSummary], shuffles: int | None = None, seed: int | None = None,
                    cancelled=None) -> StreakBaseline:
    """실제 최장 연승·연패 대 순서를 섞은 기준. **섞기는 draw_possible 층 안에서만**(통째로 섞으면 무승부가 무승부 불가
    챔스 경기 자리에 들어가 연속을 인위로 끊어 기준이 낮아진다). 층이 None 인 경기는 제자리. 시드 고정."""
    n_shuf = config.A3_SHUFFLES if shuffles is None else shuffles
    rng = random.Random(config.A3_SEED if seed is None else seed)
    dated = sorted((m for m in matches if m.match_date is not None), key=lambda m: m.match_date)
    vals = [_result_value(m.result) for m in dated]
    out = StreakBaseline(games=len(dated), shuffles=n_shuf)
    out.win_run, out.lose_run = _runs(vals)
    strata: dict = {}
    for i, m in enumerate(dated):
        if m.draw_possible is not None:
            strata.setdefault(m.draw_possible, []).append(i)
    wins: list[int] = []
    loses: list[int] = []
    for _ in range(n_shuf):
        if cancelled is not None and cancelled():
            return out
        bw, bl = _runs(shuffle_within_strata(vals, strata, rng))
        wins.append(bw)
        loses.append(bl)
    if not wins:
        return out
    wins.sort()
    loses.sort()
    cut = max(0, min(len(wins) - 1, math.ceil(config.A3_QUANTILE * len(wins)) - 1))
    out.win_cut, out.lose_cut = wins[cut], loses[cut]
    out.win_median, out.lose_median = wins[len(wins) // 2], loses[len(loses) // 2]
    out.win_longer = sum(1 for x in wins if x > out.win_run)
    out.lose_longer = sum(1 for x in loses if x > out.lose_run)
    out.win_outside = out.win_run > out.win_cut
    out.lose_outside = out.lose_run > out.lose_cut
    return out


# ── N20 경기 습관 — 표마다 카이제곱 ─────────────────────────────────────────
def chi2_sf(x: float, df: int) -> float:
    """카이제곱 분포의 상위 꼬리 확률 P(X ≥ x) — 정규화 상위 불완전 감마 Q(df/2, x/2). scipy 없이."""
    if df <= 0 or x <= 0:
        return 1.0
    a, z = df / 2.0, x / 2.0
    gln = math.lgamma(a)
    if z < a + 1:                      # 급수로 P 를 구해 1 − P
        ap, term = a, 1.0 / a
        tot = term
        for _ in range(1000):
            ap += 1
            term *= z / ap
            tot += term
            if abs(term) < abs(tot) * 1e-15:
                break
        return max(0.0, min(1.0, 1.0 - tot * math.exp(-z + a * math.log(z) - gln)))
    tiny = 1e-300                      # 연분수(Lentz)로 Q
    b = z + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return max(0.0, min(1.0, math.exp(-z + a * math.log(z) - gln) * h))


@dataclass
class HabitBin:
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    possible: int = 0          # draw_possible 참인 경기 수 — "무승부 가능 경기 중 N%" 의 분모
    possible_draw: int = 0     # 그중 무승부
    group: int = 0             # 카이제곱에서 합쳐진 칸 번호(같으면 한 칸)
    flag: int = 0              # 강조: +1 평균보다 잘 이김 · −1 못 이김 · 0 없음

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


@dataclass
class HabitTable:
    name: str
    bins: list[HabitBin] = field(default_factory=list)
    mixed: bool = False
    games: int = 0
    stat: float = 0.0
    df: int = 0
    p: float = 1.0
    tested: bool = False       # 검정할 만큼 표본이 있었나
    significant: bool = False  # p < N20_ALPHA
    dropped_strata: int = 0    # 한 층에 기대 빈도 5 미만 칸이 남아 뺀 층 수
    note: str = ""


def _bin_index(value: float, edges) -> int:
    return sum(value >= e for e in edges)


def _chi_table(name: str, labels: list[str], rows: list[tuple[int, MatchSummary]], mixed: bool) -> HabitTable:
    """rows = (칸 번호, 경기). 결과 두 갈래는 보이는 승률과 같은 양 — 섞임이면 승 대 패(무 뺌), 한 집단이면 승 대 (무+패)."""
    t = HabitTable(name, [HabitBin(lb) for lb in labels], mixed)
    cells: dict = {}      # (층, 칸) → [승, 그 밖]
    for b, m in rows:
        hb = t.bins[b]
        v = _result_value(m.result)
        if v == "승":
            hb.win += 1
        elif v == "무":
            hb.draw += 1
        elif v == "패":
            hb.lose += 1
        else:
            continue
        t.games += 1
        if m.draw_possible:
            hb.possible += 1
            hb.possible_draw += v == "무"
        if m.draw_possible is None:
            continue                       # 층을 모르는 경기는 검정에서 뺀다(승·무·패 셈에는 들어간다)
        if mixed and v == "무":
            continue
        c = cells.setdefault((bool(m.draw_possible), b), [0, 0])
        c[0 if v == "승" else 1] += 1
    k = len(labels)
    # ① 칸 합치기 — 층을 합친 표 전체 기대 빈도로 먼저 고정(화면 칸과 검정 칸이 같다)
    tot = [[sum(cells.get((s, b), [0, 0])[c] for s in (True, False)) for c in (0, 1)] for b in range(k)]
    grand_w = sum(x[0] for x in tot)
    grand_o = sum(x[1] for x in tot)
    grand = grand_w + grand_o
    groups = [[b] for b in range(k)]

    def small(g: list[int]) -> bool:
        n_w = sum(tot[b][0] for b in g)
        n_o = sum(tot[b][1] for b in g)
        n = n_w + n_o
        if not grand:
            return True
        return min(n * grand_w / grand, n * grand_o / grand) < config.N20_MIN_EXPECTED

    while len(groups) > 1:
        idx = next((i for i, g in enumerate(groups) if small(g)), None)
        if idx is None:
            break
        if idx == 0:
            j = 1
        elif idx == len(groups) - 1:
            j = idx - 1
        else:   # 이웃 둘 중 판 수가 적은 쪽과 합친다
            left = sum(sum(tot[b]) for b in groups[idx - 1])
            right = sum(sum(tot[b]) for b in groups[idx + 1])
            j = idx - 1 if left <= right else idx + 1
        lo, hi = sorted((idx, j))
        groups[lo:hi + 1] = [groups[lo] + groups[hi]]
    for gi, g in enumerate(groups):
        for b in g:
            t.bins[b].group = gi
    if len(groups) < 2 or grand_w == 0 or grand_o == 0:
        t.note = "표본이 모자라 판정하지 않습니다"
        return t
    # ② 층마다 카이제곱 — 기대 빈도 5 미만 칸이 남는 층은 뺀다
    stat, df = 0.0, 0
    exp_w = [0.0] * len(groups)
    obs_w = [0.0] * len(groups)
    for s in (True, False):
        gw = [sum(cells.get((s, b), [0, 0])[0] for b in g) for g in groups]
        go = [sum(cells.get((s, b), [0, 0])[1] for b in g) for g in groups]
        sw, so = sum(gw), sum(go)
        sn = sw + so
        if sn == 0:
            continue
        if sw == 0 or so == 0:
            t.dropped_strata += 1
            continue
        ew = [(a + b) * sw / sn for a, b in zip(gw, go)]
        eo = [(a + b) * so / sn for a, b in zip(gw, go)]
        if min(ew + eo) < config.N20_MIN_EXPECTED:
            t.dropped_strata += 1
            continue
        stat += sum((a - e) ** 2 / e for a, e in zip(gw, ew)) + sum((a - e) ** 2 / e for a, e in zip(go, eo))
        df += len(groups) - 1
        for i in range(len(groups)):
            exp_w[i] += ew[i]
            obs_w[i] += gw[i]
    if df == 0:
        t.note = "표본이 모자라 판정하지 않습니다"
        return t
    t.tested, t.stat, t.df = True, stat, df
    t.p = chi2_sf(stat, df)
    t.significant = t.p < config.N20_ALPHA
    if t.significant:
        for gi in range(len(groups)):
            if exp_w[gi] <= 0:      # 방어 — 쓰는 층은 칸마다 기대 빈도 5 이상이라 올 일이 없다(변이 검증: 동치)
                continue
            z = (obs_w[gi] - exp_w[gi]) / math.sqrt(exp_w[gi])
            flag = 1 if z > config.N20_RESID_Z else (-1 if z < -config.N20_RESID_Z else 0)
            for b in groups[gi]:
                t.bins[b].flag = flag
    return t


def habit_tables(matches: list[MatchSummary]) -> list[HabitTable]:
    """경기 습관 표 셋 — 직전 경기와의 간격 · 세션 안 몇 번째 판 · 시간대. 날짜 없는 경기는 뺀다.
    세션 = `SESSION_GAP_MIN` 분 넘게 쉬면 끊음. 간격은 '앞 경기 시작 → 이 경기 시작'이라 앞 경기가 연장까지 갔으면 길게 잡힌다."""
    dated = sorted((m for m in matches if m.match_date is not None), key=lambda m: m.match_date)
    gap_rows, ses_rows, hour_rows = [], [], []
    ordinal = 0
    prev = None
    for m in dated:
        gap_min = None if prev is None else (m.match_date - prev).total_seconds() / 60
        ordinal = 1 if gap_min is None or gap_min > config.SESSION_GAP_MIN else ordinal + 1
        prev = m.match_date
        if gap_min is not None:
            gap_rows.append((_bin_index(gap_min - 1e-9, config.N20_GAP_EDGES), m))   # 칸은 (a, b] — 13분 딱 맞으면 '13분 이하'
        ses_rows.append((_bin_index(ordinal, config.N20_SESSION_EDGES), m))
        hour_rows.append((m.match_date.hour // config.N20_HOUR_STEP, m))
    ge, se = config.N20_GAP_EDGES, config.N20_SESSION_EDGES
    gap_labels = [f"{ge[0]}분 이하(바로 이어서)"] + [f"{lo}~{hi}분" for lo, hi in zip(ge, ge[1:])] + [f"{ge[-1]}분 넘게"]
    ses_labels = ["첫 판"] + [f"{lo}번째" if hi - lo == 1 else f"{lo}~{hi - 1}번째"
                              for lo, hi in zip(se, se[1:])] + [f"{se[-1]}번째~"]
    hs = config.N20_HOUR_STEP
    hour_labels = [f"{h:02d}~{h + hs:02d}시" for h in range(0, 24, hs)]
    out = []
    for name, labels, rows in (("직전 경기와의 간격", gap_labels, gap_rows),
                               ("세션 안 몇 번째 판", ses_labels, ses_rows),
                               ("시간대", hour_labels, hour_rows)):
        mixed = is_mixed(m.draw_possible for _, m in rows)
        out.append(_chi_table(name, labels, rows, mixed))
    return out


# ── 55 구단 머리 줄(H1·H5) · 점수 리포트(R1~R3) — ROADMAP 55단계 ─────────────────────────────────────────
# 재료는 stats.win_divisions(경기마다 일시·그때 내 등급·결과)와 ELO 하루 점(app_main.elo_daily_points 의 (날짜, 점수)).
# 승률은 앱 규칙(승÷전체 · win_pct) — R3 의 "무승부 뺀 승률" 만 따로(U1 — 점수는 승·패로만 움직인다).

@dataclass
class DaySummary:
    """하루 요약(H1) — 그날 경기 승·무·패 · 승리 보상 FC."""
    day: date
    win: int = 0
    draw: int = 0
    lose: int = 0
    fc: int = 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    def rate(self, mixed: bool) -> float:
        return win_pct(self.win, self.draw, self.lose, mixed)


def club_day(rows: list[WinDivision], day: date) -> DaySummary:
    """그날(이 PC 날짜 0~24시 · J7) 경기 — 승·무·패와 FC(stats.win_rewards 와 같은 보상표)."""
    sel = [w for w in rows if w.when.date() == day]
    out = DaySummary(day, *(sum(1 for w in sel if w.outcome == k) for k in ("승", "무", "패")))
    out.fc = win_rewards(sel).fc
    return out


def day_delta(points: list[tuple[date, float]], day: date) -> float | None:
    """그날 마지막 점수 − 그 앞 마지막 점수(전날이 없으면 더 앞 · J7). 그날 점이나 앞 점이 없으면 None."""
    cur = next((e for d, e in points if d == day), None)
    before = [e for d, e in points if d < day]
    if cur is None or not before:
        return None
    return cur - before[-1]


@dataclass
class FirstReach:
    """시즌 첫 경기부터 그 등급 첫 경기까지 친 판수(H5 — 첫 경기 포함 · 1회차 [중])."""
    games: int
    at: datetime
    from_start: bool        # 저장 경기가 시즌 시작 N12_START_DAYS 안부터 있나(아니면 "저장 시작 뒤부터")
    gap: bool               # 그 사이 저장 경기가 H5_GAP_DAYS 넘게 빈 곳이 있나(덜 셌을 수 있다 · 3회차 [중])


def first_reach(rows: list[WinDivision], season_start: datetime, division: int) -> FirstReach | None:
    """division 이하(그 등급 이상 — id 가 작을수록 상위)를 처음 찍은 경기까지의 승·무·패 수. 못 찍었으면 None."""
    seq = sorted((w for w in rows if w.when >= season_start), key=lambda w: w.when)
    hit = next((i for i, w in enumerate(seq) if w.division is not None and w.division <= division), None)
    if hit is None:
        return None
    span = seq[:hit + 1]
    gap = timedelta(days=config.H5_GAP_DAYS)
    return FirstReach(hit + 1, seq[hit].when,
                      seq[0].when <= season_start + timedelta(days=config.N12_START_DAYS),
                      any(b.when - a.when > gap for a, b in zip(span, span[1:])))


@dataclass
class ScoreReport:
    """지금 시즌 점수 리포트(R1~R3) — 하루 한 점 기준(하루 안 오르내림은 안 보인다)."""
    points: int
    first_day: date
    avg: float              # R1 시즌 평균 점수(하루 점 평균)
    now: float
    peak: float             # 시즌 최고점(하루 점 중)
    peak_day: date
    max_dd: float           # R2 최고점 뒤 가장 깊은 하락(양수)
    dd_day: date | None     # 그 바닥 날
    day_avg: float          # R3 하루 평균 변화(첫 점 → 지금 ÷ 날 수)
    up_days: int            # 앞 점보다 오른 점 수
    steps: int              # 비교한 점 수(점 − 1)


def score_report(points: list[tuple[date, float]], marks: list[tuple[date, float]] | None = None) -> ScoreReport | None:
    """points 는 (날짜, 그날 마지막 점수) 오래된 것부터 — 평균·하루 변화(R1·R3)는 하루 한 점으로. 최고점·낙폭(R2)은 marks
    (그 시즌 기록 전부 · 시각순)로 — 하루 마지막 점만 보면 하루 안 최고점을 놓쳐 머리 줄의 시즌 최고점과 어긋난다(실측 4,549 대 4,461).
    marks 가 없으면 points. R_MIN_POINTS 미만이면 None(침묵)."""
    if len(points) < config.R_MIN_POINTS:
        return None
    vals = [e for _, e in points]
    marks = marks or points
    run_peak, dd, dd_day = marks[0][1], 0.0, None
    for d, e in marks:
        run_peak = max(run_peak, e)
        if run_peak - e > dd:
            dd, dd_day = run_peak - e, d
    peak = max(e for _, e in marks)
    days = (points[-1][0] - points[0][0]).days
    steps = list(zip(vals, vals[1:]))
    return ScoreReport(len(points), points[0][0], sum(vals) / len(vals), vals[-1], peak,
                       next(d for d, e in marks if e == peak), dd, dd_day,
                       (vals[-1] - vals[0]) / days if days else 0.0,
                       sum(1 for a, b in steps if b > a), len(steps))


def decisive_rate(rows: list[WinDivision], since: datetime) -> tuple[float, int] | None:
    """무승부 뺀 승률(%)과 승+패 수 — R3 해설만(U1). 승·패가 없으면 None."""
    w = sum(1 for r in rows if r.when >= since and r.outcome == "승")
    lose = sum(1 for r in rows if r.when >= since and r.outcome == "패")
    return (w / (w + lose) * 100, w + lose) if w + lose else None


# ── 55 D1 일별 기록 달력 ─────────────────────────────────────────────────────
# 칸마다 그날 승률·FC · 화살표 = 그날이 속한 시즌의 승률 ± N16 과 같은 띠(luck_band) 밖. 판수 하한만 다르다(D1_MIN_GAMES · U3).

@dataclass
class CalDay:
    """달력 한 칸 — 경기가 있던 날만."""
    day: date
    win: int
    draw: int
    lose: int
    fc: int
    n: int                  # 승률 분모(rated)
    rate: float             # 그날 승률(%)
    base: float             # 그날이 속한 시즌의 승률(%) — 띠의 가운데
    lo: float               # 띠(%)
    hi: float
    judged: bool            # n ≥ D1_MIN_GAMES — 아니면 화살표 없음
    arrow: int              # +1 띠 위 · −1 띠 아래 · 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


@dataclass
class CalSummary:
    """여러 날 묶음(이 달 · 이 시즌) — 경기가 있던 날 기준 하루 평균."""
    days: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    fc: int = 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    @property
    def per_day(self) -> float:
        return self.games / self.days if self.days else 0.0

    @property
    def fc_per_day(self) -> float:
        return self.fc / self.days if self.days else 0.0

    def rate(self, mixed: bool) -> float:
        return win_pct(self.win, self.draw, self.lose, mixed)


def _wdl(rows: list[WinDivision]) -> tuple[int, int, int]:
    return tuple(sum(1 for w in rows if w.outcome == k) for k in ("승", "무", "패"))


def calendar_days(rows: list[WinDivision], first: date, last: date, season_key, mixed: bool) -> list[CalDay]:
    """first~last(둘 다 포함) 중 경기가 있던 날 — 날짜순. season_key(date) 는 그날이 속한 시즌을 가리는 값(같으면 같은 시즌)
    · 기준선은 그 시즌의 rows 전부로 센다(달 안에 시즌 경계가 있으면 날마다 기준선이 다르다 · 1회차 [중])."""
    keys: dict[date, object] = {}     # season_key 는 날짜마다 한 번만(시즌표 훑기라 경기마다 부르면 1만 경기 167ms — 실측)
    by_season: dict = {}
    for w in rows:
        d = w.when.date()
        if d not in keys:
            keys[d] = season_key(d)
        by_season.setdefault(keys[d], []).append(w)
    base_of = {}
    for key, ws in by_season.items():
        wn, dn, ln = _wdl(ws)
        n = rated(wn, dn, ln, mixed)
        base_of[key] = wn / n if n else 0.0
    by_day: dict[date, list[WinDivision]] = {}
    for w in rows:
        d = w.when.date()
        if first <= d <= last:
            by_day.setdefault(d, []).append(w)
    out = []
    for d in sorted(by_day):
        ws = by_day[d]
        wn, dn, ln = _wdl(ws)
        n = rated(wn, dn, ln, mixed)
        p = base_of[keys[d]]
        lo, hi = luck_band(p, n)
        rate = win_pct(wn, dn, ln, mixed)
        judged = not is_weak(n, config.D1_MIN_GAMES)
        arrow = 0
        if judged:
            arrow = 1 if rate > hi * 100 + 1e-9 else (-1 if rate < lo * 100 - 1e-9 else 0)
        out.append(CalDay(d, wn, dn, ln, win_rewards(ws).fc, n, rate, p * 100, lo * 100, hi * 100, judged, arrow))
    return out


def cal_summary(rows: list[WinDivision]) -> CalSummary:
    """rows(이미 고른 구간) 의 경기 있던 날 수·승·무·패·FC."""
    wn, dn, ln = _wdl(rows)
    return CalSummary(len({w.when.date() for w in rows}), wn, dn, ln, win_rewards(rows).fc)


# ── 57 K4 이력 — 닉네임·포메이션이 바뀐 구간 ──────────────────────────────────────────────

@dataclass
class Run:
    kind: str                       # "nickname" · "formation"
    value: str
    start: datetime                 # 이 값으로 처음·마지막 경기 시각
    end: datetime
    games: int                      # 이 값으로 뛴 경기(붙인 깜빡임은 빼고)
    blips: list = field(default_factory=list)   # [(값, 경기 수)] — K4_MIN_RUN 미만으로 잠깐 바뀌었다 돌아온 것


def history_runs(details: list[dict], ouid: str, kinds: tuple = ("nickname", "formation")) -> list[Run]:
    """내 경기 기록(matchInfo 의 내 줄)에서 값이 바뀐 구간 — 종류별로 오래된 것부터. 같은 값이 K4_MIN_RUN 경기 미만이면
    앞 구간에 붙이고(blips) 그 뒤 같은 값이 이어지면 한 구간으로 합친다. 맨 앞 구간은 붙일 곳이 없어 짧아도 그대로."""
    seq: list[tuple[datetime, str, str]] = []
    for d in details:
        me = next((p for p in d.get("matchInfo") or [] if p.get("ouid") == ouid), None)
        try:
            when = datetime.fromisoformat(d.get("matchDate") or "")
        except (TypeError, ValueError):
            continue
        if me is None:
            continue
        seq.append((when, (me.get("nickname") or "").strip(), formation_of(me.get("player") or [])))
    seq.sort(key=lambda t: t[0])
    out: list[Run] = []
    for kind in kinds:
        raw: list[Run] = []
        for when, nick, form in seq:
            value = nick if kind == "nickname" else form
            if not value or (kind == "formation" and value == formation_of([])):
                continue
            if raw and raw[-1].value == value:
                raw[-1].end, raw[-1].games = when, raw[-1].games + 1
            else:
                raw.append(Run(kind, value, when, when, 1))
        runs: list[Run] = []
        for r in raw:
            if runs and r.games < config.K4_MIN_RUN:
                prev = runs[-1]
                prev.blips.append((r.value, r.games))
                prev.end = r.end
            elif runs and runs[-1].value == r.value:
                prev = runs[-1]
                prev.end, prev.games = r.end, prev.games + r.games
                prev.blips.extend(r.blips)
            else:
                runs.append(r)
        out.extend(runs)
    return out
