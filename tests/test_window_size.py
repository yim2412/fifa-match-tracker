"""창 크기 — 화면에 맞춰 여는 규칙(1.0.3). `python tests/test_window_size.py` 로 실행.

FHD 150% 노트북(논리 1280x720)·1366x768 에서 1600x900 창이 화면 밖으로 넘쳤다. 규칙은 순수 함수
(`app_main.initial_window`)로 재고, 실제 창은 **흉내 낸 화면마다 별도 프로세스**로 띄워 잰다 — 화면 정의는
앱을 만들 때 정해져서 한 프로세스에서 바꿀 수 없다. 흉내 화면엔 작업 표시줄이 없으므로 작업 표시줄 높이는
순수 함수 쪽에서 넣어 잰다.

⚠ Qt offscreen 의 configfile 경로에 한글이 있으면 아무 출력 없이 죽는다(rc=127, 2026-10-04 실측 — 이 PC 는
사용자 폴더·프로젝트 폴더가 다 한글). 자식 프로세스는 tests/screens 로 가서 상대 경로로 준다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(_ROOT, "tests", "screens")
sys.path.insert(0, _ROOT)


# ── 자식 프로세스: 흉내 낸 화면에서 실제 창을 띄우고 잰 값을 JSON 한 줄로 ──────────────
def _child(case: str, screen: str, settings_path: str) -> None:
    os.chdir(SCREENS)
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={screen}.json"
    if os.path.isdir("C:/Windows/Fonts"):
        os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication, QDialog
    app = QApplication(sys.argv)
    QThread.start = lambda self, *a, **k: None  # 시즌표 등 백그라운드 조회 차단(네트워크 없음)
    import app_main
    import config
    import nexon_api
    config.SETTINGS_PATH = settings_path  # 실제 settings.ini 를 건드리지 않는다
    if case == "old":
        # 전제: 고치기 전 동작(화면과 무관하게 1600x900·최소 1280x720) — 이게 넘쳐야 아래 판정이 의미 있다
        app_main.initial_window = lambda w, h: app_main.WindowPlan(
            app_main.DEFAULT_WINDOW, False, app_main.MIN_WINDOW, False)
        # 띄운 뒤 재확인도 끈다 — 이게 따로 최대화로 구해 내서, 끄지 않으면 옛 동작이 재현되지 않았다(실측)
        app_main.MainWindow._check_fits = lambda self: None
    if case == "frame0":
        app_main.FRAME_ALLOWANCE = (0, 0)  # 추정이 틀린 경우 — 1280x720 화면에 1280x720 창(+테두리)이 '들어간다'고 판정
    api = nexon_api.FCOnlineAPI("k", cache_dir=None)

    def make():
        w = app_main.MainWindow(api)
        w._save_settings = lambda: None
        return w

    out: dict = {}
    if case == "upgrade":
        # 1.0.2 가 저장해 둔 1600x900 — 최소 크기를 풀고 그 크기로 저장한다
        old = make()
        old.setMinimumSize(0, 0)
        old.resize(1600, 900)
        old.show()
        app.processEvents()
        from PyQt6.QtCore import QSettings
        s = QSettings(settings_path, QSettings.Format.IniFormat)
        s.setValue("window/geometry", old.saveGeometry())
        s.sync()
        old.close()
    win = make()
    if case == "move":
        win.show_initial()
        app.processEvents()
        big_ok = win.width() == app_main.DEFAULT_WINDOW[0]
        win.move(1920 + 100, 50)  # 오른쪽 모니터(150%, 논리 1280x720)로
        for _ in range(5):
            app.processEvents()
        out["moved_from_default"] = big_ok
        out["screen"] = win.screen().name()
    else:
        win.show_initial()
        app.processEvents()
    if case == "unmax":
        win.showNormal()
        app.processEvents()
    if case == "dialogs":
        sizes = {}
        orig_exec = QDialog.exec

        def fake_exec(self):
            self.show()
            app.processEvents()
            sizes[self.windowTitle()] = [self.width(), self.height(),
                                         self.minimumSizeHint().width(), self.minimumSizeHint().height()]
            self.close()
            return 0

        QDialog.exec = fake_exec
        app_main.ImageLoader.start = lambda self: None
        app_main.SeasonIconLoader.start = lambda self: None
        players = [{"spId": 100000001 + i, "spPosition": p, "spGrade": 5}
                   for i, p in enumerate((0, 3, 5, 6, 7, 10, 14, 16, 18, 23, 25))]
        win._show_opponent_squad("상대", players, "2026-10-04", "승")
        d = QDialog(win)
        d.setWindowTitle("고정 600x760")
        app_main.fit_to_screen(d, 600, 760)
        d.exec()
        QDialog.exec = orig_exec
        out["dialogs"] = sizes
    if case == "about":
        from PyQt6.QtWidgets import QTabWidget
        import theme
        theme.apply(app)  # 앱 기본 글꼴 — 없으면 탭 폭이 실제의 절반(434 vs 837)이라 고정 620 도 통과했다
        d = app_main.AboutDialog(win)
        d.show()
        app.processEvents()
        bar = d.findChild(QTabWidget).tabBar()
        dg = d.frameGeometry()
        out["about"] = {"bar": bar.width(), "need": bar.sizeHint().width(),
                        "frame": [dg.x(), dg.y(), dg.width(), dg.height()]}
        d.close()
    if case == "dialog_screen":
        # 메인 창을 오른쪽 모니터로 옮긴 뒤 대화상자를 연다 — 정보 창이 다른 모니터에 떴다(2026-10-06 사용자)
        win.show_initial()
        app.processEvents()
        win.move(1920 + 100, 50)
        for _ in range(5):
            app.processEvents()
        mf = win.frameGeometry()
        placed = {}
        orig_exec = QDialog.exec

        def fake_exec(self):
            self.show()
            for _ in range(3):
                app.processEvents()
            f = self.frameGeometry()
            c = f.center()
            placed[self.windowTitle() or type(self).__name__] = {
                "screen": self.screen().name(), "center_in_main": mf.contains(c),
                "frame": [f.x(), f.y(), f.width(), f.height()]}
            self.close()
            return 0

        QDialog.exec = fake_exec
        app_main.ImageLoader.start = lambda self: None
        app_main.SeasonIconLoader.start = lambda self: None
        app_main.PlayerInfoLoader.start = lambda self: None
        app_main.AboutDialog(win).exec()
        app_main.ApiKeyDialog(win, reason="만료").exec()
        players = [{"spId": 100000001 + i, "spPosition": p, "spGrade": 5}
                   for i, p in enumerate((0, 3, 5, 6, 7, 10, 14, 16, 18, 23, 25))]
        win._show_opponent_squad("상대", players, "2026-10-04", "승")
        out["main_screen"] = win.screen().name()
        # 대화상자가 열린 채 메인 창만 다른 모니터로 옮겨진다(실제 윈도우 실측 — 안내 창이 주 모니터에 홀로 남았다)
        d = QDialog(win)
        d.setWindowTitle("따라오기")
        app_main.fit_to_screen(d, 400, 300)
        d.show()
        for _ in range(3):
            app.processEvents()
        win.move(100, 60)   # 왼쪽(100%) 모니터로
        for _ in range(5):
            app.processEvents()
        mf2 = win.frameGeometry()
        out["follow"] = {"main": win.screen().name(), "dialog": d.screen().name(),
                         "center_in_main": mf2.contains(d.frameGeometry().center())}
        d.close()
        win.move(1920 + 100, 50)
        for _ in range(5):
            app.processEvents()
        # 최소화된 채로 뜬 대화상자 — exe 를 최소화로 켜 2번 모니터로 옮기는 사이 '다시 묻는 안내'가 주 모니터에 떴다
        win.showMinimized()
        for _ in range(3):
            app.processEvents()
        d = QDialog(win)
        d.setWindowTitle("최소화 중")
        app_main.fit_to_screen(d, 400, 300)
        d.exec()
        # 다시 묻는 안내는 최소화 중엔 미뤘다가 창이 돌아오면 — 그때 메인 창 위에
        asked = []
        app_main.NoticeDialog.exec = lambda self: asked.append(win.isMinimized()) or 0
        app_main.config.NOTICE_ACCEPTED = app_main.config.NOTICE_BASE_VERSION
        win._notice_asked = False
        win.ask_notice_update_once()
        out["asked_while_min"] = list(asked)
        win.showNormal()
        for _ in range(5):
            app.processEvents()
        out["asked_after_restore"] = list(asked)
        QDialog.exec = orig_exec
        out["placed"] = placed
    scr = (win.screen() or app.primaryScreen()).availableGeometry()
    fg = win.frameGeometry()
    out.update({
        "avail": [scr.x(), scr.y(), scr.width(), scr.height()],
        "frame": [fg.x(), fg.y(), fg.width(), fg.height()],
        "size": [win.width(), win.height()],
        "maximized": win.isMaximized(),
        "min": [win.minimumWidth(), win.minimumHeight()],
        "msg": win.statusBar().currentMessage(),
    })
    print("RESULT " + json.dumps(out, ensure_ascii=False), flush=True)


def _run(case: str, screen: str, settings_path: str | None = None) -> dict:
    own = settings_path is None
    if own:
        fd, settings_path = tempfile.mkstemp(suffix=".ini")
        os.close(fd)
        os.remove(settings_path)
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", case, screen, settings_path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"))
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
        assert line, f"자식 프로세스가 결과를 못 냈다(rc={r.returncode}): {r.stderr[-800:]}"
        return json.loads(line[len("RESULT "):])
    finally:
        if own and os.path.exists(settings_path):
            os.remove(settings_path)


def _inside(res: dict) -> bool:
    ax, ay, aw, ah = res["avail"]
    fx, fy, fw, fh = res["frame"]
    return fx >= ax and fy >= ay and fx + fw <= ax + aw and fy + fh <= ay + ah


# ── 규칙(순수 함수) ─────────────────────────────────────────────────────────
def test_rules_by_usable_screen_size():
    import app_main as a
    # 쓸 수 있는 크기 = 배율 반영 해상도 − 작업 표시줄(윈도우 11 48px → 100% 48 · 125% 38 · 150% 32)
    cases = {
        "FHD 100%": ((1920, 1032), a.DEFAULT_WINDOW, False),
        "FHD 125%": ((1536, 826), a.MIN_WINDOW, False),
        "FHD 150%": ((1280, 688), None, True),
        "1366x768": ((1366, 720), None, True),
        "FHD 175%": ((1097, 589), None, True),
    }
    for name, ((w, h), size, maxed) in cases.items():
        p = a.initial_window(w, h)
        assert p.maximized is maxed, (name, p)
        if size:
            assert p.size == size, (name, p)
        fw, fh = a.FRAME_ALLOWANCE
        # 최대화로 열어도 '풀었을 때 크기'는 테두리까지 화면에 들어가야 한다(최대화를 풀면 이 크기가 된다)
        assert p.size[0] + fw <= w and p.size[1] + fh <= h, ("풀었을 때 크기가 화면을 넘는다", name, p)
        assert p.min_size[0] <= w and p.min_size[1] + fh <= h, ("최소 크기가 화면보다 크다", name, p)
    assert a.initial_window(1097, 589).narrow and not a.initial_window(1280, 688).narrow
    # 경계 1픽셀
    fw, fh = a.FRAME_ALLOWANCE
    dw, dh = a.DEFAULT_WINDOW
    mw, mh = a.MIN_WINDOW
    assert a.initial_window(dw + fw, dh + fh).size == a.DEFAULT_WINDOW
    assert a.initial_window(dw + fw - 1, dh + fh).size == a.MIN_WINDOW
    assert a.initial_window(dw + fw, dh + fh - 1).size == a.MIN_WINDOW
    assert not a.initial_window(mw + fw, mh + fh).maximized
    assert a.initial_window(mw + fw - 1, mh + fh).maximized
    assert a.initial_window(mw + fw, mh + fh - 1).maximized


def test_small_screen_min_height_bounds():
    import app_main as a
    # 아래: 레이아웃이 버티는 최소 높이(창 전체 434, 실측). 위: 640 을 넘으면 150% 노트북(창 안쪽 약 657)에서
    # 최대화가 실제로 먹히지 않는다.
    assert 434 <= a.MIN_HEIGHT_SMALL <= 640, a.MIN_HEIGHT_SMALL


# ── 실제 창(흉내 낸 화면, 별도 프로세스) ─────────────────────────────────────────
def test_old_behavior_overflows_small_screen():
    # 전제 — 고치기 전 규칙이면 150% 화면에서 넘친다. 이게 안 넘치면 아래 테스트들이 아무것도 재지 않는다.
    res = _run("old", "fhd150")
    assert not _inside(res), res


def test_first_run_on_each_screen():
    big = _run("first", "fhd100")
    assert big["size"] == [1600, 900] and not big["maximized"] and _inside(big), big
    ax, ay, aw, ah = big["avail"]
    fx, fy, fw, fh = big["frame"]
    assert abs((fx + fw / 2) - (ax + aw / 2)) <= 2 and abs((fy + fh / 2) - (ay + ah / 2)) <= 2, ("가운데가 아니다", big)
    mid = _run("first", "fhd125")
    assert mid["size"] == [1280, 720] and not mid["maximized"] and _inside(mid), mid
    # 흉내 화면엔 작업 표시줄이 없어 1366x768 은 높이 768 이 다 쓰인다 → 1280x720 이 들어간다(규칙대로).
    # 작업 표시줄이 있는 진짜 1366x768(높이 720)은 test_rules_by_usable_screen_size 가 잰다.
    lap = _run("first", "lap1366")
    assert lap["size"] == [1280, 720] and not lap["maximized"] and _inside(lap), lap
    for screen in ("fhd150", "fhd175"):
        res = _run("first", screen)
        assert res["maximized"] and _inside(res), (screen, res)
        assert res["min"][1] <= res["avail"][3], (screen, res)
    narrow = _run("first", "fhd175")
    assert "배율" in narrow["msg"], narrow


def test_narrow_notice_only_once():
    fd, path = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    os.remove(path)
    try:
        assert "배율" in _run("first", "fhd175", path)["msg"]
        again = _run("first", "fhd175", path)["msg"]  # 상태줄엔 평소 문구("구단주명을 입력하세요.")가 있다
        assert "배율" not in again, ("좁은 화면 안내가 매번 뜬다", again)
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_saved_1600x900_from_old_version_fits_small_screen():
    res = _run("upgrade", "fhd150")
    assert _inside(res), res


def test_unmaximize_keeps_window_on_screen():
    res = _run("unmax", "fhd150")
    assert not res["maximized"] and _inside(res), res


def test_moving_to_smaller_monitor_refits():
    res = _run("move", "two")
    assert res["moved_from_default"] and res["screen"] == "lap150", res
    assert _inside(res), res


def test_dialogs_fit_small_screen():
    res = _run("dialogs", "fhd150")
    ah = res["avail"][3]
    import app_main as a
    fh = a.FRAME_ALLOWANCE[1]
    # '줄일 수 있나'는 작업 표시줄을 뺀 실제 150% 노트북 높이로 — 흉내 화면은 작업 표시줄이 없어 720 을 다 쓴다.
    # 720 으로 재면 스크롤 없는 스쿼드 창(내용 최소 677)도 통과해 버렸다(변이로 확인).
    real_h = 720 - 32
    for title, (w, h, mw, mh) in res["dialogs"].items():
        assert h + fh <= ah, ("대화상자가 화면보다 크다", title, h, ah)
        assert mh + fh <= real_h, ("내용 최소 크기 때문에 줄일 수 없다 — 스크롤 안에 넣어야", title, mh, real_h)


def test_about_dialog_shows_every_tab():
    # 정보 창이 고정 620 이던 때 탭 6개(합 837)가 안 들어가 ◀ ▶ 로 넘겨야 했다(2026-10-05 사용자).
    # 탭 줄 폭 ≥ 원하는 폭이면 넘김 버튼이 안 생긴다. 가장 좁은 흉내 화면(175%, 논리 폭 약 1097)까지.
    for screen in ("fhd100", "fhd150", "fhd175", "lap1366"):
        res = _run("about", screen)
        a = res["about"]
        assert a["bar"] >= a["need"], ("탭 줄이 좁아 넘김 버튼이 생긴다", screen, a)
        ax, ay, aw, ah = res["avail"]
        fx, fy, fw, fh = a["frame"]
        assert fw <= aw and fh <= ah, ("정보 창이 화면보다 크다", screen, a, res["avail"])


def test_dialogs_open_over_main_window_on_its_monitor():
    """대화상자는 메인 창이 있는 모니터에, 메인 창 위 가운데에 — 정보 창이 다른 모니터에 떴다(2026-10-06 사용자).
    두 모니터 배율이 다르면(100% · 150%) Qt 의 자동 위치가 빗나간다."""
    res = _run("dialog_screen", "two")
    assert res["main_screen"] == "lap150", res
    placed = res["placed"]
    assert len(placed) == 4, placed
    for title, p in placed.items():
        assert p["screen"] == "lap150" and p["center_in_main"], (title, p, res["frame"])
    assert res["follow"] == {"main": "big", "dialog": "big", "center_in_main": True}, res["follow"]
    # 최소화 중엔 다시 묻기를 미루고(주 모니터에 갑자기 뜨지 않게), 창이 돌아오면 그때 한 번
    assert res["asked_while_min"] == [] and res["asked_after_restore"] == [False], res


def test_wrong_frame_estimate_is_caught_after_show():
    # 띄우기 전 테두리 여유(FRAME_ALLOWANCE)가 틀려도 띄운 뒤 재확인이 최대화로 구한다 — 흉내 화면에선 추정이 늘 맞아
    # 이 안전망이 일할 일이 없어서, 일부러 여유를 0 으로 둬 '창이 화면과 딱 같은 크기 + 테두리'를 만든다.
    res = _run("frame0", "fhd150")
    assert res["maximized"] and _inside(res), res

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
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(main())
