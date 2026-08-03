"""집계 결과를 사람이 읽는 문장으로 — 최근 흐름과 이기는/지는 방식.

분석 창을 두 층으로 나눈다.

  · **최근 흐름** — 최근 WINDOW(20)경기. "지금 잘 되고 있나"
  · **이기는/지는 패턴** — 조회된 **전체** 경기. "원래 어떤 팀인가"

패턴까지 최근 20경기로 말하면 표본이 모자란다. 조건부로 쪼개면 6~8경기가
남는데, 그 정도면 동전 던지기로도 승률 70%가 흔히 나온다 — 노이즈를 패턴으로
읽고 사용자에게 잘못된 확신을 준다. 그래서 패턴은 전체 경기로 재고, 최근이
그 패턴에서 벗어났을 때만 대조 문장(_contrast)을 낸다.

표본이 임계값에 못 미치는 규칙은 **문장을 내지 않는다.** 침묵이 틀린 단정보다
낫다. 이 모듈은 순수 계산이라 API 호출이 없다 — 이미 받아 둔 details 만 쓴다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from models import MatchSummary, current_streak, opponent_stats, summarize
from stats import (UNKNOWN_GOAL_TYPE, clutch_summary, formation_stats,
                   goal_minute_buckets, possession_stats, result_breakdown,
                   shot_map)

WINDOW = 20          # "최근 흐름"이 보는 경기 수

# ── 최소 표본 ────────────────────────────────────────────────────────────
# 규칙마다 분모가 다르다(경기 수 / 골 수 / 슛 수). 분모가 큰 지표일수록
# 임계값을 올려 잡았다.
MIN_FLOW = 5         # 최근 흐름을 말하려면 최소 이만큼
MIN_BASE = 15        # 패턴을 말하려면 전체가 최소 이만큼
MIN_COND = 8         # 조건부(선제골 넣은 경기 등) 최소 표본 — 절대 하한
# 조건부 최소 표본은 전체 규모를 따라가야 한다. 3,774경기 계정에서 9경기짜리
# 포메이션이 "이기는 패턴" 상위에 오는 걸 실제 DB 로 돌려보고 잡았다 —
# 전체가 크면 우연히 승률이 튀는 소표본 항목도 그만큼 많아진다.
COND_SHARE = 0.02    # 전체의 2% 이상
MIN_GOALS = 15       # 득점·실점 분포를 말하려면 골이 최소 이만큼
MIN_SHOTS = 60       # 결정력(xG 대비)을 말하려면 슛이 최소 이만큼
MIN_OPP = 4          # 같은 상대와 최소 이만큼 붙어야 상성을 말한다

# ── 유의하다고 볼 최소 차이 ───────────────────────────────────────────────
GAP = 10.0           # 승률 차이(%p)
GAP_WIDE = 15.0      # 구간·전술처럼 쪼개지는 지표는 더 크게 봐야 한다
CONC_SHARE = 1.5     # 한 구간 실점 비중이 균등분포(1/6)의 몇 배부터 편중인가
TYPE_SHARE = 25.0    # 특수 골 유형이 전체의 몇 %부터 "치우쳤다"고 볼 것인가
FINISH_GAP = 25.0    # 골이 xG 대비 몇 % 벗어나야 결정력을 말할 것인가

# xG 는 슛 좌표 기반 근사(stats.shot_xg)라 편차가 크게 나올 수 있다. 정렬
# 가중치에서만 상한을 씌운다 — 문장에 적는 숫자는 실제 값 그대로 둔다.
_FINISH_CAP = 40.0

# "일반(D)"·"땅볼(DD)"은 대부분의 골이 여기 속하는 기본 유형이라, 비중이
# 높다는 사실 자체에 정보가 없다("득점의 100%가 일반 유형입니다"). 헤더·
# 프리킥·페널티킥처럼 특이한 유형이 몰릴 때만 말할 가치가 있다.
BASIC_GOAL_TYPES = frozenset({"일반(D)", "땅볼(DD)"})

SEC_FLOW = "최근 흐름"
SEC_WIN = "이기는 패턴"
SEC_LOSE = "지는 패턴"


@dataclass
class Insight:
    """분석 문장 하나. headline 은 결론, detail 은 근거 숫자."""
    section: str
    headline: str
    detail: str = ""
    weight: float = 0.0

    def text(self) -> str:
        return f"{self.headline} {self.detail}".strip()


# 표본 배수 상한 — 표본이 아무리 커도 가중치를 이 배까지만 밀어 올린다.
_CONF_CAP = 3.0


def _w(gap: float, n: int, base_n: int) -> float:
    """정렬용 가중치 — 기준에서 많이 벗어날수록, 표본이 클수록 위로.

    √ 를 씌우는 건 표준오차가 1/√n 로 줄기 때문이다. 같은 20%p 차이라도
    8경기짜리보다 40경기짜리를 먼저 보여줘야 한다.

    다만 규칙마다 분모가 다르다 — 경기 수(수십), 골 수(백여), 슛 수(수백).
    생짜 √n 을 쓰면 분모가 큰 규칙(슛 기반 결정력)이 다른 규칙을 전부
    눌러버려서 섹션 안 순위가 뒤집힌다. 그래서 **그 규칙의 최소 표본
    대비 몇 배인지**로 정규화하고, 상한을 씌운다.
    """
    ratio = max(n, 1) / max(base_n, 1)
    return abs(gap) * min(math.sqrt(ratio), _CONF_CAP)


def _min_cond(total: int) -> int:
    """전체 total 경기일 때 조건부 규칙이 요구할 최소 표본."""
    return max(MIN_COND, math.ceil(total * COND_SHARE))


def _rate(wdl: list[int]) -> float:
    tot = sum(wdl)
    return wdl[0] / tot * 100 if tot else 0.0


def _wdl_text(w: int, d: int, l: int) -> str:
    return f"{w}승 {d}무 {l}패"


def _recent_details(matches: list[MatchSummary], details: list[dict],
                    window: int) -> list[dict]:
    """최근 window 경기에 해당하는 details 만. matches 는 최신순이 전제다.

    details 가 matches 와 같은 순서라는 보장이 없어서 matchId 로 맞춘다.
    """
    ids = {m.match_id for m in matches[:window]}
    return [d for d in details if d.get("matchId") in ids]


# ── 최근 흐름 ────────────────────────────────────────────────────────────
def _flow(matches: list[MatchSummary], details: list[dict], ouid: str,
          window: int) -> list[Insight]:
    recent = matches[:window]
    s = summarize(recent)
    if s.total < MIN_FLOW:
        return []

    out = [Insight(
        SEC_FLOW,
        f"최근 {s.total}경기 {_wdl_text(s.win, s.draw, s.lose)},"
        f" 승률 {s.win_rate:.0f}%.",
        f"경기당 {s.avg_goals_for:.1f}골 넣고 {s.avg_goals_against:.1f}골 먹혔습니다.",
        float("inf"),  # 요약은 항상 맨 위
    )]

    kind, run = current_streak(recent)
    if run >= 3:
        word = {"승": "연승", "무": "연속 무승부", "패": "연패"}.get(kind, "")
        out.append(Insight(
            SEC_FLOW, f"현재 {run}{word} 중입니다.", "",
            _w(run * 10, run, 3)))

    # 이전 구간과 비교 — 최근이 평소보다 좋아졌나 나빠졌나.
    prev = summarize(matches[window:])
    if prev.total >= MIN_BASE:
        gap = s.win_rate - prev.win_rate
        if abs(gap) >= GAP:
            word = "올랐습니다" if gap > 0 else "떨어졌습니다"
            out.append(Insight(
                SEC_FLOW,
                f"그 이전 {prev.total}경기 승률 {prev.win_rate:.0f}% 대비"
                f" {abs(gap):.0f}%p {word}.",
                "", _w(gap, min(s.total, prev.total), MIN_BASE)))

    out.extend(_contrast(matches, details, ouid, window))
    return out


def _contrast(matches: list[MatchSummary], details: list[dict], ouid: str,
              window: int) -> list[Insight]:
    """전체 패턴과 최근을 대조 — "이기는 방식이 막혔다"를 잡아내는 규칙.

    한쪽만 봐서는 안 나오는 문장이라 따로 뒀다. 선제골처럼 승패 영향이 큰
    지표에서, 그걸 만들어내는 빈도 자체가 최근에 달라졌는지를 본다.
    """
    prev_d = _recent_details(matches[window:], details, len(matches))
    recent_d = _recent_details(matches, details, window)
    if len(prev_d) < MIN_BASE or len(recent_d) < MIN_FLOW:
        return []

    base = clutch_summary(prev_d, ouid)
    now = clutch_summary(recent_d, ouid)
    base_n = sum(base.first_scored) + sum(base.first_conceded)
    now_n = sum(now.first_scored) + sum(now.first_conceded)
    if base_n < MIN_COND or now_n < MIN_FLOW:
        return []

    base_share = sum(base.first_scored) / base_n * 100
    now_share = sum(now.first_scored) / now_n * 100
    gap = now_share - base_share
    if abs(gap) < GAP_WIDE or base.first_scored_rate < 50:
        return []

    if gap < 0:
        return [Insight(
            SEC_FLOW,
            "이기는 방식이 막혀 있습니다.",
            f"선제골을 넣은 경기 승률이 {base.first_scored_rate:.0f}%인데,"
            f" 최근에는 {now_n}경기 중 {sum(now.first_scored)}번만 선제골을"
            f" 넣었습니다(평소 {base_share:.0f}% → {now_share:.0f}%).",
            _w(gap, now_n, MIN_FLOW))]
    return [Insight(
        SEC_FLOW,
        "경기를 앞서서 시작하는 빈도가 올라갔습니다.",
        f"선제골 비율 {base_share:.0f}% → {now_share:.0f}%."
        f" 선제골 경기 승률이 {base.first_scored_rate:.0f}%라 그대로 성적으로"
        f" 이어집니다.",
        _w(gap, now_n, MIN_FLOW))]


# ── 이기는 / 지는 패턴 (전체 경기 기준) ────────────────────────────────────
def _clutch_rules(details: list[dict], ouid: str, base_rate: float
                  ) -> list[Insight]:
    cs = clutch_summary(details, ouid)
    out = []

    n_first = sum(cs.first_scored)
    if n_first >= MIN_COND:
        r = cs.first_scored_rate
        gap = r - base_rate
        if gap >= GAP:
            out.append(Insight(
                SEC_WIN, f"선제골을 넣으면 승률 {r:.0f}%.",
                f"{n_first}경기 {_wdl_text(*cs.first_scored)} —"
                f" 전체 승률({base_rate:.0f}%)보다 {gap:.0f}%p 높습니다."
                f" 먼저 앞서 나가는 게 승패를 가장 크게 가릅니다.",
                _w(gap, n_first, MIN_COND)))
        if cs.comeback_lose >= 3 and n_first >= MIN_COND:
            share = cs.comeback_lose / n_first * 100
            if share >= 20:
                out.append(Insight(
                    SEC_LOSE,
                    f"앞서고도 진 경기가 {cs.comeback_lose}번입니다.",
                    f"선제골을 넣은 {n_first}경기 중 {share:.0f}% —"
                    f" 리드를 끝까지 지키지 못하고 있습니다.",
                    _w(share - 20, n_first, MIN_COND)))

    n_conc = sum(cs.first_conceded)
    if n_conc >= MIN_COND:
        r = cs.first_conceded_rate
        gap = base_rate - r
        if gap >= GAP:
            # 역전승 수(comeback_win)는 정의상 first_conceded 의 승과 같은
            # 값이라 여기 적으면 같은 숫자를 두 번 말하게 된다. 대신 "얼마나
            # 자주 먼저 먹히는가"를 붙인다 — 이게 실제로 새로운 정보다.
            share = n_conc / (n_first + n_conc) * 100 if n_first + n_conc else 0
            out.append(Insight(
                SEC_LOSE, f"먼저 실점하면 승률이 {r:.0f}%로 떨어집니다.",
                f"{n_conc}경기 {_wdl_text(*cs.first_conceded)}."
                f" 선제골이 갈린 경기의 {share:.0f}%에서 먼저 실점하고 있습니다.",
                _w(gap, n_conc, MIN_COND)))
    return out


def _minute_rules(details: list[dict], ouid: str) -> list[Insight]:
    buckets = goal_minute_buckets(details, ouid)[:6]  # 연장 제외 — 분모를 왜곡한다
    out = []
    for attr, section, verb in (("conceded", SEC_LOSE, "실점"),
                                ("scored", SEC_WIN, "득점")):
        vals = [getattr(b, attr) for b in buckets]
        total = sum(vals)
        if total < MIN_GOALS:
            continue
        top = max(range(len(vals)), key=lambda i: vals[i])
        share = vals[top] / total * 100
        if share < 100 / 6 * CONC_SHARE:
            continue
        label = buckets[top].label
        if section is SEC_LOSE:
            tail = ("막판 관리가 승패를 가르고 있습니다."
                    if top == 5 else "이 구간에서 흐름을 내주고 있습니다.")
            head = f"{verb}의 {share:.0f}%가 {label}분에 몰려 있습니다."
        else:
            tail = "이 구간에 경기를 가져옵니다."
            head = f"{verb}의 {share:.0f}%가 {label}분에서 나옵니다."
        out.append(Insight(section, head,
                           f"전체 {total}골 중 {vals[top]}골. {tail}",
                           _w(share - 100 / 6, total, MIN_GOALS)))
    return out


def _possession_rules(details: list[dict], ouid: str, total: int
                      ) -> list[Insight]:
    need = _min_cond(total)
    bands = [b for b in possession_stats(details, ouid) if b.games >= need]
    if len(bands) < 2:
        return []
    best = max(bands, key=lambda b: b.win_rate)
    worst = min(bands, key=lambda b: b.win_rate)
    if best.win_rate - worst.win_rate < GAP_WIDE:
        return []

    hint = {
        "우세": "상대가 내려서면 뚫지 못하는 쪽입니다.",
        "열세": "공을 내주고 하는 경기에서 무너집니다.",
        "균형": "주도권이 애매할 때 흔들립니다.",
    }.get(worst.label, "")
    return [Insight(
        SEC_LOSE,
        f"점유율 {worst.span}% 구간 승률이 {worst.win_rate:.0f}%입니다.",
        f"{worst.games}경기 {_wdl_text(worst.win, worst.draw, worst.lose)}."
        f" 반면 {best.label}({best.span}%) 구간은 {best.games}경기에서"
        f" {best.win_rate:.0f}%. {hint}",
        _w(best.win_rate - worst.win_rate, worst.games, MIN_COND))]


def _formation_rules(details: list[dict], ouid: str, base_rate: float,
                     total: int) -> list[Insight]:
    need = _min_cond(total)
    stats = [f for f in formation_stats(details, ouid, of_opponent=True)
             if f.games >= need and f.formation != "0-0-0-0-0"]
    out = []
    for f in stats:
        gap = f.win_rate - base_rate
        if gap <= -GAP_WIDE:
            out.append(Insight(
                SEC_LOSE, f"상대가 {f.formation}로 나오면 승률 {f.win_rate:.0f}%.",
                f"{f.games}경기 {_wdl_text(f.win, f.draw, f.lose)} —"
                f" 전체보다 {abs(gap):.0f}%p 낮습니다.",
                _w(gap, f.games, MIN_COND)))
        elif gap >= GAP_WIDE:
            out.append(Insight(
                SEC_WIN, f"상대가 {f.formation}일 때 승률 {f.win_rate:.0f}%.",
                f"{f.games}경기 {_wdl_text(f.win, f.draw, f.lose)} —"
                f" 전체보다 {gap:.0f}%p 높습니다.",
                _w(gap, f.games, MIN_COND)))
    return out


def _finishing_rules(details: list[dict], ouid: str) -> list[Insight]:
    sm = shot_map(details, ouid, mine=True)
    if sm.total < MIN_SHOTS or sm.xg <= 0:
        return []
    gap = (sm.goals - sm.xg) / sm.xg * 100
    if abs(gap) < FINISH_GAP:
        return []
    # xG 는 슛 좌표로 근사한 값이다(stats.shot_xg). 절대량이 아니라 방향만 본다.
    if gap > 0:
        return [Insight(
            SEC_WIN, "슛 기회를 기대치보다 잘 넣고 있습니다.",
            f"슛 {sm.total}개 · 기대득점 {sm.xg:.1f} 대비 실제 {sm.goals}골"
            f"(+{gap:.0f}%). 골 전환율 {sm.conversion:.0f}%.",
            _w(min(gap, _FINISH_CAP), sm.total, MIN_SHOTS))]
    return [Insight(
        SEC_LOSE, "만든 기회를 못 넣고 있습니다.",
        f"슛 {sm.total}개 · 기대득점 {sm.xg:.1f}인데 실제는 {sm.goals}골"
        f"({gap:.0f}%). 유효슛률 {sm.effective_rate:.0f}%.",
        _w(max(gap, -_FINISH_CAP), sm.total, MIN_SHOTS))]


def _goal_type_rules(details: list[dict], ouid: str) -> list[Insight]:
    rb = result_breakdown(details, ouid)
    out = []
    for counter, section, verb, tail in (
            (rb.concede_types, SEC_LOSE, "실점", "이쪽을 막지 못하고 있습니다."),
            (rb.goal_types, SEC_WIN, "득점", "이 경로로 경기를 풉니다.")):
        # 유형을 못 읽은 골(GOAL_TYPES 에 없는 type)은 분모에서 뺀다 —
        # "알 수 없음이 40%"는 사용자에게 아무 의미가 없다.
        known = {k: v for k, v in counter.items() if k != UNKNOWN_GOAL_TYPE}
        total = sum(known.values())
        if total < MIN_GOALS:
            continue
        special = {k: v for k, v in known.items() if k not in BASIC_GOAL_TYPES}
        if not special:
            continue
        name, cnt = max(special.items(), key=lambda kv: kv[1])
        share = cnt / total * 100
        if share < TYPE_SHARE:
            continue
        out.append(Insight(
            section, f"{verb}의 {share:.0f}%가 {name}에서 나옵니다.",
            f"유형이 확인된 {total}골 중 {cnt}골. {tail}",
            _w(share - TYPE_SHARE, total, MIN_GOALS)))
    return out


def _opponent_rules(matches: list[MatchSummary], base_rate: float
                    ) -> list[Insight]:
    out = []
    for o in opponent_stats(matches):
        if o.games < MIN_OPP or o.nickname in ("-", ""):
            continue
        gap = base_rate - o.win_rate
        if gap >= GAP_WIDE * 1.5:
            out.append(Insight(
                SEC_LOSE, f"{o.nickname} 상대로 {o.games}경기 승률 {o.win_rate:.0f}%.",
                f"{_wdl_text(o.win, o.draw, o.lose)},"
                f" 평균 {o.avg_goals_for:.1f}:{o.avg_goals_against:.1f}."
                f" 반복해서 지고 있는 상대입니다.",
                _w(gap, o.games, MIN_OPP)))
    return out


def _patterns(matches: list[MatchSummary], details: list[dict], ouid: str
              ) -> list[Insight]:
    s = summarize(matches)
    if s.total < MIN_BASE:
        return []
    base = s.win_rate
    return (_clutch_rules(details, ouid, base)
            + _minute_rules(details, ouid)
            + _possession_rules(details, ouid, s.total)
            + _formation_rules(details, ouid, base, s.total)
            + _finishing_rules(details, ouid)
            + _goal_type_rules(details, ouid)
            + _opponent_rules(matches, base))


# ── 진입점 ───────────────────────────────────────────────────────────────
SECTIONS = (SEC_FLOW, SEC_WIN, SEC_LOSE)


def narrate(matches: list[MatchSummary], details: list[dict], ouid: str,
            window: int = WINDOW, per_section: int = 3) -> list[Insight]:
    """분석 문장 목록 — 섹션 순서(흐름 → 이기는 → 지는), 섹션 안은 weight 순.

    matches 는 최신순이 전제(화면 표시 순서 그대로). 표본이 모자라면 해당
    섹션이 통째로 비는 게 정상이다.
    """
    found = _flow(matches, details, ouid, window) + _patterns(matches, details, ouid)
    out: list[Insight] = []
    for sec in SECTIONS:
        group = sorted((i for i in found if i.section == sec),
                       key=lambda i: -i.weight)
        out.extend(group[:per_section])
    return out


def as_text(insights: list[Insight], bullet: str = "·") -> str:
    """섹션 제목이 붙은 여러 줄 텍스트 — 봇 응답·클립보드용."""
    if not insights:
        return "분석할 만큼 경기가 쌓이지 않았습니다."
    lines: list[str] = []
    for sec in SECTIONS:
        group = [i for i in insights if i.section == sec]
        if not group:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{sec}]")
        lines.extend(f"{bullet} {i.text()}" for i in group)
    return "\n".join(lines)
