"""얼린 사본 — 88단계 P2 직전(2026-11-09)의 widgets.GoalTimeline 기본값 그림(골만 줄 때).

test_goal_timeline_defaults_unchanged 가 새 GoalTimeline 을 **슛을 안 주고** 그린 그림과 이 사본의 그림을
픽셀 단위로 대조한다 — P2 가 슛 점을 더해도, 슛을 안 주는 쪽(지금은 경기 상세 요약뿐이지만 다른 자리가 쓰면)은
그림이 안 바뀌어야 한다. 같은 프로세스 안의 비교라 CI 러너 글꼴과 무관하다(tests/legacy_area_chart.py 와 같은 방식).

widgets·theme 에서 아무것도 import 하지 않는다 — 색 값·글자 크기를 복사해 뒀다(theme 를 고쳐도 사본이 같이 안 바뀌게).
테마만 theme.MODE 대신 호출하는 쪽이 `mode` 로 준다. 이 파일은 고치지 않는다(고치면 대조가 무의미해진다).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

PX_XS = 12
_COLORS = {   # 2026-11-09 의 theme 값 — BORDER · TEXT_DIM · ACCENT_FILL · OPP_FILL(BLUE)
    "dark": ("#313135", "#a1a1aa", "#3fb950", "#5b8cff"),
    "light": ("#d3d7de", "#616875", "#1f9d55", "#3b6fe0"),
}


class LegacyGoalTimeline(QWidget):
    HEIGHT = 52

    def __init__(self, mode: str = "dark"):
        super().__init__()
        self._c = _COLORS[mode]
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
        border, dim, mine_c, opp_c = self._c
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2 - 6
        p.setPen(QPen(QColor(border), 2))
        p.drawLine(QPointF(self.x_of(0), mid), QPointF(self.x_of(self.span), mid))
        f = QFont(self.font())
        f.setPixelSize(PX_XS)
        p.setFont(f)
        p.setPen(QColor(dim))
        for tick in (0, 45, 90, 120):
            if tick > self.span:
                continue
            x = self.x_of(tick)
            p.drawLine(QPointF(x, mid - 4), QPointF(x, mid + 4))
            p.drawText(QRectF(x - 20, mid + 14, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{tick}'")
        p.setPen(Qt.PenStyle.NoPen)
        for minute, mine in self.goals:
            p.setBrush(QColor(mine_c if mine else opp_c))
            p.drawEllipse(QPointF(self.x_of(minute), mid - 9 if mine else mid + 9), 5, 5)
        p.end()
