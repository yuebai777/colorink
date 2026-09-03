"""Regression and integration tests for global color picker synchronization."""

import time
import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core.memory_sync import MemorySyncThread
from core.csp_companion_sync import CSPCompanionSync
from ui.color_picker_overlay import ColorPickerOverlay


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch, tmp_path):
    """Isolate tests from real hardware/OS side-effects (network, hotkeys, sessions)."""
    from core import config as _config
    monkeypatch.setattr(_config, "get_user_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(CSPCompanionSync, "_session_path", lambda *args, **kwargs: str(tmp_path / "test_session.json"))
    monkeypatch.setattr(CSPCompanionSync, "connect", lambda *args, **kwargs: False)

    monkeypatch.setattr("core.global_hotkeys.bind_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.global_hotkeys.bind_mouse_hotkey", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.global_hotkeys.unbind_all", lambda *args, **kwargs: None)


def test_memory_sync_wake_event_and_flush(qapp):
    """MemorySyncThread should wake up immediately on write_color and flush within milliseconds."""
    st = MemorySyncThread(None)
    st.software_mode = "udm"  # UDM write is quick mock / memory
    st.sync_enabled = True

    # Test write_color sets _wake_event
    assert not st._wake_event.is_set()
    st.write_color(120, 130, 140, color_index=0)
    assert st._wake_event.is_set()

    # Test flush_pending_writes returns quickly when queue is cleared
    st._pending_writes.clear()
    t0 = time.time()
    st.flush_pending_writes(timeout=0.1)
    elapsed = time.time() - t0
    assert elapsed < 0.05


def test_companion_recv_messages_is_non_blocking_on_drain():
    """_recv_messages should not block for 100ms on socket timeout when draining fragments."""
    sync = CSPCompanionSync()
    # When socket is None, returns [] immediately
    t0 = time.time()
    msgs = sync._recv_messages(timeout=0.01)
    elapsed = time.time() - t0
    assert msgs == []
    assert elapsed < 0.05


def test_overlay_sample_pos(qapp):
    """_sample_pos extracts the exact pixel from cached snapshots."""
    overlay = ColorPickerOverlay(None)
    # Create a dummy snapshot: 100x100 RGB32 image filled with (45, 67, 89)
    img = QImage(100, 100, QImage.Format.Format_RGB32)
    img.fill(0xFF2D4359)  # 0x2D = 45, 0x43 = 67, 0x59 = 89
    geo = QRect(0, 0, 100, 100)
    overlay._shots = [(None, img, geo, 1.0)]

    rgb = overlay._sample_pos(QPoint(50, 50))
    assert rgb == (45, 67, 89)

    # Out of bounds returns None
    assert overlay._sample_pos(QPoint(200, 200)) is None


def test_picker_color_picked_flow_preserves_hsv_and_flushes(qapp, monkeypatch):
    """MainWindow._on_picker_color_picked must call flush_pending_writes and not overwrite hsv_u32."""
    from ui.main_window import MainWindow

    win = MainWindow()
    win.sync_thread.software_mode = "companion"

    # Track writes to sync_thread
    recorded_writes = []
    orig_write = win.sync_thread.write_color

    def mock_write(*args, **kwargs):
        recorded_writes.append((args, kwargs))
        orig_write(*args, **kwargs)

    flushed = []
    def mock_flush(timeout=0.2):
        flushed.append(True)

    monkeypatch.setattr(win.sync_thread, "write_color", mock_write)
    monkeypatch.setattr(win.sync_thread, "flush_pending_writes", mock_flush)

    # Simulate picker color picked
    win._on_picker_color_picked(10, 20, 30)

    # Verify that write_color was called with a valid hsv_u32 (not None)
    assert len(recorded_writes) == 1
    args, kwargs = recorded_writes[0]
    assert kwargs.get("hsv_u32") is not None
    assert kwargs.get("color_index") == 0

    # Verify flush was called
    assert len(flushed) == 1

    win.sync_thread.stop()
    win.sync_thread.wait(500)
    win.close()


def test_l_gamut_range_continuity_oklab_and_oklch(qapp):
    """Ensure L gamut range does not collapse to [0, 100] when L=50 is out of gamut."""
    from ui.main_window import MainWindow

    win = MainWindow()
    # High-chroma warm color where L=50 is slightly out of gamut (range [52.1, 92.3])
    win._gamut_oklab_a = -0.0038
    win._gamut_oklab_b = 0.1063
    mn, mx = win._compute_oklab_L_gamut_range()
    assert 50 <= mn <= 54, f"mn should be around 52, got {mn}"
    assert 90 <= mx <= 94, f"mx should be around 92, got {mx}"

    # Vertical LabSlider gamut range
    win.lab_square.a = -0.0038
    win.lab_square.b = 0.1063
    win.lab_square.render_mode = "oklab"
    win.lab_slider.L = 73.0
    win._update_lab_slider_gamut_range()
    g_min = win.lab_slider._gamut_min
    g_max = win.lab_slider._gamut_max
    assert 50.0 <= g_min <= 54.0, f"LabSlider min should be ~52, got {g_min}"
    assert 90.0 <= g_max <= 94.0, f"LabSlider max should be ~92, got {g_max}"

    # OKLCh equivalent
    win._gamut_oklch_C = 0.1064
    win._gamut_oklch_h = 92.0
    mn_c, mx_c = win._compute_oklch_L_gamut_range()
    assert 50 <= mn_c <= 54, f"OKLCh mn should be around 52, got {mn_c}"
    assert 90 <= mx_c <= 94, f"OKLCh mx should be around 92, got {mx_c}"

    win.sync_thread.stop()
    win.sync_thread.wait(500)
    win.close()


def test_color_model_rgb_float_preservation():
    """Ensure Color preserves float RGB and round-trips OKLab without quantization error."""
    from ui.color_model import Color
    from ui import color_conversions as cc

    c = Color.from_space("oklab", (0.7308, -0.0038, 0.1063))
    assert isinstance(c.rgb_float[0], float)
    assert isinstance(c.rgb_float[1], float)
    assert isinstance(c.rgb_float[2], float)

    # Display 8-bit RGB is rounded integer
    assert c.rgb == (round(c.rgb_float[0]), round(c.rgb_float[1]), round(c.rgb_float[2]))

    # Mathematical round-trip from rgb_float recovers OKLab with high accuracy (< 1e-6)
    L_rec, a_rec, b_rec = cc.rgb_to_oklab(*c.rgb_float)
    assert abs(L_rec - 0.7308) < 1e-5
    assert abs(a_rec - (-0.0038)) < 1e-5
    assert abs(b_rec - 0.1063) < 1e-5


def test_update_ui_colors_uses_float_rgb_without_jitter(qapp):
    """Ensure adjusting Lightness in OKLab produces zero jitter in L gamut ranges."""
    from ui.main_window import MainWindow
    from ui.color_model import Color

    win = MainWindow()
    win.sync_thread.stop()
    win.sync_thread.wait(500)

    fixed_a = -0.0038
    fixed_b = 0.1063
    gamut_ranges = []

    for step in range(30):
        L_val = 70.0 + step * 0.1
        color = Color.from_space("oklab", (L_val / 100.0, fixed_a, fixed_b))
        win._project_color(color, source="sliders_oklab_L")
        mn, mx = win._compute_oklab_L_gamut_range()
        gamut_ranges.append((mn, mx))

    first_range = gamut_ranges[0]
    assert all(r == first_range for r in gamut_ranges), f"Gamut range jitter detected: {set(gamut_ranges)}"

    win.close()


def test_l_gamut_range_moves_outside_gamut_and_syncs_with_right_slider(qapp):
    """Ensure dragging L beyond initial gamut moves the effective range and syncs with right slider."""
    from ui.main_window import MainWindow

    win = MainWindow()
    win.sync_thread.stop()
    win.sync_thread.wait(500)

    # Initial state with bounded gamut
    win.slider_widgets["L_oklab"][0].setValue(73)
    win.slider_widgets["a_oklab"][0].setValue(0)
    win.slider_widgets["b_oklab"][0].setValue(11)
    win.on_oklab_slider_changed()
    win._apply_deferred_color_updates()

    g_oklab = win.slider_widgets["L_oklab"][0]
    init_min, init_max = g_oklab._gamut_min, g_oklab._gamut_max
    assert (init_min, init_max) == (54, 91)

    # Move L beyond max (98 > 91)
    win.slider_widgets["L_oklab"][0].setValue(98)
    win._apply_deferred_color_updates()

    # The effective range must expand/move to follow the handle
    assert g_oklab._gamut_max >= 98
    assert g_oklab._gamut_min <= 98 <= g_oklab._gamut_max

    # Right vertical slider and bottom slider must remain in sync
    win.lab_square.set_render_mode("oklab")
    win._on_lab_lightness_changed(97)
    win._apply_deferred_color_updates()

    assert win.lab_slider._gamut_min <= 97 <= win.lab_slider._gamut_max
    assert g_oklab._gamut_min <= 97 <= g_oklab._gamut_max
    assert abs(win.lab_slider._gamut_max - g_oklab._gamut_max) <= 1

    win.close()


