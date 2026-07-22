"""매치 상세 JSON → 화면에 뿌릴 요약 자료구조."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


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
    )


@dataclass
class Stats:
    """조회한 경기들을 합친 통계."""
    total: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    possession_sum: int = 0
    rating_sum: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.win / self.total * 100) if self.total else 0.0

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


def summarize(matches: list[MatchSummary]) -> Stats:
    s = Stats(total=len(matches))
    for m in matches:
        # 몰수승/몰수패도 승패로 친다
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        s.goals_for += m.my_goals
        s.goals_against += m.opp_goals
        s.possession_sum += m.possession
        s.rating_sum += m.rating
    return s


@dataclass
class OpponentStat:
    """상대 구단주 한 명과의 상성 — 전적·평균 득실."""
    nickname: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    last_date: str = "-"

    @property
    def win_rate(self) -> float:
        return (self.win / self.games * 100) if self.games else 0.0

    @property
    def avg_goals_for(self) -> float:
        return (self.goals_for / self.games) if self.games else 0.0

    @property
    def avg_goals_against(self) -> float:
        return (self.goals_against / self.games) if self.games else 0.0


def opponent_stats(matches: list[MatchSummary]) -> list[OpponentStat]:
    """상대 닉네임별로 묶은 상성 — 많이 붙어본 순.

    matches 는 최신순으로 온다는 게 이미 전제라(화면 표시 순서 그대로),
    한 상대를 처음 만나는 순간(=가장 최근 경기)의 날짜를 last_date 로 쓴다.
    """
    acc: dict[str, OpponentStat] = {}
    for m in matches:
        s = acc.get(m.opponent)
        if s is None:
            s = acc[m.opponent] = OpponentStat(nickname=m.opponent, last_date=m.date_text)
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
class PeriodRate:
    """하루 치 승률 — 기간별 승률 추이(그래프)의 한 점."""
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    @property
    def win_rate(self) -> float:
        return (self.win / self.games * 100) if self.games else 0.0


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
class PeriodStat:
    """기간(1일/2일/1주/1개월 묶음) 하나의 전적 — 기간별 추이 표의 한 줄."""
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    @property
    def win_rate(self) -> float:
        return (self.win / self.games * 100) if self.games else 0.0

    @property
    def avg_gf(self) -> float:
        return self.goals_for / self.games if self.games else 0.0

    @property
    def avg_ga(self) -> float:
        return self.goals_against / self.games if self.games else 0.0


def period_stats(matches: list[MatchSummary], days: int = 7) -> list[PeriodStat]:
    """경기를 기간 단위로 묶은 전적 — 최신 기간부터.

    days: 1(일별) · 2(2일씩) · 7(주 — 월요일 시작) · 30(달력 월).
    7과 30은 임의 창이 아니라 달력 기준(월요일 시작 주, 그 달)으로 묶는다 —
    "이번 주" "7월" 같은 자연스러운 구간과 어긋나면 오히려 헷갈린다.
    경기가 없던 기간은 만들지 않는다."""
    dated = [m for m in matches if m.match_date is not None]
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
            s = acc[key] = PeriodStat(label=label)
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
    구분이 안 된다 — TrendChart 도 이 전제로 그린다).
    """
    dated = [m for m in matches if m.match_date is not None]
    if not dated:
        return []
    latest = max(m.match_date for m in dated).date()
    cutoff = latest - timedelta(days=days - 1)

    acc: dict = {}
    for m in dated:
        d = m.match_date.date()
        if d < cutoff:
            continue
        s = acc.get(d)
        if s is None:
            s = acc[d] = PeriodRate(label=d.strftime("%m/%d"))
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
    return [acc[k] for k in sorted(acc.keys())]
