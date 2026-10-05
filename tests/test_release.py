"""tools/release.py 의 검사 로직 — 개인정보 검사가 압축 안까지 보는지, 대조가 비면 실패하는지.

빌드·네트워크 없이 돈다. `python tests/test_release.py`
"""
from __future__ import annotations

import io
import shutil
import tempfile
import os
import sys
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import release  # noqa: E402

SECRET = "C:\\Users\\테스트사람\\Desktop"
NEEDLES = {"홈": release._variants(SECRET)}
CONTROLS = {"대조": b"Data based on NEXON Open API"}


def _fake_exe(payload: bytes) -> bytes:
    # PyInstaller 처럼 앞뒤에 다른 바이트가 있고 가운데 zlib 덩어리가 있다
    return b"MZ\x90\x00" + os.urandom(64) + zlib.compress(payload) + os.urandom(64)


def test_finds_secret_only_inside_compressed_blob():
    exe = _fake_exe(b"co_filename=" + SECRET.encode("utf-8") + b" " + CONTROLS["대조"])
    assert SECRET.encode("utf-8") not in exe  # 날 바이트로는 안 보인다 — 풀어야 보인다
    leaks, seen = release.scan([("앱.exe", exe)], NEEDLES, CONTROLS)
    assert leaks == {"홈": {"앱.exe"}}, leaks
    assert seen == {"대조": 1}, seen


def test_finds_other_encodings_and_slashes():
    for enc_text in (SECRET.encode("utf-16-le"), SECRET.replace("\\", "/").encode("utf-8"),
                     SECRET.encode("cp949")):
        leaks, _ = release.scan([("a.txt", b"x" + enc_text + b"y")], NEEDLES, CONTROLS)
        assert "홈" in leaks, enc_text


def test_clean_build_has_no_leak_but_controls_must_show():
    exe = _fake_exe(b"just code " + CONTROLS["대조"])
    leaks, seen = release.scan([("앱.exe", exe)], NEEDLES, CONTROLS)
    assert not leaks and seen["대조"] == 1, (leaks, seen)
    # 대조가 없으면 0건이어도 믿을 수 없다 — 검사가 압축을 못 풀면 이렇게 된다
    plain = b"MZ" + os.urandom(200)
    leaks, seen = release.scan([("앱.exe", plain)], NEEDLES, CONTROLS)
    assert not leaks and seen["대조"] == 0, seen


def test_zip_members_are_scanned():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("피파/_internal/a.pyz", _fake_exe(SECRET.encode("utf-8")))
    # 실행마다 다른 폴더 — 고정 이름(TEMP/release_test.zip)이면 동시에 돈 테스트끼리 서로 지우고 잠갔다
    # (2026-10-02 변이 전수 측정을 여러 사본에서 동시에 돌리다 7조각이 시작부터 실패)
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "release_test.zip")
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    try:
        leaks, _ = release.scan(release.zip_files(release.Path(path)), NEEDLES, CONTROLS)
        assert leaks == {"홈": {"피파/_internal/a.pyz"}}, leaks
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_private_needles_come_from_this_pc():
    n = release.private_needles()
    home = str(release.Path.home()).encode("utf-8")
    assert any(home == v for v in n["홈 폴더 경로"]), n.keys()
    assert all(v for vs in n.values() for v in vs), "빈 바이트열이면 모든 파일에 걸린다"


def test_preflight_stops_on_missing_notes_or_dirty_tree():
    assert release.preflight_problems("v1.0.0", "변경 내용", "") == []
    p = release.preflight_problems("v1.0.0", "", "")
    assert len(p) == 1 and "CHANGELOG" in p[0], p
    p = release.preflight_problems("v1.0.0", "변경 내용", " M app_main.py")
    assert len(p) == 1 and "커밋" in p[0], p


def test_scan_verdict_distrusts_zero_without_controls():
    assert release.scan_verdict({}, {"대조": 2}) is None
    assert "대조" in release.scan_verdict({}, {"대조": 0})  # 0건이어도 대조가 없으면 멈춘다
    assert "개인정보" in release.scan_verdict({"홈": {"a.exe"}}, {"대조": 1})
    # 둘 다면 대조부터 — 검사가 못 보는 상태의 '적중'도 믿을 수 없다
    assert "대조" in release.scan_verdict({"홈": {"a.exe"}}, {"대조": 0})


def test_private_needles_include_key_and_email_when_present():
    orig_key, orig_run = release.config.API_KEY, release.subprocess.run

    class _R:
        stdout = "someone@example.invalid\n"

    try:
        release.config.API_KEY = "live_가짜키"
        release.subprocess.run = lambda *a, **k: _R()
        n = release.private_needles()
        assert n["API 키"] == ["live_가짜키".encode()], n.get("API 키")
        assert n["git 이메일"] == [b"someone@example.invalid"], n.get("git 이메일")
        release.config.API_KEY = ""
        _R.stdout = ""
        n = release.private_needles()
        assert "API 키" not in n and "git 이메일" not in n, n.keys()  # 빈 값은 모든 파일에 걸린다
    finally:
        release.config.API_KEY, release.subprocess.run = orig_key, orig_run


def test_asset_names_are_ascii():
    # GitHub 는 첨부 이름의 한글을 지운다 — v0.2.0 zip 이 '-v0.2.0.zip' 으로 올라갔다
    for name in release.asset_names("v1.2.3"):
        assert name.isascii() and "v1.2.3" in name or name == "SHA256SUMS.txt", name
    iss = release.ISS.read_text(encoding="utf-8-sig")
    setup = release.asset_names("v{#AppVersion}")[0]
    assert f"OutputBaseFilename={setup[:-4]}" in iss, "설치 파일 이름이 .iss 와 어긋난다"


def test_updater_and_release_agree_on_names():
    # 앱 안 업데이트는 릴리스 첨부를 이름으로 찾는다 — 어긋나면 버튼이 '받으러 가기'로만 남는다
    import updatecheck
    setup, _, sums = release.asset_names("v1.2.3")
    assert updatecheck.SETUP_ASSET.format(tag="v1.2.3") == setup, (updatecheck.SETUP_ASSET, setup)
    assert updatecheck.SUMS_ASSET == sums
    # 설치 파일이 /AUTOUPDATE=1 을 받아 끝나고 앱을 다시 켠다
    iss = release.ISS.read_text(encoding="utf-8-sig")
    assert "/AUTOUPDATE=1" in updatecheck.INSTALL_ARGS and "{param:AUTOUPDATE|0}" in iss
    assert "Check: IsAutoUpdate" in iss, "자동 업데이트 뒤 앱을 다시 켜는 줄이 없다"


def test_sha256_lines_and_notes():
    import hashlib
    tmp = release.Path(tempfile.mkdtemp())  # 고정 이름이면 동시에 돈 테스트끼리 부딪친다(위 zip 테스트와 같은 이유)
    path, sums = tmp / "release_sum_test.bin", tmp / "release_sum_test.txt"
    path.write_bytes(b"abc")
    try:
        line = release.write_sums(sums, [path])
        assert line == f"{hashlib.sha256(b'abc').hexdigest()}  release_sum_test.bin\n", line
        # CRLF 면 sha256sum -c 가 'release_sum_test.bin\r' 를 찾다 실패한다(v0.2.0 첫 업로드)
        assert b"\r" not in sums.read_bytes(), sums.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    notes = release.release_notes("- 바뀐 것", "S.exe", "P.zip", line)
    # 설치 파일이 포터블보다 먼저, 체크섬과 출처 표기가 본문에
    assert notes.index("S.exe") < notes.index("P.zip"), notes
    assert line.strip() in notes and "Data based on NEXON Open API" in notes and "- 바뀐 것" in notes
    # 받기 전에 읽어야 할 고지 — 비공식·웹 데이터 기본 꺼짐·GPL
    assert release.DISCLAIMER in notes and "기본 꺼짐" in notes and "GPL v3" in notes, notes


def test_every_third_party_has_license_file():
    # 배포 이름이 바뀌어 못 찾으면 spec 이 조용히 빼고 넘어간다 — 여기서 빨개져야 한다
    found = {name for name, p in release.notice.license_files() if p.is_file()}
    want = [n for n, _lic, _d in release.notice.THIRD_PARTY]
    assert found == set(want), sorted(set(want) - found)


def test_missing_licenses_reads_zip_names():
    d = f"피파전적관리/_internal/{release.notice.LICENSE_DIR}"
    every = [f"{d}/{n}/LICENSE" for n, _l, _d in release.notice.THIRD_PARTY] + [f"{d}/this-app/LICENSE"]
    assert release.missing_licenses(every) == []
    assert release.missing_licenses(every[1:]) == [release.notice.THIRD_PARTY[0][0]]
    assert release.missing_licenses([]) != [], "빈 배포물을 통과시켰다"


def test_missing_modules_needs_the_pyd():
    base = "피파전적관리/_internal"
    net = f"{base}/PyQt6/QtNetwork.pyd"
    assert release.missing_modules([f"{base}/orjson/orjson.cp314-win_amd64.pyd", net]) == []
    assert release.missing_modules([f"{base}/orjson/__init__.pyi", net]) == ["orjson"], "pyd 없이 통과했다"
    # 1.1.1 계획 때 실측: Qt6Network.dll 은 있는데 QtNetwork.pyd 가 없었다 — dll 로 통과하면 안 된다
    only_dll = [f"{base}/orjson/orjson.cp314-win_amd64.pyd", f"{base}/PyQt6/Qt6/bin/Qt6Network.dll",
                f"{base}/PyQt6/QtCore.pyd"]
    assert release.missing_modules(only_dll) == ["PyQt6/QtNetwork.pyd"], release.missing_modules(only_dll)
    assert release.missing_modules([]) == ["orjson", "PyQt6/QtNetwork.pyd"]


def test_installer_matches_app_constants():
    # 알림 앱 이름(AUMID)이 어긋나면 알림이 exe 이름으로 뜨거나 안 뜬다 · 제거기가 다른 이름을 지우면 부팅마다 '없는 exe'
    import autostart
    iss = release.ISS.read_text(encoding="utf-8-sig")
    assert f'#define AppUserModelID "{release.config.APP_USER_MODEL_ID}"' in iss
    assert iss.count('AppUserModelID: "{#AppUserModelID}"') == 2, "바로가기 둘 다에 AppUserModelID 가 있어야"
    assert f'#define RunValue "{autostart.VALUE_INSTALLED}"' in iss
    assert "RegDeleteValue(HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\Run', '{#RunValue}')" in iss
    assert autostart.RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert "usUninstall" in iss and autostart.VALUE_PORTABLE not in iss, "제거기는 설치판 값만 지운다"


def test_installer_shows_app_name():
    iss = release.ISS.read_text(encoding="utf-8-sig")
    assert f'#define AppTitle "{release.config.APP_NAME}"' in iss, "설치 파일 표시 이름이 config.APP_NAME 과 다르다"
    # 옛 이름 바로가기를 안 지우면 업데이트 뒤 시작 메뉴에 둘이 남는다
    assert '#define OldTitle "피파 전적관리"' in iss and iss.count("{#OldTitle}.lnk") == 2, iss


def test_changelog_section():
    sec = release.changelog_section("v0.2.0")
    assert "서비스 단계" in sec and "## " not in sec, sec[:200]
    assert release.changelog_section("v9.9.9") == ""


# ── main() 의 멈춤 장치 — 검사에 걸리면 정말 멈추나(변이 5순위, 2026-10-02) ────────────────
class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def _run_main(tmp, *, dirty="", iscc=True, drop=None, make_setup=True, leak=False, gh_exists=False):
    """가짜 빌드 결과(dist/피파전적관리)로 release.main 을 돌린다 → (종료 코드, 출력)."""
    import contextlib
    root, dist = tmp / "root", tmp / "root" / "dist"
    app = dist / release.APP_DIR_NAME
    internal = app / "_internal"
    (internal / "orjson").mkdir(parents=True)
    (app / f"{release.APP_DIR_NAME}.exe").write_bytes(b"exe")
    (internal / "orjson" / "orjson.cp314-win_amd64.pyd").write_bytes(b"pyd")
    (internal / "PyQt6").mkdir()
    (internal / "PyQt6" / "QtNetwork.pyd").write_bytes(b"pyd")
    for n in [n for n, _l, _d in release.notice.THIRD_PARTY] + ["this-app"]:
        (internal / release.notice.LICENSE_DIR / n).mkdir(parents=True)
        (internal / release.notice.LICENSE_DIR / n / "LICENSE").write_text("license", encoding="utf-8")
    controls = b"".join(release.CONTROLS.values()) + (SECRET.encode("utf-8") if leak else b"")
    (internal / "base.bin").write_bytes(controls)
    if drop:
        p = internal / drop
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    iss = root / "setup.iss"
    iss.write_text("; iss", encoding="utf-8")

    def fake_run(cmd, what):
        if make_setup and "설치" in what:
            (dist / release.asset_names(release.config.APP_VERSION)[0]).write_bytes(b"setup")

    def fake_sub(cmd, **kw):
        if cmd[:2] == ["git", "status"]:
            return _FakeProc(stdout=dirty)
        if cmd[:3] == ["gh", "release", "view"]:
            return _FakeProc(returncode=0 if gh_exists else 1)
        return _FakeProc()

    saved = (release.ROOT, release.DIST, release.ISS, release.find_iscc, release.run,
             release.subprocess.run, release.changelog_section, release.private_needles, sys.argv)
    release.ROOT, release.DIST, release.ISS = root, dist, iss
    release.find_iscc = lambda: (Path("iscc.exe") if iscc else None)
    release.run, release.subprocess.run = fake_run, fake_sub
    release.changelog_section = lambda v: "- 바뀐 것"
    release.private_needles = lambda: NEEDLES
    sys.argv = ["release.py", "--skip-build"]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = release.main()
    except SystemExit as e:
        code = e.code
    finally:
        (release.ROOT, release.DIST, release.ISS, release.find_iscc, release.run,
         release.subprocess.run, release.changelog_section, release.private_needles, sys.argv) = saved
    return code, out.getvalue()


def test_release_main_stops_on_every_gate():
    from pathlib import Path as _P
    cases = [  # (설명, 인자, 멈춰야 하나, 출력에 있어야 할 말)
        ("정상", {}, False, "gh release create"),
        ("이미 있는 릴리스", {"gh_exists": True}, False, "gh release upload"),
        ("커밋 안 한 변경", {"dirty": " M app_main.py"}, True, "커밋 안 한 변경"),
        ("Inno Setup 없음", {"iscc": False}, True, "ISCC"),
        ("라이선스 빠짐", {"drop": f"{release.notice.LICENSE_DIR}/PyQt6"}, True, "라이선스 전문이 빠졌다"),
        ("orjson 빠짐", {"drop": "orjson"}, True, "빠진 모듈"),
        ("QtNetwork 빠짐", {"drop": "PyQt6/QtNetwork.pyd"}, True, "빠진 모듈"),
        ("설치 파일 안 생김", {"make_setup": False}, True, "설치 파일이 안 생겼다"),
        ("개인정보 섞임", {"leak": True}, True, "개인정보가 들어 있다"),
    ]
    for name, kw, should_stop, needle in cases:
        tmp = _P(tempfile.mkdtemp())
        try:
            code, out = _run_main(tmp, **kw)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        assert (code != 0) == should_stop, (name, code, out[-300:])
        assert needle in out, (name, out[-300:])


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
        except Exception as e:
            failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
