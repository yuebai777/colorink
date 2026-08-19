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
            0x2000 + 0x08: 0,
            # 主槽 +0x3C/+0x40/+0x44 = HSV u32 比例编码（纯红: H=0, S=100%, V=100%）
            0x2000 + 0x3C: 0x00000000,
            0x2000 + 0x40: 0xFFFFFFFF,
            0x2000 + 0x44: 0xFFFFFFFF,
        }
        self.u16 = {}
        self.bytes = {}

    def read_longlong(self, address):
        return 0x2000 if address == 0x1000 + 0x0556BFC8 else 0

    def read_int(self, address):
        return self.u32.get(address, 0)

    def write_int(self, address, value):
        self.u32[address] = value & 0xFFFFFFFF

    def write_ushort(self, address, value):
        self.u16[address] = value

    def read_bytes(self, address, size):
        if address in self.bytes:
            return self.bytes[address]
        return b"\x00" * size

    def write_bytes(self, address, value, size):
        self.bytes[address] = value


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
    # 主色写入走副本搜索（write_bytes 12 字节）：灰 (122,122,122) S=0 ->
    # H/S 用记忆值 0；V=47.84% -> 0x7A7A7A7A
    raw = sync.pm.bytes[0x2000 + 0x3C]
    h = int.from_bytes(raw[0:4], "little")
    s = int.from_bytes(raw[4:8], "little")
    v = int.from_bytes(raw[8:12], "little")
    assert h == 0
    assert s == 0
    assert v == 0x7A7A7A7A


def test_rgb_u32_read_after_write():
    sync = _make_rgb_sync()
    # stub 初始 +0x3C/0x40/0x44 = HSV u32 编码的纯红
    assert sync.get_color() == {"r": 255, "g": 0, "b": 0, "transparent": 0}


def test_transparent_flag_write_sets_u32_ff():
    sync = _make_rgb_sync()
    assert sync.set_color(255, 0, 0, transparent=True)
    assert sync.pm.u32[0x2000 + 0x08] == 0xFFFFFFFF


def test_transparent_flag_clear_on_normal_write():
    sync = _make_rgb_sync()
    assert sync.set_color(255, 0, 0, transparent=True)
    assert sync.pm.u32[0x2000 + 0x08] == 0xFFFFFFFF
    assert sync.set_color(10, 20, 30)
    assert sync.pm.u32[0x2000 + 0x08] == 0
    # 主色写入 +0x3C（HSV u32）：(10,20,30) H=210° -> 高 16 位 = 0x9555
    raw = sync.pm.bytes[0x2000 + 0x3C]
    h = int.from_bytes(raw[0:4], "little")
    assert h >> 16 == round(210 / 360 * 65535)


def test_transparent_flag_read_back():
    sync = _make_rgb_sync()
    sync.pm.u32[0x2000 + 0x08] = 0xFFFFFFFF
    assert sync.get_color()["transparent"] == 1
    assert sync._read_transparent_flag() is True


def test_sub_color_write_uses_hsv_u32():
    sync = _make_rgb_sync()
    assert sync.set_color(255, 0, 0, color_index=1)
    # 纯红 HSV: H=0, S=100%, V=100%（副本写入走 write_bytes，12 字节）
    raw = sync.pm.bytes[0x2000 + 0x9C]
    h = int.from_bytes(raw[0:4], "little")
    s = int.from_bytes(raw[4:8], "little")
    v = int.from_bytes(raw[8:12], "little")
    assert h == 0
    assert s == 0xFFFFFFFF
    assert v == 0xFFFFFFFF
    # 副色写入同时激活副色槽
    assert sync.pm.u32[0x2000 + 0x08] == 1


def test_sub_color_write_activates_sub_slot_and_main_activates_main():
    sync = _make_rgb_sync()
    sync.set_color(10, 20, 30, color_index=1)
    assert sync.pm.u32[0x2000 + 0x08] == 1
    sync.set_color(10, 20, 30, color_index=0)
    assert sync.pm.u32[0x2000 + 0x08] == 0


def test_sub_color_read_returns_index_1():
    sync = _make_rgb_sync()
    # 绿: H=120° → 120/360*0xFFFFFFFF, S=100%, V=100%
    sync.pm.u32[0x2000 + 0x9C] = round(120 / 360 * 0xFFFFFFFF)
    sync.pm.u32[0x2000 + 0xA0] = 0xFFFFFFFF
    sync.pm.u32[0x2000 + 0xA4] = 0xFFFFFFFF
    out = sync.get_sub_color()
    assert out is not None
    assert out["index"] == 1
    assert out["r"] == 0
    assert out["g"] == 255
    assert out["b"] == 0
    assert out["transparent"] == 0


def test_sub_color_transparent_write_activates_sub_then_flag():
    sync = _make_rgb_sync()
    assert sync.set_color(255, 0, 0, transparent=True, color_index=1)
    # 透明 = 激活槽(副色=1) 被透明标志覆盖为全 FF
    assert sync.pm.u32[0x2000 + 0x08] == 0xFFFFFFFF


# ── version pinning vs. auto-detection ────────────────────────────────────
# "auto" is not a profile: connect() detects the build from the running exe.
# _normalize_version_key maps anything unrecognised to the csp4.x default, so
# set_version("auto") used to switch a live CSP 5.1 connection to the 4.x
# profile, drop the process handle and clear the copy caches — on every
# settings apply, because update_versions() runs with "auto" as the default.

def test_set_version_auto_keeps_the_detected_profile():
    sync = CSPSync()
    sync._apply_profile("csp5.1")
    sync.pm = object()
    sync.pid = 4321
    sync.target = 0x2000
    sync._main_copy_addrs = [0x1000, 0x2000]

    assert sync.set_version("auto") is False
    assert sync.current_version == "csp5.1"
    assert sync.color_format == "rgb_u32"
    assert sync.pm is not None
    assert sync.pid == 4321
    assert sync._main_copy_addrs == [0x1000, 0x2000]


def test_set_version_explicit_pin_still_switches_and_reconnects():
    sync = CSPSync()
    sync._apply_profile("csp5.1")
    sync.pm = object()
    sync.pid = 4321

    assert sync.set_version("csp4.x") is True
    assert sync.current_version == "csp4.x"
    assert sync.pm is None
    assert sync.pid is None


def test_drop_connection_clears_copy_caches():
    """A restarted CSP gets a fresh address space; stale copy addresses would
    make the next write land in unrelated memory of the new process."""
    sync = CSPSync()
    sync._main_copy_addrs = [0x1000]
    sync._sub_copy_addrs = [0x2000]
    sync._main_copy_addrs_known = [0x1000]
    sync._sub_copy_addrs_known = [0x2000]
    sync._copy_scan_ts = {"_main_copy_addrs": 12.0}

    sync._drop_connection()

    assert sync._main_copy_addrs is None
    assert sync._sub_copy_addrs is None
    assert sync._main_copy_addrs_known is None
    assert sync._sub_copy_addrs_known is None
    assert sync._copy_scan_ts == {}
