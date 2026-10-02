"""화면 부품 — 랭커 카드 · 요약 카드 · 막대 그래프 행.

app_main 이 UI 흐름에 집중하도록 그리기 부품은 여기로 뺐다.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)  # QGridLayout: 랭커 카드 표, QSizePolicy: 값 칸 가로 확장

import theme as T

NA = "—"  # API 가 안 주는 값. 그럴싸한 숫자를 지어 넣지 않는다.


def add_shadow(w: QWidget) -> None:
    """카드 아래 옅은 그림자. 그림자 효과는 창 크기를 바꿀 때마다 다시
    그려져 비싸다 — 화면에 동시에 보이는 카드 몇 장에만 건다."""
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(T.SHADOW_BLUR)
    eff.setOffset(0, T.SHADOW_Y)
    eff.setColor(QColor(*T.SHADOW_RGB, T.SHADOW_ALPHA))
    w.setGraphicsEffect(eff)


class UpdateCard(QFrame):
    """창 오른쪽 아래에 떠 있는 '새 버전' 카드 — 런처의 업데이트 알림처럼.

    어느 화면 위에든 같은 자리에 뜬다(검색 화면·메인 화면). 위치는 부모가
    place() 로 정한다. 내려받는 동안은 버튼 대신 진행 막대가 보인다."""
    update_clicked = pyqtSignal()
    dismissed = pyqtSignal()
    MARGIN = 20
    WIDTH = 300  # 고정 — 글자 길이에 따라 줄면 내려받는 중 문구가 잘렸다
    # 새 버전이면 [업데이트], 최신이면 "최신 버전입니다"(사용자 요청 2026-10-02 — 이 자리에 둘 다).
    # 어느 쪽이든 사용자가 닫을 때까지 둔다 — 6초 뒤 저절로 닫았더니 사용자가 못 봤다.
    # 확인을 못 했으면 안 띄운다(왼쪽 아래 상태 칸이 "업데이트 확인 못 함"을 보인다).

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("updateCard")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(
            f"QFrame#updateCard {{ background: {T.PANEL}; border: 1px solid {T.GREEN};"
            f" border-radius: 12px; }} QLabel {{ border: none; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)
        self.lb_title = QLabel()
        f = QFont()
        f.setBold(True)
        f.setPointSize(11)
        self.lb_title.setFont(f)
        self.lb_sub = QLabel()
        self.lb_sub.setStyleSheet(f"color: {T.TEXT_DIM};")
        v.addWidget(self.lb_title)
        v.addWidget(self.lb_sub)
        self.bar = QProgressBar()
        self.bar.setMaximumHeight(10)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_later = QPushButton("나중에")
        self.btn_later.setFlat(True)
        self.btn_later.clicked.connect(self._dismiss)
        self.btn_update = QPushButton("업데이트")
        self.btn_update.setObjectName("primary")
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.clicked.connect(self.update_clicked)
        row.addStretch(1)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_update)
        v.addLayout(row)
        add_shadow(self)
        self.hide()

    def show_release(self, tag: str, current: str, installed: bool) -> None:
        self.lb_title.setText(f"새 버전 {tag}")
        self.lb_sub.setText(f"지금 {current}" + ("" if installed else " · 받기 페이지가 열립니다"))
        self.btn_update.setText("업데이트" if installed else "받으러 가기")
        self.btn_later.setText("나중에")
        self.set_busy(False)
        self.show()
        self.raise_()
        self.place()

    def show_latest(self, current: str) -> None:
        self.lb_title.setText("최신 버전입니다")
        self.lb_sub.setText(f"지금 {current}")
        self.set_busy(False)
        self.btn_update.setVisible(False)  # 받을 게 없다
        self.btn_later.setText("닫기")
        self.show()
        self.raise_()
        self.place()

    def set_busy(self, busy: bool, text: str = "") -> None:
        # 받는 동안은 버튼을 숨긴다 — 비활성 버튼이 남아 있으면 눌러도 되는지 헷갈린다
        self.bar.setVisible(busy)
        self.btn_update.setVisible(not busy)
        self.btn_update.setEnabled(not busy)
        self.btn_later.setVisible(not busy)
        if busy:
            self.lb_sub.setText(text)
            self.bar.setRange(0, 0)  # 크기를 모를 때는 물결
        self.place()

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.lb_sub.setText(f"내려받는 중 {done * 100 // total}% · {done / 2**20:.1f} / {total / 2**20:.1f} MB")
        else:
            self.lb_sub.setText(f"내려받는 중 · {done / 2**20:.1f} MB")

    def place(self) -> None:
        """부모의 오른쪽 아래(상태줄 위)에 붙인다 — 부모가 크기를 바꿀 때마다 부른다."""
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        bottom = p.height()
        sb = getattr(p, "statusBar", None)
        if callable(sb) and sb() is not None and sb().isVisible():
            bottom -= sb().height()
        self.move(p.width() - self.width() - self.MARGIN, bottom - self.height() - self.MARGIN)

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()


class FitLabel(QLabel):
    """한 줄 글자가 칸보다 길면 잘리는 대신 글꼴을 줄여 다 보여준다.

    랭커 카드 전적 칸(30pt)에 "906승 393무 903패 (41.1%)" 가 들어오자 512px 이
    필요한데 칸은 329px 이라 양 끝이 잘렸다 — 숫자 자릿수는 계정마다 달라서
    칸 폭을 상수로 맞출 수 없다. 최소 폭은 min_pt 로 다 들어가는 폭으로
    알려서, 레이아웃이 그보다 좁게는 누르지 않게 한다.
    """

    def __init__(self, text: str = "", base_pt: int = 30, min_pt: int = 14,
                 bold: bool = True):
        super().__init__(text)
        self._base_pt = base_pt
        self._min_pt = min_pt
        f = QFont()
        f.setPointSize(base_pt)
        f.setBold(bold)
        super().setFont(f)
        self._refit()

    def _font_at(self, pt: int) -> QFont:
        f = QFont(self.font())
        f.setPointSize(pt)
        return f

    def _text_w(self, pt: int) -> int:
        return QFontMetrics(self._font_at(pt)).horizontalAdvance(self.text())

    def _pad(self) -> int:
        m = self.contentsMargins()
        return m.left() + m.right() + 2 * self.margin() + 2

    def setText(self, text: str) -> None:
        super().setText(text)
        self.updateGeometry()
        self._refit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refit()

    def _refit(self) -> None:
        avail = self.width() - self._pad()
        pt = self._base_pt
        while pt > self._min_pt and self._text_w(pt) > avail:
            pt -= 1
        if self.font().pointSize() != pt:
            super().setFont(self._font_at(pt))

    def sizeHint(self) -> QSize:
        # 원하는 폭은 '줄여서라도 들어가는 폭' — 기본 크기 폭을 원하면 옆 칸을
        # 밀어내 랭커 카드 타일 두 칸의 폭이 값에 따라 들쭉날쭉해진다.
        return self.minimumSizeHint()

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetrics(self._font_at(self._base_pt))
        return QSize(self._text_w(self._min_pt) + self._pad(), fm.height())


class WrapBar(QFrame):
    """가로 바 — 한 줄에 다 들어가면 [왼쪽 | 가운데 … 오른쪽], 아니면 두 줄로
    [왼쪽 … 오른쪽] / [가운데] 를 놓는다.

    위쪽 바가 한 줄로만 놓이던 때는 바의 최소 폭(1296px)이 창 최소 폭을
    1566px 로 묶어, 1366 노트북에선 창이 화면을 넘쳤고 그보다 넓어도 시즌
    칸이 150px 로 눌려 글자가 잘렸다. 최소 폭을 두 줄 배치 기준으로 알려서
    창은 그만큼까지 줄어들고, 줄이 바뀌는 판정은 폭만 보므로 왔다 갔다 하지
    않는다.
    """

    def __init__(self, left: QWidget, middle: QWidget, right: QWidget,
                 sep: QWidget | None = None, object_name: str = "topbar"):
        super().__init__()
        self.setObjectName(object_name)
        self._left, self._middle, self._right, self._sep = left, middle, right, sep
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(14, 10, 14, 10)
        self._v.setSpacing(8)
        self._row1 = QHBoxLayout()
        self._row2 = QHBoxLayout()
        for row in (self._row1, self._row2):
            row.setSpacing(8)
            self._v.addLayout(row)
        self._two_rows: bool | None = None
        self._arrange(False)

    def _gap(self) -> int:
        return self._row1.spacing()

    def _margins_w(self) -> int:
        m = self._v.contentsMargins()
        return m.left() + m.right()

    def one_row_width(self) -> int:
        parts = [self._left, self._middle, self._right]
        if self._sep is not None:
            parts.append(self._sep)
        return (sum(w.sizeHint().width() for w in parts)
                + self._gap() * len(parts) + self._margins_w())

    def two_row_width(self) -> int:
        top = (self._left.sizeHint().width() + self._right.sizeHint().width()
               + self._gap() * 2)
        return max(top, self._middle.sizeHint().width()) + self._margins_w()

    @staticmethod
    def _clear(row: QHBoxLayout) -> None:
        while row.count():
            row.takeAt(0)

    def _arrange(self, two_rows: bool) -> None:
        if two_rows == self._two_rows:
            return
        self._two_rows = two_rows
        self._clear(self._row1)
        self._clear(self._row2)
        self._row1.addWidget(self._left)
        if two_rows:
            if self._sep is not None:
                self._sep.hide()
            self._row1.addStretch(1)
            self._row1.addWidget(self._right)
            self._row2.addWidget(self._middle)
            self._row2.addStretch(1)
        else:
            if self._sep is not None:
                self._sep.show()
                self._row1.addWidget(self._sep)
            self._row1.addWidget(self._middle)
            self._row1.addStretch(1)
            self._row1.addWidget(self._right)
        self._v.invalidate()
        self.updateGeometry()

    def is_two_rows(self) -> bool:
        return bool(self._two_rows)

    def relayout(self) -> None:
        """안에 든 위젯의 폭이 바뀌었을 때(시즌 목록 갱신 등) 다시 판정한다."""
        self.updateGeometry()
        self._arrange(self.width() < self.one_row_width())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange(self.width() < self.one_row_width())

    def minimumSizeHint(self) -> QSize:
        return QSize(self.two_row_width(), super().minimumSizeHint().height())


class VScrollArea(QScrollArea):
    """세로로만 스크롤되는 페이지 틀.

    창 높이가 페이지 최소 높이(승률 그래프 737px)보다 작으면 페이지를 눌러
    글자를 겹치게 하는 대신 스크롤로 보여준다. 가로는 스크롤을 끄는 대신
    최소 폭을 내용의 최소 폭으로 알려서, 창이 그보다 좁아지지 않게 한다 —
    QScrollArea 는 기본적으로 안의 최소 폭을 밖에 알리지 않아, 그대로 두면
    창이 줄어드는 만큼 가로가 조용히 잘린다.
    """

    def __init__(self, inner: QWidget):
        super().__init__()
        self.setWidget(inner)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _bar_w(self) -> int:
        return self.verticalScrollBar().sizeHint().width()

    def minimumSizeHint(self) -> QSize:
        w = self.widget()
        need = w.minimumSizeHint().expandedTo(w.minimumSize())
        return QSize(need.width() + self._bar_w(), 120)

    def sizeHint(self) -> QSize:
        hint = self.widget().sizeHint()
        return QSize(hint.width() + self._bar_w(), hint.height())


class Card(QFrame):
    """흰 카드 — 제목(선택) + 본문. 본문 레이아웃은 .body 로 채운다."""

    def __init__(self, title: str = "", shadow: bool = False, margin: int = 18):
        super().__init__()
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(margin, margin - 4, margin, margin)
        v.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.title.setVisible(bool(title))
        v.addWidget(self.title)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.body, 1)
        if shadow:
            add_shadow(self)


# 한 글자 폭의 상한을 잡을 때 같이 재는 넓은 글자 — 기본 글꼴 밖(대체 글꼴)에서 오는 종류마다 하나씩
_WIDE_SAMPLES = ("가", "뷁", "漢", "W", "M", "@", "%", "Ⅲ", "😀", "★", "■")


class FitTableWidget(QTableWidget):
    """열 너비는 값 텍스트 기준으로 잡되, 위젯이 그보다 넓으면 남는 폭을
    각 열의 원래 너비 비율대로 나눠 채운다 — 창을 넓혀도 오른쪽에 빈 칸이
    안 남는다.

    창이 좁아서 기본 폰트 크기로는 다 안 들어갈 때는, 가로 스크롤을 띄우거나
    글자를 자르는 대신 `MIN_FONT_PX`까지 폰트를 줄여가며 다시 재서 맞춘다 —
    글자가 잘리는 것보다 조금 작게라도 전부 보이는 쪽을 택한다.
    """

    MIN_FONT_PX = 9  # 이보다 더 줄이면 안 읽혀서 여기서 멈춘다.
    # 열마다 글자 폭 위에 얹는 여백은 상수로 박지 않고 Qt 에게 묻는다
    # (_measure_pad). 예전 상수 42 는 "재는 글꼴 ≠ 그리는 글꼴"(전역 QSS 의
    # font-size 가 setFont 를 이겼다) 시절의 실측값이라, 그 QSS 를 걷어내자
    # 24 로 줄였더니 이번엔 "33.3"(27px)이 53px 칸에서 "3…"로 잘렸다 — Qt 는
    # 그 셀에 60px 가 필요하다고 계산하고 있었다(Fusion 셀 여백 + 체크 표시
    # 자리 등). 스타일·글꼴이 바뀔 때마다 상수를 다시 맞추는 대신 실측한다.
    PAD_SLACK = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra: dict[int, int] = {}
        self._base_cell_px = 14
        self._base_header_px = 13
        self._base_text_widths: dict[int, int] = {}  # 기준 폰트 크기에서 잰 열별 텍스트 폭
        self._fit_cache: dict[int, dict[int, int]] = {}  # cell_px -> 최종 열 너비(+padding+extra)
        self._pad = 0  # 열마다 얹는 여백 — set_content_widths 때 _measure_pad 로 잰다
        self._sort_w = 0  # 정렬 화살표 폭 — Qt 는 정렬 중인 열 하나에만 그린다
        self.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)

    def set_base_font_px(self, cell_px: int, header_px: int) -> None:
        """폰트를 줄이지 않아도 될 때(창이 넓을 때) 쓸 기본 크기."""
        self._base_cell_px = cell_px
        self._base_header_px = header_px

    def set_content_widths(self, extra: dict[int, int] | None = None) -> None:
        """열 너비 계산에 쓸, 열별 추가 여백(아이콘 등)을 지정하고 즉시 맞춘다.

        표 내용(행/헤더 텍스트)이 바뀔 때만 부르는 지점이라, 여기서 기준
        폰트 크기의 텍스트 폭을 한 번 재서 캐시해 둔다. resizeEvent 는 이
        캐시로 후보 폰트 크기를 추정만 하므로, 창을 드래그하는 동안 표
        전체를 폰트 크기 수만큼 반복 측정하지 않는다."""
        self._extra = extra or {}
        self._pad = self._measure_pad()
        cell_font = QFont(self.font())
        cell_font.setPixelSize(self._base_cell_px)
        header_font = QFont(self.horizontalHeader().font())
        header_font.setPixelSize(self._base_header_px)
        self._base_text_widths = self._measure_text(
            QFontMetrics(cell_font), QFontMetrics(header_font))
        self._fit_cache = {}
        self._fit()

    def _on_sort_changed(self, *_):
        self._fit_cache = {}
        self._fit()

    def refit(self) -> None:
        """내용이 바뀐 뒤 — 지난번 열별 추가 여백을 그대로 두고 다시 잰다."""
        self.set_content_widths(self._extra)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _measure_text(self, cell_fm: QFontMetrics, hdr_fm: QFontMetrics) -> dict[int, int]:
        """열별 텍스트 폭(패딩 제외) — 폰트 크기 하나에 대해 표 전체를 한 번 훑는다.

        결과는 '가장 넓은 글자의 폭'이라 전부 잴 필요가 없다. 같은 글자는 한 번만, 긴 글자부터
        재다가 `글자 수 × 가장 넓은 글자 폭`(그 글자가 가질 수 있는 최대 폭)이 이미 잰 값보다
        작아지면 멈춘다 — 그보다 짧은 글자는 더 넓을 수 없으니 답은 전부 잴 때와 같다.
        상대 전적 표(누적 상대 수천 명)에서 측정이 11.8만 번이었다(2026-10-02 프로파일)."""
        widths: dict[int, int] = {}
        # maxWidth 는 기본 글꼴 안에서만 — 한글·한자·이모지는 다른 글꼴에서 빌려 와 더 넓을 수 있다
        char_max = max([cell_fm.maxWidth(), 1] + [cell_fm.horizontalAdvance(ch) for ch in _WIDE_SAMPLES])
        rows = self.rowCount()
        for c in range(self.columnCount()):
            w = 0
            header_item = self.horizontalHeaderItem(c)
            if header_item:
                w = hdr_fm.horizontalAdvance(header_item.text())
            texts = set()
            for r in range(rows):
                item = self.item(r, c)
                if item:
                    texts.add(item.text())
            for t in sorted(texts, key=len, reverse=True):
                if len(t) * char_max <= w:
                    break
                w = max(w, cell_fm.horizontalAdvance(t))
            widths[c] = w
        return widths

    def _measure_pad(self) -> int:
        """Qt 가 계산한 열 크기 힌트 − 글자 폭 = 셀/헤더가 글자 외에 먹는 여백.
        셀과 헤더 중 큰 쪽을 모든 열에 같이 쓴다."""
        cell_fm = QFontMetrics(self.font())
        header = self.horizontalHeader()
        hdr_fm = QFontMetrics(header.font())
        # 정렬 화살표 자리는 따로 잰다(_sort_w). 채우는 동안(_fill)은 정렬이 꺼져
        # 있어 그대로 재면 화살표 자리가 빠져 "승률▾" 이 겹쳤고, 반대로 켜고 재면
        # 19열 전부에 15px 씩 붙어 선수 지표가 최소 글꼴로도 안 들어갔다.
        shown = header.isSortIndicatorShown()
        header.setSortIndicatorShown(False)
        pad = 0
        for c in range(self.columnCount()):
            if self.rowCount():
                tw = max((cell_fm.horizontalAdvance(self.item(r, c).text())
                          for r in range(self.rowCount()) if self.item(r, c)),
                         default=0)
                if tw:
                    pad = max(pad, self.sizeHintForColumn(c) - tw)
            hi = self.horizontalHeaderItem(c)
            if hi:
                pad = max(pad, header.sectionSizeHint(c)
                          - hdr_fm.horizontalAdvance(hi.text()))
        if self.columnCount():
            bare = header.sectionSizeHint(0)
            header.setSortIndicatorShown(True)
            self._sort_w = max(0, header.sectionSizeHint(0) - bare)
        header.setSortIndicatorShown(shown)
        return pad + self.PAD_SLACK

    def _estimate_total(self, cell_px: int) -> int:
        """텍스트 폭을 기준 크기 대비 선형 비례로 추정 — 후보 크기를 고르는
        용도라, 표를 다시 훑지 않고 캐시된 기준 폭에 비율만 곱한다."""
        scale = cell_px / self._base_cell_px
        return sum(int(w * scale) + self._pad + self._extra.get(c, 0)
                  for c, w in self._base_text_widths.items())

    def _entry(self, cell_px: int) -> dict:
        """글꼴 크기 하나에 대한 실측 열 폭·글꼴 — 크기별로 캐시."""
        if cell_px not in self._fit_cache:
            header_px = max(self.MIN_FONT_PX - 1,
                            self._base_header_px - (self._base_cell_px - cell_px))
            cell_font = QFont(self.font())
            cell_font.setPixelSize(cell_px)
            header_font = QFont(self.horizontalHeader().font())
            header_font.setPixelSize(header_px)
            text_widths = (self._base_text_widths if cell_px == self._base_cell_px
                          else self._measure_text(QFontMetrics(cell_font),
                                                  QFontMetrics(header_font)))
            sort_col = self.horizontalHeader().sortIndicatorSection()
            self._fit_cache[cell_px] = {
                "widths": {c: w + self._pad + self._extra.get(c, 0)
                          + (self._sort_w if c == sort_col else 0)
                          for c, w in text_widths.items()},
                "cell_font": cell_font,
                "header_font": header_font,
            }
        return self._fit_cache[cell_px]

    def _fit(self) -> None:
        if self.columnCount() == 0 or not self._base_text_widths:
            return
        avail = self.viewport().width()
        if avail <= 0:
            return
        cell_px = self._base_cell_px
        while (self._estimate_total(cell_px) > avail
              and cell_px > self.MIN_FONT_PX):
            cell_px -= 1
        # 추정은 글자 폭이 크기에 정비례한다고 보지만 실측은 조금 더 넓게 나온다 —
        # 표 폭을 3px 씩 훑으면 몇 px 넘쳐 가로 막대가 뜨는 폭이 있었다. 실측 합으로 한 번 더 깎는다.
        while (sum(self._entry(cell_px)["widths"].values()) > avail
               and cell_px > self.MIN_FONT_PX):
            cell_px -= 1
        entry = self._entry(cell_px)
        self.setFont(entry["cell_font"])
        self.horizontalHeader().setFont(entry["header_font"])
        widths = entry["widths"]
        total = sum(widths.values())
        if total <= 0:
            return
        if avail > total:
            extra = avail - total
            for c, w in widths.items():
                self.setColumnWidth(c, w + int(extra * (w / total)))
        else:
            # 최소 글꼴로도 넘치면 열마다 둔 여유(PAD_SLACK)만큼은 깎아 맞춘다 — 글자
            # 폭은 안 건드리므로 안 잘린다. 1280 폭 실데이터 선수 지표가 9px 에서 1px
            # 넘쳐 가로 막대가 떴다. 그보다 많이 넘치면 가로 스크롤로 둔다.
            cut = 0
            over = total - avail
            if 0 < over <= self.PAD_SLACK * len(widths):
                cut = -(-over // len(widths))
            for c, w in widths.items():
                self.setColumnWidth(c, w - cut)


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
        painter.setPen(QPen(QColor(T.GREEN), 1))
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

    def paintEvent(self, e):
        # QSS 로 ::drop-down 을 꾸미면 Fusion 이 화살표를 안 그린다 — 직접 그린다.
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(T.TEXT_DIM if self.isEnabled() else T.BORDER), 1.6))
        cx, cy = self.width() - 14, self.height() / 2
        p.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2),
                        QPointF(cx + 4, cy - 2)])
        p.end()


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

    def set_color(self, color: str) -> None:
        """연승/연패처럼 값에 따라 색이 바뀌어야 하는 카드용."""
        self.value.setStyleSheet(f"color: {color}; border: none;")


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
                f"QFrame {{ background: {T.PANEL_2}; border-radius: 10px; }}")
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

            val = FitLabel(NA, base_pt=30, min_pt=14)
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet(f"color: {T.TEXT}; border: none;")
            val.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Preferred)
            tv.addWidget(val)

            grid.addWidget(tile, r, col)
            self._vals[name] = val
            self._rows[name] = (tile, val)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
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
            self._head_name.setStyleSheet(f"color: {T.ON_ACCENT}; border: none;")
            self._head_grade.setStyleSheet(f"color: {T.ON_ACCENT}; border: none;")
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


class RatioBarRow(QWidget):
    """비율 한 줄 — 이름 · 적중/시도 · 비율 · 막대.

    BarRow 와 분모가 다르다. BarRow 는 "전체 골 중 이 유형이 몇 %"(분모=합계)라
    막대들을 더하면 100%가 되지만, 여기는 "이 유형으로 찼을 때 몇 %가 들어가나"
    (분모=그 칸의 시도)라 칸마다 독립이다. 그래서 막대도 각자 0~100%로 그린다.

    enough=False 면 비율을 숨기고 회색으로 낸다 — 2번 차서 1번 들어간 걸
    50%라고 적으면 사용자가 그걸 실력으로 읽는다.
    """

    def __init__(self, name: str, hit: int, total: int, color: str,
                 enough: bool = True, extra: str = ""):
        super().__init__()
        pct = (hit / total * 100) if total else 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        lb = QLabel(name)
        lb.setStyleSheet(f"color: {T.TEXT};")
        top.addWidget(lb)
        top.addStretch(1)
        if extra:
            ex = QLabel(extra)
            ex.setStyleSheet(f"color: {T.TEXT_DIM};")
            top.addWidget(ex)
            # 간격이 없으면 "xG 2.4" + "2/8" 이 "2.42/8" 한 숫자로 읽힌다.
            top.addSpacing(12)
        n = QLabel(f"{hit}/{total}")
        n.setStyleSheet(f"color: {T.TEXT_DIM};")
        top.addWidget(n)
        p = QLabel(f"{pct:.0f}%" if enough else NA)
        p.setStyleSheet(f"color: {color if enough else T.TEXT_DIM};"
                        f" font-weight: bold;")
        p.setFixedWidth(44)
        p.setAlignment(Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(p)
        v.addLayout(top)

        bar = QProgressBar()
        bar.setRange(0, 1000)
        # 표본 미달이면 막대도 채우지 않는다. 숫자는 "—"로 숨겨 놓고 막대만
        # 절반까지 그리면 결국 그 비율을 말하는 셈이라 앞뒤가 안 맞는다.
        bar.setValue(int(pct * 10) if enough else 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        chunk = color if enough else T.BORDER
        bar.setStyleSheet(
            f"QProgressBar {{ background: {T.PANEL_2}; border: none;"
            f" border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}")
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


class DivisionChart(QWidget):
    """등급(디비전) 추이 계단 그래프 — TrendChart 와 같은 방식(QPainter 직접).

    points: (라벨, divisionId) 리스트, 날짜 오름차순.
    names: divisionId -> 등급 이름(오픈API division 메타). 등급은 id 가
    작을수록 높다 — Y축은 위가 높은 등급이 되게 뒤집어 그린다.
    Y 레벨은 데이터에 나온 등급들만 쓴다(전체 18단계를 다 그리면 실제 변화
    폭이 눌려서 안 보인다)."""

    def __init__(self):
        super().__init__()
        self._points: list[tuple[str, int]] = []
        self._names: dict[int, str] = {}
        self.setMinimumHeight(220)

    def set_data(self, points: list[tuple[str, int]],
                 names: dict[int, str]) -> None:
        self._points = points
        self._names = names
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ml, mr, mt, mb = 96, 16, 14, 26  # 왼쪽 여백은 등급 이름이 들어갈 만큼
        plot_w = max(w - ml - mr, 1)
        plot_h = max(h - mt - mb, 1)
        p.fillRect(self.rect(), QColor(T.PANEL))

        font = p.font()
        font.setPointSize(9)
        p.setFont(font)

        if not self._points:
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                      "표시 구간에 등급 정보가 있는 경기가 없습니다.")
            return

        # id 내림차순 = 낮은 등급부터 → y 아래에서 위로
        levels = sorted({div for _, div in self._points}, reverse=True)
        y_of = {div: (mt + plot_h - (plot_h * i / max(len(levels) - 1, 1))
                      if len(levels) > 1 else mt + plot_h / 2)
                for i, div in enumerate(levels)}

        for div in levels:
            y = y_of[div]
            p.setPen(QColor(T.BORDER))
            p.drawLine(ml, int(y), w - mr, int(y))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(2, int(y) - 7, ml - 8, 14,
                      Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                      self._names.get(div, str(div)))

        n = len(self._points)

        def x_of(i: int) -> float:
            return ml + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

        pts = [QPointF(x_of(i), y_of[div])
               for i, (_, div) in enumerate(self._points)]

        # 계단형 — 등급은 경기 사이에 "서서히"가 아니라 딱 바뀌는 값이다
        p.setPen(QPen(QColor(T.YELLOW), 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, QPointF(b.x(), a.y()))
            p.drawLine(QPointF(b.x(), a.y()), b)

        p.setPen(QPen(QColor(T.PANEL), 1))
        p.setBrush(QColor(T.YELLOW))
        prev_div = None
        for pt, (_, div) in zip(pts, self._points):
            if div != prev_div:  # 등급이 바뀐 지점만 점을 찍는다 — 다 찍으면 떡칠
                p.drawEllipse(pt, 3.5, 3.5)
                prev_div = div

        p.setPen(QColor(T.TEXT_DIM))
        step = max(1, n // 6)
        label_y = h - mb + 6
        for i in range(0, n, step):
            p.drawText(int(pts[i].x()) - 24, label_y, 48, 14,
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
    """피치 위에 올라가는 선수 카드 한 장 — 얼굴 사진·포지션·강화·이름.

    클릭하면 그 선수 카드 상세(오버롤·능력치·시세 등)를 보여줄 수 있게
    clicked 시그널을 낸다 — 실제 조회·다이얼로그는 app_main 쪽 책임이라
    여기서는 "눌렸다"는 사실만 알린다."""

    clicked = pyqtSignal()

    def __init__(self, pos_name: str, name: str, grade, accent: str):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame {{ background: rgba(255,255,255,240); border: 2px solid {accent};"
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
            f"background: {accent}; color: {T.ON_ACCENT}; border: none;"
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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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

    player_clicked = pyqtSignal(int)  # spId — 선수 카드를 클릭했을 때

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
                chip.clicked.connect(lambda sid=sp_id: self.player_clicked.emit(sid))
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
        p.fillRect(self.rect(), QColor(T.PITCH))
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


class ShotMapWidget(QWidget):
    """슛 좌표를 공격 진영 하프 피치 위에 점으로 찍는다(골문이 위).

    좌표는 stats.Shot 의 x·y(0~1). x=1.0 이 골문 쪽이라 위로, y 가 폭(왼→오).
    슛은 x>0.45 근처에만 오므로 하프 피치만 그린다. 색은 골=초록·유효=노랑·
    빗나감=회색, 골대 맞은 슛은 흰 테두리로 표시. 골을 마지막에 그려 위로 얹는다.
    """

    X_MIN = 0.45  # 이보다 뒤(자기 진영)의 슛은 거의 없다 — 아래 경계로 삼는다.

    def __init__(self):
        super().__init__()
        self.setMinimumSize(560, 460)
        self._shots: list = []

    def set_shots(self, shots: list) -> None:
        self._shots = shots or []
        self.update()

    def _pt(self, x: float, y: float, w: int, h: int, m: int):
        dw, dh = w - 2 * m, h - 2 * m
        xf = max(self.X_MIN, min(1.0, x))
        vt = (1.0 - xf) / (1.0 - self.X_MIN)  # x=1 → 0(위), x=X_MIN → 1(아래)
        return QPointF(m + max(0.0, min(1.0, y)) * dw, m + vt * dh)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        m = 12
        p.fillRect(self.rect(), QColor(T.PITCH))
        line = QColor(255, 255, 255, 130)
        p.setPen(QPen(line, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(m, m, w - 2 * m, h - 2 * m)

        # 골문(위 중앙) · 페널티 박스 · 골 에어리어 · 페널티 스폿
        cx = w / 2
        goal_w = (w - 2 * m) * 0.18
        p.drawLine(int(cx - goal_w / 2), m, int(cx + goal_w / 2), m)
        pen_w = (w - 2 * m) * 0.62
        pen_h = (h - 2 * m) * 0.30
        p.drawRect(int(cx - pen_w / 2), m, int(pen_w), int(pen_h))
        ga_w = (w - 2 * m) * 0.30
        ga_h = (h - 2 * m) * 0.12
        p.drawRect(int(cx - ga_w / 2), m, int(ga_w), int(ga_h))
        p.setBrush(line)
        p.drawEllipse(QPointF(cx, m + (h - 2 * m) * 0.19), 2.5, 2.5)

        if not self._shots:
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "슛 기록 없음")
            return

        colors = {3: QColor(T.PITCH_GOAL), 1: QColor(T.YELLOW), 2: QColor(T.PITCH_MISS)}
        # 빗나감 → 유효 → 골 순으로 그려서 골이 맨 위에 오게 한다.
        order = sorted(self._shots, key=lambda s: {2: 0, 1: 1, 3: 2}.get(s.result, 0))
        for s in order:
            c = colors.get(s.result, QColor(T.TEXT_DIM))
            pt = self._pt(s.x, s.y, w, h, m)
            r = 6.0 if s.result == 3 else 4.5
            if s.hit_post:
                p.setPen(QPen(QColor("#ffffff"), 1.5))
            else:
                p.setPen(QPen(c.darker(160), 1))
            p.setBrush(c)
            p.drawEllipse(pt, r, r)


def wdl_text(w: int, d: int, l: int) -> str:
    return f"{w}승 {d}무 {l}패"


def rate_of(w: int, d: int, l: int) -> float:
    t = w + d + l
    return (w / t * 100) if t else 0.0
