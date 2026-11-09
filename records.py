"""88단계 내 기록 — 업적(C1·U1). 계산만(화면은 `core_api` 경유 · 새 요청 0 · 저장 형식 그대로).

| 함수 | 쓰는 곳 |
|---|---|
| `kinds()` | 업적 종류 순서표 — `config.RECORD_*` 에서 만든다 |
| `achievements(details, ouid, seasons=…, current_start=…, current_known=…, elo_rows=…, now=…)` | 창 캐시 `_records_cache` 하나 → [내 기록] · 경기 카드 태그 · 대시보드 칩 |
| `summary(achs)` · `by_match(achs)` · `recent(achs, now)` | 위 셋이 같은 목록에서 읽는다 |

규칙(ROADMAP 88단계 · 1·2회차 검토):
- **늘 저장 전체 기준** — 시즌 필터·빠른 범위와 무관(범위를 바꿀 때마다 "첫" 이 바뀌면 안 된다). 감독모드 경기만.
- ① 연승은 `models.longest_streaks` 와 같은 기준(몰수도 승패 · 무승부는 끊음) — 대시보드 최장 연승과 어긋나지 않게.
  ⑤⑥⑦⑧ 은 몰수 경기를 뺀다(⑥ 은 건너뛸 뿐 연속을 끊지 않는다).
- ② 시즌 최고 점수 갱신은 **점수 기록(elo_history)** 기준 — 그 시즌 둘째 점부터 앞 최고보다 높으면 · 경기 태그 없음(match_id None).
- ②③ 은 시즌을 알 때만: 끝난 시즌은 시즌표가 있을 때만, 지금 시즌은 `season_span` 이 안다고 할 때만(창이 넘긴다).
- ③ 은 그 시즌 저장이 시작부터 있을 때만(첫 경기 ≤ 시작 + `N12_START_DAYS` — `club_cells` 와 같은 식).
- ④ 역전승은 슛 기록 골 수가 최종 점수와 같을 때만(자책골 경기는 판정 안 함) · 승부차기 승은 넣지 않는다(정규 점수가 같다).
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
import matchtime
from models import CHAMPION_DIVISION_ID, SUPER_CHAMPION_DIVISION_ID
from stats import _me_opp, match_goals


@dataclass(frozen=True)
class Achievement:
    key: str
    title: str
    when: datetime
    match_id: str | None    # ② 시즌 최고 점수는 경기가 아니라 None
    note: str = ""
    sp_id: int | None = None   # ⑦ 해트트릭 선수(화면이 이름으로 바꾼다)


@dataclass(frozen=True)
class RecordRow:
    key: str
    title: str
    count: int
    first: datetime | None
    last: datetime | None
    last_match_id: str | None   # 줄을 누르면 여는 경기 — 경기가 붙은 것 중 가장 늦은 것


def kinds(*, seasons_known: bool = True) -> list[tuple[str, str]]:
    """(키, 제목) — [내 기록] 줄 순서. seasons_known 이 거짓이면 ②③ 줄을 뺀다(설명 글 대신 줄을 숨긴다)."""
    out = [(f"streak{n}", f"{n}연승") for n in config.RECORD_STREAKS]
    if seasons_known:
        out += [("peak", "시즌 최고 점수 갱신"), ("champ", "시즌 첫 챔피언스"), ("superchamp", "시즌 첫 슈퍼챔피언스")]
    out += [("comeback", f"{config.RECORD_COMEBACK_DEFICIT}골 차 뒤지다 역전승"),
            ("bigwin", f"{config.RECORD_BIG_WIN_GOALS}골 이상 넣고 승리"),
            ("clean", f"{config.RECORD_CLEAN_RUN}경기 연속 무실점"),
            ("hattrick", "내 선수 해트트릭" if config.RECORD_HATTRICK == 3 else f"내 선수 {config.RECORD_HATTRICK}골")]
    out += [(f"games{n}", f"{n:,}경기") for n in config.RECORD_MILESTONES]
    out += [(f"wins{n}", f"{n:,}승") for n in config.RECORD_MILESTONES]
    return out


@dataclass
class _Game:
    when: datetime
    match_id: str | None
    outcome: str        # 승 · 무 · 패
    forfeit: bool
    gf: int
    ga: int
    division: int | None
    detail: dict


def _games(details: list[dict], ouid: str) -> list[_Game]:
    """감독모드 · 승무패가 읽히는 경기만 — 오래된 것부터(stats.win_divisions 와 같은 거르기)."""
    out = []
    for d in details:
        mt = d.get("matchType")
        if mt is not None and mt != config.DEFAULT_MATCH_TYPE:
            continue
        me, opp = _me_opp(d, ouid)
        if me is None:
            continue
        result = (me.get("matchDetail") or {}).get("matchResult") or ""
        outcome = "승" if "승" in result else "무" if "무" in result else "패" if "패" in result else None
        when = matchtime.parse(d.get("matchDate"))
        if outcome is None or when is None:
            continue
        gf = (me.get("shoot") or {}).get("goalTotal")
        ga = ((opp or {}).get("shoot") or {}).get("goalTotal")
        div = me.get("division")
        out.append(_Game(when, d.get("matchId"), outcome, "몰수" in result,
                         gf if isinstance(gf, int) else 0, ga if isinstance(ga, int) else 0,
                         div if isinstance(div, int) else None, d))
    out.sort(key=lambda g: g.when)
    return out


def _spans(seasons, current_start: datetime | None, current_known: bool, now: datetime) -> list[tuple[datetime, datetime]]:
    """②③ 을 셀 시즌 [시작, 끝) — 끝난 시즌(시즌표) + 지금 시즌(알 때만)."""
    out = [(s.start_at, s.end_at) for s in (seasons or []) if s.end_at <= now]
    if current_known and current_start is not None:
        out.append((current_start, datetime.max))
    return out


def achievements(details: list[dict], ouid: str, *, seasons=(), current_start: datetime | None = None,
                 current_known: bool = False, elo_rows: list[dict] | None = None,
                 now: datetime | None = None) -> list[Achievement]:
    """저장 전체에서 업적 — 시각 순. 원문 dict 는 안 든다(창 캐시가 match_id 만 들게 · 2회차 [중])."""
    now = now or datetime.now()
    games = _games(details, ouid)
    titles = dict(kinds())
    out: list[Achievement] = []

    def add(key: str, g: _Game | None, when: datetime | None = None, note: str = "", sp_id: int | None = None) -> None:
        out.append(Achievement(key, titles[key], when or g.when, g.match_id if g else None, note, sp_id))

    # ① 연승 — longest_streaks 와 같은 기준
    run_kind, run = "", 0
    for g in games:
        kind = g.outcome if g.outcome in ("승", "패") else ""
        run, run_kind = (run + 1, kind) if kind and kind == run_kind else ((1 if kind else 0), kind)
        if run_kind == "승" and run in config.RECORD_STREAKS:
            add(f"streak{run}", g)

    # ④⑤⑥⑦⑧ — 몰수 뺌
    clean = played = won = 0
    for g in games:
        if g.forfeit:
            continue
        played += 1
        if g.outcome == "승":
            won += 1
            if g.gf >= config.RECORD_BIG_WIN_GOALS:
                add("bigwin", g, note=f"{g.gf} : {g.ga}")
        if played in config.RECORD_MILESTONES:
            add(f"games{played}", g)
        if g.outcome == "승" and won in config.RECORD_MILESTONES:
            add(f"wins{won}", g)
        clean = clean + 1 if g.ga == 0 else 0
        if clean == config.RECORD_CLEAN_RUN:
            add("clean", g)
        # 골 흐름은 역전승(실점 ≥ 차)·해트트릭(득점 ≥ 3)이 가능한 경기만 — 전부 풀면 1만 4천 경기 약 400ms(11-09 실측)
        comeback_ok = g.outcome == "승" and g.gf > g.ga and g.ga >= config.RECORD_COMEBACK_DEFICIT
        if not comeback_ok and g.gf < config.RECORD_HATTRICK:
            continue
        goals = match_goals(g.detail, ouid)
        if comeback_ok:
            mine = sum(1 for x in goals if x.mine)
            if mine == g.gf and len(goals) - mine == g.ga:   # 자책골이 끼면 흐름을 못 믿는다
                diff = low = 0
                for x in goals:
                    diff += 1 if x.mine else -1
                    low = min(low, diff)
                if -low >= config.RECORD_COMEBACK_DEFICIT:
                    add("comeback", g, note=f"{-low}골 뒤지다 {g.gf} : {g.ga}")
        per: dict[int, int] = {}
        for x in goals:
            if x.mine and x.sp_id is not None:
                per[x.sp_id] = per.get(x.sp_id, 0) + 1
        for sp, n in per.items():
            if n >= config.RECORD_HATTRICK:
                add("hattrick", g, note=f"{n}골", sp_id=sp)

    # ②③ — 시즌을 알 때만
    times = [g.when for g in games]
    for start, end in _spans(seasons, current_start, current_known, now):
        pts = []
        for r in elo_rows or []:
            try:
                t = datetime.fromisoformat(r["taken_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= t < end and isinstance(r.get("elo"), (int, float)):
                pts.append((t, float(r["elo"])))
        pts.sort()
        if pts:
            best = pts[0][1]
            for t, e in pts[1:]:
                if e > best:
                    best = e
                    add("peak", None, when=t, note=f"{e:,.2f}")
        season = games[bisect_left(times, start):bisect_left(times, end)]   # 시즌 91개 × 전체 훑기를 안 하게
        if season and season[0].when <= start + timedelta(days=config.N12_START_DAYS):
            for key, div_id in (("champ", CHAMPION_DIVISION_ID), ("superchamp", SUPER_CHAMPION_DIVISION_ID)):
                hit = next((g for g in season if g.division is not None and g.division <= div_id), None)
                if hit is not None:
                    add(key, hit)

    out.sort(key=lambda a: a.when)
    return out


def summary(achs: list[Achievement], *, seasons_known: bool = True) -> list[RecordRow]:
    """[내 기록] 줄 — 종류마다 처음 얻은 날 · 횟수 · 마지막. 못 얻은 종류도 줄로 남긴다(횟수 0)."""
    rows = []
    for key, title in kinds(seasons_known=seasons_known):
        got = [a for a in achs if a.key == key]
        with_match = [a for a in got if a.match_id]
        rows.append(RecordRow(key, title, len(got), got[0].when if got else None, got[-1].when if got else None,
                              with_match[-1].match_id if with_match else None))
    return rows


def by_match(achs: list[Achievement]) -> dict[str, list[str]]:
    """경기 카드 태그 — match_id → 그 경기에서 생긴 업적 제목(종류 순서표 순)."""
    order = {k: i for i, (k, _t) in enumerate(kinds())}
    out: dict[str, list[Achievement]] = {}
    for a in achs:
        if a.match_id:
            out.setdefault(a.match_id, []).append(a)
    return {mid: [a.title for a in sorted(v, key=lambda a: order.get(a.key, 99))] for mid, v in out.items()}


def recent(achs: list[Achievement], now: datetime) -> list[Achievement]:
    """대시보드 "최근 기록" 칩 — RECORD_RECENT_DAYS 안 · 늦은 것부터 RECORD_RECENT_MAX 개."""
    since = now - timedelta(days=config.RECORD_RECENT_DAYS)
    got = [a for a in achs if since <= a.when <= now]
    return list(reversed(got))[:config.RECORD_RECENT_MAX]
