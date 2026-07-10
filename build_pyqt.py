import subprocess
import sys
import os

def build():
    # Install pyinstaller if not already installed
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        
    cmd = [
        "pyinstaller",
        "--onefile",
        "--windowed",
        "--paths", "core",
        "--add-data", "icons;icons",
        "--icon", "icons/icon.ico",
        "--version-file", "file_version_info.txt",
        "--name", "Colorink",
        "main.py"
    ]
    
    print("Running PyInstaller...")
    try:
        subprocess.run(cmd, check=True)
        print("Build complete! Output file in dist/Colorink.exe")
    except subprocess.CalledProcessError as e:
        print("Build failed:", e)
        sys.exit(1)

if __name__ == "__main__":
    build()
