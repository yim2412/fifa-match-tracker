"""강화 재료 시세 받기(83단계) — OVR 마다 도우미 재료 목록을 한 번씩 읽고 그날 하루 파일에 둔다.

재료 값은 대상 카드와 무관해(실측 — 대상이 달라도 같은 OVR 값이 같다) 하루 캐시를 **모든 카드가 같이 쓴다**: 첫 계산만 요청이 나가고(1→8강 약 20번),
다음 카드는 없는 OVR 만 받는다. 사용자가 [계산] 을 누를 때만 돈다. 끊겨도 받은 만큼은 남는다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import config
import enhance
import playerinfo

CACHE_NAME = "enhance_materials.json"


def _cache_path() -> Path:
    return Path(config.CACHE_DIR) / CACHE_NAME


def _load(day: str) -> dict[str, list]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("day") != day or not isinstance(data.get("levels"), dict):
        return {}
    return data["levels"]


def _save(day: str, levels: dict[str, list]) -> None:
    path = _cache_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"day": day, "levels": levels}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass   # 캐시 실패가 계산을 막으면 안 된다 — 다음에 다시 받는다


def material_prices(sp_id: int, levels: list[int], day: str, progress=lambda _done, _total: None,
                    cancel=lambda: False, fetch=None, pause=time.sleep) -> dict[int, int]:
    """재료 OVR → 한 장 값. 막힘·형식 바뀜은 PlayerInfoError 로 올린다. 취소면 받은 만큼으로 계산하지 않게 빈 dict."""
    fetch = fetch or playerinfo.fetch_boost_rows
    have = _load(day)
    missing = [o for o in levels if str(o) not in have]
    try:
        for i, ovr in enumerate(missing):
            if cancel():
                return {}
            if i:
                pause(config.ENHANCE_REQUEST_GAP_S)
            progress(i, len(missing))
            have[str(ovr)] = [list(r) for r in fetch(sp_id, ovr)]
    finally:
        if missing:
            _save(day, have)
    rows = [tuple(r) for o in levels for r in have.get(str(o), [])]
    return enhance.price_by_ovr(rows)
