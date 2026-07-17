"""피파 전적관리 — PyQt6 앱.

첫 화면은 검색창 하나. 구단주명을 넣으면 랭커 카드 + 분석 탭으로 전환된다.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QStackedWidget, QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

import config
import ranker
import scheduler
import stats as st
import store
import theme as T
from models import MatchSummary, parse_match, summarize
from nexon_api import FCOnlineAPI, NexonAPIError
from widgets import (
    NA, BarRow, NoScrollComboBox, RankerCard, SortableItem, StatCard,
    rate_of, wdl_text,
)

PAGE_SIZE = config.MAX_MATCH_LIMIT  # API 가 한 번에 주는 최대치(100)


class MatchLoader(QThread):
    """API 호출은 전부 여기서 — UI 스레드가 멈추지 않게."""

    progress = pyqtSignal(int, int, str)
    # [MatchSummary], [원본 detail], ouid, basic, spId→이름, 포지션코드→이름,
    # 새로 저장된 수, 이번에 API 로 받은 수, RankerInfo|None(넥슨 데이터센터 랭킹)
    finished_ok = pyqtSignal(list, list, str, dict, dict, dict, int, int, object)
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

            if not details:
                self.finished_ok.emit([], [], ouid, basic, {}, {}, 0, got, rank)
                return

            matches = [m for m in (parse_match(d, ouid) for d in details) if m]
            matches.sort(key=lambda m: m.match_date or 0, reverse=True)

            self.progress.emit(0, 0, "선수 정보 조회 중…")
            names = self._safe_meta("spid", "id", "name")
            positions = self._safe_meta("spposition", "spposition", "desc")

            self.finished_ok.emit(matches, details, ouid, basic, names,
                                  positions, new, got, rank)

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

    def _safe_rank(self):
        """랭킹(데이터센터 스크래핑)이 깨져도 전적은 보여준다."""
        if self._cancel:
            return None
        try:
            return ranker.fetch_manager_rank(self._nickname)
        except ranker.RankerError:
            return None


class MainWindow(QMainWindow):
    MATCH_COLUMNS = ["일시", "결과", "스코어", "상대", "점유율", "슈팅", "유효",
                     "패스성공률", "평점"]
    PLAYER_COLUMNS = ["포지션", "선수", "강화", "출전", "승률", "골", "어시",
                      "공격P", "슛", "유효슛", "패스%", "드리블%", "공중볼%",
                      "태클%", "블록%", "가로채기", "수비", "경고", "평점"]

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api
        self._loader: MatchLoader | None = None
        self._ouid = ""
        self._nick = ""
        self._basic: dict = {}
        self._rank = None   # ranker.RankerInfo | None — 넥슨 데이터센터 랭킹
        self._matches: list[MatchSummary] = []
        self._details: list[dict] = []
        self._names: dict = {}
        self._positions: dict = {}
        self._show_limit = PAGE_SIZE   # 화면에 몇 경기까지 보일지 — 100단위로 늘린다

        # 랭커/분석 두 페이지가 각각 갖는 상단 바 위젯들. 함께 갱신·잠금한다.
        self._nick_edits: list[QLineEdit] = []
        self._search_btns: list[QPushButton] = []
        self._acct_combos: list[NoScrollComboBox] = []
        self._auto_chks: list[QCheckBox] = []
        self._auto_lbs: list[QLabel] = []

        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.resize(1280, 720)
        self._build_ui()
        self._refresh_auto()

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

        title = QLabel("FC ONLINE")
        f = QFont()
        f.setPointSize(30)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {T.GREEN};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        sub = QLabel("감독모드 전적 분석")
        sub.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 14px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(sub)
        outer.addSpacing(24)

        row = QHBoxLayout()
        row.addStretch(1)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("구단주명을 입력해주세요.")
        self.ed_search.setFixedWidth(420)
        self.ed_search.setFixedHeight(42)
        self.ed_search.returnPressed.connect(self._on_search)
        btn = QPushButton("🔍")
        btn.setObjectName("primary")
        btn.setFixedSize(52, 42)
        btn.clicked.connect(self._on_search)
        row.addWidget(self.ed_search)
        row.addWidget(btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.lb_search_msg = QLabel("")
        self.lb_search_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_search_msg.setStyleSheet(f"color: {T.RED};")
        outer.addSpacing(10)
        outer.addWidget(self.lb_search_msg)
        outer.addStretch(2)
        return w

    def _top_bar(self) -> QHBoxLayout:
        """검색·등록·자동수집 — 랭커/분석 두 페이지가 공유하는 상단 바."""
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
        chk = QCheckBox(f"자동 수집 ({scheduler.DEFAULT_HOURS}시간마다)")
        chk.setToolTip("Windows 작업 스케줄러에 등록해 앱을 안 켜도 새 경기를 모읍니다.\n"
                       "PC가 켜져 있는 동안만 동작합니다.")
        chk.toggled.connect(self._on_toggle_auto)
        lb = QLabel("-")
        lb.setStyleSheet(f"color: {T.TEXT_DIM};")
        if not scheduler.is_supported():
            chk.setVisible(False)
            lb.setVisible(False)

        bar.addWidget(back)
        bar.addWidget(ed)
        bar.addWidget(btn)
        bar.addWidget(QLabel("등록"))
        bar.addWidget(cb)
        bar.addStretch(1)
        bar.addWidget(chk)
        bar.addWidget(lb)
        # 두 페이지가 각각 자기 위젯을 갖되, 조작은 리스트로 함께 처리한다.
        self._nick_edits.append(ed)
        self._search_btns.append(btn)
        self._acct_combos.append(cb)
        self._auto_chks.append(chk)
        self._auto_lbs.append(lb)
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
        self.lb_ranker_name = QLabel("-")
        nf = QFont()
        nf.setPointSize(18)
        nf.setBold(True)
        self.lb_ranker_name.setFont(nf)
        self.lb_ranker_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.lb_ranker_name)

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
        back = QPushButton("← 랭커 카드")
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

        # 표시 범위 — 100단위로 보고, 더 보기로 100씩 늘린다.
        rng = QGroupBox()
        rl = QHBoxLayout(rng)
        self.lb_total = QLabel("")
        self.lb_total.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.btn_less = QPushButton("처음 100경기")
        self.btn_less.clicked.connect(self._show_first)
        self.btn_more = QPushButton(f"{PAGE_SIZE}경기 더 보기")
        self.btn_more.setToolTip("DB에 쌓인 경기를 100씩 더 펼쳐 봅니다.")
        self.btn_more.clicked.connect(self._show_more)
        rl.addWidget(self.lb_total)
        rl.addStretch(1)
        rl.addWidget(self.btn_less)
        rl.addWidget(self.btn_more)
        outer.addWidget(rng)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_players_tab(), "선수 지표")
        self.tabs.addTab(self._build_tactics_tab(), "전술·경기 결과")
        self.tabs.addTab(self._make_table(self.MATCH_COLUMNS), "경기 목록")
        self.table = self.tabs.widget(2)
        outer.addWidget(self.tabs, 1)
        return w

    @staticmethod
    def _make_table(columns: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        return t

    def _build_players_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        note = QLabel("교체 명단이라도 기록이 있으면 출전으로 집계합니다. "
                      "헤더를 클릭하면 정렬됩니다.")
        note.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(note)
        self.tbl_players = self._make_table(self.PLAYER_COLUMNS)
        # 선수 이름은 내용에 맞춰 — 균등 분배면 긴 이름이 여러 줄로 접힌다.
        self.tbl_players.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
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
        hint = QLabel("전술은 수비-수미-미드-공미-공격 라인 인원으로 표기됩니다.")
        hint.setStyleSheet(f"color: {T.TEXT_DIM};")
        vf.addWidget(hint)

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

    # ── 자동 수집 ─────────────────────────────────────────────────────
    def _refresh_auto(self) -> None:
        if not scheduler.is_supported():
            return
        # 체크 상태는 앱이 아니라 실제 스케줄러가 정답 — 밖에서 지웠을 수 있다.
        on = scheduler.is_enabled()
        desc = scheduler.describe()
        for chk, lb in zip(self._auto_chks, self._auto_lbs):
            chk.blockSignals(True)
            chk.setChecked(on)
            chk.blockSignals(False)
            lb.setText(desc)

    def _on_toggle_auto(self, on: bool) -> None:
        try:
            scheduler.enable() if on else scheduler.disable()
        except scheduler.SchedulerError as e:
            QMessageBox.warning(self, "자동 수집",
                                f"{'등록' if on else '해제'}에 실패했습니다.\n\n{e}")
        self._refresh_auto()

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

    # 100단위 표시
    def _show_more(self) -> None:
        self._show_limit = min(self._show_limit + PAGE_SIZE, len(self._matches))
        self._render_all()

    def _show_first(self) -> None:
        self._show_limit = PAGE_SIZE
        self._render_all()

    def _set_busy(self, busy: bool) -> None:
        for w in (*self._search_btns, *self._nick_edits, self.ed_search):
            w.setEnabled(not busy)
        for b in (self.btn_more, self.btn_less, self.btn_analyze):
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
                   rank) -> None:
        self._set_busy(False)
        self._refresh_accounts()
        self._ouid = ouid
        self._basic = basic
        self._rank = rank
        self._nick = basic.get("nickname") or self._nick
        for ed in self._nick_edits:
            ed.setText(self._nick)
        if names:
            self._names, self._positions = names, positions
        self._matches, self._details = matches, details
        self._show_limit = min(PAGE_SIZE, len(matches)) or PAGE_SIZE

        # 검색 결과는 먼저 랭커 카드 페이지로.
        self.stack.setCurrentIndex(self.PAGE_RANKER)
        lv = (rank.level if rank and rank.level else basic.get("level", "-"))
        self.lb_ranker_name.setText(f"{self._nick}  Lv.{lv}")
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
        """최신순으로 _show_limit 경기까지. 100단위로 늘려 본다."""
        n = min(self._show_limit, len(self._matches)) or len(self._matches)
        shown = self._matches[:n]
        ids = {m.match_id for m in shown}
        return shown, [d for d in self._details if d.get("matchId") in ids]

    def _render_all(self) -> None:
        matches, details = self._slice()
        total = len(self._matches)
        self.lb_total.setText(f"전체 {total}경기 중 최근 {len(matches)}경기 표시")
        self.btn_more.setEnabled(len(matches) < total)
        self.btn_less.setEnabled(len(matches) > PAGE_SIZE)
        self.lb_profile.setText(self._nick)
        self.lb_sub.setText(f"Lv.{self._basic.get('level', '-')}  ·  "
                            f"감독모드 {len(matches)}경기 분석 (누적 {total})")
        s = summarize(matches)
        self.card_record.set(wdl_text(s.win, s.draw, s.lose))
        self.card_rate.set(f"{s.win_rate:.1f}%")
        self.card_gf.set(f"{s.avg_goals_for:.2f}")
        self.card_ga.set(f"{s.avg_goals_against:.2f}")
        self._render_ranker()
        self._render_matches(matches)
        self._render_players(details)
        self._render_tactics(details)

    def _render_ranker(self) -> None:
        """랭커 카드 — 넥슨 데이터센터의 감독모드 순위·구단가치·ELO·통산전적.

        데이터센터가 감독모드 통산(오픈API 의 최근 3천 경기보다 많다)을 주므로
        전적도 그 값을 우선 쓰고, 랭킹 밖이거나 조회 실패면 우리 집계로 대체한다.
        """
        c = self.card_ranker
        r = self._rank

        if r and r.ranked:
            c.set("순위", f"{r.rank:,}위", T.GREEN)
            c.set("전적", f"{r.record_text} ({r.win_rate})")
            c.set("구단가치", r.team_value_text or NA)
            c.set("점수", f"{r.elo:g}" if r.elo is not None else NA)
            c.note.setText("* 넥슨 데이터센터 · 감독모드 통산 · 매시각 갱신")
            c.setToolTip("순위·구단가치·점수·통산전적은 넥슨 공식 데이터센터에서\n"
                         "가져옵니다(감독모드 랭킹, 매시각 갱신).")
        else:
            # 랭킹 밖(공식경기 미달 등)이거나 조회 실패 — 우리 집계로 채운다.
            full = summarize(self._matches)
            c.set("전적",
                  f"{wdl_text(full.win, full.draw, full.lose)} ({full.win_rate:.1f}%)")
            reason = "랭킹 밖" if r is not None else "조회 실패"
            for row in ("순위", "구단가치", "점수"):
                c.set(row, reason, T.TEXT_DIM)
            last = self._matches[0].date_text if self._matches else "-"
            c.note.setText(f"* 최근 {len(self._matches)}경기 기준 · {last}")
            c.setToolTip("넥슨 데이터센터 감독모드 랭킹 1만 위 밖이거나\n"
                         "랭킹 조회에 실패했습니다. 전적은 앱이 받은 경기 기준입니다.")

    @staticmethod
    def _cell(text: str, key=None):
        item = SortableItem(text, key)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _fill(self, table: QTableWidget, rows: list[list]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                text, key = cell if isinstance(cell, tuple) else (cell, None)
                table.setItem(r, c, self._cell(text, key))
        table.setSortingEnabled(True)

    def _render_matches(self, matches: list[MatchSummary]) -> None:
        rows = []
        for m in matches:
            rows.append([
                m.date_text, m.result, m.score, m.opponent,
                (f"{m.possession}%", m.possession), (f"{m.shoot_total}", m.shoot_total),
                (f"{m.shoot_effective}", m.shoot_effective),
                (f"{m.pass_rate:.0f}%", m.pass_rate), (f"{m.rating:.2f}", m.rating),
            ])
        self._fill(self.table, rows)
        for r, m in enumerate(matches):
            item = self.table.item(r, 1)
            if not item:
                continue
            for key, col in (("승", T.WIN), ("무", T.DRAW), ("패", T.LOSE)):
                if key in m.result:
                    item.setForeground(QColor(col))
                    break

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
                (f"{p.goal}", p.goal), (f"{p.assist}", p.assist),
                (f"{p.attack_point}", p.attack_point),
                (f"{p.shoot}", p.shoot), (f"{p.effective_shoot}", p.effective_shoot),
                (f"{p.pass_rate:.1f}", p.pass_rate),
                (f"{p.dribble_rate:.1f}", p.dribble_rate),
                (f"{p.aerial_rate:.1f}", p.aerial_rate),
                (f"{p.tackle_rate:.1f}", p.tackle_rate),
                (f"{p.block_rate:.1f}", p.block_rate),
                (f"{p.intercept}", p.intercept), (f"{p.defending}", p.defending),
                (f"{p.yellow}", p.yellow), (f"{p.rating:.2f}", p.rating),
            ])
        self._fill(self.tbl_players, rows)

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
        super().closeEvent(e)


def main() -> int:
    # exe 로 묶이면 옆에 collect.py 가 없다. 작업 스케줄러는 exe 자신을
    # --collect 로 부르고, 그때는 창 없이 수집만 하고 끝낸다.
    if "--collect" in sys.argv[1:]:
        import collect
        return collect.main([a for a in sys.argv[1:] if a != "--collect"])

    app = QApplication(sys.argv)
    app.setStyleSheet(T.QSS)
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
