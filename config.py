"""앱 설정 — API 키 로드와 상수."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

# 화면에 보이는 이름 — FIFA·FC ONLINE 상표를 화면에서 뺐다(2026-10-02, notice.UNOFFICIAL).
# 데이터 폴더·exe·설치 AppId·릴리스 첨부·저장소 이름은 예전 그대로 — 바꾸면 기존 데이터·업데이트가 끊긴다.
APP_NAME = "감독모드 전적 분석"
APP_VERSION = "v1.0.3"
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
NOTICE_VERSION = 2  # 2 = 1.1.1 랭킹 수집·ELO 기록 안내
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


# 넥슨 홈페이지 요청 — 한 프로세스 안 동시 요청 상한(ranker.web_get 의 세마포어). 수집·팀컬러·검색·선수 카드가 같이 쓴다.
RANK_MAX_CONCURRENT = 8
RANK_PAGE_TIMEOUT_S = 10

# 랭킹 1만 명 수집(rankcollect.py) — 기본 꺼짐. 넥슨 웹 데이터(WEB_DATA)가 꺼져 있으면 켜져 있어도 잠긴다.
# set_rank_collect 가 재할당한다 — 다른 모듈은 config.RANK_COLLECT 로 읽는다.
RANK_COLLECT_VAR = "FIFA_RANK_COLLECT"
RANK_COLLECT = os.getenv(RANK_COLLECT_VAR, "0").strip() == "1"
RANK_DB_PATH = DATA_DIR / "rank.db"  # fifa.db 와 따로 — 지우기가 파일 삭제라 VACUUM 이 필요 없다(ROADMAP 1.1.1)
RANK_COLLECT_INTERVAL_H = 24
RANK_CHECK_EVERY_MIN = 60           # 앱이 켜져 있는 동안 간격이 지났는지 보는 주기
RANK_COLLECT_WORKERS = 6            # RANK_MAX_CONCURRENT 안에서 — 수집 중 검색이 굶지 않게 2칸을 남긴다
RANK_RETRY_BACKOFF_H = (1, 2, 4, 8, 16, 24)   # 실패 회차마다 다음 대기. 성공하면 처음으로
RANK_BLOCK_ROUNDS = 3               # 403·429·Cloudflare 가 서로 다른 회차에 이만큼 이어지면 수집을 스스로 끈다(D6)
RANK_FAIL_NOTICE_ROUNDS = 3         # 실패가 이만큼 이어지면 상태에 알린다
RANK_LOCK_HEARTBEAT_PAGES = 50
RANK_LOCK_STALE_MIN = 10            # 하트비트가 이만큼 끊긴 잠금은 무효(절전으로 멈춘 실행본이 남의 수집을 막지 않게)
RANK_SEASON_DROP_RATIO = 0.5        # 행 수가 앞 스냅숏의 이 비율 밑으로 떨어지면 새 시즌
RANK_TIERS = (200, 1000, 10000)     # 집계 구간의 끝 순위 — 1~200 · 201~1,000 · 1,001~10,000
RANK_CUT_RANKS = (1, 10, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
RANK_START_JITTER_MIN = (5, 50)     # 간격이 지난 시각의 +5~+50분 — 50분이면 약 1.5분 수집이 다음 정각 전에 끝난다
RANK_RAW_KEEP_DAYS = 14             # 다른 구단주 1만 명분 원본은 이만큼만. 집계는 계속

# .env 쓰기 — 다른 실행본(설치판·포터블)이 같은 파일을 열고 있으면 os.replace 가 PermissionError 를 낸다.
ENV_WRITE_RETRY = (3, 0.2)  # (다시 시도 횟수, 간격 초)


def _save_env(var: str, value: str) -> None:
    """.env 의 그 한 줄만 바꾼다(다른 줄은 그대로) — 이 프로세스의 환경 변수도 같이.

    임시 파일에 다 쓴 뒤 os.replace 로 바꾼다 — 쓰는 도중 죽거나 다른 실행본이 읽으면
    반쯤 쓴 .env(키가 사라진)를 보게 된다. 끝내 못 바꾸면 OSError 를 그대로 올린다(호출부가 잡는다).
    """
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    lines = [ln for ln in lines if not ln.strip().startswith(f"{var}=")]
    lines.append(f"{var}={value}")
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENV_PATH.with_name(f"{ENV_PATH.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tries, gap = ENV_WRITE_RETRY
    try:
        for i in range(tries + 1):
            try:
                os.replace(tmp, ENV_PATH)
                break
            except PermissionError:
                if i == tries:
                    raise
                time.sleep(gap)
    finally:
        tmp.unlink(missing_ok=True)
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
    if not on and RANK_COLLECT:
        # 웹 데이터를 끄면 수집도 끈다 — 웹 데이터를 다시 켜도 수집은 따로 다시 고르게(조용히 되살아나지 않게)
        set_rank_collect(False)


def set_rank_collect(on: bool) -> None:
    """랭킹 수집을 켜고 끈다 — 저장하고 이 프로세스에도 바로 반영."""
    global RANK_COLLECT
    _save_env(RANK_COLLECT_VAR, "1" if on else "0")
    RANK_COLLECT = on


def read_env_switches() -> tuple[bool, bool]:
    """.env 를 디스크에서 다시 읽어 (웹 데이터, 랭킹 수집) — 다른 실행본이 바꿨을 수 있다(D6 로 껐다든지).

    load_dotenv 는 이미 든 환경 변수를 덮지 않아서 못 쓴다. 읽은 값으로 이 프로세스의 전역도 맞춘다.
    """
    global WEB_DATA, RANK_COLLECT
    try:
        vals = dotenv_values(ENV_PATH, encoding="utf-8-sig") if ENV_PATH.exists() else {}
    except (OSError, UnicodeError, ValueError):
        return WEB_DATA, RANK_COLLECT  # 못 읽으면 아는 값 그대로
    WEB_DATA = (vals.get(WEB_DATA_VAR) or "0").strip() == "1"
    RANK_COLLECT = (vals.get(RANK_COLLECT_VAR) or "0").strip() == "1"
    return WEB_DATA, RANK_COLLECT


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

# 켤 때 마지막으로 본 계정을 바로 열지. 기본은 꺼짐 — 검색 화면부터(2026-10-04 사용자 결정:
# 켜자마자 남의 닉네임으로 검색돼 있는 게 하드코딩처럼 보였다). 켜면 MainWindow.open_last_account.
OPEN_LAST_ACCOUNT = False

DEFAULT_MATCH_LIMIT = 20  # 한 번 조회할 최근 경기 수
MAX_MATCH_LIMIT = 100     # 넥슨 API가 한 번에 주는 상한
