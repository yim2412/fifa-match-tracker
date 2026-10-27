"""대시보드 그래프 — QPainter 로 직접 그린다(QtCharts 없이, exe 용량·의존성 때문).

규칙(dataviz 지침): 선 2px · 영역은 계열색 ~10% 옅은 채움 · 점은 반지름 4 이상에
바탕색 2px 테두리 · 격자는 1px 실선 · 막대는 24px 이하, 끝만 4px 둥글게 · 맞닿는
채움 사이 2px 틈 · 글자는 계열색이 아니라 글자색(T.TEXT/T.TEXT_DIM). 값은 마우스를
올리면 툴팁으로도 보인다(툴팁만으로 읽히는 값은 두지 않는다 — 직접 표기 또는 상세
페이지가 같은 값을 보여준다).

모든 그래프는 데이터가 비면 그 자리에 "경기 없음" 을 그린다 — 빈 칸이 0 으로
보이면 "다 졌다"와 구분이 안 된다.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
그래프(QPainter) — 대시보드·승률 그래프·기간별 추이·시즌별 막대 · 점수 분포(`HistogramChart` — 2.3.1, 맨 왼쪽 칸 흐림 · 비교 선 · 세로선).
색은 `theme.CHART_*` — 앱의 좋음/나쁨(GOOD/BAD) 짝은 적록 색약에서 구분이 안 돼 그래프엔 안 쓴다. 히트맵(`HeatmapChart` — 칸 안 글자 · 흐린 칸은 색 없음 ·
색 상한 `HEAT_MAX_MIX` 는 글자 대비 4.5 로 잼)·도넛 조각(`donut_segments` — 6개 넘으면 "기타").
추이는 **`AreaTrendChart` 하나**(1.3.1) — 축(`Axis` · `PCT_AXIS`/`Axis.fit`)·기준선·이동평균·경기 수 막대·날짜 x·기준 계단선·띠·범례·큰 값은 전부 `set_data` 인자.
**기본값 그림은 얼린 사본과 픽셀까지 같아야** 한다(대시보드·선수 카드가 기본값만 쓴다) — `tests/legacy_area_chart.py`(3.1.1 66 G0 꼴로
다시 얼림 · 상수·도우미·색 값까지 복사해 둬서 여기를 고치면 같이 안 바뀐다 · 고치지 않는다)와 `test_area_chart_defaults_unchanged` 가 대조
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QPainter,
                         QPainterPath, QPen, QPolygonF)
from PyQt6.QtWidgets import QToolTip, QWidget

import theme as T

EMPTY_TEXT = "경기 없음"
LINE_W = 2
DOT_R = 4
RING_W = 2          # 점·끝점 둘레 바탕색 테두리
GAP = 2             # 맞닿는 채움 사이 바탕색 틈
BAR_MAX = 24        # 막대 두께 상한
BAR_RADIUS = 4      # 막대 끝 둥글기
SMALL_PT = 9        # 축 글자
TREND_LINE_W = 2.5  # 추이 그래프(AreaTrendChart) 선만 — 다른 그래프는 LINE_W
BAND_ALPHA = 0.2    # 추이 띠 진하기
BAND_BAR_MAX = 6    # 혼자 있는 띠 날의 세로 막대 폭 상한(날 간격의 1/3 과 작은 쪽)


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


@dataclass(frozen=True)
class Axis:
    """값 축 하나 — 범위·눈금·글자 모양·툴팁 이름. 승률(%)이 박혀 있던 자리를 여기 모은다
    (격자 글자 · 왼쪽 여백 폭 · 최고/최저 · 평균 · 툴팁) — ELO 축에 % 가 새지 않게."""
    lo: float
    hi: float
    ticks: tuple
    fmt: str            # 값 글자(최고·최저·평균·툴팁)
    name: str           # 툴팁 이름("승률" · "ELO")
    tick_fmt: str = ""  # 눈금 글자 — 비면 fmt

    def text(self, v: float) -> str:
        return self.fmt.format(v)

    def tick_text(self, v: float) -> str:
        return (self.tick_fmt or self.fmt).format(v)

    @staticmethod
    def fit(values, fmt: str = "{:,.0f}", name: str = "ELO") -> "Axis":
        """데이터 범위 + 위아래 10% 여백을 덮는 눈금 셋(같은 간격 · 1·2·2.5·5 의 10배수)."""
        vals = [v for v in values if v is not None]
        if not vals:
            return Axis(0, 2, (0, 1, 2), fmt, name)
        vmin, vmax = min(vals), max(vals)
        span = (vmax - vmin) or max(abs(vmax) * 0.02, 1.0)
        lo_raw, hi_raw = vmin - span * 0.1, vmax + span * 0.1
        mag = 10 ** math.floor(math.log10((hi_raw - lo_raw) / 2))
        for m in (1, 2, 2.5, 5, 10, 20):  # 20 이면 반드시 덮는다(간격 ≥ 범위)
            step = m * mag
            lo = math.floor(lo_raw / step) * step
            if lo + 2 * step >= hi_raw:
                break
        return Axis(lo, lo + 2 * step, (lo, lo + step, lo + 2 * step), fmt, name)


    @staticmethod
    def share(values, name: str = "비율") -> "Axis":
        """비율(0~100%) 축 — 0 에서 시작하고 가장 큰 값 + 10% 를 덮는 눈금 셋. fit 은 위아래로 여백을 줘서
        0% 근처 값이면 눈금이 음수까지 내려갔다(17단계 실화면: 포메이션 비율 -100%~100%)."""
        top = max([v for v in values if v is not None] or [0.0]) * 1.1
        step = next((s for s in (5, 10, 20, 25, 50) if 2 * s >= top), 50)
        return Axis(0, 2 * step, (0, step, 2 * step), "{:.1f}%", name, "{:.0f}%")


PCT_AXIS = Axis(0, 100, (0, 50, 100), "{:.1f}%", "승률", "{:.0f}%")


class AreaTrendChart(_Chart):
    """추이 — 부드러운 곡선(채움 없음 · 3.1.1 66 G0). 기본은 승률(0~100%).

    points: (x 라벨, 값, 경기 수 | None), 오래된 것부터. 경기가 없는 날은 넘기지 않는다.
    마지막 점에만 값을 직접 적는다(전부 적으면 읽히지 않는다).

    옵션은 전부 set_data 인자(1.3.1) — 기본값이면 그림이 얼린 사본과 픽셀까지 같다
    (tests/legacy_area_chart.py 와 대조). 대시보드·선수 카드는 set_data(points) 만 부른다.
      axis       값 축(PCT_AXIS · Axis.fit(…))
      avg        경기 수 가중 평균선(점선) — 경기 수가 None 인 점(ELO)은 평균에 안 들어간다
      baseline   기준값 — 눈금이면 그 격자선을 진하게, 아니면 점선 + 글자
      ma         두 번째 선(이동평균) — 점마다 값, None 이면 끊긴다
      counts     선 뒤 회색 막대로 점마다 경기 수 + 가장 큰 값만 숫자
      ref_series [(이름, [(날짜, 값)], 색)] — 날짜에 따라 움직이는 계단선(x_dates 필요)
      x_dates    점마다 날짜 — 주면 x 를 날짜 간격으로(빈 날이 순번 간격으로 찌그러지지 않게)
      weak       점마다 표본 부족 여부 — 참인 점은 빈 고리로 흐리게, 최고·최저 표시에서 뺀다(1.4.1 평점 추이)
      avg_weights 점마다 평균선의 무게 — 승률이면 승률 분모(rated · 2.6.1 A6). 없으면 경기 수.
                 섞인 묶음은 무승부가 분모 밖이라, 경기 수로 가중하면 평균선이 표의 승률과 어긋난다
      ranks      점마다 순위(57 K5) — 점선 + 오른쪽 축(로그 · 1위가 위). None 인 점에서 끊긴다
      band       점마다 (최저, 최고) | None — 선 색 옅은 띠(66 U3). 이웃 없이 혼자인 날은 세로 막대(M10).
                 축은 부르는 쪽이 띠까지 덮게 맞춘다(M6)
      color      선·점·띠 색(기본 T.CHART_LINE)
      legend     [(이름, 색, 점선?)] — 왼쪽 위 범례
      headline   오른쪽 위 큰 글자(지금 값)
    """

    BASE_MIN_H = 170
    COUNT_FRAC = 0.3    # 경기 수 막대 — 가장 큰 값이 그래프 높이의 이 비율(선 뒤라 선을 가리지 않을 만큼)
    COUNT_ALPHA = 0.35  # 경기 수 막대 진하기(CHART_NEUTRAL)
    MA_W = 1.5
    HEAD_PT = SMALL_PT + 7   # headline 글자

    def __init__(self):
        super().__init__(min_h=self.BASE_MIN_H)
        self._points: list[tuple[str, float, int | None]] = []
        self._axis = PCT_AXIS
        self._avg = True
        self._baseline: float | None = None
        self._ma: list[float | None] | None = None
        self._ma_name = "7일 평균"
        self._counts = False
        self._refs: tuple = ()
        self._x_dates: list | None = None
        self._weak: list[bool] | None = None
        self._avg_weights: list[int] | None = None
        self._ranks: list[int | None] | None = None
        self._band: list[tuple[float, float] | None] | None = None
        self._color: str | None = None
        self._legend: tuple = ()
        self._headline: str | None = None
        self.marks: dict = {}

    def set_data(self, points: list[tuple[str, float, int | None]], *, axis: Axis = PCT_AXIS,
                 avg: bool = True, baseline: float | None = None, ma: list[float | None] | None = None,
                 ma_name: str = "7일 평균", counts: bool = False, ref_series=(),
                 x_dates: list | None = None, weak: list[bool] | None = None,
                 avg_weights: list[int] | None = None, ranks: list[int | None] | None = None,
                 band: list[tuple[float, float] | None] | None = None, color: str | None = None,
                 legend=(), headline: str | None = None) -> None:
        self._points = list(points)
        if band is not None and len(band) != len(self._points):
            raise ValueError(f"band 길이 {len(band)} != 점 {len(self._points)}")
        self._band = list(band) if band is not None and any(band) else None
        self._color, self._legend, self._headline = color, tuple(legend), headline
        if ranks is not None and len(ranks) != len(self._points):
            raise ValueError(f"ranks 길이 {len(ranks)} != 점 {len(self._points)}")
        self._ranks = list(ranks) if ranks is not None and any(ranks) else None
        if avg_weights is not None and len(avg_weights) != len(self._points):
            raise ValueError(f"avg_weights 길이 {len(avg_weights)} != 점 {len(self._points)}")
        self._avg_weights = list(avg_weights) if avg_weights is not None else None
        if weak is not None and len(weak) != len(self._points):
            raise ValueError(f"weak 길이 {len(weak)} != 점 {len(self._points)}")
        self._weak = list(weak) if weak is not None else None
        if ma is not None and len(ma) != len(self._points):
            raise ValueError(f"ma 길이 {len(ma)} != 점 {len(self._points)}")
        if x_dates is not None and len(x_dates) != len(self._points):
            raise ValueError(f"x_dates 길이 {len(x_dates)} != 점 {len(self._points)}")
        self._axis, self._avg, self._baseline = axis, avg, baseline
        self._ma, self._ma_name, self._counts = (list(ma) if ma is not None else None), ma_name, counts
        self._refs = tuple(ref_series)
        self._x_dates = list(x_dates) if x_dates is not None else None
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        self.marks = {}
        if not self._points:
            self._empty(p)
            return
        ax = self._axis
        col = self._color or T.CHART_LINE
        small = _small_font(self.font())
        fm = QFontMetrics(small)
        p.setFont(small)
        ml = max(fm.horizontalAdvance(ax.tick_text(t)) for t in ax.ticks) + 8
        mr = fm.horizontalAdvance(ax.text(ax.hi)) // 2 + 10
        if self._ranks is not None:     # 오른쪽 순위 축 글자 자리
            known = [r for r in self._ranks if r]
            mr = max(mr, max(fm.horizontalAdvance(f"{r:,}위") for r in (min(known), max(known))) + 12)
        mt, mb = 18, fm.height() + 8
        w, h = self.width(), self.height()
        if self._legend or self._headline:
            mt += self._draw_header(p, fm, w) + 4
            p.setFont(small)
        top = mt
        foot = h - mb                       # x 라벨 칸 위 — 마우스 띠가 여기까지
        bottom = foot
        plot_w = max(w - ml - mr, 1)

        def y_of(v: float) -> float:
            return bottom - (bottom - top) * (min(max(v, ax.lo), ax.hi) - ax.lo) / (ax.hi - ax.lo)

        base_on_grid = self._baseline is not None and self._baseline in ax.ticks
        for t in ax.ticks:
            y = bottom - (bottom - top) * (t - ax.lo) / (ax.hi - ax.lo)
            strong = base_on_grid and t == self._baseline
            p.setPen(QPen(QColor(T.CHART_AXIS if t == ax.ticks[0] or strong else T.CHART_GRID),
                          2 if strong else 1))
            p.drawLine(QPointF(ml, y), QPointF(w - mr, y))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(0, y - fm.height() / 2, ml - 6, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       ax.tick_text(t))
            if strong:
                self.marks["baseline"] = ("grid", y)
        if self._baseline is not None and not base_on_grid:
            by = y_of(self._baseline)
            pen = QPen(QColor(T.CHART_AXIS), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(ml, by), QPointF(w - mr, by))
            self.marks["baseline"] = ("dashed", by)

        n = len(self._points)
        if self._x_dates is not None:
            d0 = self._x_dates[0]
            span = (self._x_dates[-1] - d0).days
            xs = [ml + (plot_w * (d - d0).days / span if span else plot_w / 2) for d in self._x_dates]
        else:
            xs = [ml + (plot_w * i / (n - 1) if n > 1 else plot_w / 2) for i in range(n)]
        pts = [QPointF(x, y_of(v)) for x, (_, v, _) in zip(xs, self._points)]
        line = _smooth_path(pts, top, bottom)

        # 뒤에서 앞으로: 경기 수 막대 → 띠 → 기준 계단선 → 이동평균 → 순위 점선 → 본 선
        if self._counts:
            self._draw_counts(p, xs, top, bottom)
        if self._band is not None:
            self._draw_band(p, xs, y_of, col)
        ref_ends = self._draw_ref_lines(p, y_of, ml, plot_w) if self._refs else []
        if self._ma is not None:
            self._draw_ma(p, xs, y_of)
        if self._ranks is not None:
            self._draw_ranks(p, fm, xs, top, bottom, w - mr)

        pen = QPen(QColor(col), TREND_LINE_W)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(line, pen)

        # 평균선 — 경기 수 가중(승 합 ÷ 경기 합). 승률 그래프 페이지 '평균 승률' 카드와
        # 같은 식이라 두 화면 숫자가 같다. 1px 점선(66 P6), 바탕에 묻히지 않을 만큼만 진하게.
        # 경기 수가 None 인 점(ELO)은 '모름'이 아니라 '세지 않는 값'이라 최고·최저 후보엔 든다.
        played = [(i, v, g) for i, (_, v, g) in enumerate(self._points) if g is None or g > 0]
        weighted = [(i, v, g if self._avg_weights is None else self._avg_weights[i])
                    for i, v, g in played if g is not None]
        if self._weak:  # 가중 평균엔 두고(경기 수만큼만 무게) 최고·최저 표시에서만 뺀다
            played = [t for t in played if not self._weak[t[0]]]
        placed: list[QRectF] = []
        bold = _small_font(self.font(), SMALL_PT + 1, bold=True)
        bfm = QFontMetrics(bold)
        show_avg = self._avg and sum(g for _, _, g in weighted) > 0
        if show_avg:
            avg = sum(v * g for _, v, g in weighted) / sum(g for _, _, g in weighted)
            ay = y_of(avg)
            pen = QPen(_color(T.TEXT_DIM, 0.7), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(ml, ay), QPointF(w - mr, ay))
            self.marks["avg"] = avg

        if played:
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
                p.setBrush(QColor(col))
                p.drawEllipse(pt, DOT_R + 1, DOT_R + 1)
                label = ax.text(self._points[i][1])
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

        p.setFont(small)
        if self._baseline is not None and not base_on_grid:
            text = ax.tick_text(self._baseline)
            tw = fm.horizontalAdvance(text)
            by = self.marks["baseline"][1]
            rect = self._free_rect([QRectF(ml + 4, by - fm.height() - 2, tw, fm.height()),
                                    QRectF(ml + 4, by + 2, tw, fm.height())], placed)
            p.fillRect(rect.adjusted(-3, 0, 2, 0), QColor(T.PANEL))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            placed.append(rect)
            self.marks["baseline_rect"] = rect
        if ref_ends:
            self._draw_ref_labels(p, fm, ref_ends, w - mr, top, bottom, placed)

        if show_avg:
            # 평균 글자 — 오른쪽 끝 선 위, 최고·최저 글자와 겹치면 선 아래로
            text = f"평균 {ax.text(avg)}"
            tw = fm.horizontalAdvance(text)
            rect = self._free_rect([QRectF(w - mr - tw, ty, tw, fm.height())
                                    for ty in (ay - fm.height() - 2, ay + 2)], placed)
            # 곡선이 글자 위를 지나가도 읽히게 바탕색을 깐다
            p.fillRect(rect.adjusted(-3, 0, 2, 0), QColor(T.PANEL))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            self.marks["avg_rect"] = rect
        if played:
            self.marks["label_rects"] = placed

        # 마지막 날 — 점만(지금 위치). 값은 툴팁
        end = pts[-1]
        p.setPen(QPen(QColor(T.PANEL), RING_W))
        p.setBrush(QColor(col))
        p.drawEllipse(end, DOT_R, DOT_R)

        if self._weak:
            # 표본 부족 점 — 빈 고리(흐림). 선은 그대로 잇는다: 빼면 남은 주들이 붙어 흐름이 거짓으로 매끈해진다
            self.marks["weak"] = [i for i, wk in enumerate(self._weak) if wk]
            p.setPen(QPen(QColor(T.TEXT_DIM), RING_W))
            p.setBrush(QColor(T.PANEL))
            for i in self.marks["weak"]:
                p.drawEllipse(pts[i], DOT_R, DOT_R)

        if self._counts:
            self._draw_count_peak(p, fm, xs, w - mr, placed)

        # x 라벨 — 처음·끝 + 사이 몇 개(겹치지 않게)
        p.setFont(small)
        p.setPen(QColor(T.TEXT_DIM))
        lab_w = max(fm.horizontalAdvance(lb) for lb, _, _ in self._points) + 12
        if self._x_dates is None:
            step = max(1, math.ceil(n / max(1, plot_w // lab_w)))
            shown = list(range(0, n, step))
        else:  # 날짜 간격이면 순번으로 건너뛰면 겹친다 — 앞 라벨과 떨어진 것만
            shown, last = [], -math.inf
            for i, x in enumerate(xs):
                if x - last >= lab_w:
                    shown.append(i)
                    last = x
        if n - 1 not in shown:
            if shown and pts[n - 1].x() - pts[shown[-1]].x() < lab_w:
                shown.pop()
            shown.append(n - 1)
        for i in shown:
            p.drawText(QRectF(pts[i].x() - lab_w / 2, foot + 4, lab_w, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, self._points[i][0])

        # 마우스 올림 — 가장 가까운 점까지의 세로 띠
        if self._x_dates is None:
            half = plot_w / (2 * (n - 1)) if n > 1 else plot_w / 2
            bands = [(x - half, 2 * half) for x in xs]
        else:
            mids = [(a + b) / 2 for a, b in zip(xs, xs[1:])]
            bands = [(a, max(b - a, 1)) for a, b in zip([ml] + mids, mids + [ml + plot_w])]
        for i, ((lb, v, games), (bx, bw)) in enumerate(zip(self._points, bands)):
            tip = f"{lb} · {ax.name} {ax.text(v)}"
            if self._ma is not None and self._ma[i] is not None:
                tip += f" · {self._ma_name} {ax.text(self._ma[i])}"
            if games is not None:
                tip += f" ({games}경기)"
            if self._band is not None and self._band[i] is not None:
                tip += f" · 그날 {ax.text(self._band[i][0])}~{ax.text(self._band[i][1])}"
            if self._ranks is not None and self._ranks[i]:
                tip += f" · {self._ranks[i]:,}위"
            if self._weak and self._weak[i]:
                tip += " · 표본 부족"
            self._hits.append((QRectF(bx, top, bw, foot - top), tip))

    @staticmethod
    def _free_rect(cands: list[QRectF], placed: list[QRectF]) -> QRectF:
        """후보 중 이미 놓인 글자와 안 겹치는 첫 칸 — 다 겹치면 마지막 후보."""
        for rect in cands:
            if not any(rect.intersects(r) for r in placed):
                return rect
        return cands[-1]

    def _draw_ma(self, p: QPainter, xs: list[float], y_of) -> None:
        pen = QPen(QColor(T.CHART_NEUTRAL), self.MA_W)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        segs, seg = [], []
        for x, m in zip(xs, self._ma):
            if m is None:
                if seg:
                    segs.append(seg)
                seg = []
            else:
                seg.append(QPointF(x, y_of(m)))
        if seg:
            segs.append(seg)
        for s in segs:
            if len(s) == 1:
                p.drawPoint(s[0])
            else:
                p.drawPolyline(QPolygonF(s))
        self.marks["ma_points"] = sum(len(s) for s in segs)
        self.marks["ma_segments"] = len(segs)

    def _draw_ranks(self, p: QPainter, fm: QFontMetrics, xs: list[float], top: float, bottom: float,
                    right: float) -> None:
        """순위 점선 + 오른쪽 축(로그 — 1위와 1만 위를 한 축에). 축 글자는 가장 좋은·나쁜 순위 둘."""
        known = [r for r in self._ranks if r]
        lo, hi = math.log10(min(known)), math.log10(max(known))
        if hi - lo < 0.05:              # 순위가 거의 그대로면 가운데 한 줄
            lo, hi = lo - 0.5, hi + 0.5

        def y_of(r: int) -> float:
            return top + (bottom - top) * (math.log10(r) - lo) / (hi - lo)

        pen = QPen(QColor(T.CHART_RANK), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        segs, seg = [], []
        for x, r in zip(xs, self._ranks):
            if not r:
                if seg:
                    segs.append(seg)
                seg = []
            else:
                seg.append(QPointF(x, y_of(r)))
        if seg:
            segs.append(seg)
        for s in segs:
            if len(s) == 1:
                p.drawEllipse(s[0], 1.5, 1.5)
            else:
                p.drawPolyline(QPolygonF(s))
        p.setPen(QColor(T.CHART_RANK))
        for r in sorted({min(known), max(known)}):
            y = y_of(r)
            p.drawText(QRectF(right + 6, y - fm.height() / 2, 200, fm.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{r:,}위")
        self.marks["rank_segments"] = len(segs)
        self.marks["rank_axis"] = (min(known), max(known))

    def _draw_ref_lines(self, p: QPainter, y_of, ml: float, plot_w: float) -> list:
        """기준 계단선 — 그 날짜에 유효했던 값(그날 이전 마지막 값)을 오른쪽으로 잇는다.
        날짜가 없으면(순번 x) 어디에 놓을지 몰라 안 그린다. 돌려주는 것: 오른쪽 끝 글자 재료."""
        if self._x_dates is None:
            self.marks["ref"] = [(name, "no-dates", "") for name, _, _ in self._refs]
            return []
        d0, dn = self._x_dates[0], self._x_dates[-1]
        span = (dn - d0).days

        def x_of(d) -> float:
            if not span:
                return ml + plot_w / 2
            return min(max(ml + plot_w * (d - d0).days / span, ml), ml + plot_w)

        ends = []
        for name, series, color in self._refs:
            series = sorted(series, key=lambda t: t[0])
            if not series:
                continue
            before = [t for t in series if t[0] <= d0]
            steps = ([(d0, before[-1][1])] if before else []) + [t for t in series if d0 < t[0] <= dn]
            if not steps:  # 전부 창 뒤 — 첫 값부터
                steps = [series[0]]
            pen = QPen(QColor(color), 1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            path = QPainterPath(QPointF(x_of(steps[0][0]), y_of(steps[0][1])))
            for (_, v0), (d1, v1) in zip(steps, steps[1:]):
                path.lineTo(x_of(d1), y_of(v0))
                path.lineTo(x_of(d1), y_of(v1))
            path.lineTo(ml + plot_w, y_of(steps[-1][1]))
            p.strokePath(path, pen)
            ends.append((name, steps[-1][1]))
        return ends

    def _draw_ref_labels(self, p: QPainter, fm: QFontMetrics, ends, right: float,
                         top: float, bottom: float, placed: list[QRectF]) -> None:
        """기준선 오른쪽 끝 글자 — 축 범위 밖이면 끝에 ▲/▼ 로(선은 가장자리에 붙는다)."""
        ax = self._axis
        states = []
        for name, v in ends:
            if v > ax.hi:
                state, text, ys = "above", f"▲ {name} {ax.text(v)}", [top - fm.height() + 2, top + 2]
            elif v < ax.lo:
                state, text, ys = "below", f"▼ {name} {ax.text(v)}", [bottom - fm.height() - 2]
            else:
                y = bottom - (bottom - top) * (v - ax.lo) / (ax.hi - ax.lo)
                state, text, ys = "in", f"{name} {ax.text(v)}", [y - fm.height() - 2, y + 2]
            tw = fm.horizontalAdvance(text)
            cands = [QRectF(right - tw, max(y, 0), tw, fm.height()) for y in ys]
            cands += [QRectF(right - tw, max(ys[-1], 0) + k * fm.height(), tw, fm.height()) for k in (1, -1)]
            rect = self._free_rect(cands, placed)
            p.fillRect(rect.adjusted(-3, 0, 2, 0), QColor(T.PANEL))
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            placed.append(rect)
            states.append((name, state, text))
        self.marks["ref"] = states

    def _draw_header(self, p: QPainter, fm: QFontMetrics, w: int) -> int:
        """왼쪽 위 범례 · 오른쪽 위 큰 값(66 G0) — 차지한 높이를 돌려준다."""
        big = _small_font(self.font(), self.HEAD_PT, bold=True)
        bfm = QFontMetrics(big)
        hh = max(fm.height(), bfm.height() if self._headline else 0)
        x = 4
        for name, color, dashed in self._legend:
            pen = QPen(QColor(color), 2)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, hh / 2), QPointF(x + 16, hh / 2))
            p.setPen(QColor(T.TEXT_DIM))
            tw = fm.horizontalAdvance(name)
            p.drawText(QRectF(x + 20, 0, tw, hh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            x += 20 + tw + 12
        if self._legend:
            self.marks["legend"] = [n for n, _, _ in self._legend]
        if self._headline:
            tw = bfm.horizontalAdvance(self._headline)
            rect = QRectF(max(w - tw - 4, x), 0, tw, hh)
            p.setFont(big)
            p.setPen(QColor(T.TEXT))
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._headline)
            self.marks["headline"] = (self._headline, rect)
        return int(hh)

    def _draw_band(self, p: QPainter, xs: list[float], y_of, color: str) -> None:
        """띠(66 U3) — 이어진 날은 면, 이웃 없이 혼자인 날은 세로 막대(M10 — 면이 안 생긴다)."""
        runs, run = [], []
        for i, b in enumerate(self._band):
            if b is None:
                if run:
                    runs.append(run)
                run = []
            else:
                run.append(i)
        if run:
            runs.append(run)
        p.setPen(Qt.PenStyle.NoPen)
        fill = _color(color, BAND_ALPHA)
        areas = bars = 0
        for run in runs:
            if len(run) == 1:
                i = run[0]
                gaps = [abs(xs[j] - xs[i]) for j in (i - 1, i + 1) if 0 <= j < len(xs)]
                bw = min(min(gaps) / 3 if gaps else BAND_BAR_MAX, BAND_BAR_MAX)
                lo, hi = self._band[i]
                y1, y2 = y_of(hi), y_of(lo)
                p.fillRect(QRectF(xs[i] - bw / 2, y1, bw, max(y2 - y1, 1.0)), fill)
                bars += 1
                continue
            poly = [QPointF(xs[i], y_of(self._band[i][1])) for i in run]
            poly += [QPointF(xs[i], y_of(self._band[i][0])) for i in reversed(run)]
            p.setBrush(fill)
            p.drawPolygon(QPolygonF(poly))
            areas += 1
        self.marks["band"] = (areas, bars)

    def _draw_counts(self, p: QPainter, xs: list[float], top: float, bottom: float) -> None:
        """경기 수 — 선 뒤 회색 막대(66 P6 · 가장 큰 값이 높이의 COUNT_FRAC). 숫자는 _draw_count_peak."""
        games = [g or 0 for _, _, g in self._points]
        peak = max(games)
        gaps = [b - a for a, b in zip(xs, xs[1:]) if b > a]
        bw = max(2.0, min(BAR_MAX, (min(gaps) if gaps else BAR_MAX) * 0.6))
        self._count_bw = bw
        self._count_tops: dict[int, float] = {}
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(T.CHART_NEUTRAL, self.COUNT_ALPHA))
        drawn = 0
        for i, (x, g) in enumerate(zip(xs, games)):
            if g <= 0:
                continue
            bh = max((bottom - top) * self.COUNT_FRAC * g / peak, 1.0)
            r = min(BAR_RADIUS, bw / 2)
            p.drawRoundedRect(QRectF(x - bw / 2, bottom - bh, bw, bh), r, r)
            self._count_tops[i] = bottom - bh
            drawn += 1
        self.marks["count_bars"] = drawn

    def _draw_count_peak(self, p: QPainter, fm: QFontMetrics, xs: list[float], right: float,
                         placed: list[QRectF]) -> None:
        """가장 큰 경기 수만 숫자(전부 적으면 읽히지 않는다) — 막대 위, 다른 글자와 겹치면 옆."""
        games = [g or 0 for _, _, g in self._points]
        peak = max(games)
        if peak <= 0:
            return
        i = max(range(len(games)), key=lambda k: (games[k], k))
        text = f"{peak:,}경기"
        tw, th, bw, ty = fm.horizontalAdvance(text), fm.height(), self._count_bw, self._count_tops[i]
        cands = [QRectF(min(max(xs[i] - tw / 2, 0), right - tw), ty - th - 2, tw, th),
                 QRectF(xs[i] + bw / 2 + 4, ty, tw, th), QRectF(xs[i] - bw / 2 - 4 - tw, ty, tw, th)]
        cands = [r for r in cands if r.left() >= 0 and r.right() <= right + 1] or cands[:1]
        rect = self._free_rect(cands, placed)
        p.setFont(_small_font(self.font()))
        p.setPen(QColor(T.TEXT_DIM))
        p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        self.marks["count_peak"] = (i, text)


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


class HistogramChart(_Chart):
    """점수 분포(2.3.1 B5) — 같은 폭 칸의 세로 막대 + 비교 선 하나 + 세로선들(검색 계정 실선 · 순위 컷 점선).
    faint_first: 맨 왼쪽 칸을 흐리게(1만 위 컷에서 잘려 일부만 센다). 막대 위 숫자는 없다 — 툴팁과 아래 축 글자."""

    def __init__(self):
        super().__init__(min_h=220)
        self._bins: list[float] = []      # 칸 왼쪽 끝
        self._width = 1.0
        self._main: list[float] = []
        self._main_name = ""
        self._cmp: list[float] | None = None
        self._cmp_name = ""
        self._marks: list[tuple[float, str, str, bool]] = []   # (값, 글자, 색, 점선)
        self._faint_first = False
        self.mark_rects: list[QRectF] = []   # 테스트가 글자 겹침을 잰다

    def set_data(self, bins: list[float], width: float, main: list[float], main_name: str = "",
                 cmp: list[float] | None = None, cmp_name: str = "",
                 marks: list[tuple[float, str, str, bool]] | None = None, faint_first: bool = False) -> None:
        self._bins, self._width, self._main, self._main_name = list(bins), float(width), list(main), main_name
        self._cmp, self._cmp_name = (list(cmp) if cmp else None), cmp_name
        self._marks = list(marks or ())
        self._faint_first = faint_first
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        self.mark_rects = []
        vmax = max([*self._main, *(self._cmp or [])], default=0)
        if not self._bins or vmax <= 0:
            self._empty(p, "기록 없음")
            return
        small = _small_font(self.font())
        fm = QFontMetrics(small)
        p.setFont(small)
        w, h = self.width(), self.height()
        mt, mb, ml, mr = fm.height() * 2 + 8, fm.height() + 8, 4, 4
        bottom = h - mb
        plot_h = max(bottom - mt, 1)
        lo, hi = self._bins[0], self._bins[-1] + self._width
        span = (hi - lo) or 1.0

        def xat(v: float) -> float:
            return ml + (w - ml - mr) * (v - lo) / span

        slot = (w - ml - mr) / len(self._bins)
        bar = max(2.0, slot - GAP)
        p.setPen(QPen(QColor(T.CHART_AXIS), 1))
        p.drawLine(QPointF(ml, bottom), QPointF(w - mr, bottom))
        for i, (b, v) in enumerate(zip(self._bins, self._main)):
            x = xat(b) + GAP / 2
            bh = plot_h * v / vmax
            faint = self._faint_first and i == 0
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
                p.fillPath(path, _color(T.CHART_UP, 0.35 if faint else 1.0))
            tip = f"{b:,.0f}~{b + self._width:,.0f} · {self._main_name} {v:,.0f}명"
            if self._cmp is not None and i < len(self._cmp):
                tip += f" · {self._cmp_name} {self._cmp[i]:,.0f}명"
            if faint:
                tip += " (1만 위 컷에서 잘려 일부만)"
            self._hits.append((QRectF(x - GAP, mt, bar + GAP * 2, plot_h), tip))
        if self._cmp is not None:
            pts = [QPointF(xat(b) + self._width * (w - ml - mr) / span / 2, bottom - plot_h * v / vmax)
                   for b, v in zip(self._bins, self._cmp)]
            pen = QPen(QColor(T.CHART_NEUTRAL), LINE_W)
            p.setPen(pen)
            p.drawPolyline(QPolygonF(pts))
        # 세로선 — 글자는 위 두 줄에 번갈아(가까운 선끼리 겹치지 않게)
        for k, (v, text, col, dashed) in enumerate(sorted(self._marks, key=lambda m: m[0])):
            if not lo <= v <= hi:
                continue
            x = xat(v)
            pen = QPen(QColor(col), LINE_W)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, mt - 2), QPointF(x, bottom))
            tw = fm.horizontalAdvance(text) + 4
            row = k % 2
            rect = QRectF(min(max(x - tw / 2, 0), w - tw), row * (fm.height() + 2), tw, fm.height())
            if any(rect.intersects(o) for o in self.mark_rects):
                rect.moveTop((1 - row) * (fm.height() + 2))
            self.mark_rects.append(rect)
            p.setPen(QColor(T.TEXT))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        # 아래 축 — 칸 경계 글자를 겹치지 않을 만큼만
        p.setPen(QColor(T.TEXT_DIM))
        step = max(1, math.ceil((fm.horizontalAdvance("4,500") + 8) / max(slot, 1)))
        for i in range(0, len(self._bins), step):
            b = self._bins[i]
            lw = fm.horizontalAdvance(f"{b:,.0f}") + 4
            x0 = min(max(xat(b) - lw / 2, 0), w - lw)    # 양 끝 글자가 창 밖으로 잘리지 않게("2,900" 이 "900" 으로 보였다)
            p.drawText(QRectF(x0, bottom + 4, lw, fm.height()), Qt.AlignmentFlag.AlignCenter, f"{b:,.0f}")


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
            p.setPen(QColor(T.CHART_ON_MARK))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, res[:1] if res[:1] in "승무패" else "?")
            self._hits.append((rect.adjusted(-GAP, -4, GAP, 4), tip))


class HBarList(_Chart):
    """가로 막대 몇 줄 — (이름, 값 0~100, 오른쪽 글, 툴팁[, 흐림]). 한 계열이라 범례 없음.

    다섯째 값이 참이면 표본 미달 — 막대·오른쪽 글을 흐리게(theme.WEAK_MIX, 불투명). "· 표본 N" 같은
    글자는 부르는 쪽이 오른쪽 글에 **합쳐** 넣는다 — 칸 폭을 그 글로 재므로 따로 그리면 겹친다."""

    ROW = 26
    THICK = 8

    def __init__(self):
        super().__init__(min_h=self.ROW)
        self._rows: list[tuple[str, float | None, str, str]] = []
        self._icons: list = []   # 줄마다 이름 앞 작은 그림(QPixmap | None) — 없으면 빈 목록

    def _icon_w(self) -> int:
        """이름 앞에 비워 둘 폭 — 그림이 하나라도 있으면 가장 넓은 그림 + 간격(없으면 0)."""
        return max((round(pm.width() / pm.devicePixelRatio()) + 6 for pm in self._icons
                    if pm is not None and not pm.isNull()), default=0)

    def set_data(self, rows: list[tuple[str, float | None, str, str]], icons: list | None = None) -> None:
        self._rows = list(rows)
        self._icons = list(icons or [])
        self.setMinimumHeight(max(len(self._rows), 1) * self.ROW)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(200, max(len(self._rows), 1) * self.ROW)

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetrics(_small_font(self.font(), SMALL_PT + 1))
        name_w = max((fm.horizontalAdvance(r[0]) for r in self._rows), default=0) + self._icon_w()
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
        icon_w = self._icon_w()
        name_w = max(fm.horizontalAdvance(r[0]) for r in self._rows) + 10 + icon_w
        val_w = max(fm.horizontalAdvance(r[2]) for r in self._rows) + 8
        bar_x = name_w
        bar_w = max(self.width() - name_w - val_w, 10)
        for i, (name, v, right, tip, *rest) in enumerate(self._rows):
            weak = bool(rest and rest[0])
            y = i * self.ROW
            mid = y + self.ROW / 2
            p.setPen(QColor(T.TEXT_DIM))
            pm = self._icons[i] if i < len(self._icons) else None
            if pm is not None and not pm.isNull():
                p.drawPixmap(QPointF(0, mid - pm.height() / pm.devicePixelRatio() / 2), pm)
            p.drawText(QRectF(icon_w, y, name_w - icon_w - 6, self.ROW),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            track = QRectF(bar_x, mid - self.THICK / 2, bar_w, self.THICK)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.CHART_GRID))
            p.drawRoundedRect(track, self.THICK / 2, self.THICK / 2)
            if v is not None and v > 0:
                fill = QRectF(track.left(), track.top(),
                              max(track.width() * min(v, 100) / 100, self.THICK), self.THICK)
                p.setBrush(QColor(T.blend(T.CHART_GRID, T.CHART_UP, T.WEAK_MIX) if weak else T.CHART_UP))
                p.drawRoundedRect(fill, self.THICK / 2, self.THICK / 2)
            p.setPen(QColor(T.TEXT_DIM if weak else T.TEXT))
            p.drawText(QRectF(self.width() - val_w + 6, y, val_w - 6, self.ROW),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, right)
            self._hits.append((QRectF(0, y, self.width(), self.ROW), tip))


class DivisionRunChart(_Chart):
    """등급 계단(66 G1) — 가로 한 칸 = 같은 등급이 이어진 구간 하나(`habits.division_runs`) · 칸 폭은 날짜와 무관.

    세로는 나온 등급만(18단계를 다 그리면 실제 폭이 눌린다) · id 가 작을수록 높아 위로 · 축은 배지 그림(`set_badges` —
    아직 못 받았거나 못 받는 등급은 이름 글자). head = (지금 등급, 최고 등급, 최고 라벨) — 오른쪽 위 한 줄 · 확대해도
    부르는 쪽이 시즌 전체로 준다. markers: 세로 점선을 그을 구간 번호(슈챔 진입 — 구간 시작에)."""

    BADGE = 20          # 축·머리 배지 크기(px)
    HEAD_H = 26         # 머리 줄 높이
    EMPTY = "표시 구간에 등급 정보가 있는 경기가 없습니다."

    def __init__(self):
        super().__init__(min_h=220)
        self._runs: list = []
        self._names: dict[int, str] = {}
        self._tips: list[str] = []
        self._markers: list[int] = []
        self._head: tuple[int, int, str] | None = None
        self._badges: dict[int, object] = {}   # 등급 id → 줄인 QPixmap
        self.marks: dict = {}

    def set_data(self, runs: list, names: dict[int, str], tips: list[str] | None = None, markers=(),
                 head: tuple[int, int, str] | None = None) -> None:
        self._runs = list(runs)
        self._names = names
        self._tips = list(tips) if tips is not None else []
        self._markers = sorted(set(markers))
        self._head = head
        self.update()

    def set_badges(self, badges: dict) -> None:
        """{등급 id: QPixmap} — 그릴 크기로 한 번만 줄여 둔다(그릴 때마다 줄이면 확대 막대를 끌 때 느리다)."""
        px = self.BADGE
        self._badges = {div: pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                        for div, pm in badges.items() if pm is not None and not pm.isNull()}
        self.update()

    def _name(self, div: int) -> str:
        return self._names.get(div, str(div))

    def _pieces_w(self, fm: QFontMetrics, pieces: list) -> float:
        """머리 줄 폭 — 글 조각 (색, 글) 과 배지 자리(등급 id · 그림이 있을 때만 폭을 먹는다)."""
        return sum(fm.horizontalAdvance(x[1]) if isinstance(x, tuple) else (self.BADGE + 4 if x in self._badges else 0)
                   for x in pieces)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        self._hits = []
        self.marks = {}
        f = _small_font(self.font())
        p.setFont(f)
        fm = QFontMetrics(f)
        if not self._runs:
            self._empty(p, self.EMPTY)
            return

        # 머리 줄 — "현재 [배지] 이름 · 시즌 최고 [배지] 이름"(오른쪽 맞춤)
        if self._head is not None:
            cur, best, best_label = self._head
            pieces = [(T.TEXT_DIM, "현재 "), cur, (T.TEXT, f" {self._name(cur)}"),
                      (T.TEXT_DIM, f"  ·  {best_label} "), best, (T.TEXT, f" {self._name(best)}")]
            x = w - 4 - self._pieces_w(fm, pieces)
            mid = self.HEAD_H / 2
            for piece in pieces:
                if isinstance(piece, tuple):
                    color, text = piece
                    p.setPen(QColor(color))
                    tw = fm.horizontalAdvance(text)
                    p.drawText(QRectF(x, 0, tw + 1, self.HEAD_H), Qt.AlignmentFlag.AlignVCenter, text)
                    x += tw
                else:
                    pm = self._badges.get(piece)
                    if pm is not None:
                        p.drawPixmap(QPointF(x + 2, mid - pm.height() / pm.devicePixelRatio() / 2), pm)
                        x += self.BADGE + 4
            self.marks["head"] = (cur, best, best_label)

        n = len(self._runs)
        levels = sorted({r.division for r in self._runs}, reverse=True)   # id 큰 것(낮은 등급)부터 = 아래부터
        axis_w = max((self.BADGE if div in self._badges else fm.horizontalAdvance(self._name(div))) for div in levels)
        ml, mr = axis_w + 12, 12
        mt = (self.HEAD_H if self._head is not None else 0) + 10
        mb = fm.height() + 10
        plot_w = max(w - ml - mr, 1)
        plot_h = max(h - mt - mb, 1)
        y_of = {div: (mt + plot_h - plot_h * i / (len(levels) - 1) if len(levels) > 1 else mt + plot_h / 2)
                for i, div in enumerate(levels)}

        grid = QPen(QColor(T.CHART_GRID), 1)
        grid.setDashPattern([3, 3])
        drawn_badges = 0
        for div in levels:
            y = y_of[div]
            p.setPen(grid)
            p.drawLine(QPointF(ml, y), QPointF(w - mr, y))
            pm = self._badges.get(div)
            if pm is not None:
                p.drawPixmap(QPointF(ml - 8 - pm.width() / pm.devicePixelRatio(),
                                     y - pm.height() / pm.devicePixelRatio() / 2), pm)
                drawn_badges += 1
            else:
                p.setPen(QColor(T.TEXT_DIM))
                p.drawText(QRectF(2, y - 8, ml - 10, 16),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._name(div))
        self.marks["levels"] = levels
        self.marks["badges"] = drawn_badges

        slot = plot_w / n
        x_of = lambda i: ml + slot * i   # noqa: E731 — 구간 i 의 왼쪽 끝(i == n 이면 오른쪽 끝)

        shown = [i for i in self._markers if 0 <= i < n]
        if shown:
            pen = QPen(QColor(T.CHART_NEUTRAL), 1)
            pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            for i in shown:
                p.drawLine(QPointF(x_of(i), mt), QPointF(x_of(i), mt + plot_h))
        self.marks["markers"] = len(shown)

        # 계단 — 구간마다 가로 한 칸, 등급이 바뀌는 자리에서 세로로
        path = QPainterPath(QPointF(x_of(0), y_of[self._runs[0].division]))
        for i, r in enumerate(self._runs):
            y = y_of[r.division]
            if i:
                path.lineTo(QPointF(x_of(i), y))
            path.lineTo(QPointF(x_of(i + 1), y))
        p.setPen(QPen(QColor(T.CHART_LINE), LINE_W))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        self.marks["runs"] = n

        # 가로 라벨 — 구간 시작 날짜 · 겹치거나 앞과 같은 날이면 건너뛴다
        p.setPen(QColor(T.TEXT_DIM))
        lw = fm.horizontalAdvance("00/00") + 8
        last_right, last_text, labels = -1e9, None, []
        for i, r in enumerate(self._runs):
            text = f"{r.start:%m/%d}"
            x = x_of(i)
            if text == last_text or x - lw / 2 < last_right or x + lw / 2 > w:
                continue
            p.drawText(QRectF(x - lw / 2, h - mb + 6, lw, fm.height()), Qt.AlignmentFlag.AlignCenter, text)
            last_right, last_text = x + lw / 2, text
            labels.append(text)
        self.marks["labels"] = labels

        for i, r in enumerate(self._runs):
            tip = self._tips[i] if i < len(self._tips) else \
                f"{r.start:%m/%d %H:%M} ~ {r.end:%m/%d %H:%M} · {self._name(r.division)} · {r.games}판"
            self._hits.append((QRectF(x_of(i), mt, slot, plot_h), tip))


@dataclass
class LuckRow:
    """운 범위 한 줄(66 G4) — 화면이 `CalDay` 에서 만든다. 퍼센트(0~100)."""
    label: str          # "10/27"
    games: int
    rate: float
    lo: float
    hi: float
    base: float         # 그날 기준(그 시즌 승률) — 흰 세로선
    judged: bool        # False = 판수 미달 — 빈 고리 + 흐린 글자
    arrow: int          # +1 위 · −1 아래 · 0 안 — 밖이면 위아래 같은 주의 색
    tip: str


class LuckDotRows(_Chart):
    """날마다 한 줄 — 날짜 · 판수 · 트랙(가로 0~100% 고정) 위 운 범위 띠 · 기준 세로선 · 그날 승률 점 · 오른쪽 승률(66 G4).
    폭이 `TWO_COL_W` 이상이면 두 열(세로로 먼저 채운다) — 1280 창에서 한 열 폭이 트랙을 못 담으면 한 열."""

    ROW_H = 24
    LABEL_W = 44
    GAMES_W = 44
    RATE_W = 52
    TRACK_MIN = 160
    COL_GAP = 28
    COL_MIN = LABEL_W + GAMES_W + TRACK_MIN + RATE_W + 24
    TWO_COL_W = 2 * COL_MIN + COL_GAP
    DOT_R = 5
    EMPTY = "경기가 있는 날이 없습니다."

    def __init__(self):
        super().__init__(min_h=self.ROW_H * 2)
        self._rows: list[LuckRow] = []
        self.cols = 1

    def set_data(self, rows: list[LuckRow]) -> None:
        self._rows = list(rows)
        self._fit_height()
        self.update()

    def _columns(self, width: int) -> int:
        return 2 if width >= self.TWO_COL_W and len(self._rows) > 1 else 1

    def _fit_height(self) -> None:
        self.cols = self._columns(self.width())
        per = math.ceil(len(self._rows) / self.cols) if self._rows else 2
        self.setMinimumHeight(per * self.ROW_H + 4)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._columns(self.width()) != self.cols:
            self._fit_height()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        f = _small_font(self.font())
        p.setFont(f)
        if not self._rows:
            self._empty(p, self.EMPTY)
            return
        cols = self.cols
        per = math.ceil(len(self._rows) / cols)
        col_w = (self.width() - self.COL_GAP * (cols - 1)) / cols
        for i, r in enumerate(self._rows):
            c, k = divmod(i, per)
            x0 = c * (col_w + self.COL_GAP)
            y = 2 + k * self.ROW_H
            dim = not r.judged
            p.setPen(QColor(T.TEXT_DIM if dim else T.TEXT))
            p.drawText(QRectF(x0, y, self.LABEL_W, self.ROW_H), Qt.AlignmentFlag.AlignVCenter, r.label)
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(x0 + self.LABEL_W, y, self.GAMES_W, self.ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{r.games}판")
            tx = x0 + self.LABEL_W + self.GAMES_W + 12
            tw = max(col_w - (tx - x0) - self.RATE_W - 12, 10)
            cy = y + self.ROW_H / 2
            track = QRectF(tx, cy - 4, tw, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.PANEL_2))
            p.drawRoundedRect(track, 4, 4)
            x_of = lambda v: tx + tw * min(max(v, 0.0), 100.0) / 100.0   # noqa: E731
            if r.judged:
                p.setBrush(_color(T.CHART_NEUTRAL, 0.45))
                p.drawRoundedRect(QRectF(x_of(r.lo), cy - 4, max(x_of(r.hi) - x_of(r.lo), 2), 8), 4, 4)
            p.setPen(QPen(QColor(T.TEXT), 1.5))
            p.drawLine(QPointF(x_of(r.base), cy - 7), QPointF(x_of(r.base), cy + 7))
            dot = QPointF(x_of(r.rate), cy)
            if r.judged:
                p.setPen(QPen(QColor(T.PANEL), RING_W))
                p.setBrush(QColor(T.WARN_FILL if r.arrow else T.ACCENT_FILL))
            else:
                p.setPen(QPen(QColor(T.CHART_NEUTRAL), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(dot, self.DOT_R, self.DOT_R)
            p.setPen(QColor(T.TEXT_DIM if dim else (T.WARN if r.arrow else T.TEXT)))
            p.drawText(QRectF(x0 + col_w - self.RATE_W, y, self.RATE_W, self.ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{r.rate:.1f}%")
            self._hits.append((QRectF(x0, y, col_w, self.ROW_H), r.tip))


DONUT_MAX = 6  # 도넛 조각 상한 — 넘치면 앞 DONUT_MAX-1 개 + "기타"


def donut_segments(counts: list[tuple[str, float]], max_n: int = DONUT_MAX
                   ) -> list[tuple[str, float, str]]:
    """(이름, 값) 목록 → 도넛 조각 (이름, 값, 색). 큰 순서로 색을 주고, max_n 을 넘으면 꼬리를 "기타" 하나로.

    색이 조각 순서를 따라가므로 같은 화면의 범례(BarRow)도 이 결과의 색을 쓴다."""
    rows = sorted(((n, v) for n, v in counts if v > 0), key=lambda kv: -kv[1])
    if len(rows) > max_n:
        rest = sum(v for _, v in rows[max_n - 1:])
        rows = rows[:max_n - 1] + [("기타", rest)]
    cats = T.CHART_CATS
    out = []
    for i, (name, v) in enumerate(rows):
        col = T.CHART_NEUTRAL if name == "기타" or i >= len(cats) else cats[i]
        out.append((name, v, col))
    return out


HEAT_FULL_PP = 25.0   # 승률이 50% 에서 이만큼(%p) 벗어나면 가장 진한 칸
HEAT_MAX_MIX = 0.6    # 칸 색 상한 — 이보다 진하면 어두운 테마에서 칸 안 글자(TEXT) 대비가 4.5 밑(테스트가 잰다)


def heat_cell_color(rate: float) -> str:
    """승률 → 칸 색(불투명). 50% 는 바탕, 위는 CHART_UP 쪽, 아래는 CHART_DOWN 쪽으로 최대 HEAT_MAX_MIX."""
    frac = min(abs(rate - 50.0) / HEAT_FULL_PP, 1.0)
    return T.blend(T.PANEL, T.CHART_UP if rate >= 50 else T.CHART_DOWN, frac * HEAT_MAX_MIX)


class HeatmapChart(_Chart):
    """행 × 열 칸 — 칸마다 (승률 | None, 칸 글자, 툴팁, 흐림). 색만으로 읽히지 않게 칸 안에 글자.

    흐린 칸(표본 미달)은 색을 칠하지 않고 회색 칸 + "표본 N" 같은 글자 — 소표본 100% 가 가장 진한 칸이 되지 않게.
    최소 폭은 칸마다 "100%" 글자 + 8 을 알린다(QScrollArea 가 가로를 조용히 자르지 않게 — PyQt 규칙 5)."""

    ROW_H = 30
    HEAD_H = 22
    PAD = 8

    def __init__(self):
        super().__init__(min_h=self.HEAD_H + self.ROW_H)
        self._rows: list[str] = []
        self._cols: list[str] = []
        self._cells: list[list[tuple[float | None, str, str, bool]]] = []
        self.cell_colors: list[list[str | None]] = []   # 마지막으로 정한 칸 색(테스트가 본다)

    def set_data(self, rows: list[str], cols: list[str],
                 cells: list[list[tuple[float | None, str, str, bool]]]) -> None:
        self._rows, self._cols, self._cells = list(rows), list(cols), [list(r) for r in cells]
        self.cell_colors = [[None if (weak or v is None) else heat_cell_color(v)
                             for v, _t, _tip, weak in r] for r in self._cells]
        self.setMinimumHeight(self.HEAD_H + max(len(self._rows), 1) * self.ROW_H)
        self.updateGeometry()
        self.update()

    def _font(self) -> QFont:
        return _small_font(self.font(), SMALL_PT + 1)

    def _label_w(self, fm: QFontMetrics) -> int:
        return max((fm.horizontalAdvance(r) for r in self._rows), default=0) + self.PAD * 2

    def cell_min_w(self) -> int:
        fm = QFontMetrics(self._font())
        texts = ["100%"] + [t for r in self._cells for _v, t, _tip, _w in r]
        return max(fm.horizontalAdvance(t) for t in texts) + self.PAD

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetrics(self._font())
        return QSize(self._label_w(fm) + max(len(self._cols), 1) * self.cell_min_w(),
                     self.HEAD_H + max(len(self._rows), 1) * self.ROW_H)

    def sizeHint(self) -> QSize:
        m = self.minimumSizeHint()
        return QSize(max(m.width(), 480), m.height())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        if not self._rows or not any(v is not None for r in self._cells for v, *_ in r):
            self._empty(p)
            return
        f = self._font()
        fm = QFontMetrics(f)
        p.setFont(f)
        lw = self._label_w(fm)
        cw = max((self.width() - lw) / max(len(self._cols), 1), 1)
        p.setPen(QColor(T.TEXT_DIM))
        for j, c in enumerate(self._cols):
            p.drawText(QRectF(lw + j * cw, 0, cw, self.HEAD_H), Qt.AlignmentFlag.AlignCenter, c)
        for i, name in enumerate(self._rows):
            y = self.HEAD_H + i * self.ROW_H
            p.setPen(QColor(T.TEXT_DIM))
            p.drawText(QRectF(0, y, lw - self.PAD, self.ROW_H),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
            for j, (v, text, tip, weak) in enumerate(self._cells[i]):
                rect = QRectF(lw + j * cw + GAP / 2, y + GAP / 2, cw - GAP, self.ROW_H - GAP)
                col = self.cell_colors[i][j]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(col or T.CHART_GRID))
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(QColor(T.TEXT if col else T.TEXT_DIM))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
                self._hits.append((rect, tip))
