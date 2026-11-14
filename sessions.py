"""세션 · 경기를 돌린 시간 · 연패 끊는 패턴(97단계 조각 1 — docs/ROADMAP "## 97단계"). 화면 없음 · 순수 계산 · 화면은 core_api 로.

| 함수 | 화면 | 메모 |
|---|---|---|
| `split_sessions(matches)` | [흐름 › 플레이 기록 › 세션] | 세션 = `SESSION_GAP_MIN` 분 넘게 쉬거나 **KST 0시를 넘으면 끊는다** |
| `session_rows(sessions, mixed, points)` · `size_bins` · `session_luck` | 〃 | 줄(날짜·시각·판 수·승무패·승률·점수 변화) · 판 수 구간별 승률 · 운 범위 밖 세션 수 대 기대 |
| `play_time(sessions)` · `period_totals` | [플레이 기록 › 경기 시간] | "경기를 돌린 시간(추정)" — 앉아 있던 시간이 아니다 |
| `match_context(details, ouid)` · `streak_break(matches, ctx)` | [플레이 기록 › 연패] | 연패 뒤 다음 판을 끊음(승) 대 이어짐으로 — 조건 셋 고정 |
| `season_spans(seasons, now)` · `season_pace(…)` | [흐름 › 시즌 돌아보기] 맨 위 X1 줄·그래프 | 시즌 시작부터 경과 일수로 맞춘 지난 시즌 같은 시점과의 비교 |
| `season_review(…)` · `top_scorer(…)` | 〃 카드 열둘 | 고른 시즌 한 장 — 늘 저장된 경기에서 그때그때 계산(얼리지 않는다 · U7) |
| `streak_note(matches, ctx, now)` | 대시보드 머리 칸 한 줄(Y1) | 지금 `Y1_MIN_LOSS` 연패 이상이고 마지막 경기가 `Y1_RECENT_H` 시간 안일 때만 |

규칙(근거 ROADMAP 97 F1·F2·U6):
- 감독모드는 켜 두면 혼자 돌아 30분 끊기만으로는 126시간짜리 세션이 나온다(실측) — 그래서 0시에서도 끊는다.
- **경기를 돌린 시간** = 세션마다 (마지막 판 시작 − 첫 판 시작 + 한 판 길이). 한 판 길이는 그 계정 세션 안 간격의 중앙값
  (간격이 `Y2_MIN_GAPS` 개 미만이면 `Y2_GAME_MIN_FALLBACK`). 세션이 0시에서 끊기므로 하루 합은 24시간을 못 넘는다(0시를 넘겨
  끝나는 마지막 한 판 길이만큼은 넘을 수 있어 `DAY_CAP_MIN` 으로 자른다).
- **연패 뒤 다음 판**: 연패는 세션·날짜와 무관하게 이어 센다(F2 와 같다). 무승부·오류는 연패를 끊는다.
  끊음 = 승 / 이어짐 = 무·패 — 승률 규칙과 같은 두 갈래(`habits._chi_table`). 표 셋 = 검정 셋이라 문턱은 `N20_ALPHA / N1_TESTS`.
  연패가 다음 판을 망친다는 근거는 이 자료에 없었다(F2) — 그래서 이 모듈은 좋음·나쁨을 말하지 않고 표만 만든다.
- 승률은 이 모듈이 클래스를 만들지 않는다 — `rate` 는 부르는 쪽이 정한 `mixed` 로 `win_pct` 를 한 번 낸 값이다
  (`habits.HabitBin` 꼴 · RATE_CLASSES 에 안 넣는 이유).
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config
import habits
import records
from analysis import MIN_COND
from models import MatchSummary, drop_ids, is_mixed, is_weak, longest_streaks, rated, win_pct
from stats import _me_opp, _num, _played, formation_of, win_rewards

DAY_CAP_MIN = 24 * 60
_NO_FORMATION = formation_of([])   # 선발을 못 읽은 경기(몰수 등)의 표기 — 포메이션을 모르는 것으로 본다


def _wdl(ms: list[MatchSummary]) -> tuple[int, int, int]:
    w = d = l = 0
    for m in ms:
        v = habits._result_value(m.result)
        w += v == "승"
        d += v == "무"
        l += v == "패"
    return w, d, l


# ── Y3 세션 ─────────────────────────────────────────────────────────────────
@dataclass
class Session:
    matches: list[MatchSummary] = field(default_factory=list)   # 시각 순
    ongoing: bool = False   # 마지막 세션만 — 아직 이어지는 중일 수 있다(경기는 1~2시간마다·검색할 때 받아 온다)

    @property
    def start(self) -> datetime:
        return self.matches[0].match_date

    @property
    def end(self) -> datetime:
        return self.matches[-1].match_date

    @property
    def day(self) -> date:
        return self.start.date()

    @property
    def games(self) -> int:
        return len(self.matches)

    @property
    def wdl(self) -> tuple[int, int, int]:
        return _wdl(self.matches)


def _game_length(sessions: list[Session]) -> tuple[float, bool, int]:
    """(한 판 길이 분, 기본값을 썼나, 간격 수) — 세션 안 간격의 중앙값. 간격이 `Y2_MIN_GAPS` 개 미만이면 기본값."""
    gaps = [(b.match_date - a.match_date).total_seconds() / 60
            for s in sessions for a, b in zip(s.matches, s.matches[1:])]
    if len(gaps) >= config.Y2_MIN_GAPS:
        return statistics.median(gaps), False, len(gaps)
    return float(config.Y2_GAME_MIN_FALLBACK), True, len(gaps)


def split_sessions(matches: list[MatchSummary], now: datetime | None = None) -> list[Session]:
    """오래된 세션부터. 앞 판 시작과의 간격이 `SESSION_GAP_MIN` 분을 넘거나(`habits.habit_tables` 와 같은 '넘게') 날짜(KST)가
    바뀌면 새 세션. 날짜 없는 경기는 뺀다.
    now 를 주면 마지막 세션의 `ongoing` 을 정한다: 마지막 판 시작에서 now 까지가 `SESSION_GAP_MIN` + 한 판 길이 이내이고
    같은 날이면 참(날이 바뀌었으면 이어 해도 새 세션이라 아니다)."""
    dated = sorted((m for m in matches if m.match_date is not None), key=lambda m: m.match_date)
    out: list[Session] = []
    prev: datetime | None = None
    for m in dated:
        if (prev is None or (m.match_date - prev).total_seconds() / 60 > config.SESSION_GAP_MIN
                or m.match_date.date() != prev.date()):
            out.append(Session())
        out[-1].matches.append(m)
        prev = m.match_date
    if now is not None and out:
        game_min = _game_length(out)[0]
        last = out[-1].end
        out[-1].ongoing = (now.date() == last.date()
                           and (now - last).total_seconds() / 60 <= config.SESSION_GAP_MIN + game_min)
    return out


@dataclass
class SessionRow:
    start: datetime
    end: datetime
    games: int
    win: int
    draw: int
    lose: int
    rate: float                    # win_pct(%) — 부르는 쪽이 정한 mixed
    delta: float | None = None     # 그날 점수 변화 — 그날 세션이 하나뿐이고 점수 점이 있을 때만
    ongoing: bool = False          # 아직 이어지는 중일 수 있다(판 수가 덜 셌다)


def session_rows(sessions: list[Session], mixed: bool, points: list[tuple[date, float]] | None = None) -> list[SessionRow]:
    """세션마다 한 줄(오래된 것부터). points = ELO 하루 점 (날짜, 점수) — 하루 점은 세션을 못 가르므로
    그날 세션이 **하나뿐일 때만** `habits.day_delta` 를 싣는다(둘 이상이면 어느 세션 몫인지 모른다)."""
    per_day: dict[date, int] = {}
    for s in sessions:
        per_day[s.day] = per_day.get(s.day, 0) + 1
    out = []
    for s in sessions:
        w, d, l = s.wdl
        delta = habits.day_delta(points, s.day) if points and per_day[s.day] == 1 else None
        out.append(SessionRow(s.start, s.end, s.games, w, d, l, win_pct(w, d, l, mixed), delta, s.ongoing))
    return out


@dataclass
class SizeBin:
    label: str
    sessions: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0


def size_bins(sessions: list[Session]) -> list[SizeBin]:
    """세션 판 수 구간(`SESSION_SIZE_EDGES`)별 — 그 구간 세션들에서 친 모든 판의 승무패. 이어지는 중인 세션은 뺀다(판 수가 덜 셌다)."""
    se = config.SESSION_SIZE_EDGES
    labels = [f"1~{se[0] - 1}판"] + [f"{lo}~{hi - 1}판" for lo, hi in zip(se, se[1:])] + [f"{se[-1]}판 넘게"]
    out = [SizeBin(lb) for lb in labels]
    for s in sessions:
        if s.ongoing:
            continue
        b = out[sum(s.games >= e for e in se)]
        w, d, l = s.wdl
        b.sessions += 1
        b.win, b.draw, b.lose = b.win + w, b.draw + d, b.lose + l
    return out


@dataclass
class SessionLuck:
    judged: int = 0          # 판정한 세션 수(승률 분모가 `D1_MIN_GAMES` 이상)
    outside: int = 0         # 그중 승률이 운 범위 띠 밖
    expected: float = 0.0    # 운만이면 띠 밖일 세션 수(세션마다 이항 정확히 더함)
    p: float = 0.0           # 범위 전체 승률(0~1) — 띠의 가운데


def session_luck(sessions: list[Session], mixed: bool) -> SessionLuck:
    """세션 승률이 운 범위(`habits.luck_band` — N16·달력과 같은 식) 밖인 세션 수 대 운만일 때의 기대. 띠 가운데는 범위 전체 승률."""
    out = SessionLuck()
    sessions = [s for s in sessions if not s.ongoing]   # 이어지는 중인 세션은 판 수가 덜 셌다
    tw = td = tl = 0
    for s in sessions:
        w, d, l = s.wdl
        tw, td, tl = tw + w, td + d, tl + l
    n_all = (tw + tl) if mixed else (tw + td + tl)
    if not n_all:
        return out
    out.p = tw / n_all
    for s in sessions:
        w, d, l = s.wdl
        n = (w + l) if mixed else (w + d + l)
        if n < config.D1_MIN_GAMES:
            continue
        lo, hi = habits.luck_band(out.p, n)
        rate = w / n
        out.judged += 1
        out.outside += rate > hi + 1e-9 or rate < lo - 1e-9
        out.expected += habits._binom_outside_prob(n, out.p, lo, hi)
    return out


# ── Y2 경기를 돌린 시간(추정) ───────────────────────────────────────────────
@dataclass
class PlayTime:
    game_min: float = float(config.Y2_GAME_MIN_FALLBACK)   # 한 판 길이(분)
    estimated: bool = True        # 참이면 간격이 모자라 기본값을 썼다
    gaps: int = 0                 # 중앙값을 낸 세션 안 간격 수
    days: dict = field(default_factory=dict)   # 날 → 분(24시간 상한)

    @property
    def total_min(self) -> float:
        return sum(self.days.values())

    @property
    def average_day_min(self) -> float:
        """경기가 있던 날의 하루 평균(분)."""
        return self.total_min / len(self.days) if self.days else 0.0

    @property
    def longest_day(self) -> tuple[date, float] | None:
        return max(self.days.items(), key=lambda kv: kv[1]) if self.days else None


def play_time(sessions: list[Session]) -> PlayTime:
    game_min, estimated, gaps = _game_length(sessions)
    out = PlayTime(game_min=game_min, estimated=estimated, gaps=gaps)
    for s in sessions:
        length = (s.end - s.start).total_seconds() / 60 + out.game_min
        out.days[s.day] = min(out.days.get(s.day, 0.0) + length, float(DAY_CAP_MIN))
    return out


def period_totals(pt: PlayTime, unit: str) -> list[tuple[date, float]]:
    """일·주·월 합(분) — 오래된 것부터. 주는 월요일, 월은 1일이 키. 경기가 없던 기간은 줄이 없다."""
    acc: dict[date, float] = {}
    for d, minutes in pt.days.items():
        key = d if unit == "일" else (d - timedelta(days=d.weekday()) if unit == "주" else d.replace(day=1))
        acc[key] = acc.get(key, 0.0) + minutes
    return sorted(acc.items())


# ── N1 연패 끊는 패턴 ───────────────────────────────────────────────────────
def match_context(details: list[dict], ouid: str) -> dict[str, tuple[int | None, str | None]]:
    """경기마다 (그 경기 때 내 등급, 내 포메이션) — {match_id: …}. 등급은 `stats.win_divisions` 와 같은 `matchInfo.division`
    (경기 당시 값 · 작을수록 높다) — WinDivision 은 match_id 가 없어 경기와 못 짝지어 같은 필드를 id 로 다시 읽는다.
    선발을 못 읽은 경기의 포메이션은 None."""
    out: dict[str, tuple[int | None, str | None]] = {}
    for d in details:
        me = next((p for p in d.get("matchInfo") or [] if p.get("ouid") == ouid), None)
        if me is None:
            continue
        div = me.get("division")
        form = formation_of(me.get("player") or [])
        out[d.get("matchId", "")] = (div if isinstance(div, int) else None, None if form == _NO_FORMATION else form)
    return out


DIVISION_LABELS = ("상대가 더 높음", "같은 등급", "상대가 더 낮음")
FORMATION_LABELS = ("같은 포메이션", "포메이션 바꿈")


@dataclass
class StreakBreak:
    min_loss: int = 0
    games: int = 0                              # 범위 전체 경기(승·무·패)
    base: tuple = (0, 0, 0)                     # 범위 전체 승·무·패
    after: tuple = (0, 0, 0)                    # 연패 뒤 다음 판 승·무·패
    mixed: bool = False
    alpha: float = 0.0                          # 표마다 유의 문턱
    tables: list = field(default_factory=list)  # HabitTable 셋 — 직전 간격 · 포메이션 · 상대 등급


def streak_break(matches: list[MatchSummary], ctx: dict, mixed: bool | None = None) -> StreakBreak:
    """`N1_MIN_LOSS` 연패 뒤 다음 판 — 맨 위 사실(연패 뒤 승률 대 범위 전체)과 조건 셋 표(직전 간격 · 포메이션 바꿈 · 상대 등급).
    ctx = `match_context`. 조건을 모르는 경기(포메이션·등급 없음)는 그 표에서만 빠진다."""
    dated = sorted((m for m in matches if m.match_date is not None), key=lambda m: m.match_date)
    flags = [m.draw_possible for m in dated]
    out = StreakBreak(min_loss=config.N1_MIN_LOSS, mixed=is_mixed(flags) if mixed is None else mixed,
                      alpha=config.N20_ALPHA / config.N1_TESTS)
    out.base = _wdl(dated)
    out.games = sum(out.base)
    gap_rows, form_rows, div_rows, targets = [], [], [], []
    run = 0
    prev: MatchSummary | None = None
    for m in dated:
        v = habits._result_value(m.result)
        if v and run >= config.N1_MIN_LOSS and prev is not None:
            targets.append(m)
            gap = (m.match_date - prev.match_date).total_seconds() / 60
            gap_rows.append((habits._bin_index(gap - 1e-9, config.N20_GAP_EDGES), m))
            pf, mf = ctx.get(prev.match_id, (None, None))[1], ctx.get(m.match_id, (None, None))[1]
            if pf is not None and mf is not None:
                form_rows.append((int(pf != mf), m))
            mine = ctx.get(m.match_id, (None, None))[0]
            if mine is not None and isinstance(m.opp_division, int):
                div_rows.append((0 if m.opp_division < mine else (1 if m.opp_division == mine else 2), m))
        run = run + 1 if v == "패" else 0
        prev = m
    out.after = _wdl(targets)
    for name, labels, rows in (("직전 경기와의 간격", habits.gap_labels(), gap_rows),
                               ("포메이션 바꿈", list(FORMATION_LABELS), form_rows),
                               ("상대 등급", list(DIVISION_LABELS), div_rows)):
        out.tables.append(habits._chi_table(name, labels, rows, is_mixed(m.draw_possible for _, m in rows), out.alpha))
    return out


# ── X1 지난 시즌 같은 시점 · F5 시즌 돌아보기 ───────────────────────────────
# 근거: ROADMAP 97 F3(시즌 길이 42~70일로 들쭉날쭉 · 진행 중 시즌은 끝 날을 모른다 → 진행률이 아니라 시작부터 경과 일수로 맞춘다 — U2)
@dataclass(frozen=True)
class SeasonSpan:
    label: str
    start: datetime            # `Season.start_at`(경계 시각 8시 — seasons.py 주의 2)
    end: datetime | None       # None = 진행 중(시즌표엔 진행 중 시즌이 없다 — 마지막 끝난 시즌의 경계가 시작)

    @property
    def ongoing(self) -> bool:
        return self.end is None

    @property
    def key(self) -> str:
        """`predict_final` 이 쓰는 시즌 시작 날짜(시즌표 start · iso) — 시각이 아니라 날짜."""
        return self.start.date().isoformat()


def season_spans(seasons, now: datetime) -> list[SeasonSpan]:
    """시즌표 → 시즌 구간(최신부터). 이미 시작한 시즌만 — 끝나지 않았으면 진행 중. 시즌표에 진행 중 시즌이 없으면
    마지막 끝난 시즌의 경계에서 시작하는 "지금 시즌" 을 하나 더한다. 시즌표가 비면 빈 목록(웹 데이터 꺼짐)."""
    out = [SeasonSpan(s.label, s.start_at, s.end_at if s.end_at <= now else None) for s in seasons if s.start_at <= now]
    if out and not any(sp.ongoing for sp in out):
        out.append(SeasonSpan("지금 시즌", max(s.end_at for s in seasons), None))
    out.sort(key=lambda sp: sp.start, reverse=True)
    return out


def stored_spans(spans: list[SeasonSpan], matches: list[MatchSummary]) -> list[SeasonSpan]:
    """저장된 경기가 하나라도 속한 시즌만(칩 목록 — 시즌표엔 2018년부터 89개가 있다). 순서는 그대로."""
    dates = sorted(m.match_date for m in matches if m.match_date is not None)
    return [sp for sp in spans
            if bisect_left(dates, sp.start) < (len(dates) if sp.end is None else bisect_left(dates, sp.end))]


def _in(ms: list[MatchSummary], lo: datetime, hi: datetime | None) -> list[MatchSummary]:
    return [m for m in ms if m.match_date is not None and lo <= m.match_date and (hi is None or m.match_date < hi)]


def _elo_marks(rows: list[dict] | None) -> list[tuple[datetime, float]]:
    """내 점수 기록 → (시각, 점수) 시각 순. 읽을 수 없는 줄은 뺀다."""
    out = []
    for r in rows or []:
        try:
            t = datetime.fromisoformat(r["taken_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(r.get("elo"), (int, float)):
            out.append((t, float(r["elo"])))
    out.sort(key=lambda x: x[0])
    return out


def _ctx_div(ctx: dict, m: MatchSummary) -> int | None:
    return ctx.get(m.match_id, (None, None))[0]


@dataclass
class PaceSide:
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    rate: float = 0.0
    best_div: int | None = None     # 그 구간에서 가장 높은 등급(id 가 가장 작은 것)
    elo: float | None = None        # 구간 안 마지막 점수 기록
    fc: int | None = None           # 승리 보상 합(보상이 나온 승리가 있을 때만)


def _side(ms: list[MatchSummary], wins: list, ctx: dict, marks: list, lo: datetime, hi: datetime | None, mixed: bool,
          exclude: frozenset = frozenset()) -> PaceSide:
    shown = drop_ids(ms, exclude)   # 승·무·패·판 수만 표시한 판을 뺀다 — 등급·점수·FC 는 넥슨 기록이라 그대로(109 J2)
    w, d, l = _wdl(shown)
    divs = [x for x in (_ctx_div(ctx, m) for m in ms) if x is not None]
    got = [v for t, v in marks if lo <= t and (hi is None or t < hi)]
    rw = win_rewards([x for x in wins if lo <= x.when and (hi is None or x.when < hi)])
    return PaceSide(len(shown), w, d, l, win_pct(w, d, l, mixed), min(divs) if divs else None,
                    got[-1] if got else None, rw.fc if rw.rewarded_wins else None)


def _cum_rates(ms: list[MatchSummary], start: datetime, days: int, mixed: bool) -> list[tuple[float | None, int]]:
    """날 1~days 끝까지 쌓은 (승률 | None(그때까지 판정할 경기 없음), 판 수)."""
    per = [[0, 0, 0] for _ in range(days)]
    for m in ms:
        k = int((m.match_date - start).total_seconds() // 86400)
        v = habits._result_value(m.result)
        if 0 <= k < days and v:
            per[k]["승무패".index(v)] += 1
    out, w, d, l = [], 0, 0, 0
    for a, b, c in per:
        w, d, l = w + a, d + b, l + c
        out.append((win_pct(w, d, l, mixed) if rated(w, d, l, mixed) else None, w + d + l))
    return out


@dataclass
class SeasonPace:
    span: SeasonSpan
    prev_span: SeasonSpan | None
    days: float                     # 경과 일수(진행 중이면 지금까지 · 끝난 시즌이면 전체 길이)
    mixed: bool
    cur: PaceSide
    prev: PaceSide | None = None
    prev_state: str = "ok"          # ok · none(지난 시즌이 시즌표에 없다) · empty(지난 시즌 경기가 없다) · late(저장이 시즌 일부만)
    prev_from: date | None = None   # late 일 때 지난 시즌 저장이 시작된 날
    prev_days: int | None = None    # 지난 시즌이 이 경과 일수보다 일찍 끝났으면 그 길이(일) — "비교 끝"
    curve: list = field(default_factory=list)   # [(날 k, 이 시즌 누적 승률 | None, 지난 시즌 누적 승률 | None)] — 지난 시즌 끝난 뒤는 None


def season_pace(matches: list[MatchSummary], wins: list, ctx: dict, elo_rows: list[dict] | None,
                spans: list[SeasonSpan], index: int, now: datetime, exclude: frozenset = frozenset()) -> SeasonPace:
    """(exclude = 109 J2 섞임 — 표시한 경기 번호. 승·무·패·판 수·승률 곡선에서만 빼고 등급·점수·FC·저장 범위 판정은 그대로.)
    spans[index] 의 시작부터 경과 d 일 대 바로 앞(이전) 시즌의 시작~d 일. 승률 식(mixed)은 두 구간을 합쳐 한 번 정한다.
    지난 시즌은 저장이 시작부터 있을 때만(첫 경기 ≤ 시작 + `N12_START_DAYS` — records ③ 과 같은 식) · d 가 지난 시즌 길이를 넘으면
    끝난 곳까지만 견준다(`prev_days`)."""
    span = spans[index]
    hi = span.end
    days = ((hi - span.start) if hi is not None else max(now - span.start, timedelta(0))).total_seconds() / 86400
    cur_ms = _in(matches, span.start, hi)
    prev_span = spans[index + 1] if index + 1 < len(spans) else None
    prev_all: list[MatchSummary] = []
    prev_ms: list[MatchSummary] = []
    cutoff = None
    if prev_span is not None:
        cutoff = prev_span.start + timedelta(days=days)
        if prev_span.end is not None and cutoff > prev_span.end:
            cutoff = prev_span.end
        prev_all = _in(matches, prev_span.start, prev_span.end)
        prev_ms = _in(prev_all, prev_span.start, cutoff)
    cur_f, prev_f = drop_ids(cur_ms, exclude), drop_ids(prev_ms, exclude)
    mixed = is_mixed(m.draw_possible for m in cur_f + prev_f)
    marks = _elo_marks(elo_rows)
    out = SeasonPace(span, prev_span, days, mixed, _side(cur_ms, wins, ctx, marks, span.start, hi, mixed, exclude))
    if prev_span is None:
        out.prev_state = "none"
        return out
    if not prev_all:
        out.prev_state = "empty"
        return out
    first = min(m.match_date for m in prev_all)
    if first > prev_span.start + timedelta(days=config.N12_START_DAYS):
        out.prev_state, out.prev_from = "late", first.date()
        return out
    out.prev = _side(prev_ms, wins, ctx, marks, prev_span.start, cutoff, mixed, exclude)
    if prev_span.end is not None and prev_span.start + timedelta(days=days) > prev_span.end:
        out.prev_days = round((prev_span.end - prev_span.start).total_seconds() / 86400)
    k_count = max(math.ceil(days), 1)
    cur_c = _cum_rates(cur_f, span.start, k_count, mixed)
    prev_c = _cum_rates(prev_f, prev_span.start, k_count, mixed)
    prev_len = (cutoff - prev_span.start).total_seconds() / 86400
    for k in range(k_count):
        out.curve.append((k + 1, cur_c[k][0], prev_c[k][0] if k < prev_len else None))
    return out


@dataclass
class SeasonReview:
    span: SeasonSpan
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    mixed: bool = False
    rate: float = 0.0
    best_div: int | None = None
    final_div: int | None = None            # 그 시즌 마지막 경기 때 내 등급
    peak_elo: float | None = None
    longest_win: int = 0
    formation: tuple[str, int] | None = None   # 가장 많이 쓴 포메이션 (이름, 판 수)
    scorer: tuple[int, int, int] | None = None  # 득점 1위 (spId, 골, 뛴 경기)
    final_rank: int | None = None
    achievements: int = 0
    play_min: float = 0.0
    rival: records.Rival | None = None
    from_day: date | None = None            # 저장이 시즌 시작 `N12_START_DAYS` 넘게 늦게 시작했으면 그 첫 경기 날짜


def top_scorer(details: list[dict], ouid: str, match_ids: set) -> tuple[int, int, int] | None:
    """match_ids 안 경기에서 내 선수 중 골 1위 (spId, 골, 뛴 경기) — 같으면 덜 뛴 쪽 · 골이 0 이면 None."""
    acc: dict[int, list[int]] = {}
    for d in details:
        if d.get("matchId") not in match_ids:
            continue
        me, _opp = _me_opp(d, ouid)
        if me is None:
            continue
        for p in me.get("player") or []:
            sp = p.get("spId")
            if not isinstance(sp, int) or not _played(p):
                continue
            a = acc.setdefault(sp, [0, 0])
            a[0] += int(_num(p.get("status") or {}, "goal"))
            a[1] += 1
    best = max(((g, -n, sp) for sp, (g, n) in acc.items()), default=None)
    return (best[2], best[0], -best[1]) if best and best[0] > 0 else None


def season_review(matches: list[MatchSummary], details: list[dict], ouid: str, ctx: dict, elo_rows: list[dict] | None,
                  span: SeasonSpan, finals: dict | None = None, ach_times: list[datetime] | None = None,
                  exclude: frozenset = frozenset()) -> SeasonReview:
    """(exclude = 109 J2 섞임 — 표시한 경기 번호. 판 수·승·무·패·연승·득점 1위·시간·라이벌에서만 빼고 등급·포메이션·점수·업적 수는 그대로.)
    고른 시즌 한 장. matches·details 는 저장 전체(시즌 필터와 무관) · 시즌 경계는 `Season.start_at`/`end_at`.
    finals = {시즌 시작 날짜 iso: 최종 순위}(`predict_final` 의 found 만) · ach_times = 업적 시각(records)."""
    ms = _in(matches, span.start, span.end)
    out = SeasonReview(span)
    shown = drop_ids(ms, exclude)
    out.games = len(shown)
    if not ms:
        return out
    out.win, out.draw, out.lose = _wdl(shown)
    out.mixed = is_mixed(m.draw_possible for m in shown)
    out.rate = win_pct(out.win, out.draw, out.lose, out.mixed)
    order = sorted(ms, key=lambda m: m.match_date)
    known = [d for d in (_ctx_div(ctx, m) for m in order) if d is not None]
    out.best_div = min(known) if known else None
    out.final_div = known[-1] if known else None
    marks = [v for t, v in _elo_marks(elo_rows) if span.start <= t and (span.end is None or t < span.end)]
    out.peak_elo = max(marks) if marks else None
    out.longest_win = longest_streaks(shown)[0]
    forms = Counter(f for f in (ctx.get(m.match_id, (None, None))[1] for m in ms) if f is not None)
    out.formation = forms.most_common(1)[0] if forms else None
    out.scorer = top_scorer(details, ouid, {m.match_id for m in shown})
    out.final_rank = (finals or {}).get(span.key)
    out.achievements = sum(span.start <= t and (span.end is None or t < span.end) for t in ach_times or [])
    out.play_min = play_time(split_sessions(shown)).total_min
    out.rival = records.rival(list(reversed(sorted(shown, key=lambda m: m.match_date))))
    if order[0].match_date > span.start + timedelta(days=config.N12_START_DAYS):
        out.from_day = order[0].match_date.date()
    return out


# ── Y1 연패 때 흐름 표시 ────────────────────────────────────────────────────
@dataclass
class StreakNote:
    run: int                       # 지금 연패 수
    min_loss: int                  # "이 연패 이상 뒤" 의 기준(`Y1_MIN_LOSS`)
    after: tuple = (0, 0, 0)       # 이 계정이 min_loss 연패 이상 뒤 치른 다음 판 승·무·패(저장 전체)
    base: tuple = (0, 0, 0)        # 저장 전체 승·무·패
    mixed: bool = False
    weak: bool = True              # 다음 판 표본이 모자라다(`is_weak(rated, MIN_COND)`) — 화면이 흐리게
    significant: bool = False      # [연패] 표 셋 중 하나라도 유의하다 — 화면이 이때만 흐리지 않게(E1: 경고색은 없다)


def streak_note(matches: list[MatchSummary], ctx: dict, now: datetime) -> StreakNote | None:
    """검색한 계정이 지금 `Y1_MIN_LOSS` 연패 이상이고 마지막 경기가 `Y1_RECENT_H` 시간 안이면 사실 한 줄의 재료, 아니면 None.
    범위는 저장 전체(시즌 필터와 무관) — 평소·연패 뒤 승률이 범위 따라 달라지면 한 줄이 계정 사실이 아니게 된다.
    연패는 `streak_break` 와 같이 센다(무승부·오류는 연패를 끊는다). ctx 는 dict 이거나 그것을 돌려주는 함수 — 조건을 통과했을 때만
    부른다(대시보드는 연패가 아닐 때가 대부분이라 전 경기 `match_context` 를 매번 짓지 않게)."""
    dated = sorted((m for m in matches if m.match_date is not None), key=lambda m: m.match_date)
    run = 0
    for m in reversed(dated):
        if habits._result_value(m.result) != "패":
            break
        run += 1
    if run < config.Y1_MIN_LOSS or (now - dated[-1].match_date).total_seconds() > config.Y1_RECENT_H * 3600:
        return None
    after, streak, prev = [], 0, None
    for m in dated:
        v = habits._result_value(m.result)
        if v and streak >= config.Y1_MIN_LOSS and prev is not None:
            after.append(m)
        streak = streak + 1 if v == "패" else 0
        prev = m
    mixed = is_mixed(m.draw_possible for m in dated)
    out = StreakNote(run, config.Y1_MIN_LOSS, _wdl(after), _wdl(dated), mixed)
    out.weak = is_weak(rated(*out.after, mixed), MIN_COND)
    out.significant = any(t.significant for t in streak_break(dated, ctx() if callable(ctx) else ctx, mixed).tables)
    return out
