"""MagFilterController interface contract tests (no real filter activation)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mag_grayscale import MagFilterController, MODE_DISABLED, MODE_ENABLED


class TestMagFilterControllerContract:
    def test_interface_matches_other_backends(self):
        c = MagFilterController()
        # 与其他后端（DComp/Rust）一致的最小接口
        for attr in ("is_available", "is_active", "target", "set_active",
                     "set_mode", "toggle", "set_target", "stop", "close"):
            assert hasattr(c, attr), f"missing {attr}"
        assert callable(c.set_active)
        assert callable(c.toggle)
        assert callable(c.close)

    def test_available_screens_is_all(self):
        assert MagFilterController.available_screens() == ["all"]

    def test_target_is_all(self):
        c = MagFilterController()
        assert c.target == "all"
        c.set_target("1")  # no-op, must not raise

    def test_mode_forced_to_luma(self):
        """Mag 后端仅提供 Luma——任意模式都被规范化。"""
        for mode in ("luma", "oklch", "anything"):
            c = MagFilterController(mode=mode)
            assert c._mode == "luma"
            c.set_mode("oklch")
            assert c._mode == "luma"

    def test_write_mode_roundtrip(self, tmp_path, monkeypatch):
        """控制文件写入:0=off, 1=on。"""
        import core.mag_grayscale as mg
        ctrl = tmp_path / "mag_filter_mode.txt"
        monkeypatch.setattr(mg, "_CTRL_FILE", str(ctrl))
        c = MagFilterController()
        # 不真正启动 EXE
        monkeypatch.setattr(c, "_exe", None)
        monkeypatch.setattr(c, "start", lambda: None)

        c.set_active(True)
        assert ctrl.read_text() == str(MODE_ENABLED)
        c.set_mode("oklch")  # 被强制为 luma，控制值不变
        assert ctrl.read_text() == str(MODE_ENABLED)
        c.set_active(False)
        assert ctrl.read_text() == str(MODE_DISABLED)

    def test_set_mode_keeps_active_state(self, monkeypatch):
        c = MagFilterController()
        monkeypatch.setattr(c, "_exe", str(Path("nonexistent")))
        c._active = True
        c.set_mode("luma")
        assert c._mode == "luma"

    def test_find_exe_returns_none_for_missing(self, monkeypatch):
        monkeypatch.setattr(MagFilterController, "_find_exe",
                            staticmethod(lambda: None))
        c = MagFilterController()
        assert c.is_available is False
