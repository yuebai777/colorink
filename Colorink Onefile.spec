# -*- mode: python ; coding: utf-8 -*-
# Onefile spec — single EXE, no external folder needed
import os
import sys
import site

spec_root = os.path.dirname(os.path.abspath(SPECPATH))

# Helper: only add data file if it exists (avoid build failures for unbuilt overlays)
def _add_if_exists(path_rel, dest_dir, datas_list, label=""):
    # SPECPATH resolution is unreliable in PyInstaller subprocess —
    # use cwd (project root via build_pyqt.py) as fallback.
    for base in (spec_root, os.getcwd()):
        full = os.path.join(base, path_rel)
        if os.path.exists(full):
            datas_list.append((path_rel, dest_dir))
            if label:
                print(f"  -> Including {label}: {full}")
            return
    print(f"  WARNING: {path_rel} not found — skipping{' (' + label + ')' if label else ''}")

def _find_dxcam_pyd():
    for sp in site.getsitepackages():
        pyd = os.path.join(sp, 'dxcam', 'processor', '_numpy_kernels.cp314-win_amd64.pyd')
        if os.path.exists(pyd):
            return pyd
    usp = site.getusersitepackages()
    pyd = os.path.join(usp, 'dxcam', 'processor', '_numpy_kernels.cp314-win_amd64.pyd')
    if os.path.exists(pyd):
        return pyd
    return None

_dxcam_pyd = _find_dxcam_pyd()
_binaries = []
if _dxcam_pyd:
    _binaries.append((_dxcam_pyd, 'dxcam/processor'))

# Build data files list (with existence checks for optional overlay EXEs)
_datas = []
_add_if_exists('icons/icon.ico', 'icons', _datas, 'app icon')
_add_if_exists('dcomp_overlay/build/dcomp_overlay.exe', 'dcomp_overlay/build', _datas, 'DComp overlay')
_add_if_exists('sc_overlay/build/sc_overlay.exe', 'sc_overlay/build', _datas, 'SC overlay')
_add_if_exists('core/picker_hook.dll', 'core', _datas, 'picker hook DLL')
_add_if_exists('screen_filter.exe', '.', _datas, 'Rust filter EXE')

a = Analysis(
    ['main.py'],
    pathex=['core'],
    binaries=_binaries,
    datas=_datas,
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'Pythonwin',
        'pywin.debugger',
        'PyQt6.QtPdf',
        'PyQt6.QtNetwork',
        'PyQt6.QtSvg',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Filter out unnecessary pywin32 binaries (Pythonwin IDE, debugger)
a.binaries = [b for b in a.binaries if 'Pythonwin' not in b[0] and 'pythonwin' not in b[0].lower()]
a.datas = [d for d in a.datas if 'Pythonwin' not in d[0] and 'pythonwin' not in d[0].lower()]

# Filter out opengl32sw.dll — software OpenGL renderer (~20 MB)
a.binaries = [b for b in a.binaries if 'opengl32sw.dll' not in b[0].lower()]

# Keep only zh_CN, zh_TW, en Qt6 translations
KEEP_TRANS = ('qt_zh_CN.qm', 'qt_zh_TW.qm', 'qt_en.qm',
              'qtbase_zh_CN.qm', 'qtbase_zh_TW.qm', 'qtbase_en.qm',
              'qt_help_zh_CN.qm', 'qt_help_zh_TW.qm', 'qt_help_en.qm')
a.datas = [d for d in a.datas
           if not (d[0].endswith('.qm') and 'translations' in d[0] and os.path.basename(d[0]) not in KEEP_TRANS)]

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
