"""카드 정보 계산(51단계) — 계산만(화면은 `core_api`). 지금은 측정 도구(`tools/measure_cards.py`)와 앱이 **같은 식**을 쓰게
실효 OVR·전력 차만 둔다 — 측정 A 가 맞춘 `config.C1_LOGIT_*` 가 앱의 Δ 와 어긋나지 않게(도구에 따로 두면 조각 ② 에서 갈라진다).
C1·N28·C2·C3·N21·C5·C6 집계는 조각 ②③ 에서 여기에 붙인다(ROADMAP "## 51단계 › 새 코드").

| 무엇 | 규칙 |
|---|---|
| 실효 OVR | 시뮬레이터 1강 그 자리 OVR(`config.SIM_POS_OF` 로 28칸 → 16칸) + `config.GRADE_OVR_BONUS[강화]` — 팀컬러·적응도는 뺀다(U3) |
| 선발 | `spPosition` ≠ 28(교체) — 11장 |
| 모름 | 자리 칸 없음(`pos`) · 백필 없음(`none`) · 강화 모름(`grade`) — 빈 칸을 채워 넣지 않는다(채우면 메타 카드 쪽으로 치우친다) |
"""
from __future__ import annotations

import json

import config

SUB_POSITION = 28


def pos_ovrs(extra: dict | None) -> dict[str, int] | None:
    """card_extra 줄 → 시뮬레이터 16칸 {칸: OVR}. 시뮬레이터를 아직 안 읽었거나 깨졌으면 None."""
    raw = (extra or {}).get("pos_ovr")
    if not raw:
        return None
    try:
        got = json.loads(raw)
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def effective_ovr(po: int, grade, extra: dict | None) -> tuple[int | None, str]:
    """→ (실효 OVR, 모를 때 이유). 이유는 "" · "pos"(그 자리를 시뮬레이터 칸으로 못 잇는다) · "none"(백필 없음) · "grade"."""
    cell = config.SIM_POS_OF.get(po)
    if cell is None:
        return None, "pos"
    ovrs = pos_ovrs(extra)
    if ovrs is None or not isinstance(ovrs.get(cell), int):
        return None, "none"
    bonus = config.GRADE_OVR_BONUS.get(grade)
    if bonus is None:
        return None, "grade"
    return ovrs[cell] + bonus, ""


def starters(side: dict) -> list[dict]:
    """경기 상세 matchInfo 한쪽 → 선발(교체 뺌)."""
    return [p for p in side.get("player") or [] if isinstance(p, dict) and p.get("spPosition") != SUB_POSITION]


def lineup_strength(side: dict, extras: dict[int, dict]) -> tuple[float | None, str]:
    """한쪽 선발 11장 실효 OVR 평균 → (평균, 모를 때 첫 이유). 11장이 아니거나 한 장이라도 모르면 None."""
    ps = starters(side)
    if len(ps) != 11:
        return None, "lineup"
    total = 0
    for p in ps:
        v, why = effective_ovr(p.get("spPosition"), p.get("spGrade"), extras.get(p.get("spId")))
        if v is None:
            return None, why
        total += v
    return total / 11, ""


__all__ = ["SUB_POSITION", "pos_ovrs", "effective_ovr", "starters", "lineup_strength"]
