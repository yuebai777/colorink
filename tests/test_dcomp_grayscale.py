"""Tests for DCompGrayscaleController."""
import pytest
from core.dcomp_grayscale import DCompGrayscaleController


def test_dcomp_controller_interface():
    c = DCompGrayscaleController(mode="oklch")
    assert c.target == "all"
    assert "all" in c.available_screens()
    assert isinstance(c.is_available, bool)
    assert isinstance(c.is_active, bool)
    assert isinstance(c.is_healthy, bool)

    # 模式切换
    c.set_mode("luma")
    assert c._mode == "luma"
    c.set_mode("oklch")
    assert c._mode == "oklch"
    with pytest.raises(ValueError):
        c.set_mode("invalid_mode")

    # 目标屏幕切换
    c.set_target("all")
    assert c.target == "all"
    c.set_target("0")
    assert c.target == "0"
    c.set_target("1: 显示器 (1920x1080)")
    assert c.target == "1"

    # 统计与生命周期操作
    stats = c.get_stats()
    assert isinstance(stats, dict)
    assert "capture_frames" in stats
    assert "present_frames" in stats

    c.prepare()
    c.stop()
    c.close()


def test_dcomp_controller_offline_fallback(monkeypatch):
    monkeypatch.setattr(DCompGrayscaleController, "_find_dll", lambda *args: None)
    c = DCompGrayscaleController()
    assert not c.is_available
    assert not c.is_active
    assert not c.is_healthy
    assert not c.set_active(True)
    assert "未找到" in c.last_error or "不可用" in c.last_error


def test_dcomp_controller_unhealthy_aborts():
    class DummyDLL:
        def dcomp_filter_set_active(self, active): return True
        def dcomp_filter_set_mode(self, mode): pass
        def dcomp_filter_set_target(self, target): return True
        def dcomp_filter_is_healthy(self): return False
        def dcomp_filter_cleanup(self): pass
        def dcomp_filter_get_stats(self, buf): pass

    c = DCompGrayscaleController()
    c._dll = DummyDLL()
    assert not c.set_active(True)
    assert "失败" in c.last_error or "异常" in c.last_error


def test_hotkey_mixin_grayscale_fallback_to_mag(monkeypatch):
    from ui.window.hotkey_mixin import HotkeyMixin
    from ui.window.picker_actions import PickerActionsMixin
    from core.mag_grayscale import MagFilterController

    class FakeBrokenController:
        last_error = "DComp 屏幕捕获失败"
        def toggle(self):
            return False
        def set_active(self, active):
            return False
        def close(self):
            pass

    class FakeHost(HotkeyMixin, PickerActionsMixin):
        def __init__(self):
            self.cfg = {"grayscaleFilterBackend": "dcomp"}
            self.grayscale_overlay = FakeBrokenController()
            self.region_grayscale_overlay = None

        def _sync_sidebar_grayscale_ui(self):
            pass

    host = FakeHost()
    monkeypatch.setattr(MagFilterController, "is_available", True)
    monkeypatch.setattr(MagFilterController, "toggle", lambda self: True)

    host.on_hotkey_triggered("grayscaleFilterKey")
    assert isinstance(host.grayscale_overlay, MagFilterController)
    assert host.cfg["grayscaleFilterBackend"] == "mag"


def test_dcomp_set_active_checks_delta_stats_not_cumulative():
    """If present_frames was > 0 from an earlier session and the current activation
    becomes unhealthy on the second poll, set_active(True) must not break early on
    stale cumulative present_frames."""
    class StaleStatsThenUnhealthyDLL:
        def __init__(self):
            self.health_checks = 0

        def dcomp_filter_set_active(self, active):
            return True

        def dcomp_filter_set_mode(self, mode):
            pass

        def dcomp_filter_set_target(self, target):
            return True

        def dcomp_filter_is_healthy(self):
            self.health_checks += 1
            # First check is healthy, second check fails (as when DuplicateOutput fails after retry)
            return self.health_checks == 1

        def dcomp_filter_cleanup(self):
            pass

        def dcomp_filter_get_stats(self, buf):
            # Cumulative present_frames is stuck at 10 (no new frame in this activation)
            buf[4] = 10

    c = DCompGrayscaleController()
    c._dll = StaleStatsThenUnhealthyDLL()
    assert not c.set_active(True)
    assert c._dll.health_checks >= 2


def test_dcomp_ownership_isolation_between_fullscreen_and_region(monkeypatch):
    """Fullscreen and region DComp controllers share one DLL process-wide; neither
    may hijack, stop, cleanup, or mirror is_active for the other's session."""
    class FakeSharedDLL:
        def __init__(self):
            self.active = False
            self.healthy = True
            self.mode = 0
            self.target = -1
            self.region = (0, 0, 0, 0)
            self.regions = []
            self.cleanup_calls = 0

        def dcomp_filter_init(self):
            return True

        def dcomp_filter_set_active(self, active):
            self.active = bool(active)
            return True

        def dcomp_filter_set_mode(self, mode):
            self.mode = int(mode)

        def dcomp_filter_set_target(self, target):
            self.target = int(target)
            return True

        def dcomp_filter_is_active(self):
            return self.active

        def dcomp_filter_is_healthy(self):
            return self.healthy

        def dcomp_filter_get_screen_count(self):
            return 1

        def dcomp_filter_get_stats(self, buf):
            buf[4] = 1

        def dcomp_filter_set_region(self, x, y, w, h):
            self.region = (x, y, w, h)
            self.regions = [(x, y, w, h)] if w > 0 and h > 0 else []
            return True

        def dcomp_filter_set_regions(self, arr, count):
            self.regions = [(arr[i].x, arr[i].y, arr[i].w, arr[i].h) for i in range(count)]
            self.region = self.regions[0] if self.regions else (0, 0, 0, 0)
            return True

        def dcomp_filter_cleanup(self):
            self.cleanup_calls += 1
            self.active = False

    shared_dll = FakeSharedDLL()
    DCompGrayscaleController._dll_clients.clear()
    DCompGrayscaleController._active_owner = None
    monkeypatch.setattr(DCompGrayscaleController, "_find_dll", staticmethod(lambda: "fake.dll"))

    def fake_load(self, path):
        self._dll = shared_dll

    monkeypatch.setattr(DCompGrayscaleController, "_load_dll", fake_load)

    fs_ctrl = DCompGrayscaleController(mode="oklch")
    reg_ctrl = DCompGrayscaleController(mode="oklch")

    # 1. Fullscreen owns the shared DLL; region remains inactive & cannot hijack DLL state
    assert fs_ctrl.set_active(True) is True
    assert fs_ctrl.is_active is True
    assert reg_ctrl.is_active is False

    reg_ctrl.set_regions([(10, 20, 300, 200)])
    reg_ctrl.set_mode("luma")
    reg_ctrl.set_target("1")
    assert reg_ctrl.regions == [(10, 20, 300, 200)]
    assert shared_dll.regions == []
    assert shared_dll.mode == 0
    assert shared_dll.target == -1

    # Deactivating inactive region controller must NOT turn off active fullscreen filter
    assert reg_ctrl.set_active(False) is True
    assert shared_dll.active is True
    assert fs_ctrl.is_active is True

    # 2. Switch to region ownership: DLL now receives region controller's cached params
    assert fs_ctrl.set_active(False) is True
    assert reg_ctrl.set_active(True) is True
    assert reg_ctrl.is_active is True
    assert fs_ctrl.is_active is False
    assert shared_dll.regions == [(10, 20, 300, 200)]
    assert shared_dll.mode == 1
    assert shared_dll.target == 1

    # 3. Closing fullscreen controller while region controller is still alive keeps DLL intact
    fs_ctrl.close()
    assert shared_dll.cleanup_calls == 0
    assert reg_ctrl.is_active is True

    # 4. Closing the last remaining controller cleans up the shared DLL
    reg_ctrl.close()
    assert shared_dll.cleanup_calls == 1



