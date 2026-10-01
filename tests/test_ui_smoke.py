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
