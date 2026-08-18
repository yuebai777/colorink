"""Shared widget/UI helpers for the settings sidebar.

Extracted from ``ui.settings_sidebar``: small non-scrolling controls, common
layout/card builders, rail-icon drawing and the CSP version option tables.
"""

import math
import os

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# Resolve resource paths relative to the repo root so packaged builds
# (PyInstaller) work regardless of the current working directory.
_ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "icons")
_CHECKBOX_CHECK_ICON = os.path.join(_ICONS_DIR, "checkbox_check.png").replace("\\", "/")
_ARROW_DOWN_DARK = os.path.join(_ICONS_DIR, "arrow_down_dark.png").replace("\\", "/")
_ARROW_DOWN_LIGHT = os.path.join(_ICONS_DIR, "arrow_down_light.png").replace("\\", "/")

# CSP 内存模式版本选项：显示文本 ↔ 配置存储值。前景/背景色与透明状态
# 同步（rgb_u32 槽布局）只有 csp5.1 支持；csp4.x / csp5.x 仅主色同步。
_CSP_VERSION_ITEMS: list[tuple[str, str]] = [
    ("auto", "auto（自动检测）"),
    ("csp4.x", "CSP 4.x（仅主色）"),
    ("csp5.x", "CSP 5.0（仅主色）"),
    ("csp5.1", "CSP 5.1（支持前景/背景/透明）"),
]
_CSP_DISPLAY_TO_VALUE = {disp: val for val, disp in _CSP_VERSION_ITEMS}
_CSP_VALUE_TO_DISPLAY = dict(_CSP_VERSION_ITEMS)
# 每项的悬停说明
_CSP_VERSION_TIPS: dict[str, str] = {
    "auto": "自动检测 CSP 主版本；检测为 5.1 时支持前景/背景色与透明同步，"
            "5.0 及以下仅主色同步。",
    "csp4.x": "CSP 4.x 内存模式仅支持主色同步；前景/背景色与透明同步需要 CSP 5.1。",
    "csp5.x": "CSP 5.0 内存模式仅支持主色同步；前景/背景色与透明同步需要 CSP 5.1。",
    "csp5.1": "CSP 5.1 内存模式支持前景/背景色与透明状态同步（推荐）。",
}


class NonScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NonScrollSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


class SettingsHelpersMixin:
    @staticmethod
    def _clear_layout(layout):
        """Recursively detach and schedule deletion of a layout's contents."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    SettingsHelpersMixin._clear_layout(sub)

    @staticmethod
    def _make_step_button(text, tooltip="", width=22):
        """Compact step/arrow button (-, +, ▲, ▼) with a uniform 22px hit area."""
        btn = QPushButton(text)
        btn.setObjectName("StepButton")
        btn.setFixedSize(width, 20)
        if tooltip:
            btn.setToolTip(tooltip)
        return btn

    @staticmethod
    def _set_label_state(lbl, state):
        """Theme-aware status coloring via objectName, repolished immediately.

        ``state`` is one of: None (default text color), "muted", "success",
        "warning", "danger" — the colors are defined in ``apply_theme``.
        """
        if state:
            lbl.setObjectName(f"Status{state.capitalize()}")
        else:
            lbl.setObjectName("")
        style = lbl.style()
        style.unpolish(lbl)
        style.polish(lbl)

    def _make_page(self, title):
        """Create a tab page with a QScrollArea and return its QVBoxLayout."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setSpacing(6)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(page)
        self.stack.addWidget(scroll)

        self._page_layouts[title] = page_layout
        return page_layout

    def _begin_card(self, page_layout, header_text):
        """Create a flat settings section with header, return (card, content_layout)."""
        card = QFrame()
        card.setObjectName("SettingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 1, 0, 1)
        card_layout.setSpacing(6)
        card_layout.addWidget(self.create_header(header_text))
        return card, card_layout

    def create_header(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("SectionHeader")
        return lbl

    # ── Rail icons ────────────────────────────────────────────────────────

    @staticmethod
    def _nav_icon(kind: str, color: str) -> QIcon:
        """Draw a crisp monochrome rail glyph with QPainter.

        ``kind`` is one of: hotkeys, interface, picker, filter, software, about.
        The canvas is 36×36 logical units on a 72×72 device pixmap
        (devicePixelRatio 2.0), so the painter coordinates below match
        the drawing code exactly while the glyph stays sharp on HiDPI.
        """
        pm = QPixmap(72, 72)
        pm.setDevicePixelRatio(2.0)  # logical 36×36 canvas → painter coords = drawing units
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(color)
        pen = QPen(c)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = 18.0, 18.0

        if kind == "hotkeys":
            # Keyboard
            p.drawRoundedRect(4, 8, 28, 16, 3, 3)
            p.setBrush(c)
            for row_y in (12.0, 19.0):
                for x in (8.0, 12.0, 16.0, 20.0, 24.0):
                    p.drawEllipse(QPointF(x, row_y), 1.2, 1.2)
        elif kind == "interface":
            # Monitor
            p.drawRoundedRect(4, 5, 28, 17, 2, 2)
            p.drawLine(QPointF(18, 22), QPointF(18, 27))
            p.drawLine(QPointF(11, 30), QPointF(25, 30))
        elif kind == "picker":
            # Color wheel (ring + inner ring + hue dot)
            p.drawEllipse(QPointF(cx, cy), 16, 16)
            p.drawEllipse(QPointF(cx, cy), 6, 6)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx - 11, cy), 2.1, 2.1)
        elif kind == "filter":
            # Sliders/filter: three horizontal lines with knobs
            p.drawLine(QPointF(6, 10), QPointF(30, 10))
            p.drawLine(QPointF(6, 18), QPointF(30, 18))
            p.drawLine(QPointF(6, 26), QPointF(30, 26))
            p.setBrush(c)
            p.drawEllipse(QPointF(12, 10), 2.2, 2.2)
            p.drawEllipse(QPointF(24, 18), 2.2, 2.2)
            p.drawEllipse(QPointF(18, 26), 2.2, 2.2)
        elif kind == "software":
            # Circular sync arrows
            r = 11.0
            p.drawArc(int(cx - r), int(cy - r), int(2 * r), int(2 * r), 225 * 16, -200 * 16)
            p.drawArc(int(cx - r), int(cy - r), int(2 * r), int(2 * r), 45 * 16, 200 * 16)
            p.setBrush(c)
            for start, sweep in ((225, -200), (45, 200)):
                end = math.radians(start + sweep)
                ex, ey = cx + r * math.cos(end), cy + r * math.sin(end)
                t = math.radians(start + sweep - 90)  # travel direction
                dx, dy = math.cos(t), math.sin(t)
                tip = QPointF(ex + dx * 6.0, ey + dy * 6.0)
                b1 = QPointF(ex - dy * 3.0, ey + dx * 3.0)
                b2 = QPointF(ex + dy * 3.0, ey - dx * 3.0)
                p.drawPolygon(QPolygonF([tip, b1, b2]))
        elif kind == "about":
            # Info circle
            p.drawEllipse(QPointF(cx, cy), 15, 15)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy - 7), 1.8, 1.8)
            p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 9))
        p.end()
        return QIcon(pm)

    def _refresh_nav_icons(self):
        """Re-render rail glyphs in the current theme (white when selected)."""
        if not hasattr(self, "nav"):
            return
        colors = self.theme_colors()
        text = colors["text"]
        selected = self.nav.currentRow()
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item is None:
                continue
            kind = item.data(Qt.ItemDataRole.UserRole) or "about"
            item.setIcon(self._nav_icon(kind, "#ffffff" if i == selected else text))
