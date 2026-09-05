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
