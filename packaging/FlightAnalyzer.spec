# -*- mode: python ; coding: utf-8 -*-
import os
from configparser import ConfigParser
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# PyInstaller injects SPECPATH = absolute directory holding this spec (packaging/).
# Resolve every path from it so the spec works regardless of the invocation CWD.
ROOT = os.path.dirname(SPECPATH)

# The runtime config stays at the project root on purpose; the spec only embeds
# the desktop runtime settings (local/dev), never server credentials.
source_config = Path(ROOT) / 'flight_analyzer.ini'
packaged_config = Path(SPECPATH) / 'build' / 'flight_analyzer.ini'
parser = ConfigParser(interpolation=None)
parser.read(source_config, encoding='utf-8')
packaged_parser = ConfigParser(interpolation=None)
for section in ('local', 'dev'):
    if parser.has_section(section):
        packaged_parser[section] = dict(parser[section])
packaged_config.parent.mkdir(parents=True, exist_ok=True)
with packaged_config.open('w', encoding='utf-8') as f:
    packaged_parser.write(f)

datas = [
    (str(Path(ROOT) / 'frontend' / 'dist'), 'frontend/dist'),
    (str(Path(ROOT) / 'backend'), 'backend'),
    # Include only the desktop runtime settings, never server credentials.
    # A flight_analyzer.ini next to the EXE still takes precedence.
    (str(packaged_config), '.'),
]
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
    [str(Path(ROOT) / 'main.py')],
    pathex=[ROOT],
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
