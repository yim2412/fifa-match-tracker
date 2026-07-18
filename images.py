"""선수 이미지 — 넥슨 CDN 직링크.

오픈API(JSON)가 아니라 넥슨 CDN 정적 파일이라 API 키가 필요 없다.
조사로 확인한 패턴(비공식, 문서에 없음 — 넥슨이 바꾸면 깨질 수 있다):

    https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction/p{spId}.png

"players/"(액션 없는 정면샷) 경로는 403(AccessDenied)이 떠서 못 쓴다 —
"playersAction/"만 실제로 열려 있는 걸 응답으로 확인했다.

디스크에 캐시해서 같은 선수를 다시 받지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import requests

CDN_BASE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction"
_REFERER = "https://fconline.nexon.com/"


def image_url(sp_id: int) -> str:
    return f"{CDN_BASE}/p{sp_id}.png"


def cached_path(sp_id: int, cache_dir: Path) -> Path:
    return cache_dir / f"p{sp_id}.png"


def fetch(sp_id: int, cache_dir: Path, timeout: int = 6) -> Path | None:
    """캐시에 있으면 그 경로를 돌려주고, 없으면 받아서 저장한다.

    실패(404·타임아웃 등)해도 예외를 던지지 않는다 — 선수 이미지 하나
    실패했다고 나머지 표시가 막히면 안 된다.
    """
    path = cached_path(sp_id, cache_dir)
    if path.exists():
        return path
    try:
        r = requests.get(image_url(sp_id), timeout=timeout,
                         headers={"Referer": _REFERER})
        if r.status_code != 200 or not r.content:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path
    except requests.RequestException:
        return None


# ── 등급(division) 배지 ─────────────────────────────────────────────────
# 오픈API get_meta("division") 이 주는 리스트 순서(=800 슈퍼챔피언스부터
# 3100 프로3까지 오름차순, 인덱스 0~17) 그대로가 이 CDN의 번호다.
# 넥슨 웹 화면(fconline.nexon.com) 소스에서 실제로 쓰는 경로를 확인했다.
DIVISION_CDN_BASE = "https://ssl.nexon.com/s2/game/fo4/obt/rank/large/update_2009"


def division_icon_url(rank_index: int) -> str:
    return f"{DIVISION_CDN_BASE}/ico_rank{rank_index}_m.png"


def division_icon_cached_path(rank_index: int, cache_dir: Path) -> Path:
    return cache_dir / f"division_{rank_index}.png"


def fetch_division_icon(rank_index: int, cache_dir: Path,
                        timeout: int = 6) -> Path | None:
    """등급 배지 아이콘. 선수 이미지와 같은 방식 — 실패해도 조용히 None."""
    path = division_icon_cached_path(rank_index, cache_dir)
    if path.exists():
        return path
    try:
        r = requests.get(division_icon_url(rank_index), timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path
    except requests.RequestException:
        return None


# ── 시즌(카드 클래스) 아이콘 ──────────────────────────────────────────────
# get_meta("seasonid") 가 오픈API 정식 메타라서(비공식 CDN 아님) 응답에 든
# seasonImg URL을 그대로 받는다. spId 앞 3자리가 이 seasonId 다
# (stats.season_id_of 로 뽑는다).
def fetch_season_icon(season_id: int, icon_url: str, cache_dir: Path,
                      timeout: int = 6) -> Path | None:
    path = cache_dir / f"season_{season_id}.png"
    if path.exists():
        return path
    try:
        r = requests.get(icon_url, timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path
    except requests.RequestException:
        return None
