"""CSP process scanning / Win32 version detection and live attachment.

Extracted from ``core.csp_brush_link``: the Win32 version-info surface and
the CSPSync methods that attach to / detach from the running CSP process.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

try:
    from pymem import Pymem
    from pymem.process import module_from_name
except ImportError:
    Pymem = None  # type: ignore[assignment]
    module_from_name = None  # type: ignore[assignment]

from core.csp_brush_link.profiles import _log


class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature",         wintypes.DWORD),
        ("dwStrucVersion",      wintypes.DWORD),
        ("dwFileVersionMS",    wintypes.DWORD),
        ("dwFileVersionLS",     wintypes.DWORD),
        ("dwProductVersionMS",  wintypes.DWORD),
        ("dwProductVersionLS",   wintypes.DWORD),
        ("dwFileFlagsMask",     wintypes.DWORD),
        ("dwFileFlags",         wintypes.DWORD),
        ("dwFileOS",            wintypes.DWORD),
        ("dwFileType",          wintypes.DWORD),
        ("dwFileSubtype",       wintypes.DWORD),
        ("dwFileDateMS",        wintypes.DWORD),
        ("dwFileDateLS",        wintypes.DWORD),
    ]


class _ProcessVersionQuery:
    """Lazy ctypes bindings for the Win32 process-image + version-info APIs.

    These calls give us (a) the full path of the running CLIPStudioPaint.exe
    and (b) the VS_VERSION_INFO of that on-disk exe, which we use to
    auto-detect which CSP build is currently attached without trusting
    the user-supplied version key as ground truth.
    """

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _version  = ctypes.WinDLL("version",  use_last_error=True)

    # Bind once at class-definition time so we don't pay argtype setup per call.
    _query_image_name = _kernel32.QueryFullProcessImageNameW
    _query_image_name.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _query_image_name.restype = wintypes.BOOL

    _version_info_size = _version.GetFileVersionInfoSizeW
    _version_info_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _version_info_size.restype  = wintypes.DWORD

    _version_info = _version.GetFileVersionInfoW
    _version_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    _version_info.restype  = wintypes.BOOL

    _ver_query_value = _version.VerQueryValueW
    _ver_query_value.argtypes = [
        wintypes.LPCVOID, wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
    ]
    _ver_query_value.restype = wintypes.BOOL

    @classmethod
    def image_path(cls, process_handle) -> str | None:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not cls._query_image_name(
            wintypes.HANDLE(int(process_handle)), 0, buffer, ctypes.byref(size)
        ):
            return None
        return buffer.value

    @classmethod
    def exe_version(cls, path: str) -> tuple[int, int, int, int] | None:
        """Return the four-component file version of the exe at ``path``."""
        scratch = wintypes.DWORD(0)
        size = cls._version_info_size(path, ctypes.byref(scratch))
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not cls._version_info(path, 0, size, buffer):
            return None
        value_ptr = ctypes.c_void_p()
        value_len = wintypes.UINT(0)
        if not cls._ver_query_value(buffer, "\\", ctypes.byref(value_ptr), ctypes.byref(value_len)):
            return None
        fixed = ctypes.cast(value_ptr, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        return (
            (fixed.dwFileVersionMS >> 16) & 0xFFFF,
            fixed.dwFileVersionMS & 0xFFFF,
            (fixed.dwFileVersionLS >> 16) & 0xFFFF,
            fixed.dwFileVersionLS & 0xFFFF,
        )


def _detect_build_from_version(version: tuple[int, int, int, int] | None) -> str | None:
    """Map a CSP file version to a simplified profile key."""
    if not version:
        return None
    major, minor, _build, _patch = version
    if (major, minor) >= (5, 1):
        return "csp5.1"
    if (major, minor) >= (5, 0):
        return "csp5.x"
    if major == 4:
        return "csp4.x"
    return None


def _detect_build_from_image_path(path: str | None) -> str | None:
    """Map an exe's on-disk file version to a simplified profile key."""
    if not path:
        return None
    return _detect_build_from_version(_ProcessVersionQuery.exe_version(path))


class ProcessMixin:
    # ----- connection management -----------------------------------------
    def connect(self) -> bool:
        """Attach to the running CSP process and resolve the color slot pointer.

        If the on-disk exe version doesn't match the currently selected
        profile, the profile is silently swapped to the detected one and
        the pointer is read against the new base offset.
        """
        try:
            if Pymem is None or module_from_name is None:
                raise ValueError("pymem not available")
            self.pm = Pymem(self.process_name)
            self.pid = self.pm.process_id
            mod = module_from_name(self.pm.process_handle, self.process_name)
            if mod is None:
                raise ValueError("module not found")
            module_base = mod.lpBaseOfDll
            self.module_base = module_base

            image_path = _ProcessVersionQuery.image_path(self.pm.process_handle)
            detected = _detect_build_from_image_path(image_path)
            if detected and detected != self.current_version:
                requested = self.current_version
                self._apply_profile(detected)
                _log(
                    f"Auto-detected version {detected} from process "
                    f"(requested={requested}, path={image_path})"
                )

            ptr_addr = module_base + self.base_offset
            dereferenced = int(self.pm.read_longlong(ptr_addr))
            if self.intermediate_offset is not None:
                self.target = dereferenced + self.intermediate_offset
            else:
                self.target = dereferenced
            _log(
                "Connected: "
                f"PID={self.pid} Version={self.current_version} "
                f"Base=0x{self.base_offset:X} Target=0x{self.target:X} "
                f"R_off=0x{self.r_off:X} G_off=0x{self.g_off:X} B_off=0x{self.b_off:X}"
            )
            return True
        except Exception as exc:
            _log(f"connect failed: {exc}")
            self._drop_connection()
            return False

    def _drop_connection(self) -> None:
        if self.pm is not None:
            try:
                self.pm.close_process()
            except Exception:
                pass
        self.pm = None
        self.pid = None
        self.module_base = None
        self.target = None
