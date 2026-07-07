"""
DWM grayscale filter injection controller.

Manages the C++ dwm_grayscale.dll injection into dwm.exe and
communicates the active mode (disabled/OKLCh/Luma) via shared memory.

Usage:
    ctrl = DwmGrayscaleController()
    ctrl.inject()              # inject DLL into dwm.exe
    ctrl.set_mode("oklch")    # enable OKLCh grayscale
    ctrl.set_mode("luma")     # switch to BT.709 luma
    ctrl.set_mode("disabled") # passthrough (no grayscale)
    ctrl.eject()               # unload DLL from dwm.exe

Requires:
    - dwm_grayscale.dll (built from dwm_grayscale/build.bat)
    - Administrator privileges (for DLL injection into dwm.exe)
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import subprocess

# ---------------------------------------------------------------------------
# Win32 API bindings — all restype/argtypes MUST be explicitly set on x64
# or ctypes defaults to c_int (32-bit) and truncates 64-bit pointers.
# ---------------------------------------------------------------------------
_kernel32 = ctypes.windll.kernel32
_advapi32 = ctypes.windll.advapi32

# Process
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = [ctypes.c_uint32, wt.BOOL, ctypes.c_uint32]
_kernel32.CloseHandle.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.GetCurrentProcessId.restype = ctypes.c_uint32
_kernel32.GetLastError.restype = ctypes.c_uint32

# Memory
_kernel32.VirtualAllocEx.restype = ctypes.c_void_p
_kernel32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
_kernel32.VirtualFreeEx.restype = wt.BOOL
_kernel32.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
_kernel32.WriteProcessMemory.restype = wt.BOOL
_kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

# Threads
_kernel32.CreateRemoteThread.restype = wt.HANDLE
_kernel32.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
_kernel32.WaitForSingleObject.restype = ctypes.c_uint32
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, ctypes.c_uint32]
_kernel32.GetExitCodeThread.restype = wt.BOOL
_kernel32.GetExitCodeThread.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_uint32)]

# Modules — GetProcAddress MUST be c_void_p, not default c_int!
_kernel32.GetModuleHandleW.restype = wt.HMODULE
_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
_kernel32.GetProcAddress.restype = ctypes.c_void_p  # CRITICAL: default c_int truncates 64-bit addrs
_kernel32.GetProcAddress.argtypes = [wt.HMODULE, ctypes.c_char_p]

# Control file path (same as DLL's CTRL_FILE_PATH)
_CTRL_FILE = os.path.join(
    os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp", "dwm_grayscale_mode.txt"
)

# Grayscale modes
MODE_DISABLED = 0
MODE_OKLCH = 1
MODE_LUMA = 2

_MODE_MAP = {"disabled": MODE_DISABLED, "oklch": MODE_OKLCH, "luma": MODE_LUMA}

# Process access rights for injection
PROCESS_ALL_ACCESS = 0x1F0FFF


class DwmGrayscaleController:
    """Controls the DWM-injected grayscale filter DLL."""

    def __init__(self):
        self._dll_path: str | None = None
        self._injected = False
        self._last_error: str | None = None
        self._flip_windows: list = []
        self._find_dll()

    def _create_flip_windows(self):
        """Create anti-DirectFlip overlay windows on all screens.

        On Windows 11 25H2, DWM uses DirectFlip to bypass the compositor
        for fullscreen content — our Present hook never fires. A nearly-
        transparent layered topmost window forces DWM to use the compositor
        path so Present is called.
        """
        try:
            from PyQt6.QtWidgets import QWidget, QApplication
            from PyQt6.QtCore import Qt
            app = QApplication.instance()
            if not app:
                return
            for screen in app.screens():
                w = QWidget()
                w.setWindowFlags(
                    Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.WindowStaysOnTopHint
                    | Qt.WindowType.Tool
                    | Qt.WindowType.WindowTransparentForInput
                    | Qt.WindowType.WindowDoesNotAcceptFocus
                )
                w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                w.setStyleSheet("background: rgba(0,0,0,0);")
                w.setGeometry(screen.geometry())
                # Set layered window alpha to 1 (out of 255) — almost invisible
                # but forces DWM to composite, disabling DirectFlip.
                hwnd = int(w.winId())
                ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x80000)  # WS_EX_LAYERED
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 1, 0x02)  # LWA_ALPHA, alpha=1
                w.show()
                self._flip_windows.append(w)
            print(f"[DwmGrayscale] Created {len(self._flip_windows)} anti-DirectFlip windows")
        except Exception as e:
            print(f"[DwmGrayscale] Flip window creation failed: {e}")

    def _destroy_flip_windows(self):
        """Destroy anti-DirectFlip overlay windows."""
        for w in self._flip_windows:
            try:
                w.hide()
                w.deleteLater()
            except Exception:
                pass
        self._flip_windows.clear()

    @staticmethod
    def _is_admin() -> bool:
        """Check if the current process is running with admin privileges."""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    @staticmethod
    def _clear_file_permissions(path: str):
        """Clear DACL on a file so dwm.exe (SYSTEM) can read it."""
        try:
            advapi32 = ctypes.windll.advapi32
            k32 = ctypes.windll.kernel32

            READ_CONTROL = 0x20000
            WRITE_DAC = 0x40000
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80
            FILE_FLAG_BACKUP_SEMANTICS = 0x2000000
            SE_FILE_OBJECT = 1
            DACL_SECURITY_INFORMATION = 0x4

            k32.CreateFileW.restype = wt.HANDLE
            k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, wt.HANDLE]

            h_file = k32.CreateFileW(
                path, READ_CONTROL | WRITE_DAC, 0, None,
                OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_BACKUP_SEMANTICS, None,
            )
            if h_file and h_file != wt.HANDLE(-1).value:
                advapi32.SetSecurityInfo(
                    h_file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                    None, None, None, None,
                )
                k32.CloseHandle(h_file)
        except Exception:
            pass

    def _find_dll(self):
        """Locate dwm_grayscale.dll relative to this module."""
        # Look in dwm_grayscale/build/ subdirectory
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(base, "dwm_grayscale", "build", "dwm_grayscale.dll"),
            os.path.join(base, "dwm_grayscale", "dwm_grayscale.dll"),
        ]
        for path in candidates:
            if os.path.exists(path):
                self._dll_path = os.path.abspath(path)
                return
        # DLL not found — will be reported on inject()

    @property
    def is_injected(self) -> bool:
        return self._injected

    @property
    def dll_path(self) -> str | None:
        return self._dll_path

    @property
    def is_available(self) -> bool:
        """True if the DLL has been built and is available."""
        return self._dll_path is not None and os.path.exists(self._dll_path)

    @property
    def last_error(self) -> str | None:
        """Last error message from inject/toggle, or None if no error."""
        return self._last_error

    @property
    def needs_admin(self) -> bool:
        """True if the current process lacks admin privileges."""
        return not self._is_admin()

    # -- Control file ----------------------------------------------------

    def _write_mode(self, mode: int):
        """Write grayscale mode to the control file (read by DLL)."""
        try:
            os.makedirs(os.path.dirname(_CTRL_FILE), exist_ok=True)
            with open(_CTRL_FILE, "w") as f:
                f.write(str(mode))
        except Exception as e:
            print(f"[DwmGrayscale] Write control file failed: {e}")

    def _read_mode(self) -> int:
        """Read current mode from control file."""
        try:
            if not os.path.exists(_CTRL_FILE):
                return MODE_DISABLED
            with open(_CTRL_FILE, "r") as f:
                val = f.read().strip()
                return int(val) if val in ("0", "1", "2") else MODE_DISABLED
        except Exception:
            return MODE_DISABLED

    # -- DLL injection ----------------------------------------------------

    def _enable_debug_privilege(self) -> bool:
        """Enable SeDebugPrivilege — required to open dwm.exe on 25H2."""
        try:
            TOKEN_ADJUST_PRIVILEGES = 0x0020
            TOKEN_QUERY = 0x0008
            SE_PRIVILEGE_ENABLED = 0x00000002

            class LUID(ctypes.Structure):
                _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]

            class TOKEN_PRIVILEGES(ctypes.Structure):
                _fields_ = [
                    ("PrivilegeCount", ctypes.c_uint32),
                    ("Luid", LUID),
                    ("Attributes", ctypes.c_uint32),
                ]

            advapi32 = ctypes.windll.advapi32
            k32 = ctypes.windll.kernel32

            h_token = wt.HANDLE()
            if not advapi32.OpenProcessToken(
                k32.GetCurrentProcess(),
                TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                ctypes.byref(h_token),
            ):
                return False

            luid = LUID()
            if not advapi32.LookupPrivilegeValueA(None, b"SeDebugPrivilege", ctypes.byref(luid)):
                k32.CloseHandle(h_token)
                return False

            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Luid = luid
            tp.Attributes = SE_PRIVILEGE_ENABLED

            ok = advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), 0, None, None)
            err = k32.GetLastError()
            k32.CloseHandle(h_token)
            # ERROR_NOT_ALL_ASSIGNED (1300) means privilege not held
            return ok and err != 1300
        except Exception:
            return False

    def _find_dwm_pid(self) -> int:
        """Find the PID of dwm.exe in the current session."""
        import subprocess
        # Get current session ID
        k32 = ctypes.windll.kernel32
        k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(ctypes.c_uint32(0)))
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq dwm.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True
        )
        for line in result.stdout.strip().splitlines():
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    continue
        raise RuntimeError("dwm.exe not found")

    def inject(self) -> bool:
        """Inject dwm_grayscale.dll into dwm.exe. Requires admin privileges.

        Returns True on success, False on failure (error stored in _last_error).
        Never raises — safe to call from UI thread.
        """
        if self._injected:
            return True

        # Check admin privileges first — injection requires them
        if not self._is_admin():
            self._last_error = (
                "DWM 注入需要管理员权限。\n"
                "请右键点击程序 → 以管理员身份运行，\n"
                "或在设置里切换回 \"OpenGL Overlay\" 后端。"
            )
            print(f"[DwmGrayscale] {self._last_error}")
            return False

        if not self._dll_path or not os.path.exists(self._dll_path):
            self._last_error = (
                f"dwm_grayscale.dll 未找到。\n"
                f"请先编译: cd dwm_grayscale && build.bat\n"
                f"预期路径: {self._dll_path or '(未定位)'}"
            )
            print(f"[DwmGrayscale] {self._last_error}")
            return False

        try:
            # Enable SeDebugPrivilege (equivalent to Process.EnterDebugMode in C#)
            if not self._enable_debug_privilege():
                print("[DwmGrayscale] Warning: SeDebugPrivilege not enabled")

            # Write initial disabled state to control file
            self._write_mode(MODE_DISABLED)

            pid = self._find_dwm_pid()

            # Copy DLL to %SYSTEMROOT%\Temp\ — dwm.exe may not have read access
            # to arbitrary user folders (e.g. "D:\Program Files\...").
            # dwm_lut uses the same approach.
            import shutil
            temp_dir = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp")
            temp_dll = os.path.join(temp_dir, "dwm_grayscale.dll")
            try:
                # Force-remove old DLL (may be SYSTEM-owned from previous injection)
                if os.path.exists(temp_dll):
                    self._clear_file_permissions(temp_dll)
                    os.remove(temp_dll)
                shutil.copy2(self._dll_path, temp_dll)
                # Clear ACL so dwm.exe (SYSTEM account) can read it
                self._clear_file_permissions(temp_dll)
            except Exception as e:
                self._last_error = f"无法复制 DLL 到 {temp_dll}: {e}"
                print(f"[DwmGrayscale] {self._last_error}")
                return False

            dll_path = temp_dll.encode("ascii") + b"\x00"
            print(f"[DwmGrayscale] DLL staged at: {temp_dll}")

            # Open dwm.exe process
            h_process = _kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
            if not h_process:
                err = _kernel32.GetLastError()
                self._last_error = f"OpenProcess(dwm.exe pid={pid}) 失败 (错误码 {err})\n可能需要 SeDebugPrivilege。"
                print(f"[DwmGrayscale] {self._last_error}")
                return False

            try:
                # Use PAGE_READWRITE (0x04) NOT PAGE_EXECUTE_READWRITE (0x40)
                # — dwm.exe on 25H2 has Code Integrity Guard that blocks executable memory
                remote_buf = _kernel32.VirtualAllocEx(
                    h_process, None, len(dll_path), 0x3000, 0x04
                )
                if not remote_buf:
                    err = _kernel32.GetLastError()
                    self._last_error = f"VirtualAllocEx 失败 (错误码 {err})"
                    return False

                written = ctypes.c_size_t(0)
                if not _kernel32.WriteProcessMemory(h_process, remote_buf, dll_path, len(dll_path), ctypes.byref(written)):
                    err = _kernel32.GetLastError()
                    self._last_error = f"WriteProcessMemory 失败 (错误码 {err})"
                    return False

                h_kernel32 = _kernel32.GetModuleHandleW("kernel32.dll")
                load_lib = _kernel32.GetProcAddress(h_kernel32, b"LoadLibraryA")
                if not load_lib:
                    err = _kernel32.GetLastError()
                    self._last_error = f"GetProcAddress(LoadLibraryA) 失败 (错误码 {err})"
                    return False

                print(f"[DwmGrayscale] LoadLibraryA addr: 0x{load_lib:016X}")
                print(f"[DwmGrayscale] Remote buf addr: 0x{remote_buf:016X}")

                # CreateRemoteThread: start routine = LoadLibraryA, param = remote_buf (DLL path)
                thread_id = ctypes.c_uint32(0)
                h_thread = _kernel32.CreateRemoteThread(
                    h_process, None, 0,
                    ctypes.c_void_p(load_lib),  # explicitly wrap as pointer
                    ctypes.c_void_p(remote_buf),  # explicitly wrap as pointer
                    0, ctypes.byref(thread_id),
                )
                if not h_thread:
                    err = _kernel32.GetLastError()
                    self._last_error = f"CreateRemoteThread 失败 (错误码 {err})"
                    return False

                _kernel32.WaitForSingleObject(h_thread, 5000)

                # Check if LoadLibraryA succeeded (exit code = module handle)
                exit_code = ctypes.c_uint32(0)
                _kernel32.GetExitCodeThread(h_thread, ctypes.byref(exit_code))
                print(f"[DwmGrayscale] Remote thread exit code: {exit_code.value}")

                _kernel32.CloseHandle(h_thread)
                _kernel32.VirtualFreeEx(h_process, ctypes.c_void_p(remote_buf), 0, 0x8000)

                if exit_code.value == 0:
                    self._last_error = (
                        "DLL 加载失败 (LoadLibraryA 返回 0)。\n"
                        "可能原因: DLL 路径不存在、DLL 依赖缺失、或 DWM 拒绝加载。"
                    )
                    print(f"[DwmGrayscale] {self._last_error}")
                    return False

                self._injected = True
                self._last_error = None
                print(f"[DwmGrayscale] Injected into dwm.exe (pid={pid})")
                return True

            finally:
                _kernel32.CloseHandle(h_process)

        except Exception as e:
            self._last_error = f"注入异常: {e}"
            print(f"[DwmGrayscale] {self._last_error}")
            return False

    def eject(self):
        """Unload the DLL from dwm.exe."""
        if not self._injected:
            return

        # Set mode to disabled first so the hook stops processing
        self._write_mode(MODE_DISABLED)
        import time; time.sleep(0.2)  # let any in-flight Present calls finish

        pid = self._find_dwm_pid()
        h_process = _kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not h_process:
            self._injected = False
            self._write_mode(MODE_DISABLED)
            return

        try:
            # Find the DLL's base address in dwm.exe
            # Use CreateRemoteThread(FreeLibrary) approach
            h_kernel32 = _kernel32.GetModuleHandleW("kernel32.dll")
            free_lib = _kernel32.GetProcAddress(h_kernel32, b"FreeLibrary")
            if not free_lib:
                return

            # Enumerate modules to find our DLL handle
            import struct
            h_modules = (wt.HMODULE * 1024)()
            cb_needed = wt.DWORD(0)
            psapi = ctypes.windll.psapi
            psapi.EnumProcessModules(h_process, h_modules, ctypes.sizeof(h_modules), ctypes.byref(cb_needed))
            num_modules = cb_needed.value // ctypes.sizeof(wt.HMODULE)

            our_handle = None
            for i in range(num_modules):
                mod_name = ctypes.create_unicode_buffer(260)
                psapi.GetModuleBaseNameW(h_process, h_modules[i], mod_name, 260)
                if mod_name.value.lower() == "dwm_grayscale.dll":
                    our_handle = h_modules[i]
                    break

            if our_handle:
                h_thread = _kernel32.CreateRemoteThread(
                    h_process, None, 0, free_lib, our_handle, 0, None
                )
                if h_thread:
                    _kernel32.WaitForSingleObject(h_thread, 5000)
                    _kernel32.CloseHandle(h_thread)

        finally:
            _kernel32.CloseHandle(h_process)

        self._injected = False
        self._write_mode(MODE_DISABLED)
        print("[DwmGrayscale] Ejected from dwm.exe")

    # -- Public API -------------------------------------------------------

    def set_mode(self, mode: str):
        """Set grayscale mode: 'disabled', 'oklch', or 'luma'."""
        if mode not in _MODE_MAP:
            raise ValueError(f"Unknown mode: {mode!r}. Use 'disabled', 'oklch', or 'luma'")
        self._write_mode(_MODE_MAP[mode])

    def set_active(self, active: bool, mode: str = "oklch") -> bool:
        """Enable or disable grayscale with the given mode.

        Returns True on success, False if injection failed (check last_error).
        Never raises.
        """
        try:
            if active:
                # Write mode BEFORE injection
                self._write_mode(_MODE_MAP.get(mode, MODE_OKLCH))
                if not self._injected:
                    if not self.inject():
                        return False
                # Create anti-DirectFlip windows AFTER injection
                self._create_flip_windows()
                return True
            else:
                self._destroy_flip_windows()
                self.set_mode("disabled")
                return True
        except Exception as e:
            self._last_error = str(e)
            print(f"[DwmGrayscale] set_active error: {e}")
            return False

    def toggle(self, mode: str = "oklch") -> bool:
        """Toggle grayscale on/off.

        Returns True on success, False if injection failed.
        Never raises — safe to call directly from hotkey handler.
        """
        try:
            if self._injected:
                current = self._read_mode()
                if current == MODE_DISABLED:
                    self.set_mode(mode)
                else:
                    self.set_mode("disabled")
                return True
            else:
                # Not injected yet — need to inject first
                return self.set_active(True, mode)
        except Exception as e:
            self._last_error = str(e)
            print(f"[DwmGrayscale] toggle error: {e}")
            return False

    # -- Compatibility API (matches GrayscaleOverlay interface) ----------

    @property
    def is_active(self) -> bool:
        """True if grayscale is currently enabled."""
        return self._read_mode() != MODE_DISABLED

    def set_target(self, target: str):
        """No-op — DWM injection is always global across all screens."""
        pass

    @property
    def target(self) -> str:
        return "all"

    @staticmethod
    def available_screens() -> list[str]:
        """DWM injection applies to all screens simultaneously."""
        return ["all"]

    def close(self):
        """Clean shutdown — disable and eject."""
        if self._injected:
            self.eject()
        else:
            self._write_mode(MODE_DISABLED)
