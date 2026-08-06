import sys
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint

# Pre-populate sys.modules with mocks for every external dependency touched
# by the ui.main_window import chain.  This prevents import-time side effects
# (win32com genpath, brush_color_spaces, etc.) while still letting us import
# the MainWindow class and test its pure static helpers.
_MODS = [
    "brush_color_spaces", "win32gui", "win32api", "win32con",
    "win32process", "psutil", "win32com", "win32com.client",
    "win32com.client.dynamic", "win32com.client.gencache",
    "win32com.client.CLSIDToClass", "pythoncom",
]
for _m in _MODS:
    _mock = MagicMock()
    sys.modules[_m] = _mock

# Convenience attributes that code under test dereferences
setattr(sys.modules["brush_color_spaces"], "PSColorSpace", MagicMock())
setattr(sys.modules["win32gui"], "GetForegroundWindow", MagicMock(return_value=0))
setattr(sys.modules["win32gui"], "GetWindowText", MagicMock(return_value=""))
setattr(sys.modules["win32gui"], "GetWindowLong", MagicMock(return_value=0))
setattr(sys.modules["win32gui"], "SetWindowLong", MagicMock())
setattr(sys.modules["win32gui"], "IsWindowVisible", MagicMock(return_value=False))
setattr(sys.modules["win32gui"], "IsIconic", MagicMock(return_value=False))
setattr(sys.modules["win32gui"], "ShowWindowAsync", MagicMock())
setattr(sys.modules["win32gui"], "BringWindowToTop", MagicMock())
setattr(sys.modules["win32gui"], "SetForegroundWindow", MagicMock())
setattr(sys.modules["win32gui"], "EnumWindows", MagicMock())
setattr(sys.modules["win32gui"], "GetWindowThreadProcessId", MagicMock(return_value=(0, 0)))
setattr(sys.modules["win32gui"], "GetParent", MagicMock(return_value=0))
setattr(sys.modules["win32gui"], "GetWindow", MagicMock(return_value=0))
setattr(sys.modules["win32gui"], "GetWindowTextLengthW", MagicMock(return_value=0))

from ui.main_window import MainWindow


class ContentHeightPolicyTests(unittest.TestCase):
    def test_expands_when_content_is_taller(self):
        target, manual = MainWindow._resolve_content_height(500, 640, 500, False)
        self.assertEqual((target, manual), (640, False))

    def test_shrinks_when_previous_height_was_automatic(self):
        target, manual = MainWindow._resolve_content_height(640, 500, 640, False)
        self.assertEqual((target, manual), (500, False))

    def test_content_shrink_clears_user_expanded_height(self):
        target, manual = MainWindow._resolve_content_height(900, 500, 640, True)
        self.assertEqual((target, manual), (500, False))

    def test_content_shrink_does_not_infer_manual_height_from_current_size(self):
        target, manual = MainWindow._resolve_content_height(900, 500, 640, False)
        self.assertEqual((target, manual), (500, False))

    def test_required_height_always_wins_over_manual_height(self):
        target, manual = MainWindow._resolve_content_height(500, 700, 900, True)
        self.assertEqual((target, manual), (700, False))

    def test_first_measurement_can_fit_the_saved_window_to_content(self):
        target, manual = MainWindow._resolve_content_height(710, 480, None, False)
        self.assertEqual((target, manual), (480, False))

    def test_required_height_includes_layout_parts_and_spacing(self):
        required = MainWindow._required_content_height(
            title_height=28,
            stack_min_height=120,
            sliders_height=264,
            margins_top=0,
            margins_bottom=8,
            spacing=4,
        )
        self.assertEqual(required, 428)

    def test_visualizer_height_tracks_stack_width(self):
        height = MainWindow._required_visualizer_height(
            window_width=490,
            margins_left=4,
            margins_right=4,
            stack_min_height=120,
        )
        self.assertEqual(height, 482)

    def test_hidden_window_defers_content_measurement(self):
        timer = MagicMock()
        window = SimpleNamespace(
            _adjusting_content_height=False,
            _content_height_adjust_pending=False,
            _content_height_timer=timer,
            isVisible=MagicMock(return_value=False),
        )

        MainWindow._adjust_content_height(cast(MainWindow, window))

        timer.start.assert_called_once_with(0)
        self.assertTrue(window._content_height_adjust_pending)

    def test_explicit_stack_minimum_includes_ringless_control_bar(self):
        margins = SimpleNamespace(
            left=MagicMock(return_value=4),
            right=MagicMock(return_value=4),
            top=MagicMock(return_value=0),
            bottom=MagicMock(return_value=8),
        )
        window = SimpleNamespace(
            _adjusting_content_height=False,
            _content_height_adjust_pending=False,
            _last_auto_height=None,
            _manual_height_override=False,
            isVisible=MagicMock(return_value=True),
            sliders_layout=MagicMock(),
            main_layout=MagicMock(
                contentsMargins=MagicMock(return_value=margins),
                spacing=MagicMock(return_value=4),
            ),
            stack=MagicMock(
                minimumSizeHint=MagicMock(
                    return_value=SimpleNamespace(height=MagicMock(return_value=80))
                ),
                minimumHeight=MagicMock(return_value=180),
            ),
            title_bar=MagicMock(
                sizeHint=MagicMock(
                    return_value=SimpleNamespace(height=MagicMock(return_value=28))
                )
            ),
            sliders_container=MagicMock(
                sizeHint=MagicMock(
                    return_value=SimpleNamespace(height=MagicMock(return_value=40))
                )
            ),
            _required_visualizer_height=MainWindow._required_visualizer_height,
            _required_content_height=MainWindow._required_content_height,
            _resolve_content_height=MainWindow._resolve_content_height,
            width=MagicMock(return_value=100),
            height=MagicMock(return_value=240),
            setMinimumHeight=MagicMock(),
            resize=MagicMock(),
        )

        MainWindow._adjust_content_height(cast(MainWindow, window))

        window.setMinimumHeight.assert_called_once_with(264)
        window.resize.assert_called_once_with(100, 264)


class ResizeDirectionTests(unittest.TestCase):
    def test_outside_positions_are_not_resize_edges(self):
        window = SimpleNamespace(width=lambda: 100, height=lambda: 100)
        for pos in (QPoint(-1, 50), QPoint(50, -1), QPoint(101, 50), QPoint(50, 101)):
            self.assertIsNone(MainWindow.get_resize_direction(window, pos))

    def test_cursor_sync_uses_event_global_position(self):
        seen_positions = []
        cursor = SimpleNamespace(shape=MagicMock(return_value=object()))
        window = SimpleNamespace(
            cfg={"lockWindowSize": False},
            mapFromGlobal=lambda p: seen_positions.append(p) or p,
            get_resize_direction=MagicMock(return_value=None),
            cursor=MagicMock(return_value=cursor),
            unsetCursor=MagicMock(),
            setCursor=MagicMock(),
        )

        MainWindow._sync_resize_cursor(window, QPoint(50, 60))

        self.assertEqual(seen_positions, [QPoint(50, 60)])
        window.unsetCursor.assert_called_once_with()
        window.setCursor.assert_not_called()

    def test_cursor_sync_clears_stale_cursor_when_size_is_locked(self):
        window = SimpleNamespace(
            cfg={"lockWindowSize": True},
            unsetCursor=MagicMock(),
        )

        MainWindow._sync_resize_cursor(window, QPoint(50, 60))

        window.unsetCursor.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
