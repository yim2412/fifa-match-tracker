# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app_main.py'],
    pathex=[],
    binaries=[],
    datas=[('app_icon.ico', '.')],
    hiddenimports=['store', 'stats', 'theme', 'widgets', 'ranker', 'images',
                   'analysis', 'dashboard', 'charts', 'crashlog'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
