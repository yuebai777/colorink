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
_bound_mouse_names = {}    # hotkey_type -> canonical button name (for dup check)

def bind_hotkey(hotkey_type: str, hotkey_str: str):
    if not hotkey_str:
        return

    # Normalize shortcut (e.g. "Ctrl+R" -> "ctrl+r")
    normalized = hotkey_str.lower().strip()

    # 拒绝同一组合绑定到两个功能：keyboard 库以组合名为字典键，
    # 第二次 add_hotkey 会覆盖条目，第一个回调仍挂在钩子里删不掉，
    # 按键会同时触发两个功能。
    for other_type, other_key in _bound_hotkeys.items():
        if other_type != hotkey_type and other_key == normalized:
            print(f"[Hotkeys] Refusing duplicate hotkey: {hotkey_type} -> {normalized} "
                  f"(already bound to {other_type})")
            return

    old_key = _bound_hotkeys.get(hotkey_type)

    def callback():
        hotkey_signals.triggered.emit(hotkey_type)

    # 先注册新键、成功后再移除旧键：新组合非法/失败时用户原来的
    # 可用热键不会被先删掉（旧实现先 remove 再 add，失败即丢失）。
    try:
        keyboard.add_hotkey(normalized, callback, suppress=False)
    except Exception as e:
        print(f"[Hotkeys] Failed to bind global hotkey {hotkey_type} ({hotkey_str}): {e}")
        return
    if old_key and old_key != normalized:
        try:
            keyboard.remove_hotkey(old_key)
        except Exception:
            pass
    _bound_hotkeys[hotkey_type] = normalized
    print(f"[Hotkeys] Bound global hotkey: {hotkey_type} -> {normalized}")

def bind_mouse_hotkey(hotkey_type: str, hotkey_str: str):
    """Register a system-wide mouse-button hotkey (e.g. "X1", "MiddleButton").

    The click is not suppressed, so the app under the cursor still receives
    it. Emits the same ``hotkey_signals.triggered`` channel as keyboard
    hotkeys, so the main window handles both uniformly.
    """
    button = _MOUSE_BUTTON_NAMES.get(hotkey_str)
    if button is None:
        return

    for other_type, other_str in _bound_mouse_names.items():
        if other_type != hotkey_type and other_str == hotkey_str:
            print(f"[Hotkeys] Refusing duplicate mouse hotkey: {hotkey_type} -> {hotkey_str} "
                  f"(already bound to {other_type})")
            return

    old_handler = _bound_mouse_hotkeys.get(hotkey_type)

    def callback(*_args):
        hotkey_signals.triggered.emit(hotkey_type)

    try:
        handler = _mouse.on_button(callback, buttons=(button,), types=(_mouse.DOWN,))
    except Exception as e:
        print(f"[Hotkeys] Failed to bind mouse hotkey {hotkey_type} ({hotkey_str}): {e}")
        return
    if old_handler is not None:
        try:
            _mouse.unhook(old_handler)
        except Exception:
            pass
    _bound_mouse_hotkeys[hotkey_type] = handler
    _bound_mouse_names[hotkey_type] = hotkey_str
    print(f"[Hotkeys] Bound global mouse hotkey: {hotkey_type} -> {hotkey_str}")

def unbind_all():
    keyboard.unhook_all()
    for handler in list(_bound_mouse_hotkeys.values()):
        try:
            _mouse.unhook(handler)
        except Exception:
            pass
    _bound_mouse_hotkeys.clear()
    _bound_mouse_names.clear()
    _bound_hotkeys.clear()
    print("[Hotkeys] Unbound all global hotkeys")
