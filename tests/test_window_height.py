import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

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
sys.modules["brush_color_spaces"].PSColorSpace = MagicMock()
sys.modules["win32gui"].GetForegroundWindow = MagicMock(return_value=0)
sys.modules["win32gui"].GetWindowText = MagicMock(return_value="")
sys.modules["win32gui"].GetWindowLong = MagicMock(return_value=0)
sys.modules["win32gui"].SetWindowLong = MagicMock()
sys.modules["win32gui"].IsWindowVisible = MagicMock(return_value=False)
sys.modules["win32gui"].IsIconic = MagicMock(return_value=False)
sys.modules["win32gui"].ShowWindowAsync = MagicMock()
sys.modules["win32gui"].BringWindowToTop = MagicMock()
sys.modules["win32gui"].SetForegroundWindow = MagicMock()
sys.modules["win32gui"].EnumWindows = MagicMock()
sys.modules["win32gui"].GetWindowThreadProcessId = MagicMock(return_value=(0, 0))
sys.modules["win32gui"].GetParent = MagicMock(return_value=0)
sys.modules["win32gui"].GetWindow = MagicMock(return_value=0)
sys.modules["win32gui"].GetWindowTextLengthW = MagicMock(return_value=0)

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

        MainWindow._adjust_content_height(window)

        timer.start.assert_called_once_with(0)
        self.assertTrue(window._content_height_adjust_pending)


if __name__ == "__main__":
    unittest.main()
