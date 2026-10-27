"""화면 부품 — 랭커 카드 · 요약 카드 · 막대 그래프 행.

app_main 이 UI 흐름에 집중하도록 그리기 부품은 여기로 뺐다.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
화면 부품 — 랭커 카드, 표(`FitTableWidget`), 축구장 v2(`PitchWidget` — 2.1.1: `PitchCard` 줄 목록을 받아 줄마다 폭을 사람 수로 나눈다 ·
칩은 QPainter 로 크기에 맞춰 그리고 작아지면 얼굴 → 시세 줄 순으로 뺀다 · 위 띠 = 스쿼드 가치·급여 + 자동 읽기가 막힌 이유와 [안내 보기].
OVR·시세는 화면이 `set_card` 로 — 위젯은 계산 안 한다) ·
접는 구획(`Collapsible`), 좁으면 접히는 바(`WrapBar`)·페이지 안 탭(`PageTabs` — 숨기기/보이기라 숨은 탭이 최소 폭을 안 넓힌다 ·
탭줄은 `Card.add_to_title_row` 로 제목 옆)·세로 스크롤 틀(`VScrollArea`)·줄어드는 라벨(`FitLabel`) 등

공통 표현 부품(3.1.1 · 64단계): 승·무·패 세 칸 막대 `WdlBar`(승률 한 값 막대는 `win_rate_bar` 그대로) ·
평균 대비 `delta_text`(▲▼ + 색 — 색만으로 말하지 않는다)·표 칸은 `delta_item` · 숫자 카드 `StatCard`(라벨 → 숫자 → 보조 → 변화) ·
태그 `Tag`/`tag_qss`(그 색 글자 + 10% 바탕 — 켜진 결과 필터도 이 꼴). 새 글자/바탕 짝은 test_ui_smoke 대비 표에 한 줄.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QListView, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QToolButton, QToolTip, QVBoxLayout, QWidget,
)  # QGridLayout: 랭커 카드 표, QSizePolicy: 값 칸 가로 확장

import config
import core_api as core
import theme as T

NA = "—"  # API 가 안 주는 값. 그럴싸한 숫자를 지어 넣지 않는다.

# 표 칸 그림 키가 사는 자리 — UserRole(선수 칸의 spId, 더블클릭이 읽는다)·UserRole+1(흐림 표시)을 건드리지 않는다
ICON_KEY_ROLE = Qt.ItemDataRole.UserRole + 2


class IconCell(NamedTuple):
    """_fill 의 행 데이터에 쓰는 "그림이 붙을 칸" — 글자·정렬 값은 그대로, icon_key 는 그림 종류별 키(set_icon_columns 로
    등록된 열의 종류 기준). 2-튜플 (글자, 정렬값) 은 그대로 쓴다 — 이 형만 분기 하나."""
    text: str
    sort_key: object
    icon_key: object
    name: str = ""   # 특성 칸 — 글자는 "NN%" 만이라 이름은 툴팁·NAME_ROLE 로(TraitIconDelegate 가 그림 없을 때 대신 쓴다)


class UpdateCard(QFrame):
    """창 오른쪽 아래에 떠 있는 '새 버전' 카드 — 런처의 업데이트 알림처럼.

    어느 화면 위에든 같은 자리에 뜬다(검색 화면·메인 화면). 위치는 부모가
    place() 로 정한다. 내려받는 동안은 버튼 대신 진행 막대가 보인다."""
    update_clicked = pyqtSignal()
    dismissed = pyqtSignal()
    MARGIN = 20
    WIDTH = 300  # 고정 — 글자 길이에 따라 줄면 내려받는 중 문구가 잘렸다
    # 새 버전이면 [업데이트](어느 화면에서든), 최신이면 "최신 버전입니다"(첫 검색 화면에서만 — 메인 내용을 가린다).
    # 어느 쪽이든 사용자가 닫을 때까지 둔다 — 6초 뒤 저절로 닫았더니 사용자가 못 봤다.
    # 확인을 못 했으면 안 띄운다(왼쪽 아래 상태 칸이 "업데이트 확인 못 함"을 보인다).

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("updateCard")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(
            f"QFrame#updateCard {{ background: {T.PANEL}; border: 1px solid {T.ACCENT};"
            f" border-radius: {T.RADIUS}px; }} QLabel {{ border: none; }}")
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
        self.mode = ""  # "release"(새 버전) · "latest"(최신) — 최신 카드는 메인 화면에서 닫힌다
        self.hide()

    def show_release(self, tag: str, current: str, installed: bool) -> None:
        self.mode = "release"
        self.lb_title.setText(f"새 버전 {tag}")
        self.lb_sub.setText(f"지금 {current}" + ("" if installed else " · 받기 페이지가 열립니다"))
        self.btn_update.setText("업데이트" if installed else "받으러 가기")
        self.btn_later.setText("나중에")
        self.set_busy(False)
        self.show()
        self.raise_()
        self.place()

    def show_latest(self, current: str) -> None:
        self.mode = "latest"
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
                self._row1.addWidget(self._sep)
                self._sep.show()
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


class _TabBar(QWidget):
    """PageTabs 의 탭줄 — 한 줄에 안 들어가면 탭 글자를 줄이지 않고 여러 줄로 접는다(WrapBar 와 같은 원리:
    폭만 보고 판정하니 왔다 갔다 하지 않는다). 최소 폭은 가장 넓은 탭 하나 — 탭줄이 창 최소 폭을 묶지 않게."""

    def __init__(self):
        super().__init__()
        self._btns: list[QPushButton] = []
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(6)
        self._rows_now: list[list[int]] | None = None

    def add(self, btn: QPushButton) -> None:
        self._btns.append(btn)
        self._rows_now = None
        self._arrange(self.width() if self.isVisible() else 10 ** 6)

    def _rows_for(self, width: int) -> list[list[int]]:
        gap = 6
        rows: list[list[int]] = [[]]
        used = 0
        for i, b in enumerate(self._btns):
            w = b.sizeHint().width()
            if rows[-1] and used + gap + w > width:
                rows.append([])
                used = 0
            used += (gap if rows[-1] else 0) + w
            rows[-1].append(i)
        return rows

    def _arrange(self, width: int) -> None:
        rows = self._rows_for(width)
        if rows == self._rows_now:
            return
        self._rows_now = rows
        while self._v.count():
            item = self._v.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    lay.takeAt(0)
                lay.deleteLater()
        for row in rows:
            h = QHBoxLayout()
            h.setSpacing(6)
            for i in row:
                h.addWidget(self._btns[i])
            h.addStretch(1)
            self._v.addLayout(h)
        self.updateGeometry()

    def row_count(self) -> int:
        return len(self._rows_now or [])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange(self.width())

    def minimumSizeHint(self) -> QSize:
        w = max((b.sizeHint().width() for b in self._btns), default=0)
        return QSize(w, super().minimumSizeHint().height())


class PageTabs(QWidget):
    """페이지 안 탭(2.1.1) — 탭줄 + 지금 탭의 내용 하나만 보인다.

    QStackedWidget 을 안 쓰고 숨기기/보이기로 바꾼다 — QStackedWidget 은 모든 페이지의 최소 크기 최댓값을 알려,
    넓은 표가 있는 탭 하나가 옆 탭까지 가로로 넓힌다(크기 정책 Ignored 를 따로 걸어야 한다). 숨은 위젯은
    레이아웃 크기 계산에서 빠지므로 그 함정이 처음부터 없다."""

    changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._names: list[str] = []
        self._pages: list[QWidget] = []
        self._btns: list[QPushButton] = []
        self._cur = -1
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        self.bar = _TabBar()
        v.addWidget(self.bar)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self._body, 1)

    def add_tab(self, name: str, page: QWidget) -> int:
        i = len(self._pages)
        btn = QPushButton(name)
        btn.setObjectName("pageTab")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, i=i: self.set_current(i))
        self._names.append(name)
        self._pages.append(page)
        self._btns.append(btn)
        self.bar.add(btn)
        page.setVisible(False)
        self._body.addWidget(page, 1)
        if self._cur < 0:
            self.set_current(0, emit=False)
        return i

    def take_bar(self) -> _TabBar:
        """탭줄을 이 위젯 밖(카드 제목 줄 — Card.add_to_title_row)에 놓을 때. 신호·고르기는 그대로 여기서."""
        self.layout().removeWidget(self.bar)
        return self.bar

    def names(self) -> list[str]:
        return list(self._names)

    def page(self, i: int) -> QWidget:
        return self._pages[i]

    def current_index(self) -> int:
        return self._cur

    def current_name(self) -> str | None:
        return self._names[self._cur] if 0 <= self._cur < len(self._names) else None

    def set_current(self, which: int | str, emit: bool = True) -> bool:
        """이름이나 번호로 고른다 — 없는 이름이면 아무것도 안 하고 False."""
        i = self._names.index(which) if isinstance(which, str) and which in self._names else which
        if not isinstance(i, int) or not 0 <= i < len(self._pages):
            return False
        for j, b in enumerate(self._btns):
            b.setChecked(j == i)
        if i == self._cur:
            return True
        if self._cur >= 0:
            self._pages[self._cur].setVisible(False)
        self._cur = i
        self._pages[i].setVisible(True)
        if emit:
            self.changed.emit(i)
        return True


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

    def __init__(self, title: str = "", margin: int = 18):
        super().__init__()
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(margin, margin - 4, margin, margin)
        v.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        # 레이아웃에 넣기 전에 보이면 부모 없는 QLabel 이 혼자 창으로 뜬다(켤 때 작은 창 깜빡임 · 2.13.3)
        v.addWidget(self.title)
        self.title.setVisible(bool(title))
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.body, 1)

    def add_to_title_row(self, w: QWidget) -> None:
        """제목 줄 오른쪽에 붙인다(페이지 안 탭줄) — 제목 아래 따로 두면 그 높이만큼 작은 화면에서 세로 막대가
        생겨 표 폭이 줄었다(2.1.1 — FHD 150% 최소 높이에서 랭커 비교 표 머리글이 2px 잘림)."""
        v = self.layout()
        i = v.indexOf(self.title)
        v.removeWidget(self.title)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self.title)
        row.addWidget(w, 1)
        v.insertLayout(i, row)


class Collapsible(QWidget):
    """누르면 접히고 펴지는 구획(P9) — 머리 한 줄(왼쪽 흐린 제목 · 오른쪽 흐린 12px "펼치기 ▾"/"접기 ▴") + 본문.
    제목·오른쪽 글 어느 쪽을 눌러도 같다 · 올리면 글자가 밝아진다. 접힌 본문은 숨김이라 크기 계산에서 빠진다."""

    HINT = {False: "펼치기 ▾", True: "접기 ▴"}

    def __init__(self, title: str, body: QWidget, opened: bool = False):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        qss = f"QToolButton {{ border: none; color: {T.TEXT_DIM}; }} QToolButton:hover {{ color: {T.TEXT}; }}"
        self.head = QToolButton()
        self.head.setText(title)
        self.head.setCheckable(True)
        self.head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.head.setStyleSheet(qss)
        bold = QFont(self.head.font())
        bold.setBold(True)
        self.head.setFont(bold)
        self.hint = QToolButton()
        self.hint.setStyleSheet(qss)
        small = QFont(self.hint.font())
        small.setPixelSize(12)
        self.hint.setFont(small)
        for b in (self.head, self.hint):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hint.clicked.connect(self.head.toggle)
        self.head.toggled.connect(self.set_open)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.head)
        row.addStretch(1)
        row.addWidget(self.hint)
        self.body = body
        v.addLayout(row)
        v.addWidget(body)
        self.head.setChecked(opened)
        self.set_open(opened)

    def set_open(self, on: bool) -> None:
        self.hint.setText(self.HINT[on])
        self.body.setVisible(on)

    def is_open(self) -> bool:
        return self.body.isVisibleTo(self)


# 한 글자 폭의 상한을 잡을 때 같이 재는 넓은 글자 — 기본 글꼴 밖(대체 글꼴)에서 오는 종류마다 하나씩
_WIDE_SAMPLES = ("가", "뷁", "漢", "W", "M", "@", "%", "Ⅲ", "😀", "★", "■")


_GAP_CACHE: dict[tuple, int] = {}


def measure_icon_gap(owner: QWidget, w: int, h: int) -> int:
    """그림이 든 칸이 글자만 든 칸보다 몇 px 더 넓어야 하나 — 그림 폭 + Qt 가 아는 간격(실측 6).
    상수 대신 1행 표에 가짜 그림을 넣어 sizeHintForColumn 차이를 묻는다(스타일·글꼴이 바뀌어도 맞게)."""
    key = (w, h, owner.font().pixelSize(), owner.font().pointSizeF(), owner.style().objectName())
    if key not in _GAP_CACHE:
        probe = QTableWidget(1, 1)
        probe.setIconSize(QSize(w, h))
        probe.setItem(0, 0, QTableWidgetItem("가"))
        bare = probe.sizeHintForColumn(0)
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        probe.item(0, 0).setIcon(QIcon(pm))
        _GAP_CACHE[key] = probe.sizeHintForColumn(0) - bare
        probe.deleteLater()
    return _GAP_CACHE[key]


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
        self._user_extra: dict[int, int] = {}   # set_content_widths 로 준 열별 여백(범위 막대·배지 등)
        self._icon_cols: dict[int, str] = {}    # set_icon_columns — 열 → 그림 종류
        self._icon_extra: dict[int, int] = {}   # 등록 열의 그림 폭 + Qt 가 아는 간격
        self._stacked_cols: list[int] = []      # 글자 위에 그림을 쌓아 그리는 열(특성) — 행 높이가 글꼴을 따라간다
        self._extra: dict[int, int] = {}        # 둘을 합친 값 — 폭 계산은 이것만 읽는다
        self._base_cell_px = 14
        self._base_header_px = 13
        self._base_text_widths: dict[int, int] = {}  # 기준 폰트 크기에서 잰 열별 텍스트 폭
        self._fit_cache: dict[int, dict[int, int]] = {}  # cell_px -> 최종 열 너비(+padding+extra)
        self._pad = 0  # 열마다 얹는 여백 — set_content_widths 때 _measure_pad 로 잰다
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
        self._user_extra = dict(extra or {})
        self._merge_extra()
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
        """내용이 바뀐 뒤 — 지난번 열별 추가 여백을 그대로 두고 다시 잰다. 그림 열 여백은 등록이 따로 갖고 있다."""
        self.set_content_widths(self._user_extra)

    # ── 표 칸 그림(40단계) ──────────────────────────────────────────────
    def _merge_extra(self) -> None:
        merged = dict(self._user_extra)
        for c, w in self._icon_extra.items():
            merged[c] = merged.get(c, 0) + w
        self._extra = merged

    def set_icon_columns(self, cols: dict[int, str]) -> None:
        """그림이 들어갈 열을 표를 만들 때 한 번 등록한다({열: 그림 종류}).

        그림이 받아지기 **전부터** 그 열 폭을 잡아 둔다(받은 뒤 열이 넓어지며 흔들리지 않게). 간격은 상수가 아니라
        Qt 에 물은 값(PyQt 4)이라, 등록 때 표가 비어 있어도 따로 만든 1행 표로 잰다. 열 여백은 set_content_widths 가 준 값과
        **합쳐서** 쓴다 — 갈아 끼우지 않는다(범위 막대·강화 배지 여백이 사라지지 않게)."""
        self._icon_cols = dict(cols)
        sizes = {c: self.icon_px_for(k) for c, k in cols.items()}
        widths = {c: w for c, (w, _h) in sizes.items()}
        self._icon_w = max(widths.values(), default=0)
        icon_h = max((h for _w, h in sizes.values()), default=0)
        self.setIconSize(QSize(self._icon_w, icon_h))
        # 아이콘 사각형(setIconSize)은 표 하나라 가장 넓은 종류에 맞추지만, 열 여백은 그 열 그림 폭 + 간격만 — 정사각 배지 열이
        # 국기 폭(33)만큼 넓어지지 않게(구단주 표의 등급 열)
        gap = measure_icon_gap(self, self._icon_w, icon_h) - self._icon_w if cols else 0
        # 특성(trait·train)은 글자 옆이 아니라 **위**에 그리는 칸(TraitIconDelegate) — 열 여백은 더하지 않고(글자 폭이 곧 칸 폭,
        # 여백 0 이라도 _extra 에는 넣어 _measure_pad 가 이 열을 건너뛴다) 행 높이만 그림 + 글자 한 줄로 키운다
        stacked = [c for c, k in cols.items() if k in config.STACKED_KINDS]
        self._stacked_cols = stacked
        self._icon_extra = {c: (0 if c in stacked else widths[c] + gap) for c in cols}
        for c in stacked:
            self.setItemDelegateForColumn(c, TraitIconDelegate(self, cols[c]))
        if stacked:
            self.verticalHeader().setDefaultSectionSize(TraitIconDelegate.row_height(self.fontMetrics()))
        self._merge_extra()

    @staticmethod
    def icon_px_for(kind: str) -> tuple[int, int]:
        """그 종류 그림의 (폭, 높이) — 높이는 종류별 상수(config.ICON_HEIGHT · 없으면 ICON_PX), 폭은 가로세로 비율."""
        h = config.ICON_HEIGHT.get(kind, config.ICON_PX)
        return round(h * config.ICON_ASPECT.get(kind, 1.0)), h

    @property
    def icon_columns(self) -> dict[int, str]:
        return self._icon_cols

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
            if self.isColumnHidden(c):
                continue   # 숨긴 열(57 K3 상대 점수)은 폭 계산에서 뺀다 — 넣으면 보이는 열이 그만큼 좁아진다
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
        # 정렬 화살표 자리는 따로 잰다(_arrow_w). 채우는 동안(_fill)은 정렬이 꺼져
        # 있어 그대로 재면 화살표 자리가 빠져 "승률▾" 이 겹쳤고, 반대로 켜고 재면
        # 19열 전부에 15px 씩 붙어 선수 지표가 최소 글꼴로도 안 들어갔다.
        shown = header.isSortIndicatorShown()
        header.setSortIndicatorShown(False)
        pad = 0
        for c in range(self.columnCount()):
            if c in self._extra or self.isColumnHidden(c):
                continue   # 그림 때문에 sizeHint 가 커진 열을 재면 그 여백이 모든 열에 얹힌다
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
        header.setSortIndicatorShown(shown)
        return pad + self.PAD_SLACK

    def _arrow_w(self, header_font: QFont) -> int:
        """그 머리글 글꼴에서 정렬 화살표가 먹는 폭. Qt 는 화살표 자리를 머리글 높이(= 글꼴)에 비례해 잡는다 —
        예전엔 한 번 잰 값(그때의 글꼴)을 모든 크기에 써서, 큰 글꼴일 때 잰 값이면 작은 글꼴에서 남고 반대면
        모자랐다(2.1.1 — 탭으로 옮기며 재는 순서가 바뀌자 1264×480 랭커 비교 '포지션▲' 이 2px 잘림)."""
        if not self.columnCount():
            return 0
        header = self.horizontalHeader()
        saved, shown = header.font(), header.isSortIndicatorShown()
        header.setFont(header_font)
        header.setSortIndicatorShown(False)
        bare = header.sectionSizeHint(0)
        header.setSortIndicatorShown(True)
        arrow = header.sectionSizeHint(0)
        header.setSortIndicatorShown(shown)
        header.setFont(saved)
        return max(0, arrow - bare)

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
            arrow = self._arrow_w(header_font)
            self._fit_cache[cell_px] = {
                "widths": {c: w + self._pad + self._extra.get(c, 0)
                          + (arrow if c == sort_col else 0)
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
        if self._stacked_cols:   # 글꼴이 줄면 글자 줄도 낮아진다 — 행 높이를 지금 글꼴로 다시
            self.verticalHeader().setDefaultSectionSize(TraitIconDelegate.row_height(self.fontMetrics()))
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
        painter.setPen(QPen(QColor(T.ACCENT), 1))
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


class BoldColumnDelegate(RowBorderDelegate):
    """그 열 글자를 굵게(65 M9 선수 지표 승률) — 칸에 글꼴을 박으면 FitTableWidget 이 글꼴을 줄일 때 그 칸만 큰 채로 남아
    잘린다(1280 폭 실측). 대리자는 그릴 때마다 표의 지금 글꼴에서 굵게 한다. 폭(sizeHint)은 보통 글꼴로 알린다 —
    _measure_pad 가 열마다 잰 여백 중 가장 큰 값을 모든 열에 쓰므로 굵은 만큼은 쓰는 쪽이 그 열에만 여백으로 준다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plain = False

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if not self._plain:
            option.font.setBold(True)

    def sizeHint(self, option, index):
        self._plain = True
        try:
            return super().sizeHint(option, index)
        finally:
            self._plain = False


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


class NoScrollSpinBox(QSpinBox):
    """휠 스크롤로 값이 바뀌는 사고 방지(NoScrollComboBox 와 같은 까닭 — 57 K1 경기 수 직접 지정)."""

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
        # 표본이 작아 흐린 행 — 어느 열·어느 방향으로 정렬해도 맨 아래로(2.2.1 팀컬러 표: 상대 2명 +25%p 가 맨 위로 오지 않게).
        # Qt 는 내림차순을 같은 비교를 뒤집어(other < self) 쓰므로 지금 방향을 머리글에서 읽어 맞춘다
        self.sink = False

    def __lt__(self, other):
        if isinstance(other, SortableItem) and self.sink != other.sink:
            table = self.tableWidget()
            desc = table is not None and \
                table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
            return self.sink if desc else other.sink
        if isinstance(other, SortableItem) and self._key is not None \
                and getattr(other, "_key", None) is not None:
            return self._key < other._key
        return super().__lt__(other)


# 팀가치 범위 막대(2.2.1) 칸의 값 — (최저, 평균, 최고) 원 단위. 축은 델리게이트가 쥔다(표의 전체 행 기준)
RANGE_ROLE = Qt.ItemDataRole.UserRole + 7


class RangeBarDelegate(RowBorderDelegate):
    """최저~최고 막대 + 평균 점, **로그 축** — 구단가치가 자릿수로 벌어져(수천억~수경) 선형이면 대부분이 한 점이 된다.
    축(lo, hi)은 set_axis 로 — 필터로 줄을 숨겨도 막대 길이가 안 변하게 표의 전체 행에서 잰 값을 준다.
    흐린 줄은 SortableItem.sink 를 보고 흐린 색으로."""

    PAD = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lo = self.hi = None

    def set_axis(self, lo: int | None, hi: int | None) -> None:
        self.lo, self.hi = lo, hi

    def x_of(self, v: float, left: float, width: float) -> float:
        import math
        if not self.lo or not self.hi or self.hi <= self.lo:
            return left + width / 2
        a, b = math.log10(self.lo), math.log10(self.hi)
        return left + (math.log10(max(v, self.lo)) - a) / (b - a) * width

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        val = index.data(RANGE_ROLE)
        if not val or val[0] is None:
            return
        lo, avg, hi = val
        table = self.parent()
        item = table.item(index.row(), index.column()) if isinstance(table, QTableWidget) else None
        dim = bool(getattr(item, "sink", False))
        r = option.rect.adjusted(self.PAD, 0, -self.PAD, 0)
        y = option.rect.top() + option.rect.height() / 2
        x0, x1, xa = (self.x_of(v, r.left(), r.width()) for v in (lo, hi, avg))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(T.TEXT_DIM if dim else T.CHART_NEUTRAL), 4, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x0, y), QPointF(max(x1, x0 + 0.5), y))
        painter.setPen(QPen(QColor(T.CHART_ON_MARK), 1.5))
        painter.setBrush(QColor(T.TEXT_DIM if dim else T.CHART_UP))
        painter.drawEllipse(QPointF(xa, y), 4.5, 4.5)
        painter.restore()


class StatCard(QFrame):
    """숫자 카드(P5) — 작은 라벨 → 큰 숫자 → 흐린 보조 줄 → 변화(▲▼). 보조·변화는 비어 있으면 숨는다."""

    def __init__(self, title: str, color: str = T.TEXT):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: {T.RADIUS}px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(2)

        self.cap = QLabel(title)
        cf = QFont()
        cf.setPixelSize(12)
        self.cap.setFont(cf)
        self.cap.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")

        self.value = QLabel("-")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.value.setFont(f)
        self.value.setStyleSheet(f"color: {color}; border: none;")

        self.sub = QLabel()
        self.sub.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        self.sub.hide()
        self.delta = QLabel()
        self.delta.setTextFormat(Qt.TextFormat.RichText)
        self.delta.setStyleSheet("border: none;")
        self.delta.hide()

        for lb in (self.cap, self.value, self.sub, self.delta):
            v.addWidget(lb)
        v.addStretch(1)

    def set(self, text: str) -> None:
        self.value.setText(text)

    def set_sub(self, text: str) -> None:
        self.sub.setText(text)
        self.sub.setVisible(bool(text))

    def set_delta(self, v: float | None, base: float | None, unit: str = "%p", label: str = "") -> None:
        """기준(base) 대비 변화 줄 — 둘 중 하나라도 없으면 숨긴다. label 은 뒤에 붙는 흐린 말("평균 대비")."""
        if v is None or base is None:
            self.delta.setText("")
            self.delta.setProperty("delta_color", None)
            self.delta.hide()
            return
        text, color = delta_text(v, base, unit)
        self.delta.setText(f"<span style='color:{color}'>{text}</span>"
                           + (f" <span style='color:{T.TEXT_DIM}'>{label}</span>" if label else ""))
        self.delta.setProperty("delta_color", color)
        self.delta.show()

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

    큰 숫자를 타일로 나열해 게임 레벨업 화면처럼. 타일 순서: 순위 · 점수 순으로 눈에 먼저
    들어오게, 전적·구단가치는 아래.
    """

    ROWS = ["순위", "전적", "구단가치", "점수"]
    TILE_ORDER = ["순위", "점수", "전적", "구단가치"]

    def __init__(self):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: {T.RADIUS}px; }}")
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

        # 내 팀컬러 엠블럼 · 주 포메이션(40단계) — 이미 아는 값에 그림만. 없으면 이 줄 전체가 숨는다
        self._head_icons = QWidget()
        icons_h = QHBoxLayout(self._head_icons)
        icons_h.setContentsMargins(0, 0, 0, 0)
        icons_h.setSpacing(10)
        icons_h.addStretch(1)
        self._head_emblem = QLabel()
        self._head_pitch = QLabel()
        for lb in (self._head_emblem, self._head_pitch):
            lb.setStyleSheet("border: none; background: transparent;")
            lb.setVisible(False)
            icons_h.addWidget(lb)
        icons_h.addStretch(1)
        self._head_icons.setStyleSheet("border: none; background: transparent;")
        self._head_icons.setVisible(False)
        head_v.addWidget(self._head_icons)

        # 역대 최고 등급(1.2.1) — 타일이 아니라 머리 아래 한 줄. 랭커/비랭커 두 모드 모두 같은 자리에 보이고
        # (set_mode 가 숨기지 않는다) 값이 없으면 이 줄만 숨긴다.
        self._head_best = QLabel("")
        self._head_best.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._head_best.setVisible(False)
        self._head_best.setToolTip("감독모드에서 올라가 본 가장 높은 티어(시즌과 무관)와,\n"
                                   "그 티어에 가장 최근에 올라선 날 — 넥슨 공식 기록")
        head_v.addWidget(self._head_best)

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
                f"QFrame {{ background: {T.PANEL_2}; border-radius: {T.RADIUS}px; }}")
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
                f"QFrame {{ background: {T.PRIMARY}; border: none;"
                f" border-top-left-radius: {T.RADIUS}px; border-top-right-radius: {T.RADIUS}px; }}")
            self._head_name.setStyleSheet(f"color: {T.ON_PRIMARY}; border: none;")
            self._head_grade.setStyleSheet(f"color: {T.ON_PRIMARY}; border: none;")
            self._head_best.setStyleSheet(f"color: {T.ON_PRIMARY}; border: none;")
        else:
            self._head.setStyleSheet(
                f"QFrame {{ background: {T.PANEL_2}; border: none;"
                f" border-top-left-radius: {T.RADIUS}px; border-top-right-radius: {T.RADIUS}px; }}")
            self._head_name.setStyleSheet(f"color: {T.TEXT}; border: none;")
            self._head_grade.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
            self._head_best.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        for name in ("순위", "구단가치", "점수"):
            tile, _ = self._rows[name]
            tile.setVisible(is_ranker)

    def set_name(self, text: str) -> None:
        """헤더 안 이름·레벨 줄."""
        self._head_name.setText(text)

    def set_best(self, text: str | None) -> None:
        """역대 최고 등급 줄 — 없으면(None·빈 글자) 숨긴다."""
        self._head_best.setText(text or "")
        self._head_best.setVisible(bool(text))

    def _sync_icons_row(self) -> None:
        self._head_icons.setVisible(not self._head_emblem.isHidden() or not self._head_pitch.isHidden())

    def set_formation(self, pixmap: QPixmap | None, tip: str = "") -> None:
        """주 포메이션 작은 축구장. 그림이 없으면(None·빈 그림) 숨긴다."""
        ok = pixmap is not None and not pixmap.isNull()
        if ok:
            self._head_pitch.setPixmap(pixmap)
        else:
            self._head_pitch.clear()
        self._head_pitch.setToolTip(tip if ok else "")
        self._head_pitch.setVisible(ok)
        self._sync_icons_row()

    def set_emblem(self, path: str | None, tip: str = "") -> None:
        """내 팀컬러 엠블럼(받은 파일 경로). 없거나 깨졌으면 숨긴다 — 글자 자리는 두지 않는다(이름은 다른 화면에 있다)."""
        pm = QPixmap(path) if path else QPixmap()
        ok = not pm.isNull()
        if ok:
            h = config.EMBLEM_HEAD_PX
            self._head_emblem.setPixmap(pm.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation))
        else:
            self._head_emblem.clear()
        self._head_emblem.setToolTip(tip if ok else "")
        self._head_emblem.setVisible(ok)
        self._sync_icons_row()

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


def formation_key(formation: str) -> str:
    """"4-1-2-3-0"(내 경기 · 0 줄 포함 고정 5줄)과 "4-1-2-3"(랭커 · 줄 수가 바뀜)을 같은 모양으로 — 0 줄은 버린다.
    그릴 수 없으면 ""."""
    parts = str(formation or "").split("-")
    if len(parts) < 2 or not all(x.isdigit() for x in parts):   # "4-2-3-1 계열"·"모름" 같은 글자는 모양이 아니다
        return ""
    lines = [int(x) for x in parts if int(x) > 0]
    return "-".join(str(n) for n in lines) if lines else ""


def formation_pixmap(formation: str, px: int | None = None, dpr: float | None = None, positions=()) -> QPixmap:
    """작은 축구장 — 포메이션 줄마다 점. 수비 줄이 아래, 공격 줄이 위, 맨 아래 GK 한 점. 네트워크 없음 · 같은 모양이면 같은 캐시.
    positions(경기 자리 코드 — FormationStat.positions)를 주면 줄 인원을 고르게 펴지 않고 **그 자리**에 찍는다
    (core.PITCH_X · PITCH_Y). 자리를 모르는 길(랭커 포메이션 글자)은 줄 인원으로. 그릴 수 없는 글자("모름" 등)면 빈 QPixmap(isNull)."""
    from PyQt6.QtGui import QGuiApplication, QPixmapCache
    key = formation_key(formation)
    if not key:
        return QPixmap()
    px = px or config.FORMATION_PX
    if dpr is None:
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
    spots = [c for c in positions if c in core.PITCH_X and c in core.PITCH_Y]
    if spots:
        return _formation_spots_pixmap(tuple(sorted(spots)), px, dpr)
    ck = f"formation:{key}:{px}:{dpr}"
    hit = QPixmapCache.find(ck)
    if hit is not None and not hit.isNull():
        return hit
    lines = [int(x) for x in key.split("-")]
    w = round(px * config.ICON_ASPECT.get("formation", 1.0))
    pm = QPixmap(round(w * dpr), round(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _formation_board(p, w, px)
    rows = len(lines) + 1                      # GK + 줄들
    r = max(1.1, px / 15)
    pad_y = r + 1.5
    for i in range(rows):
        count = 1 if i == 0 else lines[i - 1]  # i=0 은 GK(맨 아래), 그 위로 수비 → 공격
        y = px - pad_y - (px - 2 * pad_y) * i / max(1, rows - 1)
        color = T.FORMATION_LINE_COLORS[-1] if i == 0 else _formation_band_color(i - 1, len(lines))
        for j in range(count):
            _formation_dot(p, QPointF(w * (j + 1) / (count + 1), y), r, color, px)
    p.end()
    QPixmapCache.insert(ck, pm)
    return pm


FORMATION_SPREAD = 1.5   # 포메이션 아이콘의 가로 넓힘(가운데 0.5 에서) — 1.0 이면 큰 축구장과 같은 좌표


def _formation_band_color(i: int, n: int) -> str:
    """줄 인원 그림의 i 번째 줄(0 = 수비) 색 — 줄이 n 개면 수비~공격 다섯 띠에 고르게 놓는다(4줄 = 수비·수미·공미·공격).
    랭커 포메이션 글자는 빈 줄(0)을 안 싣고 내 경기와 같은 키를 쓰므로 정확한 띠를 모른다 — 그림 속 높이를 따른다."""
    bands = len(T.FORMATION_LINE_COLORS) - 1          # GK 뺀 다섯
    k = 0 if n <= 1 else round(i * (bands - 1) / (n - 1))
    return T.FORMATION_LINE_COLORS[bands - 1 - min(bands - 1, k)]


def _formation_board(p: QPainter, w: float, h: float) -> None:
    """작은 축구장 판 — 위→아래 그라데이션, 클 때만(config.FORMATION_LINES_FROM_PX) 테두리·하프라인·센터서클·두 박스."""
    from PyQt6.QtGui import QLinearGradient
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(T.FORMATION_PITCH_TOP))
    g.setColorAt(1, QColor(T.FORMATION_PITCH_BOTTOM))
    rad = max(2.0, w * 0.08)
    p.setBrush(g)
    if h < config.FORMATION_LINES_FROM_PX:
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), rad, rad)
        return
    line = QColor(T.FORMATION_LINE)
    thick = max(0.8, w / 60)
    p.setPen(QPen(line, thick))
    p.drawRoundedRect(QRectF(thick / 2, thick / 2, w - thick, h - thick), rad, rad)
    p.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(line, thick / 2))
    p.drawEllipse(QPointF(w / 2, h / 2), w * 0.11, w * 0.11)
    bw, bh = w * 0.6, h * 0.156
    p.drawRect(QRectF((w - bw) / 2, 0, bw, bh))
    p.drawRect(QRectF((w - bw) / 2, h - bh, bw, bh))


def _formation_dot(p: QPainter, at: QPointF, r: float, color: str, h: float) -> None:
    """점 하나 — 테두리(검정 40%)는 선과 같이 큰 그림에서만. 표 칸(r 1.3px)에선 테두리가 점 색을 덮는다."""
    if h < config.FORMATION_LINES_FROM_PX:
        p.setPen(Qt.PenStyle.NoPen)
    else:
        p.setPen(QPen(QColor(T.FORMATION_DOT_EDGE), max(0.4, r * 0.12)))
    p.setBrush(QColor(color))
    p.drawEllipse(at, r, r)


def _formation_spots_pixmap(spots: tuple, px: int, dpr: float) -> QPixmap:
    """formation_pixmap 의 자리 그림 — 점마다 그 자리 좌표(위 = 최전방) · 색은 그 자리의 줄(core.PITCH_ROWS). 같은 자리 묶음이면 같은 캐시."""
    from PyQt6.QtGui import QPixmapCache
    ck = f"formation-spots:{','.join(map(str, spots))}:{px}:{dpr}"
    hit = QPixmapCache.find(ck)
    if hit is not None and not hit.isNull():
        return hit
    w = round(px * config.ICON_ASPECT.get("formation", 1.0))
    pm = QPixmap(round(w * dpr), round(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _formation_board(p, w, px)
    r = max(1.1, px / 15)
    pad = r + 1.5
    band = {c: i for i, (_, rng) in enumerate(core.PITCH_ROWS) for c in rng}
    for c in spots:
        # PITCH_X 는 큰 축구장(칩 폭)에 맞춘 값이라 작은 그림에선 RAM·CAM·LAM 이 한 덩이로 붙는다 — 가운데에서 넓힌다
        spread = min(1.0, max(0.0, 0.5 + (core.PITCH_X[c] - 0.5) * FORMATION_SPREAD))
        x = pad + (w - 2 * pad) * spread
        y = px - pad - (px - 2 * pad) * core.PITCH_Y[c] / core.PITCH_Y_MAX
        _formation_dot(p, QPointF(x, y), r, T.FORMATION_LINE_COLORS[band.get(c, len(core.PITCH_ROWS) - 1)], px)
    p.end()
    QPixmapCache.insert(ck, pm)
    return pm


NAME_ROLE = Qt.ItemDataRole.UserRole + 3   # 특성 칸의 이름(그림이 없을 때 대신 쓴다 · 툴팁과 같다)


class TraitIconDelegate(RowBorderDelegate):
    """특성 칸 — 육각 판 위에 아이콘, 아래에 %. 그림이 아직 없거나 못 받았으면(넥슨에 그림이 아예 없는 특성도 있다)
    판 없이 그 자리에 이름을 줄여 쓴다.

    판은 넥슨 원본이 아니라 QPainter 로 그린다(일반·신규 금색 · 훈련·코치 회색 — 색은 theme). 폭은 sizeHint 로 알리지 않는다
    (GradeBadgeDelegate 와 같은 이유: _measure_pad 가 한 열의 큰 힌트를 모든 열에 얹는다) — 글자 "NN%" 가 곧 칸 폭이고
    그림은 그 위에 가운데 정렬. RowBorderDelegate 를 상속해 선택 행 테두리가 이 칸에서 끊기지 않는다."""

    TOP, GAP, BOTTOM = 2, 2, 2

    def __init__(self, parent=None, kind: str = "trait"):
        super().__init__(parent)
        self.kind = kind
        self.failed = 0   # 테스트가 읽는다 — 기록은 첫 번째만

    @classmethod
    def row_height(cls, fm: QFontMetrics) -> int:
        return cls.TOP + config.TRAIT_ICON_PX + cls.GAP + fm.height() + cls.BOTTOM

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.text = ""             # 글자·그림은 직접 그린다(기본 그리기는 배경·선택만)
        option.icon = QIcon()

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        try:
            self._draw(painter, option, index)
        except Exception as e:  # noqa: BLE001 — 칸 하나 그리기 실패로 표가 죽거나 로그가 불어나면 안 된다
            self.failed += 1
            if self.failed == 1:
                import crashlog
                crashlog.note("특성 칸 그리기", e)

    def pad_rect(self, option) -> QRectF:
        """판(육각)이 들어가는 정사각형 — 칸 위쪽 가운데. 테스트가 글자와 겹치는지 잴 때도 쓴다."""
        s = config.TRAIT_ICON_PX
        r = option.rect
        return QRectF(r.x() + (r.width() - s) / 2, r.y() + self.TOP, s, s)

    def text_rect(self, option) -> QRectF:
        r = option.rect
        return QRectF(r.x(), r.y() + self.TOP + config.TRAIT_ICON_PX + self.GAP, r.width(), option.fontMetrics.height())

    def _draw(self, painter, option, index) -> None:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        name = str(index.data(NAME_ROLE) or "")
        if not name:
            return                    # 빈 칸(줄 수가 모자란 열)
        box = self.pad_rect(option)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        pm = icon.pixmap(config.TRAIT_ICON_PX, config.TRAIT_ICON_PX) if isinstance(icon, QIcon) else QPixmap()
        if not pm.isNull():
            c = box.center()
            hexa = QPolygonF([QPointF(c.x() + box.width() / 2 * math.sin(math.radians(60 * i)),
                                      c.y() - box.height() / 2 * math.cos(math.radians(60 * i))) for i in range(6)])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T.TRAIT_PAD_GOLD if self.kind == "trait" else T.TRAIT_PAD_GRAY))
            painter.drawPolygon(hexa)
            painter.drawPixmap(box.adjusted(3, 3, -3, -3).toRect(), pm)
        else:
            # 그림이 없으면 판도 그리지 않는다 — 판 위에 이름을 찍으면 이름이 판 밖으로 번져 겹쳐 보였다(일반 특성 8개 —
            # 코드 0·1·3·4·5·6·7·9 — 는 넥슨 CDN 에 그림이 아예 없다(403, 2026-10-11 실측) · 실화면에서 발견)
            small = QFont(option.font)
            small.setPixelSize(max(9, small.pixelSize() - 3) if small.pixelSize() > 0 else 10)
            painter.setFont(small)
            painter.setPen(QColor(T.TEXT_DIM))
            label = QFontMetrics(small).elidedText(name, Qt.TextElideMode.ElideRight, option.rect.width() - 4)
            painter.drawText(QRectF(option.rect.x(), box.y(), option.rect.width(), box.height()),
                             Qt.AlignmentFlag.AlignCenter, label)
        painter.setFont(option.font)
        painter.setPen(QColor(T.TEXT))
        painter.drawText(self.text_rect(option), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class GradeBadgeDelegate(RowBorderDelegate):
    """선수 지표 '강화' 칸 — 숫자를 넥슨 데이터센터 색 배지로(_grade_badge_colors).

    RowBorderDelegate 를 상속한다: 표 전체에 걸린 선택 행 테두리가 이 칸에서 끊기지 않게.
    배지 여백(PAD)은 sizeHint 로 알리지 않는다 — FitTableWidget._measure_pad 가 열마다 잰 여백 중
    가장 큰 값 하나를 19열 전부에 쓰므로, 한 열만 키워도 표 전체가 넓어진다. 열 폭은 app_main 이
    app_main._grade_badges 로 이 열에만 준다(1.2.1 계획 검토 A · 쓰는 표는 MainWindow.GRADE_BADGE_COLUMNS).
    그리기 예외는 다시 그릴 때마다 되풀이되므로 글자만 그리는 쪽으로 넘기고 한 번만 기록한다."""

    PAD = 8  # 배지 안쪽 좌우 여백(px) — 열에 더할 폭은 2 × PAD

    def __init__(self, parent=None):
        super().__init__(parent)
        self.failed = 0  # 테스트가 읽는다 — 기록은 첫 번째만

    @staticmethod
    def grade_of(text) -> int | None:
        """칸 글자 → 배지로 그릴 강화(1 이상). "+5" 도 5(int 가 + 를 읽는다) · "3 → 5"(강화 변화)·"-"·0 은 None — 그 칸은 글자 그대로."""
        try:
            g = int(str(text or ""))
        except ValueError:
            return None
        return g if g >= 1 else None

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if self.grade_of(index.data()) is not None:
            option.text = ""  # 글자는 배지 안에 직접 그린다(기본 그리기는 배경·선택 테두리만)

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        g = self.grade_of(index.data())
        if g is None:
            return
        text = str(g)
        try:
            self._badge(painter, option, text)
        except Exception as e:  # noqa: BLE001 — 칸 하나 그리기 실패로 표가 죽거나 로그가 불어나면 안 된다
            self.failed += 1
            if self.failed == 1:
                import crashlog
                crashlog.note("강화 배지 그리기", e)
            painter.save()
            painter.setPen(QColor(T.TEXT))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()

    def _badge(self, painter, option, text: str) -> None:
        bg, fg = _grade_badge_colors(text)
        fm = QFontMetrics(option.font)
        w = fm.horizontalAdvance(text) + 2 * self.PAD
        h = min(fm.height() + 4, option.rect.height() - 4)
        r = option.rect
        from PyQt6.QtCore import QRectF
        box = QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(box, h / 2, h / 2)
        painter.setPen(QColor(fg))
        painter.setFont(option.font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


def sample_text(n: int, mixed: bool) -> str:
    """표본 글자 — 판정(is_weak)과 같은 분모(rated)로. 섞인 묶음이면 무승부를 뺀 "승패 N판"(2.6.1 A6)."""
    return f"승패 {n:,}판" if mixed else f"표본 {n:,}"


def sample_note(n: int, min_n: int, mixed: bool = False) -> str:
    """표본 미달 칸의 툴팁 — 승률 막대·대시보드 막대가 같은 말을 쓴다. n 은 승률 분모(rated)."""
    unit = "판" if mixed else "경기"
    return f"표본이 {'승패 ' if mixed else ''}{n:,}{unit}{'이라' if mixed else '라'} 승률이 크게 흔들립니다(기준 {min_n}{unit})."


def win_rate_note(mixed: bool) -> str:
    """승률 식 설명(머리·툴팁) — 숫자 옆 식 표시를 풀어 쓴 말."""
    return config.WIN_RATE_NOTE_MIXED if mixed else config.WIN_RATE_NOTE_SINGLE


def win_rate_text(win: int, draw: int, lose: int, mixed: bool, digits: int = 1, short: bool = False) -> str:
    """승률 숫자는 전부 이걸로 — "50.0% · 승÷(승+패) · 무 859"(2.6.1 A6 U5: 같은 경기가 화면마다 다른 식일 수 있어
    숫자 옆에 식과 무승부 수를 늘 붙인다). short 는 좁은 칸 — 섞인 식이면 숫자 뒤 "*"(설명은 win_rate_note 를 툴팁으로).
    분모가 0(섞인 식에서 무승부뿐)이면 "—" — 0.0% 는 "다 졌다"로 읽힌다(실화면 2026-10-11: 0승 1무 0패 → 0.0%*)."""
    if core.rated(win, draw, lose, mixed) == 0:
        return "—" if short else f"— · {'승÷(승+패)' if mixed else '승÷전체'} · 무 {draw:,}"
    pct = f"{core.win_pct(win, draw, lose, mixed):.{digits}f}%"
    if short:
        return pct + ("*" if mixed else "")
    return f"{pct} · {'승÷(승+패)' if mixed else '승÷전체'} · 무 {draw:,}"


def win_rate_bar(win: int, draw: int, lose: int, text: str, min_n: int, mixed: bool,
                 height: int = 18) -> QProgressBar:
    """승률 막대 하나 — 승률을 막대로 그리는 곳은 전부 이걸 거친다(표본 흐림 규칙, 1.2.1).

    경기 수가 min_n 보다 적으면 막대를 바탕 쪽으로 흐리게(불투명) · 끝에 "· 표본 N".
    "프로1 (1경기) 100%" 가 꽉 찬 막대로 보여 실력처럼 읽히던 것을 막는다. 색만으로 구분하지 않게
    글자를 같이 붙인다(적록 색약). 흐렸는지는 property("weak") 로 테스트가 읽는다."""
    n = core.rated(win, draw, lose, mixed)
    wr = core.win_pct(win, draw, lose, mixed)
    weak = core.is_weak(n, min_n)
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setValue(int(wr * 10))
    bar.setFormat(text + (f"  · {sample_text(n, mixed)}" if weak else ""))
    bar.setFixedHeight(height)
    chunk = T.blend(T.PANEL_2, T.WIN_BAR, T.WEAK_MIX) if weak else T.WIN_BAR
    # 글자는 흐리지 않는다 — TEXT_DIM 은 흐린 막대 위에서 대비 3.6(어두운 테마, 기준 4.5)이라 안 읽혔다(1.2.1 실측).
    # 흐림은 막대색과 "표본 N" 이 맡는다.
    bar.setStyleSheet(
        f"QProgressBar{{background:{T.PANEL_2};border:none;border-radius:3px;"
        f"color:{T.TEXT};text-align:center;}}"
        f"QProgressBar::chunk{{background:{chunk};border-radius:3px;}}")
    bar.setProperty("weak", weak)
    bar.setProperty("games", n)
    tip = win_rate_note(mixed)
    bar.setToolTip(sample_note(n, min_n, mixed) + "\n" + tip if weak else tip)
    return bar


def delta_color(diff: float, eps: float = 0.05) -> str:
    """차이 부호 → 색(P3). |diff| < eps 는 반올림하면 0 이라 흐린 글자 — "+0.0%p" 가 초록으로 보이지 않게."""
    return T.GOOD if diff >= eps else T.BAD if diff <= -eps else T.TEXT_DIM


def delta_text(v: float, base: float, unit: str = "%p", digits: int = 1) -> tuple[str, str]:
    """평균(기준) 대비 차이(P3) → ("▲ 3.2%p", 색). 색만으로 말하지 않게 ▲▼ 를 늘 붙인다(적록 색약 — theme 머리말)."""
    diff = v - base
    eps = 0.5 / 10 ** digits
    arrow = "▲" if diff >= eps else "▼" if diff <= -eps else "–"
    return f"{arrow} {abs(diff):.{digits}f}{unit}", delta_color(diff, eps)


def delta_item(item: QTableWidgetItem | None, diff: float, eps: float = 0.05) -> QTableWidgetItem | None:
    """표 칸의 차이 글자(+3.2%p 꼴 — 부호가 이미 있다)에 delta_color 를 입힌다. 칸이 없으면 None 그대로."""
    if item is not None:
        item.setForeground(QColor(delta_color(diff, eps)))
    return item


class _WdlStrip(QWidget):
    """WdlBar 의 막대 — 양 끝 둥근 띠를 경기 수 비례로 승·무·패 세 칸."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedHeight(WdlBar.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.counts = (0, 0, 0)
        self.weak = False
        self.compact = False

    def sizeHint(self) -> QSize:
        return QSize(60, WdlBar.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(24, WdlBar.HEIGHT)

    def colors(self) -> list[str]:
        return WdlBar.colors_for(self.weak, self.compact)

    def segments(self) -> list[tuple[float, str]]:
        """(폭 비율, 색) — 0경기 칸은 뺀다. 테스트가 그림 대신 읽는다."""
        return wdl_segments(self.counts, self.colors())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_wdl_strip(p, QRectF(self.rect()), self.segments())
        p.end()


def wdl_segments(counts: tuple[int, int, int], colors: list[str]) -> list[tuple[float, str]]:
    total = sum(counts)
    return [(n / total, c) for n, c in zip(counts, colors) if n] if total else []


def paint_wdl_strip(p: QPainter, r: QRectF, segments: list[tuple[float, str]]) -> None:
    """승·무·패 띠 그리기 — WdlBar 와 줄 목록 대리자(RateRowList)가 같이 쓴다(65 E6 — 두 벌로 갈리지 않게)."""
    h = r.height()
    path = QPainterPath()
    path.addRoundedRect(r, h / 2, h / 2)
    p.save()
    p.setClipPath(path)
    p.fillRect(r, QColor(T.PANEL_2))
    x = r.left()
    for share, c in segments:
        w = r.width() * share
        p.fillRect(QRectF(x, r.top(), w, h), QColor(c))
        x += w
    p.restore()


class WdlBar(QWidget):
    """승·무·패 막대(P2) — 6px 세 칸 막대 + "1,861승 404무 1,774패"(승·패만 색 · 글자가 같이 있어 색은 보조).

    weak 면 막대를 바탕 쪽으로 흐린다(WEAK_MIX — win_rate_bar 와 같은 표본 흐림) · 글자는 흐리지 않는다.
    칸 폭은 경기 수 비례 · 0경기면 빈 띠. 승률 한 값 막대는 win_rate_bar 그대로(이건 세 값을 다 보인다)."""
    HEIGHT = 6
    DRAW_MIX = 0.4   # 무 칸 = 흐린 글자색 40%
    COMPACT_MIX = 0.8   # 줄 목록 꼴(compact — 65 E6): 승·패 채움 80% — 줄마다 막대가 있어 한 단 덜 진하게

    def __init__(self, w: int = 0, d: int = 0, l: int = 0, weak: bool = False, text_first: bool = False,
                 compact: bool = False):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(4)
        self.strip = _WdlStrip(self)
        self.strip.compact = compact
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setStyleSheet("border: none; font-weight: bold;")
        for x in ((self.label, self.strip) if text_first else (self.strip, self.label)):
            v.addWidget(x)
        self.set_counts(w, d, l, weak)

    def set_counts(self, w: int, d: int, l: int, weak: bool = False) -> None:
        self.strip.counts = (w, d, l)
        self.strip.weak = weak
        self.label.setText(wdl_html(w, d, l))
        self.setAccessibleName(wdl_text(w, d, l))
        self.strip.update()

    def counts(self) -> tuple[int, int, int]:
        return self.strip.counts

    @staticmethod
    def colors_for(weak: bool, compact: bool = False) -> list[str]:
        """승·무·패 칸 색 — 표본 미달이면 바탕 쪽으로 흐린다(WEAK_MIX)."""
        good, bad = ((T.blend(T.PANEL, T.GOOD_FILL, WdlBar.COMPACT_MIX), T.blend(T.PANEL, T.BAD_FILL, WdlBar.COMPACT_MIX))
                     if compact else (T.GOOD_FILL, T.BAD_FILL))
        cols = [good, T.blend(T.PANEL, T.TEXT_DIM, WdlBar.DRAW_MIX), bad]
        return [T.blend(T.PANEL_2, c, T.WEAK_MIX) for c in cols] if weak else cols


def wdl_html(w: int, d: int, l: int) -> str:
    """승·무·패 글 줄(리치 텍스트) — 승은 좋음, 패는 나쁨, 무는 보통 글자."""
    return (f"<span style='color:{T.GOOD}'>{w:,}승</span> <span style='color:{T.TEXT}'>{d:,}무</span>"
            f" <span style='color:{T.BAD}'>{l:,}패</span>")


def tag_qss(color: str, selector: str, extra: str = "") -> str:
    """태그 꼴 QSS — 글자 = 그 색 · 바탕 = 그 색 10%(불투명 섞음 · PyQt 2번). Tag 와 켜진 결과 필터가 같이 쓴다
    (extra 는 같은 덩이 뒤에 붙어 앞 값을 덮는다). 대비는 test_ui_smoke 의 "10% 바탕" 행이 잰다."""
    soft = T.blend(T.PANEL, color, 0.10)
    return (f"{selector} {{ color: {color}; background: {soft}; border: 1px solid {soft};"
            f" border-radius: 4px; padding: 2px 6px; {extra}}}")


class Tag(QLabel):
    """작은 태그(P10) — 연승·연패·진행 중 같은 상태 한 마디. 모서리 4 · 12px · 굵기 500.
    kind: good · bad · warn · muted. 모르는 kind 는 KeyError — 조용히 회색이 되지 않게."""
    KINDS = {"good": "GOOD", "bad": "BAD", "warn": "WARN", "muted": "TEXT_DIM"}

    def __init__(self, text: str = "", kind: str = "muted"):
        super().__init__(text)
        f = QFont()
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.Medium)
        self.setFont(f)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.color = getattr(T, self.KINDS[kind])
        self.kind = kind
        self.setStyleSheet(tag_qss(self.color, "QLabel"))


class _ShareStrip(QWidget):
    """VersusBar 의 막대 — 왼쪽부터 내 몫(share) · 나머지는 상대 색(없으면 빈 띠)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedHeight(VersusBar.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.share = 0.5
        self.mine_color = T.ACCENT_FILL
        self.opp_color: str | None = None

    def sizeHint(self) -> QSize:
        return QSize(60, VersusBar.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(24, VersusBar.HEIGHT)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipPath(path)
        p.fillRect(r, QColor(self.opp_color or T.PANEL_2))
        p.fillRect(QRectF(r.left(), r.top(), r.width() * self.share, r.height()), QColor(self.mine_color))
        p.end()


class VersusBar(QWidget):
    """나와 상대 한 줄(65 M1 · 상세 창 경기 정보 M3 도 이 부품) — 위: 라벨 · 오른쪽 "나 vs 상대"(내가 크면 내 숫자 강조색),
    아래: 내 몫 막대. opp_color 를 주면 두 색으로 갈린 막대(상세 창 꼴), 안 주면 빈 띠 위 내 몫만(대시보드 꼴)."""
    HEIGHT = 6

    def __init__(self, label: str, opp_color: str | None = None):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        self.nums = QLabel("")
        self.nums.setTextFormat(Qt.TextFormat.RichText)
        self.nums.setStyleSheet("border: none;")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.nums)
        v.addLayout(top)
        self.strip = _ShareStrip(self)
        self.strip.opp_color = opp_color
        v.addWidget(self.strip)

    def set_values(self, mine: float, opp: float, fmt: str = "{:.0f}", unit: str = "") -> None:
        ahead = mine > opp
        if self.strip.opp_color is None:
            col = T.ACCENT if ahead else T.TEXT
            self.nums.setText(f"<b style='color:{col}'>{fmt.format(mine)}{unit}</b>"
                              f" <span style='color:{T.TEXT_DIM}'>vs {fmt.format(opp)}{unit}</span>")
        else:
            # 상세 창 꼴 — 파울처럼 적을수록 좋은 줄도 있어 좋고 나쁨 색 대신 큰 쪽만 굵게(작은 쪽 흐림 · 같으면 둘 다 보통)
            def num(v: float, big: bool, same: bool) -> str:
                text = f"{fmt.format(v)}{unit}"
                if same:
                    return f"<span style='color:{T.TEXT}'>{text}</span>"
                return f"<b style='color:{T.TEXT}'>{text}</b>" if big else f"<span style='color:{T.TEXT_DIM}'>{text}</span>"
            same = fmt.format(mine) == fmt.format(opp)
            self.nums.setText(f"{num(mine, ahead, same)} <span style='color:{T.TEXT_DIM}'>vs</span> "
                              f"{num(opp, opp > mine, same)}")
        tot = mine + opp
        self.strip.share = mine / tot if tot else 0.5
        self.mine_ahead = ahead
        self.strip.update()


class GoalTimeline(QWidget):
    """경기 흐름(65 M3 요약) — 가로 시간 줄 위에 내 골(위 · 강조색) · 아래에 상대 골(OPP_FILL). 연장이 있으면 120분까지.
    색만으로 말하지 않게 화면은 줄 아래에 "12' 이름" 글을 같이 둔다."""
    HEIGHT = 52

    def __init__(self):
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.goals: list[tuple[float, bool]] = []

    def set_goals(self, goals: list[tuple[float, bool]]) -> None:
        self.goals = list(goals)
        self.update()

    @property
    def span(self) -> int:
        return 120 if any(m > 90 for m, _ in self.goals) else 90

    def x_of(self, minute: float) -> float:
        m = 8
        return m + (self.width() - 2 * m) * min(max(minute, 0.0), self.span) / self.span

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2 - 6
        p.setPen(QPen(QColor(T.BORDER), 2))
        p.drawLine(QPointF(self.x_of(0), mid), QPointF(self.x_of(self.span), mid))
        f = QFont(self.font())
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(T.TEXT_DIM))
        for tick in (0, 45, 90, 120):
            if tick > self.span:
                continue
            x = self.x_of(tick)
            p.drawLine(QPointF(x, mid - 4), QPointF(x, mid + 4))
            p.drawText(QRectF(x - 20, mid + 14, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{tick}'")
        p.setPen(Qt.PenStyle.NoPen)
        for minute, mine in self.goals:
            p.setBrush(QColor(T.ACCENT_FILL if mine else T.OPP_FILL))
            p.drawEllipse(QPointF(self.x_of(minute), mid - 9 if mine else mid + 9), 5, 5)
        p.end()


# ── 줄 카드 목록(3.1.1 P4 · 65) ─────────────────────────────────────────────
# 줄은 위젯이 아니라 대리자가 그리는 그림이다 — 1만 경기를 카드 위젯으로 만들면 메뉴 전환을 못 지킨다(65 E2).
# 거르기·정렬은 모델 안 목록에서 끝낸다(QListView.setRowHidden 은 숨은 줄을 목록으로 들고 있어 1만 줄이면 느리다).
ROW_ROLE = Qt.ItemDataRole.UserRole


class _RowModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.all: list = []
        self.shown: list = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.shown)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.shown):
            return None
        row = self.shown[index.row()]
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return getattr(row, "accessible", "")
        return None

    def reset_rows(self, rows: list, pred) -> None:
        self.beginResetModel()
        self.all = list(rows)
        self.shown = [r for r in self.all if pred(r)] if pred is not None else list(self.all)
        self.endResetModel()


class CardListView(QListView):
    """줄 카드 목록의 틀 — 모델(전체 줄 · 보이는 줄) · 거르기 · 그림 표. 줄 그리기는 대리자가, 줄 모양은 물려받은 쪽이 정한다.

    그림(등급 배지·엠블럼)은 (종류, 키) → 그림 표 하나에 붙는다 — 줄이 아니라 키에 붙어서 정렬·거르기가 바뀌어도
    제 줄을 따라간다(40단계 표 칸 그림의 '키가 행을 따라가나' 와 같은 약속). 받은 뒤엔 보이는 줄만 다시 그린다."""

    def __init__(self):
        super().__init__()
        self._model = _RowModel(self)
        self.setModel(self._model)
        self._pred = None
        self._icons: dict[tuple, QPixmap] = {}
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setMouseTracking(True)
        self.setStyleSheet(f"QListView {{ background: {T.BG}; border: none; }}")

    def set_rows(self, rows: list) -> None:
        self._model.reset_rows(rows, self._pred)

    def set_filter(self, pred) -> None:
        """pred(줄) → 보일까. None 이면 전부."""
        self._pred = pred
        self._model.reset_rows(self._model.all, pred)

    def relayout(self) -> None:
        """줄 높이가 바뀌었을 때(대리자 꼴) — 같은 줄로 다시 그린다."""
        self._model.reset_rows(self._model.all, self._pred)

    def rowCount(self) -> int:
        return len(self._model.shown)

    def row(self, r: int):
        return self._model.shown[r]

    def rows(self) -> list:
        """보이는 줄(거른 뒤)."""
        return list(self._model.shown)

    def all_rows(self) -> list:
        return list(self._model.all)

    def current(self):
        idx = self.currentIndex()
        return self.row(idx.row()) if idx.isValid() and idx.row() < self.rowCount() else None

    def icon_keys(self, row) -> tuple:
        """줄이 쓰는 그림 (종류, 키) — 물려받는 쪽이 정한다."""
        return ()

    def icon_entries(self) -> set:
        out = set()
        for row in self._model.all:
            out.update(k for k in self.icon_keys(row) if k[1] is not None)
        return out

    def put_icon(self, kind: str, key, pm: QPixmap) -> None:
        self._icons[(kind, key)] = pm
        self.viewport().update()

    def icon(self, kind: str, key) -> QPixmap | None:
        return self._icons.get((kind, key)) if key is not None else None


@dataclass
class MatchCard:
    """경기 목록 카드 한 줄(65 M2) — 글자는 화면(app_main)이 다 만들어 넣는다. 대리자는 그리기만 한다."""
    match_id: str
    date_text: str
    result: str                     # 승 · 무 · 패 · 몰수승 …
    score: str                      # "3 : 1"
    shootout: str = ""              # "승부차기 4:2" | ""
    opponent: str = "-"
    division_key: object = None     # 그 경기 때 상대 등급 배지 키
    opp_pad: bool = False           # 상대 입력 장치가 패드(U4 — 패드일 때만 아이콘)
    stats: tuple = ()               # ((라벨, 값), …) 오른쪽 — 점유 · 슛 · 패스 · 평점
    color: str = ""                 # 상대 지금 팀컬러
    emblem_key: object = None
    value_text: str = ""            # 상대 지금 구단가치(짧게)
    h2h_text: str = ""              # 맞대결(시즌 범위)
    opp_score: str | None = None    # None = 칸이 없다(관문 미달·남의 목록)
    when: object = None             # 정렬 키
    goal_diff: int = 0
    rating: float = 0.0

    @property
    def kind(self) -> str:
        return "win" if "승" in self.result else ("lose" if "패" in self.result else "other")

    @property
    def accessible(self) -> str:
        return f"{self.date_text} {self.result} {self.score} {self.opponent}"


class CardSeg(NamedTuple):
    role: str       # 그린 조각 이름 — 툴팁·넘침 검사가 읽는다
    rect: QRectF
    text: str
    full: float     # 글자가 다 들어가는 폭(그림 조각은 0)


class MatchCardDelegate(QStyledItemDelegate):
    """경기 목록 카드 그리기 — 왼쪽 결과 띠 + 연한 결과 바탕(옛 표 행 색과 같은 T.WIN/LOSE · ROW_TINT),
    ① 일시 ② 결과 알약 · 점수(크게) · 승부차기 · vs 상대 · 등급 배지 · 패드 / 오른쪽 점유·슛·패스·평점
    ③ (상대 정보) 엠블럼 · 팀컬러 · 구단가치 · 맞대결 · 상대 점수. 자리는 layout() 한 곳이 정하고 그리기·툴팁·넘침 검사가 같이 쓴다."""
    MARGIN = 3          # 카드 사이 위아래
    STRIPE = 4
    PAD_X = 12
    PAD_Y = 7
    GAP = 3
    ITEM_GAP = 14       # 오른쪽 숫자 묶음 사이

    def __init__(self, view: CardListView, tips: dict[str, str] | None = None):
        super().__init__(view)
        self.view = view
        self.tips = dict(tips or {})
        self.show_extra = True

    # ── 글꼴 ──
    @staticmethod
    def fonts(base: QFont) -> dict[str, QFont]:
        from PyQt6.QtGui import QFontInfo
        px = QFontInfo(base).pixelSize() or T.BASE_FONT_PX
        out = {}
        for name, scale, bold in (("small", 0.85, False), ("main", 1.0, False), ("bold", 1.0, True), ("big", 1.3, True)):
            f = QFont(base)
            f.setPixelSize(max(9, round(px * scale)))
            f.setBold(bold)
            out[name] = f
        return out

    def row_height(self, base: QFont) -> int:
        f = self.fonts(base)
        h = (2 * self.MARGIN + 2 * self.PAD_Y + QFontMetrics(f["small"]).height() + self.GAP
             + QFontMetrics(f["big"]).height())
        if self.show_extra:
            h += self.GAP + QFontMetrics(f["main"]).height()
        return h

    def sizeHint(self, option, index) -> QSize:
        return QSize(max(option.rect.width(), 200), self.row_height(option.font))

    @staticmethod
    def colors(card: MatchCard) -> dict[str, str]:
        """카드 색 — 테스트가 그림 대신 읽는다."""
        tone = {"win": T.WIN, "lose": T.LOSE}.get(card.kind)
        fill = {"win": T.GOOD_FILL, "lose": T.BAD_FILL}.get(card.kind)
        return {"bg": T.blend(T.PANEL, tone, T.ROW_TINT) if tone else T.PANEL,
                "stripe": fill or T.blend(T.PANEL, T.TEXT_DIM, WdlBar.DRAW_MIX),
                "pill": tone or T.TEXT_DIM}

    # ── 자리 ──
    def layout(self, card: MatchCard, rect, base: QFont) -> list[CardSeg]:
        f = self.fonts(base)
        fm = {k: QFontMetrics(v) for k, v in f.items()}
        r = QRectF(rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        left = r.left() + self.STRIPE + self.PAD_X
        right = r.right() - self.PAD_X
        segs: list[CardSeg] = []

        def put(role: str, x: float, y: float, h: float, text: str, font: str, limit: float | None = None) -> float:
            full = fm[font].horizontalAdvance(text) if text else 0
            w = full if limit is None else max(0.0, min(full, limit - x))
            segs.append(CardSeg(role, QRectF(x, y, w, h), text, full))
            return x + w

        y1 = r.top() + self.PAD_Y
        h1 = fm["small"].height()
        put("date", left, y1, h1, card.date_text, "small", right)
        y2 = y1 + h1 + self.GAP
        h2 = fm["big"].height()
        # 오른쪽 숫자 묶음 — 폭을 먼저 재서 왼쪽 상대 이름이 쓸 수 있는 끝을 정한다
        items = [(lab, val, fm["small"].horizontalAdvance(lab + " "), fm["bold"].horizontalAdvance(val))
                 for lab, val in card.stats]
        total = sum(a + b for _l, _v, a, b in items) + self.ITEM_GAP * max(0, len(items) - 1)
        x = right - total
        stats_left = x
        for lab, val, a, b in items:
            segs.append(CardSeg("stat_label", QRectF(x, y2, a, h2), lab, a))
            segs.append(CardSeg("stat", QRectF(x + a, y2, b, h2), val, b))
            x += a + b + self.ITEM_GAP
        limit = stats_left - 16
        pill_w = fm["small"].horizontalAdvance(card.result) + 14
        segs.append(CardSeg("result", QRectF(left, y2 + (h2 - h1 - 4) / 2, pill_w, h1 + 4), card.result,
                            fm["small"].horizontalAdvance(card.result) + 14))
        x = left + pill_w + 10
        x = put("score", x, y2, h2, card.score, "big", limit) + 6
        if card.shootout:
            x = put("shootout", x, y2, h2, card.shootout, "small", limit) + 6
        x = put("vs", x, y2, h2, "vs", "small", limit) + 5
        icon_h = fm["main"].height()
        reserve = (icon_h * 1.4 + 4 if card.division_key is not None else 0) + (icon_h * 1.3 + 6 if card.opp_pad else 0)
        x = put("opp", x, y2, h2, card.opponent, "bold", limit - reserve) + 4
        if card.division_key is not None:
            segs.append(CardSeg("badge", QRectF(x, y2 + (h2 - icon_h) / 2, icon_h * 1.4, icon_h), "", 0))
            x += icon_h * 1.4 + 4
        if card.opp_pad:
            segs.append(CardSeg("pad", QRectF(x, y2 + (h2 - icon_h) / 2, icon_h * 1.3, icon_h), "", 0))
        if self.show_extra:
            y3 = y2 + h2 + self.GAP
            h3 = fm["main"].height()
            x = left
            if card.emblem_key is not None:
                segs.append(CardSeg("emblem", QRectF(x, y3, h3, h3), "", 0))
                x += h3 + 4
            x = put("color", x, y3, h3, card.color or "-", "main", right)
            parts = [("value", "구단가치", card.value_text or "-"), ("h2h", "맞대결", card.h2h_text or "-")]
            if card.opp_score is not None:
                parts.append(("oppscore", "상대 점수", card.opp_score))
            for role, lab, val in parts:
                x = put(role + "_label", x + 12, y3, h3, lab, "small", right) + 4
                x = put(role, x, y3, h3, val, "main", right)
        return segs

    def overflow(self, card: MatchCard, width: int, base: QFont) -> list[str]:
        """그 폭에서 잘리는 글자 조각 — 스모크가 1280·150% 에서 잰다(PyQt 5 의 카드판)."""
        return [s.role for s in self.layout(card, QRectF(0, 0, width, self.row_height(base)), base)
                if s.text and s.full > s.rect.width() + 0.5]

    # ── 그리기 ──
    def paint(self, painter, option, index) -> None:
        card = index.data(ROW_ROLE)
        if card is None:
            return
        painter.save()
        try:
            self._paint(painter, option, card)
        finally:
            painter.restore()

    def _paint(self, p: QPainter, option, card: MatchCard) -> None:
        f = self.fonts(option.font)
        col = self.colors(card)
        r = QRectF(option.rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = col["bg"]
        if option.state & QStyle.StateFlag.State_MouseOver:
            bg = T.blend(bg, T.TEXT, 0.04)
        path = QPainterPath()
        path.addRoundedRect(r, T.RADIUS, T.RADIUS)
        p.fillPath(path, QColor(bg))
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.top(), self.STRIPE, r.height()), QColor(col["stripe"]))
        p.restore()
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(QPen(QColor(T.ACCENT), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        fonts = {"date": "small", "score": "big", "shootout": "small", "vs": "small", "opp": "bold",
                 "stat_label": "small", "stat": "bold", "color": "main"}
        dim = {"date", "shootout", "vs", "stat_label"}
        for s in self.layout(card, option.rect, option.font):
            if s.role == "result":
                soft = T.blend(T.PANEL, col["pill"], 0.10)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(soft))
                p.drawRoundedRect(s.rect, 4, 4)
                p.setPen(QColor(col["pill"]))
                p.setFont(f["small"])
                p.drawText(s.rect, Qt.AlignmentFlag.AlignCenter, s.text)
                continue
            if s.role in ("badge", "emblem"):
                key = card.division_key if s.role == "badge" else card.emblem_key
                pm = self.view.icon("division" if s.role == "badge" else "emblem", key)
                if pm is not None and not pm.isNull():
                    scaled = pm.scaled(s.rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(QPointF(s.rect.left(), s.rect.top() + (s.rect.height() - scaled.height()) / 2), scaled)
                continue
            if s.role == "pad":
                self._pad(p, s.rect)
                continue
            if not s.text:
                continue
            font = fonts.get(s.role) or ("small" if s.role.endswith("_label") else "main")
            p.setFont(f[font])
            p.setPen(QColor(T.TEXT_DIM if s.role in dim or s.role.endswith("_label") else T.TEXT))
            fmx = QFontMetrics(f[font])
            text = s.text if s.full <= s.rect.width() + 0.5 else fmx.elidedText(
                s.text, Qt.TextElideMode.ElideRight, max(0, int(s.rect.width())))
            p.drawText(s.rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    @staticmethod
    def _pad(p: QPainter, rect: QRectF) -> None:
        """패드 모양(U4) — 이모지 글꼴은 PC 마다 달라 직접 그린다. 둥근 몸통 + 왼쪽 십자 + 오른쪽 단추 둘."""
        h = rect.height() * 0.62
        body = QRectF(rect.left(), rect.center().y() - h / 2, rect.width(), h)
        p.setPen(QPen(QColor(T.TEXT_DIM), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, h / 2, h / 2)
        cx, cy, a = body.left() + body.width() * 0.3, body.center().y(), h * 0.22
        p.drawLine(QPointF(cx - a, cy), QPointF(cx + a, cy))
        p.drawLine(QPointF(cx, cy - a), QPointF(cx, cy + a))
        p.setBrush(QColor(T.TEXT_DIM))
        for dx, dy in ((0.66, -0.12), (0.78, 0.12)):
            p.drawEllipse(QPointF(body.left() + body.width() * dx, cy + h * dy), h * 0.09, h * 0.09)

    def tip_at(self, card: MatchCard, rect, base: QFont, pos) -> str:
        for s in self.layout(card, rect, base):
            if s.rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                role = s.role.removesuffix("_label")
                if role == "opp" and s.full > s.rect.width() + 0.5:
                    return card.opponent
                if role in self.tips:
                    return self.tips[role]
        return ""

    def helpEvent(self, event, view, option, index) -> bool:
        card = index.data(ROW_ROLE) if index.isValid() else None
        tip = self.tip_at(card, option.rect, option.font, event.pos()) if card is not None else ""
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        QToolTip.hideText()
        return False


class MatchCardList(CardListView):
    """경기 목록(65 M2) — 카드 한 줄 = 경기 하나. 더블클릭 → 경기 상세 창(app_main 이 잇는다)."""

    def __init__(self, tips: dict[str, str] | None = None):
        super().__init__()
        self.card_delegate = MatchCardDelegate(self, tips)
        self.setItemDelegate(self.card_delegate)

    def icon_keys(self, row: MatchCard) -> tuple:
        return (("division", row.division_key), ("emblem", row.emblem_key))

    @property
    def show_extra(self) -> bool:
        return self.card_delegate.show_extra

    def set_show_extra(self, on: bool) -> None:
        if self.card_delegate.show_extra != on:
            self.card_delegate.show_extra = on
            self.relayout()


@dataclass
class RateRow:
    """승률 줄 목록 한 줄(65 P4 · M4~M6) — 이름 · 보조 글 · 오른쪽 "N경기 승 무 패 승률 (±차)" · 아래 3색 막대.
    글자는 화면이 다 만들어 넣는다(승률 식 표시는 win_rate_text 그대로). 대리자는 그리기만 한다."""
    label: str
    win: int = 0
    draw: int = 0
    lose: int = 0
    rate_text: str = "—"
    rate: float = 0.0               # 정렬 값
    diff: float | None = None       # 기준 대비 %p — None 이면 칸이 없다(표본 미달·기준 없음)
    sub: str = ""                   # 보조 글(흐림)
    weak: bool = False              # 표본 미달 — 막대·숫자 흐림 + "표본 N"
    sample: str = ""                # weak 일 때 붙는 "표본 N" 글(sample_text)
    rated: int = 0                  # 승률 분모(판정에 쓴 표본 — 테스트가 weak 와 같이 읽는다)
    icon: tuple = ()                # (종류, 키) — 등급 배지 · 엠블럼 · 포메이션
    tip: str = ""
    key: object = None              # 쓰는 쪽 값(닉네임 등)
    recent: int = 0                 # 작을수록 최근(정렬 "최근 경기")
    other: int = 0                  # 승·무·패가 아닌 경기("오류" 등) — 경기 수에는 든다(집계의 games 와 같게)

    @property
    def games(self) -> int:
        return self.win + self.draw + self.lose + self.other

    @property
    def accessible(self) -> str:
        return f"{self.label} {self.games}경기 {wdl_text(self.win, self.draw, self.lose)} {self.rate_text}"


RATE_SORTS = (("games", "매치수 순"), ("high", "승률 높은 순"), ("low", "승률 낮은 순"), ("recent", "최근 경기 순"))


def sort_rate_rows(rows: list[RateRow], how: str) -> list[RateRow]:
    """줄 목록 정렬(65 대응표) — 승률 순에서는 표본 미달 줄을 늘 아래로(⑧ — 몇 판짜리 100% 가 맨 위에 오지 않게)."""
    if how == "high":
        return sorted(rows, key=lambda r: (r.weak, -r.rate, -r.games))
    if how == "low":
        return sorted(rows, key=lambda r: (r.weak, r.rate, -r.games))
    if how == "recent":
        return sorted(rows, key=lambda r: r.recent)
    return sorted(rows, key=lambda r: (-r.games, r.recent))


class RateRowDelegate(QStyledItemDelegate):
    """승률 줄 그리기 — 자리는 layout() 한 곳(그리기·툴팁·넘침 검사가 같이 읽는다 — MatchCardDelegate 와 같은 약속)."""
    MARGIN = 3
    PAD_X = 10
    PAD_Y = 6
    GAP = 4
    ITEM_GAP = 10

    def __init__(self, view: CardListView, tips: dict[str, str] | None = None):
        super().__init__(view)
        self.view = view
        self.tips = dict(tips or {})

    fonts = staticmethod(MatchCardDelegate.fonts)

    def row_height(self, base: QFont) -> int:
        f = self.fonts(base)
        line = max(QFontMetrics(f["bold"]).height(), QFontMetrics(f["main"]).height())
        return 2 * self.MARGIN + 2 * self.PAD_Y + line + self.GAP + WdlBar.HEIGHT

    def sizeHint(self, option, index) -> QSize:
        return QSize(max(option.rect.width(), 200), self.row_height(option.font))

    def layout(self, row: RateRow, rect, base: QFont) -> list[CardSeg]:
        f = self.fonts(base)
        fm = {k: QFontMetrics(v) for k, v in f.items()}
        r = QRectF(rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        left, right = r.left() + self.PAD_X, r.right() - self.PAD_X
        y = r.top() + self.PAD_Y
        h = max(fm["bold"].height(), fm["main"].height())
        segs: list[CardSeg] = []
        # 오른쪽 숫자 — 폭을 먼저 재서 왼쪽 이름이 쓸 수 있는 끝을 정한다
        nums = [("games", f"{row.games:,}경기", "small"), ("win", f"{row.win:,}승", "bold"),
                ("draw", f"{row.draw:,}무", "bold"), ("lose", f"{row.lose:,}패", "bold"), ("rate", row.rate_text, "bold")]
        if row.diff is not None and not row.weak:
            nums.append(("diff", f"({row.diff:+.1f})", "main"))
        if row.weak and row.sample:
            nums.append(("sample", row.sample, "small"))
        widths = [fm[font].horizontalAdvance(text) for _r, text, font in nums]
        x = right - sum(widths) - self.ITEM_GAP * (len(nums) - 1)
        nums_left = x
        for (role, text, _font), w in zip(nums, widths):
            segs.append(CardSeg(role, QRectF(x, y, w, h), text, w))
            x += w + self.ITEM_GAP
        limit = nums_left - 16
        x = left
        if row.icon:
            ih = fm["main"].height()
            iw = ih * 1.4
            segs.append(CardSeg("icon", QRectF(x, y + (h - ih) / 2, iw, ih), "", 0))
            x += iw + 6
        full = fm["bold"].horizontalAdvance(row.label)
        w = max(0.0, min(full, limit - x))
        segs.append(CardSeg("label", QRectF(x, y, w, h), row.label, full))
        x += w + 8
        if row.sub and x < limit:
            full = fm["small"].horizontalAdvance(row.sub)
            segs.append(CardSeg("sub", QRectF(x, y, max(0.0, min(full, limit - x)), h), row.sub, full))
        segs.append(CardSeg("bar", QRectF(left, y + h + self.GAP, right - left, WdlBar.HEIGHT), "", 0))
        return segs

    def overflow(self, row: RateRow, width: int, base: QFont) -> list[str]:
        """그 폭에서 잘리는 글자 조각 — 보조 글(sub)은 잘려도 된다(툴팁에 같은 글). 이름·숫자가 잘리면 스모크가 FAIL."""
        return [s.role for s in self.layout(row, QRectF(0, 0, width, self.row_height(base)), base)
                if s.text and s.role != "sub" and s.full > s.rect.width() + 0.5]

    @staticmethod
    def colors(row: RateRow) -> dict[str, str]:
        """글자·막대 색 — 테스트가 그림 대신 읽는다. 승·패 숫자와 차이만 색(기준 화면 매치업 줄) · 표본 미달은 흐림."""
        bar = WdlBar.colors_for(row.weak, compact=True)
        if row.weak:
            return {**{k: T.TEXT_DIM for k in ("win", "draw", "lose", "rate", "diff", "label")}, "bar": bar}
        return {"win": T.GOOD, "draw": T.TEXT, "lose": T.BAD, "rate": T.TEXT, "label": T.TEXT,
                "diff": delta_color(row.diff) if row.diff is not None else T.TEXT_DIM, "bar": bar}

    def paint(self, painter, option, index) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        painter.save()
        try:
            self._paint(painter, option, row)
        finally:
            painter.restore()

    def _paint(self, p: QPainter, option, row: RateRow) -> None:
        f = self.fonts(option.font)
        col = self.colors(row)
        r = QRectF(option.rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = T.PANEL
        if option.state & QStyle.StateFlag.State_MouseOver:
            bg = T.blend(bg, T.TEXT, 0.04)
        path = QPainterPath()
        path.addRoundedRect(r, T.RADIUS, T.RADIUS)
        p.fillPath(path, QColor(bg))
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(QPen(QColor(T.ACCENT), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        fonts = {"games": "small", "sample": "small", "sub": "small", "diff": "main", "label": "bold"}
        for s in self.layout(row, option.rect, option.font):
            if s.role == "bar":
                paint_wdl_strip(p, s.rect, wdl_segments((row.win, row.draw, row.lose), col["bar"]))
                continue
            if s.role == "icon":
                pm = self.view.icon(*row.icon) if row.icon else None
                if pm is not None and not pm.isNull():
                    scaled = pm.scaled(s.rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(QPointF(s.rect.left(), s.rect.top() + (s.rect.height() - scaled.height()) / 2), scaled)
                continue
            font = fonts.get(s.role, "bold")
            p.setFont(f[font])
            p.setPen(QColor(col.get(s.role, T.TEXT_DIM)))
            text = s.text if s.full <= s.rect.width() + 0.5 else QFontMetrics(f[font]).elidedText(
                s.text, Qt.TextElideMode.ElideRight, max(0, int(s.rect.width())))
            p.drawText(s.rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def tip_at(self, row: RateRow, rect, base: QFont, pos) -> str:
        for s in self.layout(row, rect, base):
            if s.rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                if s.role in self.tips:
                    return self.tips[s.role]
                break
        return row.tip

    def helpEvent(self, event, view, option, index) -> bool:
        row = index.data(ROW_ROLE) if index.isValid() else None
        tip = self.tip_at(row, option.rect, option.font, event.pos()) if row is not None else ""
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        QToolTip.hideText()
        return False


class RateRowList(CardListView):
    """승률 줄 목록(65 P4 첫 부품) — 상대 전적 · 성적 진단 묶음 · 전술 상대 포메이션 · 경기 결과.
    fixed 면 줄 수만큼 높이를 고정한다(스크롤 페이지 안 — 목록 안 스크롤이 겹치지 않게). 그림은 CardListView 의 (종류, 키) 표."""

    def __init__(self, fixed: bool = False, tips: dict[str, str] | None = None):
        super().__init__()
        self.fixed = fixed
        self.rate_delegate = RateRowDelegate(self, tips)
        self.setItemDelegate(self.rate_delegate)
        if fixed:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QListView { background: transparent; border: none; }")

    def icon_keys(self, row: RateRow) -> tuple:
        return (row.icon,) if row.icon else ()

    def set_rows(self, rows: list) -> None:
        super().set_rows(rows)
        self._fit()

    def set_filter(self, pred) -> None:
        super().set_filter(pred)
        self._fit()

    def _fit(self) -> None:
        if self.fixed:
            self.setFixedHeight(self.rowCount() * self.rate_delegate.row_height(self.font()) + 2 * self.frameWidth() + 2)


class _FitMixin:
    """fixed 면 줄 수만큼 높이를 고정한다(스크롤 페이지 안 — 목록 안 스크롤이 겹치지 않게). RateRowList 와 같은 약속."""

    def _setup_fixed(self, fixed: bool) -> None:
        self.fixed = fixed
        if fixed:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QListView { background: transparent; border: none; }")

    def _fit(self) -> None:
        if self.fixed:
            self.setFixedHeight(self.rowCount() * self.itemDelegate().row_height(self.font()) + 2 * self.frameWidth() + 2)


class MetricCell(NamedTuple):
    text: str                       # 값(굵게)
    label: str                      # 값 아래 작은 라벨(흐림)
    color: str | None = None        # 값 색 — None 이면 글자색
    tip: str = ""


@dataclass
class MetricRow:
    """지표 줄 목록 한 줄(67 R0) — 순위 · 그림 · 이름 + 아래 흐린 보조 글 · 오른쪽 숫자 칸 N개(값 + 그 아래 라벨) ·
    바탕을 fill(0~1)만큼 채움. 글자는 화면이 다 만들어 넣는다. 칸 폭은 목록 전체에서 맞춘다(줄마다 숫자가 같은 줄에 선다)."""
    name: str
    sub: str = ""
    cells: tuple = ()                # (MetricCell, …)
    rank: int | None = None
    icon: tuple = ()                 # (종류, 키) — 엠블럼 · 포메이션
    fill: float | None = None        # 바탕 채움 0~1 — None 이면 없음
    dim: bool = False                # 표본 미달 — 전부 흐림
    tip: str = ""
    key: object = None               # 쓰는 쪽 값(팀컬러 라벨 · 구간 번호)
    sort: dict = None                # 정렬 값(쓰는 쪽이 이름 → 값) — 목록은 안 읽는다

    @property
    def accessible(self) -> str:
        return " ".join([self.name] + [f"{c.label} {c.text}" for c in self.cells])


class MetricRowDelegate(QStyledItemDelegate):
    """지표 줄 그리기 — 자리는 layout() 한 곳(그리기·툴팁·넘침 검사가 같이 읽는다 — RateRowDelegate 와 같은 약속)."""
    MARGIN = 3
    PAD_X = 10
    PAD_Y = 6
    GAP = 2
    CELL_GAP = 16

    def __init__(self, view: CardListView):
        super().__init__(view)
        self.view = view
        self.col_w: list[float] = []     # 칸마다 폭 — measure() 가 목록 전체에서
        self.rank_w = 0.0

    fonts = staticmethod(MatchCardDelegate.fonts)

    def row_height(self, base: QFont) -> int:
        f = self.fonts(base)
        return 2 * self.MARGIN + 2 * self.PAD_Y + QFontMetrics(f["bold"]).height() + self.GAP + QFontMetrics(f["small"]).height()

    def sizeHint(self, option, index) -> QSize:
        return QSize(max(option.rect.width(), 200), self.row_height(option.font))

    def measure(self, rows: list, base: QFont) -> None:
        f = self.fonts(base)
        bold, small = QFontMetrics(f["bold"]), QFontMetrics(f["small"])
        n = max((len(r.cells) for r in rows), default=0)
        w = [0.0] * n
        for r in rows:
            for i, c in enumerate(r.cells):
                w[i] = max(w[i], bold.horizontalAdvance(c.text), small.horizontalAdvance(c.label))
        self.col_w = w
        ranks = [r.rank for r in rows if r.rank is not None]
        self.rank_w = bold.horizontalAdvance(f"{max(ranks):,}") if ranks else 0.0

    def layout(self, row: MetricRow, rect, base: QFont) -> list[CardSeg]:
        f = self.fonts(base)
        fm = {k: QFontMetrics(v) for k, v in f.items()}
        r = QRectF(rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        left, right = r.left() + self.PAD_X, r.right() - self.PAD_X
        top = r.top() + self.PAD_Y
        h1, h2 = fm["bold"].height(), fm["small"].height()
        y2 = top + h1 + self.GAP
        segs: list[CardSeg] = []
        widths = [self.col_w[i] if i < len(self.col_w) else fm["bold"].horizontalAdvance(c.text)
                  for i, c in enumerate(row.cells)]
        x = right - sum(widths) - self.CELL_GAP * max(0, len(widths) - 1)
        cells_left = x
        for i, (c, w) in enumerate(zip(row.cells, widths)):
            segs.append(CardSeg(f"cell{i}", QRectF(x, top, w, h1), c.text, fm["bold"].horizontalAdvance(c.text)))
            segs.append(CardSeg(f"label{i}", QRectF(x, y2, w, h2), c.label, fm["small"].horizontalAdvance(c.label)))
            x += w + self.CELL_GAP
        limit = cells_left - 16
        x = left
        if self.rank_w:
            text = f"{row.rank:,}" if row.rank is not None else ""
            segs.append(CardSeg("rank", QRectF(x, top, self.rank_w, h1 + self.GAP + h2), text,
                                fm["bold"].horizontalAdvance(text)))
            x += self.rank_w + 10
        if row.icon:
            ih = h1 + self.GAP + h2 - 4
            segs.append(CardSeg("icon", QRectF(x, top + 2, ih * 1.2, ih), "", 0))
            x += ih * 1.2 + 8
        full = fm["bold"].horizontalAdvance(row.name)
        segs.append(CardSeg("name", QRectF(x, top, max(0.0, min(full, limit - x)), h1), row.name, full))
        if row.sub:
            full = fm["small"].horizontalAdvance(row.sub)
            segs.append(CardSeg("sub", QRectF(x, y2, max(0.0, min(full, limit - x)), h2), row.sub, full))
        return segs

    def overflow(self, row: MetricRow, width: int, base: QFont) -> list[str]:
        """그 폭에서 잘리는 글자 조각 — 보조 글은 잘려도 된다(툴팁). 이름·칸이 잘리면 스모크가 FAIL."""
        return [s.role for s in self.layout(row, QRectF(0, 0, width, self.row_height(base)), base)
                if s.text and s.role != "sub" and s.full > s.rect.width() + 0.5]

    @staticmethod
    def colors(row: MetricRow) -> dict[str, str]:
        """글자 색 — 테스트가 그림 대신 읽는다. 칸 값은 쓰는 쪽 색(평균 대비 등) · 표본 미달은 전부 흐림."""
        out = {"name": T.TEXT, "sub": T.TEXT_DIM, "rank": T.TEXT_DIM, "fill": T.ROW_FILL}
        for i, c in enumerate(row.cells):
            out[f"cell{i}"] = T.TEXT_DIM if row.dim else (c.color or T.TEXT)
            out[f"label{i}"] = T.TEXT_DIM
        if row.dim:
            out["name"] = T.TEXT_DIM
        return out

    def paint(self, painter, option, index) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        painter.save()
        try:
            self._paint(painter, option, row)
        finally:
            painter.restore()

    def _paint(self, p: QPainter, option, row: MetricRow) -> None:
        f = self.fonts(option.font)
        col = self.colors(row)
        r = QRectF(option.rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = T.PANEL
        if option.state & QStyle.StateFlag.State_MouseOver:
            bg = T.blend(bg, T.TEXT, 0.04)
        path = QPainterPath()
        path.addRoundedRect(r, T.RADIUS, T.RADIUS)
        p.fillPath(path, QColor(bg))
        if row.fill:
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(r.left(), r.top(), r.width() * min(1.0, max(0.0, row.fill)), r.height()), QColor(col["fill"]))
            p.restore()
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(QPen(QColor(T.ACCENT), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        for s in self.layout(row, option.rect, option.font):
            if s.role == "icon":
                pm = self.view.icon(*row.icon) if row.icon else None
                if pm is not None and not pm.isNull():
                    scaled = pm.scaled(s.rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(QPointF(s.rect.left() + (s.rect.width() - scaled.width()) / 2,
                                         s.rect.top() + (s.rect.height() - scaled.height()) / 2), scaled)
                continue
            font = "small" if s.role == "sub" or s.role.startswith("label") else "bold"
            align = Qt.AlignmentFlag.AlignLeft
            if s.role.startswith(("cell", "label", "rank")):
                align = Qt.AlignmentFlag.AlignRight
            p.setFont(f[font])
            p.setPen(QColor(col.get(s.role, T.TEXT_DIM)))
            text = s.text if s.full <= s.rect.width() + 0.5 else QFontMetrics(f[font]).elidedText(
                s.text, Qt.TextElideMode.ElideRight, max(0, int(s.rect.width())))
            p.drawText(s.rect, Qt.AlignmentFlag.AlignVCenter | align, text)

    def tip_at(self, row: MetricRow, rect, base: QFont, pos) -> str:
        for s in self.layout(row, rect, base):
            if s.rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                if s.role.startswith(("cell", "label")):
                    c = row.cells[int(s.role[4:] if s.role.startswith("cell") else s.role[5:])]
                    if c.tip:
                        return c.tip
                if s.role in ("name", "sub") and s.full > s.rect.width() + 0.5:
                    return f"{row.name} {row.sub}".strip()
                break
        return row.tip

    def helpEvent(self, event, view, option, index) -> bool:
        row = index.data(ROW_ROLE) if index.isValid() else None
        tip = self.tip_at(row, option.rect, option.font, event.pos()) if row is not None else ""
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        QToolTip.hideText()
        return False


class MetricRowList(_FitMixin, CardListView):
    """지표 줄 목록(67 R0 — 팀컬러 목록 · 구단가치 구간 · 메타 변화 · 포지션 특성). 거르기는 모델 안(CardListView) ·
    칸 폭은 set_rows 때 전체 줄에서 잰다(거르기로 줄이 바뀌어도 칸이 흔들리지 않게)."""

    def __init__(self, fixed: bool = False):
        super().__init__()
        self.metric_delegate = MetricRowDelegate(self)
        self.setItemDelegate(self.metric_delegate)
        self._setup_fixed(fixed)

    def icon_keys(self, row: MetricRow) -> tuple:
        return (row.icon,) if row.icon else ()

    def set_rows(self, rows: list) -> None:
        self.metric_delegate.measure(rows, self.font())
        super().set_rows(rows)
        self._fit()

    def set_filter(self, pred) -> None:
        super().set_filter(pred)
        self._fit()


@dataclass
class AvgBar:
    """평균 막대 줄 한 줄(67 R0) — 이름 · 가로 막대(기준선 위/아래 색) · 오른쪽 값 + 흐린 인원. value None 이면 막대 없이 "—"."""
    name: str
    value: float | None
    text: str                        # 값 글자(단위 포함)
    n_text: str = ""                 # 흐린 보조(인원 등)
    icon: tuple = ()
    key: object = None
    tip: str = ""

    @property
    def accessible(self) -> str:
        return f"{self.name} {self.text} {self.n_text}".strip()


class AvgBarDelegate(QStyledItemDelegate):
    MARGIN = 2
    PAD_X = 10
    PAD_Y = 4
    BAR_H = 10
    NAME_MAX = 0.4                   # 이름 칸 상한(폭 대비)

    def __init__(self, view: "AvgBarList"):
        super().__init__(view)
        self.view = view

    fonts = staticmethod(MatchCardDelegate.fonts)

    def row_height(self, base: QFont) -> int:
        f = self.fonts(base)
        return 2 * self.MARGIN + 2 * self.PAD_Y + max(QFontMetrics(f["bold"]).height(), self.BAR_H)

    def sizeHint(self, option, index) -> QSize:
        return QSize(max(option.rect.width(), 200), self.row_height(option.font))

    def layout(self, row: AvgBar, rect, base: QFont) -> list[CardSeg]:
        f = self.fonts(base)
        bold, small = QFontMetrics(f["bold"]), QFontMetrics(f["small"])
        r = QRectF(rect).adjusted(0, self.MARGIN, 0, -self.MARGIN)
        left, right = r.left() + self.PAD_X, r.right() - self.PAD_X
        h = bold.height()
        y = r.top() + (r.height() - h) / 2
        v = self.view
        segs: list[CardSeg] = []
        x = right - v.n_w
        segs.append(CardSeg("n", QRectF(x, y, v.n_w, h), row.n_text, small.horizontalAdvance(row.n_text)))
        x -= v.text_w + (8 if v.n_w else 0)
        segs.append(CardSeg("text", QRectF(x, y, v.text_w, h), row.text, bold.horizontalAdvance(row.text)))
        bar_right = x - 12
        nx = left
        if row.icon:
            segs.append(CardSeg("icon", QRectF(nx, y, h * 1.2, h), "", 0))
            nx += h * 1.2 + 6
        name_w = min(v.name_w, (right - left) * self.NAME_MAX)
        full = bold.horizontalAdvance(row.name)
        segs.append(CardSeg("name", QRectF(nx, y, min(full, name_w), h), row.name, full))
        bar_left = left + (h * 1.2 + 6 if v.has_icon else 0) + name_w + 12
        width = max(0.0, bar_right - bar_left)
        segs.append(CardSeg("track", QRectF(bar_left, r.top() + (r.height() - self.BAR_H) / 2, width, self.BAR_H), "", 0))
        if row.value is not None and v.max_value > 0:
            segs.append(CardSeg("bar", QRectF(bar_left, r.top() + (r.height() - self.BAR_H) / 2,
                                              width * max(0.0, row.value) / v.max_value, self.BAR_H), "", 0))
        return segs

    def overflow(self, row: AvgBar, width: int, base: QFont) -> list[str]:
        return [s.role for s in self.layout(row, QRectF(0, 0, width, self.row_height(base)), base)
                if s.text and s.role != "name" and s.full > s.rect.width() + 0.5]

    def colors(self, row: AvgBar) -> dict[str, str]:
        """기준(평균) 위가 좋음이면 위 = 강조 채움 · 아래 = 주의 채움(higher_better=False 면 뒤집는다 — 판수는 적을수록 좋다)."""
        above = row.value is not None and row.value >= self.view.base
        good = above if self.view.higher_better else not above
        return {"bar": T.ACCENT_FILL if good else T.WARN_FILL, "text": T.TEXT, "n": T.TEXT_DIM, "name": T.TEXT,
                "track": T.PANEL_2, "base": T.TEXT_DIM}

    def paint(self, painter, option, index) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        painter.save()
        try:
            self._paint(painter, option, row)
        finally:
            painter.restore()

    def _paint(self, p: QPainter, option, row: AvgBar) -> None:
        f = self.fonts(option.font)
        col = self.colors(row)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        segs = self.layout(row, option.rect, option.font)
        for s in segs:
            if s.role in ("track", "bar"):
                path = QPainterPath()
                path.addRoundedRect(s.rect, 3, 3)
                p.fillPath(path, QColor(col[s.role]))
                continue
            if s.role == "icon":
                pm = self.view.icon(*row.icon) if row.icon else None
                if pm is not None and not pm.isNull():
                    scaled = pm.scaled(s.rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(QPointF(s.rect.left(), s.rect.top() + (s.rect.height() - scaled.height()) / 2), scaled)
                continue
            font = "small" if s.role == "n" else "bold"
            p.setFont(f[font])
            p.setPen(QColor(col.get(s.role, T.TEXT)))
            text = s.text if s.full <= s.rect.width() + 0.5 else QFontMetrics(f[font]).elidedText(
                s.text, Qt.TextElideMode.ElideRight, max(0, int(s.rect.width())))
            align = Qt.AlignmentFlag.AlignLeft if s.role == "name" else Qt.AlignmentFlag.AlignRight
            p.drawText(s.rect, Qt.AlignmentFlag.AlignVCenter | align, text)
        track = next((s for s in segs if s.role == "track"), None)
        if track is not None and self.view.max_value > 0 and self.view.base > 0:
            x = track.rect.left() + track.rect.width() * self.view.base / self.view.max_value
            pen = QPen(QColor(col["base"]), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, track.rect.top() - 3), QPointF(x, track.rect.bottom() + 3))

    def helpEvent(self, event, view, option, index) -> bool:
        row = index.data(ROW_ROLE) if index.isValid() else None
        tip = (row.tip or (row.name if row is not None else "")) if row is not None else ""
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        QToolTip.hideText()
        return False


class AvgBarList(_FitMixin, CardListView):
    """평균 막대 줄 목록(67 R0 — 구단가치 구간별 · 팀컬러별 100판당 · 달성 판수). base = 기준선(평균) ·
    higher_better=False 면 기준 아래가 강조(3회차 M8 — 달성 판수는 적을수록 좋다). 막대 길이는 목록 최댓값 대비."""

    def __init__(self, fixed: bool = True, higher_better: bool = True):
        super().__init__()
        self.higher_better = higher_better
        self.base = 0.0
        self.max_value = 0.0
        self.name_w = self.text_w = self.n_w = 0.0
        self.has_icon = False
        self.bar_delegate = AvgBarDelegate(self)
        self.setItemDelegate(self.bar_delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._setup_fixed(fixed)

    def icon_keys(self, row: AvgBar) -> tuple:
        return (row.icon,) if row.icon else ()

    def set_rows(self, rows: list, base: float | None = None) -> None:
        """base None 이면 값 있는 줄의 평균."""
        vals = [r.value for r in rows if r.value is not None]
        self.base = base if base is not None else (sum(vals) / len(vals) if vals else 0.0)
        self.max_value = max(vals + [self.base], default=0.0)
        f = AvgBarDelegate.fonts(self.font())
        bold, small = QFontMetrics(f["bold"]), QFontMetrics(f["small"])
        self.name_w = max((bold.horizontalAdvance(r.name) for r in rows), default=0)
        self.text_w = max((bold.horizontalAdvance(r.text) for r in rows), default=0)
        self.n_w = max((small.horizontalAdvance(r.n_text) for r in rows), default=0)
        self.has_icon = any(r.icon for r in rows)
        super().set_rows(rows)
        self._fit()


class ShotTypeRows(QWidget):
    """슛 타입 분포(65 M6 · ④ M8 이 같이 쓴다) — 줄마다 이름 · "시도 N · 유효 N · 골 N" · 누적 막대(골 ⊂ 유효 ⊂ 시도 ·
    길이는 가장 많은 줄 대비) · 전환율(골 ÷ 시도). 시도가 min_shots 미만이면 전환율을 숨기고 막대를 흐린다(작은 표본 규칙)."""
    BAR_H = 8

    def __init__(self, tone: str = T.GOOD_FILL, stacked: bool = False):
        super().__init__()
        self.tone = tone
        self.stacked = stacked   # 좁은 자리(슛 맵 오른쪽 패널) — 시도·유효·골 글을 이름 아래 줄로
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(1 if stacked else 2, 1)
        self.rows: list[tuple] = []   # (이름, 시도, 유효(골 포함), 골, 전환율 글) — 테스트가 읽는다

    def set_data(self, buckets, min_shots: int) -> None:
        """buckets: stats.ShotBucket 목록(label · shots · on_target(골 포함) · goals)."""
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.rows = []
        peak = max((b.shots for b in buckets), default=0)
        for i, b in enumerate(buckets):
            weak = core.is_weak(b.shots, min_shots)
            conv = f"{b.goals / b.shots * 100:.0f}%" if b.shots and not weak else NA
            self.rows.append((b.label, b.shots, b.on_target, b.goals, conv))
            name = QLabel(b.label)
            name.setStyleSheet(f"color: {T.TEXT};")
            counts = QLabel(f"시도 {b.shots:,} · 유효 {b.on_target:,} · 골 {b.goals:,}")
            counts.setStyleSheet(f"color: {T.TEXT_DIM};")
            strip = _StackStrip(self, (b.goals, b.on_target - b.goals, b.shots - b.on_target), peak, self.tone, weak)
            rate = QLabel(conv)
            rate.setStyleSheet(f"color: {T.TEXT_DIM if weak else T.TEXT}; font-weight: bold;")
            rate.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rate.setToolTip(f"골 ÷ 시도 — 시도 {min_shots}회 미만이면 숨깁니다" if weak else "골 ÷ 시도")
            if self.stacked:
                counts.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 13px;")
                for c, wdg in enumerate((name, strip, rate)):
                    self.grid.addWidget(wdg, 2 * i, c)
                self.grid.addWidget(counts, 2 * i + 1, 0, 1, 3)
                continue
            for c, wdg in enumerate((name, counts, strip, rate)):
                self.grid.addWidget(wdg, i, c)


class _StackStrip(QWidget):
    """ShotTypeRows 의 누적 막대 — (골, 막힌 유효, 빗나감) 을 가장 많은 줄 대비 길이로."""

    def __init__(self, parent: QWidget, parts: tuple[int, int, int], peak: int, tone: str, weak: bool):
        super().__init__(parent)
        self.parts, self.peak, self.weak = parts, peak, weak
        cols = [tone, T.blend(T.PANEL, tone, 0.45), T.blend(T.PANEL, T.TEXT_DIM, WdlBar.DRAW_MIX)]
        self.cols = [T.blend(T.PANEL_2, c, T.WEAK_MIX) for c in cols] if weak else cols
        self.setFixedHeight(ShotTypeRows.BAR_H)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        total = sum(self.parts)
        span = r.width() * (total / self.peak if self.peak else 0)
        segs = [(n / total * span / r.width(), c) for n, c in zip(self.parts, self.cols) if n] if total else []
        paint_wdl_strip(p, r, segs)
        p.end()


class MeterBar(QWidget):
    """한 값 막대(65 M7 패스 성공률) — 바탕 띠 위에 frac(0~1)만큼 한 색. weak 면 바탕 쪽으로 흐린다(표본 흐림 WEAK_MIX).
    그리기는 승무패 띠와 같은 paint_wdl_strip(모서리·바탕 트랙이 갈리지 않게)."""
    HEIGHT = 6

    def __init__(self, frac: float, color: str, weak: bool = False):
        super().__init__()
        self.frac = max(0.0, min(frac, 1.0))
        self.color = T.blend(T.PANEL_2, color, T.WEAK_MIX) if weak else color
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_wdl_strip(p, QRectF(self.rect()), [(self.frac, self.color)] if self.frac else [])
        p.end()


@dataclass
class ScorerRow:
    """골·어시스트 순위 한 줄(65 M8) — 글자는 화면이 만든다. season = 시즌 배지 그림 키 · grade = 강화(배지)."""
    rank: int
    name: str
    goals: int
    assists: int
    grade: int = 0
    season: object = None
    tip: str = ""

    @property
    def accessible(self) -> str:
        return f"{self.rank}위 {self.name} {self.goals}골 {self.assists}어시"


class ScorerDelegate(QStyledItemDelegate):
    """순위 줄 그리기 — 순위 · 시즌 배지 · 이름 · 강화 배지 · 오른쪽 "N골 M어시". 자리는 layout() 한 곳(RateRowDelegate 와 같은 약속)."""
    PAD_X = 8
    PAD_Y = 4
    BADGE_PAD = 6
    ITEM_GAP = 8

    def __init__(self, view: CardListView):
        super().__init__(view)
        self.view = view

    fonts = staticmethod(MatchCardDelegate.fonts)

    def row_height(self, base: QFont) -> int:
        return 2 * self.PAD_Y + QFontMetrics(self.fonts(base)["bold"]).height() + 2

    def sizeHint(self, option, index) -> QSize:
        return QSize(max(option.rect.width(), 160), self.row_height(option.font))

    def layout(self, row: ScorerRow, rect, base: QFont) -> list[CardSeg]:
        f = self.fonts(base)
        fm = {k: QFontMetrics(v) for k, v in f.items()}
        r = QRectF(rect)
        left, right = r.left() + self.PAD_X, r.right() - self.PAD_X
        h = fm["bold"].height()
        y = r.top() + (r.height() - h) / 2
        segs: list[CardSeg] = []
        nums = [("goals", f"{row.goals}골", "bold"), ("assists", f"{row.assists}어시", "main")]
        widths = [fm[font].horizontalAdvance(text) for _r, text, font in nums]
        x = right - sum(widths) - self.ITEM_GAP
        limit = x - self.ITEM_GAP
        for (role, text, _font), w in zip(nums, widths):
            segs.append(CardSeg(role, QRectF(x, y, w, h), text, w))
            x += w + self.ITEM_GAP
        rank = f"{row.rank}"
        rw = fm["small"].horizontalAdvance("00")
        segs.append(CardSeg("rank", QRectF(left, y, rw, h), rank, fm["small"].horizontalAdvance(rank)))
        x = left + rw + 6
        ih = fm["main"].height()
        segs.append(CardSeg("icon", QRectF(x, y + (h - ih) / 2, ih * 1.4, ih), "", 0))
        x += ih * 1.4 + 6
        badge = f"{row.grade}" if row.grade >= 1 else ""
        bw = fm["small"].horizontalAdvance(badge) + 2 * self.BADGE_PAD if badge else 0
        full = fm["bold"].horizontalAdvance(row.name)
        w = max(0.0, min(full, limit - x - (bw + 6 if bw else 0)))
        segs.append(CardSeg("name", QRectF(x, y, w, h), row.name, full))
        if badge:
            segs.append(CardSeg("grade", QRectF(x + w + 6, y + 1, bw, h - 2), badge, bw))
        return segs

    def overflow(self, row: ScorerRow, width: int, base: QFont) -> list[str]:
        """그 폭에서 잘리거나 겹치는 조각 — 이름은 잘려도 된다(툴팁에 전체) · 숫자·배지가 잘리거나 이름 자리(순위·배지 뒤)를
        파고들면 스모크가 FAIL(숫자는 오른쪽부터 제 폭대로 놓여 폭으로는 안 잘린다 — 왼쪽 끝으로 잰다)."""
        segs = self.layout(row, QRectF(0, 0, width, self.row_height(base)), base)
        name_left = next(s.rect.left() for s in segs if s.role == "name")
        return [s.role for s in segs if s.text and s.role != "name"
                and (s.full > s.rect.width() + 0.5 or (s.role in ("goals", "assists") and s.rect.left() < name_left - 0.5))]

    def paint(self, painter, option, index) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        painter.save()
        try:
            self._paint(painter, option, row)
        finally:
            painter.restore()

    def _paint(self, p: QPainter, option, row: ScorerRow) -> None:
        f = self.fonts(option.font)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_MouseOver:
            p.fillRect(option.rect, QColor(T.blend(T.PANEL, T.TEXT, 0.04)))
        colors = {"rank": T.TEXT_DIM, "name": T.TEXT, "goals": T.GOOD, "assists": T.TEXT}
        fonts = {"rank": "small", "name": "bold", "goals": "bold", "assists": "main", "grade": "small"}
        for s in self.layout(row, option.rect, option.font):
            if s.role == "icon":
                pm = self.view.icon("season", row.season)
                if pm is not None and not pm.isNull():
                    scaled = pm.scaled(s.rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(QPointF(s.rect.left(), s.rect.top() + (s.rect.height() - scaled.height()) / 2), scaled)
                continue
            font = f[fonts[s.role]]
            p.setFont(font)
            if s.role == "grade":
                bg, fg = _grade_badge_colors(s.text)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(bg))
                p.drawRoundedRect(s.rect, s.rect.height() / 2, s.rect.height() / 2)
                p.setPen(QColor(fg))
                p.drawText(s.rect, Qt.AlignmentFlag.AlignCenter, s.text)
                continue
            p.setPen(QColor(colors[s.role]))
            text = s.text if s.full <= s.rect.width() + 0.5 else QFontMetrics(font).elidedText(
                s.text, Qt.TextElideMode.ElideRight, max(0, int(s.rect.width())))
            align = Qt.AlignmentFlag.AlignRight if s.role == "rank" else Qt.AlignmentFlag.AlignLeft
            p.drawText(s.rect, Qt.AlignmentFlag.AlignVCenter | align, text)

    def helpEvent(self, event, view, option, index) -> bool:
        row = index.data(ROW_ROLE) if index.isValid() else None
        if row is not None and row.tip:
            QToolTip.showText(event.globalPos(), row.tip, view)
            return True
        QToolTip.hideText()
        return False


class ScorerList(CardListView):
    """골·어시스트 순위(65 M8 · 슛 맵 오른쪽) — 줄 수만큼 높이 고정(스크롤 패널 안). 시즌 배지는 CardListView 의 그림 표."""

    def __init__(self):
        super().__init__()
        self.scorer_delegate = ScorerDelegate(self)
        self.setItemDelegate(self.scorer_delegate)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QListView { background: transparent; border: none; }")

    def icon_keys(self, row: ScorerRow) -> tuple:
        return (("season", row.season),)

    def set_rows(self, rows: list) -> None:
        super().set_rows(rows)
        self.setFixedHeight(self.rowCount() * self.scorer_delegate.row_height(self.font()) + 2 * self.frameWidth() + 2)


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


class Segmented(QFrame):
    """작은 전환 단추 묶음(66 G2 — [일별 | 시간별]). 하나만 눌린다 · 못 고르는 칸은 set_option_enabled 로 끄고 이유는 툴팁."""
    changed = pyqtSignal(int)

    def __init__(self, options: list[str], current: int = 0):
        super().__init__()
        self.setObjectName("segmented")
        self.setStyleSheet(
            f"QFrame#segmented {{ border: 1px solid {T.BORDER}; border-radius: 6px; }}"
            f"QFrame#segmented QPushButton {{ border: none; border-radius: 5px; padding: 2px 10px;"
            f" background: transparent; color: {T.TEXT_DIM}; }}"
            f"QFrame#segmented QPushButton:checked {{ background: {T.ACCENT_SOFT}; color: {T.ACCENT}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == current)
            b.clicked.connect(lambda _=False, k=i: self.set_current(k, emit=True))
            lay.addWidget(b)
            self.buttons.append(b)
        self._current = current

    def current(self) -> int:
        return self._current

    def set_current(self, i: int, emit: bool = False) -> None:
        for k, b in enumerate(self.buttons):
            b.setChecked(k == i)
        if i != self._current:
            self._current = i
            if emit:
                self.changed.emit(i)

    def set_option_enabled(self, i: int, enabled: bool, tip: str = "") -> None:
        self.buttons[i].setEnabled(enabled)
        self.buttons[i].setToolTip(tip)


class ChoiceChips(Segmented):
    """콤보 꼴로 다루는 칩 묶음(67 R1) — findData · currentData · setCurrentIndex · currentIndexChanged.
    거르기 상태를 콤보와 칩이 같이 쓸 때(app_main._tc_filters — 한 곳이 값을 바꾸면 나머지를 신호 없이 맞춘다)."""
    currentIndexChanged = pyqtSignal(int)

    def __init__(self, items: list[tuple[str, object]], current: int = 0):
        super().__init__([t for t, _d in items], current)
        self._data = [d for _t, d in items]
        self.changed.connect(self.currentIndexChanged)

    def count(self) -> int:
        return len(self._data)

    def findData(self, data) -> int:
        return self._data.index(data) if data in self._data else -1

    def itemData(self, i: int):
        return self._data[i]

    def currentIndex(self) -> int:
        return self._current

    def currentData(self):
        return self._data[self._current] if 0 <= self._current < len(self._data) else None

    def currentText(self) -> str:
        return self.buttons[self._current].text() if 0 <= self._current < len(self.buttons) else ""

    def setCurrentIndex(self, i: int) -> None:
        self.set_current(i, emit=True)


def chip_toggle(text: str) -> QPushButton:
    """켜고 끄는 칩 하나(67 R1 [비주류]) — QCheckBox 와 같은 setChecked/toggled. 모양은 Segmented 의 눌린 칸."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setObjectName("chipToggle")
    b.setStyleSheet(
        f"QPushButton#chipToggle {{ border: 1px solid {T.BORDER}; border-radius: 6px; padding: 2px 10px;"
        f" background: transparent; color: {T.TEXT_DIM}; }}"
        f"QPushButton#chipToggle:checked {{ background: {T.ACCENT_SOFT}; color: {T.ACCENT}; border-color: {T.ACCENT_SOFT}; }}")
    return b


class ZoomBar(QWidget):
    """그래프 구간 확대 막대(55 G1) — 점 n 개 중 [lo, hi] 를 고른다. 양끝 손잡이를 끌거나, 가운데를 끌어 옮기거나,
    바깥을 눌러 가까운 끝을 그리로. 두 번 누르면 전체. 그래프는 손대지 않고 쓰는 쪽이 range_changed 를 받아 잘라 다시 그린다
    (AreaTrendChart 기본값 그림이 얼린 사본과 같아야 해서 — charts 머리말)."""
    range_changed = pyqtSignal(int, int)
    PAD = 10        # 양끝 손잡이가 잘리지 않게
    GRAB = 8        # 손잡이를 잡았다고 보는 거리(px)

    def __init__(self):
        super().__init__()
        self._labels: list[str] = []
        self._key = None
        self._lo = self._hi = 0
        self._drag: tuple[str, float, int, int] | None = None   # (무엇, 누른 x, 그때 lo, 그때 hi)
        self.setMinimumHeight(34)
        self.setMouseTracking(True)
        self.setToolTip("양끝을 끌어 구간을 좁힙니다 · 두 번 누르면 전체")

    def set_labels(self, labels: list[str], key=None) -> None:
        """점마다 이름(날짜). 이름·key 가 그대로면 고른 구간을 지키고, 바뀌면(새 점·다른 계정) 전체로.
        key 는 계정 — 이름이 날짜뿐이라 두 계정의 날짜 범위가 같으면 이름만으로는 계정이 바뀐 줄 모른다."""
        labels = list(labels)
        if labels != self._labels or key != self._key:
            self._labels, self._key = labels, key
            self._lo, self._hi = 0, max(len(labels) - 1, 0)
        self.update()

    def span(self) -> tuple[int, int]:
        return self._lo, self._hi

    def is_full(self) -> bool:
        return self._lo == 0 and self._hi >= len(self._labels) - 1

    def set_span(self, lo: int, hi: int) -> None:
        n = len(self._labels)
        if n == 0:
            return
        lo, hi = max(0, min(lo, n - 1)), max(0, min(hi, n - 1))
        if lo > hi:
            lo, hi = hi, lo
        if lo == hi and n > 1:   # 점 하나면 선이 안 그려진다 — 늘 둘 이상
            lo, hi = (lo, lo + 1) if lo < n - 1 else (lo - 1, lo)
        if (lo, hi) != (self._lo, self._hi):
            self._lo, self._hi = lo, hi
            self.update()
            self.range_changed.emit(lo, hi)

    def _x_of(self, i: int) -> float:
        n = len(self._labels)
        w = max(self.width() - 2 * self.PAD, 1)
        return self.PAD + (w * i / (n - 1) if n > 1 else w / 2)

    def _i_of(self, x: float) -> int:
        n = len(self._labels)
        w = max(self.width() - 2 * self.PAD, 1)
        return max(0, min(n - 1, round((x - self.PAD) / w * (n - 1)))) if n > 1 else 0

    def mousePressEvent(self, event) -> None:
        if len(self._labels) < 2:
            return
        x = event.position().x()
        xl, xh = self._x_of(self._lo), self._x_of(self._hi)
        if abs(x - xl) <= self.GRAB and abs(x - xl) <= abs(x - xh):
            what = "lo"
        elif abs(x - xh) <= self.GRAB:
            what = "hi"
        elif xl < x < xh:
            what = "move"
        else:   # 바깥 — 가까운 끝을 그리로
            what = "lo" if x < xl else "hi"
            i = self._i_of(x)
            self.set_span(i, self._hi) if what == "lo" else self.set_span(self._lo, i)
        self._drag = (what, x, self._lo, self._hi)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        what, x0, lo0, hi0 = self._drag
        n = len(self._labels)
        if what == "move":
            step = self._i_of(event.position().x()) - self._i_of(x0)
            step = max(-lo0, min(n - 1 - hi0, step))
            self.set_span(lo0 + step, hi0 + step)
        elif what == "lo":
            self.set_span(min(self._i_of(event.position().x()), self._hi - 1), self._hi)
        else:
            self.set_span(self._lo, max(self._i_of(event.position().x()), self._lo + 1))

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.set_span(0, len(self._labels) - 1)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self._labels)
        if n < 2:
            return
        font = p.font()
        font.setPointSize(9)
        p.setFont(font)
        cy = 9
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.CHART_GRID))
        p.drawRoundedRect(QRectF(self.PAD, cy - 3, self.width() - 2 * self.PAD, 6), 3, 3)
        xl, xh = self._x_of(self._lo), self._x_of(self._hi)
        p.setBrush(QColor(T.CHART_NEUTRAL))
        p.drawRoundedRect(QRectF(xl, cy - 3, max(xh - xl, 1), 6), 3, 3)
        p.setPen(QPen(QColor(T.PANEL), 2))
        p.setBrush(QColor(T.TEXT_DIM))
        for x in (xl, xh):
            p.drawRoundedRect(QRectF(x - 4, cy - 8, 8, 16), 3, 3)
        p.setPen(QColor(T.TEXT_DIM))
        lo_t, hi_t = self._labels[self._lo], self._labels[self._hi]
        p.drawText(QRectF(0, cy + 9, self.width() / 2, 16), Qt.AlignmentFlag.AlignLeft, lo_t)
        p.drawText(QRectF(self.width() / 2, cy + 9, self.width() / 2, 16), Qt.AlignmentFlag.AlignRight,
                   hi_t if self.is_full() else f"{hi_t}  (두 번 눌러 전체)")


class CalCell(NamedTuple):
    """달력 한 칸의 글 — 화면이 계산해 넘긴다(위젯은 계산 안 한다)."""
    rate: str           # "56%"
    sub: str            # "14판 · 60 FC"
    arrow: int          # +1 ▲ · −1 ▼ · 0
    tip: str
    weak: bool = False  # 판수가 적어 화살표를 안 잰 날 — 흐리게
    fc: int = 0         # 그날 승리 보상 FC — 칸 배경 진하기(66 G5)


def cal_cell_colors(fc: int, fc_max: int, weak: bool) -> tuple[str, str]:
    """칸 (배경, 흐린 글자색) — 배경 = PANEL_2 에 ACCENT_FILL 을 그날 FC ÷ 이 달 최대 × `CAL_FC_MAX_MIX` 만큼(66 G5).
    미달 날·최대 0 은 0(M11 — 미달 날은 흐린 글자 그대로 보여야). 날짜·판수 글자는 TEXT_DIM 이 4.5 밑이면 TEXT 로(M4)."""
    mix = 0.0 if weak or fc_max <= 0 else min(max(fc, 0) / fc_max, 1.0) * config.CAL_FC_MAX_MIX
    bg = T.blend(T.PANEL_2, T.ACCENT_FILL, mix)
    return bg, (T.TEXT_DIM if T.contrast(T.TEXT_DIM, bg) >= 4.5 else T.TEXT)


class CalendarGrid(QWidget):
    """한 달 달력(55 D1) — 일요일부터 7칸 × 주. 경기가 있던 날만 칸을 채운다 · 배경 진하기 = 그날 FC(66 G5)."""
    HEAD_H = 22
    MIN_CELL_H = 54

    def __init__(self):
        super().__init__()
        self._year, self._month = 2000, 1
        self._cells: dict[int, CalCell] = {}
        self._today = None
        self._fc_max = 0
        self._hits: list[tuple[QRectF, str]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(self.HEAD_H + 6 * self.MIN_CELL_H)

    def set_data(self, year: int, month: int, cells: dict[int, CalCell], today=None, fc_max: int = 0) -> None:
        self._year, self._month, self._cells, self._today = year, month, dict(cells), today
        self._fc_max = fc_max
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        for area, text in self._hits:
            if area.contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(T.PANEL))
        self._hits = []
        weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(self._year, self._month)   # 6 = 일요일부터
        cw = self.width() / 7
        ch = max((self.height() - self.HEAD_H) / max(len(weeks), 1), 1)
        base = p.font()
        small = QFont(base)
        small.setPointSize(9)
        big = QFont(base)
        big.setPointSize(base.pointSize() + 1)
        big.setBold(True)
        p.setFont(small)
        p.setPen(QColor(T.TEXT_DIM))
        for i, name in enumerate("일월화수목금토"):
            p.drawText(QRectF(i * cw, 0, cw, self.HEAD_H), Qt.AlignmentFlag.AlignCenter, name)
        for r, week in enumerate(weeks):
            for c, d in enumerate(week):
                if not d:
                    continue
                rect = QRectF(c * cw + 2, self.HEAD_H + r * ch + 2, cw - 4, ch - 4)
                cell = self._cells.get(d)
                bg, dim = cal_cell_colors(cell.fc, self._fc_max, cell.weak) if cell else (T.PANEL, T.TEXT_DIM)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(bg))
                p.drawRoundedRect(rect, 4, 4)
                is_today = self._today is not None and self._today == date(self._year, self._month, d)
                p.setPen(QPen(QColor(T.CHART_NEUTRAL if is_today else T.BORDER), 2 if is_today else 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect, 4, 4)
                p.setFont(small)
                p.setPen(QColor(dim))
                p.drawText(rect.adjusted(5, 3, -5, -3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, str(d))
                if cell is None:
                    continue
                # ▲▼ 는 글자 토큰(GOOD/BAD) — 밝은 CHART_DOWN 은 섞기 0 에서도 3.25 라 진한 칸에서 3.0 을 못 지킨다(66 G5)
                arrow = {1: ("▲", T.GOOD), -1: ("▼", T.BAD)}.get(cell.arrow)
                if arrow:
                    p.setPen(QColor(arrow[1]))
                    p.drawText(rect.adjusted(5, 3, -5, -3), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                               arrow[0])
                p.setFont(big)
                p.setPen(QColor(dim if cell.weak else T.TEXT))
                p.drawText(rect.adjusted(4, 0, -4, -ch * 0.18), Qt.AlignmentFlag.AlignCenter, cell.rate)
                p.setFont(small)
                p.setPen(QColor(dim))
                fm = QFontMetrics(small)
                p.drawText(rect.adjusted(4, 0, -4, -3), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                           fm.elidedText(cell.sub, Qt.TextElideMode.ElideRight, int(rect.width() - 8)))
                self._hits.append((rect, cell.tip))


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
    for lo, bg, fg in T.GRADE_BADGES:
        if g >= lo:
            return bg, fg
    return T.PANEL_2, T.TEXT_DIM


def short_bp(v: int | None) -> str:
    """칩 한 줄에 들어가는 짧은 금액 — 1.2조 · 3,400억 · 5,600만. 모르면 NA."""
    if not isinstance(v, int) or v <= 0:
        return NA
    for unit, name in ((10 ** 12, "조"), (10 ** 8, "억"), (10 ** 4, "만")):
        if v >= unit:
            if unit == 10 ** 12:
                return f"{v / unit:.1f}".rstrip("0").rstrip(".") + name
            return f"{v // unit:,}{name}"
    return f"{v:,}"


@dataclass
class PitchCard:
    """축구장 칩 한 장의 재료 — 화면이 경기 기록에서 만든다(widgets 는 계산하지 않는다)."""
    pos_code: int
    pos_name: str
    name: str
    sp_id: int | None
    grade: int | None = None
    note: str = ""      # 시세 줄 대신 쓸 글(⑨ "만난 12회 · 34%"). 비어 있으면 시세


class _PlayerChip(QWidget):
    """축구장 위 선수 카드 한 장(v2) — 크기는 축구장이 정하고, 내용은 그 크기에 맞춰 그린다.

    위 줄 포지션 · OVR · 강화 배지 / 얼굴 / 시즌 아이콘 + 이름 / 시세(또는 note). 작아지면 얼굴 → 시세 줄 순으로 뺀다
    (글자를 MIN_FONT_PX 밑으로 줄이지 않는다). 클릭하면 clicked — 조회·창은 app_main 몫."""

    clicked = pyqtSignal()
    MIN_FONT_PX = 10
    FACE_MIN_H = 76     # 이보다 낮으면 얼굴을 뺀다
    PRICE_MIN_H = 58    # 이보다 낮으면 시세 줄을 뺀다

    def __init__(self, card: PitchCard, accent: str):
        super().__init__()
        self.card = card
        self.accent = accent
        self.ovr: int | None = None
        self.price: int | None = None
        self.face: QPixmap | None = None
        self.season: QPixmap | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{card.pos_name} · {card.name}\n누르면 선수 카드")

    def set_face(self, pixmap_path: str) -> None:
        pm = QPixmap(pixmap_path)
        if not pm.isNull():
            self.face = pm
            self.update()

    def set_season_icon(self, pixmap_path: str) -> None:
        pm = QPixmap(pixmap_path)
        if not pm.isNull():
            self.season = pm
            self.update()

    def set_card(self, ovr: int | None, price: int | None) -> None:
        self.ovr, self.price = ovr, price
        tip = f"{self.card.pos_name} · {self.card.name}"
        if ovr is not None:
            tip += f"\nOVR {ovr} — 카드 기본 포지션·강화 기준(다른 자리에 세우면 게임 값과 다르다)"
        self.setToolTip(tip + "\n누르면 선수 카드")
        self.update()

    def shows_price_line(self) -> bool:
        return self.height() >= self.PRICE_MIN_H

    def shows_face(self) -> bool:
        return self.height() >= self.FACE_MIN_H

    def _font(self, px: float, bold: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(max(self.MIN_FONT_PX, int(px)))
        f.setBold(bold)
        return f

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(self.accent), 2))
        p.setBrush(QColor(T.PANEL))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 7, 7)

        # 위 줄 — 포지션 배지(왼) · OVR(가운데) · 강화 배지(오른)
        top_h = max(16, int(h * 0.2))
        small = self._font(top_h * 0.62, bold=True)
        p.setFont(small)
        fm = QFontMetrics(small)
        pos_w = min(w // 2 - 4, fm.horizontalAdvance(self.card.pos_name) + 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.blend(T.PANEL, self.accent, T.CHIP_SOFT_MIX)))   # 태그 꼴 — 글자 = 그 색 · 바탕 = 그 색 10%
        pos_r = QRectF(4, 4, pos_w, top_h - 2)
        p.drawRoundedRect(pos_r, 3, 3)
        p.setPen(QColor(self.accent))
        p.drawText(pos_r, Qt.AlignmentFlag.AlignCenter, self.card.pos_name)
        if self.card.grade is not None:
            gbg, gfg = _grade_badge_colors(self.card.grade)
            d = top_h - 2
            g_r = QRectF(w - 4 - d, 4, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(gbg))
            p.drawEllipse(g_r)
            p.setPen(QColor(gfg))
            p.drawText(g_r, Qt.AlignmentFlag.AlignCenter, str(self.card.grade))
        if self.ovr is not None:
            p.setPen(QColor(T.TEXT))
            p.drawText(QRectF(pos_r.right(), 4, w - pos_r.right() - top_h - 4, top_h - 2),
                       Qt.AlignmentFlag.AlignCenter, str(self.ovr))

        y = top_h + 4
        line_h = max(self.MIN_FONT_PX + 4, int(h * 0.15))
        rest = h - y - 4
        price_on = self.shows_price_line()
        name_lines = 1 + (1 if price_on else 0)
        if self.shows_face():
            d = max(16, min(w - 16, rest - name_lines * line_h - 2))
            face_r = QRectF((w - d) / 2, y, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.PANEL_2))
            p.drawEllipse(face_r)
            if self.face is not None:
                p.drawPixmap(face_r.toRect(), self.face)
            y += d + 2

        # 이름 줄 — 시즌 아이콘 + 이름(넘치면 …)
        name_f = self._font(line_h * 0.78, bold=True)
        p.setFont(name_f)
        x0 = 4
        if self.season is not None:
            ic = line_h - 4
            p.drawPixmap(QRectF(x0, y + 2, ic, ic).toRect(), self.season)
            x0 += ic + 2
        avail = w - x0 - 4
        text = QFontMetrics(name_f).elidedText(self.card.name, Qt.TextElideMode.ElideRight, avail)
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(x0, y, avail, line_h), Qt.AlignmentFlag.AlignCenter, text)
        y += line_h

        if price_on:
            line = self.card.note or (short_bp(self.price) if self.price is not None else NA)
            pf = self._font(line_h * 0.7)
            p.setFont(pf)
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(4, y, w - 8, line_h), Qt.AlignmentFlag.AlignCenter,
                       QFontMetrics(pf).elidedText(line, Qt.TextElideMode.ElideRight, w - 8))
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _PitchField(QWidget):
    """잔디·선 + 칩 배치. 줄(rows)마다 높이를 똑같이, 줄 안에서는 폭을 사람 수로 나눠 칩이 겹치지 않는다."""

    MARGIN = 10
    GAP = 6
    CHIP_MAX = (118, 112)       # 넓은 화면에서도 이 이상 안 키운다
    GROUP_SPREAD = 1.7          # 줄 안 칸 폭의 상한 = 칩 최대 폭 × 이것 — 둘뿐인 줄이 양 끝으로 벌어지지 않게

    def __init__(self, rows: list[list[_PlayerChip]]):
        super().__init__()
        self._rows = rows
        for row in rows:
            for chip in row:
                chip.setParent(self)
        self.setMinimumSize(340, 420)

    def sizeHint(self) -> QSize:
        return QSize(520, 600)

    def resizeEvent(self, event) -> None:
        self.layout_chips()
        super().resizeEvent(event)

    def layout_chips(self) -> None:
        if not self._rows:
            return
        m, gap = self.MARGIN, self.GAP
        W, H = self.width() - 2 * m, self.height() - 2 * m
        row_h = H / len(self._rows)
        max_w, max_h = self.CHIP_MAX
        for r, row in enumerate(self._rows):
            cell_w = min(W / len(row), max_w * self.GROUP_SPREAD)
            left = m + (W - cell_w * len(row)) / 2
            cw = int(min(max_w, cell_w - gap))
            ch = int(min(max_h, row_h - gap))
            for i, chip in enumerate(row):
                cx = left + cell_w * (i + 0.5)
                cy = m + row_h * (r + 0.5)
                chip.setGeometry(int(cx - cw / 2), int(cy - ch / 2), max(1, cw), max(1, ch))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(T.PITCH))
        p.setPen(QPen(QColor(T.PITCH_LINE), 2))
        m = self.MARGIN
        p.drawRect(m, m, w - 2 * m, h - 2 * m)
        p.drawLine(m, h // 2, w - m, h // 2)
        p.drawEllipse(QPointF(w / 2, h / 2), 46, 46)
        box_w = int((w - 2 * m) * 0.62)
        box_h = 56
        p.drawRect(int(w / 2 - box_w / 2), m, box_w, box_h)
        p.drawRect(int(w / 2 - box_w / 2), h - m - box_h, box_w, box_h)


class PitchWidget(QWidget):
    """스쿼드 축구장 v2 — 위 띠(스쿼드 가치·급여 합 · 자동 읽기가 막힌 이유 + [안내 보기]) + 축구장.

    rows: 줄(위=공격 → 아래=GK)마다 PitchCard 목록(왼쪽 → 오른쪽) — 배치 계산(core.pitch_rows)은 화면이 한다.
    칩의 OVR·시세는 set_card 로 나중에 채운다(캐시 → 자동 읽기). band=False 면 위 띠가 없다(⑨ — 합이 뜻이 없다)."""

    player_clicked = pyqtSignal(int)   # spId — 선수 카드를 클릭했을 때
    notice_requested = pyqtSignal()    # 띠의 [안내 보기]

    def __init__(self, rows: list[list[PitchCard]], band: bool = True):
        super().__init__()
        self._chips_by_sp: dict[int, list[_PlayerChip]] = {}
        chip_rows = []
        for row in rows:
            chips = []
            for card in row:
                chip = _PlayerChip(card, self._accent_for(card.pos_code))
                chips.append(chip)
                if isinstance(card.sp_id, int):
                    self._chips_by_sp.setdefault(card.sp_id, []).append(chip)
                    chip.clicked.connect(lambda sid=card.sp_id: self.player_clicked.emit(sid))
            chip_rows.append(chips)
        self.field = _PitchField(chip_rows)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.band = QWidget()
        hb = QHBoxLayout(self.band)
        hb.setContentsMargins(2, 0, 2, 0)
        self.lb_value = QLabel("")
        self.lb_value.setStyleSheet(f"color: {T.TEXT}; font-weight: bold;")
        self.lb_value.setWordWrap(True)
        self.lb_gate = QLabel("")
        self.lb_gate.setStyleSheet(f"color: {T.TEXT_DIM};")
        self.lb_gate.setWordWrap(True)
        self.btn_notice = QPushButton("안내 보기")
        self.btn_notice.clicked.connect(self.notice_requested.emit)
        self.btn_notice.hide()
        hb.addWidget(self.lb_value, 1)
        hb.addWidget(self.lb_gate, 1)
        hb.addWidget(self.btn_notice)
        self.band.setVisible(band)
        v.addWidget(self.band)
        v.addWidget(self.field, 1)

    # ── 채우기 ──
    def sp_ids(self) -> list[int]:
        return list(self._chips_by_sp)

    def chips(self) -> list[_PlayerChip]:
        return [c for row in self.field._rows for c in row]

    def set_face(self, sp_id: int, pixmap_path: str) -> None:
        for chip in self._chips_by_sp.get(sp_id, ()):
            chip.set_face(pixmap_path)

    def set_season_icon(self, sp_id: int, pixmap_path: str) -> None:
        for chip in self._chips_by_sp.get(sp_id, ()):
            chip.set_season_icon(pixmap_path)

    def set_card(self, sp_id: int, ovr_of, price_of) -> None:
        """그 카드 칩마다 — ovr_of(강화)·price_of(강화) 로 칩의 강화에 맞는 값(같은 카드가 강화 다르게 둘일 수 있다)."""
        for chip in self._chips_by_sp.get(sp_id, ()):
            chip.set_card(ovr_of(chip.card.grade), price_of(chip.card.grade))

    def set_value_text(self, text: str) -> None:
        self.lb_value.setText(text)

    def set_gate(self, text: str, button: bool = False) -> None:
        """자동 읽기가 막힌 이유 한 줄(빈 글이면 숨김) — [안내 보기]는 동의가 답일 때만."""
        self.lb_gate.setText(text)
        self.lb_gate.setVisible(bool(text))
        self.btn_notice.setVisible(button)

    @staticmethod
    def _accent_for(sp_position: int) -> str:
        """GK 노랑 · 수비(1-8) 파랑 · 미드필더 그룹(9-19, 수미·미드·공미 전부) 초록
        · 최전방 공격(20-27) 빨강. 공미(17-19, RAM/CAM/LAM)도 미드필더로 친다 —
        스트라이커·윙어처럼 빨강으로 보이면 헷갈린다는 지적을 반영."""
        if sp_position == 0:
            return T.CHIP_GK
        if 1 <= sp_position <= 8:
            return T.CHIP_DF
        if 9 <= sp_position <= 19:
            return T.CHIP_MF
        return T.CHIP_FW


class ShotMapWidget(QWidget):
    """슛 좌표를 공격 진영 하프 피치 위에 점으로 찍는다(골문이 위).

    좌표는 stats.Shot 의 x·y(0~1). x=1.0 이 골문 쪽이라 위로, y 가 폭(왼→오).
    슛은 x>0.45 근처에만 오므로 하프 피치만 그린다. 색은 골=초록·유효=노랑·
    빗나감=회색, 골대 맞은 슛은 흰 테두리로 표시. 골을 마지막에 그려 위로 얹는다.
    """

    X_MIN = 0.45  # 이보다 뒤(자기 진영)의 슛은 거의 없다 — 아래 경계로 삼는다.

    def __init__(self, min_size: tuple[int, int] = (560, 460)):
        super().__init__()
        self.setMinimumSize(*min_size)   # 구단주 비교는 둘을 나란히 놓아 더 작게 준다(1280 창에서 가로 스크롤이 안 생기게)
        self._shots: list = []
        self._assists = False

    def set_shots(self, shots: list, assists: bool = False) -> None:
        """assists 면 골이면서 어시가 있는 슛에만 어시 위치 → 슛 위치 선(1.4.1 N6). 기본값은 예전 그림 그대로 —
        선수 카드 [내 기록]도 이 함수를 쓴다(test_shotmap_default_unchanged)."""
        self._shots = shots or []
        self._assists = assists
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
        line = QColor(T.PITCH_LINE)
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

        if self._assists:
            self._paint_assists(p, w, h, m)
        colors = {3: QColor(T.PITCH_GOAL), 1: QColor(T.WARN_FILL), 2: QColor(T.PITCH_MISS)}
        # 빗나감 → 유효 → 골 순으로 그려서 골이 맨 위에 오게 한다.
        order = sorted(self._shots, key=lambda s: {2: 0, 1: 1, 3: 2}.get(s.result, 0))
        for s in order:
            c = colors.get(s.result, QColor(T.TEXT_DIM))
            pt = self._pt(s.x, s.y, w, h, m)
            r = 6.0 if s.result == 3 else 4.5
            if s.hit_post:
                p.setPen(QPen(QColor(T.CHART_ON_MARK), 1.5))
            else:
                p.setPen(QPen(c.darker(160), 1))
            p.setBrush(c)
            p.drawEllipse(pt, r, r)

    def assist_lines(self) -> list:
        """그릴 어시스트 선의 슛 — 골이면서 어시 위치가 있는 것만(테스트가 그림 대신 이걸 본다)."""
        if not self._assists:
            return []
        return [s for s in self._shots if s.result == 3 and getattr(s, "assist_x", None) is not None]

    def _paint_assists(self, p: QPainter, w: int, h: int, m: int) -> None:
        """골 점 밑에 깔리게 슛 점보다 먼저. 하프 피치 밖(X_MIN 뒤 — 실측 2.3%)에서 온 어시는 아래 경계에 붙이고
        점 대신 삼각형으로 — 거기서 잘렸다는 뜻."""
        c = QColor(T.PITCH_ASSIST)
        for s in self.assist_lines():
            a = self._pt(s.assist_x, s.assist_y, w, h, m)
            p.setPen(QPen(c, 1.4))
            p.drawLine(a, self._pt(s.x, s.y, w, h, m))
            p.setBrush(c)
            if s.assist_x < self.X_MIN:
                p.drawPolygon(QPolygonF([QPointF(a.x() - 4, a.y()), QPointF(a.x() + 4, a.y()),
                                         QPointF(a.x(), a.y() - 6)]))
            else:
                p.drawEllipse(a, 2.5, 2.5)


def wdl_text(w: int, d: int, l: int) -> str:
    return f"{w}승 {d}무 {l}패"


def rate_of(w: int, d: int, l: int, mixed: bool) -> float:
    return core.win_pct(w, d, l, mixed)
