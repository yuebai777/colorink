"""Photoshop CEP loseFocus 焦点收回（前台锁兜底）的测试。

背景：取色交还焦点时 Colorink 用 ``SetForegroundWindow`` + ``SetFocus``，但这条
跨进程路径受 Windows 前台锁限制，可能被拒。CEP 的
``com.adobe.PhotoshopLoseFocus`` 事件让 PS **自己**收回焦点，不受前台锁约束
—— 两条路互为兜底。

这些测试锁定：``request_focus_restore`` 写触发文件（原子写、未部署时安全返回
False）、部署的面板模板含 loseFocus 与两条轮询路径的 focus 检测、以及
``StylusFocusGuard`` 只对 Photoshop（而非 SAI / CSP / UDM）触发。
"""

import os

import pytest

import core.foreground as fg
import core.photoshop_script_bridge as psb
from core.foreground import StylusFocusGuard


@pytest.fixture
def cep_dir(tmp_path, monkeypatch):
    """Point APPDATA at a temp dir and pre-create the extension folder."""
    appdata = tmp_path / "Roaming"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    d = psb.user_cep_dir()
    os.makedirs(d, exist_ok=True)
    return d


# ── request_focus_restore ──────────────────────────────────────────────────


def test_request_focus_restore_writes_trigger_file(cep_dir):
    assert psb.request_focus_restore(4321) is True
    path = os.path.join(cep_dir, "focus_4321.txt")
    assert os.path.isfile(path)
    with open(path, encoding="ascii") as f:
        assert f.read().strip().isdigit()


def test_request_focus_restore_is_atomic(cep_dir):
    # temp + replace 原子写：不留 .tmp 残留，面板不会读到半写的触发文件
    assert psb.request_focus_restore(7) is True
    assert not os.path.exists(os.path.join(cep_dir, "focus_7.txt.tmp"))


def test_request_focus_restore_false_when_not_deployed(tmp_path, monkeypatch):
    appdata = tmp_path / "Roaming"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    # 不建扩展目录 → bridge 未部署 → CEP 路径不可用，安全返回 False
    assert psb.request_focus_restore(1) is False


# ── 部署的面板模板 ─────────────────────────────────────────────────────────


def test_deployed_panel_contains_losefocus_and_trigger_checks(cep_dir):
    bridge = psb.PhotoshopScriptBridge()
    assert bridge.deploy() is True
    with open(os.path.join(cep_dir, psb.INDEX_FILENAME), encoding="utf-8") as f:
        html = f.read()
    assert "com.adobe.PhotoshopLoseFocus" in html
    assert "dispatchEvent" in html
    assert "loseFocus();" in html
    # 两条轮询路径都要检测 focus 触发文件：Node fs 与 fallback
    assert "'/focus' + suff + '.txt'" in html
    assert "File(d+'/focus" in html
    assert 'res === "FOCUS"' in html


# ── StylusFocusGuard 只对 Photoshop 触发 ───────────────────────────────────


def test_focus_guard_requests_losefocus_for_photoshop(monkeypatch):
    calls = []
    monkeypatch.setattr(psb, "request_focus_restore",
                        lambda pid: calls.append(pid) or True)
    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "photoshop.exe")
    StylusFocusGuard._request_photoshop_focus(4242)
    assert calls == [4242]


def test_focus_guard_skips_losefocus_for_other_drawing_apps(monkeypatch):
    calls = []
    monkeypatch.setattr(psb, "request_focus_restore",
                        lambda pid: calls.append(pid) or True)
    for exe in ("sai2.exe", "CLIPStudioPaint.exe", "UDMPaintPro.exe", "notepad.exe"):
        monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid, e=exe: e)
        StylusFocusGuard._request_photoshop_focus(4242)
    assert calls == []


def test_focus_guard_losefocus_never_raises(monkeypatch):
    """CEP 路径出任何错都必须静默 —— 绝不打断正常的 SetForegroundWindow 交还。"""
    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "photoshop.exe")

    def _boom(pid):
        raise RuntimeError("boom")

    monkeypatch.setattr(psb, "request_focus_restore", _boom)
    StylusFocusGuard._request_photoshop_focus(4242)  # 不应抛出
