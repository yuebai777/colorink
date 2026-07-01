import keyboard
from PyQt6.QtCore import QObject, pyqtSignal

class HotkeySignals(QObject):
    # Emits the configuration key name, e.g. "pickKey", "hideWindowKey", "followMouseKey"
    triggered = pyqtSignal(str)

hotkey_signals = HotkeySignals()

_bound_hotkeys = {}

def bind_hotkey(hotkey_type: str, hotkey_str: str):
    if not hotkey_str:
        return
        
    # Unregister existing hotkey of this type
    if hotkey_type in _bound_hotkeys:
        try:
            keyboard.remove_hotkey(_bound_hotkeys[hotkey_type])
        except Exception:
            pass
            
    # Normalize shortcut (e.g. "Ctrl+R" -> "ctrl+r")
    normalized = hotkey_str.lower().strip()
    
    def callback():
        hotkey_signals.triggered.emit(hotkey_type)
        
    try:
        # suppress=False ensures modifiers (like Ctrl) are not blocked or swallowed, preserving CSP functionality
        keyboard.add_hotkey(normalized, callback, suppress=False)
        _bound_hotkeys[hotkey_type] = normalized
        print(f"[Hotkeys] Bound global hotkey: {hotkey_type} -> {normalized}")
    except Exception as e:
        print(f"[Hotkeys] Failed to bind global hotkey {hotkey_type} ({hotkey_str}): {e}")

def unbind_all():
    keyboard.unhook_all()
    _bound_hotkeys.clear()
    print("[Hotkeys] Unbound all global hotkeys")
