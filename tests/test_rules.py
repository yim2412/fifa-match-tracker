"""규칙 검사 — CLAUDE.md 에 "이렇게 한다"로만 적혀 있어 검토자가 매번 눈으로 대조하던 것을 기계로.

검토 방법 13(ROADMAP): 여기 있는 규칙은 계획 검토에서 다시 보지 않는다. 규칙마다 두 단언 —
① 지금 코드에 위반이 0 ② **위반을 심은 가짜 소스를 실제로 잡는다**(못 잡으면 ①이 비어 있어도 통과한다).

    python tests/test_rules.py
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_FILES = sorted(p for p in ROOT.glob("*.py"))
ALL_PY = APP_FILES + sorted(ROOT.glob("tools/*.py")) + sorted(ROOT.glob("tests/*.py"))

# 색은 theme.py 에서만 — 예외는 넥슨 데이터센터 원본 색(능력치 등급표: 화면 테마가 아니라 넥슨이 정한 값)
COLOR_ALLOW = {("playerinfo.py", "STAT_COLOR_BUCKETS"),
               ("playerinfo.py", "_SKILLMOVE_ON")}          # 스크래핑 표식(HTML 안의 색 글자)
# 임시 정렬(TEMP B-TREE)을 봐줄 표 — 행이 수십 개라 통째 정렬이 공짜다(계정·시즌·수집 상태).
# 1만 행대(matches · match_players · snapshot_rows · elo_history)는 여기 넣지 않는다
SQL_SMALL_TABLES = {"accounts", "seasons", "team_colors", "collect_state", "collect_lock", "snapshots",
                    "cut_elo"}
# 그 밖에 봐줄 문장 — 앱이 안 타는 경로만
SQL_TEMP_ALLOW = {
    # 종류 없는 load_details 는 테스트만 쓴다 — 앱은 항상 종류를 준다(app_main.load_saved)
    "store.py:load_details": re.compile(r"FROM matches m JOIN"),
}
MODAL_STATIC = {"QFileDialog", "QInputDialog", "QColorDialog", "QFontDialog", "QMessageBox"}
MODAL_GUARDED_BASE = {"QDialog.exec", "QApplication.exec"}   # 하위 클래스의 exec() 도 이걸로 막힌다


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):     # 'mod.attr' — 받는 쪽이 이름이 아니면 '.attr'
        return (f.value.id if isinstance(f.value, ast.Name) else "") + "." + f.attr
    return ""


def _kw(node: ast.Call, name: str):
    return next((k.value for k in node.keywords if k.arg == name), None)


def _const(v) -> object:
    return v.value if isinstance(v, ast.Constant) else None


# ── 규칙 ─────────────────────────────────────────────────────────────────────

def check_combo(srcs: dict[str, str]) -> list[str]:
    """QComboBox 를 직접 만들지 않는다 — 휠이 지나가면 값이 바뀐다(전역 규칙 8 · NoScrollComboBox)."""
    out = []
    for rel, src in srcs.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and _call_name(node) in ("QComboBox", "QtWidgets.QComboBox"):
                out.append(f"{rel}:{node.lineno} QComboBox(...) — NoScrollComboBox 를 쓴다")
    return out


def check_dialog_fit(srcs: dict[str, str]) -> list[str]:
    """새 대화상자는 fit_to_screen 으로 연다 — 고정 크기는 150% 노트북에서 넘친다(CLAUDE.md PyQt 9)."""
    out = []
    for rel, src in srcs.items():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    (isinstance(b, ast.Name) and b.id == "QDialog") for b in node.bases):
                if "fit_to_screen(" not in ast.get_source_segment(src, node):
                    out.append(f"{rel}:{node.lineno} class {node.name}(QDialog) — fit_to_screen 이 없다")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(src, node)
                makes = [c for c in ast.walk(node) if isinstance(c, ast.Call) and _call_name(c) == "QDialog"]
                if makes and "fit_to_screen(" not in body:
                    out.append(f"{rel}:{makes[0].lineno} {node.name}() 의 QDialog(...) — fit_to_screen 이 없다")
    return out


def check_encoding(srcs: dict[str, str]) -> list[str]:
    """글자 파일·자식 프로세스 출력은 인코딩을 적는다 — 이 PC 는 UTF-8, 남의 PC 는 CP949(전역 인코딩 1·3)."""
    out = []
    for rel, src in srcs.items():
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            has_enc = _kw(node, "encoding") is not None
            if name == "open":
                mode = _const(node.args[1]) if len(node.args) > 1 else _const(_kw(node, "mode"))
                if not (isinstance(mode, str) and "b" in mode) and not has_enc:
                    out.append(f"{rel}:{node.lineno} open(...) — encoding= 이 없다")
            elif name.endswith((".read_text", ".write_text")) and not has_enc:
                out.append(f"{rel}:{node.lineno} {name.lstrip('.')}(...) — encoding= 이 없다")
            elif name in ("subprocess.run", "subprocess.Popen", "subprocess.check_output"):
                text = _const(_kw(node, "text")) or _const(_kw(node, "universal_newlines"))
                if text and not has_enc:
                    out.append(f"{rel}:{node.lineno} {name}(text=True) — encoding= 이 없다(실패 원인이 None 으로 사라진다)")
    return out


def check_signals(srcs: dict[str, str]) -> list[str]:
    """스레드 → 화면 신호에 list·dict 형을 쓰지 않는다 — PyQt 가 통째로 깊은 복사한다(CLAUDE.md PyQt 8)."""
    out = []
    for rel, src in srcs.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and _call_name(node) == "pyqtSignal":
                for a in node.args:
                    if isinstance(a, ast.Name) and a.id in ("list", "dict"):
                        out.append(f"{rel}:{node.lineno} pyqtSignal({a.id}, …) — object 로")
    return out


def guarded_modals(smoke_src: str) -> set[str]:
    """test_ui_smoke 머리의 모달 차단이 막는 이름들 — 'QMessageBox.warning' · 'QDialog.exec' 꼴."""
    got = set(re.findall(r"^(\w+\.\w+) = _no_modal", smoke_src, re.M))
    for names, cls in re.findall(r"for _n in \(([^)]*)\):\s*\n\s*setattr\((\w+)", smoke_src):
        got |= {f"{cls}.{n}" for n in re.findall(r"\"(\w+)\"", names)}
    return got


def check_modals(srcs: dict[str, str], guarded: set[str]) -> list[str]:
    """앱이 쓰는 모달은 화면 스모크가 막고 있어야 한다 — 안 막으면 회귀가 '실패' 대신 '멈춤'(CLAUDE.md PyQt 6)."""
    out = []
    for rel, src in srcs.items():
        tree = ast.parse(src)
        menus: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and _call_name(node.value) == "QMenu":
                menus |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            cls, _, attr = name.partition(".")
            if cls in MODAL_STATIC and attr and name not in guarded:
                if attr.startswith("get") or attr in ("warning", "information", "critical", "question",
                                                      "about", "aboutQt"):
                    out.append(f"{rel}:{node.lineno} {name}(...) — test_ui_smoke 모달 차단에 없다")
            if attr in ("exec", "exec_") and cls in menus and "QMenu.exec" not in guarded:
                out.append(f"{rel}:{node.lineno} {cls}.exec() (QMenu) — test_ui_smoke 모달 차단에 없다")
    return out


HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def check_colors(srcs: dict[str, str]) -> list[str]:
    """색은 theme.py 에서만 — 테마를 바꿀 때 고칠 곳이 하나여야 한다(CLAUDE.md 파일 표)."""
    out = []
    for rel, src in srcs.items():
        if rel == "theme.py":
            continue
        tree = ast.parse(src)
        allowed: list[tuple[int, int]] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and (rel, t.id) in COLOR_ALLOW:
                        allowed.append((node.lineno, node.end_lineno))
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if any(a <= line <= b for a, b in allowed):
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and HEX.fullmatch(node.value.strip()):
                out.append(f"{rel}:{line} 색 \"{node.value}\" — theme.py 에 둔다")
            if isinstance(node, ast.Call) and _call_name(node) == "QColor" and len(node.args) >= 3 \
                    and all(isinstance(a, ast.Constant) for a in node.args[:3]):
                out.append(f"{rel}:{line} QColor(r, g, b) — theme.py 에 둔다")
    return out


# ── SQL 실행 계획 ────────────────────────────────────────────────────────────

SQL_FILES = ("store.py", "rankcollect.py")


def collect_sql() -> dict[str, set[str]]:
    """파싱·수집 테스트를 그대로 돌리며 store·rankcollect 가 낸 SQL 을 모은다 — {문장: {'파일:함수'}}.
    값이 박힌 문장(trace 는 ? 를 값으로 바꿔 준다)이라 그대로 EXPLAIN 할 수 있다."""
    seen: dict[str, set[str]] = {}
    real = sqlite3.connect

    def connect(*a, **k):
        conn = real(*a, **k)

        def trace(sql):
            f = sys._getframe(1)
            while f:
                fn = Path(f.f_code.co_filename).name
                if fn in SQL_FILES:
                    seen.setdefault(" ".join(sql.split()), set()).add(f"{fn}:{f.f_code.co_name}")
                    return
                f = f.f_back
        conn.set_trace_callback(trace)
        return conn

    sqlite3.connect = connect
    try:
        for name in ("test_parsing", "test_rankcollect"):
            spec = importlib.util.spec_from_file_location(f"_rules_{name}", ROOT / "tests" / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            with contextlib.redirect_stdout(io.StringIO()):
                spec.loader.exec_module(mod)
                for k, v in sorted(vars(mod).items()):
                    if k.startswith("test_") and callable(v):
                        try:
                            v()
                        except Exception:  # noqa: BLE001 — 그 테스트의 성패는 그 파일이 본다
                            pass
        _drive_uncovered()
    finally:
        sqlite3.connect = real
    return seen


def _drive_uncovered() -> None:
    """파싱·수집 테스트가 안 부르는 읽기 함수 — 화면 스모크만 부른다(Qt 를 띄우기엔 무겁다)."""
    import store
    with tempfile.TemporaryDirectory() as d:
        conn = store.open_db(Path(d) / "f.db")
        try:
            store.all_match_ids(conn)
            store.list_accounts(conn)
            store.load_details_by_ids(conn, ["a", "b"])
            store.load_seasons(conn)
            store.load_team_colors(conn, ["가", "나"])
            store.recent_searches(conn)
        finally:
            conn.close()


def sql_functions() -> set[str]:
    """SELECT 를 품은 함수 — 이게 전부 collect_sql 에 나와야 검사가 빈 게 아니다."""
    out = set()
    for fn in SQL_FILES:
        src = _src(ROOT / fn)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and re.search(r"\bSELECT\b", ast.get_source_segment(src, node) or ""):
                out.add(f"{fn}:{node.name}")
    return out


def temp_sorts(seen: dict[str, set[str]]) -> tuple[list[str], int]:
    import rankcollect
    import store
    dbs = []
    for schema in (store.SCHEMA, rankcollect.SCHEMA):
        c = sqlite3.connect(":memory:")
        c.executescript(schema)
        dbs.append(c)
    bad, n = [], 0
    for sql, who in seen.items():
        if not re.match(r"(SELECT|UPDATE|DELETE|INSERT|WITH)\b", sql, re.I):
            continue
        plan = None
        for c in dbs:
            try:
                plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql))
                break
            except sqlite3.Error:
                continue
        if plan is None:
            continue                      # 테스트가 만든 임시 표 등 — 두 스키마 어디에도 없다
        n += 1
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+(\w+)", sql, re.I))
        if tables and tables <= SQL_SMALL_TABLES:
            continue
        if "TEMP B-TREE" in plan and not any(
                w in SQL_TEMP_ALLOW and SQL_TEMP_ALLOW[w].search(sql) for w in who):
            bad.append(f"{sorted(who)} {sql[:120]} => {plan}")
    return bad, n


# ── 테스트 ───────────────────────────────────────────────────────────────────

def _all(files) -> dict[str, str]:
    return {_rel(p): _src(p) for p in files}


def test_no_direct_combobox():
    assert check_combo({"x.py": "from PyQt6.QtWidgets import QComboBox\nc = QComboBox()\n"}), "심은 위반을 못 잡는다"
    assert not check_combo({"x.py": "c = NoScrollComboBox()\n"})
    assert check_combo(_all(APP_FILES)) == []


def test_dialogs_fit_to_screen():
    bad = "class D(QDialog):\n    def __init__(self):\n        self.resize(600, 400)\n"
    bad_fn = "def f(self):\n    dlg = QDialog(self)\n    dlg.resize(1, 1)\n"
    ok = "def f(self):\n    dlg = QDialog(self)\n    fit_to_screen(dlg, 560, 480)\n"
    assert len(check_dialog_fit({"x.py": bad, "y.py": bad_fn})) == 2, "심은 위반을 못 잡는다"
    assert not check_dialog_fit({"x.py": ok})
    assert check_dialog_fit(_all(APP_FILES)) == []


def test_text_io_names_encoding():
    bad = ("open('a.txt')\nopen('a.txt', 'w')\nPath('a').read_text()\np.write_text('x')\n"
           "subprocess.run(['git'], capture_output=True, text=True)\n")
    ok = ("open('a.bin', 'rb')\nopen('a', encoding='utf-8')\nPath('a').read_text(encoding='utf-8')\n"
          "subprocess.run(['git'], capture_output=True)\n"
          "subprocess.run(['git'], text=True, encoding='utf-8')\n")
    assert len(check_encoding({"x.py": bad})) == 5, check_encoding({"x.py": bad})
    assert check_encoding({"x.py": ok}) == []
    assert check_encoding(_all(ALL_PY)) == []


def test_signals_carry_objects():
    assert check_signals({"x.py": "s = pyqtSignal(list, int)\nt = pyqtSignal(dict)\n"}) and \
        len(check_signals({"x.py": "s = pyqtSignal(list, int)\nt = pyqtSignal(dict)\n"})) == 2
    assert not check_signals({"x.py": "s = pyqtSignal(object, int)\n"})
    assert check_signals(_all(APP_FILES)) == []


def test_every_modal_the_app_uses_is_blocked_in_smoke():
    guarded = guarded_modals(_src(ROOT / "tests" / "test_ui_smoke.py"))
    # 차단 목록을 못 읽으면 아래 검사가 전부 위반이 되거나(엄격) — 반대로 읽기가 넓으면 비어 있어도 통과한다
    assert {"QMessageBox.warning", "QMessageBox.question", "QDialog.exec", "QApplication.exec"} <= guarded, guarded
    bad = ("QFileDialog.getOpenFileName(None)\nQMessageBox.about(None, 'a', 'b')\n"
           "def f():\n    m = QMenu()\n    m.exec(pos)\n")
    assert len(check_modals({"x.py": bad}, guarded)) == 3, check_modals({"x.py": bad}, guarded)
    assert not check_modals({"x.py": "QMessageBox.warning(None, 'a', 'b')\n"}, guarded)
    assert check_modals(_all(APP_FILES), guarded) == []


def test_colors_only_in_theme():
    bad = "p.setPen(QColor('#ffffff'))\nc = QColor(10, 20, 30)\n"
    assert len(check_colors({"x.py": bad})) == 2, "심은 위반을 못 잡는다"
    assert not check_colors({"theme.py": "A = '#ffffff'\n"})
    # 예외 표는 그 이름의 할당 안에서만 — 같은 파일 다른 자리는 잡힌다
    assert check_colors({"playerinfo.py": "STAT_COLOR_BUCKETS = ['#ffffff']\nX = '#000000'\n"}) == \
        ["playerinfo.py:2 색 \"#000000\" — theme.py 에 둔다"]
    assert check_colors(_all(APP_FILES)) == []


def test_every_query_avoids_temp_sort():
    seen = collect_sql()
    need, ran = sql_functions(), {w for ws in seen.values() for w in ws}
    # 막지 않았으면: 함수가 안 돌면 그 쿼리는 검사 밖이다 — "TEMP 0" 이 빈 검사일 수 있다
    assert need <= ran, f"SQL 을 한 번도 안 낸 함수(검사 밖): {sorted(need - ran)}"
    bad, n = temp_sorts(seen)
    assert n >= 100, f"검사한 문장 {n}개 — 수집이 비었다"
    assert bad == [], "\n".join(bad)


def test_temp_sort_check_catches_unindexed_order():
    # 인덱스 없는 열로 정렬하는 문장을 심으면 잡혀야 한다(예외 표가 넓어 다 통과시키지 않는지도)
    bad, n = temp_sorts({"SELECT payload FROM matches ORDER BY payload": {"store.py:load_details"},
                         "SELECT match_id FROM matches WHERE match_id = 'a'": {"store.py:x"},
                         "SELECT nickname FROM accounts ORDER BY nickname": {"store.py:y"},
                         "SELECT m.payload FROM matches m JOIN accounts a ON a.ouid = m.match_id "
                         "ORDER BY m.payload": {"store.py:z"}})
    # 작은 표만 읽는 정렬은 봐주되, 큰 표가 하나라도 끼면 잡는다
    assert n == 4 and len(bad) == 2, (n, bad)


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
