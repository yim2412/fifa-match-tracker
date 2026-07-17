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
    QScrollArea, QSpinBox, QStackedWidget, QTableWidget, QTabWidget,
    QVBoxLayout, QWidget,
)

import config
import scheduler
import stats as st
import store
import theme as T
from models import MatchSummary, Stats, parse_match, summarize
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
    # 새로 저장된 수, 이번에 API 로 받은 수
    finished_ok = pyqtSignal(list, list, str, dict, dict, dict, int, int)
    failed = pyqtSignal(str)

    def __init__(self, api: FCOnlineAPI, nickname: str, match_type: int,
                 offset: int = 0, limit: int = PAGE_SIZE):
        super().__init__()
        self._api = api
        self._nickname = nickname
        self._match_type = match_type
        self._offset = offset
        self._limit = limit
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.progress.emit(0, self._limit, f"'{self._nickname}' 계정 조회 중…")
            ouid = self._api.get_ouid(self._nickname)
            basic = self._api.get_user_basic(ouid)

            conn = store.open_db(config.DB_PATH)  # DB 는 스레드마다 따로 연다
            try:
                store.upsert_account(conn, ouid, basic.get("nickname") or self._nickname)

                self.progress.emit(0, self._limit, "매치 목록 조회 중…")
                ids = self._api.get_match_ids(ouid, self._match_type,
                                              self._offset, self._limit)
                got = len(ids)

                have = store.existing_ids(conn, ids)
                todo = [i for i in ids if i not in have]

                fresh: list[dict] = []
                done = 0
                if todo:
                    with ThreadPoolExecutor(max_workers=6) as pool:
                        for detail in pool.map(self._safe_detail, todo):
                            if self._cancel:
                                return
                            done += 1
                            self.progress.emit(done, len(todo),
                                               f"새 경기 {done}/{len(todo)}")
                            if detail is not None:
                                fresh.append(detail)
                new = store.save_matches(conn, fresh)

                self.progress.emit(done, max(len(todo), 1), "저장된 전적 불러오는 중…")
                details = store.load_details(conn, ouid, self._match_type)
            finally:
                conn.close()

            if not details:
                self.finished_ok.emit([], [], ouid, basic, {}, {}, 0, got)
                return

            matches = [m for m in (parse_match(d, ouid) for d in details) if m]
            matches.sort(key=lambda m: m.match_date or 0, reverse=True)

            self.progress.emit(done, max(len(todo), 1), "선수 정보 조회 중…")
            names = self._safe_meta("spid", "id", "name")
            positions = self._safe_meta("spposition", "spposition", "desc")

            self.finished_ok.emit(matches, details, ouid, basic, names,
                                  positions, new, got)

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
        self._api_loaded = 0      # 이번 계정에서 API 로 받아온 경기 수(offset 용)
        self._matches: list[MatchSummary] = []
        self._details: list[dict] = []
        self._names: dict = {}
        self._positions: dict = {}

        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.resize(1280, 720)
        self._build_ui()
        self._refresh_auto()

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_search_page())
        self.stack.addWidget(self._build_result_page())
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

    def _build_result_page(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setSpacing(10)

        # 상단 바 — 뒤로/재검색 + 자동 수집
        bar = QHBoxLayout()
        back = QPushButton("← 검색")
        back.clicked.connect(self._go_search)
        self.ed_nick = QLineEdit()
        self.ed_nick.setPlaceholderText("구단주명")
        self.ed_nick.setMaximumWidth(220)
        self.ed_nick.returnPressed.connect(self._on_search)
        self.btn_search = QPushButton("조회")
        self.btn_search.setObjectName("primary")
        self.btn_search.clicked.connect(self._on_search)
        self.cb_accounts = NoScrollComboBox()
        self.cb_accounts.setMinimumWidth(170)
        self.cb_accounts.activated.connect(self._on_pick_account)

        bar.addWidget(back)
        bar.addWidget(self.ed_nick)
        bar.addWidget(self.btn_search)
        bar.addWidget(QLabel("등록"))
        bar.addWidget(self.cb_accounts)
        bar.addStretch(1)
        self.chk_auto = QCheckBox(f"자동 수집 ({scheduler.DEFAULT_HOURS}시간마다)")
        self.chk_auto.setToolTip(
            "Windows 작업 스케줄러에 등록해 앱을 안 켜도 새 경기를 모읍니다.\n"
            "PC가 켜져 있는 동안만 동작합니다.")
        self.chk_auto.toggled.connect(self._on_toggle_auto)
        self.lb_auto = QLabel("-")
        self.lb_auto.setStyleSheet(f"color: {T.TEXT_DIM};")
        bar.addWidget(self.chk_auto)
        bar.addWidget(self.lb_auto)
        if not scheduler.is_supported():
            self.chk_auto.setVisible(False)
            self.lb_auto.setVisible(False)
        outer.addLayout(bar)

        # 프로필 + 랭커 카드 + 요약 카드
        head = QHBoxLayout()
        self.card_ranker = RankerCard()
        head.addWidget(self.card_ranker)

        right = QVBoxLayout()
        self.lb_profile = QLabel("-")
        pf = QFont()
        pf.setPointSize(17)
        pf.setBold(True)
        self.lb_profile.setFont(pf)
        self.lb_sub = QLabel("-")
        self.lb_sub.setStyleSheet(f"color: {T.TEXT_DIM};")
        right.addWidget(self.lb_profile)
        right.addWidget(self.lb_sub)
        right.addSpacing(6)

        cards = QHBoxLayout()
        self.card_record = StatCard("전적")
        self.card_rate = StatCard("승률", T.GREEN)
        self.card_gf = StatCard("평균 득점", T.GREEN)
        self.card_ga = StatCard("평균 실점", T.RED)
        for c in (self.card_record, self.card_rate, self.card_gf, self.card_ga):
            cards.addWidget(c)
        right.addLayout(cards)
        right.addStretch(1)
        head.addLayout(right, 1)
        outer.addLayout(head)

        # 범위 + 더 불러오기
        rng = QGroupBox()
        rl = QHBoxLayout(rng)
        self.sp_from = QSpinBox()
        self.sp_to = QSpinBox()
        for s in (self.sp_from, self.sp_to):
            s.setRange(1, 99999)
            s.setMaximumWidth(90)
        self.btn_apply = QPushButton("적용")
        self.btn_apply.setObjectName("primary")
        self.btn_apply.clicked.connect(self._render_all)
        self.lb_total = QLabel("")
        self.lb_total.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.btn_more = QPushButton(f"{PAGE_SIZE}경기 더 불러오기")
        self.btn_more.setToolTip(
            "API 의 offset 으로 더 과거 경기를 받아 DB 에 쌓습니다.\n"
            "받아온 경기는 다음부터 다시 받지 않습니다.")
        self.btn_more.clicked.connect(self._on_load_more)

        rl.addWidget(QLabel("시작"))
        rl.addWidget(self.sp_from)
        rl.addWidget(QLabel("~  끝"))
        rl.addWidget(self.sp_to)
        rl.addWidget(self.btn_apply)
        rl.addWidget(self.lb_total)
        rl.addStretch(1)
        rl.addWidget(self.btn_more)
        outer.addWidget(rng)

        # 탭
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
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(scheduler.is_enabled())
        self.chk_auto.blockSignals(False)
        self.lb_auto.setText(scheduler.describe())

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

        self.cb_accounts.blockSignals(True)
        self.cb_accounts.clear()
        self.cb_accounts.addItem("— 선택 —", None)
        for r in rows:
            nick = r["nickname"] or r["ouid"][:8]
            self.cb_accounts.addItem(f"{nick} ({counts.get(r['ouid'], 0)})",
                                     nick)
        self.cb_accounts.blockSignals(False)

    def _on_pick_account(self, index: int) -> None:
        nick = self.cb_accounts.itemData(index)
        if nick:
            self.ed_nick.setText(nick)
            self._on_search()

    # ── 조회 ──────────────────────────────────────────────────────────
    def _go_search(self) -> None:
        self.stack.setCurrentIndex(0)
        self.ed_search.setFocus()
        self.ed_search.selectAll()

    def _on_search(self) -> None:
        src = self.ed_search if self.stack.currentIndex() == 0 else self.ed_nick
        nick = src.text().strip()
        if not nick:
            self.lb_search_msg.setText("구단주명을 입력해주세요.")
            return
        if self._loader and self._loader.isRunning():
            return
        self.lb_search_msg.setText("")
        self._nick = nick
        self._api_loaded = 0
        self._start_loader(offset=0)

    def _on_load_more(self) -> None:
        if self._loader and self._loader.isRunning():
            return
        if not self._nick:
            return
        self._start_loader(offset=self._api_loaded)

    def _start_loader(self, offset: int) -> None:
        self._set_busy(True)
        self._loader = MatchLoader(self._api, self._nick,
                                   config.DEFAULT_MATCH_TYPE, offset, PAGE_SIZE)
        self._loader.progress.connect(self._on_progress)
        self._loader.finished_ok.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def _set_busy(self, busy: bool) -> None:
        for w in (self.btn_search, self.ed_nick, self.ed_search,
                  self.btn_more, self.btn_apply):
            w.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setValue(0)

    def _on_progress(self, done: int, total: int, msg: str) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.statusBar().showMessage(msg)

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.statusBar().showMessage("조회 실패")
        if self.stack.currentIndex() == 0:
            self.lb_search_msg.setText(msg)
        else:
            QMessageBox.warning(self, "조회 실패", msg)

    def _on_loaded(self, matches: list, details: list, ouid: str, basic: dict,
                   names: dict, positions: dict, new: int, got: int) -> None:
        self._set_busy(False)
        self._refresh_accounts()
        self._ouid = ouid
        self._api_loaded += got
        self._nick = basic.get("nickname") or self._nick
        self.ed_nick.setText(self._nick)
        if names:
            self._names, self._positions = names, positions
        self._matches, self._details = matches, details

        self.stack.setCurrentIndex(1)
        if not matches:
            self.statusBar().showMessage(f"{self._nick} — 감독모드 기록이 없습니다.")
            self.lb_profile.setText(self._nick)
            self.lb_sub.setText("감독모드 기록 없음")
            return

        self.sp_from.setMaximum(len(matches))
        self.sp_to.setMaximum(len(matches))
        self.sp_from.setValue(1)
        self.sp_to.setValue(len(matches))
        self.lb_profile.setText(f"{self._nick}")
        self.lb_sub.setText(f"Lv.{basic.get('level', '-')}  ·  "
                            f"감독모드 {len(matches)}경기 분석")
        self._render_all()
        self.statusBar().showMessage(
            f"누적 {len(matches)}경기" + (f" · 새 경기 {new}건 저장" if new else "")
            + f" · API 로 {self._api_loaded}경기까지 받음")

    # ── 렌더 ──────────────────────────────────────────────────────────
    def _slice(self) -> tuple[list[MatchSummary], list[dict]]:
        """시작~끝 범위만. 표시 순서는 최신순이다."""
        a = max(self.sp_from.value() - 1, 0)
        b = min(self.sp_to.value(), len(self._matches))
        if a >= b:
            a, b = 0, len(self._matches)
        ids = {m.match_id for m in self._matches[a:b]}
        return (self._matches[a:b],
                [d for d in self._details if d.get("matchId") in ids])

    def _render_all(self) -> None:
        matches, details = self._slice()
        self.lb_total.setText(f"전체 {len(self._matches)}경기 중 {len(matches)}경기")
        s = summarize(matches)
        self.card_record.set(wdl_text(s.win, s.draw, s.lose))
        self.card_rate.set(f"{s.win_rate:.1f}%")
        self.card_gf.set(f"{s.avg_goals_for:.2f}")
        self.card_ga.set(f"{s.avg_goals_against:.2f}")
        self._render_ranker(s, len(matches))
        self._render_matches(matches)
        self._render_players(details)
        self._render_tactics(details)

    def _render_ranker(self, s: Stats, n: int) -> None:
        c = self.card_ranker
        c.set("전적", f"{wdl_text(s.win, s.draw, s.lose)} ({s.win_rate:.1f}%)")
        # 넥슨 API 가 안 주는 값 — 그럴싸한 숫자를 지어 넣지 않고, 왜 비었는지
        # 화면에서 바로 보이게 적는다.
        for row in ("순위", "구단가치", "점수"):
            c.set(row, "API 미제공", T.TEXT_DIM)
        last = self._matches[0].date_text if self._matches else "-"
        c.note.setText(f"* {n}경기 기준 · {last} 업데이트")
        c.setToolTip("순위·구단가치·점수는 넥슨 오픈API가 제공하지 않습니다.\n"
                     "지어낸 값을 넣지 않고 비워 둡니다.")

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
            self._loader.wait(3000)
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
