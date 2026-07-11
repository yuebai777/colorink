# -*- mode: python ; coding: utf-8 -*-

import os

spec_root = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    ['main.py'],
    pathex=['core'],
    binaries=[],
    datas=[
        ('icons', 'icons'),
        ('dcomp_overlay/build/dcomp_overlay.exe', 'dcomp_overlay/build'),
        ('sc_overlay/build/sc_overlay.exe', 'sc_overlay/build'),
    ],
    hiddenimports=[
        'dxcam',
        'dxcam.core',
        'dxcam.core.backend',
        'dxcam.core.capture_loop',
        'dxcam.core.capture_runtime',
        'dxcam.core.device',
        'dxcam.core.display_recovery',
        'dxcam.core.duplicator',
        'dxcam.core.dxgi_duplicator',
        'dxcam.core.dxgi_errors',
        'dxcam.core.output',
        'dxcam.core.output_recovery',
        'dxcam.core.stagesurf',
        'dxcam.core.winrt_duplicator',
        'dxcam.processor',
        'dxcam.processor.base',
        'dxcam.processor.cv2_processor',
        'dxcam.processor.numpy_processor',
        'dxcam.util',
        'dxcam.util.io',
        'dxcam.util.timer',
        'dxcam._libs',
        'dxcam._libs.d3d11',
        'dxcam._libs.dxgi',
        'dxcam._libs.user32',
        'comtypes',
        'comtypes.client',
        'PyQt6.QtOpenGL',
        'PyQt6.QtOpenGLWidgets',
        'numpy',
    ],
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
    name='Colorink Debug',
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
    icon=['icons\\icon.ico'],
    version='file_version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Colorink Debug',
)
