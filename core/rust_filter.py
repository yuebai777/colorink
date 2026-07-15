"""screen_filter Rust EXE 控制器 — 通过 stdin 管道通信.

与 DCompOverlayController 接口兼容，可无缝替换。
"""
import os
import subprocess
import time
from pathlib import Path


class RustFilterController:
    """控制 screen_filter.exe（基于 screen_filter-main 改装）。

    Rust 端通过 stdin 接收指令：
        enable   — 显示滤镜
        disable  — 隐藏滤镜
        oklab    — 切换到 OkLab 感知灰度
        lab      — 切换到 CIE Lab 感知灰度
        quit     — 退出进程
    """

    def __init__(self, mode: str = "oklch"):
        self._exe = self._find_exe()
        self._proc = None
        self._active = False
        self._mode = self._normalize_mode(mode)
        self._target = "all"

    # ── EXE 查找 ──────────────────────────────────────────

    @staticmethod
    def _find_exe() -> str | None:
        """按优先级查找 screen_filter.exe。"""
        import sys

        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        candidates = [
            Path(base) / "screen_filter.exe",
        ]
        for p in candidates:
            if p.exists():
                return str(p.resolve())
        return None

    # ── 模式名映射 ────────────────────────────────────────

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        """Rust 后端仅支持 OkLab 感知灰度。"""
        return "oklab"

    # ── 属性 ──────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return self._exe is not None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def target(self) -> str:
        return "all"

    @staticmethod
    def available_screens() -> list:
        return ["all"]

    # ── 进程管理 ──────────────────────────────────────────

    def _start(self):
        """启动 Rust EXE（如未运行）。"""
        if self._proc and self._proc.poll() is None:
            return
        if not self._exe:
            raise FileNotFoundError("screen_filter.exe 未找到")

        args = [self._exe, self._mode]
        if self._target != "all":
            try:
                idx = int(self._target)
                args.append(str(idx))
            except ValueError:
                pass

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.5)

    def _send(self, cmd: str):
        """向 Rust 进程 stdin 写入一行指令。"""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write(f"{cmd}\n".encode())
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    # ── 公共接口（与 DCompOverlayController / GrayscaleOverlay 兼容）──

    def set_active(self, active: bool, mode: str | None = None):
        if mode is not None:
            self._mode = self._normalize_mode(mode)

        if active:
            self._start()
            self._active = True
            self._send(self._mode)  # 先设模式
            self._send("enable")
        else:
            self._send("disable")
            self._active = False

    def toggle(self):
        self.set_active(not self._active, self._mode)

    def set_mode(self, mode: str):
        self._mode = self._normalize_mode(mode)
        if self._active:
            self._send(self._mode)

    def set_target(self, target):
        """切换目标屏幕。发送 screen:N 指令，Rust 端自动重建窗口。"""
        self._target = target
        if target != "all":
            try:
                idx = int(target)
                self._send(f"screen:{idx}")
            except ValueError:
                pass
        else:
            self._send("screen:-1")

    def stop(self):
        self.set_active(False)

    def close(self):
        self._send("quit")
        if self._proc:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
            self._proc = None
