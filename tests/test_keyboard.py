"""키보드만으로 조작(96 K3) — `python tests/test_keyboard.py` 로 실행(offscreen · 네트워크 없음).

잰 것:
- Tab 으로 온 포커스에만 테두리가 그려진다(마우스로 누른 단추엔 없다) — 픽셀로
- Tab 으로 들어간 목록은 지금 줄이 표시된다(안 그러면 Enter 가 무엇을 여는지 안 보인다)
- 대화상자의 Enter 는 정해 둔 단추만 누른다 — 정하지 않은 창에선 Qt 가 포커스 다음 단추를 골라 엉뚱한 것을 눌렀다
  (2026-11-10 실측: 키 창에서 키를 넣고 Enter → [키 없이 둘러보기])
- 목록은 Enter 로 열리고 더블클릭 한 번은 한 번만 연다(activated — doubleClicked 와 같이 이으면 두 번 열린다)
보호 장치마다 "장치를 끄면 무엇이 일어나는지"를 먼저 단언한다(끄고도 같으면 그 테스트는 아무것도 안 잰다).
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["FIFA_DATA_DIR"] = tempfile.mkdtemp()   # 실제 데이터 폴더를 안 연다
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402
from PySide6.QtCore import QPoint, QThread, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QLineEdit, QPushButton, QVBoxLayout,  # noqa: E402
                               QWidget)

QThread.start = lambda self, *a, **k: None   # 백그라운드 조회 차단

import app_main  # noqa: E402
import theme as T  # noqa: E402
import widgets  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
app_main._setup_app(_app)


def _aid() -> widgets.KeyboardAid:
    aid = widgets.keyboard_aid()
    assert aid is not None, "_setup_app 이 키보드 도우미를 안 걸었다"
    return aid


class _off:
    """도우미를 잠시 끈다 — '장치가 없으면 무엇이 일어나나' 를 먼저 재려고."""
    def __enter__(self):
        _aid().enabled = False
        widgets.hide_focus_ring()

    def __exit__(self, *a):
        _aid().enabled = True


def _pump() -> None:
    for _ in range(3):
        _app.processEvents()


def _img(w: QWidget) -> QImage:
    _pump()
    return w.grab().toImage()


def _region(img: QImage, w: QWidget, top: QWidget) -> QImage:
    p = w.mapTo(top, QPoint(0, 0))
    dpr = img.devicePixelRatio()
    return img.copy(int(p.x() * dpr), int(p.y() * dpr), int(w.width() * dpr), int(w.height() * dpr))


def _two_buttons():
    top = QWidget()
    v = QVBoxLayout(top)
    a, b = QPushButton("하나"), QPushButton("둘")
    v.addWidget(a)
    v.addWidget(b)
    top.resize(240, 120)
    top.show()
    top.activateWindow()
    _pump()
    return top, a, b


def test_tab_focus_draws_ring():
    """Tab 으로 온 단추는 그림이 달라지고, 마우스로 누른 단추는 그대로다."""
    top, a, b = _two_buttons()
    a.setFocus(Qt.FocusReason.MouseFocusReason)
    plain = _region(_img(top), b, top)
    # 장치가 없으면 — Qt 는 단추 안쪽에 아주 흐린 칸만 그린다(어두운 바탕에서 거의 안 보인다 · 2026-11-10 그림).
    # 그 그림과 달라야 테두리가 그려진 것이다
    with _off():
        a.setFocus(Qt.FocusReason.MouseFocusReason)
        QTest.keyClick(a, Qt.Key.Key_Tab)
        assert _app.focusWidget() is b
        qt_only = _region(_img(top), b, top)
    a.setFocus(Qt.FocusReason.MouseFocusReason)
    QTest.keyClick(a, Qt.Key.Key_Tab)
    assert _app.focusWidget() is b
    ringed = _region(_img(top), b, top)
    assert ringed != qt_only and ringed != plain, "Tab 으로 왔는데 테두리가 없다"
    edge = ringed.pixelColor(ringed.width() // 2, 1)   # 위 변 가운데 — 테두리 자리
    want = QColor(T.ACCENT)
    assert max(abs(edge.red() - want.red()), abs(edge.green() - want.green()), abs(edge.blue() - want.blue())) < 40, edge.name()
    # 마우스로 누르면 테두리가 사라진다(누를 때마다 남으면 눌린 것처럼 보인다)
    QTest.mouseClick(a, Qt.MouseButton.LeftButton)
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert _app.focusWidget() is b
    assert _region(_img(top), b, top) == plain, "마우스로 누른 단추에 테두리가 남았다"
    top.close()
    top.deleteLater()


def test_ring_survives_target_deleted():
    """테두리를 단 위젯이 지워져도(창 닫기) 다음 Tab 에서 다시 그린다."""
    top, a, b = _two_buttons()
    a.setFocus(Qt.FocusReason.MouseFocusReason)
    QTest.keyClick(a, Qt.Key.Key_Tab)
    _pump()
    top.close()
    top.deleteLater()
    QTest.qWait(10)
    _pump()
    top2, a2, b2 = _two_buttons()
    a2.setFocus(Qt.FocusReason.MouseFocusReason)
    plain = _region(_img(top2), b2, top2)
    QTest.keyClick(a2, Qt.Key.Key_Tab)
    assert _region(_img(top2), b2, top2) != plain
    top2.close()
    top2.deleteLater()


def _rate_list():
    top = QWidget()
    v = QVBoxLayout(top)
    btn = QPushButton("앞")
    lst = widgets.RateRowList()
    lst.set_rows([widgets.RateRow(label=f"줄 {i}", win=i + 1, lose=1) for i in range(4)])
    v.addWidget(btn)
    v.addWidget(lst, 1)
    top.resize(420, 360)
    top.show()
    top.activateWindow()
    _pump()
    return top, btn, lst


def test_tab_into_list_marks_current_row():
    """Tab 으로 목록에 들어가면 Qt 는 첫 줄을 '지금 줄' 로만 두고 표시하지 않는다 — Enter 가 무엇을 여는지 보이게 고른다."""
    with _off():
        top, btn, lst = _rate_list()
        btn.setFocus(Qt.FocusReason.MouseFocusReason)
        QTest.keyClick(btn, Qt.Key.Key_Tab)
        _pump()
        assert _app.focusWidget() is lst and lst.currentIndex().isValid()
        assert not lst.selectionModel().hasSelection(), "Qt 가 이미 줄을 고른다 — 이 테스트는 아무것도 안 잰다"
        top.close()
        top.deleteLater()
    top, btn, lst = _rate_list()
    btn.setFocus(Qt.FocusReason.MouseFocusReason)
    QTest.keyClick(btn, Qt.Key.Key_Tab)
    _pump()
    assert lst.selectionModel().isSelected(lst.currentIndex()), "Tab 으로 들어간 목록에 고른 줄이 없다"
    top.close()
    top.deleteLater()


def test_list_enter_and_double_click_open_once():
    """앱의 목록 연결(activated)은 Enter 로 열리고, 더블클릭 한 번은 한 번만 연다."""
    top, btn, lst = _rate_list()
    hits = []
    lst.activated.connect(lambda idx: hits.append(idx.row()))
    lst.setFocus(Qt.FocusReason.TabFocusReason)
    lst.setCurrentIndex(lst.model().index(2, 0))
    QTest.keyClick(lst, Qt.Key.Key_Return)
    assert hits == [2], hits
    hits.clear()
    pos = lst.visualRect(lst.model().index(1, 0)).center()
    vp = lst.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=pos)
    _pump()
    assert hits == [1], f"더블클릭 한 번에 {hits}"
    top.close()
    top.deleteLater()


def _plain_dialog():
    d = QDialog()
    v = QVBoxLayout(d)
    ed = QLineEdit()
    v.addWidget(ed)
    a, b = QPushButton("가"), QPushButton("나")
    v.addWidget(a)
    v.addWidget(b)
    clicks = []
    a.clicked.connect(lambda: clicks.append("가"))
    b.clicked.connect(lambda: clicks.append("나"))
    return d, ed, a, b, clicks


def test_dialog_without_default_ignores_enter_in_edit():
    """기본 단추를 정하지 않은 창 — 글 칸에서 Enter 는 아무 단추도 안 누른다. 포커스가 단추에 있으면 그 단추."""
    with _off():
        d, ed, a, b, clicks = _plain_dialog()
        d.show()
        ed.setFocus()
        QTest.keyClick(ed, Qt.Key.Key_Return)
        assert clicks == ["가"], f"장치 없이 Qt 가 고른 단추가 달라졌다 {clicks} — 이 테스트의 전제가 틀렸다"
        d.close()
        d.deleteLater()
    # 처음부터 글 칸에 포커스 — 띄우는 도중 포커스가 오고 Qt 는 그 **뒤에** 기본 단추를 고른다(포커스를 옮기지 않으니 떼는 장치가 못 본다)
    d, ed, a, b, clicks = _plain_dialog()
    d.show()
    d.activateWindow()
    _pump()
    assert _app.focusWidget() is ed, type(_app.focusWidget()).__name__
    QTest.keyClick(ed, Qt.Key.Key_Return)
    _pump()
    assert clicks == [], f"띄우자마자 Enter 가 {clicks} 를 눌렀다"
    ed.setFocus()
    QTest.keyClick(ed, Qt.Key.Key_Return)
    _pump()
    assert clicks == [], f"정하지 않은 창에서 Enter 가 {clicks} 를 눌렀다"
    b.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(b, Qt.Key.Key_Return)
    assert clicks == ["나"], clicks
    ed.setFocus()
    QTest.keyClick(ed, Qt.Key.Key_Return)
    assert clicks == ["나"], f"단추를 지나온 뒤 글 칸 Enter 가 {clicks}"
    d.close()
    d.deleteLater()


def test_key_dialog_enter_submits_once():
    """키 창 — 키를 넣고 Enter 는 [확인] 한 번(예전엔 [키 없이 둘러보기] 가 눌렸다)."""
    d = app_main.ApiKeyDialog(allow_demo=True)
    calls = []
    d._on_submit = lambda: calls.append(1)   # 넥슨에 묻지 않게 — 연결은 이름으로 찾지 않으니 단추 신호를 다시 잇는다
    d.btn_ok.clicked.disconnect()
    d.btn_ok.clicked.connect(d._on_submit)
    d.show()
    d.ed_key.setFocus()
    d.ed_key.setText("test_abc")
    QTest.keyClick(d.ed_key, Qt.Key.Key_Return)
    _pump()
    assert d.result() != app_main.ApiKeyDialog.DEMO_RESULT and d.isVisible(), "Enter 가 둘러보기를 눌렀다"
    assert calls == [1], f"Enter 한 번에 확인 {len(calls)}번"
    d.close()
    d.deleteLater()


def test_intro_enter_goes_next_then_starts():
    d = app_main.IntroDialog()
    d.show()
    _pump()
    QTest.keyClick(d, Qt.Key.Key_Return)
    assert d.view.pages.currentIndex() == 1 and d.isVisible()
    QTest.keyClick(d, Qt.Key.Key_Return)
    assert d.view.at_last() and d.isVisible()
    QTest.keyClick(d, Qt.Key.Key_Return)
    _pump()
    assert not d.isVisible() and d.result() == QDialog.DialogCode.Accepted
    d.deleteLater()


def test_notice_enter_never_closes():
    """안내 동의 — Enter 는 [시작] 만(동의 전엔 아무것도) · [닫기] 를 누르지 않는다."""
    orig = app_main.config.accept_notice
    app_main.config.accept_notice = lambda web: None
    try:
        d = app_main.NoticeDialog()
        d.show()
        _pump()
        QTest.keyClick(d, Qt.Key.Key_Return)
        _pump()
        assert d.isVisible(), "동의 전 Enter 가 창을 닫았다"
        d.chk_agree.setChecked(True)
        QTest.keyClick(d, Qt.Key.Key_Return)
        _pump()
        assert not d.isVisible() and d.result() == QDialog.DialogCode.Accepted
        d.deleteLater()
    finally:
        app_main.config.accept_notice = orig


def test_about_enter_in_nickname_does_not_open_folder():
    """[정보] 연결 점검 닉네임 칸 Enter — 점검만(예전엔 포커스 다음 단추 [데이터 폴더 열기] 도 눌렸다)."""
    d = app_main.AboutDialog()
    opened = []
    d.btn_folder.clicked.disconnect()
    d.btn_folder.clicked.connect(lambda: opened.append(1))
    d.show()
    _pump()
    d.ed_check_nick.setFocus()
    QTest.keyClick(d.ed_check_nick, Qt.Key.Key_Return)
    _pump()
    assert opened == [] and d.isVisible(), "닉네임 칸 Enter 가 다른 단추를 눌렀다"
    d.close()
    d.deleteLater()


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
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
