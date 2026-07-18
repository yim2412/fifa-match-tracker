"""여러 경기를 가로질러 집계하는 통계 — 선수 지표 · 전술 · 경기 결과.

여기 있는 상수(GOAL_TYPES, 시간 구간 인코딩, 포메이션 라인)는 공식 문서에
없어서 실제 응답 100경기로 역산·검증한 값이다. 근거는 각 상수에 적어 뒀다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

SUB_POSITION = 28  # spposition 메타: 28=SUB(교체 명단)
GK_POSITION = 0

# division 메타(오픈API get_meta('division'))는 숫자가 작을수록 높은 등급이다.
#   800 슈퍼챔피언스 · 900 챔피언스 · 1000 슈퍼챌린지 · 1100~1300 챌린지1~3 · ...
# "챔피언스 이상" = 900 이하.
CHAMPION_DIVISION_ID = 900


def is_champion_or_above(division_id: int | None) -> bool:
    return division_id is not None and division_id <= CHAMPION_DIVISION_ID

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
