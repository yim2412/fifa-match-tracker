"""선수 이미지 — 넥슨 CDN 직링크.

오픈API(JSON)가 아니라 넥슨 CDN 정적 파일이라 API 키가 필요 없다.
조사로 확인한 패턴(비공식, 문서에 없음 — 넥슨이 바꾸면 깨질 수 있다):

    https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction/p{spId}.png

"players/"(액션 없는 정면샷) 경로는 403(AccessDenied)이 떠서 못 쓴다 —
"playersAction/"만 실제로 열려 있는 걸 응답으로 확인했다.

디스크에 캐시해서 같은 선수를 다시 받지 않는다.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import requests

CDN_BASE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction"
_REFERER = "https://fconline.nexon.com/"

# ImageLoader/SeasonIconLoader 가 스쿼드 하나에 선수·시즌 아이콘 수십 개를
# 연달아 받는데, 매번 requests.get() 을 새로 쓰면 매 호출마다 TCP/TLS
# 핸드셰이크가 다시 열린다. ranker.py 의 팀컬러 조회에 적용해 효과를 본 것과
# 같은 최적화 — Session 으로 연결을 재사용한다. GET 만 하고 세션 상태를
# 바꾸지 않으니 스레드에서 재사용해도 안전하다.
_session = requests.Session()


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
        r = _session.get(image_url(sp_id), timeout=timeout,
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
        r = _session.get(division_icon_url(rank_index), timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path
    except requests.RequestException:
        return None


# 시즌(카드 클래스) 아이콘 주소는 get_meta("seasonid") 응답의 seasonImg(오픈API 정식 메타 — 비공식 CDN 아님)에서
# icon_url("season", seasonId, seasons) 로 만들고, 받기는 fetch_icon 한 곳이다(40단계 — 옛 fetch_season_icon 을 합쳤다).


# ── 잡다한 URL(선수 카드 사진·국기·특성 아이콘 등) ──────────────────────────
# playerinfo.py 가 긁어온 URL들은 spId 같은 고정 키가 없거나(국기·특성 아이콘은
# 여러 선수가 공유) 파일명 규칙이 제각각이라, URL 자체를 해시해 캐시 키로 쓴다.
def fetch_url(url: str, cache_dir: Path, timeout: int = 6) -> Path | None:
    if not url:
        return None
    suffix = Path(url.split("?", 1)[0]).suffix or ".png"
    path = cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()}{suffix}"
    if path.exists():
        return path
    try:
        r = _session.get(url, timeout=timeout, headers={"Referer": _REFERER})   # Referer 없으면 403 인 주소가 있다
        if r.status_code != 200 or not r.content:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path
    except requests.RequestException:
        return None


# ── 표 칸 그림(40단계) — 주소 한 곳 + 받기 한 곳 ───────────────────────────────
# 실측 장부와 근거는 docs/ROADMAP.md "화면 그림". 전부 정적 CDN 파일이라 웹 데이터 스위치 밖이다.
ICON_CDN = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/"
# 저장된 엠블럼 키는 ranker.emblem_key 가 크기·명암 칸을 뺀 "종류/파일"("crests/l11.png") — 종류마다 칸 틀이 다르다
EMBLEM_PATHS = {"crests": "crests/light/medium/", "countries": "countries/largeflags/",
                "teamcolorboost": "teamcolorboost/icon/medium/"}


def icon_url(kind: str, key, seasons: dict | None = None) -> str:
    """그림 종류(trait · train · emblem · division · season)와 키 → 주소. 모르는 종류·빈 키 → "".

    키 공간: trait/train = 포지션 특성 코드 · emblem = "종류/파일" 저장 키 · division = CDN 번호(등급 id 가 아니다 —
    models.division_icon_index 를 거친다) · season = seasonId(주소는 틀이 아니라 meta seasonImg 에서 — seasons 로 받는다)."""
    if key is None or key == "":
        return ""
    if kind == "trait":
        return f"{ICON_CDN}traits/trait_icon_{int(key)}.png"
    if kind == "train":
        return f"{ICON_CDN}trainingTrait/training_trait_icon_{int(key):03d}.png"
    if kind == "emblem":
        folder, _, name = str(key).partition("/")
        base = EMBLEM_PATHS.get(folder)
        return f"{ICON_CDN}{base}{name}" if base and name else ""
    if kind == "division":
        return division_icon_url(int(key))
    if kind == "season":
        return str((seasons or {}).get(int(key), {}).get("seasonImg") or "")
    return ""


# 세션 안 부정 캐시 — 403·404 를 다시 그릴 때마다 되묻지 않는다(성공·일시 실패는 넣지 않는다)
_missing: set[str] = set()


def fetch_icon(url: str, cache_dir: Path, kind: str = "", timeout: int = 6) -> Path | None:
    """표 칸 그림 한 장 — 캐시에 있으면 그 경로, 없으면 받는다. 실패해도 예외 없음(그 칸만 글자).

    넥슨이 Referer 없는 요청을 막는 주소가 있어 항상 붙인다(실측 200 은 이 값으로 잰 것).
    임시 파일에 쓰고 os.replace — 같은 그림을 두 로더가 동시에 받아도 반쯤 쓴 파일을 읽지 않는다."""
    if not url or url in _missing:
        return None
    folder = cache_dir / kind if kind else cache_dir
    path = folder / f"{hashlib.sha1(url.encode()).hexdigest()}.png"
    if path.exists():
        return path
    try:
        r = _session.get(url, timeout=timeout, headers={"Referer": _REFERER})
    except requests.RequestException:
        return None
    if r.status_code in (403, 404):
        _missing.add(url)
        return None
    if r.status_code != 200 or not r.content:
        return None
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(r.content)
        os.replace(tmp, path)
    except OSError:
        # 다른 로더가 같은 그림을 먼저 놓고 읽는 중이라 교체가 거절된 경우(Windows) — 그 파일이 이미 있으면 그걸 쓴다
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return path if path.exists() else None
    return path
