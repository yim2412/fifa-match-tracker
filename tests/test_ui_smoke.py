"""화면 스모크 — offscreen 으로 메인 창을 띄워 메뉴·페이지 배선을 확인한다.

네트워크 없이 돈다: QThread.start 를 막아 이미지·팀컬러 등 백그라운드 조회가
안 나가게 하고, 픽스처 4경기를 _on_loaded 에 직접 넣는다.
`python tests/test_ui_smoke.py` 로 실행. UI_SHOT=<폴더> 를 주면 메뉴마다
화면을 PNG 로 떠 둔다(눈으로 확인용 — 커밋하지 않는다).
"""
from __future__ import annotations

import json
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PyQt6.QtCore import QEvent, QThread, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QThread.start = lambda self, *a, **k: None  # 백그라운드 조회 차단

import app_main  # noqa: E402
import models  # noqa: E402
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
    # 픽스처: 승1 무1 패1 + 오류1 — 요약 카드가 실제로 채워졌는지
    assert _win.card_record.value.text() not in ("-", ""), _win.card_record.value.text()
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
    assert _win.card_record.cap.text() == "전적", _win.card_record.cap.text()


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


def test_ranker_tiles_shrink_to_fit():
    import widgets
    card = widgets.RankerCard()
    assert all(isinstance(v, widgets.FitLabel) for v in card._vals.values())


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
