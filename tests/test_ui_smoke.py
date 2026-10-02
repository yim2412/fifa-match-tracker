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
import sys
import tempfile
import threading

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
import crashlog  # noqa: E402
import models  # noqa: E402
import nexon_api  # noqa: E402
import playerinfo  # noqa: E402
import ranker  # noqa: E402
import requests  # noqa: E402
import seasons as sn  # noqa: E402
import store  # noqa: E402
import stats as st_mod  # noqa: E402
import updatecheck  # noqa: E402
import theme as T  # noqa: E402

_DIR = os.path.join(_ROOT, "tests", "fixtures")
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
_seed = store.open_db(config.DB_PATH)
try:
    from datetime import date as _date
    store.save_seasons(_seed, [sn.Season(no=90, name="시즌 9", start=_date(2025, 12, 1),
                                         end=_date(2026, 2, 1))])
finally:
    _seed.close()
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

    NAMES = ("_render_dashboard", "_render_opponents", "_render_synergy", "_render_teamcolor_tabs")

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
        assert c.n["_render_synergy"] == 0 and c.n["_render_teamcolor_tabs"] == 0, c.n
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


def test_narrate_once_per_scope_and_recomputed_when_scope_changes():
    calls = []
    real = app_main.analysis.narrate

    def spy(m, d, ouid, *a, **k):
        calls.append(len(m))
        return real(m, d, ouid, *a, **k)

    # 경기를 빼고 그리면 '최근 N일' 칸의 범위가 줄어든 채 남는다 — 뒤 테스트(승률 평균)가 달라졌다
    saved = _win._matches, _win._details, _win.sp_trend_days.value()
    app_main.analysis.narrate = spy
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
        app_main.analysis.narrate = real
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
        for i in range(_win.pages.count()):
            frame = _win.pages.widget(i)
            inner = frame.widget().minimumSizeHint().width()
            assert frame.width() >= inner, (i, frame.width(), inner)
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
    _win.sp_synergy_min.setValue(1)
    _win._render_synergy(_win._details)
    tables = [t for t in _win.findChildren(widgets.FitTableWidget)]
    assert len(tables) >= 11, len(tables)  # 12번째(포지션 선수 다이얼로그)는 열 때 생긴다
    try:
        for size in (app_main.MIN_WINDOW, (1600, 900)):
            _at_size(*size)
            bad = []
            for tb in tables:
                if tb.rowCount() == 0:
                    continue
                page = next((n for n, i in _win._page_index.items()
                             if _win.pages.widget(i).isAncestorOf(tb)), None)
                if page:
                    _win._go_page(page)
                    _app.processEvents()
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
        _win.sp_synergy_min.setValue(20)
        _win._render_synergy(_win._details)
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
        _win._go_page("대시보드")
        _app.processEvents()
        QTest.mouseClick(c, Qt.MouseButton.LeftButton)
        _app.processEvents()
        assert _win.pages.currentIndex() == _win._page_index[c.target], \
            (c.title.text(), c.target)
    _win._go_page("대시보드")


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

    def __init__(self, prefill: bytes = b""):
        self.prefill = prefill
        self.notified = []

    def __enter__(self):
        self._dir = pathlib.Path(tempfile.mkdtemp())
        self._saved = (config.DATA_DIR, sys.excepthook, threading.excepthook,
                       app_main.QMessageBox.warning)
        if self.prefill:
            (self._dir / "logs").mkdir()
            (self._dir / "logs" / crashlog.LOG_NAME).write_bytes(self.prefill)
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

        def show(self):
            calls.append("show")

        def start_update_check(self):
            calls.append("새 버전 확인")

        def start_cache_prune(self):
            calls.append("캐시 정리")

        def open_last_account(self):
            calls.append("마지막 계정")
            raise _Stop()  # 이 뒤는 app.exec() — 실제로 들어가면 멈춘다

    orig = app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY
    app_main._setup_app, app_main.MainWindow = (lambda app: None), _Win
    config.NOTICE_ACCEPTED = config.NOTICE_VERSION  # 안내는 이미 동의한 상태
    # 키도 있는 상태 — 이 PC 엔 실제 키가 있어 지나갔지만 키 없는 CI 에선 키 창이 떴다(2026-10-02)
    config.API_KEY = "test_key"
    try:
        app_main.main()  # 확인 호출이 빠지면 app.exec() — 모달 차단이 바로 실패시킨다
    except _Stop:
        pass
    finally:
        app_main._setup_app, app_main.MainWindow, config.NOTICE_ACCEPTED, config.API_KEY = orig
    assert calls == ["창", "show", "새 버전 확인", "캐시 정리", "마지막 계정"], calls


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
        # 이미 동의한 버전보다 안내가 새로우면 다시 묻는다 — v0.3.0 사용자(.env 에 값 없음 = 0)
        config.NOTICE_ACCEPTED, config.API_KEY = config.NOTICE_VERSION - 1, ""
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
    orig = updatecheck.check
    page0 = _win.stack.currentIndex()
    _win.stack.setCurrentIndex(_win.PAGE_SEARCH)  # 최신 카드는 첫 검색 화면에서만 뜬다
    try:
        for status, rel, want, card_shown in [
                (updatecheck.LATEST, None, ("최신 버전입니다", None), True),     # 카드 자리에도(검색 화면)
                (updatecheck.UNKNOWN, None, ("업데이트 확인 못 함", None), False),  # 모르면 '최신'이라 안 한다
                (updatecheck.NEWER, _REL, ("새 버전 v9.0.0", "받으러 가기"), True)]:
            card.hide()
            updatecheck.check = lambda *a, s=status, r=rel, **k: (s, r)
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
        updatecheck.check = orig
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
        assert (ctx.logs / crashlog.LOG_NAME).stat().st_size < len(big)


# ── 넥슨 웹 데이터 스위치 ─────────────────────────────────────────────
class _Sent(Exception):
    pass


def _web_calls():
    return [
        ("ranker", lambda: ranker.fetch_manager_rank("닉"), ranker.RankerError),
        ("rank page", lambda: ranker.fetch_rank_page(1), ranker.RankerError),
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
    """RankListLoader.run 을 스레드 없이 돌린다 — pages: {쪽: 행들 | None(실패)}."""
    orig = ranker.fetch_rank_page, ranker.RANK_PAGES

    def fake(page, timeout=10):
        rows = pages[page]
        if rows is None:
            raise ranker.RankerError("못 읽음")
        return rows

    ranker.fetch_rank_page, ranker.RANK_PAGES = fake, len(pages)
    got, done = {}, []
    try:
        ld = app_main.RankListLoader(set(wanted))
        ld.loaded_many.connect(got.update)
        ld.finished_all.connect(lambda: done.append(True))
        ld.run()
    finally:
        ranker.fetch_rank_page, ranker.RANK_PAGES = orig
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
            self.loaded_many = self.progress = self.finished_all = self

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
    finally:
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
    orig = app_main.st.opponent_position_players, _win.sp_to.value(), _win._show_teamcolor_detail
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
        app_main.st.opponent_position_players = spy
        _win._show_teamcolor_detail = lambda *a, **k: None
        _win._on_position_color_changed(0)
        _win._on_teamcolor_double_clicked(_win.tbl_teamcolor_rank.item(0, 1))
        assert seen == [len(_win._details)] * 2, (seen, len(_win._details))
    finally:
        app_main.st.opponent_position_players = orig[0]
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
                           ("FIFA_WEB_DATA=1\nFIFA_NOTICE=1\n", "True False"),
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
            assert f"FIFA_WEB_DATA={int(tick_web)}" in lines and "FIFA_NOTICE=1" in lines, lines
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
        assert _win._restore == {"page": "슛 맵", "season": want_season}, _win._restore
        # 처음 그리는 계정이면 그 메뉴·시즌으로
        _win._ouid, _win._season_picked = "", False
        _win._on_loaded(_MATCHES, _DETAILS, _OUID, {"nickname": "테스트구단주", "level": 7},
                        {}, {}, 0, len(_MATCHES), None, "-", False, "", {}, {})
        assert _win._current_page_name() == "슛 맵", _win._current_page_name()
        assert _win._season_key(_win.cb_season.currentData()) == want_season, _win.cb_season.currentText()
        assert _win._restore == {}, "한 번 쓴 복원값이 남아 다음 계정에도 적용된다"
        # 닫을 때 저장하는 배선 — closeEvent 가 _save_settings 를 부른다
        called = []
        _win._save_settings = lambda: called.append(1)
        try:
            from PyQt6.QtGui import QCloseEvent
            _win.closeEvent(QCloseEvent())
        finally:
            del _win._save_settings
        assert called == [1], "닫을 때 설정을 저장하지 않는다"
    finally:
        _win.resize(saved_state[0])
        _win._restore = {}
        _win._season_picked = saved_state[3]
        _win.cb_season.setCurrentIndex(saved_state[4])
        _win._go_page("대시보드")
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
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["카드 정보", "내 기록"]
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
    saved = st_mod.PLAYER_TREND_MIN_SHOTS
    st_mod.PLAYER_TREND_MIN_SHOTS = 1
    try:
        rec2 = _win._build_my_record(shooter.sp_id)
    finally:
        st_mod.PLAYER_TREND_MIN_SHOTS = saved
    trend2 = [x for x in rec2.findChildren(app_main.QWidget) if x.objectName() == "myRecordTrend"][0]
    assert isinstance(trend2, charts.AreaTrendChart), type(trend2)


def test_search_hands_current_account_to_loader():
    got = []

    class _Rec:
        def __init__(self, api, nick, mt, prev=None):
            got.append(prev)
            self.progress = self.finished_ok = self.failed = self.key_invalid = self.quota_hit = self

        def connect(self, *_):
            pass

        def start(self):
            pass

        def isRunning(self):
            return False

    orig = app_main.MatchLoader, _win._loader
    app_main.MatchLoader, _win._loader = _Rec, None
    try:
        _win._api_search("아무개")
    finally:
        app_main.MatchLoader, _win._loader = orig
        _win._set_busy(False)
    assert len(got) == 1 and got[0][0] == _OUID, got
    assert got[0][1] is _win._matches_all and got[0][2] is _win._details_all, "지금 가진 목록을 넘기지 않았다"


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


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
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
