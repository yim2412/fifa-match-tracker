"""표 정렬 골든(73단계 F7) — `widgets.SortableItem` 의 정렬 순서가 바인딩을 바꿔도 같은지.

PySide6 에서 `super().__lt__(other)` 는 파이썬 쪽 `__lt__` 로 되돌아와 무한 재귀(프로세스째 죽는다)라,
73단계에서 Qt 기본 비교(`QTableWidgetItem::operator<`)를 파이썬으로 옮겼다. 그 비교가 옛 PyQt6 의 기본 비교와
**같은 순서**를 내는지는 이 골든이 잰다 — `fixtures/sort_golden.json` 은 **PyQt6 6.11 에서 옮기기 전 코드로 뜬 것**이다
(2026-11-01 · 그때 `--write` 길을 쓰고 지웠다). 다시 뜨지 않는다: 지금 코드로 뜨면 고친 비교를 고친 비교와 대조하는 빈 검사가 된다.
Qt 기본 비교는 시스템 로캘의 QCollator 라 로캘을 탄다 — 이 글자들은 ko_KR·en_US·ja_JP 에서 순서가 같다(같은 날 실측 · CI 는 en_US).
되돌리는 변이 3건(파이썬 `<` · C 로캘 · 흐림 방향 고정)이 FAIL 하는 것을 같은 날 확인.

칸 종류: 키 있는 숫자(같은 값 — 안정 정렬) · 키 없는 글자(숫자 글자·한글·빈 칸·BMP 끝 글자·이모지 — UTF-16 순서) ·
키 있는/없는 섞인 열(비교가 전순서가 아니다 — 알고리즘째 같아야 같은 답) · 흐림(sink) 줄 오름·내림.
정렬은 사람이 머리글을 누르는 길과 같게 `setSortIndicator` 로 건다(흐림은 그 방향을 읽는다).

`python tests/test_sort_golden.py`
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from widgets import SortableItem  # noqa: E402

GOLDEN = Path(__file__).parent / "fixtures" / "sort_golden.json"
ROW_ID = Qt.ItemDataRole.UserRole + 900   # 줄 번호 — 어느 역할과도 안 겹치게

_TEXTS = ["10", "9", "2.5%", "-1", "가", "나", "가나", "", "A", "a", "Z", "😀", "￠", "", "10", "1,000", " 9"]

# 이름 → 줄마다 (글자, 키, 흐림)
CASES: dict[str, list[tuple[str, object, bool]]] = {
    "keyed_ties": [(str(k), k, False) for k in (3, 1, 2, 1, 3.5, -2, 2, 0, 1)],
    "text_only": [(t, None, False) for t in _TEXTS],
    "mixed_keyed_none": [(t, (i * 7) % 5 if i % 3 else None, False) for i, t in enumerate(_TEXTS)],
    "sink_keyed": [(str(k), k, s) for k, s in ((5, False), (9, True), (1, False), (7, True), (3, False), (9, False), (2, True))],
    "sink_text": [(t, None, i % 4 == 1) for i, t in enumerate(_TEXTS)],
}


def _order(rows, desc: bool) -> list[int]:
    t = QTableWidget(len(rows), 1)
    for r, (text, key, sink) in enumerate(rows):
        it = SortableItem(text, key)
        it.sink = sink
        it.setData(ROW_ID, r)
        t.setItem(r, 0, it)
    t.setSortingEnabled(True)
    order = Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder
    t.horizontalHeader().setSortIndicator(0, order)
    out = [int(t.item(r, 0).data(ROW_ID)) for r in range(t.rowCount())]
    t.deleteLater()
    return out


def snapshot() -> dict:
    return {f"{name}/{'desc' if desc else 'asc'}": _order(rows, desc)
            for name, rows in CASES.items() for desc in (False, True)}


def test_sort_matches_pyqt6_golden():
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = snapshot()
    assert set(got) == set(want), f"칸 종류가 골든과 다르다: {sorted(set(got) ^ set(want))}"
    bad = [k for k in sorted(want) if got[k] != want[k]]
    assert not bad, "정렬 순서가 PyQt6 골든과 다르다: " + "; ".join(f"{k} 골든 {want[k]} 지금 {got[k]}" for k in bad)


def test_golden_is_not_trivial():
    """골든이 '아무 순서나 맞는' 빈 검사가 아닌지 — 정렬이 실제로 순서를 바꾸고, 오름·내림이 다르다."""
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, rows in CASES.items():
        asc, desc = want[f"{name}/asc"], want[f"{name}/desc"]
        assert asc != list(range(len(rows))), f"{name}: 오름차순이 넣은 순서 그대로 — 정렬이 안 돈 골든"
        assert asc != desc, f"{name}: 오름·내림이 같다"
    # 흐림 줄은 어느 방향이든 맨 아래(위젯 머리말의 약속)
    for d in ("asc", "desc"):
        order = want[f"sink_keyed/{d}"]
        sinks = [CASES["sink_keyed"][r][2] for r in order]
        assert sinks == sorted(sinks), f"sink_keyed/{d}: 흐림 줄이 맨 아래가 아니다 {sinks}"


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
