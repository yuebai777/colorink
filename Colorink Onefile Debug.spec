# -*- mode: python ; coding: utf-8 -*-
# Debug onefile — console enabled to see errors
import os, sys, site

a = Analysis(
    ['main.py'],
    pathex=['core'],
    binaries=[('C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python314\\Lib\\site-packages\\dxcam\\processor\\_numpy_kernels.cp314-win_amd64.pyd', 'dxcam/processor')],
    datas=[
        ('icons', 'icons'),
        ('dcomp_overlay/build/dcomp_overlay.exe', 'dcomp_overlay/build'),
        ('sc_overlay/build/sc_overlay.exe', 'sc_overlay/build'),
    ],
    hiddenimports=[
        'dxcam', 'dxcam.core', 'dxcam.core.backend', 'dxcam.core.capture_loop',
        'dxcam.core.capture_runtime', 'dxcam.core.device', 'dxcam.core.display_recovery',
        'dxcam.core.duplicator', 'dxcam.core.dxgi_duplicator', 'dxcam.core.dxgi_errors',
        'dxcam.core.output', 'dxcam.core.output_recovery', 'dxcam.core.stagesurf',
        'dxcam.core.winrt_duplicator',
        'dxcam.processor', 'dxcam.processor.base', 'dxcam.processor.cv2_processor',
        'dxcam.processor.numpy_processor',
        'dxcam.util', 'dxcam.util.io', 'dxcam.util.timer',
        'dxcam._libs', 'dxcam._libs.d3d11', 'dxcam._libs.dxgi', 'dxcam._libs.user32',
        'comtypes', 'comtypes.client',
        'cv2',
        'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
    name='Colorink Debug', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=True, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    version='file_version_info.txt', icon=['icons\\icon.ico'],
)
