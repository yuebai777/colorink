"""DirectComposition 直通灰度覆盖层 — 单进程 C++ EXE (WGC 捕获 + 零拷贝 GPU 呈现)。

与 ShaderGlass 同架构：Windows.Graphics.Capture 零拷贝捕获、Present(0, ALLOW_TEARING)
异步永不阻塞、60fps 恒定呈现节拍、窗口置顶点击穿透（WS_EX_LAYERED + HTTRANSPARENT）、
WDA_EXCLUDEFROMCAPTURE 防反馈。

（注：曾尝试直接挂 ShaderGlass 引擎 + 自制 slang 着色器，实测其 D3D 渲染在
本机虚拟 GPU 上无法合成显示——透明玻璃/实心玻璃/克隆模式均失败，故回退到
本原生后端，它是本机唯一实测可用的渲染路径。）

控制文件：%SYSTEMROOT%\\Temp\\dcomp_overlay_mode.txt   (0=off, 1=OKLCh, 2=Luma)
"""
import os
import subprocess
import time

_CTRL_FILE = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp", "dcomp_overlay_mode.txt")
MODE_DISABLED, MODE_OKLCH, MODE_LUMA = 0, 1, 2
_MODE_MAP = {"disabled": MODE_DISABLED, "oklch": MODE_OKLCH, "luma": MODE_LUMA}


class DCompOverlayController:
    def __init__(self):
        self._exe = self._find_exe()
        self._proc = None
        self._active = False
        self._mode = "oklch"
        # 供主窗口在 toggle() 失败时弹提示（见 main_window.py grayscaleFilterKey）
        self.last_error = ""

    @staticmethod
    def _find_exe():
        import sys
        if getattr(sys, 'frozen', False):
            base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "dcomp_overlay", "build", "dcomp_overlay.exe")
        return os.path.abspath(path) if os.path.exists(path) else None

    @property
    def is_available(self):
        return self._exe is not None

    @property
    def is_active(self):
        return self._active

    def _write_mode(self, mode: int):
        try:
            os.makedirs(os.path.dirname(_CTRL_FILE), exist_ok=True)
            with open(_CTRL_FILE, "w") as f:
                f.write(str(mode))
        except Exception:
            pass

    def set_mode(self, mode: str):
        if mode not in _MODE_MAP:
            raise ValueError(mode)
        if mode != "disabled":
            self._mode = mode
        if self._active or mode == "disabled":
            self._write_mode(_MODE_MAP[mode])

    def set_active(self, active: bool, mode: str | None = None):
        if mode is None:
            mode = self._mode
        if active:
            if self._proc is None or self._proc.poll() is not None:
                self.start()
            self._active = True
            self.set_mode(mode)
        else:
            self._active = False
            self.set_mode("disabled")

    def toggle(self):
        """切换滤镜。失败时返回 False 并填充 last_error（与主窗口契约一致）。"""
        try:
            self.set_active(not self._active, self._mode)
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = f"DComp 直通切换失败: {e}"
            return False

    def start(self):
        if self._proc and self._proc.poll() is None:
            return
        if not self._exe:
            self.last_error = "dcomp_overlay.exe 未找到（请先运行 dcomp_overlay\\build.bat 构建）"
            raise FileNotFoundError("dcomp_overlay.exe not found")
        self._write_mode(MODE_DISABLED)
        self._proc = subprocess.Popen(
            [self._exe],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.5)  # 单进程 WGC 初始化约 0.3-0.5s

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

    def close(self):
        self.stop()

    def set_target(self, t):
        pass

    @property
    def target(self):
        return "all"

    @staticmethod
    def available_screens():
        return ["all"]
