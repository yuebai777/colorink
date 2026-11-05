"""交互式区域灰度框选遮罩组件.

允许用户在屏幕上拖拽框选任意矩形作为区域灰度滤镜的生效范围，
支持双击或按空格键快速恢复默认最大化，右键或按 ESC 取消。
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QApplication, QWidget

from core.foreground import focus_guard
from core.i18n import tr

try:
    import win32api
except Exception:  # pragma: no cover
    win32api = None


class RegionGrayscaleSelector(QWidget):
    """全屏半透明框选遮罩，输出物理桌面像素坐标 (x, y, width, height)."""

    regionSelected = pyqtSignal(int, int, int, int)
    regionReset = pyqtSignal()
    canceled = pyqtSignal()

    MIN_REGION_SIZE = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._is_selecting = False
        self._dragging = False
        self._start_local = QPoint()
        self._current_local = QPoint()
        self._start_phys: tuple[int, int] = (0, 0)
        self._current_phys: tuple[int, int] = (0, 0)

    @staticmethod
    def _physical_cursor_pos(fallback_global: QPoint | None = None) -> tuple[int, int]:
        if win32api is not None:
            try:
                px, py = win32api.GetCursorPos()
                return int(px), int(py)
            except Exception:
                pass
        pt = fallback_global if fallback_global is not None else QCursor.pos()
        screen = QGuiApplication.screenAt(pt) or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        return int(round(pt.x() * dpr)), int(round(pt.y() * dpr))

    def start_selection(self) -> None:
        """显示全屏框选遮罩."""
        if self._is_selecting:
            return
        try:
            if not focus_guard.capture():
                focus_guard.capture_drawing_app_fallback()
        except Exception:
            pass
        self._is_selecting = True
        self._dragging = False

        # 覆盖所有显示器的虚拟桌面范围
        virtual_rect = QRect()
        app = QApplication.instance()
        if isinstance(app, QGuiApplication):
            for screen in app.screens():
                virtual_rect = virtual_rect.united(screen.geometry())
        if virtual_rect.isEmpty():
            primary = QGuiApplication.primaryScreen()
            virtual_rect = primary.geometry() if primary else QRect(0, 0, 1920, 1080)

        self.setGeometry(virtual_rect)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.update()

    def stop_selection(self) -> None:
        """关闭框选遮罩并恢复原前台窗口焦点."""
        if not self._is_selecting and not self.isVisible():
            return
        self._is_selecting = False
        self._dragging = False
        self.hide()
        try:
            focus_guard.restore()
        except Exception:
            pass

    def _current_local_rect(self) -> QRect:
        return QRect(self._start_local, self._current_local).normalized()

    def _current_physical_rect(self) -> tuple[int, int, int, int]:
        x1, y1 = self._start_phys
        x2, y2 = self._current_phys
        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        return left, top, width, height

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.stop_selection()
            self.canceled.emit()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_local = event.position().toPoint()
            self._current_local = self._start_local
            global_pt = event.globalPosition().toPoint()
            self._start_phys = self._physical_cursor_pos(global_pt)
            self._current_phys = self._start_phys
            self.update()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._current_local = event.position().toPoint()
            global_pt = event.globalPosition().toPoint()
            self._current_phys = self._physical_cursor_pos(global_pt)
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._current_local = event.position().toPoint()
            global_pt = event.globalPosition().toPoint()
            self._current_phys = self._physical_cursor_pos(global_pt)
            rx, ry, rw, rh = self._current_physical_rect()
            if rw >= self.MIN_REGION_SIZE and rh >= self.MIN_REGION_SIZE:
                self.stop_selection()
                self.regionSelected.emit(rx, ry, rw, rh)
            else:
                self.update()
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.stop_selection()
            self.regionReset.emit()
            event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.stop_selection()
            self.canceled.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.stop_selection()
            self.regionReset.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        full_rect = QRectF(self.rect())
        overlay_path = QPainterPath()
        overlay_path.addRect(full_rect)

        sel_rect = self._current_local_rect() if self._dragging else QRect()
        if not sel_rect.isEmpty() and sel_rect.width() > 2 and sel_rect.height() > 2:
            hole_path = QPainterPath()
            hole_path.addRect(QRectF(sel_rect))
            overlay_path = overlay_path.subtracted(hole_path)

        painter.fillPath(overlay_path, QColor(12, 16, 24, 115))

        # 绘制顶部操作提示胶囊
        hint_text = tr("拖拽框选灰度区域 · 双击或按空格恢复最大化 · 右键/ESC 取消")
        font = QFont("Microsoft YaHei UI", 10)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(hint_text) + 32
        th = fm.height() + 16
        primary = QGuiApplication.primaryScreen()
        scr_geo = primary.geometry() if primary else self.rect()
        local_center_x = scr_geo.center().x() - self.geometry().x()
        local_top_y = scr_geo.top() - self.geometry().y() + 36
        banner_rect = QRectF(local_center_x - tw / 2, local_top_y, tw, th)
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
        painter.setBrush(QColor(20, 24, 34, 210))
        painter.drawRoundedRect(banner_rect, 10.0, 10.0)
        painter.setPen(QColor(240, 244, 255))
        painter.drawText(banner_rect, Qt.AlignmentFlag.AlignCenter, hint_text)

        # 绘制选区边框与物理像素尺寸标牌
        if not sel_rect.isEmpty() and sel_rect.width() > 2 and sel_rect.height() > 2:
            pen = QPen(QColor(78, 168, 255, 235), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(sel_rect))

            rx, ry, rw, rh = self._current_physical_rect()
            badge_text = f"{rw} × {rh} px  ({rx}, {ry})"
            badge_font = QFont("Consolas", 9)
            badge_font.setBold(True)
            painter.setFont(badge_font)
            bfm = painter.fontMetrics()
            bw = bfm.horizontalAdvance(badge_text) + 18
            bh = bfm.height() + 10
            bx = float(sel_rect.left())
            by = float(sel_rect.top() - bh - 6)
            if by < 8:
                by = float(sel_rect.top() + 6)
            badge_rect = QRectF(bx, by, bw, bh)
            painter.setPen(QPen(QColor(78, 168, 255, 160), 1.0))
            painter.setBrush(QColor(18, 22, 32, 225))
            painter.drawRoundedRect(badge_rect, 6.0, 6.0)
            painter.setPen(QColor(235, 242, 255))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
