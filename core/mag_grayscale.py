"""MagSetFullscreenColorEffect 灰度后端 — C++ EXE via control file.

与 DCompOverlayController / RustFilterController 接口兼容。

Mag 后端在 DWM 合成级应用 5x5 颜色矩阵（与 Windows 自带颜色滤镜同路径）：
- 零捕获、零覆盖窗口、无额外 GPU 开销，与 Win 自带滤镜一样流畅
- 不修改显示器 ICC profile，避免颜色管理路径导致的灰度偏色
- 鼠标光标不受矩阵影响（和 Windows 放大镜 API 一致）

仅提供 BT.709 Luma 灰度（线性矩阵无法表达 OKLCh 的立方根非线性，
编码空间线性近似的感知收益有限，故不提供 OKLCh 模式）。
"""
import os
import subprocess
import time
from typing import Optional

_CTRL_FILE = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp", "mag_filter_mode.txt")
MODE_DISABLED, MODE_ENABLED = 0, 1


class MagFilterController:
    def __init__(self, mode: str = "oklch"):
        self._exe = self._find_exe()
        self._proc = None
        self._active = False
        self._mode = self._normalize_mode(mode)

    @staticmethod
    def _find_exe():
        import sys
        if getattr(sys, 'frozen', False):
            base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "mag_overlay", "build", "mag_filter.exe")
        return os.path.abspath(path) if os.path.exists(path) else None

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        """Mag 后端仅提供 BT.709 Luma 灰度。"""
        return "luma"

    @property
    def is_available(self): return self._exe is not None
    @property
    def is_active(self): return self._active

    def _write_mode(self, mode: int):
        try:
            os.makedirs(os.path.dirname(_CTRL_FILE), exist_ok=True)
            with open(_CTRL_FILE, "w") as f:
                f.write(str(mode))
        except Exception:
            pass

    def set_mode(self, mode: str):
        self._mode = self._normalize_mode(mode)
        if self._active:
            self._write_mode(MODE_ENABLED)

    def set_active(self, active: bool, mode: str | None = None):
        if mode is not None:
            self._mode = self._normalize_mode(mode)
        if active:
            if self._proc is None or self._proc.poll() is not None:
                self.start()
            self._active = True
            self._write_mode(MODE_ENABLED)
        else:
            self._active = False
            self._write_mode(MODE_DISABLED)

    def toggle(self):
        self.set_active(not self._active, self._mode)

    def start(self):
        if self._proc and self._proc.poll() is None:
            return
        if not self._exe:
            raise FileNotFoundError("mag_filter.exe not found")
        self._write_mode(MODE_DISABLED)
        self._proc = subprocess.Popen(
            [self._exe],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.3)

    def stop(self):
        self._write_mode(MODE_DISABLED)
        self._active = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None

    def close(self): self.stop()

    def set_target(self, t): pass
    @property
    def target(self): return "all"
    @staticmethod
    def available_screens(): return ["all"]
