import keyboard
import mouse as _mouse
from PyQt6.QtCore import QObject, pyqtSignal

# Zone gate for mouse-button hotkeys (taskbar / tray / shell flyouts fall
# through to the system). Imported defensively: if the module or pywin32 is
# missing, the gate degrades to "never filter" (fail-open).
try:
    from core import input_zones
except Exception:
    input_zones = None


class HotkeySignals(QObject):
    # Emits the configuration key name, e.g. "pickKey", "hideWindowKey", "followMouseKey"
    triggered = pyqtSignal(str)


hotkey_signals = HotkeySignals()


def get_hotkey_signals() -> HotkeySignals:
    global hotkey_signals
    try:
        hotkey_signals.objectName()
    except (RuntimeError, AttributeError):
        hotkey_signals = HotkeySignals()
    return hotkey_signals


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

# Module-level switch for the mouse-hotkey zone gate. Written by
# HotkeyMixin.update_hotkey_bindings() on every rebind (GUI thread), read by
# the mouse-library handler thread — a plain bool swap is atomic under the
# GIL, and reading it here avoids touching the disk config from the handler.
_zone_filter_enabled = True


def set_zone_filter_enabled(flag):
    """Enable/disable the shell-zone gate for mouse-button hotkeys.

    Called from the GUI thread on every hotkey rebind. Also kicks the
    background taskbar-rect refresh so the first gated click already has a
    snapshot to test against.
    """
    global _zone_filter_enabled
    _zone_filter_enabled = bool(flag)
    if _zone_filter_enabled and input_zones is not None:
        try:
            input_zones.start_background_refresh()
        except Exception:
            pass


def _zone_gate_active():
    """True when the zone gate should filter mouse-hotkey callbacks."""
    if not _zone_filter_enabled or input_zones is None:
        return False
    try:
        return input_zones.is_available()
    except Exception:
        return False

def bind_hotkey(hotkey_type: str, hotkey_str: str, force: bool = False):
    if not hotkey_str:
        return

    # Normalize shortcut (e.g. "Ctrl+Alt+J" -> "ctrl+alt+j")
    normalized = hotkey_str.lower().strip()

    # 拒绝同一组合绑定到两个功能：keyboard 库以组合名为字典键，
    # 第二次 add_hotkey 会覆盖条目，第一个回调仍挂在钩子里删不掉，
    # 按键会同时触发两个功能。
    # force=True 仅用于不占用户槽位的硬编码兜底热键（__openSettings）：
    # 即使与用户组合撞车也必须注册成功，撞车时两个回调都会触发（可接受的兜底代价）。
    for other_type, other_key in _bound_hotkeys.items():
        if other_type != hotkey_type and other_key == normalized:
            if not force:
                print(f"[Hotkeys] Refusing duplicate hotkey: {hotkey_type} -> {normalized} "
                      f"(already bound to {other_type})")
                return
            print(f"[Hotkeys] Warning: forced hotkey {hotkey_type} -> {normalized} "
                  f"collides with {other_type}; both callbacks may fire")

    old_key = _bound_hotkeys.get(hotkey_type)

    def callback():
        get_hotkey_signals().triggered.emit(hotkey_type)

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
        # Zone gate: over the taskbar / tray / shell flyouts the click falls
        # through to the system untouched (we simply don't react), so native
        # context menus are never covered by our always-on-top panel. Any
        # gate error fails open (hotkey fires as before).
        try:
            if _zone_gate_active() and input_zones.ignore_at_cursor():
                return
        except Exception:
            pass
        get_hotkey_signals().triggered.emit(hotkey_type)

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
