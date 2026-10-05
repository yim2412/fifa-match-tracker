"""새 버전 확인과 앱 안 업데이트 — GitHub 최신 릴리스를 읽고, 설치판이면 받아서 설치까지.

배포받은 사람은 새 버전이 나와도 알 길이 없다(zip·설치 파일을 직접 받았으므로). 켤 때 한 번만
묻고, 확인 실패(오프라인·한도·릴리스 없음)는 전부 조용히 None — 알림 하나 때문에 앱이 늦게
뜨거나 오류 창이 뜨면 안 된다.

업데이트(2026-10-02, v0.3.0): 버튼을 누르면 설치 파일을 내려받아 릴리스의 SHA256SUMS.txt 와
대조하고, 같을 때만 /AUTOUPDATE=1 로 실행한 뒤 앱을 닫는다. 설치 파일이 같은 폴더에 덮어 깔고
앱을 다시 켠다(installer/*.iss 의 IsAutoUpdate). 포터블(zip)은 설치 위치를 앱이 관리하지
않으므로 릴리스 페이지만 연다.

서명 인증서가 없어서, 체크섬은 "받는 도중 깨지거나 바뀐 파일"을 막을 뿐 GitHub 계정 자체가
털린 경우는 못 막는다 — 그건 코드 서명의 영역이다.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import requests

import config

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# tools/release.py 의 asset_names 와 같아야 한다(테스트가 대조한다)
SETUP_ASSET = "FifaMatchTracker-Setup-{tag}.exe"
SUMS_ASSET = "SHA256SUMS.txt"
UNINSTALLER = "unins000.exe"   # Inno Setup 이 설치 폴더에 두는 제거기 — 있으면 설치판
INSTALL_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/AUTOUPDATE=1"]
CHUNK = 256 * 1024
NOTES_MAX_LINES = 12           # 확인 창에 보여 줄 '바뀐 점' 줄 수


@dataclass(frozen=True)
class Release:
    tag: str
    page_url: str
    setup_url: str = ""        # 비면 자동 설치 불가 — 페이지만 연다
    sums_url: str = ""
    notes: str = ""


def parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag: str, current: str) -> bool:
    """tag 가 current 보다 새 버전인가. 형식이 다르면 False(모르면 알리지 않는다)."""
    a, b = parse_version(tag), parse_version(current)
    return a is not None and b is not None and a > b


# check() 의 결과 — 카드는 NEWER 면 업데이트, LATEST 면 "최신 버전입니다", UNKNOWN 이면 아무것도 안 띄운다.
# 확인을 못 했는데 "최신"이라고 하면 거짓말이 되므로 둘을 가른다(2026-10-02 사용자 요청으로 LATEST 표시 추가).
NEWER, LATEST, UNKNOWN = "newer", "latest", "unknown"


# 시즌 종료일 공지(1.3.1 · 사용자 ②) — 릴리스 본문의 안 보이는 줄. 시작일을 같이 실어 앱이 지금 시즌과 대조한다
# (대조는 쓸 때 — predict.resolve_notice). 운영은 tools/season_notice.py, 새 릴리스는 tools/release.py 가 이어 붙인다
_SEASON_NOTICE = re.compile(r"<!--\s*season-end:\s*(\d{4}-\d{2}-\d{2})\s+start:\s*(\d{4}-\d{2}-\d{2})\s*-->")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def parse_season_notice(body: str) -> tuple[date, date] | None:
    """본문의 공지 → (종료일, 시작일). 형식이 틀리거나 start < end ≤ start + SEASON_NOTICE_MAX_DAYS 가 아니면 None."""
    m = _SEASON_NOTICE.search(body or "")
    if not m:
        return None
    try:
        end, start = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    except ValueError:
        return None
    if not (start < end <= start + timedelta(days=config.SEASON_NOTICE_MAX_DAYS)):
        return None
    return end, start


@dataclass(frozen=True)
class CheckResult:
    status: str
    release: Release | None = None
    season_notice: tuple[date, date] | None = None   # LATEST 일 때도 실린다 — 공지는 버전과 무관


def latest_newer(current: str = config.APP_VERSION, timeout: int = 5) -> Release | None:
    """current 보다 새 릴리스가 있으면 Release, 없거나 모르면 None."""
    return check(current, timeout)[1]


def check(current: str = config.APP_VERSION, timeout: int = 5) -> tuple[str, Release | None]:
    """(NEWER, Release) · (LATEST, None) · (UNKNOWN, None) — check_full 의 옛 모양(부르는 곳·테스트가 그대로)."""
    r = check_full(current, timeout)
    return r.status, r.release


def check_full(current: str = config.APP_VERSION, timeout: int = 5) -> CheckResult:
    """상태 · 새 릴리스 · 시즌 종료일 공지.

    LATEST 는 GitHub 의 최신 릴리스를 실제로 읽었고 current 가 그보다 새롭지 않을 때만 —
    공개 전 버전(current 가 릴리스보다 높음)도 LATEST 다. 확인을 껐거나·실패했거나·
    릴리스가 없거나(404)·버전 형식을 모르면 UNKNOWN.
    """
    if not config.UPDATE_CHECK:
        return CheckResult(UNKNOWN)
    try:
        res = requests.get(config.LATEST_RELEASE_API, timeout=timeout,
                           headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": config.WEB_USER_AGENT})
        if res.status_code != 200:  # 404 = 릴리스가 아직 없다
            return CheckResult(UNKNOWN)
        data = res.json()
    except (requests.RequestException, ValueError):
        return CheckResult(UNKNOWN)
    if not isinstance(data, dict):
        return CheckResult(UNKNOWN)
    tag = data.get("tag_name") or ""
    if data.get("draft") or data.get("prerelease"):
        return CheckResult(UNKNOWN)
    body = data.get("body") or ""
    notice = parse_season_notice(body if isinstance(body, str) else "")
    if parse_version(tag) is None or parse_version(current) is None:
        return CheckResult(UNKNOWN, None, notice)
    if not is_newer(tag, current):
        return CheckResult(LATEST, None, notice)
    urls = {a.get("name"): a.get("browser_download_url") or ""
            for a in data.get("assets") or [] if isinstance(a, dict)}
    return CheckResult(NEWER, Release(tag=tag, page_url=data.get("html_url") or config.RELEASES_URL,
                                      setup_url=urls.get(SETUP_ASSET.format(tag=tag), ""),
                                      sums_url=urls.get(SUMS_ASSET, ""),
                                      notes=_changes_excerpt(body)), notice)


def _changes_excerpt(body: str) -> str:
    """릴리스 본문의 '## 바뀐 점' 절 앞부분. 없으면 본문 앞부분. 안 보이는 주석(시즌 공지)은 걷어낸다."""
    body = _COMMENT.sub("", body or "")
    m = re.search(r"^## 바뀐 점\s*$(.*?)(?=^## |\Z)", body, re.M | re.S)
    lines = [ln for ln in (m.group(1) if m else body).strip().splitlines() if ln.strip()]
    return "\n".join(lines[:NOTES_MAX_LINES])


# ── 설치판 업데이트 ───────────────────────────────────────────────────
def install_dir() -> Path | None:
    """설치 파일로 깔린 exe 면 그 폴더, 아니면(포터블·소스 실행) None."""
    if not getattr(sys, "frozen", False):
        return None
    here = Path(sys.executable).resolve().parent
    return here if (here / UNINSTALLER).is_file() else None


def expected_sha256(sums_text: str, filename: str) -> str | None:
    """`sha256sum` 형식에서 그 파일의 값. CRLF·별표(바이너리 표시)도 받아 준다."""
    for line in sums_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].lstrip("*").strip() == filename:
            h = parts[0].lower()
            return h if re.fullmatch(r"[0-9a-f]{64}", h) else None
    return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class UpdateError(Exception):
    pass


def download_verified(rel: Release, dest_dir: Path,
                      progress: Callable[[int, int], None] | None = None,
                      cancelled: Callable[[], bool] = lambda: False,
                      timeout: int = 15) -> Path:
    """설치 파일을 받아 체크섬이 맞으면 그 경로. 아니면 UpdateError(받은 파일은 지운다)."""
    if not rel.setup_url or not rel.sums_url:
        raise UpdateError("이 릴리스에는 자동 설치용 파일이 없습니다 — 페이지에서 직접 받아 주세요.")
    name = SETUP_ASSET.format(tag=rel.tag)
    headers = {"User-Agent": config.WEB_USER_AGENT}
    try:
        sums = requests.get(rel.sums_url, timeout=timeout, headers=headers)
        sums.raise_for_status()
        want = expected_sha256(sums.text, name)
        if not want:
            raise UpdateError("체크섬 목록에 설치 파일이 없습니다 — 페이지에서 직접 받아 주세요.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / name
        with requests.get(rel.setup_url, timeout=timeout, headers=headers, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if cancelled():
                        raise UpdateError("취소했습니다.")
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except requests.RequestException as e:
        raise UpdateError(f"내려받지 못했습니다: {e}") from e
    except UpdateError:
        (dest_dir / name).unlink(missing_ok=True)
        raise
    got = sha256_of(path)
    if got != want:
        path.unlink(missing_ok=True)
        raise UpdateError("받은 파일이 릴리스의 체크섬과 다릅니다 — 설치하지 않았습니다. "
                          "페이지에서 직접 받아 주세요.")
    return path


def launch_installer(path: Path) -> None:
    """설치 파일을 따로 띄운다 — 호출한 뒤 앱은 바로 닫혀야 파일이 안 잠긴다
    (그래도 열려 있으면 /CLOSEAPPLICATIONS 가 닫는다)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path), *INSTALL_ARGS], close_fds=True, creationflags=flags)
