"""배포판 만들기 — 빌드 → zip · 설치 파일 → 개인정보 검사 → 릴리스 명령 출력.

    python tools/release.py            # 전부 하고, 공개 명령만 출력한다
    python tools/release.py --skip-build   # dist 가 이미 최신일 때

공개(gh release)는 하지 않는다 — 외부에 올라가는 일이라 사람이 명령을 보고 친다.
v0.2.0 은 이 과정을 손으로 했고, 개인정보 검사는 일회용 스크립트였다(2026-10-02).
다음 릴리스에서 그 검사를 빼먹거나, 대조 없는 "0건"을 믿지 않게 여기에 묶는다.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import nexon_api  # noqa: E402

APP_DIR_NAME = "피파전적관리"
SPEC = ROOT / "피파전적관리.spec"
ISS = ROOT / "installer" / "피파전적관리.iss"
DIST = ROOT / "dist"
ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
]
# 반드시 들어 있어야 할 문자열 — 0건이면 검사가 압축 안을 못 보는 것이다
CONTROLS = {
    "출처 문구": nexon_api.ATTRIBUTION.encode("utf-8"),
    "키 확인 닉네임": nexon_api.KEY_CHECK_NICKNAME.encode("utf-8"),
    "모듈 이름 crashlog": b"crashlog",
}
ZLIB_WINDOW = 2_000_000  # exe 안 zlib 덩어리 하나를 풀 때 읽는 최대 바이트


def ok(msg: str) -> None:
    print(f"[OK]   {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", flush=True)
    raise SystemExit(1)


# ── 검사 대상 문자열 ──────────────────────────────────────────────────
def _variants(text: str) -> list[bytes]:
    out = []
    for t in {text, text.replace("\\", "/")}:
        for enc in ("utf-8", "utf-16-le", "cp949"):
            try:
                out.append(t.encode(enc))
            except UnicodeEncodeError:
                pass
    return out


def private_needles() -> dict[str, list[bytes]]:
    """이 PC 에서 읽는다 — 저장소에 이름·이메일을 적지 않기 위해."""
    home = Path.home()
    needles = {
        "홈 폴더 경로": _variants(str(home)),
        "사용자 이름 경로": _variants(f"Users\\{home.name}"),
        "프로젝트 경로": _variants(str(ROOT)),
    }
    if config.API_KEY:
        needles["API 키"] = [config.API_KEY.encode()]
    try:
        email = subprocess.run(["git", "config", "user.email"], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace").stdout.strip()
    except OSError:
        email = ""
    if email:
        needles["git 이메일"] = [email.encode()]
    for extra in filter(None, os.environ.get("RELEASE_SCAN_EXTRA", "").split(",")):
        needles[f"추가({extra[:2]}…)"] = _variants(extra.strip())
    return needles


# ── 검사 ─────────────────────────────────────────────────────────────
def blobs_of(name: str, data: bytes):
    """파일 바이트와, exe·zip 안의 zlib 압축 덩어리를 푼 것들(PyInstaller 는 모듈을 zlib 로 넣는다)."""
    yield data
    if name.lower().endswith((".exe", ".pyz", ".zip")):
        i = 0
        while (i := data.find(b"\x78\x9c", i)) >= 0:
            try:
                yield zlib.decompressobj().decompress(data[i:i + ZLIB_WINDOW])
            except zlib.error:
                pass
            i += 2


def scan(files, needles: dict[str, list[bytes]], controls: dict[str, bytes]):
    """files: (이름, 바이트) 들. 돌려주는 값: (개인정보 적중 {라벨: 파일들}, 대조 적중 수 {라벨: n})."""
    leaks: dict[str, set] = {}
    seen = {k: 0 for k in controls}
    for name, data in files:
        for blob in blobs_of(name, data):
            for label, variants in needles.items():
                if any(v and v in blob for v in variants):
                    leaks.setdefault(label, set()).add(name)
            for label, c in controls.items():
                if c in blob:
                    seen[label] += 1
    return leaks, seen


def zip_files(path: Path):
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith("/"):
                yield n, z.read(n)


def preflight_problems(version: str, notes: str, dirty: str) -> list[str]:
    """빌드 전에 멈춰야 할 이유들. 비면 진행."""
    out = []
    if not notes:
        out.append(f"CHANGELOG.md 에 '## {version}' 절이 없다 — 받는 사람이 읽을 변경 내용부터 쓴다")
    if dirty:
        out.append("커밋 안 한 변경이 있다 — 배포판이 어느 커밋인지 모르게 된다\n" + dirty)
    return out


def scan_verdict(leaks: dict, seen: dict) -> str | None:
    """검사 결과로 멈출 이유. 대조가 하나라도 0 이면 '0건'을 믿지 않는다."""
    missing = [k for k, n in seen.items() if n == 0]
    if missing:
        return f"대조 문자열을 못 찾았다 {missing} — 검사가 압축 안을 못 보고 있다. 0건을 믿지 않는다"
    if leaks:
        return "개인정보가 들어 있다: " + "; ".join(f"{k} → {sorted(v)[:3]}" for k, v in leaks.items())
    return None


# ── 단계 ─────────────────────────────────────────────────────────────
def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def find_iscc() -> Path | None:
    return next((p for p in ISCC_CANDIDATES if p.is_file()), None)


def run(cmd: list[str], what: str) -> None:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stdout + r.stderr)[-2000:])
        fail(f"{what} 실패 (종료 코드 {r.returncode})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-build", action="store_true", help="PyInstaller 빌드를 건너뛴다")
    args = ap.parse_args()

    version = config.APP_VERSION
    num = version.lstrip("v")
    notes = changelog_section(version)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace").stdout.strip()
    problems = preflight_problems(version, notes, dirty)
    if problems:
        fail("\n       ".join(problems))
    ok(f"버전 {version} · CHANGELOG 절 있음 · 작업 트리 깨끗함")

    iscc = find_iscc()
    if iscc is None:
        fail("Inno Setup 6(ISCC.exe)이 없다 — https://jrsoftware.org/isdl.php")

    app_dir = DIST / APP_DIR_NAME
    if not args.skip_build:
        print("…  PyInstaller 빌드 (보통 30~60초)", flush=True)
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)], "빌드")
    if not (app_dir / f"{APP_DIR_NAME}.exe").is_file():
        fail(f"{app_dir} 에 exe 가 없다")
    ok("빌드 결과 있음")

    zip_path = DIST / f"{APP_DIR_NAME}-{version}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:  # 한글 이름에 UTF-8 플래그가 붙는다
        for p in sorted(app_dir.rglob("*")):
            z.write(p, p.relative_to(DIST).as_posix())
    ok(f"zip {zip_path.name} ({zip_path.stat().st_size / 2**20:.1f} MB)")

    setup = DIST / f"{APP_DIR_NAME}-setup-{version}.exe"
    setup.unlink(missing_ok=True)
    run([str(iscc), "/Q", f"/DAppVersion={num}", str(ISS)], "설치 파일 컴파일")
    if not setup.is_file():
        fail(f"설치 파일이 안 생겼다: {setup.name}")
    ok(f"설치 파일 {setup.name} ({setup.stat().st_size / 2**20:.1f} MB)")

    # 설치 파일은 LZMA 로 압축돼 안을 못 본다 — 그 입력(onedir 폴더)이 zip 과 같으므로 zip 으로 잰다.
    # 설치 파일 겉(헤더·문자열)과 설치 스크립트·릴리스 본문은 따로 잰다.
    notes_path = DIST / f"release-notes-{version}.md"
    notes_path.write_text(
        notes + "\n\n### 설치\n"
        f"- **`{setup.name}`** 를 받아 실행한다(관리자 권한 필요 없음). 바탕화면 아이콘은 설치할 때 고른다.\n"
        f"- 설치 없이 쓰려면 `{zip_path.name}` 을 폴더째 풀고 `{APP_DIR_NAME}.exe` 실행.\n"
        "- 처음 켜면 API 키를 묻는다 — 넥슨 오픈API에서 애플리케이션을 **서비스 단계**로 등록해 받는다.\n\n"
        f"{nexon_api.ATTRIBUTION}\n", encoding="utf-8")
    files = list(zip_files(zip_path)) + [
        (setup.name, setup.read_bytes()),
        (ISS.name, ISS.read_bytes()),
        (notes_path.name, notes_path.read_bytes()),
    ]
    leaks, seen = scan(files, private_needles(), CONTROLS)
    verdict = scan_verdict(leaks, seen)
    if verdict:
        fail(verdict)
    ok("대조 문자열 " + " · ".join(f"{k} {n}" for k, n in seen.items()))
    ok(f"개인정보 0건 (검사 {len(files)}개 파일 · 설치 파일 내용은 같은 입력인 zip 으로)")

    exists = subprocess.run(["gh", "release", "view", version], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace").returncode == 0
    rel = lambda p: p.relative_to(ROOT).as_posix()  # noqa: E731
    print("\n공개는 확인 후 직접:")
    if exists:
        print(f'  gh release upload {version} "{rel(setup)}" "{rel(zip_path)}" --clobber')
    else:
        print(f'  gh release create {version} "{rel(setup)}" "{rel(zip_path)}" '
              f'--target master --title "{version}" --notes-file "{rel(notes_path)}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
