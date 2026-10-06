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
APP_VERSION = "v1.4.1"
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
NOTICE_VERSION = 4  # 2 = 1.1.1 랭킹 수집·ELO 기록 안내 · 3 = 1.3.1 고른 구단주 ELO 를 하루 한 번 이어서 기록
#                     4 = 1.4.1 거래 기록(키 주인 것) · 가계부용 시세 자동 읽기
# 옛 동의로 계속 써도 되는 가장 낮은 버전 — 이보다 낮으면 처음 동의(빈 칸)로, 이상이면 창을 보일 때 다시 묻기만.
# 안내를 또 올릴 땐 "옛 동의자가 모르는 새 기록이 그 사이에 생겼나"로 판단해 고친다(새 기록만 아래처럼 따로 막는다).
NOTICE_BASE_VERSION = 2
TRACK_NOTICE_VERSION = 3   # 따라가기 기록(elo_track 계정의 하루 ELO)은 이 버전 동의 뒤부터
PRICE_NOTICE_VERSION = 4   # 가계부용 시세 자동 읽기(PriceLoader)는 이 버전 동의 뒤부터 — 사용자가 연 [시세] 탭은 그대로
try:
    NOTICE_ACCEPTED = int(os.getenv(NOTICE_VAR, "0").strip() or 0)
except ValueError:
    NOTICE_ACCEPTED = 0
# 브라우저인 척하지 않고 앱 이름을 밝힌다. 2026-10-02 실측: 이 UA 로도 네 페이지가
# 브라우저 UA 와 같은 바이트로 응답했다. UA 를 아예 비우면 playerinfo 가 500 이다.
# 저장소 주소는 여기 한 줄 — 설치기(.iss 의 RepoUrl)는 test_release 가 이 값과 대조한다.
REPO_SLUG = "yim2412/fifa-match-tracker"
REPO_URL = f"https://github.com/{REPO_SLUG}"
WEB_USER_AGENT = f"FifaMatchTracker/{APP_VERSION.lstrip('v')} (+{REPO_URL})"

# 새 버전 알림 — 켤 때 한 번 GitHub 최신 릴리스 태그를 읽는다(GitHub 에 IP 가 남는다).
# 트레이에 상주하면 UPDATE_CHECK_EVERY_H 마다 다시. 끄려면 .env 에 FIFA_UPDATE_CHECK=0.
# 끄면 시즌 종료일 공지(같은 응답에 실림 — 1.3.1)도 못 받아 예측이 최근 시즌 길이로 추정한다.
UPDATE_CHECK = os.getenv("FIFA_UPDATE_CHECK", "1").strip() != "0"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases/latest"
UPDATE_CHECK_EVERY_H = 6            # 트레이에 상주하는 동안 다시 확인하는 간격(켤 때 한 번은 그대로)

# 트레이 상주(tray.py · 1.1.1)
TRAY_WAIT_S = 60                    # --tray 로 부팅 직후 트레이가 아직 없을 때 기다리는 한도
TRAY_POLL_S = 5                     # 그동안 트레이를 다시 보는 간격
TRAY_RETRY_MIN = 1                  # 동의가 필요해 창을 못 띄우는 동안 트레이를 다시 보는 간격
RELEASE_AFTER_HIDE_MIN = 30         # 창을 숨긴 지 이만큼 지나면 경기 기록을 메모리에서 내려놓는다
RELEASE_RETRY_MIN = 5               # 그때 검색·수집 중이면 이만큼 뒤에 다시
FAST_QUIT_WAIT_S = 1                # 윈도우 종료·로그오프 때 작업을 기다리는 합계 — 길면 "종료를 막고 있습니다"
QUIT_REQUEST_WAIT_S = 15            # --quit(제거기)이 떠 있던 실행본이 끝나기를 기다리는 한도
SINGLE_WAIT_OLD_S = 60            # 앞 실행본이 끝나는 중이면(업데이트 뒤 재실행) 이만큼까지 기다린다
# 테스트·여러 개 띄워 보기용 — FIFA_SINGLE_INSTANCE=0 이면 한 번만 실행 장치를 끈다
SINGLE_INSTANCE = os.getenv("FIFA_SINGLE_INSTANCE", "1").strip() != "0"


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
ELO_TRACK_MAX = 5                   # 하루 한 번 ELO 를 이어서 기록할 구단주 — 사용자가 직접 고른다(1.3.1 사용자 ⑥)
ELO_CUT_LINES = (200, 1000)         # ELO 그래프에 시계열로 긋는 순위 컷 — 1만 위는 축을 넓혀 그래프를 찌그러뜨려 툴팁만
ELO_FALLBACK_DAYS = 70              # 시즌표도 스냅숏도 없을 때 "지금 시즌"으로 볼 기간 — 최근 시즌 최장(ROADMAP R5)

# 시즌 말 순위 예측(1.3.1 · predict.py) — 같은 점수대 구단주들의 실제 하루 ELO 변화를 이어 붙인다
PREDICT_TARGETS = RANK_TIERS[:2]    # 200 · 1,000위 — 1만 위 컷은 시즌 길이에 따라 크게 움직여(R4) 목표로 안 쓴다
PREDICT_RUNS = 2000                 # 경로 수
PREDICT_SEED = 20261005             # 같은 입력이면 같은 숫자(화면이 다시 그릴 때마다 흔들리지 않게)
PREDICT_STEP_GAP_H = (20, 28)       # 이웃 스냅숏 간격이 이 안일 때만 "하루 걸음"
PREDICT_ELO_BANDS = (50, 100, 200)  # 같은 점수대 — 칸이 얇으면 차례로 넓힌다
PREDICT_MIN_PAIRS = 3               # 이웃 스냅숏 쌍이 이만큼은 있어야
PREDICT_MIN_STEPS = 300             # 내 ELO ±50 안 걸음이 이만큼은 있어야
PREDICT_MIN_CELL = 20               # (점수대, 경기 수 칸, 흐름 3분위) 칸 하나가 이만큼 안 되면 다음 단계로 푼다
PREDICT_MIN_BLOCKS = 50             # 사흘 묶음이 칸에 이만큼 있어야 묶음으로 뽑는다(아니면 하루씩)
PREDICT_MAX_DROP = 0.02             # 그 점수대 1만 위 밖 이탈이 이 비율 이상이면 "기록이 적다" — 남은 사람만 보면 치우친다
PREDICT_RECENT_DAYS = 14            # 내 하루 경기 수를 뽑는 기간(마지막 경기 날까지)
PREDICT_FETCH_SEASONS = 8           # 지난 시즌 최종 컷을 받는 시즌 수(시즌마다 RANK_CUT_RANKS 10쪽)
PREDICT_MIN_SEASONS = 2             # 최종 컷이 이만큼 시즌은 있어야
PREDICT_EQUAL_WEIGHT_SEASONS = 3    # 받은 시즌이 이 이하면 길이 가중 없이 같은 무게
PREDICT_SEASON_WEIGHT_DAYS = 7.0    # 시즌 길이 차가 이만큼이면 무게 절반
PREDICT_RECENT_SEASONS = 12         # 종료일 추정에 쓰는 최근 시즌 길이 수
PREDICT_SOON_DAYS = 14              # 경과일이 최근 시즌 전부보다 길면 "곧 끝남" — 1~이만큼 고르게
PREDICT_WARN_SHARE = 0.10           # 띠를 넓힌 경로 · 지금 컷이 최종 컷을 넘은 경로가 이 비율을 넘으면 경고
PREDICT_RANGE = (0.10, 0.90)        # "예상 순위 80% 범위"
SEASON_NOTICE_MAX_DAYS = 120        # 공지 종료일이 시작일 + 이만큼 안일 때만 받는다
RANK_EMPTY_RETRIES = 2              # 빈 쪽은 그 쪽만 이만큼 다시 받는다 — 10-05 실측: 500쪽 중 빈 쪽 하나가 회차 전체를 버렸다
RANK_EMPTY_RETRY_WAIT_S = 1.0       # 다시 받기 전 대기. 진짜 구조 변경이면 다시 받아도 비어 그대로 실패한다

# 거래 기록(1.4.1 · tradecollect.py) — 넥슨은 API 키 주인 것만 준다
TRADE_OVERLAP_DAYS = 7              # 위쪽(새 거래) 받기를 저장된 최신 날짜보다 이만큼 더 내려가 겹쳐 받는다 — 반영이 늦다(R6)
# 카드 시세 캐시(B · card_prices) — 가계부 평가용 자동 읽기의 하루 상한(카드 수 = 홈페이지 요청 수).
# 2026-10-06 실측(키 주인 계정): 평가 대상 후보 = 최근 50경기 카드 18 + 최근 14일 구매 67 = 최대 76장 → 하루 한 번에 다 읽히게
PRICE_FETCH_MAX = 80
# 스쿼드 타임라인 · 가계부(12단계 · squad_timeline.py · trade_book.py) — 출전은 선발만(교체 명단 28 은 안 센다)
HOLD_RECENT_GAMES = 50              # 이 경기 수 안에 출전한 카드 = 보유 중
HOLD_GRACE_DAYS = 14                # 산 지 이만큼 안이면 "최근 구매 · 아직 안 씀"(평가 합계에 안 넣고 따로 소계)
TIMELINE_WINDOW = 20                # 사건 앞뒤 이만큼 경기의 승률·득실
TIMELINE_MIN_GAMES = 10             # 앞뒤 경기가 이보다 적으면 흐림(표본 흐림 규칙 — 1.2.1)
TIMELINE_MAX_ROWS = 1000            # 사건 표는 최근 이만큼 — 표 채우기·열 폭 재기가 줄 수에 비례한다
# 랭커 기록(13단계 · N1 랭커와 비교 · N2 선수 카드 [랭커 기록]) — 오픈API, 하루 캐시(store.ranker_stats)
RANKER_MIN_MATCHES = 10             # 랭커 표본(matchCount — 실측 1~20)이 이보다 적으면 흐림
RANKER_COMPARE_MAX = 40             # 비교 표는 많이 쓴 카드부터 이만큼 — 요청은 RANKER_STATS_BATCH 로 나눠 1번

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
    """처음 동의가 필요한가 — 이게 참이면 창·수집·업데이트 확인·ELO 기록이 전부 멈춘다.
    v1(1.0.x) 동의자도 여기 든다 — v2 의 수집·ELO 안내를 아직 못 봤다(ROADMAP 1.3.1 검토 B 4-1)."""
    return NOTICE_ACCEPTED < NOTICE_BASE_VERSION


def notice_update_pending() -> bool:
    """옛 동의(기본 범위는 계속 돎) + 새 안내를 아직 안 봄 — 창을 사용자에게 보일 때 한 번 다시 묻는다."""
    return NOTICE_BASE_VERSION <= NOTICE_ACCEPTED < NOTICE_VERSION


def track_allowed() -> bool:
    """따라가기 계정의 하루 ELO 기록 — 그 안내(TRACK_NOTICE_VERSION)에 동의한 뒤부터."""
    return NOTICE_ACCEPTED >= TRACK_NOTICE_VERSION


def price_auto_allowed() -> bool:
    """가계부 평가용 시세를 자동으로 읽어도 되나 — 그 안내(PRICE_NOTICE_VERSION) 동의 + 웹 데이터 켜짐."""
    return WEB_DATA and NOTICE_ACCEPTED >= PRICE_NOTICE_VERSION

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
