"""DirectComposition 显存直通 OKLCh 灰度后端控制器.

基于 D3D11 + DXGI Desktop Duplication + DirectComposition 实现零拷贝、零延迟全屏灰度。
通过 ctypes 直接加载 native_dcomp/dcomp_filter.dll，与主程序同进程运行，无需外部子进程。
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


class DCompGrayscaleController:
    """基于 DirectComposition 显存直通的零延迟 OKLCh 灰度滤镜控制器."""

    def __init__(self, mode: str = "oklch"):
        self._dll = None
        self._dll_path = self._find_dll()
        self._mode = mode if mode in ("oklch", "luma") else "oklch"
        self._target = "all"
        self._target_index = -1
        self._region: tuple[int, int, int, int] | None = None
        self.last_error = ""

        if self._dll_path:
            try:
                self._load_dll(self._dll_path)
            except Exception as exc:
                self.last_error = f"DComp DLL 加载失败: {exc}"
        else:
            self.last_error = "未找到 dcomp_filter.dll"

    @staticmethod
    def _find_dll() -> str | None:
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        candidates = [
            Path(base) / "native_dcomp" / "dcomp_filter.dll",
            Path(base) / "native_dcomp" / "target" / "release" / "dcomp_filter.dll",
            Path(base) / "dcomp_filter.dll",
        ]
        for p in candidates:
            if p.exists():
                return str(p.resolve())
        return None

    def _load_dll(self, path: str) -> None:
        dll = ctypes.CDLL(path)

        # 声明导出函数签名
        dll.dcomp_filter_init.argtypes = []
        dll.dcomp_filter_init.restype = ctypes.c_bool

        dll.dcomp_filter_set_active.argtypes = [ctypes.c_bool]
        dll.dcomp_filter_set_active.restype = ctypes.c_bool

        dll.dcomp_filter_set_mode.argtypes = [ctypes.c_int]
        dll.dcomp_filter_set_mode.restype = None

        dll.dcomp_filter_set_target.argtypes = [ctypes.c_int]
        dll.dcomp_filter_set_target.restype = ctypes.c_bool

        dll.dcomp_filter_is_active.argtypes = []
        dll.dcomp_filter_is_active.restype = ctypes.c_bool

        dll.dcomp_filter_is_healthy.argtypes = []
        dll.dcomp_filter_is_healthy.restype = ctypes.c_bool

        dll.dcomp_filter_get_screen_count.argtypes = []
        dll.dcomp_filter_get_screen_count.restype = ctypes.c_int

        dll.dcomp_filter_cleanup.argtypes = []
        dll.dcomp_filter_cleanup.restype = None

        if hasattr(dll, "dcomp_filter_get_frame_count"):
            dll.dcomp_filter_get_frame_count.argtypes = []
            dll.dcomp_filter_get_frame_count.restype = ctypes.c_uint64

        if hasattr(dll, "dcomp_filter_get_stats"):
            dll.dcomp_filter_get_stats.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
            dll.dcomp_filter_get_stats.restype = None

        if hasattr(dll, "dcomp_filter_set_region"):
            dll.dcomp_filter_set_region.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ]
            dll.dcomp_filter_set_region.restype = ctypes.c_bool

        self._dll = dll

    @property
    def is_available(self) -> bool:
        return self._dll is not None

    @property
    def is_active(self) -> bool:
        if not self._dll:
            return False
        try:
            return bool(self._dll.dcomp_filter_is_active())
        except Exception:
            return False

    @property
    def is_healthy(self) -> bool:
        if not self._dll:
            return False
        try:
            return bool(self._dll.dcomp_filter_is_healthy())
        except Exception:
            return False

    def prepare(self) -> None:
        """后台预热：初始化 D3D11 和 DirectComposition 设备与着色器."""
        if not self._dll:
            return
        try:
            ok = self._dll.dcomp_filter_init()
            if not ok:
                self.last_error = "DComp 渲染设备初始化失败"
        except Exception as exc:
            self.last_error = f"DComp 预热异常: {exc}"

    def set_mode(self, mode: str) -> None:
        if mode not in ("oklch", "luma"):
            raise ValueError("DComp 后端仅支持 oklch 或 luma")
        self._mode = mode
        if not self._dll:
            return
        try:
            mode_code = 1 if mode == "luma" else 0
            self._dll.dcomp_filter_set_mode(mode_code)
        except Exception as exc:
            self.last_error = f"设置滤镜模式失败: {exc}"

    def set_target(self, target: str | int) -> None:
        target_str = str(target or "all").strip()
        if target_str != "all" and ":" in target_str:
            target_str = target_str.split(":", 1)[0].strip()
        self._target = target_str

        idx = -1
        if target_str != "all":
            try:
                idx = int(target_str)
            except ValueError:
                idx = -1
        self._target_index = idx

        if not self._dll:
            return
        try:
            self._dll.dcomp_filter_set_target(idx)
        except Exception as exc:
            self.last_error = f"切换目标屏幕失败: {exc}"

    @property
    def target(self) -> str:
        return self._target

    @property
    def region(self) -> tuple[int, int, int, int] | None:
        return self._region

    def set_region(self, region: tuple[int, int, int, int] | list[int] | dict | None) -> bool:
        """设置灰度窗口区域 (x, y, width, height)，传 None 恢复最大化铺满."""
        parsed: tuple[int, int, int, int] | None = None
        if isinstance(region, dict):
            try:
                rx = int(region.get("x", 0))
                ry = int(region.get("y", 0))
                rw = int(region.get("width", region.get("w", 0)))
                rh = int(region.get("height", region.get("h", 0)))
                if rw > 0 and rh > 0:
                    parsed = (rx, ry, rw, rh)
            except (TypeError, ValueError):
                parsed = None
        elif isinstance(region, (tuple, list)) and len(region) == 4:
            try:
                rx, ry, rw, rh = (int(v) for v in region)
                if rw > 0 and rh > 0:
                    parsed = (rx, ry, rw, rh)
            except (TypeError, ValueError):
                parsed = None
        self._region = parsed
        return self._sync_region()

    def clear_region(self) -> bool:
        """清除自定义区域，恢复默认最大化无边框窗口."""
        return self.set_region(None)

    def _sync_region(self) -> bool:
        if not self._dll or not hasattr(self._dll, "dcomp_filter_set_region"):
            return False
        try:
            if self._region is not None:
                rx, ry, rw, rh = self._region
                return bool(self._dll.dcomp_filter_set_region(rx, ry, rw, rh))
            return bool(self._dll.dcomp_filter_set_region(0, 0, 0, 0))
        except Exception as exc:
            self.last_error = f"设置灰度区域失败: {exc}"
            return False

    @staticmethod
    def available_screens() -> list[str]:
        screens = ["all"]
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QGuiApplication
            app = QApplication.instance()
            if isinstance(app, QGuiApplication):
                for i, screen in enumerate(app.screens()):
                    geo = screen.geometry()
                    dpr = screen.devicePixelRatio()
                    name = screen.name().replace("\\\\.\\", "")
                    pw = int(geo.width() * dpr)
                    ph = int(geo.height() * dpr)
                    screens.append(f"{i}: {name} ({pw}x{ph})")
                return screens
        except Exception:
            pass
        return screens

    def set_active(self, active: bool, mode: str | None = None) -> bool:
        if mode is not None:
            self.set_mode(mode)

        if not self._dll:
            self.last_error = "DComp 后端不可用"
            return False

        try:
            if active:
                # 激活前确保同步当前模式、屏幕和区域设置
                mode_code = 1 if self._mode == "luma" else 0
                self._dll.dcomp_filter_set_mode(mode_code)
                self._dll.dcomp_filter_set_target(self._target_index)
                self._sync_region()

                initial_stats = self.get_stats()
                initial_presents = initial_stats.get("present_frames", 0)
                initial_timeouts = initial_stats.get("capture_timeouts", 0)

                ok = bool(self._dll.dcomp_filter_set_active(True))
                if not ok:
                    self.last_error = "激活 DComp 灰度滤镜失败"
                    return False
                self._dll.dcomp_filter_set_target(self._target_index)
                self._dll.dcomp_filter_set_mode(mode_code)
                self._sync_region()

                # 快速验证后台抓取是否健康：等待确认本次激活捕获状态
                import time
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.15:
                    if not self.is_healthy:
                        self.last_error = "DComp 屏幕捕获失败（可能是 HDR 屏幕或图形驱动限制）"
                        self.set_active(False)
                        return False
                    stats = self.get_stats()
                    if (stats.get("present_frames", 0) > initial_presents
                            or stats.get("capture_timeouts", 0) > initial_timeouts):
                        break
                    time.sleep(0.015)

                if not self.is_healthy:
                    self.last_error = "DComp 屏幕捕获异常，已停止滤镜"
                    self.set_active(False)
                    return False

                return True
            else:
                self._dll.dcomp_filter_set_active(False)
                return True
        except Exception as exc:
            self.last_error = f"控制 DComp 灰度失败: {exc}"
            return False

    def toggle(self) -> bool:
        return self.set_active(not self.is_active, self._mode)

    def stop(self) -> None:
        self.set_active(False)

    def close(self) -> None:
        self.stop()
        if self._dll:
            try:
                self._dll.dcomp_filter_cleanup()
            except Exception:
                pass
            self._dll = None

    def get_stats(self) -> dict[str, int]:
        """获取底层实时渲染与捕获帧数统计."""
        if not self._dll or not hasattr(self._dll, "dcomp_filter_get_stats"):
            return {
                "capture_frames": 0,
                "capture_timeouts": 0,
                "capture_with_tex": 0,
                "capture_no_tex": 0,
                "present_frames": 0,
                "present_errors": 0,
            }
        buf = (ctypes.c_uint64 * 6)()
        self._dll.dcomp_filter_get_stats(buf)
        return {
            "capture_frames": buf[0],
            "capture_timeouts": buf[1],
            "capture_with_tex": buf[2],
            "capture_no_tex": buf[3],
            "present_frames": buf[4],
            "present_errors": buf[5],
        }
