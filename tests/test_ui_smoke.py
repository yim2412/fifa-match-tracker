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
import theme as T  # noqa: E402

_DIR = os.path.join(_ROOT, "tests", "fixtures")
_app = QApplication.instance() or QApplication(sys.argv)
T.apply(_app)


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


_win = app_main.MainWindow(_NoApi())
_win.resize(1600, 900)
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


def test_setfont_sizes_survive_stylesheet():
    # QSS 의 QWidget 규칙에 font-size 가 있으면 코드의 setFont 가 전부 눌린다
    # (다크 테마 시절 실제로 그랬다 — 30pt 제목이 15px 로 보였다).
    from PyQt6.QtWidgets import QLabel
    title = [lb for lb in _win.stack.widget(_win.PAGE_SEARCH).findChildren(QLabel)
             if lb.text() == "FC ONLINE"]
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

    saved = (config.WEB_DATA, app_main.TeamColorLoader, dict(_win._team_colors),
             [lb.text() for lb in _win._teamcolor_status_labels])
    config.WEB_DATA, app_main.TeamColorLoader = False, _NoLoader
    _win._team_colors.clear()
    try:
        app_main.MainWindow._on_fetch_team_colors(_win)  # 모듈 위쪽에서 인스턴스 쪽을 막아 뒀다
        texts = {lb.text() for lb in _win._teamcolor_status_labels}
        assert texts == {config.WEB_DATA_OFF_MSG}, texts
    finally:
        config.WEB_DATA, app_main.TeamColorLoader = saved[0], saved[1]
        _win._team_colors.update(saved[2])
        for lb, t in zip(_win._teamcolor_status_labels, saved[3]):
            lb.setText(t)


# ── API 키 입력 ───────────────────────────────────────────────────────
class _TempEnv:
    """config.ENV_PATH·API_KEY 를 임시 폴더로 돌려 실제 .env 를 안 건드린다."""

    def __enter__(self):
        self._dir = tempfile.mkdtemp()
        self._saved = (config.ENV_PATH, config.API_KEY, os.environ.get(config.API_KEY_VAR))
        config.ENV_PATH = pathlib.Path(self._dir) / ".env"
        return config.ENV_PATH

    def __exit__(self, *exc):
        config.ENV_PATH, config.API_KEY, env = self._saved
        if env is None:
            os.environ.pop(config.API_KEY_VAR, None)
        else:
            os.environ[config.API_KEY_VAR] = env
        shutil.rmtree(self._dir, ignore_errors=True)


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
