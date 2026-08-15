import sys


def apply_autostart(enabled: bool) -> bool:
    """Register/unregister the HKCU Run entry. Returns True on success.

    ``print`` 在 --windowed 打包下不可见，调用方必须拿返回值决定
    是否回滚 UI 勾选状态。
    """
    if sys.platform != 'win32':
        return False

    is_packaged = getattr(sys, 'frozen', False)
    if not is_packaged:
        print("Skip autostart registration in development mode")
        return False

    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = "Colorink"
    exe_path = sys.executable

    key = None
    try:
        # Run 键可能不存在（精简系统）——CreateKeyEx 保证存在
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                 winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, f'"{exe_path}"')
            print("Successfully registered autostart registry key")
        else:
            try:
                winreg.DeleteValue(key, value_name)
                print("Successfully unregistered autostart registry key")
            except FileNotFoundError:
                pass
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
