"""매치 상세 JSON → 화면에 뿌릴 요약 자료구조.

승률 식(2.6.1 A6 — ROADMAP "A6 무승부 분모"): 무승부는 양쪽 다 챔피언스 이상인 경기에선 안 나온다(연장·승부차기 —
실측 0판). 그래서 승률을 내는 묶음(표 하나·그래프 하나·견주는 두 값은 합친 범위)에 무승부가 나는 경기와 안 나는 경기가
**섞이면** 승÷(승+패), 한쪽뿐이면 승÷전체. 식은 `win_pct` 하나 · 분모 `rated` · 표본 판정 `is_weak` · 섞임 `is_mixed`.
집계 줄의 `mixed` 는 기본 None 이고 None 이면 승률이 **오류**다 — 채우는 걸 잊은 길이 옛 식으로 조용히 남지 않게.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# division 메타(오픈API get_meta('division'))는 숫자가 작을수록 높은 등급이다.
#   800 슈퍼챔피언스 · 900 챔피언스 · 1000 슈퍼챌린지 · 1100~1300 챌린지1~3 · ...
# "챔피언스 이상" = 900 이하. stats 가 여기서 가져간다(models 는 잎 — stats 를 import 하지 않는다).
CHAMPION_DIVISION_ID = 900
SUPER_CHAMPION_DIVISION_ID = 800


def is_champion_or_above(division_id: int | None) -> bool:
    return division_id is not None and division_id <= CHAMPION_DIVISION_ID


def draw_possible(my_division, opp_division) -> bool | None:
    """그 경기가 무승부로 끝날 수 있었나 — 양쪽 다 챔스 이상이 아니면 참. 한쪽이라도 division 이 없으면 None(모름)."""
    if not isinstance(my_division, int) or not isinstance(opp_division, int):
        return None
    return not (is_champion_or_above(my_division) and is_champion_or_above(opp_division))


def draw_possible_of(detail: dict, ouid: str) -> bool | None:
    """경기 상세 하나의 draw_possible — 상대가 없으면(몰수·오류) None."""
    infos = detail.get("matchInfo") or []
    me = next((p for p in infos if p.get("ouid") == ouid), None)
    opp = next((p for p in infos if p.get("ouid") != ouid), None)
    if me is None or opp is None:
        return None
    return draw_possible(me.get("division"), opp.get("division"))


def is_mixed(flags) -> bool:
    """draw_possible 값들이 참·거짓 둘 다 있나. None 은 판정에서 뺀다 — division 없는 한 판이
    챔스 아래만 하는 계정의 식을 통째로 바꾸지 않게(검토 1회차 [상]). 판정할 값이 없으면 한 집단."""
    seen = {f for f in flags if f is not None}
    return len(seen) > 1


def mixed_of_details(details, ouid: str) -> bool:
    return is_mixed(draw_possible_of(d, ouid) for d in details)


def mixed_of_matches(matches) -> bool:
    return is_mixed(getattr(m, "draw_possible", None) for m in matches)


def rated(win: int, draw: int, lose: int, mixed: bool | None) -> int:
    """승률 분모 — 섞임이면 승+패, 한 집단이면 승+무+패. 표본 판정도 이 값으로 한다."""
    if mixed is None:
        raise ValueError("mixed 를 안 채운 승률 — 묶음에서 is_mixed 로 정해 넘겨야 한다")
    return win + lose if mixed else win + draw + lose


def win_pct(win: int, draw: int, lose: int, mixed: bool | None) -> float:
    """승률(%) — 앱의 내 경기 승률은 전부 이 한 식. 분모 0 이면 0.0."""
    n = rated(win, draw, lose, mixed)
    return win / n * 100 if n else 0.0


def is_weak(rated_n: int, min_n: int) -> bool:
    """승률 표본이 모자라나 — 흐림·침묵·거르기가 전부 이걸 부른다(분모는 rated)."""
    return rated_n < min_n


class RateMixin:
    """win·draw·lose·mixed 를 가진 집계 줄의 승률·분모. 이 믹스인을 쓰는 클래스는 RATE_CLASSES 에 있어야 한다."""

    @property
    def rated(self) -> int:
        return rated(self.win, self.draw, self.lose, self.mixed)

    @property
    def win_rate(self) -> float:
        return win_pct(self.win, self.draw, self.lose, self.mixed)


@dataclass
class MatchSummary:
    match_id: str
    match_date: datetime | None
    match_type: int
    my_nickname: str
    opponent: str
    result: str          # 승 / 무 / 패 / 몰수승 …
    my_goals: int
    opp_goals: int
    possession: int
    shoot_total: int
    shoot_effective: int
    pass_try: int
    pass_success: int
    rating: float
    my_shootout: int = 0
    opp_shootout: int = 0
    draw_possible: bool | None = None   # 무승부가 날 수 있던 경기인가(양쪽 division) — 저장 형식 아님

    @property
    def is_shootout(self) -> bool:
        """승부차기로 갈린 경기. 없었으면 양쪽 다 0으로 온다."""
        return bool(self.my_shootout or self.opp_shootout)

    @property
    def score(self) -> str:
        base = f"{self.my_goals} : {self.opp_goals}"
        if self.is_shootout:
            base += f" (승부차기 {self.my_shootout}:{self.opp_shootout})"
        return base

    @property
    def pass_rate(self) -> float:
        return (self.pass_success / self.pass_try * 100) if self.pass_try else 0.0

    @property
    def date_text(self) -> str:
        return self.match_date.strftime("%Y-%m-%d %H:%M") if self.match_date else "-"


def _i(d: dict, key: str) -> int:
    """숫자 필드를 안전하게 읽는다.

    넥슨은 값을 비울 때 키를 빼는 게 아니라 null로 준다(상대 탈주 등으로
    기록이 없는 경기에서 shoot 필드 전체가 null). .get(key, 0) 은 키가
    있으면 기본값이 안 먹어 None이 새어 나가고, 집계에서 터진다.
    """
    v = d.get(key)
    return int(v) if isinstance(v, (int, float)) else 0


def _f(d: dict, key: str) -> float:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_match(detail: dict, my_ouid: str) -> MatchSummary | None:
    """내 ouid 기준으로 한 경기를 요약한다. 내가 안 낀 경기면 None."""
    infos = detail.get("matchInfo") or []
    me = next((p for p in infos if p.get("ouid") == my_ouid), None)
    if me is None:
        return None
    opp = next((p for p in infos if p.get("ouid") != my_ouid), {})

    md = me.get("matchDetail") or {}
    shoot = me.get("shoot") or {}
    passes = me.get("pass") or {}
    opp_shoot = opp.get("shoot") or {}

    return MatchSummary(
        match_id=detail.get("matchId", ""),
        match_date=_parse_date(detail.get("matchDate", "")),
        match_type=detail.get("matchType", 0),
        my_nickname=me.get("nickname") or "-",
        opponent=opp.get("nickname") or "-",
        result=md.get("matchResult") or "-",
        my_goals=_i(shoot, "goalTotal"),
        opp_goals=_i(opp_shoot, "goalTotal"),
        possession=_i(md, "possession"),
        shoot_total=_i(shoot, "shootTotal"),
        shoot_effective=_i(shoot, "effectiveShootTotal"),
        pass_try=_i(passes, "passTry"),
        pass_success=_i(passes, "passSuccess"),
        rating=_f(md, "averageRating"),
        my_shootout=_i(shoot, "shootOutScore"),
        opp_shootout=_i(opp_shoot, "shootOutScore"),
        draw_possible=draw_possible(me.get("division"), opp.get("division")) if opp else None,
    )


@dataclass
class Stats(RateMixin):
    """조회한 경기들을 합친 통계."""
    total: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    possession_sum: int = 0
    rating_sum: float = 0.0
    mixed: bool | None = None

    @property
    def avg_goals_for(self) -> float:
        return (self.goals_for / self.total) if self.total else 0.0

    @property
    def avg_goals_against(self) -> float:
        return (self.goals_against / self.total) if self.total else 0.0

    @property
    def avg_possession(self) -> float:
        return (self.possession_sum / self.total) if self.total else 0.0

    @property
    def avg_rating(self) -> float:
        return (self.rating_sum / self.total) if self.total else 0.0


def summarize(matches: list[MatchSummary], mixed: bool | None = None) -> Stats:
    """mixed 를 주면 그 식으로(견주는 묶음이 합친 범위로 정한 값), 없으면 matches 로 정한다."""
    s = Stats(mixed=mixed_of_matches(matches) if mixed is None else mixed)
    for m in matches:
        # 몰수승/몰수패도 승패로 친다. "오류"(중단된 경기)처럼 승·무·패가
        # 아닌 결과는 아예 집계에서 뺀다 — 승률 분모는 물론 평균 득실·점유율·
        # 평점까지, 실제로 안 치러진 경기가 끌어내리지 않게.
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        else:
            continue
        s.total += 1
        s.goals_for += m.my_goals
        s.goals_against += m.opp_goals
        s.possession_sum += m.possession
        s.rating_sum += m.rating
    return s


@dataclass
class OpponentStat(RateMixin):
    """상대 구단주 한 명과의 상성 — 전적·평균 득실."""
    nickname: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    last_date: str = "-"
    mixed: bool | None = None

    @property
    def avg_goals_for(self) -> float:
        return (self.goals_for / self.games) if self.games else 0.0

    @property
    def avg_goals_against(self) -> float:
        return (self.goals_against / self.games) if self.games else 0.0


def opponent_stats(matches: list[MatchSummary], mixed: bool | None = None) -> list[OpponentStat]:
    """상대 닉네임별로 묶은 상성 — 많이 붙어본 순.

    matches 는 최신순으로 온다는 게 이미 전제라(화면 표시 순서 그대로),
    한 상대를 처음 만나는 순간(=가장 최근 경기)의 날짜를 last_date 로 쓴다.
    승률 식은 표 하나(matches 전체)로 한 번 정한다.
    """
    mixed = mixed_of_matches(matches) if mixed is None else mixed
    acc: dict[str, OpponentStat] = {}
    for m in matches:
        s = acc.get(m.opponent)
        if s is None:
            s = acc[m.opponent] = OpponentStat(nickname=m.opponent, last_date=m.date_text, mixed=mixed)
        s.games += 1
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        s.goals_for += m.my_goals
        s.goals_against += m.opp_goals
    return sorted(acc.values(), key=lambda s: -s.games)


@dataclass
class PeriodRate(RateMixin):
    """하루 치 승률 — 기간별 승률 추이(그래프)의 한 점."""
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    day: date | None = None  # 라벨("%m/%d")엔 연도가 없어 이동평균 창을 못 잰다
    mixed: bool | None = None

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


def current_streak(matches: list[MatchSummary]) -> tuple[str, int]:
    """가장 최근 경기부터 같은 결과("승"/"무"/"패")가 몇 연속인지.

    matches 는 최신순(0번이 가장 최근)이 전제 — 화면 표시 순서 그대로다.
    몰수승/몰수패도 승패로 친다(summarize 와 동일 기준). 경기가 없으면
    ("", 0)."""
    def kind(result: str) -> str:
        if "승" in result:
            return "승"
        if "무" in result:
            return "무"
        if "패" in result:
            return "패"
        return ""

    if not matches:
        return "", 0
    first = kind(matches[0].result)
    if not first:
        return "", 0
    n = 0
    for m in matches:
        if kind(m.result) != first:
            break
        n += 1
    return first, n


def longest_streaks(matches: list[MatchSummary]) -> tuple[int, int]:
    """(최장 연승, 최장 연패) — matches 순서 무관(날짜로 다시 정렬해서 센다).

    current_streak 과 같은 기준: 몰수승/몰수패도 승패로 치고, 무승부는
    연속을 끊는다. 날짜 없는 경기는 뺀다."""
    dated = sorted((m for m in matches if m.match_date is not None),
                   key=lambda m: m.match_date)
    best_win = best_lose = 0
    run_kind, run = "", 0
    for m in dated:
        kind = "승" if "승" in m.result else ("패" if "패" in m.result else "")
        if kind and kind == run_kind:
            run += 1
        else:
            run_kind, run = kind, (1 if kind else 0)
        if run_kind == "승":
            best_win = max(best_win, run)
        elif run_kind == "패":
            best_lose = max(best_lose, run)
    return best_win, best_lose


@dataclass
class PeriodStat(RateMixin):
    """기간(1일/2일/1주/1개월 묶음) 하나의 전적 — 기간별 추이 표의 한 줄."""
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    mixed: bool | None = None

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    @property
    def avg_gf(self) -> float:
        return self.goals_for / self.games if self.games else 0.0

    @property
    def avg_ga(self) -> float:
        return self.goals_against / self.games if self.games else 0.0


def period_stats(matches: list[MatchSummary], days: int = 7, mixed: bool | None = None) -> list[PeriodStat]:
    """경기를 기간 단위로 묶은 전적 — 최신 기간부터.

    days: 1(일별) · 2(2일씩) · 7(주 — 월요일 시작) · 30(달력 월).
    7과 30은 임의 창이 아니라 달력 기준(월요일 시작 주, 그 달)으로 묶는다 —
    "이번 주" "7월" 같은 자연스러운 구간과 어긋나면 오히려 헷갈린다.
    경기가 없던 기간은 만들지 않는다. 승률 식은 표 하나(dated 전체)로 한 번 — 표의 평균(overall)도 같은 값으로."""
    dated = [m for m in matches if m.match_date is not None]
    mixed = mixed_of_matches(dated) if mixed is None else mixed
    acc: dict = {}
    for m in dated:
        d = m.match_date.date()
        if days == 30:
            key = (d.year, d.month)
            label = f"{d.year}-{d.month:02d}"
        elif days == 7:
            start = d - timedelta(days=d.weekday())
            key = start
            label = f"{start:%m/%d}~{start + timedelta(days=6):%m/%d}"
        elif days == 1:
            key = d
            label = f"{d:%Y-%m-%d}"
        else:
            start_ord = d.toordinal() // days * days
            key = start_ord
            start = date.fromordinal(start_ord)
            label = f"{start:%m/%d}~{start + timedelta(days=days - 1):%m/%d}"
        s = acc.get(key)
        if s is None:
            s = acc[key] = PeriodStat(label=label, mixed=mixed)
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        s.goals_for += m.my_goals
        s.goals_against += m.opp_goals
    return [acc[k] for k in sorted(acc.keys(), reverse=True)]


def win_rate_trend(matches: list[MatchSummary], days: int = 30) -> list[PeriodRate]:
    """최근 <days>일(기본 30일) 일별 승률 추이 — 오래된 날부터.

    기준은 오늘이 아니라 matches 안에서 가장 최근 경기 날짜 — 그래야 한동안
    안 켠 계정을 조회해도 "최근 30일"이 그 계정 기준으로 잡힌다.
    경기가 아예 없던 날은 만들지 않는다(그래프에서 0%로 보이면 "다 짐"과
    구분이 안 된다 — charts.AreaTrendChart 도 이 전제로 그린다).
    """
    dated = [m for m in matches if m.match_date is not None]
    if not dated:
        return []
    latest = max(m.match_date for m in dated).date()
    cutoff = latest - timedelta(days=days - 1)
    shown = [m for m in dated if m.match_date.date() >= cutoff]
    mixed = mixed_of_matches(shown)   # 묶음 = 그래프 하나(그 기간) — 이동 승률도 이 값을 받는다

    acc: dict = {}
    for m in shown:
        d = m.match_date.date()
        s = acc.get(d)
        if s is None:
            s = acc[d] = PeriodRate(label=d.strftime("%m/%d"), day=d, mixed=mixed)
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
    return [acc[k] for k in sorted(acc.keys())]


def moving_win_rate(matches: list[MatchSummary], days: list[date], *, min_n: int, mixed: bool,
                    window_days: int = 7) -> list[float | None]:
    """days 의 날마다 달력 window_days 일(그날 포함 앞 window_days-1 일) 승률(win_pct · %).

    경기에서 직접 센다 — 그래프 점(PeriodRate)은 경기 없는 날을 안 만들어서 점 7개가 7일이 아니다.
    창 안 승률 분모(rated)가 min_n 미만이면 None(선이 끊긴다). min_n 은 부르는 쪽이 준다
    (analysis.MIN_COND — models 가 analysis 를 import 하면 순환이다). mixed 는 그래프 점과 같은 값(win_rate_trend)."""
    per_day: dict[date, list[int]] = {}
    for m in matches:
        if m.match_date is None:
            continue
        if "승" in m.result:
            k = 0
        elif "무" in m.result:
            k = 1
        elif "패" in m.result:
            k = 2
        else:
            continue  # 오류·몰수 미상 — 승률 분모에 안 넣는다(PeriodRate.games 와 같다)
        per_day.setdefault(m.match_date.date(), [0, 0, 0])[k] += 1
    out: list[float | None] = []
    for d in days:
        wdl = [0, 0, 0]
        for k in range(window_days):
            acc = per_day.get(d - timedelta(days=k))
            if acc:
                for i in range(3):
                    wdl[i] += acc[i]
        n = rated(*wdl, mixed)
        out.append(None if n == 0 or is_weak(n, min_n) else win_pct(*wdl, mixed))
    return out


# 승률을 내는 집계 결과 전부 — test_parsing 이 결과의 mixed 가 bool 인지, test_rules 가 win_pct·RateMixin 을 쓰는
# 클래스가 여기 빠짐없이 있는지 대조한다. 이름만 둔다(stats·squad_timeline 을 import 하면 순환이다).
RATE_CLASSES = ("Stats", "OpponentStat", "PeriodRate", "PeriodStat", "PlayerStat", "FormationStat",
                "TimeBandRate", "StreakAfter", "DivisionStat", "PossessionBand", "TeamColorStat", "OppCard",
                "ResultBreakdown", "ClutchSummary", "Discipline", "Window")
