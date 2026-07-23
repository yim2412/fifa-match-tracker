"""피파 전적관리 — PyQt6 앱.

첫 화면은 검색창 하나. 구단주명을 넣으면 랭커 카드 + 분석 탭으로 전환된다.
"""
from __future__ import annotations

import sys
from collections import Counter
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

import config
import images
import playerinfo
import ranker
import stats as st
import store
import theme as T
from models import (
    MatchSummary, current_streak, longest_streaks, opponent_stats, parse_match,
    period_stats, summarize, win_rate_trend,
)
from nexon_api import FCOnlineAPI, NexonAPIError
from widgets import (
    NA, BarRow, DivisionChart, FitTableWidget, NoScrollComboBox, PitchWidget,
    RankerCard, RowBorderDelegate, ShotMapWidget, SortableItem, StatCard,
    TrendChart, rate_of, wdl_text,
)

PAGE_SIZE = config.MAX_MATCH_LIMIT  # API 가 한 번에 주는 최대치(100)


class MatchLoader(QThread):
    """API 호출은 전부 여기서 — UI 스레드가 멈추지 않게."""

    progress = pyqtSignal(int, int, str)
    # [MatchSummary], [원본 detail], ouid, basic, spId→이름, 포지션코드→이름,
    # 새로 저장된 수, 이번에 API 로 받은 수, RankerInfo|None(넥슨 데이터센터 랭킹),
    # 등급이름(감독모드 최고 등급), is_champion(챔피언스 이상인지), 등급 배지 로컬 경로,
    # seasonId→{className,seasonImg}, divisionId→등급이름(등급 추이 그래프용)
    finished_ok = pyqtSignal(list, list, str, dict, dict, dict, int, int, object,
                             str, bool, str, dict, dict)
    failed = pyqtSignal(str)

    def __init__(self, api: FCOnlineAPI, nickname: str, match_type: int):
        super().__init__()
        self._api = api
        self._nickname = nickname
        self._match_type = match_type
        self._cancel = False
        self._pool: ThreadPoolExecutor | None = None

    def cancel(self) -> None:
        """즉시 취소 — 대기 중인 상세 요청을 버리고 진행 중인 것만 끝낸다.

        pool 을 그냥 두면 제출된 수천 건이 다 끝날 때까지 기다려서, 창을
        닫아도 워커가 안 죽고 프로세스가 좀비로 남았다(onefile exe 라 부트로더
        까지 함께 남는다).
        """
        self._cancel = True
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _new_match_ids(self, ouid: str, known) -> list[str]:
        """새로 저장할 매치 id 를 모은다.

        API 는 한 번에 최대 100개만 준다. 최신순으로 오므로, 한 페이지가
        전부 이미 DB 에 있으면 그 뒤는 볼 필요가 없다 — 새 경기는 늘 맨 앞이다.
        이 덕에 이미 받아 둔 계정은 3천 개를 다시 훑지 않고 첫 페이지에서 끝난다
        (12초 → 0.4초). 처음 보는 계정만 전량을 받는다.
        """
        ids: list[str] = []
        offset = 0
        while not self._cancel:
            chunk = self._api.get_match_ids(ouid, self._match_type,
                                            offset, PAGE_SIZE)
            if not chunk:
                break
            ids.extend(chunk)
            self.progress.emit(0, 0, f"경기 목록 확인 중… {len(ids)}경기")
            if all(i in known for i in chunk):  # 이 페이지가 전부 이미 있음
                break
            if len(chunk) < PAGE_SIZE:           # 마지막 페이지
                break
            offset += PAGE_SIZE
        return ids

    def run(self) -> None:
        try:
            self.progress.emit(0, 0, f"'{self._nickname}' 계정 조회 중…")
            ouid = self._api.get_ouid(self._nickname)
            basic = self._api.get_user_basic(ouid)

            conn = store.open_db(config.DB_PATH)  # DB 는 스레드마다 따로 연다
            try:
                store.upsert_account(conn, ouid, basic.get("nickname") or self._nickname)

                # 이미 가진 경기는 목록 확인도, 상세 조회도 다시 하지 않는다.
                known = store.known_ids(conn, ouid, self._match_type)
                ids = self._new_match_ids(ouid, known)
                if self._cancel:
                    return
                got = len(ids)
                todo = [i for i in ids if i not in known]

                fresh: list[dict] = []
                done = 0
                if todo:
                    self._pool = ThreadPoolExecutor(max_workers=6)
                    try:
                        for detail in self._pool.map(self._safe_detail, todo):
                            if self._cancel:
                                return
                            done += 1
                            self.progress.emit(done, len(todo),
                                               f"새 경기 받는 중… {done}/{len(todo)}")
                            if detail is not None:
                                fresh.append(detail)
                    except (RuntimeError, CancelledError):
                        return  # cancel() 이 pool 을 내려 map 이 끊긴 경우
                    finally:
                        self._pool.shutdown(wait=False, cancel_futures=True)
                        self._pool = None
                new = store.save_matches(conn, fresh)

                self.progress.emit(0, 0, "저장된 전적 불러오는 중…")
                details = store.load_details(conn, ouid, self._match_type)
            finally:
                conn.close()

            # 넥슨 데이터센터의 감독모드 랭킹(순위·구단가치·ELO). 오픈API 엔
            # 없는 값이라 여기서 받는다. 실패해도 전적 조회는 살린다.
            rank = self._safe_rank()
            grade_name, is_champion, badge_path, division_names = \
                self._current_grade(details, ouid)

            if not details:
                self.finished_ok.emit([], [], ouid, basic, {}, {}, 0, got,
                                      rank, grade_name, is_champion, badge_path,
                                      {}, division_names)
                return

            matches = [m for m in (parse_match(d, ouid) for d in details) if m]
            matches.sort(key=lambda m: m.match_date or 0, reverse=True)

            self.progress.emit(0, 0, "선수 정보 조회 중…")
            names = self._safe_meta("spid", "id", "name")
            positions = self._safe_meta("spposition", "spposition", "desc")
            seasons = self._safe_meta_raw("seasonid", "seasonId")

            self.finished_ok.emit(matches, details, ouid, basic, names,
                                  positions, new, got, rank, grade_name,
                                  is_champion, badge_path, seasons, division_names)

        except NexonAPIError as e:
            self.failed.emit(e.message)
        except Exception as e:
            self.failed.emit(f"예기치 못한 오류: {e}")

    def _safe_detail(self, match_id: str):
        """한 경기가 실패해도 전체 조회를 죽이지 않는다."""
        if self._cancel:
            return None
        try:
            return self._api.get_match_detail(match_id)
        except NexonAPIError:
            return None

    def _safe_meta(self, name: str, key: str, val: str) -> dict:
        """메타를 못 받아도 전적은 보여준다 — 이름 대신 코드가 뜰 뿐."""
        try:
            return {m[key]: m[val] for m in self._api.get_meta(name)
                    if key in m and val in m}
        except Exception:
            return {}

    def _safe_meta_raw(self, name: str, key: str) -> dict:
        """_safe_meta 와 달리 항목 전체(dict)를 key 로 묶어 돌려준다 —
        seasonid 처럼 여러 필드(className·seasonImg)가 다 필요할 때."""
        try:
            return {m[key]: m for m in self._api.get_meta(name) if key in m}
        except Exception:
            return {}

    def _current_grade(self, details: list[dict],
                       ouid: str) -> tuple[str, bool, str, dict]:
        """'지금' 등급 이름·챔피언스 이상 여부·등급 배지 아이콘 로컬 경로.

        오픈API user/maxdivision 은 '역대 최고' 등급이라 지금 등급과 다를 수
        있다(예: 예전에 슈퍼챔피언스를 찍었지만 지금은 챔피언스로 내려온 경우).
        대신 매치 상세에 그 경기 당시의 division 필드가 있으므로, 이미 받아
        둔 경기 중 가장 최근 것(details[0], store.load_details 가 최신순으로
        준다)의 값을 쓴다 — 우리가 실제로 확인한 최신 상태에 가장 가깝다.
        """
        raw = []
        try:
            raw = self._api.get_meta("division")
        except NexonAPIError:
            pass
        names = {d.get("divisionId"): d.get("divisionName") for d in raw
                if "divisionId" in d and "divisionName" in d}

        if not details:
            return "-", False, "", names
        me = next((p for p in details[0].get("matchInfo") or []
                  if p.get("ouid") == ouid), None)
        div_id = me.get("division") if me else None
        if div_id is None:
            return "-", False, "", names
        grade_name = names.get(div_id, str(div_id))

        # division.json 은 배열 순서 그대로가 등급 배지 CDN 번호다(0=슈퍼
        # 챔피언스 … 17=프로3, 실제 응답으로 확인). 실패해도 이름·랭커
        # 여부는 살린다 — 배지는 있으면 좋은 장식이다.
        badge_path = ""
        idx = next((i for i, d in enumerate(raw)
                   if d.get("divisionId") == div_id), None)
        if idx is not None:
            path = images.fetch_division_icon(idx, config.CACHE_DIR / "division_icons")
            if path:
                badge_path = str(path)
        return grade_name, st.is_champion_or_above(div_id), badge_path, names

    def _safe_rank(self):
        """랭킹(데이터센터 스크래핑)이 깨져도 전적은 보여준다."""
        if self._cancel:
            return None
        try:
            return ranker.fetch_manager_rank(self._nickname)
        except ranker.RankerError:
            return None


class ImageLoader(QThread):
    """선수 지표 표에 쓸 얼굴 이미지를 백그라운드로 받는다.

    UI 스레드에서 네트워크 대기를 하면 표를 그린 직후 창이 잠깐 얼어서,
    받는 족족 loaded 시그널로 하나씩 넘긴다 — 표는 이미지 없이 먼저 뜨고
    받아지는 대로 채워진다."""

    loaded = pyqtSignal(int, str)  # spId, 로컬 파일 경로

    def __init__(self, sp_ids: list[int], cache_dir):
        super().__init__()
        self._sp_ids = sp_ids
        self._cache_dir = cache_dir
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        for sp_id in self._sp_ids:
            if self._cancel:
                return
            path = images.fetch(sp_id, self._cache_dir)
            if path and not self._cancel:
                self.loaded.emit(sp_id, str(path))


class SeasonIconLoader(QThread):
    """스쿼드 카드에 쓸 시즌(카드 클래스) 아이콘을 백그라운드로 받는다.

    같은 시즌 선수가 여러 명이면 한 번만 받는다(entries 안에서 URL 이 같으면
    재사용) — 시즌은 최대 10여 종류라 스쿼드 하나에 중복이 흔하다."""

    loaded = pyqtSignal(int, str)  # spId, 로컬 파일 경로

    def __init__(self, entries: list[tuple[int, int, str]], cache_dir):
        """entries: (spId, seasonId, seasonImg URL) 튜플 리스트."""
        super().__init__()
        self._entries = entries
        self._cache_dir = cache_dir
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        cache: dict[int, object] = {}
        for sp_id, season_id, icon_url in self._entries:
            if self._cancel:
                return
            if season_id not in cache:
                cache[season_id] = images.fetch_season_icon(
                    season_id, icon_url, self._cache_dir)
            path = cache[season_id]
            if path and not self._cancel:
                self.loaded.emit(sp_id, str(path))


class PlayerInfoLoader(QThread):
    """선수 카드 상세(playerinfo.fetch_player_info)를 백그라운드로 받는다.

    스쿼드 화면에서 선수를 클릭할 때마다 하나씩 조회하는 일회성 요청이라
    (팀컬러처럼 수백 건을 한 번에 훑지 않는다) 풀 없이 스레드 하나로 충분하다."""

    loaded = pyqtSignal(object)   # playerinfo.PlayerInfo
    failed = pyqtSignal(str)

    def __init__(self, sp_id: int):
        super().__init__()
        self._sp_id = sp_id

    def run(self) -> None:
        try:
            info = playerinfo.fetch_player_info(self._sp_id)
            self.loaded.emit(info)
        except playerinfo.PlayerInfoError as e:
            self.failed.emit(str(e))


class AbilitySimLoader(QThread):
    """능력치 시뮬레이터(playerinfo.fetch_player_ability)를 백그라운드로 받는다.

    강화·팀컬러 콤보박스를 바꿀 때마다 PC 데이터센터에 새로 물어봐야 해서
    (서버가 직접 계산 — 로컬 근사 없음) 조작할 때마다 새로 띄운다."""

    loaded = pyqtSignal(object)   # playerinfo.AbilitySim
    failed = pyqtSignal(str)

    def __init__(self, sp_id: int, strong: int, adapt: int,
                 teamcolor_id: int, teamcolor_lv: int,
                 teamcolor_id_enhance: int, teamcolor_lv_enhance: int,
                 teamcolor_id_feature: int):
        super().__init__()
        self._args = (sp_id, strong, adapt, teamcolor_id, teamcolor_lv,
                      teamcolor_id_enhance, teamcolor_lv_enhance, teamcolor_id_feature)

    def run(self) -> None:
        try:
            sim = playerinfo.fetch_player_ability(
                self._args[0], strong=self._args[1], adapt=self._args[2],
                teamcolor_id=self._args[3], teamcolor_lv=self._args[4],
                teamcolor_id_enhance=self._args[5], teamcolor_lv_enhance=self._args[6],
                teamcolor_id_feature=self._args[7])
            self.loaded.emit(sim)
        except playerinfo.PlayerInfoError as e:
            self.failed.emit(str(e))


class UrlImageLoader(QThread):
    """선수 카드 다이얼로그의 사진·국기·특성 아이콘처럼, spId 같은 고정 키가
    없는 잡다한 URL들을 images.fetch_url 로 받는다."""

    loaded = pyqtSignal(str, str)  # url, 로컬 파일 경로

    def __init__(self, urls: list[str], cache_dir):
        super().__init__()
        self._urls = urls
        self._cache_dir = cache_dir
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        for url in self._urls:
            if self._cancel:
                return
            path = images.fetch_url(url, self._cache_dir)
            if path and not self._cancel:
                self.loaded.emit(url, str(path))


class TeamColorLoader(QThread):
    """상대 닉네임별 팀컬러를 백그라운드로 조회한다(넥슨 데이터센터 스크래핑,
    ranker.fetch_manager_rank 재사용).

    top 10,000 감독모드 랭커 밖이면 팀컬러가 빈 문자열로 온다 — 그 상대는
    통계에서 자연히 빠진다. MatchLoader 가 매치 상세를 받을 때와 같은 이유로
    (닉네임이 많으면 순차 요청은 너무 느림) ThreadPoolExecutor 로 몇 개씩
    동시에 돈다 — 다만 이건 공식 API 가 아니라 스크래핑이라 매치 상세(6)보다
    약간 많은 정도로만 예의를 지킨다. ranker._session 이 연결을 재사용해서
    스레드 수를 늘려도 서버가 받는 연결 자체는 늘 새로 여는 것보다 적다.

    nicknames 는 호출부(app_main._on_fetch_team_colors)에서 이미 "많이 만난
    상대 먼저" 순으로 정렬해서 넘겨준다 — ThreadPoolExecutor.map 은 제출
    순서대로 작업을 집어가므로, 정말 다 받기 전에 취소해도(또는 화면을
    먼저 봐도) 값어치 큰 상대부터 채워진다.

    TIMEOUT 을 짧게 잡은 이유: 실측 정상 응답이 평균 0.2초대라 5초면 이미
    넉넉하고, 응답 없는 상대 하나가 스레드를 오래 붙잡아 나머지를 늦추는
    걸 막는다."""

    MAX_WORKERS = 8
    TIMEOUT = 5

    progress = pyqtSignal(int, int)   # done, total
    # nickname, team_color("" 이면 못 찾음), 구단가치(원 단위 int, 못 찾으면 None)
    loaded = pyqtSignal(str, str, object)
    finished_all = pyqtSignal()

    def __init__(self, nicknames: list[str]):
        super().__init__()
        self._nicknames = nicknames
        self._cancel = False
        self._pool: ThreadPoolExecutor | None = None

    def cancel(self) -> None:
        self._cancel = True
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _fetch_one(self, nick: str) -> tuple[str, str, int | None] | None:
        try:
            info = ranker.fetch_manager_rank(nick, timeout=self.TIMEOUT)
            # 팀가치는 랭킹에 잡힌 상대만 의미 있다 — 못 찾으면 None
            return nick, info.team_color, (info.team_value if info.team_color else None)
        except ranker.RankerError:
            # 조회 실패(넥슨 웹 점검·타임아웃 등)는 "랭킹 밖"("")과 다르다 —
            # emit 하지 않아 캐시에 안 남고, 다음 조회 때 다시 시도된다.
            return None

    def run(self) -> None:
        total = len(self._nicknames)
        done = 0
        self._pool = ThreadPoolExecutor(max_workers=self.MAX_WORKERS)
        try:
            for result in self._pool.map(self._fetch_one, self._nicknames):
                if self._cancel:
                    return
                if result is not None:
                    self.loaded.emit(*result)
                done += 1
                self.progress.emit(done, total)
        except (RuntimeError, CancelledError):
            return  # cancel() 이 pool 을 내려 map 이 끊긴 경우
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if not self._cancel:
            self.finished_all.emit()


class MainWindow(QMainWindow):
    MATCH_COLUMNS = ["일시", "결과", "스코어", "상대", "점유율", "슈팅", "유효",
                     "패스성공률", "평점"]
    PLAYER_COLUMNS = ["포지션", "선수", "강화", "출전", "승률",
                      "공격력", "수비력", "기대득점률", "공격P", "골", "어시",
                      "패스%", "드리블%", "공중볼%", "가로채기", "태클%",
                      "블록%", "선방력", "평점"]
    # 선수별 결정력 랭킹 — shootDetail(슛 좌표)만으로 낸 값. xG는 비공식 근사치.
    FINISHING_COLUMNS = ["선수", "슛", "유효슛", "골", "전환율", "xG", "골−xG", "어시스트"]
    # 각 열 헤더에 마우스를 올렸을 때 보여줄 설명 — stats.py 의 계산식 주석을 그대로 옮김.
    # 공격력/수비력/기대득점률/가로채기/선방력은 오픈API가 안 주는 값이라 fc-info
    # 프론트엔드에서 역산한 파생 지표라, 이름만 보고는 계산 기준이 안 보여서 필요하다.
    PLAYER_COLUMN_HELP = {
        "포지션": "이 선수가 가장 많이 선 자리(출전 빈도 기준).",
        "강화": "이 선수의 여러 경기 중 가장 높았던 강화 단계.",
        "출전": "이 계정으로 이 선수가 실제로 뛴 경기 수(교체 투입 포함).",
        "승률": "이 선수가 출전한 경기만 기준으로 한 승률.",
        "공격력": "10×기대득점률 + 패스% + 드리블% + 5×(승률/출전)\n"
                 "+ 필드 플레이어면 공중볼% — fc-info 산식을 그대로 역산해 옮김.",
        "수비력": "패스% + 가로채기 + 태클% + 2×선방력 + 블록% + 5×(승률/출전)\n"
                 "+ 필드 플레이어면 공중볼% — fc-info 산식을 그대로 역산해 옮김.",
        "기대득점률": "경기당 평균 (골+어시) × 100.\n"
                    "이름과 달리 유효슛 대비 득점률이 아니라 fc-info 정의를 그대로 따름.",
        "공격P": "골 + 어시 합계(공격 포인트).",
        "가로채기": "경기당 가로채기 평균 × 100(누적 합계가 아님).",
        "선방력": "defending 스탯의 경기당 평균 × 100.\n"
                 "GK는 실제 선방, 필드 플레이어는 수비 기여도로 볼 수 있음.",
        "평점": "이 선수가 출전한 경기들의 평균 평점.",
    }
    OPPONENT_COLUMNS = ["상대", "전적", "승률", "평균득점", "평균실점", "최근 경기"]
    POSITION_OPP_COLUMNS = ["포지션", "선수", "만난 횟수", "비율"]
    TEAMCOLOR_RATE_COLUMNS = ["팀컬러", "경기", "승", "무", "패", "승률"]
    TEAMCOLOR_RANK_COLUMNS = ["순위", "팀컬러", "만난 횟수",
                              "평균 팀가치", "최저 팀가치", "최고 팀가치"]

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api
        self._loader: MatchLoader | None = None
        self._img_cache_dir = config.CACHE_DIR / "player_images"
        self._table_season_loader: SeasonIconLoader | None = None
        self._finishing_icon_loader: SeasonIconLoader | None = None
        self._ouid = ""
        self._nick = ""
        self._basic: dict = {}
        self._rank = None   # ranker.RankerInfo | None — 넥슨 데이터센터 랭킹
        self._grade_name = "-"     # 감독모드 최고 등급 이름 (division 메타)
        self._division_names: dict[int, str] = {}  # divisionId -> 등급 이름
        self._is_champion = False  # 감독모드 최고 등급 챔피언스 이상 — 랭커 카드 표시 여부
        self._badge_path = ""      # 등급 배지 아이콘 로컬 캐시 경로
        self._seasons: dict = {}   # seasonId -> {className, seasonImg} (get_meta("seasonid"))
        self._season_icon_dir = config.CACHE_DIR / "season_icons"
        self._matches: list[MatchSummary] = []
        self._details: list[dict] = []
        self._names: dict = {}
        self._positions: dict = {}
        self._trend_reset_pending = True
        self._team_colors: dict[str, str] = {}   # 상대 닉네임 -> 팀컬러("" = 못 찾음)
        self._team_values: dict[str, int | None] = {}  # 상대 닉네임 -> 구단가치(원)
        self._teamcolor_loader: TeamColorLoader | None = None
        self._teamcolor_pending: list[str] = []  # 이번 라운드에 조회 요청한 닉네임
        self._teamcolor_loaded_count = 0  # 중간 갱신 주기용
        self._teamcolor_retry_pending = False  # 조회 중 범위가 넓어져 재시도가 필요함
        self._compare_loader: MatchLoader | None = None  # 구단주 비교 — 상대 계정 조회용
        self._compare_squad_loaders: list = []  # 구단주 비교 스쿼드 이미지/시즌아이콘 로더
        self._ability_sim_loader: AbilitySimLoader | None = None
        self._position_ovr_loader: AbilitySimLoader | None = None

        # 랭커/분석 두 페이지가 각각 갖는 상단 바 위젯들. 함께 갱신·잠금한다.
        self._nick_edits: list[QLineEdit] = []
        self._search_btns: list[QPushButton] = []
        self._acct_combos: list[NoScrollComboBox] = []

        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.resize(1600, 900)  # 선수 지표 표가 스크롤 없이 다 들어차는 실측 크기 근사
        self._build_ui()
        self._refresh_recent()

    # ── UI ────────────────────────────────────────────────────────────
    PAGE_SEARCH, PAGE_RANKER, PAGE_ANALYSIS = 0, 1, 2

    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_search_page())    # 0
        self.stack.addWidget(self._build_ranker_page())    # 1
        self.stack.addWidget(self._build_analysis_page())  # 2
        self.setCentralWidget(self.stack)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(14)
        self.statusBar().addPermanentWidget(self.progress, 1)
        self.statusBar().showMessage("구단주명을 입력하세요.")

    def _build_search_page(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.addStretch(1)

        # 검은 배경에 텍스트/입력창만 떠 있으면 휑해 보여서, 제목·검색창·최근
        # 검색을 부드러운 카드형 박스 하나로 감싼다.
        centering = QHBoxLayout()
        centering.addStretch(1)
        box = QFrame()
        box.setMaximumWidth(760)
        box.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: 16px; }}")
        box_v = QVBoxLayout(box)
        box_v.setContentsMargins(40, 36, 40, 32)
        box_v.setSpacing(0)

        title = QLabel("FC ONLINE")
        f = QFont()
        f.setPointSize(30)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {T.GREEN}; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_v.addWidget(title)

        sub = QLabel("감독모드 전적 분석")
        sub.setStyleSheet(f"color: {T.TEXT_DIM}; border: none; font-size: 14px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_v.addWidget(sub)
        box_v.addSpacing(24)

        row = QHBoxLayout()
        row.addStretch(1)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("구단주명을 입력해주세요.")
        self.ed_search.setFixedWidth(580)
        self.ed_search.setFixedHeight(56)
        ef = QFont()
        ef.setPointSize(13)
        self.ed_search.setFont(ef)
        self.ed_search.returnPressed.connect(self._on_search)
        btn = QPushButton("🔍")
        btn.setObjectName("primary")
        btn.setFixedSize(64, 56)
        btn.clicked.connect(self._on_search)
        row.addWidget(self.ed_search)
        row.addWidget(btn)
        row.addStretch(1)
        box_v.addLayout(row)

        self.lb_search_msg = QLabel("")
        self.lb_search_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_search_msg.setStyleSheet(f"color: {T.RED}; border: none;")
        box_v.addSpacing(10)
        box_v.addWidget(self.lb_search_msg)

        # 최근 검색 기록 — 클릭하면 바로 재검색.
        box_v.addSpacing(20)
        lb_recent = QLabel("최근 검색")
        lb_recent.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        lb_recent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_v.addWidget(lb_recent)

        self.row_recent = QHBoxLayout()
        self.row_recent.addStretch(1)
        box_v.addLayout(self.row_recent)

        centering.addWidget(box)
        centering.addStretch(1)
        outer.addLayout(centering)

        outer.addStretch(2)
        return w

    RECENT_SEARCH_LIMIT = 5

    def _refresh_recent(self) -> None:
        """검색 화면의 '최근 검색' 칩을 최신순 5개로 다시 그린다."""
        while self.row_recent.count() > 1:  # 맨 앞 stretch 는 남긴다
            item = self.row_recent.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        try:
            conn = store.open_db(config.DB_PATH)
            try:
                rows = store.recent_searches(conn, self.RECENT_SEARCH_LIMIT)
            finally:
                conn.close()
        except Exception:
            rows = []

        for r in rows:
            nick = r["nickname"] or r["ouid"][:8]
            chip = QPushButton(nick)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, n=nick: self._search_recent(n))
            self.row_recent.addWidget(chip)
        self.row_recent.addStretch(1)

    def _search_recent(self, nickname: str) -> None:
        self.ed_search.setText(nickname)
        self._on_search()

    def _top_bar(self) -> QHBoxLayout:
        """검색·등록 — 랭커/분석 두 페이지가 공유하는 상단 바."""
        bar = QHBoxLayout()
        back = QPushButton("← 검색")
        back.clicked.connect(self._go_search)
        ed = QLineEdit()
        ed.setPlaceholderText("구단주명")
        ed.setMaximumWidth(220)
        ed.returnPressed.connect(self._on_search)
        btn = QPushButton("조회")
        btn.setObjectName("primary")
        btn.clicked.connect(self._on_search)
        cb = NoScrollComboBox()
        cb.setMinimumWidth(170)
        cb.activated.connect(self._on_pick_account)

        bar.addWidget(back)
        bar.addWidget(ed)
        bar.addWidget(btn)
        bar.addWidget(QLabel("등록"))
        bar.addWidget(cb)
        bar.addStretch(1)
        # 두 페이지가 각각 자기 위젯을 갖되, 조작은 리스트로 함께 처리한다.
        self._nick_edits.append(ed)
        self._search_btns.append(btn)
        self._acct_combos.append(cb)
        return bar

    def _build_ranker_page(self) -> QWidget:
        """검색 결과 1단계 — 랭커 카드만. '감독모드 분석'을 눌러야 탭으로 간다."""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.addLayout(self._top_bar())
        outer.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        col = QVBoxLayout()
        self.card_ranker = RankerCard()
        col.addWidget(self.card_ranker, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_analyze = QPushButton("📊  감독모드 분석")
        self.btn_analyze.setObjectName("primary")
        self.btn_analyze.setFixedHeight(40)
        self.btn_analyze.clicked.connect(self._go_analysis)
        col.addWidget(self.btn_analyze)
        row.addLayout(col)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        return w

    def _build_analysis_page(self) -> QWidget:
        """검색 결과 2단계 — 프로필·요약 카드·범위·큰 탭."""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setSpacing(10)

        bar2 = QHBoxLayout()
        back = QPushButton("← 뒤로가기")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(self.PAGE_RANKER))
        self.lb_profile = QLabel("-")
        pf = QFont()
        pf.setPointSize(16)
        pf.setBold(True)
        self.lb_profile.setFont(pf)
        self.lb_sub = QLabel("-")
        self.lb_sub.setStyleSheet(f"color: {T.TEXT_DIM};")
        bar2.addWidget(back)
        bar2.addSpacing(10)
        bar2.addWidget(self.lb_profile)
        bar2.addWidget(self.lb_sub)
        bar2.addStretch(1)
        outer.addLayout(bar2)

        cards = QHBoxLayout()
        self.card_record = StatCard("전적")
        self.card_rate = StatCard("승률", T.GREEN)
        self.card_gf = StatCard("평균 득점", T.GREEN)
        self.card_ga = StatCard("평균 실점", T.RED)
        # 연승/연패는 승률 그래프 탭에서도(그 탭은 위 4개를 기간 통계로 바꿔치기
        # 한다) 항상 "지금 흐름"을 보여줘야 의미가 있어서 별도 카드로 뺀다 —
        # _show_trend_summary/_show_range_summary 의 카드 갈아치우기 대상이 아니다.
        self.card_streak = StatCard("연속")
        for c in (self.card_record, self.card_rate, self.card_gf, self.card_ga,
                 self.card_streak):
            cards.addWidget(c)
        outer.addLayout(cards)

        # 표시 범위 — 시작~끝을 직접 입력해 그 구간만 본다(레퍼런스 화면과 동일한
        # 구성). 검색 시 이미 전량을 받아 두므로 "더 불러오기"는 그 사이 새로
        # 생긴 경기가 있는지 다시 확인하는 버튼이다.
        rng = QFrame()
        rng.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; }}")
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(14, 10, 14, 10)

        self.sp_from = QSpinBox()
        self.sp_from.setRange(1, 1)
        self.sp_from.setFixedWidth(72)
        self.sp_from.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        lb_tilde = QLabel("~")
        lb_tilde.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.sp_to = QSpinBox()
        self.sp_to.setRange(1, 1)
        self.sp_to.setFixedWidth(72)
        self.sp_to.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.btn_apply = QPushButton("적용")
        self.btn_apply.setStyleSheet(T.OUTLINE_BUTTON_QSS)
        self.btn_apply.clicked.connect(self._apply_range)
        self.lb_total = QLabel("")
        self.lb_total.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.btn_more = QPushButton("⬇  새 경기 확인")
        self.btn_more.setObjectName("primary")
        self.btn_more.setToolTip(
            "검색할 때 이미 받을 수 있는 만큼 전부 받아 둡니다.\n"
            "이 버튼은 그 사이 새로 생긴 경기가 있는지 다시 확인합니다.")
        self.btn_more.clicked.connect(self._on_search)

        rl.addWidget(self.sp_from)
        rl.addWidget(lb_tilde)
        rl.addWidget(self.sp_to)
        rl.addWidget(self.btn_apply)
        rl.addSpacing(8)
        rl.addWidget(self.lb_total)
        rl.addStretch(1)
        rl.addWidget(self.btn_more)
        outer.addWidget(rng)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_players_tab(), "선수 지표")
        self.tabs.addTab(self._build_tactics_tab(), "전술·경기 결과")
        self.tabs.addTab(self._build_matches_tab(), "경기 목록")
        self.tabs.addTab(self._build_opponents_tab(), "상대 전적")
        self.TAB_TREND = self.tabs.addTab(self._build_trend_tab(), "승률 그래프")
        self.tabs.addTab(self._build_period_tab(), "기간별 추이")
        self.tabs.addTab(self._build_clutch_tab(), "승부처 분석")
        self.tabs.addTab(self._build_shotmap_tab(), "슛 맵")
        self.tabs.addTab(self._build_finishing_tab(), "선수별 결정력")
        self.tabs.addTab(self._build_position_opp_tab(), "포지션별 최다 상대")
        self.tabs.addTab(self._build_compare_tab(), "구단주 비교")
        self._teamcolor_fetch_btns: list[QPushButton] = []
        self._teamcolor_status_labels: list[QLabel] = []
        self.tabs.addTab(self._build_teamcolor_rate_tab(), "팀컬러 승률")
        self.tabs.addTab(self._build_teamcolor_rank_tab(), "팀컬러 랭킹")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self.tabs, 1)
        return w

    def _build_teamcolor_fetch_row(self) -> tuple[QHBoxLayout, QPushButton, QLabel]:
        """팀컬러 승률·랭킹 두 탭이 같은 데이터를 쓰니 조회 트리거·상태
        표시도 각 탭에 하나씩 두되 같은 핸들러(_on_fetch_team_colors)를
        공유한다."""
        row = QHBoxLayout()
        btn = QPushButton("상대 팀컬러 불러오기")
        btn.setStyleSheet(T.OUTLINE_BUTTON_QSS)
        btn.clicked.connect(self._on_fetch_team_colors)
        lb = QLabel("")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        row.addWidget(btn)
        row.addSpacing(8)
        row.addWidget(lb)
        row.addStretch(1)
        self._teamcolor_fetch_btns.append(btn)
        self._teamcolor_status_labels.append(lb)
        return row, btn, lb

    def _teamcolor_note(self) -> QLabel:
        note = QLabel(
            "※ 넥슨 데이터센터 감독모드 랭킹 top 10,000 안에서 찾아지는 상대만"
            " 반영한 근사치입니다 — 그 상대가 가장 최근 사용한 팀컬러 기준이라"
            " 실제 경기 당시와 다를 수 있고, 10,000위 밖 상대는 빠집니다.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        return note

    def _build_teamcolor_rate_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row, _, _ = self._build_teamcolor_fetch_row()
        v.addLayout(row)
        v.addWidget(self._teamcolor_note())
        self.tbl_teamcolor_rate = self._make_table(self.TEAMCOLOR_RATE_COLUMNS)
        v.addWidget(self.tbl_teamcolor_rate, 1)
        return w

    def _build_teamcolor_rank_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row, _, _ = self._build_teamcolor_fetch_row()
        v.addLayout(row)
        v.addWidget(self._teamcolor_note())
        hint = QLabel("팀컬러 이름을 더블클릭하면 그 팀컬러를 쓴 상대들이"
                     " 포지션별로 주로 기용한 선수를 볼 수 있습니다.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(hint)
        self.tbl_teamcolor_rank = self._make_table(self.TEAMCOLOR_RANK_COLUMNS)
        self.tbl_teamcolor_rank.itemDoubleClicked.connect(
            self._on_teamcolor_double_clicked)
        v.addWidget(self.tbl_teamcolor_rank, 1)
        return w

    def _build_trend_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        ctrl = QHBoxLayout()
        lb_days = QLabel("최근")
        lb_days.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.sp_trend_days = QSpinBox()
        self.sp_trend_days.setRange(1, 1)
        self.sp_trend_days.setFixedWidth(72)
        self.sp_trend_days.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        lb_days2 = QLabel("일")
        lb_days2.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.btn_trend_apply = QPushButton("적용")
        self.btn_trend_apply.setStyleSheet(T.OUTLINE_BUTTON_QSS)
        self.btn_trend_apply.clicked.connect(self._on_trend_days_apply)
        self.lb_trend_span = QLabel("")
        self.lb_trend_span.setStyleSheet(f"color: {T.TEXT_DIM};")
        ctrl.addWidget(lb_days)
        ctrl.addWidget(self.sp_trend_days)
        ctrl.addWidget(lb_days2)
        ctrl.addWidget(self.btn_trend_apply)
        ctrl.addSpacing(8)
        ctrl.addWidget(self.lb_trend_span)
        ctrl.addStretch(1)
        v.addLayout(ctrl)

        self.gb_trend = QGroupBox("최근 30일 승률 추이")
        gv = QVBoxLayout(self.gb_trend)
        self.trend_chart = TrendChart([])
        gv.addWidget(self.trend_chart)
        v.addWidget(self.gb_trend, 1)

        # 등급 추이 — 매 경기 당시 division 이 상세에 저장돼 있어 추가 조회 없음.
        # 기간(일수)은 위 승률 추이와 같은 스핀박스를 공유한다.
        self.gb_division = QGroupBox("등급 추이")
        dv = QVBoxLayout(self.gb_division)
        self.division_chart = DivisionChart()
        dv.addWidget(self.division_chart)
        v.addWidget(self.gb_division, 1)
        return w

    PERIOD_CHOICES = [("1일", 1), ("2일", 2), ("1주", 7), ("1개월", 30)]
    PERIOD_COLUMNS = ["기간", "경기", "승", "무", "패", "승률",
                      "평균득점", "평균실점"]

    def _build_period_tab(self) -> QWidget:
        """기간별 추이 — 누적 전체 경기를 1일/2일/1주/1개월 단위로 묶은 전적 표."""
        w = QWidget()
        v = QVBoxLayout(w)
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("묶음 단위"))
        self.cb_period = NoScrollComboBox()
        for label, days in self.PERIOD_CHOICES:
            self.cb_period.addItem(label, days)
        self.cb_period.setCurrentIndex(self.cb_period.findData(7))
        self.cb_period.currentIndexChanged.connect(
            lambda: self._render_period(self._matches))
        ctrl.addWidget(self.cb_period)
        ctrl.addSpacing(12)
        self.lb_streaks = QLabel("")
        self.lb_streaks.setStyleSheet(f"color: {T.TEXT_DIM};")
        ctrl.addWidget(self.lb_streaks)
        ctrl.addStretch(1)
        v.addLayout(ctrl)
        self.tbl_period = self._make_table(self.PERIOD_COLUMNS)
        v.addWidget(self.tbl_period, 1)
        return w

    def _render_period(self, matches: list[MatchSummary]) -> None:
        periods = period_stats(matches, days=self.cb_period.currentData() or 7)
        rows = [[p.label, (str(p.games), p.games), (str(p.win), p.win),
                (str(p.draw), p.draw), (str(p.lose), p.lose),
                (f"{p.win_rate:.1f}%", p.win_rate),
                (f"{p.avg_gf:.2f}", p.avg_gf), (f"{p.avg_ga:.2f}", p.avg_ga)]
               for p in periods]
        self._fill(self.tbl_period, rows)
        best_win, best_lose = longest_streaks(matches)
        kind, n = current_streak(matches)
        now_text = f"현재 {n}{kind}" if kind else "현재 -"
        self.lb_streaks.setText(
            f"{now_text} · 최장 연승 {best_win} · 최장 연패 {best_lose} (누적 전체 기준)")

    def _build_clutch_tab(self) -> QWidget:
        """승부처 분석 — 선제골 승률·역전, 시간 구간별 득실, 시각대별 승률."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        gb_first = QGroupBox("선제골 승률")
        self.box_clutch_first = QVBoxLayout(gb_first)
        self.box_clutch_first.setSpacing(3)
        v.addWidget(gb_first)

        gb_min = QGroupBox("시간 구간별 득실 (정규시간 15분 단위)")
        self.box_clutch_minute = QVBoxLayout(gb_min)
        self.box_clutch_minute.setSpacing(3)
        v.addWidget(gb_min)

        gb_tod = QGroupBox("시각대별 승률 (경기 시작 시각 기준)")
        self.box_clutch_tod = QVBoxLayout(gb_tod)
        self.box_clutch_tod.setSpacing(3)
        v.addWidget(gb_tod)

        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _render_clutch(self, details: list[dict],
                       matches: list[MatchSummary]) -> None:
        cs = st.clutch_summary(details, self._ouid)
        self._clear(self.box_clutch_first)
        for label, wdl, color in (
                ("내가 선제골", cs.first_scored, T.GREEN),
                ("선제 실점", cs.first_conceded, T.RED)):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(label)
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            b = QLabel(wdl_text(*wdl))
            b.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            c = QLabel(f"({rate_of(*wdl):.1f}%)")
            c.setStyleSheet(f"color: {color}; font-weight: bold;")
            h.addWidget(a)
            h.addStretch(1)
            h.addWidget(b)
            h.addWidget(c)
            self.box_clutch_first.addWidget(row)
        cb = QLabel(f"역전승 {cs.comeback_win}회 · 역전패 {cs.comeback_lose}회"
                    f"    (무득점·동시각 {cs.goalless}경기 제외)")
        cb.setStyleSheet(f"color: {T.TEXT_DIM}; padding-top: 3px;")
        self.box_clutch_first.addWidget(cb)

        buckets = st.goal_minute_buckets(details, self._ouid)
        # "연장"은 연장까지 간 경기가 있을 때(득실 하나라도 있을 때)만 보여준다.
        if buckets and buckets[-1].label == "연장" \
                and not (buckets[-1].scored or buckets[-1].conceded):
            buckets = buckets[:-1]
        peak = max((max(b.scored, b.conceded) for b in buckets), default=0)
        self._clear(self.box_clutch_minute)
        for bk in buckets:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(bk.label if bk.label == "연장" else f"{bk.label}분")
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            a.setFixedWidth(64)
            gbar = QProgressBar()
            gbar.setRange(0, max(peak, 1))
            gbar.setValue(bk.scored)
            gbar.setFormat(f"{bk.scored}득")
            gbar.setFixedHeight(16)
            gbar.setStyleSheet(
                f"QProgressBar{{background:{T.PANEL};border:none;border-radius:3px;"
                f"color:{T.TEXT};text-align:right;padding-right:4px;}}"
                f"QProgressBar::chunk{{background:{T.GREEN};border-radius:3px;}}")
            rbar = QProgressBar()
            rbar.setRange(0, max(peak, 1))
            rbar.setValue(bk.conceded)
            rbar.setFormat(f"{bk.conceded}실")
            rbar.setFixedHeight(16)
            rbar.setStyleSheet(
                f"QProgressBar{{background:{T.PANEL};border:none;border-radius:3px;"
                f"color:{T.TEXT};text-align:left;padding-left:4px;}}"
                f"QProgressBar::chunk{{background:{T.RED};border-radius:3px;}}")
            h.addWidget(a)
            h.addWidget(gbar, 1)
            h.addWidget(rbar, 1)
            self.box_clutch_minute.addWidget(row)

        self._clear(self.box_clutch_tod)
        for band in st.time_of_day_rates(matches):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(f"{band.label} ({band.span})")
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            a.setFixedWidth(120)
            if band.games:
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setValue(int(band.win_rate * 10))
                bar.setFormat(f"{band.win_rate:.1f}%  ({wdl_text(band.win, band.draw, band.lose)})")
                bar.setFixedHeight(16)
                bar.setStyleSheet(
                    f"QProgressBar{{background:{T.PANEL};border:none;border-radius:3px;"
                    f"color:{T.TEXT};text-align:center;}}"
                    f"QProgressBar::chunk{{background:{T.GREEN};border-radius:3px;}}")
                h.addWidget(a)
                h.addWidget(bar, 1)
            else:
                none = QLabel("경기 없음")
                none.setStyleSheet(f"color: {T.TEXT_DIM};")
                h.addWidget(a)
                h.addWidget(none, 1)
            self.box_clutch_tod.addWidget(row)

    def _build_shotmap_tab(self) -> QWidget:
        """슛 맵 — 슛 좌표를 하프 피치 위에 점으로. 내 슛/상대 슛 토글."""
        w = QWidget()
        v = QVBoxLayout(w)

        ctrl = QHBoxLayout()
        self.cb_shotmap_side = NoScrollComboBox()
        self.cb_shotmap_side.addItem("내 슛", True)
        self.cb_shotmap_side.addItem("상대 슛 (실점 위치)", False)
        self.cb_shotmap_side.currentIndexChanged.connect(self._render_shotmap)
        ctrl.addWidget(QLabel("표시"))
        ctrl.addWidget(self.cb_shotmap_side)
        ctrl.addSpacing(16)
        # 결과 종류별 표시 토글(범례 겸용). result 코드로 필터한다.
        self.chk_shotmap_result: dict[int, QCheckBox] = {}
        for result, text, color in ((st.SHOT_GOAL, "● 골", T.GREEN),
                                    (st.SHOT_ON_TARGET, "● 유효슛", T.YELLOW),
                                    (st.SHOT_OFF_TARGET, "● 빗나감", T.TEXT_DIM)):
            chk = QCheckBox(text)
            chk.setChecked(True)
            chk.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            chk.toggled.connect(self._render_shotmap)
            self.chk_shotmap_result[result] = chk
            ctrl.addWidget(chk)
        ctrl.addStretch(1)
        self.lb_shotmap_summary = QLabel("")
        self.lb_shotmap_summary.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        ctrl.addWidget(self.lb_shotmap_summary)
        v.addLayout(ctrl)

        self.shotmap = ShotMapWidget()
        v.addWidget(self.shotmap, 1)
        return w

    def _render_shotmap(self) -> None:
        _, details = self._slice()
        mine = bool(self.cb_shotmap_side.currentData())
        sm = st.shot_map(details, self._ouid, mine=mine)
        # 체크된 결과 종류만 화면에 찍는다(요약 수치는 전체 기준 유지).
        shown = {r for r, chk in self.chk_shotmap_result.items() if chk.isChecked()}
        shots = [s for s in sm.shots if s.result in shown]
        # 상대 슛 좌표는 상대 공격 기준이라 좌우(y)가 내 시점과 뒤집혀 있다 —
        # 같은 골문(위)에 그리되 y 를 뒤집어 내 시점으로 통일한다(x=골문은 동일).
        if not mine:
            shots = [replace(s, y=1.0 - s.y) for s in shots]
        self.shotmap.set_shots(shots)
        who = "내" if mine else "상대"
        self.lb_shotmap_summary.setText(
            f"{who} 슛 {sm.total} · 골 {sm.goals} · "
            f"유효슛 {sm.effective} ({sm.effective_rate:.0f}%) · "
            f"전환율 {sm.conversion:.0f}% · 기대골(xG) {sm.xg:.1f}")

    def _build_finishing_tab(self) -> QWidget:
        """선수별 결정력 — 슈터별 슛·골·전환율·xG·어시스트. xG는 비공식 근사치."""
        w = QWidget()
        v = QVBoxLayout(w)
        note = QLabel("표시 구간 기준 · 내 슛만 · 골이 많은 순  "
                      "(xG=기대득점, 넥슨이 안 주는 비공식 근사치 · "
                      "골−xG가 +면 근사 기대보다 더 넣은 것)")
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        note.setWordWrap(True)
        v.addWidget(note)
        self.tbl_finishing = self._make_table(self.FINISHING_COLUMNS)
        self.tbl_finishing.itemDoubleClicked.connect(
            self._on_finishing_double_clicked)
        v.addWidget(self.tbl_finishing, 1)
        return w

    def _render_finishing(self, details: list[dict]) -> None:
        players = st.finishing_ranking(
            details, self._ouid, name_of=lambda i: self._names.get(i, str(i)))
        rows = []
        for p in players:
            rows.append([
                p.name, (f"{p.shots}", p.shots), (f"{p.on_target}", p.on_target),
                (f"{p.goals}", p.goals), (f"{p.conversion:.0f}%", p.conversion),
                (f"{p.xg:.1f}", p.xg), (f"{p.xg_diff:+.1f}", p.xg_diff),
                (f"{p.assists}", p.assists)])
        self._fill(self.tbl_finishing, rows, enable_sort=False)
        # 골−xG(6열)에 색: +면 초록(해결력↑), −면 빨강. spId 는 선수명(0열)에.
        peak = max((abs(p.xg_diff) for p in players), default=1) or 1
        for r, p in enumerate(players):
            self._tint(self.tbl_finishing.item(r, 6), abs(p.xg_diff), peak,
                       T.GREEN if p.xg_diff >= 0 else T.RED)
            name_item = self.tbl_finishing.item(r, 0)
            if name_item:
                name_item.setData(Qt.ItemDataRole.UserRole, p.sp_id)
        self.tbl_finishing.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.tbl_finishing.setSortingEnabled(True)
        self._load_season_icons([p.sp_id for p in players], self.tbl_finishing, 0,
                                "_finishing_icon_loader")

    def _on_finishing_double_clicked(self, item) -> None:
        """결정력 표에서 선수 더블클릭 → 선수 카드. spId 는 0열 UserRole."""
        name_item = item.tableWidget().item(item.row(), 0)
        sp_id = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if isinstance(sp_id, int):
            self._show_player_info(sp_id)

    def _on_trend_days_apply(self) -> None:
        self._render_trend(self._matches)

    @staticmethod
    def _make_table(columns: list[str],
                    widget_cls: type = QTableWidget) -> QTableWidget:
        t = widget_cls(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.setItemDelegate(RowBorderDelegate(t))
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        return t

    def _build_players_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.tbl_players = self._make_table(self.PLAYER_COLUMNS,
                                            widget_cls=FitTableWidget)
        # 19개 열이라 전역 폰트(15px)로는 스크롤 없이 한눈에 안 들어온다.
        # 이 표만 폰트·여백을 줄인 기본 크기(14/13px)에서 시작한다 — 창이
        # 좁아지면 FitTableWidget._fit() 이 이보다 더 줄여가며 맞춘다.
        # QSS 에 font-size 를 박아두면 그려질 때 그 값이 항상 이기고
        # setFont() 로 준 크기는 QFontMetrics 측정에만 쓰여서, 축소해도
        # 화면엔 그대로 큰 글씨가 남아 잘림이 재발한다 — 그래서 폰트 크기는
        # QSS 가 아니라 setFont() 하나로만 관리한다.
        self.tbl_players.setStyleSheet(
            f"QTableWidget::item {{ padding: 14px 13px; margin: 0px; }}"
            f"QHeaderView::section {{ padding: 8px 10px; }}")
        cell_font = QFont()
        cell_font.setPixelSize(14)
        self.tbl_players.setFont(cell_font)
        hdr = self.tbl_players.horizontalHeader()
        header_font = QFont()
        header_font.setPixelSize(13)
        header_font.setBold(True)
        hdr.setFont(header_font)
        hdr.setMinimumSectionSize(0)
        self.tbl_players.set_base_font_px(14, 13)
        # Fixed — _render_players 에서 데이터가 채워진 뒤 _fit_columns_to_content
        # 가 "헤더 글자 폭·값 글자 폭 중 큰 쪽" 기준으로 직접 너비를 잡는다.
        # Interactive 로 두면 사용자가 드래그로 너비를 바꿀 수 있는데, 그러면
        # 창 크기 변경 때 자동으로 다시 맞추는 로직과 계속 충돌하니 아예
        # 사용자 조절은 막는다(Fixed 라도 setColumnWidth 호출로 코드에서
        # 너비를 바꾸는 건 그대로 된다).
        for c in range(len(self.PLAYER_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
        # 헤더 위에 마우스를 올리면 지표 계산 기준을 보여준다 — 공격력/수비력처럼
        # 오픈API에 없어 역산한 지표는 이름만으로는 기준이 안 보이기 때문.
        for c, name in enumerate(self.PLAYER_COLUMNS):
            help_text = self.PLAYER_COLUMN_HELP.get(name)
            if help_text:
                item = self.tbl_players.horizontalHeaderItem(c)
                if item:
                    item.setToolTip(help_text)
        self.tbl_players.setIconSize(QSize(18, 18))
        # "선수" 열(1번)에 spId 를 UserRole 로 붙여두고(_render_players)
        # 더블클릭하면 상대 스쿼드 화면과 같은 선수 카드 다이얼로그를 연다.
        self.tbl_players.itemDoubleClicked.connect(self._on_player_cell_double_clicked)
        v.addWidget(self.tbl_players, 1)
        return w

    MATCH_FILTER_KINDS = (("승", T.GREEN), ("무", T.TEXT_DIM), ("패", T.RED))

    def _build_matches_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        row = QHBoxLayout()
        lb = QLabel("결과 필터")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        row.addWidget(lb)
        self._match_filter_btns: dict[str, QPushButton] = {}
        for kind, color in self.MATCH_FILTER_KINDS:
            btn = QPushButton(kind)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setFixedWidth(48)
            btn.setStyleSheet(
                f"QPushButton {{ border: 1px solid {T.BORDER}; border-radius: 6px;"
                f" padding: 4px; }}"
                f"QPushButton:checked {{ background: {color}; color: #06240d;"
                f" font-weight: bold; border-color: {color}; }}")
            btn.clicked.connect(self._apply_match_filter)
            row.addWidget(btn)
            self._match_filter_btns[kind] = btn
        row.addStretch(1)
        v.addLayout(row)

        self.table = self._make_table(self.MATCH_COLUMNS)
        self.table.itemDoubleClicked.connect(self._on_match_double_clicked)
        v.addWidget(self.table, 1)
        return w

    def _apply_match_filter(self) -> None:
        """체크한 결과만 남기고 나머지 행은 숨긴다 — 데이터는 안 지우고
        표시만 가린다(setRowHidden), 그래서 필터 해제하면 바로 되돌아온다."""
        active = {k for k, b in self._match_filter_btns.items() if b.isChecked()}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 1)  # 결과 컬럼
            text = item.text() if item else ""
            kind = next((k for k, _ in self.MATCH_FILTER_KINDS if k in text), None)
            self.table.setRowHidden(r, kind is not None and kind not in active)

    def _build_opponents_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        row = QHBoxLayout()
        lb = QLabel("상대 검색")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.ed_opponent_filter = QLineEdit()
        self.ed_opponent_filter.setPlaceholderText("닉네임 일부만 입력해도 찾습니다")
        self.ed_opponent_filter.setMaximumWidth(240)
        self.ed_opponent_filter.textChanged.connect(self._apply_opponent_filter)
        row.addWidget(lb)
        row.addWidget(self.ed_opponent_filter)
        row.addStretch(1)
        v.addLayout(row)

        self.tbl_opponents = self._make_table(self.OPPONENT_COLUMNS)
        self.tbl_opponents.itemDoubleClicked.connect(self._on_opponent_double_clicked)
        v.addWidget(self.tbl_opponents, 1)
        return w

    def _apply_opponent_filter(self) -> None:
        needle = self.ed_opponent_filter.text().strip().lower()
        for r in range(self.tbl_opponents.rowCount()):
            item = self.tbl_opponents.item(r, 0)  # 상대 컬럼
            hidden = bool(needle) and (not item or needle not in item.text().lower())
            self.tbl_opponents.setRowHidden(r, hidden)

    POSITION_COLOR_ALL = "전체"

    def _build_position_opp_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        row = QHBoxLayout()
        lb = QLabel("팀컬러")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.cb_position_color = NoScrollComboBox()
        self.cb_position_color.addItem(self.POSITION_COLOR_ALL)
        self.cb_position_color.setMinimumWidth(160)
        self.cb_position_color.currentIndexChanged.connect(
            self._on_position_color_changed)
        row.addWidget(lb)
        row.addWidget(self.cb_position_color)
        row.addSpacing(8)
        lb_note = QLabel("※ '팀컬러 승률/랭킹' 탭에서 상대 팀컬러를 먼저 불러와야 목록이 채워집니다.")
        lb_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        row.addWidget(lb_note)
        row.addStretch(1)
        v.addLayout(row)

        self.tbl_position_opp = self._make_table(self.POSITION_OPP_COLUMNS)
        # 이 표는 순서 자체가 정보다(공격→미들→수비→GK, 줄별 색상) — 헤더
        # 클릭 정렬을 허용하면 그 순서·색상 의미가 깨지니 꺼 둔다.
        self.tbl_position_opp.setSortingEnabled(False)
        self.tbl_position_opp.itemDoubleClicked.connect(self._on_player_cell_double_clicked)
        v.addWidget(self.tbl_position_opp, 1)
        return w

    def _refresh_position_color_options(self) -> None:
        """알려진 팀컬러 목록(self._team_colors 값)으로 필터 콤보를 다시 채운다.

        팀컬러가 새로 조회될 때마다(_render_teamcolor_tabs) 불린다. 사용자가
        고른 색이 새 목록에도 있으면 선택을 유지하고, 없어졌으면 '전체'로
        되돌린다 — 표를 다시 그릴 때마다 필터가 조용히 풀리면 안 되니까.
        """
        current = self.cb_position_color.currentText()
        colors = sorted({c for c in self._team_colors.values() if c})
        self.cb_position_color.blockSignals(True)
        self.cb_position_color.clear()
        self.cb_position_color.addItem(self.POSITION_COLOR_ALL)
        self.cb_position_color.addItems(colors)
        keep = current if current in colors else self.POSITION_COLOR_ALL
        self.cb_position_color.setCurrentText(keep)
        self.cb_position_color.blockSignals(False)

    def _on_position_color_changed(self, _index: int) -> None:
        _, details = self._slice()
        self._render_position_opponents(details)

    COMPARE_ROWS = [
        # (라벨, Stats 속성, 표시 포맷, 값이 클수록 좋음인지)
        ("승률", "win_rate", "{:.1f}%", True),
        ("평균 득점", "avg_goals_for", "{:.2f}", True),
        ("평균 실점", "avg_goals_against", "{:.2f}", False),
        ("평균 점유율", "avg_possession", "{:.1f}%", True),
        ("평균 평점", "avg_rating", "{:.2f}", True),
    ]

    def _build_compare_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        row = QHBoxLayout()
        lb = QLabel("상대 닉네임")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.ed_compare_nick = QLineEdit()
        self.ed_compare_nick.setPlaceholderText("비교할 구단주명")
        self.ed_compare_nick.setMaximumWidth(220)
        self.ed_compare_nick.returnPressed.connect(self._on_compare_search)
        lb_n = QLabel("최근")
        lb_n.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.sp_compare_n = QSpinBox()
        self.sp_compare_n.setRange(5, 100)
        self.sp_compare_n.setValue(30)
        self.sp_compare_n.setFixedWidth(64)
        self.sp_compare_n.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        lb_n2 = QLabel("경기")
        lb_n2.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.btn_compare = QPushButton("비교")
        self.btn_compare.setObjectName("primary")
        self.btn_compare.clicked.connect(self._on_compare_search)
        self.lb_compare_status = QLabel("")
        self.lb_compare_status.setStyleSheet(f"color: {T.TEXT_DIM};")

        row.addWidget(lb)
        row.addWidget(self.ed_compare_nick)
        row.addSpacing(8)
        row.addWidget(lb_n)
        row.addWidget(self.sp_compare_n)
        row.addWidget(lb_n2)
        row.addSpacing(8)
        row.addWidget(self.btn_compare)
        row.addSpacing(8)
        row.addWidget(self.lb_compare_status)
        row.addStretch(1)
        v.addLayout(row)

        note = QLabel("※ 상대 계정은 최근 경기를 새로 조회합니다(API 호출) — 처음 보는"
                      " 계정이면 시간이 걸릴 수 있습니다.")
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(note)

        self.tbl_compare = self._make_table(["지표", "내 계정", "상대 계정"])
        self.tbl_compare.setSortingEnabled(False)  # 지표 순서 자체가 정보라 정렬 고정
        v.addWidget(self.tbl_compare)

        # 각 구단주가 가장 최근 경기에 낸 스쿼드를 나란히 보여준다 —
        # PitchWidget 최소 폭(560)이 둘이면 창 기본 폭(1600)에 빠듯해서
        # 가로 스크롤 여지를 둔다.
        squad_scroll = QScrollArea()
        squad_scroll.setWidgetResizable(True)
        squad_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        squad_host = QWidget()
        squad_row = QHBoxLayout(squad_host)
        self.box_compare_my_squad = QVBoxLayout()
        gb_my = QGroupBox("내 스쿼드 (최근 경기)")
        gb_my.setLayout(self.box_compare_my_squad)
        self.box_compare_opp_squad = QVBoxLayout()
        gb_opp = QGroupBox("상대 스쿼드 (최근 경기)")
        gb_opp.setLayout(self.box_compare_opp_squad)
        squad_row.addWidget(gb_my, 1)
        squad_row.addWidget(gb_opp, 1)
        squad_scroll.setWidget(squad_host)
        v.addWidget(squad_scroll, 1)
        return w

    def _on_compare_search(self) -> None:
        if not self._ouid:
            QMessageBox.information(self, "구단주 비교", "먼저 내 계정을 검색해주세요.")
            return
        nick = self.ed_compare_nick.text().strip()
        if not nick:
            return
        if self._compare_loader and self._compare_loader.isRunning():
            return
        self.btn_compare.setEnabled(False)
        self.lb_compare_status.setText(f"'{nick}' 조회 중…")
        # 내 계정과 같은 방식(MatchLoader) 재사용 — 이미 DB 캐시·중복 방지가 있어
        # 같은 상대를 다시 비교하면 거의 즉시 끝난다.
        self._compare_loader = MatchLoader(self._api, nick, config.DEFAULT_MATCH_TYPE)
        self._compare_loader.finished_ok.connect(self._on_compare_loaded)
        self._compare_loader.failed.connect(self._on_compare_failed)
        self._compare_loader.start()

    def _on_compare_loaded(self, matches: list, details: list, ouid: str,
                           basic: dict, names: dict, positions: dict,
                           new: int, got: int, rank, grade_name: str,
                           is_champion: bool, badge_path: str,
                           seasons: dict) -> None:
        self.btn_compare.setEnabled(True)
        nick = basic.get("nickname") or self.ed_compare_nick.text().strip()
        if not matches:
            self.lb_compare_status.setText(f"{nick} — 감독모드 기록이 없습니다.")
            return
        n = self.sp_compare_n.value()
        self._render_compare(nick, matches[:n], ouid, details)
        self.lb_compare_status.setText(
            f"{nick} — 최근 {min(n, len(matches))}경기 비교")

    def _on_compare_failed(self, msg: str) -> None:
        self.btn_compare.setEnabled(True)
        self.lb_compare_status.setText(msg)

    def _render_compare(self, opp_nick: str, opp_matches: list[MatchSummary],
                        opp_ouid: str, opp_details: list[dict]) -> None:
        n = self.sp_compare_n.value()
        # self._matches 는 누적 전체가 최신순으로 있으므로 앞에서 N개만 쓴다.
        my_matches = self._matches[:n]
        my_stats = summarize(my_matches)
        opp_stats = summarize(opp_matches)

        self.tbl_compare.setHorizontalHeaderLabels(
            ["지표", f"{self._nick} (최근 {len(my_matches)}경기)",
             f"{opp_nick} (최근 {len(opp_matches)}경기)"])

        rows = [("경기수", str(len(my_matches)), str(len(opp_matches)),
                len(my_matches), len(opp_matches), None)]
        for label, attr, fmt, higher_is_better in self.COMPARE_ROWS:
            mine_val = getattr(my_stats, attr)
            opp_val = getattr(opp_stats, attr)
            rows.append((label, fmt.format(mine_val), fmt.format(opp_val),
                        mine_val, opp_val, higher_is_better))

        self.tbl_compare.setRowCount(len(rows))
        for r, (label, mine_txt, opp_txt, mine_val, opp_val,
               higher_is_better) in enumerate(rows):
            self.tbl_compare.setItem(r, 0, self._cell(label))
            item_mine = self._cell(mine_txt)
            item_opp = self._cell(opp_txt)
            self.tbl_compare.setItem(r, 1, item_mine)
            self.tbl_compare.setItem(r, 2, item_opp)
            if higher_is_better is not None and mine_val != opp_val:
                mine_wins = (mine_val > opp_val) == higher_is_better
                item_mine.setForeground(QColor(T.GREEN if mine_wins else T.TEXT))
                item_opp.setForeground(QColor(T.TEXT if mine_wins else T.GREEN))

        for loader in self._compare_squad_loaders:
            loader.cancel()
            loader.wait(500)
        self._compare_squad_loaders = []
        self._fill_compare_squad(self.box_compare_my_squad, self._ouid, self._details,
                                 self._nick)
        self._fill_compare_squad(self.box_compare_opp_squad, opp_ouid, opp_details,
                                 opp_nick)

    def _fill_compare_squad(self, box: QVBoxLayout, ouid: str, details: list[dict],
                            label: str) -> None:
        self._clear(box)
        found = st.own_squad(details, ouid)
        if found is None:
            lb = QLabel("표시할 스쿼드가 없습니다.")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            box.addWidget(lb)
            return
        players, match_date, result = found
        formation = st.formation_of(players)
        title = QLabel(f"{label}  ·  {formation}  ·  {result}  ·  {match_date}")
        title.setStyleSheet(f"color: {T.TEXT_DIM};")
        title.setWordWrap(True)
        box.addWidget(title)

        pitch, sp_ids = self._make_pitch_from_players(players)
        box.addWidget(pitch)

        loader = ImageLoader(sp_ids, self._img_cache_dir)
        loader.loaded.connect(pitch.set_face)
        loader.start()
        self._compare_squad_loaders.append(loader)

        season_entries = []
        for sp_id in sp_ids:
            season_id = st.season_id_of(sp_id)
            info = self._seasons.get(season_id)
            if info and info.get("seasonImg"):
                season_entries.append((sp_id, season_id, info["seasonImg"]))
        season_loader = SeasonIconLoader(season_entries, self._season_icon_dir)
        season_loader.loaded.connect(pitch.set_season_icon)
        season_loader.start()
        self._compare_squad_loaders.append(season_loader)

    def _build_tactics_tab(self) -> QWidget:
        # 기본 창(1600x900) 안에 스크롤 없이 담으려고 그룹박스·행 사이 여백을
        # 기본값보다 눌러뒀다 — 값이 없어서가 아니라 순전히 세로 공간 절약용.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        gb_f = QGroupBox("전술 분석")
        vf = QVBoxLayout(gb_f)
        vf.setSpacing(5)

        self.lb_my_formation = QLabel("-")
        mf = QFont()
        mf.setPointSize(14)
        mf.setBold(True)
        self.lb_my_formation.setFont(mf)
        self.lb_my_formation.setStyleSheet(
            f"background: #12261a; border: 1px solid {T.BORDER};"
            f" border-radius: 6px; padding: 8px;")
        vf.addWidget(self.lb_my_formation)

        self.box_opp = QVBoxLayout()
        self.box_opp.setSpacing(2)
        vf.addLayout(self.box_opp)
        v.addWidget(gb_f)

        gb_r = QGroupBox("경기 결과")
        rl = QHBoxLayout(gb_r)
        self.box_result = QVBoxLayout()
        self.box_gf = QVBoxLayout()
        self.box_ga = QVBoxLayout()
        for box, title in ((self.box_result, "경기 결과"),
                           (self.box_gf, "득점 유형"),
                           (self.box_ga, "실점 유형")):
            box.setSpacing(2)
            holder = QGroupBox(title)
            holder.setLayout(box)
            rl.addWidget(holder, 1)
        v.addWidget(gb_r)
        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    # ── 등록 계정 ─────────────────────────────────────────────────────
    def _refresh_accounts(self) -> None:
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                rows = store.list_accounts(conn)
                counts = {r["ouid"]: store.match_count(
                    conn, r["ouid"], config.DEFAULT_MATCH_TYPE) for r in rows}
            finally:
                conn.close()
        except Exception:
            return  # 목록을 못 채워도 검색은 되어야 한다

        for cb in self._acct_combos:
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("— 선택 —", None)
            for r in rows:
                nick = r["nickname"] or r["ouid"][:8]
                cb.addItem(f"{nick} ({counts.get(r['ouid'], 0)})", nick)
            cb.blockSignals(False)

    def _on_pick_account(self, index: int) -> None:
        cb = self.sender()
        nick = cb.itemData(index) if cb else None
        if nick:
            self._nick = nick
            self._api_search(nick)

    # ── 조회 ──────────────────────────────────────────────────────────
    def _go_search(self) -> None:
        self.stack.setCurrentIndex(self.PAGE_SEARCH)
        self.ed_search.setFocus()
        self.ed_search.selectAll()

    def _go_analysis(self) -> None:
        if self._matches:
            self.stack.setCurrentIndex(self.PAGE_ANALYSIS)

    def _on_search(self) -> None:
        if self.stack.currentIndex() == self.PAGE_SEARCH:
            nick = self.ed_search.text().strip()
        else:
            src = self.sender()
            # 상단 바의 조회 버튼/입력창 중 어느 페이지에서 눌렸든 그 입력값을 쓴다.
            nick = ""
            for ed in self._nick_edits:
                if ed.text().strip():
                    nick = ed.text().strip()
                    break
        if not nick:
            self.lb_search_msg.setText("구단주명을 입력해주세요.")
            return
        self.lb_search_msg.setText("")
        self._api_search(nick)

    def _api_search(self, nick: str) -> None:
        if self._loader and self._loader.isRunning():
            return
        self._nick = nick
        self._set_busy(True)
        self._loader = MatchLoader(self._api, nick, config.DEFAULT_MATCH_TYPE)
        self._loader.progress.connect(self._on_progress)
        self._loader.finished_ok.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def _apply_range(self) -> None:
        """시작~끝 스핀박스 값대로 표시 구간을 바꾼다."""
        self._render_all()
        self._on_fetch_team_colors()  # 범위가 넓어졌으면 새로 들어온 상대만 조회

    def _set_busy(self, busy: bool) -> None:
        for w in (*self._search_btns, *self._nick_edits, self.ed_search):
            w.setEnabled(not busy)
        for b in (self.btn_more, self.btn_apply, self.btn_analyze):
            b.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setValue(0)

    def _on_progress(self, done: int, total: int, msg: str) -> None:
        # total==0 이면 총계를 모르는 단계(목록 수집 등) — 물결 막대로 둔다.
        self.progress.setMaximum(total if total > 0 else 0)
        self.progress.setValue(done)
        self.statusBar().showMessage(msg)

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.statusBar().showMessage("조회 실패")
        if self.stack.currentIndex() == self.PAGE_SEARCH:
            self.lb_search_msg.setText(msg)
        else:
            QMessageBox.warning(self, "조회 실패", msg)

    def _on_loaded(self, matches: list, details: list, ouid: str, basic: dict,
                   names: dict, positions: dict, new: int, got: int,
                   rank, grade_name: str, is_champion: bool,
                   badge_path: str, seasons: dict, division_names: dict) -> None:
        self._set_busy(False)
        self._refresh_accounts()
        self._refresh_recent()
        if ouid != self._ouid:
            self._trend_reset_pending = True  # 다른 계정으로 전환 — 승률 그래프 기간을 30일로 되돌린다
        self._ouid = ouid
        self._basic = basic
        self._rank = rank
        self._grade_name = grade_name
        self._is_champion = is_champion
        self._badge_path = badge_path
        if seasons:
            self._seasons = seasons
        if division_names:
            self._division_names = division_names
        self._nick = basic.get("nickname") or self._nick
        for ed in self._nick_edits:
            ed.setText(self._nick)
        if names:
            self._names, self._positions = names, positions
        self._matches, self._details = matches, details

        # 시작~끝 스핀박스 — 처음엔 최근 100경기(또는 그 이하)를 기본으로 보여준다.
        total_n = len(matches)
        self.sp_from.blockSignals(True)
        self.sp_to.blockSignals(True)
        self.sp_from.setRange(1, max(total_n, 1))
        self.sp_to.setRange(1, max(total_n, 1))
        self.sp_from.setValue(1)
        self.sp_to.setValue(min(PAGE_SIZE, total_n) or 1)
        self.sp_from.blockSignals(False)
        self.sp_to.blockSignals(False)

        # 검색 결과는 먼저 랭커 카드 페이지로.
        self.stack.setCurrentIndex(self.PAGE_RANKER)
        self.btn_analyze.setEnabled(bool(matches))
        self._render_ranker()

        if not matches:
            self.statusBar().showMessage(f"{self._nick} — 감독모드 기록이 없습니다.")
            return

        self._render_all()
        self._on_fetch_team_colors()  # DB 캐시(TTL 30일)로 채우고, 모자란 것만 백그라운드 조회
        self.statusBar().showMessage(
            f"{self._nick} — 누적 {len(matches)}경기 (감독모드 전체)"
            + (f" · 새 경기 {new}건 저장" if new else ""))

    # ── 렌더 ──────────────────────────────────────────────────────────
    def _slice(self) -> tuple[list[MatchSummary], list[dict]]:
        """시작~끝 스핀박스 구간만. 표시 순서는 최신순 그대로다."""
        total_n = len(self._matches)
        a = max(self.sp_from.value() - 1, 0)
        b = min(self.sp_to.value(), total_n)
        if a >= b:
            a, b = 0, total_n
        shown = self._matches[a:b]
        ids = {m.match_id for m in shown}
        return shown, [d for d in self._details if d.get("matchId") in ids]

    def _render_all(self) -> None:
        matches, details = self._slice()
        total = len(self._matches)
        self.lb_total.setText(f"전체 {total}경기")
        self.lb_profile.setText(self._nick)
        self.lb_sub.setText(f"Lv.{self._basic.get('level', '-')}  ·  {self._grade_name}  ·  "
                            f"감독모드 {len(matches)}경기 분석 (누적 {total})")
        self._show_range_summary()
        self._render_ranker()
        self._render_matches(matches)
        self._render_players(details)
        self._render_tactics(details)
        self._render_opponents(matches)
        self._render_position_opponents(details)
        self._render_teamcolor_tabs(matches, details)
        # 승률 추이는 "최근 30일" 이 표시 구간(시작~끝, 최근 최대 100경기)에
        # 갇히면 안 된다 — 하루에 100경기 넘게 뛰는 계정은 그 구간이 하루도
        # 안 될 수 있어서, 누적 전체(self._matches)에서 30일을 계산한다.
        self._render_trend(self._matches)
        self._render_period(self._matches)  # 기간별 추이도 누적 전체 기준
        self._render_clutch(self._details, self._matches)  # 승부처도 누적 전체 기준
        self._render_shotmap()  # 슛 맵은 표시 구간(_slice) 기준
        self._render_finishing(details)  # 결정력도 표시 구간 기준

    def _render_ranker(self) -> None:
        """랭커 카드 — 챔피언스 이상일 때만 순위·구단가치·ELO 를 보여준다.

        그 등급 미만은 넥슨 데이터센터 1만 위 랭킹에도 거의 안 잡히고 값도
        의미가 약해서, 카드를 수수한 '구단주 정보'로 바꾸고 전적·등급만 보여준다.
        데이터센터가 감독모드 통산(오픈API 의 최근 3천 경기보다 많다)을 주므로
        랭커면 그 전적을 쓰고, 아니면(또는 조회 실패) 우리 집계로 대체한다.
        """
        c = self.card_ranker
        r = self._rank
        c.set_mode(self._is_champion, self._grade_name)
        lv = (r.level if r and r.level else self._basic.get("level", "-"))
        c.set_name(f"{self._nick}  Lv.{lv}")
        c.set_badge(self._badge_path or None)

        if self._is_champion and r and r.ranked:
            c.set("순위", f"{r.rank:,}위", T.GREEN)
            c.set("전적", f"{r.record_text} ({r.win_rate})")
            c.set("구단가치", r.team_value_text or NA)
            c.set("점수", f"{r.elo:g}" if r.elo is not None else NA)
            c.note.setText(f"* {self._grade_name} · 넥슨 데이터센터 감독모드 통산 · 매시각 갱신")
            c.setToolTip("순위·구단가치·점수·통산전적은 넥슨 공식 데이터센터에서\n"
                         "가져옵니다(감독모드 랭킹, 매시각 갱신).")
        else:
            # 챔피언스 미만이거나, 랭커인데 랭킹 조회에 실패한 경우 — 우리 집계로.
            full = summarize(self._matches)
            c.set("전적",
                  f"{wdl_text(full.win, full.draw, full.lose)} ({full.win_rate:.1f}%)")
            last = self._matches[0].date_text if self._matches else "-"
            c.note.setText(f"* {self._grade_name} · 최근 {len(self._matches)}경기 기준 · {last}")
            c.setToolTip("챔피언스 이상 등급에서만 넥슨 데이터센터 순위·구단가치·\n"
                         "점수를 보여줍니다. 전적은 앱이 받은 경기 기준입니다.")

    @staticmethod
    def _cell(text: str, key=None):
        item = SortableItem(text, key)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _fill(self, table: QTableWidget, rows: list[list],
             enable_sort: bool = True) -> None:
        """표를 다시 채운다.

        enable_sort=True 로 끝에서 setSortingEnabled(True) 를 부르면, 이 표에
        이미 정렬 상태(헤더의 정렬 컬럼·방향)가 남아 있을 때 Qt 가 그 자리에서
        즉시 재정렬한다(문서화된 동작). 채우자마자 재정렬되면, 채운 직후 행
        순서를 그대로 믿고 색을 칠하거나 데이터를 붙이는 코드(선수 지표의
        _render_players)가 엉뚱한 행을 건드리게 된다 — 그래서 그런 후처리가
        있는 표는 enable_sort=False 로 두고, 후처리가 끝난 뒤 직접
        setSortingEnabled(True) 를 불러야 한다.
        """
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                text, key = cell if isinstance(cell, tuple) else (cell, None)
                table.setItem(r, c, self._cell(text, key))
        if enable_sort:
            table.setSortingEnabled(True)

    @staticmethod
    def _fit_columns_to_content(table: QTableWidget,
                                extra: dict[int, int] | None = None) -> None:
        """헤더 글자 폭과 값 글자 폭 중 큰 쪽으로 열 너비를 잡는다 — 헤더만
        기준으로 하면(ResizeToContents 원래 동작) 짧은 값 주위가 헐렁해 보이고,
        값만 기준으로 하면 긴 헤더("기대득점률" 등)가 잘린다.
        extra: 열 번호별로 더 얹을 여백(아이콘이 같이 나오는 열 등).

        FitTableWidget 은 폭 계산·창 폭에 안 맞을 때 폰트를 줄이는 것까지
        전부 자기 안에서 처리한다(widgets.FitTableWidget._fit) — 여기서는
        데이터가 채워진 지금 다시 맞추라고 호출만 해 준다.
        """
        if isinstance(table, FitTableWidget):
            table.set_content_widths(extra)
            return
        fm = QFontMetrics(table.font())
        hdr_fm = QFontMetrics(table.horizontalHeader().font())
        extra = extra or {}
        widths: dict[int, int] = {}
        for c in range(table.columnCount()):
            w = 0
            header_item = table.horizontalHeaderItem(c)
            if header_item:
                w = hdr_fm.horizontalAdvance(header_item.text())
            for r in range(table.rowCount()):
                item = table.item(r, c)
                if item:
                    w = max(w, fm.horizontalAdvance(item.text()))
            widths[c] = w + 26 + extra.get(c, 0)
        for c, w in widths.items():
            table.setColumnWidth(c, w)

    def _render_matches(self, matches: list[MatchSummary]) -> None:
        rows = []
        for m in matches:
            rows.append([
                m.date_text, m.result, m.score, m.opponent,
                (f"{m.possession}%", m.possession), (f"{m.shoot_total}", m.shoot_total),
                (f"{m.shoot_effective}", m.shoot_effective),
                (f"{m.pass_rate:.0f}%", m.pass_rate), (f"{m.rating:.2f}", m.rating),
            ])
        # enable_sort=False — 선수 지표에서 겪은 것과 같은 이유(재정렬 타이밍
        # 버그). 행 배경색을 매기는 동안은 채운 순서 = matches 순서가 보장돼야
        # 한다.
        self._fill(self.table, rows, enable_sort=False)
        for r, m in enumerate(matches):
            if "승" in m.result:
                bg = T.WIN
            elif "패" in m.result:
                bg = T.LOSE
            else:
                bg = None
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if not item:
                    continue
                if bg:
                    item.setBackground(self._blend(T.PANEL, bg, 0.35))
                    item.setForeground(QColor(T.TEXT))
            # 더블클릭하면 이 경기 스쿼드를 바로 찾을 수 있게 match_id 를 붙인다.
            date_item = self.table.item(r, 0)
            if date_item:
                date_item.setData(Qt.ItemDataRole.UserRole, m.match_id)
        self.table.setSortingEnabled(True)
        self._apply_match_filter()

    def _on_match_double_clicked(self, item) -> None:
        date_item = self.table.item(item.row(), 0)
        opp_item = self.table.item(item.row(), 3)
        if not date_item or not opp_item:
            return
        match_id = date_item.data(Qt.ItemDataRole.UserRole)
        detail = next((d for d in self._details if d.get("matchId") == match_id), None)
        if detail is None:
            return
        opponent = opp_item.text()
        found = st.opponent_squad([detail], self._ouid, opponent)
        if found is None:
            return
        players, match_date, result = found
        self._show_opponent_squad(opponent, players, match_date, result)

    @staticmethod
    def _blend(base_hex: str, target_hex: str, mix: float) -> QColor:
        base, target = QColor(base_hex), QColor(target_hex)
        return QColor(
            int(base.red() + (target.red() - base.red()) * mix),
            int(base.green() + (target.green() - base.green()) * mix),
            int(base.blue() + (target.blue() - base.blue()) * mix),
        )

    def _render_opponents(self, matches: list[MatchSummary]) -> None:
        rows = []
        for s in opponent_stats(matches):
            rows.append([
                s.nickname, wdl_text(s.win, s.draw, s.lose),
                (f"{s.win_rate:.1f}%", s.win_rate),
                (f"{s.avg_goals_for:.2f}", s.avg_goals_for),
                (f"{s.avg_goals_against:.2f}", s.avg_goals_against),
                s.last_date,
            ])
        self._fill(self.tbl_opponents, rows)
        self._apply_opponent_filter()

    def _on_opponent_double_clicked(self, item) -> None:
        row = item.row()
        name_item = self.tbl_opponents.item(row, 0)
        if not name_item:
            return
        nickname = name_item.text()
        _, details = self._slice()
        found = st.opponent_squad(details, self._ouid, nickname)
        if found is None:
            QMessageBox.information(
                self, "상대 스쿼드",
                "표시 구간(시작~끝) 안에서 이 상대와 붙은 경기를 찾지 못했습니다.")
            return
        players, match_date, result = found
        self._show_opponent_squad(nickname, players, match_date, result)

    def _season_name(self, sp_id: int) -> str:
        info = self._seasons.get(st.season_id_of(sp_id))
        return info.get("className", "-") if info else "-"

    def _position_opp_rows(self, players: list[st.PositionOpponent]) -> list[list]:
        return [[p.position, f"{p.name} ({self._season_name(p.sp_id)})",
                (str(p.count), p.count), (f"{p.rate:.1f}%", p.rate)]
               for p in players]

    @staticmethod
    def _tint_position_rows(table: QTableWidget,
                            players: list[st.PositionOpponent]) -> None:
        """스쿼드 화면(PitchWidget)과 같은 라인 색상으로 행 전체를 물들인다."""
        for r, p in enumerate(players):
            bg = MainWindow._blend(T.PANEL, PitchWidget._accent_for(p.pos_code), 0.28)
            for c in range(table.columnCount()):
                item = table.item(r, c)
                if item:
                    item.setBackground(bg)
                    item.setForeground(QColor(T.TEXT))
            # "선수"(1번) 열에 spId 를 붙여 더블클릭 시 선수 카드를 열 수 있게 한다.
            name_item = table.item(r, 1)
            if name_item:
                name_item.setData(Qt.ItemDataRole.UserRole, p.sp_id)

    def _on_player_cell_double_clicked(self, item) -> None:
        """선수 지표·포지션별 최다 상대 표 공용 핸들러 — "선수"(1번) 열에
        UserRole 로 붙여둔 spId 를 읽어 상대 스쿼드 화면과 같은 선수 카드
        다이얼로그를 연다."""
        table = item.tableWidget()
        name_item = table.item(item.row(), 1)
        sp_id = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if isinstance(sp_id, int):
            self._show_player_info(sp_id)

    def _render_position_opponents(self, details: list[dict]) -> None:
        nicknames = None
        color = self.cb_position_color.currentText()
        if color and color != self.POSITION_COLOR_ALL:
            nicknames = {nick for nick, c in self._team_colors.items() if c == color}
        players = st.opponent_position_players(
            details, self._ouid,
            name_of=lambda i: self._names.get(i, str(i)),
            pos_name=lambda p: self._positions.get(p, str(p)),
            nicknames=nicknames)
        # enable_sort=False 로 채우고 이 표는 정렬 자체를 계속 꺼 둔다(위
        # setSortingEnabled(False) 참고) — 공격→미들→수비→GK 순서·줄별 색이
        # 이 표의 핵심이라 헤더 클릭 정렬이 그 순서를 흐트러뜨리면 안 된다.
        self._fill(self.tbl_position_opp, self._position_opp_rows(players),
                  enable_sort=False)
        self._tint_position_rows(self.tbl_position_opp, players)

    # ── 팀컬러 (근사치 — top 10,000 랭커 안에서 찾아지는 상대만) ──────────
    def _team_color_of(self, nickname: str) -> str | None:
        return self._team_colors.get(nickname) or None

    def _render_teamcolor_tabs(self, matches: list[MatchSummary],
                               details: list[dict]) -> None:
        stats_list = st.team_color_stats(matches, self._team_color_of,
                                         team_value_of=self._team_values.get)
        # 숫자 열은 (표시 문자열, 정렬용 값) 튜플로 줘야 SortableItem 이
        # "10"을 "9"보다 뒤로 보내는 문자열 정렬 대신 실제 크기로 정렬한다
        # (안 그러면 헤더 클릭 정렬이 9,88,80,8,8,8,75... 식으로 깨진다).
        rate_rows = [[s.team_color, (str(s.games), s.games),
                     (str(s.win), s.win), (str(s.draw), s.draw),
                     (str(s.lose), s.lose),
                     (f"{s.win_rate:.1f}%", s.win_rate)]
                    for s in stats_list]
        self._fill(self.tbl_teamcolor_rate, rate_rows)
        # 표를 다시 채울 때마다(범위 변경·새 경기 확인 등) 사용자가 전에
        # 다른 열로 정렬해 뒀어도 "경기 많은 순"으로 되돌린다 — 이 표는
        # 열어보면 항상 이 기준으로 보이는 게 목적이라, 헤더 클릭 정렬
        # 상태가 재렌더 사이에 남아 있으면 안 된다.
        self.tbl_teamcolor_rate.sortByColumn(
            self.TEAMCOLOR_RATE_COLUMNS.index("경기"), Qt.SortOrder.DescendingOrder)
        # 팀가치는 넥슨식 축약("10경 9,631조")으로 보여주고 정렬은 원 단위로.
        # 팀가치를 아는 상대가 없는 팀컬러(구버전 캐시 등)는 "-" — 다음
        # 조회(TTL 만료·백필) 때 채워진다.
        def value_cell(v):
            return (ranker.format_team_value(v), v) if v is not None else ("-", -1)

        rank_rows = [[(str(i), i), s.team_color, (str(s.games), s.games),
                     value_cell(s.avg_value), value_cell(s.min_value),
                     value_cell(s.max_value)]
                    for i, s in enumerate(stats_list, start=1)]
        self._fill(self.tbl_teamcolor_rank, rank_rows)
        self.tbl_teamcolor_rank.sortByColumn(
            self.TEAMCOLOR_RANK_COLUMNS.index("만난 횟수"), Qt.SortOrder.DescendingOrder)
        # 새로 알게 된 팀컬러가 있으면 "포지션별 최다 상대" 필터 목록도 같이 넓힌다.
        self._refresh_position_color_options()
        self._render_position_opponents(details)

    def _on_fetch_team_colors(self) -> None:
        """검색이 끝나면 자동으로도 호출된다(_on_loaded) — DB 캐시(TTL 30일)
        에 있는 상대는 그걸로 채우고, 정말 처음 보거나 캐시가 오래된 상대만
        넥슨 데이터센터에서 새로 긁는다. 그래서 같은 계정을 다시 보거나
        상대가 겹치는 다른 계정을 봐도 대부분 거의 즉시 끝난다.

        범위는 self._matches(누적 전체)가 아니라 지금 표시 구간(시작~끝)만
        — 계정에 따라 누적 상대가 수천 명이라 전체를 미리 긁으면 첫 조회가
        너무 오래 걸린다. 대신 나중에 범위를 넓히면 그만큼 새로 늘어난
        상대만큼 다시 기다려야 한다(_apply_range 가 이 함수를 다시 부른다)."""
        if self._teamcolor_loader and self._teamcolor_loader.isRunning():
            # 이미 도는 중에 범위가 넓어져 다시 불렸다 — 끝난 뒤(_on_teamcolor_finished)
            # 새로 늘어난 상대까지 마저 조회하도록 재시도를 예약해 둔다.
            self._teamcolor_retry_pending = True
            return
        shown_matches, _ = self._slice()
        missing = sorted({m.opponent for m in shown_matches
                          if m.opponent and m.opponent not in self._team_colors})
        if missing:
            try:
                conn = store.open_db(config.DB_PATH)
                try:
                    for nick, (color, value) in store.load_team_colors(conn, missing).items():
                        self._team_colors[nick] = color
                        self._team_values[nick] = value
                finally:
                    conn.close()
            except Exception:
                pass  # DB 캐시를 못 읽어도 네트워크 조회로 계속 진행

        remaining = {m.opponent for m in shown_matches
                    if m.opponent and m.opponent not in self._team_colors}
        if not remaining:
            matches, details = self._slice()
            self._render_teamcolor_tabs(matches, details)
            return
        # 많이 만난 상대부터 — 값어치 큰 상대가 먼저 채워지고, 진행 중에도
        # 화면을 갱신하니(_on_teamcolor_loaded) 다 끝나기 전에도 유용해진다.
        freq = Counter(m.opponent for m in shown_matches if m.opponent)
        nicknames = sorted(remaining, key=lambda n: -freq[n])
        for b in self._teamcolor_fetch_btns:
            b.setEnabled(False)
        for lb in self._teamcolor_status_labels:
            lb.setText(f"0 / {len(nicknames)} 조회 중…")
        self._teamcolor_pending = nicknames
        self._teamcolor_loaded_count = 0
        self._teamcolor_loader = TeamColorLoader(nicknames)
        self._teamcolor_loader.loaded.connect(self._on_teamcolor_loaded)
        self._teamcolor_loader.progress.connect(self._on_teamcolor_progress)
        self._teamcolor_loader.finished_all.connect(self._on_teamcolor_finished)
        self._teamcolor_loader.start()

    def _on_teamcolor_loaded(self, nickname: str, color: str, value) -> None:
        self._team_colors[nickname] = color
        self._team_values[nickname] = value
        # 다 끝나야만 표가 채워지면 답답하니, 10개 받을 때마다 중간 갱신한다.
        self._teamcolor_loaded_count += 1
        if self._teamcolor_loaded_count % 10 == 0:
            matches, details = self._slice()
            self._render_teamcolor_tabs(matches, details)

    def _on_teamcolor_progress(self, done: int, total: int) -> None:
        for lb in self._teamcolor_status_labels:
            lb.setText(f"{done} / {total} 조회 중…")

    def _on_teamcolor_finished(self) -> None:
        for b in self._teamcolor_fetch_btns:
            b.setEnabled(True)
        fetched = {n: (self._team_colors[n], self._team_values.get(n))
                  for n in self._teamcolor_pending if n in self._team_colors}
        if fetched:
            try:
                conn = store.open_db(config.DB_PATH)
                try:
                    store.save_team_colors(conn, fetched)
                finally:
                    conn.close()
            except Exception:
                pass  # DB 저장이 실패해도 이번 세션 캐시(메모리)는 살아 있다
        found = sum(1 for color, _ in fetched.values() if color)
        for lb in self._teamcolor_status_labels:
            lb.setText(f"상대 {len(self._teamcolor_pending)}명 조회 완료"
                      f"(팀컬러 확인 {found}명)")
        matches, details = self._slice()
        self._render_teamcolor_tabs(matches, details)
        if self._teamcolor_retry_pending:
            self._teamcolor_retry_pending = False
            self._on_fetch_team_colors()  # 조회 도중 넓어진 범위 마저 조회

    def _on_teamcolor_double_clicked(self, item) -> None:
        row = item.row()
        color_item = self.tbl_teamcolor_rank.item(row, 1)
        if not color_item:
            return
        color = color_item.text()
        nicknames = {nick for nick, c in self._team_colors.items() if c == color}
        if not nicknames:
            return
        _, details = self._slice()
        players = st.opponent_position_players(
            details, self._ouid,
            name_of=lambda i: self._names.get(i, str(i)),
            pos_name=lambda p: self._positions.get(p, str(p)),
            nicknames=nicknames)
        self._show_teamcolor_detail(color, players)

    def _show_teamcolor_detail(self, color: str,
                              players: list[st.PositionOpponent]) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{color} — 포지션별 기용률")
        v = QVBoxLayout(dlg)
        tbl = self._make_table(self.POSITION_OPP_COLUMNS)
        tbl.setSortingEnabled(False)  # 이유는 tbl_position_opp 와 동일
        tbl.itemDoubleClicked.connect(self._on_player_cell_double_clicked)
        self._fill(tbl, self._position_opp_rows(players), enable_sort=False)
        self._tint_position_rows(tbl, players)
        v.addWidget(tbl)
        dlg.resize(560, 480)
        dlg.exec()

    def _make_pitch_from_players(self, players: list[dict]
                                 ) -> tuple[PitchWidget, list[int]]:
        """매치 상세의 선수 raw 목록(교체 포함) -> 선발만 배치한 PitchWidget.

        상대 스쿼드 화면·구단주 비교 스쿼드가 공유하는 조립 로직 — 선수
        카드 클릭 연결까지 여기서 끝낸다."""
        starters, sp_ids = [], []
        for p in players:
            pos = p.get("spPosition")
            sp_id = p.get("spId")
            if not (isinstance(pos, int) and pos in PitchWidget.COORDS):
                continue
            pos_name = self._positions.get(pos, str(pos))
            name = (self._names.get(sp_id, str(sp_id))
                   if isinstance(sp_id, int) else "-")
            grade = p.get("spGrade", "-")
            starters.append((pos, pos_name, name, grade, sp_id))
            if isinstance(sp_id, int):
                sp_ids.append(sp_id)

        pitch = PitchWidget(starters)
        # 선수 카드를 클릭하면 그 카드 상세(오버롤·능력치·시세 등)를 새
        # 다이얼로그로 띄운다 — 이 경기 기록의 spGrade 를 같이 넘겨서
        # "시세" 탭에서 지금 강화 단계를 짚어줄 수 있게 한다.
        grade_by_sp_id = {sid: g for _, _, _, g, sid in starters if isinstance(sid, int)}
        pitch.player_clicked.connect(
            lambda sid: self._show_player_info(sid, grade_by_sp_id.get(sid)))
        return pitch, sp_ids

    def _show_opponent_squad(self, nickname: str, players: list[dict],
                             match_date: str, result: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{nickname} 스쿼드")
        dlg.resize(600, 760)
        v = QVBoxLayout(dlg)

        formation = st.formation_of(players)
        title = QLabel(f"{nickname}  ·  {formation}  ·  {result}  ·  {match_date}")
        title.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        title.setWordWrap(True)
        v.addWidget(title)

        pitch, sp_ids = self._make_pitch_from_players(players)
        v.addWidget(pitch, 1)

        # 얼굴 이미지·시즌 아이콘은 백그라운드로 — 다이얼로그는 모달이지만
        # Qt 이벤트 루프는 계속 돌아서 시그널이 도착하는 대로 칩에 채워진다.
        loader = ImageLoader(sp_ids, self._img_cache_dir)
        loader.loaded.connect(pitch.set_face)
        loader.start()

        season_entries = []
        for sp_id in sp_ids:
            season_id = st.season_id_of(sp_id)
            info = self._seasons.get(season_id)
            if info and info.get("seasonImg"):
                season_entries.append((sp_id, season_id, info["seasonImg"]))
        season_loader = SeasonIconLoader(season_entries, self._season_icon_dir)
        season_loader.loaded.connect(pitch.set_season_icon)
        season_loader.start()

        dlg.exec()
        loader.cancel()
        loader.wait(500)
        season_loader.cancel()
        season_loader.wait(500)

    PLAYERCARD_IMG_DIR_NAME = "player_card_images"

    def _show_player_info(self, sp_id: int, current_grade=None) -> None:
        """선수 카드 상세(넥슨 데이터센터 스크래핑, playerinfo.py) 다이얼로그.

        네트워크 조회라 절대 UI 스레드에서 안 하고 PlayerInfoLoader 로
        돌린다 — 다이얼로그를 먼저 "불러오는 중" 상태로 띄우고, 조회가
        끝나면(모달이어도 Qt 이벤트 루프는 돌아서 시그널이 도착한다) 내용을
        채운다."""
        dlg = QDialog(self)
        dlg.setWindowTitle("선수 정보")
        dlg.resize(560, 720)
        v = QVBoxLayout(dlg)
        status = QLabel("불러오는 중…")
        status.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(status)
        body = QWidget()
        v.addWidget(body, 1)

        img_dir = config.CACHE_DIR / self.PLAYERCARD_IMG_DIR_NAME
        info_loader = PlayerInfoLoader(sp_id)
        img_loader: UrlImageLoader | None = None

        def on_loaded(info: playerinfo.PlayerInfo) -> None:
            nonlocal img_loader
            status.setText("")
            widgets_by_url = self._fill_player_info(body, info, current_grade)
            urls = [u for u in widgets_by_url if u]
            if urls:
                img_loader = UrlImageLoader(urls, img_dir)
                img_loader.loaded.connect(
                    lambda url, path: self._set_player_info_image(widgets_by_url, url, path))
                img_loader.start()

        def on_failed(msg: str) -> None:
            status.setText(f"불러오지 못했습니다 — {msg}")

        info_loader.loaded.connect(on_loaded)
        info_loader.failed.connect(on_failed)
        info_loader.start()

        dlg.exec()
        info_loader.wait(3000)
        if img_loader is not None:
            img_loader.cancel()
            img_loader.wait(500)
        if self._ability_sim_loader and self._ability_sim_loader.isRunning():
            self._ability_sim_loader.wait(2000)
        if self._position_ovr_loader and self._position_ovr_loader.isRunning():
            self._position_ovr_loader.wait(2000)

    @staticmethod
    def _set_player_info_image(widgets_by_url: dict[str, QLabel], url: str, path: str) -> None:
        lb = widgets_by_url.get(url)
        if lb is None:
            return
        pm = QPixmap(path)
        if not pm.isNull():
            lb.setPixmap(pm)

    def _fill_player_info(self, body: QWidget, info: playerinfo.PlayerInfo,
                          current_grade=None) -> dict[str, QLabel]:
        """body 위젯을 선수 카드 내용으로 채우고, 나중에 이미지가 도착하면
        채울 수 있게 {URL: 그 이미지를 받을 QLabel} 맵을 돌려준다."""
        widgets_by_url: dict[str, QLabel] = {}
        outer = QVBoxLayout(body)

        header = QHBoxLayout()
        photo = QLabel()
        photo.setFixedSize(96, 96)
        photo.setScaledContents(True)
        photo.setStyleSheet(f"background: {T.PANEL_2}; border-radius: 8px;")
        header.addWidget(photo)
        if info.photo_url:
            widgets_by_url[info.photo_url] = photo

        text_col = QVBoxLayout()
        name_row = QHBoxLayout()
        flag = QLabel()
        flag.setFixedSize(20, 14)
        flag.setScaledContents(True)
        name_row.addWidget(flag)
        if info.nation_flag_url:
            widgets_by_url[info.nation_flag_url] = flag
        season = QLabel()
        season.setFixedSize(28, 20)
        season.setScaledContents(True)
        name_row.addWidget(season)
        if info.season_icon_url:
            widgets_by_url[info.season_icon_url] = season
        name_lb = QLabel(f"{info.name}  ·  {info.position}  ·  OVR {info.ovr or NA}")
        nf = QFont()
        nf.setPointSize(14)
        nf.setBold(True)
        name_lb.setFont(nf)
        name_row.addWidget(name_lb)
        name_row.addStretch(1)
        text_col.addLayout(name_row)

        sub_lb = QLabel(f"{info.nation}  ·  {info.height}  ·  {info.weight}  ·  "
                        f"{info.body_type}  ·  주발 {info.strong_foot}(약발 {info.weak_foot})")
        sub_lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        text_col.addWidget(sub_lb)

        stars = "★" * info.skill_moves + "☆" * max(info.skill_moves_max - info.skill_moves, 0)
        fame_lb = QLabel(f"명성 {info.fame}  ·  개인기 {stars}")
        fame_lb.setStyleSheet(f"color: {T.YELLOW};")
        text_col.addWidget(fame_lb)
        text_col.addStretch(1)
        header.addLayout(text_col, 1)
        outer.addLayout(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_ability_tab(info, current_grade), "능력치")
        tabs.addTab(self._build_trait_tab(info, widgets_by_url), "특징")
        tabs.addTab(self._build_price_tab(info, current_grade), "시세")
        tabs.addTab(self._build_club_history_tab(info), "클럽 경력")
        tabs.addTab(self._build_position_ovr_tab(info), "포지션별 오버롤")
        outer.addWidget(tabs, 1)
        return widgets_by_url

    # PC 데이터센터 축구장 그림과 같은 줄 구성(공격 → 골키퍼 순)
    POSITION_OVR_ROWS = [
        ("공격", ["ST", "CF", "LW", "RW"]),
        ("미드필더", ["CAM", "CM", "CDM", "LM", "RM"]),
        ("수비", ["CB", "SW", "LB", "RB", "LWB", "RWB"]),
        ("골키퍼", ["GK"]),
    ]

    def _build_position_ovr_tab(self, info: playerinfo.PlayerInfo) -> QWidget:
        """포지션별 오버롤 — PC 데이터센터 선수 상세의 축구장 그림에 있는
        16개 값. fetch_player_ability 응답의 ovr_set 블록에서 오며, 카드
        기본 상태(1강·적응도 +1) 기준으로 한 번만 조회한다."""
        w = QWidget()
        v = QVBoxLayout(w)
        status = QLabel("불러오는 중…")
        status.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(status)
        grid = QGridLayout()
        grid.setSpacing(10)
        v.addLayout(grid)
        v.addStretch(1)

        def render(sim: playerinfo.AbilitySim) -> None:
            try:
                if not sim.position_ovrs:
                    status.setText("포지션별 오버롤 정보가 없습니다.")
                    return
                status.setText("1강 · 적응도 +1 기준")
                for r, (cap_text, codes) in enumerate(self.POSITION_OVR_ROWS):
                    cap = QLabel(cap_text)
                    cap.setStyleSheet(f"color: {T.TEXT_DIM};")
                    grid.addWidget(cap, r, 0)
                    for c, code in enumerate(codes, start=1):
                        val = sim.position_ovrs.get(code)
                        if val is None:
                            continue
                        cell = QLabel(
                            f"<span style='color:{T.TEXT_DIM}'>{code}</span> "
                            f"<b style='color:{playerinfo.stat_color(val)}'>{val}</b>")
                        grid.addWidget(cell, r, c)
            except RuntimeError:
                pass  # 다이얼로그가 닫힌 뒤 응답 도착 — 무시

        def on_failed(msg: str) -> None:
            try:
                status.setText(f"조회 실패 — {msg}")
            except RuntimeError:
                pass

        loader = AbilitySimLoader(info.sp_id, 1, playerinfo.ADAPT_DEFAULT,
                                  0, 0, 0, 0, 0)
        self._position_ovr_loader = loader
        loader.loaded.connect(render)
        loader.failed.connect(on_failed)
        loader.start()
        return w

    def _build_ability_tab(self, info: playerinfo.PlayerInfo,
                           current_grade=None) -> QWidget:
        """강화·적응도·팀컬러(소속/강화/관계) 시뮬레이터 — PC 데이터센터의
        "선수 정보 변경" 팝업과 같은 조작을, 그 팝업이 부르는 것과 같은
        엔드포인트(playerinfo.fetch_player_ability)로 그대로 재현한다.
        로컬 계산이 아니라 매 조작마다 넥슨 서버에 다시 물어보므로(팀컬러
        보너스 조합표를 넥슨이 공개하지 않아 근사할 방법이 없다) 콤보를
        바꿀 때마다 짧게 "계산 중…" 이 뜬다."""
        w = QWidget()
        v = QVBoxLayout(w)
        sp_id = info.sp_id

        status = QLabel("")
        status.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(status)

        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("강화"))
        cb_strong = NoScrollComboBox()
        for lvl in playerinfo.STRONG_LEVELS:
            cb_strong.addItem(f"{lvl}강", lvl)
        # 기본은 1강(1카) — 홈페이지 첫 화면과 같은 기준으로 보여준다.
        cb_strong.setCurrentIndex(cb_strong.findData(1))
        opt_row.addWidget(cb_strong)
        opt_row.addSpacing(12)
        opt_row.addWidget(QLabel("적응도"))
        cb_adapt = NoScrollComboBox()
        for a in playerinfo.ADAPT_CHOICES:
            cb_adapt.addItem(f"+{a}", a)
        cb_adapt.setCurrentIndex(cb_adapt.findData(playerinfo.ADAPT_DEFAULT))
        opt_row.addWidget(cb_adapt)
        opt_row.addStretch(1)
        v.addLayout(opt_row)

        # 팀컬러 선택지는 fetch_player_ability 응답에 이 선수 전용으로 이미
        # 필터링돼 들어온다(수 개 수준) — 첫 응답이 오기 전까지만 비활성.
        # 레벨 UI는 없다: 소속 팀컬러는 항상 그 팀컬러의 최대 레벨로 자동
        # 조회하고(아래 club_max_lv), 강화 팀컬러는 항목 자체에 레벨이
        # 박혀 있으며("Lv.1 백금빛 물결"), 관계 팀컬러는 레벨 개념이 없다.
        def make_teamcolor_combo() -> NoScrollComboBox:
            combo = NoScrollComboBox()
            combo.addItem("(선택 안 함)", 0)
            combo.setCurrentIndex(0)
            combo.setEnabled(False)
            return combo

        tc_row1 = QHBoxLayout()
        tc_row1.addWidget(QLabel("소속 팀컬러"))
        cb_tc = make_teamcolor_combo()
        tc_row1.addWidget(cb_tc, 1)
        v.addLayout(tc_row1)

        tc_row2 = QHBoxLayout()
        tc_row2.addWidget(QLabel("강화 팀컬러"))
        cb_tc_en = make_teamcolor_combo()
        tc_row2.addWidget(cb_tc_en, 1)
        v.addLayout(tc_row2)

        tc_row3 = QHBoxLayout()
        tc_row3.addWidget(QLabel("관계 팀컬러"))
        cb_tc_feature = make_teamcolor_combo()
        tc_row3.addWidget(cb_tc_feature, 1)
        v.addLayout(tc_row3)

        ovr_lb = QLabel("OVR -")
        ovr_lb.setStyleSheet(f"color: {T.GREEN}; font-weight: bold;")
        v.addWidget(ovr_lb)

        group_cards: dict[str, StatCard] = {}
        grow_row = QHBoxLayout()
        for name in playerinfo.GROUP_NAMES:
            card = StatCard(name, T.GREEN)
            group_cards[name] = card
            grow_row.addWidget(card)
        v.addLayout(grow_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setSpacing(6)
        v.addWidget(scroll, 1)
        scroll.setWidget(grid_host)

        # 소속 팀컬러별 최대 레벨 — 팀컬러마다 다르고(대부분 4, Winning
        # Streak 는 3) 범위 밖을 보내면 넥슨이 에러 페이지를 돌려주므로,
        # 모르는 팀컬러는 일단 Lv.1(항상 유효)로 조회하고 응답의 레벨
        # 선택지(sim.club_levels)에서 최대치를 배운 뒤 자동 재조회한다.
        club_max_lv: dict[int, int] = {}

        # 매 응답마다 콤보를 그 선수 전용 목록으로 다시 채운다. 강화 팀컬러
        # 목록이 강화 단계에 따라 달라지므로 "한 번 채우고 끝"이 아니다.
        # blockSignals 로 재채움 중 currentIndexChanged → refresh() 무한루프를
        # 막고, 현재 선택은 새 목록에 남아 있으면 유지한다.
        def rebuild_combo(combo: NoScrollComboBox,
                          options: list[tuple]) -> None:  # (userData, 표시명)
            keep = combo.currentData() or 0
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(선택 안 함)", 0)
            for data, name in options:
                combo.addItem(name, data)
            # findData 는 튜플 userData 비교가 미덥지 않아 직접 훑는다.
            idx = next((i for i in range(combo.count())
                        if combo.itemData(i) == keep), 0)
            combo.setCurrentIndex(idx)
            combo.setEnabled(True)
            combo.blockSignals(False)

        def render(sim: playerinfo.AbilitySim) -> None:
            try:
                status.setText("")
                rebuild_combo(cb_tc, sim.club_options)
                rebuild_combo(cb_tc_en, [((eid, lv), label)
                                         for eid, lv, label in sim.enhance_options])
                rebuild_combo(cb_tc_feature, sim.feature_options)
                # 선택된 소속 팀컬러의 최대 레벨을 처음 배웠으면 그 레벨로 재조회
                club_id = cb_tc.currentData() or 0
                if club_id and sim.club_levels:
                    best = max(sim.club_levels)
                    if club_max_lv.get(club_id) != best:
                        club_max_lv[club_id] = best
                        refresh()
                        return
                ovr_lb.setText(f"OVR {sim.ovr if sim.ovr is not None else NA}")
                for name, card in group_cards.items():
                    gv = sim.groups.get(name)
                    card.set(str(gv if gv is not None else NA))
                    if gv is not None:
                        card.value.setStyleSheet(
                            f"color: {playerinfo.stat_color(gv)}; border: none;")
                while grid.count():
                    item = grid.takeAt(0)
                    if item.layout():
                        self._clear(item.layout())
                        item.layout().deleteLater()
                cols = 3
                for i, (name, val) in enumerate(sim.abilities.items()):
                    r, c = divmod(i, cols)
                    lb = QLabel(name)
                    lb.setStyleSheet(f"color: {T.TEXT_DIM};")
                    vb = QLabel(str(val))
                    # 홈페이지와 같은 구간 기준으로 값에 색을 입힌다
                    vb.setStyleSheet(
                        f"color: {playerinfo.stat_color(val)}; font-weight: bold;")
                    pair = QHBoxLayout()
                    pair.addWidget(lb)
                    pair.addStretch(1)
                    pair.addWidget(vb)
                    grid.addLayout(pair, r, c)
            except RuntimeError:
                pass  # 다이얼로그가 이미 닫힌 뒤 응답이 도착함 — 무시

        def on_failed(msg: str) -> None:
            try:
                status.setText(f"능력치 계산 실패 — {msg}")
            except RuntimeError:
                pass

        def refresh() -> None:
            if self._ability_sim_loader and self._ability_sim_loader.isRunning():
                self._ability_sim_loader.loaded.disconnect()
                self._ability_sim_loader.failed.disconnect()
            status.setText("계산 중…")
            club_id = cb_tc.currentData() or 0
            en = cb_tc_en.currentData() or (0, 0)  # (id, lv) — 항목에 레벨 내장
            loader = AbilitySimLoader(
                sp_id, cb_strong.currentData(), cb_adapt.currentData(),
                club_id, club_max_lv.get(club_id, 1) if club_id else 0,
                en[0], en[1], cb_tc_feature.currentData() or 0)
            self._ability_sim_loader = loader
            loader.loaded.connect(render)
            loader.failed.connect(on_failed)
            loader.start()

        cb_strong.currentIndexChanged.connect(refresh)
        cb_adapt.currentIndexChanged.connect(refresh)
        cb_tc.currentIndexChanged.connect(refresh)
        cb_tc_en.currentIndexChanged.connect(refresh)
        cb_tc_feature.currentIndexChanged.connect(refresh)

        refresh()
        return w

    @staticmethod
    def _build_trait_tab(info: playerinfo.PlayerInfo,
                         widgets_by_url: dict[str, QLabel]) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        v = QVBoxLayout(host)
        if not info.traits:
            lb = QLabel("이 카드에 등록된 특성이 없습니다.")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            v.addWidget(lb)
        for trait in info.traits:
            row = QHBoxLayout()
            icon = QLabel()
            icon.setFixedSize(28, 28)
            icon.setScaledContents(True)
            row.addWidget(icon)
            if trait.icon_url:
                widgets_by_url[trait.icon_url] = icon
            text_col = QVBoxLayout()
            name_lb = QLabel(trait.name)
            name_lb.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            text_col.addWidget(name_lb)
            if trait.desc:
                desc_lb = QLabel(trait.desc)
                desc_lb.setWordWrap(True)
                desc_lb.setStyleSheet(f"color: {T.TEXT_DIM};")
                text_col.addWidget(desc_lb)
            row.addLayout(text_col, 1)
            v.addLayout(row)
        v.addStretch(1)
        scroll.setWidget(host)
        outer = QVBoxLayout(w)
        outer.addWidget(scroll)
        return w

    @staticmethod
    def _build_price_tab(info: playerinfo.PlayerInfo, current_grade=None) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setSpacing(6)
        cols = 2  # 시세 문자열이 길어서("3,660,000,000,000 BP") 3열이면 가로 스크롤이 생긴다
        for i, grade in enumerate(sorted(info.prices)):
            r, c = divmod(i, cols)
            price = info.prices[grade]
            cell = QFrame()
            cell.setStyleSheet(
                f"QFrame {{ background: {T.PANEL}; border: 1px solid "
                f"{T.GREEN if grade == current_grade else T.BORDER}; border-radius: 6px; }}")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(8, 6, 8, 6)
            grade_lb = QLabel(f"{grade}강" if grade else "기본")
            grade_lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            price_lb = QLabel(price)
            price_lb.setWordWrap(True)
            price_lb.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            cv.addWidget(grade_lb)
            cv.addWidget(price_lb)
            grid.addWidget(cell, r, c)
        scroll.setWidget(host)
        outer = QVBoxLayout(w)
        outer.addWidget(scroll)
        return w

    @staticmethod
    def _build_club_history_tab(info: playerinfo.PlayerInfo) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        v = QVBoxLayout(host)
        if not info.club_history:
            lb = QLabel("클럽 경력 정보가 없습니다.")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            v.addWidget(lb)
        for stint in info.club_history:
            row = QHBoxLayout()
            period_lb = QLabel(stint.period)
            period_lb.setFixedWidth(110)
            period_lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            club_lb = QLabel(stint.club + ("  (임대)" if stint.loan else ""))
            club_lb.setStyleSheet(f"color: {T.TEXT};")
            row.addWidget(period_lb)
            row.addWidget(club_lb, 1)
            v.addLayout(row)
        v.addStretch(1)
        scroll.setWidget(host)
        outer = QVBoxLayout(w)
        outer.addWidget(scroll)
        return w

    def _render_trend(self, matches: list[MatchSummary]) -> None:
        """승률 그래프 — '적용 경기 수'(시작~끝)가 아니라 '적용 일수'
        (sp_trend_days)로 그린다. 하루 100경기 넘게 뛰는 계정은 경기 수
        구간이 하루도 안 될 수 있어서, 누적 전체(self._matches)를 기준으로
        날짜만 계산한다 — _render_all 의 호출도 그래서 self._matches 그대로."""
        dated = [m for m in matches if m.match_date is not None]
        if dated:
            earliest = min(m.match_date for m in dated).date()
            latest = max(m.match_date for m in dated).date()
            span_days = (latest - earliest).days + 1
            span_text = (f"전체 저장 기간: {span_days}일 "
                        f"({earliest:%Y-%m-%d} ~ {latest:%Y-%m-%d})")
        else:
            span_days = 1
            span_text = ""

        self.sp_trend_days.blockSignals(True)
        self.sp_trend_days.setRange(1, max(span_days, 1))
        if self._trend_reset_pending:
            self.sp_trend_days.setValue(min(30, span_days) or 1)
            self._trend_reset_pending = False
        self.sp_trend_days.blockSignals(False)
        self.lb_trend_span.setText(span_text)

        days = self.sp_trend_days.value()
        self.gb_trend.setTitle(f"최근 {days}일 승률 추이")
        self._trend_periods = win_rate_trend(matches, days=days)
        self.trend_chart.set_points(
            [(p.label, p.win_rate, p.games) for p in self._trend_periods])

        # 등급 추이 — 승률 추이와 같은 "최근 N일" 구간으로 자른다.
        div_points = st.division_trend(self._details, self._ouid)
        if div_points:
            latest = max(t for t, _ in div_points).date()
            cutoff = latest - timedelta(days=days - 1)
            shown = [(f"{t:%m/%d}", div) for t, div in div_points
                     if t.date() >= cutoff]
        else:
            shown = []
        self.gb_division.setTitle(f"최근 {days}일 등급 추이")
        self.division_chart.set_data(shown, self._division_names)

        if self.tabs.currentIndex() == self.TAB_TREND:
            self._show_trend_summary()

    def _on_tab_changed(self, index: int) -> None:
        if index == self.TAB_TREND:
            self._show_trend_summary()
        else:
            self._show_range_summary()

    def _show_trend_summary(self) -> None:
        """승률 그래프 탭에서는 상단 카드를 선택한 기간의 최고·평균·최저 승률로."""
        periods = getattr(self, "_trend_periods", [])
        days = self.sp_trend_days.value()
        rates = [p.win_rate for p in periods if p.games]
        total_games = sum(p.games for p in periods)
        total_win = sum(p.win for p in periods)
        avg_rate = (total_win / total_games * 100) if total_games else 0.0
        self.card_record.set_title("최고 승률")
        self.card_record.set(f"{max(rates):.1f}%" if rates else NA)
        self.card_rate.set_title("평균 승률")
        self.card_rate.set(f"{avg_rate:.1f}%" if total_games else NA)
        self.card_gf.set_title("최저 승률")
        self.card_gf.set(f"{min(rates):.1f}%" if rates else NA)
        self.card_ga.set_title(f"{days}일 경기수")
        self.card_ga.set(f"{total_games}경기")

    def _show_range_summary(self) -> None:
        """다른 탭에서는 원래대로 — 표시 구간(시작~끝) 전적."""
        matches, _ = self._slice()
        s = summarize(matches)
        self.card_record.set_title("전적")
        self.card_record.set(wdl_text(s.win, s.draw, s.lose))
        self.card_rate.set_title("승률")
        self.card_rate.set(f"{s.win_rate:.1f}%")
        self.card_gf.set_title("평균 득점")
        self.card_gf.set(f"{s.avg_goals_for:.2f}")
        self.card_ga.set_title("평균 실점")
        self.card_ga.set(f"{s.avg_goals_against:.2f}")
        self._render_streak(matches)

    def _render_streak(self, matches: list[MatchSummary]) -> None:
        kind, n = current_streak(matches)
        best_win, best_lose = longest_streaks(self._matches)
        self.card_streak.setToolTip(
            f"최장 연승 {best_win} · 최장 연패 {best_lose} (누적 전체 기준)")
        color = {"승": T.GREEN, "패": T.RED, "무": T.TEXT_DIM}.get(kind, T.TEXT_DIM)
        self.card_streak.set_color(color)
        self.card_streak.set(f"{n}{kind}" if kind else NA)

    def _render_players(self, details: list[dict]) -> None:
        players = st.aggregate_players(
            details, self._ouid,
            name_of=lambda i: self._names.get(i, str(i)),
            pos_name=lambda p: self._positions.get(p, str(p)))
        rows = []
        for p in players:
            rows.append([
                p.position, p.name, (f"{p.grade}", p.grade),
                (f"{p.games}", p.games), (f"{p.win_rate:.1f}", p.win_rate),
                (f"{p.attack_power:.1f}", p.attack_power),
                (f"{p.defense_power:.1f}", p.defense_power),
                (f"{p.expected_goal_rate:.1f}", p.expected_goal_rate),
                (f"{p.attack_point}", p.attack_point),
                (f"{p.goal}", p.goal), (f"{p.assist}", p.assist),
                (f"{p.pass_rate:.1f}", p.pass_rate),
                (f"{p.dribble_rate:.1f}", p.dribble_rate),
                (f"{p.aerial_rate:.1f}", p.aerial_rate),
                (f"{p.intercept}", p.intercept),
                (f"{p.tackle_rate:.1f}", p.tackle_rate),
                (f"{p.block_rate:.1f}", p.block_rate),
                (f"{p.save_power:.1f}", p.save_power),
                (f"{p.rating:.2f}", p.rating),
            ])
        # enable_sort=False — 재검색(2번째 이후 렌더)에서는 헤더에 이전 정렬
        # 상태(공격력 내림차순)가 남아 있어서, 여기서 정렬을 바로 켜면 Qt가
        # 채우자마자 그 상태로 재정렬해버린다. 그러면 아래 tint/데이터 루프가
        # "채운 순서 = players 순서"라고 믿고 매기는 게 틀어져 엉뚱한 행에
        # 색이 칠해진다(실제로 겪은 버그). 후처리를 다 끝낸 뒤에만 켠다.
        self._fill(self.tbl_players, rows, enable_sort=False)
        self._fit_columns_to_content(self.tbl_players, extra={1: 26})
        # 공격력(5열)·수비력(6열)에 값 크기만큼 색을 입혀 강조 — 빨강/파랑.
        atk_max = max((p.attack_power for p in players), default=1) or 1
        def_max = max((p.defense_power for p in players), default=1) or 1
        for r, p in enumerate(players):
            self._tint(self.tbl_players.item(r, 5), p.attack_power, atk_max, T.RED)
            self._tint(self.tbl_players.item(r, 6), p.defense_power, def_max, T.BLUE)
            name_item = self.tbl_players.item(r, 1)
            if name_item:
                name_item.setData(Qt.ItemDataRole.UserRole, p.sp_id)
        self.tbl_players.sortByColumn(5, Qt.SortOrder.DescendingOrder)
        self.tbl_players.setSortingEnabled(True)
        self._load_season_icons([p.sp_id for p in players], self.tbl_players, 1,
                                "_table_season_loader")

    def _load_season_icons(self, sp_ids: list[int], table: QTableWidget,
                           name_col: int, holder: str) -> None:
        """선수 얼굴 대신 시즌(카드 클래스) 아이콘을 표의 name_col 열에 채운다 —
        PitchWidget 스쿼드 화면의 SeasonIconLoader 와 같은 방식(같은 시즌은 한 번만).

        표마다 로더를 따로 두라고 holder(필드 이름)를 받는다 — 하나를 공유하면
        선수 지표·결정력 표가 같은 렌더에서 서로의 로더를 취소해버린다."""
        loader = getattr(self, holder)
        if loader and loader.isRunning():
            loader.cancel()
            loader.wait(500)
        entries = []
        for sp_id in sp_ids:
            season_id = st.season_id_of(sp_id)
            icon_url = self._seasons.get(season_id, {}).get("seasonImg")
            if icon_url:
                entries.append((sp_id, season_id, icon_url))
        loader = SeasonIconLoader(entries, self._season_icon_dir)
        loader.loaded.connect(
            lambda sid, path, t=table, c=name_col: self._apply_season_icon(t, c, sid, path))
        setattr(self, holder, loader)
        loader.start()

    @staticmethod
    def _apply_season_icon(table: QTableWidget, name_col: int,
                           sp_id: int, path: str) -> None:
        icon = QIcon(QPixmap(path))
        for r in range(table.rowCount()):
            item = table.item(r, name_col)
            if item and item.data(Qt.ItemDataRole.UserRole) == sp_id:
                item.setIcon(icon)

    @staticmethod
    def _tint(item, value: float, vmax: float, hexcolor: str) -> None:
        """값이 클수록 진한 배경. 배경은 셀(item)에 붙어 정렬해도 따라간다.

        반투명(alpha) 배경을 쓰면 alternating row 색(짝/홀 행이 다름) 위에
        섞여서 값이 같아도 행마다 진하기가 달라 보였다 — 그래서 알파 대신
        고정 배경색(T.PANEL) 기준으로 직접 섞은 불투명 색을 쓴다.
        """
        if item is None or vmax <= 0:
            return
        frac = max(0.0, min(value / vmax, 1.0))
        # 최소값도 배경과 구분되게 30%부터 시작 — 12%는 T.PANEL(#171b21)이
        # 워낙 어두워서 낮은 값 쪽이 사실상 무색으로 보였다.
        mix = 0.30 + frac * 0.55  # 30%~85% — 옅게~진하게
        item.setBackground(MainWindow._blend(T.PANEL, hexcolor, mix))

    @staticmethod
    def _clear(box) -> None:
        while box.count():
            item = box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_tactics(self, details: list[dict]) -> None:
        mine = st.formation_stats(details, self._ouid, of_opponent=False)
        if mine:
            t = mine[0]
            self.lb_my_formation.setText(
                f"{t.formation}      {t.win_rate:.1f}%      "
                f"{t.games}경기 · {wdl_text(t.win, t.draw, t.lose)}")

        self._clear(self.box_opp)
        for f in st.formation_stats(details, self._ouid):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(f.formation)
            a.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            b = QLabel(f"{f.win_rate:.1f}%")
            b.setStyleSheet(f"color: {T.GREEN}; font-weight: bold;")
            c = QLabel(f"({wdl_text(f.win, f.draw, f.lose)})")
            c.setStyleSheet(f"color: {T.TEXT_DIM};")
            h.addWidget(a)
            h.addStretch(1)
            h.addWidget(b)
            h.addWidget(c)
            self.box_opp.addWidget(row)

        rb = st.result_breakdown(details, self._ouid)
        self._clear(self.box_result)
        for label, wdl in (("전후반", rb.normal), ("연장전", rb.extra),
                           ("승부차기", rb.shootout), ("몰수", rb.forfeit)):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(label)
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            b = QLabel(wdl_text(*wdl))
            b.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            c = QLabel(f"({rate_of(*wdl):.1f}%)")
            c.setStyleSheet(f"color: {T.TEXT_DIM};")
            h.addWidget(a)
            h.addStretch(1)
            h.addWidget(b)
            h.addWidget(c)
            self.box_result.addWidget(row)

        sep = QLabel("시간대별 득실")
        sep.setStyleSheet(f"color: {T.GREEN}; font-weight: bold; padding-top: 3px;")
        self.box_result.addWidget(sep)
        for k in sorted(rb.periods):
            v = rb.periods[k]
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(st.PERIODS.get(k, str(k)))
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            b = QLabel(f"{v.scored}득점")
            b.setStyleSheet(f"color: {T.GREEN}; font-weight: bold;")
            c = QLabel(f"{v.conceded}실점")
            c.setStyleSheet(f"color: {T.RED}; font-weight: bold;")
            h.addWidget(a)
            h.addStretch(1)
            h.addWidget(b)
            h.addWidget(c)
            self.box_result.addWidget(row)

        for box, counter, color in ((self.box_gf, rb.goal_types, T.GREEN),
                                    (self.box_ga, rb.concede_types, T.RED)):
            self._clear(box)
            total = sum(counter.values())
            for name, n in counter.most_common():
                box.addWidget(BarRow(name, n, total, color))
            box.addStretch(1)

    def closeEvent(self, e) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.cancel()
            # 진행 중이던 상세 요청 몇 개가 네트워크 타임아웃까지 갈 수 있어
            # 넉넉히 기다린다. 그래도 안 끝나면 마지막 수단으로 강제 종료 —
            # 좀비 프로세스로 남기느니 낫다(DB 쓰기는 이 지점 이후라 안전).
            if not self._loader.wait(8000):
                self._loader.terminate()
                self._loader.wait(1000)
        if self._teamcolor_loader and self._teamcolor_loader.isRunning():
            self._teamcolor_loader.cancel()
            if not self._teamcolor_loader.wait(3000):
                self._teamcolor_loader.terminate()
                self._teamcolor_loader.wait(1000)
        for icon_loader in (self._table_season_loader, self._finishing_icon_loader):
            if icon_loader and icon_loader.isRunning():
                icon_loader.cancel()
                icon_loader.wait(500)
        if self._compare_loader and self._compare_loader.isRunning():
            self._compare_loader.cancel()
            if not self._compare_loader.wait(8000):
                self._compare_loader.terminate()
                self._compare_loader.wait(1000)
        for loader in self._compare_squad_loaders:
            loader.cancel()
            loader.wait(500)
        if self._ability_sim_loader and self._ability_sim_loader.isRunning():
            self._ability_sim_loader.wait(2000)
        if self._position_ovr_loader and self._position_ovr_loader.isRunning():
            self._position_ovr_loader.wait(2000)
        super().closeEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(T.QSS)
    icon_path = config.asset_path("app_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    if not config.API_KEY:
        QMessageBox.critical(
            None, "API 키 없음",
            f".env 파일에 NEXON_API_KEY가 없습니다.\n\n"
            f"위치: {config.DATA_DIR / '.env'}\n\n"
            "NEXON_API_KEY= 뒤에 발급받은 키를 넣어주세요.",
        )
        return 1

    api = FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)
    win = MainWindow(api)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
