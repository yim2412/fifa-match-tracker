"""피파 전적관리 — PyQt6 런처.

닉네임을 넣으면 넥슨 오픈API로 최근 전적을 받아 표와 요약으로 보여준다.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import config
from models import MatchSummary, Stats, parse_match, summarize
from nexon_api import FCOnlineAPI, NexonAPIError

RESULT_COLORS = {"승": QColor("#1b5e20"), "무": QColor("#4e4e4e"), "패": QColor("#8e1616")}


class NoScrollComboBox(QComboBox):
    """휠 스크롤로 값이 바뀌는 사고 방지."""

    def wheelEvent(self, e):
        e.ignore()


class MatchLoader(QThread):
    """API 호출은 전부 여기서 — UI 스레드가 멈추지 않게."""

    progress = pyqtSignal(int, int, str)          # 완료 수, 전체, 메시지
    finished_ok = pyqtSignal(list, dict)          # [MatchSummary], user_basic
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

            self.progress.emit(0, self._limit, "매치 목록 조회 중…")
            ids = self._api.get_match_ids(ouid, self._match_type, 0, self._limit)
            if not ids:
                self.finished_ok.emit([], basic)
                return

            matches: list[MatchSummary] = []
            done = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                for detail in pool.map(self._safe_detail, ids):
                    if self._cancel:
                        return
                    done += 1
                    self.progress.emit(done, len(ids), f"경기 상세 {done}/{len(ids)}")
                    if detail is None:
                        continue
                    m = parse_match(detail, ouid)
                    if m:
                        matches.append(m)

            matches.sort(key=lambda m: m.match_date or 0, reverse=True)
            self.finished_ok.emit(matches, basic)

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

        # 전적 표
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.table, 1)

        # 진행 표시
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.setCentralWidget(root)
        self.statusBar().showMessage("닉네임을 입력하세요.")

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

    def _on_loaded(self, matches: list[MatchSummary], basic: dict) -> None:
        self._set_busy(False)
        nick = basic.get("nickname", "-")
        level = basic.get("level", "-")

        if not matches:
            self.table.setRowCount(0)
            self._render_summary(Stats())
            self.statusBar().showMessage(f"{nick} (Lv.{level}) — 해당 매치 기록이 없습니다.")
            return

        self._render_table(matches)
        self._render_summary(summarize(matches))
        self.statusBar().showMessage(f"{nick} (Lv.{level}) — 최근 {len(matches)}경기")

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
