# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


# PyInstaller injects SPECPATH as the directory containing this spec.
ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "backend" / "builtin_model_seeds.json"), "backend"),
]

hiddenimports = [
    "server_app",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.mysql.pymysql",
    "pymysql",
    "multipart",
]


a = Analysis(
    [str(ROOT / "server_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "clr_loader",
        "httpx",
        "openpyxl",
        "pytest",
        "pythonnet",
        "pkg_resources",
        "setuptools",
        "sqlalchemy.dialects.mssql",
        "sqlalchemy.dialects.oracle",
        "sqlalchemy.dialects.postgresql",
        "sqlalchemy.dialects.sqlite",
        "tkinter",
        "webview",
    ],
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
    name="FlightAnalyzerServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
