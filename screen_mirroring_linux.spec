# -*- mode: python ; coding: utf-8 -*-
# Linux PyInstaller spec for Screen Mirroring.
# Run on a Linux machine with:
#   pip install pyinstaller
#   pyinstaller screen_mirroring_linux.spec

import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

datas = [
    ('resources', 'resources'),
]

hiddenimports = [
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PIL',
    'psutil',
    'psutil._common',
    'psutil._linux',
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
    excludes=[
        'matplotlib',
        'numpy',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='screen_mirroring',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=None,
    manifest=None,
)
