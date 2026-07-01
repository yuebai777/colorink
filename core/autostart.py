import sys

def apply_autostart(enabled: bool):
    import sys
    if sys.platform != 'win32':
        return
        
    is_packaged = getattr(sys, 'frozen', False)
    if not is_packaged:
        print("Skip autostart registration in development mode")
        return
        
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = "PaletteLite"
    exe_path = sys.executable
    
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, f'"{exe_path}"')
            print("Successfully registered autostart registry key")
        else:
            try:
                winreg.DeleteValue(key, value_name)
                print("Successfully unregistered autostart registry key")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print("Failed to apply autostart registry key:", e)
