"""Reusable main-window widgets, extracted from the former god class.

Each widget lives in its own module so the main window stays focused on
color-picking orchestration instead of control rendering details.
"""

from ui.widgets.clickable_frame import ClickableFrame
from ui.widgets.gradient_slider import GradientSlider
from ui.widgets.slider_value_label import SliderValueLabel
from ui.widgets.title_bar import TitleBar, _title_bar_content_offset, _visible_title_bar_height

__all__ = [
    "ClickableFrame",
    "GradientSlider",
    "SliderValueLabel",
    "TitleBar",
    "_title_bar_content_offset",
    "_visible_title_bar_height",
]
