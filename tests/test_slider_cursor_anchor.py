"""The cursor must be anchored by its *centre*, and stay whole at both ends.

Two regressions are covered here.

1. Centre offset — the value axis (where the groove's colours, its outline and
   the out-of-gamut mask live) used to be the whole widget, while the cursor's
   centre only swept the inset span Qt maps values onto. The cursor's centre
   therefore never pointed at the value it encoded: at min / max it sat half a
   cursor away from that value's own position, so the colour under the centre
   was off by half a cursor's worth of the range.

2. Boundary clipping — at the physical limits the cursor was drawn hard
   against the widget edge, where the parent's clip rect sliced it: the ring
   theme lost half of the stroke facing the edge (the cursor's two sides came
   out at different weights), and the triangle themes' ink came out wider at
   one end than the other.

The fix is one shared span. The groove is drawn across `track_span()` — inset
by half a cursor (`_cursor_pad()`) at both ends — and the cursor's *centre*
travels that same span, so the groove's ends are `minimum()` / `maximum()` and
the colour under the cursor's centre is the colour that value encodes. The
native handle is sized to the same span so Qt's own value→x mapping agrees.

The inset must not shorten the groove, so the *row* hands the slider that much
extra slot on both sides instead (`create_group_sliders` + `apply_slider_bleed`):
the widget simply overlaps its neighbours a little and the cursor is free to
overhang the groove's ends without being clipped.
"""

import math

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent
from PyQt6.QtWidgets import (
    QSizePolicy,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from ui.lab_visualizer import LabSlider
from ui.slider_themes import SLIDER_THEMES
from ui.widgets.gradient_slider import GradientSlider
from ui.window.color_updates import ColorUpdatesMixin, apply_slider_bleed

from .test_ringless_support import qapp  # noqa: F401

STYLES = list(SLIDER_THEMES)
WIDTH = 200
BLUE = QColor("#3060a0")


# ── Rendering helpers ─────────────────────────────────────────────────────

def _slider(style, value, width=WIDTH, scale=1.0, gradient=None):
    slider = GradientSlider(Qt.Orientation.Horizontal)
    stops = gradient or [(0.0, BLUE), (1.0, BLUE)]
    slider.set_gradient(stops)
    slider.update_scale(scale, SLIDER_THEMES[style])
    slider.setRange(0, 100)
    slider.setValue(value)
    slider.resize(width, slider.minimumHeight())
    return slider


def _render(slider):
    image = QImage(slider.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("#f4f4f4"))
    slider.render(image)
    return image


def _widget_bg(style, scale, width):
    """The colour the widget paints behind the groove.

    Sampled from row 0 at mid-range: every theme keeps that row above the
    groove, and at 50 the cursor is nowhere near either end.
    """
    image = _render(_slider(style, 50, width, scale))
    color = QColor(image.pixel(0, 0))
    assert color.blue() - color.red() < 25, f"{style}: row 0 is not background"
    return color


def _cursor_ink(style, value, scale=1.0, width=WIDTH):
    """`(slider, image, columns, rows)` of cursor ink.

    The groove is painted in the widget's own background colour, so anything
    that differs from the background *is* the cursor — no threshold guessing
    against the groove's antialiased edge.
    """
    bg = _widget_bg(style, scale, width)
    slider = _slider(style, value, width, scale, gradient=[(0.0, bg), (1.0, bg)])
    image = _render(slider)
    columns, rows = set(), set()
    for y in range(image.height()):
        for x in range(image.width()):
            if QColor(image.pixel(x, y)) != bg:
                columns.add(x)
                rows.add(y)
    return slider, image, columns, rows


def _handle_rect(slider):
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    style = slider.style()
    assert style is not None
    return style.subControlRect(
        QStyle.ComplexControl.CC_Slider, option,
        QStyle.SubControl.SC_SliderHandle, slider,
    )


def _painted_centre(columns):
    return (min(columns) + max(columns) + 1) / 2.0


# ── 1. The cursor's geometric centre is the value anchor ──────────────────

@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("value", [0, 25, 50, 75, 100])
def test_cursor_centre_points_at_the_value_it_represents(qapp, style, value):
    """游标几何中心必须落在它代表的数值位置上，不能整体偏移半个游标。"""
    slider, _image, columns, _rows = _cursor_ink(style, value)
    assert columns, f"{style} @ {value}: 游标没画出来"

    anchor = slider.value_anchor_x()
    painted = _painted_centre(columns)
    assert painted == pytest.approx(anchor, abs=1.0), (
        f"{style} @ {value}: 游标中心 {painted:.1f} 偏离数值锚点 "
        f"{anchor:.1f}（{painted - anchor:+.1f}px）")


@pytest.mark.parametrize("style", STYLES)
def test_value_axis_ends_sit_at_half_a_cursor_from_each_edge(qapp, style):
    """数值轴的端点 = 游标在半宽处的中点，两端对称。"""
    slider = _slider(style, 0)
    half = slider._cursor_half_width()
    x0, x1 = slider.track_span()
    assert x0 == pytest.approx(slider._cursor_pad())
    assert x1 == pytest.approx(slider.width() - slider._cursor_pad())
    assert x0 >= half - 0.01, f"{style}: 轴端点 {x0} 比游标半宽 {half} 还靠边"
    assert slider.value_anchor_x(0.0) == pytest.approx(x0)
    assert slider.value_anchor_x(1.0) == pytest.approx(x1)
    assert slider.value_anchor_x(0.5) == pytest.approx(slider.width() / 2.0, abs=0.5)


@pytest.mark.parametrize("style", STYLES)
def test_native_handle_centre_sweeps_the_same_span(qapp, style):
    """Qt 自己的取值映射必须落在同一条轴上，否则拖动和点击都会偏。

    原生把手既决定命中区域，也决定 Qt 把鼠标位置映射回数值的行程；它一旦
    和数值轴不重合，游标就会在拖动时离开鼠标、在两端被裁。
    """
    x0, x1 = _slider(style, 0).track_span()
    for value, expected in ((0, x0), (100, x1)):
        slider = _slider(style, value)
        rect = _handle_rect(slider)
        centre = rect.x() + rect.width() / 2.0
        assert centre == pytest.approx(expected, abs=0.51), (
            f"{style} @ {value}: 原生把手中心 {centre:.1f} != 数值锚点 "
            f"{expected:.1f}")
        assert rect.x() >= 0
        assert rect.x() + rect.width() <= slider.width()


@pytest.mark.parametrize("style", STYLES)
def test_the_cursor_centre_sits_on_the_bars_visible_end(qapp, style):
    """极值处游标中心必须压在色条看得见的那一端上。

    这就是截图里对不上的地方：色条的左端是 min、右端是 max，游标走到
    极值时中心就该落在那里，而不是差半个游标。色条的端点在中值处量
    （此时游标在中间，不会挡住两端）。
    """
    x0, x1 = _slider(style, 0).track_span()
    image = _render(_slider(style, 50))
    row = _groove_row(image)
    assert row is not None, f"{style}: 找不到槽"

    def is_bar(x):
        color = QColor(image.pixel(x, row))
        return color.blue() - color.red() > 25

    bar = [x for x in range(image.width()) if is_bar(x)]
    assert bar, f"{style}: 色条没画出来"
    # Nothing is painted outside the value axis: the bar's visible end *is*
    # the anchor, not a flat strip beyond it that no value can reach.
    assert min(bar) >= math.floor(x0) - 1, (
        f"{style}: 色条左端 x={min(bar)} 越过了数值轴 {x0}")
    assert max(bar) <= math.ceil(x1) + 1, (
        f"{style}: 色条右端 x={max(bar)} 越过了数值轴 {x1}")

    for value, visible in ((0, min(bar)), (100, max(bar))):
        _s, _i, columns, _r = _cursor_ink(style, value)
        assert columns, f"{style} @ {value}: 游标没画出来"
        painted = _painted_centre(columns)
        assert visible == pytest.approx(painted, abs=1.5), (
            f"{style} @ {value}: 色条端点在 x={visible}，游标中心在 "
            f"{painted:.1f} —— 差 {painted - visible:+.1f}px")


@pytest.mark.parametrize("style", STYLES)
def test_the_colour_under_the_cursor_centre_is_the_value_it_encodes(qapp, style):
    """游标中心底下的颜色必须是该数值本身的颜色。

    渐变铺满数值轴，轴的两端就是色条的可见两端，所以 min / max 的颜色
    正好落在游标走到底时的中心上。
    """
    ramp = [(0.0, QColor("#000000")), (1.0, QColor("#ffffff"))]
    x0, x1 = _slider(style, 0).track_span()
    # Anchor columns can land on a pixel boundary, so sample the nearest
    # column that lies wholly inside the axis.
    left_px = int(math.ceil(x0))
    right_px = int(math.ceil(x1)) - 2

    image = _render(_slider(style, 50, gradient=ramp))
    row = _groove_row(image)
    assert row is not None, f"{style}: 找不到槽"

    def red(x):
        return QColor(image.pixel(x, row)).red()

    assert red(left_px) == pytest.approx(0, abs=9), f"{style}: 色条左端不是 min 的颜色"
    assert red(right_px) == pytest.approx(255, abs=9), f"{style}: 色条右端不是 max 的颜色"
    assert red(int(round((x0 + x1) / 2))) == pytest.approx(127, abs=9), (
        f"{style}: 渐变中点不在数值轴中点上")

    for value, expected, sample in ((0, 0, left_px), (100, 255, right_px)):
        slider = _slider(style, value, gradient=ramp)
        image = _render(slider)
        row = _groove_row(image)

        def red(x, image=image, row=row):
            return QColor(image.pixel(x, row)).red()

        assert red(sample) == pytest.approx(expected, abs=9), (
            f"{style} @ {value}: 游标中心底下不是该数值的颜色")
        anchor = slider.value_anchor_x()
        assert sample == pytest.approx(anchor, abs=2.5), (
            f"{style} @ {value}: 采样列 {sample} 不在锚点 {anchor:.1f} 上")


def _groove_row(image):
    """Middle row of the groove: the row with the widest run of bar ink.

    The background is the most common colour along the widget's own border,
    which no theme's bar or cursor covers. (The most common colour *overall*
    would be the bar itself on a wide slider.) The middle of the widest rows
    is returned so the groove's antialiased edge row can never be picked.
    """
    w, h = image.width(), image.height()
    border = ([image.pixel(x, 0) for x in range(w)]
              + [image.pixel(x, h - 1) for x in range(w)]
              + [image.pixel(0, y) for y in range(h)]
              + [image.pixel(w - 1, y) for y in range(h)])
    counts = {}
    for pixel in border:
        counts[pixel] = counts.get(pixel, 0) + 1
    bg = max(counts, key=lambda key: counts[key])
    widths = [sum(1 for x in range(w) if image.pixel(x, y) != bg)
              for y in range(h)]
    widest = max(widths)
    rows = [y for y, width in enumerate(widths) if width == widest]
    return rows[len(rows) // 2] if widest else None


# ── 2. Nothing is clipped at the physical limits ──────────────────────────

@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_cursor_is_whole_at_both_ends(qapp, style, scale):
    """两端游标必须完整：最小值的列集合镜像后要等于最大值的列集合。"""
    _s0, _i0, at_min, _r0 = _cursor_ink(style, 0, scale)
    _s1, _i1, at_max, _r1 = _cursor_ink(style, 100, scale)
    assert at_min and at_max, f"{style} @ {scale}x: 游标没画出来"

    width = _s0.width()
    mirrored = sorted(width - 1 - x for x in at_max)
    assert sorted(at_min) == mirrored, (
        f"{style} @ {scale}x: 最小值 {sorted(at_min)} 与最大值 "
        f"{mirrored} 不对称 —— 有一端被裁了")


@pytest.mark.parametrize("style", STYLES)
def test_cursor_never_leaves_the_widget(qapp, style):
    """游标墨迹不能越过控件边界（越过就会被父级裁剪框切掉）。"""
    for value in (0, 50, 100):
        _slider_, image, columns, rows = _cursor_ink(style, value)
        assert columns, f"{style} @ {value}: 游标没画出来"
        assert min(columns) >= 0
        assert max(columns) <= image.width() - 1
        assert min(rows) >= 0
        assert max(rows) <= image.height() - 1


@pytest.mark.parametrize("style", STYLES)
def test_the_cursor_carries_the_same_ink_at_every_value(qapp, style):
    """同一个游标画在哪都必须一样重 —— 两端少掉的分量就是被裁掉的那部分。

    回归点：双层圆环的画笔压在矩形边界上，游标贴到控件边缘时朝外的那一半
    笔画落在控件外，两端各比中间少了约 25% 的墨；SAI 的三角底条也一度在
    最小值一端少一截。
    """
    weights = {}
    for value in (0, 50, 100):
        bg = _widget_bg(style, 1.0, WIDTH)
        _slider_, image, columns, _rows = _cursor_ink(style, value)
        assert columns, f"{style} @ {value}: 游标没画出来"
        weights[value] = sum(
            abs(QColor(image.pixel(x, y)).red() - bg.red())
            + abs(QColor(image.pixel(x, y)).green() - bg.green())
            + abs(QColor(image.pixel(x, y)).blue() - bg.blue())
            for x in columns for y in range(image.height())
        )
    lightest, heaviest = min(weights.values()), max(weights.values())
    assert lightest >= heaviest * 0.99, (
        f"{style}: 游标墨量随取值变化 {weights} —— 两端被裁掉了")


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("width", [40, 60, 121, 320])
def test_narrow_and_wide_widgets_keep_the_cursor_centred(qapp, style, width):
    """控件比游标还窄时也不崩：兜底是把游标居中，而不是贴死左边。"""
    for value in (0, 100):
        slider, image, columns, _rows = _cursor_ink(style, value, width=width)
        assert columns, f"{style} @ {width}px/{value}: 游标没画出来"
        assert min(columns) >= 0
        assert max(columns) <= image.width() - 1
        if width < 2 * slider._cursor_half_width():
            painted = _painted_centre(columns)
            assert painted == pytest.approx(width / 2.0, abs=2.0)


# ── 3. Hit testing agrees with the axis ───────────────────────────────────

@pytest.mark.parametrize("style", STYLES)
def test_clicking_the_axis_ends_gives_the_extremes(qapp, style):
    """点在数值轴两端 = 取到 min / max；轴外则夹住。

    轴的端点可能落在半个像素上（三角主题的把手按半像素对齐），鼠标坐标
    是整数，所以端点那一下允许差 1 个单位；轴外必须严格夹住。
    """
    x0, x1 = _slider(style, 0).track_span()
    for x, expected, tolerance in ((int(round(x0)), 0, 1),
                                   (int(round(x1)), 100, 1),
                                   (0, 0, 0),
                                   (WIDTH - 1, 100, 0)):
        slider = _slider(style, 50)
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(x, slider.height() / 2),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        slider.mousePressEvent(event)
        assert abs(slider.value() - expected) <= tolerance, (
            f"{style}: 点 x={x} 得到 {slider.value()}，应为 {expected}")


# ── 4. The LabSlider (vertical lightness bar) ─────────────────────────────

def _bar(lightness, height=200, band=None):
    bar = LabSlider()
    bar.resize(18, height)
    if band is not None:
        bar.set_track_band(*band)
    bar.set_in_gamut_range(0.0, 100.0)
    bar.set_lightness(lightness)
    return bar


def _bar_render(bar):
    image = QImage(bar.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("#f4f4f4"))
    bar.render(image)
    return image


@pytest.mark.parametrize("band", [None, (30.0, 120.0)])
@pytest.mark.parametrize("lightness", [0.0, 37.0, 50.0, 100.0])
def test_lightness_cursor_is_centred_on_its_value(qapp, band, lightness):
    """明度条的游标要画在它代表的明度位置上（旧代码 int() 截断有半像素偏）。"""
    bar = _bar(lightness, band=band)
    top, height = bar.track_band()
    image = _bar_render(bar)

    cy = int(round(bar.lightness_to_y(lightness, top, height)))
    pen = QColor(255, 255, 255) if lightness < 50.0 else QColor(0, 0, 0)
    for row in (cy - 1, cy):
        assert 0 <= row < image.height()
        painted = QColor(image.pixel(image.width() // 2, row))
        assert abs(painted.red() - pen.red()) <= 8, (
            f"L={lightness} band={band}: 第 {row} 行不是游标颜色 "
            f"({painted.getRgb()[:3]} vs {pen.getRgb()[:3]})")


@pytest.mark.parametrize("band", [None, (30.0, 120.0)])
def test_lightness_cursor_is_whole_at_both_ends(qapp, band):
    """明度条到 0 / 100 时游标两行笔画都要画出来，不能被带的边缘裁掉。"""
    for lightness in (0.0, 100.0):
        bar = _bar(lightness, band=band)
        top, height = bar.track_band()
        image = _bar_render(bar)
        cy = int(round(bar.lightness_to_y(lightness, top, height)))
        assert top <= cy - 1, f"L={lightness}: 游标上沿越出带顶"
        assert cy <= top + height - 1, f"L={lightness}: 游标下沿越出带底"


def test_lightness_clicks_and_painting_share_one_axis(qapp):
    """点哪儿就得到哪儿的明度，且和画出来的位置一致。"""
    bar = _bar(50.0, height=200, band=(0.0, 200.0))
    top, height = bar.track_band()
    for lightness in (0.0, 25.0, 50.0, 75.0, 100.0):
        y = bar.lightness_to_y(lightness, top, height)
        bar.handle_mouse(QPointF(9.0, y))
        assert bar.L == pytest.approx(lightness, abs=0.001)
    # 带外夹住
    bar.handle_mouse(QPointF(9.0, -50.0))
    assert bar.L == pytest.approx(100.0)
    bar.handle_mouse(QPointF(9.0, 400.0))
    assert bar.L == pytest.approx(0.0)


@pytest.mark.parametrize("style", ["sai", "ps", "csp", "default"])
@pytest.mark.parametrize("scale", [1.0, 1.05, 1.15, 1.25, 1.35, 1.5])
@pytest.mark.parametrize("dpr", [1.0, 1.5])
def test_the_indicator_has_no_grey_fringe_under_it(qapp, style, scale, dpr):
    """指示器下沿不能多出半亮的一行 —— 那看起来就是一道阴影。

    回归点：底边黑条的 y 是 8*scale 这类浮点数，uiScale 取 105% / 115% 或
    显示器缩放非整数时，它落在两行像素之间，抗锯齿就在条下面刷出半格灰。
    判据：换一个底色重画，真正的实心边颜色不变；混了底色的半亮行必变。
    """
    def bottom_ink(background):
        slider = GradientSlider(Qt.Orientation.Horizontal)
        if dpr != 1.0:
            slider.devicePixelRatioF = lambda: dpr
        slider.set_gradient([(0.0, QColor("#ff3020")), (1.0, QColor("#ff3020"))])
        slider.update_scale(scale, SLIDER_THEMES[style])
        slider.setRange(0, 100)
        slider.setValue(50)
        slider.resize(200, slider.minimumHeight())
        image = QImage(int(slider.width() * dpr), int(slider.height() * dpr),
                       QImage.Format.Format_ARGB32)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(background))
        slider.render(image)

        column = int(round(slider.value_anchor_x() * dpr))
        blank = QColor(background)
        inked = [y for y in range(image.height())
                 if QColor(image.pixel(column, y)) != blank]
        assert inked, f"{style}: 指示器没画出来"
        return QColor(image.pixel(column, max(inked)))

    on_grey = bottom_ink("#909090")
    on_light = bottom_ink("#bfbfbf")
    for channel in range(3):
        assert abs(on_grey.getRgb()[channel] - on_light.getRgb()[channel]) <= 3, (
            f"{style} @ {scale}x/dpr{dpr}: 指示器最下一行随底色变化 "
            f"({on_grey.getRgb()[:3]} vs {on_light.getRgb()[:3]}) —— "
            f"那是半亮的抗锯齿边，看着像阴影")


# ── 5. The row pays for the cursor's overhang ─────────────────────────────
#
# The groove is inset by half a cursor so its ends are the value anchors, and
# the cursor — which overhangs those ends by that same half — must still have
# somewhere to go. That room is taken from the row, not from the groove: the
# slider's slot is pulled out by half a cursor on each side, so the widget
# overlaps the letter and the value box a little and the groove's own span is
# exactly what it was before.

ROW_WIDTH = 300


class _RowHost(ColorUpdatesMixin, QWidget):
    """Minimal host exposing what create_group_sliders needs."""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.slider_row_layouts = []
        self.slider_labels = {}
        self.slider_widgets = {}
        self.slider_containers = {}
        self.color_session = None

    def on_rgb_slider_changed(self):
        pass


def _row(style, scale=1.0, width=ROW_WIDTH, bleed=None):
    """Lay out a real RGB slider row, exactly as create_group_sliders does."""
    theme = SLIDER_THEMES[style]
    host = _RowHost({"sliderSameSpace": 6, "uiScale": int(scale * 100)})
    layout = QVBoxLayout(host)
    layout.setContentsMargins(10, 6, 10, 10)
    layout.setSpacing(8)
    host.create_group_sliders("RGB", ["R", "G", "B"], layout)

    row_spacing = max(0, int(float(theme.get("row_spacing", 1)) * scale))
    spend = 0
    for _chan, (slider, value_label) in host.slider_widgets.items():
        slider.update_scale(scale, theme)
        value_label.setFixedWidth(
            max(24, int(27 * float(theme.get("value_label_width_factor", 1.0)))))
        spend = max(spend, int(round(slider._cursor_pad())))
    for row in host.slider_row_layouts:
        row.setSpacing(row_spacing)
    for _chan, label in host.slider_labels.items():
        label.setFixedWidth(
            max(8, int(12 * scale * float(theme["channel_label_width_factor"]))))
    apply_slider_bleed(host, spend if bleed is None else bleed)

    host.resize(width, 200)
    layout.activate()
    host.resize(width, layout.totalMinimumSize().height() or 120)
    layout.activate()
    return host


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_the_row_gives_the_cursor_room_without_shortening_the_groove(qapp, style, scale):
    """游标外溢的空间由行内腾出来，槽条长度必须和以前一模一样。

    回归点：一开始是把槽条两端各缩进半个游标（"滑条跟着变短"），用户明确
    要求保持槽条长度、只让游标可以探出两端。
    """
    plain = _row(style, scale, bleed=0)
    bled = _row(style, scale)

    plain_slider = plain.slider_widgets["G"][0]
    bled_slider = bled.slider_widgets["G"][0]
    plain_rect = plain_slider.geometry()
    bled_rect = bled_slider.geometry()

    # The slot grew by the bleed on both sides ...
    spend = int(round(bled_slider._cursor_pad()))
    assert spend > 0
    assert bled_rect.left() == plain_rect.left() - spend
    assert bled_rect.right() == plain_rect.right() + spend

    # ... and the groove — painted inside it, inset by exactly that much —
    # lands back on the span the plain row's slot had.
    inset, _ = bled_slider.track_span()
    assert bled_rect.left() + inset == pytest.approx(plain_rect.left(), abs=1.0)
    assert bled_rect.left() + bled_rect.width() - inset == pytest.approx(
        plain_rect.left() + plain_rect.width(), abs=1.0)

    # The letter and the value box stay exactly where they were.
    assert bled.slider_labels["G"].geometry() == plain.slider_labels["G"].geometry()
    assert (bled.slider_widgets["G"][1].geometry()
            == plain.slider_widgets["G"][1].geometry())


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_the_cursor_fits_its_widget_in_a_real_row(qapp, style, scale):
    """行里也必须装得下探出去的游标：两端墨迹不能超出控件。"""
    host = _row(style, scale)
    for slider, _value_label in host.slider_widgets.values():
        half = slider._cursor_half_width()
        for value in (0, 100):
            slider.setValue(value)
            anchor = slider.value_anchor_x()
            assert anchor - half >= -0.01, (
                f"{style} @ {scale}x/{value}: 游标左侧越界 {anchor - half:.2f}px")
            assert anchor + half <= slider.width() + 0.01, (
                f"{style} @ {scale}x/{value}: 游标右侧越界 "
                f"{anchor + half - slider.width():.2f}px")


@pytest.mark.parametrize("style", STYLES)
def test_apply_slider_bleed_is_the_only_thing_that_sizes_them(qapp, style):
    """空行（没有 spacer）也必须能安全调用 —— 测试替身不会建这些 spacer。"""
    class _Bare(QWidget):
        pass

    assert apply_slider_bleed(_Bare(), 6) == 6
    assert apply_slider_bleed(_Bare(), -3) == 0


def test_bleed_spacers_are_plain_spacer_items(qapp):
    """行的结构必须还是 [字母][spacer][滑条][spacer][间隔][数值]。"""
    host = _RowHost({"sliderSameSpace": 6, "uiScale": 100})
    layout = QVBoxLayout(host)
    host.create_group_sliders("RGB", ["R", "G", "B"], layout)
    assert len(host.slider_bleed_spacers) == len(host.slider_row_layouts) == 3

    from ui.widgets import SliderValueLabel

    for row in host.slider_row_layouts:
        kinds = []
        for index in range(row.count()):
            item = row.itemAt(index)
            widget = item.widget() if item is not None else None
            if item is not None and item.spacerItem() is not None:
                kinds.append("spacer")
            elif isinstance(widget, GradientSlider):
                kinds.append("slider")
            elif isinstance(widget, SliderValueLabel):
                kinds.append("value")
            else:
                kinds.append("label")
        assert kinds == ["label", "spacer", "slider", "spacer", "spacer", "value"]
