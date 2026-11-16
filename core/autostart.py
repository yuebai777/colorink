import sys


_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_APPROVED_KEY_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
)
_VALUE_NAME = "Colorink"
# Windows StartupApproved\Run uses a 12-byte REG_BINARY where bit 0 of byte 0
# indicates disabled (e.g. 0x01 / 0x03) and 0x02 followed by 11 zero bytes
# represents an enabled entry without a disable timestamp.
_STARTUP_APPROVED_ENABLED = b"\x02" + b"\x00" * 11


def is_autostart_supported() -> bool:
    """Return True when the current runtime can manage HKCU Run autostart."""
    return sys.platform == 'win32' and bool(getattr(sys, 'frozen', False))


def _is_approval_disabled(raw_value) -> bool:
    if not isinstance(raw_value, (bytes, bytearray, memoryview)) or len(raw_value) < 1:
        return False
    return bool(raw_value[0] & 0x01)


def is_autostart_blocked_by_windows() -> bool:
    """Return True if Windows Startup Manager disabled the HKCU Run entry."""
    if sys.platform != 'win32':
        return False

    import winreg

    key = None
    try:
        key = winreg.OpenKeyEx(
            winreg.HKEY_CURRENT_USER,
            _STARTUP_APPROVED_KEY_PATH,
            0,
            winreg.KEY_QUERY_VALUE,
        )
        val, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        return _is_approval_disabled(val)
    except OSError:
        return False
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass


def _clear_disabled_startup_approval(winreg) -> None:
    key = None
    try:
        key = winreg.OpenKeyEx(
            winreg.HKEY_CURRENT_USER,
            _STARTUP_APPROVED_KEY_PATH,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        )
        val, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        if _is_approval_disabled(val):
            winreg.SetValueEx(
                key, _VALUE_NAME, 0, winreg.REG_BINARY, _STARTUP_APPROVED_ENABLED
            )
    except OSError:
        pass
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass


def _delete_startup_approval(winreg) -> None:
    key = None
    try:
        key = winreg.OpenKeyEx(
            winreg.HKEY_CURRENT_USER,
            _STARTUP_APPROVED_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, _VALUE_NAME)
    except OSError:
        pass
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass


def apply_autostart(enabled: bool, *, clear_disabled_approval: bool = True) -> bool:
    """Register/unregister the HKCU Run entry. Returns True on success.

    ``print`` 在 --windowed 打包下不可见，调用方必须拿返回值决定
    是否回滚 UI 勾选状态。

    When ``clear_disabled_approval`` is True and ``enabled`` is True, any
    Windows ``StartupApproved\\Run`` disabled marker is reset to enabled so
    explicit user opt-in takes effect immediately.
    """
    if sys.platform != 'win32':
        return False

    is_packaged = getattr(sys, 'frozen', False)
    if not is_packaged:
        print("Skip autostart registration in development mode")
        return False

    import winreg
    exe_path = sys.executable

    key = None
    try:
        # Run 键可能不存在（精简系统）——CreateKeyEx 保证存在
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0,
                                 winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
            if clear_disabled_approval:
                _clear_disabled_startup_approval(winreg)
            print("Successfully registered autostart registry key")
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
                print("Successfully unregistered autostart registry key")
            except FileNotFoundError:
                pass
            _delete_startup_approval(winreg)
        return True
    except Exception as e:
        print("Failed to apply autostart registry key:", e)
        return False
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass
