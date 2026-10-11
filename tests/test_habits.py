"""habits.py — 내 경기 습관·기준선 계산(2.6.1). 지어낸 입력의 손으로 센 값으로 단언한다(네트워크 없음).

    python tests/test_habits.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import habits  # noqa: E402


def _t(day, hour=12):
    return datetime(2026, 9, day, hour)


# ── N2 등급 체류 ─────────────────────────────────────────────────────────────

def test_division_stays_counts_by_hand():
    # 9/1 챌린지1(1100) 2판 → 같은 날 슈퍼챌린지(1000) 승격 1판(9/1 은 1000 으로 끝) · 9/2 1000 → 1100 강등(9/2 는 1100 으로 끝)
    pts = [(_t(1, 9), 1100), (_t(1, 10), 1100), (_t(1, 11), 1000), (_t(2, 9), 1000), (_t(2, 10), 1100)]
    r = habits.division_stays(pts, name_of={1000: "슈퍼챌린지", 1100: "챌린지1"}.get)
    assert [x.division for x in r.rows] == [1000, 1100], "높은 등급(id 작은 것)부터"
    by = {x.division: x for x in r.rows}
    assert (by[1100].games, by[1100].days, by[1100].entries) == (3, 1, 1), by[1100]   # 시작 등급은 진입에 안 센다
    assert (by[1000].games, by[1000].days, by[1000].entries) == (2, 1, 1), by[1000]   # 날은 그날 마지막 경기 등급
    assert (r.promotions, r.demotions, r.gaps, r.start_division) == (1, 1, 0, 1100)
    assert by[1000].name == "슈퍼챌린지"


def test_division_stays_order_independent_and_empty():
    pts = [(_t(2, 10), 900), (_t(1, 10), 1000)]
    r = habits.division_stays(pts)
    assert (r.promotions, r.demotions) == (1, 0), "시각순으로 다시 놓지 않았다"
    assert r.rows[0].name == "900"
    e = habits.division_stays([])
    assert e.rows == [] and (e.promotions, e.demotions, e.gaps) == (0, 0, 0)


def test_division_gap_not_counted_as_move():
    # 막지 않았으면: 5일 빈 사이의 1100 → 1000 이 승격 1 · 진입 1 로 셌을 것
    n = config.N2_GAP_DAYS
    pts = [(_t(1), 1100), (_t(1 + n + 2), 1000)]
    r = habits.division_stays(pts)
    assert (r.promotions, r.demotions, r.gaps) == (0, 0, 1), r
    assert {x.division: x.entries for x in r.rows} == {1000: 0, 1100: 0}
    # 경계: 정확히 N2_GAP_DAYS 일이면 빈 구간이 아니다("넘으면")
    r = habits.division_stays([(_t(1), 1100), (_t(1 + n), 1000)])
    assert (r.promotions, r.gaps) == (1, 0), r
    # 빈 구간은 등급이 같아도 센다(화면 "기록 빈 구간 N곳")
    r = habits.division_stays([(_t(1), 1100), (_t(1 + n + 2), 1100)])
    assert r.gaps == 1 and r.promotions == r.demotions == 0


import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
