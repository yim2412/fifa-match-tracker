"""화면 부품 — 랭커 카드 · 요약 카드 · 막대 그래프 행.

app_main 이 UI 흐름에 집중하도록 그리기 부품은 여기로 뺐다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QSizePolicy, QTableWidgetItem, QVBoxLayout, QWidget,
)  # QGridLayout: 랭커 카드 표, QSizePolicy: 값 칸 가로 확장

import theme as T

NA = "—"  # API 가 안 주는 값. 그럴싸한 숫자를 지어 넣지 않는다.


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

        cap = QLabel(title)
        cap.setStyleSheet(f"color: {T.TEXT_DIM}; border: none;")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.value = QLabel("-")
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.value.setFont(f)
        self.value.setStyleSheet(f"color: {color}; border: none;")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        v.addWidget(cap)
        v.addWidget(self.value)

    def set(self, text: str) -> None:
        self.value.setText(text)


class RankerCard(QFrame):
    """감독모드 랭커 카드.

    순위·구단가치·점수는 넥슨 API 가 주지 않는다(엔드포인트 자체가 없다.
    존재하지 않는 경로도 같은 400 을 뱉는 것으로 확인). 칸은 두되 값은
    NA 로 남기고 각주로 이유를 밝힌다 — 지어낸 숫자를 넣지 않는다.
    """

    ROWS = ["순위", "전적", "구단가치", "점수"]

    def __init__(self):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; }}")
        self.setMaximumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QLabel("⚽  감독모드 랭커  ⚽")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont()
        f.setBold(True)
        head.setFont(f)
        head.setStyleSheet(
            f"background: {T.GREEN}; color: #06240d; border: none;"
            f" border-top-left-radius: 8px; border-top-right-radius: 8px;"
            f" padding: 7px;")
        v.addWidget(head)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        self._vals: dict[str, QLabel] = {}
        for r, name in enumerate(self.ROWS):
            cap = QLabel(name)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setStyleSheet(
                f"background: {T.PANEL_2}; color: {T.TEXT}; border: none;"
                f" padding: 9px; font-weight: bold;")
            cap.setFixedWidth(110)
            val = QLabel(NA)
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet(
                f"background: #0d1117; color: {T.TEXT}; border: none;"
                f" padding: 9px;")
            val.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Preferred)
            grid.addWidget(cap, r, 0)
            grid.addWidget(val, r, 1)
            self._vals[name] = val
        v.addLayout(grid)

        self.note = QLabel("")
        self.note.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.note.setStyleSheet(
            f"color: {T.TEXT_DIM}; border: none; padding: 5px 8px;"
            f" font-size: 11px;")
        self.note.setWordWrap(True)
        v.addWidget(self.note)

    def set(self, name: str, text: str, color: str = T.TEXT) -> None:
        lb = self._vals[name]
        lb.setText(text)
        lb.setStyleSheet(
            f"background: #0d1117; color: {color}; border: none; padding: 9px;")


class BarRow(QWidget):
    """유형별 골 한 줄 — 이름 · 골수 · 비율 · 막대."""

    def __init__(self, name: str, count: int, total: int, color: str):
        super().__init__()
        pct = (count / total * 100) if total else 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 3, 0, 3)
        v.setSpacing(3)

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
        bar.setFixedHeight(5)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {T.PANEL_2}; border: none;"
            f" border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}")
        v.addWidget(bar)


def wdl_text(w: int, d: int, l: int) -> str:
    return f"{w}승 {d}무 {l}패"


def rate_of(w: int, d: int, l: int) -> float:
    t = w + d + l
    return (w / t * 100) if t else 0.0
