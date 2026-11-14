"""내 메모·표시(109 M1·M3·M13) — **실제 QThread 로** 신호 순서·쓰기 줄 세우기·판본 다른 결과 버리기를 잰다.
`python tests/test_notes_thread.py` 로 실행(네트워크 없음).

화면 스모크(`test_ui_smoke`)는 파일 머리에서 `QThread.start` 를 막아 이 종류를 못 본다(test_startup_108 머리말과 같은 이유).
- M1: 로더가 메모 묶음(notes_ready)을 경기 목록(finished_ok) **앞에** 넘긴다 — 처음 열 때도, 메모리를 내려놓았다 다시 열 때도(offline 로더) 같은 길.
- M3: 메모 쓰기는 fifa.db 에 쓰는 작업 스레드(WRITES)에서 · 저장 공간을 지우는 중이면 거부.
- M13: 작업 스레드(연승·연패 섞기)가 시작 때 들고 간 거르기 판본이 끝날 때 다르면 결과를 버리고 다시 띄운다.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="fifa109t_"))
os.environ["FIFA_DATA_DIR"] = str(SCRATCH)
for _k in ("NEXON_API_KEY", "FIFA_SERVER_URL", "FIFA_DEMO"):
    os.environ.pop(_k, None)

import requests  # noqa: E402
import requests.adapters  # noqa: E402


def _no_net(self, request, *a, **k):
    raise requests.ConnectionError("테스트 — 네트워크 없음")


requests.adapters.HTTPAdapter.send = _no_net

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import config  # noqa: E402
import core_api as core  # noqa: E402
import demodata  # noqa: E402
import store  # noqa: E402

assert config.DATA_DIR == SCRATCH, config.DATA_DIR
config.SINGLE_INSTANCE = False
config.UPDATE_CHECK = False
config.API_KEY = "test_key"
app_main.GAME_CHECK = lambda: False

NICK, OUID, MT = demodata.NICK, demodata.OUID, demodata.MATCH_TYPE
DATA = demodata.generate(n=60)
IDS = list(DATA.ids)          # 최신순
_SEEDED = False
_WIN = None


def _pump(until, limit_s: float = 30.0) -> bool:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        _app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return False


def _seed() -> None:
    global _SEEDED
    if _SEEDED:
        return
    demodata.install(DATA, config.DB_PATH, config.CACHE_DIR)
    conn = store.open_db(config.DB_PATH)
    try:
        store.set_trade_state(conn, my_ouid=OUID)
    finally:
        conn.close()
    _SEEDED = True


def _db_notes() -> store.Notes:
    conn = store.open_db(config.DB_PATH)
    try:
        return store.load_notes(conn)
    finally:
        conn.close()


def _reset_notes(*marks: str) -> store.Notes:
    """notes 표를 주어진 경기들의 '끊김' 표시만 남기고 비운다(테스트 사이를 끊는다)."""
    conn = store.open_db(config.DB_PATH)
    try:
        conn.execute("DELETE FROM notes")
        conn.commit()
        for m in marks:
            store.save_note(conn, store.NOTE_MATCH, m, mark="cut")
        return store.load_notes(conn)
    finally:
        conn.close()


def _win():
    """메인 창 하나를 모든 테스트가 같이 쓴다(켤 때마다 만들면 로더가 겹친다). 경기는 demodata 60판을 직접 넣는다."""
    global _WIN
    if _WIN is not None:
        return _WIN
    _seed()
    win = app_main.MainWindow(demodata.DemoAPI(DATA, cache_dir=config.CACHE_DIR))
    win._on_fetch_team_colors = lambda **k: None
    matches = [m for m in (app_main.parse_match(d, OUID) for d in
                           (DATA.details[i] for i in IDS)) if m]
    matches.sort(key=lambda m: m.match_date or 0, reverse=True)
    details = [DATA.details[i] for i in IDS]
    win._on_loaded(matches, details, OUID, {"nickname": NICK, "level": 7}, {}, {}, 0, len(matches),
                   None, "-", False, "", {}, {})
    _WIN = win
    return win


def _idle(win) -> bool:
    """워커·로더가 다 끝났나 — 연승 섞기·메모 쓰기."""
    return (not any(w.isRunning() for w in win._habits_workers)
            and (win._notes_writer is None or not win._notes_writer.isRunning()))


def _clean(win) -> None:
    _pump(lambda: _idle(win), 20)
    win._exclude_on = False
    win._notes = store.EMPTY_NOTES
    win._notes_queue, win._notes_writer, win._notes_applied = [], None, []
    core.set_excluded(())
    core.bump_filter_version()
    win._streak_done = None
    app_main.CLEARING.clear()


# ── M1 ──────────────────────────────────────────────────────────────────────


def test_loader_sends_notes_before_the_match_list():
    """notes_ready 가 finished_ok 보다 먼저 — 화면이 그리기 전에 표시 묶음을 받는다. 순서를 거꾸로 하면(변이) FAIL."""
    _seed()
    seeded = _reset_notes(IDS[3], IDS[7])
    order = []
    ld = app_main.MatchLoader(demodata.DemoAPI(DATA, cache_dir=config.CACHE_DIR), NICK, MT, offline_ouid=OUID, auto=True)
    ld.notes_ready.connect(lambda n: order.append(("notes", n)))
    ld.finished_ok.connect(lambda *a: order.append(("loaded", len(a[0]))))
    ld.start()
    assert _pump(lambda: ld.isFinished()), "로더가 안 끝났다"
    _app.processEvents()
    assert [k for k, _v in order] == ["notes", "loaded"], [k for k, _v in order]
    assert order[0][1] == seeded and order[0][1].shown == frozenset({IDS[3], IDS[7]}), order[0][1]
    assert order[1][1] == len(IDS), order[1][1]


def test_reopen_after_release_goes_the_same_way_and_hands_the_set_to_core():
    """메모리를 내려놓았다 다시 열 때(reload_after_release · offline 로더)도 묶음이 로더에서 와 거를 묶음이 맞춰진다.
    스위치가 켜져 있으면 core 의 거를 묶음이 표시한 판이고, 꺼져 있으면 비어 있다."""
    win = _win()
    _clean(win)
    seeded = _reset_notes(IDS[2])
    for on, want in ((True, frozenset({IDS[2]})), (False, frozenset())):
        win._exclude_on = on
        core.set_excluded(())
        win._notes = store.EMPTY_NOTES
        win._released = True
        win.release_memory = lambda: None     # 이미 놓은 것으로 친다 — 다시 읽기만 잰다
        win.reload_after_release()
        ld = win._loader
        assert ld is not None and _pump(lambda: ld.isFinished()), "다시 읽기가 안 끝났다"
        _app.processEvents()
        assert win._notes == seeded, win._notes
        assert core.excluded() == want, (on, core.excluded())
    _clean(win)


# ── M3 ──────────────────────────────────────────────────────────────────────


def test_note_writer_runs_on_a_thread_with_fifa_writes_and_is_refused_while_clearing():
    win = _win()
    _clean(win)
    _reset_notes()
    assert app_main.STORE_FIFA in app_main.NotesWriter.WRITES, "fifa.db 에 쓰는 작업은 WRITES 에 적는다(지우는 동안 걸쇠가 막고 멈출 대상)"
    # 지우는 중이면 거부 — 막지 않았으면 줄이 써졌을 것(아래 대조가 그걸 먼저 보인다)
    app_main.CLEARING.add("all")
    try:
        win._set_match_mark(IDS[5], "cut")
        assert win._notes_writer is None and win._notes_queue == [], "거부된 쓰기가 줄에 남았다"
        assert "지우는 중" in win.statusBar().currentMessage(), win.statusBar().currentMessage()
        assert _db_notes().marks == {}, "지우는 중인데 써졌다"
    finally:
        app_main.CLEARING.clear()
    win._set_match_mark(IDS[5], "cut")                       # 막지 않으면 이렇게 써진다
    assert _pump(lambda: IDS[5] in win._notes.marks and _idle(win)), "쓰기가 끝나지 않았다"
    assert _db_notes().marks == {IDS[5]: "cut"} and win._notes.marks == {IDS[5]: "cut"}, _db_notes()
    # 둘을 이어서 — 차례대로 써진다(스레드 둘이 같은 줄을 뒤바꾸지 않는다)
    win._set_match_mark(IDS[5], "practice")
    win._set_match_memo(IDS[5], "연습 상대")
    assert _pump(lambda: win._notes.memos.get(IDS[5]) == "연습 상대" and _idle(win)), "이어 쓰기가 끝나지 않았다"
    assert _db_notes().marks == {IDS[5]: "practice"} and _db_notes().memos == {IDS[5]: "연습 상대"}, _db_notes()
    assert win._notes == _db_notes()
    # 반대 순서 — 메모 다음에 표시: 표시 줄이 앞선 메모를 되돌리지 않는다(지금 값 대신 옛 값을 읽으면 메모가 사라진다)
    win._set_match_memo(IDS[6], "먼저 쓴 메모")
    win._set_match_mark(IDS[6], "cut")
    assert _pump(lambda: IDS[6] in win._notes.marks and _idle(win)), "이어 쓰기가 끝나지 않았다"
    assert _db_notes().memos.get(IDS[6]) == "먼저 쓴 메모" and _db_notes().marks.get(IDS[6]) == "cut", _db_notes()
    _clean(win)
    _reset_notes()


def test_late_loader_notes_read_before_a_write_do_not_undo_it():
    """로더가 쓰기 전에 표를 읽고 쓰기가 끝난 뒤에 넘겨도, 방금 쓴 표시가 화면에서 사라지지 않는다.
    다시 얹기를 빼면 늦게 온 옛 값이 표시를 지워 FAIL."""
    win = _win()
    _clean(win)
    stale = _reset_notes()                 # 로더가 읽어 둔 값 — 표시가 없다
    win._set_match_mark(IDS[4], "cut")
    assert _pump(lambda: IDS[4] in win._notes.marks and _idle(win)), "쓰기가 끝나지 않았다"
    win._on_notes_ready(stale)             # 늦게 온 옛 값
    assert win._notes.marks.get(IDS[4]) == "cut", win._notes
    assert win._notes == _db_notes(), (win._notes, _db_notes())
    # 한 번 맞춘 뒤 새로 읽은 값은 그대로 받는다(다시 얹을 쓰기가 남지 않는다)
    fresh = _reset_notes()
    win._on_notes_ready(fresh)
    assert win._notes == fresh, win._notes
    _clean(win)


def test_detail_dialog_drops_its_notes_connection_when_destroyed():
    """경기 상세를 닫아 지우면 notes_changed 연결도 끊긴다. 끊지 않으면 열 때마다 연결이 쌓인다."""
    win = _win()
    _clean(win)
    before = win.receivers("2notes_changed()")
    for _ in range(3):
        dlg = app_main.MatchDetailDialog(win, "상대")
        dlg.v.addWidget(win._detail_note_row(dlg, IDS[1]))     # 실제 상세처럼 창 안에 둔다(안 두면 줄이 먼저 지워진다)
        assert win.receivers("2notes_changed()") == before + 1, win.receivers("2notes_changed()")
        dlg.deleteLater()
        _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # processEvents 는 deleteLater 를 처리하지 않는다
    assert win.receivers("2notes_changed()") == before, (before, win.receivers("2notes_changed()"))
    _clean(win)


# ── M13 ─────────────────────────────────────────────────────────────────────


class _Gate:
    """core.streak_baseline 을 바꿔 끼운다 — 부르면 열릴 때까지 작업 스레드가 그 자리에서 기다린다."""

    def __init__(self):
        self.real = core.streak_baseline
        self.open = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        self.entered.set()
        self.open.wait(30)
        return self.real(*a, **k)

    def install(self):
        core.streak_baseline = self
        # HabitsWorker.run 은 모듈 이름 core 로 부른다 — app_main.core 는 같은 모듈 객체
        assert app_main.core is core
        return self

    def uninstall(self):
        self.open.set()
        core.streak_baseline = self.real


def test_streak_result_with_an_older_filter_version_is_dropped_and_relaunched():
    """작업 스레드가 도는 사이에 거르기 판본이 바뀌면 그 결과는 버리고 새 묶음으로 다시 띄운다(M13).
    판본 비교를 빼면(변이) 도는 사이 바뀐 묶음의 옛 결과가 그대로 쓰여 아래 단언이 FAIL 한다."""
    win = _win()
    _clean(win)
    gate = _Gate().install()
    try:
        win._streak_done = None
        win._launch_streak()
        w1 = win._habits_workers[-1]
        assert gate.entered.wait(10), "작업 스레드가 안 시작됐다"
        req1 = win._streak_req
        assert w1._fv == core.filter_version() == 0
        # 도는 사이 표시한 판이 바뀌었다 — _refilter 가 아니라 판본만(묶음이 같은 경기 목록이라 키가 안 바뀌는 경우)
        core.set_excluded({IDS[0]})
        core.bump_filter_version()
        assert core.filter_version() != w1._fv
        gate.open.set()                      # 옛 판본으로 시작한 작업이 끝난다
        assert _pump(lambda: not w1.isRunning()), "작업 스레드가 안 끝났다"
        _app.processEvents()
        assert win._streak_req == req1 + 1, "판본이 다른 결과를 받고 다시 띄우지 않았다"
        assert win._streak_done is None or win._streak_done[0] == core.streak_key(win._matches), win._streak_done
        # 다시 띄운 작업이 끝나면 새 판본의 결과가 선다
        assert _pump(lambda: win._streak_done is not None and _idle(win)), "다시 띄운 작업의 결과가 안 왔다"
        w2 = win._habits_workers[-1]
        assert w2._fv == core.filter_version() and w2 is not w1
        assert win._streak_done[0] == core.streak_key(win._matches)
    finally:
        gate.uninstall()
        _clean(win)


def test_refilter_cancels_the_running_streak_worker_and_the_result_matches_the_new_set():
    """_refilter 는 도는 섞기를 새 묶음으로 갈아 끼운다 — 끝나 서는 결과는 표시한 판을 뺀 경기 목록의 것이다."""
    win = _win()
    _clean(win)
    win._notes = store.Notes({IDS[1]: "cut", IDS[4]: "practice"}, {}, {})
    gate = _Gate().install()
    try:
        win._streak_done = None
        win._launch_streak()
        w1 = win._habits_workers[-1]
        assert gate.entered.wait(10)
        win._exclude_on = True
        win._refilter()
        w2 = win._habits_workers[-1]
        assert w2 is not w1 and w1._cancel, "앞 계산을 안 멈췄다"
        assert w2._fv == core.filter_version() != 0
        gate.open.set()
        assert _pump(lambda: win._streak_done is not None and _idle(win)), "새 묶음의 결과가 안 왔다"
        want = core.streak_key(win._matches)
        assert win._streak_done[0] == want, "결과 키가 새 묶음(표시한 판을 뺀 목록)의 것이 아니다"
        assert want != core.raw.streak_key(win._matches), "표시한 판을 안 뺀 키와 같다 — 시험이 공허하다"
    finally:
        gate.uninstall()
        _clean(win)


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    if _WIN is not None:
        # test_startup_108._close_all 과 같은 정리 — 창이 스스로 로더·작업 스레드를 멈추고 기다린 뒤 지운다.
        # 창을 쥔 채 os._exit 로 내리면 열 번에 한두 번 종료 때 네이티브 충돌(0xC0000005·0xC0000409)로 rc 가 깨졌다
        import gc
        _pump(lambda: _idle(_WIN), 20)
        _WIN._quitting = True
        _WIN.shutdown(fast=False)
        _WIN.hide()
        _WIN.deleteLater()
        _app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        _app.processEvents()
        gc.collect()
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
