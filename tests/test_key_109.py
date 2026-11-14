"""API 키 보관(109 ② C · J5·J8) — 자격 증명 관리자 판정·읽기 순서·옮기기·키 관문. `python tests/test_key_109.py` 로 실행(네트워크 없음).

**진짜 자격 증명 관리자는 부르지 않는다.** 개발 PC 에는 진짜 키가 있다. 읽기·쓰기·지우기 셋을 가짜(`_World`)로 바꿔 끼우고,
진짜 API(ctypes)를 재는 테스트 하나만 테스트 전용 이름으로 쓰고 바로 지운다(`test_real_credential_api_roundtrip`).
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="fifa109key_"))
os.environ["FIFA_DATA_DIR"] = str(SCRATCH)
for _k in ("NEXON_API_KEY", "FIFA_SERVER_URL", "FIFA_DEMO"):
    os.environ.pop(_k, None)

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

import app_main  # noqa: E402
import check_api  # noqa: E402
import config  # noqa: E402
import tray  # noqa: E402

sys.path.insert(0, os.path.join(_ROOT, "tools"))
import release  # noqa: E402

assert config.DATA_DIR == SCRATCH
assert os.environ.get("FIFA_NO_CRED") == "1"
_REAL = (config._cred_read, config._cred_write, config._cred_delete)


class _World:
    """진짜 자격 증명 대신 쓰는 가짜 + 그 PC 의 .env. 안에서는 FIFA_NO_CRED 가 풀리고 ENV_PATH 는 '기본 위치'가 된다.

    read_fail: 읽기가 OSError · write_ok: 쓰기 성공 여부 · lie: 쓰기는 성공이라 하지만 저장하지 않는다(다시 읽어 달라지는 경우)."""

    def __init__(self, env_text: str | None = "", cred: str = "", read_fail: bool = False, write_ok: bool = True,
                 lie: bool = False):
        self.dir = Path(tempfile.mkdtemp(prefix="w109_"))
        self.env = self.dir / ".env"
        if env_text is not None:
            self.env.write_text(env_text, encoding="utf-8")
        self.cred, self.read_fail, self.write_ok, self.lie = cred, read_fail, write_ok, lie
        self.calls: list[str] = []

    def __enter__(self):
        self._keep = (config.ENV_PATH, config.DEFAULT_ENV_PATH, config.API_KEY, config.KEY_STATE, config.OWNER_DIST_ON,
                      config._cred_read, config._cred_write, config._cred_delete, os.environ.get("FIFA_NO_CRED"),
                      os.environ.get(config.API_KEY_VAR))
        config.ENV_PATH = config.DEFAULT_ENV_PATH = self.env
        os.environ.pop("FIFA_NO_CRED", None)
        config._cred_read, config._cred_write, config._cred_delete = self._read, self._write, self._delete
        return self

    def __exit__(self, *a):
        (config.ENV_PATH, config.DEFAULT_ENV_PATH, config.API_KEY, config.KEY_STATE, config.OWNER_DIST_ON,
         config._cred_read, config._cred_write, config._cred_delete, no_cred, env_key) = self._keep
        os.environ["FIFA_NO_CRED"] = no_cred or "1"
        if env_key is None:
            os.environ.pop(config.API_KEY_VAR, None)
        else:
            os.environ[config.API_KEY_VAR] = env_key
        shutil.rmtree(self.dir, ignore_errors=True)

    def _read(self, target=None):
        self.calls.append("read")
        if self.read_fail:
            raise OSError("가짜 읽기 실패")
        return self.cred

    def _write(self, value, target=None):
        self.calls.append("write")
        if not self.write_ok:
            return False
        if not self.lie:
            self.cred = value
        return True

    def _delete(self, target=None):
        self.calls.append("delete")
        self.cred = ""
        return True

    def env_lines(self) -> list[str]:
        return self.env.read_text(encoding="utf-8").splitlines() if self.env.exists() else []


# ── 판정 ──────────────────────────────────────────────────────────────

def test_cred_allowed_decision_table():
    with _World() as w:
        assert config.cred_allowed()
        os.environ["FIFA_NO_CRED"] = "1"
        assert not config.cred_allowed(), "FIFA_NO_CRED=1 인데 자격 증명을 쓴다"
        os.environ.pop("FIFA_NO_CRED")
        keep_demo = config.DEMO
        config.DEMO = True
        try:
            assert not config.cred_allowed(), "둘러보기인데 자격 증명을 쓴다"
        finally:
            config.DEMO = keep_demo
        # 판정은 쓸 때마다 그 시점의 ENV_PATH — 테스트가 ENV_PATH 만 바꿔도(DATA_DIR 은 그대로) 건드리지 않는다
        config.ENV_PATH = w.dir / "other" / ".env"
        assert not config.cred_allowed(), "ENV_PATH 가 기본 위치가 아닌데 자격 증명을 쓴다"
        config.ENV_PATH = w.env
        assert config.cred_allowed()


def test_fifa_data_dir_override_is_not_the_default_location():
    """FIFA_DATA_DIR 로 옮긴 폴더(스크래치·서버 시험 · CLAUDE.md 의 run_local 안내)는 기본 위치가 아니다.
    기본 위치를 DATA_DIR 에서 잡으면 그 폴더도 "기본" 이 되어, 거기서 넣은 시험용 키가 진짜 키를 덮는다(②-1 리뷰).
    새 프로세스로 잰다 — 기본 위치는 import 때 정해진다. FIFA_NO_CRED 는 뺀다(머리가 주면 판정이 늘 거짓이라 아무것도 안 잰다).
    고친 코드에서는 자격 증명을 부르지 않는다(cred_allowed 가 거짓이라 import 때 읽지 않는다)."""
    import subprocess
    import tempfile
    root = Path(__file__).resolve().parent.parent
    d = Path(tempfile.mkdtemp(prefix="cred109_"))
    env = {k: v for k, v in os.environ.items() if k not in ("FIFA_NO_CRED", "FIFA_DEMO", config.API_KEY_VAR)}
    env["FIFA_DATA_DIR"] = str(d)
    code = ("import config, sys; "
            "sys.stdout.write(f'{config.cred_allowed()}|{config.ENV_PATH == config.DATA_DIR / \".env\"}|"
            "{config.DEFAULT_ENV_PATH == config._default_data_dir() / \".env\"}')")
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        assert r.returncode == 0, r.stderr[-2000:]
        allowed, env_in_dir, default_ok = r.stdout.strip().split("|")
        assert env_in_dir == "True" and default_ok == "True", r.stdout   # .env 는 옮긴 폴더에 있다
        assert allowed == "False", "FIFA_DATA_DIR 로 옮긴 폴더에서 진짜 자격 증명을 건드린다"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_save_api_key_with_foreign_env_path_never_touches_credentials():
    """`test_ownerdist_sched.py:86` 꼴 — ENV_PATH 만 임시로 바꾸고 save_api_key 를 부른다. 막지 않았으면 진짜 키가 바뀐다."""
    with _World() as w:
        config.ENV_PATH = w.dir / "tmpdir" / ".env"
        config.save_api_key("other")
        assert config.API_KEY == "other" and config.KEY_STATE == "ok"
        assert w.calls == [], w.calls
        assert (w.dir / "tmpdir" / ".env").read_text(encoding="utf-8").splitlines() == ["NEXON_API_KEY=other"]
        assert w.cred == ""


# ── import 때 키 정하기 ───────────────────────────────────────────────

def test_resolve_prefers_env_line_then_credential():
    with _World("NEXON_API_KEY=from_env\n", cred="from_cred") as w:
        assert config._resolve_key() == ("from_env", "ok")
        assert w.calls == [], "키가 .env 에 있으면 자격 증명을 안 읽는다"
    with _World("FIFA_X=1\n", cred="from_cred") as w:
        assert config._resolve_key() == ("from_cred", "ok")
    with _World("", cred="") as w:
        assert config._resolve_key() == ("", "none")
    with _World(None, cred="from_cred") as w:   # .env 파일이 없다
        assert config._resolve_key() == ("from_cred", "ok")


def test_resolve_ignores_inherited_environment_key():
    """업데이트 설치기가 Popen 으로 물려준 환경의 옛 NEXON_API_KEY 가 새 자격 증명을 덮으면 안 된다(M59)."""
    with _World("", cred="new_cred") as w:
        os.environ[config.API_KEY_VAR] = "stale_inherited"
        assert config._resolve_key() == ("new_cred", "ok")


def test_resolve_read_failure_is_fail_not_none():
    """읽기 실패와 '키 없음' 을 가른다(T3 — 둘 다 빈 값이면 키 창·구단주 분포 토글 끔·내 특성 지우기가 일어난다)."""
    with _World("", cred="x", read_fail=True):
        assert config._resolve_key() == ("", "fail")
    with _World("NEXON_API_KEY=k\n", read_fail=True):
        assert config._resolve_key() == ("k", "ok"), ".env 에 키가 있으면 자격 증명 실패와 무관"


def test_resolve_unreadable_env_is_fail_only_where_credentials_are_used():
    with _World("", cred="x") as w:
        keep = config._read_env_disk
        config._read_env_disk = lambda: None   # .env 를 못 읽음
        try:
            assert config._resolve_key() == ("", "fail"), "자격 증명을 쓰는 PC 에서 .env 를 못 읽으면 키가 거기 있는지 모른다"
            os.environ["FIFA_NO_CRED"] = "1"
            assert config._resolve_key() == ("", "none"), "자격 증명을 안 쓰는 자리는 옛 동작(빈 값) 그대로"
        finally:
            config._read_env_disk = keep


# ── 옮기기(J5) ─────────────────────────────────────────────────────────

def test_migrate_moves_key_and_removes_only_that_line():
    with _World("FIFA_WEB_DATA=1\nNEXON_API_KEY=live_abc\nDEV_NEXON_API_KEY=dev_keep\nFIFA_SERVER_URL=http://x\n") as w:
        os.environ[config.API_KEY_VAR] = "live_abc"
        assert config.migrate_key_to_cred() == "moved"
        assert w.cred == "live_abc"
        assert w.env_lines() == ["FIFA_WEB_DATA=1", "DEV_NEXON_API_KEY=dev_keep", "FIFA_SERVER_URL=http://x"], w.env_lines()
        assert config.API_KEY == "live_abc" and config.KEY_STATE == "ok"
        assert config.API_KEY_VAR not in os.environ, "옮긴 뒤 os.environ 의 옛 값이 남았다"
        n = len(w.calls)
        assert config.migrate_key_to_cred() == "none" and len(w.calls) == n, "두 번째 켤 때 또 쓴다"


def test_migrate_deletes_env_line_only_when_reread_equals():
    """쓰고 → 다시 읽어 같을 때만 .env 줄을 지운다. 쓰기가 성공이라 하고 실제로는 안 남았으면 줄이 그대로여야 한다(키 소실 방지)."""
    with _World("NEXON_API_KEY=live_abc\n", lie=True) as w:
        assert config.migrate_key_to_cred() == "fail"
        assert w.env_lines() == ["NEXON_API_KEY=live_abc"], "다시 읽어 다른데 .env 줄을 지웠다"
        assert w.calls == ["write", "read"], w.calls   # 막지 않았으면: 쓰기 성공만 믿고 줄을 지웠을 것
    with _World("NEXON_API_KEY=live_abc\n", write_ok=False) as w:
        assert config.migrate_key_to_cred() == "fail"
        assert w.env_lines() == ["NEXON_API_KEY=live_abc"]
    with _World("NEXON_API_KEY=live_abc\n", read_fail=True) as w:
        assert config.migrate_key_to_cred() == "fail"
        assert w.env_lines() == ["NEXON_API_KEY=live_abc"]


def test_migrate_skips_where_credentials_are_not_used():
    with _World("NEXON_API_KEY=live_abc\n") as w:
        os.environ["FIFA_NO_CRED"] = "1"
        assert config.migrate_key_to_cred() == "skip"
        assert w.calls == [] and w.env_lines() == ["NEXON_API_KEY=live_abc"]


def test_save_api_key_default_folder_goes_to_credentials_and_drops_env_line():
    with _World("FIFA_X=1\nNEXON_API_KEY=old\n", cred="old") as w:
        os.environ[config.API_KEY_VAR] = "old"
        config.API_KEY = "old"
        config.save_api_key(" new_key ")
        assert w.cred == "new_key" and config.API_KEY == "new_key" and config.KEY_STATE == "ok"
        assert w.env_lines() == ["FIFA_X=1"], "자격 증명에 쓴 뒤 .env 의 옛 키 줄이 남았다(다음 시작에 .env 가 이긴다)"
        assert config.API_KEY_VAR not in os.environ
        # 자격 증명을 못 쓰면 옛 그대로 .env
        w.write_ok = False
        config.save_api_key("newer")
        assert w.env_lines() == ["FIFA_X=1", "NEXON_API_KEY=newer"] and config.API_KEY == "newer"
        # 빈 키 — 자격 증명의 옛 키도 지워 다음 시작에 되살아나지 않게
        w.write_ok, w.cred = True, "leftover"
        config.save_api_key("")
        assert w.cred == "" and config.KEY_STATE == "none"


def test_save_api_key_keeps_owner_dist_off_rule():
    """키가 바뀌면 구단주 분포 토글을 'key' 사유로 끈다(옛 규칙 그대로). 반대로 조용한 다시 읽기는 끄지 않는다(M60)."""
    with _World("NEXON_API_KEY=a\n") as w:
        calls = []
        keep = config.set_owner_dist
        config.set_owner_dist = lambda on, why="": calls.append((on, why))
        try:
            config.OWNER_DIST_ON = "2026-11-01T00:00:00"
            config.API_KEY = "a"
            config.save_api_key("b")
            assert calls == [(False, "key")], calls
            calls.clear()
            config.OWNER_DIST_ON = "2026-11-01T00:00:00"
            w.cred = "c"
            w.env.write_text("", encoding="utf-8")
            assert config.reload_key() == "ok" and config.API_KEY == "c"
            assert calls == [] and config.OWNER_DIST_ON == "2026-11-01T00:00:00", "조용한 다시 읽기가 구단주 분포를 끄려 했다"
        finally:
            config.set_owner_dist = keep


# ── 보험 ───────────────────────────────────────────────────────────────

def test_owner_dist_reads_say_unknown_when_key_state_is_fail():
    """V2 — 빈 키가 '토글·키 꺼짐' 으로 읽혀 돌던 바퀴를 끝내고 지운다. fail 이면 모른다(None)."""
    with _World("FIFA_X=1\n") as w:
        config.KEY_STATE, config.API_KEY = "fail", ""
        assert config.read_owner_dist() is None and config.owner_dist_allowed() is None
        config.KEY_STATE = "none"
        assert config.read_owner_dist() is not None and config.owner_dist_allowed() is not None


def test_release_private_needles_stops_when_key_unreadable():
    keep = (config.KEY_STATE, config.API_KEY)
    try:
        config.KEY_STATE, config.API_KEY = "fail", ""
        try:
            with contextlib.redirect_stdout(io.StringIO()):   # release.fail 이 [FAIL] 줄을 찍는다
                release.private_needles()
            raise AssertionError("키를 못 읽는데 키 유출 검사가 통과한다")
        except SystemExit as e:
            assert e.code == 1
        config.KEY_STATE, config.API_KEY = "ok", "live_k"
        assert release.private_needles()["API 키"] == [b"live_k"]
    finally:
        config.KEY_STATE, config.API_KEY = keep


def test_check_api_messages_follow_key_state():
    keep = (config.KEY_STATE, config.API_KEY)
    try:
        ctx = check_api.CheckCtx()
        config.KEY_STATE, config.API_KEY = "fail", ""
        a = check_api.check_key_loaded(ctx)
        config.KEY_STATE = "none"
        b = check_api.check_key_loaded(ctx)
        assert "읽지 못했습니다" in a.lines[0] and "없습니다" in b.lines[0] and a.lines != b.lines, (a.lines, b.lines)
        assert a.stop and b.stop
        config.KEY_STATE = "fail"
        ua, ub = check_api.user_key(ctx), None
        config.KEY_STATE = "none"
        ub = check_api.user_key(ctx)
        assert "읽지 못했습니다" in ua.lines[0] and "읽지 못했습니다" not in ub.lines[0]
    finally:
        config.KEY_STATE, config.API_KEY = keep


# ── 키 관문 ─────────────────────────────────────────────────────────────

class _Shell:
    def __init__(self):
        self.quit_calls = 0

    def quit_app(self, fast=False):
        self.quit_calls += 1


def _fake_dialog(result, log, reread_to=None):
    class _Dlg:
        QUIT_RESULT = app_main.KeyReadFailDialog.QUIT_RESULT

        def __init__(self, *a, **k):
            log.append("창")

        def exec(self):
            if reread_to is not None:   # [다시 읽기] 가 성공한 것처럼 — 창 안에서 reload_key 가 돈다
                config.KEY_STATE, config.API_KEY = reread_to
            return result
    return _Dlg


def _gate(shell, from_tray, dlg_result=None, quiet_reread=None, dlg_reread=None):
    """_key_gate 를 불러 (넘어갔나, 창 로그, 조용한 다시 읽기 횟수, 구단주 분포 토글 값)을 돌려준다."""
    log, rereads = [], []
    keep = (app_main.KeyReadFailDialog, config.reload_key, config.save_api_key)

    def reload_key():
        rereads.append(1)
        if quiet_reread is not None:
            config.KEY_STATE, config.API_KEY = quiet_reread
        return config.KEY_STATE

    app_main.KeyReadFailDialog = _fake_dialog(dlg_result, log, dlg_reread)
    config.reload_key = reload_key
    config.save_api_key = lambda *a, **k: (_ for _ in ()).throw(AssertionError("관문이 save_api_key 를 불렀다"))
    try:
        return app_main._key_gate(shell, from_tray), log, len(rereads)
    finally:
        app_main.KeyReadFailDialog, config.reload_key, config.save_api_key = keep


def test_gate_ok_state_does_nothing():
    with _World():
        config.KEY_STATE, config.API_KEY = "ok", "k"
        sh = _Shell()
        assert _gate(sh, False) == (True, [], 0) and sh.quit_calls == 0
        config.KEY_STATE, config.API_KEY = "none", ""
        assert _gate(sh, False) == (True, [], 0), "키 없음은 관문이 아니라 키 창(ApiKeyDialog) 길"


def test_gate_quiet_reread_ok_shows_no_window_and_skips_save_api_key():
    """조용한 다시 읽기가 ok 면 창 없이 평소 시작. save_api_key 를 거치면 구단주 분포 토글이 'key' 사유로 꺼진다(M60)."""
    with _World():
        config.KEY_STATE, config.API_KEY = "fail", ""
        config.OWNER_DIST_ON = "2026-11-01T00:00:00"
        sh = _Shell()
        ok, log, n = _gate(sh, False, quiet_reread=("ok", "k"))
        assert (ok, log, n) == (True, [], 1) and config.API_KEY == "k" and sh.quit_calls == 0
        assert config.OWNER_DIST_ON == "2026-11-01T00:00:00"


def test_gate_quiet_reread_none_goes_on_to_the_key_dialog_path():
    with _World():
        config.KEY_STATE, config.API_KEY = "fail", ""
        ok, log, n = _gate(_Shell(), False, quiet_reread=("none", ""))
        assert ok is True and log == [] and n == 1 and config.KEY_STATE == "none"


def test_gate_still_fail_shows_window_and_each_exit_path():
    Q, A, R = (app_main.KeyReadFailDialog.QUIT_RESULT, QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected)
    for result, from_tray, want_ok, want_quit in [
        (R, False, False, 1),    # 일반 시작의 X·Esc = 끝내기
        (Q, False, False, 1),    # [끝내기]
        (R, True, False, 0),     # 트레이 [열기]로 뜬 창의 X·Esc = 트레이에 남음
        (Q, True, False, 1),     # [끝내기] 는 트레이에서도 끝낸다
        (A, False, True, 0),     # [다시 읽기] 성공 — 평소 시작
        (A, True, True, 0),
    ]:
        with _World():
            config.KEY_STATE, config.API_KEY = "fail", ""
            sh = _Shell()
            ok, log, n = _gate(sh, from_tray, dlg_result=result, dlg_reread=("ok", "k") if result == A else None)
            assert (ok, log, n, sh.quit_calls) == (want_ok, ["창"], 1, want_quit), (result, from_tray, ok, log, n, sh.quit_calls)


def test_key_read_fail_dialog_retry_and_quit_buttons():
    with _World():
        dlg = app_main.KeyReadFailDialog()
        assert dlg.btn_retry.isDefault(), "Enter 로 받을 기본 단추가 없다(96 K3)"
        keep = config.reload_key
        try:
            config.reload_key = lambda: "fail"
            dlg._on_retry()
            assert dlg.lb_msg.text() and dlg.result() == 0, "여전히 못 읽는데 창이 닫혔다"
            config.reload_key = lambda: "ok"
            dlg._on_retry()
            assert dlg.result() == QDialog.DialogCode.Accepted
        finally:
            config.reload_key = keep
        dlg2 = app_main.KeyReadFailDialog()
        dlg2.btn_quit.click()
        assert dlg2.result() == app_main.KeyReadFailDialog.QUIT_RESULT


# ── main 길: 일반 시작 · --tray · 실행본 둘 ─────────────────────────────────

class _Boom:
    def __init__(self, *a, **k):
        raise AssertionError("키를 못 읽은 채 창·API 를 만들었다")


def _stop_shell():
    sh = app_main._SHELL
    if sh is not None:
        for t in (sh._release_timer, sh._update_timer, sh._tray_timer):
            t.stop()
        for s in (sh.sched, sh.owner_sched, sh.card_sched):
            if s is not None:
                s.shutdown()
    app_main._SHELL = None


class _Run:
    """main() 을 가짜 창으로 부른다 — QApplication.exec 는 0 을 돌려주고, shell.start·창 생성을 센다."""

    def __init__(self, argv, state, key, dialog_result=None, claim=True, notice_ok=True):
        self.argv, self.state, self.key, self.dialog_result, self.claim, self.notice_ok = argv, state, key, dialog_result, claim, notice_ok
        self.log: list[str] = []

    def __enter__(self):
        self.keep = (app_main._setup_app, app_main.MainWindow, app_main.FCOnlineAPI, app_main.KeyReadFailDialog,
                     app_main.ApiKeyDialog, QApplication.exec, tray.AppShell.start, config.SINGLE_INSTANCE,
                     config.NOTICE_ACCEPTED, config.migrate_key_to_cred, tray.SingleInstance, config.reload_key,
                     app_main._SHELL, app_main.GameWatch.start, app_main.intro_needed)
        run = self

        class _Win:
            def __init__(self, api):
                run.log.append("창")

            def __getattr__(self, name):
                return lambda *a, **k: None

        class _Single(self.keep[10]):   # 진짜 SingleInstance(신호 포함)에서 claim 만 가짜 — 뮤텍스를 잡지 않는다
            def claim(self):
                run.log.append("claim")
                return run.claim

        real_start = tray.AppShell.start
        app_main._setup_app = lambda app: None
        app_main.MainWindow = _Win
        app_main.FCOnlineAPI = lambda *a, **k: run.log.append("API")
        app_main.KeyReadFailDialog = _fake_dialog(self.dialog_result, self.log)
        app_main.ApiKeyDialog = lambda *a, **k: (_ for _ in ()).throw(AssertionError("키 창이 떴다"))
        QApplication.exec = lambda self_: 0
        tray.AppShell.start = lambda self_, tray_mode=False: (run.log.append("start"), real_start(self_, tray_mode=tray_mode))[1]
        app_main.GameWatch.start = lambda self_: None
        app_main.intro_needed = lambda: False
        config.SINGLE_INSTANCE = True
        tray.SingleInstance = _Single
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION if self.notice_ok else 0
        config.migrate_key_to_cred = lambda: run.log.append("옮기기") or "none"
        config.reload_key = lambda: (run.log.append("다시 읽기"), config.KEY_STATE)[1]
        config.KEY_STATE, config.API_KEY = self.state, self.key
        return self

    def go(self):
        try:
            return app_main.main(self.argv)
        finally:
            _stop_shell()

    def __exit__(self, *a):
        (app_main._setup_app, app_main.MainWindow, app_main.FCOnlineAPI, app_main.KeyReadFailDialog, app_main.ApiKeyDialog,
         QApplication.exec, tray.AppShell.start, config.SINGLE_INSTANCE, config.NOTICE_ACCEPTED, config.migrate_key_to_cred,
         tray.SingleInstance, config.reload_key, app_main._SHELL, app_main.GameWatch.start, app_main.intro_needed) = self.keep


def test_main_normal_start_with_fail_builds_no_window_api_or_schedules():
    with _World():
        with _Run(["app"], "fail", "", dialog_result=QDialog.DialogCode.Rejected) as r:
            rc = r.go()
            assert rc == 0
            assert r.log == ["claim", "옮기기", "다시 읽기", "창"], r.log
            assert "API" not in r.log and "start" not in r.log, f"읽지 못한 채 창·API·예약이 섰다: {r.log}"
            assert r.log.count("창") == 1, "창은 키 관문 창 하나뿐"


def test_main_normal_start_with_fail_then_quiet_ok_runs_as_usual():
    with _World():
        with _Run(["app"], "fail", "", dialog_result=QDialog.DialogCode.Rejected) as r:
            config.reload_key = lambda: (r.log.append("다시 읽기"), setattr(config, "KEY_STATE", "ok"),
                                         setattr(config, "API_KEY", "k"), "ok")[3]
            rc = r.go()
            assert rc == 0, rc
            assert r.log == ["claim", "옮기기", "다시 읽기", "API", "창", "start"], r.log   # 관문 창 없이 평소 시작


def test_main_tray_with_fail_shows_no_window_and_runs_schedules():
    """--tray + fail: 게임 위에 막는 창을 띄우지 않는다(tray.AppShell.start 규칙) · 메인 창·API 도 없다 · 예약 셋은 돈다(J8)."""
    with _World():
        with _Run(["app", "--tray"], "fail", "") as r:
            config.OWNER_DIST_ON = "2026-11-01T00:00:00"
            rc = r.go()
            assert rc == 0
            assert "창" not in r.log and "API" not in r.log and "다시 읽기" not in r.log, r.log
            assert "start" in r.log, "예약이 안 섰다"
            # 보험 ⓐ — 예약 틱이 읽는 값이 모름(None)이라 구단주 분포 바퀴를 끝내고 지우지 않는다
            assert config.read_owner_dist() is None and config.OWNER_DIST_ON == "2026-11-01T00:00:00"


def test_main_second_instance_does_not_migrate_the_key():
    """claim 이 실패한 두 번째 실행은 .env 를 다시 쓰지 않는다 — 옮기기는 claim 뒤(M61)."""
    with _World():
        with _Run(["app"], "ok", "k", claim=False) as r:
            assert r.go() == 0 and r.log == ["claim"], r.log
        with _Run(["app"], "ok", "k", claim=True) as r:
            r.go()
            assert r.log[:2] == ["claim", "옮기기"], r.log


def test_open_window_from_tray_with_fail_stays_in_tray_on_x():
    with _World():
        config.NOTICE_ACCEPTED = config.NOTICE_VERSION
        config.KEY_STATE, config.API_KEY = "fail", ""
        shell = _Shell()
        keep = (app_main.KeyReadFailDialog, config.reload_key, app_main.MainWindow, app_main.intro_needed)
        app_main.KeyReadFailDialog = _fake_dialog(QDialog.DialogCode.Rejected, [])
        config.reload_key = lambda: config.KEY_STATE
        app_main.MainWindow = _Boom
        app_main.intro_needed = lambda: False
        try:
            assert app_main._open_window(shell, from_tray=True) is None and shell.quit_calls == 0
            assert app_main._open_window(shell, from_tray=False) is None and shell.quit_calls == 1
        finally:
            app_main.KeyReadFailDialog, config.reload_key, app_main.MainWindow, app_main.intro_needed = keep


# ── 진짜 자격 증명 API(테스트 전용 이름) ─────────────────────────────────────

def test_real_credential_api_roundtrip():
    """ctypes 호출이 실제로 읽고 쓰고 지운다 — 한글·긴 값·없는 이름. 진짜 이름(CRED_TARGET)은 건드리지 않는다."""
    target = "피파전적관리/TEST_ONLY_109_" + str(os.getpid())
    assert target != config.CRED_TARGET
    if sys.platform != "win32":
        return
    try:
        assert _REAL[0](target) == "", "없는 이름은 빈 값(정상)이어야 한다"
        for value in ("live_abc123", "한글 키 값 😀", "k" * 300):
            assert _REAL[1](value, target), value[:10]
            assert _REAL[0](target) == value, value[:10]
        assert _REAL[2](target) is True
        assert _REAL[0](target) == ""
        assert _REAL[2](target) is True, "이미 없는 것을 지워도 성공이어야 한다"
    finally:
        _REAL[2](target)


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
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
