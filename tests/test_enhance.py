"""강화 비용(83단계) 테스트 — 규칙표 · 기대값 · 재료 조합 · 재료 받기 캐시. 네트워크 없음.

pytest 없이 `python tests/test_enhance.py`. 골든 수치는 넥슨 공지·도우미 응답에서 옮긴 값이다(2026-11-05).
"""
from __future__ import annotations

import gzip
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boostcollect
import config
import enhance as e
import playerinfo
import watchdog

FIX = Path(__file__).parent / "fixtures"
# 1강 OVR 115 카드 — 게이지 기준 OVR 은 5강에서 126, 7강에서 131
CARD = 115
ALL_MATS = {o: 1000 for o in range(100, 200)}   # 어떤 OVR 이든 값이 같다 → 언제나 100% 한 장
PRICES = {g: 1_000_000 * g for g in range(1, 14)}


def _mean_attempts(start, target, protect_from=None):
    return e.estimate(CARD, None, start, target, PRICES, ALL_MATS, protect_from, simulate=False).mean_attempts


def test_tables_are_the_notice_values():
    assert list(e.SUCCESS_PCT.values()) == [100, 81, 64, 50, 26, 15, 7, 5, 4, 3, 2, 1]
    for grade, row in e.RECOVERY_PCT.items():
        assert sum(row.values()) == 100, grade
        assert max(row) < grade or grade == 1, grade
    assert [e.CUM_OVR[g] for g in range(1, 14)] == [0, 1, 2, 4, 6, 8, 11, 15, 17, 19, 21, 24, 27]


def test_gauge_matches_the_helper_values():
    for card, grade, ovr, want in ((115, 5, 127, 100), (115, 5, 126, 89), (115, 5, 125, 66), (115, 5, 124, 48.9),
                                   (115, 5, 123, 36.3), (115, 7, 127, 27), (115, 7, 126, 20), (115, 7, 125, 14.8),
                                   (115, 7, 124, 11), (108, 7, 125, 100), (124, 5, 126, 6.0), (124, 5, 123, 2.5)):
        assert e.gauge_of(card, grade, ovr) == want, (card, grade, ovr)
    assert e.gauge_of(115, 5, 90) == 0, "표 아래는 0"
    # 1~3강 도전은 넥슨 응답이 표보다 높다(넥슨 값 90.3 · 49.9 · 60.2 · 33.3 · 81.9 · 45.2) — 1%p 안쪽
    for card, grade, ovr, nexon in ((115, 1, 117, 90.3), (115, 1, 115, 49.9), (115, 2, 118, 60.2), (115, 2, 116, 33.3),
                                    (115, 3, 121, 81.9), (115, 3, 119, 45.2), (115, 4, 123, 65.7), (115, 6, 127, 66.1),
                                    (110, 10, 133, 66.6), (110, 12, 138, 67.1)):
        assert abs(e.gauge_of(card, grade, ovr) - nexon) <= 1.1, (card, grade, ovr, e.gauge_of(card, grade, ovr))
    assert e.gauge_of(115, 1, 119) == 100, "넘치면 100 에서 자른다"


def test_expected_attempts_golden():
    # 공식 표로 직접 푼 값. 단순 역수 합(27회)이면 빨개진다
    assert round(_mean_attempts(4, 8)) == 4330
    assert round(_mean_attempts(1, 8)) == 4335
    assert round(_mean_attempts(7, 8)) == 4028
    assert round(_mean_attempts(1, 5)) == 12


def test_protection_keeps_the_grade_and_charges_on_success():
    plain = e.estimate(CARD, None, 8, 10, PRICES, ALL_MATS, None, simulate=False)
    guard = e.estimate(CARD, None, 8, 10, PRICES, ALL_MATS, 2, 40)
    assert plain.mean_attempts > 100_000, "보호가 없으면 내려간다 — 아래 단언의 전제"
    assert round(guard.mean_attempts) == 45     # 20 + 25
    # 시도마다(성공 포함) 그 단계 시세의 40% + 재료
    want = 20 * (1000 + PRICES[8] * 0.4) + 25 * (1000 + PRICES[9] * 0.4)
    assert abs(guard.mean_cost - want) < 1, (guard.mean_cost, want)
    assert abs(guard.mean_protect - want + 45 * 1000) < 1
    assert [s.protected for s in guard.steps] == [True, True]


def test_protect_covers_every_grade_that_can_fall():
    est = e.estimate(CARD, None, 1, 10, PRICES, ALL_MATS, 2, 40)
    assert [s.grade for s in est.steps if s.protected] == list(range(2, 10))
    first = est.steps[0]
    assert first.grade == 1 and first.protect_cost == 0, "1강 도전은 실패해도 1강이다 — 보호비만 나간다"
    # 1→8 전부 보호: 내려갈 일이 없어 1/p 합(약 30.6회). 보호 없으면 4,335회
    guard = e.estimate(CARD, None, 1, 8, PRICES, ALL_MATS, 2, 40)
    want = sum(100 / e.SUCCESS_PCT[g] for g in range(1, 8))
    assert abs(guard.mean_attempts - want) < 1e-6 and 30 < want < 31, guard.mean_attempts
    assert _mean_attempts(1, 8) > 4000


def test_protect_from_lets_the_low_grades_fall():
    # 5강 도전부터 보호: 1→5 는 떨어지는 그대로 + 5·6·7강은 1/p 씩
    est = e.estimate(CARD, None, 1, 8, PRICES, ALL_MATS, 5, 40)
    assert [s.grade for s in est.steps if s.protected] == [5, 6, 7]
    assert abs(est.mean_attempts - (_mean_attempts(1, 5) + 100 / 26 + 100 / 15 + 100 / 7)) < 1e-6, est.mean_attempts
    # 보호 시작보다 위에서 끝나면 보호비가 안 나온다
    low = e.estimate(CARD, None, 1, 5, PRICES, ALL_MATS, 5, 40)
    assert low.mean_protect == 0 and abs(low.mean_attempts - _mean_attempts(1, 5)) < 1e-6
    assert not any(s.protected for s in low.steps)
    # 1 을 줘도 1강은 안 센다
    assert not e.protected_at(1, 1, None) and e.protected_at(2, 1, None)


def test_step_means_add_up_to_the_total():
    for args in ((1, 8, 2), (1, 8, None), (5, 8, 7), (3, 10, 8)):
        est = e.estimate(CARD, None, args[0], args[1], PRICES, ALL_MATS, args[2], simulate=False)
        assert abs(sum(s.mean_attempts for s in est.steps) - est.mean_attempts) < 1e-6 * est.mean_attempts, args
        assert abs(sum(s.mean_cost for s in est.steps) - est.mean_cost) < 1e-6 * est.mean_cost, args
    # 보호 없이 7→8: 떨어지면 아래를 다시 지나므로 7강 도전(1/7%)보다 아래가 훨씬 많다 — 그 줄이 표에 있어야 비중이 100% 가 된다
    est = e.estimate(CARD, None, 7, 8, PRICES, ALL_MATS, None, simulate=False)
    by = {s.grade: s.mean_attempts for s in est.steps}
    assert list(by) == list(range(1, 8)) and abs(by[7] - 100 / 7) < 1e-6 and by[2] > by[7], by
    # 보호면 단계마다 정확히 1/p
    guard = {s.grade: s.mean_attempts for s in e.estimate(CARD, None, 5, 8, PRICES, ALL_MATS, 2, simulate=False).steps}
    assert all(abs(guard[g] - 100 / e.SUCCESS_PCT[g]) < 1e-9 for g in (5, 6, 7)), guard


def test_rate_range_only_for_grades_without_official_rates():
    est = e.estimate(CARD, None, 6, 9, PRICES, ALL_MATS, 2)
    assert est.protect_pct == 40
    v = {s.grade: s.mean_attempts for s in est.steps}
    fixed = v[8] * round(PRICES[8] * 0.4)            # 8강은 공식 비율 그대로
    for got, rate in zip(est.rate_range, e.PROTECT_RANGE_PCTS):
        want = est.mean_material + fixed + sum(v[g] * round(PRICES[g] * rate / 100) for g in (6, 7))
        assert abs(got - want) < 1e-6, (got, want, rate)
    assert est.rate_range[0] < est.mean_cost < est.rate_range[1]
    assert e.estimate(CARD, None, 8, 10, PRICES, ALL_MATS, 2).rate_range is None, "전부 공식 비율표 구간"
    assert e.estimate(CARD, None, 6, 9, PRICES, ALL_MATS, 2, 30).rate_range is None, "비율을 직접 골랐다"
    assert e.estimate(CARD, None, 1, 5, PRICES, ALL_MATS, None).rate_range is None, "보호 없음"
    assert e.estimate(CARD, None, 6, 7, PRICES, ALL_MATS, 7).rate_range is None, "보호가 안 걸린 구간"


def test_icon_tm_only_first_extended_step():
    assert e.protected_at(5, 2, 100) and not e.protected_at(6, 2, 100) and not e.protected_at(7, 2, 100)
    assert e.protected_at(8, 2, 100), "8강부터는 기존 범위"
    assert e.protected_at(6, 2, None), "ICON TM 이 아니면 6강도 보호"
    assert not e.protected_at(6, None, None), "끄면 안 걸린다"
    est = e.estimate(CARD, 100, 5, 7, PRICES, ALL_MATS, 2, 40)
    assert [s.grade for s in est.steps if s.protected] == [2, 3, 4, 5], "6강에서 떨어져 올라오는 길도 보호"
    assert est.mean_attempts > 100 / 26 + 100 / 15 + 1, "6강 실패는 내려간다"


def test_protect_rate_tiers():
    for ovr, pct in ((92, 95), (93, 80), (100, 80), (101, 60), (104, 60), (105, 40)):
        assert e.protect_pct(ovr, None) == pct, ovr
    assert e.protect_pct(110, 324) == 95, "24 LIVE"
    assert e.protect_pct(110, 317) == 95, "17 LIVE"
    assert e.protect_pct(110, 300) == 95, "25 LIVE"
    assert e.protect_pct(110, None) == 40
    assert e.protect_pct(110, 515) == 40, "24 PL 은 LIVE 가 아니다"


def test_excluded_class_is_calculated_without_protection():
    est = e.estimate(CARD, 596, 8, 9, PRICES, ALL_MATS, 2, simulate=False)     # SH — 공지 대상 목록에 없다
    assert not est.protect and est.protect_excluded and est.mean_protect == 0
    assert est.mean_attempts > 1000
    off = e.estimate(CARD, 596, 8, 9, PRICES, ALL_MATS, None, simulate=False)
    assert not off.protect_excluded, "끈 사람에게 제외 안내를 하지 않는다"
    assert e.protect_applies(2, 827), "BDO 는 5/28 목록에 있다 — 옛 공지의 제외 표로 되돌아가면 빨개진다"


def test_grades_needed_skips_below_only_when_protected():
    assert e.grades_needed(8, 10, 2) == [8, 9]
    assert e.grades_needed(8, 10, None) == list(range(1, 10))
    assert e.grades_needed(5, 10, 2) == list(range(5, 10))
    assert e.grades_needed(5, 10, 5) == list(range(5, 10)), "시작이 보호 시작이면 안 내려간다"
    assert e.grades_needed(4, 10, 5) == list(range(1, 10)), "4강은 보호 전이라 떨어질 수 있다"
    assert e.grades_needed(1, 2, None) == [1], "1강은 보호 없이도 안 떨어진다"
    assert e.grades_needed(5, 6, 2, 100) == [5], "ICON TM 5→6"
    assert e.grades_needed(5, 7, 2, 100) == list(range(1, 7)), "ICON TM 6강은 떨어진다"
    assert e.grades_needed(8, 10, 2, 596) == list(range(1, 10)), "제외 클래스는 떨어진다"


def test_scan_levels_cover_below_and_above():
    assert e.scan_levels(115, [5]) == list(range(121, 130))
    assert e.scan_levels(115, [5, 7]) == list(range(121, 135))
    # 1강은 필요량이 40% 라 훨씬 아래 OVR 도 5장으로 채운다(8.2 ÷ 0.4 = 20.5%)
    assert e.scan_levels(115, [1]) == list(range(112, 124))
    # 5장으로 못 채우는 칸은 묻지 않는다 — 요청 수가 그대로 는다
    assert e.gauge_of(115, 1, 111) < 20 and e.gauge_of(115, 5, 120) < 20


def test_best_materials_compares_every_combination():
    # 5강 기준 X=126: 123 은 36.3% 라 세 장이면 108.9%
    m = e.best_materials(115, 5, {123: 100, 127: 10_000})
    assert (m.cost, [(c.ovr, c.count) for c in m.cards]) == (300, [(123, 3)])
    # 높은 OVR 이 더 쌀 때(사용자 2026-11-05)
    m = e.best_materials(115, 5, {123: 100, 127: 250})
    assert (m.cost, [(c.ovr, c.count) for c in m.cards]) == (250, [(127, 1)])
    # 위로 올라갈수록 싼 역전 — 100% 칸끼리는 가장 싼 것
    m = e.best_materials(115, 5, {127: 500, 128: 300, 129: 900})
    assert (m.cost, [(c.ovr, c.count) for c in m.cards]) == (300, [(128, 1)])
    # 섞는 것이 가장 쌀 때: 89% + 11%
    m = e.best_materials(115, 5, {126: 100, 119: 10, 123: 400})
    assert (m.cost, sorted((c.ovr, c.count) for c in m.cards)) == (110, [(119, 1), (126, 1)])
    assert m.gauge >= 100


def test_best_materials_unfillable_lowers_success():
    m = e.best_materials(124, 5, {127: 100})      # 8.2% × 5 — 다섯 장이 한도
    assert m.cards[0].count == 5 and abs(m.gauge - 41) < 0.01 and m.cost == 500
    est = e.estimate(124, None, 1, 2, PRICES, {127: 100})
    assert est.steps[0].materials.gauge >= 100 and est.steps[0].success_pct == 100, "1강이면 채운다"
    est = e.estimate(124, None, 5, 6, PRICES, {127: 100})
    step = next(s for s in est.steps if s.grade == 5)
    assert abs(step.success_pct - 26 * 0.41) < 0.01, "게이지만큼 확률이 준다"
    assert e.best_materials(115, 5, {}) is None


def test_price_by_ovr_uses_low_average():
    rows = [(i, 1, 120, 100 * (i + 1)) for i in range(15)]    # 100~1500
    rows += [(0, 1, 120, 100)]                                # 같은 카드·강화는 한 번
    rows += [(99, 1, 120, 0)]                                 # 값 없음(이 줄이 세어지면 평균이 낮아진다)
    rows += [(200, 1, 121, 5), (201, 1, 121, 7)]              # 두 장뿐 — 믿지 않는다
    got = e.price_by_ovr(rows)
    assert got == {120: 550}, got      # 100~1000 평균


def test_estimate_errors():
    for args in ((5, 5), (6, 5), (1, 14)):
        try:
            e.estimate(CARD, None, args[0], args[1], PRICES, ALL_MATS)
            raise AssertionError(args)
        except e.EnhanceError:
            pass
    try:
        e.estimate(CARD, None, 8, 9, {}, ALL_MATS, 2)
        raise AssertionError("시세 없이 보호비를 냈다")
    except e.EnhanceError as err:
        assert "8강" in str(err)
    try:
        e.estimate(CARD, None, 1, 8, PRICES, {})
        raise AssertionError("재료 없이 계산했다")
    except e.EnhanceError as err:
        assert "재료" in str(err)


def test_budget_follows_the_real_distribution():
    a = e.estimate(CARD, None, 1, 5, PRICES, ALL_MATS, None)
    b = e.estimate(CARD, None, 1, 5, PRICES, ALL_MATS, None)
    assert a.costs == b.costs and len(a.costs) == e.SIM_RUNS, "같은 입력이면 같은 답"
    assert abs(sum(a.costs) / len(a.costs) - a.mean_cost) < 0.02 * a.mean_cost, "흉내와 식이 같은 답을 내야 한다"
    # 8→9 보호: 시도 수는 기하분포 p=5% — 1-(0.95^n) 이 50%·80%·90% 를 넘는 n 은 14·32·45
    g = e.estimate(CARD, None, 8, 9, PRICES, {o: 1 for o in range(100, 200)}, 2, 40)
    one = g.steps[0].attempt_cost
    for q, n in ((50, 14), (80, 32), (90, 45)):
        got = g.budget_at(q) / one
        assert abs(got - n) <= 2, (q, got, n)
    assert abs(g.mean_attempts - 20) < 1e-6
    # 평균은 약 64% 자리 — 1-(0.95^20). 평균 대신 85% 를 맨 위에 두는 이유이기도 하다
    assert 0.60 < g.chance_within(round(g.mean_cost)) < 0.68, g.chance_within(round(g.mean_cost))


def test_budget_levels_are_the_agreed_ones():
    assert e.BUDGET_EXPECT_Q == 85, "맨 위 기준 고정(사용자 2026-11-06)"
    assert e.BUDGET_QS == (50, 85, 95), "보통 운 · 준비 기준 · 나쁜 운까지"


def test_budget_helpers_on_a_known_list():
    est = replace(e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2, simulate=False), costs=tuple(range(1, 2001)))
    assert est.budget_at(80) == 1600, "1,600 이하가 정확히 80%"
    assert est.chance_within(est.budget_at(80)) == 0.8
    assert est.budget_at(99) == 1980
    assert est.chance_within(0) == 0 and est.chance_within(10 ** 9) == 1
    assert est.tail_mean(90) == sum(range(1801, 2001)) / 200, "90% 금액을 넘긴 판들의 평균"
    assert est.within_mean(90) == sum(range(1, 1801)) / 1800, "90% 금액 안에 끝난 판들의 평균"
    assert [q for q, _b in est.budgets] == list(e.BUDGET_QS)
    curve = est.curve
    assert curve[0][0] == 1 and curve[-1] == (1900, 95.0), "꼬리는 표의 마지막 확률에서 자른다"
    assert len(curve) <= e.CURVE_POINTS + 1
    assert all(a[0] <= b[0] and a[1] < b[1] for a, b in zip(curve, curve[1:])), "금액·확률 모두 오름차순"


def test_budget_hides_percentiles_with_too_few_runs_above():
    # 위쪽에 SIM_TAIL_MIN 판이 안 남는 확률은 금액을 적지 않는다 — 몇 판이 정하는 숫자라 믿지 않는다
    base = e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2, simulate=False)
    few = replace(base, costs=tuple(range(1, 501)))
    assert few.budget_at(95) == 475 and few.budget_at(99) is None and few.tail_mean(99) is None
    assert [q for q, _b in few.budgets] == [50, 85, 95]
    assert few.curve[-1] == (475, 95.0)
    fewer = replace(base, costs=tuple(range(1, 101)))
    assert [q for q, _b in fewer.budgets] == [50] and fewer.budget_at(85) is None and fewer.within_mean(85) is None
    assert fewer.curve[-1] == (50, 50.0)
    empty = base
    assert empty.costs == () and empty.budget_at(50) is None and empty.chance_within(1) is None
    assert empty.budgets == () and empty.curve == () and empty.tail_mean(50) is None and empty.within_mean(50) is None


def test_cheapest_protect_is_the_lowest_of_every_choice():
    # 카드가 재료만큼 싸면 낮은 칸부터 보호(3강부터가 최저 — 손으로 푼 표: None 433만 · 2강 4.24만 · 3강 4.22만 · 4강 4.27만)
    cheap_cards = {g: 1000 for g in range(1, 14)}
    assert e.cheapest_protect_from(CARD, None, 1, 8, cheap_cards, ALL_MATS)[0] == 3
    # 카드가 재료보다 훨씬 비싸면 안 하는 쪽
    pricey = {g: 10 ** 12 for g in range(1, 14)}
    assert e.cheapest_protect_from(CARD, None, 1, 8, pricey, {o: 1 for o in ALL_MATS})[0] is None
    # 낮은 강화만 비싸면 그 위부터 — 양 끝만 재면 못 고르는 답
    dear_low = {g: 10 ** 8 if g < 8 else 1000 for g in range(1, 14)}
    pf, est = e.cheapest_protect_from(CARD, None, 1, 11, dear_low, ALL_MATS)
    every = {c: e.estimate(CARD, None, 1, 11, dear_low, ALL_MATS, c, simulate=False).mean_cost for c in [None, *range(2, 11)]}
    assert pf == 8 and est.mean_cost == min(every.values()), (pf, every)
    assert est.costs == (), "평균만 — 후보마다 흉내 내면 느리다"


def test_cheapest_protect_looks_below_the_start_for_icon_tm():
    # ICON TM 은 6강에서 떨어져 5강 아래를 다시 지난다 — 시작(5강)보다 낮은 보호 칸이 실제로 값을 바꾼다
    cheap_cards = {g: 1000 for g in range(1, 14)}
    wide = e.estimate(CARD, 100, 5, 8, cheap_cards, ALL_MATS, 2, simulate=False)
    narrow = e.estimate(CARD, 100, 5, 8, cheap_cards, ALL_MATS, 5, simulate=False)
    assert wide.mean_cost < narrow.mean_cost, "아래 단언의 전제 — 시작 칸부터만 재면 놓치는 답"
    assert e.cheapest_protect_from(CARD, 100, 5, 8, cheap_cards, ALL_MATS)[0] == 3


def test_cheapest_protect_on_a_tie_names_the_later_start():
    cheap_cards = {g: 1000 for g in range(1, 14)}
    # 5강에서 시작하면 2~5강부터는 같은 값 — "5강부터" 로 적는다
    assert e.cheapest_protect_from(CARD, None, 5, 8, cheap_cards, ALL_MATS)[0] == 5
    # 1강 도전뿐이면 어느 칸도 안 걸린다 — 보호 안 함
    assert e.cheapest_protect_from(CARD, None, 1, 2, cheap_cards, ALL_MATS)[0] is None


def test_cheapest_protect_skips_what_it_cannot_price():
    assert e.cheapest_protect_from(CARD, None, 8, 10, {}, ALL_MATS)[0] is None, "시세가 없으면 보호 칸은 못 낸다"
    # 7강 시세만 없으면 7강까지 보호하는 칸들만 빠진다 — 8강부터 보호로는 7강 시세가 필요 없다
    no_seven = {g: 1000 for g in range(1, 14) if g != 7}
    pf, _est = e.cheapest_protect_from(CARD, None, 1, 10, no_seven, ALL_MATS)
    assert pf == 8, pf
    assert e.cheapest_protect_from(CARD, None, 1, 8, PRICES, {}) is None, "재료가 없으면 어느 칸도 못 낸다"


def test_cheapest_protect_finds_each_combination_once():
    calls = []
    keep = e.best_materials
    e.best_materials = lambda *a: (calls.append(a[1]), keep(*a))[1]
    try:
        e.cheapest_protect_from(CARD, None, 1, 8, PRICES, ALL_MATS)
    finally:
        e.best_materials = keep
    assert sorted(calls) == list(range(1, 8)), calls


def test_estimate_materials_store_is_not_shared_by_default():
    # 임시 저장이 호출 사이에 남으면 다른 재료 시세에 앞 답이 나온다
    a = e.estimate(CARD, None, 8, 9, PRICES, {o: 1000 for o in ALL_MATS}, None, simulate=False)
    b = e.estimate(CARD, None, 8, 9, PRICES, {o: 2000 for o in ALL_MATS}, None, simulate=False)
    assert b.mean_material == 2 * a.mean_material


def test_missing_material_error_names_the_lowest_usable_ovr():
    for card, grade in ((115, 5), (127, 12), (100, 1), (120, 3)):
        low = e.lowest_material_ovr(card, grade)
        assert e.gauge_of(card, grade, low) > 0 and e.gauge_of(card, grade, low - 1) == 0, (card, grade, low)
    try:
        e.estimate(CARD, None, 5, 6, PRICES, {}, 5)
        raise AssertionError("재료 없이 계산했다")
    except e.EnhanceError as err:
        assert f"OVR {e.lowest_material_ovr(CARD, 5)} 이상" in str(err) and "5강" in str(err), err


def test_budget_empty_when_too_many_attempts():
    est = e.estimate(CARD, None, 8, 10, PRICES, ALL_MATS, None)
    assert est.costs == () and est.budgets == ()
    assert est.mean_cost > 0, "평균은 낸다"
    # 경계: 평균이 많아도 SIM_MIN_RUNS 판은 돌릴 수 있으면 돈다
    ok = e.estimate(CARD, None, 1, 8, PRICES, ALL_MATS, None)
    assert e.SIM_MIN_RUNS <= len(ok.costs) < e.SIM_RUNS, len(ok.costs)
    assert len(ok.costs) == int(e.SIM_STEP_BUDGET / ok.mean_attempts)
    # 정확히 SIM_MIN_RUNS 판이면 돌고 한 판 모자라면 안 돈다(8→9 보호 평균 20회)
    keep = e.SIM_STEP_BUDGET
    try:
        e.SIM_STEP_BUDGET = 20 * e.SIM_MIN_RUNS + 1
        assert len(e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2).costs) == e.SIM_MIN_RUNS
        e.SIM_STEP_BUDGET = 20 * e.SIM_MIN_RUNS - 1
        assert e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2).costs == ()
    finally:
        e.SIM_STEP_BUDGET = keep


def test_simulate_off_gives_means_only():
    a = e.estimate(CARD, None, 1, 8, PRICES, ALL_MATS, 2)
    b = e.estimate(CARD, None, 1, 8, PRICES, ALL_MATS, 2, simulate=False)
    assert a.costs and b.costs == () and a.mean_cost == b.mean_cost


def test_parse_money():
    for text, want in (("3000만", 30_000_000), ("3,000만", 30_000_000), ("2천만", 20_000_000), ("5백만", 5_000_000),
                       ("1.5억", 150_000_000), ("1억 2,000만", 120_000_000), ("1억2천만", 120_000_000),
                       ("12345678", 12_345_678), ("1조 5억", 1_000_500_000_000), (" 7억 BP ", 700_000_000)):
        assert e.parse_money(text) == want, text
    for text in ("", "   ", "없음", "0", "만", "3만원", None):
        assert e.parse_money(text) is None, text


def test_buying_comparison_inputs():
    est = e.estimate(CARD, None, 1, 5, PRICES, ALL_MATS, None)
    assert est.start_price == PRICES[1] and est.target_price == PRICES[5]
    assert est.path_total == PRICES[1] + est.mean_cost


def test_parse_real_rows():
    html = gzip.decompress((FIX / "boost_list.html.gz").read_bytes()).decode("utf-8")
    assert playerinfo.parse_boost_rows(html) == [(100190042, 1, 127, 43_800_000), (100190043, 1, 127, 507_000_000),
                                                 (100001397, 1, 126, 198_000_000)]
    assert playerinfo._BOOST_MARK in html, "형식 표식이 실응답에 없으면 늘 형식 오류가 난다"


def test_parse_keeps_good_rows_when_one_breaks():
    good = '<li class="en_char_list_item">Player_info_5 <a class="ability en_level8">8</a> CF &nbsp;130 45,200 BP </li>'
    bad = '<li class="en_char_list_item">Player_info_6 깨진 줄</li>'
    assert playerinfo.parse_boost_rows(bad + good) == [(5, 8, 130, 45200)]


def test_parse_uses_main_position_ovr():
    # 넥슨 표시 예: RW 70 · ST 67 · CAM 71 — 게이지는 첫 포지션을 따른다(가장 높은 값으로 읽으면 한 칸씩 어긋났다)
    row = ('<li class="en_char_list_item">Player_info_7 <a class="ability en_level2">2</a> '
           'RW &nbsp;70 ST &nbsp;67 CAM &nbsp;71 1,000 BP </li>')
    assert playerinfo.parse_boost_rows(row) == [(7, 2, 70, 1000)]


class _Cache:
    def __enter__(self):
        self._keep, self._tmp = config.CACHE_DIR, tempfile.TemporaryDirectory()
        config.CACHE_DIR = Path(self._tmp.name)
        return self

    def __exit__(self, *a):
        config.CACHE_DIR = self._keep
        self._tmp.cleanup()


def _rows(sp, ovr):
    return [(ovr * 10 + i, 1, ovr, 100 + i) for i in range(3)]


def test_material_cache_is_shared_between_cards_for_a_day():
    with _Cache():
        calls = []

        def fetch(sp, ovr):
            calls.append((sp, ovr))
            return _rows(sp, ovr)

        got = boostcollect.material_prices(1, [120, 121], "2026-11-05", fetch=fetch, pause=lambda _s: None)
        assert got == {120: 101, 121: 101} and len(calls) == 2
        boostcollect.material_prices(2, [120, 121, 122], "2026-11-05", fetch=fetch, pause=lambda _s: None)
        assert calls[2:] == [(2, 122)], "받은 OVR 은 다른 카드도 다시 안 묻는다"
        boostcollect.material_prices(2, [120], "2026-11-06", fetch=fetch, pause=lambda _s: None)
        assert calls[3:] == [(2, 120)], "다음 날은 다시 묻는다"


def test_material_requests_are_spaced():
    with _Cache():
        gaps = []
        boostcollect.material_prices(1, [120, 121, 122], "d", fetch=_rows, pause=gaps.append)
        assert gaps == [config.ENHANCE_REQUEST_GAP_S] * 2, gaps


def test_material_failure_keeps_what_was_fetched():
    with _Cache():
        def fetch(sp, ovr):
            if ovr == 121:
                raise playerinfo.PlayerInfoError("막힘", "rate")
            return _rows(sp, ovr)
        try:
            boostcollect.material_prices(1, [120, 121], "d", fetch=fetch, pause=lambda _s: None)
            raise AssertionError("막힘을 삼켰다")
        except playerinfo.PlayerInfoError:
            pass
        calls = []

        def again(sp, ovr):
            calls.append(ovr)
            return _rows(sp, ovr)
        boostcollect.material_prices(1, [120, 121], "d", fetch=again, pause=lambda _s: None)
        assert calls == [121], "받은 것은 남는다"


def test_material_cancel_stops_and_returns_nothing():
    with _Cache():
        calls = []

        def fetch(sp, ovr):
            calls.append(ovr)
            return _rows(sp, ovr)
        got = boostcollect.material_prices(1, [120, 121], "d", cancel=lambda: bool(calls), fetch=fetch, pause=lambda _s: None)
        assert got == {} and calls == [120]


def test_material_cache_corrupt_file_is_ignored():
    with _Cache():
        Path(config.CACHE_DIR, boostcollect.CACHE_NAME).write_text("{깨짐", encoding="utf-8")
        assert boostcollect.material_prices(1, [120], "d", fetch=_rows, pause=lambda _s: None) == {120: 101}


# ── 87 V3 — N번 시도하면 붙을 확률 ──

def _attempts_sim(est, runs=20000, seed=7):
    """식과 따로 — 시도 수를 직접 흉내 낸다(비용이 아니라 횟수)."""
    import random
    rng = random.Random(seed)
    steps = {s.grade: s for s in est.steps}
    out = []
    for _ in range(runs):
        g, n = est.start, 0
        while g < est.target:
            n += 1
            st = steps[g]
            if rng.random() * 100 < st.success_pct:
                g += 1
            elif not st.protected:
                r, acc = rng.random() * 100, 0
                for j, pct in sorted(e.RECOVERY_PCT[g].items()):
                    acc += pct
                    if r < acc:
                        g = j
                        break
        out.append(n)
    return out


def test_tries_protected_one_step_is_the_geometric_formula():
    est = e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2)
    assert est.tries[0] == (1, 0.05)
    for n, p in est.tries:
        assert abs(p - (1 - 0.95 ** n)) < 1e-12, (n, p)
    assert [n for n, _ in est.tries] == [1, 2, 3, 5, 10, 20, 30, 50, 100], "1·2·3·5 칸 · 99% 를 처음 넘는 100 에서 멈춘다"


def test_tries_without_protection_follow_the_falls():
    est = e.estimate(CARD, None, 5, 6, PRICES, ALL_MATS, None)
    # 실패하면 1~3강으로 떨어져 4번째 시도 전엔 다시 못 닿는다 — 1-(1-p)^n 이면 2·3번에서 값이 오른다
    assert est.tries[:3] == ((1, 0.26), (2, 0.26), (3, 0.26)), est.tries[:3]
    assert abs(est.tries[3][1] - (1 - 0.74 ** 5)) > 0.2, "기하분포와 달라야 한다"
    sim = _attempts_sim(est)
    for n, p in est.tries:
        got = sum(1 for k in sim if k <= n) / len(sim)
        assert abs(got - p) < 0.012, (n, p, got)
    ps = [p for _, p in est.tries]
    assert ps == sorted(ps) and ps[-1] >= e.TRY_P_MAX


def test_tries_from_a_lower_start_need_enough_attempts():
    est = e.estimate(CARD, None, 1, 5, PRICES, ALL_MATS, None)
    steps = {s.grade: s for s in est.steps}
    p1, p3, p4, p5 = e.reach_within(steps, 1, 5, [1, 3, 4, 5])
    assert p1 == 0 and p3 == 0, "네 단계를 셋 이하로는 못 오른다"
    assert abs(p4 - _exact_1_to_5_in_5()) < 1e-12 and abs(p5 - p4) < 1e-12, "한 번 실패하면 여섯 번째에야 닿는다"
    assert dict(est.tries)[1] == 0, "1번 칸은 0% 여도 남긴다"


def _exact_1_to_5_in_5():
    """1→5 를 다섯 번 안에: 실패 없이 네 번(1·.81·.64·.5) + 한 번 실패하고 다섯 번. 1강은 100% 라 실패는 2~4강에서."""
    p = {1: 1.0, 2: 0.81, 3: 0.64, 4: 0.5}
    clean = p[1] * p[2] * p[3] * p[4]
    # 2강 실패(→1) 뒤 1→2→3→4→5 는 4번 더 — 총 6번이라 안 든다. 3강 실패 → 1(65)·2(35): 2강이면 3번 더 = 총 6번. 4강 실패도 넘는다.
    return clean


def test_tries_stop_at_the_cap_and_still_show_it():
    est = e.estimate(CARD, None, 1, 13, PRICES, ALL_MATS, None)
    last_n, last_p = est.tries[-1]
    assert last_n == e.TRY_MAX and last_p < e.TRY_P_MAX, est.tries
    # 평균이 TRY_MAX 보다 훨씬 크면 거의 기하분포 — 확률 ≈ 횟수 ÷ 평균
    assert abs(last_p / (e.TRY_MAX / est.mean_attempts) - 1) < 0.1, (last_p, est.mean_attempts)


def test_tries_rows_are_thinned_keeping_both_ends():
    full = e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2).tries
    keep = e.TRY_ROWS_MAX
    try:
        e.TRY_ROWS_MAX = 4
        thin = e.estimate(CARD, None, 8, 9, PRICES, ALL_MATS, 2).tries
    finally:
        e.TRY_ROWS_MAX = keep
    assert len(full) > 4 and len(thin) == 4
    assert thin[0] == full[0] and thin[-1] == full[-1] and set(thin) <= set(full)


def test_tries_skip_low_rows_but_keep_the_first():
    est = e.estimate(CARD, None, 8, 10, PRICES, ALL_MATS, None)
    assert est.tries[0][0] == 1
    assert all(p >= e.TRY_P_MIN for n, p in est.tries[1:]), est.tries
    assert est.costs == () and est.tries, "금액 흉내를 못 돌리는 구간에도 시도 표는 나온다"


def test_reach_within_matches_one_attempt_at_a_time():
    """제곱 행렬 길이의 경계 — n 이 2의 거듭제곱일 때 마지막 제곱이 있어야 한다(표 칸 최댓값 10억은 2의 거듭제곱이 아니라 안 지난다)."""
    est = e.estimate(CARD, None, 5, 7, PRICES, ALL_MATS, None)
    steps = {s.grade: s for s in est.steps}
    states, t = e._transition(steps, 7)
    v = [1.0 if s == 5 else 0.0 for s in states]
    want = {}
    for n in range(1, 17):
        v = [sum(v[i] * t[i][j] for i in range(len(v))) for j in range(len(v))]
        want[n] = v[-1]
    for ns in ([1], [2], [4], [8], [16], [3, 5, 16]):
        got = e.reach_within(steps, 5, 7, ns)
        assert all(abs(g - want[n]) < 1e-12 for g, n in zip(got, ns)), (ns, got)


def test_tries_only_when_simulating():
    assert e.estimate(CARD, None, 1, 8, PRICES, ALL_MATS, 2, simulate=False).tries == ()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as err:
            failed += 1
            print(f"[FAIL] {t.__name__}: {err}")
        except Exception as err:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(err).__name__}: {err}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
