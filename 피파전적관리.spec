# -*- mode: python ; coding: utf-8 -*-
import sys

sys.path.insert(0, SPECPATH)
import notice  # noqa: E402

# 오픈소스 라이선스 전문 — 배포물 _internal/licenses/<이름>/ 에. 목록은 notice.THIRD_PARTY 한 곳.
# PyQt6 가 GPL v3 라 받는 사람에게 라이선스 사본을 줘야 한다(tools/release.py 가 들어갔는지 확인한다).
LICENSE_DATAS = [(str(p), f"{notice.LICENSE_DIR}/{name}") for name, p in notice.license_files()]
LICENSE_DATAS.append(('LICENSE', f"{notice.LICENSE_DIR}/this-app"))

a = Analysis(
    ['app_main.py'],
    pathex=[],
    binaries=[],
    datas=[('app_icon.ico', '.')] + LICENSE_DATAS,
    hiddenimports=['store', 'stats', 'core_api', 'theme', 'widgets', 'ranker', 'images',
                   'analysis', 'dashboard', 'charts', 'crashlog', 'updatecheck', 'notice',
                   # 1.1.1 — 트레이·자동 실행·랭킹 수집. QtNetwork 는 '한 번만 실행'(QLocalServer) — release.py 가 .pyd 를 확인한다
                   'tray', 'autostart', 'rankcollect', 'PyQt6.QtNetwork'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tzdata — 앱은 시간대 자료를 안 쓴다(zoneinfo 호출 0). 개발 PC 에 pandas 와 같이 깔려 있어
    # 딸려 들어왔다: 파일 627개·0.5MB. 쓰게 되면 여기서 빼고 notice.THIRD_PARTY 에 라이선스를 넣는다.
    excludes=['tzdata'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# onedir — 결과는 dist/피파전적관리/ 폴더(exe + _internal). 배포는 이 폴더를 zip 으로.
# onefile 은 켤 때마다 임시 폴더에 전부 풀어 시작이 느리고, 부트로더·앱 두 프로세스라
# 창 닫은 뒤 좀비가 남은 적이 있다. UPX 는 백신 오탐을 늘려 끈다(이 PC 엔 설치돼 있지도 않았다).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='피파전적관리',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='피파전적관리',
)
