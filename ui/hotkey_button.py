"""Hotkey capture button shared by the settings UI."""

from PyQt6.QtWidgets import QPushButton
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import Qt, pyqtSignal


class HotkeyButton(QPushButton):
    """Push button that captures the next key press as a hotkey string."""

    hotkeyChanged = pyqtSignal(str)

    def __init__(self, hotkey_type, initial_val, parent=None):
        super().__init__(parent)
        self.hotkey_type = hotkey_type
        self.val = initial_val
        self.setText(initial_val if initial_val else "未绑定")
        self.waiting_for_key = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Keep a comfortable minimum width for hotkey labels.
        self.setMinimumWidth(90)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.waiting_for_key = True
            self.setText("请按键盘...")
            self.grabKeyboard()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.waiting_for_key:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.waiting_for_key = False
            self.setText(self.val if self.val else "未绑定")
            self.releaseKeyboard()
            return

        # Parse modifiers
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
            return

        if key_str:
            parts.append(key_str)

        hotkey = "+".join(parts)
        self.val = hotkey
        self.setText(hotkey)
        self.waiting_for_key = False
        self.releaseKeyboard()
        self.hotkeyChanged.emit(hotkey)
