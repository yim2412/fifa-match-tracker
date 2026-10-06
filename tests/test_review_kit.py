"""계획 검토 준비 도구(tools/review_kit.py) — 가짜 계획·가짜 코드로. 네트워크 없이(diff 는 임시 git 저장소).

    python tests/test_review_kit.py
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import review_kit as rk  # noqa: E402

PLAN = """# 로드맵
## 1.0.1 — 앞 버전
- `old_func` 는 그대로
## 1.3.1 — 그래프
머리말 `charts.Chart.set_data` 와 `helper(x, *, y)` 를 쓴다. 고칠 곳 charts.py:3-4
### ③ 승률
- 7일 이동평균 `new_func` 을 만든다 — 1만 경기 5ms 안(실측)
- 범위는 그대로 둔다
- `self._cache` 를 비운다 · `test_window_*` · `None`
### 진입점 표
| 진입점 | 거칠 것 |
|---|---|
| 검색 | `helper` → `new_func` |
| 종료 | `shutdown` |
### 환경 행렬
- `new_func` 은 CI 에서도 돈다
## 1.4.1 — 다음
- `later_func`
### 진입점 표
| 진입점 | 거칠 것 |
|---|---|
| 다른 버전 | `new_func` |
"""

CHARTS = '''import x

class Chart:
    def set_data(self, points):
        self._cache = None
        return helper(points)

def helper(points):
    return points
'''

USER = '''from charts import helper
def run():
    return helper([1])
def test_window_a(): pass
def test_window_b(): pass
'''

# 같은 이름이 다른 곳에도 있다 — 이름 풀이가 끝 이름으로만 찾으면 이것까지 섞인다
OTHER = '''class Other:
    def set_data(self, rows):
        return rows

def helper(rows):
    return rows
'''


def _tree():
    d = tempfile.mkdtemp()
    Path(d, "charts.py").write_text(CHARTS, encoding="utf-8")
    Path(d, "user.py").write_text(USER, encoding="utf-8")
    Path(d, "other.py").write_text(OTHER, encoding="utf-8")
    return Path(d)


@contextlib.contextmanager
def _limits(**kw):
    old = {k: getattr(rk, k) for k in kw}
    for k, v in kw.items():
        setattr(rk, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(rk, k, v)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
                   cwd=root, check=True, capture_output=True)


def test_section_stops_at_same_level_and_keeps_subsections():
    lines = PLAN.splitlines()
    first, sec = rk.section(lines, "1.3.1")
    assert lines[first - 1] == "## 1.3.1 — 그래프", first
    assert any("### 진입점 표" in s for s in sec), "하위 절이 빠졌다"
    assert not any("1.4.1" in s for s in sec) and not any("old_func" in s for s in sec), sec[-2:]


def test_identifiers_strip_args_self_and_skip_words():
    names = rk.identifiers("`charts.Chart.set_data` `helper(x, *, y)` `self._cache` `None` `a.py` `test_window_*`")
    assert names == ["charts.Chart.set_data", "helper", "_cache", "test_window_*"], names
    assert rk.identifiers("`test_window_shrinks_…`") == ["test_window_shrinks_*"]


def test_resolve_module_class_method_and_prefix():
    idx = rk.build_index(_tree())
    got = rk.resolve(idx, "charts.Chart.set_data")
    assert [(d.file, d.qual) for d in got] == [("charts.py", "Chart.set_data")], got
    assert sorted(d.file for d in rk.resolve(idx, "helper")) == ["charts.py", "other.py"], "맨 이름은 전부"
    assert [d.file for d in rk.resolve(idx, "charts.helper")] == ["charts.py"], "모듈을 적었으면 그 모듈만"
    assert [d.file for d in rk.resolve(idx, "Chart.set_data")] == ["charts.py"], "클래스를 적었으면 그 클래스만"
    assert [d.file for d in rk.resolve(idx, "T.Chart.set_data")] == ["charts.py"], "별칭.클래스.메서드"
    assert sorted(d.qual for d in rk.resolve(idx, "test_window_*")) == ["test_window_a", "test_window_b"]
    assert rk.resolve(idx, "new_func") == []


def test_class_shows_method_list_not_body():
    out = rk.bundle(rk.build_index(_tree()), ["Chart"], [], "t")
    assert "    3  class Chart:" in out and "    4      def set_data(self, points):" in out, out
    assert "self._cache = None" not in out, "클래스 본문을 통째로 실었다"


def test_long_definitions_and_many_callers_are_cut():
    idx = rk.build_index(_tree())
    with _limits(MAX_DEF_LINES=2, MAX_CALL_SITES=1):
        out = rk.bundle(idx, ["Chart.set_data", "helper"], [], "t")
    assert "… (+1줄 — charts.py:4-6)" in out, out
    assert "    6          return helper(points)" not in out, "자른 뒤의 줄이 실렸다"
    assert "- … +" in out, "부르는 곳 상한을 안 지켰다"


def test_diff_report_on_a_real_git_history():
    root = _tree()
    (root / "docs").mkdir()
    road = root / rk.ROADMAP
    road.write_text(PLAN, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "1")
    text = PLAN.replace("1만 경기 5ms 안(실측)", "1만 경기 9ms 안(실측) charts.py:8").replace("- `old_func` 는 그대로\n", "")
    road.write_text(text, encoding="utf-8")

    rep = rk.diff_report("HEAD", None, with_bundle=False, root=root)
    assert "바뀐 줄 1 · 지우기만 한 자리 1" in rep, rep
    assert "## # 로드맵 › ## 1.3.1 — 그래프 › ### ③ 승률" in rep, "바뀐 줄의 절이 안 나왔다"
    assert "9ms" in rep and "## 지우기만 한 자리" in rep
    assert "| 검색 | `helper` → `new_func` |" in rep, "바뀐 이름이 걸린 진입점 행이 빠졌다"
    assert "다른 버전" not in rep, "다른 버전 절의 진입점 표까지 읽었다"
    assert "# 근거 묶음" not in rep

    full = rk.diff_report("HEAD", None, with_bundle=True, root=root)
    tail = full.split("# 근거 묶음")[1]
    assert "`helper`" in tail and "`new_func`" in tail, "바뀐 줄·진입점 행의 이름이 묶음에 없다"
    assert "`charts.py:8`" in tail, "바뀐 줄의 줄 번호 참조가 묶음에 없다"
    assert "_cache" not in tail, "안 바뀐 줄의 이름까지 묶었다"
    try:
        rk.diff_report("없는커밋", None, with_bundle=False, root=root)
        raise AssertionError("git 실패를 삼켰다")
    except SystemExit as e:
        assert "[FAIL] git" in str(e), e


DONE = "docs/DONE.md"


def _live_version() -> tuple[str, str]:
    """실제 문서의 버전 절 → (문서, "2.2.1"). 계획 문서의 첫 버전 절, 없으면(다음 버전 계획 전) 끝난 기록의 마지막 절.
    번호를 박지 않는다 — 2.1.1 을 박아 둔 탓에 그 절이 DONE.md 로 옮겨 간 뒤로 이 파일이 빨갰다."""
    import re
    for doc, pick in ((rk.ROADMAP, 0), (DONE, -1)):
        found = [m.group(1) for s in (ROOT / doc).read_text(encoding="utf-8").splitlines()
                 if (m := re.match(r"## (\d+\.\d+\.\d+)\b", s))]
        if found:
            return doc, found[pick]
    raise AssertionError("계획 문서에도 끝난 기록에도 버전 절이 없다")


def test_main_writes_each_command():
    d = Path(tempfile.mkdtemp())
    doc, v = _live_version()
    keep, rk.ROADMAP = rk.ROADMAP, doc
    try:
        _main_writes(d, v)
    finally:
        rk.ROADMAP = keep


def _main_writes(d: Path, v: str) -> None:
    assert rk.main(["ledger", f"## {v}", "--out", str(d / "l.md")]) == 0
    assert (d / "l.md").read_text(encoding="utf-8").startswith(f"# 주장 장부 — {v}"), "ledger 가 다른 걸 썼다"
    assert rk.main(["bundle", f"## {v}", "--out", str(d / "b.md")]) == 0
    assert (d / "b.md").read_text(encoding="utf-8").startswith(f"# 근거 묶음 — {v}")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        buf.reconfigure = lambda **k: None          # main 이 콘솔 인코딩을 맞춘다 — 가짜 출력엔 없는 메서드
        sys.stdout.reconfigure = buf.reconfigure
        rk.main(["ledger", f"## {v}"])
    assert buf.getvalue().startswith("# 주장 장부"), "--out 없이 표준 출력으로 안 나왔다"
    assert not (d / "x").exists()


def test_bundle_shows_definition_callers_usage_and_missing():
    root = _tree()
    lines = PLAN.splitlines()
    _, sec = rk.section(lines, "1.3.1")
    body = "\n".join(sec)
    out = rk.bundle(rk.build_index(root), rk.identifiers(body), rk.file_refs(body), "t")
    assert "정의 `charts.py:8` (def helper)" in out, out
    assert "`user.py:3` return helper([1])" in out, "부르는 곳이 빠졌다"
    assert "- … +" not in out, "상한 안인데 '더 있음'을 붙였다"
    assert "`charts.py:8` def helper" not in out, "정의 줄을 부르는 곳으로 셌다"
    assert "- `_cache` 1곳: charts.py:5" in out, "쓰이기만 하는 이름을 못 찾았다"
    assert "`new_func`" in out.split("코드에 없는 이름")[1], "새 이름이 '없음'에 안 나왔다"
    assert "    3  class Chart:" in out, "줄 번호 참조(charts.py:3-4)가 빠졌다"
    assert "old_func" not in out and "later_func" not in out, "다른 버전 절이 섞였다"


def test_bundle_shows_each_definition_once():
    idx = rk.build_index(_tree())
    out = rk.bundle(idx, ["helper", "charts.helper"], [], "t")
    assert out.count("정의 `charts.py:8`") == 1, out


def test_changed_lines_parses_hunks():
    diff = ("diff --git a/x b/x\n@@ -3 +3 @@\n-a\n+b\n@@ -10,0 +11,2 @@\n+c\n+d\n"
            "@@ -20,2 +22,0 @@\n-e\n-f\n@@ -30,1 +30 @@\n-g\n+h\n")
    added, deleted = rk.changed_lines(diff)
    assert added == [3, 11, 12, 30], added
    assert deleted == [22], deleted


def test_entry_rows_pick_unchanged_rows_sharing_a_changed_name():
    lines = PLAN.splitlines()
    changed = [next(i + 1 for i, s in enumerate(lines) if "new_func" in s and "이동평균" in s)]
    rows = rk.entry_rows(lines, changed, set(rk.identifiers(lines[changed[0] - 1])))
    assert [r for _, r in rows] == ["| 검색 | `helper` → `new_func` |"], rows
    # 바뀐 줄이 진입점 표보다 뒤(환경 행렬)여도 같은 버전 절의 표를 처음부터 본다
    after = [next(i + 1 for i, s in enumerate(lines) if "CI 에서도" in s)]
    rows = rk.entry_rows(lines, after, set(rk.identifiers(lines[after[0] - 1])))
    assert [r for _, r in rows] == ["| 검색 | `helper` → `new_func` |"], rows


def test_heading_path():
    lines = PLAN.splitlines()
    n = next(i + 1 for i, s in enumerate(lines) if "범위는" in s)
    assert rk.heading_path(lines, n) == "# 로드맵 › ## 1.3.1 — 그래프 › ### ③ 승률", rk.heading_path(lines, n)


def test_ledger_rows_and_basis():
    lines = PLAN.splitlines()
    first, sec = rk.section(lines, "1.3.1")
    rows = rk.claim_rows(sec, first)
    texts = {s: b for _, b, s in rows}
    assert texts.get("- 7일 이동평균 `new_func` 을 만든다 — 1만 경기 5ms 안(실측)") == "실측", rows
    assert "- 범위는 그대로 둔다" not in texts, "주장이 아닌 줄을 행으로"
    assert not any(s.startswith("|---") for s in texts)
    lines_no = [n for n, _, s in rows if "이동평균" in s][0]
    assert lines[lines_no - 1].startswith("- 7일"), "줄 번호가 문서와 안 맞는다"
    assert rk.claim_rows(["- 10ms 면 충분하다(추정 · 실측은 나중)"], 1)[0][1] == "추정", "추정이 다른 표시에 가렸다"


def test_real_plan_bundle_runs():
    # 실제 계획에서 — 절 하나의 묶음이 그 파일들을 통째로 읽는 것보다 훨씬 작아야 쓸모가 있다
    doc, v = _live_version()
    lines = (ROOT / doc).read_text(encoding="utf-8").splitlines()
    _, sec = rk.section(lines, f"## {v}")
    body = "\n".join(sec)
    out = rk.bundle(rk.build_index(), rk.identifiers(body), rk.file_refs(body), v)
    head = out.splitlines()[2]
    assert "정의" in head and "통째로면" in head, head
    size = int(head.split("묶음 ")[1].split("자")[0].replace(",", ""))
    whole = int(head.split("통째로면 ")[1].split("자")[0].replace(",", ""))
    assert size * 3 < whole, head

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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
