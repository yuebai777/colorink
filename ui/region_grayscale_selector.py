"""交互式多区域灰度框选遮罩组件.

允许用户在屏幕上拖拽框选一个或多个局部矩形作为区域灰度滤镜的生效范围，
支持拖动调整位置与四边/四角缩放、逐个删除、重新框选单个区域或恢复默认最大化。
"""
from __future__ import annotations

from dataclasses import dataclass

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
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from core.foreground import focus_guard
from core.i18n import tr

try:
    import win32api
except Exception:  # pragma: no cover
    win32api = None


@dataclass
class _MonitorMap:
    index: int
    phys_rect: tuple[int, int, int, int]  # (left, top, right, bottom)
    logical_geo: QRect
    dpr: float


def build_monitor_maps() -> list[_MonitorMap]:
    """构建物理桌面像素与 Qt 逻辑屏幕几何之间的映射."""
    from core.region_grayscale import enumerate_physical_monitor_rects

    app = QApplication.instance()
    screens = list(app.screens()) if isinstance(app, QGuiApplication) else []
    if not screens:
        return [_MonitorMap(0, (0, 0, 1920, 1080), QRect(0, 0, 1920, 1080), 1.0)]

    phys_rects = enumerate_physical_monitor_rects()
    maps: list[_MonitorMap] = []
    for idx, scr in enumerate(screens):
        geo = scr.geometry()
        dpr = max(0.5, float(scr.devicePixelRatio() or 1.0))
        if idx < len(phys_rects):
            px, py, pw, ph = phys_rects[idx]
            matched = (px, py, px + pw, py + ph)
        else:
            px = int(round(geo.x() * dpr))
            py = int(round(geo.y() * dpr))
            matched = (px, py, px + int(round(geo.width() * dpr)), py + int(round(geo.height() * dpr)))
        maps.append(_MonitorMap(idx, matched, geo, dpr))
    return maps


def describe_region_screens(rx: int, ry: int, rw: int, rh: int, tr_fn=None) -> str:
    """返回指定物理像素矩形所在的显示器描述."""
    _tr = tr_fn if callable(tr_fn) else tr
    maps = build_monitor_maps()
    hits: list[int] = []
    rx2 = rx + rw
    ry2 = ry + rh
    for m in maps:
        ml, mt, mr, mb = m.phys_rect
        if min(rx2, mr) > max(rx, ml) and min(ry2, mb) > max(ry, mt):
            hits.append(m.index)
    if not hits:
        return _tr("桌面外区域")
    if len(hits) == 1:
        return _tr("显示器 {idx}", idx=hits[0] + 1)
    return _tr("跨 {count} 个显示器", count=len(hits))


class RegionGrayscaleSelector(QWidget):
    """全屏半透明多区域框选遮罩，输出物理桌面像素坐标."""

    regionSelected = pyqtSignal(int, int, int, int)
    regionsSelected = pyqtSignal(list)
    regionReplaced = pyqtSignal(int, int, int, int, int)
    regionReset = pyqtSignal()
    canceled = pyqtSignal()

    MIN_REGION_SIZE = 20
    _HANDLE_TOL = 7
    _CLOSE_BTN_SIZE = 20

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
        self._monitors: list[_MonitorMap] = []
        self._regions: list[dict] = []  # [{"x", "y", "width", "height", "enabled": True, "name": ...}]
        self._guide_regions: list[tuple[int, int, int, int]] = []
        self._replace_index: int | None = None

        self._selected_idx: int | None = None
        self._hover_idx: int | None = None
        self._hover_close_idx: int | None = None

        # _drag_mode: None | "draw" | "move" | "resize"
        self._drag_mode: str | None = None
        self._drag_region_idx: int | None = None
        self._drag_resize_edges: str = ""  # combination of 'l', 'r', 't', 'b'
        self._drag_orig_phys: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._start_local = QPoint()
        self._current_local = QPoint()
        self._start_phys: tuple[int, int] = (0, 0)
        self._current_phys: tuple[int, int] = (0, 0)

        self._build_toolbar()

    def _build_toolbar(self) -> None:
        self._toolbar = QFrame(self)
        self._toolbar.setObjectName("SelectorToolbar")
        self._toolbar.setStyleSheet("""
            QFrame#SelectorToolbar {
                background-color: rgba(18, 22, 32, 232);
                border: 1px solid rgba(255, 255, 255, 55);
                border-radius: 10px;
            }
            QLabel#SelectorStatus {
                color: #F0F4FF;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }
            QLabel#SelectorHint {
                color: rgba(210, 222, 245, 195);
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 11px;
                background: transparent;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 22);
                border: 1px solid rgba(255, 255, 255, 48);
                color: #EEF3FF;
                border-radius: 6px;
                padding: 4px 10px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 42);
                border-color: rgba(255, 255, 255, 110);
            }
            QPushButton:disabled {
                color: rgba(255, 255, 255, 80);
                background-color: rgba(255, 255, 255, 10);
                border-color: rgba(255, 255, 255, 20);
            }
            QPushButton#SelectorConfirmBtn {
                background-color: #3B82F6;
                border-color: #60A5FA;
                color: #FFFFFF;
                font-weight: bold;
            }
            QPushButton#SelectorConfirmBtn:hover {
                background-color: #2563EB;
            }
            QPushButton#SelectorConfirmBtn:disabled {
                background-color: rgba(59, 130, 246, 70);
                border-color: rgba(96, 165, 250, 60);
                color: rgba(255, 255, 255, 110);
            }
        """)
        layout = QHBoxLayout(self._toolbar)
        layout.setContentsMargins(14, 7, 10, 7)
        layout.setSpacing(10)

        self._lbl_status = QLabel()
        self._lbl_status.setObjectName("SelectorStatus")
        self._lbl_hint = QLabel()
        self._lbl_hint.setObjectName("SelectorHint")

        self._btn_confirm = QPushButton()
        self._btn_confirm.setObjectName("SelectorConfirmBtn")
        self._btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_confirm.clicked.connect(self._confirm_selection)

        self._btn_clear = QPushButton(tr("清空"))
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.clicked.connect(self._clear_all_regions)

        self._btn_reset = QPushButton(tr("恢复最大化"))
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.clicked.connect(self._trigger_reset)

        self._btn_cancel = QPushButton(tr("取消"))
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._trigger_cancel)

        layout.addWidget(self._lbl_status)
        layout.addWidget(self._lbl_hint)
        layout.addSpacing(4)
        layout.addWidget(self._btn_confirm)
        layout.addWidget(self._btn_clear)
        layout.addWidget(self._btn_reset)
        layout.addWidget(self._btn_cancel)

    def _sync_toolbar(self) -> None:
        if self._replace_index is not None:
            self._lbl_status.setText(tr("重新框选区域 #{idx}", idx=self._replace_index + 1))
            self._lbl_hint.setText(tr("拖拽框选新区域 · ESC 取消"))
            self._btn_confirm.setVisible(False)
            self._btn_clear.setVisible(False)
            self._btn_reset.setVisible(False)
        else:
            count = len(self._regions)
            if count > 0:
                self._lbl_status.setText(tr("已框选 {count} 个区域", count=count))
            else:
                self._lbl_status.setText(tr("请拖拽框选灰度区域"))
            self._lbl_hint.setText(tr("左键拖拽添加 · 拖动边框调整 · 右键区域删除"))
            self._btn_confirm.setVisible(True)
            self._btn_confirm.setText(
                tr("完成 ({count})", count=count) if count > 0 else tr("完成")
            )
            self._btn_confirm.setEnabled(count > 0)
            self._btn_clear.setVisible(True)
            self._btn_clear.setEnabled(count > 0)
            self._btn_reset.setVisible(True)

        self._toolbar.adjustSize()
        primary = QGuiApplication.primaryScreen()
        scr_geo = primary.geometry() if primary else self.rect()
        local_center_x = scr_geo.center().x() - self.geometry().x()
        local_top_y = scr_geo.top() - self.geometry().y() + 24
        tw = self._toolbar.width()
        th = self._toolbar.height()
        tx = max(8, min(int(round(local_center_x - tw / 2)), max(8, self.width() - tw - 8)))
        self._toolbar.setGeometry(tx, max(8, int(local_top_y)), tw, th)

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

    def _ensure_monitors(self) -> list[_MonitorMap]:
        if not self._monitors:
            self._monitors = build_monitor_maps()
        return self._monitors

    def _phys_to_local_point(self, px: int, py: int) -> QPoint:
        maps = self._ensure_monitors()
        chosen = maps[0]
        for m in maps:
            ml, mt, mr, mb = m.phys_rect
            if ml <= px <= mr and mt <= py <= mb:
                chosen = m
                break
        else:
            # 找最近的显示器外推
            best_dist = float("inf")
            for m in maps:
                ml, mt, mr, mb = m.phys_rect
                cx = max(ml, min(px, mr))
                cy = max(mt, min(py, mb))
                dist = (px - cx) ** 2 + (py - cy) ** 2
                if dist < best_dist:
                    best_dist = dist
                    chosen = m

        ml, mt, _, _ = chosen.phys_rect
        gx = chosen.logical_geo.x() + (px - ml) / chosen.dpr
        gy = chosen.logical_geo.y() + (py - mt) / chosen.dpr
        return QPoint(int(round(gx - self.geometry().x())), int(round(gy - self.geometry().y())))

    def _phys_to_local_rect(self, rx: int, ry: int, rw: int, rh: int) -> QRect:
        tl = self._phys_to_local_point(rx, ry)
        br = self._phys_to_local_point(rx + rw, ry + rh)
        return QRect(tl, br).normalized()

    @staticmethod
    def _entry_to_tuple(entry: dict) -> tuple[int, int, int, int]:
        return (int(entry["x"]), int(entry["y"]), int(entry["width"]), int(entry["height"]))

    def start_selection(
        self,
        regions: list | None = None,
        replace_index: int | None = None,
    ) -> None:
        """显示全屏框选遮罩."""
        if self._is_selecting:
            return
        try:
            if not focus_guard.capture():
                focus_guard.capture_drawing_app_fallback()
        except Exception:
            pass

        self._monitors = build_monitor_maps()
        self._is_selecting = True
        self._drag_mode = None
        self._drag_region_idx = None
        self._hover_idx = None
        self._hover_close_idx = None
        self._selected_idx = None
        self._replace_index = replace_index if (replace_index is not None and replace_index >= 0) else None

        from core.region_grayscale import normalize_region_entries
        parsed = normalize_region_entries(regions) if regions else []
        if self._replace_index is not None:
            self._regions = []
            self._guide_regions = [
                self._entry_to_tuple(e)
                for i, e in enumerate(parsed)
                if i != self._replace_index
            ]
        else:
            self._regions = [dict(e, enabled=True) for e in parsed if e.get("enabled", True)]
            self._guide_regions = []
            if self._regions:
                self._selected_idx = len(self._regions) - 1

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
        self._sync_toolbar()
        self._toolbar.show()
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
        self._drag_mode = None
        self._drag_region_idx = None
        self._toolbar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hide()
        try:
            focus_guard.restore()
        except Exception:
            pass

    def _current_draw_local_rect(self) -> QRect:
        return QRect(self._start_local, self._current_local).normalized()

    def _current_draw_physical_rect(self) -> tuple[int, int, int, int]:
        x1, y1 = self._start_phys
        x2, y2 = self._current_phys
        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        return left, top, width, height

    def _region_local_rect(self, idx: int) -> QRect:
        e = self._regions[idx]
        return self._phys_to_local_rect(e["x"], e["y"], e["width"], e["height"])

    def _close_button_rect(self, local_rect: QRect) -> QRect:
        s = self._CLOSE_BTN_SIZE
        cx = local_rect.right() - s - 4
        cy = local_rect.top() + 4
        if local_rect.width() < 48 or local_rect.height() < 36:
            cx = local_rect.right() - s // 2
            cy = max(4, local_rect.top() - s - 4)
        return QRect(cx, cy, s, s)

    def _hit_test_region(self, local_pt: QPoint) -> tuple[int | None, str]:
        """返回命中的区域索引及区域部位: 'close' | 'resize:<edges>' | 'move' | ''."""
        if self._replace_index is not None:
            return None, ""
        tol = self._HANDLE_TOL
        for idx in range(len(self._regions) - 1, -1, -1):
            r = self._region_local_rect(idx)
            if self._close_button_rect(r).adjusted(-2, -2, 2, 2).contains(local_pt):
                return idx, "close"

            expanded = r.adjusted(-tol, -tol, tol, tol)
            if not expanded.contains(local_pt):
                continue

            edges = ""
            if abs(local_pt.y() - r.top()) <= tol:
                edges += "t"
            elif abs(local_pt.y() - r.bottom()) <= tol:
                edges += "b"
            if abs(local_pt.x() - r.left()) <= tol:
                edges += "l"
            elif abs(local_pt.x() - r.right()) <= tol:
                edges += "r"

            if edges:
                return idx, f"resize:{edges}"
            if r.contains(local_pt):
                return idx, "move"
        return None, ""

    def _cursor_for_hit(self, hit_kind: str) -> Qt.CursorShape:
        if hit_kind == "close":
            return Qt.CursorShape.PointingHandCursor
        if hit_kind == "move":
            return Qt.CursorShape.SizeAllCursor
        if hit_kind.startswith("resize:"):
            edges = hit_kind.split(":", 1)[1]
            if edges in ("tl", "br"):
                return Qt.CursorShape.SizeFDiagCursor
            if edges in ("tr", "bl"):
                return Qt.CursorShape.SizeBDiagCursor
            if edges in ("l", "r"):
                return Qt.CursorShape.SizeHorCursor
            if edges in ("t", "b"):
                return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.CrossCursor

    def _remove_region(self, idx: int) -> None:
        if 0 <= idx < len(self._regions):
            self._regions.pop(idx)
            if self._selected_idx is not None:
                if not self._regions:
                    self._selected_idx = None
                elif self._selected_idx >= len(self._regions):
                    self._selected_idx = len(self._regions) - 1
            self._hover_idx = None
            self._hover_close_idx = None
            self._sync_toolbar()
            self.update()

    def _clear_all_regions(self) -> None:
        self._regions.clear()
        self._selected_idx = None
        self._hover_idx = None
        self._hover_close_idx = None
        self._sync_toolbar()
        self.update()

    def _confirm_selection(self) -> None:
        if self._replace_index is not None:
            return
        if not self._regions:
            self._trigger_reset()
            return
        snapshot = [dict(r) for r in self._regions]
        self.stop_selection()
        self.regionsSelected.emit(snapshot)
        if len(snapshot) == 1:
            r = snapshot[0]
            self.regionSelected.emit(r["x"], r["y"], r["width"], r["height"])

    def _trigger_reset(self) -> None:
        self.stop_selection()
        self.regionReset.emit()

    def _trigger_cancel(self) -> None:
        self.stop_selection()
        self.canceled.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        local_pt = event.position().toPoint()
        if event.button() == Qt.MouseButton.RightButton:
            if self._replace_index is None:
                hit_idx, _ = self._hit_test_region(local_pt)
                if hit_idx is not None:
                    self._remove_region(hit_idx)
                    event.accept()
                    return
            self._trigger_cancel()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            global_pt = event.globalPosition().toPoint()
            phys_pt = self._physical_cursor_pos(global_pt)
            self._start_local = local_pt
            self._current_local = local_pt
            self._start_phys = phys_pt
            self._current_phys = phys_pt

            force_draw = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            hit_idx, hit_kind = (None, "") if force_draw else self._hit_test_region(local_pt)

            if hit_idx is not None and hit_kind == "close":
                self._remove_region(hit_idx)
                event.accept()
                return

            if hit_idx is not None and hit_kind == "move":
                self._drag_mode = "move"
                self._drag_region_idx = hit_idx
                self._selected_idx = hit_idx
                self._drag_orig_phys = self._entry_to_tuple(self._regions[hit_idx])
            elif hit_idx is not None and hit_kind.startswith("resize:"):
                self._drag_mode = "resize"
                self._drag_region_idx = hit_idx
                self._selected_idx = hit_idx
                self._drag_resize_edges = hit_kind.split(":", 1)[1]
                self._drag_orig_phys = self._entry_to_tuple(self._regions[hit_idx])
            else:
                self._drag_mode = "draw"
                self._drag_region_idx = None

            self._toolbar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.update()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        local_pt = event.position().toPoint()
        if self._drag_mode is not None:
            self._current_local = local_pt
            global_pt = event.globalPosition().toPoint()
            self._current_phys = self._physical_cursor_pos(global_pt)
            dx = self._current_phys[0] - self._start_phys[0]
            dy = self._current_phys[1] - self._start_phys[1]

            if self._drag_mode == "move" and self._drag_region_idx is not None:
                ox, oy, ow, oh = self._drag_orig_phys
                entry = self._regions[self._drag_region_idx]
                entry["x"] = ox + dx
                entry["y"] = oy + dy
            elif self._drag_mode == "resize" and self._drag_region_idx is not None:
                ox, oy, ow, oh = self._drag_orig_phys
                left, top, right, bottom = ox, oy, ox + ow, oy + oh
                edges = self._drag_resize_edges
                min_s = self.MIN_REGION_SIZE
                if "l" in edges:
                    left = min(right - min_s, ox + dx)
                if "r" in edges:
                    right = max(left + min_s, ox + ow + dx)
                if "t" in edges:
                    top = min(bottom - min_s, oy + dy)
                if "b" in edges:
                    bottom = max(top + min_s, oy + oh + dy)
                entry = self._regions[self._drag_region_idx]
                entry["x"] = left
                entry["y"] = top
                entry["width"] = right - left
                entry["height"] = bottom - top

            self.update()
            event.accept()
            return

        # 未拖拽时更新悬停光标与高亮
        force_draw = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        hit_idx, hit_kind = (None, "") if force_draw else self._hit_test_region(local_pt)
        new_hover = hit_idx
        new_hover_close = hit_idx if hit_kind == "close" else None
        if new_hover != self._hover_idx or new_hover_close != self._hover_close_idx:
            self._hover_idx = new_hover
            self._hover_close_idx = new_hover_close
            self.update()
        self.setCursor(self._cursor_for_hit(hit_kind))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_mode is not None:
            mode = self._drag_mode
            self._drag_mode = None
            self._drag_region_idx = None
            self._toolbar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

            self._current_local = event.position().toPoint()
            global_pt = event.globalPosition().toPoint()
            self._current_phys = self._physical_cursor_pos(global_pt)

            if mode == "draw":
                rx, ry, rw, rh = self._current_draw_physical_rect()
                if rw >= self.MIN_REGION_SIZE and rh >= self.MIN_REGION_SIZE:
                    if self._replace_index is not None:
                        target_idx = self._replace_index
                        self.stop_selection()
                        self.regionReplaced.emit(target_idx, rx, ry, rw, rh)
                        self.regionSelected.emit(rx, ry, rw, rh)
                        event.accept()
                        return
                    self._regions.append({
                        "x": rx,
                        "y": ry,
                        "width": rw,
                        "height": rh,
                        "enabled": True,
                    })
                    self._selected_idx = len(self._regions) - 1
            self._sync_toolbar()
            self.update()
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._replace_index is not None:
                event.accept()
                return
            if self._regions:
                self._confirm_selection()
            else:
                self._trigger_reset()
            event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._trigger_cancel()
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._replace_index is None:
                if self._regions:
                    self._confirm_selection()
                else:
                    self._trigger_reset()
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            if self._replace_index is None:
                if self._regions:
                    self._confirm_selection()
                else:
                    self._trigger_reset()
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._replace_index is None:
            if self._regions:
                idx = self._selected_idx if self._selected_idx is not None else (len(self._regions) - 1)
                self._remove_region(idx)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        full_rect = QRectF(self.rect())
        overlay_path = QPainterPath()
        overlay_path.addRect(full_rect)

        draw_rect = self._current_draw_local_rect() if self._drag_mode == "draw" else QRect()
        local_regions: list[tuple[int, QRect, tuple[int, int, int, int]]] = []
        for idx, entry in enumerate(self._regions):
            lr = self._region_local_rect(idx)
            local_regions.append((idx, lr, self._entry_to_tuple(entry)))
            if not lr.isEmpty() and lr.width() > 2 and lr.height() > 2:
                hole = QPainterPath()
                hole.addRect(QRectF(lr))
                overlay_path = overlay_path.subtracted(hole)

        if not draw_rect.isEmpty() and draw_rect.width() > 2 and draw_rect.height() > 2:
            hole = QPainterPath()
            hole.addRect(QRectF(draw_rect))
            overlay_path = overlay_path.subtracted(hole)

        painter.fillPath(overlay_path, QColor(10, 14, 22, 128))

        # 替换单个区域时，用虚线标出其他已有区域作为对齐参考
        if self._guide_regions:
            guide_pen = QPen(QColor(255, 255, 255, 110), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(guide_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for gx, gy, gw, gh in self._guide_regions:
                painter.drawRect(QRectF(self._phys_to_local_rect(gx, gy, gw, gh)))

        badge_font = QFont("Consolas", 9)
        badge_font.setBold(True)
        close_font = QFont("Microsoft YaHei UI", 9)
        close_font.setBold(True)

        # 绘制已框选的每个区域边框、角点手柄、标牌与删除按钮
        for idx, lr, (rx, ry, rw, rh) in local_regions:
            is_focused = (idx == self._selected_idx or idx == self._hover_idx)
            border_color = QColor(96, 190, 255, 245) if is_focused else QColor(78, 168, 255, 215)
            painter.setPen(QPen(border_color, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(lr))

            # 8 个缩放控制点（焦点/悬停时显示）
            if is_focused:
                hs = 6
                half = hs / 2.0
                pts = [
                    (lr.left(), lr.top()),
                    (lr.center().x(), lr.top()),
                    (lr.right(), lr.top()),
                    (lr.right(), lr.center().y()),
                    (lr.right(), lr.bottom()),
                    (lr.center().x(), lr.bottom()),
                    (lr.left(), lr.bottom()),
                    (lr.left(), lr.center().y()),
                ]
                painter.setPen(QPen(QColor(59, 130, 246), 1.5))
                painter.setBrush(QColor(255, 255, 255))
                for hx, hy in pts:
                    painter.drawRect(QRectF(hx - half, hy - half, hs, hs))

            # 编号 + 物理尺寸标签
            badge_text = f"#{idx + 1}  {rw} × {rh} ({rx}, {ry})"
            painter.setFont(badge_font)
            bfm = painter.fontMetrics()
            bw = bfm.horizontalAdvance(badge_text) + 16
            bh = bfm.height() + 8
            bx = float(lr.left())
            by = float(lr.top() - bh - 5)
            if by < 8:
                by = float(lr.top() + 5)
            badge_rect = QRectF(bx, by, bw, bh)
            painter.setPen(QPen(QColor(78, 168, 255, 175), 1.0))
            painter.setBrush(QColor(18, 22, 32, 228))
            painter.drawRoundedRect(badge_rect, 6.0, 6.0)
            painter.setPen(QColor(235, 242, 255))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

            # 右上角删除按钮 ×
            cb_rect = QRectF(self._close_button_rect(lr))
            is_close_hover = (idx == self._hover_close_idx)
            painter.setPen(QPen(QColor(255, 255, 255, 130), 1.0))
            painter.setBrush(
                QColor(239, 68, 68, 235) if is_close_hover else QColor(20, 24, 34, 215)
            )
            painter.drawEllipse(cb_rect)
            painter.setFont(close_font)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(cb_rect, Qt.AlignmentFlag.AlignCenter, "×")

        # 绘制正在拖拽的新建矩形
        if self._drag_mode == "draw" and not draw_rect.isEmpty() and draw_rect.width() > 2 and draw_rect.height() > 2:
            painter.setPen(QPen(QColor(96, 190, 255, 245), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(draw_rect))

            rx, ry, rw, rh = self._current_draw_physical_rect()
            next_num = (self._replace_index + 1) if self._replace_index is not None else (len(self._regions) + 1)
            badge_text = f"#{next_num}  {rw} × {rh} px  ({rx}, {ry})"
            painter.setFont(badge_font)
            bfm = painter.fontMetrics()
            bw = bfm.horizontalAdvance(badge_text) + 18
            bh = bfm.height() + 10
            bx = float(draw_rect.left())
            by = float(draw_rect.top() - bh - 6)
            if by < 8:
                by = float(draw_rect.top() + 6)
            badge_rect = QRectF(bx, by, bw, bh)
            painter.setPen(QPen(QColor(96, 190, 255, 180), 1.0))
            painter.setBrush(QColor(18, 22, 32, 230))
            painter.drawRoundedRect(badge_rect, 6.0, 6.0)
            painter.setPen(QColor(235, 242, 255))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
