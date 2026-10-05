"""흐름 분석 회귀 테스트 — 임계값 경계와 가중치 정규화.

analysis 는 "표본이 모자라면 말하지 않는다"가 핵심 규칙이라, 여기서 검증할
것도 **문장이 나오는 조건**이다. 픽스처 4경기로는 패턴 규칙(MIN_BASE=15)을
못 건드려서, 시나리오를 심은 합성 경기를 만들어 쓴다 — 실제 응답 파싱은
test_parsing.py 가 픽스처로 이미 고정하고 있고, 여기서 볼 건 그 위의
집계·판정 로직이다.

네트워크 없이 즉시 돈다. pytest 없이도 `python tests/test_analysis.py` 로 실행.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
import models

OUID = "me"


def _gt(period: int, sec: int) -> int:
    return (period << 24) | sec


def _shot(result: int, period: int = 0, sec: int = 600, typ: int = 1,
          x: float = 0.9, y: float = 0.5, pen: bool = True) -> dict:
    return {"result": result, "goalTime": _gt(period, sec), "type": typ,
            "x": x, "y": y, "inPenalty": pen}


def _squad(formation=(4, 1, 2, 0, 3)) -> list[dict]:
    ranges = [range(1, 9), range(9, 12), range(12, 17), range(17, 20),
              range(20, 28)]
    out = [{"spPosition": 0, "spId": 100000001, "status": {}}]
    for n, rng in zip(formation, ranges):
        for i in range(n):
            out.append({"spPosition": list(rng)[i], "spId": 200000000 + i,
                        "status": {}})
    return out


def _match(i: int, result: str, gf: int, ga: int, poss: int,
           my_goals: list, opp_goals: list, opp_name: str = "상대A",
           my_shots: int = 10) -> dict:
    inv = {"승": "패", "패": "승", "무": "무"}[result]
    my_sd = [_shot(3, p, s) for p, s in my_goals]
    # 골이 아닌 슛은 박스 밖에서 — 전부 페널티 안이면 xG 가 비현실적으로 커진다.
    my_sd += [_shot(2 if k % 2 else 1, 0, 100 + k, x=0.62, y=0.4, pen=False)
              for k in range(max(my_shots - len(my_goals), 0))]
    op_sd = [_shot(3, p, s) for p, s in opp_goals]
    return {
        "matchId": f"m{i:04d}",
        "matchDate": f"2026-07-{(i % 28) + 1:02d}T20:00:00",
        "matchType": 50,
        "matchInfo": [
            {"ouid": OUID, "nickname": "나", "division": 900,
             "matchDetail": {"matchResult": result, "possession": poss,
                             "averageRating": 7.0},
             "shoot": {"goalTotal": gf, "shootTotal": len(my_sd),
                       "effectiveShootTotal": len(my_goals) + 2},
             "pass": {"passTry": 300, "passSuccess": 250},
             "shootDetail": my_sd, "player": _squad()},
            {"ouid": "opp", "nickname": opp_name, "division": 900,
             "matchDetail": {"matchResult": inv, "possession": 100 - poss,
                             "averageRating": 7.0},
             "shoot": {"goalTotal": ga, "shootTotal": len(op_sd) + 5,
                       "effectiveShootTotal": ga + 1},
             "pass": {"passTry": 300, "passSuccess": 250},
             "shootDetail": op_sd, "player": _squad()},
        ],
    }


def _build(n_old: int = 60, n_recent: int = 20):
    """선제골 의존 + 막판 실점을 심은 합성 전적. 최근 구간은 선제골이 급감한다."""
    ds = []
    for i in range(n_old):
        if i % 3 != 0:                      # 2/3 경기에서 선제골 → 대부분 승
            ds.append(_match(i, "승", 2, 1, 50, [(0, 600), (1, 1200)],
                             [(1, 2700)]))  # 실점은 막판(75~90분)에
        else:
            ds.append(_match(i, "패", 0, 2, 50, [], [(0, 400), (1, 2700)]))
    for i in range(n_old, n_old + n_recent):
        if i % 5 == 0:                      # 최근엔 1/5 로 급감
            ds.append(_match(i, "승", 2, 1, 50, [(0, 600)], [(1, 2700)]))
        else:
            ds.append(_match(i, "패", 1, 2, 50, [(1, 1500)],
                             [(0, 300), (1, 2740)]))
    ds.reverse()                            # 최신순 — narrate 의 전제
    return [models.parse_match(d, OUID) for d in ds], ds


def _find(insights, needle: str):
    return next((i for i in insights if needle in i.headline), None)


def test_empty_and_tiny_sample():
    assert analysis.narrate([], [], OUID) == []
    ms, ds = _build()
    # MIN_FLOW 미만이면 흐름조차 말하지 않는다.
    few = analysis.MIN_FLOW - 1
    assert analysis.narrate(ms[:few], ds[:few], OUID) == []
    # 딱 MIN_FLOW 면 흐름 요약은 나오되, 패턴(MIN_BASE=15)은 아직 안 나온다.
    at_min = analysis.narrate(ms[:analysis.MIN_FLOW], ds[:analysis.MIN_FLOW], OUID)
    assert at_min, "MIN_FLOW 경기면 흐름 요약은 나와야 한다"
    assert all(i.section == analysis.SEC_FLOW for i in at_min), \
        [i.section for i in at_min]


def test_sections_and_limit():
    ms, ds = _build()
    ins = analysis.narrate(ms, ds, OUID, per_section=3)
    for sec in analysis.SECTIONS:
        assert len([i for i in ins if i.section == sec]) <= 3, sec
    # 섹션 순서가 흐름 → 이기는 → 지는 으로 유지돼야 한다.
    order = [analysis.SECTIONS.index(i.section) for i in ins]
    assert order == sorted(order), order
    # 섹션 안은 weight 내림차순.
    for sec in analysis.SECTIONS:
        ws = [i.weight for i in ins if i.section == sec]
        assert ws == sorted(ws, reverse=True), (sec, ws)


def test_first_goal_rule_fires():
    ms, ds = _build()
    ins = analysis.narrate(ms, ds, OUID)
    hit = _find(ins, "선제골을 넣으면")
    assert hit is not None, [i.headline for i in ins]
    assert hit.section == analysis.SEC_WIN
    hit = _find(ins, "먼저 실점하면")
    assert hit is not None and hit.section == analysis.SEC_LOSE


def test_late_conceding_rule_fires():
    ms, ds = _build()
    ins = analysis.narrate(ms, ds, OUID)
    hit = _find(ins, "75~90")
    assert hit is not None, [i.headline for i in ins]
    assert hit.section == analysis.SEC_LOSE
    assert "막판" in hit.detail, hit.detail


def test_contrast_rule_detects_blocked_pattern():
    """전체 패턴 대비 최근이 나빠지면 '이기는 방식이 막혔다'가 나와야 한다."""
    ms, ds = _build()
    ins = analysis.narrate(ms, ds, OUID)
    hit = _find(ins, "이기는 방식이 막혀")
    assert hit is not None, [i.headline for i in ins]
    assert hit.section == analysis.SEC_FLOW


def test_no_contrast_when_recent_matches_baseline():
    """최근이 평소와 같으면 대조 문장을 내지 않는다 — 없는 변화를 지어내면 안 된다."""
    ds = [_match(i, "승" if i % 3 != 0 else "패",
                 2 if i % 3 != 0 else 0, 1 if i % 3 != 0 else 2, 50,
                 [(0, 600)] if i % 3 != 0 else [], [(1, 2700)])
          for i in range(80)]
    ds.reverse()
    ms = [models.parse_match(d, OUID) for d in ds]
    ins = analysis.narrate(ms, ds, OUID)
    assert _find(ins, "이기는 방식이 막혀") is None, [i.headline for i in ins]


def test_basic_goal_type_is_not_reported():
    """'일반(D)' 같은 기본 유형은 비중이 아무리 높아도 문장을 내지 않는다.

    _build 의 골은 전부 type=1(일반) — 정보가 없는 "득점의 100%가 일반 유형"
    문장이 다시 새어나오면 여기서 잡힌다.
    """
    ms, ds = _build()
    ins = analysis.narrate(ms, ds, OUID)
    for i in ins:
        for basic in analysis.BASIC_GOAL_TYPES:
            assert basic not in i.headline, i.headline


def test_weight_is_normalised_across_rules():
    """분모가 다른 규칙끼리 가중치가 비교 가능한 범위에 있어야 한다.

    생짜 √n 을 쓰면 슛 수(수백)를 분모로 쓰는 결정력 규칙이 경기 수(수십)
    기반 규칙을 전부 눌러 섹션 안 순위가 뒤집힌다.
    """
    ms, ds = _build()
    ins = [i for i in analysis.narrate(ms, ds, OUID)
           if i.weight != float("inf")]
    assert ins
    top = max(i.weight for i in ins)
    bottom = min(i.weight for i in ins)
    assert top / max(bottom, 1e-9) < 100, [(i.headline, i.weight) for i in ins]


def test_weight_cap():
    # 표본이 아무리 커도 _CONF_CAP 배를 넘지 않는다.
    assert analysis._w(10, 10 ** 6, 8) == 10 * analysis._CONF_CAP
    # 최소 표본과 같으면 배수 1 — gap 그대로.
    assert analysis._w(10, 8, 8) == 10


# ── 변이 전수 측정 보강(2026-10-02, docs/mutation) — 규칙마다 '말한다/안 말한다'의 경계 ──────
def _seq(results, opp="상대A", my_goals=None):
    """결과 문자열 목록(최신순) → (matches, details). 골·슛은 결과에 맞춘 최소한."""
    ds = []
    for i, r in enumerate(results):
        gf, ga = {"승": (1, 0), "무": (0, 0), "패": (0, 1)}[r]
        mg = my_goals[i] if my_goals else ([(0, 600)] if gf else [])
        ds.append(_match(1000 - i, r, len(mg) if my_goals else gf, ga, 50, mg,
                         [(1, 1200)] if ga else [], opp_name=opp))
    return [models.parse_match(d, OUID) for d in ds], ds


def test_flow_streak_needs_three():
    ms, ds = _seq(["승", "승", "승", "패", "패"])
    assert _find(analysis._flow(ms, ds, OUID, 20), "3연승")
    ms, ds = _seq(["무", "무", "무", "승", "패"])
    assert _find(analysis._flow(ms, ds, OUID, 20), "3연속 무승부")
    ms, ds = _seq(["승", "승", "패", "패", "패"])
    assert not _find(analysis._flow(ms, ds, OUID, 20), "연승"), "2연승인데 말했다"


def test_flow_compares_with_earlier_only_when_enough():
    recent = ["승"] * 5
    ms, ds = _seq(recent + ["패"] * analysis.MIN_BASE)          # 이전 구간이 딱 기준만큼
    assert _find(analysis._flow(ms, ds, OUID, 5), "올랐습니다")
    ms, ds = _seq(["패"] * 5 + ["승"] * analysis.MIN_BASE)
    assert _find(analysis._flow(ms, ds, OUID, 5), "떨어졌습니다")
    ms, ds = _seq(recent + ["패"] * (analysis.MIN_BASE - 1))    # 하나 모자라면 비교 안 한다
    assert not _find(analysis._flow(ms, ds, OUID, 5), "대비")
    ms, ds = _seq(["승", "패"] * 10 + ["승", "패"] * 10)          # 같은 승률 — 차이 없음
    assert not _find(analysis._flow(ms, ds, OUID, 20), "대비")


def test_lead_lost_rule_needs_three_and_twenty_percent():
    def run(lost):
        res = ["패"] * lost + ["승"] * (10 - lost)               # 10경기 모두 내가 선제골
        goals = [[(0, 100)]] * 10
        ds = []
        for i, r in enumerate(res):
            ga = [(1, 900), (1, 1200)] if r == "패" else []
            ds.append(_match(i, r, 1, len(ga), 50, goals[i], ga))
        return analysis._clutch_rules(ds, OUID, base_rate=50.0)
    assert _find(run(3), "앞서고도 진 경기가 3번")              # 3/10 = 30%
    assert not _find(run(2), "앞서고도 진 경기"), "2번인데 말했다"


def test_possession_rule_needs_two_bands_and_a_gap():
    def build(spec):
        ds = []
        for i, (poss, r) in enumerate(spec):
            gf, ga = (1, 0) if r == "승" else (0, 1)
            ds.append(_match(i, r, gf, ga, poss, [(0, 600)] if gf else [], [(0, 700)] if ga else []))
        return analysis._possession_rules(ds, OUID, total=len(ds))
    n = analysis.MIN_COND
    assert build([(70, "승")] * n) == [], "구간이 하나뿐인데 비교했다"
    assert build([(70, "승")] * n + [(30, "패")] * n)              # 100% vs 0%
    half = [(70, "승"), (70, "패")] * (n // 2)
    assert build(half + half[:0] + [(30, "승"), (30, "패")] * (n // 2)) == [], "승률이 같은데 말했다"


def test_finishing_rule_direction_and_floor():
    from stats import shot_xg, goal_type_name
    xg1 = shot_xg(0.9, 0.5, True, goal_type_name(1))             # _shot 기본 좌표의 기대득점

    def build(n_shots, n_goals):
        shots = [_shot(3) for _ in range(n_goals)] + [_shot(1) for _ in range(n_shots - n_goals)]
        d = _match(0, "승", n_goals, 0, 50, [], [])
        d["matchInfo"][0]["shootDetail"] = shots
        return analysis._finishing_rules([d], OUID)
    n = analysis.MIN_SHOTS
    expect = n * xg1
    hi, lo = round(expect * 1.3), round(expect * 0.6)        # +30% · -40% (기준 ±25%)
    assert hi <= n - 1, "많이 넣은 경우가 슛 수를 넘는다 — 비율을 낮춘다"
    assert _find(build(n, hi), "잘 넣고") and _find(build(n, lo), "못 넣고")
    assert build(n, round(expect)) == [], "기대만큼 넣었는데 말했다"
    assert build(n - 1, hi) == [], "슛이 기준보다 적은데 말했다"


def test_goal_type_rule_needs_enough_and_share():
    def build(n_header, n_basic):
        ga = [(0, 100 + k) for k in range(n_header + n_basic)]
        d = _match(0, "패", 0, len(ga), 50, [], ga)
        types = [3] * n_header + [1] * n_basic                    # 3=헤더(특수) · 1=일반
        for sd, t in zip(d["matchInfo"][1]["shootDetail"], types):
            sd["type"] = t
        return analysis._goal_type_rules([d], OUID)
    g = analysis.MIN_GOALS
    assert _find(build(g // 2, g - g // 2), "헤더")                # 절반이 헤더
    assert build(2, g - 2) == [], "헤더가 조금인데 치우쳤다고 했다"  # 2/15 = 13% < 25%
    assert build(7, g - 8) == [], "골이 기준보다 적은데 말했다"  # 7/14 = 50% 지만 14 < 15


def test_streak_rule_gap_and_sample_edges():
    # 날짜 순 패 승 패 승 … (20경기) — 1연패 뒤 다음 경기는 10번 전부 승(100%), 1연승 뒤는 9번 전부 패(0%).
    ms, _ = _seq(["승", "패"] * 10)
    one_loss = lambda found: [i for i in found if "1연패 직후" in i.headline]  # noqa: E731
    assert one_loss(analysis._streak_rules(ms, base_rate=88.0, total=20)), "12%p 차이인데 침묵"
    assert not one_loss(analysis._streak_rules(ms, base_rate=91.0, total=20)), "9%p 차이인데 말했다"
    assert one_loss(analysis._streak_rules(ms, base_rate=90.0, total=20)), "정확히 GAP 인데 침묵"
    # 표본 10경기: 전체 500 이면 기준 ceil(500×2%)=10 → 말한다, 501 이면 11 → 침묵
    assert one_loss(analysis._streak_rules(ms, base_rate=50.0, total=500))
    assert not one_loss(analysis._streak_rules(ms, base_rate=50.0, total=501)), "표본 미달인데 말했다"
    # narrate 에 실제로 배선돼 있다 — 20경기 전체 승률 50% 에서 1연패 뒤 100%
    assert _find(analysis.narrate(ms, [], OUID), "1연패 직후")


def test_opponent_rule_needs_games_and_name():
    ms, _ = _seq(["패"] * analysis.MIN_OPP, opp="천적")
    assert _find(analysis._opponent_rules(ms, base_rate=60.0), "천적")
    ms, _ = _seq(["패"] * (analysis.MIN_OPP - 1), opp="천적")
    assert analysis._opponent_rules(ms, base_rate=60.0) == []
    ms, _ = _seq(["패"] * analysis.MIN_OPP, opp="-")
    assert analysis._opponent_rules(ms, base_rate=60.0) == [], "이름 없는 상대를 말했다"


# ── 기준값과 정확히 같을 때도 말한다(경계 변이 3순위, 2026-10-02) ─────────────────────────
# '이상(>=)'을 '초과(>)'로 바꾸면 기준값에 딱 맞는 경우만 갈린다 — 그 경우를 일부러 만든다.
# 비율은 부동소수 오차가 없게 고른다(20경기 중 9 = 45%, 슛 64개 × 0.5 = 32).
def _g(i, first, res):
    """선제골 시나리오 한 경기. first: 'me'(내가 먼저) · 'opp'(먼저 실점) · None(무득점)."""
    if first == "me":
        return _match(i, res, 1, 1 if res == "패" else 0, 50, [(0, 100)],
                      [(0, 200), (1, 300)] if res == "패" else [])
    if first == "opp":
        return _match(i, res, 1 if res == "승" else 0, 1, 50,
                      [(0, 200), (1, 300)] if res == "승" else [], [(0, 100)])
    return _match(i, res, 0, 0, 50, [], [])


def _clutch_at(spec_me, spec_opp, base_rate):
    """spec_*: 결과 문자열 목록 — 선제골 경기들과 선제 실점 경기들."""
    ds = [_g(i, "me", r) for i, r in enumerate(spec_me)]
    ds += [_g(100 + i, "opp", r) for i, r in enumerate(spec_opp)]
    return analysis._clutch_rules(ds, OUID, base_rate=base_rate)


def test_flow_compare_fires_exactly_at_gap():
    # 최근 5경기 3승(60%) · 이전 20경기 10승(50%) → 차이 딱 10%p(GAP)
    ms, ds = _seq(["승", "승", "승", "패", "패"] + ["승", "패"] * 10)
    assert _find(analysis._flow(ms, ds, OUID, 5), "10%p 올랐습니다")


def test_clutch_rules_fire_exactly_at_thresholds():
    n = analysis.MIN_COND                                   # 8
    # 선제골 8경기(기준 딱) · 승률 75% · 전체 65% → 차이 딱 10%p(GAP)
    assert _find(_clutch_at(["승"] * 6 + ["패"] * 2, [], 65.0), "선제골을 넣으면")
    # 선제 실점 8경기 · 승률 25% · 전체 35% → 차이 딱 10%p
    assert _find(_clutch_at([], ["승"] * 2 + ["패"] * 6, 35.0), "먼저 실점하면")
    # 앞서고 진 경기 3/15 = 딱 20%
    assert _find(_clutch_at(["패"] * 3 + ["승"] * 12, [], 50.0), "앞서고도 진 경기가 3번")
    assert n == 8 and analysis.GAP == 10.0


def test_contrast_fires_exactly_at_gap_rate_and_sizes():
    def run(prev, recent):
        ds = [_g(i, f, r) for i, (f, r) in enumerate(recent + prev)]   # 최신순 — 앞이 최근
        ms = [models.parse_match(d, OUID) for d in ds]
        return analysis._contrast(ms, ds, OUID, window=len(recent))
    # 평소 선제골 9/20 = 45% → 최근 3/5 = 60% : 차이 딱 15%p(GAP_WIDE) · 선제골 승률 100%
    prev = [("me", "승")] * 9 + [("opp", "패")] * 11
    recent = [("me", "승")] * 3 + [("opp", "패")] * 2
    assert _find(run(prev, recent), "앞서서 시작하는 빈도")
    # 선제골 승률이 딱 50%(4승 4패) — 평소 8/20 = 40% → 최근 5/5 = 100%
    prev = [("me", "승")] * 4 + [("me", "패")] * 4 + [("opp", "패")] * 12
    assert _find(run(prev, [("me", "승")] * 5), "앞서서 시작하는 빈도")
    # 크기가 딱 기준: 이전 15경기(MIN_BASE) 중 선제골 갈린 8경기(MIN_COND) · 최근 5경기(MIN_FLOW)
    prev = [("me", "승")] * 8 + [(None, "무")] * 7
    recent = [("opp", "패")] * 5
    assert _find(run(prev, recent), "이기는 방식이 막혀")


def test_minute_rule_fires_at_min_goals_and_share():
    def run(spread):
        """spread: 15분 구간별 실점 수 → 한 경기에 다 넣는다."""
        goals = []
        for k, n in enumerate(spread):
            period, base = (0, k * 900) if k < 3 else (1, (k - 3) * 900)
            goals += [(period, base + 60 + j) for j in range(n)]
        return analysis._minute_rules([_match(0, "패", 0, len(goals), 50, [], goals)], OUID)
    # 딱 15골(MIN_GOALS) — 한 구간에 몰려 있음
    assert _find(run([10, 1, 1, 1, 1, 1]), "실점의")
    # 16골 중 4골 = 딱 25%(균등 1/6 의 1.5배)
    assert _find(run([4, 3, 3, 2, 2, 2]), "실점의 25%")


def test_possession_formation_finishing_type_opponent_exact():
    # 점유율: 우세 20경기 13승(65%) · 열세 20경기 10승(50%) → 차이 딱 15%p
    ds = []
    for i, (poss, r) in enumerate([(70, "승")] * 13 + [(70, "패")] * 7 + [(30, "승")] * 10 + [(30, "패")] * 10):
        ds.append(_match(i, r, 1 if r == "승" else 0, 0 if r == "승" else 1, poss,
                         [(0, 600)] if r == "승" else [], [] if r == "승" else [(0, 700)]))
    assert analysis._possession_rules(ds, OUID, total=len(ds))
    # 전술: 상대 전술 8경기(기준 딱) 승률 25% · 전체 40% → 딱 -15%p / 50% · 전체 35% → 딱 +15%p
    def form(wins, base):
        ds = [_match(i, "승" if i < wins else "패", 0, 0, 50, [], []) for i in range(analysis.MIN_COND)]
        return analysis._formation_rules(ds, OUID, base_rate=base, total=analysis.MIN_COND)
    assert _find(form(2, 40.0), "나오면 승률 25%")
    assert _find(form(4, 35.0), "일 때 승률 50%")
    # 결정력: 슛 64개 × 기대득점 0.5 = 32, 40골 → 딱 +25%
    import stats as st_
    orig = st_.shot_xg
    st_.shot_xg = lambda *a, **k: 0.5
    try:
        d = _match(0, "승", 40, 0, 50, [], [])
        d["matchInfo"][0]["shootDetail"] = [_shot(3)] * 40 + [_shot(1)] * 24
        assert _find(analysis._finishing_rules([d], OUID), "잘 넣고")
    finally:
        st_.shot_xg = orig
    # 골 유형: 16골 중 헤더 4 = 딱 25%
    d = _match(0, "패", 0, 16, 50, [], [(0, 100 + k) for k in range(16)])
    for k, sd in enumerate(d["matchInfo"][1]["shootDetail"]):
        sd["type"] = 3 if k < 4 else 1
    assert _find(analysis._goal_type_rules([d], OUID), "헤더")
    # 상대: 4경기 1승(25%) · 전체 47.5% → 딱 22.5%p(GAP_WIDE × 1.5)
    ms, _ = _seq(["승", "패", "패", "패"], opp="천적")
    assert _find(analysis._opponent_rules(ms, base_rate=47.5), "천적")


def test_patterns_start_exactly_at_min_base():
    ms, ds = _seq(["패"] * analysis.MIN_OPP + ["승"] * (analysis.MIN_BASE - analysis.MIN_OPP), opp="천적")
    for m in ms[analysis.MIN_OPP:]:
        m.opponent = "기타"                                   # 천적에게만 4패
    assert len(ms) == analysis.MIN_BASE and analysis._patterns(ms, ds, OUID), "딱 기준 경기 수인데 침묵했다"


def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[OK]   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
