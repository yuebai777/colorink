"""Independent top-level settings window for Colorink.

Hosts the existing SettingsSidebar inside a frameless stay-on-top dialog
so the user can tweak settings while watching the main picker react in
real time — no more overlay blocking the main window.
"""

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SettingsWindow(QDialog):
    """Frameless, always-on-top dialog wrapping SettingsSidebar.

    The sidebar instance is shared with the main window: main_window
    still holds ``self.settings_sidebar`` so existing connections and
    ``isVisible()`` checks continue to work unchanged.
    """

    def __init__(self, main_window, sidebar):
        super().__init__(None)  # no Qt parent — independent window
        self._main_window = main_window
        self.sidebar = sidebar
        self._drag_offset: QPoint | None = None
        self._was_visible_before_pick: bool = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Rail (96px) + gap + content panel
        self.setFixedWidth(460)

        self._build_ui()
        self._connect_signals()
        self._apply_window_theme()
        self._apply_fixed_size()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Custom title bar ──
        self._title_bar = _TitleBar(self)
        layout.addWidget(self._title_bar)

        # ── Settings sidebar (the real, existing instance) ──
        layout.addWidget(self.sidebar)
        # The sidebar was explicitly hidden while living on the main window
        # (setVisible(False) at init). addWidget() preserves that explicit
        # state, so re-show it once — parent show/hide then tracks normally.
        self.sidebar.show()

    # ── Signal wiring ─────────────────────────────────────────────────────

    def _connect_signals(self):
        s = self.sidebar
        # NOTE: main_window already connects s.settingChanged → on_settings_saved
        # at init_ui time (same sidebar instance), so changes keep applying live
        # without a duplicate connection here.
        # Re-theme the settings window itself when settings change
        s.settingChanged.connect(self._apply_window_theme)
        # Hide during eyedropper theme-point pick, restore after
        s.pickingThemePoint.connect(self._on_picking_theme_point)

    # ── Sizing ────────────────────────────────────────────────────────────

    def _apply_fixed_size(self):
        """Fixed window height: no per-page adaptation.

        Sized once from the tallest page's content (capped at a compact
        height); pages that exceed it scroll internally.
        """
        try:
            screen = QApplication.screenAt(self.pos())
            if screen is None:
                screen = QApplication.primaryScreen()
        except Exception:
            screen = QApplication.primaryScreen()
        max_h = 560
        if screen is not None:
            max_h = min(560, screen.availableGeometry().height() - 40)

        content_hint = 0
        for i in range(self.sidebar.stack.count()):
            page_widget = self.sidebar.stack.widget(i)
            if isinstance(page_widget, QScrollArea):
                page = page_widget.widget()
                if page is not None:
                    content_hint = max(content_hint, page.sizeHint().height())
        # 8px sidebar layout margins above and below the content
        content_h = self._title_bar.height() + 8 + content_hint + 8
        self.setFixedHeight(min(max_h, max(content_h, 320)))

    def _on_picking_theme_point(self, active: bool):
        if active:
            self._was_visible_before_pick = self.isVisible()
            self.hide()
        else:
            if self._was_visible_before_pick:
                self.show()
                self.raise_()

    # ── Theme ─────────────────────────────────────────────────────────────

    def _apply_window_theme(self):
        # Refresh the sidebar's stylesheet — main_window.apply_theme() no longer
        # overwrites it, so this is the single place that re-themes the sidebar.
        self.sidebar.apply_theme()
        c = self.sidebar.theme_colors()
        bg = c["bg"]
        text = c["text"]
        bar_bg = c["bar_bg"]
        border = c["border"]

        font_factor = self.sidebar.cfg.get("fontSize", 100) / 100.0
        font_size = int(11 * font_factor)

        self.setStyleSheet(f"""
            SettingsWindow {{
                background-color: {bar_bg};
            }}
            QWidget {{
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {font_size}px;
            }}
        """)
        self._title_bar.apply_theme(bg, text, border, bar_bg)

    # ── Positioning ───────────────────────────────────────────────────────

    def show_near_main_window(self):
        """Show the settings window beside the main window, clamping to screen."""
        mw = self._main_window
        if hasattr(self.sidebar, "nav") and hasattr(self.sidebar, "_last_settings_tab"):
            self.sidebar.nav.setCurrentRow(self.sidebar._last_settings_tab)
        if mw.isVisible():
            mw_geo = mw.frameGeometry()
            target_x = mw_geo.right() + 8
            target_y = mw_geo.top()

            # If the right side is off-screen, place on the left
            try:
                screen = QApplication.screenAt(mw_geo.center())
                if screen is None:
                    screen = QApplication.primaryScreen()
            except Exception:
                screen = QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                if target_x + self.width() > avail.right():
                    target_x = mw_geo.left() - self.width() - 8
                # Clamp to available geometry
                target_x = max(avail.left(), min(target_x, avail.right() - self.width()))
                target_y = max(avail.top(), min(target_y, avail.bottom() - self.height()))
        else:
            # Main window hidden — keep current position
            target_x = self.x()
            target_y = self.y()

        self.move(target_x, target_y)
        self._apply_fixed_size()
        self.show()
        self.raise_()
        self.activateWindow()

    def position_near_main_window(self):
        """Reposition without showing (called from main window resizeEvent)."""
        if not self.isVisible():
            return
        mw = self._main_window
        if not mw.isVisible():
            return
        mw_geo = mw.frameGeometry()
        target_x = mw_geo.right() + 8
        target_y = mw_geo.top()

        try:
            screen = QApplication.screenAt(mw_geo.center())
            if screen is None:
                screen = QApplication.primaryScreen()
        except Exception:
            screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if target_x + self.width() > avail.right():
                target_x = mw_geo.left() - self.width() - 8
            target_x = max(avail.left(), min(target_x, avail.right() - self.width()))
            target_y = max(avail.top(), min(target_y, avail.bottom() - self.height()))

        self.move(target_x, target_y)


class _TitleBar(QWidget):
    """Drag handle + label + close button for the settings window."""

    def __init__(self, settings_window: SettingsWindow):
        super().__init__(settings_window)
        self._sw = settings_window
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        # Title label
        self._label = QLabel("设置")
        font = self._label.font()
        font.setBold(True)
        self._label.setFont(font)

        # Close button
        self._btn_close = QPushButton("×")
        self._btn_close.setObjectName("SettingsCloseButton")
        self._btn_close.setFixedSize(20, 20)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self._sw.hide)

        layout.addWidget(self._label)
        layout.addStretch()
        layout.addWidget(self._btn_close)

    def apply_theme(self, bg: str, text: str, border: str, bar_bg: str):
        tc = QColor(text)
        divider = f"rgba({tc.red()},{tc.green()},{tc.blue()},0.14)"
        self.setStyleSheet(f"""
            _TitleBar {{
                background-color: {bar_bg};
                border-bottom: 1px solid {divider};
            }}
            QLabel {{
                color: {text};
                background: transparent;
            }}
            QPushButton#SettingsCloseButton {{
                background: transparent;
                border: none;
                color: {text};
                font-size: 14px;
                border-radius: 3px;
            }}
            QPushButton#SettingsCloseButton:hover {{
                background-color: #ff5050;
                color: white;
                border-radius: 3px;
            }}
            QPushButton#SettingsCloseButton:pressed {{
                background-color: #cc4040;
            }}
        """)

    def mousePressEvent(self, a0):
        if a0 is None:
            return
        if a0.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = a0.globalPosition().toPoint() - self._sw.frameGeometry().topLeft()
            a0.accept()

    def mouseMoveEvent(self, a0):
        if a0 is None:
            return
        if a0.buttons() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._sw.move(a0.globalPosition().toPoint() - self._drag_offset)
            a0.accept()

    def mouseReleaseEvent(self, a0):
        if a0 is None:
            return
        self._drag_offset = None
