"""대시보드 그래프 — QPainter 로 직접 그린다(QtCharts 없이, exe 용량·의존성 때문).

규칙(dataviz 지침): 선 2px · 영역은 계열색 ~10% 옅은 채움 · 점은 반지름 4 이상에
바탕색 2px 테두리 · 격자는 1px 실선 · 막대는 24px 이하, 끝만 4px 둥글게 · 맞닿는
채움 사이 2px 틈 · 글자는 계열색이 아니라 글자색(T.TEXT/T.TEXT_DIM). 값은 마우스를
올리면 툴팁으로도 보인다(툴팁만으로 읽히는 값은 두지 않는다 — 직접 표기 또는 상세
페이지가 같은 값을 보여준다).

모든 그래프는 데이터가 비면 그 자리에 "경기 없음" 을 그린다 — 빈 칸이 0 으로
보이면 "다 졌다"와 구분이 안 된다.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QToolTip, QWidget

import theme as T

EMPTY_TEXT = "경기 없음"
LINE_W = 2
DOT_R = 4
RING_W = 2          # 점·끝점 둘레 바탕색 테두리
GAP = 2             # 맞닿는 채움 사이 바탕색 틈
BAR_MAX = 24        # 막대 두께 상한
BAR_RADIUS = 4      # 막대 끝 둥글기
AREA_ALPHA = 0.16   # 영역 채움 위쪽 진하기(아래로 0 까지 옅어진다)
SMALL_PT = 9        # 축 글자


def _color(hex_: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(alpha)
    return c


def _small_font(base: QFont, pt: int = SMALL_PT, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPointSize(pt)
    f.setBold(bold)
    return f


class _Chart(QWidget):
    """공통 — 빈 데이터 표시와 마우스 올림 툴팁(_hits: (영역, 글) 목록)."""

    def __init__(self, min_h: int = 140):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(min_h)
        self._hits: list[tuple[QRectF | QPainterPath, str]] = []

    def _empty(self, p: QPainter, text: str = EMPTY_TEXT) -> None:
        p.setPen(QColor(T.TEXT_DIM))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

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


def _smooth_path(pts: list[QPointF], top: float, bottom: float) -> QPainterPath:
    """점을 지나는 부드러운 곡선(Catmull-Rom → 3차 베지어). 조절점을 위아래로
    가두어 곡선이 0%·100% 밖으로 튀지 않게 한다."""
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


class AreaTrendChart(_Chart):
    """승률 흐름 — 부드러운 곡선 + 아래 옅은 채움. 값은 0~100(%).

    points: (x 라벨, 값, 경기 수), 오래된 것부터. 경기가 없는 날은 넘기지 않는다.
    마지막 점에만 값을 직접 적는다(전부 적으면 읽히지 않는다).
    """

    def __init__(self):
        super().__init__(min_h=170)
        self._points: list[tuple[str, float, int]] = []

    def set_data(self, points: list[tuple[str, float, int]]) -> None:
        self._points = list(points)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        if not self._points:
            self._empty(p)
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
            p.setPen(QPen(QColor(T.CHART_AXIS if pct == 0 else T.CHART_GRID), 1))
            p.drawLine(QPointF(ml, y), QPointF(w - mr, y))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(0, y - fm.height() / 2, ml - 6, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{pct}%")

        n = len(self._points)
        pts = [QPointF(ml + (plot_w * i / (n - 1) if n > 1 else plot_w / 2),
                       bottom - (bottom - top) * max(0.0, min(100.0, v)) / 100)
               for i, (_, v, _) in enumerate(self._points)]
        line = _smooth_path(pts, top, bottom)

        area = QPainterPath(line)
        area.lineTo(pts[-1].x(), bottom)
        area.lineTo(pts[0].x(), bottom)
        area.closeSubpath()
        grad = QLinearGradient(0, top, 0, bottom)
        grad.setColorAt(0, _color(T.CHART_UP, AREA_ALPHA))
        grad.setColorAt(1, _color(T.CHART_UP, 0.0))
        p.fillPath(area, grad)

        pen = QPen(QColor(T.CHART_UP), LINE_W)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(line, pen)

        # 평균선 — 경기 수 가중(승 합 ÷ 경기 합). 승률 그래프 페이지 '평균 승률' 카드와
        # 같은 식이라 두 화면 숫자가 같다. 1px 실선, 바탕에 묻히지 않을 만큼만 진하게.
        played = [(i, v, g) for i, (_, v, g) in enumerate(self._points) if g > 0]
        self.marks: dict = {}
        placed: list[QRectF] = []
        bold = _small_font(self.font(), SMALL_PT + 1, bold=True)
        bfm = QFontMetrics(bold)
        if played:
            avg = sum(v * g for _, v, g in played) / sum(g for _, _, g in played)
            ay = bottom - (bottom - top) * avg / 100
            p.setPen(QPen(_color(T.TEXT_DIM, 0.7), 1))
            p.drawLine(QPointF(ml, ay), QPointF(w - mr, ay))
            self.marks["avg"] = avg

            # 최고·최저 — 같은 모양(점 + 굵은 값). 경기 없는 날(오류만)은 0% 가
            # 아니라 '모름'이라 후보에서 뺀다. 같은 값이면 최근 쪽을 표시한다.
            hi = max(played, key=lambda t: (t[1], t[0]))[0]
            lo = min(played, key=lambda t: (t[1], -t[0]))[0]
            p.setFont(bold)
            for kind, i in (("max", hi), ("min", lo)):
                if kind == "min" and i == hi:
                    continue
                pt = pts[i]
                p.setPen(QPen(QColor(T.PANEL), RING_W))
                p.setBrush(QColor(T.CHART_UP))
                p.drawEllipse(pt, DOT_R + 1, DOT_R + 1)
                label = f"{self._points[i][1]:.1f}%"
                lw = bfm.horizontalAdvance(label)
                lx = min(max(pt.x() - lw / 2, ml), w - lw - 2)
                # 최고는 점 위, 최저는 점 아래 — 곡선과 안 겹치게
                ly = pt.y() - bfm.height() - 6 if kind == "max" else pt.y() + 6
                ly = min(max(ly, 0), bottom - bfm.height())
                rect = QRectF(lx, ly, lw, bfm.height())
                p.setPen(QColor(T.TEXT))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
                placed.append(rect)
                self.marks[kind] = (i, label)

            # 평균 글자 — 오른쪽 끝 선 위, 최고·최저 글자와 겹치면 선 아래로
            p.setFont(small)
            text = f"평균 {avg:.1f}%"
            tw = fm.horizontalAdvance(text)
            for ty in (ay - fm.height() - 2, ay + 2):
                rect = QRectF(w - mr - tw, ty, tw, fm.height())
                if not any(rect.intersects(r) for r in placed):
                    break
            # 곡선이 글자 위를 지나가도 읽히게 바탕색을 깐다
            p.fillRect(rect.adjusted(-3, 0, 2, 0), QColor(T.PANEL))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            self.marks["avg_rect"] = rect
            self.marks["label_rects"] = placed

        # 마지막 날 — 점만(지금 위치). 값은 툴팁
        end = pts[-1]
        p.setPen(QPen(QColor(T.PANEL), RING_W))
        p.setBrush(QColor(T.CHART_UP))
        p.drawEllipse(end, DOT_R, DOT_R)

        # x 라벨 — 처음·끝 + 사이 몇 개(겹치지 않게)
        p.setFont(small)
        p.setPen(QColor(T.TEXT_DIM))
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

        # 마우스 올림 — 가장 가까운 점까지의 세로 띠
        half = plot_w / (2 * (n - 1)) if n > 1 else plot_w / 2
        for (lb, v, games), pt in zip(self._points, pts):
            self._hits.append((QRectF(pt.x() - half, top, 2 * half, bottom - top),
                               f"{lb} · 승률 {v:.1f}% ({games}경기)"))


class DonutChart(_Chart):
    """부분-전체(조각 6개 이하). segments: (이름, 값, 색). 가운데 큰 글자 + 작은 글자."""

    THICK = 16

    def __init__(self, size: int = 128):
        super().__init__(min_h=size)
        self.setMinimumWidth(size)
        self._segs: list[tuple[str, float, str]] = []
        self._center = ("", "")

    def set_data(self, segments: list[tuple[str, float, str]],
                 center: str = "", sub: str = "") -> None:
        self._segs = [s for s in segments if s[1] > 0]
        self._center = (center, sub)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.minimumWidth(), self.minimumHeight())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        total = sum(v for _, v, _ in self._segs)
        side = min(self.width(), self.height()) - 4
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        ring = rect.adjusted(self.THICK / 2, self.THICK / 2, -self.THICK / 2, -self.THICK / 2)
        if not total:
            p.setPen(QPen(QColor(T.CHART_GRID), self.THICK))
            p.drawEllipse(ring)
            self._empty(p)
            return
        r = ring.width() / 2
        gap_deg = math.degrees(GAP / r) if r else 0
        start = 90.0
        for name, v, col in self._segs:
            span = 360.0 * v / total
            draw = max(span - (gap_deg if len(self._segs) > 1 else 0), 0.5)
            pen = QPen(QColor(col), self.THICK)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawArc(ring, int((start - gap_deg / 2) * 16), int(-draw * 16))
            path = QPainterPath()
            path.arcMoveTo(rect, start)
            path.arcTo(rect, start, -span)
            inner = rect.adjusted(self.THICK, self.THICK, -self.THICK, -self.THICK)
            path.arcTo(inner, start - span, span)
            path.closeSubpath()
            self._hits.append((path, f"{name} {v:,.0f} ({v / total * 100:.1f}%)"))
            start -= span
        big, sub = self._center
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(13)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        fm = QFontMetrics(f)
        cy = rect.center().y()
        p.drawText(QRectF(rect.left(), cy - fm.height() + (0 if sub else fm.height() / 2),
                          rect.width(), fm.height()), Qt.AlignmentFlag.AlignCenter, big)
        if sub:
            p.setFont(_small_font(self.font()))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(rect.left(), cy + 1, rect.width(), fm.height()),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, sub)


class RingGauge(_Chart):
    """비율 하나(0~100). 채움은 계열색, 남은 길은 같은 색을 옅게.
    value 가 None 이면 표본 부족 — 링을 비우고 "—" 를 그린다."""

    THICK = 9

    def __init__(self, caption: str, color: str | None = None, size: int = 96):
        super().__init__(min_h=size)
        self.setMinimumWidth(size)
        self._caption = caption
        self._color = color or T.CHART_UP
        self._value: float | None = None
        self._note = ""

    def set_data(self, value: float | None, note: str = "", tip: str = "") -> None:
        self._value = value
        self._note = note
        self.setToolTip(tip)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.minimumWidth(), self.minimumHeight())

    def mouseMoveEvent(self, event) -> None:  # 툴팁은 setToolTip 하나로 충분하다
        QWidget.mouseMoveEvent(self, event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 2
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        ring = rect.adjusted(self.THICK / 2, self.THICK / 2, -self.THICK / 2, -self.THICK / 2)
        track = QPen(_color(self._color, 0.18), self.THICK)
        p.setPen(track)
        p.drawEllipse(ring)
        if self._value is not None:
            pen = QPen(QColor(self._color), self.THICK)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            span = 360 * max(0.0, min(100.0, self._value)) / 100
            if span > 0:
                p.drawArc(ring, 90 * 16, int(-span * 16))
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(13)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        fm = QFontMetrics(f)
        text = "—" if self._value is None else f"{self._value:.0f}%"
        cy = rect.center().y()
        p.drawText(QRectF(rect.left(), cy - fm.height() + 4, rect.width(), fm.height()),
                   Qt.AlignmentFlag.AlignCenter, text)
        p.setFont(_small_font(self.font()))
        p.setPen(QColor(T.TEXT_DIM))
        p.drawText(QRectF(rect.left(), cy + 4, rect.width(), fm.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._note)

    @property
    def caption(self) -> str:
        return self._caption


class GroupedBarChart(_Chart):
    """구간별 두 계열 세로 막대(예: 15분 구간 득·실). 계열마다 가장 큰 값에만
    숫자를 적고, 나머지는 툴팁. 범례는 카드 쪽에서 붙인다(계열 2개)."""

    def __init__(self):
        super().__init__(min_h=170)
        self._labels: list[str] = []
        self._series: list[tuple[str, list[float], str]] = []

    def set_data(self, labels: list[str],
                 series: list[tuple[str, list[float], str]]) -> None:
        self._labels = list(labels)
        self._series = list(series)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        vmax = max((v for _, vals, _ in self._series for v in vals), default=0)
        if not self._labels or vmax <= 0:
            self._empty(p)
            return
        small = _small_font(self.font())
        fm = QFontMetrics(small)
        p.setFont(small)
        w, h = self.width(), self.height()
        mt, mb, ml, mr = fm.height() + 6, fm.height() + 8, 4, 4
        bottom = h - mb
        plot_h = max(bottom - mt, 1)
        n = len(self._labels)
        slot = (w - ml - mr) / n
        k = len(self._series)
        bar = max(3.0, min(BAR_MAX, (slot * 0.7 - GAP * (k - 1)) / k))
        group_w = bar * k + GAP * (k - 1)

        p.setPen(QPen(QColor(T.CHART_AXIS), 1))
        p.drawLine(QPointF(ml, bottom), QPointF(w - mr, bottom))

        peaks = {si: max(range(n), key=lambda i, vals=vals: vals[i])
                 for si, (_, vals, _) in enumerate(self._series)}
        # 계열마다 최댓값에만 숫자 — 두 최댓값이 같은 구간이면 글자가 겹친다
        # (실데이터 30~45분 772·765 가 "77365" 로 붙었다). 큰 쪽부터 놓고 겹치면 뺀다.
        label_at: dict[tuple[int, int], QRectF] = {}
        placed: list[QRectF] = []
        order = sorted(peaks.items(), key=lambda kv: -self._series[kv[0]][1][kv[1]])
        for si, i in order:
            v = self._series[si][1][i]
            if v <= 0:
                continue
            x = ml + slot * i + (slot - group_w) / 2 + si * (bar + GAP)
            text_w = fm.horizontalAdvance(f"{v:,.0f}") + 4
            bh = plot_h * v / vmax
            rect = QRectF(x + bar / 2 - text_w / 2, bottom - bh - fm.height() - 2,
                          text_w, fm.height())
            if any(rect.intersects(r) for r in placed):
                continue
            placed.append(rect)
            label_at[(si, i)] = rect
        self.label_rects = placed  # 테스트가 겹침을 잰다
        for i, lb in enumerate(self._labels):
            x0 = ml + slot * i + (slot - group_w) / 2
            for si, (name, vals, col) in enumerate(self._series):
                v = vals[i]
                bh = plot_h * v / vmax
                x = x0 + si * (bar + GAP)
                if bh > 0:
                    r = min(BAR_RADIUS, bar / 2, bh)
                    path = QPainterPath()
                    path.moveTo(x, bottom)
                    path.lineTo(x, bottom - bh + r)
                    path.quadTo(x, bottom - bh, x + r, bottom - bh)
                    path.lineTo(x + bar - r, bottom - bh)
                    path.quadTo(x + bar, bottom - bh, x + bar, bottom - bh + r)
                    path.lineTo(x + bar, bottom)
                    path.closeSubpath()
                    p.fillPath(path, QColor(col))
                if (si, i) in label_at:
                    p.setPen(QColor(T.TEXT))
                    p.drawText(label_at[(si, i)], Qt.AlignmentFlag.AlignCenter, f"{v:,.0f}")
                self._hits.append((QRectF(x - GAP, mt, bar + 2 * GAP, plot_h),
                                   f"{lb} · {name} {v:,.0f}"))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(ml + slot * i, bottom + 4, slot, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, lb)


class RadarChart(_Chart):
    """나 vs 상대 평균. axes: (축 이름, 내 몫 0~1, 툴팁 글). 0.5 = 동률.

    눈금은 몫 0.25~0.75 를 반지름 0~1 로 편다 — 대부분의 축이 0.45~0.55 에
    모여 0~1 그대로면 두 도형이 겹쳐 차이가 안 보인다. 범위 밖은 끝에 붙인다.
    """

    LO, HI = 0.25, 0.75

    def __init__(self):
        super().__init__(min_h=220)
        self._axes: list[tuple[str, float, str]] = []

    def set_data(self, axes: list[tuple[str, float, str]]) -> None:
        self._axes = list(axes)
        self.update()

    def _r(self, share: float) -> float:
        return max(0.0, min(1.0, (share - self.LO) / (self.HI - self.LO)))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        if len(self._axes) < 3:
            self._empty(p)
            return
        small = _small_font(self.font())
        fm = QFontMetrics(small)
        p.setFont(small)
        lab_w = max(fm.horizontalAdvance(a[0]) for a in self._axes)
        cx, cy = self.width() / 2, self.height() / 2
        R = max(min(self.width() / 2 - lab_w - 10, self.height() / 2 - fm.height() - 8), 20)
        n = len(self._axes)

        def at(i: int, rr: float) -> QPointF:
            ang = -math.pi / 2 + 2 * math.pi * i / n
            return QPointF(cx + R * rr * math.cos(ang), cy + R * rr * math.sin(ang))

        # 고리 — 0.5(동률) 고리만 기준선 색, 나머지는 격자색
        for share in (0.25, 0.5, 0.75):
            ring = QPainterPath(at(0, self._r(share)))
            for i in range(1, n + 1):
                ring.lineTo(at(i % n, self._r(share)))
            p.setPen(QPen(QColor(T.CHART_AXIS if share == 0.5 else T.CHART_GRID), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(ring)
        for i in range(n):
            p.setPen(QPen(QColor(T.CHART_GRID), 1))
            p.drawLine(QPointF(cx, cy), at(i, 1.0))

        def poly(shares: list[float]) -> QPainterPath:
            path = QPainterPath(at(0, self._r(shares[0])))
            for i in range(1, n):
                path.lineTo(at(i, self._r(shares[i])))
            path.closeSubpath()
            return path

        mine = [a[1] for a in self._axes]
        opp = poly([1 - s for s in mine])
        pen = QPen(QColor(T.CHART_NEUTRAL), LINE_W)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(opp)
        me = poly(mine)
        p.fillPath(me, _color(T.CHART_UP, AREA_ALPHA + 0.06))
        pen = QPen(QColor(T.CHART_UP), LINE_W)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(me, pen)
        p.setPen(QPen(QColor(T.PANEL), RING_W))
        p.setBrush(QColor(T.CHART_UP))
        for i, s in enumerate(mine):
            p.drawEllipse(at(i, self._r(s)), DOT_R, DOT_R)

        p.setPen(QColor(T.TEXT_DIM))
        for i, (name, _, tip) in enumerate(self._axes):
            pt = at(i, 1.0)
            dx = pt.x() - cx
            align = (Qt.AlignmentFlag.AlignLeft if dx > 1 else
                     Qt.AlignmentFlag.AlignRight if dx < -1 else Qt.AlignmentFlag.AlignHCenter)
            box_w = lab_w + 4
            x = pt.x() + 6 if dx > 1 else pt.x() - 6 - box_w if dx < -1 else pt.x() - box_w / 2
            dy = pt.y() - cy
            y = (pt.y() - fm.height() - 2 if dy < -1 else pt.y() + 2 if dy > 1
                 else pt.y() - fm.height() / 2)
            rect = QRectF(x, y, box_w, fm.height())
            p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, name)
            self._hits.append((rect.united(QRectF(pt.x() - 12, pt.y() - 12, 24, 24)), tip))
            mid = at(i, 0.5)
            self._hits.append((QRectF(mid.x() - R / 3, mid.y() - R / 3, R / 1.5, R / 1.5), tip))


class ResultDots(_Chart):
    """최근 경기 결과 점 — 오래된 것 → 최신. 색만으로 구분하지 않게 점 안에 승/무/패 글자.

    점 지름은 폭에 맞춰 D(24)~MIN_D(18) 사이에서 줄어든다 — 20경기를 24px 로
    놓으면 560px 가 필요한데 1280 폭 승률 흐름 카드 안쪽은 약 440px 다. 최소 폭은
    MIN_D 기준으로 알려서 그보다 좁게는 눌리지 않는다(글자가 점 밖으로 안 넘친다).
    """

    D = 24
    MIN_D = 18

    def __init__(self):
        super().__init__(min_h=self.D + 4)
        self._items: list[tuple[str, str]] = []

    def set_data(self, items: list[tuple[str, str]]) -> None:
        """items: (결과 '승'/'무'/'패', 툴팁 글)."""
        self._items = list(items)
        self.setMinimumWidth(max(len(self._items), 1) * (self.MIN_D + GAP + 2))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(max(len(self._items), 1) * (self.D + GAP + 2), self.D + 4)

    def dot_size(self) -> float:
        n = max(len(self._items), 1)
        return max(self.MIN_D, min(self.D, self.width() / n - GAP - 2))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        if not self._items:
            self._empty(p)
            return
        d = self.dot_size()
        f = _small_font(self.font(), SMALL_PT if d >= 22 else SMALL_PT - 1, bold=True)
        p.setFont(f)
        step = d + GAP + 2
        y = (self.height() - d) / 2
        for i, (res, tip) in enumerate(self._items):
            col = (T.CHART_UP if "승" in res else T.CHART_DOWN if "패" in res
                   else T.CHART_NEUTRAL)
            rect = QRectF(i * step + 1, y, d, d)
            p.setPen(QPen(QColor(T.PANEL), RING_W))
            p.setBrush(QColor(col))
            p.drawEllipse(rect)
            p.setPen(QColor("#ffffff"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, res[:1] if res[:1] in "승무패" else "?")
            self._hits.append((rect.adjusted(-GAP, -4, GAP, 4), tip))


class HBarList(_Chart):
    """가로 막대 몇 줄 — (이름, 값 0~100, 오른쪽 글, 툴팁). 한 계열이라 범례 없음."""

    ROW = 26
    THICK = 8

    def __init__(self):
        super().__init__(min_h=self.ROW)
        self._rows: list[tuple[str, float | None, str, str]] = []

    def set_data(self, rows: list[tuple[str, float | None, str, str]]) -> None:
        self._rows = list(rows)
        self.setMinimumHeight(max(len(self._rows), 1) * self.ROW)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(200, max(len(self._rows), 1) * self.ROW)

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetrics(_small_font(self.font(), SMALL_PT + 1))
        name_w = max((fm.horizontalAdvance(r[0]) for r in self._rows), default=0)
        val_w = max((fm.horizontalAdvance(r[2]) for r in self._rows), default=0)
        return QSize(name_w + val_w + 60, max(len(self._rows), 1) * self.ROW)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        if not self._rows:
            self._empty(p)
            return
        f = _small_font(self.font(), SMALL_PT + 1)
        fm = QFontMetrics(f)
        p.setFont(f)
        name_w = max(fm.horizontalAdvance(r[0]) for r in self._rows) + 10
        val_w = max(fm.horizontalAdvance(r[2]) for r in self._rows) + 8
        bar_x = name_w
        bar_w = max(self.width() - name_w - val_w, 10)
        for i, (name, v, right, tip) in enumerate(self._rows):
            y = i * self.ROW
            mid = y + self.ROW / 2
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(0, y, name_w - 6, self.ROW),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            track = QRectF(bar_x, mid - self.THICK / 2, bar_w, self.THICK)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.CHART_GRID))
            p.drawRoundedRect(track, self.THICK / 2, self.THICK / 2)
            if v is not None and v > 0:
                fill = QRectF(track.left(), track.top(),
                              max(track.width() * min(v, 100) / 100, self.THICK), self.THICK)
                p.setBrush(QColor(T.CHART_UP))
                p.drawRoundedRect(fill, self.THICK / 2, self.THICK / 2)
            p.setPen(QColor(T.TEXT))
            p.drawText(QRectF(self.width() - val_w + 6, y, val_w - 6, self.ROW),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, right)
            self._hits.append((QRectF(0, y, self.width(), self.ROW), tip))
