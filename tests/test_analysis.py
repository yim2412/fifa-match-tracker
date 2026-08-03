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


def test_as_text_shape():
    ms, ds = _build()
    text = analysis.as_text(analysis.narrate(ms, ds, OUID))
    assert f"[{analysis.SEC_FLOW}]" in text
    assert "·" in text
    assert analysis.as_text([]) == "분석할 만큼 경기가 쌓이지 않았습니다."


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
