"""화면 부품 — 랭커 카드 · 요약 카드 · 막대 그래프 행.

app_main 이 UI 흐름에 집중하도록 그리기 부품은 여기로 뺐다.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QSizePolicy, QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)  # QGridLayout: 랭커 카드 표, QSizePolicy: 값 칸 가로 확장

import theme as T

NA = "—"  # API 가 안 주는 값. 그럴싸한 숫자를 지어 넣지 않는다.


class FitTableWidget(QTableWidget):
    """열 너비는 값 텍스트 기준으로 잡되, 위젯이 그보다 넓으면 남는 폭을
    각 열의 원래 너비 비율대로 나눠 채운다 — 창을 넓혀도 오른쪽에 빈 칸이
    안 남는다.

    창이 좁아서 기본 폰트 크기로는 다 안 들어갈 때는, 가로 스크롤을 띄우거나
    글자를 자르는 대신 `MIN_FONT_PX`까지 폰트를 줄여가며 다시 재서 맞춘다 —
    글자가 잘리는 것보다 조금 작게라도 전부 보이는 쪽을 택한다.
    """

    MIN_FONT_PX = 9  # 이보다 더 줄이면 안 읽혀서 여기서 멈춘다.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra: dict[int, int] = {}
        self._base_cell_px = 14
        self._base_header_px = 13

    def set_base_font_px(self, cell_px: int, header_px: int) -> None:
        """폰트를 줄이지 않아도 될 때(창이 넓을 때) 쓸 기본 크기."""
        self._base_cell_px = cell_px
        self._base_header_px = header_px

    def set_content_widths(self, extra: dict[int, int] | None = None) -> None:
        """열 너비 계산에 쓸, 열별 추가 여백(아이콘 등)을 지정하고 즉시 맞춘다."""
        self._extra = extra or {}
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _measure(self, cell_fm: QFontMetrics, hdr_fm: QFontMetrics) -> dict[int, int]:
        widths: dict[int, int] = {}
        for c in range(self.columnCount()):
            w = 0
            header_item = self.horizontalHeaderItem(c)
            if header_item:
                w = hdr_fm.horizontalAdvance(header_item.text())
            for r in range(self.rowCount()):
                item = self.item(r, c)
                if item:
                    w = max(w, cell_fm.horizontalAdvance(item.text()))
            # +42: QSS 좌우 padding(13px*2=26px)만 셈하면 슬랙이 0이 되고,
            # 실제로 렌더될 땐 스타일이 얹는 여분의 텍스트 여백 때문에
            # "5경기"·"6.00"처럼 폭이 애매한 값이 "…"로 잘렸다(스크린샷으로
            # 확인한 값 — 좌우 padding + 16 버퍼).
            widths[c] = w + 42 + self._extra.get(c, 0)
        return widths

    def _fit(self) -> None:
        if self.columnCount() == 0:
            return
        avail = self.viewport().width()
        if avail <= 0:
            return
        cell_px = self._base_cell_px
        header_px = self._base_header_px
        cell_font = QFont(self.font())
        header_font = QFont(self.horizontalHeader().font())
        widths: dict[int, int] = {}
        while True:
            cell_font.setPixelSize(cell_px)
            header_font.setPixelSize(header_px)
            widths = self._measure(QFontMetrics(cell_font), QFontMetrics(header_font))
            total = sum(widths.values())
            if total <= avail or cell_px <= self.MIN_FONT_PX:
                break
            cell_px -= 1
            header_px = max(self.MIN_FONT_PX - 1, header_px - 1)
        self.setFont(cell_font)
        self.horizontalHeader().setFont(header_font)
        total = sum(widths.values())
        if total <= 0:
            return
        if avail > total:
            extra = avail - total
            for c, w in widths.items():
                self.setColumnWidth(c, w + int(extra * (w / total)))
        else:
            for c, w in widths.items():
                self.setColumnWidth(c, w)


class RowBorderDelegate(QStyledItemDelegate):
    """선택한 행 전체를 셀별 네모가 아니라 하나로 이어진 테두리로 감싼다.

    QSS의 QTableWidget::item:selected 는 셀 하나하나에 테두리를 그려서
    SelectRows 로 여러 칸이 선택돼도 칸마다 따로 박스가 생겼다 — 그래서
    기본 선택 배경/테두리는 죽이고(State_Selected 플래그를 지워서 그리게 함)
    여기서 첫 칸엔 왼쪽 변, 마지막 칸엔 오른쪽 변, 모든 칸에 위아래 변을
    같은 색으로 그려 이어붙인다.
    """

    def paint(self, painter, option, index) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        opt = option
        if selected:
            from PyQt6.QtWidgets import QStyleOptionViewItem
            opt = QStyleOptionViewItem(option)
            opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt, index)
        if not selected:
            return
        painter.save()
        painter.setPen(QPen(QColor("#ffffff"), 1))
        rect = option.rect.adjusted(0, 0, -1, -1)
        table = self.parent()
        last_col = table.columnCount() - 1 if table is not None else index.column()
        painter.drawLine(rect.topLeft(), rect.topRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if index.column() == 0:
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        if index.column() == last_col:
            painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.restore()


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
                and getattr(other, "_key", None) is not None:
            return self._key < other._key
        return super().__lt__(other)


class StatCard(QFrame):
    """요약 카드 — 제목 + 큰 값. (전적 / 승률 / 평균 득점 / 평균 실점)"""

    def __init__(self, title: str, color: str = T.TEXT):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)

        self.cap = QLabel(title)
        self.cap.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        self.cap.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.value = QLabel("-")
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.value.setFont(f)
        self.value.setStyleSheet(f"color: {color}; border: none;")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        v.addWidget(self.cap)
        v.addWidget(self.value)

    def set(self, text: str) -> None:
        self.value.setText(text)

    def set_title(self, title: str) -> None:
        self.cap.setText(title)


class RankerCard(QFrame):
    """감독모드 랭커 카드 — 수치 중심 대시보드형.

    순위·구단가치·점수는 넥슨 API 가 주지 않는다(엔드포인트 자체가 없다.
    존재하지 않는 경로도 같은 400 을 뱉는 것으로 확인). 칸은 두되 값은
    NA 로 남기고 각주로 이유를 밝힌다 — 지어낸 숫자를 넣지 않는다.

    큰 숫자를 타일로 나열해 게임 레벨업 화면처럼 — 표 형태(fc-info.com 류)와
    확실히 다른 구성으로 가져간다. 타일 순서: 순위 · 점수 순으로 눈에 먼저
    들어오게, 전적·구단가치는 아래.
    """

    ROWS = ["순위", "전적", "구단가치", "점수"]
    TILE_ORDER = ["순위", "점수", "전적", "구단가치"]

    def __init__(self):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: 12px; }}")
        self.setMaximumWidth(720)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._head = QFrame()
        head_v = QVBoxLayout(self._head)
        head_v.setContentsMargins(14, 12, 14, 12)
        head_v.setSpacing(6)

        self._head_name = QLabel("-")
        nf = QFont()
        nf.setBold(True)
        nf.setPointSize(14)
        self._head_name.setFont(nf)
        self._head_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._head_name.setStyleSheet("border: none;")
        head_v.addWidget(self._head_name)

        grade_row = QHBoxLayout()
        grade_row.setSpacing(8)
        grade_row.addStretch(1)
        self._head_grade = QLabel("-")
        gf = QFont()
        gf.setBold(True)
        gf.setPointSize(19)
        self._head_grade.setFont(gf)
        self._head_grade.setStyleSheet("border: none;")
        grade_row.addWidget(self._head_grade)
        self._head_badge = QLabel()
        self._head_badge.setFixedSize(32, 32)
        self._head_badge.setScaledContents(True)
        self._head_badge.setStyleSheet("border: none; background: transparent;")
        grade_row.addWidget(self._head_badge)
        grade_row.addStretch(1)
        head_v.addLayout(grade_row)

        v.addWidget(self._head)

        grid = QGridLayout()
        grid.setContentsMargins(22, 22, 22, 10)
        grid.setSpacing(18)
        self._vals: dict[str, QLabel] = {}
        self._rows: dict[str, tuple[QWidget, QLabel]] = {}
        for i, name in enumerate(self.TILE_ORDER):
            r, col = divmod(i, 2)
            tile = QFrame()
            tile.setStyleSheet(
                f"QFrame {{ background: #0d1117; border-radius: 8px; }}")
            tv = QVBoxLayout(tile)
            tv.setContentsMargins(20, 20, 20, 20)
            tv.setSpacing(6)

            cap = QLabel(name)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            capf = QFont()
            capf.setPointSize(14)
            cap.setFont(capf)
            cap.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
            tv.addWidget(cap)

            val = QLabel(NA)
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vf = QFont()
            vf.setPointSize(30)
            vf.setBold(True)
            val.setFont(vf)
            val.setStyleSheet(f"color: {T.TEXT}; border: none;")
            val.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Preferred)
            tv.addWidget(val)

            grid.addWidget(tile, r, col)
            self._vals[name] = val
            self._rows[name] = (tile, val)
        v.addLayout(grid)

        self.note = QLabel("")
        self.note.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.note.setStyleSheet(
            f"color: {T.TEXT_DIM}; border: none; padding: 2px 14px 10px;"
            f" font-size: 13px;")
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        self.set_mode(False)  # 기본은 랭커 아님 — 확인되면 켠다

    def set_mode(self, is_ranker: bool, grade_name: str = "") -> None:
        """챔피언스 이상(랭커)일 때만 순위·구단가치·점수 타일을 보여준다.

        그 아래 등급은 애초에 넥슨 데이터센터 랭킹(1만 위)에 거의 안 잡히고
        값도 의미가 약해서, 카드 자체를 '랭커 카드'가 아니라 수수한 프로필로
        보이게 헤더 스타일까지 바꾼다. 헤더에는 현재 등급명만 보여준다.
        """
        text = grade_name or ("감독모드 랭커" if is_ranker else "구단주 정보")
        self._head_grade.setText(text)
        if is_ranker:
            self._head.setStyleSheet(
                f"QFrame {{ background: {T.GREEN}; border: none;"
                f" border-top-left-radius: 12px; border-top-right-radius: 12px; }}")
            self._head_name.setStyleSheet("color: #06240d; border: none;")
            self._head_grade.setStyleSheet("color: #06240d; border: none;")
        else:
            self._head.setStyleSheet(
                f"QFrame {{ background: {T.PANEL_2}; border: none;"
                f" border-top-left-radius: 12px; border-top-right-radius: 12px; }}")
            self._head_name.setStyleSheet(f"color: {T.TEXT}; border: none;")
            self._head_grade.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        for name in ("순위", "구단가치", "점수"):
            tile, _ = self._rows[name]
            tile.setVisible(is_ranker)

    def set_name(self, text: str) -> None:
        """헤더 안 이름·레벨 줄."""
        self._head_name.setText(text)

    def set_badge(self, pixmap_path: str | None) -> None:
        """등급 옆 배지 아이콘. 못 받아왔으면(경로 없음) 그냥 비워 둔다."""
        if pixmap_path:
            self._head_badge.setPixmap(QPixmap(pixmap_path))
            self._head_badge.setVisible(True)
        else:
            self._head_badge.clear()
            self._head_badge.setVisible(False)

    def set(self, name: str, text: str, color: str = T.TEXT) -> None:
        lb = self._vals[name]
        lb.setText(text)
        lb.setStyleSheet(f"color: {color}; border: none;")


class BarRow(QWidget):
    """유형별 골 한 줄 — 이름 · 골수 · 비율 · 막대."""

    def __init__(self, name: str, count: int, total: int, color: str):
        super().__init__()
        pct = (count / total * 100) if total else 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        lb = QLabel(name)
        lb.setStyleSheet(f"color: {T.TEXT};")
        n = QLabel(f"{count}골")
        n.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        p = QLabel(f"({pct:.1f}%)")
        p.setStyleSheet(f"color: {T.TEXT_DIM};")
        top.addWidget(lb)
        top.addStretch(1)
        top.addWidget(n)
        top.addWidget(p)
        v.addLayout(top)

        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(int(pct * 10))
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {T.PANEL_2}; border: none;"
            f" border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}")
        v.addWidget(bar)


class TrendChart(QWidget):
    """일별 승률 꺾은선 그래프 — QtCharts 없이 QPainter로 직접 그린다.

    points: (라벨, 승률, 경기수) 튜플 리스트, 날짜 오름차순. 경기가 없는 날은
    아예 넘기지 말 것 — 0%로 그려지면 "그 날 다 짐"과 구분이 안 된다.
    """

    def __init__(self, points: list[tuple[str, float, int]]):
        super().__init__()
        self._points = points
        self.setMinimumHeight(220)

    def set_points(self, points: list[tuple[str, float, int]]) -> None:
        self._points = points
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ml, mr, mt, mb = 44, 16, 14, 26
        plot_w = max(w - ml - mr, 1)
        plot_h = max(h - mt - mb, 1)

        p.fillRect(self.rect(), QColor(T.PANEL))

        font = p.font()
        font.setPointSize(9)
        p.setFont(font)
        for pct in (0, 25, 50, 75, 100):
            y = mt + plot_h * (1 - pct / 100)
            p.setPen(QColor(T.BORDER))
            p.drawLine(ml, int(y), w - mr, int(y))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(2, int(y) + 4, f"{pct}%")

        if not self._points:
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                      "표시 구간에 날짜가 있는 경기가 없습니다.")
            return

        n = len(self._points)

        def xy(i: int, rate: float) -> QPointF:
            x = ml + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)
            y = mt + plot_h * (1 - rate / 100)
            return QPointF(x, y)

        pts = [xy(i, rate) for i, (_, rate, _) in enumerate(self._points)]

        p.setPen(QPen(QColor(T.GREEN), 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)

        p.setPen(QPen(QColor(T.PANEL), 1))
        p.setBrush(QColor(T.GREEN))
        for pt in pts:
            p.drawEllipse(pt, 3.5, 3.5)

        p.setPen(QColor(T.TEXT_DIM))
        step = max(1, n // 6)
        # 라벨 rect 의 y 는 사각형 "맨 위" 다 — h-8 로 두면 높이 14짜리 rect가
        # h+6 까지 내려가 위젯 바깥(잘림)으로 삐져나갔다. margin_b(mb) 안에
        # 완전히 들어오게 올려 잡는다.
        label_y = h - mb + 6
        for i in range(0, n, step):
            x = pts[i].x()
            p.drawText(int(x) - 24, label_y, 48, 14,
                      Qt.AlignmentFlag.AlignCenter, self._points[i][0])


def _grade_badge_colors(grade) -> tuple[str, str]:
    """강화 단계 → (배지 배경색, 글자색).

    넥슨 데이터센터(fconline.nexon.com/datacenter) 강화 필터 UI에서 실제로
    쓰는 색을 그대로 옮겼다 — 숫자만으론 안 와닿는다는 지적 반영.
    1~4강 브론즈 · 5~7강 실버 · 8~10강 골드 · 11~13강 홀로그램(프리즘).
    """
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return T.PANEL_2, T.TEXT_DIM
    if g >= 11:
        return "#6dd5e8", "#0a2a30"
    if g >= 8:
        return "#e8c545", "#3a2c00"
    if g >= 5:
        return "#b8bfc7", "#20242a"
    if g >= 1:
        return "#c17a4a", "#2b1608"
    return T.PANEL_2, T.TEXT_DIM


class _PlayerChip(QFrame):
    """피치 위에 올라가는 선수 카드 한 장 — 얼굴 사진·포지션·강화·이름."""

    def __init__(self, pos_name: str, name: str, grade, accent: str):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: rgba(13,17,23,235); border: 2px solid {accent};"
            f" border-radius: 8px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 3)
        v.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.season_badge = QLabel()
        self.season_badge.setFixedSize(14, 14)
        self.season_badge.setScaledContents(True)
        self.season_badge.setStyleSheet("border: none; background: transparent;")
        top.addWidget(self.season_badge)
        pos_lb = QLabel(pos_name)
        pos_lb.setStyleSheet(
            f"background: {accent}; color: #06240d; border: none;"
            f" font-weight: bold; font-size: 11px; border-radius: 3px;"
            f" padding: 1px 4px;")
        grade_bg, grade_fg = _grade_badge_colors(grade)
        grade_lb = QLabel(str(grade))
        grade_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grade_lb.setFixedSize(18, 18)
        gf = QFont()
        gf.setBold(True)
        gf.setPointSize(9)
        grade_lb.setFont(gf)
        grade_lb.setStyleSheet(
            f"background: {grade_bg}; color: {grade_fg}; border: none;"
            f" border-radius: 9px;")
        top.addWidget(pos_lb)
        top.addStretch(1)
        top.addWidget(grade_lb)
        v.addLayout(top)

        self.face = QLabel()
        self.face.setFixedSize(40, 40)
        self.face.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face.setScaledContents(True)
        self.face.setStyleSheet(
            f"border: none; background: {T.PANEL_2}; border-radius: 20px;")
        face_row = QHBoxLayout()
        face_row.addStretch(1)
        face_row.addWidget(self.face)
        face_row.addStretch(1)
        v.addLayout(face_row)

        name_lb = QLabel(name)
        nf = QFont()
        nf.setPointSize(10)
        nf.setBold(True)
        name_lb.setFont(nf)
        name_lb.setStyleSheet(f"color: {T.TEXT}; border: none;")
        name_lb.setWordWrap(True)
        name_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(name_lb)

    def set_face(self, pixmap_path: str) -> None:
        pm = QPixmap(pixmap_path)
        if not pm.isNull():
            self.face.setPixmap(pm)

    def set_season_icon(self, pixmap_path: str) -> None:
        pm = QPixmap(pixmap_path)
        if not pm.isNull():
            self.season_badge.setPixmap(pm)


class PitchWidget(QWidget):
    """축구장 배경에 포지션대로 선수를 배치해서 보여준다(fc-info.com 류 스쿼드 화면 참고).

    players: (spPosition, 포지션이름, 선수이름, 강화) 튜플 리스트.
    좌표는 실측 없이 표준 포메이션 슬롯을 손으로 잡은 근사치 — 실제 좌표
    데이터가 없어서(API 미제공) 포지션 코드별로 "대략 그 자리"에 놓는다.
    y=0 이 상대 골대 쪽(공격 라인), y=1 이 GK.
    """

    # spPosition -> (x비율, y비율). stats._LINES 코드 정의와 맞춘 것.
    COORDS: dict[int, tuple[float, float]] = {
        0: (0.50, 0.94),                                            # GK
        1: (0.50, 0.84), 2: (0.87, 0.74), 3: (0.80, 0.79),           # SW RWB RB
        4: (0.62, 0.81), 5: (0.50, 0.82), 6: (0.38, 0.81),           # RCB CB LCB
        7: (0.20, 0.79), 8: (0.13, 0.74),                            # LB LWB
        9: (0.65, 0.63), 10: (0.50, 0.65), 11: (0.35, 0.63),         # RDM CDM LDM
        12: (0.87, 0.50), 13: (0.62, 0.52), 14: (0.50, 0.54),        # RM RCM CM
        15: (0.38, 0.52), 16: (0.13, 0.50),                          # LCM LM
        17: (0.65, 0.36), 18: (0.50, 0.34), 19: (0.35, 0.36),        # RAM CAM LAM
        20: (0.65, 0.17), 21: (0.50, 0.13), 22: (0.35, 0.17),        # RF CF LF
        23: (0.85, 0.21), 24: (0.60, 0.08), 25: (0.50, 0.05),        # RW RS ST
        26: (0.40, 0.08), 27: (0.15, 0.21),                          # LS LW
    }
    CHIP_SIZE = (108, 96)

    def __init__(self, players: list[tuple[int, str, str, object, object]]):
        """players: (spPosition, 포지션이름, 선수이름, 강화, spId) 튜플 리스트."""
        super().__init__()
        self.setMinimumSize(560, 640)
        self._chips: list[tuple[QWidget, float, float]] = []
        self._chip_by_sp_id: dict[int, _PlayerChip] = {}
        for sp_position, pos_name, name, grade, sp_id in players:
            xf, yf = self.COORDS.get(sp_position, (0.5, 0.5))
            accent = self._accent_for(sp_position)
            chip = _PlayerChip(pos_name, name, grade, accent)
            chip.setParent(self)
            chip.setFixedSize(*self.CHIP_SIZE)
            self._chips.append((chip, xf, yf))
            if isinstance(sp_id, int):
                self._chip_by_sp_id[sp_id] = chip
        self._layout_chips()

    def set_face(self, sp_id: int, pixmap_path: str) -> None:
        chip = self._chip_by_sp_id.get(sp_id)
        if chip:
            chip.set_face(pixmap_path)

    def set_season_icon(self, sp_id: int, pixmap_path: str) -> None:
        chip = self._chip_by_sp_id.get(sp_id)
        if chip:
            chip.set_season_icon(pixmap_path)

    @staticmethod
    def _accent_for(sp_position: int) -> str:
        """GK 노랑 · 수비(1-8) 파랑 · 미드필더 그룹(9-19, 수미·미드·공미 전부) 초록
        · 최전방 공격(20-27) 빨강. 공미(17-19, RAM/CAM/LAM)도 미드필더로 친다 —
        스트라이커·윙어처럼 빨강으로 보이면 헷갈린다는 지적을 반영."""
        if sp_position == 0:
            return T.YELLOW
        if 1 <= sp_position <= 8:
            return T.BLUE
        if 9 <= sp_position <= 19:
            return T.GREEN
        return T.RED

    def resizeEvent(self, event) -> None:
        self._layout_chips()
        super().resizeEvent(event)

    def _layout_chips(self) -> None:
        w, h = self.width(), self.height()
        cw, ch = self.CHIP_SIZE
        for chip, xf, yf in self._chips:
            chip.move(int(w * xf - cw / 2), int(h * yf - ch / 2))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#1e5c34"))
        line = QColor(255, 255, 255, 130)
        p.setPen(QPen(line, 2))
        m = 10
        p.drawRect(m, m, w - 2 * m, h - 2 * m)
        p.drawLine(m, h // 2, w - m, h // 2)
        p.drawEllipse(QPointF(w / 2, h / 2), 46, 46)
        box_w = int((w - 2 * m) * 0.62)
        box_h = 56
        p.drawRect(int(w / 2 - box_w / 2), m, box_w, box_h)
        p.drawRect(int(w / 2 - box_w / 2), h - m - box_h, box_w, box_h)


def wdl_text(w: int, d: int, l: int) -> str:
    return f"{w}승 {d}무 {l}패"


def rate_of(w: int, d: int, l: int) -> float:
    t = w + d + l
    return (w / t * 100) if t else 0.0
