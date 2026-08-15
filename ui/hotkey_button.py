"""Hotkey capture button shared by the settings UI.

Supports keyboard keys and (optionally) mouse buttons as shortcut values.
Canonical mouse-button names live in ``MOUSE_BUTTON_NAME_BY_QT`` — the
single source of truth shared by the capture button (ui.settings_sidebar),
the in-app LAB-toggle shortcut (ui.main_window), and the hotkey-binding
guard in ``update_hotkey_bindings``.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QPushButton


# ── Canonical mouse-button hotkey names ────────────────────────────────

MOUSE_BUTTON_NAME_BY_QT = {
    Qt.MouseButton.LeftButton: "LeftButton",
    Qt.MouseButton.RightButton: "RightButton",
    Qt.MouseButton.MiddleButton: "MiddleButton",
    Qt.MouseButton.XButton1: "X1",
    Qt.MouseButton.XButton2: "X2",
}

MOUSE_BUTTON_DISPLAY = {
    "LeftButton": "鼠标左键",
    "RightButton": "鼠标右键",
    "MiddleButton": "鼠标中键",
    "X1": "鼠标侧键1",
    "X2": "鼠标侧键2",
}


def is_mouse_hotkey(hotkey: str) -> bool:
    """True when the value is a canonical mouse-button hotkey name."""
    return hotkey in MOUSE_BUTTON_DISPLAY


def display_hotkey(hotkey: str) -> str:
    """Human-readable label for a stored hotkey value."""
    return MOUSE_BUTTON_DISPLAY.get(hotkey, hotkey)


# ── Global capture state ───────────────────────────────────────────────

_capture_active = False


def capture_active() -> bool:
    """True while any HotkeyButton is waiting for a key/button press.

    The main-window event filter skips its LAB-toggle handling during
    capture, so a mouse press over the color wheel gets recorded instead of
    toggling the view (grabMouse redirects presses from anywhere).
    """
    return _capture_active


def _set_capture_active(active: bool):
    global _capture_active
    _capture_active = active


def parse_key_event(event: QKeyEvent) -> str:
    """Convert a QKeyEvent into a hotkey string like "Ctrl+Shift+F5".

    Returns "" for a standalone modifier press or an unhandled key — nothing
    to bind. Shared by the settings capture button and the in-app LAB
    toggle shortcut (ui.main_window).
    """
    key = event.key()
    modifiers = event.modifiers()
    parts = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        parts.append("Ctrl")
    if modifiers & Qt.KeyboardModifier.AltModifier:
        parts.append("Alt")
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        parts.append("Shift")

    # Parse main key
    key_str = ""
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
        key_str = f"F{key - Qt.Key.Key_F1 + 1}"
    elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        key_str = chr(key)
    elif key == Qt.Key.Key_Space:
        key_str = "Space"
    elif key == Qt.Key.Key_Tab:
        key_str = "Tab"
    elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
        key_str = "Enter"
    elif key == Qt.Key.Key_Backspace:
        key_str = "Backspace"
    elif key == Qt.Key.Key_Delete:
        key_str = "Delete"
    elif key == Qt.Key.Key_Left:
        key_str = "Left"
    elif key == Qt.Key.Key_Right:
        key_str = "Right"
    elif key == Qt.Key.Key_Up:
        key_str = "Up"
    elif key == Qt.Key.Key_Down:
        key_str = "Down"
    elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        key_str = chr(key)

    # Ignore standalone modifier press
    if not key_str and key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
        return ""

    if key_str:
        parts.append(key_str)

    return "+".join(parts)


class HotkeyButton(QPushButton):
    """Push button that captures the next key/button press as a hotkey string."""

    hotkeyChanged = pyqtSignal(str)

    def __init__(self, hotkey_type, initial_val, parent=None, allow_mouse=False):
        super().__init__(parent)
        self.hotkey_type = hotkey_type
        self.allow_mouse = allow_mouse
        self.val = initial_val
        from core import i18n
        self.setText(i18n.tr(display_hotkey(initial_val)) if initial_val else i18n.tr("未绑定"))
        self.waiting_for_key = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Keep a comfortable minimum width for hotkey labels.
        self.setMinimumWidth(90)

    def mousePressEvent(self, event):
        if self.waiting_for_key:
            # Capturing: record the pressed mouse button (when allowed) or
            # keep waiting for a keyboard key.
            if self.allow_mouse:
                name = MOUSE_BUTTON_NAME_BY_QT.get(event.button())
                if name:
                    self._finish_capture(name)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.waiting_for_key = True
            _set_capture_active(True)
            from core import i18n
            self.setText(i18n.tr("请按键盘或鼠标键...") if self.allow_mouse else i18n.tr("请按键盘..."))
            self.grabKeyboard()
            self.grabMouse()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.waiting_for_key:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel_capture()
            return

        hotkey = parse_key_event(event)
        # 独立修饰键 / 不支持的键（Insert/Home/End/PrtSc/F13+ 等）：
        # 继续等待下一个按键，绝不 _finish_capture("")——否则会把已绑定的
        # 热键静默清空并写进配置，重启后热键丢失。
        if not hotkey:
            return

        self._finish_capture(hotkey)

    def _finish_capture(self, hotkey: str):
        self.val = hotkey
        self.setText(display_hotkey(hotkey))
        self.waiting_for_key = False
        _set_capture_active(False)
        self.releaseKeyboard()
        self.releaseMouse()
        self.hotkeyChanged.emit(hotkey)

    def _cancel_capture(self):
        self.waiting_for_key = False
        _set_capture_active(False)
        from core import i18n
        self.setText(i18n.tr(display_hotkey(self.val)) if self.val else i18n.tr("未绑定"))
        self.releaseKeyboard()
        self.releaseMouse()
