# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files


datas = [('frontend/dist', 'frontend/dist'), ('backend', 'backend')]
datas += collect_data_files('fastapi')
datas += collect_data_files('uvicorn')

hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.lifespan.on',
    'tkinter',
    'tkinter.filedialog',
    'webview',
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='FlightAnalyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
