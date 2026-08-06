import keyboard
import mouse as _mouse
from PyQt6.QtCore import QObject, pyqtSignal


class HotkeySignals(QObject):
    # Emits the configuration key name, e.g. "pickKey", "hideWindowKey", "followMouseKey"
    triggered = pyqtSignal(str)

hotkey_signals = HotkeySignals()

_bound_hotkeys = {}

# Canonical mouse-button hotkey names (ui.hotkey_button) → mouse-lib button names.
_MOUSE_BUTTON_NAMES = {
    "LeftButton": _mouse.LEFT,
    "RightButton": _mouse.RIGHT,
    "MiddleButton": _mouse.MIDDLE,
    "X1": _mouse.X,
    "X2": _mouse.X2,
}

_bound_mouse_hotkeys = {}  # hotkey_type -> handler (for unhook)

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

def bind_mouse_hotkey(hotkey_type: str, hotkey_str: str):
    """Register a system-wide mouse-button hotkey (e.g. "X1", "MiddleButton").

    The click is not suppressed, so the app under the cursor still receives
    it. Emits the same ``hotkey_signals.triggered`` channel as keyboard
    hotkeys, so the main window handles both uniformly.
    """
    button = _MOUSE_BUTTON_NAMES.get(hotkey_str)
    if button is None:
        return

    # Unregister existing hotkey of this type
    if hotkey_type in _bound_mouse_hotkeys:
        try:
            _mouse.unhook(_bound_mouse_hotkeys[hotkey_type])
        except Exception:
            pass

    def callback(*_args):
        hotkey_signals.triggered.emit(hotkey_type)

    try:
        handler = _mouse.on_button(callback, buttons=(button,), types=(_mouse.DOWN,))
        _bound_mouse_hotkeys[hotkey_type] = handler
        print(f"[Hotkeys] Bound global mouse hotkey: {hotkey_type} -> {hotkey_str}")
    except Exception as e:
        print(f"[Hotkeys] Failed to bind mouse hotkey {hotkey_type} ({hotkey_str}): {e}")

def unbind_all():
    keyboard.unhook_all()
    for handler in list(_bound_mouse_hotkeys.values()):
        try:
            _mouse.unhook(handler)
        except Exception:
            pass
    _bound_mouse_hotkeys.clear()
    _bound_hotkeys.clear()
    print("[Hotkeys] Unbound all global hotkeys")
