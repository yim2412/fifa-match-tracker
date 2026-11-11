"""둘러보기(96 O2) — 데모 프로세스를 흉내 내 잰다. `python tests/test_demo.py` 로 실행(네트워크 없음).

config.DEMO 는 import 때 정해지므로 이 파일은 **처음부터 데모 프로세스**다: 실제 키·스위치를 env 에 넣은 채로
`demo.prepare(--demo)` → config import. 잰 것:
- 실제 키·스위치를 물려받지 않는다(env 에 있고 .env 에도 있어도)
- 창을 띄우고 검색·토글·연결 점검을 눌러도 HTTP 요청 0 · .env 가 안 생긴다
- [키 넣고 시작] 의 env = 데모 전 원래 env · [키 없이 둘러보기] 의 env 에 키·스위치가 없다
- 데모 폴더 쓸기(잠금 · 60초 · 접두어) · 뮤텍스 이름 · 키 창 결과값
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

# 실제 사용자 PC 처럼 — 키·스위치가 env 에 올라와 있다(config.load_dotenv 가 .env 를 os.environ 에 올린 상태)
REAL = {"NEXON_API_KEY": "live_realkey_should_not_leak", "FIFA_WEB_DATA": "1", "FIFA_RANK_COLLECT": "1",
        "FIFA_OWNER_DIST": "2026-11-01T00:00:00", "FIFA_CARD_BACKFILL": "2026-11-01T00:00:00",
        "FIFA_SERVER_URL": "http://127.0.0.1:9", "FIFA_NOTICE": "3", "FIFA_UPDATE_CHECK": "1"}
os.environ.update(REAL)
os.environ.pop("FIFA_DATA_DIR", None)
os.environ.pop("FIFA_DEMO", None)
SCRATCH = Path(tempfile.mkdtemp())
ORIGINAL_ENV = dict(os.environ)

import demo  # noqa: E402

SESSION = demo.prepare(["app_main.py", "--demo"], tmp=SCRATCH)

import requests  # noqa: E402
import requests.adapters  # noqa: E402

SENT: list[str] = []
_real_send = requests.adapters.HTTPAdapter.send


def _record_send(self, request, *a, **k):
    SENT.append(request.url)
    raise requests.ConnectionError("테스트 — 네트워크 없음")


requests.adapters.HTTPAdapter.send = _record_send   # 실제로 나가는 HTTP 는 전부 여기를 지난다(세션·requests.get 둘 다)

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import config  # noqa: E402
import app_main  # noqa: E402
import demodata  # noqa: E402
import images  # noqa: E402
import models  # noqa: E402
import tray  # noqa: E402

config.SINGLE_INSTANCE = False


def _pump(until, limit_s: float = 30.0) -> bool:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        _app.processEvents()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_prepare_sets_demo_folder_and_flag():
    assert SESSION is not None and config.DEMO
    assert config.DATA_DIR == SESSION.folder and SESSION.folder.parent == SCRATCH
    assert SESSION.folder.name.startswith(demo.DIR_PREFIX)
    assert demo.DEMO_FLAG == config.DEMO_VAR
    assert demo.prepare(["app_main.py"]) is None, "--demo 가 없으면 아무것도 안 한다"
    assert demo.session() is SESSION


def test_config_ignores_real_keys_and_switches():
    # 대조 — 키·스위치는 env 에 그대로 있다(막지 않았으면 config 가 읽었을 값)
    for k, v in REAL.items():
        assert os.environ.get(k) == v
    assert config.API_KEY == "" and config.SERVER_URL == ""
    assert not config.WEB_DATA and not config.RANK_COLLECT and not config.UPDATE_CHECK
    assert config.OWNER_DIST_ON == "" and config.CARD_BACKFILL_ON == ""
    assert config.NOTICE_ACCEPTED == config.NOTICE_VERSION and not config.notice_needed()
    assert config.MIGRATED == []
    # 디스크의 .env 도 안 읽는다(다른 실행본이 켰다고 쓴 것처럼)
    config.ENV_PATH.write_text("FIFA_WEB_DATA=1\nFIFA_RANK_COLLECT=1\nFIFA_NOTICE=14\n", encoding="utf-8")
    try:
        assert config.read_env_switches() == (False, False)
        assert config.read_owner_dist() is None and config.read_card_backfill() is None
        assert not config.read_person_allowed() and not config.predict_check_allowed()
    finally:
        config.ENV_PATH.unlink()


def test_switches_cannot_be_saved_or_flipped():
    for call in (lambda: config.set_web_data(True), lambda: config.set_rank_collect(True),
                 lambda: config.set_owner_dist(True), lambda: config.set_card_backfill(True),
                 lambda: config.save_api_key("live_x"), lambda: config.accept_notice(True)):
        try:
            call()
        except OSError:
            pass
        else:
            raise AssertionError("데모에서 저장이 통과했다")
    assert not config.WEB_DATA and not config.RANK_COLLECT and config.API_KEY == ""
    assert not config.OWNER_DIST_ON and not config.CARD_BACKFILL_ON
    assert not config.ENV_PATH.exists()


def test_generate_is_deterministic_and_parses():
    from datetime import date
    a = demodata.generate(today=date(2026, 11, 10))
    b = demodata.generate(today=date(2026, 11, 10))
    assert json.dumps(list(a.details.values())) == json.dumps(list(b.details.values()))
    assert len(a.ids) == config.DEMO_MATCHES
    ms = [models.parse_match(d, demodata.OUID) for d in a.details.values()]
    assert all(m is not None for m in ms)
    res = {m.result for m in ms}
    assert res == {"승", "무", "패"}, res
    assert all(m.my_goals == sum(1 for s in d["matchInfo"][0]["shootDetail"] if s["result"] == 3)
               for m, d in zip(ms, a.details.values()))
    # 선수 이름은 지어낸 것 — 시즌 번호가 넥슨에 없는 950~
    assert all(r["id"] // 1_000_000 in demodata.CARD_SEASONS for r in a.meta["spid"])


def test_images_never_fetch():
    out = SCRATCH / "img"
    assert images.fetch(951900001, out) is None
    assert images.fetch_icon("https://example.invalid/a.png", out, "season") is None
    assert not out.exists() and not SENT


def _window():
    shell = tray.AppShell(_app, open_window=lambda: None)
    win = app_main._open_window(shell)
    assert win is not None
    return shell, win


_SHELL_WIN: list = []


def test_window_opens_demo_account_without_requests():
    # 대조 — 이 기록기는 실제 요청을 잡는다(빈 검사가 아니다)
    try:
        requests.get("http://127.0.0.1:9/")
    except requests.ConnectionError:
        pass
    assert len(SENT) == 1
    SENT.clear()
    shell, win = _window()
    _SHELL_WIN[:] = [shell, win]
    assert _pump(lambda: len(win._matches_all) == config.DEMO_MATCHES and not (win._loader and win._loader.isRunning())), \
        f"데모 계정이 안 열렸다: {len(win._matches_all)}"
    assert win._nick == demodata.NICK
    assert win.btn_demo_start.isVisible()
    assert not shell.should_hide(), "트레이가 없으니 X 는 끝내기"
    assert SENT == [], SENT


def test_search_toggles_and_check_make_no_requests():
    shell, win = _SHELL_WIN
    win._api_search(demodata.NICK)
    assert _pump(lambda: not (win._loader and win._loader.isRunning()))
    warned = []
    real_warn = app_main.QMessageBox.warning
    app_main.QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[-1]))   # offscreen 에선 모달이 안 닫힌다
    try:
        win._api_search("다른구단주")    # 없는 계정 — 가짜 API 가 "찾지 못함" 으로
        assert _pump(lambda: not (win._loader and win._loader.isRunning()) and warned)
    finally:
        app_main.QMessageBox.warning = real_warn
    assert "지어낸 구단주" in warned[0], warned
    dlg = app_main.AboutDialog(win)
    for chk in (dlg.chk_web, dlg.chk_rank, dlg.chk_owner, dlg.chk_card):
        assert not chk.isVisibleTo(dlg), "켜는 길은 숨긴다"
        chk.setEnabled(True)
        chk.setChecked(True)          # 숨겨도 눌렸다고 치고 — 저장이 막혀 전역이 안 바뀐다
    assert not dlg.btn_check.isEnabled()
    dlg._on_check_start()
    assert isinstance(win.start_connection_check(demodata.NICK), str)
    _pump(lambda: False, 1.5)          # 혹시 띄운 로더가 있으면 돌 시간
    assert not config.WEB_DATA and not config.RANK_COLLECT and not config.OWNER_DIST_ON and not config.CARD_BACKFILL_ON
    assert not config.ENV_PATH.exists(), "데모 폴더에 .env 가 생겼다"
    assert SENT == [], SENT
    dlg.deleteLater()


def test_restart_uses_original_env():
    shell, win = _SHELL_WIN
    got = []
    real_spawn, real_quit = demo.spawn, shell.quit_app
    demo.spawn = lambda cmd, env: got.append((cmd, env))
    shell.quit_app = lambda fast=False: got.append("quit")
    try:
        win.start_real_app()
    finally:
        demo.spawn, shell.quit_app = real_spawn, real_quit
    (cmd, env), quit_ = got
    assert demo.DEMO_ARG not in cmd and quit_ == "quit"
    # 데모가 넣은 두 이름만 원래대로(PySide6 import 가 PATH 를 바꾼 것은 데모 밖) — 원래 없던 이름이라 빠져야 한다
    assert os.environ.get("FIFA_DATA_DIR") and os.environ.get("FIFA_DEMO") == "1"
    want = {k: v for k, v in os.environ.items() if k not in ("FIFA_DATA_DIR", "FIFA_DEMO")}
    assert env == want, set(env.items()) ^ set(want.items())
    assert all(env.get(k) == ORIGINAL_ENV[k] for k in REAL)


def test_restart_env_with_and_without_data_dir():
    for before in ({"FIFA_DATA_DIR": "X:/mine", "FIFA_UPDATE_CHECK": "0", "A": "1"}, {"A": "1"}):
        env = dict(before)
        sess = demo.prepare(["x", "--demo"], environ=env, tmp=SCRATCH / "case", pid=1, now=time.time())
        try:
            assert env["FIFA_DEMO"] == "1" and env["FIFA_DATA_DIR"] == str(sess.folder)
            assert demo.restart_env(sess, env) == before
        finally:
            demo.cleanup(sess)
    demo._session = SESSION


def test_launch_demo_strips_keys_and_adds_nothing():
    got = []
    real = demo.spawn
    demo.spawn = lambda cmd, env: got.append((cmd, env))
    try:
        app_main.launch_demo()
    finally:
        demo.spawn = real
    (cmd, env), = got
    assert cmd[-1] == demo.DEMO_ARG
    strip = {v for k, v in vars(config).items() if k.endswith("_VAR") and isinstance(v, str)}
    assert {"NEXON_API_KEY", "FIFA_WEB_DATA", "FIFA_SERVER_URL", "FIFA_NOTICE"} <= strip
    assert not (strip & set(env)), strip & set(env)
    assert "FIFA_DATA_DIR" not in env
    assert all(os.environ.get(k) == v for k, v in env.items()), "부모가 무언가를 넣었다"


def test_key_dialog_results_and_open_window_branch():
    dlg = app_main.ApiKeyDialog(allow_demo=True)
    dlg.btn_demo.click()
    assert dlg.result() == app_main.ApiKeyDialog.DEMO_RESULT != QDialog.DialogCode.Accepted
    assert app_main.ApiKeyDialog().btn_demo is None, "키 거절로 다시 뜬 창엔 둘러보기가 없다"
    # 보통 실행의 키 창 길 — config.DEMO 를 잠깐 끄고 잰다
    calls = []
    saved = (config.DEMO, app_main.ApiKeyDialog.exec, app_main.launch_demo)
    config.DEMO = False
    app_main.launch_demo = lambda: calls.append("demo")
    shell = tray.AppShell(_app, open_window=lambda: None)
    shell.quit_app = lambda fast=False: calls.append("quit")
    try:
        for rc, want in ((app_main.ApiKeyDialog.DEMO_RESULT, ["demo", "quit"]), (0, [])):
            calls.clear()
            app_main.ApiKeyDialog.exec = lambda self, rc=rc: rc
            assert app_main._open_window(shell) is None
            assert calls == want, (rc, calls)
    finally:
        config.DEMO, app_main.ApiKeyDialog.exec, app_main.launch_demo = saved


def test_instance_names_and_quit_tells_both():
    assert tray.instance_name().endswith(tray.DEMO_SUFFIX)
    assert not tray.instance_name(demo=False).endswith(tray.DEMO_SUFFIX)
    assert tray.instance_name(demo=True) == tray.instance_name(demo=False) + tray.DEMO_SUFFIX
    asked = []
    real = tray.request_quit
    tray.request_quit = lambda name, *a, **k: asked.append(name) or "none"
    try:
        assert app_main.main(["app", tray.QUIT_ARG]) == 0
    finally:
        tray.request_quit = real
    assert asked == [tray.instance_name(demo=False), tray.instance_name(demo=True)]


def test_sweep_keeps_live_and_fresh_folders():
    tmp = SCRATCH / "sweep"
    tmp.mkdir()
    now = time.time()
    live = tmp / f"{demo.DIR_PREFIX}11-{int(now) - 999}"
    live.mkdir()
    lock = open(live / demo.LOCK_NAME, "w", encoding="utf-8")    # 살아 있는 데모가 쥔 잠금
    (live / "fifa.db").write_text("x", encoding="utf-8")         # 잠기지 않은 파일 — 잠금 unlink 를 먼저 안 하면 지워진다
    dead = tmp / f"{demo.DIR_PREFIX}12-{int(now) - 999}"
    dead.mkdir()
    (dead / demo.LOCK_NAME).write_text("12", encoding="utf-8")
    fresh = tmp / f"{demo.DIR_PREFIX}13-{int(now) - 5}"            # 잠금을 열기 직전
    fresh.mkdir()
    stale = tmp / f"{demo.DIR_PREFIX}14-{int(now) - 999}"          # 잠금 없이 오래됨(만들다 죽음)
    stale.mkdir()
    other = tmp / "피파전적관리-다른것"
    other.mkdir()
    try:
        gone = demo.sweep(tmp, now)
    finally:
        lock.close()
    assert set(gone) == {dead, stale}, gone
    assert (live / "fifa.db").exists() and fresh.exists() and other.exists()


def test_demo_times_form_sessions_and_keep_results():
    """97 — 경기 시각이 균등 무작위면 세션이 안 선다(30분 안에 두 판이 거의 없다). 하루 몇 번 몰아서 한 꼴이어야 하고,
    시각만 바뀌어야 한다(결과·점수·상대는 그대로 — 다른 화면의 숫자가 안 흔들리게)."""
    from datetime import date, datetime, time as dtime, timedelta
    import sessions
    today = date(2026, 11, 10)
    data = demodata.generate(today=today)
    ms = [models.parse_match(d, demodata.OUID) for d in data.details.values()]
    ss = sessions.split_sessions(ms)
    sizes = sorted(len(s.matches) for s in ss)
    # 세션이 선다: 판 수가 2 이상인 세션이 대부분이고 가장 긴 세션이 몇 판이다(균등 무작위면 거의 전부 1판이었다)
    assert sum(1 for z in sizes if z >= 3) >= len(ss) * 0.7 and sizes[-1] >= 5, sizes
    assert len(ss) < config.DEMO_MATCHES / 3, f"세션이 너무 잘게 갈렸다: {len(ss)}"
    # 쉼이 보인다: 같은 날 두 번 하는 날과 하루 쉬는 날이 모두 있다
    days = [s.start.date() for s in ss]
    assert len(days) > len(set(days)), "하루에 한 세션뿐이다"
    assert len(set(days)) < config.DEMO_DAYS, "쉬는 날이 없다"
    # 한 세션 안 간격은 끊김 문턱 안 · 세션은 자정을 안 넘는다
    for s in ss:
        gaps = [(b.match_date - a.match_date).total_seconds() / 60 for a, b in zip(s.matches, s.matches[1:])]
        assert all(g <= config.SESSION_GAP_MIN for g in gaps), gaps
        assert s.start.date() == s.end.date()
    # 범위: 오늘 0시 앞 · 시작 DEMO_DAYS 일 안
    first = min(m.match_date for m in ms)
    last = max(m.match_date for m in ms)
    assert last < datetime.combine(today, dtime(0)) and first >= datetime.combine(today - timedelta(days=config.DEMO_DAYS), dtime(0))
    # 결과·득점은 시각과 무관하게 같은 순서 — 시각 추첨만 바뀌었다(옛 균등 추첨이 쓰던 난수를 건너뛴다)
    results = "".join(m.result for m in ms)   # 시각 추첨을 바꾸기 전 판(2026-11-10 기준)에서 잰 값 — 시각만 바뀌었다는 증거
    assert (results.count("승"), results.count("무"), results.count("패"), results[:12]) == (134, 53, 113, "패패승패승승승패패승패승"), results[:12]
    assert len(data.details) == config.DEMO_MATCHES


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_")]   # 적힌 순서 — 창 테스트가 뒤 테스트의 창을 만든다
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
    for sw in _SHELL_WIN[1:]:
        sw._quitting = True
        sw.shutdown(fast=True)
    demo.cleanup(SESSION)
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
