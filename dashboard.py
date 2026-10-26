"""대시보드 페이지 — 그래프 카드 배치와 채우기.

카드마다 '눌러서 넘어가는 상세 페이지와 같은 범위'의 경기를 센다 — 대시보드
숫자와 상세 화면 숫자가 어긋나면 어느 쪽도 믿을 수 없게 된다. 그래서 카드
제목 옆에 범위를 작게 적는다.

    이번 시즌 구단(맨 위 · 55 · 65)     → 늘 이번 시즌(표시 구간·시즌 필터와 무관) — 칸은 창이 만든다(app_main.club_cells)
                                           · 그 안 승률 카드만 시즌 범위(위 셋은 이번 시즌 = 기본 시즌 범위와 같다)
    이 구단 리포트(65)                  → 이번 시즌 점수 기록(점수·예측 탭 리포트와 같은 계산) · 최근 20경기는 시즌 범위
    자주 만난 상대                      → 시즌 범위(사용자 요청 — 100경기 안에선 대부분 1번씩이라
                                           '자주'가 안 드러난다). 상대 전적 페이지도 같은 범위(10-02)
    경기 결과 · 골·실점 · 나와 상대    → 표시 구간(시작~끝) — 나와 상대는 몰수·오류 뺌(stats.versus_profile)
    점수 해설(65)                       → 리포트와 같은 재료
    승률 흐름                          → 승률 그래프와 같은 '최근 N일'
    승부처 · 15분 득실 · 시간대 · 분석  → 시즌 범위(표시 구간에 안 갇힘)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QVBoxLayout, QWidget)

import config
import core_api as core
import theme as T
from charts import AreaTrendChart, GroupedBarChart, HBarList, ResultDots, RingGauge
from core_api import (MatchSummary, current_streak, longest_streaks, opponent_stats,
                    summarize)
from widgets import (Collapsible, StatCard, Tag, VersusBar, WdlBar, delta_text, sample_note, sample_text,
                     wdl_text, win_rate_note, win_rate_text)

RECENT_N = 20          # 리포트 '최근' 승률 구간
DOTS_N = 20            # 최근 결과 점 개수
TOP_OPPONENTS = 5
MIN_GAUGE_GAMES = 10   # 승부처 게이지 — 이보다 적은 표본이면 숫자 대신 "—"
# 점수 해설(P9) 질문 — 재료가 있는 둘만(스쿼드 기대 승률 · 같은 스쿼드 사이 위치는 59)
NOTE_QUESTIONS = ("점수가 오르는 구간인가?", "가장 깊었던 하락은?")
SECTION_TAG = {core.SEC_FLOW: "흐름", core.SEC_WIN: "이길 때",
               core.SEC_LOSE: "질 때"}


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
    trend_weights: list[int] | None = None   # 점마다 승률 분모(rated) — 평균선 무게(A6). 없으면 경기 수
    trend_days: int = 30
    # 흐름 분석 결과(analysis.narrate) — 창이 흐름 분석 메뉴와 같이 쓰려고 미리 계산해 넘긴다.
    # 없으면 여기서 계산한다(같은 범위 · scope_matches/details)
    story: list | None = None
    # 55 머리 줄 — (제목, 큰 글자, 작은 글자, 툴팁) 여섯 칸. None 이면 계정 없음
    club_cells: list | None = None
    headline: str | None = None                # 57 K6 한 줄 총평(analysis.headline) — None 이면 빈 줄(자리는 그대로)
    report: ScoreParts | None = None           # 65 M1 리포트·해설 — 점수 기록이 모자라면 None


@dataclass
class ScoreParts:
    """리포트·해설 재료 — 점수·예측 탭 리포트(app_main.score_report_lines)와 같은 계산."""
    rep: object                         # habits.ScoreReport
    band: tuple[float, int] | None      # 같은 구간(최고점 ±R2_BAND) 중앙 낙폭·사람 수 — rank.db 없으면 None
    decisive: tuple[float, int] | None  # 무승부 뺀 승률(%)·승+패 수


class DashCard(QFrame):
    """제목 + 범위 글 + 본문. target(메뉴, 탭 또는 None)이 있으면 눌렀을 때 navigate(target).

    탭까지 적는다(2.1.1) — 메뉴만 적으면 그 메뉴에서 마지막에 본 탭으로 열려 카드와 다른 내용이 보인다."""

    clicked = pyqtSignal(object)

    def __init__(self, title: str, target: tuple[str, str | None] | None = None):
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
            menu, tab = target
            where = f"{menu} › {tab}" if tab else menu
            self.setToolTip(f"눌러서 '{where}' 보기")

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
    """대시보드. render(DashboardInput) 로 채운다. 카드를 누르면 navigate((메뉴, 탭 또는 None))."""

    navigate = pyqtSignal(object)

    def __init__(self, ranker_card: QWidget):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        self.cards: list[DashCard] = []

        # ⓪ 이번 시즌 구단(55 · 65 M1) — 숫자 카드 넷 → 오늘·어제 줄 → 시즌 기록 줄 → K6 한 줄
        self.head = self._card("이번 시즌 구단", ("승률 그래프", "점수·예측"))
        nums = QHBoxLayout()
        nums.setSpacing(10)
        self.card_score = StatCard("랭킹 점수")
        self.card_value = StatCard("구단가치 등수")
        self.card_fc = StatCard("시즌 FC")
        self.kpi_rate = StatCard("승률")
        # 승률 카드의 큰 숫자·보조 줄 — 테스트·툴팁이 이 이름으로 읽는다(예전 지표 줄의 승률 카드 자리)
        self.lb_rate, self.lb_rate_delta = self.kpi_rate.value, self.kpi_rate.sub
        # 보조 줄은 줄바꿈 — 식·무승부 수까지 붙은 한 줄이 카드 최소 폭을 밀어 1280 창에서 잘렸다(2026-10-11 실측)
        for c in (self.card_score, self.card_value, self.card_fc, self.kpi_rate):
            c.sub.setWordWrap(True)
            nums.addWidget(c, 1)
        self.head.body.addLayout(nums)
        self.lb_days = _label("", wrap=True)          # 오늘 · 어제
        self.lb_days.setTextFormat(Qt.TextFormat.RichText)
        self.lb_record = _label("", dim=True, wrap=True)   # 시즌 기록 — 최고점 · 챔스·슈챔 달성
        self.head.body.addWidget(self.lb_days)
        self.head.body.addWidget(self.lb_record)
        # 57 K6 — 머리 칸 아래 한 줄. 없을 때도 자리를 지켜 카드 높이가 검색마다 출렁이지 않게
        self.lb_headline = _label("", wrap=True)
        self.lb_headline.setMinimumHeight(self.lb_headline.fontMetrics().height())
        self.head.body.addWidget(self.lb_headline)
        self.head_vals: dict[str, str] = {}   # 칸 제목 → 큰 글자(테스트가 읽는다)
        outer.addWidget(self.head)

        # ① 리포트 넷(65 M1 — 점수·예측 탭 R1~R3 와 같은 재료 · 스쿼드 대비·같은 OVR 대비는 59)
        self.report = self._card("이 구단 리포트", ("승률 그래프", "점수·예측"))
        rep_row = QHBoxLayout()
        rep_row.setSpacing(10)
        self.card_day = StatCard("하루 점수")
        self.card_avg = StatCard("시즌 평균 점수")
        self.card_dd = StatCard("최대 낙폭")
        self.card_recent = StatCard(f"최근 {RECENT_N}경기 승률")
        for c in (self.card_day, self.card_avg, self.card_dd, self.card_recent):
            c.sub.setWordWrap(True)
            rep_row.addWidget(c, 1)
        self.report.body.addLayout(rep_row)
        outer.addWidget(self.report)

        # ② 경기 결과 · 골·실점 · 나와 상대(표시 구간)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.kpi_wdl = self._card("경기 결과", ("전술·경기 결과", "경기 결과"))
        self.wdl_bar = WdlBar()
        self.lb_wdl_total = _label("—", dim=True)
        streak_row = QWidget()
        sh = QHBoxLayout(streak_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        sh.addWidget(_label("현재", dim=True))
        self.lb_streak = _label("—", bold=True)
        self.tag_streak = Tag()   # 숫자 옆 "연승"·"연패" — 숫자는 한 번만
        self.tag_streak.hide()
        sh.addWidget(self.lb_streak)
        sh.addWidget(self.tag_streak, 0, Qt.AlignmentFlag.AlignVCenter)
        sh.addStretch(1)
        self.lb_streak_sub = _label("", dim=True, wrap=True)
        self.lb_forfeit = _label("", dim=True, wrap=True)
        self.lb_forfeit.setTextFormat(Qt.TextFormat.RichText)
        for w in (self.wdl_bar, self.lb_wdl_total, streak_row, self.lb_streak_sub, self.lb_forfeit):
            self.kpi_wdl.body.addWidget(w)

        self.kpi_goals = self._card("골·실점", ("경기 목록", None))
        trio = QHBoxLayout()
        trio.setSpacing(8)
        self.goal_nums: list[QLabel] = []
        for cap, col in (("경기당 골", T.GOOD), ("경기당 실점", T.BAD), ("결정력", T.TEXT)):
            box = QVBoxLayout()
            box.setSpacing(0)
            big = _label("—", pt=18, bold=True)
            big.setStyleSheet(f"color: {col};")
            big.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lb = _label(cap, dim=True)
            lb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(big)
            box.addWidget(lb)
            trio.addLayout(box, 1)
            self.goal_nums.append(big)
        self.lb_goals_sub = _label("", dim=True, wrap=True)
        self.kpi_goals.body.addLayout(trio)
        self.kpi_goals.body.addWidget(self.lb_goals_sub)

        self.versus_card = self._card("나와 상대")
        self.versus_rows = [VersusBar(name) for name, *_ in core.VERSUS_ROWS]
        for vb in self.versus_rows:
            self.versus_card.body.addWidget(vb)
        gap_row = QWidget()
        self.gap_box = QHBoxLayout(gap_row)
        self.gap_box.setContentsMargins(0, 4, 0, 0)
        self.gap_box.setSpacing(6)
        self.lb_gap = _label("벌어진 곳", dim=True)
        self.gap_box.addWidget(self.lb_gap)
        self.gap_tags: list[Tag] = []
        self.gap_box.addStretch(1)
        self.versus_card.body.addWidget(gap_row)

        for c in (self.kpi_wdl, self.kpi_goals):
            c.body.addStretch(1)
        row.addWidget(self.kpi_wdl, 1)
        row.addWidget(self.kpi_goals, 1)
        row.addWidget(self.versus_card, 2)
        outer.addLayout(row)

        # ① 구단주 + ③ 승률 흐름
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(ranker_card, 1, Qt.AlignmentFlag.AlignTop)
        self.trend = self._card("승률 흐름", ("승률 그래프", "승률·등급"))
        # 글자는 점 위 줄로 — 20개 점과 한 줄에 두면 1280 폭에서 자리가 모자란다
        self.trend.body.addWidget(_label(f"최근 {DOTS_N}경기 (왼쪽이 오래된 경기)", dim=True))
        self.dots = ResultDots()
        self.trend.body.addWidget(self.dots)
        self.trend_chart = AreaTrendChart()
        self.trend.body.addWidget(self.trend_chart, 1)
        row.addWidget(self.trend, 2)
        outer.addLayout(row)

        # ③ 점수 해설(65 M1 · P9) + ④ 승부처 · 15분 득실
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        # 눌러서 넘어가지 않는다 — 안의 접기 단추와 카드 누르기가 겹친다
        self.notes = self._card("점수 해설")
        self.note_items: list[tuple[QLabel, QLabel]] = []
        for q in NOTE_QUESTIONS:
            conclusion = _label("", wrap=True)
            conclusion.setTextFormat(Qt.TextFormat.RichText)
            explain = _label("", dim=True, wrap=True)
            fold = Collapsible(q, explain)
            fold.layout().insertWidget(1, conclusion)   # 결론은 늘 보이고 설명만 접는다
            self.notes.body.addWidget(fold)
            self.note_items.append((conclusion, explain))
        self.notes.body.addStretch(1)
        grid.addWidget(self.notes, 0, 0)

        self.clutch = self._card("승부처", ("승부처 분석", "선제골·역전"))
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

        self.minutes = self._card("언제 넣고 먹히나", ("승부처 분석", "선제골·역전"))
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
        self.story = self._card("흐름 분석", ("흐름 분석", None))
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

        self.timeband = self._card("시간대 승률", ("승부처 분석", "선제골·역전"))
        self.timeband_bars = HBarList()
        self.timeband.body.addWidget(self.timeband_bars)
        self.timeband.body.addStretch(1)
        grid.addWidget(self.timeband, 0, 1)

        self.rivals = self._card("자주 만난 상대", ("상대 전적", "전적"))
        self.rival_bars = HBarList()
        self.rivals.body.addWidget(self.rival_bars)
        self.rivals.body.addStretch(1)
        grid.addWidget(self.rivals, 0, 2)
        outer.addLayout(grid)
        outer.addStretch(1)

    def _card(self, title: str, target: tuple[str, str | None] | None = None) -> DashCard:
        c = DashCard(title, target)
        c.clicked.connect(self.navigate)
        self.cards.append(c)
        return c

    # ── 채우기 ──────────────────────────────────────────────────────────
    def render(self, d: DashboardInput) -> None:
        rng = f"표시 구간 {len(d.range_matches):,}경기"
        scope = f"{d.scope_name} {len(d.scope_matches):,}경기"
        for c in (self.kpi_wdl, self.kpi_goals):
            c.scope.setText(rng)
        for c in (self.clutch, self.minutes, self.timeband, self.story, self.rivals):
            c.scope.setText(scope)
        self.trend.scope.setText(f"최근 {d.trend_days}일 · 하루 단위")
        self.head.scope.setText("이번 시즌")
        self.report.scope.setText("이번 시즌 점수 기록")
        self.notes.scope.setText("이번 시즌 점수 기록")

        self._render_head(d)
        self._render_kpis(d)
        self._render_report(d)
        self._render_trend(d)
        self._render_versus(d)
        self._render_clutch(d)
        self._render_minutes(d)
        self._render_story(d)
        self._render_timeband(d)
        self._render_rivals(d)

    def _render_head(self, d: DashboardInput) -> None:
        self.lb_headline.setText(d.headline or "")
        cells = {c[0]: c[1:] for c in (d.club_cells or [])}
        self.head_vals = {t: v[0] for t, v in cells.items()}
        none = ("—", "", "")
        # 숫자 카드 셋 — (카드, 큰 글자 칸, 보조 줄 칸). 랭킹 점수 보조 줄 = "200위까지 +N점"(H2 의 큰 글자)
        cut_title = f"지금 {config.HEAD_TOP_CUT}위까지"
        cut = cells.get(cut_title, none)
        score_sub = f"{config.HEAD_TOP_CUT}위까지 {cut[0]}" if cut[0] not in ("—", "") else cut[1]
        for card, title, sub, tip in (
                (self.card_score, "랭킹 점수", score_sub, cut[2]),
                (self.card_value, "구단가치 등수", None, None),
                (self.card_fc, "시즌 FC", None, None)):
            big, small, ctip = cells.get(title, none)
            card.set(big)
            card.set_sub(small if sub is None else sub)
            card.setToolTip(ctip if tip is None else f"{ctip}\n{tip}".strip())
        # 오늘 · 어제 줄 — 점수 변화는 좋음/나쁨 색(글자 부호가 같이 있다)
        parts = []
        for title in ("오늘", "어제"):
            big, small, _tip = cells.get(title, none)
            col = T.GOOD if big.startswith("+") and big != "+0점" else T.BAD if big.startswith("-") else T.TEXT
            parts.append(f"<span style='color:{T.TEXT_DIM}'>{title}</span> <b style='color:{col}'>{big}</b>"
                         f" <span style='color:{T.TEXT_DIM}'>{small}</span>")
        self.lb_days.setText("&nbsp;&nbsp;│&nbsp;&nbsp;".join(parts))
        self.lb_days.setToolTip("\n".join(cells.get(t, none)[2] for t in ("오늘", "어제")))
        best, reach = cells.get("시즌 최고점", none), cells.get("챔스·슈챔 달성", none)
        self.lb_record.setText(f"시즌 기록 · 최고 {best[0]} {best[1]} · {reach[0]} · {reach[1]}"
                               if d.club_cells else "")
        self.lb_record.setToolTip(f"{best[2]}\n{reach[2]}".strip())

    def _render_kpis(self, d: DashboardInput) -> None:
        # 승률은 시즌 전체가 큰 숫자 — 표시 구간(1~100 등)은 스핀박스에 따라 바뀌는
        # 임의의 묶음이라 기준으로 약하다. 요즘 폼(최근 20경기)은 리포트 카드에.
        season = summarize(d.scope_matches)
        if not season.total:
            self.kpi_rate.set("—")
            self.kpi_rate.set_sub("경기 없음")
            self.lb_rate.setToolTip("")
        else:
            self.kpi_rate.set(f"{season.win_rate:.1f}%")
            # 손으로 승÷전체를 계산해도 왜 다른지 바로 보이게 — 식·무승부 수를 카드에 한 줄(A6 U5)
            self.lb_rate.setToolTip(win_rate_text(season.win, season.draw, season.lose, season.mixed)
                                    + "\n" + win_rate_note(season.mixed))
            formula = "승÷(승+패)" if season.mixed else "승÷전체"
            self.kpi_rate.set_sub(f"{wdl_text(season.win, season.draw, season.lose)} · {formula} · {d.scope_name}")
        recent = summarize(d.scope_matches[:RECENT_N], season.mixed)
        if recent.total and season.total > recent.total:
            self.card_recent.set(f"{recent.win_rate:.1f}%")
            self.card_recent.set_sub(f"{d.scope_name} {season.win_rate:.1f}% 대비")
            self.card_recent.set_delta(recent.win_rate, season.win_rate)
        else:
            self.card_recent.set("—")
            self.card_recent.set_sub(f"{RECENT_N}경기보다 적습니다" if season.total else "경기 없음")
            self.card_recent.set_delta(None, None)

        s = summarize(d.range_matches)
        self.wdl_bar.set_counts(s.win, s.draw, s.lose)
        formula = "승÷(승+패)" if s.mixed else "승÷전체"
        self.lb_wdl_total.setText(f"{s.total:,}경기 · 승률 {s.win_rate:.1f}%({formula})" if s.total else "경기 없음")
        self.kpi_wdl.setToolTip(wdl_text(s.win, s.draw, s.lose))
        kind, n = current_streak(d.range_matches)
        run = kind in ("승", "패") and n
        self.lb_streak.setText(str(n) if kind else "—")
        self.tag_streak.setText(f"연{kind}" if run else kind)
        self.tag_streak.set_kind("good" if kind == "승" and run else "bad" if kind == "패" and run else "muted")
        self.tag_streak.setVisible(bool(kind))
        best_win, best_lose = longest_streaks(d.scope_matches)
        self.lb_streak_sub.setText(f"최장 {best_win}연승 · {best_lose}연패 ({d.scope_name})")
        rb = core.result_breakdown(d.range_details, d.ouid)
        errors = sum(1 for m in d.range_matches if m.result not in ("승", "무", "패"))
        bits = [(f"몰수승 {rb.forfeit[0]}", rb.forfeit[0]), (f"몰수패 {rb.forfeit[2]}", rb.forfeit[2]),
                (f"오류 {errors}", errors)]
        self.lb_forfeit.setText(" · ".join(f"<span style='color:{T.WARN if n else T.TEXT_DIM}'>{t}</span>"
                                           for t, n in bits))

        if s.total:
            shots = sum(m.shoot_total for m in d.range_matches if m.result in ("승", "무", "패"))
            for lb, text in zip(self.goal_nums, (f"{s.avg_goals_for:.2f}", f"{s.avg_goals_against:.2f}",
                                                 f"{s.goals_for / shots * 100:.0f}%" if shots else "—")):
                lb.setText(text)
            self.lb_goals_sub.setText(f"득실차 {s.goals_for - s.goals_against:+,} · 결정력 = 골 ÷ 슛")
        else:
            for lb in self.goal_nums:
                lb.setText("—")
            self.lb_goals_sub.setText("")

    def _render_report(self, d: DashboardInput) -> None:
        """리포트 셋(하루 점수 · 시즌 평균 · 최대 낙폭) + 점수 해설 둘 — 점수 기록이 모자라면 침묵(점수 탭과 같다)."""
        parts = d.report
        cards = (self.card_day, self.card_avg, self.card_dd)
        if parts is None:
            for c in cards:
                c.set("—")
                c.set_sub("점수 기록이 모자랍니다")
                c.set_delta(None, None)
            for conclusion, explain in self.note_items:
                conclusion.setText("점수 기록이 모자라 아직 말할 수 없습니다.")
                explain.setText("")
            return
        rep, band, dr = parts.rep, parts.band, parts.decisive
        up = f"오른 날 {rep.up_days / rep.steps * 100:.0f}%" if rep.steps else ""
        self.card_day.set(f"{rep.day_avg:+,.0f}점")
        self.card_day.set_color(T.GOOD if rep.day_avg > 0 else T.BAD if rep.day_avg < 0 else T.TEXT)
        self.card_day.set_sub(" · ".join(x for x in (f"무승부 뺀 {dr[0]:.1f}%" if dr else "", up) if x))
        self.card_avg.set(f"{rep.avg:,.0f}점")
        self.card_avg.set_sub(f"지금 {rep.now:,.0f}점 ({rep.now - rep.avg:+,.0f})")
        self.card_dd.set(f"−{rep.max_dd:,.0f}점" if rep.max_dd else "하락 없음")
        self.card_dd.set_color(T.BAD if rep.max_dd else T.TEXT)
        self.card_dd.set_sub(f"시즌 최고 {rep.peak:,.0f}점"
                             + (f" · 같은 구간 중앙 −{band[0]:,.0f}점" if band else ""))
        # 해설 — 결론 한 줄(핵심 숫자 색) + 접은 설명
        (c1, e1), (c2, e2) = self.note_items
        if dr is None:
            c1.setText(f"하루 평균 {rep.day_avg:+,.1f}점 — 승·패가 아직 없습니다.")
        else:
            up_ok = dr[0] > 50
            col = T.GOOD if up_ok else T.BAD if dr[0] < 50 else T.TEXT
            word = "오르는 구간" if up_ok else "내리는 구간" if dr[0] < 50 else "제자리"
            c1.setText(f"무승부 뺀 승률 <b style='color:{col}'>{dr[0]:.1f}%</b> — 점수가 {word}입니다.")
        e1.setText(f"점수는 승·패로만 움직여 무승부 뺀 승률 50% 가 오르내림 경계입니다 · 하루 평균 {rep.day_avg:+,.1f}점"
                   + (f" · {up}" if up else "") + (f" · {dr[1]:,}판" if dr else ""))
        if not rep.max_dd or rep.dd_peak is None:
            c2.setText("이번 시즌엔 아직 떨어진 적이 없습니다.")
            e2.setText(f"시즌 최고 {rep.peak:,.0f}점({rep.peak_day:%m.%d})")
        else:
            deep = band is not None and rep.max_dd > band[0]
            judge = (" — 같은 구간 사람들보다 깊은 편입니다" if deep else " — 같은 구간 사람들과 비슷하거나 얕습니다") if band else ""
            c2.setText(f"한 번에 가장 크게 떨어진 폭 <b style='color:{T.BAD}'>{rep.max_dd:,.0f}점</b>{judge}.")
            e2.setText(f"{rep.dd_peak:,.0f}점에서 {rep.dd_peak - rep.max_dd:,.0f}점까지"
                       + (f"({rep.dd_day:%m.%d} 바닥)" if rep.dd_day else "")
                       + (f" · 같은 구간(최고점 ±{config.R2_BAND}) {band[1]:,}명 중앙 −{band[0]:,.0f}점" if band else "")
                       + " · 하루 한 점이라 하루 안 낙폭은 작게 나옵니다")

    def _render_trend(self, d: DashboardInput) -> None:
        recent = [m for m in d.range_matches[:DOTS_N]][::-1]
        self.dots.set_data([(m.result, f"{m.date_text} · {m.opponent} · {m.score} {m.result}")
                            for m in recent])
        w = d.trend_weights
        self.trend_chart.set_data(d.trend_points, avg_weights=w,
                                  weak=[n == 0 for n in w] if w is not None else None)

    def _render_versus(self, d: DashboardInput) -> None:
        prof = core.versus_profile(d.range_details, d.ouid)
        self.versus_card.scope.setText(f"표시 구간 {prof.games:,}판 · 몰수·오류 뺌")
        for vb, ln in zip(self.versus_rows, prof.lines):
            vb.set_values(ln.mine, ln.opp, "{:.0f}" if ln.unit == "%" else "{:.1f}", ln.unit)
            vb.setToolTip(f"{ln.name} — 나 {ln.mine:.1f}{ln.unit} · 상대 {ln.opp:.1f}{ln.unit} ({prof.games:,}경기 평균)")
        for vb in self.versus_rows[len(prof.lines):]:   # 경기 없음 — 줄은 두고 비운다
            vb.nums.setText("—")
            vb.strip.share = 0.5
            vb.strip.update()
        for t in self.gap_tags:
            self.gap_box.removeWidget(t)
            t.deleteLater()
        self.gap_tags = []
        gaps = prof.gaps
        for ln in gaps:
            diff = ln.mine - ln.opp
            t = Tag(f"{ln.name} {diff:+.1f}{'%p' if ln.unit == '%' else ln.unit}", "bad")
            self.gap_box.insertWidget(self.gap_box.count() - 1, t)
            self.gap_tags.append(t)
        self.lb_gap.setText("벌어진 곳" if gaps else "벌어진 곳 없음")

    def _render_clutch(self, d: DashboardInput) -> None:
        cs = core.clutch_summary(d.scope_details, d.ouid)
        scored = sum(cs.first_scored)
        conceded = sum(cs.first_conceded)
        # 첫째·둘째 칸은 승률(A6 식 · 분모 rated) — 둘째의 역전승 = 선제 실점 경기의 승
        note = win_rate_note(cs.mixed)
        for g, wdl, tip in ((self.gauges[0], cs.first_scored,
                             f"선제골 넣은 {scored:,}경기 중 {cs.first_scored[0]:,}승"),
                            (self.gauges[1], cs.first_conceded,
                             f"선제 실점한 {conceded:,}경기 중 {cs.comeback_win:,}번 역전승")):
            n = core.rated(*wdl, cs.mixed)
            if core.is_weak(n, MIN_GAUGE_GAMES):
                g.set_data(None, sample_text(n, cs.mixed),
                           f"{sample_text(n, cs.mixed)} — {MIN_GAUGE_GAMES}{'판' if cs.mixed else '경기'}부터 표시")
            else:
                g.set_data(core.win_pct(*wdl, cs.mixed), f"{n:,}{'판' if cs.mixed else '경기'}",
                           f"{tip}\n{win_rate_text(*wdl, cs.mixed)}\n{note}")
        # 셋째 칸 "지킨 비율" = (승+무) ÷ 선제골 경기 — 승률이 아니라 식·분모 그대로(A6 제외)
        if scored < MIN_GAUGE_GAMES:
            self.gauges[2].set_data(None, f"{scored}경기", f"표본 {scored}경기 — {MIN_GAUGE_GAMES}경기부터 표시")
        else:
            self.gauges[2].set_data(_pct(scored - cs.comeback_lose, scored), f"{scored:,}경기",
                                    f"선제골 넣은 {scored:,}경기 중 {cs.comeback_lose:,}번은 역전패")

    def _render_minutes(self, d: DashboardInput) -> None:
        buckets = core.goal_minute_buckets(d.scope_details, d.ouid)
        labels = [b.label.replace("~", "–") for b in buckets]
        self.minute_chart.set_data(labels, [
            ("득점", [b.scored for b in buckets], T.CHART_UP),
            ("실점", [b.conceded for b in buckets], T.CHART_DOWN)])

    def _render_story(self, d: DashboardInput) -> None:
        found = d.story if d.story is not None else core.narrate(
            d.scope_matches, d.scope_details, d.ouid)
        picks = []
        for sec in core.SECTIONS:
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
        for b in core.time_of_day_rates(d.scope_matches):
            if b.games:
                weak = core.is_weak(b.rated, core.MIN_COND)  # 표본 흐림 — 글자에도 "표본" 을 넣어 색만으로 구분하지 않는다
                tip = (f"{b.label} {b.span} · {wdl_text(b.win, b.draw, b.lose)}"
                       f" · {win_rate_text(b.win, b.draw, b.lose, b.mixed)}")
                pct = win_rate_text(b.win, b.draw, b.lose, b.mixed, digits=0, short=True)
                rows.append((b.label, b.win_rate,
                             f"{pct} · {sample_text(b.rated, b.mixed)}" if weak else f"{pct} · {b.games:,}",
                             f"{tip}\n{sample_note(b.rated, core.MIN_COND, b.mixed)}" if weak else tip, weak))
            else:
                rows.append((b.label, None, "경기 없음", f"{b.label} {b.span}"))
        self.timeband_bars.set_data(rows)

    def _render_rivals(self, d: DashboardInput) -> None:
        rows = []
        # 닉네임이 빈 경기(parse_match 가 "-" 로 둔다)는 한 사람이 아니다 — 실데이터에서
        # "-" 15경기가 1위로 올라왔다. 상대 전적 표에는 그대로 두고 여기서만 뺀다.
        known = [o for o in opponent_stats(d.scope_matches) if o.nickname not in ("", "-")]
        for o in known[:TOP_OPPONENTS]:
            weak = core.is_weak(o.rated, core.MIN_OPP)
            tip = (f"{o.nickname} · {o.games}경기 · 승률 {win_rate_text(o.win, o.draw, o.lose, o.mixed)}"
                   f" · 득실 {o.goals_for}:{o.goals_against}")
            rows.append((o.nickname, o.win_rate,
                         f"{o.win}승 {o.draw}무 {o.lose}패" + (f" · {sample_text(o.rated, o.mixed)}" if weak else ""),
                         f"{tip}\n{sample_note(o.rated, core.MIN_OPP, o.mixed)}" if weak else tip, weak))
        self.rival_bars.set_data(rows)
