"""피파 전적관리 — PyQt6 앱.

첫 화면은 검색창 하나. 구단주명을 넣으면 랭커 카드 + 분석 탭으로 전환된다.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QStackedWidget, QTableWidget, QTabWidget,
    QVBoxLayout, QWidget,
)

import config
import images
import ranker
import stats as st
import store
import theme as T
from models import (
    MatchSummary, opponent_stats, parse_match, summarize, win_rate_trend,
)
from nexon_api import FCOnlineAPI, NexonAPIError
from widgets import (
    NA, BarRow, FitTableWidget, NoScrollComboBox, PitchWidget, RankerCard,
    RowBorderDelegate, SortableItem, StatCard, TrendChart, rate_of, wdl_text,
)

PAGE_SIZE = config.MAX_MATCH_LIMIT  # API 가 한 번에 주는 최대치(100)


class MatchLoader(QThread):
    """API 호출은 전부 여기서 — UI 스레드가 멈추지 않게."""

    progress = pyqtSignal(int, int, str)
    # [MatchSummary], [원본 detail], ouid, basic, spId→이름, 포지션코드→이름,
    # 새로 저장된 수, 이번에 API 로 받은 수, RankerInfo|None(넥슨 데이터센터 랭킹),
    # 등급이름(감독모드 최고 등급), is_champion(챔피언스 이상인지), 등급 배지 로컬 경로,
    # seasonId→{className,seasonImg}
    finished_ok = pyqtSignal(list, list, str, dict, dict, dict, int, int, object,
                             str, bool, str, dict)
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
                    except RuntimeError:
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
            grade_name, is_champion, badge_path = self._current_grade(details, ouid)

            if not details:
                self.finished_ok.emit([], [], ouid, basic, {}, {}, 0, got,
                                      rank, grade_name, is_champion, badge_path, {})
                return

            matches = [m for m in (parse_match(d, ouid) for d in details) if m]
            matches.sort(key=lambda m: m.match_date or 0, reverse=True)

            self.progress.emit(0, 0, "선수 정보 조회 중…")
            names = self._safe_meta("spid", "id", "name")
            positions = self._safe_meta("spposition", "spposition", "desc")
            seasons = self._safe_meta_raw("seasonid", "seasonId")

            self.finished_ok.emit(matches, details, ouid, basic, names,
                                  positions, new, got, rank, grade_name,
                                  is_champion, badge_path, seasons)

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
                       ouid: str) -> tuple[str, bool, str]:
        """'지금' 등급 이름·챔피언스 이상 여부·등급 배지 아이콘 로컬 경로.

        오픈API user/maxdivision 은 '역대 최고' 등급이라 지금 등급과 다를 수
        있다(예: 예전에 슈퍼챔피언스를 찍었지만 지금은 챔피언스로 내려온 경우).
        대신 매치 상세에 그 경기 당시의 division 필드가 있으므로, 이미 받아
        둔 경기 중 가장 최근 것(details[0], store.load_details 가 최신순으로
        준다)의 값을 쓴다 — 우리가 실제로 확인한 최신 상태에 가장 가깝다.
        """
        if not details:
            return "-", False, ""
        me = next((p for p in details[0].get("matchInfo") or []
                  if p.get("ouid") == ouid), None)
        div_id = me.get("division") if me else None
        if div_id is None:
            return "-", False, ""
        raw = []
        try:
            raw = self._api.get_meta("division")
        except NexonAPIError:
            pass
        names = {d.get("divisionId"): d.get("divisionName") for d in raw
                if "divisionId" in d and "divisionName" in d}
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
        return grade_name, st.is_champion_or_above(div_id), badge_path

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


class MainWindow(QMainWindow):
    MATCH_COLUMNS = ["일시", "결과", "스코어", "상대", "점유율", "슈팅", "유효",
                     "패스성공률", "평점"]
    PLAYER_COLUMNS = ["포지션", "선수", "강화", "출전", "승률",
                      "공격력", "수비력", "기대득점률", "공격P", "골", "어시",
                      "패스%", "드리블%", "공중볼%", "가로채기", "태클%",
                      "블록%", "선방력", "평점"]
    OPPONENT_COLUMNS = ["상대", "전적", "승률", "평균득점", "평균실점", "최근 경기"]

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api
        self._loader: MatchLoader | None = None
        self._img_loader: ImageLoader | None = None
        self._img_cache_dir = config.CACHE_DIR / "player_images"
        self._ouid = ""
        self._nick = ""
        self._basic: dict = {}
        self._rank = None   # ranker.RankerInfo | None — 넥슨 데이터센터 랭킹
        self._grade_name = "-"     # 감독모드 최고 등급 이름 (division 메타)
        self._is_champion = False  # 감독모드 최고 등급 챔피언스 이상 — 랭커 카드 표시 여부
        self._badge_path = ""      # 등급 배지 아이콘 로컬 캐시 경로
        self._seasons: dict = {}   # seasonId -> {className, seasonImg} (get_meta("seasonid"))
        self._season_icon_dir = config.CACHE_DIR / "season_icons"
        self._matches: list[MatchSummary] = []
        self._details: list[dict] = []
        self._names: dict = {}
        self._positions: dict = {}

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
        for c in (self.card_record, self.card_rate, self.card_gf, self.card_ga):
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
        self.btn_apply.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {T.GREEN};"
            f" border: 1px solid {T.GREEN}; border-radius: 6px; padding: 6px 16px;"
            f" font-weight: bold; }}"
            f"QPushButton:hover {{ background: rgba(63,185,80,0.12); }}")
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
        self.tabs.addTab(self._make_table(self.MATCH_COLUMNS), "경기 목록")
        self.table = self.tabs.widget(2)
        self.table.itemDoubleClicked.connect(self._on_match_double_clicked)
        self.tbl_opponents = self._make_table(self.OPPONENT_COLUMNS)
        self.tbl_opponents.itemDoubleClicked.connect(self._on_opponent_double_clicked)
        self.tabs.addTab(self.tbl_opponents, "상대 전적")
        self.TAB_TREND = self.tabs.addTab(self._build_trend_tab(), "승률 그래프")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self.tabs, 1)
        return w

    def _build_trend_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        gb = QGroupBox("최근 30일 승률 추이")
        gv = QVBoxLayout(gb)
        self.trend_chart = TrendChart([])
        gv.addWidget(self.trend_chart)
        v.addWidget(gb, 1)
        return w

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
            f"QTableWidget::item {{ padding: 10px 10px; margin: 0px; }}"
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
        # Interactive 로 두고 _render_players 에서 데이터가 채워진 뒤
        # _fit_columns_to_content 가 "헤더 글자 폭·값 글자 폭 중 큰 쪽" 기준으로
        # 직접 너비를 잡는다 — 헤더도 값도 안 잘리면서, ResizeToContents 가
        # 남기던 것 같은 불필요한 여백은 안 남긴다.
        for c in range(len(self.PLAYER_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self.tbl_players.setIconSize(QSize(18, 18))
        v.addWidget(self.tbl_players, 1)
        return w

    def _build_tactics_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)

        gb_f = QGroupBox("전술 분석")
        vf = QVBoxLayout(gb_f)

        self.lb_my_formation = QLabel("-")
        mf = QFont()
        mf.setPointSize(16)
        mf.setBold(True)
        self.lb_my_formation.setFont(mf)
        self.lb_my_formation.setStyleSheet(
            f"background: #12261a; border: 1px solid {T.BORDER};"
            f" border-radius: 6px; padding: 10px;")
        vf.addWidget(self.lb_my_formation)

        self.box_opp = QVBoxLayout()
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
                   badge_path: str, seasons: dict) -> None:
        self._set_busy(False)
        self._refresh_accounts()
        self._refresh_recent()
        self._ouid = ouid
        self._basic = basic
        self._rank = rank
        self._grade_name = grade_name
        self._is_champion = is_champion
        self._badge_path = badge_path
        if seasons:
            self._seasons = seasons
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
        # 승률 추이는 "최근 30일" 이 표시 구간(시작~끝, 최근 최대 100경기)에
        # 갇히면 안 된다 — 하루에 100경기 넘게 뛰는 계정은 그 구간이 하루도
        # 안 될 수 있어서, 누적 전체(self._matches)에서 30일을 계산한다.
        self._render_trend(self._matches)

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

        # 교체 명단(SUB)은 안 보여준다 — 선발만.
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

    def _render_trend(self, matches: list[MatchSummary]) -> None:
        self._trend_periods = win_rate_trend(matches)
        self.trend_chart.set_points(
            [(p.label, p.win_rate, p.games) for p in self._trend_periods])
        if self.tabs.currentIndex() == self.TAB_TREND:
            self._show_trend_summary()

    def _on_tab_changed(self, index: int) -> None:
        if index == self.TAB_TREND:
            self._show_trend_summary()
        else:
            self._show_range_summary()

    def _show_trend_summary(self) -> None:
        """승률 그래프 탭에서는 상단 카드를 최근 30일 최고·평균·최저 승률로."""
        periods = getattr(self, "_trend_periods", [])
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
        self.card_ga.set_title("30일 경기수")
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
        self._load_player_images([p.sp_id for p in players])

    def _load_player_images(self, sp_ids: list[int]) -> None:
        """선수 얼굴 이미지를 백그라운드로 받아서 도착하는 대로 표에 채운다."""
        if self._img_loader and self._img_loader.isRunning():
            self._img_loader.cancel()
            self._img_loader.wait(500)
        self._img_loader = ImageLoader(sp_ids, self._img_cache_dir)
        self._img_loader.loaded.connect(self._on_player_image)
        self._img_loader.start()

    def _on_player_image(self, sp_id: int, path: str) -> None:
        icon = QIcon(QPixmap(path))
        for r in range(self.tbl_players.rowCount()):
            item = self.tbl_players.item(r, 1)
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
            h.setContentsMargins(4, 1, 4, 1)
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
        sep.setStyleSheet(f"color: {T.GREEN}; font-weight: bold; padding-top: 6px;")
        self.box_result.addWidget(sep)
        for k in sorted(rb.periods):
            v = rb.periods[k]
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 1, 4, 1)
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
        if self._img_loader and self._img_loader.isRunning():
            self._img_loader.cancel()
            if not self._img_loader.wait(3000):
                self._img_loader.terminate()
                self._img_loader.wait(1000)
        super().closeEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(T.QSS)
    icon_path = config.ROOT / "app_icon.ico"
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
