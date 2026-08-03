"""집계 결과 → 카카오톡에 보낼 텍스트.

카톡 말풍선은 **고정폭 폰트가 아니다.** 공백으로 열을 맞춘 표는 기기마다
어긋나 오히려 못 읽는다. 그래서 표 대신 줄바꿈과 구분자(·, |)로만 나눈다.
한 응답이 너무 길면 카톡이 접어버리므로 항목 수를 의도적으로 제한한다.
"""
from __future__ import annotations

from datetime import date

import analysis
from models import (OpponentStat, current_streak, longest_streaks,
                    opponent_stats, summarize)
from stats import aggregate_players

BULLET = "·"
LINE = "─" * 14

# 상대 기록이 없는 경기(탈주 등)는 닉네임이 "-" 로 온다. 그대로 두면 "vs -"
# 라 무슨 경기인지 알 수 없다.
NO_OPPONENT = "-"
NO_OPPONENT_TEXT = "(상대 기록 없음)"


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def _result_mark(result: str) -> str:
    if "승" in result:
        return "승"
    if "무" in result:
        return "무"
    if "패" in result:
        return "패"
    return "?"


def grade_name(details: list[dict], ouid: str, division_names: dict) -> str:
    """'지금' 등급 — 가장 최근 경기에 박힌 division 값 기준.

    user/maxdivision 은 '역대 최고'라 지금과 다를 수 있어 쓰지 않는다
    (app_main._current_grade 와 같은 판단).
    """
    if not details:
        return "-"
    me = next((p for p in details[0].get("matchInfo") or []
               if p.get("ouid") == ouid), None)
    div = me.get("division") if me else None
    if div is None:
        return "-"
    return division_names.get(div, str(div))


def _match_line(m) -> str:
    when = m.match_date.strftime("%m/%d %H:%M") if m.match_date else "-"
    opp = NO_OPPONENT_TEXT if m.opponent == NO_OPPONENT else m.opponent
    return f"{_result_mark(m.result)} {m.score} vs {opp} ({when})"


def summary(lookup, division_names: dict) -> str:
    """!전적 — 누적 요약 + 최근 5경기."""
    ms = lookup.matches
    if not ms:
        return f"[{lookup.nickname}]\n감독모드 전적이 없습니다."

    s = summarize(ms)
    kind, run = current_streak(ms)
    best_win, best_lose = longest_streaks(ms)

    head = [
        f"[{lookup.nickname}] {grade_name(lookup.details, lookup.ouid, division_names)}",
        LINE,
        f"{s.total}경기 {s.win}승 {s.draw}무 {s.lose}패 (승률 {_pct(s.win_rate)})",
        f"평균 득실 {s.avg_goals_for:.2f} / {s.avg_goals_against:.2f}"
        f" {BULLET} 점유율 {s.avg_possession:.0f}%",
    ]
    if run:
        head.append(f"현재 {run}연{kind} {BULLET} 최장 {best_win}연승 / {best_lose}연패")

    body = [LINE, "최근 5경기"] + [_match_line(m) for m in ms[:5]]
    tail = []
    if lookup.fetched:
        tail = [LINE, f"새로 받은 경기 {lookup.fetched}건"]
    return "\n".join(head + body + tail)


def recent(lookup, n: int) -> str:
    """!최근 — 최근 N경기 목록."""
    ms = lookup.matches[:n]
    if not ms:
        return f"[{lookup.nickname}]\n감독모드 전적이 없습니다."
    s = summarize(ms)
    head = [f"[{lookup.nickname}] 최근 {len(ms)}경기",
            f"{s.win}승 {s.draw}무 {s.lose}패 (승률 {_pct(s.win_rate)})", LINE]
    return "\n".join(head + [_match_line(m) for m in ms])


def today(lookup, on: date | None = None) -> str:
    """!오늘 — 달력 하루치 전적. 방에서 가장 자주 나올 질문이라 따로 뒀다.

    "오늘"은 달력 날짜 기준이다. 새벽까지 하는 경우 자정에서 갈리지만,
    그 편이 사람이 예상하는 것과 어긋나지 않는다.
    """
    on = on or date.today()
    todays = [m for m in lookup.matches
              if m.match_date and m.match_date.date() == on]
    label = f"{on:%m/%d}"
    if not todays:
        return f"[{lookup.nickname}] {label}\n오늘 치른 경기가 없습니다."

    s = summarize(todays)
    lines = [f"[{lookup.nickname}] {label}", LINE]
    if s.total:
        lines.append(f"{s.total}경기 {s.win}승 {s.draw}무 {s.lose}패"
                     f" (승률 {_pct(s.win_rate)})")
        lines.append(f"평균 득실 {s.avg_goals_for:.2f} / {s.avg_goals_against:.2f}")
    else:
        # 그 날 경기가 전부 "오류"(중단)인 경우. summarize 가 승·무·패만 세므로
        # 이 줄이 없으면 머리글과 목록 사이가 통째로 비어 버린다.
        lines.append(f"{len(todays)}경기 — 승·무·패로 집계된 경기가 없습니다")
    kind, run = current_streak(todays)
    if run:
        lines.append(f"마지막 {run}연{kind}")
    lines.append(LINE)
    lines += [_match_line(m) for m in todays[:10]]
    if len(todays) > 10:
        lines.append(f"… 외 {len(todays) - 10}경기")
    return "\n".join(lines)


def opponents(lookup, n: int = 8, target: str = "") -> str:
    """!상대 — 많이 붙어본 상대와의 상성. target 을 주면 그 상대만 자세히."""
    stats: list[OpponentStat] = opponent_stats(lookup.matches)
    if not stats:
        return f"[{lookup.nickname}]\n상대 전적이 없습니다."

    if target:
        key = target.strip().lower()
        hit = next((s for s in stats if s.nickname.lower() == key), None)
        if hit is None:
            return (f"[{lookup.nickname}]\n'{target}' 와(과) 붙은 기록이"
                    f" 저장된 전적에 없습니다.")
        return "\n".join([
            f"[{lookup.nickname}] vs {hit.nickname}",
            LINE,
            f"{hit.games}경기 {hit.win}승 {hit.draw}무 {hit.lose}패"
            f" (승률 {_pct(hit.win_rate)})",
            f"평균 득실 {hit.avg_goals_for:.2f} / {hit.avg_goals_against:.2f}",
            f"최근 경기 {hit.last_date}",
        ])

    # 상대 기록이 없는 경기(탈주 등)는 닉네임이 "-" 로 온다. 서로 다른 사람들이
    # 한 명처럼 묶여 목록 맨 위를 차지하므로 뺀다 — 특정 상대 조회(위)에서는
    # 이름을 정확히 쳐야 걸리니 굳이 막지 않는다.
    named = [s for s in stats if s.nickname != "-"]
    if not named:
        return f"[{lookup.nickname}]\n상대 전적이 없습니다."
    head = [f"[{lookup.nickname}] 자주 만난 상대 {min(n, len(named))}명", LINE]
    rows = [f"{s.nickname} {BULLET} {s.games}전 {s.win}승 {s.draw}무 {s.lose}패"
            f" ({_pct(s.win_rate)})" for s in named[:n]]
    return "\n".join(head + rows)


def players(lookup, names: dict, positions: dict, n: int = 6) -> str:
    """!선수 — 출전이 많은 선수의 공격포인트·평점.

    선수당 한 줄로 붙인다. 두 줄씩 쓰면 카톡이 말풍선을 접어("전체 보기")
    한눈에 안 들어온다 — 승률은 !전적 에 이미 있으니 뺐다.
    """
    rows = aggregate_players(lookup.details, lookup.ouid,
                             name_of=lambda i: names.get(i, str(i)),
                             pos_name=lambda p: positions.get(p, str(p)))
    if not rows:
        return f"[{lookup.nickname}]\n선수 기록이 없습니다."

    head = [f"[{lookup.nickname}] 주요 선수 {min(n, len(rows))}명", LINE]
    body = [f"{s.name}({s.position}) {s.goal}골 {s.assist}도움"
            f" {BULLET} 평점 {s.rating:.2f} {BULLET} {s.games}경기"
            for s in rows[:n]]
    return "\n".join(head + body)


def flow(lookup) -> str:
    """!분석 — 최근 흐름과 이기는/지는 방식을 문장으로.

    섹션당 2개로 줄인다(앱은 3개) — 카톡 말풍선이 길면 "전체 보기"로 접혀서
    한눈에 안 들어온다.
    """
    found = analysis.narrate(lookup.matches, lookup.details, lookup.ouid,
                             per_section=2)
    if not found:
        return (f"[{lookup.nickname}]\n"
                f"분석할 만큼 경기가 쌓이지 않았습니다"
                f" (최소 {analysis.MIN_FLOW}경기).")
    head = [f"[{lookup.nickname}] 흐름 분석", LINE]
    return "\n".join(head) + "\n" + analysis.as_text(found, bullet=BULLET)


def ranking(info) -> str:
    """!랭킹 — 넥슨 데이터센터 감독모드 랭킹(오픈API 에 없는 값)."""
    if not info.ranked:
        return (f"[{info.nickname}]\n감독모드 랭킹(상위 10,000위) 안에 없습니다.")
    lines = [f"[{info.nickname}] 감독모드 랭킹", LINE,
             f"{info.rank:,}위 {BULLET} 랭킹점수 {info.elo}",
             f"{info.record_text} (승률 {info.win_rate})"]
    if info.team_value_text:
        lines.append(f"구단가치 {info.team_value_text}")
    if info.team_color:
        lines.append(f"팀컬러 {info.team_color}")
    if info.level:
        lines.append(f"레벨 {info.level}")
    return "\n".join(lines)


HELP = "\n".join([
    "⚽ 피파 전적관리 봇",
    LINE,
    "!등록 <구단주명> — 내 계정 등록 (이후 구단주명 생략 가능)",
    "!전적 [구단주명] — 누적 요약 + 최근 5경기",
    "!오늘 [구단주명] — 오늘 전적",
    "!최근 [구단주명] [개수] — 최근 경기 목록",
    "!상대 [구단주명] [상대명] — 자주 만난 상대 / 특정 상대 상성",
    "!선수 [구단주명] — 주요 선수 기록",
    "!분석 [구단주명] — 최근 흐름 · 이기는/지는 패턴",
    "!랭킹 [구단주명] — 감독모드 순위·구단가치",
    "!해제 — 등록 취소",
    "!도움 — 이 안내",
    LINE,
    "먼저 !등록 을 해두면 이후엔 !전적 처럼 짧게 칠 수 있습니다.",
    "감독모드 전적만 집계합니다.",
])
