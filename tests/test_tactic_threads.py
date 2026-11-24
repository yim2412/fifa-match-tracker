"""저장 전술 창(110 ④-2) — **실제 QThread 로** 로더가 바뀌고 닫히고 끝나는 길을 잰다. `python tests/test_tactic_threads.py`(네트워크 없음).

화면 스모크(`test_ui_smoke`)는 `QThread.start` 를 막아 신호 순서(`done` 과 `finished`)·`isRunning`·늦게 오는 결과를 못 본다.
여기서는 스레드를 막지 않고 스쿼드메이커 한 칸(`traitcollect.fetch_cell`)만 느린 가짜로 바꾼다. 구단주 비교 길(E2 — 경기를 넘겨받아
오픈API 0)로 열고, 랭킹 스냅숏 찾기는 가짜다(실제 찾기는 `test_rankmeta`·`test_ui_smoke`).
"""
from __future__ import annotations

import gc
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="fifa110_"))
os.environ["FIFA_DATA_DIR"] = str(SCRATCH)
for _k in ("NEXON_API_KEY", "FIFA_SERVER_URL", "FIFA_DEMO"):
    os.environ.pop(_k, None)

import requests  # noqa: E402
import requests.adapters  # noqa: E402

SENT: list[str] = []


def _record_send(self, request, *a, **k):
    SENT.append(request.url)
    raise requests.ConnectionError("테스트 — 네트워크 없음")


requests.adapters.HTTPAdapter.send = _record_send

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import config  # noqa: E402
import app_main  # noqa: E402
import store  # noqa: E402
import tactic_codes  # noqa: E402
import tactics  # noqa: E402
import traitcollect  # noqa: E402
import matchtime  # noqa: E402

assert config.DATA_DIR == SCRATCH, config.DATA_DIR
config.SINGLE_INSTANCE = False
config.UPDATE_CHECK = False
config.API_KEY = "test_key"
config.RANKER_PICK_GAP_S = 0.01

LINE = [0, 3, 4, 6, 7, 10, 12, 13, 15, 16, 25]
PEOPLE = {"가": 11, "나": 12, "다": 13}


def _cell(sn: int, defstyle: int = 1) -> dict:
    st = {k: 0 for k in tactic_codes.TEAM_KEYS}
    st["defensivestyle"] = defstyle
    pl = [{"spid": sn * 1000 + i, "role": tactic_codes.POSITION_ROLE[p], "state": 1, "participation": [2, 2]} for i, p in enumerate(LINE)]
    return {"ResultCode": 1, "ResultData": {"Squadinfo": {"players": pl, "teamSt": st}}}


def _details(sn: int) -> tuple:
    at = matchtime.raw_bound(datetime.now() - timedelta(hours=5))
    return ({"matchId": f"m{sn}", "matchDate": at, "matchInfo": [
        {"ouid": f"o{sn}", "player": [{"spId": sn * 1000 + i, "spPosition": p, "spGrade": 8} for i, p in enumerate(LINE)]}]},)


class Web:
    """느린 가짜 스쿼드메이커 — 사람마다 부른 수 · 같이 돈 로더 수."""

    def __init__(self, delay=0.05):
        self.delay, self.calls, self.hold = delay, [], None

    def __call__(self, _session, sn, team_type, part, seq):
        self.calls.append(sn)
        if self.hold is not None:
            self.hold.wait(10)
        time.sleep(self.delay)
        return _cell(sn) if (team_type, part) == (1, 0) else {"ResultCode": -1}

    def of(self, nick) -> int:
        return self.calls.count(PEOPLE[nick])


RUN = {"now": 0, "max": 0, "n": 0}
_RUN_LOCK = threading.Lock()
_REAL_RUN = app_main.TacticLoader.run


def _counted_run(self):
    with _RUN_LOCK:
        RUN["now"] += 1
        RUN["n"] += 1
        RUN["max"] = max(RUN["max"], RUN["now"])
    try:
        _REAL_RUN(self)
    finally:
        with _RUN_LOCK:
            RUN["now"] -= 1


app_main.TacticLoader.run = _counted_run
app_main.SeasonLoader.start = lambda self: None   # 창을 만들 때 시즌표를 읽으러 가지 않게(이 파일이 재는 것 밖)


def _pump(until, limit_s: float = 15.0) -> bool:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        _app.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return False


class Env:
    """새 창 하나 — 거래·랭커 픽 잇기는 이름만 적는다(실제로 뜨면 네트워크를 탄다)."""

    def __enter__(self):
        config.WEB_DATA = False          # 창을 만들 때 시즌표를 읽으러 가지 않게
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        RUN.update(now=0, max=0, n=0)
        self.web = Web()
        self.keep = traitcollect.fetch_cell, app_main.core.tactic_target
        traitcollect.fetch_cell = self.web
        app_main.core.tactic_target = lambda _c, sn, nick: ("ok", sn or PEOPLE[nick], 5)
        conn = store.open_db(config.DB_PATH)
        try:
            with conn:
                conn.execute("DELETE FROM api_budget")
        finally:
            conn.close()
        self.win = app_main.MainWindow(app_main.FCOnlineAPI("test_key"))
        config.WEB_DATA = config.RANK_COLLECT = True
        self.win._rank_read = lambda fn: fn(None)
        self.log = []
        self.yields = []
        real_yield = self.win._yield_trades
        self.win._yield_trades = lambda: (self.yields.append(RUN["n"]), real_yield())[1]
        self.win.start_trades = lambda: self.log.append("trades")
        self.win.start_visible_api_loader = lambda: self.log.append("visible")
        return self

    def open(self, nick):
        self.win._open_tactic(nick, ouid=f"o{PEOPLE[nick]}", details=_details(PEOPLE[nick]))
        return self.win._tactic_dialog

    def idle(self) -> bool:
        return _pump(lambda: not self.win._tactic_workers or all(not w.isRunning() and getattr(w, "ended", False)
                                                                 for w in self.win._tactic_workers))

    def budget(self) -> int:
        conn = store.open_db(config.DB_PATH)
        try:
            return store.budget_used(conn, datetime.now().date().isoformat(), store.BUDGET_TACTIC_WEB)
        finally:
            conn.close()

    def __exit__(self, *exc):
        if self.web.hold is not None:
            self.web.hold.set()
        self.win._quitting = True
        self.win.shutdown(fast=False)
        for w in self.win._tactic_workers:
            assert w.wait(5000), "로더가 안 끝났다"
        _app.processEvents()
        self.win.deleteLater()
        traitcollect.fetch_cell, app_main.core.tactic_target = self.keep
        _app.processEvents()
        gc.collect()
        return False


def test_one_person_shows_result_and_second_open_uses_memory():
    with Env() as env:
        dlg = env.open("가")
        assert dlg.isVisible() and "확인하는 중" in dlg.lb_state.text(), dlg.lb_state.text()
        assert _pump(lambda: "읽는 중" in dlg.lb_state.text()), dlg.lb_state.text()
        assert _pump(lambda: len(dlg.tables) == 1), dlg.lb_state.text()
        assert env.idle() and env.web.of("가") == 15 == env.budget(), (env.web.calls, env.budget())
        assert dlg.lb_title.text() == "가" and "5위" in dlg.lb_sub.text() and "마지막 경기" in dlg.lb_sub.text(), dlg.lb_sub.text()
        assert dlg.lb_state.isHidden() and dlg.windowTitle().startswith("가")
        assert env.log == ["trades", "visible"], env.log      # 양보시킨 것들을 잇는다
        assert env.yields == [0], "시작할 때 거래·픽·특성을 양보시키지 않았다"
        env.open("가")                                        # 창을 연 채 다시 — 끝난 뒤라 새로 확인한다(받는 중일 때만 앞으로만)
        assert RUN["n"] == 2 or _pump(lambda: RUN["n"] == 2), "끝난 로더를 받는 중으로 봤다"
        assert env.idle() and _pump(lambda: len(dlg.tables) == 1) and env.web.of("가") == 15
        dlg.close()
        assert dlg.tables == [] and env.win._tactic_target is None and dlg.lb_title.text() == ""
        env.open("가")                                        # 같은 날 같은 사람 — 스쿼드메이커 요청 0
        assert _pump(lambda: len(dlg.tables) == 1) and env.idle()
        assert env.web.of("가") == 15 and RUN["n"] == 3, (env.web.calls, RUN)


def test_switching_people_keeps_one_loader_and_last_person_wins():
    """E3b — 받는 중에 다른 사람을 열면 앞 로더가 끝난 뒤 시작하고, 또 누르면 마지막 사람만 남는다. 로더 둘이 같이 돌지 않는다."""
    with Env() as env:
        env.web.hold = threading.Event()
        dlg = env.open("가")
        assert _pump(lambda: env.web.of("가") == 1), "대조: 앞 로더가 요청 중이어야 한다"
        first = env.win._tactic_workers[0]
        env.open("나")
        assert "마치는 중" in dlg.lb_state.text() and env.win._tactic_wait and first.isRunning()
        env.open("다")
        assert env.win._tactic_wait and env.win._tactic_target.sn == PEOPLE["다"] and first in env.win._tactic_workers
        assert env.log == [], "기다리는 사람이 있는데 거래를 이었다"
        env.web.hold.set()
        assert _pump(lambda: len(dlg.tables) == 1), dlg.lb_state.text()
        assert env.idle()
        assert dlg.lb_title.text() == "다" and env.web.of("나") == 0 and env.web.of("다") == 15, env.web.calls
        assert env.web.of("가") == 1, "멈춘 로더가 계속 받았다"
        assert RUN["max"] == 1 and RUN["n"] == 2, RUN
        assert env.log == ["trades", "visible"], env.log
        assert [k[0] for k in env.win._tactic_cache] == [PEOPLE["다"]], "멈춘 사람의 결과를 기억했다"


def test_new_window_starts_with_buttons_off():
    with Env() as env:
        for btn in (env.win.btn_sim_tactic, env.win.btn_compare_tactic):
            assert not btn.isEnabled(), btn.text()


def test_stale_progress_does_not_overwrite_new_person():
    """다른 사람을 연 뒤 앞 로더의 진행 글이 늦게 와도 새 사람의 글을 덮지 않는다."""
    with Env() as env:
        dlg = env.open("가")
        first = env.win._tactic_loader
        assert first.wait(8000) and env.web.of("가") == 15      # 진행·끝 신호가 줄 서 있다
        env.open("나")
        seen = []
        real = dlg._say
        dlg._say = lambda text, button=False: (seen.append(text), real(text, button))
        env.web.hold = threading.Event()
        assert _pump(lambda: env.web.of("나") >= 1), dlg.lb_state.text()
        assert not any("/15)" in t for t in seen), seen
        env.web.hold.set()
        assert _pump(lambda: len(dlg.tables) == 1 and dlg.lb_title.text() == "나") and env.idle()


def test_same_person_again_only_raises_window():
    with Env() as env:
        env.web.hold = threading.Event()
        env.open("가")
        assert _pump(lambda: env.web.of("가") == 1)
        ld = env.win._tactic_loader
        env.open("가")
        assert env.win._tactic_loader is ld and not env.win._tactic_wait and env.win._tactic_workers == [ld]
        env.web.hold.set()
        assert env.idle() and RUN["n"] == 1


def test_closed_then_same_person_and_back_and_forth():
    """멈춘 로더를 "같은 사람을 받는 중" 으로 보지 않는다: A→닫기→A · A→B→A 는 새로 받고, A→B→닫기는 B 요청 0."""
    for steps, want in ((("가", None, "가"), "가"), (("가", "나", "가"), "가"), (("가", "나", None), None)):
        with Env() as env:
            env.web.hold = threading.Event()
            dlg = env.open(steps[0])
            assert _pump(lambda: env.web.of("가") == 1), steps
            for s in steps[1:]:
                if s is None:
                    assert env.win._tactic_wait or steps[1] is None
                    dlg.close()
                    assert not env.win._tactic_wait and env.win._tactic_target is None
                else:
                    env.open(s)
            env.web.hold.set()
            if want is None:
                assert env.idle()
                _pump(lambda: False, 0.3)
                assert RUN["n"] == 1 and env.web.of("나") == 0 and not dlg.isVisible() and dlg.tables == [], (steps, RUN)
                assert env.win._tactic_cache == {} and env.log == ["trades", "visible"], (steps, env.log)
            else:
                assert _pump(lambda: len(dlg.tables) == 1), (steps, dlg.lb_state.text())
                assert env.idle() and RUN["n"] == 2 and RUN["max"] == 1, (steps, RUN)
                assert dlg.lb_title.text() == want and dlg.isVisible() and env.web.of("나") == 0, steps


def test_late_result_after_close_touches_nothing():
    """닫는 순간 이미 끝난 결과(done 이 줄 서 있다)도 화면·기억에 안 쓴다."""
    with Env() as env:
        dlg = env.open("가")
        ld = env.win._tactic_loader
        assert ld.wait(8000), "대조: 이벤트를 안 돌린 채 끝까지 받아야 done 이 줄 서 있다"
        assert env.web.of("가") == 15
        dlg.close()
        assert env.idle()
        assert env.win._tactic_cache == {} and dlg.tables == [] and dlg.lb_title.text() == "" and not dlg.isVisible()


def test_waiting_person_blocked_by_gate_resumes_others():
    """B 가 관문에 막히면(웹 데이터 꺼짐) 로더가 안 떴으니 거래·랭커 픽을 다시 잇는다."""
    with Env() as env:
        env.web.hold = threading.Event()
        dlg = env.open("가")
        assert _pump(lambda: env.web.of("가") == 1)
        env.open("나")
        config.WEB_DATA = False
        env.web.hold.set()
        assert env.idle() and _pump(lambda: env.log == ["trades", "visible"]), env.log
        assert RUN["n"] == 1 and "꺼져" in dlg.lb_state.text(), dlg.lb_state.text()


def test_other_loaders_wait_for_tactic():
    """E3c — 전술이 도는 동안 거래·랭커 픽·특성·스펙업·색인 백필은 기다린다. 검색·비교 판정에는 안 들어간다(A→B 가 busy 가 되지 않게)."""
    with Env() as env:
        w = env.win
        assert not w._api_background_busy() and not w._api_loader_busy()
        env.web.hold = threading.Event()
        env.open("가")
        assert _pump(lambda: env.web.of("가") == 1)
        assert w._api_background_busy() and w._api_loader_busy() and not w._search_busy()
        env.web.hold.set()
        assert env.idle() and not w._api_background_busy() and not w._api_loader_busy()


def test_switch_off_stops_forgets_and_closes():
    with Env() as env:
        dlg = env.open("가")
        assert _pump(lambda: len(dlg.tables) == 1) and env.idle() and env.win._tactic_cache
        env.web.hold = threading.Event()
        env.open("나")
        assert _pump(lambda: env.web.of("나") == 1)
        env.win._tactic_drop()
        assert env.win._tactic_cache == {} and not dlg.isVisible() and env.win._tactic_target is None
        env.web.hold.set()
        assert env.idle() and env.web.of("나") == 1 and env.win._tactic_cache == {}
        # 숨은 창(트레이)이어도 비운다
        env.open("가")
        assert _pump(lambda: len(dlg.tables) == 1) and env.idle()
        dlg.hide()
        env.win._tactic_drop()
        assert dlg.tables == [] and dlg.lb_title.text() == ""


def test_quit_while_running_waits_and_starts_nothing():
    with Env() as env:
        env.open("가")
        assert _pump(lambda: env.web.of("가") >= 1)
        env.open("나")
        first = env.win._tactic_workers[0]
        env.win._quitting = True
        t0 = time.monotonic()
        env.win.shutdown(fast=False)
        assert not first.isRunning() and time.monotonic() - t0 < 5, "종료가 전술 로더를 안 기다렸거나 오래 붙잡혔다"
        _pump(lambda: False, 0.3)
        assert RUN["n"] == 1 and env.web.of("나") == 0 and env.log == [], (RUN, env.log)


def test_unexpected_error_shows_reason():
    with Env() as env:
        traitcollect.fetch_cell = lambda *a: 1 / 0
        noted = []
        keep = app_main.crashlog.note
        app_main.crashlog.note = lambda name, e: noted.append((name, type(e).__name__))
        app_main.TacticLoader._noted = False
        try:
            dlg = env.open("가")
            assert env.idle() and _pump(lambda: "읽지 못했습니다" in dlg.lb_state.text()), dlg.lb_state.text()
            env.open("나")
            assert env.idle()
        finally:
            app_main.crashlog.note = keep
        assert noted == [("tactic", "ZeroDivisionError")], noted
        assert env.win._tactic_cache == {}


def test_zz_no_network():
    """위 테스트 전부에서 실제로 나간 요청이 없다."""
    assert SENT == [], SENT


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with watchdog.limit(t.__name__), watchdog.slot_errors():
                t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
