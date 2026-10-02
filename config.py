"""앱 설정 — API 키 로드와 상수."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

# 화면에 보이는 이름 — FIFA·FC ONLINE 상표를 화면에서 뺐다(2026-10-02, notice.UNOFFICIAL).
# 데이터 폴더·exe·설치 AppId·릴리스 첨부·저장소 이름은 예전 그대로 — 바꾸면 기존 데이터·업데이트가 끊긴다.
APP_NAME = "감독모드 전적 분석"
APP_VERSION = "v0.3.1"
DATA_DIR_NAME = "피파전적관리"  # 폴더명이라 공백 없이 — APP_NAME 과 별개로 둔다


def _root() -> Path:
    """개발 중에는 소스 폴더, exe로 묶이면 exe 옆 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _root()


def asset_path(name: str) -> Path:
    """소스에 같이 들어있는 정적 리소스(app_icon.ico 등)를 찾는다.

    DATA_DIR(사용자 데이터: DB·캐시·.env)과는 다른 개념 — exe 로 묶으면
    이런 리소스는 exe 옆이 아니라 sys._MEIPASS(onedir 은 exe 옆 _internal 폴더)에
    들어가므로 ROOT 를 그대로 쓰면 못 찾는다. spec 파일의
    datas 에 넣어둔 것과 짝이 맞아야 한다.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / name
    return ROOT / name


def _data_dir() -> Path:
    """데이터(키·DB·캐시)가 사는 곳.

    소스 폴더에 두면 exe 로 묶었을 때 exe 옆을 보게 돼 DB 가 둘로 갈라진다
    (실제로 겪었다 — exe 가 조용히 빈 DB 를 새로 만들었다). 실행 방식과
    무관한 고정 위치에 두고, 어느 쪽으로 켜든 같은 데이터를 본다.
    """
    override = os.getenv("FIFA_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.getenv("LOCALAPPDATA")  # 윈도우 표준 앱 데이터 위치
    if base:
        return Path(base) / DATA_DIR_NAME
    return Path.home() / f".{DATA_DIR_NAME}"  # 윈도우가 아닐 때


DATA_DIR = _data_dir()
CACHE_DIR = DATA_DIR / ".cache"
# 창 크기·위치·마지막 메뉴·시즌 — QSettings 를 레지스트리 대신 이 ini 로(데이터 폴더를 지우면 같이 사라진다)
SETTINGS_PATH = DATA_DIR / "settings.ini"
DB_PATH = DATA_DIR / "fifa.db"  # 조회한 경기 누적 — API는 약 한 달 지난 경기를 버린다(store.py 머리말)


def _migrate_from_source() -> list[str]:
    """예전에 소스 폴더에 두던 파일을 공용 폴더로 한 번만 옮긴다.

    복사가 아니라 이동 — 복사하면 양쪽이 따로 쌓여서 갈라진다.
    공용 폴더에 이미 있으면 그쪽이 정본이므로 건드리지 않는다.
    """
    moved = []
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for name in (".env", "fifa.db", "fifa.db-wal", "fifa.db-shm", ".cache"):
            src, dst = ROOT / name, DATA_DIR / name
            if src.exists() and not dst.exists():
                shutil.move(str(src), str(dst))
                moved.append(name)
    except Exception:
        pass  # 이관 실패가 앱 실행을 막으면 안 된다
    return moved


MIGRATED = _migrate_from_source()

ENV_PATH = DATA_DIR / ".env"
API_KEY_VAR = "NEXON_API_KEY"

load_dotenv(ENV_PATH)
API_KEY = os.getenv(API_KEY_VAR, "").strip()

# 넥슨 웹(데이터센터) 페이지 읽기 — 오픈API 에 없는 랭킹·팀가치·시즌표·선수 능력치.
# 오픈API 약관 밖이라 **기본 꺼짐**이고, 첫 실행 안내 창(또는 [정보])에서 사용자가 켠다
# (.env 의 FIFA_WEB_DATA=1). 끄면 ranker·playerinfo·seasons 가 요청 없이 WebDataOff 계열 예외를 던진다.
# CDN 이미지(images.py)는 페이지가 아니라 정적 파일이라 여기 안 묶는다.
# set_web_data 가 재할당한다 — 다른 모듈은 config.WEB_DATA 로 읽는다.
WEB_DATA_VAR = "FIFA_WEB_DATA"
WEB_DATA = os.getenv(WEB_DATA_VAR, "0").strip() == "1"
WEB_DATA_OFF_MSG = "넥슨 홈페이지 데이터 읽기가 꺼져 있습니다 — [정보] 에서 켤 수 있습니다"

# 이용 안내 동의 — 첫 실행 때 받는다. notice.py 의 글을 실질적으로 바꾸면 NOTICE_VERSION 을
# 올려 이미 동의한 사람에게도 다시 보인다. v0.3.0 까지는 안내 없이 웹 데이터가 켜져 있었고
# .env 에 이 값이 없으므로, 그 사람들도 업데이트 뒤 한 번 보게 된다.
NOTICE_VAR = "FIFA_NOTICE"
NOTICE_VERSION = 1
try:
    NOTICE_ACCEPTED = int(os.getenv(NOTICE_VAR, "0").strip() or 0)
except ValueError:
    NOTICE_ACCEPTED = 0
# 브라우저인 척하지 않고 앱 이름을 밝힌다. 2026-10-02 실측: 이 UA 로도 네 페이지가
# 브라우저 UA 와 같은 바이트로 응답했다. UA 를 아예 비우면 playerinfo 가 500 이다.
REPO_URL = "https://github.com/yim2412/fifa-match-tracker"
WEB_USER_AGENT = f"FifaMatchTracker/{APP_VERSION.lstrip('v')} (+{REPO_URL})"

# 새 버전 알림 — 켤 때 한 번 GitHub 최신 릴리스 태그를 읽는다(GitHub 에 IP 가 남는다).
# 끄려면 .env 에 FIFA_UPDATE_CHECK=0.
UPDATE_CHECK = os.getenv("FIFA_UPDATE_CHECK", "1").strip() != "0"
LATEST_RELEASE_API = "https://api.github.com/repos/yim2412/fifa-match-tracker/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases/latest"


def _save_env(var: str, value: str) -> None:
    """.env 의 그 한 줄만 바꾼다(다른 줄은 그대로) — 이 프로세스의 환경 변수도 같이."""
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    lines = [ln for ln in lines if not ln.strip().startswith(f"{var}=")]
    lines.append(f"{var}={value}")
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[var] = value


def save_api_key(key: str) -> None:
    """키를 .env 에 쓰고 이 프로세스의 API_KEY 도 바꾼다.

    API_KEY 를 재할당하므로 다른 모듈은 `from config import API_KEY` 가 아니라
    `config.API_KEY` 로 읽어야 한다.
    """
    global API_KEY
    key = key.strip()
    _save_env(API_KEY_VAR, key)
    API_KEY = key


def set_web_data(on: bool) -> None:
    """넥슨 홈페이지 데이터 읽기를 켜고 끈다 — 저장하고 다음 요청부터 바로 반영."""
    global WEB_DATA
    _save_env(WEB_DATA_VAR, "1" if on else "0")
    WEB_DATA = on


def accept_notice(web_data: bool) -> None:
    """이용 안내 동의 — 웹 데이터 선택과 같이 저장한다(고지 없이 켜진 채 남지 않게)."""
    global NOTICE_ACCEPTED
    set_web_data(web_data)
    _save_env(NOTICE_VAR, str(NOTICE_VERSION))
    NOTICE_ACCEPTED = NOTICE_VERSION


def notice_needed() -> bool:
    return NOTICE_ACCEPTED < NOTICE_VERSION

# 매치 종류. 정식 목록은 메타데이터 matchtype.json 으로 받아오고, 이건 폴백·기본값용.
DEFAULT_MATCH_TYPE = 52  # 감독모드 — 이 앱은 감독모드 전적만 집계한다
FALLBACK_MATCH_TYPES = [
    (50, "공식경기"),
    (52, "감독모드"),
    (40, "볼타"),
    (60, "친선경기"),
]

DEFAULT_MATCH_LIMIT = 20  # 한 번 조회할 최근 경기 수
MAX_MATCH_LIMIT = 100     # 넥슨 API가 한 번에 주는 상한
