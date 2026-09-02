"""Main-window title bar (drag handle + window controls + quick toggles)."""

from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from core import config, i18n

if TYPE_CHECKING:
    from ui.main_window import MainWindow


def _visible_title_bar_height(title_bar) -> int:
    """Title-bar height used for content sizing; 0 while the bar is hidden."""
    if not title_bar.isVisible():
        return 0
    height = title_bar.height()
    if isinstance(height, int):
        return height
    try:
        return int(title_bar.sizeHint().height())
    except (TypeError, ValueError):
        return 0


def _title_bar_content_offset(title_bar, main_layout) -> int:
    """Top offset below the title bar, including the window's top border.

    A flush title bar leaves the layout's top margin at 0, so this matches the
    old behaviour there. Border themes that inset the title bar (the frame
    wraps above it) keep a top margin, and content must clear both bands.
    """
    top_margin = 0
    if main_layout is not None:
        try:
            top_margin = int(main_layout.contentsMargins().top())
        except (AttributeError, TypeError, ValueError):
            top_margin = 0
    if not title_bar.isVisible():
        return top_margin
    return top_margin + _visible_title_bar_height(title_bar)


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = cast("MainWindow", parent)
        self.drag_position = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.init_ui()

    def _show_context_menu(self, pos):
        """Quick toggles for the most-used settings."""
        p = self._parent
        menu = QMenu(self)

        act_follow = menu.addAction(i18n.tr("跟随鼠标"))
        if act_follow is not None:
            act_follow.setCheckable(True)
            act_follow.setChecked(cast(bool, p.cfg.get("followMouseEnabled", False)))
            act_follow.triggered.connect(lambda checked: self._toggle_follow_mouse(checked))

        act_no_focus = menu.addAction(i18n.tr("无焦点取色模式"))
        if act_no_focus is not None:
            act_no_focus.setCheckable(True)
            act_no_focus.setChecked(cast(bool, p.cfg.get("noFocusMode", False)))
            act_no_focus.triggered.connect(lambda checked: self._toggle_no_focus(checked))

        menu.addSeparator()
        act_settings = menu.addAction(i18n.tr("打开设置"))
        if act_settings is not None:
            act_settings.triggered.connect(p.toggle_settings_sidebar)
        menu.exec(self.mapToGlobal(pos))

    def _toggle_follow_mouse(self, checked):
        p = self._parent
        p.follow_mouse_active = checked
        p.cfg["followMouseEnabled"] = checked
        config.save_hotkey_config(p.cfg)
        if checked and p.isVisible():
            p.show_window_at_cursor()
        sidebar = getattr(p, "settings_sidebar", None)
        if sidebar is not None and sidebar.isVisible():
            sidebar.cfg["followMouseEnabled"] = checked
            sidebar.cb_follow_mouse.blockSignals(True)
            sidebar.cb_follow_mouse.setChecked(checked)
            sidebar.cb_follow_mouse.blockSignals(False)
            sidebar._persist_config()

    def _toggle_no_focus(self, checked):
        p = self._parent
        p.cfg["noFocusMode"] = checked
        config.save_hotkey_config(p.cfg)
        p.update_window_flags()
        p.update_no_focus_policies()
        sidebar = getattr(p, "settings_sidebar", None)
        if sidebar is not None and sidebar.isVisible():
            sidebar.cfg["noFocusMode"] = checked
            sidebar.cb_no_focus.blockSignals(True)
            sidebar.cb_no_focus.setChecked(checked)
            sidebar.cb_no_focus.blockSignals(False)
            sidebar._persist_config()

    def init_ui(self):
        self.setFixedHeight(28)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        # Settings Button (Hamburger)
        self.btn_settings = QPushButton("☰")
        self.btn_settings.setFixedSize(9, 9)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        # Never keep keyboard focus — otherwise Space (the default LAB-toggle
        # hotkey) would re-click the focused button and toggle the settings.
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 7px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
                border-radius: 2px;
            }
        """)

        # Panel grips toggle — the entry point to the whole panel system.
        # Next to the hamburger on purpose: arranging panels is something you
        # reach for while looking at the window, not while reading settings.
        self.btn_panels = QPushButton("▤")
        self.btn_panels.setFixedSize(9, 9)
        self.btn_panels.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_panels.setCheckable(True)
        self.btn_panels.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_panels.setToolTip(
            "面板抓手：开启后每块面板多一条把手，可拖动重排、拖出成独立窗口，"
            "右键还有更多操作")
        self.btn_panels.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 7px;
            }
            QPushButton:checked {
                background-color: rgba(255,255,255,0.20);
                border-radius: 2px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
                border-radius: 2px;
            }
        """)
        self.btn_panels.clicked.connect(self._toggle_panel_grips)

        # Title
        self.title_label = QLabel("Colorink")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 7px;")

        # Minimize Button
        self.btn_min = QPushButton("—")
        self.btn_min.setFixedSize(9, 9)
        self.btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_min.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_min.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 6px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
                border-radius: 2px;
            }
        """)
        self.btn_min.clicked.connect(self._parent.showMinimized)

        # Close Button
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(9, 9)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 8px;
            }
            QPushButton:hover {
                background-color: #ff5050;
                color: white;
                border-radius: 2px;
            }
        """)

        layout.addWidget(self.btn_settings)
        layout.addWidget(self.btn_panels)
        layout.addStretch()
        layout.addWidget(self.title_label)
        layout.addStretch()
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_close)

    def _toggle_panel_grips(self):
        """Flip the panel grips on the window this title bar belongs to."""
        window = self._parent
        cfg = getattr(window, "cfg", None)
        if cfg is None:
            return
        cfg["panelDrag"] = bool(self.btn_panels.isChecked())
        config.save_hotkey_config(cfg)
        refresh = getattr(window, "refresh_slider_visibility_and_order", None)
        if callable(refresh):
            refresh()
        sidebar = getattr(window, "settings_sidebar", None)
        checkbox = getattr(sidebar, "cb_panel_drag", None) if sidebar else None
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(cfg["panelDrag"])
            checkbox.blockSignals(False)

    def sync_panel_grips(self, enabled: bool) -> None:
        """Reflect the current setting (the settings page can change it too)."""
        self.btn_panels.blockSignals(True)
        self.btn_panels.setChecked(bool(enabled))
        self.btn_panels.blockSignals(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._parent.cfg.get("lockWindowPosition", False):
                self.drag_position = event.globalPosition().toPoint() - self._parent.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_position is not None:
            if not self._parent.cfg.get("lockWindowPosition", False):
                self._parent.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = None
