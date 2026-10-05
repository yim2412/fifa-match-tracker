"""시즌 말 순위 예측(1.3.1 · ROADMAP 10단계) — "지금 흐름이 이어지면 200위·1,000위 안에 들 확률".

같은 점수대 실제 구단주들의 **하루 ELO 변화**(랭킹 수집 원본 14일의 이웃 스냅숏 차)를 뽑아 이어 붙인다.
점수가 오르면 승률이 내려가는 되돌림 · 하루 경기 수 · 연승/연패 연속성이 표본에 그대로 들어 있어서,
결과당 점수 회귀(첫 안)처럼 한 방향으로 폭주하지 않는다(검토 B 1).

- 걸음 표본(`day_steps`): 간격 PREDICT_STEP_GAP_H · 같은 season_seq 인 이웃 쌍만. 승무패는 시즌 누적이라
  (1.3.1 실측: 스냅숏 2,601 vs 저장 경기 2,602) 이웃 차가 그 사이 경기 수다. 두 번째에 없는 구단주는 이탈로만 센다.
- 경로(`simulate`): 날마다 내 하루 경기 수 g 를 내 최근 14일에서 뽑고, 지금 경로 ELO ± 띠 · 같은 경기 수 칸 ·
  같은 "직전 사흘 변화" 3분위의 걸음 하나를 더한다. 칸이 얇으면 ① 3분위 맞추기를 풀고 ② 띠를 넓히고 ③ 그날 0.
  사흘 묶음(같은 구단주의 이어진 사흘)이 칸에 충분하면 묶음째 — 연속성을 살린다.
- 경기 수 칸 경계(4분위)·3분위 경계는 **실행 때 표본에서** 잰다. 계획은 구현 첫 날 잰 값을 상수로 둘 생각이었는데,
  그날 스냅숏이 1개뿐이라(쌍 0) 잴 수 없었다 — 표본이 바뀌면 경계도 따라가는 쪽이 맞다.
- 최종 컷: 받아 둔 지난 시즌 중 하나를 시즌 길이가 가까울수록 크게 뽑고, 순위마다 지금 컷보다 낮지 않게.

숫자를 못 내면 원인별 문구(`Prediction.message`). 화면은 core_api 를 거쳐 `predict_for` 만 부른다.
"""
from __future__ import annotations

import bisect
import math
import random
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import analysis
import config

# 원인별 문구(ROADMAP "숫자를 낼 조건과 못 낼 때 문구") — 화면 어디에도 "보정"이라는 말을 쓰지 않는다
MSG_COLLECT_OFF = "예측은 랭킹 수집 데이터로 계산합니다 — [정보·설정]에서 켜면 하루 뒤부터"
MSG_GATHERING = "랭킹 기록을 모으는 중 — {n}일 뒤 계산합니다"
MSG_GATHERING_UNKNOWN = "랭킹 기록을 모으는 중입니다"
MSG_NO_ELO = "이 구단주의 ELO 기록이 없습니다 — 검색하면 한 점이 생깁니다"
MSG_OUTSIDE = "1만 위 밖은 예측하지 않습니다(같은 점수대 기록이 없습니다)"
MSG_THIN = "이 점수대 기록이 아직 적습니다"
MSG_FEW_GAMES = "최근 경기가 적어 계산하지 않습니다"
MSG_NO_SEASONS = "지난 시즌 기록을 아직 못 받았습니다(다음 수집 때 다시)"
MSG_CLOSING = "시즌 마감 처리 중 — 새 시즌이 시작되면 다시 계산합니다"
NOTE_UNVERIFIED = "검증 전 — 오차 범위를 아직 모릅니다"
NOTE_THIN_BAND = "점수대 기록이 얇음"
NOTE_LATE = "시즌 후반 — 컷 상승을 다 반영하지 못합니다"

OUTSIDE_RANK = config.RANK_TIERS[-1] + 1   # 경로 끝이 1만 위 컷 밑 — 범위 표시에 "1만 위 밖"
_CELL = 10                                 # 점수대 묶음을 이 점 단위로 캐시(경로 ELO 를 이만큼 반올림해 띠 중심으로)


# ── 걸음 표본 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Step:
    elo0: float                        # 그날 시작 ELO
    d: float                           # 하루 ELO 변화
    games: int                         # 그 사이 경기 수
    prev3: float | None                # 같은 구단주의 바로 앞 사흘 변화 합(이어진 사흘이 없으면 None)
    nxt: tuple = ()                    # 이어진 다음 날들 ((d, games), …) 최대 2 — 사흘 묶음


@dataclass
class Steps:
    key: tuple                         # 원본 스냅숏 id 들 — 캐시 키
    pairs: int                         # 쓸 수 있는 이웃 쌍 수
    steps: list[Step]                  # elo0 오름차순
    drops: list[float]                 # 다음 스냅숏에 없던(1만 위 밖으로) 구단주의 시작 ELO — 오름차순
    edges: tuple = ()                  # 하루 경기 수 칸 경계(4분위)
    elos: list[float] = field(default_factory=list)
    bins: list[int] = field(default_factory=list)   # 걸음마다 경기 수 칸 — 점수대 묶음을 만들 때마다 다시 안 재게

    def __post_init__(self):
        if not self.elos:
            self.elos = [s.elo0 for s in self.steps]
        if len(self.bins) != len(self.steps):
            self.bins = [self.bin_of(s.games) for s in self.steps]

    def bin_of(self, g: int) -> int:
        return bisect.bisect_left(self.edges, g)

    def in_band(self, elo: float, width: float) -> tuple[int, int]:
        return bisect.bisect_left(self.elos, elo - width), bisect.bisect_right(self.elos, elo + width)

    def drop_rate(self, elo: float, width: float) -> float:
        lo, hi = self.in_band(elo, width)
        nd = bisect.bisect_right(self.drops, elo + width) - bisect.bisect_left(self.drops, elo - width)
        n = (hi - lo) + nd
        return nd / n if n else 0.0


def _quantiles(vals: list[float], k: int) -> tuple:
    """k 등분 경계 k-1 개(정렬된 값에서 위치로 — 표본이 작아도 돈다)."""
    if not vals:
        return ()
    s = sorted(vals)
    return tuple(s[min(len(s) - 1, (len(s) * i) // k)] for i in range(1, k))


def _gap_ok(a: str, b: str) -> bool:
    try:
        h = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600
    except ValueError:
        return False
    lo, hi = config.PREDICT_STEP_GAP_H
    return lo <= h <= hi


def build_steps(snaps: list[dict], load) -> Steps:
    """snaps: [{id, taken_at, season_seq}] 오래된 것부터 · load(id) → {sn: (elo, 누적 경기)}."""
    data = [load(s["id"]) for s in snaps]
    # pair[i] = 스냅숏 i → i+1 이 하루 걸음으로 쓸 만한가 · {sn: (elo0, d, games)}
    pairs: list[dict | None] = []
    drops: list[float] = []
    for i in range(len(snaps) - 1):
        a, b = snaps[i], snaps[i + 1]
        if a["season_seq"] != b["season_seq"] or not _gap_ok(a["taken_at"], b["taken_at"]):
            pairs.append(None)
            continue
        da, db = data[i], data[i + 1]
        one = {}
        for sn, (e0, g0) in da.items():
            nb = db.get(sn)
            if nb is None:
                drops.append(e0)
                continue
            g = nb[1] - g0
            if g >= 0:
                one[sn] = (e0, nb[0] - e0, g)
        pairs.append(one)

    steps: list[Step] = []
    for i, one in enumerate(pairs):
        if one is None:
            continue
        for sn, (e0, d, g) in one.items():
            if g <= 0:
                continue   # 그날 안 한 사람 — 내 쉬는 날은 g=0 으로 따로 센다
            prev = [pairs[j].get(sn) if j >= 0 and pairs[j] is not None else None for j in (i - 3, i - 2, i - 1)]
            prev3 = sum(p[1] for p in prev) if all(p is not None for p in prev) else None
            nxt = []
            for j in (i + 1, i + 2):
                p = pairs[j].get(sn) if j < len(pairs) and pairs[j] is not None else None
                if p is None:
                    break
                nxt.append((p[1], p[2]))
            steps.append(Step(e0, d, g, prev3, tuple(nxt)))
    steps.sort(key=lambda s: s.elo0)
    drops.sort()
    return Steps(key=tuple(s["id"] for s in snaps), pairs=sum(p is not None for p in pairs), steps=steps,
                 drops=drops, edges=_quantiles([s.games for s in steps], 4))


_steps_cache: dict = {}
_steps_lock = threading.Lock()


def day_steps(rank_conn) -> Steps:
    """rank.db 원본에서 하루 걸음 표본 — 원본 스냅숏 목록이 같으면 캐시(하루 한 번만 다시 만든다)."""
    import rankcollect
    snaps = rankcollect.raw_snapshots(rank_conn)
    key = tuple(s["id"] for s in snaps)
    with _steps_lock:
        hit = _steps_cache.get("v")
        if hit is not None and hit.key == key:
            return hit
    st = build_steps(snaps, lambda sid: rankcollect.snapshot_games(rank_conn, sid))
    with _steps_lock:
        _steps_cache["v"] = st
    return st


# ── 점수대 묶음 (띠 중심 · 폭마다 한 번만 나눈다) ─────────────────────────────

class _Pool:
    """띠 하나 — (경기 수 칸, 3분위) 별 걸음 목록과 사흘 묶음 목록."""

    def __init__(self, steps: Steps, center: float, width: float):
        lo, hi = steps.in_band(center, width)
        band = steps.steps[lo:hi]
        self.tedges = _quantiles([s.prev3 for s in band if s.prev3 is not None], 3)
        self.by_bt: dict = {}
        self.by_b: dict = {}
        flow, te = len(self.tedges) == 2, self.tedges
        for s, b in zip(band, steps.bins[lo:hi]):
            self.by_b.setdefault(b, []).append(s)
            if flow and s.prev3 is not None:
                self.by_bt.setdefault((b, bisect.bisect_right(te, s.prev3)), []).append(s)
        self.blocks = {k: [s for s in v if len(s.nxt) >= 2] for k, v in (*self.by_b.items(), *self.by_bt.items())}
        self.cells = {**self.by_b, **self.by_bt}     # 한 번에 찾게(int 키 = 3분위를 푼 칸)
        self.flow = len(self.tedges) == 2

    def tert(self, prev3: float) -> int:
        # 경계와 같은 값은 위 칸 — 경계는 표본 값 자체라 bisect_left 면 맨 위 무리가 가운데로 붙는다
        return bisect.bisect_right(self.tedges, prev3)


class _Picker:
    def __init__(self, steps: Steps):
        self.steps = steps
        self._pools: dict = {}
        self._bands, self._min_cell = config.PREDICT_ELO_BANDS, config.PREDICT_MIN_CELL

    def pool(self, elo: float, width: float) -> _Pool:
        key = (round(elo / _CELL), width)
        p = self._pools.get(key)
        if p is None:
            p = self._pools[key] = _Pool(self.steps, key[0] * _CELL, width)
        return p

    def candidates(self, elo: float, prev3: float | None, g: int):
        """→ (걸음 목록, 사흘 묶음 목록, 띠를 넓혔나) · 없으면 (None, None, True)."""
        b = self.steps.bin_of(g)
        widest = None
        cell = round(elo / _CELL)
        pools, need = self._pools, self._min_cell
        for i, width in enumerate(self._bands):
            p = pools.get((cell, width)) or self.pool(elo, width)
            if prev3 is not None and p.flow:
                k = (b, bisect.bisect_right(p.tedges, prev3))      # ① 흐름 3분위까지 맞춘 칸
                cand = p.cells.get(k)
                if cand and len(cand) >= need:
                    return cand, p.blocks[k], i > 0
            cand = p.cells.get(b)                                  # ① 을 푼 칸
            if cand and len(cand) >= need:
                return cand, p.blocks[b], i > 0
            widest = cand or widest
        if widest:
            return widest, [], True
        return None, None, True


# ── 시뮬레이션 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SimInput:
    elo: float                         # 지금 내 ELO
    hist: tuple = ()                   # 내 최근 하루 변화(오래된 것부터, 최대 3) — 없으면 첫 사흘은 흐름을 안 맞춘다
    daily: tuple = ()                  # 내 최근 하루 경기 수(쉬는 날 0 포함)
    now_cuts: dict = field(default_factory=dict)   # 지금 스냅숏의 {순위: ELO}


@dataclass
class SimResult:
    p: dict                            # {목표 순위: 그 안에 들 확률}
    ranks: list[int]                   # 경로별 끝 순위(OUTSIDE_RANK = 1만 위 밖) — 오름차순
    end_elos: list[float]
    widened: float                     # 띠를 넓힌 경로 비율
    late: float                        # 지금 컷이 뽑은 최종 컷을 넘은 경로 비율


def rank_of(elo: float, cuts: dict[int, float]) -> int:
    """컷 줄({순위: ELO})에서 순위 — 순위 사이는 log-순위로 보간. 맨 아래 컷 밑이면 OUTSIDE_RANK."""
    rs = sorted(cuts)
    if not rs:
        return OUTSIDE_RANK
    if elo >= cuts[rs[0]]:
        return rs[0]
    for ra, rb in zip(rs, rs[1:]):
        ea, eb = cuts[ra], cuts[rb]
        if ea >= elo >= eb:
            t = (ea - elo) / (ea - eb) if ea > eb else 1.0
            return max(ra, min(rb, round(math.exp(math.log(ra) + t * (math.log(rb) - math.log(ra))))))
    return OUTSIDE_RANK


def _monotone(cuts: dict[int, float]) -> dict[int, float]:
    """위 순위 컷 ≥ 아래 순위 컷 — max(지금 컷) 를 순위마다 걸어도 순서가 깨지지 않게 아래로 눌러 둔다."""
    out, run = {}, math.inf
    for r in sorted(cuts):
        run = min(run, cuts[r])
        out[r] = run
    return out


def season_weights(lengths: list[int | None], path_len: int) -> list[float]:
    """지난 시즌 컷을 뽑을 무게 — 시즌 길이가 이 경로 시즌 길이와 가까울수록 크게(PREDICT_SEASON_WEIGHT_DAYS 마다 절반).
    시즌이 PREDICT_EQUAL_WEIGHT_SEASONS 이하거나 길이를 모르면 같은 무게."""
    if len(lengths) <= config.PREDICT_EQUAL_WEIGHT_SEASONS:
        return [1.0] * len(lengths)
    return [1.0 if L is None else 0.5 ** (abs(L - path_len) / config.PREDICT_SEASON_WEIGHT_DAYS) for L in lengths]


def simulate(inp: SimInput, steps: Steps, seasons: list[tuple[int | None, dict]], ends: list[int],
             season_len_today: int, rng: random.Random, n: int = config.PREDICT_RUNS) -> SimResult:
    """경로 n 개. seasons: [(시즌 길이, {순위: 최종 컷})] · ends: 남은 날 수 후보(같은 무게) ·
    season_len_today: 오늘까지 지난 날 — 경로 시즌 길이 = 이것 + 남은 날."""
    picker = _Picker(steps)
    daily = list(inp.daily) or [0]
    targets = config.PREDICT_TARGETS
    hits = Counter()
    ranks, end_elos = [], []
    widened = late = 0
    choice, candidates, min_blocks = rng.choice, picker.candidates, config.PREDICT_MIN_BLOCKS
    lens = [L for L, _c in seasons]
    for _ in range(n):
        left = choice(ends)
        _L, raw = rng.choices(seasons, weights=season_weights(lens, season_len_today + left))[0]
        cuts = _monotone({r: max(e, inp.now_cuts.get(r, -math.inf)) for r, e in raw.items()})
        if any(inp.now_cuts.get(r, -math.inf) > raw.get(r, math.inf) for r in targets):
            late += 1
        e, hist, wide = inp.elo, list(inp.hist), False
        day = 0
        while day < left:
            g = choice(daily)
            if g <= 0:
                hist.append(0.0)
                day += 1
                continue
            cand, blocks, w = candidates(e, hist[-1] + hist[-2] + hist[-3] if len(hist) >= 3 else None, g)
            wide |= w
            if cand is None:
                hist.append(0.0)      # 표본 밖 — 밀어 올리지 않는다
                day += 1
                continue
            if left - day >= 3 and len(blocks) >= min_blocks:
                s = choice(blocks)
                e += s.d
                hist.append(s.d)
                for d, _g in s.nxt[:2]:
                    e += d
                    hist.append(d)
                day += 3
            else:
                d = choice(cand).d
                e += d
                hist.append(d)
                day += 1
        widened += wide
        r = rank_of(e, cuts)
        ranks.append(r)
        end_elos.append(e)
        for t in targets:
            if t in cuts and e >= cuts[t]:
                hits[t] += 1
    ranks.sort()
    return SimResult({t: hits[t] / n for t in targets}, ranks, end_elos, widened / n, late / n)


# ── 종료일 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EndGuess:
    days: tuple                         # 남은 날 수 후보(같은 무게)
    source: str                         # notice · estimate · soon
    text: str                           # "11/12(공지)" · "10/29~11/19(중앙 11/12, 추정)" · "곧 끝날 것으로 보임"


def resolve_notice(notice: tuple[date, date] | None, table_start: date | None) -> tuple[date | None, date | None]:
    """공지 (종료, 시작) 를 시즌표의 지금 시즌 시작과 대조 — 쓸 때 한다(받을 때는 시즌표가 낡았을 수 있다).
    → (쓸 종료일, 지금 시즌 시작). 시즌표가 공지보다 옛 시즌이면(캐시가 늦음) 공지를 믿고, 새 시즌이면 버린다."""
    if notice is None:
        return None, table_start
    end, start = notice
    if table_start is None or table_start == start:
        return end, start
    if table_start < start:
        return end, start
    return None, table_start


def guess_end(today: date, start: date, recent_lengths: list[int], notice_end: date | None) -> EndGuess:
    if notice_end is not None:
        return EndGuess((max((notice_end - today).days, 0),), "notice", f"{notice_end:%m/%d}(공지)")
    elapsed = (today - start).days
    longer = [L for L in recent_lengths[:config.PREDICT_RECENT_SEASONS] if L > elapsed]
    if not longer:
        return EndGuess(tuple(range(1, config.PREDICT_SOON_DAYS + 1)), "soon", "곧 끝날 것으로 보임")
    ends = sorted(start + timedelta(days=L) for L in longer)
    mid = ends[(len(ends) - 1) // 2]
    text = f"{mid:%m/%d}(추정)" if ends[0] == ends[-1] else f"{ends[0]:%m/%d}~{ends[-1]:%m/%d}(중앙 {mid:%m/%d}, 추정)"
    return EndGuess(tuple((e - today).days for e in ends), "estimate", text)


# ── 내 상태 ────────────────────────────────────────────────────────────────

def my_state(elo_rows: list[dict]) -> tuple[float | None, tuple, int | None]:
    """elo_history 줄(오래된 것부터) → (지금 ELO, 최근 하루 변화 최대 3개, 프로필 번호).
    하루 변화는 날마다 마지막 값의 차 — 날이 하루씩 이어진 구간만(빈 날이 있으면 그 앞은 버린다)."""
    if not elo_rows:
        return None, (), None
    by_day: dict[date, float] = {}
    sn = None
    for r in elo_rows:
        try:
            by_day[datetime.fromisoformat(r["taken_at"]).date()] = r["elo"]
        except (ValueError, KeyError, TypeError):
            continue
        sn = r.get("profile_sn") or sn
    if not by_day:
        return None, (), sn
    days = sorted(by_day)
    deltas = []
    for a, b in zip(days, days[1:]):
        if (b - a).days != 1:
            deltas = []
            continue
        deltas.append(by_day[b] - by_day[a])
    return by_day[days[-1]], tuple(deltas[-3:]), sn


def daily_counts(match_dates: list[datetime], days: int = config.PREDICT_RECENT_DAYS) -> tuple:
    """마지막 경기 날까지 days 일의 하루 경기 수(쉬는 날 0) — 트레이에서 검색 없이 돌아도 "경기 0"이 끼지 않게."""
    if not match_dates:
        return ()
    last = max(match_dates).date()
    first = last - timedelta(days=days - 1)
    c = Counter(d.date() for d in match_dates if d.date() >= first)
    return tuple(c.get(first + timedelta(days=i), 0) for i in range(days))


# ── 화면용 ────────────────────────────────────────────────────────────────

@dataclass
class Prediction:
    ok: bool
    message: str = ""                  # 못 냈을 때 원인 문구
    p: dict = field(default_factory=dict)
    lo: int | None = None              # 예상 순위 80% 범위 — OUTSIDE_RANK 면 "1만 위 밖"
    hi: int | None = None
    end_text: str = ""
    end_source: str = ""
    notes: tuple = ()
    season_start: date | None = None
    profile_sn: int | None = None


def _pct(p: float) -> str:
    if p < 0.01:
        return "<1%"
    if p > 0.99:
        return ">99%"
    return f"{p * 100:.0f}%"


def _rank_txt(r: int) -> str:
    return "1만 위 밖" if r >= OUTSIDE_RANK else f"{r:,}위"


def describe(pr: Prediction) -> str:
    """화면 글 한 덩이 — 못 냈으면 원인 문구."""
    if not pr.ok:
        return pr.message
    probs = " · ".join(f"{t:,}위 안 {_pct(pr.p.get(t, 0.0))}" for t in config.PREDICT_TARGETS)
    rng_ = _rank_txt(pr.lo) if pr.lo == pr.hi else f"{_rank_txt(pr.lo)}~{_rank_txt(pr.hi)}"
    lines = [f"지금 흐름이 이어지면 — {probs} · 예상 순위 80% 범위 {rng_}",
             f"가정: 시즌 종료 {pr.end_text}"]
    lines.append(" · ".join((*pr.notes, NOTE_UNVERIFIED)))
    return "\n".join(lines)


def _wait_days(steps: Steps, elo: float | None) -> int | None:
    """모든 데이터 조건 중 가장 늦게 차는 것까지 남은 날(하루 한 번 수집 가정). 모르면 None."""
    if steps.pairs <= 0:
        return None
    need = config.PREDICT_MIN_PAIRS - steps.pairs
    if elo is not None:
        lo, hi = steps.in_band(elo, config.PREDICT_ELO_BANDS[0])
        n = hi - lo
        if n <= 0:
            return None
        per_day = n / steps.pairs
        need = max(need, math.ceil((config.PREDICT_MIN_STEPS - n) / per_day))
    return max(need, 1)


def predict_for(*, elo_rows: list[dict], match_dates: list[datetime], rank_conn, seasons: list,
                notice: tuple[date, date] | None, now: datetime,
                web_on: bool | None = None, collect_on: bool | None = None,
                rng: random.Random | None = None, n: int = config.PREDICT_RUNS) -> Prediction:
    """화면 진입점 — 조건을 차례로 보고, 다 맞으면 시뮬레이션. seasons: seasons.Season 목록(최신 먼저 아니어도 됨)."""
    import rankcollect
    web_on = config.WEB_DATA if web_on is None else web_on
    collect_on = config.RANK_COLLECT if collect_on is None else collect_on
    if not (web_on and collect_on) or rank_conn is None:
        return Prediction(False, MSG_COLLECT_OFF)
    today = now.date()
    elo, hist, sn = my_state(elo_rows)
    steps = day_steps(rank_conn)
    if steps.pairs < config.PREDICT_MIN_PAIRS:
        w = _wait_days(steps, elo)
        return Prediction(False, MSG_GATHERING.format(n=w) if w else MSG_GATHERING_UNKNOWN)
    if elo is None:
        return Prediction(False, MSG_NO_ELO)
    now_cuts = rankcollect.cut_elo(rank_conn)
    bottom = now_cuts.get(config.RANK_TIERS[-1])
    if bottom is None or elo < bottom:
        return Prediction(False, MSG_OUTSIDE)
    width = config.PREDICT_ELO_BANDS[0]
    lo, hi = steps.in_band(elo, width)
    if hi - lo < config.PREDICT_MIN_STEPS or steps.drop_rate(elo, width) >= config.PREDICT_MAX_DROP:
        return Prediction(False, MSG_THIN)
    daily = daily_counts(match_dates)
    if sum(daily) < analysis.MIN_BASE:
        return Prediction(False, MSG_FEW_GAMES)

    ended = sorted((s for s in seasons if s.end <= today), key=lambda s: s.end, reverse=True)
    table_start = ended[0].end if ended else None
    notice_end, start = resolve_notice(notice, table_start)
    cuts = rankcollect.season_cuts(rank_conn)
    length = {s.no: (s.end - s.start).days for s in seasons}
    past = [(length.get(no), c) for no, c in sorted(cuts.items(), reverse=True)]
    if start is None or len(past) < config.PREDICT_MIN_SEASONS:
        return Prediction(False, MSG_NO_SEASONS)
    if notice_end is not None and notice_end <= today and table_start == start:
        return Prediction(False, MSG_CLOSING)
    recent = [(s.end - s.start).days for s in ended]
    end = guess_end(today, start, recent, notice_end)

    res = simulate(SimInput(elo, hist, daily, now_cuts), steps, past, list(end.days),
                   (today - start).days, rng or random.Random(config.PREDICT_SEED), n)
    notes = []
    if res.widened > config.PREDICT_WARN_SHARE:
        notes.append(NOTE_THIN_BAND)
    if res.late > config.PREDICT_WARN_SHARE:
        notes.append(NOTE_LATE)
    q = lambda f: res.ranks[min(len(res.ranks) - 1, int(f * len(res.ranks)))]  # noqa: E731
    return Prediction(True, p=res.p, lo=q(config.PREDICT_RANGE[0]), hi=q(config.PREDICT_RANGE[1]),
                      end_text=end.text, end_source=end.source, notes=tuple(notes), season_start=start,
                      profile_sn=sn)
