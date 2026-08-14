import os
import shutil
import subprocess
import sys


def _strip_dist(dist_dir: str):
    """Remove unnecessary bloat from the built distribution folder."""
    removed = 0

    unused_names = {
        'opengl32sw.dll',
        'qt6pdf.dll',
        'qt6network.dll',
        'qt6svg.dll',
        'qpdf.dll',
        'qsvg.dll',
        'qsvgicon.dll',
        'qjpeg.dll',
        'qwebp.dll',
        'qtiff.dll',
        'qicns.dll',
        'qgif.dll',
        'qtga.dll',
        'qwbmp.dll',
        'qtuiotouchplugin.dll',
    }
    unused_path_parts = (
        os.sep + 'numpy' + os.sep + 'random' + os.sep,
        os.sep + 'numpy' + os.sep + 'fft' + os.sep,
        os.sep + 'numpy' + os.sep + '_core' + os.sep + '_multiarray_tests.',
    )
    unused_pil_prefixes = (
        '_avif.',
        '_webp.',
        '_imagingcms.',
        '_imagingmath.',
        '_imagingtk.',
    )

    # 1. Drop unused Qt DLLs/plugins, Pillow codecs and numpy submodules.
    for root, dirs, files in os.walk(dist_dir):
        for f in files:
            path = os.path.join(root, f)
            full = path.lower()
            if (
                f.lower() in unused_names
                or any(part.lower() in full for part in unused_path_parts)
                or f.lower().startswith(unused_pil_prefixes)
            ):
                sz = os.path.getsize(path)
                os.remove(path)
                removed += sz
                print(f"  Stripped: {os.path.relpath(path, dist_dir)} "
                      f"({sz / (1024*1024):.1f} MB)")

    # 2. Pure-Python packages pulled in by hooks but never used by the app.
    for dirname in ('yaml', 'charset_normalizer'):
        dir_path = os.path.join(dist_dir, '_internal', dirname)
        if os.path.isdir(dir_path):
            for dp, _, files in os.walk(dir_path):
                for f in files:
                    removed += os.path.getsize(os.path.join(dp, f))
            shutil.rmtree(dir_path)
            print(f"  Stripped: _internal/{dirname}/")

    # 3. Qt6 translations — keep only Chinese + English (~0.3 MB of ~5.8 MB).
    trans_dir = None
    for root, dirs, files in os.walk(dist_dir):
        if os.path.basename(root) == 'translations' and 'Qt6' in root:
            trans_dir = root
            break
    if trans_dir:
        trans_removed = 0
        keep_patterns = ('qt_zh_CN.qm', 'qt_zh_TW.qm', 'qt_en.qm',
                         'qtbase_zh_CN.qm', 'qtbase_zh_TW.qm', 'qtbase_en.qm',
                         'qt_help_zh_CN.qm', 'qt_help_zh_TW.qm', 'qt_help_en.qm')
        for f in os.listdir(trans_dir):
            if f.endswith('.qm') and f not in keep_patterns:
                path = os.path.join(trans_dir, f)
                sz = os.path.getsize(path)
                os.remove(path)
                trans_removed += sz
                removed += sz
        kept = len(os.listdir(trans_dir))
        print(f"  Stripped translations: kept {kept} .qm files "
              f"({trans_removed / (1024*1024):.1f} MB)")

    if removed > 0:
        print(f"  Total stripped: {removed / (1024*1024):.1f} MB")
    else:
        print("  Nothing to strip.")


def run_pyinstaller(spec_file, label):
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath", os.path.join("dist", label),
        "--workpath", os.path.join("build", label),
        spec_file,
    ]
    print(f"\n{'='*60}")
    print(f"  Building: {label}  ({spec_file})")
    print(f"{'='*60}")
    try:
        subprocess.run(cmd, check=True)
        print(f"  [OK] {label} — build succeeded")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [FAIL] {label} — {e}")
        return False

def _verify_version_consistency():
    """Fail fast before packaging when APP_VERSION has drifted from the
    Windows file version or the release notes heading."""
    from core import updater
    v = updater.APP_VERSION
    info = ""
    notes = ""
    try:
        with open("file_version_info.txt", "r", encoding="utf-8") as f:
            info = f.read()
    except OSError:
        pass
    try:
        with open("release_notes.md", "r", encoding="utf-8") as f:
            notes = f.read()
    except OSError:
        pass
    expected = f"StringStruct('FileVersion', '{v}.0')"
    if expected not in info:
        raise SystemExit(
            f"Version drift: file_version_info.txt missing {expected!r}. "
            f"Bump it to match core.updater.APP_VERSION ({v})."
        )
    if not notes.startswith(f"## v{v}\n"):
        raise SystemExit(
            f"Version drift: release_notes.md must start with '## v{v}'."
        )
    print(f"  Version consistency OK: v{v}")


def build():
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    _verify_version_consistency()

    # Clean
    for d in ["build", "dist"]:
        if os.path.isdir(d):
            print(f"Cleaning {d}/ ...")
            shutil.rmtree(d)

    results = {}
    results["onedir"]  = run_pyinstaller("Colorink.spec", "Onedir")
    if results["onedir"]:
        onedir_path = os.path.join("dist", "Onedir", "Colorink")
        if os.path.isdir(onedir_path):
            _strip_dist(onedir_path)
    results["onefile"] = run_pyinstaller("Colorink Onefile.spec", "Onefile")

    print(f"\n{'='*60}")
    print("  Build Summary")
    print(f"{'='*60}")
    for name, ok in results.items():
        status = "OK" if ok else "FAILED"
        dist_dir = os.path.join("dist", name)
        if ok:
            if name == "Onefile":
                exe = os.path.join(dist_dir, "Colorink.exe")
                if os.path.exists(exe):
                    sz = os.path.getsize(exe) / (1024*1024)
                    print(f"  [{status}] {name}: {exe} ({sz:.1f} MB)")
            else:
                total = sum(
                    os.path.getsize(os.path.join(dp, f))
                    for dp, _, files in os.walk(dist_dir)
                    for f in files
                ) / (1024*1024)
                print(f"  [{status}] {name}: {dist_dir}\\ ({total:.1f} MB)")
        else:
            print(f"  [{status}] {name}")

if __name__ == "__main__":
    build()
