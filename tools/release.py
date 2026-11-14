"""배포판 만들기 — 빌드 → zip · 설치 파일 → 개인정보 검사 → 릴리스 명령 출력.

    python tools/release.py            # 전부 하고, 공개 명령만 출력한다
    python tools/release.py --skip-build   # dist 가 이미 최신일 때

공개(gh release)는 하지 않는다 — 외부에 올라가는 일이라 사람이 명령을 보고 친다.
v0.2.0 은 이 과정을 손으로 했고, 개인정보 검사는 일회용 스크립트였다(2026-10-02).
다음 릴리스에서 그 검사를 빼먹거나, 대조 없는 "0건"을 믿지 않게 여기에 묶는다.


규칙·함정 (CLAUDE.md 파일 표에서 옮김 — 2026-10-07):
배포판 — 빌드 → zip · 설치 파일(Inno Setup 6) → 개인정보 검사(대조 문자열이 안 잡히면 멈춤) → `gh release` 명령 **출력만**.
`.iss` 는 **UTF-8 BOM**(없으면 한글이 ANSI 로 읽힌다)이고 `AppId` GUID 는 바꾸지 않는다(바뀌면 업데이트가 별개 프로그램으로 깔린다).
**릴리스 첨부 이름은 영문**(`ASSET_PREFIX`) — GitHub 가 한글을 지워 v0.2.0 zip 이 `-v0.2.0.zip` 으로 올라갔다(2026-10-02)
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
import notice  # noqa: E402

APP_DIR_NAME = "피파전적관리"
# 릴리스 첨부 파일 이름은 영문 — GitHub 가 한글을 지운다(v0.2.0 의 zip 이 '-v0.2.0.zip' 으로 올라갔다).
# installer/*.iss 의 OutputBaseFilename 과 같아야 한다
ASSET_PREFIX = "FifaMatchTracker"
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
    if config.KEY_STATE == "fail":
        # 키를 읽지 못하면 키 유출 검사가 조용히 빠진다(키가 자격 증명으로 옮겨진 뒤라 .env 에도 없다)
        fail("API 키를 읽지 못해 키 유출 검사를 할 수 없습니다(자격 증명 관리자). 앱을 켜서 키가 읽히는지 본 뒤 다시 돌립니다")
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


def preflight_problems(version: str, notes: str, dirty: str, hidden=(), branch: str | None = None,
                       head: str | None = None, remote_head: str | None = None) -> list[str]:
    """빌드 전에 멈춰야 할 이유들. 비면 진행.

    branch·head·remote_head 는 주면 본다(None = 안 봄) — 아래 공개 명령이 `--target master` 라, 다른 브랜치
    (3.1.1 worktree `v3.1.1`)나 아직 안 올린 커밋에서 만들면 첨부 파일과 태그가 서로 다른 코드가 된다(67 R12)."""
    out = []
    if branch is not None and branch != "master":
        out.append(f"지금 브랜치가 {branch} 다 — 공개는 --target master 라 master 에 merge·push 한 뒤 master 에서 돌린다")
    elif head is not None and head != remote_head:
        out.append("HEAD 가 origin/master 와 다르다 — 먼저 push 한다(태그가 GitHub 의 master 끝에 붙는다)")
    if hidden:
        out.append("숨긴 메뉴가 남아 있다(config.HIDDEN_NAV_UNTIL_READY) — 빈 페이지를 내보내지 않는다: "
                   + ", ".join(hidden))
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


def missing_licenses(names: list[str]) -> list[str]:
    """배포물 파일 이름들 중 라이선스 폴더에 파일이 하나도 없는 고지 항목.

    PySide6·Qt 가 LGPL v3 라 라이선스 사본을 같이 줘야 한다 — spec 이 넣지만, 패키지 이름이 바뀌어
    license_files() 가 못 찾으면 조용히 빠지므로 여기서 막는다. 글이 맞는지는 `license_text_problems`.
    """
    want = [n for n, _lic, _d in notice.THIRD_PARTY] + ["this-app"]
    have = {n.split(f"/{notice.LICENSE_DIR}/", 1)[1].split("/", 1)[0]
            for n in names if f"/{notice.LICENSE_DIR}/" in n}
    return [n for n in want if n not in have]


def license_text_problems(read) -> list[str]:
    """`notice.REPO_LICENSES` 항목의 라이선스 폴더에 LGPL·GPL 전문이 **글로** 들어 있나. read(경로 조각) → {이름: 바이트}.
    파일 이름만 보면 휠의 상용 안내문(LicenseRef-Qt-Commercial.txt)으로도 통과한다(73단계 F8)."""
    out = []
    for name, _lic, dist in notice.THIRD_PARTY:
        if dist not in notice.REPO_LICENSES:
            continue
        text = b"".join(read(f"/{notice.LICENSE_DIR}/{name}/").values()).decode("utf-8", "replace")
        lack = [m for m in notice.LGPL_MARKERS if m not in text]
        if lack:
            out.append(f"{name}: {lack}")
    return out


# 옛 바인딩 — 개발 PC 에 PyQt6 가 같이 깔린 동안(73단계 U3) PyInstaller 가 끌어오면 GPL 이 다시 섞인다
FORBIDDEN_PARTS = ("/_internal/pyqt6/", "/_internal/pyqt6_qt6", "/_internal/pyqt6_sip",
                   f"/{notice.LICENSE_DIR}/pyqt6")


def forbidden_files(names: list[str]) -> list[str]:
    """배포물 파일 이름들 중 옛 바인딩(PyQt6)의 것."""
    return [n for n in names if any(f in n.lower() for f in FORBIDDEN_PARTS)]


# 없으면 앱이 '대체 경로'로 돌아 겉으론 멀쩡한 모듈 — 빠져도 아무 오류가 안 난다(store._loads 의 orjson)
# "PySide6/QtNetwork.pyd" 처럼 파일로 적은 것은 그 파일 — 트레이의 '한 번만 실행'(QLocalServer)이 쓴다. 1.1.1 계획 때
# 배포판에 Qt6Network.dll 은 있는데 이 .pyd 가 없었다(소스·테스트에선 안 보이고 exe 에서만 import 가 죽는다).
REQUIRED_MODULES = ["orjson", "PySide6/QtNetwork.pyd"]


def bundle_pyz_names() -> list[str] | None:
    """빌드가 남긴 PYZ 의 모듈 이름들 — 없거나 못 읽으면 None."""
    pyz = ROOT / "build" / SPEC.stem / "PYZ-00.pyz"
    if not pyz.is_file():
        return None
    try:
        from PyInstaller.archive.readers import ZlibArchiveReader
        return list(ZlibArchiveReader(str(pyz)).toc)
    except Exception:
        return None


def server_modules_in_bundle(pyz_names: list[str]) -> list[str] | None:
    """번들 PYZ 목차의 중앙 서버 모듈들(docs/CENTRAL_SERVER.md 11절 — 서버 코드는 exe 에 안 들어간다).
    목차에 nexon_api 가 없으면 None — 목차를 못 읽은 것이라(빈 검사) 통과로 치지 않는다. 파일 목록만 보면 빈 검사다:
    모듈은 PYZ 안에 들어간다."""
    if "nexon_api" not in pyz_names:
        return None
    return [n for n in pyz_names if n == "server" or n.startswith("server.")]


def missing_modules(names: list[str]) -> list[str]:
    """배포물 파일 이름들에 그 모듈의 확장 모듈(.pyd)이 없는 것."""
    low = [n.lower() for n in names]

    def has(m: str) -> bool:
        m = m.lower()
        if m.endswith(".pyd"):
            return any(n.endswith(f"/_internal/{m}") for n in low)
        return any(f"/_internal/{m}/" in n and n.endswith(".pyd") for n in low)
    return [m for m in REQUIRED_MODULES if not has(m)]


def asset_names(version: str) -> tuple[str, str, str]:
    """(설치 파일, 포터블 zip, 체크섬) — 전부 ASCII 여야 GitHub 에서 이름이 안 깨진다."""
    return (f"{ASSET_PREFIX}-Setup-{version}.exe", f"{ASSET_PREFIX}-{version}-portable.zip",
            "SHA256SUMS.txt")


def sha256_lines(paths: list[Path]) -> str:
    """`sha256sum` 형식 — 받는 쪽에서 `certutil -hashfile <파일> SHA256` 로 대조한다."""
    import hashlib
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in paths)


def write_sums(path: Path, files: list[Path]) -> str:
    """LF 로 쓴다 — 윈도우 기본(CRLF)이면 `sha256sum -c` 가 파일 이름 끝의 \\r 때문에 못 연다
    (v0.2.0 에 처음 올린 SHA256SUMS.txt 가 그랬다)."""
    text = sha256_lines(files)
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


# 받기 전에 읽게 — 앱 첫 실행 안내(notice.py)와 같은 내용의 요약
DISCLAIMER = (
    "## 받기 전에\n\n"
    f"- {notice.UNOFFICIAL} 무료이며 있는 그대로 제공되고, 쓰다 생긴 손해는 책임지지 않습니다.\n"
    "- 넥슨 오픈API 키는 본인이 발급받아 넣습니다. 키 관리와 넥슨 오픈API 이용약관 준수는 본인의 몫입니다.\n"
    "- **넥슨 홈페이지 데이터(랭킹·구단가치·팀컬러·시즌 구분·선수 능력치)는 기본 꺼짐입니다.** 홈페이지를 자동으로"
    " 읽는 방식이라 넥슨이 공식으로 허락한 경로(오픈API)가 아닙니다. 첫 실행 때 켤지 고르며, 켜는 것은 사용자의"
    " 선택과 책임입니다. 끄면 그 칸이 비어 있습니다.\n"
    "- 만든 사람은 아무것도 수집하지 않습니다. 연결처는 넥슨(API·이미지·홈페이지)과 GitHub(업데이트 확인)뿐입니다.\n"
    "- 소스는 MIT 입니다. 화면 라이브러리 PySide6·Qt 6 는 LGPL v3 이고, 프로그램 폴더의 Qt 파일을 바꿔 끼울 수 있습니다."
    " 라이선스 전문은 프로그램 폴더 `_internal\\licenses` 에 있습니다.")


SEASON_END_FILE = ROOT / "docs" / "season_end.txt"


def season_notice_line(path: Path = SEASON_END_FILE) -> str:
    """마지막 시즌 종료일 공지(tools/season_notice.py 가 고치는 한 곳) — 새 릴리스 본문에 이어 붙인다.
    공지가 새 릴리스로 사라지면 앱이 그 시즌 동안만 마지막 저장값을 쓰므로, 시즌 중 릴리스는 이어 붙여야 한다."""
    import updatecheck
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("<!--") and updatecheck.parse_season_notice(ln):
            return ln
    return ""


def release_notes(changes: str, setup: str, portable: str, sums: str, notice: str = "") -> str:
    return (
        "## 받기\n\n"
        f"- **설치 파일(권장)**: `{setup}` 를 받아서 실행합니다. 관리자 권한은 필요 없습니다. 시작 메뉴에 생기고,"
        " 바탕화면 아이콘은 설치할 때 고릅니다. 지우려면 윈도우 \"설정 → 앱\"에서 지웁니다.\n"
        f"- **포터블**: 설치 없이 쓰려면 `{portable}` 을 새 폴더에 폴더째 풀고 `{APP_DIR_NAME}.exe` 를 실행합니다.\n"
        "- \"Windows의 PC 보호\" 창이 뜨면 **추가 정보 → 실행**을 누릅니다(서명 인증서가 없는 프로그램이라 처음 한 번 뜹니다).\n"
        "- 처음 켜면 API 키를 묻습니다. 넥슨 오픈API에서 애플리케이션을 **서비스 단계**로 등록해 받을 수 있습니다.\n"
        "- 전적 기록과 키는 `%LOCALAPPDATA%\\피파전적관리` 에 있어서 업데이트하거나 프로그램을 지워도 남습니다.\n\n"
        f"## 바뀐 점\n\n{changes}\n\n"
        f"{DISCLAIMER}\n\n"
        "## 파일 확인 (SHA256)\n\n"
        "받은 파일이 아래 값과 같은지 `certutil -hashfile <파일> SHA256` 으로 확인할 수 있습니다.\n\n"
        f"```\n{sums}```\n\n"
        f"{nexon_api.ATTRIBUTION}\n"
        + (f"\n{notice}\n" if notice else ""))


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
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace").stdout.strip()
    problems = preflight_problems(version, notes, dirty, config.HIDDEN_NAV_UNTIL_READY,
                                  branch=git("rev-parse", "--abbrev-ref", "HEAD"), head=git("rev-parse", "HEAD"),
                                  remote_head=git("rev-parse", "origin/master"))
    if problems:
        fail("\n       ".join(problems))
    ok(f"버전 {version} · CHANGELOG 절 있음 · 작업 트리 깨끗함 · master = origin/master")

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
    names = bundle_pyz_names()
    srv_mods = server_modules_in_bundle(names) if names is not None else None
    if srv_mods is None:
        fail("번들 PYZ 목차를 못 읽었다(build/…/PYZ-00.pyz) — 서버 코드가 안 들어갔는지 모른다")
    if srv_mods:
        fail(f"중앙 서버 모듈이 exe 에 들어갔다 {srv_mods[:5]} — 앱 쪽이 server 를 import 한다")
    ok("번들에 중앙 서버 모듈 없음")

    setup_name, zip_name, sums_name = asset_names(version)
    zip_path = DIST / zip_name
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:  # 한글 이름에 UTF-8 플래그가 붙는다
        for p in sorted(app_dir.rglob("*")):
            z.write(p, p.relative_to(DIST).as_posix())
    ok(f"zip {zip_path.name} ({zip_path.stat().st_size / 2**20:.1f} MB)")
    with zipfile.ZipFile(zip_path) as z:
        lic_missing = missing_licenses(z.namelist())
    if lic_missing:
        fail(f"라이선스 전문이 빠졌다 {lic_missing} — notice.THIRD_PARTY 의 배포 이름을 확인")
    ok(f"라이선스 전문 {len(notice.THIRD_PARTY) + 1}개 항목 전부 들어 있음")
    with zipfile.ZipFile(zip_path) as z:
        lic_bad = license_text_problems(
            lambda part: {n: z.read(n) for n in z.namelist() if part in n and not n.endswith("/")})
        old_binding = forbidden_files(z.namelist())
    if lic_bad:
        fail(f"LGPL·GPL 전문이 글로 안 들어 있다 {lic_bad} — notice.REPO_LICENSES 의 파일을 확인")
    ok("PySide6·shiboken6 라이선스 글 확인(LGPL v3 · GPL v3)")
    if old_binding:
        fail(f"옛 바인딩 PyQt6 가 배포물에 섞였다 {old_binding[:3]} — spec excludes 를 확인")
    ok("배포물에 PyQt6 없음")
    with zipfile.ZipFile(zip_path) as z:
        mods_missing = missing_modules(z.namelist())
    if mods_missing:
        fail(f"배포판에 빠진 모듈 {mods_missing} — 소스·테스트에선 안 보이고 exe 에서만 조용히 느려지거나 깨진다")
    ok(f"선택 모듈 {', '.join(REQUIRED_MODULES)} 들어 있음")

    setup = DIST / setup_name
    setup.unlink(missing_ok=True)
    run([str(iscc), "/Q", f"/DAppVersion={num}", str(ISS)], "설치 파일 컴파일")
    if not setup.is_file():
        fail(f"설치 파일이 안 생겼다: {setup.name} — .iss 의 OutputBaseFilename 과 ASSET_PREFIX 가 같은지")
    ok(f"설치 파일 {setup.name} ({setup.stat().st_size / 2**20:.1f} MB)")

    sums_path = DIST / sums_name
    sums = write_sums(sums_path, [setup, zip_path])
    ok(f"체크섬 {sums_name}")

    # 설치 파일은 LZMA 로 압축돼 안을 못 본다 — 그 입력(onedir 폴더)이 zip 과 같으므로 zip 으로 잰다.
    # 설치 파일 겉(헤더·문자열)과 설치 스크립트·릴리스 본문은 따로 잰다.
    notes_path = DIST / f"release-notes-{version}.md"
    notes_path.write_text(release_notes(notes, setup.name, zip_path.name, sums, season_notice_line()),
                          encoding="utf-8")
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
    assets = f'"{rel(setup)}" "{rel(zip_path)}" "{rel(sums_path)}"'
    print("\n공개는 확인 후 직접:")
    if exists:
        print(f"  gh release upload {version} {assets} --clobber")
        print(f'  gh release edit {version} --notes-file "{rel(notes_path)}"')
        print(f"  (옛 이름의 첨부가 남아 있으면: gh release delete-asset {version} <이름> -y)")
    else:
        print(f"  gh release create {version} {assets} "
              f'--target master --title "{version}" --notes-file "{rel(notes_path)}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
