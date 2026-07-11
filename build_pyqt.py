import subprocess
import sys
import os
import shutil

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
