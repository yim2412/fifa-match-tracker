"""얼린 사본 — 8단계(1.3.1) 확장 직전의 charts.AreaTrendChart 그대로(2026-10-05, e0bd412).

test_area_chart_defaults_unchanged 가 새 AreaTrendChart 를 **기본값으로** 그린 그림과 이 사본의 그림을
픽셀 단위로 대조한다 — 대시보드 승률 흐름·선수 카드 전환율은 set_data(points) 만 부르므로 확장이 그 둘의
그림을 바꾸면 안 된다. 글꼴이 다른 CI 러너에서도 같은 프로세스 안의 비교라 좌표를 박아 두는 것보다 안전하다.
이 파일은 고치지 않는다(고치면 대조가 무의미해진다).
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen

import theme as T
from charts import AREA_ALPHA, DOT_R, LINE_W, RING_W, SMALL_PT, _Chart, _color, _small_font, _smooth_path


class LegacyAreaTrendChart(_Chart):
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
