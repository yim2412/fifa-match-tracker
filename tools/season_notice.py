"""시즌 종료일 공지 — 최신 릴리스 본문에 안 보이는 한 줄을 넣거나 바꾼다(1.3.1 · 사용자 ②).

    python tools/season_notice.py 2026-11-12                 # 시작일은 시즌표 캐시(fifa.db)의 지금 시즌 시작
    python tools/season_notice.py 2026-11-12 --start 2026-09-10

`gh release edit --notes-file` 은 본문을 통째로 갈아서 '## 바뀐 점'을 지우기 쉽다(검토 B 13) — 그래서 지금 본문을
`gh release view` 로 받아 **주석 한 줄만** 바꾼 파일을 만들고, 바꾸기 전후 diff 와 `gh release edit` 명령을 **출력만** 한다
(release.py 와 같은 원칙 — 공개는 사람이 한다). docs/season_end.txt 도 같이 고쳐 다음 릴리스가 이어 붙이게 한다.
고친 뒤 `python check_api.py` 의 "시즌 종료 공지" 줄로 앱이 받는 값을 확인한다.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import updatecheck  # noqa: E402

DIST = ROOT / "dist"
SEASON_END_FILE = ROOT / "docs" / "season_end.txt"   # tools/release.py 와 같아야 한다(test_release 가 대조)
_LINE = re.compile(r"<!--\s*season-end:.*?-->")


def notice_line(end: date, start: date) -> str:
    return f"<!-- season-end: {end.isoformat()} start: {start.isoformat()} -->"


def apply_notice(body: str, end: date, start: date) -> str:
    """본문의 공지 줄만 바꾼다 — 없으면 끝에 한 줄. 나머지 글자는 그대로."""
    line = notice_line(end, start)
    if updatecheck.parse_season_notice(line) is None:
        raise ValueError(f"공지 범위가 이상합니다(시작 < 종료 ≤ 시작 + 120일): {line}")
    if _LINE.search(body):
        return _LINE.sub(line, body, count=1)
    return body.rstrip("\n") + f"\n\n{line}\n"


def write_season_end_file(line: str, path: Path = SEASON_END_FILE) -> None:
    keep = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("#")] if path.exists() else []
    path.write_text("\n".join(keep + [line]) + "\n", encoding="utf-8")


def _current_start() -> date | None:
    import config
    import store
    conn = store.open_db(config.DB_PATH)
    try:
        items = store.load_seasons(conn)
    finally:
        conn.close()
    ended = [s.end for s in items if s.end <= date.today()]
    return max(ended) if ended else None


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 1
    end = date.fromisoformat(argv[0])
    start = date.fromisoformat(argv[argv.index("--start") + 1]) if "--start" in argv else _current_start()
    if start is None:
        print("[FAIL] 지금 시즌 시작을 모릅니다 — --start 로 주세요")
        return 1
    r = subprocess.run(["gh", "release", "view", "--json", "tagName,body"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    if r.returncode != 0:
        print(f"[FAIL] gh release view: {r.stderr.strip()}")
        return 1
    data = json.loads(r.stdout)
    tag, body = data["tagName"], data.get("body") or ""
    new = apply_notice(body, end, start)
    DIST.mkdir(exist_ok=True)
    out = DIST / f"release-notes-{tag}-season.md"
    out.write_text(new, encoding="utf-8")
    write_season_end_file(notice_line(end, start))
    sys.stdout.writelines(difflib.unified_diff(body.splitlines(True), new.splitlines(True), "지금", "바꾼 뒤"))
    print(f"\n[OK] {SEASON_END_FILE.relative_to(ROOT)} 갱신 · 공개는 아래 명령(사람이):")
    print(f'  gh release edit {tag} --notes-file "{out.relative_to(ROOT)}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
