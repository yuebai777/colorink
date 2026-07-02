import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
                             QPushButton, QCheckBox, QComboBox, QFileDialog, QScrollArea, QWidget)
from PyQt6.QtGui import QKeyEvent, QColor
from PyQt6.QtCore import Qt, pyqtSignal

from core import config
from core import autostart
from core import global_hotkeys

class HotkeyButton(QPushButton):
    hotkeyChanged = pyqtSignal(str)

    def __init__(self, hotkey_type, initial_val, parent=None):
        super().__init__(parent)
        self.hotkey_type = hotkey_type
        self.val = initial_val
        self.setText(initial_val if initial_val else "未绑定")
        self.waiting_for_key = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("""
            QPushButton {
                background-color: var(--input-bg);
                border: 1px solid var(--border-color);
                color: var(--text-color);
                border-radius: 3px;
                padding: 4px 8px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: var(--hover-bg);
            }
        """)

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

class SettingsDialog(QDialog):
    configSaved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(420, 520)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        
        self.cfg = config.load_hotkey_config()
        self.init_ui()
        self.apply_theme()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)

        # Section: Hotkeys
        layout.addWidget(QLabel("<b>全局快捷键</b>"))
        hotkey_grid = QGridLayout()
        hotkey_grid.setSpacing(8)
        
        self.btn_pick = HotkeyButton("pickKey", self.cfg.get("pickKey", "F11"))
        self.btn_inject = HotkeyButton("injectionKey", self.cfg.get("injectionKey", "F12"))
        self.btn_hide = HotkeyButton("hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+H"))
        self.btn_follow = HotkeyButton("followMouseKey", self.cfg.get("followMouseKey", "Ctrl+R"))
        self.btn_grayscale = HotkeyButton("grayscaleFilterKey", self.cfg.get("grayscaleFilterKey", "Ctrl+G"))
        
        hotkey_grid.addWidget(QLabel("触发吸色:"), 0, 0)
        hotkey_grid.addWidget(self.btn_pick, 0, 1)
        hotkey_grid.addWidget(QLabel("注入画笔颜色:"), 1, 0)
        hotkey_grid.addWidget(self.btn_inject, 1, 1)
        hotkey_grid.addWidget(QLabel("显示/隐藏面板:"), 2, 0)
        hotkey_grid.addWidget(self.btn_hide, 2, 1)
        hotkey_grid.addWidget(QLabel("开关跟随鼠标:"), 3, 0)
        hotkey_grid.addWidget(self.btn_follow, 3, 1)
        hotkey_grid.addWidget(QLabel("黑白滤镜开关:"), 4, 0)
        hotkey_grid.addWidget(self.btn_grayscale, 4, 1)
        layout.addLayout(hotkey_grid)

        # Grayscale filter screen selector
        screen_row = QHBoxLayout()
        screen_row.addWidget(QLabel("滤镜目标屏幕:"))
        self.combo_grayscale_screen = QComboBox()
        from ui.grayscale_overlay import GrayscaleOverlay
        self.combo_grayscale_screen.addItems(GrayscaleOverlay.available_screens())
        saved_target = self.cfg.get("grayscaleFilterScreen", "all")
        if saved_target == "all":
            self.combo_grayscale_screen.setCurrentText("all")
        else:
            for i in range(self.combo_grayscale_screen.count()):
                item = self.combo_grayscale_screen.itemText(i)
                if item != "all" and item.startswith(f"{saved_target}:"):
                    self.combo_grayscale_screen.setCurrentText(item)
                    break
        screen_row.addWidget(self.combo_grayscale_screen)
        layout.addLayout(screen_row)

        # Grayscale filter mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("黑白模式:"))
        self.combo_grayscale_mode = QComboBox()
        self.combo_grayscale_mode.addItems(["OKLCh (感知均匀)", "Luma (BT.709 标准)"])
        mode_value = self.cfg.get("grayscaleFilterMode", "oklch")
        self.combo_grayscale_mode.setCurrentIndex(1 if mode_value == "luma" else 0)
        mode_row.addWidget(self.combo_grayscale_mode)
        layout.addLayout(mode_row)

        # Section: Switches
        layout.addWidget(QLabel("<b>功能开关</b>"))
        
        self.cb_picking_enabled = QCheckBox("启用屏幕吸色")
        self.cb_picking_enabled.setChecked(self.cfg.get("colorPickingEnabled", True))
        layout.addWidget(self.cb_picking_enabled)
        
        self.cb_auto_click = QCheckBox("CSP/UD 注入后自动模拟点击 (使绘图区更新)")
        self.cb_auto_click.setChecked(self.cfg.get("cspAutoClick", True))
        layout.addWidget(self.cb_auto_click)
        
        self.cb_follow_mouse = QCheckBox("开启跟随鼠标")
        self.cb_follow_mouse.setChecked(self.cfg.get("followMouseEnabled", False))
        layout.addWidget(self.cb_follow_mouse)
        
        self.cb_only_drawing = QCheckBox("只在绘图软件活动时显示 (悬浮自适应)")
        self.cb_only_drawing.setChecked(self.cfg.get("onlyShowInCsp", False))
        layout.addWidget(self.cb_only_drawing)
        
        self.cb_lock_size = QCheckBox("锁定面板大小")
        self.cb_lock_size.setChecked(self.cfg.get("lockWindowSize", False))
        layout.addWidget(self.cb_lock_size)
        
        self.cb_taskbar_icon = QCheckBox("在任务栏显示图标")
        self.cb_taskbar_icon.setChecked(self.cfg.get("showTaskbarIcon", False))
        layout.addWidget(self.cb_taskbar_icon)
        
        self.cb_autostart = QCheckBox("开机自启动 (管理员权限免 UAC)")
        self.cb_autostart.setChecked(self.cfg.get("openAtLogin", False))
        layout.addWidget(self.cb_autostart)

        # Section: Dropdowns / Software version
        layout.addWidget(QLabel("<b>软件同步设置</b>"))
        sync_grid = QGridLayout()
        sync_grid.setSpacing(8)
        
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["auto", "light", "dark"])
        self.combo_theme.setCurrentText(self.cfg.get("ui-theme", "auto"))
        
        self.combo_csp = QComboBox()
        self.combo_csp.addItems(["auto", "csp4.0", "csp4.2.7-ex", "csp5.0", "csp5.0-ex"])
        self.combo_csp.setCurrentText(self.cfg.get("cspVersion", "auto"))
        
        self.combo_sai = QComboBox()
        self.combo_sai.addItems(["auto", "pre-2024-sai2", "after-2024-sai2"])
        self.combo_sai.setCurrentText(self.cfg.get("sai2Version", "auto"))
        
        self.combo_udm = QComboBox()
        self.combo_udm.addItems(["auto"])
        self.combo_udm.setCurrentText(self.cfg.get("udmVersion", "auto"))
        
        sync_grid.addWidget(QLabel("UI 主题:"), 0, 0)
        sync_grid.addWidget(self.combo_theme, 0, 1)
        sync_grid.addWidget(QLabel("CSP 版本:"), 1, 0)
        sync_grid.addWidget(self.combo_csp, 1, 1)
        sync_grid.addWidget(QLabel("SAI2 版本:"), 2, 0)
        sync_grid.addWidget(self.combo_sai, 2, 1)
        sync_grid.addWidget(QLabel("UDM 版本:"), 3, 0)
        sync_grid.addWidget(self.combo_udm, 3, 1)
        layout.addLayout(sync_grid)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_save = QPushButton("保存")
        btn_cancel = QPushButton("取消")
        btn_save.clicked.connect(self.save_config)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        main_layout.addLayout(btn_layout)

    def apply_theme(self):
        # Premium styling matching CLIP Studio Paint
        # We define CSS variables locally using style sheets
        self.setStyleSheet("""
            QWidget {
                background-color: #2e2e2e;
                color: #d5d5d5;
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: 11px;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QLabel {
                color: #e0e0e0;
            }
            QCheckBox {
                color: #d5d5d5;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #555;
                background-color: #1e1e1e;
                border-radius: 2px;
            }
            QCheckBox::indicator:checked {
                background-color: #5a94e2;
                border-color: #5a94e2;
            }
            QComboBox {
                background-color: #1e1e1e;
                border: 1px solid #555;
                color: #d5d5d5;
                border-radius: 3px;
                padding: 3px 6px;
                min-width: 120px;
            }
            QComboBox:hover {
                border-color: #5a94e2;
            }
            QComboBox::drop-down {
                border: none;
            }
            QPushButton {
                background-color: #3e3e3e;
                border: 1px solid #555;
                color: #e0e0e0;
                border-radius: 3px;
                padding: 5px 15px;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #4e4e4e;
                border-color: #666;
            }
        """)

    def save_config(self):
        # Update config fields
        self.cfg["pickKey"] = self.btn_pick.val
        self.cfg["injectionKey"] = self.btn_inject.val
        self.cfg["hideWindowKey"] = self.btn_hide.val
        self.cfg["followMouseKey"] = self.btn_follow.val
        self.cfg["grayscaleFilterKey"] = self.btn_grayscale.val
        screen_text = self.combo_grayscale_screen.currentText()
        self.cfg["grayscaleFilterScreen"] = screen_text.split(":")[0].strip() if ":" in screen_text else screen_text
        mode_text = self.combo_grayscale_mode.currentText()
        self.cfg["grayscaleFilterMode"] = "luma" if "Luma" in mode_text else "oklch"
        
        self.cfg["colorPickingEnabled"] = self.cb_picking_enabled.isChecked()
        self.cfg["cspAutoClick"] = self.cb_auto_click.isChecked()
        self.cfg["followMouseEnabled"] = self.cb_follow_mouse.isChecked()
        self.cfg["onlyShowInCsp"] = self.cb_only_drawing.isChecked()
        self.cfg["lockWindowSize"] = self.cb_lock_size.isChecked()
        self.cfg["showTaskbarIcon"] = self.cb_taskbar_icon.isChecked()
        
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = self.cb_autostart.isChecked()
        self.cfg["openAtLogin"] = new_autostart
        
        self.cfg["ui-theme"] = self.combo_theme.currentText()
        self.cfg["cspVersion"] = self.combo_csp.currentText()
        self.cfg["sai2Version"] = self.combo_sai.currentText()
        self.cfg["udmVersion"] = self.combo_udm.currentText()

        # Save to file
        config.save_hotkey_config(self.cfg)
        
        # Apply autostart change if needed
        if old_autostart != new_autostart:
            autostart.apply_autostart(new_autostart)
            
        self.configSaved.emit()
        self.accept()
