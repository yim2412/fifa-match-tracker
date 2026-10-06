"""화면 스모크 — offscreen 으로 메인 창을 띄워 메뉴·페이지 배선을 확인한다.

네트워크 없이 돈다: QThread.start 를 막아 이미지·팀컬러 등 백그라운드 조회가
안 나가게 하고, 픽스처 4경기를 _on_loaded 에 직접 넣는다.
`python tests/test_ui_smoke.py` 로 실행. UI_SHOT=<폴더> 를 주면 메뉴마다
화면을 PNG 로 떠 둔다(눈으로 확인용 — 커밋하지 않는다).
"""
from __future__ import annotations

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

from PyQt6.QtCore import QEvent, QThread, QTimer, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QThread.start = lambda self, *a, **k: None  # 백그라운드 조회 차단

import app_main  # noqa: E402
import config  # noqa: E402
import core_api as core  # noqa: E402
import crashlog  # noqa: E402
import models  # noqa: E402
import nexon_api  # noqa: E402
import notice  # noqa: E402
import playerinfo  # noqa: E402
import rankcollect  # noqa: E402
import ranker  # noqa: E402
import requests  # noqa: E402
import seasons as sn  # noqa: E402
import store  # noqa: E402
import stats as st_mod  # noqa: E402
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


from PyQt6.QtWidgets import QDialog, QMessageBox  # noqa: E402

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
# 한 번만 실행(tray.SingleInstance) — main() 을 부르는 테스트가 이 PC 에 떠 있는 앱을 앞으로 부르거나 그 때문에 끝나지 않게
config.SINGLE_INSTANCE = False
_seed = store.open_db(config.DB_PATH)
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


def test_loaded_opens_dashboard():
    assert _AFTER_LOAD == (_win.PAGE_MAIN, _win._page_index["대시보드"]), _AFTER_LOAD
    # 픽스처: 승1 무1 패1 + 오류1 — 대시보드 지표가 실제로 채워졌는지
    assert _win.dashboard.lb_rate.text() == "33.3%", _win.dashboard.lb_rate.text()
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
    _win._go_page("승률 그래프")
    assert _win.card_trend_games.value.text().endswith("경기"), \
        _win.card_trend_games.value.text()
    assert _win.dashboard.kpi_rate.title.text() == "승률", _win.dashboard.kpi_rate.title.text()


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
        _win._go_page("팀컬러 승률")               # 같은 그리기를 쓰는 메뉴는 한 번이면 같이 깨끗
        _win._go_page("팀컬러 랭킹")
        _win._go_page("포지션별 최다 상대")
        assert c.n["_render_teamcolor_tabs"] == 1, c.n
        _win._go_page("대시보드")
        _win._invalidate("teamcolor")              # 안 보이면 미뤘다가
        assert c.n["_render_teamcolor_tabs"] == 1, c.n
        _win._go_page("팀컬러 랭킹")
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
            assert _win._current_view() == ("슛 맵", None), (args, _win._current_view())
    finally:
        config.HIDDEN_NAV_UNTIL_READY = keep
    _win._go_page("랭킹 추이")
    assert _win._current_view() == ("랭킹 추이", None), "숨김을 풀면 열린다"
    assert config.HIDDEN_NAV_UNTIL_READY == (), "2.1.1 공개판엔 숨긴 메뉴가 없다(release.py 도 막는다)"
    shown = [_win.nav.item(r) for r in range(_win.nav.count())
             if _win.nav.item(r).text() in ("랭커", "랭킹 추이", "랭커 픽", "선수로 구단주 찾기")]
    assert len(shown) == 4 and not any(it.isHidden() for it in shown), "17단계로 랭커 묶음이 다 보인다"
    _win._go_page("대시보드")


def test_page_tabs_widget():
    """PageTabs — 지금 탭만 보인다 · 바뀔 때만 신호 · 없는 탭은 거절 · 숨은 탭의 넓은 내용이 최소 폭을 안 넓힌다 ·
    좁으면 탭 글자를 줄이지 않고 탭줄이 접힌다."""
    import widgets
    from PyQt6.QtWidgets import QLabel
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

    # 경기를 빼고 그리면 '최근 N일' 칸의 범위가 줄어든 채 남는다 — 뒤 테스트(승률 평균)가 달라졌다
    saved = _win._matches, _win._details, _win.sp_trend_days.value()
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
        _win._render_trend(_win._matches)  # 범위를 먼저 되살려야 값이 들어간다
        _win.sp_trend_days.setValue(saved[2])
        _win._render_all()


def test_setfont_sizes_survive_stylesheet():
    # QSS 의 QWidget 규칙에 font-size 가 있으면 코드의 setFont 가 전부 눌린다
    # (다크 테마 시절 실제로 그랬다 — 30pt 제목이 15px 로 보였다).
    from PyQt6.QtWidgets import QLabel
    title = [lb for lb in _win.stack.widget(_win.PAGE_SEARCH).findChildren(QLabel)
             if lb.objectName() == "searchTitle"]
    assert title, "검색 화면 제목을 못 찾음"
    f = title[0].font()
    assert f.pointSize() == 30, (f.pointSize(), f.pixelSize())


def test_player_table_cells_not_elided():
    # 열 폭이 Qt 가 계산한 필요 폭보다 좁으면 "33.3" 이 "3…" 로 잘린다
    # (상수 여백을 42→24 로 줄였을 때 실제로 그랬다).
    _win._go_page("선수 지표")
    _app.processEvents()
    tb = _win.tbl_players
    short = [(tb.horizontalHeaderItem(c).text(), tb.columnWidth(c), tb.sizeHintForColumn(c))
             for c in range(tb.columnCount())
             if tb.columnWidth(c) < tb.sizeHintForColumn(c)]
    assert not short, short


def _at_size(w, h):
    _win.resize(w, h)
    _app.processEvents()
    _app.processEvents()


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


def test_top_bar_wraps_only_when_narrow():
    try:
        _at_size(*app_main.MIN_WINDOW)
        assert _win.top_bar.is_two_rows()
        _at_size(1920, 1000)
        assert not _win.top_bar.is_two_rows()
    finally:
        _at_size(1600, 900)


def test_season_combo_never_narrower_than_longest_item():
    from PyQt6.QtGui import QFontMetrics
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
    lb = widgets.FitLabel("906승 393무 903패 (41.1%)", base_pt=30, min_pt=14)
    lb.resize(329, 60)  # 실데이터에서 잘렸던 칸 폭
    lb.show()  # 숨은 위젯은 resizeEvent 가 show 때까지 미뤄진다
    _app.processEvents()
    from PyQt6.QtGui import QFontMetrics
    full = QFontMetrics(lb._font_at(30)).horizontalAdvance(lb.text())
    assert full > 329, full  # 30pt 그대로였으면 잘렸다
    assert lb.font().pointSize() < 30, lb.font().pointSize()
    assert QFontMetrics(lb.font()).horizontalAdvance(lb.text()) <= 329


def test_no_table_elides_at_min_or_default_size():
    # 예전 기본 표는 둘째 열부터 균등 분할이라 '선수 B'·'기간'·(1280 폭에서) '스코어' 가
    # "…" 로 잘렸다. 헤더도 잰다 — 채우는 동안 정렬이 꺼져 있어 화살표 자리를 빼고
    # 폭을 잡으면 "승률▾" 이 겹쳤다.
    import widgets
    # 메인 창의 표만 — 대화상자 안의 표(포지션 선수 · 선수 카드 [랭커 기록])는 열 때 생기고 앞 테스트가 열어 둔다
    tables = [t for t in _win.findChildren(widgets.FitTableWidget) if t.window() is _win]
    assert len(tables) == 19, len(tables)  # 13번째는 랭커와 비교(1.4.1) · 14~17 랭커 픽 셋 · 구단주 찾기 · 18~19 추천 둘(2.1.1)
    # 작은 화면(FHD 150% 등)의 최소 크기 — 폭 1264(화면 폭 − 테두리) · 낮춘 높이. 높이가 낮아 페이지에 세로 막대가
    # 생기고 그 폭만큼 가로가 준다(1.0.3).
    small = app_main.initial_window(1280, 688).min_size
    try:
        for size in (app_main.MIN_WINDOW, small, (1600, 900)):
            _win.setMinimumSize(*small)
            _at_size(*size)
            assert (_win.width(), _win.height()) == tuple(size), ("그 크기로 못 줄였다", size, _win.size())
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
                    w = tb.columnWidth(c)
                    head = arrow[c] if c == sort_col else bare[c]
                    if w < tb.sizeHintForColumn(c) or w < head:
                        bad.append((page, tb.horizontalHeaderItem(c).text(), w,
                                    tb.sizeHintForColumn(c), head))
            assert not bad, (size, bad[:5])
    finally:
        _win.setMinimumSize(*app_main.MIN_WINDOW)
        _at_size(1600, 900)


def test_measure_text_same_as_measuring_everything():
    # 긴 글자부터 재다 멈추는 방식(2026-10-02)이 전부 재는 것과 같은 폭을 내야 한다 —
    # 짧지만 넓은 글자(한글·이모지)를 일부러 섞는다. 다르면 열이 좁아 "…" 로 잘린다.
    import random
    from PyQt6.QtGui import QFont, QFontMetrics
    from PyQt6.QtWidgets import QTableWidgetItem
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


def test_fit_table_never_scrolls_sideways_above_min_font():
    # 글꼴 크기는 '정비례' 추정으로 고르는데 실측은 조금 더 넓게 나와, 실측으로 한 번
    # 더 깎지 않으면 몇 px 넘쳐 가로 막대가 생겼다. 폭을 훑어 잰다.
    import widgets
    cols = [f"열이름{i}" for i in range(19)]
    tb = widgets.FitTableWidget(6, len(cols))
    tb.setHorizontalHeaderLabels(cols)
    from PyQt6.QtWidgets import QHeaderView, QTableWidgetItem
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    tb.horizontalHeader().setMinimumSectionSize(0)
    tb.set_base_font_px(15, 15)
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
    tb = _win.tbl_opponents
    _win._go_page("상대 전적")
    _app.processEvents()
    hdr = tb.horizontalHeader()
    for c in range(tb.columnCount()):
        tb.sortByColumn(c, Qt.SortOrder.AscendingOrder)
        _app.processEvents()
        assert tb.columnWidth(c) >= hdr.sectionSizeHint(c), \
            (tb.horizontalHeaderItem(c).text(), tb.columnWidth(c), hdr.sectionSizeHint(c))
    # 폭이 넉넉하면 화살표 자리 없이도 들어가 위가 안 드러난다 — 남는 폭이 0 인 표로 잰다
    import widgets
    from PyQt6.QtWidgets import QHeaderView, QTableWidgetItem
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
    # 손계산: 승·무·패 3경기(오류 제외) — 득 1+4+1, 실 2+3+1. 오류 경기도 '표시 구간'
    # 4경기에는 들어간다(경기 목록과 같은 범위).
    d = _win.dashboard
    assert [lb.text() for lb in d.wdl_rows] == ["승 1 (33%)", "무 1 (33%)", "패 1 (33%)"], \
        [lb.text() for lb in d.wdl_rows]
    assert d.lb_goals.text() == "2.00 : 2.00", d.lb_goals.text()
    assert "득실차 +0" in d.lb_goals_sub.text(), d.lb_goals_sub.text()
    assert len(d.radar._axes) == 6, d.radar._axes
    assert len(d.dots._items) == 4, d.dots._items
    assert d.dots._items[-1][0] == "오류"  # 오래된 것 → 최신, 최신이 오류 경기
    assert sum(d.minute_chart._series[0][1]) == 6, d.minute_chart._series  # 득점 6골
    assert len(d.timeband_bars._rows) == 4
    assert len(d.rival_bars._rows) == 4
    # 선제골 표본 3경기 < 최소 표본 — 숫자 대신 "—"
    assert all(g._value is None for g in d.gauges), [g._value for g in d.gauges]
    assert d.kpi_wdl.scope.text() == "표시 구간 4경기", d.kpi_wdl.scope.text()
    assert d.kpi_rate.scope.text().startswith(_win._scope_text()), d.kpi_rate.scope.text()
    assert d.clutch.scope.text().startswith(_win._scope_text()), d.clutch.scope.text()
    assert d.trend.scope.text().startswith(f"최근 {_win.sp_trend_days.value()}일")


def test_dashboard_cards_keep_their_own_scope():
    # 표시 구간을 2경기로 좁혀도 승부처·15분·시간대·분석은 시즌 범위 그대로여야 한다
    # (상세 페이지와 같은 숫자). 픽스처는 둘이 같아서 좁혀야만 드러난다.
    d = _win.dashboard
    before_goals = sum(d.minute_chart._series[0][1])
    before_bands = list(d.timeband_bars._rows)
    before_gauges = [g._note for g in d.gauges]
    before_rate = (d.lb_rate.text(), d.lb_rate_delta.text())
    before_rivals = list(d.rival_bars._rows)
    old_to = _win.sp_to.value()
    try:
        _win.sp_to.setValue(2)
        _win._apply_range()
        assert d.kpi_wdl.scope.text() == "표시 구간 2경기", d.kpi_wdl.scope.text()
        # 승률은 시즌 기준 — 표시 구간을 좁혀도 안 바뀐다
        assert (d.lb_rate.text(), d.lb_rate_delta.text()) == before_rate
        assert d.rival_bars._rows == before_rivals  # 자주 만난 상대도 시즌 기준
        assert d.rivals.scope.text().startswith(_win._scope_text()), d.rivals.scope.text()
        assert len(d.dots._items) == 2
        assert sum(d.minute_chart._series[0][1]) == before_goals
        # 개수만 보면 좁힌 2경기가 같은 시간대에 몰렸을 때 차이가 안 보인다 — 값 그대로 비교
        assert d.timeband_bars._rows == before_bands, (d.timeband_bars._rows, before_bands)
        assert [g._note for g in d.gauges] == before_gauges, [g._note for g in d.gauges]
        assert d.clutch.scope.text().endswith(f"{len(_win._matches)}경기"), d.clutch.scope.text()
    finally:
        _win.sp_to.setValue(old_to)
        _win._apply_range()


def test_dashboard_trend_follows_trend_days():
    d = _win.dashboard
    old = _win.sp_trend_days.value()
    try:
        _win.sp_trend_days.setValue(1)
        _win._on_trend_days_apply()
        assert d.trend.scope.text().startswith("최근 1일"), d.trend.scope.text()
        assert len(d.trend_chart._points) == 1, d.trend_chart._points
    finally:
        _win.sp_trend_days.setValue(old)
        _win._on_trend_days_apply()


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
    # 표시 구간을 1경기로 좁혀도 상대 전적은 시즌 범위 전체를 센다(대시보드 '자주 만난 상대'와 같게)
    tb = _win.tbl_opponents
    old = (_win.sp_from.value(), _win.sp_to.value())
    shown = []
    orig_show, orig_info = _win._show_opponent_squad, app_main.QMessageBox.information
    _win._show_opponent_squad = lambda nick, *a: shown.append(nick)
    # 못 찾으면 뜨는 안내 창은 모달이라 offscreen 에서 영원히 멈춘다 — 기록만 남긴다
    app_main.QMessageBox.information = lambda *a, **k: shown.append("못 찾음 창")
    try:
        _win.sp_from.setValue(1)
        _win.sp_to.setValue(1)
        _win._render_all()
        in_range = {m.opponent for m in _win._slice()[0]}
        in_scope = {m.opponent for m in _win._matches}
        assert len(in_range) < len(in_scope), (in_range, in_scope)  # 픽스처가 차이를 만들어야 의미가 있다
        names = {tb.item(r, 0).text() for r in range(tb.rowCount())}
        assert names == in_scope, (names, in_scope)
        # 시즌을 바꾸면 문구도 따라가야 한다 — 처음 만든 문구가 우연히 맞는 것과 구분
        _win._scope_text = lambda: "가짜 시즌"
        _win._render_all()
        del _win._scope_text
        assert "가짜 시즌" in _win.lb_opponent_note.text(), _win.lb_opponent_note.text()
        # 표시 구간 밖 상대도 더블클릭하면 스쿼드가 열린다
        outside = next(iter(in_scope - in_range))
        row = next(r for r in range(tb.rowCount()) if tb.item(r, 0).text() == outside)
        _win._on_opponent_double_clicked(tb.item(row, 0))
        assert shown == [outside], shown
    finally:
        _win._show_opponent_squad, app_main.QMessageBox.information = orig_show, orig_info
        _win.sp_from.setValue(old[0])
        _win.sp_to.setValue(old[1])
        _win._render_all()


def test_season_note_explains_error_games():
    # 콤보는 모든 경기, 표는 승·무·패만 — 픽스처의 "오류" 1경기만큼 차이가 나고 그걸 적어야 한다
    import seasons as sn_
    from datetime import date as d_
    saved = _win._rank_seasons
    _win._rank_seasons = [sn_.Season(no=1, name="시즌 1", start=d_(2025, 1, 1), end=d_(2025, 6, 1))]
    try:
        _win._render_seasons()
        n_err = len(_win._matches_all) - models.summarize(_win._matches_all).total
        assert n_err == 1, n_err  # 픽스처 전제 — 0 이면 이 테스트가 아무것도 안 잰다
        assert "오류' 경기 1개" in _win.lb_season_note.text(), _win.lb_season_note.text()
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
        # 픽스처 선제골 3경기 중 1승 → 33%
        assert d.gauges[0]._value is not None and round(d.gauges[0]._value) == 33, \
            d.gauges[0]._value
    finally:
        dashboard.MIN_GAUGE_GAMES = old
        _win._render_dashboard()


def test_dashboard_cards_navigate():
    from PyQt6.QtTest import QTest
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
    _win._go_page("승률 그래프", "승률·등급")
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
        (charts.RadarChart(), [[], [("a", 0.5, ""), ("b", 0.9, ""), ("c", 0.1, "")]]),
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
    # 픽스처 일별: 01/01 패(1경기 0%) · 01/02 승+무(2경기 50%) · 01/03 오류뿐(0경기).
    # 평균은 승 합 ÷ 경기 합 = 1/3 — 날짜별 승률의 단순 평균(25%)이 아니다.
    # 최저는 01/01 — 경기 없는 01/03 을 0% 로 세면 "다 졌다"로 읽힌다.
    c = _win.dashboard.trend_chart
    c.resize(600, 220)
    c.grab()
    assert round(c.marks["avg"], 1) == 33.3, c.marks["avg"]
    assert c.marks["max"] == (1, "50.0%"), c.marks["max"]
    assert c.marks["min"] == (0, "0.0%"), c.marks["min"]
    # 승률 그래프 페이지 '평균 승률' 카드와 같은 숫자
    assert f"{c.marks['avg']:.1f}%" == _win.card_trend_avg.value.text(), \
        (c.marks["avg"], _win.card_trend_avg.value.text())
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
        assert a == b, ("기본값 그림이 바뀌었다", pts[:2], (w, h))
        om = getattr(old, "marks", {})
        assert {k: (_rect(v) if hasattr(v, "intersects") else v) for k, v in new.marks.items() if k != "label_rects"} \
            == {k: (_rect(v) if hasattr(v, "intersects") else v) for k, v in om.items() if k != "label_rects"}, \
            (new.marks, om)
        assert [(_rect(r), t) for r, t in new._hits] == [(_rect(r), t) for r, t in old._hits]
        assert new.minimumHeight() == old.minimumHeight()


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
    # counts — 경기 있는 점마다 막대, 가장 큰 값만 숫자 · 띠만큼 그래프가 위로 줄어 최소 높이가 는다
    plain = _drawn(charts.AreaTrendChart())
    c = _drawn(charts.AreaTrendChart(), pts=[("a", 40.0, 3), ("b", 50.0, 0), ("c", 60.0, 5)], counts=True)
    assert c.marks["count_bars"] == 2 and c.marks["count_peak"] == (2, "5경기"), c.marks
    assert c.minimumHeight() > plain.minimumHeight()
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
    _win._go_page("승률 그래프")
    c = _win.trend_chart
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
    try:
        _win._render_trend(_win._matches)
        c.grab()
    finally:
        app_main.core.moving_win_rate = real
    assert seen == [(len(_win._matches), [_date(2026, 1, 1), _date(2026, 1, 2), _date(2026, 1, 3)],
                     {"min_n": app_main.core.MIN_COND})], seen
    assert c.marks["ma_points"] == 3, c.marks
    # 등급 — 하루 한 점(4경기 → 3일) · 툴팁에 그날 처음·마지막·최고
    dc = _win.division_chart
    assert len(dc._points) == 3, dc._points
    assert all("처음" in t and "최고" in t for t in dc._tips), dc._tips
    _win._render_trend(_win._matches)


def test_trend_page_groups_not_squeezed():
    # 세로는 아무도 안 쟀다(창 최소 크기 테스트는 폭만) — MIN_WINDOW 에서 그룹마다 최소 높이가 지켜지고,
    # 넘치는 만큼은 세로 스크롤로 밀려야 한다.
    try:
        _at_size(*app_main.MIN_WINDOW)
        _win._go_page("승률 그래프")
        _app.processEvents()
        frame = _win.pages.currentWidget()
        groups = [_win.gb_trend, _win.gb_division, _win.gb_sc]
        bad = [(g.title(), g.height(), g.minimumSizeHint().height()) for g in groups
               if g.height() < g.minimumSizeHint().height()]
        assert not bad, bad
        assert frame.verticalScrollBar().isVisible(), "내용이 창보다 긴데 세로 스크롤이 없다 — 눌렸다"
        assert frame.verticalScrollBar().maximum() > 0
    finally:
        _at_size(1600, 900)
        _win._go_page("대시보드")


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


def test_sc_records_list_and_nexon_line():
    keep_max = dict(_win._max_division)
    try:
        # 0002(01-02 12:00) 900 → 0003(01-02 13:00) 800 → 0004(01-03) 900 : 진입 한 번
        det = _sc_details({"0003": 800})
        _win._on_loaded(_MATCHES, det, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win.lb_sc_list.text() == "2026-01-02 · 1번 (13:00)", _win.lb_sc_list.text()
        assert "2026-01-01 ~" in _win.lb_sc_note.text(), _win.lb_sc_note.text()
        assert _win.division_chart._markers == [1], _win.division_chart._markers  # 그날(01-02)에 세로 점선
        # 넥슨 최근 달성이 목록의 진입과 60초 안이면 같은 것 — 줄을 안 낸다
        _win._on_max_division(_OUID, {"division": 800, "date": "2026-01-02", "at": "2026-01-02T13:00:30"})
        assert _win.lb_sc_nexon.isHidden(), _win.lb_sc_nexon.text()
        # 5분 어긋나면 목록에 없는 것
        _win._on_max_division(_OUID, {"division": 800, "date": "2026-01-02", "at": "2026-01-02T13:05:00"})
        assert not _win.lb_sc_nexon.isHidden()
        assert _win.lb_sc_nexon.text().startswith("넥슨 기록상 최근 달성 2026-01-02 (저장된 경기에서는 못 찾음"), \
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
        _restore_account()


def test_sc_nexon_line_arrives_after_loader_finishes():
    # maxdivision 신호는 finished_ok 뒤에 온다 — _render_trend 에서만 그리면 넥슨 줄이 늘 빠진다. 진짜 로더로.
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
    from PyQt6.QtGui import QColor
    t = _win.tbl_period
    rate_col = _win.PERIOD_COLUMNS.index("승률")
    vs_col = _win.PERIOD_COLUMNS.index("평균 대비")
    games_col = _win.PERIOD_COLUMNS.index("경기")
    ms = _week_matches()
    try:
        _win.cb_period.setCurrentIndex(_win.cb_period.findData(7))
        # 헤더에 정렬이 남아 있는 상태에서 다시 그린다 — 채우자마자 재정렬되면 색이 엉뚱한 행에 간다(표 함정 1번)
        t.sortByColumn(games_col, Qt.SortOrder.AscendingOrder)
        _win._render_period(ms)
        assert t.rowCount() == 3
        by_label = {t.item(r, 0).text(): r for r in range(3)}
        a, b, c = by_label["09/07~09/13"], by_label["09/14~09/20"], by_label["09/21~09/27"]
        # 4판짜리 A 가 100% 지만 최고가 아니다(후보는 MIN_COND 판 이상) — 흐리고 칠하지 않는다
        assert not t.item(a, rate_col).font().bold()
        assert t.item(a, rate_col).foreground().color() == QColor(T.TEXT_DIM)
        assert t.item(a, rate_col).toolTip().startswith("표본이 4경기")
        assert t.item(b, rate_col).font().bold() and t.item(c, rate_col).font().bold()
        # 평균(13/23 = 56.5%)보다 높은 B 는 HEAT_ATK, 낮은 C 는 HEAT_DEF 쪽 — 그 행에 붙어 있다
        overall = 13 / 23 * 100
        f = _win.HEAT_FLOOR
        exp = lambda diff, end: _win._blend(  # noqa: E731
            T.PANEL, end, f + (1 - f) * min(abs(diff) / _win.PERIOD_HEAT_FULL_PP, 1.0)).name()
        assert t.item(b, rate_col).background().color().name() == exp(60 - overall, T.HEAT_ATK)
        assert t.item(c, rate_col).background().color().name() == exp(100 / 3 - overall, T.HEAT_DEF)
        assert t.item(b, vs_col).text() == f"{60 - overall:+.1f}%p", t.item(b, vs_col).text()
        assert t.isSortingEnabled()
        # 그래프는 표와 같은 단위 · 오래된 것부터
        g = _win.period_chart
        g.resize(600, 260)
        g.grab()
        assert [h[1].split(" · ")[0] for h in g._hits] == ["09/07~09/13", "09/14~09/20", "09/21~09/27"]
        assert g.marks["count_bars"] == 3 and g.marks["count_peak"] == (1, "10경기"), g.marks
    finally:
        t.setSortingEnabled(False)
        t.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        _win._render_period(_win._matches)


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
        vs = cols.index("지난 시즌 대비")
        # 새 시즌 33.3%(승1 무1 패1) − 지난 시즌 100%(2승) → 음수 · 가장 오래된 시즌은 비교 대상이 없다
        assert t.item(rows["2025 시즌 9"], vs).text() == f"{100 / 3 - 100:+.1f}%p", t.item(rows["2025 시즌 9"], vs).text()
        assert t.item(rows["2025 시즌 8"], vs).text() == "—"
        grade = cols.index("등급")
        assert t.item(rows["2025 시즌 9"], grade).text() == _win._division_names.get(900, "900")
        assert t.item(rows["2025 시즌 9"], grade).toolTip().startswith("최고 ")
        assert t.item(rows["2025 시즌 8"], grade).text() == "—"  # 상세가 없는 경기 — 등급 모름
        bars = _win.season_bars._rows
        assert [r[0] for r in bars] == ["2025 시즌 9", "2025 시즌 8"], bars
        assert bars[0][2] == "33.3% · 3경기" and bars[0][4] is True, bars[0]  # 3경기 < MIN_COND → 흐림
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
    from PyQt6.QtGui import QPalette
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


def test_setup_app_logs_crash_and_notifies_once():
    # 훅이 안 걸려 있으면 기본 훅이 받는다 — PyQt6 는 그때 앱을 끝낸다
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
    assert asked == [tray.instance_name()], asked


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

        def attach_rank_sched(self, sched):
            calls.append("랭킹 수집")  # 예약은 창 밖(AppShell)에 살고 창은 상태만 받는다

        def open_last_account(self):
            calls.append("마지막 계정")

        def start_prefetch(self):
            calls.append("미리 읽기")

        def ask_notice_update_once(self):
            pass   # 옛 동의자 다시 묻기 — 창이 보인 뒤(test_reask_after_show_normal)

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
    assert run(False) == ["창", "랭킹 수집", "show", "새 버전 확인", "캐시 정리", *tail, "미리 읽기"], calls
    assert run(True) == ["창", "랭킹 수집", "show", "새 버전 확인", "캐시 정리", *tail, "마지막 계정"], calls


def _stop_shell():
    """main() 이 만든 껍데기의 타이머(1시간 수집 확인·6시간 업데이트)를 내린다 — 다음 테스트로 새지 않게."""
    sh = app_main._SHELL
    if sh is not None:
        for t in (sh._release_timer, sh._update_timer, sh._tray_timer):
            t.stop()
        if sh.sched is not None:
            sh.sched.shutdown()
    app_main._SHELL = None


def test_main_tray_mode_makes_hidden_window_without_prefetch():
    calls = []

    class _Win:
        def __init__(self, api):
            calls.append("창")

        def attach_rank_sched(self, sched):
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
    finally:
        _stop_shell()
        (app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY, app_main._SHELL) = orig
    # 창은 만들되 띄우지 않고, 1만 경기 미리 읽기(약 750MB)도 안 한다
    # 색인 백필은 트레이 시작이면 첫 화면이 없어 2분 뒤(ROADMAP 2.1.1 12)
    assert calls == ["창", "start_update_check", "start_cache_prune", "sync_ranker_pick_data",
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
    finally:
        (app_main._setup_app, app_main.NoticeDialog, app_main.ApiKeyDialog,
         app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY) = orig


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
           "assets": [{"name": "FifaMatchTracker-Setup-v9.0.0.exe", "browser_download_url": setup_u},
                      {"name": "SHA256SUMS.txt", "browser_download_url": sums_u},
                      {"name": "FifaMatchTracker-v9.0.0-portable.zip", "browser_download_url": "z"}]}
    full = updatecheck.Release("v9.0.0", page, setup_u, sums_u, "- 새 기능\n- 고친 것")
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


def _status_bar_state():
    """두 화면(검색·사이드바)의 왼쪽 아래 상태 칸 — [(문구, 버튼 문구 or None)]."""
    out = []
    for idx in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
        page = _win.stack.widget(idx)
        lb = [x for x in page.findChildren(app_main.QLabel) if x.objectName() == "updateStatus"]
        bt = [x for x in page.findChildren(app_main.QPushButton) if x.objectName() == "updateInline"]
        assert len(lb) == 1 and len(bt) == 1, (idx, len(lb), len(bt))
        out.append((lb[0].text(), bt[0].text() if bt[0].isVisibleTo(page) else None))
    return out


def test_update_status_always_visible_bottom_left():
    # 사용자 요청: 잠깐 뜨는 카드가 아니라 화면에 늘 보이는 칸(버전 옆)
    card = _win.update_card
    orig = updatecheck.check_full
    page0 = _win.stack.currentIndex()
    _win.stack.setCurrentIndex(_win.PAGE_SEARCH)  # 최신 카드는 첫 검색 화면에서만 뜬다
    try:
        for status, rel, want, card_shown in [
                (updatecheck.LATEST, None, ("최신 버전입니다", None), True),     # 카드 자리에도(검색 화면)
                (updatecheck.UNKNOWN, None, ("업데이트 확인 못 함", None), False),  # 모르면 '최신'이라 안 한다
                (updatecheck.NEWER, _REL, ("새 버전 v9.0.0", "받으러 가기"), True)]:
            card.hide()
            updatecheck.check_full = lambda *a, s=status, r=rel, **k: updatecheck.CheckResult(s, r)
            _win.start_update_check()          # QThread.start 는 막혀 있다 — 배선만 만든다
            assert _status_bar_state() == [("업데이트 확인 중…", None)] * 2
            _win._update_worker.run()           # 같은 스레드에서 돌려 신호 → 창까지
            assert _status_bar_state() == [want] * 2, (status, _status_bar_state())
            assert card.isVisibleTo(_win) == card_shown, status
            if status == updatecheck.LATEST:
                assert card.lb_title.text() == "최신 버전입니다", card.lb_title.text()
                assert not card.btn_update.isVisibleTo(card) and card.btn_later.text() == "닫기"
                _app.processEvents()
                assert card.isVisibleTo(_win), "최신 카드가 저절로 닫혔다"
        # 최신 카드 뒤 새 버전이 오면 버튼이 돌아온다(마지막 반복이 NEWER)
        assert card.btn_update.isVisibleTo(card) and card.btn_later.text() == "나중에"
        # 왼쪽 아래 버튼도 카드 버튼과 같은 길(소스 실행 = 페이지 열기)
        opened = []
        with _Patch((app_main.QDesktopServices, "openUrl", lambda u: opened.append(u.toString())),
                    (updatecheck, "install_dir", lambda: None)):
            btn = [b for b in _win.stack.widget(_win.PAGE_SEARCH).findChildren(app_main.QPushButton)
                   if b.objectName() == "updateInline"][0]
            btn.click()
        assert opened == [_REL.page_url], opened
    finally:
        updatecheck.check_full = orig
        card.hide()
        _win._release = None
        _win._set_update_status("")
        _win.stack.setCurrentIndex(page0)


def test_latest_card_never_covers_main_page():
    # 사용자 요청(2026-10-02 스크린샷): '최신 버전입니다' 카드가 대시보드를 가렸다 — 메인에선 안 뜬다.
    # 새 버전 카드는 놓치면 안 되니 메인에서도 뜨고, 화면을 옮겨도 남는다.
    card = _win.update_card
    page0 = _win.stack.currentIndex()
    try:
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        card.hide()
        _win._on_update_latest()
        assert not card.isVisibleTo(_win), "메인 화면에 최신 카드가 떴다"
        assert _status_bar_state()[1][0] == "최신 버전입니다"   # 왼쪽 아래에는 그대로
        _win.stack.setCurrentIndex(_win.PAGE_SEARCH)
        _win._on_update_latest()
        assert card.isVisibleTo(_win)
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)            # 검색 → 메인(계정을 열면)이면 닫힌다
        assert not card.isVisibleTo(_win), "메인으로 넘어가도 최신 카드가 남았다"
        _win._on_update_found(_REL)                           # 새 버전 카드는 메인에서도
        assert card.isVisibleTo(_win)
        _win.stack.setCurrentIndex(_win.PAGE_SEARCH)
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        assert card.isVisibleTo(_win), "새 버전 카드가 화면을 옮기자 사라졌다"
    finally:
        card.hide()
        _win._release = None
        _win._set_update_status("")
        _win.stack.setCurrentIndex(page0)


def test_version_bottom_left_not_in_title():
    assert config.APP_VERSION not in _win.windowTitle(), _win.windowTitle()
    for idx in (_win.PAGE_SEARCH, _win.PAGE_MAIN):
        page = _win.stack.widget(idx)
        labels = [lb for lb in page.findChildren(app_main.QLabel) if lb.objectName() == "versionLabel"]
        assert [lb.text() for lb in labels] == [config.APP_VERSION], (idx, [lb.text() for lb in labels])
        lb = labels[0]
        _win.stack.setCurrentIndex(idx)
        _app.processEvents()
        pos = lb.mapTo(page, lb.rect().bottomLeft())
        # 왼쪽 아래 — 화면 왼쪽 1/4 · 아래 1/8 안
        assert pos.x() < page.width() / 4 and pos.y() > page.height() * 7 / 8, (idx, pos, page.size())
    _win.stack.setCurrentIndex(_AFTER_LOAD[0])


def test_update_card_bottom_right_on_both_pages():
    card = _win.update_card
    assert not card.isVisibleTo(_win), "새 버전 신호 없이 카드가 보인다"
    old_page = _win.stack.currentIndex()
    try:
        for page in (_win.PAGE_SEARCH, _win.PAGE_MAIN):  # 처음 검색 전 화면에도 떠야 한다(사용자 요청)
            _win.stack.setCurrentIndex(page)
            _win._on_update_found(_REL)
            _app.processEvents()
            assert card.isVisibleTo(_win) and "v9.0.0" in card.lb_title.text(), card.lb_title.text()
            g = card.geometry()
            bottom = _win.height() - _win.statusBar().height()
            # 오른쪽 아래 구석 — 여백 MARGIN, 상태줄 위
            assert g.right() == _win.width() - card.MARGIN - 1, (g, _win.width())
            assert g.bottom() == bottom - card.MARGIN - 1, (g, bottom)
        # 창 크기를 바꾸면 따라간다
        _win.resize(1400, 800)
        _app.processEvents()
        assert card.geometry().right() == _win.width() - card.MARGIN - 1, card.geometry()
        # 소스 실행(테스트)은 설치판이 아니라 '받으러 가기'
        assert card.btn_update.text() == "받으러 가기", card.btn_update.text()
    finally:
        card.hide()
        _win.resize(1600, 900)
        _win.stack.setCurrentIndex(old_page)


def test_update_click_portable_opens_page_only():
    opened = []
    with _Patch((app_main.QDesktopServices, "openUrl", lambda u: opened.append(u.toString())),
                (updatecheck, "install_dir", lambda: None)):
        _win._release = _REL
        _win._on_update_clicked()
    assert opened == [_REL.page_url] and _win._download_worker is None, (opened, _win._download_worker)


def test_update_click_asks_first_and_no_means_nothing():
    no = app_main.QMessageBox.StandardButton.No
    with _Patch((updatecheck, "install_dir", lambda: pathlib.Path("C:/fake")),
                (app_main.QMessageBox, "question", lambda *a, **k: no)):
        _win._download_worker = None
        _win._on_update_found(_REL)
        _win._on_update_clicked()
        try:
            assert _win._download_worker is None, "아니오를 눌렀는데 내려받기 시작"
            assert _win.update_card.btn_update.isEnabled(), "아니오 뒤에 버튼이 잠겼다"
        finally:
            _win.update_card.hide()


def test_update_click_installed_downloads_then_installs_and_closes():
    asked, launched, closed = [], [], []
    yes = app_main.QMessageBox.StandardButton.Yes
    with _Patch((updatecheck, "install_dir", lambda: pathlib.Path("C:/fake")),
                (app_main.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or yes),
                (updatecheck, "launch_installer", lambda p: launched.append(p)),
                (_win, "close", lambda: closed.append(1))):
        _win._on_update_found(_REL)
        assert _win.update_card.btn_update.text() == "업데이트", _win.update_card.btn_update.text()
        _win._on_update_clicked()
        try:
            w = _win._download_worker
            assert w is not None and _win.update_card.bar.isVisibleTo(_win), "내려받기가 시작되지 않았다"
            assert "v9.0.0" in asked[0] and "- 새 기능" in asked[0], asked
            w.done.emit("C:/fake/setup.exe")  # 검증 통과 신호
            assert launched == [pathlib.Path("C:/fake/setup.exe")] and closed == [1], (launched, closed)
        finally:
            _win._download_worker = None
            _win.update_card.hide()
    del _win.close


def test_inline_update_after_dismiss_shows_progress_and_locks_button():
    # 카드를 [나중에]로 닫고 왼쪽 아래 버튼으로 받으면 — 진행이 보일 곳이 없으면 멈춘 것처럼 보인다
    yes = app_main.QMessageBox.StandardButton.Yes
    card = _win.update_card
    with _Patch((updatecheck, "install_dir", lambda: pathlib.Path("C:/fake")),
                (app_main.QMessageBox, "question", lambda *a, **k: yes),
                (app_main.QMessageBox, "warning", lambda *a, **k: None)):
        _win._on_update_found(_REL)
        card.btn_later.click()
        assert not card.isVisibleTo(_win)
        try:
            _win._update_inline_btns[0].click()
            assert card.isVisibleTo(_win) and card.bar.isVisibleTo(_win), "진행이 안 보인다"
            assert not any(b.isEnabled() for b in _win._update_inline_btns), "받는 중에 또 누를 수 있다"
            _win._on_update_failed("테스트 실패")
            assert all(b.isEnabled() for b in _win._update_inline_btns), "실패 뒤 버튼이 잠긴 채다"
        finally:
            _win._download_worker = None
            _win._release = None
            card.hide()
            _win._set_update_status("")
            for b in _win._update_inline_btns:
                b.setEnabled(True)


def test_update_failure_restores_card_and_tells_why():
    warned = []
    with _Patch((app_main.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))):
        _win._release = _REL
        _win.update_card.set_busy(True, "내려받는 중…")
        _win._on_update_failed("체크섬이 다릅니다")
    try:
        assert not _win.update_card.bar.isVisibleTo(_win) and _win.update_card.btn_update.isEnabled()
        assert "체크섬" in warned[0] and _REL.page_url in warned[0], warned
    finally:
        _win.update_card.hide()


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
        ("seasons", lambda: sn.fetch_seasons(), sn.SeasonError),
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
    for mod in (ranker, playerinfo):
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
            ranker.RankRow(rank=page * 100 + i, profile_sn=page * 100 + i, nickname=n, team_color=c,
                           team_value=v if v is not None else 7)
            for i, (n, c, v) in enumerate(rows)], "")

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
    pages = {1: [("가", "네덜란드", 1), ("남", "프랑스", 2)], 2: [("나", "", None)]}
    ld, got, done = _run_rank_list({"가", "나", "밖"}, pages)
    # 원한 사람만 내보낸다(목록의 남은 저장하지 않는다) · 목록에 없으면 '랭킹 밖'("")
    assert got == {"가": ("네덜란드", 1), "나": ("", None), "밖": ("", None)}, got
    assert done == [True] and ld.failed_pages == 0
    pages[2] = None  # 한 쪽 실패 — 그 쪽에 있었을 수도 있는 상대를 '랭킹 밖'으로 굳히면 안 된다
    ld, got, done = _run_rank_list({"가", "나", "밖"}, pages)
    assert got == {"가": ("네덜란드", 1)}, got
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
        return self

    def __exit__(self, *exc):
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
    from PyQt6.QtCore import QObject, pyqtSignal

    opps = sorted({m.opponent for m in _MATCHES if m.opponent})

    class _Instant(QObject):
        loaded_many = pyqtSignal(dict)
        progress = pyqtSignal(int, int)
        finished_all = pyqtSignal()

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
        tbl = _win.tbl_teamcolor_rate
        assert tbl.rowCount() == 1 and tbl.item(0, 0).text() == "즉시컬러"
        conn = store.open_db(config.DB_PATH)
        try:
            saved = store.load_team_colors(conn, opps)
        finally:
            conn.close()
        assert saved == {n: ("즉시컬러", 7) for n in opps}, saved


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
    orig = app_main.core.opponent_position_players, _win.sp_to.value(), _win._show_teamcolor_detail
    saved_colors = dict(_win._team_colors)

    def spy(details, *a, **k):
        seen.append(len(details))
        return orig[0](details, *a, **k)

    try:
        for m in _MATCHES:
            if m.opponent:
                _win._team_colors[m.opponent] = "범위컬러"
        _win.sp_to.setValue(1)
        _win._render_all()
        assert len(_win._slice()[1]) < len(_win._details), "표시 구간이 좁아지지 않아 비교가 안 된다"
        app_main.core.opponent_position_players = spy
        _win._show_teamcolor_detail = lambda *a, **k: None
        _win._on_position_color_changed(0)
        _win._on_teamcolor_double_clicked(_win.tbl_teamcolor_rank.item(0, 1))
        assert seen == [len(_win._details)] * 2, (seen, len(_win._details))
    finally:
        app_main.core.opponent_position_players = orig[0]
        _win._show_teamcolor_detail = orig[2]
        _win._team_colors.clear()
        _win._team_colors.update(saved_colors)
        _win.sp_to.setValue(orig[1])
        _win._render_all()


def test_teamcolor_counts_season_scope_not_display_range():
    # 표시 구간을 1경기로 좁혀도 팀컬러 표는 시즌(지금은 전체) 경기를 다 센다
    saved = dict(_win._team_colors), _win.sp_to.value()
    try:
        for m in _MATCHES:
            if m.opponent:
                _win._team_colors[m.opponent] = "테스트컬러"
        _win.sp_to.setValue(1)
        _win._render_all()
        n = sum(1 for m in _win._matches if m.opponent)
        assert len(_win._slice()[0]) == 1 and n > 1, n
        tbl = _win.tbl_teamcolor_rate
        col = app_main.MainWindow.TEAMCOLOR_RATE_COLUMNS.index("경기")
        assert tbl.rowCount() == 1 and tbl.item(0, col).text() == str(n), \
            [tbl.item(0, c).text() for c in range(tbl.columnCount())]
        note = _win._teamcolor_note_labels[0].text()
        assert f"상대 {len({m.opponent for m in _win._matches if m.opponent})}명 중" in note, note
    finally:
        _win._team_colors.clear()
        _win._team_colors.update(saved[0])
        _win.sp_to.setValue(saved[1])
        _win._render_all()


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
        assert tabs.get("players") == "랭커 비교" and tabs.get("trend") == "승률·등급", tabs
        assert _win._restore == {"page": "슛 맵", "season": want_season}, _win._restore
        _win._restore["tabs"] = tabs
        # 처음 그리는 계정이면 그 메뉴·시즌으로
        _win._ouid, _win._season_picked = "", False
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win._current_page_name() == "슛 맵", _win._current_page_name()
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
            from PyQt6.QtGui import QCloseEvent
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
    cases = [*app_main.MainWindow.OLD_PAGE_NAMES.items(), ("없는 메뉴", ("대시보드", None)),
             ("랭킹 추이", ("랭킹 추이", None))]
    assert len(cases) == 5
    try:
        for old, want in cases:
            _win._go_page("선수 지표", "지표")
            _win._go_page("스쿼드·이적", "타임라인")  # 기억된 탭이 답과 달라야 복원을 잰다
            _win._go_page("슛 맵")
            _win._ouid, _win._restore = "", {"page": old}
            _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                            {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
            assert _win._current_view() == want, (old, _win._current_view())
    finally:
        _win._restore = {}
        _win._page_tabs["선수 지표"].set_current("지표", emit=False)
        _win._page_tabs["스쿼드·이적"].set_current("타임라인", emit=False)
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
                {"matchType": 52, "division": 800, "achievementDate": "2026-07-05T18:37:53"}]


class _MaxDivApi(_DetailApi):
    """maxdivision 은 바로 답하고, 로더가 finished_ok 전에 부르는 division 메타는 그 답이 끝난 뒤에야 준다 —
    그래서 finished_ok 때는 늘 '이미 끝남'이라 보내는 순서가 결정적이다(앞으로 옮기는 변이가 매번 잡힌다)."""

    def __init__(self, rows=_MAXDIV_ROWS, fail=False):
        import threading
        super().__init__([], "")
        self.rows, self.fail, self.calls = rows, fail, 0
        self.done = threading.Event()

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
        other = _MaxDivApi()
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
        api = _MaxDivApi()
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
    from PyQt6.QtCore import Qt as _Qt
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
        assert _win.statusBar().currentMessage() == "팀컬러 — 랭킹 목록 120 / 500쪽 읽는 중…", \
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
        def __init__(self, api, nick, mt, prev=None, prefetch=None, record_elo=False, want_max_division=False):
            got.append(prev)
            pf.append(prefetch)
            elo.append(record_elo)
            maxdiv.append(want_max_division)
            self.progress = self.finished_ok = self.failed = self.key_invalid = self.quota_hit = self
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
        assert f(dt_(2025, 6, 1, 0, 0), _win.ONGOING)                     # 마지막 종료일 당일부터 진행 중
        assert not f(dt_(2025, 5, 31, 23, 59), _win.ONGOING)
        assert f(dt_(2025, 5, 31, 23, 59), s1) and not f(dt_(2025, 6, 1), s1)
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
    for r in range(_win.table.rowCount()):
        res = _win.table.item(r, 1).text()
        bg = _win.table.item(r, 0).background().color().name().lower()
        tinted = _win._blend(T.PANEL, want[res], T.ROW_TINT).name().lower() if res in want else None
        if tinted:
            assert bg == tinted, (res, bg, tinted)
        else:
            assert bg not in (_win._blend(T.PANEL, T.WIN, T.ROW_TINT).name().lower(),
                              _win._blend(T.PANEL, T.LOSE, T.ROW_TINT).name().lower()), (res, bg)
        assert _win.table.item(r, 0).data(Qt.ItemDataRole.UserRole), "더블클릭용 경기 id 가 안 붙었다"
        seen.add(res)
    assert seen == set(results), seen
    _win._render_matches(_win._matches)


def test_compare_colors_the_better_side():
    import dataclasses
    from models import summarize as summ
    from PyQt6.QtGui import QColor
    green = QColor(T.GREEN).name()
    mine = [dataclasses.replace(m, result="승") for m in _MATCHES]
    opp = [dataclasses.replace(m, result="패", match_id=f"o{i}") for i, m in enumerate(_MATCHES)]
    saved = (_win._matches, _win._fill_compare_squad, _win.sp_compare_n.value())
    _win._matches = mine
    _win._fill_compare_squad = lambda *a: None          # 스쿼드 그림은 이미지 받기라 뺀다
    _win.sp_compare_n.setValue(len(mine))
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
    finally:
        _win._matches, _win._fill_compare_squad = saved[0], saved[1]
        _win.sp_compare_n.setValue(saved[2])


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
    saved = (_win.sp_compare_n.value(), config.WEB_DATA, _win.stack.currentIndex())
    config.WEB_DATA = False
    try:
        _win.stack.setCurrentIndex(_win.PAGE_MAIN)
        _at_size(*app_main.MIN_WINDOW)
        _win._go_page("구단주 비교")
        _win.sp_compare_n.setValue(len(_MATCHES))
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
        _win.sp_compare_n.setValue(saved[0])
        config.WEB_DATA = saved[1]
        _win.stack.setCurrentIndex(saved[2])
        _at_size(1600, 900)


def test_compare_profile_rows_and_key_players():
    saved = _win.sp_compare_n.value()
    try:
        _win.sp_compare_n.setValue(len(_MATCHES))
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
        _win.sp_compare_n.setValue(1)
        _win._matches, keep_m = [first], _win._matches
        try:
            _win._render_compare("상대", [first], _OUID, list(_DETAILS))
        finally:
            _win._matches = keep_m
        one = _win._details_of(list(_DETAILS), [first])
        assert len(one) == 1 and one[0]["matchId"] == first.match_id
        labels = [t.item(r, 0).text() for r in range(t.rowCount())]
        prof1 = {a.name: a.mine for a in core.team_profile(one, _OUID).axes}
        assert t.item(labels.index("경기당 슛"), 1).text() == f"{prof1['슈팅']:.1f}"
        # 지표 카드는 접힌다
        _win.fold_compare_metrics.head.click()
        assert not _win.fold_compare_metrics.is_open()
        _win.fold_compare_metrics.head.click()
        assert _win.fold_compare_metrics.is_open()
    finally:
        _win.sp_compare_n.setValue(saved)


def test_position_opponents_pitch_and_folded_table():
    """⑨ — 자리마다 가장 많이 만난 상대 카드가 축구장 칩으로(시세 줄 자리에 횟수·비율), 표는 아래 접힘. 카드 정보 요청 0."""
    keep = (list(_FakeCardInfoLoader.started), config.NOTICE_ACCEPTED, config.WEB_DATA)
    try:
        # 게이트를 연 채로 잰다 — 닫혀 있으면 "요청 0"이 ⑨ 덕인지 게이트 덕인지 모른다(변이로 확인: 닫힌 채로는 못 잡았다)
        config.NOTICE_ACCEPTED, config.WEB_DATA = config.CHIP_NOTICE_VERSION, True
        assert config.chip_auto_allowed()
        _FakeCardInfoLoader.started.clear()
        _win._render_position_opponents(list(_DETAILS))
        players = core.opponent_position_players(list(_DETAILS), _OUID,
                                                 pos_name=lambda p: _win._positions.get(p, str(p)))
        pitches = [_win.box_position_pitch.itemAt(i).widget() for i in range(_win.box_position_pitch.count())]
        pitch = next(w for w in pitches if isinstance(w, app_main.PitchWidget))
        chips = pitch.chips()
        assert len(chips) == len(players) and players, (len(chips), len(players))
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
        assert got == {"가": ("네덜란드", 5001), "나": ("", None), "밖": ("", None)}, got
        assert done == [True] and ld.fetched_at == taken, (done, ld.fetched_at)
        # 팀컬러 캐시의 유효기간은 스냅숏 시각부터 센다(지금 시각이면 7일이 최대 8일이 된다)
        _win._teamcolor_loader, _win._teamcolor_pending = ld, ["가", "나"]
        _win._team_colors.update({k: c for k, (c, _) in got.items()})
        _win._team_values.update({k: v for k, (_, v) in got.items()})
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
        assert got["가"] == ("네덜란드", 1) and ld.saved_snapshot is None
        assert not config.RANK_DB_PATH.exists(), "수집이 꺼졌는데 rank.db 를 만들었다"
        sw.write(True, True)
        ld, got, _ = _run_rank_list({"가"}, pages)
        assert ld.saved_snapshot is not None and ld.saved_snapshot.kind == "ok", ld.saved_snapshot
        assert rankcollect.read_status()["last_rows"] == 3
        # 한 쪽이라도 못 읽었으면 스냅숏을 남기지 않는다(팀컬러는 읽은 만큼 쓴다)
        rankcollect.delete_db()
        pages[2] = None
        ld, got, _ = _run_rank_list({"가"}, pages)
        assert got == {"가": ("네덜란드", 1)} and ld.saved_snapshot is None
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
        assert got == {"가": ("브라질", 5001)} and done == [True], (got, done)


def test_main_loader_records_elo_and_compare_does_not():
    tmp, saved = _loader_db(4)
    gate = threading.Event()
    orig = app_main.MatchLoader._safe_rank
    info = ranker.RankerInfo(nickname="테스트구단주", rank=12, elo=2345.0, profile_sn=77)
    app_main.MatchLoader._safe_rank = lambda self: (gate.wait(5), info)[1]

    def rows():
        c = store.open_db(config.DB_PATH)
        try:
            return [(r["elo"], r["rank"], r["profile_sn"], r["source"]) for r in store.elo_history(c, _OUID)]
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
    from PyQt6.QtGui import QCloseEvent
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
    from PyQt6.QtGui import QCloseEvent
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
    from PyQt6.QtGui import QCloseEvent
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
    _win.update_card.hide()
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
        # 테스트 쪽 참조는 지운다 — 창은 넘긴 목록 객체를 그대로 쥐므로, 그 목록을 '내 것'으로 빼면 창의 참조도 안 보인다
        mine: set = set()
        del matches, details
        # 개수만 — 목록으로 받아 두면 그 목록이 창의 옛 목록을 살려 둬서 '남았다'로 잰다(처음 판이 그랬다)
        before = sum(1 for r in gc.get_referrers(probe) if id(r) not in mine)
        assert before >= 2, f"측정 도구 확인 — 내려놓기 전엔 창(원본)·끝난 로더가 쥐고 있어야 한다: {before}"
        _win._max_division[_OUID] = {"division": 800, "date": "2026-07-05"}
        _win.release_memory()
        gc.collect()
        left = [type(r).__name__ for r in gc.get_referrers(probe) if id(r) not in mine]
        try:
            assert left == [], f"내려놓은 뒤에도 경기 기록을 쥐고 있다: {left}"
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
    from PyQt6.QtWidgets import QLabel
    text = " ".join(lb.text() for i in range(_win.box_diag_discipline.count())
                    if (w := _win.box_diag_discipline.itemAt(i).widget())
                    for lb in [w, *w.findChildren(QLabel)] if isinstance(lb, QLabel))
    assert "골대 나 0.60 상대 0.00" in text, text             # 골대 3 / 5경기
    assert "퇴장 나 1회 상대 2회" in text, text                 # 퇴장은 평균이 0.00 으로만 보여 횟수로
    assert "종료 유형: 정상 5 · 몰수승 0 · 몰수패 0 · 중단 0" in text, text
    _win._render_all()


def _weak_sites():
    """표본 흐림 규칙의 진입점(ROADMAP 1.2.1 표) — 이름 → (그리기, 위젯에서 (경기 수, 흐림) 목록 뽑기, 기준 상수 이름)."""
    from PyQt6.QtWidgets import QLabel, QProgressBar

    def bars(*boxes, kind=QProgressBar):
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

    tod = {b.label: b.games for b in app_main.core.time_of_day_rates(_win._matches)}
    opp = {o.nickname: o.games for o in app_main.core.opponent_stats(_win._matches)}
    diag = lambda: _win._render_diagnosis(_win._details)  # noqa: E731
    clutch = lambda: _win._render_clutch(_win._details, _win._matches)  # noqa: E731
    # 픽스처엔 골대·퇴장 경기가 없어 막대가 안 생긴다 — 표본이 3·1·2 로 갈리게 만든 경기로 그린다
    disc = lambda: _win._render_discipline(_discipline_details())  # noqa: E731
    return {
        "성적 진단": (diag, lambda: bars(_win.box_diag_division, _win.box_diag_possession), "MIN_COND"),
        "성적 진단 규율": (disc, lambda: bars(_win.box_diag_discipline), "MIN_COND"),
        # 흐름 분석 메뉴 그리기(_render_analysis)를 거쳐 재야 그 구역이 실제로 불리는지도 잰다
        "흐름 분석 연승": (_win._render_analysis, lambda: bars(_win.box_streak), "streak_min_n"),
        "승부처 시간대": (clutch, lambda: bars(_win.box_clutch_tod), "MIN_COND"),
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


def test_winrate_bars_use_shared_helper():
    # 승률 막대는 전부 widgets.win_rate_bar 를 거친다 — 직접 QProgressBar() 를 만들면 표본 흐림 규칙을 건너뛴다.
    # 허용은 변수 이름으로(줄 번호면 코드를 지울 때마다 깨진다): 상태 진행 막대 · 승부처 분 단위 득·실 개수 막대.
    src = (pathlib.Path(_ROOT) / "app_main.py").read_text(encoding="utf-8")
    made = re.findall(r"^\s*([\w.]+)\s*=\s*QProgressBar\(\)", src, re.M)
    assert sorted(made) == ["gbar", "rbar", "self.progress"], made
    # 대입 없이 바로 넣는 것(addWidget(QProgressBar()))도 센다 — 대입만 세던 첫 판은 그 변이를 못 잡았다
    assert len(re.findall(r"QProgressBar\(", src)) == len(made), re.findall(r".*QProgressBar\(.*", src)
    assert "QProgressBar()" not in (pathlib.Path(_ROOT) / "dashboard.py").read_text(encoding="utf-8")


def test_heat_colors_keep_text_readable():
    # 선수 지표 칸은 글자 위에 칠한다 — 두 팔레트 × 공격/수비 × 11단계 전부 TEXT 대비 4.5 이상. 원색 RED 를 끝 색으로
    # 쓰면(예전) 어두운 테마에서 밝은 글자가 안 읽힌다. 포지션 글자는 PANEL 위 4.5 이상. 흐린 승률 막대 위 글자(TEXT)도.
    f = app_main.MainWindow.HEAT_FLOOR
    bad = []
    for mode, p in T._PALETTES.items():
        for key in ("HEAT_ATK", "HEAT_DEF"):
            for s in range(11):
                bg = T.blend(p["PANEL"], p[key], f + (1 - f) * s / 10)
                if T.contrast(p["TEXT"], bg) < 4.5:
                    bad.append((mode, key, s, bg))
        for key in ("POS_FW", "POS_MF", "POS_DF", "POS_GK"):
            if T.contrast(p[key], p["PANEL"]) < 4.5:
                bad.append((mode, key))
        if T.contrast(p["TEXT"], T.blend(p["PANEL_2"], p["WIN_BAR"], T.WEAK_MIX)) < 4.5:
            bad.append((mode, "흐린 막대 글자"))
    assert not bad, bad
    # 화면이 실제로 그 끝 색을 쓰는지(배선) — 기본 RED/BLUE 가 아니다
    assert (T.HEAT_ATK, T.HEAT_DEF) == (T._P["HEAT_ATK"], T._P["HEAT_DEF"]) and T.HEAT_ATK != T.RED


def test_heat_scale_degenerate():
    hs = app_main.MainWindow._heat_scale
    assert hs([], []) == []
    assert hs([3.0, 9.0], [True, False]) == [None, None]          # 기준 넘는 선수 1명 — 눈금 못 잡음
    assert hs([5.0, 5.0, 1.0], [True, True, False]) == [None, None, None]  # 전부 같은 값
    assert hs([2.0, 4.0, 3.0, 99.0], [True, True, True, False]) == [0.0, 1.0, 0.5, None]  # 99 는 눈금 밖


def _player_rows():
    """선수 지표 표 → {spId: (출전 글자, 출전 툴팁, 공격력 배경, 수비력 배경, 포지션 글자색)}."""
    tb = _win.tbl_players
    out = {}
    for r in range(tb.rowCount()):
        sid = tb.item(r, 1).data(Qt.ItemDataRole.UserRole)
        out[sid] = (tb.item(r, 3).text(), tb.item(r, 3).toolTip(),
                    tb.item(r, 5).background().style() != Qt.BrushStyle.NoBrush,
                    tb.item(r, 6).background().style() != Qt.BrushStyle.NoBrush,
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
    from PyQt6.QtGui import QFontMetrics
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
    real_colors, real_note = widgets._grade_badge_colors, crashlog.note

    def boom(grade):
        raise ValueError("배지 색 실패")
    widgets._grade_badge_colors, crashlog.note = boom, lambda kind, e: notes.append(kind)
    dg.failed = 0
    try:
        _win._go_page("선수 지표")
        for _ in range(2):
            tb.viewport().grab()
        assert dg.failed >= 2 and len(notes) == 1, (dg.failed, notes)
    finally:
        widgets._grade_badge_colors, crashlog.note = real_colors, real_note
        dg.failed = 0
        _win._go_page("대시보드")


# ── 9단계(1.3.1): ② 흐름 분석 띠·근거 막대 · ⑥ 히트맵·도넛·포메이션 막대 · 13 ELO · 다시 묻는 동의 ───────────
from dataclasses import replace  # noqa: E402
from PyQt6.QtGui import QFontMetrics  # noqa: E402
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


def test_analysis_result_dots_and_basis_bars():
    from analysis import Basis, Insight
    keep = app_main.core.narrate
    found = [Insight(app_main.core.SEC_WIN, "선제골을 넣으면 승률 80%.", "", 5.0,
                     Basis("선제골 넣은 경기", 80.0, 30, 50.0, 8)),
             Insight(app_main.core.SEC_WIN, "득점의 30%가 헤더에서 나옵니다.", "", 4.0),       # basis 없음 — 막대 없음
             Insight(app_main.core.SEC_LOSE, "천적 상대로 4경기 승률 25%.", "", 3.0,
                     Basis("천적 상대", 25.0, 4, 50.0, 6))]                                 # 표본 미달(일부러) — 흐림
    app_main.core.narrate = lambda *a, **k: found
    try:
        _win._narrate_key = None
        _win._render_analysis()
        assert len(_win.analysis_dots._items) == min(len(_win._matches), app_main.core.WINDOW)
        assert [r for r, _ in _win.analysis_dots._items] == [m.result for m in _win._matches[:20]][::-1], "오래된 것부터가 아니다"

        def bars(sec):
            box = _win.box_analysis[sec]
            return [box.itemAt(i).widget() for i in range(box.count())
                    if isinstance(box.itemAt(i).widget(), charts.HBarList)]
        win_bars, lose_bars = bars(app_main.core.SEC_WIN), bars(app_main.core.SEC_LOSE)
        assert len(win_bars) == 1 and len(win_bars[0]._rows) == 1, "basis 있는 문장만 막대로"
        name, v, right, tip, weak = win_bars[0]._rows[0]
        assert (name, v, weak) == ("선제골 넣은 경기", 80.0, False) and "전체 50.0%" in right and "30경기" in right, right
        assert lose_bars[0]._rows[0][4] is True, "표본 미달인데 흐리지 않았다"
        assert bars(app_main.core.SEC_FLOW) == [], "문장이 없는 섹션에 막대가 생겼다"
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
        for key in ("CHART_UP", "CHART_DOWN"):
            for s in range(11):
                bg = T.blend(p["PANEL"], p[key], charts.HEAT_MAX_MIX * s / 10)
                if T.contrast(p["TEXT"], bg) < 4.5:
                    bad.append((mode, key, s))
    assert not bad, bad
    assert charts.heat_cell_color(50.0) == T.PANEL
    assert charts.heat_cell_color(90.0) == T.blend(T.PANEL, T.CHART_UP, charts.HEAT_MAX_MIX)
    assert charts.heat_cell_color(10.0) == T.blend(T.PANEL, T.CHART_DOWN, charts.HEAT_MAX_MIX)
    assert charts.heat_cell_color(62.5) == T.blend(T.PANEL, T.CHART_UP, 0.5 * charts.HEAT_MAX_MIX)


def test_donut_groups_tail_into_other():
    counts = [(f"유형{i}", 10 - i) for i in range(8)] + [("없음", 0)]
    segs = charts.donut_segments(counts)
    assert len(segs) == charts.DONUT_MAX and segs[-1][0] == "기타", segs
    assert segs[-1][1] == sum(10 - i for i in range(5, 8)) and segs[-1][2] == T.CHART_NEUTRAL
    assert [s[2] for s in segs[:5]] == list(T.CHART_CATS)
    assert sum(v for _, v, _ in segs) == sum(v for _, v in counts)
    few = charts.donut_segments([("가", 3), ("나", 5)])
    assert [s[0] for s in few] == ["나", "가"] and "기타" not in [s[0] for s in few]


def test_tactics_formation_bars_and_type_donuts():
    _win._render_tactics(_win._details)
    bars = _win.opp_formation_bars
    opp = app_main.core.formation_stats(_win._details, _win._ouid)
    assert [r[0] for r in bars._rows] == [f.formation for f in opp]
    assert all(r[4] == (f.games < app_main.core.MIN_COND) for r, f in zip(bars._rows, opp))
    assert set(_win.type_donuts) == {"gf", "ga"}
    rb = app_main.core.result_breakdown(_win._details, _win._ouid)
    assert sum(v for _, v, _ in _win.type_donuts["gf"]._segs) == sum(rb.goal_types.values())


# ── 13 ELO ──

def _elo_rows(points):
    """[(ISO 시각, elo, rank, source)] → elo_history 줄 모양."""
    return [{"taken_at": t, "elo": e, "rank": r, "source": s, "profile_sn": 1, "nickname": "n"} for t, e, r, s in points]


def test_elo_season_start_rules():
    from datetime import date as _d
    now = datetime(2026, 10, 5, 12, 0)
    seasons = [sn.Season(89, "시즌 2", _d(2026, 7, 1), _d(2026, 8, 1)), sn.Season(90, "시즌 3", _d(2026, 8, 1), _d(2026, 9, 10))]
    assert app_main.elo_season_start(seasons, "2026-09-20T00:00:00", now) == datetime(2026, 9, 10)
    assert app_main.elo_season_start([], "2026-09-20T03:00:00", now) == datetime(2026, 9, 20, 3)
    assert app_main.elo_season_start([], None, now) == now - timedelta(days=config.ELO_FALLBACK_DAYS)
    # 아직 안 끝난 시즌(미래 종료일)은 시작으로 안 쓴다
    fut = seasons + [sn.Season(91, "시즌 4", _d(2026, 9, 10), _d(2026, 11, 1))]
    assert app_main.elo_season_start(fut, None, now) == datetime(2026, 9, 10)


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
    keep = config.NOTICE_ACCEPTED
    try:
        for acc, needed, pending, track in [(0, True, False, False), (1, True, False, False),
                                            (2, False, True, False), (3, False, True, True),
                                            (4, False, True, True), (5, False, False, True)]:
            config.NOTICE_ACCEPTED = acc
            assert (config.notice_needed(), config.notice_update_pending(), config.track_allowed()) == \
                (needed, pending, track), acc
        # 1.4.1: 4 = 거래 기록·시세 자동 읽기 — 옛 동의(2·3)는 막지 않고 다시 묻기만, 자동 시세만 4 뒤부터
        # 2.1.1: 5 = 축구장 칩 카드 정보 자동 읽기(랭커 픽은 16단계가 같은 5 를 쓴다)
        assert (config.NOTICE_VERSION, config.CHIP_NOTICE_VERSION, config.PRICE_NOTICE_VERSION,
                config.TRACK_NOTICE_VERSION, config.NOTICE_BASE_VERSION) == (5, 5, 4, 3, 2)
    finally:
        config.NOTICE_ACCEPTED = keep


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


def test_dialog_origin_centers_and_clamps():
    # 메인 창 가운데 · 모니터 밖으로 나가면 안으로
    assert app_main.dialog_origin((2000, 100, 1200, 800), (1920, 0, 1920, 1040), 400, 300) == (2400, 350)
    assert app_main.dialog_origin((3500, 900, 400, 200), (1920, 0, 1920, 1040), 600, 500) == (3240, 540)
    assert app_main.dialog_origin((-50, -50, 300, 200), (0, 0, 1920, 1040), 600, 500) == (0, 0)


def test_dialog_follows_minimized_main_window():
    """윈도우는 최소화된 창의 좌표를 화면 밖(-32000)으로 보낸다 — 그때도 창이 돌아올 자리 위에(2026-10-06 사용자).
    offscreen 은 이 좌표를 흉내 내지 않아 가짜로 만든다."""
    from PyQt6.QtCore import QRect
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
        """지금 키로 다 받은 상태 + 산 (카드, 강화)."""
        c = store.open_db(config.DB_PATH)
        store.save_trades(c, "buy", [{"tradeDate": "2026-09-30T10:00:00", "saleSn": f"b{i}", "spid": s,
                                      "grade": g, "value": 100} for i, (s, g) in enumerate(bought)])
        store.save_trades(c, "sell", [{"tradeDate": "2026-09-29T10:00:00", "saleSn": "s0", "spid": 1,
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
            assert "시세 없음" in _win.lb_ledger["recent"].text(), _win.lb_ledger["recent"].text()  # 09-30 구매 = 최근 구매
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
        assert "마지막 거래 9월 30일" in status and "구매 2 · 판매 1건" in status, status
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
        sp_id=sp, prices={0: "-", 1: "308,000 BP", 8: "17,400,000 BP"})
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
        sp_id=sp, name="가", position="CM", ovr=119, salary=30, prices={1: "1,000 BP"})
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
        return playerinfo.PlayerInfo(sp_id=sp, ovr=100, salary=20, prices={1: "2,000 BP"})
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

    def __init__(self, api):
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
    g = _win.grid_pass
    assert g.rowCount() == 1 + len(st_mod.PASS_KINDS), g.rowCount()
    assert g.itemAtPosition(1, 0).widget().text() == "짧은 패스"
    # 이긴/진 경기 칸은 표본(MIN_COND) 미만이면 흐림 — 픽스처는 몇 경기뿐이라 흐리다
    cell = g.itemAtPosition(1, 4).widget()
    assert app_main.T.TEXT_DIM in cell.styleSheet() and cell.toolTip(), cell.styleSheet()


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
            assert _win.tbl_pick_colors.rowCount() == 1 and _win.tbl_pick_cards.rowCount() > 0
            assert "3명 중 1명" in _win.lb_pick_summary.text(), _win.lb_pick_summary.text()
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
        names = [_win.tbl_pick_colors.item(i, 0).text() for i in range(_win.tbl_pick_colors.rowCount())]
        assert names == ["나", "다", "가"], names


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
            assert "1명뿐" in _win.lb_rec_status.text() and _win.tbl_rec_cards.rowCount() == 0, _win.lb_rec_status.text()
            assert _win.tbl_rec_stand.rowCount() == 0, "문턱 미만인데 내 위치를 냈다"
            config.RECOMMEND_MIN_RANKERS = config.RECOMMEND_MIN_USERS = 1
            _win._invalidate("recommend")
            rec = _win.rec
            assert rec.enough and rec.by_source[core.SRC_PICK] == 1, rec
            mine = {p.get("spId") for p in _win._my_latest_starters()}
            shown = [_win.tbl_rec_cards.item(r, 1).data(app_main.Qt.ItemDataRole.UserRole)
                     for r in range(_win.tbl_rec_cards.rowCount())]
            assert shown and not (set(shown) & mine), "내가 이미 쓰는 카드를 추천했다"
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
        assert src == {1000: store.RECOMMEND, 701: store.PICK, 702: store.PICK}, src
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
            assert "모으는 중 (1/2)" in _win.lb_rtrend_note.text(), _win.lb_rtrend_note.text()
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
        assert _win.cb_owner_card.count() == 0, "2글자부터"
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

import watchdog  # noqa: E402 — 테스트 하나마다 시간 한도(멈추면 실패 + 호출 스택)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
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
    _shots()
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
