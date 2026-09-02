"""主题 pass 拆成 apply_layout / apply_style 之后的契约。

拆分动机：一次缩放事件会把窗口里每一张样式表重写一遍（实测 53 张、级联出
上万个 polish 事件），而样式只跟配置和设备像素比有关，跟窗口尺寸无关。
现在缩放路径只跑 apply_layout。
"""

import pytest

import ui.window.theme as theme_module
from ui.border_themes import BORDER_THEME_AUTO
from ui.slider_themes import SLIDER_THEMES

from .test_border_themes import _make_theme_host
from .test_ringless_preview_support import qapp  # noqa: F401


@pytest.fixture
def host(qapp):
    return _make_theme_host(qapp, {"sliderStyle": "default",
                                   "borderStyle": BORDER_THEME_AUTO})


def _count_css(monkeypatch):
    calls = []
    real = theme_module._set_css

    def spy(widget, css):
        calls.append(css)
        real(widget, css)

    monkeypatch.setattr(theme_module, "_set_css", spy)
    return calls


def test_apply_layout_writes_no_stylesheets(host, monkeypatch):
    """缩放路径一张样式表都不许写。"""
    calls = _count_css(monkeypatch)
    host.apply_layout(1.0)
    assert calls == []


def test_apply_style_is_where_the_chrome_happens(host, monkeypatch):
    calls = _count_css(monkeypatch)
    host.apply_style(1.0)
    assert calls, "apply_style 应该负责所有样式表"


def test_apply_theme_runs_both_halves(host, monkeypatch):
    order = []
    monkeypatch.setattr(type(host), "apply_layout",
                        lambda self, scale=None: order.append("layout"))
    monkeypatch.setattr(type(host), "apply_style",
                        lambda self, scale=None: order.append("style"))
    host.apply_theme(1.0, is_resize_event=True)
    assert order == ["layout", "style"]


def test_layout_half_is_geometry_identical_to_the_full_pass(host):
    """拆分不许改变任何几何 —— 只跑 layout 和跑完整 pass 结果一致。"""
    def geometry():
        return (host.title_bar.height(),
                host.main_layout.contentsMargins().left(),
                host.main_layout.spacing(),
                [row.spacing() for row in host.slider_row_layouts],
                [lbl.width() for lbl in host.slider_labels.values()])

    host.apply_theme(1.0, is_resize_event=True)
    full = geometry()
    host.apply_layout(1.0)
    assert geometry() == full
    host.apply_style(1.0)
    assert geometry() == full


def test_dpi_change_still_refreshes_the_chrome(host, monkeypatch):
    """DPI 变化是以 resize 形式到达的，字号按 ratio 缩放，必须补一次样式。"""
    host.apply_theme(1.0, is_resize_event=True)
    calls = _count_css(monkeypatch)
    host.apply_layout(1.0)
    assert calls == []            # 比例没变 → 不重建
    host._style_ratio = 42.0      # 假装换了一块屏
    host.apply_layout(1.0)
    assert calls, "设备像素比变化后应重新应用样式"
    assert host._style_ratio != 42.0


def test_theme_presets_pairs_border_with_slider(host):
    for style, preset in SLIDER_THEMES.items():
        host.cfg["sliderStyle"] = style
        host.cfg["borderStyle"] = BORDER_THEME_AUTO
        slider_theme, border_theme = host._theme_presets()
        assert slider_theme is preset
        assert border_theme  # 解析出的配套边框主题
