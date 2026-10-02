"""새 버전 알림 — GitHub 최신 릴리스 태그를 지금 버전과 비교한다.

배포받은 사람은 새 버전이 나와도 알 길이 없다(zip 을 직접 받았으므로). 켤 때 한 번만
묻고, 실패(오프라인·한도·릴리스 없음)는 전부 조용히 None — 알림 하나 때문에 앱이
늦게 뜨거나 오류 창이 뜨면 안 된다. 설치는 하지 않는다. 링크만 준다.
"""
from __future__ import annotations

import re

import requests

import config

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag: str, current: str) -> bool:
    """tag 가 current 보다 새 버전인가. 형식이 다르면 False(모르면 알리지 않는다)."""
    a, b = parse_version(tag), parse_version(current)
    return a is not None and b is not None and a > b


def latest_newer(current: str = config.APP_VERSION, timeout: int = 5) -> tuple[str, str] | None:
    """current 보다 새 릴리스가 있으면 (태그, 페이지 주소), 없거나 모르면 None."""
    if not config.UPDATE_CHECK:
        return None
    try:
        res = requests.get(config.LATEST_RELEASE_API, timeout=timeout,
                           headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": config.WEB_USER_AGENT})
        if res.status_code != 200:  # 404 = 릴리스가 아직 없다
            return None
        data = res.json()
    except (requests.RequestException, ValueError):
        return None
    tag = data.get("tag_name") or ""
    if data.get("draft") or data.get("prerelease") or not is_newer(tag, current):
        return None
    return tag, data.get("html_url") or config.RELEASES_URL
