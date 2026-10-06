"""감독모드 전적 분석(예전 이름 피파 전적관리) — PyQt6 앱.

첫 화면은 검색창 하나. 구단주명을 넣으면 왼쪽 메뉴 + 대시보드 화면으로 전환된다.
"""
from __future__ import annotations

import gc
import sqlite3
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QSize, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

import autostart
import charts
import config
import core_api as core
import crashlog
import images
import notice
import playerinfo
import rankcollect
import rankerstats
import ranker
import seasons as sn
import store
import theme as T
import tradecollect
import tray
import updatecheck
from core_api import (
    MatchSummary, current_streak, longest_streaks, opponent_stats, parse_match,
    period_stats, summarize, win_rate_trend,
)
from nexon_api import (
    ATTRIBUTION, KEY_INVALID_CODE, KEY_ISSUE_URL, QUOTA_CODE, FCOnlineAPI, NexonAPIError,
    check_key,
)
from dashboard import DashboardInput, DashboardPage
from widgets import (
    NA, BarRow, Card, DivisionChart, FitTableWidget, GradeBadgeDelegate, NoScrollComboBox, PageTabs,
    PitchWidget, RankerCard, RatioBarRow, RowBorderDelegate, ShotMapWidget, SortableItem,
    StatCard, UpdateCard, VScrollArea, WrapBar, add_shadow, rate_of, sample_note,
    wdl_text, win_rate_bar,
)

PAGE_SIZE = config.MAX_MATCH_LIMIT  # API 가 한 번에 주는 최대치(100)
# 1.2.1 에서 몰수 판정을 종료 유형으로 바꿨다 — 업데이트한 사람이 "정상 종료 수가 왜 줄었나"를 여기서 본다.
FORFEIT_TIP = ("넥슨 경기 종료 유형으로 셉니다 — 상대나 내가 나가서 끝난 경기.\n"
               "내 쪽 기록이 '오류'로 남은 몰수패도 포함해서, 승률 계산에는 아직 안 들어간 경기가 있습니다.")
# 새 경기 상세를 동시에 몇 개 받나. 2026-10-04 서비스 키 실측(250건씩): 6 → 초당 27~30건, 12 → 55,
# 24 → 97, 48 → 124(응답 상위 10% 가 235 → 427ms 로 늘어남). 처음 보는 계정 3천 경기가 113초 → 약 32초.
# 429(호출 한도)를 한 번이라도 받으면 그 검색은 THROTTLED 로 내린다 — 개발 단계 키는 초당 5건이다.
DETAIL_WORKERS = 24
DETAIL_WORKERS_THROTTLED = 2
# 창을 이보다 작게 못 줄인다 — 이 크기에서 글자가 잘리지 않는 것이 기준이다. 화면 반 분할(960)은 18열짜리
# 선수 지표 표가 의미가 없어 지원하지 않는다. ⚠ "1366x768 노트북에도 들어간다"고 적혀 있었지만 제목줄(약 31)과
# 작업 표시줄을 빼면 높이가 모자라 안 들어갔다 — FHD 150% 배율(논리 1280x720)도 같다(2026-10-04).
# 그래서 그런 화면에선 최대화로 열고 최소 높이만 MIN_HEIGHT_SMALL 로 낮춘다(페이지는 세로 스크롤이 된다).
MIN_WINDOW = (1280, 720)
DEFAULT_WINDOW = (1600, 900)  # 선수 지표 표가 스크롤 없이 다 들어차는 실측 크기 근사
MIN_HEIGHT_SMALL = 480  # 레이아웃 하한 434(실측) 위 여유 — 434~640 안에서 잘림 없음 테스트로 정함(434 도 통과)
# 창을 띄우기 전엔 실제 테두리 크기를 모른다 — 판정할 땐 이만큼 더해 보고, 띄운 뒤 실제 값으로 한 번 더 확인한다.
# 윈도우 11 의 제목줄 약 31 + 위아래 테두리, 좌우 테두리.
FRAME_ALLOWANCE = (16, 40)
NARROW_SCREEN_MSG = ("화면 배율이 커서 일부가 잘릴 수 있습니다 — 윈도우 디스플레이 설정에서 배율을 125% 이하로 "
                     "낮추면 전부 보입니다.")


class Tabs(NamedTuple):
    """메뉴 하나의 페이지 안 탭(2.1.1) — sid 는 보던 탭을 기억하는 설정 키(ASCII), tabs 는 ((탭 이름, 빌더 이름), …)."""
    sid: str
    tabs: tuple


@dataclass(frozen=True)
class WindowPlan:
    size: tuple[int, int]      # 보통 상태 크기(최대화하면 풀었을 때 이 크기)
    maximized: bool
    min_size: tuple[int, int]
    narrow: bool               # 폭조차 MIN_WINDOW 가 안 됨 — 일부가 잘릴 수 있다고 한 번 알린다


def initial_window(avail_w: int, avail_h: int) -> WindowPlan:
    """쓸 수 있는 화면 크기(배율 반영 · 작업 표시줄 뺀 것)로 창을 어떻게 열지 정한다.
    모니터 해상도로 정하면 FHD 150% 노트북에서 1600x900 이 화면 밖으로 넘친다."""
    fw, fh = FRAME_ALLOWANCE
    if avail_w >= DEFAULT_WINDOW[0] + fw and avail_h >= DEFAULT_WINDOW[1] + fh:
        return WindowPlan(DEFAULT_WINDOW, False, MIN_WINDOW, False)
    if avail_w >= MIN_WINDOW[0] + fw and avail_h >= MIN_WINDOW[1] + fh:
        return WindowPlan(MIN_WINDOW, False, MIN_WINDOW, False)
    # 최대화로 연다. 최소 폭은 '최대화를 풀어도 테두리까지 화면에 들어가는' 폭으로 — 화면 폭 그대로(1280)면
    # FHD 150% 에서 최대화를 풀 때 테두리만큼 넘쳤다(실측 2px). 그 폭에서도 표가 안 잘리는지는 스모크가 잰다.
    min_w = min(MIN_WINDOW[0], avail_w - fw)
    min_h = min(MIN_HEIGHT_SMALL, avail_h - fh)
    size = (min_w, max(min_h, min(MIN_WINDOW[1], avail_h - fh)))
    return WindowPlan(size, True, (min_w, min_h), avail_w < MIN_WINDOW[0])


def fit_to_screen(widget: QWidget, w: int, h: int) -> None:
    """대화상자를 (w, h) 로 열되 화면보다 크면 화면에 맞춘다 — 선수 카드 560x720 · 스쿼드 600x760 이
    FHD 150% 노트북(창 안쪽 높이 약 657)에서 아래가 잘렸다."""
    ref = _parent_ref(widget)
    screen = ((QApplication.screenAt(ref.center()) if ref is not None else None)
              or widget.screen() or QApplication.primaryScreen())
    if screen is None:
        widget.resize(w, h)
        return
    avail = screen.availableGeometry()
    fw, fh = FRAME_ALLOWANCE
    rw, rh = min(w, avail.width() - fw), min(h, avail.height() - fh)
    widget.resize(rw, rh)
    place_over_parent(widget)


def place_over_parent(widget: QWidget) -> None:
    """대화상자를 메인 창 위 가운데로 — Qt 에 맡기지 않고 직접(화면 밖으로 나가면 그 모니터 안으로).
    띄울 때(fit_to_screen)와 메인 창이 움직일 때(MainWindow.moveEvent) 부른다 — 메인 창만 다른 모니터로 옮겨져
    안내 창이 주 모니터(게임 중)에 홀로 남았다(2026-10-06 사용자 · 실제 윈도우 실측)."""
    ref = _parent_ref(widget)
    if ref is None:
        return
    screen = QApplication.screenAt(ref.center()) or widget.parentWidget().window().screen() \
        or QApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    fw, fh = FRAME_ALLOWANCE
    widget.setScreen(screen)
    x, y = dialog_origin((ref.x(), ref.y(), ref.width(), ref.height()),
                         (avail.x(), avail.y(), avail.width(), avail.height()),
                         widget.width() + fw, widget.height() + fh)
    widget.move(x, y)


def _parent_ref(widget: QWidget):
    """부모 창의 자리(테두리 포함) — 최소화돼 있으면 윈도우가 좌표를 화면 밖(-32000)으로 보내므로 돌아올 자리. 없으면 None."""
    parent = widget.parentWidget()
    win = parent.window() if parent is not None else None
    if win is None:
        return None
    ref = win.normalGeometry() if win.isMinimized() else win.frameGeometry()
    return ref if ref.isValid() and not ref.isEmpty() else None


def dialog_origin(ref: tuple, avail: tuple, fw: int, fh: int) -> tuple[int, int]:
    """테두리 포함 (fw, fh) 크기 대화상자의 왼쪽 위 — ref(메인 창) 가운데에 맞추고 avail(그 모니터) 안으로 민다."""
    rx, ry, rw, rh = ref
    ax, ay, aw, ah = avail
    x = rx + (rw - fw) // 2
    y = ry + (rh - fh) // 2
    x = max(ax, min(x, ax + aw - fw))
    y = max(ay, min(y, ay + ah - fh))
    return x, y


ETA_MIN_DONE = 30  # 이만큼 끝나기 전엔 남은 시간을 안 낸다 — 첫 몇 건은 연결 준비로 느려 크게 틀린다


def eta_text(done: int, total: int, elapsed: float) -> str:
    """" · 남음 약 N분" — 지금까지 실제로 걸린 시간으로만 낸다(상수 없음). 표본이 적거나 끝났으면 빈 글."""
    if done < ETA_MIN_DONE or done >= total or elapsed <= 0:
        return ""
    left = elapsed / done * (total - done)
    if left < 60:
        return f" · 남음 약 {max(int(round(left)), 1)}초"
    return f" · 남음 약 {int(round(left / 60))}분"


class _Slots:
    """동시에 도는 상세 요청 수의 상한 — 도중에 낮출 수 있다(스레드 수는 그대로, 남는 스레드는 기다린다)."""

    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0
        self.peak = 0  # 테스트·측정용 — 실제로 동시에 돈 최대 수
        self._cond = threading.Condition()

    def __enter__(self):
        with self._cond:
            while self.active >= self.limit:
                self._cond.wait()
            self.active += 1
            self.peak = max(self.peak, self.active)

    def __exit__(self, *exc):
        with self._cond:
            self.active -= 1
            self._cond.notify_all()

    def shrink(self, limit: int) -> None:
        with self._cond:
            self.limit = min(self.limit, limit)


def load_saved(ouid: str, match_type: int, stop=None) -> tuple[list, list]:
    """DB 에 저장된 경기 — (최신순 [MatchSummary], 최신순 [detail]). 넥슨을 부르지 않는다.
    1만 경기 약 2초(대부분 JSON 해석)라 검색할 때 넥슨 조회와 나란히 돌리고, 켤 때 미리 읽는다.
    자기 DB 연결을 연다 — 어느 스레드에서 불러도 된다."""
    conn = store.open_db(config.DB_PATH)
    try:
        details = store.load_details(conn, ouid, match_type, stop=stop)
    finally:
        conn.close()
    matches = [m for m in (parse_match(d, ouid) for d in details) if m]
    matches.sort(key=lambda m: m.match_date or 0, reverse=True)
    return matches, details


class SavedPrefetch:
    """켤 때 마지막으로 검색한 계정의 저장된 경기를 뒤에서 미리 읽어 둔다 — 화면엔 아무것도 안 그린다.
    그 계정을 검색하면 DB 읽기(1만 경기 약 2초)를 건너뛴다. 한 번 쓰면 끝, 다른 계정이면 멈추고 버린다."""

    def __init__(self, ouid: str, match_type: int):
        self.ouid, self.match_type = ouid, match_type
        self._stop = False
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prefetch")
        self._future = pool.submit(load_saved, ouid, match_type, lambda: self._stop)
        pool.shutdown(wait=False)

    def take(self, ouid: str, match_type: int):
        """같은 계정이면 그 Future(끝났으면 바로, 아니면 기다리면 된다), 아니면 멈추고 None."""
        if ouid == self.ouid and match_type == self.match_type and not self._stop:
            return self._future
        self.discard()
        return None

    def discard(self) -> None:
        self._stop = True  # 읽던 중이면 다음 행에서 멈춘다 — 창을 닫을 때 종료를 붙잡지 않게


class MatchLoader(QThread):
    """API 호출은 전부 여기서 — UI 스레드가 멈추지 않게."""

    progress = pyqtSignal(int, int, str)
    # [MatchSummary], [원본 detail], ouid, basic, spId→이름, 포지션코드→이름,
    # 새로 저장된 수, 이번에 API 로 받은 수, RankerInfo|None(넥슨 데이터센터 랭킹),
    # 등급이름(감독모드 최고 등급), is_champion(챔피언스 이상인지), 등급 배지 로컬 경로,
    # seasonId→{className,seasonImg}, divisionId→등급이름(등급 추이 그래프용)
    # 컨테이너는 list/dict 가 아니라 object 로 선언한다 — list/dict 면 PyQt 가 1만 경기 기록(중첩 dict)을
    # Qt 형식으로 통째로 깊은 복사했다 되돌려, 직접 읽으면 786MB 인 것이 앱에선 2.7~4.5GB 까지 올랐고
    # 로더가 끝난 뒤 화면까지 약 6초 걸렸다(2026-10-02 실측). object 는 참조만 넘긴다.
    finished_ok = pyqtSignal(object, object, str, object, object, object, int, int, object,
                             str, bool, str, object, object)
    failed = pyqtSignal(str)
    key_invalid = pyqtSignal(str)  # 넥슨이 키를 거절했다 — 키 입력 창으로 보낸다
    quota_hit = pyqtSignal(str)    # 호출 한도(429) — 저장 없이 멈췄다. 서비스 단계 키로 바꾸게 한다
    rank_ready = pyqtSignal(str, object)  # finished_ok 때 랭킹이 아직이었으면 뒤따라 — ouid, RankerInfo|None
    # 역대 최고 등급(user/maxdivision) — ouid, dict|{}(그 종류 기록 없음)|None(실패). finished_ok 뒤에 보내지만
    # 순서에 기대지 않는다: 화면이 ouid 별로 담아서, 먼저 와도 _on_loaded 가 그 계정을 그릴 때 보인다(7단계 변이로 확인)
    max_division_ready = pyqtSignal(str, object)
    # 이번 검색의 ELO 를 새 줄로 적었다(1.3.1) — 랭킹이 finished_ok 보다 늦게 와도 ELO 그래프가 이번 점을 넣게
    elo_saved = pyqtSignal(str)

    def __init__(self, api: FCOnlineAPI, nickname: str, match_type: int, prev=None,
                 offline_ouid: str | None = None, prefetch: SavedPrefetch | None = None,
                 record_elo: bool = False, want_max_division: bool = False):
        """prev: 화면이 이미 가진 (ouid, matches, details) — 같은 계정이면 새 경기만 DB 에서 읽는다.
        목록은 읽기만 하고 고치지 않는다(UI 스레드가 쓰고 있다).
        offline_ouid: 넥슨에 묻지 않고 DB 에 저장된 것만 읽는다 — 켤 때 마지막 계정을 바로 보여 줄 때.
        prefetch: 켤 때 미리 읽어 둔 저장 경기 — 같은 계정이면 DB 를 다시 안 읽는다.
        record_elo: 받은 ELO 를 fifa.db elo_history 에 — 메인 검색만. 구단주 비교도 같은 클래스라
        기본은 꺼 둔다(비교 상대의 이력을 쌓지 않는다)."""
        super().__init__()
        self._record_elo = record_elo
        self._want_max_division = want_max_division  # 메인 검색만 — 구단주 비교 로더는 안 부른다
        self._tail: ThreadPoolExecutor | None = None  # 상세 받기가 끝난 뒤 maxdivision 한 건
        self._api = api
        self._nickname = nickname
        self._match_type = match_type
        self._prev = prev
        self._offline_ouid = offline_ouid
        self._prefetch = prefetch
        self._cancel = False
        self._quota_hit = False
        self._pool: ThreadPoolExecutor | None = None
        self._side: ThreadPoolExecutor | None = None  # 넥슨 조회와 나란히 도는 DB 읽기·랭킹·메타
        self._slots = _Slots(DETAIL_WORKERS)
        self._throttled_base = 0  # 상세를 받기 직전에 잰다(run) — DB 만 읽는 경로에선 API 를 건드리지 않는다

    def cancel(self) -> None:
        """즉시 취소 — 대기 중인 상세 요청을 버리고 진행 중인 것만 끝낸다.

        pool 을 그냥 두면 제출된 수천 건이 다 끝날 때까지 기다려서, 창을
        닫아도 워커가 안 죽고 프로세스가 좀비로 남았다(onefile exe 라 부트로더
        까지 함께 남는다).
        """
        self._cancel = True
        for pool in (self._pool, self._side, self._tail):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        if self._prefetch is not None:
            self._prefetch.discard()

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

    def _run_offline(self) -> None:
        """DB 만 — 넥슨 API·웹을 부르지 않는다(메타는 디스크 캐시가 있으면 그걸로). 순위는 비워 두고,
        뒤이은 새 경기 확인(정상 검색)이 채운다."""
        ouid = self._offline_ouid
        self.progress.emit(0, 0, "저장된 전적 불러오는 중…")
        matches, details = load_saved(ouid, self._match_type, lambda: self._cancel)
        if not details or self._cancel:
            return  # 보여 줄 게 없으면 검색 화면 그대로
        grade_name, is_champion, badge_path, division_names = self._current_grade(details, ouid)
        names = self._safe_meta("spid", "id", "name")
        positions = self._safe_meta("spposition", "spposition", "desc")
        seasons = self._safe_meta_raw("seasonid", "seasonId")
        self.finished_ok.emit(matches, details, ouid, {"nickname": self._nickname}, names,
                              positions, 0, 0, None, grade_name, is_champion, badge_path,
                              seasons, division_names)

    def run(self) -> None:
        if self._offline_ouid:
            try:
                self._run_offline()
            except Exception as e:
                self.failed.emit(f"저장된 전적을 읽지 못했습니다: {e}")
            return
        try:
            self.progress.emit(0, 0, f"'{self._nickname}' 계정 조회 중…")
            ouid = self._api.get_ouid(self._nickname)
            # 넥슨 조회(기본 정보·새 경기)를 기다리는 동안 나머지를 나란히 — 차례로 돌 때 1만 경기 검색이
            # 5.3초였고 그중 DB 읽기 1.9초·랭킹 1.0초가 서로를 기다릴 이유가 없었다(2026-10-04 실측).
            # 저장된 경기의 바탕: 화면이 가진 목록(같은 계정 재검색) > 켤 때 미리 읽은 것 > 지금 DB 에서.
            # 바탕을 읽은 뒤 저장된 경기는 아래에서 id 대조로 채운다 — 바탕이 언제 읽혔든 빠지는 경기가 없다.
            self._side = ThreadPoolExecutor(max_workers=3, thread_name_prefix="loader-side")
            if self._prev and self._prev[0] == ouid:
                base_f = None
            else:
                base_f = self._prefetch.take(ouid, self._match_type) if self._prefetch else None
                if base_f is None:
                    base_f = self._side.submit(load_saved, ouid, self._match_type, lambda: self._cancel)
            rank_f = self._side.submit(self._safe_rank)
            if self._record_elo:
                # 받는 즉시 로더 쪽에서 적는다 — 화면(_on_rank_ready)은 그사이 다른 계정을 열면 늦게 온 값을 버린다
                rank_f.add_done_callback(lambda f, o=ouid: self._save_elo(o, f))
            meta_f = self._side.submit(lambda: (self._safe_meta("spid", "id", "name"),
                                                self._safe_meta("spposition", "spposition", "desc"),
                                                self._safe_meta_raw("seasonid", "seasonId")))
            self._side.shutdown(wait=False)  # 더 넣지 않는다 — 끝난 스레드는 알아서 내려간다
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
                    # 이 검색 전까지의 429 수 — API 객체는 앱이 켜져 있는 동안 계속 세므로, 지난 검색의 429 로
                    # 이번 검색까지 내리지 않게 이것과 비교한다
                    self._throttled_base = getattr(self._api, "throttled", 0)
                    self._pool = ThreadPoolExecutor(max_workers=DETAIL_WORKERS)
                    started = time.monotonic()
                    try:
                        for detail in self._pool.map(self._safe_detail, todo):
                            if self._cancel:
                                return
                            if self._quota_hit:
                                break
                            done += 1
                            self.progress.emit(done, len(todo),
                                               f"새 경기 받는 중… {done:,}/{len(todo):,}"
                                               + eta_text(done, len(todo), time.monotonic() - started))
                            if detail is not None:
                                fresh.append(detail)
                    except (RuntimeError, CancelledError):
                        return  # cancel() 이 pool 을 내려 map 이 끊긴 경우
                    finally:
                        self._pool.shutdown(wait=False, cancel_futures=True)
                        self._pool = None
                if self._quota_hit:
                    # 듬성듬성한 결과를 DB 에 넣으면 그 구멍은 영영 안 메워진다 — 다음 검색의
                    # _new_match_ids 는 첫 페이지가 다 아는 경기면 멈추기 때문이다. 대신 받은
                    # 상세는 이미 디스크 캐시에 있어(get_match_detail) 다시 검색하면 거기서 읽고
                    # 나머지만 API 로 받는다 — 이어 받기가 따로 필요 없다.
                    self.quota_hit.emit(
                        f"넥슨 API 호출 한도에 닿아 조회를 멈췄습니다({done}/{len(todo)}경기). "
                        "받은 경기는 캐시에 있어, 다시 검색하면 나머지만 이어서 받습니다.\n\n"
                        "개발 단계 키는 하루 1,000건이라 첫 조회(수천 건)를 끝내지 못합니다 — "
                        "넥슨 오픈API에서 애플리케이션을 '서비스 단계'로 등록해 받은 키로 바꿔 주세요.")
                    return
                new = store.save_matches(conn, fresh)
                # DB 에 들어간 경기의 디스크 캐시는 지운다 — DB 가 정본이고, 캐시는 한도에 걸려
                # 아직 DB 에 못 넣은 경기를 이어 받을 때만 필요하다(2026-10-02: 2만 개·407MB 중복)
                self._api.forget_details([d.get("matchId") for d in fresh])
                # 상세 받기가 다 끝난 뒤에 — 그 중에 부르면 이 요청의 429 가 api.throttled 를 올려 같은 검색의
                # 상세 슬롯을 줄인다(_safe_detail)
                maxdiv_f = self._start_max_division(ouid)

                self.progress.emit(0, 0, "저장된 전적 불러오는 중…")
                if base_f is None:
                    # 같은 계정 다시 검색 — 화면이 이미 가진 것은 다시 읽지 않는다(재검색 3.6초 → 거의 0)
                    base_matches, base_details = self._prev[1], self._prev[2]
                else:
                    base_matches, base_details = base_f.result()  # 아직이면 여기서 기다린다
                if self._cancel:
                    return
                # 바탕에 없는데 DB 에 있는 것 전부 — 방금 저장한 새 경기, 바탕을 읽은 뒤 다른 프로세스(check_api 등)가 넣은 경기,
                # 한도에 걸렸다 이어 받은 옛 경기. id 로 대조한다(시각으로 자르면 이어 받은 옛 경기를 빠뜨린다).
                have = {d.get("matchId") for d in base_details}
                missing = [i for i in store.known_ids(conn, ouid, self._match_type) if i not in have]
                new_details = store.load_details_by_ids(conn, missing)
                details = store.merge_details(base_details, new_details)
            finally:
                conn.close()

            # 넥슨 데이터센터의 감독모드 랭킹(순위·구단가치·ELO). 오픈API 엔 없는 값이라 받는다(위에서
            # 나란히 시작). 아직이면 기다리지 않고 먼저 그린다 — 미리 읽은 계정이면 검색 2.0초 중 1.1초가
            # 이 기다림이었다(2026-10-04 실측). 쓰는 곳은 랭커 카드뿐이라 오면 rank_ready 로 카드만 다시.
            # 실패해도 전적 조회는 살린다.
            rank = rank_f.result() if rank_f.done() else None
            rank_pending = rank is None and not rank_f.done()
            grade_name, is_champion, badge_path, division_names = \
                self._current_grade(details, ouid)

            if not details:
                self.finished_ok.emit([], [], ouid, basic, {}, {}, 0, got,
                                      rank, grade_name, is_champion, badge_path,
                                      {}, division_names)
                self._send_max_division(maxdiv_f, ouid)
                return

            have_m = {m.match_id for m in base_matches}
            matches = list(base_matches) + [
                m for m in (parse_match(d, ouid) for d in new_details) if m and m.match_id not in have_m]
            matches.sort(key=lambda m: m.match_date or 0, reverse=True)

            self.progress.emit(0, 0, "선수 정보 조회 중…")
            names, positions, seasons = meta_f.result()

            self.finished_ok.emit(matches, details, ouid, basic, names,
                                  positions, new, got, rank, grade_name,
                                  is_champion, badge_path, seasons, division_names)
            self._send_max_division(maxdiv_f, ouid)
            if rank_pending:
                # 여기서 기다리지 않는다 — 스레드가 살아 있으면 그동안 새 검색이 막힌다(_api_search 의 isRunning).
                # 끝나면 그 스레드에서 신호만 보낸다(받는 쪽은 UI 스레드로 줄 세워진다).
                rank_f.add_done_callback(
                    lambda f, o=ouid: None if self._cancel else self.rank_ready.emit(o, f.result()))

        except NexonAPIError as e:
            if e.code == KEY_INVALID_CODE:
                self.key_invalid.emit(e.message)
            else:
                self.failed.emit(e.message)
        except CancelledError:
            return  # cancel() 이 나란히 돌던 일을 내렸다 — 창을 닫는 중이라 알릴 곳이 없다
        except Exception as e:
            self.failed.emit(f"예기치 못한 오류: {e}")

    def _start_max_division(self, ouid: str):
        """역대 최고 등급 요청을 따로 한 스레드에서 시작 — 꺼져 있으면 None."""
        if not self._want_max_division or self._cancel:
            return None
        self._tail = ThreadPoolExecutor(max_workers=1, thread_name_prefix="loader-maxdiv")
        fut = self._tail.submit(self._safe_max_division, ouid)
        self._tail.shutdown(wait=False)
        return fut

    def _safe_max_division(self, ouid: str):
        """감독모드 줄 하나 → {"division", "date", "at"}. 그 종류 줄이 없으면 {} · 실패면 None(검색은 안 죽인다).
        at 은 achievementDate 전체(초까지) — 슈챔 달성 기록이 목록의 진입 시각과 ±60초로 대조한다."""
        try:
            rows = self._api.get_max_division(ouid)
        except Exception:
            return None
        row = next((r for r in rows if isinstance(r, dict)
                    and r.get("matchType") == config.DEFAULT_MATCH_TYPE), None)
        if row is None or row.get("division") is None:
            return {}
        at = str(row.get("achievementDate") or "")
        return {"division": row.get("division"), "date": at[:10], "at": at}

    def _send_max_division(self, fut, ouid: str) -> None:
        """끝났으면 지금, 아니면 끝나는 스레드에서. 여기서 기다리지 않는다(스레드가 살아 있으면 새 검색이 막힌다)."""
        if fut is None:
            return
        send = lambda f: None if self._cancel or f.cancelled() else \
            self.max_division_ready.emit(ouid, f.result())  # noqa: E731
        if fut.done():
            send(fut)
        else:
            fut.add_done_callback(send)

    def _safe_detail(self, match_id: str):
        """한 경기가 실패해도 전체 조회를 죽이지 않는다."""
        if self._cancel or self._quota_hit:
            return None
        try:
            with self._slots:
                if self._cancel or self._quota_hit:  # 자리를 기다리는 사이 멈췄을 수 있다
                    return None
                try:
                    return self._api.get_match_detail(match_id)
                finally:
                    if getattr(self._api, "throttled", 0) > self._throttled_base:
                        self._slots.shrink(DETAIL_WORKERS_THROTTLED)
        except NexonAPIError as e:
            # 재시도(_get)까지 거친 429 — 개발 단계 키(초당 5·하루 1,000건)일 때 난다
            if e.code == QUOTA_CODE:
                self._quota_hit = True
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
        return grade_name, core.is_champion_or_above(div_id), badge_path, names

    def _save_elo(self, ouid: str, fut) -> None:
        """ELO 한 줄(store.save_elo). 창을 닫는 중(_cancel)이면 버리고, 실패는 조용히 — 기록 하나 때문에 검색이
        죽지 않게. 다른 스레드(나란히 돌던 일)에서 불린다 — DB 는 여기서 따로 연다."""
        if self._cancel or fut.cancelled():
            return
        try:
            info = fut.result()
            if info is None or getattr(info, "elo", None) is None:
                return  # 랭킹 밖·못 받음
            conn = store.open_db(config.DB_PATH)
            try:
                wrote = store.save_elo(conn, ouid, info.elo, info.rank, profile_sn=info.profile_sn,
                                       nickname=info.nickname or self._nickname)
            finally:
                conn.close()
            if wrote and not self._cancel:   # 같은 값이라 안 적었거나 실패면 안 낸다(rank_ready 와 같은 규칙)
                self.elo_saved.emit(ouid)
        except Exception:
            pass

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
    (팀컬러처럼 수백 건을 한 번에 훑지 않는다) 풀 없이 스레드 하나로 충분하다.
    받은 시세는 카드 시세 캐시(card_prices)에도 넣는다 — 화면 스레드에서 DB 를 쓰지 않으려고 여기서(1.4.1)."""

    loaded = pyqtSignal(object)   # playerinfo.PlayerInfo
    failed = pyqtSignal(str)

    def __init__(self, sp_id: int):
        super().__init__()
        self._sp_id = sp_id

    def run(self) -> None:
        try:
            info = playerinfo.fetch_player_info(self._sp_id)
        except playerinfo.PlayerInfoError as e:
            self.failed.emit(str(e))
            return
        self.loaded.emit(info)
        prices = playerinfo.prices_as_int(info)
        if prices:
            try:
                conn = store.open_db(config.DB_PATH)
                try:
                    store.save_card_prices(conn, self._sp_id, prices, datetime.now().date().isoformat())
                finally:
                    conn.close()
            except sqlite3.Error:
                pass  # 캐시 실패가 카드 창을 막으면 안 된다 — 다음에 다시 읽는다


class TradeLoader(QThread):
    """거래 기록 받기(tradecollect.collect — 키 주인 것만). 한 번에 하나.

    다른 오픈API 로더(새 검색·구단주 비교)가 시작되면 화면이 cancel() 로 양보시킨다 — 쪽 사이에서 멈추고 다음 기회에
    이어 받는다. 쪽마다 한 트랜잭션이라 끊겨도 반쪽이 안 남아 terminate 하지 않는다(shutdown 표)."""

    wiped = pyqtSignal()        # 키가 바뀌어 옛 주인 거래를 지웠다(커밋 뒤) — 화면이 들고 있던 걸 버린다
    done = pyqtSignal(object)   # tradecollect.TradeResult

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        conn = None
        try:
            conn = store.open_db(config.DB_PATH)
            # 키는 모듈 경유 — 키 바꾸는 길 셋(키 창 둘 · .env 손 수정)을 여기 한 자리에서 지문으로 잡는다
            res = tradecollect.collect(self._api, conn, config.API_KEY, cancel=lambda: self._cancel,
                                       on_wiped=self.wiped.emit)
        except Exception as e:  # DB 잠김 등 — 거래 받기 실패가 앱을 죽이지 않게. 상태는 그대로라 다음에 이어 받는다
            res = tradecollect.TradeResult(error=f"{type(e).__name__}: {e}")
        finally:
            if conn is not None:
                conn.close()
        self.done.emit(res)


class PriceLoader(QThread):
    """가계부 평가용 카드 시세를 하루 캐시로(playerinfo.collect_prices). 띄우는 곳은 12단계 가계부 —
    config.price_auto_allowed() 뒤에서만. 카드 사이마다 cancel 을 본다(한 장 = 한 트랜잭션)."""

    done = pyqtSignal(int, int)  # 이번에 읽은 카드 수, 상한에 걸려 못 읽은 카드 수

    def __init__(self, spids: list[int]):
        super().__init__()
        self._spids = list(spids)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        got = skipped = 0
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                got, skipped = playerinfo.collect_prices(conn, self._spids, datetime.now().date().isoformat(),
                                                         config.PRICE_FETCH_MAX, cancel=lambda: self._cancel)
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        self.done.emit(got, skipped)


class RankerStatsLoader(QThread):
    """랭커 기록(rankerstats.collect — 하루 캐시 우선) — N1 메뉴 "랭커와 비교"와 N2 선수 카드 [랭커 기록]이 같이 쓴다.
    요청은 묶음(RANKER_STATS_BATCH)마다 하나 · 묶음마다 한 트랜잭션이라 terminate 하지 않는다(shutdown 표)."""

    done = pyqtSignal(object)   # (spid, po) → 응답 한 줄 | None
    failed = pyqtSignal(str)

    def __init__(self, api: FCOnlineAPI, pairs, matchtype: int):
        super().__init__()
        self._api = api
        self._pairs = list(pairs)
        self._matchtype = matchtype
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                res = rankerstats.collect(self._api, conn, self._pairs, self._matchtype,
                                          datetime.now().date().isoformat(), cancel=lambda: self._cancel)
            finally:
                conn.close()
        except NexonAPIError as e:  # 429 도 여기 — 키 입력 창으로 보내지 않는다(부가 기능)
            if not self._cancel:
                self.failed.emit(e.message)
            return
        except Exception as e:  # DB 잠김 등 — 화면 하나가 비는 것으로 끝나게
            if not self._cancel:
                self.failed.emit(f"{type(e).__name__}: {e}")
            return
        if not self._cancel:
            self.done.emit(res)


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
    # {닉네임: (팀컬러("" 이면 랭킹 밖), 구단가치(원 단위 int, 못 찾으면 None))} — RankListLoader 와 같은 모양
    loaded_many = pyqtSignal(object)  # dict — 이유는 MatchLoader.finished_ok
    finished_all = pyqtSignal()

    def __init__(self, nicknames: list[str]):
        super().__init__()
        self._nicknames = nicknames
        self.total = len(nicknames)
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
                    nick, color, value = result
                    self.loaded_many.emit({nick: (color, value)})
                done += 1
                self.progress.emit(done, total)
        except (RuntimeError, CancelledError):
            return  # cancel() 이 pool 을 내려 map 이 끊긴 경우
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if not self._cancel:
            self.finished_all.emit()


class RankListLoader(QThread):
    """감독모드 랭킹 1만 위 목록(ranker.RANK_PAGES 쪽)을 통째로 읽어 wanted 의 팀컬러를 찾는다.

    상대가 많을 때 TeamColorLoader(상대마다 검색) 대신 쓴다 — 찾을 수 있는 범위(1만 위 안)는
    같고 요청은 500번으로 고정이다. 1만 위 목록에 없는 상대는 '랭킹 밖'("")으로 내보낸다.
    단 **한 쪽이라도 못 읽었으면 그러지 않는다** — 그 쪽에 있었을 상대를 7일(store.TEAM_COLOR_TTL_DAYS) 동안 '랭킹 밖'으로
    캐시하게 되므로, 못 찾은 상대는 비워 두고 다음 조회 때 다시 찾는다.
    읽는 동안 순위가 움직여 쪽 경계에서 한두 명이 빠질 수 있다 — 그 사람은 '랭킹 밖'으로
    저장돼 캐시가 만료되면 다시 찾는다(빠지는 수가 작아 받아들인다).

    **읽는 곳은 랭킹 수집(rankcollect)과 하나다**(1.1.1): 간격 안의 스냅숏이 있으면 거기서 읽고(요청 0),
    수집이 지금 돌고 있으면 끝나기를 기다렸다 그 스냅숏을 쓴다. 스스로 500쪽을 다 읽었으면 수집이 켜져 있을 때만
    스냅숏으로도 남긴다. 실패 규칙은 쓰는 쪽마다 — 여기는 못 읽은 쪽만 빼고 쓰고, 스냅숏은 한 쪽이라도 빠지면 버린다.
    """

    MAX_WORKERS = TeamColorLoader.MAX_WORKERS  # 같은 페이지·같은 예의
    TIMEOUT = 10  # 목록 한 쪽은 검색 결과보다 크다(약 50KB)

    progress = pyqtSignal(int, int)   # 읽은 쪽, 전체 쪽
    loaded_many = pyqtSignal(object)  # {닉네임: (팀컬러, 구단가치)} — object 인 이유는 MatchLoader.finished_ok
    finished_all = pyqtSignal()
    waiting = pyqtSignal()            # 랭킹 수집이 같은 목록을 읽는 중이라 기다린다

    def __init__(self, wanted: set[str]):
        super().__init__()
        self._wanted = wanted
        self.total = ranker.RANK_PAGES
        self.failed_pages = 0
        self.fetched_at = None   # 스냅숏에서 채웠으면 그 시각 — 팀컬러 캐시 유효기간을 거기서 센다
        self.saved_snapshot = None  # 스스로 읽은 목록을 스냅숏으로 남겼으면 rankcollect.Outcome
        self._cancel = False
        self._pool: ThreadPoolExecutor | None = None

    def cancel(self) -> None:
        self._cancel = True
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _page(self, page: int):
        try:
            return ranker.fetch_rank_rows(page, timeout=self.TIMEOUT)
        except ranker.RankerError:
            return None

    def _from_snapshot(self) -> bool:
        try:
            got = rankcollect.snapshot_colors(self._wanted)
        except Exception:
            return False  # rank.db 를 못 읽으면 넥슨에서 읽는다
        if got is None:
            return False
        colors, self.fetched_at = got
        if colors and not self._cancel:
            self.loaded_many.emit(colors)
        self.progress.emit(self.total, self.total)
        return True

    def run(self) -> None:
        if self._from_snapshot():
            self.finished_all.emit()
            return
        got, waited = rankcollect.acquire_list_read(lambda: self._cancel, on_wait=self.waiting.emit)
        if not got:
            return
        try:
            if waited and self._from_snapshot():   # 기다린 수집이 남긴 스냅숏
                self.finished_all.emit()
                return
            self._read_pages()
        finally:
            rankcollect.release_list_read()

    def _read_pages(self) -> None:
        found: set[str] = set()
        results: dict[int, ranker.RankPageResult] = {}
        done = 0
        taken = datetime.now()
        self._pool = ThreadPoolExecutor(max_workers=self.MAX_WORKERS)
        try:
            for res in self._pool.map(self._page, range(1, self.total + 1)):
                if self._cancel:
                    return
                rows = ranker._color_rows(res.rows) if res is not None else []
                if not rows:  # 빈 쪽을 "아무도 없음"으로 읽으면 상대 수백 명이 '랭킹 밖'으로 캐시된다
                    self.failed_pages += 1
                else:
                    results[res.page] = res
                    batch = {n: (c, v) for n, c, v in rows if n in self._wanted and n not in found}
                    if batch:
                        found.update(batch)
                        self.loaded_many.emit(batch)
                done += 1
                self.progress.emit(done, self.total)
        except (RuntimeError, CancelledError):
            return
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if self._cancel:
            return
        if not self.failed_pages:
            rest = {n: ("", None) for n in self._wanted - found}
            if rest:
                self.loaded_many.emit(rest)
            try:
                self.saved_snapshot = rankcollect.save_from_pages(
                    results, taken, pages=self.total, ended_season=_ended_season())
            except Exception:
                pass  # 스냅숏 저장 실패가 팀컬러까지 막지 않는다 — 수집이 다음 확인에 다시 읽는다
        self.finished_all.emit()


def _ended_season() -> int | None:
    """시즌표(fifa.db 캐시)에서 마지막으로 끝난 시즌 번호 — 랭킹 스냅숏의 시즌 경계 판정용. 모르면 None."""
    try:
        conn = store.open_db(config.DB_PATH)
        try:
            items = store.load_seasons(conn)
        finally:
            conn.close()
    except Exception:
        return None
    today = datetime.now().date()
    nos = [s.no for s in items if s.end <= today]
    return max(nos) if nos else None


# ── ELO 그래프 (1.3.1 · 13) — 읽기는 EloLoader(작업 스레드), 그리기는 들고 있는 값으로 ────────────

@dataclass
class EloSeries:
    """EloLoader 가 한 번에 읽은 것 — 계정의 ELO 기록 전부 · 순위 컷 시계열 · 따라가기 상태."""
    req: int                                  # 창이 띄울 때 준 요청 번호 — 늦게 끝난 옛 읽기를 거른다
    rows: list[dict]                          # elo_history(오래된 것부터) — 계정별로 거르지 않고 시즌은 그릴 때
    cuts: dict                                # {순위: [(taken_at ISO, elo)]} — rank.db 가 없으면 {}
    snap_season_start: str | None = None      # 시즌표가 없을 때 쓸 시작(마지막 season_seq 의 첫 스냅숏)
    tracked: bool = False
    track_names: tuple = ()                   # 지금 따라가는 닉네임들(버튼 툴팁)


class EloLoader(QThread):
    """fifa.db elo_history·elo_track 과 rank.db cut_elo 를 읽는다. rank.db 는 읽기 전용 — 없으면 만들지 않는다."""
    elo_ready = pyqtSignal(str, object)   # ouid, EloSeries — object 인 이유는 PyQt 규칙 8

    def __init__(self, ouid: str, req: int):
        super().__init__()
        self._ouid, self._req = ouid, req
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        rows, tl, cuts, snap = [], [], {}, None
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                rows = store.elo_history(conn, self._ouid)
                tl = store.track_list(conn)
            finally:
                conn.close()
        except Exception:
            pass   # 못 읽으면 빈 그래프 — 다음 신호 때 다시 읽는다
        if self._cancel:
            return
        try:
            r = rankcollect.open_rank_db_ro()
            if r is not None:
                try:
                    cuts = rankcollect.cut_series(r)
                    snap = rankcollect.season_start_by_snapshots(r)
                finally:
                    r.close()
        except Exception:
            pass
        if self._cancel:
            return
        self.elo_ready.emit(self._ouid, EloSeries(
            self._req, rows, cuts, snap, any(t["ouid"] == self._ouid for t in tl),
            tuple(t["nickname"] or t["ouid"][:8] for t in tl)))


class PredictWorker(QThread):
    """시즌 말 순위 예측(1.3.1 · predict.py) — EloLoader 가 끝난 뒤 그 계정으로. rank.db 는 읽기 전용(없으면 만들지 않음).
    숫자를 내면 fifa.db predictions 에 그날 한 줄(시즌 뒤 대조용). 계산 예외는 그 구역만 "계산하지 못했습니다"."""
    pred_ready = pyqtSignal(str, object)   # ouid, (요청 번호, Prediction)

    FAILED = "예측을 계산하지 못했습니다"

    def __init__(self, ouid: str, req: int, elo_rows: list, seasons: list, notice):
        super().__init__()
        self._ouid, self._req = ouid, req
        self._rows, self._seasons, self._notice = elo_rows, list(seasons), notice
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        now = datetime.now()
        try:
            since = (now - timedelta(days=config.PREDICT_RECENT_DAYS + 31)).isoformat(timespec="seconds")
            conn = store.open_db(config.DB_PATH)
            try:
                dates = store.match_dates(conn, self._ouid, since)
            finally:
                conn.close()
            if self._cancel:
                return
            r = rankcollect.open_rank_db_ro()
            try:
                pr = core.predict_for(elo_rows=self._rows, match_dates=dates, rank_conn=r, seasons=self._seasons,
                                      notice=self._notice, now=now)
            finally:
                if r is not None:
                    r.close()
        except Exception as e:  # noqa: BLE001 — 예측 하나 때문에 화면이 죽지 않게
            if not getattr(PredictWorker, "_noted", False):
                PredictWorker._noted = True     # crash.log 에 실행당 한 번만
                crashlog.note("predict", e)
            pr = core.Prediction(False, self.FAILED)
        if self._cancel:
            return
        if pr.ok:
            try:
                conn = store.open_db(config.DB_PATH)
                try:
                    store.save_prediction(conn, self._ouid, now, profile_sn=pr.profile_sn,
                                          season_start=pr.season_start.isoformat() if pr.season_start else None,
                                          end_source=pr.end_source, p200=pr.p.get(200, 0.0),
                                          p1000=pr.p.get(1000, 0.0), lo=pr.lo, hi=pr.hi)
                finally:
                    conn.close()
            except sqlite3.Error:
                pass      # 기록 실패는 화면과 무관 — 다음 계산이 그날 줄을 다시 쓴다
        if not self._cancel:
            self.pred_ready.emit(self._ouid, (self._req, pr))


def elo_season_start(rank_seasons, snap_start: str | None, now: datetime) -> datetime:
    """ELO 그래프의 "지금 시즌" 시작 — 시즌표의 마지막 끝난 시즌 종료일(반개구간이라 그날 0시부터) ·
    시즌표가 없으면 스냅숏 season_seq 가 마지막 것과 같은 첫 스냅숏 · 둘 다 없으면 최근 ELO_FALLBACK_DAYS 일."""
    ended = [s.end for s in rank_seasons if s.end <= now.date()]
    if ended:
        return datetime.combine(max(ended), datetime.min.time())
    if snap_start:
        try:
            return datetime.fromisoformat(snap_start)
        except ValueError:
            pass
    return now - timedelta(days=config.ELO_FALLBACK_DAYS)


def elo_daily_points(rows: list[dict], start: datetime) -> list[dict]:
    """시작 이후 줄을 하루 한 점으로 — 같은 날 둘이면 늦은 것(검색·스냅숏 출처 무관). 오래된 것부터."""
    by_day: dict = {}
    for r in rows:
        try:
            t = datetime.fromisoformat(r["taken_at"])
        except (TypeError, ValueError):
            continue
        if t < start or r.get("elo") is None:
            continue
        cur = by_day.get(t.date())
        if cur is None or t >= cur["at"]:
            by_day[t.date()] = {**r, "at": t}
    return [by_day[d] for d in sorted(by_day)]


def rank_tier_index(rank: int | None) -> int:
    """순위 → RANK_TIERS 구간 번호(0 이 맨 위). 1만 위 밖·모름은 len(RANK_TIERS)."""
    if rank is None:
        return len(config.RANK_TIERS)
    for i, t in enumerate(config.RANK_TIERS):
        if rank <= t:
            return i
    return len(config.RANK_TIERS)


def rank_tier_label(idx: int) -> str:
    tiers = config.RANK_TIERS
    if idx >= len(tiers):
        return f"{tiers[-1] // 10000}만 위 밖" if tiers[-1] % 10000 == 0 else f"{tiers[-1]:,}위 밖"
    lo = 1 if idx == 0 else tiers[idx - 1] + 1
    return f"{lo:,}~{tiers[idx]:,}위"


def rank_tier_change_text(points: list[dict]) -> str:
    """순위 구간 변화 한 줄 — 지금 구간 + 바로 앞 기록 하나와 비교(알림이 아니라 지난 값 표시).
    points 는 지금 시즌 안의 점이라 하나뿐이면(새 시즌 첫 기록) 비교하지 않는다."""
    if not points:
        return ""
    now_i = rank_tier_index(points[-1].get("rank"))
    text = f"지금 {rank_tier_label(now_i)} 구간"
    if now_i >= len(config.RANK_TIERS):
        text = f"지금 {rank_tier_label(now_i)}"
    if len(points) < 2:
        return text
    prev = points[-2]
    prev_i = rank_tier_index(prev.get("rank"))
    when = prev["at"].strftime("%m/%d")
    if prev_i == now_i:
        return f"{text} · 지난 기록({when})과 같은 구간"
    way = "올라옴" if now_i < prev_i else "내려옴"
    return f"{text} · 지난 기록({when}) {rank_tier_label(prev_i)}에서 {way}"


class SeasonLoader(QThread):
    """감독모드 랭킹 시즌표를 백그라운드로 받아 온다(데이터센터 스크래핑).

    한 번의 GET 이라 금방 끝나지만, 넥슨이 느리거나 점검 중이면 몇 초씩
    걸릴 수 있어 UI 스레드에서 부르면 안 된다. 실패해도 조용히 지나간다 —
    시즌 기능만 못 쓰고 판수 기준 화면은 그대로 돌아가야 한다.
    """

    loaded = pyqtSignal(object)  # list[sn.Season] — object 인 이유는 MatchLoader.finished_ok

    def run(self) -> None:
        try:
            items = sn.fetch_seasons()
        except sn.SeasonError:
            return
        try:
            conn = store.open_db(config.DB_PATH)  # DB 는 스레드마다 따로
            try:
                store.save_seasons(conn, items)
            finally:
                conn.close()
        except Exception:
            pass  # 캐시 저장 실패가 이번 화면까지 막을 이유는 없다
        self.loaded.emit(items)


class RankCollectWorker(QThread):
    """랭킹 수집 한 회차(rankcollect.collect)를 UI 스레드 밖에서."""
    progress = pyqtSignal(int, int)  # 읽은 쪽, 전체 쪽
    done = pyqtSignal(object)        # rankcollect.Outcome

    def __init__(self):
        super().__init__()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            out = rankcollect.collect(cancel=self._cancel, progress=self.progress.emit,
                                      ended_season=_ended_season())
        except Exception as e:  # DB 를 못 여는 등 — 수집 하나 때문에 크래시 로그가 쌓이면 안 된다
            out = rankcollect.Outcome("failed", f"수집 중 오류: {e}")
        self.done.emit(out)


def collect_outcome_text(out) -> tuple[str, bool]:
    """수집 결과 → (상태줄 글, 오래 보여야 하나). 알릴 게 없으면 ("", False)."""
    k = out.kind
    if k == "ok":
        return f"랭킹 수집 완료 — {out.rows:,}명", False
    if out.disabled_by_block:
        return "넥슨이 랭킹 목록 요청을 계속 막아 랭킹 수집을 껐습니다 — [정보] 에서 다시 켤 수 있습니다", True
    if out.fail_notice:
        return f"랭킹 수집이 여러 번 연속 실패했습니다 — {out.message} · 간격을 늘려 다시 시도합니다", True
    if k in ("failed", "blocked"):
        return f"랭킹 수집 실패 — {out.message} · 나중에 다시 시도합니다", False
    if k == "offline":
        return "랭킹 수집 — 연결이 안 돼 다음 확인에 다시 합니다", False
    if k in ("straddle", "locked"):
        return f"랭킹 수집 — {out.message}", False
    return "", False  # disabled · cancelled · fresh


class RankCollectScheduler(QObject):
    """랭킹 수집 예약(1.1.1) — 켤 때와 RANK_CHECK_EVERY_MIN 마다 간격이 지났는지 보고, 지났으면 그 시각
    +5~50분(rankcollect.pick_start) 무작위에 한 번 돌린다. 예약은 하나만(planned) — 1시간 확인이 겹쳐 두 번 잡지 않게.
    타이머가 늦게 터지면(절전 복귀) 그 자리에서 시작하지 않고 지금 기준으로 다시 고른다.
    도는 조건은 매번 본다: 안내 동의 · 웹 데이터 · 수집 토글(rankcollect.collect 도 .env 를 다시 읽어 한 번 더 막는다)."""

    status = pyqtSignal(str, bool)   # 상태줄 글, 오래 보여야 하나
    outcome = pyqtSignal(object)     # 끝난 회차(rankcollect.Outcome)

    def __init__(self, parent=None, now_fn=datetime.now):
        super().__init__(parent)
        self._now = now_fn
        self._check_timer = QTimer(self)
        self._check_timer.setInterval(config.RANK_CHECK_EVERY_MIN * 60 * 1000)
        self._check_timer.timeout.connect(self.check)
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._fire)
        self.planned: datetime | None = None
        self.worker: RankCollectWorker | None = None

    @staticmethod
    def can_run() -> bool:
        return not config.notice_needed() and config.WEB_DATA and config.RANK_COLLECT

    def running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start(self) -> None:
        self._check_timer.start()
        self.check()

    def check(self) -> None:
        if not self.can_run() or self.planned is not None or self.running():
            return
        try:
            conn = rankcollect.open_rank_db()
            try:
                due = rankcollect.is_due(conn, self._now())
            finally:
                conn.close()
        except Exception:
            return  # rank.db 를 못 열면 다음 확인에
        if due:
            self._plan()

    def _plan(self) -> None:
        now = self._now()
        self.planned = rankcollect.pick_start(now)
        self._start_timer.start(max(0, int((self.planned - now).total_seconds() * 1000)))

    def _fire(self) -> None:
        planned, self.planned = self.planned, None
        if planned is None or not self.can_run() or self.running():
            return
        if not rankcollect.start_still_valid(planned, self._now()):
            self.check()  # 늦게 터졌다 — 지금 시각 기준으로 다시 고른다
            return
        self.run_now()

    def run_now(self) -> None:
        self.worker = RankCollectWorker()
        self.worker.progress.connect(lambda d, n: self.status.emit(f"랭킹 수집 {d} / {n}쪽…", False))
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, out) -> None:
        text, important = collect_outcome_text(out)
        if text:
            self.status.emit(text, important)
        self.outcome.emit(out)

    def stop(self, wait_ms: int = 3000) -> None:
        """예약을 지우고 도는 회차를 멈춘다(토글 끔·기록 지우기). 1시간 확인은 그대로 — 다시 켜면 거기서 잡는다."""
        self._start_timer.stop()
        self.planned = None
        w = self.worker
        if w is not None and w.isRunning():
            w.cancel()
            if not w.wait(wait_ms):
                w.terminate()
                w.wait(1000)

    def shutdown(self, fast: bool = False) -> list:
        """끝낼 때(quit_app). fast 면 기다리지 않고 멈춤 요청만 → 아직 도는 스레드(호출부가 합계 시간만 기다린다)."""
        self._check_timer.stop()
        if not fast:
            self.stop()
            return []
        self._start_timer.stop()
        self.planned = None
        w = self.worker
        if w is not None and w.isRunning():
            w.cancel()  # 한 트랜잭션이라 끊겨도 반쪽 스냅숏이 안 남는다
            return [w]
        return []


class MainWindow(QMainWindow):
    MATCH_COLUMNS =["일시", "결과", "스코어", "상대", "점유율", "슈팅", "유효",
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
    SEASON_COLUMNS = ["시즌", "기간", "경기", "승", "무", "패", "승률", "지난 시즌 대비", "등급",
                      "평균 득점", "평균 실점", "평균 점유율", "평균 평점"]

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api
        self._loader: MatchLoader | None = None
        self._prefetch: SavedPrefetch | None = None  # start_prefetch — 첫 검색이 가져간다
        self._quiet_search = False  # 켤 때 자동 새 경기 확인 — 실패를 상태줄로만
        self._img_cache_dir = config.CACHE_DIR / "player_images"
        self._table_season_loader: SeasonIconLoader | None = None
        self._finishing_icon_loader: SeasonIconLoader | None = None
        self._ouid = ""
        self._nick = ""
        self._basic: dict = {}
        self._rank = None   # ranker.RankerInfo | None — 넥슨 데이터센터 랭킹
        self._max_division: dict[str, dict] = {}  # ouid → {"division","date"} | {} — 역대 최고 등급(_on_max_division)
        # ELO 그래프(1.3.1) — 계정별로 들고 그리는 건 늘 self._ouid 칸. 요청 번호는 계정별 마지막 것만 받는다.
        # 내려놓기(release_memory)도 안 비운다(작다 — _max_division 과 같은 규칙).
        self._elo: dict[str, EloSeries] = {}
        self._elo_req: dict[str, int] = {}
        self._elo_workers: list[EloLoader] = []
        self._pred: dict = {}                       # ouid → Prediction (EloLoader 뒤 PredictWorker 가 채운다)
        self._pred_req: dict[str, int] = {}
        self._pred_workers: list[PredictWorker] = []
        self._notice_asked = False   # ask_notice_update_once — 실행당 한 번
        self._notice_after_restore = False  # 최소화 중이라 미뤘다 — 창이 돌아오면 묻는다
        # 거래 기록(1.4.1) — 받기는 한 번에 하나. 도는 중에 다시 띄우라는 요청(키 바꿈 등)이 오면 끝난 뒤 한 번 더
        self._trade_loader: TradeLoader | None = None
        self._trade_again = False
        self._trade_result: tradecollect.TradeResult | None = None  # 마지막 받기 결과 — 거래 화면 상태 줄
        self._price_loader: PriceLoader | None = None  # 가계부를 그릴 때 — 보유·최근 구매 카드 시세(하루 캐시)
        self._price_tried_on: str | None = None        # 오늘 이미 띄웠다 — 다 그린 뒤 다시 그려도 또 띄우지 않게
        self._timeline_cache: tuple | None = None      # (키, Timeline) — 타임라인·가계부가 같이 쓴다
        # 랭커 기록(13단계) — N1 메뉴 로더와 N2 카드 탭 로더는 따로(카드 창이 메뉴 로더를 끊지 않게)
        self._ranker_loader: RankerStatsLoader | None = None
        self._ranker_day: str | None = None   # _ranker_data 를 받은 날 — 바뀌면 비운다(하루 캐시)
        self._ranker_data: dict = {}          # (spid, po) → 응답 한 줄 | None — 오늘 받은 것
        self._ranker_note = ""                # 실패 문구(있으면 표 위에)
        self._ranker_failed: tuple | None = None  # 실패한 쌍 — 같은 쌍을 다시 그릴 때마다 묻지 않게(새 데이터면 풀린다)
        self._scout_loader: RankerStatsLoader | None = None
        self._grade_name = "-"     # 감독모드 최고 등급 이름 (division 메타)
        self._division_names: dict[int, str] = {}  # divisionId -> 등급 이름
        self._is_champion = False  # 감독모드 최고 등급 챔피언스 이상 — 랭커 카드 표시 여부
        self._badge_path = ""      # 등급 배지 아이콘 로컬 캐시 경로
        self._seasons: dict = {}   # seasonId -> {className, seasonImg} (get_meta("seasonid"))
        self._season_icon_dir = config.CACHE_DIR / "season_icons"
        # _matches/_details 는 "지금 보고 있는 것" — 시즌 필터가 걸리면 그 시즌
        # 경기만 담긴다. _matches_all/_details_all 이 누적 전체(원본)다.
        self._matches: list[MatchSummary] = []
        self._details: list[dict] = []
        self._matches_all: list[MatchSummary] = []
        self._details_all: list[dict] = []
        # 감독모드 랭킹 시즌표(seasons.py). 선수 카드 시즌(self._seasons)과 다르다.
        self._rank_seasons: list[sn.Season] = []
        self._season_loader: SeasonLoader | None = None
        # 사용자가 콤보를 직접 건드리기 전까지는 기본값(현재 시즌)을 고른다.
        # "전체"도 data 가 None 이라 prev 만으로는 첫 조회와 구분되지 않는다.
        self._season_picked = False
        self._names: dict = {}
        self._positions: dict = {}
        self._trend_reset_pending = True
        self._team_colors: dict[str, str] = {}   # 상대 닉네임 -> 팀컬러("" = 못 찾음)
        self._team_values: dict[str, int | None] = {}  # 상대 닉네임 -> 구단가치(원)
        self._teamcolor_loader: TeamColorLoader | None = None
        self._teamcolor_pending: list[str] = []  # 이번 라운드에 조회 요청한 닉네임
        self._teamcolor_loaded_count = 0
        self._teamcolor_rendered_at = 0.0  # 중간 갱신 간격(TEAMCOLOR_RENDER_INTERVAL_S)용
        self._teamcolor_progress_fmt = "{done} / {total} 조회 중…"
        self._teamcolor_retry_pending = False  # 조회 중 범위가 넓어져 재시도가 필요함
        self._dirty: set[str] = set()  # 낡은 화면 키(VIEW_OF_KEY) — 열 때 그린다
        self._narrate_key = None       # 흐름 분석 결과 캐시 — 대시보드·흐름 분석 메뉴가 같이 쓴다
        self._narrate_found: list = []
        self._rank_sched: RankCollectScheduler | None = None  # 랭킹 수집 예약 — 창 밖(tray.AppShell)이 쥐고 붙여 준다
        self.shell = None          # tray.AppShell — 없으면(테스트) X 가 예전처럼 정리하고 닫는다
        self._quitting = False     # quit_app 이 세운다 — closeEvent 는 받기만
        self._released = False     # 숨긴 지 오래돼 경기 기록을 내려놓았다(release_memory) — 열 때 다시 읽는다
        self._shown_once = False   # show_initial 을 한 번 거쳤나(--tray 로 숨긴 채 만든 창은 처음 열 때 거친다)
        self._compare_loader: MatchLoader | None = None  # 구단주 비교 — 상대 계정 조회용
        self._compare_squad_loaders: list = []  # 구단주 비교 스쿼드 이미지/시즌아이콘 로더
        self._ability_sim_loader: AbilitySimLoader | None = None
        self._position_ovr_loader: AbilitySimLoader | None = None

        # 랭커/분석 두 페이지가 각각 갖는 상단 바 위젯들. 함께 갱신·잠금한다.
        self._nick_edits: list[QLineEdit] = []
        self._search_btns: list[QPushButton] = []
        self._acct_combos: list[NoScrollComboBox] = []
        # 왼쪽 아래 버전·업데이트 상태 줄 — 검색 화면·사이드바에 하나씩, 함께 갱신한다
        self._update_status_labels: list[QLabel] = []
        self._update_inline_btns: list[QPushButton] = []

        self.setWindowTitle(config.APP_NAME)  # 버전은 왼쪽 아래(_version_bar) — 사용자 요청 2026-10-02
        self._start_maximized = False  # 작은 화면 첫 실행 — main 이 showMaximized 로 띄운다
        self._watched_screen = None    # 배율·작업 표시줄 변경 신호를 잇고 있는 화면
        self._apply_plan(QApplication.primaryScreen())  # 크기·최소 크기 — 저장값 복원(_load_settings)보다 먼저
        self._build_ui()
        self._refresh_recent()
        self._load_season_cache()  # 시즌 콤보·시즌별 성적 탭이 쓸 시즌표
        # 지난번 창 크기·위치·메뉴·시즌(사용자 요청 2026-10-02). 메뉴·시즌은 첫 계정을 그릴 때 한 번 쓴다
        self._restore = self._load_settings()

    # ── 설정 기억(settings.ini) ────────────────────────────────────────
    def _settings(self) -> QSettings:
        return QSettings(str(config.SETTINGS_PATH), QSettings.Format.IniFormat)

    def _season_key(self, data) -> str:
        if data is None:
            return "all"
        if data == self.ONGOING:
            return "ongoing"
        return f"s{getattr(data, 'no', '')}"

    def _save_settings(self) -> None:
        """닫을 때 — 실패해도 닫기는 막지 않는다(설정 파일 하나 때문에 창이 안 닫히면 안 된다)."""
        try:
            s = self._settings()
            s.setValue("window/geometry", self.saveGeometry())
            page = self._current_page_name() if self.stack.currentIndex() == self.PAGE_MAIN else None
            if page:
                s.setValue("view/page", page)
            for menu, tabs in self._page_tabs.items():  # 메뉴마다 보던 탭 — 키는 ASCII sid, 탭 이름(한글)은 값으로만
                if tabs.current_name():
                    s.setValue(f"view/tab/{self._tab_sid[menu]}", tabs.current_name())
            if self._matches_all:
                s.setValue("view/season", self._season_key(self.cb_season.currentData()))
            s.sync()
        except Exception:
            pass

    def _load_settings(self) -> dict:
        """창 크기·위치는 바로, 메뉴·시즌은 돌려줘서 첫 계정을 그릴 때 쓴다.

        restoreGeometry 는 화면보다 큰 저장값을 화면에 맞게 줄이고 화면 밖이면 보이는 곳으로 옮긴다(Qt 동작) —
        단, 최소 크기가 그보다 크게 먼저 걸려 있으면 줄임이 무시돼 넘친다(실측). 그래서 __init__ 에서 화면 판정과
        최소 크기를 먼저 정해 두고, 복원한 뒤 창이 실제로 놓인 화면 기준으로 다시 맞춘다."""
        try:
            s = self._settings()
            geo = s.value("window/geometry")
            restored = geo is not None and self.restoreGeometry(geo)
            if restored:
                screens = QApplication.screens()
                if screens and not any(sc.availableGeometry().intersects(self.frameGeometry())
                                       for sc in screens):
                    restored = False  # 그 모니터가 없다 — 처음 실행처럼
            if restored:
                self._apply_plan(self._screen_of_window(), keep_size=True)
            else:
                self._apply_plan(QApplication.primaryScreen(), center=True)
            out = {k: s.value(f"view/{k}") for k in ("page", "season") if s.value(f"view/{k}")}
            tabs = {sid: s.value(f"view/tab/{sid}") for sid in self._tab_sid.values() if s.value(f"view/tab/{sid}")}
            if tabs:
                out["tabs"] = tabs
            return out
        except Exception:
            return {}

    # ── 창 크기 — 화면에 맞추기(1.0.3) ──────────────────────────────────
    def _screen_of_window(self):
        """창 가운데가 있는 모니터 — 없으면 주 모니터."""
        return (QApplication.screenAt(self.frameGeometry().center())
                or self.screen() or QApplication.primaryScreen())

    def _apply_plan(self, screen, keep_size: bool = False, center: bool = False) -> WindowPlan | None:
        """그 화면에 맞는 최소 크기를 걸고, 창이 화면보다 크면 줄인다.
        keep_size: 저장된 크기를 살린다(화면보다 클 때만 줄임). 아니면 규칙의 기본 크기로."""
        if screen is None:
            self.resize(*DEFAULT_WINDOW)
            self.setMinimumSize(*MIN_WINDOW)
            return None
        avail = screen.availableGeometry()
        plan = initial_window(avail.width(), avail.height())
        self.setMinimumSize(*plan.min_size)
        fw, fh = FRAME_ALLOWANCE
        if keep_size:
            w = min(self.width(), max(plan.min_size[0], avail.width() - fw))
            h = min(self.height(), max(plan.min_size[1], avail.height() - fh))
            if (w, h) != (self.width(), self.height()):
                self.resize(w, h)
        else:
            self.resize(*plan.size)  # 최대화로 열어도 풀었을 때 이 크기 — 화면을 넘는 1600x900 으로 돌아가지 않게
            self._start_maximized = plan.maximized
        self._center_on_show = center
        if center:
            self._move_inside(avail, center=True)
        self._narrow_screen = plan.narrow
        return plan

    def show_initial(self) -> None:
        """main 에서 — 작은 화면이면 최대화로 띄우고, 띄운 뒤 실제 테두리로 한 번 더 확인한다."""
        self._shown_once = True
        if self._start_maximized:
            self.showMaximized()
        else:
            self.show()
        QApplication.processEvents()
        if not self.isMaximized():
            # 띄우기 전엔 제목줄이 없어, 그때 맞춘 가운데는 띄운 뒤 제목줄만큼 아래로 밀려 화면 아래를 넘었다
            # (실화면 실측: 쓸 수 있는 높이 688 에서 1px 넘침). 실제 테두리로 다시 맞춘다.
            screen = self._screen_of_window()
            if screen is not None:
                self._move_inside(screen.availableGeometry(), center=getattr(self, "_center_on_show", False))
        self._check_fits()
        self._notify_narrow_once()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        # 최대화를 풀면 띄우기 전에 정한(제목줄 없던) 위치로 돌아가 아래가 넘쳤다 — 풀린 뒤 화면 안으로.
        # 상태 신호가 올 땐 창이 아직 최대화 자리에 있고, 윈도우가 원래 위치로 되돌리는 건 그 뒤라(실화면 실측
        # 1.875초 신호 → 1.934초 이동) 신호에서 바로 맞추면 헛돈다 → 그 뒤 첫 이동·크기 변경에서 맞춘다.
        if e.type() == QEvent.Type.WindowStateChange:
            self._settle_after_restore = not (self.isMaximized() or self.isMinimized())
            if getattr(self, "_notice_after_restore", False) and not self.isMinimized():  # __init__ 중에도 온다
                self._notice_after_restore = False
                # 상태가 바뀐 직후엔 창이 아직 제자리가 아니다 — 한 바퀴 뒤 그 창 위에
                QTimer.singleShot(0, self.ask_notice_update_once)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self._settle_if_restored()
        self._follow_dialogs()

    def _follow_dialogs(self) -> None:
        """열린 대화상자(안내·정보·선수 카드 등)가 메인 창을 따라온다 — 메인 창만 다른 모니터로 옮겨져 안내 창이 주
        모니터에 홀로 남았다(2026-10-06 실측). 모달이 떠 있는 동안 사용자는 메인 창을 못 끌므로, 여기 오는 건 윈도우·
        다른 프로그램이 옮긴 경우뿐이다."""
        for d in self.findChildren(QDialog, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if d.isVisible():
                place_over_parent(d)

    # resizeEvent 는 아래(업데이트 카드 자리 잡기)에 하나만 — 두 번 정의하면 뒤의 것만 남는다
    def _settle_if_restored(self) -> None:
        if getattr(self, "_settle_after_restore", False):
            self._settle_after_restore = False
            QTimer.singleShot(0, self._keep_on_screen)

    def _keep_on_screen(self) -> None:
        if self.isMaximized() or self.isMinimized() or not self.isVisible():
            return
        screen = self._screen_of_window()
        if screen is not None and not screen.availableGeometry().contains(self.frameGeometry()):
            self._move_inside(screen.availableGeometry())

    def _move_inside(self, avail, center: bool = False) -> None:
        """창(테두리 포함)을 쓸 수 있는 영역 안으로 — center 면 가운데로."""
        fg = self.frameGeometry()
        if center:
            fg.moveCenter(avail.center())
        x = min(max(fg.left(), avail.left()), avail.right() - fg.width() + 1)
        y = min(max(fg.top(), avail.top()), avail.bottom() - fg.height() + 1)
        if (x, y) != (self.frameGeometry().left(), self.frameGeometry().top()):
            self.move(x, y)

    def _check_fits(self) -> None:
        """띄운 뒤 — 실제 테두리 포함 크기가 쓸 수 있는 영역을 넘으면 최대화(FRAME_ALLOWANCE 추정이 틀렸을 때)."""
        if self.isMaximized() or self.isFullScreen():
            return
        screen = self._screen_of_window()
        if screen is None:
            return
        avail, fg = screen.availableGeometry(), self.frameGeometry()
        if fg.width() > avail.width() or fg.height() > avail.height():
            self.showMaximized()

    def _notify_narrow_once(self) -> None:
        """폭조차 1280 이 안 되는 화면 — 막는 창 대신 상태줄로, 처음 한 번만."""
        if not getattr(self, "_narrow_screen", False):
            return
        try:
            s = self._settings()
            if s.value("window/narrow_hinted"):
                return
            s.setValue("window/narrow_hinted", 1)
        except Exception:
            pass
        self.statusBar().showMessage(NARROW_SCREEN_MSG, 30000)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        handle = self.windowHandle()
        if handle is not None and not getattr(self, "_screen_hooked", False):
            self._screen_hooked = True
            handle.screenChanged.connect(self._on_screen_changed)
            self._watch_screen(handle.screen())

    def _watch_screen(self, screen) -> None:
        """그 화면의 배율·작업 표시줄 변경도 같은 재판정으로 — 모니터 이동(screenChanged)과 다른 신호다."""
        old = self._watched_screen
        if old is not None:
            for sig in (old.availableGeometryChanged, old.logicalDotsPerInchChanged):
                try:
                    sig.disconnect(self._on_screen_metrics_changed)
                except (TypeError, RuntimeError):
                    pass
        self._watched_screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._on_screen_metrics_changed)
            screen.logicalDotsPerInchChanged.connect(self._on_screen_metrics_changed)

    def _on_screen_changed(self, screen) -> None:
        self._watch_screen(screen)
        # 한 바퀴 뒤에 — 모니터를 옮기는 순간 Qt 가 배율에 맞춰 크기를 다시 정하며 여기서 맞춘 크기를 덮어썼다(실측)
        QTimer.singleShot(0, lambda: self._refit(screen))

    def _on_screen_metrics_changed(self, *_) -> None:
        QTimer.singleShot(0, lambda: self._refit(self._watched_screen))

    def _refit(self, screen) -> None:
        """창이 다른 화면으로 갔거나 화면이 바뀌었다 — 최소 크기를 다시 정하고, 넘치면 맞춘다."""
        if screen is None:
            return
        if self.isMaximized() or self.isFullScreen():
            self.setMinimumSize(*initial_window(screen.availableGeometry().width(),
                                                screen.availableGeometry().height()).min_size)
            return
        self._apply_plan(screen, keep_size=True)
        avail, fg = screen.availableGeometry(), self.frameGeometry()
        if not avail.contains(fg):  # 크기는 맞췄는데 일부가 화면 밖 — 안으로 옮긴다
            self._move_inside(avail)

    # ── UI ────────────────────────────────────────────────────────────
    PAGE_SEARCH, PAGE_MAIN = 0, 1

    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_search_page())  # 0
        self.stack.addWidget(self._build_main_page())    # 1
        self.setCentralWidget(self.stack)
        self.stack.currentChanged.connect(self._on_stack_changed)  # 메인으로 가면 '최신 버전' 카드를 닫는다

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(14)
        self.statusBar().addPermanentWidget(self.progress, 1)
        # 새 버전 카드 — 창 오른쪽 아래, 어느 화면 위에든 같은 자리(사용자 요청 2026-10-02)
        self.update_card = UpdateCard(self)
        self.update_card.update_clicked.connect(self._on_update_clicked)
        self._release: updatecheck.Release | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._download_worker: UpdateDownloadWorker | None = None
        self.statusBar().showMessage("구단주명을 입력하세요.")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.update_card.place()
        self._settle_if_restored()

    def start_update_check(self) -> None:
        """켤 때 한 번 + 트레이 상주 중 UPDATE_CHECK_EVERY_H 마다(tray.AppShell) — 테스트·스크린샷이 네트워크를
        안 타게 main·껍데기에서만 부른다. 앞 확인이 아직 돌거나 내려받는 중이면 건너뛴다."""
        if (self._update_worker and self._update_worker.isRunning()) or \
                (self._download_worker and self._download_worker.isRunning()):
            return
        self._update_worker = UpdateCheckWorker()
        self._update_worker.found.connect(self._on_update_found)
        self._update_worker.latest.connect(self._on_update_latest)
        self._update_worker.unknown.connect(self._on_update_unknown)
        self._update_worker.season_notice.connect(self._on_season_notice)
        self._set_update_status("업데이트 확인 중…")
        self._update_worker.start()

    def _on_season_notice(self, notice) -> None:
        """릴리스 본문의 시즌 종료일 공지를 저장 — 받을 때는 형식·범위만 봤고, 지금 시즌과의 대조는 쓸 때
        (predict.resolve_notice). 공지가 빠진 릴리스가 와도 마지막 저장값을 쓴다 — 그 시즌 동안만(대조가 버린다)."""
        end, start = notice
        s = self._settings()
        if s.value("season/end") == end.isoformat() and s.value("season/start") == start.isoformat():
            return
        s.setValue("season/end", end.isoformat())
        s.setValue("season/start", start.isoformat())
        s.sync()
        self._load_predict(self._ouid)

    def _season_notice(self):
        s = self._settings()
        try:
            return (datetime.fromisoformat(str(s.value("season/end"))).date(),
                    datetime.fromisoformat(str(s.value("season/start"))).date())
        except (TypeError, ValueError):
            return None

    def ask_notice_update_once(self) -> None:
        """옛 동의자에게 바뀐 안내를 다시 묻는다 — 창이 **사용자에게 보일 때** 실행당 한 번(숨긴 창에서 띄우면 게임 중
        초점을 뺏는다). 일반 실행 · 트레이 [열기] · 두 번째 실행의 "창 앞으로"가 부른다. 취소하면 옛 동의 그대로."""
        if self._notice_asked or not config.notice_update_pending() or not self.isVisible():
            return
        if self.isMinimized():
            # isVisible 은 최소화돼도 참이다. 그대로 띄우면 부모 자리를 몰라 주 모니터(게임 중) 가운데에 떴다
            # (2026-10-06 사용자 — exe 를 최소화로 켜 2번 모니터로 옮기는 사이) → 창이 돌아오면 그때(changeEvent)
            self._notice_after_restore = True
            return
        self._notice_asked = True
        self.ask_notice_update()

    def ask_notice_update(self) -> bool:
        ok = NoticeDialog(self, reask=True).exec() == QDialog.DialogCode.Accepted
        self._render_elo()   # 따라가기 버튼 툴팁이 동의 여부를 따른다
        return ok

    def attach_rank_sched(self, sched: RankCollectScheduler) -> None:
        """랭킹 수집 예약은 창 밖(tray.AppShell)에 산다 — 창을 숨기거나 기록을 내려놓아도 돌게. 창은 상태만 받는다."""
        self._rank_sched = sched
        sched.status.connect(self._on_rank_collect_status)
        sched.outcome.connect(self._on_rank_collect_outcome)

    def _on_rank_collect_outcome(self, out) -> None:
        """수집 회차가 끝났다(UI 스레드) — 따라가기 점·컷이 새로 생겼을 수 있으니 지금 계정 ELO 를 다시 읽는다(작업 스레드)."""
        if getattr(out, "kind", None) in ("ok", "fresh") and self._ouid:
            self._load_elo(self._ouid)

    def _on_rank_collect_status(self, text: str, important: bool) -> None:
        # 검색이 돌 땐 그쪽 진행이 상태줄 주인이다 — 중요한 알림(스스로 꺼짐·연속 실패)만 덮는다
        if important or not (self._loader and self._loader.isRunning()):
            self.statusBar().showMessage(text, 0 if important else 15000)

    def start_cache_prune(self) -> None:
        """켤 때 한 번 — main 에서만 부른다(테스트가 실제 캐시 폴더를 건드리지 않게)."""
        self._prune_worker = CachePruneWorker(self._api)
        self._prune_worker.start()

    def start_prefetch(self) -> None:
        """켤 때 한 번 — 마지막으로 검색한 계정의 저장된 경기를 뒤에서 읽어 둔다(화면은 검색 화면 그대로).
        그 계정을 검색하면 DB 읽기를 건너뛴다. main 과, 검색 전에 내려놓았다 다시 열 때(reload_after_release) 부른다."""
        last = self._last_account()
        if last:
            self._prefetch = SavedPrefetch(last[0], config.DEFAULT_MATCH_TYPE)

    def _set_update_status(self, text: str, button: str = "") -> None:
        """왼쪽 아래 상태 칸 — 늘 보이는 자리(카드는 새 버전일 때만 잠깐 눈에 띄게)."""
        for lb in self._update_status_labels:
            lb.setText(text)
        for b in self._update_inline_btns:
            b.setText(button)
            b.setVisible(bool(button))

    def _on_update_latest(self) -> None:
        self._set_update_status("최신 버전입니다")
        # 카드는 첫 검색 화면에서만 — 메인 화면(대시보드 등)에선 내용을 가린다(사용자 요청 2026-10-02).
        # 메인에선 왼쪽 아래 상태 칸이 같은 말을 한다. 새 버전 카드는 놓치면 안 되니 어디서든 뜬다.
        if self.stack.currentIndex() == self.PAGE_SEARCH:
            self.update_card.show_latest(config.APP_VERSION)

    def _on_stack_changed(self, idx: int) -> None:
        card = getattr(self, "update_card", None)  # 창을 만드는 도중엔 아직 없다
        if card is not None and idx == self.PAGE_MAIN and card.mode == "latest":
            card.hide()

    def _on_update_unknown(self) -> None:
        # 확인을 못 했는데 '최신'이라 하면 거짓말 — 그렇다고 말한다
        self._set_update_status("업데이트 확인 못 함")

    def _on_update_found(self, rel) -> None:
        self._release = rel
        installed = updatecheck.install_dir() is not None and bool(rel.setup_url)
        self.update_card.show_release(rel.tag, config.APP_VERSION, installed)
        self._set_update_status(f"새 버전 {rel.tag}", "업데이트" if installed else "받으러 가기")

    def _on_update_clicked(self) -> None:
        rel = self._release
        if rel is None:
            return
        if updatecheck.install_dir() is None or not rel.setup_url:
            # 포터블·소스 실행 — 설치 위치를 앱이 관리하지 않으니 페이지만 연다
            QDesktopServices.openUrl(QUrl(rel.page_url))
            return
        notes = f"\n\n바뀐 점:\n{rel.notes}" if rel.notes else ""
        ans = QMessageBox.question(
            self, "업데이트",
            f"{config.APP_VERSION} → {rel.tag} 로 업데이트할까요?\n"
            f"내려받은 뒤 앱이 닫히고, 설치가 끝나면 다시 켜집니다. 전적 기록은 그대로입니다.{notes}")
        if ans != QMessageBox.StandardButton.Yes:
            return
        # 카드를 [나중에]로 닫았어도 왼쪽 아래 버튼으로 올 수 있다 — 진행은 카드에서 보이게 다시 띄운다
        self.update_card.show()
        self.update_card.raise_()
        for b in self._update_inline_btns:
            b.setEnabled(False)
        self.update_card.set_busy(True, "내려받는 중…")
        self._download_worker = UpdateDownloadWorker(rel)
        self._download_worker.progress.connect(self.update_card.set_progress)
        self._download_worker.done.connect(self._on_update_downloaded)
        self._download_worker.failed.connect(self._on_update_failed)
        self._download_worker.start()

    def _on_update_downloaded(self, path: str) -> None:
        try:
            updatecheck.launch_installer(Path(path))
        except OSError as e:
            self._on_update_failed(f"설치 파일을 실행하지 못했습니다: {e}")
            return
        self._quit_app()  # 파일이 잠기지 않게 바로 끝낸다 — 설치 파일이 끝나면 다시 켠다(트레이로 숨기면 안 된다)

    def _quit_app(self) -> None:
        if self.shell is not None:
            self.shell.quit_app()
        else:
            self.close()

    def _on_update_failed(self, msg: str) -> None:
        self.update_card.set_busy(False)
        for b in self._update_inline_btns:
            b.setEnabled(True)
        if self._release:
            self.update_card.show_release(self._release.tag, config.APP_VERSION, True)
        page = self._release.page_url if self._release else config.RELEASES_URL
        QMessageBox.warning(self, "업데이트 실패", f"{msg}\n\n직접 받기: {page}")

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

        title = QLabel(config.APP_NAME)
        title.setObjectName("searchTitle")
        f = QFont()
        f.setPointSize(30)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {T.GREEN}; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_v.addWidget(title)

        sub = QLabel("넥슨·EA 와 무관한 비공식 프로그램")
        sub.setStyleSheet(f"color: {T.TEXT_DIM}; border: none; font-size: 14px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_v.addWidget(sub)
        box_v.addSpacing(24)

        row = QHBoxLayout()
        row.addStretch(1)
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("bigSearch")
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

        add_shadow(box)
        centering.addWidget(box)
        centering.addStretch(1)
        outer.addLayout(centering)

        outer.addStretch(2)
        outer.addWidget(self._attribution_label(), 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self._about_button(), 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self._version_bar(), 0, Qt.AlignmentFlag.AlignLeft)  # 왼쪽 아래 구석
        return w

    def _version_bar(self) -> QWidget:
        """버전 + 업데이트 상태 — 창 제목 대신 왼쪽 아래(검색 화면 구석 · 메인 화면 사이드바 맨 아래).

        "v0.3.1 · 최신 버전입니다" / "v0.3.1 · 새 버전 v0.3.2" + [업데이트] 버튼(아랫줄 — 사이드바
        230px 에 한 줄로 안 들어간다). 상태줄 왼쪽은 안 쓴다 — showMessage 가 늘 덮어 가려진다.
        사용자 요청(2026-10-02): 잠깐 뜨는 카드가 아니라 화면에 늘 보이는 자리."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        style = f"color: {T.TEXT_DIM}; font-size: 11px; border: none;"
        ver = QLabel(config.APP_VERSION)
        ver.setObjectName("versionLabel")
        ver.setStyleSheet(style)
        status = QLabel("")
        status.setObjectName("updateStatus")
        status.setStyleSheet(style)
        row.addWidget(ver)
        row.addWidget(status)
        row.addStretch(1)
        v.addLayout(row)
        btn = QPushButton("")
        btn.setObjectName("updateInline")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(T.OUTLINE_BUTTON_QSS)
        btn.clicked.connect(self._on_update_clicked)
        btn.setVisible(False)
        v.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._update_status_labels.append(status)
        self._update_inline_btns.append(btn)
        return w

    def _about_button(self) -> QPushButton:
        """[정보] — 이용 안내·개인정보·라이선스, 넥슨 홈페이지 데이터 켜고 끄기. 두 화면에 하나씩."""
        btn = QPushButton("정보 · 이용 안내")
        btn.setObjectName("aboutButton")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; border: none;"
                          " text-decoration: underline; padding: 0;")
        btn.clicked.connect(self._open_about)
        return btn

    def _open_about(self) -> None:
        AboutDialog(self).exec()

    @staticmethod
    def _attribution_label() -> QLabel:
        """넥슨 오픈API 약관 제6조④ 출처 표기 — API 데이터가 보이는 화면마다 둔다."""
        lb = QLabel(ATTRIBUTION)
        lb.setObjectName("attribution")
        lb.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; border: none;")
        return lb

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

    def _top_bar(self) -> QFrame:
        """상단 바 — 재검색·등록 계정 + 시즌·표시 범위·새 경기 확인.

        어느 메뉴에 있든 같은 필터가 걸린다. 예전엔 랭커/분석 두 페이지가 바를
        하나씩 가져서 리스트(_nick_edits 등)로 묶었는데, 이제 하나뿐이어도
        조작 코드는 그대로 리스트를 돈다.
        """
        def group() -> tuple[QWidget, QHBoxLayout]:
            g = QWidget()
            lay = QHBoxLayout(g)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            return g, lay

        left, h = group()
        back = QPushButton("← 검색")
        back.clicked.connect(self._go_search)
        ed = QLineEdit()
        ed.setPlaceholderText("구단주명")
        ed.setFixedWidth(170)
        ed.returnPressed.connect(self._on_search)
        btn = QPushButton("조회")
        btn.setObjectName("primary")
        btn.clicked.connect(self._on_search)
        cb = NoScrollComboBox()
        cb.setMinimumWidth(150)
        cb.activated.connect(self._on_pick_account)
        lb_acct = QLabel("등록")
        lb_acct.setStyleSheet(f"color: {T.TEXT_DIM};")
        for x in (back, ed, btn):
            h.addWidget(x)
        h.addSpacing(6)
        h.addWidget(lb_acct)
        h.addWidget(cb)
        self._nick_edits.append(ed)
        self._search_btns.append(btn)
        self._acct_combos.append(cb)

        sep = QFrame()
        sep.setFixedSize(1, 26)
        sep.setStyleSheet(f"background: {T.BORDER}; border: none;")

        middle, h = group()
        # 시즌 필터가 먼저 걸리고, 그 안에서 시작~끝 판수를 자른다.
        lb_season = QLabel("시즌")
        lb_season.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.cb_season = NoScrollComboBox()
        self.cb_season.addItem("전체", None)
        self.cb_season.currentIndexChanged.connect(self._on_season_changed)
        h.addWidget(lb_season)
        h.addWidget(self.cb_season)
        h.addSpacing(8)

        # 표시 범위 — 시작~끝을 직접 입력해 그 구간만 본다. 검색 시 이미 전량을
        # 받아 두므로 "새 경기 확인"은 그 사이 새로 생긴 경기가 있는지 다시
        # 확인하는 버튼이다.
        self.sp_from = QSpinBox()
        self.sp_from.setRange(1, 1)
        self.sp_from.setFixedWidth(68)
        self.sp_from.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        lb_tilde = QLabel("~")
        lb_tilde.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.sp_to = QSpinBox()
        self.sp_to.setRange(1, 1)
        self.sp_to.setFixedWidth(68)
        self.sp_to.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.btn_apply = QPushButton("적용")
        self.btn_apply.setStyleSheet(T.OUTLINE_BUTTON_QSS)
        self.btn_apply.clicked.connect(self._apply_range)
        self.lb_total = QLabel("")
        self.lb_total.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.btn_more = QPushButton("⟳  새 경기 확인")
        self.btn_more.setObjectName("primary")
        self.btn_more.setToolTip(
            "검색할 때 이미 받을 수 있는 만큼 전부 받아 둡니다.\n"
            "이 버튼은 그 사이 새로 생긴 경기가 있는지 다시 확인합니다.")
        self.btn_more.clicked.connect(self._on_search)
        for x in (self.sp_from, lb_tilde, self.sp_to, self.btn_apply):
            h.addWidget(x)
        h.addSpacing(6)
        h.addWidget(self.lb_total)
        self.top_bar = WrapBar(left, middle, self.btn_more, sep=sep)
        return self.top_bar

    # 왼쪽 메뉴 — (묶음 제목, [(메뉴 이름, 페이지 빌더 이름 또는 Tabs)]). 묶음 제목이
    # None 이면 제목 없이 바로 메뉴. 페이지 순서는 이 표의 순서다.
    # 2.1.1 — 한 메뉴 안의 구획은 페이지 안 탭(Tabs). sid 는 보던 탭을 기억하는 설정 키(view/tab/<sid>) —
    # 메뉴 이름(한글)을 설정 키로 쓰지 않는다. 탭 하나 = 예전 구획 위젯 그대로(그리기 코드는 안 바꿨다).
    NAV = [
        (None, [("대시보드", "_build_dashboard_page")]),
        ("경기", [("경기 목록", "_build_matches_tab"),
                 ("상대 전적", "_build_opponents_tab"),
                 ("구단주 비교", "_build_compare_tab")]),
        ("흐름", [("흐름 분석", "_build_analysis_tab"),
                 ("승률 그래프", Tabs("trend", (("승률·등급", "_build_trend_tab"),
                                              ("점수·예측", "_build_elo_tab")))),
                 ("기간별 추이", "_build_period_tab"),
                 ("시즌별 성적", "_build_season_tab")]),
        ("경기력", [("승부처 분석", "_build_clutch_tab"),
                   ("성적 진단", Tabs("diagnosis", (("상대·점유율", "_build_diagnosis_tab"),
                                                ("규율·불운", "_build_discipline_tab")))),
                   ("전술·경기 결과", Tabs("tactics", (("전술", "_build_tactics_tab"),
                                                   ("경기 결과", "_build_results_tab"),
                                                   ("패스 스타일", "_build_pass_style_tab")))),
                   ("슛 맵", "_build_shotmap_tab")]),
        ("선수", [("선수 지표", Tabs("players", (("지표", "_build_players_tab"),
                                             ("랭커 비교", "_build_ranker_compare_tab")))),
                 ("선수별 결정력", "_build_finishing_tab"),
                 ("포지션별 최다 상대", "_build_position_opp_tab"),
                 ("스쿼드·이적", Tabs("squad", (("타임라인", "_build_timeline_tab"),
                                             ("가계부", "_build_ledger_tab"))))]),
        ("팀컬러", [("팀컬러 승률", "_build_teamcolor_rate_tab"),
                   ("팀컬러 랭킹", "_build_teamcolor_rank_tab")]),
        # 2.1.1 새 묶음 — 16·17단계가 채우며 config.HIDDEN_NAV_UNTIL_READY 에서 하나씩 뺀다
        ("랭커", [("랭킹 추이", "_build_pending_page"),
                 ("랭커 픽", Tabs("rankerpick", (("픽", "_build_pending_page"),
                                              ("추천", "_build_pending_page")))),
                 ("선수로 구단주 찾기", "_build_pending_page")]),
    ]
    # 1.x 메뉴 이름 → 2.1.1 의 (메뉴, 탭). settings.ini 의 view/page 가 옛 이름이면 새 자리로 연다(U4 — 알림 띠는 없다)
    OLD_PAGE_NAMES = {
        "랭커와 비교": ("선수 지표", "랭커 비교"),
        "스쿼드 타임라인": ("스쿼드·이적", "타임라인"),
        "이적시장 가계부": ("스쿼드·이적", "가계부"),
    }

    def _build_main_page(self) -> QWidget:
        """검색 이후 화면 — 왼쪽 메뉴 + (상단 바 / 메뉴별 페이지)."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(T.SIDEBAR_W)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 20, 0, 14)
        sv.setSpacing(2)
        brand = QLabel("감독모드")  # config.APP_NAME 을 두 줄로 — 사이드바 폭에 한 줄로 안 들어간다
        brand.setObjectName("brand")
        brand_sub = QLabel("전적 분석 · 비공식")
        brand_sub.setObjectName("brandSub")
        for lb in (brand, brand_sub):
            lb.setContentsMargins(22, 0, 0, 0)
            sv.addWidget(lb)
        sv.addSpacing(14)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sv.addWidget(self.nav, 1)

        prof = QFrame()
        prof.setObjectName("profileBox")
        pv = QVBoxLayout(prof)
        pv.setContentsMargins(12, 10, 12, 10)
        pv.setSpacing(2)
        self.lb_profile = QLabel("-")
        pf = QFont()
        pf.setPointSize(12)
        pf.setBold(True)
        self.lb_profile.setFont(pf)
        self.lb_sub = QLabel("-")
        self.lb_sub.setWordWrap(True)
        self.lb_sub.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        pv.addWidget(self.lb_profile)
        pv.addWidget(self.lb_sub)
        pwrap = QHBoxLayout()
        pwrap.setContentsMargins(12, 0, 12, 0)
        pwrap.addWidget(prof)
        sv.addLayout(pwrap)
        sv.addSpacing(8)
        attr = self._attribution_label()
        attr.setWordWrap(True)  # 사이드바 폭이 고정이라 넘치면 접는다
        attr.setContentsMargins(22, 0, 12, 0)
        sv.addWidget(attr)
        about = QHBoxLayout()
        about.setContentsMargins(22, 0, 12, 0)
        about.addWidget(self._about_button())
        about.addStretch(1)
        sv.addLayout(about)
        ver = self._version_bar()
        ver.setContentsMargins(22, 4, 12, 0)
        sv.addWidget(ver)
        h.addWidget(side)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(20, 16, 20, 14)
        rv.setSpacing(14)
        rv.addWidget(self._top_bar())
        self.pages = QStackedWidget()
        rv.addWidget(self.pages, 1)
        h.addWidget(right, 1)

        # 팀컬러 두 탭이 빌드 중에 채우는 목록 — 빌더보다 먼저 있어야 한다.
        self._teamcolor_fetch_btns: list[QPushButton] = []
        self._teamcolor_status_labels: list[QLabel] = []
        self._teamcolor_note_labels: list[QLabel] = []
        self._page_index: dict[str, int] = {}
        self._page_tabs: dict[str, PageTabs] = {}   # 메뉴 이름 → 페이지 안 탭(Tabs 인 메뉴만)
        self._tab_sid: dict[str, str] = {}          # 메뉴 이름 → 보던 탭 설정 키(view/tab/<sid>)
        for section, entries in self.NAV:
            if section:
                head = QListWidgetItem(section)
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                f = QFont()
                f.setPointSize(9)
                f.setBold(True)
                head.setFont(f)
                self.nav.addItem(head)
                # 묶음의 메뉴가 전부 숨김이면 제목도 숨긴다
                head.setHidden(all(n in config.HIDDEN_NAV_UNTIL_READY for n, _b in entries))
            for name, builder in entries:
                if isinstance(builder, Tabs):
                    tabs = PageTabs()
                    for tab_name, tab_builder in builder.tabs:
                        tabs.add_tab(tab_name, getattr(self, tab_builder)())
                    tabs.changed.connect(lambda _i, n=name: self._on_tab_changed(n))
                    self._page_tabs[name], self._tab_sid[name] = tabs, builder.sid
                    page = self._wrap_page(name, tabs)
                    page.add_to_title_row(tabs.take_bar())
                else:
                    page = getattr(self, builder)()
                    if builder != "_build_dashboard_page":
                        page = self._wrap_page(name, page)
                idx = self.pages.addWidget(VScrollArea(page))
                self._page_index[name] = idx
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, idx)
                self.nav.addItem(item)
                item.setHidden(name in config.HIDDEN_NAV_UNTIL_READY)
        self.nav.currentItemChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)
        return w

    @staticmethod
    def _wrap_page(title: str, body: QWidget) -> QWidget:
        """기존 탭 내용을 제목 달린 흰 카드 하나로 감싼다(내용은 그대로)."""
        card = Card(title)
        card.body.addWidget(body, 1)
        return card

    def _on_nav_changed(self, cur, _prev=None) -> None:
        if cur is None:
            return
        idx = cur.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self.pages.setCurrentIndex(idx)
            self._on_view_opened()

    def _on_tab_changed(self, menu: str) -> None:
        """탭 클릭(E2) — 그 메뉴가 지금 보이는 메뉴일 때만. 메뉴 클릭과 같은 길(_on_view_opened)."""
        if self._current_page_name() == menu:
            self._on_view_opened()

    def _on_view_opened(self) -> None:
        """메뉴나 탭으로 한 자리(메뉴, 탭)를 열었다 — 낡았으면 지금 그린다(_render_all 은 보이는 것만 그린다)."""
        if self.KEY_OF_VIEW.get(self._current_view()) == "trades":
            self._dirty.add("trades")  # 상태는 DB 에 있다 — 열 때마다 다시 읽는다(키 확인 중 · 받는 중)
            self.start_trades()
        self._render_current_page()

    def _go_page(self, name: str, tab: str | None = None) -> None:
        """그 메뉴(와 탭)로 — 탭이 None 이면 그 메뉴의 **첫 탭**(기억된 탭이 아니라 — 대시보드 카드가 늘 같은 자리로).
        없는 이름·숨긴 메뉴·없는 탭이면 아무것도 안 한다(예전엔 idx=None 이 묶음 제목 줄과 같다고 판정돼 제목 줄로 갔다)."""
        idx = self._page_index.get(name)
        if idx is None or name in config.HIDDEN_NAV_UNTIL_READY:
            return
        tabs = self._page_tabs.get(name)
        if tabs is not None:
            if tab is not None and tab not in tabs.names():
                return
            # 메뉴를 바꾸기 전에 탭을 먼저 — 바꾼 뒤에 고르면 옛 탭을 한 번 그리고 다시 그린다
            tabs.set_current(tab if tab is not None else 0, emit=self._current_page_name() == name)
        elif tab is not None:
            return
        for row in range(self.nav.count()):
            if self.nav.item(row).data(Qt.ItemDataRole.UserRole) == idx:
                self.nav.setCurrentRow(row)
                return

    def _go_target(self, target: tuple[str, str | None]) -> None:
        """대시보드 카드 대상 (메뉴, 탭 또는 None)."""
        self._go_page(*target)

    def _current_view(self) -> tuple[str | None, str | None]:
        """(지금 메뉴, 그 메뉴의 지금 탭 — 탭 없는 메뉴면 None)."""
        menu = self._current_page_name()
        tabs = self._page_tabs.get(menu or "")
        return menu, (tabs.current_name() if tabs is not None else None)

    def _build_pending_page(self) -> QWidget:
        """아직 빈 메뉴(config.HIDDEN_NAV_UNTIL_READY) — 숨겨 두므로 보통은 안 보인다."""
        lb = QLabel("준비 중입니다.")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        return lb

    def _build_dashboard_page(self) -> QWidget:
        """대시보드 — 카드 배치·채우기는 dashboard.DashboardPage. 랭커 카드만
        여기서 만들어 넘긴다(_render_ranker 가 데이터센터 값으로 채운다)."""
        self.card_ranker = RankerCard()
        add_shadow(self.card_ranker)
        self.dashboard = DashboardPage(self.card_ranker)
        self.dashboard.navigate.connect(self._go_target)
        return self.dashboard

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
        note = QLabel(self._teamcolor_note_text(0, 0))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        self._teamcolor_note_labels.append(note)
        return note

    def _teamcolor_note_text(self, opponents: int, known: int) -> str:
        """몇 명이 반영됐는지를 같이 보인다 — 부분 집계인 걸 숨기지 않게."""
        return (f"{self._scope_text()} · 상대 {opponents:,}명 중 {known:,}명 팀컬러 반영"
                " — 넥슨 감독모드 랭킹 1만 위 안 상대만 찾을 수 있고, 경기 당시가 아니라"
                " 그 상대가 지금 쓰는 팀컬러 기준입니다(지난 시즌일수록 실제와 달라질 수 있음).")

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

    def _build_season_tab(self) -> QWidget:
        """시즌끼리 가로로 비교하는 표. 위 시즌 콤보와 달리 여기는 언제나
        누적 전체(_matches_all)를 시즌별로 나눠 보여준다 — 필터를 걸어 둔
        채로도 "저번 시즌은 어땠지"를 볼 수 있어야 하기 때문."""
        w = QWidget()
        v = QVBoxLayout(w)
        self.lb_season_note = QLabel("")
        self.lb_season_note.setWordWrap(True)
        self.lb_season_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_season_note)
        # 시즌 승률 막대 — 오른쪽 글에 경기 수(GroupedBarChart 는 계열마다 최대값만 적어 경기 수를 못 단다)
        self.season_bars = charts.HBarList()
        v.addWidget(self.season_bars)
        self.tbl_seasons = self._make_table(self.SEASON_COLUMNS)
        v.addWidget(self.tbl_seasons, 1)
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

        # 선택한 기간의 최고·평균·최저 승률 — 예전엔 상단 요약 카드를 이 탭에서만
        # 바꿔치기했는데, 요약 카드가 대시보드로 옮겨 가 여기 따로 둔다.
        trow = QHBoxLayout()
        self.card_trend_max = StatCard("최고 승률", T.GREEN)
        self.card_trend_avg = StatCard("평균 승률")
        self.card_trend_min = StatCard("최저 승률", T.RED)
        self.card_trend_games = StatCard("경기수")
        for c in (self.card_trend_max, self.card_trend_avg,
                  self.card_trend_min, self.card_trend_games):
            trow.addWidget(c)
        v.addLayout(trow)

        self.gb_trend = QGroupBox("최근 30일 승률 추이")
        gv = QVBoxLayout(self.gb_trend)
        # 1.3.1 — 옛 widgets.TrendChart(GREEN 꺾은선)를 대시보드와 같은 그래프로. 50% 기준선 ·
        # 7일 이동평균(달력 7일, 표본 미달이면 끊김) · 아래 띠에 그날 경기 수.
        self.trend_chart = charts.AreaTrendChart()
        gv.addWidget(self.trend_chart)
        v.addWidget(self.gb_trend, 1)

        # 등급 추이 — 매 경기 당시 division 이 상세에 저장돼 있어 추가 조회 없음.
        # 기간(일수)은 위 승률 추이와 같은 스핀박스를 공유한다. 점은 하루 마지막 등급.
        self.gb_division = QGroupBox("등급 추이")
        dv = QVBoxLayout(self.gb_division)
        self.division_chart = DivisionChart()
        dv.addWidget(self.division_chart)
        v.addWidget(self.gb_division, 1)

        # 슈퍼 챔피언스 달성 기록 — 시즌 필터 무시(평생 기록). _render_sc_records 가 채운다.
        self.gb_sc = QGroupBox("슈퍼 챔피언스 달성 기록")
        sv = QVBoxLayout(self.gb_sc)
        self.lb_sc_note = QLabel("")
        self.lb_sc_note.setWordWrap(True)
        self.lb_sc_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.lb_sc_list = QLabel("")
        self.lb_sc_list.setWordWrap(True)
        self.lb_sc_list.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lb_sc_nexon = QLabel("")
        self.lb_sc_nexon.setWordWrap(True)
        self.lb_sc_nexon.setStyleSheet(f"color: {T.TEXT_DIM};")
        for lb in (self.lb_sc_note, self.lb_sc_list, self.lb_sc_nexon):
            sv.addWidget(lb)
        v.addWidget(self.gb_sc)
        return w

    def _build_elo_tab(self) -> QWidget:
        """승률 그래프 [점수·예측] 탭 — ELO(랭킹 점수) 지금 시즌만(시즌 필터와 무관). 읽기는 EloLoader,
        그리기는 _render_elo 가 들고 있는 값으로."""
        w = QWidget()
        v = QVBoxLayout(w)
        self.gb_elo = QGroupBox("ELO(랭킹 점수) — 지금 시즌")
        ev = QVBoxLayout(self.gb_elo)
        top = QHBoxLayout()
        self.lb_elo_tier = QLabel("")
        self.lb_elo_tier.setWordWrap(True)
        self.lb_elo_tier.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        top.addWidget(self.lb_elo_tier, 1)
        self.btn_elo_track = QPushButton("")
        self.btn_elo_track.setStyleSheet(T.OUTLINE_BUTTON_QSS)
        self.btn_elo_track.clicked.connect(self._on_elo_track_clicked)
        top.addWidget(self.btn_elo_track)
        ev.addLayout(top)
        self.lb_elo_note = QLabel("")
        self.lb_elo_note.setWordWrap(True)
        self.lb_elo_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        ev.addWidget(self.lb_elo_note)
        self.elo_chart = charts.AreaTrendChart()
        ev.addWidget(self.elo_chart)
        # 시즌 말 순위 예측(1.3.1) — 숫자를 못 내면 원인 문구. 계산은 PredictWorker(작업 스레드)
        self.lb_elo_predict = QLabel("")
        self.lb_elo_predict.setWordWrap(True)
        self.lb_elo_predict.setStyleSheet(f"color: {T.TEXT};")
        ev.addWidget(self.lb_elo_predict)
        v.addWidget(self.gb_elo)
        v.addStretch(1)
        return w

    PERIOD_CHOICES = [("1일", 1), ("2일", 2), ("1주", 7), ("1개월", 30)]
    PERIOD_COLUMNS = ["기간", "경기", "승", "무", "패", "승률", "평균 대비",
                      "평균득점", "평균실점"]
    PERIOD_HEAT_FULL_PP = 15.0  # 평균 대비 이만큼(%p) 벌어지면 가장 진한 색

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
        # 표와 같은 묶음 단위의 승률 그래프(오래된 것부터) — 단위가 다르면 헷갈린다
        self.period_chart = charts.AreaTrendChart()
        v.addWidget(self.period_chart)
        self.tbl_period = self._make_table(self.PERIOD_COLUMNS)
        v.addWidget(self.tbl_period, 1)
        return w

    def _render_period(self, matches: list[MatchSummary]) -> None:
        periods = period_stats(matches, days=self.cb_period.currentData() or 7)
        self.period_chart.set_data([(p.label, p.win_rate, p.games) for p in reversed(periods)],
                                   counts=True)
        total = sum(p.games for p in periods)
        overall = sum(p.win for p in periods) / total * 100 if total else 0.0
        rows = [[p.label, (str(p.games), p.games), (str(p.win), p.win),
                (str(p.draw), p.draw), (str(p.lose), p.lose),
                (f"{p.win_rate:.1f}%", p.win_rate),
                (f"{p.win_rate - overall:+.1f}%p", p.win_rate - overall),
                (f"{p.avg_gf:.2f}", p.avg_gf), (f"{p.avg_ga:.2f}", p.avg_ga)]
               for p in periods]
        # 색·굵게를 채운 순서대로 붙인 뒤에 정렬을 켠다(표 함정 1번 — 다시 그릴 때 헤더 정렬이 남아 있다)
        self._fill(self.tbl_period, rows, enable_sort=False)
        self._tint_period_rows(periods, overall)
        self.tbl_period.setSortingEnabled(True)
        best_win, best_lose = longest_streaks(matches)
        kind, n = current_streak(matches)
        now_text = f"현재 {n}{kind}" if kind else "현재 -"
        self.lb_streaks.setText(
            f"{now_text} · 최장 연승 {best_win} · 최장 연패 {best_lose}"
            f" ({self._scope_text()} 기준)")

    def _tint_period_rows(self, periods: list, overall: float) -> None:
        """승률·평균 대비 칸 — 평균보다 높으면 HEAT_ATK, 낮으면 HEAT_DEF 쪽으로(불투명, 표 함정 2번).
        최고·최저 기간은 굵게 — 후보는 경기 ≥ MIN_COND 인 기간만(4판짜리 주가 최고가 되지 않게).
        미달 기간은 칠하지 않고 흐리게 + 툴팁. 행 r = periods[r] 이어야 한다(정렬은 이 뒤에 켠다)."""
        t = self.tbl_period
        rate_col = self.PERIOD_COLUMNS.index("승률")
        vs_col = self.PERIOD_COLUMNS.index("평균 대비")
        few = core.MIN_COND
        enough = [i for i, p in enumerate(periods) if p.games >= few]
        best = max(enough, key=lambda i: periods[i].win_rate, default=None)
        worst = min(enough, key=lambda i: periods[i].win_rate, default=None)
        if best is not None and worst is not None and periods[best].win_rate == periods[worst].win_rate:
            best = worst = None  # 전부 같은 승률 — 최고·최저가 없다
        self._period_marks = {"best": best, "worst": worst}
        for r, p in enumerate(periods):
            cells = [t.item(r, rate_col), t.item(r, vs_col)]
            if p.games < few:
                for it in cells:
                    if it:
                        it.setForeground(QColor(T.TEXT_DIM))
                        it.setToolTip(sample_note(p.games, few))
                continue
            diff = p.win_rate - overall
            frac = min(abs(diff) / self.PERIOD_HEAT_FULL_PP, 1.0)
            for it in cells:
                self._heat(it, frac, T.HEAT_ATK if diff > 0 else T.HEAT_DEF)
            for kind, idx in (("최고", best), ("최저", worst)):
                if r == idx:
                    for it in cells:
                        if it:
                            f = it.font()
                            f.setBold(True)
                            it.setFont(f)
                            it.setToolTip(f"이 범위에서 승률이 가장 {'높은' if kind == '최고' else '낮은'} 기간"
                                          f"(경기 {few}판 이상 중)")

    def _build_analysis_tab(self) -> QWidget:
        """흐름 분석 — 집계를 문장으로. 최근 흐름 / 이기는 · 지는 패턴."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        self.lb_analysis_note = QLabel(self._analysis_note_text())
        self.lb_analysis_note.setWordWrap(True)
        self.lb_analysis_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_analysis_note)

        gb_dots = QGroupBox(f"최근 {core.WINDOW}경기 결과 (왼쪽이 오래된 경기)")
        dl = QVBoxLayout(gb_dots)
        self.analysis_dots = charts.ResultDots()
        dl.addWidget(self.analysis_dots)
        v.addWidget(gb_dots)

        self.box_analysis: dict[str, QVBoxLayout] = {}
        for sec in core.SECTIONS:
            gb = QGroupBox(sec)
            box = QVBoxLayout(gb)
            box.setSpacing(6)
            self.box_analysis[sec] = box
            v.addWidget(gb)

        gb_streak = QGroupBox("연승·연패 직후 승률")
        self.box_streak = QVBoxLayout(gb_streak)
        self.box_streak.setSpacing(3)
        v.addWidget(gb_streak)

        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _analysis_row(self, ins, color: str) -> QWidget:
        row = QWidget()
        col = QVBoxLayout(row)
        col.setContentsMargins(4, 2, 4, 2)
        col.setSpacing(1)
        head = QLabel(f"· {ins.headline}")
        head.setWordWrap(True)
        head.setStyleSheet(f"color: {color}; font-weight: bold;")
        col.addWidget(head)
        if ins.detail:
            sub = QLabel(f"   {ins.detail}")
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {T.TEXT_DIM};")
            col.addWidget(sub)
        return row

    def _narrate_scope(self) -> list:
        """시즌 범위의 흐름 분석 — 대시보드 '흐름 분석 3줄'과 흐름 분석 메뉴가 같은 결과를 쓴다.

        둘이 각자 계산하던 게 누적 전체 그리기의 55%였다(2026-10-02 프로파일). 범위가 바뀌면
        (시즌 전환·새 경기·다른 계정) 키가 달라져 다시 계산한다 — 목록을 갈아끼우든(id) 앞에
        이어 붙이든(길이·맨 앞 경기) 잡히게."""
        m, d = self._matches, self._details
        key = (self._ouid, id(m), len(m), m[0].match_id if m else None, id(d), len(d))
        if key != self._narrate_key:
            self._narrate_found = core.narrate(m, d, self._ouid)
            self._narrate_key = key
        return self._narrate_found

    def _render_analysis(self) -> None:
        found = self._narrate_scope()
        recent = self._matches[:core.WINDOW][::-1]
        self.analysis_dots.set_data([(m.result, f"{m.date_text} · {m.opponent} · {m.score} {m.result}")
                                     for m in recent])
        colors = {core.SEC_FLOW: T.TEXT,
                  core.SEC_WIN: T.GREEN,
                  core.SEC_LOSE: T.RED}
        for sec, box in self.box_analysis.items():
            self._clear(box)
            rows = [i for i in found if i.section == sec]
            if not rows:
                empty = QLabel("표본이 모자라 아직 말할 수 있는 게 없습니다.")
                empty.setStyleSheet(f"color: {T.TEXT_DIM};")
                box.addWidget(empty)
                continue
            for ins in rows:
                box.addWidget(self._analysis_row(ins, colors[sec]))
            bars = self._basis_bars(rows)
            if bars is not None:
                box.addWidget(bars)
        self._render_streak_after(self._matches)

    @staticmethod
    def _basis_bars(rows: list) -> charts.HBarList | None:
        """섹션 문장 중 조건부 승률(basis)이 있는 것만 막대로 — 조건 승률 vs 기준 승률.

        흐림은 basis.min_n(문장을 낸 표본 기준과 같은 값) — 문장이 나왔으면 흐리지 않다."""
        data = []
        for ins in rows:
            b = ins.basis
            if b is None:
                continue
            gap = b.rate - b.base_rate
            data.append((b.label, b.rate,
                         f"{b.rate:.1f}% · {b.n:,}경기 · {b.base_label} {b.base_rate:.1f}%",
                         f"{b.label}: 승률 {b.rate:.1f}% ({b.n:,}경기)\n"
                         f"{b.base_label} {b.base_rate:.1f}% 대비 {gap:+.1f}%p",
                         b.n < b.min_n))
        if not data:
            return None
        bars = charts.HBarList()
        bars.set_data(data)
        return bars

    def _render_streak_after(self, matches: list) -> None:
        """연승·연패 1·2·3+ 직후 다음 경기 승률 — 막대 + 전체 승률 대비 ±%p.

        흐림 기준은 흐름 분석 문장과 같다(streak_min_n) — 막대는 굵은데 문장은 침묵하는 엇갈림을 없앤다."""
        box = self.box_streak
        self._clear(box)
        s = summarize(matches)
        if not s.total:
            empty = QLabel("경기가 없습니다.")
            empty.setStyleSheet(f"color: {T.TEXT_DIM};")
            box.addWidget(empty)
            return
        need = core.streak_min_n(s.total)
        base = s.win_rate
        note = QLabel(f"전체 승률 {base:.1f}% 기준 · 흐린 줄은 표본 {need}경기 미만")
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        box.addWidget(note)
        for r in core.after_streak_rates(matches, core.STREAK_MAX):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            plus = "+" if r.length == core.STREAK_MAX else ""
            a = QLabel(f"{r.length}{plus}연{r.kind} 뒤")
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            a.setFixedWidth(90)
            h.addWidget(a)
            if r.games:
                gap = r.win_rate - base
                h.addWidget(win_rate_bar(
                    r.win, r.draw, r.lose,
                    f"{r.win_rate:.1f}%  ({wdl_text(r.win, r.draw, r.lose)})  기준 대비 {gap:+.1f}%p",
                    need, height=16), 1)
            else:
                none = QLabel("경기 없음")
                none.setStyleSheet(f"color: {T.TEXT_DIM};")
                h.addWidget(none, 1)
            box.addWidget(row)

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

        gb_heat = QGroupBox("시간대 × 요일 승률")
        hl = QVBoxLayout(gb_heat)
        self.lb_clutch_heat_note = QLabel()
        self.lb_clutch_heat_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.lb_clutch_heat_note.setWordWrap(True)
        hl.addWidget(self.lb_clutch_heat_note)
        self.clutch_heat = charts.HeatmapChart()
        hl.addWidget(self.clutch_heat)
        v.addWidget(gb_heat)

        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _render_clutch(self, details: list[dict],
                       matches: list[MatchSummary]) -> None:
        cs = core.clutch_summary(details, self._ouid)
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
            games = sum(wdl)
            weak = games < core.MIN_COND  # 표본 흐림 규칙 — 글자만 있는 칸도 색 + "표본 N"
            c = QLabel(f"({rate_of(*wdl):.1f}% · 표본 {games})" if weak else f"({rate_of(*wdl):.1f}%)")
            c.setStyleSheet(f"color: {T.TEXT_DIM if weak else color}; font-weight: bold;")
            c.setProperty("weak", weak)
            c.setProperty("games", games)
            if weak:
                c.setToolTip(sample_note(games, core.MIN_COND))
            h.addWidget(a)
            h.addStretch(1)
            h.addWidget(b)
            h.addWidget(c)
            self.box_clutch_first.addWidget(row)
        cb = QLabel(f"역전승 {cs.comeback_win}회 · 역전패 {cs.comeback_lose}회"
                    f"    (무득점·동시각 {cs.goalless}경기 제외)")
        cb.setStyleSheet(f"color: {T.TEXT_DIM}; padding-top: 3px;")
        self.box_clutch_first.addWidget(cb)

        buckets = core.goal_minute_buckets(details, self._ouid)
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
                f"QProgressBar{{background:{T.PANEL_2};border:none;border-radius:3px;"
                f"color:{T.TEXT};text-align:right;padding-right:4px;}}"
                f"QProgressBar::chunk{{background:{T.WIN_BAR};border-radius:3px;}}")
            rbar = QProgressBar()
            rbar.setRange(0, max(peak, 1))
            rbar.setValue(bk.conceded)
            rbar.setFormat(f"{bk.conceded}실")
            rbar.setFixedHeight(16)
            rbar.setStyleSheet(
                f"QProgressBar{{background:{T.PANEL_2};border:none;border-radius:3px;"
                f"color:{T.TEXT};text-align:left;padding-left:4px;}}"
                f"QProgressBar::chunk{{background:{T.LOSE_BAR};border-radius:3px;}}")
            h.addWidget(a)
            h.addWidget(gbar, 1)
            h.addWidget(rbar, 1)
            self.box_clutch_minute.addWidget(row)

        self._clear(self.box_clutch_tod)
        for band in core.time_of_day_rates(matches):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(f"{band.label} ({band.span})")
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            a.setFixedWidth(120)
            if band.games:
                h.addWidget(a)
                h.addWidget(win_rate_bar(
                    band.win, band.draw, band.lose,
                    f"{band.win_rate:.1f}%  ({wdl_text(band.win, band.draw, band.lose)})",
                    core.MIN_COND, height=16), 1)
            else:
                none = QLabel("경기 없음")
                none.setStyleSheet(f"color: {T.TEXT_DIM};")
                h.addWidget(a)
                h.addWidget(none, 1)
            self.box_clutch_tod.addWidget(row)
        self._render_clutch_heat(matches)

    def _render_clutch_heat(self, matches: list[MatchSummary]) -> None:
        """시간대 × 요일 히트맵 — 칸 경기가 MIN_COND 미만이면 색 없이 "표본 N"(소표본 100% 가 가장 진하지 않게)."""
        grid = core.time_weekday_rates(matches)
        few = core.MIN_COND
        cells = []
        for row in grid:
            out = []
            for c in row:
                if not c.games:
                    out.append((None, "—", f"{c.label} ({c.span}시) — 경기 없음", True))
                    continue
                weak = c.games < few
                tip = (f"{c.label} ({c.span}시) — 승률 {c.win_rate:.1f}%"
                       f" ({wdl_text(c.win, c.draw, c.lose)})")
                if weak:
                    tip += f"\n{sample_note(c.games, few)}"
                out.append((c.win_rate, f"표본 {c.games}" if weak else f"{c.win_rate:.0f}%", tip, weak))
            cells.append(out)
        self.clutch_heat.set_data([f"{name} {lo:02d}~{hi:02d}" for name, lo, hi in core.TIME_BANDS],
                                  list(core.WEEKDAYS), cells)
        self.lb_clutch_heat_note.setText(
            f"경기 시작 시각(이 PC 시간대) 기준 · 50%보다 높으면 초록, 낮으면 빨강 쪽 · 회색 칸은 경기 {few}판 미만")

    # ── 성적 진단 ─────────────────────────────────────────────────────────
    def _build_diagnosis_tab(self) -> QWidget:
        """성적 진단 — 상대 등급별·점유율 구간별 승률과 평균 득실."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        self.lb_diag_note = QLabel(self._diag_note_text())
        self.lb_diag_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_diag_note)

        gb_div = QGroupBox("상대 등급별 성적 (강한 등급부터)")
        self.box_diag_division = QVBoxLayout(gb_div)
        self.box_diag_division.setSpacing(3)
        v.addWidget(gb_div)

        gb_pos = QGroupBox("점유율 구간별 승률")
        self.box_diag_possession = QVBoxLayout(gb_pos)
        self.box_diag_possession.setSpacing(3)
        v.addWidget(gb_pos)

        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _build_discipline_tab(self) -> QWidget:
        """성적 진단 [규율·불운] 탭 — 채우기는 _render_diagnosis 가 [상대·점유율] 과 같이 한다(키 diagnosis)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)
        self.lb_diag_note2 = QLabel(self._diag_note_text())
        self.lb_diag_note2.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_diag_note2)
        gb_disc = QGroupBox("규율·불운 (경기당 평균 · 나 / 상대)")
        self.box_diag_discipline = QVBoxLayout(gb_disc)
        self.box_diag_discipline.setSpacing(3)
        v.addWidget(gb_disc)
        v.addStretch(1)
        scroll.setWidget(w)
        return scroll

    def _wr_bar_row(self, label: str, win: int, draw: int, lose: int,
                    avg_gf: float, avg_ga: float, label_w: int = 132) -> QWidget:
        """승률 막대 한 줄 — 라벨 + (승률·전적·평균득실) 막대. 승부처 탭과 같은 톤."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 2, 4, 2)
        a = QLabel(label)
        a.setStyleSheet(f"color: {T.TEXT_DIM};")
        a.setFixedWidth(label_w)
        h.addWidget(a)
        games = win + draw + lose
        if games:
            wr = win / games * 100
            h.addWidget(win_rate_bar(win, draw, lose,
                                     f"{wr:.1f}%  ({wdl_text(win, draw, lose)})  "
                                     f"득 {avg_gf:.2f} 실 {avg_ga:.2f}",
                                     core.MIN_COND), 1)
        else:
            none = QLabel("경기 없음")
            none.setStyleSheet(f"color: {T.TEXT_DIM};")
            h.addWidget(none, 1)
        return row

    def _render_diagnosis(self, details: list[dict]) -> None:
        self._clear(self.box_diag_division)
        divs = core.division_stats(
            details, self._ouid,
            name_of=lambda i: self._division_names.get(i, str(i)))
        if divs:
            for s in divs:
                self.box_diag_division.addWidget(self._wr_bar_row(
                    f"{s.name} ({s.games})", s.win, s.draw, s.lose,
                    s.avg_gf, s.avg_ga))
        else:
            empty = QLabel("상대 등급 정보가 있는 경기가 없습니다.")
            empty.setStyleSheet(f"color: {T.TEXT_DIM};")
            self.box_diag_division.addWidget(empty)

        self._clear(self.box_diag_possession)
        for b in core.possession_stats(details, self._ouid):
            self.box_diag_possession.addWidget(self._wr_bar_row(
                f"{b.label} ({b.span}%)", b.win, b.draw, b.lose,
                b.avg_gf, b.avg_ga))
        self._render_discipline(details)

    def _render_discipline(self, details: list[dict]) -> None:
        """규율·불운 — 나/상대 경기당 평균, 골대·퇴장이 있던 경기의 승률, 종료 유형별 경기 수."""
        box = self.box_diag_discipline
        self._clear(box)
        ds = core.discipline_stats(details, self._ouid)
        if not ds.games:
            empty = QLabel("경기가 없습니다.")
            empty.setStyleSheet(f"color: {T.TEXT_DIM};")
            box.addWidget(empty)
            return
        for ax in ds.axes:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(ax.name)
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            a.setFixedWidth(90)
            if ax.name == "퇴장":
                # 경기당 0.001 수준이라 평균은 "0.00" 으로만 보인다(실DB 1만 경기 14회) — 횟수로
                mine, opp = f"{round(ax.mine * ds.games)}회", f"{round(ax.opp * ds.games)}회"
            else:
                mine, opp = f"{ax.mine:.2f}", f"{ax.opp:.2f}"
            b = QLabel(f"나 {mine}")
            b.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            c = QLabel(f"상대 {opp}")
            c.setStyleSheet(f"color: {T.TEXT_DIM};")
            h.addWidget(a)
            h.addWidget(b)
            h.addSpacing(16)
            h.addWidget(c)
            h.addStretch(1)
            box.addWidget(row)

        sep = QLabel("이런 경기의 승률")
        sep.setStyleSheet(f"color: {T.GREEN}; font-weight: bold; padding-top: 3px;")
        box.addWidget(sep)
        for label, wdl in (("골대 맞힌 경기", ds.post_hit), ("내가 퇴장당한 경기", ds.my_red),
                           ("상대가 퇴장당한 경기", ds.opp_red)):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(f"{label} ({sum(wdl)})")
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            a.setFixedWidth(170)
            h.addWidget(a)
            if sum(wdl):
                h.addWidget(win_rate_bar(*wdl, f"{rate_of(*wdl):.1f}%  ({wdl_text(*wdl)})",
                                         core.MIN_COND, height=16), 1)
            else:
                none = QLabel("경기 없음")
                none.setStyleSheet(f"color: {T.TEXT_DIM};")
                h.addWidget(none, 1)
            box.addWidget(row)

        ends = QLabel("종료 유형: " + " · ".join(f"{k} {ds.end_kinds.get(k, 0)}" for k in core.END_KINDS))
        ends.setStyleSheet(f"color: {T.TEXT_DIM}; padding-top: 3px;")
        ends.setToolTip(FORFEIT_TIP)
        box.addWidget(ends)

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
        for result, text, color in ((core.SHOT_GOAL, "● 골", T.GREEN),
                                    (core.SHOT_ON_TARGET, "● 유효슛", T.YELLOW),
                                    (core.SHOT_OFF_TARGET, "● 빗나감", T.TEXT_DIM)):
            chk = QCheckBox(text)
            chk.setChecked(True)
            chk.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            chk.toggled.connect(self._render_shotmap)
            self.chk_shotmap_result[result] = chk
            ctrl.addWidget(chk)
        ctrl.addSpacing(16)
        # 1.4.1 N6 — 골에 어시 위치 → 슛 위치 선. 넥슨이 준 골의 90%에 있다(R11)
        self.chk_shotmap_assist = QCheckBox("─ 어시스트")
        self.chk_shotmap_assist.setToolTip("골이 된 슛에 어시스트한 위치에서 슛 위치까지 선을 긋습니다.\n"
                                           "삼각형은 하프라인 뒤에서 온 어시스트(아래 경계에 붙여 그림).")
        self.chk_shotmap_assist.setStyleSheet(f"QCheckBox {{ color: {T.PITCH_ASSIST}; font-weight: bold; }}")
        self.chk_shotmap_assist.toggled.connect(self._render_shotmap)
        ctrl.addWidget(self.chk_shotmap_assist)
        ctrl.addStretch(1)
        v.addLayout(ctrl)
        # 요약은 따로 한 줄 — [어시스트]가 들어오며 한 줄이 창 최소 폭(1280)을 넘었다
        self.lb_shotmap_summary = QLabel("")
        self.lb_shotmap_summary.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        self.lb_shotmap_summary.setWordWrap(True)
        v.addWidget(self.lb_shotmap_summary)

        # 좌: 슛 좌표 산점도 / 우: 같은 슛을 유형·거리로 쪼갠 효율.
        # 한 화면에 둬야 "먼 거리 효율이 낮다"와 "그 점들이 여기 몰려 있다"가
        # 같이 읽힌다. 위쪽 콤보(내 슛/상대 슛)가 양쪽에 동시에 걸린다.
        body = QHBoxLayout()
        self.shotmap = ShotMapWidget()
        body.addWidget(self.shotmap, 3)

        side = QScrollArea()
        side.setWidgetResizable(True)
        side.setFrameShape(QScrollArea.Shape.NoFrame)
        side.setMinimumWidth(300)
        host = QWidget()
        sv = QVBoxLayout(host)
        sv.setSpacing(8)

        gb_type = QGroupBox("슛 유형별 득점률")
        self.box_shot_type = QVBoxLayout(gb_type)
        sv.addWidget(gb_type)

        gb_dist = QGroupBox("거리별 득점률")
        self.box_shot_dist = QVBoxLayout(gb_dist)
        sv.addWidget(gb_dist)

        note = QLabel(
            f"분모는 골이 아니라 <b>슛</b>이다 — \"그 방식으로 찼을 때 들어가는 "
            f"비율\".<br>슛 {core.MIN_BUCKET_SHOTS}개 미만인 칸은 비율을 내지 "
            f"않는다({NA}).<br>괄호는 유효슛률 · xG는 비공식 근사치.")
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 13px;")
        note.setWordWrap(True)
        sv.addWidget(note)
        sv.addStretch(1)
        side.setWidget(host)
        body.addWidget(side, 2)
        v.addLayout(body, 1)
        return w

    def _render_shotmap(self) -> None:
        _, details = self._slice()
        mine = bool(self.cb_shotmap_side.currentData())
        sm = core.shot_map(details, self._ouid, mine=mine)
        self._render_shot_buckets(sm, mine)
        # 체크된 결과 종류만 화면에 찍는다(요약 수치는 전체 기준 유지).
        shown = {r for r, chk in self.chk_shotmap_result.items() if chk.isChecked()}
        shots = [s for s in sm.shots if s.result in shown]
        # 상대 슛 좌표는 상대 공격 기준이라 좌우(y)가 내 시점과 뒤집혀 있다 —
        # 같은 골문(위)에 그리되 y 를 뒤집어 내 시점으로 통일한다(x=골문은 동일).
        if not mine:
            shots = [replace(s, y=1.0 - s.y, assist_y=None if s.assist_y is None else 1.0 - s.assist_y)
                     for s in shots]
        assists = self.chk_shotmap_assist.isChecked()
        self.shotmap.set_shots(shots, assists=assists)
        who = "내" if mine else "상대"
        text = (f"{who} 슛 {sm.total} · 골 {sm.goals} · "
                f"유효슛 {sm.effective} ({sm.effective_rate:.0f}%) · "
                f"전환율 {sm.conversion:.0f}% · 기대골(xG) {sm.xg:.1f}")
        if assists:
            text += f" · 어시 있는 골 {sum(1 for s in sm.shots if s.result == core.SHOT_GOAL and s.assist_x is not None)}"
        self.lb_shotmap_summary.setText(text)

    def _render_shot_buckets(self, sm, mine: bool) -> None:
        """슛 유형·거리별 효율 패널. 내 슛이면 초록, 상대 슛(=내 실점)이면 빨강."""
        color = T.GREEN if mine else T.RED
        for box, buckets in (
                (self.box_shot_type, core.shot_type_breakdown(sm)),
                (self.box_shot_dist, core.shot_distance_breakdown(sm))):
            self._clear(box)
            if not buckets:
                lb = QLabel("표시할 슛이 없습니다.")
                lb.setStyleSheet(f"color: {T.TEXT_DIM};")
                box.addWidget(lb)
                continue
            for b in buckets:
                extra = (f"유효 {b.effective_rate:.0f}% · xG {b.xg:.1f}"
                         if b.enough else "")
                box.addWidget(RatioBarRow(b.label, b.goals, b.shots, color,
                                          enough=b.enough, extra=extra))

    # ── 거래 기록 · 내 계정(1.4.1 — 11단계는 띠와 받기 상태, 표는 12단계) ─────────────────────
    def _build_ledger_tab(self) -> QWidget:
        """이적시장 가계부 — 넥슨은 API 키 주인 계정의 거래만 주므로(docs/DONE.md 1.4.1 R1·R2) 위에 '내 계정' 띠(모달 아님)."""
        w = QWidget()
        v = QVBoxLayout(w)
        band = Card()
        row = QHBoxLayout()
        self.lb_trade_banner = QLabel("-")
        self.lb_trade_banner.setWordWrap(True)
        row.addWidget(self.lb_trade_banner, 1)
        self.btn_trade_mine = QPushButton("내 계정으로")
        self.btn_trade_yes = QPushButton("맞음")
        self.btn_trade_change = QPushButton("바꾸기")
        self.btn_trade_mine.clicked.connect(self._on_trade_mine)
        self.btn_trade_yes.clicked.connect(self._on_trade_yes)
        self.btn_trade_change.clicked.connect(self._on_trade_change)
        for b in (self.btn_trade_mine, self.btn_trade_yes, self.btn_trade_change):
            b.setStyleSheet(T.OUTLINE_BUTTON_QSS)
            row.addWidget(b)
        band.body.addLayout(row)
        self.lb_trade_hint = QLabel("")
        self.lb_trade_hint.setWordWrap(True)
        self.lb_trade_hint.setStyleSheet(f"color: {T.TEXT_DIM};")
        band.body.addWidget(self.lb_trade_hint)
        v.addWidget(band)
        self.lb_trade_status = QLabel("")
        self.lb_trade_status.setWordWrap(True)
        v.addWidget(self.lb_trade_status)
        # 가계부 본문 — 내 계정으로 확정되고 지금 키의 거래일 때만 보인다
        self.box_ledger = QWidget()
        lv = QVBoxLayout(self.box_ledger)
        lv.setContentsMargins(0, 0, 0, 0)
        self.lb_ledger_note = QLabel("")
        self.lb_ledger_note.setWordWrap(True)
        self.lb_ledger_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        lv.addWidget(self.lb_ledger_note)
        sums = Card()
        grid = QGridLayout()
        self.lb_ledger = {}
        for r, (key, name) in enumerate(self.LEDGER_ROWS):
            a = QLabel(name)
            a.setStyleSheet(f"color: {T.TEXT_DIM};")
            b = QLabel("-")
            b.setWordWrap(True)
            b.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
            grid.addWidget(a, r, 0)
            grid.addWidget(b, r, 1)
            self.lb_ledger[key] = b
        grid.setColumnStretch(1, 1)
        sums.body.addLayout(grid)
        lv.addWidget(sums)
        self.tbl_ledger = self._make_table(self.LEDGER_COLUMNS)
        lv.addWidget(self.tbl_ledger, 1)
        v.addWidget(self.box_ledger, 1)
        return w

    LEDGER_ROWS = [
        ("spent", "지출"), ("income", "수입"), ("net", "순지출"),
        ("realized", "실현 손익(판 카드)"), ("no_cost", "취득가 없음 판매"),
        ("held", "평가 손익 · 보유 중"), ("recent", "최근 구매 · 아직 안 씀"),
        ("never", "미출전 · 보유 여부 모름"), ("not_recent", "최근 경기에 안 씀 · 보유 여부 모름"),
    ]
    LEDGER_COLUMNS = ["선수", "상태", "산 날", "산 강화", "산 값", "지금 강화", "오늘 시세", "평가 손익"]
    TIMELINE_COLUMNS = ["날짜", "선수", "사건", "강화", "금액",
                        f"앞 {config.TIMELINE_WINDOW}경기", f"뒤 {config.TIMELINE_WINDOW}경기"]
    TIMELINE_KINDS = {"buy": "구매", "sell": "판매", "first": "첫 출전", "last": "마지막 출전", "grade": "강화 변화"}

    def _trade_mode(self, st: dict) -> str:
        """거래 화면의 상태 — checking(키 바뀜, 로더 전) · other(내 계정이 다른 계정) · unconfirmed · unset · mine."""
        if st.get("key_fp") != store.key_fingerprint(config.API_KEY):
            return "checking"
        mine = st.get("my_ouid")
        if mine and mine != self._ouid:
            return "other"
        if not mine:
            return "unconfirmed" if st.get("my_ouid_unconfirmed") else "unset"
        return "mine"

    def _invalidate_trades(self) -> None:
        """거래를 쓰는 화면 전부 — 가계부·타임라인."""
        self._invalidate("trades")
        self._invalidate("timeline")

    @staticmethod
    def _bp(v: int | None, sign: bool = False) -> str:
        """큰 금액 — 단위 표는 ranker.format_team_value 하나(억·조)를 쓴다."""
        if v is None:
            return "-"
        head = "-" if v < 0 else ("+" if sign and v > 0 else "")
        return f"{head}{ranker.format_team_value(abs(v))} BP"

    def _timeline_for_screen(self):
        """(Timeline, 거래 상태) — 타임라인·가계부가 같이 쓴다. 거래는 내 계정으로 확정된 때만 붙인다(남의 경기에 내 거래 금지).
        1만 경기 계산이 약 0.1초라 화면 스레드에서 하되, 같은 입력이면 다시 안 한다."""
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                st = store.trade_state(conn)
                mode = self._trade_mode(st)
                extra = None
                if mode == "mine":
                    extra = (st.get("key_fp"), store.trade_count(conn, "buy"), store.trade_count(conn, "sell"),
                             store.trade_latest(conn))
                key = (id(self._details_all), len(self._details_all), self._ouid, mode, extra,
                       datetime.now().date())
                if self._timeline_cache is not None and self._timeline_cache[0] == key:
                    return self._timeline_cache[1], st
                rows = store.load_trades(conn) if mode == "mine" else None
            finally:
                conn.close()
        except sqlite3.Error:
            return None, None
        trades = core.parse_trades(rows) if rows is not None else None
        tl = core.build_timeline(self._details_all, self._ouid, trades)
        self._timeline_cache = (key, tl)
        return tl, st

    # ── 스쿼드 타임라인 ──
    def _build_timeline_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.lb_timeline_note = QLabel("")
        self.lb_timeline_note.setWordWrap(True)
        self.lb_timeline_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_timeline_note)
        self.tbl_timeline = self._make_table(self.TIMELINE_COLUMNS)
        v.addWidget(self.tbl_timeline, 1)
        return w

    @staticmethod
    def _window_cell(w) -> tuple[str, float]:
        if not w.games:
            return "-", -1.0
        text = f"{w.win_rate:.0f}% · 득실 {w.gf - w.ga:+d}"
        if w.weak:
            text += f" · 표본 {w.games}"
        return text, w.win_rate

    def _render_timeline(self) -> None:
        tl, _st = self._timeline_for_screen()
        win = config.TIMELINE_WINDOW
        if tl is None:
            self.lb_timeline_note.setText("거래 기록을 읽지 못했습니다 — 다른 프로그램이 데이터 파일을 쓰는 중일 수 있습니다.")
            self._fill(self.tbl_timeline, [])
            return
        notes = [f"출전은 선발만 · 사건 앞뒤 {win}경기 성적은 그 기간의 성적이지 원인이 아닙니다"]
        if tl.has_trades:
            notes.append("거래는 넥슨 반영 기준 · '추정' = 산 기록 없이 들어옴(팩·보상·2022년 1월 27일 전 구매)")
        else:
            notes.append("거래 기록은 내 계정에서만 붙습니다 — 지금은 출전 기록만(첫 출전은 전부 '추정')")
        events = tl.events[:config.TIMELINE_MAX_ROWS]
        if len(tl.events) > len(events):
            notes.append(f"최근 {len(events):,}건만 (전체 {len(tl.events):,}건)")
        self.lb_timeline_note.setText(" · ".join(notes))
        rows = []
        for e in events:
            kind = self.TIMELINE_KINDS.get(e.kind, e.kind)
            if e.estimated:
                kind += " (추정)"
            if e.grade_from is not None:
                grade = (f"{e.grade_from} → {e.grade_to}", e.grade_to)
            else:
                grade = (str(e.grade_to) if e.grade_to is not None else "-", e.grade_to or 0)
            rows.append([(e.date.strftime("%Y-%m-%d %H:%M"), e.date.timestamp()),
                         self._names.get(e.spid, str(e.spid)), kind, grade,
                         (self._bp(e.value), e.value or 0), self._window_cell(e.before), self._window_cell(e.after)])
        self._fill(self.tbl_timeline, rows, enable_sort=False)
        min_n = config.TIMELINE_MIN_GAMES
        for r, e in enumerate(events):
            for c, w in ((5, e.before), (6, e.after)):
                item = self.tbl_timeline.item(r, c)
                if item is None or not w.games:
                    continue
                item.setToolTip(f"{w.games}경기 {w.win}승 {w.draw}무 {w.lose}패 · 득점 {w.gf} 실점 {w.ga}"
                                + (f"\n{sample_note(w.games, min_n)}" if w.weak else ""))
                if w.weak:  # 표본 흐림 규칙(1.2.1) — 색 + "표본 N"
                    item.setForeground(QColor(T.TEXT_DIM))
                    item.setData(Qt.ItemDataRole.UserRole + 1, True)
        self.tbl_timeline.setSortingEnabled(True)

    # ── 가계부 ──
    @staticmethod
    def _valued_text(v, label_cost: str = "산 값") -> str:
        """평가 묶음 한 줄 — 일부만 시세가 있으면 '보유 N장 중 M장 평가'(부분합을 전체처럼 보이지 않게)."""
        if not v.count:
            return "없음"
        head = f"{v.count}장 · {label_cost} {MainWindow._bp(v.cost)}"
        if v.gain is None:
            return head + " · 시세 없음"
        part = f" · {v.count}장 중 {v.priced}장 평가" if v.priced < v.count else ""
        return f"{head}{part} · 평가 {MainWindow._bp(v.gain, sign=True)}"

    def _render_ledger(self, st: dict) -> None:
        tl, st2 = self._timeline_for_screen()
        if tl is None or not tl.has_trades:
            self.box_ledger.setVisible(False)
            return
        self.box_ledger.setVisible(True)
        complete = tradecollect.is_complete(st2 or st)
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                prices = store.load_card_prices(conn, [h.trade.spid for h in tl.holdings])
            finally:
                conn.close()
        except sqlite3.Error:
            prices = {}
        selected = self.cb_season.currentData()
        scope = None if selected is None else (lambda d: self._in_selected_season(d, selected))
        lg = core.ledger(tl, prices, in_scope=scope, complete=complete)
        self.lb_ledger_note.setText(
            f"이적시장 기준(강화 비용 제외) · 지출·수입·실현 손익은 {self._scope_text()} · "
            "짝은 같은 카드를 먼저 산 것부터 맞춘 추정 · 평가는 홈페이지 오늘 시세라 판매 금액과 합치지 않습니다 · "
            f"보유는 최근 {tl.games_basis}경기 출전 기준")
        L = self.lb_ledger
        L["spent"].setText(f"{self._bp(lg.spent)} (구매 {lg.spent_n:,}건)")
        L["income"].setText(f"{self._bp(lg.income)} (판매 {lg.income_n:,}건)")
        L["net"].setText(self._bp(lg.net, sign=True))
        if lg.realized is None:
            L["realized"].setText("거래 기록을 받는 중 — 옛 거래를 다 받으면 나옵니다")
            L["no_cost"].setText("거래 기록을 받는 중")
        else:
            L["realized"].setText(f"{self._bp(lg.realized, sign=True)} (짝 맞은 판매 {lg.realized_n:,}건)")
            L["no_cost"].setText(f"{self._bp(lg.no_cost)} ({lg.no_cost_n:,}건 · 팩·보상·기록 전 구매)")
        running = self._price_loader is not None and self._price_loader.isRunning()
        held = self._valued_text(lg.held)
        if lg.held.count and lg.held.priced < lg.held.count:
            if not config.WEB_DATA:
                held += " — 시세 없음(홈페이지 데이터 꺼짐)"
            elif running:
                held += " — 시세 읽는 중…"
        L["held"].setText(held)
        L["recent"].setText(self._valued_text(lg.recent) + " (합계에 안 넣음)")
        for key, status in (("never", core.NEVER_PLAYED), ("not_recent", core.NOT_RECENT)):
            n, cost = lg.unknown.get(status, (0, 0))
            L[key].setText(f"{n:,}장 · 산 값 {self._bp(cost)} (평가 안 함)" if n else "없음")
        rows = []
        for group, status in ((lg.held, "보유 중"), (lg.recent, "최근 구매")):
            for h, price in group.rows:
                t = h.trade
                now = h.cur_grade if h.cur_grade is not None else t.grade
                gain = None if price is None or t.value is None else price - t.value
                rows.append([self._names.get(t.spid, str(t.spid)), status,
                             (t.date.strftime("%Y-%m-%d"), t.date.timestamp()), (str(t.grade or "-"), t.grade or 0),
                             (self._bp(t.value), t.value or 0), (str(now or "-"), now or 0),
                             (self._bp(price), price or 0), (self._bp(gain, sign=True), gain or 0)])
        self._fill(self.tbl_ledger, rows)
        self._maybe_start_prices(tl)

    def _maybe_start_prices(self, tl) -> None:
        """가계부를 그릴 때 하루 한 번 — 보유·최근 구매 카드 시세(동의 4 + 웹 데이터 뒤). 거래 받기가 돌면 그 끝(다시 그림) 뒤로."""
        if not config.price_auto_allowed() or self._quitting:
            return
        if any(t is not None and t.isRunning() for t in (self._price_loader, self._trade_loader)):
            return
        today = datetime.now().date().isoformat()
        targets = core.price_targets(tl)
        if self._price_tried_on == today or not targets:
            return
        self._price_tried_on = today
        ld = PriceLoader(targets)
        ld.finished.connect(lambda: self._invalidate("trades"))
        self._price_loader = ld
        ld.start()

    def _trade_snapshot(self) -> dict | None:
        """거래 화면이 그릴 상태 — 작은 표 몇 줄이라 화면 스레드에서 읽는다(거래 본문은 힌트용 (카드, 강화)만)."""
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                st = store.trade_state(conn)
                ids = [o for o in (st.get("my_ouid"), st.get("my_ouid_unconfirmed")) if o]
                return {
                    "state": st,
                    "counts": {k: store.trade_count(conn, k) for k in tradecollect.TRADE_KINDS},
                    "latest": store.trade_latest(conn),
                    "names": {o: store.account_nickname(conn, o) or o[:8] for o in ids},
                    "bought": store.bought_cards(conn) if tradecollect.is_complete(st) else None,
                }
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    def _render_trades(self) -> None:
        snap = self._trade_snapshot()
        buttons = {"mine": False, "yes": False, "change": False}
        hint = status = ""
        if snap is None:
            banner = "거래 기록을 읽지 못했습니다 — 다른 프로그램이 데이터 파일을 쓰는 중일 수 있습니다."
        else:
            st, names = snap["state"], snap["names"]
            mine, unconf = st.get("my_ouid"), st.get("my_ouid_unconfirmed")
            running = self._trade_loader is not None and self._trade_loader.isRunning()
            mode = self._trade_mode(st)
            if mode == "checking":
                # 로더가 아직 새 키를 못 봤다 — 옛 주인 거래를 새 주인 것으로 읽지 않게 아무것도 안 붙인다
                banner = "API 키 확인 중 — 거래 기록을 이 키 주인 것으로 다시 맞추고 있습니다."
            elif mode == "other":
                banner = f"거래 기록은 내 계정({names.get(mine)})에서만 볼 수 있습니다."
                buttons["change"] = True
            else:
                if mode == "unconfirmed":
                    banner = f"API 키가 바뀌었습니다. 내 계정이 {names.get(unconf)} 맞나요?"
                    buttons["yes"] = buttons["change"] = True
                    hint = self._trade_hint_text(snap["bought"], unconf, again=True)
                elif mode == "unset":
                    banner = (f"거래 기록은 API 키 주인 계정 것만 나옵니다. 이 계정({self._nick})이 내 계정인가요?")
                    buttons["mine"] = True
                    hint = self._trade_hint_text(snap["bought"], self._ouid, again=False)
                else:
                    banner = f"내 계정: {names.get(mine)}"
                    buttons["change"] = True
                status = self._trade_status_text(snap, running)
        self.lb_trade_banner.setText(banner)
        self.btn_trade_mine.setVisible(buttons["mine"])
        self.btn_trade_yes.setVisible(buttons["yes"])
        self.btn_trade_change.setVisible(buttons["change"])
        self.lb_trade_hint.setText(hint)
        self.lb_trade_hint.setVisible(bool(hint))
        self.lb_trade_status.setText(status)
        self.lb_trade_status.setVisible(bool(status))
        if snap is not None:
            self._render_ledger(snap["state"])  # 내 계정이 아니면 그 안에서 숨긴다(거래를 붙이는 판정이 한 곳에)
        else:
            self.box_ledger.setVisible(False)

    def _trade_hint_text(self, bought, ouid: str, again: bool) -> str:
        """R2 점수 — (카드, 강화) 단위. 판정이 아니라 참고. 옛 거래를 다 못 받았으면 숫자를 안 낸다."""
        if bought is None:
            return ("거래 기록을 다시 받는 중 — 끝나면 힌트가 나옵니다." if again
                    else "거래 기록을 받는 중 — 끝나면 힌트가 나옵니다.")
        if ouid != self._ouid:
            return "그 계정을 검색하면 힌트가 나옵니다."
        hit, total = core.trade_hint(self._details_all, ouid, bought)
        if not total:
            return ""
        return (f"참고: 최근 {core.TRADE_HINT_GAMES}경기에 쓴 (카드, 강화) 중 이 키의 구매 기록에 있는 것 "
                f"{hit / total:.0%} ({hit}/{total}) — 판정이 아니라 참고용입니다.")

    def _trade_status_text(self, snap: dict, running: bool) -> str:
        counts, latest, st = snap["counts"], snap["latest"], snap["state"]
        if not latest:
            if running or not tradecollect.is_complete(st):
                return "거래 기록을 받는 중…"
            return "거래 기록이 없습니다 — 넥슨이 이 API 키 주인의 거래를 주지 않았습니다."
        try:
            d = datetime.fromisoformat(latest)
            last = f"{d.month}월 {d.day}일"
        except ValueError:
            last = latest[:10]
        parts = [f"구매 {counts.get('buy', 0):,} · 판매 {counts.get('sell', 0):,}건",
                 f"넥슨 반영 기준 · 마지막 거래 {last}"]
        if running:
            parts.append("받는 중…")
        elif not tradecollect.is_complete(st):
            parts.append("옛 거래를 아직 다 못 받음 — 다음 검색 때 이어 받습니다")
        r = self._trade_result
        if r is not None and not running:
            if r.quota:
                parts.append("넥슨 호출 한도에 걸려 멈춤 — 다음 검색 때 이어 받습니다")
            elif r.error:
                parts.append(f"받기 실패({r.error}) — 다음 검색 때 다시")
        return " · ".join(parts)

    def _set_my_account(self, mine: str | None) -> None:
        """띠 버튼 — 화면 스레드가 trade_state 한 줄만 쓴다(받기와 무관 — 답을 안 해도 받기는 계속)."""
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                store.set_trade_state(conn, my_ouid=mine, my_ouid_unconfirmed=None)
            finally:
                conn.close()
        except sqlite3.Error as e:
            self.statusBar().showMessage(f"내 계정을 저장하지 못했습니다: {e}", 5000)
        self._invalidate_trades()

    def _on_trade_mine(self) -> None:
        if self._ouid:
            self._set_my_account(self._ouid)

    def _on_trade_yes(self) -> None:
        snap = self._trade_snapshot()
        if snap and snap["state"].get("my_ouid_unconfirmed"):
            self._set_my_account(snap["state"]["my_ouid_unconfirmed"])

    def _on_trade_change(self) -> None:
        self._set_my_account(None)

    def _api_loader_busy(self) -> bool:
        return any(t is not None and t.isRunning() for t in (self._loader, self._compare_loader))

    def start_trades(self) -> None:
        """거래 기록 받기 — 검색·비교가 끝난 뒤 · 키를 바꾼 직후 · 거래 화면을 열 때. 키가 있으면 계정과 무관하게
        (지정 전에 받아야 힌트가 나온다). 하루 제한은 로더 안에서(키가 바뀌었으면 무관하게 받는다)."""
        if not config.API_KEY or self._quitting:
            return
        if self._trade_loader is not None and self._trade_loader.isRunning():
            self._trade_again = True  # 끝난 뒤 한 번 더 — 화면 스레드에서 wait() 하지 않는다
            return
        if self._api_loader_busy():
            return  # 그 로더가 끝나면(finished) 다시 여기로 온다 — 상세 동시 요청·키 한도를 나눠 쓰지 않게
        self._trade_again = False
        ld = TradeLoader(self._api)
        ld.wiped.connect(self._on_trades_wiped)
        ld.done.connect(self._on_trades_done)
        ld.finished.connect(self._on_trade_thread_finished)
        self._trade_loader = ld
        ld.start()

    def _yield_trades(self) -> None:
        """다른 오픈API 로더가 시작된다 — 쪽 사이에서 멈추게만(기다리지 않는다). 그 로더가 끝나면 이어 받는다."""
        if self._trade_loader is not None and self._trade_loader.isRunning():
            self._trade_loader.cancel()

    def _on_trades_wiped(self) -> None:
        self._invalidate_trades()  # 지연 그리기가 들고 있던 옛 주인 거래가 남지 않게

    def _on_trades_done(self, res) -> None:
        self._trade_result = res  # 그리기는 스레드가 끝난 뒤(finished) — 여기선 아직 isRunning 이라 "받는 중"으로 남는다

    def _on_trade_thread_finished(self) -> None:
        self._invalidate_trades()
        if self._trade_again:
            self.start_trades()

    # ── 랭커와 비교(1.4.1 N1) ─────────────────────────────────────────
    RANKER_COLUMNS = ["포지션", "선수", "출전", *[name for name, _ in core.RANKER_METRICS], "랭커 표본", "기준일"]

    def _build_ranker_compare_tab(self) -> QWidget:
        """내 카드(가장 많이 선 자리) vs 그 카드를 그 자리에 쓴 상위 랭커들의 경기당 평균 — 넥슨 오픈API ranker-stats."""
        w = QWidget()
        v = QVBoxLayout(w)
        note = QLabel("표시 구간 기준 · 칸은 <b>나 / 랭커</b>(경기당 · 성공률은 %) · 열 정렬은 차이(나 − 랭커) 순<br>"
                      "랭커 = 같은 카드를 같은 자리에 쓴 상위 랭커들의 평균(넥슨 오픈API — 기준일은 카드마다 다르다). "
                      "차이는 조작 실력이 아니라 전술·역할 차이일 수 있습니다. "
                      f"흐린 줄은 랭커 표본 {config.RANKER_MIN_MATCHES}경기 · 내 출전 {core.MIN_PLAYER_GAMES}경기 미만.")
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        note.setWordWrap(True)
        v.addWidget(note)
        self.lb_ranker_status = QLabel("")
        self.lb_ranker_status.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_ranker_status)
        self.tbl_ranker = self._make_table(self.RANKER_COLUMNS)
        self.tbl_ranker.itemDoubleClicked.connect(self._on_ranker_double_clicked)
        v.addWidget(self.tbl_ranker, 1)
        return w

    def _render_ranker_compare(self, details: list[dict]) -> None:
        today = datetime.now().date().isoformat()
        if self._ranker_day != today:  # 하루 캐시 — 날이 바뀌면 다시 받는다
            self._ranker_day, self._ranker_data, self._ranker_failed = today, {}, None
        pairs = core.ranker_targets(details, self._ouid, config.RANKER_COMPARE_MAX)
        missing = [p for p in pairs if p not in self._ranker_data]
        busy = self._ranker_loader is not None and self._ranker_loader.isRunning()
        # 받는 중이면 새로 띄우지 않는다 — 끝나면(finished) 지금 범위로 다시 그리며 남은 쌍을 묻는다
        if missing and not busy and config.API_KEY and self._ranker_failed != tuple(missing):
            self._start_ranker_loader(missing)
            busy = True
        rows_data = core.ranker_compare(details, self._ouid, self._ranker_data, config.RANKER_COMPARE_MAX)
        self.ranker_rows = rows_data  # 테스트가 계산 결과를 본다
        if self._ranker_note:
            self.lb_ranker_status.setText(self._ranker_note)
        elif busy:
            self.lb_ranker_status.setText(f"랭커 기록을 받는 중… ({len(missing)}장)")
        else:
            self.lb_ranker_status.setText("" if pairs else "표시 구간에 선발로 뛴 카드가 없습니다.")
        few_r, few_me = config.RANKER_MIN_MATCHES, core.MIN_PLAYER_GAMES
        rows = []
        for r in rows_data:
            row = [self._positions.get(r.pos, str(r.pos)), self._names.get(r.sp_id, str(r.sp_id)), (f"{r.games}", r.games)]
            for name, f in core.RANKER_METRICS:
                pct = isinstance(f, tuple)
                fmt = "{:.0f}" if pct else "{:.2f}"
                mine = r.mine.get(name, 0.0)
                if r.ranker:
                    theirs = r.ranker[name]
                    row.append((f"{fmt.format(mine)} / {fmt.format(theirs)}", mine - theirs))
                else:
                    row.append((f"{fmt.format(mine)} / {NA}", -1e9))
            row.append((f"{r.ranker_games}" if r.ranker_games else ("받는 중" if (r.sp_id, r.pos) in missing and busy
                                                                    else "기록 없음"), r.ranker_games))
            row.append(r.ranker_date or NA)
            rows.append(row)
        self._fill(self.tbl_ranker, rows, enable_sort=False)
        for i, r in enumerate(rows_data):
            weak = r.ranker_games < few_r or r.games < few_me
            name_item = self.tbl_ranker.item(i, 1)
            if name_item:
                name_item.setData(Qt.ItemDataRole.UserRole, r.sp_id)
            pos_item = self.tbl_ranker.item(i, 0)
            line = core.position_line(r.pos)
            if pos_item and line and not weak:
                pos_item.setForeground(QColor(T.POS_COLORS[line]))
            if weak:  # 표본 흐림 규칙(1.2.1) — 색 + 툴팁
                tip = (f"랭커 표본 {r.ranker_games}경기(기준 {few_r})" if r.ranker_games < few_r
                       else f"내 출전 {r.games}경기(기준 {few_me})") + " — 차이가 크게 흔들립니다."
                for c in range(self.tbl_ranker.columnCount()):
                    item = self.tbl_ranker.item(i, c)
                    if item:
                        item.setForeground(QColor(T.TEXT_DIM))
                        item.setToolTip(tip)
                        item.setData(Qt.ItemDataRole.UserRole + 1, True)
        self.tbl_ranker.setSortingEnabled(True)

    def _start_ranker_loader(self, pairs: list) -> None:
        self._ranker_note = ""
        ld = RankerStatsLoader(self._api, pairs, config.DEFAULT_MATCH_TYPE)
        ld.done.connect(self._on_ranker_done)
        ld.failed.connect(lambda msg, key=tuple(pairs): self._on_ranker_failed(key, msg))
        ld.finished.connect(self._on_ranker_finished)
        self._ranker_loader = ld
        ld.start()

    def _on_ranker_done(self, res: dict) -> None:
        self._ranker_data.update(res)

    def _on_ranker_failed(self, key: tuple, msg: str) -> None:
        self._ranker_failed = key  # 같은 쌍을 같은 화면에서 되풀이해 묻지 않는다 — 다음 검색·다음 날 다시
        self._ranker_note = f"랭커 기록을 받지 못했습니다 — {msg}"

    def _on_ranker_finished(self) -> None:
        # finished 에서 — done 은 스레드 안에서 나와 그 순간 isRunning 이 참이다(TradeLoader 와 같은 이유)
        self._invalidate("rankercmp")

    def _on_ranker_double_clicked(self, item) -> None:
        name_item = self.tbl_ranker.item(item.row(), 1)
        sp_id = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if isinstance(sp_id, int):
            self._show_player_info(sp_id)

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
        players = core.finishing_ranking(
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
        self._invalidate("dashboard")  # 대시보드 승률 흐름도 같은 '최근 N일'

    @staticmethod
    def _make_table(columns: list[str]) -> FitTableWidget:
        """모든 표 — 열은 글자 폭 기준으로 잡고 남는 폭은 비율대로 나누며, 창이
        좁으면 글꼴을 줄여 맞춘다(FitTableWidget). 예전엔 첫 열만 내용 맞춤이고
        나머지는 균등 분할(Stretch)이라, 둘째 열 이후가 길면 "…" 로 잘렸다
        (선수 조합 '선수 B' 191 < 317px, 시즌별 '기간', 1280 폭에서 경기 목록
        '스코어'(승부차기)·팀컬러 이름). 폭은 _fill 이 채운 뒤 다시 잰다."""
        t = FitTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.setItemDelegate(RowBorderDelegate(t))
        hdr = t.horizontalHeader()
        hdr.setMinimumSectionSize(0)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        t.set_base_font_px(T.BASE_FONT_PX, T.BASE_FONT_PX)
        return t

    def _build_players_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.tbl_players = self._make_table(self.PLAYER_COLUMNS)
        # 19개 열이라 전역 폰트(15px)로는 스크롤 없이 한눈에 안 들어온다.
        # 이 표만 폰트·여백을 줄인 기본 크기(14/13px)에서 시작한다 — 창이
        # 좁아지면 FitTableWidget._fit() 이 이보다 더 줄여가며 맞춘다.
        # QSS 에 font-size 를 박아두면 그려질 때 그 값이 항상 이기고
        # setFont() 로 준 크기는 QFontMetrics 측정에만 쓰여서, 축소해도
        # 화면엔 그대로 큰 글씨가 남아 잘림이 재발한다 — 그래서 폰트 크기는
        # QSS 가 아니라 setFont() 하나로만 관리한다.
        # 좌우 padding 은 13 → 7px. 왼쪽 메뉴(230px)가 생겨 표 폭이 줄었는데,
        # 19열 × 좌우 26px 이 여백으로만 나가 글자가 11px 까지 줄었다.
        self.tbl_players.setStyleSheet(
            f"QTableWidget::item {{ padding: 12px 7px; margin: 0px; }}"
            f"QHeaderView::section {{ padding: 8px 6px; }}")
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
        self.tbl_players.setItemDelegateForColumn(2, GradeBadgeDelegate(self.tbl_players))
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
                f"QPushButton:checked {{ background: {color}; color: {T.ON_ACCENT};"
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
        self.lb_opponent_note = QLabel(self._opponent_note_text())
        self.lb_opponent_note.setStyleSheet(f"color: {T.TEXT_DIM};")
        row.addWidget(self.lb_opponent_note)
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
        _, details = self._teamcolor_scope()
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
        self._yield_trades()
        self._compare_loader = MatchLoader(self._api, nick, config.DEFAULT_MATCH_TYPE)
        self._compare_loader.finished.connect(self.start_trades)  # 끝나면 거래 받기를 잇는다
        self._compare_loader.finished_ok.connect(self._on_compare_loaded)
        self._compare_loader.failed.connect(self._on_compare_failed)
        self._compare_loader.key_invalid.connect(self._on_compare_key_invalid)
        self._compare_loader.quota_hit.connect(self._on_compare_key_invalid)  # 키를 바꾸는 게 답이라 같은 길
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

    def _on_compare_key_invalid(self, msg: str) -> None:
        self._on_compare_failed(msg)
        self._ask_new_key(msg)

    def _on_key_invalid(self, msg: str) -> None:
        self._on_failed(msg)
        self._ask_new_key(msg)

    def _ask_new_key(self, reason: str) -> None:
        """넥슨이 키를 거절했다(만료·폐기 등) — 앱을 끄지 않고 새 키를 받는다."""
        dlg = ApiKeyDialog(self, reason=reason)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._api.set_key(config.API_KEY)
            self.statusBar().showMessage("API 키를 바꿨습니다. 다시 검색하세요.")
            # 거래는 키 주인 것 — 새 키로 바로 다시 맞춘다("API 키 확인 중"이 다음 검색까지 안 풀리지 않게)
            self._yield_trades()
            self.start_trades()
            self._invalidate_trades()

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
        self.tbl_compare.refit()  # _fill 을 안 거치는 표

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
        found = core.own_squad(details, ouid)
        if found is None:
            lb = QLabel("표시할 스쿼드가 없습니다.")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            box.addWidget(lb)
            return
        players, match_date, result = found
        formation = core.formation_of(players)
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
            season_id = core.season_id_of(sp_id)
            info = self._seasons.get(season_id)
            if info and info.get("seasonImg"):
                season_entries.append((sp_id, season_id, info["seasonImg"]))
        season_loader = SeasonIconLoader(season_entries, self._season_icon_dir)
        season_loader.loaded.connect(pitch.set_season_icon)
        season_loader.start()
        self._compare_squad_loaders.append(season_loader)

    @staticmethod
    def _tactics_scroll() -> tuple[QScrollArea, QVBoxLayout]:
        """전술·경기 결과 탭 셋의 틀 — 채우기는 _render_tactics 하나가 셋을 같이 한다(키 tactics)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)
        scroll.setWidget(w)
        return scroll, v

    def _build_tactics_tab(self) -> QWidget:
        # 그룹박스·행 사이 여백을 기본값보다 눌러뒀다 — 값이 없어서가 아니라 순전히 세로 공간 절약용.
        scroll, v = self._tactics_scroll()

        gb_f = QGroupBox("전술 분석")
        vf = QVBoxLayout(gb_f)
        vf.setSpacing(5)

        self.lb_my_formation = QLabel("-")
        mf = QFont()
        mf.setPointSize(14)
        mf.setBold(True)
        self.lb_my_formation.setFont(mf)
        self.lb_my_formation.setStyleSheet(
            f"background: {T.GREEN_SOFT}; border: 1px solid {T.BORDER};"
            f" border-radius: 6px; padding: 8px;")
        vf.addWidget(self.lb_my_formation)

        self.box_opp = QVBoxLayout()
        self.box_opp.setSpacing(2)
        vf.addLayout(self.box_opp)
        v.addWidget(gb_f)
        v.addStretch(1)
        return scroll

    def _build_results_tab(self) -> QWidget:
        scroll, v = self._tactics_scroll()
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
        return scroll

    def _build_pass_style_tab(self) -> QWidget:
        scroll, v = self._tactics_scroll()
        # 1.4.1 N5 — 경기 상세의 패스 종류 6가지(R10). 표(FitTableWidget)는 스크롤 안에서 높이가 안 잡혀 글자 칸으로
        gb_p = QGroupBox("패스 스타일")
        pv = QVBoxLayout(gb_p)
        self.grid_pass = QGridLayout()
        self.grid_pass.setHorizontalSpacing(18)
        self.grid_pass.setVerticalSpacing(3)
        pv.addLayout(self.grid_pass)
        self.lb_pass_note = QLabel("")
        self.lb_pass_note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 13px;")
        self.lb_pass_note.setWordWrap(True)
        pv.addWidget(self.lb_pass_note)
        v.addWidget(gb_p)
        v.addStretch(1)
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

    def _api_search(self, nick: str, quiet: bool = False) -> None:
        """quiet: 켤 때 자동으로 하는 새 경기 확인 — 실패해도 경고 창 없이 상태줄에만(화면은 DB 로 이미 떠 있다).
        키가 거절된 경우만은 그래도 묻는다(사용자가 해야 할 일이 있다)."""
        if self._loader and self._loader.isRunning():
            return
        self._nick = nick
        self._quiet_search = quiet
        self._set_busy(True)
        # 지금 화면의 계정을 넘긴다 — 같은 계정이면(로더가 ouid 로 판단) 새 경기만 DB 에서 읽는다
        prev = (self._ouid, self._matches_all, self._details_all) if self._ouid else None
        # 미리 읽은 것은 한 번만 넘긴다 — 다른 계정이면 로더가 멈추고 버린다
        prefetch, self._prefetch = self._prefetch, None
        self._yield_trades()  # 거래 받기는 쪽 사이에서 멈추고, 이 검색이 끝나면(finished) 이어 받는다
        self._loader = MatchLoader(self._api, nick, config.DEFAULT_MATCH_TYPE, prev=prev,
                                   prefetch=prefetch, record_elo=True, want_max_division=True)
        self._loader.finished.connect(self.start_trades)
        self._loader.progress.connect(self._on_progress)
        self._loader.finished_ok.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.key_invalid.connect(self._on_key_invalid)
        self._loader.quota_hit.connect(self._on_quota_hit)  # 키를 바꾸는 게 답이라 같은 길
        self._loader.rank_ready.connect(self._on_rank_ready)
        self._loader.max_division_ready.connect(self._on_max_division)
        self._loader.elo_saved.connect(self._load_elo)
        self._loader.start()

    def _on_rank_ready(self, ouid: str, rank) -> None:
        """랭킹이 첫 화면보다 늦게 왔다 — 랭커 카드만. 그사이 다른 계정을 열었으면 버린다."""
        if ouid != self._ouid:
            return
        self._rank = rank
        self._render_ranker()

    def _on_max_division(self, ouid: str, info) -> None:
        """역대 최고 등급. None(실패)이면 보던 값을 둔다(순위와 같은 규칙), {} 면 기록 없음.

        ouid 별로 들고 카드는 지금 계정 것만 읽는다 — 그래서 다른 계정의 늦은 신호는 따로 거를 필요가 없다
        (거르는 줄을 둬 봤는데 빼도 결과가 같았다, 7단계 변이). 내려놓기(release_memory)도 안 비운다 —
        다시 열기는 저장본 경로라 maxdivision 을 다시 부르지 않는다."""
        if info is not None:
            self._max_division[ouid] = info
        self._render_ranker()
        self._render_sc_records()  # 이 신호는 finished_ok 뒤에 온다 — 승률 그래프의 넥슨 줄도 다시

    def _on_quota_hit(self, msg: str) -> None:
        if getattr(self, "_quiet_search", False):
            self._on_failed(msg)  # 켤 때 자동 확인 — 키 창을 갑자기 띄우지 않는다(다음 직접 검색 때 묻는다)
        else:
            self._on_key_invalid(msg)

    def _last_account(self) -> tuple[str, str] | None:
        """마지막으로 검색한 계정 (ouid, 닉네임) — 저장된 감독모드 경기가 없거나 DB 를 못 열면 None."""
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                last = store.recent_searches(conn, 1)
                if not last or not store.match_count(conn, last[0]["ouid"], config.DEFAULT_MATCH_TYPE):
                    return None
            finally:
                conn.close()
        except Exception:
            return None
        return last[0]["ouid"], last[0]["nickname"]

    def open_last_account(self) -> bool:
        """켤 때 — 마지막으로 본 계정을 DB 에서 바로 그리고, 끝나면 새 경기를 조용히 확인한다.
        main 에서만 부른다. 열 계정이 없으면 False(검색 화면 그대로)."""
        last = self._last_account()
        if not last:
            return False
        ouid, nick = last
        self._nick = nick
        self._set_busy(True)
        self._loader = MatchLoader(self._api, nick, config.DEFAULT_MATCH_TYPE, offline_ouid=ouid)
        self._loader.progress.connect(self._on_progress)
        self._loader.finished_ok.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        # finished_ok 가 아니라 스레드가 끝난 뒤(finished) — 그 전엔 isRunning 이라 새 검색이 막힌다
        self._loader.finished.connect(lambda o=ouid, n=nick: self._after_offline_open(o, n))
        self._loader.start()
        return True

    def _after_offline_open(self, ouid: str, nick: str) -> None:
        if self._ouid == ouid:  # DB 로 그리는 데 성공했다 — 이제 넥슨에 새 경기를 묻는다
            self.statusBar().showMessage(f"{nick} — 저장된 기록을 보여 주는 중 · 새 경기 확인 중…")
            self._api_search(nick, quiet=True)
        else:
            self._set_busy(False)

    def _apply_range(self) -> None:
        """시작~끝 스핀박스 값대로 표시 구간을 바꾼다."""
        self._render_all()
        self._on_fetch_team_colors()  # 범위가 넓어졌으면 새로 들어온 상대만 조회

    # ── 시즌 ──────────────────────────────────────────────────────────
    ONGOING = "ongoing"  # 콤보 항목 데이터 — 시즌표에 아직 안 올라온 진행 중 시즌

    def _load_season_cache(self) -> None:
        """캐시된 시즌표를 즉시 쓰고, 오래됐으면 백그라운드로 다시 받는다."""
        stale = True
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                self._rank_seasons = store.load_seasons(conn)
                stale = store.seasons_stale(conn)
            finally:
                conn.close()
        except Exception:
            pass  # 시즌표가 없어도 판수 기준 화면은 그대로 돈다
        if stale and not (self._season_loader and self._season_loader.isRunning()):
            self._season_loader = SeasonLoader()
            self._season_loader.loaded.connect(self._on_seasons_loaded)
            self._season_loader.start()

    def _on_seasons_loaded(self, items: list) -> None:
        self._rank_seasons = items
        self._render_elo()   # 지금 시즌 시작이 바뀐다 — 들고 있는 값으로 다시 그림(읽기 없음)
        self._load_predict(self._ouid)   # 예측은 시즌 길이·종료일 추정에 시즌표를 쓴다
        if self._matches_all:
            # 조회가 이미 끝난 뒤에 시즌표가 도착한 경우 — 콤보만 채워 준다.
            # 선택은 "전체" 그대로라 표시 중인 화면은 건드리지 않는다.
            self._rebuild_season_combo()
            self._render_seasons()

    def _season_groups(self) -> list[tuple[object, list[MatchSummary]]]:
        """누적 전체를 시즌별로 묶은 것 — 콤보와 시즌별 성적 표가 같이 쓴다."""
        return sn.group_by_season(self._rank_seasons, self._matches_all,
                                  key=lambda m: m.match_date)

    def _rebuild_season_combo(self) -> None:
        """경기가 실제로 있는 시즌만 콤보에 올린다 — 2018년까지 89개가 다
        떠 있으면 고르기만 번거롭다. 재검색이어도 고르고 있던 시즌을 유지하고,
        아직 안 고른 상태면 현재 시즌을 기본으로 잡는다.

        항목에 기간을 붙이는 건, 끝난 시즌 이름("2026 시즌 3")이 현재 시즌으로
        읽혀서 지난 시즌 기록을 보고 있는 줄 모르는 일이 실제로 있었기 때문이다."""
        prev = self.cb_season.currentData()
        last_end = max((s.end for s in self._rank_seasons), default=None)
        self.cb_season.blockSignals(True)
        self.cb_season.clear()
        self.cb_season.addItem(f"전체 ({len(self._matches_all)}경기)", None)
        for season, group in self._season_groups():
            if season is None:
                span = f"{last_end:%m-%d}~" if last_end else "최신"
                self.cb_season.addItem(
                    f"현재 시즌 (진행 중) · {span} ({len(group)}경기)", self.ONGOING)
            else:
                self.cb_season.addItem(
                    f"{season.label} · {season.start:%m-%d}~{season.end:%m-%d}"
                    f" ({len(group)}경기)", season)
        saved = self._restore.pop("season", None) if hasattr(self, "_restore") else None
        if self._season_picked:
            idx = self.cb_season.findData(prev)
        elif saved is not None:  # 지난번에 보던 시즌 — 이번 데이터에 그 시즌이 없으면 기본(현재 시즌)
            idx = next((i for i in range(self.cb_season.count())
                        if self._season_key(self.cb_season.itemData(i)) == saved), -1)
            if idx < 0:
                idx = self.cb_season.findData(self.ONGOING)
            else:
                self._season_picked = True  # 다음 재검색에도 그대로
        else:
            idx = self.cb_season.findData(self.ONGOING)  # 없으면 -1 → "전체"
        self.cb_season.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_season.blockSignals(False)
        # 가장 긴 항목이 다 들어가는 폭 밑으로는 안 눌리게 — 최소 150 이던 때
        # 창을 줄이면 "현재 시즌 (진행" 까지만 보였다.
        self.cb_season.setMinimumWidth(self.cb_season.sizeHint().width())
        self.top_bar.relayout()

    def _in_selected_season(self, when, selected) -> bool:
        if when is None:
            return False
        if selected == self.ONGOING:
            last_end = max((s.end for s in self._rank_seasons), default=None)
            return last_end is not None and when.date() >= last_end
        return selected.contains(when)

    def _scope_text(self) -> str:
        """표시 구간(시작~끝)에 안 갇히는 탭들이 실제로 쓰는 범위 이름.

        시즌을 고르면 그 탭들도 그 시즌 안에서만 계산되므로, 안내 문구를
        "누적 전체"로 박아 두면 화면에 틀린 말이 뜬다."""
        selected = self.cb_season.currentData()
        if selected is None:
            return "누적 전체"
        if selected == self.ONGOING:
            return "현재 시즌"
        return selected.label

    def _analysis_note_text(self) -> str:
        return (f"최근 흐름은 최근 {core.WINDOW}경기 · 이기는/지는 패턴은"
                f" {self._scope_text()} 기준. 표본이 모자란 항목은 표시하지 않습니다.")

    def _diag_note_text(self) -> str:
        return f"{self._scope_text()} 기준 · 승·무·패 아닌 결과(오류 등)는 제외"

    def _opponent_note_text(self) -> str:
        return f"{self._scope_text()} 기준 · 더블클릭하면 그 상대와의 최근 경기 스쿼드"

    def _refresh_scope_notes(self) -> None:
        self.lb_opponent_note.setText(self._opponent_note_text())
        self.lb_analysis_note.setText(self._analysis_note_text())
        self.lb_diag_note.setText(self._diag_note_text())
        self.lb_diag_note2.setText(self._diag_note_text())

    def _on_season_changed(self) -> None:
        self._season_picked = True
        self._apply_season()

    def _apply_season(self, render: bool = True) -> None:
        """콤보에서 고른 시즌만 남긴다.

        _matches/_details 자체를 갈아끼우므로, "누적 전체 기준"으로 그리는
        탭들(승률 그래프·기간별 추이·승부처 등)도 자연히 그 시즌 안에서만
        계산된다 — 시즌을 골랐다면 그게 기대하는 동작이다.
        """
        selected = self.cb_season.currentData()
        if selected is None:
            self._matches, self._details = self._matches_all, self._details_all
        else:
            self._matches = [m for m in self._matches_all
                             if self._in_selected_season(m.match_date, selected)]
            ids = {m.match_id for m in self._matches}
            self._details = [d for d in self._details_all
                             if d.get("matchId") in ids]

        # 시작~끝 스핀박스 — 처음엔 최근 100경기(또는 그 이하)를 기본으로 보여준다.
        total_n = len(self._matches)
        self.sp_from.blockSignals(True)
        self.sp_to.blockSignals(True)
        self.sp_from.setRange(1, max(total_n, 1))
        self.sp_to.setRange(1, max(total_n, 1))
        self.sp_from.setValue(1)
        self.sp_to.setValue(min(PAGE_SIZE, total_n) or 1)
        self.sp_from.blockSignals(False)
        self.sp_to.blockSignals(False)

        if render and self._matches_all:
            self._render_all()
            self._on_fetch_team_colors()

    def _set_busy(self, busy: bool) -> None:
        for w in (*self._search_btns, *self._nick_edits, self.ed_search):
            w.setEnabled(not busy)
        for b in (self.btn_more, self.btn_apply):
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
        if getattr(self, "_quiet_search", False) and self.stack.currentIndex() == self.PAGE_MAIN:
            first = msg.splitlines()[0] if msg else ""
            self.statusBar().showMessage(f"새 경기를 확인하지 못했습니다 — 저장된 기록을 보여 줍니다 · {first}")
            return
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
        switched = ouid != self._ouid
        if switched:
            self._trend_reset_pending = True  # 다른 계정으로 전환 — 승률 그래프 기간을 30일로 되돌린다
        self._ouid = ouid
        self._basic = basic
        if rank is not None or switched:
            # 같은 계정 재검색에서 None 이면 대개 '아직'(rank_ready 가 뒤따른다) — 카드가 비었다 차는 깜빡임 대신
            # 보던 값을 둔다. 다른 계정이면 옛 계정 순위를 남기지 않는다.
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
        self._matches_all, self._details_all = matches, details
        # 시즌 콤보를 먼저 다시 채우고(이전 선택은 그대로 있으면 유지),
        # 그 선택대로 _matches/_details 와 시작~끝 스핀박스를 맞춘다.
        self._rebuild_season_combo()
        self._apply_season(render=False)

        # 다른 계정이면 대시보드부터. 같은 계정 재확인이면 보던 메뉴를 유지한다.
        self.stack.setCurrentIndex(self.PAGE_MAIN)
        if switched:
            # 이번에 켜고 처음 그리는 계정이면 지난번에 보던 메뉴·탭으로(settings.ini), 아니면 대시보드
            self._go_page(*self._restore_view(self._restore.pop("page", None), self._restore.pop("tabs", None)))
        self._render_ranker()
        # ELO — 검색·저장본 열기·내려놓은 뒤 다시 열기가 전부 여기를 지난다(경기 0 이어도 ELO 는 있을 수 있다)
        self._load_elo(ouid)

        if not matches:
            self.statusBar().showMessage(f"{self._nick} — 감독모드 기록이 없습니다.")
            return

        self._render_all()
        self._on_fetch_team_colors()  # DB 캐시(TTL store.TEAM_COLOR_TTL_DAYS · 7일)로 채우고, 모자란 것만 백그라운드 조회
        self.statusBar().showMessage(
            f"{self._nick} — 누적 {len(matches)}경기 (감독모드 전체)"
            + (f" · 새 경기 {new}건 저장" if new else ""))

    def _restore_view(self, page, saved_tabs) -> tuple[str, str | None]:
        """저장된 보기 → (메뉴, 탭). 다른 메뉴의 보던 탭도 여기서 한 번 되살린다(신호 없이 — 그리기는 열 때).
        1.x 이름은 OLD_PAGE_NAMES 로 새 자리에 · 없는 이름·숨긴 메뉴·없는 탭은 대시보드(E5)."""
        for menu, tabs in self._page_tabs.items():
            name = (saved_tabs or {}).get(self._tab_sid[menu])
            if isinstance(name, str):
                tabs.set_current(name, emit=False)
        if not isinstance(page, str):
            return "대시보드", None
        if page in self.OLD_PAGE_NAMES:
            return self.OLD_PAGE_NAMES[page]
        if page not in self._page_index or page in config.HIDDEN_NAV_UNTIL_READY:
            return "대시보드", None
        tabs = self._page_tabs.get(page)
        return page, (tabs.current_name() if tabs is not None else None)

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

    # ── 보이는 화면만 그린다 ────────────────────────────────────────────
    # 예전엔 _render_all 이 메뉴 18개를 매번 다 그려 시즌 "전체"(1만 경기) 전환에 3.2~3.5초 동안
    # 창이 굳었다(2026-10-02 실측). 지금은 전부 '낡음'으로 표시하고 보이는 페이지만 그린 뒤,
    # 나머지는 그 메뉴를 열 때 그린다. 같은 그리기를 쓰는 메뉴는 같은 키를 쓴다(한 번 그리면 같이 깨끗).
    # 2.1.1 — 그리기 단위는 (메뉴, 탭). 키 → 그 키가 그리는 자리들. 한 키를 여러 자리가 쓰면(teamcolor · 한 함수가
    # 탭 여럿을 채우는 diagnosis·tactics) 자리를 여럿 적는다 — 그중 하나가 보이면 그린다.
    VIEW_OF_KEY = {
        "dashboard": [("대시보드", None)], "matches": [("경기 목록", None)], "opponents": [("상대 전적", None)],
        "analysis": [("흐름 분석", None)], "period": [("기간별 추이", None)], "seasons": [("시즌별 성적", None)],
        "clutch": [("승부처 분석", None)],
        "diagnosis": [("성적 진단", "상대·점유율"), ("성적 진단", "규율·불운")],
        "tactics": [("전술·경기 결과", "전술"), ("전술·경기 결과", "경기 결과"), ("전술·경기 결과", "패스 스타일")],
        "shotmap": [("슛 맵", None)],
        "players": [("선수 지표", "지표")], "rankercmp": [("선수 지표", "랭커 비교")],
        "finishing": [("선수별 결정력", None)],
        "teamcolor": [("포지션별 최다 상대", None), ("팀컬러 승률", None), ("팀컬러 랭킹", None)],
        "timeline": [("스쿼드·이적", "타임라인")], "trades": [("스쿼드·이적", "가계부")],
    }
    KEY_OF_VIEW = {view: key for key, views in VIEW_OF_KEY.items() for view in views}
    # 위 표 밖의 자리 — 이유 없이 빠진 자리는 조용히 안 그려진다(test_every_nav_page_has_a_renderer)
    VIEW_EXEMPT = {
        ("구단주 비교", None): "사용자가 [비교] 를 눌러야 그린다 — 검색 결과에 안 묶인다",
        ("승률 그래프", "승률·등급"): "_render_all 이 늘 그린다(_render_trend — 대시보드 승률 흐름이 그 결과를 쓴다)",
        ("승률 그래프", "점수·예측"): "_render_elo 가 EloLoader·PredictWorker 결과로 그린다(검색 결과에 안 묶인다)",
        ("랭킹 추이", None): "아직 빈 메뉴(config.HIDDEN_NAV_UNTIL_READY) — 17단계",
        ("랭커 픽", "픽"): "아직 빈 메뉴 — 16단계",
        ("랭커 픽", "추천"): "아직 빈 메뉴 — 17단계",
        ("선수로 구단주 찾기", None): "아직 빈 메뉴 — 16단계",
    }
    LAZY_RENDER = True  # 테스트가 "다 그려진 상태"를 볼 때만 끈다

    def _renderers(self) -> dict:
        """키 → 그리기. 범위 주석은 예전 _render_all 그대로 — 표시 구간(_slice)에 갇히는 것과
        시즌 범위(self._matches)를 쓰는 것이 메뉴마다 다르다."""
        return {
            "dashboard": self._render_dashboard,
            "matches": lambda: self._render_matches(self._slice()[0]),
            "players": lambda: self._render_players(self._slice()[1]),
            "tactics": lambda: self._render_tactics(self._slice()[1]),
            # 시즌 범위 — 표시 구간(100경기) 안에선 상대 대부분이 1번씩이라 상성이 안 드러난다.
            "opponents": lambda: self._render_opponents(self._matches),
            # 팀컬러·포지션별 최다 상대도 시즌 범위 — 100경기면 팀컬러마다 1~2경기라 승률이 안 된다
            "teamcolor": lambda: self._render_teamcolor_tabs(*self._teamcolor_scope()),
            "period": lambda: self._render_period(self._matches),
            "seasons": self._render_seasons,  # 시즌 필터도 무시(_matches_all)
            "clutch": lambda: self._render_clutch(self._details, self._matches),
            "diagnosis": lambda: self._render_diagnosis(self._details),
            "shotmap": self._render_shotmap,  # 표시 구간
            "finishing": lambda: self._render_finishing(self._slice()[1]),
            "rankercmp": lambda: self._render_ranker_compare(self._slice()[1]),  # 표시 구간 — 랭커 요청은 카드 수만큼
            "analysis": self._render_analysis,  # 패턴 규칙이 표본을 크게 잡아야 한다 — 시즌 범위
            # 시즌 필터 무관 — 거래는 키 주인 것 전체, 힌트는 누적 최근 300경기. 가계부 합계만 시즌 범위(짝은 전체로 맞춘 뒤)
            "trades": self._render_trades,
            "timeline": self._render_timeline,  # 누적 전체 — 짝·보유 상태는 전체 이력으로 맞춘다
        }

    def _render_all(self) -> None:
        matches, _ = self._slice()
        total = len(self._matches)
        self.lb_total.setText(f"전체 {total}경기")
        self.lb_profile.setText(self._nick)
        self.lb_sub.setText(f"Lv.{self._basic.get('level', '-')}  ·  {self._grade_name}  ·  "
                            f"감독모드 {len(matches)}경기 분석 (누적 {total})")
        self._refresh_scope_notes()
        self._render_ranker()
        # 승률 추이는 늘 — 대시보드 승률 흐름이 그 결과(_trend_periods)를 쓴다. 가볍다.
        # "최근 30일" 이 표시 구간에 갇히면 안 된다(하루 100경기 넘게 뛰는 계정은 하루도 안 된다).
        self._render_trend(self._matches)
        self._ranker_failed, self._ranker_note = None, ""  # 새 데이터면 실패했던 랭커 요청도 다시 해 본다
        self._dirty = set(self._renderers())
        if self.LAZY_RENDER:
            self._render_current_page()
        else:
            self._render_everything()

    def _current_page_name(self) -> str | None:
        idx = self.pages.currentIndex()
        return next((n for n, i in self._page_index.items() if i == idx), None)

    def _render_key(self, key: str | None) -> None:
        if key and key in self._dirty:
            self._dirty.discard(key)  # 먼저 지운다 — 그리다 예외가 나도 같은 화면에서 무한 반복하지 않게
            self._renderers()[key]()

    def _render_current_page(self) -> None:
        self._render_key(self.KEY_OF_VIEW.get(self._current_view()))

    def _render_everything(self) -> None:
        for key, fn in self._renderers().items():
            if key in self._dirty:
                self._dirty.discard(key)
                fn()

    def _invalidate(self, key: str) -> None:
        """그 키의 화면이 낡았다 — 그 키의 자리 중 하나가 보이고 있으면 바로, 아니면 열 때 그린다."""
        self._dirty.add(key)
        if not self.LAZY_RENDER or self._current_view() in self.VIEW_OF_KEY.get(key, ()):
            self._render_key(key)

    def _max_division_text(self) -> str | None:
        """지금 계정의 최고 티어 줄 — 기록이 없거나 등급 이름을 모르면(메타 실패) None.
        숫자("800")를 이름 대신 내지 않는다.

        넥슨 achievementDate 는 '처음'이 아니라 **가장 최근에** 그 티어로 올라선 경기의 시작 시각이고, 시즌마다
        초기화되지 않는다(2026-10-05 실측 9계정: 하루 네 번 슈챔 승급한 계정은 네 번째 경기 시각과 초 단위까지 같았고,
        2026 시즌엔 챔피언스인 계정의 값이 2025 시즌 5 날짜였다) — 그래서 '최근 달성일'이라고 쓴다."""
        info = self._max_division.get(self._ouid) if self._ouid else None
        name = self._division_names.get(info.get("division")) if info else None
        if not name:
            return None
        date = f" {info['date']}" if info.get("date") else ""
        return f"최고티어 최근 달성일{date}  [ {name} ]"

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
        c.set_best(self._max_division_text())

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
        if isinstance(table, FitTableWidget):
            table.refit()

    @staticmethod
    def _fit_columns_to_content(table: FitTableWidget,
                                extra: dict[int, int] | None = None) -> None:
        """열별 추가 여백(아이콘이 같이 나오는 열 등)을 주고 다시 맞춘다.
        폭 계산·창 폭에 안 맞을 때 글꼴 축소는 FitTableWidget 이 한다 — 여백이
        없는 표는 _fill 이 끝에서 refit() 하므로 부를 필요가 없다."""
        table.set_content_widths(extra)

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
                    item.setBackground(self._blend(T.PANEL, bg, T.ROW_TINT))
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
        found = core.opponent_squad([detail], self._ouid, opponent)
        if found is None:
            return
        players, match_date, result = found
        self._show_opponent_squad(opponent, players, match_date, result)

    @staticmethod
    def _blend(base_hex: str, target_hex: str, mix: float) -> QColor:
        return QColor(T.blend(base_hex, target_hex, mix))

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
        found = core.opponent_squad(self._details, self._ouid, nickname)  # 표와 같은 범위
        if found is None:
            QMessageBox.information(
                self, "상대 스쿼드",
                f"{self._scope_text()} 안에서 이 상대와 붙은 경기의 스쿼드를 찾지 못했습니다.")
            return
        players, match_date, result = found
        self._show_opponent_squad(nickname, players, match_date, result)

    def _season_name(self, sp_id: int) -> str:
        info = self._seasons.get(core.season_id_of(sp_id))
        return info.get("className", "-") if info else "-"

    def _position_opp_rows(self, players: list[core.PositionOpponent]) -> list[list]:
        return [[p.position, f"{p.name} ({self._season_name(p.sp_id)})",
                (str(p.count), p.count), (f"{p.rate:.1f}%", p.rate)]
               for p in players]

    @staticmethod
    def _tint_position_rows(table: QTableWidget,
                            players: list[core.PositionOpponent]) -> None:
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
        players = core.opponent_position_players(
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
        stats_list = core.team_color_stats(matches, self._team_color_of,
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
        opps = {m.opponent for m in matches if m.opponent}
        known = sum(1 for n in opps if self._team_colors.get(n))
        for lb in self._teamcolor_note_labels:
            lb.setText(self._teamcolor_note_text(len(opps), known))

    def _teamcolor_scope(self) -> tuple[list[MatchSummary], list[dict]]:
        """팀컬러 두 탭·포지션별 최다 상대가 세는 범위 — 표시 구간이 아니라 시즌 콤보.

        표시 구간(100경기)이면 상대 98명을 거의 한 번씩만 만나 팀컬러마다 1~2경기라
        승률이 안 된다(2026-10-02 실측). 시즌이면 2천 경기가 넘는다."""
        return self._matches, self._details

    def _load_cached_team_colors(self, nicknames: set[str]) -> None:
        missing = sorted(n for n in nicknames if n not in self._team_colors)
        if not missing:
            return
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

    def _on_fetch_team_colors(self) -> None:
        """검색이 끝나면 자동으로도 호출된다(_on_loaded) — DB 캐시(TTL 7일)
        에 있는 상대는 그걸로 채우고, 정말 처음 보거나 캐시가 오래된 상대만
        넥슨 데이터센터에서 새로 긁는다.

        범위는 시즌 콤보(_teamcolor_scope). 새로 읽을 상대가 랭킹 목록 페이지 수
        (ranker.RANK_PAGES)보다 많으면 상대마다 검색하는 대신 1만 위 목록을 통째로
        읽는다 — 요청이 적고(실측 시즌 1,491명 158초 vs 목록 49초), 한 번 읽으면 이 계정의
        다른 시즌 상대까지 다 채워져 시즌을 바꿔도 다시 안 읽는다. 적으면(평소 — 그날
        새로 만난 상대) 그 상대만 검색하는 쪽이 싸다."""
        if self._teamcolor_loader and self._teamcolor_loader.isRunning():
            # 이미 도는 중에 범위가 넓어져 다시 불렸다 — 끝난 뒤(_on_teamcolor_finished)
            # 새로 늘어난 상대까지 마저 조회하도록 재시도를 예약해 둔다.
            self._teamcolor_retry_pending = True
            return
        shown_matches, _ = self._teamcolor_scope()
        all_opps = {m.opponent for m in self._matches_all if m.opponent}
        self._load_cached_team_colors(all_opps)  # DB 만 — 다른 시즌으로 바꿀 때도 바로 그린다
        remaining = {m.opponent for m in shown_matches
                    if m.opponent and m.opponent not in self._team_colors}
        if not remaining or not config.WEB_DATA:
            self._invalidate("teamcolor")  # 보이면 지금, 아니면 열 때
            if remaining:  # 꺼져 있다 — 상대마다 실패를 쌓는 대신 이유를 한 번만 보인다
                for lb in self._teamcolor_status_labels:
                    lb.setText(config.WEB_DATA_OFF_MSG)
            return
        if len(remaining) > ranker.RANK_PAGES or self._rank_snapshot_fresh():
            # 목록을 읽는 김에 이 계정의 모든 시즌 상대를 같이 찾는다(요청 수는 같다).
            # 하루 안의 랭킹 스냅숏(1.1.1 수집)이 있으면 상대가 몇 명이든 거기서 — 요청 0
            wanted = sorted(n for n in all_opps if n not in self._team_colors)
            self._teamcolor_loader = RankListLoader(set(wanted))
            self._teamcolor_progress_fmt = "랭킹 목록 {done} / {total}쪽 읽는 중…"
            self._teamcolor_loader.waiting.connect(self._on_teamcolor_waiting)
        else:
            # 많이 만난 상대부터 — 값어치 큰 상대가 먼저 채워지고, 진행 중에도
            # 화면을 갱신하니(_on_teamcolor_loaded) 다 끝나기 전에도 유용해진다.
            freq = Counter(m.opponent for m in shown_matches if m.opponent)
            wanted = sorted(remaining, key=lambda n: -freq[n])
            self._teamcolor_loader = TeamColorLoader(wanted)
            self._teamcolor_progress_fmt = "상대 {done} / {total}명 조회 중…"
        for b in self._teamcolor_fetch_btns:
            b.setEnabled(False)
        self._teamcolor_pending = wanted
        self._teamcolor_loaded_count = 0
        self._teamcolor_rendered_at = time.monotonic()
        self._on_teamcolor_progress(0, self._teamcolor_loader.total)
        self._teamcolor_loader.loaded_many.connect(self._on_teamcolor_loaded)
        self._teamcolor_loader.progress.connect(self._on_teamcolor_progress)
        self._teamcolor_loader.finished_all.connect(self._on_teamcolor_finished)
        self._teamcolor_loader.start()

    # 조회 중 표 중간 갱신 간격(초). 개수로 세면 목록 읽기(한 쪽에 여러 명)에서 너무 자주
    # 다시 그린다 — 시즌 범위는 2천 경기라 한 번 그리는 값이 작지 않다.
    TEAMCOLOR_RENDER_INTERVAL_S = 2.0

    def _on_teamcolor_loaded(self, batch: dict) -> None:
        for nick, (color, value) in batch.items():
            self._team_colors[nick] = color
            self._team_values[nick] = value
        self._teamcolor_loaded_count += len(batch)
        now = time.monotonic()
        if now - self._teamcolor_rendered_at >= self.TEAMCOLOR_RENDER_INTERVAL_S:
            self._teamcolor_rendered_at = now
            self._invalidate("teamcolor")

    def _on_teamcolor_progress(self, done: int, total: int) -> None:
        text = self._teamcolor_progress_fmt.format(done=done, total=total)
        for lb in self._teamcolor_status_labels:
            lb.setText(text)
        # 팀컬러 화면에서만 보이던 진행을 상태줄에도 — 처음 목록 읽기(약 50초)가 어느 화면에서든 보이게.
        # 검색이 돌 땐 그쪽 진행이 상태줄 주인이다
        if not (self._loader and self._loader.isRunning()):
            self.statusBar().showMessage(f"팀컬러 — {text}")

    def _rank_snapshot_fresh(self) -> bool:
        try:
            return rankcollect.fresh_snapshot() is not None
        except Exception:
            return False

    def _on_teamcolor_waiting(self) -> None:
        for lb in self._teamcolor_status_labels:
            lb.setText("랭킹 수집이 같은 목록을 읽는 중 — 끝나면 그 결과를 씁니다…")

    def _on_teamcolor_finished(self) -> None:
        for b in self._teamcolor_fetch_btns:
            b.setEnabled(True)
        fetched = {n: (self._team_colors[n], self._team_values.get(n))
                  for n in self._teamcolor_pending if n in self._team_colors}
        if fetched:
            try:
                conn = store.open_db(config.DB_PATH)
                try:
                    store.save_team_colors(conn, fetched,
                                           fetched_at=getattr(self._teamcolor_loader, "fetched_at", None))
                finally:
                    conn.close()
            except Exception:
                pass  # DB 저장이 실패해도 이번 세션 캐시(메모리)는 살아 있다
        found = sum(1 for color, _ in fetched.values() if color)
        msg = f"상대 {len(self._teamcolor_pending)}명 조회 완료(팀컬러 확인 {found}명)"
        failed = getattr(self._teamcolor_loader, "failed_pages", 0)
        if failed:  # 못 읽은 쪽이 있으면 못 찾은 상대를 '랭킹 밖'으로 저장하지 않았다
            msg += f" · 랭킹 목록 {failed}쪽을 못 읽어 나머지는 다음에 다시 찾습니다"
        for lb in self._teamcolor_status_labels:
            lb.setText(msg)
        if not (self._loader and self._loader.isRunning()):
            self.statusBar().showMessage(f"팀컬러 — {msg}")
        self._invalidate("teamcolor")
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
        _, details = self._teamcolor_scope()  # 표와 같은 범위
        players = core.opponent_position_players(
            details, self._ouid,
            name_of=lambda i: self._names.get(i, str(i)),
            pos_name=lambda p: self._positions.get(p, str(p)),
            nicknames=nicknames)
        self._show_teamcolor_detail(color, players)

    def _show_teamcolor_detail(self, color: str,
                              players: list[core.PositionOpponent]) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{color} — 포지션별 기용률")
        v = QVBoxLayout(dlg)
        tbl = self._make_table(self.POSITION_OPP_COLUMNS)
        tbl.setSortingEnabled(False)  # 이유는 tbl_position_opp 와 동일
        tbl.itemDoubleClicked.connect(self._on_player_cell_double_clicked)
        self._fill(tbl, self._position_opp_rows(players), enable_sort=False)
        self._tint_position_rows(tbl, players)
        v.addWidget(tbl)
        fit_to_screen(dlg, 560, 480)
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
        fit_to_screen(dlg, 600, 760)
        # 내용은 세로 스크롤 안에 — 축구장 최소 560x640 + 제목이 약 688 이라 FHD 150% 노트북(창 안쪽 약 657)에선
        # 대화상자를 화면에 맞춰 줄여도 안 들어갔다(2026-10-04 실측)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        v = QVBoxLayout(body)
        outer.addWidget(VScrollArea(body))

        formation = core.formation_of(players)
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
            season_id = core.season_id_of(sp_id)
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
        fit_to_screen(dlg, 560, 720)
        outer = QVBoxLayout(dlg)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        card = QWidget()
        v = QVBoxLayout(card)
        status = QLabel("불러오는 중…")
        status.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(status)
        body = QWidget()
        v.addWidget(body, 1)
        tabs.addTab(card, "카드 정보")
        # 내 기록 — 이미 가진 경기 기록만 쓴다(넥슨 홈페이지 데이터를 꺼도 보인다)
        tabs.addTab(self._build_my_record(sp_id), "내 기록")
        # 랭커 기록(N2) — 오픈API 라 홈페이지 데이터 스위치와 무관. 탭을 처음 열 때 한 번 묻는다(포지션 28개 = 요청 하나)
        scout = self._build_scout_tab(sp_id)
        scout_idx = tabs.addTab(scout, "랭커 기록")
        tabs.currentChanged.connect(lambda i: i == scout_idx and self._start_scout(scout))
        self._last_player_tabs = tabs  # 테스트가 다이얼로그 안을 본다

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
        if self._scout_loader and self._scout_loader.isRunning():
            self._scout_loader.cancel()  # 창이 닫혔다 — 끝나도 신호를 안 낸다(지워진 표를 건드리지 않게)
            self._scout_loader.wait(12000)

    SCOUT_COLUMNS = ["포지션", *[name for name, _ in core.RANKER_METRICS], "랭커 표본", "기준일"]

    def _build_scout_tab(self, sp_id: int) -> QWidget:
        """선수 카드 [랭커 기록](N2) — 그 카드를 쓴 상위 랭커들의 포지션별 경기당 평균. 안 쓰는 카드도 된다."""
        w = QWidget()
        v = QVBoxLayout(w)
        status = QLabel("탭을 열면 넥슨 오픈API 에서 받습니다.")
        status.setObjectName("scoutStatus")
        status.setStyleSheet(f"color: {T.TEXT_DIM};")
        status.setWordWrap(True)
        v.addWidget(status)
        table = self._make_table(self.SCOUT_COLUMNS)
        table.setObjectName("scoutTable")
        v.addWidget(table, 1)
        w.sp_id, w.status, w.table, w.started = sp_id, status, table, False
        return w

    def _start_scout(self, tab: QWidget) -> None:
        if tab.started:
            return
        tab.started = True
        if not config.API_KEY:
            tab.status.setText("API 키가 없어 랭커 기록을 받을 수 없습니다.")
            return
        old = self._scout_loader
        if old is not None and old.isRunning():
            old.cancel()  # 앞 카드 창의 것 — 끝나도 신호를 안 낸다
        tab.status.setText("랭커 기록을 받는 중…")
        ld = RankerStatsLoader(self._api, [(tab.sp_id, po) for po in range(core.SUB_POSITION)],
                               config.DEFAULT_MATCH_TYPE)
        ld.done.connect(lambda res, t=tab: self._fill_scout(t, res))
        ld.failed.connect(lambda msg, t=tab: t.status.setText(f"랭커 기록을 받지 못했습니다 — {msg}"))
        self._scout_loader = ld
        ld.start()

    def _fill_scout(self, tab: QWidget, res: dict) -> None:
        few = config.RANKER_MIN_MATCHES
        found = []
        for (sp_id, po), r in res.items():
            got = core.ranker_values(r) if sp_id == tab.sp_id else None
            if got is not None:
                found.append((po, *got))
        found.sort(key=lambda t: -t[1])  # 표본 많은 자리부터 — 그 카드를 주로 쓰는 자리
        rows = []
        for po, n, day, vals in found:
            row = [self._positions.get(po, str(po))]
            for name, f in core.RANKER_METRICS:
                v = vals[name]
                row.append((f"{v:.0f}%" if isinstance(f, tuple) else f"{v:.2f}", v))
            row += [(f"{n}", n), day or NA]
            rows.append(row)
        self._fill(tab.table, rows, enable_sort=False)
        for i, (po, n, _day, _vals) in enumerate(found):
            if n < few:  # 표본 흐림 규칙(1.2.1)
                for c in range(tab.table.columnCount()):
                    item = tab.table.item(i, c)
                    if item:
                        item.setForeground(QColor(T.TEXT_DIM))
                        item.setToolTip(f"랭커 표본 {n}경기(기준 {few}) — 값이 크게 흔들립니다.")
                        item.setData(Qt.ItemDataRole.UserRole + 1, True)
        tab.table.setSortingEnabled(True)
        tab.status.setText(f"이 카드를 쓴 상위 랭커들의 경기당 평균 · 포지션 {len(found)}곳"
                           f" · 흐린 줄은 표본 {few}경기 미만" if found
                           else "이 카드를 쓴 랭커 기록이 없습니다.")

    def _build_my_record(self, sp_id: int) -> QWidget:
        """선수 카드 '내 기록' — 그 카드(spId)만의 슛 맵과 주 단위 결정력 추이. 범위는 위쪽 시즌 콤보.

        같은 선수라도 시즌이 다른 카드는 spId 가 달라 따로 본다(사용자 결정 2026-10-02).
        슛이 stats.PLAYER_TREND_MIN_SHOTS 보다 적으면 추이를 그리지 않는다 — 몇 개로 그린 선은 노이즈다."""
        w = QWidget()
        v = QVBoxLayout(w)
        weeks = core.player_finishing_trend(self._details, self._ouid, sp_id)
        sm = core.shot_map(self._details, self._ouid, mine=True, sp_id=sp_id)
        games = sum(x.games for x in weeks)
        shots = sum(x.shots for x in weeks)
        goals = sum(x.goals for x in weeks)
        xg = sum(x.xg for x in weeks)
        conv = f"{goals / shots * 100:.0f}%" if shots else NA
        summary = QLabel(f"{self._scope_text()} · 출전 {games}경기 · 슛 {shots} · 골 {goals}"
                         f" · 전환율 {conv} · xG {xg:.1f}")
        summary.setObjectName("myRecordSummary")
        summary.setWordWrap(True)
        v.addWidget(summary)
        pitch = ShotMapWidget()
        pitch.set_shots(sm.shots)
        pitch.setMinimumHeight(260)
        v.addWidget(pitch, 2)
        title = QLabel("주 단위 전환율(골 ÷ 슛)")
        title.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(title)
        if shots >= core.PLAYER_TREND_MIN_SHOTS:
            chart = charts.AreaTrendChart()
            chart.setObjectName("myRecordTrend")
            chart.set_data([(x.label, x.conversion, x.shots) for x in weeks])
            chart.setMinimumHeight(160)
            v.addWidget(chart, 1)
        else:
            lb = QLabel(f"표본 부족 — 슛 {shots}개(추이는 {core.PLAYER_TREND_MIN_SHOTS}개부터)")
            lb.setObjectName("myRecordTrend")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            v.addWidget(lb, 1)
        # 1.4.1 N9 — 주 단위 평균 평점(spRating 은 출전 기록 전부에 있다, R12). 슛과 달리 수비수·GK 도 나온다
        rweeks = core.rating_trend(self._details, self._ouid, sp_id)
        few = core.PLAYER_RATING_MIN_GAMES
        title = QLabel(f"주 단위 평균 평점 · 빈 고리는 그 주 {few}경기 미만")
        title.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(title)
        if rweeks:
            chart = charts.AreaTrendChart()
            chart.setObjectName("myRecordRating")
            values = [x.rating for x in rweeks]
            chart.set_data([(x.label, x.rating, x.games) for x in rweeks],
                           axis=charts.Axis.fit(values, fmt="{:.2f}", name="평점"),
                           weak=[x.weak for x in rweeks])
            chart.setMinimumHeight(160)
            v.addWidget(chart, 1)
        else:
            lb = QLabel("출전 기록이 없습니다.")
            lb.setObjectName("myRecordRating")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            v.addWidget(lb, 1)
        # 차트가 셋이라 최소 높이가 150% 노트북 대화상자 안쪽(657)을 넘는다 — 세로 스크롤(PyQt6 규칙 9)
        return VScrollArea(w)

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
        # 이동평균은 그래프에만 — _trend_periods 는 대시보드가 같이 쓴다. 창 앞 6일도 matches(시즌 범위) 안에서만.
        ma = core.moving_win_rate(matches, [p.day for p in self._trend_periods], min_n=core.MIN_COND)
        self.trend_chart.set_data(
            [(p.label, p.win_rate, p.games) for p in self._trend_periods],
            counts=True, baseline=50, ma=ma)

        # 등급 추이 — 승률 추이와 같은 "최근 N일" 구간으로 자른다. 점은 하루 마지막 등급(1만 경기 → 약 100점).
        days_div = core.daily_division(core.division_trend(self._details, self._ouid))
        if days_div:
            cutoff = days_div[-1].day - timedelta(days=days - 1)
            days_div = [d for d in days_div if d.day >= cutoff]
        name = lambda div: self._division_names.get(div, str(div))  # noqa: E731
        entry_days = {t.date() for t in self._sc_entries()[0]}
        self.gb_division.setTitle(f"최근 {days}일 등급 추이")
        self.division_chart.set_data(
            [(f"{d.day:%m/%d}", d.last) for d in days_div], self._division_names,
            tips=[f"{d.day:%Y-%m-%d} · 마지막 {name(d.last)} · 처음 {name(d.first)} · 최고 {name(d.best)}"
                  for d in days_div],
            markers=[i for i, d in enumerate(days_div) if d.day in entry_days])

        self._show_trend_summary()
        self._render_sc_records()
        self._render_elo()

    # ── ELO 그래프 (1.3.1 · 13) ──────────────────────────────────────────────
    def _load_elo(self, ouid: str | None = None) -> None:
        """EloLoader 를 띄운다 — 계정을 연 뒤(_on_loaded 전부) · 이번 검색 ELO 를 적은 뒤(elo_saved) · 수집 회차 끝.
        요청 번호를 올려 두고 elo_ready 는 그 계정의 마지막 번호만 받는다(늦게 끝난 옛 읽기가 새 값을 덮지 않게)."""
        ouid = ouid or self._ouid
        if not ouid:
            return
        self._elo_workers = [w for w in self._elo_workers if w.isRunning()]
        req = self._elo_req.get(ouid, 0) + 1
        self._elo_req[ouid] = req
        w = EloLoader(ouid, req)
        w.elo_ready.connect(self._on_elo_ready)
        self._elo_workers.append(w)
        w.start()

    def _on_elo_ready(self, ouid: str, data) -> None:
        if data is None or data.req != self._elo_req.get(ouid):
            return
        self._elo[ouid] = data
        if ouid == self._ouid:
            self._render_elo()
        self._load_predict(ouid)    # EloLoader 를 띄우는 모든 경우 뒤에(진입점 표 다섯째 줄)

    def _load_predict(self, ouid: str | None) -> None:
        """PredictWorker 를 띄운다 — 요청 번호 규칙은 EloLoader 와 같다(늦게 끝난 옛 계산이 새 값을 덮지 않게)."""
        data = self._elo.get(ouid) if ouid else None
        if data is None:
            return
        self._pred_workers = [w for w in self._pred_workers if w.isRunning()]
        req = self._pred_req.get(ouid, 0) + 1
        self._pred_req[ouid] = req
        w = PredictWorker(ouid, req, data.rows, self._rank_seasons, self._season_notice())
        w.pred_ready.connect(self._on_pred_ready)
        self._pred_workers.append(w)
        w.start()

    def _on_pred_ready(self, ouid: str, payload) -> None:
        req, pr = payload
        if req != self._pred_req.get(ouid):
            return
        self._pred[ouid] = pr
        if ouid == self._ouid:
            self._render_predict()

    def stop_predict_workers(self) -> None:
        """[수집 기록 지우기] 전에 — cancel → wait(종료 정리와 같은 규칙). 지운 뒤 들고 있던 예측도 버린다."""
        for w in self._pred_workers:
            w.cancel()
            w.wait(3000)
        self._pred_workers = []
        self._pred.clear()
        self._render_predict()

    def _render_predict(self) -> None:
        pr = self._pred.get(self._ouid) if self._ouid else None
        self.lb_elo_predict.setText(core.describe_prediction(pr) if pr is not None else "")
        self.lb_elo_predict.setVisible(pr is not None and config.WEB_DATA)

    def _elo_points(self) -> list[dict]:
        data = self._elo.get(self._ouid) if self._ouid else None
        if data is None:
            return []
        start = elo_season_start(self._rank_seasons, data.snap_season_start, datetime.now())
        return elo_daily_points(data.rows, start)

    def _render_elo(self) -> None:
        """들고 있는 self._elo[self._ouid] 로만 그린다(읽기 없음 — _render_trend 가 검색·시즌 전환마다 부른다)."""
        data = self._elo.get(self._ouid) if self._ouid else None
        pts = self._elo_points()
        start = elo_season_start(self._rank_seasons, data.snap_season_start if data else None, datetime.now())
        self.gb_elo.setTitle(f"ELO(랭킹 점수) — 지금 시즌 ({start:%m/%d}~)")
        self.lb_elo_tier.setText(rank_tier_change_text(pts))
        self.lb_elo_tier.setVisible(bool(pts))
        self.lb_elo_note.setText(self._elo_note(data, pts))
        self.lb_elo_note.setVisible(bool(self.lb_elo_note.text()))
        self._render_elo_track_button(data)
        self._render_predict()
        if len(pts) < 2:
            self.elo_chart.set_data([])
            self.elo_chart.setVisible(False)
            return
        self.elo_chart.setVisible(True)
        vals = [p["elo"] for p in pts]
        refs = []
        for i, rank in enumerate(config.ELO_CUT_LINES):
            series = []   # 창 앞 값도 넘긴다 — 계단선이 첫 점 날짜에 유효했던 컷부터 긋는다
            for t, e in (data.cuts.get(rank) or []):
                try:
                    series.append((datetime.fromisoformat(t).date(), e))
                except ValueError:
                    continue
            if series:
                refs.append((f"{rank:,}위", series, T.CHART_NEUTRAL if i else T.CHART_DOWN))
        self.elo_chart.set_data(
            [(p["at"].strftime("%m/%d"),
              p["elo"], None) for p in pts],
            axis=charts.Axis.fit(vals), avg=False, x_dates=[p["at"].date() for p in pts], ref_series=refs)
        self.elo_chart.setToolTip(self._elo_cut_tip(data))

    @staticmethod
    def _elo_cut_tip(data) -> str:
        """그리지 않는 컷(1만 위 등)은 값만 툴팁으로 — 축을 넓혀 그래프를 찌그러뜨린다."""
        last = []
        for rank in config.RANK_CUT_RANKS:
            series = (data.cuts.get(rank) if data else None) or []
            if rank in config.ELO_CUT_LINES or not series:
                continue
            last.append(f"{rank:,}위 {series[-1][1]:,.0f}")
        return ("마지막 수집의 순위 컷 — " + " · ".join(last)) if last else ""

    def _elo_note(self, data, pts: list[dict]) -> str:
        """빈 상태·안내는 원인별로(검토 B 8·9). 켜기 버튼은 두지 않는다 — 켜기는 동의 흐름이 있는 [정보·설정] 한 곳."""
        if not config.WEB_DATA:
            return "홈페이지 데이터가 꺼져 있어 ELO 를 받지 않습니다 — [정보·설정]"
        out = []
        if pts and rank_tier_index(pts[-1].get("rank")) >= len(config.RANK_TIERS):
            out.append("1만 위 밖은 하루 기록이 없습니다")
        tracked = bool(data and data.tracked)
        if not config.RANK_COLLECT:
            if len(pts) < 2:
                out.append("검색할 때마다 한 점씩 쌓입니다 — 랭킹 수집을 켜 두면 하루 한 점")
        elif tracked:
            if len(pts) < 2:
                out.append("다음 수집(하루 한 번) 뒤부터 그려집니다")
        else:
            out.append("검색할 때마다 한 점 — [따라가기]를 누르면 하루 한 점")
        return " · ".join(out)

    def _render_elo_track_button(self, data) -> None:
        b = self.btn_elo_track
        names = list(data.track_names) if data else []
        tracked = bool(data and data.tracked)
        b.setVisible(bool(self._ouid) and config.WEB_DATA)
        if tracked:
            b.setText("따라가기 그만")
            b.setEnabled(True)
            b.setToolTip("하루 한 번 기록을 그만 둡니다 — 이미 쌓인 점은 남습니다")
            return
        b.setText(f"이 구단주 ELO 따라가기 ({len(names)}/{config.ELO_TRACK_MAX})")
        full = len(names) >= config.ELO_TRACK_MAX
        b.setEnabled(not full)
        if full:
            b.setToolTip(f"{config.ELO_TRACK_MAX}명까지 — 지금 목록: " + ", ".join(names))
        elif not config.track_allowed():
            b.setToolTip("이용 안내 동의가 필요합니다 — 누르면 안내 창이 열립니다")
        else:
            tip = "랭킹 수집이 켜져 있으면 하루 한 번 ELO·순위를 이어서 기록합니다(지울 때까지)"
            if self._rank is None or getattr(self._rank, "profile_sn", None) is None:
                tip += " — 1만 위 안일 때만 하루 기록이 쌓입니다"
            b.setToolTip(tip)

    def _on_elo_track_clicked(self) -> None:
        if not self._ouid:
            return
        data = self._elo.get(self._ouid)
        if not (data and data.tracked) and not config.track_allowed():
            self.ask_notice_update()
            if not config.track_allowed():
                return
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                if data and data.tracked:
                    store.track_remove(conn, self._ouid)
                else:
                    sn_ = getattr(self._rank, "profile_sn", None) if self._rank is not None else None
                    store.track_add(conn, self._ouid, sn_, self._nick or "")
            finally:
                conn.close()
        except store.TrackFull:
            self.statusBar().showMessage(f"따라가기는 {config.ELO_TRACK_MAX}명까지입니다.", 5000)
        except sqlite3.Error as e:
            self.statusBar().showMessage(f"따라가기 목록을 저장하지 못했습니다: {e}", 5000)
        self._load_elo(self._ouid)

    def _sc_entries(self) -> tuple[list, bool]:
        """저장된 전체(_details_all — 시즌 필터 무시)에서 슈챔 진입 시각 — 캐시.

        _render_trend 는 지연 그리기 밖이라 검색·시즌 전환마다 돈다 — 1만 경기 훑기(약 10ms)를 매번 안 하게
        목록 id·길이·맨 앞 경기로 키를 잡는다(_narrate_scope 와 같은 방식)."""
        d = self._details_all
        key = (self._ouid, id(d), len(d), d[0].get("matchId") if d else None)
        if getattr(self, "_sc_cache_key", None) != key:
            self._sc_cache = core.division_entries(core.division_trend(d, self._ouid),
                                                   core.SUPER_CHAMPION_DIVISION_ID)
            self._sc_cache_key = key
        return self._sc_cache

    SC_NEXON_MATCH_S = 60  # 넥슨 최근 달성 시각이 목록의 진입과 이만큼 안이면 같은 것으로 본다

    def _render_sc_records(self) -> None:
        """슈퍼 챔피언스 달성 기록 — _render_trend 와 _on_max_division 둘 다 부른다.
        maxdivision 신호는 finished_ok 뒤에 오므로 _render_trend 만 부르면 넥슨 줄이 늘 빠진다."""
        entries, at_start = self._sc_entries()
        dated = [m.match_date for m in self._matches_all if m.match_date is not None]
        first = min(dated).date() if dated else None
        span = f"{first:%Y-%m-%d} ~" if first else "없음"
        self.lb_sc_note.setText(
            f"저장된 경기 범위({span})만 — 앱을 쓰기 전 기록은 없습니다. "
            "경기 사이가 비면 그 사이 달성은 빠질 수 있습니다.")
        by_day: dict = {}
        for t in entries:
            by_day.setdefault(t.date(), []).append(t)
        lines = [f"{d:%Y-%m-%d} · {len(ts)}번 ({' · '.join(f'{t:%H:%M}' for t in ts)})"
                 for d, ts in sorted(by_day.items(), reverse=True)]
        if at_start and first:
            lines.append(f"기록 시작({first:%Y-%m-%d}) 때 이미 슈퍼 챔피언스")
        self.lb_sc_list.setText("\n".join(lines) if lines else
                                "저장된 경기에는 슈퍼 챔피언스 달성 기록이 없습니다")
        self.lb_sc_nexon.setText(self._sc_nexon_text(entries, first) or "")
        self.lb_sc_nexon.setVisible(bool(self.lb_sc_nexon.text()))

    def _sc_nexon_text(self, entries: list, first) -> str | None:
        """넥슨 기록상 최근 달성이 목록에 없을 때만 한 줄. 최고가 슈챔이 아니면 없다."""
        info = self._max_division.get(self._ouid) if self._ouid else None
        if not info or info.get("division") != core.SUPER_CHAMPION_DIVISION_ID or not info.get("date"):
            return None
        at = None
        try:
            at = datetime.fromisoformat(info["at"]) if info.get("at") else None
        except ValueError:
            at = None
        if at is not None:
            if any(abs((t - at).total_seconds()) <= self.SC_NEXON_MATCH_S for t in entries):
                return None
        elif any(f"{t:%Y-%m-%d}" == info["date"] for t in entries):
            return None  # 옛 값(날짜만) — 날짜로 비교
        where = "저장된 경기 밖" if first is None or info["date"] < f"{first:%Y-%m-%d}" else \
            "저장된 경기에서는 못 찾음 — 그 사이 경기가 비었을 수 있습니다"
        return f"넥슨 기록상 최근 달성 {info['date']} ({where})"

    def _show_trend_summary(self) -> None:
        """승률 그래프 페이지의 기간 요약 — 선택한 기간의 최고·평균·최저 승률."""
        periods = getattr(self, "_trend_periods", [])
        days = self.sp_trend_days.value()
        rates = [p.win_rate for p in periods if p.games]
        total_games = sum(p.games for p in periods)
        total_win = sum(p.win for p in periods)
        avg_rate = (total_win / total_games * 100) if total_games else 0.0
        self.card_trend_max.set(f"{max(rates):.1f}%" if rates else NA)
        self.card_trend_avg.set(f"{avg_rate:.1f}%" if total_games else NA)
        self.card_trend_min.set(f"{min(rates):.1f}%" if rates else NA)
        self.card_trend_games.set_title(f"{days}일 경기수")
        self.card_trend_games.set(f"{total_games}경기")

    def _render_dashboard(self) -> None:
        """대시보드 — 카드마다 자기 상세 페이지와 같은 범위를 넘긴다."""
        matches, details = self._slice()
        periods = getattr(self, "_trend_periods", [])
        self.dashboard.render(DashboardInput(
            ouid=self._ouid,
            range_matches=matches, range_details=details,
            scope_matches=self._matches, scope_details=self._details,
            scope_name=self._scope_text(),
            trend_points=[(p.label, p.win_rate, p.games) for p in periods],
            trend_days=self.sp_trend_days.value(),
            story=self._narrate_scope()))

    def _render_seasons(self) -> None:
        groups = self._season_groups()
        last_end = max((s.end for s in self._rank_seasons), default=None)
        sums = [summarize(group) for _, group in groups]
        divs = core.season_divisions(core.division_trend(self._details_all, self._ouid), groups)
        name = lambda div: self._division_names.get(div, str(div))  # noqa: E731
        rows, bars, tips = [], [], []
        for i, ((season, group), s, dv) in enumerate(zip(groups, sums, divs)):
            if season is None:
                label, span, key = "진행 중", f"{last_end:%Y-%m-%d} ~", 99999999
            else:
                label, span = season.label, season.span_text
                key = int(season.start.strftime("%Y%m%d"))
            # 바로 앞 시즌 = 목록에서 다음 것(최신순) — 경기 없는 시즌은 목록에 없어 건너뛴다
            prev = sums[i + 1] if i + 1 < len(sums) else None
            if prev is not None and s.total and prev.total:
                diff = s.win_rate - prev.win_rate
                vs = (f"{diff:+.1f}%p", diff)
            else:
                vs = ("—", -1e9)
            grade = ((f"{name(dv[0])} → {name(dv[1])}" if dv[0] != dv[1] else name(dv[0]), -dv[1])
                     if dv else ("—", -1e9))
            tips.append(f"최고 {name(dv[2])}" if dv else "")
            rows.append([
                (label, key), span,
                (f"{s.total:,}", s.total),
                (f"{s.win:,}", s.win), (f"{s.draw:,}", s.draw),
                (f"{s.lose:,}", s.lose),
                (f"{s.win_rate:.1f}%", s.win_rate),
                vs, grade,
                (f"{s.avg_goals_for:.2f}", s.avg_goals_for),
                (f"{s.avg_goals_against:.2f}", s.avg_goals_against),
                (f"{s.avg_possession:.1f}%", s.avg_possession),
                (f"{s.avg_rating:.2f}", s.avg_rating),
            ])
            weak = s.total < core.MIN_COND
            bars.append((label, s.win_rate if s.total else None, f"{s.win_rate:.1f}% · {s.total:,}경기",
                         sample_note(s.total, core.MIN_COND) if weak else f"{label} · {span}", weak))
        self.season_bars.set_data(bars)
        self._fill(self.tbl_seasons, rows, enable_sort=False)  # 툴팁을 채운 순서대로 붙인다(표 함정 1번)
        grade_col = self.SEASON_COLUMNS.index("등급")
        for r, tip in enumerate(tips):
            item = self.tbl_seasons.item(r, grade_col)
            if item and tip:
                item.setToolTip(tip)
        self.tbl_seasons.setSortingEnabled(True)

        if not self._rank_seasons:
            self.lb_season_note.setText(
                "※ 넥슨 데이터센터에서 시즌표를 받지 못했습니다 —"
                " 잠시 후 다시 검색하면 채워집니다(그동안 판수 기준은 그대로 씁니다).")
        else:
            # 위쪽 시즌 콤보는 모든 경기를, 이 표·승률은 승·무·패만 센다(summarize).
            # 그 차이를 안 적으면 같은 시즌이 두 숫자로 보인다(2026-10-02 스크린샷에서 발견).
            n_err = len(self._matches_all) - summarize(self._matches_all).total
            err = (f" · 경기 수는 승·무·패만 셉니다 — 중단된 '오류' 경기 {n_err}개는 빠져서"
                   " 위 시즌 목록의 경기 수보다 적을 수 있습니다." if n_err else "")
            self.lb_season_note.setText(
                "※ 넥슨 오픈API 는 약 한 달 전까지의 경기만 돌려줍니다 — 그보다 오래된"
                " 경기는 이 앱으로 조회해 쌓아 둔 것만 있으므로, 앱을 쓰기 전 시즌은"
                " 비어 있거나 실제보다 적게 나옵니다."
                " · '진행 중'은 아직 데이터센터 시즌표에 안 올라온 최신 시즌입니다."
                " · 이 표만은 위 시즌 필터를 무시하고 언제나 누적 전체를 보여줍니다." + err)

    def _render_players(self, details: list[dict]) -> None:
        players = core.aggregate_players(
            details, self._ouid,
            name_of=lambda i: self._names.get(i, str(i)),
            pos_name=lambda p: self._positions.get(p, str(p)))
        few = core.MIN_PLAYER_GAMES
        rows = []
        for p in players:
            rows.append([
                p.position, p.name, (f"{p.grade}", p.grade),
                (f"{p.games} ⚠" if p.games < few else f"{p.games}", p.games),
                (f"{p.win_rate:.1f}", p.win_rate),
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
        # 강화 열(2)은 배지라 글자 폭 + 배지 여백 — 그 열에만 준다(GradeBadgeDelegate 머리말)
        self._fit_columns_to_content(self.tbl_players,
                                     extra={1: 26, 2: 2 * GradeBadgeDelegate.PAD})
        # 공격력(5열)·수비력(6열) — 최소~최대를 넓은 단계로 칠한다. 눈금은 출전이 충분한 선수로만 잡는다.
        enough = [p.games >= few for p in players]
        atk = self._heat_scale([p.attack_power for p in players], enough)
        dfn = self._heat_scale([p.defense_power for p in players], enough)
        for r, p in enumerate(players):
            self._heat(self.tbl_players.item(r, 5), atk[r], T.HEAT_ATK)
            self._heat(self.tbl_players.item(r, 6), dfn[r], T.HEAT_DEF)
            pos_item = self.tbl_players.item(r, 0)
            line = core.position_line(p.pos_code)
            if pos_item and line:
                pos_item.setForeground(QColor(T.POS_COLORS[line]))
            if not enough[r]:
                games_item = self.tbl_players.item(r, 3)
                if games_item:
                    games_item.setToolTip(f"출전 {p.games}경기 — 비율이 크게 흔들립니다"
                                          f"(색은 {few}경기부터)")
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
            season_id = core.season_id_of(sp_id)
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
    def _heat_scale(values: list[float], enough: list[bool]) -> list[float | None]:
        """값 → 0~1 단계((값−최소)/(최대−최소)). 최소·최대는 enough 인 것만으로 잡는다 — 1경기 선수 하나가
        눈금을 늘이지 않게. enough 가 아니거나 눈금을 못 잡으면(대상 0~1명 · 전부 같은 값) None = 안 칠함."""
        pool = [v for v, ok in zip(values, enough) if ok]
        if len(pool) < 2:
            return [None] * len(values)
        lo, hi = min(pool), max(pool)
        if hi <= lo:
            return [None] * len(values)
        return [max(0.0, min((v - lo) / (hi - lo), 1.0)) if ok else None
                for v, ok in zip(values, enough)]

    # 가장 낮은 단계도 바탕과 구분되게 — 0 이면 칠하지 않은 칸과 같아 보인다
    HEAT_FLOOR = 0.12

    @staticmethod
    def _heat(item, frac: float | None, end_hex: str) -> None:
        """단계 frac(0~1) → PANEL 에서 end_hex 쪽으로 섞은 불투명 배경(PyQt 규칙 2번). None 이면 그대로."""
        if item is None or frac is None:
            return
        f = MainWindow.HEAT_FLOOR
        item.setBackground(MainWindow._blend(T.PANEL, end_hex, f + (1 - f) * frac))

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
        # 다크 테마 땐 배경이 워낙 어두워 30%~85% 로 잡았다. 흰 배경에선 그 값이면
        # 높은 쪽이 거의 원색이라 검은 글자가 묻힌다 — 10%~55% 로 낮췄다.
        mix = 0.10 + frac * 0.45
        item.setBackground(MainWindow._blend(T.PANEL, hexcolor, mix))

    @staticmethod
    def _clear(box) -> None:
        while box.count():
            item = box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_tactics(self, details: list[dict]) -> None:
        mine = core.formation_stats(details, self._ouid, of_opponent=False)
        if mine:
            t = mine[0]
            self.lb_my_formation.setText(
                f"{t.formation}      {t.win_rate:.1f}%      "
                f"{t.games}경기 · {wdl_text(t.win, t.draw, t.lose)}")

        self._clear(self.box_opp)
        few = core.MIN_COND
        opp_rows = []
        for f in core.formation_stats(details, self._ouid):
            weak = f.games < few
            opp_rows.append((
                f.formation, f.win_rate,
                f"{f.win_rate:.1f}% · {wdl_text(f.win, f.draw, f.lose)}" + (f" · 표본 {f.games}" if weak else ""),
                f"상대 {f.formation} — {f.games}경기 승률 {f.win_rate:.1f}%"
                + (f"\n{sample_note(f.games, few)}" if weak else ""),
                weak))
        if opp_rows:
            head = QLabel(f"상대 포메이션별 내 승률 · 흐린 줄은 {few}경기 미만")
            head.setStyleSheet(f"color: {T.TEXT_DIM};")
            self.box_opp.addWidget(head)
            self.opp_formation_bars = charts.HBarList()
            self.opp_formation_bars.set_data(opp_rows)
            self.box_opp.addWidget(self.opp_formation_bars)

        rb = core.result_breakdown(details, self._ouid)
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
            if wdl is rb.forfeit:
                row.setToolTip(FORFEIT_TIP)
            self.box_result.addWidget(row)

        sep = QLabel("시간대별 득실")
        sep.setStyleSheet(f"color: {T.GREEN}; font-weight: bold; padding-top: 3px;")
        self.box_result.addWidget(sep)
        for k in sorted(rb.periods):
            v = rb.periods[k]
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            a = QLabel(core.PERIODS.get(k, str(k)))
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

        self.type_donuts = {}
        for key, box, counter, word in (("gf", self.box_gf, rb.goal_types, "득점"),
                                        ("ga", self.box_ga, rb.concede_types, "실점")):
            self._clear(box)
            total = sum(counter.values())
            segs = charts.donut_segments(list(counter.items()))
            donut = charts.DonutChart(size=120)
            donut.set_data(segs, center=f"{total:,}", sub=word)
            self.type_donuts[key] = donut
            box.addWidget(donut, 0, Qt.AlignmentFlag.AlignHCenter)
            for name, n, col in segs:   # 범례 겸 숫자 — 조각과 같은 색
                box.addWidget(BarRow(name, n, total, col))
            box.addStretch(1)
        self._render_pass_style(details)

    def _render_pass_style(self, details: list[dict]) -> None:
        """패스 종류 6가지 — 비중(6종 합 대비)·경기당·성공률, 이긴/진 경기 나란히(N5). 원인 단정 없이 숫자만."""
        ps = core.pass_style(details, self._ouid)
        self.pass_style = ps  # 테스트가 계산 결과를 본다
        g = self.grid_pass
        self._clear(g)
        if not ps.all.games:
            lb = QLabel("패스 종류 기록이 있는 경기가 없습니다.")
            lb.setStyleSheet(f"color: {T.TEXT_DIM};")
            g.addWidget(lb, 0, 0)
            self.lb_pass_note.setText("")
            return
        few, min_tries = core.MIN_COND, core.PASS_MIN_TRIES

        def rate(k) -> str:
            return f"{k.rate:.0f}%" if k.tries >= min_tries else NA

        heads = ["종류", "비중", "경기당", "성공률",
                 f"이긴 경기 {ps.win.games} — 비중 · 성공률", f"진 경기 {ps.lose.games} — 비중 · 성공률"]
        for c, text in enumerate(heads):
            lb = QLabel(text)
            lb.setStyleSheet(f"color: {T.TEXT_DIM}; font-weight: bold;")
            g.addWidget(lb, 0, c)
        for r, (k, kw, kl) in enumerate(zip(ps.all.kinds, ps.win.kinds, ps.lose.kinds), 1):
            cells = [(k.name, False), (f"{ps.all.share(k):.0f}%", False), (f"{ps.all.per_game(k):.1f}", False),
                     (rate(k), False)]
            for grp, kk in ((ps.win, kw), (ps.lose, kl)):
                cells.append((f"{grp.share(kk):.0f}% · {rate(kk)}" if grp.games else NA, grp.games < few))
            for c, (text, weak) in enumerate(cells):
                lb = QLabel(text)
                lb.setStyleSheet(f"color: {T.TEXT_DIM if weak else T.TEXT};" + (" font-weight: bold;" if c == 0 else ""))
                if weak:  # 표본 흐림 규칙(1.2.1)
                    lb.setToolTip(sample_note(ps.win.games if c == 4 else ps.lose.games, few))
                g.addWidget(lb, r, c)
        note = (f"비중은 6가지 종류 합 대비 · 성공률은 시도 {min_tries}회 미만이면 {NA} · "
                f"흐린 칸은 {few}경기 미만 · 무승부는 전체에만 들어갑니다.")
        if ps.skipped:
            note += f" 종류 기록이 없는 {ps.skipped}경기는 뺐습니다."
        self.lb_pass_note.setText(note)

    def closeEvent(self, e) -> None:
        if self._quitting:  # quit_app 이 이미 정리했다 — app.quit() 이 보이는 창에 한 번 더 부른다(PyQt 6.11 실측)
            super().closeEvent(e)
            return
        sh = self.shell
        if sh is None:  # 껍데기 없이 만든 창(테스트) — 예전처럼 여기서 정리하고 닫는다
            self.shutdown()
            super().closeEvent(e)
            return
        e.ignore()
        if sh.should_hide():
            sh.hide_window()
        else:
            sh.quit_app()

    def shutdown(self, fast: bool = False) -> list:
        """끝낼 때 정리 — 설정 저장이 먼저, 그다음 도는 작업 멈추기.
        fast(윈도우 종료·로그오프): 멈춤 요청만 하고 기다리지 않는다 → 아직 도는 스레드 목록(quit_app 이 합계
        FAST_QUIT_WAIT_S 만 기다리고 남은 것은 terminate). 아니면 예전처럼 하나씩 기다린다 — 최악 합계 약 60초."""
        self._save_settings()
        if self._prefetch is not None:
            self._prefetch.discard()  # 읽던 중이면 다음 행에서 멈춘다 — 종료를 2초씩 붙잡지 않게
        prune = getattr(self, "_prune_worker", None)

        def cancel(t):
            return getattr(t, "cancel", None)

        # (스레드, 멈춤 요청, 기다림 ms, 못 끝나면 terminate 하나)
        table = [
            # 파일을 지우는 중 — 하다 만 정리는 다음에 켤 때 이어서 한다
            (prune, prune.requestInterruption if prune else None, 3000, True),
            # requests 는 중간에 못 끊는다 — 타임아웃(5초)까지 기다려야 스레드가 안전히 끝난다
            (self._update_worker, None, 6000, False),
            # 다음 덩어리에서 멈춘다(덩어리 하나는 끝까지 받는다)
            (self._download_worker, cancel(self._download_worker), 20000, False),
            # 진행 중이던 상세 요청이 네트워크 타임아웃까지 갈 수 있어 넉넉히. DB 쓰기는 이 지점 이후라 강제 종료도 안전
            (self._loader, cancel(self._loader), 8000, True),
            (self._teamcolor_loader, cancel(self._teamcolor_loader), 3000, True),
            (self._table_season_loader, cancel(self._table_season_loader), 500, False),
            (self._finishing_icon_loader, cancel(self._finishing_icon_loader), 500, False),
            (self._compare_loader, cancel(self._compare_loader), 8000, True),
            # 쪽 사이에서 멈춘다 — 진행 중인 요청 하나(타임아웃 10초)까지. 쪽마다 한 트랜잭션이라 terminate 하지 않는다
            (self._trade_loader, cancel(self._trade_loader), 12000, False),
            # 카드 사이에서 멈춘다(한 장 = 한 트랜잭션) — 같은 이유로 terminate 하지 않는다
            (self._price_loader, cancel(self._price_loader), 12000, False),
            # 랭커 기록 — 묶음 사이에서 멈춘다(요청 하나 0.3초 · 타임아웃 10초). 묶음마다 한 트랜잭션
            (self._ranker_loader, cancel(self._ranker_loader), 12000, False),
            (self._scout_loader, cancel(self._scout_loader), 12000, False),
            *[(ld, cancel(ld), 500, False) for ld in self._compare_squad_loaders],
            # 로컬 DB 읽기 둘 — 금방 끝난다. 끝나면 신호를 안 낸다(cancel)
            *[(ld, cancel(ld), 1000, False) for ld in self._elo_workers],
            *[(ld, cancel(ld), 1000, False) for ld in self._pred_workers],
            # cancel 이 없다 — GET 한 번이라 타임아웃(10초)까지만 붙잡는다
            (self._season_loader, None, 3000, True),
            (self._ability_sim_loader, None, 2000, False),
            (self._position_ovr_loader, None, 2000, False),
        ]
        running = [(t, stop, ms, term) for t, stop, ms, term in table if t is not None and t.isRunning()]
        # 멈춤 요청을 전부 먼저 — 하나씩 "요청 → 기다림"이면 뒤 스레드가 앞의 기다림 동안 계속 돌아 최악이 합이 된다
        # (1.4.1 에 12초짜리 둘이 들어오며 85초를 넘을 뻔했다 · test_shutdown_requests_all_stops_first)
        for _t, stop, _ms, _term in running:
            if stop:
                stop()
        if fast:
            return [t for t, *_ in running]
        for t, _stop, ms, term in running:
            if not t.wait(ms) and term:
                t.terminate()
                t.wait(1000)
        return []

    # ── 숨긴 창 기록 내려놓기(1.1.1) ────────────────────────────────────
    def busy_for_release(self) -> bool:
        """검색·팀컬러·비교 조회 중이면 미룬다 — 도중에 목록을 비우면 끝난 결과가 빈 화면에 쏟아진다."""
        return any(t is not None and t.isRunning()
                   for t in (self._loader, self._teamcolor_loader, self._compare_loader))

    def release_memory(self) -> None:
        """숨긴 지 RELEASE_AFTER_HIDE_MIN 지나면 경기 기록을 놓는다(1만 경기면 수백 MB). 붙잡는 곳 전부 —
        하나라도 남으면 gc 가 못 거둔다(test_release_drops_every_reference 가 잰다). 계정(_ouid)은 비우지 않는다 —
        비우면 다시 읽을 때 '다른 계정'으로 보여 승률 그래프 기간이 초기화된다(이미 한 번 고친 버그)."""
        # 검색 없이 숨겨도 켤 때 미리 읽은 마지막 계정(1만 경기면 약 800MB)이 남는다 — 목록만 보면 그걸 놓친다(1.1.1 exe 실측)
        if self._released or (not self._matches_all and self._prefetch is None):
            return
        self._save_settings()  # 메뉴·시즌 — 다시 그릴 때 _restore 로 돌아온다
        try:
            s = self._settings()
            self._restore = {k: s.value(f"view/{k}") for k in ("page", "season") if s.value(f"view/{k}")}
        except Exception:
            self._restore = {}
        self._matches_all, self._details_all = [], []
        self._matches, self._details = [], []
        self._narrate_found, self._narrate_key = [], None
        if self._prefetch is not None:
            self._prefetch.discard()  # discard 는 멈춤 표시만 — 끝난 Future 의 결과는 참조를 놓아야 풀린다
        self._prefetch = None
        for name in ("_loader", "_compare_loader", "_teamcolor_loader"):
            t = getattr(self, name)
            if t is not None and not t.isRunning():
                setattr(self, name, None)  # 끝난 로더가 _prev(화면 목록)·미리 읽기를 쥐고 있다
        self._teamcolor_pending = []
        self._released = True
        gc.collect()

    def reload_after_release(self) -> None:
        """다시 열 때 — 그 계정의 저장된 경기를 DB 에서 다시 읽는다(1만 경기 약 1~2초). 넥슨에는 묻지 않는다."""
        if not self._released:
            return
        self._released = False
        if not self._ouid:
            self.start_prefetch()  # 검색 전에 놓은 미리 읽기 — 첫 검색이 다시 빠르게
            return
        if self._loader and self._loader.isRunning():
            return
        self._set_busy(True)
        self.statusBar().showMessage(f"{self._nick} — 저장된 기록을 다시 읽는 중…")
        self._loader = MatchLoader(self._api, self._nick, config.DEFAULT_MATCH_TYPE, offline_ouid=self._ouid)
        self._loader.progress.connect(self._on_progress)
        self._loader.finished_ok.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()


class CachePruneWorker(QThread):
    """켤 때 한 번 — 이미 DB 에 있는 경기의 디스크 캐시를 지운다(FCOnlineAPI.prune_detail_cache).
    수만 개 파일을 훑으니 UI 스레드에서 하지 않는다. 실패는 조용히 — 다음에 켤 때 다시."""
    done = pyqtSignal(int, int)  # 지운 수, 바이트

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api

    def run(self) -> None:
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                ids = store.all_match_ids(conn)
            finally:
                conn.close()
            self.done.emit(*self._api.prune_detail_cache(ids, stop=self.isInterruptionRequested))
        except Exception:
            pass


class UpdateCheckWorker(QThread):
    """GitHub 최신 릴리스 확인(updatecheck.check)을 UI 스레드 밖에서."""
    found = pyqtSignal(object)  # updatecheck.Release — 새 버전이 있을 때만
    latest = pyqtSignal()       # 실제로 확인했고 지금이 최신일 때만
    unknown = pyqtSignal()      # 확인 못 함(꺼짐·오프라인·릴리스 없음) — '최신'과 다르다
    season_notice = pyqtSignal(object)   # (종료일, 시작일) — 릴리스 본문에 공지가 있을 때만(1.3.1)

    def run(self) -> None:
        try:
            r = updatecheck.check_full()
            status, rel = r.status, r.release
            if r.season_notice is not None:
                self.season_notice.emit(r.season_notice)
        except Exception:
            status, rel = updatecheck.UNKNOWN, None  # 알림 하나 때문에 크래시 로그가 쌓이면 안 된다
        if status == updatecheck.NEWER and rel:
            self.found.emit(rel)
        elif status == updatecheck.LATEST:
            self.latest.emit()
        else:
            self.unknown.emit()


class UpdateDownloadWorker(QThread):
    """설치 파일 내려받기 + 체크섬 검증(updatecheck.download_verified)."""
    progress = pyqtSignal(int, int)  # (받은 바이트, 전체 바이트 — 모르면 0)
    done = pyqtSignal(str)           # 검증을 통과한 설치 파일 경로
    failed = pyqtSignal(str)

    def __init__(self, rel):
        super().__init__()
        self._rel = rel
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        dest = Path(tempfile.gettempdir()) / "FifaMatchTracker-update"
        try:
            path = updatecheck.download_verified(
                self._rel, dest, progress=self.progress.emit, cancelled=lambda: self._cancel)
        except updatecheck.UpdateError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:  # 업데이트가 앱을 죽이면 안 된다
            self.failed.emit(f"업데이트 중 오류: {e}")
            return
        self.done.emit(str(path))


class KeyCheckWorker(QThread):
    """키 확인 호출(nexon_api.check_key)을 UI 스레드 밖에서 한다."""
    checked = pyqtSignal(str, str)  # (키, 실패 이유 — 통과면 빈 문자열)

    def __init__(self, key: str):
        super().__init__()
        self._key = key

    def run(self) -> None:
        try:
            reason = check_key(self._key, timeout=6) or ""
        except Exception as e:
            reason = f"확인 중 오류: {e}"
        self.checked.emit(self._key, reason)


class ApiKeyDialog(QDialog):
    """넥슨 오픈API 키 입력 — 첫 실행, 또는 넥슨이 키를 거절했을 때.

    받은 사람이 숨김 폴더(%LOCALAPPDATA%)에 .env 를 직접 만들 필요가 없게 한다.
    넥슨에 한 번 물어 통과한 키만 config.save_api_key 로 저장한다.
    """
    _running: set = set()  # 창이 먼저 닫혀도 확인 스레드가 끝날 때까지 참조를 쥔다

    def __init__(self, parent=None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"{config.APP_NAME} — API 키")
        v = QVBoxLayout(self)
        intro = QLabel(
            "넥슨 오픈API 키가 필요합니다. 키는 무료로 바로 발급됩니다.<br><br>"
            f"1. <a href='{KEY_ISSUE_URL}'>NEXON Open API</a> 에 로그인<br>"
            "2. 애플리케이션 등록 — <b>서비스 단계</b>로 등록 → 발급된 키 복사<br>"
            "3. 아래 칸에 붙여넣기<br><br>"
            "개발 단계 키는 하루 1,000건이라, 처음 검색할 때 받는 수천 경기를 끝내지 못합니다.")
        intro.setOpenExternalLinks(True)
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.ed_key = QLineEdit()
        self.ed_key.setPlaceholderText("live_… 또는 test_…")
        self.ed_key.returnPressed.connect(self._on_submit)
        v.addWidget(self.ed_key)
        self.lb_msg = QLabel(reason)
        self.lb_msg.setWordWrap(True)
        self.lb_msg.setStyleSheet(f"color: {T.RED};")
        v.addWidget(self.lb_msg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_ok = QPushButton("확인")
        self.btn_ok.clicked.connect(self._on_submit)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_ok)
        row.addWidget(btn_cancel)
        v.addLayout(row)
        fit_to_screen(self, 460, self.sizeHint().height())

    def _on_submit(self) -> None:
        key = self.ed_key.text().strip()
        if not key:
            self.lb_msg.setText("키를 입력하세요.")
            return
        self.btn_ok.setEnabled(False)
        self.ed_key.setEnabled(False)
        self.lb_msg.setText("넥슨에 확인하는 중…")
        worker = KeyCheckWorker(key)
        self._running.add(worker)
        worker.checked.connect(self._on_checked)
        worker.finished.connect(lambda w=worker: self._running.discard(w))
        worker.start()

    def _on_checked(self, key: str, reason: str) -> None:
        if not self.isVisible():
            return  # 확인 중에 창을 닫았다
        self.btn_ok.setEnabled(True)
        self.ed_key.setEnabled(True)
        if reason:
            self.lb_msg.setText(reason)
            return
        try:
            config.save_api_key(key)
        except OSError as e:
            self.lb_msg.setText(f"키를 저장하지 못했습니다: {e}")
            return
        self.accept()


def _notice_browser(html: str) -> QTextBrowser:
    tb = QTextBrowser()
    tb.setOpenExternalLinks(True)
    tb.setHtml(html)
    return tb


class NoticeDialog(QDialog):
    """첫 실행(또는 안내 글이 바뀐 뒤) 이용 안내 동의 + 넥슨 홈페이지 데이터 선택.

    웹 데이터는 **기본 체크 안 됨** — 예전 버전에서 켜져 있었어도 여기서 다시 고른다
    (고지 없이 켜진 채 남지 않게). 동의하지 않고 닫으면 앱을 켜지 않는다.
    """

    def __init__(self, parent=None, reask: bool = False):
        """reask: 이미 동의한 사람에게 바뀐 안내를 다시 묻는다(1.3.1 사용자 ⑤) — 홈페이지 데이터 체크를 **지금 값으로**
        채우고 바뀐 점을 맨 위에. 처음 동의(reask=False)는 지금처럼 빈 칸(D5)."""
        super().__init__(parent)
        self.reask = reask
        self.setWindowTitle(f"{config.APP_NAME} — 이용 안내" + (" (바뀐 점)" if reask else ""))
        v = QVBoxLayout(self)
        v.addWidget(_notice_browser((notice.CHANGES_HTML if reask else "")
                                    + notice.TERMS_HTML + notice.PRIVACY_HTML + notice.WEB_DATA_HTML
                                    + notice.RANK_COLLECT_HTML + notice.TRAY_HTML), 1)
        self.chk_web = QCheckBox(notice.WEB_DATA_CHECK)
        self.chk_web.setChecked(reask and config.WEB_DATA)
        self.chk_agree = QCheckBox(notice.AGREE_CHECK)
        web_row = QHBoxLayout()
        web_row.addWidget(self.chk_web)
        # D5: 처음 동의는 체크를 비워 두되(고지 없이 켜진 채 남지 않게), 켜 둔 사람이 [시작]만 눌러 모르고 끄지 않게 알린다.
        # 다시 묻기는 체크가 이미 지금 값이라 이 글이 필요 없다 — [시작]만 눌러도 홈페이지 데이터·수집이 그대로다.
        on_now = config.WEB_DATA and not reask
        self.lb_web_now = QLabel(notice.WEB_DATA_ON_NOW if on_now else "")
        self.lb_web_now.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.lb_web_now.setVisible(on_now)
        web_row.addWidget(self.lb_web_now)
        web_row.addStretch(1)
        v.addLayout(web_row)
        v.addWidget(self.chk_agree)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_ok = QPushButton("시작")
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._on_accept)
        self.chk_agree.toggled.connect(self.btn_ok.setEnabled)
        btn_cancel = QPushButton("닫기")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_ok)
        row.addWidget(btn_cancel)
        v.addLayout(row)
        fit_to_screen(self, 620, 640)

    def _on_accept(self) -> None:
        if not self.chk_agree.isChecked():
            return
        try:
            config.accept_notice(self.chk_web.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "저장 실패", f"설정을 저장하지 못했습니다: {e}")
            return
        self.accept()


class AboutDialog(QDialog):
    """[정보] — 버전·이용 안내·개인정보·라이선스, 넥슨 홈페이지 데이터 켜고 끄기."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{config.APP_NAME} — 정보")
        v = QVBoxLayout(self)
        head = QLabel(f"<b>{config.APP_NAME}</b> {config.APP_VERSION}<br>"
                      f"{notice.UNOFFICIAL}<br>"
                      f"<a href='{config.REPO_URL}'>{config.REPO_URL}</a>")
        head.setOpenExternalLinks(True)
        head.setWordWrap(True)
        v.addWidget(head)
        tabs = QTabWidget()
        tabs.addTab(_notice_browser(notice.TERMS_HTML), "이용 안내")
        tabs.addTab(_notice_browser(notice.PRIVACY_HTML), "개인정보")
        tabs.addTab(_notice_browser(notice.WEB_DATA_HTML), "넥슨 홈페이지 데이터")
        tabs.addTab(_notice_browser(notice.RANK_COLLECT_HTML), "랭킹 수집")
        tabs.addTab(_notice_browser(notice.TRAY_HTML), "트레이·자동 실행")
        tabs.addTab(_notice_browser(notice.licenses_html()), "오픈소스 라이선스")
        v.addWidget(tabs, 1)
        self.chk_web = QCheckBox(notice.WEB_DATA_CHECK)
        self.chk_web.setChecked(config.WEB_DATA)
        self.chk_web.toggled.connect(self._on_web_toggled)
        v.addWidget(self.chk_web)
        # 랭킹 수집 — 웹 데이터가 꺼져 있으면 잠긴다. 켜짐은 .env 와 rank.db 사본 둘 다 봐야 한다(D6 로 꺼졌을 수 있다)
        try:
            st = rankcollect.read_status()
        except Exception:
            st = {}
        rank_row = QHBoxLayout()
        self.chk_rank = QCheckBox(notice.RANK_COLLECT_CHECK)
        self.chk_rank.setChecked(config.WEB_DATA and config.RANK_COLLECT and st.get("enabled") != "0")
        self.chk_rank.setEnabled(config.WEB_DATA)
        self.chk_rank.toggled.connect(self._on_rank_toggled)
        rank_row.addWidget(self.chk_rank)
        rank_row.addStretch(1)
        self.btn_clear_rank = QPushButton("수집 기록 지우기")
        self.btn_clear_rank.clicked.connect(self._on_clear_rank)
        rank_row.addWidget(self.btn_clear_rank)
        v.addLayout(rank_row)
        self.lb_rank = QLabel(rank_status_text(st))
        self.lb_rank.setWordWrap(True)
        self.lb_rank.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_rank)
        # 자동 실행(D2) — 앱 토글 하나. exe 에서만(소스 실행은 python.exe 를 등록하게 된다)
        self.chk_auto = QCheckBox(notice.AUTOSTART_CHECK)
        avail = autostart.available()
        try:
            self.chk_auto.setChecked(avail and autostart.is_enabled())
        except OSError:
            pass
        self.chk_auto.setEnabled(avail)
        if not avail:
            self.chk_auto.setToolTip("설치판·포터블 실행 파일에서만 켤 수 있습니다")
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        v.addWidget(self.chk_auto)
        self.lb_msg = QLabel("")
        self.lb_msg.setWordWrap(True)
        self.lb_msg.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_msg)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("닫기")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        v.addLayout(row)
        # 폭은 탭 줄이 원하는 만큼 — 고정 620 이던 때 탭 6개(합 837)가 안 들어가 ◀ ▶ 로 넘겨야 했고
        # 마지막 탭은 아예 안 보였다(2026-10-05). 화면보다 넓으면 fit_to_screen 이 줄인다.
        m = v.contentsMargins()
        fit_to_screen(self, max(620, tabs.tabBar().sizeHint().width() + m.left() + m.right()), 600)

    def _sched(self):
        return getattr(self.parent(), "_rank_sched", None)

    def _on_web_toggled(self, on: bool) -> None:
        try:
            config.set_web_data(on)   # 끄면 수집도 끈다(.env)
        except OSError as e:
            self.lb_msg.setText(f"저장하지 못했습니다: {e}")
            return
        if not on:
            sched = self._sched()
            if sched is not None:
                sched.stop()  # 도는 회차가 남은 쪽을 '꺼짐' 오류로 세지 않게
            self.chk_rank.blockSignals(True)
            self.chk_rank.setChecked(False)
            self.chk_rank.blockSignals(False)
        self.chk_rank.setEnabled(on)
        self.lb_msg.setText("켰습니다 — 다음 조회부터 반영됩니다." if on else
                            "껐습니다 — 다음 조회부터 넥슨 홈페이지를 읽지 않습니다.")

    def _on_rank_toggled(self, on: bool) -> None:
        try:
            rankcollect.set_enabled_at(on, "" if on else "user")
        except (OSError, sqlite3.Error) as e:
            self.lb_msg.setText(f"저장하지 못했습니다: {e}")
            return
        sched = self._sched()
        if sched is not None:
            if on:
                sched.check()
            else:
                sched.stop()
        self.lb_msg.setText("랭킹 수집을 켰습니다 — 하루 한 번, 정각 뒤 몇십 분 사이에 읽습니다." if on else
                            "랭킹 수집을 껐습니다.")

    def _on_auto_toggled(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self.chk_auto.blockSignals(True)
            self.chk_auto.setChecked(not on)
            self.chk_auto.blockSignals(False)
            self.lb_msg.setText(f"자동 실행을 바꾸지 못했습니다: {e}")
            return
        self.lb_msg.setText("윈도우를 켜면 트레이로 조용히 시작합니다 — 창을 닫아도(X) 트레이에 남습니다." if on else
                            "자동 실행을 껐습니다.")

    def _ask_clear(self, keep_ouid: str | None) -> str | None:
        """→ "all" · "keep"(지금 계정 ELO 는 남김) · None(취소)."""
        box = QMessageBox(self)
        box.setWindowTitle("수집 기록 지우기")
        box.setText("랭킹 수집 기록(다른 구단주 1만 명분과 집계)과 검색한 구단주의 ELO 기록을 지웁니다.\n"
                    "랭킹 수집은 꺼집니다.")
        b_all = box.addButton("전부 지우기", QMessageBox.ButtonRole.DestructiveRole)
        b_keep = box.addButton("지금 계정 ELO 는 남기기", QMessageBox.ButtonRole.AcceptRole) if keep_ouid else None
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        hit = box.clickedButton()
        return "all" if hit is b_all else "keep" if (b_keep is not None and hit is b_keep) else None

    def _on_clear_rank(self) -> None:
        keep_ouid = getattr(self.parent(), "_ouid", None) or None
        choice = self._ask_clear(keep_ouid)
        if choice is None:
            return
        stop = getattr(self.parent(), "stop_predict_workers", None)
        if stop is not None:
            stop()      # 예측 작업자가 predictions 에 쓰는 중이면 지운 뒤에 한 줄이 되살아난다
        try:
            done, n = clear_rank_records(self._sched(), keep_ouid if choice == "keep" else None)
        except OSError as e:
            self.lb_msg.setText(f"설정을 저장하지 못해 지우지 않았습니다: {e}")
            return
        self.chk_rank.blockSignals(True)
        self.chk_rank.setChecked(False)
        self.chk_rank.blockSignals(False)
        self.lb_rank.setText(rank_status_text({}))
        elo = (f"ELO 기록 {n}줄을 지웠습니다." if n is not None
               else "ELO 기록은 지금 쓰는 중이라 다음에 켤 때 지웁니다.")
        self.lb_msg.setText(elo if done else
                            f"{elo} 랭킹 수집 기록은 다른 실행본이 열고 있어 다음에 켤 때 지웁니다.")


def rank_status_text(st: dict) -> str:
    """[정보] 창의 랭킹 수집 상태 한 줄(rankcollect.read_status)."""
    if not st or not st.get("snapshots"):
        parts = ["아직 수집한 기록이 없습니다."]
    else:
        when = (st.get("last_taken_at") or "")[:16].replace("T", " ")
        parts = [f"마지막 수집 {when} · {int(st.get('last_rows') or 0):,}명 · 보관 {st['snapshots']}회"]
    if st.get("enabled") == "0" and st.get("off_reason") == "blocked":
        parts.append("넥슨이 요청을 계속 막아 스스로 껐습니다")
    try:
        fails = int(st.get("fail_count") or 0)
    except ValueError:
        fails = 0
    if fails:
        retry = (st.get("retry_at") or "")[:16].replace("T", " ")
        parts.append(f"연속 실패 {fails}번 · 다음 시도 {retry} 이후")
    return " · ".join(parts)


def clear_rank_records(sched, keep_ouid: str | None) -> tuple[bool, int | None]:
    """수집 기록 지우기 — 수집을 끄고(.env 를 못 쓰면 OSError — 그러면 아무것도 안 지운다: 켜진 채 지우면 다음
    확인이 곧바로 다시 모은다) 멈춘 뒤 rank.db 를 지우고 fifa.db 의 ELO 기록·따라가기 목록을 지운다.
    → (rank.db 를 다 지웠나, ELO 줄 수 — 못 지웠으면 None: 표시를 남겨 다음에 켤 때 지운다)."""
    config.set_rank_collect(False)
    if sched is not None:
        sched.stop()      # 수집 스레드가 fifa.db 에도 쓴다(따라가기 — 계정마다 cancel 을 본다)
    done = rankcollect.delete_db()
    try:
        conn = store.open_db(config.DB_PATH)
        try:
            return done, store.clear_elo(conn, keep_ouid)
        finally:
            conn.close()
    except sqlite3.Error:
        store.mark_clear_elo(config.DB_PATH, keep_ouid)
        return done, None


_SHELL: tray.AppShell | None = None  # main 이 만든다 — 숨긴 창에서 난 오류는 모달 대신 트레이로


def _crash_modal(path) -> None:
    QMessageBox.warning(
        None, "예기치 못한 오류",
        "오류가 나서 기록을 남겼습니다. 앱은 계속 쓸 수 있지만, 화면이 이상하면 "
        "다시 켜 주세요.\n\n"
        f"오류 기록: {path}\n\n이 파일을 보내 주시면 원인을 찾을 수 있습니다. "
        "기록에 PC 의 폴더 경로(사용자 이름 포함)가 들어 있을 수 있으니, 보내기 전에 열어 확인하세요.")


def _notify_crash(path) -> None:
    """첫 오류 안내 — 창이 숨어 있거나 아직 없으면(트레이 상주) 게임 위에 모달을 띄우지 않는다:
    트레이 알림만 하고, 창을 열 때 안내한다(tray.AppShell.show_window)."""
    sh = _SHELL
    if sh is not None and not sh.window_visible():
        sh.crash_while_hidden(path)
        return
    _crash_modal(path)


def _setup_app(app: QApplication) -> None:
    """main() 의 창 띄우기 전 준비 — 테스트가 같은 경로를 부를 수 있게 떼어 뒀다."""
    crashlog.install(config.DATA_DIR / "logs", config.APP_VERSION, notify=_notify_crash)
    T.apply(app)
    icon_path = config.asset_path("app_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))


def _open_window(shell: tray.AppShell, show: bool = True):
    """안내 동의·키 → 창. 동의·키 창에서 취소하면 None. show=False 는 --tray(숨긴 채 — 미리 읽기도 안 한다)."""
    if config.notice_needed():
        if NoticeDialog().exec() != QDialog.DialogCode.Accepted:
            return None
    new_key = not config.API_KEY
    if new_key:
        if ApiKeyDialog().exec() != QDialog.DialogCode.Accepted:
            return None
    try:
        rankcollect.delete_pending()  # 지난번 [수집 기록 지우기]에서 다른 실행본이 열고 있어 못 지운 것
    except Exception:
        pass
    try:
        store.clear_elo_pending(config.DB_PATH)  # 같은 지우기에서 ELO 기록이 잠겨 못 지운 것
    except Exception:
        pass
    api = FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)
    win = MainWindow(api)
    shell.attach_window(win)
    if show:
        win.show_initial()  # 작은 화면이면 최대화로 — 띄운 뒤 실제 테두리로 한 번 더 확인
    win.start_update_check()
    win.start_cache_prune()
    if new_key:
        win.start_trades()  # 키를 막 넣었다 — 거래 기록(키 주인 것)을 받아 둔다(내 계정 힌트가 검색 직후 나오게)
    if show:
        QTimer.singleShot(0, win.ask_notice_update_once)  # 창이 그려진 뒤 그 위에(옛 동의자만)
        if config.OPEN_LAST_ACCOUNT:
            win.open_last_account()  # 마지막 계정을 DB 로 바로 — 새 경기는 뒤에서 조용히
        else:
            win.start_prefetch()  # 화면은 검색창 그대로, 마지막 계정의 저장된 경기만 뒤에서 읽어 둔다
    return win


def main(argv: list[str] | None = None) -> int:
    global _SHELL
    argv = sys.argv if argv is None else argv
    # instance() — 테스트가 이미 만든 앱으로 main 을 부를 수 있게(둘째 QApplication 은 예외)
    app = QApplication.instance() or QApplication(argv)
    if tray.QUIT_ARG in argv[1:]:
        # 제거기 — 떠 있는 실행본만 끝내고 이 실행은 아무것도 띄우지 않는다(창·크래시 기록·한 번만 실행 주인도 안 됨)
        tray.request_quit(tray.instance_name())
        return 0
    _setup_app(app)
    tray_mode = autostart.TRAY_ARG in argv[1:]
    single = None
    if config.SINGLE_INSTANCE:
        single = tray.SingleInstance(tray.instance_name())
        if not single.claim():
            return 0  # 이미 떠 있다 — 그 창을 앞으로 불렀다
    # 트레이에서 [열기] 를 눌렀는데 창이 아직 없으면(--tray 로 동의 전에 켬) 그때 안내·키 창부터
    shell = tray.AppShell(app, open_window=lambda: _open_window(shell),
                          sched=RankCollectScheduler(), single=single)
    if tray_mode:
        # 부팅 때 자동 실행 — 창·막는 창·미리 읽기 없이 트레이만. 동의·키가 필요하면 창도 만들지 않는다
        # (창을 만들면 시즌표를 넥슨 웹에 요청한다) — 사용자가 트레이에서 열 때 안내·키 창.
        if not config.notice_needed() and config.API_KEY:
            _open_window(shell, show=False)
    elif _open_window(shell) is None:
        return 0
    _SHELL = shell  # 여기서부터 숨긴 창의 오류는 트레이로(동의를 거절하고 끝난 실행은 껍데기를 남기지 않는다)
    shell.crash_modal = _crash_modal
    try:
        autostart.repair()  # 등록된 exe 가 없어졌으면(폴더를 옮김) 지금 exe 로
    except Exception:
        pass
    shell.start(tray_mode=tray_mode)
    rc = app.exec()
    # 창은 이미 닫혔고 설정도 저장했다. 남은 1만 경기 기록(객체 수백만 개)을 파이썬이 종료하며 하나씩 훑느라
    # 프로세스가 1.7초 더 살아 있었다 — 얼려서 건너뛴다(메모리는 OS 가 회수, 0.05초. 2026-10-04 실측).
    # os._exit 와 달리 atexit·로그 비우기는 그대로 돈다.
    gc.freeze()
    return rc


if __name__ == "__main__":
    sys.exit(main())
