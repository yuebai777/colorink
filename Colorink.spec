# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import site

spec_root = os.path.dirname(os.path.abspath(SPECPATH))

# Find dxcam C extension .pyd (loaded dynamically via import_module)
def _find_dxcam_pyd():
    for sp in site.getsitepackages():
        pyd = os.path.join(sp, 'dxcam', 'processor', '_numpy_kernels.cp314-win_amd64.pyd')
        if os.path.exists(pyd):
            return pyd
    # Fallback: search user site-packages
    usp = site.getusersitepackages()
    pyd = os.path.join(usp, 'dxcam', 'processor', '_numpy_kernels.cp314-win_amd64.pyd')
    if os.path.exists(pyd):
        return pyd
    return None

_dxcam_pyd = _find_dxcam_pyd()
_binaries = []
if _dxcam_pyd:
    _binaries.append((_dxcam_pyd, 'dxcam/processor'))
    print(f"  -> Including dxcam C extension: {_dxcam_pyd}")
else:
    print("  WARNING: _numpy_kernels.pyd not found — dxcam may run slower")

a = Analysis(
    ['main.py'],
    pathex=['core'],
    binaries=_binaries,
    datas=[
        ('icons', 'icons'),
        # Include C++ overlay EXEs for DComp / ScreenCapture fallback backends
        ('dcomp_overlay/build/dcomp_overlay.exe', 'dcomp_overlay/build'),
        ('sc_overlay/build/sc_overlay.exe', 'sc_overlay/build'),
        ('core/picker_hook.dll', 'core'),
    ],
    hiddenimports=[
        # dxcam submodules — may not be auto-detected
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
        # comtypes (needed by dxcam for WinRT)
        'comtypes',
        'comtypes.client',
        # cv2 (opencv) — imported dynamically by dxcam via import_module()
        'cv2',
        # Ensure PyQt OpenGL modules are included
        'PyQt6.QtOpenGL',
        'PyQt6.QtOpenGLWidgets',
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
    name='Colorink',
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
    version='file_version_info.txt',
    icon=['icons\\icon.ico'],
)

# onedir — collects all files into dist/Colorink/ folder
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Colorink',
)
