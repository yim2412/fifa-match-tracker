"""랭커 메타 ② 스냅숏 읽기(2.4.1 · ROADMAP 31단계) — rank.db 원본(14일)·영구 집계를 **읽기만** 한다.

새 요청 0 · 쓰기 0. 화면은 core_api 를 거쳐 부른다. 규칙의 근거는 ROADMAP "2.4.1" 절(실측 장부 R1~R17).

| 규칙 | 왜 |
|---|---|
| 스냅숏은 지금 시즌만 · 시각은 `rankcollect.data_time` | 2.3.1 규칙 — 넥슨 기준 시각 |
| 이웃 쌍(`latest_pair`) = 원본이 남은 마지막 둘 · 데이터 시각 차 ≤ `RANK_CONT_MAX_H` | 하루 차이 |
| 하루 걸음(N10)은 `predict._gap_ok`(수집 시각 `taken_at`) | 예측과 같은 판정 — 예측은 "모으는 중"인데 N10 만 숫자를 내지 않게 |
| 승·무·패 차가 음수인 사람은 뺀다 | 시즌 경계·넥슨 보정(R17) |
| 하루 승률·승률→점수는 **앞 스냅숏 순위**로 구간 | 뒤 순위면 이긴 사람이 위로 올라와 부풀린다 |
| 두 스냅숏에 다 있는 사람만 | 1만 위 밖으로 빠진 사람은 안 보인다(R1·R5) — 안내 줄에 |
| 랭커 승률은 승÷(승+패) | 무승부 5.7%(R5) |
| 구단가치는 판정에 안 쓴다 | 매일 모두 바뀐다(R3) |

결과는 (DB 파일, 원본 스냅숏 목록, 인자) 키로 프로세스 안에 캐시한다(predict.day_steps 꼴 · 잠금 하나).


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
랭커 메타 ② 스냅숏 읽기(2.4.1 · 31단계) — rank.db 를 **읽기만**(요청 0 · 쓰기 0). 파일 머리말 표가 규칙: 지금 시즌 · `data_time` ·
이웃 쌍 `latest_pair`(원본 남은 마지막 둘 · ≤ `RANK_CONT_MAX_H`) · **N10 하루 걸음만 `predict._gap_ok`(수집 시각)** · 음수 차 뺌 ·
하루 승률·승률→점수는 **앞 스냅숏 순위**로 구간 · 랭커 승률 승÷(승+패). 함수: `day_winrate`(B2) · `value_score`(B3 — `B3_MIN_USERS` 이상 팀컬러끼리 백분위 ·
`B3_MIN_TEAMS` 미만이면 점수 없음) · `value_bins`(B4) · `tier_means`(B6) · `formation_group`/`group_meta`(N6 — B1 은 화면이 묶는다) ·
`winrate_to_elo`/`winrate_peers`/`my_day_rate`(N10) · `weekly`(N13) ·
`rank_moves`/`judge`(N8·N9 — 판정 순서는 `judge` 한 곳) · `find_sn` · `ranked_nicknames`(N7 — DISTINCT 대신 파이썬 집합).
결과는 (DB 파일, 지금 시즌 스냅숏, 원본 목록, 인자) 키로 캐시(`clear_cache` — 테스트).
1만 행 × 14스냅숏 첫 계산이 전부 CPU 0.3초 안이라 작업 스레드 없이 화면 스레드에서 읽는다(`test_rankmeta` 예산). 챔스 판정은 `rankcollect.is_champ` 한 곳.
화면은 `core_api` 로만 — rank.db 가 바뀌는 길 넷(회차 끝 · 팀컬러 목록 저장 · 정리 끝 `maintained` ·
끄기/지우기 `sync_ranker_pick_data`)은 `app_main.RANK_VIEW_KEYS` 를 같이 무효화
"""
from __future__ import annotations

import bisect
import math
import sqlite3
import statistics
import threading
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config
import predict
import rankcollect
from rankcollect import is_champ  # noqa: F401 — core_api 가 여기서도 가져갈 수 있게(정본은 rankcollect)

BLANK_COLOR = "안 씀"
BLANK_FORMATION = "모름"
BLANK_GROUP = "기타·모름"
FORMATION_HOWS = (("그대로", "raw"), ("수비 줄 수", "back"), ("앞줄 수", "front"))

Row = namedtuple("Row", "sn rank nick value elo win draw lose color formation grade")
_COLS = "profile_sn, rank, nickname, team_value, elo, win, draw, lose, team_color, formation, grade"


# ── 공통 ──────────────────────────────────────────────────────────────────

def formation_group(text: str | None, how: str = "raw") -> str:
    """포메이션 묶기(N6) — "" = 모름(그대로) · 기타·모름(묶기). 수비 줄 수 = 첫 숫자, 앞줄 수 = 끝 숫자(R10)."""
    t = (text or "").strip()
    if t in ("", "-"):
        return ""
    if how == "raw":
        return t
    parts = t.split("-")
    if not all(p.isdigit() for p in parts) or len(parts) < 2 or not 3 <= int(parts[0]) <= 5:
        return ""
    return f"{parts[0]}백" if how == "back" else f"앞줄 {parts[-1]}명"


def blank_label(kind: str, how: str = "raw") -> str:
    if kind == "team_color":
        return BLANK_COLOR
    return BLANK_FORMATION if how == "raw" else BLANK_GROUP


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """윌슨 95% 구간(비율 0~1) — 판마다 독립이라고 보는 근사."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def _rate(w: int, lose: int) -> float | None:
    return w / (w + lose) if w + lose else None


def _median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _season_raw(conn: sqlite3.Connection) -> list[dict]:
    """지금 시즌 스냅숏 중 원본이 남은 것 — 데이터 시각 순."""
    out = []
    for s in rankcollect._season_snaps(conn):
        if conn.execute("SELECT 1 FROM snapshot_rows WHERE snapshot_id = ? LIMIT 1", (s["id"],)).fetchone():
            out.append(s)
    return out


def _rows(conn: sqlite3.Connection, sid: int) -> dict[int, Row]:
    return {r[0]: Row(*r) for r in conn.execute(f"SELECT {_COLS} FROM snapshot_rows WHERE snapshot_id = ?", (sid,))}


def _hours(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def _delta(a: Row, b: Row) -> tuple[int, int, int] | None:
    """쌍 사이 승·무·패 — 음수면 None(시즌 경계·넥슨 보정 R17)."""
    d = ((b.win or 0) - (a.win or 0), (b.draw or 0) - (a.draw or 0), (b.lose or 0) - (a.lose or 0))
    return None if min(d) < 0 else d


# ── 캐시 ──────────────────────────────────────────────────────────────────

_cache: dict = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _db_key(conn: sqlite3.Connection) -> tuple:
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except (sqlite3.Error, TypeError, IndexError):
        path = ""
    snaps = rankcollect._season_snaps(conn)
    raw = tuple(s["id"] for s in _season_raw(conn))
    return path, tuple((s["id"], s["taken_at"], s["dt"]) for s in snaps), raw


def _cached(name: str, conn: sqlite3.Connection, args: tuple, make):
    key = _db_key(conn)
    with _cache_lock:
        hit = _cache.get((name, args))
        if hit is not None and hit[0] == key:
            return hit[1]
    val = make()
    with _cache_lock:
        _cache[(name, args)] = (key, val)
    return val


# ── 이웃 쌍 ───────────────────────────────────────────────────────────────

@dataclass
class Pair:
    a: dict
    b: dict
    hours: float                       # 데이터 시각 차
    ok: bool                           # hours ≤ RANK_CONT_MAX_H


def latest_pair(conn: sqlite3.Connection) -> Pair | None:
    """지금 시즌 원본이 남은 마지막 두 스냅숏 — 둘이 안 되면 None. 간격이 넘치면 ok=False(안내 줄에 시간)."""
    raw = _season_raw(conn)
    if len(raw) < 2:
        return None
    a, b = raw[-2], raw[-1]
    h = _hours(a["dt"], b["dt"])
    return Pair(a, b, h, 0 < h <= config.RANK_CONT_MAX_H)


def pair_text(p: Pair | None) -> str:
    if p is None:
        return "하루 차이를 못 냅니다 — 원본 스냅숏이 둘 이상 쌓여야 합니다(하루 한 번 수집)"
    if not p.ok:
        return f"하루 차이를 못 냅니다 — 마지막 두 수집이 {p.hours:.0f}시간 떨어져 있습니다"
    return ""


# ── B2 하루 승률 ──────────────────────────────────────────────────────────

@dataclass
class DayRow:
    key: str
    users: int
    win: int
    draw: int
    lose: int
    rate: float | None                 # 승÷(승+패) %
    lo: float | None                   # 95% 윌슨 %
    hi: float | None
    diff_pp: float | None              # 구간 전체 대비
    thin: bool                         # 인원 META_MIN_USERS 미만
    no_diff: bool                      # 구간이 전체 값을 품음

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose


@dataclass
class DayWinrate:
    pair: Pair | None = None
    users: int = 0                     # 구간 안 · 두 스냅숏 다 있음
    win: int = 0
    draw: int = 0
    lose: int = 0
    all_rate: float | None = None      # 1만 위 안에 남은 사람 전체(구간 무관) %
    negative: int = 0                  # 승무패 차가 음수라 뺀 사람
    changed: dict = field(default_factory=dict)   # kind → 그 사이 바꿔 뺀 사람
    tables: dict = field(default_factory=dict)    # kind → [DayRow] 판 수 내림차순

    @property
    def rate(self) -> float | None:
        r = _rate(self.win, self.lose)
        return None if r is None else r * 100

    @property
    def ready(self) -> bool:
        return self.pair is not None and self.pair.ok


def day_winrate(conn: sqlite3.Connection, top: int = 10000, how: str = "raw") -> DayWinrate:
    return _cached("day_winrate", conn, (top, how), lambda: _day_winrate(conn, top, how))


def _day_winrate(conn, top, how) -> DayWinrate:
    out = DayWinrate(pair=latest_pair(conn))
    if not out.ready:
        return out
    ra, rb = _rows(conn, out.pair.a["id"]), _rows(conn, out.pair.b["id"])
    aw = al = 0
    groups: dict[str, dict] = {"team_color": {}, "formation": {}}
    out.changed = {"team_color": 0, "formation": 0}
    for sn, a in ra.items():
        b = rb.get(sn)
        if b is None:
            continue
        d = _delta(a, b)
        if d is None:
            if a.rank <= top:
                out.negative += 1
            continue
        aw, al = aw + d[0], al + d[2]
        if a.rank > top:
            continue
        out.users += 1
        out.win, out.draw, out.lose = out.win + d[0], out.draw + d[1], out.lose + d[2]
        for kind, ka, kb in (("team_color", a.color or "", b.color or ""),
                             ("formation", formation_group(a.formation, how), formation_group(b.formation, how))):
            if ka != kb:
                out.changed[kind] += 1
                continue
            g = groups[kind].setdefault(ka, [0, 0, 0, 0])
            g[0] += 1
            g[1] += d[0]
            g[2] += d[1]
            g[3] += d[2]
    r = _rate(aw, al)
    out.all_rate = None if r is None else r * 100
    base = out.rate
    for kind, gs in groups.items():
        rows = []
        for key, (n, w, dr, lo_) in gs.items():
            rate = _rate(w, lo_)
            lo, hi = wilson(w, w + lo_) if w + lo_ else (None, None)
            rows.append(DayRow(key, n, w, dr, lo_, None if rate is None else rate * 100,
                               None if lo is None else lo * 100, None if hi is None else hi * 100,
                               None if rate is None or base is None else rate * 100 - base,
                               n < config.META_MIN_USERS,
                               rate is None or base is None or lo * 100 <= base <= hi * 100))
        rows.sort(key=lambda x: (-x.games, x.key))
        out.tables[kind] = rows
    return out


# ── B3 가성비 · B6 구간 평균 — 마지막 원본 스냅숏 ────────────────────────────

def _last_raw(conn) -> tuple[dict | None, dict[int, Row]]:
    raw = _season_raw(conn)
    if not raw:
        return None, {}
    return raw[-1], _rows(conn, raw[-1]["id"])


@dataclass
class ValueRow:
    key: str
    users: int
    elo: float | None                  # 평균 ELO
    rate: float | None                 # 시즌 승÷(승+패) %(사람 합)
    value: float | None                # 구단가치 중앙값(원)
    champ: float | None                # 챔스 이상 비율 %
    pct: dict = field(default_factory=dict)   # 값 → 백분위 0~100(점수를 낼 때만)
    score: float | None = None


@dataclass
class ValueScore:
    at: str | None = None
    rows: list = field(default_factory=list)  # 점수 내림차순(점수 없는 줄은 뒤 · 인원 순)
    scored: bool = False                       # 팀컬러가 B3_MIN_TEAMS 이상이라 점수를 냈다
    eligible: int = 0


def _group_stats(rows) -> tuple:
    w = sum(r.win or 0 for r in rows)
    lose = sum(r.lose or 0 for r in rows)
    rate = _rate(w, lose)
    return (_mean(r.elo for r in rows), None if rate is None else rate * 100, _median(r.value for r in rows),
            sum(1 for r in rows if is_champ(r.grade)) * 100 / len(rows) if rows else None)


def _percentiles(vals: dict[str, float], lower_better: bool = False) -> dict[str, float]:
    """백분위 0~100 — 같은 값은 평균 순위. 모두 같으면 모두 50."""
    keys = sorted(vals, key=lambda k: vals[k])
    n = len(keys)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[keys[j + 1]] == vals[keys[i]]:
            j += 1
        avg = (i + j) / 2
        p = 50.0 if n < 2 else avg * 100 / (n - 1)
        for k in keys[i:j + 1]:
            out[k] = 100 - p if lower_better else p
        i = j + 1
    return out


def value_score(conn: sqlite3.Connection, top: int = 10000) -> ValueScore:
    return _cached("value_score", conn, (top,), lambda: _value_score(conn, top))


def _value_score(conn, top) -> ValueScore:
    snap, rows = _last_raw(conn)
    out = ValueScore(at=snap["dt"] if snap else None)
    by: dict[str, list[Row]] = {}
    for r in rows.values():
        if r.rank <= top:
            by.setdefault(r.color or "", []).append(r)
    for key, rs in by.items():
        out.rows.append(ValueRow(key, len(rs), *_group_stats(rs)))
    elig = [r for r in out.rows if r.key and r.users >= config.B3_MIN_USERS
            and None not in (r.elo, r.rate, r.value, r.champ)]
    out.eligible = len(elig)
    if len(elig) >= config.B3_MIN_TEAMS:
        out.scored = True
        pcts = {
            "elo": _percentiles({r.key: r.elo for r in elig}),
            "rate": _percentiles({r.key: r.rate for r in elig}),
            "value": _percentiles({r.key: r.value for r in elig}, lower_better=True),
            "champ": _percentiles({r.key: r.champ for r in elig}),
        }
        for r in elig:
            r.pct = {k: pcts[k][r.key] for k in pcts}
            r.score = sum(config.B3_WEIGHTS[k] * r.pct[k] / 100 for k in pcts)
    out.rows.sort(key=lambda r: (r.score is None, -(r.score or 0), -r.users, r.key))
    return out


@dataclass
class MeanRow:
    key: str
    users: int
    elo: float | None
    value_median: float | None
    value_mean: float | None
    thin: bool


@dataclass
class TierMeans:
    at: str | None = None
    tables: dict = field(default_factory=dict)   # kind → [MeanRow] 인원 내림차순


def tier_means(conn: sqlite3.Connection, top: int = 10000, how: str = "raw") -> TierMeans:
    return _cached("tier_means", conn, (top, how), lambda: _tier_means(conn, top, how))


def _tier_means(conn, top, how) -> TierMeans:
    snap, rows = _last_raw(conn)
    out = TierMeans(at=snap["dt"] if snap else None)
    if snap is None:
        return out
    for kind, keyf in (("team_color", lambda r: r.color or ""), ("formation", lambda r: formation_group(r.formation, how))):
        by: dict[str, list[Row]] = {}
        for r in rows.values():
            if r.rank <= top:
                by.setdefault(keyf(r), []).append(r)
        t = [MeanRow(k, len(rs), _mean(r.elo for r in rs), _median(r.value for r in rs), _mean(r.value for r in rs),
                     len(rs) < config.META_MIN_USERS) for k, rs in by.items()]
        t.sort(key=lambda m: (-m.users, m.key))
        out.tables[kind] = t
    return out


# ── B4 구단가치 분포 ───────────────────────────────────────────────────────

def round2(x: float) -> float:
    """두 자리 유효숫자로 반올림(27.4억 → 27억)."""
    if x <= 0:
        return x
    e = math.floor(math.log10(x)) - 1
    return round(x / 10 ** e) * 10 ** e


@dataclass
class ValueBin:
    lo: float | None                   # 구간 왼쪽(포함) — 첫 구간은 None
    hi: float | None                   # 오른쪽(제외) — 끝 구간은 None
    users: int = 0
    share: float = 0.0                 # %
    elo: float | None = None
    rate: float | None = None
    champ: float | None = None


@dataclass
class ValueCell:
    users: int
    share: float                       # 그 팀컬러 인원 중 이 구간 %
    champ: float                       # %
    champ_lo: float                    # 윌슨 하한 %
    vs_bin: float | None               # 윌슨 하한 − 구간 평균 챔스 비율(%p) — 색


@dataclass
class ValueBins:
    at: str | None = None
    bins: list = field(default_factory=list)
    cross: list = field(default_factory=list)    # [(팀컬러, 인원, [ValueCell | None] × 구간)]
    best: list = field(default_factory=list)     # 구간마다 [(팀컬러, 평균 ELO, 인원)] 최대 3


def value_bins(conn: sqlite3.Connection) -> ValueBins:
    return _cached("value_bins", conn, (), lambda: _value_bins(conn))


def _value_bins(conn) -> ValueBins:
    snap, rows = _last_raw(conn)
    out = ValueBins(at=snap["dt"] if snap else None)
    rs = sorted((r for r in rows.values() if r.value is not None), key=lambda r: r.value)
    if len(rs) < config.B4_BINS * config.META_MIN_USERS:
        return out
    k = config.B4_BINS
    edges = sorted({round2(rs[len(rs) * i // k].value) for i in range(1, k)})
    members: list[list[Row]] = [[] for _ in range(len(edges) + 1)]
    for r in rs:
        members[bisect.bisect_right(edges, r.value)].append(r)
    for i, ms in enumerate(members):
        b = ValueBin(edges[i - 1] if i else None, edges[i] if i < len(edges) else None, len(ms), len(ms) * 100 / len(rs))
        if ms:
            b.elo, b.rate, _v, b.champ = _group_stats(ms)
        out.bins.append(b)
    totals: dict[str, int] = {}
    for r in rs:
        if r.color:
            totals[r.color] = totals.get(r.color, 0) + 1
    for color in sorted((c for c, n in totals.items() if n >= config.B3_MIN_USERS), key=lambda c: (-totals[c], c)):
        cells = []
        for i, ms in enumerate(members):
            mine = [r for r in ms if r.color == color]
            if len(mine) < config.META_MIN_USERS:
                cells.append(None)
                continue
            ch = sum(1 for r in mine if is_champ(r.grade))
            lo = wilson(ch, len(mine))[0] * 100
            bc = out.bins[i].champ
            cells.append(ValueCell(len(mine), len(mine) * 100 / totals[color], ch * 100 / len(mine), lo,
                                   None if bc is None else lo - bc))
        out.cross.append((color, totals[color], cells))
    for ms in members:
        by: dict[str, list[Row]] = {}
        for r in ms:
            if r.color:
                by.setdefault(r.color, []).append(r)
        ranked = sorted(((c, _mean(r.elo for r in g), len(g)) for c, g in by.items() if len(g) >= config.META_MIN_USERS),
                        key=lambda t: (-(t[1] or 0), t[0]))
        out.best.append(ranked[:3])
    return out


# ── B1 메타 변화의 포메이션 묶기(N6) ──────────────────────────────────────

def group_meta(trend: rankcollect.MetaTrend, how: str) -> rankcollect.MetaTrend:
    """메타 변화(B1) 포메이션 줄을 묶음 키로 더하고 비율·차·흐림을 다시 계산한다. raw 면 그대로."""
    if how == "raw" or trend.collecting or "formation" not in trend.rows:
        return trend
    now: dict[str, int] = {}
    then: dict[str, int] = {}
    for r in trend.rows["formation"]:
        k = formation_group(r.key, how)
        now[k] = now.get(k, 0) + r.now_n
        then[k] = then.get(k, 0) + r.then_n
    ta, tb = sum(now.values()), sum(then.values())
    rows = []
    for k in set(now) | set(then):
        na, nb = now.get(k, 0), then.get(k, 0)
        pa = na * 100 / ta if ta else 0.0
        pb = nb * 100 / tb if tb else 0.0
        rows.append(rankcollect.MetaRow(k, na, pa, nb, pb, pa - pb,
                                        na < config.META_MIN_USERS and nb < config.META_MIN_USERS))
    rows.sort(key=lambda r: (-r.diff_pp, r.key))
    return rankcollect.MetaTrend(trend.now_at, trend.then_at, trend.days, {**trend.rows, "formation": rows}, False)


# ── N10 승률 → 하루 점수 ──────────────────────────────────────────────────

@dataclass
class WrBin:
    lo: float                          # 칸 왼쪽 %(포함)
    hi: float                          # 오른쪽 %(끝 칸만 포함)
    n: int = 0
    mean: float | None = None
    median: float | None = None
    up: float | None = None            # 오른 걸음 비율 %
    p5: float | None = None
    p95: float | None = None
    x: float | None = None             # 칸 안 승률 평균 — 손익분기 보간에


@dataclass
class WinrateElo:
    pairs: int = 0                     # 쓴 하루 걸음 쌍
    negative: int = 0
    few: int = 0                       # 판 N10_MIN_GAMES 미만이라 뺀 걸음
    bins: list = field(default_factory=list)
    breakeven: float | None = None     # %
    steps: list = field(default_factory=list)   # (하루 승률 %, 시작 ELO, ELO 차) — 승률 순

    @property
    def ready(self) -> bool:
        return self.pairs >= config.PREDICT_MIN_PAIRS


def _lean(conn, sid: int) -> dict[int, tuple]:
    return {r[0]: r[1:] for r in conn.execute(
        "SELECT profile_sn, rank, elo, win, draw, lose FROM snapshot_rows WHERE snapshot_id = ?", (sid,))}


def _pct(s: list[float], q: float) -> float:
    return s[min(len(s) - 1, int(q * len(s)))]


def winrate_to_elo(conn: sqlite3.Connection, top: int = 10000) -> WinrateElo:
    return _cached("winrate_to_elo", conn, (top,), lambda: _winrate_to_elo(conn, top))


def _winrate_to_elo(conn, top) -> WinrateElo:
    out = WinrateElo()
    raw = _season_raw(conn)
    prev = None
    for a, b in zip(raw, raw[1:]):
        if not predict._gap_ok(a["taken_at"], b["taken_at"]):
            continue
        out.pairs += 1
        ra = prev[1] if prev is not None and prev[0] == a["id"] else _lean(conn, a["id"])
        rb = _lean(conn, b["id"])
        prev = (b["id"], rb)
        for sn, (rank, e0, w0, d0, l0) in ra.items():
            nb = rb.get(sn)
            if nb is None or rank > top or e0 is None or nb[1] is None:
                continue
            dw, dd, dl = nb[2] - w0, nb[3] - d0, nb[4] - l0
            if min(dw, dd, dl) < 0:
                out.negative += 1
                continue
            if dw + dd + dl < config.N10_MIN_GAMES or dw + dl == 0:
                out.few += 1
                continue
            out.steps.append((dw * 100 / (dw + dl), e0, nb[1] - e0))
    out.steps.sort()
    edges = config.N10_BINS
    groups: list[list[tuple]] = [[] for _ in range(len(edges) + 1)]
    for s in out.steps:
        groups[bisect.bisect_right(edges, s[0])].append(s)
    for i, g in enumerate(groups):
        b = WrBin(edges[i - 1] if i else 0.0, edges[i] if i < len(edges) else 100.0, len(g))
        if g:
            ds = sorted(s[2] for s in g)
            b.mean, b.median = sum(ds) / len(ds), statistics.median(ds)
            b.up = sum(1 for d in ds if d > 0) * 100 / len(ds)
            b.p5, b.p95 = _pct(ds, 0.05), _pct(ds, 0.95)
            b.x = sum(s[0] for s in g) / len(g)
        out.bins.append(b)
    full = [b for b in out.bins if b.n]
    for b0, b1 in zip(full, full[1:]):
        if b0.median <= 0 < b1.median:
            t = -b0.median / (b1.median - b0.median)
            out.breakeven = b0.x + t * (b1.x - b0.x)
            break
    return out


@dataclass
class Peers:
    median: float
    n: int
    band: int | None                   # 점수대 ± — None 이면 점수대 없이


def winrate_peers(res: WinrateElo, rate: float, elo: float | None) -> Peers | None:
    """하루 승률이 비슷한(±N10_PEER_RATE) 같은 점수대 사람들의 하루 ELO 차 중앙값 — 걸음이 없으면 None."""
    lo = bisect.bisect_left(res.steps, (rate - config.N10_PEER_RATE,))
    hi = bisect.bisect_right(res.steps, (rate + config.N10_PEER_RATE, math.inf))
    near = res.steps[lo:hi]
    if not near:
        return None
    if elo is not None:
        for band in config.N10_PEER_BANDS:
            ds = [s[2] for s in near if abs(s[1] - elo) <= band]
            if len(ds) >= config.PREDICT_MIN_STEPS:
                return Peers(statistics.median(ds), len(ds), band)
    return Peers(statistics.median(s[2] for s in near), len(near), None)


def my_day_rate(matches, now: datetime | None = None) -> tuple[float, int] | None:
    """내 마지막 24시간 감독모드 하루 승률(무승부 뺌, %) · 판 수 — 판(승+패) N10_MIN_GAMES 미만이면 None.
    기준은 마지막 경기 시각(트레이에서 검색 없이 지나도 0 이 안 끼게)."""
    dated = [m for m in matches if getattr(m, "match_date", None) is not None]
    if not dated:
        return None
    end = now or max(m.match_date for m in dated)
    start = end - timedelta(hours=24)
    w = lose = 0
    for m in dated:
        if start < m.match_date <= end:
            if "승" in m.result:
                w += 1
            elif "패" in m.result:
                lose += 1
    if w + lose < config.N10_MIN_GAMES:
        return None
    return w * 100 / (w + lose), w + lose


# ── 검색 계정 찾기 · 1만 위 안 닉네임 ─────────────────────────────────────

def find_sn(conn: sqlite3.Connection, sn: int | None, nickname: str | None) -> int | None:
    """프로필 번호 → 없으면 원본이 남은 마지막 스냅숏에서 닉네임으로(sync_tracked_elo 와 같은 순서 R14)."""
    if sn:
        return sn
    if not nickname:
        return None
    raw = _season_raw(conn)
    if not raw:
        return None
    hit = conn.execute("SELECT profile_sn FROM snapshot_rows WHERE snapshot_id = ? AND nickname = ?",
                       (raw[-1]["id"], nickname)).fetchone()
    return hit[0] if hit else None


def ranked_nicknames(conn: sqlite3.Connection) -> frozenset:
    """원본이 남은 지금 시즌 스냅숏에 한 번이라도 1만 위 안에 든 닉네임(N7). DISTINCT 대신 파이썬 집합 —
    임시 정렬(TEMP B-TREE)을 안 만든다."""
    return _cached("ranked_nicknames", conn, (), lambda: _ranked_nicknames(conn))


def _ranked_nicknames(conn) -> frozenset:
    out: set[str] = set()
    for s in _season_raw(conn):
        out.update(r[0] for r in conn.execute("SELECT nickname FROM snapshot_rows WHERE snapshot_id = ?", (s["id"],)))
    return frozenset(out)


# ── N8 순위 변동 원인 · N9 변동 이력 ───────────────────────────────────────

V_GAP = "수집 빠짐"
V_OUT = "1만 위 밖"
V_GAME_SQUAD = "경기 + 스쿼드"
V_GAME = "경기"
V_SQUAD = "스쿼드만"
V_ELO = "점수만 바뀜"
V_PUSHED = "밀림"
V_SAME = "그대로"
V_ODD = "기록이 줄어듦"


@dataclass
class Move:
    at_a: str
    at_b: str
    hours: float
    a: Row | None
    b: Row | None
    wdl: tuple | None                  # 쌍 사이 승·무·패
    verdict: str


@dataclass
class HistRow:
    at: str
    row: Row | None                    # None = 그 스냅숏에 없음(1만 위 밖)
    changed: frozenset = frozenset()   # 앞 줄과 달라진 칸("formation" · "color" · "nickname")


@dataclass
class RankMoves:
    sn: int | None = None
    moves: list = field(default_factory=list)     # 오래된 것부터
    history: list = field(default_factory=list)   # [HistRow] 오래된 것부터

    @property
    def found(self) -> bool:
        return any(h.row is not None for h in self.history)


def judge(a: Row | None, b: Row | None, hours: float) -> tuple[str, tuple | None]:
    """쌍 하나의 판정 — 위에서부터 처음 맞는 것(ROADMAP N8 "실제 순서"). 구단가치·등급은 안 쓴다."""
    if hours > config.RANK_CONT_MAX_H:
        return V_GAP, None
    if a is None or b is None:
        return V_OUT, None
    d = _delta(a, b)
    if d is None:
        return V_ODD, None
    games = sum(d)
    squad = (a.color or "") != (b.color or "") or (a.formation or "") != (b.formation or "")
    if games > 0:
        return (V_GAME_SQUAD if squad else V_GAME), d
    if squad:
        return V_SQUAD, d
    if a.elo != b.elo:
        return V_ELO, d
    if a.rank != b.rank:
        return V_PUSHED, d
    return V_SAME, d


def rank_moves(conn: sqlite3.Connection, sn: int | None) -> RankMoves:
    if sn is None:
        return RankMoves()
    return _cached("rank_moves", conn, (sn,), lambda: _rank_moves(conn, sn))


def _rank_moves(conn, sn) -> RankMoves:
    out = RankMoves(sn=sn)
    raw = _season_raw(conn)
    if not raw:
        return out
    ids = {s["id"] for s in raw}
    mine = {r[0]: Row(*r[1:]) for r in conn.execute(
        f"SELECT snapshot_id, {_COLS} FROM snapshot_rows WHERE profile_sn = ?", (sn,)) if r[0] in ids}
    prev = None
    for s in raw:
        r = mine.get(s["id"])
        changed = set()
        if r is not None and prev is not None:
            if (r.formation or "") != (prev.formation or ""):
                changed.add("formation")
            if (r.color or "") != (prev.color or ""):
                changed.add("color")
            if r.nick != prev.nick:
                changed.add("nickname")
        out.history.append(HistRow(s["dt"], r, frozenset(changed)))
        if r is not None:
            prev = r
    for a, b in zip(raw, raw[1:]):
        h = _hours(a["dt"], b["dt"])
        ra, rb = mine.get(a["id"]), mine.get(b["id"])
        verdict, d = judge(ra, rb, h)
        out.moves.append(Move(a["dt"], b["dt"], h, ra, rb, d, verdict))
    return out


# ── N13 주간 요약 ─────────────────────────────────────────────────────────

@dataclass
class Weekly:
    base_at: str | None = None
    cut_at: str | None = None          # 컷 비교 시점
    cut_days: float | None = None
    cuts: list = field(default_factory=list)       # [(순위, 그때, 지금)]
    rise_at: str | None = None
    rise_days: float | None = None
    risers: list = field(default_factory=list)     # [(닉네임, 그때 순위, 지금 순위, ELO 차)]
    pair: Pair | None = None
    day_wins: list = field(default_factory=list)   # [(닉네임, 순위, 승, 무, 패)]
    extremes: list = field(default_factory=list)   # [(팀컬러, 인원, (닉, 순위, 가치) 최고, (…) 최저)]
    my_value: tuple | None = None                  # (등수, 전체)
    park_state: str = "unknown"                    # "shown" · "far" · "unknown"
    parked: list = field(default_factory=list)     # [(닉네임, 순위, ELO)]


def _compare_snap(snaps: list[dict]) -> dict | None:
    """기준(마지막) 대비 데이터 시각 7일 전 ±1일 → 없으면 3일 이상 전 가장 옛것(meta_trend 와 같은 규칙)."""
    if len(snaps) < 2:
        return None
    base_t = datetime.fromisoformat(snaps[-1]["dt"])
    older = snaps[:-1]
    hit = rankcollect._nearest(older, base_t - timedelta(days=7), timedelta(days=1))
    if hit is None and base_t - datetime.fromisoformat(older[0]["dt"]) >= timedelta(days=3):
        hit = older[0]
    return hit


def _days(a: str, b: str) -> float:
    return _hours(a, b) / 24


def weekly(conn: sqlite3.Connection, my_sn: int | None, end_days: int | None, top: int = 10000) -> Weekly:
    return _cached("weekly", conn, (my_sn, end_days, top), lambda: _weekly(conn, my_sn, end_days, top))


def _weekly(conn, my_sn, end_days, top) -> Weekly:
    out = Weekly()
    snaps = rankcollect._season_snaps(conn)
    if not snaps:
        return out
    base = snaps[-1]
    out.base_at = base["dt"]
    cmp = _compare_snap(snaps)
    if cmp is not None:
        out.cut_at, out.cut_days = cmp["dt"], _days(cmp["dt"], base["dt"])
        now_c, then_c = rankcollect.cut_elo(conn, base["id"]), rankcollect.cut_elo(conn, cmp["id"])
        out.cuts = [(r, then_c.get(r), now_c.get(r)) for r in config.WEEKLY_CUTS]
    raw = _season_raw(conn)
    if not raw:
        return out
    last = _rows(conn, raw[-1]["id"])
    rc = _compare_snap(raw)
    if rc is not None:
        out.rise_at, out.rise_days = rc["dt"], _days(rc["dt"], raw[-1]["dt"])
        old = _rows(conn, rc["id"])
        ups = [(b.nick, old[sn].rank, b.rank, b.elo - old[sn].elo) for sn, b in last.items()
               if sn in old and b.elo is not None and old[sn].elo is not None]
        ups.sort(key=lambda t: (-t[3], t[2]))
        out.risers = ups[:config.WEEKLY_TOP_N]
    out.pair = latest_pair(conn)
    if out.pair is not None and out.pair.ok:
        prev = _rows(conn, out.pair.a["id"])
        wins, parked = [], []
        for sn, b in last.items():
            a = prev.get(sn)
            if a is None:
                continue
            d = _delta(a, b)
            if d is None:
                continue
            wins.append((b.nick, b.rank, *d))
            if sum(d) == 0 and b.rank <= config.WEEKLY_PARK_RANK:
                parked.append((b.nick, b.rank, b.elo))
        wins.sort(key=lambda t: (-t[2], t[1]))
        out.day_wins = wins[:config.WEEKLY_TOP_N]
        if end_days is None:
            out.park_state = "unknown"
        elif end_days > config.WEEKLY_PARK_DAYS:
            out.park_state = "far"
        else:
            out.park_state = "shown"
            out.parked = sorted(parked, key=lambda t: t[1])
    by: dict[str, list[Row]] = {}
    for r in last.values():
        if r.color and r.rank <= top and r.value is not None:
            by.setdefault(r.color, []).append(r)
    for color, rs in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(rs) < config.META_MIN_USERS:
            continue
        hi = max(rs, key=lambda r: (r.value, -r.rank))
        lo = min(rs, key=lambda r: (r.value, r.rank))
        out.extremes.append((color, len(rs), (hi.nick, hi.rank, hi.value), (lo.nick, lo.rank, lo.value)))
    me = last.get(my_sn) if my_sn else None
    if me is not None and me.value is not None:
        vals = [r.value for r in last.values() if r.value is not None]
        out.my_value = (1 + sum(1 for v in vals if v > me.value), len(vals))
    return out
