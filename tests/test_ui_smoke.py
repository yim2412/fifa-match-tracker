"""화면 스모크 — offscreen 으로 메인 창을 띄워 메뉴·페이지 배선을 확인한다.

네트워크 없이 돈다: QThread.start 를 막아 이미지·팀컬러 등 백그라운드 조회가
안 나가게 하고, 픽스처 4경기를 _on_loaded 에 직접 넣는다.
`python tests/test_ui_smoke.py` 로 실행. UI_SHOT=<폴더> 를 주면 메뉴마다
화면을 PNG 로 떠 둔다(눈으로 확인용 — 커밋하지 않는다).
"""
from __future__ import annotations

import contextlib
import faulthandler
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 은 글꼴 폴더를 안 주면 장식체를 집어 글자 폭이 달라지고, 그러면 1280x720
# 잘림 검사 4개가 코드와 무관하게 FAIL 한다(2026-10-02 — 문서의 명령 그대로 치면 26/30).
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtCore import QEvent, QPointF, QRectF, QThread, QTimer, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QThread.start = lambda self, *a, **k: None  # 백그라운드 조회 차단

import app_main  # noqa: E402
import boostcollect  # noqa: E402
import config  # noqa: E402
import core_api as core  # noqa: E402
import crashlog  # noqa: E402
import images  # noqa: E402
import models  # noqa: E402
import nexon_api  # noqa: E402
import notice  # noqa: E402
import playerinfo  # noqa: E402
import rankcollect  # noqa: E402
import rankmeta  # noqa: E402
import ranker  # noqa: E402
import requests  # noqa: E402
import seasons as sn  # noqa: E402
import store  # noqa: E402
import stats as st_mod  # noqa: E402
import teamcolor  # noqa: E402
import traitcollect  # noqa: E402
import updatecheck  # noqa: E402
import theme as T  # noqa: E402

_DIR = os.path.join(_ROOT, "tests", "fixtures")
# offscreen 의 기본 가상 화면은 800x800 이라, 창을 화면에 맞추는 규칙(1.0.3)이 모든 테스트를 '좁은 화면'으로
# 판정해 최대화·안내를 띄운다 → FHD 가상 화면을 기본으로 준다. 작은 화면은 test_window_size.py 가 따로 잰다.
# ⚠ configfile 경로에 한글이 있으면 Qt 가 아무 출력 없이 죽는다(rc=127, 이 PC 는 경로가 다 한글) — 그 폴더로 가서
# 상대 경로로 주고 앱을 만든 뒤 돌아온다.
SCREENS_DIR = os.path.join(_ROOT, "tests", "screens")
if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and QApplication.instance() is None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=fhd100.json"
    _cwd = os.getcwd()
    os.chdir(SCREENS_DIR)
    try:
        _app = QApplication(sys.argv)
    finally:
        os.chdir(_cwd)
_app = QApplication.instance() or QApplication(sys.argv)
T.apply(_app)


# ── 모달 차단 — offscreen 에선 모달이 안 닫혀 테스트가 실패 대신 영원히 멈춘다 ──────
# 2026-10-02 하루에 세 번 겪었다(안내 창·키 창·app.exec()). 매번 그 자리만 가로챘는데,
# 회귀가 생기면 빨개지는 대신 멈추니 무엇이 깨졌는지도 안 보였다. 그래서 전부 막고
# "부르면 즉시 실패"로 바꾼다. 창이 떠야 정상인 테스트는 지금처럼 그 함수를 직접 가로챈다.
class ModalCalled(AssertionError):
    pass


def _no_modal(name):
    def blocked(*a, **k):
        raise ModalCalled(f"테스트 중 모달 호출: {name} — 가로채지 않으면 offscreen 에서 멈춘다")
    return blocked


from PySide6.QtWidgets import QDialog, QMessageBox, QTabWidget  # noqa: E402

for _n in ("warning", "information", "critical", "question"):
    setattr(QMessageBox, _n, staticmethod(_no_modal(f"QMessageBox.{_n}")))
QDialog.exec = _no_modal("QDialog.exec")
QApplication.exec = _no_modal("QApplication.exec")


def _load():
    man = json.load(open(os.path.join(_DIR, "manifest.json"), encoding="utf-8"))
    details = [json.load(open(os.path.join(_DIR, m + ".json"), encoding="utf-8"))
               for m in man["match_ids"]]
    ouid = man["ouid"]
    matches = [m for m in (models.parse_match(d, ouid) for d in details) if m]
    # MatchLoader 와 똑같이 최신순 — 화면 코드는 전부 이 순서를 전제한다
    matches.sort(key=lambda m: m.match_date or 0, reverse=True)
    return ouid, matches, details


class _NoApi:
    def __getattr__(self, name):
        raise AssertionError(f"스모크에서 API 를 불렀다: {name}")


# 창이 켤 때 읽고 닫을 때 쓰는 settings.ini — 실제 파일을 건드리지 않게(지난번 창 크기가 테스트에 섞인다)
config.SETTINGS_PATH = pathlib.Path(tempfile.mkdtemp()) / "settings.ini"
# DB 도 임시로 — 창이 켤 때 시즌표·최근 검색을 DB 에서 읽는다. 실제 DB 를 쓰면 이 PC 와 CI 결과가 갈린다
# (2026-10-02 CI 첫 실행: 시즌표가 없는 CI 에서만 시즌 테스트 2개가 실패). 픽스처 4경기(2026-01-01~03)가
# 통째로 들어가는 끝난 시즌 하나 — 진행 중 시즌이 없어 기본은 '전체'다(이 PC 실제 DB 일 때와 같다).
config.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "ui.db"
# 랭킹 수집(rank.db)·스위치(.env)도 임시로 — 팀컬러 목록 읽기가 스냅숏을 찾고 .env 를 다시 읽는다(1.1.1).
# 실제 .env 를 읽으면 config.WEB_DATA 가 이 PC 값으로 바뀐다.
config.RANK_DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "rank.db"
config.ENV_PATH = config.RANK_DB_PATH.with_name(".env")
# 메타 처리(2.3.1)가 낡은 시즌표를 넥슨에서 다시 받는다 — 테스트는 네트워크 없이(캐시 그대로)
import rankcollect as _rc  # noqa: E402
import seasons as _seasons  # noqa: E402


def _no_season_fetch():
    raise _seasons.SeasonError("테스트 — 네트워크 없음")


_rc._fetch_seasons = _no_season_fetch
# 한 번만 실행(tray.SingleInstance) — main() 을 부르는 테스트가 이 PC 에 떠 있는 앱을 앞으로 부르거나 그 때문에 끝나지 않게
config.SINGLE_INSTANCE = False
# 숫자 카드 올라가기(86 C3) — 끄지 않으면 채운 직후 읽는 단언이 도는 중의 글자를 본다. 켠 채는 test_statcard_counts_up_86
config.UI_ANIMATE = False
_seed =store.open_db(config.DB_PATH)
try:
    from datetime import date as _date
    store.save_seasons(_seed, [sn.Season(no=90, name="시즌 9", start=_date(2025, 12, 1),
                                         end=_date(2026, 2, 1))])
finally:
    _seed.close()
class _FakeCardInfoLoader:
    """축구장 칩 카드 정보 로더 자리 — 스모크가 넥슨 홈페이지에 요청하지 않게(동의·웹 데이터는 테스트마다 바뀐다).
    띄운 기록만 남긴다. 실제 로더의 동작(쓰임별 계수·상한)은 test_trades 가 잰다."""
    started: list = []

    def __init__(self, spids):
        self.spids = list(spids)
        self.card = _Sig()
        self.done = _Sig()

    def start(self):
        _FakeCardInfoLoader.started.append(self.spids)

    def isRunning(self):
        return False

    def cancel(self):
        pass

    def wait(self, *_a):
        return True


class _Sig:
    def __init__(self):
        self.slots = []

    def connect(self, f):
        self.slots.append(f)

    def emit(self, *a):
        for f in self.slots:
            f(*a)


_RealCardInfoLoader = app_main.CardInfoLoader
app_main.CardInfoLoader = _FakeCardInfoLoader
# 84 G3 — 테스트는 이 PC 의 실제 프로세스 목록을 보지 않는다(main 을 부르는 테스트가 감시를 켠다 · 게임을 켜 둔 날
# 뒤 테스트가 전부 "게임 중" 으로 흔들린다). 게임 켜짐은 _GameOn 으로만
app_main.GAME_CHECK = lambda: False
_win = app_main.MainWindow(_NoApi())
_win.resize(1600, 900)
# 대부분의 테스트는 "모든 메뉴가 그려진 상태"를 본다 — 지연 그리기 자체는 test_lazy_* 가 켜서 잰다
_win.LAZY_RENDER = False
_win._on_fetch_team_colors = lambda: None
_OUID, _MATCHES, _DETAILS = _load()
_win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
_win.show()
# 다른 테스트가 메뉴를 옮기기 전, 로드 직후의 상태를 잡아 둔다.
_AFTER_LOAD = (_win.stack.currentIndex(), _win.pages.currentIndex())


@contextlib.contextmanager
def _range(kind: str, n: int):
    """빠른 범위(76)를 버튼에 없는 값으로 — 픽스처가 4경기라 100·200·500 으로는 안 좁혀진다.
    _on_range_changed 를 거쳐 실제 화면 배선(RANGE_KEYS 다시 그리기)을 그대로 탄다."""
    _win._range_spec = lambda: (kind, n)
    _win._on_range_changed()
    try:
        yield
    finally:
        del _win._range_spec
        _win._on_range_changed()


def _nav_items():
    return [(_win.nav.item(r), _win.nav.item(r).data(Qt.ItemDataRole.UserRole))
            for r in range(_win.nav.count())]


def test_modal_calls_fail_fast():
    # 막지 않았으면 이 호출들은 offscreen 에서 돌아오지 않는다
    for call in (lambda: QMessageBox.information(None, "t", "t"),
                 lambda: QMessageBox.warning(None, "t", "t"),
                 lambda: QDialog().exec(),
                 lambda: app_main.ApiKeyDialog().exec(),   # 하위 클래스도 막힌다
                 lambda: _app.exec()):
        try:
            call()
            raise AssertionError("모달이 막히지 않았다")
        except ModalCalled:
            pass


def _stray_windows_while(build) -> list[str]:
    """build() 동안 최상위 창으로 보이게 된 위젯(돌려준 창 자신은 빼고) — 켤 때 작은 창이 깜빡이던 원인을 잰다."""
    from PySide6.QtCore import QObject

    class _Watch(QObject):
        def __init__(self):
            super().__init__()
            self.shown = []

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.Show and getattr(obj, "isWindow", lambda: False)():
                self.shown.append(obj)
            return False

    w = _Watch()
    _app.installEventFilter(w)
    try:
        made = build()
    finally:
        _app.removeEventFilter(w)
    return [f"{type(o).__name__} {o.objectName()!r}" for o in w.shown if o is not made]


def test_main_window_build_shows_no_stray_windows():
    from PySide6.QtWidgets import QLabel
    # 재는 장치부터: 레이아웃에 넣기 전에 보이면 부모 없는 위젯이 창으로 잡혀야 한다(안 잡히면 아래 검사가 빈 것)
    lone = QLabel("t")
    assert _stray_windows_while(lambda: lone.setVisible(True)), "부모 없는 QLabel 을 창으로 못 잡았다"
    lone.hide()
    lone.deleteLater()
    # 2.13.3 — Card 제목(widgets.Card)·WrapBar 구분선이 addWidget 전에 보여 켤 때 작은 창이 4번 깜빡였다
    made = []
    stray = _stray_windows_while(lambda: made.append(app_main.MainWindow(_NoApi())) or made[0])
    for w in made:
        w.close()
        w.deleteLater()
    assert not stray, f"메인 창을 만드는 동안 따로 뜬 창 {len(stray)}개: {stray[:5]}"


def test_loaded_opens_dashboard():
    assert _AFTER_LOAD == (_win.PAGE_MAIN, _win._page_index["대시보드"]), _AFTER_LOAD
    # 픽스처: 승1 무1 패1 + 오류1 — 대시보드 지표가 실제로 채워졌는지.
    # 승률은 무승부 포함 승÷전체(2026-10-19 사용자 · config.WIN_RATE_DRAWS_IN) = 1/3. A6 섞임 식이었다면 50.0%
    assert _win.dashboard.lb_rate.text() == "33.3%", _win.dashboard.lb_rate.text()
    assert "승÷전체" in _win.dashboard.lb_rate.toolTip(), _win.dashboard.lb_rate.toolTip()
    assert "승÷전체" in _win.dashboard.lb_rate_delta.text(), _win.dashboard.lb_rate_delta.text()
    assert _win.lb_profile.text() == "테스트구단주", _win.lb_profile.text()


def test_every_menu_opens_its_own_page():
    pages = [idx for _, idx in _nav_items() if idx is not None]
    # 메뉴마다 페이지가 하나씩, 겹치지 않게
    assert sorted(pages) == list(range(_win.pages.count())), pages
    for item, idx in _nav_items():
        if idx is None:
            assert not (item.flags() & Qt.ItemFlag.ItemIsSelectable), item.text()
            continue
        _win.nav.setCurrentItem(item)
        assert _win.pages.currentIndex() == idx, (item.text(), _win.pages.currentIndex())
        title = _win.pages.currentWidget().findChild(type(_win.lb_profile), "cardTitle")
        if item.text() != "대시보드":
            assert title is not None and title.text() == item.text(), item.text()
    _win._go_page("승률 그래프")
    assert _win.pages.currentIndex() == _win._page_index["승률 그래프"]


def test_trend_page_has_own_summary():
    # 예전엔 상단 카드를 바꿔치기했다 — 이제 대시보드 카드는 그대로 전적이어야 한다.
    _win._go_page("기간별 성적")
    assert _win.card_period_games.value.text().endswith("경기"), \
        _win.card_period_games.value.text()
    assert _win.dashboard.kpi_rate.cap.text() == "승률", _win.dashboard.kpi_rate.cap.text()


def test_dashboard_65_layout():
    """65 M1 — 머리 숫자 카드(제목으로 칸을 찾는다) · 오늘/어제 줄 · 나와 상대 일곱 줄 + 벌어진 곳 · 리포트·해설(재료 없으면 침묵)."""
    import dashboard
    from habits import ScoreReport
    from datetime import date as _d
    d = _win.dashboard
    cells = [("어제", "+12점", "2승 0무 1패 · 66.7% · 30 FC", "t어제"), ("지금 200위까지", "+100.00점", "작은", "t컷"),
             ("구단가치 등수", "3,300등", "10,000명 중", "t가치"), ("시즌 최고점", "4,549.30", "#8 (10.18)", "t최고"),
             ("챔스·슈챔 달성", "챔스 1,042판", "남들 중앙", "t챔스"), ("시즌 FC", "26,015 FC", "1,057등", "tFC"),
             ("오늘", "-143점", "36승 8무 25패", "t오늘"), ("랭킹 점수", "4,103.09", "10/26 기록", "t점수")]
    ms, ds = list(_win._matches), list(_win._details)
    inp = dashboard.DashboardInput(_win._ouid, ms, ds, ms, ds, "시험", club_cells=cells)
    try:
        d.render(inp)
        # 순서가 아니라 제목으로 — 칸 순서를 섞어도 같은 카드에 간다
        assert (d.card_score.value.text(), d.card_score.sub.text()) == ("4,103.09", "200위까지 +100.00점"), \
            (d.card_score.value.text(), d.card_score.sub.text())
        assert d.card_value.value.text() == "3,300등" and d.card_fc.sub.text() == "1,057등"
        assert d.kpi_rate.sub.text().endswith(" · 시험"), ("승률 카드가 어느 범위인지 안 적었다", d.kpi_rate.sub.text())
        d.render(replace(inp, club_cells=cells[::-1]))
        assert d.card_value.value.text() == "3,300등" and d.head_vals["지금 200위까지"] == "+100.00점"
        days = d.lb_days.text()
        assert days.index("오늘") < days.index("어제") and f"color:{T.BAD}'>-143점" in days \
            and f"color:{T.GOOD}'>+12점" in days, days
        assert "4,549.30" in d.lb_record.text() and "챔스 1,042판" in d.lb_record.text()
        # 나와 상대 — 픽스처 손계산 값(test_parsing.test_versus_profile) · 태클만 벌어진 곳
        prof = core.versus_profile(ds, _win._ouid)
        assert [vb.name.text() for vb in d.versus_rows] == [ln.name for ln in prof.lines]
        assert [t.text() for t in d.gap_tags] == ["태클 성공률 -3.4%p"], [t.text() for t in d.gap_tags]
        assert "3판" in d.versus_card.scope.text() and "몰수" not in d.versus_card.scope.text(), d.versus_card.scope.text()
        drib = d.versus_rows[5]
        assert drib.mine_ahead and f"color:{T.ACCENT}" in drib.nums.text(), drib.nums.text()   # 102.7 vs 78.0
        assert abs(drib.strip.share - 102.67 / (102.67 + 78.0)) < 0.001
        tackle = d.versus_rows[6]
        assert not tackle.mine_ahead and f"color:{T.ACCENT}" not in tackle.nums.text()
        # 리포트 — 재료 없으면 "—" + 해설은 말하지 않는다
        assert d.card_day.value.text() == "—" and "모자라" in d.note_items[0][0].text()
        rep = ScoreReport(10, _d(2026, 9, 1), 4000.0, 4100.0, 4500.0, _d(2026, 9, 5), 300.0, _d(2026, 9, 3),
                          12.4, 6, 9, 4300.0)
        d.render(replace(inp, report=dashboard.ScoreParts(rep, (200.0, 30), (53.2, 400))))
        assert d.card_day.value.text() == "+12점" and "무승부 뺀 53.2%" in d.card_day.sub.text()
        assert d.card_avg.value.text() == "4,000점" and "(+100)" in d.card_avg.sub.text()
        assert d.card_dd.value.text() == "−300점" and "같은 구간 중앙 −200점" in d.card_dd.sub.text()
        assert "오르는 구간" in d.note_items[0][0].text() and "깊은 편" in d.note_items[1][0].text()
        d.render(replace(inp, report=dashboard.ScoreParts(rep, (400.0, 30), (47.0, 400))))
        assert "내리는 구간" in d.note_items[0][0].text() and "깊은 편" not in d.note_items[1][0].text()
        # 경기 결과 카드 — 픽스처 0004 는 내 쪽 오류 + 상대 몰수승 = 몰수패 1 · 오류 1
        assert "몰수패 1" in d.lb_forfeit.text() and "오류 1" in d.lb_forfeit.text(), d.lb_forfeit.text()
        assert f"color:{T.WARN}'>몰수패 1" in d.lb_forfeit.text() and f"color:{T.TEXT_DIM}'>몰수승 0" in d.lb_forfeit.text()
    finally:
        _win._render_dashboard()


class _CountRenders:
    """창의 그리기 함수 몇 개를 세는 함수로 바꿔 끼운다 — 몇 번 그렸는지."""

    NAMES = ("_render_dashboard", "_render_opponents", "_render_diagnosis", "_render_teamcolor_tabs",
             "_render_players", "_render_ranker_compare")

    def __enter__(self):
        self.n = {k: 0 for k in self.NAMES}
        for k in self.NAMES:
            real = getattr(_win, k)

            def spy(*a, _k=k, _real=real, **kw):
                self.n[_k] += 1
                return _real(*a, **kw)
            setattr(_win, k, spy)
        self._saved = (_win.LAZY_RENDER, _win.pages.currentIndex())
        _win.LAZY_RENDER = True
        return self

    def __exit__(self, *exc):
        for k in self.NAMES:
            delattr(_win, k)  # 인스턴스 속성을 지워 클래스 메서드로 돌아간다
        _win.LAZY_RENDER = self._saved[0]
        _win._go_page("대시보드")
        _win._render_all()


def test_lazy_render_draws_only_the_visible_page():
    with _CountRenders() as c:
        _win._go_page("대시보드")
        _win._render_all()
        assert c.n["_render_dashboard"] == 1 and c.n["_render_opponents"] == 0, c.n
        assert c.n["_render_diagnosis"] == 0 and c.n["_render_teamcolor_tabs"] == 0, c.n
        _win._go_page("상대 전적")                 # 열면 그 때 그린다
        assert c.n["_render_opponents"] == 1, c.n
        _win._go_page("대시보드")
        _win._go_page("상대 전적")                 # 이미 그렸으면 다시 안 그린다
        assert c.n["_render_opponents"] == 1 and c.n["_render_dashboard"] == 1, c.n
        _win._render_all()                          # 시즌 전환·새 경기 — 보이는 것만 다시
        assert c.n["_render_opponents"] == 2 and c.n["_render_dashboard"] == 1, c.n
        _win._go_page("대시보드")                  # 낡은 채로 남아 있다가 열 때
        assert c.n["_render_dashboard"] == 2, c.n
        _win._go_page("상대 팀컬러")               # 같은 그리기를 쓰는 메뉴는 한 번이면 같이 깨끗
        _win._go_page("포지션별 최다 상대")
        assert c.n["_render_teamcolor_tabs"] == 1, c.n
        _win._go_page("대시보드")
        _win._invalidate("teamcolor")              # 안 보이면 미뤘다가
        assert c.n["_render_teamcolor_tabs"] == 1, c.n
        _win._go_page("상대 팀컬러")
        assert c.n["_render_teamcolor_tabs"] == 2, c.n
        _win._invalidate("teamcolor")              # 보이면 바로
        assert c.n["_render_teamcolor_tabs"] == 3, c.n


def _views_of_nav(nav) -> list[tuple]:
    """NAV → 모든 (메뉴, 탭 또는 None) 자리."""
    out = []
    for _, items in nav:
        for name, b in items:
            if isinstance(b, app_main.Tabs):
                out += [(name, t) for t, _b in b.tabs]
            else:
                out.append((name, None))
    return out


def _render_coverage_problems(nav, view_of_key: dict, exempt: dict, renderer_keys) -> list[str]:
    """자리 ↔ 그리기 표 대조 — 표에 안 넣은 자리(메뉴·탭)는 예외 없이 조용히 안 그려진다(CLAUDE.md PyQt 7)."""
    views = _views_of_nav(nav)
    keyed = [v for vs in view_of_key.values() for v in vs]
    out = [f"{v}: VIEW_OF_KEY 에도 VIEW_EXEMPT 에도 없다" for v in views if v not in keyed and v not in exempt]
    out += [f"{v}: 두 표에 다 있다" for v in views if v in keyed and v in exempt]
    out += [f"{v}: 메뉴에 없는 자리" for v in [*keyed, *exempt] if v not in views]
    out += [f"{v}: 키 둘에 있다" for v in set(keyed) if keyed.count(v) > 1]
    out += [f"키 {k!r} 의 그리기가 _renderers() 에 없다" for k in view_of_key if k not in renderer_keys]
    out += [f"그리기 {k!r} 를 쓰는 자리가 없다" for k in renderer_keys if k not in view_of_key]
    return out


def test_every_nav_page_has_a_renderer():
    W = app_main.MainWindow
    # 심은 위반부터 — 새 메뉴·새 탭을 NAV 에만 넣은 경우를 잡지 못하면 아래 단언은 빈 검사다
    planted = [*W.NAV, ("새 묶음", [("새 메뉴", "_build_x"),
                                  ("탭 메뉴", app_main.Tabs("x", (("새 탭", "_build_x"),)))])]
    probs = _render_coverage_problems(planted, W.VIEW_OF_KEY, W.VIEW_EXEMPT, _win._renderers())
    assert any("새 메뉴" in p for p in probs) and any("새 탭" in p for p in probs), "심은 새 메뉴·탭을 못 잡음"
    moved = {**W.VIEW_OF_KEY, "players": [("선수 지표", "없는 탭")]}  # 탭 이름을 바꾸고 표를 안 고친 경우
    assert any("없는 탭" in p for p in _render_coverage_problems(W.NAV, moved, W.VIEW_EXEMPT, _win._renderers()))
    probs = _render_coverage_problems(W.NAV, W.VIEW_OF_KEY, W.VIEW_EXEMPT, _win._renderers())
    assert not probs, probs
    assert W.KEY_OF_VIEW == {v: k for k, vs in W.VIEW_OF_KEY.items() for v in vs}


def test_tab_switch_renders_dirty_only():
    """E1·E2·E6 — 메뉴·탭을 열 때 그 자리의 키만, 낡았을 때만. 한 함수가 탭 여럿을 채우는 키는 한 번이면 같이 깨끗."""
    with _CountRenders() as c:
        tabs = _win._page_tabs["선수 지표"]
        _win._go_page("대시보드")
        _win._render_all()
        _win._go_page("선수 지표")                        # 첫 탭 [지표] 만
        assert c.n["_render_players"] == 1 and c.n["_render_ranker_compare"] == 0, c.n
        tabs.set_current("랭커 비교")                      # 탭 클릭 → 그 탭의 키
        assert c.n["_render_ranker_compare"] == 1 and c.n["_render_players"] == 1, c.n
        tabs.set_current("지표")                          # 깨끗하면 다시 안 그린다
        assert c.n["_render_players"] == 1, c.n
        _win._go_page("성적 진단", "규율·불운")
        assert c.n["_render_diagnosis"] == 1, c.n
        _win._page_tabs["성적 진단"].set_current("상대·점유율")  # 같은 키 — 이미 그렸다
        assert c.n["_render_diagnosis"] == 1, c.n
        _win._invalidate("diagnosis")                      # 보이는 자리면 바로(E6)
        assert c.n["_render_diagnosis"] == 2, c.n
        _win._go_page("대시보드")
        _win._invalidate("diagnosis")                      # 안 보이면 미뤘다가
        assert c.n["_render_diagnosis"] == 2, c.n
        _win._go_page("성적 진단")
        assert c.n["_render_diagnosis"] == 3, c.n
        _win._go_page("대시보드")
        _win._render_all()
        tabs.set_current("랭커 비교")                      # 안 보이는 메뉴의 탭이 바뀌어도 안 그린다
        assert c.n["_render_ranker_compare"] == 1 and c.n["_render_players"] == 1, c.n
        tabs.set_current("지표")


def test_trades_tab_open_starts_loader():
    """E3 — 가계부를 열면(메뉴로든 탭으로든) 상태를 다시 읽고 거래 받기를 띄운다."""
    calls = []
    _win.start_trades = lambda *a, **k: calls.append(1)
    tabs = _win._page_tabs["스쿼드·이적"]
    try:
        _win._go_page("대시보드")
        _win._go_page("스쿼드·이적", "가계부")          # 메뉴로(대시보드 → 가계부)
        assert calls == [1], calls
        tabs.set_current("타임라인")
        assert calls == [1], calls
        tabs.set_current("가계부")                       # 탭으로
        assert calls == [1, 1], calls
        _win._go_page("대시보드")
        _win.nav.setCurrentRow(next(r for r in range(_win.nav.count())
                                    if _win.nav.item(r).text() == "스쿼드·이적"))  # 기억된 탭(가계부)으로 메뉴 클릭
        assert calls == [1, 1, 1], calls
        _win._go_page("대시보드")
        tabs.set_current("타임라인")
        tabs.set_current("가계부")                       # 안 보이는 메뉴의 탭 — 거래 받기를 띄우지 않는다
        assert calls == [1, 1, 1], calls
        _win._go_page("스쿼드·이적", "가계부")
        assert calls == [1, 1, 1, 1], calls
        _win._page_tabs["선수 지표"].set_current("랭커 비교")  # 가계부를 보는 중에 다른 메뉴의 탭이 바뀌어도
        _win._page_tabs["선수 지표"].set_current("지표")
        assert calls == [1, 1, 1, 1], calls
    finally:
        del _win.start_trades
        tabs.set_current("타임라인", emit=False)
        _win._go_page("대시보드")


def test_go_page_rejects_unknown_or_hidden():
    """E4 — 없는 이름·숨긴 메뉴·없는 탭이면 아무것도 안 한다(예전엔 묶음 제목 줄로 갔다)."""
    _win._go_page("슛 맵")
    keep = config.HIDDEN_NAV_UNTIL_READY
    config.HIDDEN_NAV_UNTIL_READY = ("랭킹 추이",)  # 지금 숨긴 메뉴가 없어 하나를 숨긴 셈 치고 잰다(판정은 부를 때 config 를 본다)
    try:
        for args in (("없는 메뉴",), ("랭킹 추이",), ("선수 지표", "없는 탭"), ("슛 맵", "탭"), ("랭커와 비교",)):
            _win._go_page(*args)
            assert _win._current_view() == ("슛 맵", "슛 맵"), (args, _win._current_view())
    finally:
        config.HIDDEN_NAV_UNTIL_READY = keep
    _win._go_page("랭킹 추이")
    assert _win._current_view() == ("랭킹 추이", "추이"), "숨김을 풀면 열린다(탭 없이 부르면 첫 탭)"
    assert config.HIDDEN_NAV_UNTIL_READY == (), "2.1.1 공개판엔 숨긴 메뉴가 없다(release.py 도 막는다)"
    shown = [_win.nav.item(r) for r in range(_win.nav.count())
             if _win.nav.item(r).text() in ("랭커", "랭킹 추이", "랭커 픽", "선수로 구단주 찾기")]
    assert len(shown) == 4 and not any(it.isHidden() for it in shown), "17단계로 랭커 묶음이 다 보인다"
    _win._go_page("대시보드")


def test_page_tabs_widget():
    """PageTabs — 지금 탭만 보인다 · 바뀔 때만 신호 · 없는 탭은 거절 · 숨은 탭의 넓은 내용이 최소 폭을 안 넓힌다 ·
    좁으면 탭 글자를 줄이지 않고 탭줄이 접힌다."""
    import widgets
    from PySide6.QtWidgets import QLabel
    t = widgets.PageTabs()
    pages = [QLabel("b"), QLabel("넓은 내용" * 60), QLabel("c")]
    for i, p in enumerate(pages):
        t.add_tab(f"탭 이름 {i}", p)
    got = []
    t.changed.connect(got.append)
    t.resize(700, 200)
    t.show()
    try:
        _app.processEvents()
        assert [p.isVisible() for p in pages] == [True, False, False] and got == []
        assert t.set_current("탭 이름 2") and got == [2], got
        assert [p.isVisible() for p in pages] == [False, False, True]
        assert [b.isChecked() for b in t._btns] == [False, False, True]
        assert t.set_current(2) and got == [2], "같은 탭인데 신호를 냈다"
        assert not t.set_current("없음") and not t.set_current(5) and t.current_name() == "탭 이름 2"
        wide = pages[1].sizeHint().width()
        assert t.minimumSizeHint().width() < wide, ("숨은 탭이 최소 폭을 넓혔다", t.minimumSizeHint(), wide)
        assert t.bar.row_count() == 1, t.bar.row_count()
        t.resize(t.bar.minimumSizeHint().width(), 200)
        _app.processEvents()
        assert t.bar.row_count() == 3, t.bar.row_count()
        assert all(b.width() >= b.sizeHint().width() for b in t._btns), "좁아졌다고 탭 글자를 줄였다"
        t.resize(700, 200)
        _app.processEvents()
        assert t.bar.row_count() == 1, t.bar.row_count()
    finally:
        t.close()


def test_go_page_literals_in_tests_exist():
    """테스트의 _go_page("…") 이름이 틀리면 조용히 안 움직여 엉뚱한 화면을 잰다 — 소스에서 대조한다."""
    import re
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    views = _views_of_nav(app_main.MainWindow.NAV)
    menus = {m for m, _t in views}
    calls = re.findall(r'_win\._go_page\("([^"]+)"(?:, "([^"]+)")?\)', src)
    assert len(calls) > 30, len(calls)

    def bad(cs):
        return [(m, t) for m, t in cs if not (m in menus and (not t or (m, t) in views))]
    planted = [("랭커와 비교", ""), ("선수 지표", "없는 탭"), ("슛 맵", "탭")]
    assert bad(planted) == planted, ("심은 틀린 이름을 못 잡음", bad(planted))
    assert not bad(calls), bad(calls)


def test_narrate_once_per_scope_and_recomputed_when_scope_changes():
    calls = []
    real = app_main.core.narrate

    def spy(m, d, ouid, *a, **k):
        calls.append(len(m))
        return real(m, d, ouid, *a, **k)

    saved = _win._matches, _win._details
    app_main.core.narrate = spy
    try:
        _win._narrate_key = None
        _win._render_all()   # 대시보드 + 흐름 분석 메뉴 — 다 그려도 한 번
        assert calls == [len(_win._matches)], calls
        _win._render_all()   # 범위가 같으면 다시 안 한다
        assert calls == [len(_win._matches)], calls
        # 범위를 바꾸면 반드시 다시 — 캐시가 낡은 결과를 보여 주면 안 된다
        _win._matches, _win._details = _win._matches[1:], _win._details[1:]
        _win._render_all()
        assert calls == [len(saved[0]), len(saved[0]) - 1], calls
    finally:
        app_main.core.narrate = real
        _win._matches, _win._details = saved[0], saved[1]
        _win._narrate_key = None
        _win._render_all()


def test_setfont_sizes_survive_stylesheet():
    # QSS 의 QWidget 규칙에 font-size 가 있으면 코드의 setFont 가 전부 눌린다
    # (다크 테마 시절 실제로 그랬다 — 30pt 제목이 15px 로 보였다).
    from PySide6.QtWidgets import QLabel
    title = [lb for lb in _win.stack.widget(_win.PAGE_SEARCH).findChildren(QLabel)
             if lb.objectName() == "searchTitle"]
    assert title, "검색 화면 제목을 못 찾음"
    f = title[0].font()
    assert f.pointSize() == T.PT_TITLE, (f.pointSize(), f.pixelSize())


def test_digits_are_tabular_86():
    # 86 D1 — 표·큰 숫자가 자릿수대로 줄 서려면 0~9 폭이 같아야 한다. 맑은 고딕은 이미 그렇다(11-09 실측 16px 전부 9px)
    # 그래서 글꼴을 더하지 않았다 — FONT_FAMILY 를 바꿔 이게 깨지면 그때 tnum 이 되는 글꼴을 고른다
    from PySide6.QtGui import QFont, QFontMetricsF
    for bold in (False, True):
        f = QFont(T.FONT_FAMILY)
        f.setPixelSize(T.BASE_FONT_PX)
        f.setBold(bold)
        fm = QFontMetricsF(f)
        widths = {round(fm.horizontalAdvance(d), 2) for d in "0123456789"}
        assert len(widths) == 1, (bold, widths)


def test_pills_stay_round_86():
    # 86 A2 — QSS 는 모서리가 높이 절반을 넘으면 그 값을 버리고 각지게 그린다. 칩 높이가 고정되고 모서리가 그 절반 이하인지
    import re as _re
    from widgets import Segmented, Tag, chip_toggle

    def radius(w, sel=""):
        m = _re.findall(r"border-radius: (\d+)px", w.styleSheet())
        return int(m[0]) if m else None
    t = Tag("연승", "good")
    c = chip_toggle("비주류")
    s = Segmented(["일별", "시간별"])
    for w in (t, c, s, s.buttons[0]):
        h = w.height() if w.minimumHeight() == w.maximumHeight() else None
        assert h is not None, f"{type(w).__name__} 높이가 고정되지 않았다"
    for w, r in ((t, radius(t)), (c, radius(c)), (s, radius(s))):
        assert r is not None and r * 2 <= w.maximumHeight() and r >= w.maximumHeight() // 2, (type(w).__name__, r, w.maximumHeight())
    t.set_kind("bad")   # 색을 바꿔도 알약 그대로
    assert radius(t) == t.maximumHeight() // 2, radius(t)
    assert T.RADIUS_CARD > T.RADIUS_CTRL, "카드 안 부품 모서리가 카드보다 크면 겹친 모서리가 어색하다(86 A1)"


def test_statcard_counts_up_86():
    # 86 C3 — 켜면 도는 중엔 중간 값, 끝나면 받은 글자 그대로(쉼표·소수·단위·부호). 숫자 없는 글·끔·안 보임은 바로
    import time as _time
    from PySide6.QtWidgets import QApplication
    from widgets import StatCard, count_number, count_text
    n = count_number("+1,234.5점")
    assert n is not None and n[1] == 1234.5 and count_text("+1,234.5점", n, 7.25) == "+7.2점", count_text("+1,234.5점", n, 7.25)
    assert count_number("—") is None
    card = StatCard("시험")
    card.show()
    old = config.UI_ANIMATE
    try:
        config.UI_ANIMATE = False
        card.set("1,000점")
        assert card.value.text() == "1,000점", "끈 상태는 바로 써야 한다"
        config.UI_ANIMATE = True
        card.set("3,210점")
        mid = card.value.text()
        assert mid != "3,210점" and card._anim is not None, f"켰는데 바로 썼다: {mid}"   # 막지 않았으면 이 줄이 빨개진다
        assert card.value.minimumWidth() > 0, "도는 동안 폭을 안 잡았다"
        end = _time.monotonic() + 3
        while card._anim is not None and _time.monotonic() < end:
            QApplication.processEvents()
            _time.sleep(0.01)
        assert card.value.text() == "3,210점" and card.value.minimumWidth() == 0, (card.value.text(), card.value.minimumWidth())
        card.set("—")
        assert card.value.text() == "—"
        card.set("52.3%")
        card.set("61.0%")   # 돌던 것을 멈추고 새로 — 끝 글자가 앞 값으로 덮이면 안 된다
        while card._anim is not None and _time.monotonic() < end + 3:
            QApplication.processEvents()
            _time.sleep(0.01)
        assert card.value.text() == "61.0%", card.value.text()
        # 다 돌았거나 멈춘 애니메이션은 지워진다 — 카드에 매달려 남으면 새로 그릴 때마다 쌓인다(셋을 돌렸다)
        from PySide6.QtCore import QEvent as _QEvent, QVariantAnimation
        QApplication.sendPostedEvents(None, _QEvent.Type.DeferredDelete.value)
        assert not card.findChildren(QVariantAnimation), len(card.findChildren(QVariantAnimation))
        # 줄어드는 값은 시작 글이 더 넓다 — 끝 글자 폭만 잡으면 도는 중에 옆 카드가 들썩인다
        card.set("12,345,678점")
        while card._anim is not None and _time.monotonic() < end + 6:
            QApplication.processEvents()
            _time.sleep(0.01)
        card.set("9점")
        first = count_text("9점", count_number("9점"), 12345678.0)   # 도는 첫 글(끝 글에 쉼표가 없으니 쉼표 없이)
        w0 = card.value.fontMetrics().horizontalAdvance(first)
        assert first == "12345678점" and card.value.minimumWidth() >= w0, (first, card.value.minimumWidth(), w0)
        card.hide()
        card.set("70.0%")
        assert card.value.text() == "70.0%", "안 보이는 카드는 바로 써야 한다"
    finally:
        config.UI_ANIMATE = old
        card.deleteLater()


def test_empty_state_draws_icon_86():
    # 86 C4 — 빈 칸 위에 아이콘이 그려지고(아이콘 자리 픽셀이 바탕과 다르다) 문구는 그대로 · 낮은 그림 칸은 글자만
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    import charts as _charts
    from widgets import EmptyState
    e = EmptyState("경기가 없습니다.")
    e.resize(300, 120)
    img = e.grab().toImage()
    cx, top = 150, e.layout().contentsMargins().top()
    edge = {img.pixelColor(cx - _charts.EMPTY_ICON_PX // 2 + dx, top + _charts.EMPTY_ICON_PX // 2).name() for dx in (0, 1, 2)}
    assert T.EMPTY_ICON in edge or len(edge - {QColor(T.PANEL).name(), QColor(T.BG).name()}) > 0, edge
    assert e.text() == "경기가 없습니다." and e.label.text() == e.text()
    e.deleteLater()

    def drawn(h):
        im = QImage(300, h, QImage.Format.Format_RGB32)
        im.fill(QColor(T.PANEL))
        p = QPainter(im)
        _charts.paint_empty(p, QRectF(0, 0, 300, h), "경기 없음")
        p.end()
        # 아이콘 왼쪽 테두리 선 자리(가운데 줄의 글자보다 위)
        y = int(h / 2 - (_charts.EMPTY_ICON_PX + 8 + 20) / 2 + _charts.EMPTY_ICON_PX / 2)
        return any(im.pixelColor(150 - _charts.EMPTY_ICON_PX // 2 + dx, y).name() != QColor(T.PANEL).name() for dx in (-1, 0, 1))
    assert drawn(200), "높은 칸에 아이콘이 안 그려졌다"
    assert not drawn(_charts.EMPTY_ICON_MIN_H - 1), "낮은 칸은 글자만이어야 한다"


def test_dash_card_hover_only_when_clickable_86():
    # 86 C2 — 누를 수 있는 카드만 마우스 올림에 테두리가 바뀐다
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication
    from dashboard import DashCard
    nav, still = DashCard("갈 곳", ("성적 진단", None)), DashCard("그냥")
    for c in (nav, still):
        c.show()
        QApplication.sendEvent(c, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert nav.property("hot") is True and not still.property("hot"), (nav.property("hot"), still.property("hot"))
    assert f"{T.CARD_HOVER_BORDER}" in T.QSS and 'QFrame#card[hot="true"]' in T.QSS
    QApplication.sendEvent(nav, QEvent(QEvent.Type.Leave))
    assert not nav.property("hot")
    for c in (nav, still):
        c.deleteLater()


def test_platform_class_fonts_follow_app_font():
    # 75 — 윈도우는 목록·상태 줄 등에 클래스 글꼴(9pt=12px)을 따로 주고, 실측상 **스타일시트를 걸 때** 그 표를 다시 채운다
    # (setFont 바로 뒤에 덮었더니 실제 창 경기 목록이 그대로 12px). offscreen 엔 그 표가 비어 있으니 setStyleSheet 가 윈도우처럼
    # 작은 글꼴을 심게 바꿔 끼우고, apply 가 끝난 뒤 표가 앱 글꼴인지 Qt 의 클래스 글꼴 표(QApplication.font(이름))로 본다.
    from PySide6.QtGui import QFont
    small = QFont(T.FONT_FAMILY)
    small.setPointSize(9)
    names = ("QAbstractItemView", "QListView", "QStatusBar", "QTipLabel")
    orig = _app.setStyleSheet

    def like_windows(qss):
        orig(qss)
        for name in names:
            _app.setFont(small, name)
        # 심은 것이 실제로 먹는지 먼저 — 안 먹으면 아래 단언이 고침 없이도 참이다
        assert [_app.font(n).pointSize() for n in names] == [9] * len(names)
    _app.setStyleSheet = like_windows
    try:
        T.apply(_app)
    finally:
        del _app.setStyleSheet
    got = [_app.font(n).pixelSize() for n in names]
    assert got == [T.BASE_FONT_PX] * len(names), got


def test_75_small_fixes():
    # 75 — 반올림 0% · 금액 단위 · 같은 횟수 같은 순위
    from widgets import pct_text
    assert [pct_text(p) for p in (0, 0.2, 0.5, 0.6, 1.4, 99.6)] == ["0%", "1% 미만", "1% 미만", "1%", "1%", "100%"]
    won = app_main.MainWindow._won
    vals = (None, 5000, 31_357_040, 1.5e9, 123_456_780_000)
    assert [won(v) for v in vals] == [app_main.NA, "5,000원", "3,136만", "15억", "1,235억"], [won(v) for v in vals]
    # 75 의 "같은 횟수 같은 순위"(games_rank)는 77 B 에 순위 열째 [팀컬러 랭킹] 표가 없어져 같이 지웠다


def test_75_teamcolor_min_games_from_config():
    # 75 U6 — 거르기 기본값과 흐림 기준이 같은 상수(8 이던 core.MIN_COND 가 아니다)
    import types
    assert config.TEAMCOLOR_MIN_GAMES != app_main.core.MIN_COND
    assert all(sp.value() == config.TEAMCOLOR_MIN_GAMES for sp in _win._teamcolor_min_spins), \
        [sp.value() for sp in _win._teamcolor_min_spins]
    s = types.SimpleNamespace(team_color="프랑스", rated=11, mixed=False, opponents={"a", "b", "c", "d"})
    saved = config.TEAMCOLOR_MIN_GAMES
    try:
        config.TEAMCOLOR_MIN_GAMES = 12
        assert _win._teamcolor_weak(s), "11경기 < 12 인데 안 흐림"
        config.TEAMCOLOR_MIN_GAMES = 11
        assert not _win._teamcolor_weak(s)
    finally:
        config.TEAMCOLOR_MIN_GAMES = saved


def test_75_no_account_combo_in_top_bar():
    # 75 — 위쪽 바 "등록" 콤보는 안 써서 뺐다(데이터는 그대로)
    from PySide6.QtWidgets import QLabel
    page = _win.stack.widget(_win.PAGE_MAIN)
    assert not [lb for lb in page.findChildren(QLabel) if lb.text() == "등록"]
    assert not hasattr(_win, "_acct_combos") and not hasattr(_win, "_refresh_accounts")


def test_75_version_label_click_checks_update_now():
    from PySide6.QtTest import QTest
    page = _win.stack.widget(_win.PAGE_MAIN)
    lb = [x for x in page.findChildren(app_main.QLabel) if x.objectName() == "versionLabel"][0]
    calls = []
    saved = (_win.start_update_check, config.UPDATE_CHECK, config.notice_needed, _win._update_click_at)
    _win.start_update_check = lambda: calls.append(1)
    config.notice_needed = lambda: False
    try:
        config.UPDATE_CHECK = False
        QTest.mouseClick(lb, Qt.MouseButton.LeftButton)
        assert calls == [], "끈 설정인데 확인함"
        config.UPDATE_CHECK = True
        QTest.mouseClick(lb, Qt.MouseButton.LeftButton)
        assert calls == [1], calls
        QTest.mouseClick(lb, Qt.MouseButton.LeftButton)   # 간격 안 — 다시 안 묻는다
        assert calls == [1], calls
        _win._update_click_at -= config.UPDATE_ON_CLICK_MIN_S + 1
        QTest.mouseClick(lb, Qt.MouseButton.LeftButton)
        assert calls == [1, 1], calls
    finally:
        (_win.start_update_check, config.UPDATE_CHECK, config.notice_needed, _win._update_click_at) = saved
        del _win.start_update_check   # 인스턴스 속성을 지워 클래스 메서드로 되돌린다


def test_75_pair_box_stacks_when_tables_do_not_fit():
    # 75 — 1280×720 실제 창에서 하루 승률 팀컬러 표가 9px 로 줄고도 가로 스크롤. 좁으면 위아래로
    from widgets import FitTableWidget, PairBox
    from PySide6.QtWidgets import QTableWidgetItem, QVBoxLayout
    box = PairBox()
    tables = []
    for _ in range(2):
        t = FitTableWidget(3, 4)
        t.setHorizontalHeaderLabels(["이름", "인원", "승·무·패", "승률"])
        for r in range(3):
            for c in range(4):
                t.setItem(r, c, QTableWidgetItem("아주 긴 팀컬러 이름 " * (2 if c == 0 else 1)))
        lay = QVBoxLayout()
        lay.addWidget(t)
        box.add(lay, t)
        t.refit()
        tables.append(t)
    need = box.need_width()
    assert need > 0
    box.resize(need + 10, 400)
    box.show()
    _app.processEvents()
    assert not box.stacked(), (need, box.width())
    box.resize(need - 10, 400)
    _app.processEvents()
    assert box.stacked(), (need, box.width())
    assert all(t.font().pixelSize() == t._base_cell_px for t in tables), [t.font().pixelSize() for t in tables]
    box.resize(need + 10, 400)
    _app.processEvents()
    assert not box.stacked()
    box.close()
    assert isinstance(_win.daywr_pair, PairBox)


def test_player_table_cells_not_elided():
    # 열 폭이 Qt 가 계산한 필요 폭보다 좁으면 "33.3" 이 "3…" 로 잘린다
    # (상수 여백을 42→24 로 줄였을 때 실제로 그랬다).
    _win._go_page("선수 지표")
    _app.processEvents()
    tb = _win.tbl_players
    short = [(tb.horizontalHeaderItem(c).text(), tb.columnWidth(c), tb.sizeHintForColumn(c))
             for c in range(tb.columnCount())
             if not tb.isColumnHidden(c) and tb.columnWidth(c) < tb.sizeHintForColumn(c)]   # 78 ③b — 숨긴 묶음 열은 폭 0 이 맞다
    assert not short, short


def test_player_rating_spark_q1():
    # 88 Q1 — 평점 칸 글자 왼쪽 꺾은선: 값은 표와 같은 범위의 recent_ratings · 정렬해도 행을 따라감 · 자리가 실제로 비고
    # 거기에 선이 그려진다(값을 지우면 안 그려진다) · 눈금은 고정
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor
    _win._go_page("선수 지표", "지표")
    _win._render_players(_win._slice()[1])
    _app.processEvents()
    tb, cols = _win.tbl_players, _win.PLAYER_COLUMNS
    rc, nc = cols.index("평점"), cols.index("선수")
    recent = core.recent_ratings(_win._slice()[1], _win._ouid)
    assert any(len(v) >= 2 for v in recent.values()), "픽스처에 두 판 넘게 뛴 선수가 없다 — 검사가 빈다"

    def pairs():
        return [(tb.item(r, nc).data(Qt.ItemDataRole.UserRole), tb.item(r, rc).data(app_main.SPARK_ROLE))
                for r in range(tb.rowCount())]
    got = pairs()
    assert got and all(v == recent[sp] for sp, v in got), got
    tb.sortByColumn(cols.index("출전"), Qt.SortOrder.AscendingOrder)
    _app.processEvents()
    assert all(v == recent[sp] for sp, v in pairs()), "정렬하면 선이 다른 선수로 간다"
    assert "최근" in tb.item(0, rc).toolTip(), tb.item(0, rc).toolTip()
    # 범위 — 표와 같은 빠른 범위(저장 전체가 아니다). 먼저 두 범위가 실제로 다른 값을 내는지 단언(같으면 이 검사가 빈다)
    with _range("games", 2):
        short = core.recent_ratings(_win._slice()[1], _win._ouid)
        assert short != recent, "짧은 범위가 전체와 같다 — 범위 배선을 못 잰다"
        _win._render_players(_win._slice()[1])
        assert all(v == short.get(sp) for sp, v in pairs() if v), pairs()
    _win._render_players(_win._slice()[1])
    # 자리 — 그 열 폭이 글자 크기 힌트보다 그래프 폭만큼 넉넉하다
    dg = tb.itemDelegateForColumn(rc)
    assert isinstance(dg, app_main.SparkDelegate)
    assert _win._player_fit_extra[rc] >= config.Q1_SPARK_W, _win._player_fit_extra
    assert tb.columnWidth(rc) - tb.sizeHintForColumn(rc) >= config.Q1_SPARK_W, (tb.columnWidth(rc), tb.sizeHintForColumn(rc))
    # 그림 — 두 판 넘는 줄의 칸 왼쪽 그래프 자리에 선 색이 있다 · 값을 지우면 없다
    row = next(r for r in range(tb.rowCount()) if len(tb.item(r, rc).data(app_main.SPARK_ROLE) or []) >= 2)
    tb.scrollToItem(tb.item(row, rc))
    _app.processEvents()
    line = QColor(T.CHART_LINE)

    def line_px() -> int:
        # 칸 왼쪽 절반만 — 오른쪽 끝 점(가장 최근 판 동그라미)만으로도 10px 를 넘어 선을 빼도 초록이었다(손 변이)
        rect = tb.visualRect(tb.model().index(row, rc))
        img = tb.viewport().grab(rect).toImage()
        n = 0
        for x in range(min(img.width(), config.Q1_SPARK_W // 2)):
            for y in range(img.height()):
                c = img.pixelColor(x, y)
                if abs(c.red() - line.red()) + abs(c.green() - line.green()) + abs(c.blue() - line.blue()) < 60:
                    n += 1
        return n
    assert line_px() >= 10, line_px()
    keep = tb.item(row, rc).data(app_main.SPARK_ROLE)
    tb.item(row, rc).setData(app_main.SPARK_ROLE, None)
    _app.processEvents()
    try:
        assert line_px() == 0, ("값이 없는데 선 색이 있다 — 다른 무엇을 재고 있다", line_px())
    finally:
        tb.item(row, rc).setData(app_main.SPARK_ROLE, keep)
    # 눈금 고정 — 아래 끝·위 끝·밖 값은 끝에 붙는다 · 점 하나는 안 그린다
    r = QRectF(0, 0, 40, 10)
    pts = app_main.SparkDelegate.points([config.Q1_SPARK_LO, config.Q1_SPARK_HI, 99.0, 0.0], r)
    assert [(round(p.x(), 3), round(p.y(), 3)) for p in pts] == [(0, 10), (13.333, 0), (26.667, 0), (40, 10)], pts
    assert app_main.SparkDelegate.points([7.0], r) == []


def _at_size(w, h):
    _win.resize(w, h)
    _app.processEvents()
    _app.processEvents()


def _fit_box(n_rows: int, height: int, fit: bool = True):
    """세로 레이아웃 = 위 카드 줄(Preferred) + 표(_make_table) — 높이를 고정한 틀에 넣어 띄운다."""
    from PySide6.QtWidgets import QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
    box = QWidget()
    v = QVBoxLayout(box)
    row = QHBoxLayout()
    card = app_main.StatCard("카드")
    row.addWidget(card, 1)
    v.addLayout(row)
    t = app_main.MainWindow._make_table(["가", "나"])
    if not fit:
        t.set_fit_rows(False)   # 78 전 그대로 — 끝 빈 칸도 안 붙는다
    v.addWidget(t, 1)   # 페이지들이 흔히 주는 늘림 비율 1 그대로
    t.setRowCount(n_rows)
    for r in range(n_rows):
        for c in range(2):
            t.setItem(r, c, QTableWidgetItem(f"{r}-{c}"))
    t.refit()
    box.resize(600, height)
    box.show()
    for _ in range(4):
        _app.processEvents()
    return box, card, t


def test_table_height_fits_rows_78():
    # 78 ③c — 표 높이는 줄 수만큼 · 남는 높이는 끝 빈 칸으로(옆 카드가 안 늘어난다) · 모자라면 TABLE_MIN_ROWS 줄은 지킨다 ·
    # 줄이 TABLE_FIT_MAX_ROWS 보다 많으면 예전처럼 남는 높이를 다 쓰고 안에서 스크롤(원하는 높이로 페이지를 늘리지 않는다).
    keep = (config.TABLE_MIN_ROWS, config.TABLE_FIT_MAX_ROWS)
    boxes = []
    try:
        # 막지 않았으면 — 같은 표를 줄 맞춤 없이(예전) 띄우면 1줄 표가 틀 끝까지 늘어 아래가 빈다
        box, card, t = _fit_box(1, 600, fit=False)
        boxes.append(box)
        assert t.viewport().height() - t.rowHeight(0) > 200, ("예전 배치가 빈 상자를 안 만든다 — 이 검사는 아무것도 안 잰다",
                                                              t.viewport().height())
        # 1줄 — 표는 머리글 + 1줄 · 카드는 제 높이 · 남는 높이는 아래로
        box, card, t = _fit_box(1, 600)
        boxes.append(box)
        assert t.height() == t._rows_height(), (t.height(), t._rows_height())
        assert t.viewport().height() - t.rowHeight(0) <= 2, ("1줄 표 아래 빈 상자", t.viewport().height())
        assert card.height() <= card.sizeHint().height() + 2, ("남는 높이가 카드로 갔다", card.height(), card.sizeHint().height())
        assert t.verticalScrollBar().maximum() == 0
        # 0줄 — 머리글만
        box, card, t = _fit_box(0, 600)
        boxes.append(box)
        assert t.viewport().height() <= 2, ("빈 표가 큰 상자로 남았다", t.viewport().height())
        # 자리가 모자라면 TABLE_MIN_ROWS 줄은 보이도록 최소 높이를 알린다(스크롤 페이지가 그만큼 늘어난다)
        n = config.TABLE_FIT_MAX_ROWS
        box, card, t = _fit_box(n, 150)
        boxes.append(box)
        need = t._rows_height(config.TABLE_MIN_ROWS)
        assert t.minimumSizeHint().height() == need and box.minimumSizeHint().height() >= need, \
            (t.minimumSizeHint().height(), box.minimumSizeHint().height(), need)
        # 넉넉한 틀에선 줄 전부 · 줄 맞춤 동안 표 자신의 비율은 0(빈 칸과 반씩 나누면 12줄 표가 207px 로 눌렸다)
        box, card, t = _fit_box(n, 900)
        boxes.append(box)
        assert t.height() == t._rows_height(), ("넉넉한데 줄이 다 안 보인다", t.height(), t._rows_height())
        box, card, t = _fit_box(n, t._rows_height() + card.sizeHint().height() + 40)
        boxes.append(box)
        assert t.verticalScrollBar().maximum() == 0, ("딱 맞는 틀에서 표가 빈 칸과 높이를 나눴다", t.height(), t._rows_height())
        # 긴 표 — 남는 높이를 다 쓰고(카드 아래부터 틀 끝까지) 원하는 높이는 최소 줄 수만큼
        box, card, t = _fit_box(n + 1, 600)
        boxes.append(box)
        assert t.sizeHint().height() == t._rows_height(config.TABLE_MIN_ROWS), t.sizeHint().height()
        assert t.geometry().bottom() >= box.height() - 30, ("긴 표가 남는 높이를 안 썼다", t.geometry().bottom(), box.height())
        assert t.verticalScrollBar().maximum() > 0
        # 배선 — 기본값이 아닌 상수로(같은 값이면 배선이 끊겨도 참이다)
        config.TABLE_MIN_ROWS, config.TABLE_FIT_MAX_ROWS = 2, 4
        box, card, t = _fit_box(5, 600)   # 4줄 상한을 넘었다 — 긴 표 취급
        boxes.append(box)
        assert t.sizeHint().height() == t._rows_height(2) and t.minimumSizeHint().height() == t._rows_height(2), \
            (t.sizeHint().height(), t.minimumSizeHint().height(), t._rows_height(2))
        assert t.geometry().bottom() >= box.height() - 30, t.geometry().bottom()
        box, card, t = _fit_box(4, 600)
        boxes.append(box)
        assert t.height() == t._rows_height(), (t.height(), t._rows_height())
    finally:
        config.TABLE_MIN_ROWS, config.TABLE_FIT_MAX_ROWS = keep
        for b in boxes:
            b.hide()
            b.deleteLater()
        _app.processEvents()


def test_fit_rows_on_real_pages_78():
    # 78 ③c — 실제 화면 배선: FC 보상 1줄 표 아래 빈 상자·카드 늘어남 없음 · 주간 요약 10명 목록은 끝까지 · 비교 전엔 빈 상자 없음
    _at_size(*app_main.MIN_WINDOW)
    try:
        _win._go_page("성적 진단", "FC 보상")
        _win._render_rewards(_win._slice()[1])
        for _ in range(4):
            _app.processEvents()
        t = _win.tbl_rewards
        assert t._fit_rows, "_make_table 이 줄 맞춤을 안 켰다"
        content = sum(t.rowHeight(r) for r in range(t.rowCount()))
        assert t.viewport().height() - content <= 2, ("FC 보상 표 아래 빈 상자", t.viewport().height(), content)
        for c in _win.rewards_cards[1]:
            assert c.height() <= c.sizeHint().height() + 2, ("FC 보상 카드가 세로로 늘었다", c.height(), c.sizeHint().height())
        fixed = {k: lst.fixed for k, lst in _win.weekly_lists.items()}
        assert fixed == {k: k in app_main.MainWindow.WEEKLY_FULL_LISTS for k in fixed} and any(fixed.values()), fixed
        assert set(app_main.MainWindow.WEEKLY_FULL_LISTS) <= set(fixed), fixed
    finally:
        _at_size(1600, 900)
    fresh = app_main.MainWindow(_NoApi())
    try:
        assert fresh.pt_compare.isHidden(), "비교 전에 빈 카드·표·스쿼드 상자가 보인다"
        fresh._fill_compare_squad = lambda *a: None
        fresh._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                         {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        fresh._compare_last = ("상대", list(_MATCHES), "opp", [], "")
        fresh._rerender_compare()
        assert not fresh.pt_compare.isHidden(), "비교를 그렸는데 탭 묶음이 숨은 채다"
    finally:
        fresh.deleteLater()
        _app.processEvents()


def test_content_max_width_78():
    # 78 ③a — 본문 묶음(조회 바 + 페이지)은 CONTENT_MAX_W 에서 멈추고 가운데 선다. 1280 창은 그대로(상한이 안 걸린다).
    cw = _win._content
    keep = config.CONTENT_MAX_W
    try:
        assert cw.maximumWidth() == keep, (cw.maximumWidth(), keep)
        box = _win._content_box
        _at_size(*app_main.MIN_WINDOW)
        assert cw.width() < keep, ("1280 에서 상한이 걸렸다 — 아무것도 안 바뀌어야 한다", cw.width(), keep)
        # 상한보다 좁으면 본문이 틀의 폭을 다 쓴다 — 양옆 늘림 칸 시절엔 몇 px 를 뺏겨 [패스 스타일] 이 넘쳤다(실제 창 11-03)
        assert cw.width() == box.width() and cw.x() == 0, ("1280 에서 본문이 폭을 잃었다", cw.x(), cw.width(), box.width())
        # 조회 바가 한 줄로 들어가는 폭(+ 본문 좌우 여백)이 상한 안에 있다 — 아니면 넓은 창에서도 바가 두 줄이 된다(1360 이 그랬다)
        pad = cw.layout().contentsMargins().left() + cw.layout().contentsMargins().right()
        assert _win.top_bar.one_row_width() + pad <= keep, (_win.top_bar.one_row_width(), pad, keep)
        _at_size(keep + 600, 1000)     # 왼쪽 메뉴를 빼고도 상한보다 넓은 창
        assert box.width() > keep + 100, ("창이 상한보다 충분히 넓지 않아 아무것도 안 잰다", box.width(), keep)
        assert cw.width() == keep, (cw.width(), keep)
        assert _win.top_bar._two_rows is False, ("상한 폭에서 조회 바가 두 줄이다", _win.top_bar.width(), _win.top_bar.one_row_width())
        left, right_gap = cw.x(), box.width() - (cw.x() + cw.width())   # 틀은 왼쪽 메뉴 오른쪽부터 창 끝까지
        assert abs(left - right_gap) <= 1 and left > 0, ("가운데가 아니다", left, right_gap)
        # 배선 — 기본값이 아닌 상한(같은 값이면 배선이 끊겨도 참이다)
        config.CONTENT_MAX_W = 1100
        _win._apply_content_max()
        _app.processEvents()
        _app.processEvents()
        assert cw.width() == 1100, cw.width()
        left, right_gap = cw.x(), box.width() - (cw.x() + cw.width())
        assert abs(left - right_gap) <= 1, (left, right_gap)
    finally:
        config.CONTENT_MAX_W = keep
        _win._apply_content_max()
        _at_size(1600, 900)


def test_player_column_groups_78():
    # 78 ③b — 선수 지표: 늘 보이는 열 + 칩 하나의 묶음만 보인다. 숨기기만 — 값·정렬·글씨 크기는 그대로.
    cols = list(app_main.MainWindow.PLAYER_COLUMNS)
    always = list(app_main.MainWindow.PLAYER_ALWAYS_COLUMNS)
    groups = app_main.MainWindow.PLAYER_GROUPS
    members = [n for _g, cs in groups for n in cs]
    assert len(cols) == 22 and len(always) + len(members) == 22, (len(cols), len(always), len(members))
    assert sorted(always + members) == sorted(cols), ("22열이 늘 보임 또는 묶음 하나에 정확히 안 들어간다",
                                                       set(cols) ^ set(always + members), len(set(members)) != len(members))
    _win._render_players(_win._slice()[1])
    tb, seg = _win.tbl_players, _win.seg_player_group
    s = _win._settings()
    try:
        _win._go_page("선수 지표", "지표")
        _at_size(*app_main.MIN_WINDOW)
        tb.sortByColumn(3, Qt.SortOrder.AscendingOrder)
        _app.processEvents()
        before = [tb.item(r, 1).data(Qt.ItemDataRole.UserRole) for r in range(tb.rowCount())]
        assert tb.rowCount() >= 2
        seg.set_current(1, emit=True)   # 첫 칩이 이미 눌려 있으면 눌러도 신호가 없다 — 한 번 옮겨 둔다
        for i, (gname, gcols) in enumerate(groups):
            seg.set_current(i, emit=True)
            _app.processEvents()
            shown = [tb.horizontalHeaderItem(c).text() for c in range(tb.columnCount()) if not tb.isColumnHidden(c)]
            assert shown == [c for c in cols if c in set(always) | set(gcols)], (gname, shown)
            assert tb.horizontalScrollBar().maximum() == 0, (gname, "가로 스크롤", tb.horizontalScrollBar().maximum())
            assert tb.font().pixelSize() == T.TABLE_CELL_PX, (gname, tb.font().pixelSize(), T.TABLE_CELL_PX)
            assert tb.horizontalHeader().sortIndicatorSection() == 3 and \
                tb.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder, gname
            assert [tb.item(r, 1).data(Qt.ItemDataRole.UserRole) for r in range(tb.rowCount())] == before, (gname, "행 순서가 바뀜")
            assert all(tb.columnWidth(c) >= tb.sizeHintForColumn(c) for c in range(tb.columnCount())
                       if not tb.isColumnHidden(c)), gname
            assert str(_win._settings().value(app_main.MainWindow.PLAYER_GROUP_KEY)) == gname, ("고른 칩을 안 남겼다", gname)
            assert _win._saved_player_group() == i, (gname, _win._saved_player_group())
        # 다시 그려도(재검색) 고른 묶음이 유지된다
        _win._render_players(_win._slice()[1])
        shown = [tb.horizontalHeaderItem(c).text() for c in range(tb.columnCount()) if not tb.isColumnHidden(c)]
        assert shown == [c for c in cols if c in set(always) | set(groups[-1][1])], shown
        # 모르는 저장값은 첫 칩
        s.setValue(app_main.MainWindow.PLAYER_GROUP_KEY, "없는 묶음")
        assert _win._saved_player_group() == 0
    finally:
        s.setValue(app_main.MainWindow.PLAYER_GROUP_KEY, groups[0][0])
        s.sync()
        seg.set_current(0, emit=True)
        _at_size(1600, 900)


def test_window_shrinks_to_min_without_squeezing():
    # 위쪽 바가 한 줄이던 때 창은 1566x866 밑으로 안 줄었다(1366 노트북에서 넘침).
    # 반대로 명시적 최소 크기가 내용 최소보다 작으면 그만큼 조용히 잘린다.
    try:
        _at_size(1000, 600)  # 그보다 작게 줄이려 해도 최소에서 멈춰야 한다
        assert (_win.width(), _win.height()) == app_main.MIN_WINDOW, \
            (_win.width(), _win.height())
        need = _win.centralWidget().minimumSizeHint()
        assert need.width() <= app_main.MIN_WINDOW[0], need.width()
        assert need.height() <= app_main.MIN_WINDOW[1], need.height()
        # 페이지를 감싼 스크롤 틀이 안쪽 최소 폭을 밖에 알려야 가로가 안 잘린다 —
        # 1280 에선 공간이 남아 프레임 폭만 보면 안 드러나므로, 창 쪽 최소 폭이
        # 가장 넓은 페이지의 최소 폭을 품는지 본다.
        widest = max(_win.pages.widget(i).widget().minimumSizeHint().width()
                     for i in range(_win.pages.count()))
        assert _win.pages.minimumSizeHint().width() >= widest, \
            (_win.pages.minimumSizeHint().width(), widest)
        # 숨은 페이지는 크기가 안 잡혀(640) 있다 — 열어서 잰다. 전엔 안 열고 재서, 앞선 테스트들이 페이지를 한 번씩
        # 열어 둔 전체 실행에서만 통과하고 단독으로는 실패했다(2026-10-04).
        cur = _win.pages.currentIndex()
        for i in range(_win.pages.count()):
            _win.pages.setCurrentIndex(i)
            _app.processEvents()
            frame = _win.pages.widget(i)
            inner = frame.widget().minimumSizeHint().width()
            assert frame.width() >= inner, (i, frame.width(), inner)
        _win.pages.setCurrentIndex(cur)
    finally:
        _at_size(1600, 900)


def test_dashboard_kpi_row_fits_min_window_with_longest_text():
    """승률 카드의 한 줄("최근 20경기 … · 승÷(승+패) · 무 N")이 줄바꿈 없는 라벨이라 카드 최소 폭을 밀어, 대시보드가
    1280 창(배율 150%)에서 가로로 잘렸다. 테스트 데이터는 경기가 적어 그 줄이 짧게 나오므로, 가장 긴 글을 직접 넣어 잰다."""
    d = _win.dashboard
    cur = _win.pages.currentIndex()
    # 65 M1 — 숫자 카드 여덟(머리 넷 · 리포트 넷)의 보조 줄과 머리 두 줄에 실제로 들어갈 가장 긴 글
    longest = {d.kpi_rate.sub: "99,999승 99,999무 99,999패 · 승÷(승+패)",
               d.card_score.sub: "200위까지 +9,999.99점", d.card_value.sub: "10,001명 중 · 999억 9,999만(마지막 수집)",
               d.card_fc.sub: "9,999~9,999등 / 99,999명 · 저장 시작 뒤부터",
               d.card_day.sub: "무승부 뺀 100.0% · 오른 날 100%", d.card_avg.sub: "지금 9,999점 (+9,999)",
               d.card_dd.sub: "시즌 최고 9,999점 · 같은 구간 중앙 −9,999점", d.card_recent.sub: "누적 전체 100.0% 대비",
               d.lb_goals_sub: "득실차 -99,999", d.lb_streak_sub: "최장 99연승 · 99연패 (누적 전체)",
               d.lb_days: "오늘 -9,999점 99승 99무 99패 · 100.0% · 9,999 FC  │  어제 -9,999점 99승 99무 99패 · 100.0% · 9,999 FC",
               d.lb_record: "시즌 기록 · 최고 9,999.99 #9,999 (12.31) · 챔스 9,999판 · 슈챔 9,999판 · 남들 중앙 9,999판보다 9,999판 느리게"}
    saved = {lb: lb.text() for lb in longest}
    try:
        _at_size(*app_main.MIN_WINDOW)
        _win._go_page("대시보드")
        _app.processEvents()
        for lb, text in longest.items():
            lb.setText(text)
            lb.setVisible(True)
        _app.processEvents()
        # 보조 줄은 줄바꿈이어야 한다 — 꺼지면 한 줄 글이 카드 최소 폭을 밀어 1280 창에서 잘렸다(2026-10-11 실측 106px)
        assert all(lb.wordWrap() for lb in longest), [lb.text() for lb in longest if not lb.wordWrap()]
        frame = _win.pages.widget(_win._page_index["대시보드"])
        need = d.minimumSizeHint().width()
        assert need <= frame.viewport().width(), (need, frame.viewport().width())
    finally:
        for lb, text in saved.items():
            lb.setText(text)
        _win.pages.setCurrentIndex(cur)
        _at_size(1600, 900)
        _win._render_dashboard()


def test_top_bar_wraps_only_when_narrow():
    try:
        _at_size(*app_main.MIN_WINDOW)
        assert _win.top_bar.is_two_rows()
        _at_size(1920, 1000)
        assert not _win.top_bar.is_two_rows()
    finally:
        _at_size(1600, 900)


def test_season_combo_never_narrower_than_longest_item():
    from PySide6.QtGui import QFontMetrics
    cb = _win.cb_season
    fm = QFontMetrics(cb.font())
    longest = max(fm.horizontalAdvance(cb.itemText(i)) for i in range(cb.count()))
    # 막지 않았으면: 최소 150 이라 긴 항목이 잘린다 — 픽스처 항목이 150 을 넘는지 먼저
    assert longest > 150, longest
    # 공간이 남으면 칸은 알아서 넓어지므로 실제 폭이 아니라 '눌릴 수 있는 한계'를 본다.
    # 레이아웃은 명시적 최소 폭이 있으면 힌트 대신 그 값을 쓴다(150 이던 때가 그랬다).
    floor = cb.minimumWidth() or cb.minimumSizeHint().width()
    assert floor > longest, (floor, longest)
    try:
        _at_size(*app_main.MIN_WINDOW)
        assert cb.width() > longest, (cb.width(), longest)
    finally:
        _at_size(1600, 900)


def test_fit_label_shrinks_instead_of_clipping():
    import widgets
    lb = widgets.FitLabel("906승 393무 903패 (41.1%)", base_pt=T.PT_TITLE, min_pt=T.PT_XL)
    lb.resize(329, 60)  # 실데이터에서 잘렸던 칸 폭
    lb.show()  # 숨은 위젯은 resizeEvent 가 show 때까지 미뤄진다
    _app.processEvents()
    from PySide6.QtGui import QFontMetrics
    full = QFontMetrics(lb._font_at(T.PT_TITLE)).horizontalAdvance(lb.text())
    assert full > 329, full  # 기본 크기(PT_TITLE) 그대로였으면 잘렸다
    assert lb.font().pointSize() < T.PT_TITLE, lb.font().pointSize()
    assert QFontMetrics(lb.font()).horizontalAdvance(lb.text()) <= 329


def _table_elisions(tables) -> list:
    """지금 창 크기에서 표마다 — 페이지 가로 잘림 · 가로 스크롤 · 열 폭 < 글자 · 헤더. 빈 표는 안 잰다."""
    bad = []
    for tb in tables:
        if tb.rowCount() == 0:
            continue
        page = next((n for n, i in _win._page_index.items()
                     if _win.pages.widget(i).isAncestorOf(tb)), None)
        if page:
            tabs = _win._page_tabs.get(page)
            tab = next((tabs.names()[i] for i in range(len(tabs.names()))
                        if tabs.page(i).isAncestorOf(tb)), None) if tabs else None
            _win._go_page(page, tab)
            _app.processEvents()
            # 페이지 틀이 안쪽 최소 폭보다 좁으면 가로가 조용히 잘린다 — 숨은 페이지는 크기가 안 잡혀(640)
            # 있어 연 뒤에 잰다
            frame = _win.pages.currentWidget()
            if frame.width() < frame.widget().minimumSizeHint().width():
                bad.append((page, "페이지 가로 잘림", frame.width(),
                            frame.widget().minimumSizeHint().width()))
        hdr = tb.horizontalHeader()
        # 세로 막대 자리를 안 빼면 가로 막대가 생기고 끝 열이 가려진다
        if tb.horizontalScrollBar().maximum() > 0:
            bad.append((page, "가로 스크롤", tb.horizontalScrollBar().maximum()))
        # 헤더 화살표는 정렬 중인 열에만 그려진다 — 나머지는 화살표 없이 잰다
        sort_col = hdr.sortIndicatorSection()
        shown = hdr.isSortIndicatorShown()
        hdr.setSortIndicatorShown(False)
        bare = [hdr.sectionSizeHint(c) for c in range(tb.columnCount())]
        hdr.setSortIndicatorShown(True)
        arrow = [hdr.sectionSizeHint(c) for c in range(tb.columnCount())]
        hdr.setSortIndicatorShown(shown)
        for c in range(tb.columnCount()):
            if tb.isColumnHidden(c):
                continue   # 숨긴 열(57 K3 상대 점수 — 관문 미달)은 폭 0 이 맞다
            w = tb.columnWidth(c)
            head = arrow[c] if c == sort_col else bare[c]
            if w < tb.sizeHintForColumn(c) or w < head:
                bad.append((page, tb.horizontalHeaderItem(c).text(), w,
                            tb.sizeHintForColumn(c), head))
    return bad


def _check_elisions(tables) -> None:
    # 작은 화면(FHD 150% 등)의 최소 크기 — 폭 1264(화면 폭 − 테두리) · 낮춘 높이. 높이가 낮아 페이지에 세로 막대가
    # 생기고 그 폭만큼 가로가 준다(1.0.3).
    small = app_main.initial_window(1280, 688).min_size
    try:
        for size in (app_main.MIN_WINDOW, small, (1600, 900), app_main.LARGE_WINDOW):
            _win.setMinimumSize(*small)
            _at_size(*size)
            assert (_win.width(), _win.height()) == tuple(size), ("그 크기로 못 줄였다", size, _win.size())
            bad = _table_elisions(tables)
            assert not bad, (size, bad[:5])
    finally:
        _win.setMinimumSize(*app_main.MIN_WINDOW)
        _at_size(1600, 900)


def _show_widget(wd) -> None:
    """그 위젯이 든 (메뉴, 탭)을 연다 — 숨은 페이지는 폭이 안 잡힌다."""
    page = next((n for n, i in _win._page_index.items() if _win.pages.widget(i).isAncestorOf(wd)), None)
    if page:
        tabs = _win._page_tabs.get(page)
        tab = next((tabs.names()[i] for i in range(len(tabs.names())) if tabs.page(i).isAncestorOf(wd)), None) if tabs else None
        _win._go_page(page, tab)
        _app.processEvents()


def _check_metric_lists(lists, narrow: int = 200) -> None:
    """67 R0 — 지표 줄 목록이 1280×720 · 작은 화면 · 큰 창에서 이름·칸이 안 잘리나(보조 글은 잘려도 툴팁).
    빈 목록은 안 잰다 · 좁은 폭(narrow)에서는 잘려야 한다(검사가 무언가를 재는지 — 짧은 줄 목록은 더 좁게)."""
    small = app_main.initial_window(1280, 688).min_size
    lists = [x for x in lists if x.rowCount()]
    assert lists, "잴 목록이 없다"
    try:
        for size in (app_main.MIN_WINDOW, small, (1600, 900), app_main.LARGE_WINDOW):
            _win.setMinimumSize(*small)
            _at_size(*size)
            for lst in lists:
                _show_widget(lst)
                w, dg = lst.viewport().width(), lst.itemDelegate()
                bad = [(r.name, dg.overflow(r, w, lst.font())) for r in lst.all_rows() if dg.overflow(r, w, lst.font())]
                assert not bad, (size, w, bad[:3])
        for lst in lists:
            assert lst.itemDelegate().overflow(lst.all_rows()[0], narrow, lst.font()), "좁은 폭에서도 넘침이 0 — overflow 가 아무것도 안 잰다"
    finally:
        _win.setMinimumSize(*app_main.MIN_WINDOW)
        _at_size(1600, 900)


def test_no_table_elides_at_min_or_default_size():
    # 예전 기본 표는 둘째 열부터 균등 분할이라 '선수 B'·'기간'·(1280 폭에서) '스코어' 가
    # "…" 로 잘렸다. 헤더도 잰다 — 채우는 동안 정렬이 꺼져 있어 화살표 자리를 빼고
    # 폭을 잡으면 "승률▾" 이 겹쳤다.
    import widgets
    # 메인 창의 표만 — 대화상자 안의 표(포지션 선수 · 선수 카드 [랭커 기록])는 열 때 생기고 앞 테스트가 열어 둔다
    tables = [t for t in _win.findChildren(widgets.FitTableWidget) if t.window() is _win]
    assert len(tables) == 45, len(tables)   # 45 = 88 [내 기록] 업적 표(채운 표는 test_records_page_… 가 잰다) · 44 = 87 V3 [강화 비용] 시도 횟수 표(채운 표는 test_enhance_page_shows_result 가 잰다) · 43 = 68 C6 [주간 시세] 표(채운 표는 test_68_price_week_tab_… 가 잰다) · 42 = 83 [강화 비용] 단계 표·금액 구간 표(채운 표는 test_enhance_page_shows_result 가 잰다) · 40 = 82 [영입 전후] 표 둘 · 38 = 77 B [팀컬러 랭킹] 표가 [상대 팀컬러] 줄 목록으로 합쳐짐 · 39 = 67 R4 [주간 요약] 넷(급상승·하루 최다 승·구단가치 끝·주차)·R6 [보상] 구단가치별이 줄 목록·막대로 빠짐 · 44 = 67 R3 [팀컬러 승률]·R10 랭커 픽 셋·[추천] 카드가 줄 목록으로 빠짐 · 49 = 67 R1·R7 [가성비]·[구단가치] 둘·[메타 변화] 둘이 줄 목록(MetricRowList)으로 빠짐 · 54 = 65 M4 상대 전적이 표에서 줄 목록(RateRowList)으로 빠짐 · 55 = 65 M2 경기 목록이 표에서 카드(MatchCardList)로 빠짐 · 56 = 포지션 특성 [내 스쿼드](57 K12 — test_my_traits_…) · 55 = 스쿼드·이적 [이력](57 K4 — test_history_tab_… ) · 53~54 = 랭킹 추이 [보상] 두 표(57 K11 — test_top3_cards_and_reward_board_tab) · 52 = 선수로 구단주 찾기 [카드별·강화별 사용자](50단계 N30 — 채운 표는 test_card_owner_find_player_scope 가 확인) · 51 = 구단주 비교 [자리별](50단계 D1 — 채운 표는 test_compare_extra_tabs_… 가 잰다) · 50 = 성적 진단 [자리별 수준](50단계 C4 — 채운 표는 test_poslevel_… 가 확인) · 49 = 랭킹 추이 [예측 되짚기](50단계 B8 — 채운 표는 test_predict_check_tab_… 가 잰다) · 47~48 = 상대 전적 [상대 순위] 표 둘(2.9.1 N23)  # 13번째는 랭커와 비교(1.4.1) · 14~17 랭커 픽 셋 · 구단주 찾기 · 18~19 추천 둘(2.1.1) · 등급 체류(2.6.1)
    #                                        · 20~21 랭킹 추이 [메타 변화] 팀컬러·포메이션(2.3.1)
    #                                        · 22~37 메타 분석 11 · 주간 요약 5 · 순위 변동 2 · 카드별 1(2.4.1 — 빈 표는
    #                                          여기서 안 재고 test_rank_meta_tabs_draw_and_fit 가 채워서 잰다)
    #                                        · 38 메타 분석 [포지션 특성](2.5.1 — 채운 표는 test_trait_tab_renders_usage 가 잰다)
    #                                        · 39~40 성적 진단 [운 범위] · 41~43 [경기 습관](2.7.1 — 채운 표는 두 탭 테스트가 잰다)
    #                                        · 44 슛 맵 [공격 루트](2.7.1 — 채운 표는 test_attack_route_tab_… 가 잰다)
    #                                        · 46 성적 진단 [FC 보상](2.9.1 A2 — 채운 표는 test_rewards_tab_… 가 확인)
    _check_elisions(tables)


def test_measure_text_same_as_measuring_everything():
    # 긴 글자부터 재다 멈추는 방식(2026-10-02)이 전부 재는 것과 같은 폭을 내야 한다 —
    # 짧지만 넓은 글자(한글·이모지)를 일부러 섞는다. 다르면 열이 좁아 "…" 로 잘린다.
    import random
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QTableWidgetItem
    import widgets
    rnd = random.Random(11)
    pools = ["iiii", "1.0", "가나다", "뷁뷁", "WWW", "MM", "漢字", "😀😀", "★", "a", "Fullcolor쿠팡", "lllllllllll"]
    t = widgets.FitTableWidget(0, 3)
    for trial in range(30):
        n = rnd.randint(0, 60)
        t.setRowCount(n)
        for r in range(n):
            for c in range(3):
                t.setItem(r, c, QTableWidgetItem(
                    "".join(rnd.choice(pools) for _ in range(rnd.randint(1, 3)))))
        fm = QFontMetrics(QFont(t.font()))
        got = t._measure_text(fm, fm)
        want = {c: max([fm.horizontalAdvance(t.item(r, c).text()) for r in range(n)] + [0])
                for c in range(3)}
        assert got == want, (trial, got, want)
    # 경계를 노린 경우 — 짧은데 넓은 글자(대체 글꼴)가 긴 글자보다 넓다. maxWidth() 만 믿으면
    # 짧은 쪽을 재기 전에 멈춘다(실측: 맑은 고딕 maxWidth 16 · 😀 18, Consolas maxWidth 7 · 가 8 · 漢 13).
    for family, texts in [("Consolas", ["abcde", "漢漢漢"]), (None, ["가가가가가", "😀😀😀😀"])]:
        t.setRowCount(len(texts))
        for r, s in enumerate(texts):
            t.setItem(r, 0, QTableWidgetItem(s))
            t.setItem(r, 1, QTableWidgetItem(""))
            t.setItem(r, 2, QTableWidgetItem(""))
        f = QFont(family) if family else QFont(t.font())
        f.setPixelSize(13)
        fm = QFontMetrics(f)
        widest = max(fm.horizontalAdvance(s) for s in texts)
        assert fm.horizontalAdvance(texts[1]) == widest and fm.horizontalAdvance(texts[0]) < widest, \
            (family, [fm.horizontalAdvance(s) for s in texts])  # 정말 '짧은 쪽이 넓은' 경우인지
        assert t._measure_text(fm, fm)[0] == widest, (family, t._measure_text(fm, fm)[0], widest)


def test_header_paint_size_follows_fit_78():
    # 78 ③ — 앱 QSS 가 머리글 칸을 꾸미면 Qt 는 머리글을 setFont 가 아니라 QSS 글꼴로 그린다(실제 창: 재는 14px · 그리는 16px,
    # 표만의 QSS 가 있던 선수 지표는 약 9px). 그래서 머리글 크기는 표 QSS 에도 싣는다 — 재는 크기 = 싣는 크기 · 표마다 준 QSS 는 남는다.
    import re
    import widgets
    from PySide6.QtWidgets import QHeaderView, QTableWidgetItem

    def qss_px(tb):
        got = re.findall(r"QHeaderView::section \{ font-size: (\d+)px; font-weight: bold; \}", tb.styleSheet())
        return int(got[-1]) if got else None

    # 데이터가 오기 전 빈 표도 — 기본 크기를 바꾸면 그리는 크기가 바로 따라간다(채울 때까지 옛 크기로 그리지 않게)
    empty = widgets.FitTableWidget(0, 2)
    empty.set_base_font_px(T.BASE_FONT_PX, T.BASE_FONT_PX)
    assert qss_px(empty) == T.BASE_FONT_PX, ("빈 표의 머리글 QSS 가 기본 크기를 안 따라갔다", qss_px(empty))
    empty.deleteLater()
    tb = widgets.FitTableWidget(2, 4)
    tb.setStyleSheet("QTableWidget::item { padding: 12px 7px; }")
    tb.setHorizontalHeaderLabels(["아주 긴 열 제목 하나", "둘째 긴 제목", "셋째 제목", "넷"])
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    tb.horizontalHeader().setMinimumSectionSize(0)
    tb.set_base_font_px(T.TABLE_CELL_PX, T.TABLE_HEADER_PX)
    for r in range(2):
        for c in range(4):
            tb.setItem(r, c, QTableWidgetItem("123.4"))
    tb.resize(900, 200)
    tb.show()
    _app.processEvents()
    tb.refit()
    _app.processEvents()
    try:
        hp = tb.horizontalHeader().font().pixelSize()
        assert hp == T.TABLE_HEADER_PX and tb.horizontalHeader().font().bold(), (hp, tb.horizontalHeader().font().bold())
        assert qss_px(tb) == hp, ("그리는 머리글 크기(QSS)가 재는 크기와 다르다", qss_px(tb), hp)
        assert "padding: 12px 7px" in tb.styleSheet(), ("표마다 준 QSS 가 사라졌다", tb.styleSheet())
        # 좁히면 글꼴이 줄고 — 그리는 크기도 같이 준다(전에는 재는 크기만 줄어 머리글이 그대로였다)
        tb.resize(260, 200)
        _app.processEvents()
        tb.refit()
        _app.processEvents()
        small = tb.horizontalHeader().font().pixelSize()
        assert small < T.TABLE_HEADER_PX, ("좁혔는데 머리글 글꼴이 안 줄었다 — 검사가 아무것도 안 잰다", small)
        assert qss_px(tb) == small, ("줄인 머리글을 QSS 가 안 따라왔다", qss_px(tb), small)
        assert "padding: 12px 7px" in tb.styleSheet()
    finally:
        tb.deleteLater()
    # 앱 QSS 의 머리글 칸 규칙에 글꼴 속성이 다시 들어오면 위 QSS 줄보다 먼저 꾸민 값으로 굳는다(PyQt 3)
    sec = re.search(r"QHeaderView::section \{([^}]*)\}", T.QSS)
    assert sec and "font" not in sec.group(1), sec.group(1) if sec else T.QSS[:80]


def test_fit_table_ignores_hidden_columns():
    # 57 K3 — 숨긴 열(상대 점수)의 긴 글자가 폭 계산에 들어가면 보이는 열이 글꼴을 줄이거나 오른쪽이 빈다
    import widgets
    from PySide6.QtWidgets import QHeaderView, QTableWidgetItem
    tb = widgets.FitTableWidget(2, 3)
    tb.setHorizontalHeaderLabels(["가", "나", "숨김"])
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    tb.horizontalHeader().setMinimumSectionSize(0)
    tb.set_base_font_px(T.TABLE_CELL_PX, T.TABLE_CELL_PX)
    for r in range(2):
        tb.setItem(r, 0, QTableWidgetItem("짧은 글"))
        tb.setItem(r, 1, QTableWidgetItem("12.3%"))
        tb.setItem(r, 2, QTableWidgetItem("아주 긴 글자 " * 30))
    tb.setColumnHidden(2, True)
    tb.resize(400, 200)
    tb.show()
    _app.processEvents()
    tb.refit()
    _app.processEvents()
    try:
        assert tb.font().pixelSize() == T.TABLE_CELL_PX, ("숨긴 열 때문에 글꼴을 줄였다", tb.font().pixelSize())
        vis = tb.columnWidth(0) + tb.columnWidth(1)
        assert vis >= tb.viewport().width() - 2, ("보이는 열이 폭을 다 못 받았다", vis, tb.viewport().width())
    finally:
        tb.close()


def test_fit_table_never_scrolls_sideways_above_min_font():
    # 글꼴 크기는 '정비례' 추정으로 고르는데 실측은 조금 더 넓게 나와, 실측으로 한 번
    # 더 깎지 않으면 몇 px 넘쳐 가로 막대가 생겼다. 폭을 훑어 잰다.
    import widgets
    cols = [f"열이름{i}" for i in range(19)]
    tb = widgets.FitTableWidget(6, len(cols))
    tb.setHorizontalHeaderLabels(cols)
    from PySide6.QtWidgets import QHeaderView, QTableWidgetItem
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    tb.horizontalHeader().setMinimumSectionSize(0)
    tb.set_base_font_px(T.TABLE_CELL_PX, T.TABLE_CELL_PX)
    for r in range(6):
        for c in range(len(cols)):
            tb.setItem(r, c, QTableWidgetItem(f"{(r + 3) * (c + 7) * 1.37:.2f}%"))
    tb.show()
    tb.resize(1500, 400)
    _app.processEvents()
    tb.set_content_widths()
    over = []
    for w in range(700, 1500, 3):
        tb.resize(w, 400)
        _app.processEvents()
        if (tb.horizontalScrollBar().maximum() > 0
                and tb.font().pixelSize() > widgets.FitTableWidget.MIN_FONT_PX):
            over.append((w, tb.font().pixelSize(), tb.horizontalScrollBar().maximum()))
    # 최소 글꼴에서 조금(열마다 여유 PAD_SLACK 이내) 넘치는 폭 — 여유를 깎아 맞춘다.
    # 실데이터 1280 폭 선수 지표가 9px 에서 1px 넘쳐 가로 막대가 떴다.
    tb.resize(300, 400)
    _app.processEvents()
    assert tb.font().pixelSize() == widgets.FitTableWidget.MIN_FONT_PX
    need = sum(tb._entry(widgets.FitTableWidget.MIN_FONT_PX)["widths"].values())
    frame = tb.width() - tb.viewport().width()
    tb.resize(need - 5 + frame, 400)
    _app.processEvents()
    assert tb.viewport().width() == need - 5, (tb.viewport().width(), need)
    slack_over = tb.horizontalScrollBar().maximum()
    tb.close()
    assert not over, over[:5]
    assert slack_over == 0, slack_over


def test_sorted_column_gets_arrow_room_after_resort():
    # 화살표 자리는 정렬 중인 열에만 준다 — 사용자가 다른 열을 누르면 다시 재야 한다.
    tb = _win.tbl_players   # 정렬이 켜진 실제 표로(옛 [팀컬러 랭킹] 표는 77 B 에 줄 목록으로 합쳐졌다)
    _win._go_page("선수 지표", "지표")
    _app.processEvents()
    hdr = tb.horizontalHeader()
    for c in range(tb.columnCount()):
        tb.sortByColumn(c, Qt.SortOrder.AscendingOrder)
        _app.processEvents()
        assert tb.columnWidth(c) >= hdr.sectionSizeHint(c), \
            (tb.horizontalHeaderItem(c).text(), tb.columnWidth(c), hdr.sectionSizeHint(c))
    # 폭이 넉넉하면 화살표 자리 없이도 들어가 위가 안 드러난다 — 남는 폭이 0 인 표로 잰다
    import widgets
    from PySide6.QtWidgets import QHeaderView, QTableWidgetItem
    cols = ["가", "나나", "다다다", "라"]
    t = widgets.FitTableWidget(2, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    t.horizontalHeader().setMinimumSectionSize(0)
    t.setSortingEnabled(True)
    for r in range(2):
        for c in range(len(cols)):
            t.setItem(r, c, QTableWidgetItem("1"))
    t.show()
    t.resize(800, 200)
    _app.processEvents()
    t.set_content_widths()
    frame = t.width() - t.viewport().width()
    for c in range(len(cols)):
        t.sortByColumn(c, Qt.SortOrder.AscendingOrder)
        _app.processEvents()
        tight = sum(t._entry(t._base_cell_px)["widths"].values())
        t.resize(tight + frame, 200)  # 남는 폭 0 — 늘려 줄 여유가 없다
        _app.processEvents()
        hint = t.horizontalHeader().sectionSizeHint(c)
        assert t.columnWidth(c) >= hint, (cols[c], t.columnWidth(c), hint)
    t.close()


def test_ranker_tiles_shrink_to_fit():
    import widgets
    card = widgets.RankerCard()
    assert all(isinstance(v, widgets.FitLabel) for v in card._vals.values())


def test_dashboard_cards_filled_from_fixture():
    # 손계산: 승·무·패 3경기(오류 제외) — 득 1+4+1, 실 2+3+1. 오류 경기도 빠른 범위(76)
    # 4경기에는 들어간다(경기 목록과 같은 범위).
    # 결정력 = 골 6 ÷ 슛 17(2+8+7 — 오류 경기 슛은 안 센다)
    d = _win.dashboard
    assert d.wdl_bar.counts() == (1, 1, 1), d.wdl_bar.counts()
    assert d.lb_wdl_total.text() == "3경기 · 승률 33.3%(승÷전체)", d.lb_wdl_total.text()
    assert [lb.text() for lb in d.goal_nums] == ["2.00", "2.00", "35%"], [lb.text() for lb in d.goal_nums]
    assert "득실차 +0" in d.lb_goals_sub.text(), d.lb_goals_sub.text()
    assert len(d.versus_rows) == 7 and all(vb.nums.text() != "—" for vb in d.versus_rows)
    assert len(d.dots._items) == 4, d.dots._items
    assert d.dots._items[-1][0] == "오류"  # 오래된 것 → 최신, 최신이 오류 경기
    assert sum(d.minute_chart._series[0][1]) == 6, d.minute_chart._series  # 득점 6골
    assert len(d.timeband_bars._rows) == 4
    assert len(d.rival_bars._rows) == 4
    # 선제골 표본 3경기 < 최소 표본 — 숫자 대신 "—"
    assert all(g._value is None for g in d.gauges), [g._value for g in d.gauges]
    assert d.kpi_wdl.scope.text() == f"{_win._scope_text()} 4경기", d.kpi_wdl.scope.text()
    assert d.head.scope.text() == "이번 시즌", d.head.scope.text()
    assert d.clutch.scope.text().startswith(_win._scope_text()), d.clutch.scope.text()
    assert d.trend.scope.text() == f"{_win._scope_text()} · 하루 단위", d.trend.scope.text()   # 77 U16 — 켤 때 일


def test_dashboard_cards_keep_their_own_scope():
    # 빠른 범위를 2경기로 좁혀도 승부처·15분·시간대·분석은 시즌 범위 그대로여야 한다
    # (상세 페이지와 같은 숫자). 픽스처는 둘이 같아서 좁혀야만 드러난다.
    d = _win.dashboard
    before_goals = sum(d.minute_chart._series[0][1])
    before_bands = list(d.timeband_bars._rows)
    before_gauges = [g._note for g in d.gauges]
    before_rate = (d.lb_rate.text(), d.lb_rate_delta.text())
    before_rivals = list(d.rival_bars._rows)
    with _range("games", 2):
        assert d.kpi_wdl.scope.text() == f"{_win._scope_text()} · 최근 2경기", d.kpi_wdl.scope.text()
        # 승률은 시즌 기준 — 빠른 범위를 좁혀도 안 바뀐다
        assert (d.lb_rate.text(), d.lb_rate_delta.text()) == before_rate
        assert d.rival_bars._rows == before_rivals  # 자주 만난 상대도 시즌 기준
        assert d.rivals.scope.text().startswith(_win._scope_text()), d.rivals.scope.text()
        assert len(d.dots._items) == 2
        assert sum(d.minute_chart._series[0][1]) == before_goals
        # 개수만 보면 좁힌 2경기가 같은 시간대에 몰렸을 때 차이가 안 보인다 — 값 그대로 비교
        assert d.timeband_bars._rows == before_bands, (d.timeband_bars._rows, before_bands)
        assert [g._note for g in d.gauges] == before_gauges, [g._note for g in d.gauges]
        assert d.clutch.scope.text().endswith(f"{len(_win._matches)}경기"), d.clutch.scope.text()


def test_range_chips_cut_range_screens():
    # 76 — 위쪽 바 [1일] 버튼을 실제로 눌러(신호 경로) 경기 목록·대시보드·왼쪽 아래 글이 같이 줄어드는지.
    # 상대 전적은 시즌 그대로(U11). 픽스처가 이틀 이상에 걸쳐야 1일이 좁아진다 — 아니면 이 테스트는 아무것도 못 잰다
    want = core.window_of(_win._matches, "days", 1)
    assert 0 < len(want) < len(_win._matches), "픽스처가 하루에 몰려 1일 범위가 좁아지지 않는다"
    i = _win.cb_range.findData(("days", 1))
    assert i > 0, [_win.cb_range.itemData(k) for k in range(_win.cb_range.count())]
    opp_before = {r.label for r in _win.opp_list.rows()}
    try:
        _win.cb_range.buttons[i].click()
        assert [m.match_id for m in _win._slice()[0]] == [m.match_id for m in want]
        assert len(_win._match_cards) == len(want), len(_win._match_cards)
        assert "최근 1일" in _win.dashboard.kpi_wdl.scope.text(), _win.dashboard.kpi_wdl.scope.text()
        assert f"최근 1일 {len(want)}경기" in _win.lb_sub.text(), _win.lb_sub.text()
        assert {r.label for r in _win.opp_list.rows()} == opp_before
    finally:
        _win.cb_range.buttons[0].click()
    assert len(_win._match_cards) == len(_win._matches) and _win.cb_range.currentData() == ("all", 0)


def test_top_name_field_lists_recent_searches():
    # 76 — "← 검색" 을 빼고 시작 화면에만 있던 최근 검색을 위쪽 이름 칸 목록으로(사용자 26). 누르면 펼치고, 고르면 그 이름으로 조회
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    import store
    assert not [b for b in _win.top_bar.findChildren(QPushButton) if "검색" in b.text()], "위쪽 바에 ← 검색 이 남았다"
    ed = _win._recent_edit
    assert ed in _win._nick_edits
    conn = store.open_db(config.DB_PATH)
    called = []
    orig = _win._on_search
    try:
        for i, nick in enumerate(("최근검색가", "최근검색나")):
            store.upsert_account(conn, f"recent-test-{i}", nick)
            # last_seen 은 초 단위라 같은 초에 둘이 같아진다 — 먼 미래로 순서를 박는다(다른 계정보다 앞에)
            conn.execute("UPDATE accounts SET last_seen = ? WHERE ouid = ?", (f"2999-01-01T00:00:0{i}", f"recent-test-{i}"))
        conn.commit()
        _win._refresh_recent()
        names = _win._recent_model.stringList()
        assert names[:2] == ["최근검색나", "최근검색가"], names   # 최신순
        comp = ed.completer()
        assert comp is not None and comp.model() is _win._recent_model
        QTest.mouseClick(ed, Qt.MouseButton.LeftButton)
        assert comp.popup().isVisible(), "이름 칸을 눌러도 목록이 안 펼쳐졌다"
        comp.popup().hide()
        _win._on_search = lambda: called.append([e.text() for e in _win._nick_edits])
        comp.activated.emit("최근검색가")
        assert called == [["최근검색가"] * len(_win._nick_edits)], called
    finally:
        _win._on_search = orig
        for i in range(2):
            store.remove_account(conn, f"recent-test-{i}")
        conn.close()
        for e in _win._nick_edits:
            e.setText(_win._nick)
        _win._refresh_recent()


def test_range_keys_match_slice_users():
    # RANGE_KEYS(범위를 바꾸면 다시 그리는 키)와 실제로 _slice() 를 쓰는 그리기가 같아야 한다 — 빠지면 버튼을 눌러도
    # 그 화면이 옛 범위로 남고(조용히 틀림), 남으면 시즌 화면을 괜히 다시 그린다
    import inspect
    users = set()
    for key, fn in _win._renderers().items():
        src = inspect.getsource(fn) if fn.__name__ == "<lambda>" else inspect.getsource(getattr(fn, "__func__", fn))
        if fn.__name__ == "<lambda>":
            src = src[src.index(f'"{key}"'):].split("\n")[0]   # 사전 한 줄 — 같은 줄의 다른 키를 안 섞게
        if "self._slice(" in src:
            users.add(key)
    assert users == set(_win.RANGE_KEYS), (users ^ set(_win.RANGE_KEYS))


def _weeks_spec(n_weeks):
    # 2026-09-07(월)부터 주마다 하루(월요일)에 10판 — 주 n개 · 일 n개 · 9월~10월이라 달은 1~2개
    return [(datetime(2026, 9, 7, 10, i) + timedelta(days=7 * w), "승" if (w + i) % 3 else "패", True)
            for w in range(n_weeks) for i in range(10)]


def test_render_all_still_redraws_score_tab_after_grade_split():
    """77 C — 옛 _render_trend 가 _render_elo 를 같이 불러 [점수·예측] 이 검색·시즌 전환마다 다시 그려졌다.
    승률 부분을 [기간별 성적] 으로 뺀 뒤에도 _render_all 이 그 길을 지켜야 한다(빠지면 그 탭이 조용히 옛 값/빈 채)."""
    calls = []
    real = _win._render_elo
    _win._render_elo = lambda: calls.append(1)
    try:
        _win._render_all()
    finally:
        del _win._render_elo
    assert calls, "_render_all 이 [점수·예측](_render_elo)을 안 그린다"
    assert _win._render_elo == real


def test_period_units_block_below_min_points_and_dashboard_follows():
    """77 A·U16 — 점이 PERIOD_MIN_POINTS(4) 미만인 단위는 회색 + 말풍선 "점 N개" · 고른 단위가 막히면 아래로(일은 안 막는다) ·
    대시보드 승률 흐름 = 페이지가 고른 단위의 같은 점 · 그래프와 표가 같은 단위."""
    assert config.PERIOD_MIN_POINTS == 4
    seg, d = _win.seg_period, _win.dashboard
    with _SynthScope(_synth_matches(_weeks_spec(4))):
        _win._go_page("기간별 성적")
        _win._period_unit = "일"
        _win._render_period_page()
        assert seg.buttons[1].isEnabled(), "주 4개(경계)인데 막혔다"
        assert not seg.buttons[2].isEnabled() and "점이 1개" in seg.buttons[2].toolTip(), seg.buttons[2].toolTip()
        assert seg.buttons[0].isEnabled() and seg.current() == 0
        seg.buttons[1].click()   # 신호 경로
        assert _win._period_unit == "주" and seg.current() == 1
        assert len(_win.period_chart._points) == 4 and _win.tbl_period.rowCount() == 4
        assert _win.period_chart._points[0][0] == "09/07~09/13", _win.period_chart._points   # 오래된 것부터
        assert _win.period_chart._ma is None, "이동평균은 일 단위만"
        _win._render_dashboard()
        assert d.trend_chart._points == _win.period_chart._points, (d.trend_chart._points, _win.period_chart._points)
        assert d.trend.scope.text() == f"{_win._scope_text()} · 주 단위", d.trend.scope.text()
        # 3주(경계 밑) — 주가 막히고 골라 둔 주는 일로 내려간다(페이지·대시보드 같이)
        _win._matches = _synth_matches(_weeks_spec(3))
        _win._render_period_page()
        assert not seg.buttons[1].isEnabled() and "점이 3개" in seg.buttons[1].toolTip(), seg.buttons[1].toolTip()
        assert _win._period_unit == "일" and seg.current() == 0
        assert [p[0] for p in _win.period_chart._points] == ["09/07", "09/14", "09/21"], _win.period_chart._points
        _win._render_dashboard()
        assert d.trend.scope.text().endswith("· 하루 단위") and d.trend_chart._points == _win.period_chart._points


def test_period_season_unit_ignores_season_combo():
    """77 A — 시즌 단위는 늘 누적 전체(_matches_all) — 시즌 콤보로 _matches 를 좁혀도 시즌끼리 견준다. 표는 시즌 표로."""
    from datetime import date as _d
    allm = _synth_matches(_weeks_spec(4))
    seasons = [sn.Season(93, "시즌 3", _d(2026, 9, 21), _d(2026, 9, 28)),
               sn.Season(92, "시즌 2", _d(2026, 9, 14), _d(2026, 9, 21)),
               sn.Season(91, "시즌 1", _d(2026, 9, 7), _d(2026, 9, 14))]
    keep = (_win._matches_all, _win._rank_seasons)
    seg = _win.seg_period
    try:
        _win._matches_all, _win._rank_seasons = allm, seasons
        with _SynthScope(allm[:10]):   # 시즌 콤보로 마지막 주만 고른 꼴
            _win._go_page("기간별 성적")
            _win._period_unit = "일"
            _win._render_period_page()
            assert seg.buttons[3].isEnabled(), "시즌 4개인데 막혔다 — _matches(한 주)로 셌다"
            assert not seg.buttons[1].isEnabled()   # 주는 시즌 범위(한 주)라 막힌다
            seg.buttons[3].click()
            assert _win._period_unit == "시즌"
            pts = _win.period_chart._points
            assert [p[0] for p in pts] == ["2026 시즌 1", "2026 시즌 2", "2026 시즌 3", "진행 중"], pts
            assert sum(p[2] for p in pts) == 40, pts
            assert _win.tbl_period.isHidden() and not _win.tbl_seasons.isHidden() and _win.tbl_seasons.rowCount() == 4
            assert "누적 전체 기준" in _win.lb_streaks.text(), _win.lb_streaks.text()
            _win._render_dashboard()
            assert _win.dashboard.trend.scope.text() == "누적 전체 · 시즌 단위", _win.dashboard.trend.scope.text()
            assert _win.dashboard.trend_chart._points == pts
    finally:
        _win._matches_all, _win._rank_seasons = keep
        _win._period_unit = "일"
        _win._render_period_page()


def test_dashboard_rivals_skip_unknown_nickname():
    # 닉네임 빈 경기("-")를 한 사람으로 묶으면 실데이터에서 "-" 가 1위가 됐다
    from dataclasses import replace
    from dashboard import DashboardInput
    d = _win.dashboard
    ms = [replace(m, opponent="-") for m in _win._matches] + list(_win._matches)
    d.render(DashboardInput(ouid=_win._ouid, range_matches=ms, range_details=_win._details,
                            scope_matches=ms, scope_details=_win._details, scope_name="t"))
    names = [r[0] for r in d.rival_bars._rows]
    assert "-" not in names and names, names
    _win._render_dashboard()


def test_opponents_page_uses_season_scope_not_range():
    # 빠른 범위를 1경기로 좁혀도 상대 전적은 시즌 범위 전체를 센다(대시보드 '자주 만난 상대'와 같게)
    lst = _win.opp_list
    shown = []
    orig_show, orig_info = _win._show_match_detail, app_main.QMessageBox.information
    # 65 M3 — 경기 상세 창(스쿼드 탭의 상대 칸)으로 연다 · 그 상대와의 가장 최근 경기
    _win._show_match_detail = lambda d, tab="요약", side="opp": shown.append(
        (core.match_sides(d, _OUID)[1].get("nickname"), tab, side))
    # 못 찾으면 뜨는 안내 창은 모달이라 offscreen 에서 영원히 멈춘다 — 기록만 남긴다
    app_main.QMessageBox.information = lambda *a, **k: shown.append("못 찾음 창")
    _win._range_spec = lambda: ("games", 1)
    try:
        _win._render_all()
        in_range = {m.opponent for m in _win._slice()[0]}
        in_scope = {m.opponent for m in _win._matches}
        assert len(in_range) < len(in_scope), (in_range, in_scope)  # 픽스처가 차이를 만들어야 의미가 있다
        names = {r.label for r in lst.rows()}
        assert names == in_scope, (names, in_scope)
        # 78 ① — 필터가 안 걸렸으면 머리 글(범위·더블클릭 안내)이 없다
        assert _win.lb_opponent_note.isHidden() and not _win.lb_opponent_note.text(), _win.lb_opponent_note.text()
        # 빠른 범위 밖 상대도 더블클릭하면 스쿼드가 열린다
        outside = next(iter(in_scope - in_range))
        row = next(r for r in range(lst.rowCount()) if lst.row(r).key == outside)
        _win._on_opponent_double_clicked(lst.model().index(row, 0))
        assert shown == [(outside, "스쿼드", "opp")], shown
    finally:
        _win._show_match_detail, app_main.QMessageBox.information = orig_show, orig_info
        del _win._range_spec
        _win._render_all()


def test_season_note_only_when_table_missing():
    # 78 ① — 시즌표가 있으면 글이 없다(옛 설명 · 오류 경기 수는 지웠다) · 못 받았으면 그 이유만
    import seasons as sn_
    from datetime import date as d_
    saved = _win._rank_seasons
    _win._rank_seasons = [sn_.Season(no=1, name="시즌 1", start=d_(2025, 1, 1), end=d_(2025, 6, 1))]
    try:
        _win._render_seasons()
        assert _win.lb_season_note.isHidden() and not _win.lb_season_note.text(), _win.lb_season_note.text()
        _win._rank_seasons = []
        _win._render_seasons()
        assert not _win.lb_season_note.isHidden() and "시즌표를 받지 못했습니다" in _win.lb_season_note.text()
    finally:
        _win._rank_seasons = saved
        _win._render_seasons()


def test_dashboard_gauge_shows_value_once_sample_is_enough():
    import dashboard
    d = _win.dashboard
    old = dashboard.MIN_GAUGE_GAMES
    try:
        dashboard.MIN_GAUGE_GAMES = 1
        _win._render_dashboard()
        # 픽스처 선제골 3경기 1승 1무 1패 — 승÷전체 = 33%(A6 섞임 식이었다면 50%)
        assert d.gauges[0]._value is not None and round(d.gauges[0]._value) == 33, \
            d.gauges[0]._value
    finally:
        dashboard.MIN_GAUGE_GAMES = old
        _win._render_dashboard()


def test_dashboard_cards_navigate():
    from PySide6.QtTest import QTest
    d = _win.dashboard
    targets = [c for c in d.cards if c.target]
    assert len(targets) >= 9, len(targets)
    for c in targets:
        _win._go_page("승률 그래프", "점수·예측")  # 마지막에 본 탭이 달라도 카드는 카드가 적은 탭으로
        _win._go_page("대시보드")
        _app.processEvents()
        QTest.mouseClick(c, Qt.MouseButton.LeftButton)
        _app.processEvents()
        menu, tab = c.target
        want = (menu, tab if tab else (_win._page_tabs[menu].names()[0] if menu in _win._page_tabs else None))
        assert _win._current_view() == want, (c.title.text(), c.target, _win._current_view())
    _win._go_page("승률 그래프", "등급")
    _win._go_page("대시보드")


def test_dashboard_targets_exist():
    """대시보드 카드 대상 (메뉴, 탭) 이 NAV 에 있다 — 이름을 바꾸면 카드가 조용히 아무 데도 안 간다.
    탭이 있는 메뉴는 탭까지 적는다(안 적으면 마지막에 본 탭으로 열린다)."""
    views = _views_of_nav(app_main.MainWindow.NAV)

    def bad(targets):
        return [t for t in targets if not isinstance(t, tuple) or t not in views
                or t[0] in config.HIDDEN_NAV_UNTIL_READY]
    planted = [("승률 그래프", None), ("승률 그래프", "없는 탭"), "경기 목록"]
    assert bad(planted) == planted, bad(planted)
    keep = config.HIDDEN_NAV_UNTIL_READY
    config.HIDDEN_NAV_UNTIL_READY = ("랭킹 추이",)  # 숨긴 메뉴를 가리키는 카드도 잡는다(지금은 숨긴 메뉴가 없다)
    try:
        assert bad([("랭킹 추이", None)]) == [("랭킹 추이", None)]
    finally:
        config.HIDDEN_NAV_UNTIL_READY = keep
    targets = [c.target for c in _win.dashboard.cards if c.target]
    assert targets and not bad(targets), bad(targets)


def test_charts_draw_empty_and_single():
    import charts
    cases = [
        (charts.AreaTrendChart(), [[], [("01/01", 50.0, 3)]]),
        (charts.DonutChart(), [[], [("승", 1, T.CHART_UP)]]),
        (charts.GroupedBarChart(), [([], []), (["0–15"], [("득점", [0], T.CHART_UP)])]),
        (charts.ResultDots(), [[], [("승", "")]]),
        (charts.HBarList(), [[], [("오전", None, "경기 없음", "")]]),
    ]
    for w, datas in cases:
        w.resize(300, 200)
        for data in datas:
            if isinstance(data, tuple):
                w.set_data(*data)
            else:
                w.set_data(data)
            img = w.grab().toImage()
            assert not img.isNull(), type(w).__name__
    g = charts.RingGauge("x")
    g.resize(96, 96)
    for v in (None, 0.0, 58.0, 100.0):
        g.set_data(v, "3경기")
        assert not g.grab().toImage().isNull()


def test_trend_marks_max_min_and_average():
    # 픽스처 일별: 01/01 패(1경기 0%) · 01/02 승+무(2경기 — 섞인 묶음이라 무승부를 빼 100%) · 01/03 오류뿐(0경기).
    # 평균은 승 합 ÷ 승률 분모(rated) 합 = 1/2 — 날짜별 승률의 단순 평균(50%)과 같아 보이니 아래 경기 수 가중(66.7)도 아니어야.
    # 최저는 01/01 — 경기 없는 01/03 을 0% 로 세면 "다 졌다"로 읽힌다.
    c = _win.dashboard.trend_chart
    c.resize(600, 220)
    c.grab()
    # 승÷전체(2026-10-19): 01/02 승+무 = 50% · 평균 = 승 1 ÷ 3경기(날짜별 단순 평균 25% 가 아니어야)
    assert round(c.marks["avg"], 1) == 33.3, c.marks["avg"]
    assert c.marks["max"] == (1, "50.0%"), c.marks["max"]
    assert c.marks["min"] == (0, "0.0%"), c.marks["min"]
    # [기간별 성적] '평균 승률' 카드와 같은 숫자(한 집단 식이라 * 없음)
    assert f"{c.marks['avg']:.1f}%" == _win.card_period_avg.value.text(), \
        (c.marks["avg"], _win.card_period_avg.value.text())
    rects = c.marks["label_rects"] + [c.marks["avg_rect"]]
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), rects
    # 마지막 날이 최고점이고 평균과 가까우면 두 글자가 오른쪽 끝에서 부딪힌다 — 그때 평균 글자가 비켜야 한다
    import charts
    t = charts.AreaTrendChart()
    t.set_data([("a", 40.0, 10), ("b", 40.0, 10), ("c", 46.0, 10)])
    t.resize(400, 200)
    t.grab()
    rects = t.marks["label_rects"] + [t.marks["avg_rect"]]
    assert t.marks["max"][0] == 2
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), rects


# ── 1.3.1 8단계 — 추이 그래프 확장(D) ─────────────────────────────────────────
_AREA_CASES = [
    ([("01/01", 0.0, 1), ("01/02", 50.0, 2), ("01/03", 0.0, 0)], (600, 220)),
    ([("a", 40.0, 10), ("b", 40.0, 10), ("c", 46.0, 10)], (400, 200)),
    ([(f"09/{i:02d}", v, g) for i, (v, g) in enumerate(
        [(55.0, 20), (61.2, 31), (48.0, 25), (70.0, 10), (33.3, 3), (52.5, 40), (58.0, 50), (49.0, 45)], 1)],
     (500, 180)),
    ([("01/01", 50.0, 3)], (300, 200)),
    ([], (300, 200)),
]


def _rect(r):
    return tuple(round(v, 3) for v in (r.x(), r.y(), r.width(), r.height()))


def test_area_chart_defaults_unchanged():
    # 대시보드·선수 카드는 set_data(points) 만 부른다 — 확장 전 그림(tests/legacy_area_chart.py, 얼린 사본)과
    # 픽셀까지 같아야 한다. 좌표를 박지 않고 같은 프로세스에서 두 그림을 대조하므로 CI 러너 글꼴과 무관하다.
    import charts
    sys.path.insert(0, os.path.join(_ROOT, "tests"))
    from legacy_area_chart import LegacyAreaTrendChart
    for pts, (w, h) in _AREA_CASES:
        new, old = charts.AreaTrendChart(), LegacyAreaTrendChart()
        for c in (new, old):
            c.set_data(pts)
            c.resize(w, h)
        a, b = new.grab().toImage(), old.grab().toImage()
        if pts:
            assert a == b, ("기본값 그림이 바뀌었다", pts[:2], (w, h))
        else:
            # 빈 그래프는 86 C4 에서 일부러 바꿨다(아이콘 + 문구 · charts.paint_empty — test_empty_state_draws_icon_86).
            # 얼린 사본은 charts 를 안 읽는 약속이라 고치지 않고, 여기서는 '바뀌었다' 를 단언한다(되돌리면 빨개진다)
            assert a != b, "빈 그래프에 아이콘이 안 그려졌다(86 C4)"
        om = getattr(old, "marks", {})
        assert {k: (_rect(v) if hasattr(v, "intersects") else v) for k, v in new.marks.items() if k != "label_rects"} \
            == {k: (_rect(v) if hasattr(v, "intersects") else v) for k, v in om.items() if k != "label_rects"}, \
            (new.marks, om)
        assert [(_rect(r), t) for r, t in new._hits] == [(_rect(r), t) for r, t in old._hits]
        assert new.minimumHeight() == old.minimumHeight()


_TIMELINE_CASES = [
    ([(12.5, True), (40.0, False), (88.2, True)], (600, 52)),
    ([(3.0, False), (101.5, True)], (600, 52)),     # 연장 — span 120
    ([(45.0, True), (45.0, False)], (420, 52)),
    ([], (600, 52)),
]


def test_goal_timeline_defaults_unchanged():
    """88 P2 — 슛을 안 주면(골만) GoalTimeline 그림이 얼린 사본(tests/legacy_goal_timeline.py)과 픽셀까지 같다."""
    import widgets
    sys.path.insert(0, os.path.join(_ROOT, "tests"))
    from legacy_goal_timeline import LegacyGoalTimeline
    for goals, (w, h) in _TIMELINE_CASES:
        new, old = widgets.GoalTimeline(), LegacyGoalTimeline(T.MODE)
        for c in (new, old):
            c.set_goals(goals)
            c.resize(w, h)
        assert new.grab().toImage() == old.grab().toImage(), ("골만 줄 때 그림이 바뀌었다", goals, (w, h))
        assert new.span == old.span


def test_area_chart_rank_line_k5():
    """57 K5 — 순위 점선: None 에서 끊기고 오른쪽 축은 가장 좋은·나쁜 순위 · 전부 None 이면 안 넘긴 것과 픽셀까지 같다."""
    import charts
    c = _drawn(charts.AreaTrendChart(), ranks=[120, None, 80, 95])
    assert c.marks["rank_segments"] == 2 and c.marks["rank_axis"] == (80, 120), c.marks
    assert "80위" in c._hits[2][1] and "위" not in c._hits[1][1], [t for _r, t in c._hits]
    a, b = _drawn(charts.AreaTrendChart(), ranks=[None] * 4), _drawn(charts.AreaTrendChart())
    assert a.grab().toImage() == b.grab().toImage() and "rank_segments" not in a.marks
    try:
        _drawn(charts.AreaTrendChart(), ranks=[1, 2])
        raise AssertionError("길이가 다른 순위를 받았다")
    except ValueError:
        pass


def _drawn(c, w=500, h=240, **kw):
    pts = kw.pop("pts", [("09/01", 40.0, 10), ("09/02", 55.0, 12), ("09/03", 62.0, 8), ("09/04", 48.0, 20)])
    c.set_data(pts, **kw)
    c.resize(w, h)
    c.grab()
    return c


def test_area_chart_axis_has_no_hardcoded_percent():
    import charts
    elo = [("09/01", 4196.2, None), ("09/02", 4231.0, None), ("09/05", 4180.5, None)]
    ax = charts.Axis.fit([v for _, v, _ in elo])
    assert ax.lo <= 4180.5 and ax.hi >= 4231.0 and len(ax.ticks) == 3, ax
    c = _drawn(charts.AreaTrendChart(), pts=elo, axis=ax, avg=False)
    texts = [t for _, t in c._hits] + [c.marks["max"][1], c.marks["min"][1]]
    assert not any("%" in t for t in texts), texts
    assert c.marks["max"] == (1, "4,231"), c.marks["max"]
    assert c.marks["min"] == (2, "4,180"), c.marks["min"]  # 경기 수 None 점도 최고·최저 후보(전부)
    assert "avg" not in c.marks and "avg_rect" not in c.marks, c.marks
    assert c._hits[0][1] == "09/01 · ELO 4,196", c._hits[0][1]  # 경기 수 None 이면 "(…경기)" 없음
    # 축 글자도 % 가 없다 — 승률 축이면 왼쪽 여백 폭이 "100%" 기준이라 ELO 숫자(4,200)가 안 들어간다
    assert "%" not in ax.tick_text(ax.hi) and "%" not in ax.text(ax.lo)


def test_area_chart_options_each_change_the_drawing():
    import charts
    from datetime import date as _d
    # baseline — 눈금(50)이면 그 격자선을 진하게(글자 따로 없음), 눈금 밖(45)이면 점선 + 글자(겹침 규칙 안)
    c = _drawn(charts.AreaTrendChart(), baseline=50)
    top, foot = c._hits[0][0].top(), c._hits[0][0].bottom()
    kind, y = c.marks["baseline"]
    assert kind == "grid" and abs(y - (top + (foot - top) / 2)) < 0.01, (kind, y, top, foot)
    assert "baseline_rect" not in c.marks
    c = _drawn(charts.AreaTrendChart(), baseline=45)
    assert c.marks["baseline"][0] == "dashed"
    rects = c.marks["label_rects"] + [c.marks["avg_rect"]]
    assert c.marks["baseline_rect"] in c.marks["label_rects"]
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), rects
    assert "baseline" not in _drawn(charts.AreaTrendChart()).marks
    # ma — None 에서 끊긴다 · 툴팁에 이동평균
    c = _drawn(charts.AreaTrendChart(), ma=[50.0, None, 60.0, 58.5])
    assert (c.marks["ma_points"], c.marks["ma_segments"]) == (3, 2), c.marks
    assert c._hits[3][1] == "09/04 · 승률 48.0% · 7일 평균 58.5% (20경기)", c._hits[3][1]
    assert c._hits[1][1] == "09/02 · 승률 55.0% (12경기)", c._hits[1][1]
    # counts — 경기 있는 점마다 막대, 가장 큰 값만 숫자 · 66 P6: 선 뒤 막대라 그래프가 줄지 않는다(아래 띠 없음)
    plain = _drawn(charts.AreaTrendChart())
    c = _drawn(charts.AreaTrendChart(), pts=[("a", 40.0, 3), ("b", 50.0, 0), ("c", 60.0, 5)], counts=True)
    assert c.marks["count_bars"] == 2 and c.marks["count_peak"] == (2, "5경기"), c.marks
    assert c.minimumHeight() == plain.minimumHeight()
    assert c._hits[0][0].bottom() == plain._hits[0][0].bottom(), "막대 띠만큼 그래프가 위로 줄었다"
    top, bottom = c._hits[0][0].top(), c._hits[0][0].bottom()
    assert min(c._count_tops.values()) >= bottom - (bottom - top) * c.COUNT_FRAC - 0.01, c._count_tops
    # x_dates — 날짜 간격(하루 · 9일)이 순번 간격으로 찌그러지지 않는다
    days = [_d(2026, 9, 1), _d(2026, 9, 2), _d(2026, 9, 11)]
    pts3 = [("09/01", 40.0, 5), ("09/02", 50.0, 5), ("09/11", 60.0, 5)]
    c = _drawn(charts.AreaTrendChart(), pts=pts3, x_dates=days)
    w0, w1, w2 = (c._hits[i][0].width() for i in range(3))
    assert w0 * 5 < w2 and w1 > w2, (w0, w1, w2)  # 첫 점 띠는 하루의 반, 마지막 점은 9일의 반
    even = _drawn(charts.AreaTrendChart(), pts=pts3)
    assert abs(even._hits[0][0].width() - even._hits[2][0].width()) < 0.01
    # ref_series — 축 밖이면 끝에 ▲, 안이면 이름·값 · 날짜가 없으면 안 그린다
    elo = [("09/01", 4196.0, None), ("09/02", 4231.0, None), ("09/11", 4180.0, None)]
    ax = charts.Axis.fit([v for _, v, _ in elo])
    refs = [("200위", [(_d(2026, 8, 30), 4500.0), (_d(2026, 9, 5), 4515.0)], T.CHART_DOWN),
            ("1만 위", [(_d(2026, 9, 1), 4200.0)], T.CHART_NEUTRAL)]
    c = _drawn(charts.AreaTrendChart(), pts=elo, axis=ax, avg=False, x_dates=days, ref_series=refs)
    assert c.marks["ref"] == [("200위", "above", "▲ 200위 4,515"), ("1만 위", "in", "1만 위 4,200")], c.marks["ref"]
    rects = c.marks["label_rects"]
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), rects
    c = _drawn(charts.AreaTrendChart(), pts=elo, axis=ax, avg=False, ref_series=refs)
    assert [s for _, s, _ in c.marks["ref"]] == ["no-dates", "no-dates"]
    assert c.marks["max"] == (1, "4,231")  # 기준선을 못 그려도 본 그림은 그린다


def test_trend_page_uses_area_chart_with_counts_and_ma():
    import charts
    _restore_account()
    _win._go_page("기간별 성적")
    c = _win.period_chart
    assert isinstance(c, charts.AreaTrendChart), type(c)
    c.resize(700, 300)
    c.grab()
    # 픽스처 일별: 01/01 1경기 · 01/02 2경기 · 01/03 오류뿐(0경기) — 막대는 경기 있는 날만
    assert c.marks["baseline"][0] == "grid" and c.marks["count_bars"] == 2, c.marks
    assert c._ma == [None, None, None], c._ma  # 3경기 — 이동평균 표본(MIN_COND) 미달이라 끊김
    # 배선 — 이동평균은 경기에서 직접(그날 날짜를 넘긴다), 결과가 그대로 선이 된다
    seen = []
    real = app_main.core.moving_win_rate

    def spy(matches, days, **kw):
        seen.append((len(matches), list(days), kw))
        return [40.0] * len(days)
    app_main.core.moving_win_rate = spy
    keep_rule = config.WIN_RATE_DRAWS_IN
    config.WIN_RATE_DRAWS_IN = False   # 배선은 기본값이 아닌 값(True)으로 잰다 — 승÷전체(기본)에선 mixed 가 늘 False 라 빈 검사가 된다
    try:
        _win._render_period_page()
        c.grab()
    finally:
        app_main.core.moving_win_rate = real
        config.WIN_RATE_DRAWS_IN = keep_rule
    # mixed 는 그래프 점과 같은 값 — 픽스처는 섞인 묶음이라 True(기본 False 와 다른 값으로 배선을 잰다)
    assert seen == [(len(_win._matches), [_date(2026, 1, 1), _date(2026, 1, 2), _date(2026, 1, 3)],
                     {"min_n": app_main.core.MIN_COND, "mixed": True})], seen
    assert c.marks["ma_points"] == 3, c.marks
    _win._render_period_page()


def test_trend_page_groups_not_squeezed():
    # 세로는 아무도 안 쟀다(창 최소 크기 테스트는 폭만) — MIN_WINDOW 에서 그룹마다 최소 높이가 지켜지고,
    # 넘치는 만큼은 세로 스크롤로 밀려야 한다.
    try:
        _at_size(*app_main.MIN_WINDOW)
        _win._go_page("승률 그래프")
        _app.processEvents()
        frame = _win.pages.currentWidget()
        groups = [_win.gb_division, _win.gb_sc]   # 77 C — 승률 묶음은 [기간별 성적] 으로 갔다
        bad = [(g.title(), g.height(), g.minimumSizeHint().height()) for g in groups
               if g.height() < g.minimumSizeHint().height()]
        assert not bad, bad
        inner = frame.widget().minimumSizeHint().height() if frame.widget() else 0
        if inner > frame.viewport().height():
            assert frame.verticalScrollBar().isVisible(), "내용이 창보다 긴데 세로 스크롤이 없다 — 눌렸다"
    finally:
        _at_size(1600, 900)
        _win._go_page("대시보드")


def _g1_season(end=None):
    """등급 계단(66 G1)은 "지금 시즌" 만 그린다 — 픽스처(2026-01-01~03)가 지금 시즌에 들게 끝난 시즌 하나를 둔다.
    기본 시즌표(시즌 9 · 02-01 끝)면 픽스처가 지난 시즌이라 계단이 빈다. 되돌릴 값을 돌려준다."""
    from datetime import date as _d
    keep = _win._rank_seasons
    _win._rank_seasons = [sn.Season(88, "시즌 8", _d(2025, 10, 1), end or _d(2026, 1, 1))]
    return keep


def _sc_details(div_by_id: dict):
    """픽스처 상세의 내 등급을 바꾼 사본 — {matchId 끝 4자리: division}."""
    import copy
    out = copy.deepcopy(_DETAILS)
    for d in out:
        div = div_by_id.get(d["matchId"][-4:])
        if div is not None:
            for p in d["matchInfo"]:
                if p["ouid"] == _OUID:
                    p["division"] = div
    return out


def test_divstay_tab_counts_and_note():
    # 2.6.1 N2 — 0001·0002 900 → 0003 800(승격) → 0004 900(강등). 01-02 는 800 으로 끝난 날
    _win._go_page("성적 진단", "등급 체류")
    _win._render_divstay(_sc_details({"0003": 800}))
    t = _win.tbl_divstay
    rows = {t.item(r, 0).text(): [t.item(r, c).text() for c in range(1, 4)] for r in range(t.rowCount())}
    name = lambda i: _win._division_names.get(i, str(i))  # noqa: E731
    assert rows == {name(800): ["1", "1", "1"], name(900): ["3", "2", "1"]}, rows
    assert _win.lb_divstay_note.isHidden(), _win.lb_divstay_note.text()   # 78 ① 승격·강등 횟수 설명은 지웠다
    _win._render_divstay([])
    assert not _win.lb_divstay_note.isHidden() and "등급 정보가 있는 경기가 없습니다" in _win.lb_divstay_note.text()
    _win._render_divstay(_win._details)   # 원래 데이터로 되돌린다


def test_rewards_tab_counts_and_note():
    # 43단계 A2 — 900 승 + 900 몰수승 + 800 승 + 1000 승(0) + 900 패 → 챔스 2판 30 + 슈챔 1판 20 = 50 FC
    def det(i, result, div):
        return {"matchId": f"r{i}", "matchType": 52, "matchDate": f"2026-09-0{i}T12:00:00",
                "matchInfo": [{"ouid": _OUID, "division": div, "matchDetail": {"matchResult": result}}]}
    ds = [det(1, "승", 900), det(2, "몰수승", 900), det(3, "승", 800), det(4, "승", 1000), det(5, "패", 900)]
    _win._go_page("성적 진단", "FC 보상")
    _win._render_rewards(ds)
    t = _win.tbl_rewards
    rows = {t.item(r, 0).text(): [t.item(r, c).text() for c in range(1, 3)] for r in range(t.rowCount())}
    name = lambda i: _win._division_names.get(i, str(i))  # noqa: E731
    assert rows == {name(900): ["2", "30"], name(800): ["1", "20"]}, rows
    assert "FC 50" in _win.lb_rewards_summary.text() and "100판당 1,000" in _win.lb_rewards_summary.text(), _win.lb_rewards_summary.text()
    # 등급 기록이 없는 승·패는 FC 합계에 못 넣으니 100판당 분모에서도 뺀다 — 넣으면 50/7 → 714 로 내려간다
    _win._render_rewards(ds + [det(6, "승", None), det(7, "패", None)])
    assert "100판당 1,000" in _win.lb_rewards_summary.text(), _win.lb_rewards_summary.text()
    _win._render_rewards([])
    assert _win.tbl_rewards.rowCount() == 0 and "셀 경기가 없습니다" in _win.lb_rewards_summary.text()
    _win._render_rewards(_win._details)   # 원래 데이터로 되돌린다


def test_opp_rank_tab_gates_and_draws():
    # 43단계 N23 — 막는 이유 한 줄(수집 꺼짐 · 동의 · 내 계정) → 줄이 없음 → 하한 미만 → 표
    import copy
    cfg_keep = (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED)
    keep = (_win._trade_snapshot, _win._rank_read, _win._details)
    fake_state = {"key_fp": None, "my_ouid": _OUID}
    _win._trade_snapshot = lambda: {"state": {**fake_state, "key_fp": store.key_fingerprint(config.API_KEY)},
                                    "names": {_OUID: "나"}}
    txt = lambda: _win.lb_opprank_note.text()  # noqa: E731
    try:
        _win._go_page("상대 전적", "상대 순위")
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED = True, False, config.OPP_RANK_NOTICE_VERSION
        _win._render_opp_rank()
        assert "랭킹 수집을 켜면" in txt() and _win.tbl_opprank.rowCount() == 0, txt()
        config.RANK_COLLECT, config.NOTICE_ACCEPTED = True, config.OPP_RANK_NOTICE_VERSION - 1
        _win._render_opp_rank()
        assert "새 이용 안내" in txt(), txt()
        config.NOTICE_ACCEPTED = config.OPP_RANK_NOTICE_VERSION
        _win._trade_snapshot = lambda: {"state": {"key_fp": store.key_fingerprint(config.API_KEY), "my_ouid": "누군가"},
                                        "names": {"누군가": "남"}}
        _win._render_opp_rank()
        assert "내 계정(남)" in txt(), txt()
        _win._trade_snapshot = lambda: {"state": {"key_fp": store.key_fingerprint(config.API_KEY)}, "names": {}}
        _win._render_opp_rank()
        assert "내 계정이 확인되지 않았습니다" in txt(), txt()
        _win._trade_snapshot = lambda: {"state": {"key_fp": store.key_fingerprint(config.API_KEY), "my_ouid": _OUID},
                                        "names": {_OUID: "나"}}
        _win._rank_read = lambda fn: {}
        _win._render_opp_rank()
        assert "아직 채운 경기가 없습니다" in txt(), txt()
        # 하한(OPP_MIN_KNOWN) 밑이면 표를 안 낸다 — 이 판수는 픽스처 4경기라 줄 4개를 모두 알아도 하한 밑이다
        ids = [d["matchId"] for d in _win._details]
        _win._rank_read = lambda fn: {i: (200, 6) for i in ids}
        _win._render_opp_rank()
        assert f"{config.OPP_MIN_KNOWN}판부터" in txt() and _win.tbl_opprank.rowCount() == 0, txt()
        # 하한을 낮추면 같은 줄로 표가 나온다 — 하한이 배선돼 있다
        saved_min = config.OPP_MIN_KNOWN
        config.OPP_MIN_KNOWN = 1
        try:
            _win._render_opp_rank()
        finally:
            config.OPP_MIN_KNOWN = saved_min
        t = _win.tbl_opprank
        assert t.rowCount() == len(config.OPP_RANK_BANDS), t.rowCount()
        assert t.item(0, 0).text() == "1~200위" and int(t.item(0, 1).text()) == 3, [t.item(0, c).text() for c in range(6)]
        # 표가 나오면 설명 칸은 비고 숨는다(78 ② — 상태만 남김 · 위 상태 글들은 그대로 뜬다)
        assert txt() == "" and _win.lb_opprank_note.isHidden(), txt()
    finally:
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED = cfg_keep
        _win._trade_snapshot, _win._rank_read, _win._details = keep


def test_opp_table_row_min_is_wired_to_config():
    # 승패 5판 줄은 기본(10판) 문턱이면 "표본 5" 를 붙이고, 문턱을 5 로 내리면 안 붙인다 · 30판 줄은 어느 쪽이든 안 붙는다.
    # 첫 단언이 "문턱을 안 읽는 코드"를 못 잡으므로(상수가 박혀 있어도 기본값에선 같다) 문턱을 바꾼 두 번째 단언이 배선을 잰다
    import rankmeta
    bands = [rankmeta.OppBand("작은", win=3, lose=2, mixed=False), rankmeta.OppBand("큰", win=20, lose=10, mixed=False)]
    text = lambda: [r[5][0] for r in _win._opp_table_rows(bands)]  # noqa: E731
    saved = config.OPP_ROW_MIN
    try:
        small, big = text()
        assert "표본 5" in small and "표본" not in big, (small, big)
        config.OPP_ROW_MIN = 5
        small, big = text()
        assert "표본" not in small and "표본" not in big, (small, big)
    finally:
        config.OPP_ROW_MIN = saved


def _live_widgets(layout):
    """레이아웃에 지금 들어 있는 위젯들 — findChildren 은 deleteLater 로 지운 줄도 아직 본다."""
    return [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget() is not None]


def test_goal_flow_tab_draws_a_bar_per_lead_with_games():
    # 2.7.1 N17 — 경기가 있는 최대 리드 줄마다 막대 하나 + 되짚은 경기 수 · 제외 안내
    from PySide6.QtWidgets import QLabel
    from widgets import WdlBar
    _win._go_page("승부처 분석", "리드·골 흐름")
    _win._render_goal_flow(_win._details)
    gf = app_main.core.goal_flow(_win._details, _win._ouid)
    bars = [b for w in _live_widgets(_win.box_goalflow["lead"]) for b in w.findChildren(WdlBar)]
    assert len(bars) == sum(1 for r in gf.leads if r.games) > 0, (len(bars), [r.games for r in gf.leads])
    # 경기가 없으면 막대 대신 한 줄
    _win._render_goal_flow([])
    assert not [b for w in _live_widgets(_win.box_goalflow["lead"]) for b in w.findChildren(WdlBar)]
    assert [w.text() for w in _live_widgets(_win.box_goalflow["lead"]) if isinstance(w, QLabel)] == [
        "골 순서를 되짚을 수 있는 경기가 없습니다."]
    _win._render_goal_flow(_win._details)


def test_attack_route_tab_draws_a_row_per_route_and_waits_for_enough_shots():
    # 2.7.1 N18 — 슛이 하한 이상이면 루트마다 한 줄 · 모자라면 표 대신 "아직 루트별로 나누지 못합니다" · 표시 구간이 바뀌면 새로 그린다
    _win._go_page("슛 맵", "공격 루트")
    assert _win._current_view() == ("슛 맵", "공격 루트")
    old = app_main.config.N18_MIN_SHOTS
    try:
        app_main.config.N18_MIN_SHOTS = 1
        _win._render_attack_route(_win._details)
        ar = app_main.core.attack_routes(_win._details, _win._ouid)
        assert ar.mine_total > 0 and ar.enough, ar.mine_total
        assert _win.tbl_route.rowCount() == len(app_main.core.ROUTES), _win.tbl_route.rowCount()
        assert [_win.tbl_route.item(r, 0).text() for r in range(_win.tbl_route.rowCount())] == list(app_main.core.ROUTES)
        assert _win.lb_route_note.isHidden(), _win.lb_route_note.text()   # 78 ① 표가 있으면 설명 없음
        _check_elisions([_win.tbl_route])     # 채운 표가 최소 창 폭에서 안 잘린다
        app_main.config.N18_MIN_SHOTS = ar.mine_total + 1
        _win._render_attack_route(_win._details)
        assert _win.tbl_route.rowCount() == 0 and "아직 루트별로 나누지 못합니다" in _win.lb_route_note.text(), _win.lb_route_note.text()
        assert not _win.lb_route_note.isHidden()
    finally:
        app_main.config.N18_MIN_SHOTS = old
    _win._render_attack_route([])
    assert _win.tbl_route.rowCount() == 0
    _win._go_page("대시보드")


def _synth_matches(spec):
    """spec = [(날짜 datetime, 결과, draw_possible)] → MatchSummary 목록(최신 먼저 — 화면 목록과 같은 순서)."""
    out = [app_main.MatchSummary(match_id=f"s{i}", match_date=when, match_type=52, my_nickname="me", opponent="x",
                                 result=res, my_goals=0, opp_goals=0, possession=50, shoot_total=0, shoot_effective=0,
                                 pass_try=0, pass_success=0, rating=0.0, draw_possible=dp)
           for i, (when, res, dp) in enumerate(spec)]
    return sorted(out, key=lambda m: m.match_date, reverse=True)


class _SynthScope:
    """화면이 들고 있는 경기 목록을 지어낸 것으로 갈아 끼웠다 되돌린다([기간별 성적] 단위까지)."""

    def __init__(self, matches):
        self.matches = matches

    def __enter__(self):
        self.saved = (_win._matches, _win._period_unit, _win._streak_done, _win._streak_req)
        _win._matches = self.matches
        return self

    def __exit__(self, *exc):
        _win._matches = self.saved[0]
        _win._period_unit = self.saved[1]
        _win._streak_done, _win._streak_req = self.saved[2], self.saved[3]
        _win._render_period_page()
        _win._invalidate("dashboard")


def _days_spec():
    # 10일 × 하루 20판: 앞 5일(9/1~9/5)은 전승, 뒤 5일(9/6~9/10)은 전패 → 10일 평균 50% · 최근 5일 평균 0%
    return [(datetime(2026, 9, 1 + d, 10, i), "승" if d < 5 else "패", True) for d in range(10) for i in range(20)]


def _wd_spec(spec):
    """_days_spec 꼴 → 저장 경기(WinDivision) — 운 범위(66 G4)·달력은 _season_wins() 를 본다."""
    return [st_mod.WinDivision(when, 900, res, False) for when, res, _dp in spec]


class _LuckScope(_SynthScope):
    """경기 목록(범위)과 저장 경기(_season_wins)를 같은 spec 으로 갈아 끼운다 · 시즌표도."""

    def __init__(self, spec, seasons=()):
        super().__init__(_synth_matches(spec))
        self.rows, self.seasons = _wd_spec(spec), list(seasons)

    def __enter__(self):
        self.keep2 = (_win.__dict__.get("_season_wins"), _win._rank_seasons)
        _win._season_wins = lambda: self.rows
        _win._rank_seasons = self.seasons
        return super().__enter__()

    def __exit__(self, *exc):
        if self.keep2[0] is None:
            _win.__dict__.pop("_season_wins", None)
        _win._rank_seasons = self.keep2[1]
        return super().__exit__(*exc)


def test_luck_tab_season_range_cards_and_dots_66():
    """66 G4 · U2 — 날 = 성적 진단 범위의 경기 있는 날 전부(그래프 '최근 N일' 과 끊음) · 기준 = 그날이 속한 시즌 승률 ·
    시즌표가 없으면 저장된 전체(M12) · 여러 시즌이면 "시즌마다"."""
    from datetime import date as _d
    with _LuckScope(_days_spec()):
        _win._go_page("성적 진단", "운 범위")
        _win._render_luck(_win._matches)
        rows = _win.luck_dots._rows
        assert len(rows) == 10 and rows[0].label == "09/10" and rows[-1].label == "09/01", [r.label for r in rows]
        # 시즌표 없음 → 저장된 전체 50% · 20판 전승/전패라 10일 모두 띠 밖
        assert _win.card_luck_base.cap.text() == "저장된 전체 승률" and _win.card_luck_base.value.text() == "50.0%"
        assert _win.card_luck_base.sub.text() == "200판", _win.card_luck_base.sub.text()
        assert _win.card_luck_out.value.text() == "10일" and "운이면 0." in _win.card_luck_out.sub.text(), \
            _win.card_luck_out.sub.text()
        assert all(r.arrow and r.judged and abs(r.base - 50.0) < 1e-9 for r in rows), rows
    # 한 시즌(진행 중 — 시즌표에 없는 시즌) → "시즌 평균" · 이번 시즌
    with _LuckScope(_days_spec(), [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 8, 31))]):
        _win._render_luck(_win._matches)
        assert _win.card_luck_base.cap.text() == "시즌 평균" and _win.card_luck_base.value.text() == "50.0%"
        assert _win.card_luck_base.sub.text() == "이번 시즌 · 200판", _win.card_luck_base.sub.text()
    # 두 시즌(09/06 경계) → 앞 5일 기준 100% · 뒤 5일 0% — 날마다 그 시즌이라 띠 밖 날 0
    with _LuckScope(_days_spec(), [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 6))]):
        _win._render_luck(_win._matches)
        assert _win.card_luck_base.value.text() == "시즌마다" and "2개 시즌" in _win.card_luck_base.sub.text()
        assert {round(r.base) for r in _win.luck_dots._rows} == {0, 100}, _win.luck_dots._rows
        assert _win.card_luck_out.value.text() == "0일", _win.card_luck_out.value.text()


def test_luck_counts_season_by_time_not_date():
    """시즌은 종료일 8시에 바뀐다 — 그날 새벽 경기는 옛 시즌. 날짜로 묶으면 이번 시즌이 589판(콤보는 583)이었다(11-04 실데이터)."""
    from datetime import date as _d
    end = _d(2026, 9, 6)
    early = [(datetime(2026, 9, 6, 1, i), "승", True) for i in range(6)]          # 경계 전 새벽 — 옛 시즌
    old = [(datetime(2026, 9, 5, 10, i), "패", True) for i in range(14)]
    new = [(datetime(2026, 9, 6 + d, 10, i), "승" if i < 5 else "패", True) for d in range(3) for i in range(20)]
    season = sn.Season(90, "시즌 3", _d(2026, 8, 1), end)
    with _LuckScope(old + early + new, [season]):
        all_matches = list(_win._matches)
        # 이번 시즌을 고른 상태 — 범위는 경계 뒤 경기만
        _win._matches = [m for m in all_matches if m.match_date >= datetime(2026, 9, 6, 8)]
        assert len(_win._matches) == 60, len(_win._matches)
        _win._render_luck(_win._matches)
        assert _win.card_luck_base.sub.text() == "이번 시즌 · 60판", _win.card_luck_base.sub.text()
        assert _win.card_luck_base.value.text() == "25.0%", _win.card_luck_base.value.text()   # 새벽 6승을 넣으면 28.8%
        rows = {r.label: r for r in _win.luck_dots._rows}
        assert rows["09/06"].games == 20, rows["09/06"].games                       # 새벽 6판을 넣으면 26
        assert all(abs(r.base - 25.0) < 1e-9 for r in rows.values()), rows
        # 옛 시즌을 고른 상태 — 경계일 새벽만 들어가고, 그날 뒤 경기는 안 든다 · "시즌마다" 가 아니다
        _win._matches = [m for m in all_matches if m.match_date < datetime(2026, 9, 6, 8)]
        _win._render_luck(_win._matches)
        assert _win.card_luck_base.sub.text() == f"{season.label} · 20판", _win.card_luck_base.sub.text()
        assert _win.card_luck_base.value.text() == "30.0%", _win.card_luck_base.value.text()   # 6승 14패
        assert _win.luck_dots._rows[0].label == "09/06" and _win.luck_dots._rows[0].games == 6, _win.luck_dots._rows[0]
        # 달력 — 칸은 하루 그대로지만 기준선·시즌 요약은 시각으로
        _win._matches = all_matches
        _win._cal_month = (2026, 9)
        _win._render_calendar()
        t = _win.tbl_cal_summary
        assert t.item(1, 2).text() == "25.0%", t.item(1, 2).text()                  # [승률] 줄 · 이번 시즌
        assert t.horizontalHeaderItem(2).text() == "이번 시즌", t.horizontalHeaderItem(2).text()
        cell = _win.cal_grid._cells[6]
        assert "26판" in cell.sub and "시즌 승률 25.0%" in cell.tip, cell
        # 이 계정 100판당 — 시즌 시작일 새벽 승리는 지난 시즌 몫이다(날짜로 자르면 26판)
        keep_cache = _win._reward_cache, core.win_divisions
        _win._reward_cache = None
        rows = _wd_spec(old + early + new)
        assert sum(1 for w in rows if w.when.date() >= end) == 66, "새벽 경기가 없으면 이 검사가 빈다"
        core.win_divisions = lambda _d, _o: rows
        try:
            got = _win._my_reward(end.isoformat())
            assert got.games == 60, got.games
        finally:
            _win._reward_cache, core.win_divisions = keep_cache


def test_luck_tab_judgement_matches_calendar_66():
    """U2 — 같은 날이면 달력 ▲▼ 와 같은 판정(재료·묶음·하한이 같다 · M14) · 묶음 규칙을 꺼도(A6 섞임) 같아야."""
    from datetime import date as _d
    spec = []
    for day, (w, d, l) in enumerate([(9, 0, 1), (1, 1, 8), (3, 0, 0), (4, 1, 0), (5, 0, 5), (12, 2, 6)], start=1):
        k = 0
        for res, cnt in (("승", w), ("무", d), ("패", l)):
            for _ in range(cnt):
                spec.append((datetime(2026, 9, day, 9) + timedelta(minutes=k), res, True))
                k += 1
    keep = config.WIN_RATE_DRAWS_IN
    try:
        for rule in (True, False):
            config.WIN_RATE_DRAWS_IN = rule
            with _LuckScope(spec, [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 8, 31))]):
                _win._render_luck(_win._matches)
                luck = {int(r.label[-2:]): (r.judged, r.arrow) for r in _win.luck_dots._rows}
                _win._cal_month = (2026, 9)
                _win._render_calendar()
                cal = {day: (not c.weak, c.arrow) for day, c in _win.cal_grid._cells.items()}
                assert luck == cal, (rule, luck, cal)
                assert any(a for _j, a in luck.values()), "빈 검사 — 띠 밖 날이 하나는 있어야"
                assert any(not j for j, _a in luck.values()), "빈 검사 — 미달 날이 하나는 있어야(3판 날)"
    finally:
        config.WIN_RATE_DRAWS_IN = keep
        _win._cal_month = None


def test_luck_required_table_uses_whole_range_not_30_days_66():
    """M1 — "몇 판부터" 의 기준 p 는 범위 전체. 일수를 안 넘기면 win_rate_trend 기본 30일이 조용히 들어간다."""
    spec = [(datetime(2026, 8, 1 + d, 10, i), "승", True) for d in range(10) for i in range(20)]
    spec += [(datetime(2026, 8, 11, 10) + timedelta(days=d), "패", True) for d in range(30)]
    with _LuckScope(spec):
        _win._render_luck(_win._matches)
        # 78 ① 기준 글은 지웠다 — 표 값으로 잰다. 먼저 "30일로 잘랐다면 값이 달랐다" 를 단언(안 그러면 빈 검사)
        core = app_main.core
        whole = core.daily_luck(core.win_rate_trend(_win._matches, days=40))
        cut30 = core.daily_luck(core.win_rate_trend(_win._matches))
        assert abs(whole.p - 200 / 230) < 1e-9 and [r.n for r in whole.required] != [r.n for r in cut30.required], \
            (whole.p, cut30.p)
        t = _win.tbl_luck_required
        got = [t.item(r, 1).text() for r in range(t.rowCount())]
        assert got == [f"{r.n:,}판" for r in whole.required], (got, [r.n for r in whole.required])


def test_luck_dot_rows_columns_and_hits_66():
    """두 열은 폭이 TWO_COL_W 이상일 때만(1280 에서 트랙이 안 눌리게) · 높이는 열 수로 · 줄마다 툴팁."""
    ch = charts.LuckDotRows()
    rows = [charts.LuckRow(f"09/{i:02d}", 20, 50.0 + i, 30.0, 70.0, 50.0, i != 3, (1 if i == 9 else 0), f"t{i}")
            for i in range(1, 11)]
    ch.set_data(rows)
    ch.show()      # 숨은 위젯은 resizeEvent 를 보일 때까지 미룬다
    ch.resize(ch.TWO_COL_W + 10, 400)
    _app.processEvents()
    assert ch.cols == 2 and ch.minimumHeight() == 5 * ch.ROW_H + 4, (ch.cols, ch.minimumHeight())
    ch.grab()
    assert len(ch._hits) == 10 and [t for _a, t in ch._hits][0] == "t1"
    assert ch._hits[5][0].left() > ch._hits[0][0].right(), "둘째 열이 첫 열 오른쪽이 아니다"
    ch.resize(ch.TWO_COL_W - 10, 400)
    _app.processEvents()
    assert ch.cols == 1 and ch.minimumHeight() == 10 * ch.ROW_H + 4, (ch.cols, ch.minimumHeight())
    ch.set_data([])
    ch.grab()
    assert ch._hits == []
    ch.close()


def test_luck_tab_streak_states():
    # 계산 중 → 작업자 결과 → 늦은 요청 번호는 버림 → 계산 실패
    import habits
    spec = [(datetime(2026, 9, 1, 10, 0) + timedelta(minutes=10 * i), "승" if i < 25 else "패", True) for i in range(50)]
    ms = _synth_matches(spec)
    texts = lambda: " | ".join(w.text() for w in _live_widgets(_win.box_luck_streak))  # noqa: E731
    with _SynthScope(ms):
        _win._go_page("성적 진단", "운 범위")
        key = app_main.core.streak_key(ms)
        _win._streak_done = None
        _win._invalidate("luck")
        assert "계산 중" in texts(), texts()
        _win._streak_req = 7
        _win._on_streak_ready((6, key, habits.streak_baseline(ms)))           # 늦게 끝난 옛 요청
        assert _win._streak_done is None and "계산 중" in texts()
        _win._on_streak_ready((7, key, habits.streak_baseline(ms)))
        assert "최장 연승 25" in texts() and "우연으로 보기 어렵습니다" in texts(), texts()
        assert "최장 연패 25" in texts()
        _win._on_streak_ready((7, key, None))
        assert "계산하지 못했습니다" in texts(), texts()
        # 경기 목록이 바뀌면(키가 달라지면) 들고 있던 결과를 쓰지 않는다
        _win._streak_done = (("다른", 0), habits.streak_baseline(ms))
        _win._invalidate("luck")
        assert "계산 중" in texts(), texts()


def test_render_all_launches_streak_worker_once_per_list():
    # 새 검색·범위·시즌이 바뀐 직후(_render_all) 한 번 띄운다. 같은 경기 목록이면 다시 안 띄운다(결과가 들어 있으니)
    keep = app_main.HabitsWorker.start
    started = []
    app_main.HabitsWorker.start = lambda self: started.append(self)      # 스모크는 QThread.start 를 막는다 — 띄운 것만 센다
    try:
        _win._streak_done = None
        before = _win._streak_req
        _win._render_all()
        assert len(started) == 1 and _win._streak_req == before + 1, (len(started), before, _win._streak_req)
        w = started[0]
        _win._on_streak_ready((w._req, w._key, None))
        _win._render_all()
        assert len(started) == 1, "같은 목록이면 다시 띄우지 않는다"
        _win._streak_done = None
        _win._render_all()
        assert len(started) == 2 and started[0]._cancel, "앞 계산은 취소하고 새로 띄운다"
    finally:
        app_main.HabitsWorker.start = keep
        _win._habits_workers = [x for x in _win._habits_workers if x not in started]


def test_habits_worker_failure_is_one_line_and_noted():
    keep = (app_main.core.streak_baseline, crashlog.note, getattr(app_main.HabitsWorker, "_noted", False))
    got, notes = [], []
    try:
        def boom(*a, **k):
            raise RuntimeError("섞기 실패")
        app_main.core.streak_baseline = boom
        crashlog.note = lambda tag, e: notes.append((tag, str(e)))
        app_main.HabitsWorker._noted = False
        w = app_main.HabitsWorker(3, ("k", 1), [])
        w.streak_ready.connect(got.append)
        w.run()
        w2 = app_main.HabitsWorker(4, ("k", 1), [])
        w2.streak_ready.connect(got.append)
        w2.run()
        assert [g[2] for g in got] == [None, None] and [g[0] for g in got] == [3, 4], got
        assert notes == [("habits", "섞기 실패")], notes       # crash.log 에는 실행당 한 번만
        # 취소하면 신호를 안 낸다
        app_main.core.streak_baseline, app_main.HabitsWorker._noted = keep[0], keep[2]
        w3 = app_main.HabitsWorker(5, ("k", 1), [])
        w3.streak_ready.connect(got.append)
        w3.cancel()
        w3.run()
        assert len(got) == 2, got
    finally:
        app_main.core.streak_baseline, crashlog.note, app_main.HabitsWorker._noted = keep


def test_shutdown_stops_habits_workers():
    class _Busy(app_main.HabitsWorker):
        def isRunning(self):
            return not self._cancel

        def wait(self, *a):
            return True

    w = _Busy(1, ("k", 0), [])
    _win._habits_workers.append(w)
    try:
        left = _win.shutdown(fast=True)
        assert w in left and w._cancel, "종료 정리 목록에 HabitsWorker 가 없다"
    finally:
        _win._habits_workers = [x for x in _win._habits_workers if x is not w]


def test_habits_tab_flags_a_real_effect_and_stays_quiet_otherwise():
    # 2.7.1 N20 — 시간대 칸 하나에 효과를 심으면 그 칸이 칠해지고, 효과가 없으면 "차이가 안 보입니다"
    def spec(effect):
        out, n = [], 0
        for hour in range(0, 24, 3):
            for k in range(40):
                win = (k < 36) if (effect and hour == 0) else (k < 20)
                out.append((datetime(2026, 1, 1, hour) + timedelta(days=n), "승" if win else "패", True))
                n += 1
        return out

    _win._go_page("성적 진단", "경기 습관")
    hour_tbl, hour_verdict = _win.habit_boxes[2]
    with _SynthScope(_synth_matches(spec(False))):
        _win._render_habits(_win._matches)
        assert [t.rowCount() for t, _ in _win.habit_boxes] == [4, 4, 8], [t.rowCount() for t, _ in _win.habit_boxes]
        assert hour_verdict.text().startswith("차이가 안 보입니다"), hour_verdict.text()
        assert all(hour_tbl.item(r, 0).foreground().color().name() == hour_tbl.item(r, 1).foreground().color().name()
                   for r in range(8))
    with _SynthScope(_synth_matches(spec(True))):
        _win._render_habits(_win._matches)
        assert hour_verdict.text().startswith("차이가 뚜렷합니다"), hour_verdict.text()
        from PySide6.QtGui import QColor
        green = QColor(T.GOOD).name()
        assert hour_tbl.item(0, 0).foreground().color().name() == green, "효과를 심은 칸이 안 칠해졌다"
        assert hour_tbl.item(1, 0).foreground().color().name() != green
        assert hour_tbl.item(0, 2).text().startswith("90.0%"), hour_tbl.item(0, 2).text()
        _check_elisions([t for t, _ in _win.habit_boxes])


def test_habits_tab_empty_and_note():
    with _SynthScope(_synth_matches([(datetime(2026, 9, 1, 10), "승", True)])):
        _win._render_habits(_win._matches)
        assert all("판정하지 않습니다" in v.text() for _, v in _win.habit_boxes), [v.text() for _, v in _win.habit_boxes]


def test_analysis_chip_row_shows_strengths_and_weaknesses():
    # 2.7.1 N19 — 칩은 강점·약점으로 글자 앞에 말이 붙고, 없으면 이유 한 줄
    import habits
    keep = app_main.core.strength_chips
    m = habits.METRICS
    strong = habits.Chip(habits.Diff(m["tackle_rate"], 500, 72.0, 68.0, 0.5, 400, 400))
    weak = habits.Chip(habits.Diff(m["finish"], 500, 36.0, 40.0, 0.5, 400, 400))

    def chips():
        return {w.property("chip"): w.text() for w in _live_widgets(_win.box_chips) if w.property("chip")}
    try:
        app_main.core.strength_chips = lambda t, b=None: [weak, strong]
        _win._render_chips()
        assert chips() == {"finish": "약점 결정력 -4.0%p", "tackle_rate": "강점 태클 성공 +4.0%p"}, chips()
        app_main.core.strength_chips = lambda t, b=None: []
        _win._render_chips()
        assert list(chips()) == ["none"] and "뚜렷한 강점·약점 없음" in chips()["none"], chips()
    finally:
        app_main.core.strength_chips = keep
        _win._render_chips()


class _FakeDist:
    """ownerdist.OwnerDist 꼴 — 모든 값이 백분위 p(양 끝이면 칩 · 안쪽 끝도 같은 값이라 SURE_P 를 통과)."""

    def __init__(self, round_id=7, p=5.0):
        self.round_id, self.ended_at, self.n_owners, self.p = round_id, "2026-10-12T03:00:00", 143, p
        self.values = {}

    def pct(self, key, x):
        return self.p


class _OwnerDistOn:
    """구단주 분포 허용(동의·웹·수집·토글·키) + 분포 읽기를 가짜로. 내 1000판은 창의 경기로 채운 DistBasis(바꿔 끼운 dist_basis)."""
    NAMES = ("WEB_DATA", "RANK_COLLECT", "NOTICE_ACCEPTED", "OWNER_DIST_ON", "API_KEY")

    def __init__(self, dist):
        self.dist, self.loads, self.bases = dist, [], []

    def __enter__(self):
        import habits
        self._saved = {k: getattr(config, k) for k in self.NAMES}
        self._core = (app_main.core.load_owner_dist, app_main.core.dist_basis)
        config.WEB_DATA = config.RANK_COLLECT = True
        config.NOTICE_ACCEPTED, config.OWNER_DIST_ON = config.NOTICE_VERSION, "2026-10-10T00:00:00"
        config.API_KEY = config.API_KEY or "k"

        def load(conn, now):
            self.loads.append(1)
            return self.dist

        def basis(dist, details_all, ouid):
            self.bases.append(dist.round_id)
            return habits.DistBasis(dist, habits.side_totals(_win._details, ouid))
        app_main.core.load_owner_dist, app_main.core.dist_basis = load, basis
        _win._dist_key = _win._narrate_key = None
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            setattr(config, k, v)
        app_main.core.load_owner_dist, app_main.core.dist_basis = self._core
        _win._dist_key = _win._narrate_key = None
        _win._render_all()


def _chip_widgets():
    return {w.property("chip"): w for w in _live_widgets(_win.box_chips) if w.property("chip")}


def test_owner_dist_chip_tooltip_head_and_off_switch():
    # 49단계 ③b — 분포를 쓸 수 있으면 결정력·태클은 분포 칩(하위 N%) · 툴팁에 인원·내 판수·기준일·"경향" · 머리가 두 기준을 다 말함
    with _OwnerDistOn(_FakeDist()) as od:
        _win._render_chips()
        w = _chip_widgets()
        assert "finish" in w and w["finish"].text() == "약점 결정력 하위 5%", {k: v.text() for k, v in w.items()}
        tip = w["finish"].toolTip()
        assert f"1~{config.OWNER_DIST_STRATA[-1]:,}위 구단주 143명 대비 하위 5%" in tip and "2026-10-12" in tip, tip
        assert "경향" in tip and "나 − 같은 경기 상대" in tip, tip
        head = _live_widgets(_win.box_chips)[0].text()
        assert "구단주 분포" in head and "같은 경기 상대" in head, head
        assert od.loads and od.bases == [7], (od.loads, od.bases)
        keep = app_main.core.strength_chips
        try:
            app_main.core.strength_chips = lambda t, b=None: []   # 칩이 없을 때 이유도 두 기준을 말한다
            _win._render_chips()
            none = _chip_widgets()["none"].text()
            assert f"양 끝 {config.OWNER_DIST_CHIP_P}%" in none and "유효슛 비율" in none, none
            assert "결정력·태클 성공은 1~" in none, none
            # 태클 분포가 층 표본 미달로 빠지면 태클은 같은 경기 상대 판정 — 문구도 그쪽으로(③b 리뷰 L2)
            od.dist.pct = lambda key, x: None if key == "tackle_rate" else 5.0
            _win._render_chips()
            none = _chip_widgets()["none"].text()
            assert "결정력은 1~" in none and "유효슛 비율·태클 성공은 차이" in none, none
        finally:
            app_main.core.strength_chips = keep
            od.dist.__dict__.pop("pct", None)
        config.OWNER_DIST_ON = ""   # 토글 끔 — 받아 둔 분포는 남아도 판정엔 안 쓴다
        n = len(od.loads)
        _win._render_chips()
        assert len(od.loads) == n, "꺼졌는데 분포를 읽었다"
        assert all("하위" not in x.text() and "상위" not in x.text() for x in _chip_widgets().values()), \
            [x.text() for x in _chip_widgets().values()]
        assert _live_widgets(_win.box_chips)[0].text() == "같은 경기 상대와 견주면"


def test_owner_dist_reaches_narrate_and_cache_follows_round():
    # 문장도 같은 basis — 캐시 키가 분포 바퀴를 따라간다(같은 바퀴면 다시 안 하고, 새 바퀴면 다시)
    calls = []
    real = app_main.core.narrate

    def spy(m, d, ouid, *a, **k):
        calls.append(k.get("basis"))
        return real(m, d, ouid, *a, **k)
    with _OwnerDistOn(_FakeDist(round_id=7)) as od:
        app_main.core.narrate = spy
        try:
            _win._narrate_scope()
            _win._narrate_scope()
            assert len(calls) == 1 and calls[0] is not None and calls[0].dist.round_id == 7, calls
            assert od.bases == [7], f"같은 바퀴·같은 경기인데 내 1000판을 다시 셌다: {od.bases}"
            od.dist = _FakeDist(round_id=8)
            _win._narrate_scope()
            assert len(calls) == 2 and calls[1].dist.round_id == 8, "새 바퀴인데 문장을 다시 안 냈다"
            config.OWNER_DIST_ON = ""
            _win._narrate_scope()
            assert len(calls) == 3 and calls[2] is None, "꺼졌는데 분포 문장 캐시를 그대로 썼다"
        finally:
            app_main.core.narrate = real


def test_owner_dist_basis_none_outside_manager_mode():
    # 감독모드 밖 매치 종류(공식경기 등)를 검색하면 내 감독모드 1000판이 없다 — 진짜 dist_basis 의 matchType 거르기
    with _OwnerDistOn(_FakeDist()) as od:
        app_main.core.dist_basis = od._core[1]
        keep = _win._details_all
        try:
            base = [{"matchId": f"m{i}", "matchType": config.DEFAULT_MATCH_TYPE, "matchInfo": []}
                    for i in range(config.OWNER_DIST_MATCHES)]
            _win._details_all = base
            assert _win._dist_basis() is not None, "대조군 — 감독모드 1000판이면 판정 재료가 있어야"
            _win._details_all = [dict(d, matchType=config.DEFAULT_MATCH_TYPE + 1) for d in base]
            assert _win._dist_basis() is None, "감독모드가 아닌 경기로 분포 판정을 했다"
        finally:
            _win._details_all = keep


class _PosFake:
    """ownerdist.PosDist 꼴 — 화면은 round_id·ended_at·cells[칸].n_owners 만 본다."""
    def __init__(self, round_id=7):
        self.round_id, self.ended_at = round_id, "2026-10-12T03:00:00"
        self.cells = {c: type("C", (), {"n_owners": 129})() for c in ("MF.atk", "DF.atk", "DF.def")}


def _poslevel_rows():
    t = _win.tbl_poslevel
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


def test_poslevel_empty_states_stage_names_and_cache():
    # 50단계 C4 — 빈 화면 문구(켜는 길을 숨기지 않는다) · 모집단 안에서만 5단계 이름 · 보류 사유 · 내 1000판 판정 캐시
    import habits
    _win._render_poslevel()
    assert _win.lb_poslevel_summary.text() == _win.POSLEVEL_OFF, "토글이 꺼졌는데 켜는 길을 말하지 않았다"
    keep = (app_main.core.load_pos_dist, app_main.core.pos_level, _win._poslevel_in_pop, config.OWNER_DIST_V2_NOTICE_VERSION)
    calls = []
    lv = habits.PosLevel(1000, (habits.PosCell("MF", "atk", 995, 73.4, 54.0, 81.0, 3, ""),
                                habits.PosCell("DF", "atk", 120, 34.0, 14.0, 63.0, None, "games"),
                                habits.PosCell("DF", "def", 995, 93.0, 76.0, 100.0, None, "range")))
    with _OwnerDistOn(_FakeDist()):
        try:
            # 관문 값이 자리 값 99 이던 때는 늘 "동의 전"이었다 — 확정(11) 뒤엔 동의 전 상태를 직접 만든다
            config.OWNER_DIST_V2_NOTICE_VERSION = config.NOTICE_ACCEPTED + 1
            for why, text in (("none", _win.POSLEVEL_NO_ROUND), ("thin", _win.POSLEVEL_THIN), ("v1", _win.POSLEVEL_NEED_NOTICE)):
                app_main.core.load_pos_dist = lambda conn, now, why=why: (None, why)
                _win._render_poslevel()
                assert _win.lb_poslevel_summary.text() == text and not _poslevel_rows(), (why, _win.lb_poslevel_summary.text())
            config.OWNER_DIST_V2_NOTICE_VERSION = config.NOTICE_ACCEPTED   # 이미 동의 — 다음 바퀴를 기다릴 뿐
            _win._render_poslevel()
            assert _win.lb_poslevel_summary.text() == _win.POSLEVEL_NEXT_ROUND
            pd = _PosFake()
            app_main.core.load_pos_dist = lambda conn, now: (pd, "")
            app_main.core.pos_level = lambda d, det, ouid: (calls.append(d.round_id), lv)[1]
            _win._poslevel_key = None
            _win._poslevel_in_pop = lambda: True
            _win._render_poslevel()
            rows = _poslevel_rows()
            assert [r[1] for r in rows] == ["에이스", f"판정 보류 (출전 {config.C4_MIN_LINE_GAMES}판 미만)",
                                            "판정 보류 (범위가 단계 경계에 걸침)"], rows
            assert rows[0][2:] == ["73", "54 ~ 81", "995판"], rows[0]
            assert _win.lb_poslevel_summary.text() == "미드필더 · 공격 에이스"
            _win._poslevel_in_pop = lambda: False
            _win._render_poslevel()
            assert _poslevel_rows()[0][1] == "—", "모집단 밖인데 5단계 이름을 냈다"
            assert f"{config.OWNER_DIST_STRATA[-1]:,}위 밖" in _win.lb_poslevel_summary.text()
            _win._poslevel_in_pop = lambda: None
            _win._render_poslevel()
            assert _poslevel_rows()[0][1] == "—" and "순위 기록이 없어" in _win.lb_poslevel_summary.text()
            # 판정 난 자리가 없을 때 — 사유는 칸마다 그대로(분포 부족을 "범위·출전"으로 뭉뚱그리지 않는다)
            keep_lv = lv
            lv = habits.PosLevel(1000, (habits.PosCell("MF", "atk", 995, why="dist"),) + keep_lv.cells[1:])
            _win._poslevel_in_pop = lambda: True
            _win._poslevel_key = None
            _win._render_poslevel()
            s = _win.lb_poslevel_summary.text()
            assert "미드필더 · 공격 분포 부족" in s and "수비수 · 수비 판정 보류 (범위" in s, s
            lv = keep_lv
            _win._poslevel_key = None
            _win._render_poslevel()   # 원래 판정으로 되돌린다(아래 캐시 단언의 전제)
            calls[:] = [7]
            assert calls == [7], f"같은 바퀴·같은 경기인데 내 1000판을 다시 셌다: {calls}"
            pd = _PosFake(round_id=8)
            _win._render_poslevel()
            assert calls == [7, 8], "새 바퀴인데 판정을 다시 안 냈다"
        finally:
            (app_main.core.load_pos_dist, app_main.core.pos_level, _win._poslevel_in_pop,
             config.OWNER_DIST_V2_NOTICE_VERSION) = keep
            if "_poslevel_in_pop" in vars(_win):
                del _win._poslevel_in_pop
            _win._poslevel_key = None


def test_poslevel_in_pop_reads_the_latest_rank():
    # 라벨 조건(계획 ⓗ) — 가장 최근 ELO 기록의 순위가 1~3000위 안인가. 경계 3000 은 안 · 3001 은 밖 · 순위 없는 줄은 건너뛴다
    # EloLoader 가 읽어 둔 self._elo 를 본다(DB 직접 읽기는 이번 검색 순위 저장 전 값으로 굳었다 — ③ 리뷰 [중])
    keep = _win._elo.pop(_OUID, None)
    try:
        assert _win._poslevel_in_pop() is None, "아직 안 읽었는데 판정했다"
        for rows, want in (([], None), ([{"rank": 5}, {"rank": None}], True),
                           ([{"rank": 10}, {"rank": config.OWNER_DIST_STRATA[-1]}], True),
                           ([{"rank": 10}, {"rank": config.OWNER_DIST_STRATA[-1] + 1}], False)):
            _win._elo[_OUID] = app_main.EloSeries(1, rows, {})
            assert _win._poslevel_in_pop() is want, (rows, want)
    finally:
        _win._elo.pop(_OUID, None)
        if keep is not None:
            _win._elo[_OUID] = keep


def test_poslevel_redraws_when_elo_arrives():
    # 이번 검색의 순위는 _save_elo 가 늦게 적는다 → EloLoader 가 끝나면(elo_ready) 라벨 조건을 다시 봐야 한다
    keep = _win._elo.get(_OUID)
    drawn = []
    _win._render_poslevel = lambda: drawn.append("poslevel")
    try:
        _win._on_elo_ready(_OUID, _elo_data([{"taken_at": "2026-10-18T10:00:00", "elo": 3000.0, "rank": 100}]))
        assert drawn == ["poslevel"], f"순위가 들어왔는데 자리별 수준을 다시 안 그렸다: {drawn}"
    finally:
        del _win._render_poslevel
        if keep is not None:
            _win._elo[_OUID] = keep


def test_owner_dist_changed_signal_redraws_every_dist_view():
    # 분포를 늦게 넣으면(바퀴가 끝남) 칩·문장·자리별 수준(50단계 C4)을 다시 그린다 — 예약의 dist_changed · 스스로 끔 switched_off
    keep_sched = _win._owner_sched
    drawn = []
    want = ["analysis", "dashboard", "poslevel"]
    sched = app_main.OwnerDistScheduler()
    try:
        _win.attach_owner_sched(sched)
        _win._render_analysis = lambda: drawn.append("analysis")
        _win._render_dashboard = lambda: drawn.append("dashboard")
        _win._render_poslevel = lambda: drawn.append("poslevel")
        sched.dist_changed.emit()
        assert sorted(drawn) == want, drawn
        drawn.clear()
        sched.switched_off.emit("fail")
        assert sorted(drawn) == want, f"스스로 꺼진 뒤 분포 화면이 남는다: {drawn}"
        drawn.clear()
        sched.sync = lambda start=False: None   # 끄는 길(수집·웹·동의·지우기·키) — .env 는 안 건드린다
        _win._sync_owner_dist()
        assert sorted(drawn) == want, f"끄는 길이 분포 화면을 안 거뒀다: {drawn}"
        drawn.clear()
        dlg = app_main.AboutDialog(_win)   # [정보] 토글 — 켜고 끄는 것도 다시 그린다
        keep_set = config.set_owner_dist
        config.set_owner_dist = lambda on, reason="": None
        try:
            dlg._on_owner_toggled(False)
        finally:
            config.set_owner_dist = keep_set
            dlg.deleteLater()
        assert sorted(drawn) == want, f"[정보] 토글이 분포 화면을 안 다시 그렸다: {drawn}"
    finally:
        del _win._render_analysis, _win._render_dashboard, _win._render_poslevel
        _win._owner_sched = keep_sched
        sched.deleteLater()
        _win._render_all()


def test_sc_records_list_and_nexon_line():
    keep_max = dict(_win._max_division)
    keep_seasons = _g1_season()
    try:
        # 0002(01-02 12:00) 900 → 0003(01-02 13:00) 800 → 0004(01-03) 900 : 진입 한 번 — 픽스처 원문은 UTC 라 화면은 KST 21:00·22:00(63)
        det = _sc_details({"0003": 800})
        _win._on_loaded(_MATCHES, det, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win.lb_sc_list.text() == "2026-01-02 · 1번 (22:00)", _win.lb_sc_list.text()
        # 계단 구간 900(2) · 800(1) · 900(1) — 슈챔 구간(번호 1) 시작에 세로 점선
        assert [(r.division, r.games) for r in _win.division_chart._runs] == [(900, 2), (800, 1), (900, 1)]
        assert _win.division_chart._markers == [1], _win.division_chart._markers
        # 넥슨 최근 달성이 목록의 진입과 60초 안이면 같은 것 — 줄을 안 낸다
        _win._on_max_division(_OUID, {"division": 800, "date": "2026-01-02", "at": "2026-01-02T22:00:30"})
        assert _win.lb_sc_nexon.isHidden(), _win.lb_sc_nexon.text()
        # 5분 어긋나면 목록에 없는 것
        _win._on_max_division(_OUID, {"division": 800, "date": "2026-01-02", "at": "2026-01-02T22:05:00"})
        assert not _win.lb_sc_nexon.isHidden()
        assert _win.lb_sc_nexon.text().startswith("넥슨 기록상 최근 달성 2026-01-02 (저장된 경기에서는 못 찾았습니다"), \
            _win.lb_sc_nexon.text()
        # 저장 범위보다 앞
        _win._on_max_division(_OUID, {"division": 800, "date": "2025-11-02", "at": "2025-11-02T10:00:00"})
        assert _win.lb_sc_nexon.text() == "넥슨 기록상 최근 달성 2025-11-02 (저장된 경기 밖)", _win.lb_sc_nexon.text()
        # 옛 값(at 없음)은 날짜로 비교
        _win._on_max_division(_OUID, {"division": 800, "date": "2026-01-02"})
        assert _win.lb_sc_nexon.isHidden()
        # 최고가 슈챔이 아니면(챔피언스) 줄이 없다
        _win._on_max_division(_OUID, {"division": 900, "date": "2025-11-02", "at": "2025-11-02T10:00:00"})
        assert _win.lb_sc_nexon.isHidden()
        # 첫 경기부터 슈챔이면 진입이 아니라 '기록 시작 때 이미'
        det = _sc_details({"0001": 800, "0002": 800, "0003": 800, "0004": 800})
        _win._on_loaded(_MATCHES, det, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win.lb_sc_list.text() == "기록 시작(2026-01-01) 때 이미 슈퍼 챔피언스", _win.lb_sc_list.text()
        # 기록 없음
        _restore_account()
        assert _win.lb_sc_list.text() == "저장된 경기에는 슈퍼 챔피언스 달성 기록이 없습니다"
    finally:
        _win._max_division = keep_max
        _win._rank_seasons = keep_seasons
        _restore_account()


def test_sc_one_game_run_on_division_chart_and_best_date_from_matches():
    """2026-10-19 사용자 — 슈챔에 올랐다 그날 내려온 것도 등급 그래프에 보이고(66 G1 — 한 판짜리 구간이 한 칸), 대시보드
    '최고티어 최근 달성일' 은 넥슨 값보다 저장된 경기의 진입이 늦으면 그 날짜."""
    keep_max, keep_names = dict(_win._max_division), dict(_win._division_names)
    keep_seasons = _g1_season()
    # 등급 이름 메타 — CI 에는 넥슨 키·캐시가 없어 비어 있다(이름을 모르면 최고티어 줄 자체가 None · 이 PC 에선 캐시로 통과했다)
    _win._division_names.update({800: "슈퍼 챔피언스", 900: "챔피언스"})
    try:
        # 0002(01-02 21:00) 800 → 0003(01-02 22:00) 900 : 그날 마지막은 챔스 — 하루 한 점(옛 그림)이면 슈챔이 안 보였다
        det = _sc_details({"0002": 800})
        _win._on_loaded(_MATCHES, det, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        dc = _win.division_chart
        assert [(r.division, r.games) for r in dc._runs] == [(900, 1), (800, 1), (900, 2)], dc._runs
        dc.grab()
        assert dc.marks["levels"] == [900, 800] and dc.marks["runs"] == 3, dc.marks
        assert dc.marks["head"] == (900, 800, "시즌 최고"), dc.marks   # 머리 줄 — 현재 챔스 · 시즌 최고 슈챔
        # 최고티어 날짜 — 넥슨이 옛 날짜면 저장된 진입 날짜 · 넥슨이 더 늦으면 넥슨 그대로
        _win._on_max_division(_OUID, {"division": 800, "date": "2025-12-01", "at": "2025-12-01T10:00:00"})
        assert "최근 달성일 2026-01-02 " in (_win._max_division_text() or ""), _win._max_division_text()
        _win._on_max_division(_OUID, {"division": 800, "date": "2026-02-01", "at": "2026-02-01T10:00:00"})
        assert "최근 달성일 2026-02-01 " in (_win._max_division_text() or ""), _win._max_division_text()
        _win._on_max_division(_OUID, {"division": 900, "date": "2025-12-01"})   # 최고가 챔스면 슈챔 진입을 안 끌어온다
        assert "최근 달성일 2025-12-01 " in (_win._max_division_text() or ""), _win._max_division_text()
    finally:
        _win._max_division = keep_max
        _win._division_names.clear()
        _win._division_names.update(keep_names)
        _win._rank_seasons = keep_seasons
        _restore_account()


def test_sc_nexon_line_arrives_after_loader_finishes():
    # maxdivision 신호는 finished_ok 뒤에 온다 — _render_grade_tab 에서만 그리면 넥슨 줄이 늘 빠진다. 진짜 로더로.
    tmp, saved = _loader_db(4)
    keep_names, keep_max = dict(_win._division_names), dict(_win._max_division)
    try:
        _win._ouid, _win._max_division = "옛계정", {}
        ld = app_main.MatchLoader(_MaxDivApi(), "닉", 52, want_max_division=True)
        ld.finished_ok.connect(_win._on_loaded)
        ld.max_division_ready.connect(_win._on_max_division)
        ld.run()
        _app.processEvents()
        assert _win._ouid == _OUID
        # _MAXDIV_ROWS 의 52 줄: 800 · 2026-07-05 — 저장된 4경기(01-01~01-03)엔 없다
        assert not _win.lb_sc_nexon.isHidden(), "로더가 끝난 뒤 온 최고 등급이 승률 그래프에 안 그려졌다"
        assert _win.lb_sc_nexon.text().startswith("넥슨 기록상 최근 달성 2026-07-05"), _win.lb_sc_nexon.text()
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
        _win._division_names, _win._max_division = keep_names, keep_max
        _restore_account()


def _week_matches():
    """월요일 시작 주 셋 — A(09-07 주) 4승(표본 미달) · B(09-14 주) 10경기 6승 · C(09-21 주) 9경기 3승."""
    out = []

    def mk(day, hour, result):
        base = _MATCHES[0]
        return models.MatchSummary(**{**base.__dict__, "match_id": f"w{day}{hour}{result}",
                                      "match_date": datetime(2026, 9, day, hour), "result": result})
    out += [mk(8, h, "승") for h in range(10, 14)]
    out += [mk(15, h, "승") for h in range(6)] + [mk(16, h, "패") for h in range(4)]
    out += [mk(22, h, "승") for h in range(3)] + [mk(23, h, "패") for h in range(6)]
    out.sort(key=lambda m: m.match_date, reverse=True)
    return out


def test_period_table_marks_best_worst_and_keeps_tint_after_sort():
    from PySide6.QtGui import QColor
    t = _win.tbl_period
    rate_col = _win.PERIOD_COLUMNS.index("승률")
    vs_col = _win.PERIOD_COLUMNS.index("평균 대비")
    games_col = _win.PERIOD_COLUMNS.index("경기")
    ms = _week_matches()
    try:
        # 헤더에 정렬이 남아 있는 상태에서 다시 그린다 — 채우자마자 재정렬되면 색이 엉뚱한 행에 간다(표 함정 1번)
        t.sortByColumn(games_col, Qt.SortOrder.AscendingOrder)
        mixed = app_main.core.mixed_of_matches(ms)
        _win._fill_period_table(app_main.period_stats(ms, days=7, mixed=mixed), mixed)
        assert t.rowCount() == 3
        by_label = {t.item(r, 0).text(): r for r in range(3)}
        a, b, c = by_label["09/07~09/13"], by_label["09/14~09/20"], by_label["09/21~09/27"]
        # 4판짜리 A 가 100% 지만 최고가 아니다(후보는 MIN_COND 판 이상) — 흐리고 칠하지 않는다
        assert not t.item(a, rate_col).font().bold()
        assert t.item(a, rate_col).foreground().color() == QColor(T.TEXT_DIM)
        assert t.item(a, rate_col).toolTip().startswith("표본이 4경기")
        assert t.item(b, rate_col).font().bold() and t.item(c, rate_col).font().bold()
        # 평균(13/23 = 56.5%)보다 높은 B 는 HEAT_GOOD(초록), 낮은 C 는 HEAT_BAD(빨강) 쪽(79) — 그 행에 붙어 있다
        overall = 13 / 23 * 100
        f = _win.HEAT_FLOOR
        exp = lambda diff, end: _win._blend(  # noqa: E731
            T.PANEL, end, f + (1 - f) * min(abs(diff) / _win.PERIOD_HEAT_FULL_PP, 1.0)).name()
        assert t.item(b, rate_col).background().color().name() == exp(60 - overall, T.HEAT_GOOD)
        assert t.item(c, rate_col).background().color().name() == exp(100 / 3 - overall, T.HEAT_BAD)
        assert t.item(b, vs_col).text() == f"{60 - overall:+.1f}%p", t.item(b, vs_col).text()
        assert t.isSortingEnabled()
    finally:
        t.setSortingEnabled(False)
        t.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        _win._render_period_page()


def test_season_table_compares_with_previous_season():
    keep = (_win._rank_seasons, _win._matches_all)
    old = sn.Season(no=89, name="시즌 8", start=_date(2025, 10, 1), end=_date(2025, 12, 1))
    new = sn.Season(no=90, name="시즌 9", start=_date(2025, 12, 1), end=_date(2026, 2, 1))
    base = _MATCHES[0]
    olds = [models.MatchSummary(**{**base.__dict__, "match_id": f"o{i}", "result": "승",
                                   "match_date": datetime(2025, 11, 10, 9 + i)}) for i in range(2)]
    try:
        _win._rank_seasons = [new, old]
        _win._matches_all = list(_MATCHES) + olds
        _win._render_seasons()
        t = _win.tbl_seasons
        cols = _win.SEASON_COLUMNS
        rows = {t.item(r, 0).text(): r for r in range(t.rowCount())}
        assert set(rows) == {"2025 시즌 9", "2025 시즌 8"}, rows
        # 80 — 기간은 시즌 칸 말풍선으로 · 점유율 열은 뺐다(13열이 1280 에서 글씨 하한까지 줄었다)
        assert "기간" not in cols and "평균 점유율" not in cols, cols
        assert t.item(rows["2025 시즌 8"], 0).toolTip() == old.span_text, t.item(rows["2025 시즌 8"], 0).toolTip()
        assert t.item(rows["2025 시즌 9"], 0).toolTip() == new.span_text
        vs = cols.index("지난 시즌 대비")
        # 새 시즌 33.3%(승1 무1 패1 — 승÷전체) − 지난 시즌 100%(2승) → 음수 · 가장 오래된 시즌은 비교 대상이 없다
        assert t.item(rows["2025 시즌 9"], vs).text() == f"{100 / 3 - 100:+.1f}%p", t.item(rows["2025 시즌 9"], vs).text()
        assert t.item(rows["2025 시즌 8"], vs).text() == "—"
        # 79 — 지난 시즌보다 내렸으면 빨강 · 비교 없는 칸은 칠하지 않는다
        assert t.item(rows["2025 시즌 9"], vs).foreground().color() == QColor(T.BAD)
        assert t.item(rows["2025 시즌 8"], vs).foreground().color() not in (QColor(T.BAD), QColor(T.GOOD))
        grade = cols.index("등급")
        assert t.item(rows["2025 시즌 9"], grade).text() == _win._division_names.get(900, "900")
        assert t.item(rows["2025 시즌 9"], grade).toolTip().startswith("최고 ")
        assert t.item(rows["2025 시즌 8"], grade).text() == "—"  # 상세가 없는 경기 — 등급 모름
        # 그래프·대시보드 점(77 A) — 같은 시즌 묶음 · 경기 수는 승·무·패만
        ser = _win._period_series("시즌")
        assert [(p.label, p.games) for p in ser] == [("2025 시즌 9", 3), ("2025 시즌 8", 2)], ser
        assert f"{ser[0].win_rate:.1f}" == "33.3", ser[0].win_rate
    finally:
        _win._rank_seasons, _win._matches_all = keep
        _win._render_seasons()


def test_bar_peak_labels_never_overlap():
    # 실데이터 30~45분 득 772 · 실 765 가 좁은 폭에서 "77365" 로 붙었다.
    import charts
    c = charts.GroupedBarChart()
    labels = ["0–15", "15–30", "30–45", "45–60", "60–75", "75–90", "연장"]
    c.set_data(labels, [("득점", [530, 619, 772, 515, 599, 751, 187], T.CHART_UP),
                        ("실점", [484, 586, 765, 554, 567, 728, 185], T.CHART_DOWN)])
    for w in (220, 300, 600):
        c.resize(w, 180)
        c.grab()
        rects = c.label_rects
        assert rects, w
        assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), \
            (w, rects)


def test_palettes_have_same_keys():
    # 안 쓰는 쪽 팔레트에 키가 빠지면 MODE 를 바꾸는 순간에야 KeyError 로 터진다.
    keys = {m: set(p) for m, p in T._PALETTES.items()}
    assert len(set(map(frozenset, keys.values()))) == 1, keys


def test_theme_reaches_widgets():
    # MODE 팔레트가 QPalette 와 QSS 양쪽으로 실제로 걸렸는지 — 카드 배경을 직접 찍어 본다.
    from PySide6.QtGui import QPalette
    # 제목 표시줄 색 체계(setColorScheme)는 offscreen 이 Unknown 만 돌려줘 여기선 못 잰다.
    assert _app.palette().color(QPalette.ColorRole.Window).name() == T.BG
    import widgets
    card = widgets.Card()
    card.resize(200, 120)
    img = card.grab().toImage()
    px = img.pixelColor(img.width() // 2, img.height() - 4).name()
    assert px == T.PANEL, (px, T.PANEL)


# 옛 다크 테마 시절 app_main/widgets 에 박혀 있던 색 — 색은 theme.py 에서만 온다.
_DARK = ["#0f1216", "#171b21", "#1c2128", "#2a313a", "#0d1117", "#06240d", "#12261a"]


def test_no_dark_theme_leftovers():
    for name in ("app_main.py", "widgets.py"):
        src = open(os.path.join(_ROOT, name), encoding="utf-8").read()
        for hexv in _DARK:
            hits = [i + 1 for i, line in enumerate(src.splitlines())
                    if re.search(re.escape(hexv), line, re.I)]
            assert not hits, (name, hexv, hits)


def test_attribution_on_search_and_main_pages():
    # 넥슨 오픈API 약관 제6조④ — 문구는 공식 가이드 그대로여야 한다
    want = "Data based on NEXON Open API"
    for idx in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
        page = _win.stack.widget(idx)
        labels = [lb for lb in page.findChildren(app_main.QLabel)
                  if lb.objectName() == "attribution"]
        assert [lb.text() for lb in labels] == [want], (idx, [lb.text() for lb in labels])


# ── 크래시 로그 ───────────────────────────────────────────────────────
class _TempCrash:
    """_setup_app 을 임시 데이터 폴더로 부르고, 끝나면 훅·faulthandler 를 되돌린다."""

    def __init__(self, prefill: bytes = b"", files: dict | None = None, hold: str | None = None):
        self.prefill = prefill
        self.files = files or {}   # logs 폴더에 미리 둘 파일(앞 실행이 남긴 faulthandler 기록 등)
        self.hold = hold           # 그중 열어 둘 파일 — 살아 있는 다른 실행이 쥔 것처럼
        self.notified = []

    def __enter__(self):
        self._dir = pathlib.Path(tempfile.mkdtemp())
        self._saved = (config.DATA_DIR, sys.excepthook, threading.excepthook,
                       app_main.QMessageBox.warning)
        self._held = None
        if self.prefill or self.files:
            (self._dir / "logs").mkdir()
        if self.prefill:
            (self._dir / "logs" / crashlog.LOG_NAME).write_bytes(self.prefill)
        for name, data in self.files.items():
            (self._dir / "logs" / name).write_bytes(data)
        if self.hold:
            self._held = open(self._dir / "logs" / self.hold, "a", encoding="utf-8")
        config.DATA_DIR = self._dir
        app_main.QMessageBox.warning = lambda *a, **k: self.notified.append(a)
        app_main._setup_app(_app)
        self.logs = self._dir / "logs"
        return self

    def __exit__(self, *exc):
        (config.DATA_DIR, sys.excepthook, threading.excepthook,
         app_main.QMessageBox.warning) = self._saved
        faulthandler.disable()
        fh = crashlog._state.pop("fault_file", None)
        if fh:
            fh.close()
        if self._held:
            self._held.close()
        crashlog._state.clear()
        shutil.rmtree(self._dir, ignore_errors=True)


def _raise_into_hook(msg):
    try:
        raise ValueError(msg)
    except ValueError:
        sys.excepthook(*sys.exc_info())


def test_slot_exception_fails_the_test():
    """슬롯 안 예외가 테스트를 빨갛게 만든다(73단계 F9 · watchdog.slot_errors) — PySide6 는 슬롯 예외를 훅에 넘기고
    계속 돌아, 클릭으로 연 슬롯 안의 모달 차단(ModalCalled)이 테스트까지 안 올라온다."""
    from PySide6.QtWidgets import QPushButton
    b = QPushButton()
    b.clicked.connect(lambda: QMessageBox.warning(None, "t", "t"))
    # 막지 않았으면: click() 은 정상으로 돌아오고 예외는 훅만 받는다 — 테스트 함수는 모른다
    seen, saved = [], sys.excepthook
    sys.excepthook = lambda et, e, tb: seen.append(et)
    try:
        b.click()
    finally:
        sys.excepthook = saved
    assert seen == [ModalCalled], seen
    # 테스트 하나를 감싸는 watchdog.limit 이 그 예외를 실패로 바꾼다
    try:
        with watchdog.limit("심은 슬롯 예외"):
            b.click()
    except watchdog.SlotError as e:
        assert "ModalCalled" in str(e) and "모달 호출" in str(e), e
    else:
        raise AssertionError("슬롯 안 모달이 통과로 읽혔다")
    assert sys.excepthook is saved, "감싼 뒤 훅을 되돌리지 않았다"


def test_setup_app_logs_crash_and_notifies_once():
    # 훅이 안 걸려 있으면 테스트 하네스(watchdog.slot_errors)의 훅이 받는다 — crashlog 훅이 남아 있으면 안 된다
    assert sys.excepthook is not crashlog._on_main_exc
    with _TempCrash() as ctx:
        _raise_into_hook("한글 오류 하나")
        _raise_into_hook("한글 오류 둘")
        text = (ctx.logs / crashlog.LOG_NAME).read_text(encoding="utf-8")
        assert "ValueError: 한글 오류 하나" in text and "ValueError: 한글 오류 둘" in text, text
        assert config.APP_VERSION in text, text
        assert len(ctx.notified) == 1, ctx.notified  # 같은 세션 두 번째부터는 기록만
        assert str(ctx.logs / crashlog.LOG_NAME) in ctx.notified[0][2], ctx.notified


def test_qt_slot_crash_reaches_log_and_app_survives():
    # 실제 경로 — 슬롯 안 예외. 훅이 없으면 PyQt6 가 여기서 프로세스를 끝낸다.
    def bad():
        raise RuntimeError("슬롯에서 터짐")

    with _TempCrash() as ctx:
        QTimer.singleShot(0, bad)
        for _ in range(5):
            _app.processEvents()
        text = (ctx.logs / crashlog.LOG_NAME).read_text(encoding="utf-8")
        assert "RuntimeError: 슬롯에서 터짐" in text, text
        assert len(ctx.notified) == 1, ctx.notified


def test_main_quit_arg_only_asks_and_starts_nothing():
    # 제거기가 부르는 --quit — 떠 있는 실행본에 부탁만 하고, 이 실행은 준비·한 번만 실행 주인·창 무엇도 안 된다
    import tray
    asked = []

    def boom(*a, **k):
        raise AssertionError("--quit 실행이 앱을 띄우려 했다")

    orig = app_main._setup_app, tray.request_quit, tray.SingleInstance
    app_main._setup_app = tray.SingleInstance = boom
    tray.request_quit = lambda name: asked.append(name) or "none"
    try:
        assert app_main.main(["exe", tray.QUIT_ARG]) == 0
    finally:
        app_main._setup_app, tray.request_quit, tray.SingleInstance = orig
    # 둘러보기(이름 끝 -demo)에도 알린다(96 O2) — 안 끝내면 설치 폴더 파일을 쥐고 있다
    assert asked == [tray.instance_name(demo=False), tray.instance_name(demo=True)], asked


def test_main_runs_setup_first():
    # main() → _setup_app 배선 — 이 한 줄이 빠지면 exe 의 크래시 로그가 통째로 꺼진다
    class _Stop(Exception):
        pass

    seen = []

    def fake_setup(app):
        seen.append(app)
        raise _Stop()

    def too_far(*a, **k):  # 준비를 건너뛰고 창까지 오면 — 그대로 두면 app.exec() 에서 영원히 멈춘다
        raise AssertionError("main 이 _setup_app 없이 창을 만들었다")

    orig = app_main._setup_app, app_main.MainWindow, app_main.ApiKeyDialog
    app_main._setup_app = fake_setup
    app_main.MainWindow = app_main.ApiKeyDialog = too_far
    try:
        app_main.main()
        raise AssertionError("main 이 _setup_app 을 안 불렀다")
    except _Stop:
        pass
    finally:
        app_main._setup_app, app_main.MainWindow, app_main.ApiKeyDialog = orig
    assert seen == [_app], seen


def test_main_starts_update_check_after_show():
    class _Stop(Exception):
        pass

    calls = []

    class _Win:
        def __init__(self, api):
            calls.append("창")

        def show_initial(self):
            calls.append("show")

        def start_update_check(self):
            calls.append("새 버전 확인")

        def start_cache_prune(self):
            calls.append("캐시 정리")

        def start_grade_holo(self):
            calls.append("강화 바탕")

        def attach_rank_sched(self, sched):
            calls.append("랭킹 수집")  # 예약은 창 밖(AppShell)에 살고 창은 상태만 받는다

        def attach_owner_sched(self, sched):
            calls.append("구단주 분포")  # 같은 셸이 쥐고 붙여 준다(49단계 ③a)

        def attach_card_sched(self, sched):
            calls.append("카드 정보")  # 같은 셸이 쥐고 붙여 준다(51단계 G2)

        def open_last_account(self):
            calls.append("마지막 계정")

        def start_prefetch(self):
            calls.append("미리 읽기")

        def ask_notice_update_once(self):
            pass   # 옛 동의자 다시 묻기 — 창이 보인 뒤(test_reask_after_show_normal)

        def show_changes_once(self, fresh):
            pass   # 업데이트 뒤 바뀐 점 — test_84_changes_once

        def sync_ranker_pick_data(self):
            calls.append("랭커 픽 정리")  # 켤 때 토글과 무관하게(14일 · 꺼졌으면 전부 — E12)

        def schedule_backfill(self, delay_s):
            calls.append(f"색인 {delay_s}초 뒤")

    def run(open_last: bool) -> list[str]:
        calls.clear()
        orig = (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY,
                config.OPEN_LAST_ACCOUNT, app_main._SHELL)
        app_main._setup_app, app_main.MainWindow = (lambda app: None), _Win
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION  # 안내는 이미 동의한 상태
        # 키도 있는 상태 — 이 PC 엔 실제 키가 있어 지나갔지만 키 없는 CI 에선 키 창이 떴다(2026-10-02)
        config.API_KEY = "test_key"
        config.OPEN_LAST_ACCOUNT = open_last
        try:
            app_main.main()
            raise AssertionError("main 이 app.exec() 까지 안 갔다")
        except ModalCalled as e:  # 끝의 app.exec() — 모달 차단이 멈춤 대신 여기서 끊는다
            assert "QApplication.exec" in str(e), e
        finally:
            _stop_shell()
            (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY,
             config.OPEN_LAST_ACCOUNT, app_main._SHELL) = orig
        return list(calls)

    # 기본은 검색 화면부터 — 마지막으로 본 계정을 저절로 열지 않는다(2026-10-04 사용자 결정)
    assert config.OPEN_LAST_ACCOUNT is False, "기본값이 켜져 있다 — 켜자마자 지난 닉네임이 검색된다"
    # 대신 마지막 계정의 저장된 경기를 뒤에서 읽어만 둔다(그 계정을 검색하면 DB 읽기를 건너뛴다)
    tail = ["랭커 픽 정리", f"색인 {config.SQUAD_BACKFILL_DELAY_S}초 뒤"]
    assert run(False) == ["창", "랭킹 수집", "구단주 분포", "카드 정보", "show", "새 버전 확인", "캐시 정리", "강화 바탕", *tail, "미리 읽기"], calls
    assert run(True) == ["창", "랭킹 수집", "구단주 분포", "카드 정보", "show", "새 버전 확인", "캐시 정리", "강화 바탕", *tail, "마지막 계정"], calls


def _stop_shell():
    """main() 이 만든 껍데기의 타이머(1시간 수집 확인·6시간 업데이트)를 내린다 — 다음 테스트로 새지 않게."""
    sh = app_main._SHELL
    if sh is not None:
        for t in (sh._release_timer, sh._update_timer, sh._tray_timer):
            t.stop()
        if sh.sched is not None:
            sh.sched.shutdown()
        if sh.owner_sched is not None:
            sh.owner_sched.shutdown()
        if sh.card_sched is not None:
            sh.card_sched.shutdown()
    app_main._SHELL = None


def test_main_tray_mode_makes_hidden_window_without_prefetch():
    calls = []

    class _Win:
        def __init__(self, api):
            calls.append("창")

        def attach_rank_sched(self, sched):
            pass

        def attach_owner_sched(self, sched):
            pass

        def attach_card_sched(self, sched):
            pass

        def schedule_backfill(self, delay_s):
            calls.append(f"색인 {delay_s}초 뒤")

        def __getattr__(self, name):
            return lambda *a: calls.append(name)

    orig = (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY, app_main._SHELL)
    app_main._setup_app, app_main.MainWindow = (lambda app: None), _Win
    config.NOTICE_ACCEPTED, config.API_KEY = config.NOTICE_VERSION, "test_key"
    try:
        try:
            app_main.main(["app", "--tray"])
            raise AssertionError("main 이 app.exec() 까지 안 갔다")
        except ModalCalled as e:
            assert "QApplication.exec" in str(e), e
        sh = app_main._SHELL
        assert sh is not None and sh.window is not None
        assert sh.sched._check_timer.isActive(), "--tray 인데 수집 예약을 안 걸었다 — 상주하는 이유가 수집이다"
        assert sh.owner_sched._timer.isActive(), "--tray 인데 구단주 분포 예약을 안 걸었다"
        # 74 ① — 켤 때 한 확인을 껍데기가 알아야 부팅 직후 트레이에서 열 때 GitHub 에 또 묻지 않는다
        assert sh._update_at is not None, "켤 때 확인한 시각을 껍데기에 안 적었다"
    finally:
        _stop_shell()
        (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY, app_main._SHELL) = orig
    # 창은 만들되 띄우지 않고, 1만 경기 미리 읽기(약 750MB)도 안 한다
    # 색인 백필은 트레이 시작이면 첫 화면이 없어 2분 뒤(ROADMAP 2.1.1 12)
    assert calls == ["창", "start_update_check", "start_cache_prune", "start_grade_holo", "sync_ranker_pick_data",
                     f"색인 {config.SQUAD_BACKFILL_TRAY_DELAY_S}초 뒤"], calls


def test_tray_before_consent_makes_no_request():
    # --tray 로 부팅했는데 안내 동의가 필요하다(안내 버전을 올린 뒤) — 창을 만들면 시즌표를 넥슨 웹에 요청한다.
    # 막는 창(안내·키)도 게임 위에 띄우지 않는다. 사용자가 트레이에서 열 때 묻는다.
    def boom(*a, **k):
        raise AssertionError("동의 전에 창·안내 창을 만들었다")

    orig = (app_main._setup_app, app_main.MainWindow, app_main.NoticeDialog, app_main.ApiKeyDialog,
            config.NOTICE_ACCEPTED, config.API_KEY, config.WEB_DATA, app_main._SHELL)
    app_main._setup_app = lambda app: None
    app_main.MainWindow = app_main.NoticeDialog = app_main.ApiKeyDialog = boom
    # v1(1.0.x) 동의자 — v2 의 수집 안내를 못 봤다. 처음 동의와 같이 막힌다(v2 는 test_reask_never_while_hidden)
    config.NOTICE_ACCEPTED, config.API_KEY, config.WEB_DATA = config.NOTICE_BASE_VERSION - 1, "test_key", True
    gets = []
    keep_get = ranker._session.get
    ranker._session.get = lambda *a, **k: gets.append(a) or boom()
    try:
        for key in ("test_key", ""):
            config.API_KEY = key
            try:
                app_main.main(["app", "--tray"])
                raise AssertionError("main 이 app.exec() 까지 안 갔다")
            except ModalCalled as e:
                assert "QApplication.exec" in str(e), e
            sh = app_main._SHELL
            assert sh is not None and sh.window is None, "동의 전인데 창을 만들었다"
            assert not sh.sched.can_run(), "동의 전인데 수집이 돌 수 있다"
            _stop_shell()
        config.NOTICE_ACCEPTED, config.API_KEY = config.NOTICE_VERSION, ""
        try:
            app_main.main(["app", "--tray"])
        except ModalCalled:
            pass
        assert app_main._SHELL.window is None, "키가 없는데 창을 만들었다"
    finally:
        _stop_shell()
        ranker._session.get = keep_get
        (app_main._setup_app, app_main.MainWindow, app_main.NoticeDialog, app_main.ApiKeyDialog,
         config.NOTICE_ACCEPTED, config.API_KEY, config.WEB_DATA, app_main._SHELL) = orig
    assert gets == [], gets


def test_main_freezes_gc_after_event_loop():
    # 1만 경기 기록이 든 채로 닫으면 파이썬이 종료하며 객체를 훑느라 프로세스가 1.7초 더 살았다.
    # 이벤트 루프가 끝난 '뒤' 얼려야 한다 — 앞에서 얼리면 그 뒤에 만든 기록은 그대로 훑는다.
    seen = []

    class _Win:
        def __init__(self, api):
            pass

        def __getattr__(self, name):
            return lambda *a: None

    orig = (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY,
            QApplication.exec, app_main.gc.freeze)
    app_main._setup_app, app_main.MainWindow = (lambda app: None), _Win
    config.NOTICE_ACCEPTED, config.API_KEY = config.NOTICE_VERSION, "test_key"
    QApplication.exec = lambda self: seen.append("exec") or 7
    app_main.gc.freeze = lambda: seen.append("freeze")
    keep_shell = app_main._SHELL
    try:
        rc = app_main.main()
    finally:
        _stop_shell()
        app_main._SHELL = keep_shell
        (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY,
         QApplication.exec, app_main.gc.freeze) = orig
    assert seen == ["exec", "freeze"] and rc == 7, (seen, rc)


def test_main_asks_notice_first_and_quits_on_decline():
    seen = []

    def dialog(name, result):
        class _Dlg:
            DEMO_RESULT = app_main.ApiKeyDialog.DEMO_RESULT

            def __init__(self, *a, **k):   # 키 창은 allow_demo 를 받는다(96 O2)
                pass

            def exec(self):
                seen.append(name)
                return result
        return _Dlg

    def too_far(*a, **k):
        raise AssertionError("동의하지 않았는데 창까지 왔다")

    A, R = app_main.QDialog.DialogCode.Accepted, app_main.QDialog.DialogCode.Rejected
    orig = (app_main._setup_app, app_main.NoticeDialog, app_main.ApiKeyDialog,
            app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY)
    app_main._setup_app, app_main.MainWindow = (lambda app: None), too_far
    try:
        # 옛 동의로 계속 쓸 수 없는 버전(v1 이하 — v0.3.0 사용자는 .env 에 값 없음 = 0)이면 창 전에 묻는다
        config.NOTICE_ACCEPTED, config.API_KEY = config.NOTICE_BASE_VERSION - 1, ""
        app_main.NoticeDialog = dialog("안내", R)
        app_main.ApiKeyDialog = dialog("키", R)
        assert app_main.main() == 0
        assert seen == ["안내"], seen  # 거절하면 키도 안 묻고 끝
        seen.clear()
        app_main.NoticeDialog = dialog("안내", A)
        assert app_main.main() == 0
        assert seen == ["안내", "키"], seen  # 안내가 키보다 먼저
        seen.clear()
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        assert app_main.main() == 0
        assert seen == ["키"], seen  # 동의했으면 다시 안 묻는다
        seen.clear()
        # v2(옛 동의 · 새 안내 있음) — 창 **전에는** 묻지 않는다(창을 띄운 뒤 그 위에 — test_reask_after_show_normal)
        config.NOTICE_ACCEPTED = config.NOTICE_BASE_VERSION
        assert app_main.main() == 0
        assert seen == ["키"], seen
        # 96 O3 — 새 설치(동의 0)는 소개가 안내 **앞에** 한 번. 소개를 어떻게 닫든 본 것으로(다음 실행엔 안내부터)
        seen.clear()
        app_main.IntroDialog = dialog("소개", R)
        config.NOTICE_ACCEPTED = 0
        app_main.NoticeDialog = dialog("안내", R)
        assert app_main.intro_needed()
        assert app_main.main() == 0
        assert seen == ["소개", "안내"], seen
        seen.clear()
        assert not app_main.intro_needed()
        assert app_main.main() == 0
        assert seen == ["안내"], seen
    finally:
        (app_main._setup_app, app_main.NoticeDialog, app_main.ApiKeyDialog,
         app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY) = orig
        app_main.IntroDialog = _RealIntro
        s = app_main.QSettings(str(config.SETTINGS_PATH), app_main.QSettings.Format.IniFormat)
        s.remove(config.INTRO_SEEN_KEY)
        s.sync()


_RealIntro = app_main.IntroDialog


def test_key_dialog_steps_96():
    """96 O1 — 키 창은 단계 넷(그림이 있으면 그림 + 한 줄, 없으면 한 줄만) · 첫 줄에 발급 사이트 주소."""
    dlg = app_main.ApiKeyDialog(allow_demo=True)
    texts = [lb.text() for lb in dlg.findChildren(app_main.QLabel)]
    steps = [t for t in texts if t[:2] in ("1.", "2.", "3.", "4.")]
    assert len(steps) == len(app_main.KEY_GUIDE) == 4, steps
    assert app_main.KEY_ISSUE_URL in steps[0] and "서비스 단계" in steps[2]
    have = sum(os.path.exists(os.path.join(_ROOT, "assets", "keyguide", img)) for img, _t in app_main.KEY_GUIDE)
    assert len(dlg.guide_pics) == have, (len(dlg.guide_pics), have)
    dlg.deleteLater()


def test_intro_dialog_pages_96():
    """96 O3 — 세 장 · 그림이 실제로 읽힌다(spec datas 와 같은 경로) · 마지막 장에서만 [시작하기] · 업그레이드한 사람엔 안 뜬다."""
    dlg = app_main.IntroDialog(_win)
    v = dlg.view
    assert v.pages.count() == len(app_main.INTRO_PAGES) == 3
    for i in range(v.pages.count()):
        pics = [lb for lb in v.pages.widget(i).findChildren(app_main.QLabel) if not lb.pixmap().isNull()]
        assert pics, f"{i + 1}장 그림을 못 읽었다"
    assert not dlg.btn_start.isVisibleTo(dlg) and dlg.btn_skip.isVisibleTo(dlg)
    v.btn_next.click()
    v.btn_next.click()
    assert v.at_last() and dlg.btn_start.isVisibleTo(dlg) and not v.btn_next.isEnabled()
    v.btn_prev.click()
    assert v.lb_count.text() == "2 / 3"
    dlg.deleteLater()
    spec = open(os.path.join(_ROOT, "피파전적관리.spec"), encoding="utf-8").read()
    assert "('assets/intro', 'assets/intro')" in spec
    saved = config.NOTICE_ACCEPTED
    try:
        config.NOTICE_ACCEPTED = 1
        assert not app_main.intro_needed(), "업그레이드한 사람(이미 동의)엔 안 뜬다"
    finally:
        config.NOTICE_ACCEPTED = saved
    _win._go_page("소개")
    assert _win._current_page_name() == "소개"


# ── 새 버전 알림 ──────────────────────────────────────────────────────
def test_is_newer_compares_numbers_not_text():
    assert updatecheck.is_newer("v0.10.0", "v0.9.0")  # 문자열 비교면 "0.10" < "0.9"
    assert updatecheck.is_newer("v1.0.0", "v0.2.0")
    assert updatecheck.is_newer("0.2.1", "v0.2.0")
    assert not updatecheck.is_newer("v0.2.0", "v0.2.0")
    assert not updatecheck.is_newer("v0.1.9", "v0.2.0")
    assert not updatecheck.is_newer("nightly", "v0.2.0")  # 모르는 형식은 알리지 않는다


class _FakeGet:
    def __init__(self, status=200, body=None, exc=None):
        self.status, self.body, self.exc, self.calls = status, body, exc, 0

    def __call__(self, url, **kw):
        self.calls += 1
        if self.exc:
            raise self.exc
        fake = self

        class _R:
            status_code = fake.status

            def json(self):
                if fake.body is None:
                    raise ValueError("json 아님")
                return fake.body
        return _R()


def test_latest_newer_reads_release_and_stays_quiet_on_failure():
    page = "https://example.invalid/r"
    setup_u, sums_u = "https://example.invalid/s.exe", "https://example.invalid/sums"
    rel = {"tag_name": "v9.0.0", "html_url": page, "draft": False, "prerelease": False,
           "body": "## 받기\n\n- 설치\n\n## 바뀐 점\n\n- 새 기능\n- 고친 것\n\n## 파일 확인\n\nabc",
           "assets": [{"name": "FifaMatchTracker-Setup-v9.0.0.exe", "browser_download_url": setup_u,
                       "url": "https://example.invalid/api/1"},
                      {"name": "SHA256SUMS.txt", "browser_download_url": sums_u,
                       "url": "https://example.invalid/api/2"},
                      {"name": "FifaMatchTracker-v9.0.0-portable.zip", "browser_download_url": "z",
                       "url": "https://example.invalid/api/3"}]}
    full = updatecheck.Release("v9.0.0", page, setup_u, sums_u, "- 새 기능\n- 고친 것",
                               setup_api_url="https://example.invalid/api/1",
                               sums_api_url="https://example.invalid/api/2")
    cases = [
        (_FakeGet(body=rel), full),
        (_FakeGet(body=dict(rel, assets=[])), updatecheck.Release("v9.0.0", page, "", "", full.notes)),
        (_FakeGet(body=dict(rel, tag_name=config.APP_VERSION)), None),  # 같은 버전
        (_FakeGet(body=dict(rel, draft=True)), None),
        (_FakeGet(body=dict(rel, prerelease=True)), None),
        (_FakeGet(status=404), None),                                    # 릴리스 없음
        (_FakeGet(status=403), None),                                    # GitHub 한도
        (_FakeGet(body=None), None),                                     # JSON 아님
        (_FakeGet(exc=requests.ConnectionError("오프라인")), None),
    ]
    orig_get, orig_on = updatecheck.requests.get, config.UPDATE_CHECK
    config.UPDATE_CHECK = True
    try:
        for fake, want in cases:
            updatecheck.requests.get = fake
            got = updatecheck.latest_newer()
            assert got == want, (fake.status, fake.body, got)
        # 끄면 묻지도 않는다 — 켜져 있으면 물었을 같은 응답으로
        fake = _FakeGet(body=rel)
        updatecheck.requests.get = fake
        config.UPDATE_CHECK = False
        assert updatecheck.latest_newer() is None and fake.calls == 0, fake.calls
    finally:
        updatecheck.requests.get, config.UPDATE_CHECK = orig_get, orig_on


_REL = updatecheck.Release("v9.0.0", "https://example.invalid/r",
                           "https://example.invalid/s.exe", "https://example.invalid/sums", "- 새 기능")


class _Patch:
    """(대상, 이름, 값) 들을 잠깐 바꿨다가 되돌린다."""

    def __init__(self, *items):
        self.items = items

    def __enter__(self):
        self.saved = [(o, n, getattr(o, n)) for o, n, _ in self.items]
        for o, n, v in self.items:
            setattr(o, n, v)

    def __exit__(self, *exc):
        for o, n, v in self.saved:
            setattr(o, n, v)


def test_check_says_latest_only_when_actually_checked():
    rel = {"tag_name": "v9.0.0", "html_url": "https://example.invalid/r", "draft": False,
           "prerelease": False, "body": "", "assets": []}
    N, L, U = updatecheck.NEWER, updatecheck.LATEST, updatecheck.UNKNOWN
    cases = [
        (_FakeGet(body=rel), N),
        (_FakeGet(body=dict(rel, tag_name=config.APP_VERSION)), L),   # 같은 버전
        (_FakeGet(body=dict(rel, tag_name="v0.0.1")), L),             # 공개 전 버전을 쓰는 중
        (_FakeGet(body=dict(rel, tag_name="nightly")), U),            # 형식을 모르면 최신이라 못 한다
        (_FakeGet(body=dict(rel, draft=True)), U),
        (_FakeGet(status=404), U),
        (_FakeGet(status=403), U),
        (_FakeGet(body=None), U),
        (_FakeGet(exc=requests.ConnectionError("오프라인")), U),
    ]
    orig_get, orig_on = updatecheck.requests.get, config.UPDATE_CHECK
    config.UPDATE_CHECK = True
    try:
        for fake, want in cases:
            updatecheck.requests.get = fake
            status, got = updatecheck.check()
            assert status == want and (got is not None) == (want == N), (fake.body, status, got)
        updatecheck.requests.get = _FakeGet(body=dict(rel, tag_name=config.APP_VERSION))
        config.UPDATE_CHECK = False  # 껐으면 확인 안 했으니 '최신'도 아니다
        assert updatecheck.check() == (U, None)
    finally:
        updatecheck.requests.get, config.UPDATE_CHECK = orig_get, orig_on


def _version_cards():
    """두 화면(검색·사이드바)의 왼쪽 아래 버전 카드 — 화면마다 정확히 하나."""
    out = []
    for idx in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
        cards = _win.stack.widget(idx).findChildren(app_main.VersionCard)
        assert len(cards) == 1, (idx, len(cards))
        out.append(cards[0])
    return out


def _status_bar_state():
    """두 화면의 버전 카드 — [(상태 글, 단추 글 or None)]."""
    return [(c.lb_title.text(), c.btn_update.text() if c.btn_update.isVisibleTo(c) else None) for c in _version_cards()]


def _reset_version_cards():
    _win._release = None
    _win._download_worker = None
    for c in _win._version_cards:
        c.set_busy(False)
    _win._set_update_status("")


def test_update_status_always_visible_bottom_left():
    # 사용자 요청: 잠깐 뜨는 카드가 아니라 화면에 늘 보이는 칸(버전 옆) — 3.7.2 부터 그 칸이 카드 하나다
    orig = updatecheck.check_full
    try:
        for status, rel, want in [
                (updatecheck.LATEST, None, ("최신 버전입니다", None)),
                (updatecheck.UNKNOWN, None, ("업데이트 확인 못 함", None)),  # 모르면 '최신'이라 안 한다
                (updatecheck.NEWER, _REL, ("새 버전 v9.0.0", "받으러 가기"))]:
            updatecheck.check_full = lambda *a, s=status, r=rel, **k: updatecheck.CheckResult(s, r)
            _win.start_update_check()          # QThread.start 는 막혀 있다 — 배선만 만든다
            assert _status_bar_state() == [("업데이트 확인 중…", None)] * 2
            _win._update_worker.run()           # 같은 스레드에서 돌려 신호 → 창까지
            assert _status_bar_state() == [want] * 2, (status, _status_bar_state())
        # 새 버전이면 테두리가 강조색 — 평소 카드와 구분된다
        assert all(T.ACCENT in c.styleSheet() for c in _version_cards())
        # 단추는 소스 실행이면 페이지 열기
        opened = []
        with _Patch((app_main.QDesktopServices, "openUrl", lambda u: opened.append(u.toString())),
                    (updatecheck, "install_dir", lambda: None)):
            _version_cards()[0].btn_update.click()
        assert opened == [_REL.page_url], opened
        _win._set_update_status("최신 버전입니다")
        assert all(T.ACCENT not in c.styleSheet() for c in _version_cards()), "최신인데 강조 테두리가 남았다"
    finally:
        updatecheck.check_full = orig
        _reset_version_cards()


def test_no_floating_update_card_372():
    import widgets
    # 3.7.2 사용자: 오른쪽 아래 떠 있던 카드를 없앤다 — 같은 말을 왼쪽 아래와 두 곳에서 했다
    assert not hasattr(_win, "update_card") and not hasattr(widgets, "UpdateCard")
    try:
        for page in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
            _win.stack.setCurrentIndex(page)
            _win._on_update_latest()
            _win._on_update_found(_REL)
            _app.processEvents()
            # 창 바로 아래 떠 있는 위젯(레이아웃 밖)이 없다 — 카드는 화면 안 레이아웃에만
            floating = [w for w in _win.findChildren(app_main.QFrame, options=Qt.FindChildOption.FindDirectChildrenOnly)
                        if w.isVisibleTo(_win) and w.objectName() in ("updateCard", "versionCard")]
            assert floating == [], floating
    finally:
        _reset_version_cards()
        _win.stack.setCurrentIndex(_AFTER_LOAD[0])


def test_version_bottom_left_not_in_title():
    assert config.APP_VERSION not in _win.windowTitle(), _win.windowTitle()
    for idx in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
        page = _win.stack.widget(idx)
        labels = [lb for lb in page.findChildren(app_main.QLabel) if lb.objectName() == "versionLabel"]
        assert [lb.text() for lb in labels] == [f"지금 {config.APP_VERSION}"], (idx, [lb.text() for lb in labels])
        card = page.findChildren(app_main.VersionCard)[0]
        _win.stack.setCurrentIndex(idx)
        _app.processEvents()
        pos = card.mapTo(page, card.rect().bottomLeft())
        # 왼쪽 아래 — 화면 왼쪽 1/4 · 아래 1/6 안
        assert pos.x() < page.width() / 4 and pos.y() > page.height() * 5 / 6, (idx, pos, page.size())
    _win.stack.setCurrentIndex(_AFTER_LOAD[0])


def test_version_card_as_big_as_old_card_372():
    import widgets
    # 사용자: 왼쪽 칸을 오른쪽 카드 크기로 — 제목은 옛 카드처럼 굵은 PT_S · 아래 줄은 가장 작은 글자(PX_XS)가 아니다 · 검색 화면 폭 300
    search, side = _version_cards()
    try:
        _win._on_update_latest()
        for c in (search, side):
            f = c.lb_title.font()
            assert f.bold() and f.pointSize() == T.PT_S, (f.bold(), f.pointSize())
            assert c.lb_sub.fontInfo().pixelSize() > T.PX_XS, c.lb_sub.fontInfo().pixelSize()
        assert search.width() == widgets.VersionCard.WIDTH, search.width()
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        _app.processEvents()
        assert side.width() >= T.SIDEBAR_W - 30, (side.width(), T.SIDEBAR_W)
        assert side.mapTo(_win, side.rect().topRight()).x() < T.SIDEBAR_W, "사이드바 밖으로 나갔다"
    finally:
        _reset_version_cards()
        _win.stack.setCurrentIndex(_AFTER_LOAD[0])


def test_update_click_portable_opens_page_only():
    opened = []
    with _Patch((app_main.QDesktopServices, "openUrl", lambda u: opened.append(u.toString())),
                (updatecheck, "install_dir", lambda: None)):
        _win._release = _REL
        _win._on_update_clicked()
    assert opened == [_REL.page_url] and _win._download_worker is None, (opened, _win._download_worker)
    _reset_version_cards()


def test_update_click_asks_first_and_no_means_nothing():
    no = app_main.QMessageBox.StandardButton.No
    with _Patch((updatecheck, "install_dir", lambda: pathlib.Path("C:/fake")),
                (app_main.QMessageBox, "question", lambda *a, **k: no)):
        _win._download_worker = None
        _win._on_update_found(_REL)
        _win._on_update_clicked()
        try:
            assert _win._download_worker is None, "아니오를 눌렀는데 내려받기 시작"
            ok = [c.btn_update.isEnabled() and c.btn_update.isVisibleTo(c) for c in _version_cards()]
            assert all(ok), "아니오 뒤에 단추가 잠겼다"
        finally:
            _reset_version_cards()


def test_update_click_installed_downloads_then_installs_and_closes():
    asked, launched, closed = [], [], []
    yes = app_main.QMessageBox.StandardButton.Yes
    with _Patch((updatecheck, "install_dir", lambda: pathlib.Path("C:/fake")),
                (app_main.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or yes),
                (updatecheck, "launch_installer", lambda p: launched.append(p)),
                (_win, "close", lambda: closed.append(1))):
        _win._on_update_found(_REL)
        assert _status_bar_state() == [("새 버전 v9.0.0", "업데이트")] * 2, _status_bar_state()
        _version_cards()[1].btn_update.click()   # 사이드바 카드에서 눌러도
        try:
            w = _win._download_worker
            assert w is not None, "내려받기가 시작되지 않았다"
            # 진행은 두 카드 모두에 — 받는 동안 단추는 숨고 다시 못 누른다
            for c in _version_cards():
                assert c.bar.isVisibleTo(c) and not c.btn_update.isVisibleTo(c) and not c.btn_update.isEnabled()
            w.progress.emit(5 * 2**20, 10 * 2**20)
            assert all("50%" in c.lb_sub.text() for c in _version_cards()), [c.lb_sub.text() for c in _version_cards()]
            assert "v9.0.0" in asked[0] and "- 새 기능" in asked[0], asked
            w.done.emit("C:/fake/setup.exe")  # 검증 통과 신호
            assert launched == [pathlib.Path("C:/fake/setup.exe")] and closed == [1], (launched, closed)
        finally:
            _reset_version_cards()
    del _win.close


def test_update_failure_restores_card_and_tells_why():
    warned = []
    with _Patch((app_main.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))):
        _win._on_update_found(_REL)
        _win._release = _REL
        for c in _win._version_cards:
            c.set_busy(True, "내려받는 중…")
        _win._on_update_failed("체크섬이 다릅니다")
    try:
        for c in _version_cards():
            assert not c.bar.isVisibleTo(c) and c.btn_update.isEnabled() and c.btn_update.isVisibleTo(c)
            assert c.lb_sub.text() == f"지금 {config.APP_VERSION}", c.lb_sub.text()
        assert "체크섬" in warned[0] and _REL.page_url in warned[0], warned
    finally:
        _reset_version_cards()


def test_install_dir_only_for_installed_exe():
    tmp = pathlib.Path(tempfile.mkdtemp())
    exe = tmp / "피파전적관리.exe"
    exe.write_bytes(b"")
    had_frozen = hasattr(sys, "frozen")
    try:
        assert updatecheck.install_dir() is None, "소스 실행인데 설치판으로 봤다"
        with _Patch((sys, "executable", str(exe))):
            sys.frozen = True
            assert updatecheck.install_dir() is None, "제거기 없는 exe(포터블)를 설치판으로 봤다"
            (tmp / updatecheck.UNINSTALLER).write_bytes(b"")
            assert updatecheck.install_dir() == tmp.resolve(), updatecheck.install_dir()
    finally:
        if not had_frozen and hasattr(sys, "frozen"):
            del sys.frozen
        shutil.rmtree(tmp, ignore_errors=True)


def test_expected_sha256_reads_common_formats():
    h = "a" * 64
    assert updatecheck.expected_sha256(f"{h}  F.exe\n", "F.exe") == h
    assert updatecheck.expected_sha256(f"{h} *F.exe\r\n", "F.exe") == h  # 바이너리 표시·CRLF
    assert updatecheck.expected_sha256(f"{h.upper()}  F.exe", "F.exe") == h
    assert updatecheck.expected_sha256(f"{h}  G.exe", "F.exe") is None
    assert updatecheck.expected_sha256("zzz  F.exe", "F.exe") is None


class _FakeDownload:
    """sums 주소엔 체크섬 본문을, setup 주소엔 바이트를 준다."""

    def __init__(self, payload: bytes, sums_text: str):
        self.payload, self.sums_text = payload, sums_text

    def __call__(self, url, **kw):
        fake = self

        class _R:
            status_code = 200
            text = fake.sums_text
            headers = {"Content-Length": str(len(fake.payload))}

            def raise_for_status(self):
                pass

            def iter_content(self, n):
                for i in range(0, len(fake.payload), 3):
                    yield fake.payload[i:i + 3]

            def __enter__(self):
                return self

            def __exit__(self, *e):
                pass
        return _R()


def test_download_verified_installs_only_matching_file():
    import hashlib
    payload = b"setup-bytes"
    name = updatecheck.SETUP_ASSET.format(tag=_REL.tag)
    good = f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        with _Patch((updatecheck.requests, "get", _FakeDownload(payload, good))):
            prog = []
            path = updatecheck.download_verified(_REL, tmp, progress=lambda d, t: prog.append((d, t)))
            assert path.read_bytes() == payload and prog[-1] == (len(payload), len(payload)), prog
        # 막지 않았으면: 다른 바이트가 와도 그대로 실행됐을 것이다
        bad = f"{hashlib.sha256(b'other').hexdigest()}  {name}\n"
        with _Patch((updatecheck.requests, "get", _FakeDownload(payload, bad))):
            try:
                updatecheck.download_verified(_REL, tmp)
                raise AssertionError("체크섬이 다른데 통과했다")
            except updatecheck.UpdateError as e:
                assert "체크섬" in str(e), e
            assert not (tmp / name).exists(), "검증에 실패한 설치 파일이 남았다"
        # 자동 설치용 파일이 없는 릴리스
        try:
            updatecheck.download_verified(updatecheck.Release("v9.0.0", "p"), tmp)
            raise AssertionError("설치 파일 주소 없이 통과했다")
        except updatecheck.UpdateError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_download_falls_back_to_api_path_when_normal_url_fails():
    """84 G2 — 일반 주소가 504 면 같은 첨부의 API 경로(Accept: octet-stream)로 다시 받는다."""
    import hashlib
    payload = b"setup-bytes-api"
    name = updatecheck.SETUP_ASSET.format(tag=_REL.tag)
    sums = f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
    ok = _FakeDownload(payload, sums)
    calls = []

    def get(url, **kw):
        calls.append((url, (kw.get("headers") or {}).get("Accept")))
        if "/api/" not in url:
            r = requests.Response()
            r.status_code = 504
            raise requests.HTTPError("504 Gateway Timeout", response=r)
        if (kw.get("headers") or {}).get("Accept") != updatecheck.ASSET_API_ACCEPT:
            raise AssertionError("API 경로에 Accept 를 안 붙였다 — 파일 대신 JSON 이 온다")
        return ok(url, **kw)

    rel = updatecheck.Release(_REL.tag, _REL.page_url, _REL.setup_url, _REL.sums_url, _REL.notes,
                              setup_api_url="https://example.invalid/api/1",
                              sums_api_url="https://example.invalid/api/2")
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        with _Patch((updatecheck.requests, "get", get)):
            # 막지 않았으면: API 경로가 없던 때는 504 한 번에 업데이트가 실패했다
            try:
                updatecheck.download_verified(_REL, tmp)
                raise AssertionError("일반 주소만으로 504 를 넘겼다 — 가짜가 안 막는다")
            except updatecheck.UpdateError as e:
                assert "504" in str(e), e
            assert not (tmp / name).exists()
            calls.clear()
            path = updatecheck.download_verified(rel, tmp)
            assert path.read_bytes() == payload
            assert [u for u, _ in calls] == [_REL.sums_url, rel.sums_api_url,
                                             _REL.setup_url, rel.setup_api_url], calls
            path.unlink()

        # 두 길 모두 받다가 끊기면 반쯤 쓴 설치 파일을 남기지 않는다
        class _Cut:
            status_code = 200
            text = sums
            headers = {"Content-Length": "100"}

            def raise_for_status(self):
                pass

            def iter_content(self, n):
                yield b"half"
                raise requests.ConnectionError("끊김")

            def __enter__(self):
                return self

            def __exit__(self, *e):
                pass

        seen = []

        def cut(url, **kw):
            seen.append(url)
            return _Cut()
        with _Patch((updatecheck.requests, "get", cut)):
            try:
                updatecheck.download_verified(rel, tmp)
                raise AssertionError("끊긴 내려받기가 통과했다")
            except updatecheck.UpdateError:
                pass
            assert seen.count(rel.setup_api_url) == 1, seen      # 끊겼으면 다음 길로 갔다
            assert not (tmp / name).exists(), "반쯤 받은 설치 파일이 남았다"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_84_changes_once():
    """84 H2 — 업데이트 뒤 처음 보일 때 한 번: 그 사이 버전 절 · 새로 깐 사람·내려온 사람·숨김엔 안 띄움 · 두 번째엔 안 띄움."""
    log = ("# 변경 이력\n\n## v3.5.1 — 날\n\n- 새 것\n\n## v3.4.2 — 날\n\n- 중간\n\n## v3.4.1 — 날\n\n- 옛 것\n")
    got = updatecheck.changes_between(log, "v3.4.1", "v3.5.1")
    assert "새 것" in got and "중간" in got and "옛 것" not in got, got
    assert got.index("새 것") < got.index("중간"), "새 버전부터가 아니다"
    only = updatecheck.changes_between(log, "", "v3.5.1")
    assert "새 것" in only and "중간" not in only, only     # 이 창이 생기기 전 버전에서 올라옴 — 지금 절만
    assert updatecheck.changes_between(log, "엉터리", "v3.5.1") == ""
    real = updatecheck.read_changelog()
    assert real and updatecheck.changes_between(real, "v0.0.1", "v3.4.1"), "CHANGELOG 를 못 읽거나 절을 못 고른다"
    spec = (pathlib.Path(app_main.__file__).with_name("피파전적관리.spec")).read_text(encoding="utf-8")
    assert "('CHANGELOG.md', '.')" in spec, "exe 에 CHANGELOG 가 안 들어간다 — 창이 조용히 안 뜬다"
    import inspect
    assert "show_changes_once(fresh)" in inspect.getsource(app_main._open_window), "켤 때 안 부른다"

    tmp = pathlib.Path(tempfile.mkdtemp())
    shown = []
    keep = (app_main.ChangesDialog.exec, updatecheck.read_changelog, config.APP_VERSION)
    try:
        from PySide6.QtCore import QSettings
        ini = QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat)
        app_main.ChangesDialog.exec = lambda self: shown.append(self.browser.toPlainText())
        updatecheck.read_changelog = lambda: log
        config.APP_VERSION = "v3.5.1"
        with _Patch((_win, "_settings", lambda: ini), (_win, "isVisible", lambda: True),
                    (_win, "isMinimized", lambda: False)):
            ini.setValue(updatecheck.SEEN_VERSION_KEY, "v3.4.1")
            _win.show_changes_once(False)
            assert len(shown) == 1 and "새 것" in shown[0] and "중간" in shown[0], shown
            assert ini.value(updatecheck.SEEN_VERSION_KEY) == "v3.5.1"
            _win.show_changes_once(False)
            assert len(shown) == 1, "두 번째 실행에도 떴다"
            for seen, fresh in (("v3.4.1", True), ("v9.0.0", False)):   # 새로 깔았다 · 옛 버전으로 내려왔다
                ini.setValue(updatecheck.SEEN_VERSION_KEY, seen)
                _win.show_changes_once(fresh)
                assert len(shown) == 1 and ini.value(updatecheck.SEEN_VERSION_KEY) == "v3.5.1", (seen, shown)
            ini.remove(updatecheck.SEEN_VERSION_KEY)                  # 이 창 전 버전에서 올라옴 — 지금 절만
            _win.show_changes_once(False)
            assert len(shown) == 2 and "새 것" in shown[1] and "중간" not in shown[1], shown
        with _Patch((_win, "_settings", lambda: ini), (_win, "isVisible", lambda: False)):
            ini.setValue(updatecheck.SEEN_VERSION_KEY, "v3.4.1")
            _win.show_changes_once(False)
            assert len(shown) == 2 and ini.value(updatecheck.SEEN_VERSION_KEY) == "v3.4.1", "숨긴 창에서 띄우거나 적었다"
    finally:
        app_main.ChangesDialog.exec, updatecheck.read_changelog, config.APP_VERSION = keep
        shutil.rmtree(tmp, ignore_errors=True)


def test_84_report_body_hides_user_folder_and_fits_url():
    """84 H1 — 오류 기록 끝을 붙이되 사용자 폴더(한글 이름)는 가리고 · 오래된 줄부터 빼 주소 길이 안에."""
    from urllib.parse import quote, parse_qs, urlsplit
    import crashlog
    tmp = pathlib.Path(tempfile.mkdtemp())
    home = pathlib.Path(r"C:\Users\홍길동")
    try:
        lines = [f"줄{i} File \"{home}\\AppData\\x.py\" 한글 메시지 {'가' * 30}" for i in range(100)]
        (tmp / crashlog.LOG_NAME).write_text("\n".join(lines), encoding="utf-8")
        assert str(home) in lines[-1], "가짜 기록에 사용자 폴더가 없다(아래 단언이 빈 검사)"
        body = crashlog.report_body(tmp, "v9.9.9", home=home)
        assert str(home) not in body and "홍길동" not in body and "%USERPROFILE%" in body, body[-300:]
        assert "줄99" in body and "줄0 " not in body and "v9.9.9" in body
        assert len(quote(body)) <= crashlog.REPORT_QUOTED_MAX, len(quote(body))
        assert body.count("줄") < crashlog.REPORT_LOG_LINES, "주소 길이로 안 잘랐다(한글은 글자 수로 재면 넘친다)"
        (tmp / crashlog.LOG_NAME).write_text("가" * 5000, encoding="utf-8")
        assert "길어" in crashlog.report_body(tmp, "v9.9.9", home=home)
        (tmp / crashlog.LOG_NAME).unlink()
        assert "오류 기록 없음" in crashlog.report_body(tmp, "v9.9.9", home=home)
        # 주소 — 작성 화면 · 본문이 그대로 되돌아온다
        with _Patch((config, "DATA_DIR", tmp)):
            (tmp / "logs").mkdir()
            (tmp / "logs" / crashlog.LOG_NAME).write_text("Traceback 한 줄", encoding="utf-8")
            u = app_main.issue_url()
        parts = urlsplit(u)
        assert u.startswith(config.ISSUE_NEW_URL + "?"), u
        assert "Traceback 한 줄" in parse_qs(parts.query)["body"][0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_84_info_dialog_folder_and_issue_buttons():
    """84 S2·H1 — [정보] 의 두 단추: 데이터 폴더를 탐색기로 · 이슈 작성 화면을 브라우저로(앱은 아무것도 안 보낸다)."""
    opened = []
    with _Patch((app_main.QDesktopServices, "openUrl", lambda u: opened.append(u) or True)):
        dlg = app_main.AboutDialog(_win)
        try:
            dlg.btn_folder.click()
            dlg.btn_issue.click()
        finally:
            dlg.deleteLater()
    assert len(opened) == 2, opened
    assert opened[0].isLocalFile() and pathlib.Path(opened[0].toLocalFile()) == pathlib.Path(config.DATA_DIR), opened[0]
    assert opened[1].toString().startswith(config.ISSUE_NEW_URL), opened[1].toString()


def test_thread_crash_logged_without_dialog():
    with _TempCrash() as ctx:
        t = threading.Thread(target=lambda: 1 / 0, name="일꾼")
        t.start()
        t.join()
        # QThread.run 의 예외는 threading 이 아니라 sys.excepthook 으로, 그 스레드에서 온다
        t = threading.Thread(target=lambda: _raise_into_hook("큐스레드식"))
        t.start()
        t.join()
        text = (ctx.logs / crashlog.LOG_NAME).read_text(encoding="utf-8")
        assert "thread 일꾼" in text and "ZeroDivisionError" in text, text
        assert "ValueError: 큐스레드식" in text, text
        assert not ctx.notified  # GUI 스레드가 아니면 창을 띄우지 않는다


def test_crash_log_rotates_at_start():
    big = b"x" * (crashlog.MAX_BYTES + 1)
    with _TempCrash(prefill=big) as ctx:
        old = ctx.logs / (crashlog.LOG_NAME + ".1")
        assert old.exists() and old.stat().st_size == len(big), old
        # 밀어낸 뒤엔 오류가 날 때 새로 생긴다(시작할 때 만들지 않는다 — faulthandler 는 따로 쓴다)
        assert not (ctx.logs / crashlog.LOG_NAME).exists()


def _crash_text(ctx) -> str:
    p = ctx.logs / crashlog.LOG_NAME
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_handled_native_exception_not_in_crash_log():
    # 1.1.1 exe 실측: 처리된 COM 예외 0x8001010d 를 faulthandler 가 "fatal" 로 적었다. ctypes 는 SEH 를 잡아
    # OSError 로 바꾸므로 같은 상황(처리된 네이티브 예외 · 프로세스 생존)을 그대로 만든다.
    import ctypes
    with _TempCrash() as ctx:
        try:
            ctypes.windll.kernel32.RaiseException(0x8001010D, 0, 0, None)
        except OSError:
            pass
        fault = crashlog._state["fault_path"]
        crashlog._state["fault_file"].flush()
        # 막지 않았다면 — faulthandler 는 이걸 정말로 적는다
        assert "0x8001010d" in fault.read_text(encoding="utf-8"), "faulthandler 가 처리된 예외를 안 적음 — 재현 실패"
        crashlog.discard_fault()  # 정상 종료(atexit)
        assert not fault.exists(), fault
        assert "0x8001010d" not in _crash_text(ctx), _crash_text(ctx)


def test_normal_exit_discards_fault_file():
    # 배선 — 정상 종료(atexit)가 실제로 버리는지 별도 프로세스로. 처리된 예외 하나를 남기고 끝낸다.
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        code = ("import sys, ctypes; sys.path.insert(0, sys.argv[1]); import crashlog, pathlib\n"
                "crashlog.install(pathlib.Path(sys.argv[2]), 't')\n"
                "try:\n    ctypes.windll.kernel32.RaiseException(0x8001010D, 0, 0, None)\nexcept OSError:\n    pass\n")
        r = subprocess.run([sys.executable, "-c", code, _ROOT, str(d)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        assert r.returncode == 0, r.stderr
        assert not list(d.glob(crashlog.FAULT_PREFIX + "*")), list(d.iterdir())
        assert not (d / crashlog.LOG_NAME).exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fault_from_dead_session_moves_to_crash_log():
    left = crashlog.FAULT_PREFIX + "99999"
    empty = crashlog.FAULT_PREFIX + "99998"
    with _TempCrash(files={left: b"Windows fatal exception: access violation\n", empty: b""}) as ctx:
        text = _crash_text(ctx)
        assert "access violation" in text and "비정상 종료" in text, text
        assert not (ctx.logs / left).exists() and not (ctx.logs / empty).exists()
        assert text.count("===") == 1, text  # 빈 파일은 지우기만


def test_live_session_fault_file_left_alone():
    # 두 번째 실행도 crashlog 를 건다 — 첫 실행이 쥐고 있는 파일을 옮기면 안 된다
    live = crashlog.FAULT_PREFIX + "11111"
    with _TempCrash(files={live: b"Windows fatal exception: code 0x8001010d\n"}, hold=live) as ctx:
        assert (ctx.logs / live).exists()
        assert "0x8001010d" not in _crash_text(ctx), _crash_text(ctx)


# ── 넥슨 웹 데이터 스위치 ─────────────────────────────────────────────
class _Sent(Exception):
    pass


def _web_calls():
    return [
        ("ranker", lambda: ranker.fetch_manager_rank("닉"), ranker.RankerError),
        ("rank page", lambda: ranker.fetch_rank_page(1), ranker.RankerError),
        ("rank rows", lambda: ranker.fetch_rank_rows(1), ranker.RankerError),
        ("season cut", lambda: ranker.fetch_season_cut(90, 200), ranker.RankerError),
        ("playerinfo", lambda: playerinfo.fetch_player_info(1), playerinfo.PlayerInfoError),
        ("ability", lambda: playerinfo.fetch_player_ability(1), playerinfo.PlayerInfoError),
        ("boost rows", lambda: playerinfo.fetch_boost_rows(1, 120), playerinfo.PlayerInfoError),
        ("boost cal", lambda: playerinfo.web_boost_cal(1, 5, [(2, 1)]), playerinfo.PlayerInfoError),
        ("seasons", lambda: sn.fetch_seasons(), sn.SeasonError),
        ("teamcolor list", lambda: teamcolor.fetch_list(), teamcolor.TeamColorError),
        ("teamcolor detail", lambda: teamcolor.fetch_detail(1005), teamcolor.TeamColorError),
        ("teamcolor players", lambda: teamcolor.fetch_players(1005), teamcolor.TeamColorError),
        ("trait owner", lambda: traitcollect.fetch_owner_sn(traitcollect.new_session(), "닉"), traitcollect.TraitError),
        ("trait team", lambda: traitcollect.fetch_team(traitcollect.new_session(), 1, 1, 0), traitcollect.TraitError),
    ]


def test_web_data_switch_blocks_every_request():
    def sent(self, *a, **k):
        raise _Sent()

    orig_req, orig_on = requests.Session.request, config.WEB_DATA
    requests.Session.request = sent
    try:
        # 켜져 있으면 요청이 실제로 나간다 — 이게 없으면 아래 "안 나감"이 공허하다
        config.WEB_DATA = True
        for name, call, err in _web_calls():
            try:
                call()
                raise AssertionError(f"{name}: 요청 없이 끝났다")
            except _Sent:
                pass
            except err as e:  # seasons 는 모든 예외를 SeasonError 로 감싼다
                assert isinstance(e.__cause__, _Sent), (name, e)
        config.WEB_DATA = False
        for name, call, err in _web_calls():
            try:
                call()
                raise AssertionError(f"{name}: 꺼졌는데 예외가 없다")
            except _Sent:
                raise AssertionError(f"{name}: 꺼졌는데 요청을 보냈다")
            except err as e:
                assert str(e) == config.WEB_DATA_OFF_MSG, (name, e)
    finally:
        requests.Session.request, config.WEB_DATA = orig_req, orig_on


def test_every_web_request_goes_through_the_concurrency_cap():
    # 세션을 직접 부르면 전역 동시 상한(RANK_MAX_CONCURRENT) 밖에서 요청이 나간다 — 스위치 테스트로는 안 보인다
    routed = []
    orig_get, orig_on, orig_req = ranker.web_get, config.WEB_DATA, requests.Session.request

    def spy(session, url, method="get", **kw):
        routed.append(url)
        raise _Sent()

    def direct(self, *a, **k):
        raise AssertionError("web_get 을 거치지 않은 요청")
    ranker.web_get, config.WEB_DATA, requests.Session.request = spy, True, direct
    try:
        for name, call, err in _web_calls():
            before = len(routed)
            try:
                call()
            except (_Sent, err):
                pass
            assert len(routed) == before + 1, f"{name}: web_get 을 안 거쳤다"
    finally:
        ranker.web_get, config.WEB_DATA, requests.Session.request = orig_get, orig_on, orig_req


def test_web_requests_name_the_app():
    for mod in (ranker, playerinfo, teamcolor):
        ua = mod._session.headers.get("User-Agent", "")
        assert ua == config.WEB_USER_AGENT and "Mozilla" not in ua, (mod.__name__, ua)


def test_teamcolor_off_shows_reason_without_fetching():
    class _NoLoader:
        def __init__(self, *a, **k):
            raise AssertionError("꺼졌는데 팀컬러 조회를 시작했다")

    saved = (config.WEB_DATA, app_main.TeamColorLoader, app_main.RankListLoader,
             dict(_win._team_colors), [lb.text() for lb in _win._teamcolor_status_labels],
             ranker.RANK_PAGES)
    config.WEB_DATA = False
    app_main.TeamColorLoader = app_main.RankListLoader = _NoLoader
    _win._team_colors.clear()
    try:
        for pages in (1000, 1):  # 상대 검색 쪽 · 목록 쪽 둘 다
            ranker.RANK_PAGES = pages
            app_main.MainWindow._on_fetch_team_colors(_win)  # 모듈 위쪽에서 인스턴스 쪽을 막아 뒀다
            texts = {lb.text() for lb in _win._teamcolor_status_labels}
            assert texts == {config.WEB_DATA_OFF_MSG}, (pages, texts)
    finally:
        config.WEB_DATA, app_main.TeamColorLoader, app_main.RankListLoader = saved[:3]
        _win._team_colors.update(saved[3])
        for lb, t in zip(_win._teamcolor_status_labels, saved[4]):
            lb.setText(t)
        ranker.RANK_PAGES = saved[5]


def _run_rank_list(wanted, pages):
    """RankListLoader.run 을 스레드 없이 돌린다 — pages: {쪽: [(닉, 팀컬러, 구단가치)] | None(실패)}."""
    orig = ranker.fetch_rank_rows, ranker.RANK_PAGES

    def fake(page, timeout=10):
        rows = pages[page]
        if rows is None:
            raise ranker.RankerError("못 읽음")
        return ranker.RankPageResult(page, [
            ranker.RankRow(rank=page * 100 + i, profile_sn=page * 100 + i, nickname=r[0], team_color=r[1],
                           team_value=r[2] if r[2] is not None else 7, team_color_emblem=r[3] if len(r) > 3 else "")
            for i, r in enumerate(rows)], "")

    ranker.fetch_rank_rows, ranker.RANK_PAGES = fake, len(pages)
    got, done = {}, []
    try:
        ld = app_main.RankListLoader(set(wanted))
        ld.loaded_many.connect(got.update)
        ld.finished_all.connect(lambda: done.append(True))
        ld.run()
    finally:
        ranker.fetch_rank_rows, ranker.RANK_PAGES = orig
    return ld, got, done


def test_rank_list_marks_outside_only_when_every_page_read():
    pages = {1: [("가", "네덜란드", 1, "countries/14.png"), ("남", "프랑스", 2)], 2: [("나", "", None, "x/y.png")]}
    ld, got, done = _run_rank_list({"가", "나", "밖"}, pages)
    # 원한 사람만 내보낸다(목록의 남은 저장하지 않는다) · 목록에 없으면 '랭킹 밖'("") · 엠블럼이 같이 온다(2.2.1)
    assert got == {"가": ("네덜란드", 1, "countries/14.png"), "나": ("", None, ""), "밖": ("", None, "")}, got
    assert done == [True] and ld.failed_pages == 0
    pages[2] = None  # 한 쪽 실패 — 그 쪽에 있었을 수도 있는 상대를 '랭킹 밖'으로 굳히면 안 된다
    ld, got, done = _run_rank_list({"가", "나", "밖"}, pages)
    assert got == {"가": ("네덜란드", 1, "countries/14.png")}, got
    assert done == [True] and ld.failed_pages == 1


def test_teamcolor_picks_rank_list_only_when_cheaper():
    made = []

    class _Rec:
        def __init__(self, arg):
            made.append((self.kind, arg))
            self.total = 0
            self.loaded_many = self.progress = self.finished_all = self.waiting = self

        def connect(self, *_):
            pass

        def start(self):
            pass

        def isRunning(self):
            return False

    class _Each(_Rec):
        kind = "검색"

    class _List(_Rec):
        kind = "목록"

    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = (config.WEB_DATA, config.DB_PATH, app_main.TeamColorLoader, app_main.RankListLoader,
             ranker.RANK_PAGES, dict(_win._team_colors), _win._teamcolor_loader, _win._matches,
             [lb.text() for lb in _win._teamcolor_status_labels])
    config.WEB_DATA, config.DB_PATH = True, tmp / "t.db"
    app_main.TeamColorLoader, app_main.RankListLoader = _Each, _List
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    try:
        assert len(opps) >= 3, opps
        # 시즌 범위에 상대 2명, 누적 전체엔 그보다 많다
        _win._matches = [m for m in _MATCHES if m.opponent in opps[:2]]
        for pages, want in [(1, ("목록", set(opps))),        # 2명 > 1쪽 → 목록, 모든 시즌 상대까지
                            (2, ("검색", set(opps[:2])))]:   # 2명 ≤ 2쪽 → 시즌 상대만 검색
            made.clear()
            _win._team_colors.clear()
            _win._teamcolor_loader = None
            ranker.RANK_PAGES = pages
            app_main.MainWindow._on_fetch_team_colors(_win)
            assert [(k, set(a)) for k, a in made] == [want], (pages, made)
        # 하루 안의 랭킹 스냅숏이 있으면 상대가 적어도 목록 쪽(스냅숏에서 읽는다 — 요청 0)
        _rank_snapshot(datetime.now() - timedelta(hours=1), {})
        made.clear()
        _win._team_colors.clear()
        app_main.MainWindow._on_fetch_team_colors(_win)
        assert [(k, set(a)) for k, a in made] == [("목록", set(opps))], made
    finally:
        rankcollect.delete_db()
        (config.WEB_DATA, config.DB_PATH, app_main.TeamColorLoader, app_main.RankListLoader,
         ranker.RANK_PAGES) = saved[:5]
        _win._team_colors.clear()
        _win._team_colors.update(saved[5])
        _win._teamcolor_loader, _win._matches = saved[6], saved[7]
        for lb, t in zip(_win._teamcolor_status_labels, saved[8]):
            lb.setText(t)
        for b in _win._teamcolor_fetch_btns:
            b.setEnabled(True)
        shutil.rmtree(tmp, ignore_errors=True)


def _rank_snapshot(when, colors: dict, people: int = 30):
    """config.RANK_DB_PATH 에 스냅숏 하나 — colors: {닉네임: 팀컬러}. 행 닉네임은 colors 의 키 + n1.."""
    names = list(colors) + [f"n{i}" for i in range(1, people - len(colors) + 1)]
    rows = [ranker.RankRow(rank=i, profile_sn=700000 + i, nickname=n, team_color=colors.get(n, ""),
                           team_value=5000 + i, elo=3000.0 - i) for i, n in enumerate(names, start=1)]
    c = rankcollect.open_rank_db()
    try:
        return rankcollect.save_snapshot(c, rows, when)
    finally:
        c.close()


def _tc_min(n: int) -> int:
    """팀컬러 표 최소 경기 수 필터를 n 으로 → 옛 값(두 탭 공용 스핀)."""
    old = _win._teamcolor_min_games()
    _win._teamcolor_min_spins[0].setValue(n)
    return old


class _TeamColorEnv:
    """임시 DB · 웹 데이터 켬 · 팀컬러 상태를 잡아 뒀다 되돌린다."""

    def __enter__(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._saved = (config.WEB_DATA, config.DB_PATH, app_main.TeamColorLoader,
                       app_main.RankListLoader, dict(_win._team_colors), dict(_win._team_values),
                       _win._teamcolor_loader, _win._matches,
                       [lb.text() for lb in _win._teamcolor_status_labels])
        config.WEB_DATA, config.DB_PATH = True, self.tmp / "t.db"
        _win._team_colors.clear()
        _win._teamcolor_loader = None
        self._min = _tc_min(1)   # 픽스처는 4경기라 기본 필터(최소 core.MIN_COND 경기)면 줄이 다 숨는다
        # 픽스처는 무승부가 나는 경기·안 나는 경기가 섞여(A6) 무승부뿐인 상대는 승패 0판이라 최소 1 에도 숨는다 —
        # 여기 테스트는 가르기·축·필터를 재므로 한 집단(승÷전체)으로 둔다. 섞인 식은 test_teamcolor_mixed_formula
        import dataclasses
        _win._matches = [dataclasses.replace(m, draw_possible=None) for m in _win._matches]
        return self

    def __exit__(self, *exc):
        _tc_min(self._min)
        _win._teamcolor_raw.clear()
        (config.WEB_DATA, config.DB_PATH, app_main.TeamColorLoader,
         app_main.RankListLoader) = self._saved[:4]
        _win._team_colors.clear()
        _win._team_colors.update(self._saved[4])
        _win._team_values.clear()
        _win._team_values.update(self._saved[5])
        _win._teamcolor_loader, _win._matches = self._saved[6], self._saved[7]
        for lb, t in zip(_win._teamcolor_status_labels, self._saved[8]):
            lb.setText(t)
        for b in _win._teamcolor_fetch_btns:
            b.setEnabled(True)
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_teamcolor_results_reach_table_and_db():
    # 로더 → 창 배선(받기·끝)과 DB 저장까지. 로더는 start() 에서 바로 내보낸다.
    from PySide6.QtCore import QObject, Signal

    opps = sorted({m.opponent for m in _MATCHES if m.opponent})

    class _Instant(QObject):
        loaded_many = Signal(dict)
        progress = Signal(int, int)
        finished_all = Signal()

        def __init__(self, wanted):
            super().__init__()
            self.wanted, self.total = list(wanted), len(wanted)

        def start(self):
            self.loaded_many.emit({n: ("즉시컬러", 7) for n in self.wanted})
            self.finished_all.emit()

        def isRunning(self):
            return False

    with _TeamColorEnv():
        app_main.TeamColorLoader = app_main.RankListLoader = _Instant
        app_main.MainWindow._on_fetch_team_colors(_win)
        assert all(_win._team_colors.get(n) == "즉시컬러" for n in opps), _win._team_colors
        assert all("조회 완료" in lb.text() for lb in _win._teamcolor_status_labels), \
            [lb.text() for lb in _win._teamcolor_status_labels]
        lst = _win.tc_rate_list
        assert lst.rowCount() == 1 and lst.row(0).label == "즉시컬러"
        conn = store.open_db(config.DB_PATH)
        try:
            saved = store.load_team_colors(conn, opps)
        finally:
            conn.close()
        assert saved == {n: ("즉시컬러", 7, "") for n in opps}, saved


def test_teamcolor_uses_db_cache_for_every_season_before_fetching():
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})

    def no_loader(*a, **k):
        raise AssertionError("DB 에 다 있는데 조회를 시작했다")

    with _TeamColorEnv():
        conn = store.open_db(config.DB_PATH)
        try:
            store.save_team_colors(conn, {n: ("캐시컬러", 1) for n in opps})
        finally:
            conn.close()
        app_main.TeamColorLoader = app_main.RankListLoader = no_loader
        _win._matches = [m for m in _MATCHES if m.opponent == opps[0]]  # 시즌엔 한 명뿐
        app_main.MainWindow._on_fetch_team_colors(_win)
        # 시즌 밖 상대도 DB 에서 미리 채운다 — 시즌을 바꿨을 때 바로 그리게
        assert all(_win._team_colors.get(n) == "캐시컬러" for n in opps), _win._team_colors


def test_position_views_follow_teamcolor_scope():
    # 포지션별 최다 상대(색 필터)·팀컬러 더블클릭 상세도 표와 같은 시즌 범위
    seen = []
    orig = app_main.core.opponent_position_players, None, _win._show_teamcolor_detail
    saved_colors = dict(_win._team_colors)
    old_min = _tc_min(1)

    def spy(details, *a, **k):
        seen.append(len(details))
        return orig[0](details, *a, **k)

    try:
        for m in _MATCHES:
            if m.opponent:
                _win._team_colors[m.opponent] = "범위컬러"
        _win._range_spec = lambda: ("games", 1)
        _win._render_all()
        assert len(_win._slice()[1]) < len(_win._details), "빠른 범위가 좁아지지 않아 비교가 안 된다"
        app_main.core.opponent_position_players = spy
        _win._show_teamcolor_detail = lambda *a, **k: None
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentText("범위컬러")    # [축구장] 은 팀컬러를 골라야 그린다
        lst = _win.tc_rate_list   # [상대 팀컬러] 줄의 두 번 누름(77 B — 옛 랭킹 표 자리)
        assert lst.rowCount() == 1 and lst.row(0).label == "범위컬러", [r.label for r in lst.rows()]
        lst.doubleClicked.emit(lst.model().index(0, 0))
        assert seen == [len(_win._details)] * 2, (seen, len(_win._details))
    finally:
        app_main.core.opponent_position_players = orig[0]
        _win._show_teamcolor_detail = orig[2]
        _win._team_colors.clear()
        _win._team_colors.update(saved_colors)
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentIndex(0)
        del _win._range_spec
        _tc_min(old_min)
        _win._render_all()


def test_teamcolor_counts_season_scope_not_display_range():
    # 빠른 범위를 1경기로 좁혀도 팀컬러 표는 시즌(지금은 전체) 경기를 다 센다
    saved = dict(_win._team_colors), None, _tc_min(1)
    try:
        for m in _MATCHES:
            if m.opponent:
                _win._team_colors[m.opponent] = "테스트컬러"
        _win._range_spec = lambda: ("games", 1)
        _win._render_all()
        n = sum(1 for m in _win._matches if m.opponent)
        assert len(_win._slice()[0]) == 1 and n > 1, n
        lst = _win.tc_rate_list
        assert lst.rowCount() == 1 and lst.row(0).games == n, [(r.label, r.games) for r in lst.rows()]
        note = _win._teamcolor_note_labels[0]
        assert note.isHidden() and not note.text(), note.text()   # 78 ① 다 아는 상대 · 숨긴 종류 없음 → 설명 없음
        _win._team_colors.clear()
        _win._render_all()
        assert not note.isHidden() and "팀컬러를 아는 상대가 없습니다" in note.text(), note.text()
    finally:
        _win._team_colors.clear()
        _win._team_colors.update(saved[0])
        del _win._range_spec
        _tc_min(saved[2])
        _win._render_all()


# ── 2.2.1 팀컬러 — 엠블럼으로 가르기 · 효과 창 · 표 ─────────────────────────────────────
BOOST, CLUB = "teamcolorboost/4_l999867.png", "crests/l130634.png"   # Spartan 의 두 팀컬러(실측 목록)


def _table_dump(tbl):
    return [[tbl.item(r, c).text() if tbl.item(r, c) else None for c in range(tbl.columnCount())]
            for r in range(tbl.rowCount())]


def _rate_dump(lst):
    """승률 줄 목록 → 줄마다 보이는 값(67 R3 — [팀컬러 승률] 이 표에서 줄 목록으로)."""
    return [(r.label, r.sub, r.games, r.win, r.draw, r.lose, r.rate_text, r.diff, r.weak, r.icon) for r in lst.rows()]


def test_teamcolor_dup_names_split_and_save_raw():
    # 같은 이름(Spartan)의 강화·클럽 팀컬러를 쓰는 두 상대 → 두 줄 · 저장은 넥슨 이름 + "시각\t키"(화면 글자 아님)
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    with _TeamColorEnv():
        _win._teamcolor_pending = opps[:2]
        _win._teamcolor_loader = None
        _win._on_teamcolor_loaded({opps[0]: ("Spartan", 7, BOOST), opps[1]: ("Spartan", 9, CLUB)})
        assert (_win._team_colors[opps[0]], _win._team_colors[opps[1]]) == ("Spartan · 강화", "Spartan · 클럽")
        _win._on_teamcolor_finished()
        names = {r.label for r in _win.tc_rate_list.rows()}
        assert {"Spartan · 강화", "Spartan · 클럽"} <= names, names
        c = store.open_db(config.DB_PATH)
        try:
            rows = {r["nickname"]: (r["team_color"], r["emblem"]) for r in c.execute(
                "SELECT nickname, team_color, emblem FROM team_colors")}
            loaded = store.load_team_colors(c, opps[:2])
        finally:
            c.close()
        assert rows[opps[0]][0] == "Spartan" and rows[opps[0]][1].endswith("\t" + BOOST), rows
        assert loaded[opps[1]] == ("Spartan", 9, CLUB), loaded
        # DB 에서 다시 읽어도 같은 글자(다른 실행 · 다른 시즌으로 바꿨을 때)
        _win._team_colors.clear()
        _win._load_cached_team_colors(set(opps[:2]))
        assert _win._team_colors[opps[1]] == "Spartan · 클럽", _win._team_colors


def test_teamcolor_table_same_before_and_after_meta():
    # 가르기는 목록 없이 선다(2회차 [상]) — 효과 창이 목록을 받기 전후로 표의 행·값이 같다
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    with _TeamColorEnv():
        _win._on_teamcolor_loaded({opps[0]: ("Spartan", 7, BOOST), opps[1]: ("Spartan", 9, ""),
                                   opps[2]: ("리옹", 3, "crests/l66.png")})
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        before = _rate_dump(_win.tc_rate_list)
        assert any(r[0] == "Spartan (구분 전)" for r in before), before
        c = store.open_db(config.DB_PATH)
        try:
            gz = pathlib.Path(__file__).parent / "fixtures" / "teamcolor_list.html.gz"
            import gzip
            store.save_teamcolor_meta(c, teamcolor.parse_list(gzip.decompress(gz.read_bytes()).decode("utf-8")),
                                      datetime.now().date().isoformat())
        finally:
            c.close()
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        assert _rate_dump(_win.tc_rate_list) == before


def test_teamcolor_weak_rows_sink_both_ways_and_stay_uncolored():
    # 흐린 줄(상대 수 < 기준 · 구분 전)은 숫자도 흐리고 색이 없으며, 어느 정렬이든 맨 아래
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    with _TeamColorEnv():
        saved_min = config.TEAMCOLOR_MIN_OPPONENTS
        config.TEAMCOLOR_MIN_OPPONENTS = 2
        try:
            # 강한 줄: 두 상대(X) · 약한 줄: 한 상대(Y)
            _win._on_teamcolor_loaded({opps[0]: ("X", 1, ""), opps[1]: ("X", 1, ""), opps[2]: ("Y", 1, "")})
            orig_cond = config.TEAMCOLOR_MIN_GAMES   # 75 — 팀컬러 흐림 기준은 이 상수(예전엔 core.MIN_COND)
            config.TEAMCOLOR_MIN_GAMES = 1
            try:
                _win._render_teamcolor_tabs(*_win._teamcolor_scope())
            finally:
                config.TEAMCOLOR_MIN_GAMES = orig_cond
            # [팀컬러 승률] 줄 목록(67 R3) — 흐린 줄은 기준 대비 칸이 없고 숫자·막대가 흐리며 어느 정렬이든 맨 아래
            import widgets
            lst = _win.tc_rate_list
            rows = {r.label: r for r in lst.rows()}
            assert set(rows) == {"X", "Y"}, rows
            weak, strong = rows["Y"], rows["X"]
            assert weak.weak and not strong.weak and weak.tip and weak.diff is None, (weak.tip, weak.diff)
            assert strong.diff is not None, "기준 대비가 없으면 '흐린 줄만 칸이 없다' 단언이 아무것도 못 잰다"
            col = widgets.RateRowDelegate.colors(weak)
            assert col["rate"] == T.TEXT_DIM and col["diff"] == T.TEXT_DIM, col
            for i in range(_win.cb_tc_rate_sort.count()):
                _win.cb_tc_rate_sort.setCurrentIndex(i)
                assert lst.row(lst.rowCount() - 1).label == "Y", (_win.cb_tc_rate_sort.currentText(), _rate_dump(lst))
            _win.cb_tc_rate_sort.setCurrentIndex(0)
            # 픽스처는 흐린 줄이 경기도 적어 매치수 순에선 원래 아래다 — 흐린 줄이 더 많은 지어낸 줄로(구분 전 · 상대 수 미달)
            keep_rows = _win._tc_rate_rows
            try:
                _win._tc_rate_rows = [widgets.RateRow("많은흐림", 50, 0, 50, weak=True), widgets.RateRow("적은", 3, 0, 1)]
                _win._show_tc_rate_rows()
                assert [r.label for r in lst.rows()] == ["적은", "많은흐림"], _rate_dump(lst)
            finally:
                _win._tc_rate_rows = keep_rows
                _win._show_tc_rate_rows()
        finally:
            config.TEAMCOLOR_MIN_OPPONENTS = saved_min


def test_teamcolor_min_games_filter_hides_and_counts():
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    with _TeamColorEnv():
        _win._on_teamcolor_loaded({n: ("필터컬러", 1, "") for n in opps})
        games = sum(1 for m in _win._matches if m.opponent)
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        assert _win.tc_rate_list.rowCount() == 1
        assert len(_win._teamcolor_min_spins) == 1   # 77 B — 두 탭이 한 화면이 됐다
        _win._teamcolor_min_spins[0].setValue(games + 1)
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        assert _win.tc_rate_list.rowCount() == 0
        assert "1종 숨김" in _win._teamcolor_note_labels[0].text(), _win._teamcolor_note_labels[0].text()


def test_teamcolor_value_sub_and_sort():
    # 77 B — 옛 [팀컬러 랭킹] 의 팀가치 열이 줄 보조 글(평균 · 최저~최고)·말풍선·정렬 "팀가치 높은 순" 으로
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    fv = ranker.format_team_value
    with _TeamColorEnv():
        saved = config.TEAMCOLOR_MIN_OPPONENTS, config.TEAMCOLOR_MIN_GAMES
        # 픽스처는 경기·상대가 적어 기본 기준이면 줄이 다 흐리다 — 정렬은 흐림 없는 줄로 잰다
        config.TEAMCOLOR_MIN_OPPONENTS, config.TEAMCOLOR_MIN_GAMES = 1, 1
        try:
            _value_sub_and_sort(opps, fv)
        finally:
            config.TEAMCOLOR_MIN_OPPONENTS, config.TEAMCOLOR_MIN_GAMES = saved


def _value_sub_and_sort(opps, fv):
    # 경기 수가 많은 쪽(A 두 상대)이 팀가치는 낮다 — 매치수 순과 팀가치 순이 갈려야 정렬을 잰다
    _win._on_teamcolor_loaded({opps[0]: ("A", 10 ** 9, ""), opps[1]: ("A", 3 * 10 ** 9, ""),
                               opps[2]: ("B", 10 ** 11, "")})
    _win._render_teamcolor_tabs(*_win._teamcolor_scope())
    lst = _win.tc_rate_list
    rows = {r.label: r for r in lst.rows()}
    assert set(rows) == {"A", "B"}, rows
    a = rows["A"]
    avg = (10 ** 9 + 3 * 10 ** 9) // 2
    assert a.value == avg and f"평균 {fv(avg)} · {fv(10 ** 9)} ~ {fv(3 * 10 ** 9)}" in a.sub, a.sub
    assert f"최저 {fv(10 ** 9)}" in a.tip and f"최고 {fv(3 * 10 ** 9)}" in a.tip, a.tip
    assert not any(r.weak for r in lst.rows()), [(r.label, r.tip) for r in lst.rows() if r.weak]
    assert rows["A"].games > rows["B"].games, rows   # 픽스처가 바뀌어 같아지면 아래 단언이 매치수 순과 구분이 안 된다
    sorts = [_win.cb_tc_rate_sort.itemData(i) for i in range(_win.cb_tc_rate_sort.count())]
    assert "recent" not in sorts and sorts[-1] == "value", sorts
    try:
        assert [r.label for r in lst.rows()] == ["A", "B"], [r.label for r in lst.rows()]   # 매치수 순
        _win.cb_tc_rate_sort.setCurrentIndex(sorts.index("value"))
        assert [r.label for r in lst.rows()] == ["B", "A"], [(r.label, r.value) for r in lst.rows()]
        # 팀가치를 모르는 상대(구버전 캐시 꼴)만 남은 팀컬러는 "팀가치 모름" · 팀가치 순에서 맨 아래
        _win._team_values.pop(opps[2], None)
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        b = {r.label: r for r in lst.rows()}["B"]
        assert b.value is None and "팀가치 모름" in b.sub, b.sub
        assert [r.label for r in lst.rows()] == ["A", "B"], [(r.label, r.value) for r in lst.rows()]
    finally:
        _win.cb_tc_rate_sort.setCurrentIndex(0)


def test_teamcolor_mixed_formula():
    keep = config.WIN_RATE_DRAWS_IN
    config.WIN_RATE_DRAWS_IN = False   # A6 섞임 식의 화면 처리 자체를 잰다 — 앱 기본(승÷전체)에선 섞임이 없다
    try:
        _teamcolor_mixed_formula()
    finally:
        config.WIN_RATE_DRAWS_IN = keep


def _teamcolor_mixed_formula():
    # 픽스처 그대로(섞인 묶음): 무승부뿐인 상대(0003)의 팀컬러는 승패 0판이라 최소 1 에도 숨고, 단위 글자가 "승패 판"
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    draw_opp = next(m.opponent for m in _MATCHES if m.result == "무")
    with _TeamColorEnv():
        _win._matches = list(_MATCHES)   # 환경이 한 집단으로 바꾼 것을 되돌린다
        _win._on_teamcolor_loaded({n: ("무색" if n == draw_opp else "유색", 1, "") for n in opps})
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        names = {r.label for r in _win.tc_rate_list.rows()}
        assert names == {"유색"}, names   # games 로 거르면 "무색" 이 0.0% 로 남는다
        assert _win._teamcolor_min_units[0].text() == "승패 판 이상만", _win._teamcolor_min_units[0].text()
        rate = _win.tc_rate_list.row(0).rate_text
        assert rate.endswith("*"), rate


def test_teamcolor_double_click_on_dup_name():
    # "Spartan · 강화" 줄을 더블클릭하면 그 상대만(글자 키 그대로 대조)
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    got = []
    orig = _win._show_teamcolor_detail
    with _TeamColorEnv():
        _win._on_teamcolor_loaded({opps[0]: ("Spartan", 1, BOOST), opps[1]: ("Spartan", 1, CLUB)})
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        seen = []
        orig_pp = app_main.core.opponent_position_players
        app_main.core.opponent_position_players = lambda *a, nicknames=None, **k: (seen.append(nicknames), [])[1]
        _win._show_teamcolor_detail = lambda color, players: got.append(color)
        try:
            lst = _win.tc_rate_list
            i = next(i for i, row in enumerate(lst.rows()) if row.label == "Spartan · 강화")
            lst.doubleClicked.emit(lst.model().index(i, 0))
            assert got == ["Spartan · 강화"] and seen == [{opps[0]}], (got, seen)
        finally:
            _win._show_teamcolor_detail = orig
            app_main.core.opponent_position_players = orig_pp


def test_teamcolor_info_opens_only_from_button():
    # 67 R3 — 한 번 누름은 고르기만(창 안 엶 · 두 번 누름 앞에 오는 함정) · [팀컬러 정보] 는 고른 줄이 있을 때만 켜지고 그 줄을 연다
    opps = sorted({m.opponent for m in _MATCHES if m.opponent})
    opened = []
    orig = _win._open_teamcolor_effect
    with _TeamColorEnv():
        _win._on_teamcolor_loaded({opps[0]: ("Spartan", 1, CLUB), opps[1]: ("리옹", 1, "crests/l66.png")})
        _win._render_teamcolor_tabs(*_win._teamcolor_scope())
        _win._open_teamcolor_effect = opened.append
        try:
            lst = _win.tc_rate_list
            from PySide6.QtWidgets import QHBoxLayout
            _row, fresh = _win._teamcolor_pick_row(QHBoxLayout())   # 처음 그리기 전(탭을 아직 안 연 창)에도 꺼져 있다
            assert not fresh.isEnabled()
            fresh.deleteLater()
            btn = _win.btn_tc_rate_info
            assert not btn.isEnabled(), "고른 줄이 없는데 단추가 켜져 있다"
            btn.click()
            assert opened == [], opened
            i = next(i for i, row in enumerate(lst.rows()) if row.label == "Spartan · 클럽")
            lst.setCurrentIndex(lst.model().index(i, 0))   # 한 번 누름 = 고르기만
            assert opened == [] and btn.isEnabled()
            btn.click()
            assert opened == ["Spartan · 클럽"], opened
            _win._render_teamcolor_tabs(*_win._teamcolor_scope())   # 다시 채우면 고른 줄이 사라진다 → 단추 꺼짐
            assert not btn.isEnabled()
        finally:
            _win._open_teamcolor_effect = orig


class _TcFake:
    """teamcolor.fetch_* 를 가짜로 — 보낸 요청을 센다. 실픽스처(목록 801개 · 맨유 상세 · 선수 6명)."""

    def __init__(self, players=None, fail=None):
        import gzip
        fx = pathlib.Path(__file__).parent / "fixtures"
        self.items = teamcolor.parse_list(gzip.decompress((fx / "teamcolor_list.html.gz").read_bytes()).decode("utf-8"))
        self.steps = teamcolor.parse_detail((fx / "teamcolor_detail_1005.html").read_text(encoding="utf-8"))
        self.players = players if players is not None else \
            teamcolor.parse_players((fx / "teamcolor_players_1005.json").read_text(encoding="utf-8"))
        self.fail, self.calls = fail, []

    def __enter__(self):
        self._orig = teamcolor.fetch_list, teamcolor.fetch_detail, teamcolor.fetch_players, images.fetch_url
        app_main.TeamColorEffectLoader.start = lambda ld: ld.run()   # 스모크는 QThread.start 를 막는다 — 그 자리에서

        def maybe(kind):
            self.calls.append(kind)
            if self.fail:
                raise teamcolor.TeamColorError("가짜", self.fail)

        teamcolor.fetch_list = lambda: (maybe("list"), self.items)[1]
        teamcolor.fetch_detail = lambda tid: (maybe(("detail", tid)), self.steps)[1]
        teamcolor.fetch_players = lambda tid, cap=None: (  # 넥슨처럼 한 번에 최대 100명(T6)
            maybe(("players", tid, cap)),
            [p for p in self.players if cap is None or p.ovr <= cap][:config.TEAMCOLOR_PLAYERS_PAGE])[1]
        images.fetch_url = lambda *a, **k: None
        return self

    def __exit__(self, *exc):
        teamcolor.fetch_list, teamcolor.fetch_detail, teamcolor.fetch_players, images.fetch_url = self._orig
        del app_main.TeamColorEffectLoader.start   # 물려받은 것으로 되돌린다(대입하면 바인딩 메서드가 깨진다 — PyQt6 때 실측)


def _tc_dialog_open(label, timeout=10):
    _win._open_teamcolor_effect(label)
    dlg = _win._teamcolor_dialog
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(ld.isRunning() for ld in _win._tc_effect_loaders):
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    return dlg


def test_teamcolor_effect_dialog_flow():
    with _TeamColorEnv():
        with _TcFake() as fake:
            dlg = _tc_dialog_open("맨체스터 유나이티드")
            assert not dlg.isModal() and dlg.isVisible() and dlg.parent() is _win
            assert fake.calls[0] == "list" and ("detail", 1005) in fake.calls, fake.calls
            assert dlg.tbl_steps.rowCount() == 4 and dlg.tbl_players.rowCount() == 6, \
                (dlg.tbl_steps.rowCount(), dlg.tbl_players.rowCount())
            assert "전체 능력치 +4" in dlg.tbl_steps.item(3, 2).text()
            assert not dlg.btn_more.isVisible(), "100명보다 적게 왔는데 [더 보기]를 보였다"
            sp = dlg.tbl_players.item(0, 1).data(Qt.ItemDataRole.UserRole)
            assert sp == 110226764, sp
            # 같은 팀컬러를 다시 누르면 요청 없이 앞으로만
            n = len(fake.calls)
            _tc_dialog_open("맨체스터 유나이티드")
            assert len(fake.calls) == n and _win._teamcolor_dialog is dlg, fake.calls[n:]
            # 다른 팀컬러 — 목록은 캐시(7일) · 단계·선수만 받는다
            fake.calls.clear()
            _tc_dialog_open("01-03 성남FC 리그3연패 베스트")
            assert "list" not in fake.calls and ("detail", 30007) in fake.calls, fake.calls
            # 카드 캐시(B) — 급여 열만 · 시세
            c = store.open_db(config.DB_PATH)
            try:
                info = store.load_card_info(c, [110226764])
                prices = store.load_card_prices(c, [110226764])
            finally:
                c.close()
            assert info[110226764]["salary"] == 43 and info[110226764]["base_ovr"] is None, info
            assert prices[(110226764, 1)][0] == 150000, prices
            # 목록에 없는 팀컬러 — 오늘 받은 목록이면 다시 안 받는다(요청 0)
            fake.calls.clear()
            _tc_dialog_open("단일 팀")
            assert fake.calls == [] and "목록에 없는" in dlg.lb_status.text(), (fake.calls, dlg.lb_status.text())
            # (구분 전) — 두 후보 단추, 고르면 그 id 로
            _tc_dialog_open("Spartan (구분 전)")
            btns = [dlg.box_pick.itemAt(i).widget() for i in range(dlg.box_pick.count())
                    if dlg.box_pick.itemAt(i).widget()]
            assert sorted(b.text() for b in btns) == ["Spartan · 강화", "Spartan · 클럽"], [b.text() for b in btns]
        dlg.close()


def test_teamcolor_effect_dialog_more_and_off_and_errors():
    big = [teamcolor.TeamColorPlayer(i, f"p{i}", "ST", 130 - i // 40, 10, {1: 1000 + i})
           for i in range(150)]
    with _TeamColorEnv():
        with _TcFake(players=big) as fake:
            dlg = _tc_dialog_open("맨체스터 유나이티드")
            assert dlg.tbl_players.rowCount() == config.TEAMCOLOR_PLAYERS_PAGE and dlg.btn_more.isVisible()
            dlg._on_more()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(ld.isRunning() for ld in _win._tc_effect_loaders):
                QApplication.processEvents()
                time.sleep(0.01)
            QApplication.processEvents()
            assert dlg.tbl_players.rowCount() == 150 and not dlg.btn_more.isVisible(), dlg.tbl_players.rowCount()
            assert fake.calls[-1] == ("players", 1005, big[99].ovr), fake.calls
        # 웹 데이터 꺼짐 — 캐시를 보이고 선수 목록은 꺼짐 문구 · 요청 0
        config.WEB_DATA = False
        with _TcFake() as fake:
            _win._teamcolor_dialog._label = ""
            dlg = _tc_dialog_open("맨체스터 유나이티드")
            assert fake.calls == [] and dlg.tbl_steps.rowCount() == 4, fake.calls
            assert config.WEB_DATA_OFF_MSG in dlg.lb_players.text(), dlg.lb_players.text()
        # 목록이 아직 없고 꺼짐 → 꺼짐 문구(요청 0)
        c = store.open_db(config.DB_PATH)
        c.execute("DELETE FROM teamcolor_meta")
        c.commit()
        c.close()
        with _TcFake() as fake:
            _win._teamcolor_dialog._label = ""
            dlg = _tc_dialog_open("맨체스터 유나이티드")
            assert fake.calls == [] and dlg.lb_status.text() == config.WEB_DATA_OFF_MSG, dlg.lb_status.text()
        # 켜져 있는데 넥슨이 넘김(점검) — 사람 말 문구
        config.WEB_DATA = True
        with _TcFake(fail="down"):
            _win._teamcolor_dialog._label = ""
            dlg = _tc_dialog_open("맨체스터 유나이티드")
            assert dlg.lb_status.text() == teamcolor.MESSAGES["down"], dlg.lb_status.text()
        dlg.close()


def test_my_color_strips_label_for_ranker_side():
    # 추천의 내 팀컬러 — 검색 때 읽은 데이터센터 값이 없으면 상대 캐시 글자를 쓰는데, 랭커 쪽은 넥슨 이름이라 되돌린다
    saved = _win._rank, _win._nick, dict(_win._team_colors)
    try:
        _win._rank, _win._nick = None, "나의닉"
        _win._team_colors["나의닉"] = "Spartan · 클럽"
        assert _win._my_color([]) == "Spartan", _win._my_color([])
    finally:
        _win._rank, _win._nick = saved[0], saved[1]
        _win._team_colors.clear()
        _win._team_colors.update(saved[2])


def test_shutdown_stops_teamcolor_effect_loaders():
    # 갈아 끼운(물러난) 로더까지 종료 표가 멈춤을 요청한다 — 창이 먼저 닫혀도. 스모크는 QThread.start 가 막혀 있어
    # "도는 중"을 흉내 낸다(시작은 했고 아직 안 끝남)
    cls = app_main.TeamColorEffectLoader
    # 되돌릴 땐 지운다 — 물려받은 바인딩 메서드를 하위 클래스에 다시 대입하면 이후 호출이 깨진다
    keep = {n: cls.__dict__.get(n) for n in ("start", "isRunning")}, _win._tc_effect_loaders, _win._teamcolor_dialog
    cls.start, cls.isRunning = (lambda ld: None), (lambda ld: True)
    try:
        _win._tc_effect_loaders, _win._teamcolor_dialog = [], None
        _win._open_teamcolor_effect("맨체스터 유나이티드")
        _win._teamcolor_dialog.open_label("01-03 성남FC 리그3연패 베스트")   # 첫 로더를 물리고 갈아 끼운다
        loaders = list(_win._tc_effect_loaders)
        assert len(loaders) == 2 and loaders[0]._cancel and not loaders[1]._cancel, loaders
        _win._teamcolor_dialog.close()
        left = _win.shutdown(fast=True)
        assert all(ld in left for ld in loaders) and all(ld._cancel for ld in loaders), left
    finally:
        for n, v in keep[0].items():
            if v is None:
                delattr(cls, n)
            else:
                setattr(cls, n, v)
        if _win._teamcolor_dialog is not None:
            _win._teamcolor_dialog.deleteLater()
        _win._tc_effect_loaders, _win._teamcolor_dialog = keep[1], keep[2]


# ── API 키 입력 ───────────────────────────────────────────────────────
class _TempEnv:
    """config.ENV_PATH·API_KEY·안내 동의·웹 데이터를 임시로 돌려 실제 .env 를 안 건드린다."""
    _VARS = (config.API_KEY_VAR, config.WEB_DATA_VAR, config.NOTICE_VAR)

    def __enter__(self):
        self._dir = tempfile.mkdtemp()
        self._saved = (config.ENV_PATH, config.API_KEY, config.WEB_DATA, config.NOTICE_ACCEPTED,
                       {v: os.environ.get(v) for v in self._VARS})
        config.ENV_PATH = pathlib.Path(self._dir) / ".env"
        return config.ENV_PATH

    def __exit__(self, *exc):
        config.ENV_PATH, config.API_KEY, config.WEB_DATA, config.NOTICE_ACCEPTED, env = self._saved
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._dir, ignore_errors=True)


def test_web_data_off_and_notice_due_on_fresh_or_old_env():
    # 기본값은 코드에서 바로 재야 한다 — v0.3.0 의 .env(웹 데이터 줄 없음)로 새 프로세스를 띄운다
    import subprocess
    for env_text, want in [("", "False True"), ("NEXON_API_KEY=x\n", "False True"),
                           (f"FIFA_WEB_DATA=1\nFIFA_NOTICE={config.NOTICE_VERSION}\n", "True False"),
                           # 1.0.x 에서 동의한 사람 — 1.1.1 은 랭킹 수집·ELO 기록 안내가 늘어 다시 묻는다
                           ("FIFA_WEB_DATA=1\nFIFA_NOTICE=1\n", "True True"),
                           ("FIFA_NOTICE=junk\n", "False True")]:
        d = tempfile.mkdtemp()
        try:
            pathlib.Path(d, ".env").write_text(env_text, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in _TempEnv._VARS}
            env["FIFA_DATA_DIR"] = d
            out = subprocess.run(
                [sys.executable, "-c",
                 "import config; print(config.WEB_DATA, config.notice_needed())"],
                cwd=_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60)
            assert out.stdout.strip() == want, (env_text, out.stdout, out.stderr[-500:])
        finally:
            shutil.rmtree(d, ignore_errors=True)


def test_notice_dialog_web_off_by_default_and_saves_choice():
    for tick_web in (False, True):
        with _TempEnv() as env:
            config.WEB_DATA, config.NOTICE_ACCEPTED = True, 0  # v0.3.0 — 고지 없이 켜져 있던 사람
            dlg = app_main.NoticeDialog()
            assert not dlg.chk_web.isChecked(), "예전에 켜져 있었어도 처음엔 꺼진 채로 보여야 한다"
            assert not dlg.btn_ok.isEnabled(), "동의 없이 시작할 수 있다"
            dlg.chk_web.setChecked(tick_web)
            dlg.chk_agree.setChecked(True)
            assert dlg.btn_ok.isEnabled()
            dlg.btn_ok.click()
            assert dlg.result() == app_main.QDialog.DialogCode.Accepted
            lines = env.read_text(encoding="utf-8").splitlines()
            assert f"FIFA_WEB_DATA={int(tick_web)}" in lines and f"FIFA_NOTICE={config.NOTICE_VERSION}" in lines, lines
            assert config.WEB_DATA is tick_web and not config.notice_needed()


def test_about_button_on_both_pages_toggles_web_data():
    for idx in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
        btns = [b for b in _win.stack.widget(idx).findChildren(app_main.QPushButton)
                if b.objectName() == "aboutButton"]
        assert len(btns) == 1, (idx, len(btns))
        opened = []
        orig = app_main.AboutDialog.exec
        app_main.AboutDialog.exec = lambda self: opened.append(self)
        try:
            btns[0].click()
        finally:
            app_main.AboutDialog.exec = orig
        assert len(opened) == 1, (idx, opened)
    with _TempEnv() as env:
        config.WEB_DATA = False
        dlg = app_main.AboutDialog()
        assert not dlg.chk_web.isChecked()
        dlg.chk_web.setChecked(True)
        assert config.WEB_DATA is True and "FIFA_WEB_DATA=1" in env.read_text(encoding="utf-8")
        dlg.chk_web.setChecked(False)
        assert config.WEB_DATA is False and "FIFA_WEB_DATA=0" in env.read_text(encoding="utf-8")


def test_no_trademark_in_window_names():
    # 화면 이름에서 FIFA·FC ONLINE 을 뺐다(상표) — 다시 들어오면 빨개진다
    # 창 자신의 글만 — [정보] 창의 상표 고지("FC 온라인… 각 사의 상표")는 있어야 하는 글이다
    texts = [_win.windowTitle()] + [lb.text() for lb in _win.findChildren(app_main.QLabel)
                                    if lb.window() is _win]
    bad = [t for t in texts if re.search(r"FC ONLINE|FIFA|피파", t)]
    assert not bad, bad


def test_save_api_key_replaces_only_key_line():
    with _TempEnv() as env:
        env.write_text("FIFA_X=1\nNEXON_API_KEY=old\n", encoding="utf-8")
        config.save_api_key("  new  ")
        assert env.read_text(encoding="utf-8").splitlines() == ["FIFA_X=1", "NEXON_API_KEY=new"]
        assert config.API_KEY == "new", config.API_KEY


def test_check_key_judges_by_error_code():
    def fake(err):
        def _get(self, path, **params):
            if err:
                raise err
            return {"ouid": "x"}
        return _get

    E = nexon_api.NexonAPIError
    cases = [  # (응답, 통과해야 하나)
        (None, True),
        (E("없는 닉네임", code="OPENAPI00004", status=400), True),   # 맞는 키의 실측 응답
        (E("거절", code=nexon_api.KEY_INVALID_CODE, status=400), False),
        (E("네트워크 오류"), False),                                  # status 없음
        (E("호출량 초과", code="OPENAPI00007", status=429), False),
        (E("서버 오류", code="OPENAPI00001", status=500), False),
    ]
    orig = nexon_api.FCOnlineAPI._get
    try:
        for err, ok in cases:
            nexon_api.FCOnlineAPI._get = fake(err)
            got = nexon_api.check_key("some_key")
            assert (got is None) == ok, (err and err.message, got)
    finally:
        nexon_api.FCOnlineAPI._get = orig
    assert nexon_api.check_key("   ") == "키를 입력하세요."


def test_key_dialog_saves_only_accepted_key():
    with _TempEnv() as env:
        dlg = app_main.ApiKeyDialog()
        dlg.show()
        dlg._on_checked("bad_key", "API 키가 유효하지 않습니다.")
        assert not env.exists(), "거절된 키가 저장됐다"
        assert dlg.result() != app_main.QDialog.DialogCode.Accepted
        assert "유효하지" in dlg.lb_msg.text(), dlg.lb_msg.text()
        dlg._on_checked("good_key", "")
        assert dlg.result() == app_main.QDialog.DialogCode.Accepted
        assert "NEXON_API_KEY=good_key" in env.read_text(encoding="utf-8")


def test_loader_routes_rejected_key_to_dialog():
    class _Api:
        def __init__(self, code):
            self.code = code

        def get_ouid(self, nick):
            raise nexon_api.NexonAPIError("msg", code=self.code, status=400)

    for code, want in ((nexon_api.KEY_INVALID_CODE, "key"), ("OPENAPI00009", "failed")):
        got = []
        ld = app_main.MatchLoader(_Api(code), "닉", 52)
        ld.key_invalid.connect(lambda m: got.append("key"))
        ld.failed.connect(lambda m: got.append("failed"))
        ld.run()
        assert got == [want], (code, got)


class _DetailApi:
    """픽스처 4경기를 돌려주되, bad 에 든 경기는 code 오류로 실패한다."""

    def __init__(self, bad, code, only=None):
        self.bad, self.code = set(bad), code
        self.by_id = {d["matchId"]: d for d in _DETAILS if only is None or d["matchId"] in only}
        self.forgotten = []

    def get_ouid(self, nick):
        return _OUID

    def get_user_basic(self, ouid):
        return {"nickname": "테스트구단주"}

    def forget_details(self, ids):
        self.forgotten.extend(ids)

    def get_match_ids(self, ouid, matchtype, offset, limit):
        return list(self.by_id) if offset == 0 else []

    def get_match_detail(self, mid):
        if mid in self.bad:
            raise nexon_api.NexonAPIError("x", code=self.code, status=429)
        return self.by_id[mid]

    def get_meta(self, name):
        return []


def _run_loader(api):
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA
    config.DB_PATH, config.WEB_DATA = tmp / "t.db", False  # 랭킹 스크래핑도 안 나가게
    got = {"ok": [], "quota": [], "failed": []}
    try:
        ld = app_main.MatchLoader(api, "닉", 52)
        ld.finished_ok.connect(lambda *a: got["ok"].append(a[6]))  # 새로 저장한 수
        ld.quota_hit.connect(got["quota"].append)
        ld.failed.connect(got["failed"].append)
        ld.run()
        conn = store.open_db(config.DB_PATH)
        try:
            got["db"] = store.match_count(conn, _OUID, 52)
        finally:
            conn.close()
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
    return got


def test_compare_loader_partial_only_for_unseen_account():
    # 76 U14 — 받는 중간 결과는 저장된 경기가 없는 계정만 · 켠 로더만. 저장분이 있는데 보내면 최신 몇 판과 옛 기록
    # 사이가 비어 "최근 N판"이 틀린다
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA, config.COMPARE_PARTIAL_EVERY
    config.DB_PATH, config.WEB_DATA, config.COMPARE_PARTIAL_EVERY = tmp / "t.db", False, 1

    def run(emit, only=None):
        parts, ok = [], []
        ld = app_main.MatchLoader(_DetailApi([], "", only=only), "닉", 52, emit_partial=emit)
        ld.partial.connect(lambda ms, ds, ouid: parts.append((len(ms), len(ds), ouid)))
        ld.finished_ok.connect(lambda *a: ok.append(len(a[0])))
        ld.run()
        assert ok, "끝 신호가 없다"
        return parts

    ids = sorted(d["matchId"] for d in _DETAILS)
    try:
        assert run(False, ids[:2]) == []                       # 끈 로더(메인 검색)는 안 보낸다
        # 처음 보는 계정(새 DB) — 한 판마다(문턱 1) · 마지막 판은 끝 신호가 대신한다
        config.DB_PATH = tmp / "t2.db"
        n = len(_DETAILS)
        got = run(True)
        assert got == [(k, k, _OUID) for k in range(1, n)], got
        # 저장분이 있는 계정 — 앞의 2판만 둔 DB 에서 나머지를 받아도 안 보낸다
        config.DB_PATH = tmp / "t3.db"
        run(True, ids[:2])
        assert run(True) == [], "저장분이 있는 계정에 중간 결과를 보냈다"
    finally:
        config.DB_PATH, config.WEB_DATA, config.COMPARE_PARTIAL_EVERY = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_compare_follows_top_bar_and_marks_cut():
    # 76 — 구단주 비교는 위쪽 바(시즌 + 빠른 범위)로 두 사람을 똑같이 자른다 · 바를 바꾸면 다시 받지 않고 다시 그린다 ·
    # 범위가 기록 끝에 닿았는데 기록이 범위 시작보다 늦게 시작하면 경기 수 옆 ⚠ + 말풍선 · 받는 중·멈춤이면 그것도 ⚠
    import dataclasses
    opp = [dataclasses.replace(m, match_id=f"o{i}") for i, m in enumerate(_MATCHES)]
    saved = (_win._fill_compare_squad, _win._compare_last)
    _win._fill_compare_squad = lambda *a: None
    t = _win.tbl_compare
    head = lambda c: t.horizontalHeaderItem(c)   # noqa: E731
    oldest = min(m.match_date for m in opp)
    n = len(_MATCHES)
    try:
        _win._compare_last = ("상대", opp, "opp", [], "")
        _win._range_start = lambda: oldest - timedelta(days=1)   # 시즌이 기록보다 먼저 시작 — 앞은 못 셌다
        with _range("games", 2):
            assert head(1).text().startswith(f"{_win._nick} (2경기)") and head(2).text().startswith("상대 (2경기)"), \
                (head(1).text(), head(2).text())
            assert t.item(0, 1).text() == "2" and t.item(0, 2).text() == "2"
            assert "최근 2경기" in _win.lb_compare_status.text(), _win.lb_compare_status.text()
            assert "⚠" not in head(2).text() and head(2).toolTip() == ""   # 2판으론 기록 끝에 안 닿는다
        assert head(2).text() == f"상대 ({n}경기) ⚠", head(2).text()  # 범위를 풀면 다시 그린다(전체 = 기록 끝)
        assert f"{oldest:%Y-%m-%d} 경기부터" in head(2).toolTip(), head(2).toolTip()
        # 기록이 범위 시작 때부터 있으면 셀 수 있는 것을 다 센 것 — ⚠ 없음
        _win._range_start = lambda: oldest
        _win._rerender_compare()
        assert head(2).text() == f"상대 ({n}경기)" and head(2).toolTip() == "", head(2).text()
        # 시즌을 모르면(전체 · 시즌표 없음) 판정하지 않는다 — 안 그러면 늘 ⚠(리뷰 [하]4)
        _win._range_start = lambda: None
        _win._rerender_compare()
        assert "⚠" not in head(1).text() + head(2).text(), (head(1).text(), head(2).text())
        # 받는 중 — 기록 끝과 무관하게 ⚠ · 상태 줄에 받은 수
        _win._compare_last = ("상대", opp[:1], "opp", [], "receiving")
        _win._rerender_compare()
        assert head(2).text().endswith("⚠") and "받는 중" in head(2).toolTip(), head(2).toolTip()
        assert "받는 중" in _win.lb_compare_status.text()
        # 한도 등으로 멈추면 받은 만큼은 남기고 이유를 적는다 — 바를 바꿔 다시 그려도 "받는 중"으로 안 돌아간다(리뷰 [중]1)
        _win._on_compare_failed("넥슨 API 호출 한도\n자세한 말")
        for _ in range(2):
            st = _win.lb_compare_status.text()
            assert "1경기까지만 받음(넥슨 API 호출 한도)" in st and "받는 중" not in st, st
            assert "멈춤" in head(2).toolTip() and head(2).text().endswith("⚠"), head(2).toolTip()
            _win._on_range_changed()
    finally:
        _win._fill_compare_squad, _win._compare_last = saved
        if "_range_start" in vars(_win):
            del _win._range_start


def test_compare_range_start_from_season_table():
    # _range_start 실물 — 위 테스트는 바꿔 끼우므로 여기서 잰다(리뷰 [하]5). 진행 중 = 시즌표 마지막 종료 8시 · 지난 시즌 = 그 시작 8시
    from datetime import date
    import seasons as sn_
    s1 = sn_.Season(1, "시즌 1", date(2026, 7, 30), date(2026, 9, 10))
    s2 = sn_.Season(2, "시즌 2", date(2026, 9, 10), date(2026, 10, 29))
    saved = _win._rank_seasons, _win.cb_season.currentIndex()
    _win._rank_seasons = [s1, s2]
    picked = {}
    real = _win.cb_season.currentData
    try:
        _win.cb_season.currentData = lambda: picked["v"]
        picked["v"] = None
        assert _win._range_start() is None
        picked["v"] = _win.ONGOING
        assert _win._range_start() == datetime(2026, 10, 29, config.SEASON_CUTOFF_H), _win._range_start()
        picked["v"] = s1
        assert _win._range_start() == datetime(2026, 7, 30, config.SEASON_CUTOFF_H), _win._range_start()
        _win._rank_seasons = []
        picked["v"] = _win.ONGOING
        assert _win._range_start() is None   # 시즌표가 비면 모른다 → ⚠ 판정 안 함
    finally:
        _win.cb_season.currentData = real
        _win._rank_seasons = saved[0]
        _win.cb_season.setCurrentIndex(saved[1])


def test_research_same_account_reads_only_new_matches():
    # 같은 계정을 다시 검색하면 화면이 가진 경기는 DB 에서 다시 읽지 않는다 — 결과는 전부 읽은 것과 같아야 한다
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA, store.load_details
    config.DB_PATH, config.WEB_DATA = tmp / "t.db", False
    ids = sorted(d["matchId"] for d in _DETAILS)
    full_reads = []

    def spy(*a, **k):
        full_reads.append(1)
        return saved[2](*a, **k)

    def run(api, prev):
        out = []
        ld = app_main.MatchLoader(api, "닉", 52, prev=prev)
        ld.finished_ok.connect(lambda *a: out.append(a))
        ld.failed.connect(lambda m: out.append(("실패", m)))
        ld.run()
        assert len(out) == 1 and out[0][0] != "실패", out
        return out[0]

    store.load_details = spy
    try:
        api1 = _DetailApi([], "", only=ids[:2])           # 처음엔 2경기만
        first = run(api1, None)
        assert full_reads == [1] and len(first[1]) == 2
        assert sorted(api1.forgotten) == ids[:2], api1.forgotten  # DB 에 넣은 경기의 캐시는 지운다
        prev = (_OUID, first[0], first[1])
        api2 = _DetailApi([], "")                          # 새 경기 2개가 더 생겼다
        second = run(api2, prev)
        assert full_reads == [1], "같은 계정인데 전부 다시 읽었다"
        conn = store.open_db(config.DB_PATH)
        try:
            want = saved[2](conn, _OUID, 52)               # 전부 읽었으면 나왔을 목록·순서
        finally:
            conn.close()
        assert [d["matchId"] for d in second[1]] == [d["matchId"] for d in want], (second[1], want)
        assert sorted(m.match_id for m in second[0]) == ids
        assert [m.match_id for m in second[0]] == [m.match_id for m in sorted(
            second[0], key=lambda m: m.match_date, reverse=True)], "경기 순서가 최신순이 아니다"
        assert len(first[0]) == 2 and len(first[1]) == 2, "화면이 쓰던 옛 목록을 고쳤다"
        # 화면엔 없는데 DB 에는 있는 경기(한도에 걸렸다 이어 받은 것 등) — 이번에 새로 받지 않았어도 들어와야 한다
        third = run(_DetailApi([], ""), (_OUID, second[0][:1], second[1][:1]))
        assert full_reads == [1], full_reads
        assert sorted(d["matchId"] for d in third[1]) == ids, "DB 에 있던 경기를 빠뜨렸다"
        # 다른 계정이면(ouid 가 다르면) 전부 읽는다
        run(_DetailApi([], ""), ("다른계정", first[0], first[1]))
        assert full_reads == [1, 1], full_reads
    finally:
        config.DB_PATH, config.WEB_DATA, store.load_details = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_settings_remember_window_page_and_season():
    saved_state = (_win.size(), _win._restore, _win._ouid, _win._season_picked,
                   _win.cb_season.currentIndex())
    try:
        _win.resize(1400, 800)
        _win._go_page("선수 지표", "랭커 비교")  # 기본(첫 탭)이 아닌 탭 — 같으면 복원을 빼도 결과가 같다
        _win._go_page("슛 맵")
        # 기본으로 잡히는 시즌이 아닌 것을 고른다 — 같으면 복원을 빼도 결과가 같아 안 잰다
        default = _win.cb_season.findData(_win.ONGOING)
        default = default if default >= 0 else 0
        assert _win.cb_season.count() >= 2, "시즌이 하나뿐이라 복원을 잴 수 없다"
        pick = 0 if default != 0 else 1
        _win.cb_season.setCurrentIndex(pick)
        want_season = _win._season_key(_win.cb_season.itemData(pick))
        _win._save_settings()
        # 다른 상태로 돌려놓고, 켤 때처럼 읽는다
        want_geo = bytes(_win.saveGeometry())
        _win.resize(1600, 900)
        _win._go_page("선수 지표")  # 첫 탭으로 돌려놓는다
        _win._go_page("대시보드")
        # 크기 자체는 Qt 가 화면에 맞춰 되살린다 — offscreen 화면이 800x800 이라 1280 최소폭 창은
        # 줄어든다. 여기선 저장한 값이 그대로 restoreGeometry 로 가는지만 본다
        got_geo = []
        real_restore = _win.restoreGeometry
        _win.restoreGeometry = lambda g: (got_geo.append(bytes(g)), real_restore(g))[1]
        try:
            _win._restore = _win._load_settings()
        finally:
            del _win.restoreGeometry
        assert got_geo == [want_geo], "저장한 창 위치·크기가 복원에 안 쓰였다"
        tabs = _win._restore.pop("tabs", {})
        assert tabs.get("players") == "랭커 비교" and tabs.get("trend") == "등급", tabs
        # 켤 때는 늘 대시보드 — 저장돼 있는 "슛 맵" 을 읽지 않는다(읽으면 아래 대시보드 단언이 FAIL)
        assert _win._restore == {"season": want_season}, _win._restore
        _win._restore["tabs"] = tabs
        # 처음 그리는 계정이면 그 메뉴·시즌으로
        _win._ouid, _win._season_picked = "", False
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win._current_page_name() == "대시보드", _win._current_page_name()
        assert _win._page_tabs["선수 지표"].current_name() == "랭커 비교", "보던 탭을 안 되살렸다"
        assert _win._season_key(_win.cb_season.currentData()) == want_season, _win.cb_season.currentText()
        assert _win._restore == {}, "한 번 쓴 복원값이 남아 다음 계정에도 적용된다"
        # 닫을 때 저장하는 배선 — closeEvent 가 _save_settings 를 부른다
        called = []
        _win._save_settings = lambda: called.append(1)

        class _Pf:  # 켤 때 미리 읽기 — 닫을 때 멈추게 해야 종료가 2초씩 안 붙잡힌다
            def discard(self):
                called.append("미리 읽기 멈춤")
        _win._prefetch = _Pf()
        try:
            from PySide6.QtGui import QCloseEvent
            _win.closeEvent(QCloseEvent())
        finally:
            del _win._save_settings
            _win._prefetch = None
        assert called == [1, "미리 읽기 멈춤"], called
    finally:
        _win.resize(saved_state[0])
        _win._restore = {}
        _win._season_picked = saved_state[3]
        _win.cb_season.setCurrentIndex(saved_state[4])
        _win._go_page("대시보드")
        _win._render_all()


def test_restore_old_page_names():
    """E5 — 1.x 의 메뉴 이름이 settings.ini 에 남은 사람은 새 자리(메뉴, 탭)로. 모르는 이름은 대시보드."""
    # 77 — 합친 메뉴는 답을 글자로 박는다(표에서 읽으면 표 줄을 지워도 그 경우가 같이 사라져 초록이다)
    moved_77 = [("선수별 결정력", ("선수 지표", "결정력")),
                ("팀컬러 승률", ("상대 팀컬러", None)), ("팀컬러 랭킹", ("상대 팀컬러", None)),
                ("기간별 추이", ("기간별 성적", None)), ("시즌별 성적", ("기간별 성적", None))]
    cases = [*app_main.MainWindow.OLD_PAGE_NAMES.items(), *moved_77, ("없는 메뉴", ("대시보드", None)),
             ("랭킹 추이", ("랭킹 추이", "추이"))]
    assert len(app_main.MainWindow.OLD_PAGE_NAMES) == 8
    try:
        for old, want in cases:
            _win._go_page("선수 지표", "지표")
            _win._go_page("스쿼드·이적", "타임라인")  # 기억된 탭이 답과 달라야 복원을 잰다
            _win._go_page("슛 맵")
            _win._ouid, _win._restore = "", {"page": old}
            _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                            {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
            assert _win._current_view() == want, (old, _win._current_view())
        # 67 R1 — 지운 탭 이름(가성비)을 기억한 사람은 같은 메뉴의 새 탭(팀컬러)으로(OLD_TAB_NAMES)
        _win._go_page("메타 분석", "구단가치")
        _win._ouid = ""
        _win._restore = {"page": "메타 분석", "tabs": {_win._tab_sid["메타 분석"]: "가성비"}}
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win._current_view() == ("메타 분석", "팀컬러"), _win._current_view()
        # 77 C — [승률 그래프 › 승률·등급] 을 기억한 사람은 [등급] 으로(첫 탭이라 우연히 맞지 않게 다른 탭을 보던 채로)
        _win._go_page("승률 그래프", "일별 기록")
        _win._ouid = ""
        _win._restore = {"page": "승률 그래프", "tabs": {_win._tab_sid["승률 그래프"]: "승률·등급"}}
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win._current_view() == ("승률 그래프", "등급"), _win._current_view()
    finally:
        _win._restore = {}
        _win._page_tabs["승률 그래프"].set_current("등급", emit=False)
        _win._page_tabs["선수 지표"].set_current("지표", emit=False)
        _win._page_tabs["스쿼드·이적"].set_current("타임라인", emit=False)
        _win._go_page("대시보드")
        _win._render_all()


def test_same_account_search_opens_dashboard_but_auto_check_keeps_menu():
    """사용자가 연 검색은 같은 계정이어도 대시보드부터. 켤 때 자동 새 경기 확인(quiet)만 보던 메뉴를 둔다."""
    args = (_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
            {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
    saved_quiet = _win._quiet_search
    try:
        for quiet, want in ((False, "대시보드"), (True, "슛 맵")):
            _win._ouid, _win._restore = _OUID, {}   # 같은 계정 — switched 가 거짓이다
            _win._go_page("슛 맵")
            _win._quiet_search = quiet
            _win._on_loaded(*args)
            assert _win._current_page_name() == want, (quiet, _win._current_page_name())
    finally:
        _win._quiet_search = saved_quiet
        _win._restore = {}
        _win._go_page("대시보드")
        _win._render_all()


def test_removed_page_name_restores_to_dashboard():
    # 1.2.1 에서 "선수 조합" 메뉴를 지웠다 — 그걸 보고 닫은 사람의 settings.ini 에 이름이 남아 있다.
    # 대시보드가 아닌 메뉴에서 시작한다: 대시보드에서 시작하면 이름 검사를 빼도 _go_page 가 묶음 제목 행을
    # 골라 페이지가 그대로라 통과해 버린다.
    try:
        _win._go_page("슛 맵")
        assert _win._current_page_name() == "슛 맵"
        _win._ouid, _win._restore = "", {"page": "선수 조합"}
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert "선수 조합" not in _win._page_index
        assert _win.pages.currentIndex() == _win._page_index["대시보드"], _win._current_page_name()
        row = _win.nav.currentItem()
        assert row is not None and row.data(Qt.ItemDataRole.UserRole) == _win._page_index["대시보드"]
    finally:
        _win._restore = {}
        _win._go_page("대시보드")
        _win._render_all()


def _loader_db(n_saved: int):
    """임시 DB 에 픽스처 n_saved 경기를 미리 저장 — (폴더, 되돌릴 설정)."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA
    config.DB_PATH, config.WEB_DATA = tmp / "t.db", False
    conn = store.open_db(config.DB_PATH)
    try:
        store.save_matches(conn, sorted(_DETAILS, key=lambda d: d["matchId"])[:n_saved])
    finally:
        conn.close()
    return tmp, saved


def _run_one(ld):
    out = []
    ld.finished_ok.connect(lambda *a: out.append(a))
    ld.failed.connect(lambda m: out.append(("실패", m)))
    ld.run()
    assert len(out) == 1 and out[0][0] != "실패", out
    return out[0]


def test_loader_reads_db_while_waiting_for_nexon():
    # 저장된 경기 읽기(1만 경기 약 2초)는 넥슨 응답을 기다릴 이유가 없다 — 차례로 돌 때 검색 5.3초였다.
    # 넥슨 경기 목록 응답이 'DB 읽기가 시작됐다'를 기다린다: 차례로 돌면 5초 기다리다 False 를 적는다.
    import threading
    tmp, saved = _loader_db(2)
    orig = app_main.load_saved
    started, seen = threading.Event(), []

    def spy(*a, **k):
        started.set()
        return orig(*a, **k)

    class _Api(_DetailApi):
        def get_match_ids(self, ouid, matchtype, offset, limit):
            if offset == 0:
                seen.append(started.wait(5))
            return super().get_match_ids(ouid, matchtype, offset, limit)

    app_main.load_saved = spy
    try:
        res = _run_one(app_main.MatchLoader(_Api([], ""), "닉", 52))
    finally:
        app_main.load_saved = orig
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
    assert seen == [True], "DB 읽기가 넥슨 조회가 끝나기를 기다렸다"
    assert sorted(d["matchId"] for d in res[1]) == sorted(d["matchId"] for d in _DETAILS), "합친 결과가 다르다"
    assert sorted(m.match_id for m in res[0]) == sorted(d["matchId"] for d in _DETAILS)


class _ManyApi(_DetailApi):
    """픽스처를 복제한 새 경기 n 개 — 상세 하나에 delay 초. throttle_first 면 첫 요청이 429 를 받은 셈 친다.
    peak_after: 첫 요청이 끝난 '뒤' 시작한 요청들 사이의 최대 동시 수 — 줄이기는 첫 요청이 끝날 때 걸린다."""

    def __init__(self, n, delay=0.03, throttle_first=False):
        super().__init__([], "")
        base = _DETAILS[0]
        self.by_id = {}
        for i in range(n):
            d = dict(base)
            d["matchId"] = f"many{i:04d}"
            self.by_id[d["matchId"]] = d
        self.delay, self.throttle_first = delay, throttle_first
        self.throttled = 0
        self._lock = threading.Lock()
        self.calls, self.active, self.first_done = 0, 0, False
        self.peak_all, self.peak_after, self.n_after = 0, 0, 0

    def get_match_detail(self, mid):
        with self._lock:
            self.calls += 1
            first = self.calls == 1
            if first and self.throttle_first:
                self.throttled += 1
            self.active += 1
            self.peak_all = max(self.peak_all, self.active)
            after = self.first_done
            if after:
                self.n_after += 1
                self.peak_after = max(self.peak_after, self.active)
        try:
            time.sleep(self.delay)
            return self.by_id[mid]
        finally:
            with self._lock:
                self.active -= 1
                if first:
                    self.first_done = True


def test_loader_fetches_details_concurrently_and_backs_off_on_429():
    # 동시 6개 → 24개: 처음 보는 계정 3천 경기 113초 → 약 32초(서비스 키 실측). 429 를 받으면 그 검색은 2개로.
    tmp, saved = _loader_db(0)
    try:
        fast = _ManyApi(60)
        _run_one(app_main.MatchLoader(fast, "닉", 52))
        assert fast.peak_all > 6, f"동시 요청이 예전 6개를 넘지 않는다: {fast.peak_all}"
        assert fast.peak_all <= app_main.DETAIL_WORKERS, fast.peak_all
        slow = _ManyApi(200, throttle_first=True)
        shutil.rmtree(tmp, ignore_errors=True)
        config.DB_PATH, config.WEB_DATA = saved
        tmp, saved = _loader_db(0)
        _run_one(app_main.MatchLoader(slow, "닉", 52))
        assert slow.n_after > 20, f"전제: 첫 요청 뒤에 시작한 요청이 있어야 잰다({slow.n_after})"
        assert slow.peak_after <= app_main.DETAIL_WORKERS_THROTTLED, f"429 뒤에도 {slow.peak_after}개씩 보냈다"
        # 지난 검색의 429 로 이번 검색까지 내리지 않는다 — API 객체는 앱이 켜져 있는 동안 계속 센다
        shutil.rmtree(tmp, ignore_errors=True)
        config.DB_PATH, config.WEB_DATA = saved
        tmp, saved = _loader_db(0)
        old = _ManyApi(200)
        old.throttled = 3
        _run_one(app_main.MatchLoader(old, "닉", 52))
        assert old.n_after > 20, f"전제: 첫 요청 뒤에 시작한 요청이 있어야 잰다({old.n_after})"
        assert old.peak_after > app_main.DETAIL_WORKERS_THROTTLED, \
            f"지난 검색의 429 로 이번 검색을 내렸다({old.peak_after})"
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
    # 연결 풀이 동시 요청보다 커야 한다 — requests 기본 10 이면 남는 연결을 버리고 다시 맺는다
    api = nexon_api.FCOnlineAPI("k")
    assert api._session.get_adapter("https://open.api.nexon.com")._pool_maxsize >= app_main.DETAIL_WORKERS


def test_loader_uses_prefetch_only_for_that_account():
    # 켤 때 미리 읽은 계정을 검색하면 DB 를 다시 읽지 않는다. 다른 계정이면 미리 읽기를 멈추고 직접 읽는다.
    tmp, saved = _loader_db(4)
    orig = app_main.load_saved
    calls = []

    def spy(ouid, *a, **k):
        calls.append(ouid)
        return orig(ouid, *a, **k)

    app_main.load_saved = spy
    try:
        pf = app_main.SavedPrefetch(_OUID, 52)
        pf._future.result()
        assert calls == [_OUID]
        res = _run_one(app_main.MatchLoader(_DetailApi([], ""), "닉", 52, prefetch=pf))
        assert calls == [_OUID], f"미리 읽은 계정인데 DB 를 다시 읽었다: {calls}"
        assert len(res[1]) == len(_DETAILS) and len(res[0]) == len(_DETAILS)
        other = app_main.SavedPrefetch("다른계정", 52)
        other._future.result()
        _run_one(app_main.MatchLoader(_DetailApi([], ""), "닉", 52, prefetch=other))
        assert other._stop, "다른 계정인데 미리 읽기를 버리지 않았다"
        assert calls == [_OUID, "다른계정", _OUID], calls
        assert other.take(_OUID, 52) is None and other.take("다른계정", 52) is None, "버린 것을 다시 내줬다"
    finally:
        app_main.load_saved = orig
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


_MAXDIV_ROWS = [{"matchType": 50, "division": 900, "achievementDate": "2025-01-01T00:00:00"},
                {"matchType": 52, "division": 800, "achievementDate": "2026-07-05T09:37:53"}]   # 원문 UTC(63) = KST 18:37


class _MaxDivApi(_DetailApi):
    """maxdivision 은 바로 답하고, 로더가 finished_ok 전에 부르는 division 메타는 그 답이 끝난 뒤에야 준다 —
    그래서 finished_ok 때는 늘 '이미 끝남'이라 보내는 순서가 결정적이다(앞으로 옮기는 변이가 매번 잡힌다)."""

    def __init__(self, rows=_MAXDIV_ROWS, fail=False, asked=True):
        import threading
        super().__init__([], "")
        self.rows, self.fail, self.calls = rows, fail, 0
        self.done = threading.Event()
        if not asked:
            # 안 묻는 경우(calls == 0 을 재는 쪽) — 안 열어 두면 division 메타가 5초를 꽉 채워 기다린다(85단계 실측 5.1초)
            self.done.set()

    def get_max_division(self, ouid):
        self.calls += 1
        try:
            if self.fail:
                raise nexon_api.NexonAPIError("x", code="OPENAPI00007", status=429)
            return self.rows
        finally:
            self.done.set()

    def get_meta(self, name):
        if name == "division":
            self.done.wait(5)
            return [{"divisionId": 800, "divisionName": "챔피언스"}, {"divisionId": 900, "divisionName": "슈퍼챔피언스"}]
        return []


def _best_line():
    lb = _win.card_ranker._head_best
    return lb.text() if not lb.isHidden() else None


def _restore_account():
    _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                    {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})


def test_max_division_survives_account_switch():
    # 진짜 로더 → 창. 다른 계정을 보다가 검색하면 _on_loaded 전까지 _ouid 가 옛 계정이다 — 계획 검토 B [상] 은 그 사이
    # 온 신호가 버려지는 것이었는데, 화면이 ouid 별로 담게 해 순서와 무관해졌다(보내는 자리를 앞으로 옮겨도 이 테스트는 초록).
    # 여기서 재는 건 끝에서 끝까지 — 로더가 부르고, 52 줄을 고르고, 계정을 바꾼 창에 실제로 뜨는지.
    tmp, saved = _loader_db(4)
    keep_names, keep_max = dict(_win._division_names), dict(_win._max_division)
    try:
        _win._ouid, _win._max_division = "옛계정", {}
        api = _MaxDivApi()
        ld = app_main.MatchLoader(api, "닉", 52, want_max_division=True)
        ld.finished_ok.connect(_win._on_loaded)
        ld.max_division_ready.connect(_win._on_max_division)
        ld.run()
        _app.processEvents()
        assert api.calls == 1 and _win._ouid == _OUID
        assert _best_line() == "최고티어 최근 달성일 2026-07-05  [ 챔피언스 ]", _best_line()  # 50(공식경기) 줄이 아니라 52
        # 구단주 비교 로더(기본값)는 부르지 않는다
        other = _MaxDivApi(asked=False)
        _run_one(app_main.MatchLoader(other, "닉", 52))
        assert other.calls == 0
        # 실패해도 검색은 정상 완료 · 같은 계정이면 보던 줄 유지
        bad = _MaxDivApi(fail=True)
        ld = app_main.MatchLoader(bad, "닉", 52, want_max_division=True)
        ld.max_division_ready.connect(_win._on_max_division)
        _run_one(ld)
        _app.processEvents()
        assert bad.calls == 1 and _best_line() == "최고티어 최근 달성일 2026-07-05  [ 챔피언스 ]", _best_line()
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
        _win._division_names, _win._max_division = keep_names, keep_max
        _restore_account()


def test_methods_find_bar_96():
    """96 T1 — 산출 방법 안 검색: 칠한 곳 수 · 이전/다음 돌기 · 비우면 아무것도 · 글을 다시 그린 뒤에도 남는다."""
    fb, tb = _win.methods_find, _win.tb_methods
    plain = tb.toPlainText()
    word = "xG"
    want = plain.lower().count(word.lower())
    assert want >= 3, f"전제 — 산출 방법에 '{word}' 가 셋 이상({want})"
    try:
        fb.edit.setText(word)
        assert fb.hits() == want and len(tb.extraSelections()) == want, (fb.hits(), want)
        assert fb.count.text() == f"1/{want}" and tb.textCursor().selectedText().lower() == word.lower()
        fb.prev()
        assert fb.current() == want, "처음에서 [이전] 은 마지막으로"
        fb.next()
        assert fb.current() == 1, "마지막에서 [다음] 은 처음으로"
        fb.next()
        at = tb.textCursor().selectionStart()
        assert fb.current() == 2
        _win._methods_html = ""   # 등급 이름이 늦게 온 것처럼 — setHtml 로 다시 그린다
        _win._refresh_methods_page()
        assert fb.hits() == want and fb.current() == 2 and tb.textCursor().selectionStart() == at, "다시 그린 뒤 검색이 사라졌다"
        fb.edit.setText("아무데도없는말xyz")
        assert fb.hits() == 0 and fb.count.text() == "0/0" and not tb.extraSelections()
        fb.next()   # 찾은 곳이 없으면 아무 일 없음
        fb.edit.setText("")
        assert fb.count.text() == "" and not tb.extraSelections() and not tb.textCursor().hasSelection()
    finally:
        fb.edit.setText("")


def test_glossary_tips_96():
    """96 T2 — 용어 머리글 말풍선은 어느 표든 glossary 한 곳에서 · 산출 방법 [용어] 절에 정의가 다 있다."""
    import glossary
    tbl = _win._make_table(["선수", "xG", "백분위", "구단가치"])
    for c, key in ((1, "xg"), (2, "percentile"), (3, "club_value")):
        assert tbl.horizontalHeaderItem(c).toolTip() == glossary.tip(key), c
    assert tbl.horizontalHeaderItem(0).toolTip() == "", "용어 아닌 머리글에 말풍선을 달았다"
    c = _win.PLAYER_COLUMNS.index("실효 OVR")
    assert _win.tbl_players.horizontalHeaderItem(c).toolTip().startswith(glossary.tip("effective_ovr"))
    assert _win.gb_elo.toolTip() == glossary.tip("elo")
    text = _win.tb_methods.toPlainText()
    for title, definition in glossary.TERMS.values():
        assert f"{title}: {definition}" in text, title


def test_basis_text_96():
    """96 W1 — 출처마다 머리말이 다르고, 시각을 모르면 아무것도 안 쓴다 · 화면 둘이 그 꼴을 쓴다."""
    from datetime import date as _d, datetime as _dt
    assert widgets.basis_text(widgets.BASIS_NEXON, "2026-11-10T14:00:59") == "넥슨 데이터 기준 11-10 14:00"
    assert widgets.basis_text(widgets.BASIS_FETCHED, "2026-11-10 14:00") == "받은 시각 11-10 14:00"
    assert widgets.basis_text(widgets.BASIS_FETCHED, "2026-11-10") == "받은 시각 11-10"
    assert widgets.basis_text(widgets.BASIS_FETCHED, _dt(2026, 1, 2, 3, 4)) == "받은 시각 01-02 03:04"
    assert widgets.stamp(_d(2026, 1, 2)) == "01-02"
    assert widgets.basis_text(widgets.BASIS_NEXON, None) == "" and widgets.basis_text(widgets.BASIS_NEXON, "") == ""
    st = {"snapshots": 2, "last_rows": 10000, "last_taken_at": "2026-11-10T09:05:00", "meta_ref": "2026-11-10T09:00:00",
          "meta_gaps": 0}
    text = app_main.rank_status_text(st)
    first = text.split("<br>")[0]
    assert first.endswith("받은 시각 11-10 09:05"), first
    assert "빈 순위 0 · 넥슨 데이터 기준 11-10 09:00" in text, text


def test_methods_page_gets_division_names_late():
    # 첫 실행(메타 캐시 없음)엔 산출 방법 글이 등급 번호로 만들어진다 — 조회로 이름이 오면 글도 이름으로 바뀌어야
    champ = str(models.CHAMPION_DIVISION_ID)
    keep_names = dict(_win._division_names)
    try:
        _win._division_names = {}
        _win._refresh_methods_page()
        assert f"양쪽 다 {champ} 이상" in _win.tb_methods.toPlainText(), "이름 없이 만든 글이 번호가 아니다(전제)"
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {models.CHAMPION_DIVISION_ID: "늦은챔스"})
        text = _win.tb_methods.toPlainText()
        assert "양쪽 다 늦은챔스 이상" in text and f"양쪽 다 {champ} 이상" not in text, "이름이 와도 글이 번호 그대로"
    finally:
        _win._division_names = keep_names
        _win._refresh_methods_page()
        _restore_account()


def test_max_division_card_rules():
    keep_names, keep_max = dict(_win._division_names), dict(_win._max_division)
    try:
        _restore_account()
        _win._division_names = {800: "챔피언스"}
        _win._max_division = {}
        val = {"division": 800, "date": "2026-07-05"}
        _win._on_max_division(_OUID, val)
        assert _best_line() == "최고티어 최근 달성일 2026-07-05  [ 챔피언스 ]"
        _win._on_max_division("다른계정", {"division": 800, "date": "2020-01-01"})
        assert _best_line() == "최고티어 최근 달성일 2026-07-05  [ 챔피언스 ]", "다른 계정 값이 들어갔다"
        _win._on_max_division(_OUID, None)
        assert _best_line() == "최고티어 최근 달성일 2026-07-05  [ 챔피언스 ]", "실패(None)가 보던 값을 지웠다"
        _win._on_max_division(_OUID, {})
        assert _best_line() is None, "감독모드 기록이 없는데 줄이 남았다"
        _win._on_max_division(_OUID, {"division": 999, "date": "2026-07-05"})
        assert _best_line() is None, "등급 이름을 모르는데 숫자를 냈다"
        # 두 모드 모두 같은 자리에 보인다(set_mode 가 숨기지 않는다)
        _win._on_max_division(_OUID, val)
        for champ in (True, False):
            _win._is_champion = champ
            _win._render_ranker()
            assert _best_line() == "최고티어 최근 달성일 2026-07-05  [ 챔피언스 ]", champ
            # 78 ① 리뷰 — 넥슨 순위를 못 쓰는 챔피언스만 출처가 바뀐 이유 한 줄(챔피언스 미만은 숨김)
            note = _win.card_ranker.note
            ranked = bool(_win._rank and getattr(_win._rank, "ranked", False))
            assert note.isHidden() == (not champ or ranked), (champ, ranked, note.text())
        _win._is_champion = False
        # 계정을 바꾸면 옛 계정 줄이 안 남는다 — 새 계정 요청이 실패해도
        _win._on_loaded(_MATCHES, _DETAILS, "B계정", {"nickname": "B", "level": 1},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        _win._on_max_division("B계정", None)
        assert _best_line() is None, "B 계정 화면에 A 계정 최고 등급이 남았다"
        # 랭킹·최고 등급 신호는 어느 순서로 와도 마지막 카드가 같다
        _restore_account()
        shots = []
        for order in ((0, 1), (1, 0)):
            _win._max_division, _win._rank = {}, None
            calls = [lambda: _win._on_rank_ready(_OUID, None), lambda: _win._on_max_division(_OUID, val)]
            for i in order:
                calls[i]()
            shots.append((_best_line(), _win.card_ranker._vals["전적"].text()))
        assert shots[0] == shots[1], shots
    finally:
        _win._division_names, _win._max_division = keep_names, keep_max
        _restore_account()


def test_offline_open_does_not_ask_max_division():
    # 켤 때 저장본 열기·내려놓은 뒤 다시 열기(둘 다 offline_ouid)는 넥슨에 묻지 않는다 — 뒤따르는 검색이 채운다
    tmp, saved = _loader_db(4)
    try:
        api = _MaxDivApi(asked=False)
        _run_one(app_main.MatchLoader(api, "닉", 52, offline_ouid=_OUID, want_max_division=True))
        assert api.calls == 0
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_max_division_request_does_not_retry():
    # 429·5xx 에 최대 4.5초 잠들며 재시도하던 _get 을 maxdivision 만 한 번으로 — 부가 정보라 기다릴 이유가 없다
    class _Res:
        status_code = 429

        def json(self):
            return {"error": {"name": "OPENAPI00007", "message": "x"}}

    class _Sess:
        headers: dict = {}
        calls = 0

        def get(self, *a, **k):
            _Sess.calls += 1
            return _Res()

    api = nexon_api.FCOnlineAPI("k")
    api._session = _Sess()
    keep = nexon_api.time.sleep
    nexon_api.time.sleep = lambda s: (_ for _ in ()).throw(AssertionError(f"{s}초 잠들었다"))
    try:
        try:
            api.get_max_division("o")
            raise AssertionError("429 인데 예외가 없다")
        except nexon_api.NexonAPIError:
            pass
        assert _Sess.calls == 1, _Sess.calls
        _Sess.calls = 0
        nexon_api.time.sleep = lambda s: None
        try:
            api.get_user_basic("o")   # 다른 요청은 그대로 세 번 — attempts 기본값이 바뀌지 않았다
        except nexon_api.NexonAPIError:
            pass
        assert _Sess.calls == 3, _Sess.calls
    finally:
        nexon_api.time.sleep = keep


def test_loader_does_not_wait_for_rank():
    # 랭킹(데이터센터 1.1초)은 랭커 카드에만 쓴다 — 아직이면 먼저 그리고 rank_ready 로 뒤따른다.
    # 기다리면 스레드가 살아 있어 그동안 새 검색도 막힌다(_api_search 의 isRunning).
    import threading
    tmp, saved = _loader_db(4)
    gate, ready = threading.Event(), []
    orig = app_main.MatchLoader._safe_rank
    app_main.MatchLoader._safe_rank = lambda self: (gate.wait(5), "랭킹")[1]
    try:
        ld = app_main.MatchLoader(_DetailApi([], ""), "닉", 52)
        ld.rank_ready.connect(lambda o, r: ready.append((o, r)))
        res = _run_one(ld)
        assert res[8] is None and ready == [], "랭킹을 기다린 뒤에야 화면으로 넘겼다"
        gate.set()
        for _ in range(100):  # 다른 스레드에서 온 신호는 UI 스레드 대기열로 간다 — 이벤트를 돌려야 받는다
            _app.processEvents()
            if ready:
                break
            time.sleep(0.05)
        assert ready == [(_OUID, "랭킹")], ready
        # 이미 끝났으면 첫 신호에 실려 가고 뒤따르는 신호는 없다
        gate.set()
        ld2 = app_main.MatchLoader(_DetailApi([], ""), "닉", 52)
        ld2.rank_ready.connect(lambda o, r: ready.append(("두번째", r)))
        app_main.MatchLoader._safe_rank = lambda self: "랭킹2"
        res2 = _run_one(ld2)
        time.sleep(0.1)
        _app.processEvents()
        assert res2[8] == "랭킹2" and ready == [(_OUID, "랭킹")], (res2[8], ready)
    finally:
        app_main.MatchLoader._safe_rank = orig
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
    # 받는 쪽 — 지금 계정이면 카드만 다시, 그사이 다른 계정을 열었으면 버린다. 같은 계정 재검색에서 None 이면 보던 값을 둔다
    keep = _win._rank
    try:
        drawn = []
        _win._render_ranker = lambda: drawn.append(_win._rank)
        _win._on_rank_ready("다른계정", "옛 계정 랭킹")
        assert _win._rank is keep and drawn == [], "다른 계정의 늦은 랭킹이 카드에 들어갔다"
        _win._on_rank_ready(_OUID, "새 랭킹")
        assert drawn == ["새 랭킹"], drawn
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win._rank == "새 랭킹", "같은 계정 재검색에서 아직 안 온 랭킹(None)이 보던 카드를 지웠다"
    finally:
        _win.__dict__.pop("_render_ranker", None)
        _win._rank = keep
        _win._render_all()


def test_offline_loader_reads_db_without_asking_nexon():
    class _MetaOnly:
        def get_meta(self, name):
            return []

        def __getattr__(self, n):
            raise AssertionError(f"켤 때 DB 만 읽어야 하는데 넥슨을 불렀다: {n}")

    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA
    config.DB_PATH, config.WEB_DATA = tmp / "t.db", False
    try:
        conn = store.open_db(config.DB_PATH)
        try:
            store.save_matches(conn, _DETAILS)
        finally:
            conn.close()
        out = []
        ld = app_main.MatchLoader(_MetaOnly(), "테스트구단주", 52, offline_ouid=_OUID)
        ld.finished_ok.connect(lambda *a: out.append(a))
        ld.failed.connect(lambda m: out.append(("실패", m)))
        ld.run()
        assert len(out) == 1 and out[0][0] != "실패", out
        assert len(out[0][1]) == len(_DETAILS) and out[0][2] == _OUID and out[0][8] is None, out[0][2:9]
        # DB 에 그 계정 경기가 없으면 아무것도 안 낸다(검색 화면 그대로)
        out.clear()
        ld = app_main.MatchLoader(_MetaOnly(), "없음", 52, offline_ouid="없는계정")
        ld.finished_ok.connect(lambda *a: out.append(a))
        ld.run()
        assert out == [], out
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_open_last_account_then_quiet_refresh():
    made, searched = [], []

    class _Rec:
        def __init__(self, api, nick, mt, prev=None, offline_ouid=None):
            made.append((nick, offline_ouid))
            self.progress = self.finished_ok = self.failed = self.finished = self

        def connect(self, *_):
            pass

        def start(self):
            pass

    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = (config.DB_PATH, app_main.MatchLoader, _win._loader, _win._api_search, _win._ouid)
    config.DB_PATH = tmp / "t.db"
    app_main.MatchLoader, _win._loader = _Rec, None
    _win._api_search = lambda nick, quiet=False: searched.append((nick, quiet))
    try:
        assert _win.open_last_account() is False and made == [], "열 계정이 없는데 열었다"
        conn = store.open_db(config.DB_PATH)
        try:
            store.upsert_account(conn, _OUID, "테스트구단주")
        finally:
            conn.close()
        assert _win.open_last_account() is False and made == [], "기록이 없는 계정을 열었다"
        conn = store.open_db(config.DB_PATH)
        try:
            store.save_matches(conn, _DETAILS)
        finally:
            conn.close()
        assert _win.open_last_account() is True and made == [("테스트구단주", _OUID)], made
        _win._after_offline_open("다른계정", "테스트구단주")   # DB 로 못 그렸으면 넥슨에 묻지 않는다
        assert searched == [], searched
        _win._ouid = _OUID
        _win._after_offline_open(_OUID, "테스트구단주")
        assert searched == [("테스트구단주", True)], searched   # 그렸으면 조용히 새 경기 확인
    finally:
        (config.DB_PATH, app_main.MatchLoader, _win._loader, _win._api_search, _win._ouid) = saved
        _win._set_busy(False)
        shutil.rmtree(tmp, ignore_errors=True)


def test_quiet_refresh_failure_stays_in_status_bar():
    # 켤 때 자동 확인이 실패해도(오프라인·한도) 경고 창·키 창을 띄우지 않는다 — 모달이면 ModalCalled 로 빨개진다
    saved = _win._quiet_search, _win.stack.currentIndex(), _win._ask_new_key
    asked = []
    _win._ask_new_key = asked.append
    try:
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        _win._quiet_search = True
        _win._on_failed("네트워크 오류\n자세한 내용")
        assert "저장된 기록" in _win.statusBar().currentMessage(), _win.statusBar().currentMessage()
        _win._on_quota_hit("한도")
        assert asked == [], "자동 확인인데 키 창을 띄웠다"
        _win._quiet_search = False   # 직접 검색이면 지금처럼 키를 바꾸라고 묻는다
        with _Patch((app_main.QMessageBox, "warning", lambda *a, **k: None)):
            _win._on_quota_hit("한도")
        assert asked == ["한도"], asked
    finally:
        _win._quiet_search, _, _win._ask_new_key = saved[0], None, saved[2]
        _win.stack.setCurrentIndex(saved[1])


def test_loader_signals_pass_objects_without_copying():
    # list/dict 로 선언한 신호는 PyQt 가 중첩 dict 를 통째로 깊은 복사한다 — 1만 경기에서 메모리
    # 4.5GB·화면까지 6초였다(2026-10-02). 같은 객체가 그대로 와야 한다(복사면 is 가 깨진다)
    from PySide6.QtCore import Qt as _Qt
    details, matches, names = [{"a": {"b": [1]}}], [object()], {1: "x"}
    got = []
    ld = app_main.MatchLoader(_NoApi(), "x", 52)
    ld.finished_ok.connect(lambda *a: got.append(a), _Qt.ConnectionType.DirectConnection)
    ld.finished_ok.emit(matches, details, "o", {}, names, {}, 0, 0, None, "", False, "", {}, {})
    assert got and got[0][0] is matches and got[0][1] is details and got[0][4] is names, got
    batch = {"닉": ("팀", 1)}
    for loader in (app_main.TeamColorLoader(["닉"]), app_main.RankListLoader({"닉"})):
        seen = []
        loader.loaded_many.connect(seen.append, _Qt.ConnectionType.DirectConnection)
        loader.loaded_many.emit(batch)
        assert seen and seen[0] is batch, type(loader).__name__


def test_loader_signals_cross_threads_without_copying():
    # 73 R3 — 위 테스트는 같은 스레드 직접 연결만 잰다. 실제 로더는 작업 스레드에서 내보내 화면 스레드가
    # 큐로 받는다(PyQt 8 의 4.5GB 는 그 길에서 났다). 스모크는 QThread.start 를 막으니 파이썬 스레드에서 내보낸다 —
    # 보내는 쪽 객체가 화면 스레드에 살아 Qt 가 큐 연결로 넘긴다(받을 때 화면 스레드인지까지 단언)
    import threading
    details, matches, names = [{"a": {"b": [1]}}], [object()], {1: "x"}
    got = []
    ld = app_main.MatchLoader(_NoApi(), "x", 52)
    main_thread = threading.get_ident()
    ld.finished_ok.connect(lambda *a: got.append((threading.get_ident(), a)))
    th = threading.Thread(target=lambda: ld.finished_ok.emit(
        matches, details, "o", {}, names, {}, 0, 0, None, "", False, "", {}, {}))
    th.start(); th.join()
    assert not got, "같은 스레드에서 바로 불렸다 — 큐 연결을 재지 못한다"
    for _ in range(50):
        if got:
            break
        QApplication.processEvents()
    assert got, "큐로 넘긴 신호가 안 왔다"
    tid, a = got[0]
    assert tid == main_thread, "화면 스레드에서 안 받았다"
    assert a[0] is matches and a[1] is details and a[4] is names and a[4] == {1: "x"}, a


def test_spinbox_text_field_not_collapsed():
    # 73 ② — Qt 6.11.3·6.12.0 은 QSS 를 건 스핀박스의 입력칸을 줄인다(버튼 없음이면 폭 1px — 위쪽 바 경기 범위
    # "1 ~ 4" 가 빈 칸으로 보였다 · 6.11.0·6.11.2 정상 · 2026-11-01 실측). Qt 를 올리면 여기서 먼저 빨개진다
    from PySide6.QtWidgets import QSpinBox
    seen = {}
    # 메뉴마다 첫 탭으로 가므로 뒤 테스트(촬영 포함)가 보는 탭이 바뀌지 않게 되돌린다
    keep_page = _win._current_page_name()
    keep_tabs = {n: t.current_index() for n, t in _win._page_tabs.items()}
    try:
        for name in list(_win._page_index):
            _win._go_page(name)
            QApplication.processEvents()
            for s in _win.findChildren(QSpinBox):
                if s.isVisible() and id(s) not in seen:
                    seen[id(s)] = (s.objectName() or type(s).__name__, s.width(), s.lineEdit().geometry())
    finally:
        for n, i in keep_tabs.items():
            _win._page_tabs[n].set_current(i, emit=False)
        if keep_page:
            _win._go_page(keep_page, _win._page_tabs[keep_page].current_name()
                          if keep_page in _win._page_tabs else None)
    # 위쪽 바 범위 칸은 76 에, 승률 그래프 일수 칸은 77 C 에 없어졌다 — 이제 [상대 팀컬러] 최소 칸이 대표
    assert any(n == "QSpinBox" for n, _w, _g in seen.values()) and seen, seen
    bad = [(n, w, (g.x(), g.width())) for n, w, g in seen.values() if g.width() < w // 2 or g.x() < 4]
    assert not bad, f"스핀박스 입력칸이 찌그러졌다(이름, 상자 폭, (입력칸 x, 폭)) {bad}"


def test_eta_text_only_from_real_elapsed():
    assert app_main.eta_text(app_main.ETA_MIN_DONE - 1, 100, 10.0) == "", "표본이 적은데 시간을 냈다"
    assert app_main.eta_text(100, 100, 10.0) == "", "끝났는데 남은 시간을 냈다"
    assert app_main.eta_text(50, 100, 0.0) == ""
    assert app_main.eta_text(50, 100, 25.0) == " · 남음 약 25초"      # 50건에 25초 → 남은 50건도 25초
    assert app_main.eta_text(30, 2100, 60.0) == " · 남음 약 69분"     # 2초/건 × 2,070건 = 4,140초
    assert app_main.eta_text(99, 100, 9.9) == " · 남음 약 1초"        # 1초 미만도 0초라 하지 않는다


def test_loader_progress_carries_eta():
    # 배선 — 새 경기 받는 진행 문구에 남은 시간이 실린다(픽스처는 4경기라 기준을 1로 낮춰 잰다)
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA, app_main.ETA_MIN_DONE
    config.DB_PATH, config.WEB_DATA, app_main.ETA_MIN_DONE = tmp / "t.db", False, 1
    msgs = []
    try:
        ld = app_main.MatchLoader(_DetailApi([], ""), "닉", 52)
        ld.progress.connect(lambda d, t, m: msgs.append(m))
        ld.run()
    finally:
        config.DB_PATH, config.WEB_DATA, app_main.ETA_MIN_DONE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    got = [m for m in msgs if m.startswith("새 경기 받는 중")]
    assert got and any("남음 약" in m for m in got), got


def test_teamcolor_progress_reaches_status_bar_unless_search_runs():
    saved = _win._loader, _win._teamcolor_progress_fmt, [lb.text() for lb in _win._teamcolor_status_labels]

    class _Busy:
        def isRunning(self):
            return True

    try:
        _win._teamcolor_progress_fmt = "랭킹 목록 {done} / {total}쪽 읽는 중…"
        _win._loader = None
        _win._on_teamcolor_progress(120, 500)
        assert _win.statusBar().currentMessage() == "팀컬러: 랭킹 목록 120 / 500쪽 읽는 중…", \
            _win.statusBar().currentMessage()
        _win.statusBar().showMessage("새 경기 받는 중… 3/10")
        _win._loader = _Busy()                     # 검색이 돌 땐 그쪽이 상태줄 주인
        _win._on_teamcolor_progress(121, 500)
        assert _win.statusBar().currentMessage() == "새 경기 받는 중… 3/10"
    finally:
        _win._loader, _win._teamcolor_progress_fmt = saved[0], saved[1]
        for lb, t in zip(_win._teamcolor_status_labels, saved[2]):
            lb.setText(t)


def test_player_card_has_my_record_tab():
    import charts
    shooter = next(p for p in st_mod.finishing_ranking(_win._details, _win._ouid) if p.shots)
    orig_exec = app_main.QDialog.exec
    app_main.QDialog.exec = lambda self: 0     # 다이얼로그 안을 보려고 — 띄우지는 않는다
    try:
        _win._show_player_info(shooter.sp_id)
    finally:
        app_main.QDialog.exec = orig_exec
    tabs = _win._last_player_tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["카드 정보", "내 기록", "랭커 기록"]
    rec = tabs.widget(1)
    summary = [lb for lb in rec.findChildren(app_main.QLabel) if lb.objectName() == "myRecordSummary"][0]
    assert f"슛 {shooter.shots}" in summary.text() and f"골 {shooter.goals}" in summary.text(), summary.text()
    maps = rec.findChildren(app_main.ShotMapWidget)
    want = st_mod.shot_map(_win._details, _win._ouid, mine=True, sp_id=shooter.sp_id).shots
    every = st_mod.shot_map(_win._details, _win._ouid, mine=True).shots
    assert len(want) < len(every), "그 카드 슛과 전체 슛이 같아 잴 수 없다"
    assert len(maps) == 1 and maps[0]._shots == want, (len(maps[0]._shots), len(want))
    # 픽스처는 슛이 적다 — 추이 대신 '표본 부족'. 기준을 낮추면 그래프가 나온다
    trend = [x for x in rec.findChildren(app_main.QWidget) if x.objectName() == "myRecordTrend"][0]
    assert isinstance(trend, app_main.QLabel) and "표본 부족" in trend.text(), type(trend)
    saved = app_main.core.PLAYER_TREND_MIN_SHOTS  # 화면이 읽는 쪽 — core_api 는 값을 복사해 온다
    app_main.core.PLAYER_TREND_MIN_SHOTS = 1
    try:
        rec2 = _win._build_my_record(shooter.sp_id)
    finally:
        app_main.core.PLAYER_TREND_MIN_SHOTS = saved
    trend2 = [x for x in rec2.findChildren(app_main.QWidget) if x.objectName() == "myRecordTrend"][0]
    assert isinstance(trend2, charts.AreaTrendChart), type(trend2)


def test_search_hands_current_account_to_loader():
    got = []

    class _Rec:
        def __init__(self, api, nick, mt, prev=None, prefetch=None, record_elo=False, want_max_division=False,
                     track_missed=False):
            got.append(prev)
            pf.append(prefetch)
            elo.append(record_elo)
            maxdiv.append(want_max_division)
            self.progress = self.finished_ok = self.failed = self.key_invalid = self.quota_hit = self
            self.outcome = self
            self.rank_ready = self.max_division_ready = self.finished = self
            self.elo_saved = _Sig()


        def connect(self, *_):
            pass

        def start(self):
            pass

        def isRunning(self):
            return False

    class _Sig:
        def connect(self, slot):
            elo_slots.append(slot)

    pf, elo, maxdiv, elo_slots = [], [], [], []
    orig = app_main.MatchLoader, _win._loader
    app_main.MatchLoader, _win._loader = _Rec, None
    _win._prefetch = "켤 때 미리 읽은 것"
    try:
        _win._api_search("아무개")
        _win._api_search("아무개")
    finally:
        app_main.MatchLoader, _win._loader = orig
        _win._prefetch = None
        _win._set_busy(False)
    assert len(got) == 2 and got[0][0] == _OUID, got
    assert got[0][1] is _win._matches_all and got[0][2] is _win._details_all, "지금 가진 목록을 넘기지 않았다"
    # 미리 읽은 것은 첫 검색에 한 번만 — 두 번째 검색까지 들고 있으면 옛 스냅숏을 다시 바탕으로 쓴다
    assert pf == ["켤 때 미리 읽은 것", None], pf
    assert elo == [True, True], "메인 검색이 ELO 를 안 적는다"
    assert maxdiv == [True, True], "메인 검색이 역대 최고 등급을 안 묻는다"
    assert elo_slots == [_win._load_elo] * 2, "이번 검색 ELO 를 적은 뒤 그래프를 다시 읽지 않는다(elo_saved)"


def test_start_prefetch_reads_last_searched_account():
    tmp, saved = _loader_db(4)
    try:
        conn = store.open_db(config.DB_PATH)
        try:
            # 시각을 직접 둔다 — 같은 초에 넣으면 '최근' 순서가 동률이다
            store.upsert_account(conn, _OUID, "테스트구단주")
            store.upsert_account(conn, "빈계정", "빈")          # 경기 없는 계정 — 이게 최근이면 안 읽는다
            conn.execute("UPDATE accounts SET last_seen = ? WHERE ouid = ?", ("2026-10-01T00:00:00", _OUID))
            conn.execute("UPDATE accounts SET last_seen = ? WHERE ouid = ?", ("2026-10-02T00:00:00", "빈계정"))
            conn.commit()
            empty_last = _win._last_account()
            conn.execute("UPDATE accounts SET last_seen = ? WHERE ouid = ?", ("2026-10-03T00:00:00", _OUID))
            conn.commit()
        finally:
            conn.close()
        assert empty_last is None, empty_last
        _win._prefetch = None
        _win.start_prefetch()
        assert _win._prefetch is not None and _win._prefetch.ouid == _OUID, "마지막 계정을 미리 읽지 않았다"
        matches, details = _win._prefetch._future.result()
        assert len(details) == 4 and len(matches) == 4
    finally:
        _win._prefetch = None
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_prune_cache_deletes_only_stored_matches():
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        api = nexon_api.FCOnlineAPI("k", cache_dir=tmp)
        for name in ("aaa111", "bbb222", "meta_spid"):
            (tmp / f"{name}.json").write_text("{}", encoding="utf-8")
        # 지켜야 할 것이 정말 있는지 먼저 — DB 에 없는 경기(이어 받기용)와 메타
        assert (tmp / "bbb222.json").exists() and (tmp / "meta_spid.json").exists()
        n, size = api.prune_detail_cache({"aaa111", "meta_spid", "zzz999"})
        assert (n, size) == (1, 2), (n, size)
        assert sorted(p.name for p in tmp.iterdir()) == ["bbb222.json", "meta_spid.json"]
        api.forget_details(["bbb222", None, "없는것"])
        assert sorted(p.name for p in tmp.iterdir()) == ["meta_spid.json"]
        # 멈추라면 멈춘다(창을 닫는 중)
        (tmp / "ccc333.json").write_text("{}", encoding="utf-8")
        assert api.prune_detail_cache({"ccc333"}, stop=lambda: True) == (0, 0)
        assert (tmp / "ccc333.json").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quota_stops_without_saving_holes():
    first = sorted(d["matchId"] for d in _DETAILS)[:1]
    # 대조: 한도가 아닌 오류는 그 경기만 건너뛰고 나머지를 저장한다(기존 동작)
    other = _run_loader(_DetailApi(first, "OPENAPI00009"))
    assert other["db"] == len(_DETAILS) - 1 and other["ok"] and not other["quota"], other
    # 한도(429)면 아무것도 저장하지 않고 멈춘다 — 저장하면 그 구멍은 다음 검색에서 안 메워진다
    hit = _run_loader(_DetailApi(first, nexon_api.QUOTA_CODE))
    assert hit["db"] == 0 and not hit["ok"], hit
    assert len(hit["quota"]) == 1 and "서비스 단계" in hit["quota"][0], hit["quota"]


def test_server_fail_stops_without_saving_holes_or_key_dialog():
    # 중앙 서버도 직접도 응답이 없었다(nexon_api.SERVER_CODES) — 한도처럼 저장하지 않고 멈추되, 서버 사정이라
    # 키 창(quota_hit)·그날 랭커 픽 끄기(_mark_final_429)는 안 한다(docs/CENTRAL_SERVER.md 11절 · 1회차 검토 [상]3)
    first = sorted(d["matchId"] for d in _DETAILS)[:1]
    marks = []
    orig = store.mark_final_429
    store.mark_final_429 = lambda day: marks.append(day)
    try:
        for code in sorted(nexon_api.SERVER_CODES):
            hit = _run_loader(_DetailApi(first, code))
            assert hit["db"] == 0 and not hit["ok"], (code, hit)
            assert not hit["quota"], f"{code} 가 키 창으로 갔다"
            assert len(hit["failed"]) == 1 and "중앙 서버" in hit["failed"][0], (code, hit["failed"])
        assert not marks, "서버 사정으로 그날 랭커 픽을 껐다"
    finally:
        store.mark_final_429 = orig


class _PagedApi(_DetailApi):
    """96 W2 — 목록을 쪽으로 나눠 준다(PAGE_SIZE 를 줄여) · fail 에 든 경기는 그 이유로 실패 · 물은 상세를 센다."""

    def __init__(self, fail=None):
        super().__init__([], "")
        self.fail = dict(fail or {})   # match_id → "error" | "network" | "format"
        self.asked = []
        self.order = sorted(self.by_id)

    def get_match_ids(self, ouid, matchtype, offset, limit):
        return self.order[offset:offset + limit]

    def get_match_detail(self, mid):
        self.asked.append(mid)
        how = self.fail.get(mid)
        if how == "error":
            raise nexon_api.NexonAPIError("x", code="OPENAPI00009", status=500)
        if how == "network":
            raise nexon_api.NexonAPIError("x", code=nexon_api.NETWORK_CODE)
        if how == "format":
            raise ValueError("JSON 아님")
        return self.by_id[mid]


def test_missed_details_are_retried_and_counted_96():
    # 96 W2 — 상세 하나가 실패하면 나머지는 저장되고, 다음 검색은 첫 쪽이 다 아는 경기라 멈춰 그 경기를 영영 안 받았다(구멍).
    # 이제 detail_retry 가 다음 검색 todo 앞에 붙이고 · 3번이면 포기하고 아는 경기로 · 이유별 수가 outcome 에 실린다
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved = config.DB_PATH, config.WEB_DATA, app_main.PAGE_SIZE
    config.DB_PATH, config.WEB_DATA, app_main.PAGE_SIZE = tmp / "t.db", False, 2

    def search(api, track=True):
        outs, ok = [], []
        ld = app_main.MatchLoader(api, "닉", 52, track_missed=track)
        ld.outcome.connect(outs.append)
        ld.finished_ok.connect(lambda *a: ok.append(a[6]))
        ld.failed.connect(lambda m: ok.append(("실패", m)))
        ld.run()
        assert len(outs) == 1, outs
        return outs[0], ok

    def tries():
        conn = store.open_db(config.DB_PATH)
        try:
            return {r["match_id"]: (r["reason"], r["tries"]) for r in conn.execute("SELECT * FROM detail_retry")}
        finally:
            conn.close()

    try:
        order = sorted(d["matchId"] for d in _DETAILS)
        last = order[-1]   # 둘째 쪽 — 첫 쪽(order[:2])이 다 아는 경기면 목록 확인이 거기서 멈춘다
        # 막지 않았으면 일어났을 일(대조): 남기지 않는 로더는 다음 검색에서 그 경기를 다시 안 묻는다
        out, ok = search(_PagedApi({last: "error"}), track=False)
        assert ok == [len(order) - 1] and out.stop == "" and out.listed == len(order), (ok, out)
        api = _PagedApi()
        search(api, track=False)
        assert last not in api.asked, "대조가 서지 않는다 — 구멍이 원래 없었다"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()

        # 남기는 로더 — 실패 하나는 이유별로 세고 줄에 남는다
        out, ok = search(_PagedApi({last: "error"}))
        assert out.missed == {store.RETRY_ERROR: 1} and out.saved == len(order) - 1, out
        assert tries() == {last: (store.RETRY_ERROR, 1)}, tries()
        assert "다음 검색 때 다시 받습니다" in app_main.missed_tip(out), app_main.missed_tip(out)
        # 다음 검색은 둘째 쪽까지 안 가도 그 경기를 다시 묻고, 받으면 줄에서 뺀다
        api = _PagedApi()
        out, ok = search(api)
        assert api.asked == [last] and ok == [1] and out.missed == {}, (api.asked, ok, out)
        assert tries() == {}, tries()

        # 3번 실패하면 포기 — 넷째 검색은 묻지 않고, 첫 쪽에 있어도 목록 확인이 거기서 멈춘다
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        first = order[0]
        for n in range(1, config.DETAIL_RETRY_MAX + 1):
            out, ok = search(_PagedApi({first: "error"}))
            want = {app_main.MISSED_GAVE_UP: 1} if n == config.DETAIL_RETRY_MAX else {store.RETRY_ERROR: 1}
            assert out.missed == want and tries()[first][1] == n, (n, out, tries())
        api = _PagedApi()
        out, ok = search(api)
        assert api.asked == [] and out.missed == {app_main.MISSED_GAVE_UP: 1}, (api.asked, out)
        assert out.listed == 2, f"포기한 경기가 첫 쪽에 있어 목록을 끝까지 넘겼다: {out.listed}"

        # 네트워크 끊김은 하루 한 번만 센다 — 끊긴 채 몇 번 검색해도 포기하지 않는다
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        for _ in range(config.DETAIL_RETRY_MAX + 1):
            out, ok = search(_PagedApi({last: "network"}))
        assert tries() == {last: (store.RETRY_NETWORK, 1)} and out.missed == {store.RETRY_NETWORK: 1}, (tries(), out)

        # 깨진 JSON(ValueError) — 전엔 검색 전체가 "예기치 못한 오류" 로 죽었다
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        out, ok = search(_PagedApi({last: "format"}))
        assert ok == [len(order) - 1] and out.missed == {store.RETRY_FORMAT: 1}, (ok, out)

        # 다른 길로 이미 저장된 경기는 읽을 때 줄에서 지운다(상대 검색·랭커 픽)
        conn = store.open_db(config.DB_PATH)
        try:
            store.save_matches(conn, [d for d in _DETAILS if d["matchId"] == last])
            assert store.load_detail_retry(conn, _OUID, 52) == {}
        finally:
            conn.close()
        assert tries() == {}, tries()

        # 한도로 멈추면 저장하지 않으니 tries 에 안 센다 · 못 받은 수는 todo 전부
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        out, ok = search(_DetailApi(order[:1], nexon_api.QUOTA_CODE))
        assert out.stop == "quota" and out.missed == {app_main.MISSED_QUOTA: len(order)} and tries() == {}, out
        assert "내일 다시 검색하면" in app_main.missed_tip(out)
    finally:
        config.DB_PATH, config.WEB_DATA, app_main.PAGE_SIZE = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_search_outcome_reaches_status_cell_96():
    # 배선 — 검색창 검색(_api_search)의 로더만 남기기를 켜고 outcome 을 상태 줄 칸에 잇는다 · 저장본 열기·다시 읽기는 아니다
    made = []

    class _Rec:
        def __init__(self, *a, **k):
            made.append(k)
            self.outcome = _Sig()
            self.progress = self.finished_ok = self.failed = self.finished = self.key_invalid = _Sig()
            self.quota_hit = self.rank_ready = self.max_division_ready = self.elo_saved = _Sig()

        def isRunning(self):
            return False

        def start(self):
            pass

    class _Sig:
        def __init__(self):
            self.slots = []

        def connect(self, f):
            self.slots.append(f)

    saved = app_main.MatchLoader, _win._loader, _win._ouid
    app_main.MatchLoader, _win._loader = _Rec, None
    try:
        _win._api_search("닉")
        assert made and made[-1].get("track_missed") is True, made
        assert _win._on_search_outcome in _win._loader.outcome.slots
        # 칸 — 0 이면 숨김 · 취소는 보던 값을 둔다
        out = app_main.SearchOutcome(listed=5, saved=3, missed={store.RETRY_ERROR: 2}, stop="")
        _win._on_search_outcome(out)
        assert _win.lb_missed.isVisibleTo(_win) and _win.lb_missed.text() == "받지 못한 경기 2", _win.lb_missed.text()
        assert "넥슨이 오류로 답했습니다" in _win.lb_missed.toolTip()
        _win._on_search_outcome(app_main.SearchOutcome(stop="cancel"))
        assert _win.lb_missed.text() == "받지 못한 경기 2"
        _win._on_search_outcome(app_main.SearchOutcome(stop=""))
        assert not _win.lb_missed.isVisibleTo(_win) and _win.lb_missed.text() == ""
    finally:
        app_main.MatchLoader, _win._loader, _win._ouid = saved
        _win._set_busy(False)


def test_about_connection_check_tab_96():
    # 96 W3 — [정보 › 연결 점검] 이 검색과 같은 API 객체로 사용자용 묶음을 돌려 표로 · 지우는 중엔 시작 안 함 · 종료 표에 있다
    import check_api

    class _Api(_DetailApi):
        server_mode = False

        def get_match_detail_nocache(self, mid, attempts=1):
            return self.by_id[mid]

        def get_meta(self, name):
            return [{"id": 1, "name": "가"}]

    api = _Api([], "")
    saved = (_win._api, _win._nick, config.API_KEY, config.WEB_DATA, images.fetch_icon,
             app_main.ConnectionCheckWorker.start, _win._check_worker)

    def fake_icon(url, folder, kind=""):
        p = pathlib.Path(folder) / "x.png"
        p.write_bytes(b"x")
        return p

    _win._api, _win._nick, config.API_KEY, config.WEB_DATA = api, "테스트구단주", "k", False
    images.fetch_icon = fake_icon
    app_main.ConnectionCheckWorker.start = lambda self: self.run()   # 스레드 대신 그 자리에서
    try:
        dlg = app_main.AboutDialog(_win)
        tabs = dlg.findChild(app_main.QTabWidget)
        assert "연결 점검" in [tabs.tabText(i) for i in range(tabs.count())]
        assert dlg.ed_check_nick.text() == "테스트구단주", "기본은 마지막 계정"
        dlg._on_check_start()
        t = dlg.tbl_check
        got = [(t.item(r, 0).text(), t.item(r, 1).text()) for r in range(t.rowCount())]
        assert [g[0] for g in got] == [n for n, _f in check_api.USER_CHECKS], got
        assert [g[1] for g in got].count("정상") == len(got) - 1 and got[-1][1] == "건너뜀", got   # 웹 데이터 꺼짐
        assert _win._check_worker._api is api, "검색과 다른 API 객체로 쟀다"
        dlg._on_check_done()
        assert dlg.btn_check.isEnabled() and "마쳤습니다" in dlg.lb_check.text()
        # 지우는 중이면 시작하지 않는다(걸쇠)
        app_main.CLEARING.add(app_main.STORE_DETAILS)
        try:
            assert _win.start_connection_check("x") == app_main.CLEARING_WAIT_MSG
        finally:
            app_main.CLEARING.discard(app_main.STORE_DETAILS)
        # 창이 닫힐 때 멈출 대상(종료 표)에 들어 있다
        _win._check_worker.isRunning = lambda: True
        stopped = []
        _win._check_worker.cancel = lambda: stopped.append(1)
        left = _win.shutdown(fast=True)
        assert stopped and _win._check_worker in left, "종료가 연결 점검 스레드를 멈추지 않았다"
        dlg.close()
    finally:
        (_win._api, _win._nick, config.API_KEY, config.WEB_DATA, images.fetch_icon,
         app_main.ConnectionCheckWorker.start, _win._check_worker) = saved


def test_quota_signal_reaches_key_dialog():
    # 배선 — 검색 로더의 quota_hit 이 키 변경 창으로 이어져야 한다(QThread.start 는 막혀 있다)
    asked = []
    orig = _win._ask_new_key, _win._loader, app_main.QMessageBox.warning, _win._nick
    _win._ask_new_key = asked.append
    app_main.QMessageBox.warning = lambda *a, **k: None
    try:
        _win._loader = None
        _win._api_search("닉")
        _win._loader.quota_hit.emit("한도")
        assert asked == ["한도"], asked
    finally:
        _win._ask_new_key, _win._loader, app_main.QMessageBox.warning, _win._nick = orig
        _win._set_busy(False)


def test_rejected_key_asks_and_swaps_key():
    seen = {}

    class _Dlg:
        def __init__(self, parent=None, reason=""):
            seen["reason"] = reason

        def exec(self):
            config.API_KEY = "fresh_key"  # 창이 save_api_key 를 부른 것과 같은 상태
            return app_main.QDialog.DialogCode.Accepted

    class _Api:
        def set_key(self, key):
            seen["key"] = key

    orig = app_main.ApiKeyDialog, _win._api, app_main.QMessageBox.warning
    with _TempEnv():
        app_main.ApiKeyDialog, _win._api = _Dlg, _Api()
        app_main.QMessageBox.warning = lambda *a, **k: None  # 모달이라 offscreen 에서 멈춘다
        try:
            _win._on_key_invalid("거절됨")
        finally:
            app_main.ApiKeyDialog, _win._api, app_main.QMessageBox.warning = orig
    assert seen == {"reason": "거절됨", "key": "fresh_key"}, seen


def _shots():
    out = os.environ.get("UI_SHOT")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    # 이벤트 루프가 없어 deleteLater 가 안 돈다 — 지운 '최근 검색' 칩이 (0,0)에
    # 640x480 으로 남아 검색 상자를 덮어 찍혔다. 실제 앱에선 생기지 않는다.
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    _win.stack.setCurrentIndex(_win.PAGE_SEARCH)
    _win.grab().save(os.path.join(out, "00_검색.png"))
    _win.stack.setCurrentIndex(_win.PAGE_MAIN)
    for item, idx in _nav_items():
        if idx is None:
            continue
        _win.nav.setCurrentItem(item)
        _app.processEvents()
        _win.grab().save(os.path.join(out, f"{idx + 1:02d}_{item.text()}.png"))


# ── 변이 5순위 — 화면 코드 중 사용자가 실제로 겪을 잘못만(2026-10-03) ───────────────────
# 버튼·표시 분기를 전부 막으면 테스트가 화면 구조에 묶인다. 틀리면 '틀린 숫자·엉뚱한 색·
# 엉뚱한 조회'가 되는 곳만 고른다.
def test_selected_season_filter_edges():
    import seasons as sn_
    from datetime import date as d_, datetime as dt_
    saved = _win._rank_seasons
    s1 = sn_.Season(no=1, name="시즌 1", start=d_(2025, 1, 1), end=d_(2025, 6, 1))
    _win._rank_seasons = [s1]
    try:
        f = _win._in_selected_season
        assert f(None, s1) is False and f(None, _win.ONGOING) is False   # 날짜 없는 경기는 어디에도 안 든다
        cut = config.SEASON_CUTOFF_H                                       # 경계 = 마지막 종료일 이 시각(76 · 그 새벽은 옛 시즌)
        assert f(dt_(2025, 6, 1, cut, 0), _win.ONGOING)
        assert not f(dt_(2025, 6, 1, cut - 1, 59), _win.ONGOING)
        assert f(dt_(2025, 6, 1, cut - 1, 59), s1) and not f(dt_(2025, 6, 1, cut), s1)
        _win._rank_seasons = []
        assert not f(dt_(2025, 6, 1), _win.ONGOING)                       # 시즌표가 없으면 진행 중도 없다
        # 설정 파일에 남기는 시즌 이름 — 셋이 서로 달라야 다음에 켤 때 같은 시즌으로 돌아온다
        keys = [_win._season_key(None), _win._season_key(_win.ONGOING), _win._season_key(s1)]
        assert keys == ["all", "ongoing", "s1"], keys
    finally:
        _win._rank_seasons = saved


def test_match_rows_tinted_by_result():
    import dataclasses
    results = ["승", "패", "무", "몰수승", "몰수패", "오류"]
    ms = [dataclasses.replace(_MATCHES[0], match_id=f"t{i}", result=r) for i, r in enumerate(results)]
    _win._render_matches(ms)
    want = {"승": T.WIN, "몰수승": T.WIN, "패": T.LOSE, "몰수패": T.LOSE}
    seen = set()
    lst = _win.match_list
    for r in range(lst.rowCount()):   # 65 M2 — 카드 바탕(옛 표 행 색과 같은 토큰) + 왼쪽 결과 띠
        card = lst.row(r)
        col = lst.card_delegate.colors(card)
        tinted = T.blend(T.PANEL, want[card.result], T.ROW_TINT) if card.result in want else None
        if tinted:
            assert col["bg"] == tinted, (card.result, col, tinted)
            assert col["stripe"] == (T.GOOD_FILL if want[card.result] == T.WIN else T.LOSE_FILL), col   # 95 — 패 진회색
        else:
            assert col["bg"] == T.PANEL and col["stripe"] not in (T.GOOD_FILL, T.LOSE_FILL), (card.result, col)
        assert card.match_id, "더블클릭용 경기 id 가 안 붙었다"
        seen.add(card.result)
    assert seen == set(results), seen
    _win._render_matches(_win._matches)


def test_compare_colors_the_better_side():
    import dataclasses
    from models import summarize as summ
    from PySide6.QtGui import QColor
    green = QColor(T.GOOD).name()
    mine = [dataclasses.replace(m, result="승", opp_goals=0) for m in _MATCHES]
    # 상대는 더 많이 먹는다 — 실점 카드는 "나가 낮음 = 좋음" 이라 ▼ 인데 GOOD 여야 한다(lower_better)
    opp = [dataclasses.replace(m, result="패", match_id=f"o{i}", opp_goals=3) for i, m in enumerate(_MATCHES)]
    saved = (_win._matches, _win._fill_compare_squad)
    _win._matches = mine
    _win._fill_compare_squad = lambda *a: None          # 스쿼드 그림은 이미지 받기라 뺀다
    try:
        _win._render_compare("상대", opp, "opp", [])
        a, b = summ(mine), summ(opp)
        t = _win.tbl_compare
        assert t.item(0, 1).foreground().color().name() != green                     # 경기수는 우열 없음
        for r, (label, attr, _fmt, higher) in enumerate(_win.COMPARE_ROWS, start=1):
            mv, ov = getattr(a, attr), getattr(b, attr)
            mc, oc = t.item(r, 1).foreground().color().name(), t.item(r, 2).foreground().color().name()
            if mv == ov:
                assert green not in (mc, oc), (label, mc, oc)                       # 같으면 아무도 초록이 아니다
            else:
                assert (mc == green) == ((mv > ov) == higher) and (oc == green) != (mc == green), (label, mv, ov)
        assert t.item(1, 1).foreground().color().name() == green, "승률이 높은 쪽(나)이 초록이 아니다"
        lower_better = next(i for i, row in enumerate(_win.COMPARE_ROWS, start=1) if row[3] is False)
        assert lower_better and _win.COMPARE_ROWS[lower_better - 1][1] == "avg_goals_against"
        # 67 R11 머리 숫자 카드 — 큰 숫자 = 표의 내 칸 · 보조 = 상대 칸 · ▲▼ 색 = 표의 초록 쪽과 같은 판정
        cards = _win.compare_cards
        assert list(cards) == [row[0] for row in _win.COMPARE_ROWS[:4]], list(cards)
        for r, (label, attr, _fmt, higher) in enumerate(_win.COMPARE_ROWS[:4], start=1):
            c = cards[label]
            assert c.value.text() == t.item(r, 1).text() and c.sub.text() == f"상대 {t.item(r, 2).text()}", \
                (label, c.value.text(), c.sub.text())
            mv, ov = getattr(a, attr), getattr(b, attr)
            want = T.TEXT_DIM if abs(mv - ov) < 0.005 else T.GOOD if (mv > ov) == higher else T.BAD
            assert c.delta.property("delta_color") == want, (label, mv, ov, c.delta.property("delta_color"))
        assert "▼" in cards["평균 실점"].delta.text() and cards["평균 실점"].delta.property("delta_color") == T.GOOD
        assert cards["승률"].delta.property("delta_color") == T.GOOD
    finally:
        _win._matches, _win._fill_compare_squad = saved[0], saved[1]


# ── 축구장 v2(C) · ⑨ · ① (2.1.1 15단계) ───────────────────────────────────────

_FORMATIONS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "formations.json"),
                             encoding="utf-8"))


def _pitch_for(codes, band=True):
    from widgets import PitchCard, PitchWidget
    cards = [PitchCard(c, f"P{c}", f"선수{i}", 100000000 + i, grade=1 + i % 13) for i, c in enumerate(codes)]
    return PitchWidget(_win._pitch_rows(cards), band=band)


def test_pitch_chips_never_overlap():
    """실제 DB 의 서로 다른 선발 배치 상위 50(포지션 코드만 — tests/fixtures/formations.json)에서, 가장 작은 축구장부터
    넓은 축구장까지 칩 사각형이 서로 안 겹치고 잔디 밖으로 안 나간다. 옛 고정 좌표는 RWB·RB 가 39px 떨어져 겹쳤다(R8)."""
    assert len(_FORMATIONS) == 50
    checked = 0
    for codes in _FORMATIONS:
        pitch = _pitch_for(codes)
        f = pitch.field
        for w, h in (f.minimumWidth(), f.minimumHeight()), (470, 560), (1000, 700):
            f.resize(w, h)
            f.layout_chips()
            rects = [c.geometry() for c in pitch.chips()]
            assert len(rects) == len(codes), (codes, len(rects))
            for i, a in enumerate(rects):
                assert f.rect().contains(a), (codes, w, h, a)
                for b in rects[i + 1:]:
                    assert not a.intersects(b), (codes, w, h, a, b)
            checked += 1
        pitch.deleteLater()
    assert checked == 150


def test_pitch_card_signal_skips_deleted_pitch():
    """카드 정보 로더의 신호가 닫힌 축구장에 늦게 와도 죽지 않고, 살아 있는 축구장엔 정보가 붙는다(73단계 —
    `shiboken6.isValid` 부정 방향. 옛 `sip.isdeleted` 시절부터 이 슬롯을 부르는 테스트가 없었다)."""
    import shiboken6
    import playerinfo
    pitch = _pitch_for([0, 3, 4, 6, 7, 10, 13, 15, 17, 18, 25])
    pitch.card_data = {}                     # _fill_pitch_from_cache 가 먼저 둔다
    sid = pitch.chips()[0].card.sp_id
    info = playerinfo.PlayerInfo(sid, ovr=99, salary=12, prices={1: "1억"})
    _win._on_pitch_card(pitch, info)
    assert pitch.card_data.get(sid, {}).get("base_ovr") == 99, pitch.card_data   # 살아 있으면 붙는다
    gone = _pitch_for([0, 3, 4, 6, 7, 10, 13, 15, 17, 18, 25])
    gone.card_data = {}
    shiboken6.delete(gone)
    assert not shiboken6.isValid(gone)
    _win._on_pitch_card(gone, info)          # 지워진 축구장 — 그냥 돌아온다(RuntimeError 면 FAIL)
    _win._on_pitch_card_done(gone, 3)
    pitch.deleteLater()


def test_pitch_chip_drops_face_then_price_when_small():
    pitch = _pitch_for([0, 3, 4, 6, 7, 10, 13, 15, 17, 18, 25])
    chip = pitch.chips()[0]
    chip.resize(110, 110)
    assert chip.shows_face() and chip.shows_price_line()
    chip.resize(90, chip.FACE_MIN_H - 1)
    assert not chip.shows_face() and chip.shows_price_line()
    chip.resize(90, chip.PRICE_MIN_H - 1)
    assert not chip.shows_face() and not chip.shows_price_line()
    pitch.deleteLater()


def test_pitch_card_fills_ovr_price_and_band():
    """캐시에 있는 카드 정보로 칩(강화 반영 OVR · 그 강화 시세)과 위 띠(가치 합 · 모르는 장 수 · 급여 합)를 채운다."""
    pitch = _pitch_for([0, 25])        # 카드 100000000(GK, 1강) · 100000001(ST, 2강)
    gk, st_ = 100000000, 100000001
    pitch.card_data = {gk: {"base_ovr": 100, "salary": 10, "prices": {1: 5 * 10 ** 8}},
                       st_: {"base_ovr": 110, "salary": None, "prices": {}}}
    _win._apply_pitch_card(pitch, gk)
    _win._apply_pitch_card(pitch, st_)
    chips = {c.card.sp_id: c for c in pitch.chips()}
    assert (chips[gk].ovr, chips[gk].price) == (100, 5 * 10 ** 8)
    assert (chips[st_].ovr, chips[st_].price) == (111, None), "2강 OVR 은 +1(GRADE_OVR_BONUS)"
    text = pitch.lb_value.text()
    assert "5억" in text and "1장 시세 모름" in text and "급여 10 (1장 모름)" in text, text
    pitch.deleteLater()


def test_pitch_gate_and_notice_button_starts_reading():
    """자동 읽기가 막히면 띠에 이유 — 동의가 답이면 [안내 보기], 누르고 동의하면 그 축구장이 바로 읽기 시작(E10 · 3회차)."""
    keep = (config.NOTICE_ACCEPTED, config.WEB_DATA, _win.ask_notice_update, list(_FakeCardInfoLoader.started),
            _win.stack.currentIndex())
    try:
        _win.show()
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        config.NOTICE_ACCEPTED, config.WEB_DATA = 4, True
        pitch = _pitch_for([0, 25])
        _win.box_compare_my_squad.addWidget(pitch)      # 보이는 자리에 — 다시 읽기는 보이는 축구장만
        _win._go_page("구단주 비교")
        _app.processEvents()
        _FakeCardInfoLoader.started.clear()
        _win._start_pitch_loaders(pitch)
        assert not _FakeCardInfoLoader.started, "동의 전(4)에 칩 카드 정보를 읽었다"
        assert pitch.lb_gate.text() == _win.PITCH_GATE_NOTICE and pitch.btn_notice.isVisibleTo(pitch)

        def accept():
            # 웹 데이터도 여기서 다시 — 앞 테스트가 남긴 팀컬러 목록 스레드가 .env(임시 · 꺼짐)를 다시 읽어 끌 수 있다
            config.NOTICE_ACCEPTED, config.WEB_DATA = 5, True
            _win._refresh_pitch_cards()
            return True
        _win.ask_notice_update = accept
        pitch.btn_notice.click()
        # 메인 창 안의 다른 막힌 축구장(앞 테스트의 비교 화면)도 같이 읽기 시작한다 — 이 축구장이 그중에 있으면 된다
        assert pitch.sp_ids() in _FakeCardInfoLoader.started, list(_FakeCardInfoLoader.started)
        n_after = len(_FakeCardInfoLoader.started)
        assert not pitch.btn_notice.isVisibleTo(pitch) and pitch.lb_gate.text() == ""
        # 웹 데이터 꺼짐 — 이유만, 버튼 없음(동의가 답이 아니다)
        config.WEB_DATA = False
        p2 = _pitch_for([0])
        _win._start_pitch_loaders(p2)
        assert p2.lb_gate.text() == _win.PITCH_GATE_WEB_OFF and not p2.btn_notice.isVisibleTo(p2)
        assert len(_FakeCardInfoLoader.started) == n_after, "웹 데이터가 꺼졌는데 읽었다"
        p2.deleteLater()
    finally:
        config.NOTICE_ACCEPTED, config.WEB_DATA, _win.ask_notice_update = keep[:3]
        _FakeCardInfoLoader.started[:] = keep[3]
        _win.stack.setCurrentIndex(keep[4])
        _win._clear(_win.box_compare_my_squad)


def test_ask_notice_update_refreshes_pitches():
    """ask_notice_update(진짜 함수)가 동의 뒤 축구장 다시 보기를 부른다 — 빼면 [안내 보기]로 동의해도 칩이 그대로 빈다."""
    import inspect
    assert "_refresh_pitch_cards()" in inspect.getsource(app_main.MainWindow.ask_notice_update)


def test_compare_two_pitches_fit_min_window():
    """구단주 비교의 축구장 둘이 1280×720 창에서 가로 스크롤 없이 나란히(예전엔 560×2 를 가로 스크롤로 버텼다)."""
    saved = (None, config.WEB_DATA, _win.stack.currentIndex())
    config.WEB_DATA = False
    try:
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        _at_size(*app_main.MIN_WINDOW)
        _win._go_page("구단주 비교")
        _win._render_compare("상대", list(_MATCHES), _OUID, list(_DETAILS))
        _app.processEvents()
        _app.processEvents()
        frame = _win.pages.currentWidget()
        pitches = [box.itemAt(i).widget() for box in (_win.box_compare_my_squad, _win.box_compare_opp_squad)
                   for i in range(box.count()) if isinstance(box.itemAt(i).widget(), app_main.PitchWidget)]
        assert len(pitches) == 2, len(pitches)
        inner = frame.widget().minimumSizeHint().width()
        assert inner <= frame.viewport().width(), (inner, frame.viewport().width())
        assert not frame.horizontalScrollBar().isVisible()
        a, b = sorted(pitches, key=lambda p: p.mapTo(frame, p.rect().topLeft()).x())
        assert a.mapTo(frame, a.rect().topRight()).x() < b.mapTo(frame, b.rect().topLeft()).x(), "나란히가 아니다"
        for p in pitches:
            assert p.field.width() >= p.field.minimumWidth() and len(p.chips()) == 11, (p.field.width(), len(p.chips()))
    finally:
        config.WEB_DATA = saved[1]
        _win.stack.setCurrentIndex(saved[2])
        _at_size(1600, 900)


def test_compare_profile_rows_and_key_players():
    try:
        _win._render_compare("상대", list(_MATCHES), _OUID, list(_DETAILS))
        t = _win.tbl_compare
        labels = [t.item(r, 0).text() for r in range(t.rowCount())]
        for label, *_ in _win.COMPARE_PROFILE_ROWS:
            assert label in labels, (label, labels)
        prof = {a.name: a.mine for a in core.team_profile(list(_DETAILS), _OUID).axes}
        r = labels.index("패스 성공률")
        assert t.item(r, 1).text() == f"{prof['패스 성공률']:.1f}%", (t.item(r, 1).text(), prof)
        kp = core.key_players(list(_DETAILS), _OUID, name_of=lambda i: _win._names.get(i, str(i)))
        html = _win.lb_compare_keys[0].text()
        assert "키플레이어" in html and all(p.name in html for p in kp.by_rating), html
        # 범위 — 비교 경기 수를 줄이면 그 경기만(승·무·패로 끝난 경기 하나 — 몰수·오류는 team_profile 이 뺀다)
        first = next(m for m in _MATCHES if m.result in ("승", "무", "패"))
        _win._matches, keep_m = [first], _win._matches   # 76 — 비교 범위는 위쪽 바(_slice) · 내 쪽은 시즌 목록에서
        try:
            _win._render_compare("상대", [first], _OUID, list(_DETAILS))
        finally:
            _win._matches = keep_m
        one = _win._details_of(list(_DETAILS), [first])
        assert len(one) == 1 and one[0]["matchId"] == first.match_id
        labels = [t.item(r, 0).text() for r in range(t.rowCount())]
        prof1 = {a.name: a.mine for a in core.team_profile(one, _OUID).axes}
        assert t.item(labels.index("경기당 슛"), 1).text() == f"{prof1['슈팅']:.1f}"
        # 지표 카드는 접힌다 — 제목을 눌러도, 오른쪽 "접기/펼치기"(P9)를 눌러도 같다
        fold = _win.fold_compare_metrics
        assert fold.hint.text() == fold.HINT[True]
        fold.head.click()
        assert not fold.is_open() and fold.hint.text() == fold.HINT[False]
        fold.hint.click()
        assert fold.is_open() and fold.hint.text() == fold.HINT[True]
        fold.hint.click()
        assert not fold.is_open()
        fold.head.click()
        assert fold.is_open()
    finally:
        pass


def test_compare_extra_tabs_render_and_fit():
    """구단주 비교 D1(50단계) — 탭 셋 · 차이 요약 문구 · 슛 지도·골 흐름 둘 · 자리별 표, 1280×720 에서 가로 스크롤 없이."""
    saved = (None, config.WEB_DATA, _win.stack.currentIndex(), _win._matches, _win._details)
    config.WEB_DATA = False
    real_summary = app_main.core.compare_summary
    try:
        assert _win.pt_compare.names() == ["지표·스쿼드", "슛·골 흐름", "자리별"]
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        _at_size(*app_main.MIN_WINDOW)
        _win._go_page("구단주 비교")
        many = list(_DETAILS) * 3                       # 같은 경기 12개 — 요약이 나올 수 있는 최소 경기 수를 넘긴다
        assert len(many) >= app_main.core.COMPARE_MIN_GAMES
        _win._details = many
        # 경기가 적으면 요약 대신 이유를 말한다
        _win._render_compare("상대", list(_MATCHES), _OUID, list(_DETAILS))
        assert "이상일 때" in _win.lb_compare_summary.text(), _win.lb_compare_summary.text()
        # 요약: 고르는 건 core.compare_summary 고 화면은 표 값을 그대로 적는다 — 가짜로 바꿔 끼워 문구를 본다
        fake = app_main.core.CompareDiff("경기당 슛", 3.0, True, 15.0, 7.0)
        seen = []
        app_main.core.compare_summary = lambda *a, **k: (seen.append((a, k)), [fake])[1]
        _win._render_compare("상대", list(_MATCHES), _OUID, many)
        txt = _win.lb_compare_summary.text()
        assert "경기당 슛" in txt and "15.0" in txt and "7.0" in txt and "내게 좋음" in txt, txt
        # 고르기에는 표 값이 같이 간다 — 승률·경기수 줄은 빼고 나머지 8행(방향 검사 재료)
        table = seen[-1][0][4]
        assert table and "승률" not in table and "경기수" not in table and "경기당 슛" in table and "평균 득점" in table, table
        # 행 이름은 문자열로만 이어져 있다 — 라벨이 어긋나면 방향 검사가 조용히 빈 검사가 된다
        import stats as _stats
        assert {n for n, _h, _f in _stats.COMPARE_FEATURES} == set(table), (set(table), [n for n, _h, _f in _stats.COMPARE_FEATURES])
        t_row = {_win.tbl_compare.item(r, 0).text(): r for r in range(_win.tbl_compare.rowCount())}["평균 득점"]
        assert f"{table['평균 득점'][0]:.2f}" == _win.tbl_compare.item(t_row, 1).text(), "표에 보이는 값과 다른 값이 갔다"
        app_main.core.compare_summary = lambda *a, **k: []
        _win._render_compare("상대", list(_MATCHES), _OUID, many)
        assert "뚜렷한 차이 없음" in _win.lb_compare_summary.text()
        # 슛 지도·골 흐름·자리별은 같은 상세에서
        used = _win._details_of(many, _MATCHES)
        sm = app_main.core.shot_map(used, _OUID, mine=True)
        assert [len(w._shots) for w in _win.shotmap_compare] == [len(sm.shots)] * 2 and sm.shots, "슛 지도가 비었다"
        assert "기준" in _win.lb_compare_shot_note.text() and "기준" in _win.lb_compare_line_note.text()
        t = _win.tbl_compare_lines
        pairs = app_main.core.line_pairs(used, _OUID, used, _OUID)
        assert pairs and t.rowCount() == 2 * len(pairs), (t.rowCount(), len(pairs))
        assert t.item(0, 0).text() == _win.COMPARE_LINE_NAME[pairs[0].line]
        assert t.item(0, 3).text() == str(pairs[0].mine.games)
        # 좌우 상세가 다르면 각자 자기 것 — 상대는 경기 하나만(슛 수가 내 쪽과 다르다)
        opp_ms = [m for m in _MATCHES if len(app_main.core.shot_map(_win._details_of(many, [m]), _OUID).shots) > 0][:1]
        opp_used = _win._details_of(many, opp_ms)
        assert len(opp_used) == 3 and len(app_main.core.shot_map(opp_used, _OUID).shots) != len(sm.shots)
        _win._render_compare("상대", opp_ms, _OUID, many)
        assert [len(w._shots) for w in _win.shotmap_compare] == [len(sm.shots), len(
            app_main.core.shot_map(opp_used, _OUID).shots)], "상대 슛 지도가 상대 경기가 아니다"
        for i, det in enumerate((used, opp_used)):
            flow = _win.box_compare_flow[i]
            gf = app_main.core.goal_flow(det, _OUID)   # 되짚은 경기 수는 mixed 와 무관
            assert flow.count() > 1 and f"{gf.replayed:,}경기" in flow.itemAt(0).widget().text(), \
                (i, gf.replayed, flow.itemAt(0).widget().text())
        # 1280×720 — 슛 지도 둘이 나란히, 안쪽 스크롤에 가로 막대 없음 / 자리별 표도 안 잘린다
        _win.pt_compare.set_current("슛·골 흐름")
        _app.processEvents()
        _app.processEvents()
        scroll = _win.pt_compare.page(1)   # 이 탭은 자기 QScrollArea 라 바깥 페이지의 최소 크기에는 안 잡힌다
        assert not scroll.horizontalScrollBar().isVisible(), "슛 지도 둘이 1280 창에 안 들어간다"
        a, b = sorted(_win.shotmap_compare, key=lambda s: s.mapTo(scroll, s.rect().topLeft()).x())
        assert a.mapTo(scroll, a.rect().topRight()).x() < b.mapTo(scroll, b.rect().topLeft()).x(), "나란히가 아니다"
        assert b.mapTo(scroll, b.rect().topRight()).x() <= scroll.viewport().width(), "오른쪽 지도가 잘렸다"
        _win.pt_compare.set_current("자리별")
        _app.processEvents()
        frame = _win.pages.currentWidget()
        assert not frame.horizontalScrollBar().isVisible()
        _check_elisions([_win.tbl_compare_lines])
    finally:
        app_main.core.compare_summary = real_summary
        _win._matches, _win._details = saved[3], saved[4]
        _win.pt_compare.set_current("지표·스쿼드")
        config.WEB_DATA = saved[1]
        _win.stack.setCurrentIndex(saved[2])
        _at_size(1600, 900)


_PO_SYM = [0, 3, 4, 6, 7, 10, 12, 16, 18, 24, 26]     # 4-1-2-1-2 · 좌우 짝이 다 있다
_PO_ASYM = [0, 3, 4, 6, 7, 10, 12, 13, 18, 24, 26]    # 같은 4-1-2-1-2 인데 오른쪽 RM·RCM 만(더 흔하다)
_PO_424 = [0, 3, 4, 6, 7, 9, 11, 20, 22, 24, 26]      # 4-2-0-0-4 → "4-2-4"
_PO_3142 = [0, 4, 5, 6, 10, 12, 13, 15, 16, 24, 26]   # 3-1-4-0-2 → "3-1-4-2"
_PO_5032 = [0, 2, 4, 5, 6, 8, 13, 14, 15, 24, 26]     # 5-0-3-0-2 → "5-3-2" (경기 수가 같아 글자 순으로 4번째 — 칩에서 빠진다)
_PO_OTHER = [0, 3, 4, 6, 7, 10, 13, 14, 15, 21, 23]   # 4-1-3-0-2 — 다른 팀컬러


class _PosOppEnv:
    """[포지션별 최다 상대 › 축구장] 합성 상대 기록 — 컬러X 9경기(4-1-2-1-2 5 · 4-2-4 2 · 3-1-4-2 1 · 5-3-2 1) + 컬러Y 4경기.
    화면의 범위 함수(_teamcolor_scope)를 이 기록으로 갈아 끼우고, 콤보·칩 선택을 처음 상태로 돌려놓는다."""

    def __enter__(self):
        def det(nick, positions, base):
            return {"matchId": f"po-{nick}", "matchDate": "2026-10-01T12:00:00",
                    "matchInfo": [{"ouid": _OUID, "nickname": "나", "matchDetail": {"matchResult": "승"}, "player": []},
                                  {"ouid": "x-" + nick, "nickname": nick, "matchDetail": {"matchResult": "패"},
                                   "player": [{"spId": base + p, "spPosition": p, "spGrade": 1} for p in positions]
                                   + [{"spId": 9, "spPosition": 28}]}]}
        rows = ([(f"가{i}", _PO_SYM, 1000) for i in (1, 2)] + [(f"가{i}", _PO_ASYM, 2000) for i in (3, 4, 5)]
                + [(f"가{i}", _PO_424, 3000) for i in (6, 7)] + [("가8", _PO_3142, 4000), ("가9", _PO_5032, 5000)]
                + [(f"나{i}", _PO_OTHER, 6000) for i in (1, 2, 3, 4)])
        self.details = [det(*r) for r in rows]
        self.saved = (dict(_win._team_colors), _win._posopp_formation, config.POSITION_OPP_FORMATIONS,
                      _win.ed_position_color.text())
        _win._team_colors.clear()
        for nick, _p, _b in rows:
            _win._team_colors[nick] = "컬러X" if nick.startswith("가") else "컬러Y"
        _win._teamcolor_scope = lambda: ([], self.details)
        _win._posopp_formation = None
        _win.ed_position_color.setText("")
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentIndex(0)      # "고르세요"
        return self

    def __exit__(self, *exc):
        del _win._teamcolor_scope
        _win._team_colors.clear()
        _win._team_colors.update(self.saved[0])
        _win._posopp_formation, config.POSITION_OPP_FORMATIONS = self.saved[1], self.saved[2]
        _win.ed_position_color.setText(self.saved[3])
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentIndex(0)
        _win._render_position_opponents(list(_win._details))


def _pos_pitch():
    items = [_win.box_position_pitch.itemAt(i).widget() for i in range(_win.box_position_pitch.count())]
    return next((w for w in items if isinstance(w, app_main.PitchWidget)), None)


def _pos_chips():
    out = []
    for i in range(_win.row_position_forms.count()):
        w = _win.row_position_forms.itemAt(i).widget()
        if w is not None:
            out.append(w)
    return out


def test_position_opponents_start_empty_until_color_chosen():
    """열면 빈 축구장 — 팀컬러를 고르기 전에는 칩도 축구장도 표도 없고 안내 한 줄뿐이다(사용자 2026-10-11)."""
    with _PosOppEnv():
        _win._render_position_opponents(list(_win._teamcolor_scope()[1]))
        assert _win.cb_position_color.currentText() == _win.POSITION_COLOR_NONE
        assert _pos_pitch() is None and _pos_chips() == []
        assert _win.tbl_position_opp.rowCount() == 0
        assert _win.lb_position_hint.isHidden() and not _win.lb_position_hint.text(), _win.lb_position_hint.text()
        keep_tc = dict(_win._team_colors)
        _win._team_colors.clear()
        try:
            _win._render_position_opponents(list(_win._teamcolor_scope()[1]))
            assert not _win.lb_position_hint.isHidden() and _win.lb_position_hint.text() == _win.POSITION_NEED_COLORS
        finally:
            _win._team_colors.update(keep_tc)
        _win._render_position_opponents(list(_win._teamcolor_scope()[1]))
        assert _win.lb_position_hint.isHidden(), "아는 팀컬러가 생겼는데 '먼저 불러와야' 가 남았다"
        # 알려진 팀컬러인데 이 범위에 만난 상대가 없으면 칩·축구장 없이 "기록 없음" 안내
        _win._team_colors["유령"] = "컬러Z"
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentText("컬러Z")
        assert _pos_pitch() is None and _pos_chips() == []
        assert _win.lb_position_hint.text() == "표시할 상대 기록이 없습니다.", _win.lb_position_hint.text()
        # 기록이 있는 색을 골랐다가 다시 "고르세요"로 — 처음 상태로 돌아온다
        _win.cb_position_color.setCurrentText("컬러Y")
        assert _pos_chips() and _pos_pitch() is not None, "컬러Y 는 기록이 있다"
        _win.cb_position_color.setCurrentText(_win.POSITION_COLOR_NONE)
        assert _pos_pitch() is None and _pos_chips() == [], "처음 상태로 돌아오지 않았다"


def test_position_opponents_formation_chips_and_eleven_slots():
    """팀컬러를 고르면 포메이션 칩(사용 많은 순 · 최대 3) — 가장 많이 쓴 것이 자동으로 눌려 있고, 고른 포메이션의 11자리만 그린다."""
    with _PosOppEnv():
        _win.cb_position_color.setCurrentText("컬러X")
        chips = _pos_chips()
        texts = [c.text() for c in chips]
        assert [t.split()[0] for t in texts] == ["4-1-2-1-2", "4-2-4", "3-1-4-2"], texts   # 5-3-2 는 4번째라 빠진다
        assert "56%" in texts[0] and "5경기" in texts[0] and "22%" in texts[1] and "2경기" in texts[1], texts
        assert [c.isChecked() for c in chips] == [True, False, False]
        pitch = _pos_pitch()
        codes = {c.card.pos_code for c in pitch.chips()}
        assert codes == set(_PO_SYM) and len(pitch.chips()) == 11, sorted(codes)           # 좌우 맞는 구성(더 흔한 ASYM 이 아니라)
        assert 13 not in codes and 16 in codes
        assert {c.card.note for c in pitch.chips()} == {"2회 · 100%"}, {c.card.note for c in pitch.chips()}
        assert _win.tbl_position_opp.rowCount() == 11
        hint = _win.lb_position_hint.text()
        assert "4-1-2-1-2" in hint and "9경기 중 5경기" in hint and "2경기 기준" in hint, hint
        # 다른 칩을 누르면 그 포메이션의 11자리
        chips[1].click()
        assert [c.isChecked() for c in _pos_chips()] == [False, True, False]
        assert {c.card.pos_code for c in _pos_pitch().chips()} == set(_PO_424) and len(_pos_pitch().chips()) == 11
        # 컬러Y 의 포메이션이 새지 않는다
        assert all(not t.startswith("4-1-3-2") for t in texts)
        # 팀컬러를 바꾸면 다시 가장 많이 쓴 포메이션부터 — 4-2-4 는 "전체" 목록에도 있어, 리셋을 빼면 눌린 채로 남는다
        _win.cb_position_color.setCurrentText(_win.POSITION_COLOR_ALL)
        assert [c.isChecked() for c in _pos_chips()] == [True, False, False], [(c.text(), c.isChecked()) for c in _pos_chips()]
        _win.cb_position_color.setCurrentText("컬러X")
        _pos_chips()[1].click()
        _win.cb_position_color.setCurrentText("컬러Y")
        assert [t.text().split()[0] for t in _pos_chips()] == ["4-1-3-2"]
        _win.cb_position_color.setCurrentText("컬러X")
        assert [c.isChecked() for c in _pos_chips()] == [True, False, False]
        # "전체" 는 모든 상대 — 컬러Y 의 포메이션도 순위에 든다
        _win.cb_position_color.setCurrentText(_win.POSITION_COLOR_ALL)
        assert [t.text().split()[0] for t in _pos_chips()] == ["4-1-2-1-2", "4-1-3-2", "4-2-4"]


def test_position_opponent_same_label_formations_use_keys_and_hint_says_who():
    """빈 줄을 뺀 글자가 같은 포메이션(4-1-4-1-0 · 4-1-4-0-1)은 줄 키로 가른다 — 칩도 안내 문구도(Opus 리뷰: 실데이터 6천 경기에서
    같은 글자 묶음이 12개). "전체"를 고르면 안내는 "모든 상대"라고 말한다."""
    a = [0, 3, 4, 6, 7, 10, 12, 13, 15, 16, 18]   # 4-1-4-1-0 (공미 CAM)
    b = [0, 3, 4, 6, 7, 10, 12, 13, 15, 16, 25]   # 4-1-4-0-1 (공격 ST)
    assert core.formation_label("4-1-4-1-0") == core.formation_label("4-1-4-0-1") == "4-1-4-1"
    with _PosOppEnv() as env:
        def det(nick, positions, base):
            return {"matchId": f"dup-{nick}", "matchDate": "2026-10-01T12:00:00",
                    "matchInfo": [{"ouid": _OUID, "nickname": "나", "matchDetail": {"matchResult": "승"}, "player": []},
                                  {"ouid": "x-" + nick, "nickname": nick, "matchDetail": {"matchResult": "패"},
                                   "player": [{"spId": base + p, "spPosition": p, "spGrade": 1} for p in positions]}]}
        rows = [(f"쌍{i}", a, 7000) for i in (1, 2, 3)] + [(f"쌍{i}", b, 8000) for i in (4, 5)]
        env.details = [det(*r) for r in rows]
        for nick, _p, _b in rows:
            _win._team_colors[nick] = "컬러D"
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentText("컬러D")
        texts = [c.text() for c in _pos_chips()]
        assert [t.split()[0] for t in texts] == ["4-1-4-1-0", "4-1-4-0-1"], texts          # 둘 다 키 — "4-1-4-1" 두 개가 아니다
        assert _win.lb_position_hint.text().startswith("4-1-4-1-0: 이 팀컬러 상대 5경기 중 3경기"), _win.lb_position_hint.text()
        _pos_chips()[1].click()
        assert _win.lb_position_hint.text().startswith("4-1-4-0-1: "), _win.lb_position_hint.text()
    with _PosOppEnv():
        # 글자가 하나뿐이면 줄여 쓴다 · "전체" 와 팀컬러의 안내가 다르다
        _win.cb_position_color.setCurrentText(_win.POSITION_COLOR_ALL)
        assert _win.lb_position_hint.text().startswith("4-1-2-1-2: 모든 상대 "), _win.lb_position_hint.text()
        _win.cb_position_color.setCurrentText("컬러X")
        assert _win.lb_position_hint.text().startswith("4-1-2-1-2: 이 팀컬러 상대 "), _win.lb_position_hint.text()


def test_position_opponent_chip_count_follows_config():
    """칩 수 상수가 화면까지 이어진다 — 기본(3)이 아닌 2 로 바꿔서 잰다."""
    with _PosOppEnv():
        config.POSITION_OPP_FORMATIONS = 2
        _win.cb_position_color.setCurrentText("컬러X")
        assert len(_pos_chips()) == 2, [c.text() for c in _pos_chips()]


def test_position_opponents_pitch_and_folded_table():
    """⑨ — 자리마다 가장 많이 만난 상대 카드가 축구장 칩으로(시세 줄 자리에 횟수·비율), 표는 아래 접힘. 카드 정보 요청 0."""
    keep = (list(_FakeCardInfoLoader.started), config.NOTICE_ACCEPTED, config.WEB_DATA)
    try:
        # 게이트를 연 채로 잰다 — 닫혀 있으면 "요청 0"이 ⑨ 덕인지 게이트 덕인지 모른다(변이로 확인: 닫힌 채로는 못 잡았다)
        config.NOTICE_ACCEPTED, config.WEB_DATA = config.CHIP_NOTICE_VERSION, True
        assert config.chip_auto_allowed()
        _FakeCardInfoLoader.started.clear()
        with _PosOppEnv() as env:
            _win.cb_position_color.setCurrentText("컬러X")
            players = core.opponent_position_players(env.details, _OUID,
                                                     pos_name=lambda p: _win._positions.get(p, str(p)),
                                                     nicknames={n for n, c in _win._team_colors.items() if c == "컬러X"},
                                                     formation="4-1-2-1-2")
            pitch = _pos_pitch()
            chips = pitch.chips()
            assert len(chips) == len(players) == 11, (len(chips), len(players))
            by_code = {c.card.pos_code: c for c in chips}
            for p in players:
                assert by_code[p.pos_code].card.note == f"{p.count}회 · {p.rate:.0f}%"
                assert by_code[p.pos_code].card.grade is None   # 강화가 섞여 비운다
            assert not pitch.band.isVisibleTo(pitch), "⑨ 은 합이 뜻이 없어 위 띠가 없다"
            assert _FakeCardInfoLoader.started == [], "⑨ 이 카드 정보를 읽었다"
            assert not _win.fold_position_opp.is_open() and _win.tbl_position_opp.rowCount() == len(players)
    finally:
        _FakeCardInfoLoader.started[:] = keep[0]
        config.NOTICE_ACCEPTED, config.WEB_DATA = keep[1], keep[2]


def test_position_color_search_filters_combo():
    saved = (dict(_win._team_colors), _win.cb_position_color.currentText())
    try:
        _win._team_colors.update({"가": "리버풀", "나": "레알 마드리드", "다": "리옹"})
        _win.ed_position_color.setText("")
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentText("레알 마드리드")
        _win.ed_position_color.setText("리")
        items = [_win.cb_position_color.itemText(i) for i in range(_win.cb_position_color.count())]
        assert "리버풀" in items and "리옹" in items and "레알 마드리드" in items, items   # 고른 것은 걸러도 남는다
        _win.ed_position_color.setText("옹")
        items = [_win.cb_position_color.itemText(i) for i in range(_win.cb_position_color.count())]
        assert "리옹" in items and "리버풀" not in items, items
        assert _win.cb_position_color.currentText() == "레알 마드리드"
    finally:
        _win._team_colors.clear()
        _win._team_colors.update(saved[0])
        _win.ed_position_color.setText("")
        _win.cb_position_color.setCurrentText(saved[1])


def test_search_uses_the_box_that_has_text():
    calls = []
    saved = (_win._api_search, _win.stack.currentIndex(), _win.ed_search.text(),
             [e.text() for e in _win._nick_edits])
    _win._api_search = lambda nick, quiet=False: calls.append(nick)
    try:
        _win.stack.setCurrentIndex(_win.PAGE_SEARCH)
        _win.ed_search.setText("   ")
        _win._on_search()
        assert calls == [] and "입력" in _win.lb_search_msg.text()            # 빈 칸은 조회하지 않는다
        _win.ed_search.setText("  검색칸 ")
        _win._on_search()
        assert calls == ["검색칸"] and _win.lb_search_msg.text() == ""
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        for e in _win._nick_edits:
            e.setText("")
        _win._nick_edits[-1].setText("위쪽칸")
        _win._on_search()
        assert calls[-1] == "위쪽칸", calls                                  # 메인에선 검색 화면 칸을 안 본다
    finally:
        _win._api_search = saved[0]
        _win.stack.setCurrentIndex(saved[1])
        _win.ed_search.setText(saved[2])
        for e, t in zip(_win._nick_edits, saved[3]):
            e.setText(t)


# ── 1.1.1 3단계: 랭킹 수집 앱 연결 ──────────────────────────────────────────

class _RankSwitches:
    """.env(임시)·안내 동의·웹 데이터·수집 스위치를 잡았다 되돌린다. rank.db 는 끝나면 지운다."""

    def __init__(self, web=True, collect=True):
        self.web, self.collect = web, collect

    def __enter__(self):
        self._saved = (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED,
                       {v: os.environ.get(v) for v in (config.WEB_DATA_VAR, config.RANK_COLLECT_VAR)})
        self.write(self.web, self.collect)
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        return self

    def write(self, web, collect):
        config.ENV_PATH.write_text(f"{config.WEB_DATA_VAR}={int(web)}\n{config.RANK_COLLECT_VAR}={int(collect)}\n",
                                   encoding="utf-8")
        config.read_env_switches()

    def __exit__(self, *exc):
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, env = self._saved
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.ENV_PATH.unlink(missing_ok=True)
        rankcollect.delete_db()


def test_rank_list_uses_fresh_snapshot_without_requests():
    def no_request(*a, **k):
        raise AssertionError("하루 안의 스냅숏이 있는데 넥슨에 요청했다")

    with _RankSwitches(), _TeamColorEnv():
        taken = (datetime.now() - timedelta(hours=3)).replace(microsecond=0)
        _rank_snapshot(taken, {"가": "네덜란드", "나": ""})
        orig = ranker.fetch_rank_rows
        ranker.fetch_rank_rows = no_request
        got, done = {}, []
        try:
            ld = app_main.RankListLoader({"가", "나", "밖"})
            ld.loaded_many.connect(got.update)
            ld.finished_all.connect(lambda: done.append(True))
            ld.run()
        finally:
            ranker.fetch_rank_rows = orig
        # 팀컬러 없는 행은 구단가치도 None(팀컬러 표가 '팀가치 모름'으로) · 목록에 없으면 '랭킹 밖'
        assert got == {"가": ("네덜란드", 5001, ""), "나": ("", None, ""), "밖": ("", None, "")}, got
        assert done == [True] and ld.fetched_at == taken, (done, ld.fetched_at)
        # 팀컬러 캐시의 유효기간은 스냅숏 시각부터 센다(지금 시각이면 7일이 최대 8일이 된다)
        _win._teamcolor_loader, _win._teamcolor_pending = ld, ["가", "나"]
        _win._on_teamcolor_loaded(got)   # 실제 길 — 저장은 로더가 준 원값(_teamcolor_raw)에서
        _win._on_teamcolor_finished()
        c = store.open_db(config.DB_PATH)
        try:
            when = {r["nickname"]: r["fetched_at"] for r in c.execute("SELECT nickname, fetched_at FROM team_colors")}
        finally:
            c.close()
        assert when == {"가": taken.isoformat(), "나": taken.isoformat()}, when


def test_rank_list_saves_snapshot_only_when_collect_on():
    pages = {1: [("가", "네덜란드", 1), ("남", "프랑스", 2)], 2: [("나", "", None)]}
    with _RankSwitches(collect=False) as sw:
        ld, got, _ = _run_rank_list({"가"}, pages)
        assert got["가"] == ("네덜란드", 1, "") and ld.saved_snapshot is None
        assert not config.RANK_DB_PATH.exists(), "수집이 꺼졌는데 rank.db 를 만들었다"
        sw.write(True, True)
        ld, got, _ = _run_rank_list({"가"}, pages)
        assert ld.saved_snapshot is not None and ld.saved_snapshot.kind == "ok", ld.saved_snapshot
        assert rankcollect.read_status()["last_rows"] == 3
        # 한 쪽이라도 못 읽었으면 스냅숏을 남기지 않는다(팀컬러는 읽은 만큼 쓴다)
        rankcollect.delete_db()
        pages[2] = None
        ld, got, _ = _run_rank_list({"가"}, pages)
        assert got == {"가": ("네덜란드", 1, "")} and ld.saved_snapshot is None
        assert not rankcollect.read_status().get("snapshots"), "반쪽 목록을 스냅숏으로 남겼다"


def test_rank_list_waits_for_collect_then_uses_its_snapshot():
    def no_request(*a, **k):
        raise AssertionError("수집이 읽는 중인데 같은 목록을 또 읽었다")

    with _RankSwitches():
        got, waited, done = {}, [], []
        ld = app_main.RankListLoader({"가"})
        ld.loaded_many.connect(got.update, Qt.ConnectionType.DirectConnection)
        ld.waiting.connect(lambda: waited.append(True), Qt.ConnectionType.DirectConnection)
        ld.finished_all.connect(lambda: done.append(True), Qt.ConnectionType.DirectConnection)
        orig = ranker.fetch_rank_rows
        ranker.fetch_rank_rows = no_request
        held, _ = rankcollect.acquire_list_read()   # 수집이 목록을 읽고 있다
        assert held
        try:
            t = threading.Thread(target=ld.run)
            t.start()
            time.sleep(0.3)
            assert waited == [True] and t.is_alive(), "수집을 기다리지 않았다"
            _rank_snapshot(datetime.now(), {"가": "브라질"})
        finally:
            rankcollect.release_list_read()
        t.join(5)
        ranker.fetch_rank_rows = orig
        assert got == {"가": ("브라질", 5001, "")} and done == [True], (got, done)


def test_main_loader_records_elo_and_compare_does_not():
    tmp, saved = _loader_db(4)
    gate = threading.Event()
    orig = app_main.MatchLoader._safe_rank
    info = ranker.RankerInfo(nickname="테스트구단주", rank=12, elo=2345.0, profile_sn=77, team_value=5_000_000)
    app_main.MatchLoader._safe_rank = lambda self: (gate.wait(5), info)[1]

    def rows():
        c = store.open_db(config.DB_PATH)
        try:
            return [(r["elo"], r["rank"], r["profile_sn"], r["source"]) for r in store.elo_history(c, _OUID)]
        finally:
            c.close()

    def values():    # 55 V1 — 검색 때 받은 구단가치도 같은 줄에(0 = 못 읽음 → 빈 값)
        c = store.open_db(config.DB_PATH)
        try:
            return [r["team_value"] for r in store.elo_history(c, _OUID)]
        finally:
            c.close()
    try:
        # 비교 로더(기본) — 상대 이력을 쌓지 않는다
        gate.set()
        _run_one(app_main.MatchLoader(_DetailApi([], ""), "닉", 52))
        time.sleep(0.2)
        assert rows() == [], "구단주 비교 로더가 ELO 를 적었다"
        # 메인 검색 — 랭킹이 첫 화면보다 늦게 와도 로더 쪽에서 적는다
        gate.clear()
        _run_one(app_main.MatchLoader(_DetailApi([], ""), "닉", 52, record_elo=True))
        assert rows() == []
        gate.set()
        for _ in range(50):
            if rows():
                break
            time.sleep(0.05)
        assert rows() == [(2345.0, 12, 77, "search")], rows()
        assert values() == [5_000_000], values()
        # 창을 닫는 중에 온 값은 버린다
        from concurrent.futures import Future
        f = Future()
        f.set_result(ranker.RankerInfo(nickname="x", rank=1, elo=9999.0))
        ld = app_main.MatchLoader(_DetailApi([], ""), "닉", 52, record_elo=True)
        ld._cancel = True
        ld._save_elo(_OUID, f)
        ld._cancel = False
        f2 = Future()
        f2.set_result(ranker.RankerInfo(nickname="x"))   # 랭킹 밖 — ELO 없음
        ld._save_elo(_OUID, f2)
        assert rows() == [(2345.0, 12, 77, "search")], rows()
        time.sleep(1.05)          # 같은 초 같은 출처는 DB 가 한 줄로 막는다(ux_elo_src) — 다음 초로
        ld._save_elo(_OUID, f)   # 대조군 — 막는 게 없으면 적힌다
        assert len(rows()) == 2
        assert values()[-1] is None, "구단가치를 못 읽은(0) 줄에 0 을 적었다"
    finally:
        gate.set()
        app_main.MatchLoader._safe_rank = orig
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_rank_collect_scheduler_gates_plans_once_and_rechooses_late_timer():
    clock = _Clock(datetime(2026, 10, 5, 13, 2, 0))
    with _RankSwitches():
        sched = app_main.RankCollectScheduler(now_fn=clock)
        ran = []
        sched.run_now = lambda: ran.append(clock.t)
        try:
            for web, collect, notice_ok in [(False, True, True), (True, False, True), (True, True, False)]:
                config.WEB_DATA, config.RANK_COLLECT = web, collect
                config.NOTICE_ACCEPTED = config.NOTICE_VERSION if notice_ok else config.NOTICE_BASE_VERSION - 1
                sched.check()
                assert sched.planned is None, (web, collect, notice_ok)
            assert not config.RANK_DB_PATH.exists(), "조건이 안 맞는데 rank.db 를 만들었다"
            config.WEB_DATA = config.RANK_COLLECT = True
            config.NOTICE_ACCEPTED = config.NOTICE_VERSION
            sched.check()
            first = sched.planned
            lo, hi = config.RANK_START_JITTER_MIN
            assert first is not None and datetime(2026, 10, 5, 13, lo) <= first <= datetime(2026, 10, 5, 13, hi), first
            assert sched._start_timer.isActive()
            sched.check()
            assert sched.planned == first, "1시간 확인이 겹쳐 예약을 다시 잡았다"
            # 절전 복귀로 늦게 터졌다 — 그 자리에서 시작하지 않고 지금 시각 기준으로 다시 고른다
            clock.t = datetime(2026, 10, 5, 16, 55, 0)
            sched._fire()
            assert ran == [] and sched.planned is not None and sched.planned.hour == 17, (ran, sched.planned)
            clock.t = sched.planned
            sched._fire()
            assert ran == [clock.t] and sched.planned is None
            # 터질 때 꺼져 있으면 안 돈다
            sched.check()
            config.RANK_COLLECT = False
            clock.t = sched.planned
            sched._fire()
            assert len(ran) == 1
            # 간격 안의 스냅숏이 있으면 예약하지 않는다
            config.RANK_COLLECT = True
            _rank_snapshot(clock.t - timedelta(hours=1), {})
            sched.check()
            assert sched.planned is None
            sched.planned = first
            sched.stop()
            assert sched.planned is None and not sched._start_timer.isActive()
        finally:
            sched.shutdown()


class _ShellOnWin:
    """테스트 창(_win)에 진짜 AppShell 을 잠깐 붙인다 — app.quit·창 닫기는 기록만. 끝나면 떼고 상태를 되돌린다."""

    def __init__(self, tray_visible=True):
        import tray as tray_mod
        self.tray_mod = tray_mod
        self.tray_visible = tray_visible

    def __enter__(self):
        import tray as tray_mod

        class _FakeTray:
            def __init__(s, vis):
                s.vis, s.msgs = vis, []

            def isVisible(s):
                return s.vis

            def hide(s):
                s.vis = False

            def showMessage(s, title, *a):
                s.msgs.append(title)

        self.keep = (_win._rank_sched, _win.shell, _win._quitting, _win.__dict__.get("close"))
        self.sh = tray_mod.AppShell(_app, sched=app_main.RankCollectScheduler())
        self.quits = []
        self.sh.app = type("A", (), {"quit": lambda s: self.quits.append(1)})()
        self.sh.attach_window(_win)
        self.sh.tray = _FakeTray(self.tray_visible) if self.tray_visible is not None else None
        self.closed = []
        _win.close = lambda: self.closed.append(_win._quitting)
        return self

    def __exit__(self, *a):
        for t in (self.sh._release_timer, self.sh._update_timer, self.sh._tray_timer):
            t.stop()
        self.sh.sched.shutdown()
        _win._rank_sched, _win.shell, _win._quitting, close = self.keep
        if close is None:
            _win.__dict__.pop("close", None)
        _win.show()
        _app.setQuitOnLastWindowClosed(True)
        return False


def test_shell_starts_and_stops_rank_collect():
    # 수집 예약은 창 밖(AppShell)에 산다 — 창을 숨겨도 돌고, quit_app 이 내린다(남기면 닫는 중에 수집이 터진다)
    with _RankSwitches(collect=False), _ShellOnWin() as ctx:
        sched = ctx.sh.sched
        assert _win._rank_sched is sched, "창이 예약 상태를 못 받는다(상태줄)"
        ctx.sh.start()
        assert sched._check_timer.isActive(), "1시간 확인 타이머를 안 걸었다"
        sched.planned = datetime.now()
        sched._start_timer.start(600000)
        ctx.sh.quit_app()
        assert not sched._check_timer.isActive() and not sched._start_timer.isActive() and sched.planned is None
        assert ctx.quits == [1] and ctx.closed == [True], (ctx.quits, ctx.closed)


def test_x_hides_when_collecting():
    from PySide6.QtGui import QCloseEvent
    keep = config.RANK_COLLECT, config.WEB_DATA
    try:
        with _ShellOnWin() as ctx:
            config.RANK_COLLECT = config.WEB_DATA = True
            e = QCloseEvent()
            _win.closeEvent(e)
            assert not e.isAccepted() and not _win.isVisible(), "수집이 켜져 있는데 X 가 창을 닫았다"
            assert ctx.quits == [] and ctx.sh._release_timer.isActive()
    finally:
        config.RANK_COLLECT, config.WEB_DATA = keep


def test_x_quits_by_default():
    from PySide6.QtGui import QCloseEvent
    keep = config.RANK_COLLECT, config.WEB_DATA, app_main.autostart.is_enabled
    try:
        app_main.autostart.is_enabled = lambda: False
        for collect, web, tray_vis in ((False, True, True), (True, False, True), (True, True, None)):
            config.RANK_COLLECT, config.WEB_DATA = collect, web
            with _ShellOnWin(tray_visible=tray_vis) as ctx:
                saved = []
                keep_save = _win._save_settings
                _win._save_settings = lambda: saved.append(1)
                try:
                    _win.closeEvent(QCloseEvent())
                finally:
                    _win._save_settings = keep_save
                # 트레이가 없거나 수집이 못 도는 상태면 X 는 끝낸다 — 정리(설정 저장)도 quit_app 에서 돈다
                assert ctx.quits == [1] and saved == [1], (collect, web, tray_vis, ctx.quits, saved)
    finally:
        config.RANK_COLLECT, config.WEB_DATA, app_main.autostart.is_enabled = keep


def test_close_event_while_quitting_only_accepts():
    # app.quit() 은 보이는 창의 closeEvent 를 한 번 더 부른다 — 그때 숨기거나 두 번 정리하면 안 된다
    from PySide6.QtGui import QCloseEvent
    with _ShellOnWin():
        _win._quitting = True
        hit = []
        keep_sd = _win.shutdown
        _win.shutdown = lambda fast=False: hit.append(1) or []
        try:
            e = QCloseEvent()
            e.ignore()
            _win.closeEvent(e)
            assert e.isAccepted() and hit == [], hit
        finally:
            _win.shutdown = keep_sd


def test_update_calls_quit_app():
    # [업데이트] 뒤엔 반드시 끝나야 한다 — X 처럼 트레이로 숨으면 설치기가 파일을 못 바꾼다
    keep = updatecheck.launch_installer, config.RANK_COLLECT, config.WEB_DATA
    updatecheck.launch_installer = lambda p: None
    config.RANK_COLLECT = config.WEB_DATA = True  # 숨김 조건이 서 있어도
    try:
        with _ShellOnWin() as ctx:
            _win._on_update_downloaded("setup.exe")
            assert ctx.quits == [1], "업데이트 뒤 quit_app 을 안 불렀다"
    finally:
        updatecheck.launch_installer, config.RANK_COLLECT, config.WEB_DATA = keep


def test_crash_while_hidden_no_modal():
    keep = app_main._SHELL, _win.isVisible()
    with _ShellOnWin() as ctx:
        app_main._SHELL = ctx.sh
        try:
            _win.hide()
            app_main._notify_crash("crash.log")  # 모달이면 ModalCalled
            assert ctx.sh._pending_crash == "crash.log"
            _win.show()
            try:
                app_main._notify_crash("crash.log")
                raise AssertionError("창이 보이는데 안내 창을 안 띄웠다")
            except ModalCalled:
                pass
        finally:
            app_main._SHELL = keep[0]


def test_no_tray_balloon_from_any_path():
    # 2026-10-05 사용자: "알림 자체는 안 보내도록" — 숨김·숨긴 중 오류·새 버전·수집 결과 어느 길로도 풍선이 안 뜬다
    O = rankcollect.Outcome
    with _ShellOnWin() as ctx:
        _win.hide()
        ctx.sh.hide_window()
        app_main._notify_crash("crash.log") if app_main._SHELL is ctx.sh else ctx.sh.crash_while_hidden("crash.log")
        _win._on_update_found(_REL)
        ctx.sh.sched._on_done(O("blocked", "막힘", disabled_by_block=True))
        ctx.sh.sched._on_done(O("failed", "점검", fail_notice=True))
        assert ctx.sh.tray.msgs == [], ctx.sh.tray.msgs
    _reset_version_cards()
    # 코드에 풍선을 띄우는 호출이 다시 들어오면 — 테스트가 그 경로를 안 밟아도 잡히게
    for f in ("tray.py", "app_main.py"):
        src = (pathlib.Path(app_main.__file__).parent / f).read_text(encoding="utf-8")
        assert "showMessage(" not in src.replace("statusBar().showMessage(", ""), f"{f} 에 트레이 풍선 호출이 있다"


def test_release_drops_every_reference():
    import gc
    with _ShellOnWin():
        # 새 dict 로 넘긴다 — 테스트가 쥔 _DETAILS 목록을 창이 그대로 쥐면, 그 목록을 빼는 순간 창의 참조도 안 보인다
        details = [dict(d) for d in _DETAILS]
        matches = list(_MATCHES)
        _win._ouid, _win._season_picked = "", False
        _win._on_loaded(matches, details, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(matches), None, "-", False, "", {}, {})
        _win._narrate_scope()
        _win._shot_sum_key = None
        _win._render_shot_summary(details)   # 슛 맵 요약이 최다 득점·실점 경기 원문을 쥔다(65 M8)
        assert _win.shot_sum is not None and _win.shot_sum.best is not None
        probe = details[0]
        # 끝난 로더가 화면 목록(_prev)을 쥐고 있는 경우까지
        _win._loader = app_main.MatchLoader(_NoApi(), "x", 52, prev=(_OUID, list(matches), list(details)))
        # 끝난 미리 읽기 — discard() 는 멈춤 표시만 하고 결과는 Future 가 계속 쥔다
        from concurrent.futures import Future
        pf = app_main.SavedPrefetch.__new__(app_main.SavedPrefetch)
        pf.ouid, pf.match_type, pf._stop = _OUID, 52, False
        pf._future = Future()
        pf._future.set_result((list(matches), list(details)))
        _win._prefetch = pf
        del pf
        # 구단주 비교 상대(76) — 비교한 상대의 상세 전부를 쥔다(같은 상세 객체로 심어 내려놓기가 놓는지 잰다)
        _win._compare_last = ("상대", list(matches), "opp", list(details), "")
        # 테스트 쪽 참조는 지운다 — 창은 넘긴 목록 객체를 그대로 쥐므로, 그 목록을 '내 것'으로 빼면 창의 참조도 안 보인다
        mine: set = set()
        del matches, details
        # 개수만 — 목록으로 받아 두면 그 목록이 창의 옛 목록을 살려 둬서 '남았다'로 잰다(처음 판이 그랬다)
        before = sum(1 for r in gc.get_referrers(probe) if id(r) not in mine)
        assert before >= 2, f"측정 도구 확인 — 내려놓기 전엔 창(원본)·끝난 로더가 쥐고 있어야 한다: {before}"
        _win._max_division[_OUID] = {"division": 800, "date": "2026-07-05"}
        _win._records()
        _win._rival()
        assert _win._records_cache is not None, "측정 도구 확인 — 업적 캐시가 차 있어야 비웠는지 잴 수 있다"
        assert _win._fun_cache, "측정 도구 확인 — 라이벌 캐시가 차 있어야 비웠는지 잴 수 있다"
        _win.release_memory()
        gc.collect()
        left = [type(r).__name__ for r in gc.get_referrers(probe) if id(r) not in mine]
        try:
            assert left == [], f"내려놓은 뒤에도 경기 기록을 쥐고 있다: {left}"
            assert _win._records_cache is None, "내려놓기가 업적 캐시(옛 목록 키)를 남겼다(88 2회차 [중])"
            assert _win._fun_cache == {}, "내려놓기가 라이벌·오늘의 선수 캐시를 남겼다"
            assert _win.shot_sum is None, "슛 맵 요약이 경기 원문(최다 득점·실점)을 쥐고 있다"
            # 다시 열기는 저장본 경로라 maxdivision 을 다시 안 부른다 — 비우면 줄이 다음 검색까지 사라진다
            assert _win._max_division.get(_OUID), "내려놓기가 역대 최고 등급을 비웠다"
            assert _win._ouid == _OUID, "계정을 비우면 다시 읽을 때 승률 그래프 기간이 초기화된다"
            assert _win._released and _win.busy_for_release() is False
        finally:
            reloaded = []
            keep_start = app_main.MatchLoader.start
            app_main.MatchLoader.start = lambda self: reloaded.append(self._offline_ouid)
            try:
                _win.reload_after_release()
            finally:
                app_main.MatchLoader.start = keep_start
            assert reloaded == [_OUID] and not _win._released, reloaded
            _win._set_busy(False)
            _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                            {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})


def test_release_drops_prefetch_without_search():
    # 1.1.1 exe 실측: 켜고 검색 없이 X → 숨긴 30분 뒤에도 821MB 그대로. 미리 읽기만 쥐고 목록은 비어 있어 내려놓기가 그냥 돌아갔다.
    import gc
    from concurrent.futures import Future
    with _ShellOnWin():
        keep = (_win._ouid, _win._nick)
        _win._matches_all, _win._details_all, _win._matches, _win._details = [], [], [], []
        _win._ouid, _win._released = "", False
        probe = dict(_DETAILS[0])
        pf = app_main.SavedPrefetch.__new__(app_main.SavedPrefetch)
        pf.ouid, pf.match_type, pf._stop = _OUID, 52, False
        pf._future = Future()
        pf._future.set_result(([], [probe]))
        _win._prefetch = pf
        del pf
        assert sum(1 for _ in gc.get_referrers(probe)) >= 1, "측정 도구 확인 — 미리 읽기가 쥐고 있어야 한다"
        _win.release_memory()
        gc.collect()
        left = [type(r).__name__ for r in gc.get_referrers(probe)]
        restarted = []
        keep_start = app_main.MainWindow.start_prefetch
        app_main.MainWindow.start_prefetch = lambda self: restarted.append(True)
        try:
            assert left == [], f"검색 없이 숨겼는데 미리 읽기를 쥐고 있다: {left}"
            assert _win._released and _win._prefetch is None
            _win.reload_after_release()
            assert restarted == [True] and not _win._released, "다시 열 때 미리 읽기를 다시 시작하지 않는다"
        finally:
            app_main.MainWindow.start_prefetch = keep_start
            _win._released = False
            _win._ouid, _win._nick = keep
            _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                            {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
            _win._set_busy(False)


def test_release_is_skipped_while_searching():
    class _Busy:
        def isRunning(self):
            return True
    keep = _win._loader
    _win._loader = _Busy()
    try:
        assert _win.busy_for_release() is True
    finally:
        _win._loader = keep


def test_about_dialog_autostart_toggle_locked_in_source_run():
    dlg = app_main.AboutDialog(_win)
    assert not dlg.chk_auto.isEnabled() and not dlg.chk_auto.isChecked(), "소스 실행에서 자동 실행을 켤 수 있다"
    text = " ".join(t.toPlainText() for t in dlg.findChildren(app_main.QTextBrowser))
    assert "트레이" in text and "[종료]" in text and "6시간" in text, "안내에 트레이 상주·자동 실행·업데이트 주기가 없다"
    dlg.deleteLater()


def test_rank_collect_outcomes_reach_status_bar():
    O = rankcollect.Outcome
    cases = [(O("ok", rows=10000), "10,000명", False),
             (O("blocked", "막힘", disabled_by_block=True), "껐습니다", True),
             (O("failed", "점검", fail_notice=True), "연속 실패", True),
             (O("failed", "점검"), "점검", False),
             (O("offline"), "연결", False)]
    for out, part, important in cases:
        text, imp = app_main.collect_outcome_text(out)
        assert part in text and imp is important, (out, text, imp)
    for kind in ("disabled", "cancelled", "fresh"):
        assert app_main.collect_outcome_text(O(kind)) == ("", False), kind
    sched = app_main.RankCollectScheduler()
    seen = []
    sched.status.connect(lambda t, i: seen.append((t, i)))
    sched._on_done(O("blocked", "막힘", disabled_by_block=True))
    sched._on_done(O("cancelled"))
    assert len(seen) == 1 and seen[0][1] is True, seen
    keep = _win.statusBar().currentMessage()
    try:
        _win._on_rank_collect_status("랭킹 수집 완료 — 3명", False)
        assert _win.statusBar().currentMessage() == "랭킹 수집 완료 — 3명"
    finally:
        _win.statusBar().showMessage(keep)


def test_about_rank_toggle_locked_by_web_data_and_writes_both_copies():
    with _RankSwitches(web=False, collect=False):
        dlg = app_main.AboutDialog()
        assert not dlg.chk_rank.isEnabled() and not dlg.chk_rank.isChecked(), "웹 데이터가 꺼졌는데 수집을 켤 수 있다"
        dlg.chk_web.setChecked(True)
        assert dlg.chk_rank.isEnabled() and not dlg.chk_rank.isChecked(), "웹 데이터를 켰더니 수집이 같이 켜졌다"
        dlg.chk_rank.setChecked(True)
        assert config.RANK_COLLECT and "FIFA_RANK_COLLECT=1" in config.ENV_PATH.read_text(encoding="utf-8")
        assert rankcollect.read_status()["enabled"] == "1", "rank.db 사본을 안 켰다 — D6 로 꺼진 사본이 계속 막는다"
        dlg.chk_web.setChecked(False)
        assert not dlg.chk_rank.isChecked() and not dlg.chk_rank.isEnabled()
        assert config.read_env_switches() == (False, False)
    # D6 로 꺼진 사본 — .env 는 켜져 있어도 꺼진 것으로 보여야 한다
    with _RankSwitches(web=True, collect=True):
        c = rankcollect.open_rank_db()
        rankcollect.set_enabled(c, False, "blocked")
        c.close()
        dlg = app_main.AboutDialog()
        assert not dlg.chk_rank.isChecked() and "스스로 껐습니다" in dlg.lb_rank.text(), dlg.lb_rank.text()


def test_clear_rank_records_stops_turns_off_and_deletes():
    tmp, saved = _loader_db(0)
    stops = []

    class _Sched:
        def stop(self):
            stops.append(config.RANK_COLLECT)   # 끈 다음에 멈춰야 다음 확인이 다시 모으지 않는다

    try:
        with _RankSwitches():
            _rank_snapshot(datetime.now(), {})
            c = store.open_db(config.DB_PATH)
            store.save_elo(c, "나", 2000.0, 5)
            store.save_elo(c, "남", 1900.0, 9)
            c.close()
            # 취소하면 아무것도 안 지운다
            dlg = app_main.AboutDialog()
            dlg._ask_clear = lambda keep: None
            dlg._on_clear_rank()
            assert config.RANK_DB_PATH.exists() and config.RANK_COLLECT
            # .env 를 못 쓰면(수집을 못 끄면) 지우지 않는다 — 켜진 채 지우면 곧바로 다시 모은다
            orig = config.set_rank_collect

            def fail(on):
                raise PermissionError("다른 실행본")
            config.set_rank_collect = fail
            try:
                dlg._ask_clear = lambda keep: "all"
                dlg._on_clear_rank()
            finally:
                config.set_rank_collect = orig
            assert config.RANK_DB_PATH.exists() and "지우지 않았습니다" in dlg.lb_msg.text(), dlg.lb_msg.text()
            done, n = app_main.clear_rank_records(_Sched(), keep_ouid="나")
            assert done and n == 1 and stops == [False], (done, n, stops)
            assert not config.RANK_DB_PATH.exists() and "FIFA_RANK_COLLECT=0" in config.ENV_PATH.read_text(
                encoding="utf-8")
            c = store.open_db(config.DB_PATH)
            assert [r["elo"] for r in store.elo_history(c, "나")] == [2000.0] and store.elo_history(c, "남") == []
            c.close()
    finally:
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _notice_all_text() -> str:
    return (notice.TERMS_HTML + notice.PRIVACY_HTML + notice.WEB_DATA_HTML + notice.RANK_COLLECT_HTML
            + notice.TRAY_HTML + "".join(notice.CHANGES.values()))


def test_storage_tab_replaces_clear_button_and_clears_cards():
    """57 K9 — [수집 기록 지우기] 는 [정보 › 저장 공간] 으로 옮겼다(옛 자리 글이 남으면 안내가 틀린다) · 크기 · 카드 정보 지우기."""
    tmp, saved = _loader_db(0)
    run = app_main.ClearWorker.start
    try:
        c = store.open_db(config.DB_PATH)
        c.execute("INSERT INTO card_tries (key, day, fails) VALUES ('p:1', '2026-10-20', 0)")
        c.commit()
        c.close()
        dlg = app_main.AboutDialog(_win)
        tabs = dlg.findChild(QTabWidget)
        assert "저장 공간" in [tabs.tabText(i) for i in range(tabs.count())]
        assert set(dlg._storage_btn) == set(app_main.storage.KINDS)
        assert dlg.btn_clear_rank is dlg._storage_btn["rank"], "수집 기록 지우기가 저장 공간 탭에 없다"
        assert "[정보] 의 <b>수집 기록 지우기" not in _notice_all_text() and "[정보] 의 수집 기록" not in _notice_all_text()
        size = _win._size_workers[-1]          # 스모크는 start 를 막는다 — 띄운 것을 직접 돌린다
        size.run()
        assert dlg._storage_size["cards"].text() not in ("…", "?"), dlg._storage_size["cards"].text()
        dlg._ask_storage = lambda kind: True
        app_main.ClearWorker.start = lambda self: self.run()
        dlg._on_clear_kind("cards")
        assert "지웠습니다" in dlg.lb_storage.text(), dlg.lb_storage.text()
        assert not app_main.CLEARING, "끝났는데 걸쇠가 남았다 — 그 저장소에 쓰는 로더가 영영 못 뜬다"
        again = _win._size_workers[-1]
        assert again is not size, "지운 뒤 크기를 다시 안 쟀다 — 지우기 전 숫자가 남는다"
        again.run()
        assert dlg._storage_size["cards"].text() == app_main._size_text(0), dlg._storage_size["cards"].text()
        c = store.open_db(config.DB_PATH)
        assert c.execute("SELECT COUNT(*) FROM card_tries").fetchone()[0] == 0
        c.close()
        dlg.close()
    finally:
        app_main.ClearWorker.start = run
        app_main.CLEARING.clear()
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_storage_clear_all_wipes_and_resets_window():
    """57 K9 ⑤ — fifa.db 바꿔 끼우기 · rank.db · .cache 전부 · 창이 들고 있던 계정을 놓고 검색 화면으로. 검색 중이면 거절."""
    tmp, saved = _loader_db(len(_DETAILS))
    keep_cache, run = config.CACHE_DIR, app_main.ClearWorker.start
    w = None
    try:
        config.CACHE_DIR = tmp / ".cache"
        (config.CACHE_DIR / "icons").mkdir(parents=True)
        (config.CACHE_DIR / "icons" / "a.png").write_bytes(b"x")
        with _RankSwitches():
            _rank_snapshot(datetime.now(), {})
            w = app_main.MainWindow(_NoApi())
            w.LAZY_RENDER = False
            w._on_fetch_team_colors = lambda: None
            w._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                         {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
            assert w._matches_all and w._ouid

            class _Busy:
                def isRunning(self):
                    return True
            w._loader = _Busy()
            assert "검색" in (w.clear_storage("all") or ""), "검색 중에 내 기록 전부를 지우기 시작했다"
            assert not app_main.CLEARING and config.RANK_DB_PATH.exists()
            w._loader = None
            seen = []
            w.storage_cleared.connect(seen.append)
            app_main.ClearWorker.start = lambda self: self.run()
            assert w.clear_storage("all") is None
            assert seen and seen[0].state == "ok", seen
            assert not app_main.CLEARING
            assert w._ouid == "" and not w._matches_all and w.stack.currentIndex() == w.PAGE_SEARCH
            assert not config.RANK_DB_PATH.exists() and not config.RANK_COLLECT
            assert not list(config.CACHE_DIR.iterdir())
            c = store.open_db(config.DB_PATH)
            assert c.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
            assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
            c.close()
    finally:
        app_main.ClearWorker.start = run
        app_main.CLEARING.clear()
        config.CACHE_DIR = keep_cache
        if w is not None:
            w.shutdown()
            w.deleteLater()
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_storage_latch_holds_scheduler_ticks():
    """보호 장치 — 걸쇠가 없으면 틱이 돈다(먼저 단언) · 지우는 중엔 화면 스레드의 fifa.db 열기·로더 띄우기를 미룬다."""
    for cls in (app_main.OwnerDistScheduler, app_main.CardBackfillScheduler):
        s = cls()
        calls = []
        s.sync = lambda start=False, c=calls: c.append(start)
        s.tick()
        assert calls == [True], (cls.__name__, "걸쇠 없이도 틱이 안 돈다 — 아래 단언이 비었다")
        app_main.CLEARING.add("all")
        try:
            s.tick()
        finally:
            app_main.CLEARING.clear()
        assert calls == [True], (cls.__name__, "지우는 중에 틱이 돌았다")


def test_clearing_does_not_lock_search_buttons():
    """57 ③ 리뷰 — 지우기는 창을 닫아도 돈다. 그 사이 검색·비교·팀컬러를 누르면 로더 start 가 걸쇠에 막혀 끝 신호가 안 오고,
    먼저 꺼 둔 버튼이 영영 안 풀렸다. 화면을 바꾸기 전에 묻고 글로 돌려보낸다."""
    probe = app_main.MatchLoader(_NoApi(), "x", config.DEFAULT_MATCH_TYPE)
    app_main.CLEARING.add(app_main.STORE_DETAILS)
    try:
        probe.start()
        assert probe.blocked, "걸쇠가 검색 로더를 안 막는다 — 아래 단언이 비었다"
        saved = (_win._loader, _win._compare_loader, _win._teamcolor_loader, config.WEB_DATA,
                 dict(_win._team_colors), [lb.text() for lb in _win._teamcolor_status_labels], _win._released)
        try:
            _win._loader = _win._compare_loader = _win._teamcolor_loader = None
            _win._api_search("지우는중검색")
            assert _win.ed_search.isEnabled() and not _win.progress.isVisible(), "지우는 중 검색이 버튼을 잠갔다"
            assert _win.lb_search_msg.text() == app_main.CLEARING_WAIT_MSG
            _win.ed_compare_nick.setText("비교상대")
            _win._on_compare_search()
            assert _win.btn_compare.isEnabled() and _win._compare_loader is None, "지우는 중 비교가 버튼을 잠갔다"
            # 팀컬러 로더는 fifa.db 에만 쓴다 — 상세 캐시를 지우는 동안엔 원래 띄운다. 막히는 것은 내 기록 전부("all")
            app_main.CLEARING.add("all")
            config.WEB_DATA = True
            _win._team_colors.clear()
            _win._load_cached_team_colors = lambda _opps: None
            app_main.MainWindow._on_fetch_team_colors(_win)
            assert _win._teamcolor_loader is None and all(b.isEnabled() for b in _win._teamcolor_fetch_btns), \
                ("지우는 중 팀컬러 조회가 버튼을 잠갔다", _win._teamcolor_loader)
            _win._released = True
            _win.reload_after_release()
            assert _win._released and _win._loader is None, "지우는 중 다시 읽기를 띄웠다(막혀 영영 '다시 읽는 중')"
        finally:
            (_win._loader, _win._compare_loader, _win._teamcolor_loader, config.WEB_DATA) = saved[:4]
            _win._team_colors.clear()
            _win._team_colors.update(saved[4])
            _win.__dict__.pop("_load_cached_team_colors", None)   # 인스턴스에 덮은 것만 지운다(클래스 함수로 돌아간다)
            _win._released = saved[6]
            _win.ed_compare_nick.setText("")
            _win.lb_search_msg.setText("")
            for lb, t in zip(_win._teamcolor_status_labels, saved[5]):
                lb.setText(t)
    finally:
        app_main.CLEARING.clear()


def test_my_traits_row_above_trait_table():
    """57 K12 — 관문(특성 + 안내 14)·내 계정(키 지문)일 때만 표 · 관문이 꺼지면 지우는 자리는 purge_ranker_pick_data."""
    keep_key = config.API_KEY
    c = store.open_db(config.DB_PATH)
    keep_ts = store.trade_state(c)
    c.close()
    try:
        with _RankSwitches():
            config.API_KEY = "test_key"
            c = store.open_db(config.DB_PATH)
            store.set_trade_state(c, key_fp=store.key_fingerprint("test_key"), my_ouid=_OUID)
            body = [[101 + i, 1 + i, 5, [28, 55, -2], [2, 12, 42] if i else None] for i in range(11)]
            store.save_my_traits(c, _OUID, sn=1, match_id="m", team="1-0", state="ok", body=body, fetched_at="2026-10-20T09:00:00")
            c.close()
            _win._render_my_traits()
            t = _win.tbl_my_traits
            assert t.rowCount() == 11 and not t.isHidden(), t.rowCount()
            assert t.item(0, 5).text() == "-", "잠긴 훈련 칸에 이름을 붙였다"
            assert _win.lb_my_trait.text().endswith("받은 시각 10-20 09:00"), _win.lb_my_trait.text()   # 96 W1 — 맨 오른쪽
            config.NOTICE_ACCEPTED = config.MY_TRAIT_NOTICE_VERSION - 1
            assert not config.my_trait_allowed() and config.trait_allowed()
            _win._render_my_traits()
            assert t.rowCount() == 0 and t.isHidden() and "새 이용 안내" in _win.lb_my_trait.text()
            _win.purge_ranker_pick_data(everything=False)
            c = store.open_db(config.DB_PATH)
            assert store.my_traits(c, _OUID) is None, "관문이 꺼졌는데 내 특성 줄이 남았다"
            c.close()
    finally:
        config.API_KEY = keep_key
        c = store.open_db(config.DB_PATH)
        c.execute("DELETE FROM trade_state")
        c.commit()
        if keep_ts:
            store.set_trade_state(c, **keep_ts)
        c.close()


def test_notice_dialog_says_web_data_is_on_now_and_mentions_collection():
    keep = config.WEB_DATA
    try:
        for on in (True, False):
            config.WEB_DATA = on
            dlg = app_main.NoticeDialog()
            assert not dlg.chk_web.isChecked(), "체크는 매번 비워 둔다(고지 없이 켜진 채 남지 않게)"
            assert dlg.lb_web_now.isVisibleTo(dlg) is on and (dlg.lb_web_now.text() == notice.WEB_DATA_ON_NOW) is on
            text = dlg.findChild(app_main.QTextBrowser).toPlainText()
            assert "하루 한 번" in text and "14일" in text and "ELO" in text, "안내에 랭킹 수집·ELO 기록이 없다"
    finally:
        config.WEB_DATA = keep
    assert config.NOTICE_VERSION >= 2, "안내 글이 바뀌었는데 NOTICE_VERSION 을 안 올렸다 — 동의한 사람이 다시 안 본다"


def _discipline_details():
    """골대 3경기 · 내 퇴장 1경기 · 상대 퇴장 2경기 — 규율·불운 막대 표본이 셋 다 다르게."""
    def g(i, res, post=False, my_red=0, opp_red=0):
        opp_res = {"승": "패", "패": "승"}.get(res, res)
        return {"matchId": f"disc{i}", "matchInfo": [
            {"ouid": _win._ouid, "matchDetail": {"matchResult": res, "matchEndType": 0, "redCards": my_red},
             "shootDetail": [{"hitPost": True}] if post else []},
            {"ouid": "상대", "matchDetail": {"matchResult": opp_res, "matchEndType": 0, "redCards": opp_red}}]}
    return [g(1, "승", post=True), g(2, "패", post=True), g(3, "무", post=True, my_red=1),
            g(4, "승", opp_red=1), g(5, "패", opp_red=1)]


def test_discipline_section_shows_averages_and_end_kinds():
    _win._render_discipline(_discipline_details())
    from PySide6.QtWidgets import QLabel
    text = " ".join(lb.text() for i in range(_win.box_diag_discipline.count())
                    if (w := _win.box_diag_discipline.itemAt(i).widget())
                    for lb in [w, *w.findChildren(QLabel)] if isinstance(lb, QLabel))
    assert "골대 나 0.60 상대 0.00" in text, text             # 골대 3 / 5경기
    assert "퇴장 나 1회 상대 2회" in text, text                 # 퇴장은 평균이 0.00 으로만 보여 횟수로
    assert "종료 유형: 정상 5 · 몰수승 0 · 몰수패 0 · 중단 0" in text, text
    _win._render_all()


def _weak_sites():
    """표본 흐림 규칙의 진입점(ROADMAP 1.2.1 표) — 이름 → (그리기, 위젯에서 (경기 수, 흐림) 목록 뽑기, 기준 상수 이름)."""
    from PySide6.QtWidgets import QLabel
    from widgets import WdlBar

    def bars(*boxes, kind=WdlBar):
        out = []
        for box in boxes:
            for i in range(box.count()):
                w = box.itemAt(i).widget()
                if w is None:
                    continue
                for x in [w, *w.findChildren(kind)]:
                    if isinstance(x, kind) and x.property("weak") is not None:
                        out.append((x.property("games"), bool(x.property("weak"))))
        return out

    def hbar(chart, games_of):
        return [(games_of(r), bool(r[4]) if len(r) > 4 else False) for r in chart._rows if r[1] is not None]

    def rate_rows(*lists):   # 65 P4 줄 목록 — 줄마다 (분모, 흐림) · 상자를 받으면 그 안의 목록 전부
        from widgets import RateRowList
        out = []
        for x in lists:
            found = [x] if isinstance(x, RateRowList) else [
                w for i in range(x.count()) if isinstance(w := x.itemAt(i).widget(), RateRowList)]
            for lst in found:
                out += [(r.rated, r.weak) for r in lst.all_rows()]
        return out

    # 판정 분모는 rated(A6) — 섞인 픽스처라 무승부는 표본이 아니다
    tod = {b.label: b.rated for b in app_main.core.time_of_day_rates(_win._matches)}
    opp = {o.nickname: o.rated for o in app_main.core.opponent_stats(_win._matches)}
    diag = lambda: _win._render_diagnosis(_win._details)  # noqa: E731
    clutch = lambda: _win._render_clutch(_win._details, _win._matches)  # noqa: E731
    # 픽스처엔 골대·퇴장 경기가 없어 막대가 안 생긴다 — 표본이 3·1·2 로 갈리게 만든 경기로 그린다
    disc = lambda: _win._render_discipline(_discipline_details())  # noqa: E731
    return {
        "성적 진단": (diag, lambda: rate_rows(_win.box_diag_my_division, _win.box_diag_division, _win.box_diag_possession),
                   "MIN_COND"),
        # 65 M4~M6 — 상대 전적 · 전술 상대 포메이션 · 경기 결과 줄
        "상대 전적": (lambda: _win._render_opponents(_win._matches), lambda: rate_rows(_win.opp_list), "MIN_COND"),
        "전술 상대 포메이션": (lambda: _win._render_tactics(_win._details),
                       lambda: rate_rows(_win.opp_formation_list), "MIN_COND"),
        "경기 결과": (lambda: _win._render_tactics(_win._details), lambda: rate_rows(_win.result_list), "MIN_COND"),
        "성적 진단 규율": (disc, lambda: bars(_win.box_diag_discipline), "MIN_COND"),
        # 흐름 분석 메뉴 그리기(_render_analysis)를 거쳐 재야 그 구역이 실제로 불리는지도 잰다
        "흐름 분석 연승": (_win._render_analysis, lambda: bars(_win.box_streak), "streak_min_n"),
        "승부처 시간대": (clutch, lambda: bars(_win.box_clutch_tod), "MIN_COND"),
        "승부처 리드별": (lambda: _win._render_goal_flow(_win._details), lambda: bars(_win.box_goalflow["lead"]),
                    "MIN_COND"),
        "승부처 선제골": (clutch, lambda: bars(_win.box_clutch_first, kind=QLabel), "MIN_COND"),
        "대시보드 시간대": (_win._render_dashboard,
                       lambda: hbar(_win.dashboard.timeband_bars, lambda r: tod[r[0]]), "MIN_COND"),
        "대시보드 라이벌": (_win._render_dashboard,
                       lambda: hbar(_win.dashboard.rival_bars, lambda r: opp[r[0]]), "MIN_OPP"),
    }


def test_winrate_bars_dim_small_samples():
    # 픽스처는 4경기라 기본 기준(8)에선 전부 흐리다 — 칸마다 기준을 '가장 작은 표본'과 '그보다 1 큰 값'으로
    # 바꿔 그린다. 앞에선 아무것도 안 흐려야 하고(<= 로 바꾸면 FAIL), 뒤에선 가장 작은 칸만 흐려야 한다.
    # 기본값이 아닌 기준으로 재므로 화면이 상수를 안 읽고 숫자를 박아도 FAIL 한다.
    import core_api
    names = ("MIN_COND", "MIN_OPP", "streak_min_n")
    saved = {n: getattr(core_api, n) for n in names}

    def put(const, t):  # 기준이 함수인 칸(연승 구역 — 전체 규모를 따라간다)은 그 값을 돌려주는 함수로
        setattr(core_api, const, (lambda _total, t=t: t) if callable(saved[const]) else t)
    try:
        for site, (render, read, const) in _weak_sites().items():
            put(const, 1)
            render()
            got = read()
            assert got, (site, "막대가 하나도 없다 — 이 칸은 재지 못한다")
            low = min(g for g, _ in got)
            for t in (low, low + 1):
                put(const, t)
                render()
                got = read()
                want = [(g, g < t) for g, _ in got]
                assert got == want, (site, t, got)
            assert any(w for _, w in got) and (len({g for g, _ in got}) == 1 or not all(w for _, w in got)), \
                (site, "흐린 칸과 안 흐린 칸을 둘 다 못 봤다", got)
            for n, v in saved.items():
                setattr(core_api, n, v)
    finally:
        for n, v in saved.items():
            setattr(core_api, n, v)
        _win._render_all()


def test_winrate_bar_sample_text_uses_rated():
    import widgets
    row = widgets.wdl_rate_bar(3, 10, 2, "x", 8, True)
    assert row.bar.property("weak") and row.bar.property("games") == 5, row.bar.property("games")
    assert row.bar.counts() == (3, 10, 2) and row.bar.strip.weak and row.bar.strip.compact
    assert row.side.text() == "승률 60.0%*  x  · 승패 5판", row.side.text()   # 3/5 — 한 집단 식이면 3/15 = 20%
    assert "승패 5판" in row.toolTip() and "승÷(승+패)" in row.toolTip(), row.toolTip()
    single = widgets.wdl_rate_bar(3, 10, 2, "", 8, False)
    assert not single.bar.property("weak") and single.bar.property("games") == 15 and not single.bar.strip.weak
    assert single.side.text() == "승률 20.0%", single.side.text()
    assert widgets.win_rate_text(3, 10, 2, True) == "60.0% · 승÷(승+패) · 무 10"
    assert widgets.win_rate_text(3, 10, 2, False) == "20.0% · 승÷전체 · 무 10"
    assert widgets.win_rate_text(3, 10, 2, True, digits=0, short=True) == "60%*"
    assert widgets.win_rate_text(3, 10, 2, False, short=True) == "20.0%"
    # 무승부뿐인 섞인 줄 — 분모 0 이라 "—"(0.0% 면 다 진 것처럼 읽힌다) · 한 집단이면 진짜 0%
    assert widgets.win_rate_text(0, 1, 0, True, short=True) == "—"
    assert widgets.win_rate_text(0, 1, 0, True) == "승률 없음 · 승÷(승+패) · 무 1"
    assert widgets.win_rate_text(0, 1, 0, False, short=True) == "0.0%"


def test_key_number_html_colors_numbers_only():
    import widgets
    h = widgets.key_number_html("선제골 <b>승률 78.5% · 기준 +3.2%p · 1,204경기 <b>", "#abcdef")
    nums = [x.split("</span>")[0] for x in h.split("<span style='color:#abcdef'>")[1:]]
    assert nums == ["78.5%", "+3.2%p", "1,204"], (nums, h)
    assert h.count("&lt;b&gt;") == 2 and "<b>" not in h, "글을 이스케이프하지 않았다(숫자 앞·뒤 둘 다)"
    assert widgets.key_number_html("숫자 없음", "#abcdef") == f"<span style='color:{T.TEXT}'>숫자 없음</span>"


def test_winrate_bars_use_shared_helper():
    # 승률 막대는 전부 widgets.wdl_rate_bar 를 거친다 — 직접 QProgressBar() 를 만들면 표본 흐림 규칙을 건너뛴다.
    # 허용은 변수 이름으로(줄 번호면 코드를 지울 때마다 깨진다): 상태 진행 막대 · 승부처 분 단위 득·실 개수 막대.
    src = (pathlib.Path(_ROOT) / "app_main.py").read_text(encoding="utf-8")
    made = re.findall(r"^\s*([\w.]+)\s*=\s*QProgressBar\(\)", src, re.M)
    assert sorted(made) == ["gbar", "rbar", "self.progress"], made
    # 대입 없이 바로 넣는 것(addWidget(QProgressBar()))도 센다 — 대입만 세던 첫 판은 그 변이를 못 잡았다
    assert len(re.findall(r"QProgressBar\(", src)) == len(made), re.findall(r".*QProgressBar\(.*", src)
    assert "QProgressBar()" not in (pathlib.Path(_ROOT) / "dashboard.py").read_text(encoding="utf-8")


def test_heat_colors_keep_text_readable():
    # 선수 지표 칸은 글자 위에 칠한다 — 두 팔레트 × 공격/수비 × 11단계 전부 TEXT 대비 4.5 이상. 원색 빨강을 끝 색으로
    # 쓰면(예전) 어두운 테마에서 밝은 글자가 안 읽힌다. 포지션 글자는 PANEL 위 4.5 이상. 흐린 승률 막대 위 글자(TEXT)도.
    f = app_main.MainWindow.HEAT_FLOOR
    bad = []
    for mode, p in T._PALETTES.items():
        for key in ("HEAT_GOOD", "HEAT_BAD"):
            for s in range(11):
                bg = T.blend(p["PANEL"], p[key], f + (1 - f) * s / 10)
                if T.contrast(p["TEXT"], bg) < 4.5:
                    bad.append((mode, key, s, bg))
        for key in ("POS_FW", "POS_MF", "POS_DF", "POS_GK"):   # 95 — 게임 값 그대로(사용자 결정) · 글자가 같이 있어 2.5
            if T.contrast(p[key], p["PANEL"]) < POS_MIN_CONTRAST:
                bad.append((mode, key))
        if T.contrast(p["TEXT"], T.blend(p["PANEL_2"], p["WIN_BAR"], T.WEAK_MIX)) < 4.5:
            bad.append((mode, "흐린 막대 글자"))
    assert not bad, bad
    # 화면이 실제로 그 끝 색을 쓰는지(배선) — 기본 BAD/BLUE 가 아니다
    assert (T.HEAT_GOOD, T.HEAT_BAD) == (T._P["HEAT_GOOD"], T._P["HEAT_BAD"]) and T.HEAT_BAD != T.BAD
    # 3.7.2 — 높음은 초록 쪽(G 가 가장 큼) · 낮음은 빨강 쪽(95 의 파랑으로 되돌리면 FAIL)
    for p in T._PALETTES.values():
        g, b = QColor(p["HEAT_GOOD"]), QColor(p["HEAT_BAD"])
        assert g.green() > max(g.red(), g.blue()) and b.red() > max(b.green(), b.blue()), (p["HEAT_GOOD"], p["HEAT_BAD"])


POS_MIN_CONTRAST = 2.5   # 95 — 포지션 글자·칩은 게임 값 그대로(어두운 PANEL_2 위 FW 2.73 이 가장 낮다)


def _palette_contrast_failures(p: dict) -> list:
    """한 팔레트에서 글자/바탕 짝마다 대비 4.5(포지션 칩은 POS_MIN_CONTRAST) 미만인 것 — 테스트와 '심은 팔레트' 검사가 같은 표를 쓴다."""
    pairs = []
    for key in ("TEXT", "TEXT_DIM", "ACCENT", "GOOD", "BAD", "WARN", "ERROR", "LEVEL"):   # 글자 토큰 × 카드·칸
        pairs += [(key, p[key], base, p[base]) for base in ("PANEL", "PANEL_2")]
    for key in ("GOOD", "BAD", "WARN", "TEXT_DIM", "ACCENT"):                # 태그 꼴 — 그 색 글자 + 그 색 10% 바탕
        pairs.append((key, p[key], "10% 바탕", T.blend(p["PANEL"], p[key], 0.10)))
    chips = []
    for key in ("CHIP_GK", "CHIP_DF", "CHIP_MF", "CHIP_FW"):                 # 포지션 칩(태그 꼴) — 카드 위 · 칩 바탕 위
        chips += [(key, p[key], base, p[base]) for base in ("PANEL", "PANEL_2")]
        chips.append((key, p[key], "칩 바탕", T.blend(p["PANEL"], p[key], T.CHIP_SOFT_MIX)))
    pairs += [("ON_PRIMARY", p["ON_PRIMARY"], k, p[k]) for k in ("PRIMARY", "PRIMARY_HOVER")]
    pairs += [(k, p[k], "ACCENT_SOFT", p["ACCENT_SOFT"]) for k in ("ACCENT", "TEXT")]
    for key in ("TEXT", "TEXT_DIM", "GOOD", "BAD"):                           # 지표 줄 바탕 채움(67 R0) 위 글자
        pairs.append((key, p[key], "줄 채움", T.blend(p["PANEL"], p["ACCENT_FILL"], T.ROW_FILL_MIX)))
    for bar in ("WIN_BAR", "LOSE_BAR"):                                       # 위에 글자를 겹치는 막대 — 꽉 찬 것·흐린 것
        pairs.append(("TEXT", p["TEXT"], bar, p[bar]))
        pairs.append(("TEXT", p["TEXT"], f"흐린 {bar}", T.blend(p["PANEL_2"], p[bar], T.WEAK_MIX)))
    return ([(fg_name, bg_name, round(T.contrast(fg, bg), 2)) for fg_name, fg, bg_name, bg in pairs
             if T.contrast(fg, bg) < 4.5]
            + [(fg_name, bg_name, round(T.contrast(fg, bg), 2)) for fg_name, fg, bg_name, bg in chips
               if T.contrast(fg, bg) < POS_MIN_CONTRAST])


def test_palette_text_contrast():
    # 강조·좋음·나쁨·주의 글자는 카드(PANEL)와 칸(PANEL_2) 둘 다 위에서 읽혀야 한다 — 밝은 테마의 채움 값
    # (#009966 등)을 글자로 쓰면 3점대라, 글자 토큰과 *_FILL 을 나눴다. 두 팔레트 전부.
    bad = {mode: _palette_contrast_failures(p) for mode, p in T._PALETTES.items()}
    assert not any(bad.values()), bad
    # 검사가 실제로 무언가를 재는지 — 밝은 쪽 GOOD 을 채움 값으로 되돌리면 걸려야 한다
    planted = dict(T._PALETTES["light"], GOOD=T._PALETTES["light"]["GOOD_FILL"])
    assert ("GOOD", "PANEL", 3.65) in _palette_contrast_failures(planted), _palette_contrast_failures(planted)
    planted = dict(T._PALETTES["dark"], WIN_BAR=T._PALETTES["dark"]["GOOD_FILL"])
    assert any(b == "WIN_BAR" for _, b, _ in _palette_contrast_failures(planted))
    # 줄 채움 행도 잰다 — 섞는 비율을 0.22 로 올리면 밝은 쪽 흐린 글자가 4.34 로 걸려야 한다(67 R0 · 3.2.2 초록 채움에서 0.18 은 4.55 라 안 걸린다)
    keep = T.ROW_FILL_MIX
    try:
        T.ROW_FILL_MIX = 0.22
        assert ("TEXT_DIM", "줄 채움", 4.34) in _palette_contrast_failures(T._PALETTES["light"])
    finally:
        T.ROW_FILL_MIX = keep
    # 칩 행도 잰다 — 밝은 쪽 GK 칩을 게임 노랑(#f4b400 · 흰 바탕 1.85)으로 바꾸면 걸려야 한다
    planted = dict(T._PALETTES["light"], CHIP_GK="#f4b400")
    assert any(f == "CHIP_GK" for f, _, _ in _palette_contrast_failures(planted)), _palette_contrast_failures(planted)


def test_palette_tokens_reach_widgets():
    # 모듈 이름이 MODE 팔레트 값을 그대로 싣는지(배선) + 승/패는 좋음/나쁨 · 강조는 그와 다른 색
    for key in ("ACCENT", "ACCENT_FILL", "ACCENT_SOFT", "PRIMARY", "PRIMARY_HOVER", "ON_PRIMARY",
                "GOOD", "BAD", "WARN", "GOOD_FILL", "BAD_FILL", "WARN_FILL", "CHIP_GK", "CHIP_DF", "CHIP_MF", "CHIP_FW",
                "FORMATION_PITCH_TOP", "FORMATION_PITCH_BOTTOM", "FORMATION_LINE", "CHART_LINE", "CAL_FILL"):
        assert getattr(T, key) == T._P[key], key
    for key in ("ERROR", "LEVEL", "LOSE_FILL", "DRAW_FILL"):
        assert getattr(T, key) == T._P[key], key
    # 3.7.2 사용자 — 승은 좋음(초록) · 패는 나쁨(빨강)과 같은 색 · 패 칸도 빨강(95 의 진회색 아님) · 무는 회색
    assert T.WIN == T.GOOD and T.LOSE == T.BAD and T.LOSE_FILL == T.BAD_FILL
    for p in T._PALETTES.values():
        for key in ("BAD", "BAD_FILL", "LOSE_FILL", "CHART_DOWN", "HEAT_BAD"):   # 빨강 쪽 — 파랑·회색으로 되돌리면 FAIL
            c = QColor(p[key])
            assert c.red() > max(c.green(), c.blue()) + 40, (key, p[key])
        d = QColor(p["DRAW_FILL"])
        assert max(d.red(), d.green(), d.blue()) - min(d.red(), d.green(), d.blue()) < 16, ("무는 회색", p["DRAW_FILL"])
    assert T.ACCENT not in (T.GOOD, T.BAD)
    # 꽉 칠한 버튼은 PRIMARY 로 그려진다 — 찍어서 본다
    from PySide6.QtWidgets import QPushButton
    b = QPushButton("확인")
    b.setObjectName("primary")
    b.resize(120, 40)
    img = b.grab().toImage()
    assert img.pixelColor(10, 20).name() == T.PRIMARY, (img.pixelColor(10, 20).name(), T.PRIMARY)


DARK_UPDOWN_CVD, LIGHT_UPDOWN_CVD = 17.1, 6.9   # 3.7.2 — 게임 초록·빨강(어두운) · 95 전 짝(밝은) · 95 의 초록·파랑은 36.1 · 25.4


def test_cvd_delta_matches_known_values():
    # 그래프 색을 고를 때 쓴 검증기 값(theme 의 CHART_* 주석)과 같아야 이 도구로 새 색을 잰다
    assert round(T.cvd_delta("#3fb950", "#ef6268"), 1) == 2.0           # 옛 앱 초록/빨강
    assert round(T.cvd_delta(*[T._PALETTES["dark"][k] for k in ("CHART_UP", "CHART_DOWN")]), 1) == DARK_UPDOWN_CVD
    assert round(T.cvd_delta(*[T._PALETTES["light"][k] for k in ("CHART_UP", "CHART_DOWN")]), 1) == LIGHT_UPDOWN_CVD
    assert T.cvd_delta("#123456", "#123456") == 0
    # 좋음/나쁨 짝 — 3.7.2 에 나쁨을 빨강으로 되돌리며 36.1(초록·파랑) → 14.0. 값이 바뀌면 theme 주석과 쓰는 자리를 다시 본다
    got = {(m, k): round(T.cvd_delta(p[f"GOOD{k}"], p[f"BAD{k}"]), 1)
           for m, p in T._PALETTES.items() for k in ("", "_FILL")}
    assert got == {("dark", ""): 14.0, ("dark", "_FILL"): 14.0, ("light", ""): 5.0, ("light", "_FILL"): 6.6}, got


def test_g8_cells_match_reference():
    # 95 ③ — 다른 기준 화면과 똑같이. 2026-11-10 그 화면(같은 계정 · 두 테마)에서 계산된 칸 색을 읽은 값 그대로
    # (값, 공격/수비, 어두운 (바탕, 글자), 밝은 (바탕 — None 은 흰 카드 그대로, 글자)). 밝은 공격 R 이 200~205 에 머무는 것도 그 화면 그대로.
    g8, scale = T.g8_cell, app_main.config.G8_SCALE
    assert scale == (210, 860)

    def h(r, g, b):
        return f"#{r:02x}{g:02x}{b:02x}"
    W, K, DT, DD = "#ffffff", "#000000", "#f2f6f1", "#9aa79d"
    ref = [
        (912.8, "atk", (h(198, 52, 68), DT), (h(205, 74, 100), W)),
        (798.1, "atk", (h(181, 50, 65), DT), (h(204, 97, 119), W)),
        (686.1, "atk", (h(151, 46, 61), DT), (h(204, 119, 138), K)),
        (500.8, "atk", (h(101, 39, 53), DT), (h(203, 156, 170), K)),
        (310.7, "atk", (h(50, 33, 44), DT), (h(202, 193, 202), K)),
        (224.8, "atk", (h(27, 30, 41), DT), (h(201, 210, 217), K)),
        (213.6, "atk", (h(24, 29, 40), DT), (h(201, 213, 219), K)),
        (203.6, "atk", (h(23, 29, 40), DD), (None, K)),
        (0.0, "atk", (h(23, 29, 40), DD), (None, K)),
        (855.0, "def", (h(48, 97, 204), DT), (h(13, 13, 252), W)),
        (516.6, "def", (h(35, 62, 118), DT), (h(109, 109, 232), K)),
        (433.4, "def", (h(32, 53, 97), DT), (h(132, 132, 226), K)),
        (322.6, "def", (h(27, 41, 69), DT), (h(164, 164, 220), K)),
        (241.3, "def", (h(24, 32, 48), DT), (h(187, 187, 215), K)),
        (202.0, "def", (h(23, 29, 40), DD), (None, K)),
    ]
    bad = [(v, k, m, g8(v, k, scale, m), want) for v, k, dark, light in ref
           for m, want in (("dark", dark), ("light", light)) if g8(v, k, scale, m) != want]
    assert not bad, bad
    assert g8(1527, "atk", scale, "dark")[0] == T.G8_END["atk"] == "#c63444"   # 860 넘으면 끝 색


def test_players_g8_wiring():
    # 화면이 g8_cell·G8_SCALE·G8_POS_FLOOR 를 실제로 쓰는지 — 기본값이 아닌 눈금·기준으로 잰다(같으면 배선이 끊겨도 참)
    import core_api
    players = app_main.core.aggregate_players(_win._slice()[1], _win._ouid,
                                              pos_name=lambda p: _win._positions.get(p, str(p)))
    vals = sorted(p.attack_power for p in players)
    lo = (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2      # 픽스처 값 가운데 — 칠한 칸·안 칠한 칸이 둘 다 나온다
    scale = (lo, lo + 100)
    victim = max(players, key=lambda p: p.defense_power)
    keep = (core_api.MIN_PLAYER_GAMES, app_main.config.G8_SCALE, app_main.config.G8_POS_FLOOR)
    core_api.MIN_PLAYER_GAMES = 1
    app_main.config.G8_SCALE = scale
    app_main.config.G8_POS_FLOOR = {victim.position: (0, victim.defense_power + 1)}
    try:
        _win._render_players(_win._slice()[1])
        tb = _win.tbl_players
        by_id = {tb.item(r, 1).data(Qt.ItemDataRole.UserRole): r for r in range(tb.rowCount())}
        res = [m.result for m in _win._slice()[0]]
        team = app_main.core.win_pct(res.count("승"), res.count("무"), res.count("패"), players[0].mixed)
        painted = 0
        for p in players:
            r = by_id[p.sp_id]
            for c, kind, v in ((5, "atk", p.attack_power), (6, "def", p.defense_power)):
                bg, fg = T.g8_cell(v, kind, scale)
                it = tb.item(r, c)
                got = it.background().color().name() if it.background().style() != Qt.BrushStyle.NoBrush else None
                assert got == bg and it.foreground().color().name() == fg, (p.name, kind, v, got, bg)
                painted += bg not in (None, T.G8_DARK_BASE)   # 95 ③ — 어두운 쪽은 아래 끝 밑도 바탕색으로 칠한다
                marked = p.position == victim.position and kind == "def" and v < victim.defense_power + 1
                assert ("▽" in it.text()) == marked and bool(it.toolTip()) == marked, (p.name, kind, it.text())
            rate = tb.item(r, 4)
            assert rate.foreground().color().name() == app_main.delta_color(p.win_rate - team), (p.name, p.win_rate, team)
        assert 0 < painted < 2 * len(players), painted
        # 픽스처 선수는 다 같은 경기를 뛰어 승률이 같다 — 팀 승률을 끝으로 밀어 방향을 잰다(0% 면 좋음 · 100% 면 나쁨)
        keep_slice = _win._slice
        ms, ds = keep_slice()
        mid = [p for p in players if 0 < p.win_rate < 100]
        assert mid, [p.win_rate for p in players]
        try:
            for res, color in (("패", T.GOOD), ("승", T.BAD)):
                _win._slice = lambda res=res: ([replace(m, result=res) for m in ms], ds)
                _win._render_players(ds)
                by_id = {tb.item(r, 1).data(Qt.ItemDataRole.UserRole): r for r in range(tb.rowCount())}
                got = {tb.item(by_id[p.sp_id], 4).foreground().color().name() for p in mid}
                assert got == {color.lower()}, (res, got, color)
        finally:
            _win._slice = keep_slice
            _win._render_players(ds)
        # 승률 굵게는 대리자가 그릴 때 — 표 글꼴을 줄여도 따라가고, 폭(sizeHint)은 보통 글꼴로 알린다
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QStyleOptionViewItem
        import widgets
        dg = tb.itemDelegateForColumn(4)
        assert isinstance(dg, widgets.BoldColumnDelegate), dg
        opt = QStyleOptionViewItem()
        opt.font = QFont(tb.font())
        dg.initStyleOption(opt, tb.model().index(0, 4))
        assert opt.font.bold() and not tb.item(0, 4).font().bold(), "칸에 굵은 글꼴을 박으면 글꼴 축소 때 잘린다"
        plain = QStyleOptionViewItem()
        plain.font = QFont(tb.font())
        w_bold = dg.sizeHint(plain, tb.model().index(0, 4)).width()
        assert w_bold == widgets.RowBorderDelegate(tb).sizeHint(plain, tb.model().index(0, 4)).width(), w_bold
        # 그래서 굵은 만큼은 그 열 여백으로 — 가장 작은 창(글꼴이 줄어든 상태)에서도 굵은 글자 + 칸 여백이 열 폭 안
        from PySide6.QtGui import QFontMetrics
        _win._go_page("선수 지표")
        try:
            _at_size(*app_main.MIN_WINDOW)
            bf = QFont(tb.font())
            bf.setBold(True)
            fm = QFontMetrics(bf)
            short = [(tb.item(r, 4).text(), tb.columnWidth(4), fm.horizontalAdvance(tb.item(r, 4).text()) + tb._pad)
                     for r in range(tb.rowCount()) if tb.columnWidth(4) < fm.horizontalAdvance(tb.item(r, 4).text()) + tb._pad]
            assert not short, short[:3]
        finally:
            _at_size(1600, 900)
        assert "▽" in tb.item(by_id[victim.sp_id], 6).text()
        # 출전 미달이면 색·▽ 를 안 붙인다(승률 굵게는 그대로)
        core_api.MIN_PLAYER_GAMES = 10 ** 6
        _win._render_players(_win._slice()[1])
        for r in range(tb.rowCount()):
            for c in (5, 6):
                it = tb.item(r, c)
                assert it.background().style() == Qt.BrushStyle.NoBrush and "▽" not in it.text(), (r, c, it.text())
            assert tb.item(r, 4).data(Qt.ItemDataRole.ForegroundRole) is None
    finally:
        core_api.MIN_PLAYER_GAMES, app_main.config.G8_SCALE, app_main.config.G8_POS_FLOOR = keep
        _win._render_players(_win._slice()[1])


def _player_rows():
    """선수 지표 표 → {spId: (출전 글자, 출전 툴팁, 공격력 칸 칠함, 수비력 칸 칠함, 포지션 글자색)}.
    칠함 = G8 이 글자색을 정했다(아래 끝 밑의 값은 배경 없이 흐린 글자라 배경으로는 못 잰다)."""
    tb = _win.tbl_players
    fg = Qt.ItemDataRole.ForegroundRole
    out = {}
    for r in range(tb.rowCount()):
        sid = tb.item(r, 1).data(Qt.ItemDataRole.UserRole)
        out[sid] = (tb.item(r, 3).text(), tb.item(r, 3).toolTip(),
                    tb.item(r, 5).data(fg) is not None,
                    tb.item(r, 6).data(fg) is not None,
                    tb.item(r, 0).foreground().color().name())
    return out


def test_players_small_sample_marked():
    # 픽스처 선수는 출전 3~4경기라 기본 5 면 전부 ⚠ — 기준을 '가장 적은 출전'과 '+1'로 바꿔 경계를 잰다.
    import core_api
    saved = core_api.MIN_PLAYER_GAMES
    players = app_main.core.aggregate_players(_win._slice()[1], _win._ouid)
    games = {p.sp_id: p.games for p in players}
    low = min(games.values())
    try:
        core_api.MIN_PLAYER_GAMES = low
        _win._render_players(_win._slice()[1])
        rows = _player_rows()
        assert not any("⚠" in t for t, *_ in rows.values()), rows
        assert sum(a for _, _, a, _, _ in rows.values()) >= 2, "기준을 넘는 선수가 칠해지지 않았다"
        core_api.MIN_PLAYER_GAMES = low + 1
        _win._render_players(_win._slice()[1])
        rows = _player_rows()
        for sid, (text, tip, atk, dfn, _) in rows.items():
            few = games[sid] < low + 1
            assert ("⚠" in text) == few and bool(tip) == few, (sid, games[sid], text, tip)
            if few:
                assert not atk and not dfn, (sid, "표본 미달 선수가 칠해졌다")
        # 포지션 글자색 — 묶음이 있는 선수는 그 색, 정렬이 끝난 뒤에도 행을 따라간다
        for p in players:
            line = app_main.core.position_line(p.pos_code)
            if line:
                assert rows[p.sp_id][4] == T.POS_COLORS[line].lower(), (p.sp_id, line, rows[p.sp_id][4])
    finally:
        core_api.MIN_PLAYER_GAMES = saved
        _win._render_players(_win._slice()[1])


def test_grade_badge_not_clipped():
    # 강화 열은 배지 — 글자 폭 + 배지 여백이 그 열에만 들어가야 한다. 여백을 sizeHint 로 알리면 FitTableWidget 이
    # 그 여백을 19열 전부에 얹는다(_measure_pad) — 그래서 표 공통 여백이 '배지 없을 때'와 같은지도 본다.
    # 픽셀 폭이 아니라 공통 여백·기준 글자 폭으로 잰다 — 좁은 창에선 표 글꼴 축소로 픽셀이 정상적으로 바뀐다.
    import widgets
    tb = _win.tbl_players
    dg = tb.itemDelegateForColumn(2)
    assert isinstance(dg, widgets.GradeBadgeDelegate) and isinstance(dg, widgets.RowBorderDelegate), dg
    _win._render_players(_win._slice()[1])
    with_badge = (tb._pad, dict(tb._base_text_widths), dict(tb._extra))
    tb.setItemDelegateForColumn(2, None)
    try:
        _win._render_players(_win._slice()[1])
        plain = (tb._pad, dict(tb._base_text_widths))
    finally:
        tb.setItemDelegateForColumn(2, dg)
        _win._render_players(_win._slice()[1])
    assert with_badge[0] == plain[0] and with_badge[1] == plain[1], (with_badge[:2], plain)
    assert with_badge[2].get(2) == 2 * widgets.GradeBadgeDelegate.PAD, with_badge[2]
    from PySide6.QtGui import QFontMetrics
    _win._go_page("선수 지표")
    _app.processEvents()
    fm = QFontMetrics(tb.font())
    need = max(fm.horizontalAdvance(tb.item(r, 2).text()) for r in range(tb.rowCount())) \
        + 2 * widgets.GradeBadgeDelegate.PAD
    assert tb.columnWidth(2) >= need, (tb.columnWidth(2), need)
    _win._go_page("대시보드")


def test_badge_paint_failure_falls_back():
    # 그리기 예외는 다시 그릴 때마다 되풀이된다 — 글자만 그리고 넘기되 기록은 한 번만(crash.log 가 불어나지 않게)
    import crashlog
    import widgets
    tb = _win.tbl_players
    dg = tb.itemDelegateForColumn(2)
    notes = []
    real_paint, real_note = widgets.paint_grade_badge, crashlog.note

    def boom(*a):
        raise ValueError("배지 그리기 실패")
    widgets.paint_grade_badge, crashlog.note = boom, lambda kind, e: notes.append(kind)
    dg.failed = 0
    try:
        _win._go_page("선수 지표")
        for _ in range(2):
            tb.viewport().grab()
        assert dg.failed >= 2 and len(notes) == 1, (dg.failed, notes)
    finally:
        widgets.paint_grade_badge, crashlog.note = real_paint, real_note
        dg.failed = 0
        _win._go_page("대시보드")


def test_grade_badge_columns_wired():
    # 64 P8 — GRADE_BADGE_COLUMNS 의 표마다 그 열에 배지 대리자 + 배지 여백(test_rules 가 칸을 만드는 함수와 대조한다)
    import widgets
    from PySide6.QtWidgets import QStyleOptionViewItem, QTableWidgetItem
    for fn, (attr, cols) in app_main.MainWindow.GRADE_BADGE_COLUMNS.items():
        tb = getattr(_win, attr)
        for c in cols:
            assert isinstance(tb.itemDelegateForColumn(c), widgets.GradeBadgeDelegate), (fn, attr, c)
            assert tb._user_extra.get(c) == 2 * widgets.GradeBadgeDelegate.PAD, (attr, c, tb._user_extra)
    # 배지는 강화 하나일 때만 — "+5" 는 배지, 변화("3 → 5")·"-"·0 은 글자 그대로(지우면 빈 칸이 된다)
    g = widgets.GradeBadgeDelegate.grade_of
    assert [g(t) for t in ("5", "+5", "13", "3 → 5", "-", "0", "", None)] == [5, 5, 13, None, None, None, None, None]
    tb = widgets.FitTableWidget(4, 1)
    dg = widgets.GradeBadgeDelegate(tb)
    tb.setItemDelegateForColumn(0, dg)
    for r, t in enumerate(("+5", "3 → 5", "-", "7")):
        tb.setItem(r, 0, QTableWidgetItem(t))
    shown = []
    for r in range(4):
        opt = QStyleOptionViewItem()
        dg.initStyleOption(opt, tb.model().index(r, 0))
        shown.append(opt.text)
    assert shown == ["", "3 → 5", "-", ""], shown


def test_player_chip_is_tag_style():
    # 64 조각 ③ — 포지션 칩은 꽉 칠한 색 + 진한 글자(밝은 쪽 대비 2.45)가 아니라 태그 꼴: 바탕 = 그 색 10% · 글자 = 그 색
    import widgets
    from PySide6.QtGui import QColor
    acc = widgets.PitchWidget._accent_for
    assert [acc(0), acc(5), acc(10), acc(18), acc(25)] == [T.CHIP_GK, T.CHIP_DF, T.CHIP_MF, T.CHIP_MF, T.CHIP_FW]
    chip = widgets._PlayerChip(widgets.PitchCard(25, "ST", "선수", None), T.CHIP_FW)
    chip.resize(110, 100)
    img = chip.grab().toImage()
    soft = QColor(T.blend(T.PANEL, T.CHIP_FW, T.CHIP_SOFT_MIX)).rgb()
    assert img.pixelColor(6, 6).rgb() == soft, (img.pixelColor(6, 6).name(), QColor(soft).name())
    top_h = max(16, int(100 * 0.2))
    near = [img.pixelColor(x, y) for y in range(4, 4 + top_h - 2) for x in range(4, 40)]
    fw = QColor(T.CHIP_FW)
    assert any(abs(c.red() - fw.red()) + abs(c.green() - fw.green()) + abs(c.blue() - fw.blue()) < 40 for c in near), \
        "칩 글자가 칩 색으로 안 찍혔다"


# ── 9단계(1.3.1): ② 흐름 분석 띠·근거 막대 · ⑥ 히트맵·도넛·포메이션 막대 · 13 ELO · 다시 묻는 동의 ───────────
from dataclasses import replace  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
import charts  # noqa: E402
import tray  # noqa: E402

def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_basis_bar_mixed_text():
    # 섞인 Basis — 표본 글자는 "승패 N판", 숫자 뒤 *(식은 툴팁). 흐림 판정은 rated(wdl) = n
    from analysis import Basis, Insight
    rows = [Insight(app_main.core.SEC_WIN, "x", "", 1.0,
                    Basis("선제골 넣은 경기", 80.0, 5, 50.0, 8, mixed=True, wdl=(4, 2, 1)))]
    box = app_main.MainWindow._basis_bars(rows)
    name, row, tip = box.basis_rows[0]
    side = row.side.text()
    assert "승패 5판" in side and "80.0%*" in side and "전체 50.0%*" in side and row.bar.property("weak"), side
    assert "무승부를 빼고" in tip, tip
    # 79 — 승·무·패 세 칸 + 색마다 %
    assert row.bar.counts() == (4, 2, 1), row.bar.counts()
    assert "57%" in row.bar.label.text() and "29%" in row.bar.label.text() and "14%" in row.bar.label.text(), \
        row.bar.label.text()
    # 승·무·패 수가 없는 Basis(옛 꼴)는 줄을 안 만든다 — 막대가 승률과 다른 수를 그리지 않게
    assert app_main.MainWindow._basis_bars([Insight(app_main.core.SEC_WIN, "x", "", 1.0,
                                                    Basis("y", 50.0, 10, 50.0, 8))]) is None


def test_analysis_result_dots_and_basis_bars():
    from analysis import Basis, Insight
    from PySide6.QtWidgets import QLabel
    keep = app_main.core.narrate
    found = [Insight(app_main.core.SEC_WIN, "선제골을 넣으면 승률 80%.", "", 5.0,
                     Basis("선제골 넣은 경기", 80.0, 30, 50.0, 8, wdl=(24, 0, 6))),
             Insight(app_main.core.SEC_WIN, "득점의 30%가 헤더에서 나옵니다.", "", 4.0),       # basis 없음 — 막대 없음
             Insight(app_main.core.SEC_LOSE, "천적 상대로 4경기 승률 25%.", "", 3.0,
                     Basis("천적 상대", 25.0, 4, 50.0, 6, wdl=(1, 0, 3)))]                                 # 표본 미달(일부러) — 흐림
    app_main.core.narrate = lambda *a, **k: found
    try:
        _win._narrate_key = None
        _win._render_analysis()
        assert len(_win.analysis_dots._items) == min(len(_win._matches), app_main.core.WINDOW)
        assert [r for r, _ in _win.analysis_dots._items] == [m.result for m in _win._matches[:20]][::-1], "오래된 것부터가 아니다"

        def bars(sec):
            box = _win.box_analysis[sec]
            return [box.itemAt(i).widget() for i in range(box.count())
                    if hasattr(box.itemAt(i).widget(), "basis_rows")]
        win_bars, lose_bars = bars(app_main.core.SEC_WIN), bars(app_main.core.SEC_LOSE)
        assert len(win_bars) == 1 and len(win_bars[0].basis_rows) == 1, "basis 있는 문장만 막대로"
        name, row, tip = win_bars[0].basis_rows[0]
        right = row.side.text()
        assert name == "선제골 넣은 경기" and not row.bar.property("weak") and "전체 50.0% 대비 +30.0%p" in right, right
        assert row.bar.counts() == (24, 0, 6), "근거 막대가 승·무·패 세 칸이 아니다(79)"
        assert lose_bars[0].basis_rows[0][1].bar.property("weak") is True, "표본 미달인데 흐리지 않았다"
        assert "*" not in right, "한 집단 식인데 * 를 붙였다"
        assert bars(app_main.core.SEC_FLOW) == [], "문장이 없는 섹션에 막대가 생겼다"
        box = _win.box_analysis[app_main.core.SEC_WIN]
        heads = [lb for i in range(box.count()) if (w := box.itemAt(i).widget()) is not None
                 for lb in w.findChildren(QLabel) if "선제골을" in lb.text()]   # 근거 줄 이름("선제골 넣은 경기")은 뺀다
        assert len(heads) == 1, [h.text() for h in heads]
        h = heads[0].text()
        assert f"<span style='color:{T.GOOD}'>80%</span>" in h and h.startswith(f"<span style='color:{T.TEXT}'>"), h
        assert f"color:{T.GOOD}'>선제골" not in h, "글까지 섹션 색이면 숫자가 안 튄다"
    finally:
        app_main.core.narrate = keep
        _win._narrate_key = None
        _win._render_analysis()


def test_clutch_heatmap_cells_and_weak_cells_uncolored():
    from datetime import datetime as _dt
    ms = []
    for i in range(10):                                   # 월요일 20시 10경기 — 색이 칠해지는 칸
        m = replace(_MATCHES[0], match_date=_dt(2026, 9, 14, 20, i), result="승" if i < 8 else "패")
        ms.append(m)
    ms.append(replace(_MATCHES[0], match_date=_dt(2026, 9, 15, 3, 0), result="승"))   # 화 심야 1경기 — 흐림
    _win._render_clutch_heat(ms)
    h = _win.clutch_heat
    bands = [b[0] for b in app_main.core.TIME_BANDS]
    night, eve = bands.index("심야"), bands.index("저녁·밤")
    assert len(h._cells) == 4 and all(len(r) == 7 for r in h._cells)
    v, text, tip, weak = h._cells[eve][0]
    assert (v, text, weak) == (80.0, "80%", False) and h.cell_colors[eve][0] is not None, h._cells[eve][0]
    v, text, tip, weak = h._cells[night][1]
    assert weak and text == "표본 1" and h.cell_colors[night][1] is None, "소표본 칸에 색을 칠했다"
    assert h.cell_colors[night][0] is None and h._cells[night][0][1] == "—"
    # 최소 폭은 칸마다 '100%' 이상 — QScrollArea 가 가로를 조용히 자르지 않게
    fm = QFontMetrics(h._font())
    assert h.minimumSizeHint().width() >= 7 * (fm.horizontalAdvance("100%") + h.PAD)
    _win._render_clutch(_win._details, _win._matches)


def test_heatmap_text_stays_readable_and_direction():
    bad = []
    for mode, p in T._PALETTES.items():
        for key in ("HEAT_GOOD", "HEAT_BAD"):   # 95 — 칸 끝 색은 그래프 색(게임 초록)이 아니라 글자 위 칸 전용
            for s in range(11):
                bg = T.blend(p["PANEL"], p[key], charts.HEAT_MAX_MIX * s / 10)
                if T.contrast(p["TEXT"], bg) < 4.5:
                    bad.append((mode, key, s))
    assert not bad, bad
    assert charts.heat_cell_color(50.0) == T.PANEL
    assert charts.heat_cell_color(90.0) == T.blend(T.PANEL, T.HEAT_GOOD, charts.HEAT_MAX_MIX)
    assert charts.heat_cell_color(10.0) == T.blend(T.PANEL, T.HEAT_BAD, charts.HEAT_MAX_MIX)
    assert charts.heat_cell_color(62.5) == T.blend(T.PANEL, T.HEAT_GOOD, 0.5 * charts.HEAT_MAX_MIX)


def test_donut_groups_tail_into_other():
    counts = [(f"유형{i}", 10 - i) for i in range(8)] + [("없음", 0)]
    segs = charts.donut_segments(counts)
    assert len(segs) == charts.DONUT_MAX and segs[-1][0] == "기타", segs
    assert segs[-1][1] == sum(10 - i for i in range(5, 8)) and segs[-1][2] == T.CHART_NEUTRAL
    assert [s[2] for s in segs[:5]] == list(T.CHART_CATS)
    assert sum(v for _, v, _ in segs) == sum(v for _, v in counts)
    few = charts.donut_segments([("가", 3), ("나", 5)])
    assert [s[0] for s in few] == ["나", "가"] and "기타" not in [s[0] for s in few]


def test_tactics_formation_rows_and_shot_types():
    # 65 M5·M6 — 상대 포메이션 줄 · 경기 결과 줄 · 득점/실점 유형(슛 타입 분포 꼴)
    _win._render_tactics(_win._details)
    core = app_main.core
    rows = _win.opp_formation_list.all_rows()
    opp = core.formation_stats(_win._details, _win._ouid)
    assert [r.key for r in rows] == [f.formation for f in opp]
    assert [r.label for r in rows] == [core.formation_label(f.formation) for f in opp]
    assert [(r.win, r.draw, r.lose) for r in rows] == [(f.win, f.draw, f.lose) for f in opp]
    assert all(r.weak == (f.rated < core.MIN_COND) for r, f in zip(rows, opp))
    rb = core.result_breakdown(_win._details, _win._ouid)
    res = {r.label: (r.win, r.draw, r.lose) for r in _win.result_list.all_rows()}
    assert res == {"전후반": tuple(rb.normal), "연장전": tuple(rb.extra), "승부차기": tuple(rb.shootout),
                   "몰수": tuple(rb.forfeit)}, res
    # 스크롤 페이지 안 목록은 줄 수만큼 높이를 고정한다(안쪽 스크롤이 생기면 줄이 가려진다)
    rl = _win.result_list
    assert rl.height() == 4 * rl.rate_delegate.row_height(rl.font()) + 2 * rl.frameWidth() + 2, \
        (rl.height(), rl.rate_delegate.row_height(rl.font()))
    # 상대 포메이션 그림은 그 상대들이 실제로 선 자리(positions)로 — 이름만으로 그린 그림이 아니다
    import widgets
    f0 = next(f for f in opp if f.positions)
    got = _win.opp_formation_list.icon("formation", f0.formation).toImage()
    assert got == widgets.formation_pixmap(f0.formation, positions=f0.positions).toImage()
    assert got != widgets.formation_pixmap(f0.formation).toImage(), "자리 점이 이름만으로 그린 그림과 같다 — 검사가 빈다"
    for key, mine in (("gf", True), ("ga", False)):
        sm = core.shot_map(_win._details, _win._ouid, mine=mine)
        got = _win.shot_types[key].rows
        assert sum(r[1] for r in got) == sm.total and sum(r[3] for r in got) == sm.goals, (key, got)
        assert all(r[3] <= r[2] <= r[1] for r in got), got   # 골 ⊂ 유효 ⊂ 시도
        # 시도가 문턱 미만인 줄은 전환율을 숨긴다(작은 표본) — 문턱 이상이면 골÷시도
        for name, shots, _on, goals, conv in got:
            want = f"{goals / shots * 100:.0f}%" if shots >= core.MIN_BUCKET_SHOTS else app_main.NA
            assert conv == want, (name, shots, goals, conv)


def test_dashboard_head_formation_follows_account():
    """대시보드 머리 주 포메이션 — 경기가 있으면 전술 탭 맨 위 포메이션과 같은 것이 그려지고, 경기를 비우면 숨는다(옛 계정 그림이 안 남는다)."""
    saved = (_win._details, _win._matches)
    try:
        _win._render_ranker()
        mine = app_main.core.formation_stats(_win._details, _win._ouid, of_opponent=False)
        assert mine, "지어낸 경기에 내 포메이션이 있어야 이 테스트가 뜻이 있다"
        card = _win.card_ranker
        assert not card._head_pitch.isHidden() and mine[0].formation in card._head_pitch.toolTip()
        _win._details, _win._matches = [], []
        _win._render_ranker()
        assert card._head_pitch.isHidden() and card._head_pitch.toolTip() == "", "경기가 없는데 옛 포메이션이 남았다"
    finally:
        _win._details, _win._matches = saved
        _win._render_ranker()


def test_dashboard_ranker_record_fits_1280():
    """구단주 카드 전적 — 승률을 값 줄이 아니라 제목 줄에. 실데이터(1만 4천 경기)에서 "6802승 1061무 6687패 (46.7%)" 가
    가장 작은 글씨로도 300px 라 카드 최소 폭이 대시보드를 1280×720 에서 20px 넘겼다(세로 스크롤만이라 조용히 잘림 · 2026-11-09 실화면)."""
    # 페이지 전체로는 못 잰다 — 지어낸 경기에선 옆 [승률 흐름] 카드가 실데이터(최소 474)보다 좁아 예전 글도 안 넘친다.
    # 그래서 카드 폭 예산으로: 1280×720 보이는 폭 1000 − 승률 흐름 474 − 간격 16 = 510, 여유 30.
    budget = 480
    from types import SimpleNamespace
    keep_sum, keep_size, keep_champ, keep_rank = app_main.summarize, _win.size(), _win._is_champion, _win._rank
    try:
        _win.resize(1280, 720)
        _win._go_page("대시보드")
        _win._is_champion, _win._rank = True, None   # 챔피언스 — 타일 넷(2열). 넥슨 순위를 못 읽은 길이라 앱 집계로 센다
        app_main.summarize = lambda _m: SimpleNamespace(win=6802, draw=1061, lose=6687, mixed=False)
        card = _win.card_ranker

        def card_w() -> int:
            for _ in range(3):
                card._vals["전적"].updateGeometry()
                _app.processEvents()
            return card.minimumSizeHint().width()
        _win._render_ranker()
        new_w = card_w()
        assert "(" not in card._vals["전적"].text() and "%" in card._caps["전적"].text(), \
            (card._vals["전적"].text(), card._caps["전적"].text())
        card.set("전적", "6802승 1061무 6687패 (46.7%)")   # 고치기 전 모양 — 이게 예산 안이면 아래 단언이 빈다
        assert card_w() > budget, f"예전 글({card_w()}px)도 예산 안 — 이 테스트가 재는 게 없다"
        assert new_w <= budget, f"구단주 카드 최소 폭 {new_w}px — 1280×720 대시보드를 넘긴다(예산 {budget})"
    finally:
        app_main.summarize, _win._is_champion, _win._rank = keep_sum, keep_champ, keep_rank
        _win.resize(keep_size)
        _win._render_ranker()


# ── 13 ELO ──

def _elo_rows(points):
    """[(ISO 시각, elo, rank, source)] → elo_history 줄 모양."""
    return [{"taken_at": t, "elo": e, "rank": r, "source": s, "profile_sn": 1, "nickname": "n"} for t, e, r, s in points]


def test_elo_season_start_rules():
    from datetime import date as _d
    now = datetime(2026, 10, 5, 12, 0)
    seasons = [sn.Season(89, "시즌 2", _d(2026, 7, 1), _d(2026, 8, 1)), sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
    # 시즌 시작은 종료일 0시가 아니라 교체 점검 뒤(config.SEASON_CUTOFF_H · 76) — 그 새벽 옛 시즌 점이 안 섞이게
    assert app_main.elo_season_start(seasons, "2026-09-20T00:00:00", now) == datetime(2026, 9, 10, config.SEASON_CUTOFF_H)
    assert app_main.elo_season_start(seasons, None, datetime(2026, 9, 10, 7, 0)) == datetime(2026, 8, 1, config.SEASON_CUTOFF_H)
    assert app_main.elo_season_start([], "2026-09-20T03:00:00", now) == datetime(2026, 9, 20, 3)
    assert app_main.elo_season_start([], None, now) == now - timedelta(days=config.ELO_FALLBACK_DAYS)
    # 아직 안 끝난 시즌(미래 종료일)은 시작으로 안 쓴다
    fut = seasons + [sn.Season(91, "시즌 4", _d(2026, 9, 10), _d(2026, 11, 1))]
    assert app_main.elo_season_start(fut, None, now) == datetime(2026, 9, 10, config.SEASON_CUTOFF_H)


def test_elo_daily_points_keep_latest_of_day_and_filter_start():
    rows = _elo_rows([("2026-09-01T10:00:00", 3000.0, 5000, "search"),     # 지난 시즌 — 빠진다
                      ("2026-09-11T09:00:00", 3100.0, 4000, "snapshot"),
                      ("2026-09-11T21:00:00", 3150.0, 3900, "search"),     # 같은 날 늦은 것
                      ("2026-09-13T08:00:00", 3200.0, 900, "snapshot")])
    pts = app_main.elo_daily_points(rows, datetime(2026, 9, 10))
    assert [(p["at"].day, p["elo"]) for p in pts] == [(11, 3150.0), (13, 3200.0)], pts
    assert app_main.elo_daily_points(rows, datetime(2026, 9, 11, 9, 0))[0]["elo"] == 3150.0   # 시작 시각 포함


def test_rank_tier_change_text_cases():
    def pts(*ranks):
        return [{"rank": r, "at": datetime(2026, 10, 1 + i)} for i, r in enumerate(ranks)]
    f = app_main.rank_tier_change_text
    assert f([]) == ""
    assert f(pts(150)) == "지금 1~200위 구간", "새 시즌 첫 기록인데 비교했다"
    assert f(pts(500, 1500)) == "지금 1,001~10,000위 구간 · 지난 기록(10/01) 201~1,000위에서 내려옴"
    assert f(pts(1500, 1000)) == "지금 201~1,000위 구간 · 지난 기록(10/01) 1,001~10,000위에서 올라옴"
    assert f(pts(200, 201)).endswith("1~200위에서 내려옴"), "경계(200위)가 틀렸다"
    assert f(pts(300, 900)).endswith("같은 구간")
    assert f(pts(9000, None)) == "지금 1만 위 밖 · 지난 기록(10/01) 1,001~10,000위에서 내려옴"
    assert f(pts(10001, 10000)).endswith("1만 위 밖에서 올라옴")


def _elo_data(rows, cuts=None, tracked=False, names=(), req=None, ouid=None):
    ouid = ouid or _OUID
    if req is None:
        req = _win._elo_req.get(ouid, 0) + 1
    _win._elo_req[ouid] = req
    return app_main.EloSeries(req, rows, cuts or {}, None, tracked, tuple(names))


def test_elo_chart_draws_current_season_with_cut_lines():
    from datetime import date as _d
    keep = (_win._rank_seasons, dict(_win._elo), config.WEB_DATA, config.RANK_COLLECT)
    try:
        config.WEB_DATA, config.RANK_COLLECT = True, True
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
        rows = _elo_rows([("2026-09-05T10:00:00", 2900.0, 9000, "search"),
                          ("2026-09-12T10:00:00", 3100.0, 1500, "snapshot"),
                          ("2026-09-14T10:00:00", 3300.0, 800, "snapshot")])
        cuts = {200: [("2026-09-11T10:00:00", 4300.0), ("2026-09-13T10:00:00", 4320.0)],
                1000: [("2026-09-13T10:00:00", 3200.0)], 10000: [("2026-09-13T10:00:00", 2500.0)]}
        _win._on_elo_ready(_OUID, _elo_data(rows, cuts, tracked=True))
        _win._go_page("승률 그래프", "점수·예측")     # 메뉴를 거쳐 — pages 만 바꾸면 메뉴 선택과 어긋나 다음 테스트가 깨진다
        _app.processEvents()
        ch = _win.elo_chart
        assert ch.isVisibleTo(_win) and len(ch._points) == 2, "지난 시즌 점이 섞였거나 안 그렸다"
        assert ch._x_dates == [_d(2026, 9, 12), _d(2026, 9, 14)] and ch._avg is False
        assert [r[0] for r in ch._refs] == ["200위", "1,000위"], "1만 위 컷까지 그렸다(축이 찌그러진다)"
        assert "10,000위 2,500" in ch.toolTip() and "200위" not in ch.toolTip(), "그린 컷을 툴팁에 또 적었다"
        assert "%" not in ch._axis.fmt
        assert _win.lb_elo_tier.text().endswith("1,001~10,000위에서 올라옴"), _win.lb_elo_tier.text()
        assert "09/10" in _win.gb_elo.title()
    finally:
        _win._rank_seasons, _win._elo, config.WEB_DATA, config.RANK_COLLECT = keep
        _win._render_elo()


def test_elo_empty_states_by_cause():
    keep = (dict(_win._elo), config.WEB_DATA, config.RANK_COLLECT)
    one = _elo_rows([(datetime.now().isoformat(timespec="seconds"), 3000.0, 5000, "search")])
    out = _elo_rows([(datetime.now().isoformat(timespec="seconds"), 2000.0, None, "search")])
    try:
        cases = [((False, False, False, one), "홈페이지 데이터가 꺼져"),
                 ((True, False, False, one), "검색할 때마다 한 점씩 쌓입니다"),
                 ((True, True, True, one), "다음 수집(하루 한 번) 뒤부터"),
                 ((True, True, False, one), "[따라가기]를 누르면 하루 한 점"),
                 ((True, False, False, out), "1만 위 밖은 하루 기록이 없습니다")]
        for (web, collect, tracked, rows), want in cases:
            config.WEB_DATA, config.RANK_COLLECT = web, collect
            _win._on_elo_ready(_OUID, _elo_data(rows, tracked=tracked))
            assert want in _win.lb_elo_note.text(), (web, collect, tracked, _win.lb_elo_note.text())
            assert not _win.elo_chart.isVisibleTo(_win), "점이 하나인데 그래프를 그렸다"
    finally:
        _win._elo, config.WEB_DATA, config.RANK_COLLECT = keep
        _win._render_elo()


def test_elo_stale_request_and_other_account_ignored():
    keep = dict(_win._elo)
    try:
        new = _elo_data(_elo_rows([("2026-10-01T10:00:00", 3500.0, 100, "search")]))
        old = app_main.EloSeries(new.req - 1, [], {}, None)
        _win._on_elo_ready(_OUID, new)
        _win._on_elo_ready(_OUID, old)                       # 먼저 띄운 읽기가 나중에 끝났다
        assert _win._elo[_OUID] is new, "늦게 끝난 옛 읽기가 새 값을 덮었다"
        other = _elo_data([], ouid="다른계정")
        _win._on_elo_ready("다른계정", other)
        assert _win._elo["다른계정"] is other and _win._elo[_OUID] is new
    finally:
        _win._elo = keep


def test_elo_loads_on_every_open_path():
    seen = []
    keep = _win._load_elo
    _win._load_elo = lambda ouid=None: seen.append(ouid)
    tmp, saved = _loader_db(4)
    try:
        _restore_account()                                    # 검색·다시 열기 — 전부 _on_loaded 를 지난다
        assert seen == [_OUID], seen
        ld = app_main.MatchLoader(_DetailApi([], ""), "닉", 52, offline_ouid=_OUID)   # 저장본 열기(켤 때·내려놓은 뒤)
        ld.finished_ok.connect(_win._on_loaded)
        ld.run()
        _app.processEvents()
        assert seen == [_OUID, _OUID], seen
        # 수집 회차 끝 — UI 스레드에선 시작만
        sched = app_main.RankCollectScheduler()
        keep_sched = _win._rank_sched
        _win.attach_rank_sched(sched)
        try:
            sched.outcome.emit(rankcollect.Outcome("ok"))
            sched.outcome.emit(rankcollect.Outcome("failed"))
        finally:
            sched.outcome.disconnect()
            sched.status.disconnect()
            _win._rank_sched = keep_sched
        assert seen == [_OUID] * 3, seen
    finally:
        _win._load_elo = keep
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
        _restore_account()


def test_elo_loader_runs_off_ui_and_reads_rank_db_readonly():
    tmp, saved = _loader_db(0)
    keep_rank = config.RANK_DB_PATH
    config.RANK_DB_PATH = tmp / "rank.db"
    try:
        c = store.open_db(config.DB_PATH)
        store.save_elo(c, _OUID, 3333.0, 700, taken_at=datetime.now())
        c.close()
        _win._load_elo(_OUID)
        w = _win._elo_workers[-1]
        assert isinstance(w, QThread)          # UI 스레드에선 띄우기만 — 스모크는 QThread.start 를 막아 run 을 직접 부른다
        w.run()
        assert _win._elo[_OUID].req == _win._elo_req[_OUID]
        assert [r["elo"] for r in _win._elo[_OUID].rows] == [3333.0]
        assert not config.RANK_DB_PATH.exists(), "ELO 를 읽다 rank.db 를 만들었다"
    finally:
        config.RANK_DB_PATH = keep_rank
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_elo_includes_this_search_point():
    # 랭킹이 finished_ok 보다 늦게 와도 — 첫 EloLoader 는 이번 점 없이 읽고, 적은 뒤 elo_saved 가 다시 읽게 한다
    tmp, saved = _loader_db(4)
    gate, keep = threading.Event(), app_main.MatchLoader._safe_rank
    info = ranker.RankerInfo(nickname="테스트구단주", rank=420, elo=4321.0, profile_sn=55)
    app_main.MatchLoader._safe_rank = lambda self: (gate.wait(5), info)[1]
    keep_elo = dict(_win._elo)
    app_main.EloLoader.start = lambda self: self.run()   # 스모크는 QThread.start 를 막는다 — 읽기를 그 자리에서
    try:
        _win._elo.pop(_OUID, None)
        ld = app_main.MatchLoader(_DetailApi([], ""), "닉", 52, record_elo=True)
        ld.finished_ok.connect(_win._on_loaded)
        ld.finished_ok.connect(lambda *a: gate.set())        # 화면이 열린 '뒤'에 랭킹이 온다
        ld.elo_saved.connect(_win._load_elo)
        ld.run()
        assert _wait(lambda: _win._elo.get(_OUID) is not None
                     and any(r["elo"] == 4321.0 for r in _win._elo[_OUID].rows)), "이번 검색의 점이 빠졌다"
    finally:
        gate.set()
        app_main.MatchLoader._safe_rank = keep
        del app_main.EloLoader.start
        _win._elo = keep_elo
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
        _restore_account()


def test_elo_track_button_add_remove_full_and_needs_consent():
    tmp, saved = _loader_db(0)
    keep = (config.NOTICE_ACCEPTED, config.WEB_DATA, dict(_win._elo), _win.ask_notice_update)
    asked = []
    try:
        config.WEB_DATA = True
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        _win._on_elo_ready(_OUID, _elo_data([]))
        assert _win.btn_elo_track.text() == f"이 구단주 ELO 따라가기 (0/{config.ELO_TRACK_MAX})"
        _win._on_elo_track_clicked()
        c = store.open_db(config.DB_PATH)
        assert [t["ouid"] for t in store.track_list(c)] == [_OUID]
        c.close()
        _win._on_elo_ready(_OUID, _elo_data([], tracked=True, names=("테스트구단주",)))
        assert _win.btn_elo_track.text() == "따라가기 그만"
        _win._on_elo_track_clicked()
        c = store.open_db(config.DB_PATH)
        assert store.track_list(c) == []
        c.close()
        # 가득 차면 막고 툴팁에 목록
        names = tuple(f"n{i}" for i in range(config.ELO_TRACK_MAX))
        _win._on_elo_ready(_OUID, _elo_data([], names=names))
        assert not _win.btn_elo_track.isEnabled() and "n4" in _win.btn_elo_track.toolTip()
        # 옛 동의 — 누르면 다시 묻는 창, 취소하면 안 넣는다
        config.NOTICE_ACCEPTED = config.NOTICE_BASE_VERSION
        _win.ask_notice_update = lambda: asked.append(1) or False
        _win._on_elo_ready(_OUID, _elo_data([]))
        assert "동의가 필요" in _win.btn_elo_track.toolTip()
        _win._on_elo_track_clicked()
        c = store.open_db(config.DB_PATH)
        assert asked == [1] and store.track_list(c) == [], "동의 없이 따라가기에 넣었다"
        c.close()
    finally:
        config.NOTICE_ACCEPTED, config.WEB_DATA, _win._elo, _win.ask_notice_update = keep
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)
        _win._render_elo()


def test_shutdown_stops_elo_workers():
    class _Busy(app_main.EloLoader):      # 도는 중인 척 — 스모크는 QThread.start 를 막는다
        def isRunning(self):
            return not self._cancel

    w = _Busy(_OUID, 999)
    _win._elo_workers.append(w)
    try:
        left = _win.shutdown(fast=True)
        assert w in left and w._cancel, "종료 정리 목록에 EloLoader 가 없다"
    finally:
        w.cancel()
        _win._elo_workers.remove(w)


def test_predict_runs_after_every_elo_read():
    # 진입점 표 다섯째 줄 — EloLoader 를 띄우는 세 경우는 전부 _on_elo_ready 로 끝난다(위 test_elo_loads_on_every_open_path)
    seen = []
    keep = (_win._load_predict, dict(_win._elo))
    _win._load_predict = lambda ouid: seen.append(ouid)
    try:
        new = _elo_data([])
        _win._on_elo_ready(_OUID, new)
        _win._on_elo_ready(_OUID, app_main.EloSeries(new.req - 1, [], {}, None))   # 늦게 끝난 옛 읽기 — 예측도 안 띄운다
        _win._on_elo_ready("다른계정", _elo_data([], ouid="다른계정"))
        assert seen == [_OUID, "다른계정"], seen
        _win._on_seasons_loaded(list(_win._rank_seasons))                            # 시즌표 도착 — 다시 계산
        assert seen[-1] == _win._ouid, seen
    finally:
        _win._load_predict, _win._elo = keep


def test_predict_stale_result_ignored_and_rendered():
    keep = (dict(_win._pred), dict(_win._pred_req), config.WEB_DATA)
    try:
        config.WEB_DATA = True
        _win._pred_req[_OUID] = 2
        ok = core.Prediction(True, p={200: 0.31, 1000: 0.995}, lo=150, hi=420, end_text="11/12(공지)",
                             end_source="notice")
        _win._on_pred_ready(_OUID, (2, ok))
        _win._on_pred_ready(_OUID, (1, core.Prediction(False, "옛 계산")))         # 먼저 띄운 게 나중에 끝남
        t = _win.lb_elo_predict.text()
        assert "200위 안 31%" in t and "1,000위 안 >99%" in t and "150위~420위" in t and "11/12(공지)" in t, t
        assert "검증 전" in t and "보정" not in t, t
        _win._on_pred_ready(_OUID, (2, core.Prediction(False, "랭킹 기록을 모으는 중입니다")))
        assert _win.lb_elo_predict.text() == "랭킹 기록을 모으는 중입니다"
    finally:
        _win._pred, _win._pred_req, config.WEB_DATA = keep
        _win._render_predict()


def test_prediction_logged_once_per_day_and_failure_contained():
    tmp, saved = _loader_db(0)
    keep = (core.predict_for, crashlog.note, getattr(app_main.PredictWorker, "_noted", False))
    noted, got = [], []
    try:
        ok = core.Prediction(True, p={200: 0.2, 1000: 0.9}, lo=180, hi=900, end_text="x", end_source="estimate",
                             season_start=datetime(2026, 9, 10).date(), profile_sn=55)
        core.predict_for = lambda **k: ok
        for _ in range(2):
            w = app_main.PredictWorker(_OUID, 1, [], [], None)
            w.pred_ready.connect(lambda o, p: got.append(p))
            w.run()
        c = store.open_db(config.DB_PATH)
        rows = store.predictions(c, _OUID)
        c.close()
        assert len(rows) == 1 and rows[0]["p200"] == 0.2 and rows[0]["profile_sn"] == 55, rows
        # 계산이 터져도 그 구역만 — crash.log 는 한 번
        app_main.PredictWorker._noted = False
        crashlog.note = lambda kind, e: noted.append(kind)
        core.predict_for = lambda **k: 1 / 0
        for _ in range(2):
            w = app_main.PredictWorker(_OUID, 1, [], [], None)
            w.pred_ready.connect(lambda o, p: got.append(p))
            w.run()
        assert noted == ["predict"], noted
        assert got[-1][1].message == app_main.PredictWorker.FAILED, got[-1]
        # 지우기는 남길 계정 규칙 그대로
        c = store.open_db(config.DB_PATH)
        store.save_prediction(c, "남", datetime.now(), profile_sn=None, season_start=None, end_source="x",
                              p200=0.1, p1000=0.1, lo=1, hi=2)
        store.clear_elo(c, _OUID)
        assert [r["ouid"] for r in store.predictions(c)] == [_OUID]
        c.close()
    finally:
        core.predict_for, crashlog.note, app_main.PredictWorker._noted = keep
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_shutdown_and_clear_stop_predict_workers():
    class _Busy(app_main.PredictWorker):
        def isRunning(self):
            return not self._cancel

        def wait(self, *a):
            return True

    w = _Busy(_OUID, 999, [], [], None)
    _win._pred_workers.append(w)
    try:
        left = _win.shutdown(fast=True)
        assert w in left and w._cancel, "종료 정리 목록에 PredictWorker 가 없다"
        w2 = _Busy(_OUID, 1000, [], [], None)
        _win._pred_workers.append(w2)
        _win.stop_predict_workers()
        assert w2._cancel and _win._pred_workers == [], "지우기 전에 예측 작업자를 멈추지 않았다"
    finally:
        _win._pred_workers = [x for x in _win._pred_workers if x is not w]


def test_season_notice_saved_on_latest():
    orig = updatecheck.check_full
    s = _win._settings()
    keep = (s.value("season/end"), s.value("season/start"))
    loads = []
    keep_lp = _win._load_predict
    _win._load_predict = lambda ouid: loads.append(ouid)
    try:
        notice = (datetime(2026, 11, 12).date(), datetime(2026, 9, 10).date())
        updatecheck.check_full = lambda *a, **k: updatecheck.CheckResult(updatecheck.LATEST, None, notice)
        _win.start_update_check()
        _win._update_worker.run()
        assert _win._season_notice() == notice, _win._season_notice()
        assert loads, "공지가 바뀌면 예측을 다시 계산한다"
        _win._update_worker.run()
        assert len(loads) == 1, "같은 공지로 다시 계산했다"
    finally:
        updatecheck.check_full = orig
        _win._load_predict = keep_lp
        for k, v in zip(("season/end", "season/start"), keep):
            if v is None:
                s.remove(k)
            else:
                s.setValue(k, v)
        _win._set_update_status("")


def test_season_notice_parse_and_hidden_from_notes():
    p = updatecheck.parse_season_notice
    assert p("본문\n<!-- season-end: 2026-11-12 start: 2026-09-10 -->") == (
        datetime(2026, 11, 12).date(), datetime(2026, 9, 10).date())
    assert p("<!-- season-end: 2026-09-01 start: 2026-09-10 -->") is None, "종료가 시작보다 앞"
    assert p("<!-- season-end: 2027-03-01 start: 2026-09-10 -->") is None, "120일 넘음"
    assert p("<!-- season-end: 2026-13-01 start: 2026-09-10 -->") is None
    assert p("") is None
    body = "## 바뀐 점\n- 하나\n<!-- season-end: 2026-11-12 start: 2026-09-10 -->\n- 둘\n"
    assert "season-end" not in updatecheck._changes_excerpt(body)


def test_clear_elo_failure_not_silent():
    import sqlite3 as _sq
    tmp, saved = _loader_db(0)
    keep_t = store.OPEN_TIMEOUT_S
    try:
        with _RankSwitches():
            c = store.open_db(config.DB_PATH)
            store.save_elo(c, "남", 1900.0, 9)
            c.close()
            hold = _sq.connect(str(config.DB_PATH))
            hold.execute("BEGIN EXCLUSIVE")
            store.OPEN_TIMEOUT_S = 0.2
            try:
                done, n = app_main.clear_rank_records(None, None)
            finally:
                hold.rollback()
                hold.close()
                store.OPEN_TIMEOUT_S = keep_t
            assert n is None, "잠겨서 못 지웠는데 지웠다고 했다"
            assert store.clear_elo_pending(config.DB_PATH) is True
            c = store.open_db(config.DB_PATH)
            assert store.elo_history(c, "남") == [], "다음에 켤 때 지우지 않았다"
            c.close()
            assert store.clear_elo_pending(config.DB_PATH) is False
    finally:
        store.OPEN_TIMEOUT_S = keep_t
        config.DB_PATH, config.WEB_DATA = saved
        shutil.rmtree(tmp, ignore_errors=True)


# ── 다시 묻는 동의(1.3.1 사용자 ⑤) ──

def test_notice_versions_split_first_and_reask():
    keep = config.NOTICE_ACCEPTED, config.WEB_DATA, config.RANK_COLLECT
    try:
        config.RANK_COLLECT = True   # 수집을 켠 사람 — 7 이전은 전부 다시 묻는다(끈 사람은 test_notice7_asked_only_when_collecting)
        for acc, needed, pending, track in [(0, True, False, False), (1, True, False, False),
                                            (2, False, True, False), (3, False, True, True),
                                            (4, False, True, True), (5, False, True, True),
                                            (6, False, True, True), (7, False, True, True),
                                            (8, False, True, True), (9, False, True, True),
                                            (10, False, True, True),    # 11 이 올라서 10 동의자도 수집을 켰으면 다시 묻는다
                                            (11, False, True, True),    # 12 가 올라서 11 동의자도(수집을 끈 사람은 토글을 누를 때 — 51단계 5판)
                                            (12, False, True, True),    # 13 이 올라서 12 동의자도 수집을 켰으면(57 ⓪)
                                            (13, False, True, True),    # 14 가 올라서 13 동의자도 수집을 켰으면(57 ③)
                                            (14, False, False, True)]:
            config.NOTICE_ACCEPTED = acc
            assert (config.notice_needed(), config.notice_update_pending(), config.track_allowed()) == \
                (needed, pending, track), acc
        # 1.4.1: 4 = 거래 기록·시세 자동 읽기 — 옛 동의(2·3)는 막지 않고 다시 묻기만, 자동 시세만 4 뒤부터
        # 2.1.1: 5 = 축구장 칩 카드 정보 자동 읽기(랭커 픽은 16단계가 같은 5 를 쓴다)
        # 2.3.1: 6 = 다른 구단주의 사람별 값을 그 시즌 동안(U1) — 옛 동의(5)는 다시 묻기만, 사람별 표만 6 뒤부터
        # 2.5.1: 7 = 포지션 특성 — 6 동의자는 수집을 켰을 때만 다시 묻는다(U6)
        # 2.6.1: 8 = 표 칸 그림 — 수집과 무관하게 2~7 동의자 전원에게 다시 묻는다(NOTICE_COLLECT_ONLY_FROM 도 8)
        assert (config.NOTICE_VERSION, config.TRAIT_NOTICE_VERSION, config.META_NOTICE_VERSION, config.CHIP_NOTICE_VERSION,
                config.PRICE_NOTICE_VERSION, config.TRACK_NOTICE_VERSION, config.NOTICE_BASE_VERSION) == (14, 7, 6, 5, 4, 3, 2)
        # 2.11.1: 11 = 내 계정 최종 순위 확인(B8) · 구단주 요약의 자리별 값(C4) — 둘 다 수집을 켠 사람 몫(COLLECT_ONLY_FROM 은 8 그대로)
        assert config.PREDICT_CHECK_NOTICE_VERSION == config.OWNER_DIST_V2_NOTICE_VERSION == 11
        assert config.NOTICE_COLLECT_ONLY_FROM == 8
        # 2.12.1: 12 = 카드 정보 자동 채우기 — 수집과 무관한 따로 토글이지만 COLLECT_ONLY_FROM 은 8 그대로(토글을 누를 때 묻는다)
        assert config.CARD_BACKFILL_NOTICE_VERSION == 12
        # 2.12.2: 13 = 다른 구단주의 챔스 이상 판수(57 ⓪) — 수집을 켠 사람 몫. 끈 12 동의자에겐 안 묻는다(켤 때)
        assert config.FC_GAMES_NOTICE_VERSION == 13
        # 2.13.1: 14 = 상대 점수 보여 주기·내 특성(수집 몫) + 버튼 자리·넥슨 페이지 안내 — COLLECT_ONLY_FROM 은 8 그대로(U7)
        assert config.OPP_SCORE_NOTICE_VERSION == config.MY_TRAIT_NOTICE_VERSION == 14
        config.RANK_COLLECT, config.NOTICE_ACCEPTED = False, 13
        assert not config.notice_update_pending(), "수집을 끈 13 동의자에게 14 를 다시 물었다(U7)"
        assert "저장 공간" in notice.changes_html(13)
        config.RANK_COLLECT, config.NOTICE_ACCEPTED = False, 12
        assert not config.notice_update_pending(), "수집을 끈 12 동의자에게 수집 몫 안내를 다시 물었다"
        assert "챔피언스 이상에서 뛴 판수" in notice.changes_html(12)
        config.WEB_DATA = config.RANK_COLLECT = True
        for acc, person in ((5, False), (6, True)):
            config.NOTICE_ACCEPTED = acc
            assert config.meta_person_allowed() is person, acc
    finally:
        config.NOTICE_ACCEPTED, config.WEB_DATA, config.RANK_COLLECT = keep


def test_reask_dialog_keeps_current_web_choice():
    keep = (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED)
    saved_env = config.ENV_PATH
    config.ENV_PATH = pathlib.Path(tempfile.mkdtemp()) / ".env"
    try:
        for on in (True, False):
            config.WEB_DATA = on
            dlg = app_main.NoticeDialog(reask=True)
            assert dlg.chk_web.isChecked() is on, "다시 묻기에서 지금 값을 안 채웠다"
            assert not dlg.lb_web_now.isVisibleTo(dlg)
            first = app_main.NoticeDialog()
            assert not first.chk_web.isChecked(), "처음 동의는 빈 칸(D5)"
        # [시작]만 눌러도 홈페이지 데이터·수집이 그대로
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED = True, True, config.NOTICE_BASE_VERSION
        dlg = app_main.NoticeDialog(reask=True)
        dlg.chk_agree.setChecked(True)
        dlg._on_accept()
        assert config.WEB_DATA and config.RANK_COLLECT and config.NOTICE_ACCEPTED == config.NOTICE_VERSION
    finally:
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED = keep
        config.ENV_PATH = saved_env


def test_notice8_asked_when_collect_off():
    """8(표 칸 그림)은 수집과 무관하게 모두에게 해당 — 수집을 끈 7·6 동의자에게도 다시 묻는다. 7 까지는 수집 몫이었다(U6)."""
    keep = config.NOTICE_ACCEPTED, config.RANK_COLLECT
    try:
        config.RANK_COLLECT = False
        for acc in (config.TRAIT_NOTICE_VERSION, config.TRAIT_NOTICE_VERSION - 1):   # 7 · 6
            config.NOTICE_ACCEPTED = acc
            assert config.notice_update_pending() is True, acc
        # 9(상대 순위 구간)는 수집을 켠 사람 몫 — 8 동의자는 수집을 끄면 다시 안 묻고, 켜면 묻는다
        config.NOTICE_ACCEPTED = 8
        assert not config.notice_update_pending()
        config.RANK_COLLECT = True
        assert config.notice_update_pending()
        config.RANK_COLLECT = False
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        assert not config.notice_update_pending()
        assert config.NOTICE_COLLECT_ONLY_FROM == 8, \
            "NOTICE_COLLECT_ONLY_FROM 을 6 으로 되돌리면 수집 끈 6·7 동의자가 안내 8 을 못 본다"
    finally:
        config.NOTICE_ACCEPTED, config.RANK_COLLECT = keep


def test_notice_below6_still_asked_when_not_collecting():
    """RANK_COLLECT 를 조건에 통째로 붙이면 수집을 끈 5 이하 동의자가 시세·칩(4·5)을 영영 못 받는다(검토 A [상])."""
    keep = config.NOTICE_ACCEPTED, config.RANK_COLLECT
    try:
        config.RANK_COLLECT = False
        for acc in range(config.NOTICE_BASE_VERSION, config.NOTICE_COLLECT_ONLY_FROM):
            config.NOTICE_ACCEPTED = acc
            assert config.notice_update_pending(), acc
    finally:
        config.NOTICE_ACCEPTED, config.RANK_COLLECT = keep


def test_reask_shows_only_changes_after_accepted():
    assert max(notice.CHANGES) == config.NOTICE_VERSION, "안내를 올렸는데 바뀐 점(notice.CHANGES)을 안 더했다"
    seven, six, five = notice.changes_html(7), notice.changes_html(6), notice.changes_html(5)
    assert "표 안의 그림" in seven and "카드에 넣은 포지션 특성" not in seven, "7 동의자에게 이미 본 7 을 또 보였다"
    assert "포지션 특성" in six and "슈퍼챔피언스 연속 기간" not in six, "6 동의자에게 이미 본 6 을 또 보였다"
    assert "표 안의 그림" in six, "6 동의자가 8 의 바뀐 점을 못 본다"
    assert "포지션 특성" in five and "슈퍼챔피언스" in five, "5 동의자가 6 의 바뀐 점을 못 본다"
    assert notice.changes_html(config.NOTICE_VERSION) == ""


# 렌더한 안내 전문(숫자는 config 상수로 그린다)의 해시 — 버전과 짝. 글·상수가 바뀌어 FAIL 이면:
# 실질 변경이면 config.NOTICE_VERSION 을 올리고 새 줄을 더한다 · 맞춤법·표현만이면 이 버전의 값만 고친다.
NOTICE_GOLDEN = {8: "a3253bc7f91f6821", 9: "5c222c68aff4b67f",   # 8 = 2.6.1 표 칸 그림(7 의 410b2df025725408 은 이미지 서버 문구·CHANGES 8 이 더해져 대체됐다)
                 10: "8ce00c5021fb0806",                          # 10 = 2.10.1 구단주 분포 받기(49단계)
                 11: "75573220bc230e22",                          # 11 = 2.11.1 내 계정 최종 순위 확인(B8) · 구단주 요약의 자리별 값(C4)
                 12: "b9157edc4717602e",                          # 12 = 2.12.1 카드 정보 자동 채우기(51단계) · 55 시즌 FC·구단가치 기록 · '상위 20 연속' 이름 바로잡음(공개 전이라 같은 버전)
                 13: "fc242bfee19fa311",                          # 13 = 2.12.2 다른 구단주의 챔스 이상 판수 범위(57 ⓪ — 보상 100판당)
                 14: "ca182e7bf1bf2efc"}                          # 14 = 2.13.1 상대 점수 보여 주기 · 내 스쿼드 특성 · [정보 › 저장 공간] · 넥슨 페이지 열기(57)


def _notice_digest() -> str:
    import hashlib
    text = (notice.TERMS_HTML + notice.PRIVACY_HTML + notice.WEB_DATA_HTML + notice.RANK_COLLECT_HTML
            + notice.TRAY_HTML + "".join(notice.CHANGES[v] for v in sorted(notice.CHANGES)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_notice_text_bumps_version():
    """숫자를 상수로 그리면 상수만 올려도 버전 없이 동의 내용이 바뀐다(검토 B) — 글·숫자가 바뀌었는데 버전이 그대로면 FAIL."""
    import importlib
    assert NOTICE_GOLDEN.get(config.NOTICE_VERSION) == _notice_digest(), \
        (config.NOTICE_VERSION, _notice_digest(), "안내 글·숫자가 바뀌었다 — 위 NOTICE_GOLDEN 주석대로")
    keep = config.TRAIT_WEB_DAILY_REQ
    try:   # 상수 하나만 바꿔도 잡히나 — 다시 그린다
        config.TRAIT_WEB_DAILY_REQ = keep + 1
        importlib.reload(notice)
        assert _notice_digest() != NOTICE_GOLDEN[config.NOTICE_VERSION], "상수가 바뀌었는데 해시가 같다 — 검사가 비었다"
    finally:
        config.TRAIT_WEB_DAILY_REQ = keep
        importlib.reload(notice)


def _collect_on_env(dlg_log, notice_cls):
    """[정보] 수집 켜기 — maintain·sync·다시 묻는 창을 기록으로 바꿔 끼운다."""
    keep = (_win._maintain_rank, _win.sync_ranker_pick_data, app_main.NoticeDialog)
    _win._maintain_rank = lambda enabled=None, **k: dlg_log.append(("maintain", enabled, config.RANK_COLLECT))
    _win.sync_ranker_pick_data = lambda: dlg_log.append(("sync",))
    app_main.NoticeDialog = notice_cls
    return keep


def _collect_on_restore(keep):
    _win._maintain_rank, _win.sync_ranker_pick_data, app_main.NoticeDialog = keep


def test_collect_on_asks_notice7():
    log = []

    class _Notice:
        def __init__(self, parent=None, reask=False):
            log.append(("ask", parent, reask, config.RANK_COLLECT))

        def exec(self):
            return app_main.QDialog.DialogCode.Rejected

    keep = _collect_on_env(log, _Notice)
    try:
        with _RankSwitches(web=True, collect=False) as sw:
            config.NOTICE_ACCEPTED = config.TRAIT_NOTICE_VERSION - 1
            dlg = app_main.AboutDialog(_win)
            dlg._sched = lambda: None
            dlg.chk_rank.setChecked(True)
            asks = [e for e in log if e[0] == "ask"]
            assert len(asks) == 1 and asks[0][1] is dlg and asks[0][2] and asks[0][3], (log, "부모·다시 묻기·수집 켠 뒤")
            assert [e[0] for e in log][:2] == ["maintain", "ask"], ("토글 처리를 다 한 뒤 맨 끝에서 묻는다", log)
            assert config.RANK_COLLECT and not config.trait_allowed(), "거절하면 수집은 켜진 채 특성만 막힌다"
            assert "켰습니다" in dlg.lb_msg.text()
            dlg.deleteLater()
            # 7 동의자도 안내 8(표 칸 그림 — 2~7 동의자 전원에게 한 번 다시 묻는다)을 아직 못 봤으니 수집을 켤 때 묻는다
            sw.write(True, False)
            config.NOTICE_ACCEPTED = config.TRAIT_NOTICE_VERSION
            log.clear()
            dlg = app_main.AboutDialog(_win)
            dlg._sched = lambda: None
            dlg.chk_rank.setChecked(True)
            assert len([e for e in log if e[0] == "ask"]) == 1, ("7 동의자는 8 을 보지 못했다", log)
            dlg.deleteLater()
            # 최신(8)에 동의했으면 · 끌 때는 묻지 않는다
            for acc, on in ((config.NOTICE_VERSION, True), (config.TRAIT_NOTICE_VERSION - 1, False)):
                sw.write(True, not on)
                config.NOTICE_ACCEPTED = acc
                log.clear()
                dlg = app_main.AboutDialog(_win)
                dlg._sched = lambda: None
                dlg.chk_rank.setChecked(on)
                assert not [e for e in log if e[0] == "ask"], (acc, on, log)
                dlg.deleteLater()
    finally:
        _collect_on_restore(keep)


def test_collect_on_notice_web_off_consistent():
    """다시 묻는 창에서 웹 데이터 체크를 끄고 동의 → set_web_data(False) 가 수집까지 끈다 — 체크·정리·글이 그걸 따른다."""
    log = []

    class _Notice:
        def __init__(self, parent=None, reask=False):
            pass

        def exec(self):
            config.accept_notice(False)
            return app_main.QDialog.DialogCode.Accepted

    keep = _collect_on_env(log, _Notice)
    try:
        with _RankSwitches(web=True, collect=False):
            config.NOTICE_ACCEPTED = config.TRAIT_NOTICE_VERSION - 1
            dlg = app_main.AboutDialog(_win)
            dlg._sched = lambda: None
            dlg.chk_rank.setChecked(True)
            assert config.read_env_switches() == (False, False)
            assert not dlg.chk_web.isChecked() and not dlg.chk_rank.isChecked() and not dlg.chk_rank.isEnabled()
            maint = [e[1] for e in log if e[0] == "maintain"]
            assert maint == [(True, ""), (False, "user")], ("켬 → 끔 한 번씩(중첩이면 끔이 둘)", maint)
            assert [e for e in log if e[0] == "sync"] == [("sync",)], ("뒷처리는 ask_notice_update 한 번", log)
            assert "같이 꺼졌" in dlg.lb_msg.text(), dlg.lb_msg.text()
            dlg.deleteLater()
    finally:
        _collect_on_restore(keep)


def _count_reask(fn):
    calls = []
    keep = (app_main.NoticeDialog, config.NOTICE_ACCEPTED, _win._notice_asked)

    class _Dlg:
        def __init__(self, parent=None, reask=False):
            calls.append(reask)

        def exec(self):
            return app_main.QDialog.DialogCode.Rejected
    app_main.NoticeDialog = _Dlg
    config.NOTICE_ACCEPTED = config.NOTICE_BASE_VERSION
    _win._notice_asked = False
    try:
        fn()
        _app.processEvents()
    finally:
        app_main.NoticeDialog, config.NOTICE_ACCEPTED, _win._notice_asked = keep
    return calls


def test_reask_after_show_normal_once():
    assert _count_reask(lambda: (_win.ask_notice_update_once(), _win.ask_notice_update_once())) == [True], \
        "보이는 창에서 한 번만 물어야 한다"


def test_reask_never_while_hidden():
    def hidden():
        _win.hide()
        try:
            _win.ask_notice_update_once()
        finally:
            _win.show()
    assert _count_reask(hidden) == [], "숨긴 창에서 물었다(게임 중 초점을 뺏는다)"
    # --tray(v2) — 숨긴 창을 만들고 수집·확인은 돈다, 묻지 않는다
    made = []

    class _Win:
        def __init__(self, api):
            made.append(self)

        def __getattr__(self, name):
            if name == "ask_notice_update_once":
                raise AssertionError("--tray 에서 다시 묻기를 걸었다")
            return lambda *a, **k: None

    orig = (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY, config.WEB_DATA,
            app_main._SHELL)
    app_main._setup_app, app_main.MainWindow = (lambda app: None), _Win
    config.NOTICE_ACCEPTED, config.API_KEY, config.WEB_DATA = config.NOTICE_BASE_VERSION, "test_key", True
    try:
        try:
            app_main.main(["app", "--tray"])
        except ModalCalled:
            pass
        assert made and app_main._SHELL.window is made[0], "옛 동의(v2)인데 --tray 가 창을 안 만들었다"
    finally:
        _stop_shell()
        (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY, config.WEB_DATA,
         app_main._SHELL) = orig


def test_reask_on_tray_open_and_second_instance():
    # 트레이 [열기] 와 두 번째 실행의 "창 앞으로"는 같은 show_window — 보인 뒤 한 번
    sh = tray.AppShell(_app)
    sh.window = _win          # attach_window 는 _win.shell 을 바꿔 다음 테스트의 X 동작까지 바꾼다
    try:
        assert _count_reask(sh.show_window) == [True]
    finally:
        sh.window = None


def test_go_home_after_long_hide():
    # 74 ② — 오래 숨겼다 열면 대시보드 · 내려놓은 뒤 다시 읽기가 돌아갈 보던 메뉴(_restore)도 지운다 · 검색 화면은 그대로
    keep_idx, keep_restore = _win.stack.currentIndex(), _win._restore
    try:
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        _win._go_page("경기 목록")
        assert _win._current_page_name() == "경기 목록"   # 옮기기 전 — 막지 않았으면 여기 남는다
        _win._restore = {"page": "경기 목록", "season": "x"}
        _win.go_home_after_long_hide()
        assert _win._current_page_name() == "대시보드", _win._current_page_name()
        assert _win._restore == {"season": "x"}, "다시 읽기가 끝나면 보던 메뉴로 되돌아간다"
        _win.stack.setCurrentIndex(_win.PAGE_SEARCH)
        _win.go_home_after_long_hide()
        assert _win.stack.currentIndex() == _win.PAGE_SEARCH, "계정을 안 연 검색 화면에서 메인으로 넘겼다"
    finally:
        _win._restore = keep_restore
        _win._go_page("대시보드")
        _win.stack.setCurrentIndex(keep_idx)


def test_dialog_origin_centers_and_clamps():
    # 메인 창 가운데 · 모니터 밖으로 나가면 안으로
    assert app_main.dialog_origin((2000, 100, 1200, 800), (1920, 0, 1920, 1040), 400, 300) == (2400, 350)
    assert app_main.dialog_origin((3500, 900, 400, 200), (1920, 0, 1920, 1040), 600, 500) == (3240, 540)
    assert app_main.dialog_origin((-50, -50, 300, 200), (0, 0, 1920, 1040), 600, 500) == (0, 0)


def test_dialog_follows_minimized_main_window():
    """윈도우는 최소화된 창의 좌표를 화면 밖(-32000)으로 보낸다 — 그때도 창이 돌아올 자리 위에(2026-10-06 사용자).
    offscreen 은 이 좌표를 흉내 내지 않아 가짜로 만든다."""
    from PySide6.QtCore import QRect
    host = app_main.QWidget()
    host.isMinimized = lambda: True
    host.frameGeometry = lambda: QRect(-32000, -32000, 160, 28)
    host.normalGeometry = lambda: QRect(300, 200, 1200, 700)
    d = app_main.QDialog(host)
    app_main.fit_to_screen(d, 400, 300)
    fw, fh = app_main.FRAME_ALLOWANCE
    assert (d.x(), d.y()) == (300 + (1200 - 400 - fw) // 2, 200 + (700 - 300 - fh) // 2), (d.x(), d.y())
    host.isMinimized = lambda: False
    host.frameGeometry = lambda: QRect(100, 50, 1000, 600)
    app_main.fit_to_screen(d, 400, 300)
    assert (d.x(), d.y()) == (100 + (1000 - 400 - fw) // 2, 50 + (600 - 300 - fh) // 2), (d.x(), d.y())
    d.deleteLater()
    host.deleteLater()


def test_notice_reask_waits_while_minimized():
    keep = config.NOTICE_ACCEPTED, app_main.NoticeDialog.exec, _win._notice_asked, _win.isMinimized
    asked = []
    try:
        config.NOTICE_ACCEPTED = config.NOTICE_BASE_VERSION
        app_main.NoticeDialog.exec = lambda self: asked.append(1) or 0
        _win._notice_asked = False
        _win.isMinimized = lambda: True
        _win.ask_notice_update_once()
        assert asked == [] and _win._notice_after_restore, "최소화 중에 다시 묻는 안내를 띄웠다"
        _win.isMinimized = lambda: False
        _win.changeEvent(QEvent(QEvent.Type.WindowStateChange))   # 최소화에서 돌아왔다
        for _ in range(3):
            _app.processEvents()
        assert asked == [1] and not _win._notice_after_restore, asked
    finally:
        config.NOTICE_ACCEPTED, app_main.NoticeDialog.exec, _win._notice_asked = keep[:3]
        _win.isMinimized = keep[3]
        _win._notice_after_restore = False


# ── 거래 기록 · 내 계정(1.4.1 · 11단계) ─────────────────────────────────────────

class _TradeKey:
    """config.API_KEY 를 테스트 키로 바꾸고 거래 표·상태를 비운 채 시작 — 끝나면 되돌린다."""

    def __init__(self, key="ui-test-key"):
        self.key = key

    def __enter__(self):
        self.keep = config.API_KEY
        config.API_KEY = self.key
        c = store.open_db(config.DB_PATH)
        with c:
            c.execute("DELETE FROM trades")
            c.execute("DELETE FROM trade_state")
        c.close()
        return self

    def state(self, **kv):
        c = store.open_db(config.DB_PATH)
        store.set_trade_state(c, **kv)
        c.close()

    def ready(self, bought=(), **kv):
        """지금 키로 다 받은 상태 + 산 (카드, 강화). 날짜는 오늘 기준 — 고정 날짜(09-30)였을 때 2주 뒤 "최근 구매" 창을
        벗어나 test_price_loader_targets_held_only 가 날짜만으로 깨졌다(2026-10-14~)."""
        day = lambda n: (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%dT10:00:00")
        c = store.open_db(config.DB_PATH)
        store.save_trades(c, "buy", [{"tradeDate": day(2), "saleSn": f"b{i}", "spid": s,
                                      "grade": g, "value": 100} for i, (s, g) in enumerate(bought)])
        store.save_trades(c, "sell", [{"tradeDate": day(3), "saleSn": "s0", "spid": 1,
                                       "grade": 1, "value": 50}])
        store.set_trade_state(c, key_fp=store.key_fingerprint(self.key), done_buy="1", done_sell="1", **kv)
        c.close()

    def __exit__(self, *exc):
        config.API_KEY = self.keep
        c = store.open_db(config.DB_PATH)
        with c:
            c.execute("DELETE FROM trades")
            c.execute("DELETE FROM trade_state")
        c.close()
        _win._trade_result = None


def _my_cards(n=3):
    me = next(p for p in _DETAILS[0]["matchInfo"] if p.get("ouid") == _OUID)
    return [(p["spId"], p["spGrade"]) for p in me["player"][:n]]


def _trade_view():
    _win._render_trades()
    vis = {n: getattr(_win, f"btn_trade_{n}").isVisibleTo(_win.lb_trade_banner.parentWidget())
           for n in ("mine", "yes", "change")}
    return _win.lb_trade_banner.text(), _win.lb_trade_hint.text(), _win.lb_trade_status.text(), vis


def test_trade_page_in_nav():
    views = _views_of_nav(app_main.MainWindow.NAV)
    assert ("스쿼드·이적", "가계부") in views and app_main.MainWindow.KEY_OF_VIEW[("스쿼드·이적", "가계부")] == "trades"
    assert ("스쿼드·이적", "타임라인") in views and \
        app_main.MainWindow.KEY_OF_VIEW[("스쿼드·이적", "타임라인")] == "timeline"


def _my_starters(n=3):
    me = next(p for p in _DETAILS[0]["matchInfo"] if p.get("ouid") == _OUID)
    return [(p["spId"], p["spGrade"]) for p in me["player"] if p.get("spPosition") != 28][:n]


def test_timeline_trades_only_for_my_account():
    """타임라인 — 거래는 내 계정으로 정했을 때만 붙는다(남의 경기에 내 거래 금지)."""
    with _TradeKey() as k:
        k.ready(bought=_my_starters(2))
        _win._render_timeline()
        rows = _win.tbl_timeline.rowCount()
        kinds = {_win.tbl_timeline.item(r, 2).text() for r in range(rows)}
        assert rows and "구매" not in kinds and "내 계정에서만" in _win.lb_timeline_note.text(), (kinds,)
        assert all("추정" in t for t in kinds if t.startswith("첫 출전")), kinds
        k.state(my_ouid=_OUID)
        _win._render_timeline()
        kinds = {_win.tbl_timeline.item(r, 2).text() for r in range(_win.tbl_timeline.rowCount())}
        assert "구매" in kinds and "내 계정에서만" not in _win.lb_timeline_note.text(), kinds


def test_trade_events_redraw_timeline():
    """거래가 바뀌는 길(받기 끝 · 지움 · 내 계정 지정)은 타임라인도 다시 그린다 — 가계부만 다시 그리면 타임라인에 옛 거래가 남는다."""
    keep = _win._trade_again
    try:
        with _TradeKey() as k:
            k.ready(bought=_my_starters(2))
            _win._render_timeline()
            kinds = {_win.tbl_timeline.item(r, 2).text() for r in range(_win.tbl_timeline.rowCount())}
            assert "구매" not in kinds, kinds
            k.state(my_ouid=_OUID)                     # 다른 실행본이 정했거나 받기가 끝난 것처럼 — DB 만 바뀜
            _win._trade_again = False
            _win._on_trade_thread_finished()
            kinds = {_win.tbl_timeline.item(r, 2).text() for r in range(_win.tbl_timeline.rowCount())}
            assert "구매" in kinds, "거래 받기가 끝났는데 타임라인이 그대로다"
    finally:
        _win._trade_again = keep


def test_ledger_only_for_my_account():
    with _TradeKey() as k:
        k.ready(bought=_my_starters(2))
        _trade_view()
        assert _win.box_ledger.isHidden(), "내 계정을 정하기 전에 가계부가 보였다"
        k.state(my_ouid=_OUID)
        _trade_view()
        assert not _win.box_ledger.isHidden()
        assert "구매 2건" in _win.lb_ledger["spent"].text(), _win.lb_ledger["spent"].text()
        assert "판매 1건" in _win.lb_ledger["income"].text()
        assert "취득가" not in _win.lb_ledger["realized"].text()
        k.state(key_fp=store.key_fingerprint("other-key"))        # 키가 바뀌었는데 로더 전 — 옛 주인 가계부 금지
        _trade_view()
        assert _win.box_ledger.isHidden()


def test_ledger_incomplete_blanks_realized():
    with _TradeKey() as k:
        k.ready(bought=_my_starters(1), my_ouid=_OUID)
        k.state(done_sell=None)                                    # 옛 판매를 아직 다 못 받음
        _trade_view()
        assert "받는 중" in _win.lb_ledger["realized"].text(), _win.lb_ledger["realized"].text()


def test_price_partial_label():
    v = core.ledger(core.build_timeline([], "x", []), {}).held   # 빈 묶음
    assert app_main.MainWindow._valued_text(v) == "없음"

    class _V:
        count, priced, cost, gain = 3, 1, 300, 50
    text = app_main.MainWindow._valued_text(_V())
    assert "3장 중 1장 평가" in text and "+" in text, text   # 부분합을 전체처럼 보이지 않게


class _FakePriceLoader:
    made: list = []

    def __init__(self, spids):
        self.spids = list(spids)
        self.finished = _Sig()
        _FakePriceLoader.made.append(self)

    def start(self):
        pass

    def isRunning(self):
        return False


class _Sig:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self):
        for fn in self.slots:
            fn()


def test_price_loader_targets_held_only():
    """가계부를 그릴 때 시세는 보유 중·최근 구매 카드만 — 동의·웹 데이터 뒤, 거래 받기가 도는 동안은 미룬다."""
    keep = (app_main.PriceLoader, config.price_auto_allowed, _win._price_tried_on, _win._price_loader, _win._trade_loader)
    app_main.PriceLoader = _FakePriceLoader
    _FakePriceLoader.made = []
    try:
        with _TradeKey() as k:
            held = _my_starters(2)
            k.ready(bought=held, my_ouid=_OUID)
            c = store.open_db(config.DB_PATH)
            store.save_trades(c, "buy", [{"tradeDate": "2023-01-01T10:00:00", "saleSn": "never", "spid": 77,
                                          "grade": 1, "value": 5}])   # 산 뒤 한 번도 안 씀 — 시세 안 읽는다
            c.close()
            config.price_auto_allowed = lambda: False
            _win._price_tried_on = None
            _trade_view()
            assert not _FakePriceLoader.made, "동의·웹 데이터 없이 시세를 읽었다"
            config.price_auto_allowed = lambda: True

            class _Busy:
                def isRunning(self):
                    return True
            _win._trade_loader = _Busy()
            _trade_view()
            assert not _FakePriceLoader.made, "거래 받기가 도는 중에 시세를 띄웠다"
            _win._trade_loader = None
            _trade_view()
            assert len(_FakePriceLoader.made) == 1, _FakePriceLoader.made
            assert set(_FakePriceLoader.made[0].spids) == {s for s, _ in held}, _FakePriceLoader.made[0].spids
            _trade_view()
            assert len(_FakePriceLoader.made) == 1, "같은 날 다시 그릴 때 또 띄웠다"
            # 시세를 다 읽으면 가계부를 다시 그린다 — 안 그러면 "시세 없음"이 다음 검색까지 남는다
            assert "시세 없음" in _win.lb_ledger["recent"].text(), _win.lb_ledger["recent"].text()  # 이틀 전 구매 = 최근 구매
            c = store.open_db(config.DB_PATH)
            for s, _g in held:
                store.save_card_prices(c, s, {g: 10 ** 12 for g in range(1, 14)}, datetime.now().date().isoformat())
            c.close()
            _win._dirty.discard("trades")
            _FakePriceLoader.made[0].finished.emit()
            assert "평가 " in _win.lb_ledger["recent"].text(), _win.lb_ledger["recent"].text()
    finally:
        (app_main.PriceLoader, config.price_auto_allowed, _win._price_tried_on, _win._price_loader,
         _win._trade_loader) = keep


def test_my_account_banner():
    with _TradeKey() as k:
        k.ready(bought=_my_cards(2))
        banner, hint, status, vis = _trade_view()
        assert "이 계정(테스트구단주)이 내 계정인가요" in banner and vis == {"mine": True, "yes": False, "change": False}, (banner, vis)
        assert "%" in hint and "참고" in hint, hint
        last = datetime.now() - timedelta(days=2)   # _TradeKey.ready 의 구매일(오늘 기준)
        assert f"마지막 거래 {last.month}월 {last.day}일" in status and "구매 2 · 판매 1건" in status, status
        _win.btn_trade_mine.click()
        assert store.trade_state(store.open_db(config.DB_PATH)).get("my_ouid") == _OUID
        banner, hint, status, vis = _trade_view()
        assert banner.startswith("내 계정:") and vis["change"] and not vis["mine"] and not hint, (banner, vis, hint)
        # 다른 계정이 내 계정으로 정해져 있으면 거래는 안 붙인다
        k.state(my_ouid="someone-else")
        banner, hint, status, vis = _trade_view()
        assert "내 계정(" in banner and "에서만" in banner and not status and vis["change"], (banner, status)
        _win.btn_trade_change.click()
        banner, *_ = _trade_view()
        assert "내 계정인가요" in banner, banner


def test_hint_needs_trades():
    with _TradeKey() as k:
        k.ready(bought=_my_cards(2))
        k.state(done_buy=None)  # 옛 거래를 아직 다 못 받았다
        banner, hint, status, _vis = _trade_view()
        assert "받는 중" in hint and "%" not in hint, hint
        assert "옛 거래를 아직 다 못 받음" in status, status


def test_trade_screen_key_checking():
    with _TradeKey() as k:
        k.ready(bought=_my_cards(2), my_ouid=_OUID)
        k.state(key_fp=store.key_fingerprint("old-key"))  # 로더가 아직 새 키를 못 봤다
        banner, hint, status, vis = _trade_view()
        assert "API 키 확인 중" in banner and not any(vis.values()) and not hint and not status, (banner, vis, status)


def test_key_change_asks_confirm_without_blocking_fetch():
    with _TradeKey() as k:
        k.ready(bought=_my_cards(2), my_ouid_unconfirmed=_OUID)
        banner, hint, status, vis = _trade_view()
        assert "API 키가 바뀌었습니다" in banner and vis == {"mine": False, "yes": True, "change": True}, (banner, vis)
        assert "%" in hint and "구매 2" in status, (hint, status)   # 답을 안 해도 받은 거래는 보인다
        k.state(done_sell=None)
        assert "다시 받는 중" in _trade_view()[1]
        _win.btn_trade_yes.click()
        st = store.trade_state(store.open_db(config.DB_PATH))
        assert st.get("my_ouid") == _OUID and "my_ouid_unconfirmed" not in st, st


class _FakeTradeApi:
    def __init__(self):
        self.calls, self.keys = [], []

    def set_key(self, key):
        self.keys.append(key)

    def get_trades(self, kind, offset=0, limit=100):
        self.calls.append((kind, offset))
        if offset:
            return []
        return [{"tradeDate": "2026-10-01T09:00:00", "saleSn": f"new-{kind}", "spid": 5, "grade": 2, "value": 9}]


def test_key_change_starts_loader_same_day():
    """키를 바꾼 직후 — 오늘 이미 받았어도(fetched_at) 새 키로 다시 받는다("확인 중"이 다음 날까지 안 풀리지 않게)."""
    keep = _win._api, app_main.ApiKeyDialog, _win._trade_loader
    with _TradeKey("old-key") as k:
        k.ready(bought=_my_cards(1), my_ouid=_OUID, fetched_at=datetime.now().date().isoformat())
        api = _win._api = _FakeTradeApi()

        class Accept:
            def __init__(self, *a, **kw):
                pass

            def exec(self):
                config.API_KEY = "new-key"
                return app_main.QDialog.DialogCode.Accepted
        app_main.ApiKeyDialog = Accept
        try:
            _win._trade_loader = None
            _win._ask_new_key("만료")
            ld = _win._trade_loader
            assert isinstance(ld, app_main.TradeLoader) and api.keys == ["new-key"], (ld, api.keys)
            wiped = []
            ld.wiped.connect(lambda: wiped.append(1))
            ld.run()  # QThread.start 는 막혀 있다 — 같은 스레드에서
            st = store.trade_state(store.open_db(config.DB_PATH))
            assert wiped and api.calls, (wiped, api.calls)
            assert st["key_fp"] == store.key_fingerprint("new-key") and st.get("my_ouid_unconfirmed") == _OUID, st
            assert "b0" not in {r[0] for r in store.open_db(config.DB_PATH).execute("SELECT sale_sn FROM trades")}
        finally:
            _win._api, app_main.ApiKeyDialog, _win._trade_loader = keep


def test_trade_key_change_via_env():
    """.env 를 손으로 고쳐 다시 켠 경우 — 키 창을 안 거쳐도 로더가 지문으로 잡는다."""
    with _TradeKey("first-key") as k:
        k.ready(bought=_my_cards(1), my_ouid=_OUID)
        config.API_KEY = "edited-in-env"
        api = _FakeTradeApi()
        ld = app_main.TradeLoader(api)
        got = []
        ld.done.connect(got.append)
        ld.run()
        assert got and got[0].wiped and got[0].complete, got
        assert store.trade_state(store.open_db(config.DB_PATH))["key_fp"] == store.key_fingerprint("edited-in-env")


def test_trade_wipe_clears_screen():
    """지웠음 신호 → 거래 화면을 다시 읽는다 — 지연 그리기가 들고 있던 옛 주인 거래가 남지 않게."""
    keep = _win.LAZY_RENDER
    _win.LAZY_RENDER = True
    try:
        with _TradeKey() as k:
            k.ready(bought=_my_cards(2), my_ouid=_OUID)
            _win._go_page("대시보드")
            _win._dirty.discard("trades")
            _win._dirty.discard("timeline")
            _win._on_trades_wiped()
            assert "trades" in _win._dirty, "보이지 않는 거래 화면을 낡음으로 안 표시했다"
            assert "timeline" in _win._dirty, "타임라인이 옛 주인 거래를 들고 있다"
            _win._go_page("스쿼드·이적", "가계부")      # 열면 다시 그린다
            assert "trades" not in _win._dirty
            assert _win.lb_trade_banner.text().startswith("내 계정:"), _win.lb_trade_banner.text()
            k.state(key_fp=store.key_fingerprint("other"))   # 보이는 중에 지웠음 — 바로 다시 읽는다
            _win._on_trades_wiped()
            assert "API 키 확인 중" in _win.lb_trade_banner.text(), _win.lb_trade_banner.text()
    finally:
        _win.LAZY_RENDER = keep
        _win._go_page("대시보드")


class _FakeThread:
    def __init__(self, name, log, running=True):
        self.name, self.log, self.running = name, log, running

    def isRunning(self):
        return self.running

    def cancel(self):
        self.log.append(f"stop {self.name}")

    def wait(self, ms):
        self.log.append(f"wait {self.name}")
        return True

    def terminate(self):
        self.log.append(f"kill {self.name}")


def test_trade_yields_to_new_search():
    keep = _win._trade_loader, _win._loader, _win._compare_loader, _win._api
    log = []
    try:
        _win._api = _FakeTradeApi()
        _win._trade_loader = _FakeThread("trade", log)
        _win._loader = None
        _win._api_search("다른구단주")
        assert log == ["stop trade"], log           # 쪽 사이에서 멈추게만 — 기다리지 않는다
        assert isinstance(_win._loader, app_main.MatchLoader)
        # 검색 스레드가 끝나면(finished — QThread.start 는 막혀 있어 손으로 낸다) 거래 받기를 잇는다
        with _TradeKey():
            _win._trade_loader = None
            _win._loader.finished.emit()
            assert isinstance(_win._trade_loader, app_main.TradeLoader), "검색이 끝났는데 거래 받기를 안 이었다"
            # 구단주 비교도 같은 규칙 — 시작할 때 양보, 끝나면 잇기
            _win._trade_loader = _FakeThread("trade-c", log)
            _win._loader, _win._compare_loader = None, None
            _win.ed_compare_nick.setText("비교상대")
            _win._on_compare_search()
            assert "stop trade-c" in log and isinstance(_win._compare_loader, app_main.MatchLoader), log
            _win._trade_loader = None
            _win._compare_loader.finished.emit()
            assert isinstance(_win._trade_loader, app_main.TradeLoader), "비교가 끝났는데 거래 받기를 안 이었다"
            _win.ed_compare_nick.setText("")
            _win.btn_compare.setEnabled(True)
        # 검색이 도는 동안엔 다시 안 띄운다 — 끝나면(finished → start_trades) 이어 받는다
        _win._trade_loader = None
        _win._loader = _FakeThread("search", log)
        with _TradeKey():
            _win.start_trades()
            assert _win._trade_loader is None, "검색 중에 거래 받기를 띄웠다"
            _win._loader.running = False
            _win.start_trades()
            assert isinstance(_win._trade_loader, app_main.TradeLoader), "검색이 끝났는데 안 띄웠다"
            # 도는 중에 또 부르면 끝난 뒤 한 번 더(키 바꿈 등) — 화면 스레드에서 wait() 안 함
            _win._trade_loader = _FakeThread("trade2", log)
            _win.start_trades()
            assert _win._trade_again and "wait trade2" not in log
    finally:
        _win._trade_loader, _win._loader, _win._compare_loader, _win._api = keep
        _win._trade_again = False
        _win._set_busy(False)


def test_shutdown_requests_all_stops_first():
    """멈춤 요청을 표 전체에 먼저 보내고 그다음 차례로 기다린다 — 하나씩이면 최악이 합이 된다(1.4.1 종료 대기)."""
    names = ("_loader", "_compare_loader", "_trade_loader", "_price_loader")
    keep = {n: getattr(_win, n) for n in names}
    log = []
    try:
        for n in names:
            setattr(_win, n, _FakeThread(n, log))
        assert _win.shutdown() == []
        stops = [i for i, e in enumerate(log) if e.startswith("stop")]
        waits = [i for i, e in enumerate(log) if e.startswith("wait")]
        assert len(stops) == 4 and len(waits) == 4 and max(stops) < min(waits), log
        assert not any(e.startswith("kill") for e in log), log
        log.clear()
        assert len(_win.shutdown(fast=True)) == 4 and all(e.startswith("stop") for e in log), log
    finally:
        for n, v in keep.items():
            setattr(_win, n, v)


def test_price_tab_fills_cache():
    """선수 카드 [시세] 탭이 읽은 시세는 작업 스레드가 카드 시세 캐시에 넣는다(화면 스레드에서 DB 쓰기 안 함)."""
    keep = playerinfo.fetch_player_info
    playerinfo.fetch_player_info = lambda sp, timeout=10: playerinfo.PlayerInfo(
        sp_id=sp, name="가", ovr=100, height="180cm", prices={0: "-", 1: "308,000 BP", 8: "17,400,000 BP"})
    try:
        ld = app_main.PlayerInfoLoader(424242)
        got = []
        ld.loaded.connect(got.append)
        ld.run()
        c = store.open_db(config.DB_PATH)
        prices = store.load_card_prices(c, [424242])
        c.close()
        assert got and prices == {(424242, 1): (308000, datetime.now().date().isoformat()),
                                  (424242, 8): (17400000, datetime.now().date().isoformat())}, prices
    finally:
        playerinfo.fetch_player_info = keep


def test_user_opened_card_fills_card_info_without_budget():
    """사용자가 연 선수 카드는 급여·OVR 도 캐시에(card_info) — 하루 계수는 안 한다(상한 없음 · ROADMAP B)."""
    keep = playerinfo.fetch_player_info
    playerinfo.fetch_player_info = lambda sp, timeout=10: playerinfo.PlayerInfo(
        sp_id=sp, name="가", position="CM", ovr=119, salary=30, height="180cm", prices={1: "1,000 BP"})
    def used():
        c = store.open_db(config.DB_PATH)
        try:
            return [store.budget_used(c, datetime.now().date().isoformat(), k)
                    for k in (playerinfo.KIND_CHIP, playerinfo.KIND_LEDGER)]
        finally:
            c.close()
    try:
        before = used()
        app_main.PlayerInfoLoader(434343).run()
        c = store.open_db(config.DB_PATH)
        info = store.load_card_info(c, [434343])[434343]
        c.close()
        assert (info["base_ovr"], info["salary"]) == (119, 30) and used() == before, (info, before, used())
    finally:
        playerinfo.fetch_player_info = keep


def test_card_info_loader_reads_once_then_cache():
    """실제 CardInfoLoader — 칩 카드를 읽어 신호로 넘기고(card · done) 칩 몫으로 센다. 같은 날 다시 띄우면 요청 0(캐시)."""
    calls = []
    keep = (playerinfo.fetch_player_info, config.WEB_DATA)

    def fetch(sp, timeout=10):
        calls.append(sp)
        return playerinfo.PlayerInfo(sp_id=sp, name="가", ovr=100, height="180cm", salary=20, prices={1: "2,000 BP"})
    playerinfo.fetch_player_info = fetch
    config.WEB_DATA = True
    try:
        spids = [515151, 515152]
        ld = _RealCardInfoLoader(spids)
        got, done = [], []
        ld.card.connect(got.append)
        ld.done.connect(lambda a, b: done.append((a, b)))
        ld.run()
        assert sorted(i.sp_id for i in got) == spids and done == [(2, 0)] and calls == spids, (got, done, calls)
        c = store.open_db(config.DB_PATH)
        used = store.budget_used(c, datetime.now().date().isoformat(), playerinfo.KIND_CHIP)
        c.close()
        assert used >= 2, used
        ld2 = _RealCardInfoLoader(spids)
        ld2.run()
        assert calls == spids, "같은 날 캐시가 있는데 다시 읽었다"
        # 끊으면 신호를 안 낸다
        ld3, sent = _RealCardInfoLoader([525252]), []
        ld3.done.connect(lambda *a: sent.append(a))
        ld3.cancel()
        ld3.run()
        assert sent == [] and 525252 not in calls
    finally:
        playerinfo.fetch_player_info, config.WEB_DATA = keep


def test_shutdown_stops_pitch_loaders():
    """축구장 칩 카드 정보 · 갈아 끼운 축구장 로더 · ⑨ 로더가 종료 표에 있다(E11) — 빠지면 도는 QThread 를 놓아 죽는다."""
    class _Busy:
        def __init__(self):
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def isRunning(self):
            return not self._cancel

        def wait(self, *_a):
            return True
    a, b, c = _Busy(), _Busy(), _Busy()
    _win._pitch_card_loaders.append(a)
    _win._retired_loaders.append(b)
    _win._position_pitch_loaders.append(c)
    try:
        left = _win.shutdown(fast=True)
        assert all(x in left and x._cancel for x in (a, b, c)), [x in left for x in (a, b, c)]
    finally:
        _win._pitch_card_loaders.remove(a)
        _win._retired_loaders.remove(b)
        _win._position_pitch_loaders.remove(c)


def test_shutdown_waits_icon_loaders():
    """표 칸 그림 로더(도는 것 + 물러난 것)가 종료 표에 있다 — 목록 줄이 빠지면 도는 QThread 를 놓아 프로세스가 죽는다.
    효과 창·기용률 창 표의 로더도 같은 목록(_icon_loaders)이라 이 하나가 셋을 지킨다."""
    class _Busy:
        def __init__(self):
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def isRunning(self):
            return not self._cancel

        def wait(self, *_a):
            return True
    a, b = _Busy(), _Busy()
    keep = list(_win._icon_loaders)
    _win._icon_loaders[:] = [a, b]
    try:
        left = _win.shutdown(fast=True)
        assert all(x in left and x._cancel for x in (a, b)), [x in left for x in (a, b)]
    finally:
        _win._icon_loaders[:] = keep


# ── 1.4.1 13단계 — 랭커 비교 · 랭커 기록 탭 · 패스 스타일 · 어시스트 · 평점 추이 ─────────────────
class _RankerApi:
    """넥슨처럼 데이터 있는 쌍만 — 모든 (카드, 자리)에 표본 n 경기. fail 이면 예외."""

    def __init__(self, n=20, fail=None, only_po=None):
        self.n, self.fail, self.only_po, self.calls = n, fail, only_po, []

    def get_ranker_stats(self, matchtype, pairs):
        self.calls.append(list(pairs))
        if self.fail is not None:
            raise self.fail
        return [{"spid": s, "spPosition": p, "createDate": "2026-10-05T17:30:00",
                 "status": {"shoot": 0.5, "effectiveShoot": 0.25, "goal": 0.1, "passTry": 10.0, "passSuccess": 9.0,
                            "matchCount": self.n}}
                for s, p in pairs if self.only_po is None or p in self.only_po]


class _RankerEnv:
    """임시 DB(하루 캐시가 테스트끼리 안 섞이게) · 가짜 API · 키 — 끝나면 되돌린다."""

    def __init__(self, api, mine=False):
        self.api = api

    def __enter__(self):
        for ld in (_win._ranker_loader, _win._scout_loader):  # 앞에서 띄운 것(첫 그리기 등)이 끝나야 '받는 중'이 안 섞인다
            if ld is not None:
                ld.wait(5000)
        _app.processEvents()
        self.keep = (config.DB_PATH, config.API_KEY, _win._api, _win._ranker_day, _win._ranker_data,
                     _win._ranker_failed, _win._ranker_note)
        config.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "r.db"
        config.API_KEY = "test_key"
        _win._api = self.api
        _win._ranker_day, _win._ranker_data, _win._ranker_failed, _win._ranker_note = None, {}, None, ""
        return self

    def __exit__(self, *exc):
        for ld in (_win._ranker_loader, _win._scout_loader):
            if ld is not None:
                ld.wait(5000)
        (config.DB_PATH, config.API_KEY, _win._api, _win._ranker_day, _win._ranker_data,
         _win._ranker_failed, _win._ranker_note) = self.keep
        return False


def _ranker_view():
    _win._go_page("선수 지표", "랭커 비교")
    _win._dirty.add("rankercmp")
    _win._render_ranker_compare(_win._slice()[1])


def _run_thread(ld):
    """QThread.start 는 막혀 있다 — 스레드 몸통을 여기서 돌리고 끝남 신호를 낸다(진짜와 같은 순서: done → finished)."""
    ld.run()
    ld.finished.emit()


def test_ranker_compare_page_fetches_once_and_blurs_small_samples():
    api = _RankerApi(n=8)   # 랭커 표본 8 < RANKER_MIN_MATCHES — 전부 흐림
    with _RankerEnv(api):
        _ranker_view()
        assert "받는 중" in _win.lb_ranker_status.text(), _win.lb_ranker_status.text()
        ld = _win._ranker_loader
        _run_thread(ld)   # finished → 다시 그리기(_invalidate)
        want = st_mod.ranker_targets(_win._slice()[1], _OUID, config.RANKER_COMPARE_MAX)
        assert [p for c in api.calls for p in c] == want, (api.calls, want)
        assert [(r.sp_id, r.pos) for r in _win.ranker_rows] == want
        assert all(r.ranker_games == 8 for r in _win.ranker_rows), "다 받은 뒤 다시 그리지 않았다"
        assert "받는 중" not in _win.lb_ranker_status.text(), _win.lb_ranker_status.text()
        tb = _win.tbl_ranker
        assert tb.rowCount() == len(want) and tb.columnCount() == len(app_main.MainWindow.RANKER_COLUMNS)
        assert all(tb.item(i, 0).data(Qt.ItemDataRole.UserRole + 1) for i in range(tb.rowCount())), "표본 8 인데 안 흐렸다"
        # 칸은 "나 / 랭커" — 랭커 값 0.50 이 그대로(경기당 — 다시 나누지 않는다)
        col = app_main.MainWindow.RANKER_COLUMNS.index("슛")
        assert tb.item(0, col).text().endswith("/ 0.50"), tb.item(0, col).text()
        # 다시 그려도(같은 날) 새로 안 띄운다
        _ranker_view()
        assert _win._ranker_loader is ld and len(api.calls) == 1
        # 창이 들고 있던 걸 잃어도(날이 바뀐 것처럼) DB 하루 캐시로 — 요청 0
        _win._ranker_day = None
        _ranker_view()
        assert _win._ranker_loader is not ld
        _run_thread(_win._ranker_loader)
        assert len(api.calls) == 1, "같은 날 DB 캐시를 두고 다시 물었다"
        assert all(r.ranker_games == 8 for r in _win.ranker_rows)
    # 랭커 표본이 넉넉하면 내 출전이 충분한 줄은 안 흐린다
    with _RankerEnv(_RankerApi(n=20)):
        _ranker_view()
        _run_thread(_win._ranker_loader)
        tb = _win.tbl_ranker
        for i, r in enumerate(_win.ranker_rows):
            weak = bool(tb.item(i, 0).data(Qt.ItemDataRole.UserRole + 1))
            assert weak == (r.games < core.MIN_PLAYER_GAMES), (i, r.games, weak)


def test_ranker_compare_failure_shows_reason_without_retry_loop():
    api = _RankerApi(fail=app_main.NexonAPIError("API 호출량을 초과했습니다.", code="OPENAPI00007", status=429))
    with _RankerEnv(api):
        _ranker_view()
        ld = _win._ranker_loader
        _run_thread(ld)
        text = _win.lb_ranker_status.text()
        assert "받지 못했습니다" in text and "호출량" in text, text
        _ranker_view()   # 같은 쌍을 되풀이해 묻지 않는다(finished 가 다시 그린 것까지 띄운 로더는 하나)
        assert _win._ranker_loader is ld and len(api.calls) == 1, api.calls
        assert all(r.ranker_games == 0 for r in _win.ranker_rows)
        # 새 데이터(검색·시즌 바꿈)면 다시 해 본다
        _win._render_all()
        _ranker_view()
        assert _win._ranker_loader is not ld


def _open_card(sp_id):
    orig_exec = app_main.QDialog.exec
    app_main.QDialog.exec = lambda self: 0
    try:
        _win._show_player_info(sp_id)
    finally:
        app_main.QDialog.exec = orig_exec
    return _win._last_player_tabs


def test_scout_tab_one_request():
    """선수 카드 [랭커 기록] — 탭을 열 때 포지션 28개를 요청 하나로, 데이터 있는 자리만 표에. 홈페이지 스위치와 무관."""
    sp = st_mod.ranker_targets(_DETAILS, _OUID)[0][0]
    api = _RankerApi(n=20, only_po={0, 25, 27})
    keep_web = config.WEB_DATA
    config.WEB_DATA = False
    try:
        with _RankerEnv(api):
            # 탭을 안 열면 묻지 않는다(창 하나 열 때마다 요청하지 않게) — 창을 닫은 뒤의 탭을 재 보려고 직접 만든다
            tab = _win._build_scout_tab(sp)
            assert not api.calls
            _win._start_scout(tab)
            first = _win._scout_loader
            _run_thread(first)
            assert tab.table.rowCount() == 3, tab.status.text()
            assert len(api.calls) == 1 and api.calls[0] == [(sp, po) for po in range(28)], api.calls
            assert "포지션 3곳" in tab.status.text(), tab.status.text()
            _win._start_scout(tab)
            assert _win._scout_loader is first, "같은 탭을 다시 열 때 또 띄웠다"
            # 같은 날 다른 창 — DB 캐시(빠진 25개 자리 포함)로 요청 0
            tab2 = _win._build_scout_tab(sp)
            _win._start_scout(tab2)
            _run_thread(_win._scout_loader)
            assert tab2.table.rowCount() == 3 and len(api.calls) == 1, api.calls
            # 실제 카드 창 — 세 번째 탭으로 옮기면 띄운다(탭 전환 배선)
            tabs = _open_card(sp)
            assert tabs.tabText(2) == "랭커 기록"
            before = _win._scout_loader
            tabs.setCurrentIndex(2)
            assert _win._scout_loader is not before, "탭을 열었는데 안 띄웠다"
            _run_thread(_win._scout_loader)
            assert tabs.widget(2).table.rowCount() == 3
    finally:
        config.WEB_DATA = keep_web


def test_pass_style_section_in_tactics():
    _win._go_page("전술·경기 결과")
    _win._render_tactics(_win._slice()[1])
    ps = _win.pass_style
    # 픽스처 4경기 전부 종류 필드가 있고, 하나는 "오류"(승무패 아님)라 뺀다
    wdl = [m for m in _win._slice()[0] if m.result in ("승", "무", "패")]
    assert ps.all.games == len(wdl) == 3, (ps.all.games, len(wdl))
    # 65 M7 — 종류마다 카드(PASS_CARD_COLS 칸) · 성공률 크게 · 경기당 시도/성공 · 성공률 막대(파랑)
    import core_api
    from PySide6.QtWidgets import QLabel
    from widgets import MeterBar
    g, cols = _win.grid_pass, app_main.config.PASS_CARD_COLS
    cards = _win.pass_cards
    assert len(cards) == len(st_mod.PASS_KINDS) == g.count(), (len(cards), g.count())
    for i, (card, k) in enumerate(zip(cards, ps.all.kinds)):
        assert g.itemAtPosition(i // cols, i % cols).widget() is card, i
        texts = [lb.text() for lb in card.findChildren(QLabel)]
        assert texts[0] == k.name, texts
        ok = k.tries >= st_mod.PASS_MIN_TRIES
        assert texts[1] == (f"{k.rate:.0f}%" if ok else app_main.NA), (texts[1], k.rate, k.tries)
        assert texts[2].startswith(f"경기당 시도 {k.tries / ps.all.games:.1f} · 성공 {k.success / ps.all.games:.1f}"), texts[2]
        bar = card.findChild(MeterBar)
        assert abs(bar.frac - (k.rate / 100 if ok else 0.0)) < 1e-9 and (bar.color == app_main.T.BLUE) is ok, \
            (k.name, ok, bar.frac, bar.color)
        # 이긴/진 경기 줄은 표본(MIN_COND) 미만이면 흐림 + 툴팁 — 픽스처는 몇 경기뿐이라 흐리다
        for lb in card.findChildren(QLabel)[3:]:
            assert app_main.T.TEXT_DIM in lb.styleSheet() and lb.toolTip(), (lb.text(), lb.styleSheet())
    assert any(k.tries >= st_mod.PASS_MIN_TRIES for k in ps.all.kinds), "픽스처에 성공률을 내는 종류가 없다 — 위 단언이 NA 만 잰다"
    # 시도 문턱을 못 넘으면 성공률·막대를 숨기고 카드를 흐린다
    keep = core_api.PASS_MIN_TRIES
    core_api.PASS_MIN_TRIES = 10 ** 9
    try:
        _win._render_pass_style(_win._slice()[1])
        for card in _win.pass_cards:
            assert card.findChildren(QLabel)[1].text() == app_main.NA and card.property("weak")
            bar = card.findChild(MeterBar)
            assert bar.frac == 0 and bar.color != app_main.T.BLUE, (bar.frac, bar.color)
    finally:
        core_api.PASS_MIN_TRIES = keep
        _win._render_pass_style(_win._slice()[1])


def test_shotmap_assist_toggle():
    _win._go_page("슛 맵")
    _win.cb_shotmap_side.setCurrentIndex(0)
    _win.chk_shotmap_assist.setChecked(False)
    assert _win.shotmap.assist_lines() == [], "기본은 어시 선이 없어야 한다"
    _win.chk_shotmap_assist.setChecked(True)
    try:
        want = [s for s in st_mod.shot_map(_win._slice()[1], _OUID).shots
                if s.result == 3 and s.assist_x is not None]
        assert want and len(_win.shotmap.assist_lines()) == len(want), (len(_win.shotmap.assist_lines()), len(want))
        assert "어시 있는 골" in _win.lb_shotmap_summary.text()
        _win.cb_shotmap_side.setCurrentIndex(1)   # 상대 슛 — 어시 위치도 내 시점으로 뒤집는다
        opp = st_mod.shot_map(_win._slice()[1], _OUID, mine=False).shots
        a = next(s for s in opp if s.result == 3 and s.assist_y is not None)
        drawn = [s for s in _win.shotmap.assist_lines() if abs(s.x - a.x) < 1e-12]
        assert drawn and abs(drawn[0].assist_y - (1.0 - a.assist_y)) < 1e-12
    finally:
        _win.chk_shotmap_assist.setChecked(False)
        _win.cb_shotmap_side.setCurrentIndex(0)


def test_shotmap_default_unchanged():
    """set_shots(shots) 기본값은 예전 그림 그대로 — 선수 카드 [내 기록]도 같은 함수. 어시 좌표가 실려 와도 안 그린다."""
    sm = st_mod.shot_map(_DETAILS, _OUID)
    bare = [app_main.replace(s, assist_x=None, assist_y=None) for s in sm.shots]
    assert any(s.assist_x is not None for s in sm.shots)
    w = app_main.ShotMapWidget()
    w.resize(560, 460)
    w.set_shots(bare)
    before = w.grab().toImage()
    w.set_shots(sm.shots)
    assert w.grab().toImage() == before, "어시 좌표가 기본 그림을 바꿨다"
    w.set_shots(sm.shots, assists=True)
    assert w.grab().toImage() != before, "assists=True 인데 그림이 같다(선이 안 그려졌다)"
    tabs = _open_card(next(p for p in st_mod.finishing_ranking(_DETAILS, _OUID) if p.shots).sp_id)
    card_map = tabs.widget(1).findChildren(app_main.ShotMapWidget)[0]
    assert card_map.assist_lines() == [], "선수 카드 슛 맵에 어시 선"


def test_my_record_rating_trend():
    import charts
    sp = st_mod.ranker_targets(_DETAILS, _OUID)[0][0]
    tabs = _open_card(sp)
    rec = tabs.widget(1)
    chart = [x for x in rec.findChildren(app_main.QWidget) if x.objectName() == "myRecordRating"][0]
    assert isinstance(chart, charts.AreaTrendChart), type(chart)
    weeks = st_mod.rating_trend(_win._details, _OUID, sp)
    assert [p[1] for p in chart._points] == [w.rating for w in weeks]
    assert chart._weak == [w.weak for w in weeks] and any(chart._weak), chart._weak   # 픽스처는 주 5경기 미만
    chart.resize(500, 220)
    chart.grab()
    assert chart.marks.get("weak") == [i for i, w in enumerate(weeks) if w.weak], chart.marks.get("weak")
    # 출전 없는 카드는 그래프 대신 글자
    rec2 = _win._build_my_record(1)
    lb = [x for x in rec2.findChildren(app_main.QWidget) if x.objectName() == "myRecordRating"][0]
    assert isinstance(lb, app_main.QLabel) and "출전 기록이 없습니다" in lb.text()


# ── 2.1.1 16단계 — 랭커 픽(6) · 선수로 구단주 찾기(12) 배선(진입점 표 E7~E13) ─────────────
class _FakePickLoader:
    """랭커 픽 로더 자리 — 띄운 수·멈춤 요청만 센다. 받기 규칙 자체는 tests/test_rankerpick.py 가 잰다."""
    started = 0

    class _S:  # 이 파일 아래쪽에 _Sig 가 하나 더 있어(인자 없는 emit) 이름을 따로 둔다
        def __init__(self):
            self.slots = []

        def connect(self, f):
            self.slots.append(f)

        def emit(self, *a):
            for f in self.slots:
                f(*a)

    def __init__(self, api, recommend=None):
        self.ranker, self.done, self.finished = self._S(), self._S(), self._S()
        self.running = False
        self.cancelled = False
        self.recommend = recommend

    def start(self):
        _FakePickLoader.started += 1
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def wait(self, *_a):
        return True

    def end(self):
        """스레드가 끝났다 — 진짜와 같은 순서(done → finished)."""
        self.running = False
        self.done.emit(app_main.rankerpick.PickResult())
        self.finished.emit()


def _seed_snapshot(n=3):
    r = rankcollect.open_rank_db()
    try:
        r.execute("DELETE FROM snapshot_rows")
        r.execute("DELETE FROM snapshots")
        r.execute("INSERT INTO snapshots (id, taken_at, row_count, dup_count, season_seq, new_season)"
                  " VALUES (1, ?, ?, 0, 1, 0)", (datetime.now().isoformat(timespec="seconds"), n))
        r.executemany("INSERT INTO snapshot_rows (snapshot_id, rank, profile_sn, nickname, team_color, formation)"
                      " VALUES (1, ?, ?, ?, ?, ?)", [(i + 1, 900 + i, f"랭커{i}", "팀A", "4-2-3-1") for i in range(n)])
        r.commit()
    finally:
        r.close()


class _PickEnv:
    """랭커 픽 테스트의 공통 상태 — 수집 켜짐 · 동의 5 · 키 있음 · 가짜 로더. 끝나면 되돌린다."""

    def __enter__(self):
        self.keep = (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.API_KEY,
                     app_main.RankerPickLoader, _win._pick_loader, _win._pick_result, _win._loader,
                     _win._compare_loader, _win._trade_loader, _win._ranker_loader)
        config.WEB_DATA, config.RANK_COLLECT = True, True
        config.NOTICE_ACCEPTED, config.API_KEY = config.RANKER_PICK_NOTICE_VERSION, "test_key"
        app_main.RankerPickLoader = _FakePickLoader
        _win._pick_loader = _win._loader = _win._compare_loader = _win._trade_loader = _win._ranker_loader = None
        _FakePickLoader.started = 0
        _seed_snapshot()
        return self

    def __exit__(self, *exc):
        _win._go_page("대시보드")
        # rank.db 를 남기지 않는다 — 뒤 테스트(수집 예약)가 "조건이 안 맞으면 rank.db 를 안 만든다"를 잰다
        for suffix in ("", "-wal", "-shm"):
            pathlib.Path(str(config.RANK_DB_PATH) + suffix).unlink(missing_ok=True)
        (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.API_KEY,
         app_main.RankerPickLoader, _win._pick_loader, _win._pick_result, _win._loader,
         _win._compare_loader, _win._trade_loader, _win._ranker_loader) = self.keep
        _win._pick_purge_pending = False
        return False


def test_ranker_pick_gate():
    """E9 — 수집 꺼짐(스냅숏은 남음) · 옛 동의 4 → 요청 0. [안내 보기]로 동의하면 바로 시작(E9b)."""
    with _PickEnv():
        config.RANK_COLLECT = False
        _win._go_page("랭커 픽")
        assert _FakePickLoader.started == 0, "수집이 꺼졌는데 랭커 픽을 받았다"
        assert "랭킹 수집을 켜면" in _win.lb_pick_status.text(), _win.lb_pick_status.text()
        _win._go_page("대시보드")
        config.RANK_COLLECT, config.NOTICE_ACCEPTED = True, 4
        _win._go_page("랭커 픽")
        assert _FakePickLoader.started == 0, "옛 동의(4)인데 받았다"
        assert _win.btn_pick_notice.isVisibleTo(_win) and "동의" in _win.lb_pick_status.text()

        class _Notice:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                config.NOTICE_ACCEPTED = config.NOTICE_VERSION
                return app_main.QDialog.DialogCode.Accepted

        keep = app_main.NoticeDialog
        app_main.NoticeDialog = _Notice
        try:
            _win.btn_pick_notice.click()
        finally:
            app_main.NoticeDialog = keep
        assert _FakePickLoader.started == 1, "동의 직후 시작하지 않았다"
        assert not _win.btn_pick_notice.isVisibleTo(_win)


def test_ranker_pick_only_while_visible():
    """U2 — 그 화면이 보이고 창이 보일 때만. 다른 메뉴 · 숨김(X) · 최소화면 멈추고, 다시 보이면 잇는다."""
    with _PickEnv():
        _win._go_page("랭커 픽")
        assert _FakePickLoader.started == 1
        ld = _win._pick_loader
        _win._go_page("대시보드")
        assert ld.cancelled, "다른 메뉴로 갔는데 계속 받는다"
        ld.end()
        _win._go_page("랭커 픽")
        assert _FakePickLoader.started == 2
        ld = _win._pick_loader
        _win.hide()
        assert ld.cancelled, "창을 숨겼는데(트레이) 계속 받는다"
        ld.end()
        _win.show()
        _app.processEvents()
        assert _FakePickLoader.started == 3, "다시 보였는데 잇지 않았다"
        ld = _win._pick_loader
        _win.showMinimized()
        _app.processEvents()
        assert ld.cancelled, "최소화했는데 계속 받는다"
        ld.end()
        _win.showNormal()
        _app.processEvents()
        assert _FakePickLoader.started == 4, "최소화가 풀렸는데 잇지 않았다"


def test_ranker_pick_yields_and_resumes():
    """⑧ 오픈API 백그라운드는 하나씩 — 검색·거래·랭커 기록이 돌면 시작 안 함 · 거래가 시작되면 양보 · 끝나면 잇는다(E9b)."""
    with _PickEnv():
        log = []
        _win._loader = _FakeThread("검색", log)
        _win._go_page("랭커 픽")
        assert _FakePickLoader.started == 0, "검색이 도는데 시작했다"
        _win._loader.running = False
        _win.start_ranker_pick()
        assert _FakePickLoader.started == 1
        ld = _win._pick_loader
        _win._yield_trades()                      # 새 검색·비교가 시작된다
        assert ld.cancelled, "새 검색에 양보하지 않았다"
        ld.end()
        _win._trade_loader = _FakeThread("거래", log)
        _win.start_ranker_pick()
        assert _FakePickLoader.started == 1, "거래가 도는데 시작했다"
        _win._trade_loader.running = False
        _win._trade_again = False
        _win._on_trade_thread_finished()
        assert _FakePickLoader.started == 2, "거래가 끝났는데 잇지 않았다"


def test_ranker_pick_renders_summary():
    """받아 둔 랭커 경기 → 화면(요청 없이 DB 에서) — 인원 줄 · 팀컬러 표 · 카드 표 · 내 최근 선발 축구장."""
    with _PickEnv():
        config.RANK_COLLECT = False      # 받지 않게 — 이미 받아 둔 것만 그린다
        conn = store.open_db(config.DB_PATH)
        try:
            d = json.loads(json.dumps(_DETAILS[0]))
            d["matchId"], d["matchDate"] = "pickm0", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            side = d["matchInfo"][0]
            store.save_matches(conn, [d])
            store.mark_ranker_match(conn, "pickm0", datetime.now().date().isoformat())
            store.save_ranker_squad(conn, 900, nickname="랭커0", ouid=side["ouid"], rank=1, match_id="pickm0",
                                    match_day=d["matchDate"][:10], fetched_at=datetime.now().isoformat(), fail=None,
                                    source=store.PICK)
        finally:
            conn.close()
        try:
            _win._go_page("랭커 픽")
            s = _win.pick_summary
            assert s is not None and (s.total, s.used, s.pending) == (3, 1, 2), s
            assert _win.pick_colors_list.rowCount() == 1 and _win.pick_cards_list.rowCount() > 0
            assert "3명 중 1명" in _win.lb_pick_summary.text(), _win.lb_pick_summary.text()
            # 67 R10 줄 칩 — 고른 줄의 카드만 · 줄 안 순위 · 두 번 누르면 그 카드
            lst, chips = _win.pick_cards_list, _win.pick_line_chips
            every = lst.all_rows()
            # "많이 쓴 강화" 칸은 강화 배지로 — 화면이 grade 를 넘겨야 배지가 된다(빼면 글자만)
            gcells = [c for r in every for c in r.cells if c.label == "많이 쓴 강화" and c.text != app_main.NA]
            assert gcells and all(c.grade == int(c.text) for c in gcells), gcells[:3]
            lines = [ln for ln in dict.fromkeys(r.sort["line"] for r in every)]
            assert len(lines) > 1, "줄이 하나뿐이면 거르기가 아무것도 못 잰다"
            chips.setCurrentIndex(chips.findData(lines[-1]))
            assert lst.rowCount() == sum(1 for r in every if r.sort["line"] == lines[-1]) < len(every)
            assert {r.sort["line"] for r in lst.rows()} == {lines[-1]} and lst.row(0).rank == 1
            chips.setCurrentIndex(0)
            assert lst.rowCount() == len(every)
            got = []
            keep_info = _win._show_player_info
            _win._show_player_info = got.append
            try:
                lst.doubleClicked.emit(lst.model().index(0, 0))
            finally:
                _win._show_player_info = keep_info
            assert got == [every[0].key] and isinstance(every[0].key, int), got
            _check_metric_lists([lst])
            assert _win.box_pick_pitch.count() == 1 and isinstance(_win.box_pick_pitch.itemAt(0).widget(),
                                                                   app_main.PitchWidget)
        finally:
            conn = store.open_db(config.DB_PATH)
            try:
                store.purge_ranker_data(conn, everything=True)
            finally:
                conn.close()


def test_ranker_pick_share_tables_most_common_first():
    """[픽] 팀컬러·포메이션 표는 많은 순 그대로 — 정렬을 켜 두면 머리글 기본 정렬(이름)로 다시 섞였다(17단계 실화면)."""
    with _PickEnv():
        config.RANK_COLLECT = False
        r = rankcollect.open_rank_db()
        try:
            r.execute("DELETE FROM snapshot_rows")
            r.executemany("INSERT INTO snapshot_rows (snapshot_id, rank, profile_sn, nickname, team_color, formation)"
                          " VALUES (1, ?, ?, ?, ?, ?)",
                          [(i + 1, 900 + i, f"랭커{i}", c, "4-2-3-1") for i, c in enumerate("나가나다다나")])
            r.commit()
        finally:
            r.close()
        _win._go_page("랭커 픽", "픽")
        _win._invalidate("rankerpick")
        names = [r.name for r in _win.pick_colors_list.rows()]
        assert names == ["나", "다", "가"], names
        assert [r.fill for r in _win.pick_colors_list.rows()] == [1.0, 2 / 3, 1 / 3]   # 바탕 = 가장 많은 줄 대비


def test_recommend_tab_loads_with_recommend_args():
    """[추천] 탭이 보일 때만 ③(1,000위 안 더 받기)을 싣는다 — [픽] 으로 띄운 로더가 돌면 멈추고 끝난 뒤 실어서 다시."""
    keep = (_win._nick, _win._rank, _win._ouid)
    with _PickEnv():
        _win._nick, _win._rank, _win._ouid = "랭커1", None, _OUID      # 스냅숏의 내 행 → 팀A
        try:
            _win._go_page("랭커 픽", "픽")
            ld = _win._pick_loader
            assert _FakePickLoader.started == 1 and ld.recommend is None, "[픽] 에서 ③ 을 실었다"
            _win._page_tabs["랭커 픽"].set_current("추천")
            assert ld.cancelled and _FakePickLoader.started == 1, "[추천] 을 열었는데 ③ 없는 로더를 그대로 뒀다"
            ld.end()
            assert _FakePickLoader.started == 2, "끝난 뒤 다시 띄우지 않았다"
            assert _win._pick_loader.recommend == ("팀A", "랭커1"), _win._pick_loader.recommend
            _win._pick_loader.end()
            _win._nick = "1만밖"                        # 스냅숏에 없음 · 검색 때 읽은 팀컬러도 없음 → ③ 없음
            _win._go_page("대시보드")
            _win._go_page("랭커 픽", "추천")
            assert _win._pick_loader.recommend is None, "팀컬러를 모르는데 ③ 을 실었다(짐작 금지)"
            assert "팀컬러를 모릅니다" in _win.lb_rec_status.text(), _win.lb_rec_status.text()
            _win._pick_loader.end()
        finally:
            _win._nick, _win._rank, _win._ouid = keep
            _win._page_tabs["랭커 픽"].set_current("픽", emit=False)


def test_recommend_renders_from_db():
    """후보(랭커 픽이 받아 둔 경기) → 표. 문턱 미만이면 "N명뿐"과 빈 표 — 숫자를 지어내지 않는다."""
    keep = (_win._nick, _win._rank, config.RECOMMEND_MIN_RANKERS, config.RECOMMEND_MIN_USERS)
    with _PickEnv():
        config.RANK_COLLECT = False      # 받지 않게 — 이미 받아 둔 것만 그린다
        _win._nick, _win._rank = "랭커1", None
        conn = store.open_db(config.DB_PATH)
        try:
            d = json.loads(json.dumps(_DETAILS[0]))
            d["matchId"], d["matchDate"] = "recm0", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            side = next(s for s in d["matchInfo"] if s.get("ouid") != _win._ouid)  # 내 선발과 다른 카드
            store.save_matches(conn, [d])
            store.mark_ranker_match(conn, "recm0", datetime.now().date().isoformat())
            store.save_ranker_squad(conn, 900, nickname="랭커0", ouid=side["ouid"], rank=1, match_id="recm0",
                                    match_day=d["matchDate"][:10], fetched_at=datetime.now().isoformat(), fail=None,
                                    source=store.PICK)
        finally:
            conn.close()
        try:
            _win._go_page("랭커 픽", "추천")
            rec = _win.rec
            assert rec is not None and rec.total == 1 and not rec.enough, rec
            assert "1명뿐" in _win.lb_rec_status.text() and _win.rec_cards_list.rowCount() == 0, _win.lb_rec_status.text()
            assert _win.tbl_rec_stand.rowCount() == 0, "문턱 미만인데 내 위치를 냈다"
            config.RECOMMEND_MIN_RANKERS = config.RECOMMEND_MIN_USERS = 1
            _win._invalidate("recommend")
            rec = _win.rec
            assert rec.enough and rec.by_source[core.SRC_PICK] == 1, rec
            mine = {p.get("spId") for p in _win._my_latest_starters()}
            shown = [r.key for r in _win.rec_cards_list.rows()]
            assert shown and not (set(shown) & mine), "내가 이미 쓰는 카드를 추천했다"
            assert all(r.cells[0].label == "후보" and r.cells[-1].label == "시세" for r in _win.rec_cards_list.rows())
            assert _win.tbl_rec_stand.rowCount() == 3 and "상위 200 1" in _win.lb_rec_summary.text()
        finally:
            _win._nick, _win._rank, config.RECOMMEND_MIN_RANKERS, config.RECOMMEND_MIN_USERS = keep
            _win._page_tabs["랭커 픽"].set_current("픽", emit=False)
            conn = store.open_db(config.DB_PATH)
            try:
                store.purge_ranker_data(conn, everything=True)
            finally:
                conn.close()


def test_ranker_pick_loader_fetches_recommend_first():
    """로더 본체(run 을 이 스레드에서) — [추천] 이면 ③(내 팀컬러 201~1,000위)을 상위 200보다 먼저 받는다.
    200명은 하루 상한을 혼자 다 쓰는 크기라 뒤에 두면 ③ 이 며칠 밀린다. [픽] 이면 ③ 없음."""
    import ranker

    class _Api:
        def __init__(self):
            self.asked = []

        def get_ouid(self, nickname, attempts=3):
            self.asked.append(nickname)
            return "u_" + nickname

        def get_match_ids(self, ouid, *a, **k):
            return []                       # 최근 경기 없음 — 받는 순서만 잰다

    r = rankcollect.open_rank_db()
    keep = (config.RANKER_PICK_GAP_S, config.DB_PATH)
    try:
        r.execute("DELETE FROM snapshot_rows")
        r.execute("DELETE FROM snapshots")
        rows = [ranker.RankRow(rank=i, profile_sn=700 + i, nickname=f"k{i}", team_color="팀A" if i in (2, 300) else "팀B")
                for i in (1, 2, 300, 301)]
        rankcollect.save_snapshot(r, rows, datetime.now())
        config.RANKER_PICK_GAP_S = 0.0
        config.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "loader.db"
        api = _Api()
        app_main.RankerPickLoader(api, ("팀A", "나")).run()
        assert api.asked == ["k300", "k1", "k2"], api.asked
        conn = store.open_db(config.DB_PATH)
        try:
            src = {sn: h["source"] for sn, h in store.ranker_squads(conn).items()}
        finally:
            conn.close()
        # 출처는 받은 길이 아니라 순위(32단계 source_for_rank) — 추천 길로 받은 300위는 500 안이라 trait
        assert src == {1000: store.TRAIT, 701: store.PICK, 702: store.PICK}, src
        config.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "loader2.db"
        api = _Api()
        app_main.RankerPickLoader(api, None).run()
        assert api.asked == ["k1", "k2"], ("[픽] 에서 ③ 을 받았다", api.asked)
    finally:
        r.close()
        config.RANKER_PICK_GAP_S, config.DB_PATH = keep
        for suffix in ("", "-wal", "-shm"):
            pathlib.Path(str(config.RANK_DB_PATH) + suffix).unlink(missing_ok=True)


def test_rank_trend_page():
    """P2 — rank.db 없음 · 스냅숏 1개("모으는 중") · 2개면 그래프 넷 · 구간 콤보로 다시 그림."""
    import ranker
    with _PickEnv():
        for suffix in ("", "-wal", "-shm"):
            pathlib.Path(str(config.RANK_DB_PATH) + suffix).unlink(missing_ok=True)
        config.RANK_COLLECT = False
        _win._go_page("랭킹 추이")
        _win._invalidate("ranktrend")                  # 앞 테스트가 남긴 낡음 표시에 기대지 않는다(단독 실행)
        assert "랭킹 수집을 켜면" in _win.lb_rtrend_note.text(), _win.lb_rtrend_note.text()
        assert not config.RANK_DB_PATH.exists(), "그리다 rank.db 를 만들었다"
        r = rankcollect.open_rank_db()
        try:
            rows = [ranker.RankRow(rank=i, profile_sn=900 + i, nickname=f"n{i}", elo=5000.0 - i, team_value=10 ** 10,
                                   team_color="팀A" if i % 3 else "팀B",
                                   formation="4-2-3-1" if i != 7 else "5-3-2") for i in range(1, 301)]  # 5-3-2 = 0.5%
            rankcollect.save_snapshot(r, rows, datetime.now() - timedelta(days=1))
            _win._invalidate("ranktrend")
            assert "모으는 중입니다(1/2)" in _win.lb_rtrend_note.text(), _win.lb_rtrend_note.text()
            assert not _win.rtrend_charts["cut"]._points
            rankcollect.save_snapshot(r, rows, datetime.now())
        finally:
            r.close()
        keep_ouid = _win._ouid
        _win._ouid = ""                                  # ELO 다시 읽기(작업 스레드)는 여기서 재지 않는다
        try:
            _win._on_rank_collect_outcome(rankcollect.Outcome("ok"))  # 수집 회차 끝 → 보던 추이를 다시 그린다
        finally:
            _win._ouid = keep_ouid
        ch = _win.rtrend_charts
        assert len(ch["cut"]._points) == 2 and len(ch["cut"]._refs) == len(config.RANK_TREND_CUTS) - 1, ch["cut"]._refs
        assert "실선 1위" in _win.rtrend_titles["cut"].text()
        assert "실선 팀A" in _win.rtrend_titles["team_color"].text(), _win.rtrend_titles["team_color"].text()
        assert ch["value"]._points[-1][1] == 100.0, "구단가치는 억 단위로"
        for key in ("formation", "team_color"):  # 비율 축은 0% 부터(실화면에서 -100% 까지 내려갔다)
            ax, top = ch[key]._axis, max(v for _l, v, _n in ch[key]._points)
            assert ax.lo == 0 and ax.hi >= top and ax.hi <= 100, (key, ax, top)
        _win.cb_rtrend_tier.setCurrentIndex(1)
        assert "201~1,000위" in _win.rtrend_titles["formation"].text()
        assert len(ch["formation"]._points) == 2
        _win.cb_rtrend_tier.setCurrentIndex(0)
        assert not ch["formation"]._refs, ("1% 못 미친 계열(5-3-2 0.5%)을 그었다", ch["formation"]._refs)
        _win.cb_rtrend_tier.setCurrentIndex(1)
        _win.cb_rtrend_tier.setCurrentIndex(2)         # 1,001~1만 — 데이터 없는 구간은 빈 그래프(예외 없이)
        assert not ch["formation"]._points
        _win.cb_rtrend_tier.setCurrentIndex(0)


def test_purge_waits_for_running_loader():
    """E12 — 로더가 도는 중에 끄면 멈추게 하고 끝난 뒤(finished) 지운다(겹치면 반쯤 지워지거나 다시 생긴다)."""
    with _PickEnv():
        calls = []
        keep = store.purge_ranker_data
        store.purge_ranker_data = lambda conn, **k: calls.append(k["everything"]) or 0
        try:
            _win._go_page("랭커 픽")
            ld = _win._pick_loader
            config.RANK_COLLECT = False
            _win.sync_ranker_pick_data()
            assert ld.cancelled and calls == [], "도는 로더를 두고 지웠다"
            _win.start_ranker_pick()
            assert _FakePickLoader.started == 1
            ld.end()
            assert calls == [True], calls
            config.RANK_COLLECT = True
            _win.sync_ranker_pick_data()
            assert calls == [True, False], "켜져 있으면 14일 정리만"
        finally:
            store.purge_ranker_data = keep


# ── 32단계 단계 3 — 특성 로더 배선(받기 규칙 자체는 tests/test_traits.py) ─────────────
# [포지션 특성] 탭은 단계 4 라 아직 없다 — 어느 쪽이 보이는지는 _Vis 로 바꿔 끼운다(진짜 판정 _trait_visible 은
# test_trait_visible_needs_trait_key 가 따로 잰다)
class _FakeTraitLoader(_FakePickLoader):
    started = 0

    def __init__(self, api, mine=False):
        super().__init__(api)
        self.person = self._S()

    def start(self):
        _FakeTraitLoader.started += 1
        self.running = True

    def end(self, out=None):
        self.running = False
        self.done.emit(out or app_main.traitcollect.TraitRun())
        self.finished.emit()


class _TraitEnv(_PickEnv):
    """_PickEnv + 특성 동의(7) · 가짜 특성 로더 · 보임은 self.vis("pick"/"trait"/None)."""

    def __enter__(self):
        super().__enter__()
        self.keep2 = (app_main.TraitLoader, _win._trait_loader, _win._trait_result)
        config.NOTICE_ACCEPTED = config.TRAIT_NOTICE_VERSION
        app_main.TraitLoader = _FakeTraitLoader
        _win._trait_loader = None
        _FakeTraitLoader.started = 0
        _win.set_trait_off("")
        self.vis = None
        _win._ranker_pick_visible = lambda: self.vis == "pick"
        _win._trait_visible = lambda: self.vis == "trait"
        return self

    def __exit__(self, *exc):
        del _win._ranker_pick_visible, _win._trait_visible
        _win.set_trait_off("")
        app_main.TraitLoader, _win._trait_loader, _win._trait_result = self.keep2
        return super().__exit__(*exc)


def test_trait_gate():
    """E1 — 수집 꺼짐 · 특성 안내(7) 전 · 탭 안 보임 · 스스로 끔 → 요청 0. 다 맞으면 시작."""
    with _TraitEnv() as env:
        env.vis = "trait"
        for setup in (lambda: setattr(config, "RANK_COLLECT", False),
                      lambda: setattr(config, "NOTICE_ACCEPTED", config.TRAIT_NOTICE_VERSION - 1),
                      lambda: setattr(env, "vis", None),
                      lambda: _win.set_trait_off("blocked")):
            config.RANK_COLLECT, config.NOTICE_ACCEPTED, env.vis = True, config.TRAIT_NOTICE_VERSION, "trait"
            _win.set_trait_off("")
            setup()
            _win.start_visible_api_loader()
            _win.start_trait()
            assert _FakeTraitLoader.started == 0, "막혀야 할 때 특성 로더가 떴다"
        config.RANK_COLLECT, config.NOTICE_ACCEPTED, env.vis = True, config.TRAIT_NOTICE_VERSION, "trait"
        _win.set_trait_off("")
        _win.start_visible_api_loader()
        assert _FakeTraitLoader.started == 1 and _FakePickLoader.started == 0, "다 맞는데 안 떴다(위 단언이 빈 검사)"


class _GameOn:
    """84 G3 — 게임 켜짐 판정을 잠깐 바꾼다(감시 상태 dict). 나갈 때 꺼짐으로."""

    def __init__(self, on=True):
        self.on = on

    def __enter__(self):
        app_main._GAME["on"] = self.on
        return self

    def __exit__(self, *exc):
        app_main._GAME["on"] = False


def test_84_game_pauses_visible_api_loaders():
    """게임 중엔 랭커 픽·특성 로더가 안 뜬다 — 같은 조건에서 게임이 꺼져 있으면 뜬다(먼저 단언)."""
    with _TraitEnv() as env:
        for vis, cls in (("trait", _FakeTraitLoader), ("pick", _FakePickLoader)):
            env.vis = vis
            _win._trait_loader = _win._pick_loader = None
            cls.started = 0
            with _GameOn():
                _win.start_visible_api_loader()
            assert cls.started == 0, f"게임 중인데 {vis} 로더가 떴다"
            _win.start_visible_api_loader()
            assert cls.started == 1, f"게임이 꺼져 있는데 {vis} 로더가 안 떴다(위 단언이 빈 검사)"
            if vis == "trait":
                _win._trait_loader.end()
            else:
                _win._pick_loader.running = False
                _win._pick_loader = None


def test_84_game_watch_and_schedulers():
    """감시는 바뀔 때만 알리고 못 읽으면 켜짐 · 예약 셋은 게임 중 시작하지 않고(꺼져 있으면 시작) · pause 는 도는 것을 멈춘다."""
    seen, answers = [], [False, True, True, False]
    keep = app_main.GAME_CHECK
    w = app_main.GameWatch()
    w.changed.connect(seen.append)
    try:
        app_main.GAME_CHECK = lambda: answers.pop(0)
        for _ in range(4):
            w.poll()
        assert seen == [True, False], seen

        def boom():
            raise OSError("목록 못 읽음")
        app_main.GAME_CHECK = boom
        w.poll()
        assert seen[-1] is True and app_main.game_paused(), "못 읽었는데 켜짐으로 안 봤다"
    finally:
        app_main.GAME_CHECK = keep
        app_main._GAME["on"] = False

    class _Run:
        def __init__(self):
            self.cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

    # 랭킹 수집 — 조건이 다 맞으면 돈다(먼저), 게임 중엔 안 돈다 · pause 는 예약을 지우고 회차를 멈춘다
    keep_cfg = (config.NOTICE_ACCEPTED, config.WEB_DATA, config.RANK_COLLECT)
    config.NOTICE_ACCEPTED, config.WEB_DATA, config.RANK_COLLECT = config.NOTICE_VERSION, True, True
    try:
        rs = app_main.RankCollectScheduler()
        assert rs.can_run(), "조건이 다 맞는데 못 돈다(아래 단언이 빈 검사)"
        with _GameOn():
            assert not rs.can_run(), "게임 중인데 랭킹 수집이 돈다"
            maint = []
            rs.maintain = lambda **kw: maint.append(kw)
            rs.check()
            assert maint == [], "게임 중인데 매시간 정리(예측 되짚기 요청 포함)가 돌았다"
            rs.planned, rs.worker = datetime.now(), _Run()
            rs.pause()
            assert rs.planned is None and rs.worker.cancelled
        rs.resume()
        assert maint == [{"vacuum": False}], maint
    finally:
        config.NOTICE_ACCEPTED, config.WEB_DATA, config.RANK_COLLECT = keep_cfg

    # 구단주 분포 · 카드 정보 — 같은 식
    keep_g = (config.read_owner_dist, config.read_card_backfill, app_main.CardBackfillLoader)
    started = []

    class _CardLoader(_Run):
        def __init__(self, today):
            super().__init__()
            self.done = _FakePickLoader._S()

        def isRunning(self):
            return False

        def start(self):
            started.append("card")
    try:
        config.read_owner_dist = lambda: (True, True)
        config.read_card_backfill = lambda: True
        app_main.CardBackfillLoader = _CardLoader
        od = app_main.OwnerDistScheduler()
        od._mixed = True
        od._maybe_start = lambda: started.append("owner")
        cb = app_main.CardBackfillScheduler()
        with _GameOn():
            od.sync(start=True)
            cb.sync(start=True)
            assert started == [], f"게임 중인데 시작했다: {started}"
            for s in (od, cb):
                s.loader = _Run()
                s.pause()
                assert s.loader.cancelled, f"{type(s).__name__}.pause 가 도는 로더를 안 멈췄다"
                s.loader = None
        od.resume()
        cb.resume()
        assert started == ["owner", "card"], f"게임이 꺼졌는데 다시 안 시작했다: {started}"
    finally:
        config.read_owner_dist, config.read_card_backfill, app_main.CardBackfillLoader = keep_g


def test_84_game_change_reaches_every_auto_path():
    """on_game_changed — 예약 셋 pause/resume · 창은 로더 멈춤/다시 켜기 · main 이 감시를 창·예약보다 먼저 켜고 잇는다."""
    calls = []

    class _S:
        def __init__(self, name):
            self.name = name

        def pause(self):
            calls.append(("pause", self.name))

        def resume(self):
            calls.append(("resume", self.name))

    class _W:
        _quitting = False

        def stop_api_loaders(self):
            calls.append("stop")

        def start_visible_api_loader(self):
            calls.append("start")

    class _Sh:
        sched, owner_sched, card_sched, window = _S("rank"), _S("owner"), _S("card"), _W()
    app_main.on_game_changed(_Sh, True)
    app_main.on_game_changed(_Sh, False)
    assert calls == [("pause", "rank"), ("pause", "owner"), ("pause", "card"), "stop",
                     ("resume", "rank"), ("resume", "owner"), ("resume", "card"), "start"], calls
    import inspect
    src = inspect.getsource(app_main.main)
    assert "watch.changed.connect" in src, "main 이 감시를 셸에 안 이었다"
    assert src.index("watch.start()") < src.index("tray.AppShell("), "창·예약보다 늦게 처음 본다"
    with _GameOn():
        assert "쉬는 중" in app_main.rank_status_text({})
    assert "쉬는 중" not in app_main.rank_status_text({})


def test_trait_visible_needs_trait_key():
    """진짜 보임 판정 — 지금 자리의 키가 TRAIT_KEY 여야 · TRAIT_KEY 는 PICK_KEYS 밖(넣으면 특성 탭에서 랭커 픽 로더가 뜬다)."""
    assert app_main.MainWindow.TRAIT_KEY not in app_main.MainWindow.PICK_KEYS
    _win._go_page("랭커 픽")
    assert _win._ranker_pick_visible() and not _win._trait_visible()
    _win._go_page("대시보드")


def test_trait_tab_does_not_start_pick_loader():
    """진짜 보임 판정으로 [포지션 특성] 탭을 연다 — 특성 로더만 뜬다(TRAIT_KEY 가 PICK_KEYS 에 들면 랭커 픽 로더가 떠서 막는다, 검토 A)."""
    with _TraitEnv():
        del _win._ranker_pick_visible, _win._trait_visible   # 가짜 보임을 걷어 실제 판정으로
        try:
            _win._go_page("메타 분석", "포지션 특성")
            assert _win._trait_visible() and not _win._ranker_pick_visible()
            assert _FakeTraitLoader.started == 1 and _FakePickLoader.started == 0, \
                (_FakeTraitLoader.started, _FakePickLoader.started)
            trait = _win._trait_loader
            _win._go_page("메타 분석", "하루 승률")
            assert trait.cancelled, "다른 탭으로 갔는데 특성이 계속 받는다"
        finally:
            _win._ranker_pick_visible = lambda: False   # __exit__ 이 지운다
            _win._trait_visible = lambda: False


def _trait_seed(rows):
    """(profile_sn, 포메이션, 자리 목록) — 스냅숏 900~ 에 맞춘 특성 줄. 칸마다 이름이 가장 긴 코드(표 잘림을 재려고)."""
    import trait_codes as tc

    def longest(table, n=1):
        return sorted(table, key=lambda c: (-len(table[c]), c))[:n]
    traits = [longest(tc.NORMAL)[0], longest(tc.NEW)[0], longest(tc.TRAIN)[0]]
    coach = longest(tc.TRAIN, 3)
    conn = store.open_db(config.DB_PATH)
    try:
        for sn, form, poss in rows:
            store.save_trait_squad(conn, sn, source=store.PICK, rank=sn - 899, match_id=f"m{sn}", formation=form,
                                   team="1-0", body=[[1000 + p, p, 11, traits, coach] for p in poss],
                                   state="ok", collect_on_at=config.read_rank_on_at(),
                                   match_day=datetime.now().date().isoformat(), checked_at=datetime.now().isoformat())
    finally:
        conn.close()


def test_trait_tab_renders_usage():
    """받아 둔 특성 줄 → 탭(요청 없이 DB 에서) — 포메이션 고르기(사람 수 순) · 포지션 표 · 고른 포메이션이 다시 그려도 남는다."""
    with _TraitEnv():
        config.RANK_COLLECT = False   # 받지 않게 — 이미 받아 둔 것만 그린다
        _seed_snapshot(4)             # 넷째(903)는 아래 배선 단언에서 나중에 받는다
        _trait_seed([(900, "4-2-3-1", [25, 0]), (901, "4-2-3-1", [25, 0]), (902, "4-4-2", [25])])
        try:
            _win._go_page("메타 분석", "포지션 특성")
            u = _win.trait_usage
            assert u is not None and u.used == 3 and u.formation == "4-2-3-1", u
            cb = _win.cb_trait_form
            assert [cb.itemData(i) for i in range(cb.count())] == ["4-2-3-1", "4-4-2"], "사람 수 순이 아니다"
            # 두 포지션 × (코치 셋이 가장 긴 칸 → 상위 min(3, N)줄)
            assert _win.tbl_traits.rowCount() == 2 * min(3, config.TRAIT_TOP_NAMES), _win.tbl_traits.rowCount()
            assert _win.tbl_traits.item(0, 0).text().endswith("2명"), _win.tbl_traits.item(0, 0).text()
            # 40단계 ② — 칸 글자는 "NN%" 만(이름은 툴팁·NAME_ROLE · 그림은 코드로) · 행 높이는 그림 + 글자 줄
            c = _win.tbl_traits.item(0, 1)
            assert c.text().endswith("%") and " " not in c.text(), c.text()
            assert c.toolTip() and c.data(app_main.NAME_ROLE) == c.toolTip(), (c.toolTip(), c.data(app_main.NAME_ROLE))
            assert isinstance(c.data(app_main.ICON_KEY_ROLE), int), "알려진 특성인데 그림 코드가 없다"
            assert _win.tbl_traits.icon_columns == {1: "trait", 2: "trait", 3: "train", 4: "train"}
            need = config.TRAIT_ICON_PX + _win.tbl_traits.fontMetrics().height()
            assert _win.tbl_traits.verticalHeader().defaultSectionSize() >= need, "행이 그림 + 글자 줄보다 낮다"
            assert "특성을 본 구단주 3명" in _win.lb_trait_note.text(), _win.lb_trait_note.text()
            cb.setCurrentIndex(1)                       # → 4-4-2 · 다시 채워도 고른 값이 남는다
            assert _win.trait_usage.formation == "4-4-2" and cb.currentData() == "4-4-2"
            assert _win.tbl_traits.item(0, 0).text().endswith("1명")
            _win._invalidate(_win.TRAIT_KEY)
            assert _win.trait_usage.formation == "4-4-2", "다시 그렸더니 고른 포메이션이 풀렸다"
            few = _win.tbl_traits.item(0, 1).foreground().color().name()
            assert few == app_main.QColor(app_main.T.TEXT_DIM).name(), "표본 미달인데 흐리지 않았다"
            assert all(r.dim for r in _win.trait_slots.rows()), "표본 미달인데 자리 줄을 흐리지 않았다"
            cb.setCurrentIndex(0)
            # 67 R7 — 자리마다 종류별 1위 한 줄(표 첫 줄과 같은 값) · 표는 접기 아래(기본 접힘)
            slots = _win.trait_slots.rows()
            u = _win.trait_usage
            assert len(slots) == 2 and all(r.dim == (u.thin or u.few) for r in slots), slots
            assert slots[0].name + f" · {slots[0].sub}" == _win.tbl_traits.item(0, 0).text(), (slots[0], _win.tbl_traits.item(0, 0).text())
            for c, col in zip(slots[0].cells, range(1, 5)):
                it = _win.tbl_traits.item(0, col)
                assert c.text == it.text() and c.tip == it.data(app_main.NAME_ROLE), (c, it.text())
            assert not _win.trait_table_box.is_open()
            _check_metric_lists([_win.trait_slots])
            _win.trait_table_box.head.setChecked(True)
            _check_elisions([_win.tbl_traits])   # PyQt 5 — 채운 표(가장 긴 이름)가 1280×720 에서 안 잘린다
            _win.trait_table_box.head.setChecked(False)
            # 배선 — 로더가 한 사람을 저장하면(person) 보고 있는 탭이 다시 그린다 · 바퀴가 끝나면(done) 멈춘 까닭이 뜬다
            _trait_seed([(903, "4-2-3-1", [25, 0])])
            _win._on_trait_person()
            assert _win.trait_usage.used == 4, "한 사람 저장 뒤 탭이 다시 안 그려졌다(PICK_DATA_KEYS)"
            config.RANK_COLLECT = True
            _win._on_trait_done(app_main.traitcollect.TraitRun(web=[app_main.traitcollect.TraitResult(stop="format")]))
            assert "응답이 바뀌었" in _win.lb_trait_status.text(), _win.lb_trait_status.text()
        finally:
            conn = store.open_db(config.DB_PATH)
            try:
                store.purge_ranker_data(conn, everything=True)
            finally:
                conn.close()


def test_trait_empty_state_messages():
    """원인마다 문구 하나(계획 "빈 상태") — 서로 다른 문구 · 버튼은 안내 동의·다시 켜기에만 · [다시 켜기]는 스스로 끈 것을 푼다."""
    with _TraitEnv():
        R, TR, W = app_main.rankerpick.PickResult, app_main.traitcollect.TraitRun, app_main.traitcollect.TraitResult
        cases = [
            ("web", lambda: setattr(config, "WEB_DATA", False), "웹 데이터", ""),
            ("collect", lambda: setattr(config, "RANK_COLLECT", False), "랭킹 수집을 켜면", ""),
            ("notice", lambda: setattr(config, "NOTICE_ACCEPTED", config.TRAIT_NOTICE_VERSION - 1), "안내에 동의", "notice"),
            ("blocked", lambda: _win.set_trait_off("blocked"), "스스로 껐습니다", "reenable"),
            ("format", lambda: setattr(_win, "_trait_result", TR(web=[W(stop="format")])), "응답이 바뀌었", ""),
            ("rate", lambda: setattr(_win, "_trait_result", TR(web=[W(stop="rate")])), "잠시 막았", ""),
            ("quota", lambda: setattr(_win, "_trait_result", TR(pick=R(quota=True))), "호출 한도", ""),
            ("web_limit", lambda: setattr(_win, "_trait_result", TR(web=[W(limit=True)])), "웹 상한", ""),
            ("limit", lambda: setattr(_win, "_trait_result", TR(pick=R(limit=True))), "랭커 픽과 나눠 씀", ""),
            ("share", lambda: setattr(_win, "_trait_result", TR(pick=R(share_spent=True))), "특성 몫을 다 썼", ""),
        ]
        seen = set()
        for name, setup, want, button in cases:
            config.WEB_DATA = config.RANK_COLLECT = True
            config.NOTICE_ACCEPTED = config.TRAIT_NOTICE_VERSION
            _win.set_trait_off("")
            _win._trait_result = None
            assert _win.trait_status() == ("", ""), f"{name}: 막힌 게 없는데 문구가 있다(아래 단언이 빈 검사)"
            setup()
            text, btn = _win.trait_status()
            assert want in text and btn == button, (name, text, btn)
            seen.add(text)
        assert len(seen) == len(cases), "두 원인이 같은 문구다"
        config.NOTICE_ACCEPTED = config.TRAIT_NOTICE_VERSION
        _win._trait_result = None
        assert "스냅숏이 아직" in _win.trait_status(False)[0]
        # 화면 — 스스로 끈 상태로 탭을 열면 버튼이 보이고, 누르면 풀린다
        _win.set_trait_off("blocked")
        _win._trait_result = app_main.traitcollect.TraitRun(web=[W(stop="rate")])   # 막혔던 바퀴 — 다시 켜면 지운다
        _win._go_page("메타 분석", "포지션 특성")
        assert _win.btn_trait_action.isVisibleTo(_win) and _win.btn_trait_action.text() == "다시 켜기"
        assert "스스로 껐습니다" in _win.lb_trait_status.text()
        _win.btn_trait_action.click()
        assert _win._trait_off_reason() == "" and not _win.btn_trait_action.isVisibleTo(_win)
        assert "막았" not in _win.lb_trait_status.text(), "다시 켰는데 막혔던 바퀴 문구가 남았다"


def test_pick_to_trait_tab_starts_trait_after_pick_finishes():
    """랭커 픽 → 특성 탭: 랭커 픽 로더는 멈추게만 · 그게 끝나면(finished) 특성이 뜬다(검토 3회차)."""
    with _TraitEnv() as env:
        env.vis = "pick"
        _win.start_visible_api_loader()
        pick = _win._pick_loader
        env.vis = "trait"
        _win.start_visible_api_loader()
        assert pick.cancelled and _FakeTraitLoader.started == 0, "랭커 픽이 도는데 특성이 같이 떴다"
        pick.end()
        assert _FakeTraitLoader.started == 1, "랭커 픽이 끝났는데 특성이 안 떴다"
        assert _FakePickLoader.started == 1, "특성 탭인데 랭커 픽을 다시 띄웠다"


def test_trait_to_pick_tab_waits_trait():
    with _TraitEnv() as env:
        env.vis = "trait"
        _win.start_visible_api_loader()
        trait = _win._trait_loader
        env.vis = "pick"
        _win.start_visible_api_loader()
        assert trait.cancelled and _FakePickLoader.started == 0, "특성이 도는데 랭커 픽이 같이 떴다(같은 장부 둘)"
        trait.end()
        assert _FakePickLoader.started == 1 and _FakeTraitLoader.started == 1


def test_finished_loader_does_not_restart_itself():
    """끝난 로더가 자기 자신을 다시 띄우지 않는다 — 받을 게 없어도 끝나자마자 다시 뜨는 반복(계획의 "무조건 다시 켜기" 대신)."""
    with _TraitEnv() as env:
        for side, fake in (("pick", _FakePickLoader), ("trait", _FakeTraitLoader)):
            env.vis = side
            _win.start_visible_api_loader()
            n = fake.started
            (_win._pick_loader if side == "pick" else _win._trait_loader).end()
            assert fake.started == n, f"{side} 가 끝나자마자 다시 떴다"


def test_pick_and_trait_loaders_exclusive():
    """⑧ 하나씩 — 서로를 "돌고 있으면 기다림"으로 본다 · 검색이 돌면 둘 다 시작 안 함 · 새 검색에 둘 다 양보."""
    with _TraitEnv() as env:
        env.vis = "trait"
        _win.start_trait()
        _win.start_ranker_pick()
        assert _FakeTraitLoader.started == 1 and _FakePickLoader.started == 0, "특성이 도는데 랭커 픽이 떴다"
        _win._trait_loader.end()
        env.vis = "pick"
        _win.start_ranker_pick()
        env.vis = "trait"
        _win.start_trait()
        assert _FakeTraitLoader.started == 1, "랭커 픽이 도는데 특성이 떴다"
        _win._pick_loader.end()            # 끝나면 특성이 뜬다
        trait = _win._trait_loader
        _win._yield_trades()
        assert trait.cancelled, "새 검색에 특성이 양보하지 않았다"
        trait.end()
        _win._loader = _FakeThread("검색", [])
        _win.start_trait()
        assert _FakeTraitLoader.started == 2, "검색이 도는데 특성이 떴다"


def test_trait_stops_when_hidden_and_resumes_on_show():
    with _TraitEnv() as env:
        env.vis = "trait"
        _win.start_visible_api_loader()
        ld = _win._trait_loader
        _win.hide()
        assert ld.cancelled, "창을 숨겼는데 특성을 계속 받는다"
        ld.end()
        _win.show()
        _app.processEvents()
        assert _FakeTraitLoader.started == 2, "다시 보였는데 잇지 않았다"


def test_trait_purge_waits_both_loaders():
    """D4 — 지우기는 랭커 픽·특성 로더 둘 다 멈추게 하고 **둘 다** 끝난 뒤 한 번(특성 로더도 ranker_squads·matches 에 쓴다)."""
    with _TraitEnv() as env:
        calls = []
        keep = store.purge_ranker_data
        store.purge_ranker_data = lambda conn, **k: calls.append(k["everything"]) or 0
        try:
            env.vis = "pick"
            _win.start_ranker_pick()
            pick = _win._pick_loader
            trait = _FakeTraitLoader(None)       # 하나씩이라 정상 길로는 둘이 같이 못 돈다 — 겹친 순간을 직접 만든다
            trait.start()
            trait.finished.connect(_win._on_trait_finished)
            _win._trait_loader = trait
            config.RANK_COLLECT = False
            _win.sync_ranker_pick_data()
            assert pick.cancelled and trait.cancelled and calls == [], "도는 로더를 두고 지웠다"
            pick.end()
            assert calls == [], "특성 로더가 아직 도는데 지웠다"
            env.vis = "trait"
            config.RANK_COLLECT = True
            _win.start_trait()
            assert _FakeTraitLoader.started == 1, "지우기가 기다리는 중에 특성이 떴다"
            trait.end()
            assert calls == [True], calls
            # 특성 로더만 도는 중에 끈다 — 지우기가 랭커 픽 로더만 보면 여기서 바로 지운다
            _win.start_trait()
            trait = _win._trait_loader
            config.RANK_COLLECT = False
            _win.sync_ranker_pick_data()
            assert trait.cancelled and calls == [True], ("특성 로더가 도는데 지웠다", calls)
            trait.end()
            assert calls == [True, True], calls
            # 지우기 대기 상태에선 특성을 띄우지 않는다(랭커 픽 쪽과 같은 방어 — 상태를 직접 만들어 잰다)
            config.RANK_COLLECT = True
            _win._pick_purge_pending = True
            n = _FakeTraitLoader.started
            _win.start_trait()
            assert _FakeTraitLoader.started == n, "지우기가 기다리는 중에 특성이 떴다"
            _win._pick_purge_pending = False
            _win.start_trait()
            assert _FakeTraitLoader.started == n + 1, "대기가 풀렸는데 안 떴다(위 단언이 빈 검사)"
        finally:
            store.purge_ranker_data = keep


def test_trait_block_three_days_turns_off():
    """막힌 날이 TRAIT_BLOCK_DAYS 이어지면 특성 받기만 끈다(settings.ini — 화면 스레드) · 랭킹 수집은 그대로."""
    with _TraitEnv() as env:
        env.vis = "trait"
        _win.start_trait()
        _win._trait_loader.end(app_main.traitcollect.TraitRun(block_streak=config.TRAIT_BLOCK_DAYS - 1))
        assert not _win._trait_off_reason()
        _win.start_trait()
        _win._trait_loader.end(app_main.traitcollect.TraitRun(block_streak=config.TRAIT_BLOCK_DAYS))
        assert _win._trait_off_reason() == "blocked" and config.RANK_COLLECT
        _win.start_trait()
        assert _FakeTraitLoader.started == 2, "스스로 껐는데 다시 떴다"


def test_visible_loader_after_notice():
    """다시 묻는 동의 뒤 · 다른 오픈API 로더 끝 — 보이는 쪽(특성)을 켠다."""
    with _TraitEnv() as env:
        env.vis = "trait"

        class _Notice:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return app_main.QDialog.DialogCode.Accepted

        keep = app_main.NoticeDialog
        app_main.NoticeDialog = _Notice
        try:
            _win.ask_notice_update()
        finally:
            app_main.NoticeDialog = keep
        assert _FakeTraitLoader.started == 1, "동의 뒤 특성 탭인데 안 켰다"
        _win._trait_loader.end()
        _win._trade_loader = _FakeThread("거래", [], running=False)
        _win._trade_again = False
        _win._on_trade_thread_finished()
        assert _FakeTraitLoader.started == 2, "거래가 끝났는데 특성이 안 이었다"
        _win._trait_loader.end()
        _win._on_ranker_finished()
        assert _FakeTraitLoader.started == 3, "랭커 기록이 끝났는데 특성이 안 이었다"


def test_every_api_loader_start_goes_through_one_function():
    """랭커 픽을 켜는 자리는 start_visible_api_loader 하나 · 멈추는 자리는 stop_api_loaders 하나(+ 보이는 쪽을 켤 때 반대쪽).
    새 자리가 start_ranker_pick 을 직접 부르면 특성 탭에서 랭커 픽을 켜거나 특성을 못 잇는다(검토 4회차)."""
    import ast
    src = pathlib.Path(app_main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {"start_ranker_pick": {"start_visible_api_loader"},
               "_stop_ranker_pick": {"start_visible_api_loader", "stop_api_loaders"},
               "start_trait": {"start_visible_api_loader", "_on_ranker_pick_finished"}}
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr in allowed and fn.name not in allowed[node.attr] \
                    and fn.name != node.attr:
                bad.append(f"{fn.name} → {node.attr} (줄 {node.lineno})")
    assert not bad, bad
    planted = "def f(self):\n    self._loader.finished.connect(self.start_ranker_pick)\n"
    hit = [n for n in ast.walk(ast.parse(planted)) if isinstance(n, ast.Attribute) and n.attr == "start_ranker_pick"]
    assert hit, "심은 위반을 못 잡는다 — 검사가 비었다"


def test_shutdown_waits_trait_loader():
    log = []
    keep = _win._trait_loader
    try:
        _win._trait_loader = _FakeThread("특성", log)
        left = _win.shutdown(fast=True)
        assert _win._trait_loader in left and "stop 특성" in log, "종료 표에 특성 로더가 없다"
    finally:
        _win._trait_loader = keep


def test_trait_purge_by_state():
    """32단계 D3·D5 배선 — 켤 때 정리(sync → purge_ranker_pick_data(everything=False))가 지금 스냅숏 상위 TRAIT_TOP 와
    .env 의 켠 시각을 prune_trait_squads 에 넘긴다 · 스냅숏이 없으면 순위 정리를 건너뛴다 · 수집이 꺼져 있으면 전부(D5 ①)."""
    on_at = "2026-10-01T09:00:00.000000"
    keep_read = config.read_rank_on_at

    def save(sn, at=on_at):
        conn = store.open_db(config.DB_PATH)
        try:
            store.save_trait_squad(conn, sn, source=store.PICK, rank=1, match_id=None, formation=None, team=None,
                                   body=[], state="ok", collect_on_at=at, match_day=None,
                                   checked_at=datetime.now().isoformat(timespec="seconds"))
        finally:
            conn.close()

    def have():
        conn = store.open_db(config.DB_PATH)
        try:
            return sorted(store.trait_squads(conn))
        finally:
            conn.close()

    with _PickEnv():                                   # 스냅숏 900·901·902 = 1~3위
        config.read_rank_on_at = lambda: on_at
        try:
            save(900), save(950), save(901, at="2026-09-01T00:00:00.000000")
            _win.sync_ranker_pick_data()
            assert have() == [900], ("500위 밖(950) · 켠 시각이 다른 줄(901)을 남겼다", have())
            for suffix in ("", "-wal", "-shm"):        # 스냅숏 없음 — 순위로 지우지 않는다
                pathlib.Path(str(config.RANK_DB_PATH) + suffix).unlink(missing_ok=True)
            save(950)
            _win.sync_ranker_pick_data()
            assert have() == [900, 950], ("스냅숏이 없는데 순위로 지웠다", have())
            config.RANK_COLLECT = False
            _win.sync_ranker_pick_data()
            assert have() == [], ("수집이 꺼졌는데 특성 줄이 남았다", have())
        finally:
            config.read_rank_on_at = keep_read


def test_every_off_path_purges_ranker_pick():
    """E12 — 끄는 길 넷 + [수집 기록 지우기]가 전부 sync_ranker_pick_data 를 거친다."""
    seen = []
    keep = (_win.sync_ranker_pick_data, config.WEB_DATA, config.RANK_COLLECT, app_main.NoticeDialog)
    _win.sync_ranker_pick_data = lambda: seen.append("sync")
    try:
        config.WEB_DATA = config.RANK_COLLECT = True
        dlg = app_main.AboutDialog(_win)
        dlg._on_rank_toggled(False)
        assert seen == ["sync"], ("[정보] 수집 끄기", seen)
        dlg._on_web_toggled(False)
        assert len(seen) == 2, ("웹 데이터 끄기", seen)
        dlg._ask_clear = lambda keep_ouid: "all"
        dlg._on_clear_rank()
        assert len(seen) == 3, ("수집 기록 지우기", seen)
        dlg.close()
        _win._on_rank_collect_outcome(rankcollect.Outcome("blocked", disabled_by_block=True))
        assert len(seen) == 4, ("D6 스스로 끔", seen)

        class _Notice:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return app_main.QDialog.DialogCode.Rejected

        app_main.NoticeDialog = _Notice
        _win.ask_notice_update()
        assert len(seen) == 5, ("다시 묻는 창", seen)
    finally:
        _win.sync_ranker_pick_data, config.WEB_DATA, config.RANK_COLLECT, app_main.NoticeDialog = keep


def test_shutdown_stops_ranker_pick_and_backfill():
    log = []
    keep = _win._pick_loader, _win._backfill_worker
    try:
        _win._pick_loader, _win._backfill_worker = _FakeThread("픽", log), _FakeThread("색인", log)
        left = _win.shutdown(fast=True)
        assert _win._pick_loader in left and _win._backfill_worker in left, "종료 표에 새 워커가 없다(E11)"
        assert "stop 픽" in log and "stop 색인" in log, log
    finally:
        _win._pick_loader, _win._backfill_worker = keep


def test_final_429_recorded_per_loader():
    """E13 — 재시도 끝의 429 만 적는다(검색 · 거래 · 랭커 기록). 재시도로 넘어간 429 는 안 적는다."""
    marks = []
    keep = app_main._mark_final_429, app_main.tradecollect.collect, app_main.rankerstats.collect
    app_main._mark_final_429 = lambda: marks.append(1)
    try:
        got = _run_loader(_DetailApi([d["matchId"] for d in _DETAILS], nexon_api.QUOTA_CODE))
        assert got["quota"] and len(marks) == 1, ("검색", marks)
        got = _run_loader(_DetailApi([], ""))
        assert got["ok"] and len(marks) == 1, "429 없는 검색이 적었다"
        app_main.tradecollect.collect = lambda *a, **k: app_main.tradecollect.TradeResult(quota=True)
        app_main.TradeLoader(None).run()
        assert len(marks) == 2, ("거래", marks)
        app_main.tradecollect.collect = lambda *a, **k: app_main.tradecollect.TradeResult(complete=True)
        app_main.TradeLoader(None).run()
        assert len(marks) == 2

        def boom(*a, **k):
            raise nexon_api.NexonAPIError("한도", code=nexon_api.QUOTA_CODE, status=429)
        app_main.rankerstats.collect = boom
        app_main.RankerStatsLoader(None, [(1, 2)], 52).run()
        assert len(marks) == 3, ("랭커 기록", marks)
    finally:
        app_main._mark_final_429, app_main.tradecollect.collect, app_main.rankerstats.collect = keep


def test_mark_final_429_blocks_ranker_pick_today():
    """_mark_final_429 가 정말 그날 랭커 픽을 막는 표시를 쓴다(배선 — 표시를 다른 종류로 쓰면 막지 못한다)."""
    app_main._mark_final_429()
    conn = store.open_db(config.DB_PATH)
    try:
        assert store.budget_hit_429(conn, datetime.now().date().isoformat())
        conn.execute("DELETE FROM api_budget")
        conn.commit()
    finally:
        conn.close()


def test_backfill_waits_for_loaders_and_yields():
    """E8 — 검색·로더가 돌면 미룬다 · 새 검색이 시작되면 묶음 사이에서 멈춘다 · 양보로 멈췄으면 다시 예약."""
    log = []
    keep = _win._loader, _win._backfill_worker
    try:
        _win._loader = _FakeThread("검색", log)
        _win._backfill_worker = None
        _win._start_backfill()
        assert _win._backfill_worker is None and _win._backfill_timer.isActive(), "검색 중에 색인을 시작했다"
        _win._loader.running = False
        _win._start_backfill()
        assert isinstance(_win._backfill_worker, app_main.SquadBackfillWorker)
        _win._backfill_worker = _FakeThread("색인", log)
        _win._yield_trades()
        assert "stop 색인" in log, "새 검색에 색인이 양보하지 않았다"
        _win._backfill_timer.stop()
        _win._backfill_left = (5, 10)
        _win._on_backfill_finished()
        assert _win._backfill_timer.isActive(), "남았는데 다시 예약하지 않았다"
    finally:
        _win._backfill_timer.stop()
        _win._loader, _win._backfill_worker = keep
        _win._backfill_left = None


def test_backfill_worker_fixes_squad_days_and_retries_when_stopped():
    """63 — 색인 작업이 옛 줄 날 다시 세기(store.fix_squad_days)를 부른다 · 그게 양보로 멈췄으면 남은 경기가 0 이어도 다시 예약."""
    conn = store.open_db(config.DB_PATH)
    try:
        conn.execute("DELETE FROM trade_state WHERE key = ?", (store.SQUAD_DAY_FIX_KEY,))
        conn.commit()
    finally:
        conn.close()
    w = app_main.SquadBackfillWorker()
    low = app_main.rankcollect.lower_thread_priority
    app_main.rankcollect.lower_thread_priority = lambda: None   # 화면 스레드에서 직접 돌린다 — 우선순위는 그대로
    try:
        w.run()
    finally:
        app_main.rankcollect.lower_thread_priority = low
    conn = store.open_db(config.DB_PATH)
    try:
        assert store.SQUAD_DAY_FIX_KEY in store.trade_state(conn), "색인 작업이 날 다시 세기를 안 불렀다"
    finally:
        conn.close()
    assert w.day_fix_left is False
    keep = _win._backfill_worker
    try:
        _win._backfill_timer.stop()
        _win._backfill_left = (0, 10)
        _win._backfill_worker = w
        _win._on_backfill_finished()
        assert not _win._backfill_timer.isActive(), "할 일이 없는데 다시 예약했다 — 아래 단언이 비었다"
        w.day_fix_left = True
        _win._on_backfill_finished()
        assert _win._backfill_timer.isActive(), "날 다시 세기가 멈췄는데 다시 예약하지 않았다"
    finally:
        _win._backfill_timer.stop()
        _win._backfill_worker = keep
        _win._backfill_left = None


def _enhance_env(sp: int):
    """강화 비용 로더를 가짜 넥슨으로 그 자리에서 돌린다 — 스모크는 QThread.start 를 막는다."""
    info = playerinfo.PlayerInfo(sp_id=sp, name="테스트강화", ovr=115,
                                 prices={g: f"{g * 1_000_000:,} BP" for g in range(1, 14)})
    keep = (playerinfo.fetch_player_info, boostcollect.material_prices, app_main.EnhanceLoader.start, _win._names, images.fetch_icon)

    def restore():
        playerinfo.fetch_player_info, boostcollect.material_prices, app_main.EnhanceLoader.start, _win._names, images.fetch_icon = keep
    images.fetch_icon = lambda *a, **k: None
    playerinfo.fetch_player_info = lambda _sp, **k: info
    boostcollect.material_prices = lambda *a, **k: {o: 1000 for o in range(100, 180)}
    app_main.EnhanceLoader.start = lambda self: self.run()
    _win._names = {sp: "테스트강화"}
    return restore


def test_enhance_page_shows_result():
    """83 — 이름 → 카드 → [계산] → 맨 위 금액·글·금액 구간·그래프·단계 표. 기본값은 1강 → 8강 + 전부 보호."""
    sp = 812345678
    restore = _enhance_env(sp)
    try:
        _win._go_page("강화 비용")
        assert _win.cb_enh_start.currentData() == 1 and _win.cb_enh_target.currentData() == 8
        assert _win.cb_enh_protect.currentData() == 2, "기본값 = 실패할 수 있는 구간 전부 보호(사용자 2026-11-05)"
        assert not hasattr(_win, "cb_enh_q"), "맨 위 기준은 고르지 않는다(사용자 2026-11-06)"
        assert not _win.enh_result_box.isVisibleTo(_win), "계산 전에는 빈 칸을 안 보여 준다"
        _win.ed_enh_name.setText("테스트")
        assert _win.cb_enh_card.currentData() == sp
        _win.btn_enh_calc.click()
        assert _win.enh_result_box.isVisibleTo(_win)
        assert _win.tbl_enhance.rowCount() == 7 and _win.tbl_enhance.isVisibleTo(_win)
        assert _win.tbl_enhance.item(0, 0).text() == "1강 → 2강"
        assert _win.tbl_enhance.horizontalHeaderItem(0).text() == "강화 단계"
        assert _win.tbl_enhance.item(0, 3).text() == "1,000", "재료비 칸"
        assert _win.tbl_enhance.item(0, 4).text() == "-", "1강 도전에는 보호비가 없다"
        assert _win.tbl_enhance.item(1, 4).text() == "80만", "2강 200만 × 40%"
        assert _win.tbl_enhance.item(1, 5).text() == "80만 1,000", "1회 비용 = 재료비 + 보호비"
        assert isinstance(_win.tbl_enhance.itemDelegateForColumn(0), app_main.GradeStepDelegate), "단계 칸은 배지"
        est = _win._enh_shown.est
        # 칸 머리 말풍선 — 첫 칸 빼고 전부 · 계산 뒤에도 남는다
        for c, title in enumerate(app_main.MainWindow.ENHANCE_COLUMNS):
            tip = _win.tbl_enhance.horizontalHeaderItem(c).toolTip()
            assert (tip == "") == (title == "강화 단계"), title
        assert set(app_main.MainWindow.ENHANCE_COLUMN_TIPS) < set(app_main.MainWindow.ENHANCE_COLUMNS), "없는 칸 이름"
        # 표를 줄이지 않고 페이지가 스크롤한다 — 끝 줄이 표 안 스크롤에 숨었다(실제 창 1920×1080)
        for tb in (_win.tbl_enhance, _win.tbl_enh_budget):
            assert tb.minimumSizeHint().height() == tb._rows_height(), "줄 전부가 최소 높이"
            _app.processEvents()
            assert not tb.verticalScrollBar().isVisible(), "표 안 세로 막대가 없다"
        assert _win.tbl_enhance.item(6, 7).text() == f"{100 * est.steps[6].mean_cost / est.mean_cost:.0f}%", "비중 = 그 강화 평균 ÷ 전체 평균"
        head = _win.lb_enh_head.text()
        assert "테스트강화" in head and "100% 보호(시세의 40%)" in head and "85% 확률로 붙습니다" in head, head
        assert f"<b>{app_main.enhance_won(est.budget_at(85))}</b>" in head, "맨 위 금액은 굵게 — " + head
        assert T.GOOD not in head and T.BAD not in head, "맨 위 금액에는 색을 안 쓴다 — 좋고 나쁨이 아니다"
        assert "시세 받은 시각 " in head, "시세를 언제 읽었는지 — " + head   # 96 W1
        text = _win.lb_enh_result.text()
        assert "평균 31회" in text and "% 확률)" in text, text
        assert "85% 안에 끝나면" in text and "넘긴 15%는" in text and "30~95%" in text, text
        assert "보호 안 하면" in text and "4,335회" in text, "켠 값·끈 값을 늘 같이 — " + text
        assert "비용의" in text and "7강 → 8강에서 나옵니다" in text, "가장 비싼 강화 — " + text
        assert "평균이 가장 싼 보호 시작" in text and "보호 안 함" in text, "로더가 낸 답이 글까지 — " + text
        assert [_win.tbl_enh_budget.item(r, 0).text() for r in range(_win.tbl_enh_budget.rowCount())] == ["50%", "85%", "95%"]
        assert _win.chart_enh_budget.isVisibleTo(_win) and _win.chart_enh_budget._points == list(est.curve)
        # 87 V3 — 시도 횟수 표는 계산값 그대로 · 말풍선
        tries = _win.tbl_enh_tries
        assert est.tries and tries.isVisibleTo(_win) and tries.rowCount() == len(est.tries)
        assert [tries.item(r, 0).text() for r in range(tries.rowCount())] == [app_main.enhance_tries_text(n) for n, _p in est.tries]
        assert tries.item(0, 0).text() == "1회" and tries.item(0, 1).text() == "0%", "1강 → 8강은 한 번으로 못 닿는다"
        assert tries.item(tries.rowCount() - 1, 1).text() == "99% 넘게" or est.tries[-1][1] >= 0.99
        assert "다시 오르는 시도까지" in tries.horizontalHeaderItem(0).toolTip()
        assert [m[0] for m in _win.chart_enh_budget._marks] == [est.budget_at(85)], "85% 자리에 세로선"
        calls = []
        keep_fetch = playerinfo.fetch_player_info
        playerinfo.fetch_player_info = lambda *a, **k: calls.append(a)
        # 예산 칸 — 글·세로선 · 못 읽으면 말한다 · 끝을 넘겨도 100% 라고 안 한다
        _win.ed_enh_budget.setText(f"{est.budget_at(50)}")
        assert "50% 확률로 붙습니다" in _win.lb_enh_chance.text(), _win.lb_enh_chance.text()
        assert len(_win.chart_enh_budget._marks) == 2 and _win.chart_enh_budget._marks[1][0] == est.budget_at(50)
        _win.ed_enh_budget.setText("1조")
        assert "99% 넘게" in _win.lb_enh_chance.text()
        assert T.GOOD in _win.lb_enh_chance.text(), "85% 이상 초록"
        assert _win.lb_enh_chance.textFormat() == Qt.TextFormat.RichText, "글자 그대로면 <span> 이 화면에 찍힌다"
        _win.ed_enh_budget.setText("없음")
        assert _win.lb_enh_chance.text() == "금액을 읽지 못했습니다" and len(_win.chart_enh_budget._marks) == 1
        _win.ed_enh_budget.setText("")
        assert _win.lb_enh_chance.text() == ""
        assert not calls, "예산은 넥슨에 묻지 않는다"
        playerinfo.fetch_player_info = keep_fetch
        # 안 함 → 자리가 바뀌고 비율 고르기가 잠긴다 · 시도가 많아도 흉내가 돌면 구간이 나온다
        _win.cb_enh_protect.setCurrentIndex(_win.cb_enh_protect.findData(None))
        assert not _win.cb_enh_rate.isEnabled()
        _win.btn_enh_calc.click()
        head, text = _win.lb_enh_head.text(), _win.lb_enh_result.text()
        assert "보호 없음" in head and "평균 4,335회" in text and "100% 보호하면" in text and "평균 31회" in text, text
        assert "시세의" not in text and "30~95%" not in text, text
        assert _win.tbl_enh_budget.rowCount() == 3, "흉내가 줄어도 95% 위쪽은 남는다"
        # 끝까지 못 돌리는 구간 — 평균만, 구간·그래프는 숨긴다
        _win.cb_enh_start.setCurrentIndex(_win.cb_enh_start.findData(8))
        _win.cb_enh_target.setCurrentIndex(_win.cb_enh_target.findData(10))
        _win.btn_enh_calc.click()
        assert "확률별 금액은 낼 수 없습니다" in _win.lb_enh_head.text(), _win.lb_enh_head.text()
        assert not _win.chart_enh_budget.isVisibleTo(_win) and not _win.tbl_enh_budget.isVisibleTo(_win)
        assert not _win.ed_enh_budget.isVisibleTo(_win), "금액 구간이 없으면 예산 칸도 없다"
        # 87 V3 — 금액 흉내를 못 돌려도 시도 횟수는 정확히 풀어서 나온다
        assert _win.enh_budget_panel.isVisibleTo(_win) and _win.tbl_enh_tries.isVisibleTo(_win)
        assert _win.tbl_enh_tries.item(_win.tbl_enh_tries.rowCount() - 1, 0).text().endswith("만 회")
        _app.processEvents()
        assert _win.enh_budget_panel.x() == _win.tbl_enhance.x(), "그래프가 없어도 왼쪽에 붙는다(가운데로 가지 않는다)"
        # 일부 구간만 보호 — 어디부터인지 적는다 · 그 위만 보호비
        _win.cb_enh_protect.setCurrentIndex(_win.cb_enh_protect.findData(8))
        _win.cb_enh_rate.setCurrentIndex(_win.cb_enh_rate.findData(30))
        assert _win.cb_enh_rate.isEnabled()
        _win.cb_enh_start.setCurrentIndex(_win.cb_enh_start.findData(1))
        _win.btn_enh_calc.click()
        assert "8강 도전부터" in _win.lb_enh_head.text(), _win.lb_enh_head.text()
        assert _win.tbl_enhance.rowCount() == 9
        assert _win.tbl_enhance.item(6, 4).text() == "-" and _win.tbl_enhance.item(7, 4).text() == "240만", "8강 800만 × 30%"
        assert _win.chart_enh_budget.isVisibleTo(_win), "앞 계산이 숨긴 칸을 되돌린다"
        # 전부 보호 + 비율 고름
        _win.cb_enh_protect.setCurrentIndex(_win.cb_enh_protect.findData(2))
        _win.cb_enh_start.setCurrentIndex(_win.cb_enh_start.findData(8))
        _win.btn_enh_calc.click()
        assert _win.tbl_enhance.rowCount() == 2
        assert "시세의 30%" in _win.lb_enh_head.text() and "45회" in _win.lb_enh_result.text()
        _check_elisions([_win.tbl_enhance, _win.tbl_enh_budget, _win.tbl_enh_tries])
        # 비싼 카드 — 1의 자리까지 적으면 칸이 가장 길다. 그래도 안 잘려야 한다
        info = playerinfo.PlayerInfo(sp_id=sp, name="테스트강화", ovr=115,
                                     prices={g: f"{g * 7_123_456_789:,} BP" for g in range(1, 14)})
        playerinfo.fetch_player_info = lambda _sp, **k: info
        boostcollect.material_prices = lambda *a, **k: {o: 123_456_789 for o in range(100, 180)}
        _win.cb_enh_start.setCurrentIndex(0)
        _win.cb_enh_target.setCurrentIndex(_win.cb_enh_target.findData(13))
        _win.cb_enh_rate.setCurrentIndex(0)
        _win.btn_enh_calc.click()
        assert _win.tbl_enhance.rowCount() == 12
        _check_elisions([_win.tbl_enhance, _win.tbl_enh_budget])
        # 받은 넥슨 그림이 단계 칸까지 온다
        widgets._grade_holo["pm"] = None
        assert widgets.grade_holo() is None, "아래 단언의 전제 — 아직 못 받았다"
        with tempfile.TemporaryDirectory() as folder:
            png = pathlib.Path(folder) / "holo.png"
            pm = QPixmap(4, 4)
            pm.fill(QColor(0, 200, 0))
            assert pm.save(str(png))
            asked = []
            images.fetch_icon = lambda url, *a, **k: (asked.append(url), png)[1]
            _win.btn_enh_calc.click()
            assert asked == [images.GRADE_HOLO_URL] and widgets.grade_holo() is not None
        widgets._grade_holo["pm"] = None
    finally:
        restore()
        _win.ed_enh_budget.setText("")
        _win.cb_enh_protect.setCurrentIndex(_win.cb_enh_protect.findData(2))
        _win.cb_enh_rate.setCurrentIndex(0)
        _win.cb_enh_start.setCurrentIndex(0)
        _win.cb_enh_target.setCurrentIndex(_win.cb_enh_target.findData(config.ENHANCE_DEFAULT_TARGET))
        _win.ed_enh_name.setText("")
        _win._go_page("대시보드")


def test_enhance_page_refuses_and_reports_failure():
    sp = 812345679
    restore = _enhance_env(sp)
    calls = []

    def no_web(*a, **k):
        calls.append(a)
        raise playerinfo.PlayerInfoError(config.WEB_DATA_OFF_MSG, "off")
    try:
        _win._go_page("강화 비용")
        _win.ed_enh_name.setText("")
        _win.btn_enh_calc.click()
        assert "선수 이름을 넣고" in _win.lb_enh_status.text() and not calls
        _win.ed_enh_name.setText("테스트")
        _win.cb_enh_start.setCurrentIndex(_win.cb_enh_start.findData(9))
        _win.btn_enh_calc.click()
        assert "높아야" in _win.lb_enh_status.text(), "목표가 낮으면 요청을 보내지 않는다"
        _win.cb_enh_start.setCurrentIndex(0)
        _win.btn_enh_calc.click()
        assert _win.enh_result_box.isVisibleTo(_win), "아래 단언의 전제 — 앞 결과가 떠 있다"
        playerinfo.fetch_player_info = no_web
        _win.btn_enh_calc.click()
        assert calls and _win.lb_enh_status.text() == config.WEB_DATA_OFF_MSG
        assert not _win.enh_result_box.isVisibleTo(_win), "실패한 계산이 앞 결과를 남기면 안 된다"
        assert not _win.tbl_enhance.isVisibleTo(_win)
    finally:
        restore()
        _win.cb_enh_start.setCurrentIndex(0)
        _win.ed_enh_name.setText("")
        _win._go_page("대시보드")


def test_enhance_money_keeps_detail_above_100m():
    assert app_main.enhance_won(199_000_000.4) == "1억 9,900만", "short_bp 면 1억으로 낮게 읽힌다"
    assert app_main.enhance_won(50_000) == "5만"
    assert app_main.enhance_won(999) == "999"
    assert app_main.enhance_won(0) == "0", "보호비 없는 글이 NA 로 나오면 안 된다"
    assert app_main.enhance_won(39_210) == "3만 9,210", "1의 자리까지(사용자 2026-11-06)"
    assert app_main.enhance_won(1_234_567_891) == "12억 3,456만 7,891"
    assert app_main.enhance_won(200_000_005) == "2억 5"
    assert app_main.enhance_won(359_435_000_000_000) == "359조 4,350억", "3,594,350억 으로 적었다(사용자 2026-11-06)"
    assert app_main.enhance_won(10 ** 12) == "1조"
    assert app_main.enhance_won(10 ** 12 + 7) == "1조 7"


def test_enhance_rough_amounts_for_huge_values():
    """보호 비교 줄 — 끝자리를 믿을 수 없는 큰 값은 줄인다(분수로 풀어 앞 다섯 자리까지만 같다)."""
    assert app_main.enhance_rough(359_435_000_000_000) == "약 359조"
    assert app_main.enhance_rough(10 ** 12) == "약 1조"
    assert app_main.enhance_rough(10 ** 12 - 1) == "9,999억 9,999만 9,999", "1조 아래는 그대로"
    assert app_main.enhance_count_text(359_434_829_491) == "약 3,594억 회"
    assert app_main.enhance_count_text(10 ** 8) == "약 1억 회"
    assert app_main.enhance_count_text(10 ** 8 - 1) == "99,999,999회"
    assert app_main.enhance_count_text(30.6) == "31회"


def test_enhance_colors_follow_good_and_bad():
    """사용자 2026-11-06 — 더 싼 결론 초록 · 더 비싼 쪽 금액과 넘긴 15% 빨강 · 예산 확률 85% 이상 초록 · 50% 미만 빨강."""
    import enhance
    mats = {o: 1000 for o in range(100, 180)}
    cheap_cards = {g: 1000 for g in range(1, 14)}
    on = enhance.estimate(115, None, 1, 8, cheap_cards, mats)
    off = enhance.estimate(115, None, 1, 8, cheap_cards, mats, None, simulate=False)
    good, bad = f"color:{T.GOOD}", f"color:{T.BAD}"
    line = app_main.enhance_compare_line(on, off)     # 안 하면 더 비싸다
    assert f"<span style='{bad}'>{app_main.enhance_rough(off.mean_cost)}</span>" in line, line
    assert f"<b><span style='{good}'>보호하는 쪽이" in line, line
    line = app_main.enhance_compare_line(off, on)     # 보호하면 더 싸다 — 그 금액은 초록
    assert f"<span style='{good}'>{app_main.enhance_rough(on.mean_cost)}</span>" in line and bad not in line, line
    # 아주 큰 값 — 금액·차이·횟수 모두 줄여 적는다(3,594,350억 · 3594억회 사용자 2026-11-06)
    huge = enhance.estimate(115, None, 10, 13, {g: 10 ** 9 for g in range(1, 14)}, mats, None, simulate=False)
    guard = enhance.estimate(115, None, 10, 13, {g: 10 ** 9 for g in range(1, 14)}, mats, simulate=False)
    assert huge.mean_cost > 10 ** 13 and huge.mean_attempts > 10 ** 10, "아래 단언의 전제"
    line = app_main.enhance_compare_line(guard, huge)
    assert app_main.enhance_rough(huge.mean_cost) in line and f"평균 {app_main.enhance_rough(huge.mean_cost - guard.mean_cost)} 싸다" in line, line
    assert "억 회" in line and app_main.enhance_won(huge.mean_cost) not in line, line
    text = app_main.enhance_summary_html(on)
    assert f"<span style='{bad}'>{app_main.enhance_won(on.tail_mean(85))}</span>" in text, text
    assert f"<span style='{good}'>{app_main.enhance_won(on.within_mean(85))}</span>" in text, text
    # 예산 확률 — 경계 양쪽
    for chance, want in ((0.85, good), (0.8499, None), (0.5, None), (0.4999, bad)):
        html_text = app_main.enhance_chance_html(100, chance)
        assert (good in html_text) == (want == good) and (bad in html_text) == (want == bad), (chance, html_text)


def test_enhance_top_step_and_best_lines():
    import enhance
    mats = {o: 1000 for o in range(100, 180)}
    prices = {g: 1_000_000 * g for g in range(1, 14)}
    est = enhance.estimate(115, None, 1, 8, prices, mats, simulate=False)
    top = max(est.steps, key=lambda s: s.mean_cost)
    assert top.grade == 7 and top.mean_cost > est.steps[5].mean_cost, "아래 단언의 전제"
    line = app_main.enhance_top_step_line(est)
    assert app_main.enhance_share(top.mean_cost, est.mean_cost) in line and "7강 → 8강에서" in line, line
    # 일부러 낮은 단계가 더 비싼 시세 — 마지막 단계로 고정해 적으면 빨개진다
    skew = enhance.estimate(115, None, 1, 8, {**prices, 2: 10 ** 10}, mats, simulate=False)
    assert max(skew.steps, key=lambda s: s.mean_cost).grade == 2
    assert "2강 → 3강에서" in app_main.enhance_top_step_line(skew)
    one = enhance.estimate(115, None, 8, 9, prices, mats, simulate=False)
    assert app_main.enhance_top_step_line(one) == "", "단계가 하나면 뻔하다"
    # 가장 싼 보호 시작 — 지금 고른 값이면 안 적는다 · 더 싸면 어디부터인지와 평균·횟수
    cards = {g: 10 ** 8 if g < 8 else 1000 for g in range(1, 14)}
    wide = enhance.estimate(115, None, 1, 11, cards, mats, simulate=False)
    pf, cheap = best = enhance.cheapest_protect_from(115, None, 1, 11, cards, mats)
    assert cheap.mean_cost < wide.mean_cost and pf == 8, f"아래 단언의 전제 — {pf}"
    line = app_main.enhance_best_line(wide, best)
    assert f"{pf}강부터" in line and app_main.enhance_rough(cheap.mean_cost) in line and app_main.enhance_count_text(cheap.mean_attempts) in line, line
    assert f"color:{T.GOOD}" in line
    assert app_main.enhance_best_line(cheap, best) == "", "이미 가장 싼 칸을 골랐다"
    assert app_main.enhance_best_line(wide, None) == ""
    none_best = (None, enhance.estimate(115, None, 1, 11, cards, mats, None, simulate=False))
    assert "보호 안 함" in app_main.enhance_best_line(replace(wide, mean_cost=none_best[1].mean_cost + 10), none_best)
    # 글까지 온다
    assert line in app_main.enhance_summary_html(wide, None, best)


def test_enhance_loader_passes_cheapest_and_skips_for_excluded():
    """로더가 가장 싼 보호와 시세 시각을 화면까지 넘긴다 · 보호 제외 클래스는 묻지 않는다."""
    sp = 812345678
    restore = _enhance_env(sp)
    asked = []
    keep = core.enhance_cheapest_protect
    try:
        core.enhance_cheapest_protect = lambda *a, **k: (asked.append(a), keep(*a, **k))[1]
        got = []
        w = app_main.EnhanceLoader(1, sp, 1, 8, 2, None)
        w.loaded.connect(lambda _r, p: got.append(p))
        w.run()
        assert len(asked) == 1 and got[0].best is not None and got[0].priced_at, got
        asked.clear()
        got.clear()
        excluded = 596 * 1_000_000 + 1
        w = app_main.EnhanceLoader(1, excluded, 8, 9, 2, None)
        w.loaded.connect(lambda _r, p: got.append(p))
        w.run()
        assert got[0].est.protect_excluded and got[0].best is None and not asked
    finally:
        core.enhance_cheapest_protect = keep
        restore()


def test_card_name_suggestions_with_real_keys():
    """이름 칸 — 한글은 키가 아니라 입력기 이벤트로 온다(QTest.keyClicks 에 한글을 주면 앱이 통째로 죽는다 · 실측 2026-11-06).
    조합 중인 글자는 text() 에 없어서, 확정된 글자가 2글자가 될 때 뜬다. 강화 비용·구단주 찾기가 같은 함수."""
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    keep = _win._names
    names = {100_000_000 + i: f"가나다 {i}호" for i in range(8)}
    names[300_000_000] = names[400_000_000] = "가나다 3호"       # 같은 이름의 다른 시즌 — 목록에 한 줄
    names[500_000_000] = "다른선수"
    names[600_000_000] = names[700_000_000] = "Abc Def"
    _win._names = names
    was_visible = _win.isVisible()
    assert config.CARD_NAME_SUGGESTIONS == 5, "사용자 2026-11-06"

    def ime(edit, preedit="", commit=""):
        event = QInputMethodEvent(preedit, [])
        event.setCommitString(commit)
        QApplication.sendEvent(edit, event)
        _app.processEvents()
    try:
        _win.show()
        for page, edit, combo in (("강화 비용", _win.ed_enh_name, _win.cb_enh_card),
                                  ("선수로 구단주 찾기", _win.ed_owner_name, _win.cb_owner_card)):
            _win._go_page(page)
            edit.clear()
            _win.activateWindow()   # 입력기 글자는 초점 있는 칸에만 온다 — 앞 테스트가 창을 활성화해 뒀는지에 기대지 않는다
            _app.processEvents()
            edit.setFocus()
            _app.processEvents()
            assert edit.hasFocus(), "전제 — 초점 없는 칸엔 팝업을 안 연다(_refresh_card_candidates)"
            assert config.CARD_NAME_MIN_CHARS == 1, "사용자 2026-11-08 — 한 글자부터"
            # 조합 중인 첫 글자 하나만으로 뜬다 — 칸(text)은 아직 비었다(사용자 2026-11-08 "민만 치면 안 나온다")
            ime(edit, preedit="다")
            comp = edit.completer()
            assert edit.text() == "" and comp.model().stringList() == ["다른선수"] + [f"가나다 {i}호" for i in range(4)], \
                comp.model().stringList()
            assert comp.popup().isVisible(), "조합만 바뀌어도 팝업이 떠야 한다 — " + page
            assert combo.count() == 11, "옆 칸도 조합 글자로 — " + str(combo.count())
            ime(edit, preedit="른", commit="다")   # 확정 "다" + 조합 "른" = "다른" — 확정 뒤 textChanged 도 조합 글자를 센다
            assert edit.text() == "다" and comp.model().stringList() == ["다른선수"], comp.model().stringList()
            edit.setText("나다")   # 코드로 바꾼 글 — 남은 조합 글자("른")를 안 붙인다
            assert comp.model().stringList() == [f"가나다 {i}호" for i in range(5)], comp.model().stringList()
            edit.clear()
            ime(edit, preedit="다")
            ime(edit, preedit="")   # 조합을 지우면 목록도 빈다
            assert comp.model().rowCount() == 0 and combo.count() == 0 and not comp.popup().isVisible()
            ime(edit, preedit="가")
            ime(edit, preedit="나", commit="가")
            assert edit.text() == "가" and comp.model().stringList() == [f"가나다 {i}호" for i in range(5)], \
                "확정 글자 + 조합 글자 — " + str(comp.model().stringList())
            ime(edit, preedit="다", commit="나")
            rows = comp.model().stringList()
            assert rows == [f"가나다 {i}호" for i in range(config.CARD_NAME_SUGGESTIONS)], rows
            assert comp.popup().isVisible(), "입력기로 쳐도 팝업이 떠야 한다 — " + page
            assert comp.popup().model().rowCount() == config.CARD_NAME_SUGGESTIONS, "치는 글자에 맞는 목록 그대로"
            ime(edit, commit="다")
            # 고르기 — 시즌이 셋인 이름 · 칸에 이름이 들어가고 옆 칸에 그 선수 카드 전부
            popup = comp.popup()
            popup.setCurrentIndex(popup.model().index(3, 0))
            QTest.keyClick(popup, Qt.Key.Key_Return)
            _app.processEvents()
            assert edit.text() == "가나다 3호" and not popup.isVisible(), edit.text()
            assert combo.count() == 3 and {combo.itemData(i) for i in range(combo.count())} == {100_000_003, 300_000_000, 400_000_000}
            # 이름 중간으로 찾아도 팝업에 남는다
            edit.clear()
            ime(edit, commit="나다")
            assert popup.isVisible() and popup.model().rowCount() == config.CARD_NAME_SUGGESTIONS, popup.model().rowCount()
            edit.clear()
            assert comp.model().rowCount() == 0 and combo.count() == 0, "지우면 목록도 빈다"
            assert not popup.isVisible(), "코드로 비워도 빈 네모가 남지 않는다"
            # 영문은 키 입력으로 온다
            edit.setFocus()
            QTest.keyClicks(edit, "Ab")
            _app.processEvents()
            assert comp.model().stringList() == ["Abc Def"] and popup.isVisible(), comp.model().stringList()
            edit.clear()
            edit.clearFocus()
            _app.processEvents()
            ime(edit, preedit="다")
            assert comp.model().rowCount() and not popup.isVisible(), "초점 없는 칸엔 팝업을 안 연다 — 열면 골라도 칸에 안 들어간다"
            ime(edit, preedit="")
    finally:
        _win._names = keep
        _win.ed_enh_name.clear()
        _win.ed_owner_name.clear()
        _win._go_page("대시보드")
        if not was_visible:
            _win.hide()


def test_enhance_summary_verdict():
    import enhance
    mats = {o: 1000 for o in range(100, 180)}
    prices = {g: 1_000_000 * g for g in range(1, 14)}
    cheap = enhance.estimate(115, None, 1, 8, {**prices, 8: 900_000_000}, mats)
    buy = enhance.estimate(115, None, 1, 8, {**prices, 8: 1_000}, mats)
    # 산 값과 견주되 판정은 안 한다 — 앱은 매물이 있는지 모른다(사용자 2026-11-06)
    for est in (cheap, buy):
        text = app_main.enhance_summary_html(est)
        assert "참고: 8강 시세" in text and "싸다" not in text, text
    # 표 한 줄 — 재료비 + 보호비 = 1회 비용이 글자로도 맞는다
    odd = enhance.estimate(115, None, 2, 3, {2: 12_345_678, 3: 1}, {o: 1_357 for o in mats}, simulate=False)
    row = app_main.enhance_row(odd.steps[0], odd.mean_cost)
    assert [enhance.parse_money(row[i]) for i in (3, 4, 5)] == [1_357, 4_938_271, 1_357 + 4_938_271], row
    # 보호 켠/끈 한 줄 — 어느 쪽이 싼지 양쪽 다 · 같은 값이면(보호가 안 걸리는 구간) 안 나온다
    cheap_cards = {g: 1000 for g in range(1, 14)}
    off = enhance.estimate(115, None, 1, 8, cheap_cards, mats, None, simulate=False)
    on = enhance.estimate(115, None, 1, 8, cheap_cards, mats, simulate=False)
    assert "보호하는 쪽이" in app_main.enhance_compare_line(on, off), "시세가 싸면 보호비보다 떨어져서 드는 재료가 크다"
    pricey = enhance.estimate(115, None, 7, 8, {7: 10 ** 12, 8: 10 ** 12}, {o: 1 for o in mats}, simulate=False)
    plain = enhance.estimate(115, None, 7, 8, {7: 10 ** 12, 8: 10 ** 12}, {o: 1 for o in mats}, None, simulate=False)
    assert "보호 안 하는 쪽이" in app_main.enhance_compare_line(pricey, plain)
    low_a = enhance.estimate(115, None, 1, 2, prices, mats, simulate=False)
    low_b = enhance.estimate(115, None, 1, 2, prices, mats, None, simulate=False)
    assert app_main.enhance_compare_line(low_a, low_b) == "", "1강 도전엔 보호가 없다 — 같은 값을 두 번 안 적는다"
    assert app_main.enhance_compare_line(on, None) == ""
    assert "<b>&lt;b&gt;</b>" in app_main.enhance_head_html("<b>", cheap), "선수 이름은 글자 그대로"
    assert "시세 " not in app_main.enhance_head_html("가", cheap), "시각을 모르면 안 적는다"
    assert "시세 받은 시각 11-06 12:30" in app_main.enhance_head_html("가", cheap, "2026-11-06T12:30")   # 96 W1 — 이 PC 가 받은 시각
    # 시도가 너무 많으면 넘긴 나머지 줄이 없고, 시세를 모르면 견주지 않는다
    many = enhance.estimate(115, None, 8, 10, prices, mats, None)
    assert "넘긴" not in app_main.enhance_summary_html(many) and "% 확률" not in app_main.enhance_summary_html(many)
    no_price = enhance.estimate(115, None, 1, 5, {}, mats, None)
    assert "참고" not in app_main.enhance_summary_html(no_price)
    # 어느 기준이 표본 밖이면 숫자를 지어내지 않는다
    few = replace(cheap, costs=tuple(range(1, 101)))
    assert "85% 금액은 낼 수 없습니다" in app_main.enhance_head_html("가", few)
    assert app_main.enhance_chance_text(0.004) == "1% 미만" and app_main.enhance_chance_text(0.5) == "50%"
    assert app_main.enhance_share(1, 1000) == "1% 미만" and app_main.enhance_share(0, 1000) == "0%" and app_main.enhance_share(1, 0) == "0%"


def test_grade_step_delegate_draws_badges():
    """83 — 단계 칸은 번호 배지 둘. 공지 꼴 색 구간 · 못 읽는 글자는 글자 그대로 · 그리기 실패는 표를 안 죽인다."""
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
    from widgets import GradeStepDelegate, grade_icon_style
    assert GradeStepDelegate.grades_of("12강 → 13강") == (12, 13)
    assert GradeStepDelegate.grades_of("합계") is None and GradeStepDelegate.grades_of(None) is None
    # 넥슨 공지: 1 회색 · 2~4 브론즈 · 5~7 실버 · 8~10 골드 · 11~13 홀로그램(경계 양쪽을 잰다)
    tier = {g: grade_icon_style(g)[0] for g in range(1, 14)}
    groups = ((1,), (2, 3, 4), (5, 6, 7), (8, 9, 10), (11, 12, 13))
    assert [len({tier[g] for g in grp}) for grp in groups] == [1] * 5
    assert len({tier[grp[0]] for grp in groups}) == 5
    t = QTableWidget(2, 1)
    t.resize(300, 120)
    t.setColumnWidth(0, 200)
    dg = GradeStepDelegate(t)
    t.setItemDelegateForColumn(0, dg)
    t.setItem(0, 0, QTableWidgetItem("10강 → 11강"))
    t.setItem(1, 0, QTableWidgetItem("합계"))
    t.show()
    _app.processEvents()
    before = t.grab().toImage()
    rect = t.visualItemRect(t.item(0, 0))
    # 배지 바탕색이 칸에 실제로 찍힌다 — 왼쪽은 골드, 오른쪽은 홀로그램
    cx = rect.center().x() + t.viewport().x()
    cy = rect.center().y() + t.viewport().y()
    gold = before.pixelColor(cx - 30, cy + 3)
    assert gold.red() > 200 and gold.green() > 150 and gold.blue() < 150, gold.name()
    holo = before.pixelColor(cx + 30, cy - 5)
    assert holo.blue() > 150, holo.name()
    # 넥슨 그림을 받으면 11강 이상 바탕을 그 그림으로 — 10강 쪽은 그대로
    pm = QPixmap(8, 8)
    pm.fill(QColor(0, 200, 0))
    widgets._grade_holo["pm"] = pm
    after = t.grab().toImage()
    assert after.pixelColor(cx + 30, cy - 5).green() > 150 and after.pixelColor(cx + 30, cy - 5).red() < 80, after.pixelColor(cx + 30, cy - 5).name()
    assert after.pixelColor(cx - 30, cy + 3) == gold, "10강 배지는 그림을 안 쓴다"
    widgets._grade_holo["pm"] = None
    assert not widgets.set_grade_holo(None) and widgets.grade_holo() is None, "못 받으면 그러데이션"
    keep = dg._draw
    dg._draw = lambda *a: 1 / 0
    try:
        t.grab()
        assert dg.failed >= 1, "실패를 세고 글자로 넘어간다"
    finally:
        dg._draw = keep
        t.close()


def test_budget_curve_chart_paints():
    """83 — 빈 값은 빈 표시 · 곡선 안 세로선만 · 글자 칸이 창 안."""
    chart = charts.BudgetCurveChart()
    chart.resize(600, 260)
    chart.set_data([], [], str)
    chart.grab()
    assert chart.mark_rects == [] and chart._hits == []
    pts = [(100 * i, float(i)) for i in range(1, 100)]
    chart.set_data(pts, [(5000, "기준", T.TEXT_DIM, True), (9_000_000, "밖", T.CHART_RANK, False)], lambda v: f"{v:,.0f}")
    chart.grab()
    assert len(chart.mark_rects) == 1, "곡선 끝을 넘긴 예산은 그리지 않는다(축이 늘어나 곡선이 눌린다)"
    assert all(r.left() >= 0 and r.right() <= chart.width() for r in chart.mark_rects)
    assert len(chart._hits) == len(pts), "점마다 툴팁"
    assert "5,000" in next(t for a, t in chart._hits if a.contains(QPointF(chart._hits[49][0].center())))
    assert charts._nice_step(9900, 4) == 5000 and charts._nice_step(1000, 4) == 500 and charts._nice_step(400, 4) == 100


def test_enhance_late_result_is_dropped():
    _win._enh_req = 7
    _win.lb_enh_result.setText("그대로")
    _win._on_enhance_failed(6, "늦은 실패")
    _win._on_enhance_progress(6, 1, 2)
    assert "늦은" not in _win.lb_enh_status.text() and "1/2" not in _win.lb_enh_status.text()
    _win._on_enhance_loaded(6, ("늦은", None, None))
    assert _win.lb_enh_result.text() == "그대로"


def test_card_owner_find():
    """12 — 이름 후보 → 카드 → 최근 30일 선발 구단주 · 색인 안 된 경기가 있으면 "색인 중" 줄."""
    d = json.loads(json.dumps(_DETAILS[0]))
    d["matchId"], d["matchDate"] = "ownerm0", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    me = next(p for p in d["matchInfo"] if p["ouid"] == _OUID)
    sp = next(p["spId"] for p in me["player"] if p.get("spPosition") not in (None, 28))
    keep = dict(_win._names)
    conn = store.open_db(config.DB_PATH)
    try:
        store.save_matches(conn, [d])
        conn.execute("INSERT INTO matches (match_id, match_type, match_date, payload) VALUES ('raw1', 52, ?, '{}')",
                     (d["matchDate"],))
        conn.commit()
    finally:
        conn.close()
    try:
        _win._names = {sp: "테스트카드선수"}
        _win._go_page("선수로 구단주 찾기")
        _win.ed_owner_name.setText("테스트")
        assert _win.cb_owner_card.count() == 1 and _win.cb_owner_card.currentData() == sp
        _win.btn_owner_find.click()
        assert [o["ouid"] for o in _win.owner_rows] == [_OUID], _win.owner_rows
        assert _win.tbl_owner.rowCount() == 1
        assert _win.lb_owner_backfill.isVisibleTo(_win) and "색인" in _win.lb_owner_backfill.text(), \
            "색인 안 된 경기가 있는데 조용히 덜 나왔다"
        _win.ed_owner_name.setText("테")
        assert _win.cb_owner_card.count() == 1, "한 글자부터(사용자 2026-11-08)"
        _win.ed_owner_name.setText("")
        assert _win.cb_owner_card.count() == 0, "비우면 후보도 빈다"
    finally:
        _win._names = keep
        conn = store.open_db(config.DB_PATH)
        try:
            for t in ("match_squads", "squad_match", "squad_owner"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM match_players WHERE match_id IN ('ownerm0', 'raw1')")
            conn.execute("DELETE FROM matches WHERE match_id IN ('ownerm0', 'raw1')")
            conn.commit()
        finally:
            conn.close()
        _win._backfill_left = None
        _win._go_page("대시보드")

def test_card_owner_find_player_scope():
    """N30(50단계) — [카드/선수] 전환: 선수면 pid 가 같은 시즌 카드를 합쳐 찾고 카드별·강화별 사용자 표가 나온다."""
    d1 = json.loads(json.dumps(_DETAILS[0]))
    d1["matchId"], d1["matchDate"] = "ownerp1", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    sp = next(p["spId"] for p in next(p for p in d1["matchInfo"] if p["ouid"] == _OUID)["player"]
              if p.get("spPosition") not in (None, 28))
    sp2 = sp + 1_000_000 if sp < 900_000_000 else sp - 1_000_000       # 같은 선수(pid)의 다른 시즌 카드
    d2 = json.loads(json.dumps(d1))
    d2["matchId"] = "ownerp2"
    opp = next(p for p in d2["matchInfo"] if p["ouid"] != _OUID)
    next(p for p in opp["player"] if p.get("spPosition") not in (None, 28))["spId"] = sp2   # 상대는 다른 시즌 카드
    keep = dict(_win._names)
    conn = store.open_db(config.DB_PATH)
    try:
        store.save_matches(conn, [d1, d2])
    finally:
        conn.close()
    try:
        _win._names = {sp: "테스트카드선수", sp2: "테스트카드선수", sp + 1: "테스트카드선수"}   # sp+1 = 이름만 같은 다른 선수
        _win._go_page("선수로 구단주 찾기")
        _win.ed_owner_name.setText("테스트")
        _win.cb_owner_card.setCurrentIndex(_win.cb_owner_card.findData(sp))
        assert _win.cb_owner_scope.currentData() == _win.OWNER_SCOPE_CARD, "기본은 카드 하나"
        assert _win.cb_owner_scope.isVisibleTo(_win), "전환 콤보가 화면에 안 달렸다"
        _win.btn_owner_find.click()
        card_only = {o["ouid"] for o in _win.owner_rows}
        assert _OUID in card_only and not _win.fold_owner_cards.isVisibleTo(_win), "카드 모드엔 카드별 표가 없다"
        _win.cb_owner_scope.setCurrentIndex(_win.cb_owner_scope.findData(_win.OWNER_SCOPE_PLAYER))
        _win.btn_owner_find.click()
        both = {o["ouid"] for o in _win.owner_rows}
        opp_ouid = opp["ouid"]
        assert {_OUID, opp_ouid} <= both and opp_ouid not in card_only, "다른 시즌 카드를 쓴 상대가 선수 모드에만 나온다"
        assert _win.fold_owner_cards.isVisibleTo(_win)
        t = _win.tbl_owner_cards
        cards = {t.item(r, 0).text() for r in range(t.rowCount())}
        assert t.rowCount() == 2 and all("테스트카드선수" in c for c in cards), cards   # sp·sp2 둘 — 이름만 같은 sp+1 은 없다
        assert f"시즌 카드 2장" in _win.lb_owner_status.text(), _win.lb_owner_status.text()
        assert all(t.item(r, 1).text() and t.item(r, 3).text() for r in range(t.rowCount()))
        # 카드 모드로 돌아오면 카드별 표는 다시 숨는다
        _win.cb_owner_scope.setCurrentIndex(_win.cb_owner_scope.findData(_win.OWNER_SCOPE_CARD))
        _win.btn_owner_find.click()
        assert not _win.fold_owner_cards.isVisibleTo(_win)
    finally:
        _win._names = keep
        _win.cb_owner_scope.setCurrentIndex(0)
        conn = store.open_db(config.DB_PATH)
        try:
            for t_ in ("match_squads", "squad_match", "squad_owner"):
                conn.execute(f"DELETE FROM {t_}")
            conn.execute("DELETE FROM match_players WHERE match_id IN ('ownerp1', 'ownerp2')")
            conn.execute("DELETE FROM matches WHERE match_id IN ('ownerp1', 'ownerp2')")
            conn.commit()
        finally:
            conn.close()
        _win._backfill_left = None
        _win._go_page("대시보드")


# ── 2.3.1 랭커 메타 — 랭킹 추이 탭 셋 · [정보] 수집 상태 · 끄는 길의 사람별 지우기(작업 스레드) ──────────

def _wait_maint(ms=15000):
    for w in list(_win._maint_workers):
        assert w.wait(ms), "정리 작업자가 안 끝났다"
    s = _win._rank_sched
    if s is not None and s.maint is not None:
        assert s.maint.wait(ms)
    _app.processEvents()


def _rank_files_gone():
    for suffix in ("", "-wal", "-shm"):
        pathlib.Path(str(config.RANK_DB_PATH) + suffix).unlink(missing_ok=True)
    # rankmeta 캐시 키는 (경로 · 스냅숏 id·시각) — 테스트마다 같은 꼴(같은 시간대 _meta_db)로 다시 만들면 키가 같아
    # 앞 테스트의 값을 읽는다(67 ② — 팀컬러 창 테스트 뒤 비주류 거르기 테스트가 옛 인원을 읽었다)
    rankmeta.clear_cache()


class _MetaEnv:
    """수집 켜짐 · 안내 6 동의 · 켠 시각 — 디스크의 .env 로(사람별 표는 거기서 다시 읽는다). 끝나면 정리 작업자를 기다리고 지운다."""
    ON_AT = "2026-10-10T08:00:00.000001"

    def __enter__(self):
        _wait_maint()
        self.keep_start = app_main.RankMaintWorker.start
        app_main.RankMaintWorker.start = lambda w: w.run()   # 스모크는 QThread.start 를 막는다 — 정리를 그 자리에서
        self.keep = (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED,
                     {v: os.environ.get(v) for v in (config.WEB_DATA_VAR, config.RANK_COLLECT_VAR,
                                                     config.RANK_COLLECT_ON_AT_VAR, config.NOTICE_VAR)})
        _rank_files_gone()
        self.write()
        return self

    def write(self, collect=1, notice=None, on_at=ON_AT):
        notice = config.NOTICE_VERSION if notice is None else notice
        text = f"{config.WEB_DATA_VAR}=1\n{config.RANK_COLLECT_VAR}={collect}\n{config.NOTICE_VAR}={notice}\n"
        if on_at:
            text += f"{config.RANK_COLLECT_ON_AT_VAR}={on_at}\n"
        config.ENV_PATH.write_text(text, encoding="utf-8")
        config.read_env_switches()
        config.NOTICE_ACCEPTED = notice

    def __exit__(self, *exc):
        _wait_maint()
        app_main.RankMaintWorker.start = self.keep_start
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, env = self.keep
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.ENV_PATH.unlink(missing_ok=True)
        _rank_files_gone()
        return False


def _meta_db(days=8, people=300):
    """rank.db 에 하루 간격 스냅숏(같은 시즌 · 기준 시각 있음) + 메타 처리. 1~3위는 슈챔(등급 0)."""
    rows = [ranker.RankRow(rank=i, profile_sn=800 + i, nickname=f"m{i}", elo=3000.0 + i * 5, grade=0 if i <= 3 else 2,
                           team_color="팀A" if i % 2 else "팀B", formation="4-4-2" if i % 3 else "4-3-3")
            for i in range(1, people + 1)]
    base = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(days=days)
    c = rankcollect.open_rank_db()
    try:
        for d in range(days):
            t = base + timedelta(days=d)
            rankcollect.save_snapshot(c, rows, t + timedelta(minutes=30), ref_time=t.isoformat(timespec="seconds"))
        rankcollect.process_meta(c, datetime.now(), season_ends=[])
    finally:
        c.close()


def _person_rows() -> int:
    c = rankcollect.open_rank_db()
    try:
        return sum(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in rankcollect.PERSON_TABLES)
    finally:
        c.close()


def test_teamcolor_dialog_rank_tab():
    """67 R2 — 팀컬러 정보 창의 [랭커 1만 명]: 재료 없으면 이유 글 + 효과 탭부터 · 쌓인 뒤엔 열어 둔 창이 rank.db 무효화에
    다시 읽힌다(3회차 M11) · 점유율·카드 · 지난주 대비(같은 값이면 –) · 대표 구단주 = 그 팀컬러 점수 높은 순 셋 · 안 잘림."""
    with _MetaEnv(), _TcFake():
        if _win._teamcolor_dialog is not None:
            _win._teamcolor_dialog._label = ""
        dlg = _tc_dialog_open("팀A")
        try:
            assert dlg.detail is None and "모으는 중" in dlg.lb_rank_note.text(), dlg.lb_rank_note.text()
            assert dlg.rank_body.isHidden() and dlg.tabs.currentIndex() == 1, "1만 명 숫자가 없는데 그 탭부터 열었다"
            _meta_db()
            _win._invalidate_rank_views()
            d = dlg.detail
            assert d is not None and d.now is not None and d.now.users == 150, d
            assert not dlg.rank_body.isHidden() and dlg.card_share.value.text() == "50.0%"
            assert dlg.rank_cards["elo"].value.text() == f"{d.now.elo:,.0f}"
            assert d.then is not None and "–" in dlg.rank_cards["elo"].delta.text(), dlg.rank_cards["elo"].delta.text()
            card = app_main.StatCard("t")   # 구단가치·점유율은 오르내림에 좋고 나쁨이 없다 — ▲ 는 붙되 색은 흐림
            card.set_delta(12.0, 10.0, "억", neutral=True)
            assert card.delta.property("delta_color") == T.TEXT_DIM and "▲ 2.0억" in card.delta.text()
            card.set_delta(12.0, 10.0, "억")
            assert card.delta.property("delta_color") == T.GOOD
            assert [r.name for r in dlg.owners_list.rows()] == ["m299", "m297", "m295"], [r.name for r in dlg.owners_list.rows()]
            assert dlg.bins_list.isHidden(), "구단가치가 없는 스냅숏인데 구간 줄을 보였다"
            _tc_dialog_open("팀B")
            assert dlg.tabs.currentIndex() == 0 and [r.name for r in dlg.owners_list.rows()][0] == "m300"
            w = dlg.owners_list.viewport().width()
            bad = [r.name for r in dlg.owners_list.rows() if dlg.owners_list.itemDelegate().overflow(r, w, dlg.owners_list.font())]
            assert not bad and w > 200, (w, bad)
            _tc_dialog_open("없는팀")
            assert "쓰는 사람이 없습니다" in dlg.lb_rank_note.text() and dlg.rank_body.isHidden()
        finally:
            dlg.close()


def test_rank_trend_has_three_tabs_and_meta_tabs_draw():
    with _MetaEnv():
        _win._go_page("랭킹 추이", "메타 변화")
        assert _win._current_view() == ("랭킹 추이", "메타 변화")
        _win._invalidate("metatrend")
        assert "모으는 중" in _win.lb_meta_note.text() and _win.meta_lists["team_color"].rowCount() == 0
        assert not config.RANK_DB_PATH.exists(), "그리다 rank.db 를 만들었다"
        _meta_db()
        _win._invalidate("metatrend")
        t = _win.meta_trend
        assert not t.collecting and round(t.days) == 7, t
        lst = _win.meta_lists["team_color"]
        assert lst.rowCount() == 2 and "7일 전 대비" in _win.meta_titles["team_color"].text()
        # 67 R7 — 사용 증가 순 · 변화 칸 색 = 부호(▲ 쪽 좋음 색) · 지금·그때 값은 집계 그대로
        by_key = {r.key: r for r in t.rows["team_color"]}
        assert [r.key for r in lst.rows()] == [r.key for r in _win.meta_rise_order(t.rows["team_color"])]
        from types import SimpleNamespace as NS       # 픽스처 둘은 변화가 같아(0) 순서를 못 잰다 — 지어낸 줄로
        fake = [NS(key="a", diff_pp=-1.0, now_n=50), NS(key="b", diff_pp=2.5, now_n=10), NS(key="c", diff_pp=2.5, now_n=30)]
        assert [r.key for r in _win.meta_rise_order(fake)] == ["c", "b", "a"]
        for r in lst.rows():
            src = by_key[r.key]
            assert [c.text for c in r.cells] == [f"{src.now_pct:.1f}%", f"{src.then_pct:.1f}%", f"{src.diff_pp:+.1f}%p"]
            assert lst.metric_delegate.colors(r)["cell2"] == app_main.delta_color(src.diff_pp)
        assert _win.lb_meta_note.isHidden(), _win.lb_meta_note.text()   # 78 ① 리뷰 — 비교가 되면 설명 없음
        _win._go_page("랭킹 추이", "점수 분포")
        _win._invalidate("elodist")                    # 앱에선 회차 끝(_on_rank_collect_outcome)이 낡음 표시를 한다
        h = _win.elo_dist
        assert sum(h.bins.values()) == 300 and h.cmp and 200 in h.cuts, h
        ch = _win.edist_chart
        assert ch._main and ch._cmp and ch._faint_first and any(m[1] == "200위" for m in ch._marks), ch._marks
        assert _win.lb_edist_note.isHidden(), _win.lb_edist_note.text()   # 78 ① 데이터 시각·잘린 칸 설명은 지웠다
        _win.cb_edist_cmp.setCurrentIndex(2)          # 비교 없음
        assert _win.edist_chart._cmp is None
        _win.cb_edist_cmp.setCurrentIndex(0)
        # 검색한 계정의 ELO — 막대 데이터 시각 ±1일 안의 기록만 선으로
        keep = (_win._ouid, _win._nick, dict(_win._elo))
        try:
            _win._ouid, _win._nick = "meta-ouid", "나"
            when = h.now_at
            _win._elo_req["meta-ouid"] = 7                 # EloLoader 가 끝난 길 그대로 — 점수 분포를 다시 그려야 선이 생긴다
            _win._on_elo_ready("meta-ouid", app_main.EloSeries(req=7, rows=[{"taken_at": when, "elo": 3500.0}], cuts={}))
            assert _win.elo_dist_marker == (3500.0, when) and any(m[1].startswith("나 ") for m in _win.edist_chart._marks)
            old = (datetime.fromisoformat(when) - timedelta(days=5)).isoformat()
            _win._elo["meta-ouid"] = app_main.EloSeries(req=1, rows=[{"taken_at": old, "elo": 3500.0}], cuts={})
            _win._invalidate("elodist")
            assert _win.elo_dist_marker is None, "닷새 전 기록을 지금 분포 위에 그렸다"
        finally:
            _win._ouid, _win._nick = keep[0], keep[1]
            _win._elo.clear()
            _win._elo.update(keep[2])
        _win._go_page("대시보드")


# ── 2.4.1 랭커 메타 ② 스냅숏 읽기 — 메타 분석 탭 다섯 · 주간 요약 · 순위 변동 · 카드별 ─────────────────────

META_TABS = (("메타 분석", "하루 승률", "daywr", "lb_daywr_note"), ("메타 분석", "팀컬러", "tcboard", "lb_tcboard_note"),
             ("메타 분석", "구단가치", "valuebins", "lb_vbins_note"), ("메타 분석", "구간 평균", "tiermeans", "lb_tmeans_note"),
             ("메타 분석", "승률→점수", "wrelo", "lb_wrelo_note"), ("랭킹 추이", "주간 요약", "weekly", "lb_weekly_note"))


def _meta_db_moving(days=4, people=300, me=None):
    """하루 간격 · 승패가 매일 느는 스냅숏 — 팀컬러 6종 × 50명 · 포메이션 셋 · 구단가치 제각각. 프로필 번호 900+i · 닉네임 g{i}.
    me = (닉네임, 프로필 번호) 를 주면 15위 자리에 그 사람."""
    import rankmeta
    rankmeta.clear_cache()
    base = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(days=days)
    c = rankcollect.open_rank_db()
    try:
        for d in range(days):
            rows = [ranker.RankRow(rank=i, profile_sn=900 + i, nickname=f"g{i}", elo=3000.0 + i * 5 + d * (i % 7),
                                   win=d * (10 + i % 5), lose=d * 10, team_value=(i + 1) * 10 ** 9,
                                   grade=1 if i % 3 else 3, team_color=f"팀{i % 6}",
                                   formation=("4-2-3-1", "4-1-2-3", "5-2-1-2")[i % 3])
                    for i in range(1, people + 1)]
            if me:
                rows[14] = replace(rows[14], nickname=me[0], profile_sn=me[1])
            t = base + timedelta(days=d)
            rankcollect.save_snapshot(c, rows, t + timedelta(minutes=30), ref_time=t.isoformat(timespec="seconds"))
    finally:
        c.close()


def test_rank_meta_tabs_draw_and_fit():
    import rankmeta
    with _MetaEnv() as env:
        env.write(collect=0)
        for page, tab, key, note in META_TABS:
            _win._go_page(page, tab)
            assert _win._current_view() == (page, tab), (page, tab)
            _win._invalidate(key)
            assert "[정보]" in getattr(_win, note).text(), (tab, getattr(_win, note).text())
        assert not config.RANK_DB_PATH.exists(), "그리다 rank.db 를 만들었다"
        env.write(collect=1)
        _meta_db_moving()
        for page, tab, key, _note in META_TABS:
            _win._go_page(page, tab)
            _win._invalidate(key)
        assert _win.day_wr.ready and _win.daywr_tables["team_color"].rowCount() == 6, _win.day_wr
        assert _win.daywr_tables["formation"].rowCount() == 3
        # 67 R1 팀컬러 목록 — 가성비·하루 승률을 묶기만(⑧ R1 × [하루 승률] — 같은 팀컬러 같은 숫자)
        b = _win.tc_board
        assert b.scored and b.day_ready and _win.tc_board_list.rowCount() == 6, b
        # 78 ① — 다 채워졌으면 안내 줄이 없다(손익분기는 결과값이라 승률→점수만 남을 수 있다)
        shown = {note: getattr(_win, note).text() for _p, _t, _k, note in META_TABS
                 if note != "lb_wrelo_note" and not getattr(_win, note).isHidden()}
        assert not shown, shown
        assert not _explainer_hits(), _explainer_hits()   # 데이터가 다 찬 화면에서도 지운 문구가 없다
        assert abs(sum(r.share for r in b.rows) - 100) < 1e-6 and b.total == 300
        day = {r.key: r for r in _win.day_wr.tables["team_color"]}
        vs = {r.key: r for r in _win._rank_read(lambda c: rankmeta.value_score(c, 10000)).rows}
        for r in b.rows:
            assert (r.day_rate, r.day_thin) == (day[r.name].rate, day[r.name].thin), r
            assert (r.score, r.elo, r.rate, r.value, r.champ) == (vs[r.name].score, vs[r.name].elo, vs[r.name].rate,
                                                                vs[r.name].value, vs[r.name].champ), r
        row0 = _win.tc_board_list.row(0)
        assert [c.label for c in row0.cells] == ["가성비", "평균 점수", "시즌 승÷(승+패)", "하루 승÷(승+패)", "구단가치 중앙값",
                                                 "챔스 이상", "점유율"], row0.cells
        assert row0.fill == 1.0 and row0.rank == 1
        # 정렬 — 픽스처 여섯은 값이 다 같아(점유율·하루 승률) 여기선 못 잰다 → 지어낸 줄로(손 변이 10번이 초록이었다)
        from types import SimpleNamespace as NS
        mk = lambda k, share, elo, day, thin, score: NS(key=k, share=share, users=int(share), elo=elo, day_rate=day,
                                                        day_thin=thin, score=score)
        fake = [mk("a", 30, 3000, 40.0, False, None), mk("b", 20, 3500, 60.0, False, 700), mk("c", 10, 3200, 90.0, True, 500),
                mk("d", 5, None, 50.0, False, 900)]
        order = lambda how: [r.key for r in app_main.MainWindow.tc_board_sort(fake, how)]
        assert order("share") == ["a", "b", "c", "d"] and order("elo") == ["b", "c", "a", "d"]
        assert order("day") == ["b", "d", "a", "c"], order("day")      # 하루 인원 미달(c)은 아래로
        assert order("score") == ["d", "b", "c", "a"], order("score")  # 가성비 없는 줄은 아래로
        _win.cb_tcboard_sort.setCurrentIndex(_win.cb_tcboard_sort.findData("day"))
        assert [r.key for r in _win.tc_board_list.rows()] == [r.key for r in app_main.MainWindow.tc_board_sort(b.rows, "day")]
        _win.cb_tcboard_sort.setCurrentIndex(0)
        _win.ed_tcboard.setText("팀1")
        assert [r.key for r in _win.tc_board_list.rows()] == ["팀1"]
        _win.ed_tcboard.setText("")
        assert _win.tc_board_list.rowCount() == 6
        # 67 R7 구단가치 — 구간 줄 8 · 고른 구간의 팀컬러(옛 구간 × 팀컬러 표의 한 열)
        vb = _win.value_bin_data
        assert _win.vbins_list.rowCount() == 8
        for i in (0, 3):
            _win.vbins_list.setCurrentIndex(_win.vbins_list.model().index(i, 0))
            want = sorted(((c, cells[i]) for c, _n, cells in vb.cross if cells[i] is not None), key=lambda t: (-t[1].users, t[0]))
            got = _win.vcross_list.rows()
            assert [r.key for r in got] == [c for c, _x in want], (i, [r.key for r in got])
            assert [r.cells[0].text for r in got] == [f"{x.share:.0f}%" for _c, x in want]
            assert _win._bin_label(vb.bins[i]) in _win.lb_vcross_title.text()
        _win.vbins_list.setCurrentIndex(_win.vbins_list.model().index(0, 0))
        assert _win.tmeans_tables["formation"].rowCount() == 3
        _win.cb_tmeans_form.setCurrentIndex(1)                  # 수비 줄 수
        assert {r.key for r in _win.tier_mean_data.tables["formation"]} == {"4백", "5백"}
        _win.cb_tmeans_form.setCurrentIndex(0)
        assert _win.wr_elo.ready and _win.tbl_wrelo.rowCount() == len(config.N10_BINS) + 1
        assert _win.weekly_data.day_wins and _win.weekly_lists["wins"].rowCount() == config.WEEKLY_TOP_N
        assert _win.weekly_lists["extremes"].rowCount() == 6
        w0 = _win.weekly_data.day_wins[0]                     # 67 R4 — 줄 목록 칸 = 승·무·패 · 카드 = 그 1위
        assert [c.text for c in _win.weekly_lists["wins"].row(0).cells] == [f"{w0[2]:,}", f"{w0[3]:,}", f"{w0[4]:,}"]
        assert _win.weekly_cards["day_win"].value.text() == w0[0]
        # B1 메타 변화도 묶기 콤보를 따른다(N6)
        _win._go_page("랭킹 추이", "메타 변화")
        _win._invalidate("metatrend")
        raw = {r.key for r in _win.meta_trend.rows["formation"]}
        _win.cb_meta_form.setCurrentIndex(1)
        grouped = {r.key for r in _win.meta_trend.rows["formation"]}
        assert raw == {"4-2-3-1", "4-1-2-3", "5-2-1-2"} and grouped == {"4백", "5백"}, (raw, grouped)
        _win.cb_meta_form.setCurrentIndex(0)
        # 1280×720 · 작은 화면에서 안 잘림 — 가장 넓은 B4 구간 × 팀컬러 표 포함
        import widgets
        new = [t for t in _win.findChildren(widgets.FitTableWidget)
               if any(_win._page_tabs[p].page(i).isAncestorOf(t) for p in ("메타 분석", "랭킹 추이")
                      for i in range(len(_win._page_tabs[p].names())))]
        filled = [t for t in new if t.rowCount()]
        assert len(filled) >= 6, (len(filled), len(new))   # 6 = 67 R4 주간 요약 넷이 줄 목록으로 빠짐(전엔 7)
        _check_elisions(new)
        _check_metric_lists([_win.tc_board_list, _win.vbins_list, _win.vcross_list,
                             _win.meta_lists["team_color"], _win.meta_lists["formation"]])
        rankmeta.clear_cache()
    _win._go_page("랭킹 추이", "추이")
    _win._go_page("메타 분석", "하루 승률")
    _win._go_page("대시보드")


def test_rank_views_invalidate_on_four_paths():
    """rank.db 가 바뀌는 길 넷이 같은 키 묶음(RANK_VIEW_KEYS)을 무효화 — 정리는 바뀐 게 있을 때만."""
    from types import SimpleNamespace
    W = app_main.MainWindow
    assert set(W.RANK_VIEW_KEYS) == {"rankerpick", "recommend", "ranktrend", "metatrend", "elodist", "daywr",
                                     "tcboard", "valuebins", "tiermeans", "wrelo", "weekly", "rankmove", "oppcards",
                                     "traits", "opprank", "predcheck", "rewards_rank"}
    seen = []
    keep = (_win._ouid, _win._rank_sched, _win._teamcolor_loader, list(_win._teamcolor_pending))
    _win._invalidate_rank_views = lambda: seen.append(1)
    try:
        _win._ouid = None                                          # 회차 끝의 ELO 다시 읽기는 빼고
        _win._on_rank_collect_outcome(SimpleNamespace(kind="ok", disabled_by_block=False))     # ① 회차 끝
        assert len(seen) == 1
        _win._on_rank_collect_outcome(SimpleNamespace(kind="failed", disabled_by_block=False))
        assert len(seen) == 1
        _win._teamcolor_pending = []                               # ② 팀컬러 목록을 스냅숏으로
        _win._teamcolor_loader = SimpleNamespace(saved_snapshot=SimpleNamespace(kind="ok"), failed_pages=0, fetched_at=None)
        _win._on_teamcolor_finished()
        assert len(seen) == 2
        _win._teamcolor_loader = SimpleNamespace(saved_snapshot=None, failed_pages=0, fetched_at=None)
        _win._on_teamcolor_finished()
        assert len(seen) == 2
        _win._on_rank_maintained({"pruned": 0, "anon": 0, "person": 0, "purged": False, "rolled": False})   # ③ 정리
        _win._on_rank_maintained({"missing": True})
        _win._on_rank_maintained({"locked": True})
        assert len(seen) == 2, "바뀐 것 없는 정리에 다시 그렸다(매시간 돈다)"
        _win._on_rank_maintained({"pruned": 3})
        assert len(seen) == 3
        sched = app_main.RankCollectScheduler()                    # 예약기 → 창 신호
        sched.check = lambda maint=True: None
        _win.attach_rank_sched(sched)
        sched._on_maint_done({"anon": 1})
        assert len(seen) == 4
        _win._rank_sched = None                                    # 예약 없는 창 — 작업자를 직접
        with _MetaEnv():
            c = rankcollect.open_rank_db()
            try:
                rankcollect.save_snapshot(c, [ranker.RankRow(rank=1, profile_sn=1, nickname="x", elo=3000.0)],
                                          datetime.now() - timedelta(days=30))
            finally:
                c.close()
            _win._maintain_rank()                                  # 14일 지난 원본을 지운다 → 바뀜
            _wait_maint()
            assert len(seen) == 5, seen
            _win.sync_ranker_pick_data()                           # ④ 끄기·지우기의 한 자리
            _wait_maint()
            assert len(seen) == 6, seen
    finally:
        del _win._invalidate_rank_views
        _win._ouid, _win._rank_sched, _win._teamcolor_loader = keep[0], keep[1], keep[2]
        _win._teamcolor_pending = keep[3]


def test_top3_cards_and_reward_board_tab():
    """57 K7 카드 셋(마지막 스냅숏 1~3위) · K11 [랭킹 추이 › 보상] — 상태 문구 · 구간 표 · 관문 밖이면 비움."""
    import json
    with _MetaEnv() as env:
        _win._go_page("랭킹 추이", "추이")
        _win._invalidate("ranktrend")
        assert not _win.top3_box.isVisibleTo(_win) and _win.top3_data == [], "rank.db 가 없는데 카드가 보인다"
        _meta_db()
        _win._invalidate("ranktrend")
        cards = _win.top3_data
        assert [c.rank for c in cards] == [1, 2, 3] and _win.top3_box.isVisibleTo(_win), cards
        s0 = _win.top3_cards[0]
        assert s0.card.title.text() == "1위 m1", s0.card.title.text()
        assert "하루 0.0판" in s0.body.text(), s0.body.text()   # 지어낸 스냅숏은 승무패가 안 는다
        # 67 R9 — 승무패 세 칸 막대 · 표본 흐림 규칙(property) · 미니 축구장(포메이션 4-4-2)
        assert s0.wdl.counts() == (cards[0].win, cards[0].draw, cards[0].lose)
        n0 = cards[0].win + cards[0].draw + cards[0].lose
        assert s0.wdl.property("games") == n0 and s0.wdl.property("weak") == (n0 < app_main.core.MIN_COND)
        assert cards[0].formation == "4-4-2" and not s0.pitch.isHidden() and "4-4-2" in s0.card.toolTip()

        _win._go_page("랭킹 추이", "보상")
        assert _win._current_view() == ("랭킹 추이", "보상")
        _win._invalidate("rewards_rank")
        b = _win.reward_board
        assert b is not None and b.state != "ok", b
        assert app_main.MainWindow.REWARD_BOARD_STATES[b.state] in _win.lb_rboard_summary.text()
        at = "2026-09-28T09:00:00"
        c = rankcollect.open_rank_db()
        try:
            rankcollect._set_state(c, person_season=json.dumps({"seq": 1, "start": "2026-09-28", "at": at}),
                                   **{rankcollect.FC_STARTED + at: at, rankcollect.FCG_STARTED + at: at})
            with c:
                c.execute("DELETE FROM fc_season")
                c.execute("DELETE FROM fc_games")
                n = config.K11_MIN_PER_BAND
                c.executemany("INSERT INTO fc_season VALUES (?, ?, 1500, 2000, 0, 1, ?, 0)", [(at, 800 + i, at) for i in range(1, n + 1)])
                c.executemany("INSERT INTO fc_games VALUES (?, ?, 100, 200, 0, 1, ?, 0)", [(at, 800 + i, at) for i in range(1, n + 1)])
        finally:
            c.close()
        _win._invalidate("rewards_rank")
        b = _win.reward_board
        assert b.state == "ok" and b.users == config.K11_MIN_PER_BAND, b
        t = _win.tbl_rboard_rank
        assert t.item(0, 0).text() == "1~200위" and t.item(0, 2).text() == "1,375 FC", [t.item(0, k).text() for k in range(4)]
        assert "1,375 FC" in _win.lb_rboard_summary.text(), _win.lb_rboard_summary.text()
        # K7 하루 FC 는 사람별 관문(안내 6) 뒤 — 위에서 시즌 시작·FC 시작을 앞당기고 1위(801)의 fc_season 을 넣었다
        import rankmeta
        rankmeta.clear_cache()
        _win._invalidate("ranktrend")
        assert _win.top3_data[0].fc_lo is not None, "관문 안인데 하루 FC 가 없다 — 아래 검사가 빈다"
        env.write(notice=config.META_NOTICE_VERSION - 1)
        rankmeta.clear_cache()
        _win._invalidate("ranktrend")
        assert _win.top3_data[0].fc_lo is None, "안내 6 전인데 카드가 사람별 표(fc_season)를 읽었다"
        env.write()
        keep_details = _win._details
        try:
            _win._details = []                                  # 시즌 필터로 다른 시즌을 고른 상태 — 이 계정 값은 누적 전체에서
            mine = _win._my_reward("2025-12-01")
            got = _win._show_my_reward(mine)
            assert "100판당" in got and "챔스 이상 3판" in got, got   # 픽스처 4경기 중 승·무·패 3판 · 전부 챔피언스(900)
            card = _win.rboard_cards["mine"]                     # 80 — 중앙값과 나란히 보는 이 계정 값
            assert card.value.text() == f"{mine.per_100:,.0f} FC", card.value.text()
            assert _win._show_my_reward(None) == "" and card.value.text() == "—", "계정을 모르면 옛 값이 남으면 안 된다"
        finally:
            _win._details = keep_details
        # 지금 등수 = 이번 시즌 마지막 점 하나 — 최신 줄 순위가 비었으면(1만 위 밖) 며칠 전 순위를 끌어오지 않는다(② 리뷰 [중])
        from datetime import date as _d
        keep_elo = (_win._rank_seasons, dict(_win._elo))
        try:
            _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
            now = datetime.now()
            _win._elo[_OUID] = _elo_data(_elo_rows([((now - timedelta(days=3)).isoformat(timespec="seconds"), 4300.0, 150, "search"),
                                                    ((now - timedelta(days=1)).isoformat(timespec="seconds"), 3000.0, None, "search")]))
            got = _win._rank_reward_text()
            assert "150위" not in got and "밖" in got, got
            _win._elo[_OUID] = _elo_data(_elo_rows([((now - timedelta(days=1)).isoformat(timespec="seconds"), 4300.0, 150, "search")]))
            assert "지금 150위" in _win._rank_reward_text(), _win._rank_reward_text()
        finally:
            _win._rank_seasons, _win._elo = keep_elo[0], keep_elo[1]
        env.write(notice=config.FC_GAMES_NOTICE_VERSION - 1)     # 판수 안내 전 동의자 — 남들 값을 안 읽는다
        _win._invalidate("rewards_rank")
        assert _win.reward_board is None and t.rowCount() == 0, "안내 13 전인데 보상판을 읽었다"


def test_rank_cards_follow_one_source_67():
    """67 ③ — 주간 요약 카드(R4) · 달성 판수(R5) · 보상(R6) · 시즌 말 컷(R8). 카드마다 같은 재료의 같은 값(⑧):
    20위 카드 = 200위 카드와 같은 두 스냅숏(M6) · 메타 카드 = [메타 변화] 첫 줄(M5) · 최다 보상 = R6 구간 최고 ·
    달성 판수 = 대시보드 H5 · R8 카드 둘 = 같은 마지막 원본(M7) · 예상 컷·계산 줄 = 예측 값 그대로 · 안 잘림."""
    import json
    import rankmeta
    with _MetaEnv():
        _meta_db_moving(days=8)
        at = "2026-09-28T09:00:00"
        c = rankcollect.open_rank_db()
        try:
            rankcollect._set_state(c, person_season=json.dumps({"seq": 1, "start": "2026-09-28", "at": at}),
                                   **{rankcollect.FC_STARTED + at: at, rankcollect.FCG_STARTED + at: at})
            with c:
                sns = [900 + i for i in range(1, 121)]                 # 1~120위 — 팀컬러 여섯 × 20명 · 구단가치 낮은 칸 셋
                # 팀마다 FC 를 제곱으로 벌린다 — 대칭이면 팀 중앙값의 평균 = 전체 중앙값이라 기준선(base) 변이가 초록이었다
                c.executemany("INSERT INTO fc_season VALUES (?, ?, ?, ?, 0, 1, ?, 0)",
                              [(at, sn, 1000 + 10 * (sn % 6) ** 2, 2000 + 20 * (sn % 6) ** 2, at) for sn in sns])
                c.executemany("INSERT INTO fc_games VALUES (?, ?, 100, 200, 0, 1, ?, 0)", [(at, sn, at) for sn in sns])
                c.executemany("INSERT INTO champ_first VALUES (?, ?, ?, ?, 0)",
                              [(at, g, (g // 100) * 10 ** 9, at) for g in (150, 320, 480, 650, 900, 1300, 5000)])
            snaps = rankcollect._season_snaps(c)
            # 7일 전 스냅숏의 20위만 50점 낮춘다 — 20위 카드가 정말 그 시점을 읽는지(같은 값이면 – 라 못 잰다)
            with c:
                c.execute("UPDATE snapshot_rows SET elo = elo - 50 WHERE snapshot_id = ? AND rank = 20", (snaps[0]["id"],))
        finally:
            c.close()
        rankmeta.clear_cache()
        _win._go_page("랭킹 추이", "주간 요약")
        _win._invalidate("weekly")
        res = _win.weekly_data
        assert res.cmp_id == snaps[0]["id"] and res.base_id == snaps[-1]["id"], (res.cmp_id, res.base_id)
        rc = rankcollect.open_rank_db_ro()
        try:
            now20, then20 = rankmeta.rank_cut(rc, 20, res.base_id), rankmeta.rank_cut(rc, 20, res.cmp_id)
            board = rankmeta.reward_board(rc)
            head = rankmeta.club_head(rc, None)
            last = rankmeta.last_raw_id(rc)
            r8 = {r: rankmeta.rank_cut(rc, r, last) for r in (20, 200, 1000)}
        finally:
            rc.close()
        assert now20 is not None and then20 is not None and now20 - then20 > 50, (now20, then20)   # 낮춘 50 + 그 사이 오름
        card = _win.weekly_cards["cut_top"]
        assert card.value.text() == f"{now20:,.0f}점" and f"{then20:,.0f}점" in card.sub.text(), (card.value.text(), card.sub.text())
        assert "▲" in card.delta.text() and card.delta.property("delta_color") == T.TEXT_DIM, "컷 변화에 좋고 나쁨 색을 칠했다"
        c200 = next(x for x in res.cuts if x[0] == 200)
        assert _win.weekly_cards["cut_200"].value.text() == f"{c200[2]:,.0f}점"
        assert _win.weekly_cards["riser"].value.text() == res.risers[0][0]
        best = max((b for b in board.by_value if b.bin is not None and b.median is not None), key=lambda b: b.median)
        assert _win.weekly_cards["reward"].value.text() == _win._bin_label(best.bin), "최다 보상 카드가 R6 구간 최고와 다르다"
        weekly_top = dict(_win.weekly_meta_top)
        _check_metric_lists([_win.weekly_lists[k] for k in ("risers", "wins", "extremes")], narrow=80)
        # M5 — 메타 카드 = [메타 변화] 기본값(구간 첫 칸 · 그대로)의 첫 줄
        _win._go_page("랭킹 추이", "메타 변화")
        _win.cb_meta_tier.setCurrentIndex(0)
        _win.cb_meta_form.setCurrentIndex(0)
        _win._invalidate("metatrend")
        for kind in ("team_color", "formation"):
            assert weekly_top.get(kind) == _win.meta_lists[kind].row(0).key, (kind, weekly_top, _win.meta_lists[kind].row(0).key)
        # R5 — 대시보드 H5 와 같은 행·같은 중앙값 · 꼬리(5000판)는 마지막 칸
        _win._go_page("랭킹 추이", "추이")
        _win._invalidate("ranktrend")
        cg = _win.champ_data
        assert cg.n == head.champ_n == 7 and cg.p50 == head.champ_median == 650, (cg.n, cg.p50, head.champ_median)
        assert cg.hist[-1] == (config.R5_HIST_MAX, 1) and "중앙값 650판" in _win.lb_champ_note.text(), _win.lb_champ_note.text()
        assert _win.champ_value_list.rowCount() == len(cg.by_value) and _win.champ_value_list.base == 650
        assert not _win.champ_value_list.higher_better, "달성 판수는 적을수록 좋다(3회차 M8)"
        # R6 — 카드 = 보상판 전체 · 구단가치 막대 = by_value · 팀컬러 여섯(인원 20) = 전부 펼침
        _win._go_page("랭킹 추이", "보상")
        _win._invalidate("rewards_rank")
        b = _win.reward_board
        assert _win.rboard_cards["users"].value.text() == f"{b.users:,}명" == f"{len(sns):,}명"
        assert _win.rboard_cards["median"].value.text() == f"{b.overall.median:,.0f} FC"
        tc = [r.name for r in _win.rboard_tc_list.rows()]
        want = [x.label for x in sorted((x for x in b.by_team_color if x.median is not None), key=lambda x: (-x.median, x.label))]
        assert tc == want and len(tc) == 6, (tc, want)
        assert _win.rboard_tc_list.base == b.overall.median and _win.rboard_tc_more.isHidden(), "여섯 개뿐인데 [전체 보기]"
        assert any(r.value == best.median for r in _win.rboard_value_list.rows()), "구단가치 막대에 카드 값이 없다"
        # R8 — 카드 둘은 같은 마지막 원본(M7) · 예상 컷·계산 줄은 예측 값 그대로
        _win._go_page("승률 그래프", "점수·예측")
        pr = app_main.core.Prediction(True, p={200: 0.4, 1000: 0.9}, lo=150, hi=420, cuts={200: (4510.0, 4480.0, 4540.0)},
                                      need=60.0, pace=30.0, end_text="11/12(공지)")
        _win._render_park(pr)
        cards = _win.park_cards
        assert cards["cut_200"].value.text() == f"{r8[200]:,.0f}점" and cards["cut_top"].value.text() == f"{r8[20]:,.0f}점"
        assert _win.park_pred.value.text() == "4,510점" and "4,480~4,540" in _win.park_pred.sub.text()
        assert "4,450" in _win.lb_park_margin.text() and "60점 더 필요" in _win.lb_park_margin.text(), _win.lb_park_margin.text()
        row0 = _win.park_seasons.row(0)
        assert r8[1000] is None, "픽스처는 300명 — 1,000위 칸이 '—' 인지 아래에서 본다"
        assert row0.name == "이번 시즌" and [x.text for x in row0.cells] == [f"{r8[200]:,.0f}", "—"], [x.text for x in row0.cells]
        assert not _win.park_method.isHidden() and "4,510" in _win.lb_park_method.text()
        _win._render_park(app_main.core.Prediction(False, "랭킹 기록을 모으는 중입니다"))
        assert _win.park_pred.value.text() == "—" and _win.park_method.isHidden()
        _check_metric_lists([_win.park_seasons], narrow=80)
    _win._render_predict()
    _win._go_page("랭킹 추이", "추이")
    _win._go_page("대시보드")


def test_teamcolor_kind_and_minor_filters_are_shared():
    """57 K8 — 팀컬러 종류·비주류만 거르기는 표 다섯이 같은 값 · 재료(엠블럼·rank.db)가 없으면 그 부품을 숨긴다."""
    keep_minor, keep_cache = config.MINOR_USERS, _win._emblem_cache
    with _MetaEnv():
        try:
            _meta_db(people=301)                     # 팀A 151명 · 팀B 150명
            config.MINOR_USERS = 151                 # 팀B 만 비주류
            import widgets
            _win._go_page("메타 분석", "팀컬러")
            _win._invalidate("tcboard")
            names = lambda: sorted(r.key for r in _win.tc_board_list.rows())
            assert names() == ["팀A", "팀B"], names()
            combos = [f for f in _win._tc_filters if not isinstance(f[1], widgets.ChoiceChips)]
            chips = [f for f in _win._tc_filters if isinstance(f[1], widgets.ChoiceChips)]
            assert len(chips) == 1 and len(combos) >= 3, (len(chips), len(combos))   # 67 R1 칩 하나 + 콤보(하루 승률·구간 평균·팀컬러 메뉴 …)
            lb, cb, chk = chips[0]
            assert not chk.isHidden(), "rank.db 가 있는데 비주류 칩을 숨겼다"
            combos[-1][2].setChecked(True)                      # 다른 표의 체크 — 칩까지 모두 같은 값으로
            assert all(c.isChecked() for _l, _c, c in _win._tc_filters), "거르기 값이 표마다 갈렸다"
            assert names() == ["팀B"], names()
            cb.buttons[0].click()                               # [전체] — 이미 전체여도 비주류를 끈다(U4)
            assert not _win._tc_minor_only and not any(c.isChecked() for _l, _c, c in _win._tc_filters)
            assert names() == ["팀A", "팀B"], names()
            _win._emblem_cache = {"팀A": "crests/1.png", "팀B": "countries/2.png"}
            cb.buttons[cb.findData("crests")].click()           # 칩을 누르면 콤보도(2회차 [중] — [하루 승률] 콤보가 같이 바뀐다)
            assert all(c.currentData() == "crests" for _l, c, _k in _win._tc_filters)
            assert names() == ["팀A"], names()
            chk.click()                                         # 종류 + 비주류를 같이 — 팀A 는 비주류가 아니라 0줄
            assert _win._tc_kind == "crests" and _win._tc_minor_only and names() == [], names()
            chk.click()
            _win._tc_filters[0][1].setCurrentIndex(_win._tc_filters[0][1].findData("crests"))
            assert names() == ["팀A"], names()
            _win._go_page("메타 분석", "구간 평균")
            _win._invalidate("tiermeans")
            t = _win.tmeans_tables["team_color"]
            assert [t.item(r, 0).text() for r in range(t.rowCount())] == ["팀A"]
            assert _win.tmeans_tables["formation"].rowCount() == 2, "팀컬러 거르기가 포메이션 표까지 걸렀다"
            _win._emblem_cache = {}
            _win._invalidate("tiermeans")
            assert t.rowCount() == 2 and cb.isHidden(), "엠블럼을 모르는데 종류 거르기가 걸리거나 보인다"
        finally:
            config.MINOR_USERS = keep_minor
            _win._emblem_cache = keep_cache
            _win._on_tc_filter(None, False)


def test_metric_row_and_avg_bar_parts_67():
    """67 R0 — 지표 줄: 칸 폭은 목록 전체에서 맞춘다(줄마다 숫자가 같은 x) · 흐림은 칸 색까지 · 채움 색은 ROW_FILL.
    평균 막대: 기준 위/아래 색 · higher_better=False 면 뒤집힌다(3회차 M8) · 막대 길이는 최댓값 대비."""
    import widgets
    from PySide6.QtCore import QRectF
    lst = widgets.MetricRowList()
    rows = [widgets.MetricRow("가", "1명", (widgets.MetricCell("1", "짧은 라벨"), widgets.MetricCell("12.3%", "점유", T.GOOD)), rank=1),
            widgets.MetricRow("나나나", "2명", (widgets.MetricCell("1,234,567", "라벨"), widgets.MetricCell("9.9%", "점유")),
                              rank=10, dim=True, fill=0.5)]
    lst.resize(900, 300)
    lst.set_rows(rows)
    dg = lst.metric_delegate
    xs = [[s.rect.left() for s in dg.layout(r, QRectF(0, 0, 900, 60), lst.font()) if s.role.startswith("cell")] for r in rows]
    assert xs[0] == xs[1], ("칸이 줄마다 다른 x — 폭을 줄마다 쟀다", xs)
    assert dg.colors(rows[0])["cell1"] == T.GOOD and dg.colors(rows[1])["cell1"] == T.TEXT_DIM
    assert dg.colors(rows[1])["name"] == T.TEXT_DIM and dg.colors(rows[0])["fill"] == T.ROW_FILL
    assert dg.overflow(rows[1], 120, lst.font()) and not dg.overflow(rows[1], 900, lst.font())
    lst.set_filter(lambda r: r.rank == 1)
    assert lst.rowCount() == 1 and xs[0] == [s.rect.left() for s in dg.layout(rows[0], QRectF(0, 0, 900, 60), lst.font())
                                             if s.role.startswith("cell")], "거르기로 칸 폭이 흔들렸다"
    for better, above, below in ((True, T.GOOD_FILL, T.BAD_FILL), (False, T.BAD_FILL, T.GOOD_FILL)):
        bars = widgets.AvgBarList(higher_better=better)
        bars.resize(600, 200)
        items = [widgets.AvgBar("위", 30.0, "30"), widgets.AvgBar("아래", 10.0, "10", "5명"), widgets.AvgBar("없음", None, "—")]
        bars.set_rows(items)
        assert bars.base == 20.0 and bars.max_dev == 10.0
        bd = bars.bar_delegate
        assert (bd.colors(items[0])["bar"], bd.colors(items[1])["bar"]) == (above, below), better
        segs = {s.role: s for s in bd.layout(items[1], QRectF(0, 0, 600, 30), bars.font())}
        top = {s.role: s for s in bd.layout(items[0], QRectF(0, 0, 600, 30), bars.font())}
        assert abs(segs["bar"].rect.width() - top["bar"].rect.width()) < 0.01, "기준에서 같은 거리인 두 값의 막대 길이가 다르다"
        assert "bar" not in {s.role for s in bd.layout(items[2], QRectF(0, 0, 600, 30), bars.font())}
        assert bars.height() == 3 * bd.row_height(bars.font()) + 2 * bars.frameWidth() + 2, "fixed 높이가 줄 수를 안 따른다"
    bars.set_rows(items, base=25.0)
    assert bars.base == 25.0


def test_predict_check_tab_renders_and_fits():
    """[랭킹 추이 › 예측 되짚기](50단계 B8) — 기록 없음 안내 · 확인 전 안내 · 찾은 시즌/못 찾은 시즌 판정 · 안 잘림."""
    import json
    ouid, start = "pc-ouid", "2026-07-09"
    _win._go_page("랭킹 추이", "예측 되짚기")
    keep_web = config.WEB_DATA
    config.WEB_DATA = True          # 앞선 테스트가 꺼 둔 채일 수 있다 — 안내 문구가 이 값에 달렸다
    c = store.open_db(config.DB_PATH)
    try:
        c.execute("DELETE FROM predictions")
        c.execute("DELETE FROM trade_state")
        c.commit()
        _win._invalidate("predcheck")
        assert _win.tbl_pcheck.rowCount() == 0 and "아직 남은 예측이 없습니다" in _win.lb_pcheck_note.text()
        for day, lo, hi in (("2026-08-01", 100, 300), ("2026-08-02", 10, 90)):
            store.save_prediction(c, ouid, datetime.fromisoformat(day), profile_sn=7, season_start=start,
                                  end_source="estimate", p200=0.3, p1000=0.9, lo=lo, hi=hi)
        store.set_trade_state(c, my_ouid_unconfirmed=ouid)       # 키가 바뀌어 내 계정이 '확인 전' — 지난 기록은 보인다
        _win._invalidate("predcheck")
        assert _win.tbl_pcheck.rowCount() == 2 and "내 계정 확인 전" in _win.lb_pcheck_note.text()
        store.set_trade_state(c, my_ouid=ouid, key_fp=store.key_fingerprint(config.API_KEY),
                              **{f"{store.PREDICT_FINAL_PREFIX}{ouid}:{start}": json.dumps({"state": "found", "rank": 120})})
        _win._invalidate("predcheck")
        t = _win.tbl_pcheck
        assert t.rowCount() == 2
        by_day = {t.item(r, 1).text(): [t.item(r, k).text() for k in range(t.columnCount())] for r in range(2)}
        assert by_day["2026-08-01"][5:] == ["120위", "실제 200위 안 · 범위 안"], by_day
        assert by_day["2026-08-02"][5:] == ["120위", "실제 200위 안 · 범위 밖"], by_day
        # 67 R8 판정 칸 태그 꼴 — 범위 안 = 좋음 · 범위 밖 = 주의(글자 색)
        from PySide6.QtGui import QColor
        tone = {t.item(r, 1).text(): t.item(r, 6).foreground().color().name() for r in range(2)}
        assert tone == {"2026-08-01": QColor(T.GOOD).name(), "2026-08-02": QColor(T.WARN).name()}, tone
        key = f"{store.PREDICT_FINAL_PREFIX}{ouid}:{start}"
        store.set_trade_state(c, **{key: json.dumps({"state": "not_found", "first": _date.today().isoformat()})})
        _win._invalidate("predcheck")
        assert t.item(0, 5).text() == "확인 중", "다시 묻는 동안 굳은 '확인 불가' 로 보이면 안 된다"
        store.set_trade_state(c, **{key: json.dumps({"state": "not_found", "first": "2020-01-01"})})
        _win._invalidate("predcheck")
        assert t.item(0, 5).text() == "확인 불가" and "닉네임" in t.item(0, 6).text()
        store.set_trade_state(c, **{key: None})
        _win._invalidate("predcheck")
        assert t.item(0, 5).text() == "시즌 진행 중", "시즌표에 없는(진행 중) 시즌"
        store.save_seasons(c, [sn.Season(no=9099, name="시즌 임시", start=_date.fromisoformat(start), end=_date(2026, 9, 10))])
        _win._invalidate("predcheck")
        assert t.item(0, 5).text() == "확인 전", "끝난 시즌을 '진행 중' 으로 보이면 안 된다(M2)"
        # M1 — 화면이 이미 그려진 뒤 DB 만 바뀌어도 탭을 다시 열면 새로 읽는다(_invalidate 가 안 가는 길: 내 계정 확정·새 예측 줄)
        store.set_trade_state(c, **{key: json.dumps({"state": "found", "rank": 77})})
        _win._on_view_opened()
        assert t.item(0, 5).text() == "77위", "탭을 다시 열었는데 낡은 표다"
        _check_elisions([t])
        assert app_main.MainWindow.maint_changed({"predict_check": {"asked": 1}}) is True
        assert app_main.MainWindow.maint_changed({"predict_check": {"asked": 0}}) is False
        assert app_main.MainWindow.maint_changed({"predict_check": {"skipped": "gate"}}) is False
        c.execute("DELETE FROM predictions")
        c.commit()
        config.WEB_DATA = False         # 기록이 없고 웹 데이터가 꺼졌으면 그 까닭을 알린다
        _win._invalidate("predcheck")
        assert t.rowCount() == 0 and "꺼져 있어" in _win.lb_pcheck_note.text(), _win.lb_pcheck_note.text()
    finally:
        config.WEB_DATA = keep_web
        c.execute("DELETE FROM predictions")
        c.execute("DELETE FROM trade_state")
        c.execute("DELETE FROM seasons WHERE no = 9099")
        c.commit()
        c.close()


def test_rank_move_follows_late_profile_sn():
    """[순위 변동] — 닉네임으로 못 찾으면 안내, 늦게 온 프로필 번호(rank_ready)로 다시 그린다. 판정·이력 두 표."""
    import rankmeta
    keep = (_win._ouid, _win._nick, _win._rank)
    with _MetaEnv():
        try:
            _meta_db_moving(me=("다른이름", 4242))
            _win._ouid, _win._nick, _win._rank = "rm-ouid", "검색한이름", None
            _win._go_page("승률 그래프", "순위 변동")
            _win._invalidate("rankmove")
            assert "1만 위 안에 없거나" in _win.lb_rmove_note.text() and _win.tbl_rmove.rowCount() == 0
            info = ranker.RankerInfo(nickname="검색한이름", rank=15, elo=3075.0, profile_sn=4242)
            _win._on_rank_ready("rm-ouid", info)
            assert _win.rank_move_data is not None and _win.rank_move_data.found
            assert _win.tbl_rmove.rowCount() == 3 and _win.tbl_rhist.rowCount() == 4
            assert _win.tbl_rhist.icon_columns == {3: "formation", 4: "emblem"}
            first = _win.tbl_rhist.item(0, 3)
            assert first.data(app_main.ICON_KEY_ROLE) and not first.icon().isNull(), "변동 이력 포메이션 칸에 작은 축구장이 없다"
            assert _win.tbl_rmove.item(0, 5).text() == rankmeta.V_GAME, _win.tbl_rmove.item(0, 5).text()
            _win._on_rank_ready("다른-ouid", None)                  # 다른 계정의 늦은 신호는 버린다
            assert _win.tbl_rmove.rowCount() == 3
        finally:
            _win._ouid, _win._nick, _win._rank = keep
            rankmeta.clear_cache()
    _win._go_page("승률 그래프", "등급")   # 보던 탭을 기억한다 — 다음 테스트가 첫 탭을 본다
    _win._go_page("대시보드")
    _win._render_all()


def test_opp_cards_tab_filters():
    """[카드별] — 자리 콤보 · 팀컬러 필터(자기 콤보) · 1만 위 안 상대만(rank.db 없으면 꺼짐)."""
    import rankmeta
    saved_colors = dict(_win._team_colors)
    try:
        _rank_files_gone()
        _win._go_page("포지션별 최다 상대", "카드별")
        assert _win._current_view() == ("포지션별 최다 상대", "카드별")
        _win._invalidate("oppcards")
        all_cards = _win.opp_cards
        assert all_cards and _win.cb_oppcard_pos.count() == len(all_cards) and _win.tbl_oppcard.rowCount() > 0
        assert not _win.chk_oppcard_ranked.isEnabled() and _win.chk_oppcard_ranked.toolTip()
        assert not config.RANK_DB_PATH.exists()
        opps = sorted({m.opponent for m in _MATCHES if m.opponent})
        # 보이는 탭만 그리는 상태로 — LAZY_RENDER=False 면 "teamcolor" 하나가 두 콤보를 다 채워 길이 안 갈린다
        _win.LAZY_RENDER = True
        try:
            _win._go_page("포지션별 최다 상대", "축구장")
            _win._team_colors[opps[1]] = "축구장컬러"
            _win._invalidate_teamcolor()                           # [축구장] 이 그리며 [카드별] 콤보도 같이 채운다
            assert _win.cb_oppcard_color.findText("축구장컬러") > 0, "[축구장] 의 목록 갱신이 [카드별] 콤보를 안 채웠다"
            _win._go_page("포지션별 최다 상대", "카드별")
            _win._team_colors[opps[0]] = "카드컬러"
            _win._invalidate_teamcolor()                           # 팀컬러를 새로 읽은 길 — 보이는 [카드별] 이 바로
            i = _win.cb_oppcard_color.findText("카드컬러")
            assert i > 0, "[카드별] 팀컬러 콤보가 안 채워졌다"
        finally:
            _win.LAZY_RENDER = False
        del _win._team_colors[opps[1]]
        _win.cb_oppcard_color.setCurrentIndex(i)
        met = sum(c.met for cards in _win.opp_cards.values() for c in cards)
        total = sum(c.met for cards in all_cards.values() for c in cards)
        assert 0 < met < total, (met, total)
        assert _win.cb_position_color.currentText() == _win.POSITION_COLOR_NONE, "고른 값은 탭마다 따로"
        _win.cb_oppcard_color.setCurrentIndex(0)
        # 1만 위 안 상대만 — rank.db 에 그 상대 하나
        c = rankcollect.open_rank_db()
        try:
            rankcollect.save_snapshot(c, [ranker.RankRow(rank=1, profile_sn=1, nickname=opps[0], elo=3000.0)],
                                      datetime.now())
        finally:
            c.close()
        rankmeta.clear_cache()
        _win._invalidate("oppcards")
        assert _win.chk_oppcard_ranked.isEnabled()
        _win.chk_oppcard_ranked.setChecked(True)
        met2 = sum(c.met for cards in _win.opp_cards.values() for c in cards)
        assert met2 == met, (met2, met)
        _win.chk_oppcard_ranked.setChecked(False)
    finally:
        _win._team_colors.clear()
        _win._team_colors.update(saved_colors)
        _rank_files_gone()
        rankmeta.clear_cache()
    _win._go_page("포지션별 최다 상대", "축구장")
    _win._go_page("대시보드")
    _win._render_all()


def test_about_rank_status_shows_ref_time_and_meta_counts():
    with _MetaEnv() as env:
        _meta_db(days=2)
        text = app_main.rank_status_text(rankcollect.read_status())
        assert "넥슨 데이터 기준" in text and "빈 순위 0" in text and "쌓는 중: 상위 20 연속" in text and "슈챔" not in text, text
        c = rankcollect.open_rank_db()
        try:
            c.execute("UPDATE snapshot_meta SET rank_gaps = ?", (config.RANK_GAPS_WARN,))
            c.commit()
            rankcollect._set_state(c, same_since="2026-10-08T09:12:00", ckpt_fail=config.RANK_CKPT_WARN)
        finally:
            c.close()
        text = app_main.rank_status_text(rankcollect.read_status())
        assert T.WARN in text and "멈춰 있습니다" in text and "파일 정리가" in text, text
        env.write(notice=config.META_NOTICE_VERSION - 1)
        assert "새 안내 동의 뒤부터" in app_main.rank_status_text(rankcollect.read_status())


def test_collect_off_purges_person_rows_on_worker_and_on_again_restarts():
    with _MetaEnv() as env:
        _meta_db(days=3)
        assert _person_rows() > 0, "사람별 줄이 없다 — 아래 검사가 공허"
        dlg = app_main.AboutDialog(_win)
        try:
            dlg._on_rank_toggled(False)
            _wait_maint()
            assert _person_rows() == 0, "수집을 껐는데 사람별 줄이 남았다"
            assert rankcollect.read_status()["enabled"] == "0", "켜짐 사본을 작업 스레드가 안 썼다"
            dlg._on_rank_toggled(True)
            _wait_maint()
            on_at = config.read_rank_on_at()
            assert on_at and on_at != env.ON_AT, "켤 때 켠 시각을 새로 안 적었다"
            assert rankcollect.read_status()["enabled"] == "1"
        finally:
            dlg.deleteLater()
        c = rankcollect.open_rank_db()
        try:
            rows = [ranker.RankRow(rank=1, profile_sn=801, nickname="m1", elo=3000.0, grade=0)]
            rankcollect.save_snapshot(c, rows, datetime.now())
            rankcollect.process_meta(c, datetime.now(), season_ends=[])
            assert [r[0] for r in c.execute("SELECT censored_start FROM run_open")] == [1], "끄고 켠 사이를 이어 봤다"
            assert rankcollect.get_state(c)["person_epoch"] == on_at
        finally:
            c.close()


def test_every_off_path_purges_through_sync_ranker_pick_data():
    """끄는 길(웹 데이터 끄기 · D6 · 다시 묻는 창 · .env 손 수정)은 전부 sync_ranker_pick_data 를 거친다 — 거기서 rank.db 정리."""
    with _MetaEnv() as env:
        _meta_db(days=2)
        assert _person_rows() > 0
        env.write(collect=0)                              # .env 손 수정처럼 — 토글을 안 거친다
        _win.sync_ranker_pick_data()
        _wait_maint()
        assert _person_rows() == 0, "sync_ranker_pick_data 가 rank.db 정리를 안 띄웠다"


def test_purge_missed_by_lock_is_done_on_next_hourly_check():
    with _MetaEnv() as env:
        _meta_db(days=2)
        holder = rankcollect.open_rank_db()
        lock = rankcollect.CollectLock(holder)
        assert lock.acquire()
        sched = app_main.RankCollectScheduler()
        try:
            env.write(collect=0)
            _win.sync_ranker_pick_data()                  # 끄는 길 — 첫 정리는 잠금을 못 잡는다
            _wait_maint()
            assert _person_rows() > 0, "잠금을 못 잡았는데 지웠다(아래 검사가 공허)"
            lock.release()
            sched.check()                                 # 1시간 확인 — 상태 판정이라 그대로 잡는다
            assert sched.maint is not None and sched.maint.wait(15000)
            assert _person_rows() == 0, "놓친 지우기를 다음 확인이 안 했다"
        finally:
            sched.shutdown()
            holder.close()


def test_rank_paths_never_create_rank_db():
    with _MetaEnv() as env:
        env.write(collect=0)
        _win.sync_ranker_pick_data()
        _wait_maint()
        assert not config.RANK_DB_PATH.exists(), "수집을 켠 적 없는 PC 에 rank.db 가 생겼다(정리)"
        env.write(collect=1)
        sched = app_main.RankCollectScheduler()
        ran = []
        sched.run_now = lambda: ran.append(1)
        try:
            sched.check(maint=False)
            assert sched.planned is not None and not config.RANK_DB_PATH.exists(), "예약 확인이 rank.db 를 만들었다"
        finally:
            sched.shutdown()
        conn = store.open_db(config.DB_PATH)
        try:
            app_main.RankerPickLoader._record_days(conn, [])
        finally:
            conn.close()
        assert not config.RANK_DB_PATH.exists(), "랭커 픽 끝이 rank.db 를 되살렸다"


def test_reask_notice_explains_person_values():
    dlg = app_main.NoticeDialog(_win, reask=True)
    try:
        text = " ".join(b.toPlainText() for b in dlg.findChildren(app_main.QTextBrowser))
        assert "프로필 번호" in text and "그 시즌 동안" in text, "다시 묻는 창이 v6 의 바뀐 점을 안 알린다"
    finally:
        dlg.deleteLater()


# ── 55 구단 머리 줄 · 점수 리포트 ────────────────────────────────────────────

def _club(**kw):
    base = dict(season_start="2026-09-10", snap_at="2026-10-18T09:00:00", cut=4400.0,
                values=sorted([5 * 10 ** 11, 9 * 10 ** 11, 2 * 10 ** 12]), my_value=7 * 10 ** 11,
                fc_lo=[100, 300], fc_hi=[400, 900], fc_hidden=False, me_in_fc=True, me_known=True,
                peak=4550.0, peak_at="2026-10-17T15:00:00", champ_median=500, champ_n=3, champ_hidden=False,
                peaks_dd=[])
    base.update(kw)
    return core.ClubHead(**base)


def _club_args(club, **kw):
    from datetime import date as _d
    W = core.WinDivision
    wins = [W(datetime(2026, 9, 10, 9), 1000, "승", False), W(datetime(2026, 9, 11, 9), 900, "승", False),
            W(datetime(2026, 9, 12, 9), 800, "패", False), W(datetime(2026, 10, 18, 9), 900, "승", False),
            W(datetime(2026, 10, 18, 10), 900, "무", False)]
    rows = _elo_rows([("2026-10-17T15:00:00", 4500.0, 9, "search"), ("2026-10-18T20:00:00", 4380.0, 230, "search")])
    rows[-1]["team_value"] = 8 * 10 ** 11
    args = dict(wins=wins, rows=rows, pts=app_main.elo_daily_points(rows, datetime(2026, 9, 10)), club=club,
                season_start=datetime(2026, 9, 10), today=_d(2026, 10, 19), mixed=False, known=True)
    args.update(kw)
    return args


def test_club_cells_texts():
    keep = config.RANK_COLLECT
    try:
        config.RANK_COLLECT = True
        args = _club_args(_club())
        cells = {c[0]: c[1:] for c in app_main.club_cells(**args)}
        assert list(cells)[0] == "어제" and len(cells) == 8, list(cells)
        # 65 M1 — 랭킹 점수 = 마지막 점수 기록 · 오늘 = 어제와 같은 식(club_day)을 오늘 날짜로
        assert cells["랭킹 점수"][0] == f"{args['pts'][-1]['elo']:,.2f}", cells["랭킹 점수"]
        td = core.club_day(args["wins"], args["today"])
        assert (f"{td.win}승 {td.draw}무 {td.lose}패" in cells["오늘"][1]) if td.games else cells["오늘"][1] == "오늘 경기 없음"
        none_today = {c[0]: c[1:] for c in app_main.club_cells(**{**args, "pts": [], "rows": []})}
        assert none_today["랭킹 점수"][0] == "—" and none_today["오늘"][0] == "—"
        assert cells["어제"][0] == "-120점" and cells["어제"][1] == "1승 1무 0패 · 50.0% · 15 FC", cells["어제"]
        assert cells["지금 200위까지"][0] == "+20.00점", cells["지금 200위까지"]
        assert cells["구단가치 등수"][0] == "3등" and "4명 중" in cells["구단가치 등수"][1] and "검색 때" in cells["구단가치 등수"][1]
        assert cells["시즌 최고점"] == ("4,550.00", "#9 (10.17)", cells["시즌 최고점"][2]), "수집 최고점이 더 높으면 그쪽 · 순위는 같은 시각 기록"
        assert cells["챔스·슈챔 달성"][0] == "챔스 2판 · 슈챔 3판" and "남들 중앙 500판보다 498판 빠르게" in cells["챔스·슈챔 달성"][1]
        assert cells["시즌 FC"][0] == "30 FC" and cells["시즌 FC"][1] == "3등 / 3명", cells["시즌 FC"]
        hidden = {c[0]: c[1:] for c in app_main.club_cells(**_club_args(_club(fc_hidden=True, champ_hidden=True)))}
        assert hidden["시즌 FC"][1].startswith("등수는 다음 시즌부터") and "남들 비교는 다음 시즌부터" in hidden["챔스·슈챔 달성"][1]
        out = {c[0]: c[1:] for c in app_main.club_cells(**_club_args(_club(my_value=None, me_in_fc=False)))}
        assert out["구단가치 등수"][1].endswith("위 밖(등수 없음)") and out["시즌 FC"][1].endswith("위 밖(등수 없음)")
        none = {c[0]: c[1:] for c in app_main.club_cells(**_club_args(None, pts=[], rows=[]))}
        assert none["지금 200위까지"][0] == "—" and none["구단가치 등수"][0] == "—" and none["시즌 최고점"][0] == "—"
        assert none["시즌 FC"][0] == "30 FC", "rank.db 가 없어도 내 FC 는 보인다"
        late = {c[0]: c[1:] for c in app_main.club_cells(**_club_args(_club(), season_start=datetime(2026, 8, 1)))}
        assert "저장 시작 뒤부터" in late["시즌 FC"][1] and "저장 시작 뒤부터" in late["챔스·슈챔 달성"][1]
        # 저장이 늦게 시작되면 내 값은 덜 센 것 — 넥슨 누적인 남들과 견주지 않는다(조각 ② 리뷰 [중])
        assert "등수 없음" in late["시즌 FC"][1] and "/" not in late["시즌 FC"][1], late["시즌 FC"]
        assert "빠르게" not in late["챔스·슈챔 달성"][1] and "남들 비교 없음" in late["챔스·슈챔 달성"][1], late["챔스·슈챔 달성"]
        assert all("시즌 모름" not in v[1] for v in cells.values()), cells
        # M16 — 시즌을 모르면(R2) 최근 70일을 "시즌" 이라 부르지 않는다 · 제목(대시보드 열쇠)은 그대로
        guess = {c[0]: c[1:] for c in app_main.club_cells(**_club_args(_club(), known=False))}
        for k in ("시즌 FC", "시즌 최고점", "챔스·슈챔 달성"):
            assert f"최근 {config.ELO_FALLBACK_DAYS}일(시즌 모름)" in guess[k][1], (k, guess[k])
        unk = {c[0]: c[1:] for c in app_main.club_cells(**_club_args(_club(me_known=False, me_in_fc=False)))}
        assert unk["구단가치 등수"][1] == app_main.CLUB_NO_SN and unk["시즌 FC"][1] == app_main.CLUB_NO_SN, \
            "내 번호를 모르는데 1만 위 밖이라 했다"
    finally:
        config.RANK_COLLECT = keep


def test_dashboard_head_redraws_when_elo_arrives():
    """J4 — 경기 받기 끝과 elo_ready 중 늦게 온 쪽이 머리 줄을 채운다(대시보드를 보는 채로 ELO 가 늦게 와도)."""
    from datetime import date as _d
    keep = (_win._rank_seasons, dict(_win._elo))
    try:
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
        _win._elo.pop(_OUID, None)
        _win._go_page("대시보드")
        _win._render_dashboard()
        big = _win.dashboard.head_vals
        assert big["지금 200위까지"] == "—" and _win.dashboard.head.scope.text() == "이번 시즌", big
        now = datetime.now()
        rows = _elo_rows([((now - timedelta(days=1)).isoformat(timespec="seconds"), 4300.0, 300, "search")])
        data = _elo_data(rows)
        data.club = _club()
        _win._on_elo_ready(_OUID, data)
        _app.processEvents()
        assert _win.dashboard.head_vals["지금 200위까지"] == "+100.00점", _win.dashboard.head_vals
        assert _win.dashboard.card_score.sub.text() == "200위까지 +100.00점", _win.dashboard.card_score.sub.text()
    finally:
        _win._rank_seasons, _win._elo = keep[0], keep[1]
        _win._render_dashboard()


def test_history_tab_nickname_only_for_my_account():
    """57 K4 [스쿼드·이적 › 이력] — 포메이션 이력은 늘 · 닉네임 이력은 내 계정(키 지문까지)일 때만."""
    _win._go_page("스쿼드·이적", "이력")
    assert _win._current_view() == ("스쿼드·이적", "이력")
    c = store.open_db(config.DB_PATH)
    try:
        keep = store.trade_state(c)
        c.execute("DELETE FROM trade_state")
        c.commit()
        _win._invalidate("history")
        kinds = {r.kind for r in _win.history_runs}
        assert kinds == {"formation"} and _win.tbl_history.rowCount() == len(_win.history_runs) > 0, kinds
        assert "내 계정" in _win.lb_history_note.text()
        store.set_trade_state(c, my_ouid="다른-계정", key_fp=store.key_fingerprint(config.API_KEY))
        _win._invalidate_trades()
        assert {r.kind for r in _win.history_runs} == {"formation"}, "내 계정이 다른 계정인데 닉네임 이력을 냈다"
        store.set_trade_state(c, my_ouid=_OUID, key_fp="다른 키")
        _win._invalidate_trades()
        assert {r.kind for r in _win.history_runs} == {"formation"}, "키가 바뀌었는데 닉네임 이력을 냈다"
        store.set_trade_state(c, key_fp=store.key_fingerprint(config.API_KEY))
        _win._invalidate_trades()
        assert {r.kind for r in _win.history_runs} == {"nickname", "formation"}, {r.kind for r in _win.history_runs}
        starts = [r.start for r in _win.history_runs]
        assert starts == sorted(starts, reverse=True), "최근 구간이 위가 아니다"
    finally:
        c.execute("DELETE FROM trade_state")
        if keep:
            store.set_trade_state(c, **keep)
        c.close()
        _win._invalidate_trades()


def test_dashboard_headline_k6():
    """57 K6 — 머리 칸 아래 한 줄: 순서표대로 고른 문장 · 없으면 빈 줄(자리 그대로) · 낙폭은 같은 구간 중앙보다 깊을 때만."""
    import analysis
    from datetime import date as _d
    from habits import ScoreReport
    # 하락(4,300 → 4,000)이 시즌 최고점(4,500)보다 앞 — 글의 고점·바닥은 시즌 최고점이 아니라 그 하락의 것
    rep = ScoreReport(10, _d(2026, 9, 1), 4000.0, 4100.0, 4500.0, _d(2026, 9, 5), 300.0, _d(2026, 9, 3), 1.0, 5, 9, 4300.0)
    club = core.ClubHead(peaks_dd=sorted((4500.0 + i, 100.0) for i in range(config.R2_BAND_MIN)))
    got = app_main.drawdown_headline(rep, club)
    assert got and "4,300점에서 4,000점" in got and "−100점" in got, got
    lines = app_main.score_report_lines([], [], [], None, datetime(2026, 9, 1))
    assert lines == [], "점이 없는데 리포트를 냈다"
    assert app_main.drawdown_headline(replace(rep, max_dd=100.0), club) is None, "중앙과 같은 낙폭을 눈에 띈다고 했다"
    assert app_main.drawdown_headline(rep, core.ClubHead()) is None and app_main.drawdown_headline(None, club) is None
    keep = _win._narrate_scope
    lb = _win.dashboard.lb_headline
    try:
        _win._narrate_scope = lambda: [analysis.Insight(analysis.SEC_LOSE, "연패 뒤 무너집니다", weight=1.0, rule="_streak_rules")]
        _win._render_dashboard()
        assert lb.text() == "연패 뒤 무너집니다", lb.text()
        h = lb.minimumHeight()
        _win._narrate_scope = lambda: []
        _win._render_dashboard()
        assert lb.text() == "" and lb.minimumHeight() == h > 0, "빈 줄일 때 자리를 잃었다"
    finally:
        _win._narrate_scope = keep
        _win._render_dashboard()


def test_elo_score_report_needs_enough_points():
    from datetime import date as _d
    keep = (_win._rank_seasons, dict(_win._elo))
    try:
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
        def pts(n):
            return _elo_rows([(f"2026-09-{11 + i:02d}T10:00:00", 4000.0 + 10 * i, 500, "snapshot") for i in range(n)])
        _win._on_elo_ready(_OUID, _elo_data(pts(config.R_MIN_POINTS - 1)))
        _win._go_page("승률 그래프", "점수·예측")
        _app.processEvents()
        assert not _win.lb_elo_report.isVisibleTo(_win), "점이 모자란데 리포트를 냈다"
        _win._on_elo_ready(_OUID, _elo_data(pts(config.R_MIN_POINTS)))
        _app.processEvents()
        text = _win.lb_elo_report.text()
        assert _win.lb_elo_report.isVisibleTo(_win) and "시즌 평균" in text and "가장 깊었던 하락은?" in text, text
        assert "점수가 오르는 구간인가?" in text and "무승부를 뺀 승률" in text, text
        rows = pts(config.R_MIN_POINTS) + _elo_rows([("2026-09-12T08:00:00", 4900.0, 100, "search")])   # 하루 안 최고점
        _win._on_elo_ready(_OUID, _elo_data(sorted(rows, key=lambda r: r["taken_at"])))
        assert "최고 4,900점(09.12)" in _win.lb_elo_report.text(), "리포트 최고점이 하루 마지막 점만 봤다(머리 줄과 어긋남)"
        rows = pts(config.R_MIN_POINTS) + _elo_rows([("2026-09-11T08:00:00", 4950.0, 90, "search")])   # 첫날 앞 기록
        _win._on_elo_ready(_OUID, _elo_data(sorted(rows, key=lambda r: r["taken_at"])))
        assert "최고 4,950점(09.11)" in _win.lb_elo_report.text(), "리포트가 첫날 마지막 기록 시각부터 잘랐다"
    finally:
        _win._rank_seasons, _win._elo = keep[0], keep[1]
        _win._go_page("대시보드")


# ── 55 조각 ③ — 달력(D1) · 포메이션 묶기(F1) · 상대 팀컬러 필터(F2) · 확대 막대(G1) · 구단가치 일별(V1) ──

def _wd_day(day, w, lose, div=900, month=10):
    out = []
    for i, k in enumerate(["승"] * w + ["패"] * lose):
        out.append(st_mod.WinDivision(datetime(2026, month, day, 9) + timedelta(minutes=i), div, k, False))
    return out


def test_calendar_tab_cells_arrows_and_month_nav():
    keep = _win.__dict__.get("_season_wins")
    rows = _wd_day(1, 9, 1) + _wd_day(2, 1, 9) + _wd_day(3, 3, 0) + _wd_day(30, 2, 3, month=9)
    _win._season_wins = lambda: rows
    try:
        _win._cal_month = None
        _win._go_page("승률 그래프", "일별 기록")
        _app.processEvents()
        _win._render_calendar()
        g = _win.cal_grid
        assert (g._year, g._month) == (2026, 10) and _win.lb_cal_month.text() == "2026년 10월", "마지막 경기 달로 안 열었다"
        assert sorted(g._cells) == [1, 2, 3], g._cells
        assert (g._cells[1].arrow, g._cells[2].arrow, g._cells[3].arrow) == (1, -1, 0), g._cells
        assert g._cells[3].weak and "판 미만" in g._cells[3].tip and not g._cells[1].weak
        assert g._cells[1].sub == "10판 · 135 FC", g._cells[1].sub
        t = _win.tbl_cal_summary
        assert (t.item(0, 0).text(), t.item(0, 1).text(), t.item(3, 1).text()) == ("하루 판수", "7.7판", "3일"), \
            [t.item(r, 1).text() for r in range(t.rowCount())]
        assert _win.lb_cal_month_sum.text().strip() == "3일 · 195 FC", _win.lb_cal_month_sum.text()
        assert g._fc_max == 135, "진하기 최대 = 이 달 최대 FC"
        assert g._cells[1].fc == 135 and g._cells[3].fc == 45, g._cells
        assert _win.lb_cal_note.isHidden(), _win.lb_cal_note.text()   # 78 ① 시즌표가 있으면 설명 없음
        g.grab()
        assert len(g._hits) == 3, "칸 툴팁이 경기 있는 날만큼이 아니다"
        assert _win.btn_cal_prev.isEnabled(), "앞 달에 경기가 있는데 ◀ 가 꺼졌다"
        _win._on_cal_step(-1)
        assert _win.lb_cal_month.text() == "2026년 9월" and sorted(g._cells) == [30]
        assert not _win.btn_cal_prev.isEnabled(), "저장된 첫 경기 달인데 ◀ 로 더 앞 달(빈 달)로 간다"
        _win._on_cal_step(0)
        assert _win.lb_cal_month.text() == "2026년 10월", "최근 달 단추가 안 돌아왔다"
        _win._on_cal_step(-1)
        _win._ouid, keep_ouid = "다른계정", _win._ouid
        _win._render_calendar()
        assert _win.lb_cal_month.text() == "2026년 10월", "계정이 바뀌었는데 보던 달에 남았다"
        _win._ouid = keep_ouid
    finally:
        if keep is None:
            _win.__dict__.pop("_season_wins", None)
        _win._cal_month = None
        _win._go_page("대시보드")


def test_calendar_labels_say_all_stored_without_season_table():
    """시즌표가 없으면 모든 날이 한 묶음(season_of → None) — 기준선은 저장된 전체인데 "시즌 승률"·"이번 시즌" 이라 쓰면 틀린 말."""
    from datetime import date as _d
    keep = (_win.__dict__.get("_season_wins"), _win._rank_seasons)
    rows = _wd_day(1, 9, 1) + _wd_day(30, 2, 3, month=9)
    _win._season_wins = lambda: rows
    try:
        _win._cal_month = None
        _win._go_page("승률 그래프", "일별 기록")
        _win._rank_seasons = []
        _win._render_calendar()
        t = _win.tbl_cal_summary
        tip, note = _win.cal_grid._cells[1].tip, _win.lb_cal_note.text()
        summary = f"{t.horizontalHeaderItem(2).text()}: {t.item(3, 2).text()}"
        # 막지 않았으면: 9월 30일까지 한 묶음이라 기준선이 9월 경기까지 섞인다 — 그 값을 "시즌 승률" 로 적는다
        assert "저장된 전체 승률" in tip and "시즌 승률" not in tip, tip
        assert "저장된 전체: 2일" in summary and "이번 시즌" not in summary, summary
        assert "시즌표가 없어" in note and not _win.lb_cal_note.isHidden(), note
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 10, 1))]
        _win._render_calendar()
        tip = _win.cal_grid._cells[1].tip
        summary = f"{t.horizontalHeaderItem(2).text()}: {t.item(3, 2).text()}"
        assert "시즌 승률" in tip and "저장된 전체" not in tip, tip
        assert "이번 시즌: 1일" in summary and _win.lb_cal_note.isHidden(), (summary, _win.lb_cal_note.text())
    finally:
        if keep[0] is None:
            _win.__dict__.pop("_season_wins", None)
        _win._rank_seasons = keep[1]
        _win._cal_month = None
        _win._go_page("대시보드")


def test_fc_reward_cards_match_head_line_66():
    """66 G5·G5b — 카드 셋은 늘 이번 시즌 · "매치 보상" 은 머리 줄 H6 "시즌 FC" 와 같은 숫자(M3) · 저장이 늦게 시작하면
    같은 꼬리(M15) · 시즌을 모르면 "최근 70일"(R2) · 순위 보상은 지금 등수로(rank_reward)."""
    from datetime import date as _d
    W = core.WinDivision
    rows = [W(datetime(2026, 9, 10, 9), 900, "승", False), W(datetime(2026, 9, 11, 9), 800, "승", False),
            W(datetime(2026, 9, 12, 9), 800, "패", False), W(datetime(2026, 8, 20, 9), 800, "승", False)]
    keep = (_win.__dict__.get("_season_wins"), _win._rank_seasons, dict(_win._elo), config.WEB_DATA)

    def texts(box):
        return [(c.cap.text(), c.value.text(), c.sub.text()) for c in box[1]]
    try:
        config.WEB_DATA = True
        _win._season_wins = lambda: rows
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
        _win._elo.pop(_OUID, None)
        _win._go_page("성적 진단", "FC 보상")
        _win._render_rewards(_win._details)
        head = {c[0]: c for c in _win._club_cells()}
        match, rank_c, total = texts(_win.rewards_cards)
        assert match[0] == "이번 시즌 매치 보상" and match[1] == head["시즌 FC"][1], (match, head["시즌 FC"])
        fc = core.win_rewards([r for r in rows if r.when >= datetime(2026, 9, 10)]).fc
        assert match[1] == f"{fc:,} FC" and fc > 0, "8/20 경기(지난 시즌)를 넣었다"
        assert "저장 시작 뒤부터" not in match[2], match
        assert rank_c[1] == "—" and total[1] == f"{fc:,} FC", (rank_c, total)
        _win._go_page("승률 그래프", "일별 기록")
        _win._render_calendar()
        assert texts(_win.cal_fc_cards) == texts(_win.rewards_cards), "달력 카드와 FC 보상 카드가 다르다"
        assert "FC 보상량: 이긴 판마다" in _win.cal_fc_cards[0].text(), _win.cal_fc_cards[0].text()
        # 순위 보상 — 지금 등수(마지막 점)로
        data = _elo_data(_elo_rows([("2026-09-12T10:00:00", 4500.0, 150, "search")]))
        _win._on_elo_ready(_OUID, data)
        _win._render_rewards(_win._details)
        rank, got = app_main.rank_reward(_win._elo_points(), _d.today())
        assert rank == 150, rank
        match, rank_c, total = texts(_win.rewards_cards)
        assert rank_c[2].startswith("현재 150등") and total[1] == f"{fc + (got or 0):,} FC", (rank_c, total)
        # M15 — 시즌 시작이 8/1 이면 첫 저장 경기(8/20)가 N12_START_DAYS 밖 → 같은 꼬리
        _win._elo.pop(_OUID, None)
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 7, 1), _d(2026, 8, 1))]
        _win._render_rewards(_win._details)
        assert "저장 시작 뒤부터" in texts(_win.rewards_cards)[0][2], texts(_win.rewards_cards)
        assert "저장 시작 뒤부터" in {c[0]: c for c in _win._club_cells()}["시즌 FC"][2]
        # R2 — 시즌표·스냅숏 없음
        _win._rank_seasons = []
        _win._render_rewards(_win._details)
        assert texts(_win.rewards_cards)[0][0] == f"최근 {config.ELO_FALLBACK_DAYS}일 매치 보상", texts(_win.rewards_cards)
        assert "시즌 모름" in {c[0]: c for c in _win._club_cells()}["시즌 FC"][2]
    finally:
        if keep[0] is None:
            _win.__dict__.pop("_season_wins", None)
        _win._rank_seasons, config.WEB_DATA = keep[1], keep[3]
        _win._elo.clear()
        _win._elo.update(keep[2])
        _win._go_page("대시보드")


def test_calendar_fc_shade_keeps_text_readable_66():
    """66 G5 · M4 — 진하기 0~상한 전부에서, 두 팔레트 모두 칸 위 승률(TEXT) 4.5 · ▲▼(GOOD/BAD) 3.0 · 날짜·판수 글자 4.5.
    미달 날·이 달 최대 0 은 진하기 0(M11). 상한이 0.06 밑이면 진하기가 안 보인다 — 그것도 FAIL."""
    import widgets
    names = ("PANEL_2", "CAL_FILL", "TEXT", "TEXT_DIM", "GOOD", "BAD")
    keep = {k: getattr(T, k) for k in names}
    assert config.CAL_FC_MAX_MIX >= 0.06, config.CAL_FC_MAX_MIX
    try:
        for mode, pal in T._PALETTES.items():
            for k in names:
                setattr(T, k, pal[k])
            for i in range(0, 101):
                bg, dim = widgets.cal_cell_colors(i, 100, False)
                for k, need in (("TEXT", 4.5), ("GOOD", 3.0), ("BAD", 3.0)):
                    assert T.contrast(pal[k], bg) >= need, (mode, i, k, T.contrast(pal[k], bg))
                assert T.contrast(dim, bg) >= 4.5, (mode, i, dim)
            top, _ = widgets.cal_cell_colors(100, 100, False)
            assert top == T.blend(pal["PANEL_2"], pal["CAL_FILL"], config.CAL_FC_MAX_MIX) and top != pal["PANEL_2"], mode
            assert widgets.cal_cell_colors(100, 100, True) == (pal["PANEL_2"], pal["TEXT_DIM"]), "미달 날이 칠해졌다"
            assert widgets.cal_cell_colors(0, 0, False)[0] == pal["PANEL_2"], "최대 0 에서 칠했다"
            assert widgets.cal_cell_colors(500, 100, False)[0] == top, "최대를 넘는 값이 상한을 넘었다"
    finally:
        for k, v in keep.items():
            setattr(T, k, v)


def test_tactics_opp_formation_grouping():
    cb = _win.cb_tactics_form
    try:
        _win._render_tactics(_win._details)
        raw = _win.opp_formation_list.all_rows()
        assert _win.lb_tactics_form_note.isHidden()
        assert all(r.icon == ("formation", r.key) for r in raw), "묶지 않은 줄은 자리 그림이 있어야"
        cb.setCurrentIndex(cb.findData("back"))       # 콤보가 전술 탭을 무효화 → LAZY_RENDER=False 라 바로 그린다
        grouped = [(r.label, r.icon) for r in _win.opp_formation_list.all_rows()]
        assert all(icon == () for _l, icon in grouped), "묶은 줄은 자리 그림이 없다"
        opp = app_main.core.formation_stats(_win._details, _win._ouid)
        want = {}
        for f in opp:
            k = app_main.core.formation_group(f.formation, "back") or app_main.core.blank_label("formation", "back")
            want[k] = want.get(k, 0) + f.games
        assert {r[0] for r in grouped} == set(want) and all(k.endswith("백") or k == "기타·모름" for k in want), grouped
        assert len(grouped) <= len(raw) and not _win.lb_tactics_form_note.isHidden()
        g = app_main.group_formation_stats(opp, "front")
        assert sum(s.games for s in g) == sum(f.games for f in opp) and all(s.positions == () for s in g)
        assert opp[0].positions != () or not opp, "묶기가 원본 FormationStat 을 고쳤다"
    finally:
        cb.setCurrentIndex(cb.findData("raw"))


def test_match_list_opponent_teamcolor_filter():
    keep = dict(_win._team_colors)
    try:
        _win._render_matches(_win._slice()[0])
        t = _win.match_list
        opps = [c.opponent for c in t.all_rows()]
        assert len(set(opps)) >= 2, "상대가 둘 이상이어야 이 테스트가 뜻이 있다"
        _win._team_colors[opps[0]] = "테스트컬러"
        _win._invalidate_teamcolor()
        cb = _win.cb_match_color
        assert cb.findText("테스트컬러") >= 0, "새 팀컬러가 경기 목록 콤보에 안 들어왔다"
        cb.setCurrentText("테스트컬러")
        shown = [c.opponent for c in t.rows()]
        assert shown and set(shown) == {opps[0]} and len(shown) == opps.count(opps[0]), shown
        _win._match_filter_btns["승"].setChecked(False)   # 결과 필터와 겹쳐도 둘 다 지킨다
        _win._apply_match_filter()
        assert t.rows() == [c for c in t.all_rows() if "승" not in c.result and c.opponent == opps[0]], t.rows()
        _win._match_filter_btns["승"].setChecked(True)
        cb.setCurrentText(_win.POSITION_COLOR_ALL)
        assert len(t.rows()) == len(t.all_rows()) == len(opps), "전체로 돌렸는데 숨은 줄이 남았다"
        cb.setCurrentText("테스트컬러")
        _win._render_matches(_win._slice()[0])                # 다시 그려도 고른 색이 남는다
        assert cb.currentText() == "테스트컬러" and {c.opponent for c in t.rows()} == {opps[0]}, t.rows()
    finally:
        _win._team_colors.clear()
        _win._team_colors.update(keep)
        _win._match_filter_btns["승"].setChecked(True)
        _win._refresh_match_color_options()
        _win.cb_match_color.setCurrentText(_win.POSITION_COLOR_ALL)


def _opp_rows():
    import widgets
    return {r.key: widgets.wdl_text(r.win, r.draw, r.lose) for r in _win.opp_list.rows()}


def test_opponent_matchup_filter_k1():
    """57 K1 — 경기 수(최근 N · 직접 지정) · 표본 미만 빼기 · 내 포메이션 · 상대 구단가치 칸('모름' 포함) · 계정이 바뀌면 처음으로."""
    keep_vals, keep_bins = dict(_win._team_values), _win._opp_value_bins
    try:
        _win._go_page("상대 전적")
        all_rows = _opp_rows()
        assert len(all_rows) >= 2, "상대가 둘 이상이어야 뜻이 있다"
        # 직접 지정 1경기 — 가장 최근 경기의 상대 한 줄(1전)
        _win.cb_opp_games.setCurrentIndex(_win.cb_opp_games.findData(-1))
        assert _win.sp_opp_games.isVisibleTo(_win), "직접 지정인데 숫자 칸이 안 보인다"
        _win.sp_opp_games.setValue(1)
        rows = _opp_rows()
        assert list(rows) == [_win._matches[0].opponent] and sum(1 for _ in rows) == 1, rows
        assert "1경기 /" in _win.lb_opponent_note.text(), _win.lb_opponent_note.text()
        _win.cb_opp_games.setCurrentIndex(0)
        assert _opp_rows() == all_rows and not _win.sp_opp_games.isVisibleTo(_win)
        # 표본 — 체크해야 콤보가 살고, 문턱 미만 상대가 빠진다
        assert not _win.cb_opp_min.isEnabled()
        games = {s.nickname: s.games for s in app_main.opponent_stats(_win._matches)}
        _win.chk_opp_min.setChecked(True)
        _win.cb_opp_min.setCurrentIndex(_win.cb_opp_min.findData(max(games.values())))
        assert set(_opp_rows()) == {n for n, g in games.items() if g >= max(games.values())}, _opp_rows()
        _win.chk_opp_min.setChecked(False)
        assert _opp_rows() == all_rows
        # 내 포메이션 — 콤보 글자의 (N) 이 그 포메이션 경기 수, 표 줄 합도 같다
        cb = _win.cb_opp_form
        assert cb.count() >= 2, "포메이션 목록이 비었다"
        cb.setCurrentIndex(1)
        n = int(cb.currentText().rsplit("(", 1)[1].rstrip(")"))
        forms = core.match_formations(_win._details, _OUID)
        want = [m for m in _win._matches if forms.get(m.match_id) == cb.currentData()]
        assert len(want) == n and set(_opp_rows()) == {m.opponent for m in want}, (n, want, _opp_rows())
        cb.setCurrentIndex(0)
        # 상대 구단가치 — rank.db 칸이 없으면 숨김, 있으면 칸 + '모름'
        assert not _win.cb_opp_value.isVisibleTo(_win), "칸을 모르는데 구단가치 필터가 보인다"
        opp = _win._matches[0].opponent
        from types import SimpleNamespace as NS
        _win._opp_value_bins = lambda: [NS(lo=None, hi=100), NS(lo=100, hi=None)]
        _win._team_values[opp] = 50
        _win._invalidate_teamcolor()          # 팀컬러(구단가치)를 새로 읽었다 — 보이는 상대 전적이 다시 그려져야 한다
        assert _win.cb_opp_value.isVisibleTo(_win) and _win.cb_opp_value.count() == 4, _win.cb_opp_value.count()
        _win.cb_opp_value.setCurrentIndex(_win.cb_opp_value.findData(0))
        assert set(_opp_rows()) == {opp}, _opp_rows()
        _win.cb_opp_value.setCurrentIndex(_win.cb_opp_value.findData(-1))
        assert opp not in _opp_rows() and set(_opp_rows()) == set(all_rows) - {opp}, _opp_rows()
        # 계정이 바뀌면 처음으로(남의 포메이션이 고른 채로 남지 않게)
        _win.cb_opp_games.setCurrentIndex(1)
        _win._reset_opp_filter()
        assert (_win.cb_opp_games.currentIndex(), _win.cb_opp_value.currentIndex(), _win.chk_opp_min.isChecked()) == (0, 0, False)
    finally:
        _win._team_values.clear()
        _win._team_values.update(keep_vals)
        _win._opp_value_bins = keep_bins
        _win._reset_opp_filter()
        _win._invalidate("opponents")


def test_opponent_diff_column_hides_weak_rows_k13():
    """57 K13 — '(±차)' = 그 상대 승률 − 필터에 걸린 경기 전체 승률 · 표본 흐림 줄은 비운다(E2) · 65 M4 줄 목록에서도."""
    import widgets
    lst = _win.opp_list
    keep = app_main.core.MIN_COND

    from PySide6.QtCore import QRectF

    def diff_seg(row):
        segs = lst.rate_delegate.layout(row, QRectF(0, 0, 900, 60), lst.font())
        return next((s.text for s in segs if s.role == "diff"), "")
    try:
        _win._go_page("상대 전적")
        assert all(r.diff is None and diff_seg(r) == "" for r in lst.rows()), "몇 판짜리 줄에 차이를 보였다"
        app_main.core.MIN_COND = 1
        _win._invalidate("opponents")
        ms = _win._matches
        base = core.win_pct(sum("승" in m.result for m in ms), sum("무" in m.result for m in ms),
                            sum("패" in m.result for m in ms), core.mixed_of_matches(ms))
        stats = {s.nickname: s for s in app_main.opponent_stats(ms)}
        for row in lst.rows():
            s = stats[row.key]
            want = f"({s.win_rate - base:+.1f})" if s.rated else ""     # 분모 0(몰수뿐)은 승률도 '—' 라 차이가 없다
            assert diff_seg(row) == want, (diff_seg(row), s.win_rate, base)
            if want:   # 3.1.1 P3 — 차이 글자 색이 그 줄의 차이를 따른다
                got = lst.rate_delegate.colors(row)["diff"]
                assert got == widgets.delta_color(s.win_rate - base) != T.TEXT, (got, s.win_rate - base)
        assert any(diff_seg(r) for r in lst.rows()), "차이를 보인 줄이 없다 — 검사가 비었다"
        # 승률 카드 = 같은 기준(필터에 걸린 경기 전체)
        assert _win.card_opp_rate.value.text() == widgets.win_rate_text(
            *(sum(k in m.result for m in ms) for k in "승무패"), core.mixed_of_matches(ms), short=True)
    finally:
        app_main.core.MIN_COND = keep
        _win._invalidate("opponents")


def test_opponent_rows_skip_missing_nickname_67():
    """67 R12 — 닉네임 없는 상대(NO_NICK)는 서로 다른 사람이 한 줄로 뭉친다 → 구단주 줄에서 뺀다(머리 글 안내는 78 ① 에 지웠다).
    승률 카드는 그 경기까지 센다(필터에 걸린 경기 전체 — 기준이 바뀌지 않게)."""
    import dataclasses
    import widgets
    keep = _win._matches
    ms = list(keep) + [dataclasses.replace(keep[0], match_id=f"nn{i}", opponent=app_main.core.NO_NICK, result=r)
                       for i, r in enumerate("승패패")]
    try:
        _win._matches = ms
        _win._go_page("상대 전적")
        _win.cb_opp_group.setCurrentIndex(_win.cb_opp_group.findData("owner"))
        _win._invalidate("opponents")
        # 빼지 않았으면 무엇이 보였을지 먼저 — 집계에는 "-" 한 상대 3경기가 있다
        assert {s.nickname: s.games for s in app_main.opponent_stats(ms)}.get(app_main.core.NO_NICK) == 3
        keys = [r.key for r in _win.opp_list.rows()]
        assert keys and app_main.core.NO_NICK not in keys, keys
        assert _win.card_opp_rate.sub.text() == f"{len(ms):,}경기", _win.card_opp_rate.sub.text()
        assert _win.card_opp_rate.value.text() == widgets.win_rate_text(
            *(sum(k in m.result for m in ms) for k in "승무패"), core.mixed_of_matches(ms), short=True)
    finally:
        _win._matches = keep
        _win.cb_opp_group.setCurrentIndex(_win.cb_opp_group.findData("owner"))
        _win._invalidate("opponents")


def test_match_list_search_matches_nickname_or_color_k13():
    keep = dict(_win._team_colors)
    t = _win.match_list
    try:
        _win._render_matches(_win._slice()[0])
        opps = [c.opponent for c in t.all_rows()]
        a, b = opps[0], next(o for o in opps if o != opps[0])
        _win._team_colors[b] = "찾는팀컬러"
        _win.ed_match_color.setText(a[1:] or a)          # 닉네임 일부
        shown = {c.opponent for c in t.rows()}
        assert a in shown and b not in shown, shown
        _win.ed_match_color.setText("찾는팀")               # 팀컬러 글자 — 팀컬러를 찾던 줄은 남는다
        shown = {c.opponent for c in t.rows()}
        assert shown == {b}, shown
        _win.ed_match_color.setText("")
        assert len(t.rows()) == len(t.all_rows())
    finally:
        _win.ed_match_color.setText("")
        _win._team_colors.clear()
        _win._team_colors.update(keep)


def test_profile_action_opens_nexon_rank_page_k13():
    opened = []
    orig = app_main.QDesktopServices.openUrl
    app_main.QDesktopServices.openUrl = lambda url: opened.append(url.toString())   # toString 은 한글을 풀어 준다
    try:
        lst = _win.match_list
        for view in (lst, _win.opp_list):
            _win._render_all()
            _win._render_matches(_win._slice()[0])
            assert view.contextMenuPolicy() == Qt.ContextMenuPolicy.ActionsContextMenu and view.actions(), "오른쪽 클릭 메뉴가 없다"
            # 65 M2·M4 — 줄 목록은 고른 줄의 상대(첫 줄과 다른 상대여야 '고른 줄' 을 잰다)
            nick_of = (lambda i: lst.row(i).opponent) if view is lst else (lambda i: view.row(i).key)
            r = next(i for i in range(view.rowCount()) if nick_of(i) != nick_of(0))
            view.setCurrentIndex(view.model().index(r, 0))
            nick = nick_of(r)
            view.actions()[0].trigger()
            assert opened and opened[-1].endswith(nick) and "datacenter/rank" in opened[-1], opened
        # 상대 전적의 묶음 줄(포메이션·팀컬러)은 사람이 아니다 — 메뉴를 눌러도 아무 데도 안 연다
        _win.cb_opp_group.setCurrentIndex(_win.cb_opp_group.findData("formation"))
        n = len(opened)
        _win.opp_list.setCurrentIndex(_win.opp_list.model().index(0, 0))
        _win.opp_list.actions()[0].trigger()
        assert len(opened) == n, opened[n:]
        _win.cb_opp_group.setCurrentIndex(0)
    finally:
        app_main.QDesktopServices.openUrl = orig


def test_teamcolor_batches_do_not_redraw_match_list():
    """57 ① 리뷰 — 팀컬러를 받는 중(2초마다) 경기 목록·상대 전적을 통째로 다시 그리지 않는다(1만 경기 약 2초 — 창이 굳는다).
    다 받은 뒤 한 번은 그린다 — 그 길이 끊기면 상대 정보 열이 끝내 안 채워진다."""
    calls = []
    keep_m, keep_o = _win._render_matches, _win._render_opponents
    keep_at = _win._teamcolor_rendered_at
    try:
        _win._go_page("경기 목록")
        _win._render_matches = lambda ms: calls.append("matches")
        _win._render_opponents = lambda ms: calls.append("opponents")
        _win._teamcolor_rendered_at = -1e9          # 다시 그릴 간격이 지났다
        _win._on_teamcolor_loaded({"배치상대": ("", 1, "")})
        assert _win._teamcolor_rendered_at > 0, "받는 중 다시 그리기 길을 안 탔다(테스트가 빈다)"
        assert "matches" not in calls, ("받는 중에 경기 목록을 통째로 다시 그렸다", calls)
        _win._invalidate_teamcolor()                # 다 받았다(_on_teamcolor_finished 끝)
        assert "matches" in calls, ("다 받은 뒤 경기 목록을 안 그렸다", calls)
    finally:
        _win._render_matches, _win._render_opponents = keep_m, keep_o
        _win._teamcolor_rendered_at = keep_at
        _win._team_colors.pop("배치상대", None)
        _win._team_values.pop("배치상대", None)
        _win._teamcolor_raw.pop("배치상대", None)
        _win._invalidate("matches")


def test_match_list_opponent_columns_k3():
    """57 K3 — 상대 팀컬러·구단가치(지금 값)·맞대결(시즌 범위)·상대 점수(관문 + 내 계정 · 원본 14일은 정확값, 옛 경기는 구간)."""
    import dataclasses
    keep_c, keep_v, keep_elo = dict(_win._team_colors), dict(_win._team_values), dict(_win._elo)
    t = _win.match_list
    try:
        ms = _win._slice()[0]
        a = ms[0].opponent
        _win._elo.pop(_OUID, None)
        _win._render_matches(ms)
        _win._go_page("경기 목록")
        _win._team_colors[a] = "어떤팀"
        _win._team_values[a] = 1_234_000_000_000
        _win._invalidate_teamcolor()          # 팀컬러를 새로 읽었다 — 보이는 경기 목록이 다시 그려져야 한다
        c0 = next(c for c in t.all_rows() if c.match_id == ms[0].match_id)
        assert c0.color == "어떤팀"
        assert c0.value_text == "1.2조", c0.value_text   # 짧게
        s = {x.nickname: x for x in app_main.opponent_stats(_win._matches)}[a]
        assert c0.h2h_text == app_main.wdl_text(s.win, s.draw, s.lose)
        assert all(c.opp_score is None for c in t.all_rows()), "관문·내 계정 확인 없이 상대 점수 칸이 있다"
        # 점수가 오면 칸이 생기고 — 정확값 · 구간 · 모름
        data = app_main.EloSeries(1, [], {}, opp_scores={ms[0].match_id: core.OppScore(4100.0, 7, "2026-01-03T10:00")},
                                  opp_bands={ms[1].match_id: 1000})
        _win._elo[_OUID] = data
        _win._render_matches(ms)
        by_id = {c.match_id: c.opp_score for c in t.all_rows()}
        assert by_id[ms[0].match_id] == "4,100 · 7위", by_id
        assert by_id[ms[1].match_id] == core.opp_rank_label(1000), by_id
        assert all(v == "-" for k, v in by_id.items() if k not in (ms[0].match_id, ms[1].match_id)), by_id
        # 65 M2 — 1280×720 · 작은 화면 · 기본 · 큰 창에서 카드 글자가 안 잘린다(PyQt 5 의 카드판). 픽스처 글자는 짧아
        # 실데이터 길이로 채운다(긴 팀컬러 이름 · 억 단위 구단가치 · 네 자리 점수·순위 · 열두 글자 닉네임 · 승부차기 · 패드)
        for m in ms:
            _win._team_colors[m.opponent] = "맨체스터 유나이티드"
            _win._team_values[m.opponent] = 75_312_345_678
        _win._elo[_OUID] = app_main.EloSeries(1, [], {}, opp_scores={
            m.match_id: core.OppScore(4321.0, 9876, "2026-01-03T10:00") for m in ms}, opp_bands={})
        _win._go_page("경기 목록")
        _win._render_matches(ms)
        long_cards = [dataclasses.replace(c, opponent="가나다라마바사아자차카타", shootout="승부차기 10:9", opp_pad=True,
                                          division_key=1, emblem_key="x", h2h_text="1,234승 567무 1,089패",
                                          stats=(("점유", "100%"), ("슛", "18/25"), ("패스", "100%"), ("평점", "10.00")))
                      for c in t.all_rows()[:1]]
        dlg = t.card_delegate
        small = app_main.initial_window(1280, 688).min_size
        try:
            for size in (app_main.MIN_WINDOW, small, (1600, 900), app_main.LARGE_WINDOW):
                _win.setMinimumSize(*small)
                _at_size(*size)
                w = t.viewport().width()
                assert w > 600, (size, w)
                bad = [(c.opponent, dlg.overflow(c, w, t.font())) for c in t.all_rows() + long_cards
                       if dlg.overflow(c, w, t.font())]
                assert not bad, (size, w, bad[:3])
            # 이 검사가 비지 않게 — 아주 좁으면 잘린다고 말해야 한다
            assert dlg.overflow(long_cards[0], 420, t.font()), "좁은 폭에서도 넘침이 0 — overflow 가 아무것도 안 잰다"
        finally:
            _win.setMinimumSize(*app_main.MIN_WINDOW)
            _at_size(1600, 900)
        t.viewport().grab()   # 대리자 그리기가 예외 없이 도는지(그림 경로)
        h_on = dlg.row_height(t.font())
        _win.chk_match_extra.setChecked(False)
        assert not t.show_extra and dlg.row_height(t.font()) < h_on, "끄기를 안 따랐다(셋째 줄이 그대로)"
        assert not any(s.role in ("color", "value", "h2h", "oppscore")
                       for s in dlg.layout(t.all_rows()[0], QRectF(0, 0, 900, 200), t.font()))
        assert t.visualRect(t.model().index(0, 0)).height() == dlg.row_height(t.font()), "줄 높이를 다시 안 잡았다"
        _win.chk_match_extra.setChecked(True)
        assert t.show_extra and t.visualRect(t.model().index(0, 0)).height() == h_on
    finally:
        _win._team_colors.clear()
        _win._team_colors.update(keep_c)
        _win._team_values.clear()
        _win._team_values.update(keep_v)
        _win._elo.clear()
        _win._elo.update(keep_elo)
        _win._render_matches(_win._slice()[0])


def test_opp_score_gate_and_my_account_k3():
    """57 K3 관문 — ① 관문(수집 켜짐 + 안내 9·14) 미달이면 EloLoader 에 경기를 안 준다 ② 내 계정(my_ouid + 키 지문)이
    아니면 점수를 안 읽는다. 막지 않았으면(관문 통과 · 내 계정) 점수가 실린다는 것부터 단언한다."""
    import rankcollect
    keep = (config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.NOTICE_VERSION)
    orig_scores, orig_ro = app_main.core.opp_scores, app_main.rankcollect.open_rank_db_ro
    got_args = []
    app_main.core.opp_scores = lambda r, opp: (got_args.append(opp), {"x": core.OppScore(1.0, 1, "t")})[1]
    tmp = pathlib.Path(tempfile.mkdtemp()) / "r.db"
    rankcollect.open_rank_db(tmp).close()
    app_main.rankcollect.open_rank_db_ro = lambda *a, **k: orig_ro(tmp)
    c = store.open_db(config.DB_PATH)
    try:
        config.WEB_DATA = config.RANK_COLLECT = True
        config.NOTICE_VERSION = config.NOTICE_ACCEPTED = config.OPP_SCORE_NOTICE_VERSION
        opp = _win._opp_score_matches(_OUID)
        assert opp is not None, "관문을 넘었는데 경기를 안 준다"
        assert _win._opp_score_matches("남의-ouid") is None, "지금 계정이 아닌데 경기를 준다"
        out = []

        def run(state):
            store.set_trade_state(c, **state)
            w = app_main.EloLoader(_OUID, 1, opp)
            w.elo_ready.connect(lambda _o, d: out.append(d))
            w.run()
            return out[-1]
        fp = store.key_fingerprint(config.API_KEY)
        d = run({"my_ouid": _OUID, "key_fp": fp})
        assert d.opp_scores == {"x": core.OppScore(1.0, 1, "t")} and got_args[-1] is opp, d.opp_scores
        assert d.opp_bands == {}, d.opp_bands
        # 결과가 오면 보이는 경기 목록이 다시 그려져 카드에 상대 점수 칸이 생긴다
        _win._go_page("경기 목록")
        assert all(c.opp_score is None for c in _win.match_list.all_rows())
        keep_elo, keep_req = dict(_win._elo), dict(_win._elo_req)
        try:
            _win._elo_req[_OUID] = d.req
            _win._on_elo_ready(_OUID, d)
            assert all(c.opp_score is not None for c in _win.match_list.all_rows()), "상대 점수가 왔는데 경기 목록을 다시 안 그렸다"
        finally:
            _win._elo.clear()
            _win._elo.update(keep_elo)
            _win._elo_req.clear()
            _win._elo_req.update(keep_req)
            _win._render_matches(_win._slice()[0])
        assert run({"my_ouid": "남", "key_fp": fp}).opp_scores is None, "남의 계정인데 상대 점수를 읽었다"
        assert run({"my_ouid": _OUID, "key_fp": "다른 키"}).opp_scores is None, "키가 바뀌었는데 상대 점수를 읽었다"
        config.NOTICE_ACCEPTED = config.OPP_SCORE_NOTICE_VERSION - 1
        assert _win._opp_score_matches(_OUID) is None, "안내 14 전인데 경기를 준다"
        config.NOTICE_ACCEPTED = config.OPP_SCORE_NOTICE_VERSION
        config.RANK_COLLECT = False
        assert _win._opp_score_matches(_OUID) is None, "수집이 꺼졌는데 경기를 준다"
    finally:
        config.WEB_DATA, config.RANK_COLLECT, config.NOTICE_ACCEPTED, config.NOTICE_VERSION = keep
        app_main.core.opp_scores, app_main.rankcollect.open_rank_db_ro = orig_scores, orig_ro
        with c:
            c.execute("DELETE FROM trade_state")
        c.close()


def test_zoom_bar_span_rules():
    z = app_main.ZoomBar()
    got = []
    z.range_changed.connect(lambda a, b: got.append((a, b)))
    z.set_labels([f"d{i}" for i in range(10)])
    assert z.span() == (0, 9) and z.is_full()
    z.set_span(3, 3)
    assert z.span() == (3, 4) and got == [(3, 4)], "점 하나 구간을 허락했다(선이 안 그려진다)"
    z.set_span(9, 9)
    assert z.span() == (8, 9)
    z.set_labels([f"d{i}" for i in range(10)])
    assert z.span() == (8, 9), "같은 이름인데 고른 구간을 버렸다"
    z.set_labels([f"d{i}" for i in range(11)])
    assert z.span() == (0, 10), "점이 바뀌었는데 옛 구간에 남았다"
    z.set_span(2, 5)
    z.mouseDoubleClickEvent(None)
    assert z.is_full()
    labels = [f"d{i}" for i in range(11)]
    z.set_labels(labels, "계정A")
    z.set_span(2, 5)
    z.set_labels(labels, "계정A")
    assert z.span() == (2, 5), "같은 계정·같은 날짜인데 고른 구간을 버렸다"
    z.set_labels(labels, "계정B")
    assert z.is_full(), "계정이 바뀌었는데 날짜가 같다고 옛 계정의 좁힌 구간에 남았다"


def test_step_decimals_keeps_tick_labels_apart():
    assert [app_main._step_decimals(s) for s in (1000.0, 5.0, 1.0, 0.5, 0.25, 0.2, 0.025)] == [0, 0, 0, 1, 2, 1, 3]
    for step in (0.25, 0.5, 0.025):
        d = app_main._step_decimals(step)
        texts = {f"{{:,.{d}f}}".format(12345 + i * step) for i in range(3)}
        assert len(texts) == 3, (step, texts)


def test_division_run_chart_season_zoom_and_badges_66():
    """66 G1 — 범위는 이번 시즌(시즌 콤보·"최근 N일" 과 무관 · R1·U1) · 구간 단위 확대 · 시즌을 모르면 "최근 70일"(R2) ·
    ELO·시즌표가 도착하면 다시 그림(M2) · 배지 그림이 오면 축이 그림으로."""
    from datetime import date as _d
    from PySide6.QtGui import QPixmap
    keep_seasons = _g1_season(_d(2026, 1, 2))     # 지금 시즌 = 01-02~ : 0002·0003·0004 · 0001 은 지난 시즌
    keep = (dict(_win._elo), dict(_win._div_badges), set(_win._div_badge_tried))
    det = _sc_details({"0003": 800})
    try:
        _win._on_loaded(_MATCHES, det, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        dc, z = _win.division_chart, _win.zoom_division
        want = [(900, 1), (800, 1), (900, 1)]
        assert [(r.division, r.games) for r in dc._runs] == want, dc._runs
        assert _win.gb_division.title() == "등급: 지금 시즌 (01/02~)", _win.gb_division.title()
        # R1 — 지난 시즌(0001 만)을 골라도 계단은 이번 시즌 그대로(막지 않았으면 _details 의 한 경기만 남는다)
        past = next(i for i in range(_win.cb_season.count())
                    if getattr(_win.cb_season.itemData(i), "no", None) == 88)
        _win.cb_season.setCurrentIndex(past)
        assert len(_win._details) == 1, "전제 — 지난 시즌을 고르면 _details 가 한 경기"
        assert [(r.division, r.games) for r in dc._runs] == want, dc._runs
        # 구간 단위 확대 — 고른 구간만 · 제목은 그 구간 날짜 · 머리 줄은 시즌 전체 그대로
        assert z.isVisibleTo(_win.gb_division) and z.is_full()
        z.set_span(1, 2)
        assert [(r.division, r.games) for r in dc._runs] == want[1:], dc._runs
        assert _win.gb_division.title() == "등급: 01/02~01/03", _win.gb_division.title()
        assert dc._head == (900, 800, "시즌 최고"), dc._head
        assert dc._markers == [0], dc._markers        # 슈챔 진입은 확대한 목록 안 번호로
        z.set_span(0, 2)
        # 배지 — 그림이 오면 축이 글자 대신 그림(못 받은 등급은 글자)
        pm = QPixmap(30, 30)
        pm.fill()
        dc.grab()
        assert dc.marks["badges"] == 0, dc.marks
        _win._div_badges.clear()
        path = pathlib.Path(tempfile.mkdtemp()) / "b.png"
        pm.save(str(path))
        _win._on_division_badge(800, str(path))
        dc.grab()
        assert dc.marks["badges"] == 1 and dc._badges[800].width() == dc.BADGE, dc.marks
        broken = path.with_name("x.png")
        broken.write_bytes(b"not png")
        _win._on_division_badge(900, str(broken))
        assert not broken.exists() and 900 not in _win._div_badges, "깨진 캐시를 안 지웠다"
        # 배지 받기 — 한 번 시도한 등급은 다시 안 받는다(오프라인에서 그릴 때마다 로더가 뜨지 않게)
        started = []
        keep_start = app_main.IconLoader.start
        app_main.IconLoader.start = lambda self: started.append([e[1] for e in self._entries])
        keep_meta = _win._division_meta
        _win._division_meta = [{"divisionId": 800}, {"divisionId": 900}]
        _win._div_badge_tried = set()
        try:
            _win._render_division()
            _win._render_division()
        finally:
            app_main.IconLoader.start = keep_start
            _win._division_meta = keep_meta
        assert started == [[800, 900]], started
        # R2 — 시즌표도 스냅숏도 없으면 "최근 70일" · "기간 최고"(70일을 시즌이라 부르지 않는다)
        _win._div_span = (_win._div_span[0], False)
        _win._draw_division()
        assert dc._head == (900, 800, "기간 최고"), dc._head
        _win._rank_seasons = []
        _win._render_division()
        assert _win.gb_division.title() == f"등급: 최근 {config.ELO_FALLBACK_DAYS}일", _win.gb_division.title()
        # M2 — ELO 스냅숏의 시즌 시작이 도착하면 계단이 그 시작으로 다시 그려진다(_on_elo_ready)
        data = _elo_data([])
        data.snap_season_start = "2026-01-02T22:00:00"
        _win._on_elo_ready(_OUID, data)
        assert [(r.division, r.games) for r in dc._runs] == [(800, 1), (900, 1)], dc._runs
        assert dc._head == (900, 800, "시즌 최고"), dc._head
        _win._elo.pop(_OUID, None)
        # M2 — 시즌표 도착
        _win._on_seasons_loaded([sn.Season(88, "시즌 8", _d(2025, 10, 1), _d(2026, 1, 3))])
        assert [(r.division, r.games) for r in dc._runs] == [(900, 1)], dc._runs
    finally:
        _win._rank_seasons = keep_seasons
        _win._elo, _win._div_badges, _win._div_badge_tried = keep[0], keep[1], keep[2]
        _restore_account()


def test_elo_zoom_and_team_value_chart():
    from datetime import date as _d
    keep = (_win._rank_seasons, dict(_win._elo), config.WEB_DATA)
    try:
        config.WEB_DATA = True
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
        rows = _elo_rows([(f"2026-09-{d:02d}T10:00:00", 3000.0 + d, 500, "search") for d in range(11, 21)])
        for i, r in enumerate(rows):
            r["team_value"] = None if i == 3 else (1_000_000_000_000 + i * 10 ** 10)
        rows.append({**rows[-1], "taken_at": "2026-09-20T09:00:00", "team_value": 1})   # 같은 날 앞 시각 — 늦은 값이 이긴다
        rows.sort(key=lambda r: r["taken_at"])
        _win._on_elo_ready(_OUID, _elo_data(rows))
        _win._go_page("승률 그래프", "점수·예측")
        _app.processEvents()
        assert len(_win.elo_chart._points) == 10 and _win.zoom_elo.isVisibleTo(_win)
        _win.zoom_elo.set_span(2, 5)
        assert [p[0] for p in _win.elo_chart._points] == ["09/13", "09/14", "09/15", "09/16"], _win.elo_chart._points
        assert _win.elo_chart._ranks == [500] * 4, ("57 K5 순위 점선이 확대 구간을 안 따른다", _win.elo_chart._ranks)
        tv = _win.team_value_chart
        assert _win.gb_team_value.isVisibleTo(_win) and tv.isVisibleTo(_win)
        assert len(tv._points) == 9, "빈 값 날을 0 으로 그렸거나 같은 날 둘을 다 넣었다"
        assert tv._points[0][1] == 10000.0 and tv._axis.fmt.endswith("억"), (tv._points[0], tv._axis.fmt)
        assert tv._points[-1][1] == (1_000_000_000_000 + 9 * 10 ** 10) / 1e8, "같은 날은 늦은 값"
        assert "데이터센터" in _win.lb_team_value_note.text()
        _win.zoom_team_value.set_span(0, 2)
        assert len(tv._points) == 3
        # 한 시즌 폭이 1억 밑이면 눈금 간격이 1억보다 촘촘하다 — 정수로 적으면 같은 눈금 글자가 겹친다
        for i, r in enumerate(rows):
            r["team_value"] = 1_234_500_000_000 + i * 2_000_000   # 하루 0.02억씩
        _win._on_elo_ready(_OUID, _elo_data(rows))
        step = tv._axis.ticks[1] - tv._axis.ticks[0]
        assert step < 1, f"입력이 넓어 간격이 1억 이상({step}) — 막지 않아도 안 겹쳐 이 검사가 빈다"
        ticks = [tv._axis.tick_text(t) for t in tv._axis.ticks]
        assert len(set(ticks)) == len(ticks), ticks
        _win._on_elo_ready(_OUID, _elo_data(_elo_rows([("2026-09-12T10:00:00", 3000.0, 500, "search")])))
        assert not tv.isVisibleTo(_win) and "모자랍니다" in _win.lb_team_value_note.text()
    finally:
        _win._rank_seasons, _win._elo, config.WEB_DATA = keep
        _win._render_elo()
        _win._go_page("대시보드")


# ── 66단계 조각 ① — 그래프 공통 꼴(G0) · 점수(G2) · 구단가치(G3) ─────────────────────────
def _chart_color_failures(p: dict) -> list:
    """M17 — 새 그래프 토큰은 카드 위 그래픽 대비 3.0 · 같은 그래프에 같이 그려지는 선끼리 색약 6 이상."""
    bad = []
    line = p["CHART_LINE"]
    for name, c in (("CHART_LINE", line), ("CHART_RANK", p["CHART_RANK"]), ("CHART_VALUE", p["CHART_VALUE"]),
                    ("CHART_NEUTRAL", p["CHART_NEUTRAL"])):   # 67 R12 — 무승부·"기타"·경기 수 막대
        if T.contrast(c, p["PANEL"]) < 3.0:
            bad.append(("contrast", name, round(T.contrast(c, p["PANEL"]), 2)))
    score = {"line": line, "rank": p["CHART_RANK"], "cut200": p["CHART_CUT"], "cut1000": p["CHART_NEUTRAL"]}
    keys = list(score)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            d = T.cvd_delta(score[a], score[b])
            if d < 6:
                bad.append(("cvd", a, b, round(d, 1)))
    return bad


def test_chart_tokens_contrast_and_cvd_66():
    bad = {m: _chart_color_failures(p) for m, p in T._PALETTES.items()}
    assert not any(bad.values()), bad
    assert (T.CHART_LINE, T.CHART_RANK, T.CHART_VALUE) == (T._P["CHART_LINE"], T._P["CHART_RANK"], T._P["CHART_VALUE"])
    # 검사가 무언가를 재는지 — 밝은 쪽 순위선을 옛 주황(CHART_CATS[1] · 2.25)으로, 색약 짝이 붙는 색(#b45309 · 컷 5.9)으로
    light = T._PALETTES["light"]
    assert ("contrast", "CHART_RANK", 2.25) in _chart_color_failures(dict(light, CHART_RANK=light["CHART_CATS"][1]))
    assert ("cvd", "rank", "cut200", 5.9) in _chart_color_failures(dict(light, CHART_RANK="#b45309"))
    assert ("contrast", "CHART_NEUTRAL", 2.59) in _chart_color_failures(dict(light, CHART_NEUTRAL="#9aa1b0"))


def test_elo_and_team_value_daily_band_material():
    rows = _elo_rows([("2026-09-11T09:00:00", 3100.0, 4000, "snapshot"),
                      ("2026-09-11T21:00:00", 3150.0, 3900, "search"),
                      ("2026-09-11T15:00:00", 3080.0, 3950, "search"),     # 늦지 않지만 가장 낮다 — 띠 아래 끝
                      ("2026-09-13T08:00:00", 3200.0, 900, "snapshot")])
    pts = app_main.elo_daily_points(rows, datetime(2026, 9, 10))
    assert [(p["elo"], p["lo"], p["hi"], p["n"]) for p in pts] == [(3150.0, 3080.0, 3150.0, 3), (3200.0, 3200.0, 3200.0, 1)], pts
    assert app_main.day_band(3080.0, 3150.0, 3) == (3080.0, 3150.0)
    assert app_main.day_band(3200.0, 3200.0, 1) is None, "점 하나인 날에 띠"
    assert app_main.day_band(3200.0, 3200.0, 2) is None, "폭 0 띠"
    for r, v in zip(rows, (11 * 10 ** 11, 13 * 10 ** 11, 9 * 10 ** 11, 12 * 10 ** 11)):
        r["team_value"] = v
    tv = app_main.team_value_daily(rows, datetime(2026, 9, 10))
    from datetime import date as _d
    assert tv == [(_d(2026, 9, 11), 13 * 10 ** 11, 9 * 10 ** 11, 13 * 10 ** 11, 3),
                  (_d(2026, 9, 13), 12 * 10 ** 11, 12 * 10 ** 11, 12 * 10 ** 11, 1)], tv
    assert app_main.won_text(13_454_400_000) == "134억 5,440만"
    assert app_main.won_text(13_400_000_000) == "134억" and app_main.won_text(54_400_000) == "5,440만"


def test_area_chart_band_legend_headline_66():
    import charts
    pts = [(f"09/0{i}", v, None) for i, v in enumerate((40.0, 50.0, 45.0, 55.0, 60.0), 1)]
    band = [(35.0, 42.0), (45.0, 52.0), None, (50.0, 58.0), None]
    c = _drawn(charts.AreaTrendChart(), pts=pts, avg=False, band=band)
    assert c.marks["band"] == (1, 1), ("이어진 두 날은 면 하나, 혼자인 날은 세로 막대(M10)", c.marks["band"])
    assert "그날 35.0%~42.0%" in c._hits[0][1] and "그날" not in c._hits[2][1], [t for _r, t in c._hits]
    plain = _drawn(charts.AreaTrendChart(), pts=pts, avg=False)
    assert "band" not in plain.marks and plain.grab().toImage() != c.grab().toImage()
    none = _drawn(charts.AreaTrendChart(), pts=pts, avg=False, band=[None] * 5)
    assert none.grab().toImage() == plain.grab().toImage(), "띠가 전부 None 이면 안 넘긴 것과 같아야"
    try:
        _drawn(charts.AreaTrendChart(), pts=pts, band=[None])
        raise AssertionError("길이가 다른 띠를 받았다")
    except ValueError:
        pass
    # 범례·큰 값 — 머리 줄만큼 그래프가 내려가고, 큰 값은 오른쪽 끝
    c = _drawn(charts.AreaTrendChart(), pts=pts, avg=False, legend=[("점수", T.CHART_LINE, False), ("순위", T.CHART_RANK, True)],
               headline="4,222.83")
    assert c.marks["legend"] == ["점수", "순위"] and c.marks["headline"][0] == "4,222.83"
    assert c._hits[0][0].top() > plain._hits[0][0].top(), "머리 줄 자리를 안 비웠다"
    assert abs(c.marks["headline"][1].right() - (c.width() - 4)) < 1, c.marks["headline"]
    # 색 — 기본은 CHART_LINE, color 를 주면 그 색(구단가치)
    img = _drawn(charts.AreaTrendChart(), pts=pts, avg=False, color=T.CHART_VALUE).grab().toImage()
    cols = {img.pixelColor(x, y).name() for x in range(img.width()) for y in range(img.height())}
    assert T.CHART_VALUE in cols and T.CHART_LINE not in cols, "color 가 선·점에 안 닿는다"


def test_season_span_unknown_says_recent_days():
    from datetime import date as _d
    now = datetime(2026, 10, 5, 12, 0)
    assert app_main.season_span([], None, now) == (now - timedelta(days=config.ELO_FALLBACK_DAYS), False)
    assert app_main.season_span([], "2026-09-20T03:00:00", now) == (datetime(2026, 9, 20, 3), True)
    ss = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
    assert app_main.season_span(ss, None, now) == (datetime(2026, 9, 10, config.SEASON_CUTOFF_H), True)
    assert app_main.season_title(datetime(2026, 9, 10), True) == "지금 시즌 (09/10~)"
    assert app_main.season_title(now, False) == f"최근 {config.ELO_FALLBACK_DAYS}일"
    keep = (_win._rank_seasons, dict(_win._elo), config.WEB_DATA)
    try:
        config.WEB_DATA = True
        _win._rank_seasons = []
        _win._on_elo_ready(_OUID, _elo_data(_elo_rows([(datetime.now().isoformat(timespec="seconds"), 3000.0, 5000, "search")])))
        assert _win.gb_elo.title().endswith(f"최근 {config.ELO_FALLBACK_DAYS}일"), _win.gb_elo.title()
        assert _win.gb_team_value.title().endswith(f"최근 {config.ELO_FALLBACK_DAYS}일"), _win.gb_team_value.title()
        assert "시즌" not in _win.gb_elo.title(), "시즌을 모르는데 시즌이라고 불렀다(R2)"
    finally:
        _win._rank_seasons, _win._elo, config.WEB_DATA = keep
        _win._render_elo()


def test_elo_and_team_value_charts_66():
    from datetime import date as _d
    keep = (_win._rank_seasons, dict(_win._elo), config.WEB_DATA)
    try:
        config.WEB_DATA = True
        _win._rank_seasons = [sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
        rows = _elo_rows([("2026-09-11T10:00:00", 3000.0, 900, "search"),
                          ("2026-09-12T08:00:00", 2600.0, 950, "search"),    # 그날 최저 — 마지막 값보다 크게 낮다(M6)
                          ("2026-09-12T20:00:00", 3050.0, 880, "search"),
                          ("2026-09-13T10:00:00", 3100.0, 850, "search")])
        for i, r in enumerate(rows):
            r["team_value"] = 13_000_000_000 + i * 100_000_000
        rows[1]["team_value"] = 12_000_000_000
        _win._on_elo_ready(_OUID, _elo_data(rows))
        _win._go_page("승률 그래프", "점수·예측")
        _app.processEvents()
        ch = _win.elo_chart
        assert ch._band == [None, (2600.0, 3050.0), None], ch._band
        assert ch._axis.lo <= 2600.0, ("축이 띠를 안 덮는다(M6)", ch._axis)
        assert ch._color is None and ch._headline == "3,100.00", (ch._color, ch._headline)
        assert [n for n, _c, _d in ch._legend] == ["점수", "순위"]
        assert _win.lb_elo_band.isVisibleTo(_win) and "좁을 수" in _win.lb_elo_band.text()
        seg = _win.seg_elo
        assert seg.isVisibleTo(_win) and seg.current() == 0
        assert not seg.buttons[1].isEnabled() and "모으지 않습니다" in seg.buttons[1].toolTip(), "시간별을 켤 수 있다(E6)"
        tv = _win.team_value_chart
        assert tv._color == T.CHART_VALUE and tv._band == [None, (120.0, 132.0), None], (tv._color, tv._band)
        assert tv._headline == "133억", tv._headline
        assert _win.lb_team_value_band.isVisibleTo(_win) and not _win.seg_team_value.buttons[1].isEnabled()
        # 띠 날이 없으면 안내도 숨는다
        _win._on_elo_ready(_OUID, _elo_data([r for i, r in enumerate(rows) if i != 1]))
        assert ch._band is None and not _win.lb_elo_band.isVisibleTo(_win)
        assert not _win.lb_team_value_band.isVisibleTo(_win)
    finally:
        _win._rank_seasons, _win._elo, config.WEB_DATA = keep
        _win._render_elo()
        _win._go_page("대시보드")


def test_wdl_fills_visible_95():
    # 95 ④ — 승무패 칸이 카드 위에서 보이는지(그래픽 3.0). 게임 패 #575753 그대로면 어두운 줄 막대 1.99 로 묻혔다(실화면).
    # 줄 목록 꼴(compact)은 어두운 테마만 잰다 — 밝은 테마는 앱에서 고를 수 없다(theme.MODE 고정)
    import widgets
    bad = []
    for mode, p in T._PALETTES.items():
        for k in ("GOOD_FILL", "DRAW_FILL", "LOSE_FILL"):
            if T.contrast(p[k], p["PANEL"]) < 3.0:
                bad.append((mode, k))
    p = T._PALETTES["dark"]
    for k in ("GOOD_FILL", "DRAW_FILL", "LOSE_FILL"):
        if T.contrast(T.blend(p["PANEL"], p[k], widgets.WdlBar.COMPACT_MIX), p["PANEL"]) < 3.0:
            bad.append(("dark compact", k))
    assert not bad, bad


def test_game_colors_95_ink_and_level():
    # 95 — 게임 초록·무 연회색 칸 위 글자는 진하게(흰 글자 고정이면 초록 위 1.3) · 칸 글자는 대비가 큰 쪽
    assert T.ink_on("#07f468") == T.CHART_ON_MARK_DARK and T.ink_on(T._PALETTES["dark"]["DRAW_FILL"]) == T.CHART_ON_MARK_DARK
    assert T.ink_on("#575753") == T.CHART_ON_MARK   # 진한 칸 위는 흰색
    # 결과 점(최근 경기 점) — 패는 빨강(3.7.2) · 무는 연회색 · 글자는 ink_on(그림을 떠서 가운데 색을 본다)
    from PySide6.QtGui import QImage, QPainter
    dots = charts.ResultDots()
    dots.set_data([("승", "a"), ("무", "b"), ("패", "c")])
    dots.resize(200, 40)
    img = dots.grab().toImage()
    d = dots.dot_size()
    step = d + charts.GAP + 2
    y = int((dots.height() - d) / 2 + d * 0.22)   # 점 안 · 글자 위쪽
    seen = [img.pixelColor(int(i * step + 1 + d / 2), y).name() for i in range(3)]
    assert seen[1:] == [T.DRAW_FILL, T.LOSE_FILL], seen
    # 승 점 안쪽(테두리 밖) 글자 픽셀이 진한가 — 흰 글자로 되돌리면 초록 점 안에 어두운 픽셀이 없다
    cx, cy, r = 1 + d / 2, (dots.height() - d) / 2 + d / 2, d * 0.35
    dark = [(x, yy) for x in range(int(cx - r), int(cx + r)) for yy in range(int(cy - r), int(cy + r))
            if (x - cx) ** 2 + (yy - cy) ** 2 <= r * r and img.pixelColor(x, yy).green() < 100]
    assert dark, "승 점 글자가 진하지 않다(초록 위 흰 글자)"
    # 감독 레벨 — 보통 머리에선 LEVEL 색 · 이름은 이스케이프
    card = widgets.RankerCard()
    card.set_mode(False, None)
    card.set_name("a<b", 7)
    t = card._head_name.text()
    assert T.LEVEL in t and "Lv.7" in t and "a&lt;b" in t, t
    card.set_mode(True, None)
    card.set_name("a", 7)
    assert T.LEVEL not in card._head_name.text(), "초록 머리 위에 파랑 레벨"


def test_pitch_field_game_grass_95():
    # 95 ② — 선수 카드 축구장은 게임 잔디 두 색 줄무늬 · 슛 맵 판은 그대로(점이 묻혀서)
    f = widgets._PitchField([])
    f.resize(200, 400)
    img = f.grab().toImage()
    n = T.PITCH_STRIPES
    got = [img.pixelColor(3, int(400 * (i + 0.5) / n)).name() for i in range(n)]
    assert got == [T.PITCH_GRASS[i % 2] for i in range(n)], got
    s = widgets.ShotMapWidget()
    s.resize(200, 300)
    assert s.grab().toImage().pixelColor(3, 150).name() == T.PITCH


def test_wdl_bar_segments_and_text():
    # P2 — 칸 폭은 경기 수 비례 · 0경기 칸은 없음 · 흐리면 세 칸 다 바탕 쪽으로 · 글은 승·패만 색
    import widgets
    b = widgets.WdlBar(1861, 404, 1774)
    segs = b.strip.segments()
    assert [round(w, 3) for w, _ in segs] == [round(n / 4039, 3) for n in (1861, 404, 1774)], segs
    assert [c for _, c in segs] == [T.GOOD_FILL, T.DRAW_FILL, T.LOSE_FILL], segs   # 95 — 게임 승무패 칸
    html = b.label.text()
    assert "1,861승" in html and "404무" in html and "1,774패" in html, html
    assert html.index(T.GOOD) < html.index("1,861승") < html.index(T.LOSE) < html.index("1,774패"), html
    assert b.accessibleName() == "1861승 404무 1774패"
    b.set_counts(3, 0, 1, weak=True)
    segs = b.strip.segments()
    assert [round(w, 2) for w, _ in segs] == [0.75, 0.25], "0경기 칸(무)이 남았다"
    assert [c for _, c in segs] == [T.blend(T.PANEL_2, T.GOOD_FILL, T.WEAK_MIX),
                                    T.blend(T.PANEL_2, T.LOSE_FILL, T.WEAK_MIX)], segs
    b.set_counts(0, 0, 0)
    assert b.strip.segments() == []
    assert b.strip.height() == widgets.WdlBar.HEIGHT == 6


def test_delta_text_arrow_and_color():
    # P3 — ▲▼ 와 색이 부호를 따른다 · 반올림하면 0 인 차이는 흐린 "–"
    import widgets
    assert widgets.delta_text(52.0, 50.0) == ("▲ 2.0%p", T.GOOD)
    assert widgets.delta_text(47.5, 50.0) == ("▼ 2.5%p", T.BAD)
    assert widgets.delta_text(50.04, 50.0) == ("– 0.0%p", T.TEXT_DIM)
    assert widgets.delta_text(50.06, 50.0) == ("▲ 0.1%p", T.GOOD)
    assert widgets.delta_text(10, 7, unit="골", digits=0) == ("▲ 3골", T.GOOD)
    assert widgets.delta_text(7.4, 7, unit="", digits=0)[1] == T.TEXT_DIM   # 0.4 → 정수로 0
    from PySide6.QtWidgets import QTableWidgetItem
    it = widgets.delta_item(QTableWidgetItem("-3.0%p"), -3.0)
    assert it.foreground().color().name() == T.BAD
    assert widgets.delta_item(QTableWidgetItem("+0.0%p"), 0.01).foreground().color().name() == T.TEXT_DIM
    assert widgets.delta_item(None, 1.0) is None


def test_stat_card_sub_and_delta_hide_when_empty():
    # P5 — 보조·변화 줄은 값이 있을 때만 보이고, 변화 색은 delta_text 를 따른다
    import widgets
    c = widgets.StatCard("최고 승률", T.GOOD)
    assert c.sub.isHidden() and c.delta.isHidden()
    c.set_sub("3승 1무 2패")
    c.set_delta(60.0, 50.0, label="평균 대비")
    assert not c.sub.isHidden() and not c.delta.isHidden()
    assert "▲ 10.0%p" in c.delta.text() and "평균 대비" in c.delta.text()
    assert c.delta.property("delta_color") == T.GOOD
    c.set_delta(40.0, 50.0)
    assert c.delta.property("delta_color") == T.BAD
    c.set_delta(None, 50.0)
    c.set_sub("")
    assert c.sub.isHidden() and c.delta.isHidden() and c.delta.property("delta_color") is None


def test_trend_cards_show_delta_against_average():
    # [기간별 성적] 요약 카드 — 최고·최저 카드의 변화 줄이 평균 카드 값과 견준 것인지(배선) · 후보는 MIN_COND 판 이상 기간만
    import widgets
    spec = _days_spec() + [(datetime(2026, 9, 11, 10, 0), "승", True)]   # 9/11 한 판 100% — 표본 미달이라 최고가 아니어야
    with _SynthScope(_synth_matches(spec)):
        _win._period_unit = "일"
        _win._render_period_page()
        periods = _win._period_series("일")
        rated = [p.win_rate for p in periods if not app_main.core.is_weak(p.rated, app_main.core.MIN_COND)]
        assert len(set(rated)) >= 2, ("최고·최저가 같아 방향을 못 잰다", rated)
        assert _win.card_period_min.value.text() == "0.0%" and _win.card_period_max.value.text() == "100.0%"
        wdl = [sum(p.win for p in periods), sum(p.draw for p in periods), sum(p.lose for p in periods)]
        avg = app_main.core.win_pct(*wdl, periods[0].mixed)
        for card, v, col in ((_win.card_period_max, max(rated), T.GOOD), (_win.card_period_min, min(rated), T.BAD)):
            want, got_col = widgets.delta_text(v, avg)
            assert not card.delta.isHidden() and want in card.delta.text(), (card.delta.text(), want)
            assert card.delta.property("delta_color") == got_col == col, (card.cap.text(), got_col)
        assert _win.card_period_games.sub.text() == app_main.wdl_text(*wdl)
        # 표본 미달 하루(1판 100%)를 빼고 최고 — 빼지 않으면 9/11 이 최고라 위 100% 와 구분이 안 되니 최저 쪽으로 잰다
        spec2 = _days_spec()[:100] + [(datetime(2026, 9, 11, 10, 0), "패", True)]   # 전승 5일 + 1판 0% 하루
        _win._matches = _synth_matches(spec2)
        _win._render_period_page()
        assert _win.card_period_min.value.text() == "100.0%", "표본 미달 하루(1판 0%)가 최저 카드에 들어갔다"


def test_tag_kinds():
    # P10 — 종류마다 그 색 글자 + 그 색 10% 바탕 · 모르는 종류는 조용히 회색이 되지 않는다
    import widgets
    want = {"good": T.GOOD, "bad": T.BAD, "warn": T.WARN, "muted": T.TEXT_DIM}   # 표를 베끼지 않고 손으로
    assert set(widgets.Tag.KINDS) == set(want)
    for kind, col in want.items():
        t = widgets.Tag("3연승", kind)
        assert t.color == col and f"color: {col};" in t.styleSheet(), (kind, t.styleSheet())
        assert f"background: {T.blend(T.PANEL, col, 0.10)};" in t.styleSheet()
    try:
        widgets.Tag("x", "green")
    except KeyError:
        pass
    else:
        raise AssertionError("모르는 kind 를 받아 줬다")


def test_dashboard_streak_tag_follows_kind():
    # 연속 카드 — 숫자는 큰 글자에 한 번, "연승"·"연패" 는 태그(색이 종류를 따른다)
    import dashboard
    d = _win.dashboard
    pick = lambda word: next(m for m in _win._matches if word in m.result)  # noqa: E731
    try:
        for ms, big, tag, kind, wdl in (([pick("승")] * 3, "3", "연승", "good", (3, 0, 0)),
                                        ([pick("패")] * 2 + [pick("승")], "2", "연패", "bad", (1, 0, 2)),
                                        ([pick("무"), pick("승")], "1", "무", "muted", (1, 1, 0))):
            d._render_kpis(dashboard.DashboardInput(_win._ouid, ms, [], ms, [], "시험"))
            assert (d.lb_streak.text(), d.tag_streak.text(), d.tag_streak.kind) == (big, tag, kind), \
                (d.lb_streak.text(), d.tag_streak.text(), d.tag_streak.kind)
            assert d.wdl_bar.counts() == wdl, (d.wdl_bar.counts(), wdl)   # 픽스처는 1·1·1 이라 순서를 못 잰다
            assert not d.tag_streak.isHidden()
        d._render_kpis(dashboard.DashboardInput(_win._ouid, [], [], [], [], "시험"))
        assert d.lb_streak.text() == "—" and d.tag_streak.isHidden()
    finally:
        _win._render_dashboard()


def test_match_filter_buttons_use_tag_look():
    # S1 — 켜진 결과 필터는 꽉 칠한 바탕 + 바탕색 글자가 아니라 그 색 글자 + 10% 바탕 + 그 색 테두리 · 꺼짐은 흐린 글자
    for kind, color in app_main.MainWindow.MATCH_FILTER_KINDS:
        qss = _win._match_filter_btns[kind].styleSheet()
        off, on = qss.split("QPushButton:checked")
        assert f"color: {T.TEXT_DIM};" in off, off
        assert f"color: {color};" in on and f"background: {T.blend(T.PANEL, color, 0.10)};" in on, on
        assert f"border-color: {color};" in on and "font-weight: bold" in on, on
        assert f"color: {T.BG}" not in on, "꽉 칠한 바탕 위 글자로 돌아갔다"
        assert on != off


def test_wdl_bars_replace_wdl_text_rows():
    # 경기 결과(전후반·연장·승부차기·몰수)와 승부처 선제골 줄 — 승무패 글 대신 WdlBar, 숫자는 계산 결과 그대로
    import widgets
    rb = app_main.core.result_breakdown(_win._details, _win._ouid)
    _win._render_all()
    got = [(r.win, r.draw, r.lose) for r in _win.result_list.all_rows()]   # 65 M6 — 줄 목록(승무패 막대 꼴 그대로)
    assert got == [tuple(x) for x in (rb.normal, rb.extra, rb.shootout, rb.forfeit)], got
    assert _win.box_result.indexOf(_win.result_list) >= 0
    cs = app_main.core.clutch_summary(_win._details, _win._ouid)
    _win._render_clutch(_win._details, _win._matches)
    got = [w.counts() for w in _win.findChildren(widgets.WdlBar)
           if w.parent() is not None and _win.box_clutch_first.indexOf(w.parent()) >= 0]
    assert got == [tuple(cs.first_scored), tuple(cs.first_conceded)], got


# ── 65 조각 ② — 경기 목록 카드(M2) · 경기 상세 창(M3) ──────────────────────────
def test_match_cards_sort_combo_u1():
    """U1 — 정렬 콤보(최신순 · 점수 차 · 평점) · 고른 값은 settings.ini 에 · 거르기와 같이 돌아도 둘 다 지킨다."""
    lst, cb = _win.match_list, _win.cb_match_sort
    ms = _win._slice()[0]
    try:
        _win._render_matches(ms)
        assert [c.match_id for c in lst.rows()] == [m.match_id for m in ms], "최신순(넥슨 순서)이 아니다"
        cb.setCurrentIndex(cb.findData("diff"))
        diffs = [c.goal_diff for c in lst.rows()]
        assert diffs == sorted(diffs, reverse=True) and len(set(diffs)) > 1, diffs   # 값이 섞여야 정렬을 잰다
        assert str(_win._settings().value("view/match_sort")) == "diff", "고른 정렬을 안 남겼다"
        cb.setCurrentIndex(cb.findData("rating"))
        ratings = [c.rating for c in lst.rows()]
        assert ratings == sorted(ratings, reverse=True) and len(set(ratings)) > 1, ratings
        _win._match_filter_btns["승"].setChecked(False)
        _win._apply_match_filter()
        rows = lst.rows()
        assert rows and all("승" not in c.result for c in rows) and \
            [c.rating for c in rows] == sorted((c.rating for c in rows), reverse=True), rows
        _win._render_matches(ms)          # 다시 그려도 정렬·거르기가 남는다
        assert all("승" not in c.result for c in lst.rows()) and cb.currentData() == "rating"
    finally:
        _win._match_filter_btns["승"].setChecked(True)
        cb.setCurrentIndex(0)
        _win._render_matches(_win._slice()[0])


def test_match_card_pad_icon_only_for_gamepad_u4():
    """U4 — 상대 입력 장치 아이콘은 패드일 때만(원문 matchDetail.controller). 키보드·없음이면 자리도 없다."""
    import dataclasses
    import copy
    d = copy.deepcopy(_DETAILS[0])
    opp = core.match_sides(d, _OUID)[1]
    for ctl, want in (("gamepad", True), ("keyboard", False), (None, False)):
        md = opp.setdefault("matchDetail", {})
        md.pop("controller", None)
        if ctl:
            md["controller"] = ctl
        m = app_main.parse_match(d, _OUID)
        assert m.opp_pad is want, (ctl, m.opp_controller)
        card = _win._match_card(m, None, {})
        segs = _win.match_list.card_delegate.layout(card, QRectF(0, 0, 1000, 120), _win.match_list.font())
        assert ("pad" in {s.role for s in segs}) is want, (ctl, [s.role for s in segs])
    # 툴팁 — 조각마다 옛 표 머리 툴팁과 같은 글
    card = dataclasses.replace(_win._match_card(app_main.parse_match(d, _OUID), None, {}), opp_pad=True,
                               value_text="1.2조", opp_score="4,100 · 7위")
    dlg = _win.match_list.card_delegate
    segs = {s.role: s for s in dlg.layout(card, QRectF(0, 0, 1000, 120), _win.match_list.font())}
    f = _win.match_list.font()
    for role, want in (("value", _win.MATCH_COLUMN_HELP["구단가치"]), ("oppscore", _win.MATCH_COLUMN_HELP["상대 점수"]),
                       ("pad", _win.MATCH_CARD_TIPS["pad"]), ("h2h", _win.MATCH_COLUMN_HELP["맞대결"])):
        got = dlg.tip_at(card, QRectF(0, 0, 1000, 120), f, segs[role].rect.center().toPoint())
        assert got == want, (role, got)


def test_match_card_icons_follow_keys_after_sort():
    """그림은 (종류, 키) 에 붙는다 — 정렬·거르기를 바꿔도 제 카드에 남고, 이미 받은 그림은 다시 받지 않는다."""
    lst = _win.match_list
    built = []
    real = app_main.IconLoader

    class FakeLoader(real):
        def __init__(self, entries, cache_dir):
            super().__init__(entries, cache_dir)
            built.append(entries)
    tmp = pathlib.Path(tempfile.mkdtemp()) / "b.png"
    pm = app_main.QPixmap(8, 8)
    pm.fill(app_main.QColor("red"))
    pm.save(str(tmp), "PNG")
    keep_meta = _win._division_meta
    try:
        app_main.IconLoader = FakeLoader
        # 등급 원본 목록은 테스트가 짓는다 — 디스크 메타 캐시에 기대면 캐시 없는 CI 에서 키가 전부 None 이 된다(fbde918 CI)
        divs = sorted({m.opp_division for m in _win._slice()[0] if m.opp_division is not None})
        _win._division_meta = [{"divisionId": d} for d in divs]
        _win._render_matches(_win._slice()[0])
        keys = {c.division_key for c in lst.all_rows()}
        assert len(keys - {None}) >= 2, ("등급 배지 키가 둘 이상이어야 뜻이 있다", keys)
        assert built and {k for kind, k, _u in built[-1] if kind == "division"} == keys - {None}, built[-1:]
        a = sorted(keys - {None}, key=repr)[0]
        _win._apply_list_icon(lst, "division", a, str(tmp))
        _win.cb_match_sort.setCurrentIndex(_win.cb_match_sort.findData("rating"))
        for c in lst.rows():
            assert (lst.icon("division", c.division_key) is not None) is (c.division_key == a), c
        n = len(built)
        _win._render_matches(_win._slice()[0])
        assert all(k != a for kind, k, _u in (built[-1] if len(built) > n else [])), "이미 붙은 그림을 다시 받는다"
    finally:
        app_main.IconLoader = real
        _win._division_meta = keep_meta
        _win.cb_match_sort.setCurrentIndex(0)
        _win._render_matches(_win._slice()[0])


def test_match_card_double_click_opens_detail_on_opponent_squad():
    """M2 → M3 — 카드 더블클릭은 그 경기 상세 창을 스쿼드 탭의 상대 칸으로(예전 상대 스쿼드 창과 같은 첫 화면)."""
    got = []
    keep = _win._show_match_detail
    _win._show_match_detail = lambda d, tab="요약", side="opp": got.append((d.get("matchId"), tab, side))
    try:
        _win._render_matches(_win._slice()[0])
        lst = _win.match_list
        r = min(2, lst.rowCount() - 1)
        lst.doubleClicked.emit(lst.model().index(r, 0))
        assert got == [(lst.row(r).match_id, "스쿼드", "opp")], got
    finally:
        _win._show_match_detail = keep


def test_match_detail_dialog_m3():
    """M3 — 머리 · 탭 넷 · 축구장 둘을 만들 때 같이(로더 정리 목록에 둘 다) · 요약(경기 정보 줄 = match_versus) ·
    슛 탭 내/상대 전환 · 상대 전적 탭(지금 범위 · 줄은 창을 안 연다) · 닫으면 로더 전부 멈춤. exec 는 모달이라 직접 만든다."""
    d = next(x for x in _DETAILS if app_main.parse_match(x, _OUID) and core.match_goals(x, _OUID))
    m = app_main.parse_match(d, _OUID)
    dlg = _win._make_match_detail(d, tab="스쿼드", side="opp")
    try:
        assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == list(app_main.MatchDetailDialog.TABS)
        assert dlg.tabs.currentIndex() == 1 and dlg.side() == "opp", (dlg.tabs.currentIndex(), dlg.side())
        assert set(dlg.pitches) == {"mine", "opp"}, "축구장 둘을 창을 만들 때 같이 만들어야 한다"
        mine_ids = {p["spId"] for p in core.match_sides(d, _OUID)[0]["player"] if p.get("spPosition") != core.SUB_POSITION}
        assert set(dlg.pitches["mine"].sp_ids()) == mine_ids
        assert len(dlg.loaders) >= 4, dlg.loaders   # 얼굴 · 시즌 아이콘 × 둘(+ 카드 정보 — 관문)
        dlg.side_buttons["mine"].click()
        assert dlg.side() == "mine"
        # 요약 — 경기 정보 줄은 계산 함수 그대로 · 골 글 · MVP
        lines = core.match_versus(d, _OUID)
        assert [b.name.text() for b in dlg.versus] == [ln.name for ln in lines] and len(lines) >= 15, len(lines)
        goals = core.match_goals(d, _OUID)
        assert dlg.timeline.goals == [(g.minute, g.mine) for g in goals]
        assert dlg.goal_text.text().count("'") == len(goals), dlg.goal_text.text()
        assert dlg.mvp.text().startswith("MVP") and "평점" in dlg.mvp.text()
        # 슛 탭 — 내 슛 / 상대 슛
        mine, opp = (core.shot_map([d], _OUID, mine=s).total for s in (True, False))
        assert len(dlg.shot_map._shots) == mine and mine != opp, (mine, opp)   # 둘이 달라야 전환을 잰다
        dlg.cb_shot_side.setCurrentIndex(1)
        assert len(dlg.shot_map._shots) == opp and f"슛 {opp}" in dlg.shot_note.text()
        # 상대 전적 탭 — 지금 범위의 그 상대 경기 · 범위 글
        want = [x.match_id for x in _win._matches if x.opponent == m.opponent]
        assert dlg.h2h_rows == want and m.match_id in want, (dlg.h2h_rows, want)
        assert _win._scope_text() in dlg.h2h_note.text()
        head = dlg.findChild(app_main.QLabel, "detailH2h")
        assert head is not None and "이 시즌 맞대결" in head.text() and "판" in head.text(), head
    finally:
        dlg.deleteLater()
    # 닫으면 로더 전부 멈춤 — 막지 않았으면 로더가 남는다는 것부터(목록이 비면 이 검사가 빈다)
    cancelled = []
    dlg2 = _win._make_match_detail(d)
    assert dlg2.loaders and dlg2.tabs.currentIndex() == 0
    for ld in dlg2.loaders:
        ld.cancel = lambda ld=ld: cancelled.append(ld)
    try:
        keep = _win._make_match_detail
        _win._make_match_detail = lambda *a, **k: dlg2
        try:
            _win._show_match_detail(d)
        except ModalCalled:
            pass   # 스모크는 모달을 막는다 — 그래도 finally 가 정리해야 한다
        finally:
            _win._make_match_detail = keep
        assert len(cancelled) == len(dlg2.loaders), (len(cancelled), len(dlg2.loaders))
    finally:
        dlg2.deleteLater()


def test_match_detail_shots_88():
    """88 P1·P2·P3 — 요약의 xG 흐름 선 끝 = [슛] 탭 xG · 경기 흐름 줄의 슛 점 · [재생] 한 칸씩(타이머 안 기다림) ·
    쪽을 바꾸면 멈추고 전부 · 창을 닫으면 타이머 멈춤 · 슛 맵의 다른 쓰임은 기본값 그대로(전부 보임)."""
    import charts
    d = next(x for x in _DETAILS if app_main.parse_match(x, _OUID) and core.match_goals(x, _OUID)
             and core.shot_map([x], _OUID, mine=True).total and core.shot_map([x], _OUID, mine=False).total)
    shots = core.match_shots(d, _OUID)
    dlg = _win._make_match_detail(d, tab="슛")
    try:
        # P1 — 선 끝 합 = 슛 탭 xG (둘 다 shot_map 과 같은 식) · 0분 0 에서 시작 · 끝은 span 까지 수평
        flow = dlg.xg_flow
        assert isinstance(flow, charts.XgFlowChart)
        flow.resize(600, 160)
        flow.grab()
        want = tuple(core.shot_map([d], _OUID, mine=s).xg for s in (True, False))
        assert want[0] > 0 and want[1] > 0
        assert all(abs(a - b) < 1e-9 for a, b in zip(flow.marks["totals"], want)), (flow.marks, want)
        for mine, total in zip((True, False), want):
            pts = flow.steps(mine)
            assert pts[0] == (0.0, 0.0) and pts[-1] == (float(flow.span), pts[-2][1]) and abs(pts[-1][1] - total) < 1e-9
            assert [v for _m, v in pts] == sorted(v for _m, v in pts), "누적선이 줄었다"
            # 계단 — 슛마다 같은 분에 (앞 값 → 새 값) 두 꼭짓점 · 처음·끝 꼭짓점 둘 더
            assert len(pts) == 2 + 2 * sum(1 for s in shots if s.mine == mine), (mine, pts)
            assert all(pts[i][0] == pts[i + 1][0] for i in range(1, len(pts) - 2, 2)), pts
        assert flow.marks["goals"] == sum(1 for s in shots if s.result == core.SHOT_GOAL)
        # 연장 — 90분 넘는 슛이 있으면 선 끝이 120분까지(픽스처엔 연장 경기가 없어 따로 지은 값으로)
        ext = charts.XgFlowChart()
        ext.set_data([(30.0, True, 0.2, False)])
        assert ext.span == 90 and ext.steps(True)[-1] == (90.0, 0.2)
        ext.set_data([(30.0, True, 0.2, False), (105.0, False, 0.3, True)])
        assert ext.span == 120 and ext.steps(True)[-1] == (120.0, 0.2) and ext.steps(False)[-1] == (120.0, 0.3)
        # P2 — 줄에 골 아닌 슛이 다 실리고, 그림이 슛이 없을 때와 달라진다
        tl = dlg.timeline
        assert len(tl.shots) == sum(1 for s in shots if s.result != core.SHOT_GOAL) and tl.shots, len(tl.shots)
        # 그림 — 같은 골·같은 span 에서 슛 점만 있고 없고(span 이 달라지면 눈금이 달라 점 때문인지 모른다)
        import widgets
        T_shot = type(shots[0])
        fixed = [T_shot(30.0, True, core.SHOT_ON_TARGET, 0.1, 0.9, 0.5, None),
                 T_shot(50.0, False, core.SHOT_OFF_TARGET, 0.1, 0.9, 0.5, None)]
        plain, dotted = widgets.GoalTimeline(), widgets.GoalTimeline()
        for c in (plain, dotted):
            c.set_goals([(10.0, True)])
            c.resize(600, c.HEIGHT)
        dotted.set_shots(fixed)
        assert plain.grab().toImage() != dotted.grab().toImage(), "슛 점이 그려지지 않았다"
        dotted.set_shots([s._replace(result=core.SHOT_GOAL) for s in fixed])   # 골은 set_goals 몫 — 점으로 또 그리지 않는다
        assert dotted.shots == [] and plain.grab().toImage() == dotted.grab().toImage()
        assert tl.extra is core.went_to_extra(d) and tl.span == (120 if tl.extra else 90)
        # 연장 여부를 안 주면(기본) 예전 규칙 — 90분 넘는 점이면 120
        late = [type(shots[0])(100.0, True, core.SHOT_ON_TARGET, 0.1, 0.9, 0.5, None)]
        legacy = widgets.GoalTimeline()
        legacy.set_shots(late)
        assert legacy.extra is None and legacy.span == 120, "90분 넘는 슛이 span 에 안 들어갔다"
        # 후반 추가시간(구간 1 · 47분 = 92') 슛은 연장이 아니다 — 창은 구간 값으로 정한다(실화면 2026-11-09: 정규 경기 28% 가 120분 축)
        import copy
        d2 = copy.deepcopy(d)
        mine_p = next(p for p in d2["matchInfo"] if p.get("ouid") == _OUID)
        mine_p["shootDetail"][0]["goalTime"] = (1 << 24) + 47 * 60
        assert not core.went_to_extra(d2) and any(s.minute > 90 for s in core.match_shots(d2, _OUID)), "92' 슛이 안 생겼다 — 아래 단언이 빈다"
        dlg2 = _win._make_match_detail(d2, tab="요약")
        try:
            assert dlg2.timeline.span == 90 and dlg2.xg_flow.span == 90, "후반 추가시간 슛을 연장으로 그렸다"
            assert all(m <= 90 for m, _v in dlg2.xg_flow.steps(True)), "추가시간 슛이 90분 축 밖에 찍힌다"
        finally:
            _win._close_match_detail(dlg2)
            dlg2.deleteLater()
        mine_p["shootDetail"][0]["goalTime"] = (2 << 24) + 3 * 60   # 연장 전반 93'
        assert core.went_to_extra(d2)
        dlg2 = _win._make_match_detail(d2, tab="요약")
        try:
            assert dlg2.timeline.span == 120 and dlg2.xg_flow.span == 120, "연장 경기를 90분으로 그렸다"
        finally:
            _win._close_match_detail(dlg2)
            dlg2.deleteLater()
        tl.set_shots(shots)
        # P3 — 재생: 한 칸씩 · 순서 = match_shots 시간 순(내 쪽)
        order = [s for s in shots if s.mine]
        assert len(order) >= 2 and dlg.replay_timer is not None and not dlg.replay_timer.isActive()
        dlg.btn_replay.click()
        assert dlg.replay_timer.isActive() and dlg.btn_replay.text() == "멈춤"
        assert dlg.replay_timer.interval() == app_main.config.SHOT_REPLAY_MS
        assert dlg.shot_map.visible_shots() == [] and dlg.shot_map._shots == order
        for k in range(1, len(order)):
            dlg.replay_step()
            assert dlg.shot_map.visible_shots() == order[:k], k
            assert dlg.replay_timer.isActive()
        dlg.replay_step()   # 마지막 — 전부 보이고 멈춘다
        assert not dlg.replay_timer.isActive() and dlg.btn_replay.text() == "재생"
        assert dlg.shot_map.visible_shots() == order and f"슛 {len(order)}" in dlg.shot_note.text()
        # 쪽을 바꾸면 멈추고 전부(그 쪽 슛 수)
        dlg.btn_replay.click()
        dlg.replay_step()
        assert dlg.replay_timer.isActive() and len(dlg.shot_map.visible_shots()) == 1
        dlg.cb_shot_side.setCurrentIndex(1)
        assert not dlg.replay_timer.isActive() and dlg.btn_replay.text() == "재생"
        assert len(dlg.shot_map.visible_shots()) == core.shot_map([d], _OUID, mine=False).total
        # 창을 닫으면(_close_match_detail) 타이머가 멈춘다 — 막지 않았으면 돌고 있다는 것부터
        dlg.btn_replay.click()
        assert dlg.replay_timer.isActive()
        app_main.MainWindow._close_match_detail(dlg)
        assert not dlg.replay_timer.isActive(), "창을 닫았는데 재생 타이머가 돈다"
    finally:
        dlg.deleteLater()
    # ShotMapWidget — set_upto 를 안 부르면 전부(다른 쓰임 셋) · 부르면 앞 n 개 · 새 목록은 전부로 돌아간다
    w = app_main.ShotMapWidget()
    w.set_shots(shots)
    assert w.visible_shots() == shots
    w.set_upto(2)
    assert w.visible_shots() == shots[:2]
    w.set_shots(shots)
    assert w.visible_shots() == shots
    w.set_upto(None)
    assert w.visible_shots() == shots


def test_match_detail_head_counts_only_that_season():
    """M3 머리 "이 시즌 맞대결" — 그 경기가 든 시즌 안 그 상대만. 시즌표가 없으면 저장된 전체(범위 이름도 같이)."""
    keep = (_win._rank_seasons, _win._matches_all)
    old = sn.Season(no=89, name="시즌 8", start=_date(2025, 10, 1), end=_date(2025, 12, 1))
    new = sn.Season(no=90, name="시즌 9", start=_date(2025, 12, 1), end=_date(2026, 2, 1))
    base = _MATCHES[0]

    def g(mid, day, res, opp="같은상대"):
        return models.MatchSummary(**{**base.__dict__, "match_id": mid, "result": res, "opponent": opp,
                                      "match_date": datetime(2025, *day, 12)})
    this = g("a", (12, 10), "승")
    pool = [this, g("b", (12, 20), "패"), g("c", (11, 10), "승"), g("d", (12, 15), "승", "다른상대")]
    try:
        _win._matches_all = pool
        _win._rank_seasons = [new, old]
        text = _win._season_h2h_text(this)
        # 시즌을 안 갈랐다면 지난 시즌 경기까지 3판 — 2판이어야 한다
        assert text == f"이 시즌 맞대결 2판 · {app_main.wdl_text(1, 0, 1)} (2025 시즌 9)", text
        _win._rank_seasons = []
        assert _win._season_h2h_text(this) == f"이 시즌 맞대결 3판 · {app_main.wdl_text(2, 0, 1)} (저장된 전체)"
    finally:
        _win._rank_seasons, _win._matches_all = keep


def test_rate_row_list_65():
    """65 P4 승률 줄 목록 — 정렬 넷(승률 순에선 표본 미달이 아래) · 상대 전적 묶음 셋 · 찾기 · 큰 창~1280 잘림 없음 · 막대 꼴."""
    import dataclasses
    import widgets
    R = widgets.RateRow
    rows = [R("a", 1, 0, 0, rate=100.0, weak=True, recent=0), R("b", 6, 2, 2, rate=60.0, recent=2),
            R("c", 3, 3, 5, rate=27.3, recent=1), R("d", 0, 0, 1, rate=0.0, weak=True, recent=3)]
    order = lambda how: [r.label for r in widgets.sort_rate_rows(rows, how)]  # noqa: E731
    assert order("high") == ["b", "c", "a", "d"], order("high")      # 몇 판짜리 100% 가 맨 위로 안 온다(⑧)
    assert order("low") == ["c", "b", "d", "a"], order("low")        # 몇 판짜리 0% 가 맨 위로 안 온다
    assert order("games") == ["c", "b", "a", "d"], order("games")
    assert order("recent") == ["a", "c", "b", "d"], order("recent")
    # 줄 막대는 WdlBar 의 compact 꼴(E6 — 같은 색 함수) · 흐림은 바탕 쪽으로
    WB = widgets.WdlBar
    assert WB.colors_for(False, True)[0] == T.blend(T.PANEL, T.GOOD_FILL, WB.COMPACT_MIX) != T.GOOD_FILL
    assert WB.colors_for(False) == [T.GOOD_FILL, T.DRAW_FILL, T.LOSE_FILL]   # 95 — 게임 승무패 칸
    assert widgets.WdlBar(1, 0, 1, compact=True).strip.colors() == widgets.WdlBar.colors_for(False, True)
    assert widgets.WdlBar.colors_for(True, True)[0] == T.blend(T.PANEL_2, widgets.WdlBar.colors_for(False, True)[0],
                                                               T.WEAK_MIX)
    col = widgets.RateRowDelegate.colors
    assert col(rows[1])["bar"] == widgets.WdlBar.colors_for(False, True) and col(rows[1])["win"] == T.GOOD
    assert col(rows[0])["bar"] == widgets.WdlBar.colors_for(True, True)
    assert all(col(rows[0])[k] == T.TEXT_DIM for k in ("win", "lose", "rate", "label")), col(rows[0])   # 흐림은 색도
    lst = _win.opp_list
    # 표본 미달 줄은 차이를 들고 와도 안 그리고 "표본 N" 을 붙인다(색만으로 말하지 않게)
    from PySide6.QtCore import QRectF
    w_row = R("w", 1, 0, 0, rate_text="100.0%", rate=100.0, diff=50.0, weak=True, sample="표본 1")
    roles = {s.role: s.text for s in lst.rate_delegate.layout(w_row, QRectF(0, 0, 900, 60), lst.font())}
    assert "diff" not in roles and roles.get("sample") == "표본 1", roles
    cb_g, cb_s = _win.cb_opp_group, _win.cb_opp_sort
    keep_c = dict(_win._team_colors)
    try:
        _win._go_page("상대 전적")
        owners = lst.all_rows()
        assert {r.key for r in owners} == {s.nickname for s in app_main.opponent_stats(_win._matches)}
        assert all(r.icon[0] == "division" for r in owners)
        assert [cb_s.itemData(i) for i in range(cb_s.count())] == ["games", "high", "low", "recent"]
        cb_s.setCurrentIndex(cb_s.findData("recent"))
        assert [r.key for r in lst.rows()] == list(dict.fromkeys(m.opponent for m in _win._matches)), lst.rows()
        # 더블클릭은 목록과 같은 범위(K1 필터를 거친 경기)에서 찾는다 — 범위가 비면 시즌 전체를 뒤지지 않고 안내만
        shown = []
        orig_show, orig_info = _win._show_match_detail, app_main.QMessageBox.information
        keep_picked = _win._opp_picked_details
        _win._show_match_detail = lambda *a, **k: shown.append("창")
        app_main.QMessageBox.information = lambda *a, **k: shown.append("안내")
        try:
            _win._on_opponent_double_clicked(lst.model().index(0, 0))
            _win._opp_picked_details = []
            _win._on_opponent_double_clicked(lst.model().index(0, 0))
            assert shown == ["창", "안내"], shown
        finally:
            _win._show_match_detail, app_main.QMessageBox.information = orig_show, orig_info
            _win._opp_picked_details = keep_picked
        # 상대 포메이션 묶음 — formation_stats 그대로 · 최근 경기 정렬은 없다(그 자리는 경기 수 순으로)
        cb_g.setCurrentIndex(cb_g.findData("formation"))
        f = core.formation_stats(_win._details, _OUID)
        assert sorted(r.key for r in lst.all_rows()) == sorted(x.formation for x in f), lst.all_rows()
        assert "recent" not in [cb_s.itemData(i) for i in range(cb_s.count())] and cb_s.currentData() == "games"
        assert all(lst.icon("formation", r.key) is not None for r in lst.all_rows()), "포메이션 그림을 안 붙였다"
        f0 = next(x for x in f if x.positions)   # 자리 점은 실제로 선 자리 — 이름만으로 그린 그림이 아니다
        assert lst.icon("formation", f0.formation).toImage() == widgets.formation_pixmap(
            f0.formation, positions=f0.positions).toImage()
        # 묶음 줄은 사람이 아니다 — 더블클릭해도 창도 안내도 없다
        shown = []
        orig_show, orig_info = _win._show_match_detail, app_main.QMessageBox.information
        _win._show_match_detail = lambda *a, **k: shown.append("창")
        app_main.QMessageBox.information = lambda *a, **k: shown.append("안내")
        try:
            _win._on_opponent_double_clicked(lst.model().index(0, 0))
            assert shown == [], shown
        finally:
            _win._show_match_detail, app_main.QMessageBox.information = orig_show, orig_info
        # 상대 팀컬러 묶음 — 아는 상대만 · 상대 수
        opps = list(dict.fromkeys(m.opponent for m in _win._matches))
        _win._team_colors.clear()
        _win._team_colors.update({opps[0]: "팀가", opps[1]: "팀가"})
        cb_g.setCurrentIndex(cb_g.findData("color"))
        got = [(r.label, r.games, r.sub) for r in lst.all_rows()]
        n = sum(1 for m in _win._matches if m.opponent in opps[:2])
        assert got == [("팀가", n, "상대 2명")], got
        _win.ed_opponent_filter.setText("없는글자")
        assert lst.rowCount() == 0 and len(lst.all_rows()) == 1
        _win.ed_opponent_filter.setText("가")
        assert lst.rowCount() == 1
        _win.ed_opponent_filter.setText("")
        cb_g.setCurrentIndex(0)
        # 같은 상대를 여러 번 — 최근 순서는 그 상대의 가장 최근 경기(목록 맨 앞에서 처음 나온 자리) · 팀컬러 상대 수는 사람 수
        m0 = _win._matches[0]
        ms = [dataclasses.replace(m0, opponent=n, match_id=f"r{i}") for i, n in enumerate(("가", "나", "가"))]
        rec = {r.key: r.recent for r in _win._opp_owner_rows(ms, 50.0, 3)}
        assert rec == {"가": 0, "나": 1}, rec
        _win._team_colors.update({"가": "팀나", "나": "팀나"})
        got = [(r.label, r.games, r.sub) for r in _win._opp_color_rows(ms)]
        assert got == [("팀나", 3, "상대 2명")], got
        # 큰 창 ~ 1280×720 · 작은 화면에서 이름·숫자가 안 잘린다(보조 글은 잘려도 툴팁). 실데이터 길이로 채운다
        dlg = lst.rate_delegate
        long = dataclasses.replace(lst.all_rows()[0], label="가나다라마바사아자차카타", win=1234, draw=567, lose=1089,
                                   rate_text="49.9%*", diff=-12.3, sub="득 1.23 · 실 2.34 · 최근 2026-10-26 23:59",
                                   icon=("division", 1))
        weak_long = dataclasses.replace(long, weak=True, diff=None, sample="승패 1,234판")
        small = app_main.initial_window(1280, 688).min_size
        try:
            for size in (app_main.MIN_WINDOW, small, (1600, 900), app_main.LARGE_WINDOW):
                _win.setMinimumSize(*small)
                _at_size(*size)
                w = lst.viewport().width()
                assert w > 600, (size, w)
                assert not dlg.overflow(long, w, lst.font()) and not dlg.overflow(weak_long, w, lst.font()), (size, w)
            assert dlg.overflow(long, 300, lst.font()), "좁은 폭에서도 넘침이 0 — overflow 가 아무것도 안 잰다"
        finally:
            _win.setMinimumSize(*app_main.MIN_WINDOW)
            _at_size(1600, 900)
        lst.viewport().grab()   # 대리자 그리기가 예외 없이 도는지(그림 경로)
        assert lst.visualRect(lst.model().index(0, 0)).height() == dlg.row_height(lst.font())
    finally:
        _win.ed_opponent_filter.setText("")
        _win._team_colors.clear()
        _win._team_colors.update(keep_c)
        cb_g.setCurrentIndex(0)
        cb_s.setCurrentIndex(0)


def test_shootout_keeper_cards_65():
    """65 M6 — 승부차기 상대 GK 카드: 많이 만난 순 SHOOTOUT_GK_MAX 장 · 표본 미달 흐림 · 얼굴은 받은 것을 다시 안 받는다."""
    from pathlib import Path
    from PySide6.QtGui import QPixmap

    def so_game(i, gk, res):
        return {"matchId": f"so{i}", "matchInfo": [
            {"ouid": _OUID, "nickname": "나", "matchDetail": {"matchResult": res, "matchEndType": 0},
             "shoot": {"shootOutScore": 4}},
            {"ouid": "x", "nickname": "상대", "matchDetail": {"matchEndType": 0}, "shoot": {"shootOutScore": 3},
             "player": [{"spId": gk, "spPosition": 0}]}]}
    big = core.MIN_COND
    dets = [so_game(f"a{i}", 100, "승" if i % 2 else "패") for i in range(big)] + \
        [so_game(f"b{i}", 101 + i // 2, "승") for i in range(26)]          # 13장 × 2경기
    try:
        _win._render_shootout_keepers(dets)
        cards = _win.shootout_cards
        assert len(cards) == app_main.config.SHOOTOUT_GK_MAX < 14, len(cards)
        assert cards[0].property("sp_id") == 100 and not cards[0].property("weak")
        assert all(c.property("weak") for c in cards[1:]), "2경기 카드가 흐리지 않다"
        assert _win.lb_shootout_note.isHidden(), _win.lb_shootout_note.text()   # 78 ① 카드가 있으면 설명 없음
        assert _win.grid_shootout.count() == len(cards)
        asked = list(_win._gk_face_loader._sp_ids)
        assert asked == [c.property("sp_id") for c in cards], asked
        # 얼굴이 오면 그 카드에 붙고 · 다시 그릴 때 그 얼굴은 다시 안 받는다
        path = Path(tempfile.mkdtemp()) / "f.png"
        pm = QPixmap(10, 10)
        pm.fill()
        pm.save(str(path))
        _win._on_gk_face(100, str(path))
        assert not _win._gk_face_labels[100].pixmap().isNull()
        _win._render_shootout_keepers(dets)
        assert not _win._gk_face_labels[100].pixmap().isNull(), "다시 그렸더니 받은 얼굴이 사라졌다"
        assert 100 not in _win._gk_face_loader._sp_ids, _win._gk_face_loader._sp_ids
        # 1280×720 에서 카드 넷이 가로로 안 넘친다 — 실화면(사본 12장)에서 4px 넘쳐 가로 스크롤이 생겼던 자리. 긴 이름으로
        from PySide6.QtWidgets import QScrollArea
        keep_names = dict(_win._names)
        _win._names.update({sp: "가나다라마바사아자차카타파하" for sp in range(100, 115)})
        try:
            _win._go_page("전술·경기 결과", "경기 결과")
            _win._render_shootout_keepers(dets)
            _at_size(*app_main.MIN_WINDOW)
            areas = [s for s in _win.findChildren(QScrollArea)   # 바깥 페이지 스크롤과 탭 안 스크롤 둘 다
                     if s.isVisible() and s.widget().isAncestorOf(_win.shootout_cards[0])]
            assert len(areas) >= 2, areas
            over = [(s.viewport().width(), s.widget().minimumSizeHint().width()) for s in areas
                    if s.horizontalScrollBar().maximum() > 0]
            assert not over, over
        finally:
            _win._names.clear()
            _win._names.update(keep_names)
            _at_size(1600, 900)
        _win._render_shootout_keepers([])
        assert not _win.shootout_cards and "없습니다" in _win.lb_shootout_note.text() and not _win.lb_shootout_note.isHidden()
    finally:
        _win._gk_faces.pop(100, None)
        _win._render_all()


def test_shotmap_panel_65():
    """65 M8 — 슛 맵 오른쪽: 슛 요약 · 최다 득점/실점(누르면 상세 창 요약 탭) · 슛 타입 · 박스 안/밖 · 골·어시스트 순위."""
    import core_api
    from PySide6.QtCore import QRectF
    from widgets import ScorerRow
    _win._go_page("슛 맵")
    _win.cb_shotmap_side.setCurrentIndex(0)
    details = _win._slice()[1]
    _win._shot_sum_key = None
    _win._render_shotmap()
    # 다시 그리기 사이에 지우기 예약(deleteLater)이 실제로 처리되면 — 칸을 지우며 범위 글까지 지워 두 번째 그리기가
    # "지워진 QLabel" 로 죽었다(실데이터 실화면에서만 — 오프스크린 스모크는 사이에 지우기가 안 돌아 못 봤다)
    from PySide6.QtCore import QEvent
    import shiboken6
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    _win._render_shotmap()
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    ss = core_api.shot_summary(details, _OUID)
    assert ss.games and ss.mine.shots and ss.opp.shots, (ss.games, ss.mine, ss.opp)
    cells = _win.shot_sum_cells
    assert cells["경기당 골"] == f"{ss.mine.goals / ss.games:.2f}" and cells["경기당 실점"] == f"{ss.opp.goals / ss.games:.2f}", cells
    assert cells["결정력"] == f"{ss.mine.goals / ss.mine.shots * 100:.1f}%", cells
    assert cells["기대 골 대비"] == f"{ss.mine.goals - ss.mine.xg:+.1f}", cells
    assert cells["내준 기대 골 대비"] == f"{ss.opp.goals - ss.opp.xg:+.1f}", cells
    assert len(cells) == 8, cells
    # 최다 득점/실점 — 글자는 그 경기 점수 · 누르면 그 경기 상세 창 요약 탭
    best, worst = (app_main.core.parse_match(d, _OUID) for d in (ss.best, ss.worst))
    assert f"{best.my_goals} : {best.opp_goals}" in _win.btn_shot_best.text(), _win.btn_shot_best.text()
    assert f"{worst.my_goals} : {worst.opp_goals}" in _win.btn_shot_worst.text(), _win.btn_shot_worst.text()
    got, keep = [], _win._show_match_detail
    _win._show_match_detail = lambda d, tab="요약", side="opp": got.append((d.get("matchId"), tab))
    try:
        _win.btn_shot_best.click()
        _win.btn_shot_worst.click()
    finally:
        _win._show_match_detail = keep
    assert got == [(ss.best["matchId"], "요약"), (ss.worst["matchId"], "요약")], got
    # 픽스처는 최다 득점·실점이 같은 경기이고 골·실점 수가 같다 — 갈리는 요약을 끼워 칸·버튼 짝을 잰다(같은 구간 키라 캐시를 쓴다)
    fake = core_api.ShotSummary(games=4, mine=core_api.ShotSide(10, 5, 2, 1.0), opp=core_api.ShotSide(8, 4, 6, 3.0),
                                best=ss.best, worst=next(d for d in details if d is not ss.best))
    keep_sum = _win.shot_sum
    _win.shot_sum = fake
    got.clear()
    _win._show_match_detail = lambda d, tab="요약", side="opp": got.append(d.get("matchId"))
    try:
        _win._render_shotmap()
        c = _win.shot_sum_cells
        assert (c["경기당 골"], c["경기당 실점"], c["기대 골 대비"], c["내준 기대 골 대비"]) == ("0.50", "1.50", "+1.0", "+3.0"), c
        assert (c["결정력"], c["상대 결정력"], c["경기당 슛"], c["유효슛 비율"]) == ("20.0%", "75.0%", "2.5", "50%"), c
        _win.btn_shot_best.click()
        _win.btn_shot_worst.click()
        assert got == [fake.best["matchId"], fake.worst["matchId"]] and got[0] != got[1], got
    finally:
        _win._show_match_detail = keep
        _win.shot_sum = keep_sum
        _win._render_shotmap()
    # 순위 — 골 + 어시 많은 순 · 시즌 배지 키 · 강화
    rows = _win.shot_scorers.rows()
    want = ss.scorers[:app_main.config.SHOT_RANK_MAX]
    assert rows and [(r.goals, r.assists, r.grade) for r in rows] == [(s.goals, s.assists, s.grade) for s in want], rows
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))
    assert _win.shot_scorers.icon_entries() == {("season", core_api.season_id_of(s.sp_id)) for s in want} - {("season", None)}
    dg = _win.shot_scorers.scorer_delegate
    long = ScorerRow(10, "가나다라마바사아자차카타파하" * 2, 99, 99, 13, None)
    assert dg.overflow(long, 300, _win.shot_scorers.font()) == [], dg.overflow(long, 300, _win.shot_scorers.font())
    assert "goals" in dg.overflow(long, 60, _win.shot_scorers.font()), "넘침 검사가 아무것도 못 잡는다"
    keep_max = app_main.config.SHOT_RANK_MAX
    app_main.config.SHOT_RANK_MAX = 1
    try:
        _win._render_shotmap()
        assert len(_win.shot_scorers.rows()) == 1 and _win.shot_scorers.height() < 2 * dg.row_height(_win.shot_scorers.font())
    finally:
        app_main.config.SHOT_RANK_MAX = keep_max
        _win._render_shotmap()
    assert len(rows) >= 2, "픽스처 순위가 한 줄뿐이라 위 상한 검사가 뜻이 없다"
    segs = {s.role: s for s in dg.layout(long, QRectF(0, 0, 300, 30), _win.shot_scorers.font())}
    assert segs["grade"].rect.right() <= segs["goals"].rect.left(), "강화 배지가 숫자를 덮는다"
    # 슛 타입·박스 안/밖은 콤보(내/상대)를 따른다 — 색과 줄 · 요약은 다시 세지 않는다(구간이 같으면)
    for mine, tone in ((True, T.GOOD_FILL), (False, T.BAD_FILL)):
        calls = []
        real = core_api.shot_summary
        core_api.shot_summary = lambda *a: calls.append(1) or real(*a)
        try:
            _win.cb_shotmap_side.setCurrentIndex(0 if mine else 1)
            _win._render_shotmap()
        finally:
            core_api.shot_summary = real
        assert calls == [], "콤보를 바꿀 때마다 요약을 다시 센다"
        sm = core_api.shot_map(details, _OUID, mine=mine)
        assert _win.shotmap_types.tone == tone and _win.shotmap_dist.tone == tone
        assert [r[:4] for r in _win.shotmap_types.rows] == \
            [(b.label, b.shots, b.on_target, b.goals) for b in core_api.shot_type_breakdown(sm)]
        assert [r[0] for r in _win.shotmap_dist.rows] == [b.label for b in core_api.shot_distance_breakdown(sm)]
    _win.cb_shotmap_side.setCurrentIndex(0)
    # 1280×720 에서 오른쪽 패널이 가로로 안 넘친다(가로 막대를 끈 대신 내용 최소 폭이 보이는 폭 안이어야)
    try:
        _at_size(*app_main.MIN_WINDOW)
        side = _win.shotmap_side
        assert side.widget().minimumSizeHint().width() <= side.viewport().width(), \
            (side.widget().minimumSizeHint().width(), side.viewport().width())
    finally:
        _at_size(1600, 900)
    # 경기가 없으면 빈 글 · 버튼은 꺼진다
    _win._shot_sum_key = None
    keep_slice = _win._slice
    _win._slice = lambda: ([], [])
    try:
        _win._render_shotmap()
        assert not _win.btn_shot_best.isEnabled() and not _win.shot_scorers.rows() and not _win.shot_sum_cells
    finally:
        _win._slice = keep_slice
        _win._shot_sum_key = None
        _win._render_shotmap()


# 78 ① — 지운 설명 글(ROADMAP "지울 설명 글 50줄")의 특징 문구. 하나라도 창 글자에 다시 보이면 FAIL
EXPLAINER_PHRASES = (
    "※", "표본이 모자란 항목은 표시하지 않습니다", "승·무·패 아닌 결과(오류 등)는 제외", "가로 한 칸이 등급 구간",
    "앱을 쓰기 전 기록은 없습니다", "칸 진하기 =", "판정은 위에서부터", "약 한 달 전까지의 경기만", "50%보다 높으면 초록",
    "골 순서를 되짚은", "머문 날 = 그날 마지막 경기", "운인지 숫자로 가늠", "운과 구분이 안 됩니다", "표마다 따로 판정",
    "넥슨 공식 수치가 아닌 확인값", "흐린 줄은", "위치 기록이 있는 슛만", "흐린 카드는", "비중은 6가지 종류 합",
    "결정력 = 골", "전환율 = 골 ÷ 시도", "칸은 <b>나 / 랭커", "골이 많은 순", "11자리가 채워집니다", "교체 명단 뺌",
    "그 기간의 성적이지 원인이 아닙니다", "'잠깐 바뀜'", "팀컬러 반영", "두 번 누르면 그 팀컬러", "랭킹 페이지 그대로",
    "왼쪽 끝 칸은 일부", "1만 위 안 랭킹 그대로", "구단가치 평균은 툴팁", "하루 걸음", "칸이 안 열린 선수 포함",
    "그 줄에 그 카드를 선발로 쓴 사람 수",
    "선발로 쓴 구단주 — 이 PC", "몰수·오류 뺌", "왼쪽이 오래된", "새로 조회합니다", "구단주 줄을 더블클릭",
    "최근 흐름은 최근", "기준 · 2026", "통산 · 매시각 갱신",
    "더 센 상대 쪽으로 치우쳐", "단계 경계 백분위", "판수를 아는 사람", "짝은 같은 카드를 먼저 산 것부터")
# 지워 없어진 칸 — 다시 만들면 FAIL(이름만 되살린 칸도 잡게)
GONE_NOTE_ATTRS = ("lb_analysis_note", "lb_division_note", "lb_sc_note", "lb_clutch_heat_note", "lb_goalflow_note",
                   "lb_diag_note", "lb_diag_note2", "lb_luck_note", "lb_luck_required", "lb_habits_note", "lb_rewards_note",
                   "lb_pass_note", "lb_shot_sum_scope", "lb_oppcard_note",
                   "lb_poslevel_note", "lb_rboard_note", "lb_ledger_note")   # 뒤 셋은 78 ②(사용자 11-03 "지운다")


def _explainer_hits() -> list:
    """창 안 모든 글자(QLabel·그룹 제목)에서 지운 설명 문구 — 숨은 칸도 센다(글이 남아 있으면 언젠가 뜬다)."""
    from PySide6.QtWidgets import QGroupBox, QLabel
    hits = []
    for w in _win.findChildren(QLabel) + _win.findChildren(QGroupBox):
        text = w.text() if isinstance(w, QLabel) else w.title()
        hits += [(type(w).__name__, p, text[:60]) for p in EXPLAINER_PHRASES if p in text]
    return hits


def test_explainer_notes_gone_78():
    """78 ① — 평소 화면에 설명 글이 없다. 모든 메뉴·탭을 한 번씩 그린 뒤 창 안 글자를 훑는다
    (랭킹 데이터가 다 찬 상태는 test_rank_meta_tabs_draw_and_fit 이 같은 훑기를 한다)."""
    from PySide6.QtWidgets import QGroupBox, QLabel
    # 막지 않았으면 — 같은 훑기가 심은 라벨·그룹 제목을 실제로 잡는지 먼저(판정 루프가 망가지면 여기서 FAIL)
    plant = QLabel("넥슨 데이터 기준 11-02 16:00 · 1만 위 안 랭킹 그대로", _win)
    box = QGroupBox("최근 20경기 (왼쪽이 오래된 경기)", _win)
    try:
        got = _explainer_hits()
        assert any("1만 위 안 랭킹 그대로" in h[2] for h in got) and any(h[0] == "QGroupBox" for h in got), got
    finally:
        for x in (plant, box):
            x.setParent(None)
            x.deleteLater()
    _win._render_all()
    views = [v for vs in app_main.MainWindow.VIEW_OF_KEY.values() for v in vs]
    assert len(views) > 30, len(views)
    for page, tab in views:
        _win._go_page(page, tab) if tab else _win._go_page(page)
    _win._go_page("대시보드")
    hits = _explainer_hits()
    assert not hits, hits
    back = [a for a in GONE_NOTE_ATTRS if hasattr(_win, a)]
    assert not back, back

# ── 79단계(3.3.1 ⑤ 색 규칙): 색마다 % · 좋은 값 초록 / 나쁜 값 빨강 · 근거 막대 승·무·패 ─────────────────────────
import widgets  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
import cardstats as core_cardstats  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402 — 79 검사들과 시즌·열 색 검사가 모듈 이름으로 쓴다


def test_79_share_pcts_sum_to_100():
    import random
    sp = widgets.share_pcts
    assert sp((1, 1, 1)) == [34, 33, 33] and sp((4, 2, 1)) == [57, 29, 14], (sp((1, 1, 1)), sp((4, 2, 1)))
    assert sp((0, 0, 0)) == [0, 0, 0] and sp((3, 0, 0)) == [100, 0, 0] and sp((1, 2)) == [33, 67]
    rnd = random.Random(79)
    for _ in range(500):
        c = [rnd.randint(0, 50) for _ in range(rnd.randint(2, 4))]
        got = sp(c)
        assert sum(got) == (100 if sum(c) else 0), (c, got)
        assert all(abs(g - n * 100 / sum(c)) < 1 for g, n in zip(got, c)) if sum(c) else True, (c, got)


def test_79_multi_color_bars_show_percent_per_color():
    from types import SimpleNamespace
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QLabel
    bar = widgets.WdlBar(4, 2, 1)
    txt = bar.label.text()
    assert "4승 57%" in txt and "2무 29%" in txt and "1패 14%" in txt, txt
    empty = widgets.WdlBar(0, 0, 0)
    assert "%" not in empty.label.text(), "0경기에 0% 를 적었다"
    lst = widgets.RateRowList()
    z = {sg.role: sg.text for sg in lst.rate_delegate.layout(widgets.RateRow("z", win=2, lose=2), QRectF(0, 0, 900, 60),
                                                             lst.font())}
    assert (z["win"], z["draw"], z["lose"]) == ("2승 50%", "0무", "2패 50%"), z
    row = widgets.RateRow("x", win=4, draw=2, lose=1, rate_text="57.1%")
    segs = {sg.role: sg.text for sg in lst.rate_delegate.layout(row, QRectF(0, 0, 900, 60), lst.font())}
    assert (segs["win"], segs["draw"], segs["lose"]) == ("4승 57%", "2무 29%", "1패 14%"), segs
    rows = widgets.ShotTypeRows()
    rows.set_data([SimpleNamespace(label="중거리", shots=10, on_target=6, goals=3)], 1)
    texts = [w.text() for w in rows.findChildren(QLabel)]
    assert "시도 10 · 골 3 · 막힘 3 30% · 빗나감 4 40%" in texts, texts   # 골 몫은 오른쪽 전환율 칸
    assert "30%" in texts, texts
    rows.set_data([SimpleNamespace(label="중거리", shots=3, on_target=2, goals=1)], 5)   # 작은 표본 — 전환율처럼 숨김
    texts = [w.text() for w in rows.findChildren(QLabel) if w.text().startswith("시도")]
    assert texts[-1] == "시도 3 · 골 1 · 막힘 1 · 빗나감 1", texts
    zbar = widgets.WdlBar(3, 0, 1)
    zero = zbar.label.text()
    assert "0무<" in zero and "0무 " not in zero, "0경기 칸에 % 를 붙였다"
    vb = widgets.VersusBar("다음 골", T.OPP_FILL)
    vb.set_values(3, 1, "{:,.0f}", "골", shares=True)
    assert "3골 75%" in vb.nums.text() and "1골 25%" in vb.nums.text(), vb.nums.text()
    vb.set_values(82, 79, "{:.0f}", "%")   # 값 자체가 비율인 줄 — 몫 % 를 안 붙인다(기본 끔)
    assert "51%" not in vb.nums.text() and "49%" not in vb.nums.text(), vb.nums.text()
    # % 가 붙어 길어진 승·무·패 글은 줄바꿈을 허락한다 — 아니면 막대 최소 폭이 글 한 줄 폭이 돼 좁은 칸에서 가로로 넘친다
    assert bar.label.wordWrap(), "WdlBar 글이 줄바꿈을 안 한다"
    long_bar = widgets.WdlBar(1234, 567, 890)
    one_line = long_bar.label.fontMetrics().horizontalAdvance(long_bar.label.text().replace("<", " <"))
    assert long_bar.minimumSizeHint().width() < one_line, (long_bar.minimumSizeHint().width(), one_line)


def test_79_avg_bar_colors_mean_good_and_bad():
    # 보상 탭 평균 막대(피드백 18) — 기준(점선) 이상 = 좋음 초록 · 아래 = 나쁨 빨강. 예전 강조/주의(노랑)는 뜻이 안 읽혔다
    assert T.GOOD_FILL != T.ACCENT_FILL or T.BAD_FILL != T.WARN_FILL, "비교가 비었다 — 옛 색과 새 색이 같다"
    lst = widgets.AvgBarList()
    lst.set_rows([widgets.AvgBar("위", 12.0, "12"), widgets.AvgBar("아래", 8.0, "8")], base=10.0)
    d = lst.bar_delegate
    assert d.colors(widgets.AvgBar("위", 12.0, "12"))["bar"] == T.GOOD_FILL
    assert d.colors(widgets.AvgBar("아래", 8.0, "8"))["bar"] == T.BAD_FILL
    low = widgets.AvgBarList(higher_better=False)   # 판수처럼 적을수록 좋은 줄은 뒤집힌다
    low.set_rows([widgets.AvgBar("위", 12.0, "12")], base=10.0)
    assert low.bar_delegate.colors(widgets.AvgBar("위", 12.0, "12"))["bar"] == T.BAD_FILL
    # 색 뜻은 제목 말풍선에(화면 글로 안 늘린다 — 78)
    assert _win.lb_rboard_tc_title.toolTip() == app_main.MainWindow.RBOARD_COLOR_TIP
    assert "초록" in app_main.MainWindow.RBOARD_COLOR_TIP and "빨강" in app_main.MainWindow.RBOARD_COLOR_TIP


def test_80_detail_side_shows_players_average_rating():
    # 넥슨 averageRating(벤치까지 18 로 나눈 값)이 아니라 뛴 선수 평균 — 선수 평점과 같은 척도
    from PySide6.QtWidgets import QLabel
    side = {"player": [{"status": {"spRating": 6.0 if i < 11 else 0}} for i in range(18)],
            "matchDetail": {"averageRating": 3.667}, "division": 800}
    box = _win._detail_side(side, "상대", False)
    texts = [lb.text() for lb in box.findChildren(QLabel)]
    assert any("평점 6.00" in t for t in texts) and not any("3.67" in t for t in texts), texts


def test_80_fit_pct_axis():
    import charts
    ax = charts.Axis.fit_pct([46.0, 53.0, None])
    assert (ax.lo, ax.hi, ax.ticks) == (40, 55, (40, 45, 50, 55)), ax
    assert ax.tick_text(50) == "50%" and ax.text(53.0) == "53.0%" and ax.name == "승률"
    assert charts.Axis.fit_pct([]) is charts.PCT_AXIS
    assert charts.Axis.fit_pct([None]) is charts.PCT_AXIS
    assert charts.Axis.fit_pct([50.0]).ticks == (45, 50, 55), "값이 전부 50 이면 축이 안 선다"
    assert charts.Axis.fit_pct([0.0, 100.0]).ticks == (0, 25, 50, 75, 100)
    # 전 범위를 훑어 규칙으로 — 50 포함 · 0~100 안 · 값이 다 축 안 · 칸 상한 · 눈금 간격 같음
    for a in range(0, 101, 3):
        for b in range(a, 101, 7):
            ax = charts.Axis.fit_pct([float(a), float(b)])
            assert 50 in ax.ticks and 0 <= ax.lo <= min(a, 50) and max(b, 50) <= ax.hi <= 100, (a, b, ax)
            assert (ax.ticks[0], ax.ticks[-1]) == (ax.lo, ax.hi), (a, b, ax)
            gaps = {y - x for x, y in zip(ax.ticks, ax.ticks[1:])}
            assert len(gaps) == 1 and len(ax.ticks) - 1 <= charts.PCT_FIT_MAX_CELLS, (a, b, ax)


def test_80_period_and_dashboard_use_fitted_axis():
    import charts
    from datetime import date as _d
    series = [models.PeriodStat(label=f"11/0{i}", win=w, draw=0, lose=l, mixed=False, start=_d(2026, 11, i))
              for i, (w, l) in enumerate(((12, 10), (9, 11), (11, 10), (0, 0)), start=1)]
    assert series[-1].rated == 0, "빈 날이 있어야 '빈 날은 축에서 뺀다' 를 잰다"
    want = charts.Axis.fit_pct([p.win_rate for p in series[:3]])
    assert want != charts.PCT_AXIS and want.lo > 0, "값이 좁지 않으면 배선 검사가 빈다"
    keep = _win._period_unit
    _win._period_series = lambda unit: list(series)
    try:
        _win._render_period_page()
        assert _win.period_chart._axis == want, _win.period_chart._axis
        _win._render_dashboard()
        assert _win.dashboard.trend_chart._axis == want, _win.dashboard.trend_chart._axis
    finally:
        del _win._period_series
        _win._period_unit = keep
        _win._render_period_page()
        _win._render_dashboard()


def test_80_count_bars_and_peak_stay_inside_plot():
    # 첫 날이 가장 많은 경기 — 옛 코드는 막대가 축 칸으로 나가고 숫자가 눈금 글자 위에 찍혔다
    import charts
    from PySide6.QtGui import QFontMetrics
    pts = [(f"11/{d:02d}", 50.0 + d, g) for d, g in zip(range(1, 8), (40, 5, 7, 6, 9, 3, 2))]
    c = charts.AreaTrendChart()
    c.set_data(pts, counts=True, baseline=50, avg=False)
    c.resize(420, 240)
    c.grab()
    fm = QFontMetrics(charts._small_font(c.font()))
    ml = max(fm.horizontalAdvance(c._axis.tick_text(t)) for t in c._axis.ticks) + 8
    mr = fm.horizontalAdvance(c._axis.text(c._axis.hi)) // 2 + 10
    rects = c.marks["count_rects"]
    assert len(rects) == 7 and abs(rects[0].left() - ml) < 0.01, (rects[0].left(), ml)
    assert abs(rects[-1].right() - (c.width() - mr)) < 3, (rects[-1], c.width() - mr)
    assert c.marks["count_peak"][0] == 0 and c.marks["count_peak_rect"].left() >= ml, (c.marks["count_peak_rect"], ml)
    plain = charts.AreaTrendChart()
    plain.set_data(pts, baseline=50, avg=False)
    plain.resize(420, 240)
    plain.grab()
    assert "count_rects" not in plain.marks


def test_80_division_steps_have_round_corners():
    import charts
    from PySide6.QtCore import QPointF

    def nearest(path, pt, n=400):
        return min(((path.pointAtPercent(i / n).x() - pt.x()) ** 2 + (path.pointAtPercent(i / n).y() - pt.y()) ** 2) ** 0.5
                   for i in range(n + 1))

    v = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 80), QPointF(200, 80)]
    path = charts._rounded_path(v, 6)
    assert nearest(path, v[0]) < 0.01 and nearest(path, v[-1]) < 0.01, "끝점이 달라졌다"
    assert nearest(path, v[1]) > 1 and nearest(path, v[2]) > 1, "모서리가 그대로다"
    assert nearest(path, QPointF(50, 0)) < 1 and nearest(path, QPointF(100, 40)) < 1, "직선 구간이 어긋났다"
    # 변이 반지름보다 짧으면 절반까지만 — 곡선이 옆 변을 넘어 되돌아가지 않는다
    tight = charts._rounded_path([QPointF(0, 0), QPointF(4, 0), QPointF(4, 3), QPointF(9, 3)], 6)
    xs = [tight.pointAtPercent(i / 200).x() for i in range(201)]
    assert all(a <= b + 1e-6 for a, b in zip(xs, xs[1:])), "좁은 칸에서 곡선이 뒤로 갔다"
    # 배선 — 화면 차트가 실제로 이 경로로 그린다
    from types import SimpleNamespace
    runs = [SimpleNamespace(division=d, start=datetime(2026, 11, i), end=datetime(2026, 11, i), games=3)
            for i, d in enumerate((1100, 900, 800, 900), start=1)]
    c = charts.DivisionRunChart()
    c.set_data(runs, {1100: "A", 900: "B", 800: "C"})
    c.resize(500, 260)
    c.grab()
    verts = c.marks["step_verts"]
    assert len(verts) == 8 and nearest(c.marks["step_path"], verts[1]) > 1, verts


def test_80_avg_bars_diverge_from_base():
    # 피드백 18 — 0 에서 재면 값이 비슷한 줄끼리 막대가 같아 차이가 안 보였다
    from PySide6.QtCore import QRectF
    lst = widgets.AvgBarList()
    lst.resize(600, 200)
    rows = [widgets.AvgBar("큰", 110.0, "110"), widgets.AvgBar("반", 95.0, "95"), widgets.AvgBar("같음", 100.0, "100"),
            widgets.AvgBar("살짝", 100.01, "100"), widgets.AvgBar("없음", None, "—")]
    lst.set_rows(rows, base=100.0)
    d = lst.bar_delegate
    seg = {r.name: {s.role: s.rect for s in d.layout(r, QRectF(0, 0, 600, 30), lst.font())} for r in rows}
    track = seg["큰"]["track"]
    mid = track.center().x()
    assert abs(seg["큰"]["bar"].left() - mid) < 0.01 and abs(seg["큰"]["bar"].right() - track.right()) < 0.01, "가장 큰 차이 = 오른쪽 끝까지"
    assert abs(seg["반"]["bar"].right() - mid) < 0.01, "기준 아래는 왼쪽으로"
    assert abs(seg["반"]["bar"].width() * 2 - seg["큰"]["bar"].width()) < 0.01, "길이가 차이에 비례하지 않는다"
    assert "bar" not in seg["같음"] and "bar" not in seg["없음"]
    assert seg["살짝"]["bar"].width() == d.BAR_MIN_W, "아주 작은 차이도 방향이 보여야"
    same = widgets.AvgBarList()
    same.resize(600, 200)
    same.set_rows([widgets.AvgBar("a", 5.0, "5"), widgets.AvgBar("b", 5.0, "5")])
    assert same.max_dev == 0 and "bar" not in {s.role for s in same.bar_delegate.layout(
        widgets.AvgBar("a", 5.0, "5"), QRectF(0, 0, 600, 30), same.font())}


def test_every_grade_badge_uses_one_painter():
    # 3.7.1 — 강화 배지는 표 칸·골 순위 줄·축구장 카드 모두 넥슨 공지 꼴 하나(paint_grade_badge). 예전 둥근 색 배지로 되돌리면 FAIL
    import widgets
    from PySide6.QtGui import QColor, QFontMetrics, QPixmap
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
    drawn = []
    real = widgets.paint_grade_badge

    def spy(painter, rect, grade, font):
        drawn.append(grade)
        real(painter, rect, grade, font)
    widgets.paint_grade_badge = spy
    try:
        t = QTableWidget(1, 1)
        t.resize(200, 80)
        t.setItemDelegateForColumn(0, widgets.GradeBadgeDelegate(t))
        t.setItem(0, 0, QTableWidgetItem("7"))
        t.show()
        _app.processEvents()
        t.grab()
        assert 7 in drawn, drawn
        drawn.clear()
        lst = widgets.ScorerList()
        lst.resize(400, 120)
        lst.set_rows([widgets.ScorerRow(1, "선수", 3, 1, 12, None)])
        lst.show()
        _app.processEvents()
        lst.grab()
        assert 12 in drawn, drawn
        drawn.clear()
        chip = widgets._PlayerChip(widgets.PitchCard(25, "ST", "선수", None, grade=9), T.CHIP_FW)
        chip.resize(110, 100)
        chip.grab()
        assert 9 in drawn, drawn
        # 랭커 픽 "많이 쓴 강화" 칸 — 배지 자리 폭은 여백까지(줄이면 잘림 검사가 잡는다)
        drawn.clear()
        ml = widgets.MetricRowList()
        ml.resize(500, 200)
        cell = widgets.MetricCell("11", "많이 쓴 강화", None, "", 11)
        ml.set_rows([widgets.MetricRow("선수", cells=(widgets.MetricCell("3명", "사용자"), cell))])
        ml.show()
        _app.processEvents()
        ml.grab()
        assert 11 in drawn, drawn
        dg = ml.metric_delegate
        seg = {s.role: s for s in dg.layout(ml.rows()[0], QRectF(0, 0, 500, 60), ml.font())}["cell1"]
        assert seg.full >= QFontMetrics(ml.font()).horizontalAdvance("11") + 2 * widgets.GRADE_BADGE_PAD, seg.full
        # 선수 카드 창 [시세] 칸 — 강화마다 배지, 기본(0)은 글자
        drawn.clear()
        info = playerinfo.PlayerInfo(sp_id=1, name="가", ovr=100, prices={0: "1 BP", 5: "2 BP", 12: "3 BP"})
        tab = app_main.MainWindow._build_price_tab(info, 5)
        badges = sorted(b.grade for b in tab.findChildren(widgets.GradeBadge))
        assert badges == [5, 12], badges
        tab.resize(400, 300)
        tab.show()
        _app.processEvents()
        tab.grab()
        assert {5, 12} <= set(drawn), drawn
        tab.close()
    finally:
        widgets.paint_grade_badge = real
    # 받은 홀로그램 그림은 11강 이상 배지 모두에 — 축구장 카드에서 확인(10강 이하는 안 쓴다)
    pm = QPixmap(8, 8)
    pm.fill(QColor(0, 200, 0))
    widgets._grade_holo["pm"] = pm
    try:
        for g, green in ((12, True), (10, False)):
            chip = widgets._PlayerChip(widgets.PitchCard(25, "ST", "선수", None, grade=g), T.CHIP_FW)
            chip.resize(110, 100)
            img = chip.grab().toImage()
            top_h = max(16, int(100 * 0.2))
            px = [img.pixelColor(x, y) for y in range(5, 4 + top_h - 3) for x in range(80, 106)]
            hit = any(c.green() > 180 and c.red() < 60 and c.blue() < 60 for c in px)
            assert hit == green, (g, hit)
    finally:
        widgets._grade_holo["pm"] = None


def test_start_grade_holo_sets_shared_picture():
    # 켤 때 한 번 받는 11~13강 바탕 — 받은 그림이 공용 배지까지 와야 한다(강화 비용 [계산] 을 안 눌러도)
    import widgets
    from PySide6.QtGui import QColor, QPixmap
    keep = images.fetch_icon
    asked = []
    with tempfile.TemporaryDirectory() as folder:
        png = pathlib.Path(folder) / "holo.png"
        pm = QPixmap(4, 4)
        pm.fill(QColor(0, 200, 0))
        assert pm.save(str(png))
        images.fetch_icon = lambda url, *a, **k: (asked.append(url), png)[1]
        widgets._grade_holo["pm"] = None
        app_main.IconLoader.start = lambda ld: ld.run()   # 스모크는 QThread.start 를 막는다 — 그 자리에서
        try:
            _win.start_grade_holo()
            _app.processEvents()
            assert asked == [images.GRADE_HOLO_URL] and widgets.grade_holo() is not None, asked
        finally:
            del app_main.IconLoader.start
            images.fetch_icon = keep
            widgets._grade_holo["pm"] = None


class _TcPick:
    """포지션별 최다 상대 › 축구장 — 팀컬러 40개가 든 상태."""
    def __enter__(self):
        self.saved = dict(_win._team_colors), _win.size()
        _win._team_colors.clear()
        _win._team_colors.update({f"상대{i}": f"컬러{i:02d}" for i in range(40)})
        _win.resize(1280, 720)
        _win.show()
        _win._go_page("포지션별 최다 상대", "축구장")
        _win.ed_position_color.setText("")
        _win._refresh_position_color_options()
        _app.processEvents()
        return _win.cb_position_color

    def __exit__(self, *exc):
        _win.cb_position_color.hidePopup()
        _win._team_colors.clear()
        _win._team_colors.update(self.saved[0])
        _win._refresh_position_color_options()
        _win.cb_position_color.setCurrentIndex(0)
        _win.resize(self.saved[1])
        _app.processEvents()


def test_81_team_color_grid_stays_inside_window():
    with _TcPick() as cb:
        p = cb.picker
        assert cb.count() == 42 and not p.isVisible()
        cb.showPopup()
        _app.processEvents()
        assert p.isVisible() and not p.isWindow() and p.window() is _win, "창 밖으로 나갈 수 있는 별도 창이다"
        assert _win.rect().contains(p.geometry()), (p.geometry(), _win.rect())
        assert p.list.count() == 42 and p.list.currentRow() == cb.currentIndex()
        # 가로 4칸 — 앞 네 칸은 같은 줄 · 다섯째는 다음 줄 첫 칸
        r = [p.list.visualItemRect(p.list.item(i)) for i in range(5)]
        assert len({x.top() for x in r[:4]}) == 1 and len({x.left() for x in r[:4]}) == config.TC_GRID_COLS == 4, r
        assert r[4].top() > r[0].top() and r[4].left() == r[0].left(), r
        assert r[3].right() <= p.list.viewport().width(), "넷째 칸이 잘린다"
        cb.hidePopup()
        # 어디에 있든 창 안 — 콤보가 맨 아래 · 오른쪽 끝이면 위로 뜨고 왼쪽으로 밀린다
        real = cb.mapTo
        for at in (QPoint(1100, 680), QPoint(0, 0), QPoint(600, 300)):
            cb.mapTo = lambda _w, _p, a=at: a
            try:
                p.open()
                assert _win.rect().contains(p.geometry()), (at, p.geometry())
                if at.y() == 680:
                    assert p.geometry().bottom() <= at.y(), ("아래 자리가 없는데 위로 안 떴다", p.geometry())
                p.close_popup()
            finally:
                cb.mapTo = real


def test_81_team_color_grid_pick_filter_close():
    from PySide6.QtCore import QEvent as _E, QPointF
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    with _TcPick() as cb:
        p = cb.picker
        cb.showPopup()
        _app.processEvents()
        # 찾기 칸은 펼침 안 — 입력하면 격자가 바로 걸러진다
        assert p.isAncestorOf(_win.ed_position_color)
        _win.ed_position_color.setText("컬러07")
        names = [p.list.item(i).text() for i in range(p.list.count())]
        assert names == [_win.POSITION_COLOR_NONE, _win.POSITION_COLOR_ALL, "컬러07"], names
        # 고르면 콤보 값 · 그 흐름(축구장 다시 그리기)이 돈다 · 닫힌다 · 찾기가 비워진다
        calls = []
        _win._render_position_opponents = lambda d: calls.append(1)
        try:
            p.list.itemClicked.emit(p.list.item(2))
        finally:
            del _win._render_position_opponents
        assert cb.currentText() == "컬러07" and calls == [1] and not p.isVisible(), (cb.currentText(), calls)
        assert _win.ed_position_color.text() == "" and cb.count() == 42, "다음에 열 때 전부 보여야"
        # Enter — 찾는 글자가 든 첫 칸
        cb.showPopup()
        _win.ed_position_color.setText("컬러12")
        _win.ed_position_color.returnPressed.emit()
        assert cb.currentText() == "컬러12" and not p.isVisible()
        # Esc 로 닫힘 — 값은 그대로
        cb.showPopup()
        _app.sendEvent(p.search, QKeyEvent(_E.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert not p.isVisible() and cb.currentText() == "컬러12"

        def press(widget, pos):
            ev = QMouseEvent(_E.Type.MouseButtonPress, QPointF(pos), QPointF(widget.mapToGlobal(pos)), Qt.MouseButton.LeftButton,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            return p.eventFilter(widget, ev)
        # 바깥을 누르면 닫힘 · 안을 누르면 안 닫힘 · 콤보를 다시 누르면 닫기만(먹는다)
        cb.showPopup()
        assert press(p, p.rect().center()) is False and p.isVisible(), "안을 눌렀는데 닫혔다"
        assert press(_win, QPoint(5, 5)) is False and not p.isVisible(), "바깥을 눌렀는데 안 닫혔다"
        cb.showPopup()
        assert press(cb, cb.rect().center()) is True and not p.isVisible()
        # 창 크기가 바뀌면 닫힌다(자리가 어긋난다)
        cb.showPopup()
        p.eventFilter(_win, QEvent(_E.Type.Resize))
        assert not p.isVisible()
        # 연 채 창을 숨기면(트레이) 닫힌다 — 필터가 남으면 다시 열어도 콤보 누르기를 먹는다
        cb.showPopup()
        _win.hide()
        _app.processEvents()
        assert p.isHidden(), "창을 숨겼는데 펼침이 그대로다"
        assert press(cb, cb.rect().center()) is False, "닫힌 뒤에도 필터가 눌림을 먹는다"
        _win.show()
        _app.processEvents()
        assert not p.isVisible()


def test_81_team_color_grid_loads_emblems():
    with _TcPick() as cb:
        p = cb.picker
        started = []
        _win._emblem_map = lambda: {"컬러03": "crests/l1.png"}
        _win._start_list_icons = lambda v: started.append(v)
        try:
            cb.showPopup()
            assert started == [p], "펼칠 때 그림을 안 받는다"
            assert cb.icon_of("컬러03") == ("emblem", "crests/l1.png") and cb.icon_of("컬러04") == ()
            assert p.icon_entries() == {("emblem", "crests/l1.png")}
            row = next(i for i in range(p.list.count()) if p.list.item(i).text() == "컬러03")
            assert p.list.item(row).icon().isNull()
            pm = QPixmap(8, 8)
            pm.fill(QColor(T.ACCENT))
            p.put_icon("emblem", "crests/l1.png", pm)
            assert not p.list.item(row).icon().isNull(), "받은 그림이 칸에 안 붙었다"
            assert p.list.item(row + 1).icon().isNull(), "다른 칸에 붙었다"
            p.fill()                                                  # 찾기로 다시 채워도 안 받고 남는다
            assert not p.list.item(row).icon().isNull()
            p.close_popup()
        finally:
            del _win._emblem_map
            del _win._start_list_icons
    assert isinstance(_win.cb_oppcard_color, widgets.GridPickCombo) and _win.cb_oppcard_color.icon_of == _win._emblem_icon


def test_81_owner_candidates_show_season_icons():
    keep = dict(_win._names)
    started = []
    _win._start_list_icons = lambda v: started.append(v)
    try:
        sp_a, sp_b = 283000111, 500000111
        _win._names.update({sp_a: "테스트선수가", sp_b: "테스트선수가"})
        _win.ed_owner_name.setText("테스트선수")
        cb = _win.cb_owner_card
        assert cb.count() == 2 and started and started[-1] is cb
        ids = {core.season_id_of(sp_a), core.season_id_of(sp_b)}
        assert len(ids) == 2 and cb.icon_entries() == {("season", s) for s in ids}, cb.icon_entries()
        assert {cb.itemData(i) for i in range(2)} == {sp_a, sp_b}
        row = cb.findData(sp_a)
        assert cb.itemIcon(row).isNull()
        pm = QPixmap(8, 8)
        pm.fill(QColor(T.ACCENT))
        cb.put_icon("season", core.season_id_of(sp_a), pm)
        assert not cb.itemIcon(row).isNull() and cb.itemIcon(1 - row).isNull()
        _win.ed_owner_name.setText("테스트선수가")                 # 후보를 다시 채워도 받은 그림은 바로 붙는다
        assert not cb.itemIcon(cb.findData(sp_a)).isNull()
        # 긴 후보가 있어도 옆 이름 칸이 안 눌린다(실제 창에서 글자 두 개 폭이었다) · 한 화면 안에 든다
        long_sp = 283000112
        _win._names[long_sp] = "테스트선수가" + "아주긴이름" * 8
        _win.resize(1280, 720)
        _win.show()
        _win._go_page("선수로 구단주 찾기", None)
        _win.ed_owner_name.setText("테스트선수")
        _app.processEvents()
        ed_w = _win.ed_owner_name.width()
        assert cb.count() == 3 and ed_w >= 100, ed_w   # 한글 이름 몇 글자가 보이는 폭
        assert _win.btn_owner_find.geometry().right() <= _win.btn_owner_find.parentWidget().width(), "찾기 단추가 밖으로"
        del _win._names[long_sp]
        _win.ed_owner_name.setText("")
        assert cb.count() == 0 and cb.icon_entries() == set()
    finally:
        del _win._start_list_icons
        _win._names.clear()
        _win._names.update(keep)
        _win.ed_owner_name.setText("")


def _arrival_details():
    """앞 150판 → 12번 투입(득실 크게 오름 — 범위 밖) → 120판 → 13번 투입 · 1·2번 자리 맞바꿈(득실이 앞 평균과 비슷 — 범위 안) → 120판."""
    def side(ouid, pids, gf):
        return {"ouid": ouid, "nickname": ouid, "shoot": {"goalTotal": gf}, "matchDetail": {"matchResult": "승" if ouid == "me" and gf > 0 else "패"},
                "player": [{"spPosition": 10 + k, "spId": 100000000 + p} for k, p in enumerate(pids)]}
    out = []
    base = list(range(1, 12))
    for i in range(390):
        pids = base if i < 150 else base[:10] + [12] if i < 270 else [2, 1] + base[2:9] + [13, 12]
        gf = (i % 2) if i < 150 else (2 if i < 270 else 1) + (i % 2)
        out.append({"matchId": f"m{i}", "matchDate": f"2026-08-{1 + i // 30:02d}T{i % 24:02d}:{i % 60:02d}:00",
                    "matchInfo": [side("me", pids, gf), side("opp", base, 0)]})
    return out


def test_82_arrivals_tab_shows_record_not_effect():
    keep = _win._details_all, _win._ouid, dict(_win._names), dict(_win._positions)
    try:
        _win._details_all = _arrival_details()
        _win._ouid = "me"
        _win._names.update({100000011: "빠진 선수", 100000012: "새 선수"})
        _win._positions[20] = "ST"
        _win._positions.update({10: "10", 11: "11"})
        _win._go_page("스쿼드·이적", "영입 전후")
        assert _win._page_tabs["스쿼드·이적"].current_name() == "영입 전후", "탭 자리가 없다"
        _win._render_arrivals()
        t = _win.tbl_arrivals
        cols = _win.ARRIVAL_COLUMNS
        assert t.rowCount() == 2 and _win.tbl_moves.rowCount() == 2, (t.rowCount(), _win.tbl_moves.rowCount())
        new_row = next(r for r in range(2) if t.item(r, cols.index("선수")).text() == "새 선수")
        cell = lambda name: t.item(new_row, cols.index(name))   # noqa: E731
        assert cell("선수").text() == "새 선수" and cell("앞 선수").text() == "빠진 선수" and cell("자리").text() == "ST"
        assert cell("판수(앞/뒤)").text() == "150 / 240" and cell("같이 바뀜").text() == "0명"
        assert cell("경기당 득실").text() == "+0.50 → +2.00", cell("경기당 득실").text()
        assert " → " in cell("승률").text()
        d = cell("득실 차이")
        assert d.text() == _win.ARRIVAL_DIFF_OUT and d.font().bold() and d.foreground().color() == QColor(T.TEXT)
        assert "덕분" in d.toolTip() and "득실차" in d.toolTip(), "범위 밖의 뜻 · 기준을 말풍선이 안 말한다"
        # 한 표씩 — 칩으로 바꾼다
        assert not t.isHidden() and _win.tbl_moves.isHidden()
        _win.seg_arrivals.set_current(1, emit=True)
        assert t.isHidden() and not _win.tbl_moves.isHidden()
        _win.seg_arrivals.set_current(0, emit=True)
        assert not t.isHidden() and _win.tbl_moves.isHidden()
        # "효과" 라는 말을 화면에 안 쓴다 — 탭 이름 · 머리글 · 칸 · 말풍선
        words = cols + _win.MOVE_COLUMNS + [_win.ARRIVAL_DIFF_TIP, _win.ARRIVAL_TOGETHER_TIP, _win.ARRIVAL_EMPTY, "영입 전후"]
        assert not [x for x in words if "효과" in x], words
        assert t.horizontalHeaderItem(cols.index("득실 차이")).toolTip() and t.horizontalHeaderItem(cols.index("같이 바뀜")).toolTip()
        assert not _win.lb_arrivals_note.text(), _win.lb_arrivals_note.text()
        # 이동 표 칸
        m = _win.tbl_moves
        mc = _win.MOVE_COLUMNS
        moved = {m.item(r, mc.index("선수")).text(): m.item(r, mc.index("자리")).text() for r in range(2)}
        assert moved == {"100000001": "10 → 11", "100000002": "11 → 10"}, moved   # 앞 자리 → 뒤 자리
        assert all(m.item(r, mc.index("판수(앞/뒤)")).text() == "270 / 120" and m.item(r, mc.index("득실 차이")).toolTip() for r in range(2))
        # 정렬해 둔 채 다시 그려도 꾸밈이 제 줄을 따라간다(PyQt 1 — 정렬을 먼저 켜면 색·굵게가 엉뚱한 줄로 간다)
        first = _win.arrival_report
        diff_col = cols.index("득실 차이")
        got = {t.item(r, cols.index("선수")).text(): t.item(r, diff_col).text() for r in range(2)}
        assert len(set(got.values())) == 2, ("두 줄 판정이 같으면 이 검사가 빈다", got)
        for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
            t.sortByColumn(cols.index("첫 선발"), order)
            _win._render_arrivals()
            assert _win.arrival_report is first, "같은 경기 목록인데 다시 셌다"
            for r in range(2):
                it = t.item(r, diff_col)
                assert it.font().bold() == (it.text() == _win.ARRIVAL_DIFF_OUT), (order, r, it.text())
                assert got[t.item(r, cols.index("선수")).text()] == it.text(), (order, r)
        # 이유 글은 보이는 칩 기준 — 이동만 없으면 그 칩에서만 뜬다
        only = core.arrival_report(_win._details_all, "me")
        only.moves = []
        _win._arrivals_cache = (_win._arrivals_cache[0], only)
        _win._render_arrivals()
        assert not _win.lb_arrivals_note.text()
        _win.seg_arrivals.set_current(1, emit=True)
        assert str(config.ARRIVAL_MIN_GAMES) in _win.lb_arrivals_note.text(), "빈 표인데 이유가 없다"
        _win.seg_arrivals.set_current(0, emit=True)
        assert not _win.lb_arrivals_note.text()
        # 비면 이유 글 · 표 비움
        _win._details_all = []
        _win._render_arrivals()
        assert t.rowCount() == 0 and str(config.ARRIVAL_MIN_GAMES) in _win.lb_arrivals_note.text()
    finally:
        _win._details_all, _win._ouid = keep[0], keep[1]
        _win._names.clear()
        _win._names.update(keep[2])
        _win._positions.clear()
        _win._positions.update(keep[3])
        _win._render_arrivals()


def _c5_game(spids, raw, ouid):
    """raw = 넥슨 원문(UTC) — KST 로 뜻한 시각에서 글자 그대로 9시간 뺀다(63)."""
    me = {"ouid": ouid, "player": [{"spId": s, "spPosition": 1 + i, "spGrade": 5} for i, s in enumerate(spids)],
          "matchDetail": {"matchResult": "승"}}
    return {"matchDate": raw, "matchInfo": [me, {"ouid": "상대", "player": []}]}


def _clear_card_prices():
    c = store.open_db(config.DB_PATH)
    try:
        with c:
            c.execute("DELETE FROM card_price_days")
            c.execute("DELETE FROM api_budget WHERE kind LIKE 'card_backfill%'")
    finally:
        c.close()


def test_68_value_change_card_states_and_numbers():
    """[가계부] 아래 선발 11장 시세 변화(C5) — 내 계정 전 · 남의 계정 · 모으는 중 · 숫자(가격 + 보강 = 총변화) · 색."""
    keep = _win._details_all, _win._ouid, config.WEB_DATA, config.card_backfill_allowed
    c = store.open_db(config.DB_PATH)
    st_keep = store.trade_state(c)
    c.close()
    try:
        config.WEB_DATA = True
        config.card_backfill_allowed = lambda: True
        _win._ouid = "me"
        _win._details_all = [_c5_game(range(1, 12), "2026-11-01T03:00:00", "me"),
                             _c5_game([*range(1, 11), 12], "2026-11-08T03:00:00", "me")]
        _win._go_page("스쿼드·이적", "가계부")
        _win._render_value_change({})
        assert _win.value_card.isVisibleTo(_win) and "내 계정" in _win.lb_value_note.text(), _win.lb_value_note.text()
        assert not _win.box_value.isVisibleTo(_win)
        _win._render_value_change({"my_ouid": "남"})
        assert not _win.value_card.isVisibleTo(_win), "남의 계정을 볼 땐 숨긴다"
        _clear_card_prices()
        c = store.open_db(config.DB_PATH)
        try:
            store.save_card_price_days(c, [(s, 5, "2026-11-01", 100, 1) for s in range(1, 12)])
        finally:
            c.close()
        _win._render_value_change({"my_ouid": "me"})
        assert "모으는 중" in _win.lb_value_note.text() and "1/2" in _win.lb_value_note.text(), _win.lb_value_note.text()
        c = store.open_db(config.DB_PATH)
        try:
            store.save_card_price_days(c, [(s, 5, "2026-11-08", 90, 1) for s in range(1, 11)] + [(12, 5, "2026-11-08", 500, 1)]
                                       + [(1, 5, "2026-11-10", 9, 2)])   # 내 카드와 같은 카드라도 C6 표본 줄은 안 읽는다
        finally:
            c.close()
        _win._render_value_change({"my_ouid": "me"})
        vb = _win.value_breakdown
        assert vb is not None and (vb.day1.isoformat(), vb.day2.isoformat()) == ("2026-11-01", "2026-11-08"), vb
        assert (vb.price, vb.squad, vb.unknown) == (-100, 500 - 100, 0) and vb.total == vb.price + vb.squad, vb
        assert _win.box_value.isVisibleTo(_win) and _win.lb_value_note.isHidden()
        assert T.BAD in _win.lb_value["price"].text() and T.GOOD in _win.lb_value["squad"].text(), "내림 빨강 · 오름 초록"
        assert "구단가치" in _win.value_card.toolTip(), "넥슨 구단가치와 다르다는 말은 말풍선에(화면 글 아님)"
        # 날짜를 바꾸면 DB 없이 다시 센다 — 같은 날 둘이면 변화 0
        _win.cb_value_from.setCurrentIndex(_win.cb_value_from.findData(vb.day2))
        assert _win.value_breakdown.total == 0 and _win.value_breakdown.price == 0
    finally:
        _win._details_all, _win._ouid, config.WEB_DATA, config.card_backfill_allowed = keep
        _clear_card_prices()
        c = store.open_db(config.DB_PATH)
        try:
            store.set_trade_state(c, my_ouid=st_keep.get("my_ouid"))
        finally:
            c.close()


def test_68_price_week_tab_ready_empty_and_colors():
    """[스쿼드·이적 › 주간 시세](C6) — 사유별 빈 화면 · 문턱을 넘으면 막대(오름 초록·내림 빨강)와 표 · 강화 배지 칸."""
    keep = config.WEB_DATA, config.card_backfill_allowed, dict(_win._names)
    today = datetime.now().date()
    try:
        _clear_card_prices()
        _win._go_page("스쿼드·이적", "주간 시세")
        assert _win._page_tabs["스쿼드·이적"].current_name() == "주간 시세", "탭 자리가 없다"
        config.WEB_DATA = False
        _win._render_price_week()
        assert "홈페이지 데이터" in _win.lb_price_week_note.text() and not _win.chart_price_week.bar_rects
        config.WEB_DATA = True
        config.card_backfill_allowed = lambda: False
        _win._render_price_week()
        assert "자동 채우기" in _win.lb_price_week_note.text(), _win.lb_price_week_note.text()
        config.card_backfill_allowed = lambda: True
        c = store.open_db(config.DB_PATH)
        try:
            store.budget_mark_429(c, today.isoformat(), f"{store.BUDGET_CARD}_format")
        finally:
            c.close()
        _win._render_price_week()
        assert "구조가 바뀌어" in _win.lb_price_week_note.text(), _win.lb_price_week_note.text()
        _clear_card_prices()
        need = config.C6_MIN_CARDS
        d0, d1, d2 = (today - timedelta(days=k) for k in (8, 1, 0))
        rows = []
        for s in range(need):   # 어제 +10% · 오늘 −20%(다른 카드)
            day = d1 if s % 2 else d2
            rows += [(1000 + s, 3, (day - timedelta(days=7)).isoformat(), 1000, 2),
                     (1000 + s, 3, day.isoformat(), 1100 if s % 2 else 800, 2)]
        c = store.open_db(config.DB_PATH)
        try:
            store.save_card_price_days(c, rows + [(1, 3, (d2 - timedelta(days=7)).isoformat(), 5, 1),
                                                  (1, 3, d2.isoformat(), 500, 1)])   # 내 카드 줄(+9900%)은 안 읽는다
        finally:
            c.close()
        _win._names.update({1001: "오른 선수", 1000: "내린 선수"})
        _win._render_price_week()
        idx = _win.price_index
        assert idx.ready and idx.recent == need and _win.lb_price_week_note.isHidden(), (idx.recent, _win.lb_price_week_note.text())
        _app.processEvents()
        _win.chart_price_week.repaint()
        colors = [col for _r, col in _win.chart_price_week.bar_rects]
        assert colors == [T.GOOD_FILL, T.BAD_FILL], colors   # 어제 오름 · 오늘 내림
        up_rect, down_rect = (r for r, _c in _win.chart_price_week.bar_rects)
        assert up_rect.bottom() <= down_rect.top() + 0.5, "오름은 0 위 · 내림은 0 아래"
        t = _win.tbl_price_week
        cols = _win.PRICE_WEEK_COLUMNS
        assert t.rowCount() == config.C6_TOP_N and t.item(0, cols.index("변화")).text() == "+10.0%"
        assert t.item(0, cols.index("변화")).foreground().color() == QColor(T.GOOD)
        assert isinstance(t.itemDelegateForColumn(cols.index("강화")), app_main.GradeBadgeDelegate), "강화 칸은 배지"
        _win.seg_price_week.set_current(1, emit=True)
        assert t.item(0, cols.index("변화")).text() == "-20.0%" and t.item(0, cols.index("선수")).text() == "내린 선수"
        assert t.item(0, cols.index("변화")).foreground().color() == QColor(T.BAD)
        assert "누적" in _win.chart_price_week.toolTip(), "누적 지수로 읽지 말라는 말은 말풍선에"
        _win.seg_price_week.set_current(0, emit=True)
    finally:
        config.WEB_DATA, config.card_backfill_allowed, _ = keep
        _win._names.clear()
        _win._names.update(keep[2])
        _clear_card_prices()


def test_68_player_card_columns_and_squad_ovr():
    """저장된 카드 정보 → 선수 지표 [카드] 칩 · 운 범위 카드. 값은 측정과 같은 식(cardstats) · 모르면 빈칸."""
    import json
    c = store.open_db(config.DB_PATH)
    try:
        c.execute("DELETE FROM card_extra")
        players = core.aggregate_players(_win._slice()[1], _win._ouid, name_of=lambda i: f"선수{i}", pos_name=str)
        known = next(p for p in players if p.pos_code in config.SIM_POS_OF and p.grade in config.GRADE_OVR_BONUS)
        spids = core.lineup_cards(_win._details, _win._ouid)
        assert len(spids) >= 11 and known.sp_id in spids, "픽스처에 선발이 없다 — 검사가 빈다"
        # 칸마다 다른 값 — 다 같으면 "가장 많이 선 자리 기준" 을 못 잰다
        grid = {cell: 100 + i for i, cell in enumerate(sorted(set(config.SIM_POS_OF.values())))}
        assert len(set(grid.values())) > 1
        c.executemany("INSERT INTO card_extra (spid, height, weight, body, pos_ovr) VALUES (?, 186, 80, '마름', ?)",
                      [(s, json.dumps(grid)) for s in spids])
        c.commit()
        _win._go_page("선수 지표", "지표")
        _win._render_players(_win._slice()[1])
        t, cols = _win.tbl_players, _win.PLAYER_COLUMNS
        name = _win._names.get(known.sp_id, str(known.sp_id))
        rows = [r for r in range(t.rowCount()) if t.item(r, cols.index("선수")).text() == name]
        assert rows, name
        r = rows[0]
        assert t.item(r, cols.index("실효 OVR")).text() == str(grid[config.SIM_POS_OF[known.pos_code]] + config.GRADE_OVR_BONUS[known.grade]), t.item(r, cols.index("실효 OVR")).text()
        assert t.item(r, cols.index("키")).text() == "186cm" and t.item(r, cols.index("체형")).text() == "마름"
        # 칩 — 평소엔 안 보이고 [카드] 를 고르면 보인다 · 다른 묶음은 숨는다
        i = [n for n, _c in _win.PLAYER_GROUPS].index("카드")
        keep = _win.seg_player_group.current()
        _win.seg_player_group.set_current(i, emit=True)
        assert not t.isColumnHidden(cols.index("실효 OVR")) and t.isColumnHidden(cols.index("골")), "칩이 열을 안 바꾼다"
        _win.seg_player_group.set_current(0, emit=True)
        assert t.isColumnHidden(cols.index("실효 OVR"))
        _win.seg_player_group.set_current(keep, emit=True)
        assert "[정보]" in t.horizontalHeaderItem(cols.index("키")).toolTip()
        # 운 범위 카드 — 값은 측정과 같은 식의 판 평균
        keep_min = config.SQUAD_OVR_MIN_GAMES
        config.SQUAD_OVR_MIN_GAMES = 1            # 픽스처가 몇 판뿐이다 — 문턱 판정은 아래에서 따로
        try:
            _win._render_luck(_win._matches)
            d = _win.squad_ovr_data
            want = []
            ex = {s: {"pos_ovr": json.dumps(grid)} for s in spids}
            for det in _win._details:
                me = next((p for p in det.get("matchInfo") or [] if p.get("ouid") == _win._ouid), None)
                v = core_cardstats.lineup_strength(me, ex)[0] if me else None
                if v is not None:
                    want.append(v)
            assert want and d.known == len(want) and abs(d.avg - sum(want) / len(want)) < 1e-9, (d, want)
            assert _win.card_squad_ovr.value.text() == f"{d.avg:.1f}", _win.card_squad_ovr.value.text()
            assert d.sd is not None and f"변동 ±{d.sd:.2f}" in _win.card_squad_ovr.sub.text(), _win.card_squad_ovr.sub.text()
            # 문턱 — 다 아는 판이 모자라면 값을 안 낸다(기본값이 아닌 값으로 배선을 잰다)
            config.SQUAD_OVR_MIN_GAMES = d.known + 1
            _win._show_squad_ovr()
            assert _win.card_squad_ovr.value.text() == "-" and "아는 판" in _win.card_squad_ovr.sub.text()
            config.SQUAD_OVR_MIN_GAMES = d.known
            _win._show_squad_ovr()
            assert _win.card_squad_ovr.value.text() != "-", "문턱과 같으면 낸다"
        finally:
            config.SQUAD_OVR_MIN_GAMES = keep_min
        # 카드 정보가 없으면 빈칸 · 표는 그대로
        c.execute("DELETE FROM card_extra")
        c.commit()
        _win._render_players(_win._slice()[1])
        r = next(r for r in range(t.rowCount()) if t.item(r, cols.index("선수")).text() == name)
        assert t.item(r, cols.index("실효 OVR")).text() == widgets.NA and t.item(r, cols.index("키")).text() == widgets.NA
        _win._render_luck(_win._matches)
        assert _win.card_squad_ovr.value.text() == "-"
    finally:
        c.execute("DELETE FROM card_extra")
        c.commit()
        c.close()
        _win._render_players(_win._slice()[1])


def test_79_next_goal_is_two_color_bars():
    _win._go_page("승부처 분석", "리드·골 흐름")
    _win._render_goal_flow(_win._details)
    gf = app_main.core.goal_flow(_win._details, _win._ouid)
    want = {k: (mine, total) for k, total, mine in (("trail", gf.trail_goals, gf.trail_mine),
                                                    ("lead", gf.lead_goals, gf.lead_goals - gf.lead_theirs)) if total}
    assert want, "픽스처에 점수 차가 난 상태의 골이 없다 — 이 검사가 비었다"
    assert set(_win.next_goal_bars) == set(want), (set(_win.next_goal_bars), set(want))
    for k, (mine, total) in want.items():
        bar = _win.next_goal_bars[k]
        assert abs(bar.strip.share - mine / total) < 1e-9 and bar.strip.opp_color == T.OPP_FILL, (k, bar.strip.share)
        pm, po = widgets.share_pcts((mine, total - mine))
        assert f"{mine:,}골 {pm}%" in bar.nums.text() and f"{total - mine:,}골 {po}%" in bar.nums.text(), bar.nums.text()
    # 리드별 줄 이름이 안 잘린다 — 고정 132px 은 16px 글씨에서 "최대 1골 리드 (197)" 를 잘랐다(79 실화면)
    from PySide6.QtWidgets import QLabel
    names = [lb for w in _live_widgets(_win.box_goalflow["lead"]) for lb in w.findChildren(QLabel)
             if "리드 (" in lb.text()]
    assert len(names) >= 2, "리드별 줄 이름을 못 찾았다 — 이 검사가 비었다"
    assert all(lb.width() >= lb.fontMetrics().horizontalAdvance(lb.text()) for lb in names), \
        [(lb.text(), lb.width(), lb.fontMetrics().horizontalAdvance(lb.text())) for lb in names]
    assert len({lb.width() for lb in names}) == 1, "줄마다 이름 칸 폭이 달라 막대 시작이 어긋난다"
    # 폭은 글자에서 잰다 — 고정 px 이면 이 픽스처·글꼴에선 우연히 넉넉해도 글씨가 커지면 자른다(글꼴과 무관하게 배선을 잰다)
    longest = max(lb.fontMetrics().horizontalAdvance(lb.text()) for lb in names)
    assert names[0].width() == app_main.MainWindow._same_width(names) == longest + 8, (names[0].width(), longest)
    _win._render_goal_flow([])
    assert _win.next_goal_bars == {}
    _win._render_goal_flow(_win._details)


def test_79_compare_table_marks_worse_side_red():
    import dataclasses
    half = _MATCHES[: max(1, len(_MATCHES) // 2)]
    saved = (_win._fill_compare_squad, _win._compare_last)
    _win._fill_compare_squad = lambda *a: None
    t = _win.tbl_compare
    try:
        # 상대 전패 → 내 승률 칸 초록 · 상대 전승 → 내 승률 칸 빨강(양쪽 갈래를 다 지난다)
        for result, want in (("패", (T.GOOD, T.BAD)), ("승", (T.BAD, T.GOOD))):
            opp = [dataclasses.replace(m, match_id=f"o{i}", result=result) for i, m in enumerate(half)]
            _win._compare_last = ("상대", opp, "opp", [], "")
            _win._rerender_compare()
            rows = {t.item(r, 0).text(): r for r in range(t.rowCount())}
            r = rows["승률"]
            mine, theirs = t.item(r, 1).foreground().color(), t.item(r, 2).foreground().color()
            assert (mine, theirs) == tuple(QColor(c) for c in want), (result, mine.name(), theirs.name())
            colored = 0
            for r in range(t.rowCount()):
                pair = {t.item(r, c).foreground().color().name() for c in (1, 2)}
                if QColor(T.GOOD).name() in pair or QColor(T.BAD).name() in pair:
                    colored += 1
                    assert pair == {QColor(T.GOOD).name(), QColor(T.BAD).name()}, (t.item(r, 0).text(), pair)
            assert colored >= 1
    finally:
        _win._fill_compare_squad, _win._compare_last = saved


def test_79_player_extremes_rule_and_wiring():
    pe = app_main.MainWindow.player_extremes
    assert pe([1, 5, 5, 0], [True, True, True, False]) == ({1, 2}, {0}), "GK·출전 미달(후보 밖)이 최저가 됐다"
    assert pe([3, 3], [True, True]) == (set(), set()) and pe([9, 1], [True, False]) == (set(), set())
    attr = {"기대득점률": "expected_goal_rate", "공격P": "attack_point", "골": "goal", "어시": "assist",
            "패스%": "pass_rate", "드리블%": "dribble_rate", "공중볼%": "aerial_rate", "가로채기": "intercept",
            "태클%": "tackle_rate", "블록%": "block_rate", "평점": "rating"}
    assert set(attr) == set(app_main.MainWindow.PLAYER_EXTREME_COLUMNS)
    assert not {"선방력", "출전", "승률"} & set(app_main.MainWindow.PLAYER_EXTREME_COLUMNS), "U9 — 칠하지 않는 열"
    import core_api
    keep = core_api.MIN_PLAYER_GAMES
    core_api.MIN_PLAYER_GAMES = 1
    try:
        _win._render_players(_win._slice()[1])
        players = app_main.core.aggregate_players(_win._slice()[1], _win._ouid,
                                                  pos_name=lambda p: _win._positions.get(p, str(p)))
        tb = _win.tbl_players
        by_id = {tb.item(r, 1).data(Qt.ItemDataRole.UserRole): r for r in range(tb.rowCount())}
        cand = [p for p in players if app_main.core.position_line(p.pos_code) != "GK"]
        good, bad = QColor(T.GOOD).name(), QColor(T.BAD).name()
        marked = 0
        for name, a in attr.items():
            c = _win.PLAYER_COLUMNS.index(name)
            vals = [getattr(p, a) for p in cand]
            hi, lo = max(vals), min(vals)
            for p in players:
                fg = tb.item(by_id[p.sp_id], c).foreground().color().name()
                is_gk = app_main.core.position_line(p.pos_code) == "GK"
                exp = None if is_gk or hi == lo else good if getattr(p, a) == hi else bad if getattr(p, a) == lo else None
                if exp is None:
                    assert fg not in (good, bad), (name, p.name, fg)
                else:
                    assert fg == exp, (name, p.name, getattr(p, a), hi, lo, fg)
                    marked += 1
        assert marked >= 2 * len(attr) - 2, marked
    finally:
        core_api.MIN_PLAYER_GAMES = keep
        _win._render_players(_win._slice()[1])



import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


class _RecordsState:
    """88 업적 테스트 — 창의 계정·경기·시즌표·점수를 지어낸 것으로 바꿨다가 되돌린다."""
    NAMES = ("_ouid", "_details_all", "_matches_all", "_details", "_matches", "_rank_seasons", "_records_cache",
             "_fun_cache", "_names")

    def __init__(self, details: list, seasons=None):
        self.details, self.seasons = details, seasons

    def __enter__(self):
        self.keep = {n: getattr(_win, n) for n in self.NAMES}
        self.keep_elo = _win._elo.get("me")
        self.keep_show = _win._show_match_detail
        ms = [core.parse_match(d, "me") for d in self.details]
        _win._ouid, _win._details_all, _win._matches_all = "me", self.details, ms
        _win._details, _win._matches = [], []      # 시즌 필터가 비워도 업적은 저장 전체에서
        _win._rank_seasons = list(self.seasons or [])
        _win._records_cache = None
        _win._fun_cache = {}
        _win._elo.pop("me", None)
        return self

    def __exit__(self, *exc):
        for n, v in self.keep.items():
            setattr(_win, n, v)
        if self.keep_elo is None:
            _win._elo.pop("me", None)
        else:
            _win._elo["me"] = self.keep_elo
        _win._show_match_detail = self.keep_show
        for key in ("records", "matches", "dashboard", "opponents"):
            _win._dirty.add(key)
        return False


def test_records_page_tags_and_chips_88():
    """88 C1·U1 — [내 기록] 표 · 줄 더블클릭은 저장 전체에서 경기를 찾는다 · 경기 카드 태그 · 대시보드 칩(7일 안 · 누르면 [내 기록])."""
    from test_records import _d
    now = datetime.now().replace(microsecond=0)
    ds = list(reversed([_d(i, "승", 5, 0, when=now - timedelta(hours=10 - i)) for i in range(6)]))   # 최신순
    with _RecordsState(ds):
        _win._go_page("내 기록")
        assert _win._current_view() == ("내 기록", None), _win._current_view()
        _win._invalidate("records")
        rows = {r.key: (i, r) for i, r in enumerate(_win.record_rows)}
        assert _win.tbl_records.rowCount() == len(_win.record_rows) and "peak" not in rows, \
            "시즌을 모르는데 ②③ 줄이 있다"
        i, big = rows["bigwin"]
        assert big.count == 6 and _win.tbl_records.item(i, 1).text() == "6", big
        assert "6경기" in _win.lb_records_note.text(), _win.lb_records_note.text()
        opened = []
        _win._show_match_detail = lambda d, tab="요약", side="opp": opened.append((d.get("matchId"), tab))
        _win._on_record_double_clicked(_win.tbl_records.item(i, 0))
        assert opened == [("m5", "요약")], f"마지막 경기를 저장 전체에서 못 열었다: {opened}"
        j, never = rows["streak10"]
        _win._on_record_double_clicked(_win.tbl_records.item(j, 0))
        assert len(opened) == 1, "얻지 못한 기록 줄이 경기를 열었다"
        # 경기 카드 태그 — 빠른 범위 목록이어도 저장 전체 업적에서
        _win._render_matches(_win._matches_all)
        card = next(c for c in _win._match_cards if c.match_id == "m2")
        assert card.records[:2] == ("3연승", "5골 이상 넣고 승리"), card.records
        assert next(c for c in _win._match_cards if c.match_id == "m0").records == ("5골 이상 넣고 승리", "내 선수 해트트릭")
        dg = _win.match_list.itemDelegate()
        font = _win.match_list.font()
        for width in (1280 - 220, 420):     # 1280 창의 목록 폭 언저리 · 아주 좁은 폭 — 알약은 빼지 자르지 않는다
            assert "record" not in dg.overflow(card, width, font), width
        narrow = [s for s in dg.layout(card, QRectF(0, 0, 420, dg.row_height(font)), font) if s.role == "record"]
        wide = [s for s in dg.layout(card, QRectF(0, 0, 1060, dg.row_height(font)), font) if s.role == "record"]
        assert len(narrow) < len(wide) == len(card.records), (len(narrow), len(wide))
        assert "3연승" in dg.tip_at(card, QRectF(0, 0, 1060, dg.row_height(font)), font, wide[0].rect.center().toPoint())
        # 대시보드 칩
        _win._render_dashboard()
        chips = _win.dashboard.record_chips
        assert len(chips) == config.RECORD_RECENT_MAX and not _win.dashboard.records_row.isHidden(), chips
        _win._go_page("대시보드")
        # 진짜 마우스로 칩(알약)을 누른다 — 부모 머리 카드도 누를 수 있어서, 클릭이 위로 새면 거기로 다시 간다(88 리뷰)
        from PySide6.QtTest import QTest
        row = _win.dashboard.records_row
        QApplication.processEvents()
        chip = [w for w in row.findChildren(widgets.Tag) if w.isVisibleTo(row)][-1]
        QTest.mouseClick(chip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, chip.rect().center())
        QApplication.processEvents()
        assert _win._current_view() == ("내 기록", None), f"칩을 눌렀는데 {_win._current_view()} 로 갔다"
    old = list(reversed([_d(i, "승", 5, 0, when=now - timedelta(days=config.RECORD_RECENT_DAYS + 2, hours=-i))
                         for i in range(3)]))
    with _RecordsState(old):
        _win._render_dashboard()
        assert _win.dashboard.record_chips == [] and _win.dashboard.records_row.isHidden(), "지난 기록을 칩에 올렸다"


def test_rival_card_and_day_player_88():
    """88 F4 — [상대 전적 › 전적] 라이벌 카드는 저장 전체(시즌 필터가 비어도) · 판수 미달이면 숨김.
    Q2 — 대시보드 카드는 마지막으로 경기한 날 · 그 날짜가 제목 · 누르면 [선수 지표 › 지표] · 계정이 바뀌면 다시 센다."""
    from test_records import _pd
    n = config.RIVAL_MIN_GAMES
    # 정오 기준 — 밤 11시 넘어 돌리면 10분 간격 경기가 자정을 넘어 "마지막으로 경기한 날" 이 하루 밀렸다(11-09 23:4x 실측)
    base = (datetime.now() - timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0)
    ds = []
    for i in range(n):
        d = _pd(i, [(101, 7.0 + i * 0.1, 1, 0, 25), (102, 6.0, 0, 1, 5)], when=base + timedelta(minutes=10 * i))
        d["matchInfo"][1].update({"ouid": "opp", "nickname": "라이벌갑"})
        d["matchInfo"][0].update({"nickname": "나"})
        ds.append(d)
    ds.reverse()   # 최신순
    with _RecordsState(ds):
        _win._names = {101: "앞선수", 102: "뒷선수"}
        _win._go_page("상대 전적")
        _win._invalidate("opponents")
        assert not _win.card_rival.isHidden(), "저장 전체에 라이벌이 있는데 카드가 숨었다(시즌 필터는 비어 있다)"
        assert _win.card_rival.value.text() == "라이벌갑", _win.card_rival.value.text()
        assert f"{n}경기 {n}승 0무 0패" in _win.card_rival.sub.text(), _win.card_rival.sub.text()
        assert f"{n}연승" in _win.card_rival.sub.text()
        assert "&nbsp;:&nbsp;" in _win.card_rival.sub.text() and " : " not in _win.card_rival.sub.text(), \
            "점수가 줄 끝에서 쪼개질 수 있다(150% 실화면 '0 :' / '3')"
        # 88 리뷰 [중] — 카드가 하나 늘어난 위 줄이 1280 에서 창을 밀어내지 않는다(필터 안내 글까지 보인 가장 넓은 꼴)
        try:
            _win.lb_opponent_note.setText("필터: 4-2-3-1 · 최근 100경기 중 37경기")
            _win.lb_opponent_note.show()
            _at_size(*app_main.MIN_WINDOW)
            # 페이지는 세로 스크롤만이라 넘쳐도 창은 1280 그대로 · 막대도 안 뜨고 조용히 잘린다 — 넘친 양(가로 최대값)을 잰다.
            # 리뷰 실측: 지금 0(카드 오른쪽 끝 804px) · 카드 최소 폭을 900 으로 키우면 277
            over = _win.pages.currentWidget().horizontalScrollBar().maximum()
            assert over == 0, f"라이벌 카드가 있는 위 줄이 1280 에서 {over}px 넘친다"
        finally:
            _win.lb_opponent_note.hide()
            _at_size(1600, 900)
        _win._render_dashboard()
        dash = _win.dashboard
        assert dash.lb_day_name.text() == "앞선수", dash.lb_day_name.text()
        assert dash.day_card.title.text() == f"{base:%m-%d}의 선수", dash.day_card.title.text()
        assert f"{n}경기" in dash.lb_day_sub.text() and "평점" in dash.lb_day_sub.text(), dash.lb_day_sub.text()
        _win._go_page("대시보드")
        dash.navigate.emit(dash.day_card.target)
        assert _win._current_view() == ("선수 지표", "지표"), _win._current_view()
    short = ds[:n - 1]
    with _RecordsState(short):
        _win._names = {101: "앞선수", 102: "뒷선수"}
        _win._go_page("상대 전적")
        _win._invalidate("opponents")
        assert _win.card_rival.isHidden(), f"{n - 1}판 상대로 라이벌 카드를 띄웠다"
        _win._render_dashboard()
        assert _win.dashboard.lb_day_name.text() == "앞선수", "목록이 바뀌었는데 옛 계산을 그대로 썼나"
        _win._details_all = []
        _win._matches_all = []
        _win._render_dashboard()
        assert _win.dashboard.lb_day_name.text() == "—" and _win.dashboard.day_card.title.text() == "오늘의 선수", \
            "경기가 없는데 옛 선수가 남았다(캐시 키가 목록을 안 따라간다)"
    # 88 리뷰 [중] — 다음 날 한 판만 했으면 카드를 비우지 않고 앞날 선수를 낸다
    lone = _pd(99, [(102, 9.5, 0, 0, 25)], when=base + timedelta(days=1))
    with _RecordsState([lone] + ds):
        _win._names = {101: "앞선수", 102: "뒷선수"}
        _win._render_dashboard()
        dash = _win.dashboard
        assert dash.lb_day_name.text() == "앞선수" and dash.day_card.title.text() == f"{base:%m-%d}의 선수", \
            (dash.lb_day_name.text(), dash.day_card.title.text(), "마지막 날 한 판이라 카드가 비었다")


def test_records_cache_follows_elo_replace():
    """88 2회차 [상] — 같은 계정 점수를 같은 길이 새 묶음으로 다시 받아도 ② 를 다시 센다(키에 점수 묶음 id)."""
    from datetime import date as _date
    from seasons import Season
    season = Season(1, "시즌 1", _date(2026, 10, 1), _date(2026, 10, 20))

    def series(req, vals):
        return app_main.EloSeries(req=req, rows=[{"taken_at": f"2026-10-0{k + 2}T12:00:00", "elo": v}
                                                 for k, v in enumerate(vals)], cuts={})
    with _RecordsState([], seasons=[season]):
        _win._elo["me"] = series(1, [1000.0, 1100.0])
        assert [a.key for a in _win._records()] == ["peak"]
        _win._elo["me"] = series(2, [1000.0, 900.0])           # 길이 같음 · 갱신 없음
        assert [a.key for a in _win._records()] == [], "점수를 다시 받았는데 옛 업적을 그대로 보였다"
        # 점수가 도착하면(elo_ready) 보고 있던 [내 기록] 이 다시 그려진다
        _win._go_page("내 기록")
        _win._invalidate("records")
        peak_row = lambda: next(r for r in _win.record_rows if r.key == "peak")   # noqa: E731
        assert peak_row().count == 0
        _win._elo_req["me"] = 7
        _win._on_elo_ready("me", series(7, [1000.0, 1100.0, 1200.0]))
        assert peak_row().count == 2, "점수가 왔는데 [내 기록] 을 다시 안 그렸다"
        _win._rank_seasons = []                                  # 시즌표만 바뀌어도(없어지면) ②③ 을 안 센다 — 점수는 그대로
        assert _win._records() == [] and "peak" not in {r.key for r in core.record_summary([], seasons_known=_win._records_known())}
        _win._invalidate("records")
        assert "peak" not in {r.key for r in _win.record_rows}, "시즌을 모르는데 ② 줄을 보였다"
        _win._on_seasons_loaded([season])                        # 시즌표가 도착하면 보고 있던 [내 기록] 이 다시 그려진다
        assert peak_row().count == 2, "시즌표가 왔는데 [내 기록] 을 다시 안 그렸다"


def _flush_deleted():
    """테스트 사이마다 deleteLater 한 객체를 실제로 지운다 — 실제 앱은 이벤트 루프가 이걸 한다.
    안 하면 쌓인 위젯을 스타일을 다시 입히는 테스트(_setup_app → T.apply)가 전부 다시 꾸며, 혼자 1.6초인 테스트가
    전체 실행에선 21초였다(85단계 실측 — 전체 2분 15초 → 1분 13초)."""
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed, started = 0, time.monotonic()
    for t in tests:
        try:
            with watchdog.limit(t.__name__):
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
        _flush_deleted()
    _shots()
    print(f"\n{len(tests) - failed}/{len(tests)} 통과 · {time.monotonic() - started:.0f}초")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
