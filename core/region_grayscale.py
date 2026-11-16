"""窗口/区域灰度滤镜控制器.

呈现为默认最大化（无边框、不显示任务栏图标、鼠标穿透、置顶）的灰度滤镜窗口，
并支持框选自定义灰度区域或随时重置回最大化铺满。
优先使用 DirectComposition/D3D11 (dcomp_filter.dll) 的硬件区域加速能力（支持 OKLCh 与 Luma），
并在 DComp 不可用或用户指定 Windows Magnifier 后端时使用独立高刷线程驱动的窗口化 WC_MAGNIFIER。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import threading
import time

from core.dcomp_grayscale import DCompGrayscaleController, _parse_region_rect


class _MAGCOLOREFFECT(ctypes.Structure):
    _fields_ = [("transform", ctypes.c_float * 25)]


_GRAYSCALE_MATRIX = _MAGCOLOREFFECT(
    (ctypes.c_float * 25)(
        0.2126, 0.2126, 0.2126, 0.0, 0.0,
        0.7152, 0.7152, 0.7152, 0.0, 0.0,
        0.0722, 0.0722, 0.0722, 0.0, 0.0,
        0.0,    0.0,    0.0,    1.0, 0.0,
        0.0,    0.0,    0.0,    0.0, 1.0,
    )
)

_WS_POPUP = 0x80000000
_WS_CLIPCHILDREN = 0x02000000
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000

_WS_EX_TOPMOST = 0x00000008
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000

_LWA_ALPHA = 0x00000002
_HWND_TOPMOST = -1
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SW_HIDE = 0
_PM_REMOVE = 0x0001
_MW_FILTERMODE_EXCLUDE = 0


def normalize_region_tuple(
    region: tuple[int, int, int, int] | list[int] | dict | None,
) -> tuple[int, int, int, int] | None:
    """将区域配置标准化为 (x, y, width, height) 物理像素元组，无效或空值返回 None."""
    if region is None:
        return None
    return _parse_region_rect(region)


def region_to_config_dict(
    region: tuple[int, int, int, int] | list[int] | dict | None,
) -> dict[str, int] | None:
    """将区域转换为可写入 JSON 配置的字典格式."""
    norm = normalize_region_tuple(region)
    if norm is None:
        return None
    return {
        "x": norm[0],
        "y": norm[1],
        "width": norm[2],
        "height": norm[3],
    }


def normalize_region_entry(
    entry: tuple[int, int, int, int] | list[int] | dict | None,
) -> dict | None:
    """将区域配置条目标准化为包含 enabled/name 字段的字典，无效返回 None."""
    rect = normalize_region_tuple(entry)
    if rect is None:
        return None
    enabled = True
    name = ""
    if isinstance(entry, dict):
        if "enabled" in entry:
            enabled = bool(entry.get("enabled", True))
        raw_name = entry.get("name", "")
        if isinstance(raw_name, str):
            name = raw_name.strip()
    out: dict = {
        "x": rect[0],
        "y": rect[1],
        "width": rect[2],
        "height": rect[3],
        "enabled": enabled,
    }
    if name:
        out["name"] = name
    return out


def normalize_region_entries(regions) -> list[dict]:
    """标准化区域列表（兼容单区域 dict/4-tuple、区域列表或 None）."""
    if not regions:
        return []
    if isinstance(regions, dict):
        entry = normalize_region_entry(regions)
        return [entry] if entry is not None else []
    if (
        isinstance(regions, (tuple, list))
        and len(regions) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in regions)
    ):
        entry = normalize_region_entry(regions)
        return [entry] if entry is not None else []
    if not isinstance(regions, (list, tuple)):
        return []
    out: list[dict] = []
    for item in regions:
        entry = normalize_region_entry(item)
        if entry is not None:
            out.append(entry)
    return out


def active_region_tuples(regions) -> list[tuple[int, int, int, int]]:
    """提取已启用的物理像素矩形元组列表 [(x, y, w, h), ...]."""
    entries = normalize_region_entries(regions)
    return [
        (e["x"], e["y"], e["width"], e["height"])
        for e in entries
        if e.get("enabled", True)
    ]


def read_region_entries_from_config(cfg: dict | None) -> list[dict]:
    """从配置字典读取标准化区域条目列表（自动兼容旧版单区域字段）."""
    if not isinstance(cfg, dict):
        return []
    raw_list = cfg.get("grayscaleFilterRegions")
    if isinstance(raw_list, list) and len(raw_list) > 0:
        entries = normalize_region_entries(raw_list)
        if entries:
            return entries
    legacy = normalize_region_entry(cfg.get("grayscaleFilterRegion"))
    return [legacy] if legacy is not None else []


def write_regions_to_config(cfg: dict, entries) -> list[dict]:
    """将区域列表写回配置字典，并同步维护兼容性字段 grayscaleFilterRegion."""
    normalized = normalize_region_entries(entries)
    cfg["grayscaleFilterRegions"] = normalized
    first_active = next((e for e in normalized if e.get("enabled", True)), None)
    rep = first_active or (normalized[0] if normalized else None)
    cfg["grayscaleFilterRegion"] = region_to_config_dict(rep)
    return normalized


def format_region_status_from_entries(entries: list[dict], tr_fn=None) -> str:
    """格式化区域范围状态文案供 UI 与控制器使用."""
    _tr = tr_fn if callable(tr_fn) else (lambda s, **kw: s.format(**kw) if kw else s)
    if not entries:
        return _tr("默认最大化")
    active = [e for e in entries if e.get("enabled", True)]
    if len(entries) == 1 and len(active) == 1:
        e = active[0]
        return f"{e['width']}×{e['height']} ({e['x']}, {e['y']})"
    if not active:
        return _tr(
            "共 {total} 个区域（已全部停用，暂按默认最大化）",
            total=len(entries),
        )
    return _tr(
        "共 {total} 个区域（已启用 {active} 个）",
        total=len(entries),
        active=len(active),
    )


class WindowedMagGrayscale:
    """基于 Win32 WC_MAGNIFIER 窗口控件的高帧率无边框穿透区域灰度后端.

    在独立原生工作线程中创建宿主窗口与 WC_MAGNIFIER 控件并泵送消息：
    - 使用 MagSetWindowFilterList(MW_FILTERMODE_EXCLUDE) 排除自身宿主窗口，
      避免使用 WDA_EXCLUDEFROMCAPTURE 导致 Windows 11 DWM 屏蔽放大镜图层渲染；
    - 脱离 Python Qt 主线程定时器，直接跟随 DWM 合成器刷新率（最高可达 240Hz）。
    """

    def __init__(self) -> None:
        self._user32 = None
        self._kernel32 = None
        self._mag_dll = None
        self._is_active = False
        self._running = False
        self._target = "all"
        self._target_index = -1
        self._region: tuple[int, int, int, int] | None = None
        self._regions: list[tuple[int, int, int, int]] = []
        self.last_error = ""

        self._cond = threading.Condition()
        self._ready_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._init_ok = False

        self._load_apis()

    def _load_apis(self) -> None:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            mag = ctypes.WinDLL("Magnification.dll", use_last_error=True)

            mag.MagInitialize.argtypes = []
            mag.MagInitialize.restype = wintypes.BOOL

            mag.MagUninitialize.argtypes = []
            mag.MagUninitialize.restype = wintypes.BOOL

            mag.MagSetColorEffect.argtypes = [wintypes.HWND, ctypes.POINTER(_MAGCOLOREFFECT)]
            mag.MagSetColorEffect.restype = wintypes.BOOL

            mag.MagSetWindowSource.argtypes = [wintypes.HWND, wintypes.RECT]
            mag.MagSetWindowSource.restype = wintypes.BOOL

            mag.MagSetWindowFilterList.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.POINTER(wintypes.HWND),
            ]
            mag.MagSetWindowFilterList.restype = wintypes.BOOL

            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND

            user32.SetLayeredWindowAttributes.argtypes = [
                wintypes.HWND,
                wintypes.COLORREF,
                wintypes.BYTE,
                wintypes.DWORD,
            ]
            user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL

            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL

            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.DestroyWindow.restype = wintypes.BOOL

            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.PeekMessageW.restype = wintypes.BOOL

            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL

            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = wintypes.LPARAM

            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            self._user32 = user32
            self._kernel32 = kernel32
            self._mag_dll = mag
        except Exception as exc:
            self.last_error = f"加载窗口化 Magnifier API 失败: {exc}"

    @property
    def is_available(self) -> bool:
        return self._mag_dll is not None and self._user32 is not None

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def is_healthy(self) -> bool:
        return self.is_available

    @property
    def region(self) -> tuple[int, int, int, int] | None:
        regions = getattr(self, "_regions", None)
        if regions:
            return regions[0]
        return self._region

    @property
    def regions(self) -> list[tuple[int, int, int, int]]:
        regions = getattr(self, "_regions", None)
        if regions is not None:
            return list(regions)
        return [self._region] if self._region is not None else []

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
        with self._cond:
            self._target_index = idx
            self._cond.notify_all()

    def set_region(self, region: tuple[int, int, int, int] | list | dict | None) -> bool:
        if isinstance(region, list) and (len(region) != 4 or any(isinstance(v, (dict, list, tuple)) for v in region)):
            return self.set_regions(region)
        norm = normalize_region_tuple(region)
        with self._cond:
            self._regions = [norm] if norm is not None else []
            self._region = norm
            self._cond.notify_all()
        return True

    def set_regions(self, regions) -> bool:
        parsed = active_region_tuples(regions) if regions else []
        with self._cond:
            self._regions = parsed
            self._region = parsed[0] if parsed else None
            self._cond.notify_all()
        return True

    def clear_region(self) -> bool:
        return self.set_regions([])

    def clear_regions(self) -> bool:
        return self.set_regions([])

    def _compute_bounds(self) -> tuple[int, int, int, int]:
        regions = self.regions
        if regions:
            return regions[0]

        try:
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if isinstance(app, QGuiApplication):
                screens = app.screens()
                if 0 <= self._target_index < len(screens):
                    scr = screens[self._target_index]
                    geo = scr.geometry()
                    dpr = scr.devicePixelRatio()
                    x = int(round(geo.x() * dpr))
                    y = int(round(geo.y() * dpr))
                    w = int(round(geo.width() * dpr))
                    h = int(round(geo.height() * dpr))
                    if w > 0 and h > 0:
                        return (x, y, w, h)
        except Exception:
            pass

        if self._user32 is not None:
            vx = int(self._user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
            vy = int(self._user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
            vw = int(self._user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
            vh = int(self._user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
            if vw > 0 and vh > 0:
                return (vx, vy, vw, vh)

        return (0, 0, 1920, 1080)

    def _compute_bounds_list(self) -> list[tuple[int, int, int, int]]:
        with self._cond:
            regions = list(self._regions)
        if regions:
            return regions
        return [self._compute_bounds()]

    def _create_mag_slot(
        self, hinst, x: int, y: int, w: int, h: int
    ) -> tuple[int, int] | None:
        user32 = self._user32
        mag = self._mag_dll
        assert user32 is not None and mag is not None
        ex_style = (
            _WS_EX_TOPMOST
            | _WS_EX_TOOLWINDOW
            | _WS_EX_LAYERED
            | _WS_EX_TRANSPARENT
            | _WS_EX_NOACTIVATE
        )
        host = user32.CreateWindowExW(
            ex_style,
            "Static",
            "ColorinkRegionGrayscaleHost",
            _WS_POPUP | _WS_CLIPCHILDREN,
            x,
            y,
            w,
            h,
            None,
            None,
            hinst,
            None,
        )
        if not host:
            return None
        user32.SetLayeredWindowAttributes(host, 0, 255, _LWA_ALPHA)
        mag_hwnd = user32.CreateWindowExW(
            0,
            "Magnifier",
            "ColorinkRegionMagnifier",
            _WS_CHILD | _WS_VISIBLE,
            0,
            0,
            w,
            h,
            host,
            None,
            hinst,
            None,
        )
        if not mag_hwnd:
            user32.DestroyWindow(host)
            return None
        mag.MagSetColorEffect(mag_hwnd, ctypes.byref(_GRAYSCALE_MATRIX))
        return (host, mag_hwnd)

    @staticmethod
    def _update_mag_filter_lists(mag, slots: list[tuple[int, int]], active_count: int) -> None:
        # 排除自身及所有同组宿主窗口，避免递归自捕获或区域重叠时二次抓取
        active_hosts = [slots[i][0] for i in range(active_count)]
        for i in range(active_count):
            host_i, mag_i = slots[i]
            ordered = [host_i] + [h for h in active_hosts if h != host_i]
            ordered = ordered[:25]
            exclude_list = (wintypes.HWND * len(ordered))(*ordered)
            mag.MagSetWindowFilterList(mag_i, _MW_FILTERMODE_EXCLUDE, len(ordered), exclude_list)

    def _worker_loop(self) -> None:
        user32 = self._user32
        mag = self._mag_dll
        kernel32 = self._kernel32
        if user32 is None or mag is None or kernel32 is None:
            self._init_ok = False
            self._ready_event.set()
            return

        if not mag.MagInitialize():
            self.last_error = "MagInitialize 初始化失败"
            self._init_ok = False
            self._ready_event.set()
            return

        hinst = kernel32.GetModuleHandleW(None)
        init_bounds = self._compute_bounds_list()
        x0, y0, w0, h0 = init_bounds[0]
        first_slot = self._create_mag_slot(hinst, x0, y0, w0, h0)
        if first_slot is None:
            self.last_error = "创建区域灰度宿主窗口失败"
            mag.MagUninitialize()
            self._init_ok = False
            self._ready_event.set()
            return

        slots: list[tuple[int, int]] = [first_slot]
        applied_bounds: list[tuple[int, int, int, int] | None] = [None]
        applied_active_count = 0
        self._update_mag_filter_lists(mag, slots, 1)

        self._init_ok = True
        self._ready_event.set()

        msg = wintypes.MSG()

        while self._running:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            if not self._is_active:
                for idx, (host, _mag_hwnd) in enumerate(slots):
                    if applied_bounds[idx] is not None:
                        user32.ShowWindow(host, _SW_HIDE)
                        applied_bounds[idx] = None
                applied_active_count = 0
                with self._cond:
                    while self._running and not self._is_active:
                        self._cond.wait(timeout=0.05)
                continue

            bounds_list = self._compute_bounds_list()
            needed = len(bounds_list)
            while len(slots) < needed:
                bx, by, bw, bh = bounds_list[len(slots)]
                new_slot = self._create_mag_slot(hinst, bx, by, bw, bh)
                if new_slot is None:
                    break
                slots.append(new_slot)
                applied_bounds.append(None)

            active_count = min(needed, len(slots))
            for idx in range(active_count, len(slots)):
                if applied_bounds[idx] is not None:
                    user32.ShowWindow(slots[idx][0], _SW_HIDE)
                    applied_bounds[idx] = None

            if applied_active_count != active_count and active_count > 0:
                self._update_mag_filter_lists(mag, slots, active_count)
                applied_active_count = active_count

            t0 = time.perf_counter()
            for idx in range(active_count):
                host, mag_hwnd = slots[idx]
                bounds = bounds_list[idx]
                bx, by, bw, bh = bounds
                if applied_bounds[idx] != bounds:
                    user32.SetWindowPos(
                        mag_hwnd,
                        0,
                        0,
                        0,
                        bw,
                        bh,
                        _SWP_NOACTIVATE,
                    )
                    user32.SetWindowPos(
                        host,
                        _HWND_TOPMOST,
                        bx,
                        by,
                        bw,
                        bh,
                        _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
                    )
                    applied_bounds[idx] = bounds

                rect = wintypes.RECT(bx, by, bx + bw, by + bh)
                mag.MagSetWindowSource(mag_hwnd, rect)

            dt = time.perf_counter() - t0
            if dt < 0.002:
                time.sleep(0.004)

        for host, _mag_hwnd in slots:
            user32.ShowWindow(host, _SW_HIDE)
            user32.DestroyWindow(host)
        mag.MagUninitialize()

    def set_active(self, active: bool, mode: str | None = None) -> bool:
        if not self.is_available:
            self.last_error = "窗口化 Magnifier 不可用"
            return False
        if active:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._running = True
                self._ready_event.clear()
                self._worker_thread = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name="colorink-region-mag",
                )
                self._worker_thread.start()
                self._ready_event.wait(timeout=1.0)
                if not self._init_ok:
                    self._running = False
                    return False
            with self._cond:
                self._is_active = True
                self._cond.notify_all()
            return True
        else:
            with self._cond:
                self._is_active = False
                self._cond.notify_all()
            return True

    def stop(self) -> None:
        self.set_active(False)

    def close(self) -> None:
        with self._cond:
            self._is_active = False
            self._running = False
            self._cond.notify_all()
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=0.5)
        self._worker_thread = None


class RegionGrayscaleController:
    """窗口/区域灰度滤镜统一控制器.

    默认以无边框、无任务栏图标的最大化穿透窗口呈现，支持框选局部灰度区域或重置回最大化。
    """

    def __init__(
        self,
        mode: str = "oklch",
        backend: str = "auto",
        region: tuple[int, int, int, int] | list | dict | None = None,
        regions: list | tuple | None = None,
    ) -> None:
        self._mode = mode if mode in ("oklch", "luma") else "oklch"
        self._preferred_backend = (
            backend if backend in ("auto", "dcomp", "mag") else "auto"
        )
        self._target = "all"
        initial_src = regions if regions is not None else region
        self._regions: list[tuple[int, int, int, int]] = active_region_tuples(initial_src)
        self._region: tuple[int, int, int, int] | None = (
            self._regions[0] if self._regions else None
        )
        self.last_error = ""

        self._dcomp = DCompGrayscaleController(mode=self._mode)
        self._sync_engine_regions(self._dcomp)
        self._mag = WindowedMagGrayscale()
        self._sync_engine_regions(self._mag)
        self._active_engine: DCompGrayscaleController | WindowedMagGrayscale | None = None

    @property
    def is_available(self) -> bool:
        return self._dcomp.is_available or self._mag.is_available

    @property
    def is_active(self) -> bool:
        if self._active_engine is not None:
            return self._active_engine.is_active
        return False

    @property
    def is_healthy(self) -> bool:
        if self._active_engine is not None:
            return self._active_engine.is_healthy
        return self._dcomp.is_healthy or self._mag.is_healthy

    @property
    def active_backend_name(self) -> str:
        if self._active_engine is self._dcomp and self._dcomp.is_active:
            return f"DirectComposition 区域窗口 ({self._mode.upper()})"
        if self._active_engine is self._mag and self._mag.is_active:
            return "Windows Magnifier 区域窗口 (Luma)"
        if self._preferred_backend == "mag":
            return "Windows Magnifier 区域窗口 (待命)"
        if self._dcomp.is_available:
            return f"DirectComposition 区域窗口 ({self._mode.upper()}, 待命)"
        if self._mag.is_available:
            return "Windows Magnifier 区域窗口 (Luma, 待命)"
        return "不可用"

    @property
    def preferred_backend(self) -> str:
        return self._preferred_backend

    def set_backend(self, backend: str) -> None:
        if backend not in ("auto", "dcomp", "mag"):
            backend = "auto"
        if backend == self._preferred_backend:
            return
        was_active = self.is_active
        if was_active:
            self.set_active(False)
        self._preferred_backend = backend
        if was_active:
            self.set_active(True)

    @property
    def region(self) -> tuple[int, int, int, int] | None:
        regions = getattr(self, "_regions", None)
        if regions:
            return regions[0]
        return self._region

    @property
    def regions(self) -> list[tuple[int, int, int, int]]:
        regions = getattr(self, "_regions", None)
        if regions is not None:
            return list(regions)
        return [self._region] if self._region is not None else []

    def _sync_engine_regions(self, engine) -> None:
        regions = self.regions
        if len(regions) > 1 and hasattr(engine, "set_regions"):
            engine.set_regions(regions)
        elif len(regions) == 1:
            engine.set_region(regions[0])
        else:
            engine.set_region(None)

    def set_region(self, region: tuple[int, int, int, int] | list | dict | None) -> bool:
        """设置灰度窗口区域（单区域或区域列表），传 None 恢复默认最大化窗口."""
        if isinstance(region, list) and (len(region) != 4 or any(isinstance(v, (dict, list, tuple)) for v in region)):
            return self.set_regions(region)
        norm = normalize_region_tuple(region)
        self._regions = [norm] if norm is not None else []
        self._region = norm
        self._sync_engine_regions(self._dcomp)
        self._sync_engine_regions(self._mag)
        return True

    def set_regions(self, regions) -> bool:
        """设置多个灰度窗口区域，传空列表或 None 恢复默认最大化窗口."""
        parsed = active_region_tuples(regions) if regions else []
        self._regions = parsed
        self._region = parsed[0] if parsed else None
        self._sync_engine_regions(self._dcomp)
        self._sync_engine_regions(self._mag)
        return True

    def clear_region(self) -> bool:
        """重置回默认最大化无边框窗口."""
        return self.set_regions([])

    def clear_regions(self) -> bool:
        """清除所有自定义区域，重置回默认最大化无边框窗口."""
        return self.set_regions([])

    def format_region_status(self) -> str:
        """返回供 UI 显示的当前区域描述."""
        regions = self.regions
        if not regions:
            return "默认最大化"
        if len(regions) == 1:
            rx, ry, rw, rh = regions[0]
            return f"{rw}×{rh} ({rx}, {ry})"
        return f"已启用 {len(regions)} 个区域"

    def prepare(self) -> None:
        if self._dcomp.is_available:
            self._dcomp.prepare()

    def set_mode(self, mode: str) -> None:
        if mode not in ("oklch", "luma"):
            mode = "oklch"
        self._mode = mode
        if self._dcomp.is_available:
            self._dcomp.set_mode(mode)

    def set_target(self, target: str | int) -> None:
        self._target = str(target or "all")
        if self._dcomp.is_available:
            self._dcomp.set_target(target)
        if self._mag.is_available:
            self._mag.set_target(target)

    @property
    def target(self) -> str:
        return self._target

    def set_active(self, active: bool, mode: str | None = None) -> bool:
        if mode is not None:
            self.set_mode(mode)

        if not active:
            if self._active_engine is not None:
                if self._active_engine is self._dcomp:
                    # 关闭区域模式时重置底层 DComp 的裁剪状态，避免影响原有全屏灰度滤镜
                    self._dcomp.clear_region()
                ok = self._active_engine.set_active(False)
                self._active_engine = None
                self._sync_engine_regions(self._dcomp)
                return ok
            return True

        if self._preferred_backend == "mag":
            engines = [self._mag]
        elif self._preferred_backend == "dcomp":
            engines = [self._dcomp]
        else:
            engines = [self._dcomp, self._mag]

        for engine in engines:
            if not engine.is_available:
                continue
            engine.set_target(self._target)
            self._sync_engine_regions(engine)
            if engine.set_active(True, self._mode):
                self._active_engine = engine
                self.last_error = ""
                return True
            self.last_error = engine.last_error

        if not self.last_error:
            self.last_error = "所选区域灰度后端不可用"
        return False

    def toggle(self) -> bool:
        return self.set_active(not self.is_active, self._mode)

    def stop(self) -> None:
        self.set_active(False)

    def close(self) -> None:
        self.stop()
        self._dcomp.close()
        self._mag.close()
