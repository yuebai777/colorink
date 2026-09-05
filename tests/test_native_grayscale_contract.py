"""Release contract for the consolidated grayscale backends."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_grayscale_backend_is_validated_oklch():
    cfg = (PROJECT_ROOT / "core" / "config.py").read_text(encoding="utf-8")
    assert ('"grayscaleFilterBackend": "dcomp"' in cfg or '"grayscaleFilterBackend": "native"' in cfg)
    assert '"grayscaleFilterMode": "oklch"' in cfg


def test_settings_expose_validated_oklch_and_mag():
    # The grayscale controls were extracted into the appearance-panel mixin;
    # check both the sidebar facade and the panel module so the contract
    # follows the code instead of pinning it to one file.
    sidebar = (PROJECT_ROOT / "ui" / "settings_sidebar.py").read_text(encoding="utf-8")
    appearance = (PROJECT_ROOT / "ui" / "settings" / "appearance_panel.py").read_text(encoding="utf-8")
    source = sidebar + "\n" + appearance
    assert '"OKLCh (GPU兼容)"' in source
    assert '"系统 Luma (Mag)"' in source
    assert '"OKLCh (感知均匀)"' in source
    assert '"Luma (BT.709 标准)"' in source
    assert "_update_grayscale_screen_options" in source
    for removed in (
        "OpenGL Overlay",
        "DComp 直通",
        "Rust D3D11",
        "D3D11 零拷贝 (GL)",
    ):
        assert removed not in source


def test_main_window_uses_validated_oklch_controller():
    main_window = (PROJECT_ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "NativeGrayscaleController" in main_window
    for removed in (
        "RustFilterController",
        "DCompOverlayController",
        "DXShareGrayscaleOverlay",
        "GrayscaleOverlay",
    ):
        assert removed not in main_window


def test_native_backend_uses_nonblack_validated_overlay_runtime():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "GrayscaleOverlay" in controller
    assert "DXShareGrayscaleOverlay" not in controller
    assert "native_grayscale/runtime/grayscale_overlay.pyc" in controller
    assert "is_active" in controller


def test_native_backend_supports_luma_and_screen_targets():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "def set_mode" in controller
    assert "def set_target" in controller
    assert '"luma"' in controller
    assert "_normalize_target" in controller
    assert "available_screens" in controller



def test_oklch_activation_prewarms_before_revealing_fullscreen():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "_colorink_target_geometry" in controller
    assert "_colorink_reveal_ready" in controller
    assert "QTimer.singleShot" in controller
    assert "_colorink_prewarm_installed" in controller


def test_toggle_lifecycle_uses_async_capture_transition():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "_stopping_overlays" in controller
    assert "_pending_start" in controller
    assert "_poll_transition" in controller
    assert "is_capturing" in controller


def test_dxcam_cleanup_releases_camera_off_gui_thread():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "_colorink_release_done" in controller
    assert "camera.release" in controller
    assert "threading.Thread" in controller
    assert "is_released" in controller


def test_oklch_controller_supports_hidden_warm_cache():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "def prepare" in controller
    assert "_warmed" in controller
    assert "_warming" in controller
    assert "keep the capture runtime warm" in controller


def test_main_window_schedules_hidden_oklch_warmup():
    main_window = (PROJECT_ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "grayscale_overlay.prepare" in main_window
    assert "QTimer.singleShot" in main_window

def test_light_preheat_releases_capture_when_off():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "_colorink_start_capture" in controller
    assert "_colorink_stop_capture" in controller
    assert "_warm_releasing" in controller
    assert "def _start_capture_all" in controller


def test_async_camera_init_recovers_stale_camera():
    controller = (PROJECT_ROOT / "core" / "native_grayscale.py").read_text(encoding="utf-8")
    assert "_camera_error" in controller
    assert "_DX_CAMERA_ATTEMPTS" in controller
    assert "camera.release" in controller
    assert "colorink-dxcam-init" in controller


def test_dxcam_preimport_scheduled_at_startup():
    main_window = (PROJECT_ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "colorink-dxcam-preload" in main_window
    assert "dxcam" in main_window
