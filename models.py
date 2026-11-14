"""매치 상세 JSON → 화면에 뿌릴 요약 자료구조.

승률 식(2.6.1 A6 — ROADMAP "A6 무승부 분모"): 무승부는 양쪽 다 챔피언스 이상인 경기에선 안 나온다(연장·승부차기 —
실측 0판). 그래서 승률을 내는 묶음(표 하나·그래프 하나·견주는 두 값은 합친 범위)에 무승부가 나는 경기와 안 나는 경기가
**섞이면** 승÷(승+패), 한쪽뿐이면 승÷전체. 식은 `win_pct` 하나 · 분모 `rated` · 표본 판정 `is_weak` · 섞임 `is_mixed`.
집계 줄의 `mixed` 는 기본 None 이고 None 이면 승률이 **오류**다 — 채우는 걸 잊은 길이 옛 식으로 조용히 남지 않게.
2026-10-19 사용자: 승률은 전부 무승부를 넣어(승÷전체) — `config.WIN_RATE_DRAWS_IN` 이 켜져 있으면 `is_mixed` 가 늘 거짓이다.
위 섞임 규칙은 스위치를 끄면 그대로 돌아온다(테스트가 둘 다 잰다).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import config
import matchtime

# division 메타(오픈API get_meta('division'))는 숫자가 작을수록 높은 등급이다.
#   800 슈퍼챔피언스 · 900 챔피언스 · 1000 슈퍼챌린지 · 1100~1300 챌린지1~3 · ...
# "챔피언스 이상" = 900 이하. stats 가 여기서 가져간다(models 는 잎 — stats 를 import 하지 않는다).
CHAMPION_DIVISION_ID = 900
SUPER_CHAMPION_DIVISION_ID = 800


# matchEndType — 오픈API 문서(인용): 0 정상 · 1 몰수승 · 2 몰수패. 4 는 문서에 없고 실DB 에서
# "오류" 결과에만 붙는다(55팀-경기, 2026-10-05 실측). 1·2 경기의 matchResult 는 "몰수승"이 아니라
# 그냥 "승"·"패"라, 결과 문자열로는 몰수를 못 가린다(1.2.1 전 result_breakdown 의 몰수 줄이 늘 0이던 이유).
# 108: stats 에서 여기로 옮겼다 — 승무패를 세는 자리(result_of)가 models 에 있어서.
END_NORMAL, END_FORFEIT_WIN, END_FORFEIT_LOSE, END_ABORT = 0, 1, 2, 4


def end_type(p: dict | None) -> int | None:
    v = ((p or {}).get("matchDetail") or {}).get("matchEndType")
    return v if isinstance(v, int) else None


def forfeit_loss(me: dict | None, opp: dict | None) -> bool:
    """내 쪽이 4(오류)로 남았는데 상대가 1(몰수승)을 받은 판 — 실제로는 몰수패다(108 F1 · 실DB 66판 전부 내 "오류"/상대 "승").
    보통 몰수패(내 2)는 matchResult 가 이미 "패"라 여기 안 든다."""
    return end_type(me) == END_ABORT and end_type(opp) == END_FORFEIT_WIN


def result_of(me: dict | None, opp: dict | None) -> str:
    """승·무·패를 **세는** 자리의 결과 글자(108 J2 ⓐ) — matchResult 원문, 단 forfeit_loss 면 "패"."""
    if forfeit_loss(me, opp):
        return "패"
    return ((me or {}).get("matchDetail") or {}).get("matchResult") or "-"


def _side_blank(p: dict | None) -> bool:
    # 넥슨은 지워진 쪽에 "player": [] 를 준다 — 키가 없는 판은 실DB 에 0(2026-11-14 · 71,665 팀-경기 · 빈 목록 405).
    # 키가 없는 것(지어낸 경기·옛 테스트)은 지워진 판이 아니라 모르는 판이라 빈 판으로 치지 않는다.
    if not p:
        return True
    return "player" in p and not p["player"]


def is_blank(me: dict | None, opp: dict | None) -> bool:
    """한쪽이라도 선수 기록이 없는 판(108 J1) — 경기당 평균·나 대 상대 비교에서 뺀다. 상대가 없어도 참.
    나간 쪽 기록은 슛·골·점유율이 전부 0 으로 지워져 와서(F13) 평균을 0 쪽으로, 짝 비교를 한쪽으로 민다."""
    return _side_blank(me) or _side_blank(opp)


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
    챔스 아래만 하는 계정의 식을 통째로 바꾸지 않게(검토 1회차 [상]). 판정할 값이 없으면 한 집단.
    config.WIN_RATE_DRAWS_IN 이 켜져 있으면 늘 한 집단(승÷전체) — 앱의 승률 식은 전부 여기를 거친다."""
    if config.WIN_RATE_DRAWS_IN:
        return False
    seen = {f for f in flags if f is not None}
    return len(seen) > 1


def mixed_of_details(details, ouid: str) -> bool:
    return is_mixed(draw_possible_of(d, ouid) for d in details)


def mixed_of_matches(matches) -> bool:
    return is_mixed(getattr(m, "draw_possible", None) for m in matches)


def rated(win: int, draw: int, lose: int, mixed: bool | None) -> int:
    """승률 분모 — 섞임이면 승+패, 한 집단이면 승+무+패. 표본 판정도 이 값으로 한다."""
    if mixed is None:
        raise ValueError("mixed 를 안 채운 승률입니다. 묶음에서 is_mixed 로 정해 넘겨야 합니다.")
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
    opp_division: int | None = None     # 그 경기 때 상대 등급(matchInfo 의 division — 화면 그림용) · 저장 형식 아님
    opp_controller: str | None = None   # 상대 입력 장치(matchDetail.controller — "keyboard"·"gamepad") · 저장 형식 아님
    blank: bool = False                 # 한쪽이라도 선수 기록이 없다(is_blank) — 경기당 평균에서 뺀다 · 승무패·득실 합계는 센다
    no_squad: bool = False              # 내 선수 기록이 없다 — 선수 표의 팀 승률 기준만 쓴다(선수별 승률과 같은 판)
    season_id: int | None = None        # matchDetail.seasonId(202606 꼴) — 경기를 시즌에 나누는 정본(108 B · seasons.SeasonMap)

    @property
    def opp_pad(self) -> bool:
        """상대가 패드로 했나(65 M2 — 경기 목록 카드에 패드일 때만 아이콘 · 키보드가 95%라 늘 띄우면 안 보인다)."""
        return self.opp_controller == "gamepad"

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


# 닉네임이 없는 상대(넥슨이 빈 값으로 준다 — 이 PC 감독모드 28,654경기 중 47 · 10-31)를 파싱하면 이 글자.
# 서로 다른 상대가 한 이름으로 뭉치므로 상대별 줄에서는 뺀다(67 R12 · app_main._opp_owner_rows)
NO_NICK = "-"


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


def team_rating(side: dict) -> float:
    """한 팀의 경기 평점 — 뛴 선수(spRating > 0) 평균. 없으면 0(모름 — 몰수·오류 판).
    넥슨 averageRating 은 안 뛴 벤치까지 18 로 나눈 값이라 선수 평점(6점대)과 척도가 다르다
    (2026-11-04 실측: 평점 있는 62,831줄 전부 합 ÷ averageRating = 18 — 뛴 사람은 62,768줄이 11명).
    그 값은 player 가 빠진 응답에서만 그대로 돌려준다."""
    played = []
    for p in side.get("player") or []:
        v = _f(p.get("status") or {}, "spRating")
        if v > 0:
            played.append(v)
    if played:
        return sum(played) / len(played)
    return _f(side.get("matchDetail") or {}, "averageRating")


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
        match_date=matchtime.parse(detail.get("matchDate")),   # KST — 넥슨 원문은 UTC
        match_type=detail.get("matchType", 0),
        my_nickname=me.get("nickname") or "-",
        opponent=opp.get("nickname") or NO_NICK,
        result=result_of(me, opp),
        my_goals=_i(shoot, "goalTotal"),
        opp_goals=_i(opp_shoot, "goalTotal"),
        possession=_i(md, "possession"),
        shoot_total=_i(shoot, "shootTotal"),
        shoot_effective=_i(shoot, "effectiveShootTotal"),
        pass_try=_i(passes, "passTry"),
        pass_success=_i(passes, "passSuccess"),
        rating=team_rating(me),
        my_shootout=_i(shoot, "shootOutScore"),
        opp_shootout=_i(opp_shoot, "shootOutScore"),
        draw_possible=draw_possible(me.get("division"), opp.get("division")) if opp else None,
        opp_division=opp.get("division") if opp and isinstance(opp.get("division"), int) else None,
        opp_controller=(opp.get("matchDetail") or {}).get("controller") if opp else None,
        blank=is_blank(me, opp),
        no_squad=_side_blank(me),
        season_id=_season_id(md),
    )


def _season_id(md: dict) -> int | None:
    v = md.get("seasonId")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


@dataclass
class Stats(RateMixin):
    """조회한 경기들을 합친 통계. 경기당 평균은 빈 판(MatchSummary.blank)을 뺀 판 수로 낸다 —
    승무패·total·득실 합계(goals_for·goals_against)는 빈 판도 센다(108 U4)."""
    total: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    possession_sum: int = 0     # 빈 판은 안 더한다(평균 전용)
    rating_sum: float = 0.0
    rating_n: int = 0           # 평점 있는 판 — 몰수 판은 평점이 없어(0) 평균을 끌어내리지 않게 뺀다
    mixed: bool | None = None
    blank: int = 0              # 빈 판 수 — 평균 분모에서 뺀다
    blank_gf: int = 0           # 빈 판의 득실 — 합계에는 들고 평균에서는 뺀다
    blank_ga: int = 0

    @property
    def avg_n(self) -> int:
        return self.total - self.blank

    @property
    def avg_goals_for(self) -> float:
        return ((self.goals_for - self.blank_gf) / self.avg_n) if self.avg_n else 0.0

    @property
    def avg_goals_against(self) -> float:
        return ((self.goals_against - self.blank_ga) / self.avg_n) if self.avg_n else 0.0

    @property
    def avg_possession(self) -> float:
        return (self.possession_sum / self.avg_n) if self.avg_n else 0.0

    @property
    def avg_rating(self) -> float:
        return (self.rating_sum / self.rating_n) if self.rating_n else 0.0


def summarize(matches: list[MatchSummary], mixed: bool | None = None) -> Stats:
    """mixed 를 주면 그 식으로(견주는 묶음이 합친 범위로 정한 값), 없으면 matches 로 정한다."""
    s = Stats(mixed=mixed_of_matches(matches) if mixed is None else mixed)
    for m in matches:
        # 몰수승/몰수패도 승패로 친다(내 쪽 "오류"로 남은 몰수패는 parse_match 가 이미 "패"로 바꿨다 — 108 U1).
        # 양쪽 다 "오류"(중단된 경기)처럼 승·무·패가 아닌 결과는 아예 집계에서 뺀다.
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
        if m.blank:
            # 한쪽 기록이 지워진 판 — 점유율·평점·경기당 득실에 0 이 섞이지 않게(108 J1 · U4)
            s.blank += 1
            s.blank_gf += m.my_goals
            s.blank_ga += m.opp_goals
            continue
        s.possession_sum += m.possession
        if m.rating > 0:
            s.rating_sum += m.rating
            s.rating_n += 1
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
    opp_division: int | None = None   # 그 상대와의 가장 최근 경기 때 등급(화면 그림용 — 매번 바뀌는 값이라 최근 것)
    blank: int = 0                    # 빈 판 — 평균 분모에서 뺀다(득실 합계 "a:b" 에는 든다 · 108 U4)
    blank_gf: int = 0
    blank_ga: int = 0

    @property
    def avg_goals_for(self) -> float:
        n = self.games - self.blank
        return ((self.goals_for - self.blank_gf) / n) if n else 0.0

    @property
    def avg_goals_against(self) -> float:
        n = self.games - self.blank
        return ((self.goals_against - self.blank_ga) / n) if n else 0.0


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
            s = acc[m.opponent] = OpponentStat(nickname=m.opponent, last_date=m.date_text, mixed=mixed,
                                               opp_division=m.opp_division)
        s.games += 1
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        s.goals_for += m.my_goals
        s.goals_against += m.opp_goals
        if m.blank:
            s.blank += 1
            s.blank_gf += m.my_goals
            s.blank_ga += m.opp_goals
    return sorted(acc.values(), key=lambda s: -s.games)


def filter_matches(matches: list[MatchSummary], n: int | None = None, formation: str | None = None,
                   formation_of=None, value_bin: int | None = None, bin_of=None) -> list[MatchSummary]:
    """57 K1 매치업 필터 — 최근 n경기(최신순 앞에서 · None = 전부) **안에서** 내 포메이션·상대 구단가치 칸으로 거른다.
    formation_of(경기) → 포메이션 키 · bin_of(상대 닉네임) → 칸 번호(모르면 None — value_bin=-1 이 '모름' 칸).
    None 인 조건은 안 본다. 순서는 그대로(최신순)."""
    out = list(matches[:n]) if n else list(matches)
    if formation is not None:
        out = [m for m in out if formation_of(m) == formation]
    if value_bin is not None:
        want = None if value_bin < 0 else value_bin
        out = [m for m in out if bin_of(m.opponent) == want]
    return out


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
    """기간(일·주·월 묶음) 하나의 전적 — [기간별 성적] 표의 한 줄."""
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    goals_for: int = 0
    goals_against: int = 0
    mixed: bool | None = None
    start: date | None = None   # 기간 첫날 — 일 단위 7일 이동 승률(core.moving_win_rate)이 날짜로 받는다
    blank: int = 0              # 승·무·패로 센 판 중 빈 판 — 평균 분모에서 뺀다(108 U4)
    blank_gf: int = 0
    blank_ga: int = 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    @property
    def avg_gf(self) -> float:
        n = self.games - self.blank
        return (self.goals_for - self.blank_gf) / n if n else 0.0

    @property
    def avg_ga(self) -> float:
        n = self.games - self.blank
        return (self.goals_against - self.blank_ga) / n if n else 0.0


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
            start = date(d.year, d.month, 1)
            key = (d.year, d.month)
            label = f"{d.year}-{d.month:02d}"
        elif days == 7:
            start = d - timedelta(days=d.weekday())
            key = start
            label = f"{start:%m/%d}~{start + timedelta(days=6):%m/%d}"
        elif days == 1:
            start = key = d
            label = f"{d:%Y-%m-%d}"
        else:
            start_ord = d.toordinal() // days * days
            key = start_ord
            start = date.fromordinal(start_ord)
            label = f"{start:%m/%d}~{start + timedelta(days=days - 1):%m/%d}"
        s = acc.get(key)
        if s is None:
            s = acc[key] = PeriodStat(label=label, mixed=mixed, start=start)
        counted = True
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        else:
            counted = False
        s.goals_for += m.my_goals
        s.goals_against += m.opp_goals
        if m.blank and counted:   # games(분모)에 든 판만 — 승무패가 아닌 판은 원래 분모 밖이다
            s.blank += 1
            s.blank_gf += m.my_goals
            s.blank_ga += m.opp_goals
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


def window_of(matches: list[MatchSummary], kind: str = "all", n: int = 0) -> list[MatchSummary]:
    """76 위쪽 바 빠른 범위 — 최신순 목록에서 ("all") 전부 · ("games", n) 최신 n판 · ("days", n) 최근 n일.

    날은 오늘이 아니라 목록 안 가장 최근 경기 날부터 거꾸로 n일(그날 포함) — 오늘 안 뛴 계정·지난 시즌을 골라도
    비지 않게(win_rate_trend 와 같은 기준 · U12). 날짜 없는 경기는 날 범위에서 뺀다. 순서는 받은 그대로."""
    if kind == "games":
        return list(matches[:max(n, 0)])
    if kind == "days":
        dated = [m.match_date for m in matches if m.match_date is not None]
        if not dated or n <= 0:
            return []
        cutoff = max(dated).date() - timedelta(days=n - 1)
        return [m for m in matches if m.match_date is not None and m.match_date.date() >= cutoff]
    return list(matches)


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
                "ResultBreakdown", "ClutchSummary", "Discipline", "Window", "OppBand")


def division_icon_index(div_id, division_meta: list[dict] | None) -> int | None:
    """등급 id → 등급 배지 CDN 번호. 번호 = get_meta("division") 원본 목록의 순서(0 = 슈퍼챔피언스 … 20 = 유망주3)라
    이름 dict 로는 못 낸다 — 원본 목록이 필요하다. 모르면 None(그림 없이 글자만)."""
    if div_id is None:
        return None
    for i, d in enumerate(division_meta or []):
        if d.get("divisionId") == div_id:
            return i
    return None
