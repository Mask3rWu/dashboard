# -*- mode: python ; coding: utf-8 -*-
from configparser import ConfigParser
from pathlib import Path


# PyInstaller injects SPECPATH as the directory containing this spec.
ROOT = Path(SPECPATH).parent

# Embed only the server runtime settings from the release configuration. The
# desktop build separately filters its copy and never receives MySQL secrets.
source_config = ROOT / "flight_analyzer.ini"
packaged_config = Path(SPECPATH) / "build" / "server" / "flight_analyzer.ini"
parser = ConfigParser(interpolation=None)
if not parser.read(source_config, encoding="utf-8"):
    raise SystemExit(f"Release config was not found: {source_config}")
packaged_parser = ConfigParser(interpolation=None)
for section in ("server", "mysql", "dev"):
    if parser.has_section(section):
        packaged_parser[section] = dict(parser[section])
for required_section in ("server", "mysql"):
    if not packaged_parser.has_section(required_section):
        raise SystemExit(f"Release config is missing [{required_section}]: {source_config}")
mysql_password = packaged_parser.get("mysql", "password", fallback="").strip()
if not mysql_password or mysql_password == "CHANGE_ME":
    raise SystemExit(f"Release config has no deployable [mysql].password: {source_config}")
packaged_config.parent.mkdir(parents=True, exist_ok=True)
with packaged_config.open("w", encoding="utf-8") as f:
    packaged_parser.write(f)

datas = [
    (str(ROOT / "backend" / "builtin_model_seeds.json"), "backend"),
    (str(packaged_config), "."),
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
