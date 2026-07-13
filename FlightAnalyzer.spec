# -*- mode: python ; coding: utf-8 -*-
from configparser import ConfigParser
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


source_config = Path('flight_analyzer.ini')
packaged_config = Path('build') / 'flight_analyzer.ini'
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
    ('frontend/dist', 'frontend/dist'),
    ('backend', 'backend'),
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
