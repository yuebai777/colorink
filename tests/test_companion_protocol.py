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


def test_has_session():
    sync = _make_sync()
    sync._host = ""
    sync._port = 0
    sync._password = ""
    assert sync.has_session() is False
    assert sync._has_session() is False

    sync._host = "127.0.0.1"
    sync._port = 32035
    sync._password = "secret"
    assert sync.has_session() is True
    assert sync._has_session() is True


def _make_connected_sync():
    sync = CSPCompanionSync.__new__(CSPCompanionSync)
    sync._sock = object()
    sync._recv_buf = b""
    sync._serial = 0
    sync._connected = True
    sync._aborting = False
    sync._last_heartbeat = 0.0
    sync._was_idle = False
    sync._last_local_activity = 0.0
    sync._current_color = {}
    sync._last_hue_u32 = 0
    sync._last_sat_u32 = 0
    sync.last_error = ""
    sent = []
    sync._send_raw = lambda data: sent.append(_parse_messages(data)[0])
    sync._recv_messages = lambda timeout=0.5: []
    return sync, sent


def test_idle_heartbeat_does_not_reset_csp_idle_timer_and_pauses_sync_poll(monkeypatch):
    import core.csp_companion_sync as ccs

    sync, sent = _make_connected_sync()
    monkeypatch.setattr(ccs, "_get_system_idle_seconds", lambda: 20.0)
    monkeypatch.setattr(ccs, "_is_csp_or_self_foreground", lambda: True)

    result = sync.get_color_hsv()
    assert result is None
    assert sync._connected is True
    assert sync._was_idle is True
    assert len(sent) == 1
    _type, cmd, detail_json = sent[0]
    assert cmd == "TellHeartbeat"
    assert json.loads(detail_json)["IdleTimerResetRequested"] is False


def test_background_app_foreground_enters_passive_keepalive(monkeypatch):
    import core.csp_companion_sync as ccs

    sync, sent = _make_connected_sync()
    monkeypatch.setattr(ccs, "_get_system_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(ccs, "_is_csp_or_self_foreground", lambda: False)

    result = sync.get_color_hsv()
    assert result is None
    assert sync._was_idle is True
    assert len(sent) == 1
    assert sent[0][1] == "TellHeartbeat"
    assert json.loads(sent[0][2])["IdleTimerResetRequested"] is False


def test_wake_from_idle_immediately_sends_active_heartbeat_and_resumes_poll(monkeypatch):
    import time
    import core.csp_companion_sync as ccs

    sync, sent = _make_connected_sync()
    sync._was_idle = True
    # Even if heartbeat interval has NOT elapsed yet, waking up must send
    # IdleTimerResetRequested=True right away before SyncColorCircleUIState.
    sync._last_heartbeat = time.time()
    monkeypatch.setattr(ccs, "_get_system_idle_seconds", lambda: 0.5)
    monkeypatch.setattr(ccs, "_is_csp_or_self_foreground", lambda: True)

    sync.get_color_hsv()
    assert sync._was_idle is False
    cmds = [m[1] for m in sent]
    assert cmds == ["TellHeartbeat", "SyncColorCircleUIState"]
    assert json.loads(sent[0][2])["IdleTimerResetRequested"] is True


def test_set_color_wakes_from_idle_immediately(monkeypatch):
    import time
    import core.csp_companion_sync as ccs

    sync, sent = _make_connected_sync()
    sync._was_idle = True
    sync._last_heartbeat = time.time()
    monkeypatch.setattr(ccs, "_get_system_idle_seconds", lambda: 60.0)
    monkeypatch.setattr(ccs, "_is_csp_or_self_foreground", lambda: False)

    ok = sync.set_color(200, 100, 50)
    assert ok is True
    assert sync._was_idle is False
    cmds = [m[1] for m in sent]
    assert cmds == ["TellHeartbeat", "SetCurrentColor"]
    assert json.loads(sent[0][2])["IdleTimerResetRequested"] is True
