"""xG 격자 표 읽기(43단계 A5) — 계산만, numpy 없음.

표는 `xg_grid.py`(생성물 — `tools/fit_xg.py --write`)에 있고, 여기는 칸을 찾아 값을 꺼내기만 한다.
칸 규칙(경기장 환산·칸 크기·끝 칸·종류 그룹)은 **표 안에 같이 실려 온 값**을 쓴다 — 판정한 코드(`fit_xg.C1`)와
이 파일이 따로 상수를 들고 있으면, 한쪽만 바뀌었을 때 검증한 것과 다른 값이 조용히 나온다.

되짚기 순서는 `fit_xg.C1.predict` 와 같다: (거리, 폭, 종류) 칸 → (거리, 폭) → 거리 → 전체 평균.
종류 코드가 그룹에 없거나 None 이면 '기타' 칸(그룹 수와 같은 번호)이다.
"""
from __future__ import annotations


def cell(table: dict, x: float, y: float) -> tuple[int, int]:
    """슛 좌표(0~1) → (거리 칸, 폭 칸). 끝 칸은 그보다 먼 것을 다 품는다."""
    length, width = table["pitch"]
    dist_m, wid_m, dist_max, wid_max = table["cell"]
    dx = max(0.01, (1.0 - x) * length)           # 골라인까지 거리(m)
    dy = abs(y - 0.5) * width                    # 중앙에서 벗어난 폭(m)
    return int(min(dx // dist_m, dist_max)), int(min(dy // wid_m, wid_max))


def type_slot(table: dict, type_code) -> int:
    groups = table["groups"]
    if isinstance(type_code, int) and not isinstance(type_code, bool) and type_code in groups:
        return groups.index(type_code)
    return len(groups)                           # 기타


def predict(table: dict, x: float, y: float, type_code) -> float:
    """슛 하나의 득점 확률(0~1). table 은 `xg_grid.SHOT`(전체 슛) 또는 `xg_grid.ON_TARGET`(유효슛만)."""
    a, b = cell(table, x, y)
    v = table["v3"].get((a, b, type_slot(table, type_code)))
    if v is None:
        v = table["v2"].get((a, b))
        if v is None:
            v = table["v1"].get(a, table["root"])
    eps = table["eps"]
    return min(max(v, eps), 1 - eps)
