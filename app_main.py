"""피파 전적관리 — PyQt6 런처.

닉네임을 넣으면 넥슨 오픈API로 최근 전적을 받아 표와 요약으로 보여준다.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

import config
import scheduler
import stats as st
import store
from models import MatchSummary, Stats, parse_match, summarize
from nexon_api import FCOnlineAPI, NexonAPIError

RESULT_COLORS = {"승": QColor("#1b5e20"), "무": QColor("#4e4e4e"), "패": QColor("#8e1616")}


class NoScrollComboBox(QComboBox):
    """휠 스크롤로 값이 바뀌는 사고 방지."""

    def wheelEvent(self, e):
        e.ignore()


class SortableItem(QTableWidgetItem):
    """숫자 열을 문자열로 정렬하면 '10'이 '9'보다 앞에 온다.

    Qt 기본 정렬은 DisplayRole 문자열 비교라, 표시용 문자열("46.0%")과
    정렬용 값(46.0)을 분리해서 비교를 직접 한다.
    """

    def __init__(self, text: str, sort_key=None):
        super().__init__(text)
        self._key = sort_key

    def __lt__(self, other):
        if isinstance(other, SortableItem) and self._key is not None \
                and other._key is not None:
            return self._key < other._key
        return super().__lt__(other)


class MatchLoader(QThread):
    """API 호출은 전부 여기서 — UI 스레드가 멈추지 않게."""

    progress = pyqtSignal(int, int, str)          # 완료 수, 전체, 메시지
    # [MatchSummary], [원본 detail], ouid, user_basic, spId→이름, 포지션코드→이름,
    # 이번 조회로 새로 저장된 경기 수
    finished_ok = pyqtSignal(list, list, str, dict, dict, dict, int)
    failed = pyqtSignal(str)

    def __init__(self, api: FCOnlineAPI, nickname: str, match_type: int, limit: int):
        super().__init__()
        self._api = api
        self._nickname = nickname
        self._match_type = match_type
        self._limit = limit
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.progress.emit(0, self._limit, f"'{self._nickname}' 계정 조회 중…")
            ouid = self._api.get_ouid(self._nickname)
            basic = self._api.get_user_basic(ouid)

            # DB 는 스레드마다 따로 연다 — 커넥션은 스레드 간 공유하면 안 된다.
            conn = store.open_db(config.DB_PATH)
            try:
                store.upsert_account(conn, ouid, basic.get("nickname") or self._nickname)

                self.progress.emit(0, self._limit, "매치 목록 조회 중…")
                ids = self._api.get_match_ids(ouid, self._match_type, 0, self._limit)

                # 이미 쌓아 둔 경기는 API 를 다시 부르지 않는다.
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

                # 화면에 그리는 건 API 100경기가 아니라 DB 에 쌓인 전부.
                self.progress.emit(done, max(len(todo), 1), "저장된 전적 불러오는 중…")
                details = store.load_details(conn, ouid, self._match_type)
            finally:
                conn.close()

            if not details:
                self.finished_ok.emit([], [], ouid, basic, {}, {}, 0)
                return

            matches = [m for m in (parse_match(d, ouid) for d in details) if m]
            matches.sort(key=lambda m: m.match_date or 0, reverse=True)

            # 선수 이름 메타는 8만 건이라 여기(워커)서 받는다. 실패해도 조회는 살린다.
            self.progress.emit(done, max(len(todo), 1), "선수 정보 조회 중…")
            names = self._safe_meta("spid", "id", "name")
            positions = self._safe_meta("spposition", "spposition", "desc")

            self.finished_ok.emit(matches, details, ouid, basic, names, positions, new)

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
        """메타를 못 받아도 전적 자체는 보여준다 — 이름 대신 코드가 뜰 뿐."""
        try:
            return {m[key]: m[val] for m in self._api.get_meta(name)
                    if key in m and val in m}
        except Exception:
            return {}


class MainWindow(QMainWindow):
    COLUMNS = ["일시", "결과", "스코어", "상대", "점유율", "슈팅", "유효", "패스성공률", "평점"]

    def __init__(self, api: FCOnlineAPI):
        super().__init__()
        self._api = api
        self._loader: MatchLoader | None = None
        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.resize(1000, 680)
        self._build_ui()
        self._load_match_types()
        self._refresh_accounts()
        self._refresh_auto()

    # ── UI 구성 ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)

        # 검색 바
        bar = QHBoxLayout()
        self.ed_nick = QLineEdit()
        self.ed_nick.setPlaceholderText("구단주명(닉네임)을 입력하고 Enter")
        self.ed_nick.returnPressed.connect(self._on_search)
        self.cb_type = NoScrollComboBox()
        self.sp_limit = QSpinBox()
        self.sp_limit.setRange(1, config.MAX_MATCH_LIMIT)
        self.sp_limit.setValue(config.DEFAULT_MATCH_LIMIT)
        self.sp_limit.setSuffix(" 경기")
        self.btn_search = QPushButton("조회")
        self.btn_search.clicked.connect(self._on_search)

        bar.addWidget(QLabel("닉네임"))
        bar.addWidget(self.ed_nick, 3)
        bar.addWidget(QLabel("매치"))
        bar.addWidget(self.cb_type, 1)
        bar.addWidget(self.sp_limit)
        bar.addWidget(self.btn_search)
        outer.addLayout(bar)

        # 등록 계정 — 조회하면 자동 등록된다. 고르면 바로 조회.
        fav = QHBoxLayout()
        fav.addWidget(QLabel("등록 계정"))
        self.cb_accounts = NoScrollComboBox()
        self.cb_accounts.setMinimumWidth(180)
        self.cb_accounts.activated.connect(self._on_pick_account)
        self.btn_unfav = QPushButton("등록 해제")
        self.btn_unfav.setToolTip("목록에서만 뺍니다. 쌓아 둔 경기 기록은 지우지 않습니다.")
        self.btn_unfav.clicked.connect(self._on_remove_account)
        fav.addWidget(self.cb_accounts)
        fav.addWidget(self.btn_unfav)
        fav.addStretch(1)

        # 자동 수집 — 앱을 안 켜도 등록 계정을 주기적으로 쌓는다.
        self.chk_auto = QCheckBox(f"자동 수집 ({scheduler.DEFAULT_HOURS}시간마다)")
        self.chk_auto.setToolTip(
            "Windows 작업 스케줄러에 등록해 앱을 안 켜도 새 경기를 모읍니다.\n"
            "PC가 켜져 있는 동안만 동작합니다.")
        self.chk_auto.toggled.connect(self._on_toggle_auto)
        self.lb_auto = QLabel("-")
        self.lb_auto.setStyleSheet("color: #888;")
        fav.addWidget(self.chk_auto)
        fav.addWidget(self.lb_auto)
        outer.addLayout(fav)

        if not scheduler.is_supported():
            self.chk_auto.setVisible(False)
            self.lb_auto.setVisible(False)

        # 요약
        self.gb_summary = QGroupBox("요약")
        grid = QGridLayout(self.gb_summary)
        self._summary_labels: dict[str, QLabel] = {}
        fields = ["전적", "승률", "평균 득점", "평균 실점", "평균 점유율", "평균 평점"]
        for i, name in enumerate(fields):
            cap = QLabel(name)
            cap.setStyleSheet("color: #888;")
            val = QLabel("-")
            f = QFont()
            f.setPointSize(13)
            f.setBold(True)
            val.setFont(f)
            grid.addWidget(cap, 0, i, alignment=Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(val, 1, i, alignment=Qt.AlignmentFlag.AlignCenter)
            self._summary_labels[name] = val
        outer.addWidget(self.gb_summary)

        # 탭 — 전적 / 선수 지표 / 전술·경기 결과
        self.tabs = QTabWidget()
        self.table = self._make_table(self.COLUMNS)
        self.tabs.addTab(self.table, "전적")
        self.tabs.addTab(self._build_players_tab(), "선수 지표")
        self.tabs.addTab(self._build_tactics_tab(), "전술·경기 결과")
        outer.addWidget(self.tabs, 1)

        # 진행 표시
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.setCentralWidget(root)
        self.statusBar().showMessage("닉네임을 입력하세요.")

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

    PLAYER_COLUMNS = [
        "포지션", "선수", "강화", "출전", "승률", "골", "어시", "공격P",
        "슛", "유효슛", "패스%", "드리블%", "공중볼%", "태클%", "블록%",
        "가로채기", "수비", "경고", "평점",
    ]

    def _build_players_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        note = QLabel("교체 명단이라도 기록이 있으면 출전으로 집계합니다. "
                      "헤더를 클릭하면 정렬됩니다.")
        note.setStyleSheet("color: #888;")
        v.addWidget(note)
        self.tbl_players = self._make_table(self.PLAYER_COLUMNS)
        v.addWidget(self.tbl_players, 1)
        return w

    def _build_tactics_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self.lb_my_formation = QLabel("-")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        self.lb_my_formation.setFont(f)
        box_mine = QGroupBox("내 전술")
        vm = QVBoxLayout(box_mine)
        vm.addWidget(self.lb_my_formation)
        v.addWidget(box_mine)

        row = QHBoxLayout()
        box_opp = QGroupBox("상대 전술별 내 승률")
        vo = QVBoxLayout(box_opp)
        hint = QLabel("전술은 수비-수미-미드-공미-공격 라인 인원. "
                      "0-0-0-0-0 은 상대 기록이 없는 경기(몰수 등)입니다.")
        hint.setStyleSheet("color: #888;")
        vo.addWidget(hint)
        self.tbl_formation = self._make_table(["상대 전술", "승률", "전적", "경기"])
        vo.addWidget(self.tbl_formation)
        row.addWidget(box_opp, 1)

        box_res = QGroupBox("경기 결과")
        vr = QVBoxLayout(box_res)
        self.tbl_result = self._make_table(["구분", "전적", "승률"])
        vr.addWidget(self.tbl_result)
        self.tbl_period = self._make_table(["시간대", "득점", "실점"])
        vr.addWidget(self.tbl_period)
        row.addWidget(box_res, 1)
        v.addLayout(row, 1)

        row2 = QHBoxLayout()
        box_gf = QGroupBox("득점 유형")
        vgf = QVBoxLayout(box_gf)
        self.tbl_goal_types = self._make_table(["유형", "골", "비율"])
        vgf.addWidget(self.tbl_goal_types)
        row2.addWidget(box_gf, 1)

        box_ga = QGroupBox("실점 유형")
        vga = QVBoxLayout(box_ga)
        self.tbl_concede_types = self._make_table(["유형", "골", "비율"])
        vga.addWidget(self.tbl_concede_types)
        row2.addWidget(box_ga, 1)
        v.addLayout(row2, 1)
        return w

    def _load_match_types(self) -> None:
        """매치 종류는 메타데이터에서. 실패하면 폴백 목록."""
        try:
            meta = self._api.get_meta("matchtype")
            items = [(m["matchtype"], m["desc"]) for m in meta]
        except Exception:
            items = config.FALLBACK_MATCH_TYPES
        for code, desc in items:
            self.cb_type.addItem(desc, code)
        idx = self.cb_type.findData(config.DEFAULT_MATCH_TYPE)
        if idx >= 0:
            self.cb_type.setCurrentIndex(idx)

    # ── 자동 수집 ─────────────────────────────────────────────────────
    def _refresh_auto(self) -> None:
        if not scheduler.is_supported():
            return
        # 체크 상태는 앱이 아니라 실제 스케줄러가 정답이다. 앱 밖에서 지웠을 수도 있다.
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(scheduler.is_enabled())
        self.chk_auto.blockSignals(False)
        self.lb_auto.setText(scheduler.describe())

    def _on_toggle_auto(self, on: bool) -> None:
        try:
            if on:
                scheduler.enable()
            else:
                scheduler.disable()
        except scheduler.SchedulerError as e:
            QMessageBox.warning(
                self, "자동 수집",
                f"{'등록' if on else '해제'}에 실패했습니다.\n\n{e}")
        self._refresh_auto()

    # ── 등록 계정 ─────────────────────────────────────────────────────
    def _refresh_accounts(self) -> None:
        """DB 의 등록 계정을 콤보에 다시 채운다. 조회하면 자동으로 등록된다."""
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                rows = store.list_accounts(conn)
                counts = {r["ouid"]: store.match_count(conn, r["ouid"]) for r in rows}
            finally:
                conn.close()
        except Exception:
            return  # 목록을 못 채워도 검색은 되어야 한다

        current = self.cb_accounts.currentData()
        self.cb_accounts.blockSignals(True)
        self.cb_accounts.clear()
        self.cb_accounts.addItem("— 선택 —", None)
        for r in rows:
            nick = r["nickname"] or r["ouid"][:8]
            self.cb_accounts.addItem(f"{nick} ({counts.get(r['ouid'], 0)}경기)", r["ouid"])
        idx = self.cb_accounts.findData(current)
        if idx >= 0:
            self.cb_accounts.setCurrentIndex(idx)
        self.cb_accounts.blockSignals(False)

    def _on_pick_account(self, index: int) -> None:
        ouid = self.cb_accounts.itemData(index)
        if not ouid:
            return
        nick = self.cb_accounts.itemText(index).rsplit(" (", 1)[0]
        self.ed_nick.setText(nick)
        self._on_search()

    def _on_remove_account(self) -> None:
        ouid = self.cb_accounts.currentData()
        if not ouid:
            QMessageBox.information(self, "알림", "목록에서 계정을 먼저 고르세요.")
            return
        nick = self.cb_accounts.currentText().rsplit(" (", 1)[0]
        ok = QMessageBox.question(
            self, "등록 해제",
            f"'{nick}' 을(를) 등록 목록에서 뺄까요?\n\n"
            "쌓아 둔 경기 기록은 지우지 않습니다. 다시 조회하면 목록에 돌아옵니다.",
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = store.open_db(config.DB_PATH)
            try:
                store.remove_account(conn, ouid)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.warning(self, "실패", f"등록 해제에 실패했습니다: {e}")
            return
        self._refresh_accounts()

    # ── 조회 ──────────────────────────────────────────────────────────
    def _on_search(self) -> None:
        nick = self.ed_nick.text().strip()
        if not nick:
            QMessageBox.information(self, "알림", "닉네임을 입력하세요.")
            return
        if self._loader and self._loader.isRunning():
            return

        self._set_busy(True)
        self._loader = MatchLoader(
            self._api, nick, self.cb_type.currentData(), self.sp_limit.value()
        )
        self._loader.progress.connect(self._on_progress)
        self._loader.finished_ok.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def _set_busy(self, busy: bool) -> None:
        self.btn_search.setEnabled(not busy)
        self.ed_nick.setEnabled(not busy)
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
        QMessageBox.warning(self, "조회 실패", msg)

    def _on_loaded(self, matches: list[MatchSummary], details: list, ouid: str,
                   basic: dict, names: dict, positions: dict, new: int) -> None:
        self._set_busy(False)
        self._refresh_accounts()
        nick = basic.get("nickname", "-")
        level = basic.get("level", "-")

        if not matches:
            for t in (self.table, self.tbl_players, self.tbl_formation,
                      self.tbl_result, self.tbl_period, self.tbl_goal_types,
                      self.tbl_concede_types):
                t.setRowCount(0)
            self.lb_my_formation.setText("-")
            self._render_summary(Stats())
            self.statusBar().showMessage(f"{nick} (Lv.{level}) — 해당 매치 기록이 없습니다.")
            return

        self._render_table(matches)
        self._render_summary(summarize(matches))
        self._render_players(details, ouid, names, positions)
        self._render_tactics(details, ouid)

        span = ""
        if matches:
            first, last = matches[-1].date_text[:10], matches[0].date_text[:10]
            span = f" · {first} ~ {last}" if first != last else f" · {last}"
        added = f" (새 경기 {new}건 저장)" if new else ""
        self.statusBar().showMessage(
            f"{nick} (Lv.{level}) — 누적 {len(matches)}경기{span}{added}")

    # ── 렌더 ──────────────────────────────────────────────────────────
    def _render_table(self, matches: list[MatchSummary]) -> None:
        self.table.setRowCount(len(matches))
        for r, m in enumerate(matches):
            cells = [
                m.date_text, m.result, m.score, m.opponent,
                f"{m.possession}%", str(m.shoot_total), str(m.shoot_effective),
                f"{m.pass_rate:.0f}%", f"{m.rating:.2f}",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c != 3:  # 상대 닉네임만 왼쪽 정렬
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 1:
                    for key, color in RESULT_COLORS.items():
                        if key in m.result:
                            item.setBackground(color)
                            item.setForeground(QColor("white"))
                            break
                self.table.setItem(r, c, item)

    @staticmethod
    def _cell(text: str, sort_key: float | None = None) -> QTableWidgetItem:
        item = SortableItem(text, sort_key)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _fill(self, table: QTableWidget, rows: list[list]) -> None:
        """rows: [[(표시문자열, 정렬키|None), …], …]"""
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                text, key = cell if isinstance(cell, tuple) else (cell, None)
                table.setItem(r, c, self._cell(text, key))
        table.setSortingEnabled(True)

    def _render_players(self, details: list, ouid: str,
                        names: dict, positions: dict) -> None:
        players = st.aggregate_players(
            details, ouid,
            name_of=lambda i: names.get(i, str(i)),
            pos_name=lambda p: positions.get(p, str(p)),
        )
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

    def _render_tactics(self, details: list, ouid: str) -> None:
        mine = st.formation_stats(details, ouid, of_opponent=False)
        if mine:
            top = mine[0]
            extra = f" 외 {len(mine) - 1}종" if len(mine) > 1 else ""
            self.lb_my_formation.setText(
                f"{top.formation}   {top.win_rate:.1f}%   "
                f"({top.games}경기 {top.win}승 {top.draw}무 {top.lose}패){extra}"
            )

        self._fill(self.tbl_formation, [
            [f.formation, (f"{f.win_rate:.1f}%", f.win_rate),
             f"{f.win}승 {f.draw}무 {f.lose}패", (f"{f.games}", f.games)]
            for f in st.formation_stats(details, ouid)
        ])

        rb = st.result_breakdown(details, ouid)
        self._fill(self.tbl_result, [
            [label, f"{w}승 {d}무 {l}패",
             (f"{(w / (w + d + l) * 100) if (w + d + l) else 0:.1f}%", 0)]
            for label, (w, d, l) in [
                ("전후반", rb.normal), ("연장전", rb.extra),
                ("승부차기", rb.shootout), ("몰수", rb.forfeit),
            ]
        ])
        self._fill(self.tbl_period, [
            [st.PERIODS.get(k, str(k)), (f"{v.scored}", v.scored),
             (f"{v.conceded}", v.conceded)]
            for k, v in sorted(rb.periods.items())
        ])

        for table, counter in ((self.tbl_goal_types, rb.goal_types),
                               (self.tbl_concede_types, rb.concede_types)):
            total = sum(counter.values())
            self._fill(table, [
                [name, (f"{n}", n),
                 (f"{n / total * 100:.1f}%" if total else "-", n)]
                for name, n in counter.most_common()
            ])

    def _render_summary(self, s: Stats) -> None:
        vals = {
            "전적": f"{s.win}승 {s.draw}무 {s.lose}패",
            "승률": f"{s.win_rate:.1f}%",
            "평균 득점": f"{s.avg_goals_for:.2f}",
            "평균 실점": f"{s.avg_goals_against:.2f}",
            "평균 점유율": f"{s.avg_possession:.1f}%",
            "평균 평점": f"{s.avg_rating:.2f}",
        }
        for name, text in vals.items():
            self._summary_labels[name].setText(text)

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
        args = [a for a in sys.argv[1:] if a != "--collect"]
        return collect.main(args)

    app = QApplication(sys.argv)
    if not config.API_KEY:
        QMessageBox.critical(
            None, "API 키 없음",
            ".env 파일에 NEXON_API_KEY가 없습니다.\n\n"
            "1) .env.example 을 복사해 .env 로 이름 변경\n"
            "2) 발급받은 키를 NEXON_API_KEY= 뒤에 붙여넣기",
        )
        return 1

    api = FCOnlineAPI(config.API_KEY, cache_dir=config.CACHE_DIR)
    win = MainWindow(api)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
