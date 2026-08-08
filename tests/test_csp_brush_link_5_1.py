"""Pure-function coverage for the CSP 5.1 compact HSV color slot."""

from core.csp_brush_link import (
    _PROFILE_INDEX,
    CSPSync,
    _detect_build_from_version,
    _normalize_version_key,
)


def test_normalize_version_key_recognizes_5_1():
    assert _normalize_version_key("csp5.1") == "csp5.1"
    assert _normalize_version_key("CSP 5.1") == "csp5.1"


def test_normalize_version_key_keeps_legacy_5_x():
    assert _normalize_version_key("csp5.x") == "csp5.x"
    assert _normalize_version_key("csp5.0") == "csp5.x"


def test_detect_build_version_mapping():
    assert _detect_build_from_version((5, 1, 0, 0)) == "csp5.1"
    assert _detect_build_from_version((5, 2, 1, 3)) == "csp5.1"
    assert _detect_build_from_version((5, 0, 0, 0)) == "csp5.x"
    assert _detect_build_from_version((4, 2, 7, 0)) == "csp4.x"
    assert _detect_build_from_version(None) is None


def test_csp5_1_profile_registered():
    profile = _PROFILE_INDEX["csp5.1"]
    assert profile.base_offset == 0x0556BFC8
    assert profile.color_format == "rgb_u32"


class _StubPm:
    def __init__(self):
        self.u32 = {
            0x2000 + 0x20: 0xFFFFFFFF,
            0x2000 + 0x24: 0,
            0x2000 + 0x28: 0,
        }
        self.u16 = {}

    def read_longlong(self, address):
        return 0x2000 if address == 0x1000 + 0x0556BFC8 else 0

    def read_int(self, address):
        return self.u32[address]

    def write_int(self, address, value):
        self.u32[address] = value

    def write_ushort(self, address, value):
        self.u16[address] = value


def _make_rgb_sync():
    sync = CSPSync.__new__(CSPSync)
    sync.color_format = "rgb_u32"
    sync.base_offset = 0x0556BFC8
    sync.module_base = 0x1000
    sync.target = 0x2000
    sync.pm = _StubPm()
    sync._last_hsv_h = 0.0
    sync._last_hsv_s = 0.0
    return sync


def test_rgb_u32_write_updates_channels():
    sync = _make_rgb_sync()
    assert sync.set_color(122, 122, 122)
    assert sync.pm.u32[0x2000 + 0x20] == 0x7A7A7A7A
    assert sync.pm.u32[0x2000 + 0x24] == 0x7A7A7A7A
    assert sync.pm.u32[0x2000 + 0x28] == 0x7A7A7A7A
    assert sync.pm.u16[0x2000 + 0x3E] == 0
    assert sync.pm.u16[0x2000 + 0x42] == 0
    assert sync.pm.u16[0x2000 + 0x44] == 0x7A7A
    assert sync.pm.u16[0x2000 + 0x46] == 0x7A7A


def test_rgb_u32_read_after_write():
    sync = _make_rgb_sync()
    assert sync.get_color() == {"r": 255, "g": 0, "b": 0}
