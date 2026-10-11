"""내 경기 습관·기준선 계산(2.6.1 — ROADMAP "2.6.1" 절). 화면 없음 · 순수 계산 · 화면은 core_api 로.

지금 있는 것:
| 함수 | 화면 | 메모 |
|---|---|---|
| `division_stays(points, name_of)` | [성적 진단 › 등급 체류](N2) | 재료는 `stats.division_trend` — (일시, division) 오래된 것부터 |

등급 체류의 규칙(N2):
- division id 는 **작을수록 상위**(800 슈퍼챔피언스 · 900 챔피언스 · … 1300 → 1700 도 같은 방향 — 1400~1600 은 없다).
  앞 경기보다 id 가 작아지면 승격, 커지면 강등.
- 기록 시작 때 이미 있던 등급은 "들어온 횟수"에 안 센다(언제 올랐는지 모른다 — `stats.division_entries` 와 같다).
- **기록 빈 구간**: 이웃 경기 사이가 `config.N2_GAP_DAYS` 일을 넘으면(API 는 약 한 달까지만 주므로 앱을 오래 안 켜면
  경기가 빈다) 그 앞뒤 등급 차이는 승격·강등에도, 들어온 횟수에도 안 센다 — 빈 사이에 몇 번 오갔는지 모른다.
  머문 날 수는 경기가 있던 날만 세므로 빈 사이 날은 원래 안 들어간다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config


@dataclass
class DivisionStay:
    """등급 하나에 머문 기록."""
    division: int
    name: str
    games: int = 0      # 그 등급으로 뛴 경기 수
    days: int = 0       # 그 등급으로 끝난 날 수(그날 마지막 경기의 등급)
    entries: int = 0    # 다른 등급에서 들어온 횟수(기록 시작·빈 구간 너머는 안 센다)


@dataclass
class DivisionStays:
    rows: list[DivisionStay] = field(default_factory=list)   # 높은 등급(id 작은 것)부터
    promotions: int = 0
    demotions: int = 0
    gaps: int = 0               # 기록 빈 구간 수 — 그 사이 승강은 모른다
    start_division: int | None = None   # 기록 시작 때 등급(들어온 횟수에 안 센 것)


def division_stays(points: list[tuple[datetime, int]], name_of=None) -> DivisionStays:
    """points 는 (경기 일시, 내 division) — 순서는 상관없다(시각순으로 다시 놓는다). division 없는 경기는 재료에 이미 없다."""
    seq = sorted(points, key=lambda t: t[0])
    out = DivisionStays()
    if not seq:
        return out
    gap = timedelta(days=config.N2_GAP_DAYS)
    rows: dict[int, DivisionStay] = {}

    def row(div: int) -> DivisionStay:
        r = rows.get(div)
        if r is None:
            r = rows[div] = DivisionStay(div, name_of(div) if name_of else str(div))
        return r

    out.start_division = seq[0][1]
    last_of_day: dict = {}
    prev_t, prev_div = None, None
    for t, div in seq:
        r = row(div)
        r.games += 1
        last_of_day[t.date()] = div
        if prev_div is not None and div != prev_div:
            if t - prev_t > gap:
                pass   # 빈 구간 너머 — 승강·진입 모름(아래에서 빈 구간으로만 센다)
            else:
                r.entries += 1
                if div < prev_div:
                    out.promotions += 1
                else:
                    out.demotions += 1
        if prev_t is not None and t - prev_t > gap:
            out.gaps += 1
        prev_t, prev_div = t, div
    for div in last_of_day.values():
        row(div).days += 1
    out.rows = [rows[k] for k in sorted(rows)]
    return out
