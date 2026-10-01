"""대시보드 페이지 — 그래프 카드 배치와 채우기.

카드마다 '눌러서 넘어가는 상세 페이지와 같은 범위'의 경기를 센다 — 대시보드
숫자와 상세 화면 숫자가 어긋나면 어느 쪽도 믿을 수 없게 된다. 그래서 카드
제목 옆에 범위를 작게 적는다.

    승률                               → 시즌 범위 + 그 안의 최근 20경기와 차이
    승무패 · 득실 · 연속 · 레이더 · 상대 → 표시 구간(시작~끝)
    승률 흐름                          → 승률 그래프와 같은 '최근 N일'
    승부처 · 15분 득실 · 시간대 · 분석  → 시즌 범위(표시 구간에 안 갇힘)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
                             QVBoxLayout, QWidget)

import analysis
import stats as st
import theme as T
from charts import (AreaTrendChart, DonutChart, GroupedBarChart, HBarList, RadarChart,
                    ResultDots, RingGauge)
from models import (MatchSummary, current_streak, longest_streaks, opponent_stats,
                    summarize)
from widgets import add_shadow, wdl_text

RECENT_N = 20          # 승률 카드의 '최근' 비교 구간
DOTS_N = 20            # 최근 결과 점 개수
TOP_OPPONENTS = 5
MIN_GAUGE_GAMES = 10   # 승부처 게이지 — 이보다 적은 표본이면 숫자 대신 "—"
SECTION_TAG = {analysis.SEC_FLOW: "흐름", analysis.SEC_WIN: "이길 때",
               analysis.SEC_LOSE: "질 때"}


@dataclass
class DashboardInput:
    """대시보드를 그리는 데 필요한 것 — 범위별로 나눠 받는다."""
    ouid: str
    range_matches: list[MatchSummary]          # 표시 구간, 최신순
    range_details: list[dict]
    scope_matches: list[MatchSummary]          # 시즌 범위(표시 구간에 안 갇힘), 최신순
    scope_details: list[dict]
    scope_name: str                            # "현재 시즌" · "누적 전체" …
    trend_points: list[tuple[str, float, int]] = field(default_factory=list)
    trend_days: int = 30


class DashCard(QFrame):
    """제목 + 범위 글 + 본문. target 이 있으면 눌렀을 때 navigate(target)."""

    clicked = pyqtSignal(str)

    def __init__(self, title: str, target: str | None = None):
        super().__init__()
        self.setObjectName("card")
        self.target = target
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.scope = QLabel("")
        self.scope.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.scope)
        v.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        v.addLayout(self.body, 1)
        if target:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"눌러서 '{target}' 보기")

    def mouseReleaseEvent(self, event) -> None:
        if self.target and event.button() == Qt.MouseButton.LeftButton \
                and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.target)
        super().mouseReleaseEvent(event)


def _label(text: str = "", *, dim: bool = False, pt: int | None = None,
           bold: bool = False, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if pt or bold:
        f = QFont()
        if pt:
            f.setPointSize(pt)
        f.setBold(bold)
        lb.setFont(f)
    lb.setStyleSheet(f"color: {T.TEXT_DIM if dim else T.TEXT};")
    lb.setWordWrap(wrap)
    return lb


def _legend(items: list[tuple[str, str]]) -> QWidget:
    """● 이름 — 색은 점에만, 글자는 글자색."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    for name, col in items:
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {col};")
        h.addWidget(dot)
        h.addSpacing(-8)
        h.addWidget(_label(name, dim=True))
    h.addStretch(1)
    return w


def _pct(n: int, d: int) -> float | None:
    return n / d * 100 if d else None


class DashboardPage(QWidget):
    """대시보드. render(DashboardInput) 로 채운다. 카드를 누르면 navigate(메뉴 이름)."""

    navigate = pyqtSignal(str)

    def __init__(self, ranker_card: QWidget):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        self.cards: list[DashCard] = []

        # ② 지표 4개
        row = QHBoxLayout()
        row.setSpacing(14)
        self.kpi_rate = self._card("승률", "경기 목록")
        self.lb_rate = _label("—", pt=22, bold=True)
        self.lb_rate_delta = _label("", dim=True)
        self.kpi_rate.body.addWidget(self.lb_rate)
        self.kpi_rate.body.addWidget(self.lb_rate_delta)

        self.kpi_wdl = self._card("승무패", "경기 목록")
        wdl = QHBoxLayout()
        self.donut = DonutChart(size=96)
        wdl.addWidget(self.donut)
        self.lb_wdl_legend = QVBoxLayout()
        self.lb_wdl_legend.setSpacing(2)
        self.wdl_rows = []
        for name, col in (("승", T.CHART_UP), ("무", T.CHART_NEUTRAL), ("패", T.CHART_DOWN)):
            line = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {col};")
            lb = _label(name)
            line.addWidget(dot)
            line.addWidget(lb, 1)
            self.lb_wdl_legend.addLayout(line)
            self.wdl_rows.append(lb)
        wdl.addLayout(self.lb_wdl_legend, 1)
        self.kpi_wdl.body.addLayout(wdl)

        self.kpi_goals = self._card("득실", "경기 목록")
        self.lb_goals = _label("—", pt=22, bold=True)
        self.lb_goals_sub = _label("", dim=True)
        self.kpi_goals.body.addWidget(self.lb_goals)
        self.kpi_goals.body.addWidget(self.lb_goals_sub)

        self.kpi_streak = self._card("연속", "경기 목록")
        self.lb_streak = _label("—", pt=22, bold=True)
        self.lb_streak_sub = _label("", dim=True)
        self.kpi_streak.body.addWidget(self.lb_streak)
        self.kpi_streak.body.addWidget(self.lb_streak_sub)
        for c in (self.kpi_rate, self.kpi_wdl, self.kpi_goals, self.kpi_streak):
            for i in range(c.body.count()):
                item = c.body.itemAt(i)
                if item.widget() is not None:
                    item.widget().setSizePolicy(QSizePolicy.Policy.Preferred,
                                                QSizePolicy.Policy.Fixed)
            c.body.addStretch(1)
            row.addWidget(c, 1)
        outer.addLayout(row)

        # ① 구단주 + ③ 승률 흐름
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(ranker_card, 1, Qt.AlignmentFlag.AlignTop)
        self.trend = self._card("승률 흐름", "승률 그래프")
        # 글자는 점 위 줄로 — 20개 점과 한 줄에 두면 1280 폭에서 자리가 모자란다
        self.trend.body.addWidget(_label(f"최근 {DOTS_N}경기 (왼쪽이 오래된 경기)", dim=True))
        self.dots = ResultDots()
        self.trend.body.addWidget(self.dots)
        self.trend_chart = AreaTrendChart()
        self.trend.body.addWidget(self.trend_chart, 1)
        row.addWidget(self.trend, 2)
        outer.addLayout(row)

        # ③ 레이더 + ④ 승부처 · 15분 득실
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        self.radar_card = self._card("나 vs 상대 평균")
        self.radar_card.body.addWidget(
            _legend([("나", T.CHART_UP), ("같은 경기 상대 평균", T.CHART_NEUTRAL)]))
        self.radar = RadarChart()
        self.radar_card.body.addWidget(self.radar, 1)
        self.radar_card.body.addWidget(
            _label("바깥일수록 그 항목에서 앞선다 · 진한 고리가 동률", dim=True, wrap=True))
        grid.addWidget(self.radar_card, 0, 0)

        self.clutch = self._card("승부처", "승부처 분석")
        gauges = QHBoxLayout()
        gauges.setSpacing(6)
        self.gauges: list[RingGauge] = []
        for cap in ("선제골 넣으면\n승률", "선제 실점 후\n역전승", "선제골 넣고\n끝까지 지킴"):
            col = QVBoxLayout()
            g = RingGauge(cap, size=88)
            col.addWidget(g, 0, Qt.AlignmentFlag.AlignHCenter)
            lb = _label(cap, dim=True)
            lb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lb)
            gauges.addLayout(col, 1)
            self.gauges.append(g)
        self.clutch.body.addLayout(gauges)
        self.clutch.body.addStretch(1)
        grid.addWidget(self.clutch, 0, 1)

        self.minutes = self._card("언제 넣고 먹히나", "승부처 분석")
        self.minutes.body.addWidget(_legend([("득점", T.CHART_UP), ("실점", T.CHART_DOWN)]))
        self.minute_chart = GroupedBarChart()
        self.minutes.body.addWidget(self.minute_chart, 1)
        grid.addWidget(self.minutes, 0, 2)
        outer.addLayout(grid)

        # ⑤ 분석 · 시간대 · 상대
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        self.story = self._card("흐름 분석", "흐름 분석")
        self.story_rows: list[tuple[QLabel, QLabel]] = []
        for _ in range(3):
            line = QHBoxLayout()
            tag = _label("", dim=True)
            tag.setFixedWidth(56)
            tag.setAlignment(Qt.AlignmentFlag.AlignTop)
            text = _label("", wrap=True)
            line.addWidget(tag)
            line.addWidget(text, 1)
            self.story.body.addLayout(line)
            self.story_rows.append((tag, text))
        self.story.body.addStretch(1)
        grid.addWidget(self.story, 0, 0)

        self.timeband = self._card("시간대 승률", "승부처 분석")
        self.timeband_bars = HBarList()
        self.timeband.body.addWidget(self.timeband_bars)
        self.timeband.body.addStretch(1)
        grid.addWidget(self.timeband, 0, 1)

        self.rivals = self._card("자주 만난 상대", "상대 전적")
        self.rival_bars = HBarList()
        self.rivals.body.addWidget(self.rival_bars)
        self.rivals.body.addStretch(1)
        grid.addWidget(self.rivals, 0, 2)
        outer.addLayout(grid)
        outer.addStretch(1)

    def _card(self, title: str, target: str | None = None) -> DashCard:
        c = DashCard(title, target)
        c.clicked.connect(self.navigate)
        add_shadow(c)
        self.cards.append(c)
        return c

    # ── 채우기 ──────────────────────────────────────────────────────────
    def render(self, d: DashboardInput) -> None:
        rng = f"표시 구간 {len(d.range_matches):,}경기"
        scope = f"{d.scope_name} {len(d.scope_matches):,}경기"
        for c in (self.kpi_wdl, self.kpi_goals, self.kpi_streak,
                  self.radar_card, self.rivals):
            c.scope.setText(rng)
        for c in (self.kpi_rate, self.clutch, self.minutes, self.timeband, self.story):
            c.scope.setText(scope)
        self.trend.scope.setText(f"최근 {d.trend_days}일 · 하루 단위")

        self._render_kpis(d)
        self._render_trend(d)
        self._render_radar(d)
        self._render_clutch(d)
        self._render_minutes(d)
        self._render_story(d)
        self._render_timeband(d)
        self._render_rivals(d)

    def _render_kpis(self, d: DashboardInput) -> None:
        # 승률은 시즌 전체가 큰 숫자 — 표시 구간(1~100 등)은 스핀박스에 따라 바뀌는
        # 임의의 묶음이라 기준으로 약하다. 요즘 폼은 같은 시즌의 최근 20경기와의 차이로.
        season = summarize(d.scope_matches)
        if not season.total:
            self.lb_rate.setText("—")
            self.lb_rate_delta.setText("경기 없음")
        else:
            self.lb_rate.setText(f"{season.win_rate:.1f}%")
            recent = summarize(d.scope_matches[:RECENT_N])
            if recent.total and season.total > recent.total:
                gap = recent.win_rate - season.win_rate
                arrow = "▲" if gap > 0 else "▼" if gap < 0 else "–"
                col = T.CHART_UP if gap > 0 else T.CHART_DOWN if gap < 0 else T.TEXT_DIM
                self.lb_rate_delta.setText(
                    f"최근 {RECENT_N}경기 {recent.win_rate:.1f}% "
                    f"<span style='color:{col}'>{arrow} {abs(gap):.1f}%p</span>")
            else:
                self.lb_rate_delta.setText(f"{season.total}경기")

        s = summarize(d.range_matches)
        self.donut.set_data([("승", s.win, T.CHART_UP), ("무", s.draw, T.CHART_NEUTRAL),
                             ("패", s.lose, T.CHART_DOWN)],
                            center=f"{s.total:,}", sub="경기")
        for lb, (name, n) in zip(self.wdl_rows, (("승", s.win), ("무", s.draw),
                                                 ("패", s.lose))):
            share = f" ({n / s.total * 100:.0f}%)" if s.total else ""
            lb.setText(f"{name} {n:,}{share}")
        self.kpi_wdl.setToolTip(wdl_text(s.win, s.draw, s.lose))

        if s.total:
            self.lb_goals.setText(f"{s.avg_goals_for:.2f} : {s.avg_goals_against:.2f}")
            diff = s.goals_for - s.goals_against
            self.lb_goals_sub.setText(f"경기당 득·실 · 득실차 {diff:+,}")
        else:
            self.lb_goals.setText("—")
            self.lb_goals_sub.setText("")

        kind, n = current_streak(d.range_matches)
        self.lb_streak.setText(f"{n}연{kind}" if kind in ("승", "패") and n
                               else f"{n}{kind}" if kind else "—")
        best_win, best_lose = longest_streaks(d.scope_matches)
        self.lb_streak_sub.setText(f"최장 {best_win}연승 · {best_lose}연패 ({d.scope_name})")

    def _render_trend(self, d: DashboardInput) -> None:
        recent = [m for m in d.range_matches[:DOTS_N]][::-1]
        self.dots.set_data([(m.result, f"{m.date_text} · {m.opponent} · {m.score} {m.result}")
                            for m in recent])
        self.trend_chart.set_data(d.trend_points)

    def _render_radar(self, d: DashboardInput) -> None:
        prof = st.team_profile(d.range_details, d.ouid)
        self.radar.set_data([
            (a.name, a.share,
             f"{a.name} — 나 {a.mine:.1f} · 상대 {a.opp:.1f} {a.unit} ({prof.games}경기)")
            for a in prof.axes])

    def _render_clutch(self, d: DashboardInput) -> None:
        cs = st.clutch_summary(d.scope_details, d.ouid)
        scored = sum(cs.first_scored)
        conceded = sum(cs.first_conceded)
        vals = [
            (_pct(cs.first_scored[0], scored), scored,
             f"선제골 넣은 {scored:,}경기 중 {cs.first_scored[0]:,}승"),
            (_pct(cs.comeback_win, conceded), conceded,
             f"선제 실점한 {conceded:,}경기 중 {cs.comeback_win:,}번 역전승"),
            (_pct(scored - cs.comeback_lose, scored), scored,
             f"선제골 넣은 {scored:,}경기 중 {cs.comeback_lose:,}번은 역전패"),
        ]
        for g, (v, n, tip) in zip(self.gauges, vals):
            if n < MIN_GAUGE_GAMES:
                g.set_data(None, f"{n}경기", f"표본 {n}경기 — {MIN_GAUGE_GAMES}경기부터 표시")
            else:
                g.set_data(v, f"{n:,}경기", tip)

    def _render_minutes(self, d: DashboardInput) -> None:
        buckets = st.goal_minute_buckets(d.scope_details, d.ouid)
        labels = [b.label.replace("~", "–") for b in buckets]
        self.minute_chart.set_data(labels, [
            ("득점", [b.scored for b in buckets], T.CHART_UP),
            ("실점", [b.conceded for b in buckets], T.CHART_DOWN)])

    def _render_story(self, d: DashboardInput) -> None:
        found = analysis.narrate(d.scope_matches, d.scope_details, d.ouid)
        picks = []
        for sec in analysis.SECTIONS:
            first = next((i for i in found if i.section == sec), None)
            if first:
                picks.append(first)
        for i, (tag, text) in enumerate(self.story_rows):
            if i < len(picks):
                tag.setText(SECTION_TAG.get(picks[i].section, ""))
                text.setText(picks[i].headline)
                text.setToolTip(picks[i].detail)
            else:
                tag.setText("")
                text.setText("표본이 모자라 아직 말할 수 있는 게 없습니다." if i == 0 else "")
                text.setToolTip("")

    def _render_timeband(self, d: DashboardInput) -> None:
        rows = []
        for b in st.time_of_day_rates(d.scope_matches):
            if b.games:
                rows.append((b.label, b.win_rate, f"{b.win_rate:.0f}% · {b.games:,}",
                             f"{b.label} {b.span} · {wdl_text(b.win, b.draw, b.lose)}"))
            else:
                rows.append((b.label, None, "경기 없음", f"{b.label} {b.span}"))
        self.timeband_bars.set_data(rows)

    def _render_rivals(self, d: DashboardInput) -> None:
        rows = []
        for o in opponent_stats(d.range_matches)[:TOP_OPPONENTS]:
            rows.append((o.nickname, o.win_rate, f"{o.win}승 {o.draw}무 {o.lose}패",
                         f"{o.nickname} · {o.games}경기 · 승률 {o.win_rate:.1f}%"
                         f" · 득실 {o.goals_for}:{o.goals_against}"))
        self.rival_bars.set_data(rows)
