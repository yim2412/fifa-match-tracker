"""계획 검토 준비 — 검토자(모델)가 할 기계적인 일을 스크립트로 먼저 해 둔다(ROADMAP "검토 방법" 8·9·11).

    python tools/review_kit.py bundle "1.3.1"            # 계획 절이 닿는 코드 조각 묶음(근거 묶음)
    python tools/review_kit.py diff e9cb7d8 [HEAD]       # 회차 사이 바뀐 계획 줄 + 닿는 진입점 표 행
    python tools/review_kit.py diff e9cb7d8 --bundle     #   … 바뀐 줄의 이름만으로 묶음까지
    python tools/review_kit.py ledger "1.3.1"            # 주장 장부의 빈 표(①·⑩ — 줄 하나 = 행 하나)

결과는 표준 출력(마크다운). `--out 파일` 이면 파일로(UTF-8).

왜: 검토자 1명이 회당 13만~22만 토큰을 썼고 대부분이 큰 파일(app_main.py 5천 줄)을 처음부터 읽고
"어디가 바뀌었나"를 다시 찾는 데 들었다(2026-10-04~05 실측). 그 둘은 판단이 아니라 검색이다.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP = "docs/ROADMAP.md"
PY_GLOBS = ("*.py", "tools/*.py", "tests/*.py")

MAX_DEF_LINES = 40       # 정의가 이보다 길면 머리만 — 나머지는 줄 번호로 찾아 읽게
MAX_CALL_SITES = 8       # 부르는 곳이 이보다 많으면 개수만 더 적는다
MAX_USE_SITES = 5        # 정의는 없고 쓰이기만 하는 이름(속성·표 이름·문자열) — 위치 몇 곳만
MAX_AMBIGUOUS = 4        # 같은 이름의 정의가 이보다 많으면 위치만(흔한 이름 — `run`·`set_data`)
REF_PAD = 2              # `파일.py:줄` 참조의 앞뒤 여유 줄

# 백틱 안이 이 이름 하나뿐이면 코드 이름이 아니다(파이썬 상수·흔한 단어)
SKIP_NAMES = {"None", "True", "False", "self", "cls", "int", "str", "float", "list", "dict", "set",
              "tuple", "bool", "date", "datetime", "object", "is", "or", "and", "not"}

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
BACKTICK = re.compile(r"`([^`\n]+)`")
FILE_REF = re.compile(r"\b([A-Za-z_][\w/]*\.py):(\d+)(?:-(\d+))?")
HEADING = re.compile(r"^(#{1,6})\s")


# ── 계획 문서 ────────────────────────────────────────────────────────────────

def section(lines: list[str], query: str) -> tuple[int, list[str]]:
    """제목에 query 가 들어간 첫 절 — (첫 줄 번호(1부터), 줄들). 같은 수준 이상의 다음 제목에서 끝난다."""
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and query in line:
            level = len(m.group(1))
            end = len(lines)
            for j in range(i + 1, len(lines)):
                n = HEADING.match(lines[j])
                if n and len(n.group(1)) <= level:
                    end = j
                    break
            return i + 1, lines[i:end]
    raise SystemExit(f"[FAIL] 제목에 '{query}' 가 들어간 절이 없다")


def heading_path(lines: list[str], lineno: int) -> str:
    """lineno(1부터) 줄이 속한 제목 경로 — '## 1.3.1 … › ### ③ 승률 그래프'."""
    stack: list[tuple[int, str]] = []
    for line in lines[:lineno]:
        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            stack = [s for s in stack if s[0] < level] + [(level, line.strip())]
    return " › ".join(s[1] for s in stack) or "(머리말)"


def identifiers(text: str) -> list[str]:
    """백틱 안의 코드 이름 — `set_data(points, *, …)` 는 `set_data`. 순서 유지·중복 제거."""
    out: list[str] = []
    for span in BACKTICK.findall(text):
        m = IDENT.match(span.strip())
        if not m:
            continue
        name = m.group(0).removeprefix("self.")
        if span.strip()[m.end():m.end() + 1] in ("…", "*"):     # `test_window_shrinks_…` — 앞부분으로 찾는다
            name += "*"
        if name in SKIP_NAMES or name.endswith(".py") or len(name) < 3:
            continue
        if name.split(".")[-1] in ("py", "md", "txt", "json", "db", "iss", "env", "exe"):
            continue
        if name not in out:
            out.append(name)
    return out


def file_refs(text: str) -> list[tuple[str, int, int]]:
    out = []
    for f, a, b in FILE_REF.findall(text):
        ref = (f, int(a), int(b or a))
        if ref not in out:
            out.append(ref)
    return out


# ── 코드 색인 ────────────────────────────────────────────────────────────────

@dataclass
class Def:
    file: str           # ROOT 기준 상대 경로(/)
    qual: str           # 'Class.meth' · 'func' · 'CONST'
    kind: str           # 'def' · 'class' · 'const'
    start: int
    end: int


@dataclass
class Index:
    root: Path
    files: dict[str, list[str]] = field(default_factory=dict)
    defs: list[Def] = field(default_factory=list)

    def by_name(self, name: str) -> list[Def]:
        last = name.split(".")[-1]
        return [d for d in self.defs if d.qual == name or d.qual.split(".")[-1] == last]


def build_index(root: Path = ROOT) -> Index:
    idx = Index(root)
    for pat in PY_GLOBS:
        for p in sorted(root.glob(pat)):
            rel = p.relative_to(root).as_posix()
            src = p.read_text(encoding="utf-8", errors="replace")
            idx.files[rel] = src.splitlines()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    idx.defs.append(Def(rel, node.name, "def", node.lineno, node.end_lineno))
                elif isinstance(node, ast.ClassDef):
                    idx.defs.append(Def(rel, node.name, "class", node.lineno, node.end_lineno))
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            idx.defs.append(Def(rel, f"{node.name}.{sub.name}", "def",
                                                sub.lineno, sub.end_lineno))
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            idx.defs.append(Def(rel, t.id, "const", node.lineno, node.end_lineno))
    return idx


def resolve(idx: Index, name: str) -> list[Def]:
    """`mod.func` → mod.py 의 func · `Class.meth` → 그 메서드 · `T.CHART_X` 처럼 별칭이면 끝 이름으로.
    끝이 `*` 면 앞부분이 같은 이름 전부(`test_window_shrinks_*`)."""
    if name.endswith("*"):
        stem = name[:-1].split(".")[-1]
        return [d for d in idx.defs if d.qual.split(".")[-1].startswith(stem)]
    parts = name.split(".")
    if len(parts) >= 2:
        stem, rest = parts[0], ".".join(parts[1:])
        hits = [d for d in idx.defs if Path(d.file).stem == stem and d.qual == rest]
        if hits:
            return hits
        hits = [d for d in idx.defs if d.qual == name]
        if hits:
            return hits
        tail = ".".join(parts[-2:]) if len(parts) > 2 else None
        hits = [d for d in idx.defs if tail and d.qual == tail]
        if hits:
            return hits
    return idx.by_name(name)


def call_sites(idx: Index, name: str, skip: list[Def]) -> list[tuple[str, int, str]]:
    last = name.rstrip("*").split(".")[-1]
    word = re.compile(rf"\b{re.escape(last)}" + (r"\w*" if name.endswith("*") else r"\b"))
    out = []
    for rel, lines in idx.files.items():
        own = [(d.start, d.start) for d in skip if d.file == rel]   # 정의 줄만 뺀다(본문 안 재귀 호출은 남긴다)
        for i, line in enumerate(lines, 1):
            if word.search(line) and not any(a <= i <= b for a, b in own):
                out.append((rel, i, line.strip()))
    return out


# ── 출력 ─────────────────────────────────────────────────────────────────────

def _def_block(idx: Index, d: Def) -> list[str]:
    lines = idx.files[d.file]
    if d.kind == "class":
        # 클래스 본문은 수백 줄 — 머리 줄과 메서드 목록만
        heads = [f"{d.start:>5}  {lines[d.start - 1]}"]
        for m in idx.defs:
            if m.file == d.file and m.qual.startswith(d.qual + "."):
                heads.append(f"{m.start:>5}  {lines[m.start - 1].rstrip()}")
        return heads
    body = [f"{n:>5}  {lines[n - 1]}" for n in range(d.start, min(d.end, d.start + MAX_DEF_LINES - 1) + 1)]
    if d.end - d.start + 1 > MAX_DEF_LINES:
        body.append(f"      … (+{d.end - d.start + 1 - MAX_DEF_LINES}줄 — {d.file}:{d.start}-{d.end})")
    return body


def bundle(idx: Index, names: list[str], refs: list[tuple[str, int, int]], title: str) -> str:
    out = [f"# 근거 묶음 — {title}", "",
           "검토자에게: 아래는 계획이 이름으로 짚은 코드다. 더 필요하면 줄 번호로 그 자리만 읽는다.", ""]
    missing: list[str] = []
    used_only: list[tuple[str, list[tuple[str, int, str]]]] = []
    shown: set[tuple[str, int]] = set()
    touched: set[str] = set()
    for name in names:
        defs = [d for d in resolve(idx, name) if (d.file, d.start) not in shown]
        if not defs:
            if resolve(idx, name):
                continue                       # 앞에서 다른 이름으로 이미 보였다(`check` · `updatecheck.check`)
            sites = call_sites(idx, name, [])
            (used_only.append((name, sites)) if sites else missing.append(name))
            continue
        shown.update((d.file, d.start) for d in defs)
        out.append(f"## `{name}`")
        if len(defs) > MAX_AMBIGUOUS:
            out.append(f"정의 {len(defs)}곳(흔한 이름 — 위치만): "
                       + " · ".join(f"{d.file}:{d.start} {d.qual}" for d in defs[:12]))
            out.append("")
            continue
        for d in defs:
            touched.add(d.file)
            out.append(f"정의 `{d.file}:{d.start}` ({d.kind} {d.qual})")
            out.append("```python")
            out.extend(_def_block(idx, d))
            out.append("```")
        sites = call_sites(idx, name, defs)
        if sites:
            out.append(f"부르는·쓰는 곳 {len(sites)}:")
            for rel, i, text in sites[:MAX_CALL_SITES]:
                out.append(f"- `{rel}:{i}` {text[:140]}")
            if len(sites) > MAX_CALL_SITES:
                out.append(f"- … +{len(sites) - MAX_CALL_SITES}곳")
        out.append("")
    if refs:
        out.append("## 계획이 줄 번호로 짚은 곳")
        for f, a, b in refs:
            rel = next((r for r in idx.files if r == f or r.endswith("/" + f)), None)
            if rel is None:
                out.append(f"- `{f}:{a}` — 파일 없음")
                continue
            lines = idx.files[rel]
            lo, hi = max(1, a - REF_PAD), min(len(lines), b + REF_PAD, a + MAX_DEF_LINES)
            touched.add(rel)
            out.append(f"`{rel}:{a}{'-' + str(b) if b != a else ''}`")
            out.append("```python")
            out.extend(f"{n:>5}  {lines[n - 1]}" for n in range(lo, hi + 1))
            out.append("```")
        out.append("")
    if used_only:
        out.append("## 정의는 없고 쓰이기만 하는 이름 (속성 · 표 이름 · 문자열)")
        for name, sites in used_only:
            locs = " · ".join(f"{rel}:{i}" for rel, i, _ in sites[:MAX_USE_SITES])
            out.append(f"- `{name}` {len(sites)}곳: {locs}{' …' if len(sites) > MAX_USE_SITES else ''}")
        out.append("")
    if missing:
        out.append("## 코드에 없는 이름 (계획이 새로 만들 것 — 또는 이름이 틀렸다)")
        out.append(" · ".join(f"`{m}`" for m in missing))
        out.append("")
    whole = sum(len("\n".join(idx.files[f])) for f in touched)
    text = "\n".join(out)
    out.insert(2, f"이름 {len(names)}개 · 정의 {len(names) - len(missing) - len(used_only)} · "
                  f"쓰임만 {len(used_only)} · 없음 {len(missing)} · "
                  f"묶음 {len(text):,}자 (짚은 파일 {len(touched)}개 통째로면 {whole:,}자)")
    return "\n".join(out)


# ── 회차 사이 diff ───────────────────────────────────────────────────────────

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(diff_text: str) -> tuple[list[int], list[int]]:
    """-U0 diff → (새 파일에서 더해지거나 바뀐 줄 번호, 지우기만 한 자리의 줄 번호)."""
    added, deleted_at = [], []
    for line in diff_text.splitlines():
        m = HUNK.match(line)
        if m:
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            if count == 0:
                deleted_at.append(max(start, 1))
            else:
                added.extend(range(start, start + count))
    return added, deleted_at


def entry_rows(lines: list[str], changed: list[int], names: set[str]) -> list[tuple[int, str]]:
    """바뀐 줄이 속한 버전 절(## …)의 '진입점' 표에서 바뀐 이름을 품은 행 — 문장에서 빠진 장치는 표로 본다(⑩)."""
    out: list[tuple[int, str]] = []
    tops = sorted({_top_section(lines, n) for n in changed} - {None})
    for top in tops:
        start, sec = top, None
        for i in range(start, len(lines)):
            if i > start and lines[i].startswith("## "):
                break
            m = HEADING.match(lines[i])
            if m:
                sec = lines[i]
            if sec and "진입점" in sec and lines[i].startswith("|") and not set(lines[i]) <= set("|-: "):
                row_names = set(identifiers(lines[i]))
                if row_names & names and (i + 1) not in changed:
                    out.append((i + 1, lines[i]))
    return out


def _top_section(lines: list[str], lineno: int) -> int | None:
    for i in range(min(lineno, len(lines)) - 1, -1, -1):
        if lines[i].startswith("## "):
            return i
    return None


def diff_report(base: str, head: str | None, with_bundle: bool, root: Path = ROOT) -> str:
    rng = [base, head] if head else [base]
    diff = _git(root, "diff", "-U0", *rng, "--", ROADMAP)
    text = _git(root, "show", f"{head}:{ROADMAP}") if head else (root / ROADMAP).read_text(encoding="utf-8")
    lines = text.splitlines()
    added, deleted_at = changed_lines(diff)
    out = [f"# 계획 변경 — {base}..{head or '작업 트리'}", "",
           f"바뀐 줄 {len(added)} · 지우기만 한 자리 {len(deleted_at)} — 검토자는 여기와 아래 진입점 표 행만 본다(검토 방법 9).", ""]
    groups: dict[str, list[int]] = {}
    for n in added:
        groups.setdefault(heading_path(lines, n), []).append(n)
    for path, ns in groups.items():
        out.append(f"## {path}")
        out.extend(f"{n:>5}  {lines[n - 1]}" for n in ns)
        out.append("")
    if deleted_at:
        out.append("## 지우기만 한 자리(내용은 `git diff` 로)")
        out.extend(f"- {n}줄 근처 — {heading_path(lines, n)}" for n in deleted_at)
        out.append("")
    names: list[str] = []
    for n in added:                       # -U0 의 + 줄 번호는 새 파일 안이다
        names += [x for x in identifiers(lines[n - 1]) if x not in names]
    rows = entry_rows(lines, added, set(names))
    if rows:
        out.append("## 바뀐 이름이 걸린 진입점 표 행(안 바뀐 행)")
        out.extend(f"{n:>5}  {row}" for n, row in rows)
        out.append("")
    report = "\n".join(out)
    if with_bundle:
        refs = [r for n in added for r in file_refs(lines[n - 1])]
        for _, row in rows:
            names += [x for x in identifiers(row) if x not in names]
        report += "\n\n" + bundle(build_index(root), names, refs, f"바뀐 줄 ({base}..{head or '작업 트리'})")
    return report


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"[FAIL] git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


# ── 주장 장부 ────────────────────────────────────────────────────────────────

CLAIM_NUM = re.compile(r"\d[\d,.]*\s*(ms|초|분|시간|일|주|MB|GB|KB|px|%|점|경기|줄|개|명|쪽|회|번|배|행|위|요청|토큰|자)")
CLAIM_WORD = re.compile(r"(된다|충분|없다|않는다|못 한다|같다|이면|상한|넘지|막는다|지킨다|0건|0개)")
# 근거 표시 — 먼저 맞는 것 하나. "추정"은 끝내는 조건에 걸리므로 맨 앞
BASIS = [("추정", re.compile(r"추정")),
         ("실측", re.compile(r"실측")),
         ("장부", re.compile(r"\bR\d+\b")),
         ("코드", re.compile(r"\w+\.py:\d+|코드\b|코드\(")),
         ("테스트", re.compile(r"`test_\w+")),
         ("검토", re.compile(r"검토 [AB]")),
         ("사용자", re.compile(r"사용자"))]


def claim_rows(lines: list[str], first: int) -> list[tuple[int, str, str]]:
    rows = []
    in_code = False
    for k, line in enumerate(lines):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s or HEADING.match(line) or set(s) <= set("|-: "):
            continue
        if not (CLAIM_NUM.search(s) or CLAIM_WORD.search(s)):
            continue
        basis = next((name for name, pat in BASIS if pat.search(s)), "?")
        rows.append((first + k, basis, s))
    return rows


def ledger(lines: list[str], first: int, title: str) -> str:
    rows = claim_rows(lines, first)
    counts: dict[str, int] = {}
    for _, b, _ in rows:
        counts[b] = counts.get(b, 0) + 1
    out = [f"# 주장 장부 — {title}", "",
           f"행 {len(rows)} · " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])),
           "", "근거(자동)는 그 줄에 적힌 표시만 본 것이다 — `?` 와 `추정` 행만 채우면 된다. "
               "한 줄에 주장이 여럿이면 그 줄 안에서 나눈다.", "",
           "| # | 줄 | 근거(자동) | 주장 | 근거(확인) |", "|---|---|---|---|---|"]
    for i, (n, b, s) in enumerate(rows, 1):
        cell = s.replace("|", "\\|")
        out.append(f"| {i} | {n} | {b} | {cell[:200]}{'…' if len(cell) > 200 else ''} | |")
    return "\n".join(out)


# ── 진입점 ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bundle", help="계획 절이 닿는 코드 조각 묶음")
    b.add_argument("section")
    d = sub.add_parser("diff", help="회차 사이 바뀐 계획 줄")
    d.add_argument("base")
    d.add_argument("head", nargs="?")
    d.add_argument("--bundle", action="store_true")
    g = sub.add_parser("ledger", help="주장 장부 빈 표")
    g.add_argument("section")
    for p in (b, d, g):
        p.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "diff":
        text = diff_report(a.base, a.head, a.bundle)
    else:
        lines = (ROOT / ROADMAP).read_text(encoding="utf-8").splitlines()
        first, sec = section(lines, a.section)
        body = "\n".join(sec)
        if a.cmd == "bundle":
            text = bundle(build_index(), identifiers(body), file_refs(body), sec[0].lstrip("# ").strip())
        else:
            text = ledger(sec, first, sec[0].lstrip("# ").strip())
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"[OK] {a.out} ({len(text):,}자)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
