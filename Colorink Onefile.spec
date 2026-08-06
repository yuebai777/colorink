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


def _find_site_file(package, filename):
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        path = os.path.join(sp, package, filename)
        if os.path.exists(path):
            return path
    return None

_binaries = []
for _zbar_name in ('libzbar-64.dll', 'libiconv.dll'):
    _zbar_path = _find_site_file('pyzbar', _zbar_name)
    if _zbar_path:
        _binaries.append((_zbar_path, 'pyzbar'))
        print(f"  -> Including pyzbar runtime DLL: {_zbar_path}")
    else:
        print(f"  WARNING: {_zbar_name} not found — QR scanning may be unavailable")

_datas = []
_add_if_exists('icons/icon.ico', 'icons', _datas, 'app icon')
_add_if_exists('icons/checkbox_check.png', 'icons', _datas, 'checkbox check icon')
_add_if_exists('icons/arrow_down_dark.png', 'icons', _datas, 'arrow down dark icon')
_add_if_exists('icons/arrow_down_light.png', 'icons', _datas, 'arrow down light icon')
_add_if_exists('icons/arrow_down_accent.png', 'icons', _datas, 'arrow down accent icon')
_add_if_exists('core/picker_hook.dll', 'core', _datas, 'picker hook DLL')
_add_if_exists('mag_overlay/build/mag_filter.exe', 'mag_overlay/build', _datas, 'Mag filter')
_add_if_exists('native_grayscale/runtime/grayscale_overlay.pyc', 'native_grayscale/runtime', _datas, 'Native grayscale base runtime')

a = Analysis(
    ['main.py'],
    pathex=['core'],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=[
        'dxcam',
        'dxcam.core',
        'dxcam.core.device',
        'dxcam.core.output',
        'dxcam.core.capture',
        'comtypes',
        'comtypes.client',
        # CSP Companion QR scanning uses delayed imports.
        'mss',
        'mss.base',
        'mss.screenshot',
        'mss.windows',
        'pyzbar',
        'pyzbar.pyzbar',
        'PIL',
        'PIL.Image',
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
