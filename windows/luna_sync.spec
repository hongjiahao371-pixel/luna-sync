# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent
ASSETS = ROOT / 'windows' / 'build_assets'
hidden_imports = ['pywifi._wifiutil_win'] + collect_submodules('comtypes')

a = Analysis(
    [str(ROOT / 'windows' / 'launcher.py')],
    pathex=[str(ROOT / 'app'), str(ROOT)],
    binaries=[(str(ASSETS / 'ffmpeg.exe'), '.')],
    datas=[(str(ROOT / 'app' / 'templates'), 'templates')],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LunaSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / 'luna-sync.ico'),
    version=str(ROOT / 'windows' / 'version_info.txt'),
)
