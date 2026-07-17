"""매치 상세 JSON → 화면에 뿌릴 요약 자료구조."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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

    @property
    def score(self) -> str:
        return f"{self.my_goals} : {self.opp_goals}"

    @property
    def pass_rate(self) -> float:
        return (self.pass_success / self.pass_try * 100) if self.pass_try else 0.0

    @property
    def date_text(self) -> str:
        return self.match_date.strftime("%Y-%m-%d %H:%M") if self.match_date else "-"


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
        my_nickname=me.get("nickname", "-"),
        opponent=opp.get("nickname", "-"),
        result=md.get("matchResult", "-"),
        my_goals=shoot.get("goalTotal", 0),
        opp_goals=opp_shoot.get("goalTotal", 0),
        possession=md.get("possession", 0),
        shoot_total=shoot.get("shootTotal", 0),
        shoot_effective=shoot.get("effectiveShootTotal", 0),
        pass_try=passes.get("passTry", 0),
        pass_success=passes.get("passSuccess", 0),
        rating=md.get("averageRating", 0.0),
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
