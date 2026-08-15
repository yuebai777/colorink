"""MagSetFullscreenColorEffect 灰度后端 — C++ EXE via control file.

与 NativeGrayscaleController 接口兼容。

Mag 后端在 DWM 合成级应用 5x5 颜色矩阵（与 Windows 自带颜色滤镜同路径）：
- 零捕获、零覆盖窗口、无额外 GPU 开销，与 Win 自带滤镜一样流畅
- 不修改显示器 ICC profile，避免颜色管理路径导致的灰度偏色
- 鼠标光标不受矩阵影响（和 Windows 放大镜 API 一致）

仅提供 BT.709 Luma 灰度（线性矩阵无法表达 OKLCh 的立方根非线性，
编码空间线性近似的感知收益有限，故不提供 OKLCh 模式）。
"""
import os
import subprocess
from typing import Optional

_CTRL_FILE = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp", "mag_filter_mode.txt")
MODE_DISABLED, MODE_ENABLED = 0, 1


class MagFilterController:
    def __init__(self, mode: str = "oklch"):
        self._exe = self._find_exe()
        self._proc = None
        self._active = False
        self._mode = self._normalize_mode(mode)
        self.last_error = ""

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

    def _write_mode(self, mode: int) -> bool:
        """Write the control file; returns False (and records last_error)
        when the write fails — a silent swallow would leave is_active
        claiming the filter is on while the screen is unchanged."""
        try:
            os.makedirs(os.path.dirname(_CTRL_FILE), exist_ok=True)
            with open(_CTRL_FILE, "w") as f:
                f.write(str(mode))
            return True
        except Exception as exc:
            self.last_error = f"写入 Mag 控制文件失败: {exc}"
            return False

    def set_mode(self, mode: str):
        self._mode = self._normalize_mode(mode)
        if self._active:
            if not self._write_mode(MODE_ENABLED):
                # 写失败：回滚激活状态，避免状态与真实滤镜不一致
                self._active = False

    def set_active(self, active: bool, mode: str | None = None):
        if mode is not None:
            self._mode = self._normalize_mode(mode)
        if active:
            if self._proc is None or self._proc.poll() is not None:
                self.start()
            if not self._write_mode(MODE_ENABLED):
                self._active = False
                return False
            self._active = True
            return True
        else:
            self._active = False
            return self._write_mode(MODE_DISABLED)

    def toggle(self):
        # 返回 set_active 结果：热键路径（hotkey_mixin）检查返回值
        # 为 False 时显示 last_error，让写控制文件失败可见。
        return self.set_active(not self._active, self._mode)

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
        # 不再 GUI 线程 sleep(0.3)：exe 每 200ms 轮询控制文件，
        # 启动后最多 ~200ms 内读到 set_active 写入的最新状态。

    def stop(self):
        self._write_mode(MODE_DISABLED)
        self._active = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
                self._proc = None
            except Exception:
                # 终止失败：保留引用。下次 start() 会先检查 poll()——
                # 进程若已退出则正常重启新实例，避免双实例同时轮询。
                pass

    def close(self): self.stop()

    def set_target(self, t): pass
    @property
    def target(self): return "all"
    @staticmethod
    def available_screens(): return ["all"]
