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


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
새 버전 확인 + 앱 안 업데이트 — 켤 때 한 번 GitHub 최신 릴리스 태그와 `APP_VERSION` 을 숫자로 비교(`check()` → NEWER·LATEST·UNKNOWN — 실패·릴리스 없음은 UNKNOWN 이라 "최신"이라고 하지 않는다).
창 오른쪽 아래 카드(`widgets.UpdateCard`)의 [업데이트] → 설치 파일을 받아 **`SHA256SUMS.txt` 와 같을 때만** `/AUTOUPDATE=1` 로 실행하고 앱을 닫는다 → `.iss` 의 `IsAutoUpdate` 가 설치 뒤 앱을 다시 켠다.
설치판 판별은 exe 옆 `unins000.exe`(포터블·소스 실행은 페이지만 연다). 첨부 이름(`SETUP_ASSET`)은 `tools/release.py` 와 같아야 한다(테스트가 대조)
받는 길은 둘(84 G2 · 2026-11-10): 일반 주소(`browser_download_url`)가 네트워크·HTTP 오류면 같은 첨부의 API 경로(`url` + `Accept: application/octet-stream`)로
한 번 더 — 체크섬 목록·설치 파일 각각. 끊기면 처음부터 다시 쓰고, 둘 다 실패하면 반쯤 받은 파일을 지운다. 11-10 실측: v3.4.1 SHA256SUMS 가 두 길에서 같았다
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
# 첨부를 API 경로(releases/assets/<id>)로 받을 때의 Accept — 이게 없으면 파일 대신 JSON 설명이 온다.
# 일반 주소(github.com/.../releases/download)가 막힐 때 다시 받는 길이다(84 G2 — 2026-11-08 GitHub 504)
ASSET_API_ACCEPT = "application/octet-stream"
NOTES_MAX_LINES = 12           # 확인 창에 보여 줄 '바뀐 점' 줄 수


@dataclass(frozen=True)
class Release:
    tag: str
    page_url: str
    setup_url: str = ""        # 비면 자동 설치 불가 — 페이지만 연다
    sums_url: str = ""
    notes: str = ""
    setup_api_url: str = ""    # 같은 첨부의 API 경로 — 일반 주소가 실패하면 여기로 한 번 더
    sums_api_url: str = ""


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
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    urls = {a.get("name"): a.get("browser_download_url") or "" for a in assets}
    api = {a.get("name"): a.get("url") or "" for a in assets}
    setup = SETUP_ASSET.format(tag=tag)
    return CheckResult(NEWER, Release(tag=tag, page_url=data.get("html_url") or config.RELEASES_URL,
                                      setup_url=urls.get(setup, ""),
                                      sums_url=urls.get(SUMS_ASSET, ""),
                                      notes=_changes_excerpt(body),
                                      setup_api_url=api.get(setup, ""),
                                      sums_api_url=api.get(SUMS_ASSET, "")), notice)


def _changes_excerpt(body: str) -> str:
    """릴리스 본문의 '## 바뀐 점' 절 앞부분. 없으면 본문 앞부분. 안 보이는 주석(시즌 공지)은 걷어낸다."""
    body = _COMMENT.sub("", body or "")
    m = re.search(r"^## 바뀐 점\s*$(.*?)(?=^## |\Z)", body, re.M | re.S)
    lines = [ln for ln in (m.group(1) if m else body).strip().splitlines() if ln.strip()]
    return "\n".join(lines[:NOTES_MAX_LINES])


# ── 업데이트 뒤 "바뀐 점" 창(84 H2) ─────────────────────────────────────
CHANGELOG_FILE = "CHANGELOG.md"   # spec 의 datas 에 같은 이름으로 들어간다(test_ui_smoke 가 spec 을 대조)
SEEN_VERSION_KEY = "app/seen_version"   # settings.ini — 이 PC 에서 "바뀐 점" 을 마지막으로 보여 준 버전
_CHANGE_HEAD = re.compile(r"^## (v\d+\.\d+\.\d+)\b.*$", re.M)


def changes_between(text: str, seen: str, current: str) -> str:
    """CHANGELOG 에서 seen 보다 새롭고 current 이하인 절들(새 것부터) — 마크다운 그대로. 고를 게 없으면 "".
    seen 이 비면(이 창이 생기기 전 버전에서 올라옴) current 절 하나만."""
    cur = parse_version(current)
    if cur is None:
        return ""
    old = parse_version(seen) if seen else None
    if seen and old is None:
        return ""
    heads = list(_CHANGE_HEAD.finditer(text or ""))
    out = []
    for i, m in enumerate(heads):
        v = parse_version(m.group(1))
        if v is not None and (v == cur if old is None else old < v <= cur):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            out.append(text[m.start():end].strip())
    return "\n\n".join(out)


def read_changelog() -> str:
    """번들(또는 소스 폴더)의 CHANGELOG. 못 읽으면 "" — 창을 안 띄울 뿐 앱은 그대로."""
    try:
        return config.asset_path(CHANGELOG_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


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


def _sources(normal: str, api: str) -> list[tuple[str, dict]]:
    """받을 길 순서 — 일반 주소 먼저, 실패하면 API 경로(Accept 를 붙여야 파일이 온다)."""
    out = [(normal, {})] if normal else []
    if api:
        out.append((api, {"Accept": ASSET_API_ACCEPT}))
    return out


def _first_ok(sources: list[tuple[str, dict]], fetch: Callable[[str, dict], object]):
    """앞 길이 네트워크·HTTP 오류면 다음 길로. 전부 실패하면 마지막 오류. 취소(UpdateError)는 바로 올린다."""
    last: requests.RequestException | None = None
    for url, extra in sources:
        try:
            return fetch(url, extra)
        except requests.RequestException as e:
            last = e
    raise last if last else requests.RequestException("받을 주소가 없습니다")


def download_verified(rel: Release, dest_dir: Path,
                      progress: Callable[[int, int], None] | None = None,
                      cancelled: Callable[[], bool] = lambda: False,
                      timeout: int = 15) -> Path:
    """설치 파일을 받아 체크섬이 맞으면 그 경로. 아니면 UpdateError(받은 파일은 지운다)."""
    if not rel.setup_url or not rel.sums_url:
        raise UpdateError("이 릴리스에는 자동 설치용 파일이 없습니다. 페이지에서 직접 받아 주세요.")
    name = SETUP_ASSET.format(tag=rel.tag)
    path = dest_dir / name

    def get_sums(url: str, extra: dict) -> str:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": config.WEB_USER_AGENT, **extra})
        r.raise_for_status()
        return r.text

    def get_setup(url: str, extra: dict) -> None:
        # 도중에 끊기면 다음 길이 처음부터 다시 쓴다("wb") — 반쯤 받은 바이트를 이어 붙이지 않는다
        with requests.get(url, timeout=timeout, stream=True,
                          headers={"User-Agent": config.WEB_USER_AGENT, **extra}) as r:
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

    try:
        want = expected_sha256(_first_ok(_sources(rel.sums_url, rel.sums_api_url), get_sums), name)
        if not want:
            raise UpdateError("체크섬 목록에 설치 파일이 없습니다. 페이지에서 직접 받아 주세요.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        _first_ok(_sources(rel.setup_url, rel.setup_api_url), get_setup)
    except requests.RequestException as e:
        path.unlink(missing_ok=True)
        raise UpdateError(f"내려받지 못했습니다: {e}") from e
    except UpdateError:
        path.unlink(missing_ok=True)
        raise
    got = sha256_of(path)
    if got != want:
        path.unlink(missing_ok=True)
        raise UpdateError("받은 파일이 릴리스의 체크섬과 달라 설치하지 않았습니다. "
                          "페이지에서 직접 받아 주세요.")
    return path


def launch_installer(path: Path) -> None:
    """설치 파일을 따로 띄운다 — 호출한 뒤 앱은 바로 닫혀야 파일이 안 잠긴다
    (그래도 열려 있으면 /CLOSEAPPLICATIONS 가 닫는다)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path), *INSTALL_ARGS], close_fds=True, creationflags=flags)
