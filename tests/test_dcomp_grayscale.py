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
    from core.mag_grayscale import MagFilterController

    class FakeBrokenController:
        last_error = "DComp 屏幕捕获失败"
        def toggle(self):
            return False
        def set_active(self, active):
            return False
        def close(self):
            pass

    class FakeHost(HotkeyMixin):
        def __init__(self):
            self.cfg = {"grayscaleFilterBackend": "dcomp"}
            self.grayscale_overlay = FakeBrokenController()

    host = FakeHost()
    monkeypatch.setattr(MagFilterController, "is_available", True)
    monkeypatch.setattr(MagFilterController, "toggle", lambda self: True)

    host.on_hotkey_triggered("grayscaleFilterKey")
    assert isinstance(host.grayscale_overlay, MagFilterController)
    assert host.cfg["grayscaleFilterBackend"] == "mag"

