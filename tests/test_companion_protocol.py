"""Companion protocol slot-index coverage (fg↔main / bg↔sub mapping)."""

import json

from core.csp_companion_sync import CSPCompanionSync, _build_message, _parse_messages


def _make_sync() -> CSPCompanionSync:
    sync = CSPCompanionSync.__new__(CSPCompanionSync)
    sync._last_hue_u32 = 0
    sync._last_sat_u32 = 0
    return sync


def test_build_message_serializes_color_index():
    msg = _build_message("SetCurrentColor", {
        "ColorSpaceKind": "HSV",
        "IsColorTransparent": False,
        "HSVColorH": 1,
        "HSVColorS": 2,
        "HSVColorV": 3,
        "ColorIndex": 1,
    })
    parsed = _parse_messages(msg)
    assert parsed, "no message parsed"
    _type, cmd, detail_json = parsed[0]
    assert cmd == "SetCurrentColor"
    detail = json.loads(detail_json)
    assert detail["ColorIndex"] == 1


def test_parse_hsv_response_reports_main_index():
    sync = _make_sync()
    out = sync._parse_hsv_response({
        "CurrentColorIndex": 0,
        "ColorSelectionModel": "HSV",
        "HSVColorMainH": 0,
        "HSVColorMainS": 4294967295,
        "HSVColorMainV": 2147483647,
    }, 0)
    assert out is not None
    assert out["index"] == 0
    assert out["r"] >= 0


def test_parse_hsv_response_reports_sub_index():
    sync = _make_sync()
    out = sync._parse_hsv_response({
        "CurrentColorIndex": 1,
        "ColorSelectionModel": "HSV",
        "HSVColorSubH": 0,
        "HSVColorSubS": 4294967295,
        "HSVColorSubV": 2147483647,
    }, 1)
    assert out is not None
    assert out["index"] == 1


def test_parse_hsv_response_reports_transparent_flag():
    sync = _make_sync()
    out = sync._parse_hsv_response({
        "CurrentColorIndex": 0,
        "ColorSelectionModel": "HSV",
        "IsCurrentColorTransparent": True,
        "HSVColorMainH": 0,
        "HSVColorMainS": 0,
        "HSVColorMainV": 0,
    }, 0)
    assert out is not None
    assert out["transparent"] is True
