"""얼린 사본 — 3.1.1(66단계 G0) 꼴로 다시 얼린 charts.AreaTrendChart 의 기본값 그림(2026-10-27).

test_area_chart_defaults_unchanged 가 새 AreaTrendChart 를 **기본값으로** 그린 그림과 이 사본의 그림을
픽셀 단위로 대조한다 — 대시보드 승률 흐름·선수 카드 전환율은 set_data(points) 만 부르므로 옵션을 늘려도 그 둘의
그림이 바뀌면 안 된다. 글꼴이 다른 CI 러너에서도 같은 프로세스 안의 비교라 좌표를 박아 두는 것보다 안전하다.

charts 에서 아무것도 import 하지 않는다 — 상수·도우미(_smooth_path 등)·색 값을 전부 복사해 뒀다(66 M5: 옛 사본은
charts 의 상수·도우미와 theme 색을 읽어서, 그쪽을 고치면 사본도 같이 바뀌어 골든이 아무것도 안 쟀다).
테마만 theme.MODE 로 고른다(색 값은 아래 표에 박아 둠).
이 파일은 고치지 않는다(고치면 대조가 무의미해진다). 기본값 그림을 일부러 바꿀 때만 통째로 다시 얼린다.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QToolTip, QWidget

import theme as T

LINE_W = 2.5
DOT_R = 4
RING_W = 2
SMALL_PT = 9
_COLORS = {   # 2026-10-27 의 theme 값 — PANEL · CHART_AXIS · CHART_GRID · TEXT · TEXT_DIM · 선(ACCENT_FILL)
    "dark": ("#18181b", "#3a414b", "#262b33", "#fafafa", "#a1a1aa", "#8689ea"),
    "light": ("#ffffff", "#d5dae3", "#eceff4", "#09090b", "#616875", "#373cbe"),
}


def _color(hex_: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(alpha)
    return c


def _small_font(base: QFont, pt: int = SMALL_PT, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPointSize(pt)
    f.setBold(bold)
    return f


def _smooth_path(pts: list[QPointF], top: float, bottom: float) -> QPainterPath:
    path = QPainterPath(pts[0])
    if len(pts) == 1:
        return path
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6, p1.y() + (p2.y() - p0.y()) / 6)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6, p2.y() - (p3.y() - p1.y()) / 6)
        for c in (c1, c2):
            c.setY(min(max(c.y(), top), bottom))
        path.cubicTo(c1, c2, p2)
    return path


class LegacyAreaTrendChart(QWidget):
    """승률 흐름 — 부드러운 곡선(채움 없음) · 점선 평균. 값은 0~100(%).

    points: (x 라벨, 값, 경기 수), 오래된 것부터. 경기가 없는 날은 넘기지 않는다.
    """

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(170)
        self._hits: list = []
        self._points: list[tuple[str, float, int]] = []
        self.marks: dict = {}

    def set_data(self, points: list[tuple[str, float, int]]) -> None:
        self._points = list(points)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        for area, text in self._hits:
            if area.contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()

    def paintEvent(self, event) -> None:
        panel, axis_c, grid_c, text_c, dim_c, line_c = _COLORS[T.MODE]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        self.marks = {}
        if not self._points:
            p.setPen(QColor(dim_c))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "경기 없음")
            return
        small = _small_font(self.font())
        fm = QFontMetrics(small)
        p.setFont(small)
        ml = fm.horizontalAdvance("100%") + 8
        mr = fm.horizontalAdvance("100.0%") // 2 + 10
        mt, mb = 18, fm.height() + 8
        w, h = self.width(), self.height()
        top, bottom = mt, h - mb
        plot_w = max(w - ml - mr, 1)

        for pct in (0, 50, 100):
            y = bottom - (bottom - top) * pct / 100
            p.setPen(QPen(QColor(axis_c if pct == 0 else grid_c), 1))
            p.drawLine(QPointF(ml, y), QPointF(w - mr, y))
            p.setPen(QColor(dim_c))
            p.drawText(QRectF(0, y - fm.height() / 2, ml - 6, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{pct}%")

        n = len(self._points)
        pts = [QPointF(ml + (plot_w * i / (n - 1) if n > 1 else plot_w / 2),
                       bottom - (bottom - top) * max(0.0, min(100.0, v)) / 100)
               for i, (_, v, _) in enumerate(self._points)]
        line = _smooth_path(pts, top, bottom)

        pen = QPen(QColor(line_c), LINE_W)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(line, pen)

        played = [(i, v, g) for i, (_, v, g) in enumerate(self._points) if g > 0]
        placed: list[QRectF] = []
        bold = _small_font(self.font(), SMALL_PT + 1, bold=True)
        bfm = QFontMetrics(bold)
        if played:
            avg = sum(v * g for _, v, g in played) / sum(g for _, _, g in played)
            ay = bottom - (bottom - top) * avg / 100
            pen = QPen(_color(dim_c, 0.7), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(ml, ay), QPointF(w - mr, ay))
            self.marks["avg"] = avg

            hi = max(played, key=lambda t: (t[1], t[0]))[0]
            lo = min(played, key=lambda t: (t[1], -t[0]))[0]
            p.setFont(bold)
            for kind, i in (("max", hi), ("min", lo)):
                if kind == "min" and i == hi:
                    continue
                pt = pts[i]
                p.setPen(QPen(QColor(panel), RING_W))
                p.setBrush(QColor(line_c))
                p.drawEllipse(pt, DOT_R + 1, DOT_R + 1)
                label = f"{self._points[i][1]:.1f}%"
                lw = bfm.horizontalAdvance(label)
                lx = min(max(pt.x() - lw / 2, ml), w - lw - 2)
                ly = pt.y() - bfm.height() - 6 if kind == "max" else pt.y() + 6
                ly = min(max(ly, 0), bottom - bfm.height())
                rect = QRectF(lx, ly, lw, bfm.height())
                p.setPen(QColor(text_c))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
                placed.append(rect)
                self.marks[kind] = (i, label)

            p.setFont(small)
            text = f"평균 {avg:.1f}%"
            tw = fm.horizontalAdvance(text)
            for ty in (ay - fm.height() - 2, ay + 2):
                rect = QRectF(w - mr - tw, ty, tw, fm.height())
                if not any(rect.intersects(r) for r in placed):
                    break
            p.fillRect(rect.adjusted(-3, 0, 2, 0), QColor(panel))
            p.setPen(QColor(dim_c))
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            self.marks["avg_rect"] = rect
            self.marks["label_rects"] = placed

        end = pts[-1]
        p.setPen(QPen(QColor(panel), RING_W))
        p.setBrush(QColor(line_c))
        p.drawEllipse(end, DOT_R, DOT_R)

        p.setFont(small)
        p.setPen(QColor(dim_c))
        lab_w = max(fm.horizontalAdvance(lb) for lb, _, _ in self._points) + 12
        step = max(1, math.ceil(n / max(1, plot_w // lab_w)))
        shown = list(range(0, n, step))
        if n - 1 not in shown:
            if shown and pts[n - 1].x() - pts[shown[-1]].x() < lab_w:
                shown.pop()
            shown.append(n - 1)
        for i in shown:
            p.drawText(QRectF(pts[i].x() - lab_w / 2, bottom + 4, lab_w, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, self._points[i][0])

        half = plot_w / (2 * (n - 1)) if n > 1 else plot_w / 2
        for (lb, v, games), pt in zip(self._points, pts):
            self._hits.append((QRectF(pt.x() - half, top, 2 * half, bottom - top),
                               f"{lb} · 승률 {v:.1f}% ({games}경기)"))
