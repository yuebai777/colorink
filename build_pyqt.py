import subprocess
import sys
import os
import shutil

def _strip_dist(dist_dir: str):
    """Remove unnecessary bloat from the built distribution folder."""
    removed = 0

    # 1. opengl32sw.dll — software OpenGL renderer (~20 MB), not needed on
    #    any system with GPU drivers (Windows 10+ requirement already).
    for root, dirs, files in os.walk(dist_dir):
        for f in files:
            if f.lower() == 'opengl32sw.dll':
                path = os.path.join(root, f)
                sz = os.path.getsize(path)
                os.remove(path)
                removed += sz
                print(f"  Stripped: {os.path.relpath(path, dist_dir)} "
                      f"({sz / (1024*1024):.1f} MB)")

    # 2. Qt6 translations — keep only Chinese + English (~0.3 MB of ~5.8 MB).
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

def build():
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

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
