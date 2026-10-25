"""여러 경기를 가로질러 집계하는 통계 — 선수 지표 · 전술 · 경기 결과.

여기 있는 상수(GOAL_TYPES, 시간 구간 인코딩, 포메이션 라인)는 공식 문서에
없어서 실제 응답 100경기로 역산·검증한 값이다. 근거는 각 상수에 적어 뒀다.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
여러 경기 집계 — 선수 지표·전술·경기 결과. 역산 상수가 여기 모여 있다. 1.4.1: 패스 종류(`pass_style`) · 주별 평점(`rating_trend`) ·
랭커 비교(`ranker_targets`·`ranker_compare`·`ranker_values` — **랭커 값은 이미 경기당 평균이라 다시 나누지 않는다**, `test_ranker_values_are_per_game`) ·
슛의 어시 위치(`Shot.assist_x/y`). 2.1.1: 축구장 줄 배치(`pitch_rows` — 위=공격 → 아래=GK, 줄 안 x 순 `PITCH_X`) ·
스쿼드 가치(`squad_value` — 모르는 시세는 0 이 아니라 따로 셈) · 강화 반영 OVR(`card_ovr` — `config.GRADE_OVR_BONUS`) · 키플레이어(`key_players`)
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import NamedTuple

import config
import matchtime
import xg_grid
import xgmodel
# 등급 상수·판정은 models 에 있다(A6 의 draw_possible 이 써서) — 예전 이름 그대로 다시 내보낸다.
from models import (CHAMPION_DIVISION_ID, SUPER_CHAMPION_DIVISION_ID, RateMixin,  # noqa: F401
                    draw_possible_of, is_champion_or_above, is_mixed, rated, win_pct)

SUB_POSITION = 28  # spposition 메타: 28=SUB(교체 명단)
GK_POSITION = 0


def _set_mixed(rows, flags, mixed: bool | None):
    """묶음(표 하나)의 줄 전부에 같은 식 — mixed 를 받았으면 그 값, 아니면 묶음에 든 경기 flags 로."""
    m = is_mixed(flags) if mixed is None else mixed
    for r in rows:
        r.mixed = m
    return m


def division_trend(details: list, ouid: str) -> list[tuple[datetime, int]]:
    """경기 시점별 내 등급(division) — 오래된 경기부터 (일시, divisionId).

    매치 상세에 그 경기 당시의 division 이 박혀 있어서(_current_grade 와
    같은 필드) 이미 쌓아 둔 데이터만으로 등급 변화 이력이 나온다.
    날짜나 division 이 빠진 경기는 건너뛴다."""
    out: list[tuple[datetime, int]] = []
    for d in details:
        when = matchtime.parse(d.get("matchDate"))
        me = next((p for p in d.get("matchInfo") or []
                   if p.get("ouid") == ouid), None)
        div = me.get("division") if me else None
        if when is None or div is None:
            continue
        try:
            out.append((when, int(div)))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda t: t[0])
    return out

class DayDivision(NamedTuple):
    """하루 등급 — 그래프 점은 last, 툴팁에 first·best(id 가 작을수록 높다)."""
    day: date
    last: int
    first: int
    best: int


def daily_division(points: list[tuple[datetime, int]]) -> list[DayDivision]:
    """division_trend 결과를 하루 한 점으로 — 그날 마지막 경기 등급(오래된 날부터).

    경기마다 한 점이면 1만 경기 계정이 1만 점(R7: 10,949 → 111일)이라 계단이 읽히지 않는다."""
    days: dict[date, DayDivision] = {}
    for t, div in sorted(points, key=lambda x: x[0]):
        d = t.date()
        cur = days.get(d)
        days[d] = DayDivision(d, div, div, div) if cur is None else \
            DayDivision(d, div, cur.first, min(cur.best, div))
    return [days[d] for d in sorted(days)]


def division_entries(points: list[tuple[datetime, int]],
                     division: int = SUPER_CHAMPION_DIVISION_ID) -> tuple[list[datetime], bool]:
    """그 등급에 오른 시각들 — 시간순으로 바로 앞 경기와 다르게 그 등급이 나온 경기(오래된 것부터).

    저장된 첫 경기가 이미 그 등급이면 진입으로 세지 않는다(그 전에 언제 올랐는지 모른다) —
    대신 둘째 값이 True("기록 시작 때 이미 그 등급"). 넥슨 maxdivision 의 achievementDate 가
    이 정의의 경기 시각과 초 단위로 맞았다(1.2.1 실측)."""
    seq = sorted((t, div) for t, div in points if t is not None)
    entries: list[datetime] = []
    prev = None
    for i, (t, div) in enumerate(seq):
        if div == division and (i > 0 and prev != division):
            entries.append(t)
        prev = div
    return entries, bool(seq) and seq[0][1] == division


def season_divisions(points: list[tuple[datetime, int]],
                     groups: list[tuple[object, list]]) -> list[tuple[int, int, int] | None]:
    """시즌 그룹마다 그 시즌 (첫 경기 등급, 마지막 경기 등급, 최고 등급) — groups 와 같은 순서.

    groups 는 seasons.group_by_season 결과(시즌, 경기 목록) 그대로 — 경기 시각으로 점을 그 그룹에
    넣으므로 시즌 경계(반개구간)는 표의 행과 똑같이 갈린다. 등급 있는 점이 없으면 None."""
    owner: dict[datetime, int] = {}
    for gi, (_, ms) in enumerate(groups):
        for m in ms:
            when = getattr(m, "match_date", None)
            if when is not None:
                owner[when.replace(microsecond=0)] = gi
    acc: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    for t, div in points:
        gi = owner.get(t.replace(microsecond=0))
        if gi is not None:
            acc[gi].append((t, div))
    out: list[tuple[int, int, int] | None] = []
    for gi in range(len(groups)):
        seq = sorted(acc.get(gi, []), key=lambda x: x[0])
        out.append((seq[0][1], seq[-1][1], min(d for _, d in seq)) if seq else None)
    return out


class WinDivision(NamedTuple):
    """경기 하나 — A2 보상용. division 은 그 경기 때 내 등급(없으면 None) · outcome 은 승/무/패 · forfeit 은 몰수승·몰수패."""
    when: datetime
    division: int | None
    outcome: str
    forfeit: bool


def win_divisions(details: list, ouid: str) -> list[WinDivision]:
    """경기마다 (일시, 내 division, 결과) — 오래된 경기부터. `division_trend` 는 결과를 버려 승리를 짝지을 수 없어 따로 둔다.

    감독모드 경기만(matchType 이 있고 다르면 뺀다). 일시를 못 읽은 경기와 승·무·패가 아닌 결과("오류")는 뺀다
    — summarize 와 같은 기준(몰수승/몰수패는 승패로 친다)."""
    out: list[WinDivision] = []
    for d in details:
        mt = d.get("matchType")
        if mt is not None and mt != config.DEFAULT_MATCH_TYPE:
            continue
        me = next((p for p in d.get("matchInfo") or [] if p.get("ouid") == ouid), None)
        if me is None:
            continue
        result = (me.get("matchDetail") or {}).get("matchResult") or ""
        outcome = "승" if "승" in result else "무" if "무" in result else "패" if "패" in result else None
        when = matchtime.parse(d.get("matchDate"))
        if outcome is None or when is None:
            continue
        div = me.get("division")
        out.append(WinDivision(when, div if isinstance(div, int) else None, outcome, "몰수" in result))
    out.sort(key=lambda w: w.when)
    return out


def reward_for(division: int, day: date, table=None) -> int:
    """그날 그 등급의 승리당 FC — 표에서 시작일이 day 이하인 줄 중 가장 늦은 것. 없으면 0."""
    best = None
    for start, div, fc in (config.FC_REWARDS if table is None else table):
        if div == division and start <= day and (best is None or start >= best[0]):
            best = (start, fc)
    return best[1] if best else 0


@dataclass
class WinRewards:
    games: int = 0                  # 승·무·패 전부(분모 — 100판당)
    wins: int = 0
    rewarded_wins: int = 0          # FC 가 나온 승리
    fc: int = 0
    unknown_wins: int = 0           # division 이 빠진 승리 — 합계에 못 넣고 따로 센다
    unknown_games: int = 0          # division 이 빠진 판(승·무·패) — 100판당 분모에서도 뺀다(합계에 못 넣은 승리가 분모에만 남지 않게)
    forfeit_wins: int = 0           # 보상에 센 몰수승(사용자 확인값 — 화면 기준 글에)
    by_division: dict = field(default_factory=dict)    # division → (이긴 판, FC)

    @property
    def per_100(self) -> float:
        known = self.games - self.unknown_games
        return self.fc / known * 100 if known else 0.0


def win_rewards(rows: list[WinDivision], table=None) -> WinRewards:
    """이긴 판마다 그 경기 당시 등급 × 보상표 → 합계. 승격 경기는 따로 다루지 않는다 — 기록된 등급 그대로(U5)."""
    r = WinRewards()
    for w in rows:
        r.games += 1
        r.unknown_games += w.division is None
        if w.outcome != "승":
            continue
        r.wins += 1
        if w.division is None:
            r.unknown_wins += 1
            continue
        fc = reward_for(w.division, w.when.date(), table)
        if not fc:
            continue
        r.rewarded_wins += 1
        r.fc += fc
        r.forfeit_wins += int(w.forfeit)
        wins, total = r.by_division.get(w.division, (0, 0))
        r.by_division[w.division] = (wins + 1, total + fc)
    return r


# 슛 유형. 공식 문서에 매핑이 없어 실제 응답으로 확정했다.
#  - type 8/9 는 shoot.goalFreekick / goalPenaltyKick 집계와 200개 선수-경기
#    행에서 경기별로 정확히 일치 → 확정.
#  - 1/2/3/6/7 은 같은 계정 100경기의 유형별 골 수 대조로 합계가 전부 일치 → 확정.
#  - 4/10/13/14 는 근거가 없어 비워 둔다(= "알 수 없음").
#    14 는 슛의 약 2%로 꽤 많다(골 59% · 어시 88% · 대부분 10~25m). 2026-10-12 최근 3,000경기에서 요약(`shoot`)
#    집계 어느 것과도 안 맞았고 shootHeading·shootFreekick 에도 안 들어간다(14 가 있는 458쪽 중 0) — 이름은 못 붙였다.
#    xG 격자에는 이름이 아니라 코드로 들어가 14 도 자기 칸을 가진다.
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
_POS_LINE = {pos: i for i, (_, rng) in enumerate(_LINES) for pos in rng}  # 포지션 코드 → 라인 번호
GK_CODE = 0


def position_line(code) -> str | None:
    """포지션 코드 → 큰 묶음 "GK"·"DF"·"MF"·"FW" (선수 지표 포지션 글자색). 수미·미드·공미는 MF.
    SUB(28)·모르는 값은 None. 경계는 _LINES 의 코드 범위 그대로."""
    if code == GK_CODE:
        return "GK"
    line = _POS_LINE.get(code)
    if line is None:
        return None
    return ("DF", "MF", "MF", "MF", "FW")[line]


# ── 축구장 줄 배치(C — 스쿼드 축구장 v2) ─────────────────────────────────────
# 위(공격) → 아래(GK) 여섯 줄. 줄 안 순서는 화면 왼쪽 → 오른쪽(아래 x 값 — 옛 PitchWidget.COORDS 의 x 그대로).
# 예전엔 포지션마다 고정 좌표라 같은 줄의 RWB·RB 가 560px 에서 39px 떨어져 칩(108)이 겹쳤다(ROADMAP R8) →
# 줄마다 폭을 사람 수로 나눈다.
PITCH_ROWS = [("공격", range(20, 28)), ("공미", range(17, 20)), ("미드", range(12, 17)),
              ("수미", range(9, 12)), ("수비", range(1, 9)), ("GK", range(0, 1))]
PITCH_X = {0: 0.50, 1: 0.50, 2: 0.87, 3: 0.80, 4: 0.62, 5: 0.50, 6: 0.38, 7: 0.20, 8: 0.13,
           9: 0.65, 10: 0.50, 11: 0.35, 12: 0.87, 13: 0.62, 14: 0.50, 15: 0.38, 16: 0.13,
           17: 0.65, 18: 0.50, 19: 0.35, 20: 0.65, 21: 0.50, 22: 0.35,
           23: 0.85, 24: 0.60, 25: 0.50, 26: 0.40, 27: 0.15}
_PITCH_ROW_OF = {pos: i for i, (_, rng) in enumerate(PITCH_ROWS) for pos in rng}
# 포메이션 아이콘의 세로 자리(0 = GK · 5 = 최전방) — 줄마다 고르게 펴던 때는 4-1-2-3(공격 줄 없음)이 최전방 3명처럼,
# 풀백·윙어가 가운데로 모여 보였다(2026-10-19 사용자 "하나도 안 맞아"). 가로는 PITCH_X 를 같이 쓴다
PITCH_Y = {0: 0.0, 1: 0.7, 2: 1.6, 3: 1.15, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.15, 8: 1.6,
           9: 2.0, 10: 2.0, 11: 2.0, 12: 2.9, 13: 2.8, 14: 2.8, 15: 2.8, 16: 2.9,
           17: 3.8, 18: 3.8, 19: 3.8, 20: 4.5, 21: 4.5, 22: 4.5,
           23: 4.3, 24: 5.0, 25: 5.0, 26: 5.0, 27: 4.3}
PITCH_Y_MAX = 5.0


def pitch_rows(codes: list) -> list[list[int]]:
    """포지션 코드 목록 → 줄마다 그 줄 선수의 **인덱스**(위=공격 → 아래=GK, 줄 안은 왼쪽 → 오른쪽).
    빈 줄은 빠진다(남은 줄이 높이를 나눠 가진다). 교체(28)·모르는 코드는 어느 줄에도 안 든다.
    x 가 같은 두 자리(SW·CB 등)는 원래 순서대로."""
    rows: list[list[int]] = [[] for _ in PITCH_ROWS]
    for i, code in enumerate(codes):
        r = _PITCH_ROW_OF.get(code) if isinstance(code, int) else None
        if r is not None:
            rows[r].append(i)
    for row in rows:
        row.sort(key=lambda i: PITCH_X[codes[i]])
    return [row for row in rows if row]


@dataclass
class SquadValue:
    value: int = 0          # 아는 시세 합
    unknown: int = 0        # 시세를 모르는 카드 수
    salary: int = 0         # 아는 급여 합
    salary_unknown: int = 0


def squad_value(cards: list[tuple[int | None, int | None]]) -> SquadValue:
    """(시세, 급여) 목록 → 합. 모르는 값(None)은 합에 안 넣고 따로 센다 — 0 으로 더하면 가치가 조용히 낮아 보인다."""
    out = SquadValue()
    for price, salary in cards:
        if isinstance(price, int):
            out.value += price
        else:
            out.unknown += 1
        if isinstance(salary, int):
            out.salary += salary
        else:
            out.salary_unknown += 1
    return out


def card_ovr(base_ovr, grade) -> int | None:
    """1강 OVR + 강화 가산(config.GRADE_OVR_BONUS) — 카드 기본 포지션 기준. 모르는 강화·OVR 이면 None."""
    bonus = config.GRADE_OVR_BONUS.get(grade) if isinstance(grade, int) else None
    if not isinstance(base_ovr, int) or bonus is None:
        return None
    return base_ovr + bonus


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
    """선발 포지션 인원으로 전술 표기를 만든다. 예: 4-1-2-3-0

    선수를 한 번만 훑는다 — 라인마다 선수 전체를 다시 훑던 때는 1만 경기 흐름 분석에서
    이 함수만 0.37초였다(2026-10-02 프로파일)."""
    counts = [0] * len(_LINES)
    for p in players:
        pos = p.get("spPosition")
        if isinstance(pos, int):
            line = _POS_LINE.get(pos)
            if line is not None:
                counts[line] += 1
    return "-".join(str(c) for c in counts)


def match_formations(details: list[dict], ouid: str) -> dict[str, str]:
    """경기마다 내 포메이션 키(formation_of) — {match_id: 키}. 내 기록이 없는 경기는 빠진다(57 K1 내 포메이션 필터)."""
    out = {}
    for d in details:
        me = next((p for p in d.get("matchInfo") or [] if p.get("ouid") == ouid), None)
        if me is not None and d.get("matchId"):
            out[d["matchId"]] = formation_of(me.get("player") or [])
    return out


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


TRADE_HINT_GAMES = 300  # 내 계정 힌트 — 최근 이만큼 경기(docs/DONE.md 1.4.1 R2 와 같은 기준)


def trade_hint(details: list[dict], ouid: str, bought: set, games: int = TRADE_HINT_GAMES) -> tuple[int, int]:
    """최근 경기 스쿼드에 쓴 (카드, 강화) 중 산 기록에 있는 것 → (있는 수, 전체 수).

    (카드, 강화) 단위다 — 카드 단위면 남의 계정도 81% 가 나왔다(R3, 같은 메타 카드). 판정이 아니라 참고.
    details 는 최신순 전제."""
    keys, seen = set(), 0
    for d in details:
        if seen >= games:
            break
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        seen += 1
        for p in me.get("player") or []:
            sp, gr = p.get("spId"), p.get("spGrade")
            if sp is not None and gr is not None:
                keys.add((sp, gr))
    return len(keys & bought), len(keys)


def own_squad(details: list[dict], ouid: str):
    """가장 최근 경기에서 내 스쿼드(선수 raw 목록). opponent_squad 와 대칭.

    details 는 최신순 전제. 못 찾으면 None.
    돌려주는 값: (선수 raw 목록, 경기일 문자열, 그 경기 내 내 결과).
    """
    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        return me.get("player") or [], matchtime.label(d.get("matchDate")), _result_of(me)
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
        return opp.get("player") or [], matchtime.label(d.get("matchDate")), _result_of(me)
    return None


# ── 선수 지표 ────────────────────────────────────────────────────────────
@dataclass
class PlayerStat(RateMixin):
    sp_id: int
    name: str
    position: str
    grade: int = 0
    games: int = 0
    win: int = 0
    draw: int = 0
    pos_code: int | None = None  # 가장 자주 선 자리의 코드 — position(이름)의 출처. 포지션 색(position_line)용
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
    mixed: bool | None = None   # 선수표 하나로 정한 승률 식(보이는 승률 칸만 — 공격력·수비력의 승 항은 아님)

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

    # ── 파생 지표 — 공격력·수비력·기대득점률·선방력 ────────────────────
    # API 가 안 주는 값이라 직접 만든다. 처음엔 임의 가중치였다가 계산식을 다시
    # 정해 옮겼고, 실제 100경기 데이터로 검증했다.
    #
    # 핵심 발견: "선방력"은 defending 누적 합계가 아니라 **경기당 평균 × 100**
    # 이다. 100경기 표본에서는 나눗셈과 곱셈이 상쇄돼 합계처럼 보였을 뿐이고,
    # 경기 수가 다르면(우리 DB는 누적이라 수천 경기) 완전히 다른 값이 된다.
    @property
    def expected_goal_rate(self) -> float:
        """기대득점률 — '경기당 공격포인트(골+어시) 비율×100'. 유효슛 대비
        득점률이 아니다."""
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
    def _win_term(self) -> float:
        """5×(승/출전) — 0~5. 예전엔 5×(승률%/출전)이라 1경기 1승이 +500, 1,000경기면 +0.25 였다
        (출전이 적을수록 부풀었다 · 2026-10-07). 옮겨 온 검증 표본이 정확히 100경기라 두 식이 같은 값을 냈다.
        **승÷출전 고정** — 보이는 승률(win_pct)을 따라가면 선수마다·표마다 식이 바뀌어 점수 순위가 조용히 바뀐다(2.6.1 A6)."""
        return 5 * self.win / self.games if self.games else 0.0

    @property
    def attack_power(self) -> float:
        """공격력 = 10×기대득점률 + 패스% + 드리블% + 5×(승/출전)
        + (GK 아니면 공중볼%)."""
        score = (10 * self.expected_goal_rate + self.pass_rate + self.dribble_rate
                + self._win_term)
        if not self._is_gk():
            score += self.aerial_rate
        return score

    @property
    def defense_power(self) -> float:
        """수비력 = 패스% + 가로채기포인트 + 태클% + 2×선방력 + 블록%
        + 5×(승/출전) + (GK 아니면 공중볼%)."""
        score = (self.pass_rate + self.intercept_rate + self.tackle_rate
                + 2 * self.defending_rate + self.block_rate
                + self._win_term)
        if not self._is_gk():
            score += self.aerial_rate
        return score


def aggregate_players(details: list[dict], ouid: str,
                      name_of=None, pos_name=None, mixed: bool | None = None) -> list[PlayerStat]:
    """내 선수들의 경기별 기록을 선수 단위로 합친다.

    교체 명단(SUB)이라도 기록이 있으면 출전으로 친다 — 교체 투입된 경우.
    name_of(spId)->이름, pos_name(코드)->포지션명 은 메타를 아는 쪽에서 넘긴다.
    """
    acc: dict[int, PlayerStat] = {}
    pos_count: dict[int, Counter] = defaultdict(Counter)
    flags = []

    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        flags.append(draw_possible_of(d, ouid))
        res = _result_of(me)
        for p in me.get("player") or []:
            sp_id = p.get("spId")
            if not isinstance(sp_id, int):
                continue
            st = p.get("status") or {}
            pos = p.get("spPosition")
            if not _played(p):
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
            s.pos_code = top
    _set_mixed(acc.values(), flags, mixed)
    return sorted(acc.values(), key=lambda s: (-s.games, -s.attack_point))


# ── 전술 ────────────────────────────────────────────────────────────────
@dataclass
class FormationStat(RateMixin):
    formation: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    mixed: bool | None = None
    positions: tuple = ()   # 이 전술에서 가장 많이 선 자리 코드 묶음(정렬) — 아이콘이 실제 자리에 점을 찍는다


def formation_stats(details: list[dict], ouid: str,
                    of_opponent: bool = True, mixed: bool | None = None) -> list[FormationStat]:
    """전술별 내 승패.

    기본은 '상대 전술별 내 승률' — 상성을 보는 게 목적이라 이쪽이 쓸모 있다.
    of_opponent=False 면 내 전술 분포.
    상대가 탈주해 선수 기록이 없으면 0-0-0-0-0 으로 잡힌다.
    """
    acc: dict[str, FormationStat] = {}
    shapes: dict[str, Counter] = defaultdict(Counter)
    flags = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        flags.append(draw_possible_of(d, ouid))
        side = opp if of_opponent else me
        players = side.get("player") or []
        f = formation_of(players)
        s = acc.setdefault(f, FormationStat(formation=f))
        s.games += 1
        shapes[f][tuple(sorted(p["spPosition"] for p in players
                               if isinstance(p.get("spPosition"), int) and p["spPosition"] in PITCH_X))] += 1
        res = _result_of(me)
        if "승" in res:
            s.win += 1
        elif "무" in res:
            s.draw += 1
        elif "패" in res:
            s.lose += 1
    for f, s in acc.items():
        s.positions = max(shapes[f].items(), key=lambda kv: (kv[1], kv[0]))[0] if shapes[f] else ()
    _set_mixed(acc.values(), flags, mixed)
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
    mixed: bool | None = None   # 패널 하나(정규·연장·승부차기·몰수 줄)로 정한 승률 식

    def rate(self, wdl: list[int]) -> float:
        return win_pct(*wdl, self.mixed)


def _bump(wdl: list[int], res: str) -> None:
    if "승" in res:
        wdl[0] += 1
    elif "무" in res:
        wdl[1] += 1
    elif "패" in res:
        wdl[2] += 1


# matchEndType — 오픈API 문서(인용): 0 정상 · 1 몰수승 · 2 몰수패. 4 는 문서에 없고 실DB 에서
# "오류" 결과에만 붙는다(55팀-경기, 2026-10-05 실측). 1·2 경기의 matchResult 는 "몰수승"이 아니라
# 그냥 "승"·"패"라, 결과 문자열로는 몰수를 못 가린다(1.2.1 전 result_breakdown 의 몰수 줄이 늘 0이던 이유).
END_NORMAL, END_FORFEIT_WIN, END_FORFEIT_LOSE, END_ABORT = 0, 1, 2, 4


def _end_type(p: dict) -> int | None:
    v = (p.get("matchDetail") or {}).get("matchEndType")
    return v if isinstance(v, int) else None


def end_kind(me: dict, opp: dict) -> str:
    """한 경기의 종료 유형 — "정상"|"몰수승"|"몰수패"|"중단". 양쪽 matchEndType 을 같이 본다.

    내가 4(오류)이고 상대가 1(몰수승)이면 실제로는 몰수패다 — 내 쪽 기록은 "오류"로 남는다
    (픽스처 0004 가 이 짝, 실DB 에서 1 이 2 보다 많은 것과 맞는다)."""
    mine, theirs = _end_type(me), _end_type(opp or {})
    if mine == END_FORFEIT_WIN:
        return "몰수승"
    if mine == END_FORFEIT_LOSE:
        return "몰수패"
    if mine == END_ABORT:
        return "몰수패" if theirs == END_FORFEIT_WIN else "중단"
    return "정상"


def result_breakdown(details: list[dict], ouid: str, mixed: bool | None = None) -> ResultBreakdown:
    rb = ResultBreakdown()
    flags = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        flags.append(draw_possible_of(d, ouid))
        res = _result_of(me)
        my_shoot = me.get("shoot") or {}
        op_shoot = opp.get("shoot") or {}

        # 몰수는 결과 문자열이 아니라 종료 유형으로 센다 — 내 쪽이 "오류"인 몰수패도 패로 들어간다.
        # 그래서 이 패널 합계는 승률 분모(오류 제외)보다 그만큼 클 수 있다(의도).
        kind = end_kind(me, opp)
        if kind == "몰수승":
            rb.forfeit[0] += 1
        elif kind == "몰수패":
            rb.forfeit[2] += 1
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
    _set_mixed([rb], flags, mixed)
    return rb


# ── 규율·불운 ─────────────────────────────────────────────────────────────
# (이름, matchDetail 키) — 골대는 matchDetail 이 아니라 shootDetail[].hitPost 에 있다(None 자리).
DISCIPLINE_AXES = [
    ("파울", "foul"),
    ("경고", "yellowCards"),
    ("퇴장", "redCards"),
    ("오프사이드", "offsideCount"),
    ("코너킥", "cornerKick"),
    ("골대", None),
]
END_KINDS = ("정상", "몰수승", "몰수패", "중단")


@dataclass
class Discipline:
    """성적 진단 '규율·불운' — 나/상대 경기당 평균, 조건부 승률, 종료 유형별 경기 수."""
    games: int = 0                                     # 승·무·패 경기(평균·승률의 분모)
    axes: list[ProfileAxis] = field(default_factory=list)
    post_hit: list[int] = field(default_factory=lambda: [0, 0, 0])   # 내가 골대를 맞힌 경기 승,무,패
    my_red: list[int] = field(default_factory=lambda: [0, 0, 0])     # 내가 퇴장당한 경기
    opp_red: list[int] = field(default_factory=lambda: [0, 0, 0])    # 상대가 퇴장당한 경기
    end_kinds: Counter = field(default_factory=Counter)              # 결과와 무관하게 전 경기
    mixed: bool | None = None                                        # 조건부 승률 셋이 같은 식

    def rate(self, wdl: list[int]) -> float:
        return win_pct(*wdl, self.mixed)


def _posts(p: dict) -> int:
    return sum(1 for sd in p.get("shootDetail") or [] if sd.get("hitPost"))


def discipline_stats(details: list[dict], ouid: str, mixed: bool | None = None) -> Discipline:
    """파울·경고·퇴장·오프사이드·코너킥·골대 — 나와 상대의 경기당 평균.

    승·무·패 아닌 결과("오류")는 평균·조건부 승률에서 뺀다(team_profile 과 같은 기준).
    종료 유형 수는 그 경기들까지 센다 — 몰수패가 "오류"로 남는 경우가 있어서."""
    out = Discipline()
    sums = {side: defaultdict(float) for side in ("mine", "opp")}
    flags = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        out.end_kinds[end_kind(me, opp)] += 1
        w = _wdl_of(_result_of(me))
        if w is None:
            continue
        flags.append(draw_possible_of(d, ouid))
        out.games += 1
        for side, p in (("mine", me), ("opp", opp)):
            md = p.get("matchDetail") or {}
            for name, key in DISCIPLINE_AXES:
                sums[side][name] += _posts(p) if key is None else _num(md, key)
        if _posts(me):
            out.post_hit[w] += 1
        if _num(me.get("matchDetail") or {}, "redCards"):
            out.my_red[w] += 1
        if _num(opp.get("matchDetail") or {}, "redCards"):
            out.opp_red[w] += 1
    _set_mixed([out], flags, mixed)
    n = out.games or 1
    out.axes = [ProfileAxis(name, "회/경기", sums["mine"][name] / n, sums["opp"][name] / n)
                for name, _ in DISCIPLINE_AXES]
    return out


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
    unreplayable: int = 0   # 슛 기록 골 수가 점수판과 달라 순서를 못 되짚은 경기(대부분 자책골)
    mixed: bool | None = None   # 선제골·선제 실점 두 승률이 같은 식(입력 전체로 한 번)

    def rate(self, wdl: list[int]) -> float:
        return win_pct(*wdl, self.mixed)

    @property
    def first_scored_rate(self) -> float:
        return self.rate(self.first_scored)

    @property
    def first_conceded_rate(self) -> float:
        return self.rate(self.first_conceded)


def _timeline_matches_board(p: dict) -> bool:
    """슛 기록의 골 수가 점수판과 같은가 — 점수판이 없으면(합성 경기) 같다고 본다.

    자책골은 shootDetail 에 없고 상대 점수판에만 얹힌다(shoot.ownGoal 은 넣은 쪽에 붙는다).
    시각을 모르는 골이 하나라도 있으면 선제골·역전을 되짚을 수 없다 — 2026-10-07 실측
    감독모드 21,327경기 중 점수판 > 슛 기록 320경기, 그중 270경기가 ownGoal 로 설명됐다."""
    shoot = p.get("shoot") or {}
    board = shoot.get("goalTotalDisplay", shoot.get("goalTotal"))
    return board is None or int(board) == len(_goal_events(p))


def clutch_summary(details: list[dict], ouid: str, mixed: bool | None = None) -> ClutchSummary:
    """경기별 골 타임라인으로 선제골 여부와 역전 경기를 센다.

    양측 골이 같은 (구간, 초)로 오면(동시각) 선제골 판정을 보류하고
    무득점과 함께 goalless 로 분류한다 — 애매한 걸 억지로 한쪽에 넣지 않는다.
    골 기록이 점수판과 다른 경기(자책골 등)는 unreplayable 로 뺀다.
    """
    cs = ClutchSummary()
    _set_mixed([cs], (draw_possible_of(d, ouid) for d in details), mixed)
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        if not (_timeline_matches_board(me) and _timeline_matches_board(opp)):
            cs.unreplayable += 1
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
        # 전반은 0~44.9분으로, 후반은 45~89.9분으로 가둔다 — 그래야 전반
        # 추가시간(45분+)이 후반 첫 구간(45~60)으로 새지 않고 30~45 에 남는다.
        m = min(sec / 60, 44.99)
        minute = m + (45 if period == 1 else 0)
        return buckets[min(int(minute // 15), 5)]

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
class TimeBandRate(RateMixin):
    label: str
    span: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    mixed: bool | None = None

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


def _dated_flags(matches: list) -> list:
    return [getattr(m, "draw_possible", None) for m in matches if m.match_date is not None]


def time_of_day_rates(matches: list, mixed: bool | None = None) -> list[TimeBandRate]:
    """경기 시작 시각대별 승/무/패 — 날짜 없는 경기는 뺀다."""
    bands = [TimeBandRate(name, f"{lo:02d}~{hi:02d}") for name, lo, hi in TIME_BANDS]
    _set_mixed(bands, _dated_flags(matches), mixed)
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


WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")   # date.weekday() 순서(월=0)


def time_weekday_rates(matches: list, mixed: bool | None = None) -> list[list[TimeBandRate]]:
    """시간대(TIME_BANDS) × 요일 승/무/패 — [시간대][요일(월=0)]. 날짜 없는 경기는 뺀다.

    시각·요일은 time_of_day_rates 와 같은 match_date 기준이라 두 화면의 칸 합이 맞는다."""
    grid = [[TimeBandRate(f"{name} {wd}", f"{lo:02d}~{hi:02d}") for wd in WEEKDAYS]
            for name, lo, hi in TIME_BANDS]
    _set_mixed([c for row in grid for c in row], _dated_flags(matches), mixed)
    for m in matches:
        if m.match_date is None:
            continue
        h = m.match_date.hour
        for row, (_, lo, hi) in zip(grid, TIME_BANDS):
            if lo <= h < hi:
                cell = row[m.match_date.weekday()]
                if "승" in m.result:
                    cell.win += 1
                elif "무" in m.result:
                    cell.draw += 1
                elif "패" in m.result:
                    cell.lose += 1
                break
    return grid


# ── 연승·연패 직후 승률 ───────────────────────────────────────────────────
@dataclass
class StreakAfter(RateMixin):
    """연승/연패 length 경기(max_len 이면 그 이상) 직후의 다음 경기 승·무·패."""
    kind: str          # "승" | "패"
    length: int
    win: int = 0
    draw: int = 0
    lose: int = 0
    mixed: bool | None = None

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


def after_streak_rates(matches: list, max_len: int = 3, mixed: bool | None = None) -> list[StreakAfter]:
    """연승·연패 1..max_len(마지막은 이상) 직후 다음 경기 성적 — 승 1·2·3+, 패 1·2·3+ 순.

    연속을 세는 기준은 models.longest_streaks 와 같다(무·오류가 끊는다, 날짜 없는 경기 제외,
    입력 순서 무관). 다음 경기가 승·무·패가 아니면(오류) 분모에서 뺀다. 경기 간 시간 간격 조건은
    두지 않는다 — 60분 넘게 쉰 경기를 빼도 값이 1.7%p 안에서만 움직였다(2026-10-05 실측).
    mixed 는 견주는 기준(전체 승률)과 같은 값을 넘긴다 — "기준 대비 ±%p" 가 같은 식끼리 되게."""
    rows = {(k, n): StreakAfter(k, n) for k in ("승", "패") for n in range(1, max_len + 1)}
    dated = sorted((m for m in matches if m.match_date is not None), key=lambda m: m.match_date)
    _set_mixed(rows.values(), [m.draw_possible for m in dated], mixed)
    run_kind, run = "", 0
    for m in dated:
        w = _wdl_of(m.result)
        if run_kind and w is not None:
            row = rows[(run_kind, min(run, max_len))]
            if w == 0:
                row.win += 1
            elif w == 1:
                row.draw += 1
            else:
                row.lose += 1
        kind = "승" if w == 0 else ("패" if w == 2 else "")
        if kind and kind == run_kind:
            run += 1
        else:
            run_kind, run = kind, (1 if kind else 0)
    return list(rows.values())


# ── 성적 진단(상대 등급별 · 점유율 구간별) ───────────────────────────────────
def _wdl_of(res: str) -> int | None:
    """승/무/패를 0/1/2 로. 그 외("오류" 등)는 None — 집계에서 뺀다
    (summarize 와 같은 기준)."""
    if "승" in res:
        return 0
    if "무" in res:
        return 1
    if "패" in res:
        return 2
    return None


# division 없는 옛 경기를 모으는 버킷 — 정렬상 맨 끝(id 가장 큼)에 온다.
UNKNOWN_DIVISION_ID = 10 ** 9


@dataclass
class DivisionStat(RateMixin):
    """상대 등급(division) 하나에 대한 내 전적 — 성적 진단 탭."""
    division_id: int
    name: str
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


def division_stats(details: list[dict], ouid: str,
                   name_of=None, by_mine: bool = False, mixed: bool | None = None) -> list[DivisionStat]:
    """등급별 내 승률·평균 득실 — 강한 등급(id 작을수록 상위)부터.

    기본은 상대의 division 으로, by_mine 이면 그 경기 때 **내** division 으로 묶는다(승·무·패·득실은 둘 다 내 쪽).
    division 이 없는 옛 경기는 '미상' 버킷에 모아 합계가 맞게 둔다. 승·무·패가 아닌 결과("오류" 등)는 뺀다.
    """
    acc: dict[int, DivisionStat] = {}
    flags = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        wdl = _wdl_of(_result_of(me))
        if wdl is None:
            continue
        flags.append(draw_possible_of(d, ouid))
        div = (me if by_mine else opp).get("division")
        div_id = int(div) if isinstance(div, int) else UNKNOWN_DIVISION_ID
        s = acc.get(div_id)
        if s is None:
            if div_id == UNKNOWN_DIVISION_ID:
                nm = "미상"
            else:
                nm = name_of(div_id) if name_of else str(div_id)
            s = acc[div_id] = DivisionStat(division_id=div_id, name=nm)
        if wdl == 0:
            s.win += 1
        elif wdl == 1:
            s.draw += 1
        else:
            s.lose += 1
        s.goals_for += int(_num(me.get("shoot") or {}, "goalTotal"))
        s.goals_against += int(_num(opp.get("shoot") or {}, "goalTotal"))
    _set_mixed(acc.values(), flags, mixed)
    return [acc[k] for k in sorted(acc.keys())]


# 내 점유율 3구간 — "지공(우세)이 맞나 역습(열세)이 맞나".
POSSESSION_BANDS = [("열세", 0, 40), ("균형", 40, 61), ("우세", 61, 101)]


@dataclass
class PossessionBand(RateMixin):
    """점유율 한 구간의 내 전적."""
    label: str
    span: str
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


def possession_stats(details: list[dict], ouid: str, mixed: bool | None = None) -> list[PossessionBand]:
    """내 점유율 구간(열세<40 · 균형40~60 · 우세>60)별 승률·평균 득실.

    점유율 0(상대 탈주 등 무기록)과 승·무·패 아닌 결과는 뺀다.
    """
    bands = [PossessionBand(name, f"{lo}~{hi - 1}")
             for name, lo, hi in POSSESSION_BANDS]
    flags = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        wdl = _wdl_of(_result_of(me))
        if wdl is None:
            continue
        pos = int(_num(me.get("matchDetail") or {}, "possession"))
        if pos <= 0:
            continue
        flags.append(draw_possible_of(d, ouid))
        for band, (_, lo, hi) in zip(bands, POSSESSION_BANDS):
            if lo <= pos < hi:
                if wdl == 0:
                    band.win += 1
                elif wdl == 1:
                    band.draw += 1
                else:
                    band.lose += 1
                band.goals_for += int(_num(me.get("shoot") or {}, "goalTotal"))
                band.goals_against += int(_num(opp.get("shoot") or {}, "goalTotal"))
                break
    _set_mixed(bands, flags, mixed)
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

# ── 기대득점(xG) ──────────────────────────────────────────────────────────
# ⚠️ 넥슨은 xG 정답값을 주지 않는다 — 비공식 추정이다.
# 2.9.1 부터 실제 경기 슛으로 세운 격자 표(골라인 거리 5m × 중앙 폭 5m × 슛 종류)를 쓴다 — 표는 xg_grid.py
# (생성물 · tools/fit_xg.py --write), 읽기는 xgmodel.predict. 그전 식(거리·각도 로지스틱 + 헤더 ×0.7)은 평균은
# 맞았지만 칸마다 어긋났다(검증 슛 ECE 6.9%p → 격자 0.8%p · ROADMAP 43 측정 2). 옛 식은 fit_xg.c0_frozen 에 얼려 있다.
_PITCH_LEN = 105.0   # m — x(0~1)가 경기장 전체 길이 비율
_PITCH_WID = 68.0    # m — y(0~1)가 폭 비율, 0.5 가 중앙


def shot_type_code(t) -> int | None:
    """payload 의 슛 type → shot_xg 에 넘길 코드. 정수가 아니면 None('기타' 칸) — 넥슨이 형을 바꿔도
    (예: "9") 슛 맵 전체가 예외로 죽지 않고 그 슛만 기타 칸 값이 된다(전역 규칙 7 · 43 M6)."""
    return t if isinstance(t, int) and not isinstance(t, bool) else None


def shot_xg(x: float, y: float, in_penalty: bool = False, type_code: int | None = None) -> float:
    """슛 하나의 기대득점(0~1). type_code 는 넥슨 슛 종류 **코드**(shot_type_code 로 거른 값).
    in_penalty 는 격자가 안 쓴다(좌표 칸이 이미 박스를 가른다) — 호출 꼴을 지키려고 남겼다.
    이름 문자열을 넘기면 TypeError — 옛 호출(goal_type_name)이 남아 있으면 예외 없이 '기타' 칸 값이 나와
    조용히 틀리므로 개발 중에 터지게 한다(43 3회차 A[중]2). 넥슨 값은 호출부가 shot_type_code 로 거른다."""
    if isinstance(type_code, str):
        raise TypeError(f"shot_xg 는 슛 종류 코드(int)를 받는다 — 이름 {type_code!r} 을 넘겼다")
    if type_code == xg_grid.PK_TYPE:
        return xg_grid.PK_RATE       # 위치와 무관한 상수 — 표와 같은 학습분의 성공 비율
    return xgmodel.predict(xg_grid.SHOT, x, y, type_code)


@dataclass
class Shot:
    x: float
    y: float
    result: int          # SHOT_GOAL / SHOT_ON_TARGET / SHOT_OFF_TARGET
    type_name: str
    in_penalty: bool = False
    hit_post: bool = False
    xg: float = 0.0
    # 어시스트 위치(1.4.1 N6) — 넥슨이 assist=True 로 준 슛만(골의 90%, 실측 R11). 없으면 None
    assist_x: float | None = None
    assist_y: float | None = None
    # 슛 종류 코드(2.9.1 — xG 격자 칸). 이름(type_name)은 모르는 코드를 "알 수 없음" 하나로 뭉친다.
    # 맨 끝 · 기본값 있음 — 위치 인자로 Shot 을 만드는 곳(테스트)이 그대로 돈다
    type_code: int | None = None


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


def shot_map(details: list[dict], ouid: str, mine: bool = True,
             sp_id: int | None = None) -> ShotMap:
    """여러 경기의 슛 좌표를 모은다. mine=False 면 상대 슛(내 실점 위치).

    좌표·result 가 정상 범위인 슛만 담는다 — 넥슨이 값을 비우면 건너뛴다.
    sp_id 를 주면 그 카드가 찬 슛만(선수 카드 '내 기록') — 시즌이 다른 같은 선수는 다른 spId 라 따로다.
    """
    sm = ShotMap()
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        p = me if mine else opp
        for sd in p.get("shootDetail") or []:
            if sp_id is not None and sd.get("spId") != sp_id:
                continue
            x, y, r = sd.get("x"), sd.get("y"), sd.get("result")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            if r not in _SHOT_RESULTS:
                continue
            tname = goal_type_name(sd.get("type"))
            tcode = shot_type_code(sd.get("type"))
            in_pen = bool(sd.get("inPenalty"))
            ax, ay = sd.get("assistX"), sd.get("assistY")
            has_assist = (bool(sd.get("assist")) and isinstance(ax, (int, float))
                          and isinstance(ay, (int, float)))
            sm.shots.append(Shot(
                float(x), float(y), int(r), tname, in_pen,
                bool(sd.get("hitPost")), shot_xg(float(x), float(y), in_pen, tcode),
                float(ax) if has_assist else None, float(ay) if has_assist else None, tcode))
    return sm


# ── 슛 유형·거리별 효율 ────────────────────────────────────────────────────
# 이미 만들어 둔 ShotMap 만 쓰는 순수 계산 — 추가 조회도 새 파싱도 없다.
#
# ⚠️ result_breakdown 의 goal_types 와 헷갈리면 안 된다. 저건 "넣은 골의 유형
# 분포"(분모=골)고, 여기는 "그 유형으로 찼을 때 들어가는 비율"(분모=슛)이다.
# 헤딩골이 전체 골의 10%라는 것과 헤딩 슛의 20%가 들어간다는 건 다른 말이다.
MIN_BUCKET_SHOTS = 5   # 이보다 적으면 비율을 숨긴다 — 1/2=50% 는 정보가 아니다

# 거리 구간. 박스 안 여부는 좌표로 재지 않고 넥슨이 주는 inPenalty 를 그대로
# 믿는다(직접 주는 값이라 좌표 환산보다 정확하다). 박스 밖만 골라인까지의
# 거리로 다시 두 칸으로 쪼갠다.
FAR_SHOT_M = 25.0
DIST_IN_BOX = "박스 안"
DIST_MID = f"박스 밖 ~{FAR_SHOT_M:.0f}m"
DIST_FAR = f"{FAR_SHOT_M:.0f}m 이상"
_DIST_ORDER = (DIST_IN_BOX, DIST_MID, DIST_FAR)


@dataclass
class ShotBucket:
    """한 구간(유형 하나·거리 하나)의 슛 성적."""
    label: str
    shots: int = 0
    goals: int = 0
    on_target: int = 0   # 골 포함 유효슛
    xg: float = 0.0

    @property
    def enough(self) -> bool:
        """비율을 말해도 되는 표본인가."""
        return self.shots >= MIN_BUCKET_SHOTS

    @property
    def conversion(self) -> float:  # 득점률 = 골 / 슛
        return self.goals / self.shots * 100 if self.shots else 0.0

    @property
    def effective_rate(self) -> float:  # 유효슛률 = (골+막힌 유효슛) / 슛
        return self.on_target / self.shots * 100 if self.shots else 0.0

    @property
    def xg_diff(self) -> float:  # +면 근사 기대보다 더 넣은 것
        return self.goals - self.xg


def shot_distance_m(s: Shot) -> float:
    """슛 지점에서 골라인까지의 거리(m). 좌우로 벗어난 폭까지 포함한다."""
    dx = max(0.0, (1.0 - s.x)) * _PITCH_LEN
    dy = abs(s.y - 0.5) * _PITCH_WID
    return math.hypot(dx, dy)


def _distance_label(s: Shot) -> str:
    if s.in_penalty:
        return DIST_IN_BOX
    return DIST_MID if shot_distance_m(s) < FAR_SHOT_M else DIST_FAR


def _bucketize(shots: list, key) -> dict[str, ShotBucket]:
    buckets: dict[str, ShotBucket] = {}
    for s in shots:
        b = buckets.setdefault(key(s), ShotBucket(key(s)))
        b.shots += 1
        b.xg += s.xg
        if s.result == SHOT_GOAL:
            b.goals += 1
            b.on_target += 1
        elif s.result == SHOT_ON_TARGET:
            b.on_target += 1
    return buckets


def shot_type_breakdown(sm: ShotMap) -> list[ShotBucket]:
    """슛 유형별 효율. 슛이 많은 순 — 실제로 자주 쓰는 수단부터 보게."""
    buckets = _bucketize(sm.shots, lambda s: s.type_name)
    return sorted(buckets.values(), key=lambda b: (-b.shots, b.label))


def shot_distance_breakdown(sm: ShotMap) -> list[ShotBucket]:
    """거리별 효율. 가까운 순 고정 — 순서가 바뀌면 구간 비교가 안 된다."""
    buckets = _bucketize(sm.shots, _distance_label)
    return [buckets[k] for k in _DIST_ORDER if k in buckets]


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


# 선수 카드 '내 기록' — 주 단위 결정력 추이. 슛이 이보다 적으면 추이를 그리지 않는다(흐름 분석과 같은 원칙:
# 표본이 모자라면 침묵). 주 하나의 비율도 슛 MIN_BUCKET_SHOTS 미만이면 점만 찍고 선으로 잇지 않는다.
PLAYER_TREND_MIN_SHOTS = 20
# 평점 추이(1.4.1 N9) — 그 주 출전이 이보다 적으면 그 주 점을 흐리게(평균 하나가 한두 경기에 흔들린다)
PLAYER_RATING_MIN_GAMES = 5


@dataclass
class WeekFinishing:
    week_start: date          # 그 주 월요일
    games: int = 0            # 그 카드가 선발·교체로 나온 경기(SUB 대기만은 제외)
    shots: int = 0
    on_target: int = 0
    goals: int = 0
    xg: float = 0.0

    @property
    def conversion(self) -> float:
        return self.goals / self.shots * 100 if self.shots else 0.0

    @property
    def label(self) -> str:
        return f"{self.week_start:%m/%d}~"


def _played(p: dict) -> bool:
    """그 경기에 실제로 뛰었나 — 선발이거나, 교체 명단(SUB)이라도 기록이 남았으면(들어왔다)."""
    if p.get("spPosition") != SUB_POSITION:
        return True
    st = p.get("status") or {}
    return any(_num(st, k) for k in ("shoot", "passTry", "tackleTry", "spRating"))


def _match_day(d: dict) -> date | None:
    return matchtime.day(d.get("matchDate"))


def player_finishing_trend(details: list[dict], ouid: str, sp_id: int) -> list[WeekFinishing]:
    """그 카드(spId)의 주별 출전·슛·골·xG — 오래된 주부터. 나온 주만(빈 주는 없다)."""
    weeks: dict[date, WeekFinishing] = {}
    for d in details:
        me, _ = _me_opp(d, ouid)
        day = _match_day(d)
        if me is None or day is None:
            continue
        played = any(p.get("spId") == sp_id and _played(p) for p in me.get("player") or [])
        shots = [sd for sd in me.get("shootDetail") or []
                 if sd.get("spId") == sp_id and sd.get("result") in _SHOT_RESULTS]
        if not played and not shots:
            continue
        start = day - timedelta(days=day.weekday())
        w = weeks.setdefault(start, WeekFinishing(week_start=start))
        w.games += 1 if played else 0
        for sd in shots:
            r = sd.get("result")
            w.shots += 1
            if r in (SHOT_GOAL, SHOT_ON_TARGET):
                w.on_target += 1
            if r == SHOT_GOAL:
                w.goals += 1
            x, y = sd.get("x"), sd.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                w.xg += shot_xg(float(x), float(y), bool(sd.get("inPenalty")),
                                shot_type_code(sd.get("type")))
    return [weeks[k] for k in sorted(weeks)]


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
                                     shot_type_code(sd.get("type")))
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
class TeamColorStat(RateMixin):
    team_color: str
    games: int = 0
    win: int = 0
    draw: int = 0
    lose: int = 0
    # 이 팀컬러를 쓴 상대들의 구단가치(원 단위) — 상대(닉네임)당 1개.
    # 팀가치를 모르는 상대(구버전 캐시 등)는 안 들어가므로 평균·최저·최고는
    # "팀가치를 아는 상대" 기준이다.
    team_values: list = field(default_factory=list)
    opponents: set = field(default_factory=set)   # 서로 다른 상대 닉네임 — 한 사람과의 반복 대전을 가려낸다(2.2.1)
    mixed: bool | None = None

    @property
    def avg_value(self):
        return sum(self.team_values) // len(self.team_values) if self.team_values else None

    @property
    def min_value(self):
        return min(self.team_values) if self.team_values else None

    @property
    def max_value(self):
        return max(self.team_values) if self.team_values else None


def team_color_mixed(matches: list, team_color_of) -> bool:
    """팀컬러 표 묶음(팀컬러를 아는 경기)의 승률 식 — 표 줄과 "평균 대비" 기준이 같은 값을 쓴다."""
    return is_mixed(m.draw_possible for m in matches if team_color_of(m.opponent))


def team_color_stats(matches: list, team_color_of,
                     team_value_of=None, mixed: bool | None = None) -> list[TeamColorStat]:
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
        s.opponents.add(m.opponent)   # 팀가치 인자와 상관없이 센다
        if team_value_of is not None:
            seen = seen_opponents.setdefault(color, set())
            if m.opponent not in seen:
                seen.add(m.opponent)
                value = team_value_of(m.opponent)
                if value:
                    s.team_values.append(value)
    m = team_color_mixed(matches, team_color_of) if mixed is None else mixed
    for s in acc.values():
        s.mixed = m
    return sorted(acc.values(), key=lambda s: -s.games)


def team_color_baseline(matches: list, team_color_of, mixed: bool | None = None) -> tuple[int, float]:
    """팀컬러 표의 "평균 대비" 기준 — 팀컬러를 아는 경기만의 (승률 분모 rated, 승률 %). 범위 전체로 잡으면 1만 위 밖 상대와의
    경기가 기준에만 들어가 거의 모든 팀컬러가 "평균 아래"가 된다(ROADMAP 2.2.1 ⑩). team_color_stats 와 같은 셈·같은 식."""
    wdl = [0, 0, 0]
    for m in matches:
        if not team_color_of(m.opponent):
            continue
        k = _wdl_of(m.result)
        if k is not None:
            wdl[k] += 1
    mixed = team_color_mixed(matches, team_color_of) if mixed is None else mixed
    return rated(*wdl, mixed), win_pct(*wdl, mixed)


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


# 좌우 짝 자리 — 한 줄에서 한쪽만 다른 줄 자리(RM 과 LAM)가 섞인 "자리 구성"은 정석이 아니라 고르지 않는다(사용자 2026-10-11)
_MIRROR = {2: 8, 3: 7, 4: 6, 9: 11, 12: 16, 13: 15, 17: 19, 20: 22, 23: 27, 24: 26}
_MIRROR.update({v: k for k, v in list(_MIRROR.items())})
LINEUP_SIZE = 11   # 선발 인원 — 탈주로 모자란 경기는 자리 구성을 정하는 데 쓰지 않는다


@dataclass
class FormationUsage:
    formation: str   # formation_of 표기(수비-수미-미드-공미-공격) — 전술 탭과 같은 키
    games: int       # 그 포메이션을 쓴 경기 수
    total: int       # 상대 선발이 있는 경기 수(사용률의 분모)

    @property
    def rate(self) -> float:
        return self.games / self.total * 100 if self.total else 0.0

    @property
    def label(self) -> str:
        return formation_label(self.formation)


def formation_label(formation: str) -> str:
    """"4-1-4-0-1" → "4-1-4-1" — 비어 있는 줄은 게임 표기처럼 뺀다. 줄 키(formation_of)는 그대로 두고 화면 글자만."""
    return "-".join(g for g in formation.split("-") if g != "0") or formation


def _starters(opp: dict) -> list[int]:
    """상대 선발 자리 코드(교체 명단·모르는 값은 뺀다)."""
    return [p["spPosition"] for p in (opp.get("player") or [])
            if isinstance(p.get("spPosition"), int) and p["spPosition"] != SUB_POSITION
            and isinstance(p.get("spId"), int)]


def _opp_scope(details: list[dict], ouid: str, nicknames: set[str] | None):
    """(내 기록이 있고, 지정한 닉네임이면) 상대 한 줄씩 — 포메이션·자리 구성 집계가 같은 경기 범위를 쓴다."""
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        if nicknames is not None and (opp.get("nickname") or "-") not in nicknames:
            continue
        yield opp


def opponent_formations(details: list[dict], ouid: str, nicknames: set[str] | None = None) -> list[FormationUsage]:
    """상대가 쓴 포메이션을 쓴 경기 수 내림차순으로. 선발 기록이 없는 경기(상대 탈주)는 세지 않는다.
    같은 경기 수면 표기 글자 순 — 순서가 실행마다 달라지지 않게."""
    counts: Counter = Counter()
    for opp in _opp_scope(details, ouid, nicknames):
        if _starters(opp):
            counts[formation_of(opp.get("player") or [])] += 1
    total = sum(counts.values())
    return [FormationUsage(f, n, total) for f, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _symmetric(positions: tuple[int, ...]) -> bool:
    have = set(positions)
    return all(_MIRROR[p] in have for p in positions if p in _MIRROR)


def common_lineup(details: list[dict], ouid: str, formation: str,
                  nicknames: set[str] | None = None) -> tuple[int, ...] | None:
    """그 포메이션에서 가장 많이 나온 11자리 구성(정렬한 자리 코드 묶음). 좌우가 맞는 구성을 먼저 — 하나도 없으면 좌우 무시.
    선발 11명이 안 되는 경기는 후보에서 뺀다(그것뿐이면 그 경기라도). 같은 횟수면 코드가 작은 쪽."""
    full: Counter = Counter()
    short: Counter = Counter()
    for opp in _opp_scope(details, ouid, nicknames):
        if formation_of(opp.get("player") or []) != formation:
            continue
        key = tuple(sorted(_starters(opp)))
        (full if len(key) == LINEUP_SIZE else short)[key] += 1
    for pool in (Counter({k: v for k, v in full.items() if _symmetric(k)}), full, short):
        if pool:
            return min(pool.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return None


def opponent_position_players(details: list[dict], ouid: str, name_of=None,
                              pos_name=None,
                              nicknames: set[str] | None = None,
                              formation: str | None = None
                              ) -> list[PositionOpponent]:
    """포지션별로 상대가 가장 많이 기용한 선수.

    교체 명단(SUB)은 실제로 뛴 자리가 아니라서 뺀다. nicknames 를 주면 그
    닉네임들과의 경기만 집계한다 — 팀컬러 드릴다운("이 팀컬러를 쓴 상대들은
    포지션별로 주로 누굴 쓰나")에 재사용한다.
    formation(formation_of 표기)을 주면 그 포메이션의 가장 흔한 자리 구성(common_lineup)으로 나온 경기만 센다 —
    결과가 그 구성의 자리 수(보통 11)를 넘지 않는다.
    """
    lineup = common_lineup(details, ouid, formation, nicknames) if formation is not None else None
    if formation is not None and lineup is None:
        return []
    pos_counts: dict[int, Counter] = defaultdict(Counter)
    pos_total: dict[int, int] = defaultdict(int)

    for opp in _opp_scope(details, ouid, nicknames):
        if lineup is not None and (formation_of(opp.get("player") or []) != formation
                                   or tuple(sorted(_starters(opp))) != lineup):
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


@dataclass
class OppCard(RateMixin):
    sp_id: int
    met: int            # 그 자리에 그 카드가 선발로 나온 판
    pos_games: int      # 그 자리가 나온 판(사용률의 분모)
    win: int
    draw: int
    lose: int
    mixed: bool | None = None   # 카드 표 하나(nicknames 로 거른 경기)로 정한 식

    @property
    def usage(self) -> float:
        return self.met * 100 / self.pos_games if self.pos_games else 0.0


def opponent_cards(details: list[dict], ouid: str, nicknames: set[str] | None = None,
                   mixed: bool | None = None) -> dict[int, list[OppCard]]:
    """N7 상대가 쓰는 카드 — {자리 코드: [OppCard] 만난 판 내림차순}. 교체 명단 뺌(opponent_position_players 와 같다).
    nicknames 를 주면 그 상대와의 경기만(팀컬러 · 1만 위 안 상대)."""
    met: dict[int, dict[int, list[int]]] = defaultdict(dict)
    pos_games: dict[int, int] = defaultdict(int)
    flags = []
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        if nicknames is not None and (opp.get("nickname") or "-") not in nicknames:
            continue
        flags.append(draw_possible_of(d, ouid))
        res = _result_of(me)
        k = 0 if "승" in res else 1 if "무" in res else 2 if "패" in res else None
        seen: set[tuple[int, int]] = set()
        for p in opp.get("player") or []:
            pos, sp_id = p.get("spPosition"), p.get("spId")
            if not isinstance(pos, int) or not isinstance(sp_id, int) or pos == SUB_POSITION or (pos, sp_id) in seen:
                continue
            seen.add((pos, sp_id))
            c = met[pos].setdefault(sp_id, [0, 0, 0, 0])
            c[0] += 1
            if k is not None:
                c[1 + k] += 1
        for pos in {pos for pos, _sp in seen}:
            pos_games[pos] += 1
    out: dict[int, list[OppCard]] = {}
    m = is_mixed(flags) if mixed is None else mixed
    for pos, cards in met.items():
        rows = [OppCard(sp, n, pos_games[pos], w, dr, lo, m) for sp, (n, w, dr, lo) in cards.items()]
        rows.sort(key=lambda c: (-c.met, c.sp_id))
        out[pos] = rows
    return dict(sorted(out.items(), key=lambda kv: (_position_group_rank(kv[0]), kv[0])))


# ── 키플레이어(구단주 비교 ①) ─────────────────────────────────────────────
KEY_PLAYER_TOP = 3
KEY_PLAYER_MIN_SHARE = 0.3   # 평점 상위는 비교 경기의 이 비율 이상 뛴 선수만 — 한두 경기 반짝 평점이 위로 오지 않게


@dataclass
class KeyPlayers:
    by_goal: list[PlayerStat] = field(default_factory=list)     # 골 1 이상 · 골 → 출전 적은 순
    by_rating: list[PlayerStat] = field(default_factory=list)   # 출전 문턱 이상 · 평균 평점 순
    min_games: int = 0


def key_players(details: list[dict], ouid: str, name_of=None, pos_name=None) -> KeyPlayers:
    """그 계정의 골·평점 상위 KEY_PLAYER_TOP 명 — details 는 비교에 쓰는 경기만 넘긴다."""
    players = aggregate_players(details, ouid, name_of=name_of, pos_name=pos_name)
    games = sum(1 for d in details if _me_opp(d, ouid)[0] is not None)
    min_games = max(1, math.ceil(games * KEY_PLAYER_MIN_SHARE))
    by_goal = sorted((p for p in players if p.goal > 0), key=lambda p: (-p.goal, p.games, p.sp_id))
    by_rating = sorted((p for p in players if p.games >= min_games), key=lambda p: (-p.rating, -p.games, p.sp_id))
    return KeyPlayers(by_goal[:KEY_PLAYER_TOP], by_rating[:KEY_PLAYER_TOP], min_games)


# ── 나 vs 상대 프로필(대시보드 레이더) ─────────────────────────────────────
# (축 이름, 단위) — 순서가 레이더의 시계 방향 순서다. 전부 "높을수록 좋은" 값만
# 둔다: 실점처럼 낮을수록 좋은 축이 섞이면 같은 모양이 축마다 반대 뜻이 된다.
PROFILE_AXES = [
    ("슈팅", "개/경기"),
    ("유효슈팅률", "%"),
    ("득점", "골/경기"),
    ("점유율", "%"),
    ("패스 성공률", "%"),
    ("태클 성공률", "%"),
]


@dataclass
class ProfileAxis:
    name: str
    unit: str
    mine: float = 0.0
    opp: float = 0.0

    @property
    def share(self) -> float:
        """내 몫 mine/(mine+opp) — 0.5 가 동률. 단위가 다른 축을 한 눈금에
        놓으려고 쓴다. 둘 다 0 이면 동률로 본다."""
        tot = self.mine + self.opp
        return self.mine / tot if tot else 0.5


@dataclass
class TeamProfile:
    games: int = 0
    axes: list[ProfileAxis] = field(default_factory=list)


def _ratio(num: float, den: float) -> float:
    return num / den * 100 if den else 0.0


def team_profile(details: list[dict], ouid: str) -> TeamProfile:
    """같은 경기들에서 나와 상대(그 경기 상대들의 평균)를 6축으로 비교한다.

    승·무·패가 아닌 경기("오류" 등)는 summarize 와 같은 기준으로 뺀다.
    비율 축(유효슈팅률·패스·태클)은 경기별 비율의 평균이 아니라 전체 합의
    비율이다 — 슈팅 1개짜리 경기가 100% 로 평균을 흔들지 않게.
    """
    sums = {side: defaultdict(float) for side in ("mine", "opp")}
    games = 0
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None or _wdl_of(_result_of(me)) is None:
            continue
        games += 1
        for side, p in (("mine", me), ("opp", opp)):
            shoot = p.get("shoot") or {}
            pas = p.get("pass") or {}
            dfn = p.get("defence") or {}
            s = sums[side]
            s["shots"] += _num(shoot, "shootTotal")
            s["on_target"] += _num(shoot, "effectiveShootTotal")
            s["goals"] += _num(shoot, "goalTotal")
            s["possession"] += _num(p.get("matchDetail") or {}, "possession")
            s["pass_try"] += _num(pas, "passTry")
            s["pass_ok"] += _num(pas, "passSuccess")
            s["tackle_try"] += _num(dfn, "tackleTry")
            s["tackle_ok"] += _num(dfn, "tackleSuccess")
    prof = TeamProfile(games=games)
    if not games:
        return prof

    def values(s: dict) -> list[float]:
        return [s["shots"] / games,
                _ratio(s["on_target"], s["shots"]),
                s["goals"] / games,
                s["possession"] / games,
                _ratio(s["pass_ok"], s["pass_try"]),
                _ratio(s["tackle_ok"], s["tackle_try"])]

    for (name, unit), m, o in zip(PROFILE_AXES, values(sums["mine"]), values(sums["opp"])):
        prof.axes.append(ProfileAxis(name, unit, m, o))
    return prof


# ── 평점 추이(1.4.1 N9) ────────────────────────────────────────────────────
@dataclass
class WeekRating:
    week_start: date          # 그 주 월요일
    games: int = 0            # 그 카드가 뛴 경기(교체 대기만은 제외 — _played)
    rating_sum: float = 0.0

    @property
    def rating(self) -> float:
        return self.rating_sum / self.games if self.games else 0.0

    @property
    def weak(self) -> bool:
        return self.games < PLAYER_RATING_MIN_GAMES

    @property
    def label(self) -> str:
        return f"{self.week_start:%m/%d}~"


def rating_trend(details: list[dict], ouid: str, sp_id: int) -> list[WeekRating]:
    """그 카드(spId)의 주별 평균 spRating — 오래된 주부터, 나온 주만. spRating 은 출전 기록 전부에 있다(R12)."""
    weeks: dict[date, WeekRating] = {}
    for d in details:
        me, _ = _me_opp(d, ouid)
        day = _match_day(d)
        if me is None or day is None:
            continue
        for p in me.get("player") or []:
            if p.get("spId") != sp_id or not _played(p):
                continue
            start = day - timedelta(days=day.weekday())
            w = weeks.setdefault(start, WeekRating(week_start=start))
            w.games += 1
            w.rating_sum += _num(p.get("status") or {}, "spRating")
            break  # 한 경기에 같은 카드는 한 장
    return [weeks[k] for k in sorted(weeks)]


# ── 패스 스타일(1.4.1 N5) ──────────────────────────────────────────────────
# 경기 상세 pass 의 종류별 시도/성공(R10 — 거의 모든 경기에 있다). (화면 이름, 필드 앞부분)
PASS_KINDS = (
    ("짧은 패스", "shortPass"),
    ("롱 패스", "longPass"),
    ("바운스 로브", "bouncingLobPass"),
    ("낮고 빠른 패스", "drivenGroundPass"),
    ("스루 패스", "throughPass"),
    ("로빙 스루", "lobbedThroughPass"),
)


PASS_MIN_TRIES = 20   # 종류 하나의 시도가 이보다 적으면 성공률을 숨긴다(바운스 로브 같은 드문 종류 — 3/5=60% 는 정보가 아니다)


@dataclass
class PassKind:
    name: str
    tries: float = 0.0
    success: float = 0.0

    @property
    def rate(self) -> float:
        return self.success / self.tries * 100 if self.tries else 0.0


@dataclass
class PassGroup:
    """한 묶음(전체 · 이긴 경기 · 진 경기)의 종류별 패스 — 비중은 이 묶음 종류 합 대비."""
    label: str
    games: int = 0
    kinds: list[PassKind] = field(default_factory=lambda: [PassKind(n) for n, _ in PASS_KINDS])

    @property
    def total(self) -> float:
        return sum(k.tries for k in self.kinds)

    def share(self, k: PassKind) -> float:
        return k.tries / self.total * 100 if self.total else 0.0

    def per_game(self, k: PassKind) -> float:
        return k.tries / self.games if self.games else 0.0


@dataclass
class PassStyle:
    all: PassGroup
    win: PassGroup
    lose: PassGroup
    skipped: int = 0   # 종류 필드가 없어 건너뛴 경기


def pass_style(details: list[dict], ouid: str) -> PassStyle:
    """내 패스 종류 6가지의 비중·성공률 — 전체와 이긴/진 경기. 승·무·패가 아닌 경기와 종류 필드가 없는 경기는 뺀다."""
    style = PassStyle(PassGroup("전체"), PassGroup("이긴 경기"), PassGroup("진 경기"))
    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        wdl = _wdl_of(_result_of(me))
        if wdl is None:
            continue
        pas = me.get("pass") or {}
        if not any(f"{key}Try" in pas for _, key in PASS_KINDS):
            style.skipped += 1
            continue
        groups = [style.all] + ([style.win] if wdl == 0 else [style.lose] if wdl == 2 else [])
        for g in groups:
            g.games += 1
            for k, (_, key) in zip(g.kinds, PASS_KINDS):
                k.tries += _num(pas, f"{key}Try")
                k.success += _num(pas, f"{key}Success")
    return style


# ── 내 선수 vs 랭커(1.4.1 N1) ──────────────────────────────────────────────
# 비교는 랭커 응답에 있는 항목만(R8 — 평점은 랭커 쪽에 없다). (이름, 경기당 필드 | (성공, 시도) 비율)
RANKER_METRICS = (
    ("슛", "shoot"),
    ("유효슛", "effectiveShoot"),
    ("골", "goal"),
    ("어시", "assist"),
    ("드리블 성공률", ("dribbleSuccess", "dribbleTry")),
    ("패스 성공률", ("passSuccess", "passTry")),
    ("태클", "tackle"),
    ("블록", "block"),
)


@dataclass
class RankerRow:
    sp_id: int
    pos: int
    games: int                         # 내가 그 카드를 그 자리에 세운 경기
    ranker_games: int = 0              # 랭커 표본(matchCount) — 0 이면 랭커 기록 없음
    ranker_date: str = ""              # 랭커 기준일(createDate 앞 10자) — 카드마다 다르다
    mine: dict = field(default_factory=dict)      # 지표 이름 → 경기당 값(비율은 %)
    ranker: dict = field(default_factory=dict)    # 같은 꼴, 랭커 기록이 없으면 비었다


def _my_pair_sums(details: list[dict], ouid: str) -> dict[tuple[int, int], dict]:
    """(spId, 선 자리) → 합계 — 교체 명단(28)은 자리가 없어 뺀다."""
    acc: dict[tuple[int, int], dict] = {}
    for d in details:
        me, _ = _me_opp(d, ouid)
        if me is None:
            continue
        for p in me.get("player") or []:
            sp_id, pos = p.get("spId"), p.get("spPosition")
            if not isinstance(sp_id, int) or not isinstance(pos, int) or pos == SUB_POSITION:
                continue
            st = p.get("status") or {}
            a = acc.setdefault((sp_id, pos), defaultdict(float))
            a["games"] += 1
            for _, f in RANKER_METRICS:
                for key in (f if isinstance(f, tuple) else (f,)):
                    a[key] += _num(st, key)
    return acc


def ranker_targets(details: list[dict], ouid: str, limit: int | None = None) -> list[tuple[int, int]]:
    """물어볼 (spId, 자리) — 카드마다 가장 많이 선 자리 하나, 많이 쓴 카드부터."""
    return _targets_of(_my_pair_sums(details, ouid), limit)


def _targets_of(sums: dict, limit: int | None) -> list[tuple[int, int]]:
    best: dict[int, tuple[int, int]] = {}
    for (sp_id, pos), a in sums.items():
        g = int(a["games"])
        if sp_id not in best or g > best[sp_id][1]:
            best[sp_id] = (pos, g)
    order = sorted(best.items(), key=lambda kv: -kv[1][1])
    out = [(sp_id, pos) for sp_id, (pos, _) in order]
    return out[:limit] if limit else out


def _metric_values(src: dict, games: float) -> dict:
    out = {}
    for name, f in RANKER_METRICS:
        if isinstance(f, tuple):
            ok, tr = _num(src, f[0]), _num(src, f[1])
            out[name] = ok / tr * 100 if tr else 0.0
        else:
            out[name] = _num(src, f) / games if games else 0.0
    return out


def ranker_values(r: dict | None) -> tuple[int, str, dict] | None:
    """랭커 응답 한 줄 → (표본 경기 수, 기준일, 지표 이름 → 경기당 값). 데이터가 없으면 None.
    값은 이미 경기당 평균이라 나누지 않는다(test_ranker_values_are_per_game)."""
    st = (r or {}).get("status") or {}
    n = _num(st, "matchCount")
    if not r or not n:
        return None
    day = matchtime.day(r.get("createDate"))   # 넥슨 원문 UTC(실측 `…T17:30:00` = KST 다음 날 02:30 — 받은 날과 같다)
    return int(n), day.isoformat() if day else "", _metric_values(st, 1)


def ranker_compare(details: list[dict], ouid: str, ranker: dict, limit: int | None = None) -> list[RankerRow]:
    """ranker_targets 의 각 쌍에 대해 내 경기당 값과 랭커 경기당 값을 나란히.
    ranker: (spId, 자리) → 응답 한 줄 | None(넥슨에 데이터 없음). 랭커 값은 이미 경기당 평균이라 나누지 않는다."""
    sums = _my_pair_sums(details, ouid)
    rows = []
    for sp_id, pos in _targets_of(sums, limit):
        a = sums[(sp_id, pos)]
        row = RankerRow(sp_id, pos, int(a["games"]), mine=_metric_values(a, a["games"]))
        got = ranker_values(ranker.get((sp_id, pos)))
        if got is not None:
            row.ranker_games, row.ranker_date, row.ranker = got
        rows.append(row)
    return rows


# ── 구단주 비교 확장(50단계 D1) ─────────────────────────────────────────────
COMPARE_MIN_GAMES = 10     # 어느 한쪽이 이보다 적은 경기면 그 행은 z 를 못 낸다(표본분산이 흔들린다)
COMPARE_Z_MIN = 2.5        # 요약에 올리는 문턱 — 후보 8행이라 우연히 하나 섞일 기대가 약 0.2 (10 대 10경기 Welch 자유도 ≈18 → P(|t|>2.5)≈0.022 × 8 — 정규 근사의 0.1 은 낮다)
COMPARE_TOP = 4            # 요약 최대 줄 수


def _share(ok: float, tries: float) -> float | None:
    """시도 0 인 경기는 그 행에서 뺀다(None) — 0% 로 읽으면 평균을 끌어내린다."""
    return ok / tries * 100 if tries else None


# (표 행 이름, 클수록 좋음, 한 경기 값(me, opp) → float | None). 이름은 app_main.COMPARE_ROWS·COMPARE_PROFILE_ROWS 라벨과 같다.
# 평균 득점·실점·점유율·평점은 표(summarize) 와 같은 필드에서, 아래 넷은 team_profile 과 같은 필드에서 경기별로 다시 센다.
COMPARE_FEATURES = (
    ("평균 득점", True, lambda me, opp: _num(me.get("shoot") or {}, "goalTotal")),
    ("평균 실점", False, lambda me, opp: _num(opp.get("shoot") or {}, "goalTotal")),
    ("평균 점유율", True, lambda me, opp: _num(me.get("matchDetail") or {}, "possession")),
    ("평균 평점", True, lambda me, opp: _num(me.get("matchDetail") or {}, "averageRating")),
    ("경기당 슛", True, lambda me, opp: _num(me.get("shoot") or {}, "shootTotal")),
    ("유효슛 비율", True, lambda me, opp: _share(_num(me.get("shoot") or {}, "effectiveShootTotal"),
                                              _num(me.get("shoot") or {}, "shootTotal"))),
    ("패스 성공률", True, lambda me, opp: _share(_num(me.get("pass") or {}, "passSuccess"),
                                              _num(me.get("pass") or {}, "passTry"))),
    ("태클 성공률", True, lambda me, opp: _share(_num(me.get("defence") or {}, "tackleSuccess"),
                                              _num(me.get("defence") or {}, "tackleTry"))),
)


def _per_match(details: list[dict], ouid: str) -> dict[str, list[float]]:
    """행 이름 → 경기별 값. 승·무·패가 아닌 경기("오류" 등)는 summarize 와 같은 기준으로 뺀다."""
    out: dict[str, list[float]] = {name: [] for name, _, _ in COMPARE_FEATURES}
    for d in details:
        me, opp = _me_opp(d, ouid)
        if me is None or _wdl_of(_result_of(me)) is None:
            continue
        for name, _, fn in COMPARE_FEATURES:
            v = fn(me, opp)
            if v is not None:
                out[name].append(v)
    return out


def _mean_var(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    return m, (sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0)


@dataclass
class CompareDiff:
    label: str
    z: float                   # (내 평균 − 상대 평균) / SE — 고르는 데만 쓴다. 보이는 숫자는 표 값
    higher_is_better: bool
    mine: float | None = None  # 표 값 (table 을 안 줬으면 경기별 평균)
    opp: float | None = None

    @property
    def mine_better(self) -> bool:
        return (self.z > 0) == self.higher_is_better


def compare_summary(my_details: list[dict], my_ouid: str, opp_details: list[dict], opp_ouid: str,
                    table: dict[str, tuple[float, float]] | None = None) -> list[CompareDiff]:
    """두 구단주 사이 차이가 뚜렷한 지표 — |z| ≥ COMPARE_Z_MIN 인 행을 큰 순으로 최대 COMPARE_TOP 개.

    z 는 경기별 값의 표본분산으로 낸다(같은 경기 안 슛의 묶임을 이항으로 보지 않는다). 어느 쪽이든 경기가
    COMPARE_MIN_GAMES 보다 적거나 분모가 0 이면 그 행은 뺀다. table(행 이름 → (내 표 값, 상대 표 값))을 주면
    경기별 평균과 표 값의 방향이 반대인 행도 뺀다 — 표는 합계 비율이라 경기별 평균과 어긋날 수 있다.
    승률은 후보가 아니다(표 맨 위 줄이 이미 승률이고 무승부 분모 식이 경기별 값으로 안 떨어진다)."""
    a, b = _per_match(my_details, my_ouid), _per_match(opp_details, opp_ouid)
    out = []
    for name, higher, _ in COMPARE_FEATURES:
        xs, ys = a[name], b[name]
        if len(xs) < COMPARE_MIN_GAMES or len(ys) < COMPARE_MIN_GAMES:
            continue
        (m1, v1), (m2, v2) = _mean_var(xs), _mean_var(ys)
        se = math.sqrt(v1 / len(xs) + v2 / len(ys))
        if se <= 0:
            continue
        z = (m1 - m2) / se
        if abs(z) < COMPARE_Z_MIN:
            continue
        mine, opp = table[name] if table and name in table else (m1, m2)
        if (mine - opp) * z <= 0:
            continue
        out.append(CompareDiff(name, z, higher, mine, opp))
    out.sort(key=lambda r: -abs(r.z))
    return out[:COMPARE_TOP]


# 같은 자리 줄 선수 나란히 — 줄마다 가장 많이 세운 카드 하나씩
COMPARE_LINES = ("FW", "MF", "DF", "GK")


@dataclass
class LineCard:
    sp_id: int
    games: int                                   # 그 줄에 그 카드를 세운 경기
    values: dict = field(default_factory=dict)   # RANKER_METRICS 이름 → 경기당 값(비율은 %)


@dataclass
class LinePair:
    line: str
    mine: LineCard | None = None
    opp: LineCard | None = None


def _top_cards_by_line(details: list[dict], ouid: str) -> dict[str, LineCard]:
    """줄(FW·MF·DF·GK) → 가장 많이 세운 카드. 같은 카드가 줄 안 여러 자리에 섰으면 합친다. 교체 명단은 자리가 없어 뺀다."""
    by_line: dict[str, dict[int, dict]] = defaultdict(dict)
    for (sp_id, pos), a in _my_pair_sums(details, ouid).items():
        line = position_line(pos)
        if line is None:
            continue
        acc = by_line[line].setdefault(sp_id, defaultdict(float))
        for k, v in a.items():
            acc[k] += v
    out = {}
    for line, cards in by_line.items():
        sp_id, a = max(cards.items(), key=lambda kv: (kv[1]["games"], -kv[0]))   # 동률은 카드 번호 작은 쪽 — 실행마다 같게
        out[line] = LineCard(sp_id, int(a["games"]), _metric_values(a, a["games"]))
    return out


def line_pairs(my_details: list[dict], my_ouid: str, opp_details: list[dict], opp_ouid: str) -> list[LinePair]:
    """줄마다 (내 대표 카드, 상대 대표 카드) — 어느 한쪽이라도 있는 줄만, COMPARE_LINES 순서."""
    mine, opp = _top_cards_by_line(my_details, my_ouid), _top_cards_by_line(opp_details, opp_ouid)
    return [LinePair(line, mine.get(line), opp.get(line)) for line in COMPARE_LINES if line in mine or line in opp]
