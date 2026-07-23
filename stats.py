"""여러 경기를 가로질러 집계하는 통계 — 선수 지표 · 전술 · 경기 결과.

여기 있는 상수(GOAL_TYPES, 시간 구간 인코딩, 포메이션 라인)는 공식 문서에
없어서 실제 응답 100경기로 역산·검증한 값이다. 근거는 각 상수에 적어 뒀다.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

SUB_POSITION = 28  # spposition 메타: 28=SUB(교체 명단)
GK_POSITION = 0

# division 메타(오픈API get_meta('division'))는 숫자가 작을수록 높은 등급이다.
#   800 슈퍼챔피언스 · 900 챔피언스 · 1000 슈퍼챌린지 · 1100~1300 챌린지1~3 · ...
# "챔피언스 이상" = 900 이하.
CHAMPION_DIVISION_ID = 900


def is_champion_or_above(division_id: int | None) -> bool:
    return division_id is not None and division_id <= CHAMPION_DIVISION_ID


def division_trend(details: list, ouid: str) -> list[tuple[datetime, int]]:
    """경기 시점별 내 등급(division) — 오래된 경기부터 (일시, divisionId).

    매치 상세에 그 경기 당시의 division 이 박혀 있어서(_current_grade 와
    같은 필드) 이미 쌓아 둔 데이터만으로 등급 변화 이력이 나온다.
    날짜나 division 이 빠진 경기는 건너뛴다."""
    out: list[tuple[datetime, int]] = []
    for d in details:
        raw = d.get("matchDate")
        me = next((p for p in d.get("matchInfo") or []
                   if p.get("ouid") == ouid), None)
        div = me.get("division") if me else None
        if not raw or div is None:
            continue
        try:
            out.append((datetime.fromisoformat(raw), int(div)))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda t: t[0])
    return out

# 슛 유형. 공식 문서에 매핑이 없어 실제 응답으로 확정했다.
#  - type 8/9 는 shoot.goalFreekick / goalPenaltyKick 집계와 200개 선수-경기
#    행에서 경기별로 정확히 일치 → 확정.
#  - 1/2/3/6/7 은 외부 전적 사이트가 같은 계정 100경기에서 낸 유형별 골 수와
#    합계가 전부 일치 → 확정.
#  - 4/10/13/14 는 근거가 없어 비워 둔다(= "알 수 없음"). 참고한 사이트도
#    이 타입들을 못 읽고 "알 수 없음"으로 표시한다.
GOAL_TYPES = {
    1: "일반(D)",
    2: "감아차기(ZD)",
    3: "헤더",
    6: "땅볼(DD)",
    7: "발리",
    8: "프리킥",
    9: "페널티킥",
}
UNKNOWN_GOAL_TYPE = "알 수 없음"

# goalTime 은 비트 패킹이다: 상위 8비트=구간, 하위 24비트=경과 초.
# 실제 응답에서 전·후반은 최대 49.8분(45+추가), 연장은 19.1분(15+추가)으로
# 딱 떨어져 검증됐다.
PERIODS = {0: "전반전", 1: "후반전", 2: "연장 전반", 3: "연장 후반"}

GOAL_RESULT = 3  # result==3 이 골. 100경기에서 goalTotal 합계와 정확히 일치했다.

# 포메이션 라인 — 스크린샷의 "수비-수미-미드-공미-공격" 5개 라인.
# GK(0)와 SUB(28)은 제외한다.
_LINES = [
    ("수비", range(1, 9)),    # SW RWB RB RCB CB LCB LB LWB
    ("수미", range(9, 12)),   # RDM CDM LDM
    ("미드", range(12, 17)),  # RM RCM CM LCM LM
    ("공미", range(17, 20)),  # RAM CAM LAM
    ("공격", range(20, 28)),  # RF CF LF RW RS ST LS LW
]


def decode_goal_time(raw) -> tuple[int, int]:
    """goalTime → (구간 코드, 경과 초). 값이 이상하면 (0, 0)."""
    if not isinstance(raw, int) or raw < 0:
        return 0, 0
    return raw >> 24, raw & 0xFFFFFF


def goal_type_name(t) -> str:
    return GOAL_TYPES.get(t, UNKNOWN_GOAL_TYPE)


def season_id_of(sp_id: int) -> int:
    """spId 앞 3자리 = 시즌 코드, 나머지 6자리 = 선수 코드.

    실제 spId 여러 개를 get_meta("seasonid") 목록과 대조해 확인했다
    (예: 100000041 → 100=ICON TM, 848121944 → 848=WS(Winning Streak))."""
    return int(str(sp_id)[:3])


def formation_of(players: list[dict]) -> str:
    """선발 포지션 인원으로 전술 표기를 만든다. 예: 4-1-2-3-0"""
    counts = []
    for _, rng in _LINES:
        n = sum(1 for p in players
                if isinstance(p.get("spPosition"), int) and p["spPosition"] in rng)
        counts.append(n)
    return "-".join(str(c) for c in counts)


def _num(d: dict, key: str) -> float:
    """models._i 와 같은 이유 — 넥슨은 빈 값을 null 로 준다."""
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


def _me_opp(detail: dict, ouid: str) -> tuple[dict | None, dict]:
    infos = detail.get("matchInfo") or []
    me = next((p for p in infos if p.get("ouid") == ouid), None)
    opp = next((p for p in infos if p.get("ouid") != ouid), {})
    return me, opp


def _result_of(p: dict) -> str:
    return (p.get("matchDetail") or {}).get("matchResult") or "-"


def own_squad(details: list[dict], ouid: str):
    """가장 최근 경기에서 내 스쿼드(선수 raw 목록). opponent_squad 와 대칭.

    details 는 최신순 전제. 못 찾으면 None.
    돌려주는 값: (선수 raw 목록, 경기일 문자열, 그 경기 내 내 결과).
    """
    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        return me.get("player") or [], d.get("matchDate", "-"), _result_of(me)
    return None


def opponent_squad(details: list[dict], ouid: str, opponent_nickname: str):
    """상대 닉네임과 가장 최근에 붙었던 경기의 상대 스쿼드(선수 raw 목록).

    details 는 최신순으로 온다는 전제라(화면 표시 순서와 같다), 이 닉네임과
    처음 마주치는 항목이 곧 가장 최근 경기다. 못 찾으면 None.
    돌려주는 값: (선수 raw 목록, 경기일 문자열, 그 경기 내 내 결과).
    """
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        if (opp.get("nickname") or "-") != opponent_nickname:
            continue
        return opp.get("player") or [], d.get("matchDate", "-"), _result_of(me)
    return None


# ── 선수 지표 ────────────────────────────────────────────────────────────
@dataclass
class PlayerStat:
    sp_id: int
    name: str
    position: str
    grade: int = 0
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    goal: int = 0
    assist: int = 0
    shoot: int = 0
    effective_shoot: int = 0
    pass_try: float = 0.0
    pass_success: float = 0.0
    dribble_try: float = 0.0
    dribble_success: float = 0.0
    aerial_try: float = 0.0
    aerial_success: float = 0.0
    tackle_try: float = 0.0
    tackle: float = 0.0
    block_try: float = 0.0
    block: float = 0.0
    intercept: int = 0
    defending: int = 0
    yellow: int = 0
    red: int = 0
    rating_sum: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.win / self.games * 100 if self.games else 0.0

    @property
    def attack_point(self) -> int:
        return self.goal + self.assist

    @property
    def rating(self) -> float:
        return self.rating_sum / self.games if self.games else 0.0

    def _rate(self, ok: float, try_: float) -> float:
        return ok / try_ * 100 if try_ else 0.0

    @property
    def pass_rate(self) -> float:
        return self._rate(self.pass_success, self.pass_try)

    @property
    def dribble_rate(self) -> float:
        return self._rate(self.dribble_success, self.dribble_try)

    @property
    def aerial_rate(self) -> float:
        return self._rate(self.aerial_success, self.aerial_try)

    @property
    def tackle_rate(self) -> float:
        return self._rate(self.tackle, self.tackle_try)

    @property
    def block_rate(self) -> float:
        return self._rate(self.block, self.block_try)

    def _per_match(self, total: float) -> float:
        return total / self.games * 100 if self.games else 0.0

    # ── 파생 지표 — fc-info.com 프론트엔드 JS 번들에서 역산 ────────────
    # API 가 안 주는 값이라 직접 만들어야 했는데, 처음엔 우리 나름의 가중치로
    # 지어냈다가(커밋 이력 참고) 실제 계산식을 찾아 그대로 옮겼다.
    # fc-info 의 분석 페이지 JS 청크(pages/analysis/coach/[id]-*.js)에 미니파이된
    # 채로 그대로 들어있었다 — attackScore/defenceScore/defendingPoint 등의
    # 변수명으로. 실제 100경기 데이터로 재현해 값이 거의 일치함을 확인했다
    # (예: GK 선방력 151.5 vs 참고 151.6, CDM 수비력 362.4 vs 참고 365.0).
    #
    # 핵심 발견: "선방력"은 defending 누적 합계가 아니라 **경기당 평균 × 100**
    # 이다. 100경기 표본에서는 나눗셈과 곱셈이 상쇄돼 합계처럼 보였을 뿐이고,
    # 경기 수가 다르면(우리 DB는 누적이라 수천 경기) 완전히 다른 값이 된다.
    @property
    def expected_goal_rate(self) -> float:
        """기대득점률 — '경기당 공격포인트(골+어시) 비율×100'. 유효슛 대비
        득점률이 아니다(이름과 달리 fc-info 정의를 그대로 따름)."""
        return self._per_match(self.goal + self.assist)

    @property
    def intercept_rate(self) -> float:
        """가로채기 포인트 — 경기당 평균 × 100 (원본 표기: interceptPoint)."""
        return self._per_match(self.intercept)

    @property
    def defending_rate(self) -> float:
        """선방력(defendingPoint) — defending 경기당 평균 × 100. 합계가 아니다."""
        return self._per_match(self.defending)

    @property
    def save_power(self) -> float:
        """선방력 — defending_rate 의 별칭(표에는 이 이름으로 노출)."""
        return self.defending_rate

    def _is_gk(self) -> bool:
        return self.position == "GK"

    @property
    def attack_power(self) -> float:
        """공격력 = 10×기대득점률 + 패스% + 드리블% + 5×(승률/출전)
        + (GK 아니면 공중볼%)."""
        score = (10 * self.expected_goal_rate + self.pass_rate + self.dribble_rate
                + 5 * (self.win_rate / self.games if self.games else 0.0))
        if not self._is_gk():
            score += self.aerial_rate
        return score

    @property
    def defense_power(self) -> float:
        """수비력 = 패스% + 가로채기포인트 + 태클% + 2×선방력 + 블록%
        + 5×(승률/출전) + (GK 아니면 공중볼%)."""
        score = (self.pass_rate + self.intercept_rate + self.tackle_rate
                + 2 * self.defending_rate + self.block_rate
                + 5 * (self.win_rate / self.games if self.games else 0.0))
        if not self._is_gk():
            score += self.aerial_rate
        return score


def aggregate_players(details: list[dict], ouid: str,
                      name_of=None, pos_name=None) -> list[PlayerStat]:
    """내 선수들의 경기별 기록을 선수 단위로 합친다.

    교체 명단(SUB)이라도 기록이 있으면 출전으로 친다 — 교체 투입된 경우.
    name_of(spId)->이름, pos_name(코드)->포지션명 은 메타를 아는 쪽에서 넘긴다.
    """
    acc: dict[int, PlayerStat] = {}
    pos_count: dict[int, Counter] = defaultdict(Counter)

    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        res = _result_of(me)
        for p in me.get("player") or []:
            sp_id = p.get("spId")
            if not isinstance(sp_id, int):
                continue
            st = p.get("status") or {}
            pos = p.get("spPosition")
            played = pos != SUB_POSITION or any(
                _num(st, k) for k in ("shoot", "passTry", "tackleTry", "spRating")
            )
            if not played:
                continue

            s = acc.get(sp_id)
            if s is None:
                s = acc[sp_id] = PlayerStat(
                    sp_id=sp_id,
                    name=name_of(sp_id) if name_of else str(sp_id),
                    position="-",
                )
            if isinstance(pos, int):
                pos_count[sp_id][pos] += 1
            s.grade = max(s.grade, int(_num(p, "spGrade")))
            s.games += 1
            if "승" in res:
                s.win += 1
            elif "무" in res:
                s.draw += 1
            elif "패" in res:
                s.lose += 1
            s.goal += int(_num(st, "goal"))
            s.assist += int(_num(st, "assist"))
            s.shoot += int(_num(st, "shoot"))
            s.effective_shoot += int(_num(st, "effectiveShoot"))
            s.pass_try += _num(st, "passTry")
            s.pass_success += _num(st, "passSuccess")
            s.dribble_try += _num(st, "dribbleTry")
            s.dribble_success += _num(st, "dribbleSuccess")
            s.aerial_try += _num(st, "aerialTry")
            s.aerial_success += _num(st, "aerialSuccess")
            s.tackle_try += _num(st, "tackleTry")
            s.tackle += _num(st, "tackle")
            s.block_try += _num(st, "blockTry")
            s.block += _num(st, "block")
            s.intercept += int(_num(st, "intercept"))
            s.defending += int(_num(st, "defending"))
            s.yellow += int(_num(st, "yellowCards"))
            s.red += int(_num(st, "redCards"))
            s.rating_sum += _num(st, "spRating")

    for sp_id, s in acc.items():
        if pos_count[sp_id]:
            top = pos_count[sp_id].most_common(1)[0][0]  # 가장 자주 선 자리
            s.position = pos_name(top) if pos_name else str(top)
    return sorted(acc.values(), key=lambda s: (-s.games, -s.attack_point))


# ── 전술 ────────────────────────────────────────────────────────────────
@dataclass
class FormationStat:
    formation: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0

    @property
    def win_rate(self) -> float:
        return self.win / self.games * 100 if self.games else 0.0


def formation_stats(details: list[dict], ouid: str,
                    of_opponent: bool = True) -> list[FormationStat]:
    """전술별 내 승패.

    기본은 '상대 전술별 내 승률' — 상성을 보는 게 목적이라 이쪽이 쓸모 있다.
    of_opponent=False 면 내 전술 분포.
    상대가 탈주해 선수 기록이 없으면 0-0-0-0-0 으로 잡힌다.
    """
    acc: dict[str, FormationStat] = {}
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        side = opp if of_opponent else me
        f = formation_of(side.get("player") or [])
        s = acc.setdefault(f, FormationStat(formation=f))
        s.games += 1
        res = _result_of(me)
        if "승" in res:
            s.win += 1
        elif "무" in res:
            s.draw += 1
        elif "패" in res:
            s.lose += 1
    return sorted(acc.values(), key=lambda s: -s.games)


# ── 경기 결과 ────────────────────────────────────────────────────────────
@dataclass
class PeriodGoals:
    scored: int = 0
    conceded: int = 0


@dataclass
class ResultBreakdown:
    """스크린샷의 '경기 결과' 패널 — 전후반/연장/승부차기 구분과 유형별 득실."""
    normal: list[int] = field(default_factory=lambda: [0, 0, 0])   # 승,무,패
    extra: list[int] = field(default_factory=lambda: [0, 0, 0])
    shootout: list[int] = field(default_factory=lambda: [0, 0, 0])
    forfeit: list[int] = field(default_factory=lambda: [0, 0, 0])
    periods: dict[int, PeriodGoals] = field(default_factory=dict)
    goal_types: Counter = field(default_factory=Counter)
    concede_types: Counter = field(default_factory=Counter)

    @staticmethod
    def _rate(wdl: list[int]) -> float:
        tot = sum(wdl)
        return wdl[0] / tot * 100 if tot else 0.0


def _bump(wdl: list[int], res: str) -> None:
    if "승" in res:
        wdl[0] += 1
    elif "무" in res:
        wdl[1] += 1
    elif "패" in res:
        wdl[2] += 1


def result_breakdown(details: list[dict], ouid: str) -> ResultBreakdown:
    rb = ResultBreakdown()
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        res = _result_of(me)
        my_shoot = me.get("shoot") or {}
        op_shoot = opp.get("shoot") or {}

        # 몰수는 matchEndType 으로 구분된다(0=정상). 사용자 표시는 result 문자열.
        if "몰수" in res:
            _bump(rb.forfeit, res)
        elif _num(my_shoot, "shootOutScore") or _num(op_shoot, "shootOutScore"):
            _bump(rb.shootout, res)
        else:
            # 연장 구간(2,3)에 슛 기록이 있으면 연장까지 간 경기
            went_extra = any(
                decode_goal_time(sd.get("goalTime"))[0] >= 2
                for p in (me, opp) for sd in (p.get("shootDetail") or [])
            )
            _bump(rb.extra if went_extra else rb.normal, res)

        for p, mine in ((me, True), (opp, False)):
            for sd in p.get("shootDetail") or []:
                if sd.get("result") != GOAL_RESULT:
                    continue
                period, _sec = decode_goal_time(sd.get("goalTime"))
                pg = rb.periods.setdefault(period, PeriodGoals())
                if mine:
                    pg.scored += 1
                    rb.goal_types[goal_type_name(sd.get("type"))] += 1
                else:
                    pg.conceded += 1
                    rb.concede_types[goal_type_name(sd.get("type"))] += 1
    return rb


# ── 승부처 분석 ───────────────────────────────────────────────────────────
def _goal_events(p: dict) -> list[tuple[int, int]]:
    """한 선수(matchInfo 항목)의 골을 (구간, 경과초) 목록으로. 시간 순 정렬 전."""
    out = []
    for sd in p.get("shootDetail") or []:
        if sd.get("result") != GOAL_RESULT:
            continue
        out.append(decode_goal_time(sd.get("goalTime")))
    return out


@dataclass
class ClutchSummary:
    """선제골·역전 분석. first_* 는 [승, 무, 패]."""
    first_scored: list[int] = field(default_factory=lambda: [0, 0, 0])  # 내가 선제골
    first_conceded: list[int] = field(default_factory=lambda: [0, 0, 0])  # 선제 실점
    comeback_win: int = 0   # 선제 실점 후 승
    comeback_lose: int = 0  # 선제골 후 패
    goalless: int = 0       # 양측 무득점(선제골 판정 불가)

    @staticmethod
    def _rate(wdl: list[int]) -> float:
        tot = sum(wdl)
        return wdl[0] / tot * 100 if tot else 0.0

    @property
    def first_scored_rate(self) -> float:
        return self._rate(self.first_scored)

    @property
    def first_conceded_rate(self) -> float:
        return self._rate(self.first_conceded)


def clutch_summary(details: list[dict], ouid: str) -> ClutchSummary:
    """경기별 골 타임라인으로 선제골 여부와 역전 경기를 센다.

    양측 골이 같은 (구간, 초)로 오면(동시각) 선제골 판정을 보류하고
    무득점과 함께 goalless 로 분류한다 — 애매한 걸 억지로 한쪽에 넣지 않는다.
    """
    cs = ClutchSummary()
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        res = _result_of(me)
        mine = sorted(_goal_events(me))
        their = sorted(_goal_events(opp))
        if not mine and not their:
            cs.goalless += 1
            continue
        my_first = mine[0] if mine else None
        their_first = their[0] if their else None
        if my_first is not None and (their_first is None or my_first < their_first):
            first_mine = True
        elif their_first is not None and (my_first is None or their_first < my_first):
            first_mine = False
        else:  # 정확히 같은 시각 — 판정 보류
            cs.goalless += 1
            continue
        if first_mine:
            _bump(cs.first_scored, res)
            if "패" in res:
                cs.comeback_lose += 1
        else:
            _bump(cs.first_conceded, res)
            if "승" in res:
                cs.comeback_win += 1
    return cs


# 정규시간 15분 6구간 + 연장. 구간은 (구간0=전반, 구간1=후반)에 경과초로 매긴다.
MINUTE_BUCKETS = [(0, 15), (15, 30), (30, 45), (45, 60), (60, 75), (75, 90)]


@dataclass
class MinuteBucket:
    label: str
    scored: int = 0
    conceded: int = 0


def goal_minute_buckets(details: list[dict], ouid: str) -> list[MinuteBucket]:
    """15분 단위 6구간 + 연장의 득실 분포 — 언제 넣고 언제 먹히는가.

    전반(구간0)은 분=초/60, 후반(구간1)은 45+초/60. 45+·90+ 추가시간 골은
    각각 마지막 정규 구간(30~45, 75~90)에 포함한다. 연장(구간2·3)은 별도.
    """
    buckets = [MinuteBucket(f"{lo}~{hi}") for lo, hi in MINUTE_BUCKETS]
    extra = MinuteBucket("연장")

    def place(period: int, sec: int) -> MinuteBucket:
        if period >= 2:
            return extra
        minute = sec / 60 + (45 if period == 1 else 0)
        for b, (lo, hi) in zip(buckets, MINUTE_BUCKETS):
            if minute < hi:
                return b
        return buckets[2] if period == 0 else buckets[5]  # 추가시간 → 막판 구간

    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        for period, sec in _goal_events(me):
            place(period, sec).scored += 1
        for period, sec in _goal_events(opp):
            place(period, sec).conceded += 1
    return buckets + [extra]


# 하루 시각대 4구간 — matchDate 의 시(hour) 기준. "새벽에 하면 지는가".
TIME_BANDS = [("심야", 0, 6), ("오전", 6, 12), ("오후", 12, 18), ("저녁·밤", 18, 24)]


@dataclass
class TimeBandRate:
    label: str
    span: str
    win: int = 0
    draw: int = 0
    lose: int = 0

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose

    @property
    def win_rate(self) -> float:
        return self.win / self.games * 100 if self.games else 0.0


def time_of_day_rates(matches: list) -> list[TimeBandRate]:
    """경기 시작 시각대별 승/무/패 — 날짜 없는 경기는 뺀다."""
    bands = [TimeBandRate(name, f"{lo:02d}~{hi:02d}") for name, lo, hi in TIME_BANDS]
    for m in matches:
        if m.match_date is None:
            continue
        h = m.match_date.hour
        for band, (_, lo, hi) in zip(bands, TIME_BANDS):
            if lo <= h < hi:
                if "승" in m.result:
                    band.win += 1
                elif "무" in m.result:
                    band.draw += 1
                elif "패" in m.result:
                    band.lose += 1
                break
    return bands


# ── 슛 맵 ─────────────────────────────────────────────────────────────────
# shootDetail 의 result: 3=골, 1=유효슛(막힘), 2=빗나감.
# 실제 캐시로 확인: effectiveShootTotal == 골 + result1 (1189/1190),
# shootTotal == shootDetail 총개수 (1190/1190). 좌표 x·y 는 0~1 정규화이고
# x=1.0 이 상대 골문 쪽(슛이 x>0.48 에 몰림), y=0.5 가 폭 중앙이다.
SHOT_GOAL = 3
SHOT_ON_TARGET = 1
SHOT_OFF_TARGET = 2
_SHOT_RESULTS = (SHOT_GOAL, SHOT_ON_TARGET, SHOT_OFF_TARGET)

# ── 기대득점(xG) 근사 ──────────────────────────────────────────────────────
# ⚠️ 넥슨은 xG 정답값을 주지 않는다 — 이건 순수 근사 모델이다(비공식).
# 슛 좌표(거리·골대 각도)와 박스 안 여부·슛 유형만으로 득점 확률을 추정한다.
# 공개 xG 튜토리얼(거리·각도 로지스틱)의 형태를 따르되, 계수는 실제 캐시에서
# 전체 xG 합이 실제 골 수와 같은 규모가 되도록 맞췄다(자기 계정 기준 캘리브레이션).
_PITCH_LEN = 105.0   # m — x(0~1)가 경기장 전체 길이 비율
_PITCH_WID = 68.0    # m — y(0~1)가 폭 비율, 0.5 가 중앙
_GOAL_WIDTH = 7.32   # m


def shot_xg(x: float, y: float, in_penalty: bool = False,
            type_name: str = "") -> float:
    """슛 하나의 기대득점(0~1) 근사. 정답값 없는 비공식 추정."""
    if type_name == "페널티킥":
        return 0.76  # 실측 PK 성공률 근사 — 위치와 무관
    dx = max(0.01, (1.0 - x) * _PITCH_LEN)      # 골라인까지 거리
    dy = abs(y - 0.5) * _PITCH_WID              # 중앙에서 좌우로 벗어난 폭
    dist = math.hypot(dx, dy)
    # 골대 양 포스트가 슛 지점에서 이루는 각(넓을수록 넣기 쉽다)
    denom = dx * dx + dy * dy - (_GOAL_WIDTH / 2) ** 2
    angle = math.atan2(_GOAL_WIDTH * dx, denom)
    if angle < 0:
        angle += math.pi
    z = -0.15 + 0.115 * dist - 3.2 * angle
    xg = 1.0 / (1.0 + math.exp(z))
    if type_name == "헤더":
        xg *= 0.7   # 헤더는 같은 위치라도 득점 확률이 낮다
    return max(0.0, min(0.99, xg))


@dataclass
class Shot:
    x: float
    y: float
    result: int          # SHOT_GOAL / SHOT_ON_TARGET / SHOT_OFF_TARGET
    type_name: str
    in_penalty: bool = False
    hit_post: bool = False
    xg: float = 0.0


@dataclass
class ShotMap:
    shots: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.shots)

    @property
    def goals(self) -> int:
        return sum(1 for s in self.shots if s.result == SHOT_GOAL)

    @property
    def on_target(self) -> int:  # 골이 아닌 유효슛(막힌 슛)
        return sum(1 for s in self.shots if s.result == SHOT_ON_TARGET)

    @property
    def off_target(self) -> int:
        return sum(1 for s in self.shots if s.result == SHOT_OFF_TARGET)

    @property
    def effective(self) -> int:  # 유효슛 = 골 + 막힌 유효슛
        return self.goals + self.on_target

    @property
    def effective_rate(self) -> float:  # 유효슛률 = 유효슛 / 전체 슛
        return self.effective / self.total * 100 if self.total else 0.0

    @property
    def conversion(self) -> float:  # 골 전환율 = 골 / 전체 슛
        return self.goals / self.total * 100 if self.total else 0.0

    @property
    def in_penalty_goals(self) -> int:
        return sum(1 for s in self.shots
                   if s.result == SHOT_GOAL and s.in_penalty)

    @property
    def xg(self) -> float:  # 전체 기대득점 합(근사)
        return sum(s.xg for s in self.shots)


def shot_map(details: list[dict], ouid: str, mine: bool = True) -> ShotMap:
    """여러 경기의 슛 좌표를 모은다. mine=False 면 상대 슛(내 실점 위치).

    좌표·result 가 정상 범위인 슛만 담는다 — 넥슨이 값을 비우면 건너뛴다.
    """
    sm = ShotMap()
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        p = me if mine else opp
        for sd in p.get("shootDetail") or []:
            x, y, r = sd.get("x"), sd.get("y"), sd.get("result")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            if r not in _SHOT_RESULTS:
                continue
            tname = goal_type_name(sd.get("type"))
            in_pen = bool(sd.get("inPenalty"))
            sm.shots.append(Shot(
                float(x), float(y), int(r), tname, in_pen,
                bool(sd.get("hitPost")), shot_xg(float(x), float(y), in_pen, tname)))
    return sm


# ── 선수별 결정력 랭킹 ─────────────────────────────────────────────────────
@dataclass
class PlayerFinishing:
    sp_id: int
    name: str = ""
    shots: int = 0
    on_target: int = 0   # 유효슛(골 포함) = result 1 또는 3
    goals: int = 0
    assists: int = 0     # 이 선수가 어시스트한 골 수
    xg: float = 0.0      # 기대득점 합(근사)

    @property
    def conversion(self) -> float:  # 전환율 = 골 / 슛
        return self.goals / self.shots * 100 if self.shots else 0.0

    @property
    def xg_diff(self) -> float:  # 골 − xG. +면 근사 기대보다 더 넣음(해결력/운)
        return self.goals - self.xg


def finishing_ranking(details: list[dict], ouid: str,
                      name_of=lambda i: str(i)) -> list[PlayerFinishing]:
    """내 shootDetail 을 슈터(spId)별로 모은 결정력 표. 어시스트는 골에만 준다.

    골이 많은 순 → 같으면 슛 많은 순. name_of 로 선수명을 해석한다.
    """
    acc: dict[int, PlayerFinishing] = {}

    def get(sp_id: int) -> PlayerFinishing:
        pf = acc.get(sp_id)
        if pf is None:
            pf = acc[sp_id] = PlayerFinishing(sp_id=sp_id, name=name_of(sp_id))
        return pf

    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        for sd in me.get("shootDetail") or []:
            r = sd.get("result")
            if r not in _SHOT_RESULTS:
                continue
            shooter = sd.get("spId")
            if isinstance(shooter, int):
                pf = get(shooter)
                pf.shots += 1
                if r in (SHOT_GOAL, SHOT_ON_TARGET):
                    pf.on_target += 1
                x, y = sd.get("x"), sd.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    pf.xg += shot_xg(float(x), float(y), bool(sd.get("inPenalty")),
                                     goal_type_name(sd.get("type")))
                if r == SHOT_GOAL:
                    pf.goals += 1
                    if sd.get("assist"):
                        a = sd.get("assistSpId")
                        if isinstance(a, int):
                            get(a).assists += 1
    return sorted(acc.values(), key=lambda p: (-p.goals, -p.shots))


# ── 상대 팀컬러 ───────────────────────────────────────────────────────────
# 팀컬러는 오픈API 매치 상세엔 없다(실제 캐시 JSON으로 확인 — 필드 자체가
# 없음). 넥슨 데이터센터 감독모드 랭킹(ranker.py)에서 닉네임으로 검색해야
# 나오는데, 그마저 top 10,000 랭커 안에 있을 때만 잡힌다 — 그래서 이 통계는
# "찾아지는 상대만" 반영하는 근사치다. team_color_of 는 그 조회 결과를
# 앱(app_main)이 캐시해 넘겨주는 nickname -> team_color(또는 None) 함수다.
@dataclass
class TeamColorStat:
    team_color: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    # 이 팀컬러를 쓴 상대들의 구단가치(원 단위) — 상대(닉네임)당 1개.
    # 팀가치를 모르는 상대(구버전 캐시 등)는 안 들어가므로 평균·최저·최고는
    # "팀가치를 아는 상대" 기준이다.
    team_values: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.win / self.games * 100 if self.games else 0.0

    @property
    def avg_value(self):
        return sum(self.team_values) // len(self.team_values) if self.team_values else None

    @property
    def min_value(self):
        return min(self.team_values) if self.team_values else None

    @property
    def max_value(self):
        return max(self.team_values) if self.team_values else None


def team_color_stats(matches: list, team_color_of,
                     team_value_of=None) -> list[TeamColorStat]:
    """상대 팀컬러별 내 전적 — 팀컬러를 못 찾은(top 10,000 밖) 상대는 뺀다.

    team_value_of(있으면)는 nickname -> 구단가치(원 단위 int 또는 None).
    같은 상대를 여러 번 만나도 팀가치는 한 번만 집계한다."""
    acc: dict[str, TeamColorStat] = {}
    seen_opponents: dict[str, set] = {}
    for m in matches:
        color = team_color_of(m.opponent)
        if not color:
            continue
        s = acc.setdefault(color, TeamColorStat(team_color=color))
        s.games += 1
        if "승" in m.result:
            s.win += 1
        elif "무" in m.result:
            s.draw += 1
        elif "패" in m.result:
            s.lose += 1
        if team_value_of is not None:
            seen = seen_opponents.setdefault(color, set())
            if m.opponent not in seen:
                seen.add(m.opponent)
                value = team_value_of(m.opponent)
                if value:
                    s.team_values.append(value)
    return sorted(acc.values(), key=lambda s: -s.games)


# ── 포지션별 최다 상대 선수 ─────────────────────────────────────────────────
# 정렬·색상 그룹 — widgets.PitchWidget._accent_for 의 4구간(GK 노랑 · 수비
# 1-8 파랑 · 미드필더군 9-19 초록 · 공격 20-27 빨강)과 맞춰서, 표를 봐도
# 스쿼드 화면과 같은 느낌이 나게 한다. 그룹 순서는 공격→미들→수비→GK.
def _position_group_rank(pos: int) -> int:
    if pos == GK_POSITION:
        return 3
    if 1 <= pos <= 8:
        return 2
    if 9 <= pos <= 19:
        return 1
    return 0  # 20-27 공격


@dataclass
class PositionOpponent:
    position: str
    pos_code: int    # 색상·정렬용 원본 spPosition 코드
    name: str
    sp_id: int
    count: int      # 그 선수를 만난 횟수
    total: int       # 그 포지션 자체가 등장한 총 경기 수(비율의 분모)

    @property
    def rate(self) -> float:
        return self.count / self.total * 100 if self.total else 0.0


def opponent_position_players(details: list[dict], ouid: str, name_of=None,
                              pos_name=None,
                              nicknames: set[str] | None = None
                              ) -> list[PositionOpponent]:
    """포지션별로 상대가 가장 많이 기용한 선수.

    교체 명단(SUB)은 실제로 뛴 자리가 아니라서 뺀다. nicknames 를 주면 그
    닉네임들과의 경기만 집계한다 — 팀컬러 드릴다운("이 팀컬러를 쓴 상대들은
    포지션별로 주로 누굴 쓰나")에 재사용한다.
    """
    pos_counts: dict[int, Counter] = defaultdict(Counter)
    pos_total: dict[int, int] = defaultdict(int)

    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        if nicknames is not None and (opp.get("nickname") or "-") not in nicknames:
            continue
        seen = set()
        for p in opp.get("player") or []:
            pos = p.get("spPosition")
            sp_id = p.get("spId")
            if not isinstance(pos, int) or not isinstance(sp_id, int):
                continue
            if pos == SUB_POSITION:
                continue
            pos_counts[pos][sp_id] += 1
            seen.add(pos)
        for pos in seen:
            pos_total[pos] += 1

    result = []
    for pos, counter in pos_counts.items():
        sp_id, count = counter.most_common(1)[0]
        result.append(PositionOpponent(
            position=pos_name(pos) if pos_name else str(pos), pos_code=pos,
            name=name_of(sp_id) if name_of else str(sp_id),
            sp_id=sp_id, count=count, total=pos_total[pos]))
    result.sort(key=lambda r: (_position_group_rank(r.pos_code), -r.total))
    return result
