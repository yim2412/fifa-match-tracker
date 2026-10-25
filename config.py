"""앱 설정 — API 키 로드와 상수."""
from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

# 화면에 보이는 이름 — FIFA·FC ONLINE 상표를 화면에서 뺐다(2026-10-02, notice.UNOFFICIAL).
# 데이터 폴더·exe·설치 AppId·릴리스 첨부·저장소 이름은 예전 그대로 — 바꾸면 기존 데이터·업데이트가 끊긴다.
APP_NAME = "감독모드 전적 분석"
APP_VERSION = "v2.13.1"
DATA_DIR_NAME = "피파전적관리"  # 폴더명이라 공백 없이 — APP_NAME 과 별개로 둔다

# 아직 빈 메뉴 — 왼쪽 메뉴에 안 보이고 열리지도 않는다(2.1.1 "랭커" 묶음을 14단계에 자리만 잡고 16·17단계가 다 뺐다 —
# 새 메뉴의 자리만 먼저 잡을 때 이름을 넣고 빌더는 _build_pending_page). 남아 있으면 배포판을 안 만든다
# (tools/release.py preflight_problems) — 빈 페이지를 내보내지 않게.
HIDDEN_NAV_UNTIL_READY: tuple[str, ...] = ()


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

# 중앙 서버(docs/CENTRAL_SERVER.md 11절 · 1단계 ⓐ — 숨은 스위치, 화면·안내 변화 없음). 비면 지금과 같다(직접).
# 설치 ID 는 서버 모드일 때만 만든다(install_id()) — 서버를 안 쓰는 사람에게 식별자가 생기지 않게.
SERVER_URL_VAR = "FIFA_SERVER_URL"
INSTALL_ID_VAR = "FIFA_INSTALL_ID"
SERVER_URL = os.getenv(SERVER_URL_VAR, "").strip()

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
NOTICE_VERSION = 14  # 2 =1.1.1 랭킹 수집·ELO 기록 안내 · 3 = 1.3.1 고른 구단주 ELO 를 하루 한 번 이어서 기록
#                     4 = 1.4.1 거래 기록(키 주인 것) · 가계부용 시세 자동 읽기
#                     5 = 2.1.1 축구장 칩의 카드 정보(시세·급여·OVR) 자동 읽기 · 랭커 픽(16단계) — 최종 문구는 17단계 공개 직전
#                     6 = 2.3.1 다른 구단주의 사람별 값(프로필 번호만)을 그 시즌 동안 — 슈챔 연속(55 에서 '상위 20 연속' 으로 바로잡음)·최고점·첫 챔스(U1)
#                     7 = 2.5.1 포지션 특성 — 상위 TRAIT_TOP 명의 카드 특성·코치(스쿼드메이커)와 201~500위 경기
#                     8 = 2.6.1 표 칸 그림 — 구단·팀컬러 엠블럼·국기·특성 아이콘·시즌 배지를 넥슨 이미지 서버에서 받는다(수집과 무관)
#                     9 = 2.9.1 내 계정 경기마다 상대의 그날 순위 구간을 그 시즌 동안 붙인다(N23 — 랭킹 수집을 켠 사람만)
#                    10 = 2.10.1 구단주 분포 — 1~3000위 150명의 최근 1000판을 내 키로 받아 이름 없는 구단주별 요약(랭킹 수집 + 따로 켠 사람만)
#                    11 = 2.11.1 시즌 뒤 내 계정 최종 순위 한 번 확인(B8 예측 되짚기) · 구단주 요약에 미드필더·수비수 자리의 공격·수비 값(C4) — 둘 다 랭킹 수집이 켜져야
#                    12 = 2.12.1 카드 정보 자동 채우기(51단계 — 따로 켠 사람만 · 랭킹 수집과 무관)
#                    13 = 2.12.2 다른 구단주의 챔피언스 이상 판수(추정 범위)를 그 시즌 동안(57 ⓪ — 보상 100판당 · 랭킹 수집을 켠 사람만)
#                    14 = 2.13.1 경기 목록의 상대 점수(보여 주기만) · 내 스쿼드 특성(내 닉네임을 스쿼드메이커에) · [정보 › 저장 공간] · 넥슨 페이지 열기
# 옛 동의로 계속 써도 되는 가장 낮은 버전 — 이보다 낮으면 처음 동의(빈 칸)로, 이상이면 창을 보일 때 다시 묻기만.
# 안내를 또 올릴 땐 "옛 동의자가 모르는 새 기록이 그 사이에 생겼나"로 판단해 고친다(새 기록만 아래처럼 따로 막는다).
NOTICE_BASE_VERSION = 2
TRACK_NOTICE_VERSION = 3   # 따라가기 기록(elo_track 계정의 하루 ELO)은 이 버전 동의 뒤부터
PRICE_NOTICE_VERSION = 4   # 가계부용 시세 자동 읽기(PriceLoader)는 이 버전 동의 뒤부터 — 사용자가 연 [시세] 탭은 그대로
CHIP_NOTICE_VERSION = 5    # 축구장 칩의 카드 정보 자동 읽기(CardInfoLoader)는 이 버전 동의 뒤부터
RANKER_PICK_NOTICE_VERSION = 5  # 랭커 픽(상위 랭커 경기를 오픈API 로 받기)은 이 버전 동의 뒤부터
META_NOTICE_VERSION = 6    # 랭킹 수집의 사람별 표(rank.db run_open·elo_season·champ_watch · 55 fc_season)는 이 버전 동의 뒤부터 — 익명 집계는 그 전부터
TRAIT_NOTICE_VERSION = 7   # 포지션 특성(스쿼드메이커 읽기 · 201~500 경기)은 이 버전 동의 뒤부터
OPP_RANK_NOTICE_VERSION = 9   # 상대 순위 구간(rank.db match_opp_rank — 내 경기마다 상대의 그날 순위 구간)은 이 버전 동의 뒤부터
OWNER_DIST_NOTICE_VERSION = 10   # 구단주 분포(49단계 · ownerdist.py)는 이 버전 동의 뒤부터
CARD_BACKFILL_NOTICE_VERSION = 12   # 카드 정보 자동 채우기(51단계 · cardcollect.py)는 이 버전 동의 뒤부터
FC_GAMES_NOTICE_VERSION = 13   # 사람별 챔스 이상 판수(rank.db fc_games — 57 ⓪)는 이 버전 동의 뒤부터(사람별 관문 6 과 따로 — N23 꼴)
# 57 K3 경기 목록의 상대 점수(그날 수집 원본에서 꺼내 보여 주기만 — 저장 안 함)는 이 버전 동의 뒤부터
OPP_SCORE_NOTICE_VERSION = 14
MY_TRAIT_NOTICE_VERSION = 14   # 57 K12 내 스쿼드 특성(내 닉네임을 스쿼드메이커에 보낸다 · fifa.db my_traits)은 이 버전 동의 뒤부터
# 이 버전 이상 동의자에게 남은 바뀐 점은 랭킹 수집을 켠 사람에게만 해당한다 — 수집을 끈 사람에겐 다시 묻지 않고,
# 수집을 켤 때 묻는다(32단계 U6). 수집과 무관한 안내를 또 올리면 이 값도 같이 올린다 — 8 이 그렇다(그림은 수집과 무관하게
# 모두에게 해당 · 2026-10-11 사용자: 2~7 동의자 전원에게 한 번 다시 묻는다).
# 예외 — 12(카드 정보 자동 채우기)는 수집과 무관한데도 안 올렸다: 따로 켜는 토글이라, 수집을 끈 사람은 그 토글을 누를 때
# 안내 12 를 묻는다(51단계 5판 · AboutDialog._on_card_toggled). 올리면 토글을 안 켤 사람까지 업그레이드 때 창을 본다.
# 두 번째 예외 — 14(57 · 2.13.1)도 안 올렸다(U7 사용자 "추천대로"): 새로 모으는 것(상대 점수 보여 주기 · 내 특성)은 수집을 켠 사람
# 몫이고, 수집과 무관한 바뀐 글은 버튼 자리([정보 › 저장 공간])와 넥슨 페이지 열기 안내뿐이라 모두에게 다시 묻지 않는다.
NOTICE_COLLECT_ONLY_FROM = 8
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
# [정보 › 저장 공간](57 K9) — 지우기 전 쓰는 쪽이 멈추기를 기다리는 합계(작업 스레드에서 · 넘으면 안 지우고 "다시"). 가장 긴 것이
# 특성 로더의 요청 하나(TRAIT_TIMEOUT_S + 2) · 거래·랭커 기록 12초 — 그보다 넉넉히
CLEAR_HALT_WAIT_S = 30
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
# 수집을 켤 때마다 새 시각(set_rank_collect) — 사람별 표가 "끈 사이를 건너 이어진 것"으로 읽히지 않게(rankcollect.person_epoch)
RANK_COLLECT_ON_AT_VAR = "FIFA_RANK_COLLECT_ON_AT"
# 구단주 분포(49단계) — 값은 켠 시각(초까지 · 빈 값 = 꺼짐). 켜기가 "서비스 단계 키다"라는 자가 신고다 — 개발 키 오신고·연속 실패
# 판정은 이 시각 이후 시작한 바퀴만 센다(키를 바꾸면 꺼지고, 다시 켜면 새로 센다). 날짜로 재면 키를 바꿔 같은 날 다시 켤 때
# 옛 키의 그날 요청이 새 키 누적에 섞여 개발 키가 서비스 키로 보인다(③a 리뷰). _OFF 는 앱이 스스로 끈 사유(devkey|fail|key — 사용자가 끄면 빈 값).
# set_owner_dist 가 재할당한다 — 다른 모듈은 config.OWNER_DIST_ON 으로 읽는다.
OWNER_DIST_VAR = "FIFA_OWNER_DIST"
OWNER_DIST_OFF_VAR = "FIFA_OWNER_DIST_OFF"
OWNER_DIST_ON = os.getenv(OWNER_DIST_VAR, "").strip()
OWNER_DIST_OFF = os.getenv(OWNER_DIST_OFF_VAR, "").strip()
# 카드 정보 자동 채우기(51단계 U5) — 구단주 분포와 같은 꼴: 값은 켠 시각(빈 값 = 꺼짐) · _OFF 는 스스로 끈 사유(blocked — 사용자가 끄면 빈 값).
# 랭킹 수집과 무관하다(카드 정보는 사람과 이어지지 않는다). set_card_backfill 이 재할당한다 — 다른 모듈은 config.CARD_BACKFILL_ON 으로 읽는다.
CARD_BACKFILL_VAR = "FIFA_CARD_BACKFILL"
CARD_BACKFILL_OFF_VAR = "FIFA_CARD_BACKFILL_OFF"
CARD_BACKFILL_ON = os.getenv(CARD_BACKFILL_VAR, "").strip()
CARD_BACKFILL_OFF = os.getenv(CARD_BACKFILL_OFF_VAR, "").strip()
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
# 랭커 메타(2.3.1 · rankcollect.process_meta) — 초안 값들. 근거는 ROADMAP 2.3.1 실측 장부
RANK_GAPS_WARN = 50                 # 빈 순위가 이만큼 이상이면 [정보] 에 노란 글자(R4: 지금 0)
RANK_CONT_MAX_H = 36                # 앞 스냅숏과 데이터 시각 차가 이 안이면 이어진 것 — 넘으면 틈(연속·첫 챔스를 자른다)
RANK_ELO_BIN = 50                   # 점수 분포 막대 칸 폭(R9: 2,934~4,538 → 34칸 남짓)
META_MIN_USERS = 5                  # 메타 변화 표 — 두 시점 다 이보다 적은 줄은 흐림
CHAMP_GRADE_MAX = 1                 # 등급 칸 번호 — 0 상위 20 표시 · 1 챔피언스(둘 다 챔스 이상). 판정은 rankcollect.is_champ 한 곳
# 랭킹 화면 등급 칸 0 — 챔피언스 중 상위 20위에 붙는 표시라 슈퍼챔피언스가 **아니다**(55 M1 실측: 칸 0 = 정확히 20명 ·
# 데이터센터 용어 정의). N3 "상위 20 연속"(rankcollect.is_top20_mark)만 쓴다. FC 보상은 칸으로 슈챔을 모르니 범위로(grade_fc_range)
TOP20_MARK_GRADE = 0
# 랭커 메타 ② 스냅숏 읽기(2.4.1 · rankmeta.py) — 근거는 ROADMAP 2.4.1 실측 장부
META_TOP_TIERS = (200, 2000, 5000, 10000)   # "상위 N명" 누적 구간(추이 탭의 띠 구간 TREND_TIERS 와 다르다)
B3_WEIGHTS = {"elo": 300, "rate": 200, "value": 300, "champ": 200}   # 가성비 1,000점 — 구단가치는 낮을수록
B3_MIN_USERS = 20                   # 가성비 점수 · 구단가치 × 팀컬러 표에 넣는 팀컬러 인원(R7)
B3_MIN_TEAMS = 5                    # 점수를 낼 팀컬러 수 — 백분위는 이보다 적으면 칸 몇 개로 갈려 뜻이 없다(R7: 상위 200 은 2종)
B4_BINS = 8                         # 구단가치 구간 수(경계 = 그 스냅숏 분위 · 두 자리 반올림)
N10_MIN_GAMES = 10                  # 하루 판 수가 이보다 적은 걸음은 뺀다 — 승률이 0·50·100% 로 튄다
N10_BINS = (35, 40, 45, 50, 55, 60, 65)   # 하루 승률 칸 경계(%) — 35 밑·65 위는 한 칸씩
N10_PEER_RATE = 2.5                 # 검색 계정 한 줄 — 하루 승률 ±이만큼(%p)
N10_PEER_BANDS = (100, 200)         # 같은 점수대 ±(차례로 넓힌다 — 걸음이 PREDICT_MIN_STEPS 미만이면)
WEEKLY_CUTS = (200, 1000, 10000)    # 주간 요약 컷 변화
WEEKLY_TOP_N = 10                   # 급상승 · 하루 최다 승 줄 수
WEEKLY_PARK_RANK = 200              # 주차 중 — 이 순위 안 · 시즌 종료 WEEKLY_PARK_DAYS 일 안 · 이웃 쌍 사이 0판
WEEKLY_PARK_DAYS = 7
OPP_CARD_MIN = 5                    # 상대가 쓰는 카드 — 만난 판이 이보다 적으면 흐림
N12_START_DAYS = 3                  # 시즌 시작일보다 이만큼 넘게 뒤에 처음 본 사람은 첫 챔스 판수를 '늦게 봄'으로
RANK_CKPT_WARN = 24                 # WAL 비우기가 이만큼 이어서 막히면 [정보] 에 한 줄
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
# 순위 보상 FC(50단계 — predict.main_reward) — (적용 시작일, 순위 상한, FC). **넥슨 공식 수치가 아니라 사용자 확인값**(2026-10-13):
# 시즌 말 200위 안만 — 1~10위 8,000 · 11~50위 6,000 · 51~100위 5,000 · 101~200위 4,400. 바뀌면 새 시작일 줄로 더한다(옛 줄은 둔다)
RANK_REWARDS = (
    (date.min, 10, 8000),
    (date.min, 50, 6000),
    (date.min, 100, 5000),
    (date.min, 200, 4400),
)
# B8 예측 되짚기(내 계정의 시즌 최종 순위를 넥슨 홈페이지에서 한 번 확인) — 이 안내 버전 동의 뒤부터.
# 조각 ① 에서는 자리 값(99)으로 두었다가 ④(2026-10-18)에서 NOTICE_VERSION 과 같이 확정 — 값을 되돌려 99 로 두면 아무도 통과 못 한다
PREDICT_CHECK_NOTICE_VERSION = 11
# 구단주 분포 v2 재료(50단계 C4 — owner_dist.pos_json · 자리 줄별 지수 재료) — 바퀴를 시작할 때 이 안내 버전에 동의했으면 그 바퀴가 v2.
# ④ 에서 PREDICT_CHECK_NOTICE_VERSION 과 같이 확정(그 전엔 v2 바퀴가 생기지 않았다)
OWNER_DIST_V2_NOTICE_VERSION = 11
# C4 칸 — 측정 C(2026-10-13 · ROADMAP 50 "측정 C·D 결과")를 통과한 (자리 줄, 쪽)만. 지표는 선수 status 키
C4_CELLS = (("MF", "atk"), ("DF", "atk"), ("DF", "def"))
C4_METRICS = {"atk": ("goal", "effectiveShoot", "assist", "dribbleSuccess"), "def": ("tackle", "block", "intercept")}
# C4 판정 — 측정 C 판정기(tools/step0_owner_dist/analyze50.py)와 같은 값. 바꾸면 측정 C 를 다시 잰다.
# 식은 두 군데 다르다(앱 쪽이 맞는 식 · 강제 v2 바퀴는 129명 전원이 세 칸을 가져 값 차이 0): 같은 값은 반만 센다(판정기는 다 셈) ·
# 층 가중은 칸마다 그 칸 재료가 있는 사람 수로 나눈다(판정기는 구단주 전체 수)
C4_CUTS = (20, 40, 60, 80)          # 5단계 경계 = 구단주 지수의 층 가중 백분위(95 꼬리는 150명 중 약 7명이 정해 흔들린다 — 계획 ⓓ)
C4_STAGES = ("미달", "1인분", "잘함", "에이스", "압도")
C4_MIN_LINE_GAMES = 300             # 내 그 줄 출전이 이보다 적으면 판정 보류(계획 ⓔ)
C4_Z = 1.645                        # 내 지수 ± 이만큼의 se 가 두 단계에 걸치면 판정 보류(90% — 49 SURE_P 와 같은 뜻)
PREDICT_CHECK_RETRY_DAYS = 3        # 처음 물은 날부터 이 기간은 not_found 도 하루 한 번 다시 묻는다
SEASON_NOTICE_MAX_DAYS = 120        # 공지 종료일이 시작일 + 이만큼 안일 때만 받는다
RANK_EMPTY_RETRIES = 2              # 빈 쪽은 그 쪽만 이만큼 다시 받는다 — 10-05 실측: 500쪽 중 빈 쪽 하나가 회차 전체를 버렸다
RANK_EMPTY_RETRY_WAIT_S = 1.0       # 다시 받기 전 대기. 진짜 구조 변경이면 다시 받아도 비어 그대로 실패한다

# 거래 기록(1.4.1 · tradecollect.py) — 넥슨은 API 키 주인 것만 준다
TRADE_OVERLAP_DAYS = 7              # 위쪽(새 거래) 받기를 저장된 최신 날짜보다 이만큼 더 내려가 겹쳐 받는다 — 반영이 늦다(R6)
# 카드 시세 캐시(B · card_prices) — 가계부 평가용 자동 읽기의 하루 상한(카드 수 = 홈페이지 요청 수).
# 2026-10-06 실측(키 주인 계정): 평가 대상 후보 = 최근 50경기 카드 18 + 최근 14일 구매 67 = 최대 76장 → 하루 한 번에 다 읽히게
PRICE_FETCH_MAX = 80
# 카드 정보(B · card_info — 2.1.1) — 선수 페이지 한 요청에 시세·급여·OVR(1강·카드 기본 포지션)이 같이 온다.
# 하루 상한은 쓰임별로 따로 센다(store.api_budget): 사용자가 연 선수 카드 = 없음 · 가계부 = PRICE_FETCH_MAX · 축구장 칩 = 아래.
CHIP_FETCH_MAX = 60                 # 초안 — 스쿼드 창·비교를 하루 몇 번 여는지로 다시 정한다(ROADMAP 2.1.1 "재지 않은 것")
CARD_INFO_TTL_DAYS = 30             # 급여·OVR 은 라이브 패치로만 바뀐다 — 30일인 근거는 아직 없다(재지 않은 것)
# 팀컬러(2.2.1 · teamcolor.py) — 이름이 같은 팀컬러 11쌍(2026-10-06 목록 801개 실측 — 쌍마다 강화 하나 + 클럽 하나).
# 상수인 이유: 받은 목록으로 넓히면 효과 창을 열기 전후로 승률 표의 줄이 바뀐다(ROADMAP 2.2.1 U2). check_api 가 실제 목록과 대조한다
TEAMCOLOR_DUP_NAMES = frozenset({
    "Continental Heroes", "Dramatic Comebacks", "Football Association Champions", "Greatest Runner-Ups",
    "Legend of Europa", "Number 7", "Spartan", "Step Higher", "Untouchable Champions", "Winning Streak", "Wonderboys"})
TEAMCOLOR_CACHE_DAYS = 7            # 효과 목록·단계 캐시 — 30일이면 시즌 업데이트의 효과 조정이 한 달 늦게 보인다
TEAMCOLOR_MIN_OPPONENTS = 3         # 승률 표에서 상대가 이보다 적은 팀컬러는 흐림 — 한 사람과의 반복 대전이 승률을 끌고 가지 않게(초안)
TEAMCOLOR_PLAYERS_PAGE = 100        # 넥슨 선수 목록 한 요청의 최대 인원(쪽 넘김 없음 — OVR 상한으로 이어 받는다)
POSITION_OPP_FORMATIONS = 3         # [포지션별 최다 상대 › 축구장] 팀컬러마다 칩으로 보이는 포메이션 수(사용 많은 순 · 사용자 2026-10-11)
# 포지션 특성(32단계 · traitcollect.py) — 스쿼드메이커 팀 칸 조회. 팀 = (n1TeamType 1 대표/0 클럽, n1TeamPart 0·1·2 = A·B·C)
# 대표를 먼저 본다(랭커 경기는 대부분 대표 팀 — 맞는 팀을 일찍 찾으면 거기서 멈춘다). 이 단계는 전술 번호 0(= 전술 1) 한 칸만 본다
TRAIT_TEAM_ORDER = ((1, 0), (1, 1), (1, 2), (0, 0), (0, 1), (0, 2))
TRAIT_TACTIC_SEQ = 0
TRAIT_TOP = 500                     # 특성 대상 1~500위(U7) — 추천 범위(RANKER_RECOMMEND_TOP)와 따로. 밖으로 밀린 줄은 켤 때 정리
TRAIT_TIMEOUT_S = 10               # 요청 하나 타임아웃 — 앱 종료 대기 표(TraitLoader)가 이 값 + 2초를 기다린다
TRAIT_STALE_DAYS = 10               # 201~500 의 마지막 경기 다시 묻는 주기(1~200 은 RANKER_PICK_STALE_DAYS) — 14일 정리보다 짧게(예산표)
TRAIT_STALE_RETRY_DAYS = 7          # 낡은 웹 값(stale)인 사람은 경기가 그대로면 이만큼 뒤에만 웹을 다시 본다
TRAIT_WEB_DAILY_REQ = 1000          # 스쿼드메이커 하루 요청 상한(trait_web) — 한 명 최대 6 · 다시 확인은 대개 1(R6 · 약 20MB/일)
TRAIT_BLOCK_DAYS = 3                # 403·429·Cloudflare 가 서로 다른 날 이만큼 이어지면 특성 받기만 스스로 끈다(랭킹 수집은 안 끈다)
TRAIT_RECHECK_MARGIN_DAYS = 2       # 받은 지 RANK_RAW_KEEP_DAYS − 이만큼 지난 랭커는 처음 보는 사람보다 먼저 다시 묻는다(정리에 지워지기 전에)
# [포지션 특성] 탭(단계 4) — 구간은 랭커 픽 범위·특성 범위 둘. 문턱은 초안(첫 2주 채움 실측 뒤 다시)
TRAIT_TIERS = (200, TRAIT_TOP)      # 앞은 RANKER_PICK_TOP 과 같아야 한다 — 그 상수가 아래에 있어 숫자로 두고 test_traits 가 대조
TRAIT_MIN_PEOPLE = 30               # 고른 포메이션 인원이 이보다 적으면 표를 흐림(RANKER_PICK_MIN_RANKERS 와 같은 규칙)
TRAIT_MIN_COVER = 0.5               # 구간 대상 중 특성 줄이 있는 비율이 이보다 낮으면 흐림 — 첫 바퀴엔 위 순위만 차 있다(검토 B)
MY_TRAIT_RECENT = 5                 # 57 K12 — 내 특성의 기준 경기: 최근 이만큼 중 선발이 있는 첫 경기(몰수 빈 선발 건너뜀)
TRAIT_TOP_NAMES = 3                # 포지션·칸마다 많이 넣은 순 이만큼
# 강화 단계 → OVR 가산(1강 = 0). 2026-10-06 능력치 시뮬레이터(PC PlayerAbility)로 카드 셋(CM·ST·GK, 시즌 셋) × 1~13강을
# 재서 셋이 같았다. check_api.py 가 넥슨과 다시 대조한다. 칩의 OVR 은 카드 기본 포지션 기준(다른 자리에 세우면 게임 값과 다르다)
GRADE_OVR_BONUS = {1: 0, 2: 1, 3: 2, 4: 4, 5: 6, 6: 8, 7: 11, 8: 15, 9: 17, 10: 19, 11: 21, 12: 24, 13: 27}
# 승률에 무승부를 늘 넣는다(승÷전체) — 2026-10-19 사용자("무승부 포함 승률로", 앱 전체). 끄면 A6 규칙(섞인 묶음은 승÷(승+패))으로 돌아간다.
# 판정은 models.is_mixed 한 곳이 이 값을 본다
WIN_RATE_DRAWS_IN = True
# 승률 식(2.6.1 A6 — models.win_pct) — 숫자 옆 표시(widgets.win_rate_text)의 머리·툴팁 설명
WIN_RATE_NOTE_MIXED = "무승부가 나는 경기와 안 나는 경기가 섞여 있어 무승부를 빼고 셉니다(승÷(승+패))."
WIN_RATE_NOTE_SINGLE = "무승부까지 분모에 넣어 셉니다(승÷전체)."
# 등급 체류(2.6.1 N2 — habits.division_stays) — 이웃 경기 사이가 이 날 수를 넘으면 기록 빈 구간
N2_GAP_DAYS = 3
# N23 상대 순위 구간(43단계 — rank.db match_opp_rank). 순위 구간 = 상한 순위(이하) — 모르면(1만 위 밖·못 찾음) NULL.
# 점수 격차(상대 − 나) 구간 = 모서리 사이의 칸 번호(bisect_right) — 0 은 첫 모서리보다 아래, 마지막 번호는 마지막 모서리 이상
OPP_RANK_BANDS = (200, 1000, 3000, 10000)
OPP_GAP_EDGES = (-300, -150, -50, 50, 150, 300)
OPP_SNAP_WINDOW_H = 24             # 경기 시각 ±이 시간 안의 스냅숏만 쓴다(그보다 멀면 상대 순위가 달라졌을 수 있다)
OPP_ROW_MIN = 10                   # 표 한 줄의 승률 분모(rated)가 이보다 적으면 "표본 N" 을 붙여 흐리게
OPP_MIN_KNOWN = 30               # 순위를 아는 경기가 이보다 적으면 표를 안 낸다(빈 화면 문구)
OPP_FILL_MAX_S = 30                # 채우기 한 번의 시간 상한(초) — 넘으면 거기서 멈추고 다음 바퀴가 이어 채운다
# 57 K1 매치업 필터([상대 전적 › 전적]) — 경기 수 고르기 · 표본(상대마다 판수) 문턱 · 직접 지정 상한
K1_GAME_COUNTS = (50, 100, 200, 500)
K1_SAMPLE_MINS = (1, 3, 5, 10)
K1_CUSTOM_MAX = 10000
# 57 K3 경기 목록 상대 정보 열 넷 — 펼치면 글자가 이 크기(px) 아래로 줄어야 하는 창 폭이면 접는다(1280 폭: 열 13개에 15px 로 1,309px
# 필요 · 표 폭 942 — 9px 까지 줄고 머리글이 잘렸다 · 2026-10-20 실화면). 11 = 1600×900 에서 펼친 채 13px 가 나오는 것보다 작게
MATCH_EXTRA_MIN_PX = 11
# 57 K7 [랭킹 추이 › 추이] 위 상위 랭커 카드 — 마지막 스냅숏 1~N위
K7_TOP_N = 3
# 57 K11 [랭킹 추이 › 보상] — 100판당 보상(산출 기준 "효율 랭킹": 챔스 이상에서 100판 넘게 뛴 사람만 · 판수는 하한 g_lo 로 거른다)
K11_MIN_GAMES = 100
K11_MIN_PER_BAND = 10               # 구간 인원이 이보다 적으면 그 칸은 중앙값을 안 낸다(표본 미달 — 칸이 비어 보인다)
# 57 K8 비주류 팀컬러 — 1만 명 중 이 인원 미만(산출 기준 "1% 미만" · 순위 구간을 좁혀도 기준은 전체)
MINOR_USERS = 100
# 57 K4 [스쿼드·이적 › 이력] — 같은 값이 이 경기 수 미만이면 깜빡임으로 보고 앞 구간에 붙인다(habits.history_runs)
K4_MIN_RUN = 3
# 57 K6 대시보드 한 줄 총평 — 이 순서로 처음 나오는 문장 하나(analysis.headline · 새 판정 없음 E3).
# "drawdown" = 점수 리포트의 최대 낙폭이 같은 구간 사람들 중앙보다 깊을 때(55 R2 재료) · 나머지는 analysis 규칙 함수 이름
K6_ORDER = ("drawdown", "_streak_rules", "_opponent_rules", "_finishing_rules", "_flow", "_clutch_rules",
            "_creation_rules", "_possession_rules", "_formation_rules", "_half_rules", "_minute_rules", "_goal_type_rules")
# A2 FC 보상(43단계 — stats.win_rewards) — (적용 시작일, 경기 때 내 division, 승리당 FC). **넥슨 공식 수치가 아니라 사용자 확인값**(2026-10-12):
# 이긴 판마다 챔피언스(900) 15 · 슈퍼챔피언스(800) 20, 그 밖의 등급은 없다. 몰수승(상대 탈주)도 나온다(사용자 확인).
# 넥슨이 바꾸면 사용자가 알려 줘야 한다 — 바뀐 값은 새 시작일 줄로 더한다(옛 줄은 두어 지난 경기가 그때 값으로 센다).
# 첫 줄 시작일은 맨 앞(date.min) — 확인일로 두면 그 전 승리가 조용히 0 이 된다. 900·800 은 models.CHAMPION/SUPER_CHAMPION_DIVISION_ID 와 같아야 한다(test_habits 가 대조)
FC_REWARDS = (
    (date.min, 900, 15),
    (date.min, 800, 20),
)
# 55 구단 머리 줄·점수 리포트(habits.club_day·first_reach·score_report · rankmeta.club_head) — 근거는 ROADMAP 55단계 U1~U10
R_MIN_POINTS = 7                    # 점수 리포트(R1~R3) — 지금 시즌 하루 점이 이보다 적으면 침묵(M5: 계정 하나가 2점)
R2_BAND = 100                       # 낙폭 "같은 구간" = 내 시즌 최고점 ± 이만큼(U2 · 1회차 [중] — 칸이 아니라 창)
R2_BAND_MIN = 20                    # 창 안 사람이 이보다 적으면 중앙값을 안 낸다(M4 — 4600 구간 1명)
H5_GAP_DAYS = 7                     # 내 첫 달성 판수 — 저장 경기 사이가 이보다 길면 "중간이 빔"(API 는 약 한 달만 준다 · 쉬는 날과 못 가른다)
HEAD_TOP_CUT = 200                  # 머리 줄 "지금 N위까지"(H2) — cut_elo 에 있는 순위여야
D1_MIN_GAMES = 5                    # 달력 화살표(D1) — 그날 승률 분모가 이보다 적으면 안 잰다(U3 · 띠 식은 N16 과 같고 하한만 다르다)
G1_GAME_LEVEL_DAYS = 14             # 등급 그래프를 확대해 이 날 수 이하만 보이면 하루 한 점 대신 경기마다 한 점(U4)
# N19 칩(2.7.1 — habits.paired_diff · Diff.clear) — 같은 경기 상대 대비 차이가 이 %p 이상이어야 띄운다.
# 임시값(2026-10-11 U7): 4계정 × 16범위 실측에서 2.0 은 태클 칩이 범위마다 켜졌다 꺼졌고 2.5 는 계정1 결정력만 늘 떴다.
# 42단계(구단주 분포)에서 백분위 판정으로 바꾼다 — docs/DONE.md 41단계 "크기 문턱 실측"
N19_MIN_GAP = 2.5
# "마무리 vs 창출" 문장의 창출 쪽 — 경기당 유효슛이 같은 경기 상대보다 이 % 이상(상대 평균 대비) 달라야 한다.
# 임시값(2026-10-11): 3계정 실측 +9.6%(계정1, 구간 ±1.5%) · +2.0% · +1.4% 로 가름이 선다. 42단계 백분위 판정에서 다시 본다
N19_CREATE_MIN_REL = 5.0
N19_MIN_SHOTS = 60                  # 결정력·유효슛 비율 칩 — 나·상대 슛이 각각 이만큼은 있어야(analysis.MIN_SHOTS 와 같은 값으로 시작)
N19_MIN_TACKLES = 60                # 태클 성공 칩 — 태클 시도 하한
# N17 골 사이 시간(habits.goal_flow) — 이 분 모서리로 가른다: <5 · 5~10 · 10~20 · 20+
N17_GAP_EDGES = (5, 10, 20)
# N18 공격 루트(2.7.1 — habits.classify_route) — 상위 3계정 어시 있는 슛 73,385개 실측(2026-10-11):
# 어시 위치 |y−0.5| 은 0.30 밖에서 갈리고(박스 폭 40.3m ÷ 68m = 0.296 — 물리 근거), 측면 어시의 37% 가 골라인(x≥0.98)에 몰린다.
# 컷백(측면·골라인 근처·슛보다 골라인 쪽에서 뒤로 내줌)의 비중은 골라인 문턱 0.88~0.94 에서 24~26% 로 거의 안 변해 0.90(골라인 10.5m 안)으로 둔다.
N18_WIDE_Y = 0.30                   # 어시가 박스 폭 밖(|y−0.5| 이 이상) — 크로스·컷백 후보
N18_BYLINE_X = 0.90                 # 컷백 — 어시 x 가 이 이상(골라인 쪽)이고 슛 x 보다 골라인에 가까울 때
N18_MIN_SHOTS = 200                 # 루트 표를 내는 하한(내 슛) — 미만이면 "아직 못 가릅니다"
N18_MIN_ROUTE = 30                  # 루트 한 줄의 차이 판정 하한 — 나·상대 슛이 각각 이만큼은 있어야(정규 근사 구간을 믿을 수 있는 표본)
# N16 하루 승률 운 범위(2.7.1 — habits.daily_luck) — 하루 이 판(승률 분모 rated) 미만은 점만 흐리고 띠 밖 날 수에서도 뺀다
N16_MIN_DAY = 20
N16_BAND_SIGMA = 2.0                # 띠 = 평균 승률 ± 이 σ(이항)
N16_REQUIRED_DIFFS = (1, 2, 3, 5)   # "몇 판부터 실력 차이로 읽나" 표 — 두 계정 승률 차(%p)
# A3 연승·연패 우연 기준(habits.streak_baseline) — draw_possible 층 안에서 순서를 이 횟수만큼 섞는다. 시드 고정(같은 목록 = 같은 값)
A3_SHUFFLES = 200
A3_SEED = 20261011
A3_QUANTILE = 0.975                 # 연승·연패 각각 이 분위를 넘으면 우연 밖(둘 합쳐 약 5%)
# N20 경기 습관(habits.habit_tables) — 세션 = 이 분 넘게 쉬면 끊는다
SESSION_GAP_MIN = 30
N20_GAP_EDGES = (13, 20, 60)        # 직전 경기 시작과의 간격(분) 모서리: ~13 · 13~20 · 20~60 · 60+  (13분 이하 = 바로 이어서 — 앞 경기 길이가 섞인다)
N20_SESSION_EDGES = (2, 6, 21)      # 세션 안 몇 번째 판: 1 · 2~5 · 6~20 · 21+
N20_HOUR_STEP = 3                   # 시간대 칸 폭(시) — 8칸
N20_MIN_EXPECTED = 5                # 카이제곱 칸 기대 빈도가 이보다 적으면 이웃 칸과 합친다
N20_ALPHA = 0.05                    # 표마다 따로 판정
N20_RESID_Z = 2.0                   # 강조할 칸 잔차 문턱
# 스쿼드 타임라인 · 가계부(12단계 · squad_timeline.py · trade_book.py) — 출전은 선발만(교체 명단 28 은 안 센다)
HOLD_RECENT_GAMES = 50              # 이 경기 수 안에 출전한 카드 = 보유 중
HOLD_GRACE_DAYS = 14                # 산 지 이만큼 안이면 "최근 구매 · 아직 안 씀"(평가 합계에 안 넣고 따로 소계)
TIMELINE_WINDOW = 20                # 사건 앞뒤 이만큼 경기의 승률·득실
TIMELINE_MIN_GAMES = 10             # 앞뒤 경기가 이보다 적으면 흐림(표본 흐림 규칙 — 1.2.1)
TIMELINE_MAX_ROWS = 1000            # 사건 표는 최근 이만큼 — 표 채우기·열 폭 재기가 줄 수에 비례한다
# 랭커 기록(13단계 · N1 랭커와 비교 · N2 선수 카드 [랭커 기록]) — 오픈API, 하루 캐시(store.ranker_stats)
RANKER_MIN_MATCHES = 10             # 랭커 표본(matchCount — 실측 1~20)이 이보다 적으면 흐림
RANKER_COMPARE_MAX = 40             # 비교 표는 많이 쓴 카드부터 이만큼 — 요청은 RANKER_STATS_BATCH 로 나눠 1번
# 선수로 구단주 찾기(2.1.1 · 12) — fifa.db match_squads 색인(store.backfill_squads)
CARD_OWNER_DAYS = 30                # 이만큼 안에 선발로 쓴 구단주만
SQUAD_BACKFILL_DELAY_S = 30         # 첫 화면 뒤 색인 백필 시작 — 1만 경기 첫 화면(공통 기준 3)에 끼지 않게
SQUAD_BACKFILL_TRAY_DELAY_S = 120   # 트레이로 켜졌으면(첫 화면 없음) 켠 뒤 이만큼
# 랭커 픽(2.1.1 · 6 — rankerpick.py) — 마지막 스냅숏 상위 RANKER_PICK_TOP 명의 최근 경기 하나. 화면(탭)이 보일 때만 받는다(U2)
RANKER_PICK_TOP = 200
RANKER_RECOMMEND_TOP = 1000         # 추천 후보(17단계 ③)는 이 순위 안만 — 범위 밖 줄은 켤 때 정리
RANKER_PICK_DAILY_REQ = 300         # 하루 오픈API 요청 상한(U2 — 개발 단계 키 하루 1,000 의 30%)
RANKER_PICK_STALE_DAYS = 3          # 이만큼 안에 받은 랭커는 다시 안 묻는다(닉네임이 바뀌었으면 바로 다시)
RANKER_PICK_MAX_AGE_DAYS = 14       # 마지막 경기가 이보다 오래된 랭커는 집계에서 뺀다(받은 날이 아니라 경기 날) — 초안
RANKER_PICK_GAP_S = 0.25            # 요청 사이 — 개발 단계 키 초당 5 미만
RANKER_PICK_429_WAIT_S = 60         # 429 를 받으면 이만큼 쉬고 한 번 더 → 또 429 면 그날 멈춘다(초당·하루 429 가 같은 코드라)
RANKER_PICK_MIN_RANKERS = 30        # 이보다 적은 랭커로 낸 카드 비율은 흐림(표본 흐림 규칙 — 1.2.1)
# 구단주 분포 v2(49단계 · ownerdist.py) — 사용자 앱이 자기 서비스 키로 1~3000위 구단주 150명의 최근 1000판을 받는다.
# 표본·층 가중은 2026-10-13 새 표본 검증(ROADMAP "49단계" 조각 ①)에서 통과한 값 그대로 — 바꾸면 검증을 다시 한다
OWNER_DIST_STRATA = (200, 1000, 3000)         # 층 상한 순위 — 1~200 · 201~1000 · 1001~3000
OWNER_DIST_STRATA_POP = (200, 800, 2000)      # 층 인구(= 가중) — 표본은 층당 같은 수라 가중이 모집단 비율을 되살린다
OWNER_DIST_PER_STRATUM = 50
OWNER_DIST_MATCHES = 1000                     # 구단주당 최근 판수
OWNER_DIST_PAGE = 100                         # 목록 한 번에 받는 상한
OWNER_DIST_WORKERS = 4                        # 한 구단주 안에서 상세를 받는 동시 요청 수
OWNER_DIST_MIN_GAMES = 800                    # 이만큼 못 받은 구단주는 분포에서 뺀다
OWNER_DIST_MAX_AGE_DAYS = 30                  # 마지막 경기가 바퀴 기준일보다 이만큼 오래면 뺀다
OWNER_DIST_MIN_OWNERS = 100                   # 제외 뒤 이보다 적으면 그 바퀴는 thin(분포로 안 쓴다) · 지표 하나가 이보다 적으면 그 지표만 41단계
OWNER_DIST_MIN_PER_STRATUM = 20               # 층 하나라도 이보다 적으면 thin — 합계만 보면 50+50+0 이 done 이 되어 가중이 조용히 틀어진다
OWNER_DIST_CHIP_P = 10                        # 칩: 내 백분위 ≤ 이 값(하위) 또는 ≥ 100 − 이 값(상위) — 새 표본 검증값(verify_new.CHIP_P)
OWNER_DIST_SURE_P = 50                        # 칩: 내 값 ±1.96se 안쪽 끝의 백분위가 이 값을 안 넘을 때만(verify_new.SURE_P · 2회차)
OWNER_DIST_DAILY_REQ = 200_000                # 하루 요청 상한 — 서비스 키 한도(2,000만)의 1%
OWNER_DIST_ROUND_REQ = 170_000                # 바퀴 요청 상한
OWNER_DIST_PERSON_REQ = 1020                  # 한 명분 최대(ouid 1 + 목록 10 + 상세 1000 + 여유) — 이만큼 남을 때만 시작
OWNER_DIST_429_WAIT_S = 60                    # 429 를 받으면 전원 이만큼 쉬고 한 번 더 → 또 429 면 그날 멈춘다
OWNER_DIST_ROUND_DAYS = 14                    # 직전 종료 뒤 이만큼 지나야 새 바퀴(②·③이 읽는다)
OWNER_DIST_ROUND_MAX_DAYS = 7                 # 시작 뒤 이만큼 안에 못 끝내면 dropped
OWNER_DIST_STALE_DAYS = 45                    # 분포가 이보다 낡으면 41단계 판정으로(②)
OWNER_DIST_FAIL_MAX = 3                       # dropped|aborted 연속 이만큼이면 토글 끔(③)
OWNER_DIST_SELFCHECK_REQ = 5000               # 누적 요청이 이보다 적은데 429 면 개발 키 오신고로 본다(③)
OWNER_DIST_SEED = 20261016                    # + 바퀴 시작일 ordinal + 층 번호
OWNER_DIST_TICK_S = 600                       # 예약이 "받을 차례인가"를 보는 간격(③ — 진행 바퀴가 있거나 새 바퀴 차례면 로더 시작)
OWNER_DIST_HALT_WAIT_MS = 2000                # [수집 기록 지우기] 가 rank.db 를 지우기 전 로더 멈춤을 기다리는 상한(③b — 화면 스레드가 멈춘다)
# 카드 정보 자동 채우기(51단계 · cardcollect.py) — 카드 페이지 + 능력치 시뮬레이터 + 팀컬러 상세를 한 장부(card_backfill)로.
# 근거는 ROADMAP "## 51단계"(F1~F8 · U2) — 상한은 가계부(PRICE_FETCH_MAX)·칩(CHIP_FETCH_MAX)과 따로 센다
CARD_BACKFILL_DAILY_REQ = 600                 # 하루 요청 상한(U2 — 카드 한 장 = 페이지 + 시뮬레이터 2요청)
# 그중 팀컬러 상세 몫 — 2026-10-18 조각 ① 실측: 상세 한 장이 325KB(페이지 109KB · 시뮬레이터 71KB)라, 백필 둘째 날 팀컬러 수백 개가
# 몰리면 하루 내려받기가 ⑦ 상한 100MB 를 넘는다(600 × 325KB ≈ 195MB). 120 이면 39MB + 나머지 480 × 약 90KB ≈ 82MB
CARD_BACKFILL_TC_DAILY_REQ = 120
CARD_BACKFILL_DAYS = 30                       # 수요 = 검색한 구단주의 최근 이만큼 경기 양쪽 선발(G5)
CARD_BACKFILL_TICK_S = 600                    # 예약 간격
# 개발 PC 경기 수집(tools/dev_matches.py — 이 PC 전용 · 배포판은 안 읽는다). 근거는 ROADMAP "개발 PC 수집 확장" 2판
DEV_MATCH_TOP = 10_000                        # 대상 = rank.db 마지막 스냅숏 이 순위까지
DEV_MATCH_CAP_START = 30_000                  # 첫날 하루 요청 상한(단 1 — 1만 명 마지막 경기 한 판씩)
DEV_MATCH_CAP_GROWTH = 1.5                    # 전날이 막힘 없이 제시간에 끝났으면 다음 날 이만큼 곱한다
DEV_MATCH_CAP_MAX = 700_000                   # 끝 상한 — 단 2 약 25만~32만(E2) + 단 3·4 몫 · 초당 38 이면 약 5.1시간(U4 는 80만 — 6시간에 9분 남아 낮춤 · 조각 ② M5)
DEV_MATCH_BACK_PER_PERSON = 100               # 단 4(거슬러 받기) 한 사람 하루 요청 상한 — 앞 순서 몇 명이 남는 상한을 다 먹지 않게
DEV_MATCH_DAY_HOURS = 6                       # 한 번 실행이 스스로 멈추는 시간 · "제시간" 기준
DEV_MATCH_WORKERS = 8                         # 동시에 훑는 사람 수(사람 안에서는 차례로)
DEV_MATCH_PAGE = 100                          # 목록 한 번에 받는 수(넥슨 상한)
DEV_MATCH_429_WAIT_S = 60                     # 429 → 전원 쉬고 한 번 더 → 또 429 면 그날 멈춤(막힘)
DEV_MATCH_FAIL_STREAK = 20                    # 5xx·네트워크가 이만큼 연달아면 그날 멈춤(막힘 아님)
DEV_MATCH_BLOCK_HOLD_DAYS = 3                 # 막힌 날 뒤 이만큼(달력 날) 절반 상한 유지
DEV_MATCH_FLOOR_DAYS = 1                      # 커서 없는 사람은 실행 날 이만큼 전 00:00 KST 보다 옛 경기에서 멈춘다
DEV_MATCH_MISSING_TRIES = 3                   # 상세 실패 경기를 이만큼 다시 받아 보고 포기
CARD_BACKFILL_FIRST_S = 120                   # 앱을 켠 뒤 첫 틱 — 첫 화면(공통 기준 3)에 안 끼게, 10분 안에 닫는 날도 조금은 채우게
CARD_BACKFILL_TICK_REQ = 40                   # 틱 하나의 요청 상한 — 600 을 한 틱에 몰지 않고 앱이 켜진 약 2.5시간에 퍼뜨린다
CARD_BACKFILL_GAP_S = 2                       # 요청 사이(취소로 끊긴다)
CARD_BACKFILL_FORMAT_STOP = 5                 # 서로 다른 대상이 이만큼 이어 형식 실패하면 그날 멈춤(card_backfill_format)
CARD_MY_DAYS = 7                              # 요청 순서 ① — 확정 내 계정의 최근 이만큼 선발 카드 시세를 날마다(C5)
CARD_TRY_MAX = 3                              # 형식 실패가 이만큼이면 그 대상은 CARD_TRY_REST_DAYS 쉰다(card_tries)
CARD_TRY_REST_DAYS = 7
CARD_PRICE_KEEP_DAYS = 70                     # card_price_days 보관 — 출처와 무관(켤 때 CachePruneWorker 가 정리)
C6_PANEL_SHARE = 0.90                         # C6 표본 = 등장의 이 비율을 덮는 상위 카드(F5 기준 654장)
C6_REFRESH_DAYS = 7                           # 표본 카드 시세를 다시 읽는 간격(이상)
C6_GAP_MIN = 7                                # 주간 시세 변화가 쓰는 관측 간격(일) — 앱을 하루 안 켜도 9일
C6_GAP_MAX = 10
C6_MIN_CARDS = 200                            # 최근 7일 간격 조건을 만족한 관측이 이보다 적으면 "모으는 중"
C2_GAP_MAX = 1                                # 팀컬러 상성 — 전력 차 절댓값이 이 안인 경기만
# 경기 기록 spPosition(0~27) → 능력치 시뮬레이터 포지션 칸(16칸 — position_ovrs 의 키). 2026-10-18 조각 ① 실측: 시뮬레이터가 주는 칸은
# ST LW CF RW CAM LM CM RM CDM LWB CB RWB LB SW RB GK 열여섯 — 좌우가 갈린 자리(RCB·LCB 등)는 가운데 칸으로. 28(SUB)은 없다
SIM_POS_OF = {
    0: "GK", 1: "SW", 2: "RWB", 3: "RB", 4: "CB", 5: "CB", 6: "CB", 7: "LB", 8: "LWB",
    9: "CDM", 10: "CDM", 11: "CDM", 12: "RM", 13: "CM", 14: "CM", 15: "CM", 16: "LM",
    17: "CAM", 18: "CAM", 19: "CAM", 20: "CF", 21: "CF", 22: "CF", 23: "RW", 24: "ST", 25: "ST", 26: "ST", 27: "LW",
}
# 11-lite 랭커 기반 추천(17단계 — rankerpick.recommend). 문턱 10 근거: 2026-10-06 스냅숏 1,000위 안에서 10명 이상인
# 팀컬러가 52개 중 23개 · 그 23개가 1,000명 중 938명(94%) — 대부분의 사람은 추천을 받고, 그 밖은 "N명뿐"이 맞다
RANK_TREND_CUTS = (1, 10, 50, 100, 200)  # 랭킹 추이의 컷 그래프(1위 실선 · 나머지 점선) — "상위 구간 200위"까지
RECOMMEND_MIN_RANKERS = 10         # 내 팀컬러 후보가 이보다 적으면 추천 안 함
RECOMMEND_MIN_USERS = 3             # 대체 카드는 후보 중 이만큼 이상이 쓴 것만(한두 명의 취향을 추천으로 내지 않게)
RECOMMEND_FETCH_MAX = 20            # ③ 모자랄 때 1,000위 안에서 더 받는 랭커 — 최대 60요청(하루 상한 안)
RECOMMEND_OPPONENT_MAX = 200        # ② 내 DB 의 상대 후보 상한(순위 순) — 경기 본문을 읽어 그리기 예산(0.3초) 안에
# 특성 몫(32단계 U7) — 하루 RANKER_PICK_DAILY_REQ 에서 1~200 다시 확인(3일마다 67명 × 목록·상세 2 = 134)과 추천 ③(20명 × 3 = 60)을
# 먼저 떼고 남은 것(106)만 특성 201~500 이 쓴다 — 특성 탭을 먼저 연 날 랭커 픽·[추천] 이 상한에 걸리지 않게(검토 B)
TRAIT_API_SHARE = RANKER_PICK_DAILY_REQ - 2 * -(-RANKER_PICK_TOP // RANKER_PICK_STALE_DAYS) - 3 * RECOMMEND_FETCH_MAX

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


def install_id() -> str:
    """서버 모드의 설치 ID — .env 에 있으면 그것, 없으면 무작위로 만들어 적는다. 서버 주소가 없으면 ""(만들지 않는다).
    이름·키와 무관한 값이고, 상한이 아니라 문제 추적용이다(아무나 새로 만들 수 있다)."""
    if not SERVER_URL:
        return ""
    cur = os.getenv(INSTALL_ID_VAR, "").strip()
    if cur:
        return cur
    import uuid
    new = uuid.uuid4().hex
    try:
        _save_env(INSTALL_ID_VAR, new)
    except OSError:
        os.environ[INSTALL_ID_VAR] = new  # 못 적어도 이번 실행은 같은 값으로
    return new


def save_api_key(key: str) -> None:
    """키를 .env 에 쓰고 이 프로세스의 API_KEY 도 바꾼다.

    API_KEY 를 재할당하므로 다른 모듈은 `from config import API_KEY` 가 아니라
    `config.API_KEY` 로 읽어야 한다.
    """
    global API_KEY
    key = key.strip()
    if key != API_KEY and OWNER_DIST_ON:
        # 구단주 분포 토글은 "이 키가 서비스 단계"라는 자가 신고다 — 키가 바뀌면 신고도 무효(끄기를 먼저: 못 끄면 키도 안 바꾼다)
        set_owner_dist(False, "key")
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
    if not on and CARD_BACKFILL_ON:
        set_card_backfill(False)   # 같은 이유 — 카드 정보 자동 채우기도 따로 다시 켜게(51단계 5판)


def set_rank_collect(on: bool) -> None:
    """랭킹 수집을 켜고 끈다 — 저장하고 이 프로세스에도 바로 반영. 켤 때마다 켠 시각을 새로 적는다(read_rank_on_at)."""
    global RANK_COLLECT
    if on:
        _save_env(RANK_COLLECT_ON_AT_VAR, datetime.now().isoformat(timespec="microseconds"))
    _save_env(RANK_COLLECT_VAR, "1" if on else "0")
    RANK_COLLECT = on


def set_owner_dist(on: bool, reason: str = "") -> None:
    """구단주 분포 토글 — 켜면 지금 시각(개발 키 오신고·연속 실패 판정의 시작점), 끄면 빈 값과 사유(devkey|fail|key · 사용자가 끄면 "")."""
    global OWNER_DIST_ON, OWNER_DIST_OFF
    at = datetime.now().isoformat(timespec="seconds") if on else ""
    why = "" if on else reason
    _save_env(OWNER_DIST_VAR, at)
    _save_env(OWNER_DIST_OFF_VAR, why)
    OWNER_DIST_ON, OWNER_DIST_OFF = at, why


def set_card_backfill(on: bool, reason: str = "") -> None:
    """카드 정보 자동 채우기 토글 — 켜면 지금 시각(막힘이 이어진 날은 이 뒤 날만 센다), 끄면 빈 값과 사유(blocked · 사용자가 끄면 "")."""
    global CARD_BACKFILL_ON, CARD_BACKFILL_OFF
    at = datetime.now().isoformat(timespec="seconds") if on else ""
    why = "" if on else reason
    _save_env(CARD_BACKFILL_VAR, at)
    _save_env(CARD_BACKFILL_OFF_VAR, why)
    CARD_BACKFILL_ON, CARD_BACKFILL_OFF = at, why


def _read_env_disk() -> dict | None:
    try:
        return dotenv_values(ENV_PATH, encoding="utf-8-sig") if ENV_PATH.exists() else {}
    except (OSError, UnicodeError, ValueError):
        return None


def read_rank_on_at() -> str | None:
    """수집을 마지막으로 켠 시각(.env 를 디스크에서 다시) — 없으면 None(옛 버전에서 켠 사람). 못 읽으면 None."""
    vals = _read_env_disk()
    return ((vals or {}).get(RANK_COLLECT_ON_AT_VAR) or "").strip() or None


def _read_collect_gate(version: int) -> bool:
    """디스크의 .env 로 다시: 웹 데이터 · 수집 켜짐 · 안내 version 이상 동의. 못 읽으면 False(쓰지 않는 쪽으로)."""
    vals = _read_env_disk()
    if vals is None:
        return False
    try:
        accepted = int((vals.get(NOTICE_VAR) or "0").strip() or 0)
    except ValueError:
        accepted = 0
    return ((vals.get(WEB_DATA_VAR) or "0").strip() == "1" and (vals.get(RANK_COLLECT_VAR) or "0").strip() == "1"
            and accepted >= version)


def read_person_allowed() -> bool:
    """사람별 표에 써도 되나 — 디스크의 .env 로 다시(다른 실행본·손 수정): 웹 데이터 · 수집 · 안내 META_NOTICE_VERSION 동의.
    못 읽으면 False(쓰지 않는 쪽으로)."""
    return _read_collect_gate(META_NOTICE_VERSION)


def read_opp_rank_allowed() -> bool:
    """상대 순위 구간(N23 match_opp_rank)을 채워도 되나 — read_person_allowed 와 같되 안내는 OPP_RANK_NOTICE_VERSION.
    사람별 관문(6)만 넘은 옛 동의자는 새 안내(9)를 보기 전까지 쌓이지 않는다."""
    return _read_collect_gate(OPP_RANK_NOTICE_VERSION)


def read_fc_games_allowed() -> bool:
    """사람별 챔스 이상 판수(57 ⓪ fc_games)를 쌓아도 되나 — 안내 FC_GAMES_NOTICE_VERSION. 사람별 관문(6)만 넘은 옛 동의자는
    새 안내(13)를 보기 전까지 쌓이지 않는다(read_opp_rank_allowed 와 같은 까닭)."""
    return _read_collect_gate(FC_GAMES_NOTICE_VERSION)


def read_env_switches() -> tuple[bool, bool]:
    """.env 를 디스크에서 다시 읽어 (웹 데이터, 랭킹 수집) — 다른 실행본이 바꿨을 수 있다(D6 로 껐다든지).

    load_dotenv 는 이미 든 환경 변수를 덮지 않아서 못 쓴다. 읽은 값으로 이 프로세스의 전역도 맞춘다.
    """
    global WEB_DATA, RANK_COLLECT
    vals = _read_env_disk()
    if vals is None:
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
    """옛 동의(기본 범위는 계속 돎) + 새 안내를 아직 안 봄 — 창을 사용자에게 보일 때 한 번 다시 묻는다.
    남은 바뀐 점이 수집 쪽뿐이면(NOTICE_COLLECT_ONLY_FROM 이상) 수집을 켠 사람에게만 — 통째로 RANK_COLLECT 를 붙이면
    수집을 끈 5 이하 동의자가 시세·칩 자동 읽기(4·5)를 영영 못 받는다(32단계 검토 A [상])."""
    if not NOTICE_BASE_VERSION <= NOTICE_ACCEPTED < NOTICE_VERSION:
        return False
    return RANK_COLLECT or NOTICE_ACCEPTED < NOTICE_COLLECT_ONLY_FROM


def track_allowed() -> bool:
    """따라가기 계정의 하루 ELO 기록 — 그 안내(TRACK_NOTICE_VERSION)에 동의한 뒤부터."""
    return NOTICE_ACCEPTED >= TRACK_NOTICE_VERSION


def price_auto_allowed() -> bool:
    """가계부 평가용 시세를 자동으로 읽어도 되나 — 그 안내(PRICE_NOTICE_VERSION) 동의 + 웹 데이터 켜짐."""
    return WEB_DATA and NOTICE_ACCEPTED >= PRICE_NOTICE_VERSION


def chip_auto_allowed() -> bool:
    """축구장 칩의 카드 정보를 자동으로 읽어도 되나 — 그 안내(CHIP_NOTICE_VERSION) 동의 + 웹 데이터 켜짐."""
    return WEB_DATA and NOTICE_ACCEPTED >= CHIP_NOTICE_VERSION


def meta_person_allowed() -> bool:
    """사람별 표(상위 20 연속·최고점·첫 챔스·시즌 FC)에 써도 되나 — 이 프로세스 값으로. 쓰기 직전엔 read_person_allowed 로 다시."""
    return (not notice_needed() and WEB_DATA and RANK_COLLECT and NOTICE_ACCEPTED >= META_NOTICE_VERSION)


def fc_games_view_allowed() -> bool:
    """57 K11 보상판(fc_games 를 읽는다)을 보여도 되나 — 이 프로세스 값으로(meta_person_allowed 꼴). 쓰기는 read_fc_games_allowed."""
    return (not notice_needed() and WEB_DATA and RANK_COLLECT
            and NOTICE_ACCEPTED >= max(META_NOTICE_VERSION, FC_GAMES_NOTICE_VERSION))


def ranker_pick_allowed() -> bool:
    """랭커 픽을 받아도 되나 — 랭킹 수집이 켜져 있고(웹 데이터·처음 동의 포함) 그 안내 버전에 동의했을 때(U1).
    수집 토글 하나로 묶었다 — 켜는 사람이 이미 "다른 구단주 데이터를 모은다"에 동의했다."""
    return (not notice_needed() and WEB_DATA and RANK_COLLECT
            and NOTICE_ACCEPTED >= RANKER_PICK_NOTICE_VERSION)


def opp_score_allowed() -> bool:
    """57 K3 경기 목록에 상대 점수를 보여도 되나 — N23 과 같은 관문(수집 켜짐 + 안내 9) + 안내 14. 내 계정 경기만인지는 EloLoader 가 본다."""
    return (not notice_needed() and WEB_DATA and RANK_COLLECT
            and NOTICE_ACCEPTED >= max(OPP_RANK_NOTICE_VERSION, OPP_SCORE_NOTICE_VERSION))


def trait_allowed() -> bool:
    """포지션 특성을 받아도 되나 — 랭커 픽 조건(수집 켜짐 포함) + 특성 안내(TRAIT_NOTICE_VERSION) 동의."""
    return ranker_pick_allowed() and NOTICE_ACCEPTED >= TRAIT_NOTICE_VERSION


def my_trait_allowed() -> bool:
    """57 K12 내 스쿼드 특성(내 닉네임을 스쿼드메이커에 보낸다)을 받아도 되나 — 특성 관문 + 안내 14. 지우는 조건은 이것의 부정
    (MainWindow.purge_ranker_pick_data — 관문이 어긋나면 지우고 다시 받기를 되풀이한다 · 1회차 A[상]4)."""
    return trait_allowed() and NOTICE_ACCEPTED >= MY_TRAIT_NOTICE_VERSION


def predict_check_allowed() -> bool:
    """예측 되짚기(B8)로 넥슨 웹에 물어도 되나 — 디스크의 .env 로 다시(트레이에서 새 안내를 안 본 사람에게 안 나가게)."""
    return _read_collect_gate(PREDICT_CHECK_NOTICE_VERSION)


def owner_dist_v2_allowed() -> bool:
    """새 구단주 분포 바퀴에 자리 재료(pos_json)까지 계산해도 되나 — 디스크의 .env 로(트레이에서 새 안내를 안 본 사람은 v2 NULL)."""
    return _read_collect_gate(OWNER_DIST_V2_NOTICE_VERSION)


def owner_dist_prereq() -> bool:
    """구단주 분포의 전제(이 프로세스 값) — 처음 동의 · 웹 데이터 · 랭킹 수집 · 안내 OWNER_DIST_NOTICE_VERSION 동의.
    전제가 꺼지면 받은 분포까지 지운다 — 토글만 끈 것(분포는 남김)과 다르다."""
    return (not notice_needed() and WEB_DATA and RANK_COLLECT
            and NOTICE_ACCEPTED >= OWNER_DIST_NOTICE_VERSION)


def owner_dist_allowed() -> bool:
    """구단주 분포를 받아도 되나 — 전제 + 토글 + 키(이 프로세스 값). 예약은 매 틱 read_owner_dist 로 디스크도 다시 본다."""
    return owner_dist_prereq() and bool(OWNER_DIST_ON) and bool(API_KEY)


def read_owner_dist() -> tuple[bool, bool] | None:
    """.env 를 디스크에서 다시 읽어 구단주 분포의 (전제, 토글·키) — 다른 실행본·손 수정. 토글·사유 전역도 맞춘다.
    전제·토글은 이 프로세스 값과 **둘 다** 켜져야 켜짐이다. 못 읽으면 None — 모르는 상태라 아무것도 하지 않는다
    (False 로 돌려주면 .env 를 잠깐 못 읽은 것만으로 받은 분포를 지운다)."""
    global OWNER_DIST_ON, OWNER_DIST_OFF
    vals = _read_env_disk()
    if vals is None:
        return None
    OWNER_DIST_ON = (vals.get(OWNER_DIST_VAR) or "").strip()
    OWNER_DIST_OFF = (vals.get(OWNER_DIST_OFF_VAR) or "").strip()
    try:
        accepted = int((vals.get(NOTICE_VAR) or "0").strip() or 0)
    except ValueError:
        accepted = 0
    disk = ((vals.get(WEB_DATA_VAR) or "0").strip() == "1" and (vals.get(RANK_COLLECT_VAR) or "0").strip() == "1"
            and accepted >= OWNER_DIST_NOTICE_VERSION)
    return disk and owner_dist_prereq(), bool(OWNER_DIST_ON) and bool(API_KEY)


def card_backfill_allowed() -> bool:
    """카드 정보 자동 채우기를 해도 되나(이 프로세스 값) — 처음 동의 · 웹 데이터 · 안내 CARD_BACKFILL_NOTICE_VERSION · 토글.
    랭킹 수집은 안 본다(U5 — 카드 정보는 사람과 이어지지 않는다). 예약은 매 틱 read_card_backfill 로 디스크도 다시 본다."""
    return (not notice_needed() and WEB_DATA and NOTICE_ACCEPTED >= CARD_BACKFILL_NOTICE_VERSION
            and bool(CARD_BACKFILL_ON))


def read_card_backfill() -> bool | None:
    """.env 를 디스크에서 다시 읽어 카드 정보 자동 채우기가 켜졌나 — 다른 실행본·손 수정. 토글·사유 전역도 맞춘다.
    디스크와 이 프로세스 값이 **둘 다** 허락해야 참이다. 못 읽으면 None — 그 틱은 아무것도 안 한다(켜지도 끄지도 않음)."""
    global CARD_BACKFILL_ON, CARD_BACKFILL_OFF
    vals = _read_env_disk()
    if vals is None:
        return None
    CARD_BACKFILL_ON = (vals.get(CARD_BACKFILL_VAR) or "").strip()
    CARD_BACKFILL_OFF = (vals.get(CARD_BACKFILL_OFF_VAR) or "").strip()
    try:
        accepted = int((vals.get(NOTICE_VAR) or "0").strip() or 0)
    except ValueError:
        accepted = 0
    disk = (vals.get(WEB_DATA_VAR) or "0").strip() == "1" and accepted >= CARD_BACKFILL_NOTICE_VERSION
    return disk and card_backfill_allowed()

# ── 표 칸 그림(40단계) ───────────────────────────────────────────────────
# 크기·비율은 여기 한 곳. ICON_PX 는 표 칸 그림의 높이, 폭은 종류별 가로세로 비율로 정한다.
ICON_PX = 20
TRAIT_ICON_PX = 28
FORMATION_PX = 20
# 종류별 가로/세로 — 표가 비어 있어 그림 폭을 못 잴 때(등록 시점) 열 폭을 먼저 잡는 데 쓴다.
# 2026-10-11 실측: 엠블럼·강화 엠블럼 128×128, 국기 130×78(5:3), 특성 90×90. 시즌·등급 배지는 정사각으로 둔다.
# "emblem" 열에는 구단 엠블럼·강화 엠블럼(정사각)과 국기(5:3)가 같이 올 수 있어 가장 넓은 쪽(국기)으로 폭을 잡는다.
ICON_ASPECT = {"emblem": 130 / 78, "season": 1.0, "division": 1.0, "trait": 1.0, "train": 1.0,
               "formation": 1.0}
# 종류별 그림 높이 — 없으면 ICON_PX. 특성은 판(육각) 위에 아이콘 + 아래 % 줄이라 더 크게(STACKED_KINDS: 글자 위에 쌓아 그리는 칸)
ICON_HEIGHT = {"trait": TRAIT_ICON_PX, "train": TRAIT_ICON_PX, "formation": FORMATION_PX}
STACKED_KINDS = ("trait", "train")
EMBLEM_HEAD_PX = 32      # 대시보드 머리(구단주 카드) 엠블럼 높이 — 옆 등급 배지(32)와 같다
FORMATION_HEAD_PX = 36   # 같은 머리의 주 포메이션 축구장(formation_pixmap 에 넘기는 높이)
ICON_CACHE_SUBDIR = "icons"

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
